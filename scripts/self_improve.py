"""Local-only, human-reviewed self-improvement command parser and workflow."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from botai_core.self_improvement import SelfImprovement, SelfImprovementError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/cli.py self-improve",
        description="Локальный журнал наблюдений и подтверждаемые человеком изменения guidance",
    )
    parser.add_argument("--root", default=".", help="корень workspace (по умолчанию текущий каталог)")
    parser.add_argument("--json", action="store_true", help="вывести результат в JSON")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("help", help="показать справку")
    sub.add_parser("status", help="состояние локального журнала")
    sub.add_parser("recover", help="синхронизировать журнал после прерванной операции")
    triage = sub.add_parser("triage", help="показать записи, требующие человеческой проверки")
    triage.add_argument("--area")
    triage.add_argument("--limit", type=int, default=50)
    migrate = sub.add_parser("migrate", help="скопировать валидированные записи в SQLite store")
    migrate.add_argument("--learner-id", default="self-improvement")
    migrate.add_argument("--recovery", action="store_true", help="завершить ранее прерванную миграцию")
    sub.add_parser("enable", help="включить приостановленное самоулучшение")
    sub.add_parser("disable", help="приостановить запись и изменения, сохранив журнал")

    capture = sub.add_parser("capture", help="добавить краткое наблюдение без транскрипта")
    capture.add_argument("--kind", required=True, choices=["correction", "error", "feature_request", "best_practice", "knowledge_gap"])
    capture.add_argument("--area", required=True)
    capture.add_argument("--summary", required=True)
    capture.add_argument("--source-type", default="other")
    capture.add_argument("--source-ref", default="")
    capture.add_argument("--revision", default="")
    capture.add_argument("--request-id", default="")

    search = sub.add_parser("search", help="найти записи по краткому тексту или области")
    search.add_argument("--query", default="")
    search.add_argument("--area")
    search.add_argument("--limit", type=int, default=10)

    show = sub.add_parser("show", help="показать запись")
    show.add_argument("--entry", required=True)

    review = sub.add_parser("review", help="зафиксировать вывод и проверяемое свидетельство")
    review.add_argument("--entry", required=True)
    review.add_argument("--claim", required=True)
    review.add_argument("--evidence-type", required=True)
    review.add_argument("--evidence-ref", required=True)
    review.add_argument("--result", required=True)
    review.add_argument("--validation", required=True, choices=["verified", "unresolved", "contradicted"])
    review.add_argument("--scope", default="workspace")
    review.add_argument("--expected-version", required=True, type=int)

    retire = sub.add_parser("retire", help="архивировать запись без удаления истории")
    retire.add_argument("--entry", required=True)
    retire.add_argument("--reason", required=True)
    retire.add_argument("--expected-version", required=True, type=int)

    propose = sub.add_parser("propose", help="создать draft для разрешённого guidance Markdown")
    propose.add_argument("--entry", required=True)
    propose.add_argument("--target", required=True)
    propose.add_argument("--old-text", required=True)
    propose.add_argument("--new-text", required=True)
    propose.add_argument("--rationale", required=True)

    for name in ("preview", "verify", "approve"):
        command = sub.add_parser(name, help={"preview": "показать точный diff", "verify": "структурно проверить diff", "approve": "подтвердить конкретный diff в TTY и выдать challenge"}[name])
        command.add_argument("--proposal", required=True)
    apply = sub.add_parser("apply", help="применить proposal только с TTY-подтверждением")
    apply.add_argument("--proposal", required=True)
    apply.add_argument("--approval", required=True)
    revert = sub.add_parser("revert", help="откатить применение, если файл не менялся после него")
    revert.add_argument("--application", required=True)
    for command_parser in sub.choices.values():
        command_parser.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        command_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return parser


def _render(value, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif isinstance(value, dict) and isinstance(value.get("diff"), str):
        print(value["diff"], end="" if value["diff"].endswith("\n") else "\n")
        print(f"proposal_id: {value['proposal_id']}\nstatus: {value['status']}\nfingerprint: {value['fingerprint']}")
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def run(root: Path, args) -> int:
    service = SelfImprovement(root)
    try:
        if args.action == "help":
            build_parser().print_help()
            return 0
        if args.action == "status":
            result = service.status()
        elif args.action == "recover":
            result = service.recover()
        elif args.action == "triage":
            result = service.triage(area=args.area, limit=args.limit)
        elif args.action == "migrate":
            result = service.migrate_to_store(learner_id=args.learner_id, recovery=args.recovery)
        elif args.action == "enable":
            service.enable()
            result = service.status()
        elif args.action == "disable":
            service.disable()
            result = service.status()
        elif args.action == "capture":
            result = service.capture(kind=args.kind, area=args.area, summary=args.summary,
                                     source_type=args.source_type, source_ref=args.source_ref,
                                     revision=args.revision, request_id=args.request_id)
        elif args.action == "search":
            result = service.search(args.query, args.area, args.limit)
        elif args.action == "show":
            result = service.get_entry(args.entry)
        elif args.action == "review":
            result = service.review(args.entry, claim=args.claim, evidence_type=args.evidence_type,
                                    evidence_ref=args.evidence_ref, result=args.result,
                                    validation=args.validation, scope=args.scope,
                                    expected_version=args.expected_version)
        elif args.action == "retire":
            result = service.retire(args.entry, reason=args.reason,
                                    expected_version=args.expected_version)
        elif args.action == "propose":
            result = service.propose(entry_id=args.entry, relative_path=args.target,
                                     old_text=args.old_text, new_text=args.new_text,
                                     rationale=args.rationale)
        elif args.action == "preview":
            result = service.preview(args.proposal)
        elif args.action == "verify":
            result = service.verify(args.proposal)
        elif args.action == "approve":
            if not sys.stdin.isatty():
                raise SelfImprovementError("Одобрение возможно только в интерактивном терминале человека")
            proposal = service.preview(args.proposal)
            _render(proposal, False)
            print("Внимание: структурная проверка не оценивает смысл. Путь ограничен guidance, но формулировка может влиять на поведение агента. Не одобряйте изменения политики, consent-gate или правил помощи; при сомнении откажитесь.")
            if input(f"Введите approve {args.proposal}: ").strip() != f"approve {args.proposal}":
                raise SelfImprovementError("Предложение не одобрено")
            approval_id, challenge = service.approve(args.proposal)
            result = {"approval_id": approval_id, "challenge": challenge, "expires_in_seconds": 300,
                      "warning": "Одноразовый challenge привязан к fingerprint proposal; локальные права пользователя не являются границей безопасности."}
        elif args.action == "apply":
            if not sys.stdin.isatty():
                raise SelfImprovementError("Применение возможно только в интерактивном терминале человека")
            proposal = service.preview(args.proposal)
            _render(proposal, False)
            if input(f"Введите apply {args.proposal}: ").strip() != f"apply {args.proposal}":
                raise SelfImprovementError("Применение не подтверждено")
            challenge = input("Одноразовый код: ").strip()
            result = service.apply(args.proposal, args.approval, challenge)
        elif args.action == "revert":
            result = service.revert(args.application)
        else:
            raise SelfImprovementError("Неизвестное действие")
        _render(result, args.json)
        return 0
    except (SelfImprovementError, OSError, UnicodeError) as exc:
        print(f"self-improve: {exc}", file=sys.stderr)
        return 2


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0
    if arguments[0] == "--root":
        try:
            root_index = arguments.index("--root")
            root_value = arguments[root_index + 1]
        except (ValueError, IndexError):
            build_parser().error("--root требует путь")
        del arguments[root_index:root_index + 2]
        if arguments and arguments[-1] == "help":
            build_parser().print_help()
            return 0
        os.environ["BOTAI_ROOT"] = str(Path(root_value).expanduser().resolve())
    if arguments[0] == "help":
        build_parser().print_help()
        return 0
    parser = build_parser()
    args = parser.parse_args(arguments)
    return run(Path(args.root).expanduser().resolve(), args)


if __name__ == "__main__":
    raise SystemExit(main())
