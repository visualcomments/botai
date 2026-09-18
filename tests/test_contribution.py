#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the first-contribution flow.

Three properties carry the weight, and each one is a place where a teaching tool
would otherwise quietly overstate what it knows:

* **The agent never publishes.** There is no commit, push or PR function to
  call, and the read-only Git layer refuses the mutating forms of commands that
  are otherwise fine to read (`git remote -v` yes, `git remote add` no).
* **A report is not an observation.** A student saying "I opened the PR" is
  stored as `learner_report` and advances nothing further; only a repository
  observation or a teacher may reach ACCEPTED.
* **Checks belong to a revision.** Amending or force-pushing changes the diff
  hash, which invalidates the checks recorded against the old one instead of
  carrying them onto a revision nobody inspected.

Acceptance criteria: A10 (fork vs upstream roles), A11 (a changed diff
invalidates the earlier checks), A12 (a private package is equal to a public
PR), A30 (the assessed step stays with the student).

Run:
    python3 tests/test_contribution.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from botai_core import contribution as C  # noqa: E402

_passed = 0
_failures: list[str] = []

SESSION = "22222222-2222-4222-8222-222222222222"
CONTRIBUTION = "33333333-3333-4333-8333-333333333333"


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failures.append(name)
        print(f"  FAIL {name}  {detail}")


def expect_error(name, fn, code):
    try:
        fn()
    except C.ContributionError as e:
        check(name, e.code == code, f"код {e.code}, ожидался {code}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"неожиданное исключение: {type(e).__name__}: {e}")
    else:
        check(name, False, "ошибка не возникла")


class Repo:
    """A throwaway git repository with a deliberate staged/unstaged split."""

    def __init__(self, *, nested=False):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self._env = dict(os.environ)
        self._env.update({
            "GIT_AUTHOR_NAME": "Student", "GIT_AUTHOR_EMAIL": "student@example.org",
            "GIT_COMMITTER_NAME": "Student", "GIT_COMMITTER_EMAIL": "student@example.org",
        })
        self.git("init", "-q")
        self.git("config", "user.email", "student@example.org")
        self.git("config", "user.name", "Student")
        (self.path / "a.txt").write_text("первая строка\n", encoding="utf-8")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "initial")
        self.base_oid = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.path, capture_output=True,
                              text=True, env=self._env, timeout=30)

    def change(self, name="a.txt", text="первая строка\nвторая строка\n", stage=False):
        (self.path / name).write_text(text, encoding="utf-8")
        if stage:
            self.git("add", name)

    def commit_all(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def cleanup(self):
        self._tmp.cleanup()


def fresh():
    return C.new_contribution(contribution_id=CONTRIBUTION, session_id=SESSION,
                              course_id="minimal-diff", role="developer",
                              task_ref="https://example.org/issues/7")


# ---------------------------------------------------------------------------
# Role of the agent: read-only, and no publishing path at all
# ---------------------------------------------------------------------------
def test_there_is_no_publishing_function():
    """The guarantee is an absence: nothing here commits, pushes or opens a PR."""
    import inspect
    module_source = inspect.getsource(C)
    for name in ("def commit", "def push", "def open_pull_request", "def create_pr"):
        check("в модуле нет функции %s" % name.strip(), name not in module_source)
    for call in ('"commit"', '"push"'):
        # The words appear in FORBIDDEN_GIT_ARGS, which is the point: they are
        # refused, not implemented.
        check("%s встречается только как запрещённый аргумент" % call,
              call in module_source)


def test_read_only_commands_are_allowed_and_mutating_ones_refused():
    repo = Repo()
    try:
        allowed = [["remote", "-v"], ["branch"], ["status"], ["log", "--oneline"],
                   ["diff"], ["rev-parse", "HEAD"], ["config", "--get", "user.name"]]
        for args in allowed:
            try:
                C.run_git(repo.path, args)
            except C.ContributionError as e:
                check("разрешено чтение: %s" % " ".join(args), False, e.code)
            else:
                check("разрешено чтение: %s" % " ".join(args), True)

        refused = [["add", "."], ["commit", "-m", "x"], ["push"], ["fetch", "origin"],
                   ["merge", "main"], ["reset", "--hard"], ["checkout", "-b", "x"],
                   ["remote", "add", "fork", "url"], ["branch", "-D", "main"],
                   ["config", "--add", "a", "b"], ["submodule", "update"],
                   ["clean", "-fd"], ["stash"]]
        for args in refused:
            expect_error("отклонено изменение: %s" % " ".join(args),
                         lambda a=args: C.run_git(repo.path, a),
                         "GIT_ARGUMENT_FORBIDDEN")
    finally:
        repo.cleanup()


def test_arbitrary_git_arguments_are_not_forwarded():
    """A caller cannot smuggle a write through an unexpected subcommand."""
    repo = Repo()
    try:
        for args in (["update-ref", "refs/heads/x", "HEAD"],
                     ["symbolic-ref", "HEAD", "refs/heads/y"],
                     ["filter-branch", "--all"],
                     ["gc", "--prune=now"]):
            expect_error("отклонено %s" % " ".join(args),
                         lambda a=args: C.run_git(repo.path, a),
                         "GIT_ARGUMENT_FORBIDDEN")
    finally:
        repo.cleanup()


def test_output_is_stripped_of_terminal_escapes():
    repo = Repo()
    try:
        # A branch name may legally contain an escape sequence; the output is
        # printed for a person, so it is cleaned.
        repo.git("checkout", "-q", "-b", "evil\x1b[31mred")
        result = C.run_git(repo.path, ["branch"])
        check("escape-последовательности вырезаны", "\x1b" not in result["stdout"],
              repr(result["stdout"][:80]))
    finally:
        repo.cleanup()


def test_nested_repository_is_not_mistaken_for_its_own():
    """A course cloned inside another repository belongs to the outer one."""
    outer = Repo()
    try:
        inner = outer.path / "inner"
        inner.mkdir()
        subprocess.run(["git", "init", "-q", str(inner)], capture_output=True,
                       env=outer._env, timeout=30)

        ok, toplevel = C.is_repository_root(outer.path)
        check("внешний каталог — свой корень", ok is True and toplevel is not None)

        # A plain directory inside a repository answers "--is-inside-work-tree"
        # truthfully while not being a repository root itself.
        plain = outer.path / "plain"
        plain.mkdir()
        observed = C.observe(plain)
        check("обычный подкаталог не считается корнем",
              observed["is_repository_root"] is False, str(observed))
    finally:
        outer.cleanup()


# ---------------------------------------------------------------------------
# A10 — fork and upstream are distinct roles
# ---------------------------------------------------------------------------
def test_remote_normalisation_and_fork_detection():
    for url in ("https://user:token@github.com/o/r.git",
                "git@github.com:o/r.git",
                "https://github.com/o/r",
                "https://github.com/o/r/"):
        check("адрес нормализуется: %s" % url[:38],
              C.normalise_remote(url) == "github.com/o/r",
              C.normalise_remote(url))

    check("токен не попадает в нормализованный адрес",
          "token" not in C.normalise_remote("https://user:token@github.com/o/r.git"))

    remotes = {
        "origin": {"fetch": "https://github.com/student/r.git", "push": "git@github.com:student/r.git"},
        "upstream": {"fetch": "https://github.com/o/r.git", "push": "https://github.com/o/r.git"},
    }
    found = C.find_forks(remotes, "https://github.com/o/r.git")
    check("учебный источник найден по адресу, а не по имени",
          found and found["name"] == "upstream", str(found))

    # A remote that merely looks similar is not the course.
    similar = {"origin": {"fetch": "https://github.com/o/r-fork-of-someone.git",
                          "push": "https://github.com/o/r-fork-of-someone.git"}}
    check("похожий адрес не принимается за исходный",
          C.find_forks(similar, "https://github.com/o/r.git") is None)


def test_ssh_and_https_forms_of_one_project_match():
    ssh = {"upstream": {"fetch": "git@github.com:o/r.git", "push": "git@github.com:o/r.git"}}
    https = {"upstream": {"fetch": "https://github.com/o/r", "push": "https://github.com/o/r"}}
    a = C.find_forks(ssh, "https://github.com/o/r.git")
    b = C.find_forks(https, "git@github.com:o/r.git")
    check("ssh и https описывают один проект", bool(a) and bool(b), f"{a} {b}")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def test_illegal_transitions_are_refused():
    check("нельзя прыгнуть в ACCEPTED из DISCOVERY",
          C.can_transition("DISCOVERY", "ACCEPTED")[0] is False)
    check("нельзя опубликовать без черновика",
          C.can_transition("LOCAL_CHECKED", "SUBMITTED")[0] is False)
    check("нельзя получить ACCEPTED из WORKING",
          C.can_transition("WORKING", "ACCEPTED")[0] is False)
    check("переход в то же состояние не переход",
          C.can_transition("WORKING", "WORKING")[1] == "STATE_UNCHANGED")
    check("репетиция Git необязательна после диагностики",
          C.can_transition("TASK_SELECTED", "WORKING")[0] is True)
    check("личный пакет равноправен публичному PR",
          C.can_transition("DRAFT_READY", "PRIVATE_PACKAGE_READY")[0] is True
          and C.can_transition("DRAFT_READY", "AWAITING_STUDENT_PUBLICATION")[0] is True)


def test_task_selection_requires_a_reference():
    document = fresh()
    expect_error("без ссылки на задачу выбор отклонён",
                 lambda: C.select_task(document, task_ref=""),
                 "TASK_REF_MISSING")
    updated = C.select_task(document, task_ref="issue #7", role="researcher")
    check("задача выбрана с ролью", updated["state"] == "TASK_SELECTED"
          and updated["role"] == "researcher")


def test_unknown_role_and_rights_are_refused():
    expect_error("неизвестная роль отклонена",
                 lambda: C.new_contribution(contribution_id=CONTRIBUTION,
                                            session_id=SESSION, course_id="c",
                                            role="wizard"),
                 "ROLE_UNKNOWN")
    document = fresh()
    expect_error("неизвестный статус прав отклонён",
                 lambda: C.select_task(document, task_ref="x", rights_status="maybe"),
                 "RIGHTS_STATUS_UNKNOWN")


# ---------------------------------------------------------------------------
# LOCAL_CHECKED — checks bound to a diff
# ---------------------------------------------------------------------------
def test_empty_checklist_does_not_reach_local_checked():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        expect_error("пустой список проверок не даёт LOCAL_CHECKED",
                     lambda: C.record_local_check(document, repo=repo.path, checks=[]),
                     "CHECKLIST_EMPTY")
    finally:
        repo.cleanup()


def test_failed_check_blocks_local_checked():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        repo.change()
        expect_error("проваленная проверка блокирует переход",
                     lambda: C.record_local_check(document, repo=repo.path, checks=[
                         {"check_id": "tests", "status": "fail", "observed": "2 failing"}]),
                     "CHECKLIST_HAS_FAILURES")
    finally:
        repo.cleanup()


def test_local_checked_records_the_diff_hash():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        repo.change(stage=True)
        updated, snapshot = C.record_local_check(document, repo=repo.path, checks=[
            {"check_id": "tests", "status": "pass", "observed": "12 passed"}])
        check("переход в LOCAL_CHECKED выполнен", updated["state"] == "LOCAL_CHECKED")
        check("отпечаток изменения записан",
              updated["diff_hash"] == snapshot["diff_hash"], str(updated["diff_hash"]))
        check("изменённые файлы перечислены",
              "a.txt" in snapshot["files_changed"], str(snapshot["files_changed"]))
        check("проверка привязана к отпечатку",
              updated["checks"][0]["diff_hash"] == snapshot["diff_hash"])
        check("документ соответствует схеме",
              C.validate(updated)["state"] == "LOCAL_CHECKED")
    finally:
        repo.cleanup()


def test_non_programmatic_checks_may_be_not_applicable():
    """A researcher's contribution may have no automated tests at all."""
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="doc fix", role="researcher")
        document["state"] = "WORKING"
        repo.change(name="notes.md", text="# правка текста\n", stage=True)
        updated, _ = C.record_local_check(document, repo=repo.path, checks=[
            {"check_id": "fact-check", "status": "pass", "observed": "проверено по двум источникам"},
            {"check_id": "automated-tests", "status": "not_applicable",
             "note_ru": "в этом репозитории нет тестов"},
        ])
        check("непрограммная проверка допустима с явной пометкой",
              updated["state"] == "LOCAL_CHECKED")
    finally:
        repo.cleanup()


# ---------------------------------------------------------------------------
# A11 — a changed diff invalidates the checks
# ---------------------------------------------------------------------------
def test_changed_diff_invalidates_checks():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        repo.change(stage=True)
        checked, snapshot = C.record_local_check(document, repo=repo.path, checks=[
            {"check_id": "tests", "status": "pass", "observed": "ok"}])
        drafted = C.draft_ready(checked, description="описание", self_review="прочитал diff",
                                publication_choice="public_pr")
        awaiting = C.mark_awaiting_publication(drafted)

        # The student amends the change after checking it.
        repo.change(text="первая строка\nвторая строка\nтретья строка\n", stage=True)
        submitted = C.observe_submission(awaiting, external_ref="https://example.org/pr/1",
                                         source="learner_report", repo=repo.path)
        check("старые проверки обесценены", submitted["checks"] == [],
              str(submitted["checks"]))
        check("отпечаток обновлён",
              submitted["diff_hash"] != checked["diff_hash"])
        check("сказано, почему проверки сняты",
              "недействительны" in (submitted["observation_note"] or ""),
              str(submitted["observation_note"]))

        ok, blocking, _ = C.validate_draft(submitted, snapshot=snapshot)
        check("черновик с устаревшими проверками не готов", ok is False, str(blocking))
    finally:
        repo.cleanup()


def test_unchanged_diff_keeps_checks():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        repo.change(stage=True)
        checked, _ = C.record_local_check(document, repo=repo.path, checks=[
            {"check_id": "tests", "status": "pass", "observed": "ok"}])
        drafted = C.draft_ready(checked, description="описание", self_review="прочитал",
                                publication_choice="public_pr")
        awaiting = C.mark_awaiting_publication(drafted)
        submitted = C.observe_submission(awaiting, source="learner_report", repo=repo.path)
        check("неизменённое изменение сохраняет проверки",
              len(submitted["checks"]) == 1, str(submitted["checks"]))
    finally:
        repo.cleanup()


# ---------------------------------------------------------------------------
# A12 — a self-report is not an observation
# ---------------------------------------------------------------------------
def test_learner_report_does_not_become_an_observation():
    document = fresh()
    document = C.select_task(document, task_ref="issue #7")
    document["state"] = "AWAITING_STUDENT_PUBLICATION"
    document["publication_choice"] = "public_pr"

    updated = C.observe_submission(document, source="learner_report",
                                   note="я отправил PR")
    check("самоотчёт сохранён как самоотчёт",
          updated["observation_source"] == "learner_report", updated["observation_source"])
    check("самоотчёт переводит в SUBMITTED",
          updated["state"] == "SUBMITTED", updated["state"])

    expect_error("принятие по самоотчёту отклонено",
                 lambda: C.record_acceptance(updated, source="learner_report"),
                 "ACCEPTANCE_REQUIRES_OBSERVATION")


def test_acceptance_requires_an_observation():
    document = fresh()
    document = C.select_task(document, task_ref="issue #7")
    document["state"] = "SUBMITTED"
    document["publication_choice"] = "public_pr"

    accepted = C.record_acceptance(document, source="remote_api",
                                   evidence_note="merged 2026-09-18")
    check("наблюдение репозитория даёт ACCEPTED", accepted["state"] == "ACCEPTED")
    check("источник наблюдения записан", accepted["observation_source"] == "remote_api")

    document["state"] = "AWAITING_STUDENT_PUBLICATION"
    expect_error("принятие из неверного состояния отклонено",
                 lambda: C.record_acceptance(document, source="remote_api"),
                 "ACCEPTANCE_FROM_WRONG_STATE")

    expect_error("неизвестный источник наблюдения отклонён",
                 lambda: C.observe_submission(fresh(), source="vibes"),
                 "OBSERVATION_SOURCE_UNKNOWN")


def test_private_submission_is_a_peer_path():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        repo.change(stage=True)
        checked, _ = C.record_local_check(document, repo=repo.path, checks=[
            {"check_id": "tests", "status": "pass"}])
        drafted = C.draft_ready(checked, description="описание", self_review="прочитал",
                                publication_choice="private_package")
        packaged = C.mark_awaiting_publication(drafted)
        check("личный пакет — отдельное состояние",
              packaged["state"] == "PRIVATE_PACKAGE_READY", packaged["state"])

        submitted = C.observe_submission(packaged, source="human_teacher",
                                         note="преподаватель получил пакет")
        check("личная сдача переходит в PRIVATELY_SUBMITTED",
              submitted["state"] == "PRIVATELY_SUBMITTED", submitted["state"])

        accepted = C.record_acceptance(submitted, source="human_teacher",
                                       evidence_note="принято преподавателем")
        check("личная сдача может быть принята", accepted["state"] == "ACCEPTED")
    finally:
        repo.cleanup()


# ---------------------------------------------------------------------------
# Draft and disclosure
# ---------------------------------------------------------------------------
def test_draft_requires_a_self_review():
    document = C.select_task(fresh(), task_ref="issue #7")
    document["state"] = "LOCAL_CHECKED"
    document["checks"] = [{"check_id": "t", "status": "pass"}]
    expect_error("черновик без самостоятельного просмотра отклонён",
                 lambda: C.draft_ready(document, description="описание", self_review=""),
                 "SELF_REVIEW_MISSING")
    expect_error("пустое описание отклонено",
                 lambda: C.draft_ready(document, description="", self_review="прочитал"),
                 "DRAFT_EMPTY")


def test_required_disclosure_cannot_be_empty():
    document = C.new_contribution(contribution_id=CONTRIBUTION, session_id=SESSION,
                                  course_id="c", role="developer",
                                  ai_disclosure_required=True)
    document = C.select_task(document, task_ref="issue #7")
    document["state"] = "LOCAL_CHECKED"
    document["checks"] = [{"check_id": "t", "status": "pass"}]
    expect_error("раскрытие ИИ обязательно, но не заполнено",
                 lambda: C.draft_ready(document, description="o", self_review="r"),
                 "AI_DISCLOSURE_MISSING")

    with_text = C.set_disclosure_text(document, "Описание и проверки — агент; изменение и решение — мои.")
    drafted = C.draft_ready(with_text, description="o", self_review="r")
    check("с текстом раскрытия черновик готов", drafted["state"] == "DRAFT_READY")
    check("раскрытие не подтверждено учеником само по себе",
          drafted["ai_disclosure"]["confirmed_by_learner"] is False,
          str(drafted["ai_disclosure"]))


def test_disclosure_confirmation_is_not_set_by_default():
    """A model proposal must not be able to confirm a human's disclosure."""
    document = fresh()
    updated = C.set_disclosure_text(document, "текст")
    check("подтверждение остаётся ложным без человека",
          updated["ai_disclosure"]["confirmed_by_learner"] is False)
    confirmed = C.set_disclosure_text(document, "текст", confirmed_by_learner=True)
    check("человеческий канал может подтвердить",
          confirmed["ai_disclosure"]["confirmed_by_learner"] is True)


def test_draft_renders_as_a_draft():
    repo = Repo()
    try:
        document = C.select_task(fresh(), task_ref="issue #7")
        document["state"] = "WORKING"
        repo.change(stage=True)
        checked, snapshot = C.record_local_check(document, repo=repo.path, checks=[
            {"check_id": "tests", "status": "pass", "observed": "12 passed"}])
        text = C.render_draft(checked, snapshot=snapshot, course_title="Учебный пример")
        check("черновик помечен как черновик", "ЧЕРНОВИК" in text)
        check("сказано, что публикует ученик", "ученик" in text and "не открывает PR" in text)
        check("перечислены изменённые файлы", "a.txt" in text)
        check("перечислены проверки", "tests" in text and "pass" in text)
        check("есть чек-лист публикации", "Чек-лист" in text)
        check("команды публикации показаны, а не выполнены",
              "git commit" not in text and "push в свой форк" in text)
    finally:
        repo.cleanup()


def test_blocking_conditions_are_named():
    document = fresh()
    document = C.select_task(document, task_ref="issue #7")
    document["state"] = "LOCAL_CHECKED"
    ok, blocking, advisory = C.validate_draft(document)
    check("отсутствие проверок блокирует", ok is False, str(blocking))
    check("неизвестные права блокируют",
          any("статус прав" in item for item in blocking), str(blocking))
    check("несделанные пункты перечислены как рекомендации",
          len(advisory) > 0, str(advisory))


def test_unknown_contribution_identifier_is_refused():
    expect_error("неизвестная роль в новом вкладе отклонена",
                 lambda: C.new_contribution(contribution_id=CONTRIBUTION,
                                            session_id=SESSION, course_id="c",
                                            role="developer",
                                            publication_choice="telepathy"),
                 "PUBLICATION_CHOICE_UNKNOWN")


def test_short_oids_are_never_recorded():
    check("короткий OID не принимается", C.canonical_oid("abc1234") is None)
    check("полный OID принимается",
          C.canonical_oid("a" * 40) == "a" * 40)
    check("OID из 64 символов принимается (SHA-256 репозиторий)",
          C.canonical_oid("b" * 64) == "b" * 64)


def main():
    tests = [
        test_there_is_no_publishing_function,
        test_read_only_commands_are_allowed_and_mutating_ones_refused,
        test_arbitrary_git_arguments_are_not_forwarded,
        test_output_is_stripped_of_terminal_escapes,
        test_nested_repository_is_not_mistaken_for_its_own,
        test_remote_normalisation_and_fork_detection,
        test_ssh_and_https_forms_of_one_project_match,
        test_illegal_transitions_are_refused,
        test_task_selection_requires_a_reference,
        test_unknown_role_and_rights_are_refused,
        test_empty_checklist_does_not_reach_local_checked,
        test_failed_check_blocks_local_checked,
        test_local_checked_records_the_diff_hash,
        test_non_programmatic_checks_may_be_not_applicable,
        test_changed_diff_invalidates_checks,
        test_unchanged_diff_keeps_checks,
        test_learner_report_does_not_become_an_observation,
        test_acceptance_requires_an_observation,
        test_private_submission_is_a_peer_path,
        test_draft_requires_a_self_review,
        test_required_disclosure_cannot_be_empty,
        test_disclosure_confirmation_is_not_set_by_default,
        test_draft_renders_as_a_draft,
        test_blocking_conditions_are_named,
        test_unknown_contribution_identifier_is_refused,
        test_short_oids_are_never_recorded,
    ]
    for test in tests:
        print("== %s ==" % test.__name__)
        try:
            test()
        except Exception as e:  # noqa: BLE001
            import traceback
            _failures.append(test.__name__)
            print("  FAIL %s выбросил %s: %s" % (test.__name__, type(e).__name__, e))
            traceback.print_exc()

    print()
    print("%d passed, %d failed" % (_passed, len(_failures)))
    if _failures:
        for name in _failures:
            print("  - %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
