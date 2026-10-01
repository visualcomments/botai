# -*- coding: utf-8 -*-
"""Diagnosing what kind of error an attempt contains — never executing it.

**The attempt is text.** This module reads a learner's submitted text and a
reference solution, compares them as sequences of tokens, and classifies the
difference. It never `exec`s, `eval`s, imports, or otherwise runs the
learner's code: a diagnostic engine that executed attempts would be an
arbitrary-code surface aimed at the student's own work, and a broken attempt
could take the session — or the host — down with it.

The classifier is a priority ladder of deterministic heuristics, each of
which states what evidence it rests on:

1. **CARELESS** — the attempt is nearly identical to the reference
   (SequenceMatcher ratio ≥ 0.92) but not equal: one identifier, one sign, one
   digit differs. Confidence scales with the similarity.
2. **MISSING_PREREQ** — `problem_context["prerequisites"]` contains a concept
   absent from `student_history["mastered_objectives"]`. The first missing one
   becomes `affected_concept`. A missing prerequisite outranks a repeating
   pattern: if the learner has never been taught the prerequisite, a repeated
   failure on the dependent objective is a symptom, not the cause.
3. **CONCEPTUAL** — the same failure signature recurs: the attempt's
   normalised token set has Jaccard ≥ 0.6 against at least two earlier errors
   (or one earlier error plus the current attempt reaching that bar twice).
   Recurrence is what separates "misunderstands this" from "slipped once".
4. **OVERGENERALIZATION** — the attempt applies a token that matches a key
   concept which is neither `main_concept` nor a prerequisite: the learner is
   reaching for a rule from a different part of the course.
5. **PROCEDURAL** — the attempt shares ≥2 identifiers/keywords with
   `problem_context["key_concepts"]` (right structure) but still differs from
   the reference: they know the shape and slipped in execution.
6. **INCOMPLETE** — the fallback, confidence 0.6.

`classify_confidence` documents the thresholds: ≥0.8 is high, 0.6–0.79 is
medium, below 0.6 is low. The engine never raises on empty or `None` inputs —
it returns an `INCOMPLETE` diagnosis with confidence 0.0 and a Russian
evidence string saying the inputs were empty, because a diagnostic that
crashed on an empty attempt would push its caller to invent context to feed
it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

# A near-identical attempt is "almost right": below this ratio the difference
# is too large to blame on a slip.
CARELESS_SIMILARITY = 0.92

# Token Jaccard at or above which two failure signatures count as the same
# misconception recurring.
REPEAT_JACCARD = 0.6

# How many key-concept tokens an attempt must share before its *structure* is
# said to be right (the procedural-slip case).
MIN_SHARED_CONCEPT_TOKENS = 2


class ErrorType(str, Enum):
    """The kinds of error the classifier distinguishes."""

    CONCEPTUAL = "conceptual_misconception"
    PROCEDURAL = "procedural_slip"
    MISSING_PREREQ = "missing_prerequisite"
    CARELESS = "careless_mistake"
    INCOMPLETE = "incomplete_reasoning"
    OVERGENERALIZATION = "overgeneralization"


# Intervention phrasing, in Russian, per type. The diagnosis names what to do
# next; it does not do it — routing to re-teaching is a teaching-loop concern.
INTERVENTION_BY_TYPE = {
    ErrorType.CARELESS: "Просто укажи на опечатку или мелкую неточность.",
    ErrorType.MISSING_PREREQ: (
        "Сначала объясни недостающую предпосылку, потом вернись к задаче."
    ),
    ErrorType.CONCEPTUAL: (
        "Полное переобъяснение концепции с примерами и контрвопросом."
    ),
    ErrorType.OVERGENERALIZATION: (
        "Покажи границу применимости правила: где оно работает, а где нет."
    ),
    ErrorType.PROCEDURAL: ("Укажи место ошибки, не объясняй концепцию заново."),
    ErrorType.INCOMPLETE: "Попроси продолжить рассуждение с того места, где оборвалось.",
}


class ErrorDiagnosisError(RuntimeError):
    """A refused diagnostic call, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def classify_confidence(confidence):
    """Map a confidence to a band: `high` (≥0.8), `medium` (0.6–0.79), `low`.

    Documented here because callers branch on it. The bands are deliberately
    coarse: a 0.02 difference between two heuristic scores is noise, not a
    different decision.
    """
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return "low"
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


@dataclass
class ErrorDiagnosis:
    """What kind of error this is, with the evidence behind the call."""

    error_type: ErrorType
    confidence: float
    evidence: str
    affected_concept: str
    suggested_intervention: str

    def to_document(self):
        """A JSON-friendly dict (the tool-result shape)."""
        return {
            "error_type": self.error_type.value,
            "confidence": round(float(self.confidence), 6),
            "evidence": self.evidence,
            "affected_concept": self.affected_concept,
            "suggested_intervention": self.suggested_intervention,
            "confidence_band": classify_confidence(self.confidence),
        }


def _empty_diagnosis(reason_ru):
    """The degenerate diagnosis returned for empty or unusable inputs."""
    return ErrorDiagnosis(
        error_type=ErrorType.INCOMPLETE,
        confidence=0.0,
        evidence=reason_ru,
        affected_concept="",
        suggested_intervention=(
            "Данных недостаточно для диагностики: запроси у ученика попытку "
            "или дополнь контекст задачи."
        ),
    )


# ---------------------------------------------------------------------------
# Text comparison (pure)
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"#.*?$|//.*?$|/\*.*?\*/", re.S | re.M)
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\sA-Za-z0-9_]")


def _normalize(text):
    """Whitespace-normalised, comment-stripped text for comparison."""
    if text is None:
        return ""
    text = str(text)
    text = _COMMENT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip().lower()
    return text


def _tokens(text):
    """The token list of `text` after normalisation."""
    return _TOKEN_RE.findall(_normalize(text))


def _token_set(text):
    return set(_tokens(text))


def _jaccard(a, b):
    """Jaccard similarity of two token sets; 0.0 when either is empty."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return intersection / float(union)


def _similarity(a, b):
    """0..1 SequenceMatcher ratio over whitespace-normalised, comment-stripped text."""
    left = _normalize(a)
    right = _normalize(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _is_minor_difference(s1, s2):
    """Whether two texts are near-identical (slip, not misunderstanding)."""
    return _similarity(s1, s2) >= CARELESS_SIMILARITY


def _concept_tokens(*concepts):
    """Tokens that represent the named concepts (lower-cased identifiers)."""
    collected = set()
    for concept in concepts or []:
        if not concept:
            continue
        collected.update(_tokens(str(concept)))
    return collected


def _find_repeating_patterns(errors):
    """Group `errors` by their normalised token set; return the repeats.

    Each error may be a raw string or a dict with `text`/`error_text` and an
    optional `concept`. Returns `[{"pattern", "concept", "count"}, ...]` for
    signatures seen **twice or more**, ordered by count then pattern name so
    the result is deterministic.
    """
    buckets = {}
    for entry in errors or []:
        if isinstance(entry, dict):
            text = entry.get("text") or entry.get("error_text") or ""
            concept = str(entry.get("concept") or "")
        else:
            text = entry
            concept = ""
        tokens = _token_set(text)
        if not tokens:
            continue
        # Key on a frozen, sorted token tuple so the same signature always
        # lands in one bucket regardless of word order.
        key = tuple(sorted(tokens))
        bucket = buckets.setdefault(
            key, {"tokens": tokens, "concept": concept, "count": 0}
        )
        bucket["count"] += 1
        if concept and not bucket["concept"]:
            bucket["concept"] = concept

    patterns = []
    for key, bucket in buckets.items():
        if bucket["count"] < 2:
            continue
        patterns.append(
            {
                "pattern": " ".join(key),
                "concept": bucket["concept"],
                "count": bucket["count"],
            }
        )
    patterns.sort(key=lambda item: (-item["count"], item["pattern"]))
    return patterns


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ErrorDiagnosticEngine:
    """Classify one attempt against the reference and the learner's history."""

    def __init__(self, knowledge_graph=None, *, history=None):
        self.knowledge_graph = (
            knowledge_graph if isinstance(knowledge_graph, dict) else {}
        )
        self.history = history if isinstance(history, dict) else {}

    def diagnose(self, attempt, correct_solution, problem_context, student_history):
        """Classify `attempt`. Never raises on empty/None inputs.

        Returns an `ErrorDiagnosis`. `attempt` and `correct_solution` are text
        only — they are compared, never executed.
        """
        context = problem_context if isinstance(problem_context, dict) else {}
        history = student_history if isinstance(student_history, dict) else {}

        if not attempt or not str(attempt).strip():
            return _empty_diagnosis(
                "Попытка ученика пуста или отсутствует: диагностировать нечего."
            )
        if not correct_solution or not str(correct_solution).strip():
            # Without a reference there is nothing to be *wrong against*, but
            # we can still look for a repeating misconception in history.
            repeated = self._repeating_from_history(history)
            if repeated is not None:
                return repeated
            return _empty_diagnosis(
                "Эталонное решение не задано: сравнить попытку не с чем."
            )

        attempt_text = str(attempt)
        reference_text = str(correct_solution)
        similarity = _similarity(attempt_text, reference_text)

        # 1. Careless: near-identical but not equal.
        if similarity >= CARELESS_SIMILARITY and attempt_text != reference_text:
            confidence = round(
                min(0.95, 0.6 + (similarity - CARELESS_SIMILARITY) * 4.0), 4
            )
            return ErrorDiagnosis(
                error_type=ErrorType.CARELESS,
                confidence=confidence,
                evidence=(
                    "Попытка почти совпадает с эталоном (сходство %.2f ≥ %.2f), "
                    "отличается одной мелочью: похоже на опечатку, а не на "
                    "непонимание." % (similarity, CARELESS_SIMILARITY)
                ),
                affected_concept=str(context.get("main_concept") or ""),
                suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.CARELESS],
            )

        # 2. Missing prerequisite.
        missing = self._detect_missing_prerequisite(context, history)
        if missing:
            return ErrorDiagnosis(
                error_type=ErrorType.MISSING_PREREQ,
                confidence=0.8,
                evidence=(
                    "Из предпосылок задачи не освоена «%s»: попытка строится "
                    "на материале, который ученик ещё не прошёл." % missing
                ),
                affected_concept=missing,
                suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.MISSING_PREREQ],
            )

        # 3. Conceptual: a failure signature that recurs.
        repeated = self._detect_misconception(attempt_text, context, history)
        if repeated is not None:
            return repeated

        # 4. Overgeneralization: a key concept the attempt leans on that the
        #    reference solution never needed (see the helper for why the
        #    reference is part of the test).
        over = self._detect_overgeneralization(attempt_text, context, reference_text)
        if over is not None:
            return over

        # 5. Procedural: right structure, wrong execution.
        if self._shows_correct_structure(attempt_text, context):
            main_concept = str(context.get("main_concept") or "")
            return ErrorDiagnosis(
                error_type=ErrorType.PROCEDURAL,
                confidence=0.75,
                evidence=(
                    "В попытке есть концепции задачи (%s), но результат "
                    "отличается от эталона: подход верный, ошибка в выполнении."
                    % ", ".join(
                        sorted(_concept_tokens(*(context.get("key_concepts") or [])))[
                            :6
                        ]
                    )
                ),
                affected_concept=main_concept,
                suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.PROCEDURAL],
            )

        # 6. Fallback.
        main_concept = str(context.get("main_concept") or "")
        return ErrorDiagnosis(
            error_type=ErrorType.INCOMPLETE,
            confidence=0.6,
            evidence=(
                "Решение неполное или неоднозначное: по доступным данным "
                "невозможно выделить ни опечатку, ни повторяющееся "
                "непонимание, ни ошибку в выполнении."
            ),
            affected_concept=main_concept,
            suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.INCOMPLETE],
        )

    # -- heuristics -------------------------------------------------------
    def _detect_missing_prerequisite(self, context, history):
        """The first prerequisite the learner has not mastered (or None)."""
        required = context.get("prerequisites") or []
        mastered = history.get("mastered_objectives") or []
        mastered_set = {str(item) for item in mastered}
        for concept in required:
            if str(concept) not in mastered_set:
                return str(concept)
        return None

    def _repeating_from_history(self, history):
        """A conceptual diagnosis built purely from past errors, if any."""
        patterns = _find_repeating_patterns(history.get("errors") or [])
        if not patterns:
            return None
        top = patterns[0]
        return ErrorDiagnosis(
            error_type=ErrorType.CONCEPTUAL,
            confidence=0.85,
            evidence=(
                "Повторяющаяся ошибка по шаблону «%s» (встречалась %d раз)."
                % (top["pattern"][:80], top["count"])
            ),
            affected_concept=str(top.get("concept") or ""),
            suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.CONCEPTUAL],
        )

    def _detect_misconception(self, attempt_text, context, history):
        """Conceptual when the failure signature recurs (Jaccard ≥ 0.6 twice)."""
        attempt_tokens = _token_set(attempt_text)
        if not attempt_tokens:
            return None

        past_errors = list(history.get("errors") or [])
        # The current attempt joins the comparison set: "recurring" means the
        # same signature shows up in history *and* again now, or twice in
        # history even if the present attempt is a fresh phrasing of it.
        matches = 0
        matching_concept = ""
        for entry in past_errors:
            if isinstance(entry, dict):
                text = entry.get("text") or entry.get("error_text") or ""
                concept = str(entry.get("concept") or "")
            else:
                text = entry
                concept = ""
            other = _token_set(text)
            if _jaccard(attempt_tokens, other) >= REPEAT_JACCARD:
                matches += 1
                if concept and not matching_concept:
                    matching_concept = concept

        patterns = _find_repeating_patterns(past_errors)
        if patterns and any(p["count"] >= 2 for p in patterns):
            top = next(p for p in patterns if p["count"] >= 2)
            return ErrorDiagnosis(
                error_type=ErrorType.CONCEPTUAL,
                confidence=0.85,
                evidence=(
                    "Тот же шаблон ошибки повторяется: «%s» (встречался %d раз) "
                    "и снова в текущей попытке." % (top["pattern"][:80], top["count"])
                ),
                affected_concept=str(
                    top.get("concept")
                    or matching_concept
                    or str(context.get("main_concept") or "")
                ),
                suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.CONCEPTUAL],
            )

        if matches >= 2:
            return ErrorDiagnosis(
                error_type=ErrorType.CONCEPTUAL,
                confidence=0.8,
                evidence=(
                    "Текущая попытка похожа (Жаккар ≥ %.2f) на %d прежних ошибок: "
                    "похоже на систематическое непонимание, а не на единичный "
                    "промах." % (REPEAT_JACCARD, matches)
                ),
                affected_concept=matching_concept
                or str(context.get("main_concept") or ""),
                suggested_intervention=INTERVENTION_BY_TYPE[ErrorType.CONCEPTUAL],
            )
        return None

    def _detect_overgeneralization(self, attempt_text, context, correct_solution=None):
        """A foreign key-concept the attempt leans on that the reference does not.

        The reference matters here. A key concept that appears in *both* the
        attempt and the reference is legitimate task vocabulary — flagging it
        would fire on every correct attempt that names its own topic. What
        distinguishes overgeneralization is a concept token the learner reached
        for that the reference solution never needed: that is the trace of a
        rule imported from another part of the course.
        """
        main = str(context.get("main_concept") or "")
        prerequisites = {str(c) for c in (context.get("prerequisites") or [])}
        key_concepts = [str(c) for c in (context.get("key_concepts") or [])]
        if not key_concepts:
            return None

        attempt_tokens = _token_set(attempt_text)
        reference_tokens = _token_set(correct_solution)
        main_tokens = _concept_tokens(main)
        prereq_tokens = _concept_tokens(*prerequisites)

        for concept in key_concepts:
            if concept == main or concept in prerequisites:
                continue
            concept_tokens = _concept_tokens(concept)
            if not concept_tokens:
                continue
            # The learner's attempt uses this concept...
            if not (concept_tokens <= attempt_tokens):
                continue
            # ...and the concept is foreign to this problem's own material:
            # not the main concept, not a prerequisite, and not something the
            # reference solution itself uses.
            if concept_tokens & (main_tokens | prereq_tokens | reference_tokens):
                continue
            return ErrorDiagnosis(
                error_type=ErrorType.OVERGENERALIZATION,
                confidence=0.7,
                evidence=(
                    "Попытка применяет концепцию «%s», которая не относится к "
                    "текущей задаче (не main_concept, не предпосылка и не "
                    "встречается в эталоне): похоже на перенос правила из "
                    "другого контекста." % concept
                ),
                affected_concept=concept,
                suggested_intervention=INTERVENTION_BY_TYPE[
                    ErrorType.OVERGENERALIZATION
                ],
            )
        return None

    def _shows_correct_structure(self, attempt_text, context):
        """Whether the attempt shares ≥2 key-concept tokens with the task."""
        key_concepts = context.get("key_concepts") or []
        if not key_concepts:
            return False
        attempt_tokens = _token_set(attempt_text)
        concept_tokens = _concept_tokens(*key_concepts)
        if not attempt_tokens or not concept_tokens:
            return False
        shared = attempt_tokens & concept_tokens
        return len(shared) >= MIN_SHARED_CONCEPT_TOKENS


__all__ = [
    "ErrorType",
    "ErrorDiagnosis",
    "ErrorDiagnosticEngine",
    "ErrorDiagnosisError",
    "classify_confidence",
    "CARELESS_SIMILARITY",
    "REPEAT_JACCARD",
    "MIN_SHARED_CONCEPT_TOKENS",
    "INTERVENTION_BY_TYPE",
]
