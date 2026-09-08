#!/usr/bin/env python3
"""Measure semantic-landmark affinity ceilings and THQ locality.

The experiment deliberately separates the FP32 affinity oracle from the
quantized representation.  Landmarks are spherical k-means centroids in the
frozen E5 document space.  Each document/query is represented by its dot
product with every landmark; per-landmark quantile thermometer thresholds are
then used to form THQ3/THQ4 signatures.  No retrieval result is fabricated:
all quality numbers come from the frozen exact teacher and qrels.
"""
from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import faiss
import numpy as np

POPCOUNT = np.asarray([value.bit_count() for value in range(256)], dtype=np.uint8)


def close_memmap(array: np.memmap | None) -> None:
    """Release a Windows memory-mapped file before deleting its path."""
    if array is not None:
        mmap = getattr(array, "_mmap", None)
        if mmap is not None:
            mmap.close()


def ndcg(predicted: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(i): float(s) for i, s in zip(qrel_ids, qrel_scores)}
    gains = np.asarray([grades.get(int(i), 0.0) for i in predicted[:10]], dtype=np.float64)
    discounts = 1.0 / np.log2(np.arange(2, 12, dtype=np.float64))
    ideal = np.sort(qrel_scores.astype(np.float64))[::-1][:10]
    denominator = float(np.sum(ideal * discounts))
    return float(np.sum(gains * discounts) / denominator) if denominator else 0.0


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int, descending: bool) -> np.ndarray:
    if len(scores) <= limit:
        order = np.lexsort((ids, -scores if descending else scores))
        return ids[order]
    key = -scores if descending else scores
    selected = np.argpartition(key, limit - 1)[:limit]
    selected_ids = ids[selected]
    order = np.lexsort((selected_ids, key[selected]))
    return selected_ids[order]


def decode_paths(manifest_path: Path) -> tuple[dict, Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    return manifest, root


def path_from(row: dict, root: Path) -> Path:
    value = Path(row["path"])
    return value if value.is_absolute() else (root / value).resolve()


def row_values(manifest: dict, root: Path, name: str) -> np.ndarray:
    row = manifest["outputs"][name]
    return np.fromfile(path_from(row, root), dtype=np.dtype(row["dtype"])).reshape(row["shape"])


def pack_affinity(values: np.ndarray, thresholds: np.ndarray, bits: int) -> np.ndarray:
    levels = np.arange(1, bits + 1, dtype=np.float32)
    # A thermometer bit is true when affinity exceeds its per-landmark quantile.
    thresholds = np.asarray(thresholds, dtype=np.float32)
    if thresholds.shape[1] != bits:
        raise ValueError("threshold count does not match bits")
    flags = values[:, :, None] > thresholds[None, :, :]
    # Landmark-major ordering matches the existing THQ materialization.
    flat = flags.reshape(len(values), -1)
    return np.packbits(flat, axis=1, bitorder="little")


def hamming_top(codes: np.ndarray, query_code: np.ndarray, limit: int,
                chunk: int) -> tuple[np.ndarray, np.ndarray]:
    ids = np.arange(len(codes), dtype=np.int64)
    best_dist = np.empty(limit, dtype=np.uint16)
    best_ids = np.empty(limit, dtype=np.int64)
    filled = 0
    for first in range(0, len(codes), chunk):
        stop = min(len(codes), first + chunk)
        distances = POPCOUNT[np.bitwise_xor(codes[first:stop], query_code)].sum(axis=1, dtype=np.uint16)
        take = min(limit, len(distances))
        local = np.argpartition(distances, take - 1)[:take]
        local_ids = ids[first:stop][local]
        local_dist = distances[local]
        if filled:
            candidate_ids = np.concatenate((best_ids[:filled], local_ids))
            candidate_dist = np.concatenate((best_dist[:filled], local_dist))
        else:
            candidate_ids, candidate_dist = local_ids, local_dist
        order = np.lexsort((candidate_ids, candidate_dist))[:limit]
        filled = min(limit, len(order))
        best_ids[:filled] = candidate_ids[order]
        best_dist[:filled] = candidate_dist[order]
    return best_ids[:filled], best_dist[:filled]


def hamming_teacher_ranks(codes: np.ndarray, query_code: np.ndarray,
                          teacher_ids: np.ndarray, chunk: int) -> np.ndarray:
    """Return stable 1-based ranks of teacher ids in a full Hamming scan."""
    teacher_ids = np.asarray(teacher_ids, dtype=np.int64)
    target_dist = POPCOUNT[np.bitwise_xor(codes[teacher_ids], query_code)].sum(axis=1, dtype=np.uint16)
    ranks = np.ones(len(teacher_ids), dtype=np.int64)
    all_ids = np.arange(len(codes), dtype=np.int64)
    for first in range(0, len(codes), chunk):
        stop = min(len(codes), first + chunk)
        distances = POPCOUNT[np.bitwise_xor(codes[first:stop], query_code)].sum(axis=1, dtype=np.uint16)
        ids = all_ids[first:stop]
        for position, distance in enumerate(target_dist):
            ranks[position] += int(np.count_nonzero((distances < distance) |
                                                     ((distances == distance) & (ids < teacher_ids[position]))))
    return ranks


def summarize(rows: list[dict], budgets: tuple[int, ...]) -> dict:
    result: dict[str, dict] = {}
    for budget in budgets:
        selected = [row for row in rows if row["budget"] == budget]
        if not selected:
            continue
        result[str(budget)] = {}
        for metric in ("teacher_survival", "ndcg_at_10", "query_ms"):
            values = np.asarray([row[metric] for row in selected], dtype=np.float64)
            result[str(budget)][metric] = {
                "mean": float(values.mean()),
                "p05": float(np.quantile(values, 0.05)),
                "p50": float(np.quantile(values, 0.50)),
                "p95": float(np.quantile(values, 0.95)),
                "worst": float(values.min()),
            }
    return result


def summarize_ranks(rows: list[dict]) -> dict:
    values = np.asarray([rank for row in rows for rank in row.get("teacher_ranks", [])], dtype=np.float64)
    if not len(values):
        return {}
    return {"r50": float(np.quantile(values, .50)), "r95": float(np.quantile(values, .95)),
            "r99": float(np.quantile(values, .99)), "worst": float(values.max())}


def run(args: argparse.Namespace) -> dict:
    manifest, root = decode_paths(args.manifest)
    count = min(int(manifest["documents"]), args.max_documents or int(manifest["documents"]))
    query_count = min(int(manifest["queries"]), args.max_queries or int(manifest["queries"]))
    dim = int(manifest["dimension"])
    refs = manifest["references"]
    docs = np.memmap(path_from(refs["document_vectors"], root), mode="r", dtype="<f4",
                     shape=(int(manifest["documents"]), dim))
    queries = np.fromfile(path_from(refs["queries"], root), dtype="<f4").reshape(-1, dim)[:query_count]
    teacher = np.fromfile(path_from(refs["teacher_ids"], root), dtype="<i8").reshape(-1, 10)[:query_count]
    qrel_ids = np.fromfile(path_from(refs["qrel_ids"], root), dtype="<i8").reshape(-1, 20)[:query_count]
    qrel_scores = np.fromfile(path_from(refs["qrel_scores"], root), dtype="<f4").reshape(-1, 20)[:query_count]
    train_count = min(args.train_documents, count)
    train = np.asarray(docs[:train_count], dtype=np.float32)
    budgets = tuple(sorted(set(int(x) for x in args.budgets.split(","))))
    max_budget = max(budgets)
    output = {"schema_version": 1, "family": "landmark_affinity_thq_v1",
              "documents": count, "queries": query_count, "dimension": dim,
              "train_documents": train_count, "landmarks": {},
              "protocol": {"landmarks": "spherical_faiss_kmeans_on_E5_prefix",
                           "affinity": "landmark_inner_product",
                           "quantization": "per-landmark training-prefix quantile thermometer",
                           "teacher": "frozen exact E5 top-10"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for m in args.landmarks:
        started = time.perf_counter()
        kmeans = faiss.Kmeans(dim, m, niter=args.kmeans_iterations, nredo=1,
                              seed=args.seed + m, spherical=True, verbose=False, gpu=False)
        kmeans.train(train)
        centroids = np.asarray(kmeans.centroids, dtype=np.float32).copy()
        train_affinity = train @ centroids.T
        query_affinity = np.asarray(queries, dtype=np.float32) @ centroids.T
        ids = np.arange(count, dtype=np.int64)
        affinity_path = args.output.with_name(f"{args.output.stem}-m{m}-affinity.f32")
        affinity_docs = np.memmap(affinity_path, mode="w+", dtype="<f4", shape=(count, m))
        for first in range(0, count, args.chunk):
            stop = min(count, first + args.chunk)
            affinity_docs[first:stop] = np.asarray(docs[first:stop]) @ centroids.T
        affinity_docs.flush()
        del affinity_docs
        affinity_docs = np.memmap(affinity_path, mode="r", dtype="<f4", shape=(count, m))
        oracle_rows = []
        for qi in range(query_count):
            # Stream the affinity profile so the 1M x M matrix is never copied.
            scores = np.empty(count, dtype=np.float32)
            started_q = time.perf_counter()
            for first in range(0, count, args.chunk):
                stop = min(count, first + args.chunk)
                scores[first:stop] = np.asarray(affinity_docs[first:stop]) @ query_affinity[qi]
            row = {"query": qi, "query_ms": (time.perf_counter() - started_q) * 1000.0}
            for budget in budgets:
                selected = top_ids(scores, ids, min(budget, count), True)
                survival = float(np.isin(teacher[qi], selected).sum()) / 10.0
                row["budget"] = budget
                row.setdefault("oracle", {})[str(budget)] = {
                    "teacher_survival": survival,
                    "ndcg_at_10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                }
            oracle_rows.append(row)
        entry: dict = {"centroid_training_ms": (time.perf_counter() - started) * 1000.0,
                       "centroid_bytes": int(centroids.nbytes), "oracle": {}}
        for budget in budgets:
            vals = [row["oracle"][str(budget)] for row in oracle_rows]
            for key in ("teacher_survival", "ndcg_at_10"):
                arr = np.asarray([v[key] for v in vals], dtype=np.float64)
                entry["oracle"].setdefault(str(budget), {})[key] = {
                    "mean": float(arr.mean()), "p05": float(np.quantile(arr, .05)),
                    "p50": float(np.quantile(arr, .5)), "p95": float(np.quantile(arr, .95)),
                    "worst": float(arr.min())}
        for bits in args.bits:
            thresholds = np.quantile(train_affinity, np.arange(1, bits + 1, dtype=np.float32) / (bits + 1), axis=0).T
            code_bytes = (m * bits + 7) // 8
            codes_path = args.output.with_name(f"{args.output.stem}-m{m}-thq{bits}.u8")
            codes = np.memmap(codes_path, mode="w+", dtype=np.uint8, shape=(count, code_bytes))
            for first in range(0, count, args.chunk):
                stop = min(count, first + args.chunk)
                affinity = np.asarray(affinity_docs[first:stop])
                codes[first:stop] = pack_affinity(affinity, thresholds, bits)
            codes.flush(); del codes
            query_codes = pack_affinity(query_affinity, thresholds, bits)
            codes = np.memmap(codes_path, mode="r", dtype=np.uint8, shape=(count, code_bytes))
            rows = []
            started_code = time.perf_counter()
            for qi in range(query_count):
                query_started = time.perf_counter()
                selected, _ = hamming_top(codes, query_codes[qi], max_budget, args.chunk)
                teacher_ranks = hamming_teacher_ranks(codes, query_codes[qi], teacher[qi], args.chunk) \
                    if not args.skip_ranks else np.asarray([], dtype=np.int64)
                query_ms = (time.perf_counter() - query_started) * 1000.0
                for budget in budgets:
                    chosen = selected[:min(budget, len(selected))]
                    rows.append({"query": qi, "budget": budget,
                                 "teacher_survival": float(np.isin(teacher[qi], chosen).sum()) / 10.0,
                                 "ndcg_at_10": ndcg(chosen, qrel_ids[qi], qrel_scores[qi]),
                                 "query_ms": query_ms,
                                 "teacher_ranks": teacher_ranks.tolist()})
            elapsed_ms = (time.perf_counter() - started_code) * 1000.0
            entry.setdefault("thq", {})[str(bits)] = {
                "bytes_per_document": code_bytes, "scan_total_ms": elapsed_ms,
                "summary": summarize(rows, budgets),
                "rank_summary": summarize_ranks(rows),
                "thresholds": "training_prefix_quantiles",
                "code_path": str(codes_path.resolve())}
            close_memmap(codes)
            del codes
            gc.collect()
            if not args.keep_codes:
                codes_path.unlink()
        close_memmap(affinity_docs)
        del affinity_docs
        gc.collect()
        if not args.keep_codes:
            affinity_path.unlink()
        output["landmarks"][str(m)] = entry
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--landmarks", default="32,64,128,256")
    parser.add_argument("--bits", default="3,4")
    parser.add_argument("--budgets", default="256,1000,5000,10000")
    parser.add_argument("--train-documents", type=int, default=100000)
    parser.add_argument("--max-documents", type=int, default=0)
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--chunk", type=int, default=20000)
    parser.add_argument("--kmeans-iterations", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--keep-codes", action="store_true")
    parser.add_argument("--skip-ranks", action="store_true",
                        help="Skip the additional full-scan teacher rank measurement")
    args = parser.parse_args()
    args.landmarks = tuple(int(x) for x in args.landmarks.split(","))
    args.bits = tuple(int(x) for x in args.bits.split(","))
    result = run(args)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
