# Compound Engineering Integration Design for botai

## Executive Summary

This document proposes integrating **Compound Engineering (CE)** patterns from `everyinc/compound-engineering-plugin` into the **botai education co-learner harness** to create a self-improving, evidence-based co-learning system where each session makes the next one better through systematic knowledge compounding.

## Current State Analysis

### botai Strengths
- **Strong pedagogical policy** (AGENTS.md) with enforced constraints
- **v2 Core** with JSON Schema contracts, deterministic verification
- **18 teaching skills** covering full learning lifecycle
- **Cross-platform CLI** (`scripts/cli.py`) with `make` interface
- **Course contracts** accepted by humans before governing help
- **Evidence-backed progress** with transfer-check and blockers

### botai Gaps CE Can Fill
| Gap | CE Pattern | Impact |
|-----|------------|--------|
| No systematic learning capture | `ce-compound` | Sessions don't compound knowledge |
| Ad-hoc debugging | `ce-debug` | Bug fixes lack root-cause documentation |
| No pre-implementation planning | `ce-brainstorm` + `ce-plan` | Features lack structured requirements |
| No multi-agent review | `ce-code-review` | Quality relies on single-agent judgment |
| No automated QA dogfooding | `ce-dogfood` | UX regressions caught late |
| No session handoff protocol | `ce-handoff` | Context lost between sessions |
| No autonomous pipeline | `lfg` | End-to-end work requires manual orchestration |

## Proposed Architecture: CE-Enhanced botai Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│                    COMPOUND ENGINEERING LOOP                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │ CE-BRAINSTORM│───▶│  CE-PLAN     │───▶│  CE-WORK     │          │
│  │ Requirements │    │  Technical   │    │  Execute &   │          │
│  │  Exploration │    │  Planning    │    │  Verify      │          │
│  └──────────────┘    └──────────────┘    └──────┬───────┘          │
│                                                  │                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────▼───────┐          │
│  │  CE-DEBUG    │◀───│  CE-SIMPLIFY │◀───│ CE-CODE-REVIEW│          │
│  │  Root Cause  │    │  Refactor    │    │  Multi-Agent │          │
│  └──────────────┘    └──────────────┘    └──────┬───────┘          │
│                                                  │                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────▼───────┐          │
│  │  CE-HANDOFF  │◀───│ CE-COMPOUND  │◀───│  CE-DOGFOOD  │          │
│  │  Session     │    │  Capture     │    │  Browser QA  │          │
│  │  Continuity  │    │  Learning    │    │  Diff-Scoped │          │
│  └──────────────┘    └──────────────┘    └──────────────┘          │
│                                                                     │
│  ▲                                                                    │
│  │    ┌─────────────────────────────────────────┐                   │
│  └────│        AUTONOMOUS PIPELINE (lfg)        │                   │
│       └─────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Detailed Skill Integration Design

### 1. CE-SETUP: Project Bootstrap
**Purpose**: Initialize CE configuration in botai project

```yaml
# .compound-engineering/config.yaml
docs_root: docs
solutions_root: docs/solutions
plans_root: docs/plans
dogfood_reports_root: docs/dogfood-reports
explainers_root: docs/explainers
```

**Integration**: Add `make ce-setup` target that runs `ce-setup` skill, creating config and validating tooling.

### 2. CE-BRAINSTORM: Feature Exploration
**Purpose**: Transform vague co-learning improvement ideas into requirements-only unified plans

**Use Cases for botai**:
- "How should we handle multi-student cohort learning?"
- "What's the best way to add spaced repetition to progress tracking?"
- "Explore adaptive difficulty adjustment for assignments"

**Output**: `<root>/plans/YYYY-MM-DD-HHMM-feature-<topic>-plan.md` with:
- Goal Capsule (what, why, success criteria)
- Product Contract (scope, constraints, risks)
- Ready for Planning Check

### 3. CE-PLAN: Implementation Planning
**Purpose**: Convert requirements into execution-ready technical plans

**Input**: CE-BRAINSTORM output or direct feature description
**Output**: Durable plan under `<root>/plans/` with:
- Implementation units (U1, U2, U3...)
- Technical decisions with `session-settled:` markers
- Verification strategy per unit
- Blast radius analysis

**botai-specific additions**:
- Harness impact assessment (which skills affected)
- Schema migration considerations
- Cross-platform compatibility check (Windows/Linux/macOS)

### 4. CE-WORK: Plan Execution
**Purpose**: Execute plans with local verification, return structured results

**Integration with botai CLI**:
```python
# scripts/ce_work.py - wrapper for CE-WORK skill
def execute_plan(plan_path: str, mode: str = "return-to-caller"):
    """Execute plan using CE-WORK patterns"""
    # - Resolve workspace (feature branch from default)
    # - Parse plan into units
    # - Execute each unit with verification
    # - Return structured result for caller
```

**Return-to-Caller Mode**: Returns implementation result without shipping - caller (e.g., `lfg`) owns PR creation, CI watching.

### 5. CE-SIMPLIFY-CODE: Pre-Review Refactoring
**Purpose**: Improve clarity, reuse, quality, efficiency while preserving behavior

**Reviewers** (dispatched in parallel):
| Reviewer | Focus | botai Relevance |
|----------|-------|-----------------|
| `code-reuse-reviewer` | DRY, abstraction opportunities | Harness shared utilities |
| `code-quality-reviewer` | Readability, explicitness | CLI commands, core modules |
| `efficiency-reviewer` | Performance, complexity | Corpus search, RAG retrieval |
| `test-value-reviewer` | Test utility, coverage | Pedagogical scenario tests |
| `comment-reviewer` | Comment value, constraint encoding | Policy enforcement comments |

**Pipeline Mode**: Runs automatically in `lfg`, fixes applied without prompts.

### 6. CE-CODE-REVIEW: Multi-Agent Review
**Purpose**: Report-only multi-agent review against plan before merging

**Review Personas** (from botai's AGENTS.md policy):
1. **Pedagogical Correctness** - Does help respect Socratic/assistance ladder?
2. **Policy Compliance** - Graded vs practice enforcement, consent gates
3. **Schema Validity** - Contracts validate, no remote refs
4. **Path Safety** - No traversal, containment preserved
5. **Determinism** - Offline scenarios pass, no model calls in verification
6. **Cross-Platform** - Windows/Linux/macOS compatibility

**Output**: Findings with severity, file:line references, suggested fixes
**Completion Gate**: `lfg` requires actual `ce-code-review` receipt before shipping.

### 7. CE-DEBUG: Systematic Bug Fixing
**Purpose**: Root-cause diagnosis with test-first fixes

**Phases**: Triage → Investigate → Root Cause → Fix → Handoff

**botai-specific debug targets**:
- Schema validation failures
- Path traversal vulnerabilities
- Progress import corruption
- Corpus acquisition failures
- Consent gate bypasses

**Pipeline Mode**: Conservative defaults - fix convergent bugs, defer divergent ones.

### 8. CE-COMPOUND: Knowledge Capture
**Purpose**: Document solved problems as durable repo learnings

**Trigger Conditions** (counterfactual test):
> "If this learning disappeared, would a future engineer repeat the mistake or redo substantial investigation?"

**Qualifying learnings for botai**:
- Schema migration edge cases (v1→v2 progress import)
- Windows path handling gotchas
- OCA scanner integration patterns
- Consent gate state machine corner cases
- Cross-platform CLI escaping issues

**Output**: `<root>/solutions/<category>/<topic>.md` with:
- YAML frontmatter (module, tags, problem_type)
- Problem, Root Cause, Solution, Prevention
- Vocabulary capture for CONCEPTS.md

### 9. CE-DOGFOOD: Diff-Scoped Browser QA
**Purpose**: Hands-off browser QA of active branch changes

**Prerequisites**: `agent-browser` CLI installed

**botai Dogfood Scenarios**:
| Scenario | Persona | Flow |
|----------|---------|------|
| New student onboarding | `novice` | `make setup` → `make new-course` → consent gate → first session |
| Returning student | `intermediate` | `make progress` → `make session-start` → transfer check |
| Teacher review | `instructor` | `make review` → rubric feedback → progress summary |
| Course author | `expert` | `make course-add` → `make course-accept` → corpus acquisition |
| Open source contributor | `contributor` | `make contribute-start` → draft → rehearse → publish |

**Auto-fix scope**: Small, well-understood, low-risk only (typo, missing import, CSS fix)
**Escalation**: Architectural/UX decisions → report's "Decisions for a human"

### 10. CE-HANDOFF: Session Continuity
**Purpose**: Preserve context for fresh agent to continue work

**botai Handoff Content**:
- Active course, learner, session state
- Current objective, progress stage
- Blockers with evidence
- Pending consent decisions
- Next concrete action

**Storage**: Managed temp (`/tmp/ce-handoff/`) + optional persistent (`docs/handoffs/`)

**Resume Protocol**: Fresh agent reads handoff, orients, presents numbered continuation choices.

### 11. CE-POV: External Adoption Judgments
**Purpose**: Decide on external tools/libraries/approaches

**botai Use Cases**:
- "Should we adopt LangChain for RAG?" → `ce-pov` verdict
- "Is this OCA fork worth integrating?" → grounded comparison
- "Compare spaced repetition algorithms" → approach set judgment

**Oracle Panel**: Can consult other models for independent opinions.

### 12. LFG: Autonomous Pipeline
**Purpose**: Hands-off end-to-end from idea to open PR

**botai LFG Invocation**:
```bash
# After ce-brainstorm creates requirements plan
/lfg "Add spaced repetition scheduling to progress tracking"
```

**LFG Pipeline for botai**:
1. **Work Source** - Implementation-ready plan from `ce-plan`
2. **Implement** - `ce-work mode:return-to-caller <plan>`
3. **Simplify** - `ce-simplify-code mode:pipeline`
4. **Review** - `ce-code-review mode:agent plan:<plan>`
5. **Apply Fixes** - Commit review findings
6. **Compound** - `ce-compound mode:non-interactive` if qualifying
7. **Browser QA** - `ce-test-browser mode:pipeline`
8. **Commit & Push** - `ce-commit-push-pr mode:pipeline branding:on`
9. **CI Watch** - `ce-babysit-pr mode:pipeline <pr-url>`
10. **DONE** - Output `<promise>DONE</promise>`

## Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
| Task | Skill | Deliverable |
|------|-------|-------------|
| Add CE config | `ce-setup` | `.compound-engineering/config.yaml` |
| Create solutions/plans dirs | - | `docs/solutions/`, `docs/plans/` |
| Add `make ce-setup` | - | Makefile target |
| Document CE skills in AGENTS.md | - | Updated policy |

### Phase 2: Core Loop (Week 3-4)
| Task | Skill | Deliverable |
|------|-------|-------------|
| Implement `ce-brainstorm` wrapper | `ce-brainstorm` | `scripts/ce_brainstorm.py` |
| Implement `ce-plan` wrapper | `ce-plan` | `scripts/ce_plan.py` |
| Implement `ce-work` wrapper | `ce-work` | `scripts/ce_work.py` |
| Add `make ce-brainstorm`, `make ce-plan`, `make ce-work` | - | Makefile targets |

### Phase 3: Quality Gates (Week 5-6)
| Task | Skill | Deliverable |
|------|-------|-------------|
| Implement `ce-simplify-code` | `ce-simplify-code` | `scripts/ce_simplify.py` |
| Implement `ce-code-review` | `ce-code-review` | `scripts/ce_code_review.py` |
| Define botai review personas | - | `references/personas/*.md` |
| Add review receipt gate | - | CI check for review completion |

### Phase 4: Reliability (Week 7-8)
| Task | Skill | Deliverable |
|------|-------|-------------|
| Implement `ce-debug` | `ce-debug` | `scripts/ce_debug.py` |
| Implement `ce-compound` | `ce-compound` | `scripts/ce_compound.py` |
| Create first learning docs | - | `docs/solutions/...` |
| Add `make ce-debug`, `make ce-compound` | - | Makefile targets |

### Phase 5: Dogfood & Handoff (Week 9-10)
| Task | Skill | Deliverable |
|------|-------|-------------|
| Install `agent-browser` | - | Dev dependency |
| Implement `ce-dogfood` | `ce-dogfood` | `scripts/ce_dogfood.py` |
| Define botai personas | - | `docs/personas/*.md` |
| Implement `ce-handoff` | `ce-handoff` | `scripts/ce_handoff.py` |
| Add `make ce-dogfood`, `make ce-handoff` | - | Makefile targets |

### Phase 6: Autonomous Pipeline (Week 11-12)
| Task | Skill | Deliverable |
|------|-------|-------------|
| Implement `lfg` wrapper | `lfg` | `scripts/lfg.py` |
| Add `make lfg` | - | Makefile target |
| Integration testing | - | Full pipeline run |
| Documentation | - | `docs/ce-integration.md` |

## Configuration Files

### `.compound-engineering/config.yaml`
```yaml
docs_root: docs
solutions_root: docs/solutions
plans_root: docs/plans
dogfood_reports_root: docs/dogfood-reports
explainers_root: docs/explainers
handoffs_root: docs/handoffs

# CE skill configuration
ce_simplify_code:
  proof_test: "tests/test_simplify_proof.py"
  model_tier: "balanced"

ce_code_review:
  personas:
    - pedagogical-correctness
    - policy-compliance
    - schema-validity
    - path-safety
    - determinism
    - cross-platform

ce_dogfood:
  personas:
    - novice
    - intermediate
    - instructor
    - expert
    - contributor
  dev_server: "python scripts/cli.py serve"

ce_compound:
  auto_capture: true
  interactive_depth: "full"

lfg:
  auto_babysit: true
  branding: true
```

### `docs/personas/novice.md`
```markdown
---
persona: novice
level: beginner
technical_background: none
preferred_language: ru
feedback_style: encouraging
help_preference: hints-then-solution
---

# Novice Learner Persona

**Context**: First-time programming student, no terminal experience
**Goals**: Learn basics, build confidence, avoid frustration
**Pain points**: Jargon, abstract concepts, silent failures
**Success indicators**: Completes first session, understands progress tracking
```

### `docs/personas/instructor.md`
```markdown
---
persona: instructor
role: teacher
technical_background: expert
preferred_language: ru
feedback_style: structured
help_preference: prefer-ask
---

# Instructor Persona

**Context**: Teaching assistant reviewing student submissions
**Goals**: Fair assessment, actionable feedback, progress visibility
**Pain points**: Inconsistent rubrics, hidden student struggle
**Success indicators**: Clear blockers identified, evidence-backed feedback
```

## Makefile Integration

```makefile
# Compound Engineering targets
.PHONY: ce-setup ce-brainstorm ce-plan ce-work ce-simplify-code \
        ce-code-review ce-debug ce-compound ce-dogfood ce-handoff \
        ce-pov lfg

ce-setup:
	@python3 scripts/ce_setup.py

ce-brainstorm:
	@python3 scripts/ce_brainstorm.py $(if $(TOPIC),--topic "$(TOPIC)",)

ce-plan:
	@python3 scripts/ce_plan.py $(if $(PLAN),--plan "$(PLAN)",)

ce-work:
	@python3 scripts/ce_work.py $(if $(PLAN),--plan "$(PLAN)",)

ce-simplify-code:
	@python3 scripts/ce_simplify.py $(if $(SCOPE),--scope "$(SCOPE)",)

ce-code-review:
	@python3 scripts/ce_code_review.py $(if $(PLAN),--plan "$(PLAN)",)

ce-debug:
	@python3 scripts/ce_debug.py $(if $(ISSUE),--issue "$(ISSUE)",)

ce-compound:
	@python3 scripts/ce_compound.py $(if $(CONTEXT),--context "$(CONTEXT)",)

ce-dogfood:
	@python3 scripts/ce_dogfood.py $(if $(TARGET),--target "$(TARGET)",)

ce-handoff:
	@python3 scripts/ce_handoff.py $(if $(FOCUS),--focus "$(FOCUS)",)

ce-pov:
	@python3 scripts/ce_pov.py $(if $(QUESTION),--question "$(QUESTION)",)

lfg:
	@python3 scripts/lfg.py $(if $(REQUEST),--request "$(REQUEST)",)

# Help text additions
help:
	@echo "Compound Engineering:"
	@echo "  make ce-setup                 Initialize CE config and validate tooling"
	@echo "  make ce-brainstorm TOPIC=...  Explore feature idea into requirements plan"
	@echo "  make ce-plan [PLAN=...]       Create technical plan from requirements"
	@echo "  make ce-work PLAN=...         Execute plan with local verification"
	@echo "  make ce-simplify-code         Refactor current branch changes"
	@echo "  make ce-code-review PLAN=...  Multi-agent review against plan"
	@echo "  make ce-debug ISSUE=...       Systematic bug diagnosis and fix"
	@echo "  make ce-compound              Capture durable learning from session"
	@echo "  make ce-dogfood [TARGET=...]  Diff-scoped browser QA"
	@echo "  make ce-handoff [FOCUS=...]   Create/resume session handoff"
	@echo "  make ce-pov QUESTION=...      Judge external adoption question"
	@echo "  make lfg REQUEST=...          Autonomous pipeline to open PR"
```

## Skill Wrapper Architecture

Each CE skill gets a thin Python wrapper in `scripts/`:

```python
# scripts/ce_brainstorm.py
#!/usr/bin/env python3
"""CE-BRAINSTORM wrapper for botai."""

import subprocess
import sys
from pathlib import Path

def main():
    # Resolve project root
    root = Path(__file__).resolve().parent.parent
    
    # Build skill invocation
    args = ["opencode", "skill", "ce-brainstorm"]
    if len(sys.argv) > 1:
        args.extend(sys.argv[1:])
    
    # Execute in project context
    result = subprocess.run(args, cwd=root)
    return result.returncode

if __name__ == "__main__":
    sys.exit(main())
```

**Benefits**:
- Cross-platform (works on Windows without make)
- Consistent argument parsing
- Integrates with botai's existing CLI patterns
- Can add botai-specific pre/post processing

## Artifact Structure

```
botai/
├── .compound-engineering/
│   ├── config.yaml
│   └── config.example.yaml
├── docs/
│   ├── solutions/              # ce-compound learnings
│   │   ├── developer-experience/
│   │   ├── integrations/
│   │   ├── workflow/
│   │   └── skill-design/
│   ├── plans/                  # ce-brainstorm/ce-plan artifacts
│   │   ├── 2026-01-15-1430-feature-spaced-repetition-plan.md
│   │   └── ...
│   ├── dogfood-reports/        # ce-dogfood reports
│   │   ├── 2026-01-15-feat-spaced-repetition-dogfood.md
│   │   └── ...
│   ├── explainers/             # ce-explain/ce-pov teaching artifacts
│   ├── handoffs/               # ce-handoff session continuity
│   └── personas/               # ce-dogfood personas
│       ├── novice.md
│       ├── intermediate.md
│       ├── instructor.md
│       ├── expert.md
│       └── contributor.md
├── scripts/
│   ├── ce_setup.py
│   ├── ce_brainstorm.py
│   ├── ce_plan.py
│   ├── ce_work.py
│   ├── ce_simplify.py
│   ├── ce_code_review.py
│   ├── ce_debug.py
│   ├── ce_compound.py
│   ├── ce_dogfood.py
│   ├── ce_handoff.py
│   ├── ce_pov.py
│   └── lfg.py
└── references/                 # CE skill reference files
    ├── personas/
    │   ├── code-reuse-reviewer.md
    │   ├── code-quality-reviewer.md
│   │   ├── efficiency-reviewer.md
    │   ├── test-value-reviewer.md
    │   └── comment-reviewer.md
    └── ...
```

## Integration with Existing botai Systems

### AGENTS.md Policy Updates

Add to AGENTS.md:
```markdown
## Compound Engineering Loop

The agent MUST follow the CE loop for all non-trivial changes:

1. **Brainstorm** (`ce-brainstorm`) - Explore requirements before planning
2. **Plan** (`ce-plan`) - Create implementation-ready technical plan
3. **Work** (`ce-work`) - Execute with local verification
4. **Simplify** (`ce-simplify-code`) - Refactor before review
5. **Review** (`ce-code-review`) - Multi-agent review against plan
6. **Compound** (`ce-compound`) - Capture durable learning
7. **Dogfood** (`ce-dogfood`) - Diff-scoped browser QA
8. **Ship** (`ce-commit-push-pr` + `ce-babysit-pr`) - Commit, push, watch CI

**Autonomous mode**: Use `lfg` for end-to-end execution when explicitly requested.

**Session continuity**: Use `ce-handoff` to preserve context between sessions.

**External decisions**: Use `ce-pov` for tool/library adoption judgments.

**Bugs**: Use `ce-debug` for systematic root-cause analysis.
```

### Schema Extensions

Add CE-related schemas:
```json
// schemas/v2/ce_plan.schema.json
{
  "type": "object",
  "properties": {
    "plan_id": {"type": "string"},
    "units": {"type": "array", "items": {"$ref": "#/$defs/unit"}},
    "session_settled": {"type": "array", "items": {"type": "string"}}
  }
}

// schemas/v2/ce_learning.schema.json
{
  "type": "object",
  "properties": {
    "learning_id": {"type": "string"},
    "category": {"type": "string"},
    "problem_type": {"type": "string"},
    "counterfactual_passes": {"type": "boolean"}
  }
}
```

### Progress Tracking Enhancement

Add CE session metadata to progress records:
```json
// progress/<learner>.md or .botai/state.sqlite3
{
  "ce_session": {
    "plan_id": "2026-01-15-1430-feature-xyz-plan",
    "units_completed": ["U1", "U2"],
    "review_receipt": "ce-code-review-20260115-abc123",
    "compound_learning": "docs/solutions/.../learning.md",
    "dogfood_report": "docs/dogfood-reports/...-dogfood.md",
    "handoff_path": "docs/handoffs/...-handoff.md"
  }
}
```

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| CE skills not available on all hosts | Fallback to native botai workflows; document host requirements |
| Learning curve for team | Start with `ce-setup` + `ce-brainstorm` only; add skills incrementally |
| Overhead for small changes | Use Direct/Chat brief output contracts; skip file creation for trivial work |
| Windows compatibility | All wrappers use `python scripts/ce_*.py`; no bash dependencies |
| Schema drift | `ce-compound-refresh` skill audits learnings against current code |
| PR bloat from learnings | `ce-compound` only writes when counterfactual passes; strict bar |

## Success Metrics

| Metric | Baseline | Target (3 months) |
|--------|----------|-------------------|
| Session context retention | ~30% | >80% (via handoffs) |
| Bug recurrence rate | Unknown | <10% (via compound) |
| PR review findings | Manual | 6-persona automated |
| Dogfood coverage | 0% | 5 personas × all flows |
| Knowledge rediscovery | High | Near zero (solutions/) |
| Time to ship feature | Variable | Predictable (plan → PR) |

## Appendix: Key CE Principles Applied to botai

### 1. "Each unit of work should make subsequent units easier"
- Every co-learning session captures blockers, decisions, patterns
- Next session starts with `ce-handoff` + relevant `docs/solutions/`

### 2. "80% planning/review, 20% execution"
- `ce-brainstorm` + `ce-plan` before any implementation
- `ce-code-review` + `ce-simplify-code` before shipping

### 3. "Grounding is not optional"
- `ce-pov` requires project evidence for external decisions
- `ce-explain` grounds behavior in source evidence
- `ce-debug` requires causal chain with file:line evidence

### 4. "Prescribe mechanism only where owned"
- Delegating skills state conditions, not procedures
- `ce-work` owns execution; `lfg` owns pipeline orchestration

### 5. "Bring the block you touch up to standard"
- `ce-simplify-code` improves touched code to current standard
- `ce-compound-refresh` audits stale learnings

### 6. "Decision cost raises content floor, not length ceiling"
- PR descriptions explain what's now different, not what was edited
- `ce-compound` captures only non-obvious, durable reasoning

## Next Steps

1. **Approve this design** - Review with stakeholders
2. **Install CE plugin** - `opencode plugin add compound-engineering@...`
3. **Run `ce-setup`** - Initialize configuration
4. **Pilot on real feature** - Use `ce-brainstorm` → `ce-plan` → `ce-work` for next feature
5. **Iterate and compound** - Capture learnings, improve process

---

*This design follows the Compound Engineering philosophy: systematic knowledge compounding so each co-learning session makes the next one better.*