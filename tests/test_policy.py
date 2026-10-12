#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the assistance policy: who may be told what.

This is the test file that matters most in the harness. The central promise of
botai — a graded assignment never receives a ready answer, no matter how the
request is phrased or how many times it is repeated — is enforced here or
nowhere. Everything else in the design is scaffolding around this rule.

What is deliberately *not* claimed: this module cannot stop a model from
writing the answer in prose on a host without an output gate. The design says so
(§4.3) and the scenario corpus is where that residual risk is measured. What is
tested here is that the decision procedure itself never permits it, that the
strictest applicable limit always wins, and that every decision names the rule
it came from so a human can check it.

Run:
    python3 tests/test_policy.py
"""

from __future__ import annotations

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

from botai_core import course, policy  # noqa: E402

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


class Accepted:
    """A workspace with the minimal course accepted, plus a graded assignment.

    The example course declares `practice-diff` as practice. A second fixture
    with a graded assignment is built here rather than shipped, because a
    graded assignment with its answer key must never live in the runtime
    package — the design is explicit that student-facing fixtures carry no
    keys, and a fixture is a poor place to make an exception.
    """

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.slug = "minimal-diff"
        self.course_dir = self.root / "courses" / self.slug
        self.course_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(EXAMPLE, self.course_dir)

        # Add a graded assignment, and a second practice one, so both ceilings
        # are exercised against the same accepted contract.
        contract_path = self.course_dir / "botai" / "course.json"
        document = course.load_json(contract_path)
        graded = dict(document["assignments"][0])
        graded["assignment_id"] = "graded-essay"
        graded["assessment"] = "graded"
        graded["path"] = "assignments/practice-diff.md"
        unknown = dict(document["assignments"][0])
        unknown["assignment_id"] = "imported-task"
        unknown["assessment"] = "unknown"
        unknown["path"] = "assignments/practice-diff.md"
        document["assignments"].extend([graded, unknown])
        contract_path.write_text(
            __import__("json").dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )

        course.accept(self.root, self.course_dir, slug=self.slug,
                      repository_root="courses/%s" % self.slug)
        self.course = course.load_accepted(self.root, self.slug)

    def cleanup(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
def test_graded_never_reaches_solution():
    """The core rule, stated once for every way it could be evaded."""
    ws = Accepted()
    try:
        for intent in ("solution",):
            decision = policy.request_help(
                ws.course, assignment_id="graded-essay", intent=intent,
                preference="solution-first", student_requested=True,
            )
            check("оцениваемое задание: SOLUTION отклонён",
                  decision["decision"] == "deny", decision["reason_code"])
            check("причина названа кодом",
                  decision["reason_code"] == "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT")
            check("предел помощи — EXAMPLE",
                  decision["assistance_ceiling"] == "EXAMPLE")
            check("правило указано", bool(decision.get("rule_ref")))
            check("предложена альтернатива", bool(decision["alternatives"]))
    finally:
        ws.cleanup()


def test_unknown_is_treated_as_strict():
    """An undeclared assignment must not be assumed to be practice."""
    ws = Accepted()
    try:
        declared_unknown = policy.request_help(
            ws.course, assignment_id="imported-task", intent="solution",
            preference="solution-first", student_requested=True,
        )
        check("объявленный unknown: SOLUTION отклонён",
              declared_unknown["decision"] == "deny", declared_unknown["reason_code"])
        check("оцениваемость записана как unknown",
              declared_unknown["assessment"] == "unknown")

        undeclared = policy.request_help(
            ws.course, assignment_id="never-heard-of-it", intent="solution",
            preference="solution-first", student_requested=True,
        )
        check("необъявленное задание: SOLUTION отклонён",
              undeclared["decision"] == "deny", undeclared["reason_code"])
        check("необъявленное задание — unknown",
              undeclared["assessment"] == "unknown")

        missing_id = policy.request_help(
            ws.course, assignment_id=None, intent="solution",
            preference="solution-first", student_requested=True,
        )
        check("отсутствующий assignment_id — unknown и отказ",
              missing_id["decision"] == "deny" and missing_id["assessment"] == "unknown")
    finally:
        ws.cleanup()


def test_no_accepted_course_is_strict():
    """Without an accepted contract there is no permissive default."""
    decision = policy.request_help(
        None, assignment_id="anything", intent="solution",
        preference="solution-first", student_requested=True,
    )
    check("без принятого курса SOLUTION отклонён",
          decision["decision"] == "deny", decision["reason_code"])
    check("причина — строгий режим по умолчанию",
          decision["assessment"] == "unknown")


def test_practice_permits_solution_when_asked():
    """A confirmed practice task may be fully worked through."""
    ws = Accepted()
    try:
        asked = policy.request_help(
            ws.course, assignment_id="practice-diff", intent="solution",
            preference="prefer-ask", student_requested=True,
        )
        check("тренировочная задача: SOLUTION допустим по просьбе",
              asked["decision"] == "allow", asked["reason_code"])
        check("предел — SOLUTION", asked["assistance_ceiling"] == "SOLUTION")

        unasked = policy.request_help(
            ws.course, assignment_id="practice-diff", intent="solution",
            preference="prefer-ask", student_requested=False, previous_level="HINT",
        )
        check("без просьбы ученика SOLUTION не выдаётся",
              unasked["decision"] == "deny", unasked["reason_code"])
    finally:
        ws.cleanup()


def test_hints_preference_is_not_lifted_by_a_request():
    """`hints` is a stated wish not to be handed answers."""
    ws = Accepted()
    try:
        decision = policy.request_help(
            ws.course, assignment_id="practice-diff", intent="solution",
            preference="hints", student_requested=True,
        )
        check("стиль hints не повышается до SOLUTION",
              decision["decision"] == "deny", decision["reason_code"])
        check("предел — HINT", decision["assistance_ceiling"] == "HINT")
        check("предложено сменить стиль, а не выдать разбор",
              any("стиль" in a or "уровень" in a for a in decision["alternatives"]),
              str(decision["alternatives"]))
    finally:
        ws.cleanup()


def test_strictest_limit_wins():
    """A permissive preference must never lift an assessment ceiling."""
    ws = Accepted()
    try:
        # graded + solution-first: the assessment ceiling still wins.
        decision = policy.request_help(
            ws.course, assignment_id="graded-essay", intent="solution",
            preference="solution-first", student_requested=True,
        )
        check("оцениваемость сильнее стиля помощи",
              decision["decision"] == "deny"
              and decision["assistance_ceiling"] == "EXAMPLE",
              "%s / %s" % (decision["decision"], decision.get("assistance_ceiling")))

        # graded + EXAMPLE is allowed: the ladder is not a blanket refusal.
        allowed = policy.request_help(
            ws.course, assignment_id="graded-essay", intent="example",
            preference="hints-then-solution", student_requested=True,
        )
        check("EXAMPLE для оцениваемого задания допустим",
              allowed["decision"] == "allow", allowed["reason_code"])

        hint = policy.request_help(
            ws.course, assignment_id="graded-essay", intent="hint",
            preference="hints", student_requested=True,
        )
        check("HINT для оцениваемого задания допустим",
              hint["decision"] == "allow", hint["reason_code"])
    finally:
        ws.cleanup()


def test_presenting_a_check_is_not_assistance():
    """An ordinary question about a task is not a hint.

    Recording it as one would corrupt the assistance history and could later
    deny a learner the `demonstrated` stage for help they never received.
    """
    ws = Accepted()
    try:
        decision = policy.request_help(
            ws.course, assignment_id="graded-essay", intent="check",
            preference="hints", student_requested=False,
        )
        check("предъявление проверки разрешено",
              decision["decision"] == "allow", decision["reason_code"])
        check("уровень помощи не назначается",
              decision["reason_code"] == "NOT_ASSISTANCE")
        check("предел помощи не задан",
              not decision.get("assistance_ceiling"))
    finally:
        ws.cleanup()


def test_escalation_is_one_step_at_a_time():
    """Cumulative hints must not assemble a graded solution piece by piece."""
    ws = Accepted()
    try:
        decision = policy.request_help(
            ws.course, assignment_id="practice-diff", intent="solution",
            preference="solution-first", student_requested=False,
            previous_level="HINT",
        )
        check("прыжок HINT → SOLUTION без просьбы отклонён",
              decision["decision"] == "deny"
              and decision["reason_code"] == "ESCALATION_TOO_FAST",
              decision["reason_code"])
    finally:
        ws.cleanup()


def test_unknown_intent_is_refused():
    ws = Accepted()
    try:
        decision = policy.request_help(
            ws.course, assignment_id="practice-diff", intent="just-tell-them",
            preference="prefer-ask",
        )
        check("неизвестное намерение отклонено",
              decision["decision"] == "deny"
              and decision["reason_code"] == "INTENT_UNKNOWN")
    finally:
        ws.cleanup()


def test_every_decision_matches_the_published_contract():
    """A decision that does not validate is a decision callers cannot trust."""
    ws = Accepted()
    try:
        cases = [
            ("graded-essay", "solution", "solution-first", True),
            ("graded-essay", "example", "prefer-ask", True),
            ("graded-essay", "check", "hints", False),
            ("practice-diff", "solution", "prefer-ask", True),
            ("practice-diff", "hint", "hints", True),
            ("unknown-task", "solution", "solution-first", True),
        ]
        for assignment_id, intent, preference, requested in cases:
            decision = policy.request_help(
                ws.course, assignment_id=assignment_id, intent=intent,
                preference=preference, student_requested=requested,
            )
            try:
                policy.validate_decision(decision)
            except Exception as e:  # noqa: BLE001
                check("решение соответствует схеме: %s/%s" % (assignment_id, intent),
                      False, str(e)[:200])
            else:
                check("решение соответствует схеме: %s/%s" % (assignment_id, intent), True)
    finally:
        ws.cleanup()


def test_denied_decision_always_carries_a_rule_and_an_alternative():
    """A refusal that names no rule is indistinguishable from an invented one."""
    ws = Accepted()
    try:
        for assignment_id, intent, preference in (
            ("graded-essay", "solution", "solution-first"),
            ("imported-task", "solution", "solution-first"),
            ("practice-diff", "solution", "hints"),
        ):
            decision = policy.request_help(
                ws.course, assignment_id=assignment_id, intent=intent,
                preference=preference, student_requested=True,
            )
            if decision["decision"] != "deny":
                continue
            check("отказ %s называет правило" % assignment_id,
                  bool(decision.get("rule_ref")), str(decision))
            check("отказ %s предлагает альтернативу" % assignment_id,
                  bool(decision["alternatives"]), str(decision))
            check("отказ %s объяснён по-русски" % assignment_id,
                  bool(decision["message_ru"]))
    finally:
        ws.cleanup()


def test_assessment_resolution_reports_its_source():
    ws = Accepted()
    try:
        declared = policy.resolve_assessment(ws.course, "graded-essay")
        check("оцениваемость взята из принятого контракта",
              declared["assessment"] == "graded" and declared["source"] == "accepted_contract",
              str(declared))
        check("источник правила приложен", declared["source_ref"] is not None)

        undeclared = policy.resolve_assessment(ws.course, "nope")
        check("необъявленное помечено как not_declared",
              undeclared["assessment"] == "unknown"
              and undeclared["source"] == "not_declared", str(undeclared))
    finally:
        ws.cleanup()


def test_material_scope_is_denied_without_a_course():
    decision = policy.read_material(None, "lessons/01.md")
    check("без принятого курса чтение запрещено",
          decision["decision"] == "deny"
          and decision["reason_code"] == "COURSE_NOT_ACCEPTED")


def test_search_scope_requires_an_accepted_course():
    ws = Accepted()
    try:
        scope = policy.search_scope(ws.course)
        check("область поиска ограничена разрешёнными корнями",
              scope["include_roots"] == ["lessons", "references", "assignments"],
              str(scope["include_roots"]))
        check("исключения перечислены отдельно",
              scope["exclude_roots"] == ["teacher"], str(scope["exclude_roots"]))
        check("область привязана к ревизии курса",
              scope["course_revision"]["id"] == ws.course.revision["id"])

        try:
            policy.search_scope(None)
        except ValueError:
            check("без принятого курса области поиска нет", True)
        else:
            check("без принятого курса области поиска нет", False, "область выдана")
    finally:
        ws.cleanup()


def test_operations_that_belong_to_a_human_are_refused():
    """The agent never commits, pushes, grades, or approves on the student's behalf."""
    for operation in ("git_commit", "git_push", "pull_request", "grade_set",
                      "role_change", "consent_set", "mastery_set", "approve"):
        decision = policy.request_operation(operation, actor="model_proposal",
                                            approval={"plan_hash": "x"})
        check("операция %s недоступна модели" % operation,
              decision["decision"] == "deny"
              and decision["reason_code"] == "HUMAN_ONLY_OPERATION",
              decision["reason_code"])


def test_effectful_operation_needs_a_human_approval():
    ws = Accepted()
    try:
        without = policy.request_operation("env_apply")
        check("без разрешения операция не выполняется",
              without["decision"] == "needs_confirmation"
              and without["reason_code"] == "APPROVAL_REQUIRED",
              without["reason_code"])

        stale = policy.request_operation(
            "env_apply", approval={"plan_hash": "a" * 64}, plan_hash="b" * 64,
        )
        check("разрешение другого плана не подходит",
              stale["decision"] == "deny"
              and stale["reason_code"] == "APPROVAL_STALE", stale["reason_code"])

        consumed = policy.request_operation(
            "env_apply", approval={"plan_hash": "a" * 64, "consumed_at": "2026-09-17T00:00:00Z"},
            plan_hash="a" * 64,
        )
        check("использованное разрешение не переиспользуется",
              consumed["decision"] == "deny"
              and consumed["reason_code"] == "APPROVAL_CONSUMED", consumed["reason_code"])

        granted = policy.request_operation(
            "env_apply", approval={"plan_hash": "a" * 64}, plan_hash="a" * 64,
        )
        check("разрешение для этого плана работает",
              granted["decision"] == "allow", granted["reason_code"])

        unknown = policy.request_operation("rm_rf_everything")
        check("неизвестная операция отклонена",
              unknown["decision"] == "deny"
              and unknown["reason_code"] == "OPERATION_UNKNOWN")
    finally:
        ws.cleanup()


def test_host_that_cannot_prove_approval_runs_nothing():
    """`never ask` is not a licence to proceed."""
    decision = policy.request_operation(
        "env_apply", approval={"plan_hash": "a" * 64}, plan_hash="a" * 64,
        host_can_approve=False,
    )
    check("хост без различения согласия не выполняет операцию",
          decision["decision"] == "deny"
          and decision["reason_code"] == "HOST_UNVERIFIED", decision["reason_code"])
    check("предложен ручной путь",
          bool(decision["alternatives"]), str(decision["alternatives"]))


def test_goal_change_reports_unverified_prerequisites():
    """Skipping a prerequisite is allowed, but not silently."""
    ws = Accepted()
    try:
        ready = policy.goal_change(ws.course, "explain-diff", demonstrated_ids=[])
        check("цель без предпосылок доступна",
              ready["decision"] == "allow", ready["reason_code"])

        gated = policy.goal_change(ws.course, "apply-staging", demonstrated_ids=[])
        check("цель с неподтверждённой предпосылкой требует подтверждения",
              gated["decision"] == "needs_confirmation", gated["reason_code"])
        check("названа неподтверждённая предпосылка",
              gated["reason_code"] == "PREREQUISITE_UNVERIFIED")

        confirmed = policy.goal_change(ws.course, "apply-staging",
                                       demonstrated_ids=["explain-diff"])
        check("подтверждённая предпосылка открывает цель",
              confirmed["decision"] == "allow", confirmed["reason_code"])

        missing = policy.goal_change(ws.course, "not-an-objective")
        check("несуществующая цель отклонена",
              missing["decision"] == "deny"
              and missing["reason_code"] == "OBJECTIVE_UNKNOWN")
    finally:
        ws.cleanup()


def main():
    tests = [
        test_graded_never_reaches_solution,
        test_unknown_is_treated_as_strict,
        test_no_accepted_course_is_strict,
        test_practice_permits_solution_when_asked,
        test_hints_preference_is_not_lifted_by_a_request,
        test_strictest_limit_wins,
        test_presenting_a_check_is_not_assistance,
        test_escalation_is_one_step_at_a_time,
        test_unknown_intent_is_refused,
        test_every_decision_matches_the_published_contract,
        test_denied_decision_always_carries_a_rule_and_an_alternative,
        test_assessment_resolution_reports_its_source,
        test_material_scope_is_denied_without_a_course,
        test_search_scope_requires_an_accepted_course,
        test_operations_that_belong_to_a_human_are_refused,
        test_effectful_operation_needs_a_human_approval,
        test_host_that_cannot_prove_approval_runs_nothing,
        test_goal_change_reports_unverified_prerequisites,
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
