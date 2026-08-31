#!/usr/bin/env python3
"""Materialize full-corpus lossless R4 INT8 SIMDComp stores."""
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
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_r4_compression_materializer_planner",
               "plan-neuroute-r4-int8-compression.py")


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
                       sort_keys=True) + "\n").encode()


def role(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(row for row in rows if row["role"] == name)


def resolved(root: Path, row: dict[str, Any]) -> Path:
    current = root
    if row.get("external_root"):
        current /= row["external_root"]
    return current / row["file"]


def artifact(path: Path, name: str) -> dict[str, Any]:
    return {"role": name, "file": path.name, "bytes": path.stat().st_size,
            "sha256": sha256(path)}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"mapped_access_result_sha256": sha256(args.access_result),
              "mapped_access_evidence_sha256": sha256(args.access_evidence),
              "physical_layout_materialization_sha256": sha256(
                  args.layout_materialization_root / "manifest.json")}
    require(actual == contract["activation"], "R4 compression activation differs")
    parent_path = args.layout_materialization_root / "manifest.json"
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    args.output_root.mkdir(parents=True, exist_ok=True)
    seeds = []
    for seed in parent["seeds"]:
        seed_value = seed["seed"]
        require(seed_value in contract["route"]["seeds"],
                "R4 compression parent seed differs")
        parent_root = args.layout_materialization_root / f"seed-{seed_value}"
        raw = resolved(parent_root, role(seed["layouts"], "address_major_int8"))
        counts = resolved(parent_root, role(seed["mappings"], "address_counts"))
        output = args.output_root / f"seed-{seed_value}"
        output.mkdir(parents=True, exist_ok=True)
        paths = {name: output / filename for name, filename in {
            "fixed": "simdcomp-fixed8.bin", "for": "simdcomp-adaptive-for.bin",
            "for_offsets": "adaptive-for-offsets.u64",
            "zigzag": "simdcomp-adaptive-zigzag.bin",
            "zigzag_offsets": "adaptive-zigzag-offsets.u64",
            "receipt": "pack-receipt.json"}.items()}
        completed = subprocess.run([str(args.native_executable), "--compress-pack",
            str(raw), str(counts), str(contract["route"]["documents"]),
            str(paths["fixed"]), str(paths["for"]), str(paths["for_offsets"]),
            str(paths["zigzag"]), str(paths["zigzag_offsets"]),
            str(paths["receipt"])], check=False, capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 compression pack failed: {completed.stderr}")
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        require(receipt["rows"] == contract["route"]["documents"] and
                receipt["fixed8_bytes"] == receipt["raw_bytes"],
                "R4 compression receipt differs")
        files = [artifact(paths["fixed"], "simdcomp_fixed8"),
                 artifact(paths["for"], "simdcomp_adaptive_for"),
                 artifact(paths["for_offsets"], "adaptive_for_offsets"),
                 artifact(paths["zigzag"], "simdcomp_adaptive_zigzag"),
                 artifact(paths["zigzag_offsets"], "adaptive_zigzag_offsets"),
                 artifact(paths["receipt"], "pack_receipt")]
        seeds.append({"seed": seed_value, "files": files, "receipt": receipt})
    manifest = {"schema_version": 1,
                "family": "neuroute_r4_int8_compression_materialization",
                "contract_sha256": sha256(args.contract), "activation": actual,
                "parent_manifest_sha256": sha256(parent_path), "seeds": seeds,
                "totals": {treatment: sum(role(seed["files"], treatment)["bytes"]
                                           for seed in seeds)
                           for treatment in ("simdcomp_fixed8",
                                             "simdcomp_adaptive_for",
                                             "simdcomp_adaptive_zigzag")}}
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 compression materializer canonical JSON differs")
    print("NeuRoute R4 INT8 compression materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int8-compression.example.json")
    for name in ("access-result", "access-evidence", "layout-materialization-root",
                 "native-executable", "output-root"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 compression materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError, StopIteration) as error:
        print(f"materialize-neuroute-r4-int8-compression: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
