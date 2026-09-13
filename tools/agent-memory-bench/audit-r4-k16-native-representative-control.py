#!/usr/bin/env python3
"""Fail-closed audit for the native K-prefix representative control."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

PREFIXES = (8, 16, 32)
SEEDS = (2026082701, 2026082702, 2026082703)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--codec-manifest", type=Path, required=True)
    parser.add_argument("--layout-manifest", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_k16_native_representative_control_v1",
            "wrong family")
    require(receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False, "execution mismatch")
    require(receipt["prefixes"] == list(PREFIXES) and
            receipt["seeds"] == list(SEEDS), "matrix mismatch")
    require(receipt["codec_manifest_sha256"] == sha256(args.codec_manifest),
            "codec manifest SHA mismatch")
    require(receipt["layout_manifest_sha256"] == sha256(args.layout_manifest),
            "layout manifest SHA mismatch")
    for key, path in (("native_executable", args.native_executable),
                      ("native_source", args.source)):
        require(receipt[key]["bytes"] == path.stat().st_size and
                receipt[key]["sha256"] == sha256(path), f"{key} binding mismatch")
    codec = json.loads(args.codec_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    codec_by_seed = {int(row["seed"]): row for row in codec["seeds"]}
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    rows = receipt["rows"]
    require(len(rows) == len(SEEDS) * len(PREFIXES), "receipt row count mismatch")
    seen: set[tuple[int, int]] = set()
    for row in rows:
        key = (int(row["seed"]), int(row["prefix"]))
        require(key not in seen and key[0] in SEEDS and key[1] in PREFIXES,
                "duplicate or unexpected row")
        seen.add(key)
        seed = codec_by_seed[key[0]]
        base_counts = np.fromfile(args.codec_manifest.parent / f"seed-{key[0]}" /
                                  "counts.u8", dtype=np.uint8)
        expected = int(np.minimum(base_counts, key[1]).sum())
        require(int(row["representative_count"]) == expected,
                "representative count mismatch")
        shortlist = np.fromfile(
            args.layout_manifest.parent / f"seed-{key[0]}" / "shortlist-rows.u32le",
            dtype=np.uint32).reshape(152, 1024)
        selected = np.minimum(base_counts, key[1])[shortlist].sum(axis=1)
        output = Path(row["native_result"])
        require(output.is_file() and row["native_result_sha256"] == sha256(output),
                "native result binding mismatch")
        native = json.loads(output.read_text(encoding="utf-8"))
        require(len(native["samples"]) == int(receipt["measured_passes"]) * 152,
                "native sample count mismatch")
        actual_counts = [int(sample["representatives_scored"]) for sample in native["samples"]]
        require(actual_counts == [int(value) for value in np.tile(
            selected, int(receipt["measured_passes"]))],
                "native representative count differs")
        actual_selected = row["selected_representatives"]
        values = np.asarray(selected, dtype=np.float64)
        for name, value in (("min", values.min()), ("mean", values.mean()),
                            ("p50", np.percentile(values, 50)),
                            ("p95", np.percentile(values, 95)),
                            ("p99", np.percentile(values, 99)),
                            ("max", values.max())):
            require(abs(float(actual_selected[name]) - float(value)) < 1e-12,
                    f"selected representative aggregate mismatch {key}/{name}")
        timings = np.asarray([float(sample["decode_dot_max_ms"]) for sample in native["samples"]])
        for name, value in (("min", timings.min()), ("mean", timings.mean()),
                            ("p50", np.percentile(timings, 50)),
                            ("p95", np.percentile(timings, 95)),
                            ("p99", np.percentile(timings, 99)),
                            ("max", timings.max())):
            require(abs(float(row["timing_ms"][name]) - float(value)) < 1e-12,
                    f"timing aggregate mismatch {key}/{name}")
        require(int(row["store_bytes"]) == next(item["bytes"] for item in seed["representations"]
                                                if item["id"] == "int8"),
                "INT8 store size differs")
    require(len(seen) == len(SEEDS) * len(PREFIXES), "missing matrix row")
    print(json.dumps({"status": "PASS", "family": receipt["family"],
                      "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
