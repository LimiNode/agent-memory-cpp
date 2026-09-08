#!/usr/bin/env python3
"""Bind the frozen DE-1M vectors and top-64 requests for native I/O replay."""
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


planner = load("neuroute_full_corpus_codec_materializer_planner",
               "plan-neuroute-full-corpus-codec.py")


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


def resolve(root: Path, dataset_id: str, payload: dict[str, Any],
            relative_root: str = "") -> Path:
    path = Path(payload["file"])
    if not payload.get("external_frozen_root", False):
        path = root / dataset_id / relative_root / path
    require(path.is_file() and sha256(path) == payload["sha256"],
            "full-corpus codec source payload differs")
    return path.resolve()


def descriptor(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": path.as_posix(), "sha256": payload["sha256"],
        "shape": payload["shape"], "dtype": payload["dtype"],
    }


def expected(source: dict[str, Any], representation: str,
             seed: int) -> list[dict[str, Any]]:
    dataset = next(row for row in source["datasets"] if row["id"] == "de-1m")
    row = next(row for row in dataset["rows"]
               if row["representation"] == representation and int(row["seed"]) == seed)
    return [{"query": int(query["query"]), "ranked_sha256": query["ranked_sha256"]}
            for query in row["queries"]]


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {
        "final_codec_quality_sha256": sha256(args.final_codec_quality),
        "final_codec_evidence_sha256": sha256(args.final_codec_evidence),
        "final_codec_native_sha256": sha256(args.final_codec_native),
        "final_codec_materialization_sha256": sha256(
            args.final_codec_materialization_root / "manifest.json"),
        "final_representation_materialization_sha256": sha256(
            args.final_representation_root / "manifest.json"),
        "conditional_result_sha256": sha256(args.conditional_result),
    }
    require(actual == contract["activation"], "full-corpus codec activation differs")
    quality = json.loads(args.final_codec_quality.read_text(encoding="utf-8"))
    evidence = json.loads(args.final_codec_evidence.read_text(encoding="utf-8"))
    conditional = json.loads(args.conditional_result.read_text(encoding="utf-8"))
    require(quality["decision"]["selected_quantizer"] == "int5_document" and
            evidence["decision"]["selected_layout"] == "simdcomp_bp128" and
            evidence["decision"]["full_corpus_storage_followup_licensed"] is True,
            "full-corpus codec parent decision differs")
    root = args.final_representation_root
    parent = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    dataset = next(row for row in parent["datasets"] if row["id"] == "de-1m")
    fp32 = next(row for row in dataset["representations"] if row["id"] == "fp32")
    documents = descriptor(resolve(root, "de-1m", fp32["encoded"]), fp32["encoded"])
    queries = descriptor(resolve(root, "de-1m", dataset["query_vectors"]),
                         dataset["query_vectors"])
    ranks = descriptor(resolve(root, "de-1m", dataset["document_id_rank"]),
                       dataset["document_id_rank"])
    routes = []
    for route in dataset["routes"]:
        seed = int(route["seed"])
        pool_path = resolve(root, "de-1m", route["pool"], str(seed))
        routes.append({
            "seed": seed,
            "pool": descriptor(pool_path, route["pool"]),
            "expected": {
                "int5": expected(quality, "int5_document", seed),
                "int6": expected(conditional, "int6_document", seed),
            },
        })
    require([row["seed"] for row in routes] == contract["dataset"]["router_seeds"] and
            all(len(row["expected"]["int5"]) == 76 and
                len(row["expected"]["int6"]) == 76 for row in routes),
            "full-corpus codec request matrix differs")
    output = {
        "schema_version": 1,
        "family": "neuroute_full_corpus_codec_native_input",
        "contract_sha256": sha256(args.contract),
        "activation": actual,
        "dataset": contract["dataset"],
        "representations": contract["representations"],
        "documents": documents,
        "queries": queries,
        "document_id_rank": ranks,
        "routes": routes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-full-corpus-codec.example.json")
    require(planner.plan(contract)["fresh_process_samples"] == 124,
            "full-corpus codec materializer self-test differs")
    print("NeuRoute full-corpus codec materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-full-corpus-codec.example.json")
    parser.add_argument("--final-codec-quality", type=Path)
    parser.add_argument("--final-codec-evidence", type=Path)
    parser.add_argument("--final-codec-native", type=Path)
    parser.add_argument("--final-codec-materialization-root", type=Path)
    parser.add_argument("--final-representation-root", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all full-corpus codec materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-full-corpus-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
