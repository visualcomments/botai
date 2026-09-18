# -*- coding: utf-8 -*-
"""botai core: the parts of the harness that make rules checkable.

The harness began as policy text (`AGENTS.md`) plus skills, with Python doing
installation and updates. That is enough for a cooperative model and not enough
for the guarantees the design promises: a graded task must never receive a
solution, an environment plan must not run without a human approval, and a
progress record must not claim mastery it cannot evidence.

This package holds the small amount of machinery that makes those statements
true of the tools rather than of the prompt:

* `paths`   — resolve and contain every path the tools touch;
* `schemas` — validate every contract against a local JSON Schema;
* `store`   — the SQLite record with transactions, evidence and idempotency;
* `legacy`  — import a v1 Markdown progress file without inventing facts;
* `course`  — the accepted contract: binding, objective graph, assessment lookup;
* `policy`  — allow/deny/needs_confirmation for help, effects and reading;
* `tutoring`— the teaching cycle: state machine, mastery, review, response checks;
* `session` — the cycle bound to the store, one command per transition;
* `progress`— the Markdown view, rendered from the store and never authoritative;
* `corpus`  — declarative acquisition: URL guard, safe unpack, per-file verify;
* `retrieval`— scoped search and quote verification;
* `environment`— plan an install: closed step kinds, plan hash, human screen;
* `actions` — the restricted executor: no inherited secrets, bounded output,
  whole process tree killed on timeout;
* `operation`— the lifecycle: approval bound to one plan hash, spent once, and
  a lease that stops two workers running the same install;
* `contribution`— the first-contribution flow: read-only Git, checks bound to a
  diff hash, a self-report kept distinct from an observation, and no path to
  commit, push or open a pull request;
* `personas`— presentation styles that cannot carry authority, and personal
  badges derived from evidence and never part of a course grade;
* `exports` — the learner's own data: selective export with a preview, a
  checksum that detects damage but not identity, and planned deletion;
* `teacher` — imported packets as untrusted data, a triage queue, and
  aggregation that states its denominator instead of guessing a cohort size;
* `mcp_handlers`— the narrow tool surface: one handler per catalogued tool, with
  no tool that runs a command, edits a file, or grants an approval;
* `adapters` — host profiles: a certified restriction, or an honest
  `HOST_UNVERIFIED` when the final configuration cannot be proven to restrict;
* `claims` — the absolutist-claim filter: a superlative about a person is
  refused before it reaches a record, because a claim needs evidence;
* `skills` — the incoming-skill normaliser: self-promoting frontmatter is
  stripped and provenance recorded, so an imported skill cannot grant itself
  standing the policy did not give it.

Three mechanisms here are adapted from HKUDS/DeepTutor (Apache-2.0): `claims.py`
from `services/memory/consolidator/guards.py`, the session step budget in
`tutoring.py` from its per-loop `ToolBudgets`, and `skills.py` from its "import
safety gate". See `docs/deeptutor-comparison.md` for what was taken and what was
deliberately left.

Nothing here calls a language model, opens the network, or executes course code.
"""

from __future__ import annotations

__all__ = ["paths", "schemas", "store", "legacy", "course", "policy",
           "tutoring", "session", "progress", "corpus", "retrieval",
           "environment", "actions", "operation", "contribution", "personas",
           "exports", "teacher", "mcp_handlers", "adapters", "claims", "skills"]

SCHEMA_VERSION = 2
