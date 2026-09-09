#!/usr/bin/env python3
"""Compare learned, exact-K1, and Faiss shortlist generators before local K8."""
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
FAMILIES = ["fixed_top_m_control", "exact_address_k1",
    "faiss_address_ivf", "faiss_address_hnsw", "faiss_prototype_ivf",
    "faiss_prototype_hnsw"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fixed = load("neuroute_bakeoff_fixed", "run-neuroute-fixed-top-m-router.py")
replay = fixed.replay
exact = fixed.exact


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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("run-neuroute-shortlist-generator-bakeoff.py",
             "run-neuroute-fixed-top-m-router.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


replay.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_shortlist_generator_bakeoff" and
            value["families"] == FAMILIES and
            value["shortlist_budgets"] == [4096, 8192] and
            value["partition"][
                "parameter_selection_uses_configuration_coverage_then_configuration_latency"] is True and
            value["partition"][
                "locked_internal_timing_may_not_select_parameters"] is True and
            value["decision"]["production_selection_forbidden"] is True,
            "shortlist-generator contract differs")
    return value


def read_manifest_arrays(binding: dict[str, Any]) -> dict[int, np.ndarray]:
    path = Path(binding["path"])
    require(path.is_absolute() and sha256(path) == binding["sha256"],
            "fixed Top-M manifest binding differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for row in value["seeds"]:
        payload = Path(row["path"])
        require(payload.stat().st_size == row["bytes"] and
                sha256(payload) == row["sha256"],
                "fixed Top-M payload binding differs")
        result[int(row["seed"])] = np.fromfile(payload, dtype="<u4").reshape(
            tuple(row["shape"]))
    return result


def fixed_control(result: dict[str, Any], gates: dict[str, Any]
                  ) -> tuple[str, dict[int, np.ndarray], dict[int, np.ndarray]]:
    selected = result["decision"].get("selected")
    selected_id = (selected["id"] if selected is not None else
                   result["internal_opened_from_configuration"][0])
    treatment = selected_id.rsplit("-m", 1)[0]
    require(treatment in result["configuration_shortlist_manifests"] and
            treatment in result["internal_shortlist_manifests"],
            "fixed Top-M control manifest differs")
    return (treatment, read_manifest_arrays(
        result["configuration_shortlist_manifests"][treatment]),
        read_manifest_arrays(result["internal_shortlist_manifests"][treatment]))


def top_orders(scores: np.ndarray, occupied: np.ndarray,
               maximum: int) -> np.ndarray:
    return np.asarray([replay.top_order(row, occupied, maximum)
                       for row in scores], dtype=np.uint32)


def faiss_search(index: Any, queries: np.ndarray, count: int
                 ) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    _, neighbors = index.search(np.ascontiguousarray(queries, dtype=np.float32),
                                count)
    elapsed = (time.perf_counter() - started) * 1000.0 / len(queries)
    require(neighbors.shape == (len(queries), count) and np.all(neighbors >= 0),
            "Faiss shortlist search differs")
    return neighbors.astype(np.uint32), elapsed


def stable_unique(values: np.ndarray) -> np.ndarray:
    _, first = np.unique(values, return_index=True)
    return values[np.sort(first)]


def deduplicate_prototypes(index: Any, queries: np.ndarray,
                           prototype_rows: np.ndarray, maximum: int,
                           factors: list[int]) -> tuple[np.ndarray, float]:
    result = np.empty((len(queries), maximum), dtype=np.uint32)
    unresolved = np.arange(len(queries), dtype=np.int64)
    total_ms = 0.0
    for factor in factors:
        if len(unresolved) == 0:
            break
        started = time.perf_counter()
        _, neighbors = index.search(np.ascontiguousarray(queries[unresolved],
            dtype=np.float32), min(index.ntotal, maximum * factor))
        still = []
        for local, query in enumerate(unresolved):
            valid = neighbors[local][neighbors[local] >= 0]
            mapped = prototype_rows[valid]
            ordered = stable_unique(mapped)
            if len(ordered) >= maximum:
                result[query] = ordered[:maximum]
            else:
                still.append(int(query))
        total_ms += (time.perf_counter() - started) * 1000.0
        unresolved = np.asarray(still, dtype=np.int64)
    require(len(unresolved) == 0,
            "Faiss prototype overfetch did not fill the address shortlist")
    return result, total_ms / len(queries)


def coverage(orders: dict[int, np.ndarray], common: dict[int, dict[str, Any]],
             budget: int, query_range: range) -> float:
    values = []
    for seed in SEEDS:
        for query in query_range:
            values.append(len(set(map(int, orders[seed][query, :budget])) &
                set(map(int, common[seed]["global_rows"][query]))) / 1024.0)
    return float(np.mean(values))


def materialize(output: Path, family: str, arrays: dict[int, np.ndarray],
                occupied_counts: dict[int, int], args: argparse.Namespace,
                metadata: dict[str, Any]) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in SEEDS:
        path = (output / f"seed-{seed}.rows.u32le").resolve()
        arrays[seed].astype("<u4", copy=False).tofile(path)
        rows.append({"seed": seed, "occupied_addresses": occupied_counts[seed],
            "dtype": "<u4", "shape": list(arrays[seed].shape),
            "path": str(path), "bytes": path.stat().st_size,
            "sha256": sha256(path)})
    value = {"schema_version": 1,
        "family": "neuroute_local_k8_address_shortlist_materialization",
        "router": family, "generator_metadata": metadata,
        "contract_sha256": sha256(args.contract),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "source_files_sha256": source_hashes(), "seeds": rows}
    path = output / "manifest.json"
    path.write_bytes(canonical(value))
    return path


def diagnostic(orders: dict[int, np.ndarray], common: dict[int, dict[str, Any]],
               budget: int, query_range: range, milliseconds: dict[int, float]
               ) -> dict[str, Any]:
    prototypes = []
    for seed in SEEDS:
        for query in query_range:
            selected = orders[seed][query, :budget]
            prototypes.append(int(np.minimum(common[seed]["counts"][selected],
                                             8).sum()))
    return {"mean_global_k8_top1024_coverage": coverage(orders, common, budget,
                                                         query_range),
        "mean_k8_prototypes_scored": float(np.mean(prototypes)),
        "logical_k8_bytes": float(np.mean(prototypes)) * 384 * 4,
        "directional_generator_ms_per_query": float(np.mean(list(
            milliseconds.values())))}


def run(args: argparse.Namespace) -> None:
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("Faiss is required for the shortlist bake-off") from error
    contract = load_contract(args.contract)
    fixed_result = json.loads(args.fixed_router_result.read_text(encoding="utf-8"))
    require(fixed_result.get("family") ==
            "neuroute_fixed_top_m_router_frontier_result",
            "fixed Top-M result differs")
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    internal_requests = parent["requests"]
    require(len(config_value["requests"]) == len(internal_requests) == 76,
            "shortlist-generator partitions differ")
    internal_value = dict(config_value)
    internal_value["partition"] = "locked_internal"
    internal_value["requests"] = internal_requests
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    data = exact.load_data(parent)
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    maximum = max(contract["shortlist_budgets"])
    gates = contract["quality_gates"]
    fixed_name, fixed_config, fixed_internal = fixed_control(fixed_result, gates)
    fixed_metric = next(row for row in fixed_result["locked_internal"]
        if row["id"].startswith(fixed_name + "-m"))
    fixed_router_ms = float(fixed_metric["offline_router_diagnostics"].get(
        "directional_python_router_ms_per_query", 0.0))
    orders: dict[str, dict[int, np.ndarray]] = {
        "fixed_top_m_control": {}, "exact_address_k1": {}}
    internal_fixed = {}
    config_timings: dict[str, dict[int, float]] = {
        family: {} for family in FAMILIES}
    internal_timings: dict[str, dict[int, float]] = {
        family: {} for family in FAMILIES}
    common = {}
    occupied_counts = {}
    parameter_candidates: dict[str, list[tuple[str, dict[int, np.ndarray],
        dict[int, float], dict[int, float], dict[str, Any]]]] = {
        family: [] for family in FAMILIES if family.startswith("faiss_")}
    faiss.omp_set_num_threads(max(1, int(args.faiss_threads)))
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        occupied = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "occupied_addresses")["file"], dtype="<u4")
        counts = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "address_counts")["file"], dtype="<u4")
        queries = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "query_vectors")["file"], dtype="<f4").reshape(152, 384)
        global_rows = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "shortlist_rows")["file"], dtype="<u4").reshape(152, 1024)
        active_counts = np.minimum(counts, 8).astype(np.int64)
        offsets = np.empty(len(counts) + 1, dtype=np.uint64)
        offsets[0] = 0
        np.cumsum(active_counts, out=offsets[1:])
        prototypes = np.memmap(Path(k8_seed["path"]), mode="r", dtype="<f4",
            shape=(int(k8_seed["active_prototypes"]), 384))
        k1 = np.ascontiguousarray(prototypes[offsets[:-1]], dtype=np.float32)
        prototype_rows = np.repeat(np.arange(len(counts), dtype=np.uint32),
                                   active_counts)
        common[seed] = {"occupied": occupied, "counts": counts,
                        "global_rows": global_rows}
        occupied_counts[seed] = len(occupied)
        orders["fixed_top_m_control"][seed] = fixed_config[seed]
        internal_fixed[seed] = fixed_internal[seed]
        config_fixed_metric = next(row for row in fixed_result["configuration"]
            if row["id"] == fixed_metric["id"])
        config_timings["fixed_top_m_control"][seed] = float(
            config_fixed_metric["offline_router_diagnostics"].get(
                "directional_python_router_ms_per_query", 0.0))
        internal_timings["fixed_top_m_control"][seed] = fixed_router_ms
        started = time.perf_counter()
        config_k1 = top_orders(queries[:76] @ k1.T, occupied, maximum)
        config_timings["exact_address_k1"][seed] = (
            time.perf_counter() - started) * 1000.0 / 76.0
        started = time.perf_counter()
        internal_k1 = top_orders(queries[76:] @ k1.T, occupied, maximum)
        internal_timings["exact_address_k1"][seed] = (
            time.perf_counter() - started) * 1000.0 / 76.0
        orders["exact_address_k1"][seed] = np.concatenate(
            (config_k1, internal_k1), axis=0)
        address_ivf = contract["faiss"]["address_ivf"]
        for nprobe in address_ivf["nprobe"]:
            quantizer = faiss.IndexFlatIP(384)
            index = faiss.IndexIVFFlat(quantizer, 384, int(address_ivf["nlist"]),
                                       faiss.METRIC_INNER_PRODUCT)
            index.train(k1)
            index.add(k1)
            index.nprobe = int(nprobe)
            config_found, config_elapsed = faiss_search(
                index, queries[:76], maximum)
            internal_found, internal_elapsed = faiss_search(
                index, queries[76:], maximum)
            found = np.concatenate((config_found, internal_found), axis=0)
            name = f"nprobe{nprobe}"
            candidate = next((row for row in parameter_candidates[
                "faiss_address_ivf"] if row[0] == name), None)
            if candidate is None:
                candidate = (name, {}, {}, {}, {"nlist": int(address_ivf["nlist"]),
                                             "nprobe": int(nprobe)})
                parameter_candidates["faiss_address_ivf"].append(candidate)
            candidate[1][seed] = found
            candidate[2][seed] = config_elapsed
            candidate[3][seed] = internal_elapsed
            del index, quantizer
        address_hnsw = contract["faiss"]["address_hnsw"]
        index = faiss.IndexHNSWFlat(384, int(address_hnsw["m"]),
                                    faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = int(address_hnsw["ef_construction"])
        index.add(k1)
        for ef_search in address_hnsw["ef_search"]:
            index.hnsw.efSearch = int(ef_search)
            config_found, config_elapsed = faiss_search(
                index, queries[:76], maximum)
            internal_found, internal_elapsed = faiss_search(
                index, queries[76:], maximum)
            found = np.concatenate((config_found, internal_found), axis=0)
            name = f"ef{ef_search}"
            candidate = next((row for row in parameter_candidates[
                "faiss_address_hnsw"] if row[0] == name), None)
            if candidate is None:
                candidate = (name, {}, {}, {}, {"m": int(address_hnsw["m"]),
                    "ef_construction": int(address_hnsw["ef_construction"]),
                    "ef_search": int(ef_search)})
                parameter_candidates["faiss_address_hnsw"].append(candidate)
            candidate[1][seed] = found
            candidate[2][seed] = config_elapsed
            candidate[3][seed] = internal_elapsed
        del index
        prototype_ivf = contract["faiss"]["prototype_ivf"]
        train_count = min(100000, len(prototypes))
        train_rows = np.linspace(0, len(prototypes) - 1, train_count,
                                 dtype=np.int64)
        training = np.ascontiguousarray(prototypes[train_rows], dtype=np.float32)
        quantizer = faiss.IndexFlatIP(384)
        index = faiss.IndexIVFFlat(quantizer, 384, int(prototype_ivf["nlist"]),
                                   faiss.METRIC_INNER_PRODUCT)
        index.train(training)
        index.add(np.ascontiguousarray(prototypes, dtype=np.float32))
        for nprobe in prototype_ivf["nprobe"]:
            index.nprobe = int(nprobe)
            config_found, config_elapsed = deduplicate_prototypes(index,
                queries[:76], prototype_rows, maximum,
                list(map(int, contract["faiss"]["prototype_overfetch_factors"])))
            internal_found, internal_elapsed = deduplicate_prototypes(index,
                queries[76:], prototype_rows, maximum,
                list(map(int, contract["faiss"]["prototype_overfetch_factors"])))
            found = np.concatenate((config_found, internal_found), axis=0)
            name = f"nprobe{nprobe}"
            candidate = next((row for row in parameter_candidates[
                "faiss_prototype_ivf"] if row[0] == name), None)
            if candidate is None:
                candidate = (name, {}, {}, {}, {"nlist": int(prototype_ivf["nlist"]),
                                             "nprobe": int(nprobe)})
                parameter_candidates["faiss_prototype_ivf"].append(candidate)
            candidate[1][seed] = found
            candidate[2][seed] = config_elapsed
            candidate[3][seed] = internal_elapsed
        del index, quantizer, training
        prototype_hnsw = contract["faiss"]["prototype_hnsw"]
        index = faiss.IndexHNSWFlat(384, int(prototype_hnsw["m"]),
                                    faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = int(prototype_hnsw["ef_construction"])
        index.add(np.ascontiguousarray(prototypes, dtype=np.float32))
        for ef_search in prototype_hnsw["ef_search"]:
            index.hnsw.efSearch = int(ef_search)
            config_found, config_elapsed = deduplicate_prototypes(index,
                queries[:76], prototype_rows, maximum,
                list(map(int, contract["faiss"]["prototype_overfetch_factors"])))
            internal_found, internal_elapsed = deduplicate_prototypes(index,
                queries[76:], prototype_rows, maximum,
                list(map(int, contract["faiss"]["prototype_overfetch_factors"])))
            found = np.concatenate((config_found, internal_found), axis=0)
            name = f"ef{ef_search}"
            candidate = next((row for row in parameter_candidates[
                "faiss_prototype_hnsw"] if row[0] == name), None)
            if candidate is None:
                candidate = (name, {}, {}, {}, {"m": int(prototype_hnsw["m"]),
                    "ef_construction": int(prototype_hnsw["ef_construction"]),
                    "ef_search": int(ef_search)})
                parameter_candidates["faiss_prototype_hnsw"].append(candidate)
            candidate[1][seed] = found
            candidate[2][seed] = config_elapsed
            candidate[3][seed] = internal_elapsed
        del index, prototypes, k1
        gc.collect()
    selected_parameters = {}
    parameter_frontier = {}
    for family, candidates in parameter_candidates.items():
        ranked = sorted(candidates, key=lambda row: (-coverage(row[1], common,
            maximum, range(76)), np.mean(list(row[2].values())), row[0]))
        parameter_frontier[family] = [{"id": row[0], **row[4],
            "configuration_global_k8_coverage": coverage(
                row[1], common, maximum, range(76)),
            "configuration_generator_ms_per_query": float(np.mean(
                list(row[2].values())))} for row in candidates]
        name, family_orders, family_config_timings, family_internal_timings, metadata = ranked[0]
        orders[family] = family_orders
        config_timings[family] = family_config_timings
        internal_timings[family] = family_internal_timings
        selected_parameters[family] = {"id": name, **metadata,
            "configuration_global_k8_coverage": coverage(family_orders, common,
                maximum, range(76))}
    orders["fixed_top_m_control"] = {seed: fixed_config[seed] for seed in SEEDS}
    manifests = {}
    for family in FAMILIES:
        metadata = ({"fixed_treatment": fixed_name} if family ==
            "fixed_top_m_control" else selected_parameters.get(family, {}))
        manifests[family] = materialize(args.output_root / "shortlists" / family,
            family, orders[family], occupied_counts, args, metadata)
    config_reference_protocol = replay.protocol(args.configuration_protocol,
        None, None, args.output_root / "protocols" / "configuration-reference.json",
        contract)
    internal_reference_protocol = replay.protocol(internal_source, None, None,
        args.output_root / "protocols" / "internal-reference.json", contract)
    config_inputs = replay.partition_inputs(config_reference_protocol, data, doc_rows)
    internal_inputs = replay.partition_inputs(internal_reference_protocol, data, doc_rows)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32",
                           "record_bytes": 1536}
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = replay.run_point(args, contract, "configuration",
        config_reference_protocol, "global-fp32-k8", reference_treatment,
        config_inputs, config_references, True)
    config_summaries = [replay.aggregate(config_reference, config_reference,
        reference_treatment, None, gates, None)]
    for family in FAMILIES:
        for budget in contract["shortlist_budgets"]:
            point = f"{family}-m{budget}"
            current_protocol = replay.protocol(args.configuration_protocol,
                manifests[family], budget, args.output_root / "protocols" /
                f"configuration-{point}.json", contract)
            treatment = {"id": point, "kind": "shortlist_generator",
                         "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration",
                current_protocol, point, treatment, config_inputs,
                config_references, False)
            config_summaries.append(replay.aggregate(rows, config_reference,
                treatment, budget, gates, diagnostic(orders[family], common,
                    budget, range(76), config_timings[family])))
    family_rows = []
    for family in FAMILIES:
        rows = [row for row in config_summaries if row.get("address_budget") and
                row["id"].startswith(family + "-m")]
        passing = [row for row in rows if row["passes_registered_gate"]]
        family_rows.append(min(passing, key=lambda row: (row["address_budget"],
            row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
                "directional_generator_ms_per_query"], row["id"]))
            if passing else min(rows, key=lambda row: (replay.gate_distance(
                row, gates), row["address_budget"], row["id"])))
    family_rows.sort(key=lambda row: (not row["passes_registered_gate"],
        replay.gate_distance(row, gates), row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))
    opened = family_rows[:2]
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "locked_internal",
        internal_reference_protocol, "global-fp32-k8", reference_treatment,
        internal_inputs, internal_references, True)
    internal_summaries = [replay.aggregate(internal_reference,
        internal_reference, reference_treatment, None, gates, None)]
    internal_bindings = {}
    for selected in opened:
        family, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        family_orders = (internal_fixed if family == "fixed_top_m_control"
                         else orders[family])
        manifest = materialize(args.output_root / "shortlists" / "internal" /
            family, family, family_orders, occupied_counts, args,
            {"fixed_treatment": fixed_name} if family ==
            "fixed_top_m_control" else selected_parameters.get(family, {}))
        internal_bindings[family] = {"path": str(manifest.resolve()),
                                     "sha256": sha256(manifest)}
        current_protocol = replay.protocol(internal_source, manifest, budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        treatment = {"id": selected["id"], "kind": "shortlist_generator",
                     "record_bytes": 0}
        rows = replay.run_point(args, contract, "locked_internal",
            current_protocol, selected["id"], treatment, internal_inputs,
            internal_references, False)
        internal_summaries.append(replay.aggregate(rows, internal_reference,
            treatment, budget, gates, diagnostic(family_orders, common, budget,
                range(76, 152), internal_timings[family])))
    passing = [row for row in internal_summaries
        if row.get("passes_registered_gate") and row["id"] != "global_fp32_k8"]
    selected = (min(passing, key=lambda row: (row["address_budget"],
        row["coarse_ms"]["p95"] + row["offline_router_diagnostics"][
            "directional_generator_ms_per_query"], row["id"]))
        if passing else None)
    result = {"schema_version": 1,
        "family": "neuroute_shortlist_generator_bakeoff_result",
        "claim_scope": contract["claim_scope"],
        "inputs": {"contract_sha256": sha256(args.contract),
            "fixed_router_result_sha256": sha256(args.fixed_router_result),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "configuration_protocol_closure_sha256": replay.protocol_closure(
                args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt,
            "faiss_version": faiss.__version__},
        "faiss_threads": int(args.faiss_threads),
        "parameter_frontier": parameter_frontier,
        "selected_parameters": selected_parameters,
        "configuration": config_summaries,
        "locked_internal": internal_summaries,
        "internal_opened_from_configuration": [row["id"] for row in opened],
        "configuration_shortlist_manifests": {family: {
            "path": str(path.resolve()), "sha256": sha256(path)}
            for family, path in manifests.items()},
        "internal_shortlist_manifests": internal_bindings,
        "decision": {"selected": selected,
            "generator_passed": selected is not None,
            "native_integration_licensed": selected is not None,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    rows = np.asarray([4, 2, 4, 1, 3], dtype=np.uint32)
    seen = []
    for row in rows:
        if int(row) not in seen:
            seen.append(int(row))
    require(seen == [4, 2, 1, 3], "shortlist-generator dedup self-test failed")
    require(stable_unique(rows).tolist() == seen,
            "shortlist-generator vectorized dedup self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-shortlist-generator-bakeoff.example.json")
    parser.add_argument("--fixed-router-result", type=Path)
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--faiss-threads", type=int, default=18)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(all(getattr(args, name) is not None for name in (
        "fixed_router_result", "configuration_protocol", "layout_manifest",
        "k8_manifest", "native_executable", "output_root", "output")),
        "shortlist-generator inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
