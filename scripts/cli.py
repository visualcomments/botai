#!/usr/bin/env python3
"""botai workspace CLI — cross-platform (Windows / Linux / macOS).

Implements the workspace commands that the Makefile delegates to, so the
workspace works everywhere, including on Windows without a POSIX shell:

    python scripts/cli.py setup
    python scripts/cli.py new-course --name <slug> [--title <TITLE>]
    python scripts/cli.py progress --course <slug>
    python scripts/cli.py review --course <slug>
    python scripts/cli.py courses
    python scripts/cli.py course-set --course <slug>
    python scripts/cli.py active
    python scripts/cli.py corpus --course <slug>
    python scripts/cli.py update [--check]
    python scripts/cli.py course-add --url <git-url> [--name <slug>] [--ref <ref>]
    python scripts/cli.py course-update --course <slug> [--check] [--dry-run]
    python scripts/cli.py doctor
    python scripts/cli.py clean

Paths are resolved against the current directory (the project root); override
with --root or the BOTAI_ROOT environment variable.

`update` keeps the harness current; `course-update` keeps a course current.
Neither writes into student material — see scripts/harness.py for what counts
as the harness, and why that list is defined in exactly one place.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# The workspace CLI prints Russian diagnostics. A Windows console defaults to a
# legacy code page (cp1252/cp866), where those characters are unmappable and
# printing raises UnicodeEncodeError — the tool would crash while explaining an
# error, which is the worst moment to lose the message. Reconfigure the streams
# to UTF-8 with replacement so output survives any terminal.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import courses as C  # noqa: E402
import harness as H  # noqa: E402
import pathsafe as P  # noqa: E402
import update as U  # noqa: E402

RUNTIME_DIRS = ["courses", "progress", "dist"]
DIST_GITIGNORE = "*\n!.gitignore\n"


def resolve_root(arg):
    root = Path(arg or os.environ.get("BOTAI_ROOT") or ".").expanduser().resolve()
    return root


def cmd_setup(root, dry):
    for name in RUNTIME_DIRS:
        d = root / name
        print("  workspace %s/" % name)
        if not dry:
            d.mkdir(parents=True, exist_ok=True)
    if not dry:
        (root / "dist" / ".gitignore").write_text(DIST_GITIGNORE, encoding="utf-8")
    print("workspace ready:")
    for name in RUNTIME_DIRS:
        print("  %s" % (root / name))


def slugify(name):
    """Normalise a human name into a safe slug, or exit with a clear reason."""
    try:
        return C.slugify(name)
    except P.PathError as e:
        sys.exit("имя курса отклонено: %s" % e.message)


def safe_course_dir(root, slug):
    """`courses/<slug>` through the shared containment check, or exit."""
    try:
        return P.course_path(root, slug)
    except P.PathError as e:
        sys.exit("путь курса отклонён (%s): %s" % (e.code, e.message))


def safe_progress_file(root, slug):
    """`progress/<slug>.md` through the shared containment check, or exit."""
    try:
        return P.progress_path(root, slug)
    except P.PathError as e:
        sys.exit("путь дневника отклонён (%s): %s" % (e.code, e.message))


def cmd_new_course(root, name, title, dry):
    slug = slugify(name)
    d = safe_course_dir(root, slug)
    if d.exists():
        sys.exit("course already exists: %s" % d)
    print("scaffold %s" % d)
    if dry:
        return
    for sub in ("lessons", "assignments", "references"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    t = title or slug
    (d / "README.md").write_text(
        "# %s\n\n> Scaffolded by botai. Fill in the syllabus before teaching.\n" % t,
        encoding="utf-8",
        newline="\n",
    )
    (d / "syllabus.md").write_text(
        "# Syllabus\n\n## Modules\n\n1. _module one_ - _objective, prerequisites_\n",
        encoding="utf-8",
        newline="\n",
    )
    print("course scaffolded: %s" % d)
    print("next: fill in syllabus.md, then run: python scripts/cli.py progress --course %s" % slug)


def cmd_progress(root, course, dry):
    f = safe_progress_file(root, course)
    if not f.exists():
        print("no progress record yet: %s" % f)
        print("hint: the agent writes it with the maintaining-course-progress skill")
        return
    print("== progress: %s ==" % course)
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    shown = 0
    for ln in lines:
        if ln.startswith("## ") or ln.startswith("### ") or ln.startswith("- ["):
            print(ln)
            shown += 1
            if shown >= 60:
                break


def cmd_review(root, course, dry):
    # Resolve through the shared check even though this command only prints:
    # the course name is interpolated into paths shown to the agent, and a
    # traversal string must be refused rather than echoed back as guidance.
    d = safe_course_dir(root, course)
    print("review workflow for %s:" % course)
    print("  - locate the student's submission under %s/assignments/" % d)
    print("  - run the giving-feedback skill (rubric + least-assistance-first)")
    print("  - never reveal the answer to a graded task before the attempt")
    print("route to the agent: 'review my submission for %s with the rubric'" % course)


def cmd_courses(root, dry):
    found = False
    for d in sorted((root / "courses").glob("*")):
        if not d.is_dir():
            continue
        found = True
        title = d.name
        readme = d / "README.md"
        if readme.exists():
            first = readme.read_text(encoding="utf-8", errors="replace").splitlines()
            if first and first[0].startswith("# "):
                title = first[0][2:]
        stat = "(no progress yet)"
        pf = root / "progress" / ("%s.md" % d.name)
        if pf.exists():
            for ln in pf.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("## "):
                    stat = ln[3:]
                    break
        print("%s - %s %s" % (d.name, title, stat))
    if not found:
        print("(no courses yet - run: python scripts/cli.py new-course --name <slug>)")


def cmd_course_set(root, course, dry):
    d = safe_course_dir(root, course)
    if not d.is_dir():
        print("no such course: %s" % d)
        print("list: python scripts/cli.py courses")
        sys.exit(2)
    if dry:
        print("would set active course: %s" % course)
        return
    active_dir = root / ".botai"
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "active").write_text(course, encoding="utf-8")
    print("active course: %s" % course)


def cmd_active(root, dry):
    f = root / ".botai" / "active"
    if f.exists():
        val = f.read_text(encoding="utf-8", errors="replace").strip()
        if val:
            print("active course: %s" % val)
            return
    print("no active course (run: python scripts/cli.py course-set --course <slug>)")


def cmd_doctor(root, dry):
    print("platform   : %s" % sys.platform)
    print("root       : %s" % root)
    for tool in ("git", "python3", "python", "node", "markdownlint-cli2", "make"):
        print("  %-14s: %s" % (tool, "yes" if shutil.which(tool) else "no"))
    cs = [p.name for p in (root / "courses").glob("*") if p.is_dir()] if (root / "courses").exists() else []
    ps = [p.name for p in (root / "progress").glob("*.md")] if (root / "progress").exists() else []
    print("courses     : %s" % (" ".join(cs) or "(none - run setup)"))
    print("progress    : %s" % (" ".join(ps) or "(none)"))
    for slug in cs:
        st = corpus_state(root / "courses" / slug)
        print("  corpus %-24s: %s" % (slug, st["summary"]))

    # Provenance: a project that cannot say which harness it runs cannot be
    # updated responsibly, so the doctor reports it rather than assuming it.
    rec = H.load_record(root) or {}
    print("harness     : %s" % (H.read_version(root) or "(no VERSION yet)"))
    print("  source    : %s" % (rec.get("source") or H.DEFAULT_SOURCE))
    print("  ref       : %s" % (rec.get("ref") or H.DEFAULT_REF))
    print("  updated   : %s" % (rec.get("updated_at") or rec.get("installed_at") or "(never recorded)"))
    if rec.get("files"):
        print("  fingerprint: %d files" % len(rec["files"]))
    else:
        print("  fingerprint: none - first `cli.py update` will record one")
    for cdir in sorted((root / "courses").glob("*")):
        if cdir.is_dir():
            print("  course %-20s: %s" % (cdir.name, C.course_url(cdir) or "(no source recorded)"))


# --- Corpus acquisition -----------------------------------------------------
#
# A course that publishes a corpus is not ready to teach until that corpus is
# installed. The course repo owns the how (tools/corpus_fetch.py reads its own
# manifests); this command is the workspace-level front door that finds the
# course's fetcher, runs it, and reports whether the corpus is actually usable.

CORPUS_URL_KEYS = ("COURSE_INDEX_URL", "COURSE_CORPUS_URL")
CORPUS_ROOT_KEYS = ("COURSE_CORPUS_ROOT",)


def corpus_state(course_dir):
    """Report whether a course corpus is installed, without downloading.

    Returns a dict with keys: summary, manifests, installed, fetcher.
    """
    if not course_dir.is_dir():
        return {"summary": "(no such course)", "manifests": [], "installed": False, "fetcher": None}

    manifests = [m for m in ("index-manifest.json", "corpus-manifest.json")
                 if (course_dir / m).exists()]
    fetcher = course_dir / "tools" / "corpus_fetch.py"
    if not fetcher.exists():
        fetcher = course_dir / "tools" / "index_fetch.py" if (course_dir / "tools" / "index_fetch.py").exists() else None

    root = os.environ.get("COURSE_CORPUS_ROOT")
    if not root:
        return {
            "summary": "COURSE_CORPUS_ROOT not set - cannot tell where the corpus lives",
            "manifests": manifests, "installed": False, "fetcher": fetcher,
        }

    idx = Path(root) / "index"
    txt = Path(root) / "txt"
    have_index = idx.is_dir() and (idx / "config.json").exists()
    n_txt = len(list(txt.glob("*.txt"))) if txt.is_dir() else 0

    if have_index and n_txt:
        summary = "installed (index + %d texts)" % n_txt
    elif have_index:
        summary = "PARTIAL - index present but no texts under %s" % txt
    elif n_txt:
        summary = "PARTIAL - %d texts but no index under %s" % (n_txt, idx)
    else:
        summary = "MISSING - run: python scripts/cli.py corpus --course %s" % course_dir.name
    return {"summary": summary, "manifests": manifests, "installed": have_index and bool(n_txt), "fetcher": fetcher}


def cmd_corpus(root, course, dry, force):
    """Acquire (or report on) a course corpus. Run by deploy/setup, not by hand."""
    if not course:
        sys.exit("usage: cli.py corpus --course <slug> [--force] [--dry-run]")
    cdir = safe_course_dir(root, course)
    if not cdir.is_dir():
        print("no such course: %s" % cdir)
        print("list: python scripts/cli.py courses")
        sys.exit(2)

    st = corpus_state(cdir)
    print("== corpus: %s ==" % course)
    print("  manifests : %s" % (", ".join(st["manifests"]) or "(none in the course repo)"))

    if not st["manifests"]:
        print("  this course publishes no corpus manifest - nothing to acquire.")
        print("  teach with explicitly labelled material outside the corpus instead.")
        return 0

    print("  state     : %s" % st["summary"])

    if st["installed"] and not force:
        print("  corpus already installed; nothing to do (use --force to refetch).")
        return 0

    if not st["fetcher"]:
        print("  no tools/corpus_fetch.py in the course - fetch manually per its CORPUS.md.")
        return 1

    if dry:
        print("  would run: %s" % st["fetcher"])
        return 0

    if not os.environ.get("COURSE_CORPUS_ROOT"):
        print("  COURSE_CORPUS_ROOT is not set - set it (see CORPUS.md) before fetching.")
        return 2

    print("  running: %s" % st["fetcher"])
    py = sys.executable or "python3"
    rc = subprocess.run([py, str(st["fetcher"])], cwd=str(cdir)).returncode
    if rc != 0:
        print("  corpus fetch FAILED (exit %d) - report the cause, do not substitute a URL" % rc)
        return rc
    st2 = corpus_state(cdir)
    print("  after fetch: %s" % st2["summary"])
    return 0 if st2["installed"] else 1


def cmd_clean(root, dry):
    d = root / "dist"
    if not d.exists():
        print("nothing to clean (dist/ absent)")
        return
    if dry:
        print("would remove temporary files under %s (kept courses/ and progress/)" % d)
        return
    shutil.rmtree(d, ignore_errors=True)
    print("removed temporary files (kept courses/ and progress/)")


def cmd_state_migrate(root, course, dry, learner_id, confirm_split, apply=False):
    """Import a v1 Markdown progress record into the v2 store.

    `--dry-run` reports exactly what would be imported and stops. Preview is the
    default behaviour in spirit as well as in flags: importing a record is a
    decision about someone's study history, so `--apply` is required, and a
    record that cannot be attributed to one learner is refused rather than
    guessed at.
    """
    try:
        from botai_core import legacy, store as core_store
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        print("установите зависимости: python3 -m pip install --require-hashes "
              "-r requirements-core.lock")
        return 2

    if not course:
        print("usage: cli.py state-migrate --course <slug> [--learner <id>] "
              "[--confirm-split stu-01=<id>] [--apply]")
        return 2

    try:
        legacy_file = P.progress_path(root, course)
    except P.PathError as e:
        print("путь дневника отклонён (%s): %s" % (e.code, e.message))
        return 2
    if not legacy_file.is_file():
        print("запись v1 не найдена: %s" % legacy_file)
        print("ничего не изменено")
        return 2

    parsed = legacy.read_legacy_progress(legacy_file)
    report = parsed.as_report()

    print("== миграция записи прогресса: %s ==" % course)
    print("  источник      : %s" % legacy_file)
    print("  sha256        : %s" % parsed.source_sha256)
    print("  строк         : %d (распознано %d)" % (parsed.total_lines, parsed.parsed_lines))
    print("  обучающихся   : %d" % len(parsed.students))
    for student in report["students"]:
        print("    %s %s" % (student["legacy_id"],
                             ("(%s)" % student["name"]) if student["name"] else ""))
        if student["legacy_claim"]:
            print("      заявлено: %s -> импортируется как %s (%s)"
                  % (student["legacy_claim"], student["imported_stage"], student["stage_basis"]))
        if student["module_hint"]:
            print("      привязка: %s" % student["module_hint"])
        else:
            print("      привязка: не указана — цель не угадывается, запись останется на проверку")
    print("  оцениваемые   : %s" % (", ".join(parsed.graded) or "(не указаны)"))
    print("  тренировочные : %s" % (", ".join(parsed.practice) or "(не указаны)"))
    print("  тупиков       : %d" % len(parsed.dead_ends))
    print("  на проверку   : %d строк (догадки не подставляются)"
          % len(parsed.pending_review))
    for warning in report["warnings"]:
        print("  ВНИМАНИЕ: %s" % warning)

    if parsed.multi_student and not confirm_split:
        print()
        print("  файл описывает нескольких обучающихся. Автоматический импорт")
        print("  запрещён: приписать одному человеку чужие строки нельзя.")
        print("  Разделите записи и укажите соответствие, например:")
        print("    --confirm-split stu-01=<id-обучающегося>")
        print("  ничего не изменено")
        return 2

    if dry or not apply:
        print()
        print("  предпросмотр: ничего не записано.")
        print("  для импорта повторите с --apply (исходный файл сохраняется целиком)")
        return 0

    learner_id = learner_id or default_learner_id(root)
    try:
        with core_store.Store.open(root, learner_id) as st:
            result = legacy.import_into_store(
                st, parsed, course_id=course,
                course_revision={"kind": "local_snapshot", "id": parsed.source_sha256},
                confirm_split=confirm_split,
            )
    except core_store.StoreError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        print("  ничего не изменено")
        return 1

    print()
    print("  импортировано: %d заявленных состояний" % len(result["claims_written"]))
    print("  исходник сохранён как артефакт: %s" % result["preserved_artifact"][:16])
    print("  запись состояния: .botai/state.sqlite3")
    return 0


def _load_core():
    """Import the v2 core, or explain what is missing and return None."""
    try:
        from botai_core import course as core_course, policy as core_policy
        return core_course, core_policy
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        print("установите зависимости: python3 -m pip install --require-hashes "
              "-r requirements-core.lock")
        return None


def cmd_course_inspect(root, course, dry):
    """Describe a course without accepting anything and without running it.

    Read-only by construction: it reads `botai/course.json` and the track, and
    never executes a script from the course. The point is that a human sees what
    a course declares — its graded rules, its rights, its gaps — before that
    declaration governs any help.
    """
    loaded = _load_core()
    if loaded is None:
        return 2
    core_course, _core_policy = loaded

    if not course:
        print("usage: cli.py course-inspect --course <slug>")
        return 2
    try:
        course_dir = P.course_path(root, course, must_exist=True)
    except P.PathError as e:
        print("путь курса отклонён (%s): %s" % (e.code, e.message))
        return 2

    try:
        report = core_course.inspect(course_dir)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2

    print("== осмотр курса: %s ==" % course)
    print("  каталог     : %s" % report["course_dir"])
    print("  контракт    : %s" % ("есть" if report["has_contract"]
                                  else "НЕТ (%s)" % report["contract_path"]))
    if report["has_contract"]:
        print("  course_id   : %s" % report["course_id"])
        print("  название    : %s" % report["title"])
        licenses = report["licenses"]
        print("  права       : %s" % ("не объявлены — публичное распространение "
                                      "заблокировано до уточнения"
                                      if licenses is None else licenses))
        print("  вклад       : %s" % ("разрешён" if report["contribution_enabled"]
                                      else "не предусмотрен контрактом"))
        print("  оцениваемость заданий: %s"
              % (", ".join(report.get("assessment_kinds") or []) or "(заданий нет)"))
        if report["unknown_assessment_count"]:
            print("    из них с неизвестной оцениваемостью: %d "
                  "(действует строгий режим)" % report["unknown_assessment_count"])
        print("  задания:")
        for assignment in report["assignments"]:
            mark = "" if assignment["present"] else "  [ФАЙЛ ОТСУТСТВУЕТ]"
            print("    %-24s %-9s %s%s" % (assignment["assignment_id"],
                                           assignment["assessment"],
                                           assignment["path"], mark))
        print("  цели        : %d в %d модулях"
              % (len(report["objectives"]), len(report["modules"])))
    print("  готовность  : %s" % report["readiness"])

    if report["problems"]:
        print()
        print("  замечания:")
        for problem in report["problems"]:
            print("    [%s] %s" % (problem["code"], problem["message_ru"]))

    print()
    if not report["has_contract"]:
        print("  Курс не объявляет условий: оцениваемость всех заданий считается")
        print("  неизвестной, и готовые разборы не выдаются. Создайте")
        print("  botai/course.json по схеме schemas/v2/course.schema.json.")
        return 3

    if report["readiness"] != "inspectable":
        print("  Курс не будет принят, пока замечания выше не устранены.")
        return 2

    print("  Ничего не принято и не изменено. Проверьте условия и выполните:")
    print("    python scripts/cli.py course-accept --course %s" % course)
    return 0


def cmd_course_accept(root, course, dry, kind=None, url=None, teaching_remote=None,
                      teaching_branch=None, contribution_remote=None):
    """Accept the course contract as the policy that governs this course.

    This is a human operation on purpose. There is no equivalent tool in the
    tutoring MCP surface: an agent that could accept a contract could accept a
    contract that reclassifies a graded assignment as practice. The accepted
    revision is snapshotted under `.botai/accepted/`, so editing
    `botai/course.json` in a student branch afterwards changes nothing until a
    human accepts the new revision.
    """
    loaded = _load_core()
    if loaded is None:
        return 2
    core_course, _core_policy = loaded

    if not course:
        print("usage: cli.py course-accept --course <slug>")
        return 2
    try:
        course_dir = P.course_path(root, course, must_exist=True)
    except P.PathError as e:
        print("путь курса отклонён (%s): %s" % (e.code, e.message))
        return 2

    try:
        report = core_course.inspect(course_dir)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2

    if not report["has_contract"]:
        print("  курс %s не объявляет условий (%s отсутствует)" % (course, report["contract_path"]))
        print("  принять нечего: не выдумывайте оцениваемость — создайте контракт")
        return 3

    try:
        relative = course_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # pragma: no cover - course_path already contains it
        relative = "courses/%s" % course

    if dry:
        print("== принятие курса (предпросмотр): %s ==" % course)
        print("  course_id   : %s" % report["course_id"])
        print("  будет принято: оцениваемость заданий %s"
              % (", ".join(report.get("assessment_kinds") or []) or "(заданий нет)"))
        if report["problems"]:
            print("  замечания, из-за которых принятие будет отклонено: %d"
                  % len(report["problems"]))
            for problem in report["problems"][:6]:
                print("    [%s] %s" % (problem["code"], problem["message_ru"]))
        print("  ничего не записано (--dry-run)")
        return 0

    try:
        binding = core_course.accept(
            root, course_dir, slug=course, source_url=url,
            teaching_remote=teaching_remote, teaching_branch=teaching_branch,
            contribution_remote=contribution_remote, acceptance_kind=kind,
            repository_root=relative,
        )
    except core_course.CourseError as e:
        print("  курс НЕ принят (%s): %s" % (e.code, e.message))
        print("  ничего не изменено")
        return 2

    print("== курс принят: %s ==" % binding["course_id"])
    print("  ревизия     : %s %s" % (binding["accepted_revision"]["kind"],
                                     binding["accepted_revision"]["id"][:16]))
    print("  контракт    : %s" % binding["accepted_contract_hash"][:16])
    print("  программа   : %s" % binding["accepted_track_hash"][:16])
    print("  снимок      : %s" % binding["material_snapshot"])
    print("  оцениваемость: %s" % ", ".join(binding["assessment_kinds"]))
    print()
    print("  Правила зафиксированы. Правка botai/course.json в рабочей копии")
    print("  больше не меняет их: новая ревизия принимается отдельной командой.")
    return 0


def cmd_course_status(root, course, dry):
    """What was accepted, and what changed in the course since then."""
    loaded = _load_core()
    if loaded is None:
        return 2
    core_course, _core_policy = loaded

    bindings = core_course.list_bindings(root)
    if not course and not bindings:
        print("ни один курс не принят")
        print("посмотрите условия и примите: python scripts/cli.py course-inspect "
              "--course <slug>")
        return 0

    if not course:
        print("== принятые курсы ==")
        for binding in bindings:
            print("  %-24s %-9s контракт %s"
                  % (binding["course_id"], binding["slug"],
                     binding["accepted_contract_hash"][:16]))
            print("    оцениваемость: %s"
                  % (", ".join(binding.get("assessment_kinds") or []) or "(заданий нет)"))
        return 0

    try:
        binding = core_course.load_binding(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2
    if binding is None:
        print("курс %s не принят" % course)
        print("посмотрите условия: python scripts/cli.py course-inspect --course %s" % course)
        return 3

    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2

    print("== принятый курс: %s ==" % course)
    print("  название    : %s" % accepted.contract["title"])
    print("  ревизия     : %s %s" % (accepted.revision["kind"], accepted.revision["id"]))
    print("  принят      : %s (%s)" % (binding["accepted_at"], binding["acceptance_kind"]))
    print("  контракт    : %s" % binding["accepted_contract_hash"])
    print("  программа   : %s" % binding["accepted_track_hash"])
    print("  материал    : %s" % binding["material_snapshot"])
    print("  оцениваемость заданий: %s"
          % (", ".join(binding.get("assessment_kinds") or []) or "(заданий нет)"))

    try:
        course_dir = P.course_path(root, course, must_exist=True)
    except P.PathError as e:
        print("  рабочая копия недоступна (%s): %s" % (e.code, e.message))
        return 0

    diff = core_course.diff_against_disk(root, course_dir)
    print()
    print("== расхождение с рабочей копией ==")
    if diff["problems"]:
        for problem in diff["problems"]:
            print("  [%s] %s" % (problem["code"], problem["message_ru"]))
    print("  контракт изменён : %s" % ("да" if diff["contract_changed"] else "нет"))
    print("  программа изменена: %s" % ("да" if diff["track_changed"] else "нет"))

    if diff["assessment_changed"]:
        print()
        print("  ВНИМАНИЕ: изменилась оцениваемость. Принятая политика в силе,")
        print("  рабочая копия её не меняет — новая ревизия принимается отдельно:")
        for change in diff["assessment_changed"]:
            print("    %s.%s: принято %r -> в копии %r"
                  % (change["assignment_id"], change["field"],
                     change["accepted"], change["on_disk"]))
    if diff["new_assignments"]:
        print()
        print("  Новые задания в рабочей копии (в принятом контракте их нет,")
        print("  поэтому их оцениваемость считается неизвестной):")
        for entry in diff["new_assignments"]:
            print("    %s (%s)" % (entry["assignment_id"], entry["assessment"]))
    if diff["missing_from_contract"]:
        print()
        print("  Задания принятого контракта исчезли из рабочей копии: %s"
              % ", ".join(diff["missing_from_contract"]))
    if diff["on_disk_revision"] and diff["accepted_revision"]:
        same = (diff["on_disk_revision"]["id"] == diff["accepted_revision"]["id"])
        print("  ревизия на диске : %s %s%s"
              % (diff["on_disk_revision"]["kind"], diff["on_disk_revision"]["id"][:16],
                 "" if same else "  (отличается от принятой)"))

    if diff["contract_changed"] or diff["track_changed"] or diff["problems"]:
        print()
        print("  Чтобы принять новую ревизию после просмотра: course-accept")
    return 0


def cmd_policy_check(root, course, assignment, level, preference):
    """Explain what the accepted policy permits for one assignment and level.

    This is the deterministic answer to "may I give this help?" — the same
    computation the tutoring surface uses, exposed so a human (or a test) can
    see the decision and the rule it rests on rather than trusting the model's
    account of its own constraint.
    """
    loaded = _load_core()
    if loaded is None:
        return 2
    core_course, core_policy = loaded

    if not course or not assignment:
        print("usage: cli.py policy-check --course <slug> --assignment <id> "
              "[--level HINT|EXAMPLE|SOLUTION] [--preference <style>]")
        return 2

    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 3 if e.code == "COURSE_NOT_ACCEPTED" else 2

    try:
        decision = core_policy.request_help(
            accepted, assignment_id=assignment, intent="solution" if level == "SOLUTION"
            else ("example" if level == "EXAMPLE" else "hint"),
            preference=preference, student_requested=True,
        )
        core_policy.validate_decision(decision)
    except ValueError as e:  # pragma: no cover - schema regression guard
        print("  решение не соответствует контракту политики: %s" % e)
        return 2

    resolved = core_policy.resolve_assessment(accepted, assignment)
    print("== политика помощи ==")
    print("  курс        : %s" % accepted.course_id)
    print("  задание     : %s" % assignment)
    print("  оцениваемость: %s (%s)" % (resolved["assessment"], resolved["source"]))
    print("    %s" % resolved["message_ru"])
    print("  запрошено   : %s" % level)
    print("  решение     : %s (%s)" % (decision["decision"], decision["reason_code"]))
    print("  предел      : %s" % (decision.get("assistance_ceiling") or "—"))
    print("  правило     : %s" % (decision.get("rule_ref") or "—"))
    print("  %s" % decision["message_ru"])
    if decision.get("alternatives"):
        print("  вместо этого:")
        for option in decision["alternatives"]:
            print("    - %s" % option)
    return 0 if decision["decision"] == "allow" else 3


def _open_session(root, course, learner=None, *, create=True):
    """Open a session service, or explain precisely what is missing."""
    try:
        from botai_core import course as core_course, session as core_session
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        print("установите зависимости: python3 -m pip install --require-hashes "
              "-r requirements-core.lock")
        return None, None
    if not course:
        print("нужен --course <slug>")
        return None, None
    learner_id = learner or default_learner_id(root)
    try:
        service = core_session.SessionService.open(root, learner_id, course,
                                                   create=create)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return None, None
    except Exception as e:  # noqa: BLE001 - store errors keep their own codes
        code = getattr(e, "code", type(e).__name__)
        print("  отказ (%s): %s" % (code, e))
        return None, None
    return service, learner_id


def cmd_session_start(root, course, learner, objectives, minutes, modes, dry):
    """Open a learning session for an accepted course."""
    service, learner_id = _open_session(root, course, learner, create=not dry)
    if service is None:
        return 2
    try:
        if dry:
            accepted = service.course
            print("== начало занятия (предпросмотр) ==")
            print("  курс        : %s" % accepted.course_id)
            print("  ревизия     : %s" % accepted.revision["id"][:16])
            print("  цели занятия: %s" % (", ".join(objectives) or "(будут выбраны)"))
            print("  режим       : %s" % ", ".join(modes))
            print("  длительность: %d мин" % minutes)
            print("  согласие    : %s" % (learner_id if learner else "будет запрошено"))
            print("  ничего не записано (--dry-run)")
            return 0

        # Consent comes from the store, not from a flag: a session that teaches
        # without recorded consent would be a session nobody agreed to.
        consent_version = None
        for consent in service.store.list_entities("consent"):
            if consent.get("withdrawn_at") is None:
                consent_version = consent.get("consent_id")
                break

        body, result, replayed = service.start(
            objective_ids=objectives, modes=tuple(modes), session_minutes=minutes,
            consent_version=consent_version,
        )
        print("== занятие открыто ==")
        print("  session_id  : %s" % body["session_id"])
        print("  состояние   : %s" % body["state"])
        if replayed:
            print("  (повтор запроса: возвращён прежний результат)")
        if consent_version is None:
            print()
            print("  Записано согласие на обработку данных не найдено, поэтому")
            print("  занятие не начинается. Оформите согласие отдельно:")
            print("    python scripts/cli.py consent-set --course %s" % course)
        print("  следующий шаг: python scripts/cli.py session-next "
              "--session %s" % body["session_id"])
        return 0
    finally:
        service.close()


def cmd_session_next(root, course, session, learner):
    """Print the teaching directive. A pure read — nothing is written."""
    service, _ = _open_session(root, course, learner)
    if service is None:
        return 2
    try:
        from botai_core import tutoring as core_tutoring
        body, directive = service.next_step(session)
        print("== следующий шаг ==")
        print("  сессия      : %s" % session)
        print("  состояние   : %s" % directive["state"])
        print("  действие    : %s" % directive["next_action"])
        print("  цель        : %s" % (directive["objective_id"] or "—"))
        print("  предел помощи: %s" % (directive["assistance_ceiling"] or "помощь не выдаётся"))
        print("  допустимые намерения: %s"
              % (", ".join(directive["allowed_intents"]) or "(нет)"))
        if directive["needed_inputs"]:
            print("  требуется   : %s" % ", ".join(directive["needed_inputs"]))
        print("  причина     : %s" % directive["reason"])
        print()
        print("  (session-next ничего не записывает: рекомендация — не прогресс)")
        return 0
    except core_tutoring.TutoringError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2
    finally:
        service.close()


def cmd_session_goal(root, course, session, objective, assignment, learner, confirm):
    """Choose the objective for a session (and optionally start work on it)."""
    service, _ = _open_session(root, course, learner)
    if service is None:
        return 2
    try:
        from botai_core import tutoring as core_tutoring
        _, version = service.get_session(session)
        if version == 0:
            print("сессия не найдена: %s" % session)
            return 2
        body, _, _ = service.configure_goal(
            session, objective, expected_version=version,
            assignment_id=assignment, confirm=confirm,
        )
        print("== цель занятия ==")
        print("  цель        : %s" % objective)
        print("  задание     : %s" % (assignment or "—"))
        print("  оцениваемость: %s" % body["assessment"])
        print("  состояние   : %s" % body["state"])
        if not confirm:
            print()
            print("  Цель выбрана. Начать работу по ней:")
            print("    python scripts/cli.py session-goal --session %s "
                  "--objective %s --confirm" % (session, objective))
        return 0
    except core_tutoring.TutoringError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2
    finally:
        service.close()


def cmd_session_attempt(root, course, session, objective, assignment, learner, text_file):
    """Record the learner's own attempt."""
    service, _ = _open_session(root, course, learner)
    if service is None:
        return 2
    try:
        from botai_core import tutoring as core_tutoring
        if text_file:
            path = Path(text_file)
            if not path.is_file():
                print("файл попытки не найден: %s" % path)
                return 2
            text = path.read_text(encoding="utf-8-sig")
            print("  попытка прочитана из %s (%d символов)" % (path, len(text)))
        else:
            if sys.stdin.isatty():
                print("Введите текст попытки, затем Ctrl+Z (Windows) или Ctrl+D:")
            text = sys.stdin.read()
        if not text.strip():
            print("попытка пуста: ничего не записано")
            return 2

        document, _, _ = service.record_attempt(
            session, objective_id=objective, assignment_id=assignment,
            text_excerpt=text[:65536], source="learner_report",
        )
        print("== попытка записана ==")
        print("  attempt_id  : %s" % document["attempt_id"])
        print("  помощь в цикле: %s" % document["assistance_max"])
        print("  источник    : %s" % document["source"])
        print()
        print("  Попытка принята как ваше собственное сообщение о работе —")
        print("  это не доказательство авторства, а основа для обратной связи.")
        return 0
    except core_tutoring.TutoringError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2
    finally:
        service.close()


def cmd_session_check(root, course, session, attempt, kind, verdict, criterion, learner):
    """Record a check result and let the reducer update mastery."""
    service, _ = _open_session(root, course, learner)
    if service is None:
        return 2
    try:
        from botai_core import tutoring as core_tutoring
        results = []
        for item in criterion or []:
            # criterion_id=pass|partial|fail|unknown[:evidence,evidence]
            head, _, evidence = item.partition(":")
            criterion_id, _, outcome = head.partition("=")
            refs = [e for e in evidence.split(",") if e.strip()]
            if outcome == "pass" and not refs:
                print("критерий %r зачтён без доказательства: укажите "
                      "criterion_id=pass:attempt_id" % criterion_id)
                return 2
            results.append({"criterion_id": criterion_id.strip(),
                            "result": outcome.strip() or "unknown",
                            "evidence_refs": refs, "required": True})
        if not results:
            print("нужен хотя бы один --criterion criterion_id=pass|partial|fail")
            return 2

        document, state, reasoning, _, _ = service.record_check(
            session, attempt_id=attempt, kind=kind, verdict=verdict,
            criterion_results=results,
        )
        print("== проверка записана ==")
        print("  check_id    : %s" % document["check_id"])
        print("  вид         : %s" % kind)
        print("  вердикт     : %s (%s)" % (verdict, document["reliability"]))
        print()
        print("== освоение ==")
        print("  цель        : %s" % state["objective_id"])
        print("  стадия      : %s" % state["stage"])
        print("  причина     : %s" % reasoning[1])
        if state["stage"] == "demonstrated":
            print()
            print("  Это формирующая проверка бота, а не официальная оценка:")
            print("  человек может её исправить.")
        return 0
    except core_tutoring.TutoringError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2
    finally:
        service.close()


def cmd_session_pause(root, course, session, learner, target, reason, resume):
    """pause / block / close / cancel, or resume a stopped session.

    Resuming is expressed by naming the state to return to (`--resume`), not by
    a `--target`: the target of a resume is by definition where the session
    stopped, and letting a caller name an arbitrary state there would be the
    one way to skip work the automaton exists to protect.
    """
    service, _ = _open_session(root, course, learner)
    if service is None:
        return 2
    try:
        from botai_core import tutoring as core_tutoring
        body, version = service.get_session(session)
        if body is None:
            print("сессия не найдена: %s" % session)
            return 2

        if body["state"] in ("PAUSED", "BLOCKED"):
            saved = body.get("resume_state")
            if resume and resume != saved:
                print("  отказ (RESUME_STATE_MISMATCH): сессия остановлена в "
                      "состоянии %s, а не %s" % (saved, resume))
                return 2
            target_state = saved or resume
            if not target_state:
                print("  отказ (RESUME_STATE_MISSING): неизвестно, куда возвращать "
                      "сессию: сохранённое состояние потеряно")
                return 2
            updated, _, _ = service.transition(session, target_state,
                                               expected_version=version,
                                               resume_state=target_state)
            print("== состояние занятия ==")
            print("  было        : %s" % body["state"])
            print("  стало       : %s" % updated["state"])
            print("  продолжено с: %s" % updated["resume_state"] or "—")
            return 0

        if not target:
            print("usage: cli.py session-pause --session <id> "
                  "--target PAUSED|BLOCKED|COMPLETED|CANCELLED [--reason ...]")
            print("       (для продолжения остановленного занятия: без --target,")
            print("        необязательно --resume <состояние>)")
            return 2

        updated, _, _ = service.transition(
            session, target, expected_version=version, resume_state=resume,
            reason=reason,
        )
        print("== состояние занятия ==")
        print("  было        : %s" % body["state"])
        print("  стало       : %s" % updated["state"])
        if updated.get("resume_state"):
            print("  возврат в   : %s" % updated["resume_state"])
        return 0
    except core_tutoring.TutoringError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2
    finally:
        service.close()


def cmd_progress_v2(root, course, learner, as_json):
    """Progress from the v2 store, or the legacy document when there is none."""
    service, learner_id = _open_session(root, course, learner, create=False)
    if service is None:
        # Fall back to the legacy view rather than pretending v2 has nothing.
        print("(записи v2 нет: показан прежний дневник)")
        legacy_file = safe_progress_file(root, course)
        if legacy_file.is_file():
            print(legacy_file.read_text(encoding="utf-8", errors="replace"))
        else:
            print("дневника нет: %s" % legacy_file)
        return 0
    try:
        from botai_core import progress as core_progress
        document = core_progress.build_progress(
            course=service.course, learner_id=learner_id,
            sessions=service.store.list_entities("session", course_id=course),
            objective_states=service.objective_states(),
            attempts=service.store.list_entities("attempt", course_id=course),
            checks=service.store.list_entities("check", course_id=course),
        )
        if as_json:
            print(json.dumps(document, ensure_ascii=False, indent=2))
        else:
            print(core_progress.render_markdown(document))
        return 0
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        print("  отказ (%s): %s" % (code, e))
        return 1
    finally:
        service.close()


def cmd_consent_set(root, course, learner, purposes, provider, retention, dry):
    """Record the learner's consent. A human operation, never a tool call.

    Consent is the precondition for the whole cycle, so it is deliberately not
    reachable from the tutoring surface: a model that could record consent
    could manufacture the agreement it is supposed to be operating under. The
    command prints exactly what is being agreed to before writing it.
    """
    try:
        from botai_core import store as core_store
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not course:
        print("нужен --course <slug>")
        return 2

    learner_id = learner or default_learner_id(root)
    valid = {"learning_storage", "model_processing", "teacher_export", "publication"}
    selected = sorted({p.strip() for p in (purposes or []) if p.strip()}) or ["learning_storage"]
    unknown = [p for p in selected if p not in valid]
    if unknown:
        print("неизвестные назначения обработки: %s" % ", ".join(unknown))
        print("допустимые: %s" % ", ".join(sorted(valid)))
        return 2

    print("== согласие на обработку данных ==")
    print("  обучающийся : %s" % learner_id)
    print("  курс        : %s" % course)
    print("  назначения  : %s" % ", ".join(selected))
    print("  поставщик   : %s" % (provider or "не указан"))
    print("  хранение    : %s дн." % retention)
    print()
    print("  Что это значит:")
    for purpose in selected:
        if purpose == "learning_storage":
            print("    - учебные записи сохраняются локально в .botai/state.sqlite3")
        elif purpose == "model_processing":
            print("    - содержимое занятия передаётся выбранному поставщику модели")
        elif purpose == "teacher_export":
            print("    - вы сможете отдельно подготовить пакет для преподавателя")
        elif purpose == "publication":
            print("    - публикация вклада возможна; её всё равно выполняет человек")

    if dry:
        print()
        print("  ничего не записано (--dry-run)")
        return 0

    try:
        with core_store.Store.open(root, learner_id) as store:
            consent_id = core_store.new_id()
            body = {
                "schema_version": 2,
                "consent_id": consent_id,
                "learner_id": learner_id,
                "course_id": course,
                "version": "v2.0",
                "purposes": selected,
                "provider_label": provider,
                "data_categories": ["attempts", "checks", "objective_states"],
                "recipients": [],
                "retention_days": int(retention),
                "granted_at": core_store.now_iso(),
                "withdrawn_at": None,
            }
            result, replayed = store.apply(
                kind="consent", entity_id=consent_id,
                events=["consent.changed"], course_id=course,
                new_body=body,
                event_payloads=[{
                    "consent_id": consent_id,
                    "purposes": selected,
                    "decision": "granted",
                    "human_channel": "cli",
                    "_actor": "learner_cli",
                    "_provenance": "human_input",
                }],
            )
            print()
            print("  согласие записано: %s" % consent_id)
            print("  отозвать: python scripts/cli.py consent-withdraw "
                  "--course %s --consent %s" % (course, consent_id))
            return 0
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        print("  отказ (%s): %s" % (code, e))
        return 1


def cmd_consent_withdraw(root, course, learner, consent_id, dry):
    """Withdraw consent and report what it stops.

    Withdrawing does not silently delete anything: it blocks the purposes it
    names, tells the learner what a deletion would cover, and leaves the
    decision to delete as a separate, explicit act.
    """
    try:
        from botai_core import store as core_store
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not (course and consent_id):
        print("usage: cli.py consent-withdraw --course <slug> --consent <id>")
        return 2

    learner_id = learner or default_learner_id(root)
    try:
        with core_store.Store.open(root, learner_id, create=False) as store:
            body, version = store.get("consent", consent_id, course_id=course)
            if body is None:
                print("согласие не найдено: %s" % consent_id)
                return 2
            if body.get("withdrawn_at"):
                print("согласие уже отозвано: %s" % body["withdrawn_at"])
                return 0

            print("== отзыв согласия ==")
            print("  назначения  : %s" % ", ".join(body.get("purposes") or []))
            if dry:
                print("  ничего не записано (--dry-run)")
                return 0

            updated = dict(body)
            updated["withdrawn_at"] = core_store.now_iso()
            store.apply(
                kind="consent", entity_id=consent_id,
                events=["consent.changed"], course_id=course,
                expected_version=version, new_body=updated,
                event_payloads=[{
                    "consent_id": consent_id,
                    "purposes": body.get("purposes") or [],
                    "decision": "withdrawn",
                    "human_channel": "cli",
                    "_actor": "learner_cli",
                    "_provenance": "human_input",
                }],
            )
            print("  отозвано    : %s" % updated["withdrawn_at"])
            print()
            print("  Новые занятия по этим назначениям не начнутся.")
            print("  Уже сохранённые записи не удалены: удаление — отдельное")
            print("  решение. План удаления:")
            print("    python scripts/cli.py privacy-delete --course %s --plan" % course)
            return 0
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        print("  отказ (%s): %s" % (code, e))
        return 1


def cmd_corpus_status(root, course, as_json):
    """Report the corpus readiness for one accepted course."""
    try:
        from botai_core import corpus as core_corpus, course as core_course
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not course:
        print("нужен --course <slug>")
        return 2
    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 3 if e.code == "COURSE_NOT_ACCEPTED" else 2

    manifest_path = accepted.contract.get("corpus_manifest_path")
    if not manifest_path:
        result = {"status": "not_required", "course_id": accepted.course_id,
                  "reason": "принятый контракт не объявляет корпус"}
    else:
        try:
            base = (Path(root) / accepted.binding["repository_root"]).resolve()
            manifest, kind = core_corpus.load_manifest(base / manifest_path)
        except core_corpus.CorpusError as e:
            result = {"status": "failed", "course_id": accepted.course_id,
                      "reason": e.message, "code": e.code}
        else:
            result = core_corpus.status(root, accepted.course_id, manifest)
            result["manifest_kind"] = kind
            result["manifest_path"] = manifest_path

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("ready", "not_required") else 3

    print("== корпус курса: %s ==" % accepted.course_id)
    print("  состояние   : %s" % result["status"])
    if result.get("reason"):
        print("  причина     : %s" % result["reason"])
    if result.get("manifest_kind") == "legacy_adapted":
        print("  манифест    : преобразован из v1 — уровень проверки ниже, чем у v2")
    if result.get("directory"):
        print("  каталог     : %s" % result["directory"])
    if result.get("checked"):
        print("  проверено   : %d файлов" % result["checked"])
    for label, key in (("отсутствуют", "missing"), ("не совпали", "mismatched"),
                       ("без хешей", "unverified_files")):
        if result.get(key):
            print("  %-12s: %s" % (label, result[key][:3]))
    if result["status"] == "ready":
        print()
        print("  Готово: цитаты можно проверять по этому корпусу.")
    elif result["status"] == "not_required":
        print()
        print("  Корпус не предусмотрен контрактом. Это не «не проверено»,")
        print("  а «проверять нечего».")
    else:
        print()
        print("  Обучение с цитированием по этому корпусу невозможно, пока он")
        print("  не подтверждён. Догадки вместо источника не подставляются.")
    return 0 if result["status"] in ("ready", "not_required") else 3


def cmd_corpus_acquire(root, course, dry, offline):
    """Download and verify the course corpus from its declared manifest."""
    try:
        from botai_core import corpus as core_corpus, course as core_course
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not course:
        print("нужен --course <slug>")
        return 2
    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 3 if e.code == "COURSE_NOT_ACCEPTED" else 2

    manifest_path = accepted.contract.get("corpus_manifest_path")
    if not manifest_path:
        print("принятый контракт не объявляет корпус — скачивать нечего")
        return 0

    base = (Path(root) / accepted.binding["repository_root"]).resolve()
    try:
        manifest, kind = core_corpus.load_manifest(base / manifest_path)
    except core_corpus.CorpusError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2

    report = core_corpus.acquire(root, accepted.course_id, manifest,
                                 dry_run=dry, offline=offline)

    print("== корпус: %s ==" % accepted.course_id)
    print("  манифест    : %s%s" % (manifest_path,
                                    " (преобразован из v1)" if kind == "legacy_adapted" else ""))
    print("  состояние   : %s" % report["status"])
    for item in report["downloaded"]:
        if "would_verify" in item:
            print("  будет скачано: %s" % item["url"])
        else:
            print("  скачано     : %s (%d байт)" % (item["url"], item["byte_size"]))
    for item in report["unpacked"]:
        print("  распаковано : %s, файлов %d" % (item["artifact_id"], item["files"]))
    for warning in report["warnings"]:
        print("  ВНИМАНИЕ: %s" % warning)
    if report.get("error"):
        print("  ошибка      : [%s] %s" % (report["error"]["code"],
                                           report["error"]["message"]))

    if report["status"] == "ready":
        print()
        print("  Корпус установлен и подтверждён по манифесту.")
        return 0
    if dry:
        return 0
    return 1


def cmd_source_search(root, course, query, limit, as_json):
    """Scoped search over the accepted course materials."""
    try:
        from botai_core import course as core_course, retrieval
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not (course and query):
        print("usage: cli.py source-search --course <slug> --query <текст> [--limit N]")
        return 2
    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 3 if e.code == "COURSE_NOT_ACCEPTED" else 2

    try:
        result = retrieval.search(accepted, query, limit=limit,
                                  material_snapshot=accepted.binding.get("material_snapshot"))
    except retrieval.RetrievalError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2

    if as_json:
        payload = {k: v for k, v in result.items()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("== поиск по материалам курса ==")
    print("  область     : %s (файлов %d, %s)"
          % (result["scope"]["course_id"], result["scope"]["files_searched"],
             "снимок принятой ревизии" if result["scope"]["using_snapshot"]
             else "рабочая копия — не принятый снимок"))
    print("  исключено   : %s" % (", ".join(result["scope"]["exclude_roots"]) or "—"))
    print("  способ      : %s" % result["retrieval_method"])
    if not result["hits"]:
        print()
        print("  Ничего не найдено. Это честный not_found, а не «источника нет»:")
        print("  возможно, запрос сформулирован другими словами.")
        return 0
    print()
    for index, hit in enumerate(result["hits"], 1):
        locator = hit["locator"]
        if "lines" in locator:
            where = "строки %d–%d" % (locator["lines"]["start"], locator["lines"]["end"])
        elif "chunk" in locator:
            where = "фрагмент %d" % locator["chunk"]["chunk_id"]
        else:
            where = str(locator)
        print("  %d. %s — %s" % (index, hit["relative_path"], where))
        print("     %s" % hit["text"][:160])
    return 0


def cmd_quote_verify(root, course, citation_path, as_json):
    """Verify one citation against the accepted materials."""
    try:
        from botai_core import course as core_course, retrieval
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not (course and citation_path):
        print("usage: cli.py quote-verify --course <slug> --citation <файл.json>")
        return 2
    path = Path(citation_path)
    if not path.is_file():
        print("файл цитаты не найден: %s" % path)
        return 2
    try:
        citation = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        print("не удалось прочитать цитату: %s" % e)
        return 2

    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 3 if e.code == "COURSE_NOT_ACCEPTED" else 2

    try:
        report = retrieval.verify_quote(
            accepted, citation,
            material_snapshot=accepted.binding.get("material_snapshot"))
    except retrieval.RetrievalError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 2

    allowed, warnings = retrieval.validate_citation(report)
    if as_json:
        report["allowed"] = allowed
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if allowed else 3

    print("== проверка цитаты ==")
    print("  статус текста : %s" % report["quote_status"])
    print("  координаты    : %s" % report["coordinate_check"])
    print("  смысл         : %s (строка не доказывает утверждение)"
          % report["support_status"])
    print("  %s" % report["quote_status_reason"])
    if warnings:
        for warning in warnings:
            print("  ВНИМАНИЕ: %s" % warning)
    print()
    print("  Цитату можно показывать: %s" % ("да" if allowed else "НЕТ"))
    return 0 if allowed else 3


def _open_operations(root, learner=None, *, create=True):
    try:
        from botai_core import operation as core_operation
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return None
    learner_id = learner or default_learner_id(root)
    return core_operation.OperationService.open(root, learner_id, create=create)


def cmd_env_plan(root, course, spec_path, learner):
    """Build an environment plan. Nothing is executed."""
    try:
        from botai_core import course as core_course, environment as core_env
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not (course and spec_path):
        print("usage: cli.py env-plan --course <slug> --spec <environment.json>")
        return 2
    try:
        accepted = core_course.load_accepted(root, course)
    except core_course.CourseError as e:
        print("  отказ (%s): %s" % (e.code, e.message))
        return 3 if e.code == "COURSE_NOT_ACCEPTED" else 2

    spec_file = Path(spec_path)
    if not spec_file.is_file():
        print("спецификация среды не найдена: %s" % spec_file)
        return 2
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8-sig"))
        from botai_core import schemas
        schemas.validate(spec, "environment")
    except (OSError, ValueError) as e:
        print("не удалось прочитать спецификацию: %s" % e)
        return 2
    except Exception as e:  # noqa: BLE001
        print("спецификация не соответствует схеме: %s" % getattr(e, "message", e))
        return 2

    service = _open_operations(root, learner)
    if service is None:
        return 2
    try:
        input_hashes = {}
        for entry in spec.get("inputs") or []:
            candidate = Path(root) / accepted.binding["repository_root"] / entry["path"]
            if candidate.is_file():
                from botai_core import corpus as core_corpus
                input_hashes[entry["input_id"]] = core_corpus.sha256_file(candidate)
            else:
                print("  ВНИМАНИЕ: вход %s не найден (%s)"
                      % (entry["input_id"], entry["path"]))

        try:
            plan, operation = service.create_plan(spec, course=accepted,
                                                  input_hashes=input_hashes)
        except core_env.EnvironmentError as e:
            print("  план не собран (%s): %s" % (e.code, e.message))
            return 2

        print(core_env.render_plan(plan, course_title=accepted.contract["title"]))
        print()
        if input_hashes:
            print("Входы закреплены: %s" % ", ".join(sorted(input_hashes)))
        return 0
    finally:
        service.close()


def cmd_env_apply(root, operation_id, learner, wait):
    """Execute a plan that a human already approved."""
    try:
        from botai_core import operation as core_operation
    except ImportError as e:
        print("ядро v2 недоступно: %s" % e)
        return 2
    if not operation_id:
        print("usage: cli.py env-apply --operation <id> [--wait]")
        return 2

    service = _open_operations(root, learner)
    if service is None:
        return 2
    try:
        plan = service.get_plan(operation_id)
        if plan is None:
            print("операция не найдена: %s" % operation_id)
            return 2

        # Recompute the input hashes from the workspace so the check at apply
        # time is a real comparison and not a formality.
        input_hashes = {}
        from botai_core import corpus as core_corpus
        for input_id, _expected in (plan.get("input_hashes") or {}).items():
            for step in plan["steps"]:
                for path in step["parameters"].get("write_roots") or []:
                    candidate = Path(root) / path
                    if candidate.is_file():
                        input_hashes[input_id] = core_corpus.sha256_file(candidate)

        try:
            result = service.apply(operation_id, actor="human_cli",
                                   current_hashes=input_hashes or None)
        except core_operation.OperationError as e:
            print("  отказ (%s): %s" % (e.code, e.message))
            if e.detail.get("decision"):
                for alternative in e.detail["decision"].get("alternatives", []):
                    print("    - %s" % alternative)
            return 4 if e.code in ("APPROVAL_REQUIRED",) else 3

        print("== операция %s ==" % operation_id)
        print("  состояние   : %s" % result["status"])
        if result.get("replayed"):
            print("  (повтор не выполнялся: возвращён прежний результат)")
        for step in result["operation"]["results"]:
            mark = "ok  " if step.get("ok") else "FAIL"
            note = " (пропущен: %s)" % step["note"] if step.get("skipped") else ""
            print("  [%s] %-16s %s%s" % (mark, step["step_id"], step["kind"], note))
        if result.get("report", {}).get("message_ru"):
            print("  %s" % result["report"]["message_ru"])
        return 0 if result["status"] not in ("FAILED", "CANCELLED") else 1
    finally:
        service.close()


def cmd_action_approve(root, operation_id, learner, decision, dry):
    """Record a human's decision about one plan. Human-only by construction.

    The `human_channel` argument is not settable from a tool call: this command
    runs in a terminal, and that is what it reports. A model cannot reach this
    function, which is what makes the approval meaningful rather than decorative.
    """
    if not operation_id:
        print("usage: cli.py action-approve --operation <id> [--deny]")
        return 2

    service = _open_operations(root, learner)
    if service is None:
        return 2
    try:
        plan = service.get_plan(operation_id)
        if plan is None:
            print("операция не найдена: %s" % operation_id)
            return 2

        from botai_core import environment as core_env
        print(core_env.render_plan(plan))
        print()

        if dry:
            print("ничего не записано (--dry-run)")
            return 0

        if decision == "granted":
            answer = input("Подтвердить выполнение этого плана? [y/N] ").strip().lower()
            if answer not in ("y", "yes", "д", "да"):
                print("не подтверждено: ничего не записано")
                return 0

        try:
            approval = service.approve(operation_id, decision=decision,
                                       human_channel="cli:%s" % _terminal_label())
        except Exception as e:  # noqa: BLE001
            print("  отказ (%s): %s" % (getattr(e, "code", type(e).__name__), e))
            return 3

        print("разрешение записано: %s (%s)" % (approval["approval_id"][:8],
                                                approval["decision"]))
        if decision == "granted":
            print("выполнить: python scripts/cli.py env-apply --operation %s"
                  % operation_id)
        return 0
    finally:
        service.close()


def _terminal_label():
    """A best-effort label for which terminal approved a plan."""
    for name in ("WT_SESSION", "TERM_SESSION_ID", "SSH_TTY", "TERM"):
        if os.environ.get(name):
            return "%s=%s" % (name, os.environ[name][:24])
    return "tty"


def cmd_env_status(root, operation_id, course, learner, as_json):
    """Show one operation, or list the operations of a course."""
    service = _open_operations(root, learner, create=False)
    if service is None:
        return 2
    try:
        if not operation_id:
            items = service.list_operations(course)
            if as_json:
                print(json.dumps(items, ensure_ascii=False, indent=2))
                return 0
            if not items:
                print("операций по курсу нет")
                return 0
            print("== операции ==")
            for item in items:
                print("  %s  %-22s шаг %d/%s"
                      % (item["operation_id"][:8], item["status"],
                         item["next_step"], "?"))
            return 0

        operation = service.get_operation(operation_id)
        if operation is None:
            print("операция не найдена: %s" % operation_id)
            return 2
        plan = service.get_plan(operation_id)
        if as_json:
            print(json.dumps({"operation": operation, "plan": plan},
                             ensure_ascii=False, indent=2))
            return 0

        print("== операция %s ==" % operation_id)
        print("  состояние   : %s" % operation["status"])
        print("  план-хеш    : %s" % operation["plan_hash"][:32])
        print("  шагов       : %d, выполнено до %d"
              % (len(plan["steps"]) if plan else 0, operation["next_step"]))
        if operation.get("started_at"):
            print("  начата      : %s" % operation["started_at"])
        if operation.get("finished_at"):
            print("  завершена   : %s" % operation["finished_at"])
        if operation.get("cancellation_requested"):
            print("  запрошена отмена")
        for step in operation.get("results") or []:
            mark = "ok  " if step.get("ok") else "FAIL"
            print("  [%s] %-16s %s" % (mark, step["step_id"], step["kind"]))
            if step.get("error"):
                print("        %s" % step["error"][:200])
        return 0
    finally:
        service.close()


def cmd_operation_cancel(root, operation_id, learner, reason, reconcile):
    service = _open_operations(root, learner, create=False)
    if service is None:
        return 2
    if not operation_id:
        print("usage: cli.py operation-cancel --operation <id> [--reconcile] "
              "[--reason ...]")
        return 2
    try:
        if reconcile:
            report = service.reconcile(operation_id)
            print("== восстановление операции %s ==" % operation_id)
            for key in ("status", "next_step", "steps_total", "steps_finished",
                        "failed_steps", "cancellation_requested"):
                print("  %-20s: %s" % (key, report.get(key)))
            print("  %s" % report["message_ru"])
            return 0

        result = service.cancel(operation_id, reason=reason)
        print(result["message_ru"])
        return 0
    except Exception as e:  # noqa: BLE001
        print("  отказ (%s): %s" % (getattr(e, "code", type(e).__name__), e))
        return 3
    finally:
        service.close()


def parse_confirm_split(values):
    """`--confirm-split stu-01=<id>` -> {"stu-01": "<id>"}."""
    mapping = {}
    for item in values or []:
        if "=" not in item:
            sys.exit("--confirm-split ожидает вид stu-01=<id-обучающегося>, получено: %r" % item)
        legacy_id, _, learner_id = item.partition("=")
        legacy_id, learner_id = legacy_id.strip(), learner_id.strip()
        if not legacy_id or not learner_id:
            sys.exit("--confirm-split: пустое значение в %r" % item)
        mapping[legacy_id] = learner_id
    return mapping


def default_learner_id(root):
    """The workspace's learner id, created once and then stable.

    A local workspace serves one learner; the id exists so records can be
    exported, merged or deleted without relying on a person's name.
    """
    import json as _json
    import uuid as _uuid

    marker = root / ".botai" / "workspace.json"
    if marker.is_file():
        try:
            data = _json.loads(marker.read_text(encoding="utf-8"))
            if data.get("learner_id"):
                return data["learner_id"]
        except (OSError, ValueError):
            pass
    learner_id = str(_uuid.uuid4())
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_json.dumps({"schema_version": 2, "learner_id": learner_id},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return learner_id


def main():
    ap = argparse.ArgumentParser(description="botai workspace CLI (cross-platform)")
    ap.add_argument("command", choices=["setup", "new-course", "progress", "review",
                                        "courses", "course-set", "active", "corpus",
                                        "update", "course-add", "course-update",
                                        "course-inspect", "course-accept", "course-status",
                                        "policy-check",
                                        "session-start", "session-next", "session-goal",
                                        "session-attempt", "session-check", "session-pause",
                                        "consent-set", "consent-withdraw",
                                        "corpus-status", "corpus-acquire",
                                        "source-search", "quote-verify",
                                        "env-plan", "action-approve", "env-apply",
                                        "env-status", "operation-cancel",
                                        "state-migrate",
                                        "doctor", "clean"])
    ap.add_argument("--name", help="course slug for new-course / course-add")
    ap.add_argument("--title", help="course title for new-course")
    ap.add_argument("--course", help="course slug for progress/review/course-set/corpus/course-update")
    ap.add_argument("--url", help="git URL of the course repository (course-add)")
    ap.add_argument("--ref", help="branch/tag/commit (update/course-add/course-update)")
    ap.add_argument("--source", help="upstream harness repository (update)")
    ap.add_argument("--mode", choices=["auto", "git", "archive"], default="auto",
                    help="update: how to update the harness (auto by default)")
    ap.add_argument("--check", action="store_true",
                    help="update/course-update: report only (exit code 10 = update available)")
    ap.add_argument("--overwrite", action="store_true",
                    help="update: also replace locally edited harness files (backed up first)")
    ap.add_argument("--take-upstream", action="store_true",
                    help="course-update: also replace locally edited course files (backed up first)")
    ap.add_argument("--commit-and-update", dest="commit", action="store_true",
                    help="course-update: commit the course's pending work first, then update it")
    ap.add_argument("--prune", action="store_true",
                    help="update: remove files the harness no longer ships (backed up first)")
    ap.add_argument("--root", help="project root (default: cwd or BOTAI_ROOT)")
    ap.add_argument("--force", action="store_true",
                    help="corpus: refetch even when installed; course-add: replace the directory")
    ap.add_argument("--dry-run", action="store_true", help="preview, change nothing")
    ap.add_argument("--apply", action="store_true",
                    help="state-migrate: actually import (without it the command only reports)")
    ap.add_argument("--learner", help="state-migrate: learner id the record belongs to")
    ap.add_argument("--confirm-split", action="append", default=[],
                    metavar="LEGACY_ID=LEARNER_ID",
                    help="state-migrate: explicit attribution for a multi-student file")
    ap.add_argument("--assignment",
                    help="policy-check / session-attempt: assignment id from the accepted contract")
    ap.add_argument("--level", choices=["HINT", "EXAMPLE", "SOLUTION"], default="HINT",
                    help="policy-check / session: level of help being considered")
    ap.add_argument("--preference", default="prefer-ask",
                    choices=["hints", "hints-then-solution", "solution-first", "prefer-ask"],
                    help="policy-check: the learner's recorded feedback preference")
    ap.add_argument("--kind", choices=["maintainer_manifest", "human_adapted_legacy"],
                    help="course-accept: how acceptance came about")
    ap.add_argument("--teaching-remote", help="course-accept: remote course updates come from")
    ap.add_argument("--teaching-branch", help="course-accept: branch course updates come from")
    ap.add_argument("--contribution-remote",
                    help="course-accept: the student's fork, when there is one")
    ap.add_argument("--session", help="session id for the session-* commands")
    ap.add_argument("--objective", help="objective id (session-goal/session-attempt)")
    ap.add_argument("--objectives", action="append", default=[],
                    metavar="OBJECTIVE_ID", help="session-start: objective for this sitting")
    ap.add_argument("--minutes", type=int, default=30,
                    help="session-start: agreed length of the sitting (5-120)")
    ap.add_argument("--modes", action="append", default=[],
                    help="session-start: tutoring/co-learning/supplement/contributor")
    ap.add_argument("--confirm", action="store_true",
                    help="session-goal: start work on the chosen objective")
    ap.add_argument("--attempt", help="session-check: attempt id being checked")
    # Named `--check-kind` rather than `--kind`: `--kind` is already the
    # acceptance kind for `course-accept`, and two meanings for one flag is how
    # a CLI starts silently doing the wrong thing.
    ap.add_argument("--check-kind",
                    choices=["explain", "predict", "apply", "transfer", "critique",
                             "diagnostic"],
                    help="session-check: kind of check being recorded")
    ap.add_argument("--attempt-file", help="session-attempt: read the attempt text from a file")
    ap.add_argument("--criterion", action="append", default=[],
                    metavar="ID=RESULT[:EVIDENCE,...]",
                    help="session-check: criterion result (pass needs evidence)")
    ap.add_argument("--verdict", choices=["pass", "partial", "fail", "uncertain"],
                    help="session-check: overall verdict")
    ap.add_argument("--target", choices=["PAUSED", "BLOCKED", "COMPLETED", "CANCELLED"],
                    help="session-pause: where the session moves to")
    ap.add_argument("--reason", help="session-pause: why (required for BLOCKED)")
    ap.add_argument("--resume", help="session-pause: state to return to on resume")
    ap.add_argument("--purpose", action="append", default=[],
                    metavar="PURPOSE",
                    help="consent-set: learning_storage/model_processing/"
                         "teacher_export/publication")
    ap.add_argument("--provider", help="consent-set: which model provider sees the data")
    ap.add_argument("--retention", type=int, default=180,
                    help="consent-set: how many days learning data is kept")
    ap.add_argument("--consent", help="consent-withdraw: consent id to withdraw")
    ap.add_argument("--query", help="source-search: what to look for")
    ap.add_argument("--limit", type=int, default=5, help="source-search: max hits (1-8)")
    ap.add_argument("--citation", help="quote-verify: JSON file with the citation")
    ap.add_argument("--offline", action="store_true",
                    help="corpus-acquire: use only what is already local")
    ap.add_argument("--spec", help="env-plan: environment spec JSON file")
    ap.add_argument("--operation", help="operation id (env-apply/action-approve/env-status)")
    ap.add_argument("--deny", action="store_true",
                    help="action-approve: record a refusal instead of a grant")
    ap.add_argument("--reconcile", action="store_true",
                    help="operation-cancel: inspect state instead of cancelling")
    ap.add_argument("--wait", action="store_true",
                    help="env-apply: wait for completion (default in this CLI)")
    ap.add_argument("--json", action="store_true",
                    help="progress/corpus-status/source-search/quote-verify/env-status: emit JSON")
    args = ap.parse_args()

    if args.check and args.dry_run:
        ap.error("--check and --dry-run are mutually exclusive")

    root = resolve_root(args.root)
    cmd = args.command
    if cmd == "setup":
        cmd_setup(root, args.dry_run)
    elif cmd == "new-course":
        if not args.name:
            sys.exit("usage: cli.py new-course --name <slug> [--title <TITLE>]")
        cmd_new_course(root, args.name, args.title, args.dry_run)
    elif cmd == "progress":
        if not args.course:
            sys.exit("usage: cli.py progress --course <slug>")
        # The v2 store is checked first: if the workspace has recorded sessions,
        # the projection is the real answer, and the v1 Markdown is only a
        # fallback for a workspace that was never migrated.
        sys.exit(cmd_progress_v2(root, args.course, args.learner, args.json))
    elif cmd == "review":
        if not args.course:
            sys.exit("usage: cli.py review --course <slug>")
        cmd_review(root, args.course, args.dry_run)
    elif cmd == "courses":
        cmd_courses(root, args.dry_run)
    elif cmd == "course-set":
        if not args.course:
            sys.exit("usage: cli.py course-set --course <slug>")
        cmd_course_set(root, args.course, args.dry_run)
    elif cmd == "active":
        cmd_active(root, args.dry_run)
    elif cmd == "corpus":
        sys.exit(cmd_corpus(root, args.course, args.dry_run, args.force))
    elif cmd == "update":
        sys.exit(U.run(root=root, source=args.source, ref=args.ref, mode=args.mode,
                       check=args.check, dry_run=args.dry_run,
                       overwrite=args.overwrite, prune=args.prune))
    elif cmd == "course-add":
        if not args.url:
            sys.exit("usage: cli.py course-add --url <git-url> [--name <slug>] [--ref <ref>]")
        # --dry-run must reach fetch_course: a preview that still clones is not
        # a preview, and the previous wiring dropped the flag on the floor.
        if args.force:
            sys.exit(
                "course-add --force больше не удаляет существующий курс: в нём\n"
                "может лежать работа обучающегося. Обновите его\n"
                "  python scripts/cli.py course-update --course <slug>\n"
                "или получите рядом другую копию: --name <slug>-2"
            )
        sys.exit(C.fetch_course(root, args.url, args.name, args.ref,
                                force=False, dry=args.dry_run))
    elif cmd == "course-update":
        if not args.course:
            sys.exit("usage: cli.py course-update --course <slug> [--check|--dry-run]")
        sys.exit(C.update_course(root, args.course, check=args.check, dry=args.dry_run,
                                 take_upstream=args.take_upstream, ref=args.ref,
                                 commit=args.commit))
    elif cmd == "state-migrate":
        sys.exit(cmd_state_migrate(root, args.course, args.dry_run, args.learner,
                                   parse_confirm_split(args.confirm_split), apply=args.apply))
    elif cmd == "course-inspect":
        sys.exit(cmd_course_inspect(root, args.course, args.dry_run))
    elif cmd == "course-accept":
        sys.exit(cmd_course_accept(root, args.course, args.dry_run, kind=args.kind,
                                   url=args.url, teaching_remote=args.teaching_remote,
                                   teaching_branch=args.teaching_branch,
                                   contribution_remote=args.contribution_remote))
    elif cmd == "course-status":
        sys.exit(cmd_course_status(root, args.course, args.dry_run))
    elif cmd == "policy-check":
        sys.exit(cmd_policy_check(root, args.course, args.assignment, args.level,
                                  args.preference))
    elif cmd == "session-start":
        sys.exit(cmd_session_start(root, args.course, args.learner, args.objectives,
                                   args.minutes, args.modes or ["tutoring"],
                                   args.dry_run))
    elif cmd == "session-next":
        if not args.session:
            sys.exit("usage: cli.py session-next --session <id> --course <slug>")
        sys.exit(cmd_session_next(root, args.course, args.session, args.learner))
    elif cmd == "session-goal":
        if not (args.session and args.objective):
            sys.exit("usage: cli.py session-goal --session <id> --objective <id> "
                     "[--assignment <id>] [--confirm]")
        sys.exit(cmd_session_goal(root, args.course, args.session, args.objective,
                                  args.assignment, args.learner, args.confirm))
    elif cmd == "session-attempt":
        if not args.session:
            sys.exit("usage: cli.py session-attempt --session <id> [--attempt-file <path>]")
        sys.exit(cmd_session_attempt(root, args.course, args.session, args.objective,
                                     args.assignment, args.learner, args.attempt_file))
    elif cmd == "session-check":
        if not (args.session and args.attempt and args.verdict and args.check_kind):
            sys.exit("usage: cli.py session-check --session <id> --attempt <id> "
                     "--check-kind explain|predict|apply|transfer|critique "
                     "--verdict pass|partial|fail|uncertain "
                     "--criterion <criterion_id>=<result>[:<evidence>]")
        sys.exit(cmd_session_check(root, args.course, args.session, args.attempt,
                                   args.check_kind, args.verdict, args.criterion,
                                   args.learner))
    elif cmd == "session-pause":
        if not args.session:
            sys.exit("usage: cli.py session-pause --session <id> "
                     "--target PAUSED|BLOCKED|COMPLETED|CANCELLED [--reason ...]")
        if args.target == "BLOCKED" and not args.reason:
            sys.exit("session-pause --target BLOCKED требует --reason: "
                     "блокировка без причины не сообщает, что устранять")
        sys.exit(cmd_session_pause(root, args.course, args.session, args.learner,
                                   args.target, args.reason, args.resume))
    elif cmd == "consent-set":
        sys.exit(cmd_consent_set(root, args.course, args.learner, args.purpose,
                                 args.provider, args.retention, args.dry_run))
    elif cmd == "consent-withdraw":
        sys.exit(cmd_consent_withdraw(root, args.course, args.learner, args.consent,
                                      args.dry_run))
    elif cmd == "corpus-status":
        sys.exit(cmd_corpus_status(root, args.course, args.json))
    elif cmd == "corpus-acquire":
        sys.exit(cmd_corpus_acquire(root, args.course, args.dry_run, args.offline))
    elif cmd == "source-search":
        sys.exit(cmd_source_search(root, args.course, args.query, args.limit, args.json))
    elif cmd == "quote-verify":
        sys.exit(cmd_quote_verify(root, args.course, args.citation, args.json))
    elif cmd == "env-plan":
        sys.exit(cmd_env_plan(root, args.course, args.spec, args.learner))
    elif cmd == "action-approve":
        sys.exit(cmd_action_approve(root, args.operation, args.learner,
                                    "denied" if args.deny else "granted",
                                    args.dry_run))
    elif cmd == "env-apply":
        sys.exit(cmd_env_apply(root, args.operation, args.learner, args.wait))
    elif cmd == "env-status":
        sys.exit(cmd_env_status(root, args.operation, args.course, args.learner,
                                args.json))
    elif cmd == "operation-cancel":
        sys.exit(cmd_operation_cancel(root, args.operation, args.learner,
                                      args.reason, args.reconcile))
    elif cmd == "doctor":
        cmd_doctor(root, args.dry_run)
    elif cmd == "clean":
        cmd_clean(root, args.dry_run)


if __name__ == "__main__":
    main()