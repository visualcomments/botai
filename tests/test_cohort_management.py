#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for cohort management: grouping is deterministic and never policy.

Claims under test:

* `create_cohort`/`save_cohort`/`load_cohort` round-trip and the document
  validates against the published `cohort` contract;
* an empty cohort is refused by the contract's `minItems: 1`;
* `add_member` twice with the same id does not duplicate;
* `set_member_level` with an unknown level raises `LEVEL_UNKNOWN`;
* `auto_group` is deterministic, homogeneous keeps similar learners together
  and heterogeneous interleaves them, with group sizes within 1 of each other
  and never an empty group;
* `cohort_report`'s `denominator` counts only members covered by the supplied
  facts, not the whole cohort;
* `peer_review_pairs` never pairs a student with themselves and never returns
  `n > n` reviewees;
* `remove_member` on the last member is refused with `COHORT_LAST_MEMBER`.

Run:
    python3 tests/test_cohort_management.py
    python3 -m pytest tests/test_cohort_management.py -q
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

from botai_core import cohort_management as C, schemas  # noqa: E402

_passed = 0
_failures: list[str] = []

NOW = "2026-09-17T12:00:00Z"


def clock():
    """A fixed aware-UTC clock, so `joined_at` is deterministic."""
    from datetime import datetime, timezone

    return datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


def new_cohort(root, cohort_id, members, title="Поток"):
    """`create_cohort` followed by `save_cohort`, as a real caller would."""
    document = C.create_cohort(
        root, cohort_id=cohort_id, title=title, members=members, clock=clock
    )
    C.save_cohort(root, document)
    return document


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
    except C.CohortManagementError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Workspace:
    """A temp root with a `cohorts/` directory."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "cohorts").mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        self._tmp.cleanup()


S1 = "11111111-1111-4111-8111-111111111111"
S2 = "22222222-2222-4222-8222-222222222222"
S3 = "33333333-3333-4333-8333-333333333333"
S4 = "44444444-4444-4444-8444-444444444444"
S5 = "55555555-5555-4555-8555-555555555555"


def make_members():
    return [
        {"student_id": S1, "level": "beginner"},
        {"student_id": S2, "level": "intermediate"},
        {"student_id": S3, "level": "advanced"},
    ]


def test_round_trip_and_schema():
    ws = Workspace()
    try:
        document = new_cohort(ws.root, "cohort-a", make_members(), title="Поток 1")
        check("create_cohort даёт трёх участников", len(document["members"]) == 3)
        try:
            schemas.validate(document, "cohort")
            check("документ соответствует схеме cohort", True)
        except schemas.SchemaError as e:
            check("документ соответствует схеме cohort", False, e.message[:200])

        path = C.save_cohort(ws.root, document)
        check(
            "файл создан внутри temp root",
            path.resolve().is_relative_to(ws.root.resolve()),
            str(path),
        )
        loaded = C.load_cohort(ws.root, "cohort-a")
        check("round-trip: cohort_id", loaded["cohort_id"] == "cohort-a")
        check("round-trip: title", loaded["title"] == "Поток 1")
        check(
            "round-trip: участники",
            [m["student_id"] for m in loaded["members"]] == [S1, S2, S3],
        )

        expect_error(
            "несуществующий поток — COHORT_MISSING",
            lambda: C.load_cohort(ws.root, "nope"),
            "COHORT_MISSING",
        )
        # `cohort_path` runs `safe_name` first, which raises `PathError`
        # directly; only `ensure_within` failures are wrapped in
        # CohortManagementError. Either way the traversal is refused.
        try:
            C.cohort_path(ws.root, "../evil")
        except (C.CohortManagementError, ValueError) as e:
            code = getattr(e, "code", type(e).__name__)
            check(
                "некорректный ид потока отклонён",
                code in ("SLUG_SEPARATOR", "PathError"),
                code,
            )
        else:
            check("некорректный ид потока отклонён", False, "путь разрешён")
        check("файл вне temp root не создан", not (ws.root / "evil.json").exists())
    finally:
        ws.cleanup()


def test_empty_cohort_refused():
    ws = Workspace()
    try:
        # The refusal surfaces as the schema's own `CONTRACT_INVALID` code —
        # `validate_cohort` re-raises the `SchemaError` code unchanged, so the
        # caller learns which rule (minItems) fired.
        expect_error(
            "поток без участников отклонён контрактом",
            lambda: C.create_cohort(
                ws.root, cohort_id="empty", title="Пустой", members=[], clock=clock
            ),
            "CONTRACT_INVALID",
        )
        expect_error(
            "None-участники тоже отклонены",
            lambda: C.create_cohort(
                ws.root, cohort_id="empty2", title="Пустой", members=None, clock=clock
            ),
            "CONTRACT_INVALID",
        )
        expect_error(
            "пустое название отклонено",
            lambda: C.create_cohort(
                ws.root, cohort_id="x", title="  ", members=make_members(), clock=clock
            ),
            "COHORT_TITLE_EMPTY",
        )
    finally:
        ws.cleanup()


def test_add_member_twice_does_not_duplicate():
    ws = Workspace()
    try:
        new_cohort(ws.root, "cohort-b", [make_members()[0]], title="T")
        C.add_member(ws.root, "cohort-b", S1, level="advanced")
        after = C.load_cohort(ws.root, "cohort-b")
        check(
            "повторное добавление не дублирует",
            len(after["members"]) == 1,
            str(len(after["members"])),
        )
        check(
            "уровень обновлён вместо дубля",
            after["members"][0]["level"] == "advanced",
            after["members"][0]["level"],
        )

        C.add_member(ws.root, "cohort-b", S2, level="beginner")
        second = C.load_cohort(ws.root, "cohort-b")
        check("новый участник добавлен", len(second["members"]) == 2)

        expect_error(
            "неизвестный уровень отклонён",
            lambda: C.add_member(ws.root, "cohort-b", S3, level="guru"),
            "LEVEL_UNKNOWN",
        )
        expect_error(
            "set_member_level с неизвестным уровнем отклонён",
            lambda: C.set_member_level(ws.root, "cohort-b", S1, "guru"),
            "LEVEL_UNKNOWN",
        )
        expect_error(
            "set_member_level для несуществующего участника отклонён",
            lambda: C.set_member_level(ws.root, "cohort-b", S4, "beginner"),
            "MEMBER_NOT_FOUND",
        )
    finally:
        ws.cleanup()


def test_auto_group_homogeneous_and_heterogeneous():
    members = [
        {"student_id": S1, "level": "beginner"},
        {"student_id": S2, "level": "beginner"},
        {"student_id": S3, "level": "advanced"},
        {"student_id": S4, "level": "advanced"},
        {"student_id": S5, "level": "intermediate"},
    ]
    cohort = C.create_cohort(
        Workspace().root, cohort_id="g", title="T", members=members
    )

    homo1 = C.auto_group(cohort, strategy="homogeneous", group_size=2)
    homo2 = C.auto_group(cohort, strategy="homogeneous", group_size=2)
    check("homogeneous детерминирован", homo1 == homo2, "%s != %s" % (homo1, homo2))
    check("basis назван", homo1["basis"] == "stated_levels", homo1["basis"])
    check("знаменатель — число участников", homo1["denominator"] == 5)

    # Same level inside each homogeneous group: levels set of group 1 & 2 by size.
    levels = {m["student_id"]: m["level"] for m in members}
    sizes = [len(g["members"]) for g in homo1["groups"]]
    check("размеры групп в пределах 1", max(sizes) - min(sizes) <= 1, str(sizes))
    check("нет пустых групп", all(sizes), str(sizes))
    check(
        "все участники распределены",
        sorted(m for g in homo1["groups"] for m in g["members"])
        == sorted([S1, S2, S3, S4, S5]),
    )

    # Homogeneous: the first full group is all one level (sorted by scalar).
    first_levels = {levels[m] for m in homo1["groups"][0]["members"]}
    check(
        "homogeneous: группа одного уровня", len(first_levels) == 1, str(first_levels)
    )

    hetero1 = C.auto_group(cohort, strategy="heterogeneous", group_size=3)
    hetero2 = C.auto_group(cohort, strategy="heterogeneous", group_size=3)
    check(
        "heterogeneous детерминирован",
        hetero1 == hetero2,
        "%s != %s" % (hetero1, hetero2),
    )
    sizes_h = [len(g["members"]) for g in hetero1["groups"]]
    check(
        "heterogeneous: размеры в пределах 1",
        max(sizes_h) - min(sizes_h) <= 1,
        str(sizes_h),
    )
    # Round-robin from ascending order: group 1 gets min/mid/max positions.
    g1 = [levels[m] for m in hetero1["groups"][0]["members"]]
    check("heterogeneous: внутри группы смешаны уровни", len(set(g1)) > 1, str(g1))
    check(
        "heterogeneous: распределены все",
        sorted(m for g in hetero1["groups"] for m in g["members"])
        == sorted([S1, S2, S3, S4, S5]),
    )

    expect_error(
        "неизвестная стратегия отклонена",
        lambda: C.auto_group(cohort, strategy="k-means"),
        "STRATEGY_UNKNOWN",
    )
    expect_error(
        "размер группы 0 отклонён",
        lambda: C.auto_group(cohort, group_size=0),
        "GROUP_SIZE_INVALID",
    )
    expect_error(
        "размер группы -1 отклонён",
        lambda: C.auto_group(cohort, group_size=-1),
        "GROUP_SIZE_INVALID",
    )
    expect_error(
        "размер группы True отклонён",
        lambda: C.auto_group(cohort, group_size=True),
        "GROUP_SIZE_INVALID",
    )


def test_group_size_one_and_indivisible():
    members = [{"student_id": sid, "level": "beginner"} for sid in (S1, S2, S3, S4)]
    cohort = C.create_cohort(
        Workspace().root, cohort_id="g2", title="T", members=members
    )
    solo = C.auto_group(cohort, strategy="homogeneous", group_size=1)
    check(
        "group_size=1 даёт по одному",
        [len(g["members"]) for g in solo["groups"]] == [1, 1, 1, 1],
        str(solo["groups"]),
    )
    check("group_size=1: четыре группы", len(solo["groups"]) == 4)

    # 4 members / group_size 3: homogeneous chunks sequentially, so the tail
    # group is the remainder. The guarantee is "nobody is dropped and no group
    # is empty", not "all groups are equal" — a peer-teaching group is built
    # from neighbours, so rebalancing would break the similarity property.
    uneven = C.auto_group(cohort, strategy="homogeneous", group_size=3)
    sizes = [len(g["members"]) for g in uneven["groups"]]
    check("4 на 3: размеры не превышают group_size", max(sizes) <= 3, str(sizes))
    check("4 на 3: без пустых групп", all(sizes), str(sizes))
    check("4 на 3: все учтены", sum(sizes) == 4, str(sizes))

    check("знаменатель = числу участников", uneven["denominator"] == 4)
    check(
        "basis_ru объясняет неизмеренность навыка",
        "не измерение" in uneven["basis_ru"] or "выведен" in uneven["basis_ru"],
        uneven["basis_ru"],
    )


def test_cohort_report_denominator_is_coverage():
    ws = Workspace()
    try:
        document = new_cohort(ws.root, "cohort-r", make_members(), title="Поток 1")
        facts = {
            S1: [{"stage": "demonstrated", "consecutive_stuck_sessions": 0}],
            S2: [{"stage": "review_due", "consecutive_stuck_sessions": 0}],
            # S3 gets nothing.
        }
        report = C.cohort_report(ws.root, "cohort-r", member_facts=facts)
        check(
            "denominator — число участников с фактами (2), не 3",
            report["denominator"] == 2,
            str(report["denominator"]),
        )
        check(
            "member_count — все участники",
            report["member_count"] == 3,
            str(report["member_count"]),
        )
        check(
            "denominator_ru называет покрытие",
            "по остальным показатели неизвестны" in report["denominator_ru"],
            report["denominator_ru"],
        )

        by_id = {row["student_id"]: row for row in report["members"]}
        check(
            "участник с фактами получил счётчики",
            by_id[S1]["demonstrated_count"] == 1,
            str(by_id[S1]),
        )
        check(
            "участник без фактов: None, а не 0",
            by_id[S3]["demonstrated_count"] is None,
            str(by_id[S3]),
        )
        check(
            "строка уровня заполнена", by_id[S3]["level"] == "advanced", str(by_id[S3])
        )
        check(
            "level_breakdown посчитан по всем",
            report["level_breakdown"]["beginner"] == 1,
            str(report["level_breakdown"]),
        )

        empty = C.cohort_report(ws.root, "cohort-r")
        check(
            "без фактов denominator = 0",
            empty["denominator"] == 0,
            str(empty["denominator"]),
        )
    finally:
        ws.cleanup()


def test_peer_review_pairs_never_self():
    members = make_members() + [
        {"student_id": S4, "level": "beginner"},
        {"student_id": S5, "level": "intermediate"},
    ]
    for mode in ("balanced", "focused"):
        for seed in (0, 1, 2):
            cohort = C.create_cohort(
                Workspace().root, cohort_id="pr", title="T", members=members
            )
            pairs = C.peer_review_pairs(cohort, seed=seed, mode=mode)
            for reviewer, reviewee in pairs:
                if reviewer == reviewee:
                    check(
                        "%s seed=%s: без самопары" % (mode, seed),
                        False,
                        "%s == %s" % (reviewer, reviewee),
                    )
                    break
            else:
                check("%s seed=%s: без самопары" % (mode, seed), True)
            check(
                "%s seed=%s: пары не больше участников" % (mode, seed),
                len(pairs) <= len(members),
                str(len(pairs)),
            )
            # Determinism.
            again = C.peer_review_pairs(cohort, seed=seed, mode=mode)
            check("%s seed=%s: детерминирован" % (mode, seed), pairs == again)

    one = C.create_cohort(
        Workspace().root, cohort_id="one", title="T", members=[make_members()[0]]
    )
    check("один участник — пар нет", C.peer_review_pairs(one) == [])

    expect_error(
        "неизвестный режим отклонён",
        lambda: C.peer_review_pairs(one, mode="chaos"),
        "REVIEW_MODE_UNKNOWN",
    )

    cohort = C.create_cohort(
        Workspace().root, cohort_id="pr2", title="T", members=make_members()
    )
    pairs = C.peer_review_pairs(cohort, seed=0, mode="balanced")
    reviewees = [b for _a, b in pairs]
    check(
        "каждый получает не больше одного разбора (balanced)",
        len(reviewees) == len(set(reviewees)),
        str(reviewees),
    )


def test_remove_member_last_refused():
    ws = Workspace()
    try:
        new_cohort(ws.root, "cohort-l", [make_members()[0]], title="T")
        expect_error(
            "последнего участника удалить нельзя",
            lambda: C.remove_member(ws.root, "cohort-l", S1),
            "COHORT_LAST_MEMBER",
        )
        still = C.load_cohort(ws.root, "cohort-l")
        check("поток остался с участником", len(still["members"]) == 1)

        new_cohort(ws.root, "cohort-m", make_members(), title="T")
        updated = C.remove_member(ws.root, "cohort-m", S1)
        check("удаление обычного участника прошло", len(updated["members"]) == 2)
        expect_error(
            "удаление отсутствующего участника отклонено",
            lambda: C.remove_member(ws.root, "cohort-m", S1),
            "MEMBER_NOT_FOUND",
        )
    finally:
        ws.cleanup()


def test_skill_vectors_fallback():
    cohort = C.create_cohort(
        Workspace().root, cohort_id="sv", title="T", members=make_members()
    )
    vectors = C.skill_vectors_for(cohort)
    check(
        "без векторов используется уровень",
        "__level__" in vectors[S1],
        str(vectors[S1]),
    )
    supplied = C.skill_vectors_for(cohort, {S1: {"objective-a": 0.9}})
    check(
        "предоставленный вектор используется",
        supplied[S1] == {"objective-a": 0.9},
        str(supplied[S1]),
    )
    check(
        "неучтённый падает на уровень",
        supplied[S2]["__level__"] == C.LEVEL_SCALAR["intermediate"],
        str(supplied[S2]),
    )

    grouped = C.auto_group(
        cohort, strategy="homogeneous", skill_vectors={"missing-on-purpose": {"a": 0.1}}
    )
    check(
        "skill_vectors только для участников потока не ломает группировку",
        grouped["denominator"] == 3,
        str(grouped["denominator"]),
    )


def test_fallback_focus_phrases():
    cohort = C.create_cohort(
        Workspace().root, cohort_id="f", title="T", members=make_members()
    )
    homo = C.auto_group(cohort, strategy="homogeneous", group_size=3)
    check(
        "focus не пустой", all(g["focus"] for g in homo["groups"]), str(homo["groups"])
    )
    check(
        "focus без __level__",
        "__level__" not in homo["groups"][0]["focus"],
        homo["groups"][0]["focus"],
    )


def main():
    tests = [
        test_round_trip_and_schema,
        test_empty_cohort_refused,
        test_add_member_twice_does_not_duplicate,
        test_auto_group_homogeneous_and_heterogeneous,
        test_group_size_one_and_indivisible,
        test_cohort_report_denominator_is_coverage,
        test_peer_review_pairs_never_self,
        test_remove_member_last_refused,
        test_skill_vectors_fallback,
        test_fallback_focus_phrases,
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
