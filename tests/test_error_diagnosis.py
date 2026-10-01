#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for error diagnosis: classify what kind of error an attempt contains.

The load-bearing property is negative: **the attempt is text and is never
executed.** A diagnostic engine that `exec`ed learner code would be an
arbitrary-code surface aimed at the student's own work, so an attempt carrying
a `__import__` payload must come back as a diagnosis with no side effects.

The rest is the priority ladder: CARELESS → MISSING_PREREQ → CONCEPTUAL →
OVERGENERALIZATION → PROCEDURAL → INCOMPLETE, with empty/None inputs returning
INCOMPLETE at confidence 0.0 rather than raising, and identical inputs producing
identical diagnoses.

Run:
    python3 tests/test_error_diagnosis.py
    python3 -m pytest tests/test_error_diagnosis.py -q
"""

from __future__ import annotations

import os
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

from botai_core import error_diagnosis as E  # noqa: E402

_passed = 0
_failures: list[str] = []

REFERENCE = "def add(a, b):\n    return a + b\n"

# A long reference so a single-character slip still lands above the 0.96
# similarity where CARELESS confidence crosses 0.8. The module documents the
# confidence as *scaling with similarity* (0.6 + (sim - 0.92) * 4), so a short
# reference correctly reports ~0.77 — the test pins the documented shape, not a
# fixed number.
LONG_REFERENCE = (
    "def add(a, b):\n"
    "    total = 0\n"
    "    for value in (a, b):\n"
    "        total = total + value\n"
    "    return total\n"
)


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def engine(**kwargs):
    return E.ErrorDiagnosticEngine(**kwargs)


def test_careless_mistake():
    """A one-character difference is a slip, not a misunderstanding."""
    # + -> - on the only differing line of a long reference.
    attempt = LONG_REFERENCE.replace("total = total + value", "total = total - value")
    check("попытка действительно отличается на один знак", attempt != LONG_REFERENCE)
    diagnosis = engine().diagnose(attempt, LONG_REFERENCE, {}, {})
    check(
        "одна опечатка -> CARELESS",
        diagnosis.error_type == E.ErrorType.CARELESS,
        diagnosis.error_type.value,
    )
    check(
        "уверенность CARELESS > 0.8 при высоком сходстве",
        diagnosis.confidence > 0.8,
        str(diagnosis.confidence),
    )
    check(
        "уверенность помечена как high",
        E.classify_confidence(diagnosis.confidence) == "high",
        E.classify_confidence(diagnosis.confidence),
    )
    check("указано вмешательство", bool(diagnosis.suggested_intervention))

    # Short reference: same type, confidence scales down with similarity.
    short = engine().diagnose("def add(a, b):\n    return a - b\n", REFERENCE, {}, {})
    check("короткий эталон: тот же тип", short.error_type == E.ErrorType.CARELESS)
    check(
        "уверенность растёт с подобием",
        diagnosis.confidence > short.confidence,
        "%s <= %s" % (diagnosis.confidence, short.confidence),
    )


def test_missing_prerequisite():
    context = {
        "main_concept": "index",
        "key_concepts": ["index", "staging"],
        "prerequisites": ["git-basics"],
    }
    history = {"mastered_objectives": []}
    diagnosis = engine().diagnose(
        "index = 5; add file.txt", REFERENCE, context, history
    )
    check(
        "неосвоенная предпосылка -> MISSING_PREREQ",
        diagnosis.error_type == E.ErrorType.MISSING_PREREQ,
        diagnosis.error_type.value,
    )
    check(
        "предпосылка названа в affected_concept",
        diagnosis.affected_concept == "git-basics",
        diagnosis.affected_concept,
    )
    check("уверенность высокая", diagnosis.confidence >= 0.8, str(diagnosis.confidence))

    # A mastered prerequisite removes that branch.
    ok_history = {"mastered_objectives": ["git-basics"]}
    second = engine().diagnose(
        "index = 5; add file.txt", REFERENCE, context, ok_history
    )
    check(
        "освоенная предпосылка не вызывает MISSING_PREREQ",
        second.error_type != E.ErrorType.MISSING_PREREQ,
        second.error_type.value,
    )


def test_conceptual_repeating_signature():
    past = "index the field first then query the table"
    history = {
        "mastered_objectives": ["git-basics"],
        "errors": [
            {"text": past, "concept": "indexing"},
            {"text": past, "concept": "indexing"},
        ],
    }
    context = {"main_concept": "indexing", "key_concepts": ["index", "table"]}
    diagnosis = engine().diagnose(past, REFERENCE, context, history)
    check(
        "повторяющаяся сигнатура -> CONCEPTUAL",
        diagnosis.error_type == E.ErrorType.CONCEPTUAL,
        diagnosis.error_type.value,
    )
    check(
        "уверенность повтора высокая",
        diagnosis.confidence >= 0.8,
        str(diagnosis.confidence),
    )

    # A single occurrence of a signature is not recurrence.
    once = {
        "mastered_objectives": ["git-basics"],
        "errors": [{"text": past, "concept": "indexing"}],
    }
    single = engine().diagnose(past, REFERENCE, context, once)
    check(
        "единичная ошибка не даёт CONCEPTUAL",
        single.error_type != E.ErrorType.CONCEPTUAL,
        single.error_type.value,
    )


def test_procedural_right_shape_wrong_execution():
    # `index` is a prerequisite and `staging` is the main concept: the attempt
    # shares both concept tokens with the task (right structure), so the
    # overgeneralization rung is skipped and PROCEDURAL is the call.
    context = {
        "main_concept": "staging",
        "key_concepts": ["staging", "index"],
        "prerequisites": ["index"],
    }
    attempt = "git add index and staging then commit"
    history = {"mastered_objectives": ["index"]}
    diagnosis = engine().diagnose(attempt, REFERENCE, context, history)
    check(
        "верная структура, неверный результат -> PROCEDURAL",
        diagnosis.error_type == E.ErrorType.PROCEDURAL,
        diagnosis.error_type.value,
    )
    check(
        "уверенность PROCEDURAL в средней полосе",
        E.classify_confidence(diagnosis.confidence) == "medium",
        "%s (%s)" % (diagnosis.confidence, E.classify_confidence(diagnosis.confidence)),
    )


def test_fallback_incomplete():
    diagnosis = engine().diagnose("совершенно посторонний текст", REFERENCE, {}, {})
    check(
        "неразличимый случай -> INCOMPLETE",
        diagnosis.error_type == E.ErrorType.INCOMPLETE,
        diagnosis.error_type.value,
    )
    check(
        "уверенность фолбэка 0.6",
        abs(diagnosis.confidence - 0.6) < 1e-9,
        str(diagnosis.confidence),
    )


def test_empty_and_none_inputs():
    for attempt, solution, label in (
        ("", REFERENCE, "пустая попытка"),
        (None, REFERENCE, "None-попытка"),
        ("   ", REFERENCE, "пробельная попытка"),
        ("текст", "", "пустой эталон"),
        ("текст", None, "None-эталон"),
    ):
        diagnosis = engine().diagnose(attempt, solution, {}, {})
        check(
            "%s -> INCOMPLETE" % label,
            diagnosis.error_type == E.ErrorType.INCOMPLETE,
            diagnosis.error_type.value,
        )
        check(
            "%s -> уверенность 0.0" % label,
            diagnosis.confidence == 0.0,
            str(diagnosis.confidence),
        )
        check("%s -> исключения нет" % label, isinstance(diagnosis.evidence, str))
        check(
            "%s -> полоса low" % label,
            E.classify_confidence(diagnosis.confidence) == "low",
        )


def test_attempt_is_never_executed():
    """A payload in the attempt is TEXT. Nothing runs, nothing is created."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        canary = root / "pwned.txt"
        payload = (
            "print('x')\n__import__('pathlib').Path(%r).write_text('pwned')\n"
            % str(canary)
        )
        diagnosis = engine().diagnose(payload, REFERENCE, {}, {})
        check(
            "диагноз возвращён для payload-текста", isinstance(diagnosis.evidence, str)
        )
        check("побочный файл не создан", not canary.exists())
        check(
            "payload не исполнён (нет AttributeError/TypeError от запуска)",
            diagnosis.error_type in tuple(E.ErrorType),
            diagnosis.error_type.value,
        )

    # And the pure-string form from the module's own docs.
    hostile = "__import__('os').system('echo pwned')"
    got = engine().diagnose(hostile, REFERENCE, {}, {})
    check("os.system-строка обработана как текст", isinstance(got.evidence, str))
    check("нет исключения при обработке hostile-строки", got.confidence >= 0.0)


def test_determinism():
    args = (
        "index = 5; add file.txt",
        REFERENCE,
        {
            "main_concept": "index",
            "key_concepts": ["index", "staging"],
            "prerequisites": ["git-basics"],
        },
        {"mastered_objectives": []},
    )
    first = engine().diagnose(*args).to_document()
    second = engine().diagnose(*args).to_document()
    check(
        "повторный вызов даёт тот же диагноз",
        first == second,
        "%s != %s" % (first, second),
    )


def test_overgeneralization_foreign_concept():
    context = {
        "main_concept": "staging",
        "key_concepts": ["staging", "rebase history"],
        "prerequisites": ["git-basics"],
    }
    history = {"mastered_objectives": ["git-basics"]}
    attempt = "rebase the git history then stage the file"
    diagnosis = engine().diagnose(attempt, REFERENCE, context, history)
    check(
        "чужое правило курса -> OVERGENERALIZATION",
        diagnosis.error_type == E.ErrorType.OVERGENERALIZATION,
        diagnosis.error_type.value,
    )
    check(
        "названа чужая концепция",
        diagnosis.affected_concept == "rebase history",
        diagnosis.affected_concept,
    )


def test_document_shape():
    diagnosis = engine().diagnose(
        "def add(a, b):\n    return a - b\n", REFERENCE, {}, {}
    )
    document = diagnosis.to_document()
    for key in (
        "error_type",
        "confidence",
        "evidence",
        "affected_concept",
        "suggested_intervention",
        "confidence_band",
    ):
        check("документ содержит %s" % key, key in document, str(sorted(document)))
    check(
        "error_type — значение enum, не сам enum",
        document["error_type"] == E.ErrorType.CARELESS.value,
        document["error_type"],
    )


def test_confidence_bands():
    check("0.85 -> high", E.classify_confidence(0.85) == "high")
    check("0.79 -> medium", E.classify_confidence(0.79) == "medium")
    check("0.6 -> medium", E.classify_confidence(0.6) == "medium")
    check("0.59 -> low", E.classify_confidence(0.59) == "low")
    check("мусор -> low", E.classify_confidence("не число") == "low")
    check("None -> low", E.classify_confidence(None) == "low")


def test_repeating_patterns_helper():
    patterns = E._find_repeating_patterns(
        [
            "alpha beta",
            "alpha beta",
            "gamma",
        ]
    )
    check("повтор найден", len(patterns) == 1, str(patterns))
    check("счётчик повтора 2", patterns[0]["count"] == 2, str(patterns[0]))
    check(
        "порядок детерминирован",
        E._find_repeating_patterns(["gamma", "alpha beta", "alpha beta"]) == patterns,
    )


def main():
    tests = [
        test_careless_mistake,
        test_missing_prerequisite,
        test_conceptual_repeating_signature,
        test_procedural_right_shape_wrong_execution,
        test_fallback_incomplete,
        test_empty_and_none_inputs,
        test_attempt_is_never_executed,
        test_determinism,
        test_overgeneralization_foreign_concept,
        test_document_shape,
        test_confidence_bands,
        test_repeating_patterns_helper,
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
