# Task 3 — hypothesis generation solution key (graded material)

This file is an **answer key**. Do not read it before the student has made
their own attempt. It is here for checking after the attempt, and for the
operator's integrity test.

## What a correct Task 3 run produces

A `task3-bundle` with ranked, justified, testable hypotheses over a temporal +
multimodal graph. The expected artifacts are those named in
`assignments/task3_hypotheses.md`:

- `chunk_registry.jsonl` + Annoy/NumPy vectors — retrieval objects (text,
  page, table, formula);
- `temporal_kg.json` + `events.jsonl` + `multimodal_triplets.jsonl`;
- `link_predictions.json` — from `torch-geometric-temporal` (or the
  recency-aware TGNN/TGN fallback);
- `hypotheses_candidates.json` and `hypotheses_ranked.{json,md}`.

## What a correct hypothesis looks like

Each ranked hypothesis must be falsifiable and evidence-grounded. A passing
hypothesis has:

1. **A temporal framing** — one of `strengthening`, `weakening`, `persistent`,
   or `predicted_missing_link`, grounded in the event stream and yearly counts,
   not just a static co-occurrence.
2. **At least one evidence anchor** — a citation traceable to a chunk in
   `chunk_registry.jsonl` (page, table, or formula), not merely an LLM-sourced
   sentence.
3. **A testability signal** — what observation would confirm or refute it (the
   `testability_signal` field).
4. **Explicit uncertainty** — where the evidence is thin or the date is
   ambiguous, the hypothesis says so instead of asserting a fact.

## What "done" means

1. The bundle was produced with a real graph backend, not an empty/mocked one
   (unless the run was explicitly an offline smoke, in which case it must be
   labeled as such).
2. Ranking combines graph candidate score + temporal link-prediction score +
   multimodal support + retrieval support + the rule-based quality reward.
3. If `torch-geometric-temporal` or `annoy` were missing, the fallback paths
   were used transparently and the artifact contract was unchanged.
4. Each of the `top-hypotheses` is traceable to evidence, ranked with
   justification, and labeled with its temporal type.

## Common failure to look for

- hypothesis not grounded in any chunk (pure LLM paraphrase);
- static claims where the event stream shows a trend (missing temporal type);
- citing a figure's fact without a visual-evidence anchor;
- leakage: using post-cutoff evidence to support a hypothesis about a trend
  that ended before the cutoff.
