---
name: rag-corpus-operations
description: Work with a course corpus through its RAG index — embeddings, chunks, and search — without ever fabricating a citation. Covers locating the corpus and index, installing the index from any distribution channel (bundled/local, Google Drive, plain HTTPS/S3 links, GitHub Releases, HuggingFace), hash or structural verification, searching with coordinates (file · chunk #N), quote-verification discipline, checking index freshness, and running the local RAG API. Use whenever a course supplies a corpus (verified texts) and the student or a task needs material, evidence, or citations from it, or when the index is missing/stale and must be fetched or rebuilt.
verified: 2026-09-03
---

# RAG Corpus Operations

Many courses ship a **verified corpus**: public-domain texts, free-licensed
articles, official documents, and (often) a prebuilt **RAG index** —
embeddings + Annoy + chunks — distributed as an archive via Google Drive. This
skill is how the agent works with that machinery correctly. The golden rules
upstream (AGENTS.md) always win: never fabricate; cite only what exists; when
in doubt, say "not found" instead of inventing.

## Before operating

1. **Find the corpus tooling.** The course repo that the agent is installed in
   (or the active course subproject, see `multi-course-workspace`) typically has
   `tools/` and a `Makefile`. Read its `CORPUS.md` / `docs/GOOGLE-DRIVE.md` /
   `citations.md` first — they name the exact commands and env vars
   (`FALT_CORPUS_ROOT`, `FALT_TXT_DIR`, `FALT_INDEX_DIR`, `FALT_INDEX_URL`).
2. **Check state.** `FALT_CORPUS_ROOT/txt/` holds the texts; `index/` holds
   `annoy.index`, `embeddings.npy`, `chunks.jsonl`, `config.json`. If `index/`
   is missing or empty, the index is not installed yet.

## Installing the index (any distribution channel)

The index ships in many ways; the contract is always the same —
**one archive** (`annoy.index`, `embeddings.npy`, `chunks.jsonl`,
`config.json`) described by **`index-manifest.json`** (sizes + SHA-256 per
file, archive hash, optional `url`/`index_version`). Prefer tools provided by
the course; each channel below works with or without the course tooling.

| Channel | Where to get it | Typical command |
|---|---|---|
| Preinstalled/bundled | already in `FALT_INDEX_DIR` (local copy) | sanity check only, no download |
| Google Drive share link | `index-manifest.json` → `archive.url`, or a link the student gives | `make index-fetch URL="<link>"` / `tools/index_fetch.py` (Drive big-file flow: virus-scan page, `confirm`, `drive.usercontent.google.com`) |
| Generic HTTPS / S3 / object storage | any static URL (course website, university server, S3 presigned URL) | `curl -L "<url>" -o idx.zip` + manual SHA-256 check, then unzip |
| GitHub Releases | release asset of the course repo | `curl -L "<releases>/download/<tag>/<archive>.zip"` or `gh release download <tag>` |
| HuggingFace repository | file in an HF repo (same flow as corpus) | `curl -L "https://huggingface.co/<org>/<repo>/resolve/main/<archive>?download=true"` |

Universal procedure (independent of channel):

1. **Locate the source**: manifest `url` (any scheme), `FALT_INDEX_URL` env,
   or the student's link/path; if none, ask — never guess a URL.
2. **Validate identity**: when a manifest exists, verify **SHA-256 of the
   archive and of each file before unpacking**; mismatch = wrong/stale
   artifact → stop, report, do not use. When no hash is available (plain
   URL, HF, etc.), downgrade to structural validation: archive opens, four
   expected files present, `config.json` parses, `n_chunks`/`n_files` are
   sensible; say explicitly that the artifact was **not hash-verified**.
3. **Install atomically**: swap `index/` → `index_old/`, unpack, sanity check
   (`n_chunks`, `n_files` from `config.json`), and confirm the manifest
   `index_version` matches `config.json`'s build metadata.
4. **Record provenance**: note the channel + hash/verification level in the
   session record (and, for a course repo, keep `index-manifest.json` current).

Manual fallback (no course tooling):

```bash
mkdir -p idx && cd idx
curl -L "<url-or-link>" -o index.zip
# hash check if a manifest/hash exists:
sha256sum index.zip          # compare with manifest archive.sha256
unzip index.zip              # must yield annoy.index, chunks.jsonl, config.json, embeddings.npy
python -c "import json; c=json.load(open('config.json')); print(c['n_chunks'], c['n_files'])"
```

Windows (`certutil -hashfile index.zip SHA256` instead of `sha256sum`).

## Searching

```bash
make search QUERY="..."            # or:
python tools/rag_search.py "..." -k 5 --json
```

Rules:
- Use only **returned fragments**; each carries `file` and `chunk_id` — cite
  as `file · фрагмент #N` (never invent the number).
- Prefer the fragment that actually supports the answer; if the top hits are
  irrelevant, refine the query (terms from the corpus, not your paraphrase).
- If no relevant fragment exists, say so and — if the course permits — offer
  an authorial synthesis explicitly marked "вне корпуса" (outside the corpus),
  never a fake citation.

## Quote verification discipline

When a claim must be shown as a direct quote:
- extract the exact passage from the corpus text (verbatim, OCR-normalized:
  letter folding ё→е, whitespace/hyphenation tolerance);
- expected coverage against the file ≥ 0.92 by the course's verifier
  (`make verify` → `verification/REPORT.md`);
- fill the `#N` from `chunks.jsonl` (the chunk containing the passage);
- run `make verify`; 0 fails is the norm before presenting quotes.

## Freshness, rebuild, and the RAG API

- **Freshness check.** If the manifest's `index_version` is newer than
  `config.json`'s build metadata, or the corpus `txt/` changed (new files),
  the index is stale: fetch the newest archive from the configured channel, or
  rebuild (see the `building-agent-ready-course-repo` skill in course repos:
  chunking 1400/140 or 512/128, fastembed, Annoy angular, cluster build, new
  manifest+zip, publish to the same channel).
- **Local RAG API** (optional, interactive sessions): `make serve` →
  `tools/rag_api.py` (localhost, `GET /search?q=...&k=...`; OpenAPI in
  `tools/rag-openapi.json`). Use it for live semantic search during a session;
  never hand its raw output to the student without coordinates.
- **Missing index fallback.** Without an index you can still work statically:
  grep the corpus (`tools/quote_finder.py`, or ripgrep over `txt/`), cite with
  coordinates after locating the chunk, and tell the student the index is not
  installed yet (`make index-fetch` / fetch from the configured channel).

## Anti-patterns (never)

- Inventing a `#N` or a fragment text that search did not return.
- Quoting an author whose text is not in the corpus (©, commercial editions)
  — that is authorial synthesis, labeled "вне корпуса".
- Serving stale fragments after the corpus changed; re-check freshness first.
- Presenting the index as authoritative when `config.json`/manifest mismatch
  (sizes, SHA-256, file count) — and never claiming hash verification when
  the channel provided no hash (say "not hash-verified").
- Hard-coding one distribution channel (e.g. "always Google Drive"): read the
  manifest/course docs to learn where the index actually lives.