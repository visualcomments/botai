# botai

**botai** is a self-contained setup that turns an
AGENTS.md-aware AI coding agent (Claude Code, pi, Cursor, Codex, OpenCode, or
any compatible tool) into a **co-learner for a training course**: an agent that
goes through the course *together with* the students. It ships three things
that work together:

1.  **Guardrails and teaching policy** for the agent — learn with the student,
    never instead of them; Socratic first; check before teaching; honest
    coverage ([AGENTS.md](/home/zzz/Documents/Default Project/AGENTS.md));
2.  **11 curated Agent Skills** that give the agent methodology for the full
    teaching lifecycle — mapping the course, keeping the progress record,
    planning sessions, explaining, breaking down assignments, assessing,
    feedback, supplements, and reporting
    ([.agents/skills/](/home/zzz/Documents/Default Project/.agents/skills));
3.  **A local course workspace** — a cross-platform `make` interface that
    scaffolds courses, reads progress, and runs a deliberately under-specified
    practice course for integrity testing ([Makefile](/home/zzz/Documents/Default Project/Makefile),
    [docs/](/home/zzz/Documents/Default Project/docs)).

The result is a repository you drop an agent into so it can study a course
alongside the students — breaking down assignments, explaining material,
supplying what the course lacks — while never completing graded work for them.

## How it fits together

An AI coding agent (cloud or local) runs in this directory, bound by the
[AGENTS.md](/home/zzz/Documents/Default Project/AGENTS.md) policy, drawing on
the [Agent Skills](/home/zzz/Documents/Default Project/.agents/skills), and
wired to a local course workspace created with `make setup`. The agent reads the
same lessons the students read, does the same assignments as a basis for
discussion, reviews the student's attempts, and keeps a durable progress record
in `progress/` — nothing leaves the sandbox.

## Students first

This project is for **learning together with students**. The agent's policy
makes that concrete: it never writes a graded assignment for a student, never
hands out an answer key before an attempt, never fabricates sources, and never
impersonates the student. Before working with a student it runs a mandatory
consent gate — level, delivery preference, graded vs practice split — and
records the answers. The full policy lives in
[AGENTS.md](/home/zzz/Documents/Default Project/AGENTS.md); read it before
running any session.

## What is in this repository

| Path | What it is |
| --- | --- |
| [AGENTS.md](AGENTS.md) | The agent's policy: golden rules, consent gate, scope (course track), hard refusal list, teaching-safety rules, skill routing, evidence and feedback conventions. Every skill inherits it. |
| [CLAUDE.md](CLAUDE.md) | A thin import so Claude Code (which reads `CLAUDE.md`, not `AGENTS.md`) loads the same guardrails. |
| [.agents/skills/](.agents/skills/) | 11 Agent Skills (one `SKILL.md` per capability, plus references and templates). The real files live here. |
| [.claude/skills/](.claude/skills/) | Relative symlinks pointing back to `.agents/skills/<name>`, so Claude Code discovers the same skills. Edit under `.agents/skills/`; the symlinks track changes. |
| [.cursor/skills/](.cursor/skills/) | The same relative symlinks for Cursor, which scans `.cursor/skills/`. |
| [Makefile](Makefile) | The cross-platform interface: scaffold courses, read progress, run the practice lab. Run `make help`. |
| [opencode.json](opencode.json) | opencode top-level settings: default agent `botai`, skills paths, docs reference, permissions. |
| [.opencode/](.opencode/) | opencode wiring: the `botai` primary agent + teaching subagents, slash commands, an in-repo MCP server, and skill symlinks. |
| [docs/](docs/) | Reference docs: the teaching-method catalog, the skill ecosystem, and the practice-lab guide. |
| [dist/](dist/) | Temporary files. Git-ignored except its `.gitignore`; nothing here is committed. |

### Repository layout

```
botai/
├── AGENTS.md                  # agent policy and guardrails (the source of truth)
├── CLAUDE.md                  # thin import of AGENTS.md for Claude Code
├── README.md                  # this file
├── Makefile                   # cross-platform interface: setup, new-course, progress, lab
├── opencode.json              # opencode settings (default agent, skills, permissions)
├── .agents/skills/            # 11 education Agent Skills (real files)
│   ├── README.md              # what is installed, sources, how to use globally
│   └── <skill>/SKILL.md       # one folder per skill
├── .claude/skills/            # symlinks -> ../../.agents/skills/<skill> (Claude Code)
├── .cursor/skills/            # symlinks -> ../../.agents/skills/<skill> (Cursor)
├── .opencode/                 # opencode wiring
│   ├── agent/                 # botai primary agent + tutor/mapper/reviewer/supplementer subagents
│   ├── command/               # /session, /new-course, /progress, /review, /supplement, /lab, /setup
│   ├── mcp/                   # in-repo MCP server (course-lab content tools)
│   └── skills/                # symlinks -> ../../.agents/skills/<skill>
├── courses/                   # course materials (created by make setup / make new-course)
├── progress/                  # durable progress records (created by make setup)
├── course-lab/                # gated practice course (committed content, make lab-* targets)
├── docs/
│   ├── teaching-methods.md        # catalog of teaching practices by task
│   ├── course-agent-skills.md     # catalog of skill collections in the ecosystem
│   └── course-lab.md              # practice-track guide (operator)
└── dist/                          # temporary files (git-ignored)
```

## Wiring for pi and other agents

Skills live once under `.agents/skills/` and the policy lives in `AGENTS.md`.
[pi](https://pi.dev) and Cursor read `.agents/skills/` and `AGENTS.md` directly,
so they pick the skills up with no extra setup the moment you run them from this
directory (pi asks to trust the directory on first run; approve it once). Claude
Code reads `.claude/skills/` and `CLAUDE.md` instead, and Cursor also scans
`.cursor/skills/`, so both of those directories hold relative symlinks back to
`.agents/skills/<name>`. Edit skills under `.agents/skills/`; the symlink farms
track the change.

## Wiring for opencode

opencode reads `AGENTS.md` natively and loads project skills from
`.opencode/skills/`, which holds the same relative symlinks back to
`.agents/skills/<name>`. It also loads:

- `opencode.json` — `default_agent` is `botai` (the co-learner); skills paths; a
  `docs` reference; permissions (edit/webfetch allowed, bash allowed for
  `make *` and `git *`, everything else asks);
- `.opencode/agent/` — the primary agent `botai` plus the teaching subagents
  `tutor`, `mapper`, `reviewer`, and `supplementer`, all bound by AGENTS.md;
- `.opencode/command/` — slash commands `/session`, `/new-course`, `/progress`,
  `/review`, `/supplement`, `/lab`, `/setup`;
- `.opencode/mcp/course-lab-mcp.py` — an in-repo MCP server (registered as
  `mcp.course-lab`) exposing the course-lab practice course as tools. It does
  not expose `course-lab/solutions/` (graded answer keys). Enable with
  `python3 -m pip install -r .opencode/mcp/requirements.txt`, then restart.

Run opencode from the repo root. Config and `.opencode/` files load once at
startup: after editing them, restart opencode for the changes to take effect.

## The Agent Skills

The 11 skills are non-overlapping — one per capability — and are loaded
automatically by any agent working in this repo. See
[.agents/skills/README.md](.agents/skills/README.md) for the full list and the
per-skill routing table in [AGENTS.md](AGENTS.md).

- **Course lifecycle** — `mapping-course-syllabus`,
  `maintaining-course-progress`, `planning-study-sessions`;
- **Teaching** — `breaking-down-assignments`, `explaining-concepts`,
  `assessing-understanding`, `giving-feedback`;
- **Supplements** — `providing-supplementary-material`,
  `creating-practice-exercises`;
- **Reporting** — `reporting-learning-progress`.

Each skill is a set of instructions the agent will follow. Re-read a skill's
`SKILL.md` before relying on it for a real session, and treat any third-party
skill as untrusted until reviewed — see `docs/course-agent-skills.md`.

## How the agent operates

The policy defines three modes:

- **Co-learning mode** (default) — the agent studies the course alongside the
  student: reads the same lessons, does the same assignments as a basis for
  discussion, then reviews the student's attempt and gives feedback;
- **Tutoring mode** — the agent explains concepts, breaks down assignments into
  steps, and drills the student, tailored to their current level;
- **Supplement mode** — the agent detects a gap in the course material and
  prepares additional material (explanations, examples, practice, external
  references) to fill it, labeled as a supplement.

The golden rules, in short: learn with, not instead of; Socratic first; stay on
the course track; honest coverage; check before teaching; never fabricate; log
everything; when in doubt, stop and ask. The complete, binding version is in
[AGENTS.md](AGENTS.md).

## Quick start

Everything is driven by `make`. Run `make help` for the full target list, or
`make doctor` to see the detected environment and any helper tools already
present.

### 1. Set up the workspace

```bash
make setup                       # create courses/, progress/, dist/
make new-course NAME=my-course   # scaffold a course from the template
```

### 2. Start co-learning

Run the agent from this directory and ask it to study the course with you.
The agent runs the consent gate, maps the syllabus, and starts a session.

In opencode, the `botai` agent is the default. Slash commands drive the common
workflows: `/session`, `/new-course`, `/progress`, `/review`, `/supplement`,
`/lab`, `/setup`.

```bash
make progress COURSE=my-course   # summarize the progress record for a course
make review COURSE=my-course     # review a student's submission
```

### 3. Run the practice track (optional)

`course-lab/` is a practice course built on the open course «Анализ данных в
научной литературе» from [top-papers/top-papers-graph](https://github.com/top-papers/top-papers-graph)
(GPL-3.0-or-later). Its lessons leave gaps on purpose and its solution keys are
graded material — used to test the agent's integrity. See the gate in
`AGENTS.md` and `docs/course-lab.md`.

```bash
make lab-check                   # what the integrity test looks like
make lab-clean                   # remove it when done
```

## Documentation

- [AGENTS.md](AGENTS.md) - the agent's operating policy and guardrails (start here);
- [.agents/skills/README.md](.agents/skills/README.md) - what skills are installed and how to use them globally;
- [docs/teaching-methods.md](docs/teaching-methods.md) - catalog of teaching practices by task;
- [docs/course-agent-skills.md](docs/course-agent-skills.md) - the wider ecosystem of educational Agent Skills;
- [docs/course-lab.md](docs/course-lab.md) - the practice-track guide (operator).

## Attribution and derivation

**botai is a derivative work of [SECS](https://github.com/EvilFreelancer/secs)**
(SECurity aSsistant), re-themed from information security to education.

What is carried over from SECS:

- the overall harness pattern — an `AGENTS.md` policy, a skills directory with
  symlink farms for each agent surface (`.claude/skills/`, `.cursor/skills/`,
  `.opencode/skills/`), a `Makefile` front door, a `docs/` reference set, and a
  git-ignored `dist/`;
- the guardrail structure — authorization gate and rules of engagement mapped
  to a student consent gate and teaching rules, plus a gated practice track
  (SECS's Metasploitable lab → botai's `course-lab/`).

What is original to botai:

- all teaching Skills under `.agents/skills/`;
- the educational policy in `AGENTS.md`;
- the course lifecycle `Makefile` and the opencode agent/command wiring.

The guardrail practices also draw on the [AGENTS.md spec](https://agents.md/)
and on established teaching practice (Socratic method, scaffolded instruction,
formative assessment).

SECS is licensed under the Apache License 2.0; the derived portions retain that
attribution. botai itself is distributed under the GNU GPL v3 license (the
Apache 2.0 license is GPL v3 compatible).

## License

GNU GPL v3. See [LICENSE](LICENSE).

Derivative of [SECS](https://github.com/EvilFreelancer/secs)
(Apache-2.0) by EvilFreelancer.
