# -*- coding: utf-8 -*-
"""Normalise an incoming skill before it can influence a session.

Where this comes from
---------------------
The approach is taken from HKUDS/DeepTutor (Apache-2.0), README section "The
import safety gate", which normalises frontmatter on every import and strips
`always:` so that "a downloaded skill can never force itself into every system
prompt". Their gate also strips executable bits, rejects traversal, and records
provenance in `.hub-lock.json`.

Why botai needs it
------------------
botai ships skills under `.agents/skills/` and states in `AGENTS.md` that every
SKILL.md is subordinate to the policy. That is a **textual** arrangement: the
files that exist today are all clean, but nothing checks them, and nothing would
stop an imported one from carrying a field that promotes itself or a body that
tells the agent to ignore the rules above it.

The gap is real and specific. A skill is loaded into a model's context; a field
that makes it load *always* changes what the model reads on every turn, and a
skill that says "the policy above does not apply" is exactly the injection the
policy's own §16 names as untrusted input.

What this does and does not do — stated plainly
-----------------------------------------------
It **removes self-promoting fields**, **rejects unsafe names**, **normalises the
frontmatter to the fields botai defines**, and **records provenance**.

It does **not** judge whether a skill's prose is good, safe, or honest. A skill
whose body says something harmful in ordinary sentences passes this check
untouched. `vetting-educational-material` remains the procedure for that, and a
human reads the result. Calling this a security boundary would be false.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Fields botai's own skills use. Anything else is dropped rather than carried
# into a session, so an unfamiliar key cannot acquire meaning by being present.
ALLOWED_FIELDS = ("name", "description", "verified")

# Fields whose presence changes *when* a skill loads, or grants it standing it
# was not given. Stripped, and the removal is reported rather than silent.
SELF_PROMOTING_FIELDS = (
    "always", "alwaysapply", "always_apply", "alwaysapplyfor",
    "auto", "autoload", "autoloaded", "inject", "injected",
    "global", "globalrules", "system", "systemprompt", "system_prompt",
    "priority", "override", "overrides", "force", "forced",
    "pin", "pinned", "persistent", "preload", "preloaded",
    "require", "required", "mandatory",
)

# A skill name becomes a directory name, so it is a NAME, not a path. The rules
# mirror `paths.py`: no separators, no traversal, no absolute or device names,
# and no edge hyphen — a name that starts or ends with `-` is ambiguous when it
# is also used as a flag or a path fragment.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

MAX_SKILL_BYTES = 256 * 1024
MAX_DESCRIPTION_CHARS = 2000

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S | re.DOTALL)


class SkillRejected(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def _parse_frontmatter(text):
    """Return (fields, body). A file with no frontmatter yields ({}, text).

    Parsed by hand rather than with a YAML dependency: the core must stay
    stdlib-only, and the shape here is a flat `key: value` block.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields, text[match.end():]


def _render(fields, body):
    lines = ["---"]
    for key in ALLOWED_FIELDS:
        if key in fields:
            lines.append("%s: %s" % (key, fields[key]))
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.lstrip("\n")


def normalise_skill(text, *, source=None):
    """Clean one SKILL.md. Returns a report; raises only for unusable input.

    The return value names every field that was removed, because a silent strip
    is indistinguishable from a skill that never carried the field — and a
    reader deciding whether to trust the import needs to know which it was.
    """
    if not isinstance(text, str) or not text.strip():
        raise SkillRejected("SKILL_EMPTY", "файл навыка пуст")
    if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
        raise SkillRejected(
            "SKILL_TOO_LARGE",
            "навык больше %d КиБ" % (MAX_SKILL_BYTES // 1024))

    fields, body = _parse_frontmatter(text)

    name = (fields.get("name") or "").strip()
    if not name:
        raise SkillRejected("SKILL_NAME_MISSING",
                            "во фронтматтере нет поля name")
    if not _NAME_RE.match(name):
        raise SkillRejected(
            "SKILL_NAME_INVALID",
            "имя %r недопустимо: только строчные латинские буквы, цифры и "
            "дефис, до 63 символов, без разделителей пути" % name)

    description = (fields.get("description") or "").strip()
    if not description:
        raise SkillRejected("SKILL_DESCRIPTION_MISSING",
                            "во фронтматтере нет поля description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillRejected("SKILL_DESCRIPTION_TOO_LONG",
                            "описание длиннее %d символов"
                            % MAX_DESCRIPTION_CHARS)

    # `verified:` is normalised to a date, and left empty when it is not one.
    # The policy makes a stale date a reason to re-check a skill, so a
    # malformed value must not pass as a valid one.
    verified = (fields.get("verified") or "").strip()
    if verified and not re.match(r"^\d{4}-\d{2}-\d{2}$", verified):
        verified = ""

    removed = []
    for key in fields:
        if key in SELF_PROMOTING_FIELDS:
            removed.append({"field": key, "reason": "self_promoting"})
        elif key not in ALLOWED_FIELDS:
            removed.append({"field": key, "reason": "unknown_field"})

    clean = {"name": name, "description": description}
    if verified:
        clean["verified"] = verified

    rendered = _render(clean, body)
    return {
        "ok": True,
        "name": name,
        "text": rendered,
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "source": source,
        "removed_fields": removed,
        "verified": verified or None,
        "note_ru": (
            "Поля, повышающие навык в правах, удалены: они меняют не "
            "содержание, а то, когда навык попадает в контекст."
            if removed else "Фронтматтер уже соответствует формату botai."
        ),
    }


def provenance_entry(report, *, hub=None, version=None, installed_at=None,
                     verdict=None):
    """The record that makes an import auditable later.

    Mirrors DeepTutor's `.hub-lock.json`: without it, a skill in the workspace
    cannot be traced back to where it came from, and a later reviewer has
    nothing to check against.
    """
    return {
        "name": report["name"],
        "sha256": report["sha256"],
        "source": report.get("source"),
        "hub": hub,
        "version": version,
        "installed_at": installed_at,
        "verdict": verdict,
        "removed_fields": report.get("removed_fields") or [],
    }


def check_skill_tree(root):
    """Report every skill under `root` that would be changed by a normalise.

    Used as an audit of what is already installed rather than of an import: the
    shipped skills pass untouched, so a non-empty result means something entered
    the tree by another route.
    """
    root = Path(root)
    findings = []
    for path in sorted(root.rglob("SKILL.md")):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as error:
            findings.append({"path": str(path), "code": "UNREADABLE",
                             "message_ru": str(error)})
            continue
        try:
            report = normalise_skill(text, source=str(path))
        except SkillRejected as error:
            findings.append({"path": str(path), "code": error.code,
                             "message_ru": error.message})
            continue
        if report["removed_fields"]:
            findings.append({
                "path": str(path),
                "code": "FIELDS_WOULD_BE_STRIPPED",
                "message_ru": "будут сняты поля: %s"
                              % ", ".join(f["field"] for f in report["removed_fields"]),
            })
    return findings
