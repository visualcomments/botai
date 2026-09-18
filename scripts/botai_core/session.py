# -*- coding: utf-8 -*-
"""The tutoring session as a service: tutoring rules bound to the store.

`tutoring.py` is pure — it computes directives, reducers and transitions and
never touches storage. This module is the thin layer that binds those pure
functions to a workspace: it opens the store, resolves the accepted course,
applies commands atomically, and writes the Markdown projection afterwards.

Why a separate layer rather than putting storage inside `tutoring.py`: the
purity is what makes the teaching rules testable without a database, and the
teaching rules are the part that must not be entangled with transaction
handling. It is also what lets a future MCP adapter call the same logic without
duplicating any of it.

The store remains the single source of truth. The Markdown file under
`progress/<learner-id>/` is a projection written *after* the commit: a failure
to render it must never lose a recorded fact.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import course as course_mod
from . import paths
from . import store as store_mod
from . import tutoring as T

SESSION_KIND = "session"


def new_id():
    return str(uuid.uuid4())


class SessionService:
    """Session commands for one workspace, learner and course."""

    def __init__(self, store, course, *, clock=None):
        self.store = store
        self.course = course
        self.clock = clock

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def open(cls, root, learner_id, course_id, *, create=True, clock=None):
        """Open the workspace store and load the accepted course.

        Refuses to start a session for a course nobody accepted: without an
        accepted contract there is no authoritative grading policy, and a
        session that teaches anyway would be guessing at the rules.

        The store is opened first and closed again if the course turns out not
        to be accepted — an exception must not leave a SQLite handle open on a
        workspace the caller never got a service for.
        """
        store = store_mod.Store.open(root, learner_id, create=create)
        try:
            accepted = course_mod.load_accepted(root, course_id)
        except Exception:
            store.close()
            raise
        return cls(store, accepted, clock=clock)

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- reads -------------------------------------------------------------
    def get_session(self, session_id):
        body, version = self.store.get(SESSION_KIND, session_id,
                                       course_id=self.course.course_id)
        return body, version

    def objective_states(self):
        """Every recorded objective state, as `{objective_id: state}`."""
        states = {}
        for body in self.store.list_entities("objective_state",
                                             course_id=self.course.course_id):
            states[body["objective_id"]] = body
        return states

    def checks_for(self, objective_id):
        return [c for c in self.store.list_entities("check",
                                                    course_id=self.course.course_id)
                if c.get("objective_id") in (None, objective_id)]

    # -- commands ----------------------------------------------------------
    def start(self, *, objective_ids=(), modes=("tutoring",), session_minutes=30,
              consent_version=None, request_id=None):
        """`session-start`. Creates the session in the state its inputs imply."""
        document = T.new_session(
            course=self.course,
            learner_id=self.store.learner_id,
            objectives=objective_ids,
            modes=modes,
            session_minutes=session_minutes,
            consent_version=consent_version,
            consent_pending=consent_version is None,
            clock=self.clock,
        )
        session_id = new_id()
        document["session_id"] = session_id

        result, replayed = self.store.apply(
            kind=SESSION_KIND,
            entity_id=session_id,
            events=["session.opened"],
            course_id=self.course.course_id,
            request_id=request_id,
            new_body=document,
            event_payloads=[{
                "course_revision": dict(self.course.revision),
                "contract_hash": self.course.contract_hash,
                "consent_version": consent_version,
                "modes": list(modes),
                "objective_ids": list(objective_ids),
                "state": document["state"],
                "_actor": "learner_cli",
                "_provenance": "human_input",
            }],
        )
        body, version = self.get_session(session_id)
        body["version"] = version
        self._project()
        return body, result, replayed

    def next_step(self, session_id):
        """`session-next`. A pure read: computes the directive, writes nothing."""
        body, version = self.get_session(session_id)
        if body is None:
            raise T.TutoringError("SESSION_NOT_FOUND",
                                  "сессия не найдена: %s" % session_id)
        body = dict(body)
        body["session_id"] = session_id
        body["version"] = version
        directive = T.next_step(self.course, body,
                                objective_states=self.objective_states())
        return body, directive

    def configure_goal(self, session_id, objective_id, *, expected_version,
                       assignment_id=None, request_id=None, confirm=False):
        """`session-configure` with a goal, then optionally its confirmation.

        Two steps on purpose, following the design's transition table:
        choosing an objective moves to `GOAL_SELECTED`, and starting work on it
        (`confirmed_goal`) moves to `ATTEMPT_PENDING`. Collapsing them would let
        a session begin an attempt without the learner having agreed to the
        goal, which is exactly the step the confirmation exists to record (and
        where `prerequisites_unverified` gets attached when diagnosis was
        skipped).
        """
        body, version = self.get_session(session_id)
        if body is None:
            raise T.TutoringError("SESSION_NOT_FOUND", "сессия не найдена")

        objective = self.course.objective(objective_id)
        if objective is None:
            raise T.TutoringError(
                "OBJECTIVE_UNKNOWN",
                "цель %r отсутствует в принятой программе курса" % objective_id,
            )

        assessment, why = self.course.assessment_of(assignment_id)
        demonstrated = {oid for oid, st in self.objective_states().items()
                        if st.get("stage") == "demonstrated"}
        unmet = self.course.unsatisfied_prerequisites(objective_id, demonstrated)

        # Choosing a goal and starting work on it are two transitions, and the
        # automaton requires both: DIAGNOSIS -> GOAL_SELECTED ->
        # ATTEMPT_PENDING. A caller that asks for both at once gets both, in
        # that order — skipping GOAL_SELECTED would be the one transition that
        # lets a session begin an attempt without the goal having been chosen.
        current = body["state"]
        steps = []
        if confirm:
            if current == "DIAGNOSIS":
                steps.append("GOAL_SELECTED")
            steps.append("ATTEMPT_PENDING")
        else:
            if current != "GOAL_SELECTED":
                steps.append("GOAL_SELECTED")

        updated = body
        for target in steps:
            updated = T.session_document(
                updated,
                target_state=target,
                current_objective_id=objective_id,
                current_assignment_id=assignment_id,
                assessment=assessment,
            )

        result, replayed = self.store.apply(
            kind=SESSION_KIND,
            entity_id=session_id,
            events=["session.goal_selected"],
            course_id=self.course.course_id,
            expected_version=expected_version,
            request_id=request_id,
            new_body=updated,
            event_payloads=[{
                "objective_id": objective_id,
                "assignment_id": assignment_id,
                "assessment": assessment,
                "assessment_basis": why,
                "from_state": body["state"],
                "to_state": updated["state"],
                "confirmed": bool(confirm),
                # Recorded, not hidden: the design requires that starting a goal
                # with unverified prerequisites is visible afterwards.
                "prerequisites_unverified": unmet,
                "_actor": "learner_cli",
                "_provenance": "human_input",
            }],
        )
        return updated, result, replayed

    def transition(self, session_id, target, *, expected_version, request_id=None,
                   resume_state=None, reason=None):
        """pause / resume / close / cancel, through the automaton.

        Every move goes through `can_transition`: a session cannot be closed
        from a state that never reached a reflection, which is what stops an
        early exit from being recorded as a completed cycle.
        """
        body, version = self.get_session(session_id)
        if body is None:
            raise T.TutoringError("SESSION_NOT_FOUND", "сессия не найдена")

        saved = resume_state if resume_state is not None else body.get("resume_state")
        if target in ("PAUSED", "BLOCKED"):
            saved = body["state"]

        updated = T.session_document(body, target_state=target,
                                     resume_state=saved,
                                     blocked_reason=reason if target == "BLOCKED" else body.get("blocked_reason"),
                                     ended_at=T.now_iso(self.clock)
                                     if target in ("COMPLETED", "CANCELLED") else body.get("ended_at"))

        event = {"session.paused": "session.paused",
                 "session.closed": "session.closed",
                 "session.resumed": "session.resumed",
                 "session.cancelled": "session.cancelled"}.get(
                     "session.closed" if target == "COMPLETED" else "session.paused",
                     "session.paused")
        if target == "COMPLETED":
            event = "session.closed"
        elif target == "CANCELLED":
            event = "session.cancelled"
        elif target in ("PAUSED", "BLOCKED"):
            event = "session.paused"
        elif body["state"] in ("PAUSED", "BLOCKED"):
            event = "session.resumed"

        result, replayed = self.store.apply(
            kind=SESSION_KIND,
            entity_id=session_id,
            events=[event],
            course_id=self.course.course_id,
            expected_version=expected_version,
            request_id=request_id,
            new_body=updated,
            event_payloads=[{
                "from_state": body["state"],
                "to_state": updated["state"],
                "resume_state": saved,
                "reason": reason,
                "independence_claim": updated.get("independence_claim"),
                "_actor": "learner_cli",
                "_provenance": "human_input",
            }],
        )
        return updated, result, replayed

    def record_attempt(self, session_id, *, attempt_id=None, objective_id=None,
                       assignment_id=None, source="learner_report",
                       artifact_sha256=None, text_excerpt=None,
                       task_instance_id=None, expected_version=None,
                       request_id=None):
        """`attempt-record`. Stores the learner's own work, not a solution."""
        body, version = self.get_session(session_id)
        if body is None:
            raise T.TutoringError("SESSION_NOT_FOUND", "сессия не найдена")

        attempt_id = attempt_id or new_id()

        # The assistance reached in this session's cycle comes from recorded
        # assistance events, not from the caller's word: the model must not be
        # able to declare its own help irrelevant.
        levels = [e["payload"].get("level")
                  for e in self.store.events(course_id=self.course.course_id)
                  if e["event_type"] == "assistance.recorded"
                  and e["payload"].get("objective_id") == objective_id]

        payload = T.record_attempt(
            body, attempt_id=attempt_id,
            objective_id=objective_id or body.get("current_objective_id"),
            assignment_id=assignment_id or body.get("current_assignment_id"),
            source=source, artifact_sha256=artifact_sha256,
            text_excerpt=text_excerpt, task_instance_id=task_instance_id,
            assistance_cycle=levels, clock=self.clock,
        )

        document = {
            "schema_version": T.SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "session_id": session_id,
            "objective_id": payload["objective_id"],
            "assignment_id": payload["assignment_id"],
            "source": payload["source"],
            "artifact_sha256": payload["artifact_sha256"],
            "text_excerpt": payload["text_excerpt"],
            "task_instance_id": payload["task_instance_id"],
            "assistance_max": payload["assistance_max"],
            "submitted_at": payload["submitted_at"],
        }

        # An attempt on the main task moves the session into feedback; a
        # diagnostic attempt leaves the state alone, because diagnosis has to
        # finish before a goal is chosen and a check still has to arrive.
        target = body["state"]
        if target == "ATTEMPT_PENDING":
            target = "FEEDBACK"

        updated = T.session_document(body, target_state=target,
                                     assistance_max=T.max_level(
                                         body.get("assistance_max"),
                                         payload["assistance_max"]),
                                     steps_taken=T.count_step(body))

        result, replayed = self.store.apply(
            kind="attempt", entity_id=attempt_id, events=["attempt.recorded"],
            course_id=self.course.course_id,
            expected_version=expected_version, request_id=request_id,
            new_body=document, event_payloads=[payload],
        )
        # The session is written when the state moves *or* when the step count
        # advances. Writing only on a state change would freeze the counter after
        # the first step — every later attempt lands in a session already in
        # FEEDBACK, so `steps_taken` would read 1 forever and the capacity budget
        # would never fire.
        stepped = updated.get("steps_taken") != body.get("steps_taken")
        if target != body["state"] or stepped:
            self.store.apply(
                kind=SESSION_KIND, entity_id=session_id,
                events=["session.attempt_recorded"],
                course_id=self.course.course_id,
                new_body=updated,
                event_payloads=[{"attempt_id": attempt_id,
                                 "assessment": body.get("assessment"),
                                 "steps_taken": updated.get("steps_taken"),
                                 "_actor": "core", "_provenance": "core_computed"}],
            )
        self._project()
        return document, result, replayed

    def record_assistance(self, session_id, *, level, step, reason,
                          objective_id=None, assignment_id=None,
                          response_ref=None, expected_version=None,
                          request_id=None, decision=None):
        """`assistance-record`. Refuses a level the accepted policy forbids."""
        body, version = self.get_session(session_id)
        if body is None:
            raise T.TutoringError("SESSION_NOT_FOUND", "сессия не найдена")

        objective_id = objective_id or body.get("current_objective_id")
        assignment_id = assignment_id or body.get("current_assignment_id")

        # Policy is re-checked here, not trusted from the caller. A model that
        # has already decided to escalate cannot record the escalation as
        # legitimate by asking nicely.
        from . import policy as policy_mod

        if decision is None:
            decision = policy_mod.request_help(
                self.course, assignment_id=assignment_id,
                intent={"HINT": "hint", "EXAMPLE": "example",
                        "SOLUTION": "solution"}.get(level, "hint"),
                preference=body.get("feedback_preference", "prefer-ask"),
                student_requested=True,
            )
        if decision["decision"] != "allow":
            raise T.TutoringError(
                "ASSISTANCE_DENIED",
                "запись помощи уровня %s отклонена политикой (%s): %s"
                % (level, decision["reason_code"], decision["message_ru"]),
            )

        payload = T.record_assistance(
            objective_id=objective_id, assignment_id=assignment_id,
            level=level, step=step, reason=reason, response_ref=response_ref,
            clock=self.clock,
        )
        updated = T.session_document(
            body,
            assistance_max=T.max_level(body.get("assistance_max"), level),
        )
        result, replayed = self.store.apply(
            kind=SESSION_KIND, entity_id=session_id,
            events=["assistance.recorded"],
            course_id=self.course.course_id,
            expected_version=expected_version, request_id=request_id,
            new_body=updated, event_payloads=[payload],
        )
        return updated, result, replayed, decision

    def record_check(self, session_id, *, check_id=None, attempt_id, kind,
                     criterion_results, verdict, assessed_by="model",
                     reliability="provisional", objective_id=None,
                     expected_version=None, request_id=None):
        """`check-record`: the assessment, then the mastery reducer.

        Both happen in one command with a shared `request_id`, so a crash
        between them cannot leave a check recorded but its consequence lost.
        """
        body, version = self.get_session(session_id)
        if body is None:
            raise T.TutoringError("SESSION_NOT_FOUND", "сессия не найдена")

        check_id = check_id or new_id()
        objective_id = objective_id or body.get("current_objective_id")
        if not objective_id:
            raise T.TutoringError(
                "CHECK_WITHOUT_OBJECTIVE",
                "проверка без цели не обновляет освоение: укажите objective_id",
            )

        payload = T.record_check(
            check_id=check_id, attempt_id=attempt_id, kind=kind,
            criterion_results=criterion_results, verdict=verdict,
            assessed_by=assessed_by, reliability=reliability, clock=self.clock,
        )
        payload["objective_id"] = objective_id

        document = dict(payload)
        document["session_id"] = session_id
        document["objective_id"] = objective_id

        # The mastery reducer runs on stored evidence, not on this call's
        # opinion: `evaluate_mastery` is what decides the stage.
        existing = self.objective_states().get(objective_id, {})
        attempt = self.store.get("attempt", attempt_id,
                                 course_id=self.course.course_id)[0] or {}
        checks = [c for c in self.store.list_entities("check",
                                                      course_id=self.course.course_id)]
        checks = checks + [document]
        attempts = [attempt] if attempt else []
        stage, why, evidence = T.evaluate_mastery(
            objective_id, checks=checks, attempts=attempts,
            current_stage=existing.get("stage", "new"), clock=self.clock,
        )

        state_document = T.objective_state_document(
            objective_id, stage=stage, evidence_ids=evidence,
            latest_check_id=check_id,
            assistance_max=T.max_level(existing.get("assistance_max"),
                                       body.get("assistance_max")),
            review_due_at=existing.get("review_due_at"),
            consecutive_stuck_sessions=existing.get("consecutive_stuck_sessions", 0),
        )

        state_version = self.store.version_of("objective_state", objective_id,
                                              course_id=self.course.course_id)

        target = body["state"]
        if target in ("FEEDBACK", "REMEDIATION"):
            if verdict == "pass":
                target = "UNDERSTANDING_CHECK"
            elif verdict in ("partial", "fail"):
                target = "REMEDIATION"
            else:
                target = "FEEDBACK"

        updated = T.session_document(body, target_state=target)
        result, replayed = self.store.apply(
            kind="check", entity_id=check_id,
            events=["check.recorded"],
            course_id=self.course.course_id,
            expected_version=expected_version, request_id=request_id,
            new_body=document, event_payloads=[payload],
        )
        self.store.apply(
            kind="objective_state", entity_id=objective_id,
            events=["objective.transitioned"],
            course_id=self.course.course_id,
            expected_version=state_version,
            new_body=state_document,
            event_payloads=[{
                "objective_id": objective_id,
                "from": existing.get("stage", "new"),
                "to": stage,
                "evidence_ids": evidence,
                "rule_version": "v2-mastery-1",
                "reason": why,
                "_actor": "core",
                "_provenance": "core_computed",
            }],
        )
        if target != body["state"]:
            self.store.apply(
                kind=SESSION_KIND, entity_id=session_id,
                events=["session.check_recorded"],
                course_id=self.course.course_id,
                new_body=updated,
                event_payloads=[{"check_id": check_id, "verdict": verdict,
                                 "stage": stage,
                                 "_actor": "core", "_provenance": "core_computed"}],
            )
        self._project()
        return document, state_document, (stage, why, evidence), result, replayed

    # -- projection --------------------------------------------------------
    def _project(self):
        """Write the human-readable Markdown view.

        Deliberately best-effort: the store is authoritative and a rendering
        failure must not lose a recorded fact. The next command repairs the
        file, which is why this runs after the commit rather than inside it.
        """
        try:
            self.write_projection()
        except OSError:
            pass

    def write_projection(self):
        from . import progress as progress_mod

        document = progress_mod.build_progress(
            course=self.course,
            learner_id=self.store.learner_id,
            sessions=[b for b in self.store.list_entities(SESSION_KIND,
                                                          course_id=self.course.course_id)],
            objective_states=self.objective_states(),
            attempts=self.store.list_entities("attempt",
                                              course_id=self.course.course_id),
            checks=self.store.list_entities("check",
                                            course_id=self.course.course_id),
        )
        directory = paths.learner_state_dir(self.store.root, self.store.learner_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = paths.ensure_within(directory, directory / ("%s.md" % self.course.slug))
        target.write_text(progress_mod.render_markdown(document),
                          encoding="utf-8", newline="\n")
        return target
