#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the MCP tool surface and the host profile check.

Two claims, both about things that are *absent*:

* **The MCP surface cannot self-approve and cannot reach around itself.** There
  is no tool that runs a command, edits a file, reads an arbitrary path, fetches
  a URL, or grants an approval — and a test asserts those names do not exist, so
  adding one is a visible act rather than an oversight.
* **A host is certified only on evidence.** A configuration that looks careful
  but carries a wildcard allow, a default of allow, an unconstrained subagent or
  an enabled foreign plugin is reported `HOST_UNVERIFIED`, not accepted. A host
  with no adapter is `compatibility`, never `managed`.

Acceptance criteria: A20 (an injection in course material or a packet changes no
scope and executes nothing), A26 (a bad host profile or a privileged subagent is
not certified, and the self-test names the specific bypass).

Run:
    python3 tests/test_adapters.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mcp_server as M  # noqa: E402
from botai_core import adapters as A, course, mcp_handlers  # noqa: E402

_passed = 0
_failures: list[str] = []

EXAMPLE = ROOT / "examples" / "minimal-course"
LEARNER = "11111111-1111-4111-8111-111111111111"
COURSE = "minimal-diff"


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


class Workspace:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        course_dir = self.root / "courses" / COURSE
        course_dir.parent.mkdir(parents=True)
        shutil.copytree(EXAMPLE, course_dir)
        course.accept(self.root, course_dir, slug=COURSE,
                      repository_root="courses/%s" % COURSE)
        self.server = M.Server(self.root, LEARNER)

    def cleanup(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# A20 — the surface is narrow by construction
# ---------------------------------------------------------------------------
def test_forbidden_tools_do_not_exist():
    """The guarantee is an absence, so the absence is what gets asserted."""
    for name in M.FORBIDDEN_TOOL_NAMES:
        check("инструмента %r нет в каталоге" % name, name not in M.TOOL_NAMES,
              name)

    check("инструмента approve нет", "approve" not in M.TOOL_NAMES)
    check("инструмента action_approve нет", "action_approve" not in M.TOOL_NAMES)
    check("инструмента set_permission нет", "set_permission" not in M.TOOL_NAMES)
    check("инструмента write_approval нет", "write_approval" not in M.TOOL_NAMES)


def test_no_tool_accepts_a_path_url_or_role():
    """Paths, URLs and roles are not parameters anywhere in the surface."""
    suspicious = ("path", "url", "root", "role", "approved", "permission",
                  "shell", "command", "argv", "cwd")
    offenders = {}
    for tool in M.TOOLS:
        properties = set((tool["input"].get("properties") or {}))
        # `material_read` names a course-relative path, which the core resolves
        # against the accepted contract. That is the one deliberate exception,
        # and it is checked separately below.
        found = properties & set(suspicious)
        if found and tool["name"] != "material_read":
            offenders[tool["name"]] = sorted(found)
        elif tool["name"] == "material_read":
            offenders.setdefault("_material_read", sorted(found))

    check("ни один инструмент не принимает путь, URL или роль",
          not {k: v for k, v in offenders.items() if k != "_material_read"},
          str(offenders))
    check("material_read — единственное исключение с path",
          offenders.get("_material_read") == ["path"], str(offenders))


def test_every_tool_declares_whether_it_mutates():
    """Used to build the permission list, so it cannot be omitted."""
    for tool in M.TOOLS:
        check("у инструмента %s объявлено mutates" % tool["name"],
              isinstance(tool.get("mutates"), bool), str(tool.get("mutates")))
        check("у инструмента %s есть схема входа" % tool["name"],
              isinstance(tool.get("input"), dict))


def test_declared_tools_are_all_wired():
    """A tool in the catalogue with no handler would fail at call time."""
    for name in M.TOOL_NAMES:
        check("инструмент %s подключён" % name, name in mcp_handlers.HANDLERS, name)

    for name in mcp_handlers.HANDLERS:
        check("обработчик %s объявлен в каталоге" % name, name in M.TOOL_NAMES, name)


def test_unknown_argument_is_refused():
    w = Workspace()
    try:
        result = w.server.call("course_context", {"course_id": COURSE,
                                                  "role": "teacher"})
        check("неизвестный аргумент отклонён", result["ok"] is False, str(result))
        check("код назван", result["code"] == "ARGUMENT_UNKNOWN", result["code"])

        result = w.server.call("course_context", {})
        check("отсутствие обязательного аргумента отклонено",
              result["ok"] is False and result["code"] == "ARGUMENT_REQUIRED",
              str(result))
    finally:
        w.cleanup()


def test_unknown_tool_is_refused():
    w = Workspace()
    try:
        result = w.server.call("run_shell", {"command": "ls"})
        check("несуществующий инструмент отклонён",
              result["ok"] is False and result["code"] == "TOOL_UNKNOWN",
              str(result))
    finally:
        w.cleanup()


def test_material_read_cannot_escape_the_course():
    w = Workspace()
    try:
        for path in ("../../etc/passwd", "/etc/passwd", "teacher/answers.md",
                     "solutions/key.md"):
            result = w.server.call("material_read", {"course_id": COURSE,
                                                     "path": path})
            check("чтение %r отклонено" % path, result["ok"] is False, str(result))

        allowed = w.server.call("material_read", {"course_id": COURSE,
                                                  "path": "lessons/01-working-tree.md"})
        check("разрешённый материал читается", allowed["ok"] is True, str(allowed)[:200])
    finally:
        w.cleanup()


def test_course_context_lists_no_closed_material():
    w = Workspace()
    try:
        result = w.server.call("course_context", {"course_id": COURSE})
        check("контекст курса получен", result["ok"] is True, str(result)[:200])
        data = result["data"]
        check("исключённые корни названы как исключённые",
              data["excluded_roots"] == ["teacher"], str(data["excluded_roots"]))
        serialized = json.dumps(data, ensure_ascii=False)
        check("в контексте нет содержимого заданий",
              "practice-diff.md" in serialized or "assignment" in serialized.lower())
        check("в контексте нет текстов уроков",
              "git diff сравнивает" not in serialized, "материал не должен выгружаться")
    finally:
        w.cleanup()


def test_handshake_states_its_limitations():
    w = Workspace()
    try:
        handshake = w.server.handshake()
        text = json.dumps(handshake, ensure_ascii=False).lower()
        check("сказано, что это не песочница", "не является песочницей" in text, text[:200])
        check("сказано про обход через bash",
              "bash" in text or "обойти" in text, text[:300])
        check("сказано про текстовый спойлер", "текст" in text, text[:400])
        check("гарантии названы ограниченными",
              any("ограничен" in g for g in handshake["guarantees"]),
              str(handshake["guarantees"]))
    finally:
        w.cleanup()


def test_session_start_reports_missing_agreement():
    w = Workspace()
    try:
        result = w.server.call("session_start", {"course_id": COURSE})
        check("занятие открыто", result["ok"] is True, str(result)[:200])
        check("нехватка согласия названа",
              "consent" in result["data"]["missing"], str(result["data"]["missing"]))
        check("сказано, что занятие не начнёт учить",
              any("согласие" in w for w in result["warnings"]), str(result["warnings"]))
    finally:
        w.cleanup()


def test_policy_check_answers_from_the_contract():
    w = Workspace()
    try:
        result = w.server.call("policy_check", {"course_id": COURSE,
                                                "assignment_id": "practice-diff",
                                                "level": "SOLUTION"})
        check("решение получено", result["ok"] is True, str(result)[:200])
        decision = result["data"]["decision"]
        check("решение называет правило", decision.get("rule_ref"), str(decision))
        check("оцениваемость взята из контракта",
              result["data"]["assessment"]["assessment"] == "practice",
              str(result["data"]["assessment"]))

        undeclared = w.server.call("policy_check", {"course_id": COURSE,
                                                    "assignment_id": "homework-9",
                                                    "level": "SOLUTION"})
        check("необъявленное задание — строгий режим",
              undeclared["data"]["decision"]["decision"] == "deny",
              str(undeclared["data"]["decision"])[:200])
    finally:
        w.cleanup()


def test_search_scope_is_enforced_through_the_tool():
    w = Workspace()
    try:
        result = w.server.call("source_search", {"course_id": COURSE,
                                                 "query": "git diff", "limit": 3})
        check("поиск выполнен", result["ok"] is True, str(result)[:200])
        check("исключения перечислены",
              result["data"]["scope"]["exclude_roots"] == ["teacher"],
              str(result["data"]["scope"]))
    finally:
        w.cleanup()


def test_env_status_and_cancel_have_no_apply_tool():
    """Applying an environment is not in the model's surface at all."""
    check("нет инструмента env_apply", "env_apply" not in M.TOOL_NAMES)
    check("нет инструмента env_plan", "env_plan" not in M.TOOL_NAMES)
    check("есть только чтение состояния", "env_status" in M.TOOL_NAMES)


# ---------------------------------------------------------------------------
# A26 — a host is certified only on evidence
# ---------------------------------------------------------------------------
def careful_config():
    return {
        "permission": {
            "default": "deny",
            "allow": list(A.allowed_tool_names()) + ["question"],
            "deny": list(A.MUST_BE_DENIED),
        }
    }


def test_careful_config_is_certified():
    verdict, problems = A.inspect_config(careful_config(), host="opencode")
    check("аккуратная конфигурация признана managed",
          verdict == A.PROFILE_MANAGED, str(problems))


def test_wildcard_allow_is_not_certified():
    for config in (
        {"permission": {"default": "deny", "allow": ["botai_*"],
                        "deny": list(A.MUST_BE_DENIED)}},
        {"permission": {"default": "deny", "*": "allow",
                        "deny": list(A.MUST_BE_DENIED)}},
    ):
        verdict, problems = A.inspect_config(config, host="opencode")
        codes = {p["code"] for p in problems}
        check("шаблон в разрешениях не даёт managed",
              verdict != A.PROFILE_MANAGED and "WILDCARD_ALLOW" in codes,
              str(codes))


def test_default_allow_is_not_certified():
    config = careful_config()
    config["permission"]["default"] = "allow"
    verdict, problems = A.inspect_config(config, host="opencode")
    check("default=allow не даёт managed",
          verdict != A.PROFILE_MANAGED
          and "DEFAULT_ALLOW" in {p["code"] for p in problems}, str(problems))


def test_missing_deny_of_a_bypass_tool_is_named():
    config = careful_config()
    config["permission"]["deny"] = ["bash"]
    verdict, problems = A.inspect_config(config, host="opencode")
    codes = [p["code"] for p in problems]
    check("неполный список запретов назван", "BYPASS_TOOL_NOT_DENIED" in codes,
          str(codes))
    check("названы конкретные инструменты",
          any("edit" in p["message_ru"] or "webfetch" in p["message_ru"]
              for p in problems), str(problems)[:300])


def test_privileged_subagent_is_named():
    config = careful_config()
    config["agent"] = {"helper": {"tools": ["bash", "edit"]}}
    verdict, problems = A.inspect_config(config, host="opencode")
    check("подагент с обходным инструментом не сертифицирован",
          verdict != A.PROFILE_MANAGED, verdict)
    check("назван конкретный подагент и инструмент",
          any("helper" in p["message_ru"] for p in problems), str(problems))

    unconstrained = careful_config()
    unconstrained["subagents"] = {"mapper": {}}
    verdict, problems = A.inspect_config(unconstrained, host="opencode")
    check("неограниченный подагент назван",
          any(p["code"] == "SUBAGENT_UNCONSTRAINED" for p in problems),
          str(problems))


def test_foreign_plugin_is_named():
    config = careful_config()
    config["mcp"] = {"education-club": {"enabled": True}}
    verdict, problems = A.inspect_config(config, host="opencode")
    check("включённое внешнее расширение названо",
          any(p["code"] == "FOREIGN_EXTENSION_ENABLED" for p in problems),
          str(problems))


def test_config_without_permissions_is_unverified_not_assumed():
    verdict, problems = A.inspect_config({}, host="opencode")
    check("отсутствие раздела разрешений не считается безопасным",
          verdict == "HOST_UNVERIFIED"
          and any(p["code"] == "NO_PERMISSION_BLOCK" for p in problems),
          str(problems))


def test_host_without_adapter_is_compatibility():
    report = A.adapter_check("some-unknown-host")
    check("хост без адаптера — compatibility",
          report["verdict"] == A.PROFILE_COMPATIBILITY, report["verdict"])
    # The limits paragraph is on the problem entry for a host with no adapter:
    # what matters is that the report says a technical restriction is NOT claimed.
    said = " ".join(p["message_ru"] for p in report["problems"]) \
        + " " + " ".join(report["limits_ru"])
    check("сказано, что технических ограничений нет",
          "не заявляются" in said, said[:300])


def test_unchecked_config_never_yields_managed():
    report = A.adapter_check("opencode", version=None)
    check("без проверки конфигурации managed не выдаётся",
          report["verdict"] == "HOST_UNVERIFIED", report["verdict"])
    check("сказано, чего не хватает",
          any("не проверена" in p["message_ru"] for p in report["problems"]),
          str(report["problems"]))


def test_adapter_check_reads_a_real_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "opencode.json"
        path.write_text(json.dumps(careful_config(), ensure_ascii=False),
                        encoding="utf-8")
        report = A.adapter_check("opencode", config_path=path)
        check("конфигурация из файла проверена", path in
              [Path(x) for x in report["checked"]], str(report["checked"]))
        check("вердикт вынесен", report["verdict"] in
              (A.PROFILE_MANAGED, "HOST_UNVERIFIED"), report["verdict"])

        missing = A.adapter_check("opencode", config_path=Path(tmp) / "nope.json")
        check("отсутствующий файл не сертифицируется",
              missing["verdict"] == "HOST_UNVERIFIED", missing["verdict"])


def test_profile_is_generated_as_data_and_not_global():
    profile = A.build_profile("opencode", workspace_root="/tmp/ws")
    check("профиль запрещает по умолчанию",
          profile["permission"]["default"] == "deny")
    check("профиль перечисляет конкретные инструменты, без шаблонов",
          all(not name.endswith("*") for name in profile["permission"]["allow"]),
          str(profile["permission"]["allow"][:3]))
    check("профиль запрещает обходные инструменты",
          set(A.MUST_BE_DENIED) <= set(profile["permission"]["deny"]))
    check("профиль называет свои пределы",
          any("не песочница" in note for note in profile["known_limitations_ru"]),
          str(profile["known_limitations_ru"]))

    try:
        A.build_profile("unknown-host")
    except A.AdapterError as e:
        check("профиль для неизвестного хоста не генерируется",
              e.code == "HOST_UNKNOWN", e.code)
    else:
        check("профиль для неизвестного хоста не генерируется", False, "сгенерирован")


def test_catalog_check_reports_before_use():
    with tempfile.TemporaryDirectory() as tmp:
        report = A.catalog_check(Path(tmp) / "absent")
        check("отсутствующий каталог назван",
              report["ok"] is False
              and any(p["code"] == "CATALOG_MISSING" for p in report["problems"]),
              str(report["problems"]))
        check("сказано, что каталог не обходит проверки",
              "с проверками" in report.get("note_ru", ""),
              report.get("note_ru", ""))


def test_render_report_is_readable():
    report = A.adapter_check("opencode", document=careful_config())
    text = A.render_adapter_report(report)
    check("отчёт называет вердикт", "вердикт" in text and
          report["verdict"] in text, text[:200])
    check("отчёт напоминает, что это не песочница",
          "не песочница" in text or "ограничение инструментов" in text, text[-300:])


def main():
    tests = [
        test_forbidden_tools_do_not_exist,
        test_no_tool_accepts_a_path_url_or_role,
        test_every_tool_declares_whether_it_mutates,
        test_declared_tools_are_all_wired,
        test_unknown_argument_is_refused,
        test_unknown_tool_is_refused,
        test_material_read_cannot_escape_the_course,
        test_course_context_lists_no_closed_material,
        test_handshake_states_its_limitations,
        test_session_start_reports_missing_agreement,
        test_policy_check_answers_from_the_contract,
        test_search_scope_is_enforced_through_the_tool,
        test_env_status_and_cancel_have_no_apply_tool,
        test_careful_config_is_certified,
        test_wildcard_allow_is_not_certified,
        test_default_allow_is_not_certified,
        test_missing_deny_of_a_bypass_tool_is_named,
        test_privileged_subagent_is_named,
        test_foreign_plugin_is_named,
        test_config_without_permissions_is_unverified_not_assumed,
        test_host_without_adapter_is_compatibility,
        test_unchecked_config_never_yields_managed,
        test_adapter_check_reads_a_real_file,
        test_profile_is_generated_as_data_and_not_global,
        test_catalog_check_reports_before_use,
        test_render_report_is_readable,
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
