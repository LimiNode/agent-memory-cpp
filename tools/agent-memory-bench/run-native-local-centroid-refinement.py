#!/usr/bin/env python3
"""Measure native rescoring of learned address pools from the #176 substrate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
DIMENSIONS = 384
QUERY_COUNT = 648
TOP_K = 16
WARMUPS = 5
REPEATS = 15


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("native_local_centroid_runner", "run-direct-learned-semantic-address.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction))


def load_model(path: Path) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as stored:
        metadata = json.loads(str(stored["metadata_json"].item()))
        require(metadata.get("family") == "direct_learned_semantic_address_model_v1", "native local centroid model differs")
        return {name: numpy.asarray(stored[name]) for name in stored.files if name != "metadata_json"}


def compile_native(compiler: Path, executable: Path) -> None:
    require(compiler.is_file(), "native local centroid compiler is missing")
    command = [str(compiler), "-std=c++17", "-O3", "-march=native", str(THIS / "native-local-centroid-refinement.cpp"), "-o", str(executable)]
    subprocess.run(command, check=True)
    require(executable.is_file(), "native local centroid executable differs")


def run_native(executable: Path, centroids: Path, queries: Path, pools: Path, pool_size: int) -> dict[str, Any]:
    completed = subprocess.run([str(executable), str(centroids), str(queries), str(pools), str(QUERY_COUNT), str(pool_size), str(DIMENSIONS), str(TOP_K), str(WARMUPS), str(REPEATS)], check=True, capture_output=True, text=True)
    value = json.loads(completed.stdout)
    samples = value.get("milliseconds_per_query")
    checksums = value.get("checksums")
    require(value.get("schema_version") == 1 and value.get("query_count") == QUERY_COUNT and value.get("pool_size") == pool_size
            and value.get("top_k") == TOP_K and isinstance(samples, list) and len(samples) == REPEATS
            and isinstance(checksums, list) and len(checksums) == REPEATS and len(set(checksums)) == 1,
            "native local centroid result differs")
    require(all(isinstance(sample, float) and sample > 0.0 for sample in samples), "native local centroid timing differs")
    return {"pool_size": pool_size, "samples_ms_per_query": samples, "p50_ms_per_query": percentile(samples, 0.5),
            "p95_ms_per_query": percentile(samples, 0.95), "checksum": checksums[0]}


def run(baseline_root: Path, e5_root: Path, input_root: Path, output_root: Path, compiler: Path) -> None:
    data = runner.load_inputs(e5_root, input_root)
    artifact = load_model(baseline_root / "model.npz")
    document_logits, document_artifact = runner.document_head(data["documents"])
    for name, value in document_artifact.items():
        require(numpy.array_equal(value, artifact[name]), f"native local centroid document artifact differs: {name}")
    index = runner.build_index(document_logits, data["documents"], 8, 4)
    learned_logits = runner.infer_mlp(data["queries"], artifact)
    output_root.mkdir(parents=True, exist_ok=True)
    centroids = numpy.stack([index["centroids"][address] for address in range(256)]).astype("<f4")
    query_vectors = data["queries"].astype("<f4")
    centroid_path = output_root / "centroids.f32"
    query_path = output_root / "queries.f32"
    centroids.tofile(centroid_path)
    query_vectors.tofile(query_path)
    pools = {
        "learned_64_address_pool": numpy.asarray([runner.confidence_addresses(row, 8, 64) for row in learned_logits], dtype="<u2"),
        "full_256_centroid_scan": numpy.tile(numpy.arange(256, dtype="<u2"), (QUERY_COUNT, 1)),
    }
    executable = output_root / "native-local-centroid-refinement.exe"
    compile_native(compiler, executable)
    rows = []
    for name, addresses in pools.items():
        path = output_root / f"{name}.u16"
        addresses.tofile(path)
        row = run_native(executable, centroid_path, query_path, path, addresses.shape[1])
        rows.append({"treatment": name, **row})
    report = {"schema_version": 1, "family": "native_local_centroid_refinement_v1",
              "baseline_model_sha256": sha256(baseline_root / "model.npz"), "e5_manifest_sha256": data["manifest_sha256"],
              "input_manifest_sha256": data["input_manifest_sha256"], "compiler": str(compiler), "executable_sha256": sha256(executable),
              "source_sha256": sha256(THIS / "native-local-centroid-refinement.cpp"),
              "timing_scope": "warm_native_scalar_fp32_centroid_dot_and_top16_only_excludes_e5_and_posting_cascade_v1", "rows": rows}
    (output_root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test() -> None:
    require(TOP_K < 64 and QUERY_COUNT == 648 and DIMENSIONS == 384, "native local centroid constants differ")
    print("native local centroid refinement self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.baseline_root, args.e5_root, args.input_root, args.output_root, args.compiler)):
            parser.error("--baseline-root, --e5-root, --input-root, --output-root, and --compiler are required")
        run(args.baseline_root, args.e5_root, args.input_root, args.output_root, args.compiler)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-native-local-centroid-refinement: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
