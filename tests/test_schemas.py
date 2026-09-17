#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the v2 contracts and the core schema loader.

A contract that is only described in prose does not constrain anything. These
tests check that the schemas are loaded offline, that unknown versions and
remote references are refused, and — the part that matters for teaching — that
the example course's own source references actually point at text that exists.

A citation whose coordinates do not exist is the failure mode the seminar's OCA
example describes: the link is present, the fragment is not. The same check
applies to a course contract citing its own syllabus.

Run:
    python3 tests/test_schemas.py
    python3 -m pytest tests/test_schemas.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Windows consoles default to a legacy code page (cp1252/cp866), where the
# Russian test names below are unmappable and printing raises
# UnicodeEncodeError — the suite would die before checking anything.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import schemas  # noqa: E402

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
    except schemas.SchemaError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


# ---------------------------------------------------------------------------
def test_all_contracts_load_offline():
    for contract in ("course", "track", "progress"):
        status, detail = schemas.validation_status(contract)
        check(f"схема {contract} загружается", status == "ok", detail)


def test_schemas_never_reference_the_network():
    """Every $ref must resolve inside the schemas directory."""
    for path in sorted(schemas.SCHEMA_DIR.glob("*.json")):
        refs = list(schemas._iter_refs(json.loads(path.read_text(encoding="utf-8"))))
        remote = [r for r in refs if "://" in r.split("#", 1)[0]]
        check(f"{path.name}: только локальные $ref", not remote, str(remote))


def test_remote_ref_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "course.schema.json").write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"x": {"$ref": "https://evil.example/s.json"}},
        }), encoding="utf-8")
        expect_error("внешний $ref отвергается",
                     lambda: schemas.validate({"x": 1}, "course", schema_dir=d),
                     "SCHEMA_REMOTE_REF")


def test_ref_escaping_the_schema_dir_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "course.schema.json").write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"x": {"$ref": "../../etc/passwd"}},
        }), encoding="utf-8")
        expect_error("$ref за пределы каталога схем отвергается",
                     lambda: schemas.validate({"x": 1}, "course", schema_dir=d),
                     "SCHEMA_REF_ESCAPE")


def test_unsupported_version_is_an_error():
    expect_error("schema_version=3 отвергается",
                 lambda: schemas.validate({"schema_version": 3}, "course"),
                 "SCHEMA_VERSION_UNSUPPORTED")


def test_unknown_assessment_is_representable():
    """`unknown` must be a legal value: it is the strict state, not a gap."""
    track = json.loads((EXAMPLE / "botai" / "track.json").read_text(encoding="utf-8"))
    schemas.validate(track, "track")
    course = json.loads((EXAMPLE / "botai" / "course.json").read_text(encoding="utf-8"))
    course["assignments"][0]["assessment"] = "unknown"
    schemas.validate(course, "course")
    check("assessment=unknown допустим (строгий режим)", True)

    course["assignments"][0]["assessment"] = "optional"
    expect_error("произвольное assessment отвергается",
                 lambda: schemas.validate(course, "course"), "CONTRACT_INVALID")


def test_extra_fields_are_rejected():
    course = json.loads((EXAMPLE / "botai" / "course.json").read_text(encoding="utf-8"))
    course["graded_policy_override"] = "everything is practice"
    expect_error("неизвестное поле отвергается (не расширяет контракт)",
                 lambda: schemas.validate(course, "course"), "CONTRACT_INVALID")


def test_traversal_in_contract_paths_is_rejected():
    course = json.loads((EXAMPLE / "botai" / "course.json").read_text(encoding="utf-8"))
    for bad in ("../secrets.md", "/etc/passwd", "C:key.pem", "a/../../b.md"):
        course["syllabus_path"] = bad
        expect_error(f"путь {bad!r} отвергается в контракте",
                     lambda c=course: schemas.validate(c, "course"),
                     "CONTRACT_INVALID")
    course["syllabus_path"] = "syllabus.md"
    schemas.validate(course, "course")


# ---------------------------------------------------------------------------
def test_example_course_sources_exist_and_match():
    """Every declared source_ref must point at real text, with a matching hash."""
    course = json.loads((EXAMPLE / "botai" / "course.json").read_text(encoding="utf-8"))
    track = json.loads((EXAMPLE / "botai" / "track.json").read_text(encoding="utf-8"))
    schemas.validate(course, "course")
    schemas.validate(track, "track")

    refs = []
    for assignment in course["assignments"]:
        if assignment.get("assessment_source"):
            refs.append(("assignment %s" % assignment["assignment_id"],
                         assignment["assessment_source"]))
    for module in track["modules"]:
        for ref in module.get("source_refs", []):
            refs.append(("module %s" % module["module_id"], ref))
    for objective in track["objectives"]:
        for ref in objective.get("source_refs", []):
            refs.append(("objective %s" % objective["objective_id"], ref))

    check("в примере есть ссылки на источники", bool(refs))

    import hashlib
    for label, ref in refs:
        target = EXAMPLE / ref["path"]
        if not target.is_file():
            check(f"{label}: источник существует", False, ref["path"])
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        check(f"{label}: sha256 совпадает", digest == ref["sha256"],
              "%s != %s" % (digest[:12], ref["sha256"][:12]))

        locator = ref["locator"]
        lines = target.read_text(encoding="utf-8").splitlines()
        if "lines" in locator:
            start = locator["lines"]["start"]
            end = locator["lines"]["end"]
            check(f"{label}: координаты внутри файла",
                  1 <= start <= end <= len(lines),
                  "строки %s-%s при %d строках" % (start, end, len(lines)))


def test_track_graph_is_consistent():
    track = json.loads((EXAMPLE / "botai" / "track.json").read_text(encoding="utf-8"))
    objective_ids = {o["objective_id"] for o in track["objectives"]}
    module_ids = {m["module_id"] for m in track["modules"]}

    for module in track["modules"]:
        unknown = set(module["objective_ids"]) - objective_ids
        check(f"модуль {module['module_id']} ссылается на существующие цели",
              not unknown, str(unknown))
    for objective in track["objectives"]:
        check(f"цель {objective['objective_id']} в существующем модуле",
              objective["module_id"] in module_ids)
        unknown = set(objective["prerequisites"]) - objective_ids
        check(f"предпосылки {objective['objective_id']} существуют",
              not unknown, str(unknown))

    # No prerequisite cycles: a cycle would make the plan unsatisfiable.
    edges = {o["objective_id"]: set(o["prerequisites"]) for o in track["objectives"]}
    state = {}

    def visit(node):
        if state.get(node) == "open":
            return False
        if state.get(node) == "done":
            return True
        state[node] = "open"
        for dep in edges.get(node, ()):
            if not visit(dep):
                return False
        state[node] = "done"
        return True

    acyclic = all(visit(node) for node in edges)
    check("граф предпосылок без циклов", acyclic)


def test_progress_contract_requires_evidence_for_states():
    """A `demonstrated` state must be able to name its evidence."""
    progress = {
        "schema_version": 2,
        "learner_id": "0c7fa86f-48de-4395-9694-c820be02e5ad",
        "course_id": "minimal-diff",
        "course_revision": {"kind": "git", "id": "a" * 40},
        "objective_states": [{
            "objective_id": "explain-diff", "stage": "demonstrated",
            "evidence_ids": ["11111111-1111-4111-8111-111111111111"],
            "assistance_max": "NONE", "consecutive_stuck_sessions": 0,
        }],
        "sessions": [],
    }
    schemas.validate(progress, "progress")
    check("progress с доказательством принимается", True)

    progress["objective_states"][0]["stage"] = "mastered"
    expect_error("несуществующая стадия mastery отвергается",
                 lambda: schemas.validate(progress, "progress"), "CONTRACT_INVALID")


def test_legacy_course_fixture_is_present():
    """The v1-shaped fixture used by migration tests must exist."""
    legacy = ROOT / "examples" / "legacy-course"
    check("есть вход для проверки миграции", legacy.is_dir())
    md = list(legacy.glob("progress/*.md"))
    check("в фикстуре миграции есть legacy-дневник", bool(md), str(list(legacy.iterdir())))


def test_fixtures_are_not_excluded_from_git():
    """Every fixture file must be committable.

    A file that exists locally but is excluded by `.gitignore` passes here and
    fails in CI, which is exactly how the migration fixture was lost once: an
    unanchored `progress/` pattern swallowed `examples/legacy-course/progress/`.
    The check asks git directly, so it catches the whole class of defect rather
    than the one instance.
    """
    import subprocess

    required = [
        "examples/legacy-course/progress/python-101.md",
        "examples/legacy-course/courses/python-101/syllabus.md",
        "examples/minimal-course/botai/course.json",
        "examples/minimal-course/botai/track.json",
        "schemas/v2/course.schema.json",
        "requirements-core.lock",
    ]
    for rel in required:
        path = ROOT / rel
        check(f"фикстура на месте: {rel}", path.is_file())
        ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT,
                                 capture_output=True, text=True)
        check(f"фикстура не исключена из git: {rel}", ignored.returncode != 0,
              "путь игнорируется правилом из .gitignore")
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                                 capture_output=True, text=True)
        # `git ls-files --error-unmatch` fails for an untracked file; that is
        # only a defect once the file has been committed, so report it as a
        # distinct signal rather than a failure of the ignore rule.
        if tracked.returncode != 0:
            print(f"       note: {rel} ещё не в индексе (будет добавлен этим коммитом)")


def main() -> int:
    print("[schemas: контракты загружаются локально]")
    test_all_contracts_load_offline()
    test_schemas_never_reference_the_network()
    test_remote_ref_is_refused()
    test_ref_escaping_the_schema_dir_is_refused()
    print("[schemas: строгость]")
    test_unsupported_version_is_an_error()
    test_unknown_assessment_is_representable()
    test_extra_fields_are_rejected()
    test_traversal_in_contract_paths_is_rejected()
    print("[examples: минимальный курс согласован]")
    test_example_course_sources_exist_and_match()
    test_track_graph_is_consistent()
    test_progress_contract_requires_evidence_for_states()
    test_legacy_course_fixture_is_present()
    test_fixtures_are_not_excluded_from_git()

    print(f"\n{_passed} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


def test_schemas() -> None:
    """Pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
