# -*- coding: utf-8 -*-
"""Interactive, step-by-step tutorials (design §3.2), offline only.

A tutorial is a local JSON document under `<course_dir>/tutorials/<id>/steps.json`,
validated against the `tutorial` contract before it is used. The contract itself
forbids a `full_solution` field anywhere, so a tutorial file that stores a ready
answer fails validation rather than leaking into learner-facing content.

**The runner never executes anything.** This is the module's load-bearing rule:

* `validation` on a step is a *name* — a reference to a validator the **caller**
  supplies as a Python callable to `submit()`. The harness does not eval it, does
  not interpret it as code, and does not run the learner's code.
* When no callable is given, the step result is `{"status": "unverified"}`: an
  honest "not checked" rather than a fabricated pass.
* A caller that passes a *string* expecting it to be executed gets
  `VALIDATION_NOT_EXECUTABLE` — a refusal, not an attempt.

Hints escalate one rung at a time and stop at the end of the step's ladder;
`advance()` refuses past the last step. `branch_off()` only returns content the
caller already put in `mini_lessons` — the module invents no teaching material.

Nothing here reads outside `course_dir`: the tutorial id goes through
`paths.safe_name` and the resolved directory through `paths.ensure_within`, so a
tutorial cannot become a file-read primitive over an excluded path. A course
whose materials are restricted should additionally be gated by
`policy.read_material` at the caller's edge; the path rules here are the
containment half of that, not a replacement.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths, schemas, tutoring

SCHEMA_VERSION = 2

# Tutorial files live under the course directory; the resolver pattern mirrors
# `personas.personas_dir` — resolved from this file, never from the cwd.
TUTORIALS_DIRNAME = "tutorials"
STEPS_FILENAME = "steps.json"


class TutorialError(RuntimeError):
    """A refused tutorial operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def tutorials_dir(root=None):
    """`<root>/tutorials` when that directory exists, else the repo `tutorials/`.

    Mirrors `personas.personas_dir` / `learner_profiling.profiles_dir`: the
    path is resolved from this file, never from the working directory, because
    the CLI runs from many places.
    """
    if root:
        candidate = Path(root) / TUTORIALS_DIRNAME
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / TUTORIALS_DIRNAME


def _validate_tutorial(tutorial):
    try:
        schemas.validate(tutorial, "tutorial")
    except schemas.SchemaError as e:
        raise TutorialError(e.code, e.message)
    return tutorial


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise TutorialError(
            "TUTORIAL_UNREADABLE",
            "не удалось прочитать файл туториала %s: %s" % (path, e),
        )


def load_tutorial(root, course_dir, tutorial_id):
    """Load `<course_dir>/tutorials/<tutorial_id>/steps.json` and validate it.

    `tutorial_id` is an untrusted component: it goes through `paths.safe_name`
    (a slug or UUID, no separators) and the resolved path through
    `paths.ensure_within(course_tutorials, ...)`, so `../` and absolute ids are
    refused rather than normalised. A tutorial may therefore never point at a
    path the course excludes.
    """
    base = Path(course_dir) / TUTORIALS_DIRNAME
    try:
        safe_id = str(paths.safe_name(tutorial_id, kind="идентификатор туториала"))
    except Exception as e:
        raise TutorialError(
            "TUTORIAL_PATH_OUTSIDE_COURSE",
            "идентификатор туториала %r недопустим (ожидается slug или UUID, "
            "без разделителей пути): %s" % (tutorial_id, e),
        )
    try:
        target = paths.ensure_within(base, base / safe_id / STEPS_FILENAME)
    except paths.PathError as e:
        raise TutorialError(
            "TUTORIAL_PATH_OUTSIDE_COURSE",
            "путь к туториалу выходит за пределы каталога курса: %s" % e,
        )
    except Exception as e:
        # `pathsafe` raises `PathError` in this tree, but a host-local build
        # may ship a different exception class. A bad tutorial id must not
        # surface as a raw traceback: it is a refusal either way.
        raise TutorialError(
            "TUTORIAL_PATH_OUTSIDE_COURSE",
            "путь к туториалу выходит за пределы каталога курса: %s" % e,
        )
    if not target.is_file():
        raise TutorialError(
            "TUTORIAL_NOT_FOUND",
            "туториал %r не найден: ожидался файл %s" % (tutorial_id, target),
        )

    tutorial = _read_json(target)
    if not isinstance(tutorial, dict):
        raise TutorialError(
            "TUTORIAL_NOT_OBJECT", "файл туториала должен содержать JSON-объект"
        )
    _validate_tutorial(tutorial)
    return tutorial


def list_tutorials(root, course_dir):
    """`{"tutorials": [...], "warnings": [...]}` over `<course_dir>/tutorials/`.

    One malformed directory is a warning, not a failure: a broken tutorial in
    the middle of a course must not hide the working ones from the learner.
    """
    base = Path(course_dir) / TUTORIALS_DIRNAME
    tutorials = []
    warnings = []
    if not base.is_dir():
        return {"tutorials": [], "warnings": []}

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            warnings.append("пропущен не-каталог: %s" % child.name)
            continue
        steps_path = child / STEPS_FILENAME
        if not steps_path.is_file():
            warnings.append("в %s нет %s" % (child.name, STEPS_FILENAME))
            continue
        try:
            document = _read_json(steps_path)
            _validate_tutorial(document)
        except TutorialError as e:
            warnings.append(
                "туториал %s пропущен (%s): %s" % (child.name, e.code, e.message)
            )
            continue
        tutorials.append(
            {
                "tutorial_id": document.get("tutorial_id") or child.name,
                "title": document.get("title") or child.name,
                "steps": len(document.get("steps") or []),
            }
        )
    return {"tutorials": tutorials, "warnings": warnings}


class TutorialRunner:
    """The state machine over one loaded tutorial document.

    Holds `current_step` (1-based, matching the schema), `hint_level` (how many
    hints of the current step's ladder have been shown) and `history` (an
    append-only record of what happened, for resumption and review).

    **Nothing in this class executes code.** The `validation` field of a step
    is a NAME for a validator the caller provides; when the caller supplies no
    callable, results are honestly `unverified`. A string passed where a
    callable is expected is refused with `VALIDATION_NOT_EXECUTABLE`.
    """

    def __init__(self, tutorial, *, clock=None):
        _validate_tutorial(tutorial)
        self.tutorial = tutorial
        self._steps = list(tutorial.get("steps") or [])
        self._clock = clock
        self.current_step = 1
        self.hint_level = 0
        self.history = []
        self._started = False

    # -- helpers ------------------------------------------------------------

    @property
    def total_steps(self):
        return len(self._steps)

    @property
    def finished(self):
        return self.current_step > self.total_steps

    def _step_doc(self, step_index):
        if step_index < 1 or step_index > self.total_steps:
            raise TutorialError(
                "STEP_OUT_OF_RANGE",
                "шаг %s вне диапазона 1..%d" % (step_index, self.total_steps),
            )
        return self._steps[step_index - 1]

    def _directive(self):
        step = self._step_doc(self.current_step)
        return {
            "step": step["step"],
            "instruction": step["instruction"],
            "hint_levels": list(step.get("hint_levels") or []),
            "progress": {"done": self.current_step - 1, "total": self.total_steps},
        }

    def _stamp(self):
        return tutoring.now_iso(self._clock)

    def _record(self, kind, **fields):
        entry = {"kind": kind, "at": self._stamp()}
        entry.update(fields)
        self.history.append(entry)
        return entry

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        """The directive for the first step, plus a `started` history entry."""
        if not self._steps:
            raise TutorialError("TUTORIAL_EMPTY", "в туториале нет шагов")
        self.current_step = 1
        self.hint_level = 0
        self._started = True
        self._record("start", tutorial_id=self.tutorial.get("tutorial_id"))
        return self._directive()

    def submit(self, step_index, learner_output, *, validator=None):
        """Record a submission and return its result.

        `validator` is an optional CALLABLE supplied by the caller — typically
        a function that re-checks the step's named condition with whatever the
        host environment provides. The harness **never executes learner code and
        never evaluates a `validation` string from the file**: that string is a
        NAME for the validator, not code to run.

        * `validator is None` → `{"status": "unverified"}`: nothing was checked.
        * `validator` is a callable → called as `validator(step, output)`;
          a truthy return is `"pass"`, falsy/exception is `"fail"` (the
          exception text is kept in the result, not swallowed).
        * `validator` is a string (a `validation` expression handed back to us
          expecting execution) → `TutorialError("VALIDATION_NOT_EXECUTABLE")`.
        """
        step = self._step_doc(int(step_index))

        if validator is not None and not callable(validator):
            raise TutorialError(
                "VALIDATION_NOT_EXECUTABLE",
                "поле validation в файле туториала — это ИМЯ проверки, а не код: "
                "харнесс не выполняет выражения проверки и не исполняет код "
                "обучающегося. Передайте вызываемый объект validator=..., "
                "полученный извне, либо оставьте validator=None (статус "
                '"unverified"). Получено: %r' % (validator,),
            )

        if validator is None:
            outcome: dict = {
                "status": "unverified",
                "reason": "проверка не передана: шаг не проверялся, "
                "результат не выдумывается",
            }
        else:
            try:
                passed = bool(validator(step, learner_output))
                outcome = {"status": "pass" if passed else "fail"}
            except Exception as e:  # the caller's checker failed, not the learner
                outcome = {"status": "fail", "checker_error": str(e)}

        entry = self._record(
            "submit",
            step=step["step"],
            validation_name=step.get("validation"),
            status=outcome["status"],
        )
        outcome["step"] = step["step"]
        outcome["progress"] = {
            "done": self.current_step - 1,
            "total": self.total_steps,
        }
        outcome["history_entry"] = entry
        return outcome

    def request_hint(self):
        """The next hint of the current step's ladder, or a refusal.

        A ladder of 0..5 weak-to-strong nudges; none of them is a finished
        solution (the schema says so). Past the end: `HINTS_EXHAUSTED`.
        """
        step = self._step_doc(self.current_step)
        ladder = list(step.get("hint_levels") or [])
        if not ladder:
            raise TutorialError(
                "HINTS_EXHAUSTED",
                "у шага %d нет подсказок" % step["step"],
            )
        if self.hint_level >= len(ladder):
            raise TutorialError(
                "HINTS_EXHAUSTED",
                "подсказки шага %d исчерпаны (%d из %d показано): "
                "дальше — самостоятельная попытка или новый шаг"
                % (step["step"], self.hint_level, len(ladder)),
            )
        text = ladder[self.hint_level]
        self.hint_level += 1
        self._record("hint", step=step["step"], hint_level=self.hint_level)
        return {
            "step": step["step"],
            "hint_level": self.hint_level,
            "hint": text,
            "remaining": len(ladder) - self.hint_level,
        }

    def advance(self):
        """Move to the next step. `TUTORIAL_COMPLETE` after the last one."""
        if self.current_step >= self.total_steps:
            self._record("complete", tutorial_id=self.tutorial.get("tutorial_id"))
            raise TutorialError(
                "TUTORIAL_COMPLETE",
                "туториал завершён: шаг %d из %d — дальше шагов нет"
                % (self.total_steps, self.total_steps),
            )
        self.current_step += 1
        self.hint_level = 0
        self._record("advance", to_step=self.current_step)
        return self._directive()

    def branch_off(self, error, *, mini_lessons=None):
        """A mini-lesson for `error`, taken ONLY from `mini_lessons`.

        The caller (or the tutorial file, via the caller) supplies the mapping;
        this method never invents content. When nothing matches, the caller
        gets `mini_lesson: None` and the same instruction to re-read the step.
        """
        lessons = dict(mini_lessons or {})
        key = str(error or "").strip()
        lesson = lessons.get(key)
        if lesson is None:
            # A last chance for a substring key, so a traceback mentioning the
            # error name still finds its lesson — but never a fabricated one.
            for candidate in lessons:
                if candidate and candidate in key:
                    lesson = lessons[candidate]
                    break
        self._record("branch", error=key, matched=lesson is not None)
        return {
            "mini_lesson": lesson,  # None means "no lesson on file"
            "return_to_step": self.current_step,
            "error": key,
        }

    # -- resumption ---------------------------------------------------------

    def to_state(self):
        """A JSON-safe snapshot for `from_state`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "tutorial_id": self.tutorial.get("tutorial_id"),
            "tutorial": self.tutorial,
            "current_step": self.current_step,
            "hint_level": self.hint_level,
            "history": list(self.history),
            "started": self._started,
        }

    @classmethod
    def from_state(cls, state, *, clock=None):
        if not isinstance(state, dict):
            raise TutorialError(
                "STATE_NOT_OBJECT", "состояние туториала должно быть объектом"
            )
        tutorial = state.get("tutorial")
        if not isinstance(tutorial, dict):
            raise TutorialError(
                "STATE_MISSING_TUTORIAL", "в состоянии нет документа туториала"
            )
        runner = cls(tutorial, clock=clock)
        runner.current_step = int(state.get("current_step") or 1)
        runner.hint_level = int(state.get("hint_level") or 0)
        runner.history = list(state.get("history") or [])
        runner._started = bool(state.get("started"))
        # A restored step outside the document is a corrupt state, not a
        # free pass to index nowhere.
        runner._step_doc(runner.current_step)
        return runner
