#!/usr/bin/env python3
"""Measure the high-width random overcomplete ADC quality curve."""
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
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_random_adc_ceiling_planner", "plan-neuroute-random-adc-ceiling.py")
conditional = load("neuroute_random_adc_ceiling_parent", "run-neuroute-conditional-followups.py")


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


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-random-adc-ceiling.py", "run-neuroute-random-adc-ceiling.py",
        "run-neuroute-conditional-followups.py", "run-neuroute-final-representation.py",
    )
    return {name: sha256(THIS / name) for name in names}


def adapted_contract(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "codec_screen": [],
        "overcomplete_screen": {
            "widths": contract["diagnostic_widths"],
            "projection_seed": contract["projection"]["seed"],
        },
    }


def query_positions(data: dict[str, Any], query_ids: list[str]) -> list[int]:
    by_id = {str(value): index for index, value in enumerate(data["query_ids"])}
    require(all(value in by_id for value in query_ids),
            "random-ADC ceiling query IDs differ")
    return [by_id[value] for value in query_ids]


def comparison(datasets: list[dict[str, Any]], representation: str) -> dict[str, Any]:
    dataset_losses = []
    for dataset in datasets:
        baseline = {(row["seed"], query["query"]): query["ndcg_at_10"]
                    for row in dataset["rows"] if row["representation"] == "fp32"
                    for query in row["queries"]}
        losses = [baseline[(row["seed"], query["query"])] - query["ndcg_at_10"]
                  for row in dataset["rows"] if row["representation"] == representation
                  for query in row["queries"]]
        dataset_losses.append(float(numpy.mean(losses)))
    return {
        "representation": representation,
        "dataset_losses": dataset_losses,
        "mean_loss": float(numpy.mean(dataset_losses)),
    }


def parent_curve(parent: dict[str, Any], widths: list[int]) -> list[dict[str, Any]]:
    by_name = {row["representation"]: row for row in parent["decision"]["comparisons"]}
    result = []
    for width in widths:
        source = by_name[f"adc{width}"]
        result.append({
            "width": width, "source": "frozen_parent",
            "dataset_losses": source["dataset_losses"], "mean_loss": source["mean_loss"],
        })
    return result


def decision(curve: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    by_width = {row["width"]: float(row["mean_loss"]) for row in curve}
    ordered = sorted(by_width)
    gains = [{
        "from_width": left, "to_width": right,
        "mean_loss_improvement": by_width[left] - by_width[right],
    } for left, right in zip(ordered, ordered[1:])]
    improvement = by_width[1024] - by_width[4096]
    return {
        "curve": curve, "marginal_gains": gains,
        "improvement_1024_to_4096": improvement,
        "plateau_confirmed": improvement <=
            contract["quality"]["maximum_1024_to_4096_mean_loss_improvement_for_plateau"],
        "best_measured_width": min(by_width, key=lambda width: (by_width[width], width)),
        "production_winner": None, "native_implementation_licensed": False,
        "learned_final_reranker_remains_separate": True,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {
        "conditional_result_sha256": sha256(args.conditional_result),
        "conditional_evidence_sha256": sha256(args.conditional_evidence),
        "final_materialization_sha256": sha256(args.final_materialization_root / "manifest.json"),
    }
    require(actual == contract["activation"], "random-ADC ceiling activation differs")
    parent = json.loads(args.conditional_result.read_text(encoding="utf-8"))
    receipt = json.loads(args.conditional_evidence.read_text(encoding="utf-8"))
    require(receipt.get("decision", {}).get("selected_overcomplete_width") is None and
            receipt.get("decision", {}).get("overcomplete_native_licensed") is False,
            "random-ADC ceiling parent decision differs")
    manifest = json.loads((args.final_materialization_root / "manifest.json").read_text(
        encoding="utf-8"))
    manifest_by_id = {row["id"]: row for row in manifest["datasets"]}
    roots = {language: {kind: getattr(args, f"{language}_{kind}_root")
                        for kind in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    datasets = []
    v4_contract = conditional.final.exact.v4.planner.load_contract(args.v4_contract)
    v4_by_id = {row["id"]: row for row in v4_contract["datasets"]}
    screen = adapted_contract(contract)
    for dataset_id, language in (("de-25k", "de"), ("fr-25k", "fr"),
                                 ("ja-25k", "ja")):
        data, _, split = conditional.final.exact.v4.base.load_dataset(
            v4_by_id[dataset_id], roots[language])
        rows, provenance = conditional.evaluate_dataset(
            data, query_positions(data, split["configuration_selection_query_ids"]),
            conditional.pools(manifest_by_id[dataset_id], args.final_materialization_root),
            screen)
        datasets.append({"id": dataset_id, "rows": rows, "overcomplete": provenance})
    scale_contract = conditional.final.scale.planner.load_contract(args.scale_contract)
    scale_config = next(row for row in scale_contract["scales"] if row["id"] == "de-1m")
    data = conditional.final.scale.load_scale(scale_config, args.de_1m_e5_root,
                                              args.de_1m_input_root)
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    rows, provenance = conditional.evaluate_dataset(
        data, query_positions(data, split["configuration_selection_query_ids"]),
        conditional.pools(manifest_by_id["de-1m"], args.final_materialization_root), screen)
    datasets.append({"id": "de-1m", "rows": rows, "overcomplete": provenance})
    curve = parent_curve(parent, contract["frozen_parent_widths"])
    curve.extend({
        "width": width, "source": "new_diagnostic", **comparison(datasets, f"adc{width}")
    } for width in contract["diagnostic_widths"])
    output = {
        "schema_version": 1, "family": "neuroute_random_overcomplete_adc_ceiling_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": actual, "source_files_sha256": source_hashes(),
        "datasets": datasets, "decision": decision(curve, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-random-adc-ceiling.example.json")
    matrix = conditional.projection(1536, contract["projection"]["seed"])
    require(matrix.shape == (384, 1536) and
            planner.plan(contract)["new_quality_rows"] == 36,
            "random-ADC ceiling self-test differs")
    print("NeuRoute random-ADC ceiling self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-random-adc-ceiling.example.json")
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--conditional-evidence", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{kind}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all random-ADC ceiling paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-random-adc-ceiling: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
