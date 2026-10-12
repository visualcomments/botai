---
name: reporting-learning-progress
description: Produce progress reports for the student or a teacher from the durable progress record — overview of the track, what is complete, in progress, blocked, mastery per objective, and recommended next steps. Use when the student or teacher asks for a summary, at the end of a module, or when someone needs a view of the cohort's state.
verified: 2026-08-12
---

# Reporting Learning Progress

The report is a view over the record, not a new activity log. It tells the
reader what has been mastered, what is in progress, what is blocked, and what
to do next — in a shape that serves the audience: the student (their next
steps) or the teacher (the cohort's state).

## When to Use

- The student asks "how am I doing?"
- A teacher asks for a progress report
- End of a module or a milestone
- A handoff where someone new needs the state at a glance

## When NOT to Use

- **Producing the raw record** — that is `maintaining-course-progress`; this
  skill consumes it
- **Evaluating a specific submission** — that is `giving-feedback`
- **Fabricating coverage** — the report reports the record; if the record is
  thin, say so

## Report shape

```
# Progress: <course> for <student | cohort>

Overview: <one paragraph - where the student/cohort is, in plain terms>

Track: <module> - <status (complete / in progress / blocked)>
Mastery per objective: <objective> - <new / learning / practising / mastered>

What is blocked: <objective> - <why, per the record> - <recommended action>

Recommended next steps:
1. <action>   (why)
2. <action>

Open questions: <from the record>
```

## Rules

- **Report mastery, not activity.** "Covered module 2" is activity; "can
  reverse a list unaided" is mastery. The record stores the second; the report
  reports it.
- **Be honest about evidence.** An objective marked `learning` on a claim
  rather than a demonstration is reported as unverified, not as progress.
- **No over-reporting.** One page tells the story; appendices are for detail.
- **Audience-aware.** The student gets next steps; the teacher gets state and
  blockages. Do not average the two into prose that serves neither.
- **Never report graded answers or student work.** The report is about
  mastery, not content reproduction.

## References

- `maintaining-course-progress` — the source this report reads
- `planning-study-sessions` — the "recommended next steps" come from the plan
- `giving-feedback` — per-submission detail, separate from this overview

## Last Validated

2026-08-12. Procedure current as of this date; re-verify when the course material or teaching rules change.
