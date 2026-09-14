#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Course manager: obtain and keep up to date the repository of a course.

The harness is only half of a working study environment; the other half is the
course repository itself. This script is the one place that knows how to get a
course in and how to refresh it later:

    python scripts/courses.py detect [--course <slug>]
    python scripts/courses.py fetch  --url <git-url> [--name <slug>] [--ref <ref>]
    python scripts/courses.py update [--course <slug>] [--check|--dry-run]
    python scripts/courses.py list

Design decisions that matter:

* **A checkout is what makes later updates honest.** The course is obtained as a
  git checkout and its exact revision is recorded in `.botai-course.json`
  together with a fingerprint of every file. An update then knows whether a file
  changed locally, and refuses to lose that change.
* **Local edits are preserved, not overwritten.** Student work lives in the
  course checkout (`assignments/`, `progress/`, hand-written notes), so an
  update keeps any locally modified file by default; `--take-upstream` replaces
  them after backing them up into the workspace's `.botai/backup/`.
* **A dirty tree is refused, not forced.** Uncommitted work is reported with the
  exact command to stash it. Nothing is discarded automatically.
* **A recorded source is never silently replaced.** When a course's origin
  differs from the recorded one, the update stops and says so: swapping the
  source is a decision for the student, not for the tool.
* **A course with no recorded origin is updated only from the ref it was taken
  from** — never from an invented URL.

Exit codes: 0 ok / already current, 1 failure, 2 usage or environment error;
`--check` uses 10 for "an update is available".
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as H  # noqa: E402


# ---------------------------------------------------------------------------
# Locating courses
# ---------------------------------------------------------------------------
def courses_dir(root):
    return Path(root) / "courses"


def course_path(root, slug):
    return courses_dir(root) / slug


def list_courses(root):
    d = courses_dir(root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))


def slugify(name):
    out = []
    for ch in name.strip().lower():
        out.append(ch if (ch.isalnum() or ch in "-_.") else "-")
    return "".join(out).strip("-") or "course"


def course_record(cdir):
    """The `.botai-course.json` of a course checkout, if any."""
    return H.load_json(Path(cdir) / H.COURSE_RECORD, default=None)


def course_dirty(cdir):
    """Uncommitted paths, ignoring the provenance file this tool writes itself.

    `.botai-course.json` is bookkeeping, not the student's work: reporting it as
    a local change would make every course look dirty right after it is added.
    """
    if not H.is_git_worktree(cdir):
        return []
    return [l for l in H.git_dirty(cdir) if not l.endswith(H.COURSE_RECORD)]


def commit_work(cdir, message="моя работа перед обновлением курса"):
    """Commit the course's pending work so a fast-forward can proceed.

    Committing is the one step here that writes history, so it stays a separate,
    explicitly requested action (`--commit-and-update`) instead of a silent part
    of an update. Returns (ok, detail).
    """
    rc, out = H.git(["add", "-A"], cwd=cdir)
    if rc != 0:
        return False, "git add: %s" % out
    rc, out = H.git(
        ["-c", "user.name=botai-student", "-c", "user.email=student@localhost",
         "commit", "-m", message],
        cwd=cdir,
    )
    if rc != 0:
        return False, "git commit: %s" % out
    rc, rev = H.git(["rev-parse", "--short", "HEAD"], cwd=cdir)
    return True, rev if rc == 0 else "?"


def course_url(cdir):
    """Where a course came from: its record first, then its own git origin."""
    rec = course_record(cdir) or {}
    if rec.get("source"):
        return rec["source"]
    if H.is_git_worktree(cdir):
        return H.git_origin(cdir)
    return ""


def course_ref(cdir):
    rec = course_record(cdir) or {}
    if rec.get("ref"):
        return rec["ref"]
    if rec.get("revision"):
        return rec["revision"]
    return ""


def course_revision(cdir):
    return H.git_head(cdir, short=False) if H.is_git_worktree(cdir) else ""


def course_version(cdir):
    """A human-meaningful version: the repo's own tag, else the commit sha.

    A repository without tags has nothing better than the sha to show, so the
    sha is printed alone rather than as "sha (sha)".
    """
    if not H.is_git_worktree(cdir):
        return "(не git-репозиторий)"
    rev = H.git_head(cdir)
    rc, tag = H.git(["describe", "--tags", "--exact-match"], cwd=cdir)
    if rc == 0 and tag:
        return "%s (%s)" % (tag, rev)
    return rev or "(неизвестна)"


def detect_courses(root, log=print):
    """Report every course subproject and where it came from."""
    found = list_courses(root)
    log("== курсы в проекте ==")
    if not found:
        log("  (нет ни одного курса)")
        log("  добавить: python scripts/cli.py course-add --url <git-url>")
        return 0
    for cdir in found:
        rec = course_record(cdir) or {}
        src = course_url(cdir) or "(источник не записан)"
        log("  %s" % cdir.name)
        log("    источник : %s" % src)
        log("    версия   : %s" % course_version(cdir))
        if rec.get("obtained_at"):
            log("    получен  : %s" % rec["obtained_at"])
        if rec.get("files"):
            log("    файлов в отпечатке: %d" % len(rec["files"]))
        if not rec and not H.is_git_worktree(cdir):
            log("    обновление невозможно: нет ни записи о получении, ни git-истории")
        dirty = course_dirty(cdir)
        if dirty:
            log("    незакоммиченных изменений: %d" % len(dirty))
    return 0


# ---------------------------------------------------------------------------
# Obtaining a course
# ---------------------------------------------------------------------------
def fetch_course(root, url, name=None, ref=None, force=False, log=print):
    """Clone a course repository into courses/<slug>/ and record its provenance."""
    if not url:
        log("нужен адрес курса: --url <git-url>")
        return 2
    slug = slugify(name or Path(url.rstrip("/")).name.replace(".git", ""))
    dest = course_path(root, slug)
    log("== получение курса ==")
    log("  источник : %s" % url)
    log("  ссылка   : %s" % (ref or "(по умолчанию)"))
    log("  каталог  : %s" % dest)

    if dest.exists() and any(dest.iterdir()):
        if not force:
            log("  каталог уже существует и не пуст")
            log("  обновить его: python scripts/cli.py course-update --course %s" % slug)
            log("  переустановить с нуля: добавьте --force (каталог будет снесён!)")
            return 2
        log("  --force: удаляю существующий каталог %s" % dest)
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet"]
    if ref:
        args += ["--branch", ref]
    args += [url, str(dest)]
    rc, out = H.git(args, timeout=1800)
    if rc != 0:
        log("  ошибка git clone: %s" % out)
        log("  каталог курса не создан; проверьте адрес и доступ")
        return 1

    rec = write_course_record(dest, url, ref or "")
    log("  получено : %s" % course_version(dest))
    log("  файлов   : %d" % len(rec["files"]))
    rel = dest.relative_to(root)
    log("  дальше:")
    log("    python scripts/cli.py course-set --course %s" % slug)
    log("    (курс лежит в %s — работайте и коммитьте в нём)" % rel)
    return 0


def write_course_record(cdir, url, ref):
    """Record provenance + a fingerprint, the baseline for later updates.

    The fingerprint covers the whole course tree: a course is arbitrary content,
    not the harness file list, and the provenance file itself is excluded so the
    record does not hash itself.
    """
    files = H.fingerprint_tree(cdir, skip_prefixes=(H.COURSE_RECORD,))
    rec = {
        "schema": 1,
        "source": url,
        "ref": ref or "",
        "revision": course_revision(cdir),
        "obtained_at": H.now_iso(),
        "files": files,
    }
    H.save_json_atomic(Path(cdir) / H.COURSE_RECORD, rec)
    return rec


# ---------------------------------------------------------------------------
# Updating a course
# ---------------------------------------------------------------------------
def sync_upstream_files(cdir, record, ref, take_upstream, dry, log=print):
    """Refresh a course that has no usable git history, from its tarball."""
    url = record.get("source") or ""
    gh = H.github_owner_repo(url)
    if not gh:
        log("  источник не похож на GitHub-репозиторий: %s" % (url or "(пусто)"))
        log("  обновление файловой синхронизацией недоступно;")
        log("  обновите курс вручную в его собственном репозитории")
        return 1

    tmp = tempfile.mkdtemp(prefix="botai_course_")
    try:
        tar_url = "https://codeload.github.com/%s/%s/tar.gz/%s" % (gh[0], gh[1], ref)
        log("  скачивание: %s" % tar_url)
        try:
            archive = os.path.join(tmp, "course.tar.gz")
            H.download(tar_url, archive, timeout=900)
        except RuntimeError as e:
            log("  ошибка загрузки: %s" % e)
            log("  ничего не изменено")
            return 1
        unpack = os.path.join(tmp, "src")
        try:
            H.safe_extract_tar(archive, unpack)
        except Exception as e:  # noqa: BLE001 - archive is untrusted input
            log("  ошибка распаковки: %s" % e)
            return 1
        src = H.single_root(unpack)

        prev = record.get("files") or {}
        report = H.sync_tree(
            src, cdir,
            include=None,
            preserve=("assignments", "progress"),
            prev=prev,
            overwrite=take_upstream,
            prune=False,
            backup_root=Path(cdir).parent.parent / H.BACKUP_DIR / (Path(cdir).name + "-" + H.now_stamp()),
            dry=dry,
        )
        H.report_sync(report, log=log)
        if report["kept"] and not take_upstream:
            log("  локально изменённые файлы курса оставлены; чтобы заменить их "
                "версией из репозитория курса, повторите с --take-upstream")
        if dry:
            log("  предпросмотр: ничего не записано")
            return 0
        new_rec = dict(record)
        new_rec.update({"revision": ref, "updated_at": H.now_iso(),
                        "files": dict(record.get("files") or {})})
        # `fingerprint()` из harness.py охватывает только файлы *обвязки* botai,
        # а курс — произвольное дерево (lectures/, tools/, docs/, capstone/…).
        # Записываем в отпечаток ровно то, что пришло из upstream: иначе
        # следующий --check считает свежескачанные файлы чужими правками, а
        # обновление теряет базу сравнения.
        for rel, h in H.fingerprint_tree(src).items():
            new_rec["files"][rel] = h
        H.save_json_atomic(Path(cdir) / H.COURSE_RECORD, new_rec)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_divergence(cdir, target_ref, log=print):
    """Report, without changing anything, whether a fast-forward is possible.

    A dry run that only printed "would fetch" would hide the one outcome the
    student most needs advance warning about: a course that has its own commits
    cannot be fast-forwarded, and the update will stop.
    """
    rc, out = H.git(["ls-remote", "origin", target_ref], cwd=cdir, timeout=120)
    if rc != 0 or not out:
        log("  расхождение не проверено: github/local upstream недоступен")
        return None
    rev = out.split()[0]
    head = H.git_head(cdir, short=False)
    rc, _ = H.git(["merge-base", "--is-ancestor", head, rev], cwd=cdir)
    if rc != 0:
        log("  внимание: история разошлась (в курсе есть свои коммиты)")
        log("  быстрое обновление не пройдёт; нужен перенос своих коммитов в ветку")
        log("  (решение принимает обучающийся: см. вывод обычного запуска)")
        return False
    log("  предпросмотр обновления: быстрое обновление возможно (%s -> %s)"
        % (head[:8], rev[:8]))
    return True


def update_course(root, slug, check=False, dry=False, take_upstream=False,
                  ref=None, commit=False, log=print):
    """Bring one course checkout up to date, protecting local work."""
    cdir = course_path(root, slug)
    if not cdir.is_dir():
        log("нет такого курса: %s" % cdir)
        log("список: python scripts/cli.py courses")
        return 2

    record = course_record(cdir) or {}
    url = course_url(cdir)
    git_repo = H.is_git_worktree(cdir)

    log("== обновление курса: %s ==" % slug)
    log("  каталог  : %s" % cdir)
    log("  источник : %s" % (url or "(не записан)"))
    log("  версия   : %s" % course_version(cdir))

    if not git_repo:
        ref = ref or record.get("ref") or record.get("revision") or ""
        if not url or not ref:
            log("  обновление невозможно: у курса нет ни git-истории, ни записи об источнике")
            log("  получите курс заново: python scripts/cli.py course-add --url <git-url>")
            return 2
        log("  режим: файловая синхронизация из %s" % ref)
        if check:
            rev = remote_revision_of(url, ref)
            log("  версия upstream: %s" % (rev[:8] if rev else "недоступна"))
            if not rev:
                log("  не удалось получить данные upstream: проверьте связь и повторите")
                return 1
            if rev == record.get("revision"):
                log("  обновление не требуется")
                return 0
            log("  доступно обновление")
            return 10
        return sync_upstream_files(cdir, record, ref, take_upstream, dry, log=log)

    # --- git checkout path -------------------------------------------------
    if not url:
        log("  отказ: у чекаута нет источника (remote origin не настроен)")
        log("  этот случай не восстанавливается автоматически — сообщите о нём;")
        log("  ничего не изменено")
        return 1

    origin_now = H.normalize_repo_url(H.git_origin(cdir))
    origin_rec = H.normalize_repo_url(record.get("source") or "")
    if origin_rec and origin_now and origin_now != origin_rec:
        log("  ОСТАНОВ: источник курса изменился")
        log("    записано при получении: %s" % record.get("source"))
        log("    сейчас в origin       : %s" % H.git_origin(cdir))
        log("  подмена источника — решение обучающегося, а не инструмента;")
        log("  ничего не изменено")
        return 1

    dirty = course_dirty(cdir)
    if dirty:
        log("  в курсе есть незакоммиченные изменения (%d):" % len(dirty))
        for line in dirty[:10]:
            log("    %s" % line)
        if commit and not (check or dry):
            ok, detail = commit_work(cdir)
            if not ok:
                log("  не удалось закоммитить работу: %s" % detail)
                log("  ничего не изменено")
                return 1
            log("  работа закоммичена: %s" % detail)
        else:
            log("  отказ: сначала закоммитьте или отложите их:")
            log("    git -C %s stash push -u -m 'before update'" % cdir)
            log("    либо: python scripts/cli.py course-update --course %s --commit-and-update"
                % slug)
            log("  ничего не изменено")
            return 1

    head = course_revision(cdir)
    target_ref = ref or (record.get("ref") if record.get("ref") and not record["ref"].startswith("(") else "") or "HEAD"

    if check:
        rc, remote = H.git(["ls-remote", "origin", target_ref], cwd=cdir, timeout=120)
        if rc == 0 and not remote and target_ref != "HEAD":
            rc, remote = H.git(["ls-remote", "origin", "HEAD"], cwd=cdir, timeout=120)
        if rc != 0 or not remote:
            log("  не удалось получить данные upstream: проверьте связь и повторите")
            return 1
        rev = remote.split()[0]
        log("  HEAD     : %s" % (head[:8] or "?"))
        log("  upstream : %s" % rev[:8])
        if rev == head:
            log("  обновление не требуется")
            return 0
        log("  доступно обновление")
        log("  выполните: python scripts/cli.py course-update --course %s" % slug)
        return 10

    if dry:
        rc, out = H.git(["fetch", "--dry-run", "origin"], cwd=cdir, timeout=300)
        log("  предпросмотр: git fetch origin%s (%s)"
            % ((" " + ref) if ref else "", "есть изменения" if out else "изменений нет"))
        check_divergence(cdir, target_ref, log=log)
        log("  ничего не записано")
        return 0

    if ref:
        rc, out = H.git(["fetch", "--quiet", "origin", ref], cwd=cdir, timeout=900)
        fetched = H.git_head(cdir, short=False)
        if rc == 0:
            rc2, fetched = H.git(["rev-parse", "FETCH_HEAD"], cwd=cdir)
            fetched = fetched if rc2 == 0 else ""
        if rc != 0:
            log("  ошибка git fetch: %s" % out)
            log("  ничего не изменено")
            return 1
        merge_args = ["merge", "--ff-only", "FETCH_HEAD"]
    else:
        rc, out = H.git(["fetch", "--quiet", "origin"], cwd=cdir, timeout=900)
        if rc != 0:
            log("  ошибка git fetch: %s" % out)
            log("  ничего не изменено")
            return 1
        rc, upstream_ref = H.git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=cdir)
        if rc != 0 or not upstream_ref:
            log("  у ветки нет upstream; укажите ветку явно: --ref <branch>")
            return 1
        fetched = H.git_head(cdir, short=False)
        rc2, fetched = H.git(["rev-parse", upstream_ref], cwd=cdir)
        fetched = fetched if rc2 == 0 else ""
        merge_args = ["merge", "--ff-only", upstream_ref]

    if not fetched:
        log("  не удалось определить целевой коммит; ничего не изменено")
        return 1

    if fetched == head:
        log("  уже самая свежая версия (%s)" % (head[:8] or "?"))
        write_course_record(cdir, record.get("source") or url, record.get("ref") or "")
        return 0

    rc, _ = H.git(["merge-base", "--is-ancestor", "HEAD", fetched], cwd=cdir)
    if rc != 0:
        log("  отказ: история курса разошлась (в нём есть свои коммиты)")
        log("  быстрый проход невозможен; свои коммиты можно перенести в ветку:")
        log("    git -C %s branch my-work && git -C %s reset --hard %s"
            % (cdir, cdir, fetched[:8]))
        log("  это решение обучающегося — инструмент его не принимает")
        return 1

    rc, out = H.git(merge_args, cwd=cdir, timeout=600)
    if rc != 0:
        log("  ошибка git merge --ff-only: %s" % out)
        log("  ничего не изменено (курс остался на %s)" % (head[:8] or "?"))
        return 1

    new_head = course_revision(cdir)
    log("  обновлено: %s -> %s" % (head[:8] or "?", new_head[:8] or "?"))
    write_course_record(cdir, record.get("source") or url, record.get("ref") or "")
    if record.get("files"):
        # Local edits that survived the fast-forward are reported, not hidden.
        kept = []
        for rel, h in record["files"].items():
            p = Path(cdir) / rel
            if p.is_file() and H.sha256_file(p) != h:
                kept.append(rel)
        if kept:
            log("  локальные изменения сохранены (быстрый проход их не трогал): %d файлов" % len(kept))
            for rel in kept[:10]:
                log("    %s" % rel)
    return 0


def remote_revision_of(url, ref):
    rc, out = H.git(["ls-remote", url, ref or "HEAD"], timeout=120)
    if rc != 0 or not out:
        return ""
    return out.split()[0]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Курсы проекта botai: получение и обновление")
    sub = ap.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="показать курсы и их источники")
    p_detect.add_argument("--root")
    p_detect.add_argument("--course")

    p_fetch = sub.add_parser("fetch", help="получить курс в courses/<slug>/")
    p_fetch.add_argument("--url", required=True, help="git-адрес курса")
    p_fetch.add_argument("--name", help="короткое имя (slug); по умолчанию из адреса")
    p_fetch.add_argument("--ref", help="ветка/тег/коммит")
    p_fetch.add_argument("--force", action="store_true", help="переустановить, снеся каталог")
    p_fetch.add_argument("--root")

    p_update = sub.add_parser("update", help="обновить курс из его источника")
    p_update.add_argument("--course", required=True)
    p_update.add_argument("--ref", help="ветка/тег/коммит")
    p_update.add_argument("--check", action="store_true", help="только проверить (код 10 = есть обновление)")
    p_update.add_argument("--dry-run", action="store_true")
    p_update.add_argument("--take-upstream", action="store_true",
                          help="заменить и локально изменённые файлы (с копией в .botai/backup/)")
    p_update.add_argument("--commit-and-update", dest="commit", action="store_true",
                          help="сначала закоммитить незакоммиченную работу курса, затем обновить")
    p_update.add_argument("--root")

    p_list = sub.add_parser("list", help="список курсов (кратко)")
    p_list.add_argument("--root")

    args = ap.parse_args(argv)
    root = Path(args.root or os.environ.get("BOTAI_ROOT") or ".").expanduser().resolve()

    if args.command == "detect":
        return detect_courses(root)
    if args.command == "list":
        for c in list_courses(root):
            print("%s - %s" % (c.name, course_version(c)))
        return 0
    if args.command == "fetch":
        return fetch_course(root, args.url, args.name, args.ref, args.force)
    if args.command == "update":
        if args.check and args.dry_run:
            ap.error("--check и --dry-run несовместимы: выберите одно")
        return update_course(root, args.course, check=args.check, dry=args.dry_run,
                             take_upstream=args.take_upstream, ref=args.ref,
                             commit=args.commit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
