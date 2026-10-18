# -*- coding: utf-8 -*-
"""The durable record: SQLite with transactions, evidence, and idempotency.

Why a database and not the Markdown file the v1 harness wrote: the record is
what lets the next session avoid re-teaching, and a Markdown file cannot reject a
write. Three properties are needed for the rules the design states, and all
three are properties of storage, not of a prompt:

* **A state change names its evidence.** `demonstrated` is written together with
  the attempt/check ids that justify it, in one transaction. A model cannot
  assert mastery by describing it.
* **A retried command is not a second effect.** Every write carries a
  `request_id`; a replay returns the stored result, and the same id with a
  different payload is a conflict rather than a silent overwrite.
* **A stale write is refused.** Callers pass the version they read; a mismatch
  becomes `STATE_CONFLICT` instead of losing a concurrent update.

The Markdown file remains as a human-readable projection written *after* the
commit. If projection fails, the event is still authoritative and the next
command repairs the file — losing a rendering must never lose the record.

Schema and event payloads are validated by `botai_core.schemas` before storage.
Foreign keys are enforced; WAL is used with a busy timeout, and the store refuses
to run on a filesystem where locking is unreliable (a network share), because
silent corruption of a study record is worse than a refused start.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths, schemas

SCHEMA_VERSION = 2
STORE_FILENAME = "state.sqlite3"

# Bumped only when the on-disk layout changes; `meta.schema_version` records it.
LAYOUT_VERSION = 1

WORKSPACE_COURSE_ID = "_workspace"


class StoreError(RuntimeError):
    """A refused operation, with a stable code for callers and tests."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class StateConflict(StoreError):
    def __init__(self, message, expected=None, actual=None):
        super().__init__("STATE_CONFLICT", message)
        self.expected = expected
        self.actual = actual


class IdempotencyConflict(StoreError):
    def __init__(self, message):
        super().__init__("IDEMPOTENCY_CONFLICT", message)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id():
    return str(uuid.uuid4())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def command_hash(command):
    """Stable digest of a command, ignoring transport retry metadata."""
    payload = {k: v for k, v in command.items() if k not in ("request_id", "transport")}
    return _json(payload)


DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    learner_id  TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0,
    body_json   TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (learner_id, course_id, kind, entity_id)
);

CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    request_id   TEXT NOT NULL,
    learner_id   TEXT NOT NULL,
    course_id    TEXT NOT NULL,
    entity_kind  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_request ON events(request_id, seq);
CREATE INDEX IF NOT EXISTS events_scope   ON events(learner_id, course_id, seq);

CREATE TABLE IF NOT EXISTS requests (
    request_id   TEXT PRIMARY KEY,
    command_hash TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    sha256          TEXT PRIMARY KEY,
    relative_path   TEXT NOT NULL,
    media_type      TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    retention_until TEXT,
    provenance      TEXT NOT NULL
);

-- Artifact bytes are deduplicated by content hash, so ownership is tracked
-- separately: deleting one learner's data must not remove bytes another
-- learner's evidence still points at.
CREATE TABLE IF NOT EXISTS artifact_refs (
    learner_id  TEXT NOT NULL,
    course_id   TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (learner_id, course_id, entity_kind, entity_id, sha256),
    FOREIGN KEY (sha256) REFERENCES artifacts(sha256) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    source_hash TEXT,
    report_json TEXT
);
"""


class Store:
    """A workspace store, bound to one root and one learner scope."""

    def __init__(self, connection, root, learner_id, *, owns_connection=False):
        self._conn = connection
        self.root = Path(root)
        self.learner_id = learner_id
        self._owns = owns_connection

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def open(cls, root, learner_id, *, create=True):
        """Open (or create) the store for `root`/`learner_id`."""
        root = Path(root).expanduser().resolve()
        state_dir = paths.ensure_within(root, root / ".botai")
        state_dir.mkdir(parents=True, exist_ok=True)
        db_path = state_dir / STORE_FILENAME

        if not db_path.exists() and not create:
            raise StoreError("STORE_MISSING", "запись состояния не найдена: %s" % db_path)

        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
        except sqlite3.Error as e:
            raise StoreError("STORE_UNOPENABLE", "не удалось открыть %s: %s" % (db_path, e))

        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(journal).lower() != "wal":
                # A network share silently refuses WAL; concurrent writers there
                # corrupt a record rather than failing loudly.
                conn.execute("PRAGMA journal_mode = WAL")
                journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(journal).lower() != "wal":
                conn.close()
                raise StoreError(
                    "STORE_FILESYSTEM_UNSUPPORTED",
                    "файловая система не поддерживает надёжную блокировку (journal_mode=%s). "
                    "Разместите рабочее пространство на локальном диске." % journal,
                )
            conn.executescript(DDL)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(LAYOUT_VERSION),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('learner_id', ?)", (learner_id,)
            )
            recorded = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if recorded and int(recorded[0]) > LAYOUT_VERSION:
                conn.close()
                raise StoreError(
                    "STORE_VERSION_NEWER",
                    "запись состояния создана более новой версией (%s > %s): "
                    "обновите обвязку, не понижайте версию поверх неё"
                    % (recorded[0], LAYOUT_VERSION),
                )
        except StoreError:
            raise
        except sqlite3.Error as e:
            conn.close()
            raise StoreError("STORE_BROKEN", "повреждена запись состояния: %s" % e)

        return cls(conn, root, learner_id, owns_connection=True)

    def close(self):
        if self._owns:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- reads -------------------------------------------------------------
    def get(self, kind, entity_id, course_id=WORKSPACE_COURSE_ID, learner_id=None):
        row = self._conn.execute(
            "SELECT body_json, version FROM entities "
            "WHERE learner_id=? AND course_id=? AND kind=? AND entity_id=?",
            (learner_id or self.learner_id, course_id, kind, entity_id),
        ).fetchone()
        if row is None:
            return None, 0
        return json.loads(row["body_json"]), row["version"]

    def list_entities(self, kind, course_id=None):
        sql = ("SELECT body_json FROM entities WHERE learner_id=? AND kind=?"
               + ("" if course_id is None else " AND course_id=?"))
        args = [self.learner_id, kind] + ([] if course_id is None else [course_id])
        return [json.loads(r["body_json"]) for r in self._conn.execute(sql, args)]

    def events(self, course_id=None, limit=None):
        """Events in sequence order, with the stored envelope parsed.

        Each row carries `payload_json` — the complete validated envelope, kept
        whole so a stored event is self-describing. It is parsed here and also
        exposed as `payload`, because every caller so far wants the envelope's
        fields and re-parsing the column at each call site is how one of them
        ends up reading the raw JSON string by mistake.
        """
        sql = "SELECT * FROM events WHERE learner_id=?"
        args = [self.learner_id]
        if course_id is not None:
            sql += " AND course_id=?"
            args.append(course_id)
        sql += " ORDER BY seq"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)

        rows = []
        for row in self._conn.execute(sql, args):
            record = dict(row)
            try:
                envelope = json.loads(record.get("payload_json") or "{}")
            except ValueError:
                # A stored event that cannot be parsed is a real problem, but
                # raising here would make the whole log unreadable. The raw
                # column stays visible so the damage is reportable rather than
                # fatal.
                envelope = {}
            record["envelope"] = envelope
            record["payload"] = envelope.get("payload", {})
            record["type"] = envelope.get("type", record.get("event_type"))
            rows.append(record)
        return rows

    def session_improvement_consent(self, session_id):
        row = self._conn.execute(
            "SELECT body_json FROM entities WHERE learner_id=? AND course_id=? AND kind=? AND entity_id=?",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_consent", session_id),
        ).fetchone()
        if not row:
            return None
        body = json.loads(row["body_json"])
        expires_at = body.get("expires_at")
        if body.get("state") == "enabled" and expires_at:
            try:
                if datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    body["state"] = "expired"
            except (TypeError, ValueError):
                body["state"] = "disabled"
        return body

    def set_session_improvement_consent(self, session_id, *, scope, source="interactive_tty", request_id=None, expires_at=None):
        if not self.is_session_improvement_enabled():
            raise StoreError("SESSION_IMPROVEMENT_DISABLED", "интерактивное самоулучшение отключено глобально")
        if scope not in {"session_observation", "session_observation,session_overlay"}:
            raise StoreError("CONSENT_SCOPE_INVALID", "недопустимая область согласия")
        body = {"session_id": session_id, "state": "enabled", "scope": scope.split(","),
                "source": source, "persistent_opt_in": False, "expires_at": expires_at}
        result, replayed = self.apply(
            kind="session_improvement_consent", entity_id=session_id,
            events=["session_improvement.consent_enabled"], new_body=body,
            event_payloads=[{"scope": body["scope"], "source": source}], request_id=request_id,
        )
        return body, replayed

    def stop_session_improvement(self, session_id, *, request_id=None):
        current = self.session_improvement_consent(session_id)
        if current is None:
            return None, False
        body = dict(current)
        body["state"] = "disabled"
        result, replayed = self.apply(
            kind="session_improvement_consent", entity_id=session_id,
            events=["session_improvement.consent_disabled"], new_body=body,
            expected_version=self.version_of("session_improvement_consent", session_id),
            event_payloads=[{}], request_id=request_id,
        )
        return body, replayed

    def session_observation_stats(self, session_id, *, window_seconds=60):
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat().replace("+00:00", "Z")
        row = self._conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(LENGTH(body_json)), 0) AS bytes FROM entities "
            "WHERE learner_id=? AND course_id=? AND kind=? AND json_extract(body_json, '$.session_id')=? AND updated_at>=?",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_observation", session_id, cutoff),
        ).fetchone()
        return {"count": int(row["count"]), "bytes": int(row["bytes"])}

    def record_session_observation(self, session_id, *, observation_id, kind, area, summary, source_ref="", request_id=None):
        allowed = {"tool_error", "workflow_friction", "ui_or_cli_gap", "successful_correction", "session_preference"}
        if kind not in allowed:
            raise StoreError("OBSERVATION_KIND_INVALID", "недопустимый тип наблюдения")
        stats = self.session_observation_stats(session_id)
        if stats["count"] >= 20:
            raise StoreError("OBSERVATION_RATE_LIMIT", "достигнут лимит наблюдений за минуту")
        if stats["bytes"] >= 16_000:
            raise StoreError("OBSERVATION_BUDGET_EXCEEDED", "достигнут бюджет наблюдений за минуту")
        values = (observation_id, kind, area, summary)
        if not all(isinstance(value, str) and value.strip() for value in values) or not isinstance(source_ref, str):
            raise StoreError("OBSERVATION_SCHEMA_INVALID", "поля наблюдения должны быть непустыми строками")
        if any(len(value) > limit for value, limit in zip(values, (80, 40, 80, 500, 240))):
            raise StoreError("OBSERVATION_SIZE_LIMIT", "наблюдение превышает ограничение размера")
        import re
        if any(re.search(pattern, summary, re.IGNORECASE) or re.search(pattern, source_ref, re.IGNORECASE) for pattern in (r"student|learner|course|assignment|grade|transcript", r"ученик|курс|задани|оцен|диалог")):
            raise StoreError("OBSERVATION_PRIVACY_REJECTED", "наблюдение похоже на учебные или transcript-данные")
        if re.search(r"(token|secret|password|api[_-]?key|ghp_)[^\s=:]*\s*[=:]?\s*\S+", summary, re.IGNORECASE):
            raise StoreError("OBSERVATION_SECRET_REJECTED", "наблюдение содержит потенциальный секрет")
        if "\\" in source_ref or ".." in source_ref.split("/") or source_ref.startswith(("/", "//")):
            raise StoreError("OBSERVATION_PATH_REJECTED", "ссылка источника должна быть относительной")
        if len(self.list_entities("session_improvement_observation")) >= 1000:
            raise StoreError("OBSERVATION_CIRCUIT_OPEN", "достигнут общий лимит наблюдений")
        body = {"observation_id": observation_id, "session_id": session_id, "kind": kind,
                "area": area, "summary": summary, "source_ref": source_ref,
                "session_only": True, "persistent_opt_in": False, "created_at": now_iso()}
        result, replayed = self.apply(
            kind="session_improvement_observation", entity_id=observation_id,
            events=["session_improvement.observation_recorded"], new_body=body,
            event_payloads=[{"session_id": session_id, "kind": kind}], request_id=request_id,
        )
        return body, replayed

    def session_observations(self, session_id):
        rows = self._conn.execute(
            "SELECT body_json FROM entities WHERE learner_id=? AND course_id=? AND kind=? AND json_extract(body_json, '$.session_id')=? ORDER BY updated_at",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_observation", session_id),
        ).fetchall()
        return [json.loads(row["body_json"]) for row in rows]

    def session_summary(self, session_id):
        observations = self.session_observations(session_id)
        overlays = [json.loads(row["body_json"]) for row in self._conn.execute(
            "SELECT body_json FROM entities WHERE learner_id=? AND course_id=? AND kind=? AND json_extract(body_json, '$.session_id')=?",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_overlay", session_id))]
        candidates = []
        for item in observations:
            candidate = {"session_id": session_id, "kind": item["kind"], "area": item["area"],
                         "summary": item["summary"], "source_ref": item["source_ref"],
                         "persistent_opt_in": False}
            candidate["fingerprint"] = __import__("hashlib").sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            candidates.append(candidate)
        return {"session_id": session_id, "observation_count": len(observations),
                "overlay_count": len(overlays), "overlays": [{"overlay_id": item["overlay_id"], "state": item["state"]} for item in overlays],
                "candidates": candidates, "default_action": "discard", "persistent_opt_in_required": True}

    def save_session_candidate(self, session_id, fingerprint, *, request_id=None):
        summary = self.session_summary(session_id)
        candidate = next((item for item in summary["candidates"] if item["fingerprint"] == fingerprint), None)
        if candidate is None:
            raise StoreError("CANDIDATE_NOT_FOUND", "кандидат с таким fingerprint не найден")
        candidate["persistent_opt_in"] = True
        candidate["saved_at"] = now_iso()
        result, replayed = self.apply(kind="session_improvement_candidate", entity_id=fingerprint,
                                      events=["session_improvement.candidate_saved"], new_body=candidate,
                                      event_payloads=[{"session_id": session_id, "fingerprint": fingerprint}], request_id=request_id)
        return candidate, replayed

    def discard_session(self, session_id):
        return {"session_id": session_id, "discarded": True, "persistent_state_changed": False}

    def list_active_sessions(self):
        rows = self._conn.execute(
            "SELECT body_json FROM entities WHERE learner_id=? AND course_id=? AND kind=?",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_consent"),
        ).fetchall()
        active = []
        for row in rows:
            consent = json.loads(row["body_json"])
            if consent.get("state") == "enabled":
                expires_at = consent.get("expires_at")
                is_expired = False
                if expires_at:
                    try:
                        if datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                            is_expired = True
                    except (TypeError, ValueError):
                        is_expired = True
                if not is_expired:
                    active.append(consent["session_id"])
        return active

    def cleanup_stale_session_state(self, session_id):
        count = 0
        for kind in ("session_improvement_observation", "session_improvement_overlay", "session_improvement_consent"):
            result = self._conn.execute(
                "DELETE FROM entities WHERE learner_id=? AND course_id=? AND kind=? AND json_extract(body_json, '$.session_id')=?",
                (self.learner_id, WORKSPACE_COURSE_ID, kind, session_id),
            )
            count += result.rowcount
        return {"session_id": session_id, "removed": count}

    def recover_session_overlays(self, session_id):
        rows = self._conn.execute(
            "SELECT body_json FROM entities WHERE learner_id=? AND course_id=? AND kind=?",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_overlay"),
        ).fetchall()
        recovered = []
        now = datetime.now(timezone.utc)
        for row in rows:
            overlay = json.loads(row["body_json"])
            if overlay.get("session_id") != session_id or overlay.get("state") not in {"preview", "applied"}:
                continue
            try:
                expired = datetime.fromisoformat(overlay["expires_at"].replace("Z", "+00:00")) <= now
            except (KeyError, TypeError, ValueError):
                expired = True
            if expired:
                overlay["state"] = "undone"
                self.apply(kind="session_improvement_overlay", entity_id=overlay["overlay_id"],
                           events=["session_improvement.overlay_recovered"], new_body=overlay,
                           expected_version=self.version_of("session_improvement_overlay", overlay["overlay_id"]),
                           event_payloads=[{"session_id": session_id}], request_id="overlay-recover-" + overlay["overlay_id"])
                recovered.append(overlay["overlay_id"])
        return recovered

    def session_overlay(self, session_id, overlay_id):
        row = self._conn.execute(
            "SELECT body_json FROM entities WHERE learner_id=? AND course_id=? AND kind=? AND entity_id=?",
            (self.learner_id, WORKSPACE_COURSE_ID, "session_improvement_overlay", overlay_id),
        ).fetchone()
        return json.loads(row["body_json"]) if row else None

    def create_session_overlay(self, session_id, *, overlay_id, area, before, after, ttl_seconds=3600, request_id=None):
        consent = self.session_improvement_consent(session_id)
        if not consent or consent.get("state") != "enabled" or "session_overlay" not in consent.get("scope", []):
            raise StoreError("OVERLAY_CONSENT_REQUIRED", "для overlay требуется отдельное session consent")
        if area not in {"presentation", "workflow_hint"}:
            raise StoreError("OVERLAY_AREA_INVALID", "область overlay не разрешена")
        if not all(isinstance(value, str) and value.strip() for value in (overlay_id, before, after)):
            raise StoreError("OVERLAY_SCHEMA_INVALID", "поля overlay должны быть непустыми строками")
        if len(before) > 500 or len(after) > 500:
            raise StoreError("OVERLAY_SIZE_LIMIT", "overlay превышает ограничение размера")
        body = {"overlay_id": overlay_id, "session_id": session_id, "area": area,
                "before": before, "after": after, "state": "preview",
                "session_only": True, "persistent_opt_in": False,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
        self.apply(kind="session_improvement_overlay", entity_id=overlay_id,
                   events=["session_improvement.overlay_previewed"], new_body=body,
                   event_payloads=[{"session_id": session_id, "area": area}], request_id=request_id)
        return body

    def apply_session_overlay(self, session_id, overlay_id, *, request_id=None):
        overlay = self.session_overlay(session_id, overlay_id)
        if not overlay or overlay.get("session_id") != session_id:
            raise StoreError("OVERLAY_NOT_FOUND", "overlay не найден")
        if overlay.get("state") != "preview":
            raise StoreError("OVERLAY_STATE_INVALID", "overlay уже применён или отменён")
        if datetime.fromisoformat(overlay["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            raise StoreError("OVERLAY_EXPIRED", "срок overlay истёк")
        body = dict(overlay)
        body["state"] = "applied"
        self.apply(kind="session_improvement_overlay", entity_id=overlay_id,
                   events=["session_improvement.overlay_applied"], new_body=body,
                   expected_version=self.version_of("session_improvement_overlay", overlay_id),
                   event_payloads=[{"session_id": session_id}], request_id=request_id)
        return body

    def undo_session_overlay(self, session_id, overlay_id, *, request_id=None):
        overlay = self.session_overlay(session_id, overlay_id)
        if not overlay or overlay.get("session_id") != session_id:
            raise StoreError("OVERLAY_NOT_FOUND", "overlay не найден")
        if overlay.get("state") not in {"preview", "applied"}:
            raise StoreError("OVERLAY_STATE_INVALID", "overlay уже отменён")
        body = dict(overlay)
        body["state"] = "undone"
        self.apply(kind="session_improvement_overlay", entity_id=overlay_id,
                   events=["session_improvement.overlay_undone"], new_body=body,
                   expected_version=self.version_of("session_improvement_overlay", overlay_id),
                   event_payloads=[{"session_id": session_id}], request_id=request_id)
        return body

    def is_session_improvement_enabled(self):
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='session_improvement_enabled'",
        ).fetchone()
        return row and row["value"] == "1"

    def set_session_improvement_enabled(self, enabled, *, request_id=None):
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("session_improvement_enabled", "1" if enabled else "0"),
        )

    def version_of(self, kind, entity_id, course_id=WORKSPACE_COURSE_ID):
        row = self._conn.execute(
            "SELECT version FROM entities WHERE learner_id=? AND course_id=? AND kind=? AND entity_id=?",
            (self.learner_id, course_id, kind, entity_id),
        ).fetchone()
        return row["version"] if row else 0

    # -- writes ------------------------------------------------------------
    def apply(self, *, kind, entity_id, events, course_id=WORKSPACE_COURSE_ID,
              expected_version=None, request_id=None, validate_as=None,
              new_body=None, event_payloads=None, learner_id=None):
        """Apply one command atomically: idempotency, version, events, entity.

        `events` is a list of event type names; `event_payloads` holds one dict
        per event. `new_body` is the entity body after the command. Everything is
        written in a single transaction, or nothing is.
        """
        learner_id = learner_id or self.learner_id
        request_id = request_id or new_id()
        event_payloads = event_payloads or [{} for _ in events]

        if len(event_payloads) != len(events):
            raise StoreError("EVENT_PAYLOAD_MISMATCH",
                             "число событий и payload не совпадает")

        command = {
            "kind": kind, "entity_id": entity_id, "course_id": course_id,
            "learner_id": learner_id, "events": list(events),
            "expected_version": expected_version, "body": new_body,
            "payloads": event_payloads,
        }
        digest = command_hash(command)

        # Validation happens before the transaction opens (a bad document must
        # not hold a write lock) and is translated into the store's own error
        # vocabulary, so callers can tell "your document is wrong" apart from
        # "the store broke" by code rather than by exception type.
        if new_body is not None and validate_as:
            # `validate_as` names a v2 contract. Entity bodies are validated
            # against their own contract (`objective_state`), not the document
            # that contains them: an entity is written on its own, so the rules
            # must hold at the moment of that write.
            try:
                schemas.validate(new_body, validate_as)
            except schemas.SchemaError as e:
                raise StoreError(e.code, e.message)

        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as e:
            raise StoreError("STORE_BUSY", "не удалось начать транзакцию: %s" % e)

        try:
            previous = self._conn.execute(
                "SELECT command_hash, result_json FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous is not None:
                if previous["command_hash"] != digest:
                    raise IdempotencyConflict(
                        "request_id %s уже использован для другой команды" % request_id
                    )
                self._conn.execute("COMMIT")
                return json.loads(previous["result_json"]), True

            current = self._conn.execute(
                "SELECT version FROM entities "
                "WHERE learner_id=? AND course_id=? AND kind=? AND entity_id=?",
                (learner_id, course_id, kind, entity_id),
            ).fetchone()
            current_version = current["version"] if current else 0

            if expected_version is not None and expected_version != current_version:
                raise StateConflict(
                    "версия %s/%s изменилась: ожидалась %s, сейчас %s"
                    % (kind, entity_id, expected_version, current_version),
                    expected=expected_version, actual=current_version,
                )

            stamp = now_iso()
            event_ids = []
            for event_type, payload in zip(events, event_payloads):
                envelope = {
                    "schema_version": SCHEMA_VERSION,
                    "event_id": new_id(),
                    "request_id": request_id,
                    "course_id": course_id,
                    "learner_id": learner_id,
                    "session_id": payload.get("session_id"),
                    "occurred_at": stamp,
                    "type": event_type,
                    "actor": payload.pop("_actor", "core"),
                    "provenance": payload.pop("_provenance", "core_computed"),
                    "payload": payload,
                }
                self._conn.execute(
                    "INSERT INTO events(event_id, request_id, learner_id, course_id, "
                    "entity_kind, entity_id, event_type, occurred_at, payload_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (envelope["event_id"], request_id, learner_id, course_id,
                     kind, entity_id, event_type, stamp, _json(envelope)),
                )
                event_ids.append(envelope["event_id"])

            new_version = current_version + 1
            if new_body is not None:
                body = dict(new_body)
                body.setdefault("schema_version", SCHEMA_VERSION)
                self._conn.execute(
                    "INSERT INTO entities(learner_id, course_id, kind, entity_id, version, "
                    "body_json, updated_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(learner_id, course_id, kind, entity_id) DO UPDATE SET "
                    "version=excluded.version, body_json=excluded.body_json, "
                    "updated_at=excluded.updated_at",
                    (learner_id, course_id, kind, entity_id, new_version, _json(body), stamp),
                )
            else:
                new_version = current_version

            result = {
                "ok": True,
                "entity": {"kind": kind, "entity_id": entity_id,
                           "course_id": course_id, "version": new_version},
                "event_ids": event_ids,
                "request_id": request_id,
                "replayed": False,
            }
            self._conn.execute(
                "INSERT INTO requests(request_id, command_hash, result_json, created_at) "
                "VALUES (?,?,?,?)",
                (request_id, digest, _json(result), stamp),
            )
            self._conn.execute("COMMIT")
            return result, False

        except StoreError:
            self._conn.execute("ROLLBACK")
            raise
        except schemas.SchemaError as e:
            # A contract failure is a refused command, not an internal error: it
            # must surface with its own code so callers and tests can tell
            # "your document is wrong" from "the store broke".
            self._conn.execute("ROLLBACK")
            raise StoreError(e.code, e.message)
        except sqlite3.Error as e:
            self._conn.execute("ROLLBACK")
            raise StoreError("STORE_WRITE_FAILED", "запись не выполнена: %s" % e)
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # -- artifacts ---------------------------------------------------------
    def put_artifact(self, data, *, media_type, provenance, retention_days=None):
        """Store evidence bytes under their content hash.

        The same bytes stored twice are one artifact with two owners, which is
        why ownership is tracked in `artifact_refs` rather than duplicated here.
        """
        import hashlib
        import tempfile

        digest = hashlib.sha256(data).hexdigest()
        directory = paths.artifact_store_dir(self.root)
        directory.mkdir(parents=True, exist_ok=True)
        target = paths.ensure_within(directory, directory / digest)

        if not target.exists():
            # Write then rename: a partially written artifact must never be
            # readable under its final, content-addressed name.
            fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".part")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, target)
            except OSError:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

        retention = None
        if retention_days:
            from datetime import timedelta
            retention = (datetime.now(timezone.utc) + timedelta(days=retention_days)) \
                .replace(microsecond=0).isoformat().replace("+00:00", "Z")

        self._conn.execute(
            "INSERT OR IGNORE INTO artifacts(sha256, relative_path, media_type, byte_size, "
            "created_at, retention_until, provenance) VALUES (?,?,?,?,?,?,?)",
            (digest, str(target.relative_to(self.root)).replace(os.sep, "/"),
             media_type, len(data), now_iso(), retention, provenance),
        )
        return digest

    def link_artifact(self, digest, *, entity_kind, entity_id,
                      course_id=WORKSPACE_COURSE_ID):
        self._conn.execute(
            "INSERT OR IGNORE INTO artifact_refs(learner_id, course_id, entity_kind, "
            "entity_id, sha256, created_at) VALUES (?,?,?,?,?,?)",
            (self.learner_id, course_id, entity_kind, entity_id, digest, now_iso()),
        )
        return digest

    def read_artifact(self, digest):
        """Read evidence bytes, verifying the hash on the way out."""
        import hashlib

        row = self._conn.execute(
            "SELECT relative_path FROM artifacts WHERE sha256=?", (digest,)
        ).fetchone()
        if row is None:
            raise StoreError("ARTIFACT_MISSING", "артефакт не найден: %s" % digest)
        path = paths.ensure_within(self.root, self.root / row["relative_path"])
        if not path.is_file():
            raise StoreError("ARTIFACT_MISSING", "файл артефакта отсутствует: %s" % digest)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise StoreError("ARTIFACT_CORRUPT", "содержимое артефакта не совпадает с хешем: %s" % digest)
        return data

    def artifact_scope(self, digest):
        """Which learner/course scopes may read this artifact."""
        return [(r["learner_id"], r["course_id"]) for r in self._conn.execute(
            "SELECT learner_id, course_id FROM artifact_refs WHERE sha256=?", (digest,)
        )]

    # -- maintenance -------------------------------------------------------
    def integrity_check(self):
        return self._conn.execute("PRAGMA integrity_check").fetchone()[0]

    def count(self, table):
        allowed = {"entities", "events", "requests", "artifacts", "artifact_refs", "migrations"}
        if table not in allowed:
            raise StoreError("STORE_TABLE_UNKNOWN", "неизвестная таблица: %r" % table)
        return self._conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]

    def backup_to(self, destination):
        """Consistent copy via the SQLite backup API.

        Copying an open WAL database file-by-file can capture a torn state;
        the backup API is the supported way to snapshot it.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            with target:
                self._conn.backup(target)
        finally:
            target.close()
        return destination
