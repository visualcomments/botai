---
name: facilitating-team-challenges
description: Run a cohort challenge over one of four kinds - team coding, hackathon, peer teaching, code review tournament - scored on evidence with a stated denominator, paired by strictly stronger peers, with the same assistance ceiling that applies everywhere else. Use when a cohort needs a timed or collaborative activity, when a teacher asks for a team task, or when a student asks what the group should work on next.
verified: 2026-09-30
---

# Facilitating Team Challenges

A challenge is a collaboration format, not a different grading regime. The
module is `cooperative_challenges.py`: `create_challenge`, `form_teams`,
`score_team_challenge`, `peer_teaching_pairing`, and
`challenge_assistance_ceiling`.

## When to Use

- The cohort needs a structured group activity
- A teacher asks for a team task with an outcome
- A student asks "что мы делаем дальше?"
- The course design calls for a review tournament or a peer-teaching round

## When NOT to Use

- **A student working alone** - a one-person cohort cannot team; route to
  `planning-study-sessions`
- **A graded assignment in disguise** - the challenge never reclassifies a task
- **The group has not finished the prerequisite** - a challenge on an
  unverified prerequisite teaches guessing

## The four kinds

`cooperative_challenges.CHALLENGE_KINDS`, with `KIND_TITLES_RU`:

| Kind | Russian title | What it asks the cohort to do |
|---|---|---|
| `team_coding` | «Командная задача по программированию» | Build one artifact against declared objectives |
| `hackathon` | «Хакатон» | Produce something demonstrable in a fixed window |
| `peer_teaching` | «Взаимное обучение» | Members teach each other a focus objective |
| `code_review_tournament` | «Турнир разбора кода» | Review submitted code and argue for a verdict |

Choose `team_coding` when there is a declared set of objectives and a
checkable outcome. Choose `hackathon` when the point is a demonstrable artefact
under time pressure - and then do not let the deadline become an excuse for
revealing more. Choose `peer_teaching` when the cohort has uneven levels and
the objective is to transfer, not to ship. Choose `code_review_tournament` when
the skill under test is reading and judging code rather than writing it.

`create_challenge(...)` returns a validated document with no teams and no
results; `validate_challenge` runs before it is used.

## Scoring is evidence-based, with a denominator

`score_team_challenge` states its formula once, and it is speed-free:

```
checks_passed   = number of submitted checks with verdict == "pass"
coverage        = |{objective in the submission that also is in
                   challenge.objective_ids and has a passing check}| /
                   |challenge.objective_ids|
score           = 1.0 * checks_passed
                + 2.0 * coverage
                - 0.5 * skill_spread_penalty
```

(`WEIGHT_CHECKS_PASSED = 1.0`, `WEIGHT_OBJECTIVE_COVERED = 2.0`,
`WEIGHT_SKILL_SPREAD_PENALTY = 0.5`.)

Two obligations follow from that formula:

- **State the denominator.** Report «3 из 5 проверок» and
  «4 из 6 целей», not a bare number. A score without what it was out of is
  not evidence.
- **Never rank teams by speed or by a number alone.** A team that submitted
  early and passed 3 checks is not better than one that passed 5 later. The
  formula does not encode speed; neither should the announcement.

The score is a description of what the cohort did. It is not a grade. A grade
comes from the rubric and the accepted contract.

## Peer-teaching pairing requires a strictly stronger peer

`peer_teaching_pairing(...)` pairs `(teacher, student)` where the teacher has
the **strictly** highest skill on that focus objective.

- **Strictly** is the whole point. A peer at exactly the same level has nothing
  to teach, and pairing them produces a lesson neither can give.
- When no member is strictly better, it raises
  `ChallengeError("NO_SUITABLE_PEER")`. That is not a failure to work around -
  it means the cohort cannot be paired for this objective. Run the objective as
  a study round instead, and say why.
- Never relax the rule to fill a schedule. An unpaired objective is an honest
  result.

Teams themselves come from `form_teams`: members sorted by id, dealt into
size-`team_size` buckets, sizes differing by at most one, deterministic - so a
learner can ask "why am I here" and get a real answer. `seed` rotates the
starting offset.

## The assistance ceiling still applies inside a challenge

`challenge_assistance_ceiling()` delegates to `tutoring.ASSESSMENT_CEILING`
unchanged, and its docstring says why: **a challenge is a collaboration, not a
reclassification**. A hackathon on an undeclared assignment still caps at
`EXAMPLE`; `SOLUTION` is available only on a confirmed `practice` task. A
deadline is not an exemption.

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level EXAMPLE
```

So: a team asking the agent for the finished answer inside a hackathon gets the
same refusal as an individual student asking alone. The group context is not a
loophole, and neither is urgency.

## Consent and privacy

- Grouping and pairing disclose who is stronger at what. Confirm members agreed
  to share their levels for this purpose before forming teams.
- `leaderboards` comparison is opt-in (`require_opted_in`, `is_opted_in`) and
  `leaderboards.py` refuses named ranks. A challenge scoreboard that names
  people without opt-in, or that turns into a race, is refused - report the
  numbers with denominators instead.
- Never publish a member's level or record to the team. Teams get tasks.

## Limitations

- `score_team_challenge` scores submitted checks. It cannot verify that a check
  was meaningful, or that a submission is genuinely the team's work.
- The formula has three weights and no notion of difficulty, of time, or of
  learning gain. A low score may mean an easy challenge, not a weak team.
- `form_teams` is deterministic and size-balanced only. It does not model
  schedules, interpersonal fit, or working styles.
- `peer_teaching_pairing` raises when no strictly stronger peer exists; there is
  no fallback pairing and the agent must not invent one.
- `challenge_assistance_ceiling` is a delegation, not a runtime gate. A host
  without an output gate can still reveal an answer in prose - that residual
  risk is stated in `docs/botai-v2-design.md` §4.3 and is not claimed away.
- Nothing here opens a channel or delivers messages. Teams are documents in the
  workspace; the discussion is the students'.

## References

- `cooperative_challenges.py` - `CHALLENGE_KINDS`, `create_challenge`,
  `form_teams`, `score_team_challenge`, `peer_teaching_pairing`,
  `challenge_assistance_ceiling`
- `tutoring.py` - `ASSESSMENT_CEILING`
- `cohort_management.py` - `auto_group`, `peer_review_pairs` for the cohort
  behind the challenge
- `facilitating-peer-learning` - moderating the exchange
- `facilitating-team-challenges` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for each member before forming teams; a challenge on an
   unverified prerequisite is rescheduled as a study round, not run anyway.
2. Respect the recorded delivery preference and the assistance ceiling inside
   the challenge - group context changes nothing.
3. Record: the challenge kind, the team formation and its seed, the score with
   its denominators, every peer pair and why it qualified or failed, and any
   `NO_SUITABLE_PEER`.
4. Never record a challenge score as a grade or as a demonstrated stage for
   anyone.
5. Deliver challenge briefs, titles, and every student-facing line in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`cooperative_challenges.py`, the scoring weights, or the consent rules change.
Added this revision: the four challenge kinds, the evidence-based formula with
its denominators, the strictly-stronger peer rule and its honest failure, and
the ceiling-is-unaffected statement.
