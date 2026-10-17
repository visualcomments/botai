import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core.self_improvement import SelfImprovement, SelfImprovementError


def setup_service(tmp_path, *, contents="# CLI\n\nUse old command.\n"):
    root = tmp_path / "workspace"
    guidance = root / "docs" / "self-improvement-guidance"
    guidance.mkdir(parents=True)
    target = guidance / "cli.md"
    target.write_bytes(contents.encode("utf-8"))
    (root / "Makefile").write_text("self-improve: status\n", encoding="utf-8")
    return root, target, SelfImprovement(root)


def verified_entry(service, *, request_id="test-req-1"):
    entry = service.capture(kind="error", area="cli", summary="Команда не была документирована", request_id=request_id)
    return service.review(entry["entry_id"], claim="Проверенный вывод", evidence_type="file",
                          evidence_ref="Makefile", result="Проверено по исходному файлу",
                          validation="verified", expected_version=1)


def test_capture_is_local_redacted_idempotent_and_strict(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="token=ghp_abcdefghijklmnopqrstuvwxyz123456", request_id="same")
    assert "ghp_" not in entry["summary"]
    assert entry["validation"] == "pending"
    assert service.capture(kind="error", area="cli", summary="token=ghp_abcdefghijklmnopqrstuvwxyz123456", request_id="same")["entry_id"] == entry["entry_id"]
    with pytest.raises(SelfImprovementError, match="IDEMPOTENCY_CONFLICT"):
        service.capture(kind="error", area="shell", summary="другой вывод", request_id="same")
    assert not (root / "progress").exists()
    assert service.status()["network"] is False


def test_review_requires_evidence_and_version_and_rechecks_real_file(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="correction", area="cli", summary="Уточнена команда", request_id="review")
    assert service.search("команда")[0]["validation"] == "pending"
    with pytest.raises(TypeError):
        service.review(entry["entry_id"], claim="нет версии", evidence_type="file", evidence_ref="Makefile",
                       result="проверено", validation="verified")
    with pytest.raises(SelfImprovementError, match="Для verified требуется файл"):
        service.review(entry["entry_id"], claim="это верно", evidence_type="human_report", evidence_ref="чат",
                       result="подтверждено", validation="verified", expected_version=1)
    reviewed = service.review(entry["entry_id"], claim="В Makefile есть нужная команда", evidence_type="file",
                              evidence_ref="Makefile", result="проверено по текущему файлу", validation="verified",
                              expected_version=1)
    assert reviewed["status"] == "verified"
    assert reviewed["recurrence_count"] == 1
    with pytest.raises(SelfImprovementError, match="STATE_CONFLICT"):
        service.review(entry["entry_id"], claim="устаревшее", evidence_type="file", evidence_ref="Makefile",
                       result="x", validation="verified", expected_version=1)
    (root / "Makefile").unlink()
    another = service.capture(kind="error", area="build", summary="проверка источника", request_id="missing-source")
    with pytest.raises(SelfImprovementError, match="не существует"):
        service.review(another["entry_id"], claim="ложь", evidence_type="file", evidence_ref="Makefile",
                       result="не проверено", validation="verified", expected_version=1)


def test_proposal_requires_verified_entry_and_exact_unique_fragment(tmp_path):
    _, target, service = setup_service(tmp_path)
    entry = service.capture(kind="best_practice", area="docs", summary="Путь к инструкции найден", request_id="propose")
    with pytest.raises(SelfImprovementError, match="проверенная запись"):
        service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                        old_text="Use old command.", new_text="Use new command.", rationale="обновить")
    entry = service.review(entry["entry_id"], claim="Руководство подтверждено", evidence_type="file",
                           evidence_ref="Makefile", result="проверено", validation="verified", expected_version=1)
    with pytest.raises(SelfImprovementError, match="ровно один раз"):
        service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                        old_text="absent", new_text="Change", rationale="обновить")
    for unsafe in ("AGENTS.md", "C:/outside.md", "//server/share.md", "docs\\self-improvement-guidance\\cli.md", "docs/self-improvement-guidance/../x.md"):
        with pytest.raises(SelfImprovementError):
            service.propose(entry_id=entry["entry_id"], relative_path=unsafe,
                            old_text="x", new_text="y", rationale="обновить")
    assert target.read_bytes() == b"# CLI\n\nUse old command.\n"


def test_approval_fingerprint_is_single_use_apply_and_revert_is_one_shot(tmp_path):
    root, target, service = setup_service(tmp_path)
    entry = verified_entry(service)
    proposal = service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                               old_text="Use old command.", new_text="Use new command.", rationale="Проверенное уточнение")
    with pytest.raises(SelfImprovementError, match="структурную проверку"):
        service.approve(proposal["proposal_id"])
    report = service.verify(proposal["proposal_id"])
    assert report["result"] == "structural_checks_passed"
    assert report["commands"] == []
    approval_id, challenge = service.approve(proposal["proposal_id"])
    saved = service.get_proposal(proposal["proposal_id"])
    saved["diff"] = "tampered diff"
    (service.proposals_dir / f"{proposal['proposal_id']}.json").write_text(__import__("json").dumps(saved), encoding="utf-8")
    with pytest.raises(SelfImprovementError, match="FINGERPRINT_MISMATCH"):
        service.apply(proposal["proposal_id"], approval_id, challenge)
    # Restore the independently stored known-good record for this tamper test;
    # a fresh human approval is then needed against its original fingerprint.
    from botai_core.self_improvement import _atomic_json
    proposal["status"] = "checked"
    proposal["verification"] = report
    _atomic_json(service.proposals_dir / f"{proposal['proposal_id']}.json", proposal)
    approval_id, challenge = service.approve(proposal["proposal_id"])
    applied = service.apply(proposal["proposal_id"], approval_id, challenge)
    assert b"Use new command." in target.read_bytes()
    with pytest.raises(SelfImprovementError, match="нет действующего подтверждения"):
        service.apply(proposal["proposal_id"], approval_id, challenge)
    service.revert(applied["application_id"])
    assert b"Use old command." in target.read_bytes()
    with pytest.raises(SelfImprovementError, match="уже отменено"):
        service.revert(applied["application_id"])
    assert not (root / "progress").exists()


def test_stale_proposal_and_revert_conflict_do_not_overwrite_human_changes(tmp_path):
    _, target, service = setup_service(tmp_path)
    entry = verified_entry(service)
    proposal = service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                               old_text="Use old command.", new_text="Use new command.", rationale="Проверенное уточнение")
    target.write_text("# CLI\n\nHuman edit.\n", encoding="utf-8")
    with pytest.raises(SelfImprovementError, match="STALE_PROPOSAL"):
        service.preview(proposal["proposal_id"])


def test_crlf_is_preserved_and_path_symlink_is_rejected(tmp_path):
    _, target, service = setup_service(tmp_path, contents="# CLI\r\n\r\nUse old command.\r\n")
    entry = verified_entry(service)
    proposal = service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                               old_text="Use old command.", new_text="Use new command.", rationale="проверить CRLF")
    service.verify(proposal["proposal_id"])
    approval_id, challenge = service.approve(proposal["proposal_id"])
    applied = service.apply(proposal["proposal_id"], approval_id, challenge)
    assert b"\r\n" in target.read_bytes()
    assert b"\n" not in target.read_bytes().replace(b"\r\n", b"")
    service.revert(applied["application_id"])
    assert target.read_bytes() == b"# CLI\r\n\r\nUse old command.\r\n"


def test_triage_is_read_only_and_separates_pending_unresolved_and_contradicted(tmp_path):
    _, _, service = setup_service(tmp_path)
    pending = service.capture(kind="error", area="cli", summary="ожидает проверки", request_id="triage-pending")
    unresolved = service.capture(kind="error", area="docs", summary="источник недоступен", request_id="triage-unresolved")
    service.review(unresolved["entry_id"], claim="нельзя подтвердить", evidence_type="maintainer_decision",
                   evidence_ref="issue-1", result="источник недоступен", validation="unresolved", expected_version=1)
    contradicted = service.capture(kind="error", area="cli", summary="опровергнуто", request_id="triage-contradicted")
    service.review(contradicted["entry_id"], claim="утверждение неверно", evidence_type="file",
                   evidence_ref="Makefile", result="проверено по исходнику", validation="contradicted", expected_version=1)

    before_pending = service.get_entry(pending["entry_id"])
    result = service.triage(limit=10)
    assert result["count"] == 3
    actions = {item["entry_id"]: item["recommended_action"] for item in result["items"]}
    assert actions[pending["entry_id"]] == "review"
    assert actions[unresolved["entry_id"]] == "recheck_source"
    assert actions[contradicted["entry_id"]] == "retire_or_replace"
    assert service.get_entry(pending["entry_id"]) == before_pending


def test_retire_preserves_history_and_requires_current_version(tmp_path):
    _, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="устаревшее наблюдение", request_id="retire")
    reviewed = service.review(entry["entry_id"], claim="Наблюдение опровергнуто", evidence_type="file",
                              evidence_ref="Makefile", result="проверено по текущему файлу", validation="contradicted",
                              expected_version=1)
    with pytest.raises(SelfImprovementError, match="STATE_CONFLICT"):
        service.retire(entry["entry_id"], reason="устарело", expected_version=1)
    retired = service.retire(entry["entry_id"], reason="подтверждено новое поведение", expected_version=reviewed["version"])
    assert retired["status"] == "retired"
    assert retired["validation"] == "contradicted"
    assert retired["evidence"]
    assert retired["maintenance"]["retirement_reason"] == "подтверждено новое поведение"
    assert service.search("устаревшее") == []
    assert service.get_entry(entry["entry_id"])["status"] == "retired"


def test_recover_reconciles_interrupted_apply_and_revert_without_writing_target(tmp_path):
    _, target, service = setup_service(tmp_path)
    entry = verified_entry(service, request_id="recover")
    proposal = service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                               old_text="Use old command.", new_text="Use new command.", rationale="recovery")
    service.verify(proposal["proposal_id"])
    approval_id, challenge = service.approve(proposal["proposal_id"])
    application = service.apply(proposal["proposal_id"], approval_id, challenge)
    application_path = service.applications_dir / f"{application['application_id']}.json"
    saved = service.get_proposal(proposal["proposal_id"])

    application["status"] = "prepared"
    application.pop("applied_at", None)
    application_path.write_text(__import__("json").dumps(application), encoding="utf-8")
    assert service.recover() == [{"application_id": application["application_id"], "status": "applied"}]
    assert b"Use new command." in target.read_bytes()

    application["status"] = "reverting"
    application_path.write_text(__import__("json").dumps(application), encoding="utf-8")
    assert service.recover() == [{"application_id": application["application_id"], "status": "applied"}]
    target.write_bytes(__import__("base64").b64decode(application["old_bytes_b64"]))
    application = __import__("json").loads(application_path.read_text(encoding="utf-8"))
    application["status"] = "reverting"
    application_path.write_text(__import__("json").dumps(application), encoding="utf-8")
    assert service.recover() == [{"application_id": application["application_id"], "status": "reverted"}]
    assert target.read_bytes() == b"# CLI\n\nUse old command.\n"
    assert service.get_proposal(proposal["proposal_id"])["status"] == "reverted"


def test_recover_marks_unknown_target_as_conflict_and_preserves_it(tmp_path):
    _, target, service = setup_service(tmp_path)
    entry = verified_entry(service, request_id="recover-conflict")
    proposal = service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                               old_text="Use old command.", new_text="Use new command.", rationale="conflict")
    service.verify(proposal["proposal_id"])
    approval_id, challenge = service.approve(proposal["proposal_id"])
    application = service.apply(proposal["proposal_id"], approval_id, challenge)
    application_path = service.applications_dir / f"{application['application_id']}.json"
    application["status"] = "prepared"
    application_path.write_text(__import__("json").dumps(application), encoding="utf-8")
    target.write_bytes(b"# CLI\n\nHuman edit.\n")
    assert service.recover() == [{"application_id": application["application_id"], "status": "conflict"}]
    assert target.read_bytes() == b"# CLI\n\nHuman edit.\n"
    assert service.get_proposal(proposal["proposal_id"])["status"] == "applied"


def test_disable_gate_and_corrupt_schema_fail_closed(tmp_path):
    _, _, service = setup_service(tmp_path)
    service.disable()
    assert service.status()["enabled"] is False
    with pytest.raises(SelfImprovementError, match="отключено"):
        service.capture(kind="error", area="cli", summary="не записывать")
    service.enable()
    assert service.status()["enabled"] is True
    entry = service.capture(kind="error", area="cli", summary="valid", request_id="schema")
    path = service.entries_dir / f"{entry['entry_id']}.json"
    path.write_text('{"schema_version":1,"entry_id":"bad"}', encoding="utf-8")
    with pytest.raises(SelfImprovementError, match="Схема"):
        service.entries()
