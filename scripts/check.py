#!/usr/bin/env python3
"""botai integrity checker.

Validates the harness before teaching:

1. Skill frontmatter - every `.agents/skills/<name>/SKILL.md` has a `name`,
   `description`, and a current, well-formed `verified: YYYY-MM-DD`.
2. Symlink-farm parity - `.claude/skills/`, `.cursor/skills/`, and
   `.opencode/skills/` each contain a git-tracked symlink (mode 120000) for
   every skill in `.agents/skills/`, pointing back to `../../.agents/skills/`.
   Checked via the git index so it works on Windows where junctions are not
   reported as symlinks by os.path.
3. Path safety - no SKILL.md references a file outside its own skill directory
   (`../` or an absolute path in a code-span/backtick reference).
4. Teaching-methods store - every method file under `docs/teaching-methods/`
   carries the required YAML frontmatter.

Run:  python3 scripts/check.py      (exit 0 = OK, 1 = problems found)
      make check
"""

import datetime
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / ".agents" / "skills"
FARMS = [".claude/skills", ".cursor/skills", ".opencode/skills"]
METHODS = REPO / "docs" / "teaching-methods"

REQUIRED_SKILL_FIELDS = ["name", "description", "verified"]
METHOD_FIELDS = ["method", "category", "owned_by", "applies_when", "verified"]

errors = []


def err(msg):
    errors.append(msg)


def read_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    body = text[3:end]
    fm = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, text


def valid_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def git_symlink_targets():
    out = subprocess.run(
        ["git", "ls-files", "-s"], cwd=str(REPO), capture_output=True, text=True
    )
    if out.returncode != 0:
        err("git ls-files failed; is this a git repo?")
        return {}
    targets = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "120000":
            mode, sha, stage, path = parts[0], parts[1], parts[2], parts[3]
            blob = subprocess.run(
                ["git", "cat-file", "blob", sha],
                cwd=str(REPO),
                capture_output=True,
                text=True,
            )
            targets[path] = blob.stdout.strip() if blob.returncode == 0 else ""
    return targets


def path_safety_violation(line):
    if "../" in line:
        return "../"
    if line.startswith("/") and not line.startswith("//"):
        return "root-absolute path"
    if re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", line):
        return "drive path"
    return None


def check_skills():
    if not SKILLS.is_dir():
        err("missing .agents/skills/")
        return set()
    skill_names = set()
    for skill in sorted(SKILLS.iterdir()):
        if not skill.is_dir():
            continue
        skill_names.add(skill.name)
        sk = skill / "SKILL.md"
        if not sk.exists():
            err("%s: missing SKILL.md" % skill.name)
            continue
        fm, text = read_frontmatter(sk)
        if fm is None:
            err("%s: SKILL.md missing YAML frontmatter" % skill.name)
            continue
        for field in REQUIRED_SKILL_FIELDS:
            if field not in fm:
                err("%s: SKILL.md missing frontmatter field '%s'" % (skill.name, field))
        if fm.get("name") != skill.name:
            err("%s: frontmatter name %r != directory name" % (skill.name, fm.get("name")))
        if "verified" in fm and not valid_date(fm["verified"]):
            err("%s: verified %r is not a YYYY-MM-DD date" % (skill.name, fm["verified"]))
        for line_no, line in enumerate(text.splitlines(), 1):
            violation = path_safety_violation(line)
            if violation:
                err("%s: SKILL.md path-safety violation (line %d): %s"
                    % (skill.name, line_no, violation))
    return skill_names


def check_farms(skill_names):
    symlinks = git_symlink_targets()
    for farm in FARMS:
        prefix = farm + "/"
        entries = {p[len(prefix):] for p in symlinks if p.startswith(prefix)}
        for name in sorted(skill_names):
            expected = "%s/%s" % (farm, name)
            if expected not in symlinks:
                err("%s: missing symlink for skill %s" % (farm, name))
                continue
            target = symlinks[expected]
            want = "../../.agents/skills/%s" % name
            if target != want:
                err("%s: symlink target %r != %r" % (expected, target, want))
        extra = entries - skill_names
        for name in sorted(extra):
            err("%s: symlink for unknown skill %s" % (farm, name))


def check_methods():
    if not METHODS.is_dir():
        return
    for mf in sorted(METHODS.rglob("*.md")):
        if mf.name == "README.md":
            continue
        fm, _ = read_frontmatter(mf)
        if fm is None:
            err("%s: missing YAML frontmatter" % mf.relative_to(REPO))
            continue
        for field in METHOD_FIELDS:
            if field not in fm:
                err("%s: missing frontmatter field '%s'" % (mf.relative_to(REPO), field))
        if "verified" in fm and not valid_date(fm["verified"]):
            err("%s: verified %r is not a YYYY-MM-DD date"
                % (mf.relative_to(REPO), fm["verified"]))


def main():
    skill_names = check_skills()
    check_farms(skill_names)
    check_methods()
    if errors:
        print("botai check: %d problem(s) found:" % len(errors))
        for e in errors:
            print("  - %s" % e)
        sys.exit(1)
    print("botai check: OK - skills, symlink farms, path safety, methods store")


if __name__ == "__main__":
    main()
