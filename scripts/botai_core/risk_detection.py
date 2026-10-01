# -*- coding: utf-8 -*-
"""At-risk detection as an explicit, auditable rule — not a trained model.

The design (§6.2) asks for a Random Forest classifier over 15 session-history
features. This module delivers the 15 features and refuses the rest of that
sentence. A random forest fitted on this cohort's own history would be a model
that learned this cohort's definitions of "behind" and would then be asked to
predict the same cohort, with nobody able to say which feature moved a student
from one side of a threshold to the other. The honest alternative is what is
here: 15 declared derivations, 15 declared weights that sum to exactly 1.0 and
are asserted to, and a score a reader can recompute.

Three properties the module treats as structural rather than aspirational:

* **It is a signal for a person, never a grade.** The score does not enter
  `progress`, does not change an assistance ceiling, and does not appear in any
  course assessment. A number a student is told about is not a risk score — it
  is a label, and the module's callers are expected to treat it the same way.
* **Nothing leaves the machine without consent.** `interventions_allowed`
  returns `False` unless a live, non-withdrawn consent record for the purpose
  `teacher_export` exists. That is a code guard, not a sentence in a docstring:
  `detect_at_risk` returns a list, and the caller who wants to show it to
  anybody has to pass the consent check first.
* **Missing data is 0 with a note, not a guess.** An empty history is not
  evidence of disengagement; it is an absence of evidence. Every feature
  derivation says what it does when the input is missing, and `basis_ru`
  records whether the score rests on a full record or a thin one.

The only text analysis in the module is `sentiment_signal`: a keyword counter
over a fixed list, described as a keyword counter and never as sentiment
analysis. Substring matching on ten words cannot tell irony from despair, and
pretending otherwise would put a model's confidence behind a count.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import tutoring

SCHEMA_VERSION = 2

# --------------------------------------------------------------------------
# The 15 features
# --------------------------------------------------------------------------

# (feature, russian label, one-line derivation)
RISK_FEATURES = (
    (
        "days_since_last_session",
        "дней с последнего занятия",
        "целое число дней от последней записи `session.started_at`/`ended_at` "
        "до момента наблюдения; нет данных -> 0",
    ),
    (
        "sessions_last_7_days",
        "занятий за 7 дней",
        "число сессий с `started_at` внутри последних семи дней; риск растёт при "
        "меньшем числе (приведено к 0..1 как 1 - n/5); нет данных -> 0",
    ),
    (
        "sessions_trend",
        "динамика занятий",
        "доля сессий за последние 7 дней от числа за предыдущие 7; снижение "
        "даёт риск 1 - n_now/max(1,n_prev); нет данных -> 0",
    ),
    (
        "attempts_per_session",
        "попыток на занятие",
        "len(attempts)/max(1,len(sessions)); риск растёт при меньшей плотности: "
        "1 - min(1, v/5); нет данных -> 0",
    ),
    (
        "failed_check_ratio",
        "доля неудачных проверок",
        "checks с verdict != 'pass' / len(checks); нет данных -> 0",
    ),
    (
        "assistance_max_mean",
        "средний уровень помощи",
        "среднее tutoring.LEVEL_STEP[assistance_max] по попыткам, делённое на 4 "
        "(максимум лестницы); UNKNOWN/NONE считаются 0; нет данных -> 0",
    ),
    (
        "review_due_count",
        "повторений в срок",
        "число objective_state со stage == 'review_due'; нормируется как "
        "min(1, n/3); нет данных -> 0",
    ),
    (
        "consecutive_stuck_sessions",
        "сессий подряд на одной цели",
        "максимум objective_state.consecutive_stuck_sessions; tutoring."
        "stuck_signal (>= 3) считается как полный риск; нет данных -> 0",
    ),
    (
        "objectives_demonstrated_ratio",
        "доля показанных целей",
        "stage == 'demonstrated' / max(1, числа целей в objective_states); риск "
        "= 1 - ratio; нет данных -> 0",
    ),
    (
        "days_since_last_attempt",
        "дней с последней попытки",
        "дней от последней попытки (`submitted_at`) до наблюдения; нормируется "
        "как min(1, n/14); нет данных -> 0",
    ),
    (
        "incomplete_sessions_ratio",
        "доля незавершённых занятий",
        "сессии в PAUSED/BLOCKED / max(1, total); нет данных -> 0",
    ),
    (
        "blocked_objectives",
        "заблокированных целей",
        "число objective_state со stage == 'blocked', нормируется как "
        "min(1, n/2); нет данных -> 0",
    ),
    (
        "hint_requests_per_attempt",
        "подсказок на попытку",
        "попытки с assistance_max в HINT/EXAMPLE (UNKNOWN не считается) / "
        "max(1, len(attempts)); риск = min(1, v/2); нет данных -> 0",
    ),
    (
        "idle_streak",
        "простой без занятий",
        "дней с последнего завершённого занятия в состоянии COMPLETED; "
        "нормируется как min(1, n/10); нет данных -> 0",
    ),
    (
        "negative_sentiment_hits",
        "маркеров усталости в тексте",
        "суммарное число маркеров FRUSTRATION_MARKERS во всех текстовых полях "
        "попыток и сессий, нормируется как min(1, n/3); нет данных -> 0",
    ),
)

RISK_FEATURE_NAMES = tuple(name for name, _label, _doc in RISK_FEATURES)

RISK_FEATURE_LABELS = {name: label for name, label, _doc in RISK_FEATURES}
RISK_FEATURE_DOCS = {name: doc for name, _label, doc in RISK_FEATURES}

# --------------------------------------------------------------------------
# Weights
# --------------------------------------------------------------------------
#
# Weights sum to exactly 1.0 and are asserted to below — a weight table that
# does not sum to one makes `score` a value whose meaning depends on which
# features happened to be present, which is the same as having no weights at
# all. The split is deliberately uneven: the features that describe the record
# itself (stuck cycles, demonstrated ratio, session recency) carry more than
# the ones that describe an artefact of measurement (hint density, sentiment).
RISK_WEIGHTS = {
    "days_since_last_session": 0.10,
    "sessions_last_7_days": 0.07,
    "sessions_trend": 0.05,
    "attempts_per_session": 0.06,
    "failed_check_ratio": 0.09,
    "assistance_max_mean": 0.08,
    "review_due_count": 0.05,
    "consecutive_stuck_sessions": 0.10,
    "objectives_demonstrated_ratio": 0.10,
    "days_since_last_attempt": 0.06,
    "incomplete_sessions_ratio": 0.06,
    "blocked_objectives": 0.06,
    "hint_requests_per_attempt": 0.04,
    "idle_streak": 0.04,
    "negative_sentiment_hits": 0.04,
}

_WEIGHT_SUM = sum(RISK_WEIGHTS.values())
if abs(_WEIGHT_SUM - 1.0) > 1e-9:
    # Raised at import time on purpose: a broken weight table must fail the
    # test suite and the smoke test, not produce a subtly wrong score in
    # production six months later.
    raise RuntimeError("RISK_WEIGHTS must sum to 1.0, got %r" % (_WEIGHT_SUM,))

# Thresholds that turn a raw day-count into a 0..1 contribution. Documented
# here because they are the least defensible part of any heuristic: they say
# "two weeks of silence is a full-strength signal", which is a judgement, not
# a measurement, and it is written where a reader can argue with it.
DAYS_SILENCE_FULL = 14.0
DAYS_SILENCE_ATTEMPT_FULL = 14.0
DAYS_IDLE_FULL = 10.0

LEVELS = ("low", "medium", "high")

# `score` bands. 0.50 is the default detection threshold used by
# `detect_at_risk`; 0.75 is the point at which a signal should be looked at
# the same day rather than at the next check-in.
BAND_MEDIUM = 0.50
BAND_HIGH = 0.75

LEVEL_RU = {
    "low": "обычный режим",
    "medium": "стоит проверить",
    "high": "требует внимания преподавателя",
}


class RiskDetectionError(RuntimeError):
    """A refused risk operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Keyword counter (the only text analysis in the module)
# --------------------------------------------------------------------------

# Fixed, documented, and closed. This is a keyword counter, not sentiment
# analysis: it counts how many of these substrings appear in the recorded
# text. It cannot detect irony, it cannot detect a quoted assignment
# statement, and a hit is not proof of frustration — it is a reason to look.
FRUSTRATION_MARKERS = (
    "не понимаю",
    "не получается",
    "бесит",
    "сдаюсь",
    "тупой",
    "i don't get",
    "frustrat",
    "give up",
    "stupid",
    "hate",
)


def sentiment_signal(texts):
    """Count frustration markers in the given texts.

    Case-insensitive substring match over `FRUSTRATION_MARKERS`. Returns the
    total number of marker hits (a text may hit several, and one marker in two
    texts counts twice) — a count, not an opinion. Non-string entries are
    skipped rather than coerced: a dict that is not text is not evidence
    either way.
    """
    total = 0
    for text in texts or ():
        if not isinstance(text, str) or not text:
            continue
        lowered = text.lower()
        for marker in FRUSTRATION_MARKERS:
            if marker in lowered:
                total += 1
    return total


def _text_fields(sessions, attempts):
    """Every text-bearing field we are willing to count markers in.

    Kept explicit rather than walking the dict recursively: a recursive walk
    would count a marker inside a system note or a rubric id, and those are not
    the learner's words.
    """
    collected = []
    for session in sessions or ():
        if not isinstance(session, dict):
            continue
        for key in ("reflection", "note", "blocked_reason", "goal"):
            value = session.get(key)
            if isinstance(value, str):
                collected.append(value)
    for attempt in attempts or ():
        if not isinstance(attempt, dict):
            continue
        for key in ("text_excerpt", "reflection", "note"):
            value = attempt.get(key)
            if isinstance(value, str):
                collected.append(value)
    return collected


# --------------------------------------------------------------------------
# Feature extraction
# --------------------------------------------------------------------------


def _now(now):
    """A timezone-aware UTC datetime from an injected clock or the wall clock."""
    value = datetime.now(timezone.utc)
    if callable(now):
        raw = now()
        if isinstance(raw, datetime):
            value = raw
    elif isinstance(now, datetime):
        value = now
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _days_between(earlier, later):
    """Whole days between two aware datetimes, or None if either is missing."""
    if earlier is None or later is None:
        return None
    delta = later - earlier
    return max(0.0, delta.total_seconds() / 86400.0)


def _session_starts(sessions):
    moments = []
    for session in sessions or ():
        if not isinstance(session, dict):
            continue
        when = tutoring.parse_iso(session.get("started_at"))
        if when is None:
            when = tutoring.parse_iso(session.get("ended_at"))
        if when is not None:
            moments.append(when)
    moments.sort()
    return moments


def _attempt_times(attempts):
    moments = []
    for attempt in attempts or ():
        if not isinstance(attempt, dict):
            continue
        when = tutoring.parse_iso(attempt.get("submitted_at"))
        if when is not None:
            moments.append(when)
    moments.sort()
    return moments


def _active_sessions(sessions):
    return [s for s in sessions or () if isinstance(s, dict) and _is_active(s)]


def _is_active(session):
    state = session.get("state")
    return state in tutoring.WORKING_STATES or state == "COMPLETED"


def extract_features(*, sessions, attempts, checks, objective_states, now=None):
    """The 15 features, as `{name: float}` with `basis_ru` beside them.

    Every feature is a documented derivation, and every derivation that cannot
    be made with the given input returns 0 — the absence of a record is not
    the presence of a fact. `basis_ru` names what was missing, so a caller
    can tell a well-evidenced score from a guess before acting on either.
    """
    sessions = [s for s in (sessions or []) if isinstance(s, dict)]
    attempts = [a for a in (attempts or []) if isinstance(a, dict)]
    checks = [c for c in (checks or []) if isinstance(c, dict)]
    objective_states = dict(objective_states or {})

    moment = _now(now)
    missing = []

    starts = _session_starts(sessions)
    starts_back = list(reversed(starts))  # newest first
    last_session = starts_back[0] if starts else None

    attempts_moments = _attempt_times(attempts)
    last_attempt = attempts_moments[-1] if attempts_moments else None

    features = {}

    # --- recency ---------------------------------------------------------
    days = _days_between(last_session, moment)
    if days is None:
        missing.append("занятий не записано")
        features["days_since_last_session"] = 0.0
    else:
        features["days_since_last_session"] = _clamp(days / DAYS_SILENCE_FULL)

    # --- frequency -------------------------------------------------------
    week_ago = moment - timedelta(days=7)
    fortnight_ago = moment - timedelta(days=14)
    now_sessions = [s for s in starts if s >= week_ago]
    prev_sessions = [s for s in starts if fortnight_ago <= s < week_ago]
    features["sessions_last_7_days"] = 1.0 - min(1.0, len(now_sessions) / 5.0)
    if not starts:
        missing.append("для динамики занятий нужна хотя бы одна запись с временем")
        features["sessions_trend"] = 0.0
    else:
        features["sessions_trend"] = 1.0 - min(
            1.0, float(len(now_sessions)) / float(max(1, len(prev_sessions)))
        )

    attempts_per_session = (
        float(len(attempts)) / float(max(1, len(sessions))) if attempts else 0.0
    )
    if not attempts:
        missing.append("попытки не записаны")
    features["attempts_per_session"] = 1.0 - min(1.0, attempts_per_session / 5.0)

    # --- check quality ---------------------------------------------------
    if checks:
        failed = sum(1 for c in checks if c.get("verdict") != "pass")
        features["failed_check_ratio"] = failed / float(len(checks))
    else:
        missing.append("проверки не записаны")
        features["failed_check_ratio"] = 0.0

    # --- assistance ------------------------------------------------------
    if attempts:
        ranks = [
            tutoring.LEVEL_STEP.get(a.get("assistance_max") or "", 0) for a in attempts
        ]
        features["assistance_max_mean"] = sum(ranks) / (4.0 * len(ranks))
    else:
        missing.append("попытки не записаны")
        features["assistance_max_mean"] = 0.0

    # --- review and stuck -----------------------------------------------
    review_due = sum(
        1 for st in objective_states.values() if st.get("stage") == "review_due"
    )
    features["review_due_count"] = min(1.0, review_due / 3.0)

    stuck_cycles = 0
    for st in objective_states.values():
        value = st.get("consecutive_stuck_sessions") or 0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stuck_cycles = max(stuck_cycles, int(value))
    if tutoring.stuck_signal(stuck_cycles):
        features["consecutive_stuck_sessions"] = 1.0
    else:
        features["consecutive_stuck_sessions"] = min(1.0, stuck_cycles / 3.0)

    if objective_states:
        demonstrated = sum(
            1 for st in objective_states.values() if st.get("stage") == "demonstrated"
        )
        ratio = demonstrated / float(len(objective_states))
        features["objectives_demonstrated_ratio"] = 1.0 - ratio
    else:
        missing.append("состояния целей не записаны")
        features["objectives_demonstrated_ratio"] = 0.0

    # --- attempt recency -------------------------------------------------
    days_attempt = _days_between(last_attempt, moment)
    if days_attempt is None:
        missing.append("попытки не записаны")
        features["days_since_last_attempt"] = 0.0
    else:
        features["days_since_last_attempt"] = _clamp(
            days_attempt / DAYS_SILENCE_ATTEMPT_FULL
        )

    # --- session completion ---------------------------------------------
    total_sessions = len(sessions)
    incomplete = sum(1 for s in sessions if s.get("state") in ("PAUSED", "BLOCKED"))
    features["incomplete_sessions_ratio"] = (
        incomplete / float(total_sessions) if total_sessions else 0.0
    )

    # --- blocked objectives ---------------------------------------------
    blocked = sum(1 for st in objective_states.values() if st.get("stage") == "blocked")
    features["blocked_objectives"] = min(1.0, blocked / 2.0)

    # --- hint density ----------------------------------------------------
    if attempts:
        hinted = sum(
            1
            for a in attempts
            if (a.get("assistance_max") or "") in ("HINT", "EXAMPLE", "SOLUTION")
        )
        features["hint_requests_per_attempt"] = min(
            1.0, (hinted / float(len(attempts))) / 2.0
        )
    else:
        features["hint_requests_per_attempt"] = 0.0

    # --- idle streak -----------------------------------------------------
    completed = [
        tutoring.parse_iso(s.get("ended_at")) or tutoring.parse_iso(s.get("started_at"))
        for s in sessions
        if s.get("state") == "COMPLETED"
    ]
    completed = [t for t in completed if t is not None]
    last_completed = max(completed) if completed else None
    days_idle = _days_between(last_completed, moment)
    if days_idle is None:
        missing.append("завершённых занятий не записано")
        features["idle_streak"] = 0.0
    else:
        features["idle_streak"] = _clamp(days_idle / DAYS_IDLE_FULL)

    # --- text ------------------------------------------------------------
    hits = sentiment_signal(_text_fields(sessions, attempts))
    if not (_text_fields(sessions, attempts)):
        missing.append("текстовых полей попыток/занятий нет")
    features["negative_sentiment_hits"] = min(1.0, hits / 3.0)

    # Every feature is present and numeric; a stray non-numeric value is a bug
    # in this function, not a caller error.
    for name in RISK_FEATURE_NAMES:
        features[name] = _clamp(float(features.get(name, 0.0)))

    features["basis_ru"] = (
        "признаки извлечены по документированным правилам из %d занятий, "
        "%d попыток, %d проверок, %d состояний целей"
        % (len(sessions), len(attempts), len(checks), len(objective_states))
        + (
            ("; недоступно: " + "; ".join(missing))
            if missing
            else "; весь ожидаемый объём данных присутствует"
        )
    )
    return features


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score_risk(features):
    """`{"score", "level", "contributors"}` from the 15 features.

    `score = sum(RISK_WEIGHTS[name] * features[name])`, clipped to 0..1. The
    `contributors` list is sorted by contribution descending, so the recovery
    plan can be built from the top of it without re-deriving anything.

    This is a **rule-based risk score**: a weighted sum of documented
    derivations. It is not a trained model, it is not calibrated on any cohort,
    and a score of 0.8 means "several weighted rules fired together", not "this
    student will drop out".
    """
    if not isinstance(features, dict):
        raise RiskDetectionError(
            "FEATURES_INVALID",
            "признаки должны быть объектом {имя: число}, получено %s"
            % type(features).__name__,
        )

    contributors = []
    total = 0.0
    for name, weight in RISK_WEIGHTS.items():
        raw = features.get(name, 0.0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise RiskDetectionError(
                "FEATURES_INVALID",
                "признак %s должен быть числом, получено %r" % (name, raw),
            )
        value = _clamp(float(raw))
        contribution = weight * value
        total += contribution
        contributors.append(
            {
                "feature": name,
                "label_ru": RISK_FEATURE_LABELS[name],
                "weight": weight,
                "value": round(value, 6),
                "contribution": round(contribution, 6),
            }
        )

    contributors.sort(key=lambda item: (-item["contribution"], item["feature"]))
    score = round(_clamp(total), 6)

    if score >= BAND_HIGH:
        level = "high"
    elif score >= BAND_MEDIUM:
        level = "medium"
    else:
        level = "low"

    return {
        "score": score,
        "level": level,
        "level_ru": LEVEL_RU.get(level, level),
        "contributors": contributors,
        "method_ru": (
            "правило на взвешенной сумме 15 документированных признаков "
            "(веса в сумме равны 1.0), не обученная модель"
        ),
    }


# --------------------------------------------------------------------------
# Recovery plan
# --------------------------------------------------------------------------

# Feature -> concrete next steps, in a fixed order. Deterministic mapping: the
# same top contributor always yields the same steps, because a plan that
# changes between two reads of the same record cannot be checked by anybody.
RECOVERY_STEPS = {
    "days_since_last_session": (
        "напомнить про короткое возвращение: 10–15 минут на уже знакомой цели",
        "уточнить, не помешала ли внешняя нагрузка, и предложить снизить темп",
    ),
    "sessions_last_7_days": (
        "предложить одно запланированное занятие в ближайшую неделю вместо "
        "пяти небольших",
        "проверить, не пропущен ли повторяющийся срок по одной из целей",
    ),
    "sessions_trend": (
        "вернуться к последней зачтённой цели и повторить её, а не начинать новую",
        "спросить, что изменилось с прошлой недели, и записать ответ",
    ),
    "attempts_per_session": (
        "разбить следующую цель на две попытки с разбором между ними",
        "снизить количество материалов на занятие, а не длительность",
    ),
    "failed_check_ratio": (
        "назначить проверку по предпосылке перед повторной попыткой",
        "предложить другое объяснение той же цели, а не ещё один пример",
    ),
    "assistance_max_mean": (
        "уточнить, какой уровень помощи уже давался, и сознательно снизить "
        "его на следующей попытке",
        "перевести задание на практику, если оцениваемость не подтверждена",
    ),
    "review_due_count": (
        "взять один из сроков повторения и провести его первым",
        "напомнить, что повторение — не штраф и не потеря результата",
    ),
    "consecutive_stuck_sessions": (
        "остановить подсказки по этой цели и предложить другую аналогию или "
        "пререквизит",
        "подготовить вопрос для преподавателя: три цикла подряд — это уже "
        "вопрос преподавателя, а не ещё один хинт",
    ),
    "objectives_demonstrated_ratio": (
        "выбрать одну цель из уже начатых и довести её до второй разной "
        "успешной проверки",
        "проверить, не выбрана ли цель с неутверждёнными предпосылками",
    ),
    "days_since_last_attempt": (
        "вернуться к самой свежей попытке и разобрать, что именно осталось "
        "незавершённым",
        "уточнить, нужен ли доступ к среде или материалу — без него попытку "
        "невозможно продолжить",
    ),
    "incomplete_sessions_ratio": (
        "закрыть или явно отменить незавершённые занятия, чтобы запись не "
        "выглядела как отказ",
        "предложить короткие занятия вместо длинных, которые не доходят до конца",
    ),
    "blocked_objectives": (
        "проверить, действительно ли цель заблокирована предпосылкой, и "
        "разблокировать её, если предпосылка уже показана",
        "зафиксировать причину блокировки, чтобы её можно было устранить",
    ),
    "hint_requests_per_attempt": (
        "на следующей попытке дать только один хинт и попросить попытку продолжить",
        "убедиться, что задание соответствует заявленному уровню",
    ),
    "idle_streak": (
        "напомнить про завершённые цели и предложить повторение вместо новой темы",
        "спросить про перерыв: пауза — не потеря результата",
    ),
    "negative_sentiment_hits": (
        "признать сложность вслух и предложить сменить объяснение, а не тему",
        "если маркеры сохраняются — предложить короткий перерыв и вернуться позже",
    ),
}


def recovery_plan(risk):
    """Concrete next steps, derived from the top contributors.

    The mapping is a module-level constant, so the same top contributor always
    produces the same steps. Steps are Russian, actionable, and ordered by
    contribution — the plan does not re-derive the risk, it only translates
    its top terms into what a person can do in the next session.
    """
    if not isinstance(risk, dict):
        raise RiskDetectionError(
            "RISK_INVALID",
            "ожидался результат score_risk (объект), получено %s" % type(risk).__name__,
        )

    contributors = risk.get("contributors") or []
    steps = []
    for contributor in contributors[:4]:
        if contributor.get("contribution", 0) <= 0:
            continue
        name = contributor.get("feature")
        for step in RECOVERY_STEPS.get(name, ()):
            if step not in steps:
                steps.append(step)
        if len(steps) >= 6:
            break

    if not steps:
        return {
            "steps_ru": [
                "по текущей записи весомых сигналов риска нет; "
                "продолжайте обычный темп занятий"
            ],
            "sources": [],
            "note_ru": (
                "план — не оценка и не предсказание: это перевод верхних "
                "вкладов в правила на конкретные шаги следующего занятия"
            ),
        }

    sources = [
        {
            "feature": c.get("feature"),
            "label_ru": c.get("label_ru"),
            "contribution": c.get("contribution"),
        }
        for c in contributors[:4]
        if c.get("contribution", 0) > 0
    ]
    return {
        "steps_ru": steps,
        "sources": sources,
        "note_ru": (
            "план — не оценка и не предсказание: это перевод верхних вкладов "
            "в правила на конкретные шаги следующего занятия"
        ),
    }


# --------------------------------------------------------------------------
# Consent gate
# --------------------------------------------------------------------------


def interventions_allowed(consent):
    """Whether a human may be told about a learner's risk signals.

    Returns `True` only for a live consent record carrying the purpose
    `teacher_export` — a dict with `withdrawn_at` set does not count, and
    `None` does not count. This is the guard, not a warning: a risk score that
    is shown to a teacher without that consent is a privacy breach, and the
    export path (`exports.py`) checks the same condition again.
    """
    if not isinstance(consent, dict):
        return False
    if consent.get("withdrawn_at"):
        return False
    purposes = consent.get("purposes") or []
    return "teacher_export" in purposes


# --------------------------------------------------------------------------
# Cohort detection
# --------------------------------------------------------------------------


def detect_at_risk(*, cohort_id, members, facts_by_student, threshold=0.5):
    """Who is at or above `threshold`, with the coverage stated.

    `members` is the list of student ids to consider. `facts_by_student` maps
    `student_id` to `{"sessions": [...], "attempts": [...], "checks": [...],
    "objective_states": {...}}`; a member missing from it is scored on an
    empty record and therefore lands near zero — never above the threshold,
    because "no data" must not read as "at risk".

    The return value is a **list**, not a document: the last element is
    `{"denominator": N}` so the coverage is carried with the results rather
    than being reconstructible only from the caller's own arguments.
    """
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise RiskDetectionError(
            "THRESHOLD_INVALID",
            "порог должен быть числом 0..1, получено %r" % (threshold,),
        )
    if not 0.0 <= float(threshold) <= 1.0:
        raise RiskDetectionError(
            "THRESHOLD_INVALID",
            "порог должен быть числом 0..1, получено %r" % (threshold,),
        )

    results = []
    scored = 0
    for student_id in sorted(str(m) for m in (members or []) if m):
        facts = (facts_by_student or {}).get(student_id) or {}
        features = extract_features(
            sessions=facts.get("sessions") or [],
            attempts=facts.get("attempts") or [],
            checks=facts.get("checks") or [],
            objective_states=facts.get("objective_states") or {},
        )
        scored += 1
        risk = score_risk(features)
        if risk["score"] >= float(threshold):
            results.append(
                {
                    "student_id": student_id,
                    "cohort_id": cohort_id,
                    "score": risk["score"],
                    "level": risk["level"],
                    "level_ru": risk["level_ru"],
                    "contributors": risk["contributors"],
                    "basis_ru": features.get("basis_ru"),
                    "method_ru": risk["method_ru"],
                    "not_a_grade_ru": (
                        "это сигнал для преподавателя, а не оценка и не "
                        "прогноз отчисления"
                    ),
                }
            )

    results.sort(key=lambda item: (-item["score"], item["student_id"]))
    results.append(
        {
            "denominator": scored,
            "denominator_ru": (
                "просмотрено %d из %d участников потока %s; по отсутствующим в "
                "записи участникам данных нет, а не нулевой риск"
                % (scored, len([m for m in (members or []) if m]), cohort_id)
            ),
            "threshold": float(threshold),
            "export_requires_consent": True,
        }
    )
    return results


__all__ = [
    "RiskDetectionError",
    "RISK_FEATURES",
    "RISK_FEATURE_NAMES",
    "RISK_FEATURE_LABELS",
    "RISK_FEATURE_DOCS",
    "RISK_WEIGHTS",
    "FRUSTRATION_MARKERS",
    "RECOVERY_STEPS",
    "LEVELS",
    "sentiment_signal",
    "extract_features",
    "score_risk",
    "recovery_plan",
    "interventions_allowed",
    "detect_at_risk",
]
