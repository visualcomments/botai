#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты обновления: `scripts/update.py`, `scripts/courses.py`, `scripts/harness.py`.

Проверяют не «код запускается», а правила, ради которых обновление существует:

* обновление обвязки **не трогает** студенческие каталоги;
* локально изменённый файл **сохраняется**, а не перезаписывается;
* `--overwrite` заменяет его, но оставляет копию в `.botai/backup/`;
* архив, пытающийся писать вне каталога, отвергается;
* симлинки скилл-ферм в архиве не ломают распаковку;
* курс обновляется только из своего источника и не теряет незакоммиченную работу;
* отпечаток курса покрывает всё дерево, а не только файлы обвязки.

Запуск:
    python3 tests/test_update.py        # автономно
    python3 -m pytest tests/ -q         # через pytest
"""

from __future__ import annotations

import io
import json
import shutil
import os
import subprocess
import sys
import tarfile
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
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harness as H  # noqa: E402

_passed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def make_harness_tree(base: Path, version: str, extra_files=None, prefix="up"):
    """Минимальное дерево обвязки для тестов (без сети и без полного репозитория)."""
    src = base / prefix
    (src / ".agents" / "skills" / "s-alpha").mkdir(parents=True)
    (src / ".opencode" / "agent").mkdir(parents=True)
    (src / "scripts").mkdir(parents=True)
    (src / "docs").mkdir(parents=True)
    (src / "AGENTS.md").write_text("# policy %s\n" % version, encoding="utf-8")
    (src / "README.md").write_text("# readme\n", encoding="utf-8")
    (src / "Makefile").write_text("help:\n\t@echo hi\n", encoding="utf-8")
    (src / "VERSION").write_text(version + "\n", encoding="utf-8")
    (src / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (src / ".agents" / "skills" / "s-alpha" / "SKILL.md").write_text(
        "---\nname: s-alpha\n---\n\nalpha\n", encoding="utf-8")
    (src / ".opencode" / "agent" / "botai.md").write_text("agent\n", encoding="utf-8")
    (src / "scripts" / "harness.py").write_text(
        (SCRIPTS / "harness.py").read_text(encoding="utf-8"), encoding="utf-8")
    (src / "scripts" / "update.py").write_text(
        (SCRIPTS / "update.py").read_text(encoding="utf-8"), encoding="utf-8")
    (src / "scripts" / "courses.py").write_text(
        (SCRIPTS / "courses.py").read_text(encoding="utf-8"), encoding="utf-8")
    (src / "scripts" / "cli.py").write_text(
        (SCRIPTS / "cli.py").read_text(encoding="utf-8"), encoding="utf-8")
    (src / "docs" / "updating.md").write_text("# updating\n", encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return src


def install_from(src: Path, dest: Path):
    """Установка «как install.py»: копируем обвязку и пишем запись."""
    import shutil
    for rel in H.managed_files(src):
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src / rel), str(d))
    for runtime in ("courses", "progress", "dist", ".botai"):
        (dest / runtime).mkdir(parents=True, exist_ok=True)
    H.save_record(dest, {
        # Как install.py: текущая схема записи.
        "schema": H.RECORD_SCHEMA, "version": H.read_version(src), "source": str(src),
        "ref": "main", "mode": "install", "installed_at": H.now_iso(),
        "files": H.fingerprint(dest),
    })


def run_update(project: Path, source: Path, *args):
    """Запустить update.py как отдельный процесс и вернуть (код, вывод)."""
    p = subprocess.run(
        [sys.executable, str(project / "scripts" / "update.py"),
         "--root", str(project), "--source", str(source), "--mode", "archive", *args],
        capture_output=True, text=True, timeout=180,
    )
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------------------
def test_local_edit_is_preserved():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        up1 = make_harness_tree(base, "1.0.0", prefix="up1")
        project = base / "project"
        project.mkdir()
        install_from(up1, project)

        # Правка студента и его материал.
        (project / "AGENTS.md").write_text("# policy 1.0.0\n\nмоя заметка\n", encoding="utf-8")
        (project / "progress").mkdir(exist_ok=True)
        (project / "progress" / "course.md").write_text("# прогресс\n", encoding="utf-8")

        # Новая версия upstream.
        up2 = make_harness_tree(base, "2.0.0", prefix="up2",
                                extra_files={"docs/new.md": "new\n"})

        rc, out = run_update(project, up2, "--dry-run")
        check("предпросмотр не пишет на диск",
              "AGENTS.md" in out and (project / "VERSION").read_text().strip() == "1.0.0")

        rc, out = run_update(project, up2)
        check("обновление завершилось успешно", rc == 0, out[-300:])
        check("версия обвязки обновлена",
              (project / "VERSION").read_text().strip() == "2.0.0")
        check("правка студента сохранена",
              "моя заметка" in (project / "AGENTS.md").read_text(encoding="utf-8"))
        check("правка названа в отчёте", "AGENTS.md" in out and "сохранены" in out)
        check("новый файл upstream добавлен", (project / "docs" / "new.md").is_file())
        check("прогресс студента не тронут",
              (project / "progress" / "course.md").read_text(encoding="utf-8") == "# прогресс\n")

        # --overwrite заменяет правку, но копирует старую версию в бэкап.
        rc, out = run_update(project, up2, "--overwrite")
        check("--overwrite заменяет правку",
              "моя заметка" not in (project / "AGENTS.md").read_text(encoding="utf-8"))
        backups = list((project / ".botai" / "backup").rglob("AGENTS.md"))
        check("старая версия попала в .botai/backup/", bool(backups))
        if backups:
            check("в бэкапе именно правка студента",
                  "моя заметка" in backups[0].read_text(encoding="utf-8"))


def test_student_dirs_never_touched():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        up1 = make_harness_tree(base, "1.0.0", prefix="up1")
        project = base / "project"
        project.mkdir()
        install_from(up1, project)

        (project / "courses" / "my-course").mkdir(parents=True, exist_ok=True)
        (project / "courses" / "my-course" / "README.md").write_text("курс\n", encoding="utf-8")

        # Вредоносный/ошибочный upstream кладёт файл в студенческий каталог.
        up2 = make_harness_tree(base, "9.9.9", prefix="up2",
                                extra_files={"courses/hacked.md": "не должно появиться\n"})
        rc, out = run_update(project, up2)
        check("обновление прошло", rc == 0, out[-300:])
        check("файл в courses/ от upstream НЕ создан",
              not (project / "courses" / "hacked.md").exists())
        check("материал курса студента цел",
              (project / "courses" / "my-course" / "README.md").read_text(encoding="utf-8") == "курс\n")


def test_archive_safety():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # 1. Симлинки ферм не ломают распаковку (GitHub шлёт их в архиве).
        src = base / "src"
        (src / ".claude" / "skills").mkdir(parents=True)
        (src / "AGENTS.md").write_text("x\n", encoding="utf-8")
        os.symlink("../../.agents/skills/foo", src / ".claude" / "skills" / "foo")
        tar = base / "ok.tar.gz"
        with tarfile.open(tar, "w:gz") as tf:
            tf.add(src, arcname="up-main")
        out_dir = base / "out"
        try:
            H.safe_extract_tar(tar, out_dir)
            check("архив с симлинками ферм распаковывается", True)
        except Exception as e:  # noqa: BLE001
            check("архив с симлинками ферм распаковывается", False, str(e))

        # 2. Выход за пределы каталога отвергается.
        evil = base / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tf:
            info = tarfile.TarInfo("../../etc/passwd")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        try:
            H.safe_extract_tar(evil, base / "out2")
            check("архив с выходом за каталог отвергнут", False, "не отвергнут")
        except RuntimeError:
            check("архив с выходом за каталог отвергнут", True)


def test_course_fingerprint_covers_whole_tree():
    with tempfile.TemporaryDirectory() as tmp:
        cdir = Path(tmp) / "course"
        (cdir / "lectures").mkdir(parents=True)
        (cdir / "tools").mkdir(parents=True)
        (cdir / "README.md").write_text("r\n", encoding="utf-8")
        for i in range(40):
            (cdir / "lectures" / ("%02d_l.md" % i)).write_text("l\n", encoding="utf-8")
        (cdir / "tools" / "t.py").write_text("print(1)\n", encoding="utf-8")

        harness_fp = H.fingerprint(cdir)          # только файлы обвязки botai
        tree_fp = H.fingerprint_tree(cdir)        # всё дерево курса
        check("отпечаток дерева больше обвязочного", len(tree_fp) > len(harness_fp),
              f"{len(tree_fp)} vs {len(harness_fp)}")
        check("отпечаток дерева покрывает лекции",
              sum(1 for k in tree_fp if k.startswith("lectures/")) == 40)
        check("служебная запись исключается",
              not any(k.endswith(H.COURSE_RECORD) for k in
                      H.fingerprint_tree(cdir, skip_prefixes=(H.COURSE_RECORD,))))

        # Перезапись файла меняет его хэш — на этом стоит детектор правок.
        before = H.fingerprint_tree(cdir)["README.md"]
        (cdir / "README.md").write_text("изменено\n", encoding="utf-8")
        check("правка файла меняет отпечаток",
              H.fingerprint_tree(cdir)["README.md"] != before)


def test_course_update_refuses_without_source():
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        (project / "courses" / "manual").mkdir(parents=True)
        (project / "courses" / "manual" / "README.md").write_text("r\n", encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "courses.py"), "update",
             "--course", "manual", "--root", str(project)],
            capture_output=True, text=True, timeout=120,
        )
        out = p.stdout + p.stderr
        check("курс без источника не обновляется", p.returncode == 2, out[-200:])
        check("отказ объяснён", "нет ни git-истории" in out or "источник" in out)


def test_dirty_course_is_not_touched():
    """Курс с незакоммиченной работой: обновление останавливается, файлы целы."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # upstream-курс как git-репозиторий
        up = base / "up"
        (up / "lectures").mkdir(parents=True)
        (up / "README.md").write_text("v1\n", encoding="utf-8")
        (up / "lectures" / "01.md").write_text("лекция\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=up, check=True)
        for cmd in (["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@l",
                     "commit", "-qm", "v1"]):
            subprocess.run(cmd, cwd=up, check=True)

        project = base / "project"
        (project / "courses").mkdir(parents=True)
        cdir = project / "courses" / "c1"
        subprocess.run(["git", "clone", "-q", str(up), str(cdir)], check=True)
        H.save_json_atomic(cdir / H.COURSE_RECORD, {
            "schema": 1, "source": str(up), "ref": "main",
            "revision": H.git_head(cdir, short=False), "obtained_at": H.now_iso(),
            "files": H.fingerprint_tree(cdir, skip_prefixes=(H.COURSE_RECORD,)),
        })

        # Работа студента, не закоммиченная.
        (cdir / "lectures" / "01.md").write_text("лекция + мой конспект\n", encoding="utf-8")
        (cdir / "assignments").mkdir(exist_ok=True)
        (cdir / "assignments" / "a.md").write_text("ответ\n", encoding="utf-8")

        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "courses.py"), "update",
             "--course", "c1", "--root", str(project)],
            capture_output=True, text=True, timeout=180,
        )
        out = p.stdout + p.stderr
        check("грязный курс: обновление отказано", p.returncode == 1, out[-300:])
        check("отказ называет незакоммиченные файлы", "незакоммиченные" in out)
        check("конспект студента цел",
              "мой конспект" in (cdir / "lectures" / "01.md").read_text(encoding="utf-8"))
        check("работа студента цела", (cdir / "assignments" / "a.md").is_file())


def test_legacy_record_is_migrated_once():
    """Старая запись установки не должна блокировать новые каталоги обвязки.

    Запись schema 1 (до scripts/harness.py) описывает другой набор путей:
    каталога, добавленного позже, в ней нет. Синхронизация сочла бы такие
    файлы «нетронутыми локальными», и они не доехали бы никогда. При этом
    миграция обязана сохранить базу сравнения: иначе правка студента станет
    «нетронутой» и будет затёрта.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        up1 = make_harness_tree(base, "1.0.0", prefix="up1")
        project = base / "project"
        project.mkdir()
        install_from(up1, project)

        record = H.load_record(project)
        record["schema"] = 1                      # установка до scripts/harness.py
        record["files"] = {k: v for k, v in record["files"].items()
                           if not k.startswith("scripts/")}
        H.save_record(project, record)
        (project / "AGENTS.md").write_text("# policy 1.0.0\n\nмоя заметка\n", encoding="utf-8")

        up2 = make_harness_tree(base, "2.0.0", prefix="up2")
        rc, out = run_update(project, up2)
        check("обновление со старой записью проходит", rc == 0, out[-300:])
        check("запись миграции называет причину", "старого образца" in out)
        check("схема записи поднята до текущей",
              H.load_record(project).get("schema") == H.RECORD_SCHEMA)
        check("правка студента пережила миграцию",
              "моя заметка" in (project / "AGENTS.md").read_text(encoding="utf-8"), out[-300:])
        check("пути, отсутствовавшие в старой записи, записаны заново",
              any(k.startswith("scripts/") for k in
                  (H.load_record(project).get("files") or {})))


def test_check_uses_commit_not_only_cached_version():
    """`--check` не должен верить закешированной версии.

    raw.githubusercontent отдаёт VERSION с задержкой в несколько минут после
    публикации, поэтому «версия та же» не доказывает, что обновления нет.
    Проверка обязана сверять ещё и коммит: у установки он записан, у upstream
    читается через git ls-remote, а тот задержкой не страдает.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # upstream как git-репозиторий (с полным деревом обвязки), чтобы
        # ls-remote отвечал и установка была настоящей
        up = make_harness_tree(base, "1.0.0", prefix="up")
        subprocess.run(["git", "init", "-q"], cwd=up, check=True)
        for cmd in (["git", "add", "-A"],
                    ["git", "-c", "user.name=t", "-c", "user.email=t@l",
                     "commit", "-qm", "v1"]):
            subprocess.run(cmd, cwd=up, check=True)
        rev1 = H.git_head(up, short=False)

        project = base / "project"
        project.mkdir()
        install_from(up, project)
        rec = H.load_record(project)
        rec["source"] = str(up)
        rec["revision"] = rev1
        H.save_record(project, rec)

        # upstream выпустил новый коммит, НЕ меняя VERSION (тот же кеш-эффект)
        (up / "docs").mkdir(exist_ok=True)
        (up / "docs" / "new.md").write_text("n\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=up, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@l",
                        "commit", "-qm", "v1+1"], cwd=up, check=True)
        rev2 = H.git_head(up, short=False)
        check("коммиты upstream действительно разные", rev1 != rev2)

        p = subprocess.run(
            [sys.executable, str(project / "scripts" / "update.py"),
             "--root", str(project), "--source", str(up), "--mode", "archive", "--check"],
            capture_output=True, text=True, timeout=180,
        )
        out = p.stdout + p.stderr
        check("та же версия, но новый коммит: обновление найдено",
              p.returncode == 10, out[-300:])
        check("причина названа (кеш версии)", "коммит" in out, out[-300:])


def test_update_reports_bad_source():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        up = make_harness_tree(base, "1.0.0", prefix="up")
        project = base / "project"
        project.mkdir()
        install_from(up, project)
        # Источник, который не является ни каталогом, ни GitHub-URL.
        rc, out = run_update(project, Path("/nope/does/not/exist"))
        check("недоступный источник — понятная ошибка, а не падение",
              rc != 0 and ("нет файлов обвязки" in out or "источник" in out), out[-200:])
        check("при неудаче версия не изменилась",
              (project / "VERSION").read_text().strip() == "1.0.0")


def main() -> int:
    print("[update: сохранность работы]")
    test_local_edit_is_preserved()
    print("[update: студенческие каталоги]")
    test_student_dirs_never_touched()
    print("[update: безопасность архивов]")
    test_archive_safety()
    print("[course: отпечаток дерева]")
    test_course_fingerprint_covers_whole_tree()
    print("[course: отказы]")
    test_course_update_refuses_without_source()
    test_dirty_course_is_not_touched()
    print("[update: миграция старой записи]")
    test_legacy_record_is_migrated_once()
    print("[update: проверка обновления]")
    test_check_uses_commit_not_only_cached_version()
    test_update_reports_bad_source()

    print(f"\n{_passed} passed, {len(_failures)} failed")
    for f in _failures:
        print(f"  - {f}")
    return 1 if _failures else 0


def test_update_and_courses() -> None:
    """Pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
