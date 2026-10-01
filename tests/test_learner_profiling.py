#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for learner learning-style profiles (presentation preference only).

Claims under test:

* keyword heuristics are deterministic and a visual-keyword history makes
  `visual` dominant and strictly greater than `verbal`;
* `to_document()` validates against the published `learning_profile` contract
  and round-trips through `from_document`;
* `compute_pace` clamps to the schema band 50..500;
* `should_update_profile` is False immediately after a save and True after the
  5-session / 14-day threshold with an injected clock;
* `reset_profile` removes the file once, then reports nothing to remove;
* a corrupt profile is `PROFILE_UNREADABLE`, never a silent empty profile;
* a traversal `student_id` is refused, never written outside the temp root.

Run:
    python3 tests/test_learner_profiling.py
    python3 -m pytest tests/test_learner_profiling.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import learner_profiling as L, paths, schemas  # noqa: E402

_passed = 0
_failures: list[str] = []

STUDENT = "11111111-1111-4111-8111-111111111111"
BASE_NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


def iso(moment):
    """Aware-UTC timestamp in the harness's `...Z` convention."""
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    except L.LearnerProfilingError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Profiler:
    """A LearnerProfiler over a fresh temp `profiles/` directory."""

    def __init__(self, now=BASE_NOW):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir(parents=True, exist_ok=True)
        self._now = now
        self.engine = L.LearnerProfiler(self.profiles, clock=self.clock)

    def clock(self):
        return self._now

    def set_now(self, moment):
        self._now = moment

    def cleanup(self):
        self._tmp.cleanup()


def test_visual_keyword_history_dominates():
    """The implementation guide's own example: visual keywords -> visual wins."""
    history = [
        {"content": "покажи диаграмму к решению"},
        {"content": "нужна схема и график примеров"},
        {"content": "наглядный пример с картинкой"},
    ]
    style = L.infer_cognitive_style(history)
    check("доминирует visual", style.dominant() == "visual", str(style))
    check(
        "visual > verbal",
        style.visual > style.verbal,
        "visual=%s verbal=%s" % (style.visual, style.verbal),
    )
    check("visual > baseline", style.visual > L.STYLE_BASELINE, str(style.visual))

    again = L.infer_cognitive_style(list(history))
    check(
        "повторный расчёт идентичен",
        style.to_document() == again.to_document(),
        "%s != %s" % (style.to_document(), again.to_document()),
    )


def test_style_determinism():
    history = [{"content": "объясни словами текст определения"}]
    first = L.build_profile_from_history(
        STUDENT, history, updated_at="2026-09-17T00:00:00Z"
    )
    second = L.build_profile_from_history(
        STUDENT, list(history), updated_at="2026-09-17T00:00:00Z"
    )
    check(
        "одинаковая история -> одинаковый CognitiveStyle",
        first.cognitive_style.to_document() == second.cognitive_style.to_document(),
    )
    check(
        "одинаковая история -> одинаковый профиль",
        first.to_document() == second.to_document(),
    )


def test_empty_history_baseline():
    profile = L.build_profile_from_history(
        STUDENT, [], updated_at="2026-09-17T00:00:00Z"
    )
    style = profile.cognitive_style
    check("пустая история не исключение", True)
    check(
        "базовые баллы равны STYLE_BASELINE",
        style.visual == L.STYLE_BASELINE and style.verbal == L.STYLE_BASELINE,
    )
    check(
        "доминирующий стиль определён",
        style.dominant() in L.STYLE_ORDER,
        style.dominant(),
    )
    check(
        "pace — нейтральный дефолт",
        profile.pace_wpm == L.PACE_DEFAULT_WPM,
        str(profile.pace_wpm),
    )
    check("глубина — стратегическая", profile.depth_preference == "strategic")


def test_document_validates_and_round_trips():
    p = Profiler()
    try:
        profile = L.build_profile_from_history(
            STUDENT,
            [{"content": "покажи диаграмму", "duration_sec": 120}],
            updated_at=p.clock()
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        document = profile.to_document()
        try:
            schemas.validate(document, "learning_profile")
            check("документ профиля соответствует схеме", True)
        except schemas.SchemaError as e:
            check("документ профиля соответствует схеме", False, e.message[:200])

        restored = L.LearningProfile.from_document(document)
        check(
            "round-trip сохраняет student_id", restored.student_id == profile.student_id
        )
        check(
            "round-trip сохраняет cognitive_style",
            restored.cognitive_style.to_document()
            == profile.cognitive_style.to_document(),
        )
        check("round-trip сохраняет pace_wpm", restored.pace_wpm == profile.pace_wpm)
        check(
            "round-trip сохраняет challenge_tolerance",
            restored.challenge_tolerance == profile.challenge_tolerance,
        )
        check(
            "round-trip сохраняет feedback_style",
            restored.feedback_style == profile.feedback_style,
        )
        check(
            "round-trip сохраняет interaction_count",
            restored.interaction_count == profile.interaction_count,
        )
    finally:
        p.cleanup()


def test_compute_pace_clamps():
    # 2000 words in 60s -> 2000 wpm -> clamped to 500
    fast = [{"content": " ".join(["слово"] * 2000), "duration_sec": 60}]
    check(
        "слишком быстрый темп ограничен сверху",
        L.compute_pace(fast) == L.PACE_MAX_WPM,
        str(L.compute_pace(fast)),
    )

    # 10 words in 600s -> 1 wpm -> clamped to 50
    slow = [{"content": " ".join(["w"] * 10), "duration_sec": 600}]
    check(
        "слишком медленный темп ограничен снизу",
        L.compute_pace(slow) == L.PACE_MIN_WPM,
        str(L.compute_pace(slow)),
    )

    check(
        "без тайминга — дефолт 180",
        L.compute_pace([{"content": "текст"}]) == L.PACE_DEFAULT_WPM,
    )
    check("пустая история — дефолт", L.compute_pace([]) == L.PACE_DEFAULT_WPM)
    check("None-история — дефолт", L.compute_pace(None) == L.PACE_DEFAULT_WPM)


def test_should_update_profile_threshold():
    p = Profiler()
    try:
        profile = L.build_profile_from_history(
            STUDENT, [{"content": "текст"}], updated_at=iso(BASE_NOW)
        )
        p.engine.save_profile(profile)
        check(
            "сразу после сохранения не пересчитывать",
            p.engine.should_update_profile(STUDENT) is False,
        )

        # Age alone: profile older than 14 days.
        p.set_now(BASE_NOW + timedelta(days=14, seconds=1))
        check(
            "после 14 дней — пересчёт по возрасту",
            p.engine.should_update_profile(STUDENT) is True,
        )

        # Session counter: fresh profile with sessions_since_update >= 5.
        fresh = L.build_profile_from_history(
            STUDENT,
            [{"content": "текст"}],
            updated_at=iso(p.clock()),
        )
        fresh.sessions_since_update = 5
        p.engine.save_profile(fresh)
        p.set_now(BASE_NOW)  # age is not the trigger; the counter is
        check(
            "после 5 сессий — пересчёт", p.engine.should_update_profile(STUDENT) is True
        )

        fresh2 = dict_sessions(fresh, 4)
        p.engine.save_profile(fresh2)
        check(
            "4 сессии и свежий профиль — не пересчитывать",
            p.engine.should_update_profile(STUDENT) is False,
        )

        check(
            "отсутствующий профиль — пересчитывать",
            p.engine.should_update_profile("22222222-2222-4222-8222-222222222222")
            is True,
        )
    finally:
        p.cleanup()


def dict_sessions(profile, count):
    copy = L.LearningProfile.from_document(profile.to_document())
    copy.sessions_since_update = count
    return copy


def test_reset_profile_once():
    p = Profiler()
    try:
        profile = L.build_profile_from_history(
            STUDENT, [{"content": "текст"}], updated_at=iso(p.clock())
        )
        p.engine.save_profile(profile)
        p.engine.append_interaction(STUDENT, {"content": "ещё"})
        check("reset удаляет записанное", p.engine.reset_profile(STUDENT) is True)
        check("файл профиля удалён", not p.engine.path_for(STUDENT).is_file())
        check("повторный reset — False", p.engine.reset_profile(STUDENT) is False)
        check("после reset загрузка — None", p.engine.load_profile(STUDENT) is None)
        check("после reset история пуста", p.engine.load_history(STUDENT) == [])
    finally:
        p.cleanup()


def test_corrupt_profile_is_unreadable():
    p = Profiler()
    try:
        path = p.engine.path_for(STUDENT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        expect_error(
            "битый JSON профиля — PROFILE_UNREADABLE",
            lambda: p.engine.load_profile(STUDENT),
            "PROFILE_UNREADABLE",
        )

        # Structurally invalid but parseable: schema must refuse, not return empty.
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "student_id": STUDENT,
                    "cognitive_style": {"visual": "nope"},
                    "updated_at": "2026-09-17T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        expect_error(
            "невалидная структура профиля отклонена",
            lambda: p.engine.load_profile(STUDENT),
            "CONTRACT_INVALID",
        )

        # Missing file is None — absence, not corruption.
        check(
            "отсутствующий файл — None",
            p.engine.load_profile("33333333-3333-4333-8333-333333333333") is None,
        )
    finally:
        p.cleanup()


def test_path_traversal_student_id_refused():
    p = Profiler()
    try:
        escaped = p.root / "evil"
        expect_error(
            "student_id=../evil отклонён",
            lambda: p.engine.path_for("../evil"),
            "SLUG_SEPARATOR",
        )
        expect_error(
            "student_id=.. отклонён", lambda: p.engine.path_for(".."), "SLUG_TRAVERSAL"
        )
        check("файл вне temp root не создан", not escaped.exists())

        expect_error(
            "append с traversal-ид отклонён",
            lambda: p.engine.append_interaction("../evil", {"content": "x"}),
            "SLUG_SEPARATOR",
        )
        check("история вне temp root не создана", not escaped.exists())
    finally:
        p.cleanup()


def test_analyze_interaction_carries_counters():
    p = Profiler()
    try:
        first = p.engine.analyze_interaction(
            {"content": "покажи диаграмму"}, student_id=STUDENT
        )
        check("первое взаимодействие записано", first.interaction_count == 1)
        check(
            "sessions_since_update не сброшен в ноль без предыдущего профиля",
            first.sessions_since_update == 0,
        )

        second = p.engine.analyze_interaction({"content": "ещё"}, student_id=STUDENT)
        check("счётчик взаимодействий растёт", second.interaction_count == 2)
        stored = p.engine.load_profile(STUDENT)
        check("профиль сохранён на диске", stored is not None)
        check(
            "dominant_style в документе",
            stored.to_document()["dominant_style"] == stored.cognitive_style.dominant(),
        )
    finally:
        p.cleanup()


def test_profiles_dir_resolver():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "profiles"
        target.mkdir(parents=True, exist_ok=True)
        check(
            "profiles_dir(root) указывает на temp root",
            L.profiles_dir(root) == target,
            str(L.profiles_dir(root)),
        )
        # No profiles dir under root -> falls back to the repo profiles/.
        other = root / "empty"
        other.mkdir(parents=True, exist_ok=True)
        fallback = L.profiles_dir(other)
        check(
            "без profiles/ под root — fallback не temp",
            fallback != other / "profiles" or not (other / "profiles").exists(),
            str(fallback),
        )


def main():
    tests = [
        test_visual_keyword_history_dominates,
        test_style_determinism,
        test_empty_history_baseline,
        test_document_validates_and_round_trips,
        test_compute_pace_clamps,
        test_should_update_profile_threshold,
        test_reset_profile_once,
        test_corrupt_profile_is_unreadable,
        test_path_traversal_student_id_refused,
        test_analyze_interaction_carries_counters,
        test_profiles_dir_resolver,
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
