# botai - Makefile
#
# Single, self-contained front door for the education co-learner workspace:
# scaffolding courses, connecting the education-club catalog, and reading
# progress. Everything lives here at the make level - no helper shell scripts.
#
# Cross-platform: detects the OS and adapts where a tool is needed.
#   linux (apt/dnf/pacman)   macOS (brew)   other (none)
#
# Run `make help` for the target list. Read AGENTS.md before teaching with the
# agent - this harness is a teaching assistant, not a homework writer.

# ============================================================================
# Knobs (override on the CLI, e.g. `make new-course NAME=linux-101`)
# ============================================================================
NAME       ?=
# course slug (e.g. linux-101); defaults to basename of the current dir
TITLE      ?=
# human-readable course title; falls back to NAME
STUDENT    ?= student
# student identifier used for per-student records
COURSE     ?= $(NAME)
# course slug for progress/review targets
DRY        ?=
# set to 1 to preview actions, change nothing
DEST       ?= botai-project
# destination directory for `make install` - a NEW separate project

# ============================================================================
# Paths
# ============================================================================
ROOT       := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COURSES    := $(ROOT)/courses
PROGRESS   := $(ROOT)/progress
DIST       := $(ROOT)/dist
LOG        := /tmp/botai-make.log

empty :=
space := $(empty) $(empty)
comma := ,

# ============================================================================
# Platform detection
# ============================================================================
UNAME_S := $(shell uname -s 2>/dev/null)

ifeq ($(UNAME_S),Darwin)
  OS  := macos
else
  OS  := linux
endif

# Optional tools the lab or helpers may want (all non-fatal if missing)
HAS_MAKE := $(shell command -v make >/dev/null 2>&1 && echo yes || echo no)
HAS_GIT  := $(shell command -v git  >/dev/null 2>&1 && echo yes || echo no)
HAS_PY   := $(shell command -v python3 >/dev/null 2>&1 && echo yes || echo no)
HAS_NODE := $(shell command -v node >/dev/null 2>&1 && echo yes || echo no)
HAS_MARKDOWNLINT := $(shell command -v markdownlint-cli2 >/dev/null 2>&1 && echo yes || echo no)

# ============================================================================
# Help (default target)
# ============================================================================
.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help doctor install \
        setup new-course \
        progress review \
        education-club \
        lint check \
        clean

help:
	@echo "botai - education co-learner harness"
	@echo "===================================="
	@echo "detected: $(OS)"
	@echo ""
	@echo "Workspace:"
	@echo "  make install [DEST=dir]  Install botai into a NEW separate project (default: botai-project)"
	@echo "  make setup                 Create the workspace layout (courses/ progress/ dist/)"
	@echo "  make new-course NAME=slug [TITLE=My_Course]  Scaffold a new course (TITLE without spaces)"
	@echo ""
	@echo "Working with a course:"
	@echo "  make progress COURSE=slug  Summarize the progress record for a course"
	@echo "  make review COURSE=slug    Open the review workflow for a student's submission"
	@echo "  make education-club        Verify the Open Education Club catalog checkout (EDUCATION_CLUB_CATALOG=/path/to/catalog)"
	@echo ""
	@echo "Hygiene:"
	@echo "  make doctor                Show detected OS, helpers, courses, progress"
	@echo "  make lint                  Markdown lint the policy, docs, and skills (if markdownlint-cli2 present)"
	@echo "  make check                 Validate harness integrity: skill frontmatter, symlink-farm parity, path safety, methods store"
	@echo "  make clean                 Remove temporary files (DRY=1 to preview)"
	@echo ""
	@echo "Read AGENTS.md before teaching with the agent."

doctor:
	@echo "OS          : $(OS)"
	@echo "root        : $(ROOT)"
	@echo "helpers     : git=$(HAS_GIT) python3=$(HAS_PY) node=$(HAS_NODE) markdownlint-cli2=$(HAS_MARKDOWNLINT)"
	@echo "courses     : $(shell test -d '$(COURSES)' && ls '$(COURSES)' | tr '\n' ' ' || echo '(none - run make setup)')"
	@echo "progress    : $(shell test -d '$(PROGRESS)' && ls '$(PROGRESS)' | tr '\n' ' ' || echo '(none - run make setup)')"

# ============================================================================
# Workspace
# ============================================================================
setup:
	@mkdir -p "$(COURSES)" "$(PROGRESS)" "$(DIST)"
	@echo "workspace ready:"
	@echo "  $(COURSES)   (course materials go here)"
	@echo "  $(PROGRESS)  (progress records go here)"
	@echo "  $(DIST)      (temporary files, git-ignored)"
	@echo "next: make new-course NAME=<slug>"

# ----------------------------------------------------------------------------
# Install botai into a NEW, separate, project-scoped directory. The installer
# (scripts/install.py) copies the whole harness - AGENTS.md, agent.md, skills,
# commands, config - into the created project and never writes to global
# configs (opencode / Claude Code / Cursor / ...). Agent files stay inside the
# project only.
# ----------------------------------------------------------------------------
install:
	@test "$(HAS_PY)" = yes || { echo "python3 is required for 'make install'"; exit 2; }
	python3 scripts/install.py --dest "$(DEST)"

# ----------------------------------------------------------------------------
# Scaffold a course from a minimal layout. Use as a starting point for a
# *real* course; do not ship a course empty.
# TITLE is optional and must NOT contain spaces (pass e.g. TITLE=My_Course);
# when omitted it falls back to the course name.
# ----------------------------------------------------------------------------
new-course:
	@test -n "$(NAME)" || { echo "usage: make new-course NAME=<slug> [TITLE=<no_spaces>]"; exit 2; }
	@name=$$(echo '$(NAME)' | tr 'A-Z ' 'a-z-'); \
	title="$(TITLE)"; \
	if [ -z "$$title" ]; then title=$$name; fi; \
	dir="$(COURSES)/$$name"; \
	if [ -e "$$dir" ]; then echo "course already exists: $$dir"; exit 2; fi; \
	mkdir -p "$$dir/lessons" "$$dir/assignments" "$$dir/references"; \
	{ \
	  echo "# $$title"; \
	  echo; \
	  echo "> Scaffolded by botai. Fill in the syllabus before teaching."; \
	} > "$$dir/README.md"; \
	{ \
	  echo "# Syllabus"; \
	  echo; \
	  echo "## Modules"; \
	  echo; \
	  echo "1. _module one_ - _objective, prerequisites_"; \
	} > "$$dir/syllabus.md"; \
	echo "course scaffolded: $$dir"; \
	echo "next: fill in syllabus.md, then make progress COURSE=$$name"

# ============================================================================
# Progress + review
# ============================================================================
progress:
	@test -n "$(COURSE)" || { echo "usage: make progress COURSE=<slug>"; exit 2; }
	@f="$(PROGRESS)/$(COURSE).md"; \
	if [ -f "$$f" ]; then \
	  echo "== progress: $(COURSE) =="; \
	  grep -E '^## |^### |^- \[' "$$f" | head -n 60; \
	else \
	  echo "no progress record yet: $$f"; \
	  echo "hint: the agent writes it with the maintaining-course-progress skill"; \
	fi

review:
	@test -n "$(COURSE)" || { echo "usage: make review COURSE=<slug>"; exit 2; }
	@echo "review workflow for $(COURSE):"
	@echo "  - locate the student's submission under courses/$(COURSE)/assignments/"
	@echo "  - run the giving-feedback skill (rubric + least-assistance-first)"
	@echo "  - never reveal the answer to a graded task before the attempt"
	@echo "route to the agent: 'review my submission for $(COURSE) with the rubric'"

# ============================================================================
# Open Education Club catalog (MCP)
# ============================================================================
education-club:
	@test "$(HAS_GIT)" = yes || { echo "git is required for 'make education-club'"; exit 2; }
	@test -n "$(EDUCATION_CLUB_CATALOG)" || { echo "usage: EDUCATION_CLUB_CATALOG=/path/to/open-education-club-by-yandex make education-club"; exit 2; }
	@test -f "$(EDUCATION_CLUB_CATALOG)/mcp/catalog-mcp.py" || { echo "catalog-mcp.py not found under $(EDUCATION_CLUB_CATALOG)/mcp/"; exit 2; }
	@echo "education-club catalog checkout OK:"
	@echo "  $(EDUCATION_CLUB_CATALOG)"
	@echo "next steps:"
	@echo "  python3 -m pip install -r '$(EDUCATION_CLUB_CATALOG)/mcp/requirements.txt'"
	@echo "  restart opencode (the education-club MCP is registered in opencode.json via {env:EDUCATION_CLUB_CATALOG})"
	@echo "  then run /education-club to browse the catalog and start a course"

# ============================================================================
# Hygiene
# ============================================================================
lint:
	@if [ "$(HAS_MARKDOWNLINT)" = yes ]; then \
	  markdownlint-cli2 '*.md' 'docs/**/*.md' '.agents/skills/**/*.md'; \
	else \
	  echo "markdownlint-cli2 not installed - skipping (brew install markdownlint-cli, or npm i -g markdownlint-cli2)"; \
	fi

# ----------------------------------------------------------------------------
# Integrity check: skill frontmatter (name/description/verified), symlink-farm
# parity across .claude/.cursor/.opencode, path safety in skill files, and the
# teaching-methods store. Uses the git index so it works on Windows junctions.
# ----------------------------------------------------------------------------
check:
	@test "$(HAS_PY)" = yes || { echo "python3 is required for 'make check'"; exit 2; }
	python3 scripts/check.py

clean:
	@if [ -n "$(DRY)" ]; then \
	  echo "would remove temporary files under $(DIST)"; \
	else \
	  rm -rf "$(DIST)"/\*; \
	  echo "removed temporary files (kept courses/ and progress/)"; \
	fi
