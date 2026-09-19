# Corpus diagnosis — why «Задание 1 (цитата Конта) ⏸ Требует установки корпуса» fires

**Course:** `courses/history-and-philosophy-of-science-graduate` (separate
repository: `visualcomments/history-and-philosophy-of-science-graduate`)
**Date of inspection:** 2026-09-19

## Root cause: the **texts artifact was never published**; only the index was.

The harness policy (AGENTS.md §9) treats a corpus as **both artifacts**: the
index (`annoy.index`, `embeddings.npy`, `chunks.jsonl`, `config.json`) and the
source texts (`txt/*.txt`). Quote verification — which Задание 1 exercises —
reads from `txt/`, not from the index. An index without texts retrieves a
fragment but cannot verify a quotation (AGENTS.md §9, sentence 2).

The course ships **only** the index manifest (`index-manifest.json`) with a real
Google Drive URL and hash. There is **no real `corpus-manifest.json`** — only
`corpus-manifest.example.json`, a stub with an empty `archive.url`. The tool
`tools/corpus_fetch.py` therefore fetches the index, installs it into
`COURSE_CORPUS_ROOT/index/`, prints success, then exits 1 because the texts
manifest declares no URL.

Result:

```
COURSE_CORPUS_ROOT/
  index/         ← present, hash-verified, 111 MB  (annoy.index, embeddings.npy,
                  │                                chunks.jsonl, config.json)
  index_old/     ← leftover from a prior atomic swap, 111 MB (see below)
  txt/           ← MISSING — the 98 source texts never fetched
```

The status command reports "корпус НЕ УСТАНОВЛЕН или неполон" and the student is
correctly told to run `python tools/corpus_fetch.py`. But the tool already did
its part and the agent can do nothing more without a texts archive URL.

## Evidence

- `index-manifest.json` real, populated, SHA-256 matches the installed files.
- `corpus-manifest.example.json` is the **only** texts manifest; its
  `archive.url` and `files` are empty — no source is declared.
- `index/config.json` lists 98 filenames including
  `philosophy__Comte_Positive_Philosophy.txt` — exactly the file the Comte
  quote cites.
- `index/chunks.jsonl` (33.4 MB, 24748 lines) contains the full text of all
  98 files in chunked form with 100 % coverage (as documented in
  `docs/GOOGLE-DRIVE.md`). **The texts are technically recoverable** from the
  course's own verified artifact, but `corpus_fetch.py`'s install path expects
  a *published* texts archive, not a reconstruction.
- `index_old/` (duplicate of `index/`, 111 MB) is a leftover from an earlier
  `index_fetch.py` run that performed an atomic swap (`os.rename(index →
  index_old)`). The tool deliberately leaves the old tree as backup but
  **never deletes it** — this is a real bug in `index_fetch.py`: `install_dir()`
  renames aside but never removes the backup, so repeated fetches double disk
  usage silently.

## Corrective actions (ordered)

1. **Publish a texts archive.** The course author must upload the 98 texts
   (`txt/*.txt`) as a zip and publish it on Google Drive (or HTTPS). Fill in
   `corpus-manifest.json` with the URL, archive SHA-256, and per-file hashes,
   then commit the manifest. Once that is in place, `make corpus-fetch` installs
   both artifacts atomically and quote verification is unblocked.
2. **Delete `index_old/`.** It is a stale backup created by the buggy atomic
   swap in `tools/index_fetch.py`. Reclaim 111 MB and remove the confusion
   between "live index" and "backup index".
3. **Fix `index_fetch.py.install_dir()`** to delete the `_old` backup after a
   successful install. The current code renames aside but never cleans up —
   each re-fetch leaves another 111 MB orphan.
4. **Fix `index_fetch.py` hashlib crash** when a manifest lists per-file
   hashes but no archive hash: `hashlib` is imported inside `if expect:` but
   used unconditionally at line 138, raising `NameError`.
5. **Add zip-slip protection** to `index_fetch.py`. The course's own script
   uses `zipfile.ZipFile(...).extractall(unpack)` without validating member
   paths against parent traversal, while the core module's `corpus.py`
   (line ~170) explicitly guards against it. An attacker with write access to
   the Google Drive link could replace the archive and overwrite arbitrary
   files inside the workspace — the core's safety rule must apply here too.

## What the harness does correctly

- `status.py` prints "txt файлов: 0" when `txt/` is absent — correct.
- `corpus_fetch.py` aborts with a clear message when the texts manifest has no
  URL — correct.
- `make verify` refuses to validate citations when `txt/` is missing — correct.

The problem is entirely at the **course level**: the author promised a corpus
but only published half of it, and the tooling faithfully follows what the
manifest says.
