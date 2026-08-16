# Borrowed Patterns

Changes in this repository that were ported from other agent-ecosystem
projects, with the source and the reason each was adopted. Every borrowed idea
was adapted to the education harness and its consent-gate policy; none of them
weaken the AGENTS.md guardrails.

## From oh-my-opencode-slim (MIT)

Source: https://github.com/alvinunreal/oh-my-opencode-slim

| Borrowed pattern | Where it landed | What it does here |
| --- | --- | --- |
| Per-agent `model` tier assignment in agent frontmatter | `.opencode/agent/*.md`, AGENTS.md "Model tiers for subagents" | Cost control across subagents: cheap extraction model for `mapper`, mid-tier generation for `reviewer`/`supplementer`/`contributor`, strongest model for `tutor`. The tier assignment is the part we keep; the concrete model IDs are per-installation overrides. |

## From compound-engineering-plugin (MIT)

Source: https://github.com/EveryInc/compound-engineering-plugin

| Borrowed pattern | Where it landed | What it does here |
| --- | --- | --- |
| Session-settled decisions (provenance-labelled constraints) | AGENTS.md "Session-settled decisions", `maintaining-course-progress`, `giving-feedback` | Consent-gate answers are recorded with a provenance annotation (`session-settled: user-approved` / `user-directed` / `assumed`) so downstream skills never re-ask and never contradict a settled student decision without evidence. |
| Structured learning store with YAML frontmatter | `docs/teaching-methods/` (per-method files with `method`/`category`/`owned_by`/`applies_when`/`verified`) | The flat method catalog became a searchable, validated store; new methods are added as files instead of appended to one long document. |
| Mechanical integrity guards (frontmatter, path safety, parity) | `scripts/check.py`, `make check` | Validates skill frontmatter (`verified:` dates), symlink-farm parity across `.claude/.cursor/.opencode` via the git index (works on Windows junctions), and path safety inside skill files. |
| Assistance-level escalation as an ordinal taxonomy | `docs/teaching-methods/scaffolding-and-assistance/assistance-level-tags.md` | Already present as HINT/EXAMPLE/SOLUTION (inherited from SECS); the store now gives it the same searchable treatment as every other method. |

## What was deliberately NOT borrowed

- CE's shipping/PR automation skills (`lfg`, `ce-babysit-pr`, `ce-dogfood`,
  browser-test runners) contradict the "never do the student's work" policy and
  are out of scope for an education harness.
- OMO's full autonomous `deepwork` delegation contradicts the consent gate;
  background delegation is only safe here for well-scoped supplement material.

## License notes

Both sources are MIT-licensed; their ideas are patterns and procedures, not
copied text, and were re-authored in botai's own style. botai remains GNU GPL
v3 (see [LICENSE](../LICENSE)) and a derivative of SECS
(https://github.com/EvilFreelancer/secs, Apache-2.0), as documented in
[../README.md](../README.md).
