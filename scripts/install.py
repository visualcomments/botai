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

REPO_ROOT = Path(__file__).resolve().parent.parent

FILES = [
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "TUTORIAL.md",
    "opencode.json",
]

COPY_DIRS = [".agents", "docs", "scripts"]

COPY_DIRS_IGNORING_SKILLS = [".opencode"]

FARM_DIRS = [".claude/skills", ".cursor/skills", ".opencode/skills"]

RUNTIME_DIRS = ["courses", "progress", "dist"]

DIST_GITIGNORE_CONTENT = "*\n!.gitignore\n"


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
    if link.exists() or link.is_symlink():
        return True
    rel = os.path.relpath(str(target), start=str(link.parent))
    try:
        os.symlink(rel, str(link), target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return True
        except Exception:
            pass
    shutil.copytree(str(target), str(link))
    return False


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
        if dry_run:
            continue
        for skill in sorted(skills.iterdir()):
            if not skill.is_dir():
                continue
            link = dest / farm / skill.name
            link.parent.mkdir(parents=True, exist_ok=True)
            if not make_symlink_or_copy(skill, link):
                print("    warning: symlink failed for %s; copied instead" % link)

    for runtime in RUNTIME_DIRS:
        print("  workspace %s/" % runtime)
        if not dry_run:
            (dest / runtime).mkdir(exist_ok=True)
    if not dry_run:
        (dest / "dist" / ".gitignore").write_text(DIST_GITIGNORE_CONTENT, encoding="utf-8")

    if init_git and not dry_run:
        subprocess.run(["git", "init"], cwd=str(dest), check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(dest), check=False)
        commit = subprocess.run(
            ["git", "commit", "-m", "botai initial install"],
            cwd=str(dest),
            capture_output=True,
        )
        if commit.returncode != 0:
            subprocess.run(
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
                check=False,
            )
        print("  git initial commit created")

    print()
    print("botai installed into its own project: %s" % dest)
    print("  agent files (AGENTS.md, agent.md, skills, commands) are scoped to this project")
    print("  nothing was written to global config (opencode / Claude Code / Cursor / ...)")
    print("next:")
    print("  cd %s" % dest)
    print("  make help")
    print("  make setup   # (already done by install) create courses/ progress/ dist/")


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
