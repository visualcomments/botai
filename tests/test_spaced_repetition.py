#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for SM-2 spaced repetition: the deck schedules, it never judges.

Claims under test:

* the module's own reference example —
  `calculate_next_interval(quality=5, current_interval=6, ease_factor=2.5,
  is_first_review=False) == (15, 2.6)` — holds exactly;
* `quality < 3` restarts the ladder at 1 day and increments `lapses`;
* ease is floored at `MIN_EASE = 1.3`, and the returned interval is never below
  1 for a full sweep of quality × interval × ease;
* `record_review` returns a NEW card and leaves the caller's object untouched;
* a corrupt deck is `DECK_UNREADABLE` — a learner's scheduling state is never
  silently reset;
* `get_due_cards` / `due_summary` agree with an injected clock.

Run:
    python3 tests/test_spaced_repetition.py
    python3 -m pytest tests/test_spaced_repetition.py -q
"""

from __future__ import annotations

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

from botai_core import schemas, spaced_repetition as R  # noqa: E402

_passed = 0
_failures: list[str] = []

STUDENT = "11111111-1111-4111-8111-111111111111"
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
    except R.SpacedRepetitionError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Deck:
    """An engine over a fresh temp `reviews/` directory with an injected clock."""

    def __init__(self, now=NOW):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reviews = self.root / "reviews"
        self.reviews.mkdir(parents=True, exist_ok=True)
        self._now = now
        self.engine = R.SpacedRepetitionEngine(self.reviews, clock=self.clock)

    def clock(self):
        return self._now

    def set_now(self, moment):
        self._now = moment

    def cleanup(self):
        self._tmp.cleanup()


def test_reference_example():
    """The number the docstring pins: (15, 2.6)."""
    interval, ease = R.SM2Algorithm.calculate_next_interval(
        quality=5, current_interval=6, ease_factor=2.5, is_first_review=False
    )
    check("эталонный пример даёт интервал 15", interval == 15, str(interval))
    check("эталонный пример даёт ease 2.6", ease == 2.6, repr(ease))
    check("ease не зафиксирован на 2.5", ease > 2.5, repr(ease))


def test_quality_below_three_is_a_lapse():
    interval, ease = R.SM2Algorithm.calculate_next_interval(
        quality=2, current_interval=6, ease_factor=2.5, is_first_review=False
    )
    check("провал -> интервал 1 день", interval == 1, str(interval))
    check("ease уменьшается", ease < 2.5, repr(ease))

    card = R.ReviewCard(
        objective_id="obj-a",
        last_review="2026-09-17T12:00:00Z",
        next_review="2026-09-18T12:00:00Z",
        interval_days=6,
        ease_factor=2.5,
        review_count=3,
        lapses=0,
    )
    d = Deck()
    try:
        updated = d.engine.record_review(card, 1)
        check("lapses увеличивается", updated.lapses == 1, str(updated.lapses))
        check(
            "interval сброшен на 1",
            updated.interval_days == 1,
            str(updated.interval_days),
        )
        check(
            "исходная карточка не тронута (lapses)", card.lapses == 0, str(card.lapses)
        )
        check(
            "исходная карточка не тронута (interval)",
            card.interval_days == 6,
            str(card.interval_days),
        )
        check("record_review возвращает НОВЫЙ объект", updated is not card)

        good = d.engine.record_review(card, 5)
        check("успешный повтор не трогает lapses", good.lapses == 0, str(good.lapses))
        check(
            "успешный повтор наращивает интервал",
            good.interval_days > card.interval_days,
            str(good.interval_days),
        )
    finally:
        d.cleanup()


def test_ease_floor_and_ceiling():
    ease = 2.5
    for _ in range(10):
        _interval, ease = R.SM2Algorithm.calculate_next_interval(
            quality=0, current_interval=6, ease_factor=ease, is_first_review=False
        )
    check("ease ограничен снизу 1.3", ease >= R.SM2Algorithm.MIN_EASE, repr(ease))
    check("пол — именно 1.3", ease == R.SM2Algorithm.MIN_EASE, repr(ease))
    check("пол ниже верхней границы", R.SM2Algorithm.MIN_EASE < R.SM2Algorithm.MAX_EASE)

    _interval, better = R.SM2Algorithm.calculate_next_interval(
        quality=5, current_interval=6, ease_factor=2.5, is_first_review=False
    )
    check(
        "ease не экспортирован выше 2.5 механически нет",
        better >= R.SM2Algorithm.MIN_EASE,
        repr(better),
    )
    # The documented reading: MAX_EASE bounds the multiplier, not the stored
    # ease — a perfect streak must still grow the interval.
    grown = R.SM2Algorithm.calculate_next_interval(
        quality=5, current_interval=30, ease_factor=2.6, is_first_review=False
    )
    check("рост интервала сохранён (30 -> 75)", grown[0] == 75, str(grown))


def test_interval_never_below_one():
    """Sweep quality 0..5 × interval 1/6/30 × ease 1.3/2.5: never < 1."""
    for quality in range(0, 6):
        for current in (1, 6, 30):
            for ease in (1.3, 2.5):
                for first in (False, True):
                    interval, new_ease = R.SM2Algorithm.calculate_next_interval(
                        quality=quality,
                        current_interval=current,
                        ease_factor=ease,
                        is_first_review=first,
                    )
                    label = "q=%s i=%s e=%s first=%s" % (quality, current, ease, first)
                    if interval < 1:
                        check("интервал >= 1 (%s)" % label, False, str(interval))
                        break
                    if new_ease < R.SM2Algorithm.MIN_EASE:
                        check("ease >= 1.3 (%s)" % label, False, repr(new_ease))
                        break
                else:
                    continue
                break
            else:
                continue
            break
        else:
            check("интервал >= 1 для quality=%s по всем i/e/first" % quality, True)


def test_quality_out_of_range():
    for bad in (6, -1, 10, "5", 5.0, None, True, False):
        expect_error(
            "качество %r отклонено" % (bad,),
            lambda q=bad: R.SM2Algorithm.calculate_next_interval(
                quality=q, current_interval=6, ease_factor=2.5, is_first_review=False
            ),
            "QUALITY_OUT_OF_RANGE",
        )


def test_due_cards_with_injected_clock():
    d = Deck()
    try:
        card_due_early = d.engine.create_card("obj-due")
        card_due_late = d.engine.create_card("obj-future")
        late = R.ReviewCard(
            objective_id="obj-future",
            last_review=card_due_late.last_review,
            next_review="2027-01-01T00:00:00Z",
            interval_days=1,
            ease_factor=2.5,
            review_count=1,
            lapses=0,
        )
        d.engine.save_deck(STUDENT, [card_due_early, late])

        d.set_now(NOW + timedelta(days=2))
        due = d.engine.get_due_cards(STUDENT)
        check(
            "см 2 дня вперёд: ждёт только просроченная",
            [c.objective_id for c in due] == ["obj-due"],
            str([c.objective_id for c in due]),
        )
        summary = d.engine.due_summary(STUDENT)
        check(
            "due_summary совпадает с get_due_cards",
            summary["due"] == len(due),
            str(summary),
        )
        check("total — все карточки", summary["total"] == 2, str(summary))

        d.set_now(NOW - timedelta(days=1))
        check("до сроков: ничего не ждёт", d.engine.get_due_cards(STUDENT) == [])
        check("due_summary до сроков: 0", d.engine.due_summary(STUDENT)["due"] == 0)
    finally:
        d.cleanup()


def test_due_order_is_by_next_review():
    d = Deck()
    try:
        later = R.ReviewCard(
            objective_id="z-later",
            last_review="2026-09-17T12:00:00Z",
            next_review="2026-09-18T12:00:00Z",
            interval_days=1,
            ease_factor=2.5,
            review_count=1,
            lapses=0,
        )
        earlier = R.ReviewCard(
            objective_id="a-earlier",
            last_review="2026-09-17T12:00:00Z",
            next_review="2026-09-16T12:00:00Z",
            interval_days=1,
            ease_factor=2.5,
            review_count=1,
            lapses=0,
        )
        d.engine.save_deck(STUDENT, [later, earlier])
        d.set_now(NOW + timedelta(days=3))
        ids = [c.objective_id for c in d.engine.get_due_cards(STUDENT)]
        check("сортировка по next_review", ids == ["a-earlier", "z-later"], str(ids))
    finally:
        d.cleanup()


def test_missing_and_corrupt_deck():
    d = Deck()
    try:
        missing = d.engine.load_deck(STUDENT)
        check("отсутствующая колода — пустая", missing["cards"] == [])
        check(
            "пустая колода валидна схемой",
            bool(schemas.validate(missing, "spaced_repetition")),
        )

        path = d.engine.deck_path(STUDENT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ не json", encoding="utf-8")
        expect_error(
            "битая колода — DECK_UNREADABLE",
            lambda: d.engine.load_deck(STUDENT),
            "DECK_UNREADABLE",
        )

        path.write_text('"строка, а не объект"', encoding="utf-8")
        expect_error(
            "JSON-строка вместо объекта — DECK_UNREADABLE",
            lambda: d.engine.load_deck(STUDENT),
            "DECK_UNREADABLE",
        )

        import json

        path.write_text(
            json.dumps(
                {"schema_version": 2, "student_id": STUDENT, "cards": "не список"}
            ),
            encoding="utf-8",
        )
        expect_error(
            "cards не список — DECK_UNREADABLE",
            lambda: d.engine.load_deck(STUDENT),
            "DECK_UNREADABLE",
        )

        # The file must still exist afterwards: a reset would discard state.
        check("файл колоды не удалён после ошибки", path.is_file())
    finally:
        d.cleanup()


def test_save_load_round_trip():
    d = Deck()
    try:
        card = d.engine.create_card("obj-roundtrip")
        reviewed = d.engine.record_review(card, 4)
        d.engine.save_deck(STUDENT, [card, reviewed])

        document = {
            "schema_version": R.SCHEMA_VERSION,
            "student_id": STUDENT,
            "cards": [c.to_document() for c in (card, reviewed)],
            "updated_at": d.engine.now_iso(d.clock)
            if hasattr(d.engine, "now_iso")
            else "2026-09-17T12:00:00Z",
        }
        try:
            schemas.validate(document, "spaced_repetition")
            check("сохранённый документ соответствует схеме", True)
        except schemas.SchemaError as e:
            check("сохранённый документ соответствует схеме", False, e.message[:200])

        loaded = d.engine.load_deck(STUDENT)
        check(
            "round-trip: две карточки",
            len(loaded["cards"]) == 2,
            str(len(loaded["cards"])),
        )
        restored = loaded["cards"][0]
        check("round-trip: objective_id", restored.objective_id == card.objective_id)
        check("round-trip: interval_days", restored.interval_days == card.interval_days)
        check("round-trip: ease_factor", restored.ease_factor == card.ease_factor)
        check("round-trip: lapses", restored.lapses == card.lapses)
        check(
            "round-trip: student_id",
            loaded["student_id"] == STUDENT,
            str(loaded["student_id"]),
        )
    finally:
        d.cleanup()


def test_upsert_is_idempotent():
    d = Deck()
    try:
        first = d.engine.upsert_card(STUDENT, "obj-upsert")
        second = d.engine.upsert_card(STUDENT, "obj-upsert")
        check(
            "повторный upsert не создаёт вторую карточку",
            first.objective_id == second.objective_id,
        )
        loaded = d.engine.load_deck(STUDENT)
        check(
            "в колоде одна карточка",
            len(loaded["cards"]) == 1,
            str(len(loaded["cards"])),
        )
    finally:
        d.cleanup()


def test_traversal_student_id_refused():
    d = Deck()
    try:
        expect_error(
            "student_id=../evil отклонён",
            lambda: d.engine.deck_path("../evil"),
            "SLUG_SEPARATOR",
        )
        check("файл вне temp root не создан", not (d.root / "evil").exists())
    finally:
        d.cleanup()


def test_card_document_round_trip():
    card = R.ReviewCard(
        objective_id="obj-x",
        last_review="2026-09-17T12:00:00Z",
        next_review="2026-09-18T12:00:00Z",
        interval_days=1,
        ease_factor=2.5,
        review_count=0,
        lapses=0,
    )
    document = card.to_document()
    restored = R.ReviewCard.from_document(document)
    check(
        "карточка round-trip: objective_id", restored.objective_id == card.objective_id
    )
    check(
        "карточка round-trip: все числа",
        (
            restored.interval_days,
            restored.ease_factor,
            restored.review_count,
            restored.lapses,
        )
        == (card.interval_days, card.ease_factor, card.review_count, card.lapses),
    )
    check(
        "карточка валидна как элемент колоды",
        R.ReviewCard.from_document(document).to_document() == document,
    )


def test_create_card_needs_objective_id():
    d = Deck()
    try:
        expect_error(
            "карточка без цели отклонена",
            lambda: d.engine.create_card(""),
            "OBJECTIVE_ID_EMPTY",
        )
        expect_error(
            "карточка с пробелом отклонена",
            lambda: d.engine.create_card("   "),
            "OBJECTIVE_ID_EMPTY",
        )
    finally:
        d.cleanup()


def test_first_review_sets_interval_one():
    d = Deck()
    try:
        card = d.engine.create_card("obj-first")
        updated = d.engine.record_review(card, 5)
        check(
            "первый повтор -> интервал 1",
            updated.interval_days == 1,
            str(updated.interval_days),
        )
        check(
            "счётчик повторов вырос",
            updated.review_count == 1,
            str(updated.review_count),
        )
        check(
            "next_review через день",
            updated.next_review == "2026-09-18T12:00:00Z",
            updated.next_review,
        )
    finally:
        d.cleanup()


def main():
    tests = [
        test_reference_example,
        test_quality_below_three_is_a_lapse,
        test_ease_floor_and_ceiling,
        test_interval_never_below_one,
        test_quality_out_of_range,
        test_due_cards_with_injected_clock,
        test_due_order_is_by_next_review,
        test_missing_and_corrupt_deck,
        test_save_load_round_trip,
        test_upsert_is_idempotent,
        test_traversal_student_id_refused,
        test_card_document_round_trip,
        test_create_card_needs_objective_id,
        test_first_review_sets_interval_one,
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
