#!/usr/bin/env python3
"""Bind the configuration queries to the physical full-R4 runtime."""
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


planner = load("neuroute_actual_r4_codec_planner",
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


def read_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line)["id"] for line in stream]


def configuration_ids(coverage: dict[str, Any]) -> list[str]:
    rows = [row for row in coverage["configuration_rows"]
            if row["treatment"] == "actual_k32_max"]
    require(len(rows) == 3, "actual-R4 configuration seed matrix differs")
    expected = [row["query_id"] for row in rows[0]["queries"]]
    require(len(expected) == 76 and len(set(expected)) == 76 and
            all([row["query_id"] for row in current["queries"]] == expected
                for current in rows[1:]),
            "actual-R4 configuration query order differs")
    return expected


def materialize(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    activation_paths = {
        "external_comparison_result_sha256": args.external_result,
        "external_comparison_evidence_sha256": args.external_evidence,
        "external_r4_protocol_sha256": args.external_r4_protocol,
        "coarse_k8_manifest_sha256": args.coarse_k8_manifest,
        "layout_manifest_sha256": args.layout_manifest,
        "coverage_saturation_result_sha256": args.coverage_result,
        "coverage_saturation_evidence_sha256": args.coverage_evidence,
        "native_input_manifest_sha256": args.native_input_manifest,
        "e5_manifest_sha256": args.e5_root / "manifest.json",
    }
    actual = {name: sha256(path) for name, path in activation_paths.items()}
    require(actual == contract["activation"],
            "actual-R4 codec frontier activation differs")
    base = json.loads(args.external_r4_protocol.read_text(encoding="utf-8"))
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    coverage = json.loads(args.coverage_result.read_text(encoding="utf-8"))
    require(base.get("family") ==
                "neuroute_external_ann_comparison_r4_protocol" and
            layout.get("family") == "neuroute_r4_layout_materialization" and
            coverage.get("family") ==
                "neuroute_r4_coverage_saturation_result",
            "actual-R4 codec frontier parent identity differs")
    expected = configuration_ids(coverage)
    require(layout["query_ids"][:76] == expected,
            "actual-R4 layout configuration queries differ")
    all_query_ids = read_ids(args.e5_root / "evaluation-query-ids.jsonl")
    positions = {value: index for index, value in enumerate(all_query_ids)}
    require(len(all_query_ids) == 305 and all(value in positions for value in expected),
            "actual-R4 evaluation query universe differs")
    requests = [{"request": local, "native_query": positions[query_id],
                 "query_id": query_id}
                for local, query_id in enumerate(expected)]
    protocol = {**base, "contract_sha256": sha256(args.contract),
        "partition": "configuration", "requests": requests,
        "native_executable_sha256": sha256(args.native_executable),
        "activation": actual}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(protocol))


def self_test() -> None:
    rows = [{"seed": seed, "treatment": "actual_k32_max",
             "queries": [{"query_id": f"q-{index}"} for index in range(76)]}
            for seed in (1, 2, 3)]
    require(configuration_ids({"configuration_rows": rows})[75] == "q-75",
            "actual-R4 configuration request self-test differs")
    print("NeuRoute actual-R4 codec materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("external-result", "external-evidence",
                 "external-r4-protocol", "coarse-k8-manifest",
                 "layout-manifest", "coverage-result", "coverage-evidence",
                 "native-input-manifest", "e5-root", "native-executable",
                 "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [name for name in vars(args)
                    if name not in {"self_test", "contract"}]
        if any(getattr(args, name) is None for name in required):
            parser.error("all actual-R4 codec materialization paths are required")
        materialize(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-actual-r4-codec-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
