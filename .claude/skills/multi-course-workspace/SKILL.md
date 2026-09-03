---
name: multi-course-workspace
description: Run the education agent in a dedicated project workspace and keep one subproject per course when several courses are studied simultaneously. Covers the workspace layout (one botai project = one workspace; courses/<slug>/ = one subproject per course), the active-course marker, per-course progress isolation, and switching between courses without cross-course leakage. Use when the student studies more than one course at once, when starting a new botai project, or when resuming after a gap with multiple course subprojects present.
verified: 2026-09-03
---

# Multi-Course Workspace

A student rarely studies one course at a time. botai should make that normal:
one agent, one dedicated workspace project, and a **separate subproject for
each course**, so progress, scope, consent, and graded-vs-practice rules never
bleed from one course into another.

## Workspace model

```
<workspace>/                  # one botai project (make install DEST=<dir>,
│                             # or: python scripts/install.py --dest <dir>)
├── AGENTS.md                 # policy (installed)
├── scripts/
│   └── cli.py                # cross-platform workspace commands (installed)
├── courses/                  # one subproject per course
│   ├── <slug-a>/
│   │   ├── README.md         # course title + short description
│   │   ├── syllabus.md       # modules, objectives, prerequisites
│   │   ├── lessons/          # lesson material
│   │   ├── assignments/      # graded and practice assignments (student work stays here)
│   │   └── references/       # corpus links, manifests, extra sources
│   └── <slug-b>/             # second simultaneous course
├── progress/
│   ├── <slug-a>.md           # per-course progress record (see maintaining-course-progress)
│   ├── <slug-b>.md
│   └── _journal.md           # de-identified teaching journal across all courses
├── dist/                     # builds/artifacts
└── .botai/
    └── active                # active-course marker (plain slug, no newline)
```

Rules of the model:

- **One workspace per project.** `make install DEST=<dir>` (cross-platform:
  Windows / Linux / macOS) creates the workspace and copies the harness —
  including `scripts/cli.py` — only inside it. Run the agent from that
  directory; the policy and skills are then in scope.
- **One subproject per course.** Scaffold with `make new-course NAME=<slug>`.
  Do not put two courses inside one `courses/<slug>/` directory and do not
  teach a course from the repo root.
- **State is keyed by slug.** Progress (`progress/<slug>.md`), consent and
  delivery preference, graded-vs-practice lists, and supplements all belong to
  exactly one course.
- **Active course.** `make course-set COURSE=<slug>` records the current course
  in `.botai/active`. `make active` prints it, `make courses` lists every
  subproject with a progress tail. If no marker exists, ask the student which
  course to work on — never guess.

## Cross-platform commands

All workspace commands run everywhere through `scripts/cli.py` (pure Python,
no POSIX shell). The `make` targets are thin wrappers; without make — e.g. in
the Windows command prompt — call the Python CLI directly:

```bash
# POSIX (Linux / macOS / WSL / Git Bash) — with or without make:
make install DEST=<ws>            # or: python3 scripts/install.py --dest <ws>
make new-course NAME=<slug>
make courses
make course-set COURSE=<slug>
make active
make progress COURSE=<slug>
```

```bat
rem Windows (no make needed):
py -3 scripts\cli.py setup
py -3 scripts\cli.py new-course --name <slug>
py -3 scripts\cli.py courses
py -3 scripts\cli.py course-set --course <slug>
py -3 scripts\cli.py active
py -3 scripts\cli.py progress --course <slug>
```

The agent itself runs **from the workspace root** after install and uses the
same commands to load, switch, and record courses.

## Workflow

1. **Start.** If the workspace is new: `make install DEST=<workspace>` and
   `make new-course NAME=<slug>` per course. If resuming: read `.botai/active`,
   or call `make courses` and confirm the course with the student.
2. **Load the course.** Read `courses/<slug>/syllabus.md` and
   (if mapped) its track/modules; read `progress/<slug>.md` before teaching.
3. **Teach.** All scope rules from AGENTS.md (stay in the module, supplements
   labeled) apply to the active course only.
4. **Record.** Append to `progress/<slug>.md` after the session (never a shared
   "all courses" file for per-course facts), and one de-identified line to
   `progress/_journal.md`.
5. **Switch.** The student may switch courses mid-session: run
   `make course-set COURSE=<slug>` (or set `.botai/active`), re-load that
   course's scope and progress, and state the switch explicitly.

## Anti-leakage rules

- A supplement or example made for course A must not be presented as course B
  material — re-check it against the B syllabus or mark it clearly as external.
- The student's consent gate and delivery preference are per course: they may
  differ between courses (hints in one, solutions-first in another).
- Graded vs practice is per course; never import a course A grading rule into
  course B.
- If two courses share a topic, teach it from the active course's material and
  reference, don't fuse the two.

## Commands

```bash
make install DEST=<workspace>       # create a new workspace project
make new-course NAME=<slug>         # scaffold a course subproject
make course-set COURSE=<slug>       # switch the active course
make active                         # show the active course
make courses                        # list subprojects + progress tails
make progress COURSE=<slug>         # summarize one course's record
```

Without make (any OS): `python scripts/cli.py <command>` — see
"Cross-platform commands" above. `python scripts/cli.py --help` lists
everything.