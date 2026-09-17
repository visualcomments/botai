# -*- coding: utf-8 -*-
"""First contribution: preparing work a student will publish themselves.

The rule that shapes this module is that **the agent never publishes**. It reads
Git state, explains what a diff contains, checks a change against a rubric, and
writes a draft description. The student runs `git commit`, `git push` and opens
the pull request. There is no function here that stages, commits or pushes, and
that absence is the guarantee — not a note in a prompt.

Two distinctions the record has to keep, because blurring them is how a
contribution log starts lying:

* **What the student reported vs what was observed.** `learner_report` and
  `remote_api` are different values, and a self-report never gets promoted.
  "I opened the PR" is stored as a report; only a fetched PR state is accepted.
* **Which revision the checks describe.** Checks are bound to a `diff_hash`. A
  force-push or an amended commit changes it, and the older checks stop
  applying rather than quietly transferring to the new revision.

Read-only Git access is itself defended: a trusted absolute executable, hooks
and external diff drivers disabled, no pager, bounded output, and no
arbitrary argument passthrough. A `git log --format=%x1b[…` is a way to drive a
terminal, and a `textconv` filter is a way to run a program; both are switched
off rather than trusted.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import schemas

SCHEMA_VERSION = 2

STATES = (
    "DISCOVERY", "TASK_SELECTED", "GIT_PRACTICE", "WORKING", "LOCAL_CHECKED",
    "DRAFT_READY", "AWAITING_STUDENT_PUBLICATION", "SUBMITTED", "UNDER_REVIEW",
    "REVISED", "ACCEPTED", "CLOSED", "PRIVATE_PACKAGE_READY", "PRIVATELY_SUBMITTED",
)

# Legal transitions. Anything else is refused with a reason: a contribution
# that jumps to ACCEPTED without a SUBMITTED would be a record claiming a pull
# request nobody opened.
TRANSITIONS = {
    ("DISCOVERY", "TASK_SELECTED"),
    ("DISCOVERY", "CLOSED"),
    ("TASK_SELECTED", "GIT_PRACTICE"),
    ("TASK_SELECTED", "WORKING"),          # skipping rehearsal after a diagnostic
    ("TASK_SELECTED", "CLOSED"),
    ("GIT_PRACTICE", "WORKING"),
    ("GIT_PRACTICE", "CLOSED"),
    ("WORKING", "LOCAL_CHECKED"),
    ("WORKING", "CLOSED"),
    ("LOCAL_CHECKED", "DRAFT_READY"),
    ("LOCAL_CHECKED", "WORKING"),          # a check failed; back to the edit
    ("LOCAL_CHECKED", "CLOSED"),
    ("DRAFT_READY", "AWAITING_STUDENT_PUBLICATION"),
    ("DRAFT_READY", "PRIVATE_PACKAGE_READY"),
    ("DRAFT_READY", "WORKING"),            # the diff changed after the draft
    ("DRAFT_READY", "CLOSED"),
    ("AWAITING_STUDENT_PUBLICATION", "SUBMITTED"),
    ("AWAITING_STUDENT_PUBLICATION", "WORKING"),
    ("AWAITING_STUDENT_PUBLICATION", "CLOSED"),
    ("PRIVATE_PACKAGE_READY", "PRIVATELY_SUBMITTED"),
    ("PRIVATE_PACKAGE_READY", "WORKING"),
    ("PRIVATE_PACKAGE_READY", "CLOSED"),
    ("SUBMITTED", "UNDER_REVIEW"),
    ("SUBMITTED", "ACCEPTED"),
    ("SUBMITTED", "CLOSED"),
    ("SUBMITTED", "WORKING"),              # a reopened or force-pushed change
    ("PRIVATELY_SUBMITTED", "ACCEPTED"),
    ("PRIVATELY_SUBMITTED", "CLOSED"),
    ("UNDER_REVIEW", "REVISED"),
    ("UNDER_REVIEW", "ACCEPTED"),
    ("UNDER_REVIEW", "CLOSED"),
    ("REVISED", "SUBMITTED"),
    ("REVISED", "ACCEPTED"),
    ("REVISED", "CLOSED"),
    ("ACCEPTED", "CLOSED"),
}

# States where the change exists locally and a diff can be observed.
DIFF_STATES = {"WORKING", "LOCAL_CHECKED", "DRAFT_READY",
               "AWAITING_STUDENT_PUBLICATION", "SUBMITTED", "UNDER_REVIEW",
               "REVISED", "PRIVATE_PACKAGE_READY", "PRIVATELY_SUBMITTED"}

PUBLICATION_CHOICES = ("undecided", "public_pr", "private_package")
RIGHTS_STATUSES = ("unknown", "reviewed_allowed", "restricted", "blocked")
OBSERVATION_SOURCES = ("local_git", "remote_api", "human_teacher", "learner_report")

# Git is invoked through the environment and a fixed argument list. These are
# passed on *every* call so a repository's own configuration cannot change what
# a read-only command does.
BASE_GIT_ARGS = (
    "-c", "core.hooksPath=",             # no hooks: a commit-msg hook is code
    "-c", "core.pager=cat",              # no pager: it can wait forever
    "-c", "core.editor=true",
    "-c", "diff.external=",              # no external diff driver
    "-c", "diff.trustExitCode=false",
    "-c", "core.fsmonitor=false",
    "-c", "safe.directory=*",            # the caller already proved ownership
    "--no-pager",
)

# Subcommands that always write to the repository or reach the network.
FORBIDDEN_GIT_ARGS = {
    "add", "commit", "push", "pull", "fetch", "merge", "rebase", "reset",
    "checkout", "switch", "restore", "clean", "rm", "mv", "stash", "tag",
    "filter-branch", "gc", "submodule", "prune", "update-ref", "symbolic-ref",
    "apply", "am", "cherry-pick", "revert", "worktree", "sparse-checkout",
}

# Subcommands that are read-only only in their bare form: `git remote -v` lists
# remotes, while `git remote add` configures one, and `git branch` lists while
# `git branch -D` deletes. A blanket ban on the subcommand would make ordinary
# observation impossible, so the *arguments* are checked instead.
READ_ONLY_UNLESS_FLAGGED = {
    "remote": {"add", "remove", "rm", "rename", "set-head", "set-branches",
               "set-url", "prune", "update"},
    "branch": {"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C",
               "--copy", "--set-upstream-to", "-u", "--unset-upstream",
               "--edit-description"},
    "config": {"--add", "--unset", "--unset-all", "--replace-all", "--edit",
               "-e", "--rename-section", "--remove-section"},
    "tag": {"-d", "--delete", "-a", "-s", "-f", "--force"},
}


class ContributionError(RuntimeError):
    def __init__(self, code, message, *, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def canonical_oid(text):
    """A full Git OID, or None. Short ids are never stored."""
    value = (text or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", value):
        return value
    return None


# --------------------------------------------------------------------------
# Read-only Git observation
# --------------------------------------------------------------------------

def git_executable():
    """An absolute path to a trusted git, or None.

    Absolute because a relative name is resolved through PATH, and PATH is
    something a course's own environment can change.
    """
    found = shutil.which("git")
    return str(Path(found).resolve()) if found else None


def git_env():
    """A minimal environment for a read-only git call."""
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_TERMINAL_PROMPT": "0",      # never wait for credentials
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "LC_ALL": "C",
        "LANG": "C",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC"):
            if os.environ.get(name):
                env[name] = os.environ[name]
    return env


def run_git(repo, args, *, timeout=30, max_bytes=262144, check_repo=True):
    """Run a read-only git command in `repo`.

    Refuses any subcommand in `FORBIDDEN_GIT_ARGS`, so "read-only" is enforced
    by the call rather than promised by the caller. Output is bounded and
    stripped of terminal escape sequences: a branch name can contain them, and
    the result is shown to a person.
    """
    tokens = [str(a).strip() for a in args]
    for token in tokens:
        if token in FORBIDDEN_GIT_ARGS:
            raise ContributionError(
                "GIT_ARGUMENT_FORBIDDEN",
                "команда %r изменяет репозиторий или ходит в сеть: "
                "наблюдение ограничено чтением" % token,
            )

    # A read-only-in-bare-form subcommand is checked against its mutating
    # arguments: `git remote -v` is observation, `git remote add` is not.
    if tokens and tokens[0] in READ_ONLY_UNLESS_FLAGGED:
        forbidden = READ_ONLY_UNLESS_FLAGGED[tokens[0]]
        for token in tokens[1:]:
            if token in forbidden:
                raise ContributionError(
                    "GIT_ARGUMENT_FORBIDDEN",
                    "аргумент %r у команды %r изменяет репозиторий: "
                    "наблюдение ограничено чтением" % (token, tokens[0]),
                )

    executable = git_executable()
    if executable is None:
        raise ContributionError("GIT_MISSING", "git не найден в PATH")

    repo = Path(repo)
    if check_repo and not (repo / ".git").exists():
        # A `.git` file (worktree) also counts; `exists()` covers both.
        raise ContributionError(
            "NOT_A_REPOSITORY",
            "%s не является корнем git-репозитория: учебный курс внутри "
            "другого репозитория нельзя принять за отдельный" % repo,
        )

    try:
        completed = subprocess.run(
            [executable, *BASE_GIT_ARGS, *[str(a) for a in args]],
            cwd=str(repo), capture_output=True, timeout=timeout,
            env=git_env(), shell=False, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise ContributionError("GIT_TIMEOUT",
                                "git не ответил за %d с" % timeout)
    except OSError as e:
        raise ContributionError("GIT_FAILED", "не удалось запустить git: %s" % e)

    out = (completed.stdout or b"")[:max_bytes]
    err = (completed.stderr or b"")[:max_bytes]

    def clean(data):
        text = data.decode("utf-8", errors="replace")
        # Terminal escape sequences: a branch name or file name could contain
        # them, and this text is printed for a person to read.
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\x1b", "")

    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": clean(out),
        "stderr": clean(err),
        "truncated": len(completed.stdout or b"") > max_bytes,
    }


def is_repository_root(path):
    """Whether `path` is the root of its own repository.

    Checked by comparing `--show-toplevel` with the directory: a course cloned
    *inside* another repository answers `--is-inside-work-tree` truthfully while
    belonging to the outer one, which is how a course gets mistaken for a
    repository it is not.
    """
    path = Path(path).resolve()
    try:
        result = run_git(path, ["rev-parse", "--show-toplevel"], check_repo=False)
    except ContributionError:
        return False, None
    if not result["ok"]:
        return False, None
    toplevel = result["stdout"].strip()
    if not toplevel:
        return False, None
    try:
        same = Path(toplevel).resolve() == path
    except OSError:
        return False, None
    return same, toplevel


def observe(repo):
    """A read-only snapshot of the repository's state.

    Returns branch, HEAD, dirty state, staged/unstaged/untracked files and the
    configured remotes. Nothing here changes the repository.
    """
    repo = Path(repo)
    root_ok, toplevel = is_repository_root(repo)
    if not root_ok:
        return {
            "is_repository_root": False,
            "toplevel": toplevel,
            "reason_ru": "каталог не является корнем собственного репозитория",
        }

    head = run_git(repo, ["rev-parse", "HEAD"])
    branch = run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    status = run_git(repo, ["status", "--porcelain=v1", "--untracked-files=all"])
    remotes = run_git(repo, ["remote", "-v"])

    staged, unstaged, untracked = [], [], []
    for line in status["stdout"].splitlines():
        if len(line) < 3:
            continue
        code, path = line[:2], line[3:].strip()
        if code == "??":
            untracked.append(path)
            continue
        if code[0] not in (" ", "?"):
            staged.append(path)
        if len(code) > 1 and code[1] not in (" ", "?"):
            unstaged.append(path)

    remote_map = {}
    for line in remotes["stdout"].splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remote_map.setdefault(parts[0], {"fetch": None, "push": None})
            if parts[2] == "(fetch)":
                remote_map[parts[0]]["fetch"] = parts[1]
            elif parts[2] == "(push)":
                remote_map[parts[0]]["push"] = parts[1]

    return {
        "is_repository_root": True,
        "toplevel": toplevel,
        "branch": branch["stdout"].strip() or None,
        "head_oid": canonical_oid(head["stdout"]),
        "dirty": bool(staged or unstaged or untracked),
        "staged": sorted(staged),
        "unstaged": sorted(unstaged),
        "untracked": sorted(untracked),
        "remotes": remote_map,
    }


def normalise_remote(url):
    """A remote URL in a comparable form, without its credentials.

    `https://user:token@host/o/r.git`, `git@host:o/r.git` and
    `https://host/o/r` all describe the same project. Comparing raw strings
    would report a fork as a different project merely because it was added over
    SSH, and comparing with tokens intact would put a secret in a comparison
    result that gets logged.
    """
    text = (url or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)
    text = re.sub(r"^[^/@]+@", "", text)          # strip user@ or user:token@
    text = text.replace(":", "/", 1) if ":" in text.split("/")[0] else text
    text = text.replace(":", "/")
    text = re.sub(r"\.git$", "", text)
    text = text.strip("/").lower()
    return text


def find_forks(remotes, source_url):
    """Which remote, if any, is the student's fork of the course.

    Matched by normalised URL equality, never by a name that looks similar: a
    remote called `origin` proves nothing, and a repository named like the
    course is not the course.
    """
    wanted = normalise_remote(source_url)
    for name, urls in (remotes or {}).items():
        for kind in ("fetch", "push"):
            if normalise_remote(urls.get(kind)) == wanted:
                return {"name": name, "kind": kind,
                        "role": "teaching",
                        "reason_ru": "совпадает с исходным адресом курса"}
    return None


# --------------------------------------------------------------------------
# The diff a contribution is about
# --------------------------------------------------------------------------

MAX_DIFF_BYTES = 2 * 1024 * 1024


def working_diff(repo, *, base=None, max_bytes=MAX_DIFF_BYTES):
    """The change as the repository currently sees it, plus its hash.

    The hash is what binds checks to a revision. It is computed over the
    normalised diff text, so the same change observed twice gives the same
    value and an amended commit does not.
    """
    observation = observe(repo)
    if not observation.get("is_repository_root"):
        raise ContributionError("NOT_A_REPOSITORY",
                                observation.get("reason_ru") or "не репозиторий")

    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames"]
    if base:
        args.append(str(base))
    diff = run_git(repo, args, max_bytes=max_bytes)

    staged_args = ["diff", "--cached", "--no-color", "--no-ext-diff",
                   "--no-textconv", "--no-renames"]
    staged = run_git(repo, staged_args, max_bytes=max_bytes)

    combined = diff["stdout"] + "\n" + staged["stdout"]
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()

    return {
        "diff": diff["stdout"],
        "staged_diff": staged["stdout"],
        "diff_hash": digest,
        "base_oid": canonical_oid(base) if base else None,
        "head_oid": observation["head_oid"],
        "branch": observation["branch"],
        "files_changed": sorted(set(
            _paths_from_diff(combined)
        )),
        "truncated": diff["truncated"] or staged["truncated"],
        "observation": observation,
    }


def _paths_from_diff(diff_text):
    """File names mentioned by a unified diff, without trusting the diff."""
    paths = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                candidate = parts[3]
                paths.append(candidate[2:] if candidate.startswith("b/") else candidate)
    return paths


def changed_since(repo, recorded_hash):
    """Whether the observed change differs from the recorded one."""
    current = working_diff(repo)
    return current["diff_hash"] != recorded_hash, current


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------

def can_transition(current, target):
    if current not in STATES:
        return False, "STATE_UNKNOWN"
    if target not in STATES:
        return False, "TARGET_UNKNOWN"
    if current == target:
        return False, "STATE_UNCHANGED"
    if (current, target) in TRANSITIONS:
        return True, "ok"
    return False, "CONTRIBUTION_TRANSITION"


def require_transition(current, target):
    ok, reason = can_transition(current, target)
    if not ok:
        raise ContributionError(
            reason,
            "переход %s → %s недопустим: %s" % (current, target, reason),
        )
    return True


def new_contribution(*, contribution_id, session_id, course_id, role,
                     task_ref=None, publication_choice="undecided",
                     rights_status="unknown", ai_disclosure_required=False,
                     repository_root=None, clock=None):
    """Open a contribution record in DISCOVERY."""
    if role not in ("expert", "researcher", "developer"):
        raise ContributionError("ROLE_UNKNOWN", "неизвестная роль: %r" % role)
    if publication_choice not in PUBLICATION_CHOICES:
        raise ContributionError("PUBLICATION_CHOICE_UNKNOWN",
                                "неизвестный путь сдачи: %r" % publication_choice)

    return {
        "schema_version": SCHEMA_VERSION,
        "contribution_id": contribution_id,
        "session_id": session_id,
        "course_id": course_id,
        "role": role,
        "state": "DISCOVERY",
        "task_ref": task_ref,
        "repository_root": repository_root,
        "base_oid": None,
        "head_oid": None,
        "diff_hash": None,
        "branch": None,
        "checks": [],
        "rights_status": rights_status,
        "ai_disclosure": {
            "required": bool(ai_disclosure_required),
            "text": "",
            # Never set from here: only a human channel may confirm it, and a
            # model proposal arriving with `true` must not authorise anything.
            "confirmed_by_learner": False,
        },
        "publication_choice": publication_choice,
        "external_ref": None,
        "observation_source": None,
        "observation_note": None,
        "created_at": _now(clock),
        "updated_at": None,
    }


def _now(clock=None):
    from datetime import datetime, timezone
    moment = clock() if clock else datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def select_task(document, *, task_ref, role=None, rights_status=None, clock=None):
    """DISCOVERY → TASK_SELECTED, recording what makes the task admissible."""
    require_transition(document["state"], "TASK_SELECTED")
    if not task_ref:
        raise ContributionError(
            "TASK_REF_MISSING",
            "без ссылки или описания задачи нельзя утверждать, что задача выбрана",
        )
    updated = dict(document)
    updated["state"] = "TASK_SELECTED"
    updated["task_ref"] = task_ref
    if role:
        if role not in ("expert", "researcher", "developer"):
            raise ContributionError("ROLE_UNKNOWN", "неизвестная роль: %r" % role)
        updated["role"] = role
    if rights_status:
        if rights_status not in RIGHTS_STATUSES:
            raise ContributionError("RIGHTS_STATUS_UNKNOWN",
                                    "неизвестный статус прав: %r" % rights_status)
        updated["rights_status"] = rights_status
    updated["updated_at"] = _now(clock)
    return updated


def record_local_check(document, *, repo, checks, observed_at=None, clock=None):
    """WORKING → LOCAL_CHECKED, binding the checks to a diff hash.

    An empty `checks` list is refused. "We checked it" with nothing recorded is
    exactly the claim this state exists to make checkable, and a rubric whose
    every criterion is non-programmatic must still list those criteria
    explicitly with `not_applicable`.
    """
    require_transition(document["state"], "LOCAL_CHECKED")
    if not checks:
        raise ContributionError(
            "CHECKLIST_EMPTY",
            "для перехода в LOCAL_CHECKED нужен хотя бы один проверенный пункт "
            "либо явно записанные непрограммные проверки роли",
        )

    snapshot = working_diff(repo)
    recorded = []
    for entry in checks:
        status = entry.get("status")
        if status not in ("pass", "fail", "skipped", "not_applicable"):
            raise ContributionError("CHECK_STATUS_UNKNOWN",
                                    "неизвестный статус проверки: %r" % status)
        recorded.append({
            "check_id": entry["check_id"],
            "status": status,
            "observed": (entry.get("observed") or "")[:2000] or None,
            "diff_hash": snapshot["diff_hash"],
            "note_ru": (entry.get("note_ru") or "")[:500] or None,
        })

    failed = [c["check_id"] for c in recorded if c["status"] == "fail"]
    if failed:
        raise ContributionError(
            "CHECKLIST_HAS_FAILURES",
            "проверки не пройдены (%s): переход в LOCAL_CHECKED означает, что "
            "обязательные проверки выполнены" % ", ".join(failed),
        )

    updated = dict(document)
    updated["state"] = "LOCAL_CHECKED"
    updated["checks"] = recorded
    updated["diff_hash"] = snapshot["diff_hash"]
    updated["head_oid"] = snapshot["head_oid"]
    updated["base_oid"] = snapshot["base_oid"]
    updated["branch"] = snapshot["branch"]
    updated["updated_at"] = _now(clock)
    return updated, snapshot


def draft_ready(document, *, description, self_review, publication_choice=None,
                clock=None):
    """LOCAL_CHECKED → DRAFT_READY, with the disclosure the project requires."""
    require_transition(document["state"], "DRAFT_READY")
    if not description or not description.strip():
        raise ContributionError("DRAFT_EMPTY", "черновик описания пуст")
    if not self_review or not self_review.strip():
        raise ContributionError(
            "SELF_REVIEW_MISSING",
            "черновик без самостоятельного просмотра diff не принимается: "
            "ученик должен прочитать собственное изменение",
        )

    disclosure = dict(document.get("ai_disclosure") or {})
    if disclosure.get("required") and not (disclosure.get("text") or "").strip():
        raise ContributionError(
            "AI_DISCLOSURE_MISSING",
            "проект требует раскрытия помощи ИИ: без текста раскрытия черновик "
            "не готов",
        )

    updated = dict(document)
    updated["state"] = "DRAFT_READY"
    updated["observation_note"] = description[:2000]
    if publication_choice:
        if publication_choice not in PUBLICATION_CHOICES:
            raise ContributionError("PUBLICATION_CHOICE_UNKNOWN",
                                    "неизвестный путь сдачи: %r" % publication_choice)
        updated["publication_choice"] = publication_choice
    updated["updated_at"] = _now(clock)
    return updated


def set_disclosure_text(document, text, *, confirmed_by_learner=False, clock=None):
    """Record the disclosure text.

    `confirmed_by_learner` defaults to False and is only ever set from a human
    channel. A caller that passes True because a model said so has not confirmed
    anything, which is why the field is separate from the text.
    """
    updated = dict(document)
    disclosure = dict(updated.get("ai_disclosure") or {})
    disclosure["text"] = (text or "")[:4000]
    if confirmed_by_learner:
        disclosure["confirmed_by_learner"] = True
    updated["ai_disclosure"] = disclosure
    updated["updated_at"] = _now(clock)
    return updated


def mark_awaiting_publication(document, *, choice=None, clock=None):
    """DRAFT_READY → the student's own publication step."""
    choice = choice or document.get("publication_choice")

    if choice == "private_package":
        require_transition(document["state"], "PRIVATE_PACKAGE_READY")
        target = "PRIVATE_PACKAGE_READY"
    else:
        require_transition(document["state"], "AWAITING_STUDENT_PUBLICATION")
        target = "AWAITING_STUDENT_PUBLICATION"

    updated = dict(document)
    updated["state"] = target
    updated["publication_choice"] = choice
    updated["updated_at"] = _now(clock)
    return updated


def observe_submission(document, *, external_ref=None, source, note=None,
                       repo=None, clock=None):
    """Record what happened after the student published — never assume it.

    `source="learner_report"` stores a report and moves nothing further: the
    design keeps a self-report from becoming verification. Only
    `remote_api`, `human_teacher` or `local_git` may advance past SUBMITTED.
    """
    if source not in OBSERVATION_SOURCES:
        raise ContributionError("OBSERVATION_SOURCE_UNKNOWN",
                                "неизвестный источник наблюдения: %r" % source)

    if document["state"] in ("AWAITING_STUDENT_PUBLICATION", "REVISED"):
        require_transition(document["state"], "SUBMITTED")
        target = "SUBMITTED"
    elif document["state"] == "PRIVATE_PACKAGE_READY":
        require_transition(document["state"], "PRIVATELY_SUBMITTED")
        target = "PRIVATELY_SUBMITTED"
    else:
        target = document["state"]

    updated = dict(document)
    updated["state"] = target
    updated["observation_source"] = source
    updated["observation_note"] = (note or "")[:2000] or None
    if external_ref:
        updated["external_ref"] = external_ref[:1000]

    # The diff is re-observed: a force-push or an amendment between the draft
    # and the submission invalidates the checks that were made against the old
    # revision, and carrying them over silently would attach verified status to
    # a revision nobody checked.
    if repo is not None and updated.get("diff_hash"):
        current = working_diff(repo)
        if current["diff_hash"] != updated["diff_hash"]:
            updated["checks"] = []
            updated["diff_hash"] = current["diff_hash"]
            updated["head_oid"] = current["head_oid"]
            updated["observation_note"] = (
                (updated["observation_note"] or "")
                + " | содержимое изменилось после проверки: прежние проверки "
                  "недействительны"
            )[:2000]

    updated["updated_at"] = _now(clock)
    return updated


def record_acceptance(document, *, source, evidence_note=None, clock=None):
    """SUBMITTED/PRIVATELY_SUBMITTED → ACCEPTED, from an observation only.

    Refuses a `learner_report`: "they merged it" is not a merge, and a record
    that accepts one makes the log worthless for anyone reading it later.
    """
    if source not in OBSERVATION_SOURCES:
        raise ContributionError("OBSERVATION_SOURCE_UNKNOWN",
                                "неизвестный источник наблюдения: %r" % source)
    if source == "learner_report":
        raise ContributionError(
            "ACCEPTANCE_REQUIRES_OBSERVATION",
            "принятие подтверждается наблюдением репозитория или преподавателя, "
            "а не сообщением ученика: «я отправил» не равно «принято»",
        )
    if document["state"] not in ("SUBMITTED", "PRIVATELY_SUBMITTED"):
        raise ContributionError(
            "ACCEPTANCE_FROM_WRONG_STATE",
            "принятие возможно только из SUBMITTED или PRIVATELY_SUBMITTED, "
            "сейчас %s" % document["state"],
        )

    updated = dict(document)
    updated["state"] = "ACCEPTED"
    updated["observation_source"] = source
    updated["observation_note"] = (evidence_note or "")[:2000] or updated.get("observation_note")
    updated["updated_at"] = _now(clock)
    return updated


def validate(document):
    try:
        return schemas.validate(document, "contribution")
    except schemas.SchemaError as e:
        raise ContributionError(e.code, e.message)


# --------------------------------------------------------------------------
# Draft text the student reviews and publishes
# --------------------------------------------------------------------------

CHECKLIST_ITEMS = (
    ("task", "Задача и зачем это изменение курсу"),
    ("what_changed", "Что сделано, с перечислением файлов"),
    ("sources", "Откуда данные и тексты, на каком правовом основании"),
    ("how_to_verify", "Как проверить: команда, ожидаемый результат, что уже проверено"),
    ("limitations", "Ограничения и что ещё не проверено"),
    ("ai_disclosure", "Вклад ИИ, без присвоения авторства"),
    ("review_replies", "Ответы на замечания рецензента"),
    ("no_secrets", "Нет секретов, персональных данных и закрытых материалов"),
    ("rubric", "Ссылка на rubric, если публикация связана с оцениваемой работой"),
)


def render_draft(document, *, snapshot=None, course_title=None, task_url=None):
    """The draft description and checklist the student edits and publishes.

    Written as a *draft* in the first line of the file itself: a generated
    description that reads like a finished pull request is one a student may
    paste without reading, and the whole point is that they read it.
    """
    lines = []
    lines.append("<!-- ЧЕРНОВИК. Составлен агентом как основа; ученик читает,")
    lines.append("     правит и публикует сам. Агент не создаёт коммитов,")
    lines.append("     не отправляет push и не открывает PR. -->")
    lines.append("")
    lines.append("# %s" % (task_url or document.get("task_ref") or "Вклад в курс"))
    lines.append("")
    lines.append("- курс: `%s`" % document["course_id"])
    lines.append("- роль: `%s`" % document["role"])
    lines.append("- путь сдачи: `%s`" % document["publication_choice"])
    lines.append("- статус прав: `%s`" % document["rights_status"])
    if document.get("branch"):
        lines.append("- ветка: `%s`" % document["branch"])
    if document.get("base_oid"):
        lines.append("- база: `%s`" % document["base_oid"])
    if document.get("head_oid"):
        lines.append("- голова: `%s`" % document["head_oid"])
    if document.get("diff_hash"):
        lines.append("- отпечаток изменения: `%s`" % document["diff_hash"][:32])
    lines.append("")

    if snapshot and snapshot.get("files_changed"):
        lines.append("## Изменённые файлы")
        lines.append("")
        for path in snapshot["files_changed"]:
            lines.append("- `%s`" % path)
        lines.append("")

    lines.append("## Проверки")
    lines.append("")
    if document.get("checks"):
        lines.append("| проверка | результат | наблюдение |")
        lines.append("|---|---|---|")
        for check in document["checks"]:
            lines.append("| `%s` | %s | %s |"
                         % (check["check_id"], check["status"],
                            (check.get("observed") or "—").replace("|", "\\|")[:120]))
    else:
        lines.append("Проверки не записаны. Публиковать без них нельзя.")
    lines.append("")

    lines.append("## Чек-лист перед публикацией")
    lines.append("")
    for key, text in CHECKLIST_ITEMS:
        marker = "x" if key in _satisfied_items(document) else " "
        lines.append("- [%s] %s" % (marker, text))
    lines.append("")

    disclosure = document.get("ai_disclosure") or {}
    lines.append("## Вклад ИИ")
    lines.append("")
    if disclosure.get("required"):
        lines.append("Проект требует раскрытия. Текст:")
        lines.append("")
        lines.append("> %s" % (disclosure.get("text") or "_(не заполнен)_"))
        lines.append("")
        lines.append("Подтверждено учеником: %s"
                     % ("да" if disclosure.get("confirmed_by_learner") else "нет"))
    else:
        lines.append("Раскрытие не требуется правилами проекта. Если помощь ИИ")
        lines.append("всё же упоминается, это делает ученик.")
    lines.append("")

    lines.append("## Что ученик выполняет сам")
    lines.append("")
    lines.append("```text")
    lines.append("проверить diff → выбрать конкретные файлы → проверить staged diff")
    lines.append("→ commit со своей идентичностью → push в свой форк → открыть PR")
    lines.append("```")
    lines.append("")
    lines.append("Агент показывает последствия, но не выполняет эти шаги.")
    lines.append("")
    return "\n".join(lines)


def _satisfied_items(document):
    """Which checklist items the record can actually support."""
    satisfied = set()
    if document.get("task_ref"):
        satisfied.add("task")
    if document.get("checks"):
        satisfied.add("how_to_verify")
    disclosure = document.get("ai_disclosure") or {}
    if not disclosure.get("required") or (disclosure.get("text") or "").strip():
        satisfied.add("ai_disclosure")
    return satisfied


def validate_draft(document, *, snapshot=None):
    """Whether a draft may be shown as ready for the student to publish.

    Returns `(ok, blocking, advisory)`. Blocking items are the ones that make
    publishing unsafe or dishonest; the rest are prompts for the student to
    fill in themselves.
    """
    blocking = []
    advisory = []

    if not document.get("checks"):
        blocking.append("нет записанных проверок: публиковать без них нельзя")
    if document.get("rights_status") in ("unknown", "restricted", "blocked"):
        blocking.append(
            "статус прав «%s»: право на публикацию не подтверждено"
            % document.get("rights_status")
        )
    disclosure = document.get("ai_disclosure") or {}
    if disclosure.get("required") and not (disclosure.get("text") or "").strip():
        blocking.append("проект требует раскрытия помощи ИИ, текст не заполнен")
    if document.get("diff_hash") and snapshot:
        if snapshot.get("diff_hash") != document["diff_hash"]:
            blocking.append(
                "изменение изменилось после проверки: прежние проверки "
                "относятся к другой ревизии"
            )

    for key, text in CHECKLIST_ITEMS:
        if key not in _satisfied_items(document) and key not in ("task",):
            advisory.append(text)

    return (not blocking), blocking, advisory
