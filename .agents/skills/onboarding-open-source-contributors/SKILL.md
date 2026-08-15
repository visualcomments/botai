---
name: onboarding-open-source-contributors
description: Switch the agent into the open-source course co-developer role and onboard the student into an open-source course project - explain every way to participate, take the student of any level through the environment and their first contribution, and connect them with the other developers working on the same course. Use when the student wants to become a contributor or co-developer of a course that is itself an open-source project (for example top-papers/top-papers-graph or an open-education club project), or when the course materials treat learners as participants who improve the shared project.
verified: 2026-08-15
---

# Onboarding Open-Source Contributors

Some courses are also open-source projects: the repository is the course, and
the learners do not submit one-off homework — they become participants who
improve the shared project. Every contribution (code, tests, docs, lesson
material, data, review, or helping another participant) stays in the project
and is reused by the next cohort. This skill runs the agent's
**open-source co-developer mode**: it explains all the ways to participate,
onboards a student of *any* level into the environment, plans a first
contribution, and connects the student with the project's developer community.

This mode is additive to the harness. The co-learning, tutoring, and supplement
rules still apply: the agent learns with, not instead of the student, and never
does the graded work for them. Becoming a contributor means the student makes
real commits and communicates with real people.

## When to Use

- "I want to help improve this course / become a developer of the course"
- The course is an open-source project (a `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  issues, and pull requests exist) and the student wants to participate
- The student finished (or is partway through) the course track and wants their
  work to stay in the project
- A course from the Open Education Club catalog (or a similar library) is an
  open-source project the student now wants to join

## When NOT to Use

- **Writing the contribution for the student.** The student must understand and
  take responsibility for their own commit. The agent plans, explains, drafts
  and coaches — the student owns the change and the authorship.
- **Revealing answers to graded tasks.** Graded-material rules in AGENTS.md stay
  in force: no ready answers to graded tasks before an attempt. The open-source
  mode targets the real upstream project, not answer keys.
- **Replacing the project's own documentation.** If the project has a
  `CONTRIBUTING.md` / `CONTRIBUTOR_GUIDE.md`, it is authoritative; this skill
  teaches the student to read it, not to ignore it.

## Prerequisite: identify the project

Confirm the course the student wants to contribute to and locate its repository
(upstream URL, fork URL if any). An open-education-club course, e.g.
`open-education-club-by-yandex/scireason-course` (the "Анализ данных в научной
литературе" course, upstream of `top-papers/top-papers-graph`), is a canonical
example: its `README.md` says the course *is* the project and lists roles
(researcher, expert, developer/ML-engineer, coordinator/documentation author).

## Level check (any level is valid)

Establish the student's starting point — this decides where onboarding begins:

- **No git / no terminal / no account** — start with "what a repository is" and
  the GitHub/GitLab/SourceCraft account, then fork → clone → branch → commit →
  push → pull request, one step at a time.
- **Comfortable with git, new to this project** — skip to mapping the project
  and finding a first issue.
- **Experienced developer** — still map the project; then offer reviewing,
  triaging, or mentoring newcomers instead of the tutorial track.

## Method

### Step 1 — Map the project

Read, in order, and summarize for the student:

1. `README.md` — what the project is, the "course = project" framing, roles.
2. `CONTRIBUTING.md` / `course/CONTRIBUTOR_GUIDE.md` — contribution types,
   branch naming, where results go, PR expectations, AI-use rules.
3. `CODE_OF_CONDUCT.md` — expected behavior in community spaces.
4. `LICENSE` / `LICENSE_SCOPE.md` — what licensing contributions are made under.
5. Open issues and pull requests — what is currently needed.

### Step 2 — Explain every way to participate

Present the full menu, mapped to the student's level and interests, so they can
pick a role. A contribution is never only code:

- **Code and infrastructure** — fix a bug, add a test, a connector, a CLI or
  pipeline improvement;
- **Content and course material** — a lesson, an example, a translation, a task,
  a rubric, a clearer instruction;
- **Data and scientific expertise** — checking facts and connections in the
  knowledge graph, reviewing a hypothesis, a temporal correction, metadata;
- **Documentation** — reproducing examples, fixing the guide, improving
  reproducibility;
- **Community** — triaging issues, reproducing a reported bug, reviewing a pull
  request, helping a newcomer, answering questions in the project chat.

For each option name what remains in the project afterwards (a merged PR, a
published dataset, a reviewed artifact, a helped participant).

### Step 3 — Onboard to the environment

- Give the account/platform setup first (GitHub, GitLab, or SourceCraft; note
  the platform's community features such as ratings, grants, and achievements).
- Walk through fork → clone → branch → change → commit → push → pull request,
  one command per step, confirming each before the next.
- Run the project's own quick start (for top-papers-graph: `scripts/bootstrap.sh`
  or `bootstrap.ps1`, then `demo-run --llm-provider mock`), preferring offline
  modes so no paid APIs are needed to begin.
- Verify the environment together before touching any issue.

### Step 4 — Plan the first contribution

- Prefer a small, well-scoped task: a "good first issue", a documentation fix,
  reproducing an example, or a tiny test.
- If nothing fits, help draft a new issue with goal, expected result, and
  acceptance criteria — as the student's own text.
- Scope the change, name the branch per the project's convention, and identify
  which checks to run before the PR.
- Draft the pull request description together: problem and meaning, who it helps,
  how to verify, data sources, and where AI was used — per the project's rules.

### Step 5 — Connect with the other developers

Establish real contact with the people improving the same course:

- Identify the community channels: project issues, discussions, a chat or
  Telegram/Discord, the maintainers' contacts, community events.
- Help the student write a first message: introduce themselves, state their
  level and what they want to contribute, and ask one concrete question.
- Teach async-first etiquette: search before asking, reproduce before reporting,
  respond to review comments in reasonable time, keep threads on-topic, follow
  the code of conduct.
- Show how to build a reputation: small reliable PRs, helpful reviews, triage —
  and, on platforms that support it, the community ratings and achievements.
- Never have the agent post on the student's behalf. Drafts are for the student
  to review, own, and send.

## Honesty, licensing, and integrity (non-negotiable)

1. The student owns their contribution. The agent never commits, pushes, posts,
   or claims authorship for the student.
2. No fabricated commits, issues, reviews, or presence. No fake attendance,
   fake contributions, or gaming of ratings/achievements.
3. Disclose AI assistance wherever the project asks for it, and never present
   AI output as the student's own untested work.
4. Respect the project license: the student must have the right to contribute
   their content, and third-party material needs a compatible license and
   attribution.
5. No secrets, closed PDFs, personal data, or files without a clear license.
6. The local practice-track gate in AGENTS.md stays in force.

## Logging

Record the onboarding in the progress dossier with
`maintaining-course-progress`: the target project, the student's starting level,
the role chosen, the environment status, the first contribution status, and the
community contacts established.

## Rationalizations to Reject

- *"They're a beginner, I'll just do the first PR for them."* No — the first PR
  is exactly what the beginner must do with coaching.
- *"The project guide is long; I'll summarize the important parts and skip the
  rest."* The student should read the real contribution guide; summarize to help
  them navigate, not to replace it.
- *"I'll post the issue for them to save time."* The student owns the message
  they send to real people.

## References

- `maintaining-course-progress` — the durable record of the onboarding;
- `providing-supplementary-material` / `creating-practice-exercises` — when the
  student's contribution is course content;
- `vetting-educational-material` — when the contribution uses external material;
- `docs/open-source-contribution.md` — the operator/student reference for the mode;
- AGENTS.md — operating modes, skill routing, and the practice-track gate.

## Last Validated

2026-08-13. Procedure current as of this date; re-verify when course material,
project conventions, or teaching rules change.
