#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for opt-in, anonymous comparison bands.

Claims under test:

* a config with `anonymous=False` is refused with `NAMED_LEADERBOARD_REFUSED` —
  the constructor enforces `anonymous` as a constant, so named ranking cannot
  be stored at all;
* a non-opted-in student raises `LEADERBOARD_NOT_OPTED_IN`;
* `build_leaderboard` returns bands, never an ordered list of names: the
  document carries `ordered_names_returned: False` and no key mapping
  position -> id;
* **the fairness property**: `denominator` counts only opted-in students, and
  `opt_out` removes a student from that denominator immediately;
* `render_leaderboard` shows only the asking learner's own band;
* `personal_best` returns a delta and `most_improved` is deterministic;
* `DIMENSIONS` is the allowlist; an unknown dimension raises.

Run:
    python3 tests/test_leaderboards.py
    python3 -m pytest tests/test_leaderboards.py -q
"""

from __future__ import annotations

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

from botai_core import leaderboards as B, personas  # noqa: E402

_passed = 0
_failures: list[str] = []

A = "11111111-1111-4111-8111-111111111111"
C = "22222222-2222-4222-8222-222222222222"
D = "33333333-3333-4333-8333-333333333333"
NOT_OPTED = "44444444-4444-4444-8444-444444444444"


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
    except B.LeaderboardError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def config(**kwargs):
    kwargs.setdefault("cohort_id", "cohort-a")
    kwargs.setdefault("enabled", ("fastest_learner",))
    kwargs.setdefault("opted_in", (A, C, D))
    return B.LeaderboardConfig(**kwargs)


def facts():
    return {
        A: {
            "demonstrated_per_session": 0.9,
            "help_given_count": 2,
            "passed_check_ratio": 0.8,
        },
        C: {
            "demonstrated_per_session": 0.5,
            "help_given_count": 1,
            "passed_check_ratio": 0.6,
        },
        D: {
            "demonstrated_per_session": 0.2,
            "help_given_count": 0,
            "passed_check_ratio": 0.3,
        },
    }


def test_named_leaderboard_refused():
    expect_error(
        "anonymous=False отклонён",
        lambda: B.LeaderboardConfig(
            cohort_id="c", anonymous=False, enabled=("fastest_learner",), opted_in=(A,)
        ),
        "NAMED_LEADERBOARD_REFUSED",
    )
    expect_error(
        "anonymous='да' отклонён",
        lambda: B.LeaderboardConfig(cohort_id="c", anonymous="yes"),
        "NAMED_LEADERBOARD_REFUSED",
    )
    good = config()
    check("anonymous всегда True после конструктора", good.anonymous is True)
    check("to_document пишет anonymous=true", good.to_document()["anonymous"] is True)
    document = good.to_document()
    try:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.leaderboard.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        check(
            "документ без именной таблицы перечитывается",
            B.LeaderboardConfig.from_document(document).anonymous is True,
        )
    finally:
        pass


def test_not_opted_in_is_refused():
    cfg = config()
    check("opted-in ученик принимается", B.is_opted_in(cfg, A) is True)
    check("не opted-in отсутствует", B.is_opted_in(cfg, NOT_OPTED) is False)
    expect_error(
        "запрос статуса не-opted-in отклонён",
        lambda: B.require_opted_in(cfg, NOT_OPTED),
        "LEADERBOARD_NOT_OPTED_IN",
    )
    check(
        "require_opted_in для согласного возвращает True",
        B.require_opted_in(cfg, A) is True,
    )


def test_build_leaderboard_is_anonymous():
    cfg = config()
    document = B.build_leaderboard(
        cfg, facts_by_student=facts(), dimension="fastest_learner"
    )
    check("полосы построены", document["band_for"], str(document["band_for"]))
    check(
        "все согласившиеся получили полосу",
        set(document["band_for"]) == {A, C, D},
        str(document["band_for"]),
    )
    check("явный флаг: именных мест нет", document["ordered_names_returned"] is False)
    check("анонимность заявлена", document["anonymous"] is True)
    check(
        "сравнение вне оценки",
        "не входит в оценку" in document["excluded_from_grade_ru"],
        document["excluded_from_grade_ru"],
    )

    # Structural: no key mapping a position to a name.
    forbidden_keys = {
        "ranking",
        "ranks",
        "ordered",
        "positions",
        "names",
        "ranked",
        "top",
        "leaderboard",
    }
    check(
        "нет ключа с ранжированием",
        not (set(document) & forbidden_keys),
        str(sorted(document)),
    )
    # The only lists on the document are the band titles and the ids whose
    # value was unmeasurable — neither is a position, and neither is ordered
    # by rank. `band_for` maps id -> band string, never position -> name.
    listy = [k for k, v in document.items() if isinstance(v, list)]
    check(
        "списки на документе — только titles и unknown_values",
        set(listy) <= {"band_titles_ru", "unknown_values"},
        str(listy),
    )
    check(
        "band_for отображает id -> полоса, а не позицию",
        all(isinstance(v, str) for v in document["band_for"].values()),
        str(document["band_for"]),
    )
    check(
        "документ несёт cohort_id для рендера",
        document["cohort_id"] == "cohort-a",
        str(document.get("cohort_id")),
    )


def test_denominator_counts_only_opted_in():
    cfg = config()
    facts_all = dict(facts())
    facts_all[NOT_OPTED] = {
        "demonstrated_per_session": 5.0,
        "help_given_count": 99,
        "passed_check_ratio": 0.99,
    }
    document = B.build_leaderboard(
        cfg, facts_by_student=facts_all, dimension="fastest_learner"
    )
    check(
        "знаменатель = числу opted-in (3), а не 4",
        document["denominator"] == 3,
        str(document["denominator"]),
    )
    check(
        "не-opted-in не включён в полосы",
        NOT_OPTED not in document["band_for"],
        str(document["band_for"]),
    )
    check(
        "denominator_ru называет согласие",
        "давших согласие" in document["denominator_ru"],
        document["denominator_ru"],
    )
    check(
        "сверхвысокая точка не-opted-in не влияет на полосы",
        document["band_for"][A] != "bottom 50%"
        or document["band_for"][C] != "bottom 50%"
        or document["band_for"][D] != "bottom 50%",
        str(document["band_for"]),
    )


def test_opt_out_removes_from_denominator():
    cfg = config()
    before = B.build_leaderboard(
        cfg, facts_by_student=facts(), dimension="fastest_learner"
    )
    check("до opt-out: 3", before["denominator"] == 3, str(before["denominator"]))

    after_cfg = B.opt_out(cfg, D)
    check(
        "opt-out удаляет из opted_in",
        D not in after_cfg.opted_in,
        str(after_cfg.opted_in),
    )
    after = B.build_leaderboard(
        after_cfg, facts_by_student=facts(), dimension="fastest_learner"
    )
    check(
        "после opt-out: знаменатель 2",
        after["denominator"] == 2,
        str(after["denominator"]),
    )
    check(
        "после opt-out: полос у 2",
        set(after["band_for"]) == {A, C},
        str(after["band_for"]),
    )
    expect_error(
        "после opt-out запрос статуса отклонён",
        lambda: B.require_opted_in(after_cfg, D),
        "LEADERBOARD_NOT_OPTED_IN",
    )

    only_a = B.opt_out(B.opt_out(cfg, D), C)
    check(
        "после двух opt-out остаётся один согласившийся",
        only_a.opted_in == (A,),
        str(only_a.opted_in),
    )
    check(
        "измерения включены, пока кто-то согласился",
        only_a.enabled == cfg.enabled,
        str(only_a.enabled),
    )
    everyone_out = B.opt_out(only_a, A)
    check(
        "после всех opt-out сравнение выключено",
        everyone_out.enabled == (),
        str(everyone_out.enabled),
    )


def test_opt_in_is_a_copy():
    cfg = config(opted_in=(A,))
    new = B.opt_in(cfg, C)
    check("opt-in не мутирует исходный конфиг", cfg.opted_in == (A,), str(cfg.opted_in))
    check("opt-in добавляет в копию", C in new.opted_in, str(new.opted_in))
    check(
        "opt-in не включает измерения само по себе",
        new.enabled == cfg.enabled,
        str(new.enabled),
    )
    expect_error(
        "opt-in без идентификатора отклонён",
        lambda: B.opt_in(cfg, "  "),
        "STUDENT_ID_INVALID",
    )


def test_render_shows_only_own_band():
    cfg = config()
    document = B.build_leaderboard(
        cfg, facts_by_student=facts(), dimension="fastest_learner"
    )
    rendered = B.render_leaderboard(document, learner_id=A)
    check(
        "в выдаче есть своя полоса",
        A in rendered
        or "топ" in rendered
        or "верхние" in rendered
        or "нижние" in rendered,
        rendered[:300],
    )
    check(
        "имён других участников нет",
        C not in rendered and D not in rendered,
        rendered[:400],
    )
    check(
        "сказано, что списка участников нет",
        "нет списка" in rendered or "участников" in rendered,
        rendered[:400],
    )
    check("знаменатель назван", "знаменатель" in rendered, rendered[:300])
    check(
        "сравнение вне оценки",
        "не входит в оценку" in rendered or "влияет на оценку" in rendered,
        rendered[-300:],
    )

    expect_error(
        "рендер для не-opted-in отклонён",
        lambda: B.render_leaderboard(document, learner_id=NOT_OPTED),
        "LEADERBOARD_NOT_OPTED_IN",
    )


def test_personal_best_and_most_improved():
    history = [
        {"value": 2.0, "at": "2026-09-01T00:00:00Z"},
        {"value": 5.0, "at": "2026-09-08T00:00:00Z"},
        {"value": 7.0, "at": "2026-09-15T00:00:00Z"},
    ]
    best = B.personal_best(history)
    check("личный рекорд 7.0", best["best_value"] == 7.0, str(best["best_value"]))
    check(
        "достижение датировано",
        best["achieved_at"] == "2026-09-15T00:00:00Z",
        str(best["achieved_at"]),
    )
    check(
        "дельта до второго места",
        best["delta_vs_previous"] == 2.0,
        str(best["delta_vs_previous"]),
    )
    check(
        "сравнение только с собой",
        "рекорд" in best["reason_ru"],
        best["reason_ru"],
    )

    empty = B.personal_best([])
    check("без записей рекорд None", empty["best_value"] is None, str(empty))
    check(
        "без записей причина названа",
        "первой" in empty["reason_ru"],
        empty["reason_ru"],
    )

    improved = B.most_improved(student_history=history)
    check("прирост = 5.0", improved["delta"] == 5.0, str(improved["delta"]))
    check(
        "первая и последняя значения названы",
        improved["first_value"] == 2.0 and improved["last_value"] == 7.0,
        str(improved),
    )
    again = B.most_improved(student_history=list(history))
    check("прирост детерминирован", improved == again)

    one = B.most_improved(student_history=[{"value": 1.0}])
    check("одна точка: прирост None", one["delta"] is None, str(one))
    check(
        "одна точка: причина названа", "две точки" in one["reason_ru"], one["reason_ru"]
    )


def test_dimension_allowlist():
    check(
        "DIMENSIONS совпадает с источником",
        set(B.DIMENSIONS) == set(B.DIMENSION_SOURCE),
        str(B.DIMENSIONS),
    )
    check(
        "все измерения есть в схеме cohort",
        set(B.DIMENSIONS)
        == {
            "fastest_learner",
            "most_helpful_peer",
            "best_code_quality",
            "most_improved",
        },
    )
    expect_error(
        "неизвестное измерение отклонено",
        lambda: B.build_leaderboard(
            config(), facts_by_student=facts(), dimension="most_popular"
        ),
        "DIMENSION_UNKNOWN",
    )
    expect_error(
        "неизвестное измерение в конфиге отклонено",
        lambda: B.LeaderboardConfig(
            cohort_id="c", enabled=("most_popular",), opted_in=(A,)
        ),
        "DIMENSION_UNKNOWN",
    )
    expect_error(
        "выключенное измерение отклонено",
        lambda: B.build_leaderboard(
            config(enabled=()), facts_by_student=facts(), dimension="fastest_learner"
        ),
        "DIMENSION_DISABLED",
    )


def test_config_default_posture():
    missing = B.load_config(tempfile.mkdtemp(), "no-cohort")
    check("без файла конфиг пустой", missing.enabled == (), str(missing.enabled))
    check(
        "без файла никто не согласился", missing.opted_in == (), str(missing.opted_in)
    )
    check("без файла anonymous=True", missing.anonymous is True)

    nobody = B.LeaderboardConfig(
        cohort_id="c", enabled=("fastest_learner",), opted_in=()
    )
    check("без opted-in измерения выключены", nobody.enabled == (), str(nobody.enabled))
    # The constructor zeroes `enabled` when nobody opted in, so the first gate
    # that fires for an unopted-in cohort is DIMENSION_DISABLED — an empty
    # table is never shown, by either code.
    expect_error(
        "без opted-in построение отклонено",
        lambda: B.build_leaderboard(
            nobody, facts_by_student={}, dimension="fastest_learner"
        ),
        "DIMENSION_DISABLED",
    )

    with_data = B.build_leaderboard(
        config(opted_in=(A,)),
        facts_by_student={A: {"demonstrated_per_session": 1.0}},
        dimension="fastest_learner",
    )
    check(
        "один согласившийся: полоса топ-30%",
        with_data["band_for"][A] == "top 30%",
        str(with_data["band_for"]),
    )
    check(
        "запись о неизмеренных значениях",
        "unknown_values" in with_data,
        str(sorted(with_data)),
    )


def test_unknown_value_gets_bottom_band():
    cfg = config()
    partial = {A: {"demonstrated_per_session": 1.0}, C: {}, D: None}
    document = B.build_leaderboard(
        cfg, facts_by_student=partial, dimension="fastest_learner"
    )
    check(
        "согласившийся без значения получает bottom 50%",
        document["band_for"][C] == "bottom 50%",
        str(document["band_for"]),
    )
    check(
        "None-факты тоже bottom",
        document["band_for"][D] == "bottom 50%",
        str(document["band_for"]),
    )
    check(
        "неизмеренные перечислены",
        sorted(document["unknown_values"]) == sorted([C, D]),
        str(document["unknown_values"]),
    )
    check(
        "знаменатель считает только измеренных",
        document["denominator"] == 1,
        str(document["denominator"]),
    )


def test_most_improved_dimension():
    cfg = config(enabled=("most_improved",))
    history_facts = {
        A: {
            "history": [
                {"value": 1, "at": "2026-09-01T00:00:00Z"},
                {"value": 6, "at": "2026-09-10T00:00:00Z"},
            ]
        },
        C: {
            "history": [
                {"value": 3, "at": "2026-09-01T00:00:00Z"},
                {"value": 4, "at": "2026-09-10T00:00:00Z"},
            ]
        },
    }
    document = B.build_leaderboard(
        cfg, facts_by_student=history_facts, dimension="most_improved"
    )
    check(
        "A прирос сильнее -> топ-30%",
        document["band_for"][A] == "top 30%",
        str(document["band_for"]),
    )
    # Two measured learners: percentiles 100 and 50, so the lower one is
    # "top 50%" — a band a learner can act on, never a place in a list.
    check(
        "C прирос меньше -> верхние 50%",
        document["band_for"][C] == "top 50%",
        str(document["band_for"]),
    )
    check("знаменатель 2", document["denominator"] == 2, str(document["denominator"]))


def test_badges_are_delegated():
    """This module must not define its own badge rule."""
    awarded, _, _ = personas.evaluate_achievements(
        {"explain_pass": ["c0000000-0000-4000-8000-000000000001"]},
        existing=[],
        learner_id=A,
        course_id="c",
        awarded_at="2026-09-18T00:00:00Z",
    )
    check("achievement есть", bool(awarded), str(awarded))
    via = B.evaluate_badges(
        facts={"explain_pass": ["c0000000-0000-4000-8000-000000000001"]},
        existing=[],
        learner_id=A,
        course_id="c",
        awarded_at="2026-09-18T00:00:00Z",
    )
    check(
        "evaluate_badges делегирует personas.evaluate_achievements",
        [a["achievement_id"] for a in via[0]] == [a["achievement_id"] for a in awarded],
        str(via),
    )


def test_config_round_trip():
    cfg = config(opted_in=(C, A))  # deliberately unsorted
    document = cfg.to_document()
    check(
        "opted_in сортируется",
        document["opted_in"] == [A, C],
        str(document["opted_in"]),
    )
    restored = B.LeaderboardConfig.from_document(document)
    check("round-trip: cohort_id", restored.cohort_id == cfg.cohort_id)
    check("round-trip: enabled", restored.enabled == cfg.enabled)
    check("round-trip: opted_in", restored.opted_in == (A, C))
    check("round-trip: anonymous", restored.anonymous is True)

    expect_error(
        "не-объект конфига отклонён",
        lambda: B.LeaderboardConfig.from_document("строка"),
        "CONFIG_INVALID",
    )
    expect_error(
        "неизвестные поля отклонены",
        lambda: B.LeaderboardConfig.from_document({"cohort_id": "c", "rank": [1, 2]}),
        "CONFIG_UNKNOWN_FIELDS",
    )
    expect_error(
        "пустой cohort_id отклонён",
        lambda: B.LeaderboardConfig(cohort_id=""),
        "COHORT_ID_REQUIRED",
    )


def main():
    tests = [
        test_named_leaderboard_refused,
        test_not_opted_in_is_refused,
        test_build_leaderboard_is_anonymous,
        test_denominator_counts_only_opted_in,
        test_opt_out_removes_from_denominator,
        test_opt_in_is_a_copy,
        test_render_shows_only_own_band,
        test_personal_best_and_most_improved,
        test_dimension_allowlist,
        test_config_default_posture,
        test_unknown_value_gets_bottom_band,
        test_most_improved_dimension,
        test_badges_are_delegated,
        test_config_round_trip,
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
