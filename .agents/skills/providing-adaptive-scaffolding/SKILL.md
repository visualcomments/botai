---
name: providing-adaptive-scaffolding
description: Give help at the rung the student's current evidence supports - a 0..4 ladder from "solve it yourself" to a full worked model - fading the help back as they succeed and raising it only on real evidence of struggle. Use when a student has failed the same step repeatedly, asks for more help than the hint they got, loses a thread in a multi-step task, or needs a worked model of a process rather than an explanation of a concept.
verified: 2026-09-30
---

# Providing Adaptive Scaffolding

Support that shrinks as competence grows. The module is `scaffolding.py`:
`ScaffoldingEngine` with `determine_level`, `generate_hint`,
`fade_scaffolding`, `escalate`, and the `ScaffoldingLevel` enum 0..4. The
agent reads the level the engine returned and renders it; it does not invent a
rung.

## When to Use

- The same step has failed two or more times in a row
- A student asks for more help after a hint did not land
- A multi-step task stalls: the student cannot say which step comes next
- A student asks to see a worked example of a *process* (this is not the same
  as `explaining-concepts`, which explains a concept)

## When NOT to Use

- **Explaining why something is true** — that is `explaining-concepts`
- **Reviewing an attempt** — that is `giving-feedback`; scaffolding produces
  help, feedback judges work that exists
- **The student asked for the level explicitly** — give the rung they asked
  for, provided the assistance ceiling allows it

## The ladder

`scaffolding.ScaffoldingLevel`, mapped to what each rung reveals
(`scaffolding.LEVEL_ASSISTANCE`):

| Level | Name | Assistance | What it gives |
|---|---|---|---|
| 0 | `NONE` | none | Nothing. "Try it yourself; ask if you get stuck." |
| 1 | `ATTENTION` | `HINT` | A pointer at one place in the work to look at |
| 2 | `RECALL` | `HINT` | A question that calls up the relevant concept |
| 3 | `STRUCTURE` | `EXAMPLE` | The skeleton of the process, steps to walk, still unfilled |
| 4 | `FULL` | `SOLUTION` | A complete worked model the student studies and then redoes |

Level 0 is not filler. It exists so the ladder has a floor, and a template that
nudged at level 0 would make the ladder meaningless.

## How the level is decided

`ScaffoldingEngine.determine_level(consecutive_failures,
confusion_indicators, student_profile)`:

- 0 failures -> `NONE`; 1 -> `ATTENTION`; 2 -> `RECALL`
- >=3 failures together with `asks_basic_questions` -> `STRUCTURE`;
  >=4 failures -> `FULL`
- `time_stuck` above 600 seconds with >=2 failures -> one rung up
- `random_changes` (thrash: repeated edits of the same lines) with >=1 failure
  -> one rung up
- low `challenge_tolerance` (<0.3, easily frustrated) -> the ladder climbs one
  step faster
- high `challenge_tolerance` (>0.7, wants to be pushed) -> one step slower

`escalate` only ever raises the level; `fade_scaffolding` drops exactly one
rung on success and never below `NONE`. So within one cycle the ladder is
monotone upward, and a single success is the only thing that lowers it.

## The ceiling is not this skill's decision

The rung is a help level, not a permission. `scaffolding.ceiling_for` delegates
to `tutoring.ASSESSMENT_CEILING`:

| Task status | Ceiling |
|---|---|
| `graded` | `EXAMPLE` (rung 3) |
| `unknown` | `EXAMPLE` (rung 3) |
| `practice` | `SOLUTION` (rung 4) |

So on a graded or unknown assignment the answer is never rung 4: the engine
clamps there, and `generate_hint(level=FULL, ...)` raises
`ScaffoldingError("SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT")`. The level is
re-checked against the accepted contract before it is rendered:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level SOLUTION
```

**`policy.py` decides, not this skill.** If the ladder and the policy
disagree, the policy wins and the rung drops. A student's own statement that a
task "is only practice" does not move it into the `practice` row.

Two extra limits from AGENTS.md, because a ladder walked step by step can still
arrive at a finished answer: after two failed cycles on the same prerequisite,
offer a different explanation or a break rather than the next rung; and when the
accumulated rungs would add up to the graded answer, switch to a different
practice task.

## Worked Russian examples, one per rung

Rung 0 - `NONE`:

```
Попробуй решить самостоятельно. Если застрянешь — попроси подсказку.
```

Rung 1 - `ATTENTION`:

```
Обрати внимание на условие цикла. Что должно быть верно на каждом шаге,
чтобы цикл вообще остановился?
```

Rung 2 - `RECALL`:

```
Вспомни правило про область видимости переменной. Оно объясняет,
почему именно здесь возникает ошибка?
```

Rung 3 - `STRUCTURE`:

```
Вот структура решения:
1. Считать входные данные.
2. Проверить, что они соответствуют ожидаемому виду.
3. Выполнить само преобразование.
4. Вывести результат в требуемом формате.

Теперь пройди по ней самостоятельно, шаг за шагом.
```

Rung 4 - `FULL`, **practice tasks only**:

```
Вот полное решение с объяснением. Изучи его внимательно и попробуй
применить тот же подход к похожей задаче.
```

## Limitations

- The engine counts failures and reads indicators the caller supplies. It does
  not observe the student: if the agent does not pass honest
  `consecutive_failures` and `confusion_indicators`, the ladder is fiction.
- `challenge_tolerance` comes from the stored profile
  (`learner_profiling.LearningProfile`). A stale or absent profile falls back to
  the default; the adjustment is then simply not applied.
- `ScaffoldingEngine.escalate` raises the level and never lowers it. Fading is a
  separate call; an agent that only escalates will pin a student at rung 4.
- The ceiling is enforced by `tutoring.ASSESSMENT_CEILING` and `policy-check`.
  This skill cannot raise it, and cannot detect a rephrased request; a host with
  no output gate can still leak an answer in prose.
- Nothing here executes the student's code or checks that the hint helped.

## References

- `scaffolding.py` - `ScaffoldingEngine`, `ScaffoldingLevel`, `generate_hint`,
  `fade_scaffolding`, `ceiling_for`
- `tutoring.py` - `ASSESSMENT_CEILING`, `REVIEW_INTERVALS`
- `providing-adaptive-scaffolding` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`
- `diagnosing-errors` - classify the error before choosing a rung
- `assessing-understanding` - the check that decides whether the rung came down

## Verify or update the progress record

1. Confirm the student's level for this task before scaffolding (`assessing-understanding`).
2. Respect the recorded delivery preference; do not climb above it by default.
3. Record which rungs were given, at which task status, and what the student did
   next - the rung history is the evidence that fading happened.
4. If a rung above the ceiling was requested, record the refusal and the
   alternative offered.
5. Deliver all text the student reads in Russian (`language-and-translation`).

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when the course
contract, the scaffolding ladder, or the assistance rules change. Added this
revision: the 0..4 ladder mapped to `LEVEL_ASSISTANCE`, the
`determine_level` trigger rules, the `challenge_tolerance` adjustment, the
`graded`/`unknown` refusal at rung 4, and the `policy-check` re-check.
