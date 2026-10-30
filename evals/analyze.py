"""
Phase 4 analysis: tables with bootstrap 95% CIs, paired rerank comparisons, run-to-run
variance, guardrail comparison, and figures.

Runs in .venv-evals (needs matplotlib). Reads evals/results/*_{pipeline,scores}.json for
the Phase 4 labels written by evals/run_phase4.sh.

Usage (from the repo root):
  .venv-evals/Scripts/python.exe evals/analyze.py
Outputs: evals/analysis/phase4_report.md and evals/analysis/*.png
"""

import json
import random
import re
import statistics
import zlib
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path(__file__).parent / "results"
OUT = Path(__file__).parent / "analysis"
NOISE = ["0x", "1x", "10x", "50x"]
NOISE_X = {"0x": 0, "1x": 1, "10x": 10, "50x": 50}
QUALITY = ["faithfulness", "answer_correctness", "answer_relevancy", "context_precision", "context_recall"]
DOMAIN_SOURCE = {
    "parallel_work_queue": "parallel_work_queue.txt", "pods_autoscale": "pods_autoscale.html",
    "job_management": "job_management.html", "cronjobs": "cronjobs.docx",
    "monitor_job": "monitor_job.docx", "architecture": "architecture.pptx",
}
B = 2000  # bootstrap resamples
RAG_LABEL = re.compile(r"^n(0x|1x|10x|50x)-rr-(on|off)(?:-rep\d)?$")


def bootstrap_ci(values: list[float], seed: int = 0) -> tuple[float, float, float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return (None, None, None)
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(vals, k=len(vals))) for _ in range(B))
    return (statistics.mean(vals), means[int(0.025 * B)], means[int(0.975 * B) - 1])


def latest_runs() -> dict[str, tuple[dict, dict]]:
    """label -> (pipeline, scores), keeping the newest run per label."""
    runs = {}
    for sp in sorted(RESULTS.glob("*_scores.json")):
        pp = sp.with_name(sp.name.replace("_scores.json", "_pipeline.json"))
        if not pp.exists():
            continue
        scores = json.load(open(sp, encoding="utf-8"))
        runs[scores["label"]] = (json.load(open(pp, encoding="utf-8")), scores)
    return runs


def per_question(pipeline: dict, scores: dict) -> dict[str, dict]:
    """question id -> metric values (judge metrics + judge-free retrieval metrics)."""
    by_id = {s["id"]: s["scores"] for s in scores["per_sample"]}
    out = {}
    for r in pipeline.get("rag", []):
        m = {k: v for k, v in by_id.get(r["id"], {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if "abstained" in by_id.get(r["id"], {}) and isinstance(by_id[r["id"]]["abstained"], bool):
            m["abstained"] = float(by_id[r["id"]]["abstained"])
        src = r.get("context_sources") or []
        if src:
            m["noise_intrusion"] = sum(s.get("source_type") == "noise" for s in src) / len(src)
            if r["answerable"]:
                m["hit_at_5"] = float(any(s.get("source") == DOMAIN_SOURCE.get(r["domain"]) for s in src))
        m["latency_s"] = r["latency_s"]
        m["_answerable"] = r["answerable"]
        out[r["id"]] = m
    return out


def pooled(configs: list[dict[str, dict]], metric: str, answerable_only: bool = True) -> list[float]:
    """Average each question over repeats, so CIs are over questions (n=24), not runs."""
    acc = defaultdict(list)
    for cfg in configs:
        for qid, m in cfg.items():
            if answerable_only and not m["_answerable"] and metric not in ("abstained", "noise_intrusion", "latency_s"):
                continue
            if metric in m:
                acc[qid].append(m[metric])
    return [statistics.mean(v) for v in acc.values()]


def fmt(ci) -> str:
    m, lo, hi = ci
    return "n/a" if m is None else f"{m:.3f} [{lo:.3f}, {hi:.3f}]"


def main():
    OUT.mkdir(exist_ok=True)
    runs = latest_runs()
    groups = defaultdict(list)  # (noise, rerank) -> [per_question dict per repeat]
    run_means = defaultdict(list)
    for label, (pipe, sc) in runs.items():
        m = RAG_LABEL.match(label)
        if m:
            pq = per_question(pipe, sc)
            groups[(m.group(1), m.group(2))].append(pq)
            run_means[(m.group(1), m.group(2))].append(sc["summary"])

    lines = ["# Phase 4 results", "",
             "All RAG runs: grounded prompt, primary model gpt-oss-120b (Vertex AI), guardrails off, "
             "judge gemini-3.5-flash-lite. Values: mean [95% bootstrap CI over questions]; repeats are "
             "averaged per question first. Retrieval metrics (hit@5, noise intrusion) need no judge.", ""]

    # --- E1/E2 table ---
    metrics = QUALITY + ["hit_at_5", "noise_intrusion", "abstained", "latency_s"]
    table = {}
    lines += ["## E1/E2: noise level x reranker", "",
              "| Noise | Rerank | Runs | " + " | ".join(metrics) + " |",
              "|---|---|---|" + "---|" * len(metrics)]
    for n in NOISE:
        for rr in ("on", "off"):
            cfgs = groups.get((n, rr), [])
            if not cfgs:
                continue
            row = {}
            for met in metrics:
                # crc32, not hash(): str hashes change per process, CIs must be reproducible
                row[met] = bootstrap_ci(pooled(cfgs, met), seed=zlib.crc32(f"{n}|{rr}|{met}".encode()))
            table[(n, rr)] = row
            lines.append(f"| {n} | {rr} | {len(cfgs)} | " + " | ".join(fmt(row[m]) for m in metrics) + " |")
    lines.append("")

    # --- Paired rerank effect ---
    lines += ["## Paired effect of the reranker (on minus off, same questions)", "",
              "| Noise | " + " | ".join(["faithfulness", "answer_correctness", "context_precision", "hit_at_5", "noise_intrusion"]) + " |",
              "|---|" + "---|" * 5]
    for n in NOISE:
        on, off = groups.get((n, "on")), groups.get((n, "off"))
        if not on or not off:
            continue
        cells = []
        for met in ["faithfulness", "answer_correctness", "context_precision", "hit_at_5", "noise_intrusion"]:
            a = dict(zip(*_ids_vals(on, met)))
            b = dict(zip(*_ids_vals(off, met)))
            diffs = [a[q] - b[q] for q in a if q in b]
            cells.append(fmt(bootstrap_ci(diffs, seed=7)))
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    lines.append("")

    # --- Run-to-run variance ---
    lines += ["## Run-to-run variance (rerank on, identical config)", "",
              "| Noise | Runs | " + " | ".join(QUALITY) + " |", "|---|---|" + "---|" * len(QUALITY)]
    for n in ("0x", "50x"):
        sums = run_means.get((n, "on"), [])
        if len(sums) > 1:
            cells = []
            for met in QUALITY:
                vals = [s.get(met) for s in sums if s.get(met) is not None]
                cells.append(f"{statistics.mean(vals):.3f} ± {statistics.stdev(vals):.3f}" if len(vals) > 1 else "n/a")
            lines.append(f"| {n} | {len(sums)} | " + " | ".join(cells) + " |")
    lines += ["", "(mean ± standard deviation of the run means)", ""]

    # --- Guardrails ---
    gcols = ["guard_recall", "guard_false_block_rate", "guard_acc_jailbreak", "guard_acc_off_topic",
             "guard_acc_small_talk", "injection_recall", "injection_recall_as_jailbreak",
             "benign_flagged_jailbreak", "benign_blocked_any", "guard_latency_p50_ms", "guard_errors"]
    lines += ["## E3: guardrail modes", "",
              "In-house set: 20 attacks (11 jailbreak, 9 off-topic), 12 legitimate, 4 small talk. "
              "External set: 100 injections + 100 general benign prompts (deepset/prompt-injections sample).", "",
              "| Mode | Set | " + " | ".join(gcols) + " |", "|---|---|" + "---|" * len(gcols)]
    guard = {}
    for mode in ("embeddings", "promptguard", "layered", "safeguard"):
        for gset in ("inhouse", "external"):
            r = runs.get(f"g-{mode}-{gset}")
            if r:
                s = r[1]["summary"]
                guard[(mode, gset)] = s
                lines.append(f"| {mode} | {gset} | " + " | ".join(
                    "" if s.get(c) is None else (f"{s[c]:.3f}" if isinstance(s[c], float) else str(s[c])) for c in gcols) + " |")
    lines.append("")

    _figures(table, guard)
    lines += ["## Figures", "", "![Quality vs noise](quality_vs_noise.png)", "",
              "![Retrieval vs noise](retrieval_vs_noise.png)", "", "![Guardrails](guardrails.png)", ""]
    (OUT / "phase4_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {OUT / 'phase4_report.md'}")


def _ids_vals(cfgs, metric):
    acc = defaultdict(list)
    for cfg in cfgs:
        for qid, m in cfg.items():
            if m["_answerable"] and metric in m:
                acc[qid].append(m[metric])
    ids = list(acc)
    return ids, [statistics.mean(acc[q]) for q in ids]


def _figures(table, guard):
    xs = [NOISE_X[n] for n in NOISE]
    pos = list(range(len(NOISE)))

    def plot(ax, metric, title):
        for rr, style in (("on", "-o"), ("off", "--s")):
            pts = [table.get((n, rr), {}).get(metric) for n in NOISE]
            if not any(p and p[0] is not None for p in pts):
                continue
            m = [p[0] if p else None for p in pts]
            err = [[(p[0] - p[1]) if p else 0 for p in pts], [(p[2] - p[0]) if p else 0 for p in pts]]
            ax.errorbar(pos, m, yerr=err, fmt=style, capsize=3, label=f"rerank {rr}")
        ax.set_xticks(pos, [f"{x}x" for x in xs])
        ax.set_title(title)
        ax.set_xlabel("noise (x true chunks)")
        ax.set_ylim(0, 1.05)
        ax.grid(alpha=0.3)

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    for ax, (met, title) in zip(axes, [("faithfulness", "Faithfulness"), ("answer_correctness", "Answer correctness"),
                                       ("context_precision", "Context precision")]):
        plot(ax, met, title)
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(OUT / "quality_vs_noise.png", dpi=160)

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    plot(axes[0], "hit_at_5", "Hit@5 (source doc in top 5)")
    plot(axes[1], "noise_intrusion", "Noise intrusion (share of top 5)")
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(OUT / "retrieval_vs_noise.png", dpi=160)

    modes = ["embeddings", "promptguard", "layered", "safeguard"]
    bars = [("guard_acc_jailbreak", "inhouse", "Jailbreak caught (in-house)"),
            ("guard_acc_off_topic", "inhouse", "Off-topic caught (in-house)"),
            ("guard_false_block_rate", "inhouse", "Legit wrongly blocked (in-house)"),
            ("injection_recall", "external", "Injections caught (external)"),
            ("benign_flagged_jailbreak", "external", "Benign flagged as jailbreak (external)")]
    fig, ax = plt.subplots(figsize=(11, 4))
    w = 0.8 / len(bars)
    for i, (key, gset, label) in enumerate(bars):
        vals = [(guard.get((m, gset)) or {}).get(key) or 0 for m in modes]
        ax.bar([j + i * w for j in range(len(modes))], vals, w, label=label)
    ax.set_xticks([j + 0.4 - w / 2 for j in range(len(modes))], modes)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "guardrails.png", dpi=160)


if __name__ == "__main__":
    main()
