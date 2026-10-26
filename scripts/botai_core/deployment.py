# -*- coding: utf-8 -*-
"""Course deployment choice and deterministic preparation gate.

This module deliberately does not call an LLM or mutate course materials. It
records the learner's explicit choice before optional quality preparation and
can run the optional OCA scanner when installed. Generated improvements remain
proposals until a human accepts a deployment.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import schemas
from .paths import course_path, ensure_within, safe_name

CHOICES = ("as-is", "prepare", "copilot")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _state_dir(root, course):
    base = Path(root) / ".botai" / "deployments"
    return ensure_within(base, base / safe_name(course, kind="идентификатор курса"))


def _state_path(root, course):
    return _state_dir(root, course) / "choice.json"


def _sha256_tree(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in Path(path).rglob("*") if p.is_file() and ".git" not in p.parts):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def choose(root, course, choice, *, allow_web=False, allow_local_copy=False,
           allow_derivatives=False, language="ru", learner_level="unknown",
           time_budget="standard", dry=False):
    if choice not in CHOICES:
        raise ValueError("неизвестный режим подготовки: %s" % choice)
    course_dir = course_path(root, course, must_exist=True)
    payload = {
        "schema_version": 2,
        "course": course,
        "choice": choice,
        "selected_at": _now(),
        "course_snapshot": _sha256_tree(course_dir),
        "permissions": {
            "allow_web_research": bool(allow_web),
            "allow_local_copy_of_sources": bool(allow_local_copy),
            "allow_derivative_supplements": bool(allow_derivatives),
            "require_human_approval": True,
        },
        "learner": {"language": language, "level": learner_level},
        "time_budget": time_budget,
        "status": "selected" if choice == "as-is" else "awaiting-preparation",
        "note_ru": (
            "Субагенты не изменяют исходный курс и не выпускают дополнения без "
            "отдельного человеческого принятия."
        ),
    }
    schemas.validate(payload, "deployment_choice")
    if dry:
        return payload
    path = _state_path(root, course)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_choice(root, course):
    path = _state_path(root, course)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        schemas.validate(payload, "deployment_choice")
        return payload
    except (OSError, ValueError, schemas.SchemaError) as exc:
        raise ValueError("повреждён выбор deployment: %s" % exc) from exc


def run_oca(root, course, *, offline=False):
    """Run OCA if available; never silently claim that it ran."""
    course_dir = course_path(root, course, must_exist=True)
    executable = shutil.which("oca")
    if not executable:
        return {"status": "unavailable", "reason": "команда oca не найдена", "course": course}
    command = [executable, "scan", str(course_dir), "--json"]
    if offline:
        command.append("--offline")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "reason": str(exc), "course": course}
    if result.returncode != 0:
        return {"status": "failed", "exit_code": result.returncode,
                "stderr": result.stderr[-2000:], "course": course}
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "failed", "reason": "oca не вернул JSON", "stdout": result.stdout[-2000:], "course": course}
    return {"status": "completed", "course": course, "report": report}


def status(root, course):
    choice = load_choice(root, course)
    if not choice:
        return {"course": course, "status": "not-selected", "choice": None}
    current = _sha256_tree(course_path(root, course, must_exist=True))
    result = dict(choice)
    result["status"] = "stale-source" if current != choice["course_snapshot"] else choice["status"]
    result["current_snapshot"] = current
    return result
