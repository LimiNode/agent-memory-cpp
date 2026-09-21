#!/usr/bin/env python3
"""Independent source replay for the THQ4 + Faiss RQ32/RQ48 experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


D = 384
THQ_BYTES = 96
TOP = 128
QUERY_COUNT = 152
PAYLOADS = (32, 48)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    values = np.asarray(codes, dtype=np.uint8)
    levels = np.empty((len(values), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        packed = values[:, byte]
        levels[:, 4 * byte + 0] = packed & 3
        levels[:, 4 * byte + 1] = (packed >> 2) & 3
        levels[:, 4 * byte + 2] = (packed >> 4) & 3
        levels[:, 4 * byte + 3] = (packed >> 6) & 3
    return levels


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int = 10) -> np.ndarray:
    values = np.asarray(scores, dtype=np.float64)
    return ids[np.lexsort((ids, -values))[:count]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    values64 = np.asarray(values, dtype=np.float64)
    query64 = np.asarray(query, dtype=np.float64)
    denominator = np.linalg.norm(values64, axis=1) * np.linalg.norm(query64)
    denominator = np.maximum(denominator, np.finfo(np.float64).tiny)
    return (values64 @ query64) / denominator


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    relevance = {int(doc): float(value) for doc, value in zip(qids, grades)
                 if int(doc) >= 0 and float(value) > 0.0}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in relevance.values()]))[::-1][:10]
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal_discounts = np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))
    ideal_dcg = float(np.sum(ideal / ideal_discounts)) if len(ideal) else 0.0
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2,
                    dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0, dtype=np.float64).astype(np.float32)
    for coordinate in range(D):
        for level in range(4):
            selected = train[levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = (
                float(np.mean(selected)) if len(selected) else fallback[coordinate])
    return centroids


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray,
                 thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float64)
    for coordinate in range(D):
        value = float(query[coordinate])
        for level in range(4):
            low = -np.inf if level == 0 else float(thresholds[coordinate, level - 1])
            high = np.inf if level == 3 else float(thresholds[coordinate, level])
            delta = low - value if value < low else value - high if value > high else 0.0
            lut[coordinate, level] = delta * delta
    scores = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, scores))[:min(TOP, len(ids))]]


def decode_residual(codebooks: np.ndarray, codes: np.ndarray) -> np.ndarray:
    stages = codes.shape[1]
    require(stages in PAYLOADS, "persisted code width differs")
    decoded = np.zeros((len(codes), D), dtype=np.float64)
    for stage in range(stages):
        decoded += np.asarray(codebooks[stage, codes[:, stage]], dtype=np.float64)
    return decoded


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    raw_value = json.loads(raw.read_text(encoding="utf-8"))
    rows = raw_value.get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT,
            "candidate raw row count differs")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    require(flat.stat().st_size == int(offsets[-1]) * 148,
            "candidate flat/raw cardinality differs")
    records = np.memmap(flat, mode="r", dtype=np.uint8,
                        shape=(int(offsets[-1]), 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    require(np.all((ids >= 0) & (ids < 1_000_000)),
            "candidate document ID outside corpus")
    for start, stop in zip(offsets[:-1], offsets[1:]):
        require(len(np.unique(ids[start:stop])) == int(stop - start),
                "candidate row contains duplicate document IDs")
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    require(receipt_value.get("execution_status") == "EXECUTED",
            "candidate receipt is not EXECUTED")
    require(receipt_value.get("raw_sha256") == sha256(raw),
            "candidate raw receipt binding differs")
    require(receipt_value.get("flat_file", {}).get("sha256") == sha256(flat),
            "candidate flat receipt binding differs")
    return ids, offsets


def self_test() -> None:
    codebooks = np.zeros((48, 256, D), dtype=np.float32)
    codebooks[0, 7, 0] = 1.25
    codebooks[31, 9, 1] = -0.5
    codes = np.zeros((2, 32), dtype=np.uint8)
    codes[:, 0] = 7
    codes[:, 31] = 9
    decoded = decode_residual(codebooks, codes)
    require(decoded.shape == (2, D), "manual decoder shape differs")
    require(np.array_equal(decoded[:, :2], np.asarray([[1.25, -0.5], [1.25, -0.5]])),
            "manual decoder sum differs")
    packed = np.asarray([[0b11100100] * THQ_BYTES], dtype=np.uint8)
    require(np.array_equal(unpack_thq(packed)[0, :4], np.arange(4, dtype=np.uint8)),
            "independent THQ unpack differs")
    print("THQ Faiss RQ replay audit self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    for name in ("result", "runner", "models", "codes", "documents",
                 "train-vectors", "queries", "qrel-ids", "qrel-scores",
                 "teacher-ids", "thq4-codes", "thq4-thresholds",
                 "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--expected-faiss-seed", type=int, default=20260921)
    parser.add_argument("--expected-faiss-threads", type=int, default=8)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.result, args.runner, args.models, args.codes, args.documents,
                args.train_vectors, args.queries, args.qrel_ids, args.qrel_scores,
                args.teacher_ids, args.thq4_codes, args.thq4_thresholds,
                args.candidate_flat, args.candidate_raw, args.candidate_receipt,
                args.output)
    if any(path is None for path in required):
        parser.error("all source, artifact, runner, result, and output paths are required")
    for path in required[:-1]:
        require(path.is_file(), f"audit input missing: {path}")

    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_faiss_rq_replay_v1",
            "result family differs")
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True,
            "result is not source-replay evidence")
    require(result.get("runner_sha256") == sha256(args.runner),
            "runner/result SHA binding differs")
    require(result.get("query_count") == QUERY_COUNT and result.get("metric") == "cosine",
            "query or metric contract differs")
    require(result.get("faiss_version") == "1.15.0",
            "Faiss version differs from the pinned control")
    require(result.get("faiss_train_type") == "Train_default",
            "Faiss training type differs")
    require(result.get("faiss_clustering_seed") == args.expected_faiss_seed
            and result.get("faiss_threads") == args.expected_faiss_threads,
            "Faiss seed or thread-count provenance differs")
    require(result.get("fit_rows") == 25_000 and result.get("fit_strategy") == "all_train_rows",
            "canonical full training replay was not used")
    require(result.get("iterations") == 8 and result.get("fit_beam") == 1
            and result.get("encode_beam") == 8, "Faiss fit/encode protocol differs")
    require(result.get("shared_fit") is True and result.get("fit_stage_count") == 48
            and result.get("prefix_payload_bytes") == [32, 48],
            "shared-prefix protocol differs")
    fit_indices = np.arange(25_000, dtype="<i8")
    require(result.get("fit_indices_sha256") == sha256_bytes(fit_indices.tobytes()),
            "fit row identity differs")

    sources = {
        "documents": args.documents,
        "train_vectors": args.train_vectors,
        "queries": args.queries,
        "qrel_ids": args.qrel_ids,
        "qrel_scores": args.qrel_scores,
        "teacher_ids": args.teacher_ids,
        "thq4_codes": args.thq4_codes,
        "thq4_thresholds": args.thq4_thresholds,
        "candidate_flat": args.candidate_flat,
        "candidate_raw": args.candidate_raw,
        "candidate_receipt": args.candidate_receipt,
    }
    source_hashes = {name: sha256(path) for name, path in sources.items()}
    require(result.get("source_hashes") == source_hashes,
            "result/source SHA binding differs")
    artifact_hashes = {"models": sha256(args.models), "codes": sha256(args.codes)}
    require(result.get("artifact_hashes") == artifact_hashes,
            "result/artifact SHA binding differs")

    require(args.documents.stat().st_size == 1_000_000 * D * 4,
            "document source shape differs")
    require(args.train_vectors.stat().st_size == 25_000 * D * 4,
            "training source shape differs")
    require(args.queries.stat().st_size == QUERY_COUNT * D * 4,
            "query source shape differs")
    require(args.qrel_ids.stat().st_size == QUERY_COUNT * 20 * 8,
            "qrel ID source shape differs")
    require(args.qrel_scores.stat().st_size == QUERY_COUNT * 20 * 4,
            "qrel score source shape differs")
    require(args.teacher_ids.stat().st_size == QUERY_COUNT * 10 * 8,
            "teacher source shape differs")
    require(args.thq4_codes.stat().st_size == 1_000_000 * THQ_BYTES,
            "THQ code source shape differs")
    require(args.thq4_thresholds.stat().st_size == D * 3 * 4,
            "THQ threshold source shape differs")

    documents = np.memmap(args.documents, mode="r", dtype="<f4",
                          shape=(1_000_000, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(25_000, D)), dtype=np.float32)
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8",
                         shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4",
                            shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8",
                            shape=(QUERY_COUNT, 10))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                          shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    candidate_ids, offsets = load_candidates(
        args.candidate_flat, args.candidate_raw, args.candidate_receipt)

    with np.load(args.models, allow_pickle=False) as model_archive:
        require(set(model_archive.files) == {"centroids", "rq_codebooks"},
                "model artifact member set differs")
        persisted_centroids = np.asarray(model_archive["centroids"], dtype=np.float32)
        codebooks = np.asarray(model_archive["rq_codebooks"], dtype=np.float32)
    require(persisted_centroids.shape == (D, 4), "centroid artifact shape differs")
    require(codebooks.shape == (48, 256, D), "RQ codebook artifact shape differs")
    require(np.isfinite(persisted_centroids).all() and np.isfinite(codebooks).all(),
            "model artifact contains non-finite values")
    recomputed_centroids = fit_centroids(train, thresholds)
    require(np.allclose(persisted_centroids, recomputed_centroids, rtol=0.0, atol=1e-7),
            "persisted THQ centroids differ from independent training replay")

    with np.load(args.codes, allow_pickle=False) as code_archive:
        require(set(code_archive.files) == {"selected_ids", "codes_32", "codes_48"},
                "code artifact member set differs")
        selected_ids = np.asarray(code_archive["selected_ids"], dtype=np.int64)
        persisted_codes = {
            payload: np.asarray(code_archive[f"codes_{payload}"], dtype=np.uint8)
            for payload in PAYLOADS
        }
    require(selected_ids.shape == (QUERY_COUNT, TOP),
            "persisted THQ selection shape differs")
    for payload in PAYLOADS:
        require(persisted_codes[payload].shape == (QUERY_COUNT, TOP, payload),
                f"RQ{payload} code shape differs")

    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT * len(PAYLOADS),
            "result row count differs")
    row_map = {(int(row["query"]), str(row["arm"])): row for row in rows}
    require(len(row_map) == len(rows), "result rows are not unique")
    require(set(row_map) == {(query, f"faiss_rq{payload}")
                             for query in range(QUERY_COUNT) for payload in PAYLOADS},
            "result arm/query matrix differs")

    metrics = {payload: {"ndcg": [], "candidate_overlap": [], "teacher_overlap": [],
                         "reconstruction_mse": [], "score_mae": []}
               for payload in PAYLOADS}
    top10_by_payload: dict[int, list[np.ndarray]] = {payload: [] for payload in PAYLOADS}
    for query_index in range(QUERY_COUNT):
        query = np.asarray(queries[query_index], dtype=np.float32)
        ids = candidate_ids[offsets[query_index]:offsets[query_index + 1]]
        selected = interval_top(query, ids, thq_codes, thresholds)
        require(np.array_equal(selected_ids[query_index], selected),
                f"query {query_index}: persisted THQ top128 differs")
        selected_levels = unpack_thq(np.asarray(thq_codes[selected]))
        base = persisted_centroids[np.arange(D)[None, :], selected_levels]
        selected_documents = np.asarray(documents[selected], dtype=np.float32)
        residual = np.asarray(selected_documents - base, dtype=np.float64)
        candidate_documents = np.asarray(documents[ids], dtype=np.float32)
        candidate_top10 = top_ids(cosine(candidate_documents, query), ids)

        for payload in PAYLOADS:
            row = row_map[(query_index, f"faiss_rq{payload}")]
            require(row.get("side_payload_bytes") == payload
                    and row.get("cascade_total_bytes") == THQ_BYTES + payload,
                    f"query {query_index}: RQ{payload} storage row differs")
            require(row.get("thq4_top128_ids") == selected.astype(int).tolist(),
                    f"query {query_index}: RQ{payload} THQ row differs")
            require(row.get("candidate_fp32_top10_ids") == candidate_top10.astype(int).tolist(),
                    f"query {query_index}: RQ{payload} candidate FP32 row differs")
            decoded = decode_residual(codebooks, persisted_codes[payload][query_index])
            reconstructed = np.asarray(base, dtype=np.float64) + decoded
            reconstructed_scores = cosine(reconstructed, query)
            exact_selected_scores = cosine(selected_documents, query)
            ranked = top_ids(reconstructed_scores, selected)
            require(row.get("top10_ids") == ranked.astype(int).tolist(),
                    f"query {query_index}: RQ{payload} independent decoded top10 differs")
            ndcg_value = ndcg10(ranked, qrel_ids[query_index], qrel_scores[query_index])
            candidate_overlap = float(np.isin(candidate_top10, ranked).sum() / 10.0)
            teacher_overlap = float(np.isin(teacher_ids[query_index], ranked).sum() / 10.0)
            require(abs(float(row["qrels_ndcg10"]) - ndcg_value) < 1e-12,
                    f"query {query_index}: RQ{payload} nDCG differs")
            require(abs(float(row["candidate_fp32_overlap"]) - candidate_overlap) < 1e-12,
                    f"query {query_index}: RQ{payload} candidate overlap differs")
            require(abs(float(row["teacher_overlap"]) - teacher_overlap) < 1e-12,
                    f"query {query_index}: RQ{payload} teacher overlap differs")
            metrics[payload]["ndcg"].append(ndcg_value)
            metrics[payload]["candidate_overlap"].append(candidate_overlap)
            metrics[payload]["teacher_overlap"].append(teacher_overlap)
            metrics[payload]["reconstruction_mse"].append(float(np.mean(
                (decoded - residual) ** 2)))
            metrics[payload]["score_mae"].append(float(np.mean(np.abs(
                reconstructed_scores - exact_selected_scores))))
            top10_by_payload[payload].append(ranked)

    replay_summaries = {}
    for payload in PAYLOADS:
        values = metrics[payload]
        replay = {
            "mean_qrels_ndcg10": float(np.mean(values["ndcg"])),
            "p05_qrels_ndcg10": float(np.percentile(values["ndcg"], 5)),
            "worst_qrels_ndcg10": float(np.min(values["ndcg"])),
            "mean_candidate_fp32_overlap": float(np.mean(values["candidate_overlap"])),
            "mean_teacher_overlap": float(np.mean(values["teacher_overlap"])),
            "mean_reconstruction_mse": float(np.mean(values["reconstruction_mse"])),
            "mean_score_mae": float(np.mean(values["score_mae"])),
            "side_payload_bytes": payload,
            "cascade_total_bytes": THQ_BYTES + payload,
            "global_codebook_bytes": payload * 256 * D * 4,
            "full_1m_logical_total_bytes": (
                1_000_000 * (THQ_BYTES + payload) + payload * 256 * D * 4),
        }
        original = result["summaries"][f"faiss_rq{payload}"]
        for field in ("mean_qrels_ndcg10", "p05_qrels_ndcg10",
                      "worst_qrels_ndcg10", "mean_candidate_fp32_overlap",
                      "mean_teacher_overlap"):
            require(abs(float(original[field]) - replay[field]) < 1e-12,
                    f"RQ{payload} summary {field} differs")
        for field in ("side_payload_bytes", "cascade_total_bytes",
                      "global_codebook_bytes", "full_1m_logical_total_bytes"):
            require(int(original[field]) == replay[field],
                    f"RQ{payload} storage summary {field} differs")
        replay_summaries[f"faiss_rq{payload}"] = replay

    deltas = np.asarray(metrics[48]["ndcg"]) - np.asarray(metrics[32]["ndcg"])
    disagreements = [int(np.sum(~np.isin(top10_by_payload[32][query],
                                         top10_by_payload[48][query])))
                     for query in range(QUERY_COUNT)]
    worst_queries = np.argsort(deltas, kind="stable")[:10]
    bootstrap_rng = np.random.default_rng(20260921)
    bootstrap_indices = bootstrap_rng.integers(
        0, QUERY_COUNT, size=(20_000, QUERY_COUNT))
    bootstrap_means = np.mean(deltas[bootstrap_indices], axis=1)
    rq32_codes = persisted_codes[32]
    rq48_prefix = persisted_codes[48][:, :, :32]
    comparison = {
        "mean_ndcg_delta_rq48_minus_rq32": float(np.mean(deltas)),
        "paired_bootstrap_95pct_mean_ndcg_delta": [
            float(value) for value in np.percentile(bootstrap_means, (2.5, 97.5))
        ],
        "paired_bootstrap_seed": 20260921,
        "paired_bootstrap_samples": 20_000,
        "queries_rq48_better": int(np.sum(deltas > 1e-12)),
        "queries_equal": int(np.sum(np.abs(deltas) <= 1e-12)),
        "queries_rq48_worse": int(np.sum(deltas < -1e-12)),
        "mean_top10_set_disagreement": float(np.mean(disagreements)),
        "rq32_vs_independent_rq48_prefix_symbol_mismatch_fraction": float(
            np.mean(rq32_codes != rq48_prefix)),
        "documents_with_identical_rq32_and_rq48_prefix_fraction": float(
            np.mean(np.all(rq32_codes == rq48_prefix, axis=2))),
        "worst_rq48_minus_rq32": [
            {"query": int(query),
             "rq32_ndcg10": float(metrics[32]["ndcg"][query]),
             "rq48_ndcg10": float(metrics[48]["ndcg"][query]),
             "delta": float(deltas[query])}
            for query in worst_queries
        ],
    }
    audit = {
        "schema_version": 1,
        "family": "thq_faiss_rq_replay_audit_v1",
        "status": "PASS",
        "source_replay": True,
        "faiss_assignment_replay": False,
        "persisted_code_decode_replay": True,
        "result_sha256": sha256(args.result),
        "runner_sha256": sha256(args.runner),
        "artifact_hashes": artifact_hashes,
        "input_hashes": source_hashes,
        "query_count": QUERY_COUNT,
        "row_count": len(rows),
        "faiss_clustering_seed": args.expected_faiss_seed,
        "faiss_threads": args.expected_faiss_threads,
        "summaries": replay_summaries,
        "rq48_vs_rq32": comparison,
        "checks": [
            "result/source/artifact SHA binding",
            "canonical full-training protocol and shared 48-stage fit metadata",
            "candidate receipt, row cardinality, bounds, and uniqueness",
            "independent THQ centroid and top128 replay",
            "persisted code and model shapes",
            "independent additive codebook-sum decode without Faiss",
            "candidate FP32 and reconstructed top10 replay",
            "qrels nDCG and candidate/teacher overlap replay",
            "logical per-document and global-codebook storage accounting",
        ],
        "limitations": [
            "Faiss training and code assignment are hash-bound, not independently reproduced",
            "RQ32 and RQ48 use shared codebooks but independent beam assignments; RQ32 codes are not required to equal the first 32 RQ48 codes",
            "candidate-local side codes are persisted; full 1M storage is logical accounting",
            "reference Python timing is not a native serving latency measurement",
            "held-out domain confirmation remains pending",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print("THQ Faiss RQ replay audit PASS")


if __name__ == "__main__":
    main()
