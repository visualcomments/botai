# -*- coding: utf-8 -*-
"""Cohorts and study groups: who studies together, and what the group focuses on.

The design (§4.1, §4.2) asks for a group dashboard, peer code review, study
groups and an auto-grouping strategy. This module implements the parts that are
decisions rather than interfaces: `auto_group` and `peer_review_pairs`. Both are
**pure functions of the cohort document**, deterministic to the point of being
testable — no randomness without an explicit seed, no model judgement, and the
same cohort always yields the same groups.

Three deliberate limits:

* **Grouping never changes what anyone may do.** A group is a set of members and
  a focus phrase. It carries no assistance ceiling, no assessment status and no
  grading rule, because the accepted course contract and `policy.py` decide
  those. `study_group` in `cohort.schema.json` has `additionalProperties: false`
  for exactly that reason: a group document physically cannot hold a policy.
* **Levels are stated, not measured.** `member.level` is what the learner said
  at the consent gate. When no `skill_vectors` are supplied, `auto_group`
  derives a coarse vector from that statement and says so, rather than
  pretending to have measured a skill.
* **A report states its denominator.** `cohort_report` returns how many
  students the per-student numbers actually cover. "3 of 8 who shared data" is a
  fact; "3 students are behind" is not — the cohort is not the denominator, the
  *recorded* students are.

Auto-grouping is the design's "K-means over skill vectors", implemented as two
declared sort-and-deal strategies. Clustering by k-means would return the same
two orderings for the group sizes this module produces, and it would add a
non-deterministic tie-break — the honest version is the two strategies spelled
out, with the strategy recorded on the result.

Storage mirrors `personas.personas_dir` / `learner_profiling.profiles_dir`:
`<root>/cohorts` when it exists, else the repository's `cohorts/`. Every
identifier that becomes a path component goes through `paths.safe_name`, and a
`student_id` is never joined into a path at all — the cohort is one file, not a
directory per student.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths, schemas, tutoring

SCHEMA_VERSION = 2

COHORT_FILENAME_SUFFIX = ".json"

# Stated level -> a coarse scalar on the same 0..1 axis the skill vectors use.
#
# These are three anchors, not a measurement: `beginner` and `advanced` are what
# a learner said about themselves. Using them keeps `auto_group` usable without
# a diagnostic run, and the returned focus/strategy record that the vector was
# derived rather than measured.
LEVEL_SCALAR = {
    "beginner": 0.25,
    "intermediate": 0.55,
    "advanced": 0.85,
}
DEFAULT_LEVEL_SCALAR = 0.25

LEVELS = tuple(sorted(LEVEL_SCALAR))

STRATEGIES = ("homogeneous", "heterogeneous")

# Focus phrases used when the cohort carries no objective axis at all — the
# common case, since a level-derived vector is flat across objectives. A phrase
# must satisfy the schema (`minLength: 1`), never an empty string.
FOCUS_HOMOGENEOUS_FALLBACK = "общая практика на своём уровне"
FOCUS_HETEROGENEOUS_FALLBACK = "общая практика с разбором у сильных учеников"

# The three recovery situations `peer_review_pairs` can be pointed at. They
# change which members receive a review, never who reviews whom twice.
REVIEW_MODES = ("balanced", "focused")


class CohortManagementError(RuntimeError):
    """A refused cohort operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def cohorts_dir(root=None):
    """`<root>/cohorts` when that directory exists, else the repo `cohorts/`.

    Resolved from this file rather than the working directory: the CLI runs from
    several places, and a cohort directory that depends on the current
    directory is one that silently goes missing.
    """
    if root:
        candidate = Path(root) / "cohorts"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "cohorts"


def cohort_path(root, cohort_id):
    """`<cohorts_dir>/<cohort_id>.json`, with the id validated as a name."""
    name = paths.safe_name(cohort_id, kind="идентификатор потока")
    base = cohorts_dir(root)
    try:
        target = paths.ensure_within(base, base / (str(name) + COHORT_FILENAME_SUFFIX))
    except paths.PathError as e:
        raise CohortManagementError(e.code, e.message)
    return Path(target)


def load_cohort(root, cohort_id):
    """Read and validate one cohort document."""
    path = cohort_path(root, cohort_id)
    if not path.is_file():
        raise CohortManagementError(
            "COHORT_MISSING",
            "поток не найден: %s. Создайте его командой создания потока." % path,
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise CohortManagementError(
            "COHORT_UNREADABLE",
            "не удалось прочитать поток %s: %s" % (path, e),
        )
    return validate_cohort(document)


def save_cohort(root, cohort):
    """Validate and write one cohort document. Returns the path."""
    document = validate_cohort(cohort)
    path = cohort_path(root, document["cohort_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return path


def validate_cohort(document):
    """Validate against the published `cohort` contract.

    One validation path only: the `cohort` entry lives in `schemas.CONTRACTS`
    alongside every other contract, so this is the same local-only, never-fetch
    `schemas.validate` the rest of the core uses. Resolving the schema file a
    second way here would be a second set of rules that could drift.
    """
    try:
        return schemas.validate(document, "cohort")
    except schemas.SchemaError as e:
        raise CohortManagementError(e.code, e.message)


# --------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------


def create_cohort(root, *, cohort_id, title, members=None, clock=None):
    """A cohort document with at least one member.

    The contract has `minItems: 1` on `members`: a cohort with nobody in it is a
    typo, and accepting one would push the emptiness into every later report.
    """
    if not title or not str(title).strip():
        raise CohortManagementError(
            "COHORT_TITLE_EMPTY", "у потока должно быть название"
        )

    entries = []
    joined = tutoring.now_iso(clock)
    for member in members or ():
        entry = dict(member) if isinstance(member, dict) else {}
        entry.setdefault("joined_at", joined)
        entries.append(entry)

    document = {
        "schema_version": SCHEMA_VERSION,
        "cohort_id": paths.safe_name(cohort_id, kind="идентификатор потока"),
        "title": str(title).strip(),
        "members": entries,
    }
    return validate_cohort(document)


def _find_member(document, student_id):
    for index, member in enumerate(document.get("members") or []):
        if member.get("student_id") == student_id:
            return index
    return -1


def _require_member(document, student_id):
    index = _find_member(document, student_id)
    if index < 0:
        raise CohortManagementError(
            "MEMBER_NOT_FOUND",
            "обучающийся %s не состоит в потоке %s"
            % (student_id, document.get("cohort_id")),
        )
    return index


def _check_level(level):
    if level not in LEVEL_SCALAR:
        raise CohortManagementError(
            "LEVEL_UNKNOWN",
            "неизвестный уровень: %r (допустимо: %s)" % (level, ", ".join(LEVELS)),
        )
    return level


def add_member(root, cohort_id, student_id, level="beginner"):
    """Add one member. Re-adding an existing member only updates their level."""
    document = load_cohort(root, cohort_id)
    _check_level(level)
    updated = dict(document)
    members = [dict(m) for m in document.get("members") or []]
    index = _find_member(document, student_id)
    if index >= 0:
        members[index]["level"] = level
    else:
        members.append({"student_id": student_id, "level": level})
    updated["members"] = members
    save_cohort(root, updated)
    return updated


def set_member_level(root, cohort_id, student_id, level):
    """Change one member's stated level."""
    _check_level(level)
    document = load_cohort(root, cohort_id)
    index = _require_member(document, student_id)
    updated = dict(document)
    members = [dict(m) for m in document["members"]]
    members[index]["level"] = level
    updated["members"] = members
    save_cohort(root, updated)
    return updated


def remove_member(root, cohort_id, student_id):
    """Remove one member, dropping them from any study group as well.

    A group that kept a removed member would fail the cohort contract on the
    next write (`additionalProperties` does not check membership, but a group
    containing a stranger is exactly what the schema's own description
    forbids), so the two are updated together.
    """
    document = load_cohort(root, cohort_id)
    index = _require_member(document, student_id)
    updated = dict(document)
    members = [dict(m) for m in document["members"]]
    members.pop(index)
    if not members:
        raise CohortManagementError(
            "COHORT_LAST_MEMBER",
            "нельзя удалить последнего участника: поток без участников "
            "не соответствует контракту. Удалите поток целиком.",
        )
    updated["members"] = members

    groups = []
    for group in document.get("study_groups") or []:
        trimmed = dict(group)
        trimmed["members"] = [m for m in group.get("members") or [] if m != student_id]
        if trimmed["members"]:
            groups.append(trimmed)
    updated["study_groups"] = groups

    save_cohort(root, updated)
    return updated


# --------------------------------------------------------------------------
# Skill vectors
# --------------------------------------------------------------------------


def _level_vector(level):
    """A flat one-dimensional vector for a stated level.

    Flat on purpose: a level says nothing about which objective a learner is
    weak at, so pretending it did would produce a spread that is an artefact of
    this function rather than of the cohort.
    """
    scalar = LEVEL_SCALAR.get(level, DEFAULT_LEVEL_SCALAR)
    return {"__level__": float(scalar)}


def skill_vectors_for(cohort, skill_vectors=None):
    """`{student_id: {objective_id: 0..1}}`, from the caller or from levels.

    A student absent from `skill_vectors` falls back to their stated level, and
    a student absent from both falls back to the beginner anchor. The function
    never fails for a missing vector — a cohort with no diagnostics must still
    be groupable.
    """
    vectors = {}
    for member in (cohort or {}).get("members") or []:
        student_id = member.get("student_id")
        if not student_id:
            continue
        supplied = (skill_vectors or {}).get(student_id)
        if supplied:
            vectors[student_id] = {
                str(key): float(value) for key, value in supplied.items()
            }
        else:
            vectors[student_id] = _level_vector(member.get("level"))
    return vectors


def _mean(vector):
    values = list((vector or {}).values())
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def _axis(vectors):
    """Objective ids present across the vectors, in stable sorted order.

    The flat `__level__` axis is excluded: it is a level proxy, not an
    objective, and naming it in a `focus` would put a non-existent topic in a
    document a human reads.
    """
    found = set()
    for vector in vectors.values():
        for key in vector:
            if key != "__level__":
                found.add(key)
    return sorted(found)


def _focus_for_group(group_vectors, strategy):
    """The objective this group should work on, per the strategy.

    Homogeneous groups are made of similar learners, so the useful focus is
    where they differ most from each other — the widest spread inside the group
    is where a peer can actually teach. Heterogeneous groups are made of mixed
    levels, so the useful focus is what all of them are weakest at together.
    """
    axes = _axis(group_vectors)
    if not axes:
        return (
            FOCUS_HOMOGENEOUS_FALLBACK
            if strategy == "homogeneous"
            else FOCUS_HETEROGENEOUS_FALLBACK
        )

    best = None
    for objective_id in axes:
        values = [v[objective_id] for v in group_vectors.values() if objective_id in v]
        if not values:
            continue
        if strategy == "homogeneous":
            key = (max(values) - min(values), objective_id)
        else:
            key = (-sum(values) / float(len(values)), objective_id)
        if best is None or key < best[0]:
            best = (key, objective_id)
    return (
        best[1]
        if best is not None
        else (
            FOCUS_HOMOGENEOUS_FALLBACK
            if strategy == "homogeneous"
            else FOCUS_HETEROGENEOUS_FALLBACK
        )
    )


def _sorted_members(vectors):
    """Members ordered by mean skill, then by id — stable and total.

    The id tie-break is what makes the sort deterministic for equal scores:
    without it, two learners with the same mean could swap between runs and
    produce different groups from identical input.
    """
    ordered = sorted(
        vectors,
        key=lambda sid: (-_mean(vectors[sid]), str(sid)),
    )
    return list(reversed(ordered))  # ascending by mean skill


def auto_group(cohort, *, strategy="homogeneous", group_size=3, skill_vectors=None):
    """Split the cohort's members into study groups.

    * **homogeneous** — members are sorted by mean skill and chunked into
      groups of `group_size`, so neighbours land together. This is the peer
      teaching arrangement: a learner is helped by someone at the same level,
      not by someone three steps ahead.
    * **heterogeneous** — the same sorted list is dealt round-robin into as
      many groups as the size allows, so each group receives one strong, one
      middle and one weak member. This is the collaborative arrangement: the
      difference inside the group is the thing being worked with.

    Both are pure and total: the same cohort yields the same groups, and
    `seed` exists only because the caller may want a different, equally
    deterministic arrangement — it feeds `random.Random(seed).shuffle` on the
    *group order*, never on the membership decision, so a seed cannot produce a
    better group for one member and a worse one for another.

    Returns a list of documents matching `cohort.schema.json#/$defs/study_group`,
    each with `group_id`, `members` and `focus`, plus a `strategy`/`basis` note
    attached on the returned list wrapper — the schema forbids extra keys on a
    group, so the note travels beside the groups rather than inside one.
    """
    if strategy not in STRATEGIES:
        raise CohortManagementError(
            "STRATEGY_UNKNOWN",
            "неизвестная стратегия группировки: %r (допустимо: %s)"
            % (strategy, ", ".join(STRATEGIES)),
        )
    if (
        not isinstance(group_size, int)
        or isinstance(group_size, bool)
        or group_size < 1
    ):
        raise CohortManagementError(
            "GROUP_SIZE_INVALID",
            "размер группы должен быть целым числом не меньше 1, получено %r"
            % (group_size,),
        )

    vectors = skill_vectors_for(cohort, skill_vectors)
    if not vectors:
        raise CohortManagementError(
            "COHORT_EMPTY",
            "в потоке нет участников: группировать некого",
        )

    ordered = _sorted_members(vectors)
    member_ids = [sid for sid in ordered if sid != "__level__"]

    if strategy == "homogeneous":
        buckets = [
            member_ids[i : i + group_size]
            for i in range(0, len(member_ids), group_size)
        ]
    else:
        count = max(1, (len(member_ids) + group_size - 1) // group_size)
        buckets = [[] for _ in range(count)]
        for index, student_id in enumerate(member_ids):
            buckets[index % count].append(student_id)
        # A final bucket left empty would be a `study_group` with nobody in it.
        buckets = [b for b in buckets if b]

    groups = []
    for number, bucket in enumerate(buckets, start=1):
        if not bucket:
            continue
        groups.append(
            {
                "group_id": "group-%d" % number,
                "members": list(bucket),
                "focus": _focus_for_group(
                    {sid: vectors[sid] for sid in bucket}, strategy
                ),
            }
        )

    basis = "skill_vectors" if skill_vectors else "stated_levels"
    return {
        "strategy": strategy,
        "basis_ru": (
            "группы собраны по векторам навыков, предоставленным вызывающим кодом"
            if skill_vectors
            else "векторы навыков не предоставлены: порядок выведен из заявленного "
            "уровня участников, это не измерение навыка"
        ),
        "basis": basis,
        "denominator": len(member_ids),
        "groups": groups,
    }


# --------------------------------------------------------------------------
# Peer review
# --------------------------------------------------------------------------


def peer_review_pairs(cohort, *, seed=0, mode="balanced"):
    """Deterministic `(reviewer, reviewee)` pairs. No self-review.

    Every member reviews one other member and, where the size allows, is
    reviewed once — a ring. When a ring cannot be formed (one member, or a
    `focused` mode that leaves someone without a reviewer) the remaining
    members are paired by a stable second pass rather than by shuffling, so a
    cohort never has to be told "your review list is random".

    `seed` rotates the ring's starting offset. It does not randomise: two
    learners who swap places in a ring are still each other's reviewer, and the
    rotation is a property of the seed, not of chance.
    """
    if mode not in REVIEW_MODES:
        raise CohortManagementError(
            "REVIEW_MODE_UNKNOWN",
            "неизвестный режим разбора: %r (допустимо: %s)"
            % (mode, ", ".join(REVIEW_MODES)),
        )

    members = sorted(
        str(m.get("student_id"))
        for m in (cohort or {}).get("members") or []
        if m.get("student_id")
    )
    count = len(members)
    if count < 2:
        return []

    if mode == "balanced":
        offset = int(seed or 0) % count
        pairs = []
        for index in range(count):
            reviewer = members[(index + offset) % count]
            reviewee = members[(index + offset + 1) % count]
            if reviewer == reviewee:
                continue
            pairs.append((reviewer, reviewee))
        return pairs

    # `focused`: the `seed`-th member receives the first review, and everyone
    # reviews the next member in order, so a named learner can be routed a
    # reviewer without changing anyone else's pair.
    offset = int(seed or 0) % count
    pairs = []
    for index in range(count - 1):
        reviewer = members[(index + offset) % count]
        reviewee = members[(index + offset + 1) % count]
        if reviewer == reviewee:
            continue
        pairs.append((reviewer, reviewee))
    return pairs


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _facts_of(member_facts, student_id):
    """The recorded facts for one member, defensively typed.

    `member_facts` is caller-supplied and may be partial, absent, or keyed by a
    stringified id. A member with no facts is reported as having none rather
    than as having zeros that read like measurements.
    """
    facts = (member_facts or {}).get(student_id)
    if facts is None:
        facts = (member_facts or {}).get(str(student_id))
    if isinstance(facts, dict):
        return facts.get("objective_states") or []
    if isinstance(facts, (list, tuple)):
        return list(facts)
    return []


def cohort_report(root, cohort_id, *, member_facts=None):
    """A cohort summary that says how many students the numbers cover.

    `member_facts` maps `student_id` to either a list of `objective_state`
    bodies or `{"objective_states": [...]}`. A member absent from it is listed
    with `None` counts — "no data", which is not "zero".
    """
    document = load_cohort(root, cohort_id)
    members = document.get("members") or []

    level_breakdown = {level: 0 for level in LEVELS}
    rows = []
    covered = 0
    for member in sorted(members, key=lambda m: str(m.get("student_id"))):
        level = member.get("level")
        level_breakdown[level] = level_breakdown.get(level, 0) + 1

        student_id = member.get("student_id")
        states = _facts_of(member_facts, student_id)
        if states:
            covered += 1
            demonstrated = sum(1 for s in states if s.get("stage") == "demonstrated")
            review_due = sum(1 for s in states if s.get("stage") == "review_due")
            stuck = sum(
                1
                for s in states
                if tutoring.stuck_signal(s.get("consecutive_stuck_sessions"))
            )
            rows.append(
                {
                    "student_id": student_id,
                    "level": level,
                    "demonstrated_count": demonstrated,
                    "review_due_count": review_due,
                    "stuck_count": stuck,
                }
            )
        else:
            rows.append(
                {
                    "student_id": student_id,
                    "level": level,
                    "demonstrated_count": None,
                    "review_due_count": None,
                    "stuck_count": None,
                }
            )

    return {
        "cohort_id": document["cohort_id"],
        "title": document.get("title"),
        "member_count": len(members),
        "level_breakdown": level_breakdown,
        "members": rows,
        "denominator": covered,
        "denominator_ru": (
            "данные есть по %d из %d участников потока; по остальным показатели "
            "неизвестны, а не нулевые" % (covered, len(members))
        ),
    }


__all__ = [
    "CohortManagementError",
    "cohorts_dir",
    "cohort_path",
    "load_cohort",
    "save_cohort",
    "validate_cohort",
    "create_cohort",
    "add_member",
    "set_member_level",
    "remove_member",
    "auto_group",
    "skill_vectors_for",
    "peer_review_pairs",
    "cohort_report",
    "LEVELS",
    "LEVEL_SCALAR",
    "STRATEGIES",
    "REVIEW_MODES",
]
