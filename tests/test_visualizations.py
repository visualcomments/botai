#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for offline diagram generators: text out, no rendering.

Claims under test:

* `flowchart_from_steps`, `concept_map`, `sequence_diagram` and `dot_graph`
  each return text starting with the expected Mermaid/DOT keyword;
* a label containing `"`, `[]`, `{}` or a newline is sanitised so the output
  still parses structurally — no raw bracket family inside a node label;
* a label over 200 chars is truncated with a visible marker;
* `render_chart` raises `CHART_BACKEND_UNAVAILABLE` — no plotting backend is
  bundled — and `external_backends_available()` is all-False;
* `save_diagram` rejects a non-allowlisted extension and a traversal name, and
  a valid save lands inside `.botai/visualizations/<course>/` under the temp
  root;
* `generate_from_profile` picks a diagram kind deterministically for a given
  dominant style.

Run:
    python3 tests/test_visualizations.py
    python3 -m pytest tests/test_visualizations.py -q
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

from botai_core import learner_profiling as LP, visualizations as V  # noqa: E402

_passed = 0
_failures: list[str] = []


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
    except V.VisualizationError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


def test_generators_start_with_expected_keyword():
    flow = V.flowchart_from_steps("Алгоритм", ["первый шаг", "второй шаг"])
    check(
        "flowchart начинается с flowchart TD",
        flow.splitlines()[0].strip() == "flowchart TD",
        flow.splitlines()[0],
    )
    check("flowchart содержит узлы", 'n1["первый шаг"]' in flow, flow)
    check("flowchart содержит ребро", "n1 --> n2" in flow, flow)

    cmap = V.concept_map(
        "Карта",
        [{"id": "a", "label": "Ветка"}, {"id": "b", "label": "Коммит"}],
        [{"from": "a", "to": "b", "label": "создаёт"}],
    )
    check(
        "concept_map начинается с graph LR",
        cmap.splitlines()[0].strip() == "graph LR",
        cmap.splitlines()[0],
    )
    check("concept_map содержит узлы", 'n1["Ветка"]' in cmap, cmap)
    check("concept_map содержит подписанное ребро", "-->|создаёт|" in cmap, cmap)

    seq = V.sequence_diagram(
        "Взаимодействие",
        ["Ученик", "Харнесс"],
        [{"from": "Ученик", "to": "Харнесс", "text": "вопрос"}],
    )
    check(
        "sequence начинается с sequenceDiagram",
        seq.splitlines()[0].strip() == "sequenceDiagram",
        seq.splitlines()[0],
    )
    check(
        "sequence объявляет участников",
        "participant Ученик" in seq and "participant Харнесс" in seq,
        seq,
    )
    check("sequence содержит сообщение", "Ученик->>Харнесс: вопрос" in seq, seq)

    dot = V.dot_graph(
        "Граф", [{"id": "a", "label": "Ветка"}], [{"from": "a", "to": "a"}]
    )
    check(
        "dot начинается с digraph",
        dot.splitlines()[0].strip().startswith("digraph "),
        dot.splitlines()[0],
    )
    check("dot содержит узел", 'n1 [label="Ветка"];' in dot, dot)
    check("dot закрывается", dot.rstrip().endswith("}"), dot)

    for text, keyword in (
        (flow, "flowchart TD"),
        (cmap, "graph LR"),
        (seq, "sequenceDiagram"),
        (dot, "digraph"),
    ):
        check("выдача %s — текст" % keyword.split()[0], isinstance(text, str))


def test_labels_are_sanitised():
    hostile = 'Ветка "quoted" [скобки] {фигурные}\nвторая строка'
    flow = V.flowchart_from_steps("Заголовок", [hostile])
    node_line = [ln for ln in flow.splitlines() if ln.strip().startswith("n1")][0]
    # The emitted shape is `    n1["<label>"]` — exactly one pair of quotes,
    # and the label between them contains none of the forbidden family.
    inner = node_line.strip()
    label = inner[inner.index('"') + 1 : inner.rindex('"')]
    check('узел имеет вид n1["..."]', inner.startswith('n1["'), node_line)
    check("внутри метки нет кавычек", '"' not in label, label)
    check(
        "внутри метки нет квадратных скобок",
        "[" not in label and "]" not in label,
        label,
    )
    check(
        "внутри метки нет фигурных скобок",
        "{" not in label and "}" not in label,
        label,
    )
    check("перевод строки не разорвал узел", "\n" not in node_line, node_line)
    check(
        "структура сохранена: ровно одна пара кавычек",
        node_line.count('"') == 2,
        node_line,
    )
    check("текст метки частично читаем", "quoted" in flow and "скобки" in flow, flow)

    # The forbidden set is exactly the documented one.
    for ch in ('"', "'", "`", "[", "]", "{", "}", "(", ")", "<", ">", "\n", "\r", "\t"):
        safe, _ = V._sanitize_label("a%sb" % ch)
        check("символ %r заменён" % ch, ch not in safe, repr(safe))

    dot = V.dot_graph("Граф", [{"id": "a", "label": hostile}], [])
    dot_line = [ln for ln in dot.splitlines() if "label=" in ln][0]
    check(
        "dot-метка тоже очищена", "{" not in dot_line and "\n" not in dot_line, dot_line
    )
    check("dot-метка сохраняет одну пару кавычек", dot_line.count('"') == 2, dot_line)

    cmap = V.concept_map("Карта", [{"id": "a", "label": hostile}], [])
    cmap_node = [ln for ln in cmap.splitlines() if ln.strip().startswith("n1")][0]
    cmap_inner = cmap_node.strip()
    cmap_label = cmap_inner[cmap_inner.index('"') + 1 : cmap_inner.rindex('"')]
    check(
        "concept_map очищает метку",
        "[" not in cmap_label and "{" not in cmap_label,
        cmap_label,
    )

    seq = V.sequence_diagram(
        "Последовательность",
        ['Уч"ченик'],
        [{"from": 'Уч"ченик', "to": 'Уч"ченик', "text": "тест"}],
    )
    check("последовательность очищает имя участника", '"' not in seq, seq)

    empty, _ = V._sanitize_label("   ")
    check("пустая метка получает безопасное имя", empty == "без-имени", repr(empty))
    none_label, _ = V._sanitize_label(None)
    check("None-метка безопасна", none_label == "без-имени", repr(none_label))


def test_long_label_truncated():
    long_label = "А" * 500
    safe, truncated = V._sanitize_label(long_label)
    check("метка обрезана до предела", len(safe) <= V.MAX_LABEL_CHARS, str(len(safe)))
    check("флаг обрезки выставлен", truncated is True)
    check("видимый маркер обрезки", "обрезано" in safe, safe[-30:])

    flow = V.flowchart_from_steps("T", [long_label])
    check("узел остаётся структурно корректным", 'n1["' in flow, flow[:200])
    check("маркер виден в выдаче", "обрезано" in flow, flow[:400])

    short, not_truncated = V._sanitize_label("короткая метка")
    check(
        "короткая метка не обрезана",
        not_truncated is False and short == "короткая метка",
        repr(short),
    )


def test_chart_backend_unavailable():
    expect_error(
        "render_chart без бэкенда отклонён",
        lambda: V.render_chart("bar", {"a": 1}),
        "CHART_BACKEND_UNAVAILABLE",
    )
    expect_error(
        "render_chart отклонён для любого kind",
        lambda: V.render_chart("line", []),
        "CHART_BACKEND_UNAVAILABLE",
    )

    report = V.external_backends_available()
    for backend in V.EXTERNAL_BACKENDS:
        check(
            "бэкенд %s недоступен" % backend,
            report.get(backend) is False,
            str(report.get(backend)),
        )
    check(
        "заметка объясняет, что доступно на самом деле",
        "Mermaid" in report.get("note_ru", ""),
        report.get("note_ru", ""),
    )
    check(
        "все бэкенды False",
        all(report[name] is False for name in V.EXTERNAL_BACKENDS),
        str(report),
    )


def test_save_diagram_paths_and_extensions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = V.save_diagram(
            root, "minimal-diff", "flow", "flowchart TD\n", extension=".mmd"
        )
        check(
            "файл внутри .botai/visualizations/<course>",
            target.resolve().is_relative_to(root.resolve()),
            str(target),
        )
        check(
            "путь содержит .botai/visualizations",
            ".botai" in target.parts and "visualizations" in target.parts,
            str(target),
        )
        check(
            "каталог курса есть",
            target.parent.name == "minimal-diff",
            str(target.parent),
        )
        check(
            "содержимое записано",
            target.read_text(encoding="utf-8") == "flowchart TD\n",
        )

        expect_error(
            "неразрешённое расширение отклонено",
            lambda: V.save_diagram(root, "minimal-diff", "x", "y", extension=".exe"),
            "EXTENSION_NOT_ALLOWED",
        )
        expect_error(
            "пустое расширение отклонено",
            lambda: V.save_diagram(root, "minimal-diff", "x", "y", extension=""),
            "EXTENSION_NOT_ALLOWED",
        )
        for bad_name in ("../evil", "..", "a/b", "/abs"):
            expect_error(
                "имя %r отклонено" % bad_name,
                lambda n=bad_name: V.save_diagram(root, "minimal-diff", n, "y"),
                "PATH_OUTSIDE_VISUALIZATIONS",
            )
        for bad_course in ("../evil", "..", "a/b"):
            expect_error(
                "курс %r отклонён" % bad_course,
                lambda c=bad_course: V.save_diagram(root, c, "x", "y"),
                "PATH_OUTSIDE_VISUALIZATIONS",
            )
        check(
            "вне temp root ничего не создано", not (root.parent / "evil.mmd").exists()
        )

        for extension in V.ALLOWED_EXTENSIONS:
            saved = V.save_diagram(root, "minimal-diff", "ok", "x", extension=extension)
            check("расширение %s разрешено" % extension, saved.is_file(), str(saved))


def test_generators_refuse_empty_inputs():
    expect_error(
        "flowchart без шагов отклонён",
        lambda: V.flowchart_from_steps("T", []),
        "STEPS_EMPTY",
    )
    expect_error(
        "concept_map без узлов отклонён",
        lambda: V.concept_map("T", [], []),
        "NODES_EMPTY",
    )
    expect_error(
        "dot_graph без узлов отклонён", lambda: V.dot_graph("T", [], []), "NODES_EMPTY"
    )
    expect_error(
        "sequence без участников отклонён",
        lambda: V.sequence_diagram("T", [], []),
        "PARTICIPANTS_EMPTY",
    )
    expect_error(
        "повторяющийся узел отклонён",
        lambda: V.concept_map("T", [{"id": "a"}, {"id": "a"}], []),
        "NODE_ID_DUPLICATE",
    )
    expect_error(
        "ребро к неизвестному узлу отклонено",
        lambda: V.concept_map("T", [{"id": "a"}], [{"from": "a", "to": "zz"}]),
        "EDGE_UNKNOWN_NODE",
    )
    expect_error(
        "сообщение к неизвестному участнику отклонено",
        lambda: V.sequence_diagram("T", ["A"], [("A", "B", "тест")]),
        "MESSAGE_UNKNOWN_PARTICIPANT",
    )
    expect_error(
        "повторяющийся участник отклонён",
        lambda: V.sequence_diagram("T", ["A", "A"], []),
        "PARTICIPANT_DUPLICATE",
    )


def test_generate_from_profile_is_deterministic():
    class Course:
        course_id = "minimal-diff"

    for style in ("visual", "verbal", "kinesthetic", "auditory"):
        profile = LP.LearningProfile(
            student_id="11111111-1111-4111-8111-111111111111",
            cognitive_style=LP.CognitiveStyle(
                visual=0.9 if style == "visual" else 0.1,
                verbal=0.9 if style == "verbal" else 0.1,
                kinesthetic=0.9 if style == "kinesthetic" else 0.1,
                auditory=0.9 if style == "auditory" else 0.1,
            ),
        )
        decision = V.generate_from_profile(None, Course(), LEARNER, profile)
        again = V.generate_from_profile(None, Course(), LEARNER, profile)
        check(
            "стиль %s -> детерминированная стратегия" % style,
            decision == again,
            str(decision),
        )
        check(
            "стиль %s -> известная стратегия" % style,
            decision["strategy"] in ("concept_map", "flowchart", "sequence_diagram"),
            decision["strategy"],
        )
        check(
            "стиль %s -> доминанта названа" % style,
            decision["dominant_style"] == style,
            decision["dominant_style"],
        )
        check("рендер не выполняется", decision["render"] is False)
        check(
            "бэкенды показаны",
            all(
                decision["backends_available"][b] is False for b in V.EXTERNAL_BACKENDS
            ),
        )

    visual = LP.LearningProfile(
        student_id="11111111-1111-4111-8111-111111111111",
        cognitive_style=LP.CognitiveStyle(
            visual=0.9, verbal=0.1, kinesthetic=0.1, auditory=0.1
        ),
    )
    check(
        "visual -> concept_map",
        V.generate_from_profile(None, Course(), LEARNER, visual)["strategy"]
        == "concept_map",
    )

    kina = LP.LearningProfile(
        student_id="11111111-1111-4111-8111-111111111111",
        cognitive_style=LP.CognitiveStyle(
            visual=0.1, verbal=0.1, kinesthetic=0.9, auditory=0.1
        ),
    )
    check(
        "kinesthetic -> flowchart",
        V.generate_from_profile(None, Course(), LEARNER, kina)["strategy"]
        == "flowchart",
    )

    auditory = LP.LearningProfile(
        student_id="11111111-1111-4111-8111-111111111111",
        cognitive_style=LP.CognitiveStyle(
            visual=0.1, verbal=0.1, kinesthetic=0.1, auditory=0.9
        ),
    )
    check(
        "auditory -> sequence_diagram",
        V.generate_from_profile(None, Course(), LEARNER, auditory)["strategy"]
        == "sequence_diagram",
    )

    fallback = V.generate_from_profile(None, Course(), LEARNER, None)
    check(
        "без профиля — нейтральный concept_map", fallback["strategy"] == "concept_map"
    )
    check(
        "и сказано, что это запасной вариант, а не измерение",
        "не измерение" in fallback["reason_ru"],
        fallback["reason_ru"],
    )


LEARNER = "11111111-1111-4111-8111-111111111111"


def test_dict_profile_is_accepted():
    class Course:
        course_id = "minimal-diff"

    decision = V.generate_from_profile(
        None,
        Course(),
        LEARNER,
        {
            "cognitive_style": {
                "visual": 0.9,
                "verbal": 0.1,
                "kinesthetic": 0.1,
                "auditory": 0.1,
            }
        },
    )
    check(
        "словарный профиль даёт concept_map",
        decision["strategy"] == "concept_map",
        str(decision),
    )
    check(
        "доминанта вычислена",
        decision["dominant_style"] == "visual",
        decision["dominant_style"],
    )


def main():
    tests = [
        test_generators_start_with_expected_keyword,
        test_labels_are_sanitised,
        test_long_label_truncated,
        test_chart_backend_unavailable,
        test_save_diagram_paths_and_extensions,
        test_generators_refuse_empty_inputs,
        test_generate_from_profile_is_deterministic,
        test_dict_profile_is_accepted,
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
