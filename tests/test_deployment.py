#!/usr/bin/env python3
"""Tests for the explicit pre-co-learning deployment gate."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import deployment  # noqa: E402

EXAMPLE = ROOT / "examples" / "minimal-course"


def workspace():
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    course = root / "courses" / "demo"
    course.parent.mkdir(parents=True)
    shutil.copytree(EXAMPLE, course)
    return tmp, root


def main():
    tmp, root = workspace()
    try:
        preview = deployment.choose(root, "demo", "prepare", allow_web=True, dry=True)
        assert preview["choice"] == "prepare"
        assert preview["permissions"]["allow_web_research"] is True
        assert not (root / ".botai").exists()

        saved = deployment.choose(root, "demo", "copilot", allow_derivatives=True)
        assert saved["status"] == "awaiting-preparation"
        loaded = deployment.load_choice(root, "demo")
        assert loaded["choice"] == "copilot"
        assert loaded["permissions"]["require_human_approval"] is True

        status = deployment.status(root, "demo")
        assert status["status"] == "awaiting-preparation"
        (root / "courses" / "demo" / "README.md").write_text("changed\n", encoding="utf-8")
        assert deployment.status(root, "demo")["status"] == "stale-source"

        oca = deployment.run_oca(root, "demo", offline=True)
        assert oca["status"] in {"completed", "unavailable"}
    finally:
        tmp.cleanup()
    print("deployment tests passed")


if __name__ == "__main__":
    main()
