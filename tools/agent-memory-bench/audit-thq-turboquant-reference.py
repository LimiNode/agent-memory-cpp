#!/usr/bin/env python3
"""Fail-closed source-binding audit for the TurboQuant reference result."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ARMS = {"turboquant1": 52, "turboquant2": 100}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def self_test() -> None:
    require(sum(ARMS.values()) == 152, "TurboQuant payload self-test failed")
    print("TurboQuant reference audit self-test: PASS")


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:min(count, len(ids))]]


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result", type=Path); parser.add_argument("--runner", type=Path); parser.add_argument("--artifact", type=Path); parser.add_argument("--queries", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if any(value is None for value in (args.result, args.runner, args.artifact, args.queries, args.output)):
        parser.error("--result, --runner, --artifact, --queries, and --output are required")
    result = json.loads(args.result.read_text(encoding="utf-8")); rows = result.get("rows", []); require(result.get("status") == "EXECUTED" and result.get("source_replay") is True, "result is not executed source replay"); require(result.get("metric") == "cosine" and result.get("codec_metric", "").startswith("dot-compatible") and result.get("upstream_revision") == "6ab21cac18ebb6f4ae29102c7f8f5cc11affd5de", "TurboQuant source pin or metric contract differs"); require(len(rows) == 304, "row cardinality differs"); require(result.get("artifact_sha256") == sha256(args.artifact), "persisted TurboQuant artifact SHA differs"); require(result.get("source_hashes", {}).get("queries") == sha256(args.queries), "query source SHA differs")
    with np.load(args.artifact, allow_pickle=False) as payload:
        document_ids = np.asarray(payload["document_ids"], dtype=np.int64); base = np.asarray(payload["base"], dtype=np.float32); decoded = {1: np.asarray(payload["decoded1"], dtype=np.float32), 2: np.asarray(payload["decoded2"], dtype=np.float32)}; row_ids = np.asarray(payload["row_ids"], dtype=np.int64); row_offsets = np.asarray(payload["row_offsets"], dtype=np.int64)
    require(all(value.shape == base.shape and value.shape[1:] == (384,) for value in decoded.values()), "persisted TurboQuant payload shapes differ"); require(row_offsets.shape == (153,) and row_offsets[-1] == len(row_ids), "persisted TurboQuant row offsets differ"); positions = {int(doc): i for i, doc in enumerate(document_ids)}; queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(152, 384)), dtype=np.float32); mismatches = {arm: 0 for arm in ARMS}
    for row in rows:
        qi = int(row["query"]); arm = str(row["arm"]); bits = 1 if arm == "turboquant1" else 2; ids = np.asarray(row["thq4_top128_ids"], dtype=np.int64); persisted_ids = row_ids[row_offsets[qi]:row_offsets[qi + 1]]; require(np.array_equal(ids, persisted_ids), f"persisted TurboQuant row IDs differ at query {qi}"); indexes = np.asarray([positions[int(doc)] for doc in ids], dtype=np.int64); reconstructed = base[indexes] + decoded[bits][indexes]; q = queries[qi]; scores = (reconstructed @ q) / np.maximum(np.linalg.norm(reconstructed, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny); ranked = top_ids(scores, ids, 10); mismatches[arm] += int(not np.array_equal(ranked, np.asarray(row["top10_ids"], dtype=np.int64)))
    require(all(value == 0 for value in mismatches.values()), f"independent TurboQuant top10 mismatches: {mismatches}")
    for arm, payload in ARMS.items():
        arm_rows = [row for row in rows if row.get("arm") == arm]; require(len(arm_rows) == 152, f"{arm} row cardinality differs"); require(all(int(row.get("side_payload_bytes")) == payload and int(row.get("cascade_total_bytes")) == 96 + payload for row in arm_rows), f"{arm} payload accounting differs")
        summary = result.get("summaries", {}).get(arm, {}); values = [float(row["qrels_ndcg10"]) for row in arm_rows]; require(abs(float(summary["mean_qrels_ndcg10"]) - sum(values) / len(values)) < 1e-12, f"{arm} mean summary differs")
    audit = {"schema_version": 2, "family": "thq_turboquant_reference_audit_v1", "status": "PASS", "source_binding": True, "independent_decode_replay": True, "result_sha256": sha256(args.result), "runner_sha256": sha256(args.runner), "artifact_sha256": sha256(args.artifact), "query_sha256": sha256(args.queries), "upstream_revision": result["upstream_revision"], "row_count": len(rows), "arms": sorted(ARMS), "independent_decode_top10_mismatch_count": mismatches, "checks": ["result, runner, artifact, and query SHA binding", "Qdrant revision pin", "304 row cardinality", "per-arm payload accounting", "independent persisted vector decode and top10 parity", "summary replay"], "limitations": ["TurboQuant normal mode only; TQ+ and QJL are not covered", "candidate-local cosine replay; no native serving claim"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print("TurboQuant reference audit PASS")


if __name__ == "__main__": main()
