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

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True); p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True); p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--candidate-receipt", type=Path, required=True); p.add_argument("--candidate-raw", type=Path, required=True)
    p.add_argument("--candidate-flat", type=Path, required=True); a = p.parse_args()
    raw = json.loads(a.manifest.read_text(encoding="utf-8")); receipt = json.loads(a.receipt.read_text(encoding="utf-8"))
    require(raw["family"] == receipt["family"] == "semantic_fp32_free_native_finalist_materialization_v1", "family differs")
    require(receipt["runner_sha256"] == sha256(a.runner) and receipt["raw_sha256"] == sha256(a.manifest), "provenance differs")
    require(receipt["native_replay_status"] == "PENDING_NATIVE_REPLAY", "native status differs")
    for key, path_meta in raw["files"].items():
        path = Path(path_meta["path"]); require(path.is_file() and path.stat().st_size == int(path_meta["bytes"]) and sha256(path) == path_meta["sha256"], f"file differs: {key}")
    candidate_raw = json.loads(a.candidate_raw.read_text(encoding="utf-8")); counts = [int(row["candidate_count"]) for row in candidate_raw["rows"]]
    flat = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(sum(counts), 148)); ids = np.unique(np.frombuffer(np.asarray(flat[:, :4]).tobytes(), dtype="<i4"))
    stored = np.fromfile(Path(raw["files"]["document_ids"]["path"]), dtype="<i4")
    require(np.array_equal(stored, ids), "unique document ID sequence differs")
    require(len(stored) == int(raw["unique_candidate_documents"]), "unique document count differs")
    require(raw["logical_bytes_per_document"] == {"thq3_ordinal": 96, "int8_linear": 388, "cascade_total": 484}, "byte contract differs")
    print(json.dumps({"family": "semantic_fp32_free_native_finalist_materialization_audit_v1", "status": "PASS", "unique_documents": len(stored), "native_replay_status": receipt["native_replay_status"]}, sort_keys=True))

if __name__ == "__main__":
    try: main()
    except Exception as error: raise SystemExit(f"audit-fp32-free-native-finalists: {error}")
