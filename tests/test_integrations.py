#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the external-platform adapters: offline halves only, no sockets.

Claims under test:

* `GitHubClassroomClient(allow_network=True)` raises `NETWORK_NOT_PERMITTED`;
* `parse_assignment_ref` returns owner/repo/assignment for a GitHub Classroom
  URL and refuses a non-GitHub URL;
* `build_review_report` states the test denominator and does NOT claim a grade
  — it says so explicitly ("не оценка", "Оценка не выставляется");
* `render_action_yaml` returns text containing `${{ secrets.GITHUB_TOKEN }}` and
  refuses to write outside the workspace;
* `JupyterAssistant.analyze_notebook` detects a recorded `NameError` and returns
  Russian advice, an unknown exception gets generic advice echoing the `ename`,
  and NO cell is executed — asserted by including a cell whose source would
  create a file if run, then asserting the file does not exist;
* `suggest_cell_fix(..., assessment="graded")` raises
  `SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT`;
* `LmsConnector.parse_ims_manifest` parses a small IMS manifest; malformed XML
  raises `MANIFEST_MALFORMED`; `import_assignments` derives ids through
  `paths.safe_name`; `export_grades` writes a CSV whose header is exactly
  `student_id,objective_id,stage` and contains no grade column;
* `lti_not_supported()` raises `LTI_REQUIRES_EXTERNAL` and `sso_not_supported()`
  raises `SSO_REQUIRES_EXTERNAL`.

Run:
    python3 tests/test_integrations.py
    python3 -m pytest tests/test_integrations.py -q
"""

from __future__ import annotations

import json
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

from botai_core import integrations as I  # noqa: E402

_passed = 0
_failures: list[str] = []

CANARY = "pwned-from-notebook.txt"

IMS_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="manifest-1">
  <title>Курс про Git</title>
  <organizations>
    <organization identifier="org-1">
      <title>Организация</title>
      <item identifier="item-a1">
        <title>Разница коммитов</title>
        <learningObjectiveId>explain-diff</learningObjectiveId>
      </item>
      <item identifier="item-b2">
        <title>Перенос файлов в индекс</title>
        <learningObjectiveId>apply-staging</learningObjectiveId>
      </item>
    </organization>
  </organizations>
</manifest>
"""

CANARY_SOURCE = (
    "__import__('pathlib').Path(%r).write_text('pwned', encoding='utf-8')\n" % CANARY
)


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
    except I.IntegrationError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def notebook(cells):
    return {
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def code_cell(source, outputs=None):
    return {
        "cell_type": "code",
        "execution_count": 1,
        "metadata": {},
        "source": source if isinstance(source, list) else source.splitlines(True),
        "outputs": outputs or [],
    }


def write_notebook(path, document):
    Path(path).write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return Path(path)


def test_network_is_refused():
    expect_error(
        "allow_network=True отклонён",
        lambda: I.GitHubClassroomClient(allow_network=True),
        "NETWORK_NOT_PERMITTED",
    )
    client = I.GitHubClassroomClient()
    check("обычное создание работает", client is not None)
    status = client.remote_calls_disabled()
    check("remote_calls False", status["remote_calls"] is False, str(status))
    check("token не сконфигурирован", status["token_configured"] is False)
    check(
        "заметка названа по-русски",
        "GitHub API" in status["note_ru"] or "API" in status["note_ru"],
        status["note_ru"],
    )

    client_with_token = I.GitHubClassroomClient(token="ghp_abc")
    check(
        "token сохраняется, но сетевых вызовов всё равно нет",
        client_with_token.remote_calls_disabled()["remote_calls"] is False,
    )


def test_parse_assignment_ref():
    client = I.GitHubClassroomClient()
    ref = client.parse_assignment_ref(
        "https://classroom.github.com/classroom/teacher/assignment-abc-repo/assignment-abc-repo"
    )
    check("owner разобран", ref["owner"] == "teacher", str(ref))
    check("assignment разобран", ref["assignment"] == "assignment-abc-repo", str(ref))
    # The canonical shape is `classroom/<owner>/<assignment>/assignment-<repo>`,
    # so the `assignment-` prefix is the URL's marker, not part of the repo.
    check("repo разобран без префикса assignment-", ref["repo"] == "abc-repo", str(ref))
    check(
        "путь-маркер не попал в repo",
        not ref["repo"].startswith("assignment-"),
        ref["repo"],
    )
    # Without a scheme, the module prefixes https:// and still parses.
    bare = client.parse_assignment_ref(
        "github.com/classroom/teacher/hw/assignment-hw-repo"
    )
    check(
        "URL без схемы принимается",
        bare["owner"] == "teacher" and bare["assignment"] == "hw",
        str(bare),
    )

    expect_error(
        "не-GitHub URL отклонён",
        lambda: client.parse_assignment_ref("https://example.com/a/b"),
        "NOT_A_GITHUB_URL",
    )
    expect_error(
        "пустой URL отклонён",
        lambda: client.parse_assignment_ref(""),
        "ASSIGNMENT_URL_EMPTY",
    )
    expect_error(
        "GitHub без classroom-пути отклонён",
        lambda: client.parse_assignment_ref("https://github.com/foo/bar"),
        "ASSIGNMENT_REF_UNRECOGNIZED",
    )
    expect_error(
        "не-строка отклонена",
        lambda: client.parse_assignment_ref(None),
        "ASSIGNMENT_URL_EMPTY",
    )


def test_build_review_report_claims_no_grade():
    client = I.GitHubClassroomClient()
    report = client.build_review_report(
        diff_text="+print('hello')\n-print('bye')\n",
        tests_passed=3,
        tests_total=5,
        notes="нужны тесты на падение",
    )
    check("знаменатель тестов назван явно", "3 из 5" in report, report[:400])
    check("процент пройденных", "(60%)" in report, report[:400])
    check("сказано, что это доля, а не оценка", "не оценка" in report, report[:400])
    check(
        "прямо сказано, что оценка не выставляется",
        "Оценка не выставляется" in report,
        report[-300:],
    )
    check("заголовок назван проверкой", "Проверка задания" in report, report[:120])
    check("чек-лист присутствует", "Чек-лист" in report, report)
    check("заметки выведены", "нужны тесты на падение" in report, report)
    check("размер изменения назван", "Строк в diff" in report, report[-200:])
    check("выдача — строка", isinstance(report, str))

    zero = client.build_review_report(
        diff_text="", tests_passed=0, tests_total=0, notes=None
    )
    check("0 из 0 не падает", "0 из 0" in zero, zero[:200])
    expect_error(
        "нецелое число тестов отклонено",
        lambda: client.build_review_report(
            diff_text="", tests_passed="много", tests_total=5, notes=""
        ),
        "TESTS_NOT_NUMERIC",
    )
    expect_error(
        "отрицательные тесты отклонены",
        lambda: client.build_review_report(
            diff_text="", tests_passed=-1, tests_total=5, notes=""
        ),
        "TESTS_NEGATIVE",
    )

    clamped = client.build_review_report(
        diff_text="", tests_passed=10, tests_total=3, notes=""
    )
    check("passed>total обрезается до total", "3 из 3" in clamped, clamped[:200])


def test_render_action_yaml():
    client = I.GitHubClassroomClient()
    text = client.render_action_yaml()
    check("выдача — строка", isinstance(text, str) and text)
    check(
        "ссылается на секрет GitHub", "${{ secrets.GITHUB_TOKEN }}" in text, text[:400]
    )
    check("workflow назван", "botai-reviewer" in text, text[:200])
    check("токен не выдуман", "ghp_" not in text and "github_pat_" not in text)
    check("нет других секретов", "AKIA" not in text)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = client.render_action_yaml(
            output_path=str(root / ".github" / "workflows" / "botai-reviewer.yml"),
            root=root,
        )
        check(
            "файл записан внутри workspace",
            target.resolve().is_relative_to(root.resolve()),
            str(target),
        )
        check(
            "содержимое — тот же workflow", target.read_text(encoding="utf-8") == text
        )

        expect_error(
            "запись за пределы workspace отклонена",
            lambda: client.render_action_yaml(
                output_path=str(root.parent / "outside.yml"), root=root
            ),
            "OUTPUT_PATH_OUTSIDE_WORKSPACE",
        )
        check("файл снаружи не создан", not (root.parent / "outside.yml").exists())
        expect_error(
            "запись в абсолютный путь вне root отклонена",
            lambda: client.render_action_yaml(
                output_path=str(Path(tempfile.gettempdir()) / "x.yml"),
                root=root / "sub",
            ),
            "OUTPUT_PATH_OUTSIDE_WORKSPACE",
        )


def test_jupyter_detects_and_never_executes():
    assistant = I.JupyterAssistant()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        canary = root / CANARY
        document = notebook(
            [
                code_cell(
                    "# подсчёт\nvalue = 1\n",
                    outputs=[
                        {"output_type": "stream", "name": "stdout", "text": "1\n"}
                    ],
                ),
                code_cell(
                    CANARY_SOURCE,
                    outputs=[
                        {
                            "output_type": "error",
                            "ename": "NameError",
                            "evalue": "name 'value' is not defined",
                            "traceback": ["NameError: name 'value' is not defined"],
                        }
                    ],
                ),
                code_cell("print('без ошибки')", outputs=[]),
            ]
        )
        path = write_notebook(root / "lesson.ipynb", document)

        report = assistant.analyze_notebook(path)
        check("три ячейки описаны", len(report) == 3, str(len(report)))
        check(
            "типы ячеек сохранены",
            [c["cell_type"] for c in report] == ["code", "code", "code"],
            str([c["cell_type"] for c in report]),
        )
        check(
            "источник ячейки прочитан",
            report[1]["source"].startswith("__import__('pathlib')"),
            report[1]["source"][:60],
        )
        check("NameError обнаружен", report[1]["has_error"] is True)
        check(
            "имя ошибки названо",
            report[1]["error_kind"] == "NameError",
            report[1]["error_kind"],
        )
        check(
            "даётся русский совет",
            "Имя не определено" in report[1]["advice_ru"],
            report[1]["advice_ru"],
        )
        check(
            "совет назвал проверку порядка ячеек",
            "порядок ячеек" in report[1]["advice_ru"],
            report[1]["advice_ru"],
        )
        check(
            "безошибочная ячейка без advice",
            report[2]["has_error"] is False and report[2]["advice_ru"] == "",
            str(report[2]),
        )

        # The load-bearing negative: nothing was executed.
        check("ячейка-канарейка НЕ выполнена", not canary.exists(), str(canary))
        check("файл не появился в temp root", not (root / CANARY).exists())

        unknown = notebook(
            [
                code_cell(
                    "x = ",
                    outputs=[
                        {
                            "output_type": "error",
                            "ename": "WeirdCustomError",
                            "evalue": "nope",
                            "traceback": [],
                        }
                    ],
                )
            ]
        )
        unknown_path = write_notebook(root / "unknown.ipynb", unknown)
        unknown_report = assistant.analyze_notebook(unknown_path)
        check(
            "неизвестная ошибка тоже ловится",
            unknown_report[0]["error_kind"] == "WeirdCustomError",
            str(unknown_report[0]),
        )
        check(
            "общий совет эхо-ит имя ошибки",
            "WeirdCustomError" in unknown_report[0]["advice_ru"],
            unknown_report[0]["advice_ru"],
        )
        check("канарейка по-прежнему не создана", not canary.exists())

        summary = assistant.notebook_summary(path)
        check(
            "summary считает ячейки",
            summary["total_cells"] == 3,
            str(summary["total_cells"]),
        )
        check(
            "summary считает код",
            summary["code_cells"] == 3,
            str(summary["code_cells"]),
        )
        check(
            "summary считает ошибки",
            summary["error_cells"] == 1,
            str(summary["error_cells"]),
        )
        check(
            "первая ошибка названа",
            summary["first_error"]["error_kind"] == "NameError",
            str(summary["first_error"]),
        )
        check(
            "summary по-русски",
            "В ноутбуке" in summary["summary_ru"],
            summary["summary_ru"],
        )

        expect_error(
            "несуществующий ноутбук отклонён",
            lambda: assistant.analyze_notebook(root / "nope.ipynb"),
            "NOTEBOOK_NOT_FOUND",
        )
        (root / "broken.ipynb").write_text("{ не json", encoding="utf-8")
        expect_error(
            "битый ноутбук отклонён",
            lambda: assistant.analyze_notebook(root / "broken.ipynb"),
            "NOTEBOOK_UNREADABLE",
        )
        (root / "list.ipynb").write_text("[1,2,3]", encoding="utf-8")
        expect_error(
            "ноутбук-массив отклонён",
            lambda: assistant.analyze_notebook(root / "list.ipynb"),
            "NOTEBOOK_NOT_OBJECT",
        )
        (root / "nocells.ipynb").write_text(
            json.dumps({"cells": "нет"}), encoding="utf-8"
        )
        expect_error(
            "ноутбук без cells отклонён",
            lambda: assistant.analyze_notebook(root / "nocells.ipynb"),
            "NOTEBOOK_NO_CELLS",
        )

        # And nothing appeared anywhere under the temp root.
        stray = [p.name for p in root.rglob(CANARY)]
        check("канарейка не появилась нигде под temp root", stray == [], str(stray))


def test_suggest_cell_fix_respects_the_ceiling():
    assistant = I.JupyterAssistant()
    cell = {
        "cell_type": "code",
        "source": "value = ",
        "outputs": [
            {
                "output_type": "error",
                "ename": "NameError",
                "evalue": "x",
                "traceback": [],
            }
        ],
        "error_kind": "NameError",
    }
    expect_error(
        "graded: готовое решение отклонено",
        lambda: assistant.suggest_cell_fix(cell, assessment="graded"),
        "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
    )
    expect_error(
        "unknown: готовое решение отклонено",
        lambda: assistant.suggest_cell_fix(cell, assessment="unknown"),
        "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
    )

    practice = assistant.suggest_cell_fix(cell, assessment="practice")
    check("practice: совет выдан", bool(practice["suggestion_ru"]), str(practice))
    check(
        "practice: имя ошибки сохранено",
        practice["error_kind"] == "NameError",
        str(practice),
    )
    check(
        "practice: потолок назван",
        practice["ceiling"] == "SOLUTION",
        str(practice["ceiling"]),
    )

    # Via a notebook path: an errored cell on a graded task is refused.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = write_notebook(
            root / "nb.ipynb",
            notebook(
                [
                    code_cell(
                        CANARY_SOURCE,
                        outputs=[
                            {
                                "output_type": "error",
                                "ename": "NameError",
                                "evalue": "x",
                                "traceback": [],
                            }
                        ],
                    )
                ]
            ),
        )
        # On the notebook path the advice comes from the module's FIXED
        # exception→advice table, so it is a hint at most; the refusal is
        # reserved for a caller-supplied cell object, where the harness cannot
        # know what a "suggestion" contains. What must hold here is that the
        # ceiling is reported and no cell is rewritten.
        graded_from_path = assistant.suggest_cell_fix(str(path), assessment="graded")
        check(
            "по пути ошибка названа",
            graded_from_path["error_kind"] == "NameError",
            str(graded_from_path),
        )
        check(
            "потолок graded назван",
            graded_from_path["ceiling"] == "EXAMPLE",
            str(graded_from_path["ceiling"]),
        )
        check(
            "ячейка не переписана",
            "source" not in graded_from_path,
            str(sorted(graded_from_path)),
        )
        # On the notebook path the advice comes from the module's FIXED
        # exception→advice table, so it is a hint at most; the refusal is
        # reserved for a caller-supplied cell object, where the harness cannot
        # know what a "suggestion" contains. What must hold here is that the
        # ceiling is reported and no cell is rewritten.
        graded_from_path = assistant.suggest_cell_fix(str(path), assessment="graded")
        check(
            "по пути ошибка названа",
            graded_from_path["error_kind"] == "NameError",
            str(graded_from_path),
        )
        check(
            "потолок graded назван",
            graded_from_path["ceiling"] == "EXAMPLE",
            str(graded_from_path["ceiling"]),
        )
        check(
            "ячейка не переписана",
            "source" not in graded_from_path,
            str(sorted(graded_from_path)),
        )
        check("канарейка не создана", not (root / CANARY).exists())
        practice_from_path = assistant.suggest_cell_fix(
            str(path), assessment="practice"
        )
        check(
            "practice по пути даёт ту же ошибку",
            practice_from_path["error_kind"] == "NameError",
            str(practice_from_path),
        )

        clean_path = write_notebook(
            root / "clean.ipynb", notebook([code_cell("print(1)", outputs=[])])
        )
        clean = assistant.suggest_cell_fix(str(clean_path), assessment="graded")
        check(
            "без ошибок — без решения и без отказа",
            "записанных ошибок нет" in clean["suggestion_ru"],
            str(clean),
        )

    cell_dict = {
        "cell_type": "code",
        "source": "x",
        "error_kind": "SyntaxError",
    }
    # Any caller-supplied cell object carrying an error_kind on a graded task
    # is refused — the module cannot know whether the object's "suggestion"
    # would be a full solution, so it declines.
    expect_error(
        "graded-словарь с ошибкой отклонён",
        lambda: assistant.suggest_cell_fix(cell_dict, assessment="graded"),
        "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
    )
    graded_ok = assistant.suggest_cell_fix(
        {"cell_type": "code", "source": "x"}, assessment="graded"
    )
    check(
        "graded-словарь без ошибки допускается",
        bool(graded_ok["suggestion_ru"]),
        str(graded_ok),
    )
    check(
        "и назван потолок EXAMPLE",
        graded_ok["ceiling"] == "EXAMPLE",
        str(graded_ok["ceiling"]),
    )
    expect_error(
        "не-объект ячейки отклонён",
        lambda: assistant.suggest_cell_fix(42, assessment="practice"),
        "CELL_NOT_OBJECT",
    )


def test_lms_manifest_and_export():
    connector = I.LmsConnector()
    parsed = connector.parse_ims_manifest(IMS_MANIFEST)
    check("title разобран", parsed["title"] == "Курс про Git", str(parsed["title"]))
    check("два элемента", len(parsed["items"]) == 2, str(len(parsed["items"])))
    check(
        "id первого", parsed["items"][0]["raw_id"] == "item-a1", str(parsed["items"][0])
    )
    check(
        "цель первого",
        parsed["items"][0]["objective_ids"] == ["explain-diff"],
        str(parsed["items"][0]),
    )
    check(
        "название второго",
        parsed["items"][1]["title"] == "Перенос файлов в индекс",
        str(parsed["items"][1]),
    )

    expect_error(
        "битый XML отклонён",
        lambda: connector.parse_ims_manifest("<manifest><oops>"),
        "MANIFEST_MALFORMED",
    )
    expect_error(
        "пустой манифест отклонён",
        lambda: connector.parse_ims_manifest(""),
        "MANIFEST_MALFORMED",
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        imported = connector.import_assignments(root, "minimal-diff", IMS_MANIFEST)
        check("course_id сохранён", imported["course_id"] == "minimal-diff")
        check(
            "название манифеста сохранено",
            imported["manifest_title"] == "Курс про Git",
            imported["manifest_title"],
        )
        check(
            "два задания импортировано",
            len(imported["assignments"]) == 2,
            str(imported["assignments"]),
        )
        check(
            "assignment_id нормализован через safe_name",
            imported["assignments"][0]["assignment_id"] == "item-a1",
            str(imported["assignments"][0]),
        )
        check(
            "цели перенесены",
            imported["assignments"][0]["objective_ids"] == ["explain-diff"],
            str(imported["assignments"][0]),
        )

        # A manifest whose item id is a traversal: the id must not escape.
        weird = IMS_MANIFEST.replace('identifier="item-a1"', 'identifier="../evil"')
        weird_import = connector.import_assignments(root, "minimal-diff", weird)
        ids = [a["assignment_id"] for a in weird_import["assignments"]]
        check("traversal-имя не сохранено дословно", "../evil" not in ids, str(ids))
        check(
            "вместо него — placeholder",
            any(i.startswith("item-") for i in ids),
            str(ids),
        )
        check("вне temp root ничего не создано", not (root.parent / "evil").exists())
        check("внутри root нет evil", not (root / "evil").exists())

        progress = {
            "objective_states": {
                "explain-diff": {"stage": "demonstrated"},
                "apply-staging": {"stage": "practising"},
            }
        }
        csv_path = connector.export_grades(
            root, "minimal-diff", "11111111-1111-4111-8111-111111111111", progress
        )
        check(
            "CSV создан внутри temp root",
            csv_path.resolve().is_relative_to(root.resolve()),
            str(csv_path),
        )
        check(
            "лежит в .botai/exports",
            ".botai" in csv_path.parts and "exports" in csv_path.parts,
            str(csv_path),
        )
        lines = csv_path.read_text(encoding="utf-8").splitlines()
        check(
            "заголовок ровно student_id,objective_id,stage",
            lines[0] == "student_id,objective_id,stage",
            repr(lines[0]),
        )
        check(
            "столбца оценки нет",
            not any(
                "grade" in h or "mark" in h or "score" in h for h in lines[0].split(",")
            ),
            repr(lines[0]),
        )
        check("две строки данных", len(lines) == 3, str(lines))
        check(
            "этапы записаны, строки отсортированы по objective_id",
            lines[1].endswith("apply-staging,practising")
            and lines[2].endswith("explain-diff,demonstrated"),
            str(lines),
        )
        check(
            "id ученика нормализован",
            all(
                line.startswith("11111111-1111-4111-8111-111111111111,")
                for line in lines[1:]
            ),
            str(lines),
        )

        # `export_grades` validates the learner id through `safe_name` before
        # joining it into a filename; the raw PathError is the refusal.
        try:
            connector.export_grades(root, "minimal-diff", "../evil", progress)
        except (I.IntegrationError, ValueError) as e:
            code = getattr(e, "code", type(e).__name__)
            check(
                "traversal-имя ученика отклонено",
                code in ("SLUG_SEPARATOR", "PathError"),
                code,
            )
        else:
            check("traversal-имя ученика отклонено", False, "экспорт прошёл")
        check("файл outside не создан", not (root.parent / "evil-grades.csv").exists())

        empty = connector.export_grades(
            root,
            "minimal-diff",
            "22222222-2222-4222-8222-222222222222",
            {"objective_states": {}},
        )
        empty_lines = empty.read_text(encoding="utf-8").splitlines()
        check(
            "пустой прогресс даёт только заголовок",
            len(empty_lines) == 1,
            str(empty_lines),
        )
        check(
            "заголовок тот же",
            empty_lines[0] == "student_id,objective_id,stage",
            repr(empty_lines[0]),
        )


def test_lti_and_sso_refused():
    connector = I.LmsConnector()
    expect_error(
        "LTI отклонён", lambda: connector.lti_not_supported(), "LTI_REQUIRES_EXTERNAL"
    )
    expect_error(
        "SSO отклонён", lambda: connector.sso_not_supported(), "SSO_REQUIRES_EXTERNAL"
    )
    try:
        connector.lti_not_supported()
    except I.IntegrationError as e:
        check(
            "LTI-сообщение назвало внешний эндпоинт",
            "LMS" in e.message or "эндпоинт" in e.message,
            e.message[:120],
        )
    try:
        connector.sso_not_supported()
    except I.IntegrationError as e:
        check(
            "SSO-сообщение назвало внешнего провайдера",
            "провайдер" in e.message or "идентичности" in e.message,
            e.message[:120],
        )


def main():
    tests = [
        test_network_is_refused,
        test_parse_assignment_ref,
        test_build_review_report_claims_no_grade,
        test_render_action_yaml,
        test_jupyter_detects_and_never_executes,
        test_suggest_cell_fix_respects_the_ceiling,
        test_lms_manifest_and_export,
        test_lti_and_sso_refused,
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
