---
name: multidimensional-assessment
description: Assess one learning objective across Bloom's six levels and SOLO's five structural levels, where every score carries the evidence that justifies it and no score is a grade. Use when a quick check is too coarse, when an objective is claimed "covered" on the strength of recall alone, when the progress record needs a defensible per-level picture, or when a teacher asks what exactly a student can do with a topic.
verified: 2026-09-30
---

# Multidimensional Assessment

One number per objective hides the thing that matters: a student can recall a
definition and still fail to use it. This skill splits the claim into levels,
attaches evidence to each, and refuses to let the parts stand in for the whole.

The shape is `schemas/v2/assessment.schema.json`; the per-learner roll-up is
`analytics.py` (`build_student_analytics`, `validate_analytics`), whose bands
are `analytics.BAND_RU` (`требует внимания` / `на своём пути` /
`устойчиво продвигается`).

## When to Use

- A quick check passed on recall and the objective is about using the idea
- The record says an objective is `demonstrated` on thin evidence
- Planning what to teach next and the track alone does not say
- A teacher asks "what exactly can they do with this?"

## When NOT to Use

- **A two-minute comprehension check** - that is `assessing-understanding`, and
  it is the right tool far more often
- **Grading** - this is formative and never becomes a mark
- **Scaffolding a stuck student mid-step** - that is
  `providing-adaptive-scaffolding`; diagnosis first, assessment later

## Bloom's six levels

Scored 0..1 each, `remembering`, `understanding`, `applying` required; the
upper three optional because many objectives genuinely stop at `applying`.

| Level | What it claims | Evidence that supports it |
|---|---|---|
| `remembering` | Can state the definition, name the parts | States it without prompting |
| `understanding` | Can restate it in own words, explain a case | **The student's own words**, unprompted |
| `applying` | Can use it in a **new** situation | Correct result on a case the lesson did not cover |
| `analyzing` | Can break a case into parts and locate the cause | Separates cause from symptom in unfamiliar input |
| `evaluating` | Can judge whether a solution is sound, and say why | Argues a judgement with a stated criterion |
| `creating` | Can produce a working artefact | A produced artefact that runs or holds up |

Two rules carry the weight:

- **`understanding` requires the student's own words.** A paraphrase that
  reproduces the lesson's phrasing is recall wearing a costume. Ask «объясни своими
  словами» and accept a rough explanation that is genuinely theirs; reject a
  polished one that is the text back.
- **`applying` requires a new context.** A second run of the worked example from
  the lesson measures `remembering` again. Change one variable and see whether
  the idea travels.

## SOLO's five structural levels

`assessment.schema.json#/$defs/solo_level`, an enum:

- `prestructural` - no relevant idea brought in
- `unistructural` - one relevant idea, isolated
- `multistructural` - several relevant ideas, unconnected
- `relational` - the ideas are connected into a coherent whole
- `extended_abstract` - transferred and generalised beyond the case

SOLO describes the *structure* of what the student produced, so it can be low
while Bloom is high (a fluent but disconnected recital). Report both when they
disagree; that gap is the useful finding.

## What a quick check can honestly claim

A two- or three-item check can credibly claim `remembering` and, with an
explain-back item, `understanding`. It cannot claim `applying` unless one item
is a fresh case. It cannot claim the top three levels at all - those need
artefacts and argument, not quick checks. Write the score you can support and
leave the rest out; the schema makes only the first three required, and a
missing level is honest where a zero would be a false claim.

## A score is a claim that needs evidence

`assessment.schema.json` `$defs/bloom_score` requires **both** `score` and a
non-empty `evidence` string, in Russian, saying what was actually demonstrated.
This is the same rule the progress record applies to a mastery claim, and the
same rule the record enforces: a state change with no evidence is refused.

Worked shape:

```json
{
  "schema_version": 2,
  "assessment_id": "intro-regression-check-01",
  "learning_objective": "linear-regression-fit",
  "bloom_levels": {
    "remembering": {
      "score": 1.0,
      "evidence": "Без подсказки назвал формулу линейной регрессии и смысл коэффициентов."
    },
    "understanding": {
      "score": 0.7,
      "evidence": "Своими словами объяснил, что коэффициент показывает изменение отклика на единицу предиктора."
    },
    "applying": {
      "score": 0.3,
      "evidence": "Верно подставил коэффициенты в формулу, но выбрал неверные предикторы для новой задачи."
    }
  },
  "solo_level": "multistructural",
  "confidence": 0.6
}
```

`confidence` is how confident the assessment is in these scores; a thin
evidence base says so rather than projecting false certainty. When confidence is
low, the next action is a better check, not a firmer claim.

## Limitations

- The schema validates structure. It cannot tell whether the `evidence` string
  is true - an invented but plausible sentence passes. Honest evidence is the
  agent's responsibility.
- `analytics.build_student_analytics` assembles from stored sessions, attempts,
  checks and objective states. It sees what was recorded; a check never run is
  invisible, and an unrecorded attempt does not lower a score.
- `analytics.peer_comparison` returns an anonymous band only, and only over a
  caller-supplied comparison set. It is not a ranking and names nobody.
- Nothing here grades. A band such as «устойчиво продвигается» is a description
  of the learner's own record, not a mark, and the course contract remains the
  only thing that decides one.

## References

- `analytics.py` - `build_student_analytics`, `validate_analytics`,
  `BAND_RU`, `PERFORMANCE_WEIGHTS`
- `schemas/v2/assessment.schema.json` - the document shape and the evidence rule
- `tutoring.py` - `evaluate_mastery`, the reducer that turns checks into a stage
- `assessing-understanding` - the quick check that supplies the evidence
- `multidimensional-assessment` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the level for this objective before assessing it, and use
   `assessing-understanding` where a quick check will do.
2. Respect the recorded delivery preference: a formative assessment does not
   hand over the answer it is asking about.
3. Never convert a score into a grade, and never claim a level the evidence does
   not support; leave it out instead.
4. Record the assessment with its evidence and its `confidence` in the progress
   file, so a later session can tell a thin claim from a supported one.
5. Deliver the summary to the student in Russian
   (`language-and-translation`).

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when the assessment
schema, the analytics bands, or the course objectives change. Added this
revision: the Bloom and SOLO level tables, the own-words and new-context rules,
the honest ceiling of a quick check, and the worked JSON shape.
