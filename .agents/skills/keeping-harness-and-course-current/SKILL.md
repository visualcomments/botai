---
name: keeping-harness-and-course-current
description: Keep the botai harness and every course repository up to date without losing work — scheduled and on-demand updates, what an update may and may not overwrite, how to read the "locally edited, kept" report, and what to do when a course cannot be fast-forwarded. Use when starting a study session after a pause, when the student asks "is there an update", when a course or the harness seems stale, when an update stopped with uncommitted or diverged work, or before teaching a lesson that may have changed upstream.
verified: 2026-09-14
---

# Keeping the Harness and the Course Current

A course is a living repository and the harness that teaches it is a living
harness. Version skew is not cosmetic: a lesson corrected upstream is what the
student should be reading, and a policy or skill fixed upstream is what governs
the session. This skill is the procedure for staying current **without ever
destroying the student's work**.

## When to Use

- Starting a session after a pause longer than a few days
- The student asks whether the course or the harness has an update
- A lesson, syllabus, or tool behaves differently from what the progress record
  describes
- An update stopped and printed a reason (dirty tree, diverged history,
  changed source)
- The student asks what `make update` or `make course-update` actually does

## When NOT to Use

- **Deciding what to teach** — that is `planning-study-sessions`
- **Fetching the corpus** (texts and RAG index) — that is `corpus-acquisition`;
  the corpus is a separate artifact with its own manifests and hashes
- **Editing the course** — that is the student's work as a contributor, and
  belongs to `onboarding-open-source-contributors`

## The one rule that matters

**Never let an update discard work.** Everything below is that rule applied.

## Two different updates

| | What it updates | Command | What it must never touch |
|---|---|---|---|
| Harness | botai itself: policy, skills, agents, commands, scripts, docs | `make update` | `courses/`, `progress/`, `.botai/`, `dist/` — the student's material |
| Course | one course repository under `courses/<slug>/` | `make course-update COURSE=<slug>` | the student's own edits, notes, and assignments inside that course |

They are separate on purpose: a harness update must never be able to rewrite
course material, and a course update must never be able to rewrite policy.

## Procedure: harness update

```bash
make update-check     # is a newer harness published? exit 10 means yes
make update-dry-run   # what would change — writes nothing
make update           # do it
```

Read the report, do not just watch the exit code:

- `добавлено` / `added` — new files (a new skill, a new document);
- `обновлено` / `updated` — upstream changed these and nothing local
  conflicted;
- `сохранены локальные версии` / `kept` — **the important line.** These files
  differ from what was installed and were therefore left alone. Show the list to
  the student: it is either their deliberate local change or drift they did not
  intend.
- `устарело, оставлено` / `stale` — files upstream no longer ships. They are
  kept. Removing them is `--prune`, and only after the student agrees.

To take upstream's version of a kept file anyway:

```bash
python scripts/cli.py update --overwrite
```

The replaced files are copied into `.botai/backup/<timestamp>/` first. Point the
student at that directory and say plainly what was replaced — the backup is a
safety net, not a substitute for asking.

After an update, restart the agent session if the change touched `AGENTS.md`,
`opencode.json`, `.opencode/`, or a skill the session already loaded: config and
policy are read once at startup, and continuing to teach from the old text is a
silent version skew.

## Procedure: course update

Courses arrive from their own repositories:

```bash
make course-add COURSE_URL=<git-url> [REF=<branch>]   # obtain once, records provenance
make course-update-check COURSE=<slug>                # exit 10 = update available
make course-update COURSE=<slug>                      # update it
make detect-courses                                   # source, version, dirty state
```

1. Update only from the source the course was obtained from. The update stops
   if the recorded source and the checkout's `origin` disagree: **replacing the
   source of a course is the student's decision, not the tool's.** Report the
   mismatch and ask.
2. If the course has uncommitted work, the update stops and prints both ways
   forward: commit it, or
   `make course-update-commit COURSE=<slug>` (which commits first, then updates —
   never discards).
3. If the course has its **own commits**, a fast-forward is impossible. The
   update stops and prints how to move them to a branch. This is a decision the
   student makes; do not run history-rewriting commands for them.
4. After a successful update, tell the student what changed: the printed
   `old -> new` revisions, plus `git log` for the lesson file about to be
   taught.

## What to say when something fails

Failures here are usually one of four things, and each has an honest answer:

| What happened | What it means | What to say |
|---|---|---|
| no network / upstream unreachable | a moment, not a fact | retry; if it persists, work from the local copy and say the update did not run |
| dirty tree (`незакоммиченные изменения`) | the student has work in progress | show the list, offer commit-or-stash, do not discard |
| diverged history | the course has local commits | explain the branch move; the student decides |
| recorded source ≠ `origin` | the course's origin changed | stop and ask; never silently follow a new URL |

Never work around any of these by deleting the checkout and re-cloning it: that
is exactly the operation that destroys the student's work, and a fresh clone is
not an update.

## Scheduled updates

An update is only useful if it happens. On deployment, `make install` records
the provenance needed for later updates; a scheduler entry makes them routine:

- **Cron (Linux/macOS):** `0 9 * * 1 cd <project> && python3 scripts/cli.py update >> .botai/update.log 2>&1`
- **systemd timer:** a `botai-update.service` running the same command, with a
  weekly `OnCalendar=Mon 09:00`.
- **Task Scheduler (Windows):** a weekly task running
  `py -3 scripts\cli.py update` in the project directory.

A scheduled update keeps the harness current and never touches courses or
progress. It is deliberately the *safe* half of the pair: course updates stay
manual, because a course checkout may hold uncommitted student work at any hour,
and a scheduler cannot ask.

## Evidence and reporting

After any update, the record must be updateable too. Tell the student:

- harness: previous and new version, and any kept/stale files;
- course: previous and new revision, and what changed in the lesson in play;
- anything that failed, verbatim, with the exact command to retry.

Then write it into the progress record (`maintaining-course-progress`) so the
next session knows which revisions were taught.

## References

- `corpus-acquisition` — the corpus (texts + index) has its own fetch and hashes
- `multi-course-workspace` — several courses in one project, one subproject each
- `maintaining-course-progress` — where the revisions you taught are recorded
- `docs/updating.md` — the full user-facing description, including rollback

## Last Validated

2026-09-14. Procedure current as of this date; re-verify when the update
commands, their flags, or the provenance records change.
