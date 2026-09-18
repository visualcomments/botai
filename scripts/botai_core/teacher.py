# -*- coding: utf-8 -*-
"""The teacher's side: imported packets, a queue, and honest aggregation.

Three rules shape this module, and each one answers a way a summary lies:

* **A packet is untrusted input.** It arrives from a student's machine, so a
  `question` field containing instructions is text, not a command. The content
  is never executed, Markdown is never rendered as HTML, and archive links are
  never followed. The importer reads fields; it does not obey them.
* **A denominator is not the cohort.** "3 of 8 who shared data" is a fact;
  "37.5% of the group" is a claim about people who never participated. Each
  aggregate carries the count it was computed from, and small groups collapse
  rather than identifying an individual — that is a privacy heuristic, not
  mathematical anonymity, and the summary says so.
* **Ranking people is not an output.** The queue sorts by whether something
  blocks a lesson and how many independent packets report it. There is no
  "weakest students" list anywhere, and no per-person ordering.

The teacher workspace is separate by construction: it receives packets, never
reads a student's directory. Directory separation is not authentication between
people sharing one operating system — that needs separate accounts, and the
documentation says so rather than implying the tool provides it.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths
from . import schemas
from . import claims

SCHEMA_VERSION = 2

PACKET_KIND = "teacher_packet"
TICKET_KIND = "teacher_ticket"
ISSUE_KIND = "course_issue_draft"

# Below this many contributing packets, an aggregate is withheld: with two
# respondents a "recurring difficulty" is one person's difficulty.
MIN_GROUP_FOR_BREAKDOWN = 5

# Queue categories, in the order a teacher would triage them. `environment`
# first because a broken setup blocks the lesson itself.
CATEGORY_ORDER = ("environment", "source_missing", "policy_unknown",
                  "misconception", "course_gap", "review_requested")

TICKET_STATUSES = ("new", "triaged", "draft_ready", "resolved", "dismissed")


class TeacherError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def import_packet(store, document, *, source_label=None):
    """Validate and store a packet from a learner.

    Refuses a damaged packet, a packet whose id already exists with different
    content, and anything that is not recognisably a packet. The stored record
    keeps the original bytes' checksum so a later import can tell a re-send from
    a different packet with a reused id.
    """
    from . import exports as exports_mod

    ok, problems = exports_mod.verify_packet(document)
    if not ok:
        raise TeacherError(
            "PACKET_INVALID",
            "; ".join(p["message_ru"] for p in problems),
            detail={"problems": problems},
        )

    schemas.validate(document, "packet")

    packet_id = document["packet_id"]
    existing, version = store.get(PACKET_KIND, packet_id,
                                  course_id=document["course_id"])
    if existing is not None:
        if existing.get("checksum") == document["checksum"]:
            return existing, version, True
        # One id, two contents: a conflict to resolve, never a quiet overwrite.
        raise TeacherError(
            "PACKET_ID_CONFLICT",
            "пакет %s уже импортирован с другим содержимым: это конфликт, "
            "а не повторная отправка" % packet_id[:8],
            detail={"existing_checksum": existing.get("checksum"),
                    "incoming_checksum": document["checksum"]},
        )

    record = dict(document)
    record["_source_label"] = source_label
    result, _ = store.apply(
        kind=PACKET_KIND, entity_id=packet_id,
        events=["teacher.packet_imported"], course_id=document["course_id"],
        new_body=record,
        event_payloads=[{
            "packet_id": packet_id,
            "checksum": document["checksum"],
            "source_label": source_label,
            "objective_states": len(document.get("objective_states") or []),
            "questions": len(document.get("questions") or []),
            "_actor": "teacher_cli",
            "_provenance": "human_input",
        }],
    )
    return record, result["entity"]["version"], False


def packets(store, course_id=None):
    return store.list_entities(PACKET_KIND,
                               course_id=course_id) if course_id else store.list_entities(PACKET_KIND)


def group_summary(store, course_id):
    """Aggregate imported packets, with the denominator stated.

    Every count is accompanied by how many packets it came from. When fewer than
    `MIN_GROUP_FOR_BREAKDOWN` packets have been shared, per-packet breakdowns are
    withheld: with a handful of respondents a "pattern" identifies a person.
    """
    imported = packets(store, course_id)
    total = len(imported)

    stage_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    objective_difficulty: dict[str, int] = {}
    environment_reporters = 0

    for packet in imported:
        for state in packet.get("objective_states") or []:
            stage = state.get("stage")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            # `blocked` and `review_due` are the signals a teacher can act on;
            # `demonstrated` is not a difficulty and is not counted as one.
            if stage in ("blocked",):
                objective = state.get("objective_id")
                objective_difficulty[objective] = objective_difficulty.get(objective, 0) + 1

        for question in packet.get("questions") or []:
            category = question.get("category")
            if category in CATEGORY_ORDER:
                category_counts[category] = category_counts.get(category, 0) + 1

        if packet.get("environment_issues"):
            environment_reporters += 1

    enough = total >= MIN_GROUP_FOR_BREAKDOWN

    summary = {
        "schema_version": SCHEMA_VERSION,
        "course_id": course_id,
        "packets_received": total,
        # The denominator is the packets that arrived, stated as such. It is not
        # the size of the cohort, which this workspace has no way to know.
        "denominator_ru": "%d из %d предоставивших данные" % (total, total),
        "cohort_size_known": False,
        "cohort_note_ru": "Размер группы неизвестен: знаменатель — только те, "
                          "кто передал пакет. Проценты от всей группы здесь "
                          "не считаются.",
        "objective_stages": {
            "counts": stage_counts,
            "from_packets": total,
        },
        "categories": {
            "counts": category_counts,
            "from_packets": total,
        },
        "environment_issues": {
            "reporting_packets": environment_reporters,
            "from_packets": total,
        },
        "small_group_protection": {
            "active": not enough,
            "threshold": MIN_GROUP_FOR_BREAKDOWN,
            "note_ru": ("подробные разрезы не публикуются: при %d пакетах "
                        "«закономерность» указывала бы на конкретного человека. "
                        "Это эвристика приватности, а не математическая "
                        "анонимность." % total) if not enough else
                       "разрезы по целям доступны: пакетов достаточно",
        },
        "not_provided": [
            "ранжирование обучающихся",
            "список «слабых» учеников",
            "итоговые оценки и академические выводы",
        ],
    }

    if enough:
        summary["objectives_with_difficulties"] = {
            "counts": dict(sorted(objective_difficulty.items(),
                                  key=lambda item: (-item[1], item[0]))),
            "from_packets": total,
        }
    else:
        summary["objectives_with_difficulties"] = {
            "withheld": True,
            "reason_ru": "меньше %d пакетов: разрез по целям скрыт"
                         % MIN_GROUP_FOR_BREAKDOWN,
        }

    return summary


def queue(store, course_id=None):
    """Open questions, ordered by whether they block and how widely reported.

    Priority comes from explicit grounds: does it block a lesson, how many
    independent packets report it, is a date close. People are never ranked.
    """
    imported = store.list_entities(PACKET_KIND,
                                   course_id=course_id) if course_id else store.list_entities(PACKET_KIND)

    grouped: dict[tuple, dict] = {}
    for packet in imported:
        for question in packet.get("questions") or []:
            category = question.get("category")
            if category not in CATEGORY_ORDER:
                continue
            key = (category, question.get("objective_id"))
            entry = grouped.setdefault(key, {
                "category": category,
                "objective_id": question.get("objective_id"),
                "reported_by_packets": set(),
                "examples_ru": [],
            })
            entry["reported_by_packets"].add(packet["packet_id"])
            text = (question.get("text_ru") or "").strip()
            if text and len(entry["examples_ru"]) < 3:
                # Stored as data. Nothing here interprets it as an instruction.
                entry["examples_ru"].append(text[:300])

    items = []
    for entry in grouped.values():
        reporters = len(entry["reported_by_packets"])
        items.append({
            "category": entry["category"],
            "objective_id": entry["objective_id"],
            "reporting_packets": reporters,
            "priority_reason_ru": _priority_reason(entry["category"], reporters),
            "ordering_key": (CATEGORY_ORDER.index(entry["category"]), -reporters,
                             entry["objective_id"] or ""),
            "examples_ru": entry["examples_ru"],
        })

    items.sort(key=lambda item: item["ordering_key"])
    for item in items:
        item.pop("ordering_key", None)

    return {
        "schema_version": SCHEMA_VERSION,
        "course_id": course_id,
        "items": items,
        "ordering_ru": "Сначала то, что блокирует занятие, затем то, о чём "
                       "сообщило больше независимых пакетов. Люди не "
                       "ранжируются.",
        "denominator_ru": "%d пакетов предоставлено" % len(imported),
    }


def _priority_reason(category, reporters):
    base = {
        "environment": "блокирует само занятие",
        "source_missing": "без источника нельзя проверить утверждение",
        "policy_unknown": "неясно правило помощи: агент не может решить сам",
        "misconception": "устойчивое неверное представление",
        "course_gap": "пробел в материалах курса",
        "review_requested": "ученик ждёт проверки",
    }.get(category, "категория не распознана")
    if reporters > 1:
        base += "; сообщили %d независимых пакетов" % reporters
    return base


def triage(store, *, course_id, category, objective_id=None, status="triaged",
           note_ru=None):
    """Move a queue item through the teacher's own states.

    A free-text `note_ru` passes the absolutist-claim filter first: a resolution
    note is where "полностью освоил" would otherwise enter the record unopposed,
    and a claim about a person is exactly what rule 6 and §16 forbid. The check
    exempts quotations, so recording what a learner said is unaffected.
    """
    if category not in CATEGORY_ORDER:
        raise TeacherError("CATEGORY_UNKNOWN", "неизвестная категория: %r" % category)
    if status not in TICKET_STATUSES:
        raise TeacherError("TICKET_STATUS_UNKNOWN", "неизвестный статус: %r" % status)

    if note_ru:
        try:
            claims.check_text(note_ru, field="resolution")
        except claims.ClaimRejected as e:
            raise TeacherError(
                e.code,
                "заметка не записана: %s" % claims.rejection_note(e)["message_ru"],
                detail={"phrases": list(e.phrases)},
            )

    ticket_id = "%s:%s" % (category, objective_id or "general")
    body, version = store.get(TICKET_KIND, ticket_id, course_id=course_id)
    document = dict(body or {
        "schema_version": SCHEMA_VERSION,
        "ticket_id": ticket_id,
        "course_id": course_id,
        "category": category,
        "objective_id": objective_id,
        "created_at": None,
        "evidence_refs": [],
        "priority_reason": None,
        "resolution": None,
    })
    previous = document.get("status")
    document["status"] = status
    document["resolution"] = note_ru

    store.apply(kind=TICKET_KIND, entity_id=ticket_id,
                events=["teacher.ticket_updated"], course_id=course_id,
                expected_version=version if body else None, new_body=document,
                event_payloads=[{"ticket_id": ticket_id, "from": previous, "to": status,
                                 "note_ru": note_ru,
                                 "_actor": "teacher_cli",
                                 "_provenance": "human_input"}])
    return document


# --------------------------------------------------------------------------
# Course issue drafts
# --------------------------------------------------------------------------

def build_issue_draft(*, course_id, category, reproduction, impact_on_learning,
                      proposed_action, evidence_status="not_verified",
                      revision=None, source_refs=(), contains_student_data=False):
    """A draft a teacher could send upstream, with the personal data question asked.

    `contains_student_data` defaults to false and the draft says what would have
    to be removed. When it cannot be false, the draft is marked private rather
    than published with names in it.
    """
    if category not in CATEGORY_ORDER:
        raise TeacherError("CATEGORY_UNKNOWN", "неизвестная категория: %r" % category)
    if evidence_status not in ("observed", "reported_by_learner", "not_verified"):
        raise TeacherError("EVIDENCE_STATUS_UNKNOWN",
                           "неизвестный статус доказательства: %r" % evidence_status)

    draft = {
        "schema_version": SCHEMA_VERSION,
        "course_id": course_id,
        "revision": revision,
        "category": category,
        "source_refs": list(source_refs),
        "reproduction": reproduction,
        "impact_on_learning": impact_on_learning,
        "proposed_action": proposed_action,
        "evidence_status": evidence_status,
        "contains_student_data": bool(contains_student_data),
    }
    schemas.validate(draft, "issue_draft")

    draft["publishable_ru"] = ("можно передавать" if not contains_student_data
                               else "только приватная передача: уберите "
                                    "персональные данные или передайте лично")
    return draft


def render_summary_markdown(summary):
    """The summary as a teacher reads it."""
    lines = []
    lines.append("# Сводка по курсу `%s`" % summary["course_id"])
    lines.append("")
    lines.append("> %s" % summary["denominator_ru"])
    lines.append("> %s" % summary["cohort_note_ru"])
    lines.append("")

    lines.append("## Состояния целей")
    lines.append("")
    if summary["objective_stages"]["counts"]:
        lines.append("| стадия | цель-состояний |")
        lines.append("|---|---|")
        for stage, count in sorted(summary["objective_stages"]["counts"].items()):
            lines.append("| %s | %d |" % (stage, count))
    else:
        lines.append("Пакетов с состояниями целей нет.")
    lines.append("")

    lines.append("## Категории вопросов")
    lines.append("")
    if summary["categories"]["counts"]:
        lines.append("| категория | вопросов |")
        lines.append("|---|---|")
        for category, count in sorted(summary["categories"]["counts"].items()):
            lines.append("| %s | %d |" % (category, count))
    else:
        lines.append("Вопросов в пакетах нет.")
    lines.append("")

    protection = summary["small_group_protection"]
    if protection["active"]:
        lines.append("## Разрезы скрыты")
        lines.append("")
        lines.append(protection["note_ru"])
        lines.append("")

    if not summary["objectives_with_difficulties"].get("withheld"):
        lines.append("## Цели с затруднениями")
        lines.append("")
        for objective, count in summary["objectives_with_difficulties"]["counts"].items():
            lines.append("- `%s` — %d" % (objective, count))
        lines.append("")

    lines.append("## Чего здесь нет")
    lines.append("")
    for item in summary["not_provided"]:
        lines.append("- %s" % item)
    lines.append("")
    return "\n".join(lines)


def render_queue_markdown(queue_document):
    lines = []
    lines.append("# Очередь преподавателя")
    lines.append("")
    lines.append("> %s" % queue_document["denominator_ru"])
    lines.append("> %s" % queue_document["ordering_ru"])
    lines.append("")
    if not queue_document["items"]:
        lines.append("Очередь пуста.")
        return "\n".join(lines)

    for index, item in enumerate(queue_document["items"], 1):
        lines.append("%d. **%s**%s" % (
            index, item["category"],
            "" if not item["objective_id"] else " — `%s`" % item["objective_id"]))
        lines.append("   - %s" % item["priority_reason_ru"])
        for example in item["examples_ru"]:
            lines.append("   - сообщение ученика: «%s»" % example)
    lines.append("")
    return "\n".join(lines)
