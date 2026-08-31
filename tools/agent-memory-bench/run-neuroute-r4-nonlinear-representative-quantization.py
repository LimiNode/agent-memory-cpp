#!/usr/bin/env python3
"""Evaluate nonlinear INT5/6/8 representative quantization."""
from __future__ import annotations
import argparse
import gc
import hashlib
import importlib.util
import json
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


planner = load("neuroute_r4_nonlinear_runner_planner",
               "plan-neuroute-r4-nonlinear-representative-quantization.py")
parent_runner = load("neuroute_r4_nonlinear_parent_runner",
                     "run-neuroute-r4-representative-codec.py")
_PARENT_DECODE = parent_runner.decode_store

_PARENT_ROOT: Path | None = None
_PARENT_BY_SEED: dict[int, dict[str, Any]] = {}


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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode()


def decode_store(codec: dict[str, Any], root: Path, rows: int,
                 scratch: Path, native: Path) -> tuple[numpy.ndarray, Path | None]:
    seed = int(root.name.removeprefix("seed-"))
    if codec["storage"] == "parent":
        require(_PARENT_ROOT is not None, "R4 nonlinear parent root is absent")
        parent_row = _PARENT_BY_SEED[seed]
        parent_codec = next(row for row in parent_row["representations"]
                            if row["id"] == codec["parent_role"])
        return _PARENT_DECODE(parent_codec,
            _PARENT_ROOT / f"seed-{seed}", rows, scratch, native)
    path = root / codec["file"]
    require(path.is_file() and sha256(path) == codec["sha256"],
            f"R4 nonlinear physical bytes differ: {codec['id']}")
    output = scratch / f"decoded-{root.name}-{codec['id']}.f32"
    compander = codec["compander"]
    completed = subprocess.run([str(native), "--unpack-nonlinear",
        str(codec["bits"]), compander["kind"], str(compander["parameter"]),
        str(path), str(rows), str(output)], check=False,
        capture_output=True, text=True)
    require(completed.returncode == 0,
            f"R4 nonlinear native decode failed: {completed.stderr}")
    require(output.stat().st_size == rows * 384 * 4,
            "R4 nonlinear decoded byte count differs")
    return numpy.memmap(output, mode="r", dtype="<f4", shape=(rows, 384)), output


def activation(contract: dict[str, Any], args: argparse.Namespace) -> dict[str, str]:
    return {
        "representative_codec_result_sha256": sha256(args.parent_result),
        "representative_codec_evidence_sha256": sha256(args.parent_evidence),
        "representative_codec_materialization_sha256": sha256(
            args.parent_materialization_root / "manifest.json"),
        "lossless_block_result_sha256": sha256(args.lossless_result),
        "lossless_block_evidence_sha256": sha256(args.lossless_evidence),
        "coverage_saturation_result_sha256": sha256(args.saturation_result),
        "coverage_saturation_evidence_sha256": sha256(args.saturation_evidence),
        "conditional_set_result_sha256": sha256(args.conditional_result),
        "conditional_set_evidence_sha256": sha256(args.conditional_evidence),
        "layout_materialization_sha256": sha256(args.layout_root / "manifest.json"),
        "de_1m_e5_manifest_sha256": sha256(args.de_1m_e5_root / "manifest.json"),
        "de_1m_input_manifest_sha256": sha256(args.de_1m_input_root / "manifest.json"),
    }


def selected_parameters(quality: list[dict[str, Any]],
                        representations: list[dict[str, Any]]) -> dict[str, str]:
    by_id = {row["id"]: row for row in representations}
    result = {}
    for bits in (8, 6, 5):
        rows = [row for row in quality
                if by_id[row["representation"]]["bits"] == bits and
                by_id[row["representation"]]["compander"]["kind"] != "uniform"]
        selected = min(rows, key=lambda row: (
            row["mean_ndcg_loss"], row["maximum_every_seed_ndcg_loss"],
            row["mean_actionable_loss"],
            row["maximum_every_seed_actionable_loss"], row["representation"]))
        result[f"int{bits}"] = selected["representation"]
    return result


def production_selection(quality: list[dict[str, Any]],
                         representations: list[dict[str, Any]],
                         allowed: set[str]) -> str:
    by_id = {row["id"]: row for row in representations}
    passing = [row for row in quality
               if row["passes_gates"] and row["representation"] in allowed]
    require(passing, "R4 nonlinear configuration has no passing treatment")
    return min(passing, key=lambda row: (
        by_id[row["representation"]]["record_bytes"], row["mean_ndcg_loss"],
        row["mean_actionable_loss"], row["representation"]))["representation"]


def evaluate_partition(name: str, positions: list[int], contract: dict[str, Any],
                       materialization: dict[str, Any], data: dict[str, Any],
                       args: argparse.Namespace
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    oracle, _ = parent_runner.scale.exact_oracle(data, positions, 10)
    discounts = 1.0 / numpy.log2(numpy.arange(10, dtype=numpy.float64) + 2.0)
    parent_contract = parent_runner.base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    layout = json.loads((args.layout_root / "manifest.json").read_text(
        encoding="utf-8"))
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    manifest_by_seed = {int(row["seed"]): row for row in materialization["seeds"]}
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    model_rows = {int(row["seed"]): row for row in saturation[
        "frozen_k32_parent_models"]}
    adapter = {"evaluation": {"candidate_fraction_budgets": contract[
        "frozen_algorithm"]["candidate_fraction_budgets"]},
        "cascade": {"hamming_limit": 768, "adc_limit": 64, "result_k": 10}}
    rows = []
    all_diagnostics = []
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        layout_row = layout_by_seed[int(seed)]
        layout_root = args.layout_root / f"seed-{seed}"
        layout_mappings = {row["role"]: row for row in layout_row["mappings"]}
        occupied = numpy.asarray(parent_runner.read_raw(layout_root,
            layout_mappings["occupied_addresses"]), dtype=numpy.uint32)
        document_counts = numpy.asarray(parent_runner.read_raw(layout_root,
            layout_mappings["address_counts"]), dtype=numpy.uint32)
        physical_to_document = numpy.asarray(parent_runner.read_raw(layout_root,
            layout_mappings["physical_to_document"]), dtype=numpy.int32)
        require(len(physical_to_document) == contract["route"]["documents"] and
                numpy.all(physical_to_document >= 0),
                "R4 nonlinear physical document map differs")
        physical_addresses = numpy.repeat(occupied, document_counts)
        addresses = numpy.empty(len(physical_to_document), dtype=numpy.uint32)
        addresses[physical_to_document] = physical_addresses
        index = parent_runner.scale.build_index(addresses, 16)
        rebuilt_occupied, prototypes, effective, _ = (
            parent_runner.multi.build_nested_prototypes(
                data["documents"], addresses, index, 8))
        require(numpy.array_equal(occupied, rebuilt_occupied),
                "R4 nonlinear rebuilt occupied addresses differ")
        queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
        shortlists, scalar_features = parent_runner.base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        seed_row = manifest_by_seed[int(seed)]
        seed_root = args.materialization_root / f"seed-{seed}"
        mappings = {row["role"]: row for row in seed_row["mappings"]}
        stored_occupied = numpy.asarray(parent_runner.read_raw(seed_root,
            mappings["occupied_addresses"]), dtype=numpy.uint32)
        offsets = numpy.asarray(parent_runner.read_raw(seed_root,
            mappings["address_offsets"]), dtype=numpy.uint32)
        counts = numpy.asarray(parent_runner.read_raw(seed_root,
            mappings["address_counts"]), dtype=numpy.uint8)
        document_positions = numpy.asarray(parent_runner.read_raw(seed_root,
            mappings["representative_document_positions"]), dtype=numpy.int32)
        require(numpy.array_equal(occupied, stored_occupied),
                "R4 nonlinear stored occupied addresses differ")
        model = model_rows[int(seed)]
        model_path = args.model_root / model["file"]
        require(model_path.is_file() and sha256(model_path) == model["sha256"],
                "R4 nonlinear frozen model differs")
        reference: dict[str, Any] | None = None
        for codec in seed_row["representations"]:
            vectors, temporary = decode_store(codec, seed_root,
                seed_row["representative_count"], args.scratch_root,
                args.native_executable)
            current_maximum, current_top2, winners = parent_runner.maximums(
                queries, shortlists, vectors, stored_occupied, offsets, counts,
                document_positions)
            scores = parent_runner.model_order(queries, scalar_features,
                                                current_maximum, model_path)
            orders = parent_runner.order_rows(scores, shortlists)
            value = parent_runner.coverage.treatment_rows(
                codec["id"], orders, shortlists, addresses, index, data, positions,
                oracle, discounts, adapter)
            row = {"partition": name, "dataset": "de-1m", "seed": seed, **value}
            rows.append(row)
            if codec["id"] == "fp32":
                reference = {"maximum": current_maximum.copy(),
                             "top2": current_top2.copy(),
                             "winners": winners.copy(), "orders": orders,
                             "row": row}
            else:
                require(reference is not None,
                        "R4 nonlinear FP32 reference was not evaluated first")
                all_diagnostics.append({"partition": name, "seed": seed,
                    **parent_runner.diagnostics(codec["id"], current_maximum,
                        current_top2, winners, orders, row, reference, contract)})
            del vectors, current_maximum, current_top2, winners, scores, orders
            if temporary is not None:
                temporary.unlink()
            gc.collect()
        del addresses, physical_addresses, physical_to_document, index
        del occupied, rebuilt_occupied, prototypes, effective, queries
        del shortlists, scalar_features, offsets, counts, document_positions
        gc.collect()
    return rows, all_diagnostics


def summary(values: list[float]) -> dict[str, float | int]:
    current = numpy.asarray(values, dtype=numpy.float64)
    return {"samples": len(values), "mean": float(numpy.mean(current)),
            "p50": float(numpy.quantile(current, .50)),
            "p95": float(numpy.quantile(current, .95)),
            "p99": float(numpy.quantile(current, .99)),
            "minimum": float(numpy.min(current)),
            "maximum": float(numpy.max(current))}


def payload_path(root: Path, row: dict[str, Any]) -> Path:
    if "external_root" in row:
        root /= row["external_root"]
    return root / row["file"]


def native_benchmarks(contract: dict[str, Any], materialization: dict[str, Any],
                      ids: list[str], args: argparse.Namespace
                      ) -> list[dict[str, Any]]:
    args.native_report_root.mkdir(parents=True, exist_ok=True)
    layout = json.loads((args.layout_root / "manifest.json").read_text(
        encoding="utf-8"))
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    materialized_by_seed = {int(row["seed"]): row
                            for row in materialization["seeds"]}
    samples: dict[str, list[float]] = {value: [] for value in ids}
    representatives: dict[str, list[float]] = {value: [] for value in ids}
    reports = []
    for seed in contract["route"]["seeds"]:
        seed_row = materialized_by_seed[int(seed)]
        seed_root = args.materialization_root / f"seed-{seed}"
        mappings = {row["role"]: row for row in seed_row["mappings"]}
        layout_row = layout_by_seed[int(seed)]
        layout_root = args.layout_root / f"seed-{seed}"
        layout_mappings = {row["role"]: row for row in layout_row["mappings"]}
        for treatment_id in ids:
            codec = next(row for row in seed_row["representations"]
                         if row["id"] == treatment_id)
            if codec["storage"] == "parent":
                store = (args.parent_materialization_root / f"seed-{seed}" /
                         codec["parent_file"])
            else:
                store = seed_root / codec["file"]
            compander = codec["compander"]
            output = args.native_report_root / f"seed-{seed}-{treatment_id}.json"
            completed = subprocess.run([str(args.native_executable), "--benchmark-dot",
                str(codec["bits"]), compander["kind"],
                str(compander["parameter"]), str(store),
                str(seed_row["representative_count"]),
                str(seed_root / mappings["address_offsets"]["file"]),
                str(seed_root / mappings["address_counts"]["file"]),
                str(payload_path(layout_root, layout_mappings["shortlist_rows"])),
                str(payload_path(layout_root, layout_mappings["query_vectors"])),
                str(contract["native_benchmark"]["measured_passes"]), str(output)],
                check=False, capture_output=True, text=True)
            require(completed.returncode == 0,
                    f"R4 nonlinear native benchmark failed: {completed.stderr}")
            report = json.loads(output.read_text(encoding="utf-8"))
            require(report["store_sha256"] == sha256(store),
                    "R4 nonlinear native store identity differs")
            samples[treatment_id].extend(float(row["decode_dot_max_ms"])
                                         for row in report["samples"])
            representatives[treatment_id].extend(
                float(row["representatives_scored"]) for row in report["samples"])
            reports.append({"seed": seed, "treatment": treatment_id,
                            "file": output.name, "sha256": sha256(output),
                            "samples": len(report["samples"])})
    return [{"treatment": value, "decode_dot_max_ms": summary(samples[value]),
             "representatives_scored": summary(representatives[value]),
             "reports": [row for row in reports if row["treatment"] == value]}
            for value in ids]


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-r4-nonlinear-representative-quantization.py",
             "materialize-neuroute-r4-nonlinear-representative-quantization.py",
             "run-neuroute-r4-nonlinear-representative-quantization.py",
             "neuroute_r4_representative_codec.cpp")
    return {name: sha256(THIS / name) for name in names}


def run(args: argparse.Namespace) -> None:
    global _PARENT_ROOT, _PARENT_BY_SEED
    contract = planner.load_contract(args.contract)
    actual = activation(contract, args)
    require(actual == contract["activation"], "R4 nonlinear activation differs")
    materialization_path = args.materialization_root / "manifest.json"
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    require(materialization["contract_sha256"] == sha256(args.contract) and
            materialization["parent_materialization_sha256"] ==
            actual["representative_codec_materialization_sha256"],
            "R4 nonlinear materialization identity differs")
    parent_manifest = json.loads((args.parent_materialization_root /
        "manifest.json").read_text(encoding="utf-8"))
    _PARENT_ROOT = args.parent_materialization_root
    _PARENT_BY_SEED = {int(row["seed"]): row for row in parent_manifest["seeds"]}
    parent_runner.decode_store = decode_store

    scale_config = next(row for row in parent_runner.prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = parent_runner.scale.load_scale(scale_config, args.de_1m_e5_root,
                                           args.de_1m_input_root)
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    def frozen_query_ids(partition: str) -> list[str]:
        row = next(value for value in saturation[f"{partition}_rows"]
                   if value["seed"] == contract["route"]["seeds"][0] and
                   value["treatment"] == "actual_k32_max")
        result = [value["query_id"] for value in row["queries"]]
        expected = contract["query_partitions"][
            "configuration_queries" if partition == "configuration"
            else "internal_evaluation_queries"]
        require(len(result) == expected,
                f"R4 nonlinear frozen {partition} query IDs differ")
        return result
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    configuration_positions = [by_id[value] for value in
                               frozen_query_ids("configuration")]
    internal_positions = [by_id[value] for value in frozen_query_ids("internal")]

    configuration_rows, configuration_diagnostics = evaluate_partition(
        "configuration", configuration_positions, contract, materialization,
        data, args)
    configuration_quality = parent_runner.quality(configuration_rows, contract)
    per_bit = selected_parameters(configuration_quality,
                                  contract["representations"])
    allowed = {"fp32", "int8_uniform", "int6_uniform", "int5_uniform",
               *per_bit.values()}
    selected = production_selection(configuration_quality,
                                    contract["representations"], allowed)

    internal_ids = ["fp32", "int8_uniform", "int6_uniform", "int5_uniform",
                    per_bit["int8"], per_bit["int6"], per_bit["int5"]]
    internal_contract = {**contract, "representations": [row for row in
        contract["representations"] if row["id"] in internal_ids]}
    internal_materialization = {**materialization, "seeds": [
        {**row, "representations": [value for value in row["representations"]
                                    if value["id"] in internal_ids]}
        for row in materialization["seeds"]]}
    internal_rows, internal_diagnostics = evaluate_partition(
        "internal", internal_positions, internal_contract,
        internal_materialization, data, args)
    internal_quality = parent_runner.quality(internal_rows, internal_contract)
    selected_internal = next(row for row in internal_quality
                             if row["representation"] == selected)
    benchmark_ids = ["int8_uniform", "int6_uniform", "int5_uniform",
                     per_bit["int8"], per_bit["int6"], per_bit["int5"]]
    native = native_benchmarks(contract, materialization, benchmark_ids, args)
    output = {"schema_version": 1,
              "family": "neuroute_r4_nonlinear_quantization_result",
              "claim_scope": contract["claim_scope"],
              "contract_sha256": sha256(args.contract), "activation": actual,
              "materialization_sha256": sha256(materialization_path),
              "source_files_sha256": source_hashes(),
              "matrix": planner.plan(contract),
              "configuration_rows": configuration_rows,
              "configuration_diagnostics": configuration_diagnostics,
              "configuration_selection": {"quality": configuration_quality,
                 "selected_nonlinear_per_bit": per_bit,
                 "selected_production_representation": selected,
                 "rule": contract["selection"]},
              "internal_rows": internal_rows,
              "internal_diagnostics": internal_diagnostics,
              "native_decode_dot": native,
              "decision": {"selected_representation": selected,
                 "internal_quality": internal_quality,
                 "selected_internal_passes_gates": selected_internal["passes_gates"],
                 "internal_opened_after_configuration_selection": True,
                 "nonlinear_replaces_uniform_int8": selected != "int8_uniform",
                 "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))
    del data
    gc.collect()


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-r4-nonlinear-representative-quantization.example.json")
    synthetic = []
    for row in contract["representations"]:
        synthetic.append({"representation": row["id"], "passes_gates": True,
            "mean_ndcg_loss": 0.0 if row["compander"]["kind"] == "power" else .001,
            "maximum_every_seed_ndcg_loss": .001,
            "mean_actionable_loss": 0.0,
            "maximum_every_seed_actionable_loss": .001})
    selected = selected_parameters(synthetic, contract["representations"])
    require(all("power" in value for value in selected.values()),
            "R4 nonlinear selection self-test differs")
    print("NeuRoute R4 nonlinear quantization runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-nonlinear-representative-quantization.example.json")
    for name in ("parent-result", "parent-evidence", "parent-materialization-root",
                 "lossless-result", "lossless-evidence", "saturation-result",
                 "saturation-evidence", "conditional-result", "conditional-evidence",
                 "model-root", "materialization-root",
                 "layout-root", "de-1m-e5-root", "de-1m-input-root",
                 "native-executable", "scratch-root", "native-report-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract", "parent_cache_root"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 nonlinear run paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-nonlinear-representative-quantization: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
