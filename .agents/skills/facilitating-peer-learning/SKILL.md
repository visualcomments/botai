---
name: facilitating-peer-learning
description: Put students together in the way that matches what they are practicing - homogeneous groups for peer teaching, heterogeneous groups for mixed support - and run peer review as a moderated exchange where feedback informs the author but never becomes a grade. Use when a student asks to work with others, when a cohort needs groups for a study round, when peer review is part of the course design, or when the agent is asked to assign or rotate review pairs.
verified: 2026-09-30
---

# Facilitating Peer Learning

Peers explain differently than instructors and catch things an instructor
would have filled in. The mechanism is `cohort_management.py`:
`auto_group` for group composition, `peer_review_pairs` for the review ring.

## When to Use

- A cohort needs groups for a study round or a challenge
- The course calls for peer review of submissions
- A student asks who to work with
- Review pairs need rotating without favoritism

## When NOT to Use

- **Grading a submission** - peer feedback informs the author; the grade is the
  course's judgment, from the contract and the rubric
- **A single student asking a question** - answer it; grouping is for cohorts
- **A student who has not attempted the work** - review assumes an attempt

## Grouping: which shape, and when

`cohort_management.auto_group` supports two strategies:

- **Homogeneous** - members sorted by mean skill, chunked into groups of
  `group_size`, so neighbours land together. This is the peer-teaching
  arrangement: a learner helped by someone near their own level, not by someone
  three steps ahead, who would explain at the wrong altitude.
- **Heterogeneous** - the same sorted list dealt round-robin, so each group
  gets one strong, one middling, one developing member. This is the mixed
  support arrangement: strong members consolidate by explaining, developing
  members get local help.

Choose homogeneous when the goal is *peer teaching* and every member should be
able to contribute an explanation. Choose heterogeneous when the goal is
*joint problem solving* where complementary strength matters more than
shared altitude.

`review_mode` is `balanced` (everyone reviews someone) or `focused` (a subset
is reviewed in depth). `skill_vectors_for` falls back to the stated level when
a member has no vector, and to the beginner anchor when they have neither - a
cohort with no diagnostics must still be groupable.

## Peer review pairs

`peer_review_pairs` returns deterministic `(reviewer, reviewee)` pairs:

- **No self-review**, ever.
- Each member reviews one other and, where size allows, is reviewed once - a
  ring.
- When a ring cannot form (one member, or `focused` mode leaves someone
  unreviewed), the remaining members are paired by a stable second pass, not by
  shuffling.
- `seed` rotates the ring's starting offset, so rotation changes who starts
  without making the assignment random.

## How the agent moderates a peer review

1. **Both sides see the same brief.** The reviewer gets the rubric and the
   author's stated objective; the author gets the reviewer's feedback.
2. **The reviewer is prompted to be specific.** Ask: what worked, what needs
   work, and how the author can fix it themselves. Use the `giving-feedback`
   shape.
3. **The moderator does not rewrite either side.** When feedback is vague, the
   moderator asks the reviewer to be specific; it does not write the feedback
   itself and post it as the reviewer's.
4. **Nothing is deleted silently.** Feedback that is abusive, off-topic, or a
   solution to a graded task is **reported**, with the reason, and held back
   from the author until a human looks - never quietly dropped.
5. **The author decides.** Peer feedback is input; the author acts on it.

## Peer feedback never becomes a grade

This is the invariant of the skill:

- Peer feedback is a signal to the author. It never enters a grade, a mastery
  claim, or a progress-record state change on its own.
- A reviewer's "looks good to me" is not a `demonstrated` stage. Only the
  student's own attempt, checked against the objective, can produce that.
- The moderator never reports a peer's assessment as the course's judgment.
  When the course needs a mark, it comes from the rubric and the accepted
  contract.

## Consent and privacy

Grouping exposes a student's level to other students. Before grouping:

- Confirm the student is in the cohort and has agreed to share their level for
  this purpose. A level used for grouping is a disclosure the student must have
  agreed to; `leaderboards.py` refuses named ranks, and grouping is the same
  order of disclosure.
- Never publish a member's level, score, or record to the group. Groups get
  tasks, not transcripts.

## Limitations

- `auto_group` sorts by a mean skill scalar. It does not model personality,
  schedule, or working style; a group that fits on paper may not fit in
  practice, and the agent must not claim otherwise.
- The ring is deterministic. Re-running with a different `seed` changes who
  starts, not who reviews whom - a stale pair list will not be reshuffled by
  repeating the call.
- The moderator cannot verify what a peer reviewer actually did. Feedback is
  read as text; its accuracy is not checked.
- Nothing here sends messages, opens a channel, or stores a conversation. The
  pairs and the feedback are documents in the workspace; delivery is the
  student's or the teacher's action.
- `cohort_report` reports counts with the denominator stated - how many
  members have data at all - so a report's numbers are never read as covering
  more students than they do.

## References

- `cohort_management.py` - `auto_group`, `peer_review_pairs`,
  `skill_vectors_for`, `cohort_report`, `STRATEGIES`, `REVIEW_MODES`
- `giving-feedback` - the shape a peer review should follow
- `facilitating-team-challenges` - when the grouping is for a challenge
- `leaderboards.py` - `require_opted_in`, `is_opted_in` (comparison is
  opt-in, and named ranks are refused)
- `facilitating-peer-learning` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for each member before grouping; a member with no level is
   told so, not defaulted silently.
2. Respect the recorded delivery preference; peer feedback does not change what
   the author may receive from the agent.
3. Record: the grouping strategy used, the pair list, what each member
   demonstrated in the review, and any report raised against feedback.
4. Never record peer feedback as a grade, a mastery claim, or a demonstrated
   stage.
5. Deliver group briefs and feedback templates in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`cohort_management.py`, the grouping strategies, or the consent rules change.
Added this revision: the homogeneous-versus-heterogeneous choice, the
no-self-review ring, the moderation sequence with report-not-delete, and the
invariant that peer feedback is never a grade.
