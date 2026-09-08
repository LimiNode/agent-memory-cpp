#!/usr/bin/env python3
"""Run the frozen French external confirmation of dynamic NeuRoute v3."""
from __future__ import annotations

import argparse
import hashlib
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


planner = load("neuroute_v3_external_planner", "plan-neuroute-v3-external-confirmation.py")
base = load("neuroute_v3_external_base", "run-neuroute-dynamic-false-positive-v3.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-v3-external-confirmation.py",
             "run-neuroute-v3-external-confirmation.py",
             "run-neuroute-dynamic-false-positive-v3.py",
             "run-neuroute-inspired-semantic-address-v2.py",
             "diagnose-neuroute-v2-collisions.py")
    return {name: sha256(THIS / name) for name in names}


def partitions(query_ids: list[str], contract: dict[str, Any]) -> dict[str, list[str]]:
    split = contract["partitions"]
    ordered = sorted(query_ids, key=lambda value: (
        hashlib.sha256(split["prefix_utf8"].encode("utf-8") + value.encode("utf-8")).digest(), value))
    first = split["training"]
    second = first + split["configuration_selection"]
    result = {"training_query_ids": ordered[:first],
              "configuration_selection_query_ids": ordered[first:second],
              "internal_evaluation_query_ids": ordered[second:]}
    require([len(result[name]) for name in result] == [split["training"], split["configuration_selection"],
             split["internal_evaluation"]] and len(set(sum(result.values(), []))) == split["training"]
             + split["configuration_selection"] + split["internal_evaluation"],
             "external confirmation query split differs")
    return result


def evaluate(data: dict[str, Any], positions: list[int], logits: numpy.ndarray, index: dict[str, Any],
             oracle: numpy.ndarray, full_ndcg: numpy.ndarray, probes: int, retain: bool,
             contract: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    require(probes == contract["routing"]["headline_probes"] or probes in contract["routing"]["configuration_frontier_probes"],
            "external confirmation probe count differs")
    return base.evaluate(data, positions, logits, index, oracle, full_ndcg, probes, retain)


def run(contract_path: Path, e5_root: Path, input_root: Path, output_root: Path) -> None:
    contract = planner.load_contract(contract_path)
    data = base.load_data(e5_root, input_root, contract)
    split = partitions(data["query_ids"], contract)
    positions = {value: index for index, value in enumerate(data["query_ids"])}
    partition_positions = {name: [positions[value] for value in split[f"{name}_query_ids"]]
                           for name in ("training", "configuration_selection", "internal_evaluation")}
    documents = data["documents"]
    queries = data["queries"]
    document_neighbours, document_similarities = base.v2.nearest(
        documents, documents, 16, numpy.arange(documents.shape[0], dtype=numpy.int32))
    query_neighbours, query_similarities = base.v2.nearest(queries, documents, 10)
    oracle, full_ndcg = base.direct.exact_oracle(data, 10)
    output_root.mkdir(parents=True, exist_ok=True)
    models: list[dict[str, Any]] = []
    runtime: dict[tuple[str, int], tuple[numpy.ndarray, dict[str, Any]]] = {}
    configuration: list[dict[str, Any]] = []
    internal: list[dict[str, Any]] = []
    for planned in planner.plan(contract):
        artifact, training = base.train(documents, queries, numpy.asarray(partition_positions["training"], dtype=numpy.int32),
                                        document_neighbours, document_similarities, query_neighbours, query_similarities,
                                        planned["treatment"], planned["seed"], contract)
        model_path = output_root / f"model-{planned['treatment']}-{planned['seed']}.npz"
        base.v2.save(model_path, artifact, {"schema_version": 1,
            "family": "neuroute_v3_external_confirmation_model", "contract_sha256": sha256(contract_path),
            "training": training})
        document_logits = base.v2.infer(documents, artifact)
        query_logits = base.v2.infer(queries, artifact)
        threshold = numpy.median(document_logits, axis=0).astype(numpy.float32)
        document_logits -= threshold
        query_logits -= threshold
        index = base.direct.build_index(document_logits, documents, 12, 1)
        runtime[(planned["treatment"], planned["seed"])] = (query_logits, index)
        collision = base.diagnostic.collision(documents, base.direct.code_values(document_logits, 12),
                                               document_neighbours[:, :10], document_similarities[:, :10])
        models.append({**planned, "model_sha256": sha256(model_path), "training": training,
                       "threshold": threshold.tolist(), "collision": collision})
        for probes in contract["routing"]["configuration_frontier_probes"]:
            metrics, _ = evaluate(data, partition_positions["configuration_selection"], query_logits, index,
                                  oracle, full_ndcg, probes, False, contract)
            configuration.append({**planned, "probes": probes, **metrics})
    for planned in planner.plan(contract):
        query_logits, index = runtime[(planned["treatment"], planned["seed"])]
        metrics, rows = evaluate(data, partition_positions["internal_evaluation"], query_logits, index, oracle,
                                 full_ndcg, contract["routing"]["headline_probes"], True, contract)
        internal.append({**planned, "metrics": metrics, "rows": rows})
    control_document, control_artifact = base.direct.document_head(documents)
    control_query = ((queries - control_artifact["document_mean"]) @ control_artifact["document_projection"]
                     - control_artifact["document_threshold"]).astype(numpy.float32)
    control_index = base.direct.build_index(control_document, documents, 8, 4)
    control_metrics, control_rows = base.direct.evaluate(data, partition_positions["internal_evaluation"], control_query,
        control_index, oracle, full_ndcg, "symmetric_document_head_control", 8, 16,
        contract["routing"]["candidate_mass_target"], False, True)

    def mean_rows(treatment: str) -> dict[str, float]:
        values = [row["metrics"] for row in internal if row["treatment"] == treatment]
        return {name: float(numpy.mean([value[name] for value in values]))
                for name in ("candidate_fraction", "adc_survival", "ndcg_at_10")}

    report = {"schema_version": 1, "family": "neuroute_v3_external_confirmation_result",
              "contract_sha256": sha256(contract_path), "source_files_sha256": source_hashes(),
              "e5_manifest_sha256": data["manifest_sha256"], "input_manifest_sha256": data["input_manifest_sha256"],
              "split": split, "models": models, "configuration_frontier": configuration, "internal": internal,
              "internal_means": {"positive_only_control": mean_rows("positive_only_control"),
                                 "dynamic_false_positive": mean_rows("dynamic_false_positive")},
              "symmetric_control": {"metrics": control_metrics, "rows": control_rows}}
    (output_root / "result.json").write_bytes(canonical(report))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-v3-external-confirmation.example.json")
    split = partitions([f"q{index}" for index in range(343)], contract)
    require(sum(len(value) for value in split.values()) == 343 and len(source_hashes()) == 5,
            "external confirmation self-test differs")
    print("NeuRoute v3 external confirmation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-v3-external-confirmation.example.json")
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.e5_root, args.input_root, args.output_root)):
            parser.error("--e5-root, --input-root, and --output-root are required")
        run(args.contract, args.e5_root, args.input_root, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-v3-external-confirmation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
