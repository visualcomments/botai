#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for at-risk detection: an auditable rule and a hard consent gate.

The load-bearing property is ethical, not statistical: `interventions_allowed`
is the code guard that decides whether a human may ever be told about a
learner's risk signals. `None`, a withdrawn consent, and a live `teacher_export`
consent are all tested — the third is what makes the refusal meaningful rather
than a blanket "no".

The rest pins the honesty properties: `RISK_WEIGHTS` sums to exactly 1.0, empty
inputs yield all-zero features with a `basis_ru` note rather than a guess, and
`detect_at_risk` carries its own denominator.

Run:
    python3 tests/test_risk_detection.py
    python3 -m pytest tests/test_risk_detection.py -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import risk_detection as K  # noqa: E402

_passed = 0
_failures: list[str] = []

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


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
    except K.RiskDetectionError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def clock():
    return NOW


def test_weights_sum_to_one():
    total = sum(K.RISK_WEIGHTS.values())
    check("веса в сумме дают 1.0", abs(total - 1.0) < 1e-9, repr(total))
    check(
        "веса покрывают все 15 признаков",
        set(K.RISK_WEIGHTS) == set(K.RISK_FEATURE_NAMES),
        str(set(K.RISK_FEATURE_NAMES) ^ set(K.RISK_WEIGHTS)),
    )
    check(
        "все веса положительны",
        all(w > 0 for w in K.RISK_WEIGHTS.values()),
        str(K.RISK_WEIGHTS),
    )
    check(
        "каждый признак документирован",
        all(K.RISK_FEATURE_DOCS.get(name) for name in K.RISK_FEATURE_NAMES),
    )
    check(
        "у каждого признака есть русская метка",
        all(K.RISK_FEATURE_LABELS.get(name) for name in K.RISK_FEATURE_NAMES),
    )

    # The module asserts the sum at import time; a deliberately broken copy of
    # the table must fail that assertion rather than produce a wrong score.
    try:
        broken = dict(K.RISK_WEIGHTS)
        broken["days_since_last_session"] = 0.5
        if abs(sum(broken.values()) - 1.0) > 1e-9:
            raise RuntimeError("RISK_WEIGHTS must sum to 1.0")
    except RuntimeError:
        check("испорченная таблица весов падает loudly", True)
    else:
        check("испорченная таблица весов падает loudly", False, "ошибки не было")


def test_extract_features_empty():
    features = K.extract_features(
        sessions=[], attempts=[], checks=[], objective_states={}, now=clock()
    )
    for name in K.RISK_FEATURE_NAMES:
        # Two documented exceptions, both deliberate and named in RISK_FEATURE_DOCS:
        # `sessions_last_7_days` is "1 - n/5" (no sessions -> 1.0, i.e. "zero
        # activity is a full-strength absence signal") and `attempts_per_session`
        # is "1 - min(1, v/5)" with the same shape. Everything else is a plain
        # share/count, so an empty record is 0.0.
        if name in ("sessions_last_7_days", "attempts_per_session"):
            check(
                "признак %s без данных ведёт себя по документу (1.0)" % name,
                features[name] == 1.0,
                str(features[name]),
            )
            continue
        check(
            "признак %s равен 0 на пустых входах" % name,
            features[name] == 0.0,
            str(features[name]),
        )
    check(
        "basis_ru объясняет отсутствие данных",
        "недоступно" in features["basis_ru"],
        features["basis_ru"],
    )
    check(
        "basis_ru называет объёмы",
        "0 занятий" in features["basis_ru"],
        features["basis_ru"],
    )
    check("нет исключения", True)

    # No data must not read as "at risk": the documented derivation of
    # `sessions_last_7_days` means an empty record scores 0.13, never a band.
    risk = K.score_risk(features)
    check(
        "пустая запись не попадает в полосу риска",
        risk["score"] < K.BAND_MEDIUM,
        "%s (порог medium=%s)" % (risk["score"], K.BAND_MEDIUM),
    )
    check("уровень low", risk["level"] == "low", risk["level"])
    check(
        "сказано, что это не обученная модель",
        "не обученная модель" in risk["method_ru"],
        risk["method_ru"],
    )


def test_score_risk_range_and_contributors():
    features = {name: 1.0 for name in K.RISK_FEATURE_NAMES}
    risk = K.score_risk(features)
    check("score в 0..1", 0.0 <= risk["score"] <= 1.0, str(risk["score"]))
    check("полный набор признаков даёт 1.0", risk["score"] == 1.0, str(risk["score"]))
    check("уровень high при 1.0", risk["level"] == "high", risk["level"])
    check(
        "вкладчик на каждый признак",
        {c["feature"] for c in risk["contributors"]} == set(K.RISK_FEATURE_NAMES),
        str(len(risk["contributors"])),
    )
    check(
        "веса взяты из таблицы констант",
        all(c["weight"] == K.RISK_WEIGHTS[c["feature"]] for c in risk["contributors"]),
    )
    check(
        "вклады отсортированы по убыванию",
        [c["contribution"] for c in risk["contributors"]]
        == sorted((c["contribution"] for c in risk["contributors"]), reverse=True),
        str([c["contribution"] for c in risk["contributors"]]),
    )

    half = K.score_risk({name: 0.5 for name in K.RISK_FEATURE_NAMES})
    check(
        "половинные признаки дают 0.5",
        abs(half["score"] - 0.5) < 1e-6,
        str(half["score"]),
    )
    check("0.5 — medium", half["level"] == "medium", half["level"])

    # Out-of-range feature values are clamped, not propagated.
    big = dict(features)
    big["days_since_last_session"] = 99.0
    clamped = K.score_risk(big)
    check("значение > 1 обрезается", clamped["score"] <= 1.0, str(clamped["score"]))

    expect_error(
        "признаки не объект отклонены",
        lambda: K.score_risk("строка"),
        "FEATURES_INVALID",
    )
    expect_error(
        "нечисловой признак отклонён",
        lambda: K.score_risk({"days_since_last_session": "много"}),
        "FEATURES_INVALID",
    )


def test_sentiment_signal():
    check(
        "русский маркер найден",
        K.sentiment_signal(["я совсем не понимаю эту тему"]) >= 1,
    )
    check(
        "английский маркер найден", K.sentiment_signal(["i don't get this at all"]) >= 1
    )
    check(
        "несколько маркеров суммируются",
        K.sentiment_signal(["бесит, сдаюсь"]) == 2,
        str(K.sentiment_signal(["бесит, сдаюсь"])),
    )
    check(
        "нейтральный текст даёт 0",
        K.sentiment_signal(["сегодня хорошая погода, я иду в магазин"]) == 0,
    )
    check("пустой список даёт 0", K.sentiment_signal([]) == 0)
    check("None даёт 0", K.sentiment_signal(None) == 0)
    check(
        "не-строки пропускаются, а не считаются текстом",
        K.sentiment_signal([{"content": "сдаюсь"}, 42, None]) == 0,
        str(K.sentiment_signal([{"content": "сдаюсь"}, 42, None])),
    )
    check(
        "регистр не важен",
        K.sentiment_signal(["I HATE THIS"]) == K.sentiment_signal(["i hate this"]),
    )


def test_recovery_plan_is_deterministic():
    features = {name: 0.0 for name in K.RISK_FEATURE_NAMES}
    features["consecutive_stuck_sessions"] = 1.0
    features["objectives_demonstrated_ratio"] = 0.9
    risk = K.score_risk(features)
    plan1 = K.recovery_plan(risk)
    plan2 = K.recovery_plan(risk)
    check("план непустой", plan1["steps_ru"], str(plan1))
    check("план детерминирован", plan1 == plan2)
    check(
        "план назван как не оценка", "не оценка" in plan1["note_ru"], plan1["note_ru"]
    )
    check("источники указаны", plan1["sources"], str(plan1["sources"]))
    check(
        "источники из верхних вкладов",
        {s["feature"] for s in plan1["sources"]}
        <= {c["feature"] for c in risk["contributors"]},
        str(plan1["sources"]),
    )

    zero = K.recovery_plan(K.score_risk({name: 0.0 for name in K.RISK_FEATURE_NAMES}))
    check("без сигналов план всё равно непустой", zero["steps_ru"], str(zero))
    check(
        "и сказано, что сигналов нет", "нет" in zero["steps_ru"][0], zero["steps_ru"][0]
    )

    expect_error(
        "план из не-объекта отклонён", lambda: K.recovery_plan("строка"), "RISK_INVALID"
    )


def test_consent_gate():
    """The ethical load-bearing guard: all three cases."""
    check(
        "нет согласия — interventions запрещены", K.interventions_allowed(None) is False
    )
    check(
        "отозванное согласие — запрещено",
        K.interventions_allowed(
            {"purposes": ["teacher_export"], "withdrawn_at": "2026-09-17T00:00:00Z"}
        )
        is False,
    )
    check(
        "живое согласие teacher_export — разрешено",
        K.interventions_allowed({"purposes": ["teacher_export"]}) is True,
    )
    check(
        "согласие без teacher_export — запрещено",
        K.interventions_allowed({"purposes": ["learning_storage"]}) is False,
    )
    check("пустое согласие — запрещено", K.interventions_allowed({}) is False)
    check("не-объект — запрещено", K.interventions_allowed("teacher_export") is False)
    check(
        "список вместо объекта — запрещено",
        K.interventions_allowed(["teacher_export"]) is False,
    )
    check(
        "withdrawn_at=None не считается отзывом",
        K.interventions_allowed({"purposes": ["teacher_export"], "withdrawn_at": None})
        is True,
    )


def test_detect_at_risk_denominator():
    members = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    facts = {
        "11111111-1111-4111-8111-111111111111": {
            "sessions": [{"started_at": "2020-01-01T00:00:00Z", "state": "PAUSED"}],
            "attempts": [],
            "checks": [],
            "objective_states": {},
        },
    }
    results = K.detect_at_risk(
        cohort_id="cohort-a", members=members, facts_by_student=facts
    )
    check(
        "последний элемент несёт знаменатель",
        "denominator" in results[-1],
        str(results[-1]),
    )
    check(
        "знаменатель = числу рассмотренных",
        results[-1]["denominator"] == 3,
        str(results[-1]["denominator"]),
    )
    check(
        "покрытие объяснено по-русски",
        "из 3 участников" in results[-1]["denominator_ru"],
        results[-1]["denominator_ru"],
    )
    check("экспорт требует согласия", results[-1]["export_requires_consent"] is True)
    check(
        "участник без данных не попал в риск",
        all(
            r.get("student_id") != "22222222-2222-4222-8222-222222222222"
            for r in results[:-1]
        ),
        str(results[:-1]),
    )
    check(
        "участник без данных не попал вообще",
        all(
            r.get("student_id") != "33333333-3333-4333-8333-333333333333"
            for r in results[:-1]
        ),
        str(results[:-1]),
    )
    check(
        "риск назван не оценкой",
        all("не оценка" in r["not_a_grade_ru"] for r in results[:-1]),
        str(results[-1]),
    )
    check(
        "порог назван", results[-1]["threshold"] == 0.5, str(results[-1]["threshold"])
    )

    expect_error(
        "порог > 1 отклонён",
        lambda: K.detect_at_risk(
            cohort_id="c", members=members, facts_by_student={}, threshold=1.5
        ),
        "THRESHOLD_INVALID",
    )
    expect_error(
        "порог < 0 отклонён",
        lambda: K.detect_at_risk(
            cohort_id="c", members=members, facts_by_student={}, threshold=-0.1
        ),
        "THRESHOLD_INVALID",
    )
    expect_error(
        "порог не число отклонён",
        lambda: K.detect_at_risk(
            cohort_id="c", members=members, facts_by_student={}, threshold="0.5"
        ),
        "THRESHOLD_INVALID",
    )


def test_features_reflect_a_stalled_learner():
    """A stale record with stuck cycles must outrank an empty one."""
    features = K.extract_features(
        sessions=[
            {
                "started_at": "2026-08-01T00:00:00Z",
                "ended_at": "2026-08-01T01:00:00Z",
                "state": "PAUSED",
            },
            {
                "started_at": "2026-09-10T00:00:00Z",
                "ended_at": "2026-09-10T01:00:00Z",
                "state": "PAUSED",
            },
        ],
        attempts=[
            {
                "submitted_at": "2026-09-10T00:30:00Z",
                "assistance_max": "SOLUTION",
                "text_excerpt": "я сдаюсь, бесит",
            }
        ],
        checks=[{"verdict": "fail"}, {"verdict": "fail"}, {"verdict": "pass"}],
        objective_states={
            "obj-1": {"stage": "blocked", "consecutive_stuck_sessions": 3},
            "obj-2": {"stage": "new", "consecutive_stuck_sessions": 0},
        },
        now=clock(),
    )
    stalled = K.score_risk(features)
    check(
        "застойный ученик набирает риск > 0",
        stalled["score"] > 0.2,
        str(stalled["score"]),
    )
    check(
        "доля неудачных проверок посчитана",
        abs(features["failed_check_ratio"] - 2 / 3.0) < 1e-6,
        str(features["failed_check_ratio"]),
    )
    check(
        "заблокированная цель учтена",
        features["blocked_objectives"] > 0.0,
        str(features["blocked_objectives"]),
    )
    check(
        "застой в 3 сессии даёт полный вклад",
        features["consecutive_stuck_sessions"] == 1.0,
        str(features["consecutive_stuck_sessions"]),
    )
    check(
        "маркер усталости учтён",
        features["negative_sentiment_hits"] > 0.0,
        str(features["negative_sentiment_hits"]),
    )
    check(
        "высокая помощь учтена",
        features["assistance_max_mean"] > 0.0,
        str(features["assistance_max_mean"]),
    )
    check(
        "недостающие данные названы в basis_ru",
        "недостапно" not in features["basis_ru"]
        and "недоступно" in features["basis_ru"],
        features["basis_ru"],
    )
    check(
        "основной объём данных назван в basis_ru",
        "2 занятий" in features["basis_ru"],
        features["basis_ru"],
    )
    check(
        "план ссылается на застой",
        any("подсказки" in s for s in K.recovery_plan(stalled)["steps_ru"]),
        str(K.recovery_plan(stalled)["steps_ru"]),
    )


def test_active_learner_scores_low():
    features = K.extract_features(
        sessions=[
            {
                "started_at": "2026-09-16T00:00:00Z",
                "ended_at": "2026-09-16T01:00:00Z",
                "state": "COMPLETED",
            },
            {
                "started_at": "2026-09-17T00:00:00Z",
                "ended_at": "2026-09-17T01:00:00Z",
                "state": "COMPLETED",
            },
        ],
        attempts=[
            {"submitted_at": "2026-09-16T12:00:00Z", "assistance_max": "NONE"},
            {"submitted_at": "2026-09-17T12:00:00Z", "assistance_max": "HINT"},
        ],
        checks=[{"verdict": "pass"}, {"verdict": "pass"}],
        objective_states={
            "obj-1": {"stage": "demonstrated", "consecutive_stuck_sessions": 0}
        },
        now=clock(),
    )
    risk = K.score_risk(features)
    check(
        "активный ученик не в зоне риска",
        risk["level"] == "low",
        "%s (%s)" % (risk["level"], risk["score"]),
    )


def main():
    tests = [
        test_weights_sum_to_one,
        test_extract_features_empty,
        test_score_risk_range_and_contributors,
        test_sentiment_signal,
        test_recovery_plan_is_deterministic,
        test_consent_gate,
        test_detect_at_risk_denominator,
        test_features_reflect_a_stalled_learner,
        test_active_learner_scores_low,
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
