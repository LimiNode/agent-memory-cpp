#!/usr/bin/env python3
"""Reconstruct historical address routers and replay exact K8 locally."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]
ROUTERS = ["hard_hamming_reconstruction", "occupied_bit_logit_reconstruction",
           "centroid_k1", "posting_mass", "stable_random"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exact = load("neuroute_local_k8_exact", "run-neuroute-exact-k8-codec-frontier.py")


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
    names = ("run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


def protocol_closure(path: Path) -> dict[str, str]:
    pending = [path.resolve()]
    seen: set[Path] = set()
    result: dict[str, str] = {}
    link_fields = ("routing_kernel_protocol", "parent_protocol",
                   "final_int8_layout_manifest", "coarse_k8_manifest")
    payload_fields = ("native_input_manifest", "document_id_rank_file",
                      "evaluation_document_ids", "evaluation_query_ids",
                      "evaluation_qrels")
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        result[str(current)] = sha256(current)
        value = json.loads(current.read_text(encoding="utf-8"))
        for field in link_fields:
            if field in value:
                pending.append(Path(value[field]).resolve())
        for field in payload_fields:
            if field in value:
                payload = Path(value[field]).resolve()
                result[str(payload)] = sha256(payload)
    return dict(sorted(result.items()))


def shortlist_binding(protocol_path: Path) -> dict[str, Any] | None:
    protocol_value = json.loads(protocol_path.read_text(encoding="utf-8"))
    if "coarse_k8_address_shortlist_manifest" not in protocol_value:
        return None
    path = Path(protocol_value["coarse_k8_address_shortlist_manifest"]).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    payloads = {}
    for row in manifest["seeds"]:
        payload = Path(row["path"]).resolve()
        digest = sha256(payload)
        require(digest == row["sha256"] and payload.stat().st_size == row["bytes"],
                "local K8 shortlist payload binding differs")
        payloads[str(row["seed"])] = digest
    return {"manifest_sha256": sha256(path), "payloads_sha256": payloads}


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_local_k8_historical_replay" and
            value["provenance"]["historical_checkpoint_status"] ==
            "unavailable_after_actions_artifact_expiry" and
            value["routers"] == ROUTERS and
            value["shortlist_budgets"] == [2048, 4096, 8192, 16384] and
            value["decision"]["production_selection_forbidden"] is True,
            "local K8 historical replay contract differs")
    return value


def descriptor(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    return next(row for row in rows if row["role"] == role)


def top_order(scores: np.ndarray, addresses: np.ndarray,
              limit: int) -> np.ndarray:
    if limit < len(scores):
        selected = np.argpartition(scores, -limit)[-limit:]
    else:
        selected = np.arange(len(scores), dtype=np.int64)
    order = np.lexsort((addresses[selected], -scores[selected]))
    return selected[order].astype(np.uint32)


def address_signs(addresses: np.ndarray) -> np.ndarray:
    shifts = np.arange(16, dtype=np.uint32)
    bits = ((addresses[:, None] >> shifts[None, :]) & 1).astype(np.float32)
    return bits * np.float32(2.0) - np.float32(1.0)


def ridge_bit_logits(queries: np.ndarray, occupied: np.ndarray,
                     global_rows: np.ndarray, ridge_lambda: float) -> np.ndarray:
    discounts = (1.0 / np.log2(np.arange(global_rows.shape[1],
                                          dtype=np.float64) + 2.0)).astype(np.float32)
    signs = address_signs(occupied)
    targets = np.empty((len(queries), 16), dtype=np.float32)
    denominator = float(discounts.sum())
    for query in range(len(queries)):
        targets[query] = (discounts[:, None] *
                          signs[global_rows[query]]).sum(axis=0) / denominator
    train = np.column_stack((queries[:76], np.ones(76, dtype=np.float32)))
    all_queries = np.column_stack((queries, np.ones(len(queries), dtype=np.float32)))
    gram = train @ train.T
    gram.flat[::len(gram) + 1] += np.float32(ridge_lambda)
    dual = np.linalg.solve(gram.astype(np.float64),
                           targets[:76].astype(np.float64))
    weights = train.T.astype(np.float64) @ dual
    return (all_queries.astype(np.float64) @ weights).astype(np.float32)


def first_prototypes(k8_seed: dict[str, Any], counts: np.ndarray) -> np.ndarray:
    active_counts = np.minimum(counts.astype(np.uint64), 8)
    offsets = np.empty(len(counts), dtype=np.uint64)
    offsets[0] = 0
    if len(counts) > 1:
        np.cumsum(active_counts[:-1], out=offsets[1:])
    payload = np.memmap(Path(k8_seed["path"]), mode="r", dtype="<f4",
                        shape=(int(k8_seed["active_prototypes"]), 384))
    return np.asarray(payload[offsets], dtype=np.float32)


def router_orders(seed: int, occupied: np.ndarray, counts: np.ndarray,
                  queries: np.ndarray, global_rows: np.ndarray,
                  k8_seed: dict[str, Any], maximum: int,
                  ridge_lambda: float) -> dict[str, np.ndarray]:
    signs = address_signs(occupied)
    logits = ridge_bit_logits(queries, occupied, global_rows, ridge_lambda)
    result = {name: np.empty((len(queries), maximum), dtype=np.uint32)
              for name in ROUTERS}
    static_mass = top_order(counts.astype(np.float32), occupied, maximum)
    result["posting_mass"][:] = static_mass
    for query in range(len(queries)):
        result["occupied_bit_logit_reconstruction"][query] = top_order(
            signs @ logits[query], occupied, maximum)
        hard = np.where(logits[query] >= 0.0, 1.0, -1.0).astype(np.float32)
        result["hard_hamming_reconstruction"][query] = top_order(
            signs @ hard, occupied, maximum)
        digest = hashlib.sha256(
            f"neuroute-local-k8-random-v1\0{seed}\0{query}".encode("utf-8")).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
        result["stable_random"][query] = rng.permutation(len(occupied))[:maximum]
    centroids = first_prototypes(k8_seed, counts)
    for start in range(0, len(queries), 16):
        stop = min(start + 16, len(queries))
        scores = queries[start:stop] @ centroids.T
        for local, values in enumerate(scores):
            result["centroid_k1"][start + local] = top_order(
                values, occupied, maximum)
    return result


def materialize(args: argparse.Namespace, contract: dict[str, Any]) -> tuple[
        Path, dict[int, dict[str, np.ndarray]], dict[str, Any]]:
    output = args.output_root / "shortlists"
    manifest_path = output / "manifest.json"
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    maximum = max(contract["shortlist_budgets"])
    by_seed: dict[int, dict[str, np.ndarray]] = {}
    manifest_seeds = []
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        occupied_row = descriptor(layout_seed["mappings"], "occupied_addresses")
        count_row = descriptor(layout_seed["mappings"], "address_counts")
        query_row = descriptor(layout_seed["mappings"], "query_vectors")
        global_row = descriptor(layout_seed["mappings"], "shortlist_rows")
        occupied = np.fromfile(root / occupied_row["file"], dtype="<u4")
        counts = np.fromfile(root / count_row["file"], dtype="<u4")
        queries = np.fromfile(root / query_row["file"], dtype="<f4").reshape(152, 384)
        global_rows = np.fromfile(root / global_row["file"], dtype="<u4").reshape(152, 1024)
        orders = router_orders(seed, occupied, counts, queries, global_rows,
                               k8_seed, maximum, float(contract["ridge_lambda"]))
        by_seed[seed] = {"occupied": occupied, "counts": counts,
                         "global_rows": global_rows, **orders}
        for router, values in orders.items():
            path = (output / router / f"seed-{seed}.rows.u32le").resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            values.astype("<u4", copy=False).tofile(path)
        manifest_seeds.append({"seed": seed,
            "occupied_addresses": len(occupied), "dtype": "<u4",
            "shape": [152, maximum]})
    manifests = {}
    for router in ROUTERS:
        rows = []
        for seed, common in zip(SEEDS, manifest_seeds):
            path = (output / router / f"seed-{seed}.rows.u32le").resolve()
            rows.append({**common, "path": str(path), "bytes": path.stat().st_size,
                         "sha256": sha256(path)})
        value = {"schema_version": 1,
            "family": "neuroute_local_k8_address_shortlist_materialization",
            "router": router, "contract_sha256": sha256(args.contract),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "source_files_sha256": source_hashes(), "seeds": rows}
        path = output / router / "manifest.json"
        path.write_bytes(canonical(value))
        manifests[router] = {"path": str(path.resolve()), "sha256": sha256(path)}
    root_manifest = {"schema_version": 1,
        "family": "neuroute_local_k8_router_shortlists",
        "provenance": contract["provenance"],
        "source_files_sha256": source_hashes(), "routers": manifests}
    manifest_path.write_bytes(canonical(root_manifest))
    return manifest_path, by_seed, root_manifest


def protocol(source: Path, shortlist_manifest: Path | None, budget: int | None,
             output: Path, contract: dict[str, Any]) -> Path:
    value = json.loads(source.read_text(encoding="utf-8"))
    for name in ("coarse_k8_prefilter_manifest", "coarse_k8_prefilter_treatment",
                 "coarse_k8_prefilter_query_arithmetic",
                 "coarse_k8_prefilter_prototypes", "coarse_k8_refine_addresses",
                 "coarse_k8_address_shortlist_manifest",
                 "coarse_k8_address_shortlist_size"):
        value.pop(name, None)
    if shortlist_manifest is not None:
        value["coarse_k8_address_shortlist_manifest"] = str(shortlist_manifest.resolve())
        value["coarse_k8_address_shortlist_size"] = int(budget)
    value["workers"] = [1]
    value["trace_repetitions"] = 1
    value["warmup_batches"] = contract["native_replay"]["warmup_batches"]
    value["measured_batches"] = contract["native_replay"]["measured_batches"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(value))
    return output


def partition_inputs(protocol_path: Path, data: dict[str, Any],
                     doc_rows: dict[int, np.ndarray]) -> dict[str, Any]:
    value = json.loads(protocol_path.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(value)
    requests = exact.requests(value, parent)
    positions = [int(row["native_query"]) for row in requests]
    oracle_by_position, _ = exact.scale.exact_oracle(data, positions, 10)
    oracle = np.asarray([oracle_by_position[position] for position in positions],
                        dtype=np.int32)
    return {"protocol": value, "parent": parent, "requests": requests,
        "oracle": oracle, "qrel_docs": exact.qrel_positions(parent, requests, data),
        "ndcg": exact.ndcg_rows(parent, requests), "doc_rows": doc_rows}


def run_point(args: argparse.Namespace, contract: dict[str, Any], partition: str,
              protocol_path: Path, point: str, treatment: dict[str, Any],
              inputs: dict[str, Any], references: dict[int, list[dict[str, Any]]],
              is_reference: bool) -> list[dict[str, Any]]:
    checkpoint = args.output_root / "checkpoints" / f"{partition}-{point}.json"
    identity = {"schema_version": 1, "point": point, "partition": partition,
        "contract_sha256": sha256(args.contract),
        "protocol_sha256": sha256(protocol_path),
        "protocol_closure_sha256": protocol_closure(protocol_path),
        "shortlist_binding": shortlist_binding(protocol_path),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "native_executable_sha256": sha256(args.native_executable),
        "source_files_sha256": source_hashes(),
        "authoritative_e5_receipt": args.authoritative_e5_receipt}
    if checkpoint.is_file():
        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
        if cached.get("identity") == identity:
            rows = cached["rows"]
            if is_reference:
                for seed in SEEDS:
                    references[seed] = [row for row in rows if row["seed"] == seed]
            return rows
    rows = []
    query_ids, document_ids, qrels = inputs["ndcg"]
    def one_seed(seed: int) -> tuple[int, list[dict[str, Any]]]:
        report = args.output_root / "reports" / partition / f"{point}-{seed}.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(args.native_executable), "--external-comparison-r4",
            str(protocol_path), str(seed), "int8", contract["native_replay"]["execution"],
            "1", str(report)], check=True)
        current = exact.query_metrics(report, inputs["requests"], inputs["oracle"],
            inputs["qrel_docs"], inputs["doc_rows"][seed], query_ids,
            document_ids, qrels, None if is_reference else references[seed])
        result = [{"partition": partition, "prototype_limit": 8,
            "treatment": treatment["id"], "seed": seed,
            "routing_storage_mode": "int8", "query_arithmetic": "fp32",
            "coarse_store_bytes": int(json.loads(report.read_text(
                encoding="utf-8"))["coarse_k8_store_bytes"]), **row}
            for row in current]
        exact.cleanup_report(report)
        return seed, result
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SEEDS)) as pool:
        completed = [future.result() for future in
                     [pool.submit(one_seed, seed) for seed in SEEDS]]
    for seed, current in sorted(completed):
        rows.extend(current)
        if is_reference:
            references[seed] = [row for row in current]
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(canonical({"identity": identity, "rows": rows}))
    return rows


def aggregate(rows: list[dict[str, Any]], reference: list[dict[str, Any]],
              treatment: dict[str, Any], budget: int | None,
              gates: dict[str, Any], offline: dict[str, Any] | None) -> dict[str, Any]:
    value = exact.aggregate(rows, reference, treatment, 8, gates)
    value["address_budget"] = budget
    value["offline_router_diagnostics"] = offline
    value["eligible_budget"] = budget is None or budget <= gates[
        "maximum_native_address_budget"]
    value["passes_registered_gate"] = (value["passes_quality_gates"] and
                                        value["eligible_budget"])
    return value


def offline_diagnostics(by_seed: dict[int, dict[str, np.ndarray]], router: str,
                        budget: int, partition: slice) -> dict[str, Any]:
    overlaps, masses, prototypes = [], [], []
    for seed in SEEDS:
        values = by_seed[seed]
        for query in range(*partition.indices(152)):
            selected = values[router][query, :budget]
            overlaps.append(len(set(map(int, selected)) & set(map(
                int, values["global_rows"][query]))) / 1024.0)
            masses.append(int(values["counts"][selected].sum()))
            prototypes.append(int(np.minimum(values["counts"][selected], 8).sum()))
    return {"mean_global_k8_top1024_coverage": statistics.fmean(overlaps),
        "minimum_global_k8_top1024_coverage": min(overlaps),
        "mean_candidate_mass_before_r0_boundary": statistics.fmean(masses),
        "mean_k8_prototypes_scored": statistics.fmean(prototypes),
        "logical_k8_bytes": statistics.fmean(prototypes) * 384 * 4}


def gate_distance(row: dict[str, Any], gates: dict[str, Any]) -> float:
    return (max(0.0, row["mean_ndcg_loss"] - gates[
                "maximum_mean_downstream_ndcg_loss"]) +
            max(0.0, row["maximum_stratum_mean_ndcg_loss"] - gates[
                "maximum_every_seed_downstream_ndcg_loss"]) +
            max(0.0, gates["minimum_mean_final_top10_overlap"] - row[
                "mean_final_top10_overlap"]) +
            max(0.0, gates["minimum_mean_fp32_stage_retention_at_candidate"] -
                row["mean_candidate_reference_retention"]) +
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_hamming768"] -
                row["mean_hamming_reference_overlap"]) +
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_adc64"] -
                row["mean_adc_reference_overlap"]))


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    args.output_root.mkdir(parents=True, exist_ok=True)
    shortlist_root, by_seed, root_manifest = materialize(args, contract)
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    internal_requests = parent["requests"]
    require(len(config_value["requests"]) == 76 and len(internal_requests) == 76 and
            [row["request"] for row in config_value["requests"]] == list(range(76)) and
            [row["request"] for row in internal_requests] == list(range(76, 152)),
            "local K8 query partitions differ")
    internal_value = dict(config_value)
    internal_value["partition"] = "locked_internal"
    internal_value["requests"] = internal_requests
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    data = exact.load_data(parent)
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    config_reference_protocol = protocol(args.configuration_protocol, None, None,
        args.output_root / "protocols" / "configuration-reference.json", contract)
    internal_reference_protocol = protocol(internal_source, None, None,
        args.output_root / "protocols" / "internal-reference.json", contract)
    config_inputs = partition_inputs(config_reference_protocol, data, doc_rows)
    internal_inputs = partition_inputs(internal_reference_protocol, data, doc_rows)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32",
                           "record_bytes": 1536}
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = run_point(args, contract, "configuration",
        config_reference_protocol, "global-fp32-k8", reference_treatment,
        config_inputs, config_references, True)
    config_summaries = [aggregate(config_reference, config_reference,
        reference_treatment, None, contract["quality_gates"], None)]
    config_rows_by_point = {}
    manifests = root_manifest["routers"]
    for router in ROUTERS:
        for budget in contract["shortlist_budgets"]:
            point = f"{router}-m{budget}"
            current_protocol = protocol(args.configuration_protocol,
                Path(manifests[router]["path"]), budget,
                args.output_root / "protocols" / f"configuration-{point}.json",
                contract)
            treatment = {"id": point, "kind": "router", "record_bytes": 0}
            rows = run_point(args, contract, "configuration", current_protocol,
                point, treatment, config_inputs, config_references, False)
            config_rows_by_point[point] = rows
            config_summaries.append(aggregate(rows, config_reference, treatment,
                budget, contract["quality_gates"], offline_diagnostics(
                    by_seed, router, budget, slice(0, 76))))
    gates = contract["quality_gates"]
    opened = []
    for router in ROUTERS:
        rows = [row for row in config_summaries if row.get("address_budget") and
                row["id"].startswith(router + "-m") and
                row["address_budget"] <= gates["maximum_native_address_budget"]]
        passing = [row for row in rows if row["passes_quality_gates"]]
        opened.append(min(passing, key=lambda row: (row["address_budget"],
            row["coarse_ms"]["p95"])) if passing else min(rows,
            key=lambda row: (gate_distance(row, gates), row["address_budget"])))
        sensitivity = next(row for row in config_summaries
            if row.get("address_budget") == 16384 and
               row["id"].startswith(router + "-m"))
        if sensitivity["id"] not in {value["id"] for value in opened}:
            opened.append(sensitivity)
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = run_point(args, contract, "locked_internal",
        internal_reference_protocol, "global-fp32-k8", reference_treatment,
        internal_inputs, internal_references, True)
    internal_summaries = [aggregate(internal_reference, internal_reference,
        reference_treatment, None, gates, None)]
    for selected in opened:
        router, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        current_protocol = protocol(internal_source,
            Path(manifests[router]["path"]), budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        treatment = {"id": selected["id"], "kind": "router", "record_bytes": 0}
        rows = run_point(args, contract, "locked_internal", current_protocol,
            selected["id"], treatment, internal_inputs, internal_references, False)
        internal_summaries.append(aggregate(rows, internal_reference, treatment,
            budget, gates, offline_diagnostics(by_seed, router, budget,
                                                slice(76, 152))))
    passing_internal = [row for row in internal_summaries
        if row.get("passes_registered_gate") and row["id"] != "global_fp32_k8"]
    selected = (min(passing_internal, key=lambda row: (row["address_budget"],
        row["coarse_ms"]["p95"], row["mean_ndcg_loss"], row["id"]))
        if passing_internal else None)
    result = {"schema_version": 1,
        "family": "neuroute_local_k8_historical_replay_result",
        "claim_scope": contract["claim_scope"], "provenance": contract["provenance"],
        "inputs": {"contract_sha256": sha256(args.contract),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "configuration_protocol_closure_sha256": protocol_closure(
                args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt,
            "shortlist_manifest_sha256": sha256(shortlist_root)},
        "configuration": config_summaries,
        "locked_internal": internal_summaries,
        "internal_opened_from_configuration": [row["id"] for row in opened],
        "decision": {"selected": selected,
            "historical_recipe_passed": selected is not None,
            "activate_fixed_top_m_frontier": selected is None,
            "native_integration_licensed": selected is not None,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    addresses = np.asarray([0, 1, 2, 3], dtype=np.uint32)
    signs = address_signs(addresses)
    require(signs.shape == (4, 16) and signs[0, 0] == -1 and signs[1, 0] == 1,
            "local K8 address-sign self-test failed")
    scores = np.asarray([1.0, 1.0, 2.0, 0.0], dtype=np.float32)
    require(top_order(scores, np.asarray([8, 7, 9, 1], dtype=np.uint32), 3).tolist()
            == [2, 1, 0], "local K8 deterministic ordering self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-local-k8-historical-replay.example.json")
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    require(all(getattr(args, name) is not None for name in (
        "configuration_protocol", "layout_manifest", "k8_manifest",
        "native_executable", "output_root", "output")),
        "local K8 replay inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
