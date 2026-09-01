#!/usr/bin/env python3
"""Materialize compact K1/K2 INT8 stores for approximate K8 prefiltering."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load("neuroute_k8_prefilter_base",
            "materialize-neuroute-k8-codec.py")


def run(args: argparse.Namespace) -> None:
    contract = base.planner.load_contract(args.contract)
    treatments = {row["id"]: row for row in base.planner.treatments(contract)}
    base.require(args.treatment == "int8_uniform" and
                 args.treatment in treatments and
                 args.prototype_limit in (1, 2),
                 "K8 prefilter invocation differs")
    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    base.require(source.get("family") ==
                 "neuroute_current_k8_physical_materialization" and
                 layout.get("family") == "neuroute_r4_layout_materialization" and
                 base.sha256(args.source_manifest) ==
                 contract["activation"]["coarse_k8_manifest_sha256"],
                 "K8 prefilter source identity differs")
    layout["root"] = str(args.layout_manifest.parent)
    rows = [base.materialize_seed(row, layout, treatments[args.treatment],
            args.prototype_limit, args.native_executable,
            args.output_root / f"seed-{row['seed']}", args.chunk_rows)
            for row in source["seeds"]]
    manifest = {"schema_version": 1,
        "family": "neuroute_k8_codec_materialization",
        "role": "compact_approximate_prefilter",
        "contract_sha256": base.sha256(args.contract),
        "base_materializer_sha256": base.sha256(
            THIS / "materialize-neuroute-k8-codec.py"),
        "source_manifest_sha256": base.sha256(args.source_manifest),
        "layout_manifest_sha256": base.sha256(args.layout_manifest),
        "treatment": args.treatment,
        "prototype_limit": args.prototype_limit,
        "seeds": rows}
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "manifest.json").write_bytes(base.canonical(manifest))


def self_test() -> None:
    counts = np.asarray([0, 1, 2, 7, 9], dtype=np.uint32)
    k1 = base.selected_rows(None, counts, 1)
    k2 = base.selected_rows(None, counts, 2)
    base.require(k1.tolist() == [0, 1, 3, 10] and
                 k2.tolist() == [0, 1, 2, 3, 4, 10, 11],
                 "K8 compact prefilter row selection differs")
    print("NeuRoute compact K8 prefilter materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("source-manifest", "layout-manifest", "native-executable",
                 "output-root"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--treatment", default="int8_uniform")
    parser.add_argument("--prototype-limit", type=int)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("source_manifest", "layout_manifest", "native_executable",
                    "output_root", "prototype_limit")
        if any(getattr(args, name) is None for name in required):
            parser.error("all compact K8 prefilter arguments are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-k8-prefilter: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
