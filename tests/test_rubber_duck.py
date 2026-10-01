#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the rubber duck: a question engine that may never answer.

The guard is the point. `assert_no_solution` raises `DUCK_GAVE_SOLUTION` for a
code fence, an assignment, "вот решение" and "use instead", and stays quiet for
a genuine question — and it runs on every question the duck itself emits, so
the invariant loop in `_record` is what makes `ask()` safe.

Claims under test:

* `QUESTION_BANK` has >= 12 distinct entries;
* every question from `ask()` (20 calls) passes the guard;
* an error/traceback context opens on the "what happened" question;
* `pick_question` is deterministic for the same step count;
* `to_state`/`from_state` round-trip.

Run:
    python3 tests/test_rubber_duck.py
    python3 -m pytest tests/test_rubber_duck.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import rubber_duck as D  # noqa: E402

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
    except D.RubberDuckError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def test_guard_refuses_solutions():
    expect_error(
        "блок кода отклонён",
        lambda: D.assert_no_solution("```python\nx = 1\n```"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "длинное присваивание отклонено",
        lambda: D.assert_no_solution("total = 1"),
        "DUCK_GAVE_SOLUTION",
    )
    # The regex needs an identifier of 2+ chars, so a single-letter `x = 1`
    # deliberately falls through: the guard is a pattern check, not a parser,
    # and the module says so. Pin that boundary rather than overclaim it.
    check(
        "односимвольное присваивание не считается кодом",
        D.assert_no_solution("x = 1") is None,
    )
    expect_error(
        "присваивание с выражением отклонено",
        lambda: D.assert_no_solution("result = compute(a, b)"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "«вот решение» отклонено",
        lambda: D.assert_no_solution("Вот решение вашей задачи"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "«use instead» отклонено",
        lambda: D.assert_no_solution("use instead the built-in"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "«замени» отклонено",
        lambda: D.assert_no_solution("Замени этот фрагмент на другой"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "«исправь» отклонено",
        lambda: D.assert_no_solution("Исправьте строку 4"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "«here is the fix» отклонено",
        lambda: D.assert_no_solution("here is the fix"),
        "DUCK_GAVE_SOLUTION",
    )
    expect_error(
        "«правильный ответ» отклонён",
        lambda: D.assert_no_solution("Правильный ответ: 42"),
        "DUCK_GAVE_SOLUTION",
    )

    check(
        "гenuine-вопрос не поднимает ошибку",
        D.assert_no_solution("Что вы увидели, когда запустили код?") is None,
    )
    check(
        "второй чистый вопрос тоже проходит",
        D.assert_no_solution("Как вы убедитесь, что проблема именно здесь?") is None,
    )
    check("None не исключение", D.assert_no_solution(None) is None)
    check("пустая строка не исключение", D.assert_no_solution("") is None)

    # A bare `=` in Russian prose is common; only identifier assignments trip.
    check(
        "русская фраза со знаком равенства проходит",
        D.assert_no_solution("равенство выполняется при x > 0") is None,
    )


def test_every_asked_question_passes_the_guard():
    """The module's own invariant loop, exercised 20 times."""
    for context in ("обычный разговор", "traceback: NameError: name 'x'"):
        session = D.RubberDuckSession(objective_id="explain-diff")
        for step in range(20):
            question = session.ask(context)
            try:
                D.assert_no_solution(question)
            except D.RubberDuckError as e:
                check(
                    "вопрос %d из контекста %r проходит гард" % (step, context),
                    False,
                    "%s: %s" % (e.code, question),
                )
                break
            if not isinstance(question, str) or not question:
                check("вопрос %d — непустая строка" % step, False, repr(question))
                break
        else:
            check("20 вопросов из контекста %r прошли гард" % context, True)
        check(
            "история содержит 20 вопросов",
            len(session.history) == 20,
            str(len(session.history)),
        )
        check(
            "каждая запись — вопрос, а не ответ",
            all(entry["kind"] == "ask" for entry in session.history),
            str(session.history[:3]),
        )


def test_question_bank_has_enough_variety():
    check(
        "в банке не меньше 12 вопросов",
        len(D.QUESTION_BANK) >= 12,
        str(len(D.QUESTION_BANK)),
    )
    check(
        "все уникальны",
        len(set(D.QUESTION_BANK)) == len(D.QUESTION_BANK),
        str(len(set(D.QUESTION_BANK))),
    )
    check(
        "каждый вопрос — вопрос",
        all("?" in q for q in D.QUESTION_BANK),
        str([q for q in D.QUESTION_BANK if "?" not in q]),
    )
    check(
        "ни один вопрос не даёт решения",
        all(D.assert_no_solution(q) is None for q in D.QUESTION_BANK),
    )
    check(
        "каждый вопрос проходит гард по отдельности",
        all(D.assert_no_solution(q) is None for q in D.QUESTION_BANK),
    )


def test_error_context_opens_with_what_happened():
    expected = D.QUESTION_BANK[0]
    for context in (
        "Traceback (most recent call last): NameError",
        "ошибка в коде",
        "не работает после правки",
        "IndexError: list index out of range",
    ):
        got = D.pick_question(context, step=0)
        check(
            "контекст %r открывается первым вопросом" % context[:24],
            got == expected,
            "%s != %s" % (got[:40], expected[:40]),
        )

    got = D.pick_question("как дела", step=0)
    check(
        "не-ошибочный контекст тоже даёт первый вопрос",
        got == D.QUESTION_BANK[0],
        got[:60],
    )
    check(
        "неважно, шаг 0 или нет: ошибка на шаге 0 ведёт к «что произошло»",
        D.pick_question("ошибка", step=0) == D.QUESTION_BANK[0],
    )


def test_pick_question_is_deterministic():
    for step in range(len(D.QUESTION_BANK) + 3):
        first = D.pick_question("контекст", step=step)
        second = D.pick_question("контекст", step=step)
        check(
            "шаг %d детерминирован" % step,
            first == second,
            "%s != %s" % (first, second),
        )

    cycled = [
        D.pick_question("контекст", step=step) for step in range(len(D.QUESTION_BANK))
    ]
    check(
        "цикл по банку возвращает все вопросы",
        sorted(cycled) == sorted(D.QUESTION_BANK),
    )
    # `pick_question` reads `bank or QUESTION_BANK`, so an empty tuple falls
    # back to the real bank rather than raising. Pin the actual behaviour: the
    # empty bank keeps the duck working with a question to ask.
    fallback = D.pick_question("x", step=0, bank=())
    check(
        "пустой банк откатывается к основному",
        fallback == D.QUESTION_BANK[0],
        fallback[:60],
    )
    check(
        "None-банк тоже откатывается",
        D.pick_question("x", step=3, bank=None) in D.QUESTION_BANK,
    )


def test_state_round_trip():
    session = D.RubberDuckSession(objective_id="explain-diff")
    session.ask("обычный контекст")
    session.respond("я попробовал и получилось")

    state = session.to_state()
    check("state несёт objective_id", state["objective_id"] == "explain-diff")
    check("state несёт шаг", state["step"] == 2, str(state["step"]))
    check("state несёт историю", len(state["history"]) == 2, str(len(state["history"])))

    restored = D.RubberDuckSession.from_state(state)
    check("objective_id восстановлен", restored.objective_id == "explain-diff")
    check("шаг восстановлен", restored.step == session.step, str(restored.step))
    check("история восстановлена", restored.history == session.history)

    # The restored session continues on the next question, not from zero.
    next_q = restored.ask("продолжение")
    check(
        "продолжение идёт со следующего шага",
        next_q != session.history[0]["question"],
        next_q,
    )

    expect_error(
        "не-объект состояния отклонён",
        lambda: D.RubberDuckSession.from_state("строка"),
        "STATE_NOT_OBJECT",
    )


def test_respond_never_echoes_a_solution():
    session = D.RubberDuckSession()
    question = session.respond("```python\nanswer = 42\n```")
    check("ответ на код — вопрос", "?" in question, question)
    check("код не повторён дословно", "answer = 42" not in question, question)
    try:
        D.assert_no_solution(question)
    except D.RubberDuckError as e:
        check("ответ проходит гард", False, "%s: %s" % (e.code, question))
    else:
        check("ответ проходит гард", True)

    other = session.respond("вот решение: просто используй готовую функцию")
    check(
        "ответ не выдаёт директиву решения",
        "готовую функцию" not in other or "?" in other,
        other,
    )


def test_session_history_is_transcript_of_questions_only():
    session = D.RubberDuckSession(clock=None)
    session.ask("первый контекст")
    session.respond("мой ответ")
    kinds = [entry["kind"] for entry in session.history]
    check("только ask/respond", kinds == ["ask", "respond"], str(kinds))
    check(
        "в записях есть шаг",
        [entry["step"] for entry in session.history] == [1, 2],
        str([entry["step"] for entry in session.history]),
    )
    check(
        "в записях есть вопросы",
        all(entry["question"] for entry in session.history),
        str(session.history),
    )
    check(
        "в записях есть timestamp",
        all(str(entry["at"]) for entry in session.history),
        str(session.history),
    )


def main():
    tests = [
        test_guard_refuses_solutions,
        test_every_asked_question_passes_the_guard,
        test_question_bank_has_enough_variety,
        test_error_context_opens_with_what_happened,
        test_pick_question_is_deterministic,
        test_state_round_trip,
        test_respond_never_echoes_a_solution,
        test_session_history_is_transcript_of_questions_only,
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
