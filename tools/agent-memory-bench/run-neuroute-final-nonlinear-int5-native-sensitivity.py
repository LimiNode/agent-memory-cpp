#!/usr/bin/env python3
"""Replay #256 quantized values with native C++ reduction order."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
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


parent = load("neuroute_final_nonlinear_native_parent",
              "run-neuroute-final-nonlinear-int5.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_final_nonlinear_int5_native_sensitivity",
            "final nonlinear INT5 native sensitivity contract differs")
    require(value["expected_selected_nonlinear"] == "int5_power_075" and
            value["decision"]["native_result_is_sensitivity_not_reselection"]
            is True,
            "final nonlinear INT5 native sensitivity boundary differs")
    return value


def payload(path: Path, shape: tuple[int, ...], dtype: str,
            root: Path) -> dict[str, Any]:
    return {"file": path.relative_to(root).as_posix(), "sha256": parent.sha256(path),
            "shape": list(shape), "dtype": dtype}


def ideal_dcg(grades: dict[str, int]) -> float:
    return sum((2.0 ** grade - 1.0) / math.log2(rank + 2.0)
               for rank, grade in enumerate(
                   sorted(grades.values(), reverse=True)[:10]))


def materialize(args: argparse.Namespace, contract: dict[str, Any],
                quality: dict[str, Any]) -> Path:
    root = args.work_root / "native-input"
    root.mkdir(parents=True, exist_ok=True)
    source_manifest = json.loads((args.final_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in source_manifest["datasets"]}
    case_count = sum(int(by_id[row["id"]]["query_count"]) *
                     len(by_id[row["id"]]["routes"])
                     for row in parent.planner.load_contract(
                         args.parent_contract)["datasets"])
    queries = numpy.memmap(root / "queries.f32", mode="w+", dtype="<f4",
                           shape=(case_count, 384))
    positions_out = numpy.memmap(root / "positions.u32", mode="w+", dtype="<u4",
                                 shape=(case_count, 64))
    ranks_out = numpy.memmap(root / "ranks.u32", mode="w+", dtype="<u4",
                             shape=(case_count, 64))
    grades_out = numpy.memmap(root / "grades.i32", mode="w+", dtype="<i4",
                              shape=(case_count, 64))
    fp32 = numpy.memmap(root / "fp32.f32", mode="w+", dtype="<f4",
                        shape=(case_count, 64, 384))
    uniform = numpy.memmap(root / "int5-uniform.f32", mode="w+", dtype="<f4",
                           shape=(case_count, 64, 384))
    selected = numpy.memmap(root / "int5-power-075.f32", mode="w+", dtype="<f4",
                            shape=(case_count, 64, 384))
    parent_contract = parent.planner.load_contract(args.parent_contract)
    treatment_by_id = {row["id"]: row for row in parent_contract["treatments"]}
    cases: list[dict[str, Any]] = []
    cursor = 0
    for dataset_spec in parent_contract["datasets"]:
        dataset_id = dataset_spec["id"]
        dataset = by_id[dataset_id]
        e5_root = getattr(args, dataset_id.replace("-", "_") + "_e5_root")
        e5_manifest = json.loads((e5_root / "manifest.json").read_text(
            encoding="utf-8"))
        document_ids = parent.read_ids(parent.output_entry(
            e5_root, e5_manifest, "evaluation_document_ids"))
        query_ids = parent.read_ids(parent.output_entry(
            e5_root, e5_manifest, "evaluation_query_ids"))
        qrels = parent.read_qrels(parent.output_entry(
            e5_root, e5_manifest, "evaluation_qrels"))
        e5_queries = numpy.memmap(parent.output_entry(e5_root, e5_manifest,
            "evaluation_query_vectors"), mode="r", dtype="<f4",
            shape=(len(query_ids), 384))
        source_fp32 = next(row for row in dataset["representations"]
                           if row["id"] == "fp32")
        documents = numpy.memmap(parent.resolve(args.final_materialization_root,
            dataset_id, source_fp32["encoded"]), mode="r", dtype="<f4",
            shape=tuple(source_fp32["encoded"]["shape"]))
        final_queries = parent.read_array(args.final_materialization_root,
            dataset_id, dataset["query_vectors"], "<f4")
        mapped_queries = parent.query_positions(final_queries, e5_queries,
            query_ids, dataset_id,
            parent_contract["query_vector_collision_resolution"])
        id_ranks = parent.read_array(args.final_materialization_root,
            dataset_id, dataset["document_id_rank"], "<u4")
        for route in dataset["routes"]:
            seed = int(route["seed"])
            pools = parent.read_array(args.final_materialization_root,
                dataset_id, route["pool"], "<u4", str(seed))
            for local, source_query in enumerate(mapped_queries):
                pool = numpy.asarray(pools[local], dtype=numpy.uint32)
                values = numpy.asarray(documents[pool], dtype=numpy.float32)
                query_id = query_ids[source_query]
                queries[cursor] = final_queries[local]
                positions_out[cursor] = pool
                ranks_out[cursor] = id_ranks[pool]
                grades_out[cursor] = numpy.asarray([
                    qrels.get(query_id, {}).get(document_ids[int(position)], 0)
                    for position in pool], dtype=numpy.int32)
                fp32[cursor] = values
                uniform[cursor] = parent.quantize(
                    values, treatment_by_id["int5_uniform"])[2]
                selected[cursor] = parent.quantize(
                    values, treatment_by_id[
                        quality["decision"]["selected_nonlinear_treatment"]])[2]
                cases.append({"dataset": dataset_id, "seed": seed,
                    "partition": "parameter_selection" if local % 2 == 0 else
                                 "heldout_confirmation",
                    "query": local, "query_id": query_id,
                    "ideal_dcg": ideal_dcg(qrels.get(query_id, {}))})
                cursor += 1
        del documents, e5_queries
    require(cursor == case_count, "final nonlinear INT5 native case count differs")
    for value in (queries, positions_out, ranks_out, grades_out, fp32,
                  uniform, selected):
        value.flush()
    del queries, positions_out, ranks_out, grades_out, fp32, uniform, selected
    manifest = {"schema_version": 1,
        "family": "neuroute_final_nonlinear_int5_native_input",
        "source_quality_result_sha256": parent.sha256(args.quality_result),
        "source_materialization_sha256": parent.sha256(
            args.final_materialization_root / "manifest.json"),
        "dimensions": 384, "pool_size": 64, "result_k": 10,
        "cases": cases,
        "queries": payload(root / "queries.f32", (case_count, 384), "<f4", root),
        "positions": payload(root / "positions.u32", (case_count, 64), "<u4", root),
        "ranks": payload(root / "ranks.u32", (case_count, 64), "<u4", root),
        "grades": payload(root / "grades.i32", (case_count, 64), "<i4", root),
        "treatments": [
            {"id": "fp32", "reconstructed": payload(root / "fp32.f32",
                (case_count, 64, 384), "<f4", root)},
            {"id": "int5_uniform", "reconstructed": payload(
                root / "int5-uniform.f32", (case_count, 64, 384), "<f4", root)},
            {"id": quality["decision"]["selected_nonlinear_treatment"],
             "reconstructed": payload(root / "int5-power-075.f32",
                (case_count, 64, 384), "<f4", root)}]}
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(parent.canonical(manifest))
    return manifest_path


def treatment_summary(rows: list[dict[str, Any]], treatment: str,
                      quality: dict[str, float]) -> dict[str, Any]:
    selected = [row for row in rows if row["partition"] ==
                "heldout_confirmation"]
    by_key = {(row["dataset"], row["seed"], row["query"], row["treatment"]):
              float(row["ndcg_at_10"]) for row in selected}
    datasets = ("de-25k", "fr-25k", "ja-25k", "de-1m")
    losses, regressions = [], []
    for dataset in datasets:
        keys = [(row["dataset"], row["seed"], row["query"])
                for row in selected if row["dataset"] == dataset and
                row["treatment"] == treatment]
        losses.append(float(numpy.mean([by_key[(*key, "fp32")] -
            by_key[(*key, treatment)] for key in keys])))
        regressions.append(float(numpy.mean([by_key[(*key, "int5_uniform")] -
            by_key[(*key, treatment)] for key in keys])))
    mean_loss = float(numpy.mean(losses))
    mean_regression = float(numpy.mean(regressions))
    passed = (mean_loss <= quality[
        "maximum_cross_dataset_mean_ndcg_loss_vs_fp32"] and
        max(losses) <= quality["maximum_per_dataset_ndcg_loss_vs_fp32"] and
        mean_regression <= quality[
            "maximum_confirmation_mean_ndcg_regression_vs_uniform"] and
        max(regressions) <= quality[
            "maximum_confirmation_per_dataset_regression_vs_uniform"])
    return {"treatment": treatment, "dataset_losses_vs_fp32": losses,
        "cross_dataset_mean_loss_vs_fp32": mean_loss,
        "dataset_regressions_vs_uniform": regressions,
        "cross_dataset_mean_regression_vs_uniform": mean_regression,
        "passes_heldout_quality": passed}


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    quality = json.loads(args.quality_result.read_text(encoding="utf-8"))
    selected = quality["decision"]["selected_nonlinear_treatment"]
    require(selected == contract["expected_selected_nonlinear"] and
            quality["decision"]["selected_nonlinear_passes_heldout_quality"]
            is False,
            "final nonlinear INT5 parent decision differs")
    actual_activation = {"quality_result_sha256": parent.sha256(
        args.quality_result), "final_materialization_sha256": parent.sha256(
            args.final_materialization_root / "manifest.json")}
    require(actual_activation == contract["activation"],
            "final nonlinear INT5 native activation differs")
    manifest = materialize(args, contract, quality)
    native_report = args.work_root / "native-report.json"
    completed = subprocess.run([str(args.native_executable),
        "--nonlinear-int5-sensitivity", str(manifest), str(native_report)],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            "final nonlinear INT5 native replay failed: " +
            completed.stderr.strip())
    report = json.loads(native_report.read_text(encoding="utf-8"))
    require(report["family"] ==
            "neuroute_final_nonlinear_int5_native_result" and
            report["input_manifest_sha256"] == parent.sha256(manifest),
            "final nonlinear INT5 native report differs")
    summary = treatment_summary(report["rows"], selected,
                                contract["quality"])
    python_rows = {(row["dataset"], row["seed"], row["partition"],
                    row["query"], row["treatment"]): row["ranked_sha256"]
                   for row in quality["rows"] if row["treatment"] in
                   ("int5_uniform", selected)}
    compared = [row for row in report["rows"] if row["treatment"] in
                ("int5_uniform", selected)]
    agreements = sum(python_rows[(row["dataset"], row["seed"],
        row["partition"], row["query"], row["treatment"])] ==
        row["ranked_sha256"] for row in compared)
    output = {"schema_version": 1,
        "family": "neuroute_final_nonlinear_int5_native_sensitivity_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": parent.sha256(args.contract),
        "activation": actual_activation,
        "native_executable_sha256": parent.sha256(args.native_executable),
        "native_input_manifest_sha256": parent.sha256(manifest),
        "native_report_sha256": parent.sha256(native_report),
        "native_evaluator_source_manifest_sha256": report[
            "evaluator_source_manifest_sha256"],
        "cases": report["case_count"], "summary": summary,
        "python_native_ranked_agreements": agreements,
        "python_native_ranked_comparisons": len(compared),
        "decision": {
            "native_selected_passes_heldout_quality": summary[
                "passes_heldout_quality"],
            "native_reduction_confirms_python_rejection": not summary[
                "passes_heldout_quality"],
            "nonlinear_replacement_licensed": False,
            "retained_final_codec": "int5_uniform_simdcomp_bp128"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(parent.canonical(output))


def self_test() -> None:
    contract = load_contract(THIS /
        "neuroute-final-nonlinear-int5-native-sensitivity.example.json")
    rows = []
    for dataset in ("de-25k", "fr-25k", "ja-25k", "de-1m"):
        for treatment, value in (("fp32", 1.0), ("int5_uniform", .999),
                                 ("int5_power_075", .998)):
            rows.append({"dataset": dataset, "seed": 1, "query": 1,
                "partition": "heldout_confirmation", "treatment": treatment,
                "ndcg_at_10": value})
    summary = treatment_summary(rows, "int5_power_075", contract["quality"])
    require(summary["passes_heldout_quality"] is False,
            "final nonlinear INT5 native sensitivity self-test differs")
    print("NeuRoute final nonlinear INT5 native sensitivity self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-final-nonlinear-int5-native-sensitivity.example.json")
    parser.add_argument("--parent-contract", type=Path, default=THIS /
        "neuroute-final-nonlinear-int5.example.json")
    for name in ("quality-result", "final-materialization-root",
                 "de-25k-e5-root", "fr-25k-e5-root", "ja-25k-e5-root",
                 "de-1m-e5-root", "native-executable", "work-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract", "parent_contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all native sensitivity paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"run-neuroute-final-nonlinear-int5-native-sensitivity: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
