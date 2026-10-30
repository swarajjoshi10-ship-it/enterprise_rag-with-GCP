"""
Phase 4 noise-scaling collections.

  sample  Picks noise documents (fixed seed), extracts + chunks them with the production
          loaders/splitter, and writes evals/datasets/noise_manifest.json. Chunk text goes to
          evals/noise_cache/ (gitignored: the PDFs are third-party material). No embeddings,
          no Qdrant writes.
  build   Embeds the sampled noise once (cached), then creates exp_noise_0x/1x/10x/50x:
          the true chunks are copied from the production collection with their vectors
          (read-only), plus the first N noise chunks. Levels are nested (1x ⊂ 10x ⊂ 50x).

Never writes to the production collection and never uploads to GCS (so Eventarc is never
triggered).

Usage (from the repo root, app venv):
  .venv/Scripts/python.exe -m evals.build_noise_collections sample --noise-dir "<path to noisy PDFs>"
  .venv/Scripts/python.exe -m evals.build_noise_collections build
"""

import argparse
import json
import logging
import os
import random
import uuid
from pathlib import Path

EVALS_DIR = Path(__file__).parent
MANIFEST = EVALS_DIR / "datasets" / "noise_manifest.json"
CACHE_DIR = EVALS_DIR / "noise_cache"
CHUNKS_FILE = CACHE_DIR / "noise_chunks.jsonl"
VECTORS_FILE = CACHE_DIR / "noise_vectors.jsonl"

SEED = 2026
MAX_CHUNKS_PER_DOC = 20     # keeps one long book from dominating a level
MAX_PAGES = 15              # enough text for 20 chunks; avoids parsing 1,000-page books
MIN_CHARS_PER_PAGE = 200    # below this the PDF is likely scanned (no text layer)
LEVELS = {"0x": 0, "1x": 1, "10x": 10, "50x": 50}
PROD_COLLECTION = "enterprise_rag"


def _extract(path: Path) -> str | None:
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(path))
        pages = reader.pages[:MAX_PAGES]
        text = "\n".join((p.extract_text() or "") for p in pages)
    except Exception:
        return None
    if len(text) / max(len(pages), 1) < MIN_CHARS_PER_PAGE:
        return None
    return text


def cmd_sample(args):
    from app.ingestion.chunking.splitter import chunk_text
    from qdrant_client import QdrantClient
    from app.config import settings

    logging.disable(logging.CRITICAL)  # pypdf warnings on malformed PDFs
    true_chunks = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY).count(PROD_COLLECTION).count
    target = true_chunks * max(LEVELS.values())

    files = sorted(f for f in os.listdir(args.noise_dir) if f.lower().endswith(".pdf"))
    random.Random(SEED).shuffle(files)

    CACHE_DIR.mkdir(exist_ok=True)
    docs, skipped, total = [], 0, 0
    with open(CHUNKS_FILE, "w", encoding="utf-8") as out:
        for f in files:
            if total >= target:
                break
            text = _extract(Path(args.noise_dir) / f)
            if not text:
                skipped += 1
                continue
            chunks = chunk_text(text)[:MAX_CHUNKS_PER_DOC]
            chunks = chunks[: target - total]
            for i, c in enumerate(chunks):
                out.write(json.dumps({"doc": f, "chunk_index": i, "text": c}, ensure_ascii=False) + "\n")
            docs.append({"file": f, "chunks": len(chunks), "first_chunk": total})
            total += len(chunks)
            print(f"[{total:5}/{target}] {len(chunks):2} chunks  {f[:90]}", flush=True)

    levels = {name: {"noise_chunks": true_chunks * k, "noise_docs": sum(d["first_chunk"] < true_chunks * k for d in docs),
                     "total_chunks": true_chunks * (k + 1)} for name, k in LEVELS.items()}
    json.dump({"seed": SEED, "true_chunks": true_chunks, "max_chunks_per_doc": MAX_CHUNKS_PER_DOC,
               "max_pages": MAX_PAGES, "skipped_no_text": skipped, "levels": levels, "documents": docs},
              open(MANIFEST, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n{len(docs)} documents, {total} noise chunks, {skipped} skipped (no text layer)")
    for name, lv in levels.items():
        print(f"  {name:4} +{lv['noise_chunks']:5} noise chunks from {lv['noise_docs']:4} docs -> {lv['total_chunks']} total")
    print(f"Manifest: {MANIFEST}")


def cmd_build(args):
    from qdrant_client import QdrantClient, models
    from app.config import settings
    from app.services.retrieval.embedding import embed_texts

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    chunks = [json.loads(line) for line in open(CHUNKS_FILE, encoding="utf-8")]

    # 1. Embed noise once; cached so re-builds cost nothing
    vectors = [json.loads(line)["vector"] for line in open(VECTORS_FILE, encoding="utf-8")] if VECTORS_FILE.exists() else []
    if len(vectors) < len(chunks):
        with open(VECTORS_FILE, "a", encoding="utf-8") as out:
            # 10 per request: Vertex caps a request at 20k tokens, and code/math-heavy
            # noise chunks are token-dense (50 chunks reached 23k)
            for start in range(len(vectors), len(chunks), 10):
                batch = chunks[start:start + 10]
                for v in embed_texts([c["text"] for c in batch]):
                    out.write(json.dumps({"vector": v}) + "\n")
                    vectors.append(v)
                if len(vectors) % 250 == 0 or len(vectors) == len(chunks):
                    print(f"embedded {len(vectors)}/{len(chunks)}", flush=True)

    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, timeout=120)

    # 2. True chunks: read-only copy from production, vectors included (no re-embedding)
    true_points, offset = [], None
    while True:
        page, offset = client.scroll(PROD_COLLECTION, limit=256, offset=offset, with_payload=True, with_vectors=True)
        true_points += page
        if offset is None:
            break
    assert len(true_points) == manifest["true_chunks"], "production collection changed since sampling"

    noise_points = [
        models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"noise/{c['doc']}#{c['chunk_index']}")),
            vector=v,
            payload={"text": c["text"], "source": c["doc"], "source_type": "noise"},
        )
        for c, v in zip(chunks, vectors)
    ]

    # 3. One collection per level
    for name, level in manifest["levels"].items():
        coll = f"exp_noise_{name}"
        assert coll != PROD_COLLECTION
        if client.collection_exists(coll):
            client.delete_collection(coll)
        client.create_collection(coll, vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE))
        points = [models.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in true_points]
        points += noise_points[: level["noise_chunks"]]
        for start in range(0, len(points), 256):
            client.upsert(coll, points=points[start:start + 256])
        print(f"{coll}: {client.count(coll).count} points")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--noise-dir", required=True)
    sub.add_parser("build")
    args = ap.parse_args()
    {"sample": cmd_sample, "build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    main()
