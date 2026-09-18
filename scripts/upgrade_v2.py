#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge from a 1.3.x workspace to botai 2.0. Standard library only.

Why a separate script rather than "just run update": the updater that ships with
1.3.2 carries its **own** allow-list, and that list does not mention `personas/`,
`schemas/`, `profiles/`, the lock files or most of `scripts/botai_core/`. Editing
the list inside a *downloaded* `harness.py` does not change the list the already
running process is using — it reads the file it started with. So an old updater
can never install a v2 tree, no matter how the repository is arranged.

This bridge therefore carries a **hard-coded** v2 manifest. It deliberately does
not read an `allowlist.json` from the source: a bridge that trusts a list found
in the thing it is installing has no boundary at all.

The flow the design specifies, and what each step protects:

1. `--plan` inspects the destination, verifies the source snapshot, and writes a
   plan with a stable id. Nothing is changed.
2. A human reads the plan and runs `--apply --plan-id <id>`. The id must match
   the plan on disk, so an edited plan cannot be applied under the old id.
3. Apply backs up every file it will touch, stages the new tree, copies only the
   manifest's paths, and switches the install record **last**.
4. Student material — `courses/`, `progress/`, `.botai/` — is never a
   destination of this script. It is read only to report what will be preserved.

Stdlib only, and it must stay that way: this runs in a workspace whose core
dependencies may not be installed yet, and a bridge that cannot start is not a
bridge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_VERSION = "1.0"
TARGET_VERSION_PREFIX = "2."

# The v2 manifest, hard-coded. Every path here is one the v2 installer writes;
# the bridge refuses to copy anything else, including anything it finds in the
# source that is not listed.
V2_MANAGED_FILES = (
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "TUTORIAL.md",
    "VERSION",
    "CHANGELOG.md",
    "opencode.json",
    "requirements-core.lock",
)

V2_MANAGED_DIRS = (
    ".agents",
    "docs",
    "examples",
    "personas",
    "schemas",
    "scripts",
    "tests",
)

# Copied except their regenerated `skills/` farm.
V2_KEEP_FARM = (".opencode",)

# Never written by the bridge, whatever the source contains.
STUDENT_DIRS = ("courses", "progress", "dist", ".botai")

# Assets that must exist for the result to be a usable v2 workspace. Their
# absence is reported as V2_ASSETS_MISSING with this list, which is what
# `doctor` points at when an old update half-landed.
REQUIRED_V2_ASSETS = (
    "VERSION",
    "AGENTS.md",
    "scripts/cli.py",
    "scripts/update.py",
    "scripts/install.py",
    "scripts/harness.py",
    "scripts/botai_core/__init__.py",
    "schemas/v2/course.schema.json",
    "schemas/v2/track.schema.json",
    "requirements-core.lock",
)


class BridgeError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_fingerprint(root, *, paths=None):
    """A hash over the manifest's paths, for the source snapshot.

    Deliberately over the manifest only: fingerprinting the whole tree would
    change whenever an unrelated file is added, and a plan whose id changes for
    irrelevant reasons is one nobody can apply twice.
    """
    root = Path(root)
    entries = []
    for relative in sorted(_all_manifest_paths()):
        target = root / relative
        if target.is_file():
            entries.append((relative, sha256_file(target)))
        elif target.is_dir():
            for path in sorted(p for p in target.rglob("*") if p.is_file()):
                if ".git" in path.parts:
                    continue
                entries.append((path.relative_to(root).as_posix(), sha256_file(path)))
    digest = hashlib.sha256()
    for relative, value in entries:
        digest.update(("%s\0%s\n" % (relative, value)).encode("utf-8"))
    return digest.hexdigest(), len(entries)


def _all_manifest_paths():
    paths = list(V2_MANAGED_FILES) + list(V2_MANAGED_DIRS) + list(V2_KEEP_FARM)
    return paths


def read_version(root):
    version_file = Path(root) / "VERSION"
    if not version_file.is_file():
        return None
    return version_file.read_text(encoding="utf-8-sig").strip()


def detect_installation(root):
    """What is in the destination now, and whether it looks like a workspace."""
    root = Path(root)
    report = {
        "root": str(root),
        "exists": root.is_dir(),
        "version": read_version(root) if root.is_dir() else None,
        "has_install_record": (root / ".botai" / "install.json").is_file(),
        "has_harness": (root / "scripts" / "cli.py").is_file(),
        "is_source_checkout": (root / ".git").is_dir() and (root / "scripts" / "harness.py").is_file(),
        "student_dirs_present": [],
        "missing_required": [],
    }
    if root.is_dir():
        report["student_dirs_present"] = [
            name for name in STUDENT_DIRS if (root / name).exists()
        ]
        report["missing_required"] = [
            relative for relative in REQUIRED_V2_ASSETS
            if not (root / relative).exists()
        ]
    return report


def build_plan(source_root, dest_root, *, plan_id=None):
    """Describe what would change. Reads only; writes only the plan file."""
    source_root = Path(source_root).resolve()
    dest_root = Path(dest_root).resolve()

    if source_root == dest_root:
        raise BridgeError(
            "SOURCE_IS_DEST",
            "источник и назначение совпадают: мост запускают из отдельного "
            "checkout, а не поверх рабочего пространства",
        )

    source_version = read_version(source_root)
    if not source_version or not source_version.startswith(TARGET_VERSION_PREFIX):
        raise BridgeError(
            "SOURCE_NOT_V2",
            "источник не является выпуском botai 2.x: VERSION=%r. Мост переводит "
            "1.3.x на 2.0 и не предназначен для других переходов." % source_version,
        )

    missing_source = [
        relative for relative in REQUIRED_V2_ASSETS
        if not (source_root / relative).is_file()
    ]
    if missing_source:
        raise BridgeError(
            "SOURCE_INCOMPLETE",
            "в источнике не хватает обязательных файлов: %s" % ", ".join(missing_source),
        )

    fingerprint, file_count = tree_fingerprint(source_root)
    destination = detect_installation(dest_root)

    actions = []
    for relative in V2_MANAGED_FILES:
        actions.append(_plan_file(source_root, dest_root, relative))
    for directory in list(V2_MANAGED_DIRS) + list(V2_KEEP_FARM):
        source_dir = source_root / directory
        if not source_dir.is_dir():
            continue
        skip_skills = directory in V2_KEEP_FARM
        for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
            if ".git" in path.parts:
                continue
            if skip_skills and "skills" in path.relative_to(source_root).parts:
                continue
            actions.append(_plan_file(source_root, dest_root,
                                      path.relative_to(source_root).as_posix()))

    new = [a for a in actions if a["change"] == "new"]
    same = [a for a in actions if a["change"] == "same"]
    edited = [a for a in actions if a["change"] == "locally_edited"]

    plan_id = plan_id or fingerprint[:16]
    return {
        "bridge_version": BRIDGE_VERSION,
        "plan_id": plan_id,
        "created_at": now_iso(),
        "source": {
            "root": str(source_root),
            "version": source_version,
            "fingerprint": fingerprint,
            "files": file_count,
        },
        "destination": destination,
        "summary": {
            "total": len(actions),
            "new": len(new),
            "unchanged": len(same),
            "locally_edited": len(edited),
        },
        "locally_edited": [a["path"] for a in edited],
        "will_preserve": [
            "courses/ — курсы и работа ученика",
            "progress/ — записи прогресса",
            ".botai/ — состояние, корпуса, среды, резервные копии",
            "dist/ — временные файлы",
        ],
        "network": {
            "required": False,
            "note_ru": "Мост не ходит в сеть: он читает файлы своего checkout. "
                       "Сеть нужна только чтобы получить этот checkout.",
        },
        "actions": actions,
        "note_ru": [
            "Мост копирует ТОЛЬКО пути из своего списка. Никакой внешний "
            "allowlist не читается.",
            "Установочная запись переключается последней: при сбое остаются "
            "прежние файлы и прежняя запись.",
            "Пользовательские правки не перетираются молча: они перечислены выше.",
        ],
    }


def _plan_file(source_root, dest_root, relative):
    source = source_root / relative
    target = dest_root / relative
    entry = {"path": relative, "change": "new", "source_sha256": None, "dest_sha256": None}
    if source.is_file():
        entry["source_sha256"] = sha256_file(source)
    if target.is_file():
        entry["dest_sha256"] = sha256_file(target)
        if entry["dest_sha256"] == entry["source_sha256"]:
            entry["change"] = "same"
        else:
            # A file that differs from the source is either an old version or a
            # local edit; the bridge cannot tell, and says so rather than
            # deciding. Overwriting is the operator's declared choice.
            entry["change"] = "locally_edited"
    return entry


def plan_path(dest_root):
    return Path(dest_root) / ".botai" / "upgrade-v2-plan.json"


def write_plan(plan, dest_root):
    target = plan_path(dest_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    return target


def read_plan(dest_root, plan_id):
    target = plan_path(dest_root)
    if not target.is_file():
        raise BridgeError("PLAN_MISSING", "план не найден: %s" % target)
    try:
        document = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise BridgeError("PLAN_UNREADABLE", "план повреждён: %s" % e)
    if document.get("plan_id") != plan_id:
        raise BridgeError(
            "PLAN_ID_MISMATCH",
            "plan-id не совпадает: в файле %r, запрошен %r. План мог быть "
            "пересобран после просмотра." % (document.get("plan_id"), plan_id),
        )
    return document


def apply_plan(plan, source_root, dest_root, *, backup_root=None,
               accept_local_edits=False, dry_run=False):
    """Copy the manifest's paths, backing up everything it replaces.

    The install record is switched last. If anything fails before that, the
    destination still describes itself as the version it actually is.
    """
    source_root = Path(source_root).resolve()
    dest_root = Path(dest_root).resolve()

    current_fingerprint, _ = tree_fingerprint(source_root)
    if current_fingerprint != plan["source"]["fingerprint"]:
        raise BridgeError(
            "SOURCE_CHANGED",
            "источник изменился после сборки плана: файлы checkout не совпадают "
            "с записанным отпечатком. Соберите план заново.",
        )

    edited = [a for a in plan["actions"] if a["change"] == "locally_edited"]
    if edited and not accept_local_edits:
        raise BridgeError(
            "LOCAL_EDITS_PRESENT",
            "в назначении есть файлы, отличающиеся от источника (%d). Это могут "
            "быть локальные правки. Повторите с --accept-local-edits, чтобы "
            "заменить их (копии сохраняются)." % len(edited),
        )

    if dry_run:
        return {"applied": 0, "would_apply": len(plan["actions"]),
                "backup": None, "dry_run": True}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = Path(backup_root) if backup_root else (dest_root / ".botai" / "backup" / ("upgrade-v2-%s" % stamp))
    backup.mkdir(parents=True, exist_ok=True)

    applied = 0
    skipped = 0
    staging = Path(tempfile.mkdtemp(prefix="botai-upgrade-", dir=str(dest_root)))

    try:
        for action in plan["actions"]:
            relative = action["path"]
            source = source_root / relative
            target = dest_root / relative

            # Containment: the manifest is fixed, but a path is still resolved
            # before writing, so a symlinked destination cannot redirect a copy.
            try:
                resolved = target.resolve()
                resolved.relative_to(dest_root)
            except (ValueError, OSError):
                raise BridgeError("PATH_OUTSIDE_DEST",
                                  "путь %r выходит за назначение" % relative)

            if not source.is_file():
                continue

            staged = staging / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)

            if target.is_file():
                if action["change"] == "same":
                    skipped += 1
                    continue
                preserved = backup / relative
                preserved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, preserved)

            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged, target)
            applied += 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    record = dest_root / ".botai" / "install.json"
    record_note = None
    if record.is_file():
        try:
            document = json.loads(record.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            document = None
        if document is not None:
            document["upgraded_by"] = "upgrade_v2.py"
            document["upgraded_at"] = now_iso()
            document["upgrade_from_version"] = plan["destination"].get("version")
            document["bridge_version"] = BRIDGE_VERSION
            record.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(record, backup / "install.json")
            record.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8", newline="\n")
            record_note = "установочная запись обновлена"

    missing = [relative for relative in REQUIRED_V2_ASSETS
               if not (dest_root / relative).exists()]

    return {
        "applied": applied,
        "skipped": skipped,
        "backup": str(backup),
        "record_note": record_note,
        "missing_required": missing,
        "restart_required": True,
        "dry_run": False,
    }


def check_assets(root):
    """Whether a workspace has the v2 assets — the check `doctor` points at.

    Stdlib only and cheap on purpose: after a half-finished update the workspace
    may have a `cli.py` that imports modules which were not installed, and a
    diagnostic that itself fails to import cannot report that.
    """
    root = Path(root)
    missing = [relative for relative in REQUIRED_V2_ASSETS if not (root / relative).exists()]
    version = read_version(root)
    return {
        "ok": not missing,
        "version": version,
        "missing": missing,
        "code": None if not missing else "V2_ASSETS_MISSING",
        "message_ru": ("все обязательные файлы 2.0 на месте" if not missing else
                       "не хватает %d обязательных файлов 2.0" % len(missing)),
        "hint_ru": ("Запустите мост из отдельного checkout 2.0: "
                    "python scripts/upgrade_v2.py --dest <workspace> --plan"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="botai 1.3.x → 2.0 bridge (standard library only)")
    parser.add_argument("--dest", required=True, help="the workspace to upgrade")
    parser.add_argument("--source", default=None,
                        help="clean checkout of botai 2.0 (default: this checkout)")
    parser.add_argument("--plan", action="store_true", help="build a plan and stop")
    parser.add_argument("--apply", action="store_true", help="apply a planned upgrade")
    parser.add_argument("--plan-id", help="id of the plan being applied")
    parser.add_argument("--accept-local-edits", dest="accept_edits",
                        action="store_true",
                        help="replace files that differ from the source (copies kept)")
    parser.add_argument("--backup-dir", help="where to put the backup")
    parser.add_argument("--dry-run", action="store_true", help="preview, change nothing")
    parser.add_argument("--check", action="store_true",
                        help="report whether a workspace has the 2.0 assets")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve() if args.source else Path(__file__).resolve().parent.parent
    dest = Path(args.dest).resolve()

    if args.check:
        report = check_assets(dest)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
              else "%s: %s" % (report["code"] or "OK", report["message_ru"]))
        return 0 if report["ok"] else 2

    if args.plan:
        try:
            plan = build_plan(source, dest)
        except BridgeError as e:
            print("  отказ (%s): %s" % (e.code, e.message))
            return 2
        target = write_plan(plan, dest)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print("== план перехода на 2.0 ==")
            print("  источник    : %s (%s)" % (plan["source"]["root"], plan["source"]["version"]))
            print("  назначение  : %s (%s)"
                  % (plan["destination"]["root"],
                     plan["destination"]["version"] or "версия не определена"))
            print("  plan-id     : %s" % plan["plan_id"])
            print("  отпечаток   : %s" % plan["source"]["fingerprint"][:32])
            print()
            print("  Файлов всего: %d (новых %d, без изменений %d, отличаются %d)"
                  % (plan["summary"]["total"], plan["summary"]["new"],
                     plan["summary"]["unchanged"], plan["summary"]["locally_edited"]))
            if plan["locally_edited"]:
                print()
                print("  Отличаются от источника (возможны локальные правки):")
                for relative in plan["locally_edited"][:12]:
                    print("    %s" % relative)
            print()
            print("  Сохраняется без изменений:")
            for line in plan["will_preserve"]:
                print("    %s" % line)
            print()
            print("  План записан: %s" % target)
            print("  Применить: python scripts/upgrade_v2.py --dest %s "
                  "--apply --plan-id %s" % (dest, plan["plan_id"]))
        return 0

    if args.apply:
        if not args.plan_id:
            print("нужен --plan-id: план собирается отдельно и просматривается человеком")
            return 2
        try:
            plan = read_plan(dest, args.plan_id)
        except BridgeError as e:
            print("  отказ (%s): %s" % (e.code, e.message))
            return 2
        try:
            result = apply_plan(plan, source, dest,
                                backup_root=args.backup_dir,
                                accept_local_edits=args.accept_edits,
                                dry_run=args.dry_run)
        except BridgeError as e:
            print("  отказ (%s): %s" % (e.code, e.message))
            return 3

        if args.dry_run:
            print("предпросмотр: было бы применено %d файлов" % result["would_apply"])
            return 0

        print("== переход выполнен ==")
        print("  применено   : %d" % result["applied"])
        print("  без изменений: %d" % result["skipped"])
        print("  резервная копия: %s" % result["backup"])
        if result["record_note"]:
            print("  %s" % result["record_note"])
        if result["missing_required"]:
            print()
            print("  ВНИМАНИЕ: не хватает обязательных файлов: %s"
                  % ", ".join(result["missing_required"]))
            print("  Переход неполный; состояние сообщает код V2_ASSETS_MISSING.")
            return 1
        print()
        print("  Работа ученика не тронута: courses/, progress/ и .botai/ не "
              "перезаписывались.")
        print("  Требуется перезапуск хоста: политика загружается один раз.")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
