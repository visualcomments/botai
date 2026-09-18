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
# course slug for progress/review/corpus/course-update targets
COURSE_URL ?=
# git URL of a course repository (course-add)
COURSE_REF ?=
# branch/tag/commit for course-add / course-update (empty = default branch)
REF        ?=
# branch/tag/commit of the harness for `make update` (empty = recorded/default)
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
.PHONY: help doctor install test \
        setup new-course \
        progress review courses course-set active \
        corpus corpus-status corpus-acquire source-search quote-verify state-migrate \
        env-plan action-approve env-apply env-status operation-reconcile \
        contribute-start contribute-status contribute-draft contribute-rehearsal \
        persona-set achievements \
        course-inspect course-accept course-status policy-check \
        session-start session-next session-goal session-attempt session-check session-pause \
        consent-set consent-withdraw \
        update update-check update-dry-run \
        course-add course-update course-update-check course-update-commit detect-courses \
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
	@echo "  make corpus COURSE=slug    Acquire the course corpus (index + texts, verified)"
	@echo "  make corpus-status COURSE=slug   Report corpus readiness for the accepted course"
	@echo "  make corpus-acquire COURSE=slug  Download+verify per manifest (DRY=1 preview, OFFLINE=1 local only)"
	@echo "  make source-search COURSE=slug QUERY=...  Scoped search over accepted materials"
	@echo "  make quote-verify COURSE=slug CITATION=file.json  Verify a citation"
	@echo ""
	@echo "Course environment (plan, then a HUMAN approves, then it runs):"
	@echo "  make env-plan COURSE=slug SPEC=env.json  Build a plan; executes nothing"
	@echo "  make action-approve OPERATION=id         A human approves in a terminal (DENY=1 refuses)"
	@echo "  make env-apply OPERATION=id              Run an approved plan (a repeat returns the prior result)"
	@echo "  make env-status [OPERATION=id] [COURSE=slug]  Show operation state"
	@echo "  make operation-reconcile OPERATION=id    Inspect state; never re-runs an effect"
	@echo ""
	@echo "First contribution (the STUDENT publishes; the agent only drafts):"
	@echo "  make contribute-rehearsal [DEST=dir]     Build a throwaway repo for Git practice"
	@echo "  make contribute-start COURSE=slug TASK=url [ROLE=developer] [PUBLISH=public_pr]"
	@echo "  make contribute-status [CONTRIBUTION=id] Show state, diff hash and blockers"
	@echo "  make contribute-draft COURSE=slug CONTRIBUTION=id [OUT=file]  Write the draft"
	@echo ""
	@echo "Presentation (style only; badges are opt-in and personal):"
	@echo "  make persona-set LIST=1                  List the available personas"
	@echo "  make persona-set PERSONA=expedition COURSE=slug [GAME=1] [QUIET=1]"
	@echo "  make achievements COURSE=slug            Show personal badges (off by default)"
	@echo "  make state-migrate COURSE=slug  Import a v1 progress record (report first; APPLY=1 to write)"
	@echo "  make education-club        Verify the Open Education Club catalog checkout (EDUCATION_CLUB_CATALOG=/path/to/catalog)"
	@echo ""
	@echo "Course rules (what the agent is allowed to help with):"
	@echo "  make course-inspect COURSE=slug  Show what the course declares: assignments, grading, rights (write nothing)"
	@echo "  make course-accept COURSE=slug   Accept that contract as policy (a HUMAN decision; snapshots it under .botai/)"
	@echo "  make course-status COURSE=slug   Show the accepted rules and any drift in the working copy"
	@echo "  make policy-check COURSE=slug ASSIGNMENT=id [LEVEL=SOLUTION] [PREFERENCE=hints]"
	@echo "                                   Explain whether that help is permitted, and on which rule"
	@echo ""
	@echo "Keeping things current:"
	@echo "  make update                Update this botai harness itself (harness only; never courses/ or progress/)"
	@echo "  make update-check          Report whether a newer harness is published (exit 10 = yes)"
	@echo "  make update-dry-run        Show what an update would change; write nothing"
	@echo "  make course-add COURSE_URL=<git-url> [REF=<branch>]  Get a course repository into courses/<slug>/"
	@echo "  make course-update COURSE=slug  Update a course from its own repository (keeps local work)"
	@echo "  make course-update-check COURSE=slug  Report whether the course has an update (exit 10 = yes)"
	@echo "  make course-update-commit COURSE=slug Commit the course's pending work, then update it"
	@echo "  make detect-courses        Show every course, its source, version and dirty state"
	@echo ""
	@echo "Hygiene:"
	@echo "  make doctor                Show detected OS, helpers, courses, progress"
	@echo "  make test                  Run the harness tests (update, courses, path safety)"
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
# Corpus acquisition
# ----------------------------------------------------------------------------
# A course that publishes a corpus is not ready to teach until the corpus is
# installed. `make install` fetches it automatically; this target refetches one
# course on demand (FORCE=1 to refetch an already-installed corpus).
# ============================================================================
corpus:
	@python3 scripts/cli.py corpus --course "$(COURSE)" $(if $(FORCE),--force,)

# ============================================================================
# Migrating a v1 Markdown progress record into the v2 record.
# ----------------------------------------------------------------------------
# Reports first and writes only with APPLY=1. A file describing several
# students is refused: attributing one person's lines to another is worse than
# asking. The original file is preserved verbatim either way.
# ============================================================================
state-migrate:
	@python3 scripts/cli.py state-migrate --course "$(COURSE)" $(if $(APPLY),--apply,) $(if $(LEARNER),--learner "$(LEARNER)",) $(if $(SPLIT),--confirm-split "$(SPLIT)",)

# ============================================================================
# Course rules: what the agent is allowed to help with.
# ----------------------------------------------------------------------------
# The course contract (`botai/course.json` in a course) declares which
# assignments are graded and which are practice. Until a human accepts it, every
# assignment counts as unknown, which behaves like graded: no ready solutions.
#
# `course-inspect` only reads. `course-accept` is a human decision and snapshots
# the contract, so editing the file afterwards does not change the policy --
# otherwise whoever commits last could reclassify graded work as practice.
# ============================================================================
course-inspect:
	@python3 scripts/cli.py course-inspect --course "$(COURSE)"

course-accept:
	@python3 scripts/cli.py course-accept --course "$(COURSE)" $(if $(KIND),--kind "$(KIND)",) $(if $(URL),--url "$(URL)",)

course-status:
	@python3 scripts/cli.py course-status $(if $(COURSE),--course "$(COURSE)",)

# ============================================================================
# Корпус и источники.
# ----------------------------------------------------------------------------
# `corpus-status` сообщает состояние готовности и не чинит ничего сам.
# `corpus-acquire` скачивает по декларативному манифесту, распаковывает с
# лимитами и проверяет КАЖДЫЙ файл; неудачное обновление не трогает ранее
# установленный корпус. `source-search` и `quote-verify` работают в границах
# принятого курса: исключённые материалы не попадают даже в набор кандидатов.
# ============================================================================
corpus-status:
	@python3 scripts/cli.py corpus-status --course "$(COURSE)"

corpus-acquire:
	@python3 scripts/cli.py corpus-acquire --course "$(COURSE)" $(if $(DRY),--dry-run,) $(if $(OFFLINE),--offline,)

source-search:
	@python3 scripts/cli.py source-search --course "$(COURSE)" --query "$(QUERY)"

quote-verify:
	@python3 scripts/cli.py quote-verify --course "$(COURSE)" --citation "$(CITATION)"

# ============================================================================
# Среда курса: план -> подтверждение человеком -> выполнение -> проверка.
# ----------------------------------------------------------------------------
# `env-plan` ничего не исполняет и печатает детерминированный экран: какие
# команды, куда запись, сеть, риски, ограничения. `action-approve` — команда
# ЧЕЛОВЕКА в отдельном терминале: у модели нет ни её, ни способа выдать
# разрешение. `env-apply` без разрешения отказывает. Повтор после успеха
# возвращает прежний результат, а не выполняет второй эффект.
# ============================================================================
env-plan:
	@python3 scripts/cli.py env-plan --course "$(COURSE)" --spec "$(SPEC)"

action-approve:
	@python3 scripts/cli.py action-approve --operation "$(OPERATION)" $(if $(DENY),--deny,)

env-apply:
	@python3 scripts/cli.py env-apply --operation "$(OPERATION)"

env-status:
	@python3 scripts/cli.py env-status $(if $(OPERATION),--operation "$(OPERATION)",) $(if $(COURSE),--course "$(COURSE)",)

operation-reconcile:
	@python3 scripts/cli.py operation-cancel --operation "$(OPERATION)" --reconcile

# ============================================================================
# Первый вклад.
# ----------------------------------------------------------------------------
# Публикацию выполняет УЧЕНИК: агент не делает commit, push и не открывает PR.
# Он наблюдает состояние Git только на чтение, привязывает проверки к отпечатку
# изменения и готовит ЧЕРНОВИК описания. `contribute-rehearsal` создаёт
# одноразовый репозиторий для репетиции, чтобы первые шаги Git не пробовались
# на настоящем курсе.
# ============================================================================
contribute-start:
	@python3 scripts/cli.py contribute-start --course "$(COURSE)" --task "$(TASK)" $(if $(ROLE),--role "$(ROLE)",) $(if $(REPO),--repo "$(REPO)",) $(if $(PUBLISH),--publish "$(PUBLISH)",)

contribute-status:
	@python3 scripts/cli.py contribute-status $(if $(COURSE),--course "$(COURSE)",) $(if $(CONTRIBUTION),--contribution "$(CONTRIBUTION)",)

contribute-draft:
	@python3 scripts/cli.py contribute-draft --course "$(COURSE)" --contribution "$(CONTRIBUTION)" $(if $(OUT),--out "$(OUT)",)

contribute-rehearsal:
	@python3 scripts/cli.py contribute-rehearsal $(if $(DEST),--dest "$(DEST)",) $(if $(DRY),--dry-run,)

# ============================================================================
# Оформление: персоны и награды.
# ----------------------------------------------------------------------------
# Персона — только стиль: схема не принимает поля tools/permissions/grading,
# поэтому стиль не может расширить права. Переключение действует со следующего
# ответа и не сбрасывает прогресс. Награды по умолчанию ВЫКЛЮЧЕНЫ, они личные:
# ни таблицы лидеров, ни серий, ни штрафов за перерыв, и в оценку они не входят.
# ============================================================================
persona-set:
	@python3 scripts/cli.py persona-set $(if $(PERSONA),--persona "$(PERSONA)",) $(if $(COURSE),--course "$(COURSE)",) $(if $(GAME),--gamification,) $(if $(QUIET),--low-stimulus,) $(if $(LIST),--list,)

achievements:
	@python3 scripts/cli.py achievements --course "$(COURSE)"

policy-check:
	@python3 scripts/cli.py policy-check --course "$(COURSE)" --assignment "$(ASSIGNMENT)" $(if $(LEVEL),--level "$(LEVEL)",) $(if $(PREFERENCE),--preference "$(PREFERENCE)",)

# ============================================================================
# Keeping the harness and the courses current
# ----------------------------------------------------------------------------
# `update` refreshes the harness itself (policy, skills, agents, scripts, docs)
# and never writes into courses/, progress/, .botai/ or dist/. A file edited
# locally is kept and reported; .botai/backup/<stamp>/ holds anything replaced.
# ============================================================================
update:
	@python3 scripts/cli.py update $(if $(REF),--ref "$(REF)",)

update-check:
	@python3 scripts/cli.py update --check $(if $(REF),--ref "$(REF)",)

update-dry-run:
	@python3 scripts/cli.py update --dry-run $(if $(REF),--ref "$(REF)",)

# ----------------------------------------------------------------------------
# Courses come from their own repositories. `course-add` obtains one and records
# where it came from; `course-update` refreshes it from that same place, keeping
# local work. Neither invents a source: an unknown origin is reported, not
# guessed.
# ----------------------------------------------------------------------------
course-add:
	@test -n "$(COURSE_URL)" || { echo "usage: make course-add COURSE_URL=<git-url> [REF=<branch>]"; exit 2; }
	@python3 scripts/cli.py course-add --url "$(COURSE_URL)" $(if $(COURSE_REF),--ref "$(COURSE_REF)",) $(if $(NAME),--name "$(NAME)",)

course-update:
	@python3 scripts/cli.py course-update --course "$(COURSE)" $(if $(REF),--ref "$(REF)",)

# Same, but the course's uncommitted work is committed first (never discarded).
course-update-commit:
	@python3 scripts/cli.py course-update --course "$(COURSE)" --commit-and-update $(if $(REF),--ref "$(REF)",)

course-update-check:
	@python3 scripts/cli.py course-update --course "$(COURSE)" --check

detect-courses:
	@python3 scripts/cli.py courses
	@python3 scripts/courses.py detect

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
# Тесты обвязки. Проверяют правила, ради которых обновление и workspace
# существуют: обновление не трогает студенческие каталоги, локальная правка
# сохраняется, архив с выходом за каталог отвергается, грязный курс не
# обновляется, имя курса не может стать путём, предпросмотр ничего не пишет,
# контракты валидируются локально, миграция не выдумывает факты, принятый
# контракт курса не меняется от правки рабочей копии и оцениваемое задание не
# получает готовый разбор.
test:
	@python3 tests/test_update.py
	@python3 tests/test_paths.py
	@python3 tests/test_schemas.py
	@python3 tests/test_store.py
	@python3 tests/test_course.py
	@python3 tests/test_policy.py
	@python3 tests/test_tutoring.py
	@python3 tests/test_corpus.py
	@python3 tests/test_environment.py
	@python3 tests/test_contribution.py
	@python3 tests/test_personas.py

lint:
	@if [ "$(HAS_MARKDOWNLINT)" = yes ]; then \
	  markdownlint-cli2 '*.md' 'docs/**/*.md' '.agents/skills/**/*.md'; \
	else \
	  echo "markdownlint-cli2 not installed - skipping (brew install markdownlint-cli, or npm i -g markdownlint-cli2)"; \
	fi

clean:
	@python3 scripts/cli.py clean
