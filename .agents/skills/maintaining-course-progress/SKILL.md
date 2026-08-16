---
name: maintaining-course-progress
description: Keep the durable record that outlives a session — the cohort's level, each student's baseline, delivery preference (hints vs full solutions), graded vs practice split, what was demonstrated, what was difficult, and open questions — so learning spanning days or multiple students does not restart or contradict itself. Use when a course runs longer than one sitting, when resuming work after a gap, when switching between students, before a context window rolls over, or when you cannot say where a student's level was last recorded.
verified: 2026-08-16
---

# Maintaining Course Progress

A course lasts weeks. A session does not. The gap between those two facts is
where students get retaught what they already know, graded tasks get mixed up
with practice, preferences get forgotten, and nobody notices that one student
has been stuck on the same objective for three sessions.

This is not administrative overhead bolted onto the teaching. **The progress
record is what lets the next session pick up without asking the student to
reprove their level, and it is what lets a teacher see mastery rather than
activity.** Neither can be reconstructed afterwards from chat scrollback.

Assume everything not written to the record is lost: not merely forgotten, but
*silently* lost, so the next session confidently re-teaches it or contradicts
it.

## When to Use

- Any course or tutoring relationship continuing past a single sitting
- Resuming after a break, or picking up another agent's record
- Approaching a context boundary with unrecorded state
- Switching between multiple students or multiple courses
- Before teaching a topic the student has met before
- When you cannot answer "what did this student already demonstrate?"

## When NOT to Use

- **Writing a progress report for the student or a teacher** — use
  `reporting-learning-progress`; this skill produces the raw material that
  skill consumes
- **Judging whether a concept is actually understood** — that is
  `assessing-understanding`; record the outcome here either way
- **A single self-contained tutoring question** finished in one sitting with
  nothing the next session needs to know

## The Six Records

Keep these in one file per course (see `templates/progress-file.md`), in the
`progress/` directory, updated as you go — not reconstructed at the end.

### 1. Cohort and consent

The students on this course, each with: their **baseline level** for the course
prerequisites (stated, or recorded as `not-stated`), how they want to be
assessed (tested vs self-assessed), and their **delivery preference**
(`hints` | `hints-then-solution` | `solution-first`). Copy the student's own
words where they chose to self-assess; paraphrasing a level is how students get
taught over their head.

Record one more field, because "stated" and "verified" are different things.
**Consent status** says whether the student-consent gate has been completed for
this session:

| Status | Meaning | Refused |
| --- | --- | --- |
| `consented` | Level + preference + graded split confirmed this session | Teaching before confirming |
| `defaulted` | Student declined to state a level | Guessing a level, full solutions by default |
| `partial` | Some fields confirmed | Assuming the unconfirmed ones |

Name the status before the first teaching exchange, not after a lesson has been
pitched at the wrong level. When this skill says a session needs a gate, it
means `consented` or an explicit `defaulted`.

The gate's outputs are **session-settled decisions** (AGENTS.md): each recorded
answer carries a provenance annotation — `session-settled: user-approved` (the
student explicitly chose it), `session-settled: user-directed` (the student
directed it, e.g. "don't test me, I'll self-assess"), or `assumed` (the agent's
fallback, e.g. `prefer-ask`). Downstream skills (`giving-feedback`,
`breaking-down-assignments`, `planning-study-sessions`) read these and do not
re-ask them. A decision changes only on new evidence from the student, recorded
as a new annotated entry with its own date — never silently overwritten.

### 2. Per-student baseline and progress

For each student and each module/objective: what they demonstrated, on what
date, in what mode (co-learning / tutoring / supplement), and the current
mastery state (`new` | `learning` | `practising` | `mastered` | `blocked`).

```
stu-04 | Marina | module 3: recursion
        | baseline: comfortable with loops, no recursion yet
        | demonstrated: wrote the base case for the tree traversal herself
        | status: learning  | last: 2026-08-12
```

Progress is not bookkeeping. It answers three questions you will be asked:

- **What is this student's level now?** A record that says "taught recursion
  last week" but not "still cannot write a base case" will re-teach the wrong
  thing.
- **Was this demonstrated, or just covered?** "We did the example" is not "the
  student can do it." Only mark an objective `mastered` when the student has
  produced a correct attempt on their own.
- **What is blocked?** A `blocked` objective is a signal to stop and fix the
  prerequisite before pushing new material.

### 3. Delivery preference

Per student, what was agreed for feedback: hints only, hints then full
solution, or full solutions immediately — and whether it changed. If nothing
was agreed, record `prefer-ask` and default to hints.

### 4. Graded vs practice split

Which assignments are **graded** (fall under the "no ready answers" rule in
AGENTS.md) and which are free practice. This list is the scope boundary for
`giving-feedback` and `breaking-down-assignments`: a graded task is never solved
for the student; a practice task may be demonstrated after engagement.

```
graded   : ex-02 (essay), ex-05 (quiz), final-project
practice : ex-01, ex-03, ex-04, lab track
```

Written afterwards from memory, this list is always incomplete, and what it
omits gets treated as graded when it was meant as practice (or vice versa). Two
people need it: you, to stay on the right side of the gate; and the teacher, to
know what the agent may and may not produce.

### 5. Session notes

Per session: date, mode(s) used, module and objectives covered, what the
student demonstrated, what was difficult, and open questions. Capture notes
**as you work** — the detail that proved a level will not survive the next
session's summarization.

### 6. Dead ends and open questions

The record most often skipped and most valuable across sessions: what was
tried, against what, and why it did not work.

```
dead-03 | explained recursion with the call-stack diagram | confused, not the issue
open-04 | student's prerequisite on pointers is unverified - check before module 4
```

Without this, the next session re-explains the same diagram, and a question the
student raised gets lost.

## Session Boundary Discipline

Before a session ends — planned or not — the record must answer:

1. What did this student demonstrate, and at what mastery level?
2. What is the delivery preference, and is it current?
3. What was I in the middle of?
4. What did I rule out, so nobody repeats it?
5. What is the next objective to teach, and are its prerequisites verified?

Write these down *as you work*, not at the end. The session that ends
unexpectedly is precisely the one whose state was never captured.

## Handoff

A handoff is the same record plus three additions: current position, the
immediate next action, and anything time-sensitive (a graded deadline, a
student's request to review before Friday). If the receiving agent has to ask
"what were we doing?", the handoff failed.

## Across Students: the Teaching Journal

The six records reset per course or per student. Two things are worth carrying
between them, and both fail the same way — reconstructed from memory, they are
wrong.

**What this student's environment can actually do.** Before a skill runs a tool
or presumes an installed dependency, the tool has to exist at a known path and
version. Guessing that a student has Jupyter, a compiler, or network access and
debugging the failure as if it were the material's fault is a standard way to
lose a session. Keep a local, machine-specific inventory per student — tool,
real path, version, and whether it is not just installed but *reachable*.
Present and ready are different states.

**What past sessions taught.** A short, de-identified journal entry per
finished session — the scenario, the explanation that worked, the dead ends
and the hours they cost, the tooling surprise — turns the next similar session
from a cold start into a lookup. Keep an index over the entries by topic, by
technique, and by student trait, and read *that* before starting new work, not
the entries themselves; the index is the cheap part.

Two disciplines make the journal safe to keep. **De-identify at write time**,
not before sharing — real names, course codes, and identifying details become
placeholders the moment the entry is written, so an entry that is never meant
to leave still cannot leak if it does. And **keep it local by default**:
contributing lessons to a shared or public location ships student detail to a
third party, and "the model anonymised it" is not a control you would defend
to a parent or teacher. If a journal is ever shared, that is a deliberate,
reviewed decision per entry — not a default step in the workflow.

## Rationalizations to Reject

- *"I'll remember where that student is."* You will not, and neither will the
  next session. An unrecorded level is indistinguishable from a guessed one.
- *"It's all in the chat log."* The log has no reasoning, no dates you can
  trust, no preferences, and no record of what the student demonstrated. It is
  also routinely lost with the session.
- *"I'll write the notes at the end."* The end is exactly when memory is worst
  and the session longest. Notes written from memory are always incomplete.
- *"Nobody needs my dead ends."* They are the difference between a reported
  negative and an unexamined gap, and they stop the next session re-explaining
  the same diagram.
- *"Recording failure looks bad."* A progress record with no `blocked` entries
  looks unobservant, not successful. Coverage is a finding.

## References

- `reporting-learning-progress` — consumes this record to produce the
  deliverable
- `assessing-understanding` — how to verify a level before it is recorded
- `planning-study-sessions` — what to schedule once the record is current
- `schemas/progress-entry.json` — structured shape of a session entry

## Last Validated

2026-08-16. Procedure and six-records structure current as of this date;
re-verify when the progress template or consent-gate rules change.
