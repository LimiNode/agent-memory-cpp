#!/usr/bin/env python3
"""Train and evaluate retrieval-supervised LTHQ-T thresholds.

The input is a deliberately small, explicit teacher-cache contract.  It must
contain independent train/evaluation query arrays and teacher rankings:

``train_vectors, train_queries, train_positive_ids, train_negative_ids``
and ``eval_vectors, eval_queries, eval_teacher_ids, eval_teacher_scores``.

IDs index the corresponding vector matrix.  The runner never uses evaluation
labels while fitting thresholds.  LTHQ-T keeps identity coordinates and learns
ordered thresholds with deterministic coordinate descent over quantile
candidates.  THQ uniform/quantile are reconstruction controls.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def top(scores: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), scores.size)
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    ids = np.argpartition(-scores, count - 1)[:count]
    return ids[np.argsort(-scores[ids], kind="stable")]


def ndcg(selected: np.ndarray, teacher_ids: np.ndarray,
         teacher_scores: np.ndarray, k: int) -> float:
    lookup = {int(doc): float(score) for doc, score in
              zip(teacher_ids, teacher_scores)}
    gains = np.asarray([max(0.0, lookup.get(int(doc), 0.0))
                        for doc in selected[:k]], dtype=np.float64)
    discounts = 1.0 / np.log2(np.arange(2, len(gains) + 2))
    ideal = np.sort(np.asarray(teacher_scores, dtype=np.float64))[::-1][:k]
    ideal_discounts = 1.0 / np.log2(np.arange(2, len(ideal) + 2))
    denominator = float(np.sum(ideal * ideal_discounts))
    return float(np.sum(gains * discounts) / denominator) if denominator else 0.0


def thresholds_uniform(values: np.ndarray, levels: int) -> np.ndarray:
    low = np.min(values, axis=0)
    high = np.max(values, axis=0)
    fractions = np.arange(1, levels, dtype=np.float32) / levels
    return low[:, None] + (high - low)[:, None] * fractions[None, :]


def thresholds_quantile(values: np.ndarray, levels: int) -> np.ndarray:
    fractions = np.arange(1, levels, dtype=np.float32) / levels
    return np.quantile(values, fractions, axis=0).T.astype(np.float32)


def encode(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    bits = (np.asarray(values, dtype=np.float32)[:, :, None]
            > thresholds[None, :, :]).reshape(len(values), -1)
    return np.packbits(bits, axis=1, bitorder="little")


def hamming(codes: np.ndarray, query_code: np.ndarray) -> np.ndarray:
    xor = np.bitwise_xor(codes, query_code[None, :])
    return np.unpackbits(xor, axis=1, bitorder="little").sum(axis=1,
                                                               dtype=np.int32)


def pair_loss(codes: np.ndarray, queries: np.ndarray, thresholds: np.ndarray,
              positives: np.ndarray, negatives: np.ndarray, margin: float,
              max_queries: int = 0, max_pairs: int = 256) -> float:
    query_count = min(len(queries), max_queries or len(queries))
    losses: list[float] = []
    for qi in range(query_count):
        pos = np.asarray(positives[qi], dtype=np.int64)
        neg = np.asarray(negatives[qi], dtype=np.int64)
        if len(pos) == 0 or len(neg) == 0:
            continue
        neg = neg[:max_pairs]
        pos = pos[:max(1, min(len(pos), max_pairs // max(1, len(neg))))]
        qcode = encode(queries[qi:qi + 1], thresholds)[0]
        # Positives and negatives are compared against the encoded query.  This
        # is the retrieval-aware part of the objective (not reconstruction).
        pd = hamming(codes[pos], qcode).astype(np.float32)
        nd = hamming(codes[neg], qcode).astype(np.float32)
        losses.append(float(np.maximum(0.0, margin + pd[:, None] - nd[None, :]).mean()))
    return float(np.mean(losses)) if losses else 0.0


def fit_lthq(values: np.ndarray, queries: np.ndarray, positives: np.ndarray,
             negatives: np.ndarray, levels: int, seed: int,
             candidate_quantiles: int, passes: int, margin: float) -> tuple[np.ndarray, dict[str, Any]]:
    """Fit ordered thresholds using deterministic retrieval-aware coordinate search."""
    rng = np.random.default_rng(seed)
    source = np.asarray(values, dtype=np.float32)
    current = thresholds_quantile(source, levels)
    # Keep the candidate set bounded while still including the reconstruction
    # quantiles.  A fixed seed makes subsampling reproducible for large caches.
    fractions = np.linspace(0.01, 0.99, max(candidate_quantiles, levels),
                           dtype=np.float32)
    candidates = np.quantile(source, fractions, axis=0).T.astype(np.float32)
    baseline = pair_loss(encode(source, current), queries, current, positives,
                         negatives, margin)
    best_loss = baseline
    accepted = 0
    order = rng.permutation(source.shape[1])
    for _ in range(passes):
        for coordinate in order:
            proposed = current[coordinate].copy()
            for level in range(levels - 1):
                local_best = proposed[level]
                local_loss = best_loss
                for candidate in candidates[coordinate]:
                    trial = proposed.copy()
                    trial[level] = candidate
                    trial.sort()
                    trial_thresholds = current.copy()
                    trial_thresholds[coordinate] = trial
                    value = pair_loss(encode(source, trial_thresholds), queries,
                                      trial_thresholds, positives, negatives,
                                      margin)
                    if value + 1.0e-8 < local_loss:
                        local_loss, local_best = value, float(candidate)
                proposed[level] = local_best
                proposed.sort()
            if not np.allclose(proposed, current[coordinate]):
                current[coordinate] = proposed
                best_loss = pair_loss(encode(source, current), queries, current,
                                      positives, negatives, margin)
                accepted += 1
    return current.astype(np.float32), {"initial_pair_loss": baseline,
                                       "final_pair_loss": best_loss,
                                       "accepted_coordinate_updates": accepted}


def evaluate(name: str, vectors: np.ndarray, queries: np.ndarray,
              teacher_ids: np.ndarray, teacher_scores: np.ndarray,
              thresholds: np.ndarray, payload: int, model_bytes: int,
              top_ks: list[int], partition: str) -> dict[str, Any]:
    codes = encode(vectors, thresholds)
    overlaps: dict[str, list[float]] = {str(k): [] for k in top_ks}
    ndcgs: list[float] = []
    started = time.perf_counter()
    for qi, query in enumerate(queries):
        qcode = encode(query[None, :], thresholds)[0]
        order = np.argsort(hamming(codes, qcode), kind="stable")
        target = teacher_ids[qi]
        target_set = set(map(int, target))
        for k in top_ks:
            selected = order[:k]
            overlaps[str(k)].append(float(sum(int(x) in target_set for x in selected)) /
                                    float(min(k, len(target))))
        ndcgs.append(ndcg(order, target, teacher_scores[qi], 10))
    elapsed = (time.perf_counter() - started) * 1000.0
    primary = np.asarray(overlaps[str(max(top_ks))], dtype=np.float64)
    return {"name": name, "partition": partition,
            "payload_bytes_per_vector": payload, "model_bytes": model_bytes,
            "mean_overlap": {key: float(np.mean(value)) for key, value in overlaps.items()},
            "p05_overlap_top{}_".format(max(top_ks)): float(np.quantile(primary, .05)),
            "worst_overlap_top{}_".format(max(top_ks)): float(np.min(primary)),
            "mean_ndcg_at_10": float(np.mean(ndcgs)),
            "worst_ndcg_at_10": float(np.min(ndcgs)),
            "encode_and_scan_p95_ms": float(elapsed / max(1, len(queries))),
            "query_count": len(queries)}


def load_input(path: Path) -> dict[str, np.ndarray]:
    archive = np.load(path, allow_pickle=False)
    required = ("train_vectors", "train_queries", "train_positive_ids",
                "train_negative_ids", "eval_vectors", "eval_queries",
                "eval_teacher_ids", "eval_teacher_scores")
    missing = [name for name in required if name not in archive]
    require(not missing, "LTHQ input missing: " + ", ".join(missing))
    result = {name: np.asarray(archive[name]) for name in required}
    result["eval_partition"] = np.asarray(
        archive["eval_partition"] if "eval_partition" in archive
        else np.full(len(result["eval_queries"]), "all", dtype="U5"))
    return result


def smoke() -> None:
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(96, 16)).astype(np.float32)
    queries = vectors[:12] + rng.normal(scale=.05, size=(12, 16)).astype(np.float32)
    positives = np.asarray([[int(qi)] for qi in range(12)], dtype=np.int64)
    negatives = np.asarray([[int((qi + j + 1) % 96) for j in range(16)]
                            for qi in range(12)], dtype=np.int64)
    thresholds, stats = fit_lthq(vectors, queries, positives, negatives, 4, 3, 8, 1, 1.0)
    require(thresholds.shape == (16, 3), "LTHQ smoke threshold shape differs")
    require(np.all(np.diff(thresholds, axis=1) >= 0), "LTHQ smoke thresholds unordered")
    require(stats["final_pair_loss"] <= stats["initial_pair_loss"] + 1e-6,
            "LTHQ smoke objective regressed")
    print("LTHQ runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--levels", default="3,4,5")
    parser.add_argument("--seed", type=int, default=2026090501)
    parser.add_argument("--candidate-quantiles", type=int, default=32)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--margin", type=float, default=1.0)
    parser.add_argument("--max-train-queries", type=int, default=8141)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        smoke()
        return 0
    require(args.input is not None and args.output is not None,
            "--input and --output are required")
    data = load_input(args.input)
    train_q = data["train_queries"][:args.max_train_queries]
    positives = data["train_positive_ids"][:len(train_q)]
    negatives = data["train_negative_ids"][:len(train_q)]
    eval_q = data["eval_queries"]
    eval_partition = np.asarray(data["eval_partition"])
    require(len(eval_partition) == len(eval_q),
            "LTHQ evaluation partition count differs")
    rows: list[dict[str, Any]] = []
    for levels in (int(x) for x in args.levels.split(",")):
        require(levels >= 2, "LTHQ levels must be >= 2")
        quantile = thresholds_quantile(data["train_vectors"], levels)
        uniform = thresholds_uniform(data["train_vectors"], levels)
        learned, training = fit_lthq(data["train_vectors"], train_q, positives,
                                     negatives, levels, args.seed + levels,
                                     args.candidate_quantiles, args.passes,
                                     args.margin)
        payload = (data["train_vectors"].shape[1] * (levels - 1) + 7) // 8
        model_bytes = int(learned.nbytes)
        for name, thresholds in ((f"thq{levels}_quantile", quantile),
                                 (f"thq{levels}_uniform", uniform),
                                 (f"lthq{levels}_t", learned)):
            for partition in sorted(set(map(str, eval_partition))):
                positions = np.flatnonzero(eval_partition == partition)
                row = evaluate(name, data["eval_vectors"][positions],
                               eval_q[positions],
                               data["eval_teacher_ids"][positions],
                               data["eval_teacher_scores"][positions],
                               thresholds, payload, model_bytes,
                               [10, 32, 64, 256], partition)
                if name.startswith("lthq"):
                    row["training"] = training
                rows.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1,
        "family": "neuroute_lthq_retrieval_supervised",
        "levels": [int(x) for x in args.levels.split(",")],
        "train_query_count": len(train_q), "rows": rows}, indent=2) + "\n",
                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
