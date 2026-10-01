# -*- coding: utf-8 -*-
"""Opt-in, anonymous comparison bands — and nothing that could become a rank.

The design's §8.3 asks for leaderboards and names four constraints: opt-in only,
anonymous ranks ("you're in the top 30%", not "23rd of 100"), several
dimensions, and personal bests. This module implements those four and removes
the part the design inherits from every leaderboard it ever shipped: an ordered
list of names.

The removal is structural, not stylistic. `LeaderboardConfig.anonymous` is a
field that refuses `False` in `__post_init__`, `build_leaderboard` returns
`band_for` — a mapping from student id to a band string, and *no* ordered
collection of ids — and `render_leaderboard` renders only the asking learner's
own band and personal best. There is no return path in this module that yields
a position number next to somebody else's name, so a caller cannot add one
later without writing new code that says so.

Three further constraints:

* **Opt-in is the only way in.** A student absent from `opted_in` is excluded
  from every dimension *and* from the denominator. Asking for a
  non-opted-in student's standing raises
  `LeaderboardError("LEADERBOARD_NOT_OPTED_IN", ...)`: the fact is not merely
  withheld, the request is refused, so a caller cannot accidentally treat an
  absent learner as a zero.
* **Badges are not reinvented here.** Where a dimension is genuinely about
  recognised work, it defers to `personas.evaluate_achievements` — the one
  place with a rule per badge and an evidence list per award. This module adds
  no badge of its own, because a second badge rule is a second answer to "what
  counts as evidence", and the two would disagree.
* **Nothing here is a grade.** `leaderboards.excluded_from_grade_ru` says so on
  every document, and the cohort contract this shape mirrors already encodes
  `anonymous: const true`.

The competition this module *does* keep is with oneself: `personal_best` and
`most_improved` compare one learner against their own history, where a better
number is unambiguously good and nobody else is implicated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, personas

SCHEMA_VERSION = 2

CONFIG_FILENAME_SUFFIX = ".json"

# The four dimensions from the design, with Russian titles.
DIMENSIONS = (
    "fastest_learner",
    "most_helpful_peer",
    "best_code_quality",
    "most_improved",
)

DIMENSION_TITLES_RU = {
    "fastest_learner": "Быстрое освоение",
    "most_helpful_peer": "Помощь сокурсникам",
    "best_code_quality": "Качество кода",
    "most_improved": "Наибольший прирост",
}

# Which fact each dimension reads out of `facts_by_student`, and whether a
# *higher* value is better. Declared here rather than in a dict comprehension
# inside the function so the four definitions are visible side by side and
# cannot drift apart from the titles.
#
# `most_improved` is not a fact key: it is derived from the learner's own
# history (first demonstrated count to last), so its reader gets
# `{"history": [...]}` instead.
DIMENSION_SOURCE = {
    "fastest_learner": ("demonstrated_per_session", True),
    "most_helpful_peer": ("help_given_count", True),
    "best_code_quality": ("passed_check_ratio", True),
    "most_improved": ("history", True),
}

BANDS = (
    (70.0, "top 30%"),
    (50.0, "top 50%"),
    (0.0, "bottom 50%"),
)

BAND_TITLES_RU = {
    "top 30%": "топ-30%",
    "top 50%": "верхние 50%",
    "bottom 50%": "нижние 50%",
}


class LeaderboardError(RuntimeError):
    """A refused leaderboard operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass
class LeaderboardConfig:
    """Which dimensions are on for one cohort, and who opted in.

    `anonymous=True` is a constant, and the constructor enforces it. A config
    that could carry `anonymous: False` would be a config a future caller could
    fill in, and the cohort contract this mirrors uses `const true` for exactly
    the reason: the format refuses named ranking rather than promising not to
    do it.

    `enabled` is a subset of `DIMENSIONS`; the empty subset is the default
    posture — all comparison off until a cohort asks for it.
    """

    cohort_id: str
    enabled: tuple = ()
    anonymous: bool = True
    opted_in: tuple = ()
    title: str = ""

    def __post_init__(self):
        if not self.cohort_id or not str(self.cohort_id).strip():
            raise LeaderboardError(
                "COHORT_ID_REQUIRED",
                "у конфигурации таблицы должен быть идентификатор потока",
            )

        if self.anonymous is not True:
            raise LeaderboardError(
                "NAMED_LEADERBOARD_REFUSED",
                "таблица с именами не предусмотрена по устройству: "
                "`anonymous` — константа, а не переключатель. Сравнение "
                "возвращает полосу («топ-30%»), а не место в списке.",
            )

        enabled = tuple(self.enabled or ())
        unknown = [d for d in enabled if d not in DIMENSIONS]
        if unknown:
            raise LeaderboardError(
                "DIMENSION_UNKNOWN",
                "неизвестные измерения: %s (допустимы: %s)"
                % (", ".join(str(d) for d in unknown), ", ".join(DIMENSIONS)),
            )
        self.enabled = enabled

        # Opt-in is a set: a duplicated id means a caller appended twice, not
        # that the learner opted in twice.
        self.opted_in = tuple(sorted({str(s) for s in (self.opted_in or ()) if s}))

        # A cohort that never opted in has every comparison off, whatever a
        # document claims. This is the same "accept and ignore is a lie"
        # problem `personas.ensure_no_competitive_reward` refuses.
        if not self.opted_in:
            self.enabled = ()
        self.anonymous = True

    # -- serialisation -----------------------------------------------------

    def to_document(self):
        """The stored shape. Note the key names: none of them is `rank` or
        `leaderboard`, and `personas.ensure_no_competitive_reward` refuses a
        settings document containing those — this shape stays on the allowed
        side of that refusal on purpose."""
        return {
            "schema_version": SCHEMA_VERSION,
            "cohort_id": str(self.cohort_id),
            "title": str(
                self.title or DIMENSION_TITLES_RU.get((self.enabled or ("",))[0], "")
            ),
            "enabled": list(self.enabled),
            "anonymous": True,
            "opted_in": list(self.opted_in),
            "excluded_from_grade_ru": (
                "сравнение не входит в оценку курса и не влияет на помощь"
            ),
        }

    @classmethod
    def from_document(cls, document):
        if not isinstance(document, dict):
            raise LeaderboardError(
                "CONFIG_INVALID",
                "конфигурация таблицы должна быть объектом, получено %s"
                % type(document).__name__,
            )
        unknown = [
            k
            for k in document
            if k
            not in (
                "schema_version",
                "cohort_id",
                "title",
                "enabled",
                "anonymous",
                "opted_in",
                # `to_document` writes this disclaimer, so a document this
                # module produced must read back. Leaving it out made every
                # `save_config` -> `load_config` round trip fail with
                # CONFIG_UNKNOWN_FIELDS on the module's own output.
                "excluded_from_grade_ru",
            )
        ]
        if unknown:
            raise LeaderboardError(
                "CONFIG_UNKNOWN_FIELDS",
                "в конфигурации таблицы есть неизвестные поля: %s. "
                "Таблица лидеров не хранит места, имён и сравнений с другими."
                % ", ".join(sorted(unknown)),
            )
        return cls(
            cohort_id=str(document.get("cohort_id") or ""),
            enabled=tuple(document.get("enabled") or ()),
            anonymous=document.get("anonymous", True),
            opted_in=tuple(document.get("opted_in") or ()),
            title=str(document.get("title") or ""),
        )


def config_path(root, cohort_id):
    """`<cohorts_dir>/<cohort_id>.leaderboard.json`, with the id validated."""
    name = paths.safe_name(cohort_id, kind="идентификатор потока")
    base = cohorts_dir(root)
    try:
        target = paths.ensure_within(
            base, base / ("%s.leaderboard%s" % (name, CONFIG_FILENAME_SUFFIX))
        )
    except paths.PathError as e:
        raise LeaderboardError(e.code, e.message)
    return Path(target)


def cohorts_dir(root=None):
    """`<root>/cohorts` when that directory exists, else the repo's.

    The same resolver `cohort_management` uses, re-derived rather than
    imported: this module must not depend on that module's private surface,
    and two copies of a two-line path rule is cheaper than a cycle.
    """
    if root:
        candidate = Path(root) / "cohorts"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "cohorts"


def load_config(root, cohort_id):
    """Read a stored configuration. A missing file is the default posture:
    every comparison off and nobody opted in."""
    path = config_path(root, cohort_id)
    if not path.is_file():
        return LeaderboardConfig(cohort_id=str(cohort_id), enabled=(), opted_in=())
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise LeaderboardError(
            "CONFIG_UNREADABLE",
            "не удалось прочитать конфигурацию %s: %s" % (path, e),
        )
    return LeaderboardConfig.from_document(document)


def save_config(root, config):
    """Validate and write a configuration. Returns the path."""
    if not isinstance(config, LeaderboardConfig):
        raise LeaderboardError(
            "CONFIG_INVALID",
            "ожидался объект LeaderboardConfig, получено %s" % type(config).__name__,
        )
    document = config.to_document()
    path = config_path(root, config.cohort_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


# --------------------------------------------------------------------------
# Opt-in
# --------------------------------------------------------------------------


def is_opted_in(config, student_id):
    """Whether this learner has opted into comparison."""
    return str(student_id) in tuple(config.opted_in or ())


def opt_in(config, student_id):
    """A copy of `config` with `student_id` added to the opt-in set.

    Copy, not mutation: a configuration handed to another caller must not
    change under them, and an opt-in is a decision that should be written
    through `save_config` deliberately.
    """
    if not student_id or not str(student_id).strip():
        raise LeaderboardError(
            "STUDENT_ID_INVALID", "идентификатор обучающегося обязателен"
        )
    current = {str(s) for s in (config.opted_in or ())}
    current.add(str(student_id))
    # A learner who opted in has not thereby enabled the dimensions; the
    # cohort's teacher or the learner turns each dimension on separately.
    return LeaderboardConfig(
        cohort_id=config.cohort_id,
        enabled=tuple(config.enabled),
        anonymous=True,
        opted_in=tuple(sorted(current)),
        title=config.title,
    )


def opt_out(config, student_id):
    """A copy of `config` with `student_id` removed from the opt-in set.

    Removal is immediate and complete: the id leaves the denominator as well
    as the bands, so a learner who withdraws cannot still be counted in "из N
    участников" behind an anonymous band.
    """
    current = {s for s in (config.opted_in or ()) if s != str(student_id)}
    enabled = config.enabled
    if not current:
        enabled = ()
    return LeaderboardConfig(
        cohort_id=config.cohort_id,
        enabled=tuple(enabled),
        anonymous=True,
        opted_in=tuple(sorted(current)),
        title=config.title,
    )


def require_opted_in(config, student_id):
    """Refuse a standing request for a learner who did not opt in."""
    if not is_opted_in(config, student_id):
        raise LeaderboardError(
            "LEADERBOARD_NOT_OPTED_IN",
            "обучающийся %s не давал согласия на сравнение и не участвует ни в "
            "одном измерении, включая знаменатель. Сравнение — по выбору "
            "ученика, а не по умолчанию." % student_id,
        )
    return True


# --------------------------------------------------------------------------
# Self-competition
# --------------------------------------------------------------------------


def _history_points(history):
    """`[{value, at}]` from a raw history, sorted by time when timestamps exist.

    Two accepted shapes: a list of numbers (a learner with no timestamps), or a
    list of `{"value": ..., "at": ...}`. Both are deterministic; the second is
    preferred because `personal_best` needs a date to report.
    """
    points = []
    for index, entry in enumerate(history or ()):
        if isinstance(entry, bool):
            continue
        if isinstance(entry, (int, float)):
            points.append({"value": float(entry), "at": None, "index": index})
        elif isinstance(entry, dict):
            value = entry.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            points.append(
                {"value": float(value), "at": entry.get("at"), "index": index}
            )
    # Stable: ties keep their recorded order.
    points.sort(key=lambda p: (p["at"] is None, str(p["at"] or ""), p["index"]))
    return points


def most_improved(*, student_history):
    """Improvement delta between the first and last point of one learner's own
    history. A comparison with oneself, which is the only kind this module
    keeps."""
    points = _history_points(student_history)
    if len(points) < 2:
        return {
            "delta": None,
            "first_value": points[0]["value"] if points else None,
            "last_value": points[-1]["value"] if points else None,
            "points": len(points),
            "reason_ru": (
                "нужно минимум две точки истории, чтобы сказать о приросте; "
                "одна точка — это не изменение"
            ),
        }
    return {
        "delta": round(points[-1]["value"] - points[0]["value"], 6),
        "first_value": points[0]["value"],
        "last_value": points[-1]["value"],
        "first_at": points[0]["at"],
        "last_at": points[-1]["at"],
        "points": len(points),
        "reason_ru": "прирост между первой и последней записью собственной истории",
    }


def personal_best(history):
    """One learner's own best value, when, and by how much it beat the runner-up.

    Never anybody else's number. `delta_vs_previous` is the gap to the second
    best own value, which is the only comparison a learner can be shown without
    making somebody else a reference point.
    """
    points = _history_points(history)
    if not points:
        return {
            "best_value": None,
            "achieved_at": None,
            "delta_vs_previous": None,
            "points": 0,
            "reason_ru": "записей ещё нет: личный рекорд появляется после первой",
        }

    ordered = sorted(points, key=lambda p: (-p["value"], p["index"]))
    best = ordered[0]
    delta = None
    if len(ordered) > 1:
        delta = round(best["value"] - ordered[1]["value"], 6)
    return {
        "best_value": best["value"],
        "achieved_at": best["at"],
        "delta_vs_previous": delta,
        "points": len(points),
        "reason_ru": (
            "сравнение только с собственными записями: это рекорд, а не место "
            "в общем списке"
        ),
    }


# --------------------------------------------------------------------------
# Bands
# --------------------------------------------------------------------------


def _band_for(percentile):
    for limit, name in BANDS:
        if percentile >= limit:
            return name
    return BANDS[-1][1]


def _dimension_value(config, dimension, facts):
    """One learner's value on one dimension, or `None` when it is unknown."""
    source, higher_is_better = DIMENSION_SOURCE[dimension]

    if source == "history":
        facts = (
            facts if isinstance(facts, (list, tuple)) else (facts or {}).get("history")
        )
        improved = most_improved(student_history=facts)
        value = improved.get("delta")
        if value is None:
            return None, higher_is_better
        return float(value), higher_is_better

    facts = facts if isinstance(facts, dict) else {}
    value = facts.get(source)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, higher_is_better
    return float(value), higher_is_better


def build_leaderboard(config, *, facts_by_student, dimension):
    """Bands for one dimension. Never an ordered list of names.

    `facts_by_student` maps `student_id` to the fact mapping for the dimension
    (see `DIMENSION_SOURCE`). Students who did not opt in are excluded from the
    bands *and* from `denominator`. Students who opted in but have no value for
    the dimension appear with `bottom 50%` and a note — excluded from the
    percentile computation is not an option, because a learner who opted in
    should see a band rather than a silence they cannot interpret.

    The return value has `band_for` (id -> band string) and `you` (the asking
    learner's own band, when `learner_id` is given). It has no list, so a
    caller has nothing to sort into a ranking.
    """
    if dimension not in DIMENSIONS:
        raise LeaderboardError(
            "DIMENSION_UNKNOWN",
            "неизвестное измерение: %r (допустимы: %s)"
            % (dimension, ", ".join(DIMENSIONS)),
        )
    if dimension not in tuple(config.enabled or ()):
        raise LeaderboardError(
            "DIMENSION_DISABLED",
            "измерение %r (%s) не включено для потока: сравнение включается "
            "явно и по умолчанию выключено."
            % (dimension, DIMENSION_TITLES_RU.get(dimension, "")),
        )

    opted_in = sorted({s for s in (config.opted_in or ()) if s})
    if not opted_in:
        raise LeaderboardError(
            "LEADERBOARD_EMPTY",
            "никто не дал согласия на сравнение в потоке %s: таблица не "
            "строится, а не показывается пустой" % config.cohort_id,
        )

    measured = {}
    unknown = []
    for student_id in opted_in:
        value, higher_is_better = _dimension_value(
            config, dimension, (facts_by_student or {}).get(student_id)
        )
        if value is None:
            unknown.append(student_id)
        else:
            measured[student_id] = (value, higher_is_better)

    if not measured:
        raise LeaderboardError(
            "DIMENSION_NO_DATA",
            "по измерению %r нет ни одного измеренного значения среди "
            "%d согласившихся: сравнивать нечего" % (dimension, len(opted_in)),
        )

    # Ranking is over values only, and never leaves this function: the sorted
    # list of ids computed here is used to derive percentiles and is then
    # discarded. Nothing downstream can turn it into a public list, because
    # nothing downstream receives it.
    ordered = sorted(measured, key=lambda sid: (-measured[sid][0], str(sid)))
    higher_is_better = measured[ordered[0]][1]
    count = len(ordered)

    band_for = {}
    for index, student_id in enumerate(ordered):
        percentile = 100.0 * (count - index) / float(count)
        if not higher_is_better:
            percentile = 100.0 * (index + 1) / float(count)
        band_for[student_id] = _band_for(percentile)

    for student_id in unknown:
        # Present, with the lowest band and an explicit note. `bottom 50%` here
        # is the honest reading: no measured value, so nothing supports a
        # better claim.
        band_for[student_id] = "bottom 50%"

    document = {
        # `render_leaderboard` re-checks the opt-in against the document's own
        # cohort id, so the id has to travel with it: without it every render
        # of a freshly built document died on COHORT_ID_REQUIRED.
        "cohort_id": config.cohort_id,
        "dimension": dimension,
        "title_ru": DIMENSION_TITLES_RU.get(dimension, dimension),
        "band_for": band_for,
        "denominator": count,
        "denominator_ru": (
            "полосы посчитаны по %d из %d участников потока, давших согласие "
            "на сравнение; остальные не учитываются вовсе" % (count, len(opted_in))
        ),
        "anonymous": True,
        "ordered_names_returned": False,
        "excluded_from_grade_ru": (
            "сравнение не входит в оценку курса и не влияет на объём помощи"
        ),
        "unknown_values": sorted(unknown),
        "band_titles_ru": dict(BAND_TITLES_RU),
    }
    return document


# --------------------------------------------------------------------------
# Badges — delegated, never reinvented
# --------------------------------------------------------------------------


def evaluate_badges(*, facts, existing, learner_id, course_id, awarded_at):
    """Delegate to `personas.evaluate_achievements`.

    The single entry point to badges in this module, and it is a pure
    delegation on purpose. The achievement rules — five of them, each with a
    declared evidence requirement and a revocation path — live in
    `personas.py`, and a leaderboard that defined its own badge rule would be
    a second, weaker answer to "what counts as evidence".
    """
    return personas.evaluate_achievements(
        facts,
        existing=existing,
        learner_id=learner_id,
        course_id=course_id,
        awarded_at=awarded_at,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_leaderboard(document, *, learner_id):
    """Russian Markdown showing only the asking learner's own standing.

    Deliberately not a list. A leaderboard rendered as a table of names is a
    leaderboard that will be screenshotted and read as a ranking, whatever the
    underlying data said; the only version of this feature that survives
    contact with a classroom is the one that cannot be read as a ranking.
    """
    if not isinstance(document, dict):
        raise LeaderboardError(
            "DOCUMENT_INVALID",
            "документ таблицы должен быть объектом, получено %s"
            % type(document).__name__,
        )

    require_opted_in(
        LeaderboardConfig(
            cohort_id=document.get("cohort_id") or "",
            enabled=(document.get("dimension"),),
            opted_in=tuple((document.get("band_for") or {}).keys()),
        ),
        learner_id,
    )

    bands = document.get("band_for") or {}
    band = bands.get(str(learner_id))
    title = BAND_TITLES_RU.get(band or "", band or "нет данных")

    lines = []
    lines.append("# Сравнение по измерению «%s»" % document.get("title_ru"))
    lines.append("")
    lines.append(
        "> Сравнение анонимное и добровольное: здесь нет списка "
        "участников и нет мест. Показана только ваша полоса и ваш "
        "собственный рекорд."
    )
    lines.append("")
    lines.append("- ваша полоса: **%s**" % title)
    lines.append(
        "- знаменатель: %d участников, давших согласие"
        % (document.get("denominator") or 0)
    )
    lines.append("- %s" % document.get("denominator_ru"))
    lines.append("")

    best = document.get("personal_best")
    if best:
        lines.append("## Ваш личный рекорд")
        lines.append("")
        achieved = best.get("achieved_at") or "дата не записана"
        lines.append("- лучшее значение: %s" % best.get("best_value"))
        lines.append("- достигнуто: %s" % achieved)
        delta = best.get("delta_vs_previous")
        lines.append(
            "- к предыдущему результату: %s"
            % ("нет предыдущего" if delta is None else delta)
        )
        lines.append("")
        lines.append(
            "> Сравнение только с вашими собственными записями: "
            "рекорд — это не место в общем списке."
        )
        lines.append("")

    lines.append("## Чего здесь нет")
    lines.append("")
    lines.append("- имена других участников и их результаты;")
    lines.append("- порядковые места;")
    lines.append(
        "- влияние на оценку курса: %s"
        % (document.get("excluded_from_grade_ru") or "сравнение не оценивается")
    )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "LeaderboardError",
    "LeaderboardConfig",
    "DIMENSIONS",
    "DIMENSION_TITLES_RU",
    "DIMENSION_SOURCE",
    "cohorts_dir",
    "config_path",
    "load_config",
    "save_config",
    "opt_in",
    "opt_out",
    "is_opted_in",
    "require_opted_in",
    "most_improved",
    "personal_best",
    "build_leaderboard",
    "evaluate_badges",
    "render_leaderboard",
]
