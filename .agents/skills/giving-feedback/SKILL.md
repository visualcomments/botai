---
name: giving-feedback
description: Review a student's attempt and give feedback that is actionable — what is right, what needs work, how to fix it themselves — while respecting the delivery preference and never giving a ready answer to a graded task. Use when the student submits an attempt for review, when the student is stuck after several tries, or when a teacher asks for a review of a submission against a rubric.
verified: 2026-08-16
---

# Giving Feedback

Feedback is where the student's attempt meets the course's standard. The
product of this skill is a review the student can act on: what is right (so it
is not rewritten), what needs work, and how to improve it themselves. For
graded tasks the review ends at the fix, never at the solved task.

## When to Use

- The student submits an attempt for review
- A student has tried several times and is stuck — the review becomes a
  diagnosis of where the attempt breaks
- A teacher asks for a review of a submission against the course rubric

## When NOT to Use

- **Doing the work** — a review is written after an attempt exists; it never
  precedes one
- **Explaining a concept the attempt shows is missing** — that is
  `explaining-concepts`, and it comes first
- **Deciding the next thing to teach** — that is `planning-study-sessions`;
  this skill reports, that skill schedules

## Method

1. **Read the attempt fully before judging.** The student's approach may be
   valid where it differs from the expected one.
2. **Start with what is right.** Name specific correct parts first. Students
   act on feedback they trust, and "what is right" anchors the rewrite.
3. **Name the specific gap.** "The loop condition exits early on empty input" —
   not "the logic is wrong".
4. **Give the how-to-fix, not the fix.** For each gap, give a strategy the
   student can run: "add a test with an empty input and see what the loop does".
   For graded tasks stop here.
5. **Respect the delivery preference.** Hints by default; full solutions only
   where the student chose `solution-first` and the task is practice. The
   preference is a session-settled decision recorded in the progress record —
   read it, do not re-ask it (see AGENTS.md "Session-settled decisions").
6. **Record the review and the attempt** in the progress file (raw attempt
   stays with the analysis).

## Feedback shape

```
What is right:
  <specific correct parts>

What needs work:
  <specific gap 1>  →  how to find and fix it yourself: <strategy>
  <specific gap 2>  →  ...

For graded tasks: the fix is yours. Attempt the strategy and send the result.
For practice (or where solution-first was agreed): here is the full solution
for comparison.
```

## Rules

- **One gap at a time.** If there are five, order them and say: fix the first
  and resubmit.
- **Never write the graded deliverable.** Not the essay sentence, not the
  function body, not the quiz answer. Work through an analogous example instead
  (`explaining-concepts`).
- **Be honest about uncertainty.** If the rubric is ambiguous, say so and ask
  the teacher rather than guessing the standard.
- **Reject the urge to grade harshly or softly.** Feedback is about the next
  attempt, not the ego.

## Tag every item by assistance level

Tag each point of feedback with how much assistance it provides, mirroring
SECS's noise tags. The tag tells the student (and the record) how far you
went, and keeps least-assistance-first honest:

- **HINT** — a nudge that does not reveal the answer ("check what the loop
  does on an empty input"). Default for graded tasks.
- **EXAMPLE** — an analogous worked case the student maps onto their own work.
  Escalate here when a hint has not unblocked.
- **SOLUTION** — the full answer. Only for practice tasks where
  `solution-first` was agreed, and only after the attempt.

A graded task may use HINT and EXAMPLE but never SOLUTION. If a review needs
SOLUTION for a graded task, that is a stop-and-ask: escalate to the teacher,
do not write it.

## Invariants to check before feedback leaves draft

Four checks adapted from SECS `reporting-security-findings`. Run them over
every review before you send it; each maps to a way feedback has actually gone
wrong:

1. **Every point cites the attempt.** A claim that the student "did X wrong"
   must point at the specific line, output, or sentence — not a general
   impression. Feedback whose only support is a feeling is a *comment*, not
   feedback; label it so.
2. **Confidence and certainty cannot disagree.** If you would not bet on a gap
   being real, do not write it as a fact — say "check whether …" and name what
   would settle it. Certain phrasing with low confidence is how a wrong review
   acquires authority.
3. **Every strategy is runnable without a follow-up question.** "Review the
   loop" is not a strategy; "add a print inside the loop and run it with an
   empty input" is. If the student would have to ask what you meant, rewrite it.
4. **A claim of mastery or blockage has evidence from the attempt.** "Mastered"
   needs a correct unaided attempt; "blocked" needs a demonstrated failure
   point. Reachability is not mastery.

When a point fails one of these, it does not get dropped silently: it moves to
an open question with the reason attached, so the next session knows what
would promote it.

## References

- `breaking-down-assignments` — when the feedback reveals the student needs the
  task restructured
- `assessing-understanding` — checks to confirm the fix landed
- `maintaining-course-progress` — record the review and the demonstrated level

## Last Validated

2026-08-16. Procedure current as of this date; re-verify when the course material or teaching rules change.
