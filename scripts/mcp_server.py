#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The MCP adapter: a narrow tool surface over the same core the CLI uses.

Why this file is thin on purpose: every guarantee in this project is a property
of `botai_core`, and the adapter's only job is to expose *some* of it to a model.
So the rules here are about what is **absent**:

* There is no shell tool, no edit/write tool, no generic fetch, and no tool that
  takes an arbitrary path or URL.
* There is no `approve`, `set_permission` or `write_approval`. A model cannot
  mint the human consent that `operation.py` requires — that is enforced by the
  absence of any code path, not by a rule in a prompt.
* There is no `role` argument anywhere, so a tool call cannot turn a student
  workspace into a teacher one.
* Paths and URLs are not parameters. The server resolves its own root from the
  workspace it was started in, so a tool cannot name a directory to read.
* Every write takes a `request_id`, and a change to an existing entity takes
  `expected_version`, so a retry is idempotent and a stale write is refused.

What this adapter cannot promise, and the handshake says so: on a host that
merely *loads* these tools alongside its own bash and editor, nothing here stops
the model from going around them. That is what the `managed` profile is for, and
`adapters.py` is where a host is either certified or reported `HOST_UNVERIFIED`.

The stdio server uses the official MCP SDK when it is installed. Without it the
TOOLS catalogue and `dispatch` are still importable and testable — which is how
CI exercises the whole surface without a host.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Tool results are serialised with ensure_ascii=False, so the Russian text
# in every envelope goes on the wire as real UTF-8. On a Windows host whose
# console code page is cp1251/cp866 that encoding is not UTF-8, so the
# server either raised UnicodeEncodeError on startup (the raw stderr
# prints below) or handed the client mojibake it could not parse as
# JSON-RPC. Pinning both streams is what the protocol already assumes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from botai_core import schemas  # noqa: E402

SERVER_NAME = "botai"
PROTOCOL_VERSION = "2024-11-05"

# Limits that apply to every call, whatever the tool.
MAX_TEXT_CHARS = 64 * 1024          # an attempt's text
MAX_MATERIAL_BYTES = 32 * 1024      # one permitted material read
MAX_QUERY_CHARS = 500
MAX_TOOL_RESULT_BYTES = 512 * 1024


class ToolError(RuntimeError):
    """A refused tool call, shaped for the MCP result envelope."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# The tool catalogue
# --------------------------------------------------------------------------

# Each entry: name, description, input schema, and whether it mutates state.
# `mutates` is used by `adapters.py` to build the permission list, so a new tool
# cannot be added without its write behaviour being declared.
TOOLS = (
    {
        "name": "course_context",
        "description": "Принятый контракт курса: задания, оцениваемость, цели, "
                       "готовность среды и корпуса. Только чтение.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id"],
            "properties": {"course_id": {"type": "string"}},
        },
    },
    {
        "name": "material_read",
        "description": "Прочитать разрешённый материал курса по точным "
                       f"координатам. Не более {MAX_MATERIAL_BYTES} байт.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id", "path"],
            "properties": {
                "course_id": {"type": "string"},
                "path": {"type": "string"},
                "locator": {"type": "object"},
            },
        },
    },
    {
        "name": "session_start",
        "description": "Открыть учебное занятие. Возвращает сессию и то, чего "
                       "не хватает для начала (согласие, цель, настройки).",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id"],
            "properties": {
                "course_id": {"type": "string"},
                "objective_id": {"type": ["string", "null"]},
                "assignment_id": {"type": ["string", "null"]},
                "request_id": {"type": ["string", "null"]},
            },
        },
    },
    {
        "name": "session_next",
        "description": "Следующий допустимый шаг занятия. Чистое чтение: "
                       "рекомендация не меняет состояние.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {"session_id": {"type": "string"}},
        },
    },
    {
        "name": "response_check",
        "description": "Проверить подготовленный учебный ответ перед показом "
                       "ученику: схема, предел помощи, область ссылок.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "teaching_response"],
            "properties": {
                "session_id": {"type": "string"},
                "teaching_response": {"type": "object"},
            },
        },
    },
    {
        "name": "policy_check",
        "description": "Разрешение помощи для задания: что допустимо, на "
                       "основании какого правила, и что предложить вместо.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id", "assignment_id", "level"],
            "properties": {
                "course_id": {"type": "string"},
                "assignment_id": {"type": "string"},
                "level": {"enum": ["HINT", "EXAMPLE", "SOLUTION"]},
                "preference": {"type": "string"},
            },
        },
    },
    {
        "name": "attempt_record",
        "description": "Записать попытку ученика. Ровно одно из text или "
                       "artifact_sha256; источник назначает ядро.",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string"},
                "objective_id": {"type": ["string", "null"]},
                "assignment_id": {"type": ["string", "null"]},
                "text": {"type": ["string", "null"]},
                "artifact_sha256": {"type": ["string", "null"]},
                "request_id": {"type": ["string", "null"]},
            },
        },
    },
    {
        "name": "assistance_record",
        "description": "Записать выданную помощь. Уровень перепроверяется "
                       "политикой; записать запрещённое нельзя.",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "level", "step", "reason"],
            "properties": {
                "session_id": {"type": "string"},
                "level": {"enum": ["HINT", "EXAMPLE", "SOLUTION"]},
                "step": {"type": "integer", "minimum": 0},
                "reason": {"type": "string"},
                "response_ref": {"type": ["string", "null"]},
                "expected_version": {"type": ["integer", "null"]},
                "request_id": {"type": ["string", "null"]},
            },
        },
    },
    {
        "name": "check_record",
        "description": "Записать результат проверки. Вердикт обязан следовать "
                       "из критериев; освоение считает ядро.",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "attempt_id", "kind", "verdict",
                         "criterion_results"],
            "properties": {
                "session_id": {"type": "string"},
                "attempt_id": {"type": "string"},
                "kind": {"type": "string"},
                "verdict": {"enum": ["pass", "partial", "fail", "uncertain"]},
                "criterion_results": {"type": "array"},
                "expected_version": {"type": ["integer", "null"]},
                "request_id": {"type": ["string", "null"]},
            },
        },
    },
    {
        "name": "session_transition",
        "description": "pause/resume/close/cancel. Переход проверяет автомат "
                       "занятия; недопустимый отклоняется.",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "target", "expected_version"],
            "properties": {
                "session_id": {"type": "string"},
                "target": {"enum": ["PAUSED", "COMPLETED", "CANCELLED"]},
                "expected_version": {"type": "integer"},
                "reason": {"type": ["string", "null"]},
                "request_id": {"type": ["string", "null"]},
            },
        },
    },
    {
        "name": "progress_get",
        "description": "Прогресс текущего ученика по курсу: состояния целей, "
                       "доказательства, следующий шаг.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id"],
            "properties": {"course_id": {"type": "string"}},
        },
    },
    {
        "name": "source_search",
        "description": "Поиск по разрешённым материалам курса. Область "
                       "ограничена до поиска: исключённые материалы недостижимы.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id", "query"],
            "properties": {
                "course_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            },
        },
    },
    {
        "name": "quote_verify",
        "description": "Проверить цитату по принятым материалам: статус текста "
                       "и координат отдельно от смысла.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id", "citation"],
            "properties": {
                "course_id": {"type": "string"},
                "citation": {"type": "object"},
            },
        },
    },
    {
        "name": "contribution_get",
        "description": "Состояние вклада и безопасные наблюдения Git. Только "
                       "чтение: коммитов и публикации здесь нет.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id"],
            "properties": {
                "course_id": {"type": "string"},
                "contribution_id": {"type": ["string", "null"]},
            },
        },
    },
    {
        "name": "contribution_draft",
        "description": "Черновик описания вклада. Локальный текст, не "
                       "публикация: commit, push и PR делает ученик.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id", "contribution_id"],
            "properties": {
                "course_id": {"type": "string"},
                "contribution_id": {"type": "string"},
            },
        },
    },
    {
        "name": "presentation_set",
        "description": "Выбрать оформление занятия (персона, награды). Только "
                       "стиль: права и правила не меняются.",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["course_id", "user_request_ref"],
            "properties": {
                "course_id": {"type": "string"},
                "persona_id": {"type": ["string", "null"]},
                "gamification": {"type": ["boolean", "null"]},
                "user_request_ref": {"type": "string"},
            },
        },
    },
    {
        "name": "env_status",
        "description": "Состояние операции подготовки среды. Только чтение.",
        "mutates": False,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation_id"],
            "properties": {"operation_id": {"type": "string"}},
        },
    },
    {
        "name": "operation_cancel",
        "description": "Запросить отмену своей операции. Не удаляет работу и "
                       "не запускает новую.",
        "mutates": True,
        "input": {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation_id", "request_id"],
            "properties": {
                "operation_id": {"type": "string"},
                "request_id": {"type": "string"},
            },
        },
    },
)

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)

# Names that must never exist in this surface, listed so a test can assert their
# absence and so a future contributor sees why.
FORBIDDEN_TOOL_NAMES = (
    "approve", "action_approve", "set_permission", "write_approval",
    "run_shell", "bash", "edit", "write_file", "read_file", "glob", "grep",
    "webfetch", "fetch_url", "install", "git_commit", "git_push",
    "open_pull_request", "publish", "grade_set", "set_mastery", "role_set",
    "consent_set", "teacher_import", "privacy_export", "privacy_delete",
    "course_add", "course_accept", "update",
)


def tool_by_name(name):
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None


# --------------------------------------------------------------------------
# Argument validation
# --------------------------------------------------------------------------

def validate_arguments(name, arguments):
    """Refuse a call that names unknown fields or exceeds a limit.

    `additionalProperties: false` in the schemas above is the declaration; this
    enforces it without requiring a JSON Schema runtime, and gives a readable
    refusal instead of a schema dump.
    """
    tool = tool_by_name(name)
    if tool is None:
        raise ToolError("TOOL_UNKNOWN", "инструмент не найден: %r" % name)

    if not isinstance(arguments, dict):
        raise ToolError("ARGUMENTS_NOT_OBJECT", "аргументы должны быть объектом")

    schema = tool["input"]
    allowed = set(schema.get("properties") or {})
    required = set(schema.get("required") or [])

    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolError(
            "ARGUMENT_UNKNOWN",
            "неизвестные аргументы для %s: %s" % (name, ", ".join(unknown)),
        )

    missing = sorted(key for key in required
                     if arguments.get(key) is None)
    if missing:
        raise ToolError(
            "ARGUMENT_REQUIRED",
            "не хватает обязательных аргументов для %s: %s"
            % (name, ", ".join(missing)),
        )

    if "text" in arguments and arguments["text"]:
        if len(arguments["text"]) > MAX_TEXT_CHARS:
            raise ToolError(
                "TEXT_TOO_LARGE",
                "текст попытки больше %d символов: сохраните файл как артефакт, "
                "а не обрезайте доказательство молча" % MAX_TEXT_CHARS,
            )
    if "query" in arguments and arguments["query"]:
        if len(arguments["query"]) > MAX_QUERY_CHARS:
            raise ToolError("QUERY_TOO_LARGE", "запрос слишком длинный")

    # Paths are not accepted as free-form input. `material_read` names a course
    # and a path, but the path is resolved against the accepted contract's
    # allowed roots by the core, never used to open whatever it points at.
    if name == "material_read":
        path = arguments.get("path") or ""
        if path.startswith("/") or ".." in path.replace("\\", "/").split("/"):
            raise ToolError(
                "PATH_REFUSED",
                "путь %r не может быть абсолютным или выходить за курс" % path,
            )

    return arguments


def envelope(ok, code, message_ru, *, data=None, warnings=None, effects=None,
             next_actions=None):
    """The one result shape every tool returns."""
    document = {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "ok": bool(ok),
        "code": code,
        "message_ru": message_ru,
        "data": data,
        "warnings": list(warnings or []),
        "effects": list(effects or []),
        "next_actions": list(next_actions or []),
    }
    serialized = json.dumps(document, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
        return envelope(False, "RESULT_TOO_LARGE",
                        "результат превысил предел %d КиБ: сузьте запрос"
                        % (MAX_TOOL_RESULT_BYTES // 1024))
    return document


def error_envelope(error):
    if isinstance(error, ToolError):
        return envelope(False, error.code, error.message)
    code = getattr(error, "code", type(error).__name__)
    message = getattr(error, "message", str(error))
    return envelope(False, str(code), str(message))


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

class Server:
    """Holds the bound workspace and learner, and dispatches tool calls.

    The root and learner are bound at construction and never come from a tool
    argument. That is what makes "the model cannot name a path" true: there is no
    parameter through which a path could arrive.
    """

    def __init__(self, root, learner_id, *, clock=None):
        self.root = Path(root)
        self.learner_id = learner_id
        self.clock = clock
        self._reads = 0

    # -- helpers -----------------------------------------------------------
    def _course(self, course_id):
        from botai_core import course as course_mod
        return course_mod.load_accepted(self.root, course_id)

    def _handlers(self):
        from botai_core import mcp_handlers
        return mcp_handlers.HANDLERS

    def call(self, name, arguments):
        """Validate and run one tool call, returning a result envelope."""
        try:
            validate_arguments(name, arguments or {})
        except ToolError as e:
            return error_envelope(e)

        handler = self._handlers().get(name)
        if handler is None:
            # A tool is declared in TOOLS but not wired: a defect in this
            # adapter, not a refusal the model caused.
            return envelope(False, "TOOL_UNIMPLEMENTED",
                            "инструмент %s объявлен, но не подключён" % name)

        try:
            return handler(self, arguments or {})
        except Exception as e:  # noqa: BLE001 - every failure becomes an envelope
            return error_envelope(e)

    # -- MCP plumbing ------------------------------------------------------
    def list_tools(self):
        return [
            {"name": tool["name"], "description": tool["description"],
             "inputSchema": tool["input"]}
            for tool in TOOLS
        ]

    def handshake(self):
        """What this server tells a host about itself.

        The limitations are part of the greeting on purpose: a host that reads
        only this must not conclude the tools are a sandbox.
        """
        return {
            "server": SERVER_NAME,
            "protocol_version": PROTOCOL_VERSION,
            "workspace": str(self.root),
            "learner_id": self.learner_id,
            "tools": list(TOOL_NAMES),
            "guarantees": [
                "каждый инструмент ограничен своей областью: курс, ученик, сессия",
                "пути и URL не принимаются как аргументы",
                "разрешение человека на операции нельзя выдать вызовом инструмента",
            ],
            "limitations": [
                "этот сервер не является песочницей: он ограничивает только свои "
                "инструменты",
                "на хосте с собственным bash/edit модель может обойти их",
                "свободный текст модели может раскрыть ответ в обход проверок",
                "проверенный профиль хоста — предмет adapter-check; без него "
                "режим считается compatibility",
            ],
        }


def run_stdio(root, learner_id, *, clock=None):
    """Serve MCP over stdio. Requires the official SDK.

    Stdio only: there is no HTTP listener, no socket, no API key. A network
    MCP server would be a different trust decision, and this one has not been
    taken.
    """
    try:
        from mcp.server import Server as MCPServer
        from mcp.server.stdio import stdio_server
        from mcp import types
    except ImportError as e:
        print("MCP SDK не установлен: %s" % e, file=sys.stderr)
        print("Установите зависимости: python -m pip install --require-hashes "
              "-r requirements-mcp.lock", file=sys.stderr)
        return 2

    server = Server(root, learner_id, clock=clock)
    app = MCPServer(SERVER_NAME)

    @app.list_tools()
    async def _list_tools():
        return [types.Tool(name=t["name"], description=t["description"],
                           inputSchema=t["input"])
                for t in TOOLS]

    @app.call_tool()
    async def _call_tool(name, arguments):
        result = server.call(name, arguments or {})
        return [types.TextContent(type="text",
                                  text=json.dumps(result, ensure_ascii=False))]

    async def _main():
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    import asyncio
    asyncio.run(_main())
    return 0


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="botai MCP server (stdio)")
    parser.add_argument("--root", required=True)
    parser.add_argument("--learner", required=True)
    args = parser.parse_args(argv)
    return run_stdio(args.root, args.learner)


if __name__ == "__main__":
    sys.exit(main())
