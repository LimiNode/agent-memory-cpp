#!/usr/bin/env python3
"""Measure native packed representative scoring for R4 prefixes."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
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


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "p99": float(np.percentile(array, 99)),
            "max": float(array.max())}


def record(root: Path, rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    selected = [row for row in rows if row["role"] == role]
    require(len(selected) == 1, f"missing or duplicate {role}")
    value = dict(selected[0]); path = root / value["file"]
    require(path.is_file() and path.stat().st_size == int(value["bytes"]),
            f"{role} bytes differ: {path}")
    require(sha256(path) == value["sha256"], f"{role} SHA differs: {path}")
    return {**value, "path": str(path)}


def run_one(executable: Path, store: Path, rows: int, offsets: Path,
            counts: Path, shortlists: Path, queries: Path, passes: int,
            output: Path) -> dict[str, Any]:
    command = [str(executable), "--benchmark-dot", "8", "uniform", "0",
               str(store), str(rows), str(offsets), str(counts),
               str(shortlists), str(queries), str(passes), str(output)]
    completed = subprocess.run(command, check=False, capture_output=True,
                               text=True)
    require(completed.returncode == 0,
            f"native representative scorer failed: {completed.stderr}")
    result = json.loads(output.read_text(encoding="utf-8"))
    require(result["family"] == "neuroute_r4_nonlinear_codec_native_samples",
            "unexpected native result family")
    require(int(result["bits"]) == 8 and result["compander"] == "uniform",
            "native representation differs")
    require(int(result["measured_passes"]) == passes,
            "native measured-pass count differs")
    require(len(result["samples"]) == 152 * passes,
            "native sample count differs")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codec-manifest", type=Path, required=True)
    parser.add_argument("--layout-manifest", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measured-passes", type=int, default=3)
    args = parser.parse_args()
    require(args.measured_passes >= 1, "measured passes must be positive")
    codec = json.loads(args.codec_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    require(codec["family"] == "neuroute_r4_representative_codec_materialization",
            "unexpected codec manifest family")
    require(len(codec["seeds"]) == len(SEEDS), "codec seed matrix differs")
    require(len(layout["seeds"]) == len(SEEDS), "layout seed matrix differs")
    require(args.native_executable.is_file() and args.source.is_file(),
            "native executable/source is missing")
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    codec_by_seed = {int(row["seed"]): row for row in codec["seeds"]}
    require(set(layout_by_seed) == set(SEEDS) and set(codec_by_seed) == set(SEEDS),
            "seed identity differs")
    args.work_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for seed in SEEDS:
        codec_seed = codec_by_seed[seed]
        layout_seed = layout_by_seed[seed]
        codec_root = args.codec_manifest.parent / f"seed-{seed}"
        layout_root = args.layout_manifest.parent / f"seed-{seed}"
        mappings = {row["role"]: record(codec_root, codec_seed["mappings"], row["role"])
                    for row in codec_seed["mappings"]}
        layout_mappings = {row["role"]: record(layout_root, layout_seed["mappings"], row["role"])
                           for row in layout_seed["mappings"]
                           if row["role"] in {"shortlist_rows", "query_vectors"}}
        representation = next(row for row in codec_seed["representations"]
                              if row["id"] == "int8")
        store = codec_root / representation["file"]
        require(store.is_file() and store.stat().st_size == int(representation["bytes"]),
                f"INT8 store bytes differ: {store}")
        require(sha256(store) == representation["sha256"],
                f"INT8 store SHA differs: {store}")
        rows_count = int(codec_seed["representative_count"])
        base_counts = np.fromfile(mappings["address_counts"]["path"], dtype=np.uint8)
        base_offsets = mappings["address_offsets"]
        shortlist_rows = np.fromfile(
            layout_mappings["shortlist_rows"]["path"], dtype=np.uint32).reshape(152, 1024)
        for prefix in PREFIXES:
            counts_path = args.work_root / f"seed-{seed}-counts-k{prefix}.u8"
            clipped_counts = np.minimum(base_counts, prefix).astype(np.uint8)
            clipped_counts.tofile(counts_path)
            expected_count = int(clipped_counts.sum())
            selected_counts = clipped_counts[shortlist_rows].sum(axis=1)
            native_output = args.work_root / f"seed-{seed}-k{prefix}.native.json"
            native = run_one(args.native_executable, store, rows_count,
                             Path(base_offsets["path"]), counts_path,
                             Path(layout_mappings["shortlist_rows"]["path"]),
                             Path(layout_mappings["query_vectors"]["path"]),
                             args.measured_passes, native_output)
            samples = native["samples"]
            per_query = [int(row["representatives_scored"]) for row in samples]
            timings = [float(row["decode_dot_max_ms"]) for row in samples]
            require(per_query == [int(value) for value in np.tile(
                selected_counts, args.measured_passes)],
                    f"native K{prefix} representative count differs")
            rows.append({
                "seed": seed,
                "prefix": prefix,
                "representative_count": expected_count,
                "selected_representatives": aggregate(
                    [float(value) for value in selected_counts]),
                "native_result": str(native_output),
                "native_result_sha256": sha256(native_output),
                "checksum": float(native["checksum"]),
                "timing_ms": aggregate(timings),
                "samples": len(samples),
                "store_bytes": int(representation["bytes"]),
                "store_sha256": representation["sha256"],
                "counts_sha256": sha256(counts_path),
            })
    receipt = {
        "schema_version": 1,
        "family": "semantic_r4_k16_native_representative_control_v1",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "prefixes": list(PREFIXES),
        "seeds": list(SEEDS),
        "queries": 152,
        "addresses_per_query": 1024,
        "measured_passes": args.measured_passes,
        "codec_manifest_sha256": sha256(args.codec_manifest),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "native_executable": {"path": str(args.native_executable),
                               "bytes": args.native_executable.stat().st_size,
                               "sha256": sha256(args.native_executable)},
        "native_source": {"path": str(args.source),
                           "bytes": args.source.stat().st_size,
                           "sha256": sha256(args.source)},
        "environment": {"platform": platform.platform()},
        "protocol": {
            "representation": "symmetric unsigned INT8 plus FP32 scale",
            "score": "native max dot product over the first K representatives",
            "shortlist": "frozen 1,024-address query rows",
            "warmup": "one untimed pass inside the native executable",
            "timing_scope": "decode plus dot plus per-address max",
            "physical_page_bytes": "not measured",
            "production_activation": False,
        },
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
