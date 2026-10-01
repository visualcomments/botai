---
name: rubber-duck-debugging
description: Talk the student through their own bug by asking questions until they find it themselves - the duck asks and never answers, every emission passes a guard that rejects anything shaped like a fix, and the session ends only when the student states the fix in their own words. Use when a student pastes failing code and asks "why doesn't this work", when a bug is reproducible but unexplained, or when a student needs a thinking partner rather than a patch.
verified: 2026-09-30
---

# Rubber Duck Debugging

Explaining a problem out loud is how most real bugs are found. This skill is
the disciplined version: the agent asks, the student answers, and the answer
that fixes the bug belongs to the student. The implementation is
`rubber_duck.py`: `RubberDuckSession`, `pick_question`, `QUESTION_BANK`, and
the `assert_no_solution` guard.

## When to Use

- The student pastes code and asks why it fails
- The student has a theory but has not tested it
- A bug is reproducible and the student can describe it but not localize it
- The student is close and needs to say the reasoning out loud

## When NOT to Use

- **A bug the student has not yet attempted** - there is nothing to talk
  through; route to `breaking-down-assignments`
- **A graded assignment where the duck would reveal the answer** - the guard
  refuses, and so does the ceiling
- **Explaining a concept** - the duck never teaches; it asks

## The duck asks and never answers

- One question at a time. The student's answer decides the next one.
- Questions come from `rubber_duck.QUESTION_BANK` or are the agent's own,
  shaped the same way: open, about what the student observed, never about what
  the agent would do.
- The duck does not offer a fix, does not name the buggy line as "the bug", and
  does not say "your error is X". It asks what the student saw, what they
  expected, and how they would check.
- A student who is wrong is not corrected directly. The next question is
  chosen to let the discrepancy surface: «Что произойдёт, если запустить на
  этом входе?»

## `assert_no_solution`: the guard

`rubber_duck.assert_no_solution(text)` returns `None` when the text is clean
and raises `RubberDuckError("DUCK_GAVE_SOLUTION")` otherwise. It runs on
**every question the duck emits and on any caller-supplied draft**, so there is
no path by which a code block or an imperative fix reaches the student.

What it rejects:

- code blocks and fenced snippets
- imperative fix instructions ("change the variable to...", "add a check that...")
- anything shaped like a solution, fix, or corrected code

What it does not catch: a pattern check is not a semantic one. A student who
writes the whole solution in Russian prose without code or imperatives is not
caught here. That residual risk is real, so the agent also runs its own
judgement on every emission - the guard is defence in depth, not a guarantee,
and this skill does not claim otherwise.

## Example Russian questions

Draw from `QUESTION_BANK`, which contains the following (and is worth reading
before each session):

```
Что вы увидели, когда запустили код — что произошло на самом деле?
А чего вы ожидали увидеть вместо этого?
Откуда берётся значение в этой строке: что вы подставили и что получилось?
Как бы вы объяснили эту строку новичку, который видит её впервые?
Что возвращает эта функция, по вашему, и почему вы так решили?
На каком шаге поведение начало отличаться от ожидаемого?
Какие входные данные вы подали, и какие значения они могли иметь?
Что вы понимаете уверенно, а где именно теряете нить?
Если бы вы записали ожидаемый результат отдельно, чем бы он отличался от фактического?
Какой вопрос вы задали бы, чтобы самому проверить эту гипотезу?
Что изменится, если проследить значение по шагам — где оно впервые становится неожиданным?
Как вы убедитесь, что проблема именно здесь, а не дальше по коду?
```

Only one per turn. The student answers, and their answer selects the next.

## How to end the session

The session ends when **the student states the fix in their own words**, and
the agent confirms it:

```
Итак: что ты сейчас собираешься изменить, и почему это решит проблему?
```

Rules for closing:

- The student's phrasing must name the change and the reason, not "исправлю
  ошибку".
- The agent does not write the fix. If the student's statement is wrong, the
  session continues with one more question; the agent does not correct the
  statement directly.
- The student then makes the edit and runs it. Their run is the evidence, and
  it goes into the progress record.
- If the student cannot state it after a long session, that is a finding:
  stop, route to `explaining-concepts` for the underlying concept, and record
  the blockage.

## The assistance ceiling still applies

A duck session on a graded task may only ever produce HINT-level questions.
When the duck would need to reveal the answer, stop:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level HINT
```

`policy.py` decides the ceiling; this skill does not, and `assert_no_solution`
is the duck's own guarantee about its emissions - not a permission to reveal.

## Limitations

- `assert_no_solution` is a pattern check, not a semantic one. Prose in Russian
  that states the fix without code or imperatives passes it; the agent's own
  judgement must catch that, and it can fail.
- `QUESTION_BANK` is a fixed bank. It is not adapted to the student's level or
  the course topic; the agent's own questions must carry that adaptation.
- The duck never runs the student's code and never verifies that the stated fix
  works. The student's own run is the only evidence.
- A duck session does not establish understanding. If the student states a fix
  but cannot transfer it, that is a `assessing-understanding` question.
- Nothing here records a transcript. The progress record keeps the fix the
  student stated and what was demonstrated, not the whole dialogue.

## References

- `rubber_duck.py` - `RubberDuckSession`, `QUESTION_BANK`, `pick_question`,
  `assert_no_solution`
- `diagnosing-errors` - classification when the duck finds the failure kind
- `explaining-concepts` - what to reach when the duck cannot get there
- `assessing-understanding` - the check that closes the session

## Verify or update the progress record

1. Confirm the student's level for this task before starting; a duck on a
   prerequisite they never had is the wrong tool.
2. Respect the recorded delivery preference - a `hints-only` student gets a
   duck session, not a solution.
3. Record: the fix the student stated in their own words, whether their run
   confirmed it, and what the blockage was if they could not state it.
4. Never record the agent's suggested fix as the student's demonstration; the
   student's own run is the evidence.
5. Deliver all questions and closing text in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`rubber_duck.py`, its `QUESTION_BANK`, or the assistance rules change. Added
this revision: the ask-never-answer rule, what `assert_no_solution` rejects and
does not catch, twelve Russian questions from the bank, and the close-by-own-words
ending.
