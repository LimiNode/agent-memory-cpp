#!/usr/bin/env python3
"""Independent persisted-model checks for the official Faiss OPQ control."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    args = p.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if result.get("family") != "thq_faiss_official_opq_control_v1" or result.get("status") != "EXECUTED":
        raise RuntimeError("unexpected OPQ result family/status")
    if result.get("query_count") != 152 or result.get("metric") != "cosine":
        raise RuntimeError("OPQ protocol differs")
    config = result.get("opq_config", {})
    if config.get("niter") != 50 or config.get("niter_pq") != 40 or config.get("niter_pq_0") != 40:
        raise RuntimeError("official Faiss OPQ configuration is not pinned")
    rows = result.get("rows", [])
    if len(rows) != 152 or {int(row["query"]) for row in rows} != set(range(152)):
        raise RuntimeError("OPQ row cardinality differs")
    model = np.load(args.model)
    rotation = np.asarray(model["rotation"], dtype=np.float32)
    centers = np.asarray(model["centers"], dtype=np.float32)
    if rotation.shape != (384, 384) or centers.shape != (32, 16, 12):
        raise RuntimeError("OPQ model shape differs")
    if not np.isfinite(rotation).all() or not np.isfinite(centers).all():
        raise RuntimeError("OPQ model contains non-finite values")
    if abs(float(np.max(np.abs(rotation.T @ rotation - np.eye(384, dtype=np.float32))))) > 5e-4:
        raise RuntimeError("OPQ rotation is not orthogonal")
    if result.get("model_sha256") != sha(args.model):
        raise RuntimeError("OPQ model hash differs")
    print("THQ official Faiss OPQ audit PASS")

if __name__ == "__main__":
    main()
