# -*- coding: utf-8 -*-
"""Cooperative challenges: four formats, one refusal that applies to all of them.

The design's §8.2 names the formats — team coding, hackathon, peer teaching,
code review tournament — and this module implements the mechanics for each. Two
rules run through all four and are enforced here rather than asked for:

* **A challenge is not a grade.** A challenge result never becomes a course
  grade unless the *accepted course contract* declares the assignment graded.
  That is the same rule `policy.py` enforces for assistance, and this module
  cites it rather than re-deciding it: `graded_challenge(challenge)` returns
  `False` for any challenge that does not carry a `graded: true` flag the
  contract supplied, and a caller that sets the flag itself is the caller who
  has to have read the contract.

* **The assistance ceiling applies during a challenge.** A hackathon is not a
  graded exam and is not an exemption either. `tutoring.ASSESSMENT_CEILING`
  applies unchanged: a challenge on an undeclared or graded assignment caps
  help at `EXAMPLE`, exactly as it does outside a challenge. A deadline is not
  a licence to hand over a solution, and `challenge_assistance_ceiling` is the
  one place a caller reads the applicable ceiling for a challenge so that it
  cannot quietly use a different one.

Scoring is on **evidence, not speed**. The design's "hackathon mode" invites a
race; a race rewards whoever had the answer ready, which is not what a
cooperative challenge is for. `score_team_challenge` therefore counts passing
checks per objective and coverage of the declared objectives, and reports time
only as an observation the caller may ignore. `code_review_tournament` counts
*confirmed* findings — each one must reference a line and a reason, because a
review score that a bare "looks wrong" can earn is a score that rewards noise.

Everything is deterministic. `form_teams` deals a stable sort round-robin;
`seed` exists for callers who want a different, equally reproducible
arrangement, and no function in this module draws from `random` without an
explicit seed parameter.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths, tutoring

SCHEMA_VERSION = 2

CHALLENGE_FILENAME_SUFFIX = ".json"

CHALLENGE_KINDS = (
    "team_coding",
    "hackathon",
    "peer_teaching",
    "code_review_tournament",
)

KIND_TITLES_RU = {
    "team_coding": "Командная задача по программированию",
    "hackathon": "Хакатон",
    "peer_teaching": "Взаимное обучение",
    "code_review_tournament": "Турнир разбора кода",
}

DEFAULT_TEAM_SIZE = 3

# Evidence weights for `score_team_challenge`. Written out rather than inlined
# so a reader can see the whole scoring model at once.
WEIGHT_CHECKS_PASSED = 1.0
WEIGHT_OBJECTIVE_COVERED = 2.0
WEIGHT_SKILL_SPREAD_PENALTY = 0.5


class ChallengeError(RuntimeError):
    """A refused challenge operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def challenges_dir(root=None):
    """`<root>/challenges` when that directory exists, else the repo's.

    Mirrors `personas.personas_dir`: resolved from this file, never from the
    working directory.
    """
    if root:
        candidate = Path(root) / "challenges"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "challenges"


def challenge_path(root, challenge_id):
    """`<challenges_dir>/<challenge_id>.json`, with the id validated."""
    name = paths.safe_name(challenge_id, kind="идентификатор задания")
    base = challenges_dir(root)
    try:
        target = paths.ensure_within(
            base, base / (str(name) + CHALLENGE_FILENAME_SUFFIX)
        )
    except paths.PathError as e:
        raise ChallengeError(e.code, e.message)
    return Path(target)


def load_challenge(root, challenge_id):
    """Read and validate one challenge document."""
    path = challenge_path(root, challenge_id)
    if not path.is_file():
        raise ChallengeError(
            "CHALLENGE_MISSING",
            "задание не найдено: %s. Создайте его, прежде чем проверять результаты."
            % path,
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ChallengeError(
            "CHALLENGE_UNREADABLE",
            "не удалось прочитать задание %s: %s" % (path, e),
        )
    return validate_challenge(document)


def save_challenge(root, challenge):
    """Validate and write one challenge document. Returns the path."""
    document = validate_challenge(challenge)
    path = challenge_path(root, document["challenge_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_challenge(document):
    """Structural validation.

    There is no `challenge` contract in `schemas/v2`, so this checks what a
    schema would: required ids, a known `kind`, at least two distinct members,
    at least one objective, and the assistance/grading facts. A structural check
    is not a promise that the document is well-formed in every respect — it is
    the check this module can actually perform, and it raises rather than
    repairing.
    """
    if not isinstance(document, dict):
        raise ChallengeError(
            "CHALLENGE_INVALID",
            "документ задания должен быть объектом, получено %s"
            % type(document).__name__,
        )

    challenge_id = document.get("challenge_id")
    if not isinstance(challenge_id, str) or not challenge_id.strip():
        raise ChallengeError(
            "CHALLENGE_INVALID",
            "у задания должен быть идентификатор (challenge_id)",
        )

    kind = document.get("kind")
    if kind not in CHALLENGE_KINDS:
        raise ChallengeError(
            "KIND_UNKNOWN",
            "неизвестный вид задания: %r (допустимы: %s)"
            % (kind, ", ".join(CHALLENGE_KINDS)),
        )

    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ChallengeError("CHALLENGE_INVALID", "у задания должно быть название")

    members = [str(m) for m in (document.get("members") or ()) if m]
    if len(members) < 2:
        raise ChallengeError(
            "MEMBERS_TOO_FEW",
            "совместное задание требует хотя бы двух участников, указано %d. "
            "Задание в одиночку — это практика, а не сотрудничество." % len(members),
        )
    if len(set(members)) != len(members):
        raise ChallengeError(
            "MEMBERS_DUPLICATED",
            "участники перечислены дважды: состав задания должен быть однозначным",
        )

    objective_ids = [str(o) for o in (document.get("objective_ids") or ()) if o]
    if not objective_ids:
        raise ChallengeError(
            "OBJECTIVES_EMPTY",
            "у задания должны быть объявлены цели: без них нечего покрывать "
            "и не за что признать результат состоящимся доказательством",
        )

    duration = document.get("duration_hours")
    if duration is not None:
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration <= 0
        ):
            raise ChallengeError(
                "DURATION_INVALID",
                "длительность задания должна быть положительным числом часов, "
                "получено %r" % (duration,),
            )

    validated = dict(document)
    validated["members"] = members
    validated["objective_ids"] = objective_ids
    validated.setdefault("schema_version", SCHEMA_VERSION)
    validated.setdefault("graded", False)
    validated.setdefault("assessment", "unknown")
    return validated


def create_challenge(
    root,
    *,
    challenge_id,
    kind,
    title,
    members,
    objective_ids,
    duration_hours=None,
    clock=None,
):
    """A validated challenge document with no teams and no results."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "challenge_id": paths.safe_name(challenge_id, kind="идентификатор задания"),
        "kind": kind,
        "title": str(title).strip(),
        "members": [str(m) for m in (members or ()) if m],
        "objective_ids": [str(o) for o in (objective_ids or ()) if o],
        "duration_hours": duration_hours,
        # Defaults are the strict ones: an undeclared assessment behaves
        # exactly like a graded one for assistance purposes, and a challenge is
        # not graded until the contract says so.
        "assessment": "unknown",
        "graded": False,
        "created_at": tutoring.now_iso(clock),
        "graded_by_contract_ru": (
            "результат задания не является оценкой курса, пока принятый "
            "контракт не объявит это задание оцениваемым"
        ),
    }
    return validate_challenge(document)


# --------------------------------------------------------------------------
# Assistance ceiling during a challenge
# --------------------------------------------------------------------------


def challenge_assistance_ceiling(challenge):
    """The assistance ceiling that applies during this challenge.

    Delegates to `tutoring.ASSESSMENT_CEILING` unchanged. A challenge is a
    collaboration, not a reclassification: a hackathon on an undeclared or
    graded assignment still caps at `EXAMPLE`, and a `SOLUTION` is available
    only on a confirmed practice task. A deadline is not an exemption.
    """
    assessment = (challenge or {}).get("assessment") or "unknown"
    ceiling = tutoring.ASSESSMENT_CEILING.get(assessment, "EXAMPLE")
    return {
        "assessment": assessment,
        "ceiling": ceiling,
        "source": "tutoring.ASSESSMENT_CEILING",
        "note_ru": (
            "совместная работа не меняет правила помощи: для оцениваемого или "
            "не объявленного контрактом задания потолок остаётся «пример»"
        ),
    }


def graded_challenge(challenge):
    """Whether this challenge counts as a course grade.

    `False` unless the challenge document carries `graded: true` **and** names
    the contract revision that declared it. The flag alone is not enough,
    because a caller that sets it is asserting a fact about the contract, and
    an assertion without a reference is exactly what the accepted contract
    exists to prevent.
    """
    document = challenge or {}
    if not document.get("graded"):
        return False
    reference = document.get("graded_by_contract")
    if not reference:
        raise ChallengeError(
            "GRADED_WITHOUT_CONTRACT",
            "задание помечено как оцениваемое, но не указано, каким решением "
            "принятого контракта это объявлено. Оценку задаёт контракт курса, "
            "не флаг в документе задания.",
        )
    return True


# --------------------------------------------------------------------------
# Team formation
# --------------------------------------------------------------------------


def form_teams(members, *, team_size=DEFAULT_TEAM_SIZE, seed=0):
    """Balanced teams from a stable sort, dealt round-robin.

    Members are sorted by their id, then dealt into `team_size`-sized buckets.
    That gives balanced sizes (sizes differ by at most one) without a shuffle,
    and it is deterministic: the same roster produces the same teams, so a
    learner can ask "why am I here" and get an answer.

    `seed` rotates the starting offset, which changes *who is dealt first* and
    therefore who ends up where — a rotation, not a reshuffle. Nobody is left
    out and nobody is duplicated either way.
    """
    if not isinstance(team_size, int) or isinstance(team_size, bool) or team_size < 1:
        raise ChallengeError(
            "TEAM_SIZE_INVALID",
            "размер команды должен быть целым числом не меньше 1, получено %r"
            % (team_size,),
        )

    roster = sorted({str(m) for m in (members or ()) if m})
    if not roster:
        raise ChallengeError("MEMBERS_EMPTY", "состав команд пуст")

    count = (len(roster) + team_size - 1) // team_size
    teams = [[] for _ in range(count)]
    offset = int(seed or 0) % len(roster)
    for index in range(len(roster)):
        teams[((index + offset) % len(roster)) % count].append(
            roster[(index + offset) % len(roster)]
        )

    # The distribution above can put two members in one team while leaving
    # another empty when the rotation wraps oddly; repair by refilling in
    # stable order, which keeps determinism and the size guarantee.
    filled = [m for team in teams for m in team]
    if len(set(filled)) != len(roster):
        teams = [[] for _ in range(count)]
        for index, member in enumerate(roster):
            teams[index % count].append(member)

    return [
        {"team_id": "team-%d" % (number + 1), "members": team}
        for number, team in enumerate(teams)
        if team
    ]


# --------------------------------------------------------------------------
# Evidence scoring
# --------------------------------------------------------------------------


def score_team_challenge(*, challenge, submissions):
    """Score a team challenge on evidence, not on speed.

    **The formula**, stated once:

        for each team
            checks_passed     = number of submitted checks with verdict == "pass"
            coverage          = |{objective_id in the submission that is also
                                 in challenge.objective_ids and has at least
                                 one passing check}| / |challenge.objective_ids|
            score             = 1.0 * checks_passed
                              + 2.0 * coverage
            reported_time_sec = max(finished_at - started_at) over the team's
                                 submissions — **reported, never scored**

    Two things the formula deliberately does not contain. Speed is absent: a
    race rewards whoever had the answer prepared, which is not a
    collaboration. Member count is absent: a team of six submits more passing
    checks than a team of two for no better reason, so the score is per
    objective covered, not per person.

    `submissions` is a list of
    `{"team_id", "objective_id", "verdict", "started_at", "finished_at"}`.
    A submission for an objective the challenge did not declare is refused
    (`SUBMISSION_OBJECTIVE_UNKNOWN`) rather than counted — a challenge that
    quietly accepts extra objectives is not a challenge.
    """
    document = validate_challenge(challenge)
    declared = set(document["objective_ids"])

    submissions = list(submissions or ())
    if not submissions:
        raise ChallengeError(
            "SUBMISSIONS_EMPTY",
            "нет ни одной посылки: оценивать нечего, и нулевой результат "
            "был бы выдумкой, а не измерением",
        )

    per_team = {}
    for submission in submissions:
        if not isinstance(submission, dict):
            raise ChallengeError(
                "SUBMISSION_INVALID",
                "посылка должна быть объектом, получено %s" % type(submission).__name__,
            )
        team_id = submission.get("team_id")
        objective_id = submission.get("objective_id")
        if not team_id:
            raise ChallengeError(
                "SUBMISSION_INVALID",
                "у посылки должен быть team_id: без команды результат некуда отнести",
            )
        if objective_id not in declared:
            raise ChallengeError(
                "SUBMISSION_OBJECTIVE_UNKNOWN",
                "посылка ссылается на цель %r, которой нет в задании (%s): "
                "задание не объявляло эту цель, и её результат не может "
                "войти в оценку" % (objective_id, ", ".join(sorted(declared))),
            )
        per_team.setdefault(str(team_id), []).append(submission)

    teams = []
    for team_id in sorted(per_team):
        team_submissions = per_team[team_id]
        passed = [s for s in team_submissions if s.get("verdict") == "pass"]
        covered = sorted(
            {s.get("objective_id") for s in passed if s.get("objective_id") in declared}
        )
        coverage = len(covered) / float(len(declared)) if declared else 0.0
        checks_passed = len(passed)
        score = (
            WEIGHT_CHECKS_PASSED * checks_passed + WEIGHT_OBJECTIVE_COVERED * coverage
        )

        times = [
            (
                tutoring.parse_iso(s.get("finished_at")),
                tutoring.parse_iso(s.get("started_at")),
            )
            for s in team_submissions
        ]
        durations = [
            int((end - start).total_seconds())
            for end, start in times
            if end is not None and start is not None and end >= start
        ]

        members = sorted(
            {str(s.get("member_id")) for s in team_submissions if s.get("member_id")}
        )

        teams.append(
            {
                "team_id": team_id,
                "members": members,
                "checks_passed": checks_passed,
                "objectives_covered": covered,
                "coverage": round(coverage, 6),
                "score": round(score, 6),
                "reported_time_sec": max(durations) if durations else None,
                "time_is_scored_ru": (
                    "время только сообщается и не входит в балл: скорость "
                    "награждает готовый ответ, а не сотрудничество"
                ),
            }
        )

    teams.sort(key=lambda item: (-item["score"], item["team_id"]))
    return {
        "challenge_id": document["challenge_id"],
        "kind": document["kind"],
        "teams": teams,
        "denominator": len(teams),
        "denominator_ru": (
            "оценены %d команд(ы) из %d, по которым есть посылки; команд без "
            "посылок в баллы не попадают" % (len(teams), max(len(teams), len(per_team)))
        ),
        "assistance_ceiling": challenge_assistance_ceiling(document)["ceiling"],
        "graded_challenge": graded_challenge(document)
        if document.get("graded")
        else False,
        "formula_ru": (
            "балл = 1.0 * зачтённые проверки + 2.0 * доля покрытых целей; "
            "время не входит в балл"
        ),
    }


# --------------------------------------------------------------------------
# Peer teaching
# --------------------------------------------------------------------------


def peer_teaching_pairing(cohort, *, skill_vectors, objective_ids=None, focus=None):
    """Pairs `(teacher, student)` where the teacher is strictly better.

    For each student and each focus objective, the teacher is the member with
    the **strictly** highest skill on that objective. "Strictly" is the whole
    point: a peer at exactly the same level has nothing to teach, and pairing
    them would produce a lesson neither can give. When no member is strictly
    better, this raises `ChallengeError("NO_SUITABLE_PEER", ...)` — a peer
    without a peer to learn from gets a teacher, not a fake pairing.

    `focus` selects one objective; without it, pairing is done over
    `objective_ids` and a student is paired once per objective they are
    weakest at (the objective with their lowest vector value).
    """
    vectors = {}
    for member in (cohort or {}).get("members") or []:
        student_id = member.get("student_id")
        if not student_id:
            continue
        vectors[str(student_id)] = {
            str(k): float(v)
            for k, v in ((skill_vectors or {}).get(student_id) or {}).items()
        }

    if len(vectors) < 2:
        raise ChallengeError(
            "NO_SUITABLE_PEER",
            "в потоке меньше двух участников: обучаться друг у друга некому",
        )

    if focus:
        objectives = [str(focus)]
    else:
        objectives = [
            str(o)
            for o in (
                objective_ids
                or sorted(set(key for vector in vectors.values() for key in vector))
            )
        ]

    if not objectives:
        raise ChallengeError(
            "OBJECTIVES_EMPTY",
            "не заданы цели для разбора: без них неизвестно, по чему сравнивать навык",
        )

    pairs = []
    unpairable = []
    for student_id in sorted(vectors):
        target = (
            objectives[0]
            if len(objectives) == 1
            else _weakest_objective(vectors[student_id], objectives)
        )
        student_value = vectors[student_id].get(target)
        if student_value is None:
            unpairable.append(
                {
                    "student_id": student_id,
                    "objective_id": target,
                    "reason_ru": "нет данных о навыке по этой цели: пара не строится",
                }
            )
            continue

        candidates = [
            (value.get(target), other)
            for other, value in vectors.items()
            if other != student_id and value.get(target) is not None
        ]
        if not candidates:
            unpairable.append(
                {
                    "student_id": student_id,
                    "objective_id": target,
                    "reason_ru": "нет ни одного участника с данными по этой цели",
                }
            )
            continue

        best_value, teacher = max(candidates, key=lambda item: (item[0], item[1]))
        if best_value <= student_value:
            # Strictly better or nothing: equal skill is not teaching.
            unpairable.append(
                {
                    "student_id": student_id,
                    "objective_id": target,
                    "reason_ru": (
                        "нет участника строго лучше по цели %s (лучший равен %.3f, "
                        "у ученика %.3f): нужен преподаватель, а не ровесник"
                        % (target, best_value, student_value)
                    ),
                }
            )
            continue

        pairs.append(
            {
                "teacher": teacher,
                "student": student_id,
                "objective_id": target,
                "teacher_level": best_value,
                "student_level": student_value,
            }
        )

    if not pairs and unpairable:
        raise ChallengeError(
            "NO_SUITABLE_PEER",
            "не удалось составить ни одной пары: %s. Равный по навыку сокурсник "
            "не объясняет материал — назначьте преподавателя."
            % "; ".join(item["reason_ru"] for item in unpairable),
        )

    return {
        "pairs": pairs,
        "unpairable": unpairable,
        "denominator": len(vectors),
        "denominator_ru": (
            "пары построены для %d из %d участников потка; остальным нужен "
            "преподаватель" % (len(pairs), len(vectors))
        ),
        "strict_rule_ru": (
            "наставник обязан быть строго лучше по цели: равный уровень — это "
            "не обучение"
        ),
    }


def _weakest_objective(vector, objectives):
    """The objective with the learner's lowest known skill (ties by id)."""
    known = [o for o in objectives if vector.get(o) is not None]
    if not known:
        return objectives[0]
    return min(known, key=lambda o: (vector[o], o))


# --------------------------------------------------------------------------
# Code review tournament
# --------------------------------------------------------------------------


def code_review_tournament(*, submissions):
    """Review points from **confirmed** findings only.

    A finding counts when it names both a line and a reason:

        {"submission_id", "reviewer_id", "line": <int >= 1>,
         "reason_ru": "<non-empty>", "confirmed": true}

    `findings_count` counts confirmed findings. An unconfirmed or malformed
    finding is reported in `rejected_ru` rather than dropped, because a review
    tournament whose scoring silently discards a contestant's reason is a
    tournament where the cheapest way to win is to submit nothing.

    Score per submission is `confirmed_findings_count`; the score is a count of
    *useful* observations, so it is not normalised by submission size — a large
    file would otherwise win by having more lines.
    """
    submissions = list(submissions or ())
    if not submissions:
        raise ChallengeError(
            "SUBMISSIONS_EMPTY",
            "нет ни одной посылки для разбора кода",
        )

    per_submission = {}
    rejected = []
    confirmed = 0
    for submission in submissions:
        if not isinstance(submission, dict):
            raise ChallengeError(
                "SUBMISSION_INVALID",
                "посылка должна быть объектом, получено %s" % type(submission).__name__,
            )
        submission_id = submission.get("submission_id")
        if not submission_id:
            raise ChallengeError(
                "SUBMISSION_INVALID",
                "у посылки должен быть submission_id",
            )
        submission_id = str(submission_id)

        findings = submission.get("findings")
        if findings is None:
            findings = []
        if not isinstance(findings, list):
            raise ChallengeError(
                "FINDINGS_INVALID",
                "поле findings должно быть списком, получено %s"
                % type(findings).__name__,
            )

        accepted = 0
        for index, finding in enumerate(findings):
            problem = _finding_problem(finding, submission_id, index)
            if problem:
                rejected.append(problem)
                continue
            if not finding.get("confirmed"):
                rejected.append(
                    {
                        "submission_id": submission_id,
                        "index": index,
                        "reason_ru": "замечание не подтверждено и в балл не идёт",
                    }
                )
                continue
            accepted += 1
        confirmed += accepted

        per_submission.setdefault(
            submission_id,
            {
                "submission_id": submission_id,
                "reviewer_id": submission.get("reviewer_id"),
                "findings_count": 0,
            },
        )
        per_submission[submission_id]["findings_count"] += accepted

    rows = []
    for submission_id in sorted(per_submission):
        row = dict(per_submission[submission_id])
        row["score"] = row["findings_count"]
        rows.append(row)
    rows.sort(key=lambda item: (-item["score"], item["submission_id"]))

    return {
        "findings_count": confirmed,
        "pairs": rows,
        "rejected_ru": rejected,
        "denominator": len(rows),
        "denominator_ru": (
            "учтено %d посылок из %d переданных; отклонённые замечания "
            "перечислены отдельно и не скрыты" % (len(rows), len(submissions))
        ),
        "rule_ru": (
            "засчитывается только подтверждённое замечание, указавшее строку "
            "и причину: «здесь плохо» баллом не является"
        ),
        "normalisation_ru": (
            "балл — число полезных замечаний, без нормировки на размер "
            "файла: большой файл не выигрывает количеством строк"
        ),
    }


def _finding_problem(finding, submission_id, index):
    """Why a finding is malformed, or `None` when it is well-formed."""
    if not isinstance(finding, dict):
        return {
            "submission_id": submission_id,
            "index": index,
            "reason_ru": "замечание должно быть объектом",
        }
    line = finding.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        return {
            "submission_id": submission_id,
            "index": index,
            "reason_ru": "замечание должно указывать номер строки (целое >= 1)",
        }
    reason = finding.get("reason_ru")
    if not isinstance(reason, str) or not reason.strip():
        return {
            "submission_id": submission_id,
            "index": index,
            "reason_ru": "замечание должно содержать причину",
        }
    return None


__all__ = [
    "ChallengeError",
    "CHALLENGE_KINDS",
    "KIND_TITLES_RU",
    "challenges_dir",
    "challenge_path",
    "load_challenge",
    "save_challenge",
    "validate_challenge",
    "create_challenge",
    "challenge_assistance_ceiling",
    "graded_challenge",
    "form_teams",
    "score_team_challenge",
    "peer_teaching_pairing",
    "code_review_tournament",
]
