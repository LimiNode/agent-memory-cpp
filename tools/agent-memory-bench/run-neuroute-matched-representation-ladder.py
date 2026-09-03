#!/usr/bin/env python3
"""Train and evaluate the matched R0/R1/R2 representation ladder."""

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


planner = load("neuroute_matched_representation_ladder_planner",
               "plan-neuroute-matched-representation-ladder.py")
ambiguity = load("neuroute_matched_representation_ambiguity_parent",
                 "run-neuroute-representation-ambiguity.py")
base = load("neuroute_matched_representation_nonlinear_parent",
            "run-neuroute-nonlinear-listwise-reranker.py")
prototype = base.parent
multi = base.multi
scale = base.scale
task = base.task


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return ambiguity.sha256(path)


def canonical(value: Any) -> bytes:
    return ambiguity.canonical(value)


def array_sha256(value: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.ascontiguousarray(value).tobytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-matched-representation-ladder.py",
        "run-neuroute-matched-representation-ladder.py",
        "run-neuroute-representation-ambiguity.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
        "run-neuroute-prototype-gain-density-reranker.py",
        "run-neuroute-address-multi-prototype.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], list[str], numpy.ndarray]:
    actual = {
        "representation_ambiguity_result_sha256": sha256(args.ambiguity_result),
        "representation_ambiguity_evidence_sha256": sha256(args.ambiguity_evidence),
        "nonlinear_result_sha256": sha256(args.nonlinear_result),
        "nonlinear_evidence_sha256": sha256(args.nonlinear_evidence),
    }
    require(actual == contract["activation"],
            f"matched-representation activation bytes differ: {actual!r}")
    diagnostic = json.loads(args.ambiguity_result.read_text(encoding="utf-8"))
    diagnostic_evidence = json.loads(
        args.ambiguity_evidence.read_text(encoding="utf-8"))
    require(diagnostic.get("family")
            == "neuroute_representation_ambiguity_diagnostic_result"
            and diagnostic.get("decision", {}).get(
                "representation_ladder_licensed") is True
            and diagnostic_evidence.get("passed") is True
            and diagnostic_evidence.get("result_byte_replay_passed") is True,
            "matched-representation ambiguity parent differs")
    nonlinear = json.loads(args.nonlinear_result.read_text(encoding="utf-8"))
    nonlinear_evidence = json.loads(args.nonlinear_evidence.read_text(encoding="utf-8"))
    require(nonlinear.get("family")
            == "neuroute_nonlinear_listwise_reranker_result"
            and nonlinear_evidence.get("passed") is True
            and nonlinear_evidence.get("result_byte_replay_passed") is True,
            "matched-representation nonlinear parent differs")
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    _, _, materialization, split, external_ids, external_vectors = (
        base.validate_activation(parent_contract, args))
    return materialization, split, external_ids, external_vectors


def shared_projection(seed: int, dimensions: int) -> numpy.ndarray:
    rng = numpy.random.default_rng(seed ^ 0x4C8A31D7)
    source = rng.standard_normal((384, dimensions), dtype=numpy.float64)
    orthogonal, upper = numpy.linalg.qr(source, mode="reduced")
    signs = numpy.where(numpy.diag(upper) < 0.0, -1.0, 1.0)
    return numpy.asarray(orthogonal * signs[None, :], dtype=numpy.float32)


def projected_state(prototypes: numpy.ndarray, effective: numpy.ndarray,
                    projection: numpy.ndarray) -> dict[str, numpy.ndarray]:
    projected = numpy.einsum("kod,dh->koh", prototypes, projection,
                             dtype=numpy.float32, optimize=True)
    slots = projected.shape[0]
    mask = numpy.arange(slots)[:, None] < effective[None, :]
    count = effective.astype(numpy.float32)[:, None]
    mean = numpy.where(mask[:, :, None], projected, 0.0).sum(
        axis=0, dtype=numpy.float32) / count
    maximum = numpy.where(mask[:, :, None], projected, -numpy.inf).max(axis=0)
    centered = numpy.where(mask[:, :, None], projected - mean[None, :, :], 0.0)
    deviation = numpy.sqrt((centered * centered).sum(
        axis=0, dtype=numpy.float32) / count)
    summary = numpy.concatenate((mean, maximum, deviation), axis=1).astype(numpy.float32)
    summary_mean = summary.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    summary_deviation = summary.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    summary_deviation[summary_deviation < 1.0e-6] = 1.0
    normalized_summary = numpy.asarray(
        (summary - summary_mean) / summary_deviation, dtype=numpy.float32)
    valid = projected[mask]
    prototype_mean = valid.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    prototype_deviation = valid.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    prototype_deviation[prototype_deviation < 1.0e-6] = 1.0
    return {
        "projected": projected,
        "normalized_summary": normalized_summary,
        "summary_mean": summary_mean,
        "summary_deviation": summary_deviation,
        "prototype_mean": prototype_mean,
        "prototype_deviation": prototype_deviation,
    }


def address_lookup(occupied: numpy.ndarray) -> numpy.ndarray:
    result = numpy.full(1 << 16, -1, dtype=numpy.int32)
    result[occupied.astype(numpy.int64)] = numpy.arange(len(occupied), dtype=numpy.int32)
    return result


def initialized_arrays(variant: str, contract: dict[str, Any], seed: int
                       ) -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(seed)

    def weight(rows: int, columns: int) -> numpy.ndarray:
        bound = numpy.sqrt(6.0 / float(rows + columns))
        return rng.uniform(-bound, bound, size=(rows, columns)).astype(numpy.float32)

    local_dimensions = 22 if variant == "r0_scalar" else 195
    score_hidden = int(contract["models"]["score_hidden_dimensions"][variant])
    arrays = {
        "query_weight": weight(384, 32),
        "query_bias": numpy.zeros(32, dtype=numpy.float32),
        "local_weight": weight(local_dimensions, 32),
        "local_bias": numpy.zeros(32, dtype=numpy.float32),
        "score_weight1": weight(160, score_hidden),
        "score_bias1": numpy.zeros(score_hidden, dtype=numpy.float32),
        "score_weight2": weight(score_hidden, 1),
        "score_bias2": numpy.zeros(1, dtype=numpy.float32),
    }
    if variant == "r2_query_gated_raw_k8":
        arrays["attention_diagonal"] = numpy.ones(64, dtype=numpy.float32)
    return arrays


def parameter_count(arrays: dict[str, numpy.ndarray]) -> int:
    return sum(int(value.size) for value in arrays.values())


def invariant_local_numpy(shortlists: numpy.ndarray, scalar: numpy.ndarray,
                          lookup: numpy.ndarray, state: dict[str, numpy.ndarray],
                          scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray
                          ) -> numpy.ndarray:
    positions = lookup[shortlists]
    require(numpy.all(positions >= 0), "matched-representation shortlist is unoccupied")
    logistics = numpy.asarray((scalar[:, :, 13:16] - scalar_mean[13:16])
                              / scalar_deviation[13:16], dtype=numpy.float32)
    return numpy.concatenate((state["normalized_summary"][positions], logistics),
                             axis=2).astype(numpy.float32)


def torch_local(variant: str, query: Any, shortlists: numpy.ndarray,
                scalar: numpy.ndarray, lookup: numpy.ndarray,
                state_tensors: dict[str, Any], scalar_mean: numpy.ndarray,
                scalar_deviation: numpy.ndarray, parameters: dict[str, Any]) -> Any:
    torch = importlib.import_module("torch")
    if variant == "r0_scalar":
        value = (scalar.astype(numpy.float32) - scalar_mean) / scalar_deviation
        return torch.from_numpy(numpy.asarray(value, dtype=numpy.float32))
    positions_numpy = lookup[shortlists]
    positions = torch.from_numpy(positions_numpy.astype(numpy.int64))
    invariant = state_tensors["normalized_summary"][positions]
    logistics = torch.from_numpy(numpy.asarray(
        (scalar[:, :, 13:16].astype(numpy.float32) - scalar_mean[13:16])
        / scalar_deviation[13:16], dtype=numpy.float32))
    if variant == "r1_invariant_raw_k8":
        return torch.cat((invariant, logistics), dim=2)
    gathered = state_tensors["projected"][:, positions].permute(1, 2, 0, 3)
    query_projected = query @ state_tensors["projection"]
    logits = (gathered * query_projected[:, None, None, :]
              * parameters["attention_diagonal"][None, None, None, :]).sum(dim=3)
    logits = logits / numpy.sqrt(float(gathered.shape[3]))
    effective = state_tensors["effective"][positions]
    slot = torch.arange(gathered.shape[2], dtype=torch.int64)[None, None, :]
    logits = logits.masked_fill(slot >= effective[:, :, None], -1.0e9)
    weights = torch.softmax(logits, dim=2)
    pooled = (weights[:, :, :, None] * gathered).sum(dim=2)
    pooled = ((pooled - state_tensors["prototype_mean"])
              / state_tensors["prototype_deviation"])
    return torch.cat((pooled, invariant[:, :, :128], logistics), dim=2)


def score_torch(query: Any, local_input: Any, parameters: dict[str, Any],
                score_scale: float) -> Any:
    torch = importlib.import_module("torch")
    local = torch.tanh(local_input @ parameters["local_weight"]
                       + parameters["local_bias"])
    query_hidden = torch.tanh(query @ parameters["query_weight"]
                              + parameters["query_bias"])
    expanded = query_hidden[:, None, :].expand_as(local)
    mean_context = local.mean(dim=1, keepdim=True).expand_as(local)
    maximum_context = local.max(dim=1, keepdim=True).values.expand_as(local)
    joined = torch.cat((local, expanded, local * expanded,
                        mean_context, maximum_context), dim=2)
    hidden = torch.tanh(joined @ parameters["score_weight1"]
                        + parameters["score_bias1"])
    return ((hidden @ parameters["score_weight2"]
             + parameters["score_bias2"])[..., 0] * score_scale)


def training_state_tensors(state: dict[str, numpy.ndarray], projection: numpy.ndarray,
                           effective: numpy.ndarray) -> dict[str, Any]:
    torch = importlib.import_module("torch")
    return {
        "projected": torch.from_numpy(state["projected"]),
        "normalized_summary": torch.from_numpy(state["normalized_summary"]),
        "prototype_mean": torch.from_numpy(state["prototype_mean"]),
        "prototype_deviation": torch.from_numpy(state["prototype_deviation"]),
        "projection": torch.from_numpy(projection),
        "effective": torch.from_numpy(effective.astype(numpy.int64)),
    }


def train_model(variant: str, queries: numpy.ndarray, shortlists: numpy.ndarray,
                scalar_features: numpy.ndarray, targets: numpy.ndarray,
                lookup: numpy.ndarray, state: dict[str, numpy.ndarray],
                projection: numpy.ndarray, effective: numpy.ndarray,
                scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
                model_seed: int, contract: dict[str, Any]) -> tuple[
                    dict[str, numpy.ndarray], dict[str, Any]]:
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    training = contract["training"]
    require(torch.__version__.startswith(str(training["torch_version_prefix"])),
            f"matched-representation torch version differs: {torch.__version__}")
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(training["torch_threads"]))
    torch.manual_seed(model_seed & 0x7FFFFFFF)
    initialized = initialized_arrays(variant, contract, model_seed ^ 0x6815D3A7)
    expected_count = planner.parameter_counts(contract)[variant]
    require(parameter_count(initialized) == expected_count,
            f"matched-representation parameter count differs: {variant}")
    parameters = {name: torch.nn.Parameter(torch.from_numpy(value.copy()))
                  for name, value in initialized.items()}
    optimizer = torch.optim.AdamW(
        list(parameters.values()), lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]))
    query_tensor = torch.from_numpy(numpy.asarray(queries, dtype=numpy.float32))
    target_tensor = torch.from_numpy(numpy.asarray(targets, dtype=numpy.float32))
    state_tensors = training_state_tensors(state, projection, effective)
    supervised_mask = numpy.asarray(targets.sum(axis=1, dtype=numpy.float64) > 0.0)
    require(numpy.any(supervised_mask), "matched-representation training is empty")
    losses = []
    batch_size = int(training["batch_queries"])
    for epoch in range(int(training["epochs"])):
        rng = numpy.random.default_rng(model_seed ^ ((epoch + 1) * 0x9E3779B1))
        order = rng.permutation(len(queries))
        total_loss = 0.0
        total_rows = 0
        for start in range(0, len(order), batch_size):
            selected = order[start:start + batch_size]
            keep = supervised_mask[selected]
            if not numpy.any(keep):
                continue
            selected = selected[keep]
            positions = torch.from_numpy(selected.astype(numpy.int64))
            query = query_tensor[positions]
            current_shortlists = numpy.asarray(shortlists[selected], dtype=numpy.uint32)
            current_scalar = numpy.asarray(scalar_features[selected], dtype=numpy.float32)
            target = target_tensor[positions]
            target = target / target.sum(dim=1, keepdim=True)
            optimizer.zero_grad(set_to_none=True)
            local = torch_local(
                variant, query, current_shortlists, current_scalar, lookup,
                state_tensors, scalar_mean, scalar_deviation, parameters)
            scores = score_torch(query, local, parameters,
                                 float(training["score_scale"]))
            loss = -(target * functional.log_softmax(scores, dim=1)).sum(dim=1).mean()
            loss.backward()
            optimizer.step()
            rows = len(selected)
            total_loss += float(loss.detach()) * rows
            total_rows += rows
        losses.append(total_loss / max(total_rows, 1))
    arrays = {name: value.detach().numpy().astype(numpy.float32)
              for name, value in parameters.items()}
    arrays["shared_projection"] = projection
    arrays["summary_mean"] = state["summary_mean"]
    arrays["summary_deviation"] = state["summary_deviation"]
    arrays["prototype_mean"] = state["prototype_mean"]
    arrays["prototype_deviation"] = state["prototype_deviation"]
    return arrays, {
        "epoch_losses": losses,
        "final_loss": losses[-1],
        "parameter_count": expected_count,
        "supervised_query_count": int(numpy.count_nonzero(supervised_mask)),
        "zero_target_query_count": int(numpy.count_nonzero(~supervised_mask)),
        "torch_version": torch.__version__,
        "external_pseudo_supervision": True,
        "external_qrels_used": False,
    }


def numpy_local(variant: str, queries: numpy.ndarray, shortlists: numpy.ndarray,
                scalar: numpy.ndarray, occupied: numpy.ndarray,
                prototypes: numpy.ndarray, effective: numpy.ndarray,
                arrays: dict[str, numpy.ndarray], scalar_mean: numpy.ndarray,
                scalar_deviation: numpy.ndarray) -> numpy.ndarray:
    projection = arrays["shared_projection"]
    state = projected_state(prototypes, effective, projection)
    lookup = address_lookup(occupied)
    positions = lookup[shortlists]
    require(numpy.all(positions >= 0), "matched-representation evaluation address differs")
    if variant == "r0_scalar":
        return numpy.asarray((scalar - scalar_mean) / scalar_deviation,
                             dtype=numpy.float32)
    logistics = numpy.asarray((scalar[:, :, 13:16] - scalar_mean[13:16])
                              / scalar_deviation[13:16], dtype=numpy.float32)
    invariant = state["normalized_summary"][positions]
    if variant == "r1_invariant_raw_k8":
        return numpy.concatenate((invariant, logistics), axis=2).astype(numpy.float32)
    gathered = state["projected"][:, positions].transpose(1, 2, 0, 3)
    query_projected = numpy.asarray(queries @ projection, dtype=numpy.float32)
    logits = (gathered * query_projected[:, None, None, :]
              * arrays["attention_diagonal"][None, None, None, :]).sum(
                  axis=3, dtype=numpy.float32) / numpy.sqrt(64.0)
    mask = numpy.arange(gathered.shape[2])[None, None, :] < effective[positions][:, :, None]
    logits = numpy.where(mask, logits, -1.0e9)
    logits -= logits.max(axis=2, keepdims=True)
    weights = numpy.exp(logits, dtype=numpy.float32)
    weights /= weights.sum(axis=2, keepdims=True, dtype=numpy.float32)
    pooled = (weights[:, :, :, None] * gathered).sum(axis=2, dtype=numpy.float32)
    pooled = numpy.asarray((pooled - state["prototype_mean"])
                           / state["prototype_deviation"], dtype=numpy.float32)
    return numpy.concatenate((pooled, invariant[:, :, :128], logistics),
                             axis=2).astype(numpy.float32)


def numpy_scores(variant: str, queries: numpy.ndarray, shortlists: numpy.ndarray,
                 scalar: numpy.ndarray, occupied: numpy.ndarray,
                 prototypes: numpy.ndarray, effective: numpy.ndarray,
                 arrays: dict[str, numpy.ndarray], scalar_mean: numpy.ndarray,
                 scalar_deviation: numpy.ndarray) -> numpy.ndarray:
    local_input = numpy_local(
        variant, queries, shortlists, scalar, occupied, prototypes, effective,
        arrays, scalar_mean, scalar_deviation)
    local = numpy.tanh(local_input @ arrays["local_weight"] + arrays["local_bias"])
    query_hidden = numpy.tanh(numpy.asarray(queries, dtype=numpy.float32)
                              @ arrays["query_weight"] + arrays["query_bias"])
    expanded = numpy.broadcast_to(query_hidden[:, None, :], local.shape)
    mean_context = local.mean(axis=1, keepdims=True, dtype=numpy.float32)
    maximum_context = local.max(axis=1, keepdims=True)
    joined = numpy.concatenate((
        local, expanded, local * expanded,
        numpy.broadcast_to(mean_context, local.shape),
        numpy.broadcast_to(maximum_context, local.shape)), axis=2)
    hidden = numpy.tanh(joined @ arrays["score_weight1"] + arrays["score_bias1"])
    return numpy.asarray((hidden @ arrays["score_weight2"]
                          + arrays["score_bias2"])[..., 0], dtype=numpy.float64)


def save_model(path: Path, arrays: dict[str, numpy.ndarray], scalar_mean: numpy.ndarray,
               scalar_deviation: numpy.ndarray, metadata: dict[str, Any]) -> str:
    return base.save_model(path, arrays, scalar_mean, scalar_deviation, metadata)


def calibration_row(variant: str, seed: int, shortlists: numpy.ndarray,
                    targets: numpy.ndarray, scores: numpy.ndarray,
                    counts: numpy.ndarray, document_count: int,
                    budget: int) -> dict[str, Any]:
    gains = []
    candidates = []
    for query_index in range(len(shortlists)):
        order = prototype.ordered(scores[query_index], shortlists[query_index])
        selected = order[:budget]
        positions = {int(address): column for column, address
                     in enumerate(shortlists[query_index].tolist())}
        total = float(targets[query_index].sum(dtype=numpy.float64))
        if total <= 0.0:
            continue
        gains.append(sum(targets[query_index, positions[int(address)]]
                         for address in selected.tolist()) / total)
        candidates.append(int(counts[selected].sum(dtype=numpy.int64)) / document_count)
    return {
        "seed": seed,
        "variant": variant,
        "query_count": len(shortlists),
        "supervised_query_count": len(gains),
        "static_gain_density_coverage_at_256": float(numpy.mean(
            gains, dtype=numpy.float64)),
        "candidate_fraction_at_256": float(numpy.mean(
            candidates, dtype=numpy.float64)),
    }


def budget(row: dict[str, Any], value: int) -> dict[str, Any]:
    return next(item for item in row["budgets"] if item["address_budget"] == value)


def result_decision(rows: list[dict[str, Any]], contract: dict[str, Any]
                    ) -> dict[str, Any]:
    rule = contract["decision"]
    comparisons = []
    success = {}
    for variant in contract["representations"]["variants"]:
        current = []
        for seed in contract["route"]["seeds"]:
            control = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == "prototype_order")
            learned = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == variant)
            teacher = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == "privileged_teacher")
            p, learned_budget, t = (budget(control, 256), budget(learned, 256),
                                    budget(teacher, 256))
            closure = ((learned_budget["actionable_gain_coverage"]
                        - p["actionable_gain_coverage"])
                       / max(t["actionable_gain_coverage"]
                             - p["actionable_gain_coverage"], 1.0e-30))
            comparison = {
                "seed": seed,
                "variant": variant,
                "prototype_actionable_gain_at_256": p["actionable_gain_coverage"],
                "learned_actionable_gain_at_256": learned_budget[
                    "actionable_gain_coverage"],
                "teacher_actionable_gain_at_256": t["actionable_gain_coverage"],
                "teacher_gap_closure": closure,
                "prototype_candidate_fraction_at_256": p["candidate_fraction"],
                "learned_candidate_fraction_at_256": learned_budget[
                    "candidate_fraction"],
                "candidate_fraction_ratio": learned_budget["candidate_fraction"]
                    / max(p["candidate_fraction"], 1.0e-30),
            }
            comparisons.append(comparison)
            current.append(comparison)
        direct = all(row["learned_actionable_gain_at_256"]
                     >= rule["minimum_actionable_gain"]
                     and row["learned_candidate_fraction_at_256"]
                     <= rule["maximum_candidate_fraction"] for row in current)
        progress = all(row["teacher_gap_closure"]
                       >= rule["minimum_prototype_to_teacher_gap_closed"]
                       and row["candidate_fraction_ratio"]
                       <= rule["maximum_candidate_fraction_ratio_vs_prototype_order"]
                       for row in current)
        success[variant] = {"direct_gate_passed": direct,
                            "progress_gate_passed": progress}
    richer_success = any(success[name]["direct_gate_passed"]
                         or success[name]["progress_gate_passed"]
                         for name in ("r1_invariant_raw_k8",
                                      "r2_query_gated_raw_k8"))
    return {
        "de_1m_internal_comparisons": comparisons,
        "variant_success": success,
        "richer_k8_representation_sufficient": richer_success,
        "r3_document_summary_licensed": not richer_success,
        "stateful_policy_licensed": False,
        "configuration_opened_after_all_models_frozen": True,
        "internal_evaluation_opened_after_configuration_replay": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def evaluate(contract: dict[str, Any], materialization: dict[str, Any],
             split: dict[str, Any], external_ids: list[str],
             external_vectors: numpy.ndarray, args: argparse.Namespace) -> tuple[
                 list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
                 dict[str, Any]]:
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root, args.de_1m_input_root)
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
            "matched-representation training query pool differs")
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    ambiguity_contract = ambiguity.planner.load_contract(
        THIS / "neuroute-representation-ambiguity.example.json")
    models = []
    seed_state = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            ambiguity_contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(cache / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        scalar_features = numpy.load(cache / manifest["outputs"]["features"]["path"],
                                     mmap_mode="r")
        targets = numpy.load(cache / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            scalar_features)
        projection = shared_projection(seed, 64)
        state = projected_state(prototypes, effective, projection)
        lookup = address_lookup(occupied)
        state_hashes = {
            "shared_projection_sha256": array_sha256(projection),
            "projected_prototypes_sha256": array_sha256(state["projected"]),
            "normalized_address_summary_sha256": array_sha256(
                state["normalized_summary"]),
            "effective_prototypes_sha256": array_sha256(effective),
            "occupied_addresses_sha256": array_sha256(occupied),
        }
        seed_state.append({"seed": seed, **state_hashes})
        for variant_index, variant in enumerate(
                contract["representations"]["variants"]):
            model_seed = seed ^ ((variant_index + 1) * 0x13579BD) ^ 8141
            arrays, training_metrics = train_model(
                variant, pool_vectors, shortlists, scalar_features, targets,
                lookup, state, projection, effective, scalar_mean,
                scalar_deviation, model_seed, contract)
            metadata = {
                "schema_version": 1,
                "family": "neuroute_matched_representation_model",
                "seed": seed,
                "variant": variant,
                "model_seed": model_seed,
                "training_query_count": 8141,
                "training_query_ids_sha256": scale.hash_ids(numpy.asarray(
                    pool_ids, dtype=object)),
                "contract_sha256": sha256(args.contract),
                "document_addresses_sha256": route["document_addresses"]["sha256"],
                "cache_manifest_sha256": ambiguity_contract[
                    "cache_manifest_sha256"][str(seed)],
                "representation_state": state_hashes,
                "training": training_metrics,
            }
            path = args.model_root / f"model-{variant}-{seed}.npz"
            digest = save_model(path, arrays, scalar_mean, scalar_deviation, metadata)
            models.append({
                "seed": seed, "variant": variant, "file": path.name,
                "sha256": digest, "metadata": metadata,
            })
        del addresses, index, occupied, prototypes, effective, members
        del shortlists, scalar_features, targets, state
        gc.collect()

    # All nine models are serialized before configuration qrels are opened.
    configuration_oracle, _ = scale.exact_oracle(
        data, configuration_positions, contract["cascade"]["oracle_k"])
    discounts = 1.0 / numpy.log2(numpy.arange(
        contract["cascade"]["oracle_k"], dtype=numpy.float64) + 2.0)
    configuration_rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        queries = numpy.asarray(data["queries"][configuration_positions],
                                dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        targets = prototype.density_targets(
            shortlists, configuration_oracle, configuration_positions,
            addresses, index["counts"], discounts)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            ambiguity_contract["cache_manifest_sha256"][str(seed)])
        training_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            training_features)
        for variant in contract["representations"]["variants"]:
            artifact = next(row for row in models if row["seed"] == seed
                            and row["variant"] == variant)
            arrays, saved_mean, saved_deviation, metadata = base.read_model(
                args.model_root / artifact["file"])
            require(metadata == artifact["metadata"]
                    and numpy.array_equal(saved_mean, scalar_mean)
                    and numpy.array_equal(saved_deviation, scalar_deviation),
                    "matched-representation configuration model differs")
            scores = numpy_scores(
                variant, queries, shortlists, scalar_features, occupied,
                prototypes, effective, arrays, saved_mean, saved_deviation)
            configuration_rows.append(calibration_row(
                variant, seed, shortlists, targets, scores, index["counts"],
                len(data["document_ids"]), 256))
        del addresses, index, occupied, prototypes, effective, members
        del shortlists, scalar_features, targets, training_features
        gc.collect()

    # Configuration replay is complete. Internal qrels are opened exactly once.
    internal_oracle, _ = scale.exact_oracle(
        data, internal_positions, contract["cascade"]["oracle_k"])
    protocol = base.evaluation_contract(contract)
    internal_rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        queries = numpy.asarray(data["queries"][internal_positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        targets = prototype.density_targets(
            shortlists, internal_oracle, internal_positions,
            addresses, index["counts"], discounts)
        orders = {
            "prototype_order": [row.copy() for row in shortlists],
            "privileged_teacher": [prototype.ordered(
                targets[row], shortlists[row], index["counts"])
                for row in range(len(shortlists))],
        }
        for variant in contract["representations"]["variants"]:
            artifact = next(row for row in models if row["seed"] == seed
                            and row["variant"] == variant)
            arrays, scalar_mean, scalar_deviation, metadata = base.read_model(
                args.model_root / artifact["file"])
            require(metadata == artifact["metadata"],
                    "matched-representation internal model differs")
            scores = numpy_scores(
                variant, queries, shortlists, scalar_features, occupied,
                prototypes, effective, arrays, scalar_mean, scalar_deviation)
            orders[variant] = [prototype.ordered(scores[row], shortlists[row])
                               for row in range(len(shortlists))]
        for treatment in ["prototype_order",
                          *contract["representations"]["variants"],
                          "privileged_teacher"]:
            summary = base.summarize_orders(
                treatment, orders[treatment], shortlists, addresses, index,
                data, internal_positions, internal_oracle, discounts, protocol)
            internal_rows.append({"dataset": "de-1m", "seed": seed, **summary})
        del addresses, index, occupied, prototypes, effective, members
        del shortlists, scalar_features, targets
        gc.collect()
    return models, configuration_rows, internal_rows, {
        "seed_representation_state": seed_state,
        "decision": result_decision(internal_rows, contract),
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    materialization, split, external_ids, external_vectors = validate_activation(
        contract, args)
    models, configuration_rows, internal_rows, state = evaluate(
        contract, materialization, split, external_ids, external_vectors, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_matched_representation_ladder_result",
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
        "representation_state": state["seed_representation_state"],
        "models": models,
        "configuration_rows": configuration_rows,
        "internal_rows": internal_rows,
        "decision": state["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-matched-representation-ladder.example.json")
    projection = shared_projection(7, 64)
    require(projection.shape == (384, 64)
            and numpy.allclose(projection.T @ projection,
                               numpy.eye(64), atol=1.0e-5),
            "matched-representation projection self-test differs")
    for variant, expected in planner.parameter_counts(contract).items():
        arrays = initialized_arrays(variant, contract, 11)
        require(parameter_count(arrays) == expected,
                f"matched-representation model self-test differs: {variant}")
    prototypes = numpy.zeros((8, 2, 384), dtype=numpy.float32)
    prototypes[0, 0, 0] = 1.0
    prototypes[:2, 1, 1] = 1.0
    state = projected_state(
        prototypes, numpy.asarray([1, 2], dtype=numpy.int32), projection)
    require(state["projected"].shape == (8, 2, 64)
            and state["normalized_summary"].shape == (2, 192),
            "matched-representation state self-test differs")
    queries = numpy.zeros((1, 384), dtype=numpy.float32)
    shortlists = numpy.asarray([[1, 2]], dtype=numpy.uint32)
    scalar = numpy.zeros((1, 2, 22), dtype=numpy.float32)
    for variant in contract["representations"]["variants"]:
        arrays = initialized_arrays(variant, contract, 17)
        arrays["shared_projection"] = projection
        local = numpy_local(
            variant, queries, shortlists, scalar,
            numpy.asarray([1, 2], dtype=numpy.uint32), prototypes,
            numpy.asarray([1, 2], dtype=numpy.int32), arrays,
            numpy.zeros(22, dtype=numpy.float32),
            numpy.ones(22, dtype=numpy.float32))
        scores = numpy_scores(
            variant, queries, shortlists, scalar,
            numpy.asarray([1, 2], dtype=numpy.uint32), prototypes,
            numpy.asarray([1, 2], dtype=numpy.int32), arrays,
            numpy.zeros(22, dtype=numpy.float32),
            numpy.ones(22, dtype=numpy.float32))
        require(local.shape[:2] == (1, 2) and scores.shape == (1, 2)
                and numpy.all(numpy.isfinite(scores)),
                f"matched-representation forward self-test differs: {variant}")
    print("NeuRoute matched-representation runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-matched-representation-ladder.example.json")
    parser.add_argument("--ambiguity-result", type=Path)
    parser.add_argument("--ambiguity-evidence", type=Path)
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
            parser.error("all matched-representation paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-matched-representation-ladder: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
