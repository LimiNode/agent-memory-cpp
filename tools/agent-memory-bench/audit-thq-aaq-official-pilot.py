#!/usr/bin/env python3
"""Independent persisted-decode audit for the bounded official-AAQ pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, QUERY_COUNT = 384, 96, 152


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(codes: np.ndarray) -> np.ndarray:
    packed = np.asarray(codes, dtype=np.uint8)
    levels = np.empty((len(packed), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        value = packed[:, byte]
        levels[:, 4 * byte : 4 * byte + 4] = np.stack((value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1)
    return levels


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    x, q = np.asarray(values, dtype=np.float64), np.asarray(query, dtype=np.float64)
    return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), 1e-30)


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("result", "runner", "artifact", "documents", "queries", "thq4-codes", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    a = p.parse_args()
    result = json.loads(a.result.read_text(encoding="utf-8"))
    if result.get("family") != "thq_official_aaq_pca32_bounded_pilot_v1" or result.get("quality_status") != "BOUNDED_PILOT":
        raise RuntimeError("unexpected AAQ pilot result")
    if result.get("runner_sha256") != sha256(a.runner) or result.get("artifact_sha256") != sha256(a.artifact):
        raise RuntimeError("AAQ runner/artifact binding differs")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    artifact = np.load(a.artifact, allow_pickle=False)
    selected = np.asarray(artifact["selected_ids"], dtype=np.int64)
    unique_ids = np.asarray(artifact["unique_ids"], dtype=np.int64)
    codes = np.asarray(artifact["codes"], dtype=np.uint8)
    codebook = np.asarray(artifact["codebook"], dtype=np.float32)
    centroids = np.asarray(artifact["centroids"], dtype=np.float32)
    mean = np.asarray(artifact["residual_mean"], dtype=np.float32)
    components = np.asarray(artifact["components"], dtype=np.float32)
    scales = np.asarray(artifact["residual_scales"], dtype=np.float32)
    decoded_projected = np.sum(codebook[:, codes + np.arange(codes.shape[1]) * 16], axis=2).T
    residual = (decoded_projected * scales[:, None]) @ components + mean
    base = centroids[np.arange(D)[None, :], unpack(np.asarray(thq[unique_ids]))]
    reconstructed = base + residual.astype(np.float32)
    position = {int(doc): index for index, doc in enumerate(unique_ids)}
    mismatches = 0
    rows = result.get("rows", [])
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        rank = top_ids(cosine(reconstructed[indexes], queries[qi]), ids)
        row = next(row for row in rows if row["query"] == qi and row["arm"] == "official_aaq_pca32_m8k16")
        mismatches += int(rank.tolist() != row["top10_ids"])
    if mismatches:
        raise RuntimeError(f"{mismatches} independent AAQ top10 mismatches")
    audit = {"schema_version": 1, "family": "thq_official_aaq_pca32_bounded_pilot_audit_v1", "status": "PASS", "source_binding": True, "independent_decode_replay": True, "optimizer_replay": False, "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "artifact_sha256": sha256(a.artifact), "documents_sha256": sha256(a.documents), "queries_sha256": sha256(a.queries), "thq4_codes_sha256": sha256(a.thq4_codes), "row_count": len(rows), "top10_mismatch_count": mismatches, "checks": ["runner/artifact/source binding", "independent packed THQ decode", "independent AAQ additive decode", "PCA32 inverse reconstruction", "152-query top10 replay"], "limitations": ["persisted decode audit; official optimizer is source-bound but not independently reimplemented", "bounded PCA32 M8K16 pilot"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
