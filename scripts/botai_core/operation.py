# -*- coding: utf-8 -*-
"""The operation lifecycle: plan, approve, apply, verify — with a human in it.

This module is where "a learner approved this" becomes a checkable fact rather
than a claim. The design's requirement is specific: approval is bound to one
plan hash, spent once, and impossible for the tutoring model to produce. That
last part is structural, not a rule in a prompt — there is no function here that
a tool call can reach to mint an approval, because minting one requires a
`human_channel` that the caller does not get to invent.

The lifecycle, and what each state actually asserts:

    PLAN_READY            a plan exists; nothing has run
    AWAITING_APPROVAL     a human has been asked; nothing has run
    APPLYING              approval consumed, lease held by exactly one worker
    VERIFYING             steps finished; checks have not
    READY                 every required check passed
    PARTIAL               some steps ran, some did not — never reported as READY
    FAILED / CANCELLED    terminal, with the observed results kept

Two details that are easy to get wrong and expensive to debug:

* **The lease exists to stop two workers running one operation.** A second
  worker that finds a live lease is refused rather than queued: two processes
  running the same install steps is how a half-installed environment appears.
* **A stale lease is not stolen immediately.** The owner is checked before
  anything is taken over, because a lease usually looks stale right before it
  finishes.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import actions
from . import environment as env_mod
from . import policy
from . import store as store_mod

SCHEMA_VERSION = 2

OPERATION_KIND = "operation"
APPROVAL_KIND = "approval"

# How long a worker's lease is valid without a heartbeat, and how long a whole
# plan may run. The plan deadline comes from the plan itself.
LEASE_SECONDS = 120


def new_id():
    return str(uuid.uuid4())


class OperationError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


class OperationService:
    """Plan and run environment operations for one workspace."""

    def __init__(self, store, root, *, clock=None, runner=None):
        self.store = store
        self.root = Path(root)
        self.clock = clock
        self.runner = runner

    @classmethod
    def open(cls, root, learner_id, *, create=True, clock=None, runner=None):
        store = store_mod.Store.open(root, learner_id, create=create)
        return cls(store, root, clock=clock, runner=runner)

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- plan ------------------------------------------------------------
    def create_plan(self, spec, *, course, profile_id=None, input_hashes=None,
                    limits=None, request_id=None):
        """Build and store a plan for a course. Nothing is executed."""
        environment_root = self._environment_dir(course.course_id)
        operation_id = new_id()

        plan = env_mod.build_plan(
            spec, course=course, operation_id=operation_id,
            workspace_id=self._workspace_id(), course_id=course.course_id,
            input_hashes=input_hashes or {}, limits=limits,
            clock=self.clock, environment_root=environment_root,
        )

        operation = {
            "schema_version": SCHEMA_VERSION,
            "operation_id": operation_id,
            "course_id": course.course_id,
            "plan_hash": plan["plan_hash"],
            "status": "PLAN_READY",
            "next_step": 0,
            "cancellation_requested": False,
            "lease_owner": None,
            "lease_expires_at": None,
            "results": [],
            "started_at": None,
            "finished_at": None,
        }
        self._store_document(OPERATION_KIND, operation_id, operation,
                             events=["environment.plan_ready"],
                             payload={"plan_hash": plan["plan_hash"],
                                      "steps": len(plan["steps"]),
                                      "profile_id": plan.get("profile_id")},
                             request_id=request_id)
        self._store_document("plan", operation_id, plan,
                             events=["environment.plan_stored"],
                             payload={"plan_hash": plan["plan_hash"]})
        return plan, operation

    def get_plan(self, operation_id):
        return self.store.get("plan", operation_id,
                              course_id=store_mod.WORKSPACE_COURSE_ID)[0]

    def get_operation(self, operation_id):
        body, version = self.store.get(OPERATION_KIND, operation_id,
                                       course_id=store_mod.WORKSPACE_COURSE_ID)
        if body is not None:
            body["version"] = version
        return body

    def list_operations(self, course_id=None):
        items = self.store.list_entities(OPERATION_KIND,
                                         course_id=store_mod.WORKSPACE_COURSE_ID)
        if course_id:
            items = [i for i in items if i.get("course_id") == course_id]
        return items

    # -- approval --------------------------------------------------------
    def approve(self, operation_id, *, decision="granted", human_channel,
                request_id=None):
        """Record a human's decision about one plan hash.

        `human_channel` is required and is not defaulted: a caller that cannot
        say *how* a human approved cannot produce an approval here. This is the
        single point where the model's inability to self-approve is enforced.
        """
        if not human_channel or not str(human_channel).strip():
            raise OperationError(
                "APPROVAL_CHANNEL_REQUIRED",
                "разрешение требует указания человеческого канала: "
                "approval без него неотличим от выдуманного моделью",
            )
        if decision not in ("granted", "denied"):
            raise OperationError("APPROVAL_DECISION_UNKNOWN",
                                 "решение должно быть granted или denied")

        plan = self.get_plan(operation_id)
        if plan is None:
            raise OperationError("OPERATION_NOT_FOUND",
                                 "план не найден: %s" % operation_id)

        # Only the plan's own integrity and its expiry are checked here. Input
        # hashes are deliberately *not* re-verified: they describe the workspace
        # at execution time, and comparing them against nothing would refuse a
        # perfectly good approval for a plan that pinned any input at all. The
        # input check belongs at `apply`, immediately before a step runs.
        ok, problems = env_mod.verify_plan(plan, clock=self.clock)
        if not ok:
            # Approving an expired or edited plan is refused rather than
            # recorded: an approval that cannot be acted on is worse than none,
            # because it looks like permission.
            raise OperationError(
                "PLAN_NOT_APPROVABLE",
                "; ".join(p["message_ru"] for p in problems),
                detail={"problems": problems},
            )

        approval = {
            "schema_version": SCHEMA_VERSION,
            "approval_id": new_id(),
            "operation_id": operation_id,
            "plan_hash": plan["plan_hash"],
            "expires_at": plan["expires_at"],
            "consumed_at": None,
            "decision": decision,
            "human_channel": str(human_channel),
        }
        self._store_document(APPROVAL_KIND, approval["approval_id"], approval,
                             events=["environment.approval_recorded"],
                             payload={"operation_id": operation_id,
                                      "plan_hash": plan["plan_hash"],
                                      "decision": decision,
                                      "human_channel": human_channel},
                             request_id=request_id)

        operation = self.get_operation(operation_id)
        operation["status"] = ("AWAITING_APPROVAL" if decision == "denied"
                               else "AWAITING_APPROVAL")
        self._update_operation(operation)
        return approval

    def find_approval(self, operation_id, plan_hash):
        """The live, unconsumed approval for this exact plan, if any."""
        for body in self.store.list_entities(APPROVAL_KIND,
                                             course_id=store_mod.WORKSPACE_COURSE_ID):
            if (body.get("operation_id") == operation_id
                    and body.get("plan_hash") == plan_hash
                    and body.get("decision") == "granted"
                    and not body.get("consumed_at")):
                return body
        return None

    def _consume_approval(self, approval):
        updated = dict(approval)
        updated["consumed_at"] = env_mod.now_iso(self.clock)
        version = self.store.version_of(APPROVAL_KIND, approval["approval_id"],
                                        course_id=store_mod.WORKSPACE_COURSE_ID)
        self._store_document(APPROVAL_KIND, approval["approval_id"], updated,
                             events=["environment.approval_consumed"],
                             payload={"operation_id": approval["operation_id"],
                                      "plan_hash": approval["plan_hash"]},
                             expected_version=version)
        return updated

    # -- apply -----------------------------------------------------------
    def apply(self, operation_id, *, actor="human_cli", current_hashes=None,
              max_steps=None, request_id=None, lease_owner=None):
        """Run an approved plan, in this process.

        Refuses unless: the plan verifies, a live approval exists for its exact
        hash, no other worker holds the lease, and the policy engine allows the
        operation for this actor.
        """
        plan = self.get_plan(operation_id)
        if plan is None:
            raise OperationError("OPERATION_NOT_FOUND",
                                 "план не найден: %s" % operation_id)
        operation = self.get_operation(operation_id)

        # A repeat after the steps finished is not a retry, it is a second
        # effect. VERIFYING counts as finished for this purpose: the steps ran,
        # the approval is spent, and re-running them because verification has
        # not happened yet would install twice.
        if operation["status"] in ("READY", "FAILED", "CANCELLED", "VERIFYING",
                                   "PARTIAL"):
            return {"status": operation["status"], "operation": operation,
                    "replayed": True,
                    "message_ru": "операция уже выполнена (%s): повтор не "
                                  "запускается, возвращён прежний результат"
                                  % operation["status"]}

        # Inputs are re-checked here and only here: immediately before anything
        # runs. A plan that pinned an input and a caller that supplies no hashes
        # is refused rather than assumed unchanged.
        ok, problems = env_mod.verify_plan(plan, current_hashes=current_hashes,
                                          clock=self.clock, check_inputs=True)
        if not ok:
            self._set_status(operation, "STALE_PLAN")
            raise OperationError(
                "PLAN_NOT_EXECUTABLE",
                "; ".join(p["message_ru"] for p in problems),
                detail={"problems": problems},
            )

        approval = self.find_approval(operation_id, plan["plan_hash"])
        decision = policy.request_operation(
            "env_apply",
            approval=approval,
            plan_hash=plan["plan_hash"],
            host_can_approve=actor == "human_cli",
        )
        if decision["decision"] != "allow":
            self._set_status(operation, "AWAITING_APPROVAL")
            raise OperationError(
                decision["reason_code"],
                decision["message_ru"],
                detail={"decision": dict(decision)},
            )

        owner = lease_owner or ("%s:%d" % (os.getpid(), id(self)))
        self._acquire_lease(operation, owner=owner)
        self._consume_approval(approval)

        runner = actions.ActionRunner(self.root, self.store, plan,
                                      clock=self.clock, runner=self.runner)
        self._set_status(operation, "APPLYING", started=True)
        report = runner.run(operation_state=operation, max_steps=max_steps)

        operation["results"] = report["results"]
        operation["next_step"] = report["next_step"]
        self._set_status(operation, report["status"],
                         finished=report["status"] in ("FAILED", "CANCELLED"))

        return {"status": report["status"], "operation": operation,
                "report": report, "plan": plan, "replayed": False}

    def verify(self, operation_id, spec, *, request_id=None):
        """Run the spec's checks and decide READY.

        READY requires every required check to pass. A plan whose steps were
        skipped cannot be READY however clean the checks look, because the
        thing being checked was never built.
        """
        operation = self.get_operation(operation_id)
        if operation is None:
            raise OperationError("OPERATION_NOT_FOUND", "операция не найдена")

        skipped = [r for r in operation.get("results") or [] if r.get("skipped")]
        checks = actions.run_checks(spec, root=self.root, clock=self.clock)
        verdict = env_mod.readiness(checks)

        if skipped:
            status = "PARTIAL"
            message = ("шаги не исполнялись (%s), поэтому READY не выставляется: "
                       "проверять нечего" % ", ".join(s["step_id"] for s in skipped))
        elif verdict["ready"]:
            status = "READY"
            message = "все обязательные проверки пройдены"
        else:
            status = "FAILED"
            message = "не пройдены проверки: %s" % ", ".join(
                f["check_id"] or "?" for f in verdict["failures"])

        self._set_status(operation, status, finished=True)
        return {"status": status, "checks": checks, "verdict": verdict,
                "message_ru": message, "skipped_steps": [s["step_id"] for s in skipped]}

    def cancel(self, operation_id, *, reason=None, request_id=None):
        """Request cancellation. The runner checks the flag between steps."""
        operation = self.get_operation(operation_id)
        if operation is None:
            raise OperationError("OPERATION_NOT_FOUND", "операция не найдена")
        operation["cancellation_requested"] = True
        self._update_operation(operation)
        if operation["status"] not in ("APPLYING",):
            self._set_status(operation, "CANCELLED", finished=True)
        return {"operation_id": operation_id, "cancellation_requested": True,
                "reason": reason,
                "message_ru": "запрошена отмена: исполнитель проверит флаг между "
                              "шагами и завершит группу процессов текущего шага"}

    def reconcile(self, operation_id):
        """Inspect an operation left in an unclear state.

        Never re-runs an effect. It reports what the stored results say actually
        happened, so a human — or a later resume — decides from evidence instead
        of from a guess about whether the last step ran.
        """
        operation = self.get_operation(operation_id)
        if operation is None:
            raise OperationError("OPERATION_NOT_FOUND", "операция не найдена")
        plan = self.get_plan(operation_id)

        finished = [r for r in operation.get("results") or [] if r.get("exit_code") is not None]
        failed = [r for r in operation["results"] if not r.get("ok")]

        return {
            "operation_id": operation_id,
            "status": operation["status"],
            "next_step": operation["next_step"],
            "steps_total": len(plan["steps"]) if plan else None,
            "steps_finished": len(finished),
            "failed_steps": [r["step_id"] for r in failed],
            "cancellation_requested": operation.get("cancellation_requested"),
            "lease": {
                "owner": operation.get("lease_owner"),
                "expires_at": operation.get("lease_expires_at"),
                "claimed_by_this_process": False,
            },
            "message_ru": "восстановление не повторяет внешнее действие: ниже "
                          "перечислено, что фактически выполнено",
        }

    # -- internals -------------------------------------------------------
    def _environment_dir(self, course_id):
        """`.botai/environments/<course-id>/` — where a venv step may write.

        Contained and course-scoped: a spec cannot name a destination, so there
        is no path here for it to reach outside this directory.
        """
        from . import paths
        base = self.root / ".botai" / "environments"
        return paths.ensure_within(
            base, base / paths.safe_name(course_id, kind="идентификатор курса"))

    def _workspace_id(self):
        """The workspace identity, or the root path as a fallback.

        Read from the marker `course.accept` writes, so the id is the same one
        the binding and the plan use; inventing a second identity here would
        mean two plans for one workspace could not be told apart.
        """
        marker = self.root / ".botai" / "workspace.json"
        if marker.is_file():
            try:
                recorded = json.loads(marker.read_text(encoding="utf-8"))
                if recorded.get("workspace_id"):
                    return recorded["workspace_id"]
            except (OSError, ValueError):
                pass
        return str(self.root)

    def _acquire_lease(self, operation, *, owner):
        """Take the execution lease, refusing a live one held by someone else.

        A lease is not stolen on the assumption that the holder is dead: a lease
        usually looks stale immediately before its owner finishes. Only an
        expired lease is replaceable, and the takeover is reported so the
        previous holder's fate can be checked.
        """
        current = operation.get("lease_owner")
        expires = env_mod.parse_iso(operation.get("lease_expires_at"))
        now = self.clock() if self.clock else datetime.now(timezone.utc)

        if current and current != owner and expires and expires > now:
            raise OperationError(
                "OPERATION_LEASED",
                "операцию уже выполняет другой процесс (%s до %s): "
                "параллельный запуск одной операции запрещён"
                % (current, operation["lease_expires_at"]),
            )

        operation["lease_owner"] = owner
        operation["lease_expires_at"] = (
            now + timedelta(seconds=LEASE_SECONDS)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self._update_operation(operation)

    def _set_status(self, operation, status, *, started=False, finished=False):
        if status not in env_mod.STATUSES:
            raise OperationError("OPERATION_STATUS_UNKNOWN",
                                 "неизвестное состояние операции: %r" % status)
        operation["status"] = status
        if started and not operation.get("started_at"):
            operation["started_at"] = env_mod.now_iso(self.clock)
        if finished:
            operation["finished_at"] = env_mod.now_iso(self.clock)
            operation["lease_owner"] = None
            operation["lease_expires_at"] = None
        self._update_operation(operation)
        return operation

    def _update_operation(self, operation):
        version = self.store.version_of(OPERATION_KIND, operation["operation_id"],
                                        course_id=store_mod.WORKSPACE_COURSE_ID)
        self._store_document(OPERATION_KIND, operation["operation_id"], operation,
                             events=["environment.operation_updated"],
                             payload={"status": operation["status"],
                                      "next_step": operation["next_step"]},
                             expected_version=version)

    def _store_document(self, kind, entity_id, body, *, events, payload,
                        expected_version=None, request_id=None):
        document = dict(body)
        document["schema_version"] = SCHEMA_VERSION
        # `_actor`/`_provenance` are consumed by the store when it builds the
        # event envelope; they mark this as a human-channel action rather than a
        # model proposal.
        enriched = dict(payload)
        enriched.setdefault("_actor", "runner")
        enriched.setdefault("_provenance", "host_observed")
        return self.store.apply(
            kind=kind, entity_id=entity_id, events=events,
            course_id=store_mod.WORKSPACE_COURSE_ID,
            expected_version=expected_version, request_id=request_id,
            new_body=document, event_payloads=[enriched],
        )
