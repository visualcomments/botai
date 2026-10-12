# -*- coding: utf-8 -*-
"""The progress record as a projection of the store, never a second truth.

The v1 harness kept progress in Markdown that a model wrote by hand, which is
why a claim like `mastered` could appear with no evidence behind it and nothing
could reject the write. Here the direction is reversed: the store holds the
facts, and this module *renders* them.

Two consequences worth stating explicitly:

* **Nothing here is an input.** The file is regenerated from the store, so a
  hand-edit to it is overwritten. That is deliberate — a projection that could
  be edited back into authority would give editing a text file the same power as
  recording a check.
* **The absence of a claim is rendered as absence.** An objective with no
  check prints "нет проверки", not a blank or a dash that could be read as
  "nothing needed". Most of this module is about not overstating.
"""

from __future__ import annotations

from . import schemas

STAGE_LABEL = {
    "new": "не начато",
    "learning": "изучается",
    "practising": "отрабатывается",
    "demonstrated": "показано по формирующей проверке",
    "review_due": "пора повторить",
    "blocked": "заблокировано",
}

RELIABILITY_NOTE = {
    "provisional": "формирующая проверка бота",
    "observed": "наблюдаемое подтверждение",
    "human_reviewed": "проверено человеком",
}


def build_progress(*, course, learner_id, sessions, objective_states, attempts,
                   checks, exported_at=None):
    """Assemble the progress document from stored entities."""
    latest_by_objective = {}
    for check in checks:
        objective_id = check.get("objective_id")
        if objective_id:
            latest_by_objective[objective_id] = check

    objectives = []
    for objective in course.track["objectives"]:
        objective_id = objective["objective_id"]
        state = objective_states.get(objective_id) or {}
        check = latest_by_objective.get(objective_id)
        objectives.append({
            "objective_id": objective_id,
            "title": objective["title"],
            "required": bool(objective.get("required")),
            "stage": state.get("stage", "new"),
            "evidence_ids": list(state.get("evidence_ids") or []),
            "latest_check_id": state.get("latest_check_id"),
            "latest_verdict": (check or {}).get("verdict"),
            "latest_reliability": (check or {}).get("reliability"),
            "review_due_at": state.get("review_due_at"),
            "blocked_reason": state.get("blocked_reason"),
            "assistance_max": state.get("assistance_max"),
            "legacy_claim": state.get("legacy_claim"),
        })

    return {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "learner_id": learner_id,
        "course_id": course.course_id,
        "course_title": course.contract["title"],
        "course_revision": dict(course.revision),
        "accepted_contract_hash": course.contract_hash,
        "objectives": objectives,
        "sessions": [
            {
                "session_id": s.get("session_id"),
                "state": s.get("state"),
                "modes": list(s.get("modes") or []),
                "started_at": s.get("started_at"),
                "ended_at": s.get("ended_at"),
            }
            for s in sessions
        ],
        "counts": {
            "attempts": len(attempts),
            "checks": len(checks),
            "sessions": len(sessions),
            "demonstrated": sum(1 for o in objectives if o["stage"] == "demonstrated"),
            "review_due": sum(1 for o in objectives if o["stage"] == "review_due"),
        },
    }


def render_markdown(document):
    """Render the projection as Markdown. ASCII structure, Russian content."""
    lines = []
    lines.append("# Прогресс: %s" % document.get("course_title") or document["course_id"])
    lines.append("")
    lines.append("> Это представление записи состояния, а не самостоятельный "
                 "источник истины: файл пересобирается из хранилища, и правка "
                 "текста не меняет освоение.")
    lines.append("")
    lines.append("- курс: `%s`" % document["course_id"])
    revision = document.get("course_revision") or {}
    lines.append("- ревизия: `%s` (%s)" % (revision.get("id", "—")[:16],
                                           revision.get("kind", "—")))
    lines.append("- контракт: `%s`" % (document.get("accepted_contract_hash") or "—")[:16])
    lines.append("- обучающийся: `%s`" % document.get("learner_id"))
    lines.append("")

    counts = document.get("counts") or {}
    lines.append("## Сводка")
    lines.append("")
    lines.append("| показатель | значение |")
    lines.append("|---|---|")
    lines.append("| цели показаны | %d |" % counts.get("demonstrated", 0))
    lines.append("| ждут повторения | %d |" % counts.get("review_due", 0))
    lines.append("| попыток записано | %d |" % counts.get("attempts", 0))
    lines.append("| проверок записано | %d |" % counts.get("checks", 0))
    lines.append("| занятий | %d |" % counts.get("sessions", 0))
    lines.append("")

    lines.append("## Цели")
    lines.append("")
    lines.append("| цель | стадия | последняя проверка | повтор | помощь |")
    lines.append("|---|---|---|---|---|")
    for objective in document["objectives"]:
        stage = STAGE_LABEL.get(objective["stage"], objective["stage"])
        if objective.get("latest_verdict"):
            reliability = RELIABILITY_NOTE.get(objective.get("latest_reliability", ""),
                                               objective.get("latest_reliability") or "")
            verdict = "%s (%s)" % (objective["latest_verdict"], reliability)
        else:
            # No check is not "fine": it is an absence, and it is printed as one.
            verdict = "нет проверки"
        evidence = len(objective.get("evidence_ids") or [])
        if objective["stage"] == "demonstrated" and evidence == 0:
            # Should be impossible — the store refuses it — but a projection
            # that silently rendered it would hide a real inconsistency.
            verdict = "НЕСОГЛАСОВАННО: показано без доказательств"
        review = (objective.get("review_due_at") or "—")
        assistance = objective.get("assistance_max") or "—"
        title = objective["title"]
        if not objective.get("required"):
            title += " (необязательная)"
        lines.append("| %s | %s | %s | %s | %s |"
                     % (title, stage, verdict, review, assistance))
    lines.append("")

    blocked = [o for o in document["objectives"] if o["stage"] == "blocked"]
    if blocked:
        lines.append("## Заблокировано")
        lines.append("")
        for objective in blocked:
            lines.append("- **%s** — %s" % (objective["title"],
                                            objective.get("blocked_reason") or "причина не указана"))
        lines.append("")

    legacy = [o for o in document["objectives"] if o.get("legacy_claim")]
    if legacy:
        lines.append("## Импортированные утверждения")
        lines.append("")
        lines.append("Эти записи пришли из старого дневника как утверждение, "
                     "а не как доказательство:")
        lines.append("")
        for objective in legacy:
            lines.append("- %s: заявлено «%s», записано как «%s»"
                         % (objective["title"], objective["legacy_claim"],
                            STAGE_LABEL.get(objective["stage"], objective["stage"])))
        lines.append("")

    lines.append("## Занятия")
    lines.append("")
    if not document["sessions"]:
        lines.append("Занятий ещё не было.")
    else:
        lines.append("| занятие | состояние | режим | начато |")
        lines.append("|---|---|---|---|")
        for session in document["sessions"]:
            lines.append("| `%s` | %s | %s | %s |"
                         % (str(session["session_id"])[:8], session["state"],
                            ", ".join(session["modes"]), session["started_at"]))
    lines.append("")
    return "\n".join(lines)


def validate(document):
    """Validate a progress document against the published contract."""
    return schemas.validate(document, "progress")
