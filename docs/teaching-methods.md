# Teaching Methods for the Co-learner Agent

A catalog of teaching practices the botai harness routes through its Skills.
Each entry says what it is, when the agent uses it, and which Skill owns it.
These are established methods from teaching practice; the Skills in this repo
encode them into repeatable workflows.

## Socratic method

**What:** teaching by questioning — the agent asks guiding questions so the
student reaches the conclusion themselves instead of being handed it.

**When:** default for `explaining-concepts` and for opening a new topic. The
agent asks before answering, and the answer escalates only after engagement.

**Owned by:** `explaining-concepts`.

## Scaffolded instruction (least-assistance-first)

**What:** assistance is offered at the smallest useful level and escalated only
when the student is stuck: hint → example → analogous solved problem → (for
practice) full solution. For graded tasks the ladder stops at the hint/example
rungs.

**When:** any tutoring exchange. It is the AGENTS.md rule "least-assistance
first" made concrete.

**Owned by:** `breaking-down-assignments`, `giving-feedback`.

## Formative assessment

**What:** quick, low-stakes checks of understanding *while learning happens* —
explain-back, predict, transfer — rather than a single high-stakes exam at the
end. The result feeds the next teaching step, not a grade.

**When:** before teaching new material (level check) and after explaining
(check that it landed).

**Owned by:** `assessing-understanding`.

## Worked examples

**What:** showing one fully-solved analogous problem, then having the student
solve a parallel one. Contrasts with the student's own attempted problem, which
is always left for them.

**When:** explaining a concept the student has not met; unblocking a stuck
student by analogy.

**Owned by:** `explaining-concepts`, `breaking-down-assignments`.

## Problem decomposition

**What:** breaking an assignment into ordered steps with per-step
prerequisites and self-checkpoints, so "I don't know how to start" becomes a
list of things the student can do and verify.

**When:** an assignment looks overwhelming, or a student is stuck at the start.

**Owned by:** `breaking-down-assignments`.

## Spaced practice

**What:** revisiting earlier objectives at increasing intervals instead of
cramming new material, so mastery survives rather than decays.

**When:** multi-week study plans in `planning-study-sessions`; practice sets in
`creating-practice-exercises`.

## Feedback sandwich with specificity

**What:** what is right (first), what needs work (specific, not "it's wrong"),
how to fix it themselves (strategy, not the fix). For graded tasks, the fix is
never supplied.

**When:** every review of a student attempt.

**Owned by:** `giving-feedback`.

## Gap-based supplementation

**What:** when the course is unclear, incomplete, or wrong, the agent names the
gap with course evidence, then supplies labeled material with a real source —
never silently substituting its own version of the course.

**When:** course text is thin, or `mapping-course-syllabus` flags a missing
prerequisite.

**Owned by:** `providing-supplementary-material`.

## References

- The harness pattern (AGENTS.md, Makefile, skills, symlink farms) mirrors
  SECS: https://github.com/EvilFreelancer/secs
- The educational skill ecosystem is cataloged in `course-agent-skills.md`
