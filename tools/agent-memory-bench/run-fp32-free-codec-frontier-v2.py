#!/usr/bin/env python3
"""Frozen whole-posting FP32-free THQ/scalar cascade frontier.

The runner is deliberately an oracle: it evaluates the corrected candidate
stream, records candidate-local FP32 controls, and reports logical bytes. It
does not claim native codec latency, MDBX pages, or production activation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


QUERIES = 152
TOP_K = 10
STAGES = ((3, "ordinal_l1"), (3, "interval_l1"), (3, "interval_sq"),
          (4, "ordinal_l1"), (4, "interval_l1"), (4, "interval_sq"),
          (5, "ordinal_l1"), (5, "interval_l1"), (5, "interval_sq"),
          (8, "ordinal_l1"), (8, "interval_l1"), (8, "interval_sq"))
SHORTLISTS = (64, 128, 256, 512)
PARITY_STAGE = (4, "interval_sq", 128)
PARITY_REPRESENTATIONS = ("int8_linear", "int8_power0625")


def ordinal_payload_bytes(dimension: int, levels: int) -> int:
    """Return packed ordinal level-ID bytes, including no per-record header."""
    require(dimension > 0 and levels >= 2, "invalid ordinal payload shape")
    bits_per_coordinate = int(np.ceil(np.log2(levels)))
    return (dimension * bits_per_coordinate + 7) // 8


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def resolve_artifact(manifest: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else manifest.parent / path


def freeze_references(manifest_path: Path, refs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    frozen: dict[str, dict[str, Any]] = {}
    for name in ("document_vectors", "queries", "teacher_ids", "qrel_ids", "qrel_scores"):
        meta = refs[name]
        path = resolve_artifact(manifest_path, str(meta["path"]))
        require(path.is_file(), f"referenced input is missing: {name}")
        actual_bytes = path.stat().st_size
        if "bytes" in meta:
            require(actual_bytes == int(meta["bytes"]), f"referenced input size differs: {name}")
        frozen[name] = {"bytes": actual_bytes, "sha256": sha256(path)}
    return frozen


def stable_top(ids: np.ndarray, scores: np.ndarray, k: int, ascending: bool) -> np.ndarray:
    order = np.lexsort((ids, scores if ascending else -scores))
    return ids[order[:min(k, len(ids))]]


def aggregate(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()),
            "p05": float(np.percentile(a, 5)), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:TOP_K]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in grades.values()]))[::-1][:TOP_K]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def interval_costs(thresholds: np.ndarray, query: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    levels = thresholds.shape[1] + 1
    edges = np.concatenate((np.full((thresholds.shape[0], 1), -np.inf, dtype=np.float32),
                            thresholds, np.full((thresholds.shape[0], 1), np.inf, dtype=np.float32)), axis=1)
    costs = np.empty((thresholds.shape[0], levels), dtype=np.float32)
    for level in range(levels):
        left = edges[:, level]
        right = edges[:, level + 1]
        distance = np.where(query < left, left - query,
                            np.where(query >= right, query - right, 0.0))
        costs[:, level] = np.nan_to_num(distance, nan=0.0, posinf=0.0, neginf=0.0)
    return costs, costs.copy(), costs * costs


def scalar_score(vectors: np.ndarray, query: np.ndarray, bits: int, power: float) -> np.ndarray:
    transformed = np.copysign(np.power(np.abs(vectors), power), vectors)
    limit = (1 << (bits - 1)) - 1
    scale = np.maximum(np.max(np.abs(transformed), axis=1, keepdims=True) / limit, 1e-8)
    codes = np.rint(transformed / scale).clip(-limit, limit)
    decoded = codes * scale
    if power != 1.0:
        decoded = np.copysign(np.power(np.abs(decoded), 1.0 / power), decoded)
    return np.asarray(decoded @ query, dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--training-count", type=int, default=100_000)
    args = parser.parse_args()
    manifest = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    candidate_receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    candidate_raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    require(candidate_receipt["flat_file"]["sha256"] == sha256(args.candidate_flat), "candidate payload SHA differs")
    require(candidate_receipt["raw_sha256"] == sha256(args.candidate_raw), "candidate raw SHA differs")
    n, dimension, queries = int(manifest["documents"]), int(manifest["dimension"]), int(manifest["queries"])
    require(queries == QUERIES, "query count differs")
    refs = manifest["references"]
    input_files = freeze_references(args.thq_manifest, refs)
    docs = np.memmap(resolve_artifact(args.thq_manifest, refs["document_vectors"]["path"]), mode="r", dtype="<f4", shape=(n, dimension))
    query_vectors = np.memmap(resolve_artifact(args.thq_manifest, refs["queries"]["path"]), mode="r", dtype="<f4", shape=(queries, dimension))
    teachers = np.memmap(resolve_artifact(args.thq_manifest, refs["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(queries, 10))
    qrel_ids = np.memmap(resolve_artifact(args.thq_manifest, refs["qrel_ids"]["path"]), mode="r", dtype="<i8", shape=(queries, 20))
    qrel_scores = np.memmap(resolve_artifact(args.thq_manifest, refs["qrel_scores"]["path"]), mode="r", dtype="<f4", shape=(queries, 20))
    train_count = min(args.training_count, n)
    training = np.asarray(docs[:train_count])
    rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    direct_rows: list[dict[str, Any]] = []
    direct_vs_cascade_parity: list[dict[str, Any]] = []
    counts = [int(row["candidate_count"]) for row in candidate_raw["rows"]]
    flat = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8,
                     shape=(sum(counts), 148))
    offset = 0
    threshold_cache: dict[int, np.ndarray] = {}
    for qi, count in enumerate(counts):
        ids = np.frombuffer(np.asarray(flat[offset:offset + count, :4]).tobytes(), dtype="<i4").astype(np.int64)
        offset += count
        vectors = np.asarray(docs[ids], dtype=np.float32)
        query = np.asarray(query_vectors[qi], dtype=np.float32)
        fp32_scores = vectors @ query
        fp32_top10 = stable_top(ids, fp32_scores, TOP_K, ascending=False)
        teacher = np.asarray(teachers[qi])
        scalar_scores: dict[str, np.ndarray] = {"fp32": fp32_scores, "fp16": vectors.astype(np.float16).astype(np.float32) @ query}
        scalar_payloads = {"fp32": 1536, "fp16": 768}
        for bits in (4, 5, 6, 7, 8, 9, 10, 12):
            for power, suffix in ((1.0, "linear"), (0.5, "power05"),
                                  (0.625, "power0625"), (0.75, "power075"),
                                  (0.875, "power0875")):
                name = f"int{bits}_{suffix}"
                scalar_scores[name] = scalar_score(vectors, query, bits, power)
                scalar_payloads[name] = (dimension * bits + 7) // 8 + 4
        direct_rankings: dict[str, np.ndarray] = {}
        direct_qrels: dict[str, float] = {}
        for name, scores in scalar_scores.items():
            ranked = stable_top(ids, scores, TOP_K, ascending=False)
            direct_rankings[name] = ranked
            direct_qrels[name] = ndcg(ranked, np.asarray(qrel_ids[qi]), np.asarray(qrel_scores[qi]))
            direct_rows.append({"query": qi, "representation": name,
                                "candidate_count": count, "payload_bytes_per_document": scalar_payloads[name],
                                "exact_top10_overlap": float(np.intersect1d(ranked, fp32_top10).size / TOP_K),
                                "teacher_top10_recall": float(np.isin(teacher, ranked).sum() / len(teacher)),
                                "qrels_ndcg10": ndcg(ranked, np.asarray(qrel_ids[qi]), np.asarray(qrel_scores[qi]))})
        for levels, mode in STAGES:
            if levels not in threshold_cache:
                fractions = np.arange(1, levels, dtype=np.float32) / levels
                threshold_cache[levels] = np.quantile(training, fractions, axis=0).T.astype(np.float32)
            thresholds = threshold_cache[levels]
            doc_levels = np.sum(vectors[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
            qlevel = np.sum(query[:, None] > thresholds, axis=1, dtype=np.uint8)
            if mode == "ordinal_l1":
                scores = np.abs(doc_levels.astype(np.int16) - qlevel[None, :]).sum(axis=1, dtype=np.float32)
                ascending = True
            else:
                interval_l1 = np.empty((dimension, levels), dtype=np.float32)
                for level in range(levels):
                    lo = -np.inf if level == 0 else thresholds[:, level - 1]
                    hi = np.inf if level == levels - 1 else thresholds[:, level]
                    interval_l1[:, level] = np.nan_to_num(np.where(query < lo, lo - query,
                                                                     np.where(query >= hi, query - hi, 0.0)))
                scores = interval_l1[np.arange(dimension)[None, :], doc_levels].sum(axis=1, dtype=np.float32)
                if mode == "interval_sq":
                    # Squared interval ADC is the sum of per-coordinate
                    # squared distances, not the square of aggregate L1.
                    scores = (interval_l1 * interval_l1)[np.arange(dimension)[None, :], doc_levels].sum(axis=1, dtype=np.float32)
                ascending = True
            for shortlist in SHORTLISTS:
                selected = stable_top(ids, scores, shortlist, ascending=ascending)
                row = {"query": qi, "levels": levels, "mode": mode, "shortlist": shortlist,
                       "candidate_count": count, "payload_bytes_per_document": ordinal_payload_bytes(dimension, levels),
                       "exact_top10_overlap": float(np.intersect1d(selected, fp32_top10).size / TOP_K),
                       "teacher_top10_recall": float(np.isin(teacher, selected).sum() / len(teacher)),
                       "qrels_ndcg10": ndcg(selected, np.asarray(qrel_ids[qi]), np.asarray(qrel_scores[qi]))}
                stage_rows.append(row)
                for name, scalar in scalar_scores.items():
                    if name == "fp32":
                        continue
                    positions = {int(doc): index for index, doc in enumerate(ids)}
                    selected_scores = scalar[np.asarray([positions[int(doc)] for doc in selected], dtype=np.int64)]
                    reranked = stable_top(selected, selected_scores, TOP_K, ascending=False)
                    final_rows.append({"query": qi, "levels": levels, "mode": mode, "shortlist": shortlist,
                                       "representation": name, "candidate_count": count,
                                       "payload_bytes_per_document": row["payload_bytes_per_document"] + scalar_payloads[name],
                                       "exact_top10_overlap": float(np.intersect1d(reranked, fp32_top10).size / TOP_K),
                                       "teacher_top10_recall": float(np.isin(teacher, reranked).sum() / len(teacher)),
                                       "qrels_ndcg10": ndcg(reranked, np.asarray(qrel_ids[qi]), np.asarray(qrel_scores[qi]))})
                    if (levels, mode, shortlist) == PARITY_STAGE and name in PARITY_REPRESENTATIONS:
                        direct_top10 = direct_rankings[name]
                        cascade_qrels = ndcg(reranked, np.asarray(qrel_ids[qi]), np.asarray(qrel_scores[qi]))
                        direct_vs_cascade_parity.append({
                            "query": qi,
                            "representation": name,
                            "shortlist_ids": [int(value) for value in selected.tolist()],
                            "direct_top10_ids": [int(value) for value in direct_top10.tolist()],
                            "cascade_top10_ids": [int(value) for value in reranked.tolist()],
                            "direct_top10_set_overlap": float(np.intersect1d(direct_top10, reranked).size / TOP_K),
                            "exact_ordered_top10_parity": bool(np.array_equal(direct_top10, reranked)),
                            "direct_top10_survival_in_thq_shortlist": float(np.isin(direct_top10, selected).sum() / TOP_K),
                            "qrels_ndcg10_delta_vs_direct": float(cascade_qrels - direct_qrels[name]),
                        })
    def summarize(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for item in items:
            groups.setdefault(tuple(item.get(key) for key in keys), []).append(item)
        result = []
        for group, values in groups.items():
            row = {key: value for key, value in zip(keys, group)}
            for metric in ("exact_top10_overlap", "teacher_top10_recall", "qrels_ndcg10"):
                row[metric] = aggregate([float(value[metric]) for value in values])
            row["query_count"] = len(values)
            result.append(row)
        return result
    def paired_direct_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_query = {(int(row["query"]), str(row["representation"])): row for row in items}
        representations = sorted({str(row["representation"]) for row in items} - {"fp32"})
        rng = np.random.default_rng(20260915)
        result = []
        for representation in representations:
            deltas = np.asarray([
                float(by_query[(query, representation)]["qrels_ndcg10"]) -
                float(by_query[(query, "fp32")]["qrels_ndcg10"])
                for query in range(QUERIES)
            ], dtype=np.float64)
            samples = rng.integers(0, QUERIES, size=(10_000, QUERIES))
            bootstrap_means = deltas[samples].mean(axis=1)
            result.append({
                "representation": representation,
                "qrels_ndcg10_delta_vs_candidate_fp32": aggregate(deltas.tolist()),
                "maximum_positive_ndcg_loss": float(np.maximum(-deltas, 0.0).max()),
                "paired_bootstrap_mean_delta_95ci": {
                    "low": float(np.percentile(bootstrap_means, 2.5)),
                    "high": float(np.percentile(bootstrap_means, 97.5)),
                    "resamples": 10_000,
                    "seed": 20260915,
                },
            })
        return result

    def summarize_cascade_parity(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rng = np.random.default_rng(20260916)
        result = []
        for representation in PARITY_REPRESENTATIONS:
            values = [row for row in items if row["representation"] == representation]
            deltas = np.asarray([float(row["qrels_ndcg10_delta_vs_direct"]) for row in values], dtype=np.float64)
            samples = rng.integers(0, len(values), size=(10_000, len(values)))
            means = deltas[samples].mean(axis=1)
            result.append({
                "representation": representation,
                "stage": {"levels": PARITY_STAGE[0], "mode": PARITY_STAGE[1], "shortlist": PARITY_STAGE[2]},
                "direct_top10_set_overlap": aggregate([float(row["direct_top10_set_overlap"]) for row in values]),
                "direct_top10_survival_in_thq_shortlist": aggregate([float(row["direct_top10_survival_in_thq_shortlist"]) for row in values]),
                "exact_ordered_top10_parity_rate": float(np.mean([bool(row["exact_ordered_top10_parity"]) for row in values])),
                "qrels_ndcg10_delta_vs_direct": aggregate(deltas.tolist()),
                "maximum_positive_ndcg_loss": float(np.maximum(-deltas, 0.0).max()),
                "paired_bootstrap_mean_delta_95ci": {
                    "low": float(np.percentile(means, 2.5)), "high": float(np.percentile(means, 97.5)),
                    "resamples": 10_000, "seed": 20260916,
                },
            })
        return result
    raw = {"schema_version": 4, "family": "semantic_fp32_free_codec_frontier_v2",
           "execution_status": "EXECUTED", "production_activation": False,
           "protocol": {"candidate_semantics": "corrected whole-posting R4 stream",
                        "training_count": train_count, "stages": "THQ3/4/5/8 × ordinal-L1/interval-L1/interval-squared",
                        "payload_accounting": "ordinal level IDs: ceil(dimension * ceil(log2(levels)) / 8); scalar records include one FP32 scale",
                        "shortlists": list(SHORTLISTS), "scalar_final": "FP16; INT4/5/6/7/8/9/10/12 linear and power-.5/.625/.75/.875",
                        "scores": "candidate-local FP32 top-10 reference; qrels nDCG@10"},
           "inputs": {"thq_manifest_sha256": sha256(args.thq_manifest), "candidate_receipt_sha256": sha256(args.candidate_receipt),
                      "candidate_raw_sha256": sha256(args.candidate_raw), "candidate_flat_sha256": sha256(args.candidate_flat)},
           "input_files": input_files,
           "stage_rows": stage_rows, "final_rows": final_rows, "direct_rows": direct_rows,
           "direct_vs_cascade_parity": direct_vs_cascade_parity,
           "stage_summary": summarize(stage_rows, ("levels", "mode", "shortlist")),
           "final_summary": summarize(final_rows, ("levels", "mode", "shortlist", "representation")),
           "direct_summary": summarize(direct_rows, ("representation",)),
           "paired_direct_summary": paired_direct_summary(direct_rows),
           "direct_vs_cascade_parity_summary": summarize_cascade_parity(direct_vs_cascade_parity)}
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = (json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 4, "family": raw["family"], "execution_status": "EXECUTED", "production_activation": False,
               "runner_sha256": sha256(Path(__file__)), "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
               "sha256": hashlib.sha256(raw_bytes).hexdigest()}, "inputs": raw["inputs"], "input_files": input_files,
               "row_counts": {"stage": len(stage_rows), "final": len(final_rows), "direct": len(direct_rows)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt["row_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
