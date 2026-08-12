<!-- SPDX-FileCopyrightText: 2026 top-papers-graph contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Task 2 validation bundle

This repository snapshot includes:

- direct notebook support via `pip install -e ".[task2_notebook]"` (g4f включён по умолчанию)
- alias extras: `.[multimodal]`, `.[notebook_viz]`, `.[notebook]`, `.[colab]` (`.[multimodal]` теперь тоже включает g4f)
- compatibility module `scireason.task2_validation` for legacy notebooks
- CLI aliases `top-papers-graph prepare-task2-validation` and `top-papers-graph task2-bundle`
- temporal review schema v3 (`start_date`, `end_date`, `valid_from`, `valid_to`)
- backward compatibility with legacy `time_interval`
- notebook `notebooks/task2_temporal_graph_validation_colab.ipynb` that works without patching repository files from inside the notebook

Recommended entrypoint for experts:

```bash
pip install -e ".[task2_notebook]"
# or: pip install -e ".[temporal,multimodal,notebook_viz]"
```

Both commands install `g4f` by default.

Then open the notebook and run it top-to-bottom.

## Что нового в рабочем ноутбуке

- Вкладки для просмотра **эталонного** и **автоматически сгенерированного** графов.
- Внутри каждого графа доступны отдельные окна:
  - **Assertions** — таблица утверждений/рёбер с поиском.
  - **Визуализация** — граф в notebook.
- Раздел **Валидация эксперта** позволяет:
  - выставлять verdict по каждому ребру;
  - добавлять комментарии;
  - корректировать временные параметры (`start_date`, `end_date`, `valid_from`, `valid_to`, `time_source`);
  - сохранять готовые `CSV`, `JSON` и `ZIP` с результатами валидации.


По умолчанию Task 2 bundle теперь работает в надёжном offline-first режиме: notebook и CLI не блокируются на сетевом поиске метаданных и PDF. Чтобы включить удалённое обогащение, передайте `--remote-lookup`.

Дополнительно OCR-воркер PP-Structure теперь защищён таймаутом `PADDLEOCR_WORKER_TIMEOUT_SECONDS` (по умолчанию `90`). Если PaddleOCR зависает на конкретном PDF, pipeline автоматически переключается на локальный PyMuPDF fallback и продолжает сборку bundle.


## Обновление Task 2 v2

Теперь эксперт во втором задании валидирует не только верность ребра, но и его пригодность для downstream-конвейера генерации проверяемых гипотез. Рекомендуется заполнять дополнительные поля: `semantic_correctness`, `evidence_sufficiency`, `scope_match`, `hypothesis_role`, `hypothesis_relevance`, `testability_signal`, `causal_status`, `severity`, `evidence_before_cutoff`, `leakage_risk`, `time_type`, `time_granularity`, `time_confidence`, а для визуальных evidence — `mm_verdict` и `mm_rationale`.
