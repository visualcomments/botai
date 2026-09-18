#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for corpus acquisition and scoped retrieval.

The two claims under test are the ones that fail silently in a teaching tool:

* **A search cannot reach outside its course.** Not "should not" — the excluded
  path never enters the candidate set, so a hit for the teacher's answer key is
  impossible rather than filtered after the fact.
* **A citation that is present but wrong is reported as wrong.** Text that
  matches elsewhere in the same file is a coordinate error, not a valid quote;
  and a string match is never reported as proof of the claim made from it.

Also covered: the URL guard (SSRF), archive limits (a zip bomb lies about its
size), hash mismatch as a hard stop, and the legacy manifest adapter refusing to
guess.

Acceptance criteria: A08 (corpus A ready, B absent), A09 (mismatch/broken
archive keeps the old corpus), A27 (right quote in the wrong chunk; exact text
with a false conclusion).

Run:
    python3 tests/test_corpus.py
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import corpus, course, retrieval, schemas  # noqa: E402

_passed = 0
_failures: list[str] = []

EXAMPLE = ROOT / "examples" / "minimal-course"


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def expect_error(name, fn, code):
    try:
        fn()
    except corpus.CorpusError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def make_zip(files):
    """A zip archive in memory, as bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_manifest(files, *, url="https://example.invalid/corpus.zip",
                  artifact_id="corpus", unpack="zip", digest=None,
                  unpacked_max=None):
    payload = make_zip(files)
    entries = []
    for name, content in files.items():
        entries.append({
            "path": name,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "byte_size": len(content.encode("utf-8")),
            "role": "text",
            "artifact_id": artifact_id,
        })
    manifest = {
        "schema_version": 2,
        "corpus_id": "test-corpus",
        "version": "1",
        "source_revision": "2026-01",
        "artifacts": [{
            "artifact_id": artifact_id,
            "url": url,
            "sha256": digest or hashlib.sha256(payload).hexdigest(),
            "media_type": "application/zip",
            "unpack": unpack,
            "compressed_bytes": len(payload),
            "unpacked_bytes_max": unpacked_max,
            "auth_required": False,
        }],
        "files": entries,
        "index": {"kind": "none"},
    }
    return manifest, payload


# ---------------------------------------------------------------------------
# A08 — one corpus ready does not make another ready
# ---------------------------------------------------------------------------
def test_corpus_is_scoped_per_course():
    """The v1 harness had one global root: 'installed' could describe another
    course than the one being studied."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest, payload = make_manifest({"txt/a.txt": "содержимое курса A"})
        digest = corpus.manifest_hash(manifest)

        report = corpus.acquire(root, "course-a", manifest,
                                fetch=lambda url: payload)
        check("курс A установлен", report["status"] == "ready", report["status"])

        other = corpus.status(root, "course-b", manifest)
        check("курс B не считается готовым", other["status"] != "ready",
              other["status"])

        dir_a = corpus.installed_dir(root, "course-a", digest)
        dir_b = corpus.installed_dir(root, "course-b", digest)
        check("каталоги разных курсов не совпадают", dir_a != dir_b)
        check("каталог курса A существует", dir_a.is_dir())
        check("каталог курса B отсутствует", not dir_b.exists())


def test_missing_manifest_is_missing_not_not_required():
    """'We did not look' and 'there is nothing to check' are different facts."""
    with tempfile.TemporaryDirectory() as tmp:
        status = corpus.status(Path(tmp), "course-a", None)
        check("без манифеста статус missing", status["status"] == "missing",
              status["status"])

        manifest = {
            "schema_version": 2, "corpus_id": "empty", "version": "1",
            "artifacts": [], "files": [], "index": {"kind": "none"},
        }
        status = corpus.status(Path(tmp), "course-a", manifest)
        check("пустой манифест — not_required", status["status"] == "not_required",
              status["status"])


# ---------------------------------------------------------------------------
# A09 — a failed install must not destroy the working corpus
# ---------------------------------------------------------------------------
def test_hash_mismatch_is_a_hard_stop():
    manifest, payload = make_manifest({"txt/a.txt": "текст"}, digest="0" * 64)
    manifest["artifacts"][0]["sha256"] = "1" * 64
    with tempfile.TemporaryDirectory() as tmp:
        report = corpus.acquire(Path(tmp), "c", manifest, fetch=lambda url: payload)
        check("несовпадение хеша отклонено", report["status"] == "failed",
              report["status"])
        check("код ошибки назван",
              report.get("error", {}).get("code") == "HASH_MISMATCH",
              str(report.get("error")))
        check("ничего не установлено",
              not corpus.installed_dir(Path(tmp), "c",
                                       corpus.manifest_hash(manifest)).exists())


def test_failed_update_keeps_the_previous_corpus():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good, payload = make_manifest({"txt/a.txt": "версия 1"})
        first = corpus.acquire(root, "c", good, fetch=lambda url: payload)
        check("первая версия установлена", first["status"] == "ready")

        # A second manifest whose artifact does not match what the server serves.
        bad, other_payload = make_manifest({"txt/a.txt": "версия 2"})
        bad["artifacts"][0]["sha256"] = hashlib.sha256(
            "другие байты".encode("utf-8")).hexdigest()
        second = corpus.acquire(root, "c", bad, fetch=lambda url: other_payload)
        check("вторая версия отклонена", second["status"] == "failed")

        still = corpus.status(root, "c", good)
        check("ранее установленный корпус цел", still["status"] == "ready",
              still["status"])
        check("предупреждение говорит, что старый корпус не тронут",
              any("не тронут" in w for w in second["warnings"]),
              str(second["warnings"]))


def test_dry_run_writes_nothing():
    manifest, payload = make_manifest({"txt/a.txt": "текст"})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = corpus.acquire(root, "c", manifest, dry_run=True,
                                fetch=lambda url: payload)
        check("предпросмотр не устанавливает", report["status"] != "ready")
        check("в предпросмотре перечислены артефакты", len(report["downloaded"]) == 1)
        check("каталог корпуса не создан",
              not corpus.installed_dir(root, "c",
                                       corpus.manifest_hash(manifest)).exists())


# ---------------------------------------------------------------------------
# URL guard (SSRF)
# ---------------------------------------------------------------------------
def test_url_guard_refuses_dangerous_destinations():
    for url, code in (
        ("http://example.com/c.zip", "URL_SCHEME_REFUSED"),
        ("file:///etc/passwd", "URL_SCHEME_REFUSED"),
        ("https://localhost/c.zip", "URL_LOOPBACK"),
        ("https://127.0.0.1/c.zip", "URL_PRIVATE_ADDRESS"),
        ("https://169.254.169.254/latest/meta-data/", "URL_PRIVATE_ADDRESS"),
        ("https://10.0.0.5/c.zip", "URL_PRIVATE_ADDRESS"),
        ("https://192.168.1.1/c.zip", "URL_PRIVATE_ADDRESS"),
        ("https://metadata.google.internal/x", "URL_METADATA_HOST"),
        ("https://user:token@example.com/c.zip", "URL_HAS_CREDENTIALS"),
    ):
        expect_error("отклонён %s" % url, lambda u=url: corpus.check_url(u), code)


def test_signed_query_is_stripped_from_reports():
    """A private link carries a token; a report must not."""
    cleaned = corpus._clean_url("https://host/file.zip?X-Amz-Signature=SECRETVALUE")
    check("подпись не попадает в отчёт", "SECRETVALUE" not in cleaned, cleaned)
    check("адрес остался узнаваемым", cleaned.startswith("https://host/file.zip"),
          cleaned)


# ---------------------------------------------------------------------------
# Archive safety
# ---------------------------------------------------------------------------
def test_archive_refuses_traversal_and_links():
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        evil = work / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("../../outside.txt", "вышел наружу")
        expect_error("выход за каталог в архиве отклонён",
                     lambda: corpus.safe_extract(evil, work / "out"),
                     "ARCHIVE_UNSAFE_MEMBER")
        check("файл за пределами не создан",
              not (work.parent / "outside.txt").exists())

        absolute = work / "abs.zip"
        with zipfile.ZipFile(absolute, "w") as archive:
            archive.writestr("/abs.txt", "абсолютный путь")
        expect_error("абсолютный путь в архиве отклонён",
                     lambda: corpus.safe_extract(absolute, work / "out2"),
                     "ARCHIVE_UNSAFE_MEMBER")

        tar_path = work / "link.tar.gz"
        with tarfile.open(tar_path, "w:gz") as archive:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        expect_error("симлинк в архиве отклонён",
                     lambda: corpus.safe_extract(tar_path, work / "out3",
                                                 media_type="application/gzip"),
                     "ARCHIVE_LINK_REFUSED")


def test_archive_limit_is_enforced_while_writing():
    """A zip bomb declares a small size; the limit must not trust the header."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        bomb = work / "bomb.zip"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("big.txt", "A" * (2 * 1024 * 1024))
        expect_error("предел распаковки соблюдён",
                     lambda: corpus.safe_extract(bomb, work / "out",
                                                 max_bytes=100_000),
                     "ARCHIVE_TOO_LARGE")


def test_unverified_manifest_cannot_claim_ready():
    """A legacy manifest without hashes must never yield `ready`."""
    manifest, payload = make_manifest({"txt/a.txt": "текст"})
    manifest["artifacts"][0]["sha256"] = "0" * 64
    for entry in manifest["files"]:
        entry["sha256"] = "0" * 64
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = corpus.acquire(root, "c", manifest, fetch=lambda url: payload)
        check("корпус без хешей не называется готовым",
              report["status"] == "unverified", report["status"])
        check("сказано, что именно не проверено",
              report["checks"]["unverified_files"], str(report["checks"]))


# ---------------------------------------------------------------------------
# Legacy manifest adapter
# ---------------------------------------------------------------------------
def test_legacy_manifest_is_adapted_or_refused():
    legacy = {
        "id": "old-corpus",
        "version": "0.9",
        "downloads": [
            {"name": "texts", "url": "https://example.com/texts.zip",
             "hash": "a" * 64},
        ],
        "index": {"kind": "annoy", "config_path": "index/config.json"},
    }
    converted = corpus.adapt_legacy_manifest(legacy)
    check("legacy-манифест распознан", converted is not None)
    if converted:
        check("артефакты перенесены", len(converted["artifacts"]) == 1)
        check("описание честно называет уровень проверки",
              "v1" in (converted["description"] or ""), converted["description"])

    check("неизвестная форма не угадывается",
          corpus.adapt_legacy_manifest({"something": "else"}) is None)
    check("не-объект не угадывается",
          corpus.adapt_legacy_manifest(["не", "объект"]) is None)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
def test_corpus_schema_rejects_bad_manifests():
    manifest, _ = make_manifest({"txt/a.txt": "x"})
    schemas.validate(manifest, "corpus")
    check("корректный манифест принят", True)

    bad = dict(manifest)
    bad["artifacts"] = [dict(manifest["artifacts"][0])]
    bad["artifacts"][0]["sha256"] = "not-a-hash"
    try:
        schemas.validate(bad, "corpus")
    except schemas.SchemaError:
        check("некорректный sha256 отклонён", True)
    else:
        check("некорректный sha256 отклонён", False, "принят")


def test_unpack_kind_is_restricted():
    manifest, _ = make_manifest({"txt/a.txt": "x"})
    manifest["artifacts"][0]["unpack"] = "rar"
    try:
        schemas.validate(manifest, "corpus")
    except schemas.SchemaError:
        check("неизвестный способ распаковки отклонён", True)
    else:
        check("неизвестный способ распаковки отклонён", False, "принят")


# ---------------------------------------------------------------------------
# Retrieval scope
# ---------------------------------------------------------------------------
class Scoped:
    """An accepted course with a material snapshot, plus planted exclusion bait."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.course_dir = self.root / "courses" / "minimal-diff"
        self.course_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(EXAMPLE, self.course_dir)
        course.accept(self.root, self.course_dir, slug="minimal-diff",
                      repository_root="courses/minimal-diff")
        self.course = course.load_accepted(self.root, "minimal-diff")
        self.snapshot = self.course.binding["material_snapshot"]

        # A file under the excluded root, inside the snapshot. If the scope is
        # applied after ranking, this shows up in the results.
        secret = self.root / self.snapshot / "teacher"
        secret.mkdir(parents=True, exist_ok=True)
        (secret / "answers.md").write_text("ЭТАЛОННЫЙ ОТВЕТ 42", encoding="utf-8")

    def cleanup(self):
        self._tmp.cleanup()


def test_excluded_root_is_unreachable_by_search():
    s = Scoped()
    try:
        result = retrieval.search(s.course, "ЭТАЛОННЫЙ ОТВЕТ 42", limit=8,
                                  material_snapshot=s.snapshot)
        check("ответ преподавателя не найден поиском",
              len(result["hits"]) == 0, str(result["hits"]))
        check("исключения перечислены в области",
              result["scope"]["exclude_roots"] == ["teacher"],
              str(result["scope"]["exclude_roots"]))
    finally:
        s.cleanup()


def test_search_finds_legitimate_material_with_coordinates():
    s = Scoped()
    try:
        result = retrieval.search(s.course, "git diff", limit=5,
                                  material_snapshot=s.snapshot)
        check("материал курса найден", len(result["hits"]) > 0, str(result))
        hit = result["hits"][0]
        check("у попадания есть координаты", "lines" in hit["locator"],
              str(hit["locator"]))
        check("способ поиска назван честно",
              hit["retrieval_method"] == "text", hit["retrieval_method"])
        check("привязка к ревизии курса", "course_revision" in hit)
    finally:
        s.cleanup()


def test_chunk_search_without_chunks_says_so():
    s = Scoped()
    try:
        try:
            retrieval.search(s.course, "git diff", method="chunk",
                             material_snapshot=s.snapshot)
        except retrieval.RetrievalError as e:
            check("поиск по chunk без файла фрагментов назван невозможным",
                  e.code == "CHUNKS_UNAVAILABLE", e.code)
        else:
            check("поиск по chunk без файла фрагментов назван невозможным",
                  False, "поиск выполнен")
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# A27 — citation verification
# ---------------------------------------------------------------------------
def test_exact_quote_verifies():
    s = Scoped()
    try:
        lesson = "lessons/01-working-tree.md"
        lines = (s.root / s.snapshot / lesson).read_text(
            encoding="utf-8").splitlines()
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": lesson,
                           "locator": {"lines": {"start": 11, "end": 11}}},
            "verbatim": lines[10].strip(),
        }, material_snapshot=s.snapshot)
        check("точная цитата подтверждена", report["quote_status"] == "exact",
              report["quote_status"])
        check("координаты проверены отдельно",
              report["coordinate_check"] == "ok", report["coordinate_check"])
        allowed, warnings = retrieval.validate_citation(report)
        check("точную цитату можно показывать", allowed and not warnings)
    finally:
        s.cleanup()


def test_quote_status_is_never_proof_of_support():
    """A string match is not evidence that the claim is true."""
    s = Scoped()
    try:
        lesson = "lessons/01-working-tree.md"
        lines = (s.root / s.snapshot / lesson).read_text(
            encoding="utf-8").splitlines()
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": lesson,
                           "locator": {"lines": {"start": 11, "end": 11}}},
            "verbatim": lines[10].strip(),
        }, material_snapshot=s.snapshot)
        check("подтверждение смысла не заявляется",
              report["support_status"] == "not_checked", report["support_status"])
        check("поле существует, а не отсутствует", "support_status" in report)
    finally:
        s.cleanup()


def test_right_text_in_the_wrong_place_is_a_mismatch():
    """The seminar's OCA example: the link is present, the fragment is not."""
    s = Scoped()
    try:
        lesson = "lessons/01-working-tree.md"
        lines = (s.root / s.snapshot / lesson).read_text(
            encoding="utf-8").splitlines()
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": lesson,
                           "locator": {"lines": {"start": 1, "end": 1}}},
            "verbatim": lines[10].strip(),
        }, material_snapshot=s.snapshot)
        check("текст в другом месте — ошибка координат",
              report["quote_status"] == "mismatch", report["quote_status"])
        check("ошибка названа как ошибка координат",
              "координат" in report["quote_status_reason"],
              report["quote_status_reason"])
        allowed, _ = retrieval.validate_citation(report)
        check("такую цитату нельзя показывать как точную", allowed is False)
    finally:
        s.cleanup()


def test_quote_from_an_excluded_path_is_refused():
    s = Scoped()
    try:
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": "teacher/answers.md",
                           "locator": {"lines": {"start": 1, "end": 1}}},
            "verbatim": "ЭТАЛОННЫЙ ОТВЕТ 42",
        }, material_snapshot=s.snapshot)
        check("цитата из закрытых материалов не проверяется",
              report["quote_status"] == "unverifiable", report["quote_status"])
        check("сказано, что путь вне разрешённых материалов",
              "вне разрешённых" in report["quote_status_reason"],
              report["quote_status_reason"])
    finally:
        s.cleanup()


def test_out_of_range_locator_is_not_clamped():
    s = Scoped()
    try:
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": "lessons/01-working-tree.md",
                           "locator": {"lines": {"start": 9000, "end": 9001}}},
            "verbatim": "что угодно",
        }, material_snapshot=s.snapshot)
        check("диапазон за концом файла не подгоняется",
              report["quote_status"] == "unverifiable", report["quote_status"])
        check("причина названа",
              report["coordinate_check"] == "line_range_beyond_file",
              report["coordinate_check"])
    finally:
        s.cleanup()


def test_pdf_and_chunk_locators_are_honestly_unverifiable():
    """Claiming to verify a PDF page without a parser would be a lie."""
    s = Scoped()
    try:
        for locator, expected in (
            ({"pdf_page": {"physical_page": 3}}, "PDF_PAGE_UNVERIFIABLE"),
            ({"chunk": {"chunk_id": 1, "index_hash": "a" * 64}}, "CHUNK_UNVERIFIABLE"),
            ({"notebook_cell": {"cell_id": "c1"}}, "NOTEBOOK_CELL_UNVERIFIABLE"),
        ):
            report = retrieval.verify_quote(s.course, {
                "source_ref": {"path": "lessons/01-working-tree.md",
                               "locator": locator},
                "verbatim": "текст",
            }, material_snapshot=s.snapshot)
            check("координаты %s названы непроверяемыми"
                  % list(locator)[0],
                  report["quote_status"] == "unverifiable", report["quote_status"])
    finally:
        s.cleanup()


def test_normalised_match_is_reported_as_weaker():
    s = Scoped()
    try:
        lesson = "lessons/01-working-tree.md"
        lines = (s.root / s.snapshot / lesson).read_text(
            encoding="utf-8").splitlines()
        spaced = "  " + "   ".join(lines[10].split()) + "  "
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": lesson,
                           "locator": {"lines": {"start": 11, "end": 11}}},
            "verbatim": spaced,
        }, material_snapshot=s.snapshot)
        check("совпадение после нормализации названо своим именем",
              report["quote_status"] in ("exact", "normalized_match"),
              report["quote_status"])
        if report["quote_status"] == "normalized_match":
            check("нормализация описана", bool(report["normalisation"]),
                  str(report["normalisation"]))
            allowed, warnings = retrieval.validate_citation(report)
            check("предложено показывать с оговоркой",
                  allowed and warnings, str(warnings))
    finally:
        s.cleanup()


def test_changed_file_invalidates_the_quote_coordinates():
    s = Scoped()
    try:
        lesson = "lessons/01-working-tree.md"
        target = s.root / s.snapshot / lesson
        lines = target.read_text(encoding="utf-8").splitlines()
        report = retrieval.verify_quote(s.course, {
            "source_ref": {"path": lesson, "sha256": "b" * 64,
                           "locator": {"lines": {"start": 11, "end": 11}}},
            "verbatim": lines[10].strip(),
        }, material_snapshot=s.snapshot)
        check("изменившийся файл делает цитату недействительной",
              report["quote_status"] == "mismatch", report["quote_status"])
        check("сказано, что координаты относятся к другой версии",
              "другой версии" in report["quote_status_reason"],
              report["quote_status_reason"])
    finally:
        s.cleanup()


def test_search_without_accepted_course_is_refused():
    try:
        retrieval.search(None, "запрос")
    except retrieval.RetrievalError as e:
        check("поиск без принятого курса отклонён",
              e.code == "COURSE_NOT_ACCEPTED", e.code)
    else:
        check("поиск без принятого курса отклонён", False, "поиск выполнен")


def test_empty_query_is_refused():
    s = Scoped()
    try:
        try:
            retrieval.search(s.course, "   ", material_snapshot=s.snapshot)
        except retrieval.RetrievalError as e:
            check("пустой запрос отклонён", e.code == "QUERY_EMPTY", e.code)
        else:
            check("пустой запрос отклонён", False, "поиск выполнен")
    finally:
        s.cleanup()


def main():
    tests = [
        test_corpus_is_scoped_per_course,
        test_missing_manifest_is_missing_not_not_required,
        test_hash_mismatch_is_a_hard_stop,
        test_failed_update_keeps_the_previous_corpus,
        test_dry_run_writes_nothing,
        test_url_guard_refuses_dangerous_destinations,
        test_signed_query_is_stripped_from_reports,
        test_archive_refuses_traversal_and_links,
        test_archive_limit_is_enforced_while_writing,
        test_unverified_manifest_cannot_claim_ready,
        test_legacy_manifest_is_adapted_or_refused,
        test_corpus_schema_rejects_bad_manifests,
        test_unpack_kind_is_restricted,
        test_excluded_root_is_unreachable_by_search,
        test_search_finds_legitimate_material_with_coordinates,
        test_chunk_search_without_chunks_says_so,
        test_exact_quote_verifies,
        test_quote_status_is_never_proof_of_support,
        test_right_text_in_the_wrong_place_is_a_mismatch,
        test_quote_from_an_excluded_path_is_refused,
        test_out_of_range_locator_is_not_clamped,
        test_pdf_and_chunk_locators_are_honestly_unverifiable,
        test_normalised_match_is_reported_as_weaker,
        test_changed_file_invalidates_the_quote_coordinates,
        test_search_without_accepted_course_is_refused,
        test_empty_query_is_refused,
    ]
    for test in tests:
        print("== %s ==" % test.__name__)
        try:
            test()
        except Exception as e:  # noqa: BLE001
            import traceback
            _failures.append(test.__name__)
            print("  FAIL %s выбросил %s: %s" % (test.__name__, type(e).__name__, e))
            traceback.print_exc()

    print()
    print("%d passed, %d failed" % (_passed, len(_failures)))
    if _failures:
        for name in _failures:
            print("  - %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
