#!/usr/bin/env bash
# Phase 4 experiment matrix. Run from the repo root after:
#   .venv/Scripts/python.exe -m evals.build_noise_collections build
#
# E1/E2: noise level (0x, 1x, 10x, 50x) x FlashRank rerank (on, off), plus two extra
#        repeats of 0x and 50x with rerank on (run-to-run variance).
# E3:    guardrail modes (embeddings, promptguard, safeguard, layered) on the in-house
#        set (guardrails.json) and the external sample (external_prompt_injections.json).
#
# RAG runs disable guardrails so only retrieval/generation vary. Each run is scored right
# after it finishes. A failed run is logged and the matrix continues.

set -u
APP_PY=.venv/Scripts/python.exe
EVAL_PY=.venv-evals/Scripts/python.exe
export LOCAL_MODE=true LANGSMITH_TRACING=false PYTHONIOENCODING=utf-8 PYTHONWARNINGS=ignore

score_latest() {
  local f
  f=$(ls -t evals/results/*_"$1"_pipeline.json 2>/dev/null | head -1)
  [ -n "$f" ] && "$EVAL_PY" -X utf8 evals/score.py "$f" | grep -aE '^  [a-z_@0-9]+ |^  judge' || echo "!! scoring failed for $1"
}

rag_run() {  # label collection rerank
  echo "=== RAG $1 ($2, rerank=$3) $(date +%H:%M:%S)"
  QDRANT_COLLECTION=$2 RERANK_ENABLED=$3 GUARDRAILS_ENABLED=false \
    "$APP_PY" -X utf8 -m evals.run_pipeline --label "$1" --dataset rag | grep -aE '^\[|Saved' | tail -3 \
    || echo "!! run failed: $1"
  score_latest "$1"
}

guard_run() {  # mode dataset-file short-name
  echo "=== GUARD $1 on $3 $(date +%H:%M:%S)"
  GUARDRAILS_ENABLED=true GUARDRAILS_MODE=$1 \
    "$APP_PY" -X utf8 -m evals.run_pipeline --label "g-$1-$3" --dataset guardrails --guard-dataset "$2" | grep -aE 'Saved' \
    || echo "!! run failed: g-$1-$3"
  score_latest "g-$1-$3"
}

for n in 0x 1x 10x 50x; do
  rag_run "n$n-rr-on" "exp_noise_$n" true
  rag_run "n$n-rr-off" "exp_noise_$n" false
done
for rep in 2 3; do
  rag_run "n0x-rr-on-rep$rep" exp_noise_0x true
  rag_run "n50x-rr-on-rep$rep" exp_noise_50x true
done

for mode in embeddings promptguard layered safeguard; do
  guard_run "$mode" guardrails.json inhouse
  guard_run "$mode" external_prompt_injections.json external
done
echo "=== DONE $(date +%H:%M:%S)"
