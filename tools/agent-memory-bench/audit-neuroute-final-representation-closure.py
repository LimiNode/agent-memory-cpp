#!/usr/bin/env python3
"""Additive provenance and statistics closure for the frozen final frontier."""

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


runner = load("neuroute_final_representation_closure_runner",
              "run-neuroute-final-representation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def stratified_bootstrap(dataset_values: list[numpy.ndarray], seed: int,
                         replicates: int) -> list[float]:
    require(dataset_values and all(values.size for values in dataset_values),
            "final-representation closure bootstrap samples are absent")
    generator = numpy.random.default_rng(seed)
    samples = numpy.zeros(replicates, dtype=numpy.float64)
    for values in dataset_values:
        indices = generator.integers(0, values.size, size=(replicates, values.size))
        samples += numpy.mean(values[indices], axis=1)
    samples /= len(dataset_values)
    return [float(numpy.quantile(samples, 0.025)),
            float(numpy.quantile(samples, 0.975))]


def validate_artifacts(contract: dict[str, Any], args: argparse.Namespace
                       ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract_sha = sha256(args.contract)
    require(result.get("family") == "neuroute_final_representation_quality_result"
            and result.get("contract_sha256") == contract_sha
            and result.get("activation") == contract["activation"],
            "final-representation closure result binding differs")
    require(evidence.get("family") == "neuroute_final_representation_evidence"
            and evidence.get("passed") is True
            and evidence.get("contract_sha256") == contract_sha
            and evidence.get("quality_result_sha256") == sha256(args.result)
            and evidence.get("materialization_sha256") == sha256(manifest_path)
            and evidence.get("activation") == contract["activation"],
            "final-representation closure evidence binding differs")
    require(manifest.get("contract_sha256") == contract_sha
            and manifest.get("quality_result_sha256") == sha256(args.result),
            "final-representation closure materialization binding differs")
    return result, evidence, manifest


def validate_roots(contract: dict[str, Any], result: dict[str, Any],
                   args: argparse.Namespace) -> dict[str, Any]:
    exact_contract = runner.exact.planner.load_contract(args.exact_contract)
    scale_contract = runner.scale.planner.load_contract(args.scale_contract)
    v4_contract = runner.exact.v4.planner.load_contract(args.v4_contract)
    require(sha256(args.exact_contract) == contract["activation"]["exact_contract_sha256"],
            "final-representation closure exact contract differs")
    require(sha256(args.scale_contract) == contract["activation"]["scale_contract_sha256"],
            "final-representation closure scale contract differs")
    require(sha256(args.v4_contract) == exact_contract["activation"]["v4_contract_sha256"],
            "final-representation closure v4 contract differs")
    require(sha256(args.german_split_result)
            == scale_contract["activation"]["german_split_result_sha256"],
            "final-representation closure German split differs")

    result_by_id = {row["id"]: row for row in result["datasets"]}
    v4_by_id = {row["id"]: row for row in v4_contract["datasets"]}
    roots = {language: {name: getattr(args, f"{language}_{name}_root")
                        for name in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    root_rows = []
    for dataset_id, language in (("de-25k", "de"), ("fr-25k", "fr"),
                                 ("ja-25k", "ja")):
        data, _, split = runner.exact.v4.base.load_dataset(v4_by_id[dataset_id],
                                                           roots[language])
        expected = result_by_id[dataset_id]
        require(expected["document_count"] == len(data["document_ids"])
                and expected["query_count"]
                == len(split["configuration_selection_query_ids"]),
                f"final-representation closure dataset differs: {dataset_id}")
        root_rows.append({
            "id": dataset_id,
            "frozen_result_sha256": v4_by_id[dataset_id]["result_sha256"],
            "e5_manifest_sha256": data["manifest_sha256"],
            "input_manifest_sha256": data["input_manifest_sha256"],
            "configuration_query_count": expected["query_count"],
        })

    scale = next(row for row in scale_contract["scales"] if row["id"] == "de-1m")
    data = runner.scale.load_scale(scale, args.de_1m_e5_root, args.de_1m_input_root)
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    expected = result_by_id["de-1m"]
    require(expected["document_count"] == len(data["document_ids"])
            and expected["query_count"]
            == len(split["configuration_selection_query_ids"]),
            "final-representation closure dataset differs: de-1m")
    root_rows.append({
        "id": "de-1m",
        "e5_manifest_sha256": data["e5_manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "german_split_result_sha256": sha256(args.german_split_result),
        "configuration_query_count": expected["query_count"],
    })
    return {
        "exact_contract_sha256": sha256(args.exact_contract),
        "v4_contract_sha256": sha256(args.v4_contract),
        "scale_contract_sha256": sha256(args.scale_contract),
        "datasets": root_rows,
    }


def statistics_closure(contract: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    representations = [row["id"] for row in contract["representations"]]
    recorded = {row["representation"]: row
                for row in result["decision"]["quality_comparisons"]}
    for number, name in enumerate(representations):
        dataset_values = []
        dataset_means = []
        legacy_tau_count = 0
        for dataset in result["datasets"]:
            baseline = {(row["seed"], query["query"]): query["ndcg_at_10"]
                        for row in dataset["rows"] if row["representation"] == "fp32"
                        for query in row["queries"]}
            values = numpy.asarray([
                baseline[(row["seed"], query["query"])] - query["ndcg_at_10"]
                for row in dataset["rows"] if row["representation"] == name
                for query in row["queries"]
            ], dtype=numpy.float64)
            require(values.size == dataset["query_count"] * 3,
                    f"final-representation closure query matrix differs: {dataset['id']}/{name}")
            dataset_values.append(values)
            dataset_means.append(float(numpy.mean(values)))
            legacy_tau_count += sum(
                "kendall_tau_b_on_pool" in query
                for row in dataset["rows"] if row["representation"] == name
                for query in row["queries"])
        observed = recorded[name]
        point = float(numpy.mean(dataset_means))
        require(dataset_means == observed["dataset_ndcg_losses"]
                and point == observed["cross_dataset_mean_ndcg_loss"],
                f"final-representation closure point estimate differs: {name}")
        output.append({
            "representation": name,
            "dataset_ndcg_losses": dataset_means,
            "cross_dataset_mean_ndcg_loss": point,
            "stratified_mean_of_dataset_means_ci95": stratified_bootstrap(
                dataset_values, contract["quality"]["bootstrap_seed"] + number,
                contract["quality"]["bootstrap_replicates"]),
            "legacy_pooled_query_seed_ci95": observed["paired_mean_loss_ci95"],
            "legacy_kendall_field_count": legacy_tau_count,
        })
    return output


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result, evidence, manifest = validate_artifacts(contract, args)
    receipt = {
        "schema_version": 1,
        "family": "neuroute_final_representation_additive_closure",
        "passed": True,
        "claim_scope": "provenance_and_statistics_only_no_measurement_replacement",
        "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(args.result),
        "evidence_sha256": sha256(args.evidence),
        "materialization_sha256": sha256(args.materialization_root / "manifest.json"),
        "closure_source_sha256": sha256(Path(__file__)),
        "upstream": validate_roots(contract, result, args),
        "statistics": {
            "primary_estimand": "mean_of_four_dataset_mean_ndcg_losses",
            "bootstrap": "within_dataset_query_seed_resampling_then_equal_dataset_mean_v1",
            "replicates": contract["quality"]["bootstrap_replicates"],
            "seed_base": contract["quality"]["bootstrap_seed"],
            "comparisons": statistics_closure(contract, result),
        },
        "metric_semantics": {
            "legacy_field": "kendall_tau_b_on_pool",
            "correct_interpretation": "tie_broken_kendall_tau_a_on_pool",
            "reason": "both rankings are deterministic total orders, so inversion counting has no tie correction",
        },
        "frozen_decision": evidence["decision"],
        "frozen_materialization_dataset_count": len(manifest["datasets"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(receipt))


def self_test() -> None:
    values = [numpy.asarray([0.0, 2.0]), numpy.asarray([10.0])]
    first = stratified_bootstrap(values, 17, 100)
    second = stratified_bootstrap(values, 17, 100)
    require(first == second and len(first) == 2,
            "final-representation closure bootstrap self-test differs")
    contract = runner.planner.load_contract(THIS / "neuroute-final-representation.example.json")
    require(contract["quality"]["bootstrap_replicates"] == 10000,
            "final-representation closure contract self-test differs")
    print("NeuRoute final-representation additive closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-final-representation.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--exact-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for name in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{name}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [value for name, value in vars(args).items()
                    if name not in ("self_test",) and value is None]
        require(not required, "all final-representation closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-final-representation-closure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
