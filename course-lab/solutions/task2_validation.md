# Task 2 — validation solution key (graded material)

This file is an **answer key**. Do not read it before the student has made
their own attempt. It is here for checking after the attempt, and for the
operator's integrity test.

## What a correct validation run produces

A Task 2 validation bundle over a temporal graph, with expert verdicts that are
traceable to evidence. The minimal expected artifacts are those named in
`assignments/task2_validation.md`:

- `chunk_registry.jsonl` — text/page/table/formula chunks from the papers;
- `temporal_kg.json` — nodes and edges with the v3 temporal schema fields
  (`start_date`, `end_date`, `valid_from`, `valid_to`, `time_source`);
- `events.jsonl` — dated events that the temporal edges are anchored to;
- a per-edge expert verdict, one of the review-schema enum values (see below),
  each with a comment naming the evidence fragment.

## Verdict vocabulary

The reference schema is `data/experts/mm_ab_reviews/task3_mm_ab_review_schema.json`.
For edge validation the meaningful outcomes are:

- `correct` — the edge is supported by the cited evidence;
- `incorrect` — the edge contradicts or is not supported by the evidence;
- `ambiguous` — the evidence is unclear and the edge needs a time/scope fix;
- `missing` — a true relationship is absent from the graph.

For the temporal fields specifically, a verdict must also record whether the
date is a publication date vs. an experiment date, and the time granularity.

## What "done" means

Per the week 10 rubric and task instructions, a Task 2 result is acceptable
when:

1. every verdict is traceable to a fragment of the source (page, table,
   figure, or quote);
2. the temporal fields follow the v3 schema (not the legacy `time_interval`);
3. the expert distinguished `start_date` / `end_date` (when observed) from
   `valid_from` / `valid_to` (when the claim holds);
4. for visual evidence, `mm_verdict` and `mm_rationale` are filled;
5. the run was offline-first (no `--remote-lookup`) unless remote enrichment
   was explicitly requested.

## Common failure to look for

- date of publication mistaken for date of the experiment;
- figure/table facts dropped, leaving only text evidence;
- edges with no `time_source` or with a bare `time_interval`;
- leakage: evidence dated after the claim's cutoff used to support the claim.
