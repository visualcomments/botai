# -*- coding: utf-8 -*-
"""SM-2 spaced repetition: when each learning objective is due next.

The deck *schedules* reviews; it never decides mastery. Whether a learner
knows something is still a question for the progress record and its evidence
(`tutoring.evaluate_mastery`), not for an interval timer — conflating the two
would let a lucky review stand in for two different passing checks.

Two properties this module leans on:

* **A corrupt deck is never silently reset.** `load_deck` raises
  `SpacedRepetitionError("DECK_UNREADABLE", ...)` on a parse failure. A deck
  is a learner's own scheduling state; wiping it because a write was
  interrupted would discard work, and the harness's update rule — never
  discard work — applies here too.
* **Timestamps are aware UTC with a trailing `Z`**, produced and parsed by
  `tutoring.now_iso` / `tutoring.parse_iso` so every module in the core agrees
  on what a timestamp means. A naive `datetime` compared against an aware one
  is a `TypeError`, not a scheduling decision.

The SM-2 ease update is `ease + (0.1 - (5-q)*(0.08 + (5-q)*0.02))`. Ease is
floored at `MIN_EASE = 1.3`, and `MAX_EASE = 2.5` bounds the interval
multiplier rather than the stored ease value — see `calculate_next_interval`
for why clamping the returned ease at 2.5 would freeze it at its starting
value and kill the growth term the algorithm exists for. The floor keeps a
run of failures from shrinking the interval below one day (a sub-day interval
is not spaced repetition).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths, schemas, tutoring
from .tutoring import now_iso, parse_iso

SCHEMA_VERSION = 2

# Re-exported for callers that build cards without importing tutoring.
DEFAULT_EASE = 2.5


class SpacedRepetitionError(RuntimeError):
    """A refused review or deck operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ReviewCard:
    """One objective's scheduling state. All fields are timing, not mastery."""

    objective_id: str
    last_review: str
    next_review: str
    interval_days: int
    ease_factor: float
    review_count: int
    lapses: int

    def to_document(self):
        """The dict matching `spaced_repetition.schema.json`'s `review_card`."""
        return {
            "objective_id": self.objective_id,
            "last_review": self.last_review,
            "next_review": self.next_review,
            "interval_days": int(self.interval_days),
            "ease_factor": round(float(self.ease_factor), 6),
            "review_count": int(self.review_count),
            "lapses": int(self.lapses),
        }

    @classmethod
    def from_document(cls, document):
        document = document or {}
        return cls(
            objective_id=str(document.get("objective_id") or ""),
            last_review=str(document.get("last_review") or ""),
            next_review=str(document.get("next_review") or ""),
            interval_days=int(document.get("interval_days") or 1),
            ease_factor=float(document.get("ease_factor") or DEFAULT_EASE),
            review_count=int(document.get("review_count") or 0),
            lapses=int(document.get("lapses") or 0),
        )


class SM2Algorithm:
    """SuperMemo-2 interval and ease update, as a pure static function."""

    MIN_EASE = 1.3
    MAX_EASE = 2.5
    INITIAL_EASE = 2.5

    @staticmethod
    def calculate_next_interval(
        quality, current_interval, ease_factor, is_first_review
    ):
        """`(new_interval_days, new_ease_factor)` for one review.

        `quality` is an int 0..5 (0 = complete blackout, 5 = perfect).
        Anything else raises `SpacedRepetitionError("QUALITY_OUT_OF_RANGE")`:
        a non-integer quality would silently become a float through arithmetic
        and schedule a review on a fractional day nobody asked for.

        Rules:
        * ease update `ease + (0.1 - (5-q)*(0.08 + (5-q)*0.02))`, floored at
          `MIN_EASE`. The floor is what keeps a run of failures from shrinking
          the interval below one day: SM-2's raw term subtracts up to 0.5 per
          blackout, so without it the multiplier could go negative and the
          "review tomorrow" case would break.
        * `quality < 3` -> interval 1 and the card is a **lapse** (the caller
          increments `lapses`).
        * `quality >= 3`: first review -> 1; `current_interval == 1` -> 6;
          else `int(current_interval * multiplier)`.
        * the result is always >= 1 - never 0.

        **On `MAX_EASE`.** `MAX_EASE = 2.5` bounds the *interval multiplier*,
        not the returned ease, and this is a deliberate reading rather than an
        oversight. `INITIAL_EASE` is also 2.5, so clamping the returned ease at
        2.5 would make ease permanently equal to its starting value: the
        growth term could never fire, the multiplier would never exceed 1, and
        a perfect streak would schedule the same interval forever. That is
        the opposite of spaced repetition. Bounding the multiplier instead
        keeps the schedule realisable (a streak cannot multiply the interval
        without limit) while leaving the algorithm's growth signal intact.
        The reference example that pins this behaviour is
        `calculate_next_interval(5, 6, 2.5, False) == (15, 2.6)`.
        """
        if isinstance(quality, bool) or not isinstance(quality, int):
            raise SpacedRepetitionError(
                "QUALITY_OUT_OF_RANGE",
                "качество ответа должно быть целым 0..5, получено %r" % (quality,),
            )
        if quality < 0 or quality > 5:
            raise SpacedRepetitionError(
                "QUALITY_OUT_OF_RANGE",
                "качество ответа вне диапазона 0..5: %d" % quality,
            )

        try:
            interval = int(current_interval)
        except (TypeError, ValueError) as e:
            raise SpacedRepetitionError(
                "INTERVAL_INVALID",
                "текущий интервал должен быть целым числом дней: %r"
                % (current_interval,),
            ) from e
        if interval < 1:
            interval = 1

        try:
            ease = float(ease_factor)
        except (TypeError, ValueError) as e:
            raise SpacedRepetitionError(
                "EASE_INVALID",
                "ease factor должен быть числом: %r" % (ease_factor,),
            ) from e

        # Ease update. (5-q) is the "penalty" term: q=5 adds 0.1, q=0
        # subtracts 0.5. The floor is a hard clamp (see the docstring for why
        # the ceiling is not).
        penalty = 5 - quality
        new_ease = ease + (0.1 - penalty * (0.08 + penalty * 0.02))
        if new_ease < SM2Algorithm.MIN_EASE:
            new_ease = SM2Algorithm.MIN_EASE
        # Rounded to 6 places so a long streak accumulates no float noise:
        # 2.5 + 0.1 must be exactly 2.6, not 2.6000000000000005, or the
        # reference example in this module's docstring stops holding.
        new_ease = round(new_ease, 6)

        # The ceiling bounds the interval multiplier, not the stored ease.
        multiplier = min(new_ease, SM2Algorithm.MAX_EASE)

        if quality < 3:
            # Forgot: restart the ladder. The caller records a lapse.
            new_interval = 1
        elif is_first_review:
            new_interval = 1
        elif interval == 1:
            new_interval = 6
        else:
            new_interval = int(interval * multiplier)

        # Floor of 1 day: a sub-day interval is not a spaced review, and the
        # schema requires `interval_days >= 1`.
        if new_interval < 1:
            new_interval = 1

        return new_interval, new_ease


def _clamp_float(value, low, high):
    return max(low, min(high, float(value)))


def _now(moment):
    """A timezone-aware UTC datetime from an injected clock or the wall clock."""
    value = datetime.now(timezone.utc)
    if callable(moment):
        raw = moment()
        if isinstance(raw, datetime):
            value = raw
    elif isinstance(moment, datetime):
        value = moment
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Deck storage
# ---------------------------------------------------------------------------

DECK_FILENAME = "deck.json"


def deck_dir(root=None):
    """`<root>/reviews` when that directory exists, else the repo `reviews/`.

    Mirrors `personas.personas_dir` / `learner_profiling.profiles_dir`: the
    path is resolved from this file, never from the working directory.
    """
    if root:
        candidate = Path(root) / "reviews"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "reviews"


_default_deck_dir = deck_dir


class SpacedRepetitionEngine:
    """Per-learner SM-2 decks, stored under `<deck_dir>/<student_id>/deck.json`."""

    def __init__(self, deck_dir=None, *, root=None, clock=None):
        if deck_dir is None:
            self._deck_dir: Path = _default_deck_dir(root)
        elif isinstance(deck_dir, (str, Path)):
            self._deck_dir = Path(deck_dir)
        else:
            self._deck_dir = Path(deck_dir(root))
        self._root = root
        self._clock = clock

    # -- paths ------------------------------------------------------------
    def _learner_dir(self, student_id):
        try:
            name = paths.safe_name(student_id, kind="идентификатор обучающегося")
        except paths.PathError as e:
            raise SpacedRepetitionError(e.code, e.message)
        base = self._deck_dir
        base.mkdir(parents=True, exist_ok=True)
        try:
            contained = paths.ensure_within(base, base / str(name))
        except paths.PathError as e:
            raise SpacedRepetitionError(e.code, e.message)
        return Path(contained)

    def deck_path(self, student_id):
        """`reviews/<student_id>/deck.json`."""
        return self._learner_dir(student_id) / DECK_FILENAME

    # -- cards ------------------------------------------------------------
    def create_card(self, objective_id):
        """A fresh card for `objective_id`, due in one day."""
        if not objective_id or not str(objective_id).strip():
            raise SpacedRepetitionError(
                "OBJECTIVE_ID_EMPTY",
                "у карточки повторения должен быть идентификатор цели",
            )
        moment = _now(self._clock)
        return ReviewCard(
            objective_id=str(objective_id),
            last_review=now_iso(self._clock),
            next_review=now_iso(lambda: moment + timedelta(days=1)),
            interval_days=1,
            ease_factor=SM2Algorithm.INITIAL_EASE,
            review_count=0,
            lapses=0,
        )

    def record_review(self, card, quality):
        """A *copy* of `card` after one review. The caller's object is untouched.

        Mutating the input would let a failed review be "undone" by reading the
        old object again, and would make the caller's object's identity carry
        state the store has not seen.
        """
        if not isinstance(card, ReviewCard):
            raise SpacedRepetitionError(
                "CARD_INVALID",
                "ожидалась карточка ReviewCard, получено %s" % type(card).__name__,
            )
        is_first = card.review_count == 0
        new_interval, new_ease = SM2Algorithm.calculate_next_interval(
            quality=quality,
            current_interval=card.interval_days,
            ease_factor=card.ease_factor,
            is_first_review=is_first,
        )
        moment = _now(self._clock)
        lapses = card.lapses
        if isinstance(quality, int) and not isinstance(quality, bool) and quality < 3:
            lapses = card.lapses + 1

        return ReviewCard(
            objective_id=card.objective_id,
            last_review=now_iso(self._clock),
            next_review=now_iso(lambda: moment + timedelta(days=new_interval)),
            interval_days=new_interval,
            ease_factor=new_ease,
            review_count=card.review_count + 1,
            lapses=lapses,
        )

    # -- deck I/O ---------------------------------------------------------
    def load_deck(self, student_id):
        """`{"schema_version", "student_id", "cards": [ReviewCard, ...]}`.

        Missing file → empty deck. Corrupt file → `DECK_UNREADABLE`: never a
        silent reset, because a reset would discard the learner's own
        scheduling state.
        """
        path = self.deck_path(student_id)
        if not path.is_file():
            return {
                "schema_version": SCHEMA_VERSION,
                "student_id": student_id,
                "cards": [],
            }
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise SpacedRepetitionError(
                "DECK_UNREADABLE",
                "колода повторений повреждена и не сброшена: %s (%s). "
                "Восстановите файл из резервной копии или проверьте его вручную."
                % (path, e),
            )
        if not isinstance(document, dict):
            raise SpacedRepetitionError(
                "DECK_UNREADABLE",
                "колода повторений повреждена: ожидался JSON-объект, получено %s"
                % type(document).__name__,
            )
        raw_cards = document.get("cards")
        if raw_cards is None:
            raw_cards = []
        if not isinstance(raw_cards, list):
            raise SpacedRepetitionError(
                "DECK_UNREADABLE",
                "колода повторений повреждена: поле cards должно быть списком",
            )
        cards = []
        for entry in raw_cards:
            if not isinstance(entry, dict):
                raise SpacedRepetitionError(
                    "DECK_UNREADABLE",
                    "колода повторений повреждена: карточка должна быть объектом",
                )
            cards.append(ReviewCard.from_document(entry))
        return {
            "schema_version": SCHEMA_VERSION,
            "student_id": str(document.get("student_id") or student_id),
            "cards": cards,
        }

    def save_deck(self, student_id, cards):
        """Validate and write the deck document. Returns the path."""
        cards = list(cards or [])
        document = {
            "schema_version": SCHEMA_VERSION,
            "student_id": student_id,
            "cards": [card.to_document() for card in cards],
            "updated_at": now_iso(self._clock),
        }
        try:
            schemas.validate(document, "spaced_repetition")
        except schemas.SchemaError as e:
            raise SpacedRepetitionError(e.code, e.message)

        path = self.deck_path(student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def upsert_card(self, student_id, objective_id):
        """Ensure a card for `objective_id` exists; return it (created if new)."""
        deck = self.load_deck(student_id)
        for card in deck["cards"]:
            if card.objective_id == objective_id:
                return card
        card = self.create_card(objective_id)
        deck["cards"].append(card)
        self.save_deck(student_id, deck["cards"])
        return card

    def get_due_cards(self, student_id):
        """Cards whose `next_review` has passed, sorted by `next_review`."""
        deck = self.load_deck(student_id)
        moment = _now(self._clock)
        due = []
        for card in deck["cards"]:
            when = parse_iso(card.next_review)
            if when is None:
                continue
            if when <= moment:
                due.append(card)
        due.sort(
            key=lambda card: (parse_iso(card.next_review) or moment, card.objective_id)
        )
        return due

    def due_summary(self, student_id):
        """`{"due", "total", "next_due_at"}` — a small report for the caller."""
        deck = self.load_deck(student_id)
        moment = _now(self._clock)
        due_count = 0
        next_due = None
        for card in deck["cards"]:
            when = parse_iso(card.next_review)
            if when is None:
                continue
            if when <= moment:
                due_count += 1
            if next_due is None or when < next_due:
                next_due = when
        return {
            "due": due_count,
            "total": len(deck["cards"]),
            "next_due_at": (
                next_due.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                if next_due
                else None
            ),
        }


__all__ = [
    "SpacedRepetitionError",
    "ReviewCard",
    "SM2Algorithm",
    "SpacedRepetitionEngine",
    "deck_dir",
    "DECK_FILENAME",
    "DEFAULT_EASE",
    "now_iso",
    "parse_iso",
]
