---
name: teaching-offline
description: Teach from the accepted corpus on disk with scoped search and the skills already loaded, when there is no local model, no network, and nothing new to generate - and say plainly that any claim not backed by a corpus fragment is (вне корпуса). Use when the student is offline, when a session must run with no outbound connection, when the corpus is installed but no material may be invented, or when someone asks what the harness can do without a model provider.
verified: 2026-09-30
---

# Teaching Offline

The offline path is **corpus + scoped search + the skills on disk**. That is
the whole of it, and the thing to say first:

**There is no bundled local model.** The design mentions Ollama as a possible
host; it is **not installed here**, no model is packaged with the harness, and
this skill must never claim one. Whatever generates the prose is the host the
student already runs - whatever that is. If there is no host that can generate
text at all, there is no offline session, and saying so is the right answer.

## When to Use

- The student is offline or the network is unavailable
- A session must run with no outbound connection
- The corpus is installed and only retrieval plus skill prose is needed
- Someone asks what the harness can do without a model provider

## When NOT to Use

- **Acquiring a corpus** - if the corpus is missing, fetching it is the
  session's first action, and that needs the network
- **Teaching from an unverified corpus** - an unverified corpus is never called
  verified
- **When the host cannot produce text** - name the blocker; do not fake a
  session

## The offline loop

```
1. Confirm the corpus   corpus.status() / tools/status.py
2. Load and verify      corpus.load_manifest(), corpus.verify()
3. Scoped search        retrieval.search_chunks() / retrieval.search_text()
4. Teach with the skills on disk
5. Mark everything ungrounded as (вне корпуса)
```

### 1. Confirm the corpus is installed

`corpus.status()` reports what is present. A corpus is **both** artifacts - the
index (`annoy.index`, `embeddings.npy`, `chunks.jsonl`, `config.json`) **and**
the texts (`txt/*.txt`). An index without texts can retrieve a fragment but
cannot verify a quotation, so a quotation from it would be unverifiable and
must not be offered as one.

If the corpus is missing, that is the session's first action and it needs the
network - report it as a blocker for the offline session rather than working
around it with whatever text happens to be on disk.

### 2. Search inside the corpus, not the machine

`retrieval.search_chunks(...)` and `retrieval.search_text(...)` with
`retrieval.resolve_scope(...)` bound the search to the corpus and the active
scope. `retrieval.METHODS` names the available approaches; `MAX_EXCERPT`,
`MAX_HIT_TEXT` and `MAX_LIMIT` bound what comes back, so a search cannot
silently become a whole-file dump.

Quote only what came back, with its coordinates - «файл · фрагмент #N» - and
check it with `retrieval.verify_quote(...)` before you present it as a
quotation.

### 3. Teach with what is already loaded

The skills are on disk. `explaining-concepts`, `breaking-down-assignments`,
`assessing-understanding`, `giving-feedback`, `providing-adaptive-scaffolding`,
`diagnosing-errors`, `scheduling-reviews` - all of them are instructions, and
they work offline because they are text the host already has.

What each needs from the student, it asks for.

## What degrades

State these plainly to the student before starting:

- **No new material generation beyond the corpus.** Anything not in a verified
  fragment is not course material. An explanation of a corpus gap is a
  supplement, and it is marked.
- **Every claim is (вне корпуса) if the corpus is absent.** With no corpus
  consulted, there is no grounding, and the marking rule is absolute: never let
  unmarked text look corpus-grounded when the corpus was never read.
- **No corpus acquisition.** `corpus.acquire()` and `corpus.download()` need
  the network. An offline session cannot install or update a corpus.
- **No update, no course fetch, no catalog browse.** `make update`,
  `make course-update`, and the `education-club` MCP all need the network.
- **No network calls of any kind.** The harness opens no socket; that is a
  design property, not a temporary state.
- **No model of its own.** There is no local model, and the harness does not
  fetch one. If the student's host cannot generate text, the session cannot run.

The honest framing, in Russian:

```
Сейчас у нас есть только корпус на диске и навыки агента. Нового
вне корпуса я не придумываю: всё, что не подтверждено фрагментом,
отмечу как (вне корпуса). Сеть не нужна - и не подключается.
```

## When back online

Sync is a deliberate step, not an automatic one:

```
make corpus-fetch        # только если корпус не установлен или устарел
make update-check        # exit 10 = есть обновление харнесса
make course-update-check # exit 10 = есть обновление курса
```

- Fetch or update only after the network is confirmed available, and never on
  the strength of one failure report. A timeout is a moment, not a fact.
- Re-verify before teaching from the refreshed corpus. A hash mismatch is a
  hard stop, not a warning.
- `make update` touches nothing under `courses/`, `progress/`, `.botai/` or
  `dist/`, so nothing the student wrote is lost. A file whose hash differs from
  the recorded install is a local edit: kept and reported, never overwritten.
- Restart the session after an update that touched `AGENTS.md`, `opencode.json`,
  `.opencode/`, or a loaded skill - configuration is read once at startup.

## Limitations

- **No bundled local model.** The host provides whatever model it has, or the
  session does not happen. This skill never claims a model ships with the
  harness.
- **No corpus, no grounding.** Without an installed corpus every statement is
  (вне корпуса) and must be marked. Teaching from a collection assembled ad hoc
  from whatever text exists on the machine is exactly what rule 9 forbids.
- **No new content beyond the corpus.** Examples and supplements may be written,
  and they are marked as supplements with their source.
- **Retrieval is scoped, not open.** It searches the corpus under `resolve_scope`;
  it is not a general search engine, and it does not search the filesystem or
  the web.
- **The skills are instructions, not capability.** A skill that names a module
  (`corpus.acquire`, `retrieval.search_chunks`) does not make that module able
  to run offline if it needs a network.
- `speech_interface.synthesize()` raises `SPEECH_BACKEND_UNAVAILABLE` offline.
  There is no voice, and saying otherwise is a false claim.

## References

- `corpus.py` - `status`, `load_manifest`, `verify`, `manifest_hash`,
  `installed_dir`, `corpus_root`
- `retrieval.py` - `search_chunks`, `search_text`, `resolve_scope`,
  `verify_quote`, `validate_citation`, `METHODS`, `MAX_EXCERPT`
- `corpus-acquisition` - what must happen when the network returns
- `keeping-harness-and-course-current` - the update path and its safety rules
- `explaining-concepts` - the (вне корпуса) marking rule this skill depends on
- `teaching-offline` verification: `python scripts/cli.py policy-check --course <slug> --assignment <id> --level <LEVEL>`

## Verify or update the progress record

1. Confirm the student's level and the corpus status before the session; a
   missing corpus is reported as a blocker, not worked around.
2. Respect the recorded delivery preference as in any session - offline is not
   a different policy.
3. Record: that the session was offline, whether the corpus was installed and
   verified, which fragments were quoted with their coordinates, and how much
   material was (вне корпуса).
4. Never record an unverified or absent corpus as verified, and never record a
   generated claim as corpus-grounded.
5. Deliver all student-facing material in Russian
   (`language-and-translation`), and carry `verified:`.

## Last Validated

2026-09-30. Procedure current as of this date; re-verify when `corpus.py`,
`retrieval.py`, or the harness's network policy changes. Added this revision:
the explicit no-bundled-model statement, the corpus-and-scoped-search loop,
what degrades offline with the (вне корпуса) rule, and the deliberate sync
when back online.
