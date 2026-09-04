#!/usr/bin/env python3
"""Materialize one physical K4/K8 scalar-codec treatment at a time."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
DIMENSIONS = 384


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_k8_codec_planner",
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


def descriptor(root: Path, row: dict[str, Any]) -> np.ndarray:
    path = root / row["file"]
    require(path.is_file() and path.stat().st_size == row["bytes"] and
            sha256(path) == row["sha256"],
            f"K8 codec mapping differs: {row['role']}")
    return np.memmap(path, mode="r", dtype=row["dtype"],
                     shape=tuple(row["shape"]))


def forward_compand(values: np.ndarray, kind: str,
                     parameter: float) -> np.ndarray:
    if kind == "uniform":
        return values
    if kind == "power":
        return np.copysign(np.power(np.abs(values), parameter), values)
    require(kind == "mulaw", "K8 codec compander differs")
    return np.copysign(np.log1p(parameter * np.abs(values)) /
                       np.log1p(parameter), values)


def selected_rows(source: np.memmap, counts: np.ndarray,
                  prototype_limit: int) -> np.ndarray:
    source_counts = np.minimum(counts, 8).astype(np.int64)
    target_counts = np.minimum(counts, prototype_limit).astype(np.int64)
    source_offsets = np.zeros(len(counts), dtype=np.int64)
    if len(counts) > 1:
        source_offsets[1:] = np.cumsum(source_counts[:-1], dtype=np.int64)
    positions = np.concatenate([
        np.arange(source_offsets[row], source_offsets[row] + target_counts[row],
                  dtype=np.int64) for row in range(len(counts))])
    require(len(positions) == int(target_counts.sum(dtype=np.int64)),
            "K8 codec selected-row count differs")
    return positions


def direct_integer_records(unsigned: np.ndarray, amplitudes: np.ndarray,
                           bits: int) -> np.ndarray:
    require(bits in (4, 8), "K8 direct integer layout differs")
    record_bytes = DIMENSIONS * bits // 8 + 4
    records = np.empty((len(unsigned), record_bytes), dtype=np.uint8)
    if bits == 4:
        records[:, :192] = (unsigned[:, 0::2] |
                            (unsigned[:, 1::2] << 4)).astype(np.uint8)
    else:
        records[:, :DIMENSIONS] = unsigned.astype(np.uint8, copy=False)
    records[:, -4:] = amplitudes.astype(
        "<f4", copy=False).view(np.uint8).reshape(-1, 4)
    return records


def materialize_seed(source_row: dict[str, Any], layout: dict[str, Any],
                     treatment: dict[str, Any], prototype_limit: int,
                     native: Path, root: Path, chunk_rows: int
                     ) -> dict[str, Any]:
    seed = int(source_row["seed"])
    layout_row = next(row for row in layout["seeds"]
                      if int(row["seed"]) == seed)
    layout_root = Path(layout["root"]) / f"seed-{seed}"
    mappings = {row["role"]: row for row in layout_row["mappings"]}
    counts = np.asarray(descriptor(layout_root, mappings["address_counts"]),
                        dtype=np.uint32)
    source_path = Path(source_row["path"])
    require(source_path.is_file() and sha256(source_path) ==
            source_row["sha256"], "K8 codec FP32 source differs")
    source = np.memmap(source_path, mode="r", dtype="<f4",
                       shape=(int(source_row["active_prototypes"]), DIMENSIONS))
    positions = selected_rows(source, counts, prototype_limit)
    active = len(positions)
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"k{prototype_limit}-{treatment['id']}.records"
    if treatment["kind"] == "fp32" and prototype_limit == 8:
        output_path = source_path
    elif treatment["kind"] in {"fp32", "fp16"}:
        dtype = "<f4" if treatment["kind"] == "fp32" else "<f2"
        with output.open("wb") as stream:
            for first in range(0, active, chunk_rows):
                values = np.asarray(source[positions[first:first + chunk_rows]],
                                    dtype=np.float32)
                stream.write(values.astype(dtype, copy=False).tobytes())
        output_path = output
    else:
        bits = int(treatment["bits"])
        compander = treatment["compander"]
        maximum = (1 << (bits - 1)) - 1
        codes_path = root / f"tmp-{treatment['id']}-codes"
        amplitudes_path = root / f"tmp-{treatment['id']}-amplitudes.f32le"
        code_dtype = "u1" if bits <= 8 else "<u2"
        direct_stream = output.open("wb") if bits in (4, 8) else None
        with codes_path.open("wb") as codes, amplitudes_path.open("wb") as scales:
            for first in range(0, active, chunk_rows):
                values = np.asarray(source[positions[first:first + chunk_rows]],
                                    dtype=np.float32)
                amplitudes = np.max(np.abs(values), axis=1).astype(np.float32)
                safe = np.where(amplitudes == 0.0, 1.0, amplitudes)
                normalized = np.asarray(values / safe[:, None], dtype=np.float32)
                transformed = forward_compand(normalized, compander["kind"],
                                               float(compander["parameter"]))
                quantized = np.clip(np.rint(transformed * maximum),
                                    -maximum, maximum)
                unsigned = np.asarray(quantized + maximum, dtype=code_dtype)
                if bits in (4, 8):
                    direct_stream.write(direct_integer_records(
                        unsigned, amplitudes, bits).tobytes(order="C"))
                else:
                    codes.write(unsigned.tobytes(order="C"))
                    scales.write(amplitudes.astype("<f4", copy=False).tobytes())
        if direct_stream is not None:
            direct_stream.close()
        if bits not in (4, 8):
            completed = subprocess.run([str(native), "--pack", str(bits),
                str(codes_path), str(amplitudes_path), str(active), str(output)],
                check=False, capture_output=True, text=True)
            require(completed.returncode == 0,
                    f"K8 codec native packing failed: {completed.stderr}")
        codes_path.unlink()
        amplitudes_path.unlink()
        output_path = output
    expected = active * int(treatment["record_bytes"])
    require(output_path.stat().st_size == expected,
            "K8 codec physical size differs")
    representation = {**treatment,
        "packing": ("nibble_linear" if treatment.get("bits") == 4 else
                    "simdcomp_bp128" if treatment["kind"] == "integer" and
                    treatment.get("bits") != 8 else "byte_linear"),
        "path": str(output_path.resolve()),
        "bytes": expected, "sha256": sha256(output_path)}
    return {"seed": seed, "occupied_addresses": len(counts),
        "dimensions": DIMENSIONS, "prototype_limit": prototype_limit,
        "active_prototypes": active,
        "layout": "address_major_effective_prefix_scalar_codec_v1",
        "representations": [representation]}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    treatments = {row["id"]: row for row in planner.treatments(contract)}
    require(args.treatment in treatments and args.prototype_limit in (4, 8),
            "K8 codec invocation differs")
    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    require(source.get("family") ==
                "neuroute_current_k8_physical_materialization" and
            layout.get("family") == "neuroute_r4_layout_materialization" and
            sha256(args.source_manifest) ==
                contract["activation"]["coarse_k8_manifest_sha256"],
            "K8 codec source identity differs")
    layout["root"] = str(args.layout_manifest.parent)
    rows = [materialize_seed(row, layout, treatments[args.treatment],
            args.prototype_limit, args.native_executable,
            args.output_root / f"seed-{row['seed']}", args.chunk_rows)
            for row in source["seeds"]]
    manifest = {"schema_version": 1,
        "family": "neuroute_k8_codec_materialization",
        "contract_sha256": sha256(args.contract),
        "source_manifest_sha256": sha256(args.source_manifest),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "treatment": args.treatment,
        "prototype_limit": args.prototype_limit, "seeds": rows}
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    values = np.asarray([[-1.0, -.25, 0.0, .25, 1.0]], dtype=np.float32)
    transformed = forward_compand(values, "power", .625)
    require(np.allclose(transformed[[0, 0], [0, 4]], [-1.0, 1.0]) and
            transformed[0, 2] == 0.0,
            "K8 codec compander self-test differs")
    codes = np.arange(DIMENSIONS, dtype=np.uint16)[None, :] % 255
    amplitudes = np.asarray([0.75], dtype=np.float32)
    int8 = direct_integer_records(codes.astype(np.uint8), amplitudes, 8)
    require(int8.shape == (1, 388) and
            np.array_equal(int8[0, :DIMENSIONS], codes[0]) and
            np.frombuffer(int8[0, -4:].tobytes(), dtype="<f4")[0] == .75,
            "K8 raw INT8 record self-test differs")
    print("NeuRoute K8 codec materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("source-manifest", "layout-manifest", "native-executable",
                 "output-root"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--treatment")
    parser.add_argument("--prototype-limit", type=int)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("source_manifest", "layout_manifest", "native_executable",
                    "output_root", "treatment", "prototype_limit")
        if any(getattr(args, name) is None for name in required):
            parser.error("all K8 codec materialization arguments are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-k8-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
