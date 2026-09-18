#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.3.x → 2.0 bridge.

The bridge exists because the 1.3.2 updater carries its own allow-list that does
not mention `personas/`, `schemas/` or the lock files — so it can never install a
v2 tree. These tests check the properties that make the bridge safe to run
against a real workspace:

* it copies **only** its hard-coded manifest, never a list found in the source;
* it never writes into `courses/`, `progress/` or `.botai/` student material;
* a plan is bound to its id and to the source fingerprint, so an edited plan or a
  changed checkout cannot be applied under the old id;
* local edits are reported and, unless explicitly accepted, **stop** the apply;
* the install record is switched last, and a partial result reports
  `V2_ASSETS_MISSING` rather than claiming success.

Run:
    python3 tests/test_migrations.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import upgrade_v2 as B  # noqa: E402

_passed = 0
_failures: list[str] = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def expect_error(name, fn, code):
    try:
        fn()
    except B.BridgeError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Workspace:
    """A 1.3.x-style workspace plus a clean 2.0 source tree."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.source = self.base / "source"
        self.dest = self.base / "workspace"

        # The source is a small stand-in for a 2.0 checkout: the manifest's
        # required assets plus enough shape to be recognisable.
        (self.source / "scripts" / "botai_core").mkdir(parents=True)
        (self.source / "schemas" / "v2").mkdir(parents=True)
        (self.source / "docs").mkdir()
        (self.source / "examples").mkdir()
        (self.source / "personas").mkdir()
        (self.source / "tests").mkdir()
        (self.source / ".agents").mkdir()

        (self.source / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        for relative in ("AGENTS.md", "CLAUDE.md", "LICENSE", "Makefile",
                         "README.md", "TUTORIAL.md", "CHANGELOG.md",
                         ".gitignore", "opencode.json",
                         "requirements-core.lock"):
            (self.source / relative).write_text("content of %s\n" % relative,
                                                encoding="utf-8")
        for relative in ("scripts/cli.py", "scripts/update.py", "scripts/install.py",
                         "scripts/harness.py"):
            (self.source / relative).write_text("# %s\n" % relative, encoding="utf-8")
        (self.source / "scripts" / "botai_core" / "__init__.py").write_text(
            "SCHEMA_VERSION = 2\n", encoding="utf-8")
        for name in ("course", "track"):
            (self.source / "schemas" / "v2" / ("%s.schema.json" % name)).write_text(
                "{}\n", encoding="utf-8")
        (self.source / "personas" / "neutral.json").write_text("{}\n", encoding="utf-8")
        (self.source / "docs" / "botai-v2-design.md").write_text("# design\n",
                                                                encoding="utf-8")

        # The destination: an old workspace with student material in it.
        (self.dest / "scripts").mkdir(parents=True)
        (self.dest / ".botai").mkdir()
        (self.dest / "courses" / "course-a").mkdir(parents=True)
        (self.dest / "progress").mkdir()
        (self.dest / "dist").mkdir()

        (self.dest / "VERSION").write_text("1.3.2\n", encoding="utf-8")
        (self.dest / "AGENTS.md").write_text("old policy\n", encoding="utf-8")
        (self.dest / "scripts" / "cli.py").write_text("# old cli\n", encoding="utf-8")
        (self.dest / "scripts" / "harness.py").write_text("# old harness\n",
                                                          encoding="utf-8")
        (self.dest / "courses" / "course-a" / "work.md").write_text(
            "работа ученика\n", encoding="utf-8")
        (self.dest / "progress" / "course-a.md").write_text(
            "# прогресс\n", encoding="utf-8")
        (self.dest / ".botai" / "install.json").write_text(
            json.dumps({"schema_version": 1, "version": "1.3.2", "files": []}) + "\n",
            encoding="utf-8")
        (self.dest / ".botai" / "state.sqlite3").write_text("state\n", encoding="utf-8")

    def cleanup(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def test_plan_reads_and_changes_nothing():
    w = Workspace()
    try:
        before = sorted(p.relative_to(w.dest).as_posix()
                        for p in w.dest.rglob("*") if p.is_file())
        plan = B.build_plan(w.source, w.dest)
        after = sorted(p.relative_to(w.dest).as_posix()
                       for p in w.dest.rglob("*") if p.is_file())
        check("план ничего не изменил в назначении", before == after)
        check("план называет источник", plan["source"]["root"] == str(w.source.resolve()))
        check("план фиксирует отпечаток источника",
              len(plan["source"]["fingerprint"]) == 64, plan["source"]["fingerprint"])
        check("план перечисляет сохраняемое",
              any("courses/" in item for item in plan["will_preserve"]),
              str(plan["will_preserve"]))
        check("план говорит, что сеть не нужна",
              plan["network"]["required"] is False)
    finally:
        w.cleanup()


def test_plan_refuses_a_non_v2_source():
    w = Workspace()
    try:
        (w.source / "VERSION").write_text("1.3.2\n", encoding="utf-8")
        expect_error("источник не 2.x отклонён",
                     lambda: B.build_plan(w.source, w.dest), "SOURCE_NOT_V2")
    finally:
        w.cleanup()


def test_plan_refuses_an_incomplete_source():
    w = Workspace()
    try:
        (w.source / "schemas" / "v2" / "course.schema.json").unlink()
        expect_error("неполный источник отклонён",
                     lambda: B.build_plan(w.source, w.dest), "SOURCE_INCOMPLETE")
    finally:
        w.cleanup()


def test_plan_refuses_source_equal_to_destination():
    w = Workspace()
    try:
        expect_error("источник поверх назначения отклонён",
                     lambda: B.build_plan(w.source, w.source), "SOURCE_IS_DEST")
    finally:
        w.cleanup()


def test_plan_reports_local_edits():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        check("изменённый AGENTS.md найден среди отличающихся",
              "AGENTS.md" in plan["locally_edited"], str(plan["locally_edited"]))
        check("новые файлы посчитаны", plan["summary"]["new"] > 0,
              str(plan["summary"]))
    finally:
        w.cleanup()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def test_apply_installs_the_manifest():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        B.write_plan(plan, w.dest)
        result = B.apply_plan(plan, w.source, w.dest, accept_local_edits=True)

        check("файлы применены", result["applied"] > 0, str(result))
        check("VERSION обновлён",
              (w.dest / "VERSION").read_text(encoding="utf-8").strip().startswith("2."),
              (w.dest / "VERSION").read_text(encoding="utf-8"))
        check("появился botai_core",
              (w.dest / "scripts" / "botai_core" / "__init__.py").is_file())
        check("появились схемы",
              (w.dest / "schemas" / "v2" / "course.schema.json").is_file())
        check("появились персоны",
              (w.dest / "personas" / "neutral.json").is_file())
        check("обязательных файлов не не хватает",
              result["missing_required"] == [], str(result["missing_required"]))
        check("сообщено о необходимости перезапуска",
              result["restart_required"] is True)
    finally:
        w.cleanup()


def test_apply_never_touches_student_material():
    w = Workspace()
    try:
        course_work = (w.dest / "courses" / "course-a" / "work.md").read_text(
            encoding="utf-8")
        progress = (w.dest / "progress" / "course-a.md").read_text(encoding="utf-8")

        plan = B.build_plan(w.source, w.dest)
        B.apply_plan(plan, w.source, w.dest, accept_local_edits=True)

        check("работа ученика не изменена",
              (w.dest / "courses" / "course-a" / "work.md").read_text(
                  encoding="utf-8") == course_work)
        check("прогресс не изменён",
              (w.dest / "progress" / "course-a.md").read_text(
                  encoding="utf-8") == progress)
        check("состояние .botai/state.sqlite3 не тронуто",
              (w.dest / ".botai" / "state.sqlite3").is_file())
        check("план не перечисляет студенческие пути как цели",
              not any(a["path"].startswith(("courses/", "progress/"))
                      for a in plan["actions"]),
              str([a["path"] for a in plan["actions"]
                   if a["path"].startswith("courses/")])[:200])
    finally:
        w.cleanup()


def test_local_edits_stop_apply_without_the_flag():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        expect_error("локальные правки останавливают применение",
                     lambda: B.apply_plan(plan, w.source, w.dest),
                     "LOCAL_EDITS_PRESENT")
        check("файл не заменён",
              (w.dest / "AGENTS.md").read_text(encoding="utf-8") == "old policy\n")
    finally:
        w.cleanup()


def test_apply_backs_up_what_it_replaces():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        result = B.apply_plan(plan, w.source, w.dest, accept_local_edits=True)
        backup = Path(result["backup"])
        check("резервная копия создана", backup.is_dir(), str(backup))
        check("старый AGENTS.md сохранён",
              (backup / "AGENTS.md").is_file())
        check("сохранённое содержимое — прежнее",
              (backup / "AGENTS.md").read_text(encoding="utf-8") == "old policy\n")
        check("прежняя установочная запись сохранена",
              (backup / "install.json").is_file())
    finally:
        w.cleanup()


def test_apply_switches_the_record_last():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        B.apply_plan(plan, w.source, w.dest, accept_local_edits=True)
        record = json.loads((w.dest / ".botai" / "install.json").read_text(encoding="utf-8"))
        check("запись помечена как обновлённая мостом",
              record.get("upgraded_by") == "upgrade_v2.py", str(record)[:200])
        check("прежняя версия записана",
              record.get("upgrade_from_version") == "1.3.2", str(record)[:200])
    finally:
        w.cleanup()


def test_apply_refuses_a_changed_source():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        (w.source / "AGENTS.md").write_text("changed after planning\n", encoding="utf-8")
        expect_error("изменившийся источник отклонён",
                     lambda: B.apply_plan(plan, w.source, w.dest,
                                          accept_local_edits=True),
                     "SOURCE_CHANGED")
    finally:
        w.cleanup()


def test_plan_id_binds_the_plan():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        B.write_plan(plan, w.dest)
        expect_error("чужой plan-id отклонён",
                     lambda: B.read_plan(w.dest, "deadbeef"), "PLAN_ID_MISMATCH")
        reloaded = B.read_plan(w.dest, plan["plan_id"])
        check("свой plan-id принимается", reloaded["plan_id"] == plan["plan_id"])
    finally:
        w.cleanup()


# ---------------------------------------------------------------------------
# The half-finished update the design names
# ---------------------------------------------------------------------------
def test_half_finished_update_reports_v2_assets_missing():
    """An old updater that copied only part of the v2 tree must be detectable."""
    w = Workspace()
    try:
        # Simulate: the old update replaced VERSION and cli.py but never
        # installed botai_core, schemas or personas.
        (w.dest / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        (w.dest / "scripts" / "cli.py").write_text(
            "from botai_core import course\n", encoding="utf-8")

        report = B.check_assets(w.dest)
        check("неполное обновление обнаружено", report["ok"] is False, str(report))
        check("код назван", report["code"] == "V2_ASSETS_MISSING", report["code"])
        check("перечислены конкретные файлы",
              "scripts/botai_core/__init__.py" in report["missing"],
              str(report["missing"]))
        check("подсказан мост", "upgrade_v2.py" in report["hint_ru"],
              report["hint_ru"])
    finally:
        w.cleanup()


def test_check_assets_is_stdlib_only():
    """It must run where the core cannot be imported yet."""
    w = Workspace()
    try:
        source = (ROOT / "scripts" / "upgrade_v2.py").read_text(encoding="utf-8")
        for forbidden in ("import botai_core", "from botai_core",
                          "import jsonschema", "from jsonschema"):
            check("мост не импортирует %r" % forbidden, forbidden not in source,
                  forbidden)

        # The string `allowlist.json` appears in the docstring explaining that no
        # such file is read; what must not appear is code that opens one.
        check("мост не читает внешний allowlist по имени",
              "allowlist.json\"" not in source and "allowlist.json'" not in source
              and "read_text" not in source.split("allowlist.json")[0][-200:],
              "проверьте вручную")
        check("мост не открывает ничего по пути из источника",
              "json.loads((source_root" not in source,
              "мост не должен читать конфигурацию из устанавливаемого дерева")
    finally:
        w.cleanup()


def test_check_assets_after_a_full_apply():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        B.apply_plan(plan, w.source, w.dest, accept_local_edits=True)
        report = B.check_assets(w.dest)
        check("после полного перехода обязательные файлы на месте",
              report["ok"] is True, str(report["missing"]))
    finally:
        w.cleanup()


def test_manifest_is_hard_coded_not_read_from_source():
    """A bridge that trusts a list in the thing it installs has no boundary."""
    w = Workspace()
    try:
        # Plant a tempting extra directory in the source.
        (w.source / "malicious").mkdir()
        (w.source / "malicious" / "payload.py").write_text("print('x')\n",
                                                           encoding="utf-8")
        plan = B.build_plan(w.source, w.dest)
        copied = {a["path"] for a in plan["actions"]}
        check("посторонний каталог не попал в план",
              not any(p.startswith("malicious") for p in copied),
              str([p for p in copied if p.startswith("malicious")]))
        check("манифест — фиксированный кортеж в коде",
              isinstance(B.V2_MANAGED_DIRS, tuple) and "malicious" not in B.V2_MANAGED_DIRS)
    finally:
        w.cleanup()


def test_dry_run_applies_nothing():
    w = Workspace()
    try:
        plan = B.build_plan(w.source, w.dest)
        result = B.apply_plan(plan, w.source, w.dest, accept_local_edits=True,
                              dry_run=True)
        check("предпросмотр ничего не применил", result["applied"] == 0, str(result))
        check("VERSION остался прежним",
              (w.dest / "VERSION").read_text(encoding="utf-8").strip() == "1.3.2")
    finally:
        w.cleanup()


def test_cli_end_to_end_through_subprocess():
    """The bridge is run as a script, so it is exercised as one."""
    w = Workspace()
    try:
        bridge = ROOT / "scripts" / "upgrade_v2.py"
        planned = subprocess.run(
            [sys.executable, str(bridge), "--source", str(w.source),
             "--dest", str(w.dest), "--plan", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        check("планирование через CLI прошло", planned.returncode == 0,
              planned.stderr[-300:])
        plan = json.loads(planned.stdout)
        check("план записан", B.plan_path(w.dest).is_file())

        applied = subprocess.run(
            [sys.executable, str(bridge), "--source", str(w.source),
             "--dest", str(w.dest), "--apply", "--plan-id", plan["plan_id"],
             "--accept-local-edits"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        check("применение через CLI прошло", applied.returncode == 0,
              applied.stderr[-300:])
        check("VERSION стал 2.x",
              (w.dest / "VERSION").read_text(encoding="utf-8").strip().startswith("2."))

        checked = subprocess.run(
            [sys.executable, str(bridge), "--dest", str(w.dest), "--check", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        report = json.loads(checked.stdout)
        check("проверка подтверждает готовность", report["ok"] is True, str(report))
    finally:
        w.cleanup()


def test_apply_without_plan_id_is_refused():
    w = Workspace()
    try:
        bridge = ROOT / "scripts" / "upgrade_v2.py"
        result = subprocess.run(
            [sys.executable, str(bridge), "--source", str(w.source),
             "--dest", str(w.dest), "--apply"],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        check("применение без plan-id отклонено", result.returncode == 2,
              str(result.returncode))
        check("сказано, что план просматривает человек",
              "человек" in (result.stdout + result.stderr),
              (result.stdout + result.stderr)[-200:])
    finally:
        w.cleanup()


def main():
    tests = [
        test_plan_reads_and_changes_nothing,
        test_plan_refuses_a_non_v2_source,
        test_plan_refuses_an_incomplete_source,
        test_plan_refuses_source_equal_to_destination,
        test_plan_reports_local_edits,
        test_apply_installs_the_manifest,
        test_apply_never_touches_student_material,
        test_local_edits_stop_apply_without_the_flag,
        test_apply_backs_up_what_it_replaces,
        test_apply_switches_the_record_last,
        test_apply_refuses_a_changed_source,
        test_plan_id_binds_the_plan,
        test_half_finished_update_reports_v2_assets_missing,
        test_check_assets_is_stdlib_only,
        test_check_assets_after_a_full_apply,
        test_manifest_is_hard_coded_not_read_from_source,
        test_dry_run_applies_nothing,
        test_cli_end_to_end_through_subprocess,
        test_apply_without_plan_id_is_refused,
    ]
    for test in tests:
        print("== %s ==" % test.__name__)
        try:
            test()
        except Exception as e:  # noqa: BLE001
            import traceback
            _failures.append(test.__name__)
            print("  FAIL %s выбросил %s: %s" % (test.__name__, type(e).__name__, e))
            traceback.print_exc()

    print()
    print("%d passed, %d failed" % (_passed, len(_failures)))
    if _failures:
        for name in _failures:
            print("  - %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
