#!/usr/bin/env python3
"""Production-shaped OOF comparison for ADC48 against THQ-local controls."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
TOP = 128


def load_crossfit():
    path = Path(__file__).with_name("run-thq-adc-crossfit.py")
    spec = importlib.util.spec_from_file_location("thq_adc_crossfit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load cross-fit helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crossfit = load_crossfit()
gate = crossfit.gate
score = gate.score
h = score.h.h


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paired(values: np.ndarray) -> dict:
    rng = np.random.default_rng(20260919)
    bootstrap = np.asarray([np.mean(values[rng.integers(0, len(values), len(values))])
                            for _ in range(10000)])
    return {"mean": float(np.mean(values)), "median": float(np.median(values)),
            "p05": float(np.quantile(values, 0.05)), "min": float(np.min(values)),
            "wins": int(np.sum(values > 1e-12)), "ties": int(np.sum(np.abs(values) <= 1e-12)),
            "losses": int(np.sum(values < -1e-12)),
            "bootstrap_ci95": [float(np.quantile(bootstrap, 0.025)),
                                float(np.quantile(bootstrap, 0.975))]}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "int8-codes", "int8-scales", "candidate-flat", "candidate-raw",
                 "candidate-receipt", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_count = args.queries.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    int8_codes = np.memmap(args.int8_codes, mode="r", dtype=np.int8, shape=(document_count, D))
    int8_scales = np.memmap(args.int8_scales, mode="r", dtype="<f4", shape=(document_count,))
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
    signs = np.random.default_rng(20260916).choice(np.asarray([-1.0, 1.0], dtype=np.float32), size=(3, 128))
    rslm4_centers = score.h.lloyd_centers(score.h.fwht_blocks(train_residual, signs), 4)

    shuffled = np.random.default_rng(20260919).permutation(query_count)
    folds = np.array_split(shuffled, 4)
    rows = []
    for fold, test_ids in enumerate(folds):
        fit_ids = np.setdiff1d(np.arange(query_count), test_ids)
        covariance = queries[fit_ids].astype(np.float64).T @ queries[fit_ids].astype(np.float64)
        print(f"fold {fold}: fitting ADC48/3bit", flush=True)
        codebooks, transforms, diagnostics = gate.fit_codebooks(
            train_residual, covariance, 128, 3, sample_limit=8192, iterations=5, restarts=1,
            seed=20260919 + fold * 100003)
        for qi in test_ids:
            ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
            docs = np.asarray(documents[ids], dtype=np.float32)
            query = queries[qi]
            levels = h.unpack_thq(np.asarray(thq_codes[ids]))
            base = centroids[np.arange(D)[None, :], levels]
            residual = docs - base
            exact = docs @ query
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
            exact_top = gate.gate.top_ids(exact[positions], thq_top)
            int8_values = np.asarray(int8_codes[thq_top], dtype=np.float32) * np.asarray(int8_scales[thq_top], dtype=np.float32)[:, None]
            int8_scores = score.cosine_from_parts(np.zeros_like(int8_values), int8_values, query)
            rotated = score.h.fwht_blocks(residual[positions], signs)
            decoded = score.h.quantize_decode(rotated, rslm4_centers)
            rslm_values = base[positions] + score.h.fwht_blocks(decoded, signs, inverse=True)
            rslm_scores = score.cosine_from_parts(np.zeros_like(rslm_values), rslm_values, query)
            symbols = gate.gate.encode(residual, codebooks, transforms)
            adc_scores = gate.gate.direct_adc_scores(base[positions], codebooks, symbols[positions], query)
            arms = {"thq4-fp32": (exact[positions], 1536), "thq4-int8": (int8_scores, 388),
                    "thq4-rslm4": (rslm_scores, 288), "adc48-3bit": (adc_scores, 144)}
            for arm, (scores, payload) in arms.items():
                selected = gate.gate.top_ids(scores, thq_top)
                rows.append({"fold": fold, "query": int(qi), "arm": arm,
                             "top10_ids": selected.astype(int).tolist(),
                             "qrels_ndcg10": gate.gate.ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                             "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                             "payload_bytes": payload, "thq4_top128_count": int(len(thq_top))})
    by_query = {(row["query"], row["arm"]): row for row in rows}
    summaries = {}
    for arm in ("thq4-fp32", "thq4-int8", "thq4-rslm4", "adc48-3bit"):
        arm_rows = [row for row in rows if row["arm"] == arm]
        summaries[arm] = {metric: float(np.mean([row[metric] for row in arm_rows]))
                          for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")}
        summaries[arm]["paired_qrels_ndcg10"] = {}
        for baseline in ("thq4-fp32", "thq4-int8", "thq4-rslm4"):
            if arm == baseline:
                continue
            deltas = np.asarray([by_query[(q, arm)]["qrels_ndcg10"] - by_query[(q, baseline)]["qrels_ndcg10"]
                                 for q in range(query_count)])
            summaries[arm]["paired_qrels_ndcg10"][baseline] = paired(deltas)
    result = {"schema_version": 1, "family": "thq_adc_production_shaped_crossfit_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": document_count, "training_count": train_count,
              "query_count": query_count, "fold_count": 4, "fold_sizes": [len(fold) for fold in folds],
              "fold_seed": 20260919, "candidate_flat_sha256": sha(args.candidate_flat),
              "candidate_raw_sha256": sha(args.candidate_raw), "candidate_receipt_sha256": sha(args.candidate_receipt),
              "documents_sha256": sha(args.documents), "training_sha256": sha(args.train_vectors),
              "thq4_codes_sha256": sha(args.thq4_codes), "thq4_thresholds_sha256": sha(args.thq4_thresholds),
              "int8_codes_sha256": sha(args.int8_codes), "int8_scales_sha256": sha(args.int8_scales),
              "queries_sha256": sha(args.queries), "qrel_ids_sha256": sha(args.qrel_ids),
              "qrel_scores_sha256": sha(args.qrel_scores), "teacher_ids_sha256": sha(args.teacher_ids),
              "rslm4_centers_sha256": hashlib.sha256(np.asarray(rslm4_centers, dtype="<f4").tobytes()).hexdigest(),
              "summary": summaries, "rows": rows,
              "evidence_status": "four_fold_shuffled_production_shaped_reference_quality",
              "limitations": ["candidate-local replay", "NumPy reference quality only",
                               "no native latency or persistent materialization", "single shuffled fold assignment"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
