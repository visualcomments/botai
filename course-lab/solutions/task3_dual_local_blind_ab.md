# Task 3 — dual local blind A/B solution key (graded material)

This file is an **answer key**. Do not read it before the student has made
their own attempt. It is here for checking after the attempt, and for the
operator's integrity test.

## What a correct blind A/B run produces

Two Task 3 bundles (variant α and variant β) over the **same** processed
papers, plus an anonymized review package and a **separate** owner key. The
expected artifacts are those named in `assignments/task3_dual_local_blind_ab.md`:

- `task3_dual_local_model_review_offline_ab.html` — anonymized, for the expert;
- `expert_dual_model_blind_review_bundle.zip` — what the expert receives;
- `task3_dual_local_model_blind_key.json` — the mapping of anonymized system to
  real model. **Never sent to the expert.**
- two Task 3 bundles: `variant_alpha/...` and `variant_beta/...`.

## What "done" means

1. Both variants ran on the **same** `processed_papers` input — the comparison
   is fair by construction.
2. The expert review is blind: no identifier in the HTML/zip reveals which
   model is α and which is β.
3. The owner key exists and is kept out of the expert bundle.
4. The review schema fields are populated per case: `pair_id`,
   `preferred_variant`, `better_evidence`, `better_temporal`, `global_verdict`
   (enum: A, B, tie, skip), plus `mm_verdict`/`mm_rationale` for
   multimodal-hard cases.

## Common failure to look for

- the two variants did not share the same processed input (unfair comparison);
- the owner key leaked into the expert zip or the HTML;
- expert verdicts filled with real model names instead of A/B;
- `skip` overused because the cases were too hard — the hard subset is meant
  to be answered, with the `global_verdict` reflecting uncertainty, not avoided.
