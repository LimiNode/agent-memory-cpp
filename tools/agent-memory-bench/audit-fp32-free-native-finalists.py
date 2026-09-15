#!/usr/bin/env python3
"""Fail-closed audit for the THQ3→INT8 finalist payload materialization."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()
def require(value: bool, message: str) -> None:
    if not value: raise RuntimeError(message)

def resolve_artifact(manifest: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else manifest.parent / path

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True); p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True); p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--candidate-receipt", type=Path, required=True); p.add_argument("--candidate-raw", type=Path, required=True)
    p.add_argument("--candidate-flat", type=Path, required=True); p.add_argument("--codec-frontier-receipt", type=Path, required=True)
    p.add_argument("--codec-recompute-chunk", type=int, default=8192); a = p.parse_args()
    raw = json.loads(a.manifest.read_text(encoding="utf-8")); receipt = json.loads(a.receipt.read_text(encoding="utf-8"))
    require(raw["family"] == receipt["family"] == "semantic_fp32_free_native_finalist_materialization_v1", "family differs")
    require(receipt["runner_sha256"] == sha256(a.runner) and receipt["raw_sha256"] == sha256(a.manifest), "provenance differs")
    provenance = raw.get("provenance", {})
    require(provenance.get("thq_manifest_sha256") == sha256(a.thq_manifest), "THQ manifest provenance differs")
    require(provenance.get("candidate_receipt_sha256") == sha256(a.candidate_receipt), "candidate receipt provenance differs")
    require(provenance.get("candidate_raw_sha256") == sha256(a.candidate_raw), "candidate raw provenance differs")
    require(provenance.get("candidate_flat_sha256") == sha256(a.candidate_flat), "candidate flat provenance differs")
    require(provenance.get("corrected_codec_frontier_receipt_sha256") == sha256(a.codec_frontier_receipt), "codec frontier provenance differs")
    require(provenance.get("corrected_codec_frontier_raw_sha256"), "codec frontier raw provenance is missing")
    require(receipt.get("provenance") == provenance, "receipt/raw provenance differs")
    codec_frontier_receipt = json.loads(a.codec_frontier_receipt.read_text(encoding="utf-8"))
    require(codec_frontier_receipt.get("family") == "semantic_fp32_free_codec_frontier_v2" and
            int(codec_frontier_receipt.get("schema_version", 0)) >= 3 and
            codec_frontier_receipt.get("execution_status") == "EXECUTED" and
            codec_frontier_receipt.get("production_activation") is False,
            "codec frontier receipt status differs")
    frontier_raw_meta = codec_frontier_receipt.get("raw_output", {})
    frontier_raw_path = resolve_artifact(a.codec_frontier_receipt, frontier_raw_meta.get("path", ""))
    require(frontier_raw_path.is_file() and sha256(frontier_raw_path) == frontier_raw_meta.get("sha256"),
            "codec frontier raw binding differs")
    require(provenance["corrected_codec_frontier_raw_sha256"] == frontier_raw_meta.get("sha256"),
            "codec frontier raw provenance differs")
    frontier_raw = json.loads(frontier_raw_path.read_text(encoding="utf-8"))
    require(any(int(row.get("levels", 0)) == 4 and row.get("mode") == "interval_sq" and
                int(row.get("shortlist", 0)) == 128 for row in frontier_raw.get("stage_rows", [])),
            "THQ4 interval-squared top128 arm is missing")
    direct_names = {str(row.get("representation")) for row in frontier_raw.get("direct_rows", [])}
    require({"int8_linear", "int8_power0625"}.issubset(direct_names), "matched INT8 finalists are missing")
    candidate_receipt = json.loads(a.candidate_receipt.read_text(encoding="utf-8"))
    require(candidate_receipt.get("raw_sha256") == sha256(a.candidate_raw), "candidate receipt/raw mismatch")
    require(candidate_receipt.get("flat_file", {}).get("sha256") == sha256(a.candidate_flat), "candidate receipt/flat mismatch")
    require(receipt["native_replay_status"] == "PENDING_NATIVE_REPLAY", "native status differs")
    require(receipt.get("selection_status") == "PROVISIONAL_PENDING_NATIVE_FINALIST_SELECTION", "selection status differs")
    for key, path_meta in raw["files"].items():
        path = resolve_artifact(a.manifest, path_meta["path"])
        require(path.is_file() and path.stat().st_size == int(path_meta["bytes"]) and sha256(path) == path_meta["sha256"], f"file differs: {key}")
    candidate_raw = json.loads(a.candidate_raw.read_text(encoding="utf-8")); counts = [int(row["candidate_count"]) for row in candidate_raw["rows"]]
    flat = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(sum(counts), 148)); ids = np.unique(np.frombuffer(np.asarray(flat[:, :4]).tobytes(), dtype="<i4"))
    stored = np.fromfile(resolve_artifact(a.manifest, raw["files"]["document_ids"]["path"]), dtype="<i4")
    require(np.array_equal(stored, ids), "unique document ID sequence differs")
    require(len(stored) == int(raw["unique_candidate_documents"]), "unique document count differs")
    thq_manifest = json.loads(a.thq_manifest.read_text(encoding="utf-8"))
    dimension = int(thq_manifest["dimension"])
    require(dimension == 384 and int(thq_manifest["documents"]) == int(raw["documents"]),
            "source vector shape differs")
    source_meta = thq_manifest["references"]["document_vectors"]
    source_path = resolve_artifact(a.thq_manifest, source_meta["path"])
    require(source_path.is_file(), "source vectors are missing")
    if "bytes" in source_meta:
        require(source_path.stat().st_size == int(source_meta["bytes"]), "source vector size differs")
    if "sha256" in source_meta:
        require(sha256(source_path) == source_meta["sha256"], "source vector SHA differs")
    documents = np.memmap(source_path, mode="r", dtype="<f4",
                          shape=(int(raw["documents"]), dimension))
    training_count = int(raw["training_count"])
    thresholds = np.quantile(np.asarray(documents[:training_count]),
                             (1.0 / 3.0, 2.0 / 3.0), axis=0).T.astype(np.float32)
    thq = np.memmap(resolve_artifact(a.manifest, raw["files"]["thq3_ordinal"]["path"]),
                    mode="r", dtype=np.uint8, shape=(len(stored), 96))
    scales = np.memmap(resolve_artifact(a.manifest, raw["files"]["int8_scales"]["path"]),
                       mode="r", dtype="<f4", shape=(len(stored),))
    int8 = np.memmap(resolve_artifact(a.manifest, raw["files"]["int8_linear"]["path"]),
                     mode="r", dtype=np.int8, shape=(len(stored), dimension))
    require(a.codec_recompute_chunk > 0, "codec recompute chunk differs")
    for start in range(0, len(stored), a.codec_recompute_chunk):
        stop = min(start + a.codec_recompute_chunk, len(stored))
        vectors = np.asarray(documents[stored[start:stop]], dtype=np.float32)
        levels = np.sum(vectors[:, :, None] > thresholds[None, :, :], axis=2,
                        dtype=np.uint8)
        expected_thq = (levels[:, 0::4] | (levels[:, 1::4] << 2) |
                        (levels[:, 2::4] << 4) | (levels[:, 3::4] << 6)).astype(np.uint8)
        expected_scales = np.maximum(np.max(np.abs(vectors), axis=1) / 127.0,
                                     1e-8).astype(np.float32)
        expected_int8 = np.rint(vectors / expected_scales[:, None]).clip(
            -127, 127).astype(np.int8)
        require(np.array_equal(thq[start:stop], expected_thq),
                f"independent THQ recomputation differs at row {start}")
        require(np.array_equal(scales[start:stop], expected_scales),
                f"independent INT8 scale recomputation differs at row {start}")
        require(np.array_equal(int8[start:stop], expected_int8),
                f"independent INT8 code recomputation differs at row {start}")
    require(raw["logical_bytes_per_document"] == {"thq3_ordinal": 96, "int8_linear": 388, "cascade_total": 484}, "byte contract differs")
    require(int(raw.get("physical_subset_bytes_per_document", 0)) == 488, "subset physical byte contract differs")
    require(raw.get("materialization_scope") == "query-derived-evaluation-subset", "materialization scope differs")
    require(int(raw["documents"]) == 1_000_000, "full corpus cardinality differs")
    require(len(stored) < int(raw["documents"]), "subset/full-corpus scope is not explicit")
    print(json.dumps({"family": "semantic_fp32_free_native_finalist_materialization_audit_v1", "status": "PASS", "unique_documents": len(stored), "corpus_documents": int(raw["documents"]), "materialization_scope": "query-derived-evaluation-subset", "native_replay_status": receipt["native_replay_status"]}, sort_keys=True))

if __name__ == "__main__":
    try: main()
    except Exception as error: raise SystemExit(f"audit-fp32-free-native-finalists: {error}")
