#!/usr/bin/env python3
"""Measure INT5 quality on the frozen final-rerank pools."""
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


planner = load("neuroute_final_codec_planner", "plan-neuroute-final-codec.py")
conditional = load("neuroute_final_codec_parent", "run-neuroute-conditional-followups.py")


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
    names = ("plan-neuroute-final-codec.py", "run-neuroute-final-codec.py",
             "run-neuroute-conditional-followups.py")
    return {name: sha256(THIS / name) for name in names}


def adapted_contract() -> dict[str, Any]:
    return {
        "codec_screen": [{"id": "int5_document", "bits": 5, "blocks": 1,
                          "bytes_per_document": 244}],
        "overcomplete_screen": {"widths": [], "projection_seed": 0},
    }


def query_positions(data: dict[str, Any], query_ids: list[str]) -> list[int]:
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    require(all(value in by_id for value in query_ids), "final-codec query IDs differ")
    return [by_id[value] for value in query_ids]


def evaluate(data: dict[str, Any], positions: list[int], seed_pools: dict[int, numpy.ndarray]) -> list[dict[str, Any]]:
    rows, _ = conditional.evaluate_dataset(data, positions, seed_pools, adapted_contract())
    return rows


def comparison(datasets: list[dict[str, Any]], representation: str,
               contract: dict[str, Any]) -> dict[str, Any]:
    dataset_losses = []
    for dataset in datasets:
        baseline = {(row["seed"], query["query"]): query["ndcg_at_10"]
                    for row in dataset["rows"] if row["representation"] == "fp32"
                    for query in row["queries"]}
        losses = [baseline[(row["seed"], query["query"])] - query["ndcg_at_10"]
                  for row in dataset["rows"] if row["representation"] == representation
                  for query in row["queries"]]
        dataset_losses.append(float(numpy.mean(losses)))
    mean_loss = float(numpy.mean(dataset_losses))
    quality = contract["quality"]
    return {
        "representation": representation,
        "dataset_losses": dataset_losses,
        "mean_loss": mean_loss,
        "quality_eligible": (
            mean_loss <= quality["maximum_cross_dataset_mean_ndcg_loss_vs_fp32"] and
            max(dataset_losses) <= quality["maximum_per_dataset_ndcg_loss_vs_fp32"]),
    }


def frozen_parent_comparisons(parent: dict[str, Any]) -> list[dict[str, Any]]:
    wanted = {"int6_document", "int7_document", "int8_document"}
    return [row for row in parent["decision"]["comparisons"]
            if row["representation"] in wanted]


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {
        "conditional_result_sha256": sha256(args.conditional_result),
        "conditional_evidence_sha256": sha256(args.conditional_evidence),
        "final_materialization_sha256": sha256(args.final_materialization_root / "manifest.json"),
    }
    require(actual == contract["activation"], "final-codec activation differs")
    parent = json.loads(args.conditional_result.read_text(encoding="utf-8"))
    receipt = json.loads(args.conditional_evidence.read_text(encoding="utf-8"))
    require(parent["decision"]["selected_codec"] == "int6_document",
            "final-codec parent winner differs")
    require(receipt["decision"]["selected_codec"] == "int6_document",
            "final-codec parent evidence differs")
    manifest = json.loads((args.final_materialization_root / "manifest.json").read_text(
        encoding="utf-8"))
    manifest_by_id = {row["id"]: row for row in manifest["datasets"]}

    datasets = []
    roots = {language: {kind: getattr(args, f"{language}_{kind}_root")
                        for kind in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    v4_contract = conditional.final.exact.v4.planner.load_contract(args.v4_contract)
    v4_by_id = {row["id"]: row for row in v4_contract["datasets"]}
    for dataset_id, language in (("de-25k", "de"), ("fr-25k", "fr"), ("ja-25k", "ja")):
        data, _, split = conditional.final.exact.v4.base.load_dataset(
            v4_by_id[dataset_id], roots[language])
        rows = evaluate(data, query_positions(data, split["configuration_selection_query_ids"]),
                        conditional.pools(manifest_by_id[dataset_id], args.final_materialization_root))
        datasets.append({"id": dataset_id, "rows": rows})

    scale_contract = conditional.final.scale.planner.load_contract(args.scale_contract)
    scale = next(row for row in scale_contract["scales"] if row["id"] == "de-1m")
    data = conditional.final.scale.load_scale(scale, args.de_1m_e5_root,
                                              args.de_1m_input_root)
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    rows = evaluate(data, query_positions(data, split["configuration_selection_query_ids"]),
                    conditional.pools(manifest_by_id["de-1m"], args.final_materialization_root))
    datasets.append({"id": "de-1m", "rows": rows})

    comparisons = [comparison(datasets, "int5_document", contract),
                   *frozen_parent_comparisons(parent)]
    bytes_by_id = {row["id"]: row["bytes_per_document"] for row in contract["quantizers"]}
    candidates = [row for row in comparisons if row["quality_eligible"]]
    selected = min(candidates,
                   key=lambda row: (bytes_by_id[row["representation"]],
                                    row["representation"]))["representation"]
    output = {
        "schema_version": 1,
        "family": "neuroute_final_codec_quality_result",
        "contract_sha256": sha256(args.contract),
        "activation": actual,
        "source_files_sha256": source_hashes(),
        "datasets": datasets,
        "decision": {
            "comparisons": comparisons,
            "selected_quantizer": selected,
            "native_layout_timing_licensed": True,
            "full_corpus_storage_followup_licensed": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-final-codec.example.json")
    values = numpy.asarray([[1.0, -0.5]], dtype=numpy.float32)
    score = conditional.quantized_scores(values, numpy.asarray([1.0, 1.0], dtype=numpy.float32),
                                         5, 1)
    require(score.shape == (1,), "final-codec INT5 quantizer differs")
    require(planner.plan(contract)["native_rows"] == 84, "final-codec matrix differs")
    print("NeuRoute final-codec self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-final-codec.example.json")
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
            parser.error("all final-codec paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-final-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
