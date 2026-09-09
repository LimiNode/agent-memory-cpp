#!/usr/bin/env python3
"""Measure latent binary address-code capacity beyond the 16-bit address ID."""
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

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import svds

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]
WIDTHS = [16, 24, 32, 48, 64]
MAX_WIDTH = max(WIDTHS)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bitwise = load("neuroute_capacity_bitwise", "run-neuroute-bitwise-address-selector.py")
prefix = bitwise.prefix
training = bitwise.training
bakeoff = bitwise.bakeoff
replay = bitwise.replay
exact = bitwise.exact
fixed = bitwise.fixed
require = bitwise.require
sha256 = bitwise.sha256
canonical = bitwise.canonical


def source_hashes() -> dict[str, str]:
    names = (
        "run-neuroute-address-capacity-frontier.py",
        "run-neuroute-bitwise-address-selector.py",
        "run-neuroute-prefix-aware-router.py",
        "run-neuroute-training-sufficient-router.py",
        "run-neuroute-shortlist-generator-bakeoff.py",
        "run-neuroute-local-k8-historical-replay.py",
        "run-neuroute-exact-k8-codec-frontier.py",
        "run-neuroute-generator-policy-bakeoff.py",
        "neuroute_authoritative_qrels.py",
    )
    return {name: sha256(THIS / name) for name in names}


replay.source_hashes = source_hashes
bakeoff.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_address_capacity_frontier",
            "address capacity contract family differs")
    training_value = value["training"]
    require(training_value["query_count"] == 8141 and
            training_value["projection_rank"] == 64 and
            training_value["code_widths"] == WIDTHS and
            training_value["teacher_target"] == "k8_margin_mass" and
            value["shortlist_budgets"] == [1024, 2048, 4096] and
            value["decision"]["post_16_bits_are_latent_codes"] is True,
            "address capacity contract differs")
    return value


def address_bit_codes(occupied: np.ndarray) -> np.ndarray:
    return bitwise.bit_signs(occupied, 16)


def sparse_teacher_matrix(cache_rows: np.ndarray, teacher_scores: np.ndarray,
                          occupied_count: int, target: str) -> coo_matrix:
    query_count, top_count = cache_rows.shape
    values = bitwise.target_values(target, teacher_scores).astype(np.float32,
                                                                  copy=False)
    rows = np.repeat(np.arange(query_count, dtype=np.int64), top_count)
    cols = cache_rows.reshape(-1).astype(np.int64, copy=False)
    return coo_matrix((values.reshape(-1), (rows, cols)),
                      shape=(query_count, occupied_count), dtype=np.float32).tocsr()


def latent_binary_codes(teacher: coo_matrix, width: int,
                        random_seed: int) -> tuple[np.ndarray, dict[str, Any]]:
    require(width <= min(teacher.shape), "latent width exceeds matrix rank bound")
    started = time.perf_counter()
    _, singular, vectors_t = svds(teacher, k=width, which="LM",
                                   return_singular_vectors=True,
                                   random_state=random_seed,
                                   tol=1.0e-4, maxiter=2000)
    order = np.argsort(singular)[::-1]
    vectors = np.asarray(vectors_t[order], dtype=np.float32)
    codes = np.where(vectors.T >= 0.0, 1.0, -1.0).astype(np.float32)
    diagnostics = {
        "solver": "scipy_sparse_svds",
        "width": width,
        "singular_values": singular[order].astype(np.float64).tolist(),
        "mean_bit_balance": np.mean(codes, axis=0, dtype=np.float64).tolist(),
        "svd_ms": (time.perf_counter() - started) * 1000.0,
    }
    return codes, diagnostics


def fit_orders(train_z: np.ndarray, evaluate_z: np.ndarray,
               teacher: coo_matrix, codes: np.ndarray,
               occupied: np.ndarray, budgets: list[int], ridge: float,
               labels_override: np.ndarray | None = None
               ) -> tuple[dict[int, np.ndarray], dict[int, float], int]:
    labels = (np.asarray(teacher @ codes, dtype=np.float32)
              if labels_override is None else np.asarray(labels_override, dtype=np.float32))
    labels /= np.maximum(np.abs(labels).max(axis=1, keepdims=True), 1.0e-8)
    gram = train_z.T @ train_z
    regularized = gram.astype(np.float64)
    regularized.flat[::len(regularized) + 1] += ridge
    weights = np.linalg.solve(regularized,
                              train_z.T @ labels).astype(np.float32)
    started = time.perf_counter()
    scores = (evaluate_z @ weights) @ codes.T
    score_ms = (time.perf_counter() - started) * 1000.0 / len(evaluate_z)
    orders: dict[int, np.ndarray] = {}
    elapsed: dict[int, float] = {}
    for budget in budgets:
        point = time.perf_counter()
        orders[budget] = np.asarray([
            replay.top_order(row, occupied, budget) for row in scores
        ], dtype=np.uint32)
        elapsed[budget] = score_ms + (time.perf_counter() - point) * 1000.0 / len(evaluate_z)
    return orders, elapsed, int(weights.nbytes)


def metrics(value: dict[str, Any], common: dict[int, dict[str, Any]], budget: int,
            query_range: range, partition: str) -> dict[str, Any]:
    orders = {seed: value["orders"][budget][seed] for seed in SEEDS}
    timings = {seed: {budget: value["timings"][partition][budget][seed]}
               for seed in SEEDS}
    return training.generator_metrics(orders, common, budget, query_range,
                                      timings, value["model_bytes"])


def aggregate(rows: list[dict[str, Any]], reference: list[dict[str, Any]],
              treatment: dict[str, Any], budget: int | None,
              gates: dict[str, Any], offline: dict[str, Any] | None
              ) -> dict[str, Any]:
    return training.aggregate(rows, reference, treatment, budget, gates, offline)


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    policy_result = json.loads(args.policy_result.read_text(encoding="utf-8"))
    policy_evidence = json.loads(args.policy_evidence.read_text(encoding="utf-8"))
    require(policy_result.get("family") == "neuroute_generator_policy_bakeoff_result" and
            policy_evidence.get("result_sha256") == sha256(args.policy_result) and
            policy_result["decision"]["adaptive_prefix_training_required"] is True,
            "address capacity policy activation differs")
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    internal_value = dict(config_value)
    internal_value["partition"] = "reused_confirmation"
    internal_value["requests"] = parent["requests"]
    require(len(config_value["requests"]) == len(parent["requests"]) == 76,
            "address capacity partitions differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    data = exact.load_data(parent)
    _, pool, pool_identity = training.load_pool(data, args.multilingual_query_root)
    caches = training.training_caches(args.training_cache_root, pool_identity)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    request_rows = sorted(list(config_value["requests"]) + list(parent["requests"]),
                          key=lambda row: int(row["request"]))
    native_positions = [int(row["native_query"]) for row in request_rows]
    oracle_native, _ = exact.scale.exact_oracle(data, native_positions, 10)
    oracle = {request: oracle_native[native]
              for request, native in enumerate(native_positions)}
    budgets = list(map(int, contract["shortlist_budgets"]))
    common: dict[int, dict[str, Any]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    occupied_counts: dict[int, int] = {}
    latent_diagnostics: dict[str, Any] = {}
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        occupied = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "occupied_addresses")["file"], dtype="<u4")
        counts = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "address_counts")["file"], dtype="<u4")
        evaluate = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "query_vectors")["file"], dtype="<f4").reshape(152, 384)
        global_rows = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "shortlist_rows")["file"], dtype="<u4").reshape(152, 1024)
        scalar_features = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "scalar_features")["file"], dtype="<f4").reshape(152, 1024, 22)
        cache_identity = caches[seed]["manifest"]["identity"]
        require(cache_identity.get("prototypes_sha256") == k8_seed["dense_prototypes_sha256"] and
                cache_identity.get("effective_sha256") == k8_seed["effective_sha256"],
                "address capacity cache topology differs")
        cache_addresses, cache_features, _ = training.open_cache(caches[seed])
        address_to_row = np.full(1 << 16, -1, dtype=np.int32)
        address_to_row[occupied] = np.arange(len(occupied), dtype=np.int32)
        cache_rows = address_to_row[cache_addresses]
        require(np.all(cache_rows >= 0), "address capacity cache has unoccupied address")
        cache_rows = np.asarray(cache_rows, dtype=np.uint32)
        common[seed] = {"counts": counts, "global_rows": global_rows,
            "global_scores": np.asarray(scalar_features[:, :, 8], dtype=np.float32),
            "actionable_rows": training.actionable_rows(oracle, doc_rows[seed])}
        occupied_counts[seed] = len(occupied)
        train_z, evaluate_z, projection, projection_ms = training.projected_queries(
            pool, evaluate, int(contract["training"]["projection_rank"]), 76)
        teacher_scores = np.asarray(cache_features[:, :, 8], dtype=np.float32)
        teacher = sparse_teacher_matrix(cache_rows, teacher_scores, len(occupied),
                                        contract["training"]["teacher_target"])
        address_codes = address_bit_codes(occupied)
        latent_all, latent_diag = latent_binary_codes(teacher, MAX_WIDTH, seed + MAX_WIDTH)
        for width in WIDTHS:
            if width == 16:
                codes = address_codes
                diag = {"kind": "literal_address_bits16", "width": width}
                labels_override = bitwise.bit_labels(
                    cache_rows, occupied, teacher_scores, 16,
                    contract["training"]["teacher_target"])
            else:
                codes = latent_all[:, :width]
                diag = dict(latent_diag)
                diag["width"] = width
                diag["singular_values"] = diag["singular_values"][:width]
                labels_override = None
            latent_diagnostics[f"{seed}/{width}"] = diag
            orders, elapsed, weight_bytes = fit_orders(
                train_z, evaluate_z, teacher, codes, occupied, budgets,
                float(contract["training"]["ridge_lambda"]), labels_override)
            name = f"width{width}"
            value = candidates.setdefault(name, {"width": width, "orders": {budget: {} for budget in budgets},
                "timings": {partition: {budget: {} for budget in budgets}
                            for partition in ("configuration", "internal")},
                "model_bytes": {}})
            for budget in budgets:
                value["orders"][budget][seed] = orders[budget]
                value["timings"]["configuration"][budget][seed] = elapsed[budget]
                value["timings"]["internal"][budget][seed] = elapsed[budget]
            projection_bytes = (384 + 384 * int(projection["rank"]) +
                                int(projection["rank"])) * 8
            packed_code_bytes = ((int(width) + 7) // 8) * len(occupied)
            value["model_bytes"][seed] = int(weight_bytes + projection_bytes + packed_code_bytes)
        del cache_addresses, cache_features, cache_rows, train_z, evaluate_z, teacher
        gc.collect()
    selection_budget = int(contract["selection"]["offline_budget"])
    finalists = []
    offline = []
    for width in WIDTHS:
        value = candidates[f"width{width}"]
        value["configuration_diagnostics"] = metrics(value, common, selection_budget,
                                                       range(76), "configuration")
        offline.append({"width": width, "configuration_diagnostics": value["configuration_diagnostics"]})
        finalists.append(value)
    manifests = {f"width{value['width']}": {budget: bakeoff.materialize(
        args.output_root / "shortlists" / f"width{value['width']}" / f"m{budget}",
        f"width{value['width']}", value["orders"][budget], occupied_counts, args,
        {"width": value["width"], "address_budget": budget}) for budget in budgets}
        for value in finalists}
    gates = contract["quality_gates"]
    config_ref_protocol = replay.protocol(args.configuration_protocol, None, None,
        args.output_root / "protocols/configuration-reference.json", contract)
    internal_ref_protocol = replay.protocol(internal_source, None, None,
        args.output_root / "protocols/internal-reference.json", contract)
    config_inputs = replay.partition_inputs(config_ref_protocol, data, doc_rows)
    internal_inputs = replay.partition_inputs(internal_ref_protocol, data, doc_rows)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32", "record_bytes": 1536}
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = replay.run_point(args, contract, "configuration", config_ref_protocol,
        "global-fp32-k8", reference_treatment, config_inputs, config_references, True)
    configuration = [aggregate(config_reference, config_reference, reference_treatment,
                               None, gates, None)]
    for value in finalists:
        name = f"width{value['width']}"
        for budget in budgets:
            point = f"{name}-m{budget}"
            protocol = replay.protocol(args.configuration_protocol, manifests[name][budget],
                budget, args.output_root / f"protocols/configuration-{point}.json", contract)
            treatment = {"id": point, "kind": "latent_binary_address_router", "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration", protocol, point,
                treatment, config_inputs, config_references, False)
            configuration.append(aggregate(rows, config_reference, treatment, budget, gates,
                metrics(value, common, budget, range(76), "configuration")))
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "reused_confirmation", internal_ref_protocol,
        "global-fp32-k8", reference_treatment, internal_inputs, internal_references, True)
    internal = [aggregate(internal_reference, internal_reference, reference_treatment,
                          None, gates, None)]
    internal_manifests = {}
    opened = []
    for value in finalists:
        name = f"width{value['width']}"
        rows = [row for row in configuration if row.get("address_budget") == 4096 and
                row["id"].startswith(name + "-")]
        selected = min(rows, key=lambda row: (replay.gate_distance(row, gates), row["id"]))
        opened.append(selected["id"])
        manifest = manifests[name][4096]
        internal_manifests[name] = {"path": str(manifest.resolve()), "sha256": sha256(manifest)}
        protocol = replay.protocol(internal_source, manifest, 4096,
            args.output_root / f"protocols/internal-{selected['id']}.json", contract)
        treatment = {"id": selected["id"], "kind": "latent_binary_address_router", "record_bytes": 0}
        rows = replay.run_point(args, contract, "reused_confirmation", protocol, selected["id"],
            treatment, internal_inputs, internal_references, False)
        internal.append(aggregate(rows, internal_reference, treatment, 4096, gates,
            metrics(value, common, 4096, range(76, 152), "internal")))
    passing = [row for row in internal if row["id"] != "global_fp32_k8" and
               row["passes_registered_gate"]]
    result = {"schema_version": 1, "family": "neuroute_address_capacity_frontier_result",
        "inputs": {"contract_sha256": sha256(args.contract),
            "policy_result_sha256": sha256(args.policy_result),
            "policy_evidence_sha256": sha256(args.policy_evidence),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "multilingual_query_manifest_sha256": pool_identity["manifest_sha256"],
            "training_pool": pool_identity,
            "training_cache_manifests_sha256": {str(seed): sha256(caches[seed]["path"])
                for seed in SEEDS}, "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "offline_frontier": offline, "latent_code_diagnostics": latent_diagnostics,
        "selected_finalists": [f"width{value['width']}" for value in finalists],
        "configuration": configuration, "reused_confirmation": internal,
        "opened_from_configuration": opened,
        "shortlist_manifests": {name: {str(budget): {"path": str(path.resolve()),
            "sha256": sha256(path)} for budget, path in by_budget.items()}
            for name, by_budget in manifests.items()},
        "confirmation_shortlist_manifests": internal_manifests,
        "decision": {"global_fp32_k8_role": "offline_teacher_and_reference_only",
            "maximum_local_k8_addresses": 4096, "latent_capacity_frontier_passed": bool(passing),
            "passing_confirmation_rows": passing, "native_integration_licensed": False,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    matrix = coo_matrix(np.asarray([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype=np.float32))
    codes, _ = latent_binary_codes(matrix, 1, 7)
    require(codes.shape == (2, 1) and
            np.all(np.isin(codes, [-1.0, 1.0])),
            "capacity latent code self-test differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-capacity-frontier.example.json")
    for name in ("policy-result", "policy-evidence", "configuration-protocol",
                 "layout-manifest", "k8-manifest", "native-executable",
                 "multilingual-query-root", "training-cache-root", "output-root", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ("policy_result", "policy_evidence", "configuration_protocol", "layout_manifest",
        "k8_manifest", "native_executable", "multilingual_query_root", "training_cache_root",
        "output_root", "output")
    require(all(getattr(args, name) is not None for name in required),
            "address capacity inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
