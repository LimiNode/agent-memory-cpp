#!/usr/bin/env python3
"""Materialize the 484-byte THQ3→INT8 finalist on the frozen candidate union.

This is a payload/parity materializer only. Native scoring and page/latency
receipts remain pending until a native runner consumes these files.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

DIMENSION = 384

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
    p.add_argument("--thq-manifest", type=Path, required=True)
    p.add_argument("--candidate-receipt", type=Path, required=True)
    p.add_argument("--candidate-raw", type=Path, required=True)
    p.add_argument("--candidate-flat", type=Path, required=True)
    p.add_argument("--codec-frontier-receipt", type=Path, required=True)
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--training-count", type=int, default=100_000)
    a = p.parse_args()
    manifest = json.loads(a.thq_manifest.read_text(encoding="utf-8"))
    candidate_receipt = json.loads(a.candidate_receipt.read_text(encoding="utf-8"))
    codec_frontier_receipt = json.loads(a.codec_frontier_receipt.read_text(encoding="utf-8"))
    candidate_raw = json.loads(a.candidate_raw.read_text(encoding="utf-8"))
    require(candidate_receipt["raw_sha256"] == sha256(a.candidate_raw), "candidate raw SHA differs")
    require(candidate_receipt["flat_file"]["sha256"] == sha256(a.candidate_flat), "candidate flat SHA differs")
    require(codec_frontier_receipt.get("family") == "semantic_fp32_free_codec_frontier_v2",
            "codec frontier family differs")
    require(int(codec_frontier_receipt.get("schema_version", 0)) >= 3 and
            codec_frontier_receipt.get("execution_status") == "EXECUTED" and
            codec_frontier_receipt.get("production_activation") is False,
            "codec frontier receipt status differs")
    frontier_raw_meta = codec_frontier_receipt.get("raw_output", {})
    frontier_raw_path = resolve_artifact(a.codec_frontier_receipt, frontier_raw_meta.get("path", ""))
    require(frontier_raw_path.is_file() and sha256(frontier_raw_path) == frontier_raw_meta.get("sha256"),
            "codec frontier raw binding differs")
    frontier_raw = json.loads(frontier_raw_path.read_text(encoding="utf-8"))
    require(frontier_raw.get("family") == codec_frontier_receipt["family"] and
            int(frontier_raw.get("schema_version", 0)) >= 3,
            "codec frontier raw schema differs")
    require(any(int(row.get("levels", 0)) == 4 and row.get("mode") == "interval_sq" and
                int(row.get("shortlist", 0)) == 128 for row in frontier_raw.get("stage_rows", [])),
            "THQ4 interval-squared top128 arm is missing")
    direct_names = {str(row.get("representation")) for row in frontier_raw.get("direct_rows", [])}
    require({"int8_linear", "int8_power0625"}.issubset(direct_names),
            "matched INT8 finalists are missing")
    n = int(manifest["documents"]); refs = manifest["references"]
    source_meta = refs["document_vectors"]
    source_path = resolve_artifact(a.thq_manifest, source_meta["path"])
    require(source_path.is_file(), "source vectors are missing")
    if "bytes" in source_meta:
        require(source_path.stat().st_size == int(source_meta["bytes"]), "source vector size differs")
    if "sha256" in source_meta:
        require(sha256(source_path) == source_meta["sha256"], "source vector SHA differs")
    docs = np.memmap(source_path, mode="r", dtype="<f4", shape=(n, DIMENSION))
    train = np.asarray(docs[:min(a.training_count, n)])
    thresholds = np.quantile(train, (1.0 / 3.0, 2.0 / 3.0), axis=0).T.astype(np.float32)
    counts = [int(row["candidate_count"]) for row in candidate_raw["rows"]]
    flat = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(sum(counts), 148))
    ids = np.frombuffer(np.asarray(flat[:, :4]).tobytes(), dtype="<i4").astype(np.int64)
    unique = np.unique(ids)
    vectors = np.asarray(docs[unique], dtype=np.float32)
    levels = np.sum(vectors[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    packed = (levels[:, 0::4] | (levels[:, 1::4] << 2) |
              (levels[:, 2::4] << 4) | (levels[:, 3::4] << 6)).astype(np.uint8)
    maxima = np.max(np.abs(vectors), axis=1)
    scales = np.maximum(maxima / 127.0, 1e-8).astype(np.float32)
    int8 = np.rint(vectors / scales[:, None]).clip(-127, 127).astype(np.int8)
    a.output_root.mkdir(parents=True, exist_ok=True)
    ids_path = a.output_root / "document_ids.i4"; thq_path = a.output_root / "thq3-ordinal.u8"
    scale_path = a.output_root / "int8-scales.f32"; int8_path = a.output_root / "int8-linear.i8"
    unique.astype("<i4").tofile(ids_path); packed.tofile(thq_path); scales.astype("<f4").tofile(scale_path); int8.tofile(int8_path)
    files = {name: {"path": str(path.relative_to(a.output_root)), "bytes": path.stat().st_size, "sha256": sha256(path)}
             for name, path in (("document_ids", ids_path), ("thq3_ordinal", thq_path),
                                ("int8_scales", scale_path), ("int8_linear", int8_path))}
    raw = {"schema_version": 1, "family": "semantic_fp32_free_native_finalist_materialization_v1",
           "execution_status": "EXECUTED", "production_activation": False,
           "documents": n, "unique_candidate_documents": int(len(unique)),
           "materialization_scope": "query-derived-evaluation-subset",
           "logical_bytes_per_document": {"thq3_ordinal": 96, "int8_linear": 388, "cascade_total": 484},
           "physical_subset_bytes_per_document": 488,
           "training_count": min(a.training_count, n), "files": files,
           "provenance": {"thq_manifest_sha256": sha256(a.thq_manifest), "candidate_receipt_sha256": sha256(a.candidate_receipt),
                          "candidate_raw_sha256": sha256(a.candidate_raw), "candidate_flat_sha256": sha256(a.candidate_flat),
                          "corrected_codec_frontier_receipt_sha256": sha256(a.codec_frontier_receipt),
                          "corrected_codec_frontier_raw_sha256": frontier_raw_meta["sha256"]}}
    raw_path = a.output_root / "finalist.raw.json"; raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {"schema_version": 1, "family": raw["family"], "execution_status": "EXECUTED",
               "production_activation": False, "native_replay_status": "PENDING_NATIVE_REPLAY",
               "selection_status": "PROVISIONAL_PENDING_NATIVE_FINALIST_SELECTION",
               "runner_sha256": sha256(Path(__file__)), "raw_sha256": sha256(raw_path), "provenance": raw["provenance"], "files": files}
    (a.output_root / "finalist.receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
