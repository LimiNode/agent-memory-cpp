#!/usr/bin/env python3
"""Independent, fail-closed audit for the source-bound Gate C replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
ARMS = ("rabitq_like", "bbq_like")
CORRECTIONS = ("code_only", "scale")
SOURCE_NAMES = (
    "documents", "train_vectors", "queries", "qrel_ids", "qrel_scores",
    "teacher_ids", "thq4_codes", "thq4_thresholds", "candidate_flat",
    "candidate_raw", "candidate_receipt",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    values = np.asarray(codes, dtype=np.uint8)
    output = np.empty((len(values), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        value = values[:, byte]
        output[:, 4 * byte:4 * byte + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3,
             (value >> 6) & 3), axis=1)
    return output


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray,
                 thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for dimension in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[dimension, level - 1]
            high = np.inf if level == 3 else thresholds[dimension, level]
            delta = (low - query[dimension] if query[dimension] < low else
                     query[dimension] - high if query[dimension] > high else 0.0)
            lut[dimension, level] = delta * delta
    distance = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, distance))[:min(TOP, len(ids))]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    vectors = np.asarray(values, dtype=np.float64)
    vector_query = np.asarray(query, dtype=np.float64)
    denominator = np.maximum(
        np.linalg.norm(vectors, axis=1) * np.linalg.norm(vector_query),
        np.finfo(np.float64).tiny,
    )
    return (vectors @ vector_query) / denominator


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def ndcg10(ids: np.ndarray, qrel_ids: np.ndarray,
           qrel_scores: np.ndarray) -> float:
    relevance = {
        int(document): float(score)
        for document, score in zip(qrel_ids, qrel_scores)
        if int(document) >= 0 and float(score) > 0
    }
    gains = np.asarray([2.0 ** relevance.get(int(document), 0.0) - 1.0
                        for document in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** score - 1.0
                                for score in relevance.values()]))[::-1][:10]
    discounts = np.log2(np.arange(2, 2 + len(gains)))
    denominator = (np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))
                   if len(ideal) else 0.0)
    return float(np.sum(gains / discounts) / denominator) if denominator else 0.0


def load_candidates(flat: Path, raw_path: Path, receipt_path: Path):
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    rows = raw.get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT,
            "candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows],
                        dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    require(flat.stat().st_size == int(offsets[-1]) * 148,
            "candidate flat/raw cardinality mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8,
                        shape=(int(offsets[-1]), 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    require(np.all((ids >= 0) & (ids < 1_000_000)),
            "candidate ID outside corpus")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("execution_status") == "EXECUTED",
            "candidate receipt is not EXECUTED")
    require(receipt.get("raw_sha256") == sha256(raw_path) and
            receipt.get("flat_file", {}).get("sha256") == sha256(flat),
            "candidate receipt binding differs")
    return ids, offsets


def decode_signs(models: np.lib.npyio.NpzFile, codes: np.lib.npyio.NpzFile,
                 arm: str, correction: str, positions: np.ndarray) -> np.ndarray:
    rotation = np.asarray(models["rotation"], dtype=np.float64)
    if arm == "rabitq_like":
        packed = np.asarray(codes["rabitq_codes"])[positions]
        signs = np.unpackbits(packed, axis=1, bitorder="little")[:, :D]
        scales = np.asarray(codes["rabitq_scales"], dtype=np.float64)[positions, None]
    else:
        packed = np.asarray(codes["bbq_codes"])[positions]
        signs = np.unpackbits(packed, axis=2, bitorder="little")[:, :, :48]
        signs = signs.reshape(len(positions), D)
        scales = np.repeat(
            np.asarray(codes["bbq_scales"], dtype=np.float64)[positions], 48,
            axis=1,
        )
    decoded = signs.astype(np.float64) * 2.0 - 1.0
    if correction == "scale":
        decoded *= scales
    return decoded @ rotation.T


def self_test() -> None:
    packed = np.packbits(np.asarray([[True, False, True, False, True, False,
                                       True, False]], dtype=bool), bitorder="little")
    require(int(packed[0]) == 0x55, "bit-order self-test failed")
    print("THQ residual binary Gate C audit self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    names = ("result", "runner", "models", "codes", *SOURCE_NAMES, "output")
    for name in names:
        parser.add_argument(f"--{name.replace('_', '-')}",
                            dest=name, type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    inputs = [getattr(args, name) for name in names if name != "output"]
    require(all(path is not None and path.is_file() for path in inputs),
            "all audit inputs must be files")
    require(args.output is not None, "--output is required")

    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_residual_binary_gate_c_v2",
            "Gate C family differs")
    require(result.get("status") == "EXECUTED" and
            result.get("source_replay") is True,
            "result is not source-replay evidence")
    require(result.get("runner_sha256") == sha256(args.runner),
            "runner SHA binding differs")
    require(result.get("query_count") == QUERY_COUNT and
            result.get("metric") == "cosine", "Gate C protocol differs")
    source_hashes = {name: sha256(getattr(args, name)) for name in SOURCE_NAMES}
    require(result.get("source_hashes") == source_hashes,
            "source SHA binding differs")
    require(result.get("artifact_hashes") == {
        "models": sha256(args.models), "codes": sha256(args.codes)},
            "artifact SHA binding differs")
    rows = result.get("rows")
    require(isinstance(rows, list) and
            len(rows) == QUERY_COUNT * len(ARMS) * len(CORRECTIONS),
            "Gate C row matrix differs")
    require(tuple(result.get("arms", ())) == ARMS and
            tuple(result.get("corrections", ())) == CORRECTIONS,
            "Gate C taxonomy differs")

    documents = np.memmap(args.documents, mode="r", dtype="<f4",
                          shape=(1_000_000, D))
    train_count = args.train_vectors.stat().st_size // (D * 4)
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    queries = np.memmap(args.queries, mode="r", dtype="<f4",
                        shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8",
                         shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4",
                            shape=(QUERY_COUNT, 20))
    teacher = np.memmap(args.teacher_ids, mode="r", dtype="<i8",
                        shape=(QUERY_COUNT, 10))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                    shape=(1_000_000, THQ_BYTES))
    candidate_ids, offsets = load_candidates(args.candidate_flat,
                                              args.candidate_raw,
                                              args.candidate_receipt)

    # Reconstruct the same centroid table from the training source, without
    # trusting a persisted centroid array for this part of the audit.
    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2,
                          dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0).astype(np.float32)
    for dimension in range(D):
        for level in range(4):
            values = train[train_levels[:, dimension] == level, dimension]
            centroids[dimension, level] = (float(np.mean(values))
                                           if len(values) else fallback[dimension])

    with np.load(args.models, allow_pickle=False) as models, \
            np.load(args.codes, allow_pickle=False) as codes:
        model_centroids = np.asarray(models["centroids"], dtype=np.float64)
        rotation = np.asarray(models["rotation"], dtype=np.float64)
        unique_ids = np.asarray(models["unique_ids"], dtype=np.int64)
        selected_saved = np.asarray(codes["selected_ids"], dtype=np.int64)
        require(model_centroids.shape == (D, 4) and
                np.allclose(model_centroids, centroids, rtol=0.0, atol=1e-6),
                "persisted centroid table differs from source replay")
        require(rotation.shape == (D, D) and np.isfinite(rotation).all(),
                "rotation shape or finiteness differs")
        require(unique_ids.ndim == 1 and np.array_equal(unique_ids,
                                                         np.unique(unique_ids)),
                "unique document manifest is not canonical")
        require(selected_saved.shape == (QUERY_COUNT, TOP),
                "persisted THQ selection shape differs")
        require(np.asarray(codes["rabitq_codes"]).shape[0] == len(unique_ids) and
                np.asarray(codes["bbq_codes"]).shape[0] == len(unique_ids),
                "persisted code cardinality differs")
        id_to_pos = {int(document): position
                     for position, document in enumerate(unique_ids)}
        row_map = {(int(row["query"]), row["arm"], row["correction"]): row
                   for row in rows}
        metrics = {f"{arm}:{correction}": []
                   for arm in ARMS for correction in CORRECTIONS}
        for query_index, query in enumerate(queries):
            ids = candidate_ids[offsets[query_index]:offsets[query_index + 1]]
            selected = interval_top(query, ids, thq, thresholds)
            require(np.array_equal(selected_saved[query_index], selected),
                    f"query {query_index}: THQ selection differs")
            positions = np.asarray([id_to_pos[int(document)]
                                    for document in selected], dtype=np.int64)
            levels = unpack_thq(np.asarray(thq[selected]))
            base = centroids[np.arange(D)[None, :], levels]
            for arm in ARMS:
                for correction in CORRECTIONS:
                    decoded = decode_signs(models, codes, arm, correction,
                                           positions)
                    ranked = top_ids(cosine(base + decoded, query), selected)
                    row = row_map.get((query_index, arm, correction))
                    require(row is not None, "missing Gate C row")
                    require(row["top10_ids"] == ranked.astype(int).tolist() and
                            row["thq4_top128_ids"] == selected.astype(int).tolist(),
                            f"query {query_index}: {arm}/{correction} IDs differ")
                    value = ndcg10(ranked, qrel_ids[query_index],
                                   qrel_scores[query_index])
                    require(abs(float(row["qrels_ndcg10"]) - value) < 1e-12,
                            f"query {query_index}: nDCG differs")
                    overlap = float(np.isin(teacher[query_index], ranked).sum() / 10)
                    require(abs(float(row["teacher_overlap"]) - overlap) < 1e-12,
                            f"query {query_index}: teacher overlap differs")
                    expected_payload = 50 if arm == "rabitq_like" else 64
                    require(row["side_payload_bytes"] == expected_payload and
                            row["cascade_total_bytes"] == THQ_BYTES + expected_payload,
                            f"query {query_index}: payload accounting differs")
                    metrics[f"{arm}:{correction}"].append(value)

    summaries = {
        key: {
            "mean_qrels_ndcg10": float(np.mean(values)),
            "p05_qrels_ndcg10": float(np.percentile(values, 5)),
            "worst_qrels_ndcg10": float(np.min(values)),
        }
        for key, values in metrics.items()
    }
    audit = {
        "schema_version": 2,
        "family": "thq_residual_binary_gate_c_audit_v2",
        "status": "PASS",
        "source_binding": True,
        "independent_decode_replay": True,
        "result_sha256": sha256(args.result),
        "runner_sha256": sha256(args.runner),
        "input_hashes": source_hashes,
        "artifact_hashes": {"models": sha256(args.models),
                            "codes": sha256(args.codes)},
        "query_count": QUERY_COUNT,
        "row_count": len(rows),
        "summaries": summaries,
        "checks": [
            "source/result/artifact SHA binding",
            "candidate receipt and flat/raw cardinality binding",
            "source-derived centroid reconstruction",
            "independent canonical THQ interval-squared top128 replay",
            "independent sign/FP16-scale/rotation decode",
            "cosine top10, nDCG, teacher-overlap, and payload replay",
        ],
        "limitations": [
            "local RaBitQ/BBQ-like residual references; not faithful TurboQuant/NEQ",
            "cosine lane intentionally excludes norm-explicit IP correction",
            "candidate-local replay; held-out confirmation remains pending",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print("THQ residual binary Gate C audit PASS")


if __name__ == "__main__":
    main()
