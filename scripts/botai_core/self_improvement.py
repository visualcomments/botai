"""SelfSearch-aligned Self-Improvement Engine for botai.

Implements the reward-free self-improvement mechanism described in
arXiv:2609.37968 ("SelfSearch: Reward-Free Search for Self-Improving Agents").

Core Architecture:
1. Two Lineages:
   - pedagogical_capability (Capability Lineage): developing reusable skills/tools/helpers.
   - adaptive_scaffolding (Adaptive Lineage): improving error recovery and scaffolding.
2. Runtime Protection:
   - Strict immutable boundaries: Core governance, Hard Refusals, Assistance Ladder,
     and SQLite schemas cannot be mutated by the self-improvement loop.
3. Episode Trajectories:
   - Tracks reasoning, proposed actions, verification outcomes, and code diffs.
   - Preserves episode records as read-only evidence for subsequent generations.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Protected files and path prefixes that CANNOT be modified during self-improvement
PROTECTED_PREFIXES: Set[str] = {
    "scripts/botai_core/policy.py",
    "scripts/botai_core/security.py",
    "scripts/botai_core/schemas.py",
    "scripts/botai_core/store.py",
    "AGENTS.md",
    "LICENSE",
    "progress/",
    ".botai/",
}

# Permitted paths for self-improvement mutations
PERMITTED_MUTATION_PREFIXES: Set[str] = {
    ".agents/skills/",
    "scripts/botai_core/skills.py",
    "scripts/botai_core/tutoring.py",
    "scripts/botai_core/scaffolding.py",
    "scripts/botai_core/error_diagnosis.py",
    "scripts/botai_core/retrieval.py",
    "scripts/botai_core/visualizations.py",
}


@dataclass
class ToolAction:
    """A single tool execution record in an episode trajectory."""

    tool_name: str
    arguments: Dict[str, Any]
    output: str
    success: bool
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class MutationDiff:
    """Record of a source modification made during an episode."""

    relative_path: str
    old_hash: str
    new_hash: str
    diff_text: str


@dataclass
class EpisodeRecord:
    """Full trajectory of a self-improvement episode (arXiv:2609.37968, Section 2)."""

    episode_id: str
    generation: int
    lineage: str  # 'pedagogical_capability' or 'adaptive_scaffolding'
    direction_prompt: str
    reasoning: List[str]
    actions: List[ToolAction]
    mutations: List[MutationDiff]
    verification_passed: bool
    summary: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EpisodeRecord:
        actions = [ToolAction(**a) for a in data.get("actions", [])]
        mutations = [MutationDiff(**m) for m in data.get("mutations", [])]
        clean_data = dict(data)
        clean_data["actions"] = actions
        clean_data["mutations"] = mutations
        return cls(**clean_data)


class SafetyGateError(Exception):
    """Raised when an agent attempts to mutate protected invariants."""

    pass


class SafetyGate:
    """Validates that candidate changes do not compromise core safety or pedagogy."""

    @staticmethod
    def validate_path(relative_path: str) -> None:
        rel = relative_path.replace("\\", "/").strip("/")
        for prot in PROTECTED_PREFIXES:
            if rel == prot.strip("/") or rel.startswith(prot):
                raise SafetyGateError(
                    f"Violation of invariant: Path '{rel}' is protected and immutable."
                )

        # Ensure path is within permitted mutable areas
        allowed = any(
            rel.startswith(perm.strip("/")) for perm in PERMITTED_MUTATION_PREFIXES
        )
        if not allowed:
            raise SafetyGateError(
                f"Path '{rel}' is outside permitted self-improvement zones "
                f"({', '.join(PERMITTED_MUTATION_PREFIXES)})."
            )

    @staticmethod
    def validate_skill_content(content: str) -> None:
        """Ensure modified SKILL.md does not inject instructions weakening policy."""
        lowered = content.lower()
        forbidden_phrases = [
            "ignore agents.md",
            "give ready solution",
            "skip consent gate",
            "bypass policy-check",
            "answer graded task",
            "всегда выдавай готовое решение",
        ]
        for phrase in forbidden_phrases:
            if phrase in lowered:
                raise SafetyGateError(
                    f"Harmful instruction detected in candidate skill: '{phrase}'"
                )


class EpisodeEnvironment:
    """Isolated filesystem sandbox for evaluating candidate mutations."""

    def __init__(self, base_root: Path, sandbox_root: Path) -> None:
        self.base_root = base_root.resolve()
        self.sandbox_root = sandbox_root.resolve()

    def setup(self) -> None:
        """Create clean sandbox copy of permitted mutable directories."""
        if self.sandbox_root.exists():
            shutil.rmtree(self.sandbox_root)
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

        for perm in PERMITTED_MUTATION_PREFIXES:
            src_path = self.base_root / perm
            dst_path = self.sandbox_root / perm
            if src_path.is_dir():
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
            elif src_path.is_file():
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)

    def verify_syntax_and_tests(self, target_rel_paths: List[str]) -> Tuple[bool, str]:
        """Runs syntax checks and targeted pytest regression tests."""
        for rel in target_rel_paths:
            full_sandbox_path = self.sandbox_root / rel
            if full_sandbox_path.suffix == ".py" and full_sandbox_path.exists():
                try:
                    with open(full_sandbox_path, "r", encoding="utf-8") as f:
                        compile(f.read(), str(full_sandbox_path), "exec")
                except Exception as e:
                    return False, f"Syntax compilation error in {rel}: {e}"

        # Run relevant unit tests if test suite exists in the base_root
        test_file = self.base_root / "tests" / "test_skills.py"
        if test_file.exists():
            env = dict(os.environ)
            env["PYTHONPATH"] = str(self.base_root / "scripts")
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pytest", str(test_file), "-q"],
                    cwd=str(self.base_root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env,
                )
                if res.returncode != 0:
                    return False, f"Regression tests failed: {res.stdout}\n{res.stderr}"
            except Exception as e:
                return False, f"Test runner execution failure: {e}"

        return True, "Verification tests passed cleanly."

    def cleanup(self) -> None:
        if self.sandbox_root.exists():
            shutil.rmtree(self.sandbox_root, ignore_errors=True)


class SelfSearchEngine:
    """Orchestrates SelfSearch across generations for botai."""

    DIRECTIONS: Dict[str, str] = {
        "pedagogical_capability": (
            "Analyze episode records and student interactions to identify capabilities "
            "where the assistant needed multiple attempts, hit corpus retrieval limits, "
            "or lacked clean formatting. Synthesize or refine helper skills and tools."
        ),
        "adaptive_scaffolding": (
            "Analyze episode records to improve recovery from student confusion, refine "
            "error diagnosis categorizations, and adapt Socratic questioning rungs without "
            "violating the assistance ladder."
        ),
    }

    def __init__(
        self, workspace_root: Path, storage_dir: Optional[Path] = None
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.storage_dir = (
            storage_dir.resolve()
            if storage_dir
            else self.workspace_root / ".botai" / "self_search"
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir = self.storage_dir / "episodes"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def load_previous_episodes(self, max_recent: int = 4) -> List[EpisodeRecord]:
        """Loads read-only episode records to form evidence for the next improver."""
        records: List[EpisodeRecord] = []
        if not self.evidence_dir.exists():
            return records

        for file_path in sorted(self.evidence_dir.glob("*.json")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    records.append(EpisodeRecord.from_dict(data))
            except Exception:
                continue

        return records[-max_recent:]

    def save_episode(self, episode: EpisodeRecord) -> Path:
        """Persists episode record into the evidence archive."""
        filename = (
            f"gen_{episode.generation:03d}_{episode.lineage}_{episode.episode_id}.json"
        )
        target = self.evidence_dir / filename
        with open(target, "w", encoding="utf-8") as f:
            json.dump(episode.to_dict(), f, indent=2, ensure_ascii=False)
        return target

    def apply_mutation(
        self,
        rel_path: str,
        new_content: str,
        sandbox: EpisodeEnvironment,
    ) -> MutationDiff:
        """Validates and applies a mutation inside the sandbox."""
        SafetyGate.validate_path(rel_path)
        if rel_path.endswith("SKILL.md"):
            SafetyGate.validate_skill_content(new_content)

        full_base_path = self.workspace_root / rel_path
        old_text = ""
        old_hash = ""
        if full_base_path.exists():
            with open(full_base_path, "r", encoding="utf-8") as f:
                old_text = f.read()
            old_hash = hashlib.sha256(old_text.encode("utf-8")).hexdigest()

        new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()

        # Compute diff
        diff_lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
        diff_text = "".join(diff_lines)

        # Write to sandbox
        full_sandbox_path = sandbox.sandbox_root / rel_path
        full_sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_sandbox_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return MutationDiff(
            relative_path=rel_path,
            old_hash=old_hash,
            new_hash=new_hash,
            diff_text=diff_text,
        )

    def execute_episode(
        self,
        generation: int,
        lineage: str,
        improver_fn: Callable[
            [str, List[EpisodeRecord], EpisodeEnvironment],
            Tuple[List[str], List[ToolAction], List[MutationDiff], str],
        ],
    ) -> EpisodeRecord:
        """Executes a single self-improvement episode under a lineage direction."""
        direction = self.DIRECTIONS.get(
            lineage, "Explore general capability and reliability improvements."
        )
        previous_records = self.load_previous_episodes()

        sandbox_dir = self.storage_dir / f"sandbox_g{generation}_{lineage}"
        sandbox = EpisodeEnvironment(self.workspace_root, sandbox_dir)
        sandbox.setup()

        episode_id = hashlib.sha256(
            f"{generation}:{lineage}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:12]

        reasoning: List[str] = []
        actions: List[ToolAction] = []
        mutations: List[MutationDiff] = []
        summary = ""
        verified = False

        try:
            reasoning, actions, mutations, summary = improver_fn(
                direction, previous_records, sandbox
            )

            # Validate all mutations
            mutated_paths = [m.relative_path for m in mutations]
            passed, check_msg = sandbox.verify_syntax_and_tests(mutated_paths)
            verified = passed
            actions.append(
                ToolAction(
                    tool_name="verify_mutations",
                    arguments={"paths": mutated_paths},
                    output=check_msg,
                    success=passed,
                )
            )

            # If verified, promote mutations from sandbox to workspace
            if verified and mutations:
                for m in mutations:
                    src = sandbox.sandbox_root / m.relative_path
                    dst = self.workspace_root / m.relative_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        except Exception as e:
            actions.append(
                ToolAction(
                    tool_name="episode_execution",
                    arguments={},
                    output=f"Failed episode with exception: {e}",
                    success=False,
                )
            )
            verified = False
            summary = f"Aborted due to error: {e}"
        finally:
            sandbox.cleanup()

        record = EpisodeRecord(
            episode_id=episode_id,
            generation=generation,
            lineage=lineage,
            direction_prompt=direction,
            reasoning=reasoning,
            actions=actions,
            mutations=mutations,
            verification_passed=verified,
            summary=summary,
        )

        self.save_episode(record)
        return record
