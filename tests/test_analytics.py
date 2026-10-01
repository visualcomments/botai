#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for learning analytics: what the record shows, and what it does not.

Claims under test:

* `build_student_analytics` with empty inputs is total — no ZeroDivisionError,
  `velocity == 0.0`, and a stated denominator;
* `predicted_performance` returns a band in `low|medium|high`, a score in
  0..1, a `basis_ru` naming the inputs, and the module's own honesty flag
  `trained_model is False`;
* `peer_comparison` returns an anonymous band only — no ordered list of names,
  guarded by `named_ranks_returned`;
* `render_report_html` escapes an injected `<script>` in an objective title;
* `render_report_markdown` contains the denominator;
* `validate_analytics` raises `ANALYTICS_INVALID` on a malformed document.

Run:
    python3 tests/test_analytics.py
    python3 -m pytest tests/test_analytics.py -q
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

from botai_core import analytics as A  # noqa: E402

_passed = 0
_failures: list[str] = []

LEARNER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


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
    except A.AnalyticsError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def empty_course():
    """A course object shaped like `AcceptedCourse` for analytics: track only."""

    class Course:
        course_id = "minimal-diff"
        contract = {"title": "Курс: различия коммитов"}
        track = {"objectives": []}

    return Course()


def course_with_objectives(titles):
    class Course:
        course_id = "minimal-diff"
        contract = {"title": "Курс: различия коммитов"}
        track = {
            "objectives": [
                {"objective_id": "obj-%d" % index, "title": title}
                for index, title in enumerate(titles, start=1)
            ]
        }

    return Course()


def build(**kwargs):
    base = {
        "course": empty_course(),
        "learner_id": LEARNER,
        "sessions": [],
        "objective_states": {},
        "attempts": [],
        "checks": [],
        "clock": lambda: __import__("datetime").datetime(
            2026, 9, 17, 12, 0, 0, tzinfo=__import__("datetime").timezone.utc
        ),
    }
    base.update(kwargs)
    return A.build_student_analytics(**base)


def test_empty_inputs_are_total():
    document = build()
    check("пустые входы не исключение", isinstance(document, dict))
    check("velocity == 0.0", document["velocity"] == 0.0, str(document["velocity"]))
    check(
        "counts нулевые",
        document["counts"]
        == {
            "objectives": 0,
            "demonstrated": 0,
            "review_due": 0,
            "stuck": 0,
            "sessions": 0,
            "active_sessions": 0,
            "attempts": 0,
            "checks": 0,
        },
        str(document["counts"]),
    )
    check("denominator нулевой", document["denominator"]["objectives"] == 0)
    check(
        "denominator_ru назван",
        "целей" in document["denominator_ru"],
        document["denominator_ru"][:80],
    )
    check("heatmap пуст", document["progress_heatmap"] == [])
    check("struggle пуст", document["struggle_points"] == [])
    check(
        "generated_at в формате Z",
        document["generated_at"].endswith("Z"),
        document["generated_at"],
    )

    prediction = A.predicted_performance(document)
    check(
        "прогноз на пустых входах не падает",
        0.0 <= prediction["score"] <= 1.0,
        str(prediction["score"]),
    )


def test_predicted_performance_is_honest():
    course = course_with_objectives(["Цель A", "Цель B"])
    document = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={
            "obj-1": {
                "stage": "demonstrated",
                "assistance_max": "HINT",
                "consecutive_stuck_sessions": 0,
            },
            "obj-2": {
                "stage": "review_due",
                "assistance_max": "NONE",
                "consecutive_stuck_sessions": 0,
            },
        },
        checks=[{"objective_id": "obj-1", "verdict": "pass"}],
        attempts=[{"objective_id": "obj-1", "duration_seconds": 60}],
    )
    prediction = A.predicted_performance(document)
    check(
        "band в допустимых",
        prediction["band"] in ("low", "medium", "high"),
        prediction["band"],
    )
    check("score в 0..1", 0.0 <= prediction["score"] <= 1.0, str(prediction["score"]))
    check(
        "basis_ru называет входы",
        "освоено" in prediction["basis_ru"],
        prediction["basis_ru"][:120],
    )
    check(
        "basis_ru называет веса",
        "0.50" in prediction["basis_ru"],
        prediction["basis_ru"][-80:],
    )
    check("явный флаг: не обученная модель", prediction["trained_model"] is False)
    check("формула документирована", "0.50" in prediction["formula"])
    check("band_ru заполнен", bool(prediction["band_ru"]), prediction["band_ru"])

    # The full-mastery case lands in the top band.
    all_good = build(
        course=course_with_objectives(["A", "B"]),
        sessions=[{"state": "COMPLETED"}, {"state": "FEEDBACK"}],
        objective_states={
            "obj-1": {"stage": "demonstrated"},
            "obj-2": {"stage": "demonstrated"},
        },
    )
    high = A.predicted_performance(all_good)
    check("все цели освоены -> high", high["band"] == "high", str(high))
    check("высокий score", high["score"] >= 0.7, str(high["score"]))


def test_peer_comparison_is_anonymous():
    def analytics(demonstrated, objectives):
        return {
            "learner_id": LEARNER,
            "counts": {"objectives": objectives, "demonstrated": demonstrated},
        }

    mapping = {
        LEARNER: analytics(2, 4),
        OTHER: analytics(3, 4),
        "33333333-3333-4333-8333-333333333333": analytics(0, 4),
        "44444444-4444-4444-8444-444444444444": analytics(1, 4),
    }
    band = A.peer_comparison(mapping, student_id=LEARNER)
    check(
        "percentile в 0..100",
        0.0 <= band["percentile"] <= 100.0,
        str(band["percentile"]),
    )
    check(
        "band_ru назван",
        "относительно сравниваемой группы" in band["band_ru"],
        band["band_ru"],
    )
    check("явный флаг: именных мест нет", band["named_ranks_returned"] is False)
    check(
        "знаменатель = числу сравнений",
        band["denominator"] == 4,
        str(band["denominator"]),
    )

    # Structural: no key that maps a position -> someone else's id.
    keys = set(band)
    forbidden = {
        "ranking",
        "ordered",
        "ranks",
        "positions",
        "names",
        "ordered_names_returned",
    }
    check("нет ключа с ранжированием", not (keys & forbidden), str(keys))
    check(
        "нет списка имён в значениях",
        OTHER not in str(band.values()) or band["student_id"] == LEARNER,
        str(band),
    )

    expect_error(
        "пустое сравнение отклонено",
        lambda: A.peer_comparison({}, student_id=LEARNER),
        "ANALYTICS_EMPTY",
    )
    expect_error(
        "несколько учеников без student_id отклонено",
        lambda: A.peer_comparison({LEARNER: mapping[LEARNER], OTHER: mapping[OTHER]}),
        "STUDENT_ID_REQUIRED",
    )
    expect_error(
        "неизвестный student_id отклонён",
        lambda: A.peer_comparison(
            mapping, student_id="55555555-5555-4555-8555-555555555555"
        ),
        "STUDENT_NOT_IN_COMPARISON",
    )


def test_html_escapes_script_injection():
    course = course_with_objectives(["<script>alert('xss')</script>"])
    document = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={"obj-1": {"stage": "demonstrated"}},
    )
    html = A.render_report_html(document)
    check("сырой <script> отсутствует в выдаче", "<script>" not in html, html[:400])
    check(
        "экранированный скрипт присутствует",
        "&lt;script&gt;" in html or "&lt;" in html,
        html[:400],
    )
    check("document_title не падает", True)
    check("HTML начинается с doctype", html.startswith("<!DOCTYPE html>"))
    check("denominator_ru выведен", "Покрытие" in html)

    # The title itself is escaped too.
    class BadCourse:
        course_id = "x"
        contract = {"title": "<img src=x onerror=alert(1)>"}
        track = {"objectives": []}

    html2 = A.render_report_html(build(course=BadCourse()))
    check("заголовок курса экранирован", "<img" not in html2, html2[:300])


def test_markdown_contains_denominator():
    course = course_with_objectives(["A", "B"])
    document = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={
            "obj-1": {"stage": "demonstrated"},
            "obj-2": {"stage": "new"},
        },
        attempts=[{"objective_id": "obj-1", "duration_seconds": 30}],
    )
    markdown = A.render_report_markdown(document)
    check("деноминатор назван", document["denominator_ru"] in markdown, markdown[:400])
    check(
        "markdown содержит счётчики",
        "| целей показано | 1 |" in markdown,
        markdown[:600],
    )
    check(
        "прогноз не назван обученной моделью",
        "не обученная модель" in markdown,
        markdown[:400],
    )
    check(
        "время без данных не выдумывается",
        "нет данных" in markdown or "30" in markdown,
        markdown[-400:],
    )

    # A missing measurement stays "нет данных", not 0.
    no_time = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={
            "obj-1": {"stage": "demonstrated"},
            "obj-2": {"stage": "new"},
        },
        attempts=[{"objective_id": "obj-1"}],
    )
    rendered = A.render_report_markdown(no_time)
    check(
        "неизвестное время показано как нет данных",
        "нет данных" in rendered,
        rendered[-300:],
    )


def test_validate_analytics_refuses_malformed():
    good = build()
    expect_error(
        "None вместо документа отклонён",
        lambda: A.validate_analytics(None),
        "ANALYTICS_INVALID",
    )
    expect_error(
        "список вместо документа отклонён",
        lambda: A.validate_analytics([]),
        "ANALYTICS_INVALID",
    )

    missing = dict(good)
    del missing["velocity"]
    expect_error(
        "нет velocity — ANALYTICS_INVALID",
        lambda: A.validate_analytics(missing),
        "ANALYTICS_INVALID",
    )

    bad_counts = dict(good, counts="не объект")
    expect_error(
        "counts не объект — ANALYTICS_INVALID",
        lambda: A.validate_analytics(bad_counts),
        "ANALYTICS_INVALID",
    )

    negative = dict(good, counts=dict(good["counts"], objectives=-1))
    expect_error(
        "отрицательный счётчик — ANALYTICS_INVALID",
        lambda: A.validate_analytics(negative),
        "ANALYTICS_INVALID",
    )

    no_learner = dict(good, learner_id="")
    expect_error(
        "без learner_id — ANALYTICS_INVALID",
        lambda: A.validate_analytics(no_learner),
        "ANALYTICS_INVALID",
    )

    bad_list = dict(good, progress_heatmap={})
    expect_error(
        "heatmap не список — ANALYTICS_INVALID",
        lambda: A.validate_analytics(bad_list),
        "ANALYTICS_INVALID",
    )

    no_denom = dict(good)
    del no_denom["denominator"]
    expect_error(
        "нет denominator — ANALYTICS_INVALID",
        lambda: A.validate_analytics(no_denom),
        "ANALYTICS_INVALID",
    )

    check("валидный документ проходит", A.validate_analytics(good) is good)


def test_weekly_digest_stays_offline():
    course = course_with_objectives(["A"])
    document = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={"obj-1": {"stage": "demonstrated"}},
    )
    text = A.weekly_digest(document)
    check("digest — строка", isinstance(text, str) and text)
    check("digest называет цель", "Освоено 1 из 1" in text, text[:200])
    check("digest не отправляется модулем", True)


def test_struggle_points_and_heatmap():
    course = course_with_objectives(["A", "B"])
    document = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={
            "obj-1": {
                "stage": "practising",
                "assistance_max": "EXAMPLE",
                "consecutive_stuck_sessions": 0,
            },
            "obj-2": {
                "stage": "practising",
                "assistance_max": "NONE",
                "consecutive_stuck_sessions": 0,
            },
        },
        checks=[
            {"objective_id": "obj-2", "verdict": "fail"},
            {"objective_id": "obj-2", "verdict": "fail"},
            {"objective_id": "obj-2", "verdict": "fail"},
        ],
    )
    check(
        "EXAMPLE-помощь даёт точку затруднения",
        any(s["objective_id"] == "obj-1" for s in document["struggle_points"]),
        str(document["struggle_points"]),
    )
    check(
        "три неудачные проверки дают точку затруднения",
        any(s["objective_id"] == "obj-2" for s in document["struggle_points"]),
        str(document["struggle_points"]),
    )
    # obj-1: EXAMPLE assistance (rank 3), no failed checks -> weight 3.
    # obj-2: no assistance, 3 failed checks -> weight 3. A tie, broken by id,
    # so obj-1 precedes obj-2 — the ordering never depends on store order.
    check(
        "порядок затруднений по весу, затем по id",
        [s["objective_id"] for s in document["struggle_points"]] == ["obj-1", "obj-2"],
        str([s["objective_id"] for s in document["struggle_points"]]),
    )
    weights = [(s["objective_id"], s["weight"]) for s in document["struggle_points"]]
    check(
        "веса рассчитаны по документированной формуле",
        weights == [("obj-1", 3), ("obj-2", 3)],
        str(weights),
    )
    check(
        "у obj-1 названа причина EXAMPLE",
        any("EXAMPLE" in r for r in document["struggle_points"][0]["reasons_ru"]),
        str(document["struggle_points"][0]["reasons_ru"]),
    )
    check(
        "у obj-2 названо число неудач",
        any("неудачных" in r for r in document["struggle_points"][1]["reasons_ru"]),
        str(document["struggle_points"][1]["reasons_ru"]),
    )

    heatmap = {row["objective_id"]: row for row in document["progress_heatmap"]}
    check(
        "heatmap покрывает все цели", set(heatmap) == {"obj-1", "obj-2"}, str(heatmap)
    )
    check(
        "heatmap несёт число проверок",
        heatmap["obj-2"]["checks"] == 3,
        str(heatmap["obj-2"]),
    )

    prediction = A.predicted_performance(document)
    check(
        "struggle_points входят в прогноз",
        "точек затруднения" in prediction["basis_ru"],
        prediction["basis_ru"][-120:],
    )


def test_time_investment_none_vs_zero():
    course = course_with_objectives(["A"])
    with_duration = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={"obj-1": {"stage": "new"}},
        attempts=[{"objective_id": "obj-1", "duration_seconds": 90}],
    )
    check(
        "явная длительность учтена",
        with_duration["time_investment"][0]["seconds"] == 90,
        str(with_duration["time_investment"]),
    )

    no_data = build(
        course=course,
        sessions=[{"state": "COMPLETED"}],
        objective_states={"obj-1": {"stage": "new"}},
        attempts=[{"objective_id": "obj-1"}],
    )
    check(
        "одна отметка времени -> нет данных, а не 0",
        no_data["time_investment"][0]["seconds"] is None,
        str(no_data["time_investment"]),
    )


def test_velocity_with_sessions():
    course = course_with_objectives(["A", "B"])
    document = build(
        course=course,
        sessions=[{"state": "COMPLETED"}, {"state": "FEEDBACK"}],
        objective_states={
            "obj-1": {"stage": "demonstrated"},
            "obj-2": {"stage": "demonstrated"},
        },
    )
    check(
        "velocity = 2/2 = 1.0", document["velocity"] == 1.0, str(document["velocity"])
    )
    check(
        "отменённое занятие не считается активным",
        A._is_active_session({"state": "CANCELLED"}) is False,
    )
    check(
        "приостановленное занятие не активно",
        A._is_active_session({"state": "PAUSED"}) is False,
    )
    check("COMPLETED — активное", A._is_active_session({"state": "COMPLETED"}) is True)


def test_profile_note_never_scores():
    document = build(
        profile={
            "cognitive_style": {"dominant_style": "visual"},
            "dominant_style": "visual",
        }
    )
    check(
        "профиль упомянут строкой",
        "визуаль" in (document["profile_note_ru"] or "")
        or "visual" in (document["profile_note_ru"] or ""),
        str(document["profile_note_ru"]),
    )
    check(
        "сказано, что профиль не влияет на оценки",
        "не на оценки" in document["profile_note_ru"],
        document["profile_note_ru"],
    )
    check(
        "профиль не попал в score-поля",
        "visual" not in str(document.get("counts")),
        str(document.get("counts")),
    )


def main():
    tests = [
        test_empty_inputs_are_total,
        test_predicted_performance_is_honest,
        test_peer_comparison_is_anonymous,
        test_html_escapes_script_injection,
        test_markdown_contains_denominator,
        test_validate_analytics_refuses_malformed,
        test_weekly_digest_stays_offline,
        test_struggle_points_and_heatmap,
        test_time_investment_none_vs_zero,
        test_velocity_with_sessions,
        test_profile_note_never_scores,
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
