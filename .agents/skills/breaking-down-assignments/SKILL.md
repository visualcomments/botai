---
name: breaking-down-assignments
description: Decompose an assignment into steps without solving it for the student — identify the pieces, the order, the prerequisite knowledge each piece assumes, and the checkpoints where the student can self-verify. Use when a student is stuck on an assignment, when an assignment looks overwhelming, or when the student asks "how do I even start?".
verified: 2026-08-12
---

# Breaking Down Assignments

The skill that makes "I don't know how to start" tractable. It produces a map
of the assignment — steps, order, prerequisites, checkpoints — and never the
answer itself. For a graded task the breakdown stops one step short of a
solution; for practice it may demonstrate one analogous piece.

## When to Use

- A student says "I don't know where to start" on an assignment
- An assignment spans multiple skills or has hidden prerequisites
- A student has tried and failed; the failure point needs isolating
- The student explicitly asks for the assignment to be broken into steps

## When NOT to Use

- **Solving the assignment** — never. For graded tasks a finished solution is a
  refusal under AGENTS.md; for practice, demonstrate an analogy instead
- **Explaining a concept the assignment depends on** — that is
  `explaining-concepts`; this skill stays at the level of the task's structure
- **Deciding whether the work is correct** — that is `giving-feedback` after
  the attempt

## Method

1. **Read the assignment once, completely.** Note what is asked, what is
   supplied, and what is to be produced.
2. **Identify the steps.** Ask: what must the student be able to do, in what
   order, to reach the deliverable? Name each step as a verb phrase ("parse the
   input", "state the base case").
3. **Map prerequisites per step.** For each step, what earlier course material
   does it assume? Flag any prerequisite that is `unstated` or unverified for
   this student.
4. **Order and reorder.** Steps must follow the student's own logic where
   possible; the student's proposed order beats a textbook order when both
   work.
5. **Add checkpoints.** Between steps, what can the student check themselves
   without an answer key? A checkpoint is a question ("what should this output
   for input X?") or a mini-verification, not an answer.
6. **Leave the last step open.** Present the breakdown ending in "the final
   step is yours" for graded tasks; do not narrate the conclusion.

## Output shape

Present the breakdown as:

```
Assignment: <title>   [graded | practice]

1. <step>             (needs: <prereq>)   checkpoint: <self-check>
2. <step>             (needs: <prereq>)   checkpoint: <self-check>
...
Last step: your turn. Show me your attempt at step N and I will review it.
```

## Guidance

- **One step per line.** If a step needs two sentences, it is two steps.
- **Name the risk.** If one step is the likely sticking point, say so plainly:
  "most people get stuck on step 3; here is a way to check you are on track."
- **Never read ahead to the answer.** If the course has a solution note, treat
  it as gated material and do not consult it to build the breakdown.

## References

- `explaining-concepts` — when a step's prerequisite turns out to be missing
- `giving-feedback` — review the attempt the breakdown produces
- `maintaining-course-progress` — record which steps were demonstrated

## Last Validated

2026-08-12. Procedure current as of this date; re-verify when the course material or teaching rules change.
