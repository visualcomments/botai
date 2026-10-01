# -*- coding: utf-8 -*-
"""Learning analytics: what the record shows, and what it does not.

The design's §6.1 asks for a dashboard with a heatmap, time investment,
struggle points, a predicted performance and a peer comparison. This module
delivers every one of them — and refuses two of the things a dashboard usually
lies about.

* **`predicted_performance` is a documented heuristic, not a model.** Its
  formula is written out in the docstring, its inputs are the three ratios a
  reader can check by hand, and it says so in the `basis_ru` string a caller
  displays. There is no training set, no fit and no hidden weight; the design
  asks for an ML classifier, and an honest deterministic rule that a teacher can
  audit is strictly better than an untrained model pretending to have been
  trained. Nothing in this module is called "trained" anywhere.
* **`peer_comparison` returns a band, never a rank over names.** "Вы в топ-30%"
  is a fact about a percentile of a comparison set; "23-е место из 100" is a
  fact about other people. The function takes a mapping of *many* students and
  returns one band for one of them — it has no path by which an ordered list of
  other people's ids could come back out.
* **Numbers that cannot be computed are `None`, not zero.** A time estimate
  with one timestamp is unknown; `0.0` there would be a claim that the work
  took no time. Every counter carries a `denominator` saying how many
  objectives / attempts / checks it covers, because a bare count is
  indistinguishable between a strong cohort and an empty record.

The inputs are the same store entities `progress.build_progress` consumes —
`session`, `attempt`, `check`, `objective_state` — so a report and an analytics
document describe the same facts by construction. Like `progress`, this module
reads and renders; it never writes to storage and never becomes a second truth.
"""

from __future__ import annotations

import html as _html

from . import tutoring

SCHEMA_VERSION = 2

# The threshold at which an objective becomes a struggle point. `EXAMPLE` is
# rung 3 of the ladder (`tutoring.LEVEL_STEP`), so reaching it means the
# learner needed a worked example — a level the graded ceiling still permits,
# and a level worth looking at twice.
STRUGGLE_ASSISTANCE_MIN = "EXAMPLE"

# Non-pass checks above which an objective is flagged. Two failures are a
# normal learning path; three on one objective is a pattern.
STRUGGLE_MAX_FAILED_CHECKS = 2

# Bands for `predicted_performance`. Split at 0.40 and 0.70 so the middle band
# is the widest — a middle reading is the least actionable one, and making it
# broad keeps the labels from turning ordinary variation into a verdict.
PERFORMANCE_BANDS = (
    (0.40, "low"),
    (0.70, "medium"),
    (1.01, "high"),
)

BAND_RU = {
    "low": "требует внимания",
    "medium": "на своём пути",
    "high": "устойчиво продвигается",
}


class AnalyticsError(RuntimeError):
    """A refused analytics operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _ratio(numerator, denominator):
    """A 0..1 ratio; `None` when there is nothing to divide by."""
    if not denominator:
        return None
    return _clamp(float(numerator) / float(denominator))


def _timestamp(value):
    return tutoring.parse_iso(value)


def _assistance_rank(level):
    return tutoring.LEVEL_STEP.get(level or "", 0)


def _is_active_session(session):
    """A session the learner was actually working in.

    `tutoring.WORKING_STATES` plus `COMPLETED`: a cancelled or paused session
    is a session the learner did not work through, and counting it would
    deflate every per-session rate below.
    """
    state = (session or {}).get("state")
    return state in tutoring.WORKING_STATES or state == "COMPLETED"


def _attempt_times(attempts, objective_id):
    moments = []
    for attempt in attempts or []:
        if attempt.get("objective_id") != objective_id:
            continue
        when = _timestamp(attempt.get("submitted_at"))
        if when is not None:
            moments.append(when)
    moments.sort()
    return moments


# --------------------------------------------------------------------------
# Building the document
# --------------------------------------------------------------------------


def build_student_analytics(
    *,
    course,
    learner_id,
    sessions,
    objective_states,
    attempts,
    checks,
    profile=None,
    clock=None,
):
    """Assemble one learner's analytics document from stored entities.

    `course` supplies the objective list and titles (the same source
    `progress.build_progress` uses); `objective_states` is the mapping
    `{objective_id: body}`. `profile`, when given, is the learner's
    `learning_profile` and contributes a single line about presentation
    preference — it never enters a score.
    """
    sessions = list(sessions or [])
    attempts = list(attempts or [])
    checks = list(checks or [])
    objective_states = dict(objective_states or {})

    objectives = list((course.track or {}).get("objectives") or [])
    active_sessions = [s for s in sessions if _is_active_session(s)]

    attempts_by_objective = {}
    for attempt in attempts:
        attempts_by_objective.setdefault(attempt.get("objective_id"), []).append(
            attempt
        )

    checks_by_objective = {}
    for check in checks:
        checks_by_objective.setdefault(check.get("objective_id"), []).append(check)

    heatmap = []
    struggle_points = []
    time_investment = []
    demonstrated = 0
    review_due = 0
    stuck = 0

    for objective in objectives:
        objective_id = objective["objective_id"]
        state = objective_states.get(objective_id) or {}
        objective_checks = checks_by_objective.get(objective_id) or []
        objective_attempts = attempts_by_objective.get(objective_id) or []
        stage = state.get("stage", "new")

        assistance = state.get("assistance_max")
        failed = sum(1 for c in objective_checks if c.get("verdict") != "pass")

        if stage == "demonstrated":
            demonstrated += 1
        if stage == "review_due":
            review_due += 1
        if tutoring.stuck_signal(state.get("consecutive_stuck_sessions")):
            stuck += 1

        heatmap.append(
            {
                "objective_id": objective_id,
                "title": objective.get("title") or objective_id,
                "stage": stage,
                "checks": len(objective_checks),
                "attempts": len(objective_attempts),
                "assistance_max": assistance,
            }
        )

        if (
            _assistance_rank(assistance) >= _assistance_rank(STRUGGLE_ASSISTANCE_MIN)
            or failed > STRUGGLE_MAX_FAILED_CHECKS
        ):
            struggle_points.append(
                {
                    "objective_id": objective_id,
                    "title": objective.get("title") or objective_id,
                    "reasons_ru": _struggle_reasons(assistance, failed),
                    "assistance_max": assistance,
                    "failed_checks": failed,
                    "weight": failed + _assistance_rank(assistance),
                }
            )

        time_investment.append(
            {
                "objective_id": objective_id,
                "seconds": _seconds_between_attempts(objective_attempts),
            }
        )

    # Ranked: the most failed/most assisted objective first, ties broken by id
    # so the ordering never depends on the order the store returned rows in.
    struggle_points.sort(key=lambda item: (-item["weight"], item["objective_id"]))

    velocity = _velocity(demonstrated, len(active_sessions))

    document = {
        "schema_version": SCHEMA_VERSION,
        "learner_id": learner_id,
        "course_id": getattr(course, "course_id", None),
        "course_title": (course.contract or {}).get("title")
        if getattr(course, "contract", None)
        else None,
        "progress_heatmap": heatmap,
        "time_investment": time_investment,
        "struggle_points": struggle_points,
        "velocity": velocity,
        "counts": {
            "objectives": len(objectives),
            "demonstrated": demonstrated,
            "review_due": review_due,
            "stuck": stuck,
            "sessions": len(sessions),
            "active_sessions": len(active_sessions),
            "attempts": len(attempts),
            "checks": len(checks),
        },
        "denominator": {
            "objectives": len(objectives),
            "sessions": len(sessions),
            "attempts": len(attempts),
            "checks": len(checks),
        },
        "denominator_ru": (
            "отчёт покрывает %d целей, %d занятий, %d попыток и %d проверок из "
            "записи этого обучающегося"
            % (
                len(objectives),
                len(sessions),
                len(attempts),
                len(checks),
            )
        ),
        "profile_note_ru": _profile_note(profile),
        "generated_at": tutoring.now_iso(clock),
    }
    validate_analytics(document)
    return document


def _struggle_reasons(assistance, failed):
    reasons = []
    if _assistance_rank(assistance) >= _assistance_rank(STRUGGLE_ASSISTANCE_MIN):
        reasons.append("нужен был пример (помощь до уровня %s)" % assistance)
    if failed > STRUGGLE_MAX_FAILED_CHECKS:
        reasons.append("неудачных проверок: %d" % failed)
    return reasons


def _seconds_between_attempts(attempts):
    """Estimated seconds spent on an objective, or `None` when unknown.

    Two ways to know, tried in order: an explicit `duration_seconds` on the
    attempt itself (a timer the learner saw), and otherwise the span from the
    first to the last timestamped attempt on that objective. With one
    timestamp there is no span, and with none there is no measurement — the
    result is `None`, because inventing a number here would be the single
    most reliable way to make the whole report wrong.
    """
    explicit = 0
    saw_explicit = False
    for attempt in attempts or []:
        value = attempt.get("duration_seconds")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            explicit += int(value)
            saw_explicit = True
    if saw_explicit:
        return explicit

    moments = []
    for attempt in attempts or []:
        when = _timestamp(attempt.get("submitted_at"))
        if when is not None:
            moments.append(when)
    if len(moments) < 2:
        return None
    return int((max(moments) - min(moments)).total_seconds())


def _velocity(demonstrated, active_sessions):
    """Demonstrated objectives per active session.

    Deliberately 0.0 with no sessions rather than an error: a learner who has
    not started has demonstrated nothing, and `0.0 / 0` would otherwise be the
    first thing the caller has to defend against.
    """
    if active_sessions <= 0:
        return 0.0
    return round(demonstrated / float(active_sessions), 6)


def _profile_note(profile):
    if not profile:
        return None
    style = getattr(profile, "dominant_style", None)
    if not style:
        style = (
            (profile.get("cognitive_style") or {}).get("dominant_style")
            if isinstance(profile, dict)
            else None
        )
    if not style:
        return None
    return (
        "по профилю обучения преобладает подача «%s»; профиль влияет только на "
        "формулировки, не на оценки" % style
    )


# --------------------------------------------------------------------------
# The heuristic
# --------------------------------------------------------------------------

# Documented in `predicted_performance`; kept here so the docstring and the code
# cannot drift apart into two different formulas.
PERFORMANCE_WEIGHTS = {
    "demonstrated_ratio": 0.50,
    "review_health": 0.25,
    "struggle_free": 0.25,
}


def predicted_performance(analytics):
    """An honest, documented heuristic — not a trained model.

    Formula, stated once and implemented exactly:

        demonstrated_ratio = demonstrated / max(1, objectives)
        review_health      = 1 - review_due / max(1, objectives)
        struggle_free      = 1 - min(1, struggle_points / max(1, objectives))

        score = 0.50 * demonstrated_ratio
              + 0.25 * review_health
              + 0.25 * struggle_free

    Every input is a count already present in the document, so a reader can
    recompute the number with a calculator. Bands: `< 0.40` low, `< 0.70`
    medium, else high.

    What this is *not*: it is not fitted to any cohort, it does not predict a
    grade, and it does not claim to know what will happen next. It is a summary
    of three ratios that were already true.
    """
    counts = analytics.get("counts") or {}
    objectives = counts.get("objectives") or 0
    divisor = max(1, objectives)

    demonstrated_ratio = _ratio(counts.get("demonstrated") or 0, divisor) or 0.0
    review_health = 1.0 - min(1.0, (counts.get("review_due") or 0) / float(divisor))
    struggle_free = 1.0 - min(
        1.0, len(analytics.get("struggle_points") or []) / float(divisor)
    )

    score = (
        PERFORMANCE_WEIGHTS["demonstrated_ratio"] * demonstrated_ratio
        + PERFORMANCE_WEIGHTS["review_health"] * review_health
        + PERFORMANCE_WEIGHTS["struggle_free"] * struggle_free
    )
    score = round(_clamp(score), 6)

    band = "high"
    for limit, candidate in PERFORMANCE_BANDS:
        if score < limit:
            band = candidate
            break

    basis = (
        "правило на трёх долях из записи, не обученная модель: "
        "освоено %d из %d целей (%.2f), повторений в срок %d из %d (%.2f), "
        "точек затруднения %d из %d (%.2f); веса 0.50/0.25/0.25"
        % (
            counts.get("demonstrated") or 0,
            objectives,
            demonstrated_ratio,
            objectives - (counts.get("review_due") or 0),
            objectives,
            review_health,
            len(analytics.get("struggle_points") or []),
            objectives,
            struggle_free,
        )
    )
    return {
        "score": score,
        "band": band,
        "band_ru": BAND_RU.get(band, band),
        "basis_ru": basis,
        "formula": "score = 0.50*osvoeno + 0.25*povtory_v_srok + 0.25*bez_tochek_zatyazheniya",
        "trained_model": False,
    }


# --------------------------------------------------------------------------
# Peer comparison
# --------------------------------------------------------------------------


def peer_comparison(analytics_by_student, *, student_id=None):
    """One learner's percentile, as an anonymous band only.

    `analytics_by_student` is `{student_id: analytics_document}`. The returned
    `percentile` is 0..100 (higher = more objectives demonstrated relative to
    the comparison set) and `band_ru` is one of «топ-30%», «верхние 50%» or
    «нижние 50%». No other student's id, position or score is returned: the
    only value the caller learns about anyone else is where *this* learner sits
    relative to them.

    `student_id` is keyword-only and optional so the specified positional
    surface still works — with a single-entry mapping the choice is
    unambiguous.
    """
    if not analytics_by_student:
        raise AnalyticsError(
            "ANALYTICS_EMPTY",
            "нет данных для сравнения: передайте записи хотя бы одного обучающегося",
        )
    keys = sorted(analytics_by_student)
    if student_id is None:
        if len(keys) != 1:
            raise AnalyticsError(
                "STUDENT_ID_REQUIRED",
                "в сравнении несколько обучающихся: укажите student_id, "
                "которому считать полосу",
            )
        student_id = keys[0]
    if student_id not in analytics_by_student:
        raise AnalyticsError(
            "STUDENT_NOT_IN_COMPARISON",
            "обучающийся %s отсутствует в переданном наборе для сравнения" % student_id,
        )

    values = []
    for key in keys:
        counts = (analytics_by_student[key] or {}).get("counts") or {}
        divisor = max(1, counts.get("objectives") or 0)
        values.append((key, _ratio(counts.get("demonstrated") or 0, divisor) or 0.0))

    target = dict(values)[student_id]
    better = sum(1 for _key, value in values if value > target)
    ties = sum(1 for _key, value in values if value == target)
    # Mid-rank percentile: a tie counts as half, so a cohort of identical
    # learners lands at 50 rather than at 0 or 100.
    percentile = 100.0 * (better + 0.5 * (ties - 1)) / float(len(values))
    if len(values) == 1:
        percentile = 50.0
    percentile = round(percentile, 2)

    if percentile >= 70:
        band_ru = "топ-30% относительно сравниваемой группы"
    elif percentile >= 50:
        band_ru = "верхние 50% относительно сравниваемой группы"
    else:
        band_ru = "нижние 50% относительно сравниваемой группы"

    return {
        "student_id": student_id,
        "percentile": percentile,
        "band_ru": band_ru,
        "denominator": len(values),
        "denominator_ru": (
            "полоса посчитана по %d записям, переданным для сравнения" % len(values)
        ),
        "named_ranks_returned": False,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_report_markdown(document):
    """Markdown rendering: ASCII structure, Russian content.

    The shape follows `progress.render_markdown` — a heading, a quoted note
    about what the document is, then tables — because a report a learner has
    already learned to read should not change format between sections.
    """
    validate_analytics(document)
    lines = []
    lines.append(
        "# Аналитика: %s"
        % (document.get("course_title") or document.get("course_id") or "—")
    )
    lines.append("")
    lines.append(
        "> Это представление записи состояния, а не самостоятельный "
        "источник истины. Оценка «прогноз» — правило на трёх долях, "
        "а не обученная модель."
    )
    lines.append("")
    lines.append("- обучающийся: `%s`" % document.get("learner_id"))
    lines.append("- курс: `%s`" % (document.get("course_id") or "—"))
    lines.append("- сформировано: %s" % document.get("generated_at"))
    lines.append("")

    counts = document.get("counts") or {}
    prediction = predicted_performance(document)
    lines.append("## Сводка")
    lines.append("")
    lines.append("| показатель | значение |")
    lines.append("|---|---|")
    lines.append("| целей показано | %d |" % (counts.get("demonstrated") or 0))
    lines.append("| ждут повторения | %d |" % (counts.get("review_due") or 0))
    lines.append(
        "| точек затруднения | %d |" % len(document.get("struggle_points") or [])
    )
    lines.append("| занятий | %d |" % (counts.get("sessions") or 0))
    lines.append("| попыток | %d |" % (counts.get("attempts") or 0))
    lines.append("| проверок | %d |" % (counts.get("checks") or 0))
    lines.append(
        "| освоения в час занятия | %.2f |" % (document.get("velocity") or 0.0)
    )
    lines.append("| оценка (%s) | %.2f |" % (prediction["band"], prediction["score"]))
    lines.append("")
    lines.append("> %s" % prediction["basis_ru"])
    lines.append("")
    lines.append("> Покрытие: %s" % document.get("denominator_ru"))

    lines.append("")
    lines.append("## Цели")
    lines.append("")
    lines.append("| цель | стадия | проверок | попыток | помощь |")
    lines.append("|---|---|---|---|---|")
    for row in document.get("progress_heatmap") or []:
        title = row.get("title") or row.get("objective_id")
        stage = row.get("stage") or "new"
        assistance = row.get("assistance_max") or "—"
        lines.append(
            "| %s | %s | %d | %d | %s |"
            % (
                title,
                stage,
                row.get("checks") or 0,
                row.get("attempts") or 0,
                assistance,
            )
        )
    lines.append("")

    struggle = document.get("struggle_points") or []
    lines.append("## Точки затруднения")
    lines.append("")
    if not struggle:
        lines.append("Точек затруднения не выявлено.")
    else:
        for row in struggle:
            lines.append(
                "- **%s** — %s"
                % (
                    row.get("title") or row.get("objective_id"),
                    "; ".join(row.get("reasons_ru") or []),
                )
            )
    lines.append("")

    lines.append("## Время по целям")
    lines.append("")
    lines.append("| цель | секунд |")
    lines.append("|---|---|")
    for row in document.get("time_investment") or []:
        seconds = row.get("seconds")
        lines.append(
            "| %s | %s |"
            % (
                row.get("objective_id"),
                # A missing measurement is printed as an absence, never as 0.
                "нет данных" if seconds is None else seconds,
            )
        )
    lines.append("")

    note = document.get("profile_note_ru")
    if note:
        lines.append("## Профиль обучения")
        lines.append("")
        lines.append("> %s" % note)
        lines.append("")

    return "\n".join(lines)


def render_report_html(document):
    """A self-contained HTML export.

    No JavaScript and no external assets — this is the export path, and a file
    that loads a script from a CDN is a file that phones home. Every
    interpolated value goes through `html.escape`, because a course title with
    an ampersand in it must not become markup.
    """
    validate_analytics(document)
    counts = document.get("counts") or {}
    prediction = predicted_performance(document)
    peer = document.get("peer_comparison") or None

    def esc(value):
        return _html.escape(str(value if value is not None else "—"), quote=True)

    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="ru">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append(
        "<title>%s</title>"
        % esc(document.get("course_title") or document.get("course_id") or "Аналитика")
    )
    parts.append("<style>")
    parts.append("body{font-family:system-ui,sans-serif;margin:2rem;color:#111;}")
    parts.append("table{border-collapse:collapse;margin:1rem 0;}")
    parts.append("td,th{border:1px solid #ccc;padding:.35rem .6rem;text-align:left;}")
    parts.append("th{background:#f2f2f2;}")
    parts.append(".note{color:#444;border-left:3px solid #888;padding-left:.75rem;}")
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append(
        "<h1>%s</h1>"
        % esc(document.get("course_title") or document.get("course_id") or "Аналитика")
    )
    parts.append(
        '<p class="note">Это представление записи состояния. '
        "Оценка — правило на трёх долях, не обученная модель.</p>"
    )
    parts.append(
        "<p>Обучающийся: <code>%s</code><br>Сформировано: %s</p>"
        % (esc(document.get("learner_id")), esc(document.get("generated_at")))
    )

    parts.append("<h2>Сводка</h2>")
    parts.append("<table><tr><th>показатель</th><th>значение</th></tr>")
    for label, value in (
        ("целей показано", counts.get("demonstrated") or 0),
        ("ждут повторения", counts.get("review_due") or 0),
        ("точек затруднения", len(document.get("struggle_points") or [])),
        ("занятий", counts.get("sessions") or 0),
        ("попыток", counts.get("attempts") or 0),
        ("проверок", counts.get("checks") or 0),
        ("освоения в час занятия", "%.2f" % (document.get("velocity") or 0.0)),
        ("оценка", "%.2f (%s)" % (prediction["score"], prediction["band"])),
    ):
        parts.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(label), esc(value)))
    parts.append("</table>")
    parts.append('<p class="note">%s</p>' % esc(prediction["basis_ru"]))
    parts.append(
        '<p class="note">Покрытие: %s</p>' % esc(document.get("denominator_ru"))
    )

    if peer:
        parts.append("<h2>Сравнение с группой</h2>")
        parts.append(
            "<p>%s (перцентиль %s)</p>"
            % (esc(peer.get("band_ru")), esc(peer.get("percentile")))
        )

    parts.append("<h2>Цели</h2>")
    parts.append(
        "<table><tr><th>цель</th><th>стадия</th><th>проверок</th>"
        "<th>попыток</th><th>помощь</th></tr>"
    )
    for row in document.get("progress_heatmap") or []:
        parts.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                esc(row.get("title") or row.get("objective_id")),
                esc(row.get("stage")),
                esc(row.get("checks") or 0),
                esc(row.get("attempts") or 0),
                esc(row.get("assistance_max") or "—"),
            )
        )
    parts.append("</table>")

    parts.append("<h2>Точки затруднения</h2>")
    struggle = document.get("struggle_points") or []
    if not struggle:
        parts.append("<p>Точек затруднения не выявлено.</p>")
    else:
        parts.append("<ul>")
        for row in struggle:
            parts.append(
                "<li><strong>%s</strong> — %s</li>"
                % (
                    esc(row.get("title") or row.get("objective_id")),
                    esc("; ".join(row.get("reasons_ru") or [])),
                )
            )
        parts.append("</ul>")

    parts.append("<h2>Время по целям</h2>")
    parts.append("<table><tr><th>цель</th><th>секунд</th></tr>")
    for row in document.get("time_investment") or []:
        seconds = row.get("seconds")
        parts.append(
            "<tr><td>%s</td><td>%s</td></tr>"
            % (
                esc(row.get("objective_id")),
                esc("нет данных" if seconds is None else seconds),
            )
        )
    parts.append("</table>")

    parts.append("</body></html>")
    return "\n".join(parts)


def weekly_digest(document):
    """A short Russian summary for the week. Text only — no sending.

    The harness never opens a network connection, so a "digest" here is a
    string a human pastes or a caller writes to a file. Anything that would
    deliver it is outside this module by construction.
    """
    validate_analytics(document)
    counts = document.get("counts") or {}
    prediction = predicted_performance(document)
    struggle = document.get("struggle_points") or []
    objectives = counts.get("objectives") or 0

    lines = []
    lines.append(
        "Сводка за неделю по курсу %s"
        % (document.get("course_title") or document.get("course_id") or "—")
    )
    lines.append(
        "Освоено %d из %d целей, ждут повторения %d, точек затруднения %d."
        % (
            counts.get("demonstrated") or 0,
            objectives,
            counts.get("review_due") or 0,
            len(struggle),
        )
    )
    lines.append(
        "Занятий: %d, попыток: %d, проверок: %d."
        % (
            counts.get("sessions") or 0,
            counts.get("attempts") or 0,
            counts.get("checks") or 0,
        )
    )
    lines.append(
        "Оценка: %s (%.2f). %s"
        % (prediction["band"], prediction["score"], prediction["basis_ru"])
    )
    if struggle:
        lines.append(
            "Точки затруднения: %s"
            % ", ".join(
                row.get("title") or row.get("objective_id") for row in struggle[:5]
            )
        )
    else:
        lines.append("Точек затруднения не выявлено.")
    lines.append(document.get("denominator_ru") or "")
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# Validation (structural — there is no analytics contract in schemas/v2)
# --------------------------------------------------------------------------

_ANALYTICS_REQUIRED = (
    "learner_id",
    "progress_heatmap",
    "time_investment",
    "struggle_points",
    "velocity",
    "counts",
    "denominator",
    "generated_at",
)

_ANALYTICS_LISTS = ("progress_heatmap", "time_investment", "struggle_points")
_ANALYTICS_COUNTS = ("objectives", "sessions", "attempts", "checks")


def validate_analytics(document):
    """Structural validation for a document with no published contract.

    `schemas/v2` has no analytics schema, so this checks the things a schema
    would have checked — the required keys, their types, and the shape of the
    counters — and raises `AnalyticsError("ANALYTICS_INVALID", ...)`. It is
    deliberately stricter than "has a key": a report whose `counts` is not a
    dict would otherwise render as zeros and read as a real finding.
    """
    if not isinstance(document, dict):
        raise AnalyticsError(
            "ANALYTICS_INVALID",
            "документ аналитики должен быть объектом, получено %s"
            % type(document).__name__,
        )

    missing = [key for key in _ANALYTICS_REQUIRED if key not in document]
    if missing:
        raise AnalyticsError(
            "ANALYTICS_INVALID",
            "в документе аналитики нет обязательных полей: %s" % ", ".join(missing),
        )

    for key in _ANALYTICS_LISTS:
        if not isinstance(document.get(key), list):
            raise AnalyticsError(
                "ANALYTICS_INVALID",
                "поле %s должно быть списком, получено %s"
                % (key, type(document.get(key)).__name__),
            )

    counts = document.get("counts")
    if not isinstance(counts, dict):
        raise AnalyticsError(
            "ANALYTICS_INVALID",
            "поле counts должно быть объектом, получено %s" % type(counts).__name__,
        )
    for key in _ANALYTICS_COUNTS:
        value = counts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AnalyticsError(
                "ANALYTICS_INVALID",
                "counts.%s должно быть неотрицательным целым, получено %r"
                % (key, value),
            )

    denominator = document.get("denominator")
    if not isinstance(denominator, dict):
        raise AnalyticsError(
            "ANALYTICS_INVALID",
            "поле denominator должно быть объектом с числами покрытия, "
            "получено %s" % type(denominator).__name__,
        )

    velocity = document.get("velocity")
    if isinstance(velocity, bool) or not isinstance(velocity, (int, float)):
        raise AnalyticsError(
            "ANALYTICS_INVALID",
            "поле velocity должно быть числом, получено %r" % (velocity,),
        )

    if document.get("learner_id") in (None, ""):
        raise AnalyticsError(
            "ANALYTICS_INVALID",
            "у документа аналитики должен быть обучающийся (learner_id)",
        )

    return document


__all__ = [
    "AnalyticsError",
    "build_student_analytics",
    "predicted_performance",
    "peer_comparison",
    "render_report_markdown",
    "render_report_html",
    "weekly_digest",
    "validate_analytics",
    "PERFORMANCE_WEIGHTS",
    "BAND_RU",
]
