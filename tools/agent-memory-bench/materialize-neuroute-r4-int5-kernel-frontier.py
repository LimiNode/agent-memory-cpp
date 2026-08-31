#!/usr/bin/env python3
"""Materialize zero-overhead bit-sliced INT5 mixed full stores."""
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


planner = load("neuroute_r4_int5_kernel_materializer_planner",
               "plan-neuroute-r4-int5-kernel-frontier.py")


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


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    require(sha256(args.parent_protocol) == contract["activation"][
                "physical_integration_protocol_sha256"] and
            sha256(args.parent_materialization_manifest) ==
                contract["activation"][
                    "physical_integration_materialization_sha256"] and
            sha256(args.native_executable) ==
                contract["activation"]["native_executable_sha256"],
            "R4 INT5 kernel materializer activation differs")
    parent = json.loads(args.parent_protocol.read_text(encoding="utf-8"))
    require(parent["family"] ==
            "neuroute_r4_int5_physical_integration_protocol",
            "R4 INT5 kernel materializer parent differs")
    layouts = []
    avx2_layouts = []
    for seed in contract["route"]["seeds"]:
        root = args.output_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        output = root / "mixed-int5-bitsliced-prefix-int8-remainder.records"
        receipt = root / "receipt.json"
        if not args.reuse or not output.is_file() or not receipt.is_file():
            completed = subprocess.run([str(args.native_executable),
                "--int5-bitslice-materialize", str(args.parent_protocol),
                str(seed), str(output), str(receipt)], check=False,
                capture_output=True, text=True)
            require(completed.returncode == 0,
                    "R4 INT5 bitsliced materialization failed: " +
                    completed.stderr.strip())
        value = json.loads(receipt.read_text(encoding="utf-8"))
        require(value["family"] ==
                "neuroute_r4_int5_bitsliced_materialization_receipt" and
                value["seed"] == seed and value["record_bytes"] == 244 and
                value["output_sha256"] == sha256(output) and
                value["bytes"] == output.stat().st_size,
                "R4 INT5 bitsliced materialization receipt differs")
        layouts.append({"seed": seed, "path": str(output.resolve()),
            "sha256": value["output_sha256"], "bytes": value["bytes"],
            "representatives": value["representatives"],
            "record_bytes": 244, "physical": "five_plane_vector_major",
            "receipt": str(receipt.resolve()),
            "receipt_sha256": sha256(receipt)})
        avx2_output = root / "mixed-int5-avx2-prefix-int8-remainder.records"
        avx2_receipt = root / "avx2-receipt.json"
        if (not args.reuse or not avx2_output.is_file() or
                not avx2_receipt.is_file()):
            completed = subprocess.run([str(args.native_executable),
                "--int5-avx2-materialize", str(args.parent_protocol),
                str(seed), str(avx2_output), str(avx2_receipt)], check=False,
                capture_output=True, text=True)
            require(completed.returncode == 0,
                    "R4 INT5 AVX2 materialization failed: " +
                    completed.stderr.strip())
        avx2_value = json.loads(avx2_receipt.read_text(encoding="utf-8"))
        require(avx2_value["family"] ==
                "neuroute_r4_int5_avx2_materialization_receipt" and
                avx2_value["seed"] == seed and
                avx2_value["record_bytes"] == 244 and
                avx2_value["output_sha256"] == sha256(avx2_output) and
                avx2_value["bytes"] == avx2_output.stat().st_size,
                "R4 INT5 AVX2 materialization receipt differs")
        avx2_layouts.append({"seed": seed,
            "path": str(avx2_output.resolve()),
            "sha256": avx2_value["output_sha256"],
            "bytes": avx2_value["bytes"],
            "representatives": avx2_value["representatives"],
            "record_bytes": 244, "physical": "avx2_256_sse_128_vector_major",
            "receipt": str(avx2_receipt.resolve()),
            "receipt_sha256": sha256(avx2_receipt)})
    manifest = {"schema_version": 1,
        "family": "neuroute_r4_int5_kernel_frontier_materialization",
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "parent_protocol_sha256": sha256(args.parent_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "layouts": layouts, "avx2_layouts": avx2_layouts}
    args.output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_root / "manifest.json"
    manifest_path.write_bytes(canonical(manifest))
    protocol = {"schema_version": 1,
        "family": "neuroute_r4_int5_kernel_frontier_protocol",
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "parent_protocol": str(args.parent_protocol.resolve()),
        "bitsliced_layouts": [{key: row[key] for key in
            ("seed", "path", "sha256", "bytes")} for row in layouts],
        "avx2_layouts": [{key: row[key] for key in
            ("seed", "path", "sha256", "bytes")} for row in avx2_layouts],
        "kernels": [row["id"] for row in contract["kernels"]],
        "conditions": contract["conditions"],
        "workers": contract["workers"],
        "trace_repetitions": contract["trace"]["repetitions"],
        "warmup_batches": contract["trace"]["warmup_batches"],
        "measured_batches": contract["trace"]["measured_batches"],
        "working_set_cap_bytes":
            contract["trace"]["working_set_cap_bytes"],
        "memory_crossover": contract["memory_crossover"]}
    (args.output_root / "protocol.json").write_bytes(canonical(protocol))


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-r4-int5-kernel-frontier.example.json")
    require(planner.plan(contract)["bitsliced_full_store_materializations"] == 3,
            "R4 INT5 kernel materializer self-test differs")
    print("NeuRoute R4 INT5 kernel-frontier materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int5-kernel-frontier.example.json")
    parser.add_argument("--parent-protocol", type=Path)
    parser.add_argument("--parent-materialization-manifest", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "reuse", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 INT5 kernel materializer paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"materialize-neuroute-r4-int5-kernel-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
