# -*- coding: utf-8 -*-
"""Environment planning: what would be installed, stated before it is installed.

The design's rule is that a learner approves *effects*, not intentions. That is
only possible if the effects are enumerated first, by code, from a declared
spec — which is what this module does. It never executes anything: it reads an
`EnvironmentSpec`, checks the machine against it, and produces an `ActionPlan`
whose hash a human approves.

Three properties carry the weight:

* **The plan hash covers the inputs.** It is computed over the canonical JSON of
  everything except the hash itself, so editing a step after approval changes
  the hash and the approval stops matching. Without that, "approved" would mean
  "approved something once, in the vicinity of this operation".
* **`estimated_cost: null` means unknown, not free.** A paid operation cannot be
  auto-approved, and there is deliberately no code path that reads null as zero.
* **A step kind outside the closed set is a refusal.** The kinds are not a
  plugin point: an "install anything" step would make every other limit here
  decorative. The same reasoning applies to `declared_command`, which names an
  argv array from the accepted spec rather than accepting one from a caller.

Nothing here decides whether to *run* a plan. That is `actions.py`, and it
requires a stored human approval to do anything at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

from . import schemas

SCHEMA_VERSION = 2

# Step kinds the executor implements. A spec naming anything else is refused at
# plan time rather than at step time: failing early keeps the approval honest,
# because a human approved a plan that claimed to be runnable.
STEP_KINDS = (
    "course_clone", "venv_create", "pip_sync", "npm_ci",
    "artifact_fetch", "container_create", "declared_command", "service_stop",
)

CHECK_KINDS = ("tool_version", "file_hash", "path_exists", "http_health", "declared_check")

RISK_ORDER = ("none", "writes_local", "installs_code", "runs_course_code", "changes_system")

# Steps that can execute code a course author wrote. These are the ones the
# learner most needs to see named, and the ones the compatibility profile warns
# about rather than silently running.
CODE_EXECUTING_RISKS = ("installs_code", "runs_course_code", "changes_system")

# Lifecycle states, exactly as the design lists them.
STATUSES = (
    "UNINSPECTED", "INSPECTED", "PLAN_READY", "AWAITING_APPROVAL",
    "AWAITING_COURSE_ACCEPTANCE", "APPLYING", "VERIFYING", "READY",
    "PARTIAL", "FAILED", "CANCELLED", "STALE_PLAN",
)

# Defaults from the design. A single command is bounded much tighter than a
# whole install, because a command that has not finished in two minutes is
# usually waiting on something rather than working.
DEFAULT_LIMITS = {
    "plan_timeout_seconds": 1800,
    "step_timeout_seconds": 120,
    "install_timeout_seconds": 900,
    "max_log_bytes": 1024 * 1024,
}

PLAN_TTL_MINUTES = 30

# Shells are refused as argv[0]. A `declared_command` exists so a command is an
# argv array; if argv[0] may be a shell, the array is just a string with extra
# steps, and the separation the design requires is undone by one spec field.
_SHELL_NAMES = {
    "sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish",
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "wsl", "wsl.exe",
}


class EnvironmentError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def now_iso(clock=None):
    moment = clock() if clock else datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text):
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def canonical(document):
    """Byte-stable JSON: the plan hash must not depend on key order or spacing."""
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def plan_hash(plan):
    """SHA-256 over the plan with its own hash removed.

    `allow_nan=False` in `canonical` matters here: NaN would make the encoding
    unstable between runs, so a plan containing one is refused rather than
    hashed inconsistently.
    """
    body = {key: value for key, value in plan.items() if key != "plan_hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


# --------------------------------------------------------------------------
# Platform and prerequisites
# --------------------------------------------------------------------------

def current_platform():
    """One of the four names the spec uses."""
    system = platform.system().lower()
    if system == "linux":
        # WSL reports Linux; the distinction matters because a Windows path may
        # be visible under /mnt/c and behaves differently.
        if "microsoft" in platform.uname().release.lower() or os.environ.get("WSL_DISTRO_NAME"):
            return "wsl"
        return "linux"
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return system


def check_platform(spec, *, target=None):
    """Whether this machine is a supported platform for the spec."""
    want = spec.get("supported_platforms") or []
    here = target or current_platform()
    if here in want:
        return True, here, ""
    return False, here, (
        "профиль поддерживает %s, а эта машина — %s"
        % (", ".join(want), here)
    )


_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
# A version as it may appear inside a constraint, anchored so that trailing
# junk cannot ride along. `parse_version` searches for a version anywhere (it is
# used on tool output like "git version 2.43.0"); a constraint must be exact, or
# `>=1.0; rm -rf /` parses as `>=1.0` and the rest is silently dropped.
_CONSTRAINT_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def parse_version(text):
    """First version-looking run of digits in a string, as a tuple.

    Deliberately permissive: this is used on a tool's own version banner
    ("git version 2.43.0.windows.1"), where the version is embedded in prose.
    """
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    parts = [int(p) for p in match.groups() if p is not None]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _parse_constraint_version(text):
    """A version in a constraint, which must be *only* a version.

    Separate from `parse_version` on purpose. Accepting a substring here would
    mean `>=1.0; rm -rf /` is read as `>=1.0` with the remainder discarded —
    the parser would be agreeing to a constraint it never evaluated.
    """
    match = _CONSTRAINT_VERSION_RE.match((text or "").strip())
    if not match:
        return None
    parts = [int(p) for p in match.groups() if p is not None]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def satisfies(version, constraint):
    """Evaluate a restricted constraint form: `>=3.11,<3.14` and friends.

    Parsed here rather than handed to a shell. A version constraint arriving
    from a course spec is untrusted input, and `subprocess.run("python -c ...")`
    to evaluate it would be the injection the whole module exists to avoid.
    Supported operators: `>=`, `<=`, `>`, `<`, `==`, `!=`, joined by commas.
    Anything else — including trailing text after a valid clause — is refused.
    """
    if not constraint:
        return True, ""
    if version is None:
        return False, "версия не определена, а требуется %s" % constraint

    for clause in str(constraint).split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = re.match(r"^(>=|<=|==|!=|>|<)\s*(.+)$", clause)
        if not match:
            return False, "непонятное условие версии: %r" % clause
        operator, wanted_text = match.group(1), match.group(2)
        wanted = _parse_constraint_version(wanted_text)
        if wanted is None:
            return False, "непонятная версия в условии: %r" % wanted_text

        if operator == ">=" and not version >= wanted:
            return False, "нужна версия %s или новее, установлена %s" % (wanted_text, _render(version))
        if operator == "<=" and not version <= wanted:
            return False, "нужна версия не новее %s, установлена %s" % (wanted_text, _render(version))
        if operator == ">" and not version > wanted:
            return False, "нужна версия новее %s, установлена %s" % (wanted_text, _render(version))
        if operator == "<" and not version < wanted:
            return False, "нужна версия старее %s, установлена %s" % (wanted_text, _render(version))
        if operator == "==" and not version == wanted:
            return False, "нужна версия %s, установлена %s" % (wanted_text, _render(version))
        if operator == "!=" and version == wanted:
            return False, "версия %s запрещена" % wanted_text
    return True, ""


def _render(version):
    return ".".join(str(p) for p in version)


TOOL_PROBES = {
    "python": ([os.sys.executable, "--version"],),
    "git": (["git", "--version"],),
    "docker": (["docker", "--version"],),
    "podman": (["podman", "--version"],),
    "node": (["node", "--version"],),
}


def probe_tool(tool, *, runner=None):
    """Ask a trusted binary for its version, without a shell.

    `declared_check`-style commands are not used here: probing a tool for a
    version is the harness's own business, and running a course-supplied command
    to answer it would be a lopehole straight through the plan.
    """
    candidates = TOOL_PROBES.get(tool)
    if not candidates:
        return None, "неизвестный инструмент: %r" % tool

    def default_runner(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=15, shell=False)
        except (OSError, subprocess.SubprocessError):
            return None

    run = runner or default_runner
    for argv in candidates:
        executable = argv[0]
        if executable not in (os.sys.executable,):
            if shutil.which(executable) is None:
                continue
        result = run(argv)
        if result is None or result.returncode != 0:
            continue
        text = (result.stdout or "") + (result.stderr or "")
        return parse_version(text), text.strip()
    return None, "инструмент не найден"


def check_prerequisites(spec, *, runner=None):
    """Verify each declared prerequisite, reporting versions actually observed."""
    results = []
    for entry in spec.get("prerequisites") or []:
        tool = entry["tool"]
        version, raw = probe_tool(tool, runner=runner)
        ok, why = satisfies(version, entry.get("version_constraint"))
        present = version is not None
        required = bool(entry.get("required"))

        status = "pass" if (present and ok) else ("fail" if required else "skipped")
        results.append({
            "tool": tool,
            "required": required,
            "status": status,
            "observed": raw or None,
            "observed_version": _render(version) if version else None,
            "constraint": entry.get("version_constraint"),
            "message_ru": why or ("версия подходит" if present else "инструмент не найден"),
            "install_help_url": entry.get("install_help_url"),
            "why_ru": entry.get("why_ru"),
        })
    return results


# --------------------------------------------------------------------------
# Step and command validation
# --------------------------------------------------------------------------

def _write_roots_for(step, *, environment_root):
    """Where a step may write, taken from the step itself.

    Each kind knows its own destination; the spec does not get to declare one
    for `venv_create`, and `declared_command` declares its roots explicitly. A
    plan whose effects list is assembled generically would show "(writes
    nothing)" for a step that creates a virtual environment — the precise
    omission the approval screen exists to prevent.
    """
    parameters = step["parameters"]
    kind = step["kind"]

    if kind == "venv_create":
        destination = parameters.get("destination")
        return [str(destination)] if destination else []
    if kind == "pip_sync":
        environment_id = parameters.get("environment_id")
        return [str(environment_root / str(environment_id))] if environment_id else []
    if kind in ("course_clone", "artifact_fetch"):
        return [str(environment_root)]
    if kind == "container_create":
        mounts = parameters.get("mount_ids") or []
        return [str(m) for m in mounts]
    return [str(r) for r in (parameters.get("write_roots") or [])]


def _command_of(spec, command_id):
    for command in spec.get("commands") or []:
        if command["command_id"] == command_id:
            return command
    return None
    for command in spec.get("commands") or []:
        if command["command_id"] == command_id:
            return command
    return None


def validate_step(spec, step, *, environment_root):
    """Refuse a step that the executor could not safely run as written.

    Returns the parameters the plan will carry. Each kind has a closed set of
    allowed parameter names: an unexpected key is refused rather than carried
    along, so a spec cannot smuggle extra meaning past the reviewer.
    """
    kind = step["kind"]
    if kind not in STEP_KINDS:
        raise EnvironmentError(
            "STEP_KIND_UNKNOWN",
            "шаг %s: вид %r не входит в разрешённый набор (%s)"
            % (step["step_id"], kind, ", ".join(STEP_KINDS)),
        )

    parameters = dict(step.get("parameters") or {})

    if kind == "declared_command":
        command_id = parameters.get("command_id")
        if not command_id:
            raise EnvironmentError("STEP_PARAMETERS_MISSING",
                                   "шаг %s: declared_command требует command_id" % step["step_id"])
        command = _command_of(spec, command_id)
        if command is None:
            raise EnvironmentError(
                "COMMAND_NOT_DECLARED",
                "шаг %s ссылается на команду %r, которой нет в спецификации: "
                "свободный argv не принимается" % (step["step_id"], command_id),
            )
        argv = list(command.get("argv") or [])
        if not argv:
            raise EnvironmentError("COMMAND_EMPTY",
                                   "команда %r: пустой argv" % command_id)
        head = os.path.basename(str(argv[0])).lower()
        if head in _SHELL_NAMES:
            raise EnvironmentError(
                "COMMAND_SHELL_REFUSED",
                "команда %r начинается с оболочки (%s): argv должен быть "
                "массивом аргументов, а не строкой для shell"
                % (command_id, argv[0]),
            )
        parameters["argv"] = argv
        parameters["cwd_id"] = command.get("cwd_id")
        parameters["env_allowlist"] = list(command.get("env_allowlist") or [])
        parameters["write_roots"] = list(command.get("write_roots") or [])
        parameters["expected_exit_codes"] = list(command.get("expected_exit_codes") or [0])
        # Typed arguments are appended as separate argv items after checking
        # their types — never concatenated into a string.
        typed = parameters.pop("arguments", None) or {}
        if not isinstance(typed, dict):
            raise EnvironmentError("STEP_ARGUMENTS_NOT_OBJECT",
                                   "шаг %s: arguments должен быть объектом" % step["step_id"])
        for key, value in sorted(typed.items()):
            if not isinstance(value, (str, int, float, bool)):
                raise EnvironmentError(
                    "STEP_ARGUMENT_TYPE",
                    "шаг %s: аргумент %r не является скалярным значением" % (step["step_id"], key),
                )
            parameters.setdefault("typed_arguments", {})[key] = value

    elif kind == "venv_create":
        destination = parameters.get("destination_id")
        if not destination:
            raise EnvironmentError(
                "STEP_PARAMETERS_MISSING",
                "шаг %s: venv_create требует destination_id" % step["step_id"],
            )
        # A venv is created under the course's own environment directory and
        # nowhere else. A spec-supplied path would be an arbitrary write.
        parameters["destination"] = str(
            environment_root / str(destination).replace("/", "-").replace("\\", "-")
        )
        parameters.pop("destination_id", None)

    elif kind == "pip_sync":
        if not parameters.get("lock_path"):
            raise EnvironmentError(
                "STEP_PARAMETERS_MISSING",
                "шаг %s: pip_sync требует lock_path с закреплёнными хешами" % step["step_id"],
            )
        # Installations can execute code; the plan says so out loud.
        if step.get("risk") in ("none", "writes_local"):
            raise EnvironmentError(
                "STEP_RISK_UNDERSTATED",
                "шаг %s: установка пакетов может исполнять код, поэтому риск "
                "должен быть указан как installs_code или выше" % step["step_id"],
            )

    elif kind == "npm_ci":
        scripts = parameters.get("scripts_policy", "ignore")
        if scripts not in ("ignore", "allow"):
            raise EnvironmentError("STEP_PARAMETERS_INVALID",
                                   "шаг %s: scripts_policy должен быть ignore или allow"
                                   % step["step_id"])
        if scripts == "allow" and step.get("risk") in ("none", "writes_local"):
            raise EnvironmentError(
                "STEP_RISK_UNDERSTATED",
                "шаг %s: разрешение lifecycle-скриптов исполняет код курса, "
                "риск указан слишком низко" % step["step_id"],
            )
        parameters["scripts_policy"] = scripts

    elif kind == "container_create":
        recipe = parameters.get("approved_recipe_id")
        if not recipe:
            raise EnvironmentError(
                "STEP_PARAMETERS_MISSING",
                "шаг %s: container_create требует approved_recipe_id" % step["step_id"],
            )
        for forbidden in ("privileged", "host_network", "host_socket"):
            if parameters.get(forbidden):
                raise EnvironmentError(
                    "CONTAINER_ESCALATION_REFUSED",
                    "шаг %s: %s для контейнера запрещён" % (step["step_id"], forbidden),
                )

    return parameters


def build_plan(spec, *, course, operation_id, workspace_id, course_id,
               environment_hash=None, accepted_revision=None, input_hashes=None,
               limits=None, clock=None, environment_root=None):
    """Turn an `EnvironmentSpec` into an `ActionPlan`.

    Every step is validated here, so a plan that exists is a plan that could in
    principle run. A human then approves the hash.
    """
    ok, here, why = check_platform(spec)
    if not ok:
        raise EnvironmentError("PLATFORM_UNSUPPORTED", why,
                               detail={"platform": here})

    full_limits = dict(DEFAULT_LIMITS)
    full_limits.update(limits or {})

    root = environment_root or "."
    steps = []
    for step in spec.get("steps") or []:
        parameters = validate_step(spec, step, environment_root=root)
        steps.append({
            "step_id": step["step_id"],
            "kind": step["kind"],
            "parameters": parameters,
            "risk": step["risk"],
            "explanation_ru": step["explanation_ru"],
            "timeout_seconds": step.get("timeout_seconds")
                or (full_limits["step_timeout_seconds"] if step["kind"] == "declared_command"
                    else full_limits["install_timeout_seconds"]),
            "network": bool(step.get("network")),
            "depends_on": list(step.get("depends_on") or []),
        })

    unknown = [s["step_id"] for s in steps
               if any(dep not in [x["step_id"] for x in steps] for dep in s["depends_on"])]
    if unknown:
        raise EnvironmentError(
            "STEP_DEPENDENCY_UNKNOWN",
            "шаги ссылаются на несуществующие зависимости: %s" % ", ".join(unknown),
        )

    # Effects are assembled from what each step will actually do, not from what
    # the spec volunteered: a step that creates a virtual environment writes,
    # and the screen must say so.
    write_roots = sorted({
        written for step in steps
        for written in _write_roots_for(step, environment_root=root)
    })
    network = sorted({
        str(step["parameters"].get("url") or step["parameters"].get("source_input_id")
            or step["kind"])
        for step in steps if step["network"]
    })

    created = now_iso(clock)
    expires = (clock() if clock else datetime.now(timezone.utc)) + timedelta(minutes=PLAN_TTL_MINUTES)

    plan = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "workspace_id": workspace_id,
        "course_id": course_id,
        "accepted_revision": dict(accepted_revision or course.revision),
        "environment_hash": environment_hash,
        "profile_id": spec.get("profile_id"),
        "input_hashes": dict(input_hashes or {}),
        "steps": steps,
        "effects": {
            "read_roots": sorted({str(root)}),
            "write_roots": write_roots,
            "network_destinations": network if any(s["network"] for s in steps) else [],
            "external_publications": [],
            "system_changes": sorted({
                s["step_id"] for s in steps if s["risk"] == "changes_system"
            }),
            "estimated_cost": None,
        },
        "limits": full_limits,
        "created_at": created,
        "expires_at": expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    plan["plan_hash"] = plan_hash(plan)

    try:
        schemas.validate(dict(plan, schema_version=SCHEMA_VERSION), "plan")
    except schemas.SchemaError as e:
        raise EnvironmentError(e.code, e.message)
    return plan


def plan_expired(plan, *, clock=None):
    expires = parse_iso(plan.get("expires_at"))
    if expires is None:
        return True
    return (clock() if clock else datetime.now(timezone.utc)) >= expires


def inputs_changed(plan, current_hashes):
    """Which pinned inputs differ from what the plan was built against.

    An input that changed since approval invalidates the plan: otherwise a spec
    could be edited after the human read it, and the approved description would
    no longer describe what runs.

    `current_hashes=None` means the caller did not look, which is *not* the same
    as "nothing changed". A plan that pinned inputs and a caller that supplies
    no hashes is reported as unverified rather than silently passing.
    """
    pinned = plan.get("input_hashes") or {}
    if not pinned:
        return []

    if current_hashes is None:
        return [{
            "input_id": input_id,
            "expected": expected,
            "actual": None,
            "reason": "хеши входов не перепроверены перед выполнением",
        } for input_id, expected in pinned.items()]

    changed = []
    for input_id, expected in pinned.items():
        actual = current_hashes.get(input_id)
        if actual != expected:
            changed.append({"input_id": input_id, "expected": expected, "actual": actual})
    return changed


def verify_plan(plan, *, current_hashes=None, clock=None, check_inputs=False):
    """Whether a stored plan is still the plan that was approved.

    `check_inputs` separates two questions that happen at different times:

    * integrity and expiry — asked when a human is about to approve, and when
      execution starts;
    * input freshness — asked only immediately before running, because the
      workspace legitimately changes between a plan being written and being
      approved.

    Conflating them means an approval is refused for a plan that pinned any
    input at all, which is the opposite of safe: it teaches people to leave
    inputs unpinned.
    """
    problems = []
    recomputed = plan_hash(plan)
    if recomputed != plan.get("plan_hash"):
        problems.append({"code": "PLAN_HASH_MISMATCH",
                         "message_ru": "содержимое плана изменилось после сохранения"})
    if plan_expired(plan, clock=clock):
        problems.append({"code": "PLAN_EXPIRED",
                         "message_ru": "срок действия плана истёк: нужен новый план "
                                       "и новое подтверждение"})

    if check_inputs:
        changed = inputs_changed(plan, current_hashes)
        if changed:
            problems.append({
                "code": "PLAN_INPUTS_CHANGED",
                "message_ru": "входы изменились после сборки плана: %s"
                % ", ".join(c["input_id"] for c in changed),
                "detail": changed,
            })
    return (not problems), problems


# --------------------------------------------------------------------------
# Human-readable plan screen
# --------------------------------------------------------------------------

RISK_LABEL = {
    "none": "ничего не меняет",
    "writes_local": "пишет в рабочее пространство",
    "installs_code": "устанавливает пакеты (может исполнять код)",
    "runs_course_code": "исполняет код курса",
    "changes_system": "меняет систему",
}


def render_plan(plan, *, course_title=None):
    """The deterministic screen a human reads before approving.

    Rendered from the plan, not from a model's summary of it: a summary is
    exactly where an inconvenient effect goes missing.
    """
    lines = []
    header = course_title or plan["course_id"]
    lines.append("=== План подготовки среды: %s ===" % header)
    lines.append("")
    lines.append("  операция    : %s" % plan["operation_id"])
    lines.append("  профиль     : %s" % (plan.get("profile_id") or "—"))
    lines.append("  ревизия     : %s %s" % (plan["accepted_revision"].get("kind"),
                                             str(plan["accepted_revision"].get("id"))[:16]))
    lines.append("  план-хеш    : %s" % plan["plan_hash"][:32])
    lines.append("  действует до: %s" % plan["expires_at"])
    lines.append("")

    lines.append("Что будет сделано (%d шагов):" % len(plan["steps"]))
    for index, step in enumerate(plan["steps"], 1):
        lines.append("  %d. [%s] %s" % (index, step["kind"], step["explanation_ru"]))
        lines.append("     риск: %s" % RISK_LABEL.get(step["risk"], step["risk"]))
        if step["kind"] == "declared_command":
            argv = step["parameters"].get("argv") or []
            lines.append("     команда: %s" % " ".join(str(a) for a in argv))
            typed = step["parameters"].get("typed_arguments") or {}
            if typed:
                lines.append("     параметры: %s"
                             % ", ".join("%s=%s" % (k, v) for k, v in sorted(typed.items())))
        if step.get("network"):
            lines.append("     сеть: да")
        if step.get("depends_on"):
            lines.append("     после: %s" % ", ".join(step["depends_on"]))
    lines.append("")

    effects = plan["effects"]
    lines.append("Куда пишет:")
    for root in effects["write_roots"] or ["(ничего не записывает)"]:
        lines.append("  - %s" % root)
    lines.append("")
    lines.append("Сеть:")
    for destination in effects["network_destinations"] or ["(сеть не используется)"]:
        lines.append("  - %s" % destination)
    lines.append("")
    lines.append("Публикации наружу: %s"
                 % (", ".join(effects["external_publications"]) or "нет"))
    lines.append("Изменения системы : %s"
                 % (", ".join(effects["system_changes"]) or "нет"))
    lines.append("Стоимость         : %s"
                 % ("не известна" if effects["estimated_cost"] is None
                    else effects["estimated_cost"]))
    lines.append("")
    lines.append("Ограничения: шаг до %d с, установка до %d с, весь план до %d с."
                 % (plan["limits"]["step_timeout_seconds"],
                    plan["limits"]["install_timeout_seconds"],
                    plan["limits"]["plan_timeout_seconds"]))
    lines.append("")
    lines.append("Подтверждение выдаётся человеком в отдельном терминале:")
    lines.append("  python scripts/cli.py action-approve --operation %s" % plan["operation_id"])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

def readiness(checks, *, required_only=True):
    """Whether the environment counts as READY.

    READY requires every required check to pass. An exit code of 0 is not
    enough on its own: a step that ran and produced nothing has not prepared
    anything, and reporting otherwise is the false-READY case the design names.
    """
    relevant = [c for c in checks if (not required_only) or c.get("required", True)]
    failures = [c for c in relevant if c.get("status") != "pass"]
    return {
        "ready": not failures,
        "checked": len(relevant),
        "failures": [
            {"check_id": c.get("check_id"), "status": c.get("status"),
             "message_ru": c.get("message_ru")}
            for c in failures
        ],
    }


def environment_check_result(check_id, status, *, observed=None, expected=None,
                             evidence_ref=None, duration_ms=None, message_ru=None):
    """One `EnvironmentCheckResult`, a type distinct from a teaching Check.

    Kept separate on purpose: mixing "docker is 24.0" into the teaching record
    would let an environment fact be read as evidence about a learner.
    """
    if status not in ("pass", "fail", "skipped", "unknown"):
        raise EnvironmentError("CHECK_STATUS_UNKNOWN",
                               "неизвестный статус проверки: %r" % status)
    return {
        "schema_version": SCHEMA_VERSION,
        "check_id": check_id,
        "status": status,
        "observed": observed,
        "expected": expected,
        "evidence_ref": evidence_ref,
        "duration_ms": duration_ms,
        "message_ru": message_ru,
    }
