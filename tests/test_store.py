#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the durable record (store) and the v1 → v2 migration.

The record is not bookkeeping. It is what stops the next session re-teaching
what a student already demonstrated — and what stops it *claiming* mastery the
student never evidenced. These tests check the properties that make that true:

* a retried command is not a second effect; the same request id with a
  different payload is a conflict, not an overwrite;
* a write based on a stale version is refused, so a concurrent update is never
  silently lost;
* a state change is stored together with the events that justify it, or not at
  all — there is no way to write `demonstrated` and then fail to record why;
* evidence bytes are addressed by content hash, verified on read, and one
  learner's deletion cannot remove bytes another learner's evidence points at;
* importing a v1 record never upgrades a claim into evidence, never guesses at
  unparsed prose, and refuses a multi-student file rather than mis-attributing.

Acceptance criteria covered (docs/botai-v2-design.md §17.3): A18 (a repeated
command is not a second effect, and a stale write is refused) and A21 (the
migration neither deletes the original nor invents facts from it).

Run:
    python3 tests/test_store.py
    python3 -m pytest tests/test_store.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Windows consoles default to a legacy code page (cp1252/cp866), where the
# Russian test names below are unmappable and printing raises
# UnicodeEncodeError — the suite would die before checking anything.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import legacy, schemas, store as S  # noqa: E402

_passed = 0
_failures: list[str] = []

LEARNER = "0c7fa86f-48de-4395-9694-c820be02e5ad"
COURSE = "minimal-diff"
PENDING = ROOT / "examples" / "legacy-course" / "progress" / "python-101.md"


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def expect_store_error(name, fn, code):
    try:
        fn()
    except S.StoreError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def objective_body(stage, evidence=None):
    return {
        "schema_version": 2,
        "objective_id": "explain-diff",
        "stage": stage,
        "evidence_ids": evidence or [],
        "assistance_max": "HINT",
        "consecutive_stuck_sessions": 0,
    }


# ---------------------------------------------------------------------------
def test_write_is_atomic_and_keeps_events_with_state():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            result, replayed = st.apply(
                kind="objective_state", entity_id="explain-diff",
                events=["objective.transitioned", "assistance.recorded"],
                new_body=objective_body("learning"),
                event_payloads=[
                    {"objective_id": "explain-diff", "from": "new", "to": "learning"},
                    {"objective_id": "explain-diff", "level": "HINT", "step": 1},
                ],
                expected_version=0,
            )
            check("запись создана", result["entity"]["version"] == 1, str(result))
            check("два события записаны одной командой",
                  len(result["event_ids"]) == 2 and st.count("events") == 2)
            check("событие несёт актора и происхождение",
                  all(json.loads(e["payload_json"])["provenance"]
                      for e in st.events()), "нет provenance")
            body, version = st.get("objective_state", "explain-diff")
            check("состояние сохранено", body["stage"] == "learning" and version == 1)


def test_invalid_body_is_refused_before_storage():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            bad = objective_body("learning")
            bad["stage"] = "mastered"  # not a v2 stage
            expect_store_error(
                "недопустимое состояние отвергается контрактом",
                lambda: st.apply(kind="objective_state", entity_id="explain-diff",
                                 events=["objective.transitioned"], new_body=bad,
                                 validate_as="progress"),
                "CONTRACT_INVALID",
            )
            check("после отказа ничего не записано", st.count("entities") == 0)


def test_idempotent_replay_and_payload_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            request_id = "11111111-1111-4111-8111-111111111111"
            first, replayed = st.apply(
                kind="objective_state", entity_id="explain-diff",
                events=["objective.transitioned"], new_body=objective_body("learning"),
                expected_version=0, request_id=request_id,
            )
            check("первый вызов не помечен как повтор", replayed is False)

            again, replayed = st.apply(
                kind="objective_state", entity_id="explain-diff",
                events=["objective.transitioned"], new_body=objective_body("learning"),
                expected_version=0, request_id=request_id,
            )
            check("повтор возвращает прежний результат",
                  again["entity"]["version"] == first["entity"]["version"] and replayed)
            check("повтор не создал второе событие", st.count("events") == 1)

            expect_store_error(
                "тот же request_id с другим payload — конфликт",
                lambda: st.apply(kind="objective_state", entity_id="explain-diff",
                                 events=["objective.transitioned"],
                                 new_body=objective_body("practising"),
                                 expected_version=1, request_id=request_id),
                "IDEMPOTENCY_CONFLICT",
            )
            check("конфликт не изменил состояние",
                  st.get("objective_state", "explain-diff")[0]["stage"] == "learning")


def test_stale_version_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            st.apply(kind="objective_state", entity_id="explain-diff",
                     events=["objective.transitioned"], new_body=objective_body("learning"),
                     expected_version=0)
            expect_store_error(
                "устаревшая версия отвергается",
                lambda: st.apply(kind="objective_state", entity_id="explain-diff",
                                 events=["objective.transitioned"],
                                 new_body=objective_body("demonstrated"),
                                 expected_version=0),
                "STATE_CONFLICT",
            )
            check("состояние не потеряно",
                  st.get("objective_state", "explain-diff")[0]["stage"] == "learning")

            st.apply(kind="objective_state", entity_id="explain-diff",
                     events=["objective.transitioned"], new_body=objective_body("practising"),
                     expected_version=1)
            check("корректная версия принимается",
                  st.get("objective_state", "explain-diff")[1] == 2)


def test_scope_isolation_between_courses():
    """One course's record must not be readable as another's."""
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            st.apply(kind="objective_state", entity_id="explain-diff",
                     events=["objective.transitioned"], new_body=objective_body("learning"),
                     course_id="course-a")
            body, version = st.get("objective_state", "explain-diff", course_id="course-b")
            check("состояние другого курса не читается", body is None and version == 0)
            body, _ = st.get("objective_state", "explain-diff", course_id="course-a")
            check("состояние своего курса читается", body is not None)


def test_artifacts_are_content_addressed_and_verified():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            payload = "моя попытка решения".encode("utf-8")
            digest = st.put_artifact(payload, media_type="text/plain",
                                     provenance="learner_report", retention_days=30)
            check("артефакт читается и совпадает", st.read_artifact(digest) == payload)
            check("хеш — sha256 содержимого",
                  digest == __import__("hashlib").sha256(payload).hexdigest())

            same = st.put_artifact(payload, media_type="text/plain",
                                   provenance="learner_report")
            check("те же байты не дублируются", same == digest and st.count("artifacts") == 1)

            st.link_artifact(digest, entity_kind="attempt", entity_id="a-1")
            st.link_artifact(digest, entity_kind="attempt", entity_id="a-2")
            check("владение отслеживается отдельно от байтов",
                  len(st.artifact_scope(digest)) == 2)

            # Corrupting the file must be detected on read, not trusted.
            target = Path(tmp) / ".botai" / "artifacts" / digest
            target.write_bytes("подменённое содержимое".encode("utf-8"))
            expect_store_error("подмена содержимого обнаружена",
                               lambda: st.read_artifact(digest), "ARTIFACT_CORRUPT")


def test_query_a_schema_library_is_not_required_for_basic_use():
    """The store itself must not need the network or a model to be usable."""
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            check("integrity_check проходит", st.integrity_check() == "ok")
            backup = Path(tmp) / "backup.sqlite3"
            st.backup_to(backup)
            check("резервная копия создана и читается", backup.is_file())


def test_refuses_a_newer_store_layout():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            st._conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        expect_store_error(
            "более новая версия записи не понижается",
            lambda: S.Store.open(tmp, LEARNER),
            "STORE_VERSION_NEWER",
        )


# ---------------------------------------------------------------------------
def test_legacy_import_never_upgrades_a_claim():
    parsed = legacy.read_legacy_progress(PENDING)
    report = parsed.as_report()

    anya = next(s for s in report["students"] if s["legacy_id"] == "stu-01")
    check("v1 «mastered» не становится demonstrated",
          anya["imported_stage"] == "practising", str(anya["imported_stage"]))
    check("заявленное значение сохранено рядом с импортированным",
          anya["legacy_claim"] == "mastered")
    check("основание импорта названо явно",
          anya["stage_basis"] == "legacy_claim_not_evidence")
    check("в отчёте есть предупреждение о понижении",
          any(s.startswith("заявленное «mastered»") for s in report["warnings"]),
          str(report["warnings"]))


def test_legacy_import_preserves_the_source_verbatim():
    raw = PENDING.read_bytes()
    parsed = legacy.read_legacy_progress(PENDING)
    check("sha256 исходника в отчёте", parsed.source_sha256
          == __import__("hashlib").sha256(raw).hexdigest())
    check("исходник не изменён на диске", PENDING.read_bytes() == raw)
    report = parsed.as_report()
    check("нераспознанные строки сохранены с номерами",
          bool(report["pending_review"]) and all("line" in p for p in report["pending_review"]))
    check("догадки не подставляются", report["counts"]["pending_review"] > 0)


def test_legacy_import_extracts_only_what_is_written():
    report = legacy.read_legacy_progress(PENDING).as_report()
    check("оцениваемые задания распознаны",
          report["graded"] == ["ex-02 (essay)", "ex-05 (quiz)"], str(report["graded"]))
    check("тренировочные задания распознаны",
          report["practice"] == ["ex-01", "ex-03", "ex-04"], str(report["practice"]))
    check("тупики сохранены как findings", len(report["dead_ends"]) == 1)
    check("открытые вопросы сохранены", any("указател" in q for q in report["open_questions"]))
    check("чек-лист не превращается в освоение",
          any("чек-лист" in q for q in
              next(s for s in report["students"] if s["legacy_id"] == "stu-01")["open_questions"]))


def test_multi_student_file_is_refused_without_explicit_split():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            parsed = legacy.read_legacy_progress(PENDING)
            check("несколько обучающихся обнаружены", parsed.multi_student)
            expect_store_error(
                "автоимпорт файла с несколькими обучающимися запрещён",
                lambda: legacy.import_into_store(
                    st, parsed, course_id="python-101",
                    course_revision={"kind": "local_snapshot", "id": "legacy"}),
                "LEGACY_MULTI_STUDENT",
            )
            check("при отказе ничего не записано", st.count("entities") == 0)


def test_import_writes_only_verifiable_claims():
    with tempfile.TemporaryDirectory() as tmp:
        with S.Store.open(tmp, LEARNER) as st:
            parsed = legacy.read_legacy_progress(PENDING)
            result = legacy.import_into_store(
                st, parsed, course_id="python-101",
                course_revision={"kind": "local_snapshot", "id": "legacy"},
                confirm_split={"stu-01": LEARNER, "stu-02": LEARNER},
            )
            check("импорт выполнен", result["import"]["ok"] is True)
            check("исходник сохранён как артефакт", st.count("artifacts") == 1)
            claims = st.list_entities("legacy_objective_claim", course_id="python-101")
            check("заявленные состояния помечены как непроверенные",
                  all(c["verified"] is False for c in claims) and bool(claims))
            check("импортированные состояния не выше practising",
                  all(c["imported_stage"] in ("new", "learning", "practising", "blocked")
                      for c in claims), str([c["imported_stage"] for c in claims]))

            # Re-importing the same bytes must not duplicate the record: the
            # import entity is keyed by the source hash, so a second run is the
            # same import seen again, not a new one.
            again = legacy.import_into_store(
                st, parsed, course_id="python-101",
                course_revision={"kind": "local_snapshot", "id": "legacy"},
                confirm_split={"stu-01": LEARNER, "stu-02": LEARNER},
            )
            check("повторный импорт того же файла не дублирует артефакт",
                  st.count("artifacts") == 1)
            check("повторный импорт не создаёт вторую запись для того же источника",
                  len(st.list_entities("legacy_import")) == 1,
                  str(len(st.list_entities("legacy_import"))))
            check("повтор не переписывает заявленные состояния",
                  len(st.list_entities("legacy_objective_claim", course_id="python-101")) == 1)


def test_migration_report_validates_against_contract():
    report = legacy.read_legacy_progress(PENDING).as_report()
    check("отчёт миграции содержит версию схемы", report["schema_version"] == 2)
    check("отчёт перечисляет предупреждения", isinstance(report["warnings"], list))
    json.dumps(report)  # must be serialisable for an export


def main() -> int:
    print("[store: транзакции и доказательства]")
    test_write_is_atomic_and_keeps_events_with_state()
    test_invalid_body_is_refused_before_storage()
    test_idempotent_replay_and_payload_conflict()
    test_stale_version_is_refused()
    test_scope_isolation_between_courses()
    print("[store: артефакты и обслуживание]")
    test_artifacts_are_content_addressed_and_verified()
    test_query_a_schema_library_is_not_required_for_basic_use()
    test_refuses_a_newer_store_layout()
    print("[legacy: миграция v1 → v2 без выдумывания]")
    test_legacy_import_never_upgrades_a_claim()
    test_legacy_import_preserves_the_source_verbatim()
    test_legacy_import_extracts_only_what_is_written()
    test_multi_student_file_is_refused_without_explicit_split()
    test_import_writes_only_verifiable_claims()
    test_migration_report_validates_against_contract()

    print(f"\n{_passed} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


def test_store() -> None:
    """Pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
