---
name: rag-corpus-operations
description: Work with a course corpus through its RAG index — embeddings, chunks, and search — without ever fabricating a citation. Covers locating the corpus and index, installing the index from Google Drive (manifest + SHA-256), searching with coordinates (file · chunk #N), quote-verification discipline, checking index freshness, and running the local RAG API. Use whenever a course supplies a corpus (verified texts) and the student or a task needs material, evidence, or citations from it, or when the index is missing/stale and must be fetched or rebuilt.
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

## Installing the index (Google Drive, no server)

```bash
make index-fetch URL="<share link>"        # or:
python tools/index_fetch.py                # reads url from index-manifest.json / FALT_INDEX_URL
```

What the tool does and must verify:
- downloads the archive (Drive big-file flow: virus-scan page, `confirm`,
  `drive.usercontent.google.com`);
- **checks SHA-256 of the archive and each file against the manifest before
  unpacking** — mismatch means a wrong/stale archive: stop, report, do not use;
- atomically swaps `index/` → `index_old/`, unpacks, verifies sanity
  (`n_chunks`, `n_files` from `config.json`).

After install, confirm: `config.json` says `n_chunks`/`n_files` matching the
manifest; if the manifest says a newer `index_version`, flag the stale index.

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

- **Freshness check.** If `index_version` (manifest) is newer than
  `config.json`'s build date, or the corpus `txt/` changed (new files), the
  index is stale: fetch the new archive, or rebuild (see the
  `building-agent-ready-course-repo` skill in course repos: chunking
  1400/140 or 512/128, fastembed, Annoy angular, cluster build, new
  manifest+zip).
- **Local RAG API** (optional, interactive sessions): `make serve` →
  `tools/rag_api.py` (localhost, `GET /search?q=...&k=...`; OpenAPI in
  `tools/rag-openapi.json`). Use it for live semantic search during a session;
  never hand its raw output to the student without coordinates.
- **Missing index fallback.** Without an index you can still work statically:
  grep the corpus (`tools/quote_finder.py`, or ripgrep over `txt/`), cite with
  coordinates after locating the chunk, and tell the student the index is not
  installed yet (`make index-fetch`).

## Anti-patterns (never)

- Inventing a `#N` or a fragment text that search did not return.
- Quoting an author whose text is not in the corpus (©, commercial editions)
  — that is authorial synthesis, labeled "вне корпуса".
- Serving stale fragments after the corpus changed; re-check freshness first.
- Presenting the index as authoritative when `config.json`/manifest mismatch
  (sizes, SHA-256, file count).