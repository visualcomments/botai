---
name: generating-visual-explanations
description: Choose and produce a diagram when structure is the thing being taught - Mermaid flowcharts, concept maps, sequence diagrams and Graphviz DOT - as text the student renders themselves, matched to the learner's dominant cognitive style, with honest refusal when a raster chart is asked for and no plotting backend exists. Use when prose has already failed to carry a relationship, when a process has more than three steps, or when the student's profile says they think diagrammatically.
verified: 2026-09-30
---

# Generating Visual Explanations

Some things stop making sense in a sentence and start making sense as a shape.
This skill decides when, and generates the diagram **as text** - which is all the
harness can do. The module is `visualizations.py`.

## When to Use

- A relationship between more than two things is not landing in prose
- A process has three or more steps and the student keeps losing the order
- The student asks "а можно нарисовать?"
- A concept has prerequisites that need to be seen as a graph
- The learner's profile indicates a visual or kinesthetic preference

## When NOT to Use

- **The idea is one sentence** - a diagram for «переменная хранит значение» is
  overhead
- **The student needs the code, not the picture** - a diagram does not
  substitute for running anything
- **The relationship is genuinely linear and prose works** - try prose first;
  the diagram is the second attempt, not the first

## When a diagram beats prose

Ask three questions before drawing:

1. **Is there a shape?** Branches, cycles, ordering, or many-to-many links. If
   there is no shape, there is nothing to draw.
2. **Did prose fail once already?** A diagram after a clear explanation is
   redundant. A diagram after a failed explanation is the point.
3. **Is it the learner's shape?** `visualizations.generate_from_profile` reads
   `profile.cognitive_style.dominant()` (`learner_profiling.CognitiveStyle`) and
   returns a *decision* - what to draw - never a picture.

## The four generators

| Call | Produces |
|---|---|
| `flowchart_from_steps(steps)` | Mermaid `flowchart TD` from an ordered list of instruction strings |
| `concept_map(nodes, edges)` | Mermaid `graph LR` from labels and `(from, to)` pairs |
| `sequence_diagram(participants, messages)` | Mermaid `sequenceDiagram` from `(from, to, text)` triples |
| `dot_graph(...)` | Graphviz DOT text |

Every one returns **source text**. `flowchart_from_steps` is explicit that the
module does not run a renderer and the caller hands the text to a Mermaid
viewer. `dot_graph` states it just as directly: it returns a string and never
spawns `dot`, never writes a file, never opens a network socket.

So the deliverable to the student is the **text plus one line of how to view
it**. Never a claim that an image was produced.

```
Вот схема в формате Mermaid. Открой любой онлайн-редактор Mermaid
(или расширение VS Code с Mermaid) и вставь этот текст - получится диаграмма.
```

`save_diagram(text, name, ext)` writes the text under
`.botai/visualizations/<course_id>/<name><ext>` when the student wants a file.
Every name goes through `paths.safe_name` and every resolved path through
`paths.ensure_within`, so a course id or name containing `..` is refused
rather than normalised, and the extension must be in `ALLOWED_EXTENSIONS`.

## Sanitisation and truncation

- `MAX_LABEL_CHARS` bounds every label. A node label longer than the limit is
  truncated by the generator - so a truncated diagram is expected, and the
  agent must not present a clipped label as the concept's name.
- `ALLOWED_EXTENSIONS` bounds what can be written. Anything outside it is
  refused, so a "diagram" cannot become an arbitrary write.
- Student-supplied text goes through `security.sanitize_user_input` before it
  reaches a label; a label is text that gets rendered elsewhere.

## Honest note on charts

`visualizations.EXTERNAL_BACKENDS` is
`('graphviz', 'matplotlib', 'plotly', 'manim')` and
`external_backends_available()` reports **every one as `False`** - the harness
ships none of them. `render_chart` is a **refusal**: it accepts `kind` and `data`
only so the refusal can name what was asked for, and computes nothing. The
design's data-visualization section is deliberately out of scope here.

So when a student asks for a chart of their results:

```
Настоящий график здесь построить нечем: в харнессе нет бэкенда
построения графиков (matplotlib, plotly, manim не установлены).
Но структуру данных можно показать так - таблицей или схемой
из узлов, а сам график ты построишь у себя в Jupyter.
```

That is the honest answer. Drawing a fake chart, or describing one as if it
existed, is exactly what this module refuses to do.

## Limitations

- **No renderer, no image, no video.** The output is source text. The student
  renders it.
- **No charts.** `render_chart` refuses. `external_backends_available()` is all
  `False`. A student who wants a chart gets a table, a diagram, or their own
  local plotting.
- Labels are truncated at `MAX_LABEL_CHARS`; a long concept name loses its tail.
- `generate_from_profile` is a decision from a stored profile. A missing or
  stale profile means the visual-preference hook does not apply - the diagram
  still gets made, just not on evidence.
- Nothing here fetches a renderer, installs a package, or opens a socket.
- A diagram is not evidence of understanding. Reading one correctly is a
  different act from drawing one; that is an `assessing-understanding`
  question.

## References

- `visualizations.py` - `flowchart_from_steps`, `concept_map`,
  `sequence_diagram`, `dot_graph`, `generate_from_profile`, `save_diagram`,
  `external_backends_available`, `MAX_LABEL_CHARS`, `ALLOWED_EXTENSIONS`
- `learner_profiling.py` - `CognitiveStyle`, `LearningProfile.dominant()`
- `explaining-concepts` - prose first, then the diagram
- `creating-video-explanations` - the offline storyboard path
- `security.py` - `sanitize_user_input` for student-supplied labels
- `generating-visual-explanations` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for the topic before diagramming it; a diagram the student
   cannot read is not simpler than the prose.
2. Respect the recorded delivery preference and check the cognitive-style
   profile before assuming a diagram is wanted.
3. Record: which diagram was produced, why prose had failed, and what the
   student could read off it unaided.
4. Never record a diagram as rendered, an image as produced, or a chart as
   drawn. `external_backends_available()` is all `False`, and the record must
   say so.
5. Deliver the diagram text, the viewing instruction, and the truncation note
   in Russian (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`visualizations.py`, the backend list, or the profile schema changes. Added this
revision: the four text generators, the cognitive-style hook, the
sanitisation and truncation rules, and the explicit refusal to pretend a chart
can be drawn.
