#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for interactive tutorials: the runner never executes anything.

Claims under test:

* `load_tutorial` reads `<course_dir>/tutorials/<id>/steps.json`; a traversal id
  is refused with `TUTORIAL_PATH_OUTSIDE_COURSE`, never normalised;
* a tutorial document validates against the `tutorial` contract, and a
  document carrying a `full_solution` FAILS validation — the schema has no
  such field and `additionalProperties: false` refuses to smuggle one in;
* `submit(..., validator=None)` returns `{"status": "unverified"}`: an honest
  "not checked", never a fabricated pass;
* `submit(..., validator=<callable>)` honours the callable's verdict, and a
  STRING validator raises `VALIDATION_NOT_EXECUTABLE` rather than being
  evaluated;
* hints escalate then raise `HINTS_EXHAUSTED`; `advance` past the last step
  raises `TUTORIAL_COMPLETE`;
* `list_tutorials` reports a malformed directory as a warning, not a crash.

Run:
    python3 tests/test_interactive_tutorial.py
    python3 -m pytest tests/test_interactive_tutorial.py -q
"""

from __future__ import annotations

import json
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

from botai_core import interactive_tutorial as T, schemas  # noqa: E402

_passed = 0
_failures: list[str] = []


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
    except T.TutorialError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def tutorial_document(tutorial_id="intro-git", steps=3):
    return {
        "schema_version": 2,
        "tutorial_id": tutorial_id,
        "title": "Первые шаги",
        "steps": [
            {
                "step": index,
                "instruction": "Шаг %d: выполните действие" % index,
                "validation": "check-%d" % index,
                "hint_levels": ["Намёк %d.1" % index, "Намёк %d.2" % index],
            }
            for index in range(1, steps + 1)
        ],
    }


class Course:
    """A temp course directory with a tutorials/ folder on disk."""

    def __init__(self, document=None, tutorial_id="intro-git", write=True):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.course_dir = self.root / "courses" / "minimal-diff"
        self.tutorials = self.course_dir / T.TUTORIALS_DIRNAME
        (self.tutorials / tutorial_id).mkdir(parents=True, exist_ok=True)
        if write:
            payload = document if document is not None else tutorial_document()
            (self.tutorials / tutorial_id / T.STEPS_FILENAME).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )

    def write_raw(self, tutorial_id, text):
        target = self.tutorials / tutorial_id
        target.mkdir(parents=True, exist_ok=True)
        (target / T.STEPS_FILENAME).write_text(text, encoding="utf-8")

    def cleanup(self):
        self._tmp.cleanup()


def test_load_tutorial_from_disk():
    course = Course()
    try:
        loaded = T.load_tutorial(course.root, course.course_dir, "intro-git")
        check(
            "туториал прочитан",
            loaded["tutorial_id"] == "intro-git",
            str(loaded.get("tutorial_id")),
        )
        check("три шага", len(loaded["steps"]) == 3, str(len(loaded["steps"])))

        try:
            schemas.validate(loaded, "tutorial")
            check("документ соответствует схеме tutorial", True)
        except schemas.SchemaError as e:
            check("документ соответствует схеме tutorial", False, e.message[:200])

        expect_error(
            "несуществующий туториал отклонён",
            lambda: T.load_tutorial(course.root, course.course_dir, "nope"),
            "TUTORIAL_NOT_FOUND",
        )
    finally:
        course.cleanup()


def test_traversal_id_refused():
    course = Course()
    try:
        for bad in ("../../x", "..", "/etc/passwd", "a/b"):
            expect_error(
                "id %r отклонён" % bad,
                lambda b=bad: T.load_tutorial(course.root, course.course_dir, b),
                "TUTORIAL_PATH_OUTSIDE_COURSE",
            )
        check(
            "вне курса ничего не появилось",
            not (course.root / "x").exists()
            and not (course.root.parent / "x").exists(),
        )
    finally:
        course.cleanup()


def test_full_solution_fails_validation():
    """The schema forbids a ready answer anywhere in the tutorial."""
    document = tutorial_document()
    document["steps"][0]["full_solution"] = "просто сделайте так"
    expect_error(
        "шаг с full_solution отклонён схемой",
        lambda: T.TutorialRunner(document),
        "CONTRACT_INVALID",
    )

    document = tutorial_document()
    document["full_solution"] = "ответ"
    expect_error(
        "документ с full_solution отклонён схемой",
        lambda: T.TutorialRunner(document),
        "CONTRACT_INVALID",
    )

    # And it cannot even be loaded off disk.
    course = Course(document=document)
    try:
        expect_error(
            "файл с full_solution не загружается",
            lambda: T.load_tutorial(course.root, course.course_dir, "intro-git"),
            "CONTRACT_INVALID",
        )
    finally:
        course.cleanup()

    check(
        "валидный туториал принимается",
        T.TutorialRunner(tutorial_document()) is not None,
    )


def test_start_returns_first_step_and_total():
    runner = T.TutorialRunner(tutorial_document(steps=4))
    directive = runner.start()
    check("первый шаг", directive["step"] == 1, str(directive["step"]))
    check(
        "название шага",
        directive["instruction"].startswith("Шаг 1"),
        directive["instruction"],
    )
    check(
        "прогресс 0/4",
        directive["progress"] == {"done": 0, "total": 4},
        str(directive["progress"]),
    )
    check(
        "лестница подсказок отдана",
        len(directive["hint_levels"]) == 2,
        str(directive["hint_levels"]),
    )
    check(
        "запись start в истории",
        runner.history[0]["kind"] == "start",
        str(runner.history[0]),
    )


def test_submit_without_validator_is_unverified():
    runner = T.TutorialRunner(tutorial_document())
    runner.start()
    outcome = runner.submit(1, "мой ответ")
    check("статус unverified", outcome["status"] == "unverified", outcome["status"])
    check(
        "причина: проверка не передана",
        "не выдумывается" in outcome["reason"],
        outcome["reason"],
    )
    check(
        "прогресс в результате",
        outcome["progress"]["total"] == 3,
        str(outcome["progress"]),
    )
    check(
        "история пометила submit",
        runner.history[-1]["kind"] == "submit",
        str(runner.history[-1]),
    )
    check("проверка не притворилась pass", outcome["status"] != "pass")


def test_submit_with_callable_validator():
    runner = T.TutorialRunner(tutorial_document())
    runner.start()
    good = runner.submit(1, "ответ", validator=lambda step, out: True)
    check("callable true -> pass", good["status"] == "pass", good["status"])

    bad = runner.submit(1, "ответ", validator=lambda step, out: False)
    check("callable false -> fail", bad["status"] == "fail", bad["status"])

    exploding = runner.submit(
        1,
        "ответ",
        validator=lambda step, out: (_ for _ in ()).throw(ValueError("boom")),
    )
    check(
        "исключение проверяющего -> fail, а не падение",
        exploding["status"] == "fail",
        str(exploding),
    )
    check(
        "текст исключения сохранён",
        "boom" in exploding.get("checker_error", ""),
        str(exploding),
    )


def test_string_validator_refused():
    runner = T.TutorialRunner(tutorial_document())
    runner.start()
    expect_error(
        "строка validation не исполняется",
        lambda: runner.submit(1, "ответ", validator="check-1"),
        "VALIDATION_NOT_EXECUTABLE",
    )
    expect_error(
        "lambda-строка не исполняется",
        lambda: runner.submit(1, "ответ", validator="lambda s: True"),
        "VALIDATION_NOT_EXECUTABLE",
    )
    check(
        "какой-либо статус не вернулся",
        runner.history[-1]["kind"] != "submit"
        or runner.history[-1]["status"] != "pass",
        str(runner.history[-1]),
    )

    expect_error(
        "необъектное состояние отклонено",
        lambda: T.TutorialRunner.from_state("строка"),
        "STATE_NOT_OBJECT",
    )
    expect_error(
        "состояние без туториала отклонено",
        lambda: T.TutorialRunner.from_state({"current_step": 1}),
        "STATE_MISSING_TUTORIAL",
    )


def test_request_hint_escalates_then_exhausts():
    runner = T.TutorialRunner(tutorial_document())
    runner.start()
    first = runner.request_hint()
    check(
        "первая подсказка уровня 1", first["hint_level"] == 1, str(first["hint_level"])
    )
    check("осталась одна", first["remaining"] == 1, str(first["remaining"]))
    second = runner.request_hint()
    check(
        "вторая подсказка уровня 2",
        second["hint_level"] == 2,
        str(second["hint_level"]),
    )
    check("осталось 0", second["remaining"] == 0, str(second["remaining"]))
    expect_error(
        "третья подсказка исчерпана", lambda: runner.request_hint(), "HINTS_EXHAUSTED"
    )

    no_hints = T.TutorialRunner(tutorial_document())
    no_hints.start()
    no_hints.advance()  # step 2 has hints; force a ladder-less step instead
    document = tutorial_document()
    document["steps"][1]["hint_levels"] = []
    bare = T.TutorialRunner(document)
    bare.start()
    bare.advance()
    expect_error(
        "шаг без подсказок -> HINTS_EXHAUSTED",
        lambda: bare.request_hint(),
        "HINTS_EXHAUSTED",
    )


def test_advance_past_last_step():
    runner = T.TutorialRunner(tutorial_document(steps=2))
    runner.start()
    second = runner.advance()
    check("второй шаг", second["step"] == 2, str(second["step"]))
    check("hint_level сброшен на новом шаге", runner.hint_level == 0)
    expect_error(
        "advance за последний шаг", lambda: runner.advance(), "TUTORIAL_COMPLETE"
    )
    check(
        "состояние «завершено» засчитано",
        runner.history[-1]["kind"] == "complete",
        str(runner.history[-1]),
    )

    expect_error(
        "выход шага за пределы диапазона",
        lambda: T.TutorialRunner(tutorial_document())._step_doc(99),
        "STEP_OUT_OF_RANGE",
    )
    expect_error(
        "ноль-шаг отклонён",
        lambda: T.TutorialRunner(tutorial_document())._step_doc(0),
        "STEP_OUT_OF_RANGE",
    )
    expect_error(
        "пустой туториал не стартует",
        lambda: T.TutorialRunner(
            {"schema_version": 2, "tutorial_id": "x", "steps": []}
        ).start(),
        "CONTRACT_INVALID",
    )


def test_state_round_trip():
    runner = T.TutorialRunner(tutorial_document(steps=3))
    runner.start()
    runner.request_hint()
    runner.submit(1, "ответ", validator=None)
    runner.advance()

    state = runner.to_state()
    check("state несёт шаг", state["current_step"] == 2, str(state["current_step"]))
    check("state несёт туториал", state["tutorial"]["tutorial_id"] == "intro-git")
    check("state несёт историю", len(state["history"]) >= 3, str(len(state["history"])))

    restored = T.TutorialRunner.from_state(state)
    check(
        "восстановлен на том же шаге",
        restored.current_step == runner.current_step,
        str(restored.current_step),
    )
    check("восстановлен hint_level", restored.hint_level == runner.hint_level)
    check("история восстановлена", len(restored.history) == len(runner.history))
    directive = (
        restored._directive() if restored.current_step <= restored.total_steps else None
    )
    check(
        "директива читается после восстановления",
        directive is None or directive["step"] == restored.current_step,
        str(directive),
    )


def test_branch_off_invents_nothing():
    runner = T.TutorialRunner(tutorial_document())
    runner.start()
    hit = runner.branch_off("NameError", mini_lessons={"NameError": "урок про имена"})
    check("урок по ключу найден", hit["mini_lesson"] == "урок про имена", str(hit))
    check("шаг возврата назван", hit["return_to_step"] == 1, str(hit))

    substring = runner.branch_off(
        "NameError: name 'x' is not defined",
        mini_lessons={"NameError": "substring-урок"},
    )
    check(
        "подстрочный ключ тоже срабатывает",
        substring["mini_lesson"] == "substring-урок",
        str(substring),
    )

    miss = runner.branch_off("NoSuchError")
    check(
        "без урока — None, а не выдуманный текст",
        miss["mini_lesson"] is None,
        str(miss),
    )


def test_list_tutorials_warns_rather_than_raises():
    course = Course()
    try:
        broken = course.tutorials / "broken"
        broken.mkdir(parents=True, exist_ok=True)
        (broken / T.STEPS_FILENAME).write_text("{ не json", encoding="utf-8")
        (course.tutorials / "no-steps").mkdir(exist_ok=True)
        (course.tutorials / "loose.json").write_text("{}", encoding="utf-8")

        listing = T.list_tutorials(course.root, course.course_dir)
        check(
            "исправный туториал в списке",
            [t["tutorial_id"] for t in listing["tutorials"]] == ["intro-git"],
            str(listing["tutorials"]),
        )
        check(
            "битый каталог — предупреждение, не исключение",
            len(listing["warnings"]) == 3,
            str(listing["warnings"]),
        )
        check(
            "битый JSON назван в предупреждении",
            any(
                "broken" in w and "TUTORIAL_UNREADABLE" in w
                for w in listing["warnings"]
            ),
            str(listing["warnings"]),
        )
        check(
            "каталог без steps.json назван",
            any("no-steps" in w for w in listing["warnings"]),
            str(listing["warnings"]),
        )
        check(
            "не-каталог пропущен с предупреждением",
            any("loose.json" in w for w in listing["warnings"]),
            str(listing["warnings"]),
        )
    finally:
        course.cleanup()

    empty = T.list_tutorial if False else T.list_tutorials
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        course_dir = root / "course"
        course_dir.mkdir(parents=True)
        listing = T.list_tutorials(root, course_dir)
        check(
            "без tutorials/ — пусто, без исключения",
            listing == {"tutorials": [], "warnings": []},
            str(listing),
        )


def test_unreadable_file_is_reported():
    course = Course(write=False)
    try:
        course.write_raw("intro-git", "{ не json")
        expect_error(
            "битый JSON туториала — TUTORIAL_UNREADABLE",
            lambda: T.load_tutorial(course.root, course.course_dir, "intro-git"),
            "TUTORIAL_UNREADABLE",
        )
        course.write_raw("intro-git", "[1, 2, 3]")
        expect_error(
            "JSON-массив вместо объекта отклонён",
            lambda: T.load_tutorial(course.root, course.course_dir, "intro-git"),
            "TUTORIAL_NOT_OBJECT",
        )
    finally:
        course.cleanup()


def test_submit_records_validation_name():
    runner = T.TutorialRunner(tutorial_document())
    runner.start()
    runner.submit(1, "ответ")
    entry = runner.history[-1]
    check(
        "имя проверки записано, но не исполнено",
        entry.get("validation_name") == "check-1",
        str(entry),
    )
    check("статус — unverified", entry.get("status") == "unverified", str(entry))


def main():
    tests = [
        test_load_tutorial_from_disk,
        test_traversal_id_refused,
        test_full_solution_fails_validation,
        test_start_returns_first_step_and_total,
        test_submit_without_validator_is_unverified,
        test_submit_with_callable_validator,
        test_string_validator_refused,
        test_request_hint_escalates_then_exhausts,
        test_advance_past_last_step,
        test_state_round_trip,
        test_branch_off_invents_nothing,
        test_list_tutorials_warns_rather_than_raises,
        test_unreadable_file_is_reported,
        test_submit_records_validation_name,
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
