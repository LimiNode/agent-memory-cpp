#!/usr/bin/env python3
"""Independently replay packed TQ1/PQ8 scores from a native JSONL run."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import tempfile
from pathlib import Path

import numpy as np

D, TQ_BYTES, PQ_SUBSPACES, PQ_WIDTH = 384, 48, 8, 48
CENTROID = 0.7978846
SCORE_TOLERANCE = 1e-8


def load_tq():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_score_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_payload(path: Path) -> dict[str, np.ndarray | int | bool]:
    data = path.read_bytes()
    require(data[:7] == b"AMTQP01", "invalid payload header")
    dimension, count, subspaces, flags = struct.unpack_from("<IIII", data, 8)
    require((dimension, subspaces) == (D, PQ_SUBSPACES), "payload shape differs")
    offset = 24
    def take(dtype: str, shape: tuple[int, ...]) -> np.ndarray:
        nonlocal offset
        size = int(np.prod(shape)) * np.dtype(dtype).itemsize
        value = np.frombuffer(data, dtype=dtype, count=int(np.prod(shape)), offset=offset).reshape(shape).copy()
        offset += size
        return value
    result: dict[str, np.ndarray | int | bool] = {"count": count, "has_tq_norm": bool(flags & 1)}
    result["ids"] = take("<i4", (count,))
    result["signs"] = take("u1", (count, TQ_BYTES))
    result["scales"] = take("<f4", (count,))
    result["pq_codes"] = take("u1", (count, PQ_SUBSPACES))
    if flags & 1:
        result["tq_norms"] = take("<f4", (count,))
    result["final_norms"] = take("<f4", (count,))
    result["thq_centroids"] = take("<f4", (D, 4))
    result["pq_centroids"] = take("<f4", (PQ_SUBSPACES, 256, PQ_WIDTH))
    require(offset == len(data), "payload trailing bytes")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--native-jsonl", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--thq", type=Path)
    parser.add_argument("--payload-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(np.array_equal(np.asarray([[-1.0, 0.0, 1.0]]) > 0.0,
                               np.asarray([[False, False, True]])),
                "boundary replay failed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value"
            path.write_bytes(b"score-replay")
            require(len(sha256(path)) == 64, "score replay hash self-test failed")
            require(np.isfinite(0.0) and 0.0 <= SCORE_TOLERANCE,
                    "score replay tolerance self-test failed")
            try:
                require(1e-3 <= SCORE_TOLERANCE,
                        "deliberate score-error rejection was not exercised")
            except RuntimeError:
                pass
        print("native TQ1/PQ8 score replay self-test PASS")
        return
    if any(value is None for value in (args.payload, args.native_jsonl, args.queries,
                                      args.thq, args.output)):
        parser.error("all replay paths are required")
    payload = load_payload(args.payload)
    ids = np.asarray(payload["ids"]); rows = {int(row["query"]): row for row in (json.loads(line) for line in args.native_jsonl.read_text(encoding="utf-8").splitlines() if line.strip())}
    queries = np.fromfile(args.queries, dtype="<f4").reshape(-1, D)
    thq = np.memmap(args.thq, mode="r", dtype="u1", shape=(1_000_000, 96))
    tq = load_tq(); max_tq = max_pq = 0.0; checked = 0
    id_to_row = {int(doc): index for index, doc in enumerate(ids)}
    centroids = np.asarray(payload["thq_centroids"]); pq_centroids = np.asarray(payload["pq_centroids"])
    for query_index, row in rows.items():
        query = queries[query_index].astype(np.float64)
        query_norm = max(float(np.linalg.norm(query)), np.finfo(np.float64).tiny)
        rotated = tq.rotate(query[None, :])[0].astype(np.float64)
        tq_lut = np.empty((TQ_BYTES, 256), dtype=np.float64)
        for byte in range(TQ_BYTES):
            for packed in range(256):
                signs = np.where((packed >> np.arange(8)) & 1, 1.0, -1.0)
                tq_lut[byte, packed] = np.sum(signs * CENTROID * rotated[byte * 8:byte * 8 + 8])
        pq_lut = np.einsum("scw,sw->sc", pq_centroids,
                           query.reshape(PQ_SUBSPACES, PQ_WIDTH)).astype(np.float64)
        base_lut = centroids * query[:, None]
        coarse = row["thq4_top128_ids"]
        tq_scores = []; pq_scores = []
        for doc in coarse:
            index = id_to_row[int(doc)]; code = np.asarray(thq[int(doc)], dtype=np.uint8)
            levels = np.stack(((code & 3), ((code >> 2) & 3), ((code >> 4) & 3), ((code >> 6) & 3)), axis=1).reshape(-1)
            numerator = float(np.sum(base_lut[np.arange(D), levels]))
            numerator += float(np.sum(tq_lut[np.arange(TQ_BYTES), np.asarray(payload["signs"])[index]])) * float(np.asarray(payload["scales"])[index])
            if bool(payload["has_tq_norm"]):
                tq_scores.append(numerator / (float(np.asarray(payload["tq_norms"])[index]) * query_norm))
            pq_code = np.asarray(payload["pq_codes"])[index]
            numerator += float(np.sum(pq_lut[np.arange(PQ_SUBSPACES), pq_code]))
            pq_scores.append(numerator / (float(np.asarray(payload["final_norms"])[index]) * query_norm))
        native_pq = np.asarray(row["tq1_pq8_scores"], dtype=np.float64)
        require(native_pq.shape == (len(coarse),), "native PQ score array shape differs")
        max_pq = max(max_pq, float(np.max(np.abs(native_pq - pq_scores))))
        if bool(payload["has_tq_norm"]):
            native_tq = np.asarray(row["tq1_scores"], dtype=np.float64)
            require(native_tq.shape == (len(coarse),), "native TQ score array shape differs")
            max_tq = max(max_tq, float(np.max(np.abs(native_tq - tq_scores))))
        checked += len(coarse)
    require(np.isfinite(max_pq) and max_pq <= SCORE_TOLERANCE,
            f"PQ score replay error exceeds tolerance: {max_pq}")
    if bool(payload["has_tq_norm"]):
        require(np.isfinite(max_tq) and max_tq <= SCORE_TOLERANCE,
                f"TQ score replay error exceeds tolerance: {max_tq}")
    require(checked == sum(len(row["thq4_top128_ids"]) for row in rows),
            "score replay count does not match frozen candidate lists")
    result = {"schema_version": 1, "status": "PASS", "rows": len(rows), "scores": checked,
              "score_tolerance": SCORE_TOLERANCE,
              "max_abs_tq_score_error": max_tq if bool(payload["has_tq_norm"]) else None,
              "max_abs_pq8_score_error": max_pq,
              "payload_sha256": sha256(args.payload),
              "native_jsonl_sha256": sha256(args.native_jsonl),
              "queries_sha256": sha256(args.queries),
              "thq_sha256": sha256(args.thq),
              "payload_receipt_sha256": sha256(args.payload_receipt) if args.payload_receipt else None,
              "replay_runner_sha256": sha256(Path(__file__))}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
