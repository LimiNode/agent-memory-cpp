#!/usr/bin/env python3
"""Five-seed QJL m=384 stability diagnostic on the frozen canonical shell."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

Q, D = 152, 384


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_qjl():
    path = Path(__file__).with_name("qjl_reference.py")
    spec = importlib.util.spec_from_file_location("qjl_seed", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["qjl_seed"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ndcg(ids, qids, grades):
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    den = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den)) if den else 0.0


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("documents", "queries", "qrel-ids", "qrel-scores", "artifact", "tq-payload", "output"):
        p.add_argument("--" + name, dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--seeds", default="20261001,20261002,20261003,20261004,20261005")
    args = p.parse_args()
    qjl = load_qjl()
    seeds = [int(x) for x in args.seeds.split(",")]
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(Q, D)))
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(Q, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(Q, 20)))
    with np.load(args.tq_payload, allow_pickle=False) as payload:
        candidate_ids = np.asarray(payload["row_ids"], dtype=np.int64)
        offsets = np.asarray(payload["row_offsets"], dtype=np.int64)
    with np.load(args.artifact, allow_pickle=False) as artifact:
        ids = np.asarray(artifact["document_ids"], dtype=np.int64)
        base = np.asarray(artifact["base"], dtype=np.float32)
        decoded = np.asarray(artifact["decoded1"], dtype=np.float32)
        residual = np.asarray(docs[ids], dtype=np.float32) - base - decoded
        residual_norms = np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32)
    positions = {int(doc): i for i, doc in enumerate(ids)}
    rows = []
    for seed in seeds:
        projection = qjl.make_projection(D, D, seed, "gaussian")
        signs, _ = qjl.encode(residual, projection)
        values = []
        for qi, query in enumerate(queries):
            docs_q = candidate_ids[offsets[qi]:offsets[qi + 1]]
            ix = np.asarray([positions[int(doc)] for doc in docs_q])
            base_q = base[ix] + decoded[ix]
            qn = max(float(np.linalg.norm(query)), 1e-30)
            correction = qjl.estimate_dot_reference(query, signs[ix], residual_norms[ix], projection)
            scores = (base_q @ query + correction) / qn
            ranked = docs_q[np.lexsort((docs_q, -scores))]
            values.append(ndcg(ranked, qids[qi], grades[qi]))
        rows.append({"seed": seed, "mean_qrels_ndcg10": float(np.mean(values)), "p05_qrels_ndcg10": float(np.percentile(values, 5)), "worst_qrels_ndcg10": float(np.min(values))})
    sources = {
        name: sha(getattr(args, name.replace("-", "_")))
        for name in ("documents", "queries", "qrel-ids", "qrel-scores", "artifact", "tq-payload")
    }
    sources["qjl-reference"] = sha(Path(__file__).with_name("qjl_reference.py"))
    result = {"schema_version": 2, "family": "thq_qjl_m384_seed_stability_v1", "status": "EXECUTED", "width": 384, "distribution": "gaussian", "query_count": Q, "seed_count": len(seeds), "seeds": seeds, "side_payload_bytes": 52, "global_model_bytes": D * D * 4, "source_hashes": sources, "runner_sha256": sha(Path(__file__)), "rows": rows, "summaries": {"mean_of_seed_means": float(np.mean([x["mean_qrels_ndcg10"] for x in rows])), "min_seed_mean": float(np.min([x["mean_qrels_ndcg10"] for x in rows])), "max_seed_mean": float(np.max([x["mean_qrels_ndcg10"] for x in rows]))}, "limitations": ["five independent projection draws, candidate-local frozen shell", "not a fresh query split", "score-correction primitive, not a vector decoder"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summaries"], sort_keys=True))


if __name__ == "__main__":
    main()
