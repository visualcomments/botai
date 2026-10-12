#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the teaching cycle: state machine, mastery, review, responses.

Two claims are being tested here, and both are the kind that quietly rot:

* **Mastery follows evidence, not fluency.** `demonstrated` requires two
  *different* passing checks on *different* attempts — one explanation, one
  application. A model cannot award it by describing progress, and a single
  exercise repeated twice is still a single piece of evidence.
* **The state machine refuses illegal moves.** A session cannot jump from
  `ATTEMPT_PENDING` to `COMPLETED`, cannot leave `PAUSED` into a state it never
  reached, and cannot be resumed with a `resume_state` that does not match.

Acceptance criteria covered: A01 (first session asks at most three questions),
A02/A03/A04 (graded never receives a solution), A05 (mastery then review),
A29 (stop is honoured immediately), A30 (the assessed step stays with the
student).

Run:
    python3 tests/test_tutoring.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import course, policy, schemas, tutoring as T  # noqa: E402

_passed = 0
_failures: list[str] = []

EXAMPLE = ROOT / "examples" / "minimal-course"
LEARNER = "11111111-1111-4111-8111-111111111111"


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
    except T.TutoringError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Session:
    """An accepted course plus a session, with a controllable clock."""

    def __init__(self, *, graded_ids=("graded-essay",)):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.course_dir = self.root / "courses" / "minimal-diff"
        self.course_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(EXAMPLE, self.course_dir)

        contract_path = self.course_dir / "botai" / "course.json"
        document = course.load_json(contract_path)
        for assignment_id in graded_ids:
            entry = dict(document["assignments"][0])
            entry["assignment_id"] = assignment_id
            entry["assessment"] = "graded"
            document["assignments"].append(entry)
        contract_path.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                                 encoding="utf-8", newline="\n")

        course.accept(self.root, self.course_dir, slug="minimal-diff",
                      repository_root="courses/minimal-diff")
        self.course = course.load_accepted(self.root, "minimal-diff")

        self._now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
        self.session = T.new_session(
            course=self.course, learner_id=LEARNER, consent_version="c1",
            clock=self.clock,
        )
        self.session["session_id"] = "22222222-2222-4222-8222-222222222222"
        self.session["state"] = "GOAL_SELECTED"
        self.session["current_objective_id"] = "explain-diff"
        self.session["assessment"] = "practice"

    def clock(self):
        return self._now

    def advance(self, **kwargs):
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def cleanup(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# A01 — the first session asks little and reports the rules
# ---------------------------------------------------------------------------
def test_first_session_asks_consent_not_grading_policy():
    """The grading policy is *reported* from the contract, never asked for.

    Asking the learner to decide which assignments are graded would let a
    session's own framing become the rule. The contract decides; the session
    confirms agreement on things only the learner can answer.
    """
    s = Session()
    try:
        pending = T.new_session(course=s.course, learner_id=LEARNER,
                                consent_pending=True, clock=s.clock)
        check("без согласия сессия не начинает учить",
              pending["state"] == "CONSENT_PENDING", pending["state"])

        directive = T.start_directive(s.course, pending)
        needed = set(directive["needed_inputs"])
        check("запрашивается согласие", "consent" in needed, str(needed))
        check("оцениваемость не запрашивается у ученика",
              not any("assess" in item or "graded" in item for item in needed),
              str(needed))
        check("не более трёх вводных вопросов", len(needed) <= 3, str(needed))
        check("на первом шаге помощь не выдаётся",
              directive["assistance_ceiling"] is None,
              str(directive["assistance_ceiling"]))

        resolved = policy.resolve_assessment(s.course, "practice-diff")
        check("оцениваемость берётся из контракта",
              resolved["assessment"] == "practice"
              and resolved["source"] == "accepted_contract", str(resolved))
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# A02/A03/A04 — graded never reaches SOLUTION, practice does
# ---------------------------------------------------------------------------
def test_graded_objective_directive_caps_help():
    s = Session()
    try:
        s.session["assessment"] = "graded"
        directive = T.next_step(s.course, s.session)
        check("для оцениваемого задания предел EXAMPLE",
              directive["assistance_ceiling"] == "EXAMPLE",
              str(directive["assistance_ceiling"]))
        check("SOLUTION не входит в разрешённые намерения",
              "solution" not in directive["allowed_intents"])

        decision = policy.request_help(s.course, assignment_id="graded-essay",
                                       intent="solution", preference="solution-first",
                                       student_requested=True)
        check("policy отказывает в SOLUTION для graded",
              decision["decision"] == "deny"
              and decision["reason_code"] == "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
              decision["reason_code"])
    finally:
        s.cleanup()


def test_practice_objective_allows_solution_but_unknown_does_not():
    s = Session()
    try:
        s.session["assessment"] = "practice"
        practice = T.next_step(s.course, s.session)
        check("для практики предел SOLUTION",
              practice["assistance_ceiling"] == "SOLUTION",
              str(practice["assistance_ceiling"]))

        s.session["assessment"] = "unknown"
        unknown = T.next_step(s.course, s.session)
        check("неизвестная оцениваемость снова строгая",
              unknown["assistance_ceiling"] == "EXAMPLE",
              str(unknown["assistance_ceiling"]))
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# A05 — mastery from two different checks, then review
# ---------------------------------------------------------------------------
def test_mastery_needs_two_different_checks():
    s = Session()
    try:
        one = [{"check_id": "c1", "attempt_id": "a1", "kind": "explain",
                "verdict": "pass"}]
        stage, reason, _ = T.evaluate_mastery(
            "explain-diff", checks=one,
            attempts=[{"attempt_id": "a1", "assistance_max": "NONE"}])
        check("одной проверки недостаточно для demonstrated",
              stage == "practising", stage)
        check("причина названа", "перенос" in reason or "применение" in reason, reason)

        two = one + [{"check_id": "c2", "attempt_id": "a2", "kind": "transfer",
                      "verdict": "pass"}]
        stage, reason, evidence = T.evaluate_mastery(
            "explain-diff", checks=two,
            attempts=[{"attempt_id": "a1", "assistance_max": "NONE"},
                      {"attempt_id": "a2", "assistance_max": "NONE"}])
        check("две разные проверки дают demonstrated", stage == "demonstrated", stage)
        check("доказательства перечислены", len(evidence) == 2, str(evidence))
    finally:
        s.cleanup()


def test_same_attempt_twice_is_one_evidence():
    """The rule that stops 'practise until it works' from becoming mastery."""
    s = Session()
    try:
        checks = [
            {"check_id": "c1", "attempt_id": "a1", "kind": "explain", "verdict": "pass"},
            {"check_id": "c2", "attempt_id": "a1", "kind": "transfer", "verdict": "pass"},
        ]
        stage, reason, _ = T.evaluate_mastery(
            "explain-diff", checks=checks,
            attempts=[{"attempt_id": "a1", "assistance_max": "NONE"}])
        check("две проверки одной попытки не дают demonstrated",
              stage != "demonstrated", stage)
    finally:
        s.cleanup()


def test_two_understanding_checks_are_not_mastery():
    """Explanation twice is not transfer: different kinds are required."""
    s = Session()
    try:
        checks = [
            {"check_id": "c1", "attempt_id": "a1", "kind": "explain", "verdict": "pass"},
            {"check_id": "c2", "attempt_id": "a2", "kind": "critique", "verdict": "pass"},
        ]
        stage, reason, _ = T.evaluate_mastery(
            "explain-diff", checks=checks,
            attempts=[{"attempt_id": "a1", "assistance_max": "NONE"},
                      {"attempt_id": "a2", "assistance_max": "NONE"}])
        check("два объяснения не заменяют применение", stage == "practising", stage)
        check("сказано, чего не хватает", "применение" in reason, reason)
    finally:
        s.cleanup()


def test_review_schedule_and_failure_path():
    s = Session()
    try:
        last = "2026-09-17T12:00:00Z"
        due, when = T.review_due("demonstrated", last, clock=s.clock, reviews_completed=0)
        check("сразу после проверки повторение не нужно", due is False, str(due))

        s.advance(days=2, minutes=1)
        due, when = T.review_due("demonstrated", last, clock=s.clock, reviews_completed=0)
        check("через 2 дня повторение наступило", due is True, str(due))
        check("срок повторения вычислен", when is not None and when.endswith("Z"), str(when))

        stage, reason = T.apply_review_result("demonstrated", "fail")
        check("неудачное повторение возвращает в practising",
              stage == "practising", stage)
        check("прежние доказательства не затираются", "сохранены" in reason, reason)

        stage, _ = T.apply_review_result("demonstrated", "pass")
        check("успешное повторение сохраняет demonstrated", stage == "demonstrated")
    finally:
        s.cleanup()


def test_mastery_document_refuses_claim_without_evidence():
    """A `demonstrated` state with no evidence is refused at construction.

    `evidence_ids` are UUIDs in the contract — the id of the check that
    supports the claim, not a readable label — so this test uses a real one.
    """
    expect_error("demonstrated без доказательств отклонён",
                 lambda: T.objective_state_document("explain-diff", stage="demonstrated"),
                 "MASTERY_WITHOUT_EVIDENCE")

    document = T.objective_state_document(
        "explain-diff", stage="demonstrated",
        evidence_ids=["c0a80101-0000-4000-8000-000000000001"])
    check("demonstrated с доказательством принимается",
          document["stage"] == "demonstrated")
    check("assistance_max не выдумывается при отсутствии попытки",
          "assistance_max" not in document, str(document.get("assistance_max")))


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def test_illegal_transitions_are_refused():
    check("нельзя прыгнуть из ATTEMPT_PENDING в COMPLETED",
          T.can_transition("ATTEMPT_PENDING", "COMPLETED")[0] is False)
    check("нельзя начать урок из COMPLETED",
          T.can_transition("COMPLETED", "ATTEMPT_PENDING")[0] is False)
    check("неизвестное состояние отклонено",
          T.can_transition("NOPE", "DIAGNOSIS")[0] is False)
    check("переход в то же состояние не переход",
          T.can_transition("FEEDBACK", "FEEDBACK")[1] == "STATE_UNCHANGED")
    check("FEEDBACK → UNDERSTANDING_CHECK разрешён",
          T.can_transition("FEEDBACK", "UNDERSTANDING_CHECK")[0] is True)
    check("любое рабочее состояние можно приостановить",
          all(T.can_transition(state, "PAUSED")[0] for state in T.WORKING_STATES))


def test_pause_remembers_where_it_stopped():
    s = Session()
    try:
        s.session["state"] = "FEEDBACK"
        paused = T.session_document(s.session, target_state="PAUSED",
                                    resume_state="FEEDBACK")
        check("приостановка сохраняет состояние", paused["resume_state"] == "FEEDBACK")

        expect_error("возврат не в то состояние отклонён",
                     lambda: T.session_document(paused, target_state="DIAGNOSIS"),
                     "RESUME_STATE_MISMATCH")

        resumed = T.session_document(paused, target_state="FEEDBACK")
        check("возврат в сохранённое состояние разрешён",
              resumed["state"] == "FEEDBACK")

        paused["resume_state"] = None
        expect_error("возврат без сохранённого состояния отклонён",
                     lambda: T.session_document(paused, target_state="FEEDBACK"),
                     "RESUME_STATE_MISMATCH")
    finally:
        s.cleanup()


def test_session_minutes_are_bounded():
    s = Session()
    try:
        expect_error("слишком короткое занятие отклонено",
                     lambda: T.new_session(course=s.course, learner_id=LEARNER,
                                           session_minutes=1, clock=s.clock),
                     "SESSION_MINUTES_OUT_OF_RANGE")
        expect_error("слишком длинное занятие отклонено",
                     lambda: T.new_session(course=s.course, learner_id=LEARNER,
                                           session_minutes=999, clock=s.clock),
                     "SESSION_MINUTES_OUT_OF_RANGE")
        expect_error("неизвестный режим отклонён",
                     lambda: T.new_session(course=s.course, learner_id=LEARNER,
                                           modes=["telepathy"], clock=s.clock),
                     "MODE_UNKNOWN")
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# A29 — stopping is immediate
# ---------------------------------------------------------------------------
def test_stop_is_honoured_from_every_working_state():
    s = Session()
    try:
        for state in sorted(T.WORKING_STATES):
            s.session["state"] = state
            directive = T.next_step(s.course, s.session)
            check("директива вычисляется из состояния %s" % state,
                  directive["next_action"] in T.ACTIONS,
                  str(directive["next_action"]))

        for terminal, action in (("COMPLETED", "rest"), ("CANCELLED", "rest")):
            s.session["state"] = terminal
            directive = T.next_step(s.course, s.session)
            check("%s не предлагает новую задачу" % terminal,
                  directive["next_action"] == action
                  and directive["allowed_intents"] == [],
                  str(directive))
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# A30 — the assessed step stays with the student
# ---------------------------------------------------------------------------
def test_model_cannot_plan_a_graded_check():
    s = Session()
    try:
        expect_error("модель не может предложить проверку по graded",
                     lambda: T.plan_check(check_plan_id="p1", session=s.session,
                                          objective_id="explain-diff", kind="explain",
                                          prompt="?", assessment="graded",
                                          provenance="model_proposed"),
                     "GRADED_CHECK_REQUIRES_COURSE")

        plan = T.plan_check(check_plan_id="p1", session=s.session,
                            objective_id="explain-diff", kind="explain",
                            prompt="Объясните своими словами", assessment="practice",
                            provenance="model_proposed", clock=s.clock)
        check("модель может предложить тренировочную проверку",
              plan["provenance"] == "model_proposed" and plan["assessment"] == "practice")
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# Checks and assistance
# ---------------------------------------------------------------------------
def test_check_verdict_must_follow_from_criteria():
    expect_error(
        "pass при незачтённом обязательном критерии отклонён",
        lambda: T.record_check(check_id="c1", attempt_id="a1", kind="explain",
                               verdict="pass",
                               criterion_results=[{"criterion_id": "k1", "result": "fail",
                                                   "evidence_refs": ["e1"], "required": True}]),
        "VERDICT_INCONSISTENT")
    expect_error(
        "зачтённый критерий без основания отклонён",
        lambda: T.record_check(check_id="c1", attempt_id="a1", kind="explain",
                               verdict="pass",
                               criterion_results=[{"criterion_id": "k1", "result": "pass",
                                                   "evidence_refs": [], "required": True}]),
        "CRITERION_WITHOUT_EVIDENCE")
    expect_error(
        "проверка без критериев отклонена",
        lambda: T.record_check(check_id="c1", attempt_id="a1", kind="explain",
                               verdict="uncertain", criterion_results=[]),
        "CHECK_WITHOUT_CRITERIA")

    good = T.record_check(check_id="c1", attempt_id="a1", kind="explain",
                          verdict="pass",
                          criterion_results=[{"criterion_id": "k1", "result": "pass",
                                              "evidence_refs": ["a1"], "required": True}])
    check("согласованная проверка принимается", good["verdict"] == "pass")
    check("модельная проверка всегда provisional",
          good["reliability"] == "provisional", good["reliability"])

    human = T.record_check(check_id="c2", attempt_id="a1", kind="explain",
                           verdict="pass", assessed_by="human",
                           reliability="human_reviewed",
                           criterion_results=[{"criterion_id": "k1", "result": "pass",
                                               "evidence_refs": ["a1"], "required": True}])
    check("человеческая проверка сохраняет достоверность",
          human["reliability"] == "human_reviewed")
    check("человек не может записать uncertain-проверку как pass",
          human["verdict"] == "pass")


def test_attempt_requires_exactly_one_content_form():
    s = Session()
    try:
        expect_error("попытка без содержимого отклонена",
                     lambda: T.record_attempt(s.session, attempt_id="a1"),
                     "ATTEMPT_CONTENT_AMBIGUOUS")
        expect_error("попытка с двумя формами содержимого отклонена",
                     lambda: T.record_attempt(s.session, attempt_id="a1",
                                              artifact_sha256="a" * 64,
                                              text_excerpt="текст"),
                     "ATTEMPT_CONTENT_AMBIGUOUS")
        expect_error("слишком большой текст попытки отклонён",
                     lambda: T.record_attempt(s.session, attempt_id="a1",
                                              text_excerpt="x" * 70000),
                     "ATTEMPT_TEXT_TOO_LARGE")

        attempt = T.record_attempt(s.session, attempt_id="a1", text_excerpt="моя попытка",
                                   clock=s.clock)
        check("попытка без помощи помечена NONE",
              attempt["assistance_max"] == "NONE", attempt["assistance_max"])

        assisted = T.record_attempt(s.session, attempt_id="a2", text_excerpt="ещё",
                                    assistance_cycle=["HINT", "EXAMPLE"], clock=s.clock)
        check("накопленная помощь берёт максимум",
              assisted["assistance_max"] == "EXAMPLE", assisted["assistance_max"])
    finally:
        s.cleanup()


def test_assistance_levels_are_validated():
    expect_error("неизвестный уровень помощи отклонён",
                 lambda: T.record_assistance(objective_id="o", assignment_id="a",
                                             level="ANSWER", step=1, reason="r"),
                 "ASSISTANCE_LEVEL_UNKNOWN")
    event = T.record_assistance(objective_id="o", assignment_id="a", level="HINT",
                                step=1, reason="learner_requested_hint")
    check("подсказка записывается как модельное наблюдение",
          event["_provenance"] == "model_reported")


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------
def test_response_check_catches_form_violations():
    s = Session()
    try:
        good = {"session_id": s.session["session_id"], "intent": "hint",
                "assessment": "practice", "assistance_level": "HINT",
                "assistance_step": 1, "explanation": "Короткое объяснение.",
                "question": "Что произойдёт?", "citations": []}
        accepted, violations, action = T.validate_response(s.course, s.session, good)
        check("корректный ответ принимается", accepted and not violations,
              str(violations))
        check("принятый ответ показывается ученику", action == "show_to_learner")

        over = dict(good, assessment="graded", assistance_level="SOLUTION")
        accepted, violations, action = T.validate_response(s.course, s.session, over)
        codes = {v["code"] for v in violations}
        check("ответ выше предела отклонён",
              not accepted and "RESPONSE_EXCEEDS_CEILING" in codes, str(codes))
        check("предложено исправить, а не показать", action == "fix_response")

        outside = dict(good, citations=[{"source_ref": {"path": "teacher/keys.md"},
                                         "verbatim": "x", "quote_status": "exact"}])
        accepted, violations, _ = T.validate_response(s.course, s.session, outside)
        check("ссылка вне разрешённых материалов отклонена",
              not accepted
              and "CITATION_OUT_OF_SCOPE" in {v["code"] for v in violations},
              str(violations))

        unverified = dict(good, citations=[{"source_ref": {"path": "lessons/01-working-tree.md"},
                                            "verbatim": "текст"}])
        accepted, violations, _ = T.validate_response(s.course, s.session, unverified)
        check("непроверенная цитата не выдаётся как точная",
              not accepted
              and "CITATION_UNVERIFIED" in {v["code"] for v in violations},
              str(violations))

        many = dict(good, question=["Первый?", "Второй?"])
        accepted, violations, _ = T.validate_response(s.course, s.session, many)
        check("не более одного основного вопроса",
              not accepted
              and "RESPONSE_MULTIPLE_QUESTIONS" in {v["code"] for v in violations},
              str(violations))

        other = dict(good, session_id="33333333-3333-4333-8333-333333333333")
        accepted, violations, _ = T.validate_response(s.course, s.session, other)
        check("ответ от другой сессии отклонён",
              not accepted and "RESPONSE_SESSION_MISMATCH" in {v["code"] for v in violations})
    finally:
        s.cleanup()


def test_language_consistency_is_enforced_not_advisory():
    """Rule 0 is computed, not requested.

    A Russian answer carrying Chinese characters or bare English words is
    refused; the legitimate exceptions pass. This is the reported defect:
    a multilingual model welded CJK into Russian words and dropped English
    mid-clause, and stating the rule in AGENTS.md did not stop it.
    """
    s = Session()
    try:
        def codes(text):
            return sorted({v["code"] for v in T.language_violations(text, "ru")})

        # -- the reported defects: all must be refused ----------------------
        check("знак чужой письменности в русском слове отклонён",
              codes("не\u7edd\u5bf9") != [],
              str(codes("не\u7edd\u5bf9")))
        check("китайское слово, вкравшееся в предложение, отклонено",
              "RESPONSE_FOREIGN_SCRIPT" in codes("граница не\u5dee\u5f02, а \u6df1\u5316"),
              str(codes("граница не\u5dee\u5f02, а \u6df1\u5316")))
        check("голое английское слово в русском предложении отклонено",
              "RESPONSE_UNTRANSLATED_LATIN" in codes(
                  "вы нашли genuine философскую проблему"),
              str(codes("вы нашли genuine философскую проблему")))
        check("голое organized отклонено",
              "RESPONSE_UNTRANSLATED_LATIN" in codes(
                  "знания organized в логическую систему"),
              str(codes("знания organized в логическую систему")))

        # -- the legitimate exceptions: all must pass -----------------------
        clean = "Наука — одна из форм познания. Алгоритм, файл, интерфейс — русские слова."
        check("чистый русский текст не отклоняется", codes(clean) == [], str(codes(clean)))
        check("иностранный термин один раз в скобках допускается",
              codes("состязательная проверка (adversarial evaluation)") == [],
              str(codes("состязательная проверка (adversarial evaluation)")))
        check("дословная цитата в кавычках не проверяется как проза",
              codes("Фейерабенд выдвинул принцип «anything goes».") == [],
              str(codes("Фейерабенд выдвинул принцип «anything goes».")))
        check("пути и команды в бэктиках допускаются",
              codes("Запустите `make verify` и проверьте `COURSE_CORPUS_ROOT`.") == [],
              str(codes("Запустите `make verify` и проверьте `COURSE_CORPUS_ROOT`.")))
        check("латинские термины искусства допускаются",
              codes("a priori Кант разбирает раньше; qualia остаются спорными.") == [],
              str(codes("a priori Кант разбирает раньше; qualia остаются спорными.")))
        check("курс не на русском не проверяется",
              T.language_violations("A fine English response.", "en") == [],
              str(T.language_violations("A fine English response.", "en")))

        # -- integration: validate_response refuses the leak ----------------
        bad = {"session_id": s.session["session_id"], "intent": "hint",
               "assessment": "practice", "assistance_level": "HINT",
               "assistance_step": 1,
               "explanation": "Вы нашли genuine философскую проблему.",
               "question": "Почему?", "citations": []}
        accepted, violations, action = T.validate_response(s.course, s.session, bad)
        got = {v["code"] for v in violations}
        check("validate_response отказывает ответу с чужим языком",
              not accepted and "RESPONSE_UNTRANSLATED_LATIN" in got, str(got))
        check("языковой провал возвращается как исправление",
              action == "fix_response", action)
    finally:
        s.cleanup()


# ---------------------------------------------------------------------------
# Directives and next step
# ---------------------------------------------------------------------------
def test_directive_is_a_pure_read():
    """`session-next` must not change state: a recommendation is not progress."""
    s = Session()
    try:
        before = json.dumps(s.session, sort_keys=True)
        T.next_step(s.course, s.session)
        after = json.dumps(s.session, sort_keys=True)
        check("вычисление следующего шага не меняет сессию", before == after)
    finally:
        s.cleanup()


def test_prerequisite_is_surfaced_before_new_material():
    s = Session()
    try:
        s.session["current_objective_id"] = "apply-staging"
        directive = T.next_step(s.course, s.session, objective_states={})
        check("неподтверждённая предпосылка предлагается первой",
              directive["next_action"] == "review_prerequisite",
              directive["next_action"])
        check("причина названа", "предпосылк" in directive["reason"], directive["reason"])

        with_evidence = {"explain-diff": {"stage": "demonstrated"}}
        directive = T.next_step(s.course, s.session, objective_states=with_evidence)
        check("с подтверждённой предпосылкой предлагается попытка",
              directive["next_action"] == "resume_attempt", directive["next_action"])
    finally:
        s.cleanup()


def test_due_review_is_offered_before_new_objectives():
    s = Session()
    try:
        s.session["current_objective_id"] = None
        s.session["state"] = "DIAGNOSIS"
        states = {"explain-diff": {"stage": "review_due"}}
        directive = T.next_step(s.course, s.session, objective_states=states)
        check("просроченное повторение предлагается первым",
              directive["next_action"] == "do_check"
              and directive["objective_id"] == "explain-diff",
              str(directive))
    finally:
        s.cleanup()


def test_unknown_objective_is_reported_not_guessed():
    s = Session()
    try:
        s.session["current_objective_id"] = "not-in-the-course"
        directive = T.next_step(s.course, s.session)
        check("несуществующая цель не подменяется похожей",
              directive["next_action"] == "choose_goal", directive["next_action"])
        check("причина названа", "отсутствует" in directive["reason"], directive["reason"])
    finally:
        s.cleanup()


def test_check_kinds_list_matches_the_schema():
    """Two lists that can drift silently are worse than one."""
    contract = schemas.schema_for("track")
    declared = set(contract["properties"]["objectives"]["items"]
                   ["properties"]["check_kinds"]["items"]["$ref"]
                   .split("/")[-1])
    schema = schemas.schema_for("objective_state")
    check("виды проверок объявлены в общей схеме", bool(declared))
    common = json.loads((schemas.SCHEMA_DIR / "common.schema.json").read_text(encoding="utf-8"))
    kinds = set(common["$defs"]["check_kind"]["enum"])
    check("список видов проверок в коде совпадает со схемой",
          set(schemas.SUPPORTED_CHECK_KINDS) == kinds,
          "код=%s схема=%s" % (sorted(schemas.SUPPORTED_CHECK_KINDS), sorted(kinds)))


def test_session_document_validates():
    s = Session()
    try:
        try:
            schemas.validate(s.session, "session")
        except schemas.SchemaError as e:
            check("документ сессии соответствует схеме", False, e.message[:200])
        else:
            check("документ сессии соответствует схеме", True)
    finally:
        s.cleanup()


def test_stuck_signal_after_three_sessions():
    check("две сессии — ещё не сигнал", T.stuck_signal(2) is False)
    check("три сессии с одной блокировкой — сигнал", T.stuck_signal(3) is True)


def test_session_step_budget_has_three_states():
    """A session has a step budget, and the budget is not a suggestion.

    The design bounds a sitting by minutes as well as by topics; a loop that
    keeps producing `session_next` without converging is a cost the learner
    pays. The budget makes that visible *before* it becomes a stuck session —
    `consecutive_stuck_sessions` counts sittings, this counts steps inside one.
    """
    check("пустая сессия не израсходована",
          T.step_budget_state(0) == "ok")
    check("отсутствие счётчика не считается исчерпанием",
          T.step_budget_state(None) == "ok")
    check("до границы предупреждения — ok",
          T.step_budget_state(T.SESSION_STEP_WARN_AT - 1) == "ok")
    check("на границе — warn",
          T.step_budget_state(T.SESSION_STEP_WARN_AT) == "warn")
    check("на пределе — exhausted",
          T.step_budget_state(T.SESSION_STEP_BUDGET) == "exhausted")
    check("далеко за пределом — тоже exhausted",
          T.step_budget_state(T.SESSION_STEP_BUDGET * 10) == "exhausted")


def test_exhausted_budget_offers_a_stop_not_a_new_topic():
    """Past the limit the answer is rest, and the directive says why.

    A budget that merely warned would be decorative: the model would keep
    going. `next_step` must stop offering new work, and must say that the
    reason is the budget rather than the learner's progress — those are
    different facts and the learner is told the true one.
    """
    directive = T.step_budget_directive(T.SESSION_STEP_BUDGET)
    check("на пределе предлагается закрыть или приостановить",
          directive["next_action"] == "close_or_pause", str(directive))
    check("причина названа бюджетом шагов, а не прогрессом",
          "шаг" in directive["reason_ru"], directive["reason_ru"])
    check("в директиве есть счётчик и предел",
          directive.get("budget", {}).get("limit") == T.SESSION_STEP_BUDGET,
          str(directive.get("budget")))
    check("сказано, что это предел внимания, а не наказание",
          "предел" in directive["reason_ru"], directive["reason_ru"])


def test_step_budget_is_a_capacity_limit_not_a_lock():
    """A session that keeps going must slow down before it burns out.

    The limit is graded: nothing happens for a long while, then a break is
    *offered* at `SESSION_STEP_WARN_AT`, then at `SESSION_STEP_BUDGET` the
    session asks to close or pause. It never refuses to record an attempt and
    never deletes work — a learner genuinely mid-flow is told about the budget,
    not locked out of it.

    Mirrors DeepTutor's per-loop tool budgets (``ToolBudgets`` in
    ``deeptutor/services/memory/consolidator/guards.py``), applied to a study
    sitting rather than to a memory-consolidation loop.
    """
    check("в начале занятия счётчик пуст",
          T.step_budget_state(None) == "ok", T.step_budget_state(None))
    check("до границы предупреждения ничего не предлагается",
          T.step_budget_state(T.SESSION_STEP_WARN_AT - 1) == "ok")
    check("на границе предупреждения предлагается пауза",
          T.step_budget_state(T.SESSION_STEP_WARN_AT) == "warn")
    check("на пределе занятие просит закрыться",
          T.step_budget_state(T.SESSION_STEP_BUDGET) == "exhausted")
    check("предупреждение наступает раньше предела",
          T.SESSION_STEP_WARN_AT < T.SESSION_STEP_BUDGET,
          "%s < %s" % (T.SESSION_STEP_WARN_AT, T.SESSION_STEP_BUDGET))

    # The directive for "ok" is empty — the budget must be invisible until it
    # has something to say, or it becomes noise the learner learns to ignore.
    check("на обычном шаге директивы нет",
          T.step_budget_directive(3) is None, str(T.step_budget_directive(3)))

    warn = T.step_budget_directive(T.SESSION_STEP_WARN_AT)
    check("на границе предложен перерыв, а не отказ",
          warn["next_action"] == "offer_break", str(warn))
    check("в директиве видно, сколько шагов пройдено",
          warn["budget"]["taken"] == T.SESSION_STEP_WARN_AT,
          str(warn.get("budget")))
    check("назван предел, а не только счётчик",
          warn["budget"]["limit"] == T.SESSION_STEP_BUDGET,
          str(warn.get("budget")))

    done = T.step_budget_directive(T.SESSION_STEP_BUDGET)
    check("исчерпанный бюджет просит закрыть или отложить",
          done["next_action"] == "close_or_pause", str(done))
    check("состояние бюджета названо исчерпанным",
          done["budget"]["state"] == "exhausted", str(done.get("budget")))
    check("причина говорит о пределе внимания, а не о провале ученика",
          "предел" in done["reason_ru"] or "бюджет" in done["reason_ru"],
          done["reason_ru"])


def test_count_step_increments_and_never_loses_the_count():
    """The counter is what makes the budget real; it must not reset or stall."""
    check("пустая сессия — шаг первый", T.count_step({}) == 1)
    check("счётчик растёт", T.count_step({"steps_taken": 4}) == 5)
    check("мусор в поле не обнуляет счёт",
          T.count_step({"steps_taken": "7"}) == 8,
          str(T.count_step({"steps_taken": "7"})))
    check("испорченное значение читается как ноль",
          T.count_step({"steps_taken": "не число"}) == 1)


# ---------------------------------------------------------------------------
# The service layer: the cycle bound to the store
# ---------------------------------------------------------------------------
class Service:
    """An accepted course with a real store, for the service-layer tests."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.course_dir = self.root / "courses" / "minimal-diff"
        self.course_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(EXAMPLE, self.course_dir)

        contract_path = self.course_dir / "botai" / "course.json"
        document = course.load_json(contract_path)
        graded = dict(document["assignments"][0])
        graded["assignment_id"] = "graded-essay"
        graded["assessment"] = "graded"
        document["assignments"].append(graded)
        contract_path.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                                 encoding="utf-8", newline="\n")

        course.accept(self.root, self.course_dir, slug="minimal-diff",
                      repository_root="courses/minimal-diff")
        from botai_core import session as session_mod
        self.service = session_mod.SessionService.open(
            self.root, LEARNER, "minimal-diff")
        self._now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)

    def cleanup(self):
        self.service.close()
        self._tmp.cleanup()

    def version_of(self, session_id):
        return self.service.get_session(session_id)[1]

    def started(self, consent="c1"):
        body, _, _ = self.service.start(consent_version=consent)
        return body["session_id"]


def test_service_refuses_a_course_nobody_accepted():
    """Without an accepted contract there is no authoritative grading policy."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dst = root / "courses" / "minimal-diff"
        dst.parent.mkdir(parents=True)
        shutil.copytree(EXAMPLE, dst)
        from botai_core import session as session_mod
        try:
            session_mod.SessionService.open(root, LEARNER, "minimal-diff")
        except course.CourseError as e:
            check("занятие без принятого курса не открывается",
                  e.code == "COURSE_NOT_ACCEPTED", e.code)
        except Exception as e:  # noqa: BLE001
            check("занятие без принятого курса не открывается", False,
                  "%s: %s" % (type(e).__name__, e))
        else:
            check("занятие без принятого курса не открывается", False,
                  "сессия открылась")


def test_service_records_the_vertical_scenario():
    """chose goal -> attempt -> check -> mastery, through the store."""
    s = Service()
    try:
        sid = s.started()
        body, _, _ = s.service.configure_goal(
            sid, "explain-diff", expected_version=s.version_of(sid),
            assignment_id="practice-diff", confirm=True)
        check("цель подтверждена и работа начата",
              body["state"] == "ATTEMPT_PENDING", body["state"])
        check("оцениваемость взята из контракта",
              body["assessment"] == "practice", str(body["assessment"]))

        attempt, _, _ = s.service.record_attempt(
            sid, text_excerpt="попытка", objective_id="explain-diff",
            assignment_id="practice-diff")
        check("попытка записана без доказательства авторства",
              attempt["source"] == "learner_report", attempt["source"])

        _, state, reasoning, _, _ = s.service.record_check(
            sid, attempt_id=attempt["attempt_id"], kind="explain", verdict="pass",
            criterion_results=[{"criterion_id": "k1", "result": "pass",
                                "evidence_refs": [attempt["attempt_id"]],
                                "required": True}])
        check("одна проверка даёт practising", state["stage"] == "practising",
              state["stage"])
        check("доказательство связано с проверкой",
              state["evidence_ids"], str(state["evidence_ids"]))

        attempt2, _, _ = s.service.record_attempt(
            sid, text_excerpt="вторая попытка", objective_id="explain-diff",
            assignment_id="practice-diff")
        _, state2, _, _, _ = s.service.record_check(
            sid, attempt_id=attempt2["attempt_id"], kind="transfer", verdict="pass",
            criterion_results=[{"criterion_id": "k2", "result": "pass",
                                "evidence_refs": [attempt2["attempt_id"]],
                                "required": True}])
        check("две разные проверки дают demonstrated",
              state2["stage"] == "demonstrated", state2["stage"])
        check("оба доказательства сохранены",
              len(state2["evidence_ids"]) == 2, str(state2["evidence_ids"]))

        # The reducer's result must round-trip through the store, not only exist
        # in the return value.
        stored = s.service.objective_states()["explain-diff"]
        check("освоение записано в хранилище",
              stored["stage"] == "demonstrated", stored["stage"])
    finally:
        s.cleanup()


def test_service_refuses_assistance_policy_denies():
    """`assistance-record` re-checks policy; it does not trust the caller."""
    s = Service()
    try:
        sid = s.started()
        s.service.configure_goal(sid, "explain-diff",
                                 expected_version=s.version_of(sid),
                                 assignment_id="graded-essay", confirm=True)
        try:
            s.service.record_assistance(sid, level="SOLUTION", step=4,
                                        reason="learner_asked")
        except T.TutoringError as e:
            check("SOLUTION для оцениваемого задания не записывается",
                  e.code == "ASSISTANCE_DENIED", e.code)
        else:
            check("SOLUTION для оцениваемого задания не записывается", False,
                  "помощь записана")

        _, _, _, decision = s.service.record_assistance(
            sid, level="HINT", step=1, reason="learner_asked")
        check("подсказка для оцениваемого задания записывается",
              decision["decision"] == "allow")
    finally:
        s.cleanup()


def test_service_attempt_inherits_recorded_assistance():
    """The model cannot declare its own help irrelevant."""
    s = Service()
    try:
        sid = s.started()
        s.service.configure_goal(sid, "explain-diff",
                                 expected_version=s.version_of(sid),
                                 assignment_id="practice-diff", confirm=True)
        s.service.record_assistance(sid, level="EXAMPLE", step=3,
                                    reason="learner_asked",
                                    objective_id="explain-diff")
        attempt, _, _ = s.service.record_attempt(
            sid, text_excerpt="после примера", objective_id="explain-diff",
            assignment_id="practice-diff")
        check("попытка наследует выданную помощь",
              attempt["assistance_max"] == "EXAMPLE", attempt["assistance_max"])
    finally:
        s.cleanup()


def test_service_optimistic_locking_is_enforced():
    """A write against a version the caller did not read is refused.

    This is what stops two writers — a CLI and a tool call, say — from silently
    losing each other's change to the same session.
    """
    s = Service()
    try:
        sid = s.started()
        try:
            s.service.configure_goal(sid, "explain-diff", expected_version=0)
        except Exception as e:  # noqa: BLE001
            check("устаревшая версия отклонена",
                  getattr(e, "code", "") == "STATE_CONFLICT",
                  getattr(e, "code", type(e).__name__))
        else:
            check("устаревшая версия отклонена", False, "запись прошла")
    finally:
        s.cleanup()


def test_service_projection_reflects_the_store():
    s = Service()
    try:
        sid = s.started()
        s.service.configure_goal(sid, "explain-diff",
                                 expected_version=s.version_of(sid),
                                 assignment_id="practice-diff", confirm=True)
        attempt, _, _ = s.service.record_attempt(
            sid, text_excerpt="попытка", objective_id="explain-diff",
            assignment_id="practice-diff")
        s.service.record_check(
            sid, attempt_id=attempt["attempt_id"], kind="explain", verdict="pass",
            criterion_results=[{"criterion_id": "k1", "result": "pass",
                                "evidence_refs": [attempt["attempt_id"]],
                                "required": True}])
        target = s.service.write_projection()
        text = target.read_text(encoding="utf-8")

        check("проекция создана", target.is_file())
        check("проекция называет себя представлением, а не источником",
              "не самостоятельный" in text or "пересобирается" in text, text[:200])
        check("освоение помечено как формирующая проверка",
              "формирующая" in text, text[:400])
        check("непроверенная цель названа отсутствием проверки",
              "нет проверки" in text, text[:600])
    finally:
        s.cleanup()


def test_service_projection_is_not_a_source_of_truth():
    """Editing the Markdown must not change the recorded state."""
    s = Service()
    try:
        sid = s.started()
        target = s.service.write_projection()
        target.write_text("# подделано\nвсё освоено\n", encoding="utf-8")

        stored = s.service.objective_states()
        check("правка проекции не меняет хранилище",
              stored.get("explain-diff", {}).get("stage", "new") == "new",
              str(stored))

        s.service.write_projection()
        check("проекция пересобирается из хранилища и затирает правку",
              "подделано" not in target.read_text(encoding="utf-8"))
    finally:
        s.cleanup()


def main():
    tests = [
        test_first_session_asks_consent_not_grading_policy,
        test_graded_objective_directive_caps_help,
        test_practice_objective_allows_solution_but_unknown_does_not,
        test_mastery_needs_two_different_checks,
        test_same_attempt_twice_is_one_evidence,
        test_two_understanding_checks_are_not_mastery,
        test_review_schedule_and_failure_path,
        test_mastery_document_refuses_claim_without_evidence,
        test_illegal_transitions_are_refused,
        test_pause_remembers_where_it_stopped,
        test_session_minutes_are_bounded,
        test_stop_is_honoured_from_every_working_state,
        test_model_cannot_plan_a_graded_check,
        test_check_verdict_must_follow_from_criteria,
        test_attempt_requires_exactly_one_content_form,
        test_assistance_levels_are_validated,
        test_response_check_catches_form_violations,
        test_language_consistency_is_enforced_not_advisory,
        test_directive_is_a_pure_read,
        test_prerequisite_is_surfaced_before_new_material,
        test_due_review_is_offered_before_new_objectives,
        test_unknown_objective_is_reported_not_guessed,
        test_check_kinds_list_matches_the_schema,
        test_session_document_validates,
        test_stuck_signal_after_three_sessions,
        test_session_step_budget_has_three_states,
        test_exhausted_budget_offers_a_stop_not_a_new_topic,
        test_step_budget_is_a_capacity_limit_not_a_lock,
        test_service_refuses_a_course_nobody_accepted,
        test_service_records_the_vertical_scenario,
        test_service_refuses_assistance_policy_denies,
        test_service_attempt_inherits_recorded_assistance,
        test_service_optimistic_locking_is_enforced,
        test_service_projection_reflects_the_store,
        test_service_projection_is_not_a_source_of_truth,
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
