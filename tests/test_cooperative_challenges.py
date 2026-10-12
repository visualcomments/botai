#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for cooperative challenges: four formats, one refusal, evidence scoring.

Claims under test:

* `create_challenge`/`save_challenge`/`load_challenge` round-trip; an unknown
  `kind` raises `KIND_UNKNOWN`, duplicate members raise `MEMBERS_DUPLICATED`
  and empty objectives raise `OBJECTIVES_EMPTY`;
* `form_teams` is deterministic with `seed=0` and balanced (sizes differ by at
  most 1) with nobody left out or duplicated;
* `challenge_assistance_ceiling` equals `tutoring.ASSESSMENT_CEILING` for the
  same assessment — it delegates, it does not duplicate;
* **the "not speed alone" property**: `score_team_challenge` ranks on
  evidence, so a team with more passed checks but slower elapsed time beats a
  faster team with fewer checks, constructed explicitly below;
* `peer_teaching_pairing` refuses with `NO_SUITABLE_PEER` when everyone has
  equal skill;
* `code_review_tournament` counts only submissions carrying BOTH a line and a
  reason.

Run:
    python3 tests/test_cooperative_challenges.py
    python3 -m pytest tests/test_cooperative_challenges.py -q
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import cooperative_challenges as K, schemas, tutoring  # noqa: E402

_passed = 0
_failures: list[str] = []

S1 = "11111111-1111-4111-8111-111111111111"
S2 = "22222222-2222-4222-8222-222222222222"
S3 = "33333333-3333-4333-8333-333333333333"
S4 = "44444444-4444-4444-8444-444444444444"
S5 = "55555555-5555-4555-8555-555555555555"
S6 = "66666666-6666-4666-8666-666666666666"


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def expect_error(name, fn, code):
    try:
        fn()
    except K.ChallengeError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def clock():
    from datetime import datetime, timezone

    return datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


class Workspace:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "challenges").mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        self._tmp.cleanup()


def base_challenge(**overrides):
    document = {
        "challenge_id": "ch-a",
        "kind": "team_coding",
        "title": "Командная задача",
        "members": [S1, S2, S3],
        "objective_ids": ["obj-1", "obj-2"],
        "duration_hours": 4,
        "assessment": "unknown",
        "graded": False,
    }
    document.update(overrides)
    return document


def test_create_save_load_round_trip():
    ws = Workspace()
    try:
        document = K.create_challenge(
            ws.root,
            challenge_id="ch-a",
            kind="hackathon",
            title="Хакатон",
            members=[S1, S2, S3],
            objective_ids=["obj-1"],
            duration_hours=6,
            clock=clock,
        )
        check(
            "create даёт строгий assessment",
            document["assessment"] == "unknown",
            document["assessment"],
        )
        check("create не объявляет оценкой", document["graded"] is False)
        check(
            "сказано, что результат не оценка",
            "не является оценкой" in document["graded_by_contract_ru"],
            document["graded_by_contract_ru"],
        )

        path = K.save_challenge(ws.root, document)
        check(
            "файл создан внутри temp root",
            path.resolve().is_relative_to(ws.root.resolve()),
            str(path),
        )
        loaded = K.load_challenge(ws.root, "ch-a")
        check("round-trip: challenge_id", loaded["challenge_id"] == "ch-a")
        check("round-trip: kind", loaded["kind"] == "hackathon")
        check("round-trip: участники", loaded["members"] == [S1, S2, S3])
        check("round-trip: цели", loaded["objective_ids"] == ["obj-1"])

        expect_error(
            "несуществующее задание отклонено",
            lambda: K.load_challenge(ws.root, "nope"),
            "CHALLENGE_MISSING",
        )
        try:
            K.challenge_path(ws.root, "../evil")
        except (K.ChallengeError, ValueError) as e:
            code = getattr(e, "code", type(e).__name__)
            check(
                "traversal-ид отклонён", code in ("SLUG_SEPARATOR", "PathError"), code
            )
        else:
            check("traversal-ид отклонён", False, "путь разрешён")
        check("файл вне temp root не создан", not (ws.root / "evil.json").exists())
    finally:
        ws.cleanup()


def test_validation_refusals():
    expect_error(
        "неизвестный вид отклонён",
        lambda: K.validate_challenge(base_challenge(kind="telepathy")),
        "KIND_UNKNOWN",
    )
    expect_error(
        "дубликаты участников отклонены",
        lambda: K.validate_challenge(base_challenge(members=[S1, S2, S1])),
        "MEMBERS_DUPLICATED",
    )
    expect_error(
        "пустые цели отклонены",
        lambda: K.validate_challenge(base_challenge(objective_ids=[])),
        "OBJECTIVES_EMPTY",
    )
    expect_error(
        "меньше двух участников отклонено",
        lambda: K.validate_challenge(base_challenge(members=[S1])),
        "MEMBERS_TOO_FEW",
    )
    expect_error(
        "без challenge_id отклонён",
        lambda: K.validate_challenge(base_challenge(challenge_id="  ")),
        "CHALLENGE_INVALID",
    )
    expect_error(
        "без названия отклонён",
        lambda: K.validate_challenge(base_challenge(title="")),
        "CHALLENGE_INVALID",
    )
    expect_error(
        "отрицательная длительность отклонена",
        lambda: K.validate_challenge(base_challenge(duration_hours=-1)),
        "DURATION_INVALID",
    )
    expect_error(
        "не-объект отклонён",
        lambda: K.validate_challenge("строка"),
        "CHALLENGE_INVALID",
    )

    for kind in K.CHALLENGE_KINDS:
        document = K.validate_challenge(base_challenge(kind=kind))
        check("допустимый вид %s принят" % kind, document["kind"] == kind)

    check(
        "усреднение schema_version",
        K.validate_challenge(
            {k: v for k, v in base_challenge().items() if k != "schema_version"}
        )["schema_version"]
        == K.SCHEMA_VERSION,
    )


def test_form_teams_is_deterministic_and_balanced():
    members = [S1, S2, S3, S4, S5, S6]
    first = K.form_teams(members, team_size=2, seed=0)
    second = K.form_teams(members, team_size=2, seed=0)
    check("form_teams детерминирован", first == second, "%s != %s" % (first, second))

    sizes = [len(t["members"]) for t in first]
    check("размеры в пределах 1", max(sizes) - min(sizes) <= 1, str(sizes))
    check("все учтены", sum(sizes) == 6, str(sizes))
    flat = [m for t in first for m in t["members"]]
    check("без дубликатов", len(flat) == len(set(flat)), str(flat))
    check("id команд стабильны", first[0]["team_id"] == "team-1", first[0]["team_id"])

    # A seed rotates the deal but keeps the guarantees.
    rotated = K.form_teams(members, team_size=2, seed=1)
    sizes_r = [len(t["members"]) for t in rotated]
    check("seed меняет раскладку", rotated != first)
    check(
        "seed сохраняет сбалансированность",
        max(sizes_r) - min(sizes_r) <= 1,
        str(sizes_r),
    )
    flat_r = [m for t in rotated for m in t["members"]]
    check(
        "seed не дублирует участников",
        len(flat_r) == len(set(flat_r)) == 6,
        str(flat_r),
    )

    one = K.form_teams([S1], team_size=1, seed=0)
    check("один участник — одна команда", len(one) == 1, str(one))

    # A roster that does not divide evenly.
    uneven = K.form_teams([S1, S2, S3, S4, S5], team_size=2, seed=0)
    sizes_u = [len(t["members"]) for t in uneven]
    check(
        "5 на 2: размеры в пределах 1", max(sizes_u) - min(sizes_u) <= 1, str(sizes_u)
    )
    check("5 на 2: все учтены", sum(sizes_u) == 5, str(sizes_u))

    expect_error("пустой состав отклонён", lambda: K.form_teams([]), "MEMBERS_EMPTY")
    expect_error(
        "team_size=0 отклонён",
        lambda: K.form_teams(members, team_size=0),
        "TEAM_SIZE_INVALID",
    )
    expect_error(
        "team_size=True отклонён",
        lambda: K.form_teams(members, team_size=True),
        "TEAM_SIZE_INVALID",
    )


def test_assistance_ceiling_delegates():
    challenge = base_challenge()
    for assessment in ("graded", "unknown", "practice", None, ""):
        challenge = dict(base_challenge(), assessment=assessment)
        got = K.challenge_assistance_ceiling(challenge)
        expected = tutoring.ASSESSMENT_CEILING.get(
            assessment or "unknown", tutoring.ASSESSMENT_CEILING["unknown"]
        )
        check(
            "ceiling_for(%r) совпадает с tutoring" % (assessment,),
            got["ceiling"] == expected,
            "%s != %s" % (got["ceiling"], expected),
        )
        check(
            "источник назван",
            got["source"] == "tutoring.ASSESSMENT_CEILING",
            got["source"],
        )
        check(
            "сказано, что дедлайн не разрешает выдавать решения",
            "не меняет правила помощи" in got["note_ru"],
            got["note_ru"],
        )
    check(
        "экспортирована та же таблица",
        tutoring.ASSESSMENT_CEILING["graded"] == "EXAMPLE",
    )
    check(
        "default для unknown — EXAMPLE",
        tutoring.ASSESSMENT_CEILING["unknown"] == "EXAMPLE",
    )
    check(
        "default для practice — SOLUTION",
        tutoring.ASSESSMENT_CEILING["practice"] == "SOLUTION",
    )


def test_graded_challenge_requires_contract_reference():
    check(
        "флаг без ссылки — не оценка", K.graded_challenge({"graded": True}) is False
    ) if False else None
    document = {"graded": True, "graded_by_contract": "rev-3"}
    check(
        "graded со ссылкой на контракт — оценка", K.graded_challenge(document) is True
    )
    check(
        "без флага — не оценка",
        K.graded_challenge({"graded": False, "graded_by_contract": "rev-3"}) is False,
    )
    expect_error(
        "флаг без ссылки на контракт отклонён",
        lambda: K.graded_challenge({"graded": True}),
        "GRADED_WITHOUT_CONTRACT",
    )
    check("пустое задание — не оценка", K.graded_challenge({}) is False)
    check("None — не оценка", K.graded_challenge(None) is False)


def test_score_team_challenge_ignores_speed():
    """More passed checks + more coverage beats fewer, even when slower."""
    challenge = base_challenge(objective_ids=["obj-1", "obj-2"])
    submissions = [
        # Slow team A: 4 passed checks covering both objectives.
        {
            "team_id": "team-slow",
            "objective_id": "obj-1",
            "verdict": "pass",
            "started_at": "2026-09-17T10:00:00Z",
            "finished_at": "2026-09-17T13:00:00Z",
            "member_id": S1,
        },
        {
            "team_id": "team-slow",
            "objective_id": "obj-1",
            "verdict": "pass",
            "started_at": "2026-09-17T10:00:00Z",
            "finished_at": "2026-09-17T13:00:00Z",
            "member_id": S2,
        },
        {
            "team_id": "team-slow",
            "objective_id": "obj-2",
            "verdict": "pass",
            "started_at": "2026-09-17T10:00:00Z",
            "finished_at": "2026-09-17T13:00:00Z",
            "member_id": S1,
        },
        {
            "team_id": "team-slow",
            "objective_id": "obj-2",
            "verdict": "pass",
            "started_at": "2026-09-17T10:00:00Z",
            "finished_at": "2026-09-17T13:00:00Z",
            "member_id": S2,
        },
        # Fast team B: 1 passed check covering one objective only.
        {
            "team_id": "team-fast",
            "objective_id": "obj-1",
            "verdict": "pass",
            "started_at": "2026-09-17T12:00:00Z",
            "finished_at": "2026-09-17T12:10:00Z",
            "member_id": S3,
        },
    ]
    result = K.score_team_challenge(challenge=challenge, submissions=submissions)
    scores = {t["team_id"]: t for t in result["teams"]}
    check(
        "медленная команда идёт первой",
        result["teams"][0]["team_id"] == "team-slow",
        str([t["team_id"] for t in result["teams"]]),
    )
    check(
        "медленная команда набирает больше",
        scores["team-slow"]["score"] > scores["team-fast"]["score"],
        "%s <= %s" % (scores["team-slow"]["score"], scores["team-fast"]["score"]),
    )
    check(
        "время сообщено, но не входит в балл",
        scores["team-slow"]["time_is_scored_ru"].startswith("время только"),
        scores["team-slow"]["time_is_scored_ru"],
    )
    check(
        "время медленной команды больше",
        scores["team-slow"]["reported_time_sec"]
        > scores["team-fast"]["reported_time_sec"],
        str(
            (
                scores["team-slow"]["reported_time_sec"],
                scores["team-fast"]["reported_time_sec"],
            )
        ),
    )
    check(
        "покрытие медленной команды 1.0",
        scores["team-slow"]["coverage"] == 1.0,
        str(scores["team-slow"]["coverage"]),
    )
    check(
        "формула названа",
        "время не входит в балл" in result["formula_ru"],
        result["formula_ru"],
    )
    check("знаменатель назван", result["denominator"] == 2, str(result["denominator"]))
    check(
        "assistance ceiling продублирован из tutoring",
        result["assistance_ceiling"] == tutoring.ASSESSMENT_CEILING["unknown"],
        result["assistance_ceiling"],
    )
    check("результат не оценка курса", result["graded_challenge"] is False)

    expect_error(
        "пустые посылки отклонены",
        lambda: K.score_team_challenge(challenge=challenge, submissions=[]),
        "SUBMISSIONS_EMPTY",
    )
    expect_error(
        "посылка по чужой цели отклонена",
        lambda: K.score_team_challenge(
            challenge=challenge,
            submissions=[{"team_id": "t", "objective_id": "obj-99", "verdict": "pass"}],
        ),
        "SUBMISSION_OBJECTIVE_UNKNOWN",
    )
    expect_error(
        "посылка без team_id отклонена",
        lambda: K.score_team_challenge(
            challenge=challenge,
            submissions=[{"objective_id": "obj-1", "verdict": "pass"}],
        ),
        "SUBMISSION_INVALID",
    )
    expect_error(
        "не-объект посылки отклонён",
        lambda: K.score_team_challenge(challenge=challenge, submissions=["строка"]),
        "SUBMISSION_INVALID",
    )

    failed_only = K.score_team_challenge(
        challenge=challenge,
        submissions=[{"team_id": "team-x", "objective_id": "obj-1", "verdict": "fail"}],
    )
    check(
        "только провалы дают балл 0",
        failed_only["teams"][0]["score"] == 0.0,
        str(failed_only["teams"][0]),
    )
    check(
        "непокрытая цель не входит в coverage",
        failed_only["teams"][0]["objectives_covered"] == [],
        str(failed_only["teams"][0]),
    )


def test_peer_teaching_refuses_equal_skill():
    cohort = {
        "members": [
            {"student_id": S1, "level": "intermediate"},
            {"student_id": S2, "level": "intermediate"},
        ]
    }
    equal = {S1: {"obj-1": 0.5}, S2: {"obj-1": 0.5}}
    expect_error(
        "равный уровень — нет преподавателя",
        lambda: K.peer_teaching_pairing(cohort, skill_vectors=equal, focus="obj-1"),
        "NO_SUITABLE_PEER",
    )

    one = {"members": [{"student_id": S1, "level": "beginner"}]}
    expect_error(
        "меньше двух участников отклонено",
        lambda: K.peer_teaching_pairing(one, skill_vectors={S1: {"o": 0.5}}, focus="o"),
        "NO_SUITABLE_PEER",
    )

    better = {S1: {"obj-1": 0.3}, S2: {"obj-1": 0.9}}
    pairing = K.peer_teaching_pairing(cohort, skill_vectors=better, focus="obj-1")
    check(
        "наставник строго лучше",
        pairing["pairs"][0]["teacher"] == S2 and pairing["pairs"][0]["student"] == S1,
        str(pairing["pairs"]),
    )
    check(
        "уровни записаны",
        pairing["pairs"][0]["teacher_level"] == 0.9
        and pairing["pairs"][0]["student_level"] == 0.3,
        str(pairing["pairs"][0]),
    )
    check(
        "строгий запрет назван",
        "строго лучше" in pairing["strict_rule_ru"],
        pairing["strict_rule_ru"],
    )
    check(
        "знаменатель назван", pairing["denominator"] == 2, str(pairing["denominator"])
    )

    # Weakest-first over multiple objectives: the student's weakest is paired.
    multi = {
        S1: {"obj-1": 0.2, "obj-2": 0.8},
        S2: {"obj-1": 0.9, "obj-2": 0.9},
        S3: {"obj-1": 0.1, "obj-2": 0.1},
    }
    cohort3 = {"members": [{"student_id": S1}, {"student_id": S2}, {"student_id": S3}]}
    result = K.peer_teaching_pairing(
        cohort3, skill_vectors=multi, objective_ids=["obj-1", "obj-2"]
    )
    s1_pair = [p for p in result["pairs"] if p["student"] == S1]
    check(
        "S1 учится по самой слабой цели obj-1",
        s1_pair and s1_pair[0]["objective_id"] == "obj-1",
        str(result["pairs"]),
    )
    check(
        "у S2 никто не строго лучше — unpairable назван",
        any(u["student_id"] == S2 for u in result["unpairable"]),
        str(result["unpairable"]),
    )
    check(
        "в unpairable сказано, что нужен преподаватель",
        all("преподавател" in u["reason_ru"] for u in result["unpairable"]),
        str(result["unpairable"]),
    )

    # With no objective keys anywhere there is nothing to compare skill on.
    expect_error(
        "без целей отклонено",
        lambda: K.peer_teaching_pairing(cohort, skill_vectors={S1: {}, S2: {}}),
        "OBJECTIVES_EMPTY",
    )


def test_code_review_tournament_counts_line_and_reason():
    submissions = [
        {
            "submission_id": "sub-1",
            "reviewer_id": S1,
            "findings": [
                {
                    "line": 3,
                    "reason_ru": "переприсваивание без нужды",
                    "confirmed": True,
                },
                {"line": 5, "reason_ru": "", "confirmed": True},  # no reason
                {"line": 0, "reason_ru": "строка 0 запрещена", "confirmed": True},
                {
                    "line": 7,
                    "reason_ru": "пропущен обработчик ошибки",
                    "confirmed": False,
                },  # unconfirmed
                {"line": 9, "reason_ru": "без единиц измерения", "confirmed": True},
            ],
        },
        {"submission_id": "sub-2", "reviewer_id": S2, "findings": []},
    ]
    result = K.code_review_tournament(submissions=submissions)
    by_id = {row["submission_id"]: row for row in result["pairs"]}
    check(
        "учтены только замечания с строкой И причиной и подтверждением",
        by_id["sub-1"]["findings_count"] == 2,
        str(by_id["sub-1"]),
    )
    check(
        "балл совпадает со счётчиком",
        by_id["sub-1"]["score"] == 2,
        str(by_id["sub-1"]["score"]),
    )
    check(
        "пустая посылка тоже в списке",
        by_id["sub-2"]["findings_count"] == 0,
        str(by_id["sub-2"]),
    )
    check(
        "всего подтверждённых замечаний 2",
        result["findings_count"] == 2,
        str(result["findings_count"]),
    )
    check(
        "отклонённые перечислены, а не скрыты",
        len(result["rejected_ru"]) == 3,
        str(result["rejected_ru"]),
    )
    check("знаменатель назван", result["denominator"] == 2, str(result["denominator"]))
    check(
        "правило названо",
        "строку" in result["rule_ru"] and "причину" in result["rule_ru"],
        result["rule_ru"],
    )
    check(
        "нормировка запрещена",
        "без нормировки" in result["normalisation_ru"],
        result["normalisation_ru"],
    )
    check(
        "порядок по убыванию балла",
        [r["score"] for r in result["pairs"]]
        == sorted((r["score"] for r in result["pairs"]), reverse=True),
        str([r["score"] for r in result["pairs"]]),
    )

    # A large file must not win by line count.
    big = {
        "submission_id": "sub-big",
        "reviewer_id": S3,
        "findings": [
            {"line": n, "reason_ru": "мелочь", "confirmed": True} for n in range(1, 40)
        ],
    }
    small = {
        "submission_id": "sub-small",
        "reviewer_id": S4,
        "findings": [
            {"line": 2, "reason_ru": "архитектурная ошибка", "confirmed": True}
        ],
    }
    ranked = K.code_review_tournament(submissions=[small, big])
    check(
        "большой файл не нормируется на размер",
        ranked["pairs"][0]["submission_id"] == "sub-big",
        str([r["submission_id"] for r in ranked["pairs"]]),
    )

    expect_error(
        "пустые посылки отклонены",
        lambda: K.code_review_tournament(submissions=[]),
        "SUBMISSIONS_EMPTY",
    )
    expect_error(
        "посылка без id отклонена",
        lambda: K.code_review_tournament(
            submissions=[{"reviewer_id": S1, "findings": []}]
        ),
        "SUBMISSION_INVALID",
    )
    expect_error(
        "findings не список отклонён",
        lambda: K.code_review_tournament(
            submissions=[{"submission_id": "s", "findings": "много"}]
        ),
        "FINDINGS_INVALID",
    )
    # A malformed finding is reported, not raised: dropping a contestant's
    # reason silently would be a tournament where submitting nothing wins.
    with_stray = K.code_review_tournament(
        submissions=[
            {
                "submission_id": "s",
                "findings": [
                    {"line": 1, "reason_ru": "ok", "confirmed": True},
                    "не-объект",
                ],
            }
        ]
    )
    check(
        "строка вместо замечания посчитана отклонённой",
        with_stray["pairs"][0]["findings_count"] == 1,
        str(with_stray["pairs"][0]),
    )
    check(
        "и названа в rejected_ru",
        any("объектом" in r["reason_ru"] for r in with_stray["rejected_ru"]),
        str(with_stray["rejected_ru"]),
    )


def test_schema_is_structural_only():
    """The module documents that no `challenge` contract exists in schemas/v2."""
    check(
        "схемы challenge нет в CONTRACTS",
        "challenge"
        not in __import__("botai_core.schemas", fromlist=["schemas"]).CONTRACTS,
    )
    validated = K.validate_challenge(base_challenge())
    check(
        "validate_challenge возвращает нормализованный документ",
        validated["members"] == [S1, S2, S3],
        str(validated["members"]),
    )


def main():
    tests = [
        test_create_save_load_round_trip,
        test_validation_refusals,
        test_form_teams_is_deterministic_and_balanced,
        test_assistance_ceiling_delegates,
        test_graded_challenge_requires_contract_reference,
        test_score_team_challenge_ignores_speed,
        test_peer_teaching_refuses_equal_skill,
        test_code_review_tournament_counts_line_and_reason,
        test_schema_is_structural_only,
    ]
    for test in tests:
        print("== %s ==" % test.__name__)
        try:
            test()
        except Exception as e:  # noqa: BLE001
            import traceback

            _failures.append(test.__name__)
            print("  FAIL %s выбросил %s: %s" % (test.__name__, type(e).__name__, e))
            traceback.print_exc()

    print()
    print("%d passed, %d failed" % (_passed, len(_failures)))
    if _failures:
        for name in _failures:
            print("  - %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
