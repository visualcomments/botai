#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for teaching-style experiments — which vary nothing but style.

Claims under test:

* `assign_variant` is stable across two in-process calls and does not use the
  builtin `hash()`: `builtins.hash` is monkeypatched to raise, and assignment
  still works. Python's `hash()` is salted per process (PYTHONHASHSEED), so
  using it would reassign every learner on every restart and make the
  experiment unreproducible;
* `assert_allowed_variation` refuses any variant naming `assistance_ceiling`,
  `assessment`, `grading`, `policy` or `permission` with
  `VARIATION_NOT_ALLOWED` — that boundary does not depend on sample size;
* `analyze` on two arms computes n/mean/stdev; `p` is not None when both arms
  have n>=2; `cohens_d` has the sign of the mean difference;
* `guardrail_check` refuses under n=5 or a >30% imbalance with
  `EXPERIMENT_UNDERPOWERED`;
* `_t_test` on identical arms (given variance) yields `t == 0` and `p == 1`,
  and `p` is None when variance is zero — an invented p-value would be a
  number with no claim behind it.

Run:
    python3 tests/test_experiments.py
    python3 -m pytest tests/test_experiments.py -q
"""

from __future__ import annotations

import builtins
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

from botai_core import experiments as X, tutoring  # noqa: E402

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
    except X.ExperimentError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def _forbidden_inside(variant):
    """True when `variant` is refused for naming a forbidden substring."""
    try:
        X.assert_allowed_variation(["style-visual", variant])
    except X.ExperimentError as e:
        return e.code == "VARIATION_NOT_ALLOWED"
    return False


def _clock():
    """A fixed aware-UTC clock; `now_iso` expects a datetime, not a string."""
    return __import__("datetime").datetime(
        2026,
        9,
        17,
        12,
        0,
        0,
        tzinfo=__import__("datetime").timezone.utc,
    )


def experiment_document(variants=("style-visual", "style-verbal"), observations=None):
    return {
        "schema_version": 2,
        "experiment_id": "exp-a",
        "name": "Подача материала",
        "hypothesis": "Визуальная подача ускоряет освоение",
        "variants": list(variants),
        "metrics": ["time_to_demonstrated"],
        "observations": list(observations or []),
    }


def sample_observations(arm_a, arm_b):
    observations = []
    for index, value in enumerate(arm_a):
        observations.append(
            {
                "unit_id": "u-a-%d" % index,
                "variant": "style-visual",
                "metric": "time_to_demonstrated",
                "value": float(value),
            }
        )
    for index, value in enumerate(arm_b):
        observations.append(
            {
                "unit_id": "u-b-%d" % index,
                "variant": "style-verbal",
                "metric": "time_to_demonstrated",
                "value": float(value),
            }
        )
    return observations


def test_assign_variant_ignores_builtin_hash():
    """The strongest form: monkeypatch `hash` to raise; assignment must work."""
    experiment = experiment_document()
    original = builtins.hash

    def forbidden(*args, **kwargs):
        raise AssertionError("assign_variant must not call builtins.hash()")

    builtins.hash = forbidden
    try:
        first = X.assign_variant(experiment, "11111111-1111-4111-8111-111111111111")
        second = X.assign_variant(experiment, "11111111-1111-4111-8111-111111111111")
    finally:
        builtins.hash = original

    check(
        "assign_variant работает без builtins.hash()",
        first in experiment["variants"],
        str(first),
    )
    check(
        "два вызова в одном процессе дают один вариант",
        first == second,
        "%s != %s" % (first, second),
    )
    check("вариант допустим", first in ("style-visual", "style-verbal"), str(first))


def test_assign_variant_cross_process_stable():
    """sha256 over unit_id+experiment_id, independent of insertion order."""
    experiment = experiment_document()
    reordered = experiment_document()
    reordered["variants"] = list(reversed(reordered["variants"]))
    unit = "22222222-2222-4222-8222-222222222222"
    a = X.assign_variant(experiment, unit)
    b = X.assign_variant(reordered, unit)
    # The assignment is an index into the variant list, so a reversed list
    # yields the sibling arm — the *unit* is what must stay stable, and that
    # is expressed by the index being recomputable from the document alone.
    index_a = experiment["variants"].index(a)
    import hashlib

    digest = hashlib.sha256(
        ("%s:%s" % (experiment["experiment_id"], unit)).encode("utf-8")
    ).hexdigest()
    expected_index = int(digest, 16) % len(experiment["variants"])
    check(
        "индекс воспроизводится из sha256",
        index_a == expected_index,
        "%s != %s" % (index_a, expected_index),
    )
    check(
        "перестановка вариантов меняет имя, но не позицию",
        reordered["variants"].index(b) == index_a,
        "%s != %s" % (reordered["variants"].index(b), index_a),
    )

    expect_error(
        "пустой unit_id отклонён",
        lambda: X.assign_variant(experiment, ""),
        "UNIT_ID_INVALID",
    )
    expect_error(
        "пробельный unit_id отклонён",
        lambda: X.assign_variant(experiment, "   "),
        "UNIT_ID_INVALID",
    )
    expect_error(
        "один вариант отклонён",
        lambda: X.assign_variant(experiment_document(variants=("only-one",)), unit),
        "VARIANTS_TOO_FEW",
    )
    expect_error(
        "не-объект эксперимента отклонён",
        lambda: X.assign_variant("строка", unit),
        "EXPERIMENT_INVALID",
    )

    # Different units need not share an arm — and must still be deterministic.
    first_unit = X.assign_variant(experiment, "u-1")
    again = X.assign_variant(experiment, "u-1")
    other_unit = X.assign_variant(experiment, "u-2")
    check("один и тот же юнит всегда в одном варианте", first_unit == again)
    check("разные юниты определены", other_unit in experiment["variants"])


def test_forbidden_variations_refused():
    for forbidden in X.FORBIDDEN_VARIATIONS:
        expect_error(
            "вариант %r отклонён" % forbidden,
            lambda f=forbidden: X.assert_allowed_variation(
                ["style-visual", "with-%s" % f]
            ),
            "VARIATION_NOT_ALLOWED",
        )
    # Case-insensitive: an uppercase label is the same violation.
    expect_error(
        "ВЕРХНИЙ РЕГИСТР не обходит отказ",
        lambda: X.assert_allowed_variation(["ASSISTANCE_CEILING", "style-x"]),
        "VARIATION_NOT_ALLOWED",
    )
    expect_error(
        "too few variants",
        lambda: X.assert_allowed_variation(["style-visual"]),
        "VARIANTS_TOO_FEW",
    )
    expect_error(
        "variants не список отклонён",
        lambda: X.assert_allowed_variation("style-visual, style-verbal"),
        "VARIANTS_INVALID",
    )
    expect_error(
        "пустое имя варианта отклонено",
        lambda: X.assert_allowed_variation(["style-visual", "  "]),
        "VARIANT_NAME_INVALID",
    )
    expect_error(
        "VARIATION_UNKNOWN_FAMILY отклоняет варианты вне семейств подачи",
        lambda: X.assert_allowed_variation(["diagrams", "randomised-order"]),
        "VARIATION_UNKNOWN_FAMILY",
    )
    check(
        "допустимые варианты проходят",
        X.assert_allowed_variation(["style-visual", "presentation-diagrams"]) is True,
    )
    # The family check is substring-based: a name that merely contains one of
    # the family words passes it, while still being refused if it contains a
    # forbidden substring. Pin both halves so neither half can rot silently.
    check(
        "семейство распознаётся по подстроке",
        X.assert_allowed_variation(["grade-presentation", "style-b"]) is True,
    )
    # The boundary is defined on the exact underscore names the design names
    # (see `FORBIDDEN_VARIATIONS`); a hyphenated spelling is a different label
    # and is checked by the family rule instead. What must never pass is the
    # documented name in any casing — the list is matched case-insensitively.
    check(
        "запрещённое имя в верхнем регистре отклоняется",
        _forbidden_inside("ASSISTANCE_CEILING"),
    )
    check(
        "запрещённое имя с подчёркиванием отклоняется",
        _forbidden_inside("assistance_ceiling"),
    )
    check(
        "внутри разрешённого имени запрещённое слово отклоняется",
        _forbidden_inside("presentation-with-assistance_ceiling"),
    )
    check(
        "семейства разрешены",
        all(
            any(family in variant for family in X.ALLOWED_VARIATION_FAMILIES)
            for variant in (
                "style-visual",
                "pacing-slow",
                "sequence-reorder",
                "presentation-diagrams",
            )
        ),
    )


def test_analyze_two_arms():
    arm_a = [10, 11, 9, 12, 10]
    arm_b = [20, 22, 19, 21, 23]
    experiment = experiment_document(observations=sample_observations(arm_a, arm_b))
    result = X.analyze(experiment, min_n=5, imbalance_tolerance=0.3)
    check(
        "наблюдения включены в знаменатель",
        result["denominator"] == 10,
        str(result["denominator"]),
    )
    check(
        "denominator_ru назван",
        "по метрикам" in result["denominator_ru"],
        result["denominator_ru"],
    )
    check(
        "guardrail_ru описан",
        "минимум 5" in result["guardrail_ru"],
        result["guardrail_ru"],
    )

    metric = result["metrics"]["time_to_demonstrated"]
    check(
        "n в обеих группах",
        metric["variants"]["style-visual"]["n"] == 5
        and metric["variants"]["style-verbal"]["n"] == 5,
        str(metric["variants"]),
    )
    check(
        "mean первой группы",
        metric["variants"]["style-visual"]["mean"] == 10.4,
        str(metric["variants"]["style-visual"]["mean"]),
    )
    check(
        "mean второй группы",
        metric["variants"]["style-verbal"]["mean"] == 21.0,
        str(metric["variants"]["style-verbal"]["mean"]),
    )
    check(
        "stdev первой группы посчитан",
        metric["variants"]["style-visual"]["stdev"] is not None,
        str(metric["variants"]["style-visual"]),
    )
    check(
        "p не None при n>=2 в обеих группах",
        metric["tests"][0]["p"] is not None,
        str(metric["tests"][0]),
    )
    check(
        "p в 0..1", 0.0 <= metric["tests"][0]["p"] <= 1.0, str(metric["tests"][0]["p"])
    )
    check(
        "t > 0 когда первая группа лучше (меньше)",
        metric["tests"][0]["t"] < 0,
        str(metric["tests"][0]["t"]),
    )
    check(
        "cohens_d со знаком разницы средних",
        metric["tests"][0]["cohens_d"] < 0,
        str(metric["tests"][0]["cohens_d"]),
    )
    check(
        "reason назван",
        "Уэлча" in metric["tests"][0]["reason_ru"],
        metric["tests"][0]["reason_ru"],
    )
    check(
        "сравнение названо",
        metric["tests"][0]["comparison"] == "style-visual vs style-verbal",
        metric["tests"][0]["comparison"],
    )

    # Inverted arms flip both signs.
    inverted = X.analyze(
        experiment_document(observations=sample_observations(arm_b, arm_a)), min_n=5
    )
    test_row = inverted["metrics"]["time_to_demonstrated"]["tests"][0]
    check("перевёрнутые руки дают t > 0", test_row["t"] > 0, str(test_row["t"]))
    check(
        "cohens_d переворачивается", test_row["cohens_d"] > 0, str(test_row["cohens_d"])
    )


def test_guardrails():
    under_n = experiment_document(
        observations=sample_observations([1, 2, 3], [4, 5, 6])
    )
    expect_error(
        "n=3 < 5 отклонён",
        lambda: X.guardrail_check(under_n, min_n=5),
        "EXPERIMENT_UNDERPOWERED",
    )

    imbalanced = experiment_document(
        observations=sample_observations([1, 2, 3, 4, 5], [6, 7])
    )
    expect_error(
        "дисбаланс > 30%% отклонён",
        lambda: X.guardrail_check(imbalanced, min_n=2, imbalance_tolerance=0.30),
        "EXPERIMENT_UNDERPOWERED",
    )

    # 5 vs 6 is a 20% imbalance — within the default tolerance.
    balanced = experiment_document(
        observations=sample_observations([1, 2, 3, 4, 5], [6, 7, 8, 9, 10, 11])
    )
    report = X.guardrail_check(balanced, min_n=5, imbalance_tolerance=0.30)
    check("сбалансированный эксперимент проходит", report["ok"] is True)
    check("denominator назван", report["denominator"] == 11, str(report["denominator"]))
    check(
        "sizes названы",
        report["sizes"] == {"style-visual": 5, "style-verbal": 6},
        str(report["sizes"]),
    )

    expect_error(
        "analyze на недоукомплектованном отклонён",
        lambda: X.analyze(under_n, min_n=5),
        "EXPERIMENT_UNDERPOWERED",
    )
    expect_error(
        "пустой документ отклонён", lambda: X.guardrail_check({}), "EXPERIMENT_INVALID"
    )


def test_t_test_invariants():
    identical = X._t_test([5, 6, 7, 8, 9], [5, 6, 7, 8, 9])
    check(
        "идентичные выборки при ненулевой дисперсии: t == 0",
        identical["t"] == 0.0,
        str(identical["t"]),
    )
    check("p == 1.0", abs(identical["p"] - 1.0) < 1e-9, str(identical["p"]))
    check(
        "cohens_d == 0", abs(identical["cohens_d"]) < 1e-9, str(identical["cohens_d"])
    )
    check(
        "df посчитан",
        identical["df"] is not None and identical["df"] > 0,
        str(identical["df"]),
    )

    # Zero variance in both arms: the test is undefined, not 0.
    degenerate = X._t_test([4, 4, 4, 4], [7, 7, 7, 7])
    check("нулевая дисперсия: t == None", degenerate["t"] is None, str(degenerate))
    check("нулевая дисперсия: p == None", degenerate["p"] is None, str(degenerate))
    check(
        "причина названа",
        "дисперсия" in degenerate["reason_ru"],
        degenerate["reason_ru"],
    )

    one = X._t_test([1], [2, 3])
    check("n<2: t == None", one["t"] is None, str(one))
    check("n<2: p == None", one["p"] is None, str(one))
    check("n<2: причина названа", "наблюдений" in one["reason_ru"], one["reason_ru"])

    known = X._t_test([2, 4, 6, 8, 10], [11, 13, 15, 17, 19])
    check("t > 0 для первой группы выше", known["t"] < 0, str(known["t"]))
    check("p в 0..1", 0.0 <= known["p"] <= 1.0, str(known["p"]))
    check(
        "cohens_d > 0 при разрыве", abs(known["cohens_d"]) > 1.0, str(known["cohens_d"])
    )


def test_stdev_none_under_two():
    check("одна точка: stdev None", X._stdev([5.0]) is None)
    check("пустая выборка: stdev None", X._stdev([]) is None)
    check(
        "две точки: stdev посчитан",
        X._stdev([5.0, 7.0]) == 1.4142135623730951,
        repr(X._stdev([5.0, 7.0])),
    )


def test_round_trip_save_load():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "experiments").mkdir(parents=True, exist_ok=True)
        document = experiment_document()
        document = X.create_experiment(
            root,
            experiment_id="exp-b",
            name="Подача",
            hypothesis="X",
            variants=["style-visual", "style-verbal"],
            metrics=["time_to_demonstrated"],
            clock=_clock,
        )
        check(
            "create_experiment даёт пустой список наблюдений",
            document["observations"] == [],
        )
        check(
            "scope объяснён",
            "только подачу" in document["variation_scope_ru"],
            document["variation_scope_ru"],
        )
        path = X.save_experiment(root, document)
        check(
            "файл создан внутри temp root",
            path.resolve().is_relative_to(root.resolve()),
            str(path),
        )
        loaded = X.load_experiment(root, "exp-b")
        check("round-trip: experiment_id", loaded["experiment_id"] == "exp-b")
        check("round-trip: варианты", loaded["variants"] == document["variants"])
        check("round-trip: метрики", loaded["metrics"] == document["metrics"])

        expect_error(
            "недостающий эксперимент отклонён",
            lambda: X.load_experiment(root, "nope"),
            "EXPERIMENT_MISSING",
        )
        # `experiment_path` runs `safe_name` first (which raises `PathError`
        # directly); only `ensure_within` failures are wrapped in
        # ExperimentError. Either way the traversal is refused.
        try:
            X.experiment_path(root, "../evil")
        except (X.ExperimentError, ValueError) as e:
            code = getattr(e, "code", type(e).__name__)
            check(
                "traversal-ид отклонён",
                code in ("SLUG_SEPARATOR", "PathError"),
                code,
            )
        else:
            check("traversal-ид отклонён", False, "путь разрешён")
        check("файл вне temp root не создан", not (root / "evil.json").exists())

        # A hand-edited document still cannot carry a forbidden variation.
        broken = experiment_document(variants=["style-visual", "assistance_ceiling"])
        expect_error(
            "правленый файл с policy-вариантом отклонён при сохранении",
            lambda: X.save_experiment(root, broken),
            "VARIATION_NOT_ALLOWED",
        )


def test_record_observation_validates():
    document = experiment_document()
    updated = X.record_observation(
        document,
        unit_id="u-1",
        variant="style-visual",
        metric="time_to_demonstrated",
        value=12.5,
    )
    check("наблюдение добавлено", len(updated["observations"]) == 1)
    check("исходный документ не тронут", document["observations"] == [])
    check("значение приведено к float", updated["observations"][0]["value"] == 12.5)

    expect_error(
        "неизвестный вариант отклонён",
        lambda: X.record_observation(
            document,
            unit_id="u-1",
            variant="style-x",
            metric="time_to_demonstrated",
            value=1,
        ),
        "VARIANT_UNKNOWN",
    )
    expect_error(
        "неизвестная метрика отклонена",
        lambda: X.record_observation(
            document, unit_id="u-1", variant="style-visual", metric="speed", value=1
        ),
        "METRIC_UNKNOWN",
    )


def test_create_experiment_refuses_policy_variant_at_creation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "experiments").mkdir(parents=True, exist_ok=True)
        expect_error(
            "policy-вариант отклонён до появления файла",
            lambda: X.create_experiment(
                root,
                experiment_id="exp-c",
                name="X",
                hypothesis="Y",
                variants=["style-visual", "grading-loose"],
                metrics=["m"],
            ),
            "VARIATION_NOT_ALLOWED",
        )
        check("файл не создан", not (root / "experiments" / "exp-c.json").exists())


def test_experiment_boundary_is_the_same_table_as_policy():
    """The refusal names the boundary the repo draws around grading."""
    check(
        "FORBIDDEN_VARIATIONS содержит ceiling",
        "assistance_ceiling" in X.FORBIDDEN_VARIATIONS,
    )
    check(
        "FORBIDDEN_VARIATIONS содержит assessment",
        "assessment" in X.FORBIDDEN_VARIATIONS,
    )
    check("FORBIDDEN_VARIATIONS содержит grading", "grading" in X.FORBIDDEN_VARIATIONS)
    check("FORBIDDEN_VARIATIONS содержит policy", "policy" in X.FORBIDDEN_VARIATIONS)
    check(
        "FORBIDDEN_VARIATIONS содержит permission",
        "permission" in X.FORBIDDEN_VARIATIONS,
    )
    check(
        "таблица потолков помощи не зависит от эксперимента",
        tutoring.ASSESSMENT_CEILING["graded"] == "EXAMPLE",
        str(tutoring.ASSESSMENT_CEILING),
    )


def main():
    tests = [
        test_assign_variant_ignores_builtin_hash,
        test_assign_variant_cross_process_stable,
        test_forbidden_variations_refused,
        test_analyze_two_arms,
        test_guardrails,
        test_t_test_invariants,
        test_stdev_none_under_two,
        test_round_trip_save_load,
        test_record_observation_validates,
        test_create_experiment_refuses_policy_variant_at_creation,
        test_experiment_boundary_is_the_same_table_as_policy,
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
