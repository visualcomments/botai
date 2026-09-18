# -*- coding: utf-8 -*-
"""Tool handlers: the core services, reached through a narrow door.

Kept separate from `mcp_server.py` so the adapter's transport plumbing and the
work each tool does can be tested independently — and so the catalogue of tools
is one readable list rather than being scattered among the implementations.

Every handler returns the shared result envelope. None of them accepts a
filesystem path, a URL, or an operation approval: those either come from the
bound workspace, from the accepted course contract, or do not exist at all.
"""

from __future__ import annotations

import json

from . import corpus as corpus_mod
from . import course as course_mod
from . import environment as env_mod
from . import exports as exports_mod
from . import operation as operation_mod
from . import personas as personas_mod
from . import policy as policy_mod
from . import retrieval
from . import session as session_mod
from . import store as store_mod
from . import tutoring as tutoring_mod


def _ok(message_ru, data=None, **extra):
    return {
        "schema_version": 2,
        "ok": True,
        "code": "OK",
        "message_ru": message_ru,
        "data": data,
        "warnings": list(extra.get("warnings") or []),
        "effects": list(extra.get("effects") or []),
        "next_actions": list(extra.get("next_actions") or []),
    }


def _fail(code, message_ru, data=None, **extra):
    return {
        "schema_version": 2,
        "ok": False,
        "code": code,
        "message_ru": message_ru,
        "data": data,
        "warnings": list(extra.get("warnings") or []),
        "effects": [],
        "next_actions": list(extra.get("next_actions") or []),
    }


def _store(server, create=False):
    return store_mod.Store.open(server.root, server.learner_id, create=create)


# --------------------------------------------------------------------------
# Reading the course
# --------------------------------------------------------------------------

def course_context(server, arguments):
    """The accepted contract, its outline, and readiness. Never closed material."""
    course_id = arguments["course_id"]
    try:
        accepted = server._course(course_id)
    except course_mod.CourseError as e:
        return _fail(e.code, e.message,
                     next_actions=[{"kind": "manual_command",
                                    "command": "course-inspect --course %s" % course_id}])

    contract = accepted.contract
    return _ok(
        "Контракт курса %s" % course_id,
        {
            "course_id": accepted.course_id,
            "title": contract["title"],
            "language": contract["language"],
            "revision": dict(accepted.revision),
            "contract_hash": accepted.contract_hash,
            "licenses": contract.get("licenses"),
            "contribution_enabled": bool((contract.get("contribution") or {}).get("enabled")),
            "assignments": [
                {"assignment_id": a["assignment_id"], "assessment": a["assessment"],
                 "objective_ids": list(a["objective_ids"])}
                for a in contract["assignments"]
            ],
            "modules": [
                {"module_id": m["module_id"], "title": m["title"], "order": m["order"]}
                for m in accepted.track["modules"]
            ],
            "objectives": [
                {"objective_id": o["objective_id"], "title": o["title"],
                 "module_id": o["module_id"],
                 "prerequisites": list(o["prerequisites"]),
                 "check_kinds": list(o["check_kinds"]),
                 "required": bool(o.get("required"))}
                for o in accepted.track["objectives"]
            ],
            "readable_roots": list(contract["student_material_roots"]),
            "excluded_roots": list(contract.get("excluded_material_roots") or []),
        },
        warnings=["Материалы под исключёнными корнями недостижимы и здесь не перечислены."],
    )


def material_read(server, arguments):
    """Read one permitted material file, bounded."""
    course_id = arguments["course_id"]
    relative = arguments["path"]
    try:
        accepted = server._course(course_id)
    except course_mod.CourseError as e:
        return _fail(e.code, e.message)

    if not accepted.is_readable_path(relative):
        return _fail(
            "MATERIAL_OUT_OF_SCOPE",
            "путь %r вне разрешённых материалов курса: решения и материалы "
            "преподавателя не читаются" % relative,
        )

    try:
        scope = retrieval.resolve_scope(
            accepted, material_snapshot=accepted.binding.get("material_snapshot"))
    except retrieval.RetrievalError as e:
        return _fail(e.code, e.message)

    for candidate, path in scope["files"]:
        if candidate != relative:
            continue
        data = path.read_bytes()
        if len(data) > 32 * 1024:
            return _fail(
                "MATERIAL_TOO_LARGE",
                "материал больше 32 КиБ: читайте по фрагментам через locator",
            )
        return _ok("Материал прочитан", {
            "path": relative,
            "text": data.decode("utf-8", errors="replace"),
            "byte_size": len(data),
            "using_snapshot": scope["using_snapshot"],
        })

    return _fail("MATERIAL_NOT_FOUND", "файл %r не найден среди разрешённых" % relative)


def source_search(server, arguments):
    course_id = arguments["course_id"]
    try:
        accepted = server._course(course_id)
    except course_mod.CourseError as e:
        return _fail(e.code, e.message)
    try:
        result = retrieval.search(
            accepted, arguments["query"], limit=arguments.get("limit") or 5,
            material_snapshot=accepted.binding.get("material_snapshot"))
    except retrieval.RetrievalError as e:
        return _fail(e.code, e.message)
    return _ok("Найдено попаданий: %d" % len(result["hits"]), result)


def quote_verify(server, arguments):
    course_id = arguments["course_id"]
    try:
        accepted = server._course(course_id)
    except course_mod.CourseError as e:
        return _fail(e.code, e.message)
    try:
        report = retrieval.verify_quote(
            accepted, arguments["citation"],
            material_snapshot=accepted.binding.get("material_snapshot"))
    except retrieval.RetrievalError as e:
        return _fail(e.code, e.message)

    allowed, warnings = retrieval.validate_citation(report)
    report["allowed"] = allowed
    message = ("Цитата подтверждена" if allowed
               else "Цитату нельзя показывать как точную: %s"
                    % report.get("quote_status_reason"))
    return _ok(message, report, warnings=warnings)


# --------------------------------------------------------------------------
# The teaching cycle
# --------------------------------------------------------------------------

def session_start(server, arguments):
    course_id = arguments["course_id"]
    try:
        accepted = server._course(course_id)
    except course_mod.CourseError as e:
        return _fail(e.code, e.message)

    with _store(server, create=True) as store:
        service = session_mod.SessionService(store, accepted, clock=server.clock)
        consent_version = None
        for consent in store.list_entities("consent", course_id=course_id):
            if consent.get("withdrawn_at") is None:
                consent_version = consent.get("consent_id")
                break

        body, result, replayed = service.start(
            objective_ids=[arguments["objective_id"]] if arguments.get("objective_id") else [],
            consent_version=consent_version,
            request_id=arguments.get("request_id"),
        )
        missing = []
        if consent_version is None:
            missing.append("consent")
        if not arguments.get("objective_id"):
            missing.append("goal")
        return _ok(
            "Занятие открыто" + ("" if not missing else "; не хватает: %s" % ", ".join(missing)),
            {"session": body, "missing": missing},
            warnings=(["Занятие не начнёт учить, пока не записано согласие."]
                      if "consent" in missing else []),
            effects=["session.created"],
        )


def session_next(server, arguments):
    session_id = arguments["session_id"]
    with _store(server) as store:
        for body in store.list_entities("session"):
            if body.get("session_id") != session_id:
                continue
            course_id = body["course_id"]
            accepted = server._course(course_id)
            service = session_mod.SessionService(store, accepted, clock=server.clock)
            _, directive = service.next_step(session_id)
            return _ok("Следующий шаг вычислен", dict(directive),
                       warnings=["Это чистое чтение: рекомендация не изменила состояние."])
    return _fail("SESSION_NOT_FOUND", "сессия не найдена: %s" % session_id)


def response_check(server, arguments):
    session_id = arguments["session_id"]
    with _store(server) as store:
        for body in store.list_entities("session"):
            if body.get("session_id") != session_id:
                continue
            accepted = server._course(body["course_id"])
            body = dict(body)
            body["session_id"] = session_id
            accepted_flag, violations, action = tutoring_mod.validate_response(
                accepted, body, arguments["teaching_response"])
            return _ok(
                "Ответ соответствует ограничениям" if accepted_flag
                else "Ответ нарушает ограничения: %d" % len(violations),
                {"accepted": accepted_flag, "violations": violations,
                 "safe_next_action": action},
                warnings=["Проверяется форма, а не смысл: текстовый спойлер этим "
                          "не обнаруживается."],
            )
    return _fail("SESSION_NOT_FOUND", "сессия не найдена: %s" % session_id)


def policy_check(server, arguments):
    try:
        accepted = server._course(arguments["course_id"])
    except course_mod.CourseError as e:
        return _fail(e.code, e.message)

    intent = {"HINT": "hint", "EXAMPLE": "example", "SOLUTION": "solution"}[arguments["level"]]
    decision = policy_mod.request_help(
        accepted, assignment_id=arguments["assignment_id"], intent=intent,
        preference=arguments.get("preference") or "prefer-ask",
        student_requested=True)
    resolved = policy_mod.resolve_assessment(accepted, arguments["assignment_id"])
    return _ok(
        decision["message_ru"],
        {"decision": dict(decision), "assessment": resolved},
        warnings=(["Основание: %s" % decision.get("rule_ref")]
                  if decision.get("rule_ref") else []),
    )


def attempt_record(server, arguments):
    session_id = arguments["session_id"]
    with _store(server, create=True) as store:
        for body in store.list_entities("session"):
            if body.get("session_id") != session_id:
                continue
            accepted = server._course(body["course_id"])
            service = session_mod.SessionService(store, accepted, clock=server.clock)
            try:
                document, _, _ = service.record_attempt(
                    session_id,
                    objective_id=arguments.get("objective_id") or body.get("current_objective_id"),
                    assignment_id=arguments.get("assignment_id") or body.get("current_assignment_id"),
                    text_excerpt=arguments.get("text"),
                    artifact_sha256=arguments.get("artifact_sha256"),
                    request_id=arguments.get("request_id"),
                )
            except tutoring_mod.TutoringError as e:
                return _fail(e.code, e.message)
            return _ok(
                "Попытка записана",
                {"attempt": document},
                warnings=["Источник — сообщение ученика: это не доказательство "
                          "авторства, а основание для обратной связи."],
                effects=["attempt.recorded"],
            )
    return _fail("SESSION_NOT_FOUND", "сессия не найдена: %s" % session_id)


def assistance_record(server, arguments):
    session_id = arguments["session_id"]
    with _store(server) as store:
        for body in store.list_entities("session"):
            if body.get("session_id") != session_id:
                continue
            accepted = server._course(body["course_id"])
            service = session_mod.SessionService(store, accepted, clock=server.clock)
            try:
                _, _, _, decision = service.record_assistance(
                    session_id, level=arguments["level"], step=arguments["step"],
                    reason=arguments["reason"], response_ref=arguments.get("response_ref"),
                    expected_version=arguments.get("expected_version"),
                    request_id=arguments.get("request_id"),
                )
            except tutoring_mod.TutoringError as e:
                return _fail(e.code, e.message, data={"decision": None})
            return _ok("Помощь уровня %s записана" % arguments["level"],
                       {"decision": dict(decision)}, effects=["assistance.recorded"])
    return _fail("SESSION_NOT_FOUND", "сессия не найдена: %s" % session_id)


def check_record(server, arguments):
    session_id = arguments["session_id"]
    with _store(server) as store:
        for body in store.list_entities("session"):
            if body.get("session_id") != session_id:
                continue
            accepted = server._course(body["course_id"])
            service = session_mod.SessionService(store, accepted, clock=server.clock)
            try:
                document, state, reasoning, _, _ = service.record_check(
                    session_id, attempt_id=arguments["attempt_id"],
                    kind=arguments["kind"], verdict=arguments["verdict"],
                    criterion_results=arguments["criterion_results"],
                    assessed_by="model",
                    expected_version=arguments.get("expected_version"),
                    request_id=arguments.get("request_id"),
                )
            except tutoring_mod.TutoringError as e:
                return _fail(e.code, e.message)
            return _ok(
                "Проверка записана; освоение: %s" % state["stage"],
                {"check": document, "objective_state": state,
                 "reasoning_ru": reasoning[1]},
                warnings=["Модельная проверка — формирующая: это не официальная оценка."],
                effects=["check.recorded", "objective.transitioned"],
            )
    return _fail("SESSION_NOT_FOUND", "сессия не найдена: %s" % session_id)


def session_transition(server, arguments):
    session_id = arguments["session_id"]
    with _store(server) as store:
        for body in store.list_entities("session"):
            if body.get("session_id") != session_id:
                continue
            accepted = server._course(body["course_id"])
            service = session_mod.SessionService(store, accepted, clock=server.clock)
            try:
                updated, _, _ = service.transition(
                    session_id, arguments["target"],
                    expected_version=arguments["expected_version"],
                    reason=arguments.get("reason"),
                    request_id=arguments.get("request_id"),
                )
            except tutoring_mod.TutoringError as e:
                return _fail(e.code, e.message)
            return _ok("Состояние занятия: %s" % updated["state"],
                       {"session": updated}, effects=["session.state_changed"])
    return _fail("SESSION_NOT_FOUND", "сессия не найдена: %s" % session_id)


def progress_get(server, arguments):
    course_id = arguments["course_id"]
    from . import progress as progress_mod
    with _store(server) as store:
        try:
            accepted = server._course(course_id)
        except course_mod.CourseError as e:
            return _fail(e.code, e.message)
        document = progress_mod.build_progress(
            course=accepted, learner_id=server.learner_id,
            sessions=store.list_entities("session", course_id=course_id),
            objective_states={s["objective_id"]: s for s in
                              store.list_entities("objective_state", course_id=course_id)},
            attempts=store.list_entities("attempt", course_id=course_id),
            checks=store.list_entities("check", course_id=course_id),
        )
        return _ok("Прогресс по курсу %s" % course_id, document)


# --------------------------------------------------------------------------
# Contribution: read and draft only
# --------------------------------------------------------------------------

def contribution_get(server, arguments):
    from . import contribution as contribution_mod
    course_id = arguments["course_id"]
    contribution_id = arguments.get("contribution_id")
    with _store(server) as store:
        items = store.list_entities("contribution", course_id=course_id)
        if contribution_id:
            items = [i for i in items if i.get("contribution_id") == contribution_id]
        if not items:
            return _fail("CONTRIBUTION_NOT_FOUND",
                         "вклад не найден; открыть его может только ученик "
                         "командой contribute-start")
        document = items[0]
        snapshot = None
        if document.get("repository_root"):
            try:
                snapshot = contribution_mod.working_diff(document["repository_root"])
            except contribution_mod.ContributionError as e:
                snapshot = {"error": e.message, "code": e.code}
        return _ok("Состояние вклада", {"contribution": document, "git": snapshot},
                   warnings=["Наблюдение только на чтение. Коммит, push и PR "
                             "делает ученик."])


def contribution_draft(server, arguments):
    from . import contribution as contribution_mod
    course_id = arguments["course_id"]
    contribution_id = arguments["contribution_id"]
    with _store(server) as store:
        for document in store.list_entities("contribution", course_id=course_id):
            if document.get("contribution_id") != contribution_id:
                continue
            snapshot = None
            if document.get("repository_root"):
                try:
                    snapshot = contribution_mod.working_diff(document["repository_root"])
                except contribution_mod.ContributionError:
                    snapshot = None
            text = contribution_mod.render_draft(document, snapshot=snapshot)
            ok, blocking, advisory = contribution_mod.validate_draft(
                document, snapshot=snapshot)
            return _ok("Черновик подготовлен", {
                "draft": text, "ready_to_publish": ok,
                "blocking": blocking, "advisory": advisory,
            }, warnings=["Это черновик. Публикацию выполняет ученик."])
    return _fail("CONTRIBUTION_NOT_FOUND", "вклад не найден: %s" % contribution_id)


# --------------------------------------------------------------------------
# Presentation and operations
# --------------------------------------------------------------------------

def presentation_set(server, arguments):
    course_id = arguments["course_id"]
    settings = personas_mod.resolve(arguments.get("persona_id") or personas_mod.DEFAULT_PERSONA,
                                    gamification=bool(arguments.get("gamification")))
    with _store(server, create=True) as store:
        entity_id = "%s:presentation" % course_id
        body, version = store.get("presentation", entity_id, course_id=course_id)
        document = dict(body or {})
        document.update({
            "schema_version": 2,
            "course_id": course_id,
            "persona_id": settings["persona_id"],
            "gamification": settings["gamification"],
            "low_stimulus": settings["low_stimulus"],
        })
        store.apply(kind="presentation", entity_id=entity_id,
                    events=["profile.configured"], course_id=course_id,
                    expected_version=version if body else None, new_body=document,
                    event_payloads=[{"changed_fields": ["persona_id", "gamification"],
                                     "user_request_ref": arguments["user_request_ref"],
                                     "_actor": "model_proposal",
                                     "_provenance": "model_reported"}])
    return _ok("Оформление выбрано: %s" % settings["persona_id"],
               {"presentation": settings},
               warnings=["Персона меняет только стиль: права, задания и правила "
                         "помощи остаются прежними."])


def env_status(server, arguments):
    with _store(server) as store:
        service = operation_mod.OperationService(store, server.root, clock=server.clock)
        history = service.get_operation(arguments["operation_id"])
        if history is None:
            return _fail("OPERATION_NOT_FOUND", "операция не найдена")
        plan = service.get_plan(arguments["operation_id"])
        return _ok("Состояние операции: %s" % history["status"],
                   {"operation": history,
                    "steps_total": len(plan["steps"]) if plan else None})


def operation_cancel(server, arguments):
    with _store(server) as store:
        service = operation_mod.OperationService(store, server.root, clock=server.clock)
        try:
            result = service.cancel(arguments["operation_id"],
                                    request_id=arguments["request_id"])
        except operation_mod.OperationError as e:
            return _fail(e.code, e.message)
    return _ok(result["message_ru"], result, effects=["operation.cancellation_requested"])


HANDLERS = {
    "course_context": course_context,
    "material_read": material_read,
    "session_start": session_start,
    "session_next": session_next,
    "response_check": response_check,
    "policy_check": policy_check,
    "attempt_record": attempt_record,
    "assistance_record": assistance_record,
    "check_record": check_record,
    "session_transition": session_transition,
    "progress_get": progress_get,
    "source_search": source_search,
    "quote_verify": quote_verify,
    "contribution_get": contribution_get,
    "contribution_draft": contribution_draft,
    "presentation_set": presentation_set,
    "env_status": env_status,
    "operation_cancel": operation_cancel,
}
