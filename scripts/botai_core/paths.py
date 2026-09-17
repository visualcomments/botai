# -*- coding: utf-8 -*-
"""Path resolution for the v2 core.

The v2 tools receive paths from documents, packets and model proposals — all of
which are untrusted input. `pathsafe` (the v1-era helper kept for compatibility,
since the workspace CLI imports it by name) already implements slug validation
and containment; this module re-exports that behaviour under the core package
and adds the v2-specific resolver used for learner/course scoped directories.

Keeping one implementation matters more than a tidy namespace: two independent
containment checks drift, and the weaker one becomes the real boundary.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pathsafe as _pathsafe  # noqa: E402

PathError = _pathsafe.PathError
validate_slug = _pathsafe.validate_slug
ensure_within = _pathsafe.ensure_within
courses_dir = _pathsafe.courses_dir
course_path = _pathsafe.course_path
progress_path = _pathsafe.progress_path
artifact_path = _pathsafe.artifact_path

__all__ = [
    "PathError", "validate_slug", "ensure_within", "courses_dir",
    "course_path", "progress_path", "artifact_path",
    "learner_state_dir", "course_state_dir", "artifact_store_dir", "safe_name",
]

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def safe_name(value, *, kind="идентификатор"):
    """Validate a UUID or slug used as a directory component.

    Used for learner ids, course ids and packet ids that arrive from documents
    or exports. A component that is not a plain identifier is refused rather
    than normalised, because silently renaming an identifier would break the
    link between a record and its evidence.
    """
    text = value if isinstance(value, str) else str(value or "")
    if not text:
        raise PathError("NAME_EMPTY", "%s не задан" % kind)
    if _UUID_RE.match(text):
        return text.lower()
    return validate_slug(text, allow_legacy=True)


def learner_state_dir(root, learner_id):
    """`progress/<learner-id>/` — the per-learner projection directory."""
    base = Path(root) / "progress"
    return ensure_within(base, base / safe_name(learner_id, kind="идентификатор обучающегося"))


def course_state_dir(root, course_id):
    """`.botai/courses/<course-id>/` — binding and accepted snapshots."""
    base = Path(root) / ".botai" / "courses"
    return ensure_within(base, base / safe_name(course_id, kind="идентификатор курса"))


def artifact_store_dir(root):
    """`.botai/artifacts/` — content-addressed evidence snapshots."""
    return ensure_within(Path(root), Path(root) / ".botai" / "artifacts")
