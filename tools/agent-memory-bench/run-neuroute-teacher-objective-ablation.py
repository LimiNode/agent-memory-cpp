#!/usr/bin/env python3
"""Run the frozen K8/top-1024 teacher-objective ablation."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy

THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_teacher_objective_planner",
               "plan-neuroute-teacher-objective-ablation.py")
base = load("neuroute_teacher_objective_parent",
            "run-neuroute-nonlinear-listwise-reranker.py")
scale = base.scale
task = base.task
multi = base.multi
prototype = base.parent
sequential = base.sequential
POPCOUNT = task.POPCOUNT


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-teacher-objective-ablation.py",
        "run-neuroute-teacher-objective-ablation.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace
                        ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any],
                                   list[str], numpy.ndarray]:
    actual = {
        "nonlinear_listwise_result_sha256": sha256(args.nonlinear_result),
        "nonlinear_listwise_evidence_sha256": sha256(args.nonlinear_evidence),
    }
    require(actual == contract["activation"],
            "teacher-objective parent activation bytes differ")
    result = json.loads(args.nonlinear_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.nonlinear_evidence.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_nonlinear_listwise_reranker_result",
            "teacher-objective parent result differs")
    require(evidence.get("family") == "neuroute_nonlinear_listwise_reranker_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual[
                "nonlinear_listwise_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("model_archive_sha_map_replay_passed") is True,
            "teacher-objective parent evidence differs")
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    (_, _, materialization, split, external_ids,
     external_vectors) = base.validate_activation(parent_contract, args)
    return result, materialization, split, external_ids, external_vectors


def frozen_selection(parent_result: dict[str, Any], seed: int,
                     contract: dict[str, Any]) -> dict[str, Any]:
    eligible = set(contract["frozen_selection"]["eligible_variants"])
    rows = [row for row in parent_result["configuration_selected"]
            if row["seed"] == seed and row["variant"] in eligible]
    require(len(rows) == len(eligible),
            "teacher-objective frozen parent selection differs")
    headline = int(contract["evaluation"]["headline_address_budget"])

    def key(row: dict[str, Any]) -> tuple[float, float, str]:
        budget = next(value for value in row["budgets"]
                      if value["address_budget"] == headline)
        return (-budget["actionable_gain_coverage"], budget["candidate_fraction"],
                row["variant"])
    return min(rows, key=key)


def parent_cache(cache_root: Path, seed: int, parent_result: dict[str, Any]
                 ) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    expected = next(row["manifest_sha256"]
                    for row in parent_result["training_cache"]["seed_caches"]
                    if row["seed"] == seed)
    matches = []
    for path in cache_root.glob("seed-*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("identity", {}).get("seed") == seed and hashlib.sha256(
                canonical(manifest)).hexdigest() == expected:
            matches.append((path, manifest))
    require(len(matches) == 1, "teacher-objective parent cache differs")
    path, manifest = matches[0]
    arrays = []
    for name in ("shortlists", "features", "targets"):
        payload = path.parent / manifest["outputs"][name]["path"]
        require(payload.is_file()
                and sha256(payload) == manifest["outputs"][name]["sha256"],
                f"teacher-objective parent cache payload differs: {name}")
        arrays.append(numpy.load(payload, mmap_mode="r"))
    return arrays[0], arrays[1], arrays[2], manifest


def fit_query_projection(data: dict[str, Any], validation_positions: list[int],
                         contract: dict[str, Any]
                         ) -> tuple[numpy.ndarray, dict[str, Any]]:
    settings = contract["query_projection"]
    sample_count = int(settings["fit_documents"])
    positions = numpy.linspace(0, len(data["documents"]) - 1, sample_count,
                               dtype=numpy.int64)
    vectors = numpy.asarray(data["documents"][positions], dtype=numpy.float64)
    codes = numpy.unpackbits(numpy.asarray(data["document_codes"][positions]),
                             axis=1, bitorder="little")
    targets = data["adc_centroids"][numpy.arange(256)[None, :], codes]
    gram = vectors.T @ vectors
    cross = vectors.T @ numpy.asarray(targets, dtype=numpy.float64)
    matrix = numpy.linalg.solve(
        gram + float(settings["ridge_alpha"]) * numpy.eye(gram.shape[0]), cross)
    predicted = (numpy.asarray(data["queries"][validation_positions],
                               dtype=numpy.float64) @ matrix)
    actual = numpy.asarray(data["query_projection"][validation_positions],
                           dtype=numpy.float64)
    sign = float(numpy.mean((predicted >= 0.0) == (actual >= 0.0)))
    correlation = float(numpy.corrcoef(predicted.ravel(), actual.ravel())[0, 1])
    require(sign >= settings["minimum_german_sign_agreement"]
            and correlation >= settings["minimum_german_projection_correlation"],
            "teacher-objective derived query projection validation failed")
    metrics = {
        "fit_document_count": sample_count,
        "validation_query_count": len(validation_positions),
        "validation_query_positions_sha256": base.bytes_sha256(numpy.asarray(
            validation_positions, dtype=numpy.int32)),
        "sample_positions_sha256": base.bytes_sha256(positions),
        "matrix_sha256": base.bytes_sha256(matrix),
        "german_sign_agreement": sign,
        "german_projection_correlation": correlation,
        "german_projection_rmse": float(numpy.sqrt(numpy.mean(
            (predicted - actual) ** 2, dtype=numpy.float64))),
    }
    return numpy.asarray(matrix, dtype=numpy.float32), metrics


def query_cascade_inputs(vectors: numpy.ndarray, matrix: numpy.ndarray
                         ) -> tuple[numpy.ndarray, numpy.ndarray]:
    projection = numpy.asarray(vectors, dtype=numpy.float32) @ matrix
    codes = numpy.packbits(projection >= 0.0, axis=1, bitorder="little")
    return numpy.asarray(codes, dtype=numpy.uint8), projection


def cascade_coverage(data: dict[str, Any], query_vector: numpy.ndarray,
                     query_code: numpy.ndarray, query_projection: numpy.ndarray,
                     candidates: numpy.ndarray, target: numpy.ndarray,
                     discounts: numpy.ndarray, contract: dict[str, Any]) -> float:
    if not candidates.size:
        return 0.0
    xor = numpy.bitwise_xor(data["document_codes"][candidates], query_code)
    distances = POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
    local_hamming = scale.select_smallest(
        distances, data["document_ids"][candidates],
        contract["cascade"]["hamming_limit"])
    hamming = candidates[local_hamming]
    bits = numpy.unpackbits(data["document_codes"][hamming], axis=1,
                            bitorder="little")
    table = (query_projection[:, None] - data["adc_centroids"]) ** 2
    adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
    local_adc = scale.select_smallest(
        adc_distances, data["document_ids"][hamming],
        contract["cascade"]["adc_limit"])
    adc = hamming[local_adc]
    return float(discounts[numpy.isin(target, adc)].sum(dtype=numpy.float64)
                 / discounts.sum(dtype=numpy.float64))


def teacher_targets(shortlists: numpy.ndarray, top_positions: numpy.ndarray,
                    addresses: numpy.ndarray, index: dict[str, Any],
                    data: dict[str, Any], query_vectors: numpy.ndarray,
                    query_codes: numpy.ndarray, query_projections: numpy.ndarray,
                    discounts: numpy.ndarray, contract: dict[str, Any]
                    ) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray,
                               dict[str, Any]]:
    shape = shortlists.shape
    independent = numpy.zeros(shape, dtype=numpy.float64)
    conditional = numpy.zeros(shape, dtype=numpy.float64)
    sequences = numpy.full((shape[0], contract["training"][
        "teacher_sequence_max_steps"]), -1, dtype=numpy.int16)
    independent_supervised = conditional_supervised = 0
    for query_index in range(shape[0]):
        positions = {int(address): column for column, address
                     in enumerate(shortlists[query_index].tolist())}
        gains = sequential.target_gains(top_positions[query_index], addresses,
                                        discounts)
        action_addresses = sorted(address for address in gains if address in positions)
        if not action_addresses:
            continue
        for address in action_addresses:
            candidates, accepted, _ = scale.candidate_union(
                [address], index, len(data["document_ids"]))
            require(address in accepted,
                    "teacher-objective independent candidate union differs")
            coverage = cascade_coverage(
                data, query_vectors[query_index], query_codes[query_index],
                query_projections[query_index], candidates, top_positions[query_index],
                discounts, contract)
            independent[query_index, positions[address]] = coverage / max(
                int(index["counts"][address]), 1)
        if independent[query_index].sum(dtype=numpy.float64) > 0.0:
            independent_supervised += 1

        remaining = list(action_addresses)
        selected: list[int] = []
        current = 0.0
        for step in range(sequences.shape[1]):
            options = []
            for address in remaining:
                candidates, accepted, _ = scale.candidate_union(
                    [*selected, address], index, len(data["document_ids"]))
                coverage = cascade_coverage(
                    data, query_vectors[query_index], query_codes[query_index],
                    query_projections[query_index], candidates,
                    top_positions[query_index], discounts, contract)
                delta = coverage - current
                options.append((delta / max(int(index["counts"][address]), 1),
                                coverage, -int(index["counts"][address]), -address,
                                address))
            if not options:
                break
            chosen = max(options)
            if chosen[0] <= 0.0:
                break
            address = int(chosen[4])
            column = positions[address]
            sequences[query_index, step] = column
            conditional[query_index, column] = chosen[0]
            selected.append(address)
            remaining.remove(address)
            current = float(chosen[1])
        if numpy.any(sequences[query_index] >= 0):
            conditional_supervised += 1
    metrics = {
        "query_count": shape[0],
        "independent_supervised_query_count": independent_supervised,
        "independent_zero_target_query_count": shape[0] - independent_supervised,
        "conditional_supervised_query_count": conditional_supervised,
        "conditional_zero_target_query_count": shape[0] - conditional_supervised,
        "independent_targets_sha256": base.bytes_sha256(independent),
        "conditional_targets_sha256": base.bytes_sha256(conditional),
        "conditional_sequences_sha256": base.bytes_sha256(sequences),
    }
    return independent, conditional, sequences, metrics


def budget(row: dict[str, Any], value: int) -> dict[str, Any]:
    return next(item for item in row["budgets"] if item["address_budget"] == value)


def result_decision(rows: list[dict[str, Any]], contract: dict[str, Any]
                    ) -> dict[str, Any]:
    rule = contract["decision"]
    comparisons = []
    success = {}
    for treatment in contract["teachers"]["variants"]:
        current_rows = []
        for seed in contract["route"]["seeds"]:
            control = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == "prototype_order")
            learned = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == treatment)
            teacher = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == "privileged_teacher")
            p = budget(control, 256)
            l = budget(learned, 256)
            t = budget(teacher, 256)
            closure = ((l["actionable_gain_coverage"] - p["actionable_gain_coverage"])
                       / max(t["actionable_gain_coverage"]
                             - p["actionable_gain_coverage"], 1.0e-30))
            row = {
                "seed": seed, "treatment": treatment,
                "prototype_actionable_gain_at_256": p["actionable_gain_coverage"],
                "learned_actionable_gain_at_256": l["actionable_gain_coverage"],
                "teacher_actionable_gain_at_256": t["actionable_gain_coverage"],
                "teacher_gap_closure": closure,
                "prototype_candidate_fraction_at_256": p["candidate_fraction"],
                "learned_candidate_fraction_at_256": l["candidate_fraction"],
                "candidate_fraction_ratio": l["candidate_fraction"]
                    / max(p["candidate_fraction"], 1.0e-30),
            }
            current_rows.append(row)
            comparisons.append(row)
        direct = all(row["learned_actionable_gain_at_256"]
                     >= rule["minimum_actionable_gain"]
                     and row["learned_candidate_fraction_at_256"]
                     <= rule["maximum_candidate_fraction"] for row in current_rows)
        progress = all(row["teacher_gap_closure"]
                       >= rule["minimum_prototype_to_teacher_gap_closed"]
                       and row["candidate_fraction_ratio"]
                       <= rule["maximum_candidate_fraction_ratio_vs_prototype_order"]
                       for row in current_rows)
        success[treatment] = {"direct_gate_passed": direct,
                              "progress_gate_passed": progress}
    return {
        "de_1m_internal_comparisons": comparisons,
        "treatment_success": success,
        "teacher_objective_sufficient": any(
            value["direct_gate_passed"] for value in success.values()),
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "internal_evaluation_opened_after_parent_selection_was_frozen": True,
        "external_training_qrels_used": False,
    }


def evaluate(contract: dict[str, Any], parent_result: dict[str, Any],
             materialization: dict[str, Any], split: dict[str, Any],
             external_ids: list[str], external_vectors: numpy.ndarray,
             args: argparse.Namespace) -> tuple[list[dict[str, Any]],
                                                  list[dict[str, Any]],
                                                  dict[str, Any], dict[str, Any]]:
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    scale_config = next(row for row in base.parent.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root, args.de_1m_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    train_positions = [by_id[value] for value in split["training_query_ids"]]
    configuration_positions = [by_id[value]
                               for value in split["configuration_selection_query_ids"]]
    internal_positions = [by_id[value]
                          for value in split["internal_evaluation_query_ids"]]
    pool_ids = list(split["training_query_ids"]) + external_ids
    pool_vectors = numpy.concatenate((
        numpy.asarray(data["queries"][train_positions], dtype=numpy.float32),
        external_vectors), axis=0)
    top_positions = base.exact_top_k_batched(
        data["documents"], data["document_ids"], pool_vectors,
        parent_contract["teacher"]["top_k"],
        parent_contract["training"]["exact_query_batch_size"])
    projection_matrix, projection_metrics = fit_query_projection(
        data, [*train_positions, *configuration_positions], contract)
    pool_codes, pool_projections = query_cascade_inputs(pool_vectors,
                                                        projection_matrix)
    german_training_count = len(train_positions)
    pool_codes[:german_training_count] = data["query_codes"][train_positions]
    pool_projections[:german_training_count] = data[
        "query_projection"][train_positions]
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    protocol = base.evaluation_contract({
        **contract,
        "training": {**parent_contract["training"], **contract["training"]},
    })
    models = []
    rows = []
    teacher_cache = []
    for seed in contract["route"]["seeds"]:
        selected = frozen_selection(parent_result, seed, contract)
        training_count = int(selected["training_query_count"])
        variant = selected["variant"]
        training_shortlists, training_features, static_targets, cache_manifest = (
            parent_cache(args.parent_cache_root, seed, parent_result))
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        independent, conditional, sequences, teacher_metrics = teacher_targets(
            training_shortlists[:training_count], top_positions[:training_count],
            addresses, index, data, pool_vectors[:training_count],
            pool_codes[:training_count], pool_projections[:training_count], discounts,
            contract)
        teacher_cache.append({"seed": seed, "training_query_count": training_count,
                              **teacher_metrics})
        targets_by_teacher = {
            "static_gain_density": static_targets[:training_count],
            "cascade_independent_density": independent,
            "conditional_marginal_sequence_distillation": conditional,
        }
        for teacher_index, teacher in enumerate(contract["teachers"]["variants"]):
            model_seed = seed ^ ((teacher_index + 1) * 0x13579BD) ^ training_count
            arrays, mean, deviation, metrics = base.train_neural_model(
                variant, pool_vectors[:training_count],
                training_features[:training_count],
                targets_by_teacher[teacher], model_seed, protocol)
            if teacher == "conditional_marginal_sequence_distillation":
                metrics["loss"] = "querywise_sequence_weighted_listnet"
                metrics["teacher_sequence_sha256"] = base.bytes_sha256(sequences)
            metadata = {
                "schema_version": 1,
                "family": "neuroute_teacher_objective_model",
                "seed": seed, "teacher": teacher, "variant": variant,
                "training_query_count": training_count,
                "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                    pool_ids[:training_count], dtype=object)),
                "parent_cache_manifest_sha256": hashlib.sha256(
                    canonical(cache_manifest)).hexdigest(),
                "contract_sha256": sha256(args.contract),
                "document_addresses_sha256": route[
                    "document_addresses"]["sha256"],
                "model_seed": model_seed,
                "external_qrels_used": False,
                "training": metrics,
            }
            path = args.model_root / f"model-{teacher}-{seed}.npz"
            digest = base.save_model(path, arrays, mean, deviation, metadata)
            models.append({"seed": seed, "teacher": teacher, "variant": variant,
                           "training_query_count": training_count,
                           "file": path.name, "sha256": digest,
                           "metadata": metadata})
        del independent, conditional, sequences, training_shortlists
        del training_features, static_targets, addresses, index
        gc.collect()

    # All objective models are now frozen. Only now may internal vectors and qrels
    # be opened for the single predeclared comparison.
    internal_oracle, _ = scale.exact_oracle(
        data, internal_positions, contract["cascade"]["oracle_k"])
    internal_queries = numpy.asarray(data["queries"][internal_positions],
                                     dtype=numpy.float32)
    for seed in contract["route"]["seeds"]:
        selected = frozen_selection(parent_result, seed, contract)
        training_count = int(selected["training_query_count"])
        variant = selected["variant"]
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index,
            contract["prototype_shortlist"]["requested_prototypes_per_address"])
        internal_shortlists, internal_features = base.prepare_query_features(
            internal_queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]),
            contract["prototype_shortlist"]["address_shortlist"],
            parent_contract["training"]["feature_query_batch_size"])
        internal_targets = prototype.density_targets(
            internal_shortlists, internal_oracle, internal_positions,
            addresses, index["counts"], discounts)
        orders = {
            "prototype_order": [row.copy() for row in internal_shortlists],
            "privileged_teacher": [prototype.ordered(
                internal_targets[row], internal_shortlists[row], index["counts"])
                for row in range(len(internal_shortlists))],
        }
        for teacher in contract["teachers"]["variants"]:
            artifact = next(row for row in models if row["seed"] == seed
                            and row["teacher"] == teacher)
            arrays, mean, deviation, metadata = base.read_model(
                args.model_root / artifact["file"])
            require(metadata["seed"] == seed and metadata["teacher"] == teacher
                    and metadata["variant"] == variant,
                    "teacher-objective selected model metadata differs")
            scores = base.numpy_model_scores(
                variant, internal_queries, internal_features, arrays, mean, deviation)
            orders[teacher] = [prototype.ordered(
                scores[row], internal_shortlists[row])
                for row in range(len(internal_shortlists))]
        for treatment in ["prototype_order", *contract["teachers"]["variants"],
                          "privileged_teacher"]:
            summary = base.summarize_orders(
                treatment, orders[treatment], internal_shortlists, addresses, index,
                data, internal_positions, internal_oracle, discounts, protocol)
            rows.append({"dataset": "de-1m", "seed": seed,
                         "frozen_variant": variant,
                         "frozen_training_query_count": training_count,
                         **summary})
        del addresses, index, occupied, prototypes, effective, members
        del internal_shortlists, internal_features, internal_targets
        gc.collect()
    projection_metrics["external_projection_source"] = (
        "document_code_adc_centroid_regression")
    projection_metrics["german_training_projection_source"] = "authoritative_frozen"
    projection_metrics["external_qrels_used"] = False
    return models, rows, {
        "teacher_cache": teacher_cache,
        "projection": projection_metrics,
        "top_positions_sha256": base.bytes_sha256(top_positions),
        "combined_training_query_ids_sha256": scale.hash_ids(
            numpy.asarray(pool_ids, dtype=object)),
    }, result_decision(rows, contract)


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    parent_result, materialization, split, external_ids, external_vectors = (
        validate_activation(contract, args))
    models, rows, training, decision = evaluate(
        contract, parent_result, materialization, split, external_ids,
        external_vectors, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_teacher_objective_ablation_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "torch_version": importlib.import_module("torch").__version__,
            "torch_threads": contract["training"]["torch_threads"],
            "device": contract["training"]["device"],
        },
        "matrix": planner.plan(contract),
        "training": training,
        "models": models,
        "internal_rows": rows,
        "decision": decision,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-teacher-objective-ablation.example.json")
    projection = numpy.asarray([[1.0, 0.0], [0.0, -1.0]], dtype=numpy.float32)
    vectors = numpy.asarray([[1.0, 1.0], [-1.0, 1.0]], dtype=numpy.float32)
    codes, values = query_cascade_inputs(vectors, projection)
    require(values.tolist() == [[1.0, -1.0], [-1.0, -1.0]]
            and numpy.unpackbits(codes, axis=1, bitorder="little")[:, :2].tolist()
            == [[1, 0], [0, 0]], "teacher-objective projection self-test differs")
    parent_result = {"configuration_selected": [
        {"seed": 1, "variant": "pointwise_listnet", "training_query_count": 10,
         "budgets": [{"address_budget": 256, "actionable_gain_coverage": .7,
                      "candidate_fraction": .3}]},
        {"seed": 1, "variant": "context_deepsets_listnet",
         "training_query_count": 20,
         "budgets": [{"address_budget": 256, "actionable_gain_coverage": .8,
                      "candidate_fraction": .4}]},
    ]}
    local = {**contract, "route": {**contract["route"], "seeds": [1]}}
    require(frozen_selection(parent_result, 1, local)["training_query_count"] == 20,
            "teacher-objective frozen selection self-test differs")
    document_addresses = numpy.asarray([1] * 5 + [2] * 5, dtype=numpy.uint32)
    index = scale.build_index(document_addresses, 16)
    synthetic_data = {
        "document_ids": numpy.arange(10, dtype=numpy.int64),
        "document_codes": numpy.zeros((10, 32), dtype=numpy.uint8),
        "adc_centroids": numpy.zeros((256, 2), dtype=numpy.float32),
    }
    discounts = 1.0 / numpy.log2(numpy.arange(10, dtype=numpy.float64) + 2.0)
    independent, conditional, sequences, metrics = teacher_targets(
        numpy.asarray([[1, 2, 3]], dtype=numpy.uint32),
        numpy.arange(10, dtype=numpy.int32)[None, :], document_addresses, index,
        synthetic_data, numpy.zeros((1, 2), dtype=numpy.float32),
        numpy.zeros((1, 32), dtype=numpy.uint8),
        numpy.zeros((1, 256), dtype=numpy.float32), discounts, contract)
    require(numpy.count_nonzero(independent) == 2
            and numpy.count_nonzero(conditional) == 2
            and sequences[0, :2].tolist() == [0, 1]
            and metrics["conditional_supervised_query_count"] == 1,
            "teacher-objective conditional target self-test differs")
    print("NeuRoute teacher-objective ablation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-teacher-objective-ablation.example.json")
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--prototype-gain-density-result", type=Path)
    parser.add_argument("--prototype-gain-density-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all teacher-objective paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-teacher-objective-ablation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
