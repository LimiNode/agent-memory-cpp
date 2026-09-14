#!/usr/bin/env python3
"""Measure native mean-coarse scoring plus K16 INT8 refinement."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np


A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def checked(root: Path, row: dict[str, Any]) -> Path:
    path = root / str(row["file"])
    require(path.is_file() and path.stat().st_size == int(row["bytes"]),
            f"artifact differs: {path}")
    require(sha256(path) == row["sha256"], f"artifact SHA differs: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-manifest", type=Path, required=True)
    parser.add_argument("--coarse-root", type=Path, required=True)
    parser.add_argument("--r4-layout-manifest", type=Path, required=True)
    parser.add_argument("--r4-layout-root", type=Path, required=True)
    parser.add_argument("--r4-codec-manifest", type=Path, required=True)
    parser.add_argument("--r4-codec-root", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measured-passes", type=int, default=1)
    args = parser.parse_args()
    require(args.measured_passes >= 1, "measured passes must be positive")
    coarse_manifest = json.loads(args.coarse_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.r4_layout_manifest.read_text(encoding="utf-8"))
    codec = json.loads(args.r4_codec_manifest.read_text(encoding="utf-8"))
    codec_by_seed = {int(row["seed"]): row for row in codec["seeds"]}
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    coarse_by_seed = {int(row["seed"]): row for row in coarse_manifest["seeds"]}
    args.work_root.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    native_outputs: list[dict[str, Any]] = []
    for seed in SEEDS:
        coarse_record = coarse_by_seed[seed]
        coarse_path = args.coarse_root / coarse_record["file"]
        require(coarse_path.is_file() and coarse_path.stat().st_size == int(coarse_record["bytes"]),
                f"coarse vector file differs: {coarse_path}")
        codec_record = codec_by_seed[seed]
        codec_root = args.r4_codec_root / f"seed-{seed}"
        mappings = {str(row["role"]): row for row in codec_record["mappings"]}
        offsets = checked(codec_root, mappings["address_offsets"])
        counts = checked(codec_root, mappings["address_counts"])
        int8 = next(row for row in codec_record["representations"] if row["id"] == "int8")
        store = checked(codec_root, int8)
        layout_record = layout_by_seed[seed]
        layout_root = args.r4_layout_root / f"seed-{seed}"
        queries_row = next(row for row in layout_record["mappings"]
                           if row["role"] == "query_vectors")
        queries = checked(layout_root, queries_row)
        output = args.work_root / f"seed-{seed}.native.json"
        command = [str(args.native_executable), "--benchmark-coarse-refine",
                   str(coarse_path), str(coarse_record["rows"]), str(store),
                   str(codec_record["representative_count"]), str(offsets), str(counts),
                   str(queries), ",".join(str(value) for value in A_VALUES),
                   str(args.measured_passes), str(output)]
        subprocess.run(command, check=True, timeout=1_800,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        result = json.loads(output.read_text(encoding="utf-8"))
        require(result["family"] == "semantic_r4_k1_coarse_k16_native_samples_v1" and
                int(result["measured_passes"]) == args.measured_passes and
                result["a_values"] == list(A_VALUES),
                f"native result metadata differs: {seed}")
        require(len(result["samples"]) == QUERIES * len(A_VALUES) * args.measured_passes,
                f"native sample count differs: {seed}")
        for sample in result["samples"]:
            samples.append({"seed": seed, **sample})
        native_outputs.append({"seed": seed, "path": str(output), "bytes": output.stat().st_size,
                              "sha256": sha256(output), "coarse_sha256": coarse_record["sha256"],
                              "store_sha256": int8["sha256"], "offsets_sha256": mappings["address_offsets"]["sha256"],
                              "counts_sha256": mappings["address_counts"]["sha256"],
                              "queries_sha256": queries_row["sha256"]})
    summaries: list[dict[str, Any]] = []
    for a in A_VALUES:
        selected = [row for row in samples if int(row["addresses_refined"]) == a]
        summaries.append({"addresses_refined": a, "query_count": len(selected),
                          "coarse_ms": aggregate([row["coarse_ms"] for row in selected]),
                          "refine_ms": aggregate([row["refine_ms"] for row in selected]),
                          "representatives_scored": aggregate([
                              row["representatives_scored"] for row in selected])})
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = {"schema_version": 1,
                   "family": "semantic_r4_k1_coarse_k16_native_samples_v1",
                   "samples": samples}
    raw_bytes = (json.dumps(raw_payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.write_bytes(raw_bytes)
    receipt = {"schema_version": 1, "family": "semantic_r4_k1_coarse_k16_native_control_v1",
               "execution_status": "EXECUTED", "production_activation": False,
               "scientific_control": True, "seeds": list(SEEDS), "queries": QUERIES,
               "a_values": list(A_VALUES), "measured_passes": args.measured_passes,
               "coarse_manifest_sha256": sha256(args.coarse_manifest),
               "r4_layout_manifest_sha256": sha256(args.r4_layout_manifest),
               "r4_codec_manifest_sha256": sha256(args.r4_codec_manifest),
               "native_executable": {"path": str(args.native_executable),
                                     "bytes": args.native_executable.stat().st_size,
                                     "sha256": sha256(args.native_executable)},
               "native_outputs": native_outputs, "summaries": summaries,
               "runner_sha256": sha256(Path(__file__)),
               "raw_output": {"path": str(args.raw_output), "bytes": len(raw_bytes),
                              "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                              "samples": len(samples)},
               "protocol": {"coarse": "mean of clipped K16 FP32 representatives",
                            "refinement": "unsigned INT8 uniform K16 representatives, scalar native",
                            "timing": "one warmup and measured passes per seed; coarse/refine only",
                            "quality": "inherited from logical coarse/refine control; not recomputed here",
                            "physical_page_bytes": "not measured", "mdbx_bytes": "not measured"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"run-r4-k1-native-coarse-refine: {error}")
