---
name: narrative-teaching
description: Wrap the course's real objectives in a quest frame with fictional characters and unlockable content, where the story never asserts facts, the NPCs are never presented as real people or authority, unlocked content maps to real objective completion, and rewards are evidence-based rather than awarded for time spent. Use when a student asks for a story-driven course, when a cohort needs motivation through a long module, or when a narrative document already exists for the course.
verified: 2026-09-30
---

# Narrative Teaching

A frame makes a long module bearable. A frame that invents facts, promotes a
character into an authority, or awards progress for sitting in a chair is worse
than no frame. The shape is `schemas/v2/narrative.schema.json`; the rewards are
`leaderboards.py` and `personas.py` (`evaluate_achievements`).

## When to Use

- A student asks for the course to be taught as a story or a quest
- A cohort is losing momentum through a long module
- The course already ships a `narrative` document
- The student wants to write their own narrative for the material

## When NOT to Use

- **A student who asked a question** - answer it; a quest they did not ask for
  is an obstacle
- **Contradicting the course** - the narrative is a wrapper, never an override
- **Grading** - the contract decides; see "The contract still decides"

## Quest framing over the real objectives

Every quest in the schema carries `objectives`: a non-empty list of objective
ids matching the track.

- **A quest with no objectives is flavour with nothing to learn.** Do not
  create one.
- **A quest covers objectives that exist.** Never invent an objective id; the
  track is the source.
- The mapping is one-to-one and checkable: for each quest, the student can name
  which course objective it covers. If they cannot, the quest is decoration.

State the mapping explicitly when the frame starts, in Russian, so the student
knows the frame is a route through the real material and not a substitute for
it:

```
Это квест - способ пройти тему. За каждым заданием квеста стоит
настоящая цель курса: [список целей].
```

## NPCs are fiction and must never be authority

`narrative.schema.json#/$defs/character` is a framing device. The `premise` is
explicitly a scene-setting device that does not assert facts about real people,
real companies, or the course's claims. That constraint extends to the whole
cast:

- **Never present a character as a real person, a real expert, a teacher, or a
  grader.** They are invented, and the student is told so.
- **A character never says something the course does not support.** If the
  character asserts a fact, the fact belongs to the corpus or it is marked
  (вне корпуса) - the character's mouth does not launder it.
- **A character never grants an exception, a hint beyond the ceiling, or a
  grade.** «The oracle will give you the answer» is not a scenario, it is a
  violation with a costume on.
- Say it once, plainly, when the frame starts:

```
Персонажи в этом квесте - выдуманные. Они задают вопросы и подсказывают
направление, но ответы и оценки - только настоящие, из курса.
```

## Unlocks map to real completion

Unlockable content is gated on **objective completion recorded in the progress
record**, not on the student having read a page.

- A gate opens when the objective's stage says so - `demonstrated` or
  `review_due`, from `tutoring.evaluate_mastery`, which is a reducer over
  recorded checks and attempts.
- Never open a gate because the story reached that point. Story position is not
  evidence; the check is.
- Never keep a gate shut because a student read it quickly. Reading is not
  demonstrating.
- Say which objective the gate was waiting for. A gate the student cannot
  connect to a real objective reads as arbitrary and gets resented.

## The reward is evidence-based

`personas.evaluate_achievements` holds five achievement rules, each with a
declared evidence requirement and a revocation path. Two are directly relevant
here:

- `first-explain-back` - «Объяснил своими словами» - requires `explain_pass`:
  a credited explain-back check from a real attempt.
- `revision-learned` - «Учёл замечание» - requires `revision_made` **and**
  `revision_explained`: a fix and a stated reason, across two linked attempts.

`personas.NEVER_AWARD_FOR` is the list of things a badge must never be awarded
for, and it is worth reading before designing any reward:

```
количество сообщений
длительность занятия
число коммитов
помощь без проверки понимания
принятие PR любой ценой
ежедневная серия входов
публичность сдачи
```

**No XP for time spent.** A quest that grants points for minutes on a page
teaches attendance, not understanding, and it is on that list. `xp` in the
schema is a story number; the badge is the evidence-based part, and the badge
rules live in one place on purpose - a second, weaker rule inside the narrative
would be a way around them.

## The contract still decides

- The narrative never substitutes for the accepted contract when deciding what
  help is allowed. A quest does not reclassify a graded assignment:

```bash
python scripts/cli.py policy-check --course <slug> --assignment <id> --level EXAMPLE
```

- A narrative cannot override the consent gate, the hard refusal list, or the
  assistance ceiling. «In the story, the mentor writes the code for you» is a
  refusal with a hat on.
- Named comparison is refused by `leaderboards` (`require_opted_in`,
  `is_opted_in`); a narrative's scoreboard may not become a ranking of names
  without opt-in, and never a `fastest_learner` board that turns a learning
  frame into a race.

## Limitations

- The narrative is a **wrapper**. It adds motivation and structure; it does not
  add course content, and a quest cannot teach what the material does not.
- Characters are text. Nothing here makes a character interactive beyond what
  the agent writes in a turn, and there is no engine, no state machine, no
  branching runtime behind them.
- Unlock gating depends on the progress record being accurate. A gate that
  opens wrongly reflects a wrong record, not a wrong narrative.
- `evaluate_achievements` checks the declared evidence keys exist. It cannot
  verify the student actually understands; that is what the check behind the
  key was for.
- `xp` is unvalidated against anything external. Treat every number in the
  story as story, and put the real claim in the badge.

## References

- `schemas/v2/narrative.schema.json` - premise, quests, `objectives`, `characters`
- `personas.py` - `ACHIEVEMENT_RULES`, `NEVER_AWARD_FOR`, `evaluate_achievements`
- `leaderboards.py` - `evaluate_badges`, `require_opted_in`, `is_opted_in`
- `tutoring.py` - `evaluate_mastery`, the reducer that opens a gate
- `facilitating-team-challenges` - the quest as a group activity

## Verify or update the progress record

1. Confirm the level before framing the material as a quest; a story the student
   cannot follow is a second obstacle.
2. Respect the recorded delivery preference, and the assistance ceiling, inside
   the frame as everywhere.
3. Record: the quest-to-objective mapping, which gates opened and on what
   objective evidence, and which badges were earned with their evidence keys.
4. Never record XP, story position, or time spent as a demonstrated objective.
   The record holds objective stages and evidence.
5. Deliver the premise, quests, and every character line in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when
`narrative.schema.json`, the achievement rules, or the opt-in rules change.
Added this revision: the quest-to-objective requirement, the fiction-never-
authority rule, the evidence-gated unlocks, the `NEVER_AWARD_FOR` list as the
no-XP-for-time rule, and the contract's precedence.
