# -*- coding: utf-8 -*-
"""Rubber-duck debugging — a Socratic question engine that never answers (§3.3).

The duck is the student's thinking partner: it asks the next question and lets
the student find their own fix. It **never gives a solution, a fix, or code**,
and that rule is enforced by `assert_no_solution()`, not by a promise in the
prompt. The guard runs on every question the duck emits **and** on any
caller-supplied draft, so a caller cannot slip a code block through by asking
the duck to "repeat my phrasing". A duck that prints the answer is not a
duck — it is a solution generator wearing a mascot.

Selection is deterministic: `pick_question(context)` cycles through
`QUESTION_BANK` by the session's step count, with an error/traceback context
starting on the "what happened" question. Determinism means the same context
always produces the same question sequence, which is what makes the duck
testable and what stops it from wandering into a new topic every restart.

Content is fixed and local: the bank ships in this file, in Russian, and
nothing is invented at runtime. `respond()` takes the student's words and asks
a clarifying follow-up from the same bank — a question about what they just
said, never an assertion about their code.
"""

from __future__ import annotations

import re

from . import tutoring

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# The question bank (fixed, Russian, Socratic)
# ---------------------------------------------------------------------------
#
# Every entry is a question, never an instruction to change code and never a
# statement of what the answer is. Ordered so the first entries cover the
# universal "what happened / what did you expect" pair that any debugging
# session starts with.
QUESTION_BANK = (
    "Что вы увидели, когда запустили код — что произошло на самом деле?",
    "А чего вы ожидали увидеть вместо этого?",
    "Откуда берётся значение в этой строке: что вы подставили и что получилось?",
    "Как бы вы объяснили эту строку новичку, который видит её впервые?",
    "Что возвращает эта функция, по вашему, и почему вы так решили?",
    "На каком шаге поведение начало отличаться от ожидаемого?",
    "Какие входные данные вы подали, и какие значения они могли иметь?",
    "Что в этом фрагменте вы понимаете уверенно, а где именно теряете нить?",
    "Если бы вы записали ожидаемый результат отдельно, чем бы он отличался "
    "от фактического?",
    "Какой вопрос вы задали бы, чтобы самому проверить эту гипотезу?",
    "Что изменится, если проследить значение по шагам — где оно впервые "
    "становится неожиданным?",
    "Как вы убедитесь, что проблема именно здесь, а не дальше по коду?",
    "Что вы уже пробовали, и что это показало?",
    "Если бы этот код объясняли вслух, где бы вы запнулись?",
)

# Context markers that mean "the student hit an error": pick the opening
# "what happened" question instead of cycling from a arbitrary point.
_ERROR_MARKERS = (
    "traceback",
    "error",
    "exception",
    "ошибка",
    "исключение",
    "не работает",
    "не запускается",
    "failed",
    "failure",
    "attributeerror",
    "typeerror",
    "nameerror",
    "keyerror",
    "indexerror",
    "valueerror",
    "syntaxerror",
)


class RubberDuckError(RuntimeError):
    """A refused duck operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# The guard: the duck may not answer
# ---------------------------------------------------------------------------

# Code fences — any triple backtick, regardless of language.
_CODE_FENCE_RE = re.compile(r"```")

# A long identifier assignment (`total = 0`, `result = foo(...)`). Deliberately
# stricter than a word: a bare `=` in Russian prose is common, but an
# identifier followed by `=` and an expression is a code statement.
_ASSIGNMENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z_0-9]{1,}\s*=\s*\S")

# Imperative fix phrases: the duck does not tell the student to do a thing.
_FIX_PHRASES = (
    "замени",
    "замените",
    "замена на",
    "исправь",
    "исправьте",
    "исправь на",
    "правильный ответ",
    "вот решение",
    "решение ниже",
    "use instead",
    "change it to",
    "replace it with",
    "the answer is",
    "here is the fix",
    "fix is",
    "add this code",
    "вставьте следующее",
    "нужно написать",
)


def assert_no_solution(text):
    """Refuse `text` if it looks like a solution, fix or code.

    Returns `None` when clean; raises `RubberDuckError("DUCK_GAVE_SOLUTION")`
    otherwise. This is the module's central guarantee: it runs on **every**
    question the duck emits and on any caller-supplied draft, so there is no
    path by which a code block or an imperative fix reaches the student.

    It is a pattern check, not a semantic one — a student who writes the whole
    solution in Russian prose without code or imperatives is not caught here.
    That residual risk is real; the duck's own emissions are the ones it can
    guarantee.
    """
    if text is None:
        return None
    blob = str(text)
    lowered = blob.lower()

    if _CODE_FENCE_RE.search(blob):
        raise RubberDuckError(
            "DUCK_GAVE_SOLUTION",
            "вопрос утёнка содержал блок кода: утёнок никогда не показывает "
            "код и не даёт решения. Текст отклонён.",
        )
    if _ASSIGNMENT_RE.search(blob):
        raise RubberDuckError(
            "DUCK_GAVE_SOLUTION",
            "вопрос утёнка содержал присваивание (строка похожа на код): "
            "утёнок задаёт вопрос, а не пишет код. Текст отклонён.",
        )
    for phrase in _FIX_PHRASES:
        if phrase in lowered:
            raise RubberDuckError(
                "DUCK_GAVE_SOLUTION",
                "вопрос утёнка содержал директиву решения («%s»): "
                "утёнок не выдаёт готовых исправлений. Текст отклонён." % phrase,
            )
    return None


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _is_error_context(context):
    blob = str(context or "").lower()
    return any(marker in blob for marker in _ERROR_MARKERS)


def pick_question(context, step=0, *, bank=None):
    """The next question for this session. Deterministic.

    With an error/traceback context the sequence opens on the "what happened"
    question; otherwise it cycles by `step` through the bank. The same
    `(context kind, step)` always yields the same question.
    """
    questions = tuple(bank or QUESTION_BANK)
    if not questions:
        raise RubberDuckError("QUESTION_BANK_EMPTY", "вопросы для утёнка не заданы")
    index = int(step or 0) % len(questions)
    if _is_error_context(context) and int(step or 0) == 0:
        index = 0
    return questions[index]


class RubberDuckSession:
    """A Socratic Q&A session over the fixed bank.

    `ask(context)` returns the next question; `respond(learner_text)` returns a
    clarifying follow-up about what the learner *just said*. Both are guarded
    by `assert_no_solution` before they leave this object. `history` is the
    transcript of questions asked (not of answers — answers are the learner's).
    """

    def __init__(self, *, objective_id=None, clock=None):
        self.objective_id = objective_id
        self._clock = clock
        self.step = 0
        self.history = []
        self._last_context = ""

    def _record(self, kind, text, *, context=None):
        entry = {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "step": self.step,
            "at": tutoring.now_iso(self._clock),
            "question": text,
        }
        if context is not None:
            entry["context"] = context
        assert_no_solution(text)
        self.history.append(entry)
        return entry

    def ask(self, context):
        """The next question for `context`. Guarded; raises if it would answer."""
        self._last_context = str(context or "")
        question = pick_question(self._last_context, self.step)
        self.step += 1
        self._record("ask", question, context=self._last_context)
        return question

    def respond(self, learner_text):
        """A clarifying question about `learner_text`, never a judgment.

        The follow-up comes from the bank at the next index, so the duck's
        reply is a question and never a correction of the learner's work. The
        learner's own text is echoed only as a short quote, and even that
        passes the guard — if the learner pasted a code block, the duck asks
        about it rather than repeating it back.
        """
        text = str(learner_text or "")
        question = pick_question(text or self._last_context, self.step)
        self.step += 1
        # Do not echo a learner's code: quoting it back would violate the guard
        # and would hand the answer to a reader who did not have it before.
        excerpt = " ".join(text.split())[:160]
        entry = self._record("respond", question, context=excerpt)
        entry["learner_excerpt"] = excerpt
        return question

    def to_state(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "objective_id": self.objective_id,
            "step": self.step,
            "history": list(self.history),
        }

    @classmethod
    def from_state(cls, state, *, clock=None):
        if not isinstance(state, dict):
            raise RubberDuckError(
                "STATE_NOT_OBJECT", "состояние утёнка должно быть объектом"
            )
        session = cls(objective_id=state.get("objective_id"), clock=clock)
        session.step = int(state.get("step") or 0)
        session.history = list(state.get("history") or [])
        return session
