# -*- coding: utf-8 -*-
"""The accepted course: contract, binding, objective graph.

Why this module exists separately from `courses.py`: `courses.py` moves course
*files* around (clone, update, dirty state). That is bookkeeping. This module
answers a different question — *what is this course allowed to tell the agent to
do* — and those two must not be the same code path, because file operations are
run by whoever owns the machine while policy is read by the teaching model.

Three properties are the whole point:

* **Acceptance is a snapshot, not a pointer.** A course directory is editable;
  a student branch can change `botai/course.json` after the fact. `accept()`
  copies the contract/track into `.botai/accepted/<sha256>/` and the binding
  refers to those hashes. Reading policy from the live file would mean an
  assignment could be reclassified from `graded` to `practice` by whoever
  commits last.
* **Unknown is strict.** An assignment not in the accepted contract is
  `unknown`, which behaves like `graded` for help purposes. The default must
  never be "probably practice".
* **The graph is checked, not assumed.** Prerequisites must exist, ids must be
  unique, and the objective graph must be acyclic. A course whose track is
  broken is refused at acceptance with the specific defect named, rather than
  discovered mid-session when the planner walks a missing prerequisite.

Nothing here calls a model, opens the network, or executes course code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import paths, schemas, store as store_mod

ACCEPTED_DIRNAME = "accepted"
BINDING_FILENAME = "binding.json"
COURSE_CONTRACT_PATH = "botai/course.json"

# Assessment values, ordered from strictest. `unknown` is deliberately treated
# as strict everywhere: it is the state of "we could not establish this", and
# assuming practice would hand out solutions to graded work.
ASSESSMENT_STRICT_ORDER = ("graded", "unknown", "practice")

# The equivalence used when deciding whether a new revision is a change worth
# re-accepting. Only content that can alter teaching behaviour matters.
ASSESSMENT_FIELDS = ("assessment", "assessment_source", "rubric_path")


class CourseError(RuntimeError):
    """A refused course operation, with a stable code for callers and tests."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(document):
    """Byte-stable rendering: the hash of a contract must not depend on spacing.

    A contract re-saved by an editor with different indentation is the same
    contract, and re-accepting it would be noise a human cannot review.
    """
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------
# Loading and validating a course tree
# --------------------------------------------------------------------------

def load_json(path, *, code="COURSE_JSON_INVALID"):
    path = Path(path)
    if not path.is_file():
        raise CourseError("COURSE_FILE_MISSING", "файл не найден: %s" % path)
    try:
        # `utf-8-sig` silently drops a byte-order mark. Windows editors add one
        # to JSON by default, and a course authored there would otherwise be
        # rejected for a character the author never typed. It does not weaken
        # the parse: the BOM is not part of the document.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise CourseError(code, "не удалось прочитать %s: %s" % (path, e))


def _validate(document, contract):
    try:
        schemas.validate(document, contract)
    except schemas.SchemaError as e:
        raise CourseError(e.code, e.message)
    except schemas.SchemasUnavailable as e:
        raise CourseError("SCHEMAS_UNAVAILABLE", str(e))


def _safe_relative(root, relative, *, what):
    """Resolve a contract-declared relative path, refusing anything outside.

    The paths come from `course.json`, which arrives with the course — i.e. from
    a document the agent does not control. A `../../` in `syllabus_path` would
    otherwise read whatever it points at.
    """
    text = str(relative or "")
    if not text:
        raise CourseError("COURSE_PATH_EMPTY", "%s не задан в контракте курса" % what)
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CourseError(
            "COURSE_PATH_OUTSIDE",
            "%s указывает за пределы курса: %r" % (what, text),
        )
    try:
        return paths.ensure_within(Path(root), Path(root) / candidate)
    except paths.PathError as e:
        raise CourseError(e.code, "%s: %s" % (what, e.message))


def find_course_root(course_dir):
    """Locate the directory that actually holds `botai/course.json`.

    A course clone may put the contract at the top level or one directory down
    (a repository that keeps the course in a subfolder). Both are legitimate;
    guessing a deeper layout is not, so only these two are tried.
    """
    course_dir = Path(course_dir)
    if (course_dir / COURSE_CONTRACT_PATH).is_file():
        return course_dir
    for child in sorted(p for p in course_dir.iterdir() if p.is_dir()):
        if (child / COURSE_CONTRACT_PATH).is_file():
            return child
    return course_dir


def inspect(course_dir):
    """Read-only description of a course: what it declares, what is missing.

    Never writes and never executes anything from the course. This is what
    `course-inspect` shows *before* a human accepts anything, so it must be
    honest about absence rather than filling gaps with defaults.
    """
    course_dir = Path(course_dir)
    root = find_course_root(course_dir)
    report = {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "course_dir": str(course_dir),
        "contract_root": str(root),
        "has_contract": False,
        "contract_path": str(root / COURSE_CONTRACT_PATH),
        "problems": [],
        "course_id": None,
        "title": None,
        "licenses": None,
        "contribution_enabled": False,
        "assignments": [],
        "unknown_assessment_count": 0,
        "objectives": [],
        "modules": [],
        "readiness": "no_contract",
    }

    contract_file = root / COURSE_CONTRACT_PATH
    if not contract_file.is_file():
        report["problems"].append({
            "code": "COURSE_CONTRACT_MISSING",
            "message_ru": "в курсе нет %s: условия обучения не объявлены" % COURSE_CONTRACT_PATH,
        })
        legacy = root / ".botai-course.json"
        report["legacy_contract_present"] = legacy.is_file()
        return report

    contract = load_json(contract_file)
    try:
        _validate(contract, "course")
    except CourseError as e:
        report["problems"].append({"code": e.code, "message_ru": e.message})
        report["readiness"] = "contract_invalid"
        return report

    report["has_contract"] = True
    report["course_id"] = contract["course_id"]
    report["title"] = contract["title"]
    report["licenses"] = contract.get("licenses")
    report["contribution_enabled"] = bool(
        (contract.get("contribution") or {}).get("enabled")
    )
    report["assessment_kinds"] = sorted({a["assessment"] for a in contract["assignments"]})
    report["unknown_assessment_count"] = sum(
        1 for a in contract["assignments"] if a["assessment"] == "unknown"
    )

    for assignment in contract["assignments"]:
        target = _safe_relative(root, assignment["path"], what="assignment path")
        entry = {
            "assignment_id": assignment["assignment_id"],
            "path": assignment["path"],
            "assessment": assignment["assessment"],
            "present": target.is_file(),
            "objective_ids": list(assignment["objective_ids"]),
        }
        if not entry["present"]:
            report["problems"].append({
                "code": "ASSIGNMENT_FILE_MISSING",
                "message_ru": "файл задания отсутствует: %s" % assignment["path"],
            })
        report["assignments"].append(entry)

    track_path = _safe_relative(root, contract["track_path"], what="track_path")
    if not track_path.is_file():
        report["problems"].append({
            "code": "TRACK_MISSING",
            "message_ru": "программа курса отсутствует: %s" % contract["track_path"],
        })
        report["readiness"] = "track_missing"
        return report

    track = load_json(track_path)
    try:
        _validate(track, "track")
        graph_problems = check_track(track)
    except CourseError as e:
        report["problems"].append({"code": e.code, "message_ru": e.message})
        report["readiness"] = "track_invalid"
        return report

    for problem in graph_problems:
        report["problems"].append(problem)

    report["modules"] = [
        {"module_id": m["module_id"], "title": m["title"], "order": m["order"],
         "objective_ids": list(m["objective_ids"])}
        for m in track["modules"]
    ]
    report["objectives"] = [
        {"objective_id": o["objective_id"], "title": o["title"],
         "prerequisites": list(o["prerequisites"]),
         "check_kinds": list(o["check_kinds"]),
         "required": bool(o.get("required"))}
        for o in track["objectives"]
    ]

    # Readiness is reported in the design's vocabulary so a caller never has to
    # infer "is this course teachable" from a pile of flags.
    if graph_problems or report["problems"]:
        report["readiness"] = "problems"
    else:
        report["readiness"] = "inspectable"
    return report


# --------------------------------------------------------------------------
# Track graph checks
# --------------------------------------------------------------------------

def check_track(track):
    """Structural defects in a track, as a list of problem dicts (possibly empty).

    Returns problems instead of raising so `inspect` can report *all* of them at
    once — fixing a course one error per run is needless friction.
    """
    problems = []
    objectives = track.get("objectives") or []
    modules = track.get("modules") or []

    objective_ids = [o["objective_id"] for o in objectives]
    module_ids = [m["module_id"] for m in modules]

    for kind, ids in (("objective", objective_ids), ("module", module_ids)):
        seen = set()
        for value in ids:
            if value in seen:
                problems.append({
                    "code": "DUPLICATE_ID",
                    "message_ru": "идентификатор %s повторяется: %s" % (kind, value),
                })
            seen.add(value)

    known_objectives = set(objective_ids)
    known_modules = set(module_ids)

    for objective in objectives:
        if objective["module_id"] not in known_modules:
            problems.append({
                "code": "OBJECTIVE_MODULE_UNKNOWN",
                "message_ru": "цель %s ссылается на несуществующий модуль %s"
                              % (objective["objective_id"], objective["module_id"]),
            })
        for prereq in objective["prerequisites"]:
            if prereq not in known_objectives:
                problems.append({
                    "code": "PREREQUISITE_UNKNOWN",
                    "message_ru": "цель %s требует несуществующую предпосылку %s"
                                  % (objective["objective_id"], prereq),
                })
            elif prereq == objective["objective_id"]:
                problems.append({
                    "code": "PREREQUISITE_SELF",
                    "message_ru": "цель %s требует саму себя" % objective["objective_id"],
                })

    for module in modules:
        for objective_id in module["objective_ids"]:
            if objective_id not in known_objectives:
                problems.append({
                    "code": "MODULE_OBJECTIVE_UNKNOWN",
                    "message_ru": "модуль %s ссылается на несуществующую цель %s"
                                  % (module["module_id"], objective_id),
                })

    # A cycle would make "what comes next" undecidable, so it is a refusal at
    # acceptance rather than a surprise for the planner.
    order = {m["module_id"]: m["order"] for m in modules}
    module_of = {o["objective_id"]: o["module_id"] for o in objectives}
    prereqs = {o["objective_id"]: list(o["prerequisites"]) for o in objectives}

    for module in modules:
        for objective_id in module["objective_ids"]:
            for prereq in prereqs.get(objective_id, []):
                prereq_module = module_of.get(prereq)
                if prereq_module is None:
                    continue
                if order.get(prereq_module, 0) > order.get(module["module_id"], 0):
                    problems.append({
                        "code": "PREREQUISITE_LATER_MODULE",
                        "message_ru": "цель %s (модуль %s) требует %s из более позднего "
                                      "модуля %s: программа должна быть исправлена автором "
                                      "или внешняя предпосылка описана отдельной целью"
                                      % (objective_id, module["module_id"], prereq,
                                         prereq_module),
                    })

    cycle = find_cycle(prereqs)
    if cycle:
        problems.append({
            "code": "PREREQUISITE_CYCLE",
            "message_ru": "цикл в предпосылках: %s" % " → ".join(cycle),
        })

    return problems


def find_cycle(prereqs):
    """A prerequisite cycle as a readable path, or None.

    Iterative on purpose: a pathological course could nest deeply enough to hit
    Python's recursion limit, and a stack overflow is not a useful answer to
    "is this track valid".
    """
    colour = {}  # node -> "grey" (on stack) | "black" (done)

    for start in sorted(prereqs):
        if colour.get(start) == "black":
            continue
        stack = [(start, iter(sorted(prereqs.get(start, []))))]
        colour[start] = "grey"
        trail = [start]
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if child not in prereqs:
                    continue
                state = colour.get(child)
                if state == "grey":
                    return trail[trail.index(child):] + [child]
                if state is None:
                    colour[child] = "grey"
                    trail.append(child)
                    stack.append((child, iter(sorted(prereqs.get(child, [])))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = "black"
                stack.pop()
                trail.pop()
    return None


def topological_order(track):
    """Objective ids in a stable, prerequisite-respecting order.

    Deterministic tie-breaking (by id) matters: "what comes next" must not
    change between two runs over the same course.
    """
    prereqs = {o["objective_id"]: set(o["prerequisites"]) for o in track["objectives"]}
    remaining = dict(prereqs)
    ordered = []
    while remaining:
        ready = sorted(k for k, v in remaining.items() if not (v & set(remaining)))
        if not ready:
            raise CourseError("PREREQUISITE_CYCLE",
                              "цикл в предпосылках: порядок целей не определён")
        for key in ready:
            ordered.append(key)
            remaining.pop(key)
    return ordered


# --------------------------------------------------------------------------
# The accepted course
# --------------------------------------------------------------------------

class AcceptedCourse:
    """An accepted contract plus the snapshots its hashes refer to.

    Constructed by `load_binding`/`accept`, never assembled field by field by a
    caller: a half-initialised course that silently falls back to the live file
    is exactly the failure this class exists to prevent.
    """

    def __init__(self, binding, contract, track, root):
        self.binding = binding
        self.contract = contract
        self.track = track
        self.root = Path(root)

    # -- identity ----------------------------------------------------------
    @property
    def course_id(self):
        return self.binding["course_id"]

    @property
    def slug(self):
        return self.binding["slug"]

    @property
    def revision(self):
        return self.binding["accepted_revision"]

    @property
    def contract_hash(self):
        return self.binding["accepted_contract_hash"]

    # -- assessment --------------------------------------------------------
    def assignment(self, assignment_id):
        for entry in self.contract["assignments"]:
            if entry["assignment_id"] == assignment_id:
                return entry
        return None

    def assessment_of(self, assignment_id):
        """The authoritative graded/practice status, or `unknown`.

        A missing id is not "no policy" — it is "this course does not declare
        it", which is the strict case. The caller gets `unknown` together with
        the reason, so a refusal can be explained instead of just issued.
        """
        if assignment_id is None:
            return "unknown", "assignment_id не задан: оцениваемость не установлена"
        entry = self.assignment(assignment_id)
        if entry is None:
            return "unknown", (
                "задание %r отсутствует в принятом контракте курса: "
                "оцениваемость не установлена, действует строгий режим" % assignment_id
            )
        return entry["assessment"], "принятый контракт курса"

    def assessment_rule_ref(self, assignment_id):
        entry = self.assignment(assignment_id)
        if entry is None:
            return None
        return "course.assignments[%s].assessment" % assignment_id

    def objectives_for_assignment(self, assignment_id):
        entry = self.assignment(assignment_id)
        return list(entry["objective_ids"]) if entry else []

    # -- graph -------------------------------------------------------------
    def objective(self, objective_id):
        for entry in self.track["objectives"]:
            if entry["objective_id"] == objective_id:
                return entry
        return None

    def module(self, module_id):
        for entry in self.track["modules"]:
            if entry["module_id"] == module_id:
                return entry
        return None

    def objective_ids(self):
        return [o["objective_id"] for o in self.track["objectives"]]

    def prerequisites_of(self, objective_id):
        entry = self.objective(objective_id)
        return list(entry["prerequisites"]) if entry else []

    def ordered_objectives(self):
        return topological_order(self.track)

    def next_required_objective(self, completed_ids=()):
        """The first required objective not yet completed, in track order."""
        done = set(completed_ids)
        for objective_id in self.ordered_objectives():
            entry = self.objective(objective_id)
            if entry.get("required") and objective_id not in done:
                return objective_id
        return None

    def unsatisfied_prerequisites(self, objective_id, demonstrated_ids=()):
        """Prerequisites of `objective_id` that are not demonstrated yet."""
        done = set(demonstrated_ids)
        return [p for p in self.prerequisites_of(objective_id) if p not in done]

    # -- materials ---------------------------------------------------------
    def is_readable_path(self, relative):
        """Whether the accepted contract allows reading this course-relative path.

        Exclusions win over allowances, and both are compared on normalised
        POSIX paths so `lessons/../teacher/x.md` cannot dodge the exclusion by
        spelling.
        """
        try:
            normalised = _normalise_relative(relative)
        except CourseError:
            return False

        excluded = self.contract.get("excluded_material_roots") or []
        for root in excluded:
            prefix = _normalise_relative(root)
            if normalised == prefix or normalised.startswith(prefix + "/"):
                return False

        allowed = self.contract["student_material_roots"] or []
        for root in allowed:
            prefix = _normalise_relative(root)
            if normalised == prefix or normalised.startswith(prefix + "/"):
                return True
        return False

    def describe(self):
        """A compact description for CLI output and tool results."""
        return {
            "course_id": self.course_id,
            "slug": self.slug,
            "title": self.contract["title"],
            "language": self.contract["language"],
            "revision": self.revision,
            "contract_hash": self.contract_hash,
            "track_hash": self.binding["accepted_track_hash"],
            "accepted_at": self.binding["accepted_at"],
            "acceptance_kind": self.binding["acceptance_kind"],
            "objectives": len(self.track["objectives"]),
            "modules": len(self.track["modules"]),
            "assignments": [
                {"assignment_id": a["assignment_id"], "assessment": a["assessment"]}
                for a in self.contract["assignments"]
            ],
            "contribution_enabled": bool(
                (self.contract.get("contribution") or {}).get("enabled")
            ),
            "licenses": self.contract.get("licenses"),
        }


def _normalise_relative(relative):
    """POSIX-style, no `.`/`..`, no leading slash."""
    text = str(relative or "").replace("\\", "/").strip()
    if not text:
        raise CourseError("COURSE_PATH_EMPTY", "пустой относительный путь")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise CourseError("COURSE_PATH_OUTSIDE", "абсолютный путь недопустим: %r" % text)
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise CourseError("COURSE_PATH_OUTSIDE", "выход за каталог в пути: %r" % text)
        parts.append(part)
    if not parts:
        raise CourseError("COURSE_PATH_EMPTY", "пустой относительный путь")
    return "/".join(parts)


# --------------------------------------------------------------------------
# Binding storage
# --------------------------------------------------------------------------

def binding_path(root, course_id):
    return paths.course_state_dir(root, course_id) / BINDING_FILENAME


def accepted_dir(root, contract_hash):
    base = Path(root) / ".botai" / ACCEPTED_DIRNAME
    return paths.ensure_within(base, base / contract_hash)


def load_binding(root, course_id):
    """Read an accepted binding, or None. Raises when the file is corrupt.

    A corrupt binding is not "no binding": treating it as absent would silently
    drop a course back to unknown-strict mode and the agent would tell the
    student their course is not accepted when it is.
    """
    path = binding_path(root, course_id)
    if not path.is_file():
        return None
    document = load_json(path)
    _validate(document, "binding")
    return document


def list_bindings(root):
    """Every accepted course in this workspace, sorted by course_id."""
    base = Path(root) / ".botai" / "courses"
    if not base.is_dir():
        return []
    found = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / BINDING_FILENAME
        if candidate.is_file():
            try:
                document = load_json(candidate)
                _validate(document, "binding")
            except CourseError:
                continue
            found.append(document)
    return found


def load_accepted(root, course_id):
    """The `AcceptedCourse` for a binding, read from the accepted snapshots.

    Material is read from the snapshot rather than the working tree: if the
    live `botai/course.json` is edited after acceptance, this must keep
    returning what was accepted, not what is on disk now.
    """
    binding = load_binding(root, course_id)
    if binding is None:
        raise CourseError(
            "COURSE_NOT_ACCEPTED",
            "курс %s не принят: условия обучения не установлены. "
            "Сначала выполните course-accept после просмотра course-inspect." % course_id,
        )
    directory = accepted_dir(root, binding["accepted_contract_hash"])
    contract = load_json(directory / "course.json")
    track = load_json(directory / "track.json")
    # Re-validate on read: the snapshot lives on disk and is therefore as
    # untrusted as any other file. Verifying the hash is what makes the stored
    # contract the same one a human accepted.
    _verify_snapshot(directory, binding)
    return AcceptedCourse(binding, contract, track, root)


def _verify_snapshot(directory, binding):
    contract_file = directory / "course.json"
    track_file = directory / "track.json"
    for path, expected, label in (
        (contract_file, binding["accepted_contract_hash"], "контракт"),
        (track_file, binding["accepted_track_hash"], "программа"),
    ):
        if not path.is_file():
            raise CourseError(
                "ACCEPTED_SNAPSHOT_MISSING",
                "принятый снимок %s отсутствует: %s" % (label, path),
            )
        actual = sha256_bytes(canonical_json(load_json(path)))
        if actual != expected:
            raise CourseError(
                "ACCEPTED_SNAPSHOT_CHANGED",
                "принятый снимок %s изменён: ожидался %s, получен %s. "
                "Доверенный снимок не редактируется на месте; примите новую "
                "ревизию курса отдельной операцией." % (label, expected, actual),
            )


def _write_snapshot(root, documents):
    """Write contract/track snapshots and return their canonical hashes.

    Written to a staging directory and then renamed, so a crash mid-acceptance
    cannot leave a hash that refers to a half-written file.
    """
    contract_hash = sha256_bytes(canonical_json(documents["course"]))
    track_hash = sha256_bytes(canonical_json(documents["track"]))
    directory = accepted_dir(root, contract_hash)
    driver = directory / "course.json"
    if driver.is_file():
        # Already snapshotted under this hash: reuse it. The bytes are
        # content-addressed, so identical content is the same snapshot.
        return contract_hash, track_hash

    directory.mkdir(parents=True, exist_ok=True)
    for name, document in (("course.json", documents["course"]),
                           ("track.json", documents["track"])):
        target = directory / name
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(document, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    return contract_hash, track_hash


def _snapshot_materials(root, course_dir, contract, revision_id):
    """Copy readable material into `.botai/materials/<course_id>/<revision>/`.

    Only roots the accepted contract allows are copied, and exclusions are
    applied first. The point is that the text the agent cites is the text that
    was accepted — a lesson edited in the working tree afterwards does not
    silently become the course.
    """
    root = Path(root)
    allowed = [_normalise_relative(r) for r in contract["student_material_roots"]]
    excluded = [_normalise_relative(r) for r in (contract.get("excluded_material_roots") or [])]

    def readable(rel):
        for prefix in excluded:
            if rel == prefix or rel.startswith(prefix + "/"):
                return False
        return any(rel == p or rel.startswith(p + "/") for p in allowed)

    materials_root = root / ".botai" / "materials"
    base = paths.ensure_within(
        materials_root,
        materials_root / paths.safe_name(contract["course_id"], kind="идентификатор курса")
        / paths.safe_name(revision_id, kind="идентификатор ревизии"),
    )
    copied = []

    for source in sorted(Path(course_dir).rglob("*")):
        if not source.is_file():
            continue
        try:
            relative = source.relative_to(course_dir).as_posix()
        except ValueError:
            continue
        if not readable(relative):
            continue
        # Never follow a link out of the course: a symlinked lesson could point
        # at a credential file and would then be copied into the snapshot.
        if source.is_symlink():
            continue
        target = paths.ensure_within(base, base / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        target.write_bytes(data)
        copied.append({"path": relative, "sha256": sha256_bytes(data), "byte_size": len(data)})

    # Stored workspace-relative: the binding travels between machines and an
    # absolute path would be wrong on the next one, and inside the schema.
    #
    # `ensure_within` resolves symlinks while `root` may be given unresolved
    # (on Windows `%TEMP%` is a short-name path such as `NONHUM~1`, which
    # resolve() rewrites). Compare against the resolved root, not the argument,
    # or this raises for a path that is genuinely inside the workspace.
    base_prefix = base.relative_to(root.resolve()).as_posix()
    return base_prefix, copied


def detect_revision(course_dir):
    """The course revision, or an honest local snapshot.

    A Git OID is only reported when the course directory is the root of its own
    repository — a course cloned *inside* another repository must not inherit
    that repository's commit as if it described the course.
    """
    import subprocess

    course_dir = Path(course_dir).resolve()
    env = dict(os.environ)
    # An inherited GIT_DIR would make git answer about some other repository.
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"

    def run(args):
        try:
            result = subprocess.run(
                ["git", "-C", str(course_dir)] + args,
                capture_output=True, text=True, timeout=20, env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    toplevel = run(["rev-parse", "--show-toplevel"])
    if toplevel and Path(toplevel).resolve() == course_dir:
        oid = run(["rev-parse", "HEAD"])
        if oid and re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", oid):
            return {"kind": "git", "id": oid}

    digest = hash_tree(course_dir)
    return {"kind": "local_snapshot", "id": "local-%s" % digest}


def hash_tree(directory):
    """A deterministic digest of a directory's file contents.

    Used only for a course with no Git history of its own; it is a change
    detector, not a commit id, and it is named `local-<hash>` so nothing can
    mistake it for one.
    """
    directory = Path(directory)
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        if ".git" in path.parts:
            continue
        if path.is_symlink():
            continue
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def accept(root, course_dir, *, slug=None, source_url=None, teaching_remote=None,
           teaching_branch=None, contribution_remote=None, acceptance_kind=None,
           workspace_id=None, repository_root=None):
    """Accept the course contract in `course_dir` as the governing policy.

    This is a human operation. It is deliberately not reachable from the
    tutoring tool surface: a model that could accept a course could accept a
    contract that reclassifies a graded assignment as practice.
    """
    root = Path(root)
    course_dir = Path(course_dir)
    croot = find_course_root(course_dir)

    contract = load_json(croot / COURSE_CONTRACT_PATH)
    _validate(contract, "course")

    track_path = _safe_relative(croot, contract["track_path"], what="track_path")
    if not track_path.is_file():
        raise CourseError("TRACK_MISSING",
                          "программа курса отсутствует: %s" % contract["track_path"])
    track = load_json(track_path)
    _validate(track, "track")

    problems = check_track(track)
    if problems:
        raise CourseError(
            "TRACK_INVALID",
            "программа курса не принята: %s"
            % "; ".join(p["message_ru"] for p in problems[:6]),
        )

    # Every declared assignment file must exist. Accepting a contract that
    # points at missing files would produce a course whose graded rules can
    # never be checked against anything.
    missing = []
    for assignment in contract["assignments"]:
        target = _safe_relative(croot, assignment["path"], what="assignment path")
        if not target.is_file():
            missing.append(assignment["path"])
    if missing:
        raise CourseError(
            "ASSIGNMENT_FILE_MISSING",
            "объявленные задания отсутствуют: %s" % ", ".join(missing),
        )

    course_id = contract["course_id"]
    safe_slug = paths.validate_slug(slug or course_id, allow_legacy=True)
    if repository_root is None:
        repository_root = course_dir.relative_to(root).as_posix() \
            if _is_relative_to(course_dir, root) else course_dir.name

    revision = detect_revision(croot)
    revision["accepted_at"] = now_iso()

    contract_hash, track_hash = _write_snapshot(
        root, {"course": contract, "track": track}
    )
    material_snapshot, _copied = _snapshot_materials(
        root, croot, contract, revision["id"]
    )

    binding = {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "course_id": course_id,
        "slug": safe_slug,
        "legacy_path": None if safe_slug == (slug or course_id) else (slug or course_id),
        "workspace_id": workspace_id or _workspace_id(root),
        "repository_root": repository_root,
        "source_url": _strip_credentials(source_url) if source_url else None,
        "teaching_remote": teaching_remote,
        "teaching_branch": teaching_branch,
        "contribution_remote": contribution_remote,
        "accepted_revision": revision,
        "accepted_contract_hash": contract_hash,
        "accepted_track_hash": track_hash,
        "accepted_environment_hash": None,
        "accepted_at": now_iso(),
        "acceptance_kind": acceptance_kind
            or ("maintainer_manifest" if (croot / COURSE_CONTRACT_PATH).is_file()
                else "human_adapted_legacy"),
        "assessment_kinds": sorted({a["assessment"] for a in contract["assignments"]}),
        "material_snapshot": material_snapshot,
    }
    _validate(binding, "binding")

    target = binding_path(root, course_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(binding, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return binding


def _is_relative_to(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _strip_credentials(url):
    """Remove `user:token@` from a URL before it is stored or shown.

    A source URL is written into the binding, which is readable by the agent;
    an embedded token there would leak into logs and exports.
    """
    text = str(url)
    return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]*@", r"\1", text)


def _workspace_id(root):
    """The workspace identity, created once and then stable."""
    meta_path = Path(root) / ".botai" / "workspace.json"
    if meta_path.is_file():
        try:
            document = json.loads(meta_path.read_text(encoding="utf-8"))
            if document.get("workspace_id"):
                return document["workspace_id"]
        except (OSError, ValueError):
            pass
    identifier = store_mod.new_id()
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"schema_version": schemas.SUPPORTED_MAJOR,
                    "workspace_id": identifier,
                    "created_at": now_iso()},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    return identifier


# --------------------------------------------------------------------------
# Change detection between the accepted revision and what is on disk
# --------------------------------------------------------------------------

def diff_against_disk(root, course_dir):
    """What changed in the course contract since it was accepted.

    This is the check behind "a student branch cannot soften the grading
    policy": the caller sees the difference and a human decides, rather than the
    running policy quietly following the working tree.
    """
    course_dir = Path(course_dir)
    croot = find_course_root(course_dir)
    result = {
        "schema_version": schemas.SUPPORTED_MAJOR,
        "accepted": False,
        "contract_changed": False,
        "track_changed": False,
        "assessment_changed": [],
        "missing_from_contract": [],
        "new_assignments": [],
        "problems": [],
        "on_disk_revision": None,
        "accepted_revision": None,
    }

    try:
        accepted = load_accepted(root, _course_id_for_dir(root, course_dir))
    except CourseError as e:
        result["problems"].append({"code": e.code, "message_ru": e.message})
        return result

    result["accepted"] = True
    result["accepted_revision"] = accepted.revision
    result["on_disk_revision"] = detect_revision(croot)

    contract_file = croot / COURSE_CONTRACT_PATH
    if not contract_file.is_file():
        result["problems"].append({
            "code": "COURSE_CONTRACT_MISSING",
            "message_ru": "в рабочей копии курса больше нет %s" % COURSE_CONTRACT_PATH,
        })
        return result

    # An unreadable or invalid working copy is itself the finding. Raising here
    # would turn "your course file is broken" into a traceback, which tells the
    # user nothing about which file or what is wrong with it.
    try:
        on_disk = load_json(contract_file)
        _validate(on_disk, "course")
    except CourseError as e:
        result["problems"].append({"code": e.code, "message_ru": e.message})
        result["contract_changed"] = True
        return result

    accepted_hashes = {h for h in (accepted.contract_hash,)}
    on_disk_hash = sha256_bytes(canonical_json(on_disk))
    result["contract_changed"] = on_disk_hash not in accepted_hashes

    accepted_by_id = {a["assignment_id"]: a for a in accepted.contract["assignments"]}
    disk_by_id = {a["assignment_id"]: a for a in on_disk["assignments"]}

    for assignment_id, entry in disk_by_id.items():
        previous = accepted_by_id.get(assignment_id)
        if previous is None:
            result["new_assignments"].append({
                "assignment_id": assignment_id,
                "assessment": entry["assessment"],
            })
            continue
        for field in ASSESSMENT_FIELDS:
            if previous.get(field) != entry.get(field):
                result["assessment_changed"].append({
                    "assignment_id": assignment_id,
                    "field": field,
                    "accepted": previous.get(field),
                    "on_disk": entry.get(field),
                })

    for assignment_id in accepted_by_id:
        if assignment_id not in disk_by_id:
            result["missing_from_contract"].append(assignment_id)

    track_file = _safe_relative(croot, on_disk["track_path"], what="track_path")
    if track_file.is_file():
        track_hash = sha256_bytes(canonical_json(load_json(track_file)))
        result["track_changed"] = track_hash != accepted.binding["accepted_track_hash"]
    else:
        result["track_changed"] = True
        result["problems"].append({
            "code": "TRACK_MISSING",
            "message_ru": "программа курса отсутствует в рабочей копии",
        })

    return result


def _course_id_for_dir(root, course_dir):
    """Find which accepted course a directory belongs to.

    Matched by `repository_root`, never by the directory name: a name is not an
    identity, and two courses may live under different paths with the same
    basename.
    """
    try:
        relative = Path(course_dir).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(course_dir).name
    for binding in list_bindings(root):
        if binding.get("repository_root") == relative:
            return binding["course_id"]
    return Path(course_dir).name
