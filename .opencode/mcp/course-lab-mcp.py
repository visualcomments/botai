#!/usr/bin/env python3
"""course-lab MCP server.

Exposes the practice-course content (lessons and assignments) under
course-lab/ to the botai agent as MCP tools, so the agent can read the
materials it teaches from.

Deliberately does NOT expose course-lab/solutions/ (the answer keys):
those are graded material, gated by AGENTS.md, and must not be reachable
through the agent's tools before a student has made their own attempt.

Run standalone to check tooling:
    python3 course-lab-mcp.py --list

Run as an MCP server over stdio:
    python3 course-lab-mcp.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_DIR = REPO_ROOT / "course-lab"
LESSONS_DIR = LAB_DIR / "lessons"
ASSIGNMENTS_DIR = LAB_DIR / "assignments"


def _read_markdown(path: Path) -> str:
    """Read a course file, returning its markdown body."""
    if not path.exists():
        return f"file not found: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _slugify(name: str) -> str:
    return os.path.splitext(os.path.basename(name))[0]


mcp = FastMCP("course-lab", instructions=(
    "Course-lab content access. Use these tools to read the practice course "
    "materials (lessons and assignments) under course-lab/. The solutions/ "
    "answer keys are graded material and are NOT exposed here; do not try to "
    "reach them through other means before a student has attempted the task."
))


@mcp.tool()
def list_lessons() -> str:
    """List the lesson files of the course-lab practice course."""
    if not LESSONS_DIR.exists():
        return "course-lab/lessons not found"
    files = sorted(LESSONS_DIR.glob("*.md"))
    if not files:
        return "no lessons found"
    return "\n".join(f"- {_slugify(f.name)}" for f in files)


@mcp.tool()
def read_lesson(lesson: str) -> str:
    """Read a lesson by name (with or without .md), e.g. 'week03'."""
    for f in LESSONS_DIR.glob("*.md"):
        if _slugify(f.name) == _slugify(lesson):
            return _read_markdown(f)
    return f"lesson not found: {lesson} (see list_lessons)"


@mcp.tool()
def list_assignments() -> str:
    """List the assignment files of the course-lab practice course."""
    if not ASSIGNMENTS_DIR.exists():
        return "course-lab/assignments not found"
    files = sorted(ASSIGNMENTS_DIR.glob("*.md"))
    if not files:
        return "no assignments found"
    return "\n".join(f"- {_slugify(f.name)}" for f in files)


@mcp.tool()
def read_assignment(assignment: str) -> str:
    """Read an assignment by name (with or without .md), e.g. 'task3_hypotheses'."""
    for f in ASSIGNMENTS_DIR.glob("*.md"):
        if _slugify(f.name) == _slugify(assignment):
            return _read_markdown(f)
    return f"assignment not found: {assignment} (see list_assignments)"


def main() -> None:
    parser = argparse.ArgumentParser(description="course-lab MCP server")
    parser.add_argument("--list", action="store_true", help="list available tools and exit")
    args = parser.parse_args()

    if args.list:
        print("tools:")
        for tool in (list_lessons, read_lesson, list_assignments, read_assignment):
            print(f"  {tool.__name__}()")
        sys.exit(0)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
