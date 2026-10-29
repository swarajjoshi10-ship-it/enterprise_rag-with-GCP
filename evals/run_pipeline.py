"""
Eval step 1 - run the system under test and save its outputs (no scoring here).

Runs in the app virtualenv (.venv). Scoring happens separately in .venv-evals
(evals/score.py), so RAGAS never has to share dependencies with the app and old
runs can be re-scored without calling Groq again.

Modes:
  inproc  imports the app and calls the same /query handler in this process.
          Experiment knobs come from env vars (QDRANT_COLLECTION, RERANK_ENABLED,
          RETRIEVAL_TOP_K, RERANK_TOP_N, GUARDRAILS_ENABLED, GROQ_MODEL, ...).
  http    calls a deployed backend (--backend-url) for end-to-end checks and latency.

Usage (from the repo root):
  .venv/Scripts/python.exe -m evals.run_pipeline --label baseline
  .venv/Scripts/python.exe -m evals.run_pipeline --mode http --backend-url https://... --label cloud
"""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"


def _route(thought_process: list[str]) -> str:
    joined = " ".join(thought_process or []).lower()
    if "guardrails fired" in joined:
        return "guardrail"
    if "error" in joined:
        return "error"
    if "intent: technical" in joined:
        return "retrieve"
    if "conversational" in joined:
        return "conversational"
    return "unknown"


def _models(thought_process: list[str]) -> dict:
    found = {}
    for step in thought_process or []:
        for role in ("Planner", "Responder"):
            if step.startswith(f"{role} model: "):
                found[role.lower()] = step.split(": ", 1)[1]
    return found


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=EVALS_DIR, text=True).strip()
    except Exception:
        return "unknown"


def _config_snapshot(mode: str, backend_url: str | None) -> dict:
    if mode == "http":
        return {"mode": "http", "backend_url": backend_url}
    from app.config import settings
    return {
        "mode": "inproc",
        "qdrant_collection": settings.QDRANT_COLLECTION,
        "retrieval_top_k": settings.RETRIEVAL_TOP_K,
        "rerank_enabled": settings.RERANK_ENABLED,
        "rerank_top_n": settings.RERANK_TOP_N,
        "responder_prompt": settings.RESPONDER_PROMPT,
        "guardrails_enabled": settings.GUARDRAILS_ENABLED,
        "guardrails_threshold": settings.GUARDRAILS_THRESHOLD,
        "groq_model": settings.GROQ_MODEL,
        "groq_fallback_models": settings.GROQ_FALLBACK_MODELS,
        "gemini_fallback_model": settings.GEMINI_FALLBACK_MODEL,
        "pdf_parser": settings.PDF_PARSER,
    }


class _Client:
    """Same request/response shape for both modes: the /query handler's JSON."""

    def __init__(self, mode: str, backend_url: str | None):
        self.mode = mode
        self.backend_url = backend_url
        if mode == "inproc":
            from app.guardrails import initialize_rails
            from app.main import QueryRequest, query
            initialize_rails()
            self._query, self._request = query, QueryRequest

    def query(self, q: str, thread_id: str) -> dict:
        if self.mode == "inproc":
            return self._query(self._request(q=q, thread_id=thread_id))
        import requests
        r = requests.post(f"{self.backend_url}/query", json={"q": q, "thread_id": thread_id}, timeout=180)
        r.raise_for_status()
        return r.json()


def run_rag(client: _Client, samples: list[dict], delay: float, run_id: str) -> list[dict]:
    out = []
    for i, s in enumerate(samples):
        if i:
            time.sleep(delay)  # pacing for Groq's free tier (8k tokens/min per model)
        start = time.time()
        try:
            resp = client.query(s["question"], thread_id=f"eval-{run_id}-{s['id']}")
            error = None
        except Exception as e:
            resp, error = {}, f"{type(e).__name__}: {e}"
        latency = round(time.time() - start, 2)
        tp = resp.get("thought_process") or []
        contexts = [c.removeprefix("CONTENT: ") for c in (resp.get("sources") or [])]
        rec = {
            **{k: s[k] for k in ("id", "domain", "question", "reference", "relevant_contexts", "answerable", "expected_route")},
            "answer": resp.get("answer") or "",
            "contexts": contexts,
            "thought_process": tp,
            "route": "error" if error else _route(tp),
            "models": _models(tp),
            "latency_s": latency,
            "error": error or (resp.get("answer") if resp.get("status") == "error" else None),
        }
        out.append(rec)
        print(f"[{i+1}/{len(samples)}] {rec['id']} {rec['route']:9} {latency:5.1f}s {rec['models'].get('responder', '-'):24} {s['question'][:60]}", flush=True)
    return out


def run_guardrails(client: _Client, samples: list[dict]) -> list[dict]:
    out = []
    if client.mode == "inproc":
        # Measures the gate alone: no LLM calls, so the full set runs in seconds
        from app.guardrails import guard
        for s in samples:
            start = time.time()
            blocked, _, rail = guard(s["input"])
            out.append({**s, "actual_blocked": blocked, "rail": rail, "latency_s": round(time.time() - start, 4)})
    else:
        for i, s in enumerate(samples):
            start = time.time()
            resp = client.query(s["input"], thread_id=f"eval-guard-{s['id']}")
            tp = resp.get("thought_process") or []
            rail = next((t.split(": ", 1)[1] for t in tp if t.startswith("Rail: ")), "pass")
            out.append({**s, "actual_blocked": _route(tp) == "guardrail", "rail": rail, "latency_s": round(time.time() - start, 2)})
    for r in out:
        mark = "OK  " if r["actual_blocked"] == r["expected_blocked"] else "MISS"
        print(f"{mark} {r['type']:10} blocked={r['actual_blocked']!s:5} rail={r['rail']:12} {r['input'][:60]}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["inproc", "http"], default="inproc")
    ap.add_argument("--backend-url", default=os.getenv("BACKEND_URL"))
    ap.add_argument("--label", default="run", help="short name for this configuration, used in the file name")
    ap.add_argument("--dataset", choices=["rag", "guardrails", "both"], default="both")
    ap.add_argument("--limit", type=int, default=None, help="only the first N RAG questions (smoke tests)")
    ap.add_argument("--delay", type=float, default=10.0, help="seconds between RAG questions")
    args = ap.parse_args()
    if args.mode == "http" and not args.backend_url:
        ap.error("--backend-url (or BACKEND_URL) is required in http mode")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    client = _Client(args.mode, args.backend_url)
    result = {"run_id": run_id, "label": args.label, "git_commit": _git_commit(),
              "config": _config_snapshot(args.mode, args.backend_url)}

    if args.dataset in ("rag", "both"):
        samples = json.load(open(EVALS_DIR / "datasets/rag_golden.json", encoding="utf-8"))["samples"][: args.limit]
        result["rag"] = run_rag(client, samples, args.delay, run_id)
    if args.dataset in ("guardrails", "both"):
        samples = json.load(open(EVALS_DIR / "datasets/guardrails.json", encoding="utf-8"))["samples"]
        result["guardrails"] = run_guardrails(client, samples)

    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{run_id}_{args.label}_pipeline.json"
    json.dump(result, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
