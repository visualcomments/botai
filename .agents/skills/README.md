# Installed education skills

11 curated, non-overlapping Agent Skills that give the co-learner assistant
methodology for the full teaching lifecycle: mapping the course, keeping the
durable progress record, planning sessions, tutoring (explaining, breaking
down, assessing, feedback), supplementing the course, vetting adopted
material, and reporting progress. They are loaded automatically by any
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

- **course-agent-skills** - https://github.com/evilfreelancer/secs (harness pattern);
- the wider educational skill ecosystem is cataloged in
  [../../docs/course-agent-skills.md](../../docs/course-agent-skills.md).

Before relying on any skill for a real teaching session, re-read its `SKILL.md`:
skills are instructions the agent will follow, and student needs change over
time.

## What is installed

Course lifecycle: `mapping-course-syllabus`, `maintaining-course-progress`,
`planning-study-sessions`.

Teaching: `breaking-down-assignments`, `explaining-concepts`,
`assessing-understanding`, `giving-feedback`.

Supplements: `providing-supplementary-material`, `creating-practice-exercises`.

Trust and vetting: `vetting-educational-material`.

Reporting: `reporting-learning-progress`.

## Using these globally

These skills are scoped to this project. To make them available in every
session, symlink the real folders from `.agents/skills/` into `~/.claude/skills/`
(run from the repo root):

```bash
for d in .agents/skills/*/; do ln -s "$(pwd)/$d" "$HOME/.claude/skills/$(basename "$d")"; done
```
