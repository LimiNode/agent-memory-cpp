#!/usr/bin/env python3
"""Independent, fail-closed audit for the additive THQ diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
TOP = 128
PAYLOADS = (4, 6, 8)
VARIANTS = ("additive_greedy", "additive_mse_beam", "additive_fp32_score_oracle")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int = 10) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(value) for doc, value in zip(qids, grades) if int(doc) >= 0 and float(value) > 0}
    values = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in rel.values()], dtype=np.float64))[::-1][:10]
    dcg = float(np.sum(values / np.log2(np.arange(2, 2 + len(values)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    bits = np.unpackbits(np.asarray(codes, dtype=np.uint8), axis=1, bitorder="little")
    return (bits[:, 0::2] + 2 * bits[:, 1::2]).astype(np.uint8)


def candidate_ids(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    metadata = json.loads(raw.read_text(encoding="utf-8"))
    rows = metadata.get("rows")
    require(isinstance(rows, list) and len(rows) == 152, "candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    require(flat.stat().st_size == total * 148, "candidate flat/raw cardinality mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    require(receipt_data.get("execution_status") == "EXECUTED", "candidate receipt is not EXECUTED")
    require(receipt_data.get("raw_sha256") == sha256(raw), "candidate raw SHA mismatch")
    require(receipt_data.get("flat_file", {}).get("sha256") == sha256(flat), "candidate flat SHA mismatch")
    for start, stop in zip(offsets[:-1], offsets[1:]):
        require(len(np.unique(ids[start:stop])) == int(stop - start), "candidate row contains duplicate IDs")
    return ids, offsets


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(codes[ids])
    lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level - 1]
            high = np.inf if level == 3 else thresholds[d, level]
            delta = low - query[d] if query[d] < low else (query[d] - high if query[d] > high else 0.0)
            lut[d, level] = delta * delta
    scores = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, scores))[:min(TOP, len(ids))]]


def centroids_from_train(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.zeros((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else float(np.mean(train[:, coordinate]))
    return centroids


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    qnorm = max(float(np.linalg.norm(query)), np.finfo(np.float32).tiny)
    return np.einsum("kd,d->k", values, query) / np.maximum(np.linalg.norm(values, axis=1) * qnorm,
                                                               np.finfo(np.float32).tiny)


def self_test() -> None:
    require(top_ids(np.array([1.0, 2.0]), np.array([4, 3]), 1).tolist() == [3], "top-id ordering")
    require(np.isclose(ndcg10(np.array([1, 2]), np.array([1, 2]), np.array([2.0, 1.0])), 1.0), "nDCG check")
    print(json.dumps({"status": "PASS", "checks": ["independent top-id ordering", "nDCG calculation"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--codes", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--source", action="append", nargs=2, metavar=("NAME", "PATH"))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.result or not args.models or not args.codes or not args.source:
        parser.error("--result, --models, --codes, and --source NAME PATH are required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_additive_upper_bounds_v2", "wrong result family")
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True,
            "result is not source-replay bound")
    runner = args.runner or Path(__file__).with_name("run-thq-additive-upper-bounds.py")
    require(result.get("runner_sha256") == sha256(runner), "runner SHA mismatch")
    sources = {name: Path(path) for name, path in args.source}
    hashes = result.get("source_hashes", {})
    require(set(sources) == set(hashes), "source manifest differs")
    for name, path in sources.items():
        require(path.is_file(), f"missing source: {name}")
        require(sha256(path) == hashes[name], f"source SHA mismatch: {name}")
    artifact_hashes = result.get("artifact_hashes", {})
    require(artifact_hashes.get("models") == sha256(args.models), "model artifact SHA mismatch")
    require(artifact_hashes.get("codes") == sha256(args.codes), "code artifact SHA mismatch")
    require(result.get("stages_by_payload_bytes") == {"4": 4, "6": 6, "8": 8}, "stage/rate manifest differs")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == 152 * len(PAYLOADS) * len(VARIANTS), "row cardinality differs")
    row_map = {}
    for row in rows:
        key = (int(row["query"]), int(row["payload_bytes"]), str(row["variant"]))
        require(key not in row_map, f"duplicate row: {key}")
        require(0 <= key[0] < 152 and key[1] in PAYLOADS and key[2] in VARIANTS, f"invalid row key: {key}")
        require(len(row.get("top10_ids", [])) == 10 and len(row.get("selected_ids", [])) == TOP, f"ID cardinality differs: {key}")
        row_map[key] = row
    required = {"documents", "train_vectors", "queries", "qrel_ids", "qrel_scores", "teacher_ids",
                "thq4_codes", "thq4_thresholds", "candidate_flat", "candidate_raw", "candidate_receipt"}
    require(set(sources) == required, "full source replay manifest is required")
    documents = np.memmap(sources["documents"], mode="r", dtype="<f4", shape=(1_000_000, D))
    train_rows = sources["train_vectors"].stat().st_size // (D * 4)
    train = np.asarray(np.memmap(sources["train_vectors"], mode="r", dtype="<f4", shape=(train_rows, D)), dtype=np.float32)
    queries = np.asarray(np.memmap(sources["queries"], mode="r", dtype="<f4", shape=(152, D)), dtype=np.float32)
    qrel_ids = np.asarray(np.memmap(sources["qrel_ids"], mode="r", dtype="<i8", shape=(152, 20)))
    qrel_scores = np.asarray(np.memmap(sources["qrel_scores"], mode="r", dtype="<f4", shape=(152, 20)))
    teacher_ids = np.asarray(np.memmap(sources["teacher_ids"], mode="r", dtype="<i8", shape=(152, 10)))
    thq_codes = np.memmap(sources["thq4_codes"], mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(sources["thq4_thresholds"], dtype="<f4").reshape(D, 3)
    ids, offsets = candidate_ids(sources["candidate_flat"], sources["candidate_raw"], sources["candidate_receipt"])
    centroids = centroids_from_train(train, thresholds)
    models = np.load(args.models, allow_pickle=False)
    codes = np.load(args.codes, allow_pickle=False)
    for qi, query in enumerate(queries):
        candidate_slice = ids[offsets[qi]:offsets[qi + 1]]
        selected = interval_top(query, candidate_slice, thq_codes, thresholds)
        for payload in PAYLOADS:
            selected_saved = codes[f"payload_{payload}_selected_ids"][qi].astype(np.int64)
            require(np.array_equal(selected, selected_saved), f"THQ top128 mismatch: q={qi}, payload={payload}")
            base = centroids[np.arange(D)[None, :], unpack_thq(thq_codes[selected])]
            docs = np.asarray(documents[selected], dtype=np.float32)
            codebooks = [models[f"payload_{payload}_stage_{i}"] for i in range(payload)]
            greedy_codes = codes[f"payload_{payload}_greedy_codes"][qi]
            greedy = base + sum((codebooks[i][greedy_codes[:, i]] for i in range(payload)), start=np.zeros_like(base))
            beam_codes = codes[f"payload_{payload}_beam_codes"][qi]
            beam = base[:, None, :] + sum((codebooks[i][beam_codes[:, :, i]] for i in range(payload)), start=np.zeros_like(base[:, None, :]))
            exact = cosine(docs, query)
            beam_scores = np.asarray([cosine(beam[k], query) for k in range(len(selected))])
            mse_idx = np.argmin(np.sum((docs[:, None, :] - beam) ** 2, axis=2), axis=1)
            oracle_idx = np.argmin(np.abs(beam_scores - exact[:, None]), axis=1)
            values_by_variant = {"additive_greedy": greedy,
                                 "additive_mse_beam": beam[np.arange(len(selected)), mse_idx] + 0.0,
                                 "additive_fp32_score_oracle": beam[np.arange(len(selected)), oracle_idx] + 0.0}
            for variant, values in values_by_variant.items():
                ranked = top_ids(cosine(values, query), selected)
                row = row_map[(qi, payload, variant)]
                require(np.array_equal(selected, np.asarray(row["selected_ids"], dtype=np.int64)), f"row selected-ID mismatch: {(qi, payload, variant)}")
                require(np.array_equal(ranked, np.asarray(row["top10_ids"], dtype=np.int64)), f"top10 replay mismatch: {(qi, payload, variant)}")
                require(np.isclose(ndcg10(ranked, qrel_ids[qi], qrel_scores[qi]), float(row["qrels_ndcg10"]), atol=1e-6), f"nDCG replay mismatch: {(qi, payload, variant)}")
                overlap = float(np.isin(teacher_ids[qi], ranked).sum() / 10.0)
                require(np.isclose(overlap, float(row["teacher_overlap"]), atol=1e-6), f"teacher replay mismatch: {(qi, payload, variant)}")
    print(json.dumps({"status": "PASS", "checks": ["source SHA replay", "artifact SHA replay", "independent THQ top128", "independent additive decode", "top10 replay", "nDCG replay", "teacher-overlap replay"]}, indent=2))


if __name__ == "__main__":
    main()
