# -*- coding: utf-8 -*-
"""Deterministic context budget accounting for the agent harness.

This module does not inspect chat transcripts or call a model. It accounts for
repository-local context artifacts using character counts as a stable offline
proxy for tokens, assigns them to progressive-disclosure tiers, and emits
reasons when the startup context should be reduced.
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_BUDGET = 12000
DEFAULT_CAPS = {
    "metadata": 2500,
    "instructions": 5000,
    "resources": 4500,
}

ARTIFACTS = (
    ("metadata", "features/feature_list.json", "always"),
    ("metadata", "PROGRESS.md", "always"),
    ("metadata", ".botai/handoff.json", "always"),
    ("instructions", "AGENTS.md", "on_activation"),
    ("instructions", "docs/course-rules.md", "on_activation"),
    ("resources", "README.md", "on_demand"),
    ("resources", "SESSION_HANDOFF.md", "on_demand"),
)


class ContextError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_size(root, relative):
    path = (Path(root) / relative).resolve()
    root = Path(root).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ContextError("CONTEXT_PATH_OUTSIDE_ROOT", relative)
    if not path.is_file():
        return 0
    return path.stat().st_size


def build_context_status(root, *, budget=DEFAULT_BUDGET, include_resources=False):
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ContextError("CONTEXT_BUDGET_INVALID", "бюджет контекста должен быть положительным целым")
    caps = dict(DEFAULT_CAPS)
    artifacts = []
    totals = {tier: 0 for tier in caps}
    for tier, relative, disclosure in ARTIFACTS:
        included = disclosure != "on_demand" or include_resources
        size = _safe_size(root, relative)
        item = {
            "tier": tier,
            "path": relative,
            "disclosure": disclosure,
            "included": included,
            "characters": size if included else 0,
            "available_characters": size,
        }
        artifacts.append(item)
        if included:
            totals[tier] += size
    total = sum(totals.values())
    reasons = []
    for tier, value in totals.items():
        if value > caps[tier]:
            reasons.append("%s превышает локальный предел (%d > %d)" % (tier, value, caps[tier]))
    if total > budget:
        reasons.append("общий бюджет превышен (%d > %d)" % (total, budget))
    if not reasons:
        recommendation = "контекст в пределах бюджета"
    elif totals["resources"] > caps["resources"]:
        recommendation = "не загружать resources до активации конкретной задачи"
    else:
        recommendation = "сжать старый прогресс и загрузить только нужный раздел инструкций"
    return {
        "schema_version": SCHEMA_VERSION,
        "budget_characters": budget,
        "used_characters": total,
        "remaining_characters": budget - total,
        "within_budget": not reasons,
        "include_resources": bool(include_resources),
        "tiers": {
            tier: {
                "characters": totals[tier],
                "cap_characters": caps[tier],
                "within_budget": totals[tier] <= caps[tier],
            }
            for tier in caps
        },
        "artifacts": artifacts,
        "reasons": reasons,
        "recommendation_ru": recommendation,
    }
