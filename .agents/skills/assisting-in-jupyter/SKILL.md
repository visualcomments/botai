---
name: assisting-in-jupyter
description: Read a Jupyter notebook the student provides, name each recorded error from the notebook's own outputs, and suggest the NEXT STEP for a cell rather than rewriting it - because the harness parses notebook JSON and never executes a cell. Use when a student pastes or opens a .ipynb with errors, when a notebook assignment needs review, or when a student is stuck on one cell and does not want the notebook rewritten.
verified: 2026-09-30
---

# Assisting in Jupyter

**The harness does not execute notebook cells.** There is no kernel, no
`Execute`, no hidden run. `integrations.JupyterAssistant` parses the `.ipynb`
JSON and reads what the notebook's own `outputs` already recorded.

## When to Use

- A student shares a `.ipynb` and asks why it fails
- A notebook assignment needs review
- The student is stuck on one specific cell
- The student wants a hint, not a rewritten notebook

## When NOT to Use

- **Producing the notebook** - the ceiling applies; see "Graded tasks"
- **Running the student's code to check a hypothesis** - not possible here; ask
  the student to run it and paste the output
- **Explaining a library concept** - that is `explaining-concepts`; this skill
  reads the student's notebook

## What the assistant actually reads

- `notebook_summary()` - counts, the first recorded error, and a short Russian
  paragraph.
- `analyze_notebook()` - per-cell `{cell_index, cell_type, source, has_error,
  error_kind, advice_ru}`.
- `suggest_cell_fix(cell_index)` - a Russian suggestion for one cell.

`has_error` comes from the cell's recorded `outputs` (`output_type == "error"`
or `ename`), never from running anything. A cell with no recorded error is
reported as having none - not as "probably fine".

The advice table is fixed: `integrations.NOTEBOOK_ERROR_ADVICE` maps
`NameError`, `TypeError`, `KeyError`, `IndexError`, `ValueError`,
`AttributeError`, `ImportError`, `ZeroDivisionError`, `SyntaxError`. An
exception outside that table gets a generic advice that echoes the name, and
that generic answer must be presented as generic rather than dressed up as
expertise.

Worked Russian shape:

```
Ячейка 7: NameError — имя `df_raw` не определено.

Что делать дальше (следующий шаг):
Проверь, создаётся ли `df_raw` выше по ноутбуку и под каким именем.
Если ты создавал её в другой ячейке, возможно, порядок выполнения ячеек
другой: «Ядро» → «Перезапустить и выполнить все».
```

## Suggest the next step, not the finished cell

`Suggest_cell_fix` returns a **suggestion for one cell**. It is never a
rewritten notebook, and never a replacement cell the student pastes in.

- Name the variable, the function, the line - and then ask the student to do
  the edit.
- One cell at a time. Fixing three cells at once leaves the student unable to
  attribute the change to anything they learned.
- Prefer a check the student can run: print the type, look at `dir(...)`, run
  the cell above it. A question they can answer beats a line they can paste.
- If the student asks "просто перепиши мне ячейку", that is the hard refusal
  list, not a small favour.

## Graded tasks

`Suggest_cell_fix` reuses `tutoring.ASSESSMENT_CEILING` - the same table the
policy check uses - rather than duplicating the ceiling logic. For a `graded`
or `unknown` assignment a suggestion that constitutes the full solution is
refused with `SOLUTION_NOT_ALLOWED_FOR_ASSESSMENT`; for `practice`, a
full-solution-level suggestion is allowed.

That is the module's own behaviour, and it is backed by the contract:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level EXAMPLE
```

`policy.py` decides, not this skill. A notebook assignment is graded unless the
accepted contract says otherwise.

## The state that is never checked

**A notebook can look finished and have been run in a state nobody can see.**
The assistant cannot know:

- whether the outputs are current, or stale from three runs ago
- whether the kernel state the outputs came from matches the notebook as it now
  reads
- whether a cell ran at all

So the review says «по записанным выводам» and, where it matters, asks the
student to **Restart & Run All** and report what changed. Never claim a
notebook runs because its saved outputs look clean.

## Limitations

- **No execution.** Nothing is run, no kernel is started, no cell is
  re-evaluated. Every statement about behaviour comes from recorded outputs.
- The advice table is fixed and small. An unlisted exception gets a generic
  answer.
- A cell whose outputs were never recorded shows `has_error: false` because
  nothing was recorded - the honest reading is "no error is recorded", not
  "this cell is correct".
- `suggest_cell_fix` works on one cell. It has no view of variable flow across
  cells, so an `NameError` three cells apart is explained, not diagnosed.
- The file must be one the student provides. There is no notebook server, no
  file browser, and no access to the student's home directory.

## References

- `integrations.py` - `JupyterAssistant`: `analyze_notebook`,
  `notebook_summary`, `suggest_cell_fix`, `NOTEBOOK_ERROR_ADVICE`
- `tutoring.py` - `ASSESSMENT_CEILING`, the table the refusal reuses
- `diagnosing-errors` - classify the failure kind before explaining it
- `rubber-duck-debugging` - when the student wants to find it themselves

## Verify or update the progress record

1. Confirm the level for the notebook's objectives before explaining anything
   new; a student who has not run the file cannot be reviewed.
2. Respect the recorded delivery preference; a notebook hint is HINT by default.
3. Record: the notebook's recorded-error summary, which cells were discussed,
   what the student verified by running it, and what remains unverified because
   no cell was executed.
4. Never record a cell as working because its saved output looks right.
5. Deliver all analysis and suggestions in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`integrations.py`, the advice table, or the tutorial schema changes. Added this
revision: the explicit no-execution statement, the fixed advice mapping, the
next-step-not-finished-cell rule, the graded-task refusal, and the
stale-outputs caveat.
