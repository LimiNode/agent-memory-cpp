#!/usr/bin/env python3
"""Run convergence and fixed-capacity controls for the THQ4 ADC scorer.

This is a quality-only reference experiment.  It deliberately keeps the
candidate shell, score definition, tie ordering, and held-out split identical
to the learned-ADC gate while varying only codebook fitting effort and the
rate/geometry of the residual code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
TOP = 128
TRAIN_QUERIES = 120


def load_module():
    path = Path(__file__).with_name("run-thq-learned-adc-gate.py")
    spec = importlib.util.spec_from_file_location("thq_learned_adc_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load learned ADC helpers")
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


def fit_kmeans(values: np.ndarray, count: int, iterations: int,
               restarts: int, seed: int, sample_limit: int) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(seed)
    if len(values) > sample_limit:
        sample = values[rng.choice(len(values), sample_limit, replace=False)]
    else:
        sample = values
    sample = np.asarray(sample, dtype=np.float32)
    best_centers = None
    best_objective = np.inf
    best_empty = 0
    restart_objectives = []
    for restart in range(restarts):
        restart_rng = np.random.default_rng(seed + restart * 104729)
        if len(sample) <= count:
            centers = sample[np.arange(count) % len(sample)].copy()
        elif restart == 0 and restarts == 1:
            # Reproduce the original learned-ADC gate exactly for the
            # ``current`` controls; convergence arms then add independent
            # restart initializations on top of this baseline.
            centers = sample[np.linspace(0, len(sample) - 1, count, dtype=np.int64)].copy()
        else:
            centers = sample[restart_rng.choice(len(sample), count, replace=False)].copy()
        for _ in range(iterations):
            distances = (np.sum(sample * sample, axis=1)[:, None] +
                         np.sum(centers * centers, axis=1)[None, :] -
                         2.0 * (sample @ centers.T))
            symbols = np.argmin(distances, axis=1)
            for level in range(count):
                selected = sample[symbols == level]
                if len(selected):
                    centers[level] = np.mean(selected, axis=0, dtype=np.float64)
        distances = (np.sum(sample * sample, axis=1)[:, None] +
                     np.sum(centers * centers, axis=1)[None, :] -
                     2.0 * (sample @ centers.T))
        symbols = np.argmin(distances, axis=1)
        objective = float(np.mean(np.min(distances, axis=1)))
        empty = int(np.sum(np.bincount(symbols, minlength=count) == 0))
        restart_objectives.append(objective)
        if objective < best_objective:
            best_objective = objective
            best_centers = centers.copy()
            best_empty = empty
    assert best_centers is not None
    return best_centers.astype(np.float32), {
        "sample_count": int(len(sample)),
        "iterations": int(iterations),
        "restarts": int(restarts),
        "objective": best_objective,
        "empty_clusters": best_empty,
        "restart_objectives": restart_objectives,
    }


def fit_codebooks(train_residual: np.ndarray, covariance: np.ndarray, blocks: int,
                  bits: int, sample_limit: int, iterations: int, restarts: int,
                  seed: int) -> tuple[np.ndarray, np.ndarray, dict]:
    width = D // blocks
    levels = 1 << bits
    codebooks = np.empty((blocks, levels, width), dtype=np.float32)
    transforms = np.empty((blocks, width, width), dtype=np.float32)
    diagnostics = []
    for block in range(blocks):
        sl = slice(block * width, (block + 1) * width)
        block_covariance = covariance[sl, sl].astype(np.float64)
        block_covariance.flat[:: width + 1] += 1e-4
        eigenvalues, eigenvectors = np.linalg.eigh(block_covariance)
        transform = (eigenvectors * np.sqrt(np.maximum(eigenvalues, 1e-8))) @ eigenvectors.T
        inverse = (eigenvectors * (1.0 / np.sqrt(np.maximum(eigenvalues, 1e-8)))) @ eigenvectors.T
        transforms[block] = transform.astype(np.float32)
        weighted = np.asarray(train_residual[:, sl], dtype=np.float64) @ transform.T
        fit_seed = (20260918 + width + levels) if restarts == 1 else seed + block * 8191
        fitted, fit_diag = fit_kmeans(weighted.astype(np.float32), levels, iterations,
                                      restarts, fit_seed, sample_limit)
        codebooks[block] = (fitted.astype(np.float64) @ inverse.T).astype(np.float32)
        diagnostics.append(fit_diag)
    return codebooks, transforms, {
        "blocks": blocks,
        "width": width,
        "bits": bits,
        "sample_limit": sample_limit,
        "iterations": iterations,
        "restarts": restarts,
        "blocks_diagnostics": diagnostics,
        "mean_objective": float(np.mean([d["objective"] for d in diagnostics])),
        "empty_cluster_blocks": int(sum(d["empty_clusters"] > 0 for d in diagnostics)),
    }


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
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = score.load_candidates(args.candidate_flat, args.candidate_raw)
    score.validate_candidate_receipt(args.candidate_receipt, args.candidate_raw, args.candidate_flat)
    centroids = h.fit_centroids(train, thresholds)
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    covariance = np.asarray(queries[:min(TRAIN_QUERIES, query_count)], dtype=np.float64).T @ np.asarray(
        queries[:min(TRAIN_QUERIES, query_count)], dtype=np.float64)

    # The first two entries reproduce the earlier small-fit arms.  The other
    # entries separate extra bits from finer block geometry at 48/64 bytes.
    configs = [
        {"name": "32B-2bit-current", "blocks": 128, "bits": 2, "sample": 8192, "iterations": 5, "restarts": 1},
        {"name": "32B-2bit-converged", "blocks": 128, "bits": 2, "sample": 25000, "iterations": 25, "restarts": 4},
        {"name": "32B-8bit-current", "blocks": 32, "bits": 8, "sample": 8192, "iterations": 5, "restarts": 1},
        {"name": "32B-8bit-restart", "blocks": 32, "bits": 8, "sample": 8192, "iterations": 12, "restarts": 3},
        {"name": "48B-3bit-128x3D", "blocks": 128, "bits": 3, "sample": 25000, "iterations": 20, "restarts": 3},
        {"name": "48B-2bit-192x2D", "blocks": 192, "bits": 2, "sample": 25000, "iterations": 20, "restarts": 3},
        {"name": "64B-4bit-128x3D", "blocks": 128, "bits": 4, "sample": 25000, "iterations": 20, "restarts": 3},
    ]
    models = {}
    for index, config in enumerate(configs):
        print(f"fitting {config['name']}", flush=True)
        codebooks, transforms, diagnostics = fit_codebooks(
            train_residual, covariance, config["blocks"], config["bits"],
            config["sample"], config["iterations"], config["restarts"], 20260918 + index * 100003)
        config = dict(config)
        config["fit"] = diagnostics
        models[config["name"]] = (config, codebooks, transforms)

    rows = []
    rng = np.random.default_rng(20260918)
    for qi in range(query_count):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        docs = np.asarray(documents[ids], dtype=np.float32)
        query = np.asarray(queries[qi], dtype=np.float32)
        levels = h.unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels]
        residual = docs - base
        exact = docs @ query
        exact_top = gate.top_ids(exact, ids)
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
        top_positions = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
        exact_norm_fp16 = np.asarray(np.linalg.norm(docs, axis=1), dtype=np.float16).astype(np.float32)
        for name, (config, codebooks, transforms) in models.items():
            symbols = gate.encode(residual, codebooks, transforms)
            side_bytes = int(config["blocks"] * config["bits"] // 8)
            for scope, positions, scope_ids in (("full-shell", np.arange(len(ids)), ids),
                                                  ("thq4-top128", top_positions, thq_top)):
                scores = gate.direct_adc_scores(base[positions], codebooks, symbols[positions], query)
                selected = gate.top_ids(scores, scope_ids)
                rows.append({"query": qi, "split": "train" if qi < TRAIN_QUERIES else "heldout",
                             "arm": name, "scope": scope, "top10_ids": selected.astype(int).tolist(),
                             "qrels_ndcg10": gate.ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                             "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                             "pairwise_order": gate.pairwise_order(scores, exact[positions], rng),
                             "pairwise_top10_boundary": gate.focused_pairwise(scores, exact[positions], rng, 8, 4),
                             "side_payload_bytes": side_bytes, "thq4_top128_count": int(len(thq_top))})
    summaries = {}
    for name in models:
        summaries[name] = {}
        for scope in ("full-shell", "thq4-top128"):
            scoped = [r for r in rows if r["arm"] == name and r["scope"] == scope]
            summaries[name][scope] = {}
            for split in ("all", "train", "heldout"):
                selected = scoped if split == "all" else [r for r in scoped if r["split"] == split]
                summaries[name][scope][split] = {
                    key: float(np.mean([r[key] for r in selected]))
                    for key in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap",
                                "pairwise_order", "pairwise_top10_boundary")}
    result = {"schema_version": 1, "family": "thq_adc_convergence_capacity_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": document_count,
              "training_count": train_count, "query_count": query_count,
              "train_query_count": min(TRAIN_QUERIES, query_count),
              "candidate_flat_sha256": sha(args.candidate_flat), "candidate_raw_sha256": sha(args.candidate_raw),
              "candidate_receipt_sha256": sha(args.candidate_receipt), "documents_sha256": sha(args.documents),
              "thq4_codes_sha256": sha(args.thq4_codes), "thq4_thresholds_sha256": sha(args.thq4_thresholds),
              "training_sha256": sha(args.train_vectors), "queries_sha256": sha(args.queries),
              "qrel_ids_sha256": sha(args.qrel_ids), "qrel_scores_sha256": sha(args.qrel_scores),
              "teacher_ids_sha256": sha(args.teacher_ids),
              "model_hashes": {name: hashlib.sha256(codebooks.astype("<f4").tobytes() +
                               transforms.astype("<f4").tobytes()).hexdigest()
                               for name, (_, codebooks, transforms) in models.items()},
              "configs": {name: config for name, (config, _, _) in models.items()},
              "summaries": summaries, "rows": rows,
              "evidence_status": "convergence_and_fixed_capacity_reference_quality",
              "limitations": ["candidate-local replay; no routing membership claim",
                               "query-weighted fit uses first 120 queries; held-out rows are not used for fitting",
                               "reference quality only, with no native latency, materialization, or held-out-domain evidence"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
