#!/usr/bin/env bash
# Responder-prompt variants on the same setup as the Phase 4 re-ranked runs, so results pair with
# n0x-rr-on (3 runs) and n50x-rr-on. Usage: bash evals/run_prompt_variant.sh <prompt> <label-prefix>
#   bash evals/run_prompt_variant.sh complete v2    # labels v2-n0x-rr-on[-repN], v2-n50x-rr-on
#   bash evals/run_prompt_variant.sh detailed v3
# v1-v3 and v2r used pure re-ranking: prefix with RERANK_KEEP_DENSE=0 to reproduce them; v2k used the default (2)
set -u
PROMPT=$1; PREFIX=$2
APP_PY=.venv/Scripts/python.exe
EVAL_PY=.venv-evals/Scripts/python.exe
run() {  # label collection
  echo "=== $1 $(date +%H:%M:%S)"
  QDRANT_COLLECTION=$2 RERANK_ENABLED=true GUARDRAILS_ENABLED=false RESPONDER_PROMPT=$PROMPT \
    "$APP_PY" -X utf8 -m evals.run_pipeline --label "$1" --dataset rag | grep -aE 'Saved' || echo "!! run failed: $1"
  f=$(ls -t evals/results/*_"$1"_pipeline.json 2>/dev/null | head -1)
  [ -n "$f" ] && "$EVAL_PY" -X utf8 evals/score.py "$f" | grep -aE '^  [a-z_@0-9]+ ' || echo "!! scoring failed: $1"
}
run "$PREFIX-n0x-rr-on" exp_noise_0x
run "$PREFIX-n0x-rr-on-rep2" exp_noise_0x
run "$PREFIX-n0x-rr-on-rep3" exp_noise_0x
run "$PREFIX-n50x-rr-on" exp_noise_50x
echo "=== DONE $(date +%H:%M:%S)"
