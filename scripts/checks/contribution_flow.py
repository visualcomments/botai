#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end check of the contribution flow through the real CLI.

Asserts the properties that make this stage trustworthy:

* the agent has no path to commit, push or open a PR — the commands simply do
  not exist, and the read-only Git layer refuses the mutating form of every
  command it is allowed to read;
* a course whose contract disables contribution is refused, because that is the
  course's decision and not the agent's to override;
* a self-report of publication does not become an observation;
* a changed diff invalidates the checks made against the earlier revision;
* the rehearsal repository reproduces staged-vs-unstaged without touching any
  real repository.

No network. The Git repository used is created inside a temporary directory.

Run:
    python3 scripts/checks/contribution_flow.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from botai_core import contribution as C  # noqa: E402

failures = 0


def check(name, condition, detail=""):
    global failures
    if condition:
        print("  [ok]   %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        failures += 1


def cli(root, *args, input_text=None):
    completed = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), *args, "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), input=input_text, timeout=180,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, env=env, timeout=30)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="botai-contribflow-"))
    root = tmp / "workspace"
    course_dir = root / "courses" / "minimal-diff"
    course_dir.parent.mkdir(parents=True)
    shutil.copytree(REPO / "examples" / "minimal-course", course_dir)

    print("workspace: %s" % root)

    print()
    print("### 1. курс без вклада отказывает — это условие курса")
    code, out = cli(root, "course-accept", "--course", "minimal-diff")
    check("курс принят", code == 0, out[-200:])
    code, out = cli(root, "contribute-start", "--course", "minimal-diff",
                    "--task", "https://example.org/issues/7")
    check("вклад отклонён при contribution.enabled=false", code == 3, "exit=%d" % code)
    check("отказ называет причину, а не молчит",
          "не включает вклад" in out or "условие курса" in out, out[-300:])

    print()
    print("### 2. курс, разрешающий вклад")
    contract_path = course_dir / "botai" / "course.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["contribution"] = {"enabled": True, "guide_path": "README.md",
                                "private_submission_allowed": True,
                                "ai_disclosure_required": True}
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2),
                             encoding="utf-8", newline="\n")
    code, out = cli(root, "course-accept", "--course", "minimal-diff")
    check("курс принят заново с включённым вкладом", code == 0, out[-200:])

    # The course directory is not its own repository on purpose: that is the
    # A22 case, and contribute-status must say so rather than guess.
    code, out = cli(root, "contribute-start", "--course", "minimal-diff",
                    "--task", "https://example.org/issues/7", "--role", "developer",
                    "--publish", "public_pr", "--repo", str(course_dir))
    check("вклад открыт", code == 0, out[-300:])
    contribution_id = None
    for line in out.splitlines():
        if "contribution_id :" in line:
            contribution_id = line.split(":", 1)[1].strip()
            break
    check("идентификатор вклада получен", bool(contribution_id), str(contribution_id))
    check("сказано, что публикует ученик",
          "не делаем" not in out and "выполняет ученик" in out, out[-300:])

    print()
    print("### 3. рабочая копия внутри другого каталога не свой репозиторий")
    code, out = cli(root, "contribute-status", "--course", "minimal-diff",
                    "--contribution", contribution_id)
    check("статус читается", code == 0, out[-200:])
    check("названо, что это не корень своего репозитория",
          "не является корнем" in out, out[-500:])
    check("перечислены блокирующие условия", "БЛОКИРУЕТ" in out, out[-600:])

    print()
    print("### 4. репетиционный репозиторий воспроизводит staged/unstaged")
    practice = tmp / "practice"
    code, out = cli(root, "contribute-rehearsal", "--dest", str(practice))
    check("репетиция создана", code == 0, out[-300:])
    check("репозиторий существует", (practice / ".git").exists())

    staged = git(practice, "diff", "--cached", "--name-only")
    unstaged = git(practice, "diff", "--name-only")
    check("в индексе только notes.txt",
          staged.stdout.split() == ["notes.txt"], repr(staged.stdout))
    check("вне индекса только hello.txt",
          unstaged.stdout.split() == ["hello.txt"], repr(unstaged.stdout))

    print()
    print("### 5. наблюдение запрещает изменение и разрешает чтение")
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "S", "GIT_AUTHOR_EMAIL": "s@e.org",
                "GIT_COMMITTER_NAME": "S", "GIT_COMMITTER_EMAIL": "s@e.org"})
    for args, should_allow in ((["status"], True), (["diff"], True),
                               (["add", "."], False), (["commit", "-m", "x"], False),
                               (["push"], False)):
        try:
            C.run_git(practice, args)
            allowed = True
        except C.ContributionError:
            allowed = False
        check("git %-14s разрешено=%s" % (" ".join(args), allowed),
              allowed is should_allow, "ожидалось %s" % should_allow)

    print()
    print("### 6. изменение diff обесценивает проверки")
    document = C.new_contribution(contribution_id="44444444-4444-4444-8444-444444444444",
                                  session_id=None, course_id="minimal-diff",
                                  role="developer")
    document = C.select_task(document, task_ref="issue")
    document["state"] = "WORKING"
    checked, snapshot = C.record_local_check(document, repo=practice, checks=[
        {"check_id": "review", "status": "pass", "observed": "прочитал diff"}])
    check("проверки записаны с отпечатком", bool(checked["diff_hash"]))

    # Change the working tree after the check.
    (practice / "hello.txt").write_text("первая строка\nвторая\nтретья\n", encoding="utf-8")
    drafted = C.draft_ready(checked, description="описание", self_review="прочитал",
                            publication_choice="public_pr")
    awaiting = C.mark_awaiting_publication(drafted)
    submitted = C.observe_submission(awaiting, source="learner_report",
                                     note="я отправил PR", repo=practice)
    check("изменившийся diff снял проверки", submitted["checks"] == [],
          str(submitted["checks"]))
    check("самоотчёт остаётся самоотчётом",
          submitted["observation_source"] == "learner_report")
    check("принятие по самоотчёту невозможно", True)
    try:
        C.record_acceptance(submitted, source="learner_report")
        check("принятие по самоотчёту отклонено", False, "принято")
    except C.ContributionError as e:
        check("принятие по самоотчёту отклонено",
              e.code == "ACCEPTANCE_REQUIRES_OBSERVATION", e.code)

    print()
    print("### 7. черновик помечен как черновик и не содержит готовых команд")
    text = C.render_draft(submitted, snapshot=snapshot)
    check("файл начинается с пометки ЧЕРНОВИК", "ЧЕРНОВИК" in text[:400], text[:200])
    check("сказано, что агент не коммитит и не открывает PR",
          "не открывает PR" in text, text[:600])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures == 0:
        print("CONTRIBUTION FLOW OK")
    else:
        print("CONTRIBUTION FLOW FAILURES: %d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
