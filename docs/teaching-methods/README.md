# Teaching Methods for the Co-learner Agent

A structured store of teaching practices the botai harness routes through its
Skills. Each method is one file with YAML frontmatter (`method`, `category`,
`owned_by`, `applies_when`, `verified`) so the store stays searchable and
`make check` validates every entry. These are established methods from teaching
practice; the Skills in this repo encode them into repeatable workflows.

New methods are added as new files under the matching category. Reuse an
existing method's file rather than duplicating it; a `verified:` date change
means the method's procedure was re-checked, not that it is new.

## Index

### Questioning and dialogue

- [Socratic method](questioning-and-dialogue/socratic-method.md)
- [Think-aloud](questioning-and-dialogue/think-aloud.md)
- [Prediction before explanation](questioning-and-dialogue/prediction-before-explanation.md)

### Scaffolding and assistance

- [Scaffolded instruction (least-assistance-first)](scaffolding-and-assistance/scaffolded-instruction.md)
- [Assistance-level tags (HINT / EXAMPLE / SOLUTION)](scaffolding-and-assistance/assistance-level-tags.md)
- [Worked examples](scaffolding-and-assistance/worked-examples.md)
- [Problem decomposition](scaffolding-and-assistance/problem-decomposition.md)

### Assessment

- [Formative assessment](assessment/formative-assessment.md)
- [Pre-testing and placement](assessment/pre-testing-and-placement.md)
- [Error-spotting checks](assessment/error-spotting-checks.md)

### Practice and retention

- [Spaced practice](practice-and-retention/spaced-practice.md)
- [Deliberate practice with progressive difficulty](practice-and-retention/deliberate-practice.md)
- [Interleaving](practice-and-retention/interleaving.md)

### Feedback

- [Feedback sandwich with specificity](feedback/feedback-sandwich.md)
- [Feedback invariants](feedback/feedback-invariants.md)

### Supplements and materials

- [Gap-based supplementation](supplements-and-materials/gap-based-supplementation.md)
- [Material vetting](supplements-and-materials/material-vetting.md)

### Motivation and capacity

- [Pacing and capacity respect](motivation-and-capacity/pacing-and-capacity.md)
- [Mastery-based progression](motivation-and-capacity/mastery-based-progression.md)

## References

- The harness pattern (AGENTS.md, Makefile, skills, symlink farms) mirrors
  SECS: https://github.com/EvilFreelancer/secs
- The educational skill ecosystem is cataloged in `../course-agent-skills.md`
- Method-catalog structure follows SECS `docs/security-tools.md`
