---
name: creating-video-explanations
description: Produce a video-style explanation entirely offline as a reproducible script plus a storyboard plus Mermaid and DOT stills the student renders themselves, because the harness ships no text-to-speech, no Manim, and no screen recorder. Use when a student asks for a video, when a concept would be better shown over time than in a diagram, or when the student wants to author a video explanation themselves.
verified: 2026-09-30
---

# Creating Video Explanations

**There is no video in this harness.** Named plainly, because this is the kind
of thing that gets described as available when it is not:

- **No TTS.** `speech_interface` is an offline scaffold; `synthesize()` and
  `transcribe()` accept an *injected local backend* supplied by the caller, and
  with no backend both raise `SpeechError("SPEECH_BACKEND_UNAVAILABLE")`. No
  engine ships.
- **No Manim.** It appears in `visualizations.EXTERNAL_BACKENDS` alongside
  graphviz, matplotlib and plotly, and `external_backends_available()` reports
  every one as `False`.
- **No screen recorder**, no animation renderer, no video encoder.
- **No network.** Nothing here calls an external API or launches a process.

So this skill does not make a video. It makes the three things that make a
video, and hands them over reproducibly.

## When to Use

- The student asks for a video, a screencast, or "объясни на видео"
- A process unfolds over time and a single diagram does not carry it
- The student is writing their own course material and needs a storyboard
- The student wants a reproducible build they can re-run themselves

## When NOT to Use

- **The student wants the answer** - a walkthrough of a graded assignment is a
  solution in a different container
- **A concept explanation** - that is `explaining-concepts`; prose is cheaper
  and often enough
- **A diagram is enough** - `generating-visual-explanations` produces one in
  seconds and the student can read it immediately

## The offline path

### 1. The script

Write the narration first, in Russian, in the student's own words as a draft
they edit. One idea per shot. No jargon the student has not met; no sentence
that reads like a lecture instead of an explanation. Give it the working title
and the running time, and mark any line that is (вне корпуса) so the marking
rule still applies to a script.

### 2. The storyboard

A numbered list of shots. For each: what is on screen, what is said, and the
diagram or code the shot shows.

```
Кадр 1.  Экран: пустой терминал.  Голос: «Смотрим, что происходит,
          когда скрипт запускают без аргументов».
Кадр 2.  Экран: Mermaid-схема вызова (см. ниже).
          Голос: «Три шага: разобрать аргументы, прочитать файл, ...».
Кадр 3.  Экран: код, 12 строк.
          Голос: «Здесь важно, что происходит проверка ДО чтения».
```

### 3. The stills

Each shot's visual is a **Mermaid or DOT text block** from
`visualizations.py` - `flowchart_from_steps`, `concept_map`,
`sequence_diagram`, `dot_graph`. The student pastes each block into a Mermaid
viewer or runs it through `dot` locally. Full procedure and the honest limits:
`generating-visual-explanations`.

### 4. A reproducible build the student runs

The deliverable that makes this real is not the script - it is a script the
student can re-run. Give them a text file - not a binary, not a claim:

```
build_video.md   - the script and storyboard above
still_01.mmd     - Mermaid text for shot 1
still_02.mmd     - Mermaid text for shot 2
still_03.dot     - Graphviz DOT for shot 3
```

And say plainly what they will need to do themselves:

```
Чтобы получить видео, у себя на машине:
1. Отрендерить каждый .mmd в изображение (любой Mermaid-вьюер).
2. Собрать изображения в слайды и озвучить своим голосом или локальным TTS.
Всё это делается на твоей стороне: в харнессе ни рендерера, ни
озвучки нет.
```

## What the agent must not do

- Do not call an external API, an online TTS, or a hosted renderer. The
  harness opens no socket; that is a design property, not a limitation to route
  around.
- Do not launch a subprocess, a renderer, or `ffmpeg`. `opencode.json` narrows
  `bash` to `make *` and `git *`, and every other command needs a human's
  approval - which is the correct outcome here, because the agent should not be
  producing media.
- Do not hand back a file path pretending to be a video, an audio file, or a
  thumbnail. An absent capability is named as absent, the way
  `speech_interface` names it.
- Do not invent a duration, a resolution, a file size, or a rendering time.
  Nothing was rendered, so there is nothing to report.
- Do not produce the narration as a *solution* to a graded task. A video
  explanation of a graded assignment is a `SOLUTION` in a different container.

## The ceiling still applies

The container is irrelevant to the ceiling. A video script that walks through a
graded assignment's answer is a solution:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level EXAMPLE
```

`policy.py` decides. A student asking for a video of the graded task being
solved is asking for the answer with extra steps.

## Limitations

- **No media is produced.** Script, storyboard, and stills-as-text only.
- **No TTS, no Manim, no recorder, no network, no subprocess.** All four are
  absent, not merely unconfigured.
- Storyboard timing is an estimate in the agent's head, not a measured
  duration. It is labelled as an estimate.
- Mermaid and DOT stills are text. The student renders them; nothing here
  verifies that they render.
- A video explanation is not evidence of understanding. Watching a good one and
  being able to reproduce the reasoning are different acts; that check is
  `assessing-understanding`.
- Script quality is the agent's prose. It carries the marking obligations of any
  learner-facing material, including (вне корпуса) for anything ungrounded.

## References

- `visualizations.py` - `flowchart_from_steps`, `concept_map`,
  `sequence_diagram`, `dot_graph`, `save_diagram`, `external_backends_available`
- `speech_interface.py` - the offline scaffold and
  `SpeechError("SPEECH_BACKEND_UNAVAILABLE")`
- `generating-visual-explanations` - the stills, in full
- `explaining-concepts` - the narration discipline the script follows
- `creating-video-explanations` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for the topic before writing the script; a script written
   above the student's level teaches nothing when it plays.
2. Respect the recorded delivery preference and the assistance ceiling - a
   "video of the answer" is a graded-task solution request.
3. Record: the script and storyboard produced, which stills were generated as
   text, and explicitly that **no media was rendered**.
4. Never record a video, an audio file, or a rendered still as existing when
   nothing was rendered.
5. Deliver the script, storyboard, and every line the student reads in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when the harness gains
a renderer, a TTS engine, or a recorder. Added this revision: the explicit
statement that no TTS, Manim, or recorder ships, the script-storyboard-stills
offline path, the reproducible build handed to the student, and the refusal to
call any API or launch a process.
