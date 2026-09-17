#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for path safety, preview honesty, and Git-root detection.

These cover the boundary conditions that let a study workspace lose work or
write outside itself:

* a course slug is a NAME, not a path — `..`, separators, drive letters, NUL and
  Windows device names are refused, and a symlinked directory inside `courses/`
  that points outside the workspace is refused rather than followed;
* `--dry-run` writes nothing, including for commands that clone;
* a course scaffolded inside a versioned workspace is not mistaken for a
  repository of its own, so course bookkeeping never operates on the workspace
  repository;
* re-installing a course never deletes an existing directory that may hold the
  student's work.

Run:
    python3 tests/test_paths.py
    python3 -m pytest tests/test_paths.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows consoles default to a legacy code page (cp1252/cp866), so printing
# Russian test names raises UnicodeEncodeError and the suite dies before
# checking anything. The output is a diagnostic, so reconfiguring it is the fix;
# the alternative — ASCII-only test names — would make failures harder to read
# for the audience this project is written for.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harness as H  # noqa: E402
import pathsafe as P  # noqa: E402

_passed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def run_cli(ws: Path, *args):
    """Run the workspace CLI against `ws`, returning (rc, combined output).

    `encoding="utf-8"` is explicit because the child now prints UTF-8 Russian
    diagnostics while the parent's default text decoding on Windows is cp1252:
    the reader would raise UnicodeDecodeError on output the child wrote
    correctly, turning a passing command into a test error.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "cli.py"), *args, "--root", str(ws)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def workspace(base: Path, name="ws") -> Path:
    ws = base / name
    (ws / "courses").mkdir(parents=True)
    (ws / "progress").mkdir(parents=True)
    return ws


# ---------------------------------------------------------------------------
# Slugs are names, not paths
# ---------------------------------------------------------------------------
def test_slug_validation_rejects_paths():
    for bad, code in [
        ("../etc", "SLUG_SEPARATOR"),
        ("a/b", "SLUG_SEPARATOR"),
        ("a\\b", "SLUG_SEPARATOR"),
        ("/absolute", "SLUG_SEPARATOR"),
        ("C:win", "SLUG_SEPARATOR"),
        ("..", "SLUG_TRAVERSAL"),
        (".", "SLUG_TRAVERSAL"),
        ("con", "SLUG_RESERVED"),
        ("lpt1", "SLUG_RESERVED"),
        ("trailing.", "SLUG_TRAILING"),
        (" space", "SLUG_WHITESPACE"),
        ("a\x00b", "SLUG_NUL"),
        ("a\nb", "SLUG_CONTROL"),
    ]:
        try:
            P.validate_slug(bad)
            check(f"slug отвергает {bad!r}", False, "принят")
        except P.PathError as e:
            check(f"slug отвергает {bad!r}", e.code == code, f"код {e.code}, ожидался {code}")

    for good in ["my-course", "course_1", "a.b", "x9"]:
        check(f"slug принимает {good!r}", P.validate_slug(good) == good)


def test_legacy_names_survive_but_cannot_escape():
    # An existing installation may hold a Cyrillic course directory; it must
    # keep working without the traversal rules being relaxed.
    check("legacy-имя принимается",
          P.validate_slug("История-науки", allow_legacy=True) == "История-науки")
    for bad in ["../x", "a/b", ".."]:
        try:
            P.validate_slug(bad, allow_legacy=True)
            check(f"legacy-режим всё равно отвергает {bad!r}", False, "принят")
        except P.PathError:
            check(f"legacy-режим всё равно отвергает {bad!r}", True)


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------
def test_symlink_escape_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = workspace(base)
        outside = base / "outside"
        outside.mkdir()
        link = ws / "courses" / "evil"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            print("  skip симлинки недоступны на этой платформе")
            return

        rc, out = run_cli(ws, "new-course", "--name", "evil")
        check("симлинк за пределы workspace: отказ", rc != 0, out[-200:])
        check("симлинк за пределы workspace: ничего не записано",
              not any(outside.iterdir()))
        check("отказ назван кодом PATH_OUTSIDE_SCOPE", "PATH_OUTSIDE_SCOPE" in out, out[-200:])


def test_traversal_is_refused_at_every_entry_point():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = workspace(base)
        sentinel = base / "outside"
        sentinel.mkdir()

        for args in (("new-course", "--name", "../outside"),
                     ("progress", "--course", "../outside"),
                     ("review", "--course", "../../etc"),
                     ("course-set", "--course", "../outside"),
                     ("corpus", "--course", "../outside")):
            rc, out = run_cli(ws, *args)
            check(f"traversal отклонён: {' '.join(args[:2])}",
                  rc != 0 and "отклон" in out or rc != 0, out[-160:])

        # The scaffold of a normal name still lands inside courses/.
        rc, out = run_cli(ws, "new-course", "--name", "fine")
        check("обычное имя по-прежнему создаётся",
              rc == 0 and (ws / "courses" / "fine" / "syllabus.md").is_file(), out[-200:])


def test_drive_letter_is_refused_not_renamed():
    """`C:win` names a volume, so it is refused rather than made into `c-win`.

    Silently renaming it would create a differently-named course than the one
    the caller asked for, and would hide that the name addressed another drive.
    A harmless title with a colon (e.g. `Python: основы`) is a different case
    and is normalised, so the two must be told apart rather than both rejected.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ws = workspace(Path(tmp))
        for name in ("C:win", "c:win", "Z:temp"):
            rc, out = run_cli(ws, "new-course", "--name", name)
            check(f"имя диска {name!r} отвергается", rc != 0, out[-160:])
            check(f"каталог {name!r} не создан",
                  not any(p.name.lower().startswith(name[0].lower() + "-")
                          for p in (ws / "courses").iterdir()))

        rc, out = run_cli(ws, "new-course", "--name", "Мой курс")
        check("человеческое название нормализуется в slug",
              rc == 0 and (ws / "courses" / "мой-курс" / "syllabus.md").is_file(),
              out[-200:])


def test_containment_helper_own_checks():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "root"
        root.mkdir()
        inside = root / "child"
        inside.mkdir()

        check("внутренний путь разрешён",
              P.ensure_within(root, inside) == inside.resolve())
        check("несуществующий внутренний путь разрешён",
              str(P.ensure_within(root, root / "new" / "deep")).startswith(str(root.resolve())))
        try:
            P.ensure_within(root, base / "root2")
            check("соседний каталог отвергнут", False, "принят")
        except P.PathError as e:
            check("соседний каталог отвергнут", e.code == "PATH_OUTSIDE_SCOPE")
        try:
            P.ensure_within(root, "../escape")
            check("выход через .. отвергнут", False, "принят")
        except P.PathError as e:
            check("выход через .. отвергнут", e.code == "PATH_OUTSIDE_SCOPE")


# ---------------------------------------------------------------------------
# Preview honesty
# ---------------------------------------------------------------------------
def test_course_add_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = workspace(base)

        rc, out = run_cli(ws, "course-add", "--url", "https://example.invalid/c.git",
                          "--name", "dry", "--dry-run")
        check("course-add --dry-run завершается успешно", rc == 0, out[-200:])
        check("course-add --dry-run называет себя предпросмотром",
              "предпросмотр" in out, out[-200:])
        check("course-add --dry-run не создаёт каталог курса",
              not (ws / "courses" / "dry").exists())
        check("course-add --dry-run не создаёт запись о курсе",
              not (ws / "courses" / "dry" / H.COURSE_RECORD).exists())


def test_course_add_refuses_destructive_reinstall():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = workspace(base)
        existing = ws / "courses" / "kept"
        existing.mkdir(parents=True)
        work = existing / "assignments"
        work.mkdir()
        (work / "answer.md").write_text("работа студента\n", encoding="utf-8")

        rc, out = run_cli(ws, "course-add", "--url", "https://example.invalid/c.git",
                          "--name", "kept", "--force")
        check("course-add --force не удаляет каталог", rc != 0, out[-200:])
        check("работа студента цела",
              (work / "answer.md").read_text(encoding="utf-8") == "работа студента\n")
        check("отказ объясняет безопасный путь", "не удаляет" in out, out[-300:])


# ---------------------------------------------------------------------------
# Git root ownership
# ---------------------------------------------------------------------------
def _git_ready(path, env):
    """True when `git` can operate in `path` under this environment.

    Windows refuses repositories in temp directories owned by a different
    principal unless `safe.directory` allows them, and a mapped drive or an
    unusual mount can fail the same way. That is a property of the machine, not
    of the code under test: the caller skips rather than reporting a false
    defect. On Linux and macOS this is always true.
    """
    probe = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=path,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return probe.returncode == 0


def test_course_inside_workspace_repo_is_not_its_own_repo():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = workspace(base)
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@l",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@l")
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True, env=env)
        if not _git_ready(ws, env):
            print("  skip git недоступен для этого каталога на этой платформе")
            return
        (ws / "README.md").write_text("ws\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=ws, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True, env=env)

        rc, _ = run_cli(ws, "new-course", "--name", "inner")
        inner = ws / "courses" / "inner"

        check("workspace распознаётся как собственный репозиторий",
              H.is_git_worktree(ws))
        check("курс внутри workspace НЕ считается своим репозиторием",
              not H.is_git_worktree(inner),
              "иначе bookkeeping курса работал бы с репозиторием workspace")
        check("курс не получил чужой remote",
              not (inner / ".git").exists())


def test_real_course_repo_is_detected():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ws = workspace(base)
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@l",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@l")
        course = ws / "courses" / "own"
        course.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=course, check=True, env=env)
        if not _git_ready(course, env):
            print("  skip git недоступен для этого каталога на этой платформе")
            return
        check("собственный репозиторий курса распознаётся",
              H.is_git_worktree(course))
        check("workspace без git не считается репозиторием",
              not H.is_git_worktree(ws))


# ---------------------------------------------------------------------------
# Private runtime state never enters git
# ---------------------------------------------------------------------------
def test_install_ignores_private_runtime_state():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "project"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        p = subprocess.run([sys.executable, str(SCRIPTS / "install.py"), "--dest", str(dest)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, env=env, cwd=ROOT)
        check("установка проходит", p.returncode == 0,
              (p.stdout[-300:] + p.stderr[-300:]))

        gitignore = (dest / ".gitignore").read_text(encoding="utf-8")
        check(".botai/ исключён из git до первого коммита", ".botai/" in gitignore)
        check("courses/ и progress/ исключены",
              "courses/" in gitignore and "progress/" in gitignore)

        # The rule that matters is `.gitignore`, not whether this particular
        # machine could complete the installer's convenience commit: a
        # repository with no git identity still has a correct ignore file, and
        # the next `git add` honours it. So the ignore file is authoritative and
        # the index check is additional evidence when git is usable here.
        if not _git_ready(dest, env):
            print("  skip git недоступен в этом каталоге; .gitignore проверен по содержимому")
            check("запись установки существует", (dest / ".botai" / "install.json").is_file())
            return

        listed = subprocess.run(["git", "ls-files"], cwd=dest, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", env=env)
        if listed.returncode != 0:
            check("индекс читается для проверки", False,
                  (listed.stderr or listed.stdout or "")[-200:])
        else:
            tracked = listed.stdout.splitlines()
            leaked = [f for f in tracked
                      if f.startswith((".botai/", "courses/", "progress/", "dist/"))]
            check("приватные каталоги не попали в индекс", not leaked, str(leaked[:5]))
            # `git check-ignore` is the direct question: is this path ignored?
            for probe in (".botai/install.json", "courses/x/y.md"):
                res = subprocess.run(["git", "check-ignore", "-q", probe],
                                     cwd=dest, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
                check(f"git считает {probe} игнорируемым", res.returncode == 0,
                      "check-ignore вернул %d" % res.returncode)

        # The install record must exist on disk but stay untracked.
        check("запись установки существует", (dest / ".botai" / "install.json").is_file())


def main() -> int:
    print("[paths: slug — имя, а не путь]")
    test_slug_validation_rejects_paths()
    test_legacy_names_survive_but_cannot_escape()
    print("[paths: удержание внутри workspace]")
    test_symlink_escape_is_refused()
    test_traversal_is_refused_at_every_entry_point()
    test_drive_letter_is_refused_not_renamed()
    test_containment_helper_own_checks()
    print("[cli: предпросмотр ничего не пишет]")
    test_course_add_dry_run_writes_nothing()
    test_course_add_refuses_destructive_reinstall()
    print("[git: собственный корень рабочего дерева]")
    test_course_inside_workspace_repo_is_not_its_own_repo()
    test_real_course_repo_is_detected()
    print("[install: приватное состояние вне git]")
    test_install_ignores_private_runtime_state()

    print(f"\n{_passed} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


def test_paths() -> None:
    """Pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
