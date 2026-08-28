#!/usr/bin/env python3
"""Materialize raw quantized top-64 rows for codec-layout replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def resolve_payload(root: Path, dataset_id: str, payload: dict[str, Any]) -> Path:
    path = Path(payload["file"])
    if not payload.get("external_frozen_root", False):
        path = root / dataset_id / path
    require(path.is_file() and sha256(path) == payload["sha256"],
            "final-codec source payload differs")
    return path


def read_payload(root: Path, dataset_id: str, payload: dict[str, Any], dtype: str) -> numpy.ndarray:
    values = numpy.fromfile(resolve_payload(root, dataset_id, payload), dtype=dtype)
    return values.reshape(payload["shape"])


def write_payload(path: Path, values: numpy.ndarray, dtype: str) -> dict[str, Any]:
    array = numpy.ascontiguousarray(values, dtype=dtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    array.tofile(path)
    return {"file": path.name, "sha256": sha256(path), "shape": list(array.shape),
            "dtype": dtype}


def expected_rows(quality: dict[str, Any], parent: dict[str, Any], dataset_id: str,
                  seed: int, bits: int) -> list[dict[str, Any]]:
    source = quality if bits == 5 else parent
    representation = f"int{bits}_document"
    dataset = next(row for row in source["datasets"] if row["id"] == dataset_id)
    row = next(row for row in dataset["rows"]
               if int(row["seed"]) == seed and row["representation"] == representation)
    return [{"query": query["query"], "ranked_sha256": query["ranked_sha256"]}
            for query in row["queries"]]


def run(args: argparse.Namespace) -> None:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    quality = json.loads(args.quality_result.read_text(encoding="utf-8"))
    parent = json.loads(args.conditional_result.read_text(encoding="utf-8"))
    manifest_path = args.final_materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(args.quality_result) == args.quality_result_sha256,
            "final-codec quality binding differs")
    require(sha256(manifest_path) == contract["activation"]["final_materialization_sha256"],
            "final-codec materialization binding differs")

    output_datasets = []
    for dataset in manifest["datasets"]:
        dataset_id = dataset["id"]
        fp32 = next(row for row in dataset["representations"] if row["id"] == "fp32")
        documents = numpy.memmap(resolve_payload(args.final_materialization_root, dataset_id,
                                                 fp32["encoded"]), dtype="<f4", mode="r",
                                 shape=tuple(fp32["encoded"]["shape"]))
        queries = read_payload(args.final_materialization_root, dataset_id,
                               dataset["query_vectors"], "<f4")
        document_ranks = read_payload(args.final_materialization_root, dataset_id,
                                      dataset["document_id_rank"], "<u4")
        dataset_root = args.output_root / dataset_id
        routes = []
        for route in dataset["routes"]:
            seed = int(route["seed"])
            pools = read_payload(args.final_materialization_root, dataset_id,
                                 route["pool"], "<u4")
            route_root = dataset_root / str(seed)
            quantizers = []
            for bits in (5, 6, 7, 8):
                maximum = (1 << (bits - 1)) - 1
                values = numpy.asarray(documents[pools], dtype=numpy.float32)
                scales = numpy.max(numpy.abs(values), axis=2).astype(numpy.float32) / maximum
                scales[scales == 0] = 1.0
                codes = numpy.clip(numpy.rint(values / scales[:, :, None]),
                                   -maximum, maximum).astype(numpy.int16)
                unsigned = (codes + maximum).astype(numpy.uint8)
                quantizers.append({
                    "bits": bits,
                    "bytes_per_document": 384 * bits // 8 + 4,
                    "raw_codes": write_payload(route_root / f"codes-u{bits}.bin", unsigned, "u1"),
                    "scales": write_payload(route_root / f"scales-u{bits}.f32le", scales, "<f4"),
                    "expected": expected_rows(quality, parent, dataset_id, seed, bits),
                })
            routes.append({
                "seed": seed,
                "pools": write_payload(route_root / "pools.u32le", pools, "<u4"),
                "ranks": write_payload(route_root / "ranks.u32le", document_ranks[pools], "<u4"),
                "quantizers": quantizers,
            })
        output_datasets.append({
            "id": dataset_id,
            "query_count": int(dataset["query_count"]),
            "queries": write_payload(dataset_root / "queries.f32le", queries, "<f4"),
            "routes": routes,
        })

    output = {
        "schema_version": 1,
        "family": "neuroute_final_codec_native_materialization",
        "contract_sha256": sha256(args.contract),
        "quality_result_sha256": sha256(args.quality_result),
        "conditional_result_sha256": sha256(args.conditional_result),
        "final_materialization_sha256": sha256(manifest_path),
        "simdcomp_commit": contract["simdcomp"]["commit"],
        "timing": contract["native_timing"],
        "datasets": output_datasets,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "manifest.json").write_bytes(canonical(output))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--quality-result", type=Path, required=True)
    parser.add_argument("--quality-result-sha256", required=True)
    parser.add_argument("--conditional-result", type=Path, required=True)
    parser.add_argument("--final-materialization-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-final-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
