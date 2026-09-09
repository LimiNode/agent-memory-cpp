#!/usr/bin/env python3
"""Bind the R4 INT5 layout stress protocol to its physical parent."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
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


planner = load("neuroute_r4_int5_stress_materializer_planner",
               "plan-neuroute-r4-int5-layout-stress.py")


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


def materialize(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {
        "physical_integration_result_sha256": sha256(args.parent_result),
        "physical_integration_evidence_sha256": sha256(args.parent_evidence),
        "physical_integration_materialization_sha256": sha256(
            args.parent_materialization),
        "physical_integration_protocol_sha256": sha256(args.parent_protocol),
    }
    require(actual == contract["activation"],
            "R4 INT5 stress activation differs")
    parent_result = json.loads(args.parent_result.read_text(encoding="utf-8"))
    parent_evidence = json.loads(args.parent_evidence.read_text(encoding="utf-8"))
    parent_protocol = json.loads(args.parent_protocol.read_text(encoding="utf-8"))
    require(parent_result["family"] ==
            "neuroute_r4_int5_physical_integration_result" and
            parent_evidence["passed"] is True and
            parent_protocol["family"] ==
            "neuroute_r4_int5_physical_integration_protocol",
            "R4 INT5 stress parent identity differs")
    args.output_root.mkdir(parents=True, exist_ok=True)
    protocol = {"schema_version": 1,
        "family": "neuroute_r4_int5_layout_stress_protocol",
        "contract_sha256": sha256(args.contract), "activation": actual,
        "parent_protocol": str(args.parent_protocol.resolve()),
        "treatments": contract["treatments"],
        "conditions": contract["conditions"],
        "workers": contract["workers"],
        "seeds": contract["route"]["seeds"],
        "trace_repetitions": contract["trace_repetitions"],
        "warmup_batches": contract["warmup_batches"],
        "measured_batches": contract["measured_batches"],
        "working_set_cap_bytes": contract["working_set_cap_bytes"]}
    protocol_path = args.output_root / "protocol.json"
    protocol_path.write_bytes(canonical(protocol))
    manifest = {"schema_version": 1,
        "family": "neuroute_r4_int5_layout_stress_materialization",
        "contract_sha256": sha256(args.contract), "activation": actual,
        "protocol_file": protocol_path.name,
        "protocol_sha256": sha256(protocol_path)}
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    require(len(canonical({"a": 1})) > 4,
            "R4 INT5 stress materializer canonical JSON differs")
    print("NeuRoute R4 INT5 layout-stress materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-layout-stress.example.json")
    for name in ("parent-result", "parent-evidence",
                 "parent-materialization", "parent-protocol", "output-root"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 INT5 stress materialization paths are required")
        materialize(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-r4-int5-layout-stress: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
