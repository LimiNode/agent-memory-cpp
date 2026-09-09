#!/usr/bin/env python3
"""Materialize physical INT4 K32 representative side stores."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
DIMENSIONS = 384
TREATMENTS = ("int4_uniform", "int4_power_625")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_k32_codec_planner",
               "plan-neuroute-actual-r4-codec-frontier.py")


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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def descriptor(root: Path, rows: list[dict[str, Any]], role: str) -> np.ndarray:
    row = next(value for value in rows if value["role"] == role)
    path = root / row["file"]
    require(path.is_file() and path.stat().st_size == row["bytes"] and
            sha256(path) == row["sha256"],
            f"K32 codec mapping differs: {role}")
    return np.memmap(path, mode="r", dtype=row["dtype"],
                     shape=tuple(row["shape"]))


def quantize(values: np.ndarray, parameter: float | None
             ) -> tuple[np.ndarray, np.ndarray]:
    amplitudes = np.max(np.abs(values), axis=1).astype(np.float32)
    safe = np.where(amplitudes == 0.0, 1.0, amplitudes)
    normalized = np.asarray(values / safe[:, None], dtype=np.float32)
    transformed = (normalized if parameter is None else
        np.copysign(np.power(np.abs(normalized), parameter), normalized))
    codes = np.clip(np.rint(transformed * 7.0), -7.0, 7.0)
    return np.asarray(codes + 7.0, dtype=np.uint8), amplitudes


def records(codes: np.ndarray, amplitudes: np.ndarray) -> np.ndarray:
    output = np.empty((len(codes), 196), dtype=np.uint8)
    output[:, :192] = (codes[:, 0::2] |
                       (codes[:, 1::2] << 4)).astype(np.uint8)
    output[:, 192:] = amplitudes.astype(
        "<f4", copy=False).view(np.uint8).reshape(-1, 4)
    return output


def materialize_seed(seed_row: dict[str, Any], layout_root: Path,
                     output_root: Path,
                     treatments: dict[str, dict[str, Any]],
                     chunk_rows: int) -> dict[str, Any]:
    seed = int(seed_row["seed"])
    source_root = layout_root / f"seed-{seed}"
    mappings = seed_row["mappings"]
    offsets = np.asarray(descriptor(source_root, mappings, "address_offsets"),
                         dtype=np.uint32)
    counts = np.asarray(descriptor(source_root, mappings,
                                   "representative_counts"), dtype=np.uint8)
    positions = np.concatenate([np.arange(int(offset), int(offset) + int(count),
        dtype=np.int64) for offset, count in zip(offsets, counts)])
    expected = int(seed_row["representative_count"])
    require(len(positions) == expected,
            "K32 codec representative positions differ")
    fp32 = next(row for row in seed_row["layouts"]
                if row["role"] == "address_major_fp32")
    fp32_path = source_root / fp32["file"]
    require(fp32_path.stat().st_size == fp32["bytes"] and
            sha256(fp32_path) == fp32["sha256"],
            "K32 codec FP32 layout differs")
    source = np.memmap(fp32_path, mode="r", dtype="<f4",
                       shape=(int(fp32["records"]), DIMENSIONS))
    seed_output_root = output_root / f"seed-{seed}"
    seed_output_root.mkdir(parents=True, exist_ok=True)
    representations = [{**treatments["fp32"],
        "addressing": "full_address_prefix", "packing": "byte_linear",
        "path": str(fp32_path.resolve()), "bytes": int(fp32["bytes"]),
        "sha256": fp32["sha256"]}]
    for treatment_id in TREATMENTS:
        treatment = treatments[treatment_id]
        parameter = (None if treatment_id == "int4_uniform" else
                     float(treatment["compander"]["parameter"]))
        output = seed_output_root / f"{treatment_id}.records"
        with output.open("wb") as stream:
            for first in range(0, expected, chunk_rows):
                values = np.asarray(source[positions[first:first + chunk_rows]],
                                    dtype=np.float32)
                codes, amplitudes = quantize(values, parameter)
                stream.write(records(codes, amplitudes).tobytes(order="C"))
        require(output.stat().st_size == expected * 196,
                "K32 codec physical size differs")
        representations.append({**treatment,
            "addressing": "representative_side_store",
            "packing": "nibble_linear",
            "path": str(output.resolve()), "bytes": output.stat().st_size,
            "sha256": sha256(output)})
    return {"seed": seed, "occupied_addresses": len(offsets),
        "dimensions": DIMENSIONS, "active_prototypes": expected,
        "prototype_limit": 32,
        "layout": "address_major_representative_side_store_scalar_codec_v1",
        "representations": representations}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    treatments = {row["id"]: row for row in planner.treatments(contract)}
    require(all(name in treatments and treatments[name]["record_bytes"] == 196
                for name in TREATMENTS), "K32 codec treatments differ")
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    require(layout.get("family") == "neuroute_r4_layout_materialization",
            "K32 codec layout manifest differs")
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows = [materialize_seed(row, args.layout_manifest.parent,
            args.output_root, treatments, args.chunk_rows)
            for row in layout["seeds"]]
    manifest = {"schema_version": 1,
        "family": "neuroute_k32_codec_materialization",
        "contract_sha256": sha256(args.contract),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "treatments": list(TREATMENTS), "seeds": rows}
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    values = np.asarray([[-1.0, -.25, 0.0, .25, 1.0, -.5]],
                        dtype=np.float32)
    uniform, amplitudes = quantize(values, None)
    nonlinear, _ = quantize(values, .625)
    padded = np.zeros((1, DIMENSIONS), dtype=np.uint8)
    padded[0, :uniform.shape[1]] = uniform
    packed = records(padded, amplitudes)
    require(uniform.tolist() == [[0, 5, 7, 9, 14, 3]] and
            nonlinear[0, 0] == 0 and nonlinear[0, 4] == 14 and
            packed.shape == (1, 196) and
            (packed[0, 0] & 0x0f) == uniform[0, 0] and
            (packed[0, 0] >> 4) == uniform[0, 1],
            "K32 codec materializer self-test differs")
    print("NeuRoute K32 codec materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.layout_manifest is None or args.output_root is None:
            parser.error("all K32 codec materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-k32-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
