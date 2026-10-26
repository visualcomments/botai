# Compound Engineering Integration for botai

## Quick Start

### One-line summary
> Every co-learning session systematically captures its hard-won knowledge so the next session starts smarter.

### 3-step beginning

1. **One-time setup**
   ```bash
   make ce-setup
   ```

2. **Start a feature**
   ```bash
   make ce-brainstorm TOPIC="add spaced repetition to progress"
   make ce-plan
   make ce-work PLAN=docs/plans/...-plan.md
   ```

3. **Apply learnings every session**
   ```bash
   make ce-handoff                    # Save context at end of session
   make ce-compound                  # Capture lessons learned
   ```

### What this gives you

| Area | What changes |
|------|--------------|
| **Session continuity** | Context saved across sessions via `ce-handoff` |
| **Knowledge compounding** | `ce-compound` writes durable learnings |
| **Multi-agent review** | 6 automated personas catch issues before PR |
| **Deterministic verification** | `ce-debug` root cause with file:line evidence |
| **Automated QA** | `ce-dogfood` diff-scoped browser testing |
| **Structured planning** | `ce-brainstorm` + `ce-plan` vs ad-hoc markdown |
| **External adoption** | `ce-pov` grounded tool selection |
| **Autonomous pipeline** | `lfg` end-to-end from idea to open PR |

## Integration Overview

### Core Loop (The Compound Engineering way)

```
Brainstorm → Plan → Work → Simplify → Review → Compound → Dogfood → Ship
```

### CE Skills mapped to botai needs

| CE Skill | botai application |
|----------|-------------------|
| **`ce-brainstorm`** | Explore co-learning feature ideas before planning |
| **`ce-plan`** | Convert requirements to structured plans with `session-settled:` decisions |
| **`ce-work`** | Execute plans with verification (Return-to-Caller mode) |
| **`ce-simplify-code`** | 5 parallel reviewers (reuse, quality, efficiency, tests, comments) |
| **`ce-code-review`** | 6 pedagogical personas (policy, schema, paths, determinism, cross-platform) |
| **`ce-debug`** | Root-cause diagnosis with causal chain file:line evidence |
| **`ce-compound`** | Capture only durable, non-obvious learnings (`counterfactual` test) |
| **`ce-dogfood`** | 5 personas (novice→contributor) diff-scoped browser testing |
| **`ce-handoff`** | Session continuity with objective, blockers, next action |
| **`ce-pov`** | Grounded judgments on external tools (e.g., "LangChain for RAG?") |
| **`lfg`** | Autonomous pipeline: brainstorm → PR with CI watched |

### Files Added

```
docs/
├── solutions/              # Durable learnings (ce-compound)
├── plans/                  # Requirements → Implementation plans
├── dogfood-reports/        # Browser QA reports (ce-dogfood)
├── handoffs/               # Session continuity (ce-handoff)
├── personas/               # Dogfood test personas
├── ce-integration-quickref.md  # Quick reference (this file)
└── compound-engineering-integration-design.md  # Full design
```

### Makefile targets added

```make
make ce-setup                    # Initialize CE config
make ce-brainstorm TOPIC=...     # Brainstorm feature ideas
make ce-plan                      # Create technical plan
make ce-work PLAN=...             # Execute plan
make ce-simplify-code             # Refactoring before review
make ce-code-review PLAN=...      # Multi-agent review
make ce-debug ISSUE=...           # Root cause analysis
make ce-compound                  # Capture learnings
make ce-dogfood TARGET=...        # Diff-scoped browser QA
make ce-handoff FOCUS=...          # Session continuity
make ce-pov QUESTION=...           # External adoption judgment
make lfg REQUEST=...              # Autonomous end-to-end
```

### Quick reference commands

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

## Why this matters for botai

| Without CE | With CE |
|------------|---------|
| Each session rediscover Windows path bugs | `docs/solutions/.../windows-paths.md` prevents |
| Feature scope drifts | `ce-plan` locks scope with `session-settled:` decisions |
| Review catches same issues repeatedly | 6 automated personas catch pre-PR |
| Context lost between sessions | `ce-handoff` preserves objective, blockers, next action |
| External tool choices ad-hoc | `ce-pov` grounds decisions in evidence |
| Bug fixes lack root-cause docs | `ce-debug` requires causal chain file:line |
| UX regressions caught by students | `ce-dogfood` drives 5 personas through all flows |

## Success story (18 days apart)

```
Day 1: Agent encounters Windows path bug, fixes it, `ce-compound` captures learning
Day 19: Different agent, different feature → `ce-plan` reads learning, carries constraint
Day 20: Bug never recurs; plan already handles it
```

> This is the entire point: knowledge compounds instead of being rediscovered.

---

## Links

- [README](/README.md) — Main botai documentation
- [CLAUDE.md](/CLAUDE.md) — Claude Code plugin instructions
- [AGENTS.md](/AGENTS.md) — Co-learning policy and procedures
- [docs/ce-integration-quickref.md](/docs/ce-integration-quickref.md) — Quick reference
- [docs/compound-engineering-integration-design.md](/docs/compound-engineering-integration-design.md) — Full integration design