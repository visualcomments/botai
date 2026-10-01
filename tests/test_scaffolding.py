#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for adaptive scaffolding: the rung ladder and its safety ceiling.

Claims under test:

* the failure ladder maps 0/1/2/3+failures to NONE/ATTENTION/RECALL/STRUCTURE
  and ≥4 to FULL; low `challenge_tolerance` escalates faster, high tolerance
  does not take the extra step;
* the level is monotone inside a cycle — `determine_level` never returns lower
  than the engine's current level;
* **the safety property**: `generate_hint(FULL, ..., assessment="graded"|"unknown")`
  raises `SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT`; `assessment="practice"` succeeds;
* a missing `key_concepts`/`full_solution` degrades to a generic hint and a
  FULL level with no `full_solution` does not invent one;
* `ceiling_for` equals `tutoring.ASSESSMENT_CEILING` for the same assessment.

Run:
    python3 tests/test_scaffolding.py
    python3 -m pytest tests/test_scaffolding.py -q
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = sys.path[0] if sys.path else "."
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import scaffolding as S, tutoring as T  # noqa: E402

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
    except S.ScaffoldingError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def profile(tolerance):
    return {"challenge_tolerance": tolerance}


def test_failure_ladder():
    engine = S.ScaffoldingEngine()
    check(
        "0 неудач -> NONE",
        engine.determine_level(0, {}, None) == S.ScaffoldingLevel.NONE,
    )
    check(
        "1 неудача -> ATTENTION",
        engine.determine_level(1, {}, None) == S.ScaffoldingLevel.ATTENTION,
    )
    check(
        "2 неудачи -> RECALL",
        engine.determine_level(2, {}, None) == S.ScaffoldingLevel.RECALL,
    )

    engine3 = S.ScaffoldingEngine()
    check(
        "3 неудачи с asks_basic_questions -> STRUCTURE",
        engine3.determine_level(3, {"asks_basic_questions": True}, None)
        == S.ScaffoldingLevel.STRUCTURE,
    )
    engine3b = S.ScaffoldingEngine()
    check(
        "3 неудачи без вопросов -> RECALL",
        engine3b.determine_level(3, {}, None) == S.ScaffoldingLevel.RECALL,
    )

    engine4 = S.ScaffoldingEngine()
    check(
        "4 неудачи -> FULL",
        engine4.determine_level(4, {}, None) == S.ScaffoldingLevel.FULL,
    )


def test_challenge_tolerance_shifts_speed():
    low = S.ScaffoldingEngine()
    check(
        "низкая толерантность ускоряет подъём (1 неудача -> RECALL)",
        low.determine_level(1, {}, profile(0.2)) == S.ScaffoldingLevel.RECALL,
    )
    low3 = S.ScaffoldingEngine()
    # `asks_basic_questions` is absent: the base for 3 failures without it is
    # RECALL (2), and low tolerance adds exactly one rung -> STRUCTURE (3).
    check(
        "низкая толерантность ускоряет подъём (3 неудачи без вопросов -> STRUCTURE)",
        low3.determine_level(3, {}, profile(0.2)) == S.ScaffoldingLevel.STRUCTURE,
    )
    low3b = S.ScaffoldingEngine()
    check(
        "низкая толерантность вместе с вопросами -> FULL (4)",
        low3b.determine_level(3, {"asks_basic_questions": True}, profile(0.2))
        == S.ScaffoldingLevel.FULL,
    )

    high = S.ScaffoldingEngine()
    check(
        "высокая толерантность не добавляет ступень (3 неудачи -> STRUCTURE)",
        high.determine_level(3, {"asks_basic_questions": True}, profile(0.8))
        == S.ScaffoldingLevel.STRUCTURE,
    )
    high4 = S.ScaffoldingEngine()
    check(
        "высокая толерантность не опускает базовую лестницу (4 -> FULL)",
        high4.determine_level(4, {}, profile(0.8)) == S.ScaffoldingLevel.FULL,
    )

    neutral = S.ScaffoldingEngine()
    check(
        "нейтральная толерантность 0.5 не ускоряет (1 -> ATTENTION)",
        neutral.determine_level(1, {}, profile(0.5)) == S.ScaffoldingLevel.ATTENTION,
    )


def test_monotonic_within_cycle():
    engine = S.ScaffoldingEngine()
    order = []
    for failures in (0, 1, 2, 3, 2, 1, 0):
        level = engine.determine_level(failures, {}, None)
        engine.escalate(level)  # engine keeps the max within the cycle
        order.append(int(level))
    check("внутри цикла уровень не понижается сам", order == sorted(order), str(order))
    check("уровень движка не ниже вычисленного", int(engine.level) >= 1)

    engine2 = S.ScaffoldingEngine(level=S.ScaffoldingLevel.RECALL)
    got = engine2.determine_level(0, {}, None)
    check(
        "после RECALL новая оценка не опускает уровень",
        got == S.ScaffoldingLevel.RECALL,
        str(got),
    )


def test_fade_scaffolding():
    engine = S.ScaffoldingEngine(level=S.ScaffoldingLevel.FULL)
    check(
        "fade на 1 ступень вниз",
        engine.fade_scaffolding(True) == S.ScaffoldingLevel.STRUCTURE,
    )
    engine.fade_scaffolding(True)
    check("второй fade -> RECALL", engine.level == S.ScaffoldingLevel.RECALL)
    engine.level = S.ScaffoldingLevel.NONE
    check(
        "fade на NONE остаётся NONE",
        engine.fade_scaffolding(True) == S.ScaffoldingLevel.NONE,
    )
    engine.level = S.ScaffoldingLevel.ATTENTION
    check(
        "fade при неуспехе не снижает",
        engine.fade_scaffolding(False) == S.ScaffoldingLevel.ATTENTION,
    )


def test_full_hint_refused_for_graded_and_unknown():
    engine = S.ScaffoldingEngine()
    context = {
        "key_concepts": ["git diff"],
        "full_solution": "решение",
        "solution_structure": ["шаг"],
    }
    expect_error(
        "FULL + graded -> SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
        lambda: engine.generate_hint(
            S.ScaffoldingLevel.FULL, context, assessment="graded"
        ),
        "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
    )
    expect_error(
        "FULL + unknown -> SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
        lambda: engine.generate_hint(
            S.ScaffoldingLevel.FULL, context, assessment="unknown"
        ),
        "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
    )
    expect_error(
        "FULL без оцениваемости (None) -> отказ",
        lambda: engine.generate_hint(S.ScaffoldingLevel.FULL, context, assessment=None),
        "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
    )

    practice = engine.generate_hint(
        S.ScaffoldingLevel.FULL, context, assessment="practice"
    )
    check("FULL + practice разрешён", "решение" in practice, practice[:120])
    example = engine.generate_hint(
        S.ScaffoldingLevel.STRUCTURE, context, assessment="graded"
    )
    check(
        "STRUCTURE для graded допустим",
        "Вот структура" in example or "шаг" in example,
        example[:120],
    )


def test_generate_hint_degrades_without_context():
    engine = S.ScaffoldingEngine()
    try:
        got = engine.generate_hint(
            S.ScaffoldingLevel.ATTENTION, {}, assessment="practice"
        )
        check("ATTENTION без key_concepts не падает", "условие задачи" in got, got)
        got2 = engine.generate_hint(
            S.ScaffoldingLevel.RECALL, {"key_concepts": []}, assessment="practice"
        )
        check("RECALL без концептов не падает", "концепт" in got2, got2)
        got3 = engine.generate_hint(
            S.ScaffoldingLevel.STRUCTURE, {}, assessment="practice"
        )
        check(
            "STRUCTURE без solution_structure не падает",
            "Перечитай условие" in got3,
            got3,
        )
        got4 = engine.generate_hint(
            S.ScaffoldingLevel.FULL, {"key_concepts": ["x"]}, assessment="practice"
        )
        check(
            "FULL без full_solution деградирует",
            "Полное решение недоступно" in got4,
            got4[:160],
        )
        check(
            "FULL без решения не выдаёт выдуманный текст",
            "решение" not in got4.lower() or "недоступно" in got4.lower(),
            got4[:160],
        )
        check(
            "FULL без решения не содержит конструкцию шаблона решения",
            "Вот полное решение с объяснением" not in got4,
            got4[:200],
        )
    finally:
        pass


def test_full_without_solution_does_not_invent():
    engine = S.ScaffoldingEngine()
    text = engine.generate_hint(
        S.ScaffoldingLevel.FULL,
        {"solution_structure": ["a", "b"]},
        assessment="practice",
    )
    check("показана структура вместо решения", "a" in text and "b" in text, text[:200])
    check("сказано, что полное решение недоступно", "недоступно" in text, text[:200])


def test_ceiling_for_matches_tutoring():
    for assessment in ("graded", "unknown", "practice", "optional", None, ""):
        expected = T.ASSESSMENT_CEILING.get(
            assessment or "unknown", T.ASSESSMENT_CEILING["unknown"]
        )
        got = S.ceiling_for(assessment)
        check(
            "ceiling_for(%r) == tutoring.ASSESSMENT_CEILING" % (assessment,),
            got == expected,
            "%s != %s" % (got, expected),
        )
    check(
        "ceiling_for экспортирует ту же таблицу",
        S.ASSESSMENT_CEILING is T.ASSESSMENT_CEILING,
    )


def test_level_assistance_mapping():
    check("NONE не соответствует помощи", S.level_for_assistance(0) is None)
    check("ATTENTION -> HINT", S.level_for_assistance(1) == "HINT")
    check("RECALL -> HINT", S.level_for_assistance(2) == "HINT")
    check("STRUCTURE -> EXAMPLE", S.level_for_assistance(3) == "EXAMPLE")
    check("FULL -> SOLUTION", S.level_for_assistance(4) == "SOLUTION")


def test_state_round_trip():
    engine = S.ScaffoldingEngine(level=S.ScaffoldingLevel.STRUCTURE)
    state = engine.to_state()
    check(
        "state несёт число и имя",
        state["scaffolding_level"] == 3
        and state["scaffolding_level_name"] == "STRUCTURE",
        str(state),
    )
    restored = S.ScaffoldingEngine.from_state(state)
    check(
        "from_state восстанавливает уровень",
        restored.level == S.ScaffoldingLevel.STRUCTURE,
    )


def test_language_guard():
    engine = S.ScaffoldingEngine()
    expect_error(
        "английский язык подсказки отклонён",
        lambda: engine.generate_hint(1, {}, language="en", assessment="practice"),
        "LANGUAGE_UNSUPPORTED",
    )


def test_unsupported_context_types_do_not_raise():
    engine = S.ScaffoldingEngine()
    got = engine.generate_hint(
        S.ScaffoldingLevel.ATTENTION, None, assessment="practice"
    )
    check("problem_context=None не исключение", isinstance(got, str) and got)
    got2 = engine.generate_hint(
        S.ScaffoldingLevel.ATTENTION, ["неправильный тип"], assessment="practice"
    )
    check("problem_context-список не исключение", isinstance(got2, str) and got2)


def main():
    tests = [
        test_failure_ladder,
        test_challenge_tolerance_shifts_speed,
        test_monotonic_within_cycle,
        test_fade_scaffolding,
        test_full_hint_refused_for_graded_and_unknown,
        test_generate_hint_degrades_without_context,
        test_full_without_solution_does_not_invent,
        test_ceiling_for_matches_tutoring,
        test_level_assistance_mapping,
        test_state_round_trip,
        test_language_guard,
        test_unsupported_context_types_do_not_raise,
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
