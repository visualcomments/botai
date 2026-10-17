import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core.self_improvement import SelfImprovement, SelfImprovementError
from botai_core.store import Store


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


def test_promotion_requires_applied_proposal_and_never_injects_runtime(tmp_path):
    _, target, service = setup_service(tmp_path)
    entry = verified_entry(service, request_id="promotion")
    proposal = service.propose(entry_id=entry["entry_id"], relative_path="docs/self-improvement-guidance/cli.md",
                               old_text="Use old command.", new_text="Use promoted command.", rationale="promotion")
    with pytest.raises(SelfImprovementError, match="PROMOTION_INVALID"):
        service.promote(proposal["proposal_id"], rationale="не применено")
    service.verify(proposal["proposal_id"])
    approval_id, challenge = service.approve(proposal["proposal_id"])
    service.apply(proposal["proposal_id"], approval_id, challenge)
    record = service.promote(proposal["proposal_id"], rationale="Человек явно подтвердил актуальность")
    assert record["proposal_fingerprint"] == proposal["fingerprint"]
    assert record["runtime_injected"] is False
    assert service.promotion_records()[0]["record_sha256"] == record["record_sha256"]
    assert b"Use promoted command." in target.read_bytes()


def test_purge_requires_consent_and_keeps_tombstone_without_content(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="удалить", request_id="purge")
    preview = service.purge_preview()
    assert preview["consent_required"] is True
    with pytest.raises(SelfImprovementError, match="PURGE_CONSENT_REQUIRED"):
        service.purge()
    result = service.purge(consent=True)
    assert result["removed"] >= 1
    assert list(service.entries_dir.glob("*.json")) == []
    tombstones = list(service.state_dir.glob("purge-*.json"))
    assert len(tombstones) == 1
    tombstone = __import__("json").loads(tombstones[0].read_text(encoding="utf-8"))
    assert entry["summary"] not in str(tombstone)
    assert tombstone["tombstone_sha256"] == result["tombstone_sha256"]


def test_backup_restore_uses_manifest_and_requires_consent(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="backup", request_id="backup")
    preview = service.backup_preview()
    assert preview["consent_required"] is True
    with pytest.raises(SelfImprovementError, match="BACKUP_CONSENT_REQUIRED"):
        service.backup_to("backup")
    saved = service.backup_to("backup", consent=True)
    assert saved["files"] >= 1
    restored = service.backup_restore_preview("backup")
    assert restored["manifest_sha256"] == saved["manifest_sha256"]
    path = service.entries_dir / f"{entry['entry_id']}.json"
    path.unlink()
    with pytest.raises(SelfImprovementError, match="RESTORE_CONSENT_REQUIRED"):
        service.backup_restore("backup")
    service.backup_restore("backup", consent=True)
    assert path.is_file()
    assert service.get_entry(entry["entry_id"])["summary"] == "backup"


def test_import_evidence_requires_valid_report_hash_and_current_version(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="evidence", request_id="evidence")
    report_path = root / "reports" / "self-improvement.json"
    service.export_to_file("reports/self-improvement.json", consent=True)
    imported = service.import_evidence(entry["entry_id"], "reports/self-improvement.json", expected_version=1)
    assert imported["version"] == 2
    assert imported["evidence"][-1]["type"] == "report"
    with pytest.raises(SelfImprovementError, match="STATE_CONFLICT"):
        service.import_evidence(entry["entry_id"], "reports/self-improvement.json", expected_version=1)
    payload = __import__("json").loads(report_path.read_text(encoding="utf-8"))
    payload["entries"][0]["summary"] = "tampered"
    report_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    with pytest.raises(SelfImprovementError, match="EVIDENCE_INVALID"):
        service.import_evidence(entry["entry_id"], "reports/self-improvement.json", expected_version=2)


def test_export_preview_requires_explicit_consent_for_local_file(tmp_path):
    root, _, service = setup_service(tmp_path)
    service.capture(kind="error", area="cli", summary="preview", request_id="export-preview")
    preview = service.export_preview()
    assert preview["consent_required"] is True
    assert preview["entries"] == 1
    with pytest.raises(SelfImprovementError, match="EXPORT_CONSENT_REQUIRED"):
        service.export_to_file("reports/self-improvement.json")
    saved = service.export_to_file("reports/self-improvement.json", consent=True)
    assert saved["consent"] is True
    assert (root / "reports/self-improvement.json").is_file()
    assert service.export_report()["report_sha256"] == saved["report_sha256"]


def test_export_report_is_redacted_and_excludes_private_context(tmp_path):
    _, _, service = setup_service(tmp_path)
    service.capture(kind="error", area="cli", summary="token=ghp_abcdefghijklmnopqrstuvwxyz123456", request_id="export")
    report = service.export_report()
    assert report["report_type"] == "self_improvement"
    assert report["provenance"]["contains_student_data"] is False
    assert report["provenance"]["contains_transcript"] is False
    assert report["provenance"]["network_transferred"] is False
    assert report["entries"][0]["evidence_count"] == 0
    assert "ghp_" not in str(report)
    assert report["report_sha256"]


def test_audit_reports_corrupt_record_without_mutating_it(tmp_path):
    _, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="проверка повреждения", request_id="audit")
    path = service.entries_dir / f"{entry['entry_id']}.json"
    corrupt = '{"schema_version":1,"entry_id":"broken"}'
    path.write_text(corrupt, encoding="utf-8")
    result = service.audit()
    assert result["ok"] is False
    assert result["checked"] == 1
    assert result["valid"] == 0
    assert result["issues"][0]["file"] == path.name
    assert path.read_text(encoding="utf-8") == corrupt
    with pytest.raises(SelfImprovementError, match="MIGRATION_INVALID"):
        service.migrate_to_store()


def test_migrate_to_workspace_store_is_idempotent_and_preserves_json_source(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="мигрируемая запись", request_id="migrate")
    source = (service.entries_dir / f"{entry['entry_id']}.json").read_bytes()
    first = service.migrate_to_store()
    assert first["entries"] == 1
    assert first["imported"] == 1
    assert first["source_preserved"] is True
    assert first["status"] == "completed"
    with Store.open(root, "self-improvement") as store:
        assert store.count("entities") == 1
        assert store.count("events") == 1
        assert store.migration(1)["source_hash"] == first["source_hash"]
    assert (service.entries_dir / f"{entry['entry_id']}.json").read_bytes() == source
    second = service.migrate_to_store()
    assert second == first


def test_migration_batch_rolls_back_entities_when_marker_update_fails(tmp_path, monkeypatch):
    root, _, service = setup_service(tmp_path)
    service.capture(kind="error", area="cli", summary="атомарная миграция", request_id="migrate-atomic")
    original = Store.import_self_improvement_entries

    def fail_after_batch(self, entries, *, source_hash, recovery=False):
        raise __import__("botai_core.store", fromlist=["StoreError"]).StoreError("MIGRATION_WRITE_FAILED", "forced")

    monkeypatch.setattr(Store, "import_self_improvement_entries", fail_after_batch)
    with pytest.raises(SelfImprovementError, match="MIGRATION_CONFLICT"):
        service.migrate_to_store()
    with Store.open(root, "self-improvement") as store:
        assert store.count("entities") == 0
        assert store.count("events") == 0
        assert store.migration(1) is None
    monkeypatch.setattr(Store, "import_self_improvement_entries", original)
    assert service.migrate_to_store()["status"] == "completed"


def test_migration_recovery_completes_pending_marker_after_interruption(tmp_path):
    root, _, service = setup_service(tmp_path)
    service.capture(kind="error", area="cli", summary="ожидающая миграция", request_id="migrate-pending")
    source_hash = service._source_manifest_hash()
    with Store.open(root, "self-improvement") as store:
        store.begin_migration(1, source_hash=source_hash)
    result = service.migrate_to_store(recovery=True)
    assert result["status"] == "completed"
    assert result["entries"] == 1
    with Store.open(root, "self-improvement") as store:
        assert store.migration(1)["report"]["status"] == "completed"


def test_migration_recovery_accepts_partial_import_and_rejects_conflicting_entity(tmp_path):
    root, _, service = setup_service(tmp_path)
    first = service.capture(kind="error", area="cli", summary="первая", request_id="migrate-recovery-1")
    second = service.capture(kind="error", area="docs", summary="вторая", request_id="migrate-recovery-2")
    source = service._source_manifest_hash()
    with Store.open(root, "self-improvement") as store:
        stored = dict(first)
        stored["summary"] = "чужая версия"
        store.apply(kind="self_improvement_entry", entity_id=first["entry_id"],
                    events=["self_improvement.entry_imported"], new_body=stored,
                    event_payloads=[{"entry_id": first["entry_id"], "source_hash": source}],
                    request_id="self-improvement-migrate-v1-" + first["entry_id"])
    with pytest.raises(SelfImprovementError, match="MIGRATION_CONFLICT"):
        service.migrate_to_store(recovery=True)
    with Store.open(root, "self-improvement") as store:
        assert store.get("self_improvement_entry", second["entry_id"])[0] is None


def test_migration_refuses_changed_json_source(tmp_path):
    root, _, service = setup_service(tmp_path)
    entry = service.capture(kind="error", area="cli", summary="первая версия", request_id="migrate-conflict")
    service.migrate_to_store()
    path = service.entries_dir / f"{entry['entry_id']}.json"
    value = __import__("json").loads(path.read_text(encoding="utf-8"))
    value["summary"] = "изменено после миграции"
    path.write_text(__import__("json").dumps(value), encoding="utf-8")
    with pytest.raises(SelfImprovementError, match="MIGRATION_CONFLICT"):
        service.migrate_to_store()


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
