#!/usr/bin/env python3
"""Offline behavioral fixtures for realtime self-improvement safety claims."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import store


LEARNER = "fixture-realtime"


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        with store.Store.open(directory, LEARNER) as state:
            check(not state.is_session_improvement_enabled(), "feature flag must default off")
            try:
                state.set_session_improvement_consent("fixture", scope="session_observation")
            except store.StoreError as error:
                check(error.code == "SESSION_IMPROVEMENT_DISABLED", "disabled consent must fail closed")
            else:
                raise AssertionError("disabled consent unexpectedly succeeded")
            state.set_session_improvement_enabled(True)
            state.set_session_improvement_consent("fixture", scope="session_observation")
            try:
                state.record_session_observation(
                    "fixture", observation_id="bad", kind="tool_error", area="cli",
                    summary="student answer", source_ref="course.md")
            except store.StoreError as error:
                check(error.code == "OBSERVATION_PRIVACY_REJECTED", "student data must be rejected")
            else:
                raise AssertionError("student data unexpectedly stored")
            summary = state.session_summary("fixture")
            check(summary["default_action"] == "discard", "session default must discard")
            check(summary["persistent_opt_in_required"], "persistent opt-in must be explicit")
    print("realtime behavioral fixtures: PASS")


if __name__ == "__main__":
    run()
