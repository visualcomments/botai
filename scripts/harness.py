#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the botai harness scripts (install / update / courses).

Everything that must agree between the installer, the updater and the course
manager lives here once:

* **which paths are the harness** (safe to overwrite) and **which belong to the
  student** (`courses/`, `progress/`, `.botai/`, `dist/` — never touched);
* how a project remembers what was installed (`.botai/install.json`);
* how a course remembers where it came from (`.botai-course.json`);
* how a tree is synchronised so that **a local edit is never silently lost**:
  every file that would be overwritten is copied into a backup directory first,
  and a locally edited file is kept by default;
* how the skill farms (`.claude/skills/`, `.cursor/skills/`, `.opencode/skills/`)
  are re-linked after an update.

One definition of these lists is what makes updating safe: the updater can only
overwrite paths that the installer is known to have written.

This module is copied into every installed project together with `scripts/`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = "https://github.com/visualcomments/botai.git"
DEFAULT_REF = "main"

VERSION_FILE = "VERSION"

# Files the installer copies. VERSION is included: an update must be able to
# tell which version the project is on. The lock files travel with the harness
# so an installed project can rebuild its core environment without network
# guesswork.
MANAGED_FILES = [
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "TUTORIAL.md",
    "VERSION",
    "opencode.json",
    "requirements-core.lock",
]

# Directories the installer copies wholesale. `tests` travels with the harness
# so that `make test` works in an installed project, not only in a checkout:
# a test suite that exists only upstream cannot be run where it matters.
#
# `schemas`, `examples` and `personas` are contracts, fixtures and shipped
# styles, not documentation: an installed project cannot validate a course, run
# a migration or offer `persona-set` without them. The v2 bridge carries its own
# hard-coded copy of this list, because an updater that reads the list from the
# tree it is installing has no boundary.
MANAGED_DIRS = [".agents", "docs", "examples", "personas", "schemas", "scripts", "tests"]

# Directories copied wholesale EXCEPT their `skills/` subdirectory, which is a
# symlink farm regenerated on install and on every update.
MANAGED_DIRS_KEEP_SKILLS = [".opencode"]

FARM_DIRS = [".claude/skills", ".cursor/skills", ".opencode/skills"]

# Student-owned runtime directories: never overwritten, never pruned.
STUDENT_DIRS = ["courses", "progress", "dist", ".botai"]

# Private runtime state: bookkeeping, backups, future databases, corpus and
# environment caches. It is never part of the harness, never committed, and
# must be excluded from git BEFORE the project's first `git add -A` — otherwise
# an operator's backups and edit records quietly become published history.
GITIGNORE_LINES = [
    "courses/",
    "progress/",
    "",
    "__pycache__/",
    "*.py[cod]",
    "",
    "# Private runtime state: never published, never reused as course material.",
    ".botai/",
    "dist/",
    "",
    "# Local course environments created by the v2 environment profile.",
    ".venv/",
    "venv/",
    "",
    "# Secrets and local configuration are never part of the harness.",
    ".env",
    ".env.*",
    "!.env.example",
]

# Версия формата записи об установке (.botai/install.json). Поднимается,
# когда меняется смысл полей — тогда обновлятор знает, что прежнюю запись
# нужно мигрировать, а не доверять ей как есть.
RECORD_SCHEMA = 2

STATE_DIR = ".botai"
INSTALL_RECORD = ".botai/install.json"
COURSE_RECORD = ".botai-course.json"

BACKUP_DIR = ".botai/backup"

IGNORED_DIR_NAMES = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def now_stamp():
    """A filesystem-safe UTC timestamp, used for backup directories."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_version(root):
    """Version of a harness checkout, or "" when it predates VERSION."""
    p = Path(root) / VERSION_FILE
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# What counts as "the harness"
# ---------------------------------------------------------------------------
def is_farm_path(rel):
    """True for the symlink farms: regenerated, never synced as content."""
    rel = rel.replace("\\", "/")
    return any(rel == farm or rel.startswith(farm + "/") for farm in FARM_DIRS)


def is_student_path(rel):
    rel = rel.replace("\\", "/")
    return any(rel == d or rel.startswith(d + "/") for d in STUDENT_DIRS)


def managed_files(root):
    """Relative POSIX paths of every harness-managed file present in `root`.

    Skill farms are skipped (they are symlinks), as are caches and VCS metadata.
    """
    root = Path(root)
    out = []

    for rel in MANAGED_FILES:
        if (root / rel).is_file() and not (root / rel).is_symlink():
            out.append(rel)

    keep_skills = {d.replace("\\", "/") for d in MANAGED_DIRS_KEEP_SKILLS}
    for rel_dir in list(MANAGED_DIRS) + list(MANAGED_DIRS_KEEP_SKILLS):
        base = root / rel_dir
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(base)):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in IGNORED_DIR_NAMES and not os.path.islink(os.path.join(dirpath, d))
            )
            rel_here = os.path.relpath(dirpath, str(root)).replace("\\", "/")
            # `.opencode/skills/` is a farm: regenerated by relink_skill_farms().
            if rel_dir in keep_skills and rel_here == rel_dir + "/skills":
                dirnames[:] = []
                continue
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue
                rel = os.path.normpath(os.path.join(rel_here, name)).replace("\\", "/")
                if is_farm_path(rel):
                    continue
                out.append(rel)

    return sorted(set(out))


def fingerprint(root, files=None):
    """Map of relative path -> sha256 for the harness files of a project.

    Scoped to the harness on purpose: this is what the updater compares against,
    and a hash of the student's own files must never be treated as something an
    update owns. For a whole tree use `fingerprint_tree()`.
    """
    root = Path(root)
    files = managed_files(root) if files is None else files
    fp = {}
    for rel in files:
        p = root / rel
        if p.is_file() and not p.is_symlink():
            fp[rel] = sha256_file(p)
    return fp


def fingerprint_tree(root, skip_prefixes=()):
    """Map of relative path -> sha256 for every regular file under `root`.

    Used for course checkouts, whose content is arbitrary (lectures/, tools/,
    docs/, data/…) and has nothing to do with the harness file list. VCS
    metadata and caches are skipped; symlinks are skipped (their targets are
    hashed through the files themselves). `skip_prefixes` drops subtrees that
    must never enter the record, such as a tool's own provenance file.
    """
    root = Path(root)
    skip = tuple(p.replace("\\", "/").strip("/") for p in skip_prefixes)
    fp = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in IGNORED_DIR_NAMES and not os.path.islink(os.path.join(dirpath, d))
        )
        rel_dir = os.path.relpath(dirpath, str(root)).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = ("%s/%s" % (rel_dir, name)) if rel_dir else name
            rel = os.path.normpath(rel).replace("\\", "/")
            if any(rel == p or rel.startswith(p + "/") for p in skip):
                continue
            fp[rel] = sha256_file(full)
    return fp


# ---------------------------------------------------------------------------
# Skill farms
# ---------------------------------------------------------------------------
def make_symlink_or_copy(target, link):
    """Link a skill directory; fall back to junction/copy where linking fails."""
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


def relink_skill_farms(root, dry=False, log=print):
    """Re-point every framework's skill farm at `.agents/skills/`.

    New skills appear here after an update; a farm entry that no longer has a
    source (a skill removed upstream) is reported but left alone, because only
    the student can know whether it is still wanted.
    """
    root = Path(root)
    skills = root / ".agents" / "skills"
    if not skills.is_dir():
        log("  no .agents/skills/ - nothing to link")
        return
    names = sorted(p.name for p in skills.iterdir() if p.is_dir())
    for farm in FARM_DIRS:
        created = 0
        for name in names:
            link = root / farm / name
            if dry:
                continue
            link.parent.mkdir(parents=True, exist_ok=True)
            known = link.exists() or link.is_symlink()
            if not make_symlink_or_copy(skills / name, link):
                log("  warning: symlink failed for %s; copied instead" % link)
            if not known:
                created += 1
        stale = []
        farm_dir = root / farm
        if farm_dir.is_dir():
            for entry in sorted(farm_dir.iterdir()):
                if entry.name not in names and (entry.is_symlink() or entry.is_dir()):
                    stale.append(entry.name)
        log("  %-18s %d skills%s%s"
            % (farm + "/", len(names),
               (", +%d new" % created) if created else "",
               (", stale: " + ", ".join(stale)) if stale else ""))


# ---------------------------------------------------------------------------
# Install / course records
# ---------------------------------------------------------------------------
def load_record(root, rel=INSTALL_RECORD):
    return load_json(Path(root) / rel, default=None)


def save_record(root, data, rel=INSTALL_RECORD):
    save_json_atomic(Path(root) / rel, data)


# ---------------------------------------------------------------------------
# git helpers (never required: the archive path works without git)
# ---------------------------------------------------------------------------
def git(args, cwd=None, timeout=180):
    """Run git, returning (returncode, stdout.strip()). Never raises."""
    try:
        p = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "%s: %s" % (type(e).__name__, e)
    return p.returncode, (p.stdout or "").strip()


def is_git_worktree(root):
    """True only for a directory that is the ROOT of its own git worktree.

    `rev-parse --is-inside-work-tree` is not enough: a course scaffolded inside
    an already-versioned workspace answers "true" through its parent repository.
    Treating that as "the course has its own history" makes course bookkeeping
    operate on the workspace repository instead — reading its remote, refusing
    updates for the wrong reason, and staging unrelated files.

    The caller must therefore own the top level of its worktree.
    """
    root = Path(root).resolve()
    rc, out = git(["rev-parse", "--is-inside-work-tree"], cwd=root)
    if rc != 0 or out.lower() != "true":
        return False
    rc, top = git(["rev-parse", "--show-toplevel"], cwd=root)
    if rc != 0 or not top:
        return False
    try:
        return Path(top).resolve() == root
    except OSError:
        return False


def git_origin(root):
    rc, out = git(["config", "--get", "remote.origin.url"], cwd=root)
    return out if rc == 0 else ""


def git_head(root, short=True):
    args = ["rev-parse", "--short", "HEAD"] if short else ["rev-parse", "HEAD"]
    rc, out = git(args, cwd=root)
    return out if rc == 0 else ""


def git_dirty(root):
    """List of changed/untracked paths, empty when the tree is clean."""
    rc, out = git(["status", "--porcelain"], cwd=root)
    return out.splitlines() if rc == 0 and out else []


def normalize_repo_url(url):
    """`git@github.com:o/r.git` and `https://github.com/o/r.git` -> same key."""
    if not url:
        return ""
    u = url.strip()
    if u.startswith("git@") and ":" in u:
        host, path = u[4:].split(":", 1)
        u = "https://%s/%s" % (host, path)
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def github_owner_repo(url):
    """(owner, repo) for a GitHub URL, else None."""
    u = normalize_repo_url(url)
    marker = "github.com/"
    if marker not in u:
        return None
    rest = u.split(marker, 1)[1].strip("/")
    parts = rest.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Synchronising one tree into another, without losing local work
# ---------------------------------------------------------------------------
def backup_file(src_path, backup_root, rel):
    if backup_root is None:
        return None
    dst = Path(backup_root) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src_path), str(dst))
    return dst


def sync_tree(src, dst, include=None, preserve=(), prev=None, overwrite=False,
              prune=False, backup_root=None, dry=False):
    """Copy files from `src` into `dst`, protecting anything the user changed.

    src, dst        directories (dst is created if missing)
    include(rel)    optional predicate; only rels it accepts are considered
    preserve        path prefixes (first component) that are never written or
                    deleted — student material such as `assignments/`
    prev            fingerprint of what was installed last time (upstream
                    truth). A file whose current hash differs from `prev`
                    was edited locally and is KEPT unless overwrite=True.
    overwrite       replace locally edited files too (they are backed up first)
    prune           delete files that upstream dropped (backed up first)
    backup_root     where replaced/removed files are copied
    dry             report only, change nothing

    Returns a report dict with sorted lists: added, updated, kept, removed,
    stale, preserved, unchanged.
    """
    src = Path(src)
    dst = Path(dst)
    prev = prev or {}

    report = {"added": [], "updated": [], "kept": [], "removed": [],
              "stale": [], "preserved": [], "unchanged": []}

    def preserved(rel):
        first = rel.split("/", 1)[0]
        return first in set(preserve)

    incoming = []
    for dirpath, dirnames, filenames in os.walk(str(src)):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in IGNORED_DIR_NAMES and not os.path.islink(os.path.join(dirpath, d))
        )
        rel_dir = os.path.relpath(dirpath, str(src)).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = ("%s/%s" % (rel_dir, name)) if rel_dir else name
            rel = os.path.normpath(rel).replace("\\", "/")
            if is_farm_path(rel):
                continue
            incoming.append(rel)

    incoming = sorted(set(incoming))
    if include is not None:
        incoming = [r for r in incoming if include(r)]

    for rel in incoming:
        if preserved(rel):
            report["preserved"].append(rel)
            continue
        s = src / rel
        d = dst / rel
        new_hash = sha256_file(s)
        if not d.exists():
            report["added"].append(rel)
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(s), str(d))
            continue
        if sha256_file(d) == new_hash:
            report["unchanged"].append(rel)
            continue
        local_edit = prev.get(rel) not in (None, sha256_file(d)) if prev else True
        if local_edit and not overwrite:
            report["kept"].append(rel)
            continue
        report["updated"].append(rel)
        if not dry:
            backup_file(d, backup_root, rel)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(s), str(d))

    for rel in sorted(prev):
        if rel in set(incoming) or preserved(rel) or is_farm_path(rel):
            continue
        if not (dst / rel).exists():
            continue
        if prune:
            report["removed"].append(rel)
            if not dry:
                backup_file(dst / rel, backup_root, rel)
                try:
                    (dst / rel).unlink()
                except OSError:
                    pass
        else:
            report["stale"].append(rel)

    return report


def report_sync(report, log=print, quiet_unchanged=True):
    """Print a sync report in a form a human can act on."""
    for key, label in (("added", "добавлено"), ("updated", "обновлено"),
                       ("kept", "сохранены локальные версии"),
                       ("removed", "удалено (нет в новой версии)"),
                       ("stale", "устарело, оставлено (--prune чтобы удалить)")):
        items = report.get(key) or []
        if not items:
            continue
        log("  %s: %d" % (label, len(items)))
        for rel in items[:12]:
            log("    %s" % rel)
        if len(items) > 12:
            log("    ... и ещё %d" % (len(items) - 12))
    if not quiet_unchanged and report.get("unchanged"):
        log("  без изменений: %d" % len(report["unchanged"]))


# ---------------------------------------------------------------------------
# Archives (GitHub tarballs) — downloaded, verified, extracted safely
# ---------------------------------------------------------------------------
def download(url, dest, timeout=600, user_agent="botai-updater"):
    """Download a URL to a file. Raises RuntimeError with a readable message."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            with open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s при загрузке %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise RuntimeError("нет связи с %s (%s)" % (url, e.reason))
    except OSError as e:
        raise RuntimeError("ошибка записи в %s: %s" % (dest, e))
    return dest


def safe_extract_tar(archive, dest):
    """Extract a tar archive, refusing anything that could escape `dest`.

    An upstream archive is still input from the network: `../` or absolute
    targets must never reach the filesystem. Symlinks inside the archive are not
    dangerous to `dest` if their resolved target stays under it, but GitHub
    ships this repo's skill-farm symlinks (`.claude/skills/*`); the archive
    cannot represent them reliably across platforms, and the farms are
    regenerated right after an update by relink_skill_farms(). So symlinks are
    skipped here — an empty farm in the extracted tree is expected.
    """
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(archive)) as tf:
        skipped_links = 0
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise RuntimeError("архив пытается писать вне каталога: %s" % member.name)
            if member.isdev():
                raise RuntimeError("архив содержит устройство: %s" % member.name)
            if member.issym() or member.islnk():
                skipped_links += 1
                continue
        tf.extractall(str(dest))
    return dest


def single_root(directory):
    """GitHub tarballs wrap everything in one top-level directory."""
    directory = Path(directory)
    entries = [e for e in directory.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return directory
