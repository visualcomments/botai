# -*- coding: utf-8 -*-
"""Adaptive scaffolding: how much of a solution the learner is shown.

Scaffolding is the rung ladder the design's ZPD section asks for: as a learner
stuck on a task shows evidence of being stuck, the help grows — first a nudge,
then a memory hook, then the shape of the solution — and it *fades* back one
step at a time as success returns. The ladder is monotone inside a cycle on
purpose: an engine that lowered its own level mid-cycle on new evidence could
be talked back down to no help at all by a single lucky attempt.

The safety property of this module, and of the repository around it: **level
FULL may not be rendered for a graded assignment.** `AGENTS.md` states that a
graded task never receives a finished answer — not after one try, not after
five, not when the request is rephrased as practice. That table lives in
`tutoring.ASSESSMENT_CEILING`; this module imports it rather than duplicating
it, because two copies of the ceiling drift and the weaker one becomes the
real boundary. `generate_hint(..., assessment="graded"|"unknown")` at level
FULL raises `ScaffoldingError` instead of producing text, and `ceiling_for`
lets a caller ask the same question before building the hint.

Everything else here degrades rather than raises: a `problem_context`
missing its `key_concepts` or `full_solution` produces a level-appropriate
generic Russian hint. A hint engine that raises on a sparse context would
either crash the teaching loop or, worse, push its caller to fill the context
with invented content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum

from . import tutoring as T

# The ceiling and step tables are the tutoring module's, not re-typed here.
# `tutoring` imports only `schemas`, so `tutoring -> scaffolding` cannot form
# a cycle; importing it is what keeps one authority for the assistance table.
ASSESSMENT_CEILING = T.ASSESSMENT_CEILING
LEVEL_STEP = T.LEVEL_STEP

# What each scaffolding level is allowed to reach, in assistance terms. This
# maps the 0..4 ladder onto the HINT/EXAMPLE/SOLUTION vocabulary the policy
# speaks, so `generate_hint` can be checked against the ceiling in one place.
LEVEL_ASSISTANCE = {
    0: None,  # no help at all: "try it yourself"
    1: "HINT",  # a nudge, reveals nothing
    2: "HINT",  # a recall prompt, still a hint
    3: "EXAMPLE",  # the structure of a solution, not the solution
    4: "SOLUTION",  # the finished answer
}


class ScaffoldingError(RuntimeError):
    """A refused scaffolding request, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ScaffoldingLevel(IntEnum):
    """How much of the answer the learner is shown. 0 = nothing."""

    NONE = 0
    ATTENTION = 1
    RECALL = 2
    STRUCTURE = 3
    FULL = 4


# Russian hint templates per level. Level 0 must not hint at all — it exists
# precisely to say "no scaffolding yet", and a template that nudged anyway
# would make the ladder's floor meaningless.
HINT_TEMPLATES = {
    ScaffoldingLevel.NONE: (
        "Попробуй решить самостоятельно. Если застрянешь — попроси подсказку."
    ),
    ScaffoldingLevel.ATTENTION: (
        "Обрати внимание на {focus}. Что ты уже знаешь об этом?"
    ),
    ScaffoldingLevel.RECALL: (
        "Вспомни концепцию {concept}. Как она применяется здесь?"
    ),
    ScaffoldingLevel.STRUCTURE: (
        "Вот структура решения:\n{steps}\n\nТеперь попробуй пройти по ней "
        "самостоятельно, шаг за шагом."
    ),
    ScaffoldingLevel.FULL: (
        "Вот полное решение с объяснением:\n\n{solution}\n\nИзучи его "
        "внимательно и попробуй применить тот же подход к похожей задаче."
    ),
}

# Generic fallbacks used when `problem_context` is missing the keys the real
# template needs. A level-appropriate Russian hint, never an invented answer.
GENERIC_FOCUS = "условие задачи"
GENERIC_CONCEPT = "связанный концепт из программы курса"

GENERIC_STRUCTURE_STEPS = (
    "Перечитай условие и выпиши данные.",
    "Определи, к какой концепции курса относится задача.",
    "Сформулируй, что именно нужно найти.",
    "Реши задачу по шагам и проверь результат.",
)


def ceiling_for(assessment):
    """The highest assistance level `assessment` permits.

    Delegates to `tutoring.ASSESSMENT_CEILING` so there is exactly one table.
    An undeclared assessment is `unknown`, which behaves like `graded`.
    """
    return ASSESSMENT_CEILING.get(
        assessment or "unknown", ASSESSMENT_CEILING["unknown"]
    )


def level_for_assistance(level):
    """The assistance level (`HINT`/`EXAMPLE`/`SOLUTION`) a rung renders as."""
    return LEVEL_ASSISTANCE.get(int(level))


def _as_level(value):
    """Coerce an int or `ScaffoldingLevel` to the enum, clamped 0..4."""
    if isinstance(value, ScaffoldingLevel):
        return value
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ScaffoldingLevel.NONE
    if number < int(ScaffoldingLevel.NONE):
        return ScaffoldingLevel.NONE
    if number > int(ScaffoldingLevel.FULL):
        return ScaffoldingLevel.FULL
    return ScaffoldingLevel(number)


def _get(mapping, key, default=None):
    """Read from a dict-or-object defensively; missing keys never raise."""
    if mapping is None:
        return default
    if isinstance(mapping, dict):
        return mapping.get(key, default)
    return getattr(mapping, key, default)


class ScaffoldingEngine:
    """The rung ladder: escalate on evidence, fade on success.

    The engine holds one piece of state — the current level — and exposes it
    as a serialisable dict (`to_state`/`from_state`) so a session can persist
    it between turns without the session document having to know the enum.
    """

    def __init__(self, *, level=ScaffoldingLevel.NONE, clock=None):
        self.level = _as_level(level)
        self._clock = clock

    # -- the ladder -------------------------------------------------------
    def determine_level(
        self, consecutive_failures, confusion_indicators, student_profile
    ):
        """The level the current evidence supports, monotone within a cycle.

        Ladder (design §1.3, with the profile adjustment):

        * 0 failures → `NONE`; 1 → `ATTENTION`; 2 → `RECALL`;
        * ≥3 with `asks_basic_questions` → `STRUCTURE`; ≥4 → `FULL`;
        * `time_stuck > 600` seconds with ≥2 failures → escalate one rung;
        * `random_changes` (thrashing) with ≥1 failure → escalate one rung;
        * low `challenge_tolerance` (<0.3, easily frustrated) escalates one
          step *faster* (capped at `FULL`);
        * high `challenge_tolerance` (>0.7, wants to be pushed) keeps the
          computed level and never takes the extra step — i.e. it does not
          escalate faster, but the base ladder still applies.

        The returned level is never below `self.level`: within a cycle the
        engine only goes up. Only `fade_scaffolding` lowers it, and that is
        called on success, not on new confusing evidence.
        """
        failures = int(consecutive_failures or 0)
        confusion = (
            confusion_indicators if isinstance(confusion_indicators, dict) else {}
        )
        tolerance = _get(student_profile, "challenge_tolerance")
        try:
            if tolerance is None:
                tolerance = None
            else:
                tolerance = float(tolerance)
        except (TypeError, ValueError):
            tolerance = None

        if failures <= 0:
            base = ScaffoldingLevel.NONE
        elif failures == 1:
            base = ScaffoldingLevel.ATTENTION
        elif failures == 2:
            base = ScaffoldingLevel.RECALL
        elif failures >= 4:
            base = ScaffoldingLevel.FULL
        else:  # failures == 3
            base = (
                ScaffoldingLevel.STRUCTURE
                if confusion.get("asks_basic_questions")
                else ScaffoldingLevel.RECALL
            )

        # Confusion signals push the ladder up independently of the count,
        # each with its own precondition so a single boolean cannot jump the
        # whole ladder on its own.
        escalate_by = 0
        if failures >= 2:
            try:
                stuck = int(confusion.get("time_stuck") or 0)
            except (TypeError, ValueError):
                stuck = 0
            if stuck > 600:
                escalate_by = max(escalate_by, 1)
        if failures >= 1 and confusion.get("random_changes"):
            escalate_by = max(escalate_by, 1)

        proposed = min(int(ScaffoldingLevel.FULL), int(base) + escalate_by)

        # Profile adjustment: frustration tolerance shifts how fast we climb,
        # not whether the evidence exists.
        if tolerance is not None:
            if tolerance < 0.3:
                proposed = min(int(ScaffoldingLevel.FULL), proposed + 1)
            # tolerance > 0.7: keep the computed level; no extra step either
            # way (the "never lower than computed" clause is satisfied by
            # construction, since we never compute below `base`).

        result = _as_level(max(int(proposed), int(self.level)))
        return result

    # -- hint rendering ---------------------------------------------------
    def generate_hint(
        self, level, problem_context, *, language="ru", assessment="practice"
    ):
        """Russian text for `level`, or a `ScaffoldingError` if it is refused.

        Safety: `level == FULL` for a `graded` or `unknown` assessment raises
        `SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT`. This is the assistance ceiling
        from the design — the ladder may grow, but it may not cross the row
        the accepted contract draws for the task.

        `problem_context` is read defensively. Missing keys degrade to a
        level-appropriate generic Russian hint; a missing `full_solution` at
        level FULL degrades to the STRUCTURE output and *says so*, rather than
        inventing an answer the caller did not supply.
        """
        if (language or "ru") != "ru":
            raise ScaffoldingError(
                "LANGUAGE_UNSUPPORTED",
                "скаффолдинг пока выдаёт подсказки только на русском: %r" % language,
            )

        level = _as_level(level)
        context = problem_context if isinstance(problem_context, dict) else {}
        assessment = assessment or "unknown"

        if level == ScaffoldingLevel.FULL:
            ceiling = ceiling_for(assessment)
            if LEVEL_STEP.get(ceiling, 0) < LEVEL_STEP.get("SOLUTION", 4):
                raise ScaffoldingError(
                    "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
                    "уровень FULL (полное решение) запрещён для оцениваемости "
                    "«%s»: потолок помощи здесь — %s. Оценка задания берётся из "
                    "принятого контракта курса, а не из просьбы ученика; "
                    "сформулируй вопрос преподавателю вместо выдачи решения."
                    % (assessment, ceiling),
                )

        return self._render(level, context)

    def _render(self, level, context):
        template = HINT_TEMPLATES.get(level)
        if template is None:
            raise ScaffoldingError(
                "LEVEL_UNKNOWN",
                "неизвестный уровень скаффолдинга: %r" % level,
            )

        if level == ScaffoldingLevel.NONE:
            # The floor template takes no substitutions: level 0 must not hint.
            return template

        key_concepts = context.get("key_concepts") or []
        concept = str(key_concepts[0]) if key_concepts else GENERIC_CONCEPT
        focus = concept if key_concepts else GENERIC_FOCUS

        if level in (ScaffoldingLevel.ATTENTION, ScaffoldingLevel.RECALL):
            return template.format(focus=focus, concept=concept)

        if level == ScaffoldingLevel.STRUCTURE:
            steps = [
                str(s)
                for s in (context.get("solution_structure") or [])
                if str(s).strip()
            ]
            if not steps:
                steps = list(GENERIC_STRUCTURE_STEPS)
            numbered = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(steps))
            return template.format(steps=numbered)

        # level == FULL: the caller must have supplied a solution.
        solution = context.get("full_solution")
        if not solution or not str(solution).strip():
            # No solution in the context: show the structure and say so. The
            # engine will not invent an answer — that would be fabrication.
            steps = [
                str(s)
                for s in (context.get("solution_structure") or [])
                if str(s).strip()
            ]
            if not steps:
                steps = list(GENERIC_STRUCTURE_STEPS)
            numbered = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(steps))
            return (
                "Полное решение недоступно в контексте задачи — вместо него "
                "даётся структура (уровень FULL деградирует до STRUCTURE):\n"
                "%s\n\nКонцепция: %s" % (numbered, concept)
            )
        return template.format(solution=str(solution).strip())

    # -- lifecycle --------------------------------------------------------
    def fade_scaffolding(self, success):
        """One rung down on success (never below `NONE`)."""
        if success and self.level > ScaffoldingLevel.NONE:
            self.level = ScaffoldingLevel(int(self.level) - 1)
        return self.level

    def escalate(self, level):
        """Raise the level to `max(current, level)` — never lower it."""
        target = _as_level(level)
        if int(target) > int(self.level):
            self.level = target
        return self.level

    def reset(self):
        """Back to no scaffolding, for a new task or a fresh cycle."""
        self.level = ScaffoldingLevel.NONE
        return self.level

    # -- persistence ------------------------------------------------------
    def to_state(self):
        """A JSON-friendly snapshot of the engine's state."""
        return {
            "scaffolding_level": int(self.level),
            "scaffolding_level_name": self.level.name,
        }

    @classmethod
    def from_state(cls, state, *, clock=None):
        """Rebuild an engine from `to_state()` output (or a session doc)."""
        value = _get(state, "scaffolding_level", 0)
        return cls(level=_as_level(value), clock=clock)


__all__ = [
    "ScaffoldingError",
    "ScaffoldingLevel",
    "ScaffoldingEngine",
    "HINT_TEMPLATES",
    "ceiling_for",
    "level_for_assistance",
    "ASSESSMENT_CEILING",
    "LEVEL_STEP",
]
