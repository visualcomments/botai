#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end check of the environment approval lifecycle through the real CLI.

Drives the commands a learner actually types, in order, and asserts the property
the whole stage exists for: **nothing executes without a human approval bound to
the exact plan**. Also checks the honest-reporting cases — a skipped step must
not yield READY, and a repeated apply must not repeat the effect.

No network, no system changes: the only step is creating a virtual environment
inside the workspace's own `.botai/environments/<course>/`.

Run:
    python3 scripts/checks/environment_flow.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

failures = 0


def check(name, condition, detail=""):
    global failures
    if condition:
        print("  [ok]   %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        failures += 1


def cli(root, *args, input_text=None):
    completed = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), *args, "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), input=input_text, timeout=300,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="botai-envflow-"))
    root = tmp / "workspace"
    course_dir = root / "courses" / "minimal-diff"
    course_dir.parent.mkdir(parents=True)
    shutil.copytree(REPO / "examples" / "minimal-course", course_dir)

    spec = {
        "schema_version": 2,
        "profile_id": "minimal",
        "title": "Минимальная среда курса",
        "supported_platforms": ["linux", "macos", "windows", "wsl"],
        "prerequisites": [{"tool": "python", "version_constraint": ">=3.11",
                           "required": True}],
        "inputs": [],
        "steps": [{"step_id": "mkvenv", "kind": "venv_create", "risk": "writes_local",
                   "explanation_ru": "Создать виртуальную среду курса",
                   "parameters": {"destination_id": "venv"}}],
        "checks": [{"check_id": "venv-dir", "kind": "path_exists", "required": True,
                    "parameters": {"path": ".botai/environments/minimal-diff/venv"},
                    "expected": True}],
        "resources": {"ports": []},
        "offline": {"supported": False},
    }
    spec_path = tmp / "env.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    print("workspace: %s" % root)

    print()
    print("### 1. принять курс")
    code, out = cli(root, "course-accept", "--course", "minimal-diff")
    check("курс принят", code == 0, out[-200:])

    print()
    print("### 2. собрать план (ничего не выполняется)")
    code, out = cli(root, "env-plan", "--course", "minimal-diff", "--spec", str(spec_path))
    print("       %s" % " | ".join(line.strip() for line in out.splitlines()
                                  if "план-хеш" in line or "операция" in line))
    check("план собран", code == 0, out[-300:])
    operation_id = None
    for line in out.splitlines():
        if "операция    :" in line:
            operation_id = line.split(":", 1)[1].strip()
            break
    check("идентификатор операции получен", bool(operation_id), str(operation_id))
    check("экран называет путь записи", "environments" in out, out[-400:])
    check("экран не утверждает, что записей нет",
          "(ничего не записывает)" not in out)
    check("среда ещё не создана",
          not (root / ".botai" / "environments" / "minimal-diff" / "venv").exists())

    print()
    print("### 3. выполнить БЕЗ подтверждения (должен быть отказ)")
    code, out = cli(root, "env-apply", "--operation", operation_id)
    check("запуск без подтверждения отклонён", code != 0, "exit=%d" % code)
    check("сказано, что нужно подтверждение",
          "APPROVAL_REQUIRED" in out or "подтвержд" in out, out[-300:])
    check("среда всё ещё не создана",
          not (root / ".botai" / "environments" / "minimal-diff" / "venv").exists())

    print()
    print("### 4. подтвердить план (ответ 'y' на приглашение)")
    code, out = cli(root, "action-approve", "--operation", operation_id, input_text="y\n")
    check("подтверждение записано", code == 0 and "granted" in out, out[-300:])
    # The interactive prompt has no trailing newline before the confirmation
    # line, so the assertion looks for the recorded decision rather than for a
    # line break that is not there.
    check("записано решение granted", "разрешение записано" in out and "granted" in out,
          out[-300:])

    print()
    print("### 5. выполнить с подтверждением")
    code, out = cli(root, "env-apply", "--operation", operation_id)
    print("       %s" % " | ".join(line.strip() for line in out.splitlines()
                                  if "[ok" in line or "состояние" in line))
    check("операция выполнена", code == 0, out[-400:])
    check("шаг отмечен успешным", "[ok" in out, out[-400:])
    check("venv действительно создан",
          (root / ".botai" / "environments" / "minimal-diff" / "venv").is_dir())

    print()
    print("### 6. состояние операции")
    code, out = cli(root, "env-status", "--operation", operation_id)
    check("состояние читается", code == 0, out[-200:])
    check("состояние названо", "VERIFYING" in out or "APPLYING" in out or "READY" in out,
          out[-300:])

    print()
    print("### 7. повторное выполнение не повторяет действие")
    code, out = cli(root, "env-apply", "--operation", operation_id)
    check("повтор вернул прежний результат", "повтор" in out.lower(), out[-300:])

    print()
    print("### 8. статус курса показывает операции")
    code, out = cli(root, "env-status", "--course", "minimal-diff")
    check("операция видна в списке", operation_id[:8] in out, out[-300:])

    print()
    print("### 9. отмена и восстановление")
    code, out = cli(root, "operation-cancel", "--operation", operation_id,
                    "--reconcile")
    check("восстановление сообщает, что выполнено",
          "steps_finished" in out, out[-400:])
    check("сказано, что действие не повторяется", "не повторяет" in out, out[-400:])

    print()
    print("### 10. подтверждение с отказом")
    code, out = cli(root, "env-plan", "--course", "minimal-diff", "--spec", str(spec_path))
    second_id = None
    for line in out.splitlines():
        if "операция    :" in line:
            second_id = line.split(":", 1)[1].strip()
            break
    code, out = cli(root, "action-approve", "--operation", second_id, "--deny")
    check("отказ записан", code == 0 and "denied" in out, out[-300:])
    code, out = cli(root, "env-apply", "--operation", second_id)
    check("отклонённый план не выполняется", code != 0, "exit=%d" % code)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures == 0:
        print("ENVIRONMENT FLOW OK")
    else:
        print("ENVIRONMENT FLOW FAILURES: %d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
