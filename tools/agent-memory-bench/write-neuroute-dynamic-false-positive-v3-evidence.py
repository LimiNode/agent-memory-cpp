#!/usr/bin/env python3
"""Replay saved v3 models and write an integrity-closed evidence receipt."""
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


runner = load("v3_evidence_runner", "run-neuroute-dynamic-false-positive-v3.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def artifact(path: Path) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as stored:
        return {name: stored[name] for name in ("mean", "scale", "weight1", "bias1", "weight2", "bias2", "weight3", "bias3")}


def key(value: dict[str, Any]) -> tuple[str, int, int]:
    return value["treatment"], value["seed"], value["bits"]


def validate_matrix(result: dict[str, Any], contract: dict[str, Any]) -> set[tuple[str, int, int]]:
    expected = {key(value) for value in runner.planner.plan(contract)}
    models = result.get("models", [])
    require(isinstance(models, list) and len(models) == len(expected) and {key(value) for value in models} == expected,
            "v3 evidence model matrix differs")
    require(len({key(value) for value in models}) == len(models), "v3 evidence model matrix has duplicates")
    internal = result.get("internal", [])
    require(isinstance(internal, list) and len(internal) == len(expected) and {key(value) for value in internal} == expected,
            "v3 evidence internal matrix differs")
    expected_configuration = {(treatment, seed, bits, probes) for treatment, seed, bits in expected
                              for probes in contract["routing"]["configuration_frontier_probes"]}
    configuration = result.get("configuration_frontier", [])
    actual_configuration = {(value["treatment"], value["seed"], value["bits"], value["probes"])
                            for value in configuration}
    require(isinstance(configuration, list) and len(configuration) == len(expected_configuration)
            and actual_configuration == expected_configuration, "v3 evidence configuration matrix differs")
    return expected


def means(entries: list[dict[str, Any]], treatment: str) -> dict[str, float]:
    values = [entry["metrics"] for entry in entries if entry["treatment"] == treatment]
    require(len(values) == 3, "v3 evidence treatment seed count differs")
    return {name: float(numpy.mean([value[name] for value in values]))
            for name in ("candidate_fraction", "adc_survival", "ndcg_at_10")}


def averaged(entries: list[dict[str, Any]], treatment: str) -> list[dict[str, Any]]:
    seeds = [entry["rows"] for entry in entries if entry["treatment"] == treatment]
    require(len(seeds) == 3 and len({len(rows) for rows in seeds}) == 1, "v3 evidence row matrix differs")
    return [{**seeds[0][index],
             "e5_oracle_survival_after_adc": float(numpy.mean([rows[index]["e5_oracle_survival_after_adc"] for rows in seeds])),
             "reranked_ndcg_at_10": float(numpy.mean([rows[index]["reranked_ndcg_at_10"] for rows in seeds]))}
            for index in range(len(seeds[0]))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-dynamic-false-positive-v3.example.json")
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = runner.planner.load_contract(args.contract)
        if args.self_test:
            planned = runner.planner.plan(contract)
            configuration = [{**row, "probes": probes} for row in planned
                             for probes in contract["routing"]["configuration_frontier_probes"]]
            validate_matrix({"models": planned, "internal": planned, "configuration_frontier": configuration}, contract)
            try:
                validate_matrix({"models": planned[:-1], "internal": planned, "configuration_frontier": configuration}, contract)
            except ValueError:
                print("Dynamic v3 evidence self-test passed")
                return 0
            raise ValueError("v3 evidence self-test accepted incomplete matrix")
        require(all((args.result_root, args.e5_root, args.input_root, args.output)), "v3 evidence paths are required")
        result_path = args.result_root / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        require(result.get("family") == "neuroute_dynamic_false_positive_result_v3"
                and result.get("contract_sha256") == sha256(args.contract), "v3 evidence result binding differs")
        validate_matrix(result, contract)
        data = runner.load_data(args.e5_root, args.input_root, contract)
        split = runner.partitions(data["query_ids"], contract)
        require(result.get("split") == split, "v3 evidence split differs")
        positions = {value: index for index, value in enumerate(data["query_ids"])}
        internal_positions = [positions[value] for value in split["internal_evaluation_query_ids"]]
        configuration_positions = [positions[value] for value in split["configuration_selection_query_ids"]]
        oracle, full_ndcg = runner.direct.exact_oracle(data, 10)
        recomputed_internal: list[dict[str, Any]] = []
        configuration_count = 0
        for model in result["models"]:
            path = args.result_root / f"model-{model['treatment']}-{model['seed']}.npz"
            require(path.is_file() and sha256(path) == model["model_sha256"], "v3 model bytes differ")
            document_raw = runner.v2.infer(data["documents"], artifact(path))
            threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
            require(numpy.array_equal(threshold, numpy.asarray(model["threshold"], dtype=numpy.float32)),
                    "v3 threshold replay differs")
            document_logits = document_raw - threshold
            query_logits = runner.v2.infer(data["queries"], artifact(path)) - threshold
            index = runner.direct.build_index(document_logits, data["documents"], 12, 1)
            metrics, rows = runner.evaluate(data, internal_positions, query_logits, index, oracle, full_ndcg, 512, True)
            stored = next(value for value in result["internal"] if key(value) == key(model))
            require(canonical({"metrics": metrics, "rows": rows}) == canonical({"metrics": stored["metrics"], "rows": stored["rows"]}),
                    "v3 internal replay differs")
            recomputed_internal.append({**model, "metrics": metrics, "rows": rows})
            for probes in contract["routing"]["configuration_frontier_probes"]:
                metrics, _ = runner.evaluate(data, configuration_positions, query_logits, index, oracle, full_ndcg, probes, False)
                stored = next(value for value in result["configuration_frontier"]
                              if key(value) == key(model) and value["probes"] == probes)
                require(canonical(metrics) == canonical({name: stored[name] for name in metrics}), "v3 configuration replay differs")
                configuration_count += 1
        require(configuration_count == len(result["configuration_frontier"]), "v3 configuration replay count differs")
        require(canonical(result["internal_means"]) == canonical({"positive_only_control": means(recomputed_internal, "positive_only_control"),
                                                                     "dynamic_false_positive": means(recomputed_internal, "dynamic_false_positive")}),
                "v3 internal means differ")
        control_document, control_artifact = runner.direct.document_head(data["documents"])
        control_query = ((data["queries"] - control_artifact["document_mean"]) @ control_artifact["document_projection"]
                         - control_artifact["document_threshold"]).astype(numpy.float32)
        control_index = runner.direct.build_index(control_document, data["documents"], 8, 4)
        control_metrics, control_rows = runner.direct.evaluate(data, internal_positions, control_query, control_index, oracle, full_ndcg,
            "symmetric_document_head_control", 8, 16, .1, False, True)
        require(canonical({"metrics": control_metrics, "rows": control_rows}) == canonical(result["symmetric_control"]),
                "v3 PCA control replay differs")
        positive, dynamic = averaged(recomputed_internal, "positive_only_control"), averaged(recomputed_internal, "dynamic_false_positive")
        mechanism, architecture = runner.paired(positive, dynamic, contract), runner.paired(control_rows, dynamic, contract)
        gates = contract["gates"]
        mechanism["passed"] = mechanism["e5_oracle_survival_after_adc"]["delta"] >= gates["mechanism_minimum_survival_gain"] and mechanism["e5_oracle_survival_after_adc"]["ci95"][0] > 0
        architecture["passed"] = architecture["e5_oracle_survival_after_adc"]["delta"] >= gates["architecture_minimum_survival_gain"] and architecture["e5_oracle_survival_after_adc"]["ci95"][0] > 0 and architecture["reranked_ndcg_at_10"]["delta"] >= -gates["maximum_ndcg_loss"] and architecture["reranked_ndcg_at_10"]["ci95"][0] >= -gates["maximum_ndcg_loss"]
        receipt = {"schema_version": 2, "family": "neuroute_dynamic_false_positive_v3_evidence_v2",
                   "contract_sha256": sha256(args.contract), "result_sha256": sha256(result_path),
                   "evidence_writer_sha256": sha256(Path(__file__)), "model_count": len(result["models"]),
                   "configuration_row_count": configuration_count, "internal_replayed": True,
                   "configuration_replayed": True, "pca_control_replayed": True,
                   "mechanism_gate": mechanism, "architecture_gate": architecture,
                   "quality_gates_passed": mechanism["passed"] and architecture["passed"],
                   "integrity_replay_passed": True}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(receipt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"write-neuroute-dynamic-false-positive-v3-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
