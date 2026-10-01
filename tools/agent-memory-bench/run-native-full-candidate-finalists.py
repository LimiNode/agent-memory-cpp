#!/usr/bin/env python3
"""Run the full candidate-stream native serving gate for dense finalists.

This is deliberately separate from the top-128 preparation runner.  It keeps
the complete frozen R4 candidate stream, lets the native scorer perform the
THQ byte-LUT top-128 selection, and then reranks those rows from persisted
decoded payloads.  Compressed-code decode is still outside the timing scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
QUERY_COUNT = 152
TOP = 128


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    value = np.asarray(codes, dtype=np.uint8)
    result = np.empty((len(value), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        lane = value[:, byte]
        result[:, 4 * byte : 4 * byte + 4] = np.stack(
            ((lane & 3), (lane >> 2) & 3, (lane >> 4) & 3,
             (lane >> 6) & 3), axis=1)
    return result


def candidate_stream(flat: Path, raw_path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]],
                        dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    record_bytes = int(raw.get("record_bytes", 0))
    if record_bytes not in (100, 148):
        # Older fused receipts kept the width in the receipt rather than the
        # raw note.  The row count still gives a fail-closed inference.
        require(flat.stat().st_size % int(offsets[-1]) == 0,
                "candidate flat/raw cardinality differs")
        record_bytes = flat.stat().st_size // int(offsets[-1])
    require(record_bytes in (100, 148), "unsupported candidate record width")
    require(flat.stat().st_size == int(offsets[-1]) * record_bytes,
            "candidate flat/raw cardinality differs")
    payload = flat.read_bytes()
    ids = np.ndarray(shape=(int(offsets[-1]),), dtype="<i4", buffer=payload,
                     strides=(record_bytes,)).astype(np.int32, copy=True)
    require(len(counts) == QUERY_COUNT, "candidate query count differs")
    require(np.all((ids >= 0) & (ids < 1_000_000)), "candidate ID out of range")
    return ids, offsets.astype(np.uint64)


def decode_joint2(thq: np.ndarray, ids: np.ndarray, models: Path,
                  artifact_dir: Path) -> np.ndarray:
    persisted = np.fromfile(artifact_dir / "candidate.ids.i4", dtype="<i4")
    require(np.array_equal(persisted, ids), "joint2 IDs are not the full union")
    with np.load(models, allow_pickle=False) as model:
        centroids = np.asarray(model["centroids"], dtype=np.float32)
    levels = unpack_thq(np.asarray(thq[ids]))
    base = centroids[np.arange(D)[None, :], levels]
    packed_width = (128 * 2 + 7) // 8
    packed = np.memmap(artifact_dir / "thq-joint2.candidate.packed",
                       mode="r", dtype=np.uint8,
                       shape=(len(ids), packed_width))
    symbols = np.empty((len(ids), 128), dtype=np.uint8)
    for index in range(128):
        bit = index * 2
        byte = bit // 8
        shift = bit % 8
        symbols[:, index] = (packed[:, byte] >> shift) & 3
    codebook = np.memmap(artifact_dir / "thq-joint2.codebook.f32",
                         mode="r", dtype="<f4", shape=(128, 64, 4, 3))
    patterns = levels.reshape(len(ids), 128, 3)
    pattern_ids = (patterns[:, :, 0] + 4 * patterns[:, :, 1] +
                   16 * patterns[:, :, 2]).astype(np.int64)
    decoded = np.empty((len(ids), D), dtype=np.float32)
    for block in range(128):
        decoded[:, 3 * block : 3 * block + 3] = codebook[
            block, pattern_ids[:, block], symbols[:, block]]
    return base + decoded


def thq_top(query: np.ndarray, ids: np.ndarray, thq: np.ndarray,
            thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(thq[ids])
    lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            delta = (low - query[coordinate] if query[coordinate] < low else
                     query[coordinate] - high if query[coordinate] > high else 0.0)
            lut[coordinate, level] = delta * delta
    scores = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, scores))[:TOP]]


def cosine_top(query: np.ndarray, ids: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    scores = (vectors @ query) / np.maximum(
        np.linalg.norm(vectors, axis=1) * np.linalg.norm(query), 1e-30)
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--lsq-models", type=Path, required=True)
    parser.add_argument("--rslm-dir", type=Path, required=True)
    parser.add_argument("--joint-artifact-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8,
                    shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3)
    ids, offsets = candidate_stream(args.candidate_flat, args.candidate_raw)
    unique = np.unique(ids).astype(np.int32)
    offsets_path = args.output_root / "candidate-offsets.u64"
    offsets.astype("<u8").tofile(offsets_path)
    query_values = np.memmap(args.queries, mode="r", dtype="<f4",
                             shape=(QUERY_COUNT, D))
    rows = []
    # Process one dense payload at a time.  The full union is 463,258 rows;
    # retaining all three 711-MiB matrices simultaneously turns this gate into
    # an avoidable host-memory/OOM experiment.
    codec_specs = (
        ("joint2", 32, lambda: decode_joint2(
            thq, unique, args.lsq_models, args.joint_artifact_dir)),
        # Faithful RSLM relative payloads include 2 B inner UE7M9 and 2 B
        # outer reconstruction scale: 148 B for RSLM3, 196 B for RSLM4.
        ("rslm3", 148, lambda: np.memmap(
            args.rslm_dir / "rslm3.faithful.f32", mode="r", dtype="<f4",
            shape=(len(unique), D))),
        ("rslm4", 196, lambda: np.memmap(
            args.rslm_dir / "rslm4.faithful.f32", mode="r", dtype="<f4",
            shape=(len(unique), D))),
    )
    for name, payload_bytes, load_vectors in codec_specs:
        vectors = load_vectors()
        require(vectors.shape == (len(unique), D) and np.isfinite(vectors).all(),
                f"invalid {name} dense payload")
        ids_path = args.output_root / f"{name}.document-ids.i4"
        payload_path = args.output_root / f"{name}.decoded.f32"
        unique.astype("<i4").tofile(ids_path)
        vectors.astype("<f4", copy=False).tofile(payload_path)
        command = [str(args.native_executable), "--dense-candidate-gate",
                   str(args.thq), str(args.thresholds), str(ids_path),
                   str(payload_path), str(args.candidate_flat),
                   str(offsets_path), str(args.queries), str(QUERY_COUNT),
                   str(payload_bytes)]
        completed = subprocess.run(command, check=True, capture_output=True,
                                   text=True)
        native_rows = [json.loads(line) for line in completed.stdout.splitlines()
                       if line]
        require(len(native_rows) == QUERY_COUNT,
                f"{name} native row count differs")
        output_path = args.output_root / f"{name}.native.jsonl"
        output_path.write_text(completed.stdout, encoding="utf-8")
        native_by_query = {int(row["query"]): row for row in native_rows}
        parity = True
        parity_mismatches = []
        thq_set_parity = True
        thq_ordered_parity = True
        thq_mismatches = []
        for query_index in range(QUERY_COUNT):
            query = np.asarray(query_values[query_index], dtype=np.float32)
            selected = thq_top(query, ids[offsets[query_index]:offsets[query_index + 1]],
                               thq, thresholds)
            native_thq = np.asarray(native_by_query[query_index]["thq4_top128_ids"],
                                    dtype=np.int32)
            if not np.array_equal(np.sort(native_thq), np.sort(selected)):
                thq_set_parity = False
                thq_mismatches.append(query_index)
            if not np.array_equal(native_thq, selected):
                thq_ordered_parity = False
            positions = np.searchsorted(unique, selected)
            expected = cosine_top(query, selected, vectors[positions])
            actual = np.asarray(native_by_query[query_index]["top10_ids"],
                                dtype=np.int32)
            if not np.array_equal(actual, expected):
                parity = False
                parity_mismatches.append(query_index)
        timing = {}
        for field in ("thq4_prefilter", "codec_rerank", "total"):
            values = [float(row["timing_ms"][field]) for row in native_rows]
            timing[field] = {f"p{p}": float(np.percentile(values, p))
                             for p in (50, 95, 99)}
        rows.append({"codec": name, "payload_bytes": payload_bytes,
                     "timing_percentiles_ms": timing,
                     "exact_top10_parity": parity,
                     "parity_mismatch_queries": parity_mismatches,
                     "thq_top128_set_parity": thq_set_parity,
                     "thq_top128_ordered_parity": thq_ordered_parity,
                     "thq_top128_ordering_policy": "set parity mandatory; ordering-only numerical/tie-order differences are reported, not treated as semantic mismatches",
                     "thq_top128_set_mismatch_queries": thq_mismatches,
                     "native_summary": json.loads(
                         completed.stderr.strip().splitlines()[-1]),
                     "output_sha256": sha256(output_path),
                     "decoded_payload_sha256": sha256(payload_path)})
        del vectors
    result = {
        "schema_version": 1,
        "family": "thq_native_full_candidate_predecoded_v1",
        "status": "EXECUTED",
        "metric": "cosine",
        "score_precision": "float64 scalar accumulation",
        "query_count": QUERY_COUNT,
        "candidate_scope": "complete frozen R4 candidate stream -> native THQ4 top128",
        "unique_candidate_documents": int(len(unique)),
        "decode_scope": "persisted dense payload rerank; compressed decode excluded",
        "timing_scope": "warm process per-query wall-clock; cold/page-fault latency excluded",
        "source_hashes": {k: sha256(v) for k, v in {
            "thq": args.thq, "thresholds": args.thresholds,
            "candidate_flat": args.candidate_flat,
            "candidate_raw": args.candidate_raw, "queries": args.queries}.items()},
        "codec_source_hashes": {
            "lsq_models": sha256(args.lsq_models),
            "joint_candidate_ids": sha256(args.joint_artifact_dir / "candidate.ids.i4"),
            "joint2_packed": sha256(args.joint_artifact_dir / "thq-joint2.candidate.packed"),
            "joint2_codebook": sha256(args.joint_artifact_dir / "thq-joint2.codebook.f32"),
            "rslm3": sha256(args.rslm_dir / "rslm3.faithful.f32"),
            "rslm4": sha256(args.rslm_dir / "rslm4.faithful.f32")},
        "rows": rows,
        "limitations": [
            "native benchmark starts from the frozen R4 candidate stream",
            "compressed decode is not included in native timing",
            "LSQ, TurboQuant and BBQ are separate gates and are not silently substituted"],
    }
    result_path = args.output_root / "native-full-candidate.result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"result": str(result_path), "unique_candidate_documents": len(unique)},
                     sort_keys=True))


if __name__ == "__main__":
    main()
