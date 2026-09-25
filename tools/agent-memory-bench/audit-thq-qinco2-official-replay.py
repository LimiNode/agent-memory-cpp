#!/usr/bin/env python3
"""Independent persisted-code decode audit for official QINCo2 replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

D, THQ_BYTES, QUERY_COUNT = 384, 96, 152


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        v = np.asarray(codes)[:, b]
        out[:, 4 * b : 4 * b + 4] = np.stack((v & 3, (v >> 2) & 3, (v >> 4) & 3, (v >> 6) & 3), axis=1)
    return out


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("qinco-root", "result", "runner", "checkpoint", "codes-artifact", "documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "lsq-models", "lsq-codes", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    a = p.parse_args()
    result = json.loads(a.result.read_text(encoding="utf-8"))
    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if result.get("family") != "thq_qinco2_official_replay_v1" or result.get("upstream_revision") != revision:
        raise RuntimeError("unexpected QINCo2 result/upstream revision")
    if result.get("runner_sha256") != sha256(a.runner) or result.get("checkpoint_sha256") != sha256(a.checkpoint) or result.get("codes_artifact_sha256") != sha256(a.codes_artifact):
        raise RuntimeError("runner/checkpoint/code artifact binding differs")
    sources = {"documents": a.documents, "queries": a.queries, "qrel-ids": a.qrel_ids, "qrel-scores": a.qrel_scores, "teacher-ids": a.teacher_ids, "thq4-codes": a.thq4_codes,
               "lsq-models": a.lsq_models, "lsq-codes": a.lsq_codes}
    for name, path in sources.items():
        if result.get("source_hashes", {}).get(name) != sha256(path):
            raise RuntimeError(f"source hash mismatch: {name}")
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo
    saved = torch.load(a.checkpoint, map_location="cpu", weights_only=False)
    params = saved["parameters"]
    acc = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    cfg = SimpleNamespace(_accelerator=acc, _D=D, _M_ivf=int(params["M"]), K=int(params["K"]), L=int(params["L"]),
        de=int(params["de"]), dh=int(params["dh"]), A=int(params["A"]), B=int(params["B"]), _ivf_book=None,
        _qinco_jit=False, ivf_in_use=False, task="eval", _data_mean=np.zeros(D, np.float32), _data_std=1.0,
        codebook_noise_init=0.0, qinco1_mode=False, enc_max_bs=32768)
    model = QINCo(cfg); model.load_state_dict(saved["model"]); model.eval()
    artifact = np.load(a.codes_artifact, allow_pickle=False)
    selected = np.asarray(artifact["selected_ids"], dtype=np.int64)
    unique_ids = np.asarray(artifact["unique_ids"], dtype=np.int64)
    codes = np.asarray(artifact["codes"], dtype=np.int64)
    if codes.shape != (int(params["M"]), len(unique_ids)) or len(selected) != QUERY_COUNT:
        raise RuntimeError("persisted QINCo2 code shape/cardinality mismatch")
    centroids = np.asarray(np.load(a.lsq_models, allow_pickle=False)["centroids"], dtype=np.float32)
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    with torch.inference_mode():
        decoded = model.decode(torch.from_numpy(codes)).cpu().numpy() * float(model.data_std.item()) + model.data_mean.cpu().numpy()
    base = centroids[np.arange(D)[None, :], unpack(np.asarray(thq[unique_ids]))]
    reconstructed = base + decoded.astype(np.float32)
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = {int(row["query"]): row for row in result.get("rows", [])}
    mismatches = 0
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        values = np.asarray(reconstructed[indexes], dtype=np.float64)
        query = np.asarray(queries[qi], dtype=np.float64)
        scores = (values @ query) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(query), 1e-30)
        ranked = top_ids(scores, ids).astype(int).tolist()
        if ranked != rows[qi]["top10_ids"]:
            mismatches += 1
        grades = {int(doc): float(score) for doc, score in zip(qrel_ids[qi], qrel_scores[qi]) if int(doc) >= 0 and float(score) > 0}
        gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ranked])
        ideal = np.sort(np.asarray([2.0 ** grade - 1.0 for grade in grades.values()]))[::-1][:10]
        denom = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))))
        value = float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / denom)) if denom else 0.0
        if abs(value - float(rows[qi]["qrels_ndcg10"])) > 1e-12:
            raise RuntimeError(f"qrels nDCG mismatch for query {qi}")
        teacher_overlap = float(np.isin(teacher_ids[qi], ranked).sum() / 10.0)
        if abs(teacher_overlap - float(rows[qi].get("teacher_overlap", teacher_overlap))) > 1e-12:
            raise RuntimeError(f"teacher overlap mismatch for query {qi}")
    if mismatches:
        raise RuntimeError(f"persisted official QINCo2 decode mismatches: {mismatches}")
    audit = {"schema_version": 1, "family": "thq_qinco2_official_replay_audit_v1", "status": "PASS",
        "source_binding": True, "official_model_decode_replay": True, "independent_decoder": False,
        "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "checkpoint_sha256": sha256(a.checkpoint),
        "codes_artifact_sha256": sha256(a.codes_artifact), "source_hashes": {k: sha256(v) for k, v in sources.items()},
        "row_count": int(len(result.get("rows", []))), "query_count": QUERY_COUNT, "top10_mismatch_count": mismatches,
        "checks": ["upstream revision and runner/checkpoint/code binding", "all source input SHA binding", "persisted code shape/cardinality", "official QINCo2 decode", "152-query top10 replay"],
        "limitations": ["official decoder replay, not an independent QINCo2 reimplementation", "bounded undertrained 25k control", "historical 152-query fold"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
