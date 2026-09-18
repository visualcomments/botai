#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the accepted course: contract, binding, objective graph.

The property under test is the one that makes every other guarantee possible:
**what governs help is a snapshot a human accepted, not a file anyone can
edit afterwards.** A student branch that flips an assignment from `graded` to
`practice`, or adds a new assignment, must not change what the running agent is
allowed to do.

Two other rules are checked here because they are cheap to get wrong and
expensive to notice:

* an assignment the contract does not declare is `unknown`, which behaves like
  `graded` — the default is never "probably practice";
* a course whose track is broken (missing prerequisite, cycle, prerequisite in
  a later module) is refused at acceptance with the defect named, rather than
  failing later when the planner walks the graph.

Run:
    python3 tests/test_course.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import course  # noqa: E402

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
    except course.CourseError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Workspace:
    """A throwaway workspace with one installed course."""

    def __init__(self, source=EXAMPLE, slug="minimal-diff"):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.slug = slug
        self.course_dir = self.root / "courses" / slug
        self.course_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, self.course_dir)

    def contract(self):
        return course.load_json(self.course_dir / "botai" / "course.json")

    def write_contract(self, document):
        path = self.course_dir / "botai" / "course.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")

    def track(self):
        return course.load_json(self.course_dir / "botai" / "track.json")

    def write_track(self, document):
        path = self.course_dir / "botai" / "track.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")

    def accept(self, **kwargs):
        kwargs.setdefault("slug", self.slug)
        kwargs.setdefault("repository_root", "courses/%s" % self.slug)
        return course.accept(self.root, self.course_dir, **kwargs)

    def cleanup(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
def test_inspect_reports_a_contract_without_writing():
    ws = Workspace()
    try:
        before = sorted(p.relative_to(ws.root).as_posix()
                        for p in ws.root.rglob("*"))
        report = course.inspect(ws.course_dir)
        after = sorted(p.relative_to(ws.root).as_posix()
                       for p in ws.root.rglob("*"))
        check("осмотр читает контракт", report["has_contract"])
        check("осмотр видит курс", report["course_id"] == "minimal-diff")
        check("осмотр ничего не пишет", before == after)
        check("осмотр не создаёт .botai", not (ws.root / ".botai").exists())
        check("готовность inspectable", report["readiness"] == "inspectable",
              report["readiness"])
        check("проблем нет", report["problems"] == [], str(report["problems"])[:200])
    finally:
        ws.cleanup()


def test_inspect_without_contract_is_not_ready():
    """No contract is not "no rules": it is unknown assessment everywhere."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "courses" / "bare"
        d.mkdir(parents=True)
        (d / "README.md").write_text("# bare\n", encoding="utf-8")
        report = course.inspect(d)
        check("курс без контракта не готов", report["readiness"] == "no_contract")
        check("отсутствие контракта названо",
              any(p["code"] == "COURSE_CONTRACT_MISSING" for p in report["problems"]))


def test_accept_freezes_the_contract():
    """After acceptance, editing the contract must not change the policy."""
    ws = Workspace()
    try:
        binding = ws.accept()
        accepted = course.load_accepted(ws.root, ws.slug)
        check("принятая оцениваемость — practice",
              accepted.assessment_of("practice-diff")[0] == "practice")

        # The student branch flips it to graded and adds a new assignment.
        document = ws.contract()
        document["assignments"][0]["assessment"] = "graded"
        extra = dict(document["assignments"][0])
        extra["assignment_id"] = "homework-3"
        document["assignments"].append(extra)
        ws.write_contract(document)

        still = course.load_accepted(ws.root, ws.slug)
        check("правка рабочей копии не меняет политику",
              still.assessment_of("practice-diff")[0] == "practice",
              still.assessment_of("practice-diff"))
        check("новое задание не появляется в принятом контракте",
              still.assignment("homework-3") is None)

        diff = course.diff_against_disk(ws.root, ws.course_dir)
        check("расхождение контракта обнаружено", diff["contract_changed"])
        check("смена оцениваемости названа",
              any(c["assignment_id"] == "practice-diff" and c["field"] == "assessment"
                  for c in diff["assessment_changed"]),
              str(diff["assessment_changed"]))
        check("новое задание перечислено",
              [a["assignment_id"] for a in diff["new_assignments"]] == ["homework-3"],
              str(diff["new_assignments"]))
        check("принятый хеш не изменился",
              still.contract_hash == binding["accepted_contract_hash"])
    finally:
        ws.cleanup()


def test_undeclared_assignment_is_unknown():
    ws = Workspace()
    try:
        ws.accept()
        accepted = course.load_accepted(ws.root, ws.slug)
        assessment, why = accepted.assessment_of("homework-3")
        check("задание вне контракта — unknown", assessment == "unknown", assessment)
        check("причина названа", "не установлена" in why)
        check("assignment_id=None тоже unknown",
              accepted.assessment_of(None)[0] == "unknown")
    finally:
        ws.cleanup()


def test_accepted_snapshot_cannot_be_edited_in_place():
    """The trust boundary is the snapshot: tampering with it must be detected."""
    ws = Workspace()
    try:
        binding = ws.accept()
        snapshot = course.accepted_dir(ws.root, binding["accepted_contract_hash"])
        target = snapshot / "course.json"
        document = course.load_json(target)
        document["assignments"][0]["assessment"] = "practice"
        document["title"] = "подменённый курс"
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        expect_error("подмена принятого снимка обнаружена",
                     lambda: course.load_accepted(ws.root, ws.slug),
                     "ACCEPTED_SNAPSHOT_CHANGED")
    finally:
        ws.cleanup()


def test_unaccepted_course_is_refused():
    ws = Workspace()
    try:
        expect_error("непринятый курс не читается как политика",
                     lambda: course.load_accepted(ws.root, ws.slug),
                     "COURSE_NOT_ACCEPTED")
        check("binding отсутствует", course.load_binding(ws.root, ws.slug) is None)
    finally:
        ws.cleanup()


def test_accept_refuses_a_missing_assignment_file():
    ws = Workspace()
    try:
        document = ws.contract()
        document["assignments"][0]["path"] = "assignments/does-not-exist.md"
        ws.write_contract(document)
        expect_error("объявленное, но отсутствующее задание отклоняет принятие",
                     ws.accept, "ASSIGNMENT_FILE_MISSING")
        check("binding не создан", course.load_binding(ws.root, ws.slug) is None)
    finally:
        ws.cleanup()


def test_accept_refuses_a_broken_track():
    ws = Workspace()
    try:
        track = ws.track()
        track["objectives"][1]["prerequisites"] = ["no-such-objective"]
        ws.write_track(track)
        expect_error("несуществующая предпосылка отклоняет принятие",
                     ws.accept, "TRACK_INVALID")
    finally:
        ws.cleanup()


def test_graph_checks_name_the_defect():
    objectives = [
        {"objective_id": "a", "module_id": "m1", "title": "A", "prerequisites": ["b"],
         "check_kinds": ["explain"]},
        {"objective_id": "b", "module_id": "m1", "title": "B", "prerequisites": ["a"],
         "check_kinds": ["explain"]},
    ]
    modules = [{"module_id": "m1", "title": "M1", "order": 0, "objective_ids": ["a", "b"]}]
    problems = course.check_track({"modules": modules, "objectives": objectives})
    codes = {p["code"] for p in problems}
    check("цикл предпосылок найден", "PREREQUISITE_CYCLE" in codes, str(codes))

    objectives = [
        {"objective_id": "a", "module_id": "m1", "title": "A", "prerequisites": [],
         "check_kinds": ["explain"]},
        {"objective_id": "a", "module_id": "m1", "title": "A2", "prerequisites": [],
         "check_kinds": ["explain"]},
    ]
    problems = course.check_track({"modules": modules, "objectives": objectives})
    check("повтор идентификатора найден",
          any(p["code"] == "DUPLICATE_ID" for p in problems))


def test_prerequisite_from_a_later_module_is_reported():
    """A dependency on later material is a course defect, not personalisation."""
    modules = [
        {"module_id": "m1", "title": "M1", "order": 0, "objective_ids": ["a"]},
        {"module_id": "m2", "title": "M2", "order": 1, "objective_ids": ["b"]},
    ]
    objectives = [
        {"objective_id": "a", "module_id": "m1", "title": "A",
         "prerequisites": ["b"], "check_kinds": ["explain"]},
        {"objective_id": "b", "module_id": "m2", "title": "B",
         "prerequisites": [], "check_kinds": ["explain"]},
    ]
    problems = course.check_track({"modules": modules, "objectives": objectives})
    check("предпосылка из позднего модуля названа",
          any(p["code"] == "PREREQUISITE_LATER_MODULE" for p in problems),
          str([p["code"] for p in problems]))


def test_topological_order_is_stable_and_respects_prerequisites():
    ws = Workspace()
    try:
        ws.accept()
        accepted = course.load_accepted(ws.root, ws.slug)
        order = accepted.ordered_objectives()
        check("предпосылка идёт раньше цели",
              order.index("explain-diff") < order.index("apply-staging"), str(order))
        check("порядок воспроизводим", accepted.ordered_objectives() == order)
        check("следующая обязательная цель — первая в порядке",
              accepted.next_required_objective([]) == "explain-diff")
        check("выполненная цель пропускается",
              accepted.next_required_objective(["explain-diff"]) == "apply-staging")
    finally:
        ws.cleanup()


def test_material_scope_exclusions_win():
    ws = Workspace()
    try:
        ws.accept()
        accepted = course.load_accepted(ws.root, ws.slug)
        check("урок разрешён",
              accepted.is_readable_path("lessons/01-working-tree.md"))
        check("исключённый корень запрещён",
              not accepted.is_readable_path("teacher/answers.md"))
        check("traversal не обходит исключение",
              not accepted.is_readable_path("lessons/../teacher/answers.md"))
        check("абсолютный путь запрещён",
              not accepted.is_readable_path("/etc/passwd"))
        check("путь вне разрешённых корней запрещён",
              not accepted.is_readable_path("solutions/answer.md"))
    finally:
        ws.cleanup()


def test_material_snapshot_is_isolated_from_later_edits():
    """The text the agent cites must be the text that was accepted."""
    ws = Workspace()
    try:
        binding = ws.accept()
        snapshot = ws.root / binding["material_snapshot"]
        lesson = snapshot / "lessons" / "01-working-tree.md"
        check("снимок материала создан", lesson.is_file())
        check("исключённый корень в снимок не попал",
              not (snapshot / "teacher").exists())

        # Edit the lesson in the working tree afterwards.
        source = ws.course_dir / "lessons" / "01-working-tree.md"
        source.write_text("# подменённый урок\n", encoding="utf-8")
        check("правка рабочей копии не меняет снимок",
              "подменённый урок" not in lesson.read_text(encoding="utf-8"))
    finally:
        ws.cleanup()


def test_material_snapshot_is_workspace_relative():
    ws = Workspace()
    try:
        binding = ws.accept()
        path = binding["material_snapshot"]
        check("снимок хранится относительным путём",
              not Path(path).is_absolute() and path.startswith(".botai/"), path)
        check("снимок внутри рабочего пространства",
              (ws.root / path).is_dir())
    finally:
        ws.cleanup()


def test_url_credentials_are_stripped():
    ws = Workspace()
    try:
        binding = ws.accept(source_url="https://student:ghp_secret@github.com/o/r.git")
        check("токен не сохранён в binding",
              "ghp_secret" not in (binding["source_url"] or ""),
              binding["source_url"])
        check("адрес сохранён", binding["source_url"] == "https://github.com/o/r.git")
    finally:
        ws.cleanup()


def test_binding_survives_a_restart():
    """Acceptance is durable: reopening the workspace must find the same policy."""
    ws = Workspace()
    try:
        binding = ws.accept()
        reloaded = course.load_binding(ws.root, ws.slug)
        check("binding прочитан после перезапуска", reloaded is not None)
        check("хеш контракта сохранён",
              reloaded["accepted_contract_hash"] == binding["accepted_contract_hash"])
        listed = course.list_bindings(ws.root)
        check("курс виден в списке принятых",
              [b["course_id"] for b in listed] == ["minimal-diff"],
              str([b["course_id"] for b in listed]))
    finally:
        ws.cleanup()


def test_reaccepting_unchanged_contract_reuses_the_snapshot():
    ws = Workspace()
    try:
        first = ws.accept()
        second = ws.accept()
        check("повторное принятие не меняет хеш",
              first["accepted_contract_hash"] == second["accepted_contract_hash"])
        check("binding остаётся читаемым",
              course.load_accepted(ws.root, ws.slug).course_id == "minimal-diff")
    finally:
        ws.cleanup()


def test_course_contract_declared_paths_cannot_escape():
    """`syllabus_path`/`track_path` come from the course, so they are untrusted.

    Two independent defences should catch this: the JSON Schema rejects `..` in
    a `relative_path`, and `_safe_relative` refuses anything resolving outside
    the course. The schema fires first, so the asserted property is "refused
    with a path-related code", not which of the two layers spoke — pinning the
    inner code would make the test fail if the schema were tightened, which is
    the wrong direction.
    """
    ws = Workspace()
    try:
        document = ws.contract()
        document["track_path"] = "../../../etc/passwd"
        ws.write_contract(document)
        try:
            ws.accept()
        except course.CourseError as e:
            check("путь программы за пределы курса отклонён",
                  e.code in ("COURSE_PATH_OUTSIDE", "CONTRACT_INVALID"),
                  "код %s" % e.code)
        else:
            check("путь программы за пределы курса отклонён", False, "принятие прошло")

        # And the resolver itself refuses what the schema would also refuse,
        # so a course is not safe merely because a schema happened to catch it.
        try:
            course._safe_relative(ws.course_dir, "../../etc/passwd", what="track_path")
        except course.CourseError as e:
            check("резолвер путей отклоняет выход за курс",
                  e.code == "COURSE_PATH_OUTSIDE", "код %s" % e.code)
        else:
            check("резолвер путей отклоняет выход за курс", False, "путь разрешён")
    finally:
        ws.cleanup()


def test_contract_with_a_bom_is_readable():
    """A BOM is an editor artifact, not a reason to reject a course."""
    ws = Workspace()
    try:
        path = ws.course_dir / "botai" / "course.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                        encoding="utf-8-sig", newline="\n")
        report = course.inspect(ws.course_dir)
        check("контракт с BOM читается", report["has_contract"] and not report["problems"],
              str(report["problems"])[:200])
    finally:
        ws.cleanup()


def test_broken_working_copy_is_reported_not_raised():
    ws = Workspace()
    try:
        ws.accept()
        (ws.course_dir / "botai" / "course.json").write_text("{ not json", encoding="utf-8")
        diff = course.diff_against_disk(ws.root, ws.course_dir)
        check("битый контракт — это замечание, а не падение",
              any(p["code"] == "COURSE_JSON_INVALID" for p in diff["problems"]),
              str(diff["problems"])[:200])
        check("битый контракт отмечен как изменение", diff["contract_changed"])
    finally:
        ws.cleanup()


def main():
    tests = [
        test_inspect_reports_a_contract_without_writing,
        test_inspect_without_contract_is_not_ready,
        test_accept_freezes_the_contract,
        test_undeclared_assignment_is_unknown,
        test_accepted_snapshot_cannot_be_edited_in_place,
        test_unaccepted_course_is_refused,
        test_accept_refuses_a_missing_assignment_file,
        test_accept_refuses_a_broken_track,
        test_graph_checks_name_the_defect,
        test_prerequisite_from_a_later_module_is_reported,
        test_topological_order_is_stable_and_respects_prerequisites,
        test_material_scope_exclusions_win,
        test_material_snapshot_is_isolated_from_later_edits,
        test_material_snapshot_is_workspace_relative,
        test_url_credentials_are_stripped,
        test_binding_survives_a_restart,
        test_reaccepting_unchanged_contract_reuses_the_snapshot,
        test_course_contract_declared_paths_cannot_escape,
        test_contract_with_a_bom_is_readable,
        test_broken_working_copy_is_reported_not_raised,
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
