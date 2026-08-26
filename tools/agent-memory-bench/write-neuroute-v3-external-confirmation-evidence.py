#!/usr/bin/env python3
"""Replay the French external confirmation and emit a fail-closed receipt."""
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


runner = load("neuroute_v3_external_evidence_runner", "run-neuroute-v3-external-confirmation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def artifact(path: Path) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as stored:
        return {name: stored[name] for name in ("mean", "scale", "weight1", "bias1", "weight2", "bias2", "weight3", "bias3")}


def average(rows_by_seed: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [{**rows_by_seed[0][index],
             "e5_oracle_survival_after_adc": float(numpy.mean([rows[index]["e5_oracle_survival_after_adc"] for rows in rows_by_seed])),
             "reranked_ndcg_at_10": float(numpy.mean([rows[index]["reranked_ndcg_at_10"] for rows in rows_by_seed]))}
            for index in range(len(rows_by_seed[0]))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-v3-external-confirmation.example.json")
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--e5-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = runner.planner.load_contract(args.contract)
        result_path = args.result_root / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        require(result.get("family") == "neuroute_v3_external_confirmation_result"
                and result.get("contract_sha256") == runner.sha256(args.contract)
                and result.get("source_files_sha256") == runner.source_hashes(),
                "external confirmation result binding differs")
        data = runner.base.load_data(args.e5_root, args.input_root, contract)
        split = runner.partitions(data["query_ids"], contract)
        positions = {value: index for index, value in enumerate(data["query_ids"])}
        internal_positions = [positions[value] for value in split["internal_evaluation_query_ids"]]
        configuration_positions = [positions[value] for value in split["configuration_selection_query_ids"]]
        oracle, full_ndcg = runner.base.direct.exact_oracle(data, 10)
        internal: list[dict[str, Any]] = []
        configuration_count = 0
        for model in result["models"]:
            path = args.result_root / f"model-{model['treatment']}-{model['seed']}.npz"
            require(path.is_file() and runner.sha256(path) == model["model_sha256"], "external confirmation model bytes differ")
            document_logits = runner.base.v2.infer(data["documents"], artifact(path)) - numpy.asarray(model["threshold"], dtype=numpy.float32)
            query_logits = runner.base.v2.infer(data["queries"], artifact(path)) - numpy.asarray(model["threshold"], dtype=numpy.float32)
            index = runner.base.direct.build_index(document_logits, data["documents"], 12, 1)
            metrics, rows = runner.evaluate(data, internal_positions, query_logits, index, oracle, full_ndcg,
                                            contract["routing"]["headline_probes"], True, contract)
            stored = next(value for value in result["internal"] if value["treatment"] == model["treatment"] and value["seed"] == model["seed"])
            require(canonical({"metrics": metrics, "rows": rows}) == canonical({"metrics": stored["metrics"], "rows": stored["rows"]}),
                    "external confirmation internal replay differs")
            require(all(row["candidate_count"] / len(data["document_ids"]) <= contract["gates"]["maximum_candidate_fraction"] for row in rows),
                    "external confirmation candidate ceiling differs")
            internal.append({"treatment": model["treatment"], "seed": model["seed"], "rows": rows})
            for probes in contract["routing"]["configuration_frontier_probes"]:
                metrics, _ = runner.evaluate(data, configuration_positions, query_logits, index, oracle, full_ndcg, probes, False, contract)
                stored = next(value for value in result["configuration_frontier"] if value["treatment"] == model["treatment"] and value["seed"] == model["seed"] and value["probes"] == probes)
                require(canonical(metrics) == canonical({key: stored[key] for key in metrics}),
                        "external confirmation configuration replay differs")
                configuration_count += 1
        control_document, control_artifact = runner.base.direct.document_head(data["documents"])
        control_query = ((data["queries"] - control_artifact["document_mean"]) @ control_artifact["document_projection"] - control_artifact["document_threshold"]).astype(numpy.float32)
        control_index = runner.base.direct.build_index(control_document, data["documents"], 8, 4)
        control_metrics, control_rows = runner.base.direct.evaluate(data, internal_positions, control_query, control_index, oracle, full_ndcg,
            "symmetric_document_head_control", 8, 16, contract["routing"]["candidate_mass_target"], False, True)
        require(canonical({"metrics": control_metrics, "rows": control_rows}) == canonical(result["symmetric_control"]),
                "external confirmation PCA replay differs")
        dynamic = average([entry["rows"] for entry in internal if entry["treatment"] == "dynamic_false_positive"])
        positive = average([entry["rows"] for entry in internal if entry["treatment"] == "positive_only_control"])
        mechanism = runner.base.paired(positive, dynamic, contract)
        architecture = runner.base.paired(control_rows, dynamic, contract)
        gates = contract["gates"]
        mechanism["passed"] = mechanism["e5_oracle_survival_after_adc"]["delta"] >= gates["mechanism_minimum_survival_gain"] and mechanism["e5_oracle_survival_after_adc"]["ci95"][0] > 0
        architecture["passed"] = architecture["e5_oracle_survival_after_adc"]["delta"] > gates["external_minimum_survival_gain"] and architecture["e5_oracle_survival_after_adc"]["ci95"][0] > 0 and architecture["reranked_ndcg_at_10"]["delta"] > gates["external_minimum_ndcg_gain"] and architecture["reranked_ndcg_at_10"]["ci95"][0] > 0
        receipt = {"schema_version": 1, "family": "neuroute_v3_external_confirmation_evidence_v1",
                   "contract_sha256": runner.sha256(args.contract), "result_sha256": runner.sha256(result_path),
                   "source_files_sha256": runner.source_hashes(), "model_count": len(result["models"]),
                   "configuration_row_count": configuration_count, "internal_replayed": True,
                   "configuration_replayed": True, "pca_control_replayed": True,
                   "mechanism_gate": mechanism, "external_confirmation_gate": architecture,
                   "replay_passed": True}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(receipt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"write-neuroute-v3-external-confirmation-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
