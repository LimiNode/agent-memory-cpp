#!/usr/bin/env python3
"""Independent audit for the DE-1M direct-ID codec materialization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N = 1_000_000
D = 384


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    require(manifest["documents"] == N and manifest["dimension"] == D, "manifest shape differs")
    require(receipt["family"] == "native_full_corpus_codec_materialization_v1", "family differs")
    require(receipt["manifest_sha256"] == sha256(args.manifest), "manifest SHA differs")
    require(receipt["runner_sha256"] == sha256(args.runner), "runner SHA differs")
    source_meta = manifest["references"]["document_vectors"]
    source = Path(source_meta["path"])
    require(source.is_file() and source.stat().st_size == int(source_meta["bytes"]), "source size differs")
    require(sha256(source) == source_meta["sha256"], "source SHA differs")
    expected = {
        "thq4_ordinal": (96 * N, np.uint8),
        "int8_linear": (384 * N, np.int8),
        "int8_power0625": (384 * N, np.int8),
        "int8_linear_scales": (4 * N, np.float32),
        "int8_power0625_scales": (4 * N, np.float32),
        "thq4_thresholds": (D * 3 * 4, np.float32),
    }
    for role, (size, _) in expected.items():
        row = receipt["files"][role]
        path = args.output_root / row["path"]
        require(path.is_file() and path.stat().st_size == size, f"{role} size differs")
        require(sha256(path) == row["sha256"], f"{role} SHA differs")
    docs = np.memmap(source, mode="r", dtype="<f4", shape=(N, D))
    thresholds = np.fromfile(args.output_root / receipt["files"]["thq4_thresholds"]["path"], dtype="<f4").reshape(D, 3)
    expected_thresholds = np.quantile(np.asarray(docs[:100_000], dtype=np.float32), (0.25, 0.5, 0.75), axis=0).T.astype(np.float32)
    require(np.array_equal(thresholds, expected_thresholds), "threshold parity differs")
    thq = np.memmap(args.output_root / receipt["files"]["thq4_ordinal"]["path"], mode="r", dtype=np.uint8, shape=(N, 96))
    linear = np.memmap(args.output_root / receipt["files"]["int8_linear"]["path"], mode="r", dtype=np.int8, shape=(N, D))
    linear_scales = np.memmap(args.output_root / receipt["files"]["int8_linear_scales"]["path"], mode="r", dtype="<f4", shape=(N,))
    power = np.memmap(args.output_root / receipt["files"]["int8_power0625"]["path"], mode="r", dtype=np.int8, shape=(N, D))
    power_scales = np.memmap(args.output_root / receipt["files"]["int8_power0625_scales"]["path"], mode="r", dtype="<f4", shape=(N,))
    for row in (0, 1, N - 1):
        values = np.asarray(docs[row], dtype=np.float32)
        levels = np.sum(values[:, None] > thresholds, axis=1, dtype=np.uint8)
        packed = (levels[0::4] | (levels[1::4] << 2) | (levels[2::4] << 4) | (levels[3::4] << 6)).astype(np.uint8)
        require(np.array_equal(thq[row], packed), f"THQ parity differs at {row}")
        for payload, scales, power_value in ((linear, linear_scales, 1.0), (power, power_scales, 0.625)):
            transformed = np.copysign(np.power(np.abs(values), power_value), values)
            scale = max(float(np.max(np.abs(transformed))) / 127.0, 1e-8)
            encoded = np.clip(np.rint(transformed / scale), -127, 127).astype(np.int8)
            require(np.array_equal(payload[row], encoded) and np.float32(scale).tobytes() == np.float32(scales[row]).tobytes(), f"INT8 parity differs at {row}")
    print(json.dumps({"family": "native_full_corpus_codec_materialization_audit_v1", "status": "PASS", "documents": N, "sample_rows": [0, 1, N - 1]}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-native-full-corpus-codecs: {error}")
