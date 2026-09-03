#!/usr/bin/env python3
"""Materialize relevance-aware v4 routes for native MDBX measurement."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_relevance_aware_v4_materializer_runner",
              "run-neuroute-relevance-aware-v4.py")
native = load("neuroute_relevance_aware_v4_native_helpers",
              "materialize-neuroute-native-mdbx-cost.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def materializer_source_hashes() -> dict[str, str]:
    names = (
        "materialize-neuroute-relevance-aware-v4.py",
        "materialize-neuroute-native-mdbx-cost.py",
        "run-neuroute-relevance-aware-v4.py",
    )
    return {name: runner.sha256(THIS / name) for name in names}


def model_file(args: argparse.Namespace, dataset: str, treatment: dict[str, Any], seed: int) -> Path:
    if treatment["source"] == "reuse_frozen_training_sanity_raw_euclidean_model_bytes":
        return args.training_model_root / dataset / f"model-raw_euclidean_mined_pairs-{seed}.npz"
    return args.model_root / dataset / f"model-{treatment['id']}-{seed}.npz"


def materialize_dataset(dataset: dict[str, Any], roots: dict[str, Path],
                        result_dataset: dict[str, Any], contract: dict[str, Any],
                        args: argparse.Namespace) -> dict[str, Any]:
    data, _, split = runner.base.load_dataset(dataset, roots)
    positions_by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [positions_by_id[value] for value in split["configuration_selection_query_ids"]]
    require(len(positions) == dataset["configuration_queries"],
            f"relevance-aware v4 materialization query partition differs: {dataset['id']}")
    dataset_root = args.output_root / dataset["id"]
    dataset_root.mkdir(parents=True, exist_ok=True)
    common = {
        "document_codes": native.write_array(dataset_root / "document-codes.u8",
                                               data["document_codes"], "u1"),
        "query_codes": native.write_array(dataset_root / "query-codes.u8",
                                            data["query_codes"][positions], "u1"),
        "query_projection": native.write_array(dataset_root / "query-projection.f32le",
                                                 data["query_projection"][positions], "<f4"),
        "adc_centroids": native.write_array(dataset_root / "adc-centroids.f32le",
                                              data["adc_centroids"], "<f4"),
        "document_id_rank": native.write_array(dataset_root / "document-id-rank.u32le",
                                                 native.lexicographic_ranks(data["document_ids"]), "<u4"),
    }
    routes = []
    model_rows = {(row["treatment"], row["seed"]): row for row in result_dataset["models"]}
    for treatment in contract["treatments"]:
        for seed in contract["encoder"]["seeds"]:
            row = model_rows[(treatment["id"], seed)]
            path = model_file(args, dataset["id"], treatment, seed)
            require(path.is_file() and runner.sha256(path) == row["model_sha256"],
                    f"relevance-aware v4 materialization model differs: {dataset['id']} {treatment['id']} {seed}")
            arrays, _ = runner.base.read_model(path)
            document_raw = runner.base.infer(data["documents"], arrays, False)
            threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
            require(numpy.array_equal(threshold, numpy.asarray(row["threshold"], dtype=numpy.float32)),
                    f"relevance-aware v4 materialization threshold differs: {dataset['id']} {treatment['id']} {seed}")
            query_raw = runner.base.infer(data["queries"], arrays, False)
            route_id = f"{treatment['id']}-{seed}"
            routes.append(native.route_record(
                dataset_root, data, positions, route_id, "learned", seed,
                document_raw - threshold, query_raw[positions] - threshold,
                contract["encoder"]["bits"], 1, contract["routing"]["probe_budgets"],
                row["model_sha256"], contract["routing"]["candidate_mass_target"]))
    pca_document, artifact = runner.base.alignment.german.direct.document_head(data["documents"])
    pca_query = ((data["queries"] - artifact["document_mean"]) @ artifact["document_projection"]
                 - artifact["document_threshold"]).astype(numpy.float32)
    routes.append(native.route_record(
        dataset_root, data, positions, "pca", "pca", None, pca_document, pca_query[positions],
        contract["routing"]["pca_bits"], contract["routing"]["pca_replication"],
        [contract["routing"]["pca_probes"]], None,
        contract["routing"]["candidate_mass_target"]))
    return {
        "id": dataset["id"], "language": dataset["language"],
        "document_count": len(data["document_ids"]), "query_count": len(positions),
        "source_query_positions": positions, "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"], "common": common, "routes": routes,
    }


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_relevance_aware_v4_quality_result"
            and result.get("contract_sha256") == runner.sha256(args.contract)
            and result.get("source_files_sha256") == runner.source_hashes(),
            "relevance-aware v4 materialization result binding differs")
    roots = {language: {name: getattr(args, f"{language}_{name}_root")
                        for name in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    require(all(path is not None for values in roots.values() for path in values.values()),
            "relevance-aware v4 materialization dataset roots are required")
    result_by_id = {row["id"]: row for row in result["datasets"]}
    datasets = [materialize_dataset(dataset, roots[dataset["language"]],
                                    result_by_id[dataset["id"]], contract, args)
                for dataset in contract["datasets"]]
    manifest = {
        "schema_version": 1, "family": "neuroute_relevance_aware_v4_native_materialization",
        "claim_scope": contract["claim_scope"], "contract_sha256": runner.sha256(args.contract),
        "quality_result_sha256": runner.sha256(args.result),
        "quality_source_files_sha256": runner.source_hashes(),
        "materializer_source_files_sha256": materializer_source_hashes(),
        "activation": contract["activation"], "storage": contract["storage"],
        "native_timing": contract["native_timing"], "datasets": datasets,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "manifest.json").write_bytes(runner.canonical(manifest))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-relevance-aware-v4.example.json")
    require(len(runner.planner.matrix(contract)) + len(contract["datasets"]) == 111,
            "relevance-aware v4 materialization matrix differs")
    values = numpy.asarray([7, 2, 99], dtype=numpy.uint32)
    require(native.sequence_sha256(values) == runner.sequence_sha256(values),
            "relevance-aware v4 materialization digest differs")
    print("NeuRoute relevance-aware v4 materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-relevance-aware-v4.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(all(value is not None for value in (
            args.result, args.training_model_root, args.model_root, args.output_root)),
            "relevance-aware v4 materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-relevance-aware-v4: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
