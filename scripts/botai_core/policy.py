# -*- coding: utf-8 -*-
"""Policy decisions: may this help be given, and how much may it reveal?

The single most important rule in the whole harness is that a graded assignment
never receives a ready solution — not after three failed attempts, not when the
student says it is practice, not when the request is rephrased as a translation
or a "just fill in the TODO". `AGENTS.md` states that rule; this module is where
it becomes checkable.

Why code and not a paragraph of prompt: the rule has to hold when the model
misreads the prompt, when a course document contains an instruction to ignore
it, and when the request arrives inside a role-play persona. A rule the model
enforces on itself is a rule the model can be talked out of. What this module
does *not* claim is that it can intercept free text: a host without an output
gate can still be told the answer in prose. That residual risk is stated in the
design (§4.3) and is a scenario-evaluation concern, not something a JSON check
can close. Claiming otherwise would be the more dangerous error.

Decisions are computed from the accepted course contract. When there is no
accepted course, or the assignment is not declared, the answer is `unknown`
handling — strict, never a permissive default.
"""

from __future__ import annotations

from . import schemas

# Assistance ladder, least revealing first. The numbers are the design's
# ladder steps; they are here so a caller can compare levels without knowing
# the string ordering.
LEVEL_STEP = {"HINT": 1, "EXAMPLE": 3, "SOLUTION": 4}
STEP_LEVEL = {1: "HINT", 3: "EXAMPLE", 4: "SOLUTION"}

# The highest level each assessment kind may ever reach, regardless of
# preference or persistence. `graded` and `unknown` stop at EXAMPLE: an
# analogous worked case teaches, a finished answer does not.
ASSESSMENT_CEILING = {
    "graded": "EXAMPLE",
    "unknown": "EXAMPLE",
    "practice": "SOLUTION",
}

# Intents the tutoring surface may express, and the level each one implies.
INTENT_LEVEL = {
    "diagnose": "HINT",
    "hint": "HINT",
    "explain": "EXAMPLE",
    "example": "EXAMPLE",
    "feedback": "EXAMPLE",
    "check": None,        # presenting a check is not assistance at all
    "reflect": None,
    "stop": None,
    "solution": "SOLUTION",
}

# What the student's recorded feedback preference permits *without asking
# again*. `prefer-ask` is not a low ceiling: it means "do not decide for me",
# so a level above HINT is reachable when the learner asks for it, and is
# refused only when the agent would escalate on its own initiative.
PREFERENCE_CEILING = {
    "hints": "HINT",
    "hints-then-solution": "SOLUTION",
    "solution-first": "SOLUTION",
    "prefer-ask": "EXAMPLE",
}

# Preferences under which an explicit learner request lifts the ceiling to the
# assessment ceiling, because the preference defers the choice to the learner.
DEFERS_TO_LEARNER = {"prefer-ask", "solution-first"}


class PolicyDecision(dict):
    """A decision that renders as the policy schema.

    A dict subclass rather than a dataclass because it is serialised directly
    into tool results and stored as event payload; converting between a model
    object and the wire form at every boundary is how the two drift.
    """

    @property
    def allowed(self):
        return self["decision"] == "allow"

    @property
    def code(self):
        return self["reason_code"]


def _decision(decision, code, message, **fields):
    document = {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "decision": decision,
        "reason_code": code,
        "message_ru": message,
    }
    document.update({k: v for k, v in fields.items() if v is not None})
    document.setdefault("alternatives", [])
    return PolicyDecision(document)


def validate_decision(document):
    """Validate a decision against the published policy contract."""
    return schemas.validate(dict(document), "policy")


# --------------------------------------------------------------------------
# The assistance ladder
# --------------------------------------------------------------------------

def assessment_ceiling(assessment):
    """The highest assistance level an assessment kind permits."""
    return ASSESSMENT_CEILING.get(assessment, ASSESSMENT_CEILING["unknown"])


def request_help(course, *, assignment_id, intent, preference="prefer-ask",
                 student_requested=False, previous_level=None):
    """Decide whether the requested help may be given, and its ceiling.

    Inputs the caller must supply honestly:

    * `course` — the `AcceptedCourse`, or None when nothing is accepted. None is
      not "unrestricted": it is the strict case.
    * `intent` — what the agent wants to do, from `INTENT_LEVEL`.
    * `student_requested` — whether the learner asked for this level, as opposed
      to the agent deciding to escalate on its own.
    """
    if intent not in INTENT_LEVEL:
        return _decision(
            "deny", "INTENT_UNKNOWN",
            "неизвестное учебное намерение %r: уровень помощи не определён" % intent,
            assessment="unknown", assignment_id=assignment_id,
        )

    level = INTENT_LEVEL[intent]

    # Presenting a check, reflecting, or stopping is not assistance: an ordinary
    # question about a task is not a hint, and tagging it as one would make the
    # assistance record meaningless.
    if level is None:
        assessment = _assessment(course, assignment_id)[0]
        return _decision(
            "allow", "NOT_ASSISTANCE",
            "это не помощь, а элемент учебного цикла: уровень помощи не назначается",
            assessment=assessment, assignment_id=assignment_id,
            assistance_ceiling=None, requested_level=None,
            rule_ref="policy.INTENT_LEVEL[%s]" % intent,
        )

    assessment, why = _assessment(course, assignment_id)
    ceiling = assessment_ceiling(assessment)
    rule_ref = _rule_ref(course, assignment_id, assessment)

    preference_ceiling = PREFERENCE_CEILING.get(preference, PREFERENCE_CEILING["prefer-ask"])

    # A preference that defers the decision to the learner is lifted by the
    # learner's own request — but only up to the assessment ceiling, which is
    # absolute. `hints` is *not* lifted: a student who chose hints has stated a
    # preference against being handed the answer, and only changing that
    # preference changes it.
    if student_requested and preference in DEFERS_TO_LEARNER:
        preference_ceiling = _higher(preference_ceiling, ceiling)

    # The strictest limit wins. Ordering matters: a permissive preference must
    # never lift the assessment ceiling, and an unrequested escalation must
    # never lift the preference ceiling.
    if _step(level) > _step(ceiling):
        return _decision(
            "deny", "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
            "готовый разбор этой задачи недопустим: оцениваемость «%s». "
            "Разрешённый максимум — %s. %s" % (assessment, ceiling, why),
            assessment=assessment, assignment_id=assignment_id,
            assistance_ceiling=ceiling, requested_level=level,
            rule_ref=rule_ref,
            alternatives=[
                "разобрать нетождественный тренировочный пример (%s)" % ceiling,
                "разобрать собственную попытку ученика без готового решения",
                "предложить следующий мыслительный шаг и вернуть задачу ученику",
                "при длительном затруднении — подготовить вопрос преподавателю",
            ],
        )

    if _step(level) > _step(preference_ceiling):
        return _decision(
            "deny", "PREFERENCE_CEILING",
            "ученик выбрал стиль помощи «%s», который не предполагает уровень %s "
            "без отдельного согласия" % (preference, level),
            assessment=assessment, assignment_id=assignment_id,
            assistance_ceiling=preference_ceiling, requested_level=level,
            rule_ref="policy.PREFERENCE_CEILING[%s]" % preference,
            alternatives=[
                "спросить ученика, повышать ли уровень помощи",
                "дать %s в пределах выбранного стиля" % preference_ceiling,
            ],
        )

    # Escalating without being asked is how a session drifts into solving the
    # task, one helpful step at a time.
    if not student_requested and previous_level is not None:
        if _step(level) > _step(previous_level) + 1:
            return _decision(
                "deny", "ESCALATION_TOO_FAST",
                "уровень помощи повышается не более чем на одну ступень за раз",
                assessment=assessment, assignment_id=assignment_id,
                assistance_ceiling=ceiling, requested_level=level,
                rule_ref="policy.escalation_one_step",
                alternatives=["сначала дать %s" % previous_level],
            )

    code = "ALLOWED_PRACTICE" if assessment == "practice" else "ALLOWED_BELOW_CEILING"
    message = (
        "полный разбор допустим: подтверждённая тренировочная задача"
        if assessment == "practice" and level == "SOLUTION"
        else "помощь уровня %s допустима (оцениваемость «%s», предел %s)"
             % (level, assessment, ceiling)
    )
    return _decision(
        "allow", code, message,
        assessment=assessment, assignment_id=assignment_id,
        assistance_ceiling=ceiling, requested_level=level,
        rule_ref=rule_ref,
    )


def _step(level):
    return LEVEL_STEP.get(level or "", 0)


def _higher(left, right):
    """The more revealing of two levels, by ladder position."""
    return left if _step(left) >= _step(right) else right


def _assessment(course, assignment_id):
    if course is None:
        return "unknown", (
            "курс не принят, поэтому оцениваемость задания не установлена"
        )
    return course.assessment_of(assignment_id)


def _rule_ref(course, assignment_id, assessment):
    if course is None:
        return "policy.no_accepted_course"
    ref = course.assessment_rule_ref(assignment_id)
    if ref:
        return ref
    return "policy.assessment_default_unknown"


# --------------------------------------------------------------------------
# Assessment resolution as a task in its own right
# --------------------------------------------------------------------------

def resolve_assessment(course, assignment_id):
    """The graded/practice answer for a task, with its source, for display.

    The learner is *told* the policy; they are not asked to restate it. A course
    that declares nothing gets `unknown` and says so, which is different from
    "we forgot to look".
    """
    if course is None:
        return {
            "assessment": "unknown",
            "assignment_id": assignment_id,
            "source": "no_accepted_course",
            "message_ru": "курс не принят: оцениваемость не установлена, "
                          "действует строгий режим",
        }
    assessment, why = course.assessment_of(assignment_id)
    entry = course.assignment(assignment_id)
    return {
        "assessment": assessment,
        "assignment_id": assignment_id,
        "source": "accepted_contract" if entry else "not_declared",
        "source_ref": (entry or {}).get("assessment_source"),
        "message_ru": why,
    }


def goal_change(course, objective_id, *, demonstrated_ids=()):
    """Whether an objective may be started now, given its prerequisites.

    Skipping a prerequisite is not personalisation; it is how a student ends up
    in material they cannot yet follow. Backtracking to repeat a prerequisite is
    always allowed.
    """
    if course is None:
        return _decision(
            "deny", "COURSE_NOT_ACCEPTED",
            "курс не принят: цели курса не установлены",
            objective_id=objective_id,
        )
    entry = course.objective(objective_id)
    if entry is None:
        return _decision(
            "deny", "OBJECTIVE_UNKNOWN",
            "цель %r отсутствует в принятой программе курса" % objective_id,
            objective_id=objective_id,
            rule_ref="course.track.objectives",
        )
    missing = course.unsatisfied_prerequisites(objective_id, demonstrated_ids)
    if missing:
        return _decision(
            "needs_confirmation", "PREREQUISITE_UNVERIFIED",
            "у цели %s не подтверждены предпосылки: %s. Начать можно, но "
            "освоение не отмечается до проверки, и первый вопрос будет "
            "безопасным, а не предполагающим знание" % (objective_id, ", ".join(missing)),
            objective_id=objective_id,
            rule_ref="course.track.objectives[%s].prerequisites" % objective_id,
            alternatives=["повторить предпосылку %s" % missing[0],
                          "выбрать цель без неподтверждённых предпосылок"],
        )
    return _decision(
        "allow", "OBJECTIVE_AVAILABLE",
        "цель %s доступна: предпосылки подтверждены" % objective_id,
        objective_id=objective_id,
        rule_ref="course.track.objectives[%s].prerequisites" % objective_id,
    )


# --------------------------------------------------------------------------
# Effectful operations
# --------------------------------------------------------------------------

# Operations that change something outside the study session. Each one needs a
# human approval bound to an exact plan, not a general "yes".
EFFECTFUL_OPERATIONS = {
    "env_apply": "установка и настройка среды курса на этой машине",
    "course_prepare_apply": "создание копии курса, корпуса и среды",
    "course_add": "клонирование нового курса в рабочее пространство",
    "course_accept": "принятие условий курса как управляющей политики",
    "privacy_delete": "удаление учебных записей",
    "privacy_export": "создание пакета данных для передачи",
    "teacher_import": "импорт пакета в пространство преподавателя",
    "update": "обновление обвязки",
    "adapter_install": "изменение конфигурации хоста",
}

# Operations that write outside the workspace or publish. These are never
# available to the tutoring model at all, regardless of approval.
MODEL_FORBIDDEN = {
    "git_commit": "коммит должен делать ученик: авторство нельзя подменять",
    "git_push": "публикация выполняется учеником",
    "pull_request": "открытие PR выполняет ученик",
    "grade_set": "итоговая оценка остаётся за человеком",
    "role_change": "роль рабочего пространства задаёт человек при установке",
    "consent_set": "согласие на обработку данных выражает только человек",
    "mastery_set": "освоение выводится из доказательств, а не устанавливается",
    "approve": "разрешение выдаёт человек из отдельного терминала",
}


def request_operation(operation, *, actor="model_proposal", approval=None,
                      plan_hash=None, host_can_approve=True):
    """Decide whether an effectful operation may run.

    `approval` is a stored approval record for this exact operation and plan
    hash. A model cannot mint one: the tool surface has no such command, and a
    passed-in dictionary with the right shape is not evidence that a human
    agreed to anything.
    """
    if operation in MODEL_FORBIDDEN:
        return _decision(
            "deny", "HUMAN_ONLY_OPERATION",
            "%s: %s" % (operation, MODEL_FORBIDDEN[operation]),
            rule_ref="policy.MODEL_FORBIDDEN[%s]" % operation,
            alternatives=["показать ученику точную команду и объяснить последствия"],
        )

    if operation not in EFFECTFUL_OPERATIONS:
        return _decision(
            "deny", "OPERATION_UNKNOWN",
            "операция %r не входит в разрешённый набор" % operation,
            rule_ref="policy.EFFECTFUL_OPERATIONS",
        )

    if not host_can_approve:
        return _decision(
            "deny", "HOST_UNVERIFIED",
            "хост не позволяет надёжно отличить человеческое разрешение от "
            "модельного: операция не выполняется. Покажите план и дайте "
            "инструкцию ручного запуска. Режим «никогда не спрашивать» не "
            "является разрешением на всё.",
            rule_ref="policy.host_can_approve",
            alternatives=["выполнить шаг вручную по показанному плану",
                          "использовать профиль compatibility без автоматизации"],
        )

    if not approval:
        return _decision(
            "needs_confirmation", "APPROVAL_REQUIRED",
            "план подготовлен, но не разрешён пользователем: %s"
            % EFFECTFUL_OPERATIONS[operation],
            rule_ref="policy.EFFECTFUL_OPERATIONS[%s]" % operation,
            alternatives=["показать план и выполнить action-approve в отдельном терминале"],
        )

    if approval.get("consumed_at"):
        return _decision(
            "deny", "APPROVAL_CONSUMED",
            "разрешение уже использовано: повторное разрешение не подставляется",
            rule_ref="policy.approval_single_use",
        )

    approved_hash = approval.get("plan_hash")
    if plan_hash and approved_hash and approved_hash != plan_hash:
        return _decision(
            "deny", "APPROVAL_STALE",
            "разрешение относится к другому плану (%s вместо %s): входы изменились, "
            "нужен новый план и новое подтверждение"
            % (approved_hash[:12], plan_hash[:12]),
            rule_ref="policy.approval_plan_hash",
        )

    return _decision(
        "allow", "APPROVED",
        "операция разрешена человеком для этого плана",
        rule_ref="policy.EFFECTFUL_OPERATIONS[%s]" % operation,
    )


# --------------------------------------------------------------------------
# Reading material
# --------------------------------------------------------------------------

def read_material(course, relative_path):
    """Whether the agent may read a course-relative path.

    Exclusions beat allowances, and when no course is accepted there is no
    allowance at all — the scope comes from the contract, not from the directory
    happening to exist.
    """
    if course is None:
        return _decision(
            "deny", "COURSE_NOT_ACCEPTED",
            "курс не принят: разрешённые материалы не определены",
            rule_ref="policy.no_accepted_course",
        )
    if course.is_readable_path(relative_path):
        return _decision(
            "allow", "MATERIAL_ALLOWED",
            "материал входит в разрешённые корни принятого курса",
            rule_ref="course.student_material_roots",
        )
    return _decision(
        "deny", "MATERIAL_OUT_OF_SCOPE",
        "путь %r вне разрешённых материалов курса (исключения сильнее разрешений: "
        "решения и материалы преподавателя не читаются)" % relative_path,
        rule_ref="course.student_material_roots/excluded_material_roots",
    )


def search_scope(course):
    """The roots and exclusions a search must be limited to, before retrieval."""
    if course is None:
        raise ValueError("курс не принят: область поиска не определена")
    return {
        "course_id": course.course_id,
        "course_revision": course.revision,
        "contract_hash": course.contract_hash,
        "include_roots": list(course.contract["student_material_roots"]),
        "exclude_roots": list(course.contract.get("excluded_material_roots") or []),
        "corpus_manifest_path": course.contract.get("corpus_manifest_path"),
    }
