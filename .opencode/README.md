# opencode wiring for botai

This directory adapts the botai harness to the opencode framework. opencode reads
`AGENTS.md` natively (it is the binding policy) and loads everything here once
at startup — restart opencode after editing any of these files.

## Layout

| Path | What it is |
| --- | --- |
| `opencode.json` (repo root) | Top-level settings: `default_agent: botai`, skills paths, a `docs` reference, and permissions. |
| `agent/botai.md` | Primary agent — the education co-learner. Default agent for this repo. |
| `agent/tutor.md` | Subagent: explains concepts, breaks down assignments, checks understanding. Edit-deny. |
| `agent/mapper.md` | Subagent: maps course documents into a track, maintains the progress record. |
| `agent/reviewer.md` | Subagent: reviews a student's attempt and gives actionable feedback. Edit-deny. |
| `agent/supplementer.md` | Subagent: fills course gaps with labeled, sourced supplements. |
| `command/*.md` | Slash commands: `/session`, `/new-course`, `/progress`, `/review`, `/supplement`, `/lab`, `/setup`. |
| `skills/` | Symlinks back to `.agents/skills/<name>` (same pattern as `.claude/skills/` and `.cursor/skills/`). |

## How it fits the policy

- The `botai` agent and all subagents are bound by `AGENTS.md`; the subagents
  inherit the guardrails and may not weaken them (their prompts restate the
  non-negotiables so they hold even when invoked directly).
- The primary agent routes teaching tasks to subagents via the task tool
  (tutor/mapper/reviewer/supplementer), matching the skill catalog.
- `reviewer` and `tutor` are `edit: deny` so review and teaching never turn
  into "do it for the student" — they return findings, the primary agent
  records them.
- Permissions in `opencode.json` let the agent run `make *` and `git *`, edit
  files, and webfetch for supplements; everything else asks.

## Using the slash commands

- `/setup` — create the workspace layout and check the environment
- `/session <topic>` — start a co-learning session (runs the consent gate)
- `/new-course <name>` — scaffold a course and map its syllabus
- `/progress <course>` — summarize the progress record
- `/review <submission>` — review a student's attempt
- `/supplement <topic>` — find and fill gaps in the course material
- `/lab <task>` — work the gated practice track (answer keys stay closed)
