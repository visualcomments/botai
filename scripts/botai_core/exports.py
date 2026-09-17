# -*- coding: utf-8 -*-
"""Export and deletion: the learner's data leaves only when they choose.

Two operations live here, and they are the ones where a mistake is not
recoverable by re-running something:

* **Export** builds the file a learner may hand to a teacher. The shape of that
  file is the privacy boundary — raw chat, full submissions and contact details
  are not fields, so they cannot be included by accident, only by a deliberate
  schema change. The learner sees the exact contents *and* the recipient before
  the file exists, and export requires recorded consent for the purpose.
* **Deletion** is planned before it happens. `deletion_plan` lists exactly which
  database rows, artifacts, projections and backups would go, and the caller
  confirms. The journal is append-only in normal operation but explicitly does
  not stand in the way of deleting personal data — a record that cannot be
  corrected is worse than a gap in a log.

What this module refuses to claim: a checksum detects corruption, not identity.
A packet produced on a student's own machine can say anything, and the importer
treats it as untrusted input — it does not execute Markdown, follow archive
links, or obey instructions found in a question field.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from . import paths
from . import schemas

SCHEMA_VERSION = 2

PACKET_KIND = "teacher_packet"
RECEIPT_KIND = "export_receipt"

# Purposes that must be granted before data may leave. Kept explicit so a caller
# cannot export by naming a purpose nobody recorded.
REQUIRED_PURPOSE = "teacher_export"

MAX_PACKET_BYTES = 20 * 1024 * 1024
MAX_EXCERPT = 2000

# Categories a question or issue may carry. The list is closed so the queue can
# group reliably and a model cannot invent a category that skips triage.
CATEGORIES = ("environment", "source_missing", "policy_unknown", "misconception",
              "course_gap", "review_requested")


class ExportError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def new_id():
    return str(uuid.uuid4())


def canonical(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def packet_checksum(packet):
    """SHA-256 over the packet without its checksum field."""
    body = {key: value for key, value in packet.items() if key != "checksum"}
    return hashlib.sha256(canonical(body)).hexdigest()


def find_consent(store, course_id, purpose=REQUIRED_PURPOSE):
    """A live consent granting `purpose`, or None.

    Withdrawn consent does not count. This is checked at export time rather than
    assumed from the fact that an export was possible before.
    """
    for body in store.list_entities("consent", course_id=course_id):
        if body.get("withdrawn_at"):
            continue
        if purpose in (body.get("purposes") or []):
            return body
    return None


# --------------------------------------------------------------------------
# Export preview
# --------------------------------------------------------------------------

def preview(store, course, *, course_revision, selection, objective_ids=(),
            include_evidence=False, include_questions=False,
            include_environment=False, text_excerpts=False, recipient_label=None):
    """Exactly what a packet would contain, before anything is written.

    Returns a structure a human reads: the fields, the counts, and what is
    deliberately absent. The learner approves *this*, not a description of it.
    """
    consent = find_consent(store, course.course_id)
    states = store.list_entities("objective_state", course_id=course.course_id)
    checks = store.list_entities("check", course_id=course.course_id)
    attempts = store.list_entities("attempt", course_id=course.course_id)
    tickets = store.list_entities("teacher_ticket", course_id=course.course_id)

    # An empty selection means *nothing*, not everything. Treating it as "no
    # filter" would export a learner's whole record the moment they cleared the
    # list of objectives — the opposite of what an empty choice says.
    if not objective_ids:
        states, checks, attempts, tickets = [], [], [], []

    wanted = set(objective_ids or [])
    included_states = [s for s in states if s.get("objective_id") in wanted]
    questions = [
        t for t in tickets
        if t.get("status") in ("new", "triaged", "draft_ready")
        and t.get("category") in CATEGORIES
    ]

    excerpt_count = 0
    if text_excerpts and include_evidence:
        excerpt_count = sum(1 for a in attempts if a.get("text_excerpt"))

    return {
        "schema_version": SCHEMA_VERSION,
        "course_id": course.course_id,
        "course_revision": dict(course_revision),
        "recipient_label": recipient_label or "(получатель не указан)",
        "consent": {
            "found": consent is not None,
            "consent_id": (consent or {}).get("consent_id"),
            "purpose": REQUIRED_PURPOSE,
            "message_ru": ("согласие на передачу преподавателю записано"
                           if consent else
                           "согласие на передачу преподавателю НЕ записано: "
                           "экспорт невозможен"),
        },
        "selection": {
            "objective_ids": sorted(wanted) if wanted else [],
            "include_evidence": bool(include_evidence),
            "include_questions": bool(include_questions),
            "include_environment": bool(include_environment),
            "text_excerpts": bool(text_excerpts),
        },
        "will_include": {
            "objective_states": [s.get("objective_id") for s in included_states],
            "evidence_items": (len(checks) + len(attempts)) if include_evidence else 0,
            "text_excerpts": excerpt_count,
            "questions": len(questions) if include_questions else 0,
            "environment_issues": 0 if not include_environment else "из журнала операций",
        },
        "never_included": [
            "полные тексты попыток (только короткие выдержки и лишь по отдельному выбору)",
            "сырой чат и рассуждения модели",
            "имя, контакты и любые прямые идентификаторы личности",
            "секреты, токены и переменные окружения",
            "работы других обучающихся",
            "материалы под исключёнными корнями курса",
        ],
        "note_ru": "Пакет создаётся как локальный файл. Отправку выполняет "
                   "человек: автоматической рассылки в 2.0 нет. Контрольная "
                   "сумма выявляет повреждение и не удостоверяет личность.",
    }


def build_packet(store, course, *, course_revision, selection, objective_ids=(),
                 include_evidence=False, include_questions=False,
                 include_environment=False, text_excerpts=False,
                 generated_at, tool_version=None):
    """Assemble a `LearningPacket`.

    Refuses without recorded consent for the export purpose: the design makes
    consent a precondition, not a notification sent afterwards.
    """
    consent = find_consent(store, course.course_id)
    if consent is None:
        raise ExportError(
            "CONSENT_REQUIRED",
            "нет записанного согласия на передачу данных преподавателю: "
            "экспорт не создаётся",
        )

    # An empty selection exports nothing. It is an explicit choice — "I am not
    # disclosing any objective" — and must not be read as "no filter applies".
    if not objective_ids:
        states, checks, attempts, tickets = [], [], [], []
    wanted = set(objective_ids or [])
    states = [s for s in store.list_entities("objective_state", course_id=course.course_id)
              if s.get("objective_id") in wanted]
    checks = [c for c in store.list_entities("check", course_id=course.course_id)
              if c.get("objective_id") in wanted]
    attempts = [a for a in store.list_entities("attempt", course_id=course.course_id)
                if a.get("objective_id") in wanted]
    tickets = [t for t in store.list_entities("teacher_ticket", course_id=course.course_id)
               if t.get("objective_id") in wanted]

    objective_states = []
    for state in states:
        latest = next((c for c in checks
                       if c.get("check_id") == state.get("latest_check_id")), None)
        objective_states.append({
            "objective_id": state["objective_id"],
            "stage": state.get("stage", "new"),
            "reliability": (latest or {}).get("reliability"),
            "assistance_max": state.get("assistance_max"),
            "note_ru": None,
        })

    selected_evidence = []
    if include_evidence:
        for check in checks:
            selected_evidence.append({
                "evidence_id": check.get("check_id"),
                "kind": "check",
                "excerpt": None,
                "verdict": check.get("verdict"),
            })
        if text_excerpts:
            for attempt in attempts:
                text = attempt.get("text_excerpt")
                if not text:
                    continue
                selected_evidence.append({
                    "evidence_id": attempt.get("attempt_id"),
                    "kind": "attempt",
                    "excerpt": text[:MAX_EXCERPT],
                    "verdict": None,
                })

    questions = []
    if include_questions:
        for ticket in tickets:
            if ticket.get("category") not in CATEGORIES:
                continue
            if ticket.get("status") not in ("new", "triaged", "draft_ready"):
                continue
            questions.append({
                "question_id": ticket.get("ticket_id") or new_id(),
                "text_ru": (ticket.get("question") or ticket.get("summary_ru") or "")[:2000],
                "category": ticket["category"],
                "objective_id": ticket.get("objective_id"),
            })

    packet = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": new_id(),
        "pseudonymous_learner_id": store.learner_id,
        "course_id": course.course_id,
        "course_revision": dict(course_revision),
        "generated_at": generated_at,
        "consent_id": consent.get("consent_id"),
        "selection": {
            "objective_ids": sorted(wanted) if wanted else [],
            "include_evidence": bool(include_evidence),
            "include_questions": bool(include_questions),
            "include_environment": bool(include_environment),
            "text_excerpts": bool(text_excerpts),
        },
        "objective_states": objective_states,
        "selected_evidence": selected_evidence,
        "questions": questions,
        "environment_issues": [] if not include_environment else _environment_issues(store),
        "ai_assistance_summary": _assistance_summary(store, course.course_id),
        "provenance": {
            "source": "student_workspace",
            "exported_by": "learner_cli",
            "tool_version": tool_version,
        },
    }
    packet["checksum"] = packet_checksum(packet)

    schemas.validate(packet, "packet")
    serialized = json.dumps(packet, ensure_ascii=False, indent=2)
    if len(serialized.encode("utf-8")) > MAX_PACKET_BYTES:
        raise ExportError(
            "PACKET_TOO_LARGE",
            "пакет больше %d МиБ: уменьшите выбор или выдержки, а не обрезайте "
            "молча" % (MAX_PACKET_BYTES // (1024 * 1024)),
        )
    return packet


def _environment_issues(store):
    """Environment problems from recorded operations, summarised.

    Operation ids replace raw logs: a teacher needs to know a step failed and
    what it was, not to receive a console dump.
    """
    issues = []
    for operation in store.list_entities("operation"):
        if operation.get("status") not in ("FAILED", "PARTIAL", "STALE_PLAN"):
            continue
        issues.append({
            "issue_id": operation["operation_id"],
            "summary_ru": "операция подготовки среды завершилась состоянием %s"
                          % operation["status"],
            "check_id": None,
        })
    return issues


def _assistance_summary(store, course_id):
    """How much help was received, per level — not what was said."""
    counts = {}
    for event in store.events(course_id=course_id):
        if event.get("event_type") != "assistance.recorded":
            continue
        level = (event.get("payload") or {}).get("level")
        if level:
            counts[level] = counts.get(level, 0) + 1
    if not counts:
        return None
    return {
        "levels_used": sorted(counts),
        "counts": counts,
        "note_ru": "Это уровни помощи, а не расшифровка занятия: содержание "
                   "разговора в пакет не входит.",
    }


def write_packet(packet, directory, *, filename=None):
    """Write a packet to a local file. Never uploads anything."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    name = filename or ("packet-%s.json" % packet["packet_id"][:8])
    target = paths.ensure_within(directory, directory / name)
    target.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    return target


def verify_packet(document):
    """Whether an imported packet is intact.

    Returns `(ok, problems)`. An intact checksum means the file is undamaged; it
    says nothing about whether the contents are true, and this function does not
    claim otherwise.
    """
    problems = []
    if not isinstance(document, dict):
        return False, [{"code": "PACKET_NOT_OBJECT",
                        "message_ru": "пакет должен быть JSON-объектом"}]

    declared = document.get("checksum")
    actual = packet_checksum(document)
    if declared != actual:
        problems.append({
            "code": "PACKET_CHECKSUM_MISMATCH",
            "message_ru": "контрольная сумма не совпала: файл повреждён или изменён",
            "detail": {"declared": declared, "actual": actual},
        })

    if document.get("provenance", {}).get("source") != "student_workspace":
        problems.append({"code": "PACKET_UNKNOWN_SOURCE",
                         "message_ru": "неизвестный источник пакета"})

    return (not problems), problems


# --------------------------------------------------------------------------
# Deletion
# --------------------------------------------------------------------------

def deletion_plan(store, *, course_id=None, learner_id=None):
    """What deleting this learner's data would remove.

    Listed exhaustively before anything is deleted, because "delete my data" and
    "delete the rows you happened to think of" are different promises. Artifacts
    are counted, and shared bytes another learner still points at are marked as
    kept so the plan does not overstate what will be removed either.
    """
    scope = learner_id or store.learner_id
    course = course_id

    def entities(kind):
        items = store.list_entities(kind, course_id=course)
        return items

    counts = {}
    for kind in ("session", "objective_state", "attempt", "check", "consent",
                 "presentation", "achievement", "contribution", "teacher_ticket",
                 "legacy_import", "legacy_objective_claim"):
        counts[kind] = len(entities(kind))

    events = [e for e in store.events(course_id=course)]
    artifacts = store.count("artifacts")
    refs = store.count("artifact_refs")

    # Bytes are content-addressed and may be shared; deleting a reference must
    # not remove bytes another learner's evidence still needs.
    shared = []
    for row in store._conn.execute(
            "SELECT sha256, COUNT(DISTINCT learner_id) AS owners FROM artifact_refs "
            "GROUP BY sha256 HAVING owners > 1"):
        shared.append({"sha256": row["sha256"][:16], "owners": row["owners"]})

    return {
        "schema_version": SCHEMA_VERSION,
        "learner_id": scope,
        "course_id": course,
        "would_delete": {
            "entities": {k: v for k, v in counts.items() if v},
            "events": len(events),
            "artifact_refs": refs,
        },
        "would_keep": {
            "shared_artifacts": shared,
            "reason_ru": "общие байты остаются: на них ссылаются другие "
                         "разрешённые записи",
        },
        "also_affected": [
            "проекции прогресса (Markdown пересобирается без этих записей)",
            "резервные копии состояния, если они были созданы",
            "ранее созданные экспортные пакеты в workspace (удаляются отдельно)",
        ],
        "not_promised": [
            "физическое стирание блоков на SSD",
            "удаление копий, уже переданных преподавателю или провайдеру модели",
        ],
        "note_ru": "Уже переданные данные не исчезают автоматически: нужен "
                   "отдельный запрос получателю. Журнал событий не препятствует "
                   "удалению персональных данных.",
    }


def apply_deletion(store, *, course_id=None, plan=None):
    """Delete the learner's records for a course, then report what went.

    The event journal is deleted too. It is append-only during normal work, and
    that property deliberately yields to a deletion request: a log that cannot
    be corrected is worse than a gap, and the alternative would be a record the
    learner cannot get rid of.
    """
    if plan is None:
        plan = deletion_plan(store, course_id=course_id)

    deleted = {}
    connection = store._conn
    course = course_id
    learner = store.learner_id

    connection.execute("BEGIN IMMEDIATE")
    try:
        for table in ("entities", "events"):
            if course:
                cursor = connection.execute(
                    "DELETE FROM %s WHERE learner_id=? AND course_id=?" % table,
                    (learner, course))
            else:
                cursor = connection.execute(
                    "DELETE FROM %s WHERE learner_id=?" % table, (learner,))
            deleted[table] = cursor.rowcount

        # Remove only references this learner owned, then any bytes no other
        # reference still points at. Bytes are content-addressed and may be
        # shared, so the reference goes first and the file only when nothing
        # else needs it.
        if course:
            cursor = connection.execute(
                "DELETE FROM artifact_refs WHERE learner_id=? AND course_id=?",
                (learner, course))
        else:
            cursor = connection.execute(
                "DELETE FROM artifact_refs WHERE learner_id=?", (learner,))
        deleted["artifact_refs"] = cursor.rowcount

        orphaned = [row["sha256"] for row in connection.execute(
            "SELECT sha256 FROM artifacts WHERE sha256 NOT IN "
            "(SELECT DISTINCT sha256 FROM artifact_refs)")]
        for digest in orphaned:
            connection.execute("DELETE FROM artifacts WHERE sha256=?", (digest,))
        deleted["artifacts"] = len(orphaned)

        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    # The files are removed only after the database agrees they are orphaned: a
    # file deleted while a row still referenced it would be a worse outcome than
    # a file left behind.
    directory = paths.artifact_store_dir(store.root)
    removed_files = 0
    for digest in orphaned:
        target = directory / digest
        try:
            if target.is_file():
                target.unlink()
                removed_files += 1
        except OSError:
            pass
    deleted["artifact_files"] = removed_files

    deleted.pop("plan", None)
    deleted["plan"] = plan
    return deleted


def deletion_notice(packet_id, *, learner_label, course_id, reason_ru):
    """A request a teacher can act on for data already handed over.

    Deleting locally cannot reach a copy someone else holds, so the honest
    deliverable is a request naming what was shared and why it should go.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "notice_id": new_id(),
        "packet_id": packet_id,
        "learner_label": learner_label,
        "course_id": course_id,
        "reason_ru": reason_ru,
        "requested_at": None,
        "note_ru": "Удаление в локальном workspace не отзывает уже переданные "
                   "копии: этот запрос нужно передать получателю вручную.",
    }
