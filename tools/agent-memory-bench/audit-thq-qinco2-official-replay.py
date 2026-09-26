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

D, THQ_BYTES, QUERY_COUNT, DOCUMENT_COUNT = 384, 96, 152, 1_000_000


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


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    result = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0).astype(np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[levels[:, coordinate] == level, coordinate]
            result[coordinate, level] = float(np.mean(values)) if len(values) else fallback[coordinate]
    return result


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    payload = json.loads(raw.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != QUERY_COUNT:
        raise RuntimeError("candidate raw must contain exactly 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    metadata = json.loads(receipt.read_text(encoding="utf-8"))
    record_bytes = int(metadata.get("flat_file", {}).get("record_bytes", payload.get("record_bytes", 100)))
    if record_bytes not in (100, 148) or flat.stat().st_size != int(offsets[-1]) * record_bytes:
        raise RuntimeError("candidate flat/raw cardinality mismatch")
    if metadata.get("execution_status") != "EXECUTED" or metadata.get("raw_sha256") != sha256(raw) or metadata.get("flat_file", {}).get("sha256") != sha256(flat):
        raise RuntimeError("candidate receipt binding differs")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), record_bytes))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= DOCUMENT_COUNT):
        raise RuntimeError("candidate ID outside corpus")
    return ids, offsets


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def ndcg10(ids: np.ndarray, grades: dict[int, float]) -> float:
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in grades.values()]))[::-1][:10]
    denom = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))))
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / denom)) if denom else 0.0


def validate_codes(codes: np.ndarray, stages: int, document_count: int) -> None:
    if codes.dtype != np.uint8 or codes.shape != (stages, document_count) or np.any(codes >= 256):
        raise RuntimeError("persisted QINCo2 uint8 code shape/cardinality mismatch")


def self_test() -> None:
    codes = np.zeros((4, 16), dtype=np.uint8)
    validate_codes(codes, 4, 16)
    try:
        validate_codes(codes.astype(np.uint16), 4, 16)
        raise RuntimeError("QINCo2 audit malformed dtype was accepted")
    except RuntimeError as exc:
        if "malformed" in str(exc):
            raise
    try:
        validate_codes(np.zeros((4, 15), dtype=np.uint8), 4, 16)
        raise RuntimeError("QINCo2 audit malformed shape was accepted")
    except RuntimeError as exc:
        if "malformed" in str(exc):
            raise
    if unpack(np.zeros((1, THQ_BYTES), dtype=np.uint8)).shape != (1, D):
        raise RuntimeError("QINCo2 THQ unpack self-test failed")
    print("audit-thq-qinco2-official-replay self-test: PASS")


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    p = argparse.ArgumentParser()
    for name in ("qinco-root", "result", "runner", "checkpoint", "codes-artifact", "training-dataset", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    a = p.parse_args()
    result = json.loads(a.result.read_text(encoding="utf-8"))
    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if result.get("family") != "thq_qinco2_official_replay_v2" or result.get("upstream_revision") != revision:
        raise RuntimeError("unexpected QINCo2 result/upstream revision")
    if result.get("runner_sha256") != sha256(a.runner) or result.get("checkpoint_sha256") != sha256(a.checkpoint) or result.get("codes_artifact_sha256") != sha256(a.codes_artifact):
        raise RuntimeError("runner/checkpoint/code artifact binding differs")
    sources = {"training-dataset": a.training_dataset, "documents": a.documents, "train-vectors": a.train_vectors, "queries": a.queries, "qrel-ids": a.qrel_ids, "qrel-scores": a.qrel_scores, "teacher-ids": a.teacher_ids, "thq4-codes": a.thq4_codes, "thq4-thresholds": a.thq4_thresholds, "candidate-flat": a.candidate_flat, "candidate-raw": a.candidate_raw, "candidate-receipt": a.candidate_receipt}
    for name, path in sources.items():
        if result.get("source_hashes", {}).get(name) != sha256(path):
            raise RuntimeError(f"source hash mismatch: {name}")
    if result.get("training", {}).get("dataset_sha256") != sha256(a.training_dataset):
        raise RuntimeError("training dataset provenance mismatch")
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo
    saved = torch.load(a.checkpoint, map_location="cpu", weights_only=False)
    params = saved["parameters"]
    acc = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    cfg = SimpleNamespace(_accelerator=acc, _D=D, _M_ivf=int(params["M"]), K=int(params["K"]), L=int(params["L"]), de=int(params["de"]), dh=int(params["dh"]), A=int(params["A"]), B=int(params["B"]), _ivf_book=None, _qinco_jit=False, ivf_in_use=False, task="eval", _data_mean=np.zeros(D, np.float32), _data_std=1.0, codebook_noise_init=0.0, qinco1_mode=False, enc_max_bs=32768)
    model = QINCo(cfg)
    model.load_state_dict(saved["model"])
    model.eval()
    artifact = np.load(a.codes_artifact, allow_pickle=False)
    selected = np.asarray(artifact["selected_ids"], dtype=np.int64)
    unique_ids = np.asarray(artifact["unique_ids"], dtype=np.int64)
    codes = np.asarray(artifact["codes"])
    final_norms = np.asarray(artifact["final_norms"], dtype=np.float32)
    validate_codes(codes, int(params["M"]), len(unique_ids))
    if len(selected) != QUERY_COUNT:
        raise RuntimeError("persisted QINCo2 selected-id cardinality mismatch")
    if final_norms.shape != (len(unique_ids),) or not np.isfinite(final_norms).all() or np.any(final_norms <= 0):
        raise RuntimeError("persisted QINCo2 final-norm sidecar mismatch")
    candidate_ids, offsets = load_candidates(a.candidate_flat, a.candidate_raw, a.candidate_receipt)
    train_count = a.train_vectors.stat().st_size // (4 * D)
    train = np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(train_count, D))
    thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(D, 3)
    centroids = fit_centroids(np.asarray(train, dtype=np.float32), thresholds)
    documents = np.memmap(a.documents, mode="r", dtype="<f4", shape=(DOCUMENT_COUNT, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(DOCUMENT_COUNT, THQ_BYTES))
    # Recompute the candidate shell and compare its IDs to the persisted shell.
    expected_selected = []
    for qi in range(QUERY_COUNT):
        ids = candidate_ids[offsets[qi] : offsets[qi + 1]]
        levels = unpack(np.asarray(thq[ids]))
        lut = np.empty((D, 4), dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - queries[qi, coordinate] if queries[qi, coordinate] < low else queries[qi, coordinate] - high if queries[qi, coordinate] > high else 0.0
                lut[coordinate, level] = delta * delta
        distance = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
        expected_selected.append(ids[np.lexsort((ids, distance))[: min(128, len(ids))]])
    expected_selected = np.stack(expected_selected).astype(np.int64)
    if not np.array_equal(expected_selected, selected):
        raise RuntimeError("persisted candidate THQ top128 differs from independent replay")
    with torch.inference_mode():
        # Storage is uint8; torch embedding indices must be promoted for decode.
        decoded = model.decode(torch.from_numpy(codes.astype(np.int64, copy=False))).cpu().numpy() * float(model.data_std.item()) + model.data_mean.cpu().numpy()
    base = centroids[np.arange(D)[None, :], unpack(np.asarray(thq[unique_ids]))]
    reconstructed = base + decoded.astype(np.float32)
    recomputed_norms = np.linalg.norm(reconstructed, axis=1).astype(np.float32)
    if not np.allclose(recomputed_norms, final_norms, rtol=0.0, atol=2e-5):
        raise RuntimeError("persisted QINCo2 final norms differ from decoded vectors")
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = {int(row["query"]): row for row in result.get("rows", [])}
    mismatches = 0
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        values = np.asarray(reconstructed[indexes], dtype=np.float64)
        query = np.asarray(queries[qi], dtype=np.float64)
        scores = (values @ query) / np.maximum(np.asarray(final_norms[indexes], dtype=np.float64) * np.linalg.norm(query), 1e-30)
        ranked = top_ids(scores, ids).astype(int).tolist()
        if ranked != rows[qi]["top10_ids"]:
            mismatches += 1
        if int(rows[qi]["side_payload_bytes"]) != int(params["M"]) + 4 or int(rows[qi]["cascade_total_bytes"]) != THQ_BYTES + int(params["M"]) + 4:
            raise RuntimeError("QINCo2 storage contract differs")
        grades = {int(doc): float(score) for doc, score in zip(qrel_ids[qi], qrel_scores[qi]) if int(doc) >= 0 and float(score) > 0}
        value = ndcg10(np.asarray(ranked), grades)
        if abs(value - float(rows[qi]["qrels_ndcg10"])) > 1e-12:
            raise RuntimeError(f"qrels nDCG mismatch for query {qi}")
        teacher_overlap = float(np.isin(teacher_ids[qi], ranked).sum() / 10.0)
        if abs(teacher_overlap - float(rows[qi].get("teacher_overlap", teacher_overlap))) > 1e-12:
            raise RuntimeError(f"teacher overlap mismatch for query {qi}")
    if mismatches:
        raise RuntimeError(f"persisted official QINCo2 decode mismatches: {mismatches}")
    audit = {"schema_version": 2, "family": "thq_qinco2_official_replay_audit_v2", "status": "PASS", "source_replay": True, "source_binding": True, "official_model_decode_replay": True, "independent_decoder": False, "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "checkpoint_sha256": sha256(a.checkpoint), "codes_artifact_sha256": sha256(a.codes_artifact), "source_hashes": {k: sha256(v) for k, v in sources.items()}, "row_count": int(len(result.get("rows", []))), "query_count": QUERY_COUNT, "top10_mismatch_count": mismatches, "persisted_code_dtype": str(codes.dtype), "persisted_code_shape": list(codes.shape), "persisted_norm_sidecar": True, "checks": ["upstream revision and runner/checkpoint/code binding", "all canonical source input SHA binding", "independent candidate-stream and THQ interval² top128 replay", "persisted uint8 code shape/cardinality/range", "official QINCo2 decode", "persisted FP32 norm sidecar parity", "152-query top10 and summary replay", "logical storage contract 16 B code + 4 B norm"], "limitations": ["official decoder replay, not an independent QINCo2 reimplementation", "bounded undertrained 25k control", "historical 152-query fold"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
