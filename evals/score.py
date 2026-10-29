"""
Eval step 2 - score a saved pipeline run with RAGAS and a Gemini judge.

Runs in the separate eval virtualenv (.venv-evals, see requirements-evals.txt)
because RAGAS pins an older langchain-community than the app uses.

Judge: Gemini on Vertex AI (location "global"), billed to GCP credits.
Embeddings (AnswerRelevancy / AnswerCorrectness): Vertex text-embedding-004,
the same model production uses for retrieval.

Usage (from the repo root):
  .venv-evals/Scripts/python.exe evals/score.py evals/results/<run>_pipeline.json
"""

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")  # no usage telemetry

import instructor
from dotenv import load_dotenv
from google import genai
from ragas.embeddings import GoogleEmbeddings
from ragas.llms.base import InstructorLLM, InstructorModelArgs
from ragas.metrics.collections import (
    AnswerCorrectness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)

load_dotenv()
PROJECT_ID = os.getenv("PROJECT_ID", "enterprise-rag-499816")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = "text-embedding-004"
CONCURRENCY = 4
RAG_METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness"]

ABSTAIN_PROMPT = """You are grading a documentation assistant. The question below is NOT covered by its documentation.
Did the assistant clearly say it cannot answer from the available documentation (instead of answering anyway)?
Reply with exactly YES or NO.

Question: {question}
Assistant answer: {answer}"""


class TokenCounter:
    """Wraps the async generate call so each run reports the judge's real token usage."""

    def __init__(self, client: genai.Client):
        self.calls = self.prompt_tokens = self.output_tokens = 0
        original = client.aio.models.generate_content

        async def counted(*args, **kwargs):
            response = await original(*args, **kwargs)
            usage = getattr(response, "usage_metadata", None)
            self.calls += 1
            if usage:
                self.prompt_tokens += usage.prompt_token_count or 0
                self.output_tokens += (usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
            return response

        client.aio.models.generate_content = counted


def build_judge():
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    counter = TokenCounter(client)  # patch before instructor captures the method
    llm = InstructorLLM(
        client=instructor.from_genai(client, use_async=True),
        model=JUDGE_MODEL,
        provider="google",
        model_args=InstructorModelArgs(temperature=0, max_tokens=4096),
    )
    emb_client = genai.Client(vertexai=True, project=PROJECT_ID, location="us-central1")
    embeddings = GoogleEmbeddings(client=emb_client, model=EMBED_MODEL)
    metrics = {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
        "answer_correctness": AnswerCorrectness(llm=llm, embeddings=embeddings),
    }
    return client, counter, metrics


async def _retry(fn, attempts: int = 3):
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:
            if i == attempts - 1:
                return f"ERROR: {type(e).__name__}: {str(e)[:200]}"
            await asyncio.sleep(5 * (i + 1))


async def score_sample(rec: dict, metrics: dict, client: genai.Client, sem: asyncio.Semaphore) -> dict:
    scores: dict = {}
    if rec["route"] == "error" or not rec["answer"]:
        return {"id": rec["id"], "scores": {}, "skipped": "pipeline error"}

    q, a, ctx, ref = rec["question"], rec["answer"], rec["contexts"], rec["reference"]
    jobs = {"answer_relevancy": lambda: metrics["answer_relevancy"].ascore(user_input=q, response=a)}
    if ctx:
        jobs["faithfulness"] = lambda: metrics["faithfulness"].ascore(user_input=q, response=a, retrieved_contexts=ctx)
    if rec["answerable"]:
        jobs["answer_correctness"] = lambda: metrics["answer_correctness"].ascore(user_input=q, response=a, reference=ref)
        if ctx:
            jobs["context_precision"] = lambda: metrics["context_precision"].ascore(user_input=q, reference=ref, retrieved_contexts=ctx)
            jobs["context_recall"] = lambda: metrics["context_recall"].ascore(user_input=q, retrieved_contexts=ctx, reference=ref)

    async def run(name, job):
        async with sem:
            res = await _retry(job)
            scores[name] = res if isinstance(res, str) else round(float(res.value), 4)

    await asyncio.gather(*(run(n, j) for n, j in jobs.items()))

    if not rec["answerable"]:
        async def abstain():
            r = await client.aio.models.generate_content(
                model=JUDGE_MODEL, contents=ABSTAIN_PROMPT.format(question=q, answer=a[:4000]))
            return r.text.strip().upper().startswith("YES")
        async with sem:
            res = await _retry(abstain)
        scores["abstained"] = res if isinstance(res, str) else bool(res)

    print(f"  {rec['id']}: " + ", ".join(f"{k}={v}" for k, v in sorted(scores.items())), flush=True)
    return {"id": rec["id"], "scores": scores}


def _mean(values):
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(statistics.mean(nums), 4) if nums else None


def _pct(values, p):
    vals = sorted(values)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 2)


def summarize(run: dict, per_sample: list[dict]) -> dict:
    rag = run.get("rag") or []
    by_id = {s["id"]: s["scores"] for s in per_sample}
    answerable = [r for r in rag if r["answerable"]]
    summary = {m: _mean(by_id.get(r["id"], {}).get(m) for r in answerable) for m in RAG_METRICS}

    unanswerable = [by_id.get(r["id"], {}).get("abstained") for r in rag if not r["answerable"]]
    graded = [x for x in unanswerable if isinstance(x, bool)]
    summary["abstention_rate"] = round(sum(graded) / len(graded), 4) if graded else None

    if rag:
        summary["route_accuracy"] = round(sum(r["route"] == r["expected_route"] for r in rag) / len(rag), 4)
        summary["pipeline_errors"] = sum(r["route"] == "error" for r in rag)
        lat = [r["latency_s"] for r in rag if r["route"] != "error"]
        summary["latency_p50_s"], summary["latency_p95_s"] = _pct(lat, 0.5), _pct(lat, 0.95)
        primary = run["config"].get("groq_model")
        answered = [r for r in rag if r["models"].get("responder")]
        if primary and answered:
            summary["fallback_rate"] = round(sum(r["models"]["responder"] != primary for r in answered) / len(answered), 4)

    g = run.get("guardrails") or []
    if g:
        tp = sum(r["expected_blocked"] and r["actual_blocked"] for r in g)
        fn = sum(r["expected_blocked"] and not r["actual_blocked"] for r in g)
        fp = sum(not r["expected_blocked"] and r["actual_blocked"] for r in g)
        tn = sum(not r["expected_blocked"] and not r["actual_blocked"] for r in g)
        summary.update({
            "guard_precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "guard_recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "guard_false_block_rate": round(fp / (fp + tn), 4) if fp + tn else None,
            "guard_accuracy": round((tp + tn) / len(g), 4),
        })
        for t in sorted({r["type"] for r in g}):
            rows = [r for r in g if r["type"] == t]
            summary[f"guard_acc_{t}"] = round(sum(r["actual_blocked"] == r["expected_blocked"] for r in rows) / len(rows), 4)
    return summary


async def main_async(path: Path):
    run = json.load(open(path, encoding="utf-8"))
    rag = run.get("rag") or []
    client, counter, metrics = build_judge()
    sem = asyncio.Semaphore(CONCURRENCY)
    start = time.time()
    print(f"Scoring {len(rag)} RAG samples from {path.name} with judge {JUDGE_MODEL}")
    per_sample = list(await asyncio.gather(*(score_sample(r, metrics, client, sem) for r in rag)))
    summary = summarize(run, per_sample)
    judge = {"model": JUDGE_MODEL, "calls": counter.calls, "prompt_tokens": counter.prompt_tokens,
             "output_tokens": counter.output_tokens, "seconds": round(time.time() - start, 1)}

    out = {"run_id": run["run_id"], "label": run["label"], "config": run["config"], "git_commit": run.get("git_commit"),
           "judge": judge, "summary": summary, "per_sample": per_sample}
    out_path = path.with_name(path.name.replace("_pipeline.json", "_scores.json"))
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=2)

    csv_path = path.parent / "summary.csv"
    row = {"run_id": run["run_id"], "label": run["label"], "git_commit": run.get("git_commit"),
           "collection": run["config"].get("qdrant_collection"), "rerank": run["config"].get("rerank_enabled"),
           "guardrails": run["config"].get("guardrails_enabled"),
           # Runs before the setting existed used the original prompt
           "responder_prompt": run["config"].get("responder_prompt", "original"), **summary,
           "judge_model": JUDGE_MODEL, "judge_prompt_tokens": counter.prompt_tokens, "judge_output_tokens": counter.output_tokens}
    existing = list(csv.DictReader(open(csv_path, encoding="utf-8"))) if csv_path.exists() else []
    existing = [r for r in existing if r["run_id"] != row["run_id"]] + [row]
    fields = list(dict.fromkeys(k for r in existing for k in r))
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(existing)

    print("\nSUMMARY")
    for k, v in summary.items():
        print(f"  {k:28} {v}")
    print(f"  judge: {judge}")
    print(f"\nSaved {out_path}\nUpdated {csv_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pipeline_json", type=Path)
    args = ap.parse_args()
    if not args.pipeline_json.exists():
        sys.exit(f"Not found: {args.pipeline_json}")
    asyncio.run(main_async(args.pipeline_json))


if __name__ == "__main__":
    main()
