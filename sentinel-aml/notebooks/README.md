# Notebooks

No exploratory notebooks are committed: all analysis is reproducible as scripts.

| Analysis | Script |
|---|---|
| Data generation and quality | `python -m app.pipeline --generate --ingest` (report in `evaluation/reports/pipeline_run.json`) |
| Model training, calibration, operating curve | `python -m app.pipeline --train` → `evaluation/reports/ml_training_metrics.json` |
| Retrieval evaluation | `python evaluation/rag_eval.py` |
| Agent and adversarial evaluation | `python evaluation/agent_eval.py` |
| Load test | `python evaluation/system_eval.py` |

Suggested first notebook when reverse-engineering: load `ml_training_metrics.json` and plot the operating curve and per-rule recall.
