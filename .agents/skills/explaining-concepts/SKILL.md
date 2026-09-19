---
name: explaining-concepts
description: Explain course material at the student's current level, Socratic-first, with examples, one idea per step. Use when a student asks "what does X mean", when a lesson did not land, when a concept's prerequisite is missing, or when the course text is unclear and a plain-language explanation plus a worked example is needed.
verified: 2026-09-19
---

# Explaining Concepts

The core tutoring skill. It explains course material the way a good tutor
would: establish the level first, one idea per step, questions before answers,
examples before abstractions — and it stays inside the course track unless the
student asks to look ahead.

## When to Use

- "What does X mean?" / "I don't understand module 2"
- A lesson did not land and the student needs a different explanation
- The course text is unclear, and the student needs it re-explained — then
  possibly supplemented (`providing-supplementary-material`)
- A concept depends on a missing prerequisite

## When NOT to Use

- **Doing the student's assignment** — see `breaking-down-assignments`; a
  concept explanation is never a solution to a graded task
- **Checking whether the student understands** — that is
  `assessing-understanding` (though this skill opens with a quick level check)
- **Adding material the course does not contain** — that is
  `providing-supplementary-material`, and must be labeled

## Method

1. **Level check first.** One or two questions to establish what the student
   already knows about this concept and its prerequisites. Never assume; the
   consent gate in AGENTS.md requires it.
2. **Socratic first.** Ask a guiding question before giving the answer. If the
   student can reach the concept with prompting, the prompting is the teaching.
3. **One idea per step.** State the idea, give one example, check in. Do not
   move to the next idea until the student confirms.
4. **Concrete before abstract.** Start from an example the student can touch;
   generalize after. Name the general rule explicitly once, in the course's own
   terms.
5. **Anchor to the course.** Use the course's notation and terminology. When the
   course is unclear, say so and offer a supplement.
6. **Confirm, then close.** End with a quick check (see
   `assessing-understanding`) and record the outcome.

## Two levels: plain first, then precise — and they must agree

Explain in **two passes over the same account**, never two different accounts:

1. **Plain level** — the working intuition, in the simplest true form. Say that
   it is a simplification and in what respect: «на уровне интуиции…», «если
   упростить до одной мысли…».
2. **Precise level** — the same claim with the qualifications the discipline
   requires (the exceptions, the debate, the sense in which the plain version is
   only roughly right).

The two passes are **the same position at two resolutions**, so the precise
level may *qualify* the plain one but may never *reverse* it. A reversal is a
contradiction the student will notice and lose trust over.

**Before delivering the precise level, check it against the plain one.** If the
precise statement denies something the plain one asserted, do not close the gap
by abandoning the first: state plainly that the simple form was a
simplification, say which part of it does not survive, and replace it. The
honest form of a correction is:

> «Уточнение: на уровне интуиции я сказал, что основа науки — неизменные факты.
> Это упрощение. Точнее: факт всегда нагружен теорией, и «неизменность» относится
> к принятой теории, а не к наблюдению как таковому. Простое утверждение было
> неполным, а не неверным.»

Worked example of the failure this prevents: the agent first says "the basis of
science is unchanging facts", then, when the student pushes, agrees that facts
are theory-laden. Both statements cannot be flatly true. The fix is not to
defend the first, nor to silently adopt the second: mark the first as the plain
simplification and deliver the second as the precise level of the *same* claim.

## Ask organizational questions — and mark them visibly

Some questions are not about the material but about **how to present it**. Ask
them when they would change what you produce, and never bury them inside an
explanation:

- «Подать тему проще или сразу строго?»
- «Разобрать на примере или сразу в общем виде?»
- «Идём по шагам или сразу к сути?»
- «Нужны ли ссылки на источники корпуса или достаточно изложения?»

**Mark them as organizational, distinctly from course content.** Use a leading
tag the student can scan for, on its own line, visually set apart:

```
🧭 Организационный вопрос: подать тему проще или сразу строго?
```

Rules for the marker:

- The tag is always `Организационный вопрос:` (optionally prefixed with 🧭 when
  the channel renders emoji). It is the same every time, so it is scannable.
- It sits on its own line/paragraph, never mid-sentence, so it is never confused
  with a teaching question.
- **At most one organizational question per turn** — it is a fork in the road,
  not a questionnaire. Batch the rest for later.
- Ask at most **twice** per topic. If the student does not engage with the
  organizational question, choose a sensible default (plain level first),
  state the default, and proceed — do not keep asking.

## Mark material that is not from the corpus

Rule 4 of AGENTS.md requires supplements to be labeled; rule 9 governs the
corpus. The marker is **«вне корпуса»** — the same phrase the course itself
uses for material it discusses but cannot quote (copyright-protected authors).

When any part of an explanation, example, or claim did not come from a verified
corpus fragment, mark it **where it appears**, not in a footnote:

- A claim with no corpus fragment behind it: append `(вне корпуса)`.
- A block of such material: prefix the block with
  `> Вне корпуса — не из проверенного корпуса курса:` and give the source if
  there is one.
- A quotation from a copyrighted author: state the idea in your own words and
  mark it `(вне корпуса)`; never present it as a corpus quotation. A direct
  quotation is only ever from the corpus, verified, with coordinates
  «файл · фрагмент #N».

**If the corpus is unavailable locally, everything is «вне корпуса»** and must
be marked so. Never let an unmarked sentence look like it came from the corpus
when the corpus was never consulted — that is the failure rule 4 exists to
prevent.

## Delivery

Respect the recorded delivery preference: hints first by default, full
explanation after engagement, never a full solution to a graded task. If the
student's question requires material the course places later, say where it is
covered rather than teaching it prematurely — unless the student explicitly
asks for a preview.

## Tag the assistance level

Label each explanation step with how much it reveals, mirroring SECS's noise
tags. The tag keeps least-assistance-first honest and lets the student see how
much help they took:

- **HINT** — a question or nudge that does not state the concept ("what stays
  the same when you run this twice?"). Default opening.
- **EXAMPLE** — a worked case that demonstrates the idea without solving the
  student's task. Escalate here when the hint did not land.
- **SOLUTION** — the concept stated outright. Fine for explaining a concept;
  never for a graded task's answer.

Escalate one level at a time, and only when the student is stuck and asks.
A graded task may receive HINT and EXAMPLE but never SOLUTION.

## Worked-example shape

```
Concept: <name>          (course: <module>)
Level check: <Q the student answered>  → <what that established>
Idea: <one sentence in course terms>
Example: <minimal, concrete>
Check-in: <one question to confirm the idea landed>
```

## Rationalizations to Reject

- *"I'll just explain it from first principles."* First principles are not the
  course. Anchor to the track.
- *"They said they get it."* "Get it" is not demonstrated. Close with a check.
- *"The course is wrong, so I'll quietly teach my version."* Say it is wrong,
  then supplement with a labeled source.

## References

- `assessing-understanding` — the level check that opens and the check that closes
- `providing-supplementary-material` — when the course is unclear or thin
- `maintaining-course-progress` — record what was explained and demonstrated

## Last Validated

2026-09-19. Procedure current as of this date; re-verify when the course material or teaching rules change. Added this revision: the two-level (plain → precise) consistency rule, the marked organizational-question convention, and the «вне корпуса» marking rule for material not drawn from the verified corpus.
