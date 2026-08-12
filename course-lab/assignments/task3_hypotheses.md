<!-- SPDX-FileCopyrightText: 2026 top-papers-graph contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Task 3 — multimodal temporal hypothesis generation

This repository snapshot adds a dedicated Task 3 pipeline for **vision-language assisted, evidence-grounded hypothesis generation** over a **temporal knowledge graph**.

## What the new module does

Input options:
- free-text query
- Task 1 trajectory YAML
- explicit list of DOI / URL / PMID / PMCID / arXiv / OpenAlex identifiers
- prebuilt `processed_papers/` directory for offline smoke / deterministic runs

Output bundle:
- selected paper metadata
- acquired PDFs and processed paper folders
- multimodal chunk registry (`text`, `page`, `table`, `formula`)
- chunk embeddings + file-backed Annoy sidecar index
- temporal KG + event stream
- multimodal triplets extracted from text / table / image-page evidence
- temporal link predictions (PyTorch Geometric Temporal when available, otherwise TGNN/TGN heuristic fallback)
- ranked, justified, testable hypotheses in JSON and Markdown

## Why this Task 3 design is stronger than a naive baseline

Compared with a minimal `papers -> chunks -> triplets -> hypotheses` chain, the new pipeline adds:

1. **Dual acquisition mode**  
   Works both online (search/acquire PDFs) and offline (`processed_papers`) for reproducible classroom and CI runs.

2. **Multimodal chunk registry instead of raw text-only chunks**  
   The pipeline promotes page, table and formula fragments to first-class retrieval objects, so VL evidence can participate directly in retrieval and scoring.

3. **Annoy sidecar with graceful fallback**  
   Even if the optional `annoy` package is missing, vectors are still saved and searched through a NumPy fallback path. The artifact contract stays stable.

4. **Two temporal link-prediction paths**  
   - preferred: `torch-geometric-temporal`
   - fallback: existing recency-aware TGNN/TGN-style predictor already present in the repo

5. **Multimodal triplet extraction layer**  
   VL descriptions, tables and OCR text are fused before temporal triplet extraction, instead of treating images as detached annotations.

6. **Time-aware candidate analysis**  
   Hypothesis generation is explicitly conditioned on:
   - temporal ordering (`strengthening`, `weakening`, `persistent`, `predicted_missing_link`)
   - yearly counts
   - first/last appearance
   - predicted future or missing links

7. **Evidence-aware ranking**  
   Final ranking combines:
   - graph candidate score
   - temporal link-prediction score
   - multimodal support
   - retrieval support from Annoy neighbors
   - rule-based scientific quality reward

## New code entrypoints

Python API:
- `scireason.task3_hypothesis_generation.prepare_task3_hypothesis_bundle`
- `scireason.pipeline.task3_hypothesis_generation.prepare_task3_hypothesis_bundle`

CLI:
- `top-papers-graph prepare-task3-hypotheses`
- `top-papers-graph task3-bundle`


## Task 3 notebook

This snapshot now also includes a working notebook pair:
- `notebooks/task3_multimodal_temporal_hypothesis_generation_colab.ipynb`
- `notebooks/task3_multimodal_temporal_hypothesis_generation_colab.ipynb`

The notebook supports:
- query / YAML / commands / processed ZIP inputs
- local Hugging Face / Transformers VLM selection
- g4f routing for text and VLM paths
- offline A/B HTML generation for expert review
- expert artifact ZIP packaging


## Headless smoke mode for CI / local validation

Task 3 notebooks now support environment-driven defaults so they can be executed non-interactively with `nbclient` or `jupyter nbconvert --execute`.

Important environment variables:
- `TPG_REPO_DIR` — explicit repository root; now takes precedence over auto-discovered archives under `/mnt/data`
- `TASK3_NOTEBOOK_SMOKE=1`
- `TASK3_NOTEBOOK_SMOKE_PROCESSED_DIR=/abs/path/to/processed_papers`
- `TASK3_NOTEBOOK_SMOKE_OUT_DIR=/abs/path/to/output`
- `TASK3_NOTEBOOK_SMOKE_QUERY=...`

In smoke mode the notebook pre-fills its widgets, switches to an offline-safe configuration (`mock` text route, `hash` embeddings, `none` VLM by default) and writes the usual Task 3 artifacts without requiring manual widget interaction.

## Installation

Minimal Task 3 extra:

```bash
pip install -e ".[task3]"
```

This extra includes:
- multimodal runtime (`transformers`, `qwen-vl-utils`, `pymupdf`, `pillow`)
- `g4f`
- temporal helpers (`python-dateutil`, `dateparser`)
- vector index (`annoy`)
- temporal GNN stack (`torch-geometric`, `torch-geometric-temporal`)

## Example runs

### 1) Query-driven run

```bash
top-papers-graph task3-bundle \
  --query "temporal knowledge graph multimodal hypothesis generation" \
  --top-papers 12 \
  --top-hypotheses 8 \
  --multimodal \
  --vlm \
  --link-backend auto
```

### 2) Run from Task 1 trajectory

```bash
top-papers-graph task3-bundle \
  --trajectory bundles/my_topic/task1_trajectory.yaml \
  --top-papers 10 \
  --top-hypotheses 6
```

### 3) Run from explicit identifiers

```bash
top-papers-graph task3-bundle \
  --identifiers "10.1000/example-doi, https://arxiv.org/abs/2401.12345" \
  --top-hypotheses 5
```

### 4) Fully offline run from prebuilt processed papers

```bash
top-papers-graph task3-bundle \
  --processed-dir runs/task2_validation/example/processed_papers \
  --query "offline smoke" \
  --edge-mode cooccurrence \
  --link-backend heuristic \
  --no-vlm
```

## Local HF VL models and g4f support

Task 3 reuses the repository's existing VLM selection layer.

Examples:

### Local Hugging Face model

```bash
top-papers-graph task3-bundle \
  --query "multimodal science reasoning" \
  --vlm-backend qwen2_vl \
  --vlm-model-id "Qwen/Qwen2.5-VL-3B-Instruct"
```

### g4f-backed VL path

```bash
top-papers-graph task3-bundle \
  --query "multimodal science reasoning" \
  --llm-provider g4f \
  --g4f-model deepseek-r1 \
  --vlm-backend g4f
```

## Main artifacts inside a Task 3 bundle

```text
<bundle>/
  query.json
  papers_selected.json
  acquire_results.json
  paper_records.json
  chunk_registry.jsonl
  annoy/
    annoy_manifest.json
    item_ids.json
    item_metadata.jsonl
    vectors.npy
    chunks.ann              # only when Annoy is installed
  automatic_graph/
    temporal_kg.json
    events.jsonl
    multimodal_triplets.jsonl
    link_predictions.json
    vlm_candidate_analysis.jsonl   # optional
  hypotheses_candidates.json
  hypotheses_ranked.json
  hypotheses_ranked.md
  task3_manifest.json
```

## Implementation notes

- If `torch-geometric-temporal` is unavailable, Task 3 does **not** fail; it transparently falls back to the lighter TGNN/TGN predictor.
- If `annoy` is unavailable, retrieval still works from `vectors.npy` via NumPy scoring.
- If a PDF cannot be acquired or parsed, the pipeline stores a metadata-only paper fallback, preserving continuity of the hypothesis workflow.
- If VLM analysis fails for a candidate, Task 3 continues and keeps the non-VL evidence.

## Recommended workflow with the existing tasks

1. **Task 1** — define the topic / trajectory / expert framing.
2. **Task 2** — validate temporal graph structure and evidence quality.
3. **Task 3** — generate and rank falsifiable hypotheses using the validated temporal + multimodal signals.
