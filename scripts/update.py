#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-update for an installed botai project.

Updates the harness itself — policy, skills, agents, commands, scripts, docs —
from the upstream botai repository, and **never** touches the student's own
material (`courses/`, `progress/`, `.botai/`, `dist/`).

Two modes, chosen automatically:

* **git** — the project is a git checkout whose `origin` is the upstream
  repository. The update is `git fetch` + fast-forward merge; a diverged tree
  is refused rather than forced.
* **archive** — anything else (the normal case: `make install` copies the
  harness into a fresh project). The upstream tarball for the ref is downloaded,
  safely extracted, and its files are synchronised into the project.

Safety rules, in order of importance:

1. **A locally edited file is never overwritten silently.** Its hash is compared
   with the hash recorded at install/update time; if it differs, the file is
   kept and reported, unless `--overwrite` is given — and even then the old
   version is copied into `.botai/backup/<stamp>/` first.
2. **Nothing outside the harness file list is written** (see
   `scripts/harness.py`). An upstream file that lands in a student directory is
   ignored by construction.
3. **A file upstream deleted is kept**, not removed, unless `--prune` is given.
4. **A failed download changes nothing**: the archive is fetched and extracted
   in a temporary directory before a single byte is written into the project.

Usage:
    python scripts/update.py                 # update in place
    python scripts/update.py --check         # report only (exit 10 if an update exists)
    python scripts/update.py --dry-run       # plan only, write nothing
    python scripts/update.py --overwrite     # also replace locally edited files (backed up)
    python scripts/update.py --ref <ref>     # update to a branch/tag/commit
    python scripts/update.py --source <url>  # another upstream repository

Exit codes: 0 ok / already current, 1 failure, 2 usage or environment error.
`--check` additionally uses 10 to mean "an update is available".
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as H  # noqa: E402


# ---------------------------------------------------------------------------
# Remote queries
# ---------------------------------------------------------------------------
def raw_version_url(source, ref):
    gh = H.github_owner_repo(source)
    if not gh:
        return None
    return "https://raw.githubusercontent.com/%s/%s/%s/%s" % (gh[0], gh[1], ref, H.VERSION_FILE)


def tarball_url(source, ref):
    gh = H.github_owner_repo(source)
    if not gh:
        return None
    return "https://codeload.github.com/%s/%s/tar.gz/%s" % (gh[0], gh[1], ref)


def remote_version(source, ref):
    """Version string published upstream, or None when it cannot be read.

    A local source is read from disk: an update that can only be checked over
    the network cannot be tested, and an untested update path is one that fails
    in front of a student.
    """
    local = local_source_dir(source)
    if local is not None:
        try:
            return (local / H.VERSION_FILE).read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    url = raw_version_url(source, ref)
    if not url:
        return None
    tmp = tempfile.mkdtemp(prefix="botai_check_")
    try:
        dest = os.path.join(tmp, "VERSION")
        H.download(url, dest, timeout=30)
        with open(dest, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except RuntimeError:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


        if len(parts) == 2:
            return parts[0]
    return ""


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------
def detect_mode(root, source):
    """`git` when the project is a checkout of the upstream repo, else `archive`."""
    if not H.is_git_worktree(root):
        return "archive"
    origin = H.normalize_repo_url(H.git_origin(root))
    if origin and origin == H.normalize_repo_url(source):
        return "git"
    return "archive"


def remote_revision(source, ref):
    """Commit sha of `ref` at `source`, when git is available to ask.

    `source` may be a URL or a local path, so both work — which is what makes
    the update path testable without a network round trip.
    """
    rc, out = H.git(["ls-remote", source, ref], timeout=60)
    if rc != 0 or not out:
        rc, out = H.git(["ls-remote", source, "HEAD"], timeout=60)
    if rc != 0 or not out:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            return parts[0]
    return ""


def upstream_files(extracted_root):
    """The harness file set of an extracted upstream tree."""
    return set(H.managed_files(extracted_root))


def local_source_dir(source):
    """The working tree of a local upstream checkout, or None.

    A local source is read directly, which is the same operation as reading an
    extracted tarball minus the network: it makes the update path testable end
    to end without downloading anything.
    """
    if not source or source.startswith(("http://", "https://", "git@", "ssh://")):
        return None
    p = Path(source).expanduser()
    return p.resolve() if p.is_dir() else None


# ---------------------------------------------------------------------------
# The update itself
# ---------------------------------------------------------------------------
def update_git(root, source, ref, dry, log=print):
    """Fast-forward the checkout onto the upstream ref."""
    changed = H.git_dirty(root)
    if changed:
        log("  отказ: в проекте есть незакоммиченные изменения (%d файлов):" % len(changed))
        for line in changed[:10]:
            log("    %s" % line)
        log("  закоммитьте или отложите их (git stash), затем повторите")
        return 1, "dirty"

    rc, head = H.git(["rev-parse", "HEAD"], cwd=root)
    if rc != 0:
        log("  ошибка: не удалось прочитать HEAD (%s)" % head)
        return 1, "no-head"

    if dry:
        log("  будет выполнено: git fetch %s %s && git merge --ff-only FETCH_HEAD" % (source, ref))
        return 0, "dry"

    rc, out = H.git(["fetch", "--quiet", source, ref], cwd=root, timeout=600)
    if rc != 0:
        log("  ошибка git fetch: %s" % out)
        log("  проверьте связь и адрес источника; ничего не изменено")
        return 1, "fetch-failed"

    rc, fetched = H.git(["rev-parse", "FETCH_HEAD"], cwd=root)
    if rc != 0:
        log("  ошибка: не удалось прочитать FETCH_HEAD")
        return 1, "no-fetch-head"

    if fetched == head:
        log("  уже самая свежая версия (%s)" % head[:8])
        return 0, "current"

    rc, _ = H.git(["merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], cwd=root)
    if rc != 0:
        log("  отказ: история разошлась (в проекте есть свои коммиты)")
        log("  быстрый проход невозможен; перенесите свои правки в отдельную ветку")
        return 1, "diverged"

    rc, out = H.git(["merge", "--ff-only", "FETCH_HEAD"], cwd=root, timeout=300)
    if rc != 0:
        log("  ошибка git merge --ff-only: %s" % out)
        return 1, "merge-failed"

    log("  обновлено: %s -> %s" % (head[:8], fetched[:8]))
    return 0, "updated"


def update_archive(root, source, ref, dry, overwrite, prune, record, log=print):
    """Sync an upstream tree (downloaded tarball, or a local checkout) in."""
    local = local_source_dir(source)
    url = None if local else tarball_url(source, ref)
    if not local and not url:
        log("  источник не похож ни на локальный каталог, ни на GitHub-репозиторий: %s" % source)
        log("  для другого источника используйте --mode git или скачайте вручную")
        return 1, "bad-source"

    tmp = tempfile.mkdtemp(prefix="botai_update_")
    try:
        if local:
            log("  источник — локальный каталог: %s" % local)
            src = local
        else:
            log("  скачивание: %s" % url)
            try:
                archive = os.path.join(tmp, "botai.tar.gz")
                H.download(url, archive, timeout=600)
            except RuntimeError as e:
                log("  ошибка загрузки: %s" % e)
                log("  ничего не изменено")
                return 1, "download-failed"

            unpack = os.path.join(tmp, "src")
            try:
                H.safe_extract_tar(archive, unpack)
            except Exception as e:  # noqa: BLE001 - an archive is untrusted input
                log("  ошибка распаковки: %s" % e)
                log("  ничего не изменено")
                return 1, "bad-archive"
            src = H.single_root(unpack)

        files = upstream_files(src)
        if not files:
            log("  в источнике нет файлов обвязки — похоже, это не botai")
            return 1, "empty-archive"

        new_version = H.read_version(src) or "(нет VERSION)"
        log("  версия в источнике: %s (файлов обвязки: %d)" % (new_version, len(files)))

        include = lambda rel: rel in files  # noqa: E731 - small local predicate
        backup_root = Path(root) / H.BACKUP_DIR / H.now_stamp()
        report = H.sync_tree(
            src, root,
            include=include,
            preserve=tuple(H.STUDENT_DIRS),
            prev=record.get("files") or {},
            overwrite=overwrite,
            prune=prune,
            backup_root=backup_root,
            dry=dry,
        )
        H.report_sync(report, log=log)

        if report["kept"] and not overwrite:
            log("  локально изменённые файлы оставлены как есть; чтобы заменить их "
                "версией из обвязки, повторите с --overwrite "
                "(старые версии будут скопированы в %s)" % backup_root)

        if dry:
            log("  предпросмотр: ничего не записано")
            return 0, "dry"

        H.relink_skill_farms(root, dry=False, log=log)
        write_record(root, record, source, ref, "archive", new_version,
                     remote_revision(source, ref), src)
        return 0, "updated"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write_record(root, old_record, source, ref, mode, version, revision, src_root=None):
    """Record what is installed now, so the next update can detect local edits."""
    files = H.fingerprint(root)
    if src_root is not None:
        upstream = H.fingerprint(src_root)
        # Upstream truth: the next update compares the project against this.
        for rel in upstream:
            files[rel] = upstream[rel]
    record = {
        "schema": 1,
        "version": version or H.read_version(root),
        "source": source,
        "ref": ref,
        "mode": mode,
        "revision": revision or H.git_head(root, short=False),
        "updated_at": H.now_iso(),
        "files": files,
    }
    if old_record and old_record.get("installed_at"):
        record["installed_at"] = old_record["installed_at"]
    H.save_record(root, record)
    return record


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(root=None, source=None, ref=None, mode="auto", check=False,
        dry_run=False, overwrite=False, prune=False, log=print):
    """Update an installed project. Returns an exit code."""
    root = Path(root or os.environ.get("BOTAI_ROOT") or ".").expanduser().resolve()

    if not (root / "AGENTS.md").is_file() and not (root / ".botai").is_dir():
        log("это не похоже на установленный проект botai: %s" % root)
        log("  нет ни AGENTS.md, ни .botai/")
        log("  установите проект: python scripts/install.py --dest <dir>")
        return 2

    record = H.load_record(root) or {}
    source = (source or os.environ.get("BOTAI_UPDATE_REPO")
              or record.get("source") or H.DEFAULT_SOURCE)
    ref = (ref or os.environ.get("BOTAI_UPDATE_REF")
           or record.get("ref") or H.DEFAULT_REF)
    if mode == "auto":
        mode = detect_mode(root, source)

    local_version = H.read_version(root) or "(неизвестна: установка до версии с VERSION)"
    local_rev = H.git_head(root, short=False)

    log("== обновление botai ==")
    log("  проект       : %s" % root)
    log("  источник     : %s (%s)" % (source, ref))
    log("  режим        : %s" % mode)
    log("  установлено  : %s%s" % (local_version,
                                   (" (%s)" % local_rev[:8]) if local_rev else ""))
    log("  записано     : %s" % (record.get("updated_at") or record.get("installed_at") or "нет записи"))

    if check:
        return check_only(root, source, ref, local_version, mode, log=log)

    if mode == "git":
        rc, reason = update_git(root, source, ref, dry_run, log=log)
        if rc == 0 and reason == "updated" and not dry_run:
            H.relink_skill_farms(root, dry=False, log=log)
            write_record(root, record, source, ref, "git", H.read_version(root),
                         H.git_head(root, short=False))
        return rc

    rc, reason = update_archive(root, source, ref, dry_run, overwrite, prune, record, log=log)
    if rc == 0 and reason == "updated":
        log("  готово: %s -> %s" % (local_version, H.read_version(root) or "?"))
        log("  студенческие каталоги (courses/, progress/, .botai/) не затронуты")
    return rc


def check_only(root, source, ref, local_version, mode, log=print):
    """Report whether a newer harness is published. Writes nothing."""
    log("  проверка (ничего не изменяется):")

    if mode == "git":
        head = H.git_head(root, short=False)
        rev = remote_revision(source, ref)
        if not rev:
            log("  не удалось получить данные upstream: нет связи, нет доступа "
                "или неверный адрес источника")
            log("  это момент, а не факт: проверьте сеть и повторите")
            return 1
        log("  HEAD          : %s" % (head[:8] or "(неизвестен)"))
        log("  upstream (%s): %s" % (ref, rev[:8]))
        if head == rev:
            log("  обновление не требуется")
            return 0
        log("  доступно обновление")
        log("  выполните: python scripts/cli.py update   (или: make update)")
        return 10

    remote = remote_version(source, ref)
    if remote is None:
        rev = remote_revision(source, ref)
        if rev:
            log("  версия upstream не читается (нет VERSION или нет доступа), "
                "но ref доступен: %s" % rev[:8])
            log("  вывод: проверить можно только обновлением (--dry-run)")
            return 1
        log("  не удалось получить данные upstream: нет связи, нет доступа "
            "или неверный адрес источника")
        log("  это момент, а не факт: проверьте сеть и повторите")
        return 1

    log("  версия upstream: %s" % remote)
    if remote == H.read_version(root):
        log("  обновление не требуется")
        return 0
    log("  доступно обновление: %s -> %s" % (local_version, remote))
    log("  выполните: python scripts/cli.py update   (или: make update)")
    return 10


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Самообновление установленного проекта botai",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", help="каталог проекта (по умолчанию текущий)")
    ap.add_argument("--source", help="репозиторий-источник (по умолчанию %s)" % H.DEFAULT_SOURCE)
    ap.add_argument("--ref", help="ветка/тег/коммит (по умолчанию %s)" % H.DEFAULT_REF)
    ap.add_argument("--mode", choices=["auto", "git", "archive"], default="auto",
                    help="способ обновления (auto по умолчанию)")
    ap.add_argument("--check", action="store_true",
                    help="только сообщить, есть ли обновление (код 10 = есть)")
    ap.add_argument("--dry-run", action="store_true", help="показать план, ничего не записывать")
    ap.add_argument("--overwrite", action="store_true",
                    help="заменять и локально изменённые файлы (с копией в .botai/backup/)")
    ap.add_argument("--prune", action="store_true",
                    help="удалять файлы, которых больше нет в обвязке (с копией в .botai/backup/)")
    args = ap.parse_args(argv)

    if args.check and args.dry_run:
        ap.error("--check и --dry-run несовместимы: выберите одно")

    return run(root=args.root, source=args.source, ref=args.ref, mode=args.mode,
               check=args.check, dry_run=args.dry_run,
               overwrite=args.overwrite, prune=args.prune)


if __name__ == "__main__":
    sys.exit(main())
