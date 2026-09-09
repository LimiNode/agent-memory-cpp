#!/usr/bin/env python3
"""Replay independent and prefix-compatible 12/14-to-16-bit routers."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]
PREFIX_FACTORS = [1, 2, 4]
PREFIX_WIDTHS = [12, 14]


def prefix_treatment(width: int, factor: int) -> str:
    return f"same_head_prefix{width}_beam_{factor}x"


def independent_treatment(width: int) -> str:
    return f"independent_{width}bit_mean_association"


def treatment_family(treatment: str) -> str:
    for width in PREFIX_WIDTHS:
        if treatment.startswith(f"same_head_prefix{width}_"):
            return f"same_head_prefix{width}"
    return treatment


def configuration_open(rows: list[dict[str, Any]], gates: dict[str, Any]
                       ) -> dict[str, Any]:
    passing = [row for row in rows if row["passes_registered_gate"]]
    if passing:
        return min(passing, key=lambda row: (row["address_budget"],
            row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"]))
    boundary = max(int(row["address_budget"]) for row in rows)
    controls = [row for row in rows if int(row["address_budget"]) == boundary]
    return min(controls, key=lambda row: (
        replay.gate_distance(row, gates),
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))


NATIVE_TREATMENTS = ["direct_same_head_16bit_logits",
    *[prefix_treatment(width, factor) for width in PREFIX_WIDTHS
      for factor in PREFIX_FACTORS],
    *[independent_treatment(width) for width in PREFIX_WIDTHS]]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


training = load("neuroute_hierarchy_training",
                "run-neuroute-training-sufficient-router.py")
bakeoff = training.bakeoff
replay = training.replay
exact = training.exact
require = training.require
sha256 = training.sha256
canonical = training.canonical


def source_hashes() -> dict[str, str]:
    names = ("run-neuroute-12-to-16-hierarchy-replay.py",
             "run-neuroute-training-sufficient-router.py",
             "run-neuroute-shortlist-generator-bakeoff.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "run-neuroute-width-scale-budget.py",
             "run-neuroute-frozen-scale-transfer.py",
             "run-neuroute-dynamic-false-positive-v3.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


replay.source_hashes = source_hashes
bakeoff.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_12_14_to_16_hierarchy_replay" and
            value["topology"]["independent_widths"] == PREFIX_WIDTHS and
            value["topology"]["same_head_prefix_widths"] == PREFIX_WIDTHS and
            value["topology"]["same_head_prefix_expansion_factors"] ==
            PREFIX_FACTORS and value["native_treatments"] ==
            NATIVE_TREATMENTS and value["shortlist_budgets"] ==
            [1024, 2048, 4096, 8192] and
            value["partition"]["confirmation_is_not_pristine"] is True and
            value["decision"]["confirmation_open"] ==
            "minimum_passing_budget_per_treatment_else_8192_boundary" and
            value["decision"]["production_selection_forbidden"] is True,
            "12/14-to-16 hierarchy contract differs")
    return value


def load_width_module() -> Any:
    return load("neuroute_hierarchy_width", "run-neuroute-width-scale-budget.py")


def verified_array(root: Path, descriptor: dict[str, Any], dtype: str,
                   shape: tuple[int, ...] | None = None) -> np.ndarray:
    path = root / descriptor["file"]
    require(path.stat().st_size == int(descriptor.get("bytes",
        int(np.prod(descriptor["shape"])) * np.dtype(dtype).itemsize)) and
        sha256(path) == descriptor["sha256"],
        f"hierarchy payload differs: {descriptor.get('role', path.name)}")
    values = np.fromfile(path, dtype=dtype)
    expected = tuple(descriptor["shape"]) if shape is None else shape
    require(values.size == int(np.prod(expected)),
            "hierarchy payload shape differs")
    return values.reshape(expected)


def width_artifact(width: Any, width_result: dict[str, Any],
                   width_dataset: dict[str, Any], model_root: Path,
                   materialization_root: Path, seed: int, bits: int,
                   queries: np.ndarray) -> tuple[np.ndarray, np.ndarray,
                                                  dict[str, float],
                                                  dict[str, Any]]:
    model = next(row for row in width_result["models"]
                 if row["seed"] == seed and row["width"] == bits)
    model_path = model_root / model["file"]
    require(model_path.is_file() and sha256(model_path) == model["sha256"],
            "hierarchy width model differs")
    route = next(row for row in width_dataset["routes"]
                 if row["seed"] == seed and row["width"] == bits)
    route_root = materialization_root / width_dataset["id"] / route["id"]
    addresses = verified_array(route_root, route["document_addresses"],
                               "<u4", (1000000,))
    stored_logits = verified_array(route_root, route["query_logits"], "<f4",
                                   (76, bits))
    result_rows = [row for row in next(dataset for dataset in
        width_result["datasets"] if dataset["id"] == "de-1m")["rows"]
        if row["seed"] == seed and row["width"] == bits]
    require(result_rows and all(np.array_equal(np.asarray(row["threshold"],
        dtype=np.float32), np.asarray(route["threshold"], dtype=np.float32))
        for row in result_rows), "hierarchy width threshold differs")
    arrays, _ = width.trainer.read_model(model_path)
    threshold = np.asarray(route["threshold"], dtype=np.float32)
    started = time.perf_counter()
    config_logits = width.scale.infer_batched(queries[:76], arrays) - threshold
    config_ms = (time.perf_counter() - started) * 1000.0 / 76.0
    started = time.perf_counter()
    internal_logits = width.scale.infer_batched(queries[76:], arrays) - threshold
    internal_ms = (time.perf_counter() - started) * 1000.0 / 76.0
    maximum_error = float(np.max(np.abs(config_logits - stored_logits)))
    require(maximum_error <= 1.0e-6,
            "hierarchy configuration inference differs")
    return (addresses, np.concatenate((config_logits, internal_logits), axis=0),
        {"configuration": config_ms, "internal": internal_ms},
        {"bits": bits, "model_sha256": model["sha256"],
         "model_bytes": model_path.stat().st_size,
         "document_addresses_sha256": route["document_addresses"]["sha256"],
         "query_logits_sha256": route["query_logits"]["sha256"],
         "maximum_configuration_logit_error": maximum_error})


def allocate_orders(treatments: list[str], budgets: list[int]
                    ) -> dict[str, dict[int, np.ndarray]]:
    return {treatment: {budget: np.empty((152, budget), dtype=np.uint32)
        for budget in budgets} for treatment in treatments}


def generate_orders(occupied: np.ndarray, counts: np.ndarray,
                    doc_rows: np.ndarray,
                    independent_addresses: dict[int, np.ndarray],
                    independent_logits: dict[int, np.ndarray],
                    logits16: np.ndarray,
                    inference: dict[int, dict[str, float]], budgets: list[int]
                    ) -> tuple[dict[str, dict[int, np.ndarray]],
                               dict[str, dict[str, dict[int, float]]],
                               dict[str, dict[str, dict[int, dict[str, float]]]]]:
    treatments = list(NATIVE_TREATMENTS)
    orders = allocate_orders(treatments, budgets)
    elapsed = {treatment: {partition: {budget: 0.0 for budget in budgets}
        for partition in ("configuration", "internal")}
        for treatment in treatments}
    work = {treatment: {partition: {budget: {"prefixes_scored": 0.0,
        "fine_addresses_scored": 0.0} for budget in budgets}
        for partition in ("configuration", "internal")}
        for treatment in treatments}
    prefixes = {}
    for width in PREFIX_WIDTHS:
        code_count = 1 << width
        codes = np.arange(code_count, dtype=np.uint32)
        shifts = np.arange(width, dtype=np.uint32)
        signs = (((codes[:, None] >> shifts[None, :]) & 1).astype(
            np.float32) * 2.0 - 1.0)
        row_prefix = occupied & np.uint32(code_count - 1)
        suffix_shifts = np.arange(width, 16, dtype=np.uint32)
        suffix_signs = (((occupied[:, None] >> suffix_shifts[None, :]) & 1
                        ).astype(np.float32) * 2.0 - 1.0)
        prefixes[width] = {"codes": codes, "signs": signs,
            "row_prefix": row_prefix, "suffix_signs": suffix_signs,
            "child_counts": np.bincount(row_prefix, minlength=code_count),
            "children_per_prefix": 1 << (16 - width)}
    shifts16 = np.arange(16, dtype=np.uint32)
    signs16 = (((occupied[:, None] >> shifts16[None, :]) & 1).astype(
               np.float32) * 2.0 - 1.0)
    require(np.all(counts > 0) and len(doc_rows) == 1000000 and
            all(len(independent_addresses[width]) == 1000000
                for width in PREFIX_WIDTHS) and
            np.all(doc_rows < len(occupied)),
            "hierarchy topology arrays differ")
    for query in range(152):
        partition = "configuration" if query < 76 else "internal"
        started = time.perf_counter()
        direct_scores = signs16 @ logits16[query]
        direct_score_ms = (time.perf_counter() - started) * 1000.0
        for budget in budgets:
            started = time.perf_counter()
            orders["direct_same_head_16bit_logits"][budget][query] = (
                replay.top_order(direct_scores, occupied, budget))
            elapsed["direct_same_head_16bit_logits"][partition][budget] += (
                inference[16][partition] + direct_score_ms +
                (time.perf_counter() - started) * 1000.0)
            work["direct_same_head_16bit_logits"][partition][budget][
                "fine_addresses_scored"] += len(occupied)

        for width in PREFIX_WIDTHS:
            current = prefixes[width]
            started = time.perf_counter()
            prefix_scores = current["signs"] @ logits16[query, :width]
            prefix_order = np.lexsort((current["codes"], -prefix_scores))
            prefix_rank = np.empty(len(current["codes"]), dtype=np.int32)
            prefix_rank[prefix_order] = np.arange(len(current["codes"]),
                                                  dtype=np.int32)
            cumulative_children = np.cumsum(
                current["child_counts"][prefix_order])
            prefix_common_ms = (time.perf_counter() - started) * 1000.0
            for factor in PREFIX_FACTORS:
                treatment = prefix_treatment(width, factor)
                for budget in budgets:
                    started = time.perf_counter()
                    minimum_prefixes = int(np.searchsorted(cumulative_children,
                                                           budget) + 1)
                    prefix_count = min(len(current["codes"]), max(
                        minimum_prefixes, int(np.ceil(budget / current[
                            "children_per_prefix"])) * factor))
                    candidates = np.flatnonzero(prefix_rank[current[
                        "row_prefix"]] < prefix_count).astype(np.uint32)
                    candidate_scores = (prefix_scores[current[
                        "row_prefix"][candidates]] + current["suffix_signs"][
                            candidates] @ logits16[query, width:])
                    relative = replay.top_order(candidate_scores,
                                                occupied[candidates], budget)
                    orders[treatment][budget][query] = candidates[relative]
                    elapsed[treatment][partition][budget] += (
                        inference[16][partition] + prefix_common_ms +
                        (time.perf_counter() - started) * 1000.0)
                    work[treatment][partition][budget][
                        "prefixes_scored"] += prefix_count
                    work[treatment][partition][budget][
                        "fine_addresses_scored"] += len(candidates)

            treatment = independent_treatment(width)
            started = time.perf_counter()
            independent_prefix_scores = (current["signs"] @
                                         independent_logits[width][query])
            child_scores = (np.bincount(doc_rows,
                weights=independent_prefix_scores[
                    independent_addresses[width]], minlength=len(occupied)) /
                counts)
            for budget in budgets:
                selected_started = time.perf_counter()
                orders[treatment][budget][query] = replay.top_order(
                    child_scores, occupied, budget)
                elapsed[treatment][partition][budget] += (
                    inference[width][partition] +
                    (selected_started - started) * 1000.0 +
                    (time.perf_counter() - selected_started) * 1000.0)
                work[treatment][partition][budget][
                    "prefixes_scored"] += len(current["codes"])
                work[treatment][partition][budget][
                    "fine_addresses_scored"] += len(occupied)
    for treatment in treatments:
        for partition in ("configuration", "internal"):
            for budget in budgets:
                elapsed[treatment][partition][budget] /= 76.0
                for key in work[treatment][partition][budget]:
                    work[treatment][partition][budget][key] /= 76.0
    return orders, elapsed, work


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    width = load_width_module()
    width_result = json.loads(args.width_result.read_text(encoding="utf-8"))
    width_manifest = json.loads(args.width_materialization_manifest.read_text(
        encoding="utf-8"))
    require(width_result.get("family") ==
            "neuroute_width_scale_budget_quality_result" and
            width_manifest.get("family") ==
            "neuroute_width_scale_budget_native_materialization",
            "hierarchy width parents differ")
    width_dataset = next(row for row in width_manifest["datasets"]
                         if row["id"] == "de-1m")
    config_value = json.loads(args.configuration_protocol.read_text(
        encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    internal_value = dict(config_value)
    internal_value["partition"] = "reused_confirmation"
    internal_value["requests"] = parent["requests"]
    require(len(config_value["requests"]) == len(parent["requests"]) == 76,
            "hierarchy partitions differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    data = exact.load_data(parent)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    doc_rows_by_seed = exact.layout_doc_rows(args.layout_manifest)
    request_rows = sorted(list(config_value["requests"]) +
        list(parent["requests"]), key=lambda row: int(row["request"]))
    native_positions = [int(row["native_query"]) for row in request_rows]
    oracle_native, _ = exact.scale.exact_oracle(data, native_positions, 10)
    oracle = {request: oracle_native[native]
              for request, native in enumerate(native_positions)}
    budgets = list(map(int, contract["shortlist_budgets"]))
    all_orders: dict[str, dict[int, dict[int, np.ndarray]]] = {}
    all_timings: dict[str, dict[str, dict[int, dict[int, float]]]] = {}
    all_work: dict[str, dict[str, dict[int, dict[int, dict[str, float]]]]] = {}
    model_bytes: dict[str, dict[int, int]] = {treatment: {}
                                               for treatment in NATIVE_TREATMENTS}
    common = {}
    occupied_counts = {}
    topology_audit = {}
    model_hashes = {}
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"]
                           if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        by_role = {row["role"]: row for row in layout_seed["mappings"]}
        occupied = verified_array(root, by_role["occupied_addresses"], "<u4")
        offsets = verified_array(root, by_role["address_offsets"], "<u4")
        counts = verified_array(root, by_role["address_counts"], "<u4")
        document_to_physical = verified_array(root,
            by_role["document_to_physical"], "<u4", (1000000,))
        queries = verified_array(root, by_role["query_vectors"], "<f4",
                                 (152, 384))
        global_rows = verified_array(root, by_role["shortlist_rows"], "<u4",
                                     (152, 1024))
        scalar_features = verified_array(root, by_role["scalar_features"],
                                         "<f4", (152, 1024, 22))
        document_rows = np.searchsorted(offsets, document_to_physical,
                                        side="right") - 1
        require(np.array_equal(document_rows, doc_rows_by_seed[seed]) and
                np.all(document_to_physical < offsets[document_rows] +
                       counts[document_rows]),
                "hierarchy current document topology differs")
        independent_addresses = {}
        independent_logits = {}
        inference = {}
        independent_audits = {}
        for bits in PREFIX_WIDTHS:
            addresses, logits, current_inference, audit = width_artifact(width,
                width_result, width_dataset, args.width_model_root,
                args.width_materialization_manifest.parent, seed, bits, queries)
            independent_addresses[bits] = addresses
            independent_logits[bits] = logits
            inference[bits] = current_inference
            independent_audits[bits] = audit
        addresses16, logits16, inference16, audit16 = width_artifact(width,
            width_result, width_dataset, args.width_model_root,
            args.width_materialization_manifest.parent, seed, 16, queries)
        current_addresses = occupied[document_rows]
        require(np.array_equal(current_addresses, addresses16),
                "current R4 layout is not the retained width16 topology")
        inference[16] = inference16
        seed_orders, seed_timings, seed_work = generate_orders(occupied, counts,
            document_rows, independent_addresses, independent_logits, logits16,
            inference, budgets)
        for treatment, by_budget in seed_orders.items():
            all_orders.setdefault(treatment, {})[seed] = by_budget
        for treatment, by_partition in seed_timings.items():
            target = all_timings.setdefault(treatment, {
                "configuration": {}, "internal": {}})
            for partition in target:
                target[partition][seed] = by_partition[partition]
        for treatment, by_partition in seed_work.items():
            target = all_work.setdefault(treatment, {
                "configuration": {}, "internal": {}})
            for partition in target:
                target[partition][seed] = by_partition[partition]
        common[seed] = {"counts": counts, "global_rows": global_rows,
            "global_scores": np.asarray(scalar_features[:, :, 8],
                                        dtype=np.float32),
            "actionable_rows": training.actionable_rows(
                oracle, doc_rows_by_seed[seed])}
        occupied_counts[seed] = len(occupied)
        topology_audit[str(seed)] = {
            **{f"independent_{bits}bit": independent_audits[bits]
               for bits in PREFIX_WIDTHS},
            "same_head_16bit": audit16,
            "current_document_addresses_sha256": hashlib.sha256(
                current_addresses.astype("<u4", copy=False).tobytes()).hexdigest(),
            "current_matches_width16": True}
        for bits in PREFIX_WIDTHS:
            model_hashes[f"{bits}/{seed}"] = independent_audits[bits][
                "model_sha256"]
            model_bytes[independent_treatment(bits)][seed] = int(
                independent_audits[bits]["model_bytes"] +
                independent_addresses[bits].nbytes)
        model_hashes[f"16/{seed}"] = audit16["model_sha256"]
        model_bytes["direct_same_head_16bit_logits"][seed] = int(
            audit16["model_bytes"])
        for bits in PREFIX_WIDTHS:
            for factor in PREFIX_FACTORS:
                model_bytes[prefix_treatment(bits, factor)][seed] = int(
                    audit16["model_bytes"])
        gc.collect()

    offline = {}
    for treatment, by_seed in all_orders.items():
        offline[treatment] = {}
        for budget in budgets:
            orders = {seed: by_seed[seed][budget] for seed in SEEDS}
            timings = {seed: all_timings[treatment]["configuration"][seed]
                       for seed in SEEDS}
            internal_timings = {seed: all_timings[treatment]["internal"][seed]
                                for seed in SEEDS}
            configuration_metrics = training.generator_metrics(orders, common,
                budget, range(76), timings, model_bytes[treatment])
            internal_metrics = training.generator_metrics(orders, common,
                budget, range(76, 152), internal_timings,
                model_bytes[treatment])
            for key in ("prefixes_scored", "fine_addresses_scored"):
                configuration_metrics[f"mean_{key}"] = float(np.mean([
                    all_work[treatment]["configuration"][seed][budget][key]
                    for seed in SEEDS]))
                internal_metrics[f"mean_{key}"] = float(np.mean([
                    all_work[treatment]["internal"][seed][budget][key]
                    for seed in SEEDS]))
            offline[treatment][str(budget)] = {
                "configuration": configuration_metrics,
                "reused_confirmation": internal_metrics}

    manifests = {}
    for treatment in NATIVE_TREATMENTS:
        manifests[treatment] = {}
        for budget in budgets:
            orders = {seed: all_orders[treatment][seed][budget]
                      for seed in SEEDS}
            manifests[treatment][budget] = bakeoff.materialize(
                args.output_root / "shortlists" / treatment / f"m{budget}",
                treatment, orders, occupied_counts, args,
                {"address_budget": budget})

    config_reference_protocol = replay.protocol(args.configuration_protocol,
        None, None, args.output_root / "protocols" /
        "configuration-reference.json", contract)
    internal_reference_protocol = replay.protocol(internal_source, None, None,
        args.output_root / "protocols" / "internal-reference.json", contract)
    config_inputs = replay.partition_inputs(config_reference_protocol, data,
                                             doc_rows_by_seed)
    internal_inputs = replay.partition_inputs(internal_reference_protocol, data,
                                               doc_rows_by_seed)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32",
                           "record_bytes": 1536}
    gates = contract["quality_gates"]
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = replay.run_point(args, contract, "configuration",
        config_reference_protocol, "global-fp32-k8", reference_treatment,
        config_inputs, config_references, True)
    configuration = [training.aggregate(config_reference, config_reference,
        reference_treatment, None, gates, None)]
    for treatment in NATIVE_TREATMENTS:
        for budget in budgets:
            point = f"{treatment}-m{budget}"
            protocol = replay.protocol(args.configuration_protocol,
                manifests[treatment][budget], budget,
                args.output_root / "protocols" / f"configuration-{point}.json",
                contract)
            native_treatment = {"id": point, "kind": "hierarchy_router",
                                "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration", protocol,
                point, native_treatment, config_inputs, config_references, False)
            timings = {seed: all_timings[treatment]["configuration"][seed]
                       for seed in SEEDS}
            orders = {seed: all_orders[treatment][seed][budget]
                      for seed in SEEDS}
            configuration.append(training.aggregate(rows, config_reference,
                native_treatment, budget, gates,
                offline[treatment][str(budget)]["configuration"]))
    opened = []
    families = []
    for treatment in NATIVE_TREATMENTS:
        family = treatment_family(treatment)
        if family not in families:
            families.append(family)
    for family in families:
        rows = [row for row in configuration if row.get("address_budget") and
                ((row["id"].startswith(family + "_beam_") if
                  family.startswith("same_head_prefix") else
                  row["id"].startswith(family + "-m")))]
        opened.append(configuration_open(rows, gates))

    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract,
        "reused_confirmation", internal_reference_protocol, "global-fp32-k8",
        reference_treatment, internal_inputs, internal_references, True)
    internal = [training.aggregate(internal_reference, internal_reference,
        reference_treatment, None, gates, None)]
    internal_bindings = {}
    for selected in opened:
        treatment, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        manifest = manifests[treatment][budget]
        internal_bindings[treatment] = {"path": str(manifest.resolve()),
                                        "sha256": sha256(manifest)}
        protocol = replay.protocol(internal_source, manifest, budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        native_treatment = {"id": selected["id"],
                            "kind": "hierarchy_router", "record_bytes": 0}
        rows = replay.run_point(args, contract, "reused_confirmation", protocol,
            selected["id"], native_treatment, internal_inputs,
            internal_references, False)
        timings = {seed: all_timings[treatment]["internal"][seed]
                   for seed in SEEDS}
        orders = {seed: all_orders[treatment][seed][budget] for seed in SEEDS}
        internal.append(training.aggregate(rows, internal_reference,
            native_treatment, budget, gates,
            offline[treatment][str(budget)]["reused_confirmation"]))
    passing = [row for row in internal if row.get("passes_registered_gate") and
               row["id"] != "global_fp32_k8"]
    selected = min(passing, key=lambda row: (row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"])) if passing else None
    result = {"schema_version": 1,
        "family": "neuroute_12_14_to_16_hierarchy_replay_result",
        "inputs": {"contract_sha256": sha256(args.contract),
            "width_result_sha256": sha256(args.width_result),
            "width_materialization_manifest_sha256": sha256(
                args.width_materialization_manifest),
            "width_model_files_sha256": model_hashes,
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(
                args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "topology_audit": topology_audit,
        "offline_prefix_frontier": offline,
        "configuration": configuration, "reused_confirmation": internal,
        "opened_from_configuration": [row["id"] for row in opened],
        "shortlist_manifests": {treatment: {str(budget): {
            "path": str(path.resolve()), "sha256": sha256(path)}
            for budget, path in by_budget.items()}
            for treatment, by_budget in manifests.items()},
        "confirmation_shortlist_manifests": internal_bindings,
        "decision": {"global_fp32_k8_role":
                "offline_teacher_and_reference_only",
            "selected": selected,
            "hierarchy_passed": selected is not None,
            "native_integration_licensed": False,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    occupied = np.asarray([0, 1, 16, 17], dtype=np.uint32)
    scores = np.asarray([1.0, 3.0, 2.0, 0.0], dtype=np.float32)
    order = replay.top_order(scores, occupied, 3)
    contract = load_contract(THIS /
        "neuroute-12-to-16-hierarchy-replay.example.json")
    require(order.tolist() == [1, 2, 0] and
            (occupied & np.uint32(15)).tolist() == [0, 1, 0, 1],
            "12-to-16 hierarchy self-test failed")
    require(contract["topology"]["same_head_prefix_widths"] == [12, 14] and
            treatment_family("same_head_prefix14_beam_2x") ==
            "same_head_prefix14",
            "12/14-to-16 hierarchy family self-test failed")
    def boundary_row(name: str, budget: int, loss: float) -> dict[str, Any]:
        return {"id": name, "address_budget": budget,
            "passes_registered_gate": False, "mean_ndcg_loss": loss,
            "maximum_stratum_mean_ndcg_loss": loss,
            "mean_final_top10_overlap": 1.0,
            "mean_candidate_reference_retention": 1.0,
            "mean_hamming_reference_overlap": 1.0,
            "mean_adc_reference_overlap": 1.0, "coarse_ms": {"p95": 1.0},
            "offline_router_diagnostics": {
                "directional_generator_ms_per_query": 1.0}}
    boundary_rows = [boundary_row("a-m4096", 4096, 0.0),
                     boundary_row("a-m8192", 8192, 1.0)]
    require(configuration_open(boundary_rows,
            contract["quality_gates"])["id"] == "a-m8192",
            "12/14-to-16 hierarchy boundary self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-12-to-16-hierarchy-replay.example.json")
    for name in ("width-result", "width-materialization-manifest",
                 "width-model-root", "configuration-protocol",
                 "layout-manifest", "k8-manifest", "native-executable",
                 "output-root", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ("width_result", "width_materialization_manifest",
        "width_model_root", "configuration_protocol", "layout_manifest",
        "k8_manifest", "native_executable", "output_root", "output")
    require(all(getattr(args, name) is not None for name in required),
            "12-to-16 hierarchy inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
