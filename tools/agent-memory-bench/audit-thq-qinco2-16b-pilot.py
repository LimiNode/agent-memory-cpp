#!/usr/bin/env python3
"""Source-bound model/code replay for the bounded QINCo2 16-byte pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

D, THQ_BYTES, QUERY_COUNT, STAGES, K = 384, 96, 152, 16, 256


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


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("qinco-root", "result", "runner", "model-artifact", "codes-artifact", "queries", "thq4-codes", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    a = p.parse_args()
    result = json.loads(a.result.read_text(encoding="utf-8"))
    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if (result.get("family") != "thq_qinco2_16b_bounded_pilot_v1" or
            result.get("upstream_revision") != revision or
            result.get("threshold_layout") != "D,3"):
        raise RuntimeError("unexpected QINCo2 pilot/source revision")
    if result.get("runner_sha256") != sha256(a.runner) or result.get("model_artifact_sha256") != sha256(a.model_artifact) or result.get("codes_artifact_sha256") != sha256(a.codes_artifact):
        raise RuntimeError("QINCo2 runner/artifact binding differs")
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo
    saved = torch.load(a.model_artifact, map_location="cpu", weights_only=True)
    accelerator = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    cfg = SimpleNamespace(_accelerator=accelerator, _D=D, _M_ivf=STAGES, K=K, L=1, de=32, dh=64, A=8, B=4, _ivf_book=None, _qinco_jit=False, ivf_in_use=False, task="eval", _data_mean=saved["data_mean"], _data_std=saved["data_std"], codebook_noise_init=0.0, qinco1_mode=False, enc_max_bs=32768)
    model = QINCo(cfg)
    model.load_state_dict(saved["state_dict"])
    model.eval()
    codes_artifact = np.load(a.codes_artifact, allow_pickle=False)
    selected = np.asarray(codes_artifact["selected_ids"], dtype=np.int64)
    unique_ids = np.asarray(codes_artifact["unique_ids"], dtype=np.int64)
    codes = np.asarray(codes_artifact["codes"], dtype=np.uint8)
    centroids = np.asarray(codes_artifact["centroids"], dtype=np.float32)
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    with torch.inference_mode():
        decoded = model.decode(torch.from_numpy(codes.T.astype(np.int64))).numpy() * float(model.data_std) + np.asarray(model.data_mean)
    base = centroids[np.arange(D)[None, :], unpack(np.asarray(thq[unique_ids]))]
    reconstructed = base + decoded.astype(np.float32)
    position = {int(doc): index for index, doc in enumerate(unique_ids)}
    mismatches = 0
    rows = result.get("rows", [])
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        values = np.asarray(reconstructed[indexes], dtype=np.float64)
        query = np.asarray(queries[qi], dtype=np.float64)
        scores = (values @ query) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(query), 1e-30)
        rank = top_ids(scores, ids)
        row = next(row for row in rows if row["query"] == qi and row["arm"] == "qinco2_16b_bounded")
        mismatches += int(rank.tolist() != row["top10_ids"])
    if mismatches:
        raise RuntimeError(f"{mismatches} QINCo2 model/code replay mismatches")
    audit = {"schema_version": 1, "family": "thq_qinco2_16b_bounded_pilot_audit_v1", "status": "PASS", "source_binding": True, "official_model_decode_replay": True, "independent_decoder": False, "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "model_artifact_sha256": sha256(a.model_artifact), "codes_artifact_sha256": sha256(a.codes_artifact), "queries_sha256": sha256(a.queries), "thq4_codes_sha256": sha256(a.thq4_codes), "row_count": len(rows), "top10_mismatch_count": mismatches, "checks": ["upstream revision binding", "runner/model/code artifact binding", "official QINCo2 model reload", "persisted 16-byte code decode", "152-query top10 replay"], "limitations": ["source-bound official decoder replay, not an independent QINCo2 reimplementation", "bounded undertrained CPU pilot"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
