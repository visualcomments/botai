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
* `policy`  — allow/deny/needs_confirmation for help, effects and reading.

Nothing here calls a language model, opens the network, or executes course code.
"""

from __future__ import annotations

__all__ = ["paths", "schemas", "store", "legacy", "course", "policy"]

SCHEMA_VERSION = 2
