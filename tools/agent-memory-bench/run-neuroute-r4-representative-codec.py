#!/usr/bin/env python3
"""Evaluate physical representative codecs with the frozen FF32 K32 scorer."""
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


planner = load("neuroute_r4_rep_codec_planner",
               "plan-neuroute-r4-representative-codec.py")
coverage = load("neuroute_r4_rep_codec_coverage",
                "run-neuroute-r4-coverage-saturation.py")
fine = coverage.fine
base = coverage.base
scale = coverage.scale
task = coverage.task
multi = coverage.multi
prototype = coverage.prototype
ambiguity = coverage.ambiguity


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


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-r4-representative-codec.py",
             "materialize-neuroute-r4-representative-codec.py",
             "run-neuroute-r4-representative-codec.py",
             "neuroute_r4_representative_codec.cpp")
    return {name: sha256(THIS / name) for name in names}


def read_raw(root: Path, row: dict[str, Any]) -> numpy.ndarray:
    path = root / row["file"]
    require(path.is_file() and sha256(path) == row["sha256"],
            f"R4 representative-codec payload differs: {row['role']}")
    return numpy.memmap(path, mode="r", dtype=row["dtype"],
                        shape=tuple(row["shape"]))


def decode_store(codec: dict[str, Any], root: Path, rows: int,
                 scratch: Path, native: Path) -> tuple[numpy.ndarray, Path | None]:
    path = root / codec["file"]
    require(path.is_file() and sha256(path) == codec["sha256"],
            f"R4 representative-codec physical bytes differ: {codec['id']}")
    if codec["id"] == "fp32":
        return numpy.memmap(path, mode="r", dtype="<f4", shape=(rows, 384)), None
    output = scratch / f"decoded-{root.name}-{codec['id']}.f32"
    if codec["id"] == "fp16":
        source = numpy.memmap(path, mode="r", dtype="<f2", shape=(rows, 384))
        target = numpy.memmap(output, mode="w+", dtype="<f4", shape=(rows, 384))
        for start in range(0, rows, 16384):
            target[start:start + 16384] = source[start:start + 16384]
        target.flush()
        del target, source
    elif codec["id"] == "int8":
        source = numpy.memmap(path, mode="r", dtype=numpy.uint8, shape=(rows, 388))
        target = numpy.memmap(output, mode="w+", dtype="<f4", shape=(rows, 384))
        for start in range(0, rows, 16384):
            stop = min(rows, start + 16384)
            block = numpy.asarray(source[start:stop])
            scales = block[:, 384:].copy().reshape(-1).view("<f4")
            target[start:stop] = ((block[:, :384].astype(numpy.int16) - 127)
                                  * scales[:, None])
        target.flush()
        del target, source
    else:
        completed = subprocess.run([
            str(native), "--unpack", str(codec["bits"]), str(path), str(rows),
            str(output)], check=False, capture_output=True, text=True)
        require(completed.returncode == 0,
                f"R4 representative-codec native decode failed: {completed.stderr}")
    require(output.stat().st_size == rows * 384 * 4,
            "R4 representative-codec decoded byte count differs")
    return numpy.memmap(output, mode="r", dtype="<f4", shape=(rows, 384)), output


def maximums(queries: numpy.ndarray, shortlists: numpy.ndarray,
             vectors: numpy.ndarray, occupied: numpy.ndarray,
             offsets: numpy.ndarray, counts: numpy.ndarray,
             documents: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray,
                                                 numpy.ndarray]:
    lookup = fine.address_lookup(occupied)
    top1 = numpy.empty(shortlists.shape, dtype=numpy.float32)
    top2 = numpy.empty(shortlists.shape, dtype=numpy.float32)
    winners = numpy.empty(shortlists.shape, dtype=numpy.int32)
    slots = numpy.arange(32, dtype=numpy.int64)
    for query_index in range(len(queries)):
        rows = lookup[numpy.asarray(shortlists[query_index], dtype=numpy.uint32)]
        require(numpy.all(rows >= 0),
                "R4 representative-codec shortlist contains empty address")
        valid = slots[None, :] < counts[rows, None]
        physical = offsets[rows, None].astype(numpy.int64) + slots[None, :]
        safe = numpy.where(valid, physical, 0)
        values = numpy.asarray(vectors[safe], dtype=numpy.float32)
        scores = numpy.einsum("kpd,d->kp", values,
                              numpy.asarray(queries[query_index], dtype=numpy.float32),
                              dtype=numpy.float32, optimize=True)
        scores[~valid] = -numpy.inf
        order = numpy.argsort(-scores, axis=1, kind="stable")[:, :2]
        top1[query_index] = numpy.take_along_axis(scores, order[:, :1], axis=1)[:, 0]
        second = numpy.take_along_axis(scores, order[:, 1:2], axis=1)[:, 0]
        top2[query_index] = numpy.where(counts[rows] >= 2, second,
                                        top1[query_index])
        winner_physical = numpy.take_along_axis(safe, order[:, :1], axis=1)[:, 0]
        winners[query_index] = documents[winner_physical]
        del values, scores, order, safe, physical, valid
    return top1, top2, winners


def model_order(queries: numpy.ndarray, scalar_features: numpy.ndarray,
                maximum: numpy.ndarray, model_path: Path) -> list[numpy.ndarray]:
    arrays, scalar_mean, scalar_deviation, _ = base.read_model(model_path)
    interactions = numpy.zeros((*maximum.shape, 3, 8), dtype=numpy.float32)
    aggregate = numpy.zeros((*maximum.shape, 3), dtype=numpy.float32)
    aggregate[..., 0] = maximum
    scores = fine.numpy_scores("actual_k32_max", queries, scalar_features,
                               interactions, aggregate, arrays,
                               scalar_mean, scalar_deviation)
    return scores


def order_rows(scores: numpy.ndarray, shortlists: numpy.ndarray) -> list[numpy.ndarray]:
    return [prototype.ordered(scores[row], shortlists[row])
            for row in range(len(shortlists))]


def accepted_prefix(order: numpy.ndarray, row: dict[str, Any], budget: float,
                    role: str) -> set[int]:
    current = next(value for value in row["budgets"]
                   if value["candidate_fraction_budget"] == budget)[role]
    return set(int(value) for value in order[:current["accepted_address_count"]])


def diagnostics(codec: str, maximum: numpy.ndarray, top2: numpy.ndarray,
                winners: numpy.ndarray, orders: list[numpy.ndarray],
                row: dict[str, Any], reference: dict[str, Any],
                contract: dict[str, Any]) -> dict[str, Any]:
    error = numpy.abs(maximum - reference["maximum"])
    margin = reference["maximum"] - reference["top2"]
    agreement = winners == reference["winners"]
    flat_error = error.reshape(-1).astype(numpy.float64)
    exact_positions = numpy.mean(numpy.concatenate([
        current == expected for current, expected in zip(orders, reference["orders"])]))
    top128 = numpy.mean([len(set(current[:128]) & set(expected[:128])) / 128.0
                         for current, expected in zip(orders, reference["orders"])])
    overlap = []
    for budget in contract["frozen_algorithm"]["candidate_fraction_budgets"]:
        for role in ("last_feasible", "first_crossing"):
            values = []
            for query_index, (current_order, expected_order) in enumerate(
                    zip(orders, reference["orders"])):
                current_set = accepted_prefix(current_order, row["queries"][query_index],
                                              budget, role)
                expected_set = accepted_prefix(expected_order,
                                               reference["row"]["queries"][query_index],
                                               budget, role)
                values.append(len(current_set & expected_set) /
                              max(len(current_set | expected_set), 1))
            overlap.append({"candidate_fraction_budget": budget, "role": role,
                            "mean_jaccard": float(numpy.mean(values))})
    conditioned = []
    lower = -numpy.inf
    for upper in [*contract["diagnostics"]["margin_bins"], numpy.inf]:
        selected = (margin > lower) & (margin <= upper)
        conditioned.append({
            "minimum_exclusive": None if not numpy.isfinite(lower) else lower,
            "maximum_inclusive": None if not numpy.isfinite(upper) else upper,
            "pairs": int(numpy.count_nonzero(selected)),
            "argmax_agreement": (float(numpy.mean(agreement[selected]))
                                 if numpy.any(selected) else None),
        })
        lower = upper
    return {
        "codec": codec,
        "absolute_max_score_error": {
            "mean": float(numpy.mean(flat_error)),
            "p50": float(numpy.quantile(flat_error, 0.5)),
            "p95": float(numpy.quantile(flat_error, 0.95)),
            "p99": float(numpy.quantile(flat_error, 0.99)),
            "maximum": float(numpy.max(flat_error)),
        },
        "max_representative_identity_agreement": float(numpy.mean(agreement)),
        "fp32_top1_top2_margin": {
            "mean": float(numpy.mean(margin, dtype=numpy.float64)),
            "p50": float(numpy.quantile(margin, 0.5)),
            "p95": float(numpy.quantile(margin, 0.95)),
            "p99": float(numpy.quantile(margin, 0.99)),
        },
        "argmax_agreement_by_fp32_margin": conditioned,
        "address_order_exact_position_fraction": float(exact_positions),
        "address_order_top128_overlap": float(top128),
        "accepted_address_overlap": overlap,
    }


def quality(rows: list[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    fraction = contract["frozen_algorithm"]["headline_candidate_fraction"]
    result = []
    for representation in [row["id"] for row in contract["representations"]]:
        current = [coverage.headline(row, fraction) for row in rows
                   if row["treatment"] == representation]
        reference = [coverage.headline(row, fraction) for row in rows
                     if row["treatment"] == "fp32"]
        require(len(current) == len(reference) == 3,
                "R4 representative-codec quality matrix differs")
        actionable = [reference[index]["actionable_gain_coverage"]
                      - current[index]["actionable_gain_coverage"]
                      for index in range(3)]
        ndcg = [reference[index]["exact_ndcg_at_10"]
                - current[index]["exact_ndcg_at_10"] for index in range(3)]
        gates = contract["configuration_gates"]
        value = {
            "representation": representation,
            "mean_actionable_loss": float(numpy.mean(actionable)),
            "maximum_every_seed_actionable_loss": float(max(actionable)),
            "mean_ndcg_loss": float(numpy.mean(ndcg)),
            "maximum_every_seed_ndcg_loss": float(max(ndcg)),
            "per_seed_actionable_losses": actionable,
            "per_seed_ndcg_losses": ndcg,
        }
        value["passes_gates"] = bool(
            value["mean_actionable_loss"] <= gates["maximum_mean_actionable_loss"]
            and value["maximum_every_seed_actionable_loss"]
            <= gates["maximum_every_seed_actionable_loss"]
            and value["mean_ndcg_loss"] <= gates["maximum_mean_ndcg_loss"]
            and value["maximum_every_seed_ndcg_loss"]
            <= gates["maximum_every_seed_ndcg_loss"])
        result.append(value)
    return result


def evaluate_partition(name: str, positions: list[int], contract: dict[str, Any],
                       materialization: dict[str, Any], width: dict[str, Any],
                       data: dict[str, Any], args: argparse.Namespace
                       ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    oracle, _ = scale.exact_oracle(data, positions, 10)
    discounts = 1.0 / numpy.log2(numpy.arange(10, dtype=numpy.float64) + 2.0)
    dataset = next(row for row in width["datasets"] if row["id"] == "de-1m")
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    rows = []
    all_diagnostics = []
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    manifest_by_seed = {int(row["seed"]): row for row in materialization["seeds"]}
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    model_rows = {int(row["seed"]): row for row in saturation[
        "frozen_k32_parent_models"]}
    adapter = {
        "evaluation": {"candidate_fraction_budgets": contract[
            "frozen_algorithm"]["candidate_fraction_budgets"]},
        "cascade": {"hamming_limit": 768, "adc_limit": 64,
                    "result_k": 10},
    }
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        queries = numpy.asarray(data["queries"][positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        seed_row = manifest_by_seed[int(seed)]
        seed_root = args.materialization_root / f"seed-{seed}"
        mappings = {row["role"]: row for row in seed_row["mappings"]}
        stored_occupied = numpy.asarray(read_raw(seed_root,
            mappings["occupied_addresses"]), dtype=numpy.uint32)
        offsets = numpy.asarray(read_raw(seed_root, mappings["address_offsets"]),
                                dtype=numpy.uint32)
        counts = numpy.asarray(read_raw(seed_root, mappings["address_counts"]),
                               dtype=numpy.uint8)
        document_positions = numpy.asarray(read_raw(seed_root,
            mappings["representative_document_positions"]), dtype=numpy.int32)
        require(numpy.array_equal(occupied, stored_occupied),
                "R4 representative-codec occupied addresses differ")
        model = model_rows[int(seed)]
        model_path = args.model_root / model["file"]
        require(model_path.is_file() and sha256(model_path) == model["sha256"],
                "R4 representative-codec frozen model differs")
        reference: dict[str, Any] | None = None
        for codec in seed_row["representations"]:
            vectors, temporary = decode_store(
                codec, seed_root, seed_row["representative_count"],
                args.scratch_root, args.native_executable)
            current_maximum, current_top2, winners = maximums(
                queries, shortlists, vectors, stored_occupied, offsets, counts,
                document_positions)
            scores = model_order(queries, scalar_features, current_maximum, model_path)
            orders = order_rows(scores, shortlists)
            value = coverage.treatment_rows(
                codec["id"], orders, shortlists, addresses, index, data, positions,
                oracle, discounts, adapter)
            row = {"partition": name, "dataset": "de-1m", "seed": seed, **value}
            rows.append(row)
            if codec["id"] == "fp32":
                reference = {"maximum": current_maximum.copy(),
                             "top2": current_top2.copy(), "winners": winners.copy(),
                             "orders": orders, "row": row}
            else:
                require(reference is not None,
                        "R4 representative-codec FP32 reference was not evaluated first")
                all_diagnostics.append({"partition": name, "seed": seed,
                    **diagnostics(codec["id"], current_maximum, current_top2,
                                  winners, orders, row, reference, contract)})
            del vectors, current_maximum, current_top2, winners, scores, orders
            if temporary is not None:
                temporary.unlink()
            gc.collect()
        del addresses, index, occupied, prototypes, effective, queries
        del shortlists, scalar_features, offsets, counts, document_positions
        gc.collect()
    return rows, all_diagnostics


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    activation = contract["activation"]
    actual = {
        "coverage_saturation_result_sha256": sha256(args.saturation_result),
        "coverage_saturation_evidence_sha256": sha256(args.saturation_evidence),
        "conditional_set_result_sha256": sha256(args.conditional_result),
        "conditional_set_evidence_sha256": sha256(args.conditional_evidence),
        "width_materialization_sha256": sha256(args.width_materialization_root /
                                                "manifest.json"),
        "de_1m_e5_manifest_sha256": sha256(args.de_1m_e5_root / "manifest.json"),
        "de_1m_input_manifest_sha256": sha256(args.de_1m_input_root / "manifest.json"),
    }
    require(actual == activation, "R4 representative-codec activation differs")
    materialization_path = args.materialization_root / "manifest.json"
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    require(materialization["contract_sha256"] == sha256(args.contract)
            and materialization["saturation_result_sha256"] ==
            actual["coverage_saturation_result_sha256"],
            "R4 representative-codec materialization identity differs")
    width = json.loads((args.width_materialization_root / "manifest.json").read_text(
        encoding="utf-8"))
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    def frozen_query_ids(partition: str) -> list[str]:
        row = next(value for value in saturation[f"{partition}_rows"]
                   if value["seed"] == contract["route"]["seeds"][0]
                   and value["treatment"] == "actual_k32_max")
        result = [value["query_id"] for value in row["queries"]]
        count_key = ("configuration_queries" if partition == "configuration"
                     else "internal_evaluation_queries")
        require(len(result) == contract["query_partitions"][count_key],
                f"R4 representative-codec frozen {partition} query IDs differ")
        return result
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    configuration_positions = [by_id[value] for value in
                               frozen_query_ids("configuration")]
    internal_positions = [by_id[value] for value in
                          frozen_query_ids("internal")]
    configuration_rows, configuration_diagnostics = evaluate_partition(
        "configuration", configuration_positions, contract, materialization,
        width, data, args)
    configuration_quality = quality(configuration_rows, contract)
    bytes_by_id = {row["id"]: row["record_bytes"]
                   for row in contract["representations"]}
    passing = [row for row in configuration_quality if row["passes_gates"]]
    selected = min(passing, key=lambda row: (bytes_by_id[row["representation"]],
                                              row["representation"]))[
                                                  "representation"]
    internal_rows, internal_diagnostics = evaluate_partition(
        "internal", internal_positions, contract, materialization, width, data, args)
    internal_quality = quality(internal_rows, contract)
    selected_internal = next(row for row in internal_quality
                             if row["representation"] == selected)
    output = {
        "schema_version": 1,
        "family": "neuroute_r4_representative_codec_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": actual,
        "materialization_sha256": sha256(materialization_path),
        "source_files_sha256": source_hashes(),
        "matrix": planner.plan(contract),
        "configuration_rows": configuration_rows,
        "configuration_diagnostics": configuration_diagnostics,
        "configuration_selection": {
            "quality": configuration_quality,
            "selected_representation": selected,
            "rule": contract["configuration_gates"],
        },
        "internal_rows": internal_rows,
        "internal_diagnostics": internal_diagnostics,
        "decision": {
            "selected_representation": selected,
            "internal_quality": internal_quality,
            "selected_internal_passes_gates": selected_internal["passes_gates"],
            "weights_frozen_before_configuration": True,
            "internal_opened_after_configuration_selection": True,
            "physical_layout_benchmark_licensed": bool(
                selected != "fp32" and selected_internal["passes_gates"]),
            "production_selection_licensed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-r4-representative-codec.example.json")
    require(planner.plan(contract)["physical_stores"] == 15,
            "R4 representative-codec runner matrix differs")
    values = numpy.asarray([[0.5, 0.4]], dtype=numpy.float32)
    require(float((values[:, 0] - values[:, 1])[0]) > 0,
            "R4 representative-codec margin differs")
    print("NeuRoute R4 representative-codec runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-representative-codec.example.json")
    for name in ("saturation-result", "saturation-evidence", "conditional-result",
                 "conditional-evidence", "model-root",
                 "materialization-root", "width-materialization-root",
                 "parent-cache-root", "de-1m-e5-root", "de-1m-input-root",
                 "native-executable", "scratch-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract", "parent_cache_root"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 representative-codec paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, subprocess.SubprocessError) as error:
        print(f"run-neuroute-r4-representative-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
