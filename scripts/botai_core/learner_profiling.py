# -*- coding: utf-8 -*-
"""Learner learning-style profiles, computed from recorded interactions.

A profile is a *presentation preference*, not a diagnosis and not a grading
input: it tells the harness how to phrase and pace material for one learner,
never what that learner is worth or what mark they deserve. Three properties
keep it honest:

* **Deterministic.** The same interaction history always yields the same
  profile. Keyword heuristics are fixed sets, scores are clamped, ties break
  in a declared order — there is no model judgement in this module.
* **Stateless by design.** The profiler never mutates a profile in place from
  a single event. Interactions are appended to a rolling history file;
  `rebuild_profile` recomputes from that history. Losing a rebuilt profile is
  therefore recoverable; losing raw events is not.
* **Paths are untrusted input.** Every directory and file component goes
  through `paths.safe_name` / `paths.ensure_within`. A student id that is
  also a path traversal is refused, never normalised into something that
  would silently write elsewhere.

The four cognitive scores start from a small baseline (0.25) so a bare or
short history is not all-zero and the dominant style is still defined. The
scores are relative weights among presentation preferences, not absolute
skill measurements.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths, schemas

SCHEMA_VERSION = 2

# Deterministic tie-break order for `dominant()`. Listed in the dataclass
# declaration order; `max` over a dict keeps first-insertion order on ties.
STYLE_ORDER = ("visual", "verbal", "kinesthetic", "auditory")

# Baseline every style starts from. A history with no signal still has a
# defined dominant style instead of a division-by-zero or an all-zero argmax.
STYLE_BASELINE = 0.25

# Keyword sets, lower-cased stems. Substring match over the whole interaction
# content: stems, not full words, so «покажи диаграмму» hits `diagram` stems
# without needing morphology.
STYLE_KEYWORDS = {
    "visual": ("пример", "диаграмма", "картинк", "схем", "график", "покажи", "наглядн"),
    "auditory": (
        "объясни",
        "расскажи",
        "проговори",
        "услыш",
        "аудио",
        "послушай",
        "вслух",
    ),
    "kinesthetic": (
        "попробовать",
        "сделать сам",
        "практик",
        "руками",
        "запусти",
        "поэкспериментируй",
        "потрогать",
    ),
    "verbal": ("текст", "почита", "определение", "формулировк", "напиши", "словами"),
}

# Depth preference stems.
DEEP_MARKERS = ("почему", "зачем", "как связано", "откуда")
SURFACE_MARKERS = ("задача", "нужно", "быстро", "решить")

# Pace bounds from the schema: outside this band the number is measurement
# error, not a learner. These must match `learning_profile.schema.json`
# (`measured_wpm`: 50..500) — a copy-paste of the minimum into the maximum
# would silently report every learner at 50 wpm and never fail loudly.
PACE_MIN_WPM = 50
PACE_MAX_WPM = 500
PACE_DEFAULT_WPM = 180

# How often the profile is recomputed from stored history, and how stale it
# may be before age alone forces a rebuild.
RECOMPUTE_EVERY_SESSIONS = 5
PROFILE_MAX_AGE_DAYS = 14

# Rolling history kept per learner. Bounded so a long-running course cannot
# grow one learner's history file without limit.
DEFAULT_HISTORY_KEEP = 200

# Interaction shapes the profiler records.
INTERACTION_TYPES = ("question", "attempt", "feedback_response")

FEEDBACK_STYLES = ("direct", "socratic", "exploratory", "socratic_then_direct")

CHUNK_SIZES = ("small", "medium", "large")

DEPTH_PREFERENCES = ("surface", "strategic", "deep")


class LearnerProfilingError(RuntimeError):
    """A refused profiling operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class CognitiveStyle:
    """Four presentation preferences, each independently 0..1.

    They are preferences, not measured ability: a high visual score says how
    this learner likes material presented, not how well they read diagrams.
    """

    visual: float = STYLE_BASELINE
    verbal: float = STYLE_BASELINE
    kinesthetic: float = STYLE_BASELINE
    auditory: float = STYLE_BASELINE

    def _scores(self):
        # Fixed insertion order = the declared tie-break order.
        return (
            ("visual", float(self.visual)),
            ("verbal", float(self.verbal)),
            ("kinesthetic", float(self.kinesthetic)),
            ("auditory", float(self.auditory)),
        )

    def dominant(self):
        """The strongest style; ties break in STYLE_ORDER (visual first)."""
        best_name = STYLE_ORDER[0]
        best_value = None
        for name, value in self._scores():
            if best_value is None or value > best_value:
                best_name, best_value = name, value
        return best_name

    def to_document(self):
        return {
            "visual": round(float(self.visual), 6),
            "verbal": round(float(self.verbal), 6),
            "kinesthetic": round(float(self.kinesthetic), 6),
            "auditory": round(float(self.auditory), 6),
        }

    @classmethod
    def from_document(cls, document):
        document = document or {}
        return cls(
            visual=float(document.get("visual", STYLE_BASELINE)),
            verbal=float(document.get("verbal", STYLE_BASELINE)),
            kinesthetic=float(document.get("kinesthetic", STYLE_BASELINE)),
            auditory=float(document.get("auditory", STYLE_BASELINE)),
        )


@dataclass
class LearningProfile:
    """One learner's presentation profile, as a plain document shape.

    `student_id` must be a UUID when the document is validated against the
    published contract; the in-memory object keeps whatever string the caller
    used so a non-UUID workspace id can still be reasoned about before save.
    """

    student_id: str
    cognitive_style: CognitiveStyle = field(default_factory=CognitiveStyle)
    pace_wpm: int = PACE_DEFAULT_WPM
    avg_time_per_concept: int = 120
    preferred_chunk_size: str = "medium"
    depth_preference: str = "strategic"
    challenge_tolerance: float = 0.5
    feedback_style: str = "socratic_then_direct"
    updated_at: str = ""
    sessions_since_update: int = 0
    interaction_count: int = 0

    def to_document(self):
        """The dict matching `learning_profile.schema.json` (schema_version 2)."""
        return {
            "schema_version": SCHEMA_VERSION,
            "student_id": self.student_id,
            "cognitive_style": self.cognitive_style.to_document(),
            "pace": {
                "measured_wpm": int(self.pace_wpm),
                "avg_time_per_concept": int(self.avg_time_per_concept),
                "preferred_chunk_size": self.preferred_chunk_size,
            },
            "depth_preference": self.depth_preference,
            "challenge_tolerance": round(float(self.challenge_tolerance), 6),
            "feedback_style": self.feedback_style,
            "dominant_style": self.cognitive_style.dominant(),
            "interaction_count": int(self.interaction_count),
            "sessions_since_update": int(self.sessions_since_update),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_document(cls, document):
        pace = document.get("pace") or {}
        style_doc = document.get("cognitive_style") or {}
        return cls(
            student_id=str(document.get("student_id") or ""),
            cognitive_style=CognitiveStyle.from_document(style_doc),
            pace_wpm=int(pace.get("measured_wpm", PACE_DEFAULT_WPM)),
            avg_time_per_concept=int(pace.get("avg_time_per_concept", 120)),
            preferred_chunk_size=str(pace.get("preferred_chunk_size", "medium")),
            depth_preference=str(document.get("depth_preference", "strategic")),
            challenge_tolerance=float(document.get("challenge_tolerance", 0.5)),
            feedback_style=str(document.get("feedback_style", "socratic_then_direct")),
            updated_at=str(document.get("updated_at") or ""),
            sessions_since_update=int(document.get("sessions_since_update") or 0),
            interaction_count=int(document.get("interaction_count") or 0),
        )


# ---------------------------------------------------------------------------
# Heuristics (pure; no I/O)
# ---------------------------------------------------------------------------


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def _content_of(interaction):
    if not isinstance(interaction, dict):
        return ""
    return str(interaction.get("content") or "")


def _hit_ratio(text, stems):
    """Fraction of `stems` present in `text` (lower-cased substring match)."""
    lowered = text.lower()
    if not stems:
        return 0.0
    hits = sum(1 for stem in stems if stem in lowered)
    return hits / float(len(stems))


def infer_cognitive_style(history):
    """Keyword-hit ratio per style, clamped 0..1, with a small baseline.

    Deterministic: same history, same scores. A bare history yields the
    baseline for every style and `dominant()` resolves to `visual` by the
    declared tie-break order.
    """
    history = list(history or [])
    text = "\n".join(_content_of(item) for item in history)

    scores = {}
    for style, stems in STYLE_KEYWORDS.items():
        ratio = _hit_ratio(text, stems)
        # Baseline ensures a short history is not all-zero; ratio can only
        # raise a style, never the others.
        scores[style] = round(_clamp(STYLE_BASELINE + ratio * 0.75, 0.0, 1.0), 6)

    return CognitiveStyle(
        visual=scores["visual"],
        verbal=scores["verbal"],
        kinesthetic=scores["kinesthetic"],
        auditory=scores["auditory"],
    )


def compute_pace(history):
    """Words per minute over timed interactions, clamped 50..500.

    Untimed interactions contribute words but not minutes: counting them as
    zero time would inflate pace for a learner who mostly types long untimed
    answers. No timing data at all yields the neutral default 180.
    """
    total_words = 0
    total_sec = 0
    for item in history or []:
        if not isinstance(item, dict):
            continue
        duration = item.get("duration_sec")
        if not isinstance(duration, (int, float)) or duration <= 0:
            continue
        content = _content_of(item).strip()
        if not content:
            continue
        total_words += len(content.split())
        total_sec += int(duration)

    if total_sec <= 0:
        return PACE_DEFAULT_WPM

    minutes = total_sec / 60.0
    wpm = total_words / minutes
    return int(_clamp(round(wpm), PACE_MIN_WPM, PACE_MAX_WPM))


def detect_depth_preference(history):
    """`surface` / `strategic` / `deep` from marker counts in the history.

    deep: at least as many "why/where-does-it-come-from" markers as surface
    markers and at least one present. strategic: no clear winner (the default).
    surface: strictly more surface markers than deep markers.
    """
    text = "\n".join(_content_of(item) for item in (history or [])).lower()
    deep_hits = sum(text.count(marker) for marker in DEEP_MARKERS)
    surface_hits = sum(text.count(marker) for marker in SURFACE_MARKERS)

    if deep_hits > surface_hits and deep_hits > 0:
        return "deep"
    if surface_hits > deep_hits and surface_hits > 0:
        return "surface"
    return "strategic"


def infer_challenge_tolerance(history):
    """0..1 estimate from success rate and help-seeking behaviour.

    Heuristic, documented here because it is the part a reader will distrust:
    base = success rate over attempts that carry a `success` flag. Asking for
    hints is treated as a mild *discomfort* signal (harder work is welcome),
    so `hints_requested` reduces the estimate slightly; a history that never
    tries anything hard is not evidence of high tolerance either. The result
    is clamped to 0..1 and defaults to 0.5 with no evidence — the same neutral
    default as a fresh profile.
    """
    history = list(history or [])
    if not history:
        return 0.5

    attempts = [
        i for i in history if isinstance(i, dict) and i.get("success") is not None
    ]
    if not attempts:
        return 0.5

    successes = sum(1 for i in attempts if i.get("success"))
    success_rate = successes / float(len(attempts))

    hint_requests = 0
    for item in history:
        if not isinstance(item, dict):
            continue
        hints = item.get("hints_requested")
        if isinstance(hints, (int, float)) and hints > 0:
            hint_requests += int(hints)

    # Success rate dominates; hint-seeking gently pulls the estimate down.
    tolerance = success_rate - min(0.3, 0.05 * hint_requests)
    return round(_clamp(tolerance, 0.0, 1.0), 6)


def infer_feedback_style(history):
    """One of the schema's four feedback styles, from dialogue shape.

    * socratic_then_direct (policy default): both question-seeking and
      answer-seeking behaviour present, or neither is decisive.
    * socratic: the learner asks questions / engages in dialogue but does not
      ask for the finished answer.
    * direct: the learner repeatedly asks for the answer or a full solution.
    * exploratory: the learner experiments (long timed attempts, high
      hint-seeking without asking for answers) rather than asking questions.
    """
    history = list(history or [])
    if not history:
        return "socratic_then_direct"

    questionish = 0
    answerish = 0
    experimental = 0
    for item in history:
        if not isinstance(item, dict):
            continue
        content = _content_of(item).lower()
        itype = str(item.get("type") or "")
        if itype == "question":
            questionish += 1
        if any(
            word in content
            for word in (
                "дай ответ",
                "готовое решение",
                "просто ответь",
                "покажи решение",
                "что отвечать",
            )
        ):
            answerish += 1
        duration = item.get("duration_sec")
        if isinstance(duration, (int, float)) and duration >= 300:
            experimental += 1

    if answerish and questionish:
        return "socratic_then_direct"
    if answerish and not questionish:
        return "direct"
    if questionish and not answerish:
        return "socratic"
    if experimental:
        return "exploratory"
    return "socratic_then_direct"


def preferred_chunk_size_for(wpm):
    """Chunk size from pace: slower readers get smaller chunks."""
    if wpm < 120:
        return "small"
    if wpm > 240:
        return "large"
    return "medium"


def avg_time_per_concept(history):
    """Mean seconds per timed interaction, floored at 60 (schema minimum)."""
    durations = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        duration = item.get("duration_sec")
        if isinstance(duration, (int, float)) and duration > 0:
            durations.append(int(duration))
    if not durations:
        return 120
    return max(60, int(round(sum(durations) / float(len(durations)))))


def build_profile_from_history(student_id, history, *, updated_at=None, clock=None):
    """A full `LearningProfile` computed from `history`. Pure."""
    history = list(history or [])
    style = infer_cognitive_style(history)
    pace = compute_pace(history)
    return LearningProfile(
        student_id=student_id,
        cognitive_style=style,
        pace_wpm=pace,
        avg_time_per_concept=avg_time_per_concept(history),
        preferred_chunk_size=preferred_chunk_size_for(pace),
        depth_preference=detect_depth_preference(history),
        challenge_tolerance=infer_challenge_tolerance(history),
        feedback_style=infer_feedback_style(history),
        updated_at=updated_at or now_iso(clock),
        sessions_since_update=0,
        interaction_count=len(history),
    )


# ---------------------------------------------------------------------------
# Time helpers (aware UTC, trailing Z — same convention as tutoring.py)
# ---------------------------------------------------------------------------


def now_iso(clock=None):
    moment = clock() if clock else datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text):
    if not text:
        return None
    value = str(text).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

PROFILE_FILENAME = "learning_profile.json"
HISTORY_FILENAME = "interaction_history.json"


def profiles_dir(root=None):
    """`<root>/profiles` when that directory exists, else the repo `profiles/`.

    Mirrors `personas.personas_dir`: resolved from this file, never from the
    working directory, because the CLI runs from many places.
    """
    if root:
        candidate = Path(root) / "profiles"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "profiles"


# The implementation lives under a name the class parameter does not shadow:
# `LearnerProfiler(profiles_dir=...)` takes the same argument as this function,
# and a parameter that shadows a module function is how a call quietly changes
# meaning the next time the body is refactored.
_default_profiles_dir = profiles_dir


class LearnerProfiler:
    """Loads, infers and persists one learner's profile and interaction history.

    The profiler keeps no in-memory history: every call that needs the past
    reads `interaction_history.json`, and every rebuild recomputes from that
    file. A process restart therefore loses nothing that was persisted.
    """

    def __init__(self, profiles_dir=None, *, root=None, clock=None):
        # `_default_profiles_dir` rather than `profiles_dir(root)`: the
        # parameter shadows the module function, and reading a global that
        # shares the parameter's name is exactly the kind of thing that
        # silently changes meaning when the parameter is reused.
        if profiles_dir is None:
            self._profiles_dir: Path = _default_profiles_dir(root)
        elif isinstance(profiles_dir, (str, Path)):
            self._profiles_dir = Path(profiles_dir)
        else:
            # A callable resolver: accepted so a caller may pass
            # `personas_dir`-shaped helpers directly.
            self._profiles_dir = Path(profiles_dir(root))
        self._root = root
        self._clock = clock

    # -- paths ------------------------------------------------------------
    def _learner_dir(self, student_id):
        try:
            name = paths.safe_name(student_id, kind="идентификатор обучающегося")
        except paths.PathError as e:
            raise LearnerProfilingError(e.code, e.message)
        base = self._profiles_dir
        base.mkdir(parents=True, exist_ok=True)
        try:
            contained = paths.ensure_within(base, base / str(name))
        except paths.PathError as e:
            raise LearnerProfilingError(e.code, e.message)
        return Path(contained)

    def path_for(self, student_id):
        """`profiles/<student_id>/learning_profile.json`."""
        return self._learner_dir(student_id) / PROFILE_FILENAME

    def _history_path(self, student_id):
        return self._learner_dir(student_id) / HISTORY_FILENAME

    # -- profile ----------------------------------------------------------
    def load_profile(self, student_id):
        """The stored profile, or None when there is none yet.

        A present-but-invalid document is an error, not a silent None: a
        learner whose profile cannot be read must not be taught as if no
        profile existed.
        """
        path = self.path_for(student_id)
        if not path.is_file():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise LearnerProfilingError(
                "PROFILE_UNREADABLE",
                "не удалось прочитать профиль %s: %s" % (path, e),
            )
        try:
            schemas.validate(document, "learning_profile")
        except schemas.SchemaError as e:
            raise LearnerProfilingError(e.code, e.message)
        return LearningProfile.from_document(document)

    def save_profile(self, profile):
        """Validate and write the profile document. Returns the path."""
        if not isinstance(profile, LearningProfile):
            raise LearnerProfilingError(
                "PROFILE_TYPE_INVALID",
                "ожидался объект LearningProfile, получено %s" % type(profile).__name__,
            )
        document = profile.to_document()
        try:
            schemas.validate(document, "learning_profile")
        except schemas.SchemaError as e:
            raise LearnerProfilingError(e.code, e.message)

        path = self.path_for(profile.student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(payload + "\n", encoding="utf-8", newline="\n")
        return path

    # -- history ----------------------------------------------------------
    def load_history(self, student_id):
        """The stored rolling interaction list (empty when absent)."""
        path = self._history_path(student_id)
        if not path.is_file():
            return []
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise LearnerProfilingError(
                "HISTORY_UNREADABLE",
                "не удалось прочитать историю взаимодействий %s: %s" % (path, e),
            )
        items = document.get("items") if isinstance(document, dict) else document
        if not isinstance(items, list):
            raise LearnerProfilingError(
                "HISTORY_UNREADABLE",
                "история взаимодействий повреждена: ожидался список, получено %s"
                % type(items).__name__,
            )
        return items

    def append_interaction(
        self, student_id, interaction, max_keep=DEFAULT_HISTORY_KEEP
    ):
        """Append one interaction to the rolling history, trimming to `max_keep`.

        Returns the trimmed list that is now on disk. A malformed interaction
        is stored as-is with a defensive cast: history is evidence, and
        silently dropping an event the learner produced would be dishonest.
        """
        if not isinstance(interaction, dict):
            raise LearnerProfilingError(
                "INTERACTION_NOT_OBJECT",
                "взаимодействие должно быть объектом, получено %s"
                % type(interaction).__name__,
            )
        items = self.load_history(student_id)
        items.append(dict(interaction))
        if max_keep and max_keep > 0 and len(items) > max_keep:
            items = items[-int(max_keep) :]

        path = self._history_path(student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "student_id": student_id,
            "items": items,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return items

    # -- inference on stored history --------------------------------------
    def analyze_interaction(
        self, interaction, *, student_id, max_keep=DEFAULT_HISTORY_KEEP
    ):
        """Record one interaction and return the profile recomputed from history.

        Counters (`interaction_count`, `sessions_since_update`) live on the
        rebuilt profile; `sessions_since_update` is carried forward from the
        stored profile so a single new interaction does not pretend a session
        boundary happened.
        """
        self.append_interaction(student_id, interaction, max_keep=max_keep)
        history = self.load_history(student_id)
        previous = self.load_profile(student_id)
        profile = build_profile_from_history(
            student_id, history, updated_at=now_iso(self._clock), clock=self._clock
        )
        if previous is not None:
            profile.sessions_since_update = previous.sessions_since_update
        self.save_profile(profile)
        return profile

    def infer_cognitive_style(self, history):
        return infer_cognitive_style(history)

    def compute_pace(self, history):
        return compute_pace(history)

    def detect_depth_preference(self, history):
        return detect_depth_preference(history)

    def should_update_profile(self, student_id):
        """True every 5 sessions or when the profile is older than 14 days."""
        profile = self.load_profile(student_id)
        if profile is None:
            return True
        if profile.sessions_since_update >= RECOMPUTE_EVERY_SESSIONS:
            return True
        updated = parse_iso(profile.updated_at)
        if updated is None:
            return True
        moment = self._clock() if self._clock else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (moment - updated) > timedelta(days=PROFILE_MAX_AGE_DAYS)

    def rebuild_profile(self, student_id):
        """Recompute the profile from stored history and persist it."""
        history = self.load_history(student_id)
        previous = self.load_profile(student_id)
        profile = build_profile_from_history(
            student_id, history, updated_at=now_iso(self._clock), clock=self._clock
        )
        if previous is not None:
            profile.sessions_since_update = 0
        self.save_profile(profile)
        return profile

    def reset_profile(self, student_id):
        """Delete profile + history. Returns True if anything was removed."""
        removed = False
        for path in (self.path_for(student_id), self._history_path(student_id)):
            if path.is_file():
                path.unlink()
                removed = True
        directory = self._learner_dir(student_id)
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                # Not empty (other files) or already gone: leave it.
                pass
        return removed


__all__ = [
    "LearnerProfilingError",
    "CognitiveStyle",
    "LearningProfile",
    "LearnerProfiler",
    "STYLE_ORDER",
    "STYLE_BASELINE",
    "STYLE_KEYWORDS",
    "infer_cognitive_style",
    "compute_pace",
    "detect_depth_preference",
    "infer_challenge_tolerance",
    "infer_feedback_style",
    "build_profile_from_history",
    "profiles_dir",
    "now_iso",
    "parse_iso",
]
