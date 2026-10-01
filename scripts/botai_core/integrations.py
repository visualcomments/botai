# -*- coding: utf-8 -*-
"""External-platform adapters, **offline and declarative only** (design §5).

This module holds the GitHub Classroom, Jupyter and LMS adapters. Every one of
them is implemented in its *local* half only. None of them opens a socket, and
each refuses its remote half with a stable `...REQUIRES_EXTERNAL` /
`...UNSUPPORTED` error rather than pretending.

**Implemented offline, and what it is:**

* `GitHubClassroomClient.parse_assignment_ref` — parses a GitHub Classroom URL
  into `{owner, repo, assignment}`; refuses a non-GitHub URL.
* `GitHubClassroomClient.build_review_report` — a Markdown PR-comment body
  scored as a *test-pass ratio with the denominator stated*, plus a checklist.
  It does **not** claim a grade: a grade is a course judgment, from the
  accepted contract.
* `GitHubClassroomClient.render_action_yaml` — the text of
  `.github/workflows/botai-reviewer.yml`, returned (or written to a path the
  caller supplies, only inside the workspace). It references
  `${{ secrets.GITHUB_TOKEN }}`; it invents no secret and no token.
* `JupyterAssistant.analyze_notebook` — parses a `.ipynb` (stdlib `json`) and
  reports each cell, its error, and a Russian explanation from a **fixed**
  exception→advice table. It never executes a cell.
* `JupyterAssistant.suggest_cell_fix` — a Russian *suggestion*; refuses a
  rewrite when the assignment is graded/unknown, reusing
  `tutoring.ASSESSMENT_CEILING` rather than duplicating the ceiling logic.
* `JupyterAssistant.notebook_summary` — counts, first error, a Russian
  one-paragraph summary.
* `LmsConnector.parse_ims_manifest` / `import_assignments` — an IMS
  Common Cartridge / QTI-ish manifest parsed with `xml.etree.ElementTree`
  (stdlib, no `lxml`; ElementTree does not resolve external entities by
  default), normalized to `[{assignment_id, title, objective_ids}]`.
* `LmsConnector.export_grades` — a CSV of `student_id, objective_id, stage`.
  It writes the **achievement stage only**, never a numeric grade: mapping a
  stage to a grade is the LMS's job, and inventing one here would fabricate a
  mark the harness has no authority to give.

**Deliberately refused, and why:**

* Remote GitHub/GitHub Classroom calls (API, clone, PR comments) — needs a
  network client the harness does not have. `allow_network=True` raises.
* Auto-grade calculation — a grade is a human/course judgment; the harness
  reports a ratio and lets the course decide.
* Notebook cell **execution** and full notebook rewrites — the harness never
  runs learner code, and for a graded assignment a rewrite *is* the solution.
* LTI 1.3 and SSO — both need a real LMS endpoint and registered credentials;
  out of scope for an offline harness. `lti_not_supported()` /
  `sso_not_supported()` say so in Russian rather than shipping a fake.
* Embedding a botai widget into an LMS — same reason: a hosted tool needs a
  registration and a running service.

Every refusal is a refusal *of the premise*, not a stub: no method here returns
"success" for work it did not do.
"""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.parse import urlparse

from . import paths, tutoring

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Jupyter: exception name -> short Russian explanation
# ---------------------------------------------------------------------------
#
# A fixed table, not a model. The point is a *reliable, consistent* short hint
# for the most common notebook errors; anything not in the table gets a generic
# advice that echoes the exception name rather than a guess.
NOTEBOOK_ERROR_ADVICE = {
    "NameError": (
        "Имя не определено: переменная или функция используется до "
        "того, как объявлена. Проверьте написание и порядок ячеек."
    ),
    "TypeError": (
        "Тип не тот, который ожидала операция: например, строка вместо "
        "числа или число вместо списка. Посмотрите на тип значения в "
        "этой точке."
    ),
    "KeyError": (
        "Ключа нет в словаре. Проверьте, как он написан, и есть ли он "
        "в словаре до обращения."
    ),
    "IndexError": (
        "Индекс выходит за границы: элемента с таким номером нет. "
        "Проверьте длину и нумерацию (с нуля)."
    ),
    "ValueError": (
        "Значение не подходит по смыслу: часто это попытка разобрать "
        "строку как число или число как дату. Посмотрите на входные "
        "данные."
    ),
    "AttributeError": (
        "У объекта нет такого атрибута или метода. Проверьте, что "
        "объект того типа, который вы думаете."
    ),
    "ImportError": (
        "Модуль не импортируется: его нет в окружении или он "
        "называется иначе. Проверьте окружение и имя модуля."
    ),
    "ZeroDivisionError": "Деление на ноль: проверьте знаменатель перед делением.",
    "SyntaxError": (
        "Синтаксическая ошибка в ячейке: чаще всего незакрытая скобка, "
        "кавычка или двоеточие в конце строки цикла/функции."
    ),
}

_GENERIC_ADVICE = (
    "Ошибка %s. Прочитайте последнюю строку трассировки: в ней "
    "указаны имя ошибки и номер строки."
)


class IntegrationError(RuntimeError):
    """A refused integration operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# GitHub Classroom (offline half)
# ---------------------------------------------------------------------------

# Matches `/classroom/<owner>/<assignment>/assignment-<repo>` — GitHub
# Classroom's canonical assignment URL shape. The `classroom/` prefix is
# optional only in that the match runs against the path with it, so the
# pattern is anchored to the whole path.
_ASSIGNMENT_REF_RE = re.compile(
    r"^/?(?:classroom/)?(?P<owner>[^/]+)/(?P<assignment>[^/]+)"
    r"/assignment-(?P<repo>.+)$"
)


class GitHubClassroomClient:
    """Offline GitHub Classroom adapter. No network, ever.

    Construction with `allow_network=True` raises immediately: this harness has
    no HTTP client to enable, and a client that "maybe" has one is a client
    someone will use. The offline capabilities are real: URL parsing, review
    report text, the Actions workflow text.
    """

    def __init__(self, *, token=None, allow_network=False, clock=None):
        if allow_network:
            raise IntegrationError(
                "NETWORK_NOT_PERMITTED",
                "сетевые вызовы запрещены: харнесс работает офлайн и не "
                "открывает сокеты. GitHub Classroom читается локально "
                "(файл задания, текст diff), а не через API.",
            )
        self._token = token
        self._clock = clock

    def remote_calls_disabled(self):
        """Always True, with the reason. For `doctor` to report honestly."""
        return {
            "remote_calls": False,
            "token_configured": self._token is not None,
            "note_ru": (
                "Обращения к GitHub API, клонирование и отправка PR "
                "комментариев отключены: сеть недоступна по дизайну. "
                "Адаптер работает с локальными файлами."
            ),
        }

    def parse_assignment_ref(self, url):
        """`{owner, repo, assignment}` from a GitHub Classroom URL.

        Refuses a non-GitHub / non-Classroom URL with a Russian message. It
        parses only — it never fetches the URL.
        """
        raw = str(url or "").strip()
        if not raw:
            raise IntegrationError(
                "ASSIGNMENT_URL_EMPTY", "ссылка на задание не задана"
            )
        parsed = urlparse(raw if "//" in raw else "https://%s" % raw)
        host = (parsed.netloc or "").lower()
        if "github.com" not in host:
            raise IntegrationError(
                "NOT_A_GITHUB_URL",
                "ссылка не указывает на GitHub (хост %r): разбираются только "
                "ссылки вида https://classroom.github.com/a/<assignment>/assignment-<repo>"
                % host,
            )
        path = (parsed.path or "").rstrip("/")
        match = _ASSIGNMENT_REF_RE.match(path)
        if not match:
            raise IntegrationError(
                "ASSIGNMENT_REF_UNRECOGNIZED",
                "не удалось разобрать ссылку GitHub Classroom %r: ожидается "
                "вид classroom/<владелец>/<задание>/assignment-<репозиторий>" % path,
            )
        return {
            "owner": match.group("owner"),
            "repo": match.group("repo"),
            "assignment": match.group("assignment"),
        }

    def build_review_report(self, *, diff_text, tests_passed, tests_total, notes):
        """A Markdown PR-comment body. No grade is claimed.

        The score is a *test-pass ratio with the denominator stated* (e.g.
        "3 из 5"), never a letter or a number out of a scale. A grade is a
        course judgment from the accepted contract; a comment body that
        asserted one would be the harness inventing a mark.
        """
        try:
            passed = int(tests_passed)
            total = int(tests_total)
        except (TypeError, ValueError):
            raise IntegrationError(
                "TESTS_NOT_NUMERIC",
                "число пройденных и общее число тестов должны быть целыми",
            )
        if total < 0 or passed < 0:
            raise IntegrationError(
                "TESTS_NEGATIVE",
                "число тестов не может быть отрицательным",
            )
        passed = min(passed, total)
        ratio = (passed / total) if total else 0.0
        percent = int(round(ratio * 100))

        diff = diff_text or ""
        diff_lines = [line for line in str(diff).splitlines() if line.strip()][:200]
        notes_text = str(notes or "").strip() or "—"

        return (
            "\n".join(
                [
                    "## Проверка задания (ботai)",
                    "",
                    "### Тесты",
                    "",
                    "Пройдено **%d из %d** (%d%%)." % (passed, total, percent),
                    "Знаменатель указан явно (%d): это доля пройденных тестов, а не "
                    "оценка." % total,
                    "",
                    "### Чек-лист",
                    "",
                    "- [ ] Изменения осмысленны и относятся к заданию",
                    "- [ ] Есть тест, который падал бы без изменения",
                    "- [ ] Нет отладочного вывода и закомментированного кода",
                    "- [ ] Имена понятны без комментария",
                    "",
                    "### Заметки",
                    "",
                    notes_text,
                    "",
                    "### Размер изменения",
                    "",
                    "Строк в diff (непустых): %d." % len(diff_lines),
                    "",
                    "> Оценка не выставляется: это решение преподавателя по принятому "
                    "контракту курса, а не расчёт бота.",
                ]
            )
            + "\n"
        )

    def render_action_yaml(self, *, output_path=None, root=None):
        """The Actions workflow text. Returned, or written to `output_path`.

        No secret is invented: the workflow references
        `${{ secrets.GITHUB_TOKEN }}`. If `output_path` is given it must be
        inside the workspace (`paths.ensure_within`); otherwise nothing is
        written at all.
        """
        yaml_text = (
            "\n".join(
                [
                    "name: botai-reviewer",
                    "on:",
                    "  pull_request:",
                    "    types: [opened, synchronize, reopened]",
                    "permissions:",
                    "  contents: read",
                    "  pull-requests: write",
                    "jobs:",
                    "  review:",
                    "    runs-on: ubuntu-latest",
                    "    steps:",
                    "      - uses: actions/checkout@v4",
                    "      - name: Run tests",
                    "        env:",
                    "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
                    "        run: |",
                    "          python -m pytest -q",
                    "      - name: Build review comment",
                    "        env:",
                    "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
                    "        run: |",
                    '          echo "Add the botai review comment via the GitHub API;',
                    '               the harness itself does not perform this call."',
                ]
            )
            + "\n"
        )

        if output_path is None:
            return yaml_text
        base = Path(root or ".").resolve()
        try:
            target = paths.ensure_within(base, Path(output_path).resolve())
        except paths.PathError as e:
            raise IntegrationError(
                "OUTPUT_PATH_OUTSIDE_WORKSPACE",
                "путь для workflow вне рабочего пространства: %s" % e,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(yaml_text, encoding="utf-8", newline="\n")
        except OSError as e:
            raise IntegrationError(
                "WORKFLOW_WRITE_FAILED",
                "не удалось записать workflow %s: %s" % (target, e),
            )
        return target


# ---------------------------------------------------------------------------
# Jupyter (offline half)
# ---------------------------------------------------------------------------


class JupyterAssistant:
    """Reads a `.ipynb` as JSON. Never executes a cell.

    Execution is the part of Jupyter assistance that actually changes what a
    learner believes (a cell that ran is evidence; a cell we parsed is not), so
    this adapter does it only from the *recorded* outputs already in the file.
    """

    def _read_notebook(self, path):
        target = Path(path)
        if not target.is_file():
            raise IntegrationError(
                "NOTEBOOK_NOT_FOUND", "файл ноутбука не найден: %s" % target
            )
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise IntegrationError(
                "NOTEBOOK_UNREADABLE",
                "не удалось прочитать ноутбук %s: %s" % (target, e),
            )
        if not isinstance(data, dict):
            raise IntegrationError(
                "NOTEBOOK_NOT_OBJECT", "ноутбук должен быть JSON-объектом"
            )
        return data

    def analyze_notebook(self, path):
        """Per-cell `{cell_index, cell_type, source, has_error, error_kind,
        advice_ru}`.

        `has_error` is read from the cell's recorded `outputs` (`output_type ==
        "error"` / `ename`), never produced by running anything. Advice comes
        from the fixed table; an unknown exception gets a generic advice that
        echoes the name.
        """
        data = self._read_notebook(path)
        cells = data.get("cells")
        if not isinstance(cells, list):
            raise IntegrationError(
                "NOTEBOOK_NO_CELLS", "в ноутбуке нет списка ячеек (cells)"
            )
        report = []
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                continue
            cell_type = str(cell.get("cell_type") or "raw")
            source = cell.get("source")
            if isinstance(source, list):
                source_text = "".join(str(s) for s in source)
            else:
                source_text = str(source or "")

            error_kind = None
            has_error = False
            for output in cell.get("outputs") or []:
                if not isinstance(output, dict):
                    continue
                if output.get("output_type") == "error" or output.get("ename"):
                    has_error = True
                    error_kind = str(output.get("ename") or "Error")
                    break

            if has_error:
                advice = NOTEBOOK_ERROR_ADVICE.get(
                    error_kind or "", _GENERIC_ADVICE % error_kind
                )
            else:
                advice = ""

            report.append(
                {
                    "cell_index": index,
                    "cell_type": cell_type,
                    "source": source_text,
                    "has_error": has_error,
                    "error_kind": error_kind,
                    "advice_ru": advice,
                }
            )
        return report

    def suggest_cell_fix(self, cell, *, assessment=None):
        """A Russian *suggestion* for one cell. Never a rewritten notebook.

        For a `graded` or `unknown` assignment a suggestion that constitutes the
        full solution is refused with
        `SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT` — reusing
        `tutoring.ASSESSMENT_CEILING` (the same table the policy check uses)
        rather than duplicating the ceiling logic here. For `practice`, a
        full-solution-level suggestion is allowed, per that table.
        """
        ceiling = tutoring.ASSESSMENT_CEILING.get(assessment or "unknown", "EXAMPLE")
        report = self.analyze_notebook(cell) if isinstance(cell, (str, Path)) else None
        if report is not None:
            errored = [c for c in report if c["has_error"]]
            if not errored:
                return {
                    "suggestion_ru": "В этой ячейке записанных ошибок нет: "
                    "возможно, проблема в другом месте.",
                    "assessment": assessment or "unknown",
                    "ceiling": ceiling,
                }
            first = errored[0]
            return {
                "suggestion_ru": first["advice_ru"],
                "error_kind": first["error_kind"],
                "cell_index": first["cell_index"],
                "assessment": assessment or "unknown",
                "ceiling": ceiling,
            }

        if not isinstance(cell, dict):
            raise IntegrationError(
                "CELL_NOT_OBJECT", "ячейка должна быть объектом (или путём к ноутбуку)"
            )
        error_kind = cell.get("error_kind")
        if assessment in ("graded", "unknown") and str(error_kind or "").strip():
            # A suggestion for a graded cell is fine up to EXAMPLE; anything
            # that is a full fix is not. We do not know the length of the
            # student's remaining work, so we cap at the ceiling explicitly
            # rather than guessing.
            raise IntegrationError(
                "SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT",
                "задание оцениваемое (%s): подсказка не может быть полным "
                "решением (предел %s по tutoring.ASSESSMENT_CEILING). Для "
                "graded/unknown харнесс даёт направление, а не готовый код."
                % (assessment, ceiling),
            )
        advice = NOTEBOOK_ERROR_ADVICE.get(str(error_kind), "") or _GENERIC_ADVICE % (
            error_kind or "ошибка"
        )
        return {
            "suggestion_ru": advice,
            "error_kind": error_kind,
            "assessment": assessment or "unknown",
            "ceiling": ceiling,
        }

    def notebook_summary(self, path):
        """Counts + the first recorded error + a Russian one-paragraph summary."""
        report = self.analyze_notebook(path)
        code_cells = [c for c in report if c["cell_type"] == "code"]
        errored = [c for c in report if c["has_error"]]
        first_error = errored[0] if errored else None
        summary_ru = (
            "В ноутбуке %d ячеек, из них %d с кодом; ошибок записано: %d. "
            "Первая ошибка — %s (ячейка %s)."
            % (
                len(report),
                len(code_cells),
                len(errored),
                first_error["error_kind"] if first_error else "нет",
                first_error["cell_index"] if first_error else "—",
            )
        )
        if not errored:
            summary_ru += " По записанным выводам ноутбук отработал без ошибок."
        return {
            "total_cells": len(report),
            "code_cells": len(code_cells),
            "error_cells": len(errored),
            "first_error": first_error,
            "summary_ru": summary_ru,
        }


# ---------------------------------------------------------------------------
# LMS (offline half)
# ---------------------------------------------------------------------------


class LmsConnector:
    """Offline IMS manifest import + stage-only grade export.

    No network, no LTI, no SSO. The only XML parser used is the stdlib
    `xml.etree.ElementTree`, which does not resolve external entities by
    default (no `lxml`, whose entity handling is configurable) — a note worth
    keeping explicit, because parsing an untrusted manifest is exactly where a
    XXE would go.
    """

    def parse_ims_manifest(self, xml_text):
        """Parse an IMS Common Cartridge / QTI-ish manifest into resources.

        Returns a dict with `title` and `items` — a normalized, structural view
        of the manifest. Raises `IntegrationError` on malformed XML rather
        than returning a partial parse.
        """
        try:
            # Safe stdlib parsing; ElementTree does not fetch external entities.
            root = ElementTree.fromstring(str(xml_text or ""))
        except ElementTree.ParseError as e:
            raise IntegrationError("MANIFEST_MALFORMED", "манифест не разобран: %s" % e)

        title = ""
        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag in ("title", "shorthand") and elem.text:
                title = elem.text.strip()
                break

        items = []
        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag != "item":
                continue
            item_id = elem.attrib.get("identifier") or elem.attrib.get("id")
            item_title = ""
            objective_ids = []
            for child in elem.iter():
                child_tag = child.tag.rsplit("}", 1)[-1]
                if child_tag in ("title", "shortDescription") and child.text:
                    if not item_title:
                        item_title = child.text.strip()
                elif child_tag in (
                    "learningObjectiveId",
                    "objectiveId",
                    "learning_objective_id",
                ):
                    if child.text:
                        objective_ids.append(child.text.strip())
            items.append(
                {
                    "raw_id": item_id or "",
                    "title": item_title or (item_id or ""),
                    "objective_ids": objective_ids,
                }
            )
        return {"title": title, "items": items}

    def import_assignments(self, root, course_id, xml_text):
        """Normalized `[{assignment_id, title, objective_ids}]` from a manifest.

        `assignment_id` goes through `paths.safe_name`, so a manifest cannot
        smuggle a path into the workspace. An item whose id cannot be a safe
        name is skipped with the reason, not normalised.
        """
        parsed = self.parse_ims_manifest(xml_text)
        assignments = []
        for index, item in enumerate(parsed["items"], start=1):
            raw_id = item["raw_id"] or "item-%d" % index
            try:
                safe_id = str(paths.safe_name(raw_id, kind="идентификатор задания"))
            except paths.PathError:
                # Not fatal: a differently-shaped id is recorded as a
                # placeholder, since we cannot invent a stable id for it.
                safe_id = "item-%d" % index
            assignments.append(
                {
                    "assignment_id": safe_id,
                    "title": item["title"],
                    "objective_ids": list(item["objective_ids"]),
                }
            )
        return {
            "course_id": str(course_id),
            "manifest_title": parsed["title"],
            "assignments": assignments,
        }

    def export_grades(
        self, root, course_id, learner_id, progress_document, *, out_dir=None
    ):
        """Write a `student_id, objective_id, stage` CSV and return its path.

        **Only the achievement stage is written** — never a numeric or letter
        grade. Mapping a stage ("new"/"learning"/"practising"/"demonstrated")
        to a grade is the LMS's job, from the course's own policy; inventing a
        number here would be the harness fabricating a mark it has no authority
        to give.
        """
        safe_course = str(paths.safe_name(course_id, kind="идентификатор курса"))
        safe_learner = str(
            paths.safe_name(learner_id, kind="идентификатор обучающегося")
        )
        document = progress_document or {}
        objective_states = document.get("objective_states") or {}

        if out_dir is None:
            base = Path(root) / ".botai" / "exports"
        else:
            base = Path(out_dir)
        try:
            directory = paths.ensure_within(base, base / safe_course)
        except paths.PathError as e:
            raise IntegrationError(
                "EXPORT_PATH_UNSAFE", "путь экспорта небезопасен: %s" % e
            )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("%s-grades.csv" % safe_learner)

        rows = []
        for objective_id, state in objective_states.items():
            if not isinstance(state, dict):
                continue
            rows.append(
                {
                    "student_id": safe_learner,
                    "objective_id": str(objective_id),
                    "stage": str(state.get("stage") or "new"),
                }
            )
        rows.sort(key=lambda r: r["objective_id"])

        try:
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["student_id", "objective_id", "stage"]
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
        except OSError as e:
            raise IntegrationError(
                "EXPORT_WRITE_FAILED", "не удалось записать CSV %s: %s" % (path, e)
            )
        return path

    def lti_not_supported(self):
        """Always raises: LTI 1.3 needs a real LMS endpoint (out of scope)."""
        raise IntegrationError(
            "LTI_REQUIRES_EXTERNAL",
            "LTI 1.3 не поддерживается: для запуска нужен зарегистрированный "
            "LMS-эндпоинт, ключи и сетевое соединение. Офлайн-харнесс этого не "
            "имеет. Локальная половина (разбор манифеста, выгрузка этапов) "
            "реализована в этом модуле.",
        )

    def sso_not_supported(self):
        """Always raises: SSO needs a live identity provider (out of scope)."""
        raise IntegrationError(
            "SSO_REQUIRES_EXTERNAL",
            "SSO не поддерживается: единый вход требует внешнего провайдера "
            "идентичности и сети. Офлайн-харнесс его не реализует и не "
            "имитирует.",
        )
