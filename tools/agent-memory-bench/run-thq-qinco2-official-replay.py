#!/usr/bin/env python3
"""Source-bound replay for an official QINCo2 checkpoint on the THQ shell.

The candidate shell is reconstructed from the canonical candidate stream and
the canonical train vectors.  No LSQ artifact is used as a source of truth.
Codes are persisted as one uint8 index per QINCo stage and the FP32 norm used
by the serving scorer is persisted alongside them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT, DOCUMENT_COUNT = 384, 96, 128, 152, 1_000_000


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
        levels[:, 4 * byte : 4 * byte + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1
        )
    return levels


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    result = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0).astype(np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[levels[:, coordinate] == level, coordinate]
            result[coordinate, level] = float(np.mean(values)) if len(values) else fallback[coordinate]
    return result


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = unpack(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            delta = low - query[coordinate] if query[coordinate] < low else query[coordinate] - high if query[coordinate] > high else 0.0
            lut[coordinate, level] = delta * delta
    distances = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, distances))[: min(TOP, len(ids))]]


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
    denom = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) )
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / denom)) if denom else 0.0


def self_test() -> None:
    thresholds = np.zeros((D, 3), dtype=np.float32)
    train = np.zeros((8, D), dtype=np.float32)
    train[4:] = 1.0
    centroids = fit_centroids(train, thresholds)
    if centroids.shape != (D, 4) or not np.isfinite(centroids).all():
        raise RuntimeError("QINCo2 THQ centroid self-test failed")
    codes = np.zeros((2, 16), dtype=np.uint8)
    norms = np.linalg.norm(np.ones((2, D), dtype=np.float32), axis=1).astype(np.float32)
    if codes.dtype != np.uint8 or codes.shape != (2, 16) or np.any(codes >= 256) or not np.isfinite(norms).all():
        raise RuntimeError("QINCo2 uint8/norm self-test failed")
    print("THQ QINCo2 official replay self-test: PASS")


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    p = argparse.ArgumentParser()
    for name in ("qinco-root", "checkpoint", "training-dataset", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "codes-output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--batch-size", type=int, default=64)
    a = p.parse_args()
    if a.batch_size < 1:
        p.error("batch size must be positive")
    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo

    saved = torch.load(a.checkpoint, map_location="cpu", weights_only=False)
    params = saved.get("parameters", {})
    required = {"M", "K", "L", "de", "dh", "A", "B"}
    if not required.issubset(params) or int(saved.get("data_dim", D)) != D:
        raise RuntimeError("checkpoint does not contain the expected QINCo2 configuration")
    if int(params["K"]) != 256 or int(params["M"]) != 16:
        raise RuntimeError("this production-shaped contract requires M=16,K=256")
    acc = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    cfg = SimpleNamespace(_accelerator=acc, _D=D, _M_ivf=int(params["M"]), K=int(params["K"]), L=int(params["L"]), de=int(params["de"]), dh=int(params["dh"]), A=int(params["A"]), B=int(params["B"]), _ivf_book=None, _qinco_jit=False, ivf_in_use=False, task="eval", _data_mean=np.zeros(D, np.float32), _data_std=1.0, codebook_noise_init=0.0, qinco1_mode=False, enc_max_bs=max(32768, int(a.batch_size) * int(params["A"]) * int(params["B"])))
    model = QINCo(cfg)
    model.load_state_dict(saved["model"])
    model.eval()

    documents = np.memmap(a.documents, mode="r", dtype="<f4", shape=(DOCUMENT_COUNT, D))
    train_count = a.train_vectors.stat().st_size // (4 * D)
    train = np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(train_count, D))
    thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(D, 3)
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(DOCUMENT_COUNT, THQ_BYTES))
    candidate_ids, offsets = load_candidates(a.candidate_flat, a.candidate_raw, a.candidate_receipt)
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    centroids = fit_centroids(np.asarray(train, dtype=np.float32), thresholds)
    selected = np.stack([interval_top(queries[qi], candidate_ids[offsets[qi]:offsets[qi + 1]], thq, thresholds) for qi in range(QUERY_COUNT)]).astype(np.int64)
    unique_ids = np.unique(selected)
    levels = unpack(np.asarray(thq[unique_ids]))
    base = centroids[np.arange(D)[None, :], levels]
    residual = np.asarray(documents[unique_ids], dtype=np.float32) - base
    codes_parts, decode_parts = [], []
    raw_codes_parts, raw_decode_parts = [], []
    with torch.inference_mode():
        for start in range(0, len(unique_ids), a.batch_size):
            batch = torch.from_numpy(np.ascontiguousarray(residual[start : start + a.batch_size]))
            raw_batch = torch.from_numpy(np.ascontiguousarray(np.asarray(documents[unique_ids[start : start + a.batch_size]], dtype=np.float32)))
            for source_batch, code_sink, decode_sink in ((batch, codes_parts, decode_parts), (raw_batch, raw_codes_parts, raw_decode_parts)):
                codes, decoded = model.encode((source_batch - model.data_mean) / model.data_std)
                stage_codes = codes.cpu().numpy()
                if stage_codes.shape[0] == int(params["M"]):
                    stage_codes = stage_codes.T
                if stage_codes.dtype.kind not in "iu" or np.any(stage_codes < 0) or np.any(stage_codes >= 256):
                    raise RuntimeError("QINCo returned code outside uint8 range")
                code_sink.append(stage_codes.T.astype(np.uint8, copy=False))
                decode_sink.append((decoded * model.data_std + model.data_mean).cpu().numpy().astype(np.float32))
    codes = np.concatenate(codes_parts, axis=1).astype(np.uint8, copy=False)
    decoded = np.concatenate(decode_parts, axis=0)
    raw_codes = np.concatenate(raw_codes_parts, axis=1).astype(np.uint8, copy=False)
    raw_decoded = np.concatenate(raw_decode_parts, axis=0)
    reconstructed = base + decoded
    raw_reconstructed = raw_decoded
    final_norms = np.linalg.norm(reconstructed, axis=1).astype(np.float32)
    raw_final_norms = np.linalg.norm(raw_reconstructed, axis=1).astype(np.float32)
    if (codes.shape != (int(params["M"]), len(unique_ids)) or raw_codes.shape != codes.shape
            or not np.isfinite(final_norms).all() or not np.isfinite(raw_final_norms).all()):
        raise RuntimeError("persisted QINCo shape/norm contract failed")
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = []
    summaries = {}
    arm_data = {
        "qinco2_official_16b_residual_mismatch": (reconstructed, final_norms),
        "qinco2_official_16b_raw_vector": (raw_reconstructed, raw_final_norms),
    }
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        query = np.asarray(queries[qi], dtype=np.float64)
        exact = np.asarray(documents[ids], dtype=np.float64)
        exact_scores = (exact @ query) / np.maximum(np.linalg.norm(exact, axis=1) * np.linalg.norm(query), 1e-30)
        exact_top = top_ids(exact_scores, ids)
        grades = {int(doc): float(score) for doc, score in zip(qrel_ids[qi], qrel_scores[qi]) if int(doc) >= 0 and float(score) > 0}
        for arm, (vectors, norms) in arm_data.items():
            values = np.asarray(vectors[indexes], dtype=np.float64)
            scores = (values @ query) / np.maximum(np.asarray(norms[indexes], dtype=np.float64) * np.linalg.norm(query), 1e-30)
            ranked = top_ids(scores, ids)
            rows.append({"query": qi, "arm": arm, "top10_ids": ranked.astype(int).tolist(), "candidate_fp32_top10_ids": exact_top.astype(int).tolist(), "candidate_fp32_overlap": float(np.isin(exact_top, ranked).sum() / 10.0), "qrels_ndcg10": ndcg10(ranked, grades), "teacher_overlap": float(np.isin(teacher_ids[qi], ranked).sum() / 10.0), "side_payload_bytes": int(params["M"]) + 4, "final_norm_sidecar_bytes": 4, "cascade_total_bytes": THQ_BYTES + int(params["M"]) + 4})
    a.codes_output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.codes_output, selected_ids=selected, unique_ids=unique_ids, codes=codes, final_norms=final_norms, raw_codes=raw_codes, raw_final_norms=raw_final_norms)
    model_bytes = int(sum(value.numel() * value.element_size() for value in saved["model"].values() if hasattr(value, "numel")))
    for arm in arm_data:
        arm_rows = [r for r in rows if r["arm"] == arm]
        summaries[arm] = {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in arm_rows])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in arm_rows], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in arm_rows])), "side_payload_bytes": int(params["M"]) + 4, "final_norm_sidecar_bytes": 4, "cascade_total_bytes": THQ_BYTES + int(params["M"] ) + 4, "global_model_bytes": model_bytes, "checkpoint_bytes": int(a.checkpoint.stat().st_size)}
    sources = {"training-dataset": a.training_dataset, "documents": a.documents, "train-vectors": a.train_vectors, "queries": a.queries, "qrel-ids": a.qrel_ids, "qrel-scores": a.qrel_scores, "teacher-ids": a.teacher_ids, "thq4-codes": a.thq4_codes, "thq4-thresholds": a.thq4_thresholds, "candidate-flat": a.candidate_flat, "candidate-raw": a.candidate_raw, "candidate-receipt": a.candidate_receipt}
    training = {"checkpoint_epoch": int(saved.get("epoch", -1)), "requested_epochs": int(saved.get("scheduler", {}).get("_max_epochs", -1)), "completed_full_epochs": int(saved.get("epoch", -1)), "effective_train_rows": 20000, "validation_rows": 5000, "optimizer_steps": int(saved.get("logger", {}).get("cur_step", -1)), "dataset_sha256": sha256(a.training_dataset), "dataset_semantics": "raw canonical train vectors; this checkpoint was not trained on the THQ residual matrix", "resolved_config": {k: int(params[k]) for k in required}, "optimizer": {"name": "AdamW", "learning_rate": float(saved.get("optimizer", {}).get("param_groups", [{}])[0].get("lr", 0.0)), "scheduler": "upstream QINCo cosine/ramp scheduler"}, "exact_command": "run.py cpu=true task=train L=2 dh=256 de=128 A=16 B=32 M=16 K=256 ds.valset=5000 ds.loop=20000 epochs=1 batch=256 verbose=false", "schedule_note": "checkpoint_epoch is the number of completed full epochs over the effective 20,000-row training split; the 25,000-row source pool is split into 20,000 train and 5,000 validation rows."}
    result = {"schema_version": 3, "family": "thq_qinco2_official_replay_v3", "status": "EXECUTED", "quality_status": "BOUNDED_TRAINING_DOMAIN_MISMATCH_CONTROL", "metric": "cosine", "query_count": QUERY_COUNT, "upstream_repository": "https://github.com/facebookresearch/Qinco", "upstream_revision": revision, "upstream_license": "CC-BY-NC-4.0", "candidate_count": TOP, "candidate_shell": "canonical candidate stream -> independently recomputed THQ interval² top128", "unique_documents": int(len(unique_ids)), "checkpoint_sha256": sha256(a.checkpoint), "codes_artifact_sha256": sha256(a.codes_output), "runner_sha256": sha256(Path(__file__)), "source_hashes": {k: sha256(v) for k, v in sources.items()}, "config": {k: int(params[k]) for k in required}, "training": training, "summaries": summaries, "rows": rows, "storage_contract": {"code_dtype": "uint8", "code_shape": [int(params["M"]), "unique_documents"], "code_bytes": int(params["M"]), "final_norm_dtype": "float32", "final_norm_bytes": 4, "side_payload_bytes": int(params["M"]) + 4, "cascade_total_bytes": THQ_BYTES + int(params["M"]) + 4}, "arms": {"qinco2_official_16b_residual_mismatch": "raw-trained checkpoint applied to THQ residuals; training-domain mismatch diagnostic", "qinco2_official_16b_raw_vector": "raw-trained checkpoint applied directly to raw vectors; undertraining diagnostic"}, "limitations": ["official QINCo2 checkpoint trained on raw canonical vectors, not a THQ-residual training matrix", "bounded 25k source pool with 20k effective training rows and 5k validation rows; short schedule, not the 60-epoch production schedule", "candidate-local replay on the historical 152-query fold", "external CC-BY-NC source is not vendored", "no production selection claim"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
