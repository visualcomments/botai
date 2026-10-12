# Open-Source Course Co-Development (mode reference)

Some courses are also open-source projects: the repository *is* the course, and
learners do not submit one-off homework — they become participants who improve
the shared project. This is the "course = project" model. A canonical example is
[top-papers/top-papers-graph](https://github.com/top-papers/top-papers-graph):
students contribute code, tests, docs, lesson material, data and expert review,
and every contribution stays in the repository for the next cohort. Similar
communities run on platforms such as
[SourceCraft](https://sourcecraft.dev) (Yandex), which add ratings, grants,
achievements, and developer community features on top of a Git forge.

This document is the operator/student reference for the agent's
**open-source co-developer mode**, run by the
`onboarding-open-source-contributors` skill and the `contributor` subagent.
Trigger it with `/contribute <project>` or by telling the agent you want to
help improve the course.

## What the mode does

1. Explains **all** the ways to participate and lets the student pick a role.
2. Onboards a student of **any** level — from "first time in a terminal" to
   "experienced developer" — into the environment and a first contribution.
3. Connects the student with the **other developers** who improve the same
   course, so participation is real and communal, not a solo exercise.

## The roles menu (nothing is "only code")

| Role | Typical contribution | Stays in the project |
| --- | --- | --- |
| Researcher | reads papers, formulates questions and verification criteria | a topic, a review, a verified claim |
| Expert | checks facts, connections, temporal bindings, hypotheses | reviewed artifacts, corrections |
| Developer / ML-engineer | improves ingestion, the graph, search, models, CLI, tests, infra | merged code, new metrics, baselines |
| Coordinator / docs author | helps onboarding, tasks, reviews, examples, reproducibility | guides, examples, reproducible runs |
| Community helper | triages issues, reproduces bugs, reviews PRs, helps newcomers | helped participants, triaged backlog |

A contribution can be as small as fixing an instruction, checking ten
connections, adding one test, or describing a paper. See the project's
`CONTRIBUTING.md` / `course/CONTRIBUTOR_GUIDE.md` for its authoritative list.

## Onboarding path (any level)

- **Level check** — git/terminal/account experience.
- **Map the project** — README, CONTRIBUTING, CODE_OF_CONDUCT, LICENSE, open
  issues and PRs.
- **Environment** — platform account (GitHub / GitLab / SourceCraft), fork →
  clone → branch → commit → push → pull request, one command per step. Prefer
  the project's offline quick start (e.g. `scripts/bootstrap.ps1` +
  `demo-run --llm-provider mock` for top-papers-graph) so no paid API is
  required to begin.
- **First contribution** — a "good first issue", a docs fix, a reproduced
  example, or a tiny test; otherwise help draft a new issue.
- **Pull request** — problem and meaning, who it helps, how to verify, sources,
  and where AI was used (per the project's rules).

## Connecting with other developers

The agent helps the student establish real contact with the people working on
the same course:

- identify the channels — issues, Discussions, project chat/Telegram/Discord,
  maintainer contacts, community events;
- draft a first message the student reviews and owns: introduce yourself, state
  your level and intended contribution, ask one concrete question;
- teach async-first etiquette — search before asking, reproduce before
  reporting, respond to review comments in reasonable time, follow the code of
  conduct;
- build reputation through small reliable PRs and helpful reviews — and, where
  the platform supports it, community ratings and achievements.

The agent never posts on the student's behalf. Drafts are for the student to
review, own, and send.

## Integrity rules (non-negotiable)

- The student owns their contribution; the agent never commits, pushes, or
  claims authorship for them.
- No fabricated commits, issues, reviews, or presence; no gaming of ratings or
  achievements.
- AI assistance is disclosed wherever the project asks, and AI output is never
  presented as the student's untested work.
- The project license governs contributions; third-party material needs a
  compatible license and attribution; no secrets or personal data.
- Graded-material rules in `AGENTS.md` stay in force for the upstream project:
  no ready answers to graded tasks before an attempt, and the student owns
  every contribution.

## Where it fits the harness

- Skill: `.agents/skills/onboarding-open-source-contributors/`
- Subagent: `.opencode/agent/contributor.md` (`edit: deny`)
- Command: `/contribute` (`.opencode/command/contribute.md`)
- Policy: "Operating modes" and "Skill catalog" in `AGENTS.md`
