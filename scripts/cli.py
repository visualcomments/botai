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
    python scripts/cli.py doctor
    python scripts/cli.py clean

Paths are resolved against the current directory (the project root); override
with --root or the BOTAI_ROOT environment variable.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

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
    out = []
    for ch in name.strip().lower():
        out.append(ch if (ch.isalnum() or ch in "-_.") else "-")
    return "".join(out).strip("-") or "course"


def cmd_new_course(root, name, title, dry):
    slug = slugify(name)
    d = root / "courses" / slug
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
    f = root / "progress" / ("%s.md" % course)
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
    print("review workflow for %s:" % course)
    print("  - locate the student's submission under courses/%s/assignments/" % course)
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
    d = root / "courses" / course
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


def main():
    ap = argparse.ArgumentParser(description="botai workspace CLI (cross-platform)")
    ap.add_argument("command", choices=["setup", "new-course", "progress", "review",
                                        "courses", "course-set", "active", "doctor", "clean"])
    ap.add_argument("--name", help="course slug for new-course")
    ap.add_argument("--title", help="course title for new-course")
    ap.add_argument("--course", help="course slug for progress/review/course-set")
    ap.add_argument("--root", help="project root (default: cwd or BOTAI_ROOT)")
    ap.add_argument("--dry-run", action="store_true", help="preview, change nothing")
    args = ap.parse_args()

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
    elif cmd == "doctor":
        cmd_doctor(root, args.dry_run)
    elif cmd == "clean":
        cmd_clean(root, args.dry_run)


if __name__ == "__main__":
    main()