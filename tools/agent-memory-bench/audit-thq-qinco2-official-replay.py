#!/usr/bin/env python3
"""Independent persisted-code decode audit for official QINCo2 replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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


def validate_norms(norms: np.ndarray, document_count: int) -> None:
    if norms.dtype != np.float32 or norms.shape != (document_count,) or not np.isfinite(norms).all() or np.any(norms <= 0):
        raise RuntimeError("persisted QINCo2 final-norm sidecar mismatch")


def validate_summary(summary: dict, values: list[float]) -> None:
    expected = {
        "mean_qrels_ndcg10": float(np.mean(values)),
        "p05_qrels_ndcg10": float(np.percentile(values, 5)),
        "worst_qrels_ndcg10": float(np.min(values)),
    }
    for key, value in expected.items():
        if abs(float(summary.get(key, -1.0)) - value) > 1e-12:
            raise RuntimeError(f"QINCo2 summary mismatch: {key}")


def self_test() -> None:
    codes = np.zeros((4, 16), dtype=np.uint8)
    validate_codes(codes, 4, 16)
    validate_norms(np.ones(16, dtype=np.float32), 16)
    try:
        validate_norms(np.zeros(16, dtype=np.float32), 16)
        raise RuntimeError("QINCo2 audit malformed norm was accepted")
    except RuntimeError as exc:
        if "malformed" in str(exc):
            raise
    values = [0.0, 0.5, 1.0]
    validate_summary({"mean_qrels_ndcg10": 0.5, "p05_qrels_ndcg10": 0.05, "worst_qrels_ndcg10": 0.0}, values)
    try:
        validate_summary({"mean_qrels_ndcg10": 0.0, "p05_qrels_ndcg10": 0.05, "worst_qrels_ndcg10": 0.0}, values)
        raise RuntimeError("QINCo2 audit altered summary was accepted")
    except RuntimeError as exc:
        if "altered" in str(exc):
            raise
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
    if result.get("family") != "thq_qinco2_official_replay_v3" or result.get("upstream_revision") != revision:
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
    residual_codes = np.asarray(artifact["codes"])
    residual_norms = np.asarray(artifact["final_norms"], dtype=np.float32)
    raw_codes = np.asarray(artifact["raw_codes"])
    raw_norms = np.asarray(artifact["raw_final_norms"], dtype=np.float32)
    validate_codes(residual_codes, int(params["M"]), len(unique_ids))
    validate_codes(raw_codes, int(params["M"]), len(unique_ids))
    if selected.shape != (QUERY_COUNT, 128):
        raise RuntimeError("persisted QINCo2 selected-id cardinality mismatch")
    validate_norms(residual_norms, len(unique_ids))
    validate_norms(raw_norms, len(unique_ids))
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
        decoded_residual = model.decode(torch.from_numpy(residual_codes.astype(np.int64, copy=False))).cpu().numpy() * float(model.data_std.item()) + model.data_mean.cpu().numpy()
        decoded_raw = model.decode(torch.from_numpy(raw_codes.astype(np.int64, copy=False))).cpu().numpy() * float(model.data_std.item()) + model.data_mean.cpu().numpy()
    base = centroids[np.arange(D)[None, :], unpack(np.asarray(thq[unique_ids]))]
    reconstructed = base + decoded_residual.astype(np.float32)
    raw_reconstructed = decoded_raw.astype(np.float32)
    recomputed_norms = np.linalg.norm(reconstructed, axis=1).astype(np.float32)
    recomputed_raw_norms = np.linalg.norm(raw_reconstructed, axis=1).astype(np.float32)
    if not np.allclose(recomputed_norms, residual_norms, rtol=0.0, atol=2e-5) or not np.allclose(recomputed_raw_norms, raw_norms, rtol=0.0, atol=2e-5):
        raise RuntimeError("persisted QINCo2 final norms differ from decoded vectors")
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = {(str(row["arm"]), int(row["query"])): row for row in result.get("rows", [])}
    arm_vectors = {
        "train_thq_residual__eval_thq_residual": (reconstructed, residual_norms),
        "train_thq_residual__eval_raw": (raw_reconstructed, raw_norms),
    }
    for name in result.get("arms", {}):
        if re.fullmatch(r"qinco2_official_\d+b_residual_mismatch", name):
            arm_vectors[name] = (reconstructed, residual_norms)
        elif re.fullmatch(r"qinco2_official_\d+b_raw_vector", name):
            arm_vectors[name] = (raw_reconstructed, raw_norms)
    arms = {name: arm_vectors[name] for name in result.get("arms", {}) if name in arm_vectors}
    if len(arms) != 2:
        raise RuntimeError("QINCo2 result must declare exactly two recognized domain arms")
    expected_keys = {(arm, qi) for arm in arms for qi in range(QUERY_COUNT)}
    if len(result.get("rows", [])) != len(expected_keys) or set(rows) != expected_keys:
        raise RuntimeError("QINCo2 result rows must contain exactly one row per arm and query")
    candidate_fp32 = {}
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        query = np.asarray(queries[qi], dtype=np.float64)
        exact = np.asarray(documents[ids], dtype=np.float64)
        candidate_fp32[qi] = top_ids((exact @ query) / np.maximum(np.linalg.norm(exact, axis=1) * np.linalg.norm(query), 1e-30), ids).astype(int).tolist()
    mismatches = 0
    for arm, (vectors, norms) in arms.items():
        quality_values = []
        for qi in range(QUERY_COUNT):
            ids = selected[qi]
            indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
            query = np.asarray(queries[qi], dtype=np.float64)
            values = np.asarray(vectors[indexes], dtype=np.float64)
            scores = (values @ query) / np.maximum(np.asarray(norms[indexes], dtype=np.float64) * np.linalg.norm(query), 1e-30)
            ranked = top_ids(scores, ids).astype(int).tolist()
            row = rows[(arm, qi)]
            if "training_domain" in row:
                expected_training = "thq_residual" if arm.startswith("train_thq_residual__") else "raw"
                expected_evaluation = "thq_residual" if arm.endswith("eval_thq_residual") else "raw"
                if row.get("training_domain") != expected_training or row.get("evaluation_domain") != expected_evaluation:
                    raise RuntimeError(f"domain semantics mismatch for {arm} query {qi}")
            if ranked != row["top10_ids"]:
                mismatches += 1
            if row["candidate_fp32_top10_ids"] != candidate_fp32[qi]:
                raise RuntimeError(f"candidate FP32 top10 mismatch for {arm} query {qi}")
            if abs(float(row["candidate_fp32_overlap"]) - float(np.isin(candidate_fp32[qi], ranked).sum() / 10.0)) > 1e-12:
                raise RuntimeError(f"candidate FP32 overlap mismatch for {arm} query {qi}")
            if int(row["side_payload_bytes"]) != int(params["M"]) + 4 or int(row["cascade_total_bytes"]) != THQ_BYTES + int(params["M"]) + 4:
                raise RuntimeError("QINCo2 storage contract differs")
            grades = {int(doc): float(score) for doc, score in zip(qrel_ids[qi], qrel_scores[qi]) if int(doc) >= 0 and float(score) > 0}
            value = ndcg10(np.asarray(ranked), grades)
            quality_values.append(value)
            if abs(value - float(row["qrels_ndcg10"])) > 1e-12:
                raise RuntimeError(f"qrels nDCG mismatch for {arm} query {qi}")
            teacher_overlap = float(np.isin(teacher_ids[qi], ranked).sum() / 10.0)
            if abs(teacher_overlap - float(row.get("teacher_overlap", teacher_overlap))) > 1e-12:
                raise RuntimeError(f"teacher overlap mismatch for {arm} query {qi}")
        summary = result.get("summaries", {}).get(arm, {})
        validate_summary(summary, quality_values)
    expected_config = {k: int(params[k]) for k in ("M", "K", "L", "de", "dh", "A", "B")}
    if result.get("config") != expected_config:
        raise RuntimeError("result configuration differs from checkpoint parameters")
    contract = result.get("storage_contract", {})
    if (contract.get("code_dtype") != "uint8" or int(contract.get("code_bytes", -1)) != int(params["M"])
            or int(contract.get("final_norm_bytes", -1)) != 4 or int(contract.get("cascade_total_bytes", -1)) != THQ_BYTES + int(params["M"]) + 4):
        raise RuntimeError("top-level QINCo2 storage contract differs")
    if mismatches:
        raise RuntimeError(f"persisted official QINCo2 decode mismatches: {mismatches}")
    audit = {"schema_version": 3, "family": "thq_qinco2_official_replay_audit_v3", "status": "PASS", "source_replay": True, "source_binding": True, "official_model_decode_replay": True, "independent_decoder": False, "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "checkpoint_sha256": sha256(a.checkpoint), "codes_artifact_sha256": sha256(a.codes_artifact), "source_hashes": {k: sha256(v) for k, v in sources.items()}, "row_count": int(len(result.get("rows", []))), "query_count": QUERY_COUNT, "top10_mismatch_count": mismatches, "persisted_code_dtype": str(residual_codes.dtype), "persisted_code_shape": list(residual_codes.shape), "persisted_norm_sidecar": True, "checks": ["upstream revision and runner/checkpoint/code binding", "all canonical source input SHA binding", "independent candidate-stream and THQ interval² top128 replay", "persisted uint8 code shape/cardinality/range for both diagnostic arms", "official QINCo2 decode for raw and THQ-residual arms", "persisted FP32 norm sidecar parity", "candidate FP32 top10 and overlap replay", "152-query per-arm top10 and summary replay", "logical storage contract M-byte code + 4 B norm", "checkpoint configuration equality"], "limitations": ["official decoder replay, not an independent QINCo2 reimplementation", "bounded undertrained 25k control", "historical 152-query fold"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
