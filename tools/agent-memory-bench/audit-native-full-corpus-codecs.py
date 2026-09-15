#!/usr/bin/env python3
"""Independent chunk-wise audit for the DE-1M direct-ID codec materialization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N = 1_000_000
D = 384
FAMILY = "native_full_corpus_codec_materialization_v1"


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


def encode_int8(values: np.ndarray, power: float) -> tuple[np.ndarray, np.ndarray]:
    transformed = np.copysign(np.power(np.abs(values), power), values)
    maxima = np.max(np.abs(transformed), axis=1)
    scales = np.divide(maxima, 127.0, out=np.ones_like(maxima), where=maxima > 0)
    codes = np.clip(np.rint(transformed / scales[:, None]), -127, 127).astype(np.int8)
    return codes, scales.astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True,
                        help="materializer source used to create the payload")
    parser.add_argument("--audit-receipt", type=Path,
                        help="machine-readable receipt written by this audit")
    parser.add_argument("--chunk-size", type=int, default=16_384)
    args = parser.parse_args()
    require(args.chunk_size > 0, "chunk-size must be positive")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    require(manifest["documents"] == N and manifest["dimension"] == D,
            "manifest shape differs")
    require(receipt["family"] == FAMILY, "family differs")
    require(receipt["manifest_sha256"] == sha256(args.manifest), "manifest SHA differs")
    require(receipt["runner_sha256"] == sha256(args.runner), "materializer SHA differs")

    raw_path = args.receipt.parent / "native-full-corpus.raw.json"
    require(raw_path.is_file(), f"missing materialization raw receipt: {raw_path}")
    require(receipt.get("raw_sha256") == sha256(raw_path), "raw receipt SHA differs")

    source_meta = manifest["references"]["document_vectors"]
    source = resolve(args.manifest, str(source_meta["path"]))
    require(source.is_file() and source.stat().st_size == int(source_meta["bytes"]),
            "source size differs")
    require(sha256(source) == source_meta["sha256"], "source SHA differs")

    expected = {
        "thq4_ordinal": (96 * N, np.uint8),
        "int8_linear_codes": (384 * N, np.int8),
        "int8_power0625_codes": (384 * N, np.int8),
        "int8_linear_scales": (4 * N, np.float32),
        "int8_power0625_scales": (4 * N, np.float32),
        "thq4_thresholds": (D * 3 * 4, np.float32),
    }
    payloads: dict[str, Path] = {}
    for role, (size, _) in expected.items():
        row = receipt["files"][role]
        path = args.receipt.parent / row["path"]
        require(path.is_file() and path.stat().st_size == size, f"{role} size differs")
        require(sha256(path) == row["sha256"], f"{role} SHA differs")
        payloads[role] = path

    docs = np.memmap(source, mode="r", dtype="<f4", shape=(N, D))
    thresholds = np.fromfile(payloads["thq4_thresholds"], dtype="<f4").reshape(D, 3)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    require(raw.get("quantile_method") == "linear", "quantile method differs")
    require(isinstance(raw.get("numpy_version"), str) and raw["numpy_version"],
            "NumPy version is absent")
    power_semantics = raw.get("power0625_scale_semantics", {})
    require(power_semantics.get("stored_value") == "transformed_domain_scale",
            "power scale semantics differ")
    require(power_semantics.get("decode_gain") == "pow(stored_value, 1.6)",
            "power decode-gain semantics differ")
    training_count = int(raw.get("training_count", 0))
    require(training_count > 0 and training_count <= N, "training count differs")
    expected_thresholds = np.quantile(
        np.asarray(docs[:training_count], dtype=np.float32),
        (0.25, 0.5, 0.75), axis=0, method="linear"
    ).T.astype(np.float32)
    require(expected_thresholds.tobytes() == thresholds.tobytes(),
            "threshold parity differs")

    thq = np.memmap(payloads["thq4_ordinal"], mode="r", dtype=np.uint8, shape=(N, 96))
    linear = np.memmap(payloads["int8_linear_codes"], mode="r", dtype=np.int8,
                       shape=(N, D))
    linear_scales = np.memmap(payloads["int8_linear_scales"], mode="r", dtype="<f4",
                              shape=(N,))
    power = np.memmap(payloads["int8_power0625_codes"], mode="r", dtype=np.int8,
                      shape=(N, D))
    power_scales = np.memmap(payloads["int8_power0625_scales"], mode="r", dtype="<f4",
                             shape=(N,))

    audited = 0
    for start in range(0, N, args.chunk_size):
        stop = min(start + args.chunk_size, N)
        values = np.asarray(docs[start:stop], dtype=np.float32)
        levels = np.sum(values[:, :, None] > thresholds[None, :, :], axis=2,
                        dtype=np.uint8)
        packed = (levels[:, 0::4] | (levels[:, 1::4] << 2) |
                  (levels[:, 2::4] << 4) | (levels[:, 3::4] << 6)).astype(np.uint8)
        require(packed.tobytes() == np.asarray(thq[start:stop]).tobytes(),
                f"THQ parity differs in rows {start}:{stop}")
        for actual, actual_scales, power_value, label in (
            (linear, linear_scales, 1.0, "linear"),
            (power, power_scales, 0.625, "power0625"),
        ):
            observed = np.asarray(actual[start:stop])
            require(int(observed.min()) >= -127 and int(observed.max()) <= 127,
                    f"{label} code range differs in rows {start}:{stop}")
            encoded, scales = encode_int8(values, power_value)
            require(encoded.tobytes() == np.asarray(actual[start:stop]).tobytes(),
                    f"{label} code parity differs in rows {start}:{stop}")
            require(scales.tobytes() == np.asarray(actual_scales[start:stop]).tobytes(),
                    f"{label} scale parity differs in rows {start}:{stop}")
        audited += stop - start

    audit_path = args.audit_receipt or (args.output_root / "native-full-corpus.audit.receipt.json")
    audit = {
        "schema_version": 1,
        "family": "native_full_corpus_codec_materialization_audit_v2",
        "status": "PASS",
        "documents": N,
        "rows_audited": audited,
        "chunk_size": args.chunk_size,
        "training_count": training_count,
        "quantile_method": "linear",
        "numpy_version": raw["numpy_version"],
        "power0625_scale_semantics": power_semantics,
        "manifest_sha256": sha256(args.manifest),
        "materializer_sha256": sha256(args.runner),
        "materialization_receipt_sha256": sha256(args.receipt),
        "materialization_raw_sha256": sha256(raw_path),
        "source_document_vectors": {
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        },
        "output_payload_sha256": {
            role: sha256(path) for role, path in payloads.items()
        },
        "audit_runner_sha256": sha256(Path(__file__)),
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-native-full-corpus-codecs: {error}")
