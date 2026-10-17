"""Local, human-reviewed memory and documentation proposals for botai.

This module does not call a model, shell, network, or access learner/course data.
Only existing Markdown files under docs/self-improvement-guidance/ can be edited.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import paths
from .store import Store, StoreError

SCHEMA_VERSION = 1
ENTRY_KINDS = {"correction", "error", "feature_request", "best_practice", "knowledge_gap"}
ENTRY_STATUSES = {"captured", "verified", "unresolved", "contradicted", "retired"}
VALIDATION_STATES = {"pending", "verified", "unresolved", "contradicted"}
GUIDANCE_DIR = "docs/self-improvement-guidance"
ENTRY_ID_RE = re.compile(r"^[A-Z]{3}-\d{8}-[A-Z0-9]{3}$")
RECORD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SelfImprovementError(ValueError):
    """A rejected operation with a safe, user-facing explanation."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(value: str, field: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise SelfImprovementError(f"{field}: требуется обычный текст")
    value = " ".join(value.split())
    if not value or len(value) > limit:
        raise SelfImprovementError(f"{field}: текст должен содержать 1–{limit} символов")
    return value


def _redact(text: str) -> str:
    patterns = [
        (r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\b\s*[=:]\s*\S+", r"\1=[REDACTED]"),
        (r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (r"\bgh[pousr]_[A-Za-z0-9]{16,}\b", "[REDACTED]"),
        (r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED]"),
        (r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b", "[REDACTED-JWT]"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".botai-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _atomic_bytes(path, data)


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelfImprovementError(f"Локальная запись повреждена или недоступна: {path.name}") from exc


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _proposal_fingerprint(proposal: dict) -> str:
    fields = ("schema_version", "proposal_id", "entry_ids", "path", "base_sha256", "candidate_sha256", "old_text", "new_text", "rationale", "risk", "diff")
    return _digest(_canonical_json({key: proposal[key] for key in fields}))


def _validate_entry(entry: Any, expected_id: str | None = None) -> dict:
    required = {"schema_version", "entry_id", "kind", "scope", "area", "summary", "claim", "source", "evidence", "recurrence_count", "status", "validation", "applicability", "maintenance", "related_entry_ids", "version", "created_at", "updated_at"}
    if not isinstance(entry, dict) or not required.issubset(entry) or entry.get("schema_version") != SCHEMA_VERSION:
        raise SelfImprovementError("Схема записи самоулучшения повреждена")
    if not ENTRY_ID_RE.fullmatch(str(entry["entry_id"])) or (expected_id and entry["entry_id"] != expected_id):
        raise SelfImprovementError("Идентификатор записи не соответствует файлу")
    if entry["kind"] not in ENTRY_KINDS or entry["status"] not in ENTRY_STATUSES or entry["validation"] not in VALIDATION_STATES:
        raise SelfImprovementError("Неизвестный тип или статус записи")
    if not isinstance(entry["scope"], dict) or entry["scope"].get("kind") != "workspace" or entry["scope"].get("key") != "botai":
        raise SelfImprovementError("Область записи недопустима")
    if not isinstance(entry["source"], dict) or not isinstance(entry["evidence"], list) or not isinstance(entry["applicability"], dict) or not isinstance(entry["maintenance"], dict) or not isinstance(entry["related_entry_ids"], list):
        raise SelfImprovementError("Вложенная структура записи повреждена")
    for key in ("area", "summary", "claim", "created_at", "updated_at"):
        if not isinstance(entry[key], str):
            raise SelfImprovementError(f"Поле записи {key} повреждено")
    if not isinstance(entry["version"], int) or entry["version"] < 1 or not isinstance(entry["recurrence_count"], int) or entry["recurrence_count"] < 1:
        raise SelfImprovementError("Счётчик/версия записи повреждены")
    for item in entry["evidence"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("type", "ref", "result", "checked_at")):
            raise SelfImprovementError("Доказательство записи повреждено")
    return entry


class SelfImprovement:
    """Workspace-bound local observation and documentation-proposal service."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.state_dir = paths.ensure_within(self.root, self.root / ".botai" / "self_improvement")
        self.entries_dir = paths.ensure_within(self.state_dir, self.state_dir / "entries")
        self.proposals_dir = paths.ensure_within(self.state_dir, self.state_dir / "proposals")
        self.approvals_dir = paths.ensure_within(self.state_dir, self.state_dir / "approvals")
        self.applications_dir = paths.ensure_within(self.state_dir, self.state_dir / "applications")
        self.disabled_path = paths.ensure_within(self.state_dir, self.state_dir / "disabled")
        for directory in (self.entries_dir, self.proposals_dir, self.approvals_dir, self.applications_dir):
            directory.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.root), threading.RLock())

    def _ensure_enabled(self, *, allow_disabled=False) -> None:
        if not allow_disabled and self.disabled_path.exists():
            raise SelfImprovementError("Самоулучшение отключено; выполните enable, чтобы продолжить")

    def disable(self) -> None:
        _atomic_bytes(self.disabled_path, b"disabled\n")

    def enable(self) -> None:
        try:
            self.disabled_path.unlink()
        except FileNotFoundError:
            pass

    def _record_path(self, directory: Path, record_id: str) -> Path:
        if not isinstance(record_id, str) or not RECORD_ID_RE.fullmatch(record_id):
            raise SelfImprovementError("Недопустимый идентификатор")
        return paths.ensure_within(directory, directory / f"{record_id}.json")

    def capture(self, *, kind: str, area: str, summary: str, source_type: str = "other", source_ref: str = "", revision: str = "", request_id: str = "") -> dict:
        self._ensure_enabled()
        if kind not in ENTRY_KINDS:
            raise SelfImprovementError("Неизвестный тип наблюдения")
        area = _clean(area, "area", 80)
        summary = _redact(_clean(summary, "summary"))
        source_type = _clean(source_type, "source_type", 40)
        source_ref = _redact(source_ref[:200])
        revision = revision[:80] or None
        request_id = _clean(request_id or secrets.token_hex(12), "request_id", 80)
        idem_path = self._record_path(self.entries_dir, "request-" + hashlib.sha256(request_id.encode()).hexdigest())
        with self._lock:
            existing = _load_json(idem_path)
            if existing:
                _validate_entry(existing)
                if (existing["kind"], existing["area"], existing["summary"], existing["source"]["type"], existing["source"]["ref"], existing["source"]["revision"]) != (kind, area, summary, source_type, source_ref, revision):
                    raise SelfImprovementError("IDEMPOTENCY_CONFLICT: request_id уже использован с другими данными")
                return existing
            prefix = {"correction": "LRN", "error": "ERR", "feature_request": "FEAT", "best_practice": "LRN", "knowledge_gap": "LRN"}[kind]
            while True:
                entry_id = f"{prefix}-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(2).upper()[:3]}"
                entry_path = self._record_path(self.entries_dir, entry_id)
                if not entry_path.exists():
                    break
            now = _now()
            entry = {
                "schema_version": SCHEMA_VERSION, "entry_id": entry_id, "kind": kind,
                "scope": {"kind": "workspace", "key": "botai"}, "area": area,
                "summary": summary, "claim": "unknown — needs triage",
                "source": {"type": source_type, "ref": source_ref, "revision": revision, "observed_at": now},
                "evidence": [], "pattern_key": None, "recurrence_count": 1,
                "status": "captured", "validation": "pending",
                "applicability": {"versions": [], "environment": "unknown", "limits": "unknown"},
                "maintenance": {"decay_type": "unknown", "rate": "unknown", "revalidate_on": [], "last_evidence_at": None},
                "related_entry_ids": [], "version": 1, "created_at": now, "updated_at": now,
            }
            _atomic_json(entry_path, entry)
            _atomic_json(idem_path, entry)
            return entry

    def audit(self) -> dict:
        """Validate local JSON state without changing it."""
        checked = 0
        valid = 0
        issues = []
        for file in sorted(self.entries_dir.glob("*.json")):
            if file.name.startswith("request-"):
                continue
            checked += 1
            try:
                _validate_entry(_load_json(file), file.stem)
            except SelfImprovementError as exc:
                issues.append({"file": file.name, "error": str(exc)})
            else:
                valid += 1
        return {"checked": checked, "valid": valid, "issues": issues, "ok": not issues}

    def entries(self) -> list[dict]:
        records = []
        for file in self.entries_dir.glob("*.json"):
            if file.name.startswith("request-"):
                continue
            entry = _validate_entry(_load_json(file), file.stem)
            records.append(entry)
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def get_entry(self, entry_id: str) -> dict:
        if not isinstance(entry_id, str) or not ENTRY_ID_RE.fullmatch(entry_id):
            raise SelfImprovementError("Недопустимый идентификатор записи")
        entry = _load_json(self._record_path(self.entries_dir, entry_id))
        if entry is None:
            raise SelfImprovementError("Запись не найдена")
        return _validate_entry(entry, entry_id)

    def search(self, query: str = "", area: str | None = None, limit: int = 10) -> list[dict]:
        self._ensure_enabled()
        query = query.casefold().strip()
        if limit < 1 or limit > 100:
            raise SelfImprovementError("limit должен быть от 1 до 100")
        results = []
        for entry in self.entries():
            if entry["status"] in {"retired", "contradicted"}:
                continue
            if area and entry["area"] != area:
                continue
            haystack = " ".join((entry["summary"], entry.get("claim", ""), entry["area"])).casefold()
            if query and query not in haystack:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def triage(self, *, area: str | None = None, limit: int = 50) -> dict:
        """Return entries needing human review without changing any state."""
        self._ensure_enabled()
        if limit < 1 or limit > 100:
            raise SelfImprovementError("limit должен быть от 1 до 100")
        pending = []
        for entry in self.entries():
            if area and entry["area"] != area:
                continue
            if entry["validation"] not in {"pending", "unresolved", "contradicted"}:
                continue
            action = "review"
            if entry["validation"] == "unresolved":
                action = "recheck_source"
            elif entry["validation"] == "contradicted":
                action = "retire_or_replace"
            pending.append({"entry_id": entry["entry_id"], "area": entry["area"],
                            "summary": entry["summary"], "validation": entry["validation"],
                            "version": entry["version"], "recommended_action": action})
            if len(pending) >= limit:
                break
        return {"count": len(pending), "items": pending}

    def review(self, entry_id: str, *, claim: str, evidence_type: str, evidence_ref: str, result: str, validation: str, scope: str = "workspace", expected_version: int) -> dict:
        self._ensure_enabled()
        if not isinstance(expected_version, int):
            raise SelfImprovementError("expected_version обязателен для проверки записи")
        if validation not in {"verified", "unresolved", "contradicted"}:
            raise SelfImprovementError("validation должен быть verified, unresolved или contradicted")
        claim = _redact(_clean(claim, "claim"))
        evidence_type = _clean(evidence_type, "evidence_type", 40)
        evidence_ref = _redact(_clean(evidence_ref, "evidence_ref", 200))
        result = _redact(_clean(result, "result", 500))
        if validation == "verified":
            if evidence_type == "file":
                if not evidence_ref or evidence_ref.startswith(("/", "\\")) or ".." in Path(evidence_ref.replace("\\", "/")).parts:
                    raise SelfImprovementError("Ссылка на файл должна быть относительной и внутри проекта")
                source = paths.ensure_within(self.root, self.root / evidence_ref)
                if not source.is_file() or source.is_symlink():
                    raise SelfImprovementError("Указанный файл-источник не существует или является ссылкой")
            elif evidence_type == "test":
                raise SelfImprovementError("Итог теста нельзя объявить verified вручную; приложите подтверждение maintainer")
            elif evidence_type not in {"command_code", "maintainer_decision"}:
                raise SelfImprovementError("Для verified требуется файл, исходный код команды или решение maintainer")
        evidence = {"type": evidence_type, "ref": evidence_ref, "result": result, "checked_at": _now()}
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry["version"] != expected_version:
                raise SelfImprovementError("STATE_CONFLICT: запись изменена; перечитайте её")
            entry["claim"] = claim
            entry["evidence"].append(evidence)
            entry["validation"] = validation
            entry["status"] = validation if validation != "verified" else "verified"
            entry["applicability"]["limits"] = _redact(_clean(scope, "scope", 300))
            entry["maintenance"]["last_evidence_at"] = evidence["checked_at"]
            entry["version"] += 1
            entry["updated_at"] = _now()
            _validate_entry(entry, entry_id)
            _atomic_json(self._record_path(self.entries_dir, entry_id), entry)
            return entry

    def import_evidence(self, entry_id: str, report_path: str, *, expected_version: int) -> dict:
        """Attach a locally generated report after validating its provenance and hash."""
        self._ensure_enabled()
        if not isinstance(expected_version, int):
            raise SelfImprovementError("expected_version обязателен для импорта evidence")
        if not isinstance(report_path, str) or not report_path or "\\" in report_path:
            raise SelfImprovementError("Путь отчёта должен быть каноническим POSIX-путём")
        candidate = PurePosixPath(report_path)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts) or candidate.as_posix() != report_path:
            raise SelfImprovementError("Путь отчёта должен быть каноническим относительным путём")
        path = paths.ensure_within(self.root, self.root / report_path)
        if not path.is_file() or path.is_symlink():
            raise SelfImprovementError("Файл отчёта не существует или является ссылкой")
        report = _load_json(path)
        if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("report_type") != "self_improvement":
            raise SelfImprovementError("EVIDENCE_INVALID: неизвестная схема отчёта")
        stored_hash = report.get("report_sha256")
        unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
        if not isinstance(stored_hash, str) or not secrets.compare_digest(stored_hash, _digest(_canonical_json(unsigned))):
            raise SelfImprovementError("EVIDENCE_INVALID: hash отчёта не совпадает")
        provenance = report.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("source") != "local_self_improvement" or provenance.get("contains_student_data") is not False or provenance.get("contains_transcript") is not False or provenance.get("network_transferred") is not False:
            raise SelfImprovementError("EVIDENCE_INVALID: provenance отчёта не подтверждает локальное происхождение")
        if not isinstance(report.get("entries"), list):
            raise SelfImprovementError("EVIDENCE_INVALID: отсутствует список записей")
        entry = self.get_entry(entry_id)
        if entry["version"] != expected_version:
            raise SelfImprovementError("STATE_CONFLICT: запись изменена; перечитайте её")
        matching = next((item for item in report["entries"] if isinstance(item, dict) and item.get("entry_id") == entry_id), None)
        if matching is None or matching.get("version") != entry["version"]:
            raise SelfImprovementError("EVIDENCE_CONFLICT: отчёт не содержит текущую версию записи")
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry["version"] != expected_version:
                raise SelfImprovementError("STATE_CONFLICT: запись изменена; перечитайте её")
            evidence = {"type": "report", "ref": report_path, "result": "локальный redacted report с проверенным hash/provenance", "checked_at": _now(), "report_sha256": stored_hash}
            entry["evidence"].append(evidence)
            entry["maintenance"]["last_evidence_at"] = evidence["checked_at"]
            entry["version"] += 1
            entry["updated_at"] = _now()
            _validate_entry(entry, entry_id)
            _atomic_json(self._record_path(self.entries_dir, entry_id), entry)
            return entry

    def retire(self, entry_id: str, *, reason: str, expected_version: int) -> dict:
        """Retire a record while preserving its evidence and complete history."""
        self._ensure_enabled()
        if not isinstance(expected_version, int):
            raise SelfImprovementError("expected_version обязателен для архивации записи")
        reason = _redact(_clean(reason, "reason", 500))
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry["version"] != expected_version:
                raise SelfImprovementError("STATE_CONFLICT: запись изменена; перечитайте её")
            if entry["status"] == "retired":
                raise SelfImprovementError("Запись уже архивирована")
            entry["status"] = "retired"
            entry["maintenance"]["retirement_reason"] = reason
            entry["maintenance"]["retired_at"] = _now()
            entry["version"] += 1
            entry["updated_at"] = _now()
            _validate_entry(entry, entry_id)
            _atomic_json(self._record_path(self.entries_dir, entry_id), entry)
            return entry

    def _target(self, relative: str) -> tuple[Path, str]:
        if not isinstance(relative, str) or not relative or "\\" in relative or DRIVE_RE.match(relative) or relative.startswith("//") or "\x00" in relative:
            raise SelfImprovementError("Недопустимый путь предложения")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts) or pure.as_posix() != relative:
            raise SelfImprovementError("Путь должен быть каноническим относительным POSIX-путём")
        unresolved = self.root.joinpath(*pure.parts)
        probe = self.root
        for part in pure.parts:
            probe = probe / part
            if probe.is_symlink():
                raise SelfImprovementError("Symlink в пути предложения запрещён")
        path = paths.ensure_within(self.root, unresolved)
        rel = path.relative_to(self.root).as_posix()
        if not rel.startswith(GUIDANCE_DIR + "/") or path.suffix.lower() != ".md":
            raise SelfImprovementError("В MVP разрешены только Markdown-файлы docs/self-improvement-guidance/")
        if path.is_symlink() or not path.is_file():
            raise SelfImprovementError("Целевой файл должен существовать и не быть symlink")
        return path, rel

    def _proposal_payload(self, proposal: dict) -> dict:
        required = {"schema_version", "proposal_id", "entry_ids", "path", "base_sha256", "candidate_sha256", "old_text", "new_text", "rationale", "risk", "status", "verification", "diff", "fingerprint", "created_at", "updated_at"}
        if not isinstance(proposal, dict) or not required.issubset(proposal) or proposal["schema_version"] != 1:
            raise SelfImprovementError("Схема предложения повреждена")
        if not re.fullmatch(r"[0-9a-f]{16}", str(proposal["proposal_id"])):
            raise SelfImprovementError("Идентификатор предложения повреждён")
        payload = {key: proposal[key] for key in ("schema_version", "proposal_id", "entry_ids", "path", "base_sha256", "candidate_sha256", "old_text", "new_text", "rationale", "risk", "diff")}
        fingerprint = _digest(_canonical_json(payload))
        if not secrets.compare_digest(fingerprint, str(proposal["fingerprint"])):
            raise SelfImprovementError("FINGERPRINT_MISMATCH: предложение изменилось после создания")
        return payload

    def propose(self, *, entry_id: str, relative_path: str, old_text: str, new_text: str, rationale: str) -> dict:
        self._ensure_enabled()
        entry = self.get_entry(entry_id)
        if entry["validation"] != "verified" or entry["status"] != "verified":
            raise SelfImprovementError("Для предложения требуется проверенная запись")
        target, rel = self._target(relative_path)
        base = target.read_bytes()
        try:
            current = base.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SelfImprovementError("Файл guidance должен быть UTF-8") from exc
        old_text = _clean(old_text, "old_text", 100_000)
        if current.count(old_text) != 1:
            raise SelfImprovementError("Исходный фрагмент должен встречаться в файле ровно один раз")
        candidate = current.replace(old_text, new_text, 1)
        candidate_bytes = candidate.encode("utf-8")
        if candidate_bytes == base or len(candidate_bytes) > 256_000:
            raise SelfImprovementError("Пустое изменение или превышен лимит файла")
        proposal_id = secrets.token_hex(8)
        proposal = {"schema_version": 1, "proposal_id": proposal_id, "entry_ids": [entry_id],
                    "path": rel, "base_sha256": _digest(base), "candidate_sha256": _digest(candidate_bytes),
                    "old_text": old_text, "new_text": new_text,
                    "rationale": _redact(_clean(rationale, "rationale")), "risk": "low",
                    "status": "draft", "verification": None, "created_at": _now(), "updated_at": _now()}
        proposal["diff"] = "".join(difflib.unified_diff(current.splitlines(True), candidate.splitlines(True), fromfile="a/" + rel, tofile="b/" + rel))
        proposal["fingerprint"] = _digest(_canonical_json({key: proposal[key] for key in ("schema_version", "proposal_id", "entry_ids", "path", "base_sha256", "candidate_sha256", "old_text", "new_text", "rationale", "risk", "diff")}))
        _atomic_json(self._record_path(self.proposals_dir, proposal_id), proposal)
        return proposal

    def get_proposal(self, proposal_id: str) -> dict:
        proposal = _load_json(self._record_path(self.proposals_dir, proposal_id))
        if proposal is None:
            raise SelfImprovementError("Предложение не найдено")
        self._proposal_payload(proposal)
        return proposal

    def preview(self, proposal_id: str) -> dict:
        self._ensure_enabled()
        proposal = self.get_proposal(proposal_id)
        target, normalized = self._target(proposal["path"])
        if normalized != proposal["path"]:
            raise SelfImprovementError("Путь proposal изменился")
        current = target.read_bytes()
        if _digest(current) != proposal["base_sha256"]:
            raise SelfImprovementError("STALE_PROPOSAL: исходный файл изменился; создайте предложение заново")
        return proposal

    def verify(self, proposal_id: str) -> dict:
        proposal = self.preview(proposal_id)
        target, _ = self._target(proposal["path"])
        source_bytes = target.read_bytes()
        if _digest(source_bytes) != proposal["base_sha256"]:
            raise SelfImprovementError("STALE_PROPOSAL: исходный файл изменился")
        try:
            source = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SelfImprovementError("Файл guidance должен быть UTF-8") from exc
        if source.count(proposal["old_text"]) != 1:
            raise SelfImprovementError("Исходный фрагмент должен встречаться ровно один раз")
        candidate = source.replace(proposal["old_text"], proposal["new_text"], 1)
        candidate_bytes = candidate.encode("utf-8")
        if _digest(candidate_bytes) != proposal["candidate_sha256"]:
            raise SelfImprovementError("Кандидат не совпадает с сохранённым hash")
        if len(candidate_bytes) > 256_000 or "\x00" in candidate:
            raise SelfImprovementError("Кандидат превышает разрешённый размер или содержит NUL")
        # This is structural validation, not a semantic proof. Human approval is
        # still mandatory and the mutation scope excludes active policy/skills.
        report = {"result": "structural_checks_passed", "checks": ["canonical allowlisted path", "base hash", "UTF-8", "unique snippet", "candidate hash", "size limit"], "commands": [], "verified_at": _now(), "limitations": "структурные проверки не доказывают истинность или безопасность смысла; требуется просмотр diff человеком"}
        proposal["verification"] = report
        proposal["status"] = "checked"
        proposal["updated_at"] = _now()
        _atomic_json(self._record_path(self.proposals_dir, proposal_id), proposal)
        return report

    def approve(self, proposal_id: str) -> tuple[str, str]:
        proposal = self.preview(proposal_id)
        payload = self._proposal_payload(proposal)
        if proposal["status"] != "checked" or not proposal.get("verification"):
            raise SelfImprovementError("Сначала выполните структурную проверку")
        token = secrets.token_urlsafe(24)
        approval_id = secrets.token_hex(8)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        approval = {"approval_id": approval_id, "proposal_id": proposal_id, "fingerprint": _digest(_canonical_json(payload)),
                    "token_sha256": _digest(token.encode()), "expires_at": expires, "used": False}
        _atomic_json(self._record_path(self.approvals_dir, approval_id), approval)
        return approval_id, token

    def _atomic_replace_target(self, target: Path, original: bytes, replacement: bytes) -> None:
        # Refuse a changed target immediately before replacement. A same-UID
        # hostile process remains outside the guarantee; the workspace owner is
        # the trust boundary. On POSIX, use the opened parent directory as a
        # stable rename anchor instead of resolving it again after validation.
        if target.is_symlink() or not target.is_file() or target.read_bytes() != original:
            raise SelfImprovementError("STALE_PROPOSAL: цель изменилась непосредственно перед записью")
        fd, temporary = tempfile.mkstemp(prefix=".botai-self-improve-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
            if target.is_symlink() or target.read_bytes() != original:
                raise SelfImprovementError("STALE_PROPOSAL: цель изменилась до атомарной замены")
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def apply(self, proposal_id: str, approval_id: str, challenge: str) -> dict:
        self._ensure_enabled()
        with self._lock:
            approval_path = self._record_path(self.approvals_dir, approval_id)
            approval = _load_json(approval_path)
            if not isinstance(approval, dict) or approval.get("used") or approval.get("proposal_id") != proposal_id:
                raise SelfImprovementError("APPROVAL_INVALID: нет действующего подтверждения")
            proposal = self.preview(proposal_id)
            payload = self._proposal_payload(proposal)
            fingerprint = _digest(_canonical_json(payload))
            if datetime.now(timezone.utc) > datetime.fromisoformat(approval["expires_at"]):
                raise SelfImprovementError("APPROVAL_EXPIRED: получите новое подтверждение")
            if not isinstance(challenge, str) or not secrets.compare_digest(approval["token_sha256"], _digest(challenge.encode())):
                raise SelfImprovementError("APPROVAL_INVALID: проверочный код не совпадает")
            if proposal["status"] != "checked" or approval.get("fingerprint") != fingerprint or not proposal.get("verification"):
                raise SelfImprovementError("APPROVAL_INVALID: proposal не проверен или изменён")
            target, _ = self._target(proposal["path"])
            current_bytes = target.read_bytes()
            if _digest(current_bytes) != proposal["base_sha256"]:
                raise SelfImprovementError("STALE_PROPOSAL: исходный файл изменился")
            current = current_bytes.decode("utf-8")
            if current.count(proposal["old_text"]) != 1:
                raise SelfImprovementError("STALE_PROPOSAL: исходный фрагмент изменился")
            candidate_bytes = current.replace(proposal["old_text"], proposal["new_text"], 1).encode("utf-8")
            if _digest(candidate_bytes) != proposal["candidate_sha256"]:
                raise SelfImprovementError("STALE_PROPOSAL: содержимое кандидата изменилось")
            application_id = secrets.token_hex(8)
            application = {"application_id": application_id, "proposal_id": proposal_id, "path": proposal["path"],
                           "old_sha256": proposal["base_sha256"], "new_sha256": proposal["candidate_sha256"],
                           "old_bytes_b64": base64.b64encode(current_bytes).decode("ascii"), "approval_id": approval_id,
                           "status": "prepared", "prepared_at": _now(), "reverted_at": None}
            application_path = self._record_path(self.applications_dir, application_id)
            _atomic_json(application_path, application)
            approval["used"] = True
            _atomic_json(approval_path, approval)
            try:
                self._atomic_replace_target(target, current_bytes, candidate_bytes)
            except BaseException:
                application["status"] = "failed"
                _atomic_json(application_path, application)
                raise
            application["status"] = "applied"
            application["applied_at"] = _now()
            _atomic_json(application_path, application)
            proposal["status"] = "applied"
            proposal["updated_at"] = _now()
            _atomic_json(self._record_path(self.proposals_dir, proposal_id), proposal)
            return application

    def recover(self) -> list[dict]:
        """Reconcile interrupted application metadata without changing workspace files.

        A prepared application is considered applied only when the target hash is the
        recorded candidate hash. If the target is still the recorded old hash, the
        operation is marked failed. Any other content is left as a conflict for a
        human to inspect; recovery never overwrites a target file.
        """
        self._ensure_enabled(allow_disabled=True)
        recovered = []
        with self._lock:
            for application_path in sorted(self.applications_dir.glob("*.json")):
                application = _load_json(application_path)
                if not isinstance(application, dict) or application.get("status") not in {"prepared", "reverting"}:
                    continue
                application_id = application.get("application_id")
                proposal_id = application.get("proposal_id")
                path = application.get("path")
                if not all(isinstance(value, str) for value in (application_id, proposal_id, path)):
                    raise SelfImprovementError("Схема применения повреждена")
                target, _ = self._target(path)
                current_hash = _digest(target.read_bytes())
                if application["status"] == "prepared":
                    if current_hash == application.get("new_sha256"):
                        application["status"] = "applied"
                        application["applied_at"] = _now()
                        outcome = "applied"
                    elif current_hash == application.get("old_sha256"):
                        application["status"] = "failed"
                        application["failure"] = "interrupted_before_target_replace"
                        outcome = "failed"
                    else:
                        application["status"] = "conflict"
                        application["failure"] = "target_hash_matches_neither_recorded_state"
                        outcome = "conflict"
                else:
                    if current_hash == application.get("old_sha256"):
                        application["status"] = "reverted"
                        application["reverted_at"] = _now()
                        outcome = "reverted"
                    elif current_hash == application.get("new_sha256"):
                        application["status"] = "applied"
                        application.pop("reverted_at", None)
                        outcome = "applied"
                    else:
                        application["status"] = "conflict"
                        application["failure"] = "target_hash_matches_neither_recorded_state"
                        outcome = "conflict"
                _atomic_json(application_path, application)
                if outcome in {"applied", "reverted"}:
                    proposal = self.get_proposal(proposal_id)
                    proposal["status"] = outcome
                    proposal["updated_at"] = _now()
                    _atomic_json(self._record_path(self.proposals_dir, proposal_id), proposal)
                recovered.append({"application_id": application_id, "status": outcome})
        return recovered

    def revert(self, application_id: str) -> dict:
        self._ensure_enabled()
        with self._lock:
            application_path = self._record_path(self.applications_dir, application_id)
            application = _load_json(application_path)
            if not isinstance(application, dict):
                raise SelfImprovementError("Применение не найдено")
            if application.get("status") != "applied" or application.get("reverted_at"):
                raise SelfImprovementError("REVERT_INVALID: применение уже отменено или не завершено")
            if application.get("failure"):
                raise SelfImprovementError("REVERT_INVALID: применение требует восстановления или конфликта")

            target, _ = self._target(application["path"])
            current = target.read_bytes()
            if _digest(current) != application["new_sha256"]:
                raise SelfImprovementError("REVERT_CONFLICT: файл изменился после применения; не перезаписываю новые правки")
            try:
                old_bytes = base64.b64decode(application["old_bytes_b64"], validate=True)
            except (ValueError, KeyError) as exc:
                raise SelfImprovementError("Журнал отката повреждён") from exc
            if _digest(old_bytes) != application["old_sha256"]:
                raise SelfImprovementError("Журнал отката не прошёл проверку hash")
            application["status"] = "reverting"
            _atomic_json(application_path, application)
            try:
                self._atomic_replace_target(target, current, old_bytes)
            except BaseException:
                application["status"] = "applied"
                _atomic_json(application_path, application)
                raise
            application["status"] = "reverted"
            application["reverted_at"] = _now()
            _atomic_json(application_path, application)
            proposal = self.get_proposal(application["proposal_id"])
            proposal["status"] = "reverted"
            proposal["updated_at"] = _now()
            _atomic_json(self._record_path(self.proposals_dir, proposal["proposal_id"]), proposal)
            return application

    def disable(self) -> None:
        _atomic_bytes(self.disabled_path, b"disabled\n")

    def enable(self) -> None:
        try:
            self.disabled_path.unlink()
        except FileNotFoundError:
            pass

    def _source_manifest_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.entries_dir.glob("*.json")):
            if path.name.startswith("request-"):
                continue
            relative = path.relative_to(self.root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            data = path.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    def purge_preview(self, *, include_backups: bool = False) -> dict:
        targets = []
        for directory in (self.entries_dir, self.proposals_dir, self.approvals_dir, self.applications_dir):
            targets.extend(path for path in directory.glob("*.json") if path.is_file())
        if include_backups:
            targets.extend(path for path in self.state_dir.glob("*.backup-*") if path.is_file())
        return {"files": len(targets), "bytes": sum(path.stat().st_size for path in targets),
                "include_backups": include_backups, "consent_required": True,
                "tombstone_only": True}

    def purge(self, *, include_backups: bool = False, consent: bool = False) -> dict:
        if not consent:
            raise SelfImprovementError("PURGE_CONSENT_REQUIRED: сначала покажите preview и подтвердите удаление")
        preview = self.purge_preview(include_backups=include_backups)
        tombstone = {"schema_version": 1, "type": "self_improvement_purge", "created_at": _now(),
                     "files": preview["files"], "bytes": preview["bytes"],
                     "include_backups": include_backups}
        tombstone["tombstone_sha256"] = _digest(_canonical_json(tombstone))
        _atomic_json(self._record_path(self.state_dir, "purge-" + secrets.token_hex(8)), tombstone)
        removed = 0
        for directory in (self.entries_dir, self.proposals_dir, self.approvals_dir, self.applications_dir):
            for path in directory.glob("*.json"):
                path.unlink()
                removed += 1
        if include_backups:
            for path in self.state_dir.glob("*.backup-*"):
                if path.is_file():
                    path.unlink()
                    removed += 1
        return {"removed": removed, "tombstone_sha256": tombstone["tombstone_sha256"], "consent": True}

    def backup_preview(self) -> dict:
        files = []
        for directory in (self.entries_dir, self.proposals_dir, self.approvals_dir, self.applications_dir):
            for path in sorted(directory.glob("*.json")):
                data = path.read_bytes()
                files.append({"path": path.relative_to(self.root).as_posix(), "sha256": _digest(data), "byte_size": len(data)})
        manifest = {"schema_version": 1, "backup_type": "self_improvement_state", "files": files}
        return {"manifest_sha256": _digest(_canonical_json(manifest)), "files": len(files),
                "byte_size": sum(item["byte_size"] for item in files), "consent_required": True,
                "destination": "explicit local directory required"}

    def backup_to(self, destination: str, *, consent: bool = False) -> dict:
        if not consent:
            raise SelfImprovementError("BACKUP_CONSENT_REQUIRED: сначала покажите preview и подтвердите путь")
        destination_path = Path(destination).expanduser()
        if not destination_path.is_absolute():
            destination_path = self.root / destination_path
        destination_path = paths.ensure_within(self.root, destination_path)
        if destination_path.exists() and not destination_path.is_dir():
            raise SelfImprovementError("Путь backup должен быть каталогом")
        destination_path.mkdir(parents=True, exist_ok=True)
        files = []
        for directory in (self.entries_dir, self.proposals_dir, self.approvals_dir, self.applications_dir):
            for path in sorted(directory.glob("*.json")):
                relative = path.relative_to(self.state_dir)
                target = paths.ensure_within(destination_path, destination_path / relative)
                data = path.read_bytes()
                _atomic_bytes(target, data)
                files.append({"path": relative.as_posix(), "sha256": _digest(data), "byte_size": len(data)})
        manifest = {"schema_version": 1, "backup_type": "self_improvement_state", "files": files}
        _atomic_json(destination_path / "manifest.json", {**manifest, "manifest_sha256": _digest(_canonical_json(manifest))})
        return {"path": destination_path.relative_to(self.root).as_posix(), "manifest_sha256": _digest(_canonical_json(manifest)), "files": len(files), "consent": True}

    def backup_restore_preview(self, source: str) -> dict:
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = self.root / source_path
        source_path = paths.ensure_within(self.root, source_path)
        manifest = _load_json(paths.ensure_within(source_path, source_path / "manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("backup_type") != "self_improvement_state":
            raise SelfImprovementError("BACKUP_INVALID: неизвестный manifest")
        unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if manifest.get("manifest_sha256") != _digest(_canonical_json(unsigned)):
            raise SelfImprovementError("BACKUP_INVALID: manifest hash не совпадает")
        for item in manifest["files"]:
            path = paths.ensure_within(source_path, source_path / item["path"])
            if not path.is_file() or _digest(path.read_bytes()) != item["sha256"]:
                raise SelfImprovementError("BACKUP_INVALID: файл backup повреждён")
        return {"source": source_path.relative_to(self.root).as_posix(), "files": len(manifest["files"]), "manifest_sha256": manifest["manifest_sha256"], "consent_required": True}

    def backup_restore(self, source: str, *, consent: bool = False) -> dict:
        if not consent:
            raise SelfImprovementError("RESTORE_CONSENT_REQUIRED: сначала покажите preview и подтвердите путь")
        preview = self.backup_restore_preview(source)
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = self.root / source_path
        source_path = paths.ensure_within(self.root, source_path)
        manifest = _load_json(source_path / "manifest.json")
        for item in manifest["files"]:
            source_file = paths.ensure_within(source_path, source_path / item["path"])
            target = paths.ensure_within(self.state_dir, self.state_dir / item["path"])
            _atomic_bytes(target, source_file.read_bytes())
        return {"source": preview["source"], "files": preview["files"], "manifest_sha256": preview["manifest_sha256"], "consent": True}

    def export_preview(self) -> dict:
        report = self.export_report()
        encoded = _canonical_json(report)
        return {"report_sha256": report["report_sha256"], "byte_size": len(encoded),
                "entries": len(report["entries"]), "provenance": report["provenance"],
                "destination": "explicit local path required", "consent_required": True}

    def export_report(self) -> dict:
        """Build a redacted, local-only report without learner or course data."""
        self._ensure_enabled(allow_disabled=True)
        audit = self.audit()
        if not audit["ok"]:
            raise SelfImprovementError("EXPORT_INVALID: исправьте повреждённые записи перед экспортом")
        entries = []
        for entry in self.entries():
            entries.append({
                "entry_id": entry["entry_id"], "kind": entry["kind"], "area": entry["area"],
                "summary": _redact(entry["summary"]), "claim": _redact(entry["claim"]),
                "validation": entry["validation"], "status": entry["status"],
                "recurrence_count": entry["recurrence_count"], "version": entry["version"],
                "evidence_count": len(entry["evidence"]), "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
            })
        payload = {"schema_version": 1, "report_type": "self_improvement",
                   "provenance": {"source": "local_self_improvement", "workspace_scoped": True,
                                  "contains_student_data": False, "contains_transcript": False,
                                  "network_transferred": False},
                   "entries": entries}
        payload["report_sha256"] = _digest(_canonical_json(payload))
        return payload

    def export_to_file(self, destination: str, *, consent: bool = False) -> dict:
        if not consent:
            raise SelfImprovementError("EXPORT_CONSENT_REQUIRED: сначала покажите preview и подтвердите путь")
        destination_path = Path(destination).expanduser()
        if not destination_path.is_absolute():
            destination_path = self.root / destination_path
        destination_path = paths.ensure_within(self.root, destination_path)
        if destination_path.exists() and destination_path.is_dir():
            raise SelfImprovementError("Путь экспорта должен быть файлом")
        report = self.export_report()
        _atomic_json(destination_path, report)
        return {"path": destination_path.relative_to(self.root).as_posix(),
                "report_sha256": report["report_sha256"], "byte_size": len(_canonical_json(report)),
                "consent": True}

    def migrate_to_store(self, *, learner_id="self-improvement", recovery=False) -> dict:
        """Copy validated JSON entries to the authoritative workspace SQLite store.

        The JSON records remain untouched. A deterministic request id makes a retry
        idempotent, while a source manifest prevents recording a migration after a
        concurrent source change.
        """
        self._ensure_enabled(allow_disabled=True)
        if not isinstance(learner_id, str) or not learner_id.strip() or len(learner_id) > 80:
            raise SelfImprovementError("Недопустимый learner_id для миграции")
        audit = self.audit()
        if not audit["ok"]:
            raise SelfImprovementError("MIGRATION_INVALID: исправьте повреждённые записи перед миграцией")
        entries = self.entries()
        source_hash = self._source_manifest_hash()
        with Store.open(self.root, learner_id) as store:
            if recovery:
                for entry in entries:
                    stored, version = store.get("self_improvement_entry", entry["entry_id"])
                    if stored is not None and stored != entry:
                        raise SelfImprovementError("MIGRATION_CONFLICT: импортированная запись отличается от JSON")
            try:
                report = store.import_self_improvement_entries(entries, source_hash=source_hash, recovery=recovery)
            except StoreError as exc:
                raise SelfImprovementError(f"MIGRATION_CONFLICT: {exc}") from exc
            if self._source_manifest_hash() != source_hash:
                raise SelfImprovementError("MIGRATION_CONFLICT: JSON-источник изменился во время миграции")
            return report

    def status(self) -> dict:
        entries = self.entries()
        proposals = [_load_json(p, {}) for p in self.proposals_dir.glob("*.json")]
        return {"enabled": not self.disabled_path.exists(), "storage": ".botai/self_improvement/",
                "entries": len(entries), "entries_by_status": {status: sum(1 for entry in entries if entry["status"] == status) for status in sorted(ENTRY_STATUSES)},
                "proposals": len(proposals), "network": False}
