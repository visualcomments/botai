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
        progress review courses course-set active \
        education-club \
        lint \
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
	@echo "  (on any OS, incl. Windows without make: python scripts/cli.py <command>)"
	@echo ""
	@echo "Multi-course workspace:"
	@echo "  make courses                List course subprojects with progress tails"
	@echo "  make course-set COURSE=slug Switch the active course (.botai/active)"
	@echo "  make active                 Show the active course"
	@echo ""
	@echo "Working with a course:"
	@echo "  make progress COURSE=slug  Summarize the progress record for a course"
	@echo "  make review COURSE=slug    Open the review workflow for a student's submission"
	@echo "  make education-club        Verify the Open Education Club catalog checkout (EDUCATION_CLUB_CATALOG=/path/to/catalog)"
	@echo ""
	@echo "Hygiene:"
	@echo "  make doctor                Show detected OS, helpers, courses, progress"
	@echo "  make lint                  Markdown lint the policy, docs, and skills (if markdownlint-cli2 present)"
	@echo "  make clean                 Remove temporary files (DRY=1 to preview)"
	@echo ""
	@echo "Read AGENTS.md before teaching with the agent."

doctor:
	@python3 scripts/cli.py doctor

# ============================================================================
# Workspace
# ============================================================================
setup:
	@python3 scripts/cli.py setup

# ----------------------------------------------------------------------------
# Install botai into a NEW, separate, project-scoped directory. The installer
# (scripts/install.py) copies the whole harness - AGENTS.md, agent.md, skills,
# commands, config - into the created project and never writes to global
# configs (opencode / Claude Code / Cursor / ...). Agent files stay inside the
# project only.
# ----------------------------------------------------------------------------
install:
	@python3 scripts/install.py --dest "$(DEST)"

# ----------------------------------------------------------------------------
# Scaffold a course from a minimal layout. Use as a starting point for a
# *real* course; do not ship a course empty.
# TITLE is optional and must NOT contain spaces (pass e.g. TITLE=My_Course);
# when omitted it falls back to the course name.
# ----------------------------------------------------------------------------
new-course:
	@python3 scripts/cli.py new-course --name "$(NAME)" --title "$(TITLE)"

# ============================================================================
# Progress + review
# ============================================================================
progress:
	@python3 scripts/cli.py progress --course "$(COURSE)"

review:
	@python3 scripts/cli.py review --course "$(COURSE)"

# ============================================================================
# Multi-course workspace: per-course subprojects + active-course switching
# ============================================================================
courses:
	@python3 scripts/cli.py courses

course-set:
	@python3 scripts/cli.py course-set --course "$(COURSE)"

active:
	@python3 scripts/cli.py active

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

clean:
	@python3 scripts/cli.py clean
