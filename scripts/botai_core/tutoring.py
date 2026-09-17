# -*- coding: utf-8 -*-
"""The teaching cycle: what state a session is in, and what may happen next.

This is the module that decides *what to teach next* and *what counts as
knowing it*. It contains no language model and no phrasing: it computes a
`TeachingDirective` (the allowed action, the assistance ceiling, the reason),
and the model turns that into words. Keeping the two apart is the point — a
model that chose the next step could also choose to skip a prerequisite, and a
model that awarded mastery could award it for a fluent answer.

Three rules are structural here, not advisory:

* **A state change names its evidence.** `demonstrated` requires two
  *different* passing checks on distinct attempts, one of them a transfer or
  application. Repeating the same exercise twice is one piece of evidence.
* **Assistance is remembered across the cycle.** An attempt's
  `assistance_max` is the highest level reached in its check cycle, so a
  sequence of hints cannot be laundered into "unaided" by starting a new
  session — the history follows the task instance, not the session id.
* **Review is scheduled, not guessed.** 2 → 7 → 21 days, computed by the core
  with an injectable clock, because a test that cannot control time cannot test
  a schedule.

Nothing here writes to storage directly. `apply_*` functions return a validated
transition that the caller commits through `Store.apply`, which is what makes
the whole cycle replayable and auditable after a crash.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import schemas

SCHEMA_VERSION = 2

# Session states. The automaton is deliberately small: every state has a reason
# to exist, and an unlisted transition is an error rather than a fallback.
STATES = (
    "CREATED", "CONSENT_PENDING", "DIAGNOSIS", "GOAL_SELECTED",
    "ATTEMPT_PENDING", "FEEDBACK", "UNDERSTANDING_CHECK", "REMEDIATION",
    "REFLECTION", "COMPLETED", "PAUSED", "BLOCKED", "CANCELLED",
)

# Transitions the core permits. Anything else is refused with STATE_TRANSITION.
# `PAUSED`/`BLOCKED` are reachable from every working state and return to the
# state they saved, which is why they are listed separately below.
TRANSITIONS = {
    ("CREATED", "CONSENT_PENDING"),
    ("CREATED", "DIAGNOSIS"),
    ("CREATED", "CANCELLED"),
    ("CONSENT_PENDING", "DIAGNOSIS"),
    ("CONSENT_PENDING", "CANCELLED"),
    ("DIAGNOSIS", "GOAL_SELECTED"),
    ("DIAGNOSIS", "PAUSED"),
    ("GOAL_SELECTED", "ATTEMPT_PENDING"),
    ("GOAL_SELECTED", "REFLECTION"),
    ("GOAL_SELECTED", "PAUSED"),
    ("ATTEMPT_PENDING", "FEEDBACK"),
    ("ATTEMPT_PENDING", "DIAGNOSIS"),
    ("ATTEMPT_PENDING", "PAUSED"),
    ("FEEDBACK", "ATTEMPT_PENDING"),
    ("FEEDBACK", "UNDERSTANDING_CHECK"),
    ("FEEDBACK", "REMEDIATION"),
    ("FEEDBACK", "PAUSED"),
    ("FEEDBACK", "REFLECTION"),
    ("UNDERSTANDING_CHECK", "REFLECTION"),
    ("UNDERSTANDING_CHECK", "REMEDIATION"),
    ("UNDERSTANDING_CHECK", "ATTEMPT_PENDING"),
    ("UNDERSTANDING_CHECK", "PAUSED"),
    ("REMEDIATION", "ATTEMPT_PENDING"),
    ("REMEDIATION", "PAUSED"),
    ("REFLECTION", "COMPLETED"),
    ("REFLECTION", "GOAL_SELECTED"),
    ("REFLECTION", "PAUSED"),
}

# States from which the learner can still be studying. `BLOCKED` and `PAUSED`
# remember where they came from; the others are terminal.
WORKING_STATES = {
    "CONSENT_PENDING", "DIAGNOSIS", "GOAL_SELECTED", "ATTEMPT_PENDING",
    "FEEDBACK", "UNDERSTANDING_CHECK", "REMEDIATION", "REFLECTION",
}
TERMINAL_STATES = {"COMPLETED", "CANCELLED", "PAUSED", "BLOCKED"}

# Check kinds that prove "explained in your own words" versus "used it on
# something new". Mastery needs one of each: understanding and transfer are
# different claims, and a course that only asks for recall has not shown either.
UNDERSTANDING_KINDS = {"explain", "critique"}
APPLICATION_KINDS = {"apply", "transfer", "predict"}

# Repetition intervals in days. A plain fixed ladder on purpose: an "optimal
# adaptive algorithm" would be a claim this project cannot evidence.
REVIEW_INTERVALS = (2, 7, 21)

# Assistance ladder ceiling per assessment, mirroring policy.py. Duplicated
# here as a guard, not as the authority: `policy.request_help` decides, and this
# table exists so tutoring cannot offer a level policy would refuse.
ASSESSMENT_CEILING = {"graded": "EXAMPLE", "unknown": "EXAMPLE", "practice": "SOLUTION"}

LEVEL_STEP = {"HINT": 1, "EXAMPLE": 3, "SOLUTION": 4}

# Actions a directive may name, from the design's `next_step`.
ACTIONS = (
    "resume_attempt", "review_prerequisite", "do_check", "read_source",
    "prepare_environment", "ask_teacher", "choose_goal", "rest",
)

MODES = ("co-learning", "tutoring", "supplement", "contributor")

MAX_SESSION_MINUTES = 120
MIN_SESSION_MINUTES = 5

# After this many failed cycles on one objective, the right answer is not
# another hint: it is a different explanation, a prerequisite, or a teacher.
STUCK_CYCLES_BEFORE_TEACHER = 3


class TutoringError(RuntimeError):
    """A refused transition or command, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso(clock=None):
    moment = clock() if clock else datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text):
    if not text:
        return None
    value = str(text).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _step(level):
    return LEVEL_STEP.get(level or "", 0)


def max_level(*levels):
    """The most revealing level among those given (None when all are empty)."""
    best = None
    for level in levels:
        if level and _step(level) > _step(best):
            best = level
    return best


# --------------------------------------------------------------------------
# Transition legality
# --------------------------------------------------------------------------

def can_transition(current, target, *, resume_state=None):
    """Whether `current -> target` is permitted, with the reason if not."""
    if current not in STATES:
        return False, "STATE_UNKNOWN"
    if target not in STATES:
        return False, "TARGET_UNKNOWN"
    if current == target:
        # Re-entering the same state is not a transition; a caller that wants to
        # record progress within a state should write an event, not move.
        return False, "STATE_UNCHANGED"
    if (current, target) in TRANSITIONS:
        return True, "ok"

    # Leaving a paused or blocked session returns it to where it stopped. The
    # saved state is the only legal destination: resuming into a state the
    # session never reached would skip the work that state represents. A
    # missing saved state is reported as a mismatch rather than a separate
    # code — from the caller's side it is the same defect (the target is not
    # where this session stopped), and one code per defect keeps branching
    # honest.
    if current in ("PAUSED", "BLOCKED"):
        if not resume_state or target != resume_state:
            return False, "RESUME_STATE_MISMATCH"
        return True, "resumed"
    if target in ("PAUSED", "BLOCKED"):
        if current in WORKING_STATES:
            return True, "interrupted"
        return False, "STATE_NOT_INTERRUPTIBLE"
    if target == "CANCELLED":
        if current not in TERMINAL_STATES:
            return True, "cancelled"
        return False, "ALREADY_TERMINAL"
    return False, "STATE_TRANSITION"


def require_transition(current, target, *, resume_state=None):
    ok, reason = can_transition(current, target, resume_state=resume_state)
    if not ok:
        raise TutoringError(
            reason,
            "переход %s → %s недопустим (%s)" % (current, target, reason),
        )
    return True


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------

def new_session(*, course, learner_id, objectives=(), modes=("tutoring",),
                session_minutes=30, consent_version=None, consent_pending=False,
                clock=None):
    """A fresh session document. State depends on what is still missing.

    Consent is read, never assumed: a caller that has no recorded consent gets
    `CONSENT_PENDING`, and the directive will ask for it rather than starting to
    teach. There is no default that treats a missing consent as granted.
    """
    if not modes:
        raise TutoringError("MODES_EMPTY", "режим занятия не задан")
    for mode in modes:
        if mode not in MODES:
            raise TutoringError("MODE_UNKNOWN", "неизвестный режим: %r" % mode)

    minutes = int(session_minutes or 30)
    if not (MIN_SESSION_MINUTES <= minutes <= MAX_SESSION_MINUTES):
        raise TutoringError(
            "SESSION_MINUTES_OUT_OF_RANGE",
            "длительность занятия %d мин вне диапазона %d–%d"
            % (minutes, MIN_SESSION_MINUTES, MAX_SESSION_MINUTES),
        )

    state = "CONSENT_PENDING" if consent_pending else "DIAGNOSIS"
    return {
        "schema_version": SCHEMA_VERSION,
        "course_id": course.course_id,
        "contract_hash": course.contract_hash,
        "course_revision": dict(course.revision),
        "learner_id": learner_id,
        "modes": list(modes),
        "state": state,
        "session_minutes": minutes,
        "objective_ids": list(objectives),
        "current_objective_id": None,
        "current_assignment_id": None,
        "assessment": None,
        "consent_version": consent_version,
        "resume_state": None,
        "blocked_reason": None,
        "assistance_max": None,
        "independence_claim": "unknown",
        "started_at": now_iso(clock),
        "ended_at": None,
        "next_step": None,
        "version": 0,
    }


def session_document(session, *, target_state=None, **changes):
    """A copy with `changes` applied, transition-checked when a state moves."""
    document = dict(session)
    if target_state and target_state != session["state"]:
        require_transition(session["state"], target_state,
                           resume_state=session.get("resume_state"))
        document["state"] = target_state
    document.update(changes)
    return document


def start_directive(course, session):
    """What to ask for first, given a session that has just been created.

    Consent first, then (at most) the three introductory questions from the
    design: goal/time, preparation or diagnosis, help style. Everything the
    course already answers — notably the grading policy — is *reported*, not
    asked.
    """
    if session["state"] == "CONSENT_PENDING":
        return TeachingDirective(
            session_id=session.get("session_id"),
            session_version=session.get("version"),
            state="CONSENT_PENDING",
            objective_id=None,
            assignment_id=None,
            allowed_intents=("stop",),
            assistance_ceiling=None,
            needed_inputs=("consent", "baseline", "preferences"),
            next_action="choose_goal",
            reason="Перед началом нужно согласие на обработку данных и выбор "
                   "режима помощи; без него занятие не начинается.",
            evidence_refs=(),
        )

    return TeachingDirective(
        session_id=session.get("session_id"),
        session_version=session.get("version"),
        state=session["state"],
        objective_id=None,
        assignment_id=None,
        allowed_intents=("diagnose", "explain", "stop"),
        assistance_ceiling="EXAMPLE",
        needed_inputs=("goal", "baseline"),
        next_action="choose_goal",
        reason="Нужно выбрать цель занятия и, если уровень неизвестен, "
               "сначала провести короткую диагностику.",
        evidence_refs=(),
    )


# --------------------------------------------------------------------------
# TeachingDirective: the deterministic answer to "what now?"
# --------------------------------------------------------------------------

class TeachingDirective(dict):
    """The core's answer about the next step. Rendered as JSON for tools.

    A dict subclass for the same reason `PolicyDecision` is one: it goes
    straight into a tool result and into an event payload, and converting
    between a model object and the wire form at each boundary is how the two
    drift apart.
    """

    @property
    def action(self):
        return self["next_action"]


def _directive(session, *, state=None, objective_id=None, assignment_id=None,
               allowed_intents=(), assistance_ceiling=None, needed_inputs=(),
               next_action="rest", reason="", evidence_refs=()):
    return TeachingDirective({
        "schema_version": SCHEMA_VERSION,
        "session_id": session.get("session_id"),
        "session_version": session.get("version"),
        "state": state or session["state"],
        "objective_id": objective_id,
        "assignment_id": assignment_id,
        "allowed_intents": list(allowed_intents),
        "assistance_ceiling": assistance_ceiling,
        "needed_inputs": list(needed_inputs),
        "next_action": next_action,
        "reason": reason,
        "evidence_refs": list(evidence_refs),
    })


def next_step(course, session, *, objective_states=None, attempts=None,
              checks=None, clock=None):
    """Compute what may happen next. **Pure**: it reads and never writes.

    The eight ordered rules of design §7.3, in order. The ordering matters:
    a stop request beats everything, a missing prerequisite beats a new topic,
    and an unfinished objective beats a new one. "What would be more
    interesting" is not a rule.
    """
    objective_states = objective_states or {}
    attempts = attempts or []
    checks = checks or []
    state = session["state"]

    # 1. The learner asked to stop. Nothing is more important than that.
    if state == "COMPLETED":
        return _directive(session, allowed_intents=(), next_action="rest",
                          reason="Занятие завершено.")
    if state == "CANCELLED":
        return _directive(session, allowed_intents=(), next_action="rest",
                          reason="Занятие отменено учеником.")
    if state == "PAUSED":
        return _directive(session, allowed_intents=("stop", "reflect"),
                          next_action="resume_attempt",
                          reason="Занятие приостановлено; можно продолжить с "
                                 "сохранённого шага.")
    if state == "BLOCKED":
        return _directive(session, allowed_intents=("stop",),
                          next_action="ask_teacher",
                          reason="Работа заблокирована: %s"
                                 % (session.get("blocked_reason") or "причина не указана"))

    # 2. A missing obligatory agreement — ask only the missing question.
    missing = []
    if session.get("consent_version") is None:
        missing.append("consent")
    if state == "CONSENT_PENDING":
        return _directive(session, allowed_intents=("stop",),
                          assistance_ceiling=None,
                          needed_inputs=tuple(missing or ["preferences"]),
                          next_action="choose_goal",
                          reason="Не хватает обязательного согласования "
                                 "перед началом занятия.")

    objective_id = session.get("current_objective_id")
    assignment_id = session.get("current_assignment_id")

    # 3. The assignment must be declared by the accepted contract. An
    #    undeclared one is `unknown`, which caps help at EXAMPLE.
    assessment = session.get("assessment") or "unknown"
    if assignment_id:
        resolved, _why = course.assessment_of(assignment_id)
        assessment = resolved
    ceiling = ASSESSMENT_CEILING.get(assessment, "EXAMPLE")

    # 4. An objective whose prerequisites are not demonstrated is reachable —
    #    repeating a prerequisite is always allowed — but it is not "next".
    if objective_id and state in ("GOAL_SELECTED", "ATTEMPT_PENDING", "FEEDBACK",
                                  "UNDERSTANDING_CHECK", "REMEDIATION"):
        objective = course.objective(objective_id)
        if objective is None:
            return _directive(session, allowed_intents=("stop",),
                              next_action="choose_goal",
                              reason="Цель %r отсутствует в принятой программе курса."
                                     % objective_id)
        demonstrated = {oid for oid, st in objective_states.items()
                        if st.get("stage") == "demonstrated"}
        unmet = course.unsatisfied_prerequisites(objective_id, demonstrated)
        if unmet and state == "GOAL_SELECTED":
            return _directive(session, objective_id=objective_id,
                              assignment_id=assignment_id,
                              allowed_intents=("hint", "explain", "diagnose", "stop"),
                              assistance_ceiling=ceiling,
                              next_action="review_prerequisite",
                              reason="У цели %s не подтверждены предпосылки: %s. "
                                     "Предлагается сначала повторить их."
                                     % (objective_id, ", ".join(unmet)))

    objective_state = objective_states.get(objective_id or "", {})
    stage = objective_state.get("stage", "new")

    # A due repetition outranks starting or continuing anything else, and it has
    # to be checked *before* the per-state dispatch: a learner who arrives in
    # DIAGNOSIS with a demonstrated objective waiting for review would otherwise
    # never be shown it, and the schedule would silently never run.
    due = sorted(oid for oid, st in objective_states.items()
                 if st.get("stage") == "review_due")
    if due and state in ("DIAGNOSIS", "GOAL_SELECTED", "ATTEMPT_PENDING"):
        return _directive(session, objective_id=due[0],
                          allowed_intents=("check", "stop"),
                          assistance_ceiling=None,
                          next_action="do_check",
                          reason="Пора повторить цель %s: срок повторения наступил. "
                                 "Это не штраф и не потеря результата." % due[0])

    if state == "DIAGNOSIS":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("diagnose", "explain", "stop"),
                          assistance_ceiling="EXAMPLE",
                          needed_inputs=("baseline",) if stage == "new" else (),
                          next_action="do_check" if objective_id else "choose_goal",
                          reason="Диагностика: проверяем предпосылки перед выбором "
                                 "основной цели. Освоение здесь не отмечается.")

    if state == "GOAL_SELECTED":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("hint", "explain", "example", "stop"),
                          assistance_ceiling=ceiling,
                          next_action="resume_attempt",
                          reason="Цель выбрана; следующий шаг — попытка ученика. "
                                 "Готовый ответ не выдаётся.")

    if state == "ATTEMPT_PENDING":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("hint", "explain", "example", "stop"),
                          assistance_ceiling=ceiling,
                          next_action="resume_attempt",
                          reason="Ожидается попытка ученика по текущей цели.")

    if state == "FEEDBACK":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("feedback", "hint", "explain", "example", "stop"),
                          assistance_ceiling=ceiling,
                          next_action="do_check",
                          reason="Есть попытка: сначала обратная связь по ней, "
                                 "затем проверка понимания.")

    if state == "UNDERSTANDING_CHECK":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("check", "feedback", "stop"),
                          # Presenting a check is not assistance: no level.
                          assistance_ceiling=None,
                          next_action="do_check",
                          reason="Идёт проверка понимания. Для перехода к "
                                 "освоению нужны две разные успешные проверки: "
                                 "объяснение своими словами и применение.")

    if state == "REMEDIATION":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("hint", "explain", "example", "stop"),
                          assistance_ceiling=ceiling,
                          next_action="resume_attempt",
                          reason="Возвращаемся к материалу: разбор принципа или "
                                 "другая аналогия, затем новая попытка.")

    if state == "REFLECTION":
        return _directive(session, objective_id=objective_id,
                          assignment_id=assignment_id,
                          allowed_intents=("reflect", "stop"),
                          assistance_ceiling=None,
                          next_action="rest",
                          reason="Кратко подведём итог: что получилось, что "
                                 "осталось, каков следующий шаг.")

    # 5-8. No active objective: choose one deterministically.
    demonstrated = {oid for oid, st in objective_states.items()
                    if st.get("stage") == "demonstrated"}
    next_objective = course.next_required_objective(demonstrated)
    if next_objective:
        return _directive(session, objective_id=next_objective,
                          allowed_intents=("explain", "diagnose", "stop"),
                          assistance_ceiling="EXAMPLE",
                          next_action="choose_goal",
                          reason="Следующая обязательная цель по программе — %s."
                                 % next_objective)

    return _directive(session, allowed_intents=("stop",), next_action="rest",
                      reason="Все обязательные цели программы достигнуты.")


# --------------------------------------------------------------------------
# Attempts, assistance, checks
# --------------------------------------------------------------------------

def record_attempt(session, *, attempt_id, objective_id=None, assignment_id=None,
                   source="learner_report", artifact_sha256=None, text_excerpt=None,
                   task_instance_id=None, assistance_cycle=None, clock=None):
    """Validate one attempt and return the event payload it earns.

    Exactly one of `artifact_sha256` and `text_excerpt` is set: an attempt is
    either a stored file or the text that was submitted, and leaving both empty
    would let "the learner tried" become an unfalsifiable claim.
    """
    if bool(artifact_sha256) == bool(text_excerpt):
        raise TutoringError(
            "ATTEMPT_CONTENT_AMBIGUOUS",
            "попытка должна содержать ровно одно из: artifact_sha256 или "
            "text_excerpt (сейчас задано %s)"
            % ("оба" if artifact_sha256 and text_excerpt else "ни одного"),
        )
    if source not in ("learner_report", "host_observed", "file_snapshot"):
        raise TutoringError("ATTEMPT_SOURCE_UNKNOWN",
                            "неизвестный источник попытки: %r" % source)
    if text_excerpt and len(text_excerpt) > 65536:
        raise TutoringError(
            "ATTEMPT_TEXT_TOO_LARGE",
            "текст попытки больше 64 КиБ: сохраняйте файл как артефакт, "
            "а не обрезайте доказательство молча",
        )

    levels = assistance_cycle or []
    return {
        "attempt_id": attempt_id,
        "objective_id": objective_id,
        "assignment_id": assignment_id,
        "source": source,
        "artifact_sha256": artifact_sha256,
        "text_excerpt": text_excerpt,
        "task_instance_id": task_instance_id,
        # NONE means *no help in this cycle*, which is a positive fact the
        # cycle's assistance events establish. An import with no such records
        # gets UNKNOWN instead — never NONE, which would be a claim nobody made.
        "assistance_max": max_level(*levels) or "NONE",
        "submitted_at": now_iso(clock),
        "_actor": "learner_cli" if source == "learner_report" else "core",
        "_provenance": "human_input" if source == "learner_report" else "host_observed",
    }


def plan_check(*, check_plan_id, session, objective_id, kind, prompt,
               assessment, rubric_id=None, task_instance_id=None,
               provenance="core_computed", clock=None):
    """Register the check *before* the attempt. Order is the whole point.

    A check chosen after seeing the answer can be the easiest check available.
    Registering it first means the difficulty was fixed while it could not be
    tailored to the response, and two checks then really are two.
    """
    if kind not in schemas.SUPPORTED_CHECK_KINDS:
        raise TutoringError("CHECK_KIND_UNKNOWN", "неизвестный вид проверки: %r" % kind)
    if assessment not in ("graded", "practice", "unknown"):
        raise TutoringError("ASSESSMENT_UNKNOWN",
                            "неизвестная оцениваемость: %r" % assessment)
    if provenance == "model_proposed" and assessment == "graded":
        # A model may propose practice. It may not propose a graded check:
        # that is the course's judgment, from the accepted contract.
        raise TutoringError(
            "GRADED_CHECK_REQUIRES_COURSE",
            "модель не может предложить проверку по оцениваемому заданию: "
            "вид и rubric берутся из принятого контракта курса",
        )
    if not prompt or not prompt.strip():
        raise TutoringError("CHECK_PROMPT_EMPTY", "условие проверки пустое")

    return {
        "schema_version": SCHEMA_VERSION,
        "check_plan_id": check_plan_id,
        "session_id": session.get("session_id"),
        "objective_id": objective_id,
        "task_instance_id": task_instance_id,
        "kind": kind,
        "prompt": prompt,
        "rubric_ref": {"rubric_id": rubric_id, "status": provenance} if rubric_id else None,
        "assessment": assessment,
        "provenance": provenance,
        "created_at": now_iso(clock),
    }


def record_check(*, check_id, attempt_id, kind, criterion_results, verdict,
                 assessed_by="model", reliability="provisional",
                 rubric_ref=None, feedback_refs=(), clock=None):
    """Validate a check result. The verdict must follow from the criteria.

    A `pass` whose required criteria are not all `pass` is refused, and a
    positive criterion with no evidence is refused too: those are the two ways
    a check becomes a compliment instead of an assessment.
    """
    if kind not in schemas.SUPPORTED_CHECK_KINDS:
        raise TutoringError("CHECK_KIND_UNKNOWN", "неизвестный вид проверки: %r" % kind)
    if verdict not in ("pass", "partial", "fail", "uncertain"):
        raise TutoringError("VERDICT_UNKNOWN", "неизвестный вердикт: %r" % verdict)
    if assessed_by not in ("model", "deterministic_check", "human"):
        raise TutoringError("ASSESSED_BY_UNKNOWN",
                            "неизвестный оценщик: %r" % assessed_by)
    if reliability not in ("provisional", "observed", "human_reviewed"):
        raise TutoringError("RELIABILITY_UNKNOWN",
                            "неизвестная достоверность: %r" % reliability)
    if not criterion_results:
        raise TutoringError(
            "CHECK_WITHOUT_CRITERIA",
            "проверка без критериев не является проверкой: неизвестно, что именно "
            "подтверждено",
        )

    for result in criterion_results:
        if result.get("result") not in ("pass", "partial", "fail", "unknown"):
            raise TutoringError("CRITERION_RESULT_UNKNOWN",
                                "неизвестный результат критерия: %r" % result.get("result"))
        if result.get("result") == "pass" and not result.get("evidence_refs"):
            raise TutoringError(
                "CRITERION_WITHOUT_EVIDENCE",
                "критерий %r зачтён без основания: положительное решение без "
                "ссылки на доказательство не принимается" % result.get("criterion_id"),
            )

    required = [r for r in criterion_results if r.get("required", True)]
    required_results = [r["result"] for r in required]

    if verdict == "pass" and any(r != "pass" for r in required_results):
        raise TutoringError(
            "VERDICT_INCONSISTENT",
            "вердикт pass при незачтённых обязательных критериях: %s"
            % ", ".join("%s=%s" % (r.get("criterion_id"), r["result"]) for r in required),
        )
    if verdict == "fail" and required_results and all(r == "pass" for r in required_results):
        raise TutoringError(
            "VERDICT_INCONSISTENT",
            "вердикт fail при всех зачтённых обязательных критериях",
        )

    # A model's own judgment is provisional by construction, whatever the
    # caller asked for: only a human or a deterministic check can be otherwise.
    if assessed_by == "model":
        reliability = "provisional"

    return {
        "schema_version": SCHEMA_VERSION,
        "check_id": check_id,
        "attempt_id": attempt_id,
        "kind": kind,
        "rubric_ref": rubric_ref,
        "criterion_results": list(criterion_results),
        "verdict": verdict,
        "assessed_by": assessed_by,
        "reliability": reliability,
        "feedback_refs": list(feedback_refs),
        "checked_at": now_iso(clock),
    }


def record_assistance(*, objective_id, assignment_id, level, step, reason,
                      response_ref=None, clock=None):
    """One rung of the attendance ladder, as an event payload."""
    if level not in ("HINT", "EXAMPLE", "SOLUTION"):
        raise TutoringError("ASSISTANCE_LEVEL_UNKNOWN",
                            "неизвестный уровень помощи: %r" % level)
    if not isinstance(step, int) or step < 0:
        raise TutoringError("ASSISTANCE_STEP_INVALID",
                            "шаг помощи должен быть неотрицательным целым")
    return {
        "objective_id": objective_id,
        "assignment_id": assignment_id,
        "level": level,
        "step": step,
        "reason": reason,
        "response_ref": response_ref,
        "recorded_at": now_iso(clock),
        "_actor": "model_proposal",
        "_provenance": "model_reported",
    }


# --------------------------------------------------------------------------
# Mastery and review
# --------------------------------------------------------------------------

def evaluate_mastery(objective_id, *, checks, attempts, current_stage="new",
                     task_instances=None, clock=None):
    """The mastery transition an objective's evidence supports.

    Returns `(new_stage, reason, evidence_ids)`. This is a **reducer**: it reads
    checks and attempts and returns a state, and the caller writes it. No model
    output takes part, which is what stops "the student seems to understand"
    from becoming a recorded fact.

    The rules, in the order they apply:

    1. no interaction yet → `new`, otherwise → `learning`;
    2. a correct attempt with HINT/EXAMPLE, or one unaided pass → `practising`;
    3. `demonstrated` needs **two different** passing checks on **different
       attempts**, one understanding (`explain`/`critique`) and one
       application (`apply`/`transfer`/`predict`);
    4. a due review moves `demonstrated` → `review_due`;
    5. a failure on review moves back to `practising` and flags the
       prerequisite — without erasing the earlier evidence.
    """
    checks = list(checks or [])
    attempts = list(attempts or [])
    by_id = {a["attempt_id"]: a for a in attempts}

    passed = [c for c in checks if c.get("verdict") == "pass"]

    if not checks and not attempts:
        return "new", "взаимодействия по цели ещё не было", []

    # Two checks on the same attempt are one piece of evidence, however many
    # kinds are recorded against it.
    seen_attempts = set()
    distinct = []
    for check in passed:
        attempt_id = check.get("attempt_id")
        if attempt_id in seen_attempts:
            continue
        seen_attempts.add(attempt_id)
        distinct.append(check)

    understanding = [c for c in distinct if c["kind"] in UNDERSTANDING_KINDS]
    application = [c for c in distinct if c["kind"] in APPLICATION_KINDS]

    if understanding and application:
        evidence = [c["check_id"] for c in (understanding[:1] + application[:1])]
        return ("demonstrated",
                "две разные успешные проверки: объяснение своими словами и "
                "применение/перенос",
                evidence)

    unaided = [c for c in passed
               if (by_id.get(c["attempt_id"], {}).get("assistance_max") in ("NONE", None,
                                                                           "UNKNOWN"))]
    assisted = [c for c in passed if c not in unaided]

    if unaided or assisted:
        reason = ("успешная попытка без помощи" if unaided
                  else "успешная попытка с подсказкой или примером")
        if understanding and not application:
            reason += "; не хватает проверки на применение или перенос"
        elif application and not understanding:
            reason += "; не хватает объяснения своими словами"
        else:
            reason += "; нужна вторая, другая успешная проверка"
        return "practising", reason, [c["check_id"] for c in (unaided or assisted)[:1]]

    if current_stage == "demonstrated":
        return "demonstrated", "доказательства сохранены", []

    return "learning", "попытки были, но ни одна не зачтена", []


def review_due(stage, last_check_at, *, clock=None, reviews_completed=0):
    """Whether a demonstrated objective is due for repetition.

    The ladder is 2 → 7 → 21 days. When the ladder is exhausted the objective
    stays due only on the longest interval, so a long-running course keeps
    refreshing instead of asking forever at two-day spacing.
    """
    if stage != "demonstrated":
        return False, None
    last = parse_iso(last_check_at)
    if last is None:
        return False, None
    index = min(int(reviews_completed or 0), len(REVIEW_INTERVALS) - 1)
    interval = REVIEW_INTERVALS[index]
    moment = (clock() if clock else datetime.now(timezone.utc))
    due_at = last + timedelta(days=interval)
    return moment >= due_at, due_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def apply_review_result(stage, verdict, *, due_at=None, clock=None):
    """The stage after a repetition attempt.

    A failed repetition is information, not a penalty: the objective returns to
    `practising`, the prerequisite is flagged, and nothing that was already
    demonstrated is deleted. Losing a record because of a bad day would make the
    record a source of anxiety rather than a description of learning.
    """
    if verdict == "pass":
        return "demonstrated", "повторение пройдено"
    if verdict in ("partial", "fail", "uncertain"):
        return "practising", ("повторение не подтверждено: цель возвращается в "
                              "«отрабатывается», прежние доказательства сохранены")
    return stage, "вердикт не распознан: состояние не изменено"


def objective_state_document(objective_id, *, stage, evidence_ids=(),
                             latest_check_id=None, assistance_max=None,
                             review_due_at=None, blocked_reason=None,
                             consecutive_stuck_sessions=0, legacy_claim=None):
    """A validated `objective_state` body for the store.

    `assistance_max` is omitted when unknown rather than sent as null: the
    schema requires one of the five levels, and "no attempt has happened yet"
    is not the same statement as "no help was given" (NONE). Writing NONE here
    would invent a fact about a cycle that has not occurred.
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "objective_id": objective_id,
        "stage": stage,
        "evidence_ids": list(evidence_ids),
        "latest_check_id": latest_check_id,
        "review_due_at": review_due_at,
        "blocked_reason": blocked_reason,
        "consecutive_stuck_sessions": int(consecutive_stuck_sessions),
    }
    if assistance_max:
        document["assistance_max"] = assistance_max
    if legacy_claim is not None:
        document["legacy_claim"] = legacy_claim
    # A stage that claims more than the evidence shows is refused here rather
    # than explained later: `demonstrated` with no evidence is the single most
    # damaging thing this record could contain.
    if stage == "demonstrated" and not evidence_ids:
        raise TutoringError(
            "MASTERY_WITHOUT_EVIDENCE",
            "стадия demonstrated требует доказательств: состояние без "
            "ссылок на попытки/проверки не записывается",
        )
    try:
        schemas.validate(document, "objective_state")
    except schemas.SchemaError as e:
        raise TutoringError(e.code, e.message)
    return document


def stuck_signal(consecutive_stuck_sessions):
    """Whether a repeated blocker should be surfaced to a human.

    Three sessions on the same obstacle is the point at which another hint is
    the wrong tool. This only produces a signal; sending anything to a teacher
    happens through the separate, consented export path.
    """
    return int(consecutive_stuck_sessions or 0) >= STUCK_CYCLES_BEFORE_TEACHER


# --------------------------------------------------------------------------
# TeachingResponse validation (the model's own output, before the learner sees it)
# --------------------------------------------------------------------------

RESPONSE_INTENTS = ("diagnose", "hint", "explain", "example", "feedback",
                    "check", "reflect", "stop")

MAX_EXPLANATION_WORDS = 400


def validate_response(course, session, response, *, directive=None):
    """Check a prepared `TeachingResponse` before it is shown to the learner.

    Returns `(accepted, violations, safe_next_action)`. This checks **form**:
    schema, the assistance ceiling for this assessment, that citations point at
    readable material, and that at most one main question is asked.

    What it cannot do — and must not pretend to do — is detect a semantic
    spoiler. A correct-looking response can still reveal the answer in prose.
    On a host with no output interception there is no guarantee this is even
    called; the adapter reports `output_gate=advisory` in that case.
    """
    violations = []

    if not isinstance(response, dict):
        return False, [{"code": "RESPONSE_NOT_OBJECT",
                        "message_ru": "ответ должен быть объектом"}], "regenerate"

    intent = response.get("intent")
    if intent not in RESPONSE_INTENTS:
        violations.append({"code": "RESPONSE_INTENT_UNKNOWN",
                           "message_ru": "неизвестное намерение ответа: %r" % intent})

    levels = [response.get("assistance_level")]
    if response.get("assistance_step") is not None and not isinstance(
            response.get("assistance_step"), int):
        violations.append({"code": "RESPONSE_STEP_INVALID",
                           "message_ru": "assistance_step должен быть целым или null"})

    assessment = response.get("assessment") or session.get("assessment") or "unknown"
    ceiling = ASSESSMENT_CEILING.get(assessment, "EXAMPLE")
    for level in levels:
        if not level:
            continue
        if level not in ("HINT", "EXAMPLE", "SOLUTION"):
            violations.append({"code": "RESPONSE_LEVEL_UNKNOWN",
                               "message_ru": "неизвестный уровень помощи: %r" % level})
        elif _step(level) > _step(ceiling):
            violations.append({
                "code": "RESPONSE_EXCEEDS_CEILING",
                "message_ru": "уровень %s превышает предел %s для оцениваемости «%s»"
                              % (level, ceiling, assessment),
            })

    if response.get("session_id") != session.get("session_id"):
        violations.append({"code": "RESPONSE_SESSION_MISMATCH",
                           "message_ru": "ответ относится к другой сессии"})

    # Citations must point at material this course actually allows reading.
    for citation in response.get("citations") or []:
        if not isinstance(citation, dict):
            violations.append({"code": "CITATION_NOT_OBJECT",
                               "message_ru": "ссылка должна быть объектом"})
            continue
        source = citation.get("source_ref") or {}
        path = source.get("path")
        if path and not course.is_readable_path(path):
            violations.append({
                "code": "CITATION_OUT_OF_SCOPE",
                "message_ru": "ссылка на %r вне разрешённых материалов курса" % path,
            })
        if citation.get("quote_status") in (None, "unverifiable") and citation.get("verbatim"):
            violations.append({
                "code": "CITATION_UNVERIFIED",
                "message_ru": "цитата не проверена по источнику: нельзя показывать "
                              "как точную",
            })

    explanation = response.get("explanation") or ""
    if explanation and len(explanation.split()) > MAX_EXPLANATION_WORDS:
        violations.append({
            "code": "RESPONSE_TOO_LONG",
            "message_ru": "объяснение длиннее %d слов: сократите или разбейте "
                          "на шаги" % MAX_EXPLANATION_WORDS,
        })

    question = response.get("question")
    if isinstance(question, list) and len(question) > 1:
        violations.append({
            "code": "RESPONSE_MULTIPLE_QUESTIONS",
            "message_ru": "в учебном ходе не более одного основного вопроса "
                          "(задано %d)" % len(question),
        })

    if not explanation and not question and intent not in ("stop", "reflect"):
        violations.append({
            "code": "RESPONSE_EMPTY",
            "message_ru": "ответ не содержит ни объяснения, ни вопроса",
        })

    if violations:
        return False, violations, "fix_response"
    return True, [], "show_to_learner"
