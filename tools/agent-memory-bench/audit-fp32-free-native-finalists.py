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
    if path.is_absolute() or path.is_file():
        return path
    relative = manifest.parent / path
    return relative if relative.is_file() else manifest.parent / path.name

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True); p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True); p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--candidate-receipt", type=Path, required=True); p.add_argument("--candidate-raw", type=Path, required=True)
    p.add_argument("--candidate-flat", type=Path, required=True); p.add_argument("--codec-frontier-receipt", type=Path, required=True); a = p.parse_args()
    raw = json.loads(a.manifest.read_text(encoding="utf-8")); receipt = json.loads(a.receipt.read_text(encoding="utf-8"))
    require(raw["family"] == receipt["family"] == "semantic_fp32_free_native_finalist_materialization_v1", "family differs")
    require(receipt["runner_sha256"] == sha256(a.runner) and receipt["raw_sha256"] == sha256(a.manifest), "provenance differs")
    provenance = raw.get("provenance", {})
    require(provenance.get("thq_manifest_sha256") == sha256(a.thq_manifest), "THQ manifest provenance differs")
    require(provenance.get("candidate_receipt_sha256") == sha256(a.candidate_receipt), "candidate receipt provenance differs")
    require(provenance.get("candidate_raw_sha256") == sha256(a.candidate_raw), "candidate raw provenance differs")
    require(provenance.get("candidate_flat_sha256") == sha256(a.candidate_flat), "candidate flat provenance differs")
    require(provenance.get("corrected_codec_frontier_receipt_sha256") == sha256(a.codec_frontier_receipt), "codec frontier provenance differs")
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
    require(raw["logical_bytes_per_document"] == {"thq3_ordinal": 96, "int8_linear": 388, "cascade_total": 484}, "byte contract differs")
    require(int(raw.get("physical_subset_bytes_per_document", 0)) == 488, "subset physical byte contract differs")
    require(raw.get("materialization_scope") == "query-derived-evaluation-subset", "materialization scope differs")
    require(int(raw["documents"]) == 1_000_000, "full corpus cardinality differs")
    require(len(stored) < int(raw["documents"]), "subset/full-corpus scope is not explicit")
    print(json.dumps({"family": "semantic_fp32_free_native_finalist_materialization_audit_v1", "status": "PASS", "unique_documents": len(stored), "corpus_documents": int(raw["documents"]), "materialization_scope": "query-derived-evaluation-subset", "native_replay_status": receipt["native_replay_status"]}, sort_keys=True))

if __name__ == "__main__":
    try: main()
    except Exception as error: raise SystemExit(f"audit-fp32-free-native-finalists: {error}")
