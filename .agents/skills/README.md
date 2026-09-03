# Installed education skills

13 curated, non-overlapping Agent Skills that give the co-learner assistant
methodology for the full teaching lifecycle: mapping the course, keeping the
durable progress record, planning sessions, tutoring (explaining, breaking
down, assessing, feedback), supplementing the course, onboarding students into
open-source course projects as co-developers, vetting adopted material, and
reporting progress. They are loaded automatically by any
AGENTS.md/CLAUDE.md-aware agent working in this repo and are governed by the
guardrails in [../../AGENTS.md](../../AGENTS.md).

## Layout

The skill folders physically live here in `.agents/skills/`. Each is exposed to
Claude Code through a relative symlink at `.claude/skills/<name>` pointing back
to `../../.agents/skills/<name>`, and to Cursor through `.cursor/skills/<name>`,
matching the repo-wide convention. Edit skills here; the symlinks pick up
changes automatically.

## Sources and attribution

These skills were written for this project in its own house style. The teaching
practices they encode draw on established pedagogy - the Socratic method,
scaffolded instruction, formative assessment, and spaced practice - and on the
educational Agent Skills ecosystem:

- the wider educational skill ecosystem is cataloged in
  [../../docs/course-agent-skills.md](../../docs/course-agent-skills.md);
- the harness structure follows the SECS pattern -
  https://github.com/evilfreelancer/secs.

Before relying on any skill for a real teaching session, re-read its `SKILL.md`:
skills are instructions the agent will follow, and student needs change over
time.

## What is installed

Course lifecycle: `mapping-course-syllabus`, `maintaining-course-progress`,
`planning-study-sessions`, `multi-course-workspace`,
`starting-course-from-education-club`.

Teaching: `breaking-down-assignments`, `explaining-concepts`,
`assessing-understanding`, `giving-feedback`.

Supplements: `providing-supplementary-material`, `creating-practice-exercises`.

Open-source course development: `onboarding-open-source-contributors`.

Trust and vetting: `vetting-educational-material`.

Reporting: `reporting-learning-progress`.

## These skills stay in the project

These skills are scoped to this project, and so are the agent files that load
them: `AGENTS.md`, `CLAUDE.md`, the opencode agents (`agent.md` and the
subagents under `.opencode/agent/`), the slash commands, and `opencode.json`.
Installing botai never copies any of them into global configuration — nothing
is written to `~/.claude/skills/`, `~/.config/opencode/`, `~/.cursor/`, or any
other framework-wide location, so the harness can never leak into your other
projects.

To use botai in another course or workspace, install a **separate project**
instead of reaching for global dirs:

```bash
cd botai
make install DEST=my-other-botai     # or: python3 scripts/install.py --dest my-other-botai
cd my-other-botai
```

Each installed project is self-contained: the policy, the skills, and the agent
files live only inside it. Do not symlink or copy `.agents/skills/` (or
`AGENTS.md`, `agent.md`, `opencode.json`) into a global config directory — that
would make the education policy and agents apply to every project you open,
which this harness is explicitly designed against.
