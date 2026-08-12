---
name: creating-practice-exercises
description: Generate additional practice matched to the course's learning objectives — extra problems, quick checks, and micro-quiz items — at the right difficulty for the student, labeled as practice and separate from graded work. Use when a student needs more reps on an objective, when preparing assessment items for assessing-understanding, or when the course has too few exercises on a topic.
verified: 2026-08-12
---

# Creating Practice Exercises

Practice is what turns "understood" into "can do". This skill generates
additional exercises matched to the course's stated objectives, at a difficulty
that fits the student's recorded level — and keeps them clearly separated from
graded work.

## When to Use

- The student needs more reps on an objective before it becomes reliable
- `assessing-understanding` needs fresh check items
- The course has too few exercises on a topic
- The student has finished all course practice and asks for more

## When NOT to Use

- **Generating graded or exam material** — the course owns that. Practice
  exercises are labeled as such and never masquerade as course work
- **Creating exercises for material beyond the course** — unless it is an
  explicit supplement (`providing-supplementary-material`)
- **Doing the exercises** — the student solves them; the agent reviews
  (`giving-feedback`)

## Method

1. **Read the objective.** Each exercise must trace to one learning objective
   from the course track. If it does not, it is scope creep.
2. **Match difficulty to level.** A student at `learning` gets scaffolded
   problems (small, single-concept); a student at `practising` gets transfer
   problems (mixed, applied).
3. **Vary the type.** Recall, near-transfer, applied, and error-spotting items
   exercise different parts of the skill. See `assessing-understanding` for the
   type ladder.
4. **Provide the answer separately.** The exercise sheet has no answers on it;
   a solution note is provided for the agent to check the student's attempt
   against — and never shown before the attempt.
5. **Label clearly.** "Practice, not part of the course. Matches objective
   <id>."

## Output shape

```
Practice set: <objective id>   (<N> items)
Level: <learning | practising>   (for student <name>, from progress record)

1. <prompt>            [type: recall / transfer / applied / error-spotting]
   (solution in the answer note - not shown before the attempt)

Answer note (agent-only): 1. <correct answer + why>
```

## Rules

- **Progressive difficulty.** Order items easy-to-hard so the student
  experiences success before challenge.
- **One objective per set.** Mixed sets are for `practising` review, not for
  `learning`.
- **Solutions are gated material.** The answer note is read to check an
  attempt, never to produce the first attempt.

## References

- `assessing-understanding` — the type ladder and check usage
- `giving-feedback` — reviewing the completed set
- `maintaining-course-progress` — the level that sets difficulty

## Last Validated

2026-08-12. Procedure current as of this date; re-verify when the course material or teaching rules change.
