---
name: contextual-live-help
description: Notice when a student is actually stuck - an unfixed compile error, a failing test with no new edits, a long idle stretch, edit thrashing on the same lines - and offer help at that moment, in the channel the work is already in, without interrupting an attempt that is going well. Use when the student has pasted a diff, an error log, or a test output and looks stalled, or when the agent is asked for help "while I'm working" rather than after a finished submission.
verified: 2026-09-30
---

# Contextual Live Help

Teaching arrives better when it arrives at the moment of stuckness, not after
the student has given up and reconstructed what happened. This skill describes
what to look for in work already in front of the agent, and how to offer help
without taking over.

**There is no IDE extension in this harness.** No VSCode extension, no Jupyter
extension, no editor plugin ships with the repository. What follows is
behaviour the agent applies when it has the work in front of it - a pasted
diff, a pasted log, a pasted test run - not an installed observer that reports
activity on its own. If nothing is pasted, the agent sees nothing; it does not
watch the student's machine.

## When to Use

- The student pastes an error, a diff, a test output, and stops producing
- The student asks for help while the attempt is open, not after submitting
- Two or three successive messages are the same failure re-explained
- The agent has been asked a teaching question while the work sits unresolved

## When NOT to Use

- **A finished attempt for review** - that is `giving-feedback`
- **A request to explain a concept** - that is `explaining-concepts`; the
  attempt can wait
- **The student is mid-thought and has not stalled** - a "quick note" breaks
  the thread; hold it

## Struggle signals, and how they are read

These are read from what the student pastes. None of them is certain alone;
two together are a fair offer.

| Signal | What it looks like in a paste | What it usually means |
|---|---|---|
| Unfixed compile error | The same error text, unchanged across two or more messages | They are editing elsewhere and the error is not where they are looking |
| Failing test, no new edits | Test output pasted again with no diff or no changed code between | They are guessing at the failure rather than narrowing it |
| Long idle stretch | A long gap, then the same problem restated with no new evidence | They stopped working on it and returned defeated |
| Edit thrashing | Diff where the same lines change back and forth without progress | A wrong model being retried in different disguises |

Thrashing detection is the clearest of the four: when the same lines change
back and forth and the failure signature does not move, the student is
retrying, not investigating. That is the moment for the lowest rung of help,
not for a full explanation.

## How to detect them from a paste

- Compare the error text across messages: identical `ename` plus identical
  message means nothing has been tried on that error yet.
- Compare the diff across messages: unchanged `hunk` content between two
  submissions means no new edit.
- Ask directly rather than infer past a point: «Смотрел ли ты на эту строку
  ещё раз или это тот же вывод, который ты показывал?» A direct question
  settles what inference would get wrong.
- Do not invent signals from silence the agent never observed. If the student
  went quiet for a reason you cannot see, treat it as neutral, not as struggle.

## The offer

Offer in Russian, on its own line, low commitment, no assumption that help was
wanted:

```
Похоже, ты застрял на этой ошибке. Хочешь, разберём её вместе, или
пока просто скажу, на какой шаг посмотреть?
```

Or, for the thrashing case:

```
Я вижу, что эти строки меняются туда-сюда. Дай отдохнуть от кода на
пару минут и попробуй описать задачу своими словами - я посмотрю, что
не так с подходом, а не с кодом.
```

Rules for the offer:

- **Offer, do not proceed.** One offer, then wait. A second offer without a
  reply is noise.
- **Least-assistance first.** Name the place to look, do not state the fix.
- **Respect the delivery preference** already recorded; the offer is the same,
  the rung is not.
- **Never take the keyboard.** The student types; the agent points.

## The assistance ceiling still applies

An offer made "in the moment" does not relax anything. The task's status comes
from the accepted contract, and the rung is re-checked the same as anywhere
else:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level HINT
```

On a graded or unknown assignment the moment of stuckness is the least moment
to reveal the answer. `policy.py` decides; this skill does not.

## Limitations

- No extension, no telemetry, no editor hook. The agent only ever sees what
  the student pastes or what the harness recorded in the progress record.
- "Long idle stretch" cannot be observed from a paste at all; where it is
  claimed, it comes from recorded session data
  (`risk_detection.RISK_FEATURE_LABELS.days_since_last_attempt`), not from the
  chat.
- Thrashing detection is a heuristic. Two edits to the same line can be
  legitimate; state what you saw rather than what you concluded when the two
  are close.
- Nothing here runs the student's code, re-runs their tests, or edits their
  files.
- `risk_detection.detect_at_risk` produces a signal for a consented human, and
  it is never a grade. An at-risk signal never enters a teaching decision as a
  verdict about the student.

## References

- `giving-feedback` - when the stuck attempt is finished and ready for review
- `providing-adaptive-scaffolding` - the ladder rung the offer escalates to
- `risk_detection.py` - `detect_at_risk`, `RISK_FEATURE_LABELS` (signal only,
  consent-gated, never a grade)
- `diagnosing-errors` - what to do once the offer is accepted

## Verify or update the progress record

1. Confirm the student's level for the task being discussed before offering
   live help.
2. Respect the recorded delivery preference; a "live" offer is still HINT by
   default.
3. Record that help was offered, whether it was taken, and what rung followed -
   the offer is evidence about engagement, not a verdict.
4. Never record a struggle signal as a grade or as a statement about the
   student's ability.
5. Deliver the offer and any following explanation in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when the harness
gains an editor integration or the risk-signal table changes. Added this
revision: the four struggle signals, how they are read from a paste, the offer
phrasings, and the honest statement that no IDE extension ships.
