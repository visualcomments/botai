---
name: diagnosing-errors
description: Classify what actually went wrong in a student's attempt before saying anything about it, and route the failure to the intervention that fixes it: a pointer for a slip, a re-teach for a misconception, a prerequisite review for a missing base. Use when an attempt fails, when a student reports an error message, when the same objective has failed across several sessions, or when the agent is unsure which of the six error kinds it is looking at.
verified: 2026-09-30
---

# Diagnosing Errors

Wrong feedback applied to the wrong kind of failure is worse than no feedback.
The classifier is `error_diagnosis.py`:
`ErrorDiagnosticEngine.diagnose(attempt, correct_solution, problem_context,
student_history)`, which compares two texts - it never executes them - and
returns an `ErrorDiagnosis` with an `ErrorType` and a `suggested_intervention`.

## When to Use

- The student submits an attempt that fails
- An error message is pasted
- The same objective has been blocked across sessions
- The agent is about to write feedback and does not know what kind of failure
  it is describing

## When NOT to Use

- **Explaining the concept** - diagnosis names the kind of failure; the re-teach
  is `explaining-concepts`
- **The student asked how to open a file or what a command does** - answer
  directly; this is not an error in their work
- **The attempt fails because a graded task is graded** - refusal and
  alternative, not a diagnosis

## Sequence: classify first, then talk

1. Read the attempt and the correct solution or specification fully.
2. Call the classifier. For a repeating pattern, let it look at
   `student_history`; `diagnose` never raises on empty input.
3. Only then choose the intervention. **No feedback before classification.**
   Feedback written first and justified later is how a slip becomes a
   permanent false story about the student.

## The six error kinds

`error_diagnosis.ErrorType`, each with its Russian intervention
(`error_diagnosis.INTERVENTION_BY_TYPE`):

| `ErrorType` | Value | Meaning | Intervention |
|---|---|---|---|
| `CARELESS` | `careless_mistake` | The attempt is near-identical to correct; the difference is a slip | «Просто укажи на опечатку или мелкую неточность.» Point, do not re-teach. |
| `MISSING_PREREQ` | `missing_prerequisite` | A concept the task assumes is absent | «Сначала объясни недостающую предпосылку, потом вернись к задаче.» |
| `CONCEPTUAL` | `conceptual_misconception` | A wrong model, not a wrong character | «Полное переобъяснение концепции с примерами и контрвопросом.» |
| `OVERGENERALIZATION` | `overgeneralization` | A rule applied past where it holds | «Покажи границу применимости правила: где оно работает, а где нет.» |
| `PROCEDURAL` | `procedural_slip` | The structure is right; one step is wrong | «Укажи место ошибки, не объясняй концепцию заново.» |
| `INCOMPLETE` | `incomplete_reasoning` | The reasoning stops before the end | «Попроси продолжить рассуждение с того места, где оборвалось.» |

Two contrasts are the point of the skill:

- A `careless_mistake` gets a **pointer**, not a re-teach. Re-explaining a
  concept to a student who typed a wrong variable name teaches nothing and
  erodes trust.
- A `conceptual_misconception` gets a **re-teach**, not a pointer. Pointing at
  the line where the wrong belief produced the wrong output leaves the belief
  intact and guarantees a repeat failure on the next variation.

The classifier distinguishes the first pair by similarity: below the
`_is_minor_difference` threshold a near-identical attempt is a slip; recurring
patterns are matched by token similarity so the same misconception is not
diagnosed differently every time it appears.

## When unsure: ask a diagnostic question

Classification is a claim and can be wrong. When the evidence does not settle
it, **ask before asserting** - a diagnostic question that would discriminate
between the two candidate kinds. A cheap discriminator:

- «Что должно было получиться, по твоей формулировке задачи?» - separates a
  wrong model (CONCEPTUAL) from a slip (CARELESS) about an understood rule.
- «Можешь объяснить эту строку своими словами?» - separates an ununderstood
  prerequisite (MISSING_PREREQ) from a typing error (CARELESS).

If the student's answer does not settle it, say plainly that you are unsure and
which two you are between. Record the ambiguity as an open question rather than
committing to a diagnosis you cannot support.

## The intervention is the next step, not the fix

`ErrorDiagnosis` names what to do; it never does it. The re-teach is
`explaining-concepts`, the prerequisite review is
`planning-study-sessions`, the pointer is a line in `giving-feedback`. Do not
fold a solution into the diagnosis, and re-check the assistance ceiling before
anything is written:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level HINT
```

`policy.py` decides what a graded or unknown assignment may receive; on a graded
task the diagnosis may only ever surface as HINT or EXAMPLE.

## Limitations

- `ErrorDiagnosticEngine.diagnose` compares two texts. It is a pattern check,
  not a semantic one, and a well-worded attempt with a wrong conclusion can be
  misread as correct structure.
- A missing `correct_solution` (or an empty one) means there is nothing to be
  wrong *against*; the engine then only looks for a repeating misconception in
  history. That is a weaker diagnosis, and must be labelled as such.
- The six kinds are a closed set. A failure outside them lands in
  `INCOMPLETE` with a reason string, not in an invented category.
- `INTERVENTION_BY_TYPE` is phrasing, not enforcement. Nothing here refuses an
  over-revealing diagnosis - the ladder in `tutoring.ASSESSMENT_CEILING` and
  `policy-check` do that.
- The classifier never runs the student's code and never verifies a claim about
  what the code does.

## References

- `error_diagnosis.py` - `ErrorDiagnosticEngine`, `ErrorType`,
  `INTERVENTION_BY_TYPE`, `_find_repeating_patterns`
- `giving-feedback` - the shape the diagnosis lands in
- `explaining-concepts` - the re-teach a `CONCEPTUAL` or `MISSING_PREREQ`
  diagnosis routes to
- `providing-adaptive-scaffolding` - the ladder rung that follows the diagnosis
- `diagnosing-errors` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the student's level for this task before diagnosing it.
2. Respect the recorded delivery preference when phrasing the intervention.
3. Record the classified kind with the evidence that chose it, and the
   alternative you rejected - a diagnosis is falsifiable only if the rejected
   candidate stays visible.
4. When you asked a diagnostic question, record what the student answered and
   whether it settled the classification.
5. Deliver the diagnosis and its intervention in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`error_diagnosis.py`, the error-kind table, or the assistance rules change.
Added this revision: the six `ErrorType` values with their Russian
interventions, the classify-before-feedback sequence, the ask-when-unsure rule,
and the pointer-versus-re-teach contrast.
