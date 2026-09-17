#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path resolution and containment for every botai entry point.

`--course`, `--name` and `--url` all end up as filesystem paths. Composing those
paths with `Path / user_input` is traversal-prone: an absolute path discards the
prefix, `..` climbs out, and a symlink or Windows junction redirects the write
somewhere else entirely. Every entry point (CLI, courses, corpus, future v2
tools) must therefore resolve course paths through this module rather than
joining strings itself.

Two rules make the rest of the code honest:

* a slug is a name, not a path — it may not contain separators, drive letters,
  `.`/`..`, control characters or Windows device names, and it may not end with
  a dot or a space (Windows silently strips those, so `a.` and `a` would
  collide);
* a resolved path must stay under its root with symlinks resolved, so a link
  pointing outside the workspace is refused instead of followed.

Legacy installations may legitimately hold course directories whose names are
not valid new slugs (Cyrillic, underscores, spaces). `slugify()` normalises the
name it is given, and `resolve_course()` accepts an existing directory under the
root even when the slug rules would reject it — but it never lets such a name
escape containment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# A conservative new-slug pattern: lowercase latin, digits, hyphen. It is
# deliberately narrower than "anything a filename allows", because these values
# travel into git URLs, JSON ids and shell examples.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

# Windows refuses (or silently rewrites) these names in any directory.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_BAD_CHARS = set('<>:"/\\|?*')


class PathError(ValueError):
    """A path or slug that must not be used, with a human-readable reason."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _problem(code, message):
    raise PathError(code, message)


def validate_slug(name, *, allow_legacy=False):
    """Return a safe slug, or raise PathError with a specific code.

    With `allow_legacy=True` an already-acceptable legacy directory name is
    returned unchanged (Cyrillic, underscore, dot); it is still checked for
    separators, traversal, NUL and Windows device names, because those are never
    legitimate in an existing directory name either.
    """
    if name is None:
        _problem("SLUG_EMPTY", "имя курса не задано")
    text = str(name)
    if not text or not text.strip():
        _problem("SLUG_EMPTY", "имя курса не может быть пустым")
    if "\x00" in text:
        _problem("SLUG_NUL", "имя курса содержит нулевой байт")
    if any(ch in _BAD_CHARS for ch in text):
        _problem("SLUG_SEPARATOR", "имя курса содержит разделитель пути: %r" % text)
    if any(ord(ch) < 32 for ch in text):
        _problem("SLUG_CONTROL", "имя курса содержит управляющий символ")
    if text in (".", ".."):
        _problem("SLUG_TRAVERSAL", "имя курса не может быть %r" % text)
    if os.path.isabs(text):
        _problem("SLUG_ABSOLUTE", "имя курса не может быть абсолютным путём")
    # Windows drive-relative forms (`C:foo`) and UNC prefixes.
    if len(text) >= 2 and text[1] == ":":
        _problem("SLUG_DRIVE", "имя курса не может содержать букву диска")
    if text.startswith("\\\\"):
        _problem("SLUG_UNC", "имя курса не может быть сетевым путём")
    if text != text.strip():
        _problem("SLUG_WHITESPACE", "имя курса не должно начинаться или кончаться пробелом")
    if text.endswith((".", " ")):
        _problem("SLUG_TRAILING", "имя курса не должно кончаться точкой или пробелом")
    stem = text.split(".", 1)[0].lower()
    if stem in _WINDOWS_RESERVED:
        _problem("SLUG_RESERVED", "имя курса зарезервировано в Windows: %r" % text)

    if SLUG_RE.match(text):
        return text
    if allow_legacy:
        return text
    _problem(
        "SLUG_INVALID",
        "имя курса %r недопустимо: используйте строчные латинские буквы, "
        "цифры, точку, дефис или подчёркивание (до 63 символов)" % text,
    )


def ensure_within(root, candidate, *, must_exist=False):
    """Resolve `candidate` and require it to stay under `root`.

    Symlinks are resolved, so a link inside the workspace that points outside it
    is refused rather than silently followed. For a path that does not exist
    yet, the nearest existing ancestor is resolved first so the check still
    applies before creation.
    """
    root = Path(root).expanduser().resolve()
    candidate = Path(candidate).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    probe = candidate
    suffix = []
    while not probe.exists() and probe != probe.parent:
        suffix.append(probe.name)
        probe = probe.parent
    try:
        resolved_existing = probe.resolve()
    except OSError as e:  # pragma: no cover - depends on the filesystem
        _problem("PATH_UNRESOLVABLE", "не удалось разрешить путь %s: %s" % (candidate, e))
    resolved = resolved_existing.joinpath(*reversed(suffix)) if suffix else resolved_existing

    if resolved != root and root not in resolved.parents:
        _problem(
            "PATH_OUTSIDE_SCOPE",
            "путь %s выходит за пределы рабочего пространства %s" % (resolved, root),
        )
    if must_exist and not resolved.exists():
        _problem("PATH_MISSING", "путь не существует: %s" % resolved)
    return resolved


def courses_dir(root):
    """The workspace `courses/` directory, contained under root."""
    return ensure_within(root, "courses")


def course_path(root, slug, *, must_exist=False):
    """Resolve `courses/<slug>` with the slug validated and contained."""
    safe = validate_slug(slug, allow_legacy=True)
    base = courses_dir(root)
    return ensure_within(base, base / safe, must_exist=must_exist)


def progress_path(root, slug):
    """Resolve `progress/<slug>.md`, contained under root."""
    safe = validate_slug(slug, allow_legacy=True)
    base = Path(root) / "progress"
    return ensure_within(base, base / (safe + ".md"))


def artifact_path(root, relative):
    """Resolve a path inside a botai-managed runtime directory.

    Used by anything that stores generated files (`dist/`, `.botai/`). The
    caller names a directory, never a full free-form path.
    """
    base = Path(root)
    target = Path(relative)
    if target.is_absolute() or ".." in target.parts:
        _problem("PATH_TRAVERSAL", "недопустимый относительный путь: %r" % relative)
    return ensure_within(base, base / target)
