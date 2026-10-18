#!/usr/bin/env python3
"""Deterministic, offline pedagogical contract scenarios for botai P0/P1."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import policy, tutoring, analytics, context  # noqa: E402


class Course:
    def assessment_of(self, assignment_id):
        return ("graded", "принятое правило курса") if assignment_id == "graded-task" else ("unknown", "задание не объявлено")

    def assessment_rule_ref(self, assignment_id):
        return "contract.assignment.%s" % assignment_id

    def assignment(self, assignment_id):
        return {"assignment_id": assignment_id} if assignment_id == "graded-task" else None


def scenario_graded_solution_is_denied():
    decision = policy.request_help(
        Course(), assignment_id="graded-task", intent="solution",
        preference="solution-first", student_requested=True,
    )
    assert not decision.allowed
    assert decision.code == "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT"


def scenario_unknown_is_strict():
    decision = policy.request_help(
        Course(), assignment_id="missing-task", intent="solution",
        preference="solution-first", student_requested=True,
    )
    assert not decision.allowed
    assert decision.code == "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT"


def scenario_help_is_bounded():
    decision = policy.request_help(
        Course(), assignment_id="graded-task", intent="hint",
        preference="hints", student_requested=True,
    )
    assert decision.allowed
    assert decision["assistance_ceiling"] in {"HINT", "EXAMPLE"}


def scenario_transfer_needs_application():
    """demonstrated needs one understanding and one application check."""
    passed_understanding = [{
        "check_id": "c1", "attempt_id": "a1", "kind": "explain",
        "verdict": "pass", "reliability": "provisional"
    }]
    passed_application = [{
        "check_id": "c2", "attempt_id": "a2", "kind": "transfer",
        "verdict": "pass", "reliability": "provisional"
    }]
    attempts = [{"attempt_id": "a1", "assistance_max": "HINT"},
                {"attempt_id": "a2", "assistance_max": "NONE"}]

    stage, why, evidence = tutoring.evaluate_mastery(
        "obj-1", checks=passed_understanding + passed_application, attempts=attempts)
    assert stage == "demonstrated"
    assert len(evidence) == 2
    assert set(evidence) == {"c1", "c2"}


def scenario_session_step_budget():
    assert tutoring.step_budget_state(17) == "ok"
    assert tutoring.step_budget_state(18) == "warn"
    assert tutoring.step_budget_state(24) == "exhausted"
    warning = tutoring.step_budget_directive(18)
    exhausted = tutoring.step_budget_directive(24)
    assert warning["next_action"] == "offer_break"
    assert exhausted["next_action"] == "close_or_pause"
    assert "пауз" in warning["reason_ru"]
    assert "исчерпан" in exhausted["reason_ru"]


def scenario_context_budget_is_deterministic():
    document = context.build_context_status(ROOT, budget=12000, include_resources=False)
    assert document["schema_version"] == 1
    assert document["used_characters"] == sum(
        item["characters"] for item in document["artifacts"]
    )
    assert all(item["disclosure"] != "on_demand" or not item["included"]
               for item in document["artifacts"])
    expanded = context.build_context_status(ROOT, budget=1, include_resources=True)
    assert not expanded["within_budget"]
    assert expanded["reasons"]
    assert "не загружать resources" in expanded["recommendation_ru"] or "сжать" in expanded["recommendation_ru"]


def scenario_blockers_are_evidence_backed():
    """A blocker report is derived from state/check evidence, never a grade."""
    class Track:
        track = {"objectives": [{"objective_id": "obj-1", "title": "Цель"}]}
        contract = {"title": "Тест"}
        course_id = "test"

    document = analytics.build_student_analytics(
        course=Track(), learner_id="student", sessions=[],
        objective_states={"obj-1": {"stage": "blocked", "evidence_ids": ["check-1"]}},
        attempts=[], checks=[],
    )
    assert document["counts"]["objectives"] == 1
    assert document["denominator"]["checks"] == 0
    assert "официальная оценка" not in document.get("basis_ru", "")


def scenario_cli_transfer_flow():
    """Run the public CLI through two attempts and verify demonstrated state."""
    workspace = Path(tempfile.mkdtemp(prefix="botai-eval-"))
    try:
        courses = workspace / "courses"
        courses.mkdir()
        shutil.copytree(ROOT / "examples" / "minimal-course", courses / "minimal")

        learner = "eval-student"

        def cli(*args, expect=0):
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "cli.py"), *args,
                 "--root", str(workspace)],
                text=True, capture_output=True,
            )
            if result.returncode != expect:
                print(f"CLI exit {result.returncode} for {' '.join(args)}")
                print(result.stdout)
                print(result.stderr)
            assert result.returncode == expect, result.stdout + result.stderr
            return result.stdout

        cli("course-accept", "--course", "minimal")
        cli("consent-set", "--course", "minimal-diff", "--learner", learner,
            "--purpose", "learning_storage")
        output = cli("session-start", "--course", "minimal-diff", "--learner", learner)
        session_id = re.search(r"session_id\s+:\s+(\S+)", output).group(1)
        cli("session-goal", "--course", "minimal-diff", "--learner", learner,
            "--session", session_id, "--objective", "explain-diff",
            "--assignment", "practice-diff", "--confirm")

        attempts = []
        for text, kind in (("объяснение", "explain"), ("новый перенос", "transfer")):
            attempt_file = workspace / ("attempt-%s.txt" % kind)
            attempt_file.write_text(text, encoding="utf-8")
            output = cli("session-attempt", "--course", "minimal-diff", "--learner", learner,
                         "--session", session_id, "--objective", "explain-diff",
                         "--assignment", "practice-diff", "--attempt-file", str(attempt_file))
            attempt_id = re.search(r"attempt_id\s+:\s+([^ ]+)", output).group(1)
            attempts.append(attempt_id)
            cli("session-check", "--course", "minimal-diff", "--learner", learner,
                "--session", session_id, "--attempt", attempt_id,
                "--check-kind", kind, "--verdict", "pass",
                "--criterion", "%s=pass:%s" % (kind, attempt_id))

        state = json.loads(cli("state-view", "--course", "minimal-diff",
                                "--learner", learner, "--json"))
        objective = next(item for item in state["objectives"]
                         if item["objective_id"] == "explain-diff")
        assert objective["stage"] == "demonstrated", state
        assert len(objective["evidence_ids"]) == 2, state
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def scenario_transfer_same_attempt_is_one():
    """Two checks on same attempt count as one piece of evidence."""
    checks = [
        {"check_id": "c1", "attempt_id": "a1", "kind": "explain", "verdict": "pass"},
        {"check_id": "c2", "attempt_id": "a1", "kind": "transfer", "verdict": "pass"},
    ]
    attempts = [{"attempt_id": "a1", "assistance_max": "NONE"}]

    stage, why, evidence = tutoring.evaluate_mastery(
        "obj-1", checks=checks, attempts=attempts)
    assert stage == "practising"
    assert "вторая, другая успешная проверка" in why or "не хватает" in why


SCENARIOS = {
    "graded-solution-denied": scenario_graded_solution_is_denied,
    "unknown-is-strict": scenario_unknown_is_strict,
    "help-is-bounded": scenario_help_is_bounded,
    "transfer-needs-application": scenario_transfer_needs_application,
    "transfer-same-attempt-is-one": scenario_transfer_same_attempt_is_one,
    "cli-transfer-flow": scenario_cli_transfer_flow,
    "blockers-are-evidence-backed": scenario_blockers_are_evidence_backed,
    "context-budget-is-deterministic": scenario_context_budget_is_deterministic,
    "session-step-budget": scenario_session_step_budget,
}


def main():
    selected = sys.argv[1:] or list(SCENARIOS)
    failures = []
    for name in selected:
        if name not in SCENARIOS:
            print("unknown scenario: %s" % name, file=sys.stderr)
            return 2
        try:
            SCENARIOS[name]()
        except Exception as exc:  # keep all scenario results visible
            failures.append((name, exc))
            print("FAIL %s: %s" % (name, exc))
        else:
            print("PASS %s" % name)
    print("summary: %d passed, %d failed" % (len(selected) - len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
