#!/usr/bin/env python3
"""Additive fail-closed audit for the frozen conditional representation rows."""

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


runner = load("neuroute_conditional_closure_runner",
              "run-neuroute-conditional-followups.py")


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
            "conditional closure bootstrap samples are absent")
    generator = numpy.random.default_rng(seed)
    samples = numpy.zeros(replicates, dtype=numpy.float64)
    for values in dataset_values:
        indices = generator.integers(0, values.size, size=(replicates, values.size))
        samples += numpy.mean(values[indices], axis=1)
    samples /= len(dataset_values)
    return [float(numpy.quantile(samples, 0.025)),
            float(numpy.quantile(samples, 0.975))]


def validate_parent(contract: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    final_contract = runner.final.planner.load_contract(args.final_contract)
    final_result = json.loads(args.final_result.read_text(encoding="utf-8"))
    final_evidence = json.loads(args.final_evidence.read_text(encoding="utf-8"))
    final_manifest_path = args.final_materialization_root / "manifest.json"
    actual = {
        "final_result_sha256": sha256(args.final_result),
        "final_materialization_sha256": sha256(final_manifest_path),
        "final_evidence_sha256": sha256(args.final_evidence),
    }
    require(actual == contract["activation"], "conditional closure parent activation differs")
    require(final_result.get("contract_sha256") == sha256(args.final_contract)
            and final_result.get("activation") == final_contract["activation"],
            "conditional closure parent result differs")
    require(final_evidence.get("passed") is True
            and final_evidence.get("quality_result_sha256") == sha256(args.final_result)
            and final_evidence.get("materialization_sha256") == sha256(final_manifest_path),
            "conditional closure parent evidence differs")

    exact_contract = runner.final.exact.planner.load_contract(args.exact_contract)
    scale_contract = runner.final.scale.planner.load_contract(args.scale_contract)
    v4_contract = runner.final.exact.v4.planner.load_contract(args.v4_contract)
    require(sha256(args.exact_contract)
            == final_contract["activation"]["exact_contract_sha256"],
            "conditional closure exact contract differs")
    require(sha256(args.scale_contract)
            == final_contract["activation"]["scale_contract_sha256"],
            "conditional closure scale contract differs")
    require(sha256(args.v4_contract) == exact_contract["activation"]["v4_contract_sha256"],
            "conditional closure v4 contract differs")
    require(sha256(args.german_split_result)
            == scale_contract["activation"]["german_split_result_sha256"],
            "conditional closure German split differs")

    roots = {language: {name: getattr(args, f"{language}_{name}_root")
                        for name in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    v4_by_id = {row["id"]: row for row in v4_contract["datasets"]}
    datasets = []
    for dataset_id, language in (("de-25k", "de"), ("fr-25k", "fr"),
                                 ("ja-25k", "ja")):
        data, _, split = runner.final.exact.v4.base.load_dataset(v4_by_id[dataset_id],
                                                                 roots[language])
        datasets.append({
            "id": dataset_id,
            "frozen_result_sha256": v4_by_id[dataset_id]["result_sha256"],
            "e5_manifest_sha256": data["manifest_sha256"],
            "input_manifest_sha256": data["input_manifest_sha256"],
            "configuration_query_count": len(split["configuration_selection_query_ids"]),
        })
    scale = next(row for row in scale_contract["scales"] if row["id"] == "de-1m")
    data = runner.final.scale.load_scale(scale, args.de_1m_e5_root,
                                         args.de_1m_input_root)
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    datasets.append({
        "id": "de-1m",
        "e5_manifest_sha256": data["e5_manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "german_split_result_sha256": sha256(args.german_split_result),
        "configuration_query_count": len(split["configuration_selection_query_ids"]),
    })
    return {
        **actual,
        "final_contract_sha256": sha256(args.final_contract),
        "exact_contract_sha256": sha256(args.exact_contract),
        "v4_contract_sha256": sha256(args.v4_contract),
        "scale_contract_sha256": sha256(args.scale_contract),
        "datasets": datasets,
    }


def validate_matrix(contract: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    representations = (["fp32"] + [row["id"] for row in contract["codec_screen"]]
                       + [f"adc{width}" for width in contract["overcomplete_screen"]["widths"]])
    require([row["id"] for row in result["datasets"]] == contract["datasets"],
            "conditional closure dataset order differs")
    total_rows = reference_rows = treatment_rows = query_rows = 0
    for dataset in result["datasets"]:
        rows = dataset["rows"]
        require(len(rows) == len(representations) * len(contract["seeds"]),
                f"conditional closure row count differs: {dataset['id']}")
        for representation in representations:
            selected = [row for row in rows if row["representation"] == representation]
            require([row["seed"] for row in selected] == contract["seeds"],
                    f"conditional closure seed matrix differs: {dataset['id']}/{representation}")
            for row in selected:
                require(row["query_count"] == len(row["queries"]),
                        f"conditional closure query count differs: {dataset['id']}/{representation}")
                query_rows += len(row["queries"])
        total_rows += len(rows)
        reference_rows += sum(row["representation"] == "fp32" for row in rows)
        treatment_rows += sum(row["representation"] != "fp32" for row in rows)
    require((treatment_rows, reference_rows, total_rows) == (108, 12, 120),
            "conditional closure 108+12 matrix differs")
    return {"treatment_rows": treatment_rows, "fp32_reference_rows": reference_rows,
            "total_rows": total_rows, "query_rows": query_rows,
            "identity": "120_total_equals_108_treatments_plus_12_fp32_references"}


def statistics(contract: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    recorded = {row["representation"]: row
                for row in result["decision"]["comparisons"]}
    names = (["fp32"] + [row["id"] for row in contract["codec_screen"]]
             + [f"adc{width}" for width in contract["overcomplete_screen"]["widths"]])
    output = []
    for number, name in enumerate(names):
        values_by_dataset = []
        dataset_means = []
        for dataset in result["datasets"]:
            baseline = {(row["seed"], query["query"]): query["ndcg_at_10"]
                        for row in dataset["rows"] if row["representation"] == "fp32"
                        for query in row["queries"]}
            values = numpy.asarray([
                baseline[(row["seed"], query["query"])] - query["ndcg_at_10"]
                for row in dataset["rows"] if row["representation"] == name
                for query in row["queries"]
            ], dtype=numpy.float64)
            require(values.size == dataset["rows"][0]["query_count"] * len(contract["seeds"]),
                    f"conditional closure statistics matrix differs: {dataset['id']}/{name}")
            values_by_dataset.append(values)
            dataset_means.append(float(numpy.mean(values)))
        observed = recorded[name]
        point = float(numpy.mean(dataset_means))
        require(dataset_means == observed["dataset_losses"] and point == observed["mean_loss"],
                f"conditional closure point estimate differs: {name}")
        output.append({
            "representation": name,
            "dataset_mean_losses": dataset_means,
            "mean_of_four_dataset_mean_losses": point,
            "stratified_mean_of_dataset_means_ci95": stratified_bootstrap(
                values_by_dataset, contract["quality"]["bootstrap_seed"] + number,
                contract["quality"]["bootstrap_replicates"]),
            "frozen_quality_eligible": observed["quality_eligible"],
        })
    return output


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_conditional_representation_quality_result"
            and result.get("contract_sha256") == sha256(args.contract)
            and result.get("activation") == contract["activation"],
            "conditional closure result binding differs")
    require(evidence.get("family") == "neuroute_conditional_representation_evidence"
            and evidence.get("passed") is True
            and evidence.get("contract_sha256") == sha256(args.contract)
            and evidence.get("result_sha256") == sha256(args.result),
            "conditional closure evidence binding differs")
    receipt = {
        "schema_version": 1,
        "family": "neuroute_conditional_representation_additive_closure",
        "passed": True,
        "claim_scope": "provenance_matrix_and_bootstrap_only_no_treatment_change",
        "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(args.result),
        "legacy_evidence_sha256": sha256(args.evidence),
        "closure_source_sha256": sha256(Path(__file__)),
        "upstream": validate_parent(contract, args),
        "matrix": validate_matrix(contract, result),
        "statistics": {
            "primary_estimand": "mean_of_four_dataset_mean_ndcg_losses",
            "bootstrap": "within_dataset_query_seed_resampling_then_equal_dataset_mean_v1",
            "replicates": contract["quality"]["bootstrap_replicates"],
            "seed_base": contract["quality"]["bootstrap_seed"],
            "comparisons": statistics(contract, result),
        },
        "frozen_decision": result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(receipt))


def self_test() -> None:
    values = [numpy.asarray([1.0, 3.0]), numpy.asarray([7.0])]
    require(stratified_bootstrap(values, 23, 100)
            == stratified_bootstrap(values, 23, 100),
            "conditional closure bootstrap self-test differs")
    contract = runner.planner.load_contract(THIS / "neuroute-conditional-followups.example.json")
    require(4 * 3 * (1 + 6 + 3) == 120
            and contract["quality"]["bootstrap_replicates"] == 10000,
            "conditional closure matrix self-test differs")
    print("NeuRoute conditional additive closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-conditional-followups.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--final-contract", type=Path)
    parser.add_argument("--final-result", type=Path)
    parser.add_argument("--final-evidence", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
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
        require(all(value is not None for name, value in vars(args).items()
                    if name != "self_test"),
                "all conditional closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-conditional-followups-closure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
