# -*- coding: utf-8 -*-
"""Defence-in-depth: injection sanitising, rate limiting, secrets, audit, RBAC.

Design §9.4 names five gaps: prompt injection, no rate limiting, tokens in
plaintext, no audit log, no roles. This module implements the local, offline
part of each one — and **every one of these controls is advisory**. The real
guarantees in this repository live somewhere else, and it would be dishonest to
blur the line:

* what a learner may do is decided by the **accepted course contract** and
  `policy.py`, which a human accepted;
* what the model may do is decided by the absence of privileged tools in
  `mcp_server.py` — a model that cannot call a network tool cannot exfiltrate;
* nothing in this module can un-send a prompt that already reached a provider.

So read it as what it is: a set of guards that make the *bad* path noisy, cheap
to detect and hard to script, plus honest reporting for `doctor`.

* `sanitize_user_input` neutralises known injection markers **and reports them**
  (`removed`), because a silent deletion is indistinguishable from a bug and an
  injection the model never sees is an injection that can still be shaped by
  what surrounds it. It is not a guarantee — novel phrasings pass through.
* `RateLimiter` bounds requests per student (design: 100/hour) in memory, for a
  single process. It is not a distributed limiter and does not survive a restart.
* `redact_secrets` masks known credential shapes; `assert_no_secrets` refuses to
  let a document containing one be stored or exported. Both are pattern-based:
  a secret in an unknown format is not caught.
* `record_audit` / `read_audit` append and parse a JSONL log. A corrupt line is
  skipped with a warning — a damaged log must not take the tool down.
* `PERMISSIONS` encodes the repository's central invariant: **a model cannot
  mint human consent**. `student` therefore has no `accept_course`,
  `install_environment`, `export_privacy` or `admin` — those belong to a human,
  and giving them to the lowest role would make the whole consent gate
  advisory.
"""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from . import tutoring

SCHEMA_VERSION = 2

# Design §9.4: 100 requests per hour per student.
DEFAULT_MAX_REQUESTS = 100
DEFAULT_WINDOW_SECONDS = 3600

AUDIT_FILENAME = "audit.jsonl"


class SecurityError(RuntimeError):
    """A refused security operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class RateLimitError(SecurityError):
    """Raised by `RateLimiter.require` only; `check` never raises."""


# ---------------------------------------------------------------------------
# Prompt-injection defence (sanitising, and reporting what was sanitised)
# ---------------------------------------------------------------------------

# Each entry: (kind, compiled pattern, replacement). Matching is case-insensitive
# where the marker is language-specific; the replacement is an explicit
# quarantine tag so the text keeps its shape and the model can SEE that
# something was removed.
_INJECTION_MARKERS = (
    (
        "instruction_override_ru",
        re.compile(r"забудь\s+(все\s+)?предыдущие\s+инструкции", re.I),
        "[САНИТИЗАЦИЯ: попытка отменить инструкции]",
    ),
    (
        "instruction_override_ru",
        re.compile(r"игнорируй\s+(все\s+)?предыдущие\s+инструкции", re.I),
        "[САНИТИЗАЦИЯ: попытка отменить инструкции]",
    ),
    (
        "instruction_override_en",
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
        "[SANITIZED: instruction-override attempt]",
    ),
    (
        "role_header_system",
        re.compile(r"(?im)^[ \t]*(system|система)[ \t]*:"),
        "[САНИТИЗАЦИЯ: попытка выдать себя за системный блок]",
    ),
    (
        "role_header_chatml",
        re.compile(r"<\|im_(start|end)\|>", re.I),
        "[САНИТИЗАЦИЯ: маркер чат-шаблона]",
    ),
    (
        "role_header_markdown",
        re.compile(r"(?im)^[ \t]*#{2,}[ \t]*(system|система|developer|разработчик)\b"),
        "[САНИТИЗАЦИЯ: роль в заголовке раздела]",
    ),
    (
        "fake_system_block",
        re.compile(r"(?i)begin\s+system\b"),
        "[САНИТИЗАЦИЯ: поддельный системный блок]",
    ),
    (
        "fake_tool_call",
        re.compile(
            r"(?i)(<\|?(tool_call|function_call)\|?>|\[?(TOOL_CALL|FUNCTION_CALL)\]?\s*[:{])"
        ),
        "[САНИТИЗАЦИЯ: поддельный вызов инструмента]",
    ),
    (
        "policy_override",
        re.compile(
            r"(?i)(ignore|забудь|отмени)\s+(весь\s+|всю\s+)?"
            r"(grading|оценивани|policy|политик|помощ)",
        ),
        "[САНИТИЗАЦИЯ: попытка изменить правила оценивания/помощи]",
    ),
)


def sanitize_user_input(text, *, max_chars=8192):
    """Neutralise known injection markers in untrusted input.

    Returns `{"text": ..., "removed": [{"kind", "snippet"}], "truncated": bool}`.

    Nothing is deleted silently: each match is replaced with an explicit tag and
    recorded in `removed`, so the model (and the audit log) can see that an
    injection was attempted rather than meeting a text that quietly changed.
    This is **defence in depth, not a guarantee** — a novel phrasing, or an
    injection aimed at a different layer, is not in the marker table.
    """
    raw = "" if text is None else str(text)
    truncated = False
    if len(raw) > max_chars:
        raw = raw[:max_chars]
        truncated = True

    removed = []
    cleaned = raw
    for kind, pattern, replacement in _INJECTION_MARKERS:
        matches = list(pattern.finditer(cleaned))
        if not matches:
            continue
        for match in matches[:10]:
            removed.append(
                {
                    "kind": kind,
                    "snippet": match.group(0)[:120],
                }
            )
        cleaned = pattern.sub(replacement, cleaned)

    return {"text": cleaned, "removed": removed, "truncated": truncated}


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per process)
# ---------------------------------------------------------------------------


class RateLimiter:
    """A sliding-window request counter keyed by student id.

    **In-memory only.** One process, lost on restart, and not shared between
    processes — this bounds a runaway loop or a script hammering a single
    student's session, which is the failure it was designed for. It is not a
    substitute for a server-side limiter.
    """

    def __init__(
        self,
        *,
        max_requests=DEFAULT_MAX_REQUESTS,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        clock=None,
    ):
        self.max_requests = int(max_requests)
        self.window_seconds = int(window_seconds)
        self._clock = clock
        self._hits: dict = {}

    def _now(self):
        moment = self._clock() if self._clock else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment

    def check(self, key):
        """`{"allowed", "remaining", "retry_after"}` for `key`. Never raises."""
        now = self._now()
        window_start = now.timestamp() - self.window_seconds
        bucket = self._hits.setdefault(str(key), deque())
        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= self.max_requests:
            retry_after = max(1, int(bucket[0] + self.window_seconds - now.timestamp()))
            return {"allowed": False, "remaining": 0, "retry_after": retry_after}

        bucket.append(now.timestamp())
        return {
            "allowed": True,
            "remaining": self.max_requests - len(bucket),
            "retry_after": 0,
        }

    def require(self, key):
        """`check`, but a refusal raises `RateLimitError`.

        The raise-on-refusal form is separate on purpose: most callers want the
        decision (a message, a metric, a skipped feature), and only the gate
        itself needs an exception.
        """
        result = self.check(key)
        if not result["allowed"]:
            raise RateLimitError(
                "RATE_LIMITED",
                "превышен лимит запросов (максимум %d за %d с на ученика). "
                "Повторите через %d с. Это ограничение нагрузки, а не оценка "
                "работы."
                % (self.max_requests, self.window_seconds, result["retry_after"]),
            )
        return result


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

# (kind, pattern). Ordered: the more specific GitHub shapes first, because
# `github_pat_` also starts with a token-ish prefix.
_SECRET_PATTERNS = (
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("huggingface_token", re.compile(r"hf_[A-Za-z0-9]{20,}")),
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    (
        "bearer_header",
        re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/-]{8,}=*"),
    ),
)


def redact_secrets(text):
    """Replace credential-shaped substrings with `[REDACTED:<kind>]`."""
    out = "" if text is None else str(text)
    for kind, pattern in _SECRET_PATTERNS:
        if kind == "bearer_header":
            out = pattern.sub(lambda m: "%s[REDACTED:%s]" % (m.group(1), kind), out)
        else:
            out = pattern.sub("[REDACTED:%s]" % kind, out)
    return out


def find_secrets(text):
    """Every secret-shaped match in `text`, as `{"kind", "snippet"}`."""
    found = []
    raw = "" if text is None else str(text)
    for kind, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(raw):
            found.append({"kind": kind, "snippet": match.group(0)[:12] + "…"})
    return found


def assert_no_secrets(document):
    """Refuse a document about to be stored or exported if it holds a secret.

    A guard on the export edge, not a scanner: pattern-based, so a credential
    in an unrecognised format passes. The refusal is deliberate — an export
    that has already left cannot be taken back.
    """
    if isinstance(document, (dict, list)):
        blob = json.dumps(document, ensure_ascii=False, default=str)
    else:
        blob = str(document or "")
    found = find_secrets(blob)
    if found:
        raise SecurityError(
            "SECRET_IN_DOCUMENT",
            "в документе обнаружены секреты (%s): экспорт заблокирован. "
            "Уберите токены из документа или замените их переменными окружения."
            % ", ".join(sorted({f["kind"] for f in found})),
        )
    return True


# ---------------------------------------------------------------------------
# Audit log (JSONL, append-only, tolerant reader)
# ---------------------------------------------------------------------------


def audit_log_path(root):
    """`<root>/.botai/logs/audit.jsonl` — the append-only audit log."""
    return Path(root) / ".botai" / "logs" / AUDIT_FILENAME


def record_audit(root, *, actor, action, subject, outcome, detail=None, clock=None):
    """Append one JSON object per line to the audit log. Returns the path.

    The log is append-only and per-event: one damaged line costs one record,
    not the file. `detail` is recorded verbatim but is **not** scanned for
    secrets here — a caller storing a secret in the audit log is a caller bug;
    the guard for *exports* is `assert_no_secrets`.
    """
    path = audit_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": SCHEMA_VERSION,
        "at": tutoring.now_iso(clock),
        "actor": actor,
        "action": action,
        "subject": subject,
        "outcome": outcome,
    }
    if detail is not None:
        entry["detail"] = detail
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def read_audit(root, limit=100):
    """The most recent `limit` audit entries, newest last.

    Returns `{"entries": [...], "warnings": [...]}`. A corrupt line is skipped
    with a warning: a half-written log from an interrupted process must not
    make the whole tool unusable.
    """
    path = audit_log_path(root)
    if not path.is_file():
        return {"entries": [], "warnings": []}
    entries = []
    warnings = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return {"entries": [], "warnings": ["не удалось прочитать журнал: %s" % e]}

    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            warnings.append("строка %d журнала повреждена и пропущена" % number)
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
        else:
            warnings.append("строка %d журнала не является объектом" % number)

    if limit is not None and limit >= 0 and len(entries) > limit:
        entries = entries[-limit:]
    return {"entries": entries, "warnings": warnings}


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

ROLES = ("student", "teacher", "admin")

CAPABILITIES = (
    "teach",
    "review",
    "import_packet",
    "export_privacy",
    "accept_course",
    "install_environment",
    "admin",
)

# The `student` role deliberately lacks `accept_course`, `install_environment`,
# `export_privacy` and `admin`. Accepting a course is a human act (it creates
# the consent the whole model trusts); installing an environment changes the
# learner's machine; exporting privacy data leaves the workspace; `admin` is
# the union. A model given any of these could mint the consent it is supposed
# to respect — the repository's central invariant.
PERMISSIONS = {
    "student": frozenset({"teach"}),
    "teacher": frozenset(
        {
            "teach",
            "review",
            "import_packet",
            "export_privacy",
            "accept_course",
            "install_environment",
        }
    ),
    "admin": frozenset(CAPABILITIES),
}


def check_permission(role, capability):
    """Whether `role` holds `capability`. Unknown role or capability → False."""
    granted = PERMISSIONS.get(str(role))
    if granted is None:
        return False
    return str(capability) in granted


def require_permission(role, capability):
    """`check_permission`, raising `PERMISSION_DENIED` when it is False."""
    if not check_permission(role, capability):
        raise SecurityError(
            "PERMISSION_DENIED",
            "роль %r не имеет права %r. Роль ученика намеренно не может "
            "принять курс, установить окружение, выгрузить приватные данные "
            "или стать администратором: принятие курса — человеческое действие, "
            "и модель не может выдать согласие за человека." % (role, capability),
        )
    return True


def describe_permissions():
    """A `doctor`-friendly report of the role table."""
    return {
        "roles": list(ROLES),
        "capabilities": list(CAPABILITIES),
        "matrix": {role: sorted(caps) for role, caps in PERMISSIONS.items()},
        "note_ru": (
            "Таблица прав — защита в глубину. Реальные гарантии дают принятый "
            "контракт курса, policy.py и отсутствие привилегированных "
            "инструментов у модели."
        ),
    }
