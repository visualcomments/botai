#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end check of the MCP tool surface and the host profile verdict.

Asserts what the surface *cannot* do and what the certification *refuses to*
claim:

* no tool runs a command, edits a file, reads an arbitrary path, fetches a URL,
  or grants an approval;
* a tool call cannot reach outside its course — an excluded path and a traversal
  are both refused;
* a careful host config is `managed`; a wildcard allow, a default of allow, a
  privileged subagent and an enabled foreign plugin are each `HOST_UNVERIFIED`
  with the specific reason named;
* a host with no adapter is `compatibility`, never `managed`.

No network, no host process: the surface is exercised through the same dispatch
the stdio server uses.

Run:
    python3 scripts/checks/mcp_surface.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import mcp_server as M  # noqa: E402
from botai_core import adapters as A, course, mcp_handlers  # noqa: E402

failures = 0
COURSE = "minimal-diff"
LEARNER = "11111111-1111-4111-8111-111111111111"


def check(name, condition, detail=""):
    global failures
    if condition:
        print("  [ok]   %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        failures += 1


def main():
    tmp = Path(tempfile.mkdtemp(prefix="botai-mcpsurface-"))
    root = tmp / "workspace"
    course_dir = root / "courses" / COURSE
    course_dir.parent.mkdir(parents=True)
    shutil.copytree(REPO / "examples" / "minimal-course", course_dir)
    course.accept(root, course_dir, slug=COURSE,
                  repository_root="courses/%s" % COURSE)

    # Bait under the excluded root: it must be unreachable through every tool.
    bait = root / ".botai" / "materials" / COURSE / "bait" / "teacher"
    bait.mkdir(parents=True, exist_ok=True)
    (bait / "answers.md").write_text("ЭТАЛОННЫЙ ОТВЕТ", encoding="utf-8")

    server = M.Server(root, LEARNER)
    print("workspace: %s" % root)

    print()
    print("### 1. поверхность инструментов узкая по построению")
    check("инструментов объявлено: %d" % len(M.TOOLS), len(M.TOOLS) >= 18)
    for name in ("bash", "shell", "edit", "write", "webfetch", "task",
                 "approve", "action_approve", "set_permission", "write_approval",
                 "git_commit", "git_push", "open_pull_request", "grade_set",
                 "role_set", "consent_set", "privacy_export", "privacy_delete"):
        check("инструмента %r нет" % name, name not in M.TOOL_NAMES, name)

    print()
    print("### 2. нет инструмента применения среды")
    check("env_apply отсутствует", "env_apply" not in M.TOOL_NAMES)
    check("env_plan отсутствует", "env_plan" not in M.TOOL_NAMES)
    check("env_status присутствует как чтение", "env_status" in M.TOOL_NAMES)

    print()
    print("### 3. ни один инструмент не принимает путь, URL или роль")
    offenders = {}
    for tool in M.TOOLS:
        if tool["name"] == "material_read":
            continue
        props = set((tool["input"].get("properties") or {}))
        bad = props & {"path", "url", "root", "role", "approved", "permission",
                       "shell", "command", "argv"}
        if bad:
            offenders[tool["name"]] = sorted(bad)
    check("нет инструментов с path/url/role/approved", not offenders, str(offenders))

    print()
    print("### 4. материал вне разрешённых корней недостижим")
    for path in ("teacher/answers.md", "../../etc/passwd", "/etc/passwd",
                 "solutions/x.md"):
        result = server.call("material_read", {"course_id": COURSE, "path": path})
        check("чтение %r отклонено" % path, result["ok"] is False, str(result)[:160])

    print()
    print("### 5. неизвестные аргументы и инструменты отклоняются")
    result = server.call("course_context", {"course_id": COURSE, "role": "teacher"})
    check("role=teacher отклонён", result["ok"] is False
          and result["code"] == "ARGUMENT_UNKNOWN", str(result)[:160])
    result = server.call("run_shell", {"command": "ls"})
    check("run_shell отклонён", result["ok"] is False
          and result["code"] == "TOOL_UNKNOWN", str(result)[:160])

    print()
    print("### 6. решения считаются ядром, а не моделью")
    result = server.call("policy_check", {"course_id": COURSE,
                                          "assignment_id": "practice-diff",
                                          "level": "SOLUTION"})
    decision = result["data"]["decision"]
    check("решение называет правило", bool(decision.get("rule_ref")), str(decision)[:200])
    undeclared = server.call("policy_check", {"course_id": COURSE,
                                              "assignment_id": "nope",
                                              "level": "SOLUTION"})
    check("необъявленное задание — строгий режим",
          undeclared["data"]["decision"]["decision"] == "deny", str(undeclared)[:200])

    print()
    print("### 7. рукопожатие называет свои пределы")
    text = json.dumps(server.handshake(), ensure_ascii=False).lower()
    check("сказано, что это не песочница", "не является песочницей" in text)
    check("сказано про обход", "обойти" in text or "bash" in text)
    check("сказано про текстовый спойлер", "спойлер" in text or "текст" in text)

    print()
    print("### 8. аккуратный профиль сертифицируется")
    # Written against the host's own vocabulary: denying `shell` or `execute`
    # would be decorative, because OpenCode implements neither, while `read` and
    # `apply_patch` would stay reachable.
    careful = {"permission": {"default": "deny",
                              "allow": list(A.allowed_tool_names())
                                       + list(A.READ_ONLY_ALLOWED),
                              "deny": list(A.bypass_tools_for("opencode"))}}
    verdict, problems = A.inspect_config(careful, host="opencode")
    check("аккуратный конфиг -> managed", verdict == A.PROFILE_MANAGED, str(problems))

    print()
    print("### 8b. конфиг с запретами несуществующих инструментов не проходит")
    decorative = {"permission": {
        "default": "deny",
        "allow": list(A.allowed_tool_names()) + ["question"],
        "deny": ["shell", "run_command", "execute", "fetch", "patch",
                 "multiedit", "notebook_edit"],
    }}
    verdict, problems = A.inspect_config(decorative, host="opencode")
    check("декоративный запрет не сертифицирован",
          verdict != A.PROFILE_MANAGED, verdict)
    named = " ".join(p["message_ru"] for p in problems)
    check("назван настоящий обходной инструмент read",
          "read" in named, named[:200])

    print()
    print("### 8c. незакреплённая версия не сертифицируется")
    pinned = A.adapter_check("opencode", document=careful,
                             version=A.TESTED_VERSIONS["opencode"])
    check("закреплённая версия -> managed",
          pinned["verdict"] == A.PROFILE_MANAGED, str(pinned["problems"]))
    other = A.adapter_check("opencode", document=careful, version="0.0.1")
    check("другая версия -> не managed",
          other["verdict"] == "HOST_UNVERIFIED", other["verdict"])

    print()
    print("### 9. каждая лазейка названа")
    deny_all = list(A.bypass_tools_for("opencode"))
    holes = {
        "шаблон botai_*": {"permission": {"default": "deny", "allow": ["botai_*"],
                                          "deny": deny_all}},
        "шаблон *": {"permission": {"default": "deny", "*": "allow",
                                    "deny": deny_all}},
        "default=allow": {"permission": {"default": "allow",
                                         "allow": list(A.allowed_tool_names()),
                                         "deny": deny_all}},
        "подгаент с bash": {**careful, "agent": {"h": {"tools": ["bash"]}}},
        "внешний плагин": {**careful, "mcp": {"other": {"enabled": True}}},
        "нет раздела разрешений": {},
    }
    for label, document in holes.items():
        verdict, problems = A.inspect_config(document, host="opencode")
        check("%s -> не managed" % label, verdict != A.PROFILE_MANAGED, verdict)
        check("%s -> причина названа" % label, bool(problems), label)

    print()
    print("### 10. хост без адаптера не сертифицируется")
    report = A.adapter_check("not-a-real-host")
    check("вердикт compatibility", report["verdict"] == A.PROFILE_COMPATIBILITY,
          report["verdict"])
    said = " ".join(p["message_ru"] for p in report["problems"])
    check("сказано, что ограничения не заявляются",
          "не заявляются" in said, said[:160])

    print()
    print("### 11. проверка без конфига не выдаёт managed")
    report = A.adapter_check("opencode", version=None)
    check("вердикт HOST_UNVERIFIED", report["verdict"] == "HOST_UNVERIFIED",
          report["verdict"])

    print()
    print("### 12. проверка на настоящем конфиге репозитория")
    real = REPO / "opencode.json"
    if real.is_file():
        # The CLI detects the installed version; this call must pass it too, or
        # an otherwise-correct config reports HOST_VERSION_UNKNOWN.
        import cli as cli_module
        detected = cli_module._detect_host_version("opencode")
        report = A.adapter_check("opencode", config_path=real, version=detected)
        check("реальный конфиг проверен, вердикт вынесен",
              report["verdict"] in (A.PROFILE_MANAGED, "HOST_UNVERIFIED"),
              report["verdict"])
        print("       вердикт: %s, замечаний: %d"
              % (report["verdict"], len(report["problems"])))
        check("managed не выдан без доказательства",
              report["verdict"] != A.PROFILE_MANAGED or not report["problems"],
              str(report["problems"])[:200])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures == 0:
        print("MCP SURFACE OK")
    else:
        print("MCP SURFACE FAILURES: %d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
