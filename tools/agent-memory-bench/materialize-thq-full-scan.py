#!/usr/bin/env python3
"""Materialize THQ and packed scalar codes for the native flat scan."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

D = 384


def pack_scalar(values: np.ndarray, bits: int, minimum: np.ndarray,
                maximum: np.ndarray) -> np.ndarray:
    """Pack unsigned per-coordinate scalar levels into a little-endian bitstream."""
    levels = (1 << bits) - 1
    span = np.maximum(maximum - minimum, 1.0e-8)
    codes = np.rint((values - minimum[None, :]) / span[None, :] * levels)
    codes = np.clip(codes, 0, levels).astype(np.uint16)
    total_bits = D * bits
    bit_planes = ((codes[:, :, None] >> np.arange(bits, dtype=np.uint16)) & 1).astype(np.uint8)
    return np.packbits(bit_planes.reshape(values.shape[0], total_bits), axis=1, bitorder="little")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def output_array(manifest: dict[str, Any], root: Path, name: str) -> np.ndarray:
    row = manifest["outputs"][name]
    return np.fromfile(root / row["path"], dtype=np.dtype(row["dtype"])).reshape(row["shape"])


def record(path: Path, dtype: str, shape: tuple[int, ...]) -> dict[str, Any]:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": sha256(path), "dtype": dtype, "shape": list(shape)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routing-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-documents", type=int, default=100000)
    parser.add_argument("--chunk", type=int, default=20000)
    args = parser.parse_args()
    source = json.loads(args.routing_manifest.read_text(encoding="utf-8"))
    root = args.routing_manifest.parent
    count = int(source["documents"])
    documents_path = Path(source["native_payloads"]["document_vectors"]["path"])
    documents = np.memmap(documents_path, mode="r", dtype="<f4", shape=(count, D))
    queries = output_array(source, root, "eval_queries").astype(np.float32)
    training = np.asarray(documents[:args.training_documents], dtype=np.float32)
    thresholds4 = np.quantile(training, (.25, .5, .75), axis=0).T.astype("<f4")
    thresholds3 = np.quantile(training, (1.0 / 3.0, 2.0 / 3.0), axis=0).T.astype("<f4")
    minimum = np.min(training, axis=0).astype("<f4")
    maximum = np.max(training, axis=0).astype("<f4")
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, thresholds, threshold_count in (("thq4", thresholds4, 3), ("thq3", thresholds3, 2)):
        threshold_path = args.output / f"{name}-quantile-thresholds.f32"
        thresholds.tofile(threshold_path)
        code_bytes = D * threshold_count // 8
        document_path = args.output / f"{name}-document-codes.u8"
        encoded = np.memmap(document_path, mode="w+", dtype=np.uint8, shape=(count, code_bytes))
        for start in range(0, count, args.chunk):
            stop = min(count, start + args.chunk)
            bits = (np.asarray(documents[start:stop])[:, :, None] > thresholds[None, :, :]).reshape(stop - start, -1)
            encoded[start:stop] = np.packbits(bits, axis=1, bitorder="little")
        encoded.flush(); del encoded
        query_path = args.output / f"{name}-query-codes.u8"
        np.packbits((queries[:, :, None] > thresholds[None, :, :]).reshape(len(queries), -1), axis=1,
                    bitorder="little").tofile(query_path)
        outputs[name + "_thresholds"] = record(threshold_path, "<f4", thresholds.shape)
        outputs[name + "_document_codes"] = record(document_path, "|u1", (count, code_bytes))
        outputs[name + "_query_codes"] = record(query_path, "|u1", (len(queries), code_bytes))
    minimum_path = args.output / "scalar-minimum.f32"; minimum.tofile(minimum_path)
    maximum_path = args.output / "scalar-maximum.f32"; maximum.tofile(maximum_path)
    outputs["scalar_minimum"] = record(minimum_path, "<f4", minimum.shape)
    outputs["scalar_maximum"] = record(maximum_path, "<f4", maximum.shape)
    for bits in (8, 10, 12):
        code_bytes = (D * bits + 7) // 8
        document_path = args.output / f"int{bits}-document-codes.u8"
        encoded = np.memmap(document_path, mode="w+", dtype=np.uint8, shape=(count, code_bytes))
        for start in range(0, count, args.chunk):
            stop = min(count, start + args.chunk)
            encoded[start:stop] = pack_scalar(np.asarray(documents[start:stop]), bits, minimum, maximum)
        encoded.flush(); del encoded
        query_path = args.output / f"int{bits}-query-codes.u8"
        pack_scalar(queries, bits, minimum, maximum).tofile(query_path)
        outputs[f"int{bits}_document_codes"] = record(document_path, "|u1", (count, code_bytes))
        outputs[f"int{bits}_query_codes"] = record(query_path, "|u1", (len(queries), code_bytes))
    references = {
        "document_vectors": source["native_payloads"]["document_vectors"],
        "itq_document_codes": source["native_payloads"]["document_codes"],
        "itq_query_codes": source["outputs"]["query_codes_mapped"],
        "queries": source["outputs"]["eval_queries"],
        "teacher_ids": source["outputs"]["eval_teacher_ids"],
        "qrel_ids": source["outputs"]["eval_qrel_ids"],
        "qrel_scores": source["outputs"]["eval_qrel_scores"],
    }
    for row in references.values():
        path = Path(row["path"])
        if not path.is_absolute():
            row["path"] = str((root / path).resolve())
    manifest = {"schema_version": 2, "family": "thq_full_scan_materialization_v2",
                "documents": count, "dimension": D, "queries": len(queries),
                "outputs": outputs, "references": references,
                "protocol": {"codec": "THQ3/THQ4 quantile thermometer Hamming plus packed scalar",
                             "training_documents": args.training_documents,
                             "threshold_quantiles": [.25, .5, .75],
                             "scalar_quantization": "per-coordinate min/max unsigned little-endian packed levels",
                             "routing_manifest_sha256": sha256(args.routing_manifest)}}
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "sha256": sha256(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
