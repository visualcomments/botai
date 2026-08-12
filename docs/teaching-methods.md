# Teaching Methods for the Co-learner Agent

A catalog of teaching practices the botai harness routes through its Skills.
Each entry says what it is, when the agent uses it, and which Skill owns it.
These are established methods from teaching practice; the Skills in this repo
encode them into repeatable workflows.

## Table of Contents

- [Questioning and dialogue](#questioning-and-dialogue)
- [Scaffolding and assistance](#scaffolding-and-assistance)
- [Assessment](#assessment)
- [Practice and retention](#practice-and-retention)
- [Feedback](#feedback)
- [Supplements and materials](#supplements-and-materials)
- [Motivation and capacity](#motivation-and-capacity)

## Questioning and dialogue

### Socratic method

**What:** teaching by questioning — the agent asks guiding questions so the
student reaches the conclusion themselves instead of being handed it.

**When:** default for `explaining-concepts` and for opening a new topic. The
agent asks before answering, and the answer escalates only after engagement.

**Owned by:** `explaining-concepts`.

### Think-aloud

**What:** the student narrates their reasoning while solving, so the agent can
see where the reasoning actually breaks instead of guessing from the output.

**When:** a student is stuck on a graded task and the failure point is not
obvious; before `giving-feedback` on a confusing attempt.

**Owned by:** `explaining-concepts`, `giving-feedback`.

### Prediction before explanation

**What:** ask the student to predict the outcome of an example before showing
it. The prediction exposes the mental model, and the correction lands harder
than a passive explanation.

**When:** opening a new idea in `explaining-concepts`; a check in
`assessing-understanding`.

## Scaffolding and assistance

### Scaffolded instruction (least-assistance-first)

**What:** assistance is offered at the smallest useful level and escalated only
when the student is stuck: hint → example → analogous solved problem → (for
practice) full solution. For graded tasks the ladder stops at the hint/example
rungs.

**When:** any tutoring exchange. It is the AGENTS.md rule "least-assistance
first" made concrete.

**Owned by:** `breaking-down-assignments`, `giving-feedback`.

### Assistance-level tags (HINT / EXAMPLE / SOLUTION)

**What:** every piece of help is tagged with how much it reveals — HINT (a
nudge that does not reveal the answer), EXAMPLE (an analogous worked case),
SOLUTION (the full answer). Escalate one level at a time, only when stuck.
Graded tasks may use HINT and EXAMPLE but never SOLUTION. Mirrors SECS's
noise tags QUIET/MODERATE/LOUD.

**When:** every explanation and review; the tag keeps least-assistance-first
honest and tells the progress record how far the help went.

**Owned by:** `explaining-concepts`, `giving-feedback`.

### Worked examples

**What:** showing one fully-solved analogous problem, then having the student
solve a parallel one. Contrasts with the student's own attempted problem, which
is always left for them.

**When:** explaining a concept the student has not met; unblocking a stuck
student by analogy.

**Owned by:** `explaining-concepts`, `breaking-down-assignments`.

### Problem decomposition

**What:** breaking an assignment into ordered steps with per-step
prerequisites and self-checkpoints, so "I don't know how to start" becomes a
list of things the student can do and verify.

**When:** an assignment looks overwhelming, or a student is stuck at the start.

**Owned by:** `breaking-down-assignments`.

## Assessment

### Formative assessment

**What:** quick, low-stakes checks of understanding *while learning happens* —
explain-back, predict, transfer — rather than a single high-stakes exam at the
end. The result feeds the next teaching step, not a grade.

**When:** before teaching new material (level check) and after explaining
(check that it landed).

**Owned by:** `assessing-understanding`.

### Pre-testing and placement

**What:** establish the student's baseline for the course's prerequisites
before the first session (the consent gate), so teaching does not start over
the student's head or re-teach what they already know.

**When:** first session of a course; resuming after a long gap.

**Owned by:** `assessing-understanding`, `maintaining-course-progress`.

### Error-spotting checks

**What:** give the student a deliberately flawed attempt and ask them to find
and justify the error. It checks understanding of a graded-style task without
producing an answer.

**When:** after explaining a concept; before a graded task the student has not
yet tried.

**Owned by:** `assessing-understanding`, `creating-practice-exercises`.

## Practice and retention

### Spaced practice

**What:** revisiting earlier objectives at increasing intervals instead of
cramming new material, so mastery survives rather than decays.

**When:** multi-week study plans in `planning-study-sessions`; practice sets in
`creating-practice-exercises`.

### Deliberate practice with progressive difficulty

**What:** practice sets ordered easy-to-hard, matched to one objective at a
time, so the student experiences success before challenge and the reps target
the specific weakness rather than everything at once.

**When:** `creating-practice-exercises`; re-drilling a `blocked` objective.

### Interleaving

**What:** mixing recently-learned objectives in one practice session instead of
blocking them by topic, to force the student to choose the right technique.

**When:** `practising`-level review sets; end-of-module practice.

## Feedback

### Feedback sandwich with specificity

**What:** what is right (first), what needs work (specific, not "it's wrong"),
how to fix it themselves (strategy, not the fix). For graded tasks, the fix is
never supplied.

**When:** every review of a student attempt.

**Owned by:** `giving-feedback`.

### Feedback invariants

**What:** four mechanical checks before a review leaves draft — every point
cites the attempt; confidence and certainty agree; every strategy is runnable
without a follow-up question; mastery/blockage claims have evidence. A failing
point becomes an open question, not a silent drop.

**When:** every review, before sending. Mirrors SECS `reporting-security-findings`.

**Owned by:** `giving-feedback`.

## Supplements and materials

### Gap-based supplementation

**What:** when the course is unclear, incomplete, or wrong, the agent names the
gap with course evidence, then supplies labeled material with a real source —
never silently substituting its own version of the course.

**When:** course text is thin, or `mapping-course-syllabus` flags a missing
prerequisite.

**Owned by:** `providing-supplementary-material`.

### Material vetting

**What:** external course material, third-party skills, and supplement sources
are gated before adoption: provenance, spot-checked facts, course alignment,
and a scan for steering content. Treat content the way SECS treats security
tooling — never trust it unverified.

**When:** before adopting any external material or third-party skill.

**Owned by:** `vetting-educational-material`.

## Motivation and capacity

### Pacing and capacity respect

**What:** one idea per step, confirmed before the next; stop and recommend a
break at signs of overload. Never push to "just one more topic".

**When:** any session; a student shows overload.

**Owned by:** `planning-study-sessions` (general rule from AGENTS.md).

### Mastery-based progression

**What:** advancing is decided by demonstrated mastery, not by covering the
material — an objective is `mastered` only after an unaided correct attempt.

**When:** updating the progress record; planning the next objective.

**Owned by:** `maintaining-course-progress`, `reporting-learning-progress`.

## References

- The harness pattern (AGENTS.md, Makefile, skills, symlink farms) mirrors
  SECS: https://github.com/EvilFreelancer/secs
- The educational skill ecosystem is cataloged in `course-agent-skills.md`
- Method catalog structure follows SECS `docs/security-tools.md`
