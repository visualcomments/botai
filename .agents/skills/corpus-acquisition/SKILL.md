---
name: corpus-acquisition
description: Obtain a course corpus automatically when the course publishes it as links (Google Drive, HTTPS/S3, GitHub Releases, HuggingFace) — index archive plus source texts — verify it, install it atomically, and rebuild only when needed. Covers the corpus manifest, the fetch command, what to do when a link is dead or a hash mismatches, and the rule that a course is not ready to teach until its corpus is installed. Use when deploying a course repo, when `tools/status.py` reports the corpus missing or stale, when the student asks for material but search returns nothing, or when a course supplies corpus links and the agent must set itself up.
verified: 2026-09-12
---

# Corpus acquisition

A course that ships a corpus but leaves it to the student to find is a course
that will be taught without evidence. This skill is how the agent obtains the
corpus **by itself**, verifies it, and knows when it is not usable.

The governing rule:

> **A course is not ready to teach until its corpus is installed and
> verified.** If `tools/status.py` says the corpus is missing, acquiring it is
> the first action of the session — not a note to raise with the student later.

## What a corpus consists of

Two artifacts, both described by a manifest in the course repo root:

| Artifact | Manifest | Contents |
|---|---|---|
| **Index** | `index-manifest.json` | `annoy.index`, `embeddings.npy`, `chunks.jsonl`, `config.json` |
| **Texts** | `corpus-manifest.json` | `txt/*.txt` — the verified source texts |

The index makes search fast; the **texts** are what a quotation is actually
checked against. An index without texts can retrieve a fragment but cannot
verify it, so both are needed.

## Deploying: acquire the corpus

When a course repository is deployed (or the corpus is absent, incomplete, or
stale), run:

```bash
make corpus-fetch          # both artifacts, verified
make corpus-fetch INDEX=1  # index only
```

or without make (any OS, including Windows):

```bash
python3 tools/corpus_fetch.py
python3 tools/corpus_fetch.py --index-only
python3 tools/corpus_fetch.py --status      # report only, download nothing
```

`CORPUS.md` and `docs/GOOGLE-DRIVE.md` in the course name the exact env vars —
`COURSE_CORPUS_ROOT` (where the corpus lives), `COURSE_INDEX_URL` and
`COURSE_CORPUS_URL` (override links, for a mirror or a fresh upload).

### What the fetch does

1. **Locate** each artifact: an explicit `--url` / env var wins; otherwise read
   the manifest's `archive.url`.
2. **Download** with the right flow per channel — Google Drive needs the
   large-file confirm handling (`tools/corpus_fetch.py` implements it; `gdown`
   is used when installed, with a stdlib fallback).
3. **Verify** the archive and **every file** against the manifest SHA-256
   **before** unpacking. A mismatch is a hard stop, not a warning: it means the
   artifact is corrupt, stale, or not the one the course was verified against.
   When no hash exists (a plain URL, a fresh upload), fall back to structural
   validation — the archive opens, the expected files are present, `config.json`
   parses, `n_chunks`/`n_files` look sane — and **say explicitly that it was not
   hash-verified**.
4. **Install atomically**: unpack to a temporary directory, then swap
   `index/` → `index_old/`. A failed download must never leave a half-written
   corpus where the previous one used to be.
5. **Record provenance**: the channel, the hash, and the verification level go
   into the session record and, for the course repo, into the manifest itself.

## When something goes wrong

| Symptom | Meaning | Action |
|---|---|---|
| Corpus missing | never fetched, or fetched elsewhere | run `make corpus-fetch` |
| Link dead / 403 / Drive "no access" | the file was unshared, moved, or the link is stale | report it to the course maintainer; **do not** invent a substitute URL |
| SHA-256 mismatch | wrong or stale artifact | stop; do not use the download; report hash expected vs got |
| `config.json` missing / unparsable | archive is not a corpus index | stop; the upload is wrong |
| `index/` present, `txt/` empty | index without texts | fetch the texts; quotations cannot be verified without them |
| `index_version` older than the texts | **stale index** | refetch, or rebuild (see `rag-corpus-operations`) |
| Source endpoint unreachable (no network) | environment problem, not a corpus problem | say so; work statically (grep the texts) and retry later |

Never report a corpus as "unavailable because the link failed once". A timeout
or a transient 5xx is a moment, not a fact — retry, then report what actually
happened.

## Distributing a corpus (course maintainer side)

When the course author publishes a corpus, the deployment must not silently
depend on a manual step. The course repo therefore:

- keeps `index-manifest.json` and `corpus-manifest.json` current — with
  `size_bytes` and `sha256` for every file, and a working `url`;
- runs `make corpus-fetch` as part of its own setup, so a fresh clone becomes
  teachable in one command;
- validates the manifests in CI (`tools/validate_course.py`), so a manifest
  that lost its hash or its URL fails the build rather than the lesson.

A manifest without `sha256` is a manifest that cannot detect a corrupted
download — treat it as incomplete.

## Working without a corpus

If the course genuinely publishes no corpus, that is a stated property, not a
failure — but it must be stated. Teach with explicitly labelled material
("вне корпуса", see `language-and-translation` and the citation contract) and
never present an unverifiable claim as a quotation. Do not quietly build a
private corpus from whatever the agent can find: an unverified collection
gives the appearance of evidence without the substance.

## Self-check before teaching

1. `tools/status.py` — does it report the corpus present, complete, and fresh?
2. Are **both** the index and the texts installed?
3. Was the artifact hash-verified, and if not, was that said out loud?
4. Does a test search return fragments with coordinates?
5. Does `make verify` pass on the course's quotations?

Only when all five hold is the course ready to teach from evidence.
