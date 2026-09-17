#!/usr/bin/env python3
"""botai installer.

Creates a NEW, self-contained project directory and installs the whole botai
harness into it: AGENTS.md, CLAUDE.md, Makefile, opencode.json, the agent files
(.opencode/agent/*.md), the skills (.agents/skills/), the per-framework symlink
farms (.claude/skills/, .cursor/skills/, .opencode/skills/), and docs.

Everything is scoped to the new project directory only. Nothing is written to
global configuration (opencode, Claude Code, Cursor, Codex, pi, ...): agent
files such as AGENTS.md and agent.md are never installed outside the project.
The installer refuses to target the repo root itself or any global config dir.

The install also records *what* was installed and *from where* in
`.botai/install.json` (source, ref, revision, per-file sha256). That record is
what makes `scripts/update.py` safe later: a file whose hash no longer matches
the record was edited locally, and an update keeps it instead of overwriting it.
Without the record an update could only guess.

Usage:
    python3 scripts/install.py [--dest PATH] [--no-git] [--dry-run]
    make install DEST=my-botai-project
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as H  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# The harness file list lives in scripts/harness.py: the updater overwrites
# exactly what the installer wrote, and one list is what keeps those two honest.
FILES = list(H.MANAGED_FILES)

COPY_DIRS = list(H.MANAGED_DIRS)

COPY_DIRS_IGNORING_SKILLS = list(H.MANAGED_DIRS_KEEP_SKILLS)

FARM_DIRS = list(H.FARM_DIRS)

RUNTIME_DIRS = ["courses", "progress", "dist"]

DIST_GITIGNORE_CONTENT = "*\n!.gitignore\n"

# Written into the new project BEFORE its initial `git add -A`, so the private
# runtime directory never enters the project's history. The harness's own
# .gitignore is copied too, but it describes the botai source repository, not
# an installed workspace.
PROJECT_GITIGNORE = """# Student-owned course material and progress. Anchored to the project root:
# without the leading slash these patterns would also ignore a same-named
# directory nested inside a course (for example a course that keeps its own
# `progress/` notes), which is course material, not workspace state.
/courses/
/progress/
/dist/

# Python caches.
__pycache__/
*.py[cod]

# Private runtime state: install record, backups of locally edited files,
# local databases and caches. Never published, never reused as course material.
/.botai/

# Local course environments (created by the environment profile).
/.venv/
/venv/
/.core-venv/

# Secrets and machine-local configuration.
.env
.env.*
!.env.example
"""


def ensure_project_gitignore(dest, dry_run=False):
    """Make sure `.botai/` and friends are ignored before anything is committed.

    Returns True when the file was (or would be) created or extended.
    """
    path = Path(dest) / ".gitignore"
    if path.exists():
        current = path.read_text(encoding="utf-8", errors="replace")
        missing = [line for line in PROJECT_GITIGNORE.splitlines()
                   if line and not line.startswith("#") and line not in current.splitlines()]
        if not missing and ".botai/" in current:
            return False
        if not dry_run:
            addition = "" if current.endswith("\n") else "\n"
            addition += "\n# Added by botai: private runtime state must never be committed.\n"
            addition += "\n".join(missing or ["/.botai/", "/dist/", "/.venv/", "/venv/", "/.core-venv/"]) + "\n"
            path.write_text(current + addition, encoding="utf-8", newline="\n")
        return True
    if not dry_run:
        path.write_text(PROJECT_GITIGNORE, encoding="utf-8", newline="\n")
    return True


def global_config_homes():
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", "")).expanduser()
        local = Path(os.environ.get("LOCALAPPDATA", "")).expanduser()
        candidates = [
            home / ".config" / "opencode",
            home / ".claude",
            home / ".cursor",
            home / ".agents",
            appdata / "opencode",
            appdata / "claude",
            appdata / "cursor",
            local / "opencode",
        ]
    else:
        candidates = [
            home / ".config" / "opencode",
            home / ".claude",
            home / ".cursor",
            home / ".agents",
        ]
    return [p for p in candidates if p]


def make_symlink_or_copy(target, link):
    return H.make_symlink_or_copy(target, link)


def corpus_manifests(course_dir):
    return [m for m in ("index-manifest.json", "corpus-manifest.json")
            if (course_dir / m).exists()]


def fetch_corpora(dest):
    """Acquire the corpus of every course subproject that publishes one.

    Best-effort by design: a failed download must not abort an install that has
    already produced a usable workspace. Failures are reported loudly and the
    documented one-liner to retry is printed, because a course taught without
    its corpus is taught without evidence.
    """
    courses = dest / "courses"
    if not courses.is_dir():
        return
    todos = [d for d in sorted(courses.iterdir()) if d.is_dir() and corpus_manifests(d)]
    if not todos:
        return

    print()
    print("corpus acquisition (courses that publish a corpus):")
    failures = []
    for cdir in todos:
        fetcher = cdir / "tools" / "corpus_fetch.py"
        if not fetcher.exists():
            fetcher = cdir / "tools" / "index_fetch.py" if (cdir / "tools" / "index_fetch.py").exists() else None
        print("  %s: %s" % (cdir.name, ", ".join(corpus_manifests(cdir))))
        if not fetcher:
            print("    no tools/corpus_fetch.py - fetch manually per its CORPUS.md")
            failures.append((cdir.name, "no fetcher"))
            continue
        rc = subprocess.run([sys.executable or "python3", str(fetcher)], cwd=str(cdir)).returncode
        if rc != 0:
            failures.append((cdir.name, "exit %d" % rc))
            print("    FAILED (exit %d)" % rc)
        else:
            print("    ok")

    if failures:
        print()
        print("WARNING: corpus not installed for: %s"
              % ", ".join("%s (%s)" % f for f in failures))
        print("  a course without its corpus cannot verify quotations - retry with:")
        for name, _ in failures:
            print("    python scripts/cli.py corpus --course %s" % name)
        print("  if the link is dead, report it: do not substitute a URL.")
    else:
        print("  all published corpora installed.")


def install(src, dest, init_git, dry_run):
    dest = Path(dest).expanduser().resolve()
    if dest == src:
        raise SystemExit("refusing to install into the repo root itself; pass --dest")
    for home in global_config_homes():
        if dest == home or home in dest.parents:
            raise SystemExit("refusing to install into global config: %s" % home)

    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("destination already exists and is not empty: %s" % dest)
    if not dest.exists() and not dry_run:
        dest.mkdir(parents=True)

    for name in FILES:
        print("  install %s" % name)
        if not dry_run:
            shutil.copy2(str(src / name), str(dest / name))

    for name in COPY_DIRS:
        print("  install %s/" % name)
        if not dry_run:
            shutil.copytree(str(src / name), str(dest / name))

    for name in COPY_DIRS_IGNORING_SKILLS:
        print("  install %s/ (skills farm re-linked)" % name)
        if not dry_run:
            shutil.copytree(
                str(src / name),
                str(dest / name),
                ignore=shutil.ignore_patterns("skills"),
            )

    skills = dest / ".agents" / "skills"
    for farm in FARM_DIRS:
        print("  link farm %s/" % farm)
    if not dry_run:
        H.relink_skill_farms(dest, dry=False, log=lambda _m: None)

    for runtime in RUNTIME_DIRS:
        print("  workspace %s/" % runtime)
        if not dry_run:
            (dest / runtime).mkdir(exist_ok=True)
    if not dry_run:
        (dest / "dist" / ".gitignore").write_text(DIST_GITIGNORE_CONTENT, encoding="utf-8")

    # Before any `git add -A`: the private runtime directory must be ignored from
    # the very first commit, otherwise the install record and backups of the
    # student's edited files become published history.
    if ensure_project_gitignore(dest, dry_run=dry_run):
        print("  .gitignore: .botai/ and local environments excluded from git")

    # Where the harness came from, and what it looked like: the baseline every
    # later update compares against to tell "upstream changed this" apart from
    # "the student changed this".
    if not dry_run:
        H.save_record(dest, {
            # Текущая схема записи (см. scripts/update.py): её понимает
            # обновлятор, и по ней же он видит, что мигрировать нечего.
            "schema": H.RECORD_SCHEMA,
            "version": H.read_version(src),
            "source": H.DEFAULT_SOURCE,
            "ref": H.DEFAULT_REF,
            "mode": "install",
            "revision": H.git_head(src, short=False),
            "installed_at": H.now_iso(),
            "files": H.fingerprint(dest),
        })
        print("  install record .botai/install.json (%s)" % (H.read_version(src) or "no VERSION"))

    # A course that publishes a corpus is not ready to teach until the corpus is
    # installed, so acquisition is part of deployment - not a later manual step.
    # Only courses that actually ship corpus manifests are fetched; an empty or
    # corpus-less workspace is a normal, fully valid outcome.
    if not dry_run:
        fetch_corpora(dest)

    if init_git and not dry_run:
        subprocess.run(["git", "init"], cwd=str(dest), check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(dest), check=False)
        commit = subprocess.run(
            ["git", "commit", "-m", "botai initial install"],
            cwd=str(dest),
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            # No git identity configured is the common case on a fresh machine.
            # This is the INSTALLER's own bookkeeping commit, not the student's
            # work, so a clearly-labelled service identity is acceptable here —
            # unlike course commits, which must always be the student's own.
            commit = subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=botai",
                    "-c",
                    "user.email=botai@localhost",
                    "commit",
                    "-m",
                    "botai initial install",
                ],
                cwd=str(dest),
                capture_output=True,
                text=True,
            )
        if commit.returncode == 0:
            print("  git initial commit created")
        else:
            # Reporting success after a failed commit is how a workspace ends up
            # with no baseline while its owner believes there is one.
            reason = (commit.stderr or commit.stdout or "").strip().splitlines()
            print("  ВНИМАНИЕ: git initial commit не создан (%s)"
                  % (reason[-1] if reason else "неизвестная причина"))
            print("  файлы на месте; закоммитьте вручную после настройки git identity")

    print()
    print("botai installed into its own project: %s" % dest)
    print("  agent files (AGENTS.md, agent.md, skills, commands) are scoped to this project")
    print("  nothing was written to global config (opencode / Claude Code / Cursor / ...)")
    print("next:")
    print("  cd %s" % dest)
    print("  make help            # or, without make (any OS incl. Windows):")
    print("                       #   python scripts/cli.py doctor")
    print("  make setup           # (already done by install) create courses/ progress/ dist/")
    print("                       # or: python scripts/cli.py setup")
    print("  make course-add COURSE_URL=<git-url>   # bring in a course and keep it current")
    print("  make update-check                      # is a newer botai published?")


def main():
    parser = argparse.ArgumentParser(
        description="Install botai into a NEW, project-scoped directory"
    )
    parser.add_argument(
        "--dest",
        default="botai-project",
        help="destination project directory (default: botai-project)",
    )
    parser.add_argument(
        "--git",
        dest="init_git",
        action="store_true",
        default=True,
        help="git init the new project (default)",
    )
    parser.add_argument(
        "--no-git",
        dest="init_git",
        action="store_false",
        help="do not git init the new project",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="preview what would be installed"
    )
    args = parser.parse_args()

    if args.dry_run:
        print("dry run: nothing will be written")
    install(REPO_ROOT, args.dest, args.init_git, args.dry_run)


if __name__ == "__main__":
    main()
