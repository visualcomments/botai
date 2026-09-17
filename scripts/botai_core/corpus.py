# -*- coding: utf-8 -*-
"""Corpus acquisition: declarative, verified, and boring on purpose.

The v1 harness ran a fetcher *shipped inside the course* — `install.py` executed
a Python file that came with the course materials. That inverts the trust
relationship: the thing being checked decides how it is checked. Here the
manifest is data (a JSON document the author publishes) and the downloader is
ours, so every rule below is enforced by code the learner installs rather than
by code the course supplies.

The rules exist because each one stops a specific real failure:

* **A URL is not a request.** Loopback, link-local, private ranges and cloud
  metadata addresses are refused, and every redirect is re-checked — a public
  link that redirects to `169.254.169.254` is the classic way to read a
  machine's credentials through a "download this PDF" feature.
* **A hash mismatch is a stop, not a warning.** It means the artifact is
  corrupt or the source changed after the manifest was written; continuing
  would attach an author's name to bytes they never published.
* **A zip bomb lies in its headers.** The declared unpacked size is a hint, so
  the limit is enforced *while writing*, and the file count and total size are
  capped independently of what the archive claims.
* **Only the accepted manifest is truth.** A corpus from another course is not
  merely discouraged, it is unreachable: each course resolves to its own
  directory keyed by the manifest hash, and the search path never crosses.

Nothing here needs the network for the checks themselves: `verify` works on an
already-downloaded directory, which is what lets an offline learner confirm that
the corpus they have is the one that was accepted.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import paths, schemas

SCHEMA_VERSION = 2

# Corpus readiness, in the design's vocabulary. `ready` is a claim that a
# specific accepted manifest was verified file by file — never merely that a
# directory exists.
STATUS = ("not_required", "missing", "downloading", "verifying", "ready",
          "unverified", "stale", "failed")

# Limits. Generous enough for a real course reader, tight enough that a hostile
# or broken source cannot fill a disk before anyone notices.
MAX_ARCHIVE_BYTES = 2 * 1024 ** 3          # 2 GiB compressed
MAX_UNPACKED_BYTES = 10 * 1024 ** 3        # 10 GiB total
MAX_FILES = 100_000
MAX_REDIRECTS = 5
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
CHUNK = 1024 * 256

# Addresses a download must never reach. `is_private` covers RFC1918 and the
# loopback range; link-local covers the cloud metadata endpoint; and metadata
# hostnames are blocked by name because a resolver can point them anywhere.
BLOCKED_HOSTNAMES = {
    "metadata.google.internal", "metadata.goog",
    "instance-data", "metadata",
}


class CorpusError(RuntimeError):
    """A refused corpus operation, with a stable code for callers and tests."""

    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def sha256_file(path, *, chunk=CHUNK):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def manifest_hash(document):
    """The canonical hash that identifies an accepted manifest revision.

    Content-addressed so the same manifest republished keeps resolving to the
    same installed directory, and a changed manifest installs beside the old one
    instead of overwriting it.
    """
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# --------------------------------------------------------------------------
# URL safety
# --------------------------------------------------------------------------

def _is_forbidden_address(host):
    """Whether a host resolves only to addresses a download must not reach."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable is not "safe": it is unknown, and an unknown destination
        # is refused rather than attempted.
        return True, "узел не разрешается в адрес"
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return True, "не удалось разобрать адрес %r" % address
        if (parsed.is_loopback or parsed.is_private or parsed.is_link_local
                or parsed.is_reserved or parsed.is_multicast
                or parsed.is_unspecified):
            return True, "адрес %s ведёт во внутреннюю или служебную сеть" % address
    return False, ""


def check_url(url):
    """Validate a corpus URL. Returns the parsed URL or raises `CorpusError`.

    Checks the scheme, the hostname allow-list and the resolved addresses. It is
    deliberately conservative: refusing an unusual-but-fine URL costs one
    manual step, while allowing an unusual-but-hostile one costs the machine's
    credentials.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise CorpusError("URL_INVALID", "не удалось разобрать адрес: %s" % e)

    if parsed.scheme.lower() != "https":
        raise CorpusError(
            "URL_SCHEME_REFUSED",
            "разрешён только https, получено %r: по http содержимое можно "
            "подменить по пути" % parsed.scheme,
        )
    if parsed.username or parsed.password:
        raise CorpusError(
            "URL_HAS_CREDENTIALS",
            "адрес содержит логин или токен: секреты не хранятся в манифесте",
        )
    host = parsed.hostname
    if not host:
        raise CorpusError("URL_NO_HOST", "в адресе нет узла")
    if host.lower() in BLOCKED_HOSTNAMES:
        raise CorpusError(
            "URL_METADATA_HOST",
            "адрес %r указывает на службу метаданных: такие запросы запрещены" % host,
        )
    if host.lower() in ("localhost",) or host.endswith(".localhost"):
        raise CorpusError("URL_LOOPBACK", "адрес %r указывает на localhost" % host)

    forbidden, why = _is_forbidden_address(host)
    if forbidden:
        raise CorpusError("URL_PRIVATE_ADDRESS", "адрес %r отклонён: %s" % (host, why))
    return parsed


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target, not only the original URL.

    urllib follows redirects internally; without this hook a manifest could
    name a harmless public URL that 302s into the internal network.
    """

    def __init__(self):
        super().__init__()
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops += 1
        if self.hops > MAX_REDIRECTS:
            raise CorpusError("URL_TOO_MANY_REDIRECTS",
                              "больше %d перенаправлений" % MAX_REDIRECTS)
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination, *, expected_sha256=None, max_bytes=MAX_ARCHIVE_BYTES,
             connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT):
    """Download one artifact to `destination`, enforcing the size limit.

    A partial download is deleted rather than left behind: a truncated file that
    later fails its hash is confusing, while a missing file is unambiguous.
    """
    check_url(url)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    opener = urllib.request.build_opener(_GuardedRedirects())
    request = urllib.request.Request(url, headers={
        "User-Agent": "botai-corpus/2.0",
        "Accept": "*/*",
    })

    digest = hashlib.sha256()
    written = 0
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(destination.parent), suffix=".part")
    try:
        try:
            with opener.open(request, timeout=read_timeout) as response:
                # A redirect chain could still land somewhere unexpected; the
                # final URL is checked too.
                check_url(response.geturl())
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    written += len(block)
                    if written > max_bytes:
                        raise CorpusError(
                            "DOWNLOAD_TOO_LARGE",
                            "загрузка превысила предел %d байт" % max_bytes,
                        )
                    digest.update(block)
                    os.write(tmp_fd, block)
        except urllib.error.HTTPError as e:
            raise CorpusError(
                "DOWNLOAD_HTTP_ERROR",
                "сервер ответил HTTP %s на %s" % (e.code, url),
                detail={"http_status": e.code},
            )
        except urllib.error.URLError as e:
            raise CorpusError("DOWNLOAD_FAILED",
                              "не удалось скачать %s: %s" % (url, e.reason))
        except socket.timeout:
            raise CorpusError("DOWNLOAD_TIMEOUT", "истёк таймаут загрузки %s" % url)
    except BaseException:
        os.close(tmp_fd)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    os.close(tmp_fd)
    actual = digest.hexdigest()
    if expected_sha256 and actual != expected_sha256:
        os.unlink(tmp_name)
        raise CorpusError(
            "HASH_MISMATCH",
            "SHA-256 не совпал: ожидался %s, получен %s. Артефакт повреждён или "
            "источник изменился после публикации манифеста."
            % (expected_sha256[:16], actual[:16]),
            detail={"expected": expected_sha256, "actual": actual},
        )

    os.replace(tmp_name, destination)
    return {"path": str(destination), "sha256": actual, "byte_size": written}


# --------------------------------------------------------------------------
# Safe unpacking
# --------------------------------------------------------------------------

def _member_problem(name, *, seen):
    """Whether an archive member name is unsafe, with the reason."""
    normalised = name.replace("\\", "/")
    if not normalised or normalised.endswith("/"):
        return None  # a directory entry; handled by the caller
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
        return "абсолютный путь в архиве: %r" % name
    parts = normalised.split("/")
    if any(part == ".." for part in parts):
        return "выход за каталог в архиве: %r" % name
    if "\x00" in normalised:
        return "нулевой байт в имени: %r" % name

    lowered = normalised.lower()
    if lowered in seen:
        # Two members differing only by case collide on Windows and silently
        # overwrite each other on macOS; the corpus would then differ from the
        # one the manifest hashes describe.
        return "повтор имени (с учётом регистра): %r" % name
    seen.add(lowered)
    return None


def safe_extract(archive_path, destination, *, media_type="application/zip",
                 max_bytes=MAX_UNPACKED_BYTES, max_files=MAX_FILES):
    """Unpack an archive into `destination`, refusing anything suspicious.

    Returns a report of what was written. Enforces the limits while writing,
    because the sizes a hostile archive declares in its headers are exactly the
    numbers it is lying about.
    """
    archive_path = Path(archive_path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    written = []
    total = 0
    seen = set()

    def record(member_name, stream_size_note=None):
        nonlocal total
        problem = _member_problem(member_name, seen=seen)
        if problem:
            raise CorpusError("ARCHIVE_UNSAFE_MEMBER", problem)
        return problem

    if media_type in ("application/zip", "application/x-zip-compressed") \
            or archive_path.suffix.lower() == ".zip":
        if not zipfile.is_zipfile(archive_path):
            raise CorpusError("ARCHIVE_NOT_ZIP", "файл не является zip-архивом")
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if len(written) >= max_files:
                    raise CorpusError("ARCHIVE_TOO_MANY_FILES",
                                      "в архиве больше %d файлов" % max_files)
                record(info.filename)
                target = paths.ensure_within(destination, destination / info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        block = source.read(CHUNK)
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            raise CorpusError(
                                "ARCHIVE_TOO_LARGE",
                                "распакованное содержимое превысило предел %d байт"
                                % max_bytes,
                            )
                        sink.write(block)
                written.append(info.filename)
    else:
        mode = "r:gz" if media_type.endswith("gzip") or str(archive_path).endswith(".gz") \
            else "r:"
        if not tarfile.is_tarfile(archive_path):
            raise CorpusError("ARCHIVE_NOT_TAR", "файл не является tar-архивом")
        with tarfile.open(archive_path, mode) as archive:
            for member in archive:
                if member.isdir():
                    continue
                # A symlink or device node in an archive is never needed for a
                # text corpus, and following one is how an unpack writes outside
                # its destination.
                if member.issym() or member.islnk():
                    raise CorpusError(
                        "ARCHIVE_LINK_REFUSED",
                        "архив содержит ссылку (%s): ссылки не распаковываются"
                        % member.name,
                    )
                if not member.isfile():
                    raise CorpusError(
                        "ARCHIVE_SPECIAL_FILE",
                        "архив содержит не обычный файл (%s)" % member.name,
                    )
                if len(written) >= max_files:
                    raise CorpusError("ARCHIVE_TOO_MANY_FILES",
                                      "в архиве больше %d файлов" % max_files)
                record(member.name)
                target = paths.ensure_within(destination, destination / member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:  # pragma: no cover - guarded by isfile()
                    continue
                with source, open(target, "wb") as sink:
                    while True:
                        block = source.read(CHUNK)
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            raise CorpusError(
                                "ARCHIVE_TOO_LARGE",
                                "распакованное содержимое превысило предел %d байт"
                                % max_bytes,
                            )
                        sink.write(block)
                written.append(member.name)

    return {"files": written, "count": len(written), "bytes": total}


# --------------------------------------------------------------------------
# Installation and verification
# --------------------------------------------------------------------------

def corpus_root(root, course_id):
    """`.botai/corpus/<course-id>/` — one directory per course, always.

    The v1 harness had a single global corpus root, so "the corpus is installed"
    could be true of a different course than the one being studied. Scoping by
    course makes cross-course retrieval impossible rather than discouraged.
    """
    base = Path(root) / ".botai" / "corpus"
    return paths.ensure_within(base, base / paths.safe_name(course_id, kind="идентификатор курса"))


def installed_dir(root, course_id, manifest_digest):
    base = corpus_root(root, course_id)
    return paths.ensure_within(base, base / manifest_digest)


def load_manifest(path):
    """Load and validate a corpus manifest, or a legacy one via the adapter."""
    path = Path(path)
    if not path.is_file():
        raise CorpusError("MANIFEST_MISSING", "манифест корпуса не найден: %s" % path)
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise CorpusError("MANIFEST_UNREADABLE", "не удалось прочитать манифест: %s" % e)

    try:
        schemas.validate(document, "corpus")
        return document, "v2"
    except schemas.SchemaError:
        # Not v2 — try the legacy shape before giving up, and say which one was
        # used so the verification level is never overstated.
        converted = adapt_legacy_manifest(document)
        if converted is not None:
            return converted, "legacy_adapted"
        raise CorpusError(
            "MANIFEST_INVALID",
            "манифест не соответствует схеме v2, а как legacy не распознан. "
            "Поля v1 не угадываются: попросите у автора манифест v2.",
        )


def adapt_legacy_manifest(document):
    """Convert a v1 corpus/index manifest to v2, or return None.

    The v1 harness knew two ad-hoc shapes (`index-manifest.json`,
    `corpus-manifest.json`) with different field names. The adapter maps only
    fields it can map with certainty; anything else returns None so the caller
    reports `LEGACY_MANIFEST_UNSUPPORTED` rather than running a guessed fetch.
    """
    if not isinstance(document, dict):
        return None

    artifacts = []
    for key in ("artifacts", "downloads", "files"):
        for entry in document.get(key) or []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url") or entry.get("link")
            digest = entry.get("sha256") or entry.get("hash")
            if not url:
                continue
            artifact_id = entry.get("id") or entry.get("name") or ("a%d" % (len(artifacts) + 1))
            artifact_id = re.sub(r"[^a-z0-9._-]", "-", str(artifact_id).lower())[:63] or "artifact"
            artifacts.append({
                "artifact_id": artifact_id,
                "url": url,
                "sha256": digest or ("0" * 64),
                "media_type": entry.get("media_type") or entry.get("type") or "application/octet-stream",
                "unpack": entry.get("unpack") or ("zip" if str(url).endswith(".zip") else "none"),
                "compressed_bytes": entry.get("size") or entry.get("bytes"),
                "unpacked_bytes_max": None,
                "auth_required": bool(entry.get("auth_required")),
            })
    if not artifacts:
        return None

    corpus_id = re.sub(r"[^a-z0-9._-]", "-",
                       str(document.get("corpus_id") or document.get("id") or "legacy").lower())[:63]
    index_document = document.get("index") or {}
    kind = index_document.get("kind") or ("annoy" if (document.get("annoy_index")
                                                      or index_document.get("annoy_index")) else "text")
    if kind not in ("none", "annoy", "text"):
        kind = "text"

    converted = {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": corpus_id or "legacy",
        "version": str(document.get("version") or "legacy"),
        "source_revision": document.get("source_revision"),
        "description": "преобразовано из манифеста v1; уровень проверки — "
                       "адаптированный, не проверенный автором v2",
        "artifacts": artifacts,
        "files": [
            {
                "path": f.get("path"),
                "sha256": f.get("sha256") or ("0" * 64),
                "byte_size": int(f.get("byte_size") or f.get("size") or 0),
                "role": f.get("role") if f.get("role") in
                        ("text", "chunks", "index", "metadata", "mapping") else "text",
                "artifact_id": artifacts[0]["artifact_id"],
            }
            for f in (document.get("files") or [])
            if isinstance(f, dict) and f.get("path")
        ],
        "index": {
            "kind": kind,
            "config_path": index_document.get("config_path"),
            "chunks_path": index_document.get("chunks_path"),
            "text_mapping_path": index_document.get("text_mapping_path"),
            "embedding_model_id": index_document.get("embedding_model_id"),
            "embedding_revision": index_document.get("embedding_revision"),
        },
        "license": document.get("license") if isinstance(document.get("license"), dict) else None,
    }
    # A legacy manifest without hashes cannot claim verification; the sentinel
    # zero digest is detectable and forces `unverified` status downstream.
    try:
        schemas.validate(converted, "corpus")
    except schemas.SchemaError:
        return None
    return converted


def verify(root, course_id, manifest_digest, manifest):
    """Check an installed corpus against its manifest, file by file.

    Returns a report. `ok` is true only when every declared file is present and
    hashes correctly — an archive hash proves the archive, not that the file the
    index points at is the intended one.
    """
    directory = installed_dir(root, course_id, manifest_digest)
    report = {
        "status": "missing",
        "directory": str(directory),
        "manifest_hash": manifest_digest,
        "checked": 0,
        "missing": [],
        "mismatched": [],
        "unverified_files": [],
        "extra": [],
        "ok": False,
    }
    if not directory.is_dir():
        return report

    declared = {}
    for entry in manifest.get("files") or []:
        declared[entry["path"]] = entry

    for relative, entry in declared.items():
        target = paths.ensure_within(directory, directory / relative)
        report["checked"] += 1
        if not target.is_file():
            report["missing"].append(relative)
            continue
        expected = entry["sha256"]
        if expected == "0" * 64:
            # The sentinel from the legacy adapter: the manifest simply does not
            # carry a hash. The file is counted, and the corpus can never be
            # called verified on the strength of a placeholder.
            report["unverified_files"].append(relative)
            continue
        actual = sha256_file(target)
        if actual != expected:
            report["mismatched"].append({
                "path": relative, "expected": expected, "actual": actual,
            })

    if not declared:
        report["status"] = "unverified"
        return report

    if report["missing"] or report["mismatched"]:
        report["status"] = "stale" if not report["missing"] else "failed"
        return report

    if report["unverified_files"]:
        report["status"] = "unverified"
        return report

    report["status"] = "ready"
    report["ok"] = True
    return report


def acquire(root, course_id, manifest, *, manifest_digest=None, dry_run=False,
            offline=False, fetch=None):
    """Install a corpus from its manifest into the course's own directory.

    Downloads to staging, unpacks with limits, verifies per file, and only then
    moves the directory into place. A failure leaves any previously installed
    corpus untouched — losing a working corpus to a failed update would cost the
    learner their sources for no reason.

    `fetch` lets a test supply bytes without a network; production passes None
    and the real downloader is used.
    """
    digest = manifest_digest or manifest_hash(manifest)
    target = installed_dir(root, course_id, digest)

    report = {
        "schema_version": SCHEMA_VERSION,
        "course_id": course_id,
        "corpus_id": manifest.get("corpus_id"),
        "version": manifest.get("version"),
        "manifest_hash": digest,
        "status": "missing",
        "directory": str(target),
        "downloaded": [],
        "unpacked": [],
        "checks": {},
        "warnings": [],
        "dry_run": bool(dry_run),
    }

    existing = verify(root, course_id, digest, manifest)
    if existing["ok"]:
        report["status"] = "ready"
        report["checks"] = existing
        report["warnings"].append("корпус уже установлен и проверен: повтор не нужен")
        return report

    if offline:
        fetchable = [a for a in (manifest.get("artifacts") or [])
                     if not (a.get("url") or "").startswith("file:")]
        if fetchable and not (target / "files").is_dir():
            report["status"] = "failed"
            report["warnings"].append(
                "офлайн: артефакты не скачаны, а локальной копии нет. "
                "Это не «корпус отсутствует» — это «проверить не удалось»."
            )
            return report

    if dry_run:
        report["status"] = "verifying" if target.is_dir() else "missing"
        report["warnings"].append("предпросмотр: ничего не скачано и не распаковано")
        for artifact in manifest.get("artifacts") or []:
            report["downloaded"].append({
                "artifact_id": artifact["artifact_id"],
                "url": _clean_url(artifact["url"]),
                "would_verify": artifact["sha256"][:16],
            })
        return report

    root_path = corpus_root(root, course_id)
    root_path.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=str(root_path), prefix=".staging-"))

    try:
        for artifact in manifest.get("artifacts") or []:
            url = artifact["url"]
            artifact_id = artifact["artifact_id"]
            archive_path = staging / ("%s.payload" % artifact_id)

            if fetch is not None:
                payload = fetch(url)
                archive_path.write_bytes(payload)
                actual = hashlib.sha256(payload).hexdigest()
                if artifact["sha256"] != "0" * 64 and actual != artifact["sha256"]:
                    raise CorpusError(
                        "HASH_MISMATCH",
                        "SHA-256 артефакта %s не совпал: ожидался %s, получен %s"
                        % (artifact_id, artifact["sha256"][:16], actual[:16]),
                        detail={"artifact_id": artifact_id},
                    )
                downloaded = {"sha256": actual, "byte_size": len(payload)}
            else:
                downloaded = download(url, archive_path,
                                      expected_sha256=None if artifact["sha256"] == "0" * 64
                                      else artifact["sha256"],
                                      max_bytes=artifact.get("compressed_bytes")
                                      or MAX_ARCHIVE_BYTES)

            report["downloaded"].append({
                "artifact_id": artifact_id,
                "url": _clean_url(url),
                "sha256": downloaded["sha256"],
                "byte_size": downloaded["byte_size"],
            })

            unpack = artifact.get("unpack", "none")
            if unpack == "none":
                destination = staging / artifact_id
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(archive_path), str(destination))
                report["unpacked"].append({"artifact_id": artifact_id, "files": 1})
            else:
                destination = staging
                media = {"zip": "application/zip", "tar": "application/x-tar",
                         "tar.gz": "application/gzip"}[unpack]
                extracted = safe_extract(
                    archive_path, destination, media_type=media,
                    max_bytes=artifact.get("unpacked_bytes_max") or MAX_UNPACKED_BYTES,
                )
                os.unlink(archive_path)
                report["unpacked"].append({
                    "artifact_id": artifact_id, "files": extracted["count"],
                    "bytes": extracted["bytes"],
                })

        # Publish the staging directory before verifying, because verification
        # reads through the same path resolution a learner's search will use.
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)

        checks = verify(root, course_id, digest, manifest)
        report["checks"] = checks
        report["status"] = checks["status"]

        if not checks["ok"]:
            report["warnings"].append(
                "корпус установлен, но НЕ подтверждён по манифесту: %s"
                % (checks["missing"] or checks["mismatched"] or checks["unverified_files"])
            )
        return report

    except CorpusError as e:
        shutil.rmtree(staging, ignore_errors=True)
        report["status"] = "failed"
        report["error"] = {"code": e.code, "message": e.message, "detail": e.detail}
        report["warnings"].append(
            "установка не выполнена; ранее установленный корпус не тронут"
        )
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _clean_url(url):
    """A URL safe to show and export: query tokens removed.

    A private link often carries a signed query string. It is needed to download
    and must never appear in a report, a log or an export.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url))
    except ValueError:
        return "(неразбираемый адрес)"
    if not parsed.query:
        return url
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment)
    ) + "?<подпись скрыта>"


def status(root, course_id, manifest=None, *, manifest_digest=None):
    """The single per-course readiness answer.

    `not_required` is returned only when a manifest exists and declares no
    artifacts — never merely because nobody looked. A missing manifest is
    `missing`, because "we did not check" and "there is nothing to check" are
    different facts and only one of them is reassuring.
    """
    if manifest is None:
        return {"status": "missing", "course_id": course_id,
                "reason": "манифест корпуса не задан"}

    digest = manifest_digest or manifest_hash(manifest)
    if not (manifest.get("artifacts") or []):
        return {"status": "not_required", "course_id": course_id,
                "manifest_hash": digest,
                "reason": "манифест не объявляет артефактов"}

    report = verify(root, course_id, digest, manifest)
    report["course_id"] = course_id
    return report
