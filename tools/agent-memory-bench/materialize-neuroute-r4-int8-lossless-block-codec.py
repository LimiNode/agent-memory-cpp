#!/usr/bin/env python3
"""Materialize independently decodable Zstd and VByte R4 INT8 stores."""
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


planner = load("neuroute_r4_lossless_materializer_planner",
               "plan-neuroute-r4-int8-lossless-block-codec.py")


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
    if "external_root" in row:
        root /= row["external_root"]
    return root / row["file"]


def artifact(path: Path, name: str) -> dict[str, Any]:
    return {"role": name, "file": path.name, "bytes": path.stat().st_size,
            "sha256": sha256(path)}


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {"int8_compression_result_sha256": sha256(args.int8_compression_result),
            "int8_compression_evidence_sha256": sha256(args.int8_compression_evidence),
            "layout_manifest_sha256": sha256(args.layout_root / "manifest.json"),
            "native_end_to_end_result_sha256": sha256(args.native_end_to_end_result),
            "native_end_to_end_evidence_sha256": sha256(args.native_end_to_end_evidence)}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = activation(args)
    require(actual == contract["activation"], "R4 lossless activation differs")
    revision = subprocess.run(["git", "-C", str(args.zstd_root), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
    require(revision == contract["zstd"]["commit"],
            "R4 lossless Zstd revision differs")
    parent_path = args.layout_root / "manifest.json"
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    args.output_root.mkdir(parents=True, exist_ok=True)
    seeds = []
    for seed_value in contract["route"]["seeds"]:
        seed = next(row for row in parent["seeds"] if row["seed"] == seed_value)
        parent_root = args.layout_root / f"seed-{seed_value}"
        raw = resolved(parent_root, role(seed["layouts"], "address_major_int8"))
        counts = resolved(parent_root, role(seed["mappings"], "address_counts"))
        root = args.output_root / f"seed-{seed_value}"
        root.mkdir(parents=True, exist_ok=True)
        paths = {"zstd": root / "zstd-block.frames",
                 "zstd_offsets": root / "zstd-block-offsets.u64le",
                 "dictionary": root / "zstd-dictionary.bin",
                 "zstd_dictionary": root / "zstd-dictionary-block.frames",
                 "zstd_dictionary_offsets": root /
                    "zstd-dictionary-block-offsets.u64le",
                 "vbyte": root / "vbyte-zigzag.blocks",
                 "vbyte_offsets": root / "vbyte-offsets.u64le",
                 "receipt": root / "pack-receipt.json"}
        completed = subprocess.run([str(args.native_executable),
            "--lossless-block-pack", str(raw), str(counts),
            str(contract["route"]["documents"]), str(contract["zstd"]["level"]),
            str(contract["zstd"]["dictionary_capacity_bytes"]),
            str(contract["zstd"]["dictionary_training_blocks"]),
            str(paths["zstd"]), str(paths["zstd_offsets"]),
            str(paths["dictionary"]), str(paths["zstd_dictionary"]),
            str(paths["zstd_dictionary_offsets"]), str(paths["vbyte"]),
            str(paths["vbyte_offsets"]), str(paths["receipt"])],
            check=False, capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 lossless pack failed: {completed.stderr}")
        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        require(receipt["rows"] == contract["route"]["documents"] and
                receipt["zstd_version"] == contract["zstd"]["version"],
                "R4 lossless pack receipt differs")
        files = [artifact(paths["zstd"], "zstd_block"),
                 artifact(paths["zstd_offsets"], "zstd_block_offsets"),
                 artifact(paths["dictionary"], "zstd_dictionary"),
                 artifact(paths["zstd_dictionary"], "zstd_dictionary_block"),
                 artifact(paths["zstd_dictionary_offsets"],
                          "zstd_dictionary_block_offsets"),
                 artifact(paths["vbyte"], "vbyte_zigzag"),
                 artifact(paths["vbyte_offsets"], "vbyte_offsets"),
                 artifact(paths["receipt"], "pack_receipt")]
        seeds.append({"seed": seed_value, "representative_count":
                      seed["representative_count"], "files": files,
                      "receipt": receipt})
    manifest = {"schema_version": 1,
                "family": "neuroute_r4_int8_lossless_block_materialization",
                "contract_sha256": sha256(args.contract), "activation": actual,
                "layout_manifest_sha256": sha256(parent_path),
                "zstd_revision": revision, "seeds": seeds}
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "R4 lossless materializer canonical JSON differs")
    print("NeuRoute R4 lossless block materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int8-lossless-block-codec.example.json")
    for name in ("int8-compression-result", "int8-compression-evidence",
                 "layout-root", "native-end-to-end-result",
                 "native-end-to-end-evidence", "zstd-root",
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
            parser.error("all R4 lossless materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError, StopIteration) as error:
        print(f"materialize-neuroute-r4-int8-lossless-block-codec: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
