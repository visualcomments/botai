"""CLI and generation driver for botai SelfSearch self-improvement loop.

Usage:
    python scripts/self_improve.py --generations 2 [--mock] [--lineage capability|adaptive|all]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from botai_core.self_improvement import (
    EpisodeEnvironment,
    EpisodeRecord,
    MutationDiff,
    SafetyGate,
    SafetyGateError,
    SelfSearchEngine,
    ToolAction,
)


def mock_improver(
    direction: str,
    previous_records: list[EpisodeRecord],
    sandbox: EpisodeEnvironment,
    engine: SelfSearchEngine,
    lineage: str,
    generation: int,
) -> tuple[list[str], list[ToolAction], list[MutationDiff], str]:
    """Demonstration improver executing realistic, verified skill additions."""
    reasoning = [
        f"Analyzing direction: {direction}",
        f"Loaded {len(previous_records)} previous episode trajectories as evidence.",
        "Identified need for enhanced course corpus diagnostic skill.",
    ]

    actions = [
        ToolAction(
            tool_name="read_evidence",
            arguments={"count": len(previous_records)},
            output=f"Extracted findings from {len(previous_records)} prior episodes.",
            success=True,
        )
    ]

    # Propose a non-destructive skill enhancement
    skill_rel_path = f".agents/skills/self-improved-{lineage}-g{generation}/SKILL.md"
    skill_content = f"""---
name: self-improved-{lineage}-g{generation}
description: Automatically generated assistant skill for {lineage} (gen {generation})
verified: 2026-10-13
---

# Self-Improved Skill ({lineage})

Generated via SelfSearch (arXiv:2609.37968) reward-free self-improvement loop.

## Objective
Provide guided diagnostic checkpoints for the {lineage} lineage.

## Instructions
1. Check student understanding before presenting material.
2. Maintain pedagogical ladder: HINT -> EXAMPLE -> SOLUTION (practice only).
3. Always respond in Russian per language-and-translation.
"""

    mutations = []
    try:
        diff = engine.apply_mutation(skill_rel_path, skill_content, sandbox)
        mutations.append(diff)
        actions.append(
            ToolAction(
                tool_name="apply_mutation",
                arguments={"path": skill_rel_path},
                output=f"Mutation drafted. Diff size: {len(diff.diff_text)} bytes.",
                success=True,
            )
        )
    except SafetyGateError as sge:
        actions.append(
            ToolAction(
                tool_name="apply_mutation",
                arguments={"path": skill_rel_path},
                output=f"Safety Gate rejected mutation: {sge}",
                success=False,
            )
        )

    summary = (
        f"Completed episode for {lineage} generation {generation}. "
        f"Proposed {len(mutations)} safe mutations."
    )
    return reasoning, actions, mutations, summary


def run_self_search(
    workspace_root: Path,
    generations: int = 1,
    lineage_filter: str = "all",
) -> None:
    engine = SelfSearchEngine(workspace_root)
    print(f"=== botai SelfSearch Engine (arXiv:2609.37968) ===")
    print(f"Workspace: {workspace_root}")
    print(f"Generations planned: {generations}")

    lineages = (
        ["pedagogical_capability", "adaptive_scaffolding"]
        if lineage_filter == "all"
        else [lineage_filter]
    )

    for gen in range(1, generations + 1):
        print(f"\n--- Generation {gen}/{generations} ---")
        for lin in lineages:
            print(f"Executing lineage: {lin}...")

            def improver_wrapper(d, prevs, sb):
                return mock_improver(d, prevs, sb, engine, lin, gen)

            record = engine.execute_episode(
                generation=gen,
                lineage=lin,
                improver_fn=improver_wrapper,
            )

            status = "PASSED" if record.verification_passed else "FAILED"
            print(
                f"  [{status}] Episode {record.episode_id}: {record.summary} "
                f"({len(record.mutations)} mutations applied)"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="botai SelfSearch CLI")
    parser.add_argument(
        "--generations",
        type=int,
        default=1,
        help="Number of self-improvement generations (default: 1)",
    )
    parser.add_argument(
        "--lineage",
        choices=["all", "pedagogical_capability", "adaptive_scaffolding"],
        default="all",
        help="Lineage to run (default: all)",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=".",
        help="Workspace root path",
    )
    args = parser.parse_args()
    workspace_root = Path(args.root).resolve()
    run_self_search(workspace_root, args.generations, args.lineage)


if __name__ == "__main__":
    main()
