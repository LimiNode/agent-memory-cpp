#!/usr/bin/env python3
"""Train and evaluate the matched R0/R3a/R3b/R3c representation ladder."""

from __future__ import annotations

import argparse
import gc
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


planner = load("neuroute_r3_matched_ladder_planner",
               "plan-neuroute-r3-matched-ladder.py")
r3 = load("neuroute_r3_matched_ladder_parent",
          "run-neuroute-r3-document-summary.py")
parent = r3.parent
base = parent.base
prototype = parent.prototype
multi = parent.multi
scale = parent.scale
task = parent.task
ambiguity = parent.ambiguity


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return parent.sha256(path)


def canonical(value: Any) -> bytes:
    return parent.canonical(value)


def array_sha256(value: numpy.ndarray) -> str:
    return parent.array_sha256(value)


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-r3-matched-ladder.py",
        "run-neuroute-r3-matched-ladder.py",
        "run-neuroute-r3-document-summary.py",
        "run-neuroute-matched-representation-ladder.py",
        "run-neuroute-nonlinear-listwise-reranker.py",
        "run-neuroute-prototype-gain-density-reranker.py",
        "run-neuroute-address-multi-prototype.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], list[str], numpy.ndarray, dict[str, Any]]:
    actual = {
        "r3_document_summary_result_sha256": sha256(args.r3_summary_result),
        "r3_document_summary_evidence_sha256": sha256(args.r3_summary_evidence),
    }
    require(actual == contract["activation"],
            f"R3 matched-ladder activation bytes differ: {actual!r}")
    summary = json.loads(args.r3_summary_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.r3_summary_evidence.read_text(encoding="utf-8"))
    require(summary.get("family") == "neuroute_r3_document_summary_result"
            and summary.get("decision", {}).get("matched_r3_ladder_licensed")
            is True and evidence.get("passed") is True
            and evidence.get("artifact_sha_map_replay_passed") is True
            and evidence.get("result_byte_replay_passed") is True,
            "R3 matched-ladder summary parent differs")
    r3_contract = r3.planner.load_contract(
        THIS / "neuroute-r3-document-summary.example.json")
    materialization, split = r3.validate_activation(r3_contract, args)
    parent_contract = parent.planner.load_contract(
        THIS / "neuroute-matched-representation-ladder.example.json")
    _, _, external_ids, external_vectors = parent.validate_activation(
        parent_contract, args)
    return materialization, split, external_ids, external_vectors, summary


def normalization(values: numpy.ndarray, axes: tuple[int, ...]
                  ) -> tuple[numpy.ndarray, numpy.ndarray]:
    mean = values.mean(axis=axes, dtype=numpy.float64).astype(numpy.float32)
    deviation = values.std(axis=axes, dtype=numpy.float64).astype(numpy.float32)
    deviation[deviation < 1.0e-6] = 1.0
    return mean, deviation


def artifact_path(summary_root: Path, seed: int, row: dict[str, Any],
                  role: str) -> Path:
    artifact = next(value for value in row["artifacts"]
                    if value["role"] == role)
    path = summary_root / f"seed-{seed}" / artifact["path"]
    require(path.is_file() and sha256(path) == artifact["sha256"]
            and path.stat().st_size == artifact["bytes"],
            f"R3 matched summary artifact differs: {seed}/{role}")
    return path


def load_summary_state(summary_root: Path, summary: dict[str, Any], seed: int,
                       occupied: numpy.ndarray) -> dict[str, numpy.ndarray]:
    row = next(value for value in summary["seeds"] if value["seed"] == seed)
    require(row["occupied_addresses_sha256"] == array_sha256(occupied),
            "R3 matched occupied addresses differ")
    state = {
        "counts": numpy.load(artifact_path(
            summary_root, seed, row, "local_document_count"), mmap_mode="r"),
        "mean": numpy.load(artifact_path(
            summary_root, seed, row, "mean_residual"), mmap_mode="r"),
        "variance": numpy.load(artifact_path(
            summary_root, seed, row, "diagonal_residual_variance"), mmap_mode="r"),
        "direction": numpy.load(artifact_path(
            summary_root, seed, row, "top_centered_residual_direction"),
            mmap_mode="r"),
        "eigenvalue": numpy.load(artifact_path(
            summary_root, seed, row, "top_residual_eigenvalue"), mmap_mode="r"),
        "energy": numpy.load(artifact_path(
            summary_root, seed, row, "total_residual_energy"), mmap_mode="r"),
    }
    expected = (len(occupied), 8)
    require(state["counts"].shape == expected
            and state["eigenvalue"].shape == expected
            and state["energy"].shape == expected
            and all(state[name].shape == (*expected, 384)
                    for name in ("mean", "variance", "direction")),
            "R3 matched summary shape differs")
    counts = numpy.asarray(state["counts"], dtype=numpy.float32)
    totals = counts.sum(axis=1, keepdims=True, dtype=numpy.float32)
    count_features = numpy.concatenate((
        numpy.log1p(counts), counts / numpy.maximum(totals, 1.0)), axis=1)
    count_mean, count_deviation = normalization(count_features, (0,))
    eigenvalue = numpy.asarray(state["eigenvalue"], dtype=numpy.float32)
    energy = numpy.asarray(state["energy"], dtype=numpy.float32)
    eigenvalue_mean, eigenvalue_deviation = normalization(eigenvalue, (0,))
    energy_mean, energy_deviation = normalization(energy, (0,))
    state.update({
        "normalized_counts": numpy.asarray(
            (count_features - count_mean) / count_deviation, dtype=numpy.float32),
        "normalized_eigenvalue": numpy.asarray(
            (eigenvalue - eigenvalue_mean) / eigenvalue_deviation,
            dtype=numpy.float32),
        "normalized_energy": numpy.asarray(
            (energy - energy_mean) / energy_deviation, dtype=numpy.float32),
        "count_mean": count_mean,
        "count_deviation": count_deviation,
        "eigenvalue_mean": eigenvalue_mean,
        "eigenvalue_deviation": eigenvalue_deviation,
        "energy_mean": energy_mean,
        "energy_deviation": energy_deviation,
    })
    return state


def interaction_arrays(queries: numpy.ndarray, shortlists: numpy.ndarray,
                       occupied: numpy.ndarray, state: dict[str, numpy.ndarray],
                       batch_queries: int, output_root: Path | None = None
                       ) -> dict[str, numpy.ndarray]:
    lookup = parent.address_lookup(occupied)
    shape = (*shortlists.shape, 8)
    result = {}
    for name in ("mean_dot", "variance_projection", "direction_dot"):
        if output_root is None:
            result[name] = numpy.zeros(shape, dtype=numpy.float32)
        else:
            output_root.mkdir(parents=True, exist_ok=True)
            result[name] = numpy.lib.format.open_memmap(
                output_root / f"{name}.npy", mode="w+", dtype=numpy.float32,
                shape=shape)
    for start in range(0, len(queries), batch_queries):
        stop = min(len(queries), start + batch_queries)
        query = numpy.asarray(queries[start:stop], dtype=numpy.float32)
        positions = lookup[numpy.asarray(shortlists[start:stop], dtype=numpy.uint32)]
        require(numpy.all(positions >= 0), "R3 matched shortlist is unoccupied")
        gathered = numpy.asarray(state["mean"][positions], dtype=numpy.float32)
        result["mean_dot"][start:stop] = numpy.einsum(
            "bd,bksd->bks", query, gathered, dtype=numpy.float32, optimize=True)
        del gathered
        gathered = numpy.asarray(state["variance"][positions], dtype=numpy.float32)
        result["variance_projection"][start:stop] = numpy.einsum(
            "bd,bksd->bks", query * query, gathered,
            dtype=numpy.float32, optimize=True)
        del gathered
        gathered = numpy.asarray(state["direction"][positions], dtype=numpy.float32)
        result["direction_dot"][start:stop] = numpy.einsum(
            "bd,bksd->bks", query, gathered, dtype=numpy.float32, optimize=True)
        del gathered
    for value in result.values():
        if isinstance(value, numpy.memmap):
            value.flush()
    return result


def interaction_normalization(interactions: dict[str, numpy.ndarray]
                              ) -> dict[str, numpy.ndarray]:
    result = {}
    for name, values in interactions.items():
        mean, deviation = normalization(values, (0, 1))
        result[f"{name}_mean"] = mean
        result[f"{name}_deviation"] = deviation
    return result


def initialized_arrays(variant: str, contract: dict[str, Any], seed: int
                       ) -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(seed)

    def weight(rows: int, columns: int) -> numpy.ndarray:
        bound = numpy.sqrt(6.0 / float(rows + columns))
        return rng.uniform(-bound, bound, size=(rows, columns)).astype(numpy.float32)

    local_dimensions = int(contract["models"]["local_input_dimensions"][variant])
    score_hidden = int(contract["models"]["score_hidden_dimensions"][variant])
    return {
        "query_weight": weight(384, 32),
        "query_bias": numpy.zeros(32, dtype=numpy.float32),
        "local_weight": weight(local_dimensions, 32),
        "local_bias": numpy.zeros(32, dtype=numpy.float32),
        "score_weight1": weight(160, score_hidden),
        "score_bias1": numpy.zeros(score_hidden, dtype=numpy.float32),
        "score_weight2": weight(score_hidden, 1),
        "score_bias2": numpy.zeros(1, dtype=numpy.float32),
    }


def parameter_count(arrays: dict[str, numpy.ndarray]) -> int:
    return sum(int(value.size) for value in arrays.values())


def fixed_normalizers(state: dict[str, numpy.ndarray],
                      interaction_norm: dict[str, numpy.ndarray]
                      ) -> dict[str, numpy.ndarray]:
    return {
        "r3_count_mean": state["count_mean"],
        "r3_count_deviation": state["count_deviation"],
        "r3_eigenvalue_mean": state["eigenvalue_mean"],
        "r3_eigenvalue_deviation": state["eigenvalue_deviation"],
        "r3_energy_mean": state["energy_mean"],
        "r3_energy_deviation": state["energy_deviation"],
        **{f"r3_{name}": value for name, value in interaction_norm.items()},
    }


def local_numpy(variant: str, shortlists: numpy.ndarray,
                scalar: numpy.ndarray, occupied: numpy.ndarray,
                state: dict[str, numpy.ndarray],
                interactions: dict[str, numpy.ndarray],
                scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
                arrays: dict[str, numpy.ndarray]) -> numpy.ndarray:
    scalar_value = numpy.asarray(
        (scalar.astype(numpy.float32) - scalar_mean) / scalar_deviation,
        dtype=numpy.float32)
    if variant == "r0_scalar":
        return scalar_value
    lookup = parent.address_lookup(occupied)
    positions = lookup[numpy.asarray(shortlists, dtype=numpy.uint32)]
    require(numpy.all(positions >= 0), "R3 matched local address differs")
    counts = numpy.asarray(state["normalized_counts"][positions],
                           dtype=numpy.float32)
    parts = [scalar_value, counts]
    if variant in ("r3b_residual_mean", "r3c_residual_shape"):
        parts.append(numpy.asarray(
            (interactions["mean_dot"] - arrays["r3_mean_dot_mean"])
            / arrays["r3_mean_dot_deviation"], dtype=numpy.float32))
    if variant == "r3c_residual_shape":
        parts.extend((
            numpy.asarray(
                (interactions["variance_projection"]
                 - arrays["r3_variance_projection_mean"])
                / arrays["r3_variance_projection_deviation"],
                dtype=numpy.float32),
            numpy.asarray(
                (interactions["direction_dot"]
                 - arrays["r3_direction_dot_mean"])
                / arrays["r3_direction_dot_deviation"], dtype=numpy.float32),
            numpy.asarray(state["normalized_eigenvalue"][positions],
                          dtype=numpy.float32),
            numpy.asarray(state["normalized_energy"][positions],
                          dtype=numpy.float32),
        ))
    result = numpy.concatenate(parts, axis=2).astype(numpy.float32)
    return result


def score_torch(query: Any, local_input: Any, parameters: dict[str, Any],
                score_scale: float) -> Any:
    return parent.score_torch(query, local_input, parameters, score_scale)


def train_model(variant: str, queries: numpy.ndarray, shortlists: numpy.ndarray,
                scalar_features: numpy.ndarray, targets: numpy.ndarray,
                occupied: numpy.ndarray, state: dict[str, numpy.ndarray],
                interactions: dict[str, numpy.ndarray],
                scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
                interaction_norm: dict[str, numpy.ndarray], model_seed: int,
                contract: dict[str, Any]) -> tuple[
                    dict[str, numpy.ndarray], dict[str, Any]]:
    torch = importlib.import_module("torch")
    functional = importlib.import_module("torch.nn.functional")
    training = contract["training"]
    require(torch.__version__.startswith(str(training["torch_version_prefix"])),
            f"R3 matched torch version differs: {torch.__version__}")
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(training["torch_threads"]))
    torch.manual_seed(model_seed & 0x7FFFFFFF)
    initialized = initialized_arrays(variant, contract, model_seed ^ 0x6815D3A7)
    expected_count = planner.parameter_counts(contract)[variant]
    require(parameter_count(initialized) == expected_count,
            f"R3 matched parameter count differs: {variant}")
    parameters = {name: torch.nn.Parameter(torch.from_numpy(value.copy()))
                  for name, value in initialized.items()}
    optimizer = torch.optim.AdamW(
        list(parameters.values()), lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]))
    query_tensor = torch.from_numpy(numpy.asarray(queries, dtype=numpy.float32))
    target_tensor = torch.from_numpy(numpy.asarray(targets, dtype=numpy.float32))
    normalizers = fixed_normalizers(state, interaction_norm)
    supervised_mask = numpy.asarray(targets.sum(axis=1, dtype=numpy.float64) > 0.0)
    require(numpy.any(supervised_mask), "R3 matched training is empty")
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
            target = target_tensor[positions]
            target = target / target.sum(dim=1, keepdim=True)
            current_interactions = {
                name: numpy.asarray(value[selected], dtype=numpy.float32)
                for name, value in interactions.items()}
            local = local_numpy(
                variant, numpy.asarray(shortlists[selected], dtype=numpy.uint32),
                numpy.asarray(scalar_features[selected], dtype=numpy.float32),
                occupied, state, current_interactions, scalar_mean,
                scalar_deviation, normalizers)
            optimizer.zero_grad(set_to_none=True)
            scores = score_torch(
                query, torch.from_numpy(local), parameters,
                float(training["score_scale"]))
            loss = -(target * functional.log_softmax(scores, dim=1)).sum(
                dim=1).mean()
            loss.backward()
            optimizer.step()
            rows = len(selected)
            total_loss += float(loss.detach()) * rows
            total_rows += rows
        losses.append(total_loss / max(total_rows, 1))
    arrays = {name: value.detach().numpy().astype(numpy.float32)
              for name, value in parameters.items()}
    arrays.update(normalizers)
    return arrays, {
        "epoch_losses": losses,
        "final_loss": losses[-1],
        "parameter_count": expected_count,
        "supervised_query_count": int(numpy.count_nonzero(supervised_mask)),
        "zero_target_query_count": int(numpy.count_nonzero(~supervised_mask)),
        "torch_version": torch.__version__,
        "external_pseudo_supervision": True,
        "external_qrels_used": False,
        "full_384d_summary_interactions": variant in (
            "r3b_residual_mean", "r3c_residual_shape"),
    }


def numpy_scores(variant: str, queries: numpy.ndarray,
                 shortlists: numpy.ndarray, scalar: numpy.ndarray,
                 occupied: numpy.ndarray, state: dict[str, numpy.ndarray],
                 interactions: dict[str, numpy.ndarray],
                 arrays: dict[str, numpy.ndarray], scalar_mean: numpy.ndarray,
                 scalar_deviation: numpy.ndarray) -> numpy.ndarray:
    local_input = local_numpy(
        variant, shortlists, scalar, occupied, state, interactions,
        scalar_mean, scalar_deviation, arrays)
    local = numpy.tanh(local_input @ arrays["local_weight"]
                       + arrays["local_bias"])
    query_hidden = numpy.tanh(numpy.asarray(queries, dtype=numpy.float32)
                              @ arrays["query_weight"] + arrays["query_bias"])
    expanded = numpy.broadcast_to(query_hidden[:, None, :], local.shape)
    mean_context = local.mean(axis=1, keepdims=True, dtype=numpy.float32)
    maximum_context = local.max(axis=1, keepdims=True)
    joined = numpy.concatenate((
        local, expanded, local * expanded,
        numpy.broadcast_to(mean_context, local.shape),
        numpy.broadcast_to(maximum_context, local.shape)), axis=2)
    hidden = numpy.tanh(joined @ arrays["score_weight1"]
                        + arrays["score_bias1"])
    return numpy.asarray((hidden @ arrays["score_weight2"]
                          + arrays["score_bias2"])[..., 0], dtype=numpy.float64)


def save_model(path: Path, arrays: dict[str, numpy.ndarray],
               scalar_mean: numpy.ndarray, scalar_deviation: numpy.ndarray,
               metadata: dict[str, Any]) -> str:
    return base.save_model(path, arrays, scalar_mean, scalar_deviation, metadata)


def budget(row: dict[str, Any], value: int) -> dict[str, Any]:
    return next(item for item in row["budgets"] if item["address_budget"] == value)


def result_decision(rows: list[dict[str, Any]], contract: dict[str, Any]
                    ) -> dict[str, Any]:
    rule = contract["decision"]
    comparisons = []
    success = {}
    for variant in contract["representations"]["variants"][1:]:
        current = []
        for seed in contract["route"]["seeds"]:
            control = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == "r0_scalar")
            learned = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == variant)
            teacher = next(row for row in rows if row["seed"] == seed
                           and row["treatment"] == "privileged_teacher")
            r0_value, learned_value, teacher_value = (
                budget(control, 256), budget(learned, 256), budget(teacher, 256))
            closure = ((learned_value["actionable_gain_coverage"]
                        - r0_value["actionable_gain_coverage"])
                       / max(teacher_value["actionable_gain_coverage"]
                             - r0_value["actionable_gain_coverage"], 1.0e-30))
            comparison = {
                "seed": seed,
                "variant": variant,
                "r0_actionable_gain_at_256": r0_value["actionable_gain_coverage"],
                "learned_actionable_gain_at_256": learned_value[
                    "actionable_gain_coverage"],
                "teacher_actionable_gain_at_256": teacher_value[
                    "actionable_gain_coverage"],
                "r0_to_teacher_gap_closure": closure,
                "r0_candidate_fraction_at_256": r0_value["candidate_fraction"],
                "learned_candidate_fraction_at_256": learned_value[
                    "candidate_fraction"],
                "candidate_fraction_ratio_vs_r0": learned_value[
                    "candidate_fraction"] / max(
                        r0_value["candidate_fraction"], 1.0e-30),
            }
            comparisons.append(comparison)
            current.append(comparison)
        direct = all(row["learned_actionable_gain_at_256"]
                     >= rule["minimum_actionable_gain"]
                     and row["learned_candidate_fraction_at_256"]
                     <= rule["maximum_candidate_fraction"] for row in current)
        progress = all(row["r0_to_teacher_gap_closure"]
                       >= rule["minimum_r0_to_teacher_gap_closed"]
                       and row["candidate_fraction_ratio_vs_r0"]
                       <= rule["maximum_candidate_fraction_ratio_vs_r0"]
                       for row in current)
        strong = all(row["learned_actionable_gain_at_256"]
                     >= rule["strong_evidence_actionable_gain"]
                     and row["candidate_fraction_ratio_vs_r0"]
                     <= rule["maximum_candidate_fraction_ratio_vs_r0"]
                     for row in current)
        success[variant] = {
            "direct_gate_passed": direct,
            "progress_gate_passed": progress,
            "strong_document_distribution_evidence": strong,
        }
    sufficient = any(value["direct_gate_passed"]
                     or value["progress_gate_passed"] for value in success.values())
    strong = any(value["strong_document_distribution_evidence"]
                 for value in success.values())
    best_mean = max(numpy.mean([
        row["learned_actionable_gain_at_256"] for row in comparisons
        if row["variant"] == variant], dtype=numpy.float64)
                    for variant in success)
    return {
        "de_1m_internal_comparisons": comparisons,
        "variant_success": success,
        "r3_document_summary_sufficient": sufficient,
        "document_distribution_bottleneck_supported": sufficient or strong,
        "best_r3_mean_actionable_gain_at_256": float(best_mean),
        "residual_summary_failure_band": bool(
            best_mean <= rule["residual_failure_band_upper"]),
        "teacher_trained_full_resolution_licensed": not sufficient,
        "stateful_policy_licensed": False,
        "configuration_opened_after_all_models_frozen": True,
        "internal_evaluation_opened_after_configuration_replay": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
    }


def evaluate(contract: dict[str, Any], materialization: dict[str, Any],
             split: dict[str, Any], external_ids: list[str],
             external_vectors: numpy.ndarray, summary: dict[str, Any],
             args: argparse.Namespace) -> tuple[
                 list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
            "R3 matched training query pool differs")
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    ambiguity_contract = ambiguity.planner.load_contract(
        THIS / "neuroute-representation-ambiguity.example.json")
    models = []
    args.model_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = load_summary_state(
            args.r3_summary_materialization_root, summary, seed, occupied)
        cache, manifest = ambiguity.locate_cache(
            args.parent_cache_root, seed,
            ambiguity_contract["cache_manifest_sha256"][str(seed)])
        shortlists = numpy.load(cache / manifest["outputs"]["shortlists"]["path"],
                                mmap_mode="r")
        scalar_features = numpy.load(
            cache / manifest["outputs"]["features"]["path"], mmap_mode="r")
        targets = numpy.load(cache / manifest["outputs"]["targets"]["path"],
                             mmap_mode="r")
        scalar_mean, scalar_deviation = ambiguity.feature_normalization(
            scalar_features)
        interactions = interaction_arrays(
            pool_vectors, shortlists, occupied, state,
            int(contract["training"]["interaction_batch_queries"]),
            args.interaction_cache_root / f"seed-{seed}")
        interaction_norm = interaction_normalization(interactions)
        summary_row = next(value for value in summary["seeds"]
                           if value["seed"] == seed)
        summary_hashes = {
            f"{value['role']}_sha256": value["sha256"]
            for value in summary_row["artifacts"]}
        for variant in contract["representations"]["variants"]:
            model_seed = seed ^ 0x13579BD ^ 8141
            path = args.model_root / f"model-{variant}-{seed}.npz"
            if path.is_file():
                _, saved_mean, saved_deviation, metadata = base.read_model(path)
                training = metadata.get("training", {})
                require(metadata.get("family") == "neuroute_r3_matched_model"
                        and metadata.get("seed") == seed
                        and metadata.get("variant") == variant
                        and metadata.get("model_seed") == model_seed
                        and metadata.get("training_query_count") == 8141
                        and metadata.get("contract_sha256") == sha256(args.contract)
                        and metadata.get("r3_summary_state") == summary_hashes
                        and training.get("parameter_count")
                        == planner.parameter_counts(contract)[variant]
                        and numpy.array_equal(saved_mean, scalar_mean)
                        and numpy.array_equal(saved_deviation, scalar_deviation),
                        "R3 matched resumable model differs")
                models.append({
                    "seed": seed, "variant": variant, "file": path.name,
                    "sha256": sha256(path), "metadata": metadata,
                })
                continue
            arrays, training_metrics = train_model(
                variant, pool_vectors, shortlists, scalar_features, targets,
                occupied, state, interactions, scalar_mean, scalar_deviation,
                interaction_norm, model_seed, contract)
            metadata = {
                "schema_version": 1,
                "family": "neuroute_r3_matched_model",
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
                "r3_summary_state": summary_hashes,
                "training": training_metrics,
            }
            digest = save_model(
                path, arrays, scalar_mean, scalar_deviation, metadata)
            models.append({
                "seed": seed, "variant": variant, "file": path.name,
                "sha256": digest, "metadata": metadata,
            })
        del addresses, index, occupied, prototypes, effective, members
        del state, shortlists, scalar_features, targets, interactions
        gc.collect()

    # All twelve models are serialized before configuration qrels are opened.
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
        state = load_summary_state(
            args.r3_summary_materialization_root, summary, seed, occupied)
        queries = numpy.asarray(data["queries"][configuration_positions],
                                dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        interactions = interaction_arrays(
            queries, shortlists, occupied, state,
            int(contract["training"]["interaction_batch_queries"]))
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
                    "R3 matched configuration model differs")
            scores = numpy_scores(
                variant, queries, shortlists, scalar_features, occupied, state,
                interactions, arrays, saved_mean, saved_deviation)
            configuration_rows.append(parent.calibration_row(
                variant, seed, shortlists, targets, scores, index["counts"],
                len(data["document_ids"]), 256))
        del addresses, index, occupied, prototypes, effective, members
        del state, shortlists, scalar_features, targets, interactions
        del training_features
        gc.collect()

    # Configuration replay is complete. Internal qrels are opened exactly once.
    internal_oracle, _ = scale.exact_oracle(
        data, internal_positions, contract["cascade"]["oracle_k"])
    protocol_contract = {
        **contract,
        "evaluation": {**contract["evaluation"], "candidate_mass_target": 0.1},
    }
    protocol = base.evaluation_contract(protocol_contract)
    internal_rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        state = load_summary_state(
            args.r3_summary_materialization_root, summary, seed, occupied)
        queries = numpy.asarray(data["queries"][internal_positions], dtype=numpy.float32)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"],
            len(data["document_ids"]), 1024,
            parent_contract["training"]["feature_query_batch_size"])
        interactions = interaction_arrays(
            queries, shortlists, occupied, state,
            int(contract["training"]["interaction_batch_queries"]))
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
                    "R3 matched internal model differs")
            scores = numpy_scores(
                variant, queries, shortlists, scalar_features, occupied, state,
                interactions, arrays, scalar_mean, scalar_deviation)
            orders[variant] = [prototype.ordered(scores[row], shortlists[row])
                               for row in range(len(shortlists))]
        for treatment in ["prototype_order",
                          *contract["representations"]["variants"],
                          "privileged_teacher"]:
            value = base.summarize_orders(
                treatment, orders[treatment], shortlists, addresses, index,
                data, internal_positions, internal_oracle, discounts, protocol)
            internal_rows.append({"dataset": "de-1m", "seed": seed, **value})
        del addresses, index, occupied, prototypes, effective, members
        del state, shortlists, scalar_features, targets, interactions
        gc.collect()
    return models, configuration_rows, internal_rows


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    materialization, split, external_ids, external_vectors, summary = (
        validate_activation(contract, args))
    models, configuration_rows, internal_rows = evaluate(
        contract, materialization, split, external_ids, external_vectors,
        summary, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_r3_matched_ladder_result",
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
        "configuration_rows": configuration_rows,
        "internal_rows": internal_rows,
        "decision": result_decision(internal_rows, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-r3-matched-ladder.example.json")
    for variant, expected in planner.parameter_counts(contract).items():
        arrays = initialized_arrays(variant, contract, 11)
        require(parameter_count(arrays) == expected,
                f"R3 matched model self-test differs: {variant}")
    occupied = numpy.asarray([1, 2], dtype=numpy.uint32)
    state = {
        "normalized_counts": numpy.zeros((2, 16), dtype=numpy.float32),
        "normalized_eigenvalue": numpy.zeros((2, 8), dtype=numpy.float32),
        "normalized_energy": numpy.zeros((2, 8), dtype=numpy.float32),
        "mean": numpy.zeros((2, 8, 384), dtype=numpy.float32),
        "variance": numpy.zeros((2, 8, 384), dtype=numpy.float32),
        "direction": numpy.zeros((2, 8, 384), dtype=numpy.float32),
    }
    state["mean"][0, 0, 0] = 0.5
    state["variance"][1, 1, 0] = 0.25
    state["direction"][1, 2, 0] = 1.0
    shortlists = numpy.asarray([[1, 2]], dtype=numpy.uint32)
    scalar = numpy.zeros((1, 2, 22), dtype=numpy.float32)
    query = numpy.zeros((1, 384), dtype=numpy.float32)
    query[0, 0] = 1.0
    interactions = interaction_arrays(query, shortlists, occupied, state, 1)
    require(interactions["mean_dot"][0, 0, 0] == 0.5
            and interactions["variance_projection"][0, 1, 1] == 0.25
            and interactions["direction_dot"][0, 1, 2] == 1.0,
            "R3 matched interaction self-test differs")
    fixed = {f"r3_{name}_{suffix}": numpy.zeros(8, dtype=numpy.float32)
             if suffix == "mean" else numpy.ones(8, dtype=numpy.float32)
             for name in ("mean_dot", "variance_projection", "direction_dot")
             for suffix in ("mean", "deviation")}
    for variant in contract["representations"]["variants"]:
        value = local_numpy(
            variant, shortlists, scalar, occupied, state, interactions,
            numpy.zeros(22, dtype=numpy.float32),
            numpy.ones(22, dtype=numpy.float32), fixed)
        require(value.shape == (1, 2, contract["models"][
            "local_input_dimensions"][variant]) and numpy.all(numpy.isfinite(value)),
                f"R3 matched local self-test differs: {variant}")
    print("NeuRoute R3 matched-ladder runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r3-matched-ladder.example.json")
    parser.add_argument("--r3-summary-result", type=Path)
    parser.add_argument("--r3-summary-evidence", type=Path)
    parser.add_argument("--r3-summary-materialization-root", type=Path)
    parser.add_argument("--matched-representation-result", type=Path)
    parser.add_argument("--matched-representation-evidence", type=Path)
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
    parser.add_argument("--interaction-cache-root", type=Path)
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
            parser.error("all R3 matched-ladder paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r3-matched-ladder: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
