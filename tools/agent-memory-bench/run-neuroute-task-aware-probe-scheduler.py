#!/usr/bin/env python3
"""Train and evaluate query-only schedulers over frozen NeuRoute postings."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import io
import json
import math
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_task_scheduler_planner",
               "plan-neuroute-task-aware-probe-scheduler.py")
width = load("neuroute_task_scheduler_width", "run-neuroute-width-scale-budget.py")
diagnostic = load("neuroute_task_scheduler_diagnostic",
                  "run-neuroute-router-mechanism-diagnostic.py")
scale = width.scale
trainer = width.trainer


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-task-aware-probe-scheduler.py",
        "run-neuroute-task-aware-probe-scheduler.py",
        "run-neuroute-width-scale-budget.py",
        "run-neuroute-frozen-scale-transfer.py",
        "run-neuroute-training-sanity.py",
        "diagnose-neuroute-v2-collisions.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual = {
        "diagnostic_result_sha256": sha256(args.diagnostic_result),
        "diagnostic_evidence_sha256": sha256(args.diagnostic_evidence),
        "width_result_sha256": sha256(args.width_result),
        "width_evidence_sha256": sha256(args.width_evidence),
        "width_materialization_sha256": sha256(args.width_materialization_root / "manifest.json"),
    }
    require(actual == contract["activation"],
            f"task-aware scheduler activation bytes differ: {actual!r}")
    mechanism = json.loads(args.diagnostic_result.read_text(encoding="utf-8"))
    mechanism_evidence = json.loads(args.diagnostic_evidence.read_text(encoding="utf-8"))
    result = json.loads(args.width_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.width_evidence.read_text(encoding="utf-8"))
    manifest = json.loads((args.width_materialization_root / "manifest.json").read_text(encoding="utf-8"))
    require(mechanism.get("family") == "neuroute_router_mechanism_diagnostic_result"
            and mechanism.get("decision", {}).get("scheduler_followup_activated") is True
            and mechanism_evidence.get("passed") is True,
            "task-aware scheduler mechanism gate differs")
    require(result.get("family") == "neuroute_width_scale_budget_quality_result"
            and evidence.get("passed") is True
            and manifest.get("family") == "neuroute_width_scale_budget_native_materialization"
            and manifest.get("quality_result_sha256") == actual["width_result_sha256"],
            "task-aware scheduler width parent differs")
    require(sha256(args.german_split_result)
            == result["activation"]["german_split_result_sha256"],
            "task-aware scheduler German split bytes differ")
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    require(len(split["training_query_ids"]) == contract["training"]["queries"]
            and len(split["configuration_selection_query_ids"]) == 76,
            "task-aware scheduler query partitions differ")
    return result, manifest, split, mechanism


def model_entries(result: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    keys = {(int(row["width"]), int(row["seed"])): row for row in result["models"]}
    entries = [keys[(bits, seed)] for bits in contract["routes"]["widths"]
               for seed in contract["routes"]["seeds"]]
    require(len(entries) == 6, "task-aware scheduler frozen model matrix differs")
    return entries


def load_models(entries: list[dict[str, Any]], root: Path) -> dict[tuple[int, int], dict[str, numpy.ndarray]]:
    result: dict[tuple[int, int], dict[str, numpy.ndarray]] = {}
    for entry in entries:
        path = root / entry["file"]
        require(path.is_file() and sha256(path) == entry["sha256"],
                f"task-aware scheduler model differs: {entry['width']}/{entry['seed']}")
        arrays, metadata = trainer.read_model(path)
        require(metadata.get("width") == entry["width"] and metadata.get("seed") == entry["seed"]
                and arrays["weight3"].shape == (entry["width"], 64),
                "task-aware scheduler model provenance differs")
        result[(entry["width"], entry["seed"])] = arrays
    return result


def infer_hidden(vectors: numpy.ndarray, arrays: dict[str, numpy.ndarray]) -> numpy.ndarray:
    values = (numpy.asarray(vectors, dtype=numpy.float32) - arrays["mean"]) / arrays["scale"]
    values = numpy.maximum(values @ arrays["weight1"].T + arrays["bias1"], 0.0)
    return numpy.maximum(values @ arrays["weight2"].T + arrays["bias2"], 0.0).astype(numpy.float32)


def address_signs(addresses: numpy.ndarray, bits: int) -> numpy.ndarray:
    shifts = numpy.arange(bits, dtype=numpy.uint32)
    return (((addresses.astype(numpy.uint32)[..., None] >> shifts) & 1).astype(numpy.float32)
            * 2.0 - 1.0)


def fit_head(hidden: numpy.ndarray, top_positions: numpy.ndarray, addresses: numpy.ndarray,
             posting_counts: numpy.ndarray, arrays: dict[str, numpy.ndarray],
             threshold: numpy.ndarray, treatment: str, contract: dict[str, Any]) -> dict[str, numpy.ndarray]:
    bits = arrays["weight3"].shape[0]
    signs = address_signs(addresses[top_positions], bits)
    discounts = (1.0 / numpy.log2(numpy.arange(top_positions.shape[1], dtype=numpy.float64) + 2.0))[None, :, None]
    weights = numpy.broadcast_to(discounts, signs.shape).copy()
    if treatment == "anchored_mass_aware":
        masses = posting_counts[addresses[top_positions]].astype(numpy.float64)
        weights /= numpy.maximum(masses[:, :, None], 1.0)
    means = (signs.astype(numpy.float64) * weights).sum(axis=1) / weights.sum(axis=1)
    clipped = float(contract["training"]["target_clip"])
    targets = numpy.arctanh(numpy.clip(means, -clipped, clipped))
    x = numpy.concatenate((hidden.astype(numpy.float64),
                           numpy.ones((hidden.shape[0], 1), dtype=numpy.float64)), axis=1)
    original = numpy.concatenate((arrays["weight3"].T,
                                  (arrays["bias3"] - threshold)[None, :]), axis=0).astype(numpy.float64)
    ridge = float(contract["training"]["ridge_anchor_coefficient"])
    solved = numpy.linalg.solve(x.T @ x + ridge * numpy.eye(x.shape[1]),
                                x.T @ targets + ridge * original)
    return {"weight": solved[:-1].T.astype(numpy.float32),
            "bias": solved[-1].astype(numpy.float32)}


def array_npy_bytes(value: numpy.ndarray) -> bytes:
    stream = io.BytesIO()
    numpy.lib.format.write_array(stream, numpy.asarray(value), allow_pickle=False)
    return stream.getvalue()


def save_head(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payloads = {**arrays, "metadata_json": numpy.asarray(json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True))}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(payloads):
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, array_npy_bytes(payloads[name]))


def read_head(path: Path) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    with numpy.load(path, allow_pickle=False) as stored:
        arrays = {name: stored[name] for name in ("weight", "bias")}
        metadata = json.loads(str(stored["metadata_json"].item()))
    return arrays, metadata


def route_entry(dataset: dict[str, Any], bits: int, seed: int) -> dict[str, Any]:
    route_id = f"width-{bits}-seed-{seed}"
    return next(row for row in dataset["routes"] if row["id"] == route_id)


def read_descriptor(root: Path, descriptor: dict[str, Any]) -> numpy.ndarray:
    return diagnostic.read_array(root, descriptor)


def requested_full_space(logits: numpy.ndarray, bits: int, count: int) -> numpy.ndarray:
    return numpy.asarray(diagnostic.probe.addresses(logits, bits, count), dtype=numpy.uint32)


def requested_occupied(logits: numpy.ndarray, occupied: numpy.ndarray,
                       signs: numpy.ndarray, counts: numpy.ndarray,
                       count: int, penalty: float) -> numpy.ndarray:
    scores = signs @ logits.astype(numpy.float32)
    if penalty:
        scores = scores - numpy.float32(penalty) * numpy.log1p(counts[occupied]).astype(numpy.float32)
    limit = min(count, occupied.size)
    if limit == occupied.size:
        pool = numpy.arange(occupied.size)
    else:
        boundary = numpy.partition(scores, scores.size - limit)[scores.size - limit]
        pool = numpy.flatnonzero(scores >= boundary)
    order = numpy.lexsort((occupied[pool], -scores[pool]))[:limit]
    return occupied[pool[order]].astype(numpy.uint32)


def orders(logits: numpy.ndarray, treatment: str, index: dict[str, Any], bits: int,
           maximum: int, penalty: float) -> list[numpy.ndarray]:
    occupied = numpy.flatnonzero(index["counts"] > 0).astype(numpy.uint32)
    signs = address_signs(occupied, bits)
    if treatment == "current_full_space":
        return [requested_full_space(row, bits, maximum) for row in logits]
    return [requested_occupied(row, occupied, signs, index["counts"], maximum, penalty)
            for row in logits]


def candidate_summary(index: dict[str, Any], requested: list[numpy.ndarray],
                      budgets: list[int], oracle: dict[int, numpy.ndarray], positions: list[int],
                      document_count: int, contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for budget in budgets:
        fractions, survivals = [], []
        digest = hashlib.sha256()
        for local, position in enumerate(positions):
            candidates, _, _ = scale.candidate_union(
                requested[local][:budget].tolist(), index,
                int(math.floor(document_count * contract["calibration"]["candidate_mass_target"])))
            survival = float(numpy.isin(oracle[position], candidates).sum()) / contract["cascade"]["oracle_k"]
            fractions.append(candidates.size / document_count)
            survivals.append(survival)
            scale.update_sequence(digest, local, candidates)
        rows.append({"probes": budget, "mean_candidate_fraction": float(numpy.mean(fractions)),
                     "maximum_candidate_fraction": float(numpy.max(fractions)),
                     "mean_raw_e5_top10_survival": float(numpy.mean(survivals)),
                     "minimum_per_query_raw_e5_top10_survival": float(numpy.min(survivals)),
                     "candidate_sequence_sha256": digest.hexdigest()})
    return rows


def select_calibration(rows: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["calibration"]
    passing = [row for row in rows
               if row["maximum_candidate_fraction"] <= rule["candidate_mass_target"]
               and row["mean_raw_e5_top10_survival"] >= rule["minimum_mean_raw_e5_top10_survival"]
               and row["minimum_per_query_raw_e5_top10_survival"]
               >= rule["minimum_per_query_raw_e5_top10_survival"]]
    if passing:
        selected = min(passing, key=lambda row: (row["probes"], row["mean_candidate_fraction"],
                                                  -row["mean_raw_e5_top10_survival"], row["mass_penalty"]))
        passed = True
    else:
        selected = min(rows, key=lambda row: (-row["mean_raw_e5_top10_survival"],
                                               row["mean_candidate_fraction"], row["probes"],
                                               row["mass_penalty"]))
        passed = False
    return {**selected, "calibration_gate_passed": passed}


def train_and_calibrate(contract: dict[str, Any], width_contract: dict[str, Any],
                        entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
                        manifest: dict[str, Any], split: dict[str, Any], args: argparse.Namespace) -> tuple[
                            list[dict[str, Any]], dict[tuple[int, int, str], dict[str, Any]],
                            dict[tuple[int, int, str], dict[str, numpy.ndarray]]]:
    config = next(row for row in width_contract["scales"] if row["id"] == "de-25k")
    data = scale.load_scale(config, args.de_25k_e5_root, args.de_25k_input_root)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [by_id[value] for value in split["training_query_ids"]]
    oracle, _ = scale.exact_oracle(data, positions, 100)
    top100 = numpy.stack([oracle[position] for position in positions])
    manifest_dataset = next(row for row in manifest["datasets"] if row["id"] == "de-25k")
    calibration_rows: list[dict[str, Any]] = []
    selected: dict[tuple[int, int, str], dict[str, Any]] = {}
    learned: dict[tuple[int, int, str], dict[str, numpy.ndarray]] = {}
    for entry in entries:
        bits, seed = int(entry["width"]), int(entry["seed"])
        arrays = models[(bits, seed)]
        route = route_entry(manifest_dataset, bits, seed)
        route_root = args.width_materialization_root / "de-25k" / route["id"]
        addresses = numpy.asarray(read_descriptor(route_root, route["document_addresses"]), dtype=numpy.uint32)
        threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
        index = scale.build_index(addresses, bits)
        hidden = infer_hidden(data["queries"][positions], arrays)
        original_logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
        for treatment in contract["training"]["heads"]:
            head = fit_head(hidden, top100, addresses, index["counts"], arrays, threshold,
                            treatment, contract)
            metadata = {"schema_version": 1, "family": "neuroute_task_aware_query_head",
                        "contract_sha256": sha256(args.contract), "width": bits, "seed": seed,
                        "treatment": treatment, "source_model_sha256": entry["sha256"],
                        "document_addresses_sha256": route["document_addresses"]["sha256"],
                        "training_query_ids_sha256": scale.hash_ids(
                            numpy.asarray(split["training_query_ids"], dtype=object))}
            path = args.head_root / f"head-{treatment}-{bits}bit-{seed}.npz"
            save_head(path, head, metadata)
            replay, replay_metadata = read_head(path)
            require(replay_metadata == metadata and all(numpy.array_equal(replay[name], head[name])
                                                         for name in head),
                    "task-aware scheduler head serialization differs")
            learned[(bits, seed, treatment)] = head
        logits_by_treatment = {
            "current_full_space": original_logits,
            "occupied_logit": original_logits,
            "occupied_mass_aware": original_logits,
            "anchored_reachability": hidden @ learned[(bits, seed, "anchored_reachability")]["weight"].T
                                      + learned[(bits, seed, "anchored_reachability")]["bias"],
            "anchored_mass_aware": hidden @ learned[(bits, seed, "anchored_mass_aware")]["weight"].T
                                    + learned[(bits, seed, "anchored_mass_aware")]["bias"],
        }
        for treatment in contract["treatments"]:
            penalties = (contract["calibration"]["mass_penalty_grid"]
                         if treatment in ("occupied_mass_aware", "anchored_mass_aware") else [0.0])
            treatment_rows = []
            for penalty in penalties:
                requested = orders(logits_by_treatment[treatment], treatment, index, bits,
                                   max(contract["calibration"]["probe_budgets"]), float(penalty))
                measured = candidate_summary(index, requested,
                                             contract["calibration"]["probe_budgets"],
                                             oracle, positions, len(data["document_ids"]), contract)
                treatment_rows.extend({"width": bits, "seed": seed, "treatment": treatment,
                                       "mass_penalty": float(penalty), **row} for row in measured)
            calibration_rows.extend(treatment_rows)
            selected[(bits, seed, treatment)] = select_calibration(treatment_rows, contract)
        del addresses, index, hidden, original_logits
        gc.collect()
    del data, oracle, top100
    gc.collect()
    return calibration_rows, selected, learned


def evaluate_requested(data: dict[str, Any], positions: list[int], requested: list[numpy.ndarray],
                       index: dict[str, Any], oracle: dict[int, numpy.ndarray],
                       full_ndcg: dict[int, float], contract: dict[str, Any]) -> dict[str, Any]:
    cascade = contract["cascade"]
    digests = {name: hashlib.sha256() for name in ("candidate", "hamming", "adc", "exact")}
    rows = []
    for local, position in enumerate(positions):
        candidates, accepted, requested_entries = scale.candidate_union(
            requested[local].tolist(), index,
            int(math.floor(len(data["document_ids"]) * contract["evaluation"]["candidate_mass_target"])))
        require(candidates.size > 0, "task-aware scheduler produced an empty candidate set")
        xor = numpy.bitwise_xor(data["document_codes"][candidates], data["query_codes"][position])
        distances = POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
        local_hamming = scale.select_smallest(distances, data["document_ids"][candidates],
                                               cascade["hamming_limit"])
        hamming = candidates[local_hamming]
        bits = numpy.unpackbits(data["document_codes"][hamming], axis=1, bitorder="little")
        table = (data["query_projection"][position, :, None] - data["adc_centroids"]) ** 2
        adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
        local_adc = scale.select_smallest(adc_distances, data["document_ids"][hamming],
                                          cascade["adc_limit"])
        adc = hamming[local_adc]
        exact_scores = numpy.asarray((data["documents"][adc] * data["queries"][position]).sum(axis=1),
                                     dtype=numpy.float32)
        exact = adc[scale.select_largest(exact_scores, data["document_ids"][adc],
                                         cascade["result_k"])]
        for name, values in (("candidate", candidates), ("hamming", hamming),
                             ("adc", adc), ("exact", exact)):
            scale.update_sequence(digests[name], local, values)
        target = oracle[position]
        exact_ndcg = scale.ndcg(data, position, exact)
        rows.append({"query_id": str(data["query_ids"][position]),
                     "source_query_position": int(position),
                     "requested_address_count": int(requested[local].size),
                     "requested_address_sha256": scale.sequence_sha256(requested[local]),
                     "accepted_probe_count": len(accepted), "posting_entries_requested": requested_entries,
                     "candidate_count": int(candidates.size),
                     "raw_e5_oracle_survival": float(numpy.isin(target, candidates).sum()) / cascade["oracle_k"],
                     "hamming_e5_oracle_survival": float(numpy.isin(target, hamming).sum()) / cascade["oracle_k"],
                     "adc64_e5_oracle_survival": float(numpy.isin(target, adc).sum()) / cascade["oracle_k"],
                     "exact64_ndcg_at_10": exact_ndcg,
                     "full_exact_e5_ndcg_at_10": full_ndcg[position],
                     "candidate_sha256": scale.sequence_sha256(candidates),
                     "hamming_sha256": scale.sequence_sha256(hamming),
                     "adc_sha256": scale.sequence_sha256(adc),
                     "exact_sha256": scale.sequence_sha256(exact)})
    numeric = ("accepted_probe_count", "posting_entries_requested", "candidate_count",
               "raw_e5_oracle_survival", "hamming_e5_oracle_survival",
               "adc64_e5_oracle_survival", "exact64_ndcg_at_10", "full_exact_e5_ndcg_at_10")
    metrics = {name: float(numpy.mean([row[name] for row in rows], dtype=numpy.float64))
               for name in numeric}
    metrics["candidate_fraction"] = metrics["candidate_count"] / len(data["document_ids"])
    metrics["exact64_ndcg_retention_vs_full_e5"] = (
        metrics["exact64_ndcg_at_10"] / metrics["full_exact_e5_ndcg_at_10"]
        if metrics["full_exact_e5_ndcg_at_10"] else 1.0)
    return {"query_count": len(rows), "metrics": metrics, "queries": rows,
            **{f"{name}_sequence_sha256": value.hexdigest() for name, value in digests.items()}}


def evaluate_all(contract: dict[str, Any], width_contract: dict[str, Any],
                 entries: list[dict[str, Any]], models: dict[tuple[int, int], dict[str, numpy.ndarray]],
                 manifest: dict[str, Any], split: dict[str, Any],
                 selected: dict[tuple[int, int, str], dict[str, Any]],
                 learned: dict[tuple[int, int, str], dict[str, numpy.ndarray]],
                 args: argparse.Namespace) -> list[dict[str, Any]]:
    datasets = []
    for config in width_contract["scales"]:
        prefix = config["id"].replace("-", "_")
        data = scale.load_scale(config, getattr(args, f"{prefix}_e5_root"),
                                getattr(args, f"{prefix}_input_root"))
        by_id = {value: index for index, value in enumerate(data["query_ids"])}
        positions = [by_id[value] for value in split["configuration_selection_query_ids"]]
        oracle, full_ndcg = scale.exact_oracle(data, positions, contract["cascade"]["oracle_k"])
        manifest_dataset = next(row for row in manifest["datasets"] if row["id"] == config["id"])
        dataset_rows = []
        for entry in entries:
            bits, seed = int(entry["width"]), int(entry["seed"])
            arrays = models[(bits, seed)]
            route = route_entry(manifest_dataset, bits, seed)
            route_root = args.width_materialization_root / config["id"] / route["id"]
            addresses = numpy.asarray(read_descriptor(route_root, route["document_addresses"]), dtype=numpy.uint32)
            index = scale.build_index(addresses, bits)
            hidden = infer_hidden(data["queries"][positions], arrays)
            threshold = numpy.asarray(route["threshold"], dtype=numpy.float32)
            original_logits = hidden @ arrays["weight3"].T + arrays["bias3"] - threshold
            frozen_logits = numpy.asarray(read_descriptor(route_root, route["query_logits"]), dtype=numpy.float32)
            require(numpy.array_equal(original_logits.astype(numpy.float32), frozen_logits),
                    f"task-aware scheduler original query logits differ: {config['id']}/{bits}/{seed}")
            logits_by_treatment = {
                "current_full_space": original_logits,
                "occupied_logit": original_logits,
                "occupied_mass_aware": original_logits,
                "anchored_reachability": hidden @ learned[(bits, seed, "anchored_reachability")]["weight"].T
                                          + learned[(bits, seed, "anchored_reachability")]["bias"],
                "anchored_mass_aware": hidden @ learned[(bits, seed, "anchored_mass_aware")]["weight"].T
                                        + learned[(bits, seed, "anchored_mass_aware")]["bias"],
            }
            for treatment in contract["treatments"]:
                choice = selected[(bits, seed, treatment)]
                roles_by_budget: dict[int, list[str]] = {
                    contract["evaluation"]["fixed_mechanism_probe_budget"]: ["fixed_256"]}
                roles_by_budget.setdefault(int(choice["probes"]), []).append("calibration_selected")
                maximum = max(roles_by_budget)
                requested = orders(logits_by_treatment[treatment], treatment, index, bits,
                                   maximum, float(choice["mass_penalty"]))
                for budget, roles in sorted(roles_by_budget.items()):
                    measured = evaluate_requested(data, positions,
                                                  [row[:budget] for row in requested], index,
                                                  oracle, full_ndcg, contract)
                    dataset_rows.append({"width": bits, "seed": seed, "model_sha256": entry["sha256"],
                                         "treatment": treatment, "mass_penalty": choice["mass_penalty"],
                                         "probes": budget, "budget_roles": roles,
                                         "calibration_gate_passed": choice["calibration_gate_passed"],
                                         "document_addresses_sha256": route["document_addresses"]["sha256"],
                                         **measured})
            del addresses, index, hidden, original_logits, frozen_logits
            gc.collect()
        datasets.append({"id": config["id"], "document_count": len(data["document_ids"]),
                         "query_count": len(positions),
                         "configuration_query_ids_sha256": scale.hash_ids(
                             numpy.asarray(split["configuration_selection_query_ids"], dtype=object)),
                         "rows": dataset_rows})
        del data, oracle, full_ndcg
        gc.collect()
    return datasets


def decision(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    checks = []
    for dataset in datasets:
        for row in dataset["rows"]:
            if rule["evaluated_role"] not in row["budget_roles"]:
                continue
            metrics = row["metrics"]
            checks.append({"dataset": dataset["id"], "width": row["width"], "seed": row["seed"],
                           "treatment": row["treatment"], "probes": row["probes"],
                           "candidate_pass": metrics["candidate_fraction"]
                           <= rule["maximum_candidate_fraction"],
                           "survival_pass": metrics["adc64_e5_oracle_survival"]
                           >= rule["minimum_adc64_e5_oracle_survival"],
                           "quality_pass": metrics["exact64_ndcg_retention_vs_full_e5"]
                           >= rule["minimum_exact64_ndcg_retention_vs_full_e5"]})
    by_treatment = {}
    for treatment in contract["treatments"]:
        rows = [row for row in checks if row["treatment"] == treatment]
        by_treatment[treatment] = bool(rows) and all(
            row["candidate_pass"] and row["survival_pass"] and row["quality_pass"] for row in rows)
    passed = any(by_treatment.values())
    return {"treatment_quality_pass": by_treatment, "native_confirmation_licensed": passed,
            "production_selection_licensed": False, "checks": checks}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    width_result, manifest, split, mechanism = validate_activation(contract, args)
    width_contract = width.planner.load_contract(THIS / "neuroute-width-scale-budget.example.json")
    require(width_result["contract_sha256"] == sha256(THIS / "neuroute-width-scale-budget.example.json"),
            "task-aware scheduler width contract differs")
    entries = model_entries(width_result, contract)
    models = load_models(entries, args.width_model_root)
    calibration, selected, learned = train_and_calibrate(
        contract, width_contract, entries, models, manifest, split, args)
    datasets = evaluate_all(contract, width_contract, entries, models, manifest, split,
                            selected, learned, args)
    heads = []
    for entry in entries:
        for treatment in contract["training"]["heads"]:
            path = args.head_root / f"head-{treatment}-{entry['width']}bit-{entry['seed']}.npz"
            arrays, metadata = read_head(path)
            heads.append({"width": entry["width"], "seed": entry["seed"],
                          "treatment": treatment, "file": path.name, "sha256": sha256(path),
                          "source_model_sha256": entry["sha256"],
                          "weight_shape": list(arrays["weight"].shape),
                          "bias_shape": list(arrays["bias"].shape), "metadata": metadata})
    output = {"schema_version": 1, "family": "neuroute_task_aware_probe_scheduler_result",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
              "activation": contract["activation"], "source_files_sha256": source_hashes(),
              "german_split_result_sha256": sha256(args.german_split_result),
              "training_query_ids_sha256": scale.hash_ids(numpy.asarray(split["training_query_ids"], dtype=object)),
              "configuration_query_ids_sha256": scale.hash_ids(
                  numpy.asarray(split["configuration_selection_query_ids"], dtype=object)),
              "mechanism_qualifying_widths": mechanism["decision"]["qualifying_widths"],
              "matrix": planner.plan(contract), "heads": heads, "calibration": calibration,
              "selected": [{"width": key[0], "seed": key[1], "treatment": key[2], **value}
                           for key, value in sorted(selected.items())],
              "datasets": datasets, "decision": decision(datasets, contract)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-task-aware-probe-scheduler.example.json")
    addresses = numpy.asarray([0, 1, 2, 3], dtype=numpy.uint32)
    signs = address_signs(addresses, 2)
    require(signs.tolist() == [[-1.0, -1.0], [1.0, -1.0], [-1.0, 1.0], [1.0, 1.0]],
            "task-aware scheduler address signs differ")
    index = scale.build_index(numpy.asarray([0, 0, 1, 3], dtype=numpy.uint32), 2)
    ranked = requested_occupied(numpy.asarray([2.0, -1.0], dtype=numpy.float32),
                                numpy.asarray([0, 1, 3], dtype=numpy.uint32),
                                signs[[0, 1, 3]], index["counts"], 3, 0.0)
    require(ranked.tolist() == [1, 3, 0], "task-aware scheduler occupied ordering differs")
    require(planner.plan(contract)["learned_query_heads"] == 12,
            "task-aware scheduler matrix differs")
    print("NeuRoute task-aware probe scheduler self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-task-aware-probe-scheduler.example.json")
    parser.add_argument("--diagnostic-result", type=Path)
    parser.add_argument("--diagnostic-evidence", type=Path)
    parser.add_argument("--width-result", type=Path)
    parser.add_argument("--width-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--width-model-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale_id in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale_id}-e5-root", type=Path)
        parser.add_argument(f"--{scale_id}-input-root", type=Path)
    parser.add_argument("--head-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all task-aware scheduler paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, MemoryError) as error:
        print(f"run-neuroute-task-aware-probe-scheduler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
