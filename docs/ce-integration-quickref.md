# CE Integration Quick Reference for botai

## One-Liner: What This Gives You

**Every co-learning session systematically captures its hard-won knowledge so the next session starts smarter.**

## The Loop in Practice

```
Student asks → Agent checks handoff → Reads relevant learnings → 
Session runs → Blocker found → ce-debug fixes it → 
ce-compound captures learning → ce-handoff saves context → 
Next session: already knows the fix
```

## New Make Commands

```bash
# One-time setup
make ce-setup

# Feature development
make ce-brainstorm TOPIC="add spaced repetition"
make ce-plan
make ce-work PLAN=docs/plans/...-plan.md
make ce-simplify-code
make ce-code-review PLAN=...
make ce-compound
make ce-dogfood
make ce-commit-push-pr

# Bug fixing
make ce-debug ISSUE="corpus acquisition fails on Windows"

# Session continuity
make ce-handoff                    # Save context at end of session
make ce-handoff resume             # Resume next session

# External decisions
make ce-pov QUESTION="Should we use LangChain for RAG?"

# Autonomous (when you just want it done)
make lfg REQUEST="Add spaced repetition scheduling"
```

## Key Files Created

```
docs/
├── solutions/          # Durable learnings (ce-compound)
│   └── developer-experience/windows-path-escaping.md
├── plans/              # Requirements → Implementation plans
│   └── 2026-01-15-1430-feature-spaced-repetition-plan.md
├── dogfood-reports/    # Browser QA reports (ce-dogfood)
├── handoffs/           # Session continuity (ce-handoff)
├── personas/           # Dogfood test personas
└── compound-engineering-integration-design.md  # This design
```

## First Learning to Capture

After this PR merges, run:
```bash
make ce-compound CONTEXT="Added pre-co-learning deployment gate with user choice, OCA baseline, and 18-subagent preparation pipeline. Key insight: explicit user consent before any subagent modifies course materials is non-negotiable for pedagogical integrity."
```

This creates `docs/solutions/.../deployment-gate-consent.md` so future agents know:
- **Why** the three-mode choice exists (as-is/prepare/copilot)
- **Why** OCA runs before LLM subagents (deterministic baseline)
- **Why** human approval is required (policy: never modify student material without consent)

## Minimal Viable Adoption

Start with just these three:
1. `make ce-setup` — initialize
2. `make ce-handoff` — save session context
3. `make ce-compound` — capture one learning per session

Everything else layers on top when needed.

## Why This Matters for botai

| Without CE | With CE |
|------------|---------|
| Each session rediscover Windows path bugs | `docs/solutions/.../windows-paths.md` prevents it |
| Feature scope drifts during implementation | `ce-plan` locks scope with `session-settled:` decisions |
| Review finds same issues repeatedly | 6 automated personas catch them pre-PR |
| Context lost between sessions | `ce-handoff` preserves objective, blockers, next action |
| External tool choices are ad-hoc | `ce-pov` grounds decisions in project evidence |
| Bug fixes lack root-cause docs | `ce-debug` requires causal chain with file:line |
| UX regressions caught by students | `ce-dogfood` drives 5 personas through every flow |

## The Compound Effect

> **Run 1**: Agent encounters Windows path bug, fixes it, captures learning
> **Run 2 (18 days later)**: Different agent, different feature — `ce-plan` reads the learning, carries its constraint into new plan
> **Run 3**: Bug never recurs; plan already handles it

This is the entire point: **knowledge compounds instead of being rediscovered**.