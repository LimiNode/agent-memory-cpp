#!/usr/bin/env python3
"""Materialize the production-shaped 1M direct-ID codec tables.

The source manifest and all referenced files are fail-closed SHA/size inputs.
No candidate subset or document-ID indirection is accepted: row ``i`` is the
codec record for document ``i``.  The script writes THQ4 ordinal, linear INT8,
and power-.625 INT8 tables in chunks so the 1.5 GiB FP32 source is never copied
into one Python array.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

DIMENSION = 384
DOCUMENTS = 1_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def resolve(manifest: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else manifest.parent / path


def checked_input(manifest: Path, row: dict, role: str) -> Path:
    path = resolve(manifest, str(row["path"]))
    require(path.is_file(), f"missing {role}: {path}")
    if "bytes" in row:
        require(path.stat().st_size == int(row["bytes"]), f"{role} size differs")
    if "sha256" in row:
        require(sha256(path) == row["sha256"], f"{role} SHA differs")
    return path


def encode_int8(values: np.ndarray, power: float) -> tuple[np.ndarray, np.ndarray]:
    transformed = np.copysign(np.power(np.abs(values), power), values)
    maxima = np.max(np.abs(transformed), axis=1)
    scales = np.divide(maxima, 127.0, out=np.ones_like(maxima), where=maxima > 0)
    codes = np.clip(np.rint(transformed / scales[:, None]), -127, 127).astype(np.int8)
    return codes, scales.astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--training-count", type=int, default=100_000)
    parser.add_argument("--chunk-size", type=int, default=16_384)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    require(int(manifest.get("documents", 0)) == DOCUMENTS, "manifest is not DE-1M")
    require(int(manifest.get("dimension", 0)) == DIMENSION, "dimension differs")
    refs = manifest["references"]
    source = checked_input(args.manifest, refs["document_vectors"], "document_vectors")
    for role in ("queries", "teacher_ids", "qrel_ids", "qrel_scores"):
        if role in refs:
            checked_input(args.manifest, refs[role], role)
    docs = np.memmap(source, mode="r", dtype="<f4", shape=(DOCUMENTS, DIMENSION))
    train_count = min(int(args.training_count), DOCUMENTS)
    require(train_count > 0 and args.chunk_size > 0, "invalid materialization parameters")
    thresholds = np.quantile(np.asarray(docs[:train_count], dtype=np.float32),
                             (0.25, 0.5, 0.75), axis=0, method="linear").T.astype(np.float32)
    args.output_root.mkdir(parents=True, exist_ok=True)
    thq_path = args.output_root / "thq4-ordinal.u8"
    linear_path = args.output_root / "int8-linear.i8"
    linear_scales_path = args.output_root / "int8-linear-scales.f32"
    power_path = args.output_root / "int8-power0625.i8"
    power_scales_path = args.output_root / "int8-power0625-scales.f32"
    thresholds_path = args.output_root / "thq4-thresholds.f32"
    thq = np.memmap(thq_path, mode="w+", dtype=np.uint8, shape=(DOCUMENTS, 96))
    linear = np.memmap(linear_path, mode="w+", dtype=np.int8, shape=(DOCUMENTS, DIMENSION))
    linear_scales = np.memmap(linear_scales_path, mode="w+", dtype=np.float32, shape=(DOCUMENTS,))
    power = np.memmap(power_path, mode="w+", dtype=np.int8, shape=(DOCUMENTS, DIMENSION))
    power_scales = np.memmap(power_scales_path, mode="w+", dtype=np.float32, shape=(DOCUMENTS,))
    for start in range(0, DOCUMENTS, args.chunk_size):
        stop = min(start + args.chunk_size, DOCUMENTS)
        values = np.asarray(docs[start:stop], dtype=np.float32)
        levels = np.sum(values[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
        thq[start:stop] = (levels[:, 0::4] | (levels[:, 1::4] << 2) |
                           (levels[:, 2::4] << 4) | (levels[:, 3::4] << 6))
        linear[start:stop], linear_scales[start:stop] = encode_int8(values, 1.0)
        power[start:stop], power_scales[start:stop] = encode_int8(values, 0.625)
    for array in (thq, linear, linear_scales, power, power_scales):
        array.flush()
    thresholds.astype("<f4").tofile(thresholds_path)
    files = {}
    for role, path, payload in (("thq4_ordinal", thq_path, 96),
                                ("int8_linear_codes", linear_path, 384),
                                ("int8_power0625_codes", power_path, 384),
                                ("int8_linear_scales", linear_scales_path, 4),
                                ("int8_power0625_scales", power_scales_path, 4),
                                ("thq4_thresholds", thresholds_path, 0)):
        files[role] = {"path": str(path.relative_to(args.output_root)),
                       "bytes": path.stat().st_size, "sha256": sha256(path),
                       "logical_bytes_per_document": payload}
    raw = {"schema_version": 1, "family": "native_full_corpus_codec_materialization_v1",
           "execution_status": "EXECUTED", "production_activation": False,
           "documents": DOCUMENTS, "dimension": DIMENSION,
           "training_count": train_count, "quantile_method": "linear",
           "numpy_version": np.__version__, "files": files,
           "manifest_sha256": sha256(args.manifest),
           "source_document_vectors": {"bytes": source.stat().st_size, "sha256": sha256(source)}}
    raw["power0625_scale_semantics"] = {
        "stored_value": "transformed_domain_scale",
        "decode_gain": "pow(stored_value, 1.6)",
        "native_query_path": "decode_gain_materialized_once_before_query_loop",
    }
    raw_path = args.output_root / "native-full-corpus.raw.json"
    # Keep file-level byte accounting separate from representation totals.  A
    # future footprint composer must add the 384-byte code stream and the
    # 4-byte scale stream exactly once.
    raw["representations"] = {
        "int8_linear": {"logical_bytes_per_document": 388,
                         "components": ["int8_linear_codes", "int8_linear_scales"]},
        "int8_power0625": {"logical_bytes_per_document": 388,
                            "components": ["int8_power0625_codes", "int8_power0625_scales"]},
    }
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {"schema_version": 1, "family": raw["family"],
               "execution_status": "EXECUTED", "production_activation": False,
               "runner_sha256": sha256(Path(__file__)), "raw_sha256": sha256(raw_path),
               "manifest_sha256": raw["manifest_sha256"], "files": files,
               "representations": raw["representations"],
               "documents": DOCUMENTS, "dimension": DIMENSION,
               "training_count": train_count, "quantile_method": "linear",
               "numpy_version": np.__version__,
               "source_document_vectors": raw["source_document_vectors"]}
    receipt["power0625_scale_semantics"] = raw["power0625_scale_semantics"]
    (args.output_root / "native-full-corpus.receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
