# -*- coding: utf-8 -*-
"""Importing a v1 progress record without inventing anything.

The v1 harness kept progress in a Markdown file that a model wrote by hand. It
is heterogeneous on purpose: several students in one file, prose mixed with
tables, and claims like `status: mastered` that were never evidence-checked.

Migration has one job, and it is a conservative one: **extract only what is
actually written, preserve everything else verbatim, and mark what a human still
has to confirm.** Specifically:

* `mastered` becomes `practising` with `legacy_claim: "mastered"`. It was a
  claim, not a demonstrated result, and silently promoting it would hand the
  learner credit they never evidenced — the exact failure the v2 record exists
  to prevent.
* prose that cannot be parsed is not guessed at: it goes to `pending_review`
  with its line numbers, and the original file is kept whole.
* a file describing several students is refused for automatic import, because
  attributing one person's line to another is worse than asking.
* the original bytes are preserved as an artifact, so the import is auditable
  and reversible.

The parser is deliberately narrow. It recognises the structures the v1 skill
actually documented (the six records, per-student blocks) and treats everything
else as prose. A cleverer parser would produce more structure and less truth.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# v1 mastery values -> v2 stage. A v1 `mastered` claim is NOT evidence.
STAGE_MAP = {
    "new": "new",
    "learning": "learning",
    "practising": "practising",
    "practicing": "practising",
    "mastered": "practising",
    "blocked": "blocked",
}

_STUDENT_HEADING = re.compile(r"^#{2,4}\s*(?P<id>stu-[A-Za-z0-9_-]+)\s*(?:\|\s*(?P<name>[^|]+))?")
_STUDENT_LINE = re.compile(r"^-\s*(?P<id>stu-[A-Za-z0-9_-]+)\s*\|\s*(?P<rest>.+)$")
_STATUS_LINE = re.compile(
    r"(?:\*\*)?status(?:\*\*)?\s*[:=]\s*(?P<stage>[A-Za-z_]+)"
    r"(?:\s*\|\s*last\s*[:=]\s*(?P<last>\d{4}-\d{2}-\d{2}))?",
    re.IGNORECASE,
)
_BASELINE_LINE = re.compile(r"^-\s*baseline\s*:\s*(?P<text>.+)$", re.IGNORECASE)
_DEMONSTRATED_LINE = re.compile(r"^-\s*(?:demonstrated|показал[аи]?)\s*:\s*(?P<text>.+)$", re.IGNORECASE)
_MODULE_LINE = re.compile(r"модул[ьяе]\s*(?P<num>\d+)", re.IGNORECASE)
_GRADED_LINE = re.compile(r"^\s*graded\s*:\s*(?P<items>.+)$", re.IGNORECASE)
_PRACTICE_LINE = re.compile(r"^\s*practice\s*:\s*(?P<items>.+)$", re.IGNORECASE)
_DEAD_END = re.compile(r"^-\s*(?P<id>dead-\d+)\s*\|\s*(?P<body>.+)$")
_OPEN_Q = re.compile(r"^-\s*(?P<id>open-\d+)\s*\|\s*(?P<body>.+)$")
_PREFERENCE = re.compile(
    r"(?P<id>stu-[A-Za-z0-9_-]+)[^\n]*?—\s*(?P<pref>hints-then-solution|solution-first|hints|prefer-ask)",
    re.IGNORECASE,
)


@dataclass
class LegacyStudent:
    legacy_id: str
    name: str | None = None
    baseline: str | None = None
    demonstrated: str | None = None
    legacy_stage: str | None = None
    last_seen: str | None = None
    module_hint: str | None = None
    preference: str | None = None
    open_questions: list[str] = field(default_factory=list)


@dataclass
class LegacyImport:
    source_path: str
    source_sha256: str
    source_bytes: int
    students: list[LegacyStudent]
    graded: list[str]
    practice: list[str]
    dead_ends: list[dict]
    open_questions: list[str]
    pending_review: list[dict]
    multi_student: bool
    parsed_lines: int
    total_lines: int

    def as_report(self):
        """A report for a human: what was understood, what still needs a look."""
        return {
            "schema_version": 2,
            "source": {
                "path": self.source_path,
                "sha256": self.source_sha256,
                "bytes": self.source_bytes,
                "lines": self.total_lines,
            },
            "students": [
                {
                    "legacy_id": s.legacy_id,
                    "name": s.name,
                    "baseline": s.baseline,
                    "demonstrated": s.demonstrated,
                    "legacy_claim": s.legacy_stage,
                    "imported_stage": STAGE_MAP.get((s.legacy_stage or "").lower()),
                    "stage_basis": ("legacy_claim_not_evidence"
                                   if (s.legacy_stage or "").lower() == "mastered"
                                   else "legacy_record"),
                    "last_seen": s.last_seen,
                    "module_hint": s.module_hint,
                    "preference": s.preference,
                    "open_questions": s.open_questions,
                }
                for s in self.students
            ],
            "graded": self.graded,
            "practice": self.practice,
            "dead_ends": self.dead_ends,
            "open_questions": self.open_questions,
            "pending_review": self.pending_review,
            "multi_student": self.multi_student,
            "counts": {
                "students": len(self.students),
                "parsed_lines": self.parsed_lines,
                "pending_review": len(self.pending_review),
            },
            "warnings": self._warnings(),
        }

    def _warnings(self):
        warnings = []
        if self.multi_student:
            warnings.append(
                "в одном файле несколько обучающихся: автоматический импорт "
                "запрещён, требуется разделение человеком"
            )
        if any((s.legacy_stage or "").lower() == "mastered" for s in self.students):
            warnings.append(
                "заявленное «mastered» импортировано как «practising»: "
                "это было утверждение без проверки, а не доказательство"
            )
        if self.pending_review:
            warnings.append(
                "%d строк не распознаны и сохранены для ручной проверки "
                "(догадки не подставляются)" % len(self.pending_review)
            )
        return warnings


def read_legacy_progress(path):
    """Parse a v1 progress file into `LegacyImport`. Never raises on content."""
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()

    students: dict[str, LegacyStudent] = {}
    order: list[str] = []
    graded: list[str] = []
    practice: list[str] = []
    dead_ends: list[dict] = []
    open_questions: list[str] = []
    pending: list[dict] = []
    parsed = 0
    current: LegacyStudent | None = None
    inside_dead_end_section = False

    def ensure(legacy_id, name=None):
        if legacy_id not in students:
            students[legacy_id] = LegacyStudent(legacy_id=legacy_id, name=name)
            order.append(legacy_id)
        elif name and not students[legacy_id].name:
            students[legacy_id].name = name
        return students[legacy_id]

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        heading = _STUDENT_HEADING.match(stripped)
        if heading:
            current = ensure(heading.group("id"), (heading.group("name") or "").strip() or None)
            parsed += 1
            continue

        listed = _STUDENT_LINE.match(stripped)
        if listed and listed.group("id") not in ("", None):
            current = ensure(listed.group("id"))
            # A cohort list line may also carry the delivery preference.
            pref = _PREFERENCE.search(stripped)
            if pref and current.preference is None:
                current.preference = pref.group("pref").lower()
            parsed += 1
            continue

        if stripped.lower().startswith("## ") and "dead end" in stripped.lower():
            inside_dead_end_section = True
            continue
        if stripped.startswith("## ") and "dead end" not in stripped.lower():
            inside_dead_end_section = False

        status = _STATUS_LINE.search(stripped)
        if status and current is not None:
            current.legacy_stage = status.group("stage").lower()
            current.last_seen = status.group("last")
            parsed += 1
            continue

        baseline = _BASELINE_LINE.match(stripped)
        if baseline and current is not None:
            current.baseline = baseline.group("text").strip()
            parsed += 1
            continue

        demonstrated = _DEMONSTRATED_LINE.match(stripped)
        if demonstrated and current is not None:
            current.demonstrated = demonstrated.group("text").strip()
            parsed += 1
            continue

        graded_hit = _GRADED_LINE.match(line)
        if graded_hit:
            graded = [x.strip() for x in graded_hit.group("items").split(",") if x.strip()]
            parsed += 1
            continue

        practice_hit = _PRACTICE_LINE.match(line)
        if practice_hit:
            practice = [x.strip() for x in practice_hit.group("items").split(",") if x.strip()]
            parsed += 1
            continue

        dead = _DEAD_END.match(stripped)
        if dead:
            parts = [p.strip() for p in dead.group("body").split("|")]
            dead_ends.append({
                "legacy_id": dead.group("id"),
                "what": parts[0] if parts else "",
                "why": parts[1] if len(parts) > 1 else "",
                "line": number,
            })
            parsed += 1
            continue

        question = _OPEN_Q.match(stripped)
        if question:
            parts = [p.strip() for p in question.group("body").split("|")]
            text_value = " ".join(p for p in parts if p)
            open_questions.append(text_value)
            if current is not None:
                current.open_questions.append(text_value)
            parsed += 1
            continue

        module = _MODULE_LINE.search(stripped)
        if module and current is not None and current.module_hint is None:
            current.module_hint = "module %s" % module.group("num")
            parsed += 1
            continue

        if stripped.startswith(("- [x]", "- [ ]")):
            # A checklist item is a coverage note, not a demonstrated result:
            # v1 recorded "we covered it", and only an unaided attempt can
            # support a mastery claim.
            if current is not None:
                current.open_questions.append(
                    "пункт чек-листа требует проверки: %s" % stripped[5:].strip()
                )
            parsed += 1
            continue

        # Anything else is prose. It is recorded with its coordinates instead of
        # being interpreted; guessing here is how a record starts lying.
        pending.append({"line": number, "text": stripped[:300]})

    cohort_names = [s.name for s in students.values() if s.name]
    multi = len(students) > 1 or len([n for n in cohort_names if n]) > 1

    return LegacyImport(
        source_path=str(path),
        source_sha256=digest,
        source_bytes=len(raw),
        students=[students[i] for i in order],
        graded=graded,
        practice=practice,
        dead_ends=dead_ends,
        open_questions=open_questions,
        pending_review=pending,
        multi_student=multi,
        parsed_lines=parsed,
        total_lines=len(lines),
    )


def import_into_store(store, legacy, *, course_id, course_revision, confirm_split=None):
    """Write a parsed legacy record into the v2 store.

    The source bytes are always preserved as a content-addressed artifact: the
    import must be auditable and reversible, and a report that says "we read
    your file" is not evidence unless the file itself is kept.

    Refuses a multi-student file unless `confirm_split` maps each legacy id to a
    separate learner — one learner's record must not absorb another's, and the
    store holds one learner's data.
    """
    from . import store as store_module

    if legacy.multi_student and not confirm_split:
        raise store_module.StoreError(
            "LEGACY_MULTI_STUDENT",
            "файл описывает нескольких обучающихся (%s); разделите записи "
            "явно через confirm_split — приписывать одному чужие строки нельзя"
            % ", ".join(s.legacy_id for s in legacy.students),
        )

    if confirm_split:
        wanted = set(confirm_split.values())
        if len(wanted) != 1 or store.learner_id not in wanted:
            raise store_module.StoreError(
                "LEGACY_SPLIT_MISMATCH",
                "confirm_split указывает на другого обучающегося, чем открытая "
                "запись %s" % store.learner_id,
            )

    report = legacy.as_report()

    # Preserve the original bytes first: if anything below fails, the source is
    # still recoverable and the import can be retried from it.
    source_path = Path(legacy.source_path)
    digest = store.put_artifact(
        source_path.read_bytes(),
        media_type="text/markdown",
        provenance="legacy_import",
    )

    result, _ = store.apply(
        kind="legacy_import",
        entity_id=legacy.source_sha256,
        events=["legacy.imported"],
        course_id=course_id,
        new_body={
            "schema_version": 2,
            "source_sha256": legacy.source_sha256,
            "source_path": legacy.source_path,
            "preserved_artifact": digest,
            "course_revision": course_revision,
            "report": report,
        },
        event_payloads=[{
            "course_revision": course_revision,
            "source_sha256": legacy.source_sha256,
            "preserved_artifact": digest,
            "students": len(legacy.students),
            "pending_review": len(legacy.pending_review),
            "_actor": "learner_cli",
            "_provenance": "legacy_import",
        }],
    )
    store.link_artifact(digest, entity_kind="legacy_import",
                        entity_id=legacy.source_sha256, course_id=course_id)

    written = []
    for student in legacy.students:
        stage = STAGE_MAP.get((student.legacy_stage or "").lower())
        if not stage:
            continue
        if not student.module_hint:
            # Without a module there is no objective to attach the legacy state
            # to. Inventing one would fabricate a fact about the course.
            continue
        _, version = store.get("legacy_objective_claim", student.legacy_id, course_id)
        store.apply(
            kind="legacy_objective_claim",
            entity_id=student.legacy_id,
            events=["objective.legacy_claim_imported"],
            course_id=course_id,
            expected_version=version,
            new_body={
                "schema_version": 2,
                "legacy_id": student.legacy_id,
                "course_id": course_id,
                "imported_stage": stage,
                "legacy_claim": student.legacy_stage,
                "module_hint": student.module_hint,
                "verified": False,
                "basis": "legacy_record_not_evidence",
            },
            event_payloads=[{
                "legacy_id": student.legacy_id,
                "imported_stage": stage,
                "legacy_claim": student.legacy_stage,
                "_actor": "learner_cli",
                "_provenance": "legacy_import",
            }],
        )
        written.append(student.legacy_id)

    return {"import": result, "report": report, "claims_written": written,
            "preserved_artifact": digest}
