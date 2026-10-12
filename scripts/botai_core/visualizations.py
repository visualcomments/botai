# -*- coding: utf-8 -*-
"""Offline diagram and visualization generators (design §7.1).

What this module produces is **text** — Mermaid source, Graphviz DOT source —
plus a decision helper that says which kind of diagram fits a learner's
dominant cognitive style. What it does **not** do is render anything:

* no Graphviz `dot` process is spawned, no Manim is invoked, no Matplotlib or
  Plotly is imported (none of them are third-party dependencies here);
* `render_chart()` refuses with `CHART_BACKEND_UNAVAILABLE` and points at the
  text generators that *are* available;
* `EXTERNAL_BACKENDS` / `external_backends_available()` exist so `doctor` can
  report the absence honestly instead of pretending a backend exists.

Mermaid is used as the default because its diagrams are plain text that any
Markdown viewer can render, which keeps the whole loop offline: the harness
emits a string, the learner's own renderer draws it.

Every label passes through `_sanitize_label`: Mermaid and DOT both break on
brackets, quotes and newlines, and a diagram that silently eats half a node
name is worse than one that says so. Labels longer than `MAX_LABEL_CHARS` are
truncated with an explicit marker.

Path safety: `save_diagram()` takes `root`, `course_id` and `name` through
`paths.safe_name`, resolves under `.botai/visualizations/<course>/`, and runs
`paths.ensure_within` on the final Path. The extension is allowlisted; anything
else is refused rather than written to a surprising place.
"""

from __future__ import annotations

from pathlib import Path

from . import paths, tutoring, learner_profiling

# Rendering backends the design names. None of them is a dependency of the
# harness; this tuple is what `doctor` reports on.
EXTERNAL_BACKENDS = ("graphviz", "matplotlib", "plotly", "manim")

# Diagram formats we emit as text and will write to disk.
ALLOWED_EXTENSIONS = (".mmd", ".dot", ".svg", ".md")

# Mermaid and DOT both choke on these inside a node/edge label. Newlines become
# spaces; the bracket family is replaced outright because no escaping is
# portable across both syntaxes.
_FORBIDDEN_LABEL_CHARS = "\"'`[]{}()<>\n\r\t"

MAX_LABEL_CHARS = 200

# Strategy decision table: dominant style -> what to draw first. `visual`
# learners get a concept map, `kinesthetic` learners a flowchart they can walk
# step by step, `auditory` learners a sequence of interactions, and `verbal`
# learners the text-first fallback (still a diagram — a labeled list — rather
# than nothing).
_STRATEGY_BY_STYLE = {
    "visual": "concept_map",
    "kinesthetic": "flowchart",
    "auditory": "sequence_diagram",
    "verbal": "concept_map",
}


class VisualizationError(RuntimeError):
    """A refused visualization operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def external_backends_available():
    """Honest availability report for `doctor`: every backend is absent.

    Returns a dict keyed by backend name. Each value is `False` — the harness
    ships none of them — and `note_ru` explains where the real capability lives
    (the Mermaid/DOT text generators in this module).
    """
    return {name: False for name in EXTERNAL_BACKENDS} | {
        "note_ru": (
            "Внешние бэкенды рендеринга (graphviz, matplotlib, plotly, manim) "
            "не установлены и не вызываются. Доступны текстовые генераторы "
            "Mermaid и DOT: диаграмма собирается как строка, рендерит её "
            "сторонний просмотрщик."
        )
    }


def render_chart(kind, data):
    """Refuse chart rendering. There is no plotting backend in the harness.

    `kind` and `data` are accepted only so the refusal names what was asked
    for; nothing is computed. The design's "data visualizations" (§7.1) are
    deliberately out of scope here — a chart that cannot be drawn must say so
    rather than draw a wrong one.
    """
    raise VisualizationError(
        "CHART_BACKEND_UNAVAILABLE",
        "бэкенд для графиков не установлен: matplotlib/plotly недоступны, "
        "и харнесс их не устанавливает. Для диаграмм используйте "
        "flowchart_from_steps / concept_map / sequence_diagram (Mermaid) или "
        "dot_graph (Graphviz DOT) — они работают офлайн и отдают текст. "
        "Запрошено: kind=%r" % (kind,),
    )


# ---------------------------------------------------------------------------
# Label sanitisation
# ---------------------------------------------------------------------------


def _sanitize_label(label):
    """Make `label` safe inside a Mermaid node/edge and a DOT node/edge.

    Returns the safe text plus a `truncated` flag. Characters that break either
    syntax are replaced with a space (not deleted silently — the label keeps a
    readable shape); a label longer than `MAX_LABEL_CHARS` is cut and marked.
    """
    text = "" if label is None else str(label)
    cleaned = []
    for ch in text:
        if ch in _FORBIDDEN_LABEL_CHARS:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    flat = " ".join("".join(cleaned).split())
    truncated = False
    if len(flat) > MAX_LABEL_CHARS:
        flat = flat[: MAX_LABEL_CHARS - 18].rstrip() + "… [обрезано]"
        truncated = True
    if not flat:
        flat = "без-имени"
    return flat, truncated


def _mermaid_id(index):
    """A stable, syntax-safe node id for Mermaid (no special characters)."""
    return "n%d" % index


def _dot_id(index):
    return "n%d" % index


# ---------------------------------------------------------------------------
# Mermaid generators (pure strings; no rendering, no external process)
# ---------------------------------------------------------------------------


def flowchart_from_steps(title, steps):
    """Mermaid `flowchart TD` text for an ordered sequence of steps.

    `steps` is a list of instruction strings (or dicts with an `instruction`
    or `title` key). The result is Mermaid *source*: this module does not run
    a renderer, and the caller is expected to hand the text to a Mermaid
    viewer (their own, offline) when a picture is wanted.
    """
    if not steps:
        raise VisualizationError(
            "STEPS_EMPTY",
            "для flowchart нужен хотя бы один шаг",
        )
    safe_title, _ = _sanitize_label(title or "Алгоритм")
    lines = ["flowchart TD", "    %% %s" % safe_title.replace("\n", " ")]
    for index, raw in enumerate(steps, start=1):
        if isinstance(raw, dict):
            text = raw.get("instruction") or raw.get("title") or raw.get("step")
        else:
            text = raw
        safe, _ = _sanitize_label(text)
        lines.append('    %s["%s"]' % (_mermaid_id(index), safe))
    for index in range(1, len(steps)):
        lines.append("    %s --> %s" % (_mermaid_id(index), _mermaid_id(index + 1)))
    return "\n".join(lines) + "\n"


def concept_map(title, nodes, edges):
    """Mermaid `graph LR` text for a concept map.

    `nodes` is a list of labels (or dicts with `id`/`label`); `edges` is a
    list of `(from, to)` pairs or dicts with `from`/`to` (and an optional
    `label`). Unknown node references are refused rather than silently dropped:
    a map that quietly loses a node teaches the wrong map.
    """
    if not nodes:
        raise VisualizationError(
            "NODES_EMPTY",
            "для карты концепций нужен хотя бы один узел",
        )
    safe_title, _ = _sanitize_label(title or "Карта концепций")
    id_by_key = {}
    ordered = []
    for index, raw in enumerate(nodes, start=1):
        if isinstance(raw, dict):
            key = str(raw.get("id") or "")
            if not key:
                key = str(index)
            label = raw.get("label") or raw.get("name") or key
        else:
            key = str(index)
            label = raw
        if key in id_by_key:
            raise VisualizationError(
                "NODE_ID_DUPLICATE",
                "повторяющийся id узла: %r" % key,
            )
        mid = _mermaid_id(index)
        id_by_key[key] = mid
        safe, _ = _sanitize_label(label)
        ordered.append('    %s["%s"]' % (mid, safe))

    lines = ["graph LR", "    %% %s" % safe_title.replace("\n", " ")]
    lines.extend(ordered)
    for raw in edges or []:
        if isinstance(raw, dict):
            src = raw.get("from")
            dst = raw.get("to")
            edge_label = raw.get("label")
        else:
            src, dst = raw
            edge_label = None
        src_key, dst_key = str(src), str(dst)
        if src_key not in id_by_key or dst_key not in id_by_key:
            raise VisualizationError(
                "EDGE_UNKNOWN_NODE",
                "ребро ссылается на неизвестный узел: %r -> %r" % (src_key, dst_key),
            )
        if edge_label:
            safe_edge, _ = _sanitize_label(edge_label)
            lines.append(
                "    %s -->|%s| %s"
                % (id_by_key[src_key], safe_edge, id_by_key[dst_key])
            )
        else:
            lines.append("    %s --> %s" % (id_by_key[src_key], id_by_key[dst_key]))
    return "\n".join(lines) + "\n"


def sequence_diagram(title, participants, messages):
    """Mermaid `sequenceDiagram` text for an interaction.

    `participants` is a list of names; `messages` is a list of
    `(from, to, text)` triples or dicts with `from`/`to`/`text`. Unknown
    participants are refused — an invisible actor is a wrong diagram.
    """
    if not participants:
        raise VisualizationError(
            "PARTICIPANTS_EMPTY",
            "для диаграммы последовательности нужны участники",
        )
    safe_title, _ = _sanitize_label(title or "Последовательность")
    names = []
    seen = set()
    for raw in participants:
        safe, _ = _sanitize_label(raw)
        if safe in seen:
            raise VisualizationError(
                "PARTICIPANT_DUPLICATE",
                "повторяющийся участник: %r" % safe,
            )
        seen.add(safe)
        names.append(safe)

    lines = ["sequenceDiagram", "    %% %s" % safe_title.replace("\n", " ")]
    for name in names:
        lines.append("    participant %s" % name)
    for raw in messages or []:
        if isinstance(raw, dict):
            src = raw.get("from")
            dst = raw.get("to")
            text = raw.get("text") or raw.get("message") or ""
        else:
            src, dst, text = raw
        src_safe, _ = _sanitize_label(src)
        dst_safe, _ = _sanitize_label(dst)
        if src_safe not in seen or dst_safe not in seen:
            raise VisualizationError(
                "MESSAGE_UNKNOWN_PARTICIPANT",
                "сообщение ссылается на неизвестного участника: %r -> %r"
                % (src_safe, dst_safe),
            )
        safe_text, _ = _sanitize_label(text)
        lines.append("    %s->>%s: %s" % (src_safe, dst_safe, safe_text))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Graphviz DOT generator (string only; the `dot` binary is never spawned)
# ---------------------------------------------------------------------------


def dot_graph(title, nodes, edges):
    """Graphviz DOT text for a graph.

    **Rendering is the caller's business.** This function returns a string and
    never spawns `dot`, never writes a file, never opens a network socket. If a
    picture is wanted, the caller runs a renderer themselves (or pastes the
    DOT into one) — the harness does not.
    """
    if not nodes:
        raise VisualizationError(
            "NODES_EMPTY",
            "для DOT-графа нужен хотя бы один узел",
        )
    safe_title, _ = _sanitize_label(title or "Граф")
    id_by_key = {}
    lines = ['digraph "%s" {' % safe_title.replace('"', "'")]
    for index, raw in enumerate(nodes, start=1):
        if isinstance(raw, dict):
            key = str(raw.get("id") or "")
            if not key:
                key = str(index)
            label = raw.get("label") or raw.get("name") or key
        else:
            key = str(index)
            label = raw
        if key in id_by_key:
            raise VisualizationError(
                "NODE_ID_DUPLICATE",
                "повторяющийся id узла: %r" % key,
            )
        mid = _dot_id(index)
        id_by_key[key] = mid
        safe, _ = _sanitize_label(label)
        lines.append('    %s [label="%s"];' % (mid, safe))
    for raw in edges or []:
        if isinstance(raw, dict):
            src = raw.get("from")
            dst = raw.get("to")
            edge_label = raw.get("label")
        else:
            src, dst = raw
            edge_label = None
        src_key, dst_key = str(src), str(dst)
        if src_key not in id_by_key or dst_key not in id_by_key:
            raise VisualizationError(
                "EDGE_UNKNOWN_NODE",
                "ребро ссылается на неизвестный узел: %r -> %r" % (src_key, dst_key),
            )
        if edge_label:
            safe_edge, _ = _sanitize_label(edge_label)
            lines.append(
                '    %s -> %s [label="%s"];'
                % (id_by_key[src_key], id_by_key[dst_key], safe_edge)
            )
        else:
            lines.append("    %s -> %s;" % (id_by_key[src_key], id_by_key[dst_key]))
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def visualizations_dir(root=None):
    """`<root>/.botai/visualizations` — where generated diagrams are stored.

    The path is derived from `root` only (never the cwd). `save_diagram`
    creates it on demand with `mkdir(parents=True, exist_ok=True)`.
    """
    base = Path(root) if root else Path(__file__).resolve().parent.parent.parent
    return base / ".botai" / "visualizations"


def save_diagram(root, course_id, name, text, *, extension=".mmd"):
    """Write `text` under `.botai/visualizations/<course_id>/<name><ext>`.

    Every component goes through `paths.safe_name` and the resolved Path
    through `paths.ensure_within`, so a course id or file name with a `..` is
    refused rather than normalised. The extension must be in
    `ALLOWED_EXTENSIONS`; anything else is refused so a "diagram" cannot
    become an arbitrary write.
    """
    try:
        safe_course = str(paths.safe_name(course_id, kind="идентификатор курса"))
        safe_name = str(paths.safe_name(name, kind="имя диаграммы"))
    except Exception as e:
        # `pathsafe` raises `PathError` in this tree, but a host-local build
        # may ship a different class. An invalid component is a refusal in
        # either case, and must not surface as a raw traceback.
        raise VisualizationError(
            "PATH_OUTSIDE_VISUALIZATIONS",
            "компонент пути диаграммы недопустим (ожидается slug или UUID): %s" % e,
        )
    if extension not in ALLOWED_EXTENSIONS:
        raise VisualizationError(
            "EXTENSION_NOT_ALLOWED",
            "недопустимое расширение %r: разрешены только %s"
            % (extension, ", ".join(ALLOWED_EXTENSIONS)),
        )
    base = visualizations_dir(root)
    try:
        directory = paths.ensure_within(base, base / safe_course)
        target = paths.ensure_within(base, directory / (safe_name + extension))
    except paths.PathError as e:
        raise VisualizationError(
            "PATH_OUTSIDE_VISUALIZATIONS",
            "путь к диаграмме выходит за пределы .botai/visualizations: %s" % e,
        )
    except Exception as e:
        # Same shape as the component refusal above: a bad path must never
        # escape as a raw traceback from the path helper.
        raise VisualizationError(
            "PATH_OUTSIDE_VISUALIZATIONS",
            "путь к диаграмме выходит за пределы .botai/visualizations: %s" % e,
        )
    directory.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(
            text if text is not None else "", encoding="utf-8", newline="\n"
        )
    except OSError as e:
        raise VisualizationError(
            "DIAGRAM_WRITE_FAILED",
            "не удалось записать диаграмму %s: %s" % (target, e),
        )
    return target


# ---------------------------------------------------------------------------
# Strategy decision helper (no rendering)
# ---------------------------------------------------------------------------


def generate_from_profile(root, course, learner_id, profile):
    """Choose a visual strategy from a learner profile. Decision only.

    Reads `profile.cognitive_style.dominant()` (see `learner_profiling`) and
    returns a small dict describing what to draw — never a rendered picture,
    never content invented from a course the caller did not pass. When no
    profile is available the decision is the neutral `concept_map` fallback,
    which is honest about being a default rather than a measurement.
    """
    dominant = None
    if profile is not None:
        cognitive = getattr(profile, "cognitive_style", None)
        if cognitive is not None and hasattr(cognitive, "dominant"):
            dominant = cognitive.dominant()
        elif isinstance(profile, dict):
            style_doc = profile.get("cognitive_style") or {}
            try:
                dominant = learner_profiling.CognitiveStyle.from_document(
                    style_doc
                ).dominant()
            except Exception:
                dominant = profile.get("dominant_style")

    strategy = _STRATEGY_BY_STYLE.get(dominant or "", "concept_map")
    return {
        "course_id": getattr(course, "course_id", None),
        "learner_id": learner_id,
        "dominant_style": dominant,
        "strategy": strategy,
        "reason_ru": (
            "по профилю обучения выбрана стратегия %s" % strategy
            if dominant
            else "профиль обучения недоступен: выбрана нейтральная стратегия "
            "concept_map (не измерение, а запасной вариант)"
        ),
        "render": False,
        "backends_available": external_backends_available(),
    }
