---
name: scheduling-reviews
description: Schedule when each learning objective comes back - SM-2 quality 0..5, interval x ease, a 1/6/interval ladder - so review happens when forgetting is about to win, and treat a lapse as information about the interval rather than a penalty. Use when a session should include review, when a student asks "when should I come back to this", when the record shows objectives sitting unreviewed, or when a student forgot something they had clearly demonstrated.
verified: 2026-09-30
---

# Scheduling Reviews

Spaced repetition answers one question: what comes back, and when. The
implementation is `spaced_repetition.py`: `SM2Algorithm` as a pure static
function, `SpacedRepetitionEngine` holding per-learner decks under
`<deck_dir>/<student_id>/deck.json`, and `get_due_cards` returning what has
passed its `next_review`.

`REVIEW_INTERVALS` in `tutoring.py` is the built-in review ladder
`(2, 7, 21)` days, used as the coarse plan; the SM-2 engine refines it per card.

## When to Use

- Opening a session: run the due review before new material
- An objective was demonstrated and has not been reviewed since
- A student says they have forgotten something that was solid
- Planning a study week and nothing on the plan is due

## When NOT to Use

- **Teaching a prerequisite they never had** - that is a gap, not a forgotten
  review; route to `explaining-concepts`
- **A single question** - answer it; scheduling is for material that already
  landed
- **Deciding what to learn next** - `planning-study-sessions` does that, and it
  reads the due list

## SM-2 quality 0..5, with a Russian rubric

`quality` is an integer 0..5. Anything else raises
`SpacedRepetitionError("QUALITY_OUT_OF_RANGE")` - a non-integer quality would
silently become fractional through arithmetic.

| q | Rubric (what "this grade" means) | Result |
|---|---|---|
| 0 | «Не помню вообще, пришлось выучить заново.» | interval 1, **lapse** |
| 1 | «Есть ощущение, но воспроизвести не получилось.» | interval 1, **lapse** |
| 2 | «Пришлось напоминать и угадывать; уверенности нет.» | interval 1, **lapse** |
| 3 | «Пришлось напоминать, но после этого вспомнилось.» | keep going, ease drops |
| 4 | «Вспомнил после небольшой паузы, правильно.» | keep going |
| 5 | «Вспомнил сразу и правильно, без усилий.» | keep going, ease rises |

The rule that matters: `quality < 3` is a **lapse** and the card returns to
interval 1. Ease falls by the SM-2 term `ease + (0.1 - (5-q)*(0.08 + (5-q)*0.02))`,
floored at `MIN_EASE = 1.3`; that floor is what stops a run of blackouts from
driving the interval below one day. `MAX_EASE = 2.5` bounds the *multiplier*,
not the stored ease - clamping the ease itself at 2.5 would freeze it at its
starting value and a perfect streak would schedule the same interval forever.

## The interval ladder

`SM2Algorithm.calculate_next_interval(quality, current_interval, ease_factor,
is_first_review)`:

- `quality < 3` -> interval 1 (lapse)
- `quality >= 3`, first review -> interval 1
- `quality >= 3`, current interval 1 -> 6
- otherwise -> `int(current_interval * multiplier)`, never below 1

The coarse review plan the course uses is `tutoring.REVIEW_INTERVALS`:
`(2, 7, 21)` days - review at two days, again at a week, again at three weeks.
`get_due_cards` returns cards whose `next_review` has passed, sorted by
`next_review`, so the session opens with what is actually due rather than with
whatever is newest.

## Running a due-review session

1. `SpacedRepetitionEngine.get_due_cards()` - what is due now.
2. `due_summary()` - `{"due", "total", "next_due_at"}`, a small report the
   student can see.
3. Present each due card as a question, not as a recap. The recall attempt is
   the review.
4. Judge quality against the rubric, honestly. An answer the student produced
   with a nudge is quality 3-4, not 5.
5. `record_review(quality)` - returns a *copy* of the updated card; the stored
   object is untouched, so a review cannot be undone by re-reading the old one.
6. `save_deck()`. Then move to new material only if the due list is clear and
   capacity allows.

## A lapse is information, not a penalty

The record carries `lapses` per card. Say it in those terms: «Интервал снова
короткий - значит, к этой теме стоит вернуться раньше.» Never frame it as a
mistake to be corrected by more effort, and never treat a lapse as evidence
that the objective was not really demonstrated - it is evidence about the
*interval*, and the fix is scheduling, not scolding.

## Limitations

- `SpacedRepetitionEngine` stores decks on disk and loads them per student.
  It does not know what the student actually recalled; the quality grade is the
  agent's or the student's honest judgement, and a self-graded quality 5 on
  material they cannot recall will mis-schedule the card.
- `quality` must be an integer in 0..5. The engine raises on anything else; the
  agent must not round.
- Ease starts at `DEFAULT_EASE = 2.5`. A card created today has no history, and
  the first review always lands at interval 1 - that is by design, not a bug.
- Nothing here fetches material to review. The deck says *when*; the content
  comes from the course track.
- `tutoring.evaluate_mastery` is a separate reducer that decides whether an
  objective moves to `demonstrated`; it does not schedule reviews. Conflating
  the two - treating a mastered objective as "no longer due" - is exactly the
  mistake the module's docstring warns against.

## References

- `spaced_repetition.py` - `SM2Algorithm`, `SpacedRepetitionEngine`,
  `ReviewCard`, `get_due_cards`, `record_review`
- `tutoring.py` - `REVIEW_INTERVALS`, `evaluate_mastery`
- `planning-study-sessions` - the study plan that consumes the due list
- `maintaining-course-progress` - where objective stages live
- `scheduling-reviews` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for this topic before reviewing it; a due card for a
   prerequisite the student never had is a gap, not a review.
2. Respect the recorded delivery preference during the review session.
3. Record each review: the objective, the quality 0..5, the new interval, and
   whether the card lapsed - the interval history is the evidence.
4. Never let a lapse become a grade or a note about the student's ability;
   record it as a scheduling signal.
5. Deliver the review summary in Russian (`language-and-translation`), and
   carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`spaced_repetition.py`, `tutoring.REVIEW_INTERVALS`, or the course objectives
change. Added this revision: the SM-2 quality rubric in Russian, the
1/6/interval ladder and the 2/7/21 review plan, the session procedure, and the
lapse-is-information rule.
