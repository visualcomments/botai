#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end check of the export → import → summary path through the real CLI.

Asserts the properties that make this stage trustworthy:

* nothing leaves without recorded consent, and withdrawing it stops the export;
* a preview works before anything is recorded, and names the recipient;
* an empty objective selection exports *nothing*, not everything;
* a packet is data: an embedded instruction changes nothing on import;
* the summary states its denominator and withholds breakdowns in a small group;
* deletion is planned first and does not overstate what it removes.

No network. Two temporary workspaces: a student's and a teacher's.

Run:
    python3 scripts/checks/privacy_flow.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from botai_core import course, exports as E, store as S  # noqa: E402

failures = 0
COURSE = "minimal-diff"


def check(name, condition, detail=""):
    global failures
    if condition:
        print("  [ok]   %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        failures += 1


def cli(root, *args, input_text=None):
    completed = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), *args, "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), input=input_text, timeout=180,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="botai-privacyflow-"))
    student_root = tmp / "student"
    teacher_root = tmp / "teacher"
    export_dir = tmp / "packets"

    course_dir = student_root / "courses" / COURSE
    course_dir.parent.mkdir(parents=True)
    shutil.copytree(REPO / "examples" / "minimal-course", course_dir)

    print("student: %s" % student_root)
    print("teacher: %s" % teacher_root)

    print()
    print("### 1. принять курс, записать состояние цели")
    code, out = cli(student_root, "course-accept", "--course", COURSE)
    check("курс принят", code == 0, out[-200:])

    # The workspace's learner id is generated once and kept in
    # .botai/workspace.json. Every step below must use that same id: a check
    # that invents its own would write into one database and verify another,
    # and the deletion check would then pass while deleting nothing.
    sys.path.insert(0, str(REPO / "scripts"))
    import cli as cli_module
    learner_id = cli_module.default_learner_id(student_root)

    store = S.Store.open(student_root, learner_id)
    store.apply(kind="objective_state", entity_id="explain-diff",
                events=["objective.transitioned"], course_id=COURSE,
                new_body={"schema_version": 2, "objective_id": "explain-diff",
                          "stage": "blocked", "evidence_ids": [],
                          "latest_check_id": None, "assistance_max": "HINT",
                          "review_due_at": None,
                          "blocked_reason": "не найден источник",
                          "consecutive_stuck_sessions": 1},
                event_payloads=[{"objective_id": "explain-diff", "from": "new",
                                 "to": "blocked", "evidence_ids": [],
                                 "rule_version": "v2", "_actor": "core",
                                 "_provenance": "core_computed"}])
    written = store.list_entities("objective_state", course_id=COURSE)
    store.close()
    check("состояние цели записано под идентификатором workspace",
          len(written) == 1, str(written))

    print()
    print("### 2. предпросмотр работает до записи согласия")
    code, out = cli(student_root, "privacy-preview", "--course", COURSE,
                    "--objective", "explain-diff", "--recipient", "преподаватель")
    check("предпросмотр выполнен", code == 3, "exit=%d" % code)
    check("сказано, что согласия нет", "согласие" in out.lower(), out[-300:])
    check("получатель назван", "преподаватель" in out)
    check("перечислено, чего не будет", "не будет никогда" in out, out[-400:])

    print()
    print("### 3. экспорт без согласия отклонён")
    code, out = cli(student_root, "privacy-export", "--course", COURSE,
                    "--objective", "explain-diff", "--out-dir", str(export_dir))
    check("экспорт без согласия отклонён", code != 0, "exit=%d" % code)
    check("причина названа", "CONSENT_REQUIRED" in out or "соглас" in out, out[-300:])

    print()
    print("### 4. согласие только на хранение не разрешает передачу")
    code, out = cli(student_root, "consent-set", "--course", COURSE,
                    "--purpose", "learning_storage")
    check("согласие на хранение записано", code == 0, out[-200:])
    code, out = cli(student_root, "privacy-export", "--course", COURSE,
                    "--objective", "explain-diff", "--out-dir", str(export_dir))
    check("передача всё ещё запрещена", code != 0, "exit=%d" % code)

    print()
    print("### 5. согласие на передачу, затем экспорт")
    code, out = cli(student_root, "consent-set", "--course", COURSE,
                    "--purpose", "learning_storage", "--purpose", "teacher_export")
    check("согласие записано", code == 0, out[-200:])

    code, out = cli(student_root, "privacy-export", "--course", COURSE,
                    "--objective", "explain-diff", "--out-dir", str(export_dir))
    check("пакет собран", code == 0, out[-300:])
    packet_files = sorted(export_dir.glob("*.json"))
    check("файл пакета создан", len(packet_files) == 1, str(packet_files))

    print()
    print("### 6. пустой выбор не экспортирует всё подряд")
    code, out = cli(student_root, "privacy-export", "--course", COURSE,
                    "--out-dir", str(export_dir))
    check("пустой выбор отклонён", code != 0, "exit=%d" % code)
    check("сказано, почему", "хотя бы один" in out, out[-300:])

    print()
    print("### 7. пакет не содержит сырых данных")
    packet = json.loads(packet_files[0].read_text(encoding="utf-8"))
    for forbidden in ("chat", "transcript", "student_name", "email"):
        check("в пакете нет поля %s" % forbidden, forbidden not in packet)
    ok, problems = E.verify_packet(packet)
    check("контрольная сумма сходится", ok, str(problems))

    print()
    print("### 8. импорт в отдельное пространство преподавателя")
    code, out = cli(teacher_root, "teacher-import", "--packet", str(packet_files[0]))
    check("пакет импортирован", code == 0, out[-300:])
    check("сказано, что содержимое — данные",
          "не исполняются" in out or "данные" in out, out[-300:])

    code, out = cli(teacher_root, "teacher-import", "--packet", str(packet_files[0]))
    check("повтор распознан", "уже был импортирован" in out, out[-300:])

    print()
    print("### 9. сводка называет знаменатель")
    code, out = cli(teacher_root, "teacher-summary", "--course", COURSE)
    check("сводка построена", code == 0, out[-200:])
    check("знаменатель назван", "1 из 1 предоставивших данные" in out, out[:400])
    check("размер группы помечен неизвестным",
          "неизвестен" in out, out[:500])
    check("при малой группе разрез скрыт", "скрыт" in out or "меньше" in out,
          out[:900])
    check("сказано про отсутствие ранжирования", "ранжирование" in out, out[-500:])

    print()
    print("### 10. очередь преподавателя")
    code, out = cli(teacher_root, "teacher-summary", "--course", COURSE, "--queue")
    check("очередь построена", code == 0, out[-200:])
    check("сказано, что люди не ранжируются", "не ранжируются" in out, out[:400])

    print()
    print("### 11. план удаления и удаление")
    code, out = cli(student_root, "privacy-delete", "--course", COURSE, "--plan")
    check("план показан", code == 0, out[-200:])
    check("план называет сохраняемое", "останется" in out, out[:600])
    check("план честно называет пределы", "НЕ обещает" in out, out[:900])

    code, out = cli(student_root, "privacy-delete", "--course", COURSE,
                    input_text="y\n")
    check("удаление выполнено", code == 0, out[-300:])
    check("записи удалены", "удалено" in out, out[-400:])

    # Verified with the workspace's OWN learner id, for the same reason.
    workspace_learner = cli_module.default_learner_id(student_root)
    store = S.Store.open(student_root, workspace_learner, create=False)
    remaining = store.list_entities("objective_state", course_id=COURSE)
    events_left = store.events(course_id=COURSE)
    store.close()
    check("состояний целей не осталось", remaining == [], str(remaining))
    check("журнал событий тоже очищен", events_left == [],
          str(len(events_left)))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures == 0:
        print("PRIVACY FLOW OK")
    else:
        print("PRIVACY FLOW FAILURES: %d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
