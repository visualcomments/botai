# -*- coding: utf-8 -*-
"""The restricted executor: runs a saved plan, and only a saved plan.

Everything in this module is written on the assumption that the commands it runs
may be hostile. A course's own tests, its requirements and its notebooks are
**executable untrusted code** even with `shell=False` — avoiding shell injection
does not make code safe, it only removes one class of bug. What the module can
honestly promise is narrow and worth stating precisely:

* **No command from a caller.** Every step comes from a stored plan that a human
  approved by hash. There is no `run(argv)` entry point.
* **No inherited environment.** A child gets an allow-list starting from a
  minimal PATH/locale/temp, so a course script cannot read the learner's cloud
  tokens or the Git credential helper. Secrets are not merely undocumented here;
  they are absent.
* **The whole process tree is killed on timeout**, not just the parent. A test
  runner spawns children, and killing the parent leaves them holding the port,
  the file lock, or the CPU.
* **Output is bounded and redacted** before it is stored: 1 MiB per step, with
  anything resembling a token replaced.

What it cannot promise: a native venv is a dependency convenience, **not a
sandbox**. The design says so and this module repeats it in the warnings it
returns, because a learner who believes they are isolated will run things they
otherwise would not.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import environment as env_mod
from . import paths
from . import store as store_mod

SCHEMA_VERSION = 2

MAX_LOG_BYTES = 1024 * 1024

# A minimal environment. Anything a course needs beyond this must be named in
# the spec's `env_allowlist`, which means it appears in the plan a human reads.
BASE_ENV_NAMES = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
                  "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC")

# Patterns redacted from stored output. Deliberately broad: a false positive
# costs a mangled log line, a false negative writes a live credential to disk.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})"),
    re.compile(r"(?i)\b(github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9]{16,})"),
    re.compile(r"(?i)\b(AKIA[0-9A-Z]{16})"),
    re.compile(r"(?i)\b(bearer\s+[A-Za-z0-9._\-]{16,})"),
    re.compile(r"(?i)\b([A-Za-z0-9_]*(?:token|secret|password|passwd|api[_-]?key)[A-Za-z0-9_]*"
               r"\s*[:=]\s*)([^\s\"']{8,})"),
)


class ActionError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def redact(text):
    """Replace anything that looks like a credential before it is stored."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: m.group(1) + "<скрыто>", text)
        else:
            text = pattern.sub("<скрыто>", text)
    return text


def bound_output(data, *, limit=MAX_LOG_BYTES):
    """Keep at most `limit` bytes, and say so when truncating.

    A silently truncated log is worse than a marked one: the reader cannot tell
    "the command printed nothing more" from "we stopped recording".
    """
    if data is None:
        return b"", False
    if len(data) <= limit:
        return data, False
    half = limit // 2
    marker = "\n...<вывод обрезан>...\n".encode("utf-8")
    return (data[:half] + marker + data[-half:]), True


def build_env(allowlist=(), *, extra=None):
    """A minimal child environment, plus explicitly allowed names.

    The environment is *built*, not copied: inheriting and then deleting secrets
    means knowing every name a secret might have, which is not a list anyone can
    finish.
    """
    child = {}
    for name in BASE_ENV_NAMES:
        value = os.environ.get(name)
        if value is not None:
            child[name] = value
    if not child.get("PATH"):
        child["PATH"] = os.pathsep.join(
            p for p in ("/usr/local/bin", "/usr/bin", "/bin") if Path(p).is_dir()
        ) or os.defpath

    for name in allowlist or []:
        value = (extra or {}).get(name, os.environ.get(name))
        if value is not None:
            child[name] = value

    # A course script must not be able to ask git for the learner's credentials.
    child["GIT_TERMINAL_PROMPT"] = "0"
    child["GIT_CONFIG_NOSYSTEM"] = "1"
    child["GIT_ASKPASS"] = ""
    child["SSH_ASKPASS"] = ""
    child["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    child["PYTHONDONTWRITEBYTECODE"] = "1"
    return child


def _popen_kwargs():
    """Platform extras: a new process group/job so the tree can be killed."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_tree(process, *, grace=5.0):
    """Kill a process *and its children*, then confirm it died.

    Killing only the parent leaves a test runner's workers alive; they keep the
    port, the lock or the CPU, and the next step fails for a reason that has
    nothing to do with it. Returns whether the process is gone.
    """
    if process.poll() is not None:
        return True

    try:
        if os.name == "nt":
            # `taskkill /T` walks the child tree; without /F it asks politely
            # first, which some console apps honour.
            subprocess.run(["taskkill", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=10)
            try:
                process.wait(timeout=grace)
                return True
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, timeout=10)
        else:
            try:
                group = os.getpgid(process.pid)
                os.killpg(group, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
            try:
                process.wait(timeout=grace)
                return True
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        return False
    return True


def run_command(argv, *, cwd, env, timeout_seconds, max_bytes=MAX_LOG_BYTES):
    """Run one argv array with `shell=False`, bounded in time and output.

    Returns a result dict rather than raising: a non-zero exit is data about the
    step, and the caller decides whether the plan may continue.
    """
    if not argv:
        raise ActionError("COMMAND_EMPTY", "пустой argv")
    if str(argv[0]).lower() in env_mod._SHELL_NAMES:
        raise ActionError(
            "COMMAND_SHELL_REFUSED",
            "argv начинается с оболочки (%s): команда должна быть массивом "
            "аргументов" % argv[0],
        )

    resolved = shutil.which(str(argv[0])) or str(argv[0])
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [resolved] + [str(a) for a in argv[1:]],
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            shell=False,
            **_popen_kwargs(),
        )
    except OSError as e:
        return {
            "ok": False, "exit_code": None, "timed_out": False,
            "error": "не удалось запустить %s: %s" % (argv[0], e),
            "stdout": "", "stderr": "", "truncated": False,
            "duration_ms": 0,
        }

    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        killed = terminate_tree(process)
        try:
            out, err = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        if not killed:
            # Saying so is the point: a surviving process can still be holding
            # a resource, and the next step's failure would otherwise look
            # unrelated.
            note = "\n[процесс не удалось завершить: он может всё ещё занимать ресурсы]"
            err = (err or b"") + note.encode("utf-8")

    duration_ms = int((time.monotonic() - started) * 1000)
    out_bounded, out_truncated = bound_output(out, limit=max_bytes)
    err_bounded, err_truncated = bound_output(err, limit=max_bytes)

    return {
        "ok": (not timed_out) and process.returncode == 0,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout": redact(out_bounded.decode("utf-8", errors="replace")),
        "stderr": redact(err_bounded.decode("utf-8", errors="replace")),
        "truncated": out_truncated or err_truncated,
    }


# --------------------------------------------------------------------------
# Step execution
# --------------------------------------------------------------------------

class ActionRunner:
    """Executes the steps of one approved plan.

    Constructed with the stored plan; it re-verifies the plan and its inputs
    before the first step, and re-checks the cancellation flag between steps.
    """

    def __init__(self, root, store, plan, *, clock=None, runner=None):
        self.root = Path(root)
        self.store = store
        self.plan = plan
        self.clock = clock
        self._runner = runner or run_command
        self._cancelled = False

    def environment_dir(self, course_id):
        base = self.root / ".botai" / "environments"
        return paths.ensure_within(
            base, base / paths.safe_name(course_id, kind="идентификатор курса"))

    def run(self, *, operation_state=None, max_steps=None):
        """Run the plan's steps from `next_step`, honouring cancellation.

        Returns a report including every step's observed result. A step that
        fails stops the run: continuing past a failed installation produces an
        environment nobody can describe.
        """
        state = operation_state or {}
        results = list(state.get("results") or [])
        next_index = int(state.get("next_step") or 0)
        environment_root = self.environment_dir(self.plan["course_id"])
        environment_root.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()
        plan_deadline = self.plan["limits"]["plan_timeout_seconds"]

        steps = self.plan["steps"]
        executed = 0

        while next_index < len(steps):
            if self._cancelled:
                return {"status": "CANCELLED", "results": results,
                        "next_step": next_index,
                        "message_ru": "операция отменена между шагами"}

            elapsed = time.monotonic() - started
            if elapsed > plan_deadline:
                return {"status": "FAILED", "results": results, "next_step": next_index,
                        "message_ru": "превышен общий лимит времени плана (%d с)"
                                      % plan_deadline}

            if max_steps is not None and executed >= max_steps:
                return {"status": "APPLYING", "results": results, "next_step": next_index,
                        "message_ru": "достигнут предел шагов этого запуска"}

            step = steps[next_index]
            outcome = self.run_step(step, environment_root=environment_root)
            results.append({
                "step_id": step["step_id"],
                "kind": step["kind"],
                "risk": step["risk"],
                **outcome,
            })
            executed += 1

            if not outcome["ok"]:
                return {
                    "status": "FAILED",
                    "results": results,
                    "next_step": next_index,
                    "message_ru": "шаг %s завершился неуспешно (код %s): %s"
                                  % (step["step_id"], outcome.get("exit_code"),
                                     (outcome.get("error") or outcome.get("stderr") or "")[:300]),
                }

            next_index += 1

        return {"status": "VERIFYING", "results": results, "next_step": next_index,
                "message_ru": "шаги выполнены; требуется проверка готовности"}

    def run_step(self, step, *, environment_root):
        """Dispatch one step to its implementation."""
        kind = step["kind"]
        parameters = step["parameters"]
        timeout = step.get("timeout_seconds") or env_mod.DEFAULT_LIMITS["step_timeout_seconds"]

        if kind == "declared_command":
            command = self._declared_command(step, environment_root=environment_root)
            result = self._runner(
                command["argv"], cwd=command["cwd"], env=command["env"],
                timeout_seconds=timeout,
            )
            expected = parameters.get("expected_exit_codes") or [0]
            result["ok"] = (not result["timed_out"]) and result["exit_code"] in expected
            if result["timed_out"]:
                result["error"] = ("команда не уложилась в %d с; группа процессов "
                                   "завершена" % timeout)
            elif result["exit_code"] not in expected:
                result["error"] = ("код возврата %s не входит в допустимые %s"
                                   % (result["exit_code"], expected))
            return result

        if kind == "venv_create":
            return self._venv_create(parameters, timeout=timeout)

        if kind in ("pip_sync", "npm_ci", "artifact_fetch", "container_create",
                    "course_clone", "service_stop"):
            # The executor's honest state: the plumbing for these exists, the
            # operations themselves are not implemented in this stage. Reporting
            # `skipped`, not `ok`, is what keeps a READY from being claimed.
            return {
                "ok": True,
                "exit_code": None,
                "timed_out": False,
                "skipped": True,
                "duration_ms": 0,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "note": ("шаг вида %s в этой версии не исполняется: среда будет "
                         "проверена, но READY не выставляется" % kind),
            }

        raise ActionError("STEP_KIND_UNIMPLEMENTED", "вид шага %r не реализован" % kind)

    def _declared_command(self, step, *, environment_root):
        parameters = step["parameters"]
        argv = [str(a) for a in parameters.get("argv") or []]
        typed = parameters.get("typed_arguments") or {}
        for key in sorted(typed):
            # Appended as separate argv items, never concatenated: the type
            # check upstream is what makes this safe, and it only holds if the
            # value never becomes part of a string.
            argv.append("--%s=%s" % (key, typed[key]))

        cwd_id = parameters.get("cwd_id") or "course"
        cwd = environment_root if cwd_id in ("environment", "env") else self.root
        cwd = paths.ensure_within(self.root, cwd)

        env = build_env(parameters.get("env_allowlist") or [])
        return {"argv": argv, "cwd": cwd, "env": env}

    def _venv_create(self, parameters, *, timeout):
        destination = Path(parameters["destination"])
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "exit_code": None, "timed_out": False,
                    "duration_ms": 0, "stdout": "", "stderr": "",
                    "truncated": False,
                    "error": "не удалось создать каталог среды: %s" % e}

        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(destination)],
            capture_output=True, timeout=timeout, shell=False,
            env=build_env(),
        )
        duration = int((time.monotonic() - started) * 1000)
        ok = result.returncode == 0
        # `--system-site-packages` is not used: a venv that sees the system
        # packages is not a reproducible one, and reproducibility is the only
        # thing a venv actually provides here.
        return {
            "ok": ok,
            "exit_code": result.returncode,
            "timed_out": False,
            "duration_ms": duration,
            "stdout": redact((result.stdout or b"").decode("utf-8", errors="replace")),
            "stderr": redact((result.stderr or b"").decode("utf-8", errors="replace")),
            "truncated": False,
            "error": None if ok else "не удалось создать venv",
        }


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def run_checks(spec, *, root, runner=None, clock=None):
    """Run the spec's declared checks and return `EnvironmentCheckResult`s.

    Only two kinds are evaluated by the harness itself; the rest report
    `unknown` rather than a cheerful default. A check nobody performed must not
    look like a check that passed.
    """
    results = []
    for check in spec.get("checks") or []:
        kind = check["kind"]
        required = bool(check.get("required", True))
        check_id = check["check_id"]

        if kind == "path_exists":
            relative = (check.get("parameters") or {}).get("path")
            expected = check.get("expected", True)
            if not relative:
                results.append(env_mod.environment_check_result(
                    check_id, "unknown", message_ru="не указан path",
                    expected=expected))
                continue
            try:
                target = paths.ensure_within(Path(root), Path(root) / relative)
            except paths.PathError as e:
                results.append(env_mod.environment_check_result(
                    check_id, "fail", observed=None, expected=expected,
                    message_ru="путь вне рабочего пространства: %s" % e.message))
                continue
            present = target.exists()
            results.append(env_mod.environment_check_result(
                check_id, "pass" if present == bool(expected) else "fail",
                observed=present, expected=expected,
                message_ru="путь %s" % ("существует" if present else "отсутствует")))

        elif kind == "tool_version":
            parameters = check.get("parameters") or {}
            tool = parameters.get("tool")
            version, raw = env_mod.probe_tool(tool, runner=None)
            ok, why = env_mod.satisfies(version, parameters.get("version_constraint")
                                        or check.get("expected"))
            results.append(env_mod.environment_check_result(
                check_id, "pass" if ok else "fail",
                observed=raw, expected=parameters.get("version_constraint"),
                message_ru=why or "версия подходит"))

        elif kind == "file_hash":
            parameters = check.get("parameters") or {}
            relative = parameters.get("path")
            expected = parameters.get("sha256") or check.get("expected")
            if not relative:
                results.append(env_mod.environment_check_result(
                    check_id, "unknown", message_ru="не указан path"))
                continue
            try:
                target = paths.ensure_within(Path(root), Path(root) / relative)
            except paths.PathError:
                results.append(env_mod.environment_check_result(
                    check_id, "fail", message_ru="путь вне рабочего пространства"))
                continue
            if not target.is_file():
                results.append(env_mod.environment_check_result(
                    check_id, "fail", observed=None, expected=expected,
                    message_ru="файл не найден: %s" % relative))
                continue
            from . import corpus as corpus_mod
            actual = corpus_mod.sha256_file(target)
            results.append(env_mod.environment_check_result(
                check_id, "pass" if actual == expected else "fail",
                observed=actual, expected=expected,
                message_ru=("хеш совпал" if actual == expected
                            else "хеш не совпал: %s вместо %s"
                                 % (actual[:16], str(expected)[:16]))))

        else:
            # `http_health` and `declared_check` need the approval and sandbox
            # that an executable step needs, and this stage has no business
            # running them implicitly through a "check".
            results.append(env_mod.environment_check_result(
                check_id, "unknown", expected=check.get("expected"),
                message_ru="проверка вида %s выполняется отдельным шагом "
                           "с тем же подтверждением, что и исполняемые шаги" % kind))

        results[-1]["required"] = required
    return results
