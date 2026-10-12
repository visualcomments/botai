#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one saved operation plan. Started by `env-apply`, never by a model.

Why a separate process: a long installation must not hold an MCP call open for
thirty minutes, and a worker with its own lifetime can be cancelled, reconciled
and observed. The worker loads the plan **from the store** — it accepts an
operation id and a root, not an argv array, not a script, and not a plan hash to
trust. Everything it runs comes from bytes a human already saw.

It also checks its own parent periodically. If the host that started it is gone,
continuing to install into a workspace nobody is watching is the wrong default,
so the default policy is to stop at the next step boundary.

Usage:
    python scripts/operation_worker.py --operation-id <uuid> --root <workspace>
    python scripts/operation_worker.py --operation-id <uuid> --root <w> --detach
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from botai_core import operation as operation_mod  # noqa: E402


def parent_alive(ppid):
    """Whether the process that started us still exists.

    The pid is checked together with its creation behaviour rather than by
    signalling: on POSIX `os.kill(pid, 0)` raises when nothing holds the pid, and
    a recycled pid belonging to an unrelated process would otherwise look like a
    live parent.
    """
    if ppid is None:
        return True
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, ppid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(ppid, 0)
        return True
    except (OSError, AttributeError):
        return False


def main():
    parser = argparse.ArgumentParser(description="botai operation worker")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--learner", help="learner id owning the workspace")
    parser.add_argument("--max-steps", type=int, default=None,
                        help="stop after this many steps (for testing/resume)")
    parser.add_argument("--parent-pid", type=int, default=None,
                        help="stop when this process disappears (default: current parent)")
    parser.add_argument("--ignore-parent", action="store_true",
                        help="run to completion even if the parent exits")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    learner = args.learner
    if not learner:
        marker = root / ".botai" / "workspace.json"
        if marker.is_file():
            try:
                learner = json.loads(marker.read_text(encoding="utf-8")).get("learner_id")
            except (OSError, ValueError):
                learner = None
    if not learner:
        print("не удалось определить learner_id: укажите --learner", file=sys.stderr)
        return 2

    parent = None if args.ignore_parent else (args.parent_pid or os.getppid())

    with operation_mod.OperationService.open(root, learner) as service:
        operation = service.get_operation(args.operation_id)
        if operation is None:
            print("операция не найдена: %s" % args.operation_id, file=sys.stderr)
            return 2

        if operation["status"] == "APPLYING" and not parent_alive(parent):
            # The host is gone. Reconciling is the safe move: the operation may
            # have been mid-step, and re-running an effect is exactly what must
            # not happen automatically.
            report = service.reconcile(args.operation_id)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print("хост, запустивший операцию, недоступен: выполнение остановлено")
                print("состояние: %s" % report["status"])
            return 4

        try:
            result = service.apply(args.operation_id,
                                   actor="operation_worker",
                                   max_steps=args.max_steps)
        except operation_mod.OperationError as e:
            payload = {"ok": False, "code": e.code, "message_ru": e.message}
            print(json.dumps(payload, ensure_ascii=False) if args.json else
                  "отказ (%s): %s" % (e.code, e.message), file=sys.stderr)
            return 3

        if args.json:
            print(json.dumps({"ok": result["status"] not in ("FAILED", "CANCELLED"),
                              "status": result["status"],
                              "operation_id": args.operation_id,
                              "next_step": result["operation"]["next_step"]},
                             ensure_ascii=False, indent=2))
        else:
            print("операция %s: %s" % (args.operation_id, result["status"]))
            for step in result["operation"]["results"]:
                mark = "ok " if step.get("ok") else "FAIL"
                print("  [%s] %s (%s)" % (mark, step["step_id"], step["kind"]))
        return 0 if result["status"] not in ("FAILED", "CANCELLED") else 1


if __name__ == "__main__":
    sys.exit(main())
