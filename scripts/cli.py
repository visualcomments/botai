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
    ap.add_argument("--assignment", help="policy-check: assignment id from the accepted contract")
    ap.add_argument("--level", choices=["HINT", "EXAMPLE", "SOLUTION"], default="HINT",
                    help="policy-check: level of help being considered")
    ap.add_argument("--preference", default="prefer-ask",
                    choices=["hints", "hints-then-solution", "solution-first", "prefer-ask"],
                    help="policy-check: the learner's recorded feedback preference")
    ap.add_argument("--kind", choices=["maintainer_manifest", "human_adapted_legacy"],
                    help="course-accept: how acceptance came about")
    ap.add_argument("--teaching-remote", help="course-accept: remote course updates come from")
    ap.add_argument("--teaching-branch", help="course-accept: branch course updates come from")
    ap.add_argument("--contribution-remote",
                    help="course-accept: the student's fork, when there is one")
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
        cmd_progress(root, args.course, args.dry_run)
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
    elif cmd == "doctor":
        cmd_doctor(root, args.dry_run)
    elif cmd == "clean":
        cmd_clean(root, args.dry_run)


if __name__ == "__main__":
    main()