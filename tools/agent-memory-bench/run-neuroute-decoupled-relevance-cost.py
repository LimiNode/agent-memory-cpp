#!/usr/bin/env python3
"""Train relevance targets separately from hard candidate-cost policies."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.util
import json
import math
import platform
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


planner = load("neuroute_decoupled_relevance_cost_planner",
               "plan-neuroute-decoupled-relevance-cost.py")
frontier = load("neuroute_decoupled_frontier_parent",
                "run-neuroute-feasible-candidate-frontier.py")
matched = frontier.matched
base = matched.base
prototype = matched.prototype
multi = matched.multi
scale = matched.scale
task = matched.task
sequential = base.sequential
teacher_objective = load("neuroute_decoupled_teacher_projection",
                         "run-neuroute-teacher-objective-ablation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def array_sha256(value: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.ascontiguousarray(value).view(numpy.uint8)).hexdigest()


def source_hashes() -> dict[str, str]:
    names = [
        "plan-neuroute-decoupled-relevance-cost.py",
        "run-neuroute-decoupled-relevance-cost.py",
        "run-neuroute-feasible-candidate-frontier.py",
        "run-neuroute-r3-matched-ladder.py",
        "run-neuroute-r3-document-summary.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
        "run-neuroute-sequential-oracle-diagnostic.py",
        "run-neuroute-teacher-objective-ablation.py",
    ]
    return {name: sha256(THIS / name) for name in names}


def validate_parent(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], list[str],
        numpy.ndarray, dict[str, Any]]:
    actual = {
        "feasible_frontier_result_sha256": sha256(args.feasible_frontier_result),
        "feasible_frontier_evidence_sha256": sha256(args.feasible_frontier_evidence),
    }
    require(actual == contract["activation"],
            f"decoupled relevance activation bytes differ: {actual!r}")
    result = json.loads(args.feasible_frontier_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.feasible_frontier_evidence.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_feasible_candidate_frontier_result"
            and evidence.get("family")
            == "neuroute_feasible_candidate_frontier_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_byte_replay_passed") is True,
            "decoupled relevance frontier parent differs")
    parent_contract = matched.planner.load_contract(
        THIS / "neuroute-r3-matched-ladder.example.json")
    materialization, split, external_ids, external_vectors, summary = (
        matched.validate_activation(parent_contract, args))
    return result, materialization, split, external_ids, external_vectors, summary


def exact_teacher(path: Path, documents: numpy.ndarray,
                  document_ids: numpy.ndarray, queries: numpy.ndarray,
                  top_k: int) -> tuple[numpy.ndarray, dict[str, Any]]:
    manifest_path = path / "manifest.json"
    positions_path = path / "top100.npy"
    identity = {
        "family": "neuroute_decoupled_exact_teacher",
        "query_sha256": array_sha256(queries),
        "document_count": len(documents),
        "dimensions": int(documents.shape[1]),
        "top_k": top_k,
    }
    if manifest_path.is_file() and positions_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("identity") == identity
                and sha256(positions_path) == manifest["top100_sha256"],
                "decoupled exact-teacher cache differs")
        value = numpy.load(positions_path, mmap_mode="r")
        require(value.shape == (len(queries), top_k),
                "decoupled exact-teacher cache shape differs")
        return value, manifest
    path.mkdir(parents=True, exist_ok=True)
    value = base.exact_top_k_batched(
        documents, document_ids, queries, top_k, 4)
    numpy.save(positions_path, value, allow_pickle=False)
    manifest = {"schema_version": 1, "identity": identity,
                "top100_sha256": sha256(positions_path)}
    manifest_path.write_bytes(canonical(manifest))
    return numpy.load(positions_path, mmap_mode="r"), manifest


def adc_indices(data: dict[str, Any], query_code: numpy.ndarray,
                query_projection: numpy.ndarray,
                candidates: numpy.ndarray, cascade: dict[str, Any]) -> numpy.ndarray:
    if not candidates.size:
        return numpy.empty(0, dtype=numpy.int64)
    xor = numpy.bitwise_xor(data["document_codes"][candidates], query_code)
    distances = sequential.POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
    local_hamming = scale.select_smallest(
        distances, data["document_ids"][candidates], cascade["hamming_limit"])
    hamming = candidates[local_hamming]
    bits = numpy.unpackbits(data["document_codes"][hamming], axis=1,
                            bitorder="little")
    table = (query_projection[:, None] - data["adc_centroids"]) ** 2
    adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
    local_adc = scale.select_smallest(
        adc_distances, data["document_ids"][hamming], cascade["adc_limit"])
    return hamming[local_adc]


def target_arrays(shortlists: numpy.ndarray, top100: numpy.ndarray,
                  query_codes: numpy.ndarray, query_projections: numpy.ndarray,
                  addresses: numpy.ndarray,
                  counts: numpy.ndarray, index: dict[str, Any],
                  data: dict[str, Any], discounts: numpy.ndarray,
                  contract: dict[str, Any]) -> dict[str, numpy.ndarray]:
    shape = shortlists.shape
    density = numpy.zeros(shape, dtype=numpy.float32)
    useful = numpy.zeros(shape, dtype=numpy.float32)
    actionable = numpy.zeros(shape, dtype=numpy.float32)
    graded = numpy.zeros(shape, dtype=numpy.float32)
    weights = contract["target_definition"]["graded_weights"]
    top100_discounts = 1.0 / numpy.log2(numpy.arange(100, dtype=numpy.float64) + 2.0)
    for row in range(shape[0]):
        shortlist = numpy.asarray(shortlists[row], dtype=numpy.uint32)
        local = {int(value): offset for offset, value in enumerate(shortlist.tolist())}
        candidates, _, _ = scale.candidate_union(
            shortlist.tolist(), index, len(data["document_ids"]))
        adc = set(adc_indices(
            data, query_codes[row], query_projections[row], candidates,
            contract["cascade"]).tolist())
        for rank, document in enumerate(numpy.asarray(top100[row]).tolist()):
            address = int(addresses[int(document)])
            slot = local.get(address)
            if slot is None:
                continue
            graded[row, slot] += float(weights["top100"] * top100_discounts[rank])
            if rank < 10:
                gain = float(discounts[rank])
                density[row, slot] += gain / max(int(counts[address]), 1)
                graded[row, slot] += float(weights["top10"] * gain)
                if int(document) in adc:
                    useful[row, slot] = 1.0
                    actionable[row, slot] += gain
                    graded[row, slot] += float(weights["cascade"] * gain)
    return {
        "gain_density_listnet": density,
        "cascade_useful_probability": useful,
        "expected_actionable_gain": actionable,
        "graded_top100_top10_cascade": graded,
        "lambda_candidate_boundary": actionable.copy(),
    }


def teacher_cache(root: Path, seed: int, shortlists: numpy.ndarray,
                  top100: numpy.ndarray, addresses: numpy.ndarray,
                  counts: numpy.ndarray, index: dict[str, Any],
                  data: dict[str, Any], query_codes: numpy.ndarray,
                  query_projections: numpy.ndarray, discounts: numpy.ndarray,
                  contract: dict[str, Any]) -> tuple[
                      dict[str, numpy.ndarray], dict[str, Any]]:
    path = root / f"seed-{seed}"
    manifest_path = path / "manifest.json"
    identity = {
        "family": "neuroute_decoupled_training_targets",
        "seed": seed,
        "shortlists_sha256": array_sha256(numpy.asarray(shortlists)),
        "top100_sha256": array_sha256(numpy.asarray(top100)),
        "query_codes_sha256": array_sha256(query_codes),
        "query_projections_sha256": array_sha256(query_projections),
        "document_addresses_sha256": array_sha256(addresses),
        "targets": contract["targets"],
    }
    paths = {name: path / f"{name}.npy" for name in contract["targets"]}
    if manifest_path.is_file() and all(value.is_file() for value in paths.values()):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest.get("identity") == identity,
                "decoupled target-cache identity differs")
        result = {}
        for name, target_path in paths.items():
            require(sha256(target_path) == manifest["outputs"][name]["sha256"],
                    f"decoupled target-cache bytes differ: {name}")
            result[name] = numpy.load(target_path, mmap_mode="r")
        return result, manifest
    path.mkdir(parents=True, exist_ok=True)
    result = target_arrays(shortlists, top100, query_codes, query_projections,
                           addresses, counts, index, data, discounts, contract)
    outputs = {}
    for name, value in result.items():
        numpy.save(paths[name], value, allow_pickle=False)
        outputs[name] = {"path": paths[name].name, "sha256": sha256(paths[name])}
    manifest = {"schema_version": 1, "identity": identity, "outputs": outputs}
    manifest_path.write_bytes(canonical(manifest))
    return {name: numpy.load(path, mmap_mode="r") for name, path in paths.items()}, manifest


def train_model(target_name: str, queries: numpy.ndarray,
                shortlists: numpy.ndarray, scalar_features: numpy.ndarray,
                targets: numpy.ndarray, occupied: numpy.ndarray,
                state: dict[str, numpy.ndarray],
                interactions: dict[str, numpy.ndarray],
                scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
                interaction_norm: dict[str, numpy.ndarray], counts: numpy.ndarray,
                model_seed: int, contract: dict[str, Any],
                parent_contract: dict[str, Any]) -> tuple[
                    dict[str, numpy.ndarray], dict[str, Any]]:
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    training = contract["training"]
    require(torch.__version__.startswith(str(training["torch_version_prefix"])),
            f"decoupled torch version differs: {torch.__version__}")
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(training["torch_threads"]))
    torch.manual_seed(model_seed & 0x7FFFFFFF)
    initialized = matched.initialized_arrays(
        "r3c_residual_shape", parent_contract, model_seed ^ 0x6815D3A7)
    expected_count = matched.parameter_count(initialized)
    parameters = {name: torch.nn.Parameter(torch.from_numpy(value.copy()))
                  for name, value in initialized.items()}
    optimizer = torch.optim.AdamW(
        list(parameters.values()), lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]))
    query_tensor = torch.from_numpy(numpy.asarray(queries, dtype=numpy.float32))
    target_tensor = torch.from_numpy(
        numpy.asarray(targets, dtype=numpy.float32).copy())
    normalizers = matched.fixed_normalizers(state, interaction_norm)
    supervised = numpy.asarray(targets.sum(axis=1, dtype=numpy.float64) > 0.0)
    require(numpy.any(supervised), "decoupled target has no supervision")
    positive = float(numpy.count_nonzero(numpy.asarray(targets) > 0.0))
    negative = float(targets.size - positive)
    pos_weight = negative / max(positive, 1.0)
    losses = []
    batch_size = int(training["batch_queries"])
    for epoch in range(int(training["epochs"])):
        rng = numpy.random.default_rng(model_seed ^ ((epoch + 1) * 0x9E3779B1))
        order = rng.permutation(len(queries))
        total = 0.0
        rows = 0
        for start in range(0, len(order), batch_size):
            selected = order[start:start + batch_size]
            selected = selected[supervised[selected]]
            if not len(selected):
                continue
            positions = torch.from_numpy(selected.astype(numpy.int64))
            current_interactions = {name: numpy.asarray(value[selected],
                                                               dtype=numpy.float32)
                                    for name, value in interactions.items()}
            local = matched.local_numpy(
                "r3c_residual_shape",
                numpy.asarray(shortlists[selected], dtype=numpy.uint32),
                numpy.asarray(scalar_features[selected], dtype=numpy.float32),
                occupied, state, current_interactions, scalar_mean,
                scalar_deviation, normalizers)
            optimizer.zero_grad(set_to_none=True)
            scores = matched.score_torch(
                query_tensor[positions], torch.from_numpy(local), parameters,
                float(training["score_scale"]))
            target = target_tensor[positions]
            if target_name == "cascade_useful_probability":
                loss = functional.binary_cross_entropy_with_logits(
                    scores, target,
                    pos_weight=torch.tensor(pos_weight, dtype=torch.float32))
            elif target_name == "lambda_candidate_boundary":
                terms = []
                for local_row, original in enumerate(selected.tolist()):
                    score = scores[local_row]
                    gain = target[local_row]
                    positive_indices = torch.nonzero(gain > 0.0).flatten()
                    if not len(positive_indices):
                        continue
                    address_row = numpy.asarray(shortlists[original], dtype=numpy.uint32)
                    cost = counts[address_row]
                    ranked = numpy.lexsort((address_row,
                                            -score.detach().numpy()))
                    cumulative = numpy.cumsum(cost[ranked], dtype=numpy.int64)
                    boundary_index = int(numpy.searchsorted(cumulative, 5000,
                                                            side="right"))
                    negative_pool = ranked[max(0, boundary_index - 16):
                                           min(len(ranked), boundary_index + 16)]
                    negative_pool = [value for value in negative_pool
                                     if float(gain[int(value)]) == 0.0]
                    if not negative_pool:
                        continue
                    negatives = torch.tensor(negative_pool, dtype=torch.int64)
                    difference = (score[positive_indices, None]
                                  - score[negatives][None, :])
                    weights = gain[positive_indices, None]
                    terms.append((functional.softplus(-difference) * weights).mean())
                loss = torch.stack(terms).mean() if terms else scores.sum() * 0.0
            else:
                normalized = target / target.sum(dim=1, keepdim=True)
                loss = -(normalized * functional.log_softmax(scores, dim=1)).sum(
                    dim=1).mean()
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * len(selected)
            rows += len(selected)
        losses.append(total / max(rows, 1))
    arrays = {name: value.detach().numpy().astype(numpy.float32)
              for name, value in parameters.items()}
    arrays.update(normalizers)
    return arrays, {
        "objective": target_name,
        "epoch_losses": losses,
        "final_loss": losses[-1],
        "parameter_count": expected_count,
        "supervised_query_count": int(numpy.count_nonzero(supervised)),
        "zero_target_query_count": int(numpy.count_nonzero(~supervised)),
        "posting_size_importance_weighted_bce": False,
        "torch_version": torch.__version__,
        "external_qrels_used": False,
    }


def calibrate(scores: numpy.ndarray, targets: dict[str, numpy.ndarray]
              ) -> dict[str, Any]:
    flat_scores = scores.reshape(-1).astype(numpy.float64)
    expected = numpy.asarray(targets["expected_actionable_gain"]).reshape(-1)
    useful = numpy.asarray(targets["cascade_useful_probability"]).reshape(-1)
    mean = float(flat_scores.mean())
    deviation = float(max(flat_scores.std(), 1.0e-6))
    z = (flat_scores - mean) / deviation
    design = numpy.stack((z, numpy.ones_like(z)), axis=1)
    gram = design.T @ design
    gram.flat[::3] += 1.0e-6
    coefficient = numpy.linalg.solve(gram, design.T @ expected)
    prevalence = min(1.0 - 1.0e-9, max(1.0e-9, float(useful.mean())))
    bias = math.log(prevalence / (1.0 - prevalence))
    temperatures = [0.5, 1.0, 2.0, 4.0]
    briers = []
    for temperature in temperatures:
        probability = 1.0 / (1.0 + numpy.exp(
            -numpy.clip(z / temperature + bias, -30.0, 30.0)))
        briers.append(float(numpy.mean((probability - useful) ** 2,
                                      dtype=numpy.float64)))
    temperature = temperatures[int(numpy.argmin(briers))]
    return {
        "score_mean": mean,
        "score_deviation": deviation,
        "gain_slope": float(coefficient[0]),
        "gain_intercept": float(coefficient[1]),
        "useful_logit_bias": bias,
        "useful_temperature": temperature,
        "configuration_brier": min(briers),
    }


def calibrated(scores: numpy.ndarray, calibration: dict[str, Any]
               ) -> tuple[numpy.ndarray, numpy.ndarray]:
    z = ((scores.astype(numpy.float64) - calibration["score_mean"])
         / calibration["score_deviation"])
    gain = numpy.maximum(0.0, calibration["gain_slope"] * z
                         + calibration["gain_intercept"])
    logit = z / calibration["useful_temperature"] + calibration[
        "useful_logit_bias"]
    return gain, logit


def policy_candidates(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"policy": "predicted_gain", "lambda": None},
        {"policy": "predicted_gain_per_cost", "lambda": None},
    ]
    for name in ["predicted_gain_minus_lambda_cost",
                 "useful_logit_minus_lambda_log_cost"]:
        rows.extend({"policy": name, "lambda": value}
                    for value in contract["policies"]["lambda_grid"])
    return rows


def policy_scores(gain: numpy.ndarray, logit: numpy.ndarray,
                  shortlists: numpy.ndarray, counts: numpy.ndarray,
                  policy: dict[str, Any]) -> numpy.ndarray:
    costs = counts[numpy.asarray(shortlists, dtype=numpy.uint32)].astype(numpy.float64)
    normalized = costs / numpy.maximum(costs.mean(axis=1, keepdims=True), 1.0)
    if policy["policy"] == "predicted_gain":
        return gain
    if policy["policy"] == "predicted_gain_per_cost":
        return gain / numpy.maximum(costs, 1.0)
    if policy["policy"] == "predicted_gain_minus_lambda_cost":
        return gain - float(policy["lambda"]) * normalized
    log_cost = numpy.log1p(costs)
    log_cost /= numpy.maximum(log_cost.mean(axis=1, keepdims=True), 1.0e-30)
    return logit - float(policy["lambda"]) * log_cost


def hard_budget_order(scores: numpy.ndarray, addresses: numpy.ndarray,
                      counts: numpy.ndarray, maximum: int) -> numpy.ndarray:
    ranked = numpy.lexsort((addresses, -scores))
    selected = []
    mass = 0
    for offset in ranked.tolist():
        address = int(addresses[offset])
        cost = int(counts[address])
        if mass + cost > maximum:
            continue
        selected.append(address)
        mass += cost
    return numpy.asarray(selected, dtype=numpy.uint32)


def evaluate_policy(name: str, policy: dict[str, Any], score_rows: numpy.ndarray,
                    shortlists: numpy.ndarray, addresses: numpy.ndarray,
                    index: dict[str, Any], data: dict[str, Any],
                    positions: list[int], top100: numpy.ndarray,
                    discounts: numpy.ndarray, contract: dict[str, Any]
                    ) -> dict[str, Any]:
    maximum = int(len(data["document_ids"])
                  * contract["policies"]["hard_unique_candidate_fraction"])
    queries = []
    for local, position in enumerate(positions):
        order = hard_budget_order(score_rows[local], shortlists[local],
                                  index["counts"], maximum)
        candidates, accepted, _ = scale.candidate_union(
            order.tolist(), index, len(data["document_ids"]))
        target = numpy.asarray(top100[local, :10], dtype=numpy.int64)
        state = sequential.cascade_state(
            data, position, candidates, target, discounts, contract["cascade"])
        gains = sequential.target_gains(target, addresses, discounts)
        queries.append({
            "query_id": str(data["query_ids"][position]),
            "accepted_address_count": len(accepted),
            "candidate_count": int(candidates.size),
            "candidate_fraction": candidates.size / len(data["document_ids"]),
            "static_gain_coverage": sum(gains.get(int(value), 0.0)
                                        for value in accepted)
                                    / max(sum(gains.values()), 1.0e-30),
            "actionable_gain_coverage": state["coverage"],
            "exact_ndcg_at_10": state["ndcg_at_10"],
            "hamming_input_count": state["hamming_distance_evaluations"],
            "adc_input_count": state["adc_distance_evaluations"],
            "selected_address_sha256": scale.sequence_sha256(order),
        })
    keys = ["accepted_address_count", "candidate_count", "candidate_fraction",
            "static_gain_coverage", "actionable_gain_coverage",
            "exact_ndcg_at_10", "hamming_input_count", "adc_input_count"]
    return {
        "treatment": name,
        "policy": policy["policy"],
        "lambda": policy["lambda"],
        "query_count": len(queries),
        **{key: float(numpy.mean([row[key] for row in queries],
                                 dtype=numpy.float64)) for key in keys},
        "queries": queries,
    }


def best_configuration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda row: (
        row["actionable_gain_coverage"], row["exact_ndcg_at_10"],
        -row["candidate_fraction"], -row["accepted_address_count"],
        row["policy"], -(row["lambda"] if row["lambda"] is not None else -1.0)))


def decision(internal: list[dict[str, Any]], frontier_result: dict[str, Any],
             contract: dict[str, Any]) -> dict[str, Any]:
    comparisons = []
    success = {}
    for target_name in contract["targets"]:
        current = []
        for seed in contract["route"]["seeds"]:
            learned = next(row for row in internal if row["seed"] == seed
                           and row["target"] == target_name)
            def baseline(treatment: str) -> dict[str, Any]:
                row = next(value for value in frontier_result["rows"]
                           if value["seed"] == seed
                           and value["treatment"] == treatment)
                return next(value["last_feasible"] for value in row["frontier"]
                            if value["candidate_fraction_budget"] == 0.005)
            r0 = baseline("r0_scalar")
            privileged = baseline("privileged_gain_density")
            closure = ((learned["actionable_gain_coverage"]
                        - r0["actionable_gain_coverage"])
                       / max(privileged["actionable_gain_coverage"]
                             - r0["actionable_gain_coverage"], 1.0e-30))
            value = {
                "seed": seed, "target": target_name,
                "actionable_gain_coverage": learned["actionable_gain_coverage"],
                "candidate_fraction": learned["candidate_fraction"],
                "exact_ndcg_at_10": learned["exact_ndcg_at_10"],
                "r0_to_privileged_gap_closed": closure,
                "ndcg_delta_vs_r0": learned["exact_ndcg_at_10"]
                                      - r0["exact_ndcg_at_10"],
            }
            comparisons.append(value)
            current.append(value)
        direct = all(row["actionable_gain_coverage"] >= contract["decision"][
            "minimum_actionable_gain"]
                     and row["candidate_fraction"] <= contract["decision"][
                         "maximum_candidate_fraction"] for row in current)
        progress = all(row["r0_to_privileged_gap_closed"] >= contract["decision"][
            "minimum_r0_to_privileged_gap_closed"]
                       and row["ndcg_delta_vs_r0"] >= -contract["decision"][
                           "maximum_ndcg_regression"] for row in current)
        success[target_name] = {"direct_gate_passed": direct,
                                "progress_gate_passed": progress}
    any_success = any(value["direct_gate_passed"]
                      or value["progress_gate_passed"] for value in success.values())
    return {
        "de_1m_internal_comparisons": comparisons,
        "target_success": success,
        "decoupled_relevance_cost_gate_passed": any_success,
        "replication_topology_diagnostic_required": True,
        "configuration_opened_after_models_frozen": True,
        "internal_opened_after_policy_selection": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def evaluate(contract: dict[str, Any], frontier_result: dict[str, Any],
             materialization: dict[str, Any], split: dict[str, Any],
             external_ids: list[str], external_vectors: numpy.ndarray,
             summary: dict[str, Any], args: argparse.Namespace) -> tuple[
                 list[dict[str, Any]], list[dict[str, Any]],
                 list[dict[str, Any]], dict[str, Any]]:
    parent_contract = matched.planner.load_contract(
        THIS / "neuroute-r3-matched-ladder.example.json")
    feature_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = [by_id[value] for value in split["training_query_ids"]]
    configuration_positions = [by_id[value]
                               for value in split["configuration_selection_query_ids"]]
    internal_positions = [by_id[value]
                          for value in split["internal_evaluation_query_ids"]]
    pool_ids = list(split["training_query_ids"]) + external_ids
    pool_vectors = numpy.concatenate((
        numpy.asarray(data["queries"][training_positions], dtype=numpy.float32),
        external_vectors), axis=0)
    require(pool_vectors.shape == (8141, 384),
            "decoupled relevance training pool differs")
    training_top100, exact_manifest = exact_teacher(
        args.teacher_cache_root / "training-top100", data["documents"],
        data["document_ids"], pool_vectors, 100)
    projection_contract = teacher_objective.planner.load_contract(
        THIS / "neuroute-teacher-objective-ablation.example.json")
    projection_matrix, projection_metrics = teacher_objective.fit_query_projection(
        data, [*training_positions, *configuration_positions], projection_contract)
    pool_codes, pool_projections = teacher_objective.query_cascade_inputs(
        pool_vectors, projection_matrix)
    pool_codes[:len(training_positions)] = data["query_codes"][training_positions]
    pool_projections[:len(training_positions)] = data["query_projection"][
        training_positions]
    discounts = 1.0 / numpy.log2(numpy.arange(10, dtype=numpy.float64) + 2.0)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    models = []
    configuration_rows = []
    selections = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = matched.load_summary_state(
            args.r3_summary_materialization_root, summary, seed, occupied)
        cache, cache_manifest = matched.ambiguity.locate_cache(
            args.parent_cache_root, seed,
            matched.ambiguity.planner.load_contract(
                THIS / "neuroute-representation-ambiguity.example.json")[
                    "cache_manifest_sha256"][str(seed)])
        training_shortlists = numpy.load(
            cache / cache_manifest["outputs"]["shortlists"]["path"], mmap_mode="r")
        training_features = numpy.load(
            cache / cache_manifest["outputs"]["features"]["path"], mmap_mode="r")
        scalar_mean, scalar_deviation = matched.ambiguity.feature_normalization(
            training_features)
        interactions = matched.interaction_arrays(
            pool_vectors, training_shortlists, occupied, state,
            int(contract["training"]["interaction_batch_queries"]),
            args.interaction_cache_root / f"seed-{seed}")
        interaction_norm = matched.interaction_normalization(interactions)
        targets, target_manifest = teacher_cache(
            args.teacher_cache_root / "training-targets", seed,
            training_shortlists, training_top100, addresses, index["counts"],
            index, data, pool_codes, pool_projections, discounts, contract)
        for target_index, target_name in enumerate(contract["targets"]):
            model_seed = seed ^ 0x2468ACE ^ 8141
            path = args.model_root / f"model-{target_name}-{seed}.npz"
            if path.is_file():
                arrays, saved_mean, saved_deviation, metadata = base.read_model(path)
                require(metadata.get("family") == "neuroute_decoupled_relevance_model"
                        and metadata.get("seed") == seed
                        and metadata.get("target") == target_name
                        and metadata.get("model_seed") == model_seed
                        and metadata.get("contract_sha256") == sha256(args.contract)
                        and numpy.array_equal(saved_mean, scalar_mean)
                        and numpy.array_equal(saved_deviation, scalar_deviation),
                        "decoupled resumable model differs")
            else:
                arrays, training = train_model(
                    target_name, pool_vectors, training_shortlists,
                    training_features, targets[target_name], occupied, state,
                    interactions, scalar_mean, scalar_deviation, interaction_norm,
                    index["counts"], model_seed, contract, parent_contract)
                metadata = {
                    "schema_version": 1,
                    "family": "neuroute_decoupled_relevance_model",
                    "seed": seed, "target": target_name,
                    "model_seed": model_seed,
                    "training_query_count": 8141,
                    "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                        pool_ids, dtype=object)),
                    "contract_sha256": sha256(args.contract),
                    "teacher_cache_sha256": hashlib.sha256(canonical(
                        target_manifest)).hexdigest(),
                    "training": training,
                }
                matched.save_model(path, arrays, scalar_mean, scalar_deviation,
                                   metadata)
            models.append({"seed": seed, "target": target_name,
                           "file": path.name, "sha256": sha256(path),
                           "metadata": metadata})
        # All target models for this seed are frozen before configuration labels.
        configuration_queries = numpy.asarray(
            data["queries"][configuration_positions], dtype=numpy.float32)
        configuration_top100, _ = exact_teacher(
            args.teacher_cache_root / "configuration-top100",
            data["documents"], data["document_ids"], configuration_queries, 100)
        configuration_shortlists, configuration_features = base.prepare_query_features(
            configuration_queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            feature_contract["training"]["feature_query_batch_size"])
        configuration_interactions = matched.interaction_arrays(
            configuration_queries, configuration_shortlists, occupied, state,
            int(contract["training"]["interaction_batch_queries"]))
        configuration_targets = target_arrays(
            configuration_shortlists, configuration_top100,
            data["query_codes"][configuration_positions],
            data["query_projection"][configuration_positions],
            addresses, index["counts"], index, data,
            discounts, contract)
        for target_name in contract["targets"]:
            artifact = next(row for row in models if row["seed"] == seed
                            and row["target"] == target_name)
            arrays, saved_mean, saved_deviation, metadata = base.read_model(
                args.model_root / artifact["file"])
            scores = matched.numpy_scores(
                "r3c_residual_shape", configuration_queries,
                configuration_shortlists, configuration_features, occupied,
                state, configuration_interactions, arrays, saved_mean,
                saved_deviation)
            calibration = calibrate(scores, configuration_targets)
            gain, logit = calibrated(scores, calibration)
            candidates = []
            for policy in policy_candidates(contract):
                values = policy_scores(gain, logit, configuration_shortlists,
                                       index["counts"], policy)
                row = evaluate_policy(
                    target_name, policy, values, configuration_shortlists,
                    addresses, index, data, configuration_positions,
                    configuration_top100, discounts, contract)
                row.update({"seed": seed, "target": target_name,
                            "calibration": calibration})
                configuration_rows.append(row)
                candidates.append(row)
            chosen = best_configuration(candidates)
            selections.append({
                "seed": seed, "target": target_name,
                "policy": chosen["policy"], "lambda": chosen["lambda"],
                "calibration": calibration,
                "configuration_actionable_gain_coverage": chosen[
                    "actionable_gain_coverage"],
                "configuration_exact_ndcg_at_10": chosen["exact_ndcg_at_10"],
                "configuration_candidate_fraction": chosen["candidate_fraction"],
            })
        del addresses, index, occupied, prototypes, effective, state
        del training_shortlists, training_features, interactions, targets
        del configuration_shortlists, configuration_features
        del configuration_interactions, configuration_targets
        gc.collect()
    # Every model and configuration choice is frozen before internal qrels open.
    internal_queries = numpy.asarray(data["queries"][internal_positions],
                                     dtype=numpy.float32)
    internal_top100, internal_manifest = exact_teacher(
        args.teacher_cache_root / "internal-top100", data["documents"],
        data["document_ids"], internal_queries, 100)
    internal_rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = matched.load_summary_state(
            args.r3_summary_materialization_root, summary, seed, occupied)
        shortlists, features = base.prepare_query_features(
            internal_queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            feature_contract["training"]["feature_query_batch_size"])
        interactions = matched.interaction_arrays(
            internal_queries, shortlists, occupied, state,
            int(contract["training"]["interaction_batch_queries"]))
        for target_name in contract["targets"]:
            artifact = next(row for row in models if row["seed"] == seed
                            and row["target"] == target_name)
            selection = next(row for row in selections if row["seed"] == seed
                             and row["target"] == target_name)
            arrays, scalar_mean, scalar_deviation, metadata = base.read_model(
                args.model_root / artifact["file"])
            require(metadata == artifact["metadata"],
                    "decoupled internal model metadata differs")
            scores = matched.numpy_scores(
                "r3c_residual_shape", internal_queries, shortlists, features,
                occupied, state, interactions, arrays, scalar_mean,
                scalar_deviation)
            gain, logit = calibrated(scores, selection["calibration"])
            policy = {"policy": selection["policy"], "lambda": selection["lambda"]}
            values = policy_scores(gain, logit, shortlists, index["counts"], policy)
            row = evaluate_policy(
                target_name, policy, values, shortlists, addresses, index, data,
                internal_positions, internal_top100, discounts, contract)
            row.update({"dataset": "de-1m", "seed": seed,
                        "target": target_name, "selection": selection})
            internal_rows.append(row)
        del addresses, index, occupied, prototypes, effective, state
        del shortlists, features, interactions
        gc.collect()
    cache_summary = {
        "training_exact_teacher_manifest_sha256": hashlib.sha256(canonical(
            exact_manifest)).hexdigest(),
        "internal_exact_teacher_manifest_sha256": hashlib.sha256(canonical(
            internal_manifest)).hexdigest(),
        "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
            pool_ids, dtype=object)),
        "query_projection_metrics": projection_metrics,
        "external_qrels_used": False,
    }
    return models, configuration_rows, internal_rows, {
        "selections": selections, "training_cache": cache_summary,
        "decision": decision(internal_rows, frontier_result, contract)}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    (frontier_result, materialization, split, external_ids,
     external_vectors, summary) = validate_parent(contract, args)
    models, configuration, internal, summary_result = evaluate(
        contract, frontier_result, materialization, split, external_ids,
        external_vectors, summary, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_decoupled_relevance_cost_result",
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
        "models": models,
        "configuration_rows": configuration,
        "selections": summary_result["selections"],
        "internal_rows": internal,
        "training_cache": summary_result["training_cache"],
        "decision": summary_result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-decoupled-relevance-cost.example.json")
    scores = numpy.asarray([0.4, 0.9, 0.8, 0.7], dtype=numpy.float64)
    addresses = numpy.asarray([1, 2, 3, 4], dtype=numpy.uint32)
    counts = numpy.asarray([0, 5, 8, 4, 3], dtype=numpy.int64)
    order = hard_budget_order(scores, addresses, counts, 11)
    require(order.tolist() == [2, 4]
            and len(policy_candidates(contract)) == 16
            and planner.plan(contract)["model_fits"] == 15,
            "decoupled hard-budget self-test differs")
    shortlists = numpy.asarray([[1, 2]], dtype=numpy.uint32)
    gain = numpy.asarray([[0.2, 0.1]])
    logit = numpy.asarray([[1.0, 0.5]])
    values = policy_scores(gain, logit, shortlists, counts,
                           {"policy": "predicted_gain_per_cost", "lambda": None})
    require(values[0, 0] > values[0, 1],
            "decoupled policy score self-test differs")
    print("NeuRoute decoupled relevance/cost runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-decoupled-relevance-cost.example.json")
    for name in [
            "feasible-frontier-result", "feasible-frontier-evidence",
            "r3-summary-result", "r3-summary-evidence",
            "r3-summary-materialization-root", "matched-representation-result",
            "matched-representation-evidence", "ambiguity-result",
            "ambiguity-evidence", "nonlinear-result", "nonlinear-evidence",
            "prototype-gain-density-result", "prototype-gain-density-evidence",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "interaction-cache-root", "teacher-cache-root",
            "model-root", "output"]:
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all decoupled relevance/cost paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-decoupled-relevance-cost: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
