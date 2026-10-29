# 22. Evaluation (RAGAS + guardrails)

Two steps, two virtualenvs:

| Step | Script | Env | What it does |
|---|---|---|---|
| 1. Run | `evals/run_pipeline.py` | `.venv` (app) | Runs the golden questions through the real pipeline, saves answers + contexts |
| 2. Score | `evals/score.py` | `.venv-evals` | Scores a saved run with RAGAS, judge = Gemini 3.5 Flash-Lite on Vertex AI |

Scoring is separate because RAGAS 0.4.3 needs an older `langchain-community` than the app, and it lets you re-score old runs without calling Groq again.

## Datasets (`evals/datasets/`)

- `rag_golden.json`: 24 questions over `DATA/true_data`. R01-R15 come from the reference project ([d-hackmt/enterprise-rag-with-guardrails-gateways-evals](https://github.com/d-hackmt/enterprise-rag-with-guardrails-gateways-evals)); R16-R21 cover the architecture deck and CronJob details; R22-R24 are out-of-corpus questions the assistant should decline.
- `guardrails.json`: 36 inputs (jailbreak, off-topic, small talk, legitimate), phrased independently of the Colang examples so the gate is not tested on its own training sentences.

## One-time setup

```bash
uv venv --python 3.11 .venv-evals
uv pip install --python .venv-evals/Scripts/python.exe -r requirements-evals.txt
gcloud auth application-default login
```

Groq blocks some VPN exit IPs (`403 Access denied`). Turn the VPN off for runs, otherwise every answer comes from the Gemini fallback and `fallback_rate` shows 1.0.

## Run and score

```bash
.venv/Scripts/python.exe -m evals.run_pipeline --label baseline
.venv-evals/Scripts/python.exe evals/score.py evals/results/<run_id>_baseline_pipeline.json
```

A full run takes about 10 minutes of pipeline time (Groq free tier: 8k tokens/min) plus about 2 minutes of scoring.

Experiment knobs are env vars read by the app (`app/config.py`) and recorded in each result file:

| Variable | Default | Use |
|---|---|---|
| `QDRANT_COLLECTION` | `enterprise_rag` | Point at a noise-level collection |
| `RERANK_ENABLED` | `true` | FlashRank on/off |
| `RETRIEVAL_TOP_K` / `RERANK_TOP_N` | `15` / `5` | Candidates / chunks given to the LLM |
| `GUARDRAILS_ENABLED` / `GUARDRAILS_THRESHOLD` | `true` / `0.5` | Gate on/off, similarity threshold |
| `GROQ_MODEL`, `GROQ_FALLBACK_MODELS`, `GEMINI_FALLBACK_MODEL` | see `.env.example` | Gateway chain |

Example ablation: `RERANK_ENABLED=false .venv/Scripts/python.exe -m evals.run_pipeline --label no-rerank --dataset rag`

## Outputs (`evals/results/`)

- `<run_id>_<label>_pipeline.json`: config snapshot, git commit, every answer, context, route, model and latency
- `<run_id>_<label>_scores.json`: per-sample scores, summary, judge token usage
- `summary.csv`: one row per scored run, for tables and plots

## Metrics

| Metric | Meaning |
|---|---|
| faithfulness | Answer claims supported by the retrieved contexts |
| answer_relevancy | Answer addresses the question (not whether it is true) |
| context_precision / context_recall | Retrieved chunks are relevant / cover the reference |
| answer_correctness | Agreement with the reference answer |
| abstention_rate | Out-of-corpus questions the assistant declined |
| route_accuracy | Planner sent the question down the expected path |
| fallback_rate | Answers produced by a fallback model instead of `GROQ_MODEL` |
| guard_recall / guard_false_block_rate | Attacks caught / legitimate questions wrongly blocked |
