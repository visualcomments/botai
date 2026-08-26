# opencode wiring for botai

This directory adapts the botai harness to the opencode framework. opencode reads
`AGENTS.md` natively (it is the binding policy) and loads everything here once
at startup — restart opencode after editing any of these files.

Everything in this directory, and the `AGENTS.md`/`CLAUDE.md` policy files, are
**project-scoped**. They are installed only into the botai project created by
`scripts/install.py` / `make install` — the installer never writes agent files,
skills, or config into opencode's global config, so botai can never affect other
projects. Run opencode from the created project root.

## Layout

| Path | What it is |
| --- | --- |
| `opencode.json` (repo root) | Top-level settings: `default_agent: botai`, skills paths, a `docs` reference, and permissions. |
| `agent/botai.md` | Primary agent — the education co-learner. Default agent for this repo. |
| `agent/tutor.md` | Subagent: explains concepts, breaks down assignments, checks understanding. Edit-deny. |
| `agent/mapper.md` | Subagent: maps course documents into a track, maintains the progress record. |
| `agent/reviewer.md` | Subagent: reviews a student's attempt and gives actionable feedback. Edit-deny. |
| `agent/supplementer.md` | Subagent: fills course gaps with labeled, sourced supplements. |
| `agent/contributor.md` | Subagent: onboards a student into an open-source course as a co-developer and connects them with the project community. Edit-deny. |
| `command/*.md` | Slash commands: `/session`, `/new-course`, `/progress`, `/review`, `/supplement`, `/setup`, `/contribute`, `/education-club`. |
| `skills/` | Symlinks back to `.agents/skills/<name>` (same pattern as `.claude/skills/` and `.cursor/skills/`). |

## How it fits the policy

- The `botai` agent and all subagents are bound by `AGENTS.md`; the subagents
  inherit the guardrails and may not weaken them (their prompts restate the
  non-negotiables so they hold even when invoked directly).
- The primary agent routes teaching tasks to subagents via the task tool
  (tutor/mapper/reviewer/supplementer/contributor), matching the skill catalog.
- `reviewer` and `tutor` are `edit: deny` so review and teaching never turn
  into "do it for the student" — they return findings, the primary agent
  records them.
- Permissions in `opencode.json` let the agent run `make *` and `git *`, edit
  files, and webfetch for supplements; everything else asks.

## Verifying the botai agent is active

- `opencode.json` sets `default_agent: botai`, so the TUI starts on the botai
  agent; the active agent's name is shown in the input line.
- In the TUI, `/agents` lists the agents — `botai` is the primary one.
- A control question ("Who are you and how do you work?") should get the
  co-learner introduction: the operating modes and a reference to `AGENTS.md`.
- Config and these files are loaded once at startup: after editing them,
  restart opencode for the changes to take effect.

## Using the slash commands

- `/setup` — create the workspace layout and check the environment
- `/session <topic>` — start a co-learning session (runs the consent gate)
- `/new-course <name>` — scaffold a course and map its syllabus
- `/progress <course>` — summarize the progress record
- `/review <submission>` — review a student's attempt
- `/supplement <topic>` — find and fill gaps in the course material
- `/contribute <project>` — switch to the open-source co-developer role: explain
  how to participate in an open-source course, onboard the student into the
  environment and a first contribution, and connect them with the project's
  other developers
- `/education-club [course]` — start a course from the SourceCraft Open
  Education Club catalog: browse the catalog, read a course README, fetch the
  course into `courses/`, and begin co-learning (requires the `education-club`
  MCP, see below)

## MCP: Open Education Club catalog

The catalog MCP is registered in `opencode.json` under `mcp.education-club`. It
is served by `catalog-mcp.py` from a checkout of the SourceCraft
[open-education-club-by-yandex](https://sourcecraft.dev/open-education-club-by-yandex/open-education-club-by-yandex)
catalog repo, referenced through `{env:EDUCATION_CLUB_CATALOG}`. It exposes:

- `education-club_list_courses()` — the whole course catalog;
- `education-club_get_course(slug)` — course metadata + README;
- `education-club_fetch_course(slug, courses)` — clone the course repo into
  `courses/<slug>`.

Enable it with `make education-club` (or set `EDUCATION_CLUB_CATALOG`), install
the catalog's `mcp/requirements.txt`, and restart opencode. See
[`docs/education-club.md`](../docs/education-club.md) for the full reference and
the `starting-course-from-education-club` skill for the workflow.

## Headless and automated use

The interactive TUI is the primary interface (permissions are `ask` by design,
and slash commands are typed there). For automated / non-interactive sessions,
note the following:

- **One-shot runs over plain SSH may hang without a TTY.** Use the interactive
  channel or force a pseudo-TTY:
  ```bash
  ssh -tt server "cd <botai-project> && opencode run --agent botai --auto 'prompt'"
  ```
- **Non-interactive `opencode run` needs `--auto`** so that `ask`-permissions
  (e.g. arbitrary bash beyond `make *` / `git *`) are not auto-rejected, which
  would abort the session.
- **Remote serve (`opencode serve`)**: attach with `--dir` to pick the botai
  project and `--auto` to allow tools:
  ```bash
  opencode run --attach http://<host>:4096 --dir <botai-project> --agent botai --auto "prompt"
  ```
  The REST `/session`/`/message` endpoints currently ignore a per-session `dir`
  and do not reliably preserve UTF-8 text bodies — prefer the CLI attach channel.
- **Slash commands are not available through `opencode run --command /...`**
  against a remote serve in opencode 1.18.x (returns `UnknownError`). Run the
  interactive TUI to use `/session`, `/contribute`, etc., or prompt the `botai`
  agent directly with the intent.
