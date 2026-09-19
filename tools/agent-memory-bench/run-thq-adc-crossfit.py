#!/usr/bin/env python3
"""Four-fold query cross-fitting control for the THQ4 ADC scorer."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
TOP = 128


def load_module():
    path = Path(__file__).with_name("run-thq-adc-convergence-capacity.py")
    spec = importlib.util.spec_from_file_location("thq_adc_convergence_capacity", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load convergence helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_module()
score = gate.score
h = score.h.h


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    return gate.gate.ndcg(ids, qrel_ids, qrel_scores)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "candidate-flat", "candidate-raw", "candidate-receipt", "queries",
                 "qrel-ids", "qrel-scores", "teacher-ids", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_count = args.queries.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D)), dtype=np.float32)
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = score.load_candidates(args.candidate_flat, args.candidate_raw)
    score.validate_candidate_receipt(args.candidate_receipt, args.candidate_raw, args.candidate_flat)
    centroids = h.fit_centroids(train, thresholds)
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    configs = [
        {"name": "32B-2bit", "blocks": 128, "bits": 2},
        {"name": "48B-3bit-128x3D", "blocks": 128, "bits": 3},
        {"name": "48B-2bit-192x2D", "blocks": 192, "bits": 2},
        {"name": "64B-4bit-128x3D", "blocks": 128, "bits": 4},
    ]
    folds = np.array_split(np.arange(query_count), 4)
    rows = []
    for fold, test_ids in enumerate(folds):
        fit_ids = np.setdiff1d(np.arange(query_count), test_ids)
        covariance = queries[fit_ids].astype(np.float64).T @ queries[fit_ids].astype(np.float64)
        models = {}
        for index, config in enumerate(configs):
            print(f"fold {fold} fitting {config['name']}", flush=True)
            codebooks, transforms, diagnostics = gate.fit_codebooks(
                train_residual, covariance, config["blocks"], config["bits"],
                sample_limit=8192, iterations=5, restarts=1,
                seed=20260918 + fold * 100003 + index * 8191)
            models[config["name"]] = (config, codebooks, transforms, diagnostics)
        for qi in test_ids:
            ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
            docs = np.asarray(documents[ids], dtype=np.float32)
            query = queries[qi]
            levels = h.unpack_thq(np.asarray(thq_codes[ids]))
            base = centroids[np.arange(D)[None, :], levels]
            residual = docs - base
            exact = docs @ query
            exact_top = gate.gate.top_ids(exact, ids)
            interval_lut = np.empty((D, 4), dtype=np.float32)
            for coordinate in range(D):
                for level in range(4):
                    low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                    high = np.inf if level == 3 else thresholds[coordinate, level]
                    delta = low - query[coordinate] if query[coordinate] < low else (
                        query[coordinate] - high if query[coordinate] > high else 0.0)
                    interval_lut[coordinate, level] = delta * delta
            interval = np.sum(interval_lut[np.arange(D)[None, :], levels], axis=1)
            thq_top = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
            positions = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
            for name, (config, codebooks, transforms, _) in models.items():
                symbols = gate.gate.encode(residual, codebooks, transforms)
                scores = gate.gate.direct_adc_scores(base[positions], codebooks, symbols[positions], query)
                selected = gate.gate.top_ids(scores, thq_top)
                rows.append({"fold": fold, "query": int(qi), "arm": name,
                             "top10_ids": selected.astype(int).tolist(),
                             "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                             "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                             "pairwise_top10_boundary": gate.gate.focused_pairwise(scores, exact[positions],
                                                                                     np.random.default_rng(20260918 + qi), 8, 4),
                             "side_payload_bytes": int(config["blocks"] * config["bits"] // 8),
                             "thq4_top128_count": int(len(thq_top))})
    summaries = {}
    for name in [c["name"] for c in configs]:
        arm_rows = [row for row in rows if row["arm"] == name]
        summaries[name] = {str(key): {metric: float(np.mean([row[metric] for row in arm_rows if row["fold"] == key]))
                                      for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap",
                                                     "pairwise_top10_boundary")}
                           for key in range(4)}
        summaries[name]["oof"] = {metric: float(np.mean([row[metric] for row in arm_rows]))
                                   for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap",
                                                  "pairwise_top10_boundary")}
    result = {"schema_version": 1, "family": "thq_adc_crossfit_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": document_count,
              "training_count": train_count, "query_count": query_count, "fold_count": 4,
              "fold_sizes": [len(fold) for fold in folds], "candidate_flat_sha256": sha(args.candidate_flat),
              "candidate_raw_sha256": sha(args.candidate_raw), "candidate_receipt_sha256": sha(args.candidate_receipt),
              "documents_sha256": sha(args.documents), "thq4_codes_sha256": sha(args.thq4_codes),
              "thq4_thresholds_sha256": sha(args.thq4_thresholds), "training_sha256": sha(args.train_vectors),
              "queries_sha256": sha(args.queries), "qrel_ids_sha256": sha(args.qrel_ids),
              "qrel_scores_sha256": sha(args.qrel_scores), "teacher_ids_sha256": sha(args.teacher_ids),
              "configs": configs, "summaries": summaries, "rows": rows,
              "evidence_status": "four_fold_out_of_fold_reference_quality",
              "limitations": ["current five-iteration fit only", "candidate-local replay",
                               "no native latency or persistent materialization", "no cross-domain holdout"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
