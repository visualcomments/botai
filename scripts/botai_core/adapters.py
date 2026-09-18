# -*- coding: utf-8 -*-
"""Host profiles: certified restriction, or an honest `HOST_UNVERIFIED`.

A host is a program that loads a model and gives it tools. botai's guarantees
are properties of *its own* tools; whether they survive depends entirely on what
else the host has loaded. A host with a bash tool, an editor and an unrestricted
web fetch makes every botai limit bypassable, and no amount of configuration
text changes that.

So this module has one job: **decide, from evidence, whether the final host
configuration actually restricts the model**, and report honestly when it cannot
tell. Three states, and the middle one matters most:

* `managed` — the profile was generated, AND the resulting configuration was
  inspected and found to allow only the botai tools plus the shipped skills.
* `compatibility` — instructions and CLI only. The policy text and skills apply;
  no technical restriction is claimed. This is a legitimate, useful mode.
* `HOST_UNVERIFIED` — the file cannot be proven restrictive. Only compatibility
  or a read-only teaching mode is allowed.

The temptation this module exists to resist is declaring `managed` because a
config *looks* right. A permission block with `"*": "allow"` after a specific
allow rule, a subagent that inherits a wider toolset, a plugin that registers its
own tools at startup — each makes the profile decorative, and each is something
a self-test can actually check for.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = 2

# Hosts with an adapter today. Everything else is compatibility until an adapter
# with its own tests exists — the design is explicit that support is not
# promised from a doc page.
KNOWN_HOSTS = ("opencode", "claude-code", "cursor", "codex", "pi", "dsh")

# The exact versions an adapter has been exercised against. A version not in
# this map is not "probably fine": it is untested, and the check says so.
TESTED_VERSIONS = {
    "opencode": None,   # to be filled by the compatibility run; see docs/host-compatibility.md
}

PROFILE_MANAGED = "managed"
PROFILE_COMPATIBILITY = "compatibility"

# Tools the managed profile must NOT leave reachable. Each is a way around the
# botai tool surface: run a command, edit a file, read any file, fetch any URL,
# or delegate to an agent that has them.
MUST_BE_DENIED = (
    "bash", "shell", "run_command", "execute",
    "edit", "write", "patch", "multiedit", "notebook_edit",
    "webfetch", "websearch", "fetch",
    "task",
)

# Tool names the managed profile is allowed to expose. Built from the MCP
# catalogue so a new tool cannot be added without appearing here — and never as
# a wildcard prefix, because a later administrative tool would then be allowed
# by the same rule.
def allowed_tool_names():
    import mcp_server
    return tuple("botai_%s" % name for name in mcp_server.TOOL_NAMES)


class AdapterError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def build_profile(host, *, workspace_root=None):
    """The configuration botai can generate for a host.

    Generated as data, written only into the project. Nothing global is touched:
    a tool that edits a user's global config can break every other project they
    have, and the design forbids it.
    """
    if host not in KNOWN_HOSTS:
        raise AdapterError(
            "HOST_UNKNOWN",
            "%r не входит в список хостов с адаптером (%s). Для остальных "
            "доступен только режим compatibility: политика и навыки без "
            "технических ограничений." % (host, ", ".join(KNOWN_HOSTS)),
        )

    allowed = list(allowed_tool_names())
    profile = {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "profile": PROFILE_MANAGED,
        "workspace_root": str(workspace_root) if workspace_root else None,
        "permission": {
            # Default deny first, explicit allows after: the order in the file
            # must not be the thing that decides, so the deny is stated as the
            # default rather than as a trailing rule.
            "default": "deny",
            "allow": sorted(allowed),
            "deny": sorted(MUST_BE_DENIED),
        },
        "notes_ru": [
            "Профиль запрещает всё по умолчанию и разрешает только инструменты botai.",
            "Обходные инструменты (bash, edit, произвольное чтение и сеть) закрыты.",
            "Учебные материалы модель получает через material_read и source_search.",
        ],
        "known_limitations_ru": [
            "Это ограничение инструментов, а не песочница: модель всё ещё может "
            "выдать нежелательный текст.",
            "Профиль обязателен к проверке итогового конфига: сгенерированный файл "
            "не доказывает, что хост его применил.",
        ],
    }
    return profile


def inspect_config(document, *, host, allowed=None):
    """Examine a host configuration for ways around the botai tools.

    Returns `(verdict, problems)` where verdict is `managed`, `compatibility` or
    `HOST_UNVERIFIED`. The check reads the *final* configuration, not the file
    botai wrote: a host may merge global settings, load plugins or add subagents.
    """
    problems = []
    allowed = set(allowed or allowed_tool_names())

    if not isinstance(document, dict):
        return "HOST_UNVERIFIED", [{
            "code": "CONFIG_NOT_OBJECT",
            "message_ru": "конфигурацию хоста не удалось прочитать как объект",
        }]

    permission = document.get("permission")
    if permission is None:
        # No permission block at all is not "permissive by default": it is
        # unknown, and unknown is not certified.
        problems.append({
            "code": "NO_PERMISSION_BLOCK",
            "message_ru": "в конфигурации нет раздела разрешений: ограничения "
                          "доказать нельзя",
        })
        return "HOST_UNVERIFIED", problems

    # A wildcard allow is the most common way a careful-looking config still
    # permits everything.
    if isinstance(permission, dict):
        for key, value in permission.items():
            if key in ("default", "deny", "allow"):
                continue
            if value == "allow" and _is_wildcard(key):
                problems.append({
                    "code": "WILDCARD_ALLOW",
                    "message_ru": "правило %r разрешает всё: ограничение перестаёт "
                                  "что-либо значить" % key,
                })
        for entry in permission.get("allow") or []:
            # A wildcard is checked before the catalogue-name branch: `botai_*`
            # both starts with `botai_` and is a wildcard, and reporting it as an
            # unknown tool would send the reader looking for a typo instead of at
            # the pattern that also permits every tool added later.
            text = str(entry)
            if _is_wildcard(text):
                problems.append({
                    "code": "WILDCARD_ALLOW",
                    "message_ru": "в списке разрешений есть шаблон %r: он разрешит и "
                                  "те инструменты, которые появятся позже" % entry,
                })
            elif text.startswith("botai_") and text not in allowed:
                problems.append({
                    "code": "TOOL_NOT_IN_CATALOGUE",
                    "message_ru": "разрешён инструмент %r, которого нет в каталоге "
                                  "botai: проверьте, не добавлен ли он позже" % entry,
                })
            elif not text.startswith("botai_") and text not in ("question",):
                problems.append({
                    "code": "NON_BOTAI_TOOL_ALLOWED",
                    "message_ru": "разрешён посторонний инструмент %r: он может "
                                  "обойти ограничения botai" % entry,
                })

        if permission.get("default") == "allow":
            problems.append({
                "code": "DEFAULT_ALLOW",
                "message_ru": "по умолчанию разрешено всё: профиль не является "
                              "ограничивающим",
            })

    # A deny list that names a bypass tool is necessary but not sufficient; a
    # missing entry is a concrete, checkable defect.
    denied = set()
    if isinstance(permission, dict):
        denied.update(str(x) for x in (permission.get("deny") or []))
        denied.update(k for k, v in permission.items() if v == "deny")
    for tool in MUST_BE_DENIED:
        if tool not in denied:
            problems.append({
                "code": "BYPASS_TOOL_NOT_DENIED",
                "message_ru": "инструмент %r не запрещён явно: он даёт обход "
                              "ограничений botai" % tool,
            })

    # Subagents can be given a wider toolset than the primary agent. If the
    # configuration does not constrain them, they are the hole.
    for key in ("agent", "agents", "subagent", "subagents"):
        for name, spec in (document.get(key) or {}).items() if isinstance(document.get(key), dict) else []:
            spec_tools = (spec or {}).get("tools") if isinstance(spec, dict) else None
            if spec_tools is None:
                problems.append({
                    "code": "SUBAGENT_UNCONSTRAINED",
                    "message_ru": "подагент %r не ограничен по инструментам" % name,
                })
            else:
                for tool in spec_tools:
                    if tool in MUST_BE_DENIED:
                        problems.append({
                            "code": "SUBAGENT_HAS_BYPASS_TOOL",
                            "message_ru": "подагент %r получает инструмент %r"
                                          % (name, tool),
                        })

    # A plugin that registers tools at startup can add anything the profile
    # allowed by omission.
    for key in ("plugins", "mcp", "extensions"):
        entries = document.get(key)
        if isinstance(entries, dict):
            for name, spec in entries.items():
                if isinstance(spec, dict) and spec.get("enabled") is True \
                        and not str(name).startswith("botai"):
                    problems.append({
                        "code": "FOREIGN_EXTENSION_ENABLED",
                        "message_ru": "включено внешнее расширение %r: оно может "
                                      "добавить собственные инструменты" % name,
                    })

    verdict = PROFILE_MANAGED if not problems else "HOST_UNVERIFIED"
    return verdict, problems


def _is_wildcard(text):
    """Whether a permission pattern matches more than one specific tool.

    A trailing wildcard (`botai_*`) is exactly as permissive as a leading one and
    is the form a careful-looking config actually uses, so both are caught. A
    bare name matches one tool and is not a wildcard.
    """
    value = str(text).strip()
    if value in ("*", "**", "*:*"):
        return True
    return "*" in value


def adapter_check(host, config_path=None, *, document=None, version=None,
                  workspace_root=None):
    """Report whether a host can be certified, and what stands in the way.

    Never returns `managed` for a host that has no adapter, and never for a
    configuration it could not read.
    """
    report = {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "version_seen": version,
        "verdict": None,
        "problems": [],
        "checked": [],
        "allowed_tools": list(allowed_tool_names()),
        "limits_ru": [],
    }

    if host not in KNOWN_HOSTS:
        report["verdict"] = PROFILE_COMPATIBILITY
        report["problems"].append({
            "code": "NO_ADAPTER",
            "message_ru": "для %r нет адаптера: доступны политика, навыки и ручной "
                          "CLI. Технические ограничения не заявляются." % host,
        })
        report["limits_ru"].append("Материалы и состояние — через CLI, вручную.")
        return report

    tested = TESTED_VERSIONS.get(host)
    if tested is None:
        report["problems"].append({
            "code": "HOST_VERSION_UNTESTED",
            "message_ru": "версия хоста %r для этого адаптера не закреплена как "
                          "проверенная: результат проверки конфигурации не "
                          "распространяется на неё" % (version or "неизвестна"),
            })
    elif version and version != tested:
        report["problems"].append({
            "code": "HOST_VERSION_MISMATCH",
            "message_ru": "проверена версия %s, обнаружена %s: поведение разрешений "
                          "может отличаться" % (tested, version),
        })

    if document is None and config_path is not None:
        path = Path(config_path)
        report["checked"].append(str(path))
        if not path.is_file():
            report["verdict"] = "HOST_UNVERIFIED"
            report["problems"].append({
                "code": "CONFIG_MISSING",
                "message_ru": "конфигурация не найдена: %s" % path,
            })
            return report
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            report["verdict"] = "HOST_UNVERIFIED"
            report["problems"].append({
                "code": "CONFIG_UNREADABLE",
                "message_ru": "конфигурацию не удалось прочитать: %s" % e,
            })
            return report

    if document is None:
        report["verdict"] = "HOST_UNVERIFIED"
        report["problems"].append({
            "code": "CONFIG_NOT_CHECKED",
            "message_ru": "итоговая конфигурация не проверена: без этого managed "
                          "не выдаётся",
        })
        return report

    verdict, problems = inspect_config(document, host=host)
    report["problems"].extend(problems)
    report["verdict"] = verdict

    if verdict != PROFILE_MANAGED:
        report["limits_ru"] = [
            "Доступен режим compatibility: единая политика, навыки и ручной CLI.",
            "Либо read-only учебный режим без операций изменения.",
            "AGENTS.md не является файрволом: текстом ограничения не обеспечиваются.",
        ]
    return report


def catalog_check(catalog_path, *, expected_tools=None):
    """Verify an optional external catalogue before it is offered to a learner.

    The Education Club catalogue is a *discovery* aid. Adding a course from it
    still goes through the ordinary `course-add` path with its own checks, and a
    catalogue that fails this check is reported rather than used.
    """
    catalog_path = Path(catalog_path)
    report = {
        "schema_version": SCHEMA_VERSION,
        "catalog_path": str(catalog_path),
        "ok": False,
        "problems": [],
        # Stated on every path, including the failing ones: what a catalogue may
        # and may not be used for does not depend on whether this one exists.
        "note_ru": (
            "Каталог используется только для поиска курсов. Добавление курса идёт "
            "обычным путём с проверками; внешний fetch_course не обходит их."
        ),
    }
    if not catalog_path.is_dir():
        report["problems"].append({
            "code": "CATALOG_MISSING",
            "message_ru": "каталог не найден: %s" % catalog_path,
        })
        return report

    server_file = catalog_path / "mcp" / "catalog-mcp.py"
    report["server_path"] = str(server_file)
    if not server_file.is_file():
        report["problems"].append({
            "code": "CATALOG_SERVER_MISSING",
            "message_ru": "в каталоге нет mcp/catalog-mcp.py",
        })

    manifest_file = catalog_path / "version"
    if manifest_file.is_file():
        report["version"] = manifest_file.read_text(encoding="utf-8-sig").strip()
    else:
        report["problems"].append({
            "code": "CATALOG_VERSION_UNKNOWN",
            "message_ru": "версия каталога не указана: совместимость не заявляется",
        })

    report["ok"] = not report["problems"]
    return report


def render_adapter_report(report):
    lines = ["== проверка хоста: %s ==" % report["host"]]
    lines.append("  вердикт     : %s" % report["verdict"])
    if report.get("version_seen"):
        lines.append("  версия      : %s" % report["version_seen"])
    if report["checked"]:
        lines.append("  проверено   : %s" % ", ".join(report["checked"]))
    lines.append("  разрешено   : %d инструментов botai" % len(report["allowed_tools"]))
    lines.append("")
    if report["problems"]:
        lines.append("  Замечания:")
        for problem in report["problems"]:
            lines.append("    [%s] %s" % (problem["code"], problem["message_ru"]))
        lines.append("")
    for item in report.get("limits_ru") or []:
        lines.append("  %s" % item)
    if report["verdict"] == PROFILE_MANAGED:
        lines.append("")
        lines.append("  Учебные операции разрешены в границах инструментов botai.")
        lines.append("  Это ограничение инструментов, а не песочница: текстовый")
        lines.append("  спойлер им не предотвращается.")
    return "\n".join(lines)
