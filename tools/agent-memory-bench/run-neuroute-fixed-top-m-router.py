#!/usr/bin/env python3
"""Evaluate leakage-safe fixed Top-M learned address routers before local K8."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]
TREATMENTS = ["direct_linear_global", "direct_linear_actionable",
    "direct_linear_hybrid", "low_rank_32_hybrid", "low_rank_64_hybrid",
    "nonlinear_random_gelu_256_hybrid",
    "centroid_k1_plus_low_rank_64_hybrid"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


replay = load("neuroute_fixed_top_m_replay",
              "run-neuroute-local-k8-historical-replay.py")
exact = replay.exact


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
    names = ("run-neuroute-fixed-top-m-router.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


# The shared native replay helper calls this symbol when checkpointing. Bind the
# complete fixed-router implementation rather than only its imported substrate.
replay.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_fixed_top_m_router_frontier" and
            value["treatments"] == TREATMENTS and
            value["shortlist_budgets"] == [2048, 4096, 8192] and
            value["partition"]["configuration_folds"] == 4 and
            value["decision"]["production_selection_forbidden"] is True,
            "fixed Top-M router contract differs")
    return value


def descriptor(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    return next(row for row in rows if row["role"] == role)


def gelu(values: np.ndarray) -> np.ndarray:
    return (0.5 * values * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) *
        (values + 0.044715 * values * values * values)))).astype(np.float32)


def ridge_coefficients(train: np.ndarray, evaluate: np.ndarray,
                       ridge_lambda: float) -> np.ndarray:
    train64 = np.column_stack((train, np.ones(len(train), dtype=np.float32)
                              )).astype(np.float64)
    evaluate64 = np.column_stack((evaluate,
        np.ones(len(evaluate), dtype=np.float32))).astype(np.float64)
    gram = train64 @ train64.T
    gram.flat[::len(gram) + 1] += ridge_lambda
    return ((evaluate64 @ train64.T) @ np.linalg.inv(gram)).astype(np.float32)


def low_rank_prediction(coefficients: np.ndarray, targets: np.ndarray,
                        rank: int) -> np.ndarray:
    gram = targets @ targets.T
    eigenvalues, vectors = np.linalg.eigh(gram.astype(np.float64))
    keep = np.argsort(eigenvalues)[::-1][:min(rank, len(targets))]
    basis = vectors[:, keep].astype(np.float32)
    return ((coefficients @ basis) @ (basis.T @ targets)).astype(np.float32)


def row_standardize(values: np.ndarray) -> np.ndarray:
    mean = values.mean(axis=1, keepdims=True, dtype=np.float64)
    deviation = values.std(axis=1, keepdims=True, dtype=np.float64)
    return ((values - mean) / np.maximum(deviation, 1.0e-7)).astype(np.float32)


def target_matrix(global_rows: np.ndarray, oracle: dict[int, np.ndarray],
                  doc_rows: np.ndarray, row_count: int
                  ) -> dict[str, np.ndarray]:
    global_target = np.zeros((152, row_count), dtype=np.float32)
    discounts = (1.0 / np.log2(np.arange(1024, dtype=np.float64) + 2.0)
                 ).astype(np.float32)
    for query in range(152):
        global_target[query, global_rows[query]] = discounts
    actionable = np.zeros_like(global_target)
    for query in range(152):
        rows = np.unique(doc_rows[np.asarray(oracle[query], dtype=np.int64)])
        actionable[query, rows] = 1.0
    normalized_global = global_target / np.maximum(
        global_target.max(axis=1, keepdims=True), 1.0e-7)
    hybrid = 0.5 * normalized_global + 0.5 * actionable
    return {"global": global_target, "actionable": actionable,
            "hybrid": hybrid.astype(np.float32)}


def predict(treatment: str, queries: np.ndarray, targets: dict[str, np.ndarray],
            train_indices: np.ndarray, evaluate_indices: np.ndarray,
            centroids: np.ndarray, ridge_lambda: float,
            random_features: np.ndarray, residual_weight: float
            ) -> tuple[np.ndarray, float]:
    started = time.perf_counter()
    if treatment == "nonlinear_random_gelu_256_hybrid":
        train = gelu(queries[train_indices] @ random_features)
        evaluate = gelu(queries[evaluate_indices] @ random_features)
        coefficients = ridge_coefficients(train, evaluate, ridge_lambda)
        scores = coefficients @ targets["hybrid"][train_indices]
    else:
        coefficients = ridge_coefficients(queries[train_indices],
                                           queries[evaluate_indices],
                                           ridge_lambda)
        target_name = ("global" if treatment.endswith("_global") else
                       "actionable" if treatment.endswith("_actionable") else
                       "hybrid")
        selected_targets = targets[target_name][train_indices]
        if "low_rank_32" in treatment:
            scores = low_rank_prediction(coefficients, selected_targets, 32)
        elif "low_rank_64" in treatment:
            scores = low_rank_prediction(coefficients, selected_targets, 64)
        else:
            scores = coefficients @ selected_targets
    if treatment == "centroid_k1_plus_low_rank_64_hybrid":
        centroid_scores = queries[evaluate_indices] @ centroids.T
        scores = row_standardize(centroid_scores) + residual_weight * row_standardize(
            scores)
    elapsed = (time.perf_counter() - started) * 1000.0 / len(evaluate_indices)
    return np.asarray(scores, dtype=np.float32), elapsed


def top_orders(scores: np.ndarray, occupied: np.ndarray,
               maximum: int) -> np.ndarray:
    result = np.empty((len(scores), maximum), dtype=np.uint32)
    for query, values in enumerate(scores):
        result[query] = replay.top_order(values, occupied, maximum)
    return result


def materialize_manifest(output: Path, router: str, arrays: dict[int, np.ndarray],
                         occupied_counts: dict[int, int], contract_path: Path,
                         layout_path: Path, k8_path: Path,
                         partition_semantics: str) -> Path:
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
        "router": router, "partition_semantics": partition_semantics,
        "contract_sha256": sha256(contract_path),
        "layout_manifest_sha256": sha256(layout_path),
        "k8_manifest_sha256": sha256(k8_path),
        "source_files_sha256": source_hashes(), "seeds": rows}
    path = output / "manifest.json"
    path.write_bytes(canonical(value))
    return path


def diagnostic(orders: dict[int, np.ndarray], common: dict[int, dict[str, Any]],
               budget: int, query_range: range,
               router_ms: dict[int, np.ndarray]) -> dict[str, Any]:
    global_coverage, actionable_coverage, prototype_counts, timings = [], [], [], []
    for seed in SEEDS:
        values = common[seed]
        for query in query_range:
            selected = orders[seed][query, :budget]
            global_coverage.append(len(set(map(int, selected)) & set(map(
                int, values["global_rows"][query]))) / 1024.0)
            actionable = set(map(int, values["actionable_rows"][query]))
            actionable_coverage.append(len(set(map(int, selected)) & actionable) /
                                       max(1, len(actionable)))
            prototype_counts.append(int(np.minimum(values["counts"][selected], 8).sum()))
            timings.append(float(router_ms[seed][query]))
    return {"mean_global_k8_top1024_coverage": float(np.mean(global_coverage)),
        "minimum_global_k8_top1024_coverage": min(global_coverage),
        "mean_actionable_address_coverage": float(np.mean(actionable_coverage)),
        "minimum_actionable_address_coverage": min(actionable_coverage),
        "mean_k8_prototypes_scored": float(np.mean(prototype_counts)),
        "logical_k8_bytes": float(np.mean(prototype_counts)) * 384 * 4,
        "directional_python_router_ms_per_query": float(np.mean(timings))}


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    args.output_root.mkdir(parents=True, exist_ok=True)
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    internal_requests = parent["requests"]
    require(len(config_value["requests"]) == len(internal_requests) == 76 and
            [row["request"] for row in config_value["requests"]] == list(range(76)) and
            [row["request"] for row in internal_requests] == list(range(76, 152)),
            "fixed Top-M query partitions differ")
    internal_value = dict(config_value)
    internal_value["partition"] = "locked_internal"
    internal_value["requests"] = internal_requests
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    data = exact.load_data(parent)
    request_rows = sorted(list(config_value["requests"]) + list(internal_requests),
                          key=lambda row: int(row["request"]))
    require([int(row["request"]) for row in request_rows] == list(range(152)),
            "fixed Top-M request sequence differs")
    native_positions = [int(row["native_query"]) for row in request_rows]
    oracle_by_native, _ = exact.scale.exact_oracle(data, native_positions, 10)
    oracle = {request: oracle_by_native[native]
              for request, native in enumerate(native_positions)}
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    maximum = max(contract["shortlist_budgets"])
    occupied_counts: dict[int, int] = {}
    common: dict[int, dict[str, Any]] = {}
    config_orders = {name: {} for name in TREATMENTS}
    internal_orders = {name: {} for name in TREATMENTS}
    config_ms = {name: {} for name in TREATMENTS}
    internal_ms = {name: {} for name in TREATMENTS}
    rng = np.random.default_rng(int(contract["random_feature_seed"]))
    random_features = (rng.standard_normal((384, 256)) /
                       math.sqrt(384.0)).astype(np.float32)
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        occupied = np.fromfile(root / descriptor(layout_seed["mappings"],
            "occupied_addresses")["file"], dtype="<u4")
        counts = np.fromfile(root / descriptor(layout_seed["mappings"],
            "address_counts")["file"], dtype="<u4")
        queries = np.fromfile(root / descriptor(layout_seed["mappings"],
            "query_vectors")["file"], dtype="<f4").reshape(152, 384)
        global_rows = np.fromfile(root / descriptor(layout_seed["mappings"],
            "shortlist_rows")["file"], dtype="<u4").reshape(152, 1024)
        centroids = replay.first_prototypes(k8_seed, counts)
        targets = target_matrix(global_rows, oracle, doc_rows[seed], len(occupied))
        actionable_rows = [np.flatnonzero(targets["actionable"][query])
                           for query in range(152)]
        common[seed] = {"occupied": occupied, "counts": counts,
            "global_rows": global_rows, "actionable_rows": actionable_rows}
        occupied_counts[seed] = len(occupied)
        for treatment in TREATMENTS:
            orders = np.empty((152, maximum), dtype=np.uint32)
            timings = np.empty(152, dtype=np.float64)
            for fold in range(4):
                evaluate = np.asarray([query for query in range(76)
                                       if query % 4 == fold], dtype=np.int64)
                train = np.asarray([query for query in range(76)
                                    if query % 4 != fold], dtype=np.int64)
                scores, elapsed = predict(treatment, queries, targets, train,
                    evaluate, centroids, float(contract["ridge_lambda"]),
                    random_features, float(contract["residual_weight"]))
                orders[evaluate] = top_orders(scores, occupied, maximum)
                timings[evaluate] = elapsed
            # These rows are never evaluated from the OOF manifest. A valid
            # deterministic placeholder prevents accidental internal leakage.
            orders[76:] = np.arange(maximum, dtype=np.uint32)
            timings[76:] = np.nan
            config_orders[treatment][seed] = orders
            config_ms[treatment][seed] = timings
            train = np.arange(76, dtype=np.int64)
            evaluate = np.arange(76, 152, dtype=np.int64)
            scores, elapsed = predict(treatment, queries, targets, train,
                evaluate, centroids, float(contract["ridge_lambda"]),
                random_features, float(contract["residual_weight"]))
            internal = orders.copy()
            internal[evaluate] = top_orders(scores, occupied, maximum)
            internal_orders[treatment][seed] = internal
            internal_timing = timings.copy()
            internal_timing[evaluate] = elapsed
            internal_ms[treatment][seed] = internal_timing
    manifests = {}
    configuration_manifest_bindings = {}
    for treatment in TREATMENTS:
        path = materialize_manifest(args.output_root / "shortlists" /
            "configuration" / treatment, treatment, config_orders[treatment],
            occupied_counts, args.contract, args.layout_manifest,
            args.k8_manifest, "four_fold_out_of_fold_configuration_only")
        manifests[treatment] = path
        configuration_manifest_bindings[treatment] = {
            "path": str(path.resolve()), "sha256": sha256(path)}
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
    gates = contract["quality_gates"]
    config_summaries = [replay.aggregate(config_reference, config_reference,
        reference_treatment, None, gates, None)]
    for treatment in TREATMENTS:
        for budget in contract["shortlist_budgets"]:
            point = f"{treatment}-m{budget}"
            current_protocol = replay.protocol(args.configuration_protocol,
                manifests[treatment], budget, args.output_root / "protocols" /
                f"configuration-{point}.json", contract)
            descriptor_value = {"id": point, "kind": "learned_router",
                                "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration",
                current_protocol, point, descriptor_value, config_inputs,
                config_references, False)
            summary = replay.aggregate(rows, config_reference, descriptor_value,
                budget, gates, diagnostic(config_orders[treatment], common,
                    budget, range(76), config_ms[treatment]))
            config_summaries.append(summary)
    candidates = []
    for treatment in TREATMENTS:
        rows = [row for row in config_summaries if row.get("address_budget") and
                row["id"].startswith(treatment + "-m")]
        passing = [row for row in rows if row["passes_registered_gate"]]
        candidates.append(min(passing, key=lambda row: (row["address_budget"],
            row["total_ms"]["p95"], row["id"])) if passing else min(rows,
            key=lambda row: (replay.gate_distance(row, gates),
                             row["address_budget"], row["id"])))
    candidates.sort(key=lambda row: (not row["passes_registered_gate"],
        replay.gate_distance(row, gates), row["address_budget"],
        row["total_ms"]["p95"], row["id"]))
    opened = candidates[:2]
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "locked_internal",
        internal_reference_protocol, "global-fp32-k8", reference_treatment,
        internal_inputs, internal_references, True)
    internal_summaries = [replay.aggregate(internal_reference,
        internal_reference, reference_treatment, None, gates, None)]
    internal_manifests = {}
    for selected in opened:
        treatment, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        manifest = materialize_manifest(args.output_root / "shortlists" /
            "locked_internal" / treatment, treatment,
            internal_orders[treatment], occupied_counts, args.contract,
            args.layout_manifest, args.k8_manifest,
            "selected_treatment_retrained_on_all_configuration_requests")
        internal_manifests[treatment] = {"path": str(manifest.resolve()),
                                        "sha256": sha256(manifest)}
        current_protocol = replay.protocol(internal_source, manifest, budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        descriptor_value = {"id": selected["id"], "kind": "learned_router",
                            "record_bytes": 0}
        rows = replay.run_point(args, contract, "locked_internal",
            current_protocol, selected["id"], descriptor_value, internal_inputs,
            internal_references, False)
        internal_summaries.append(replay.aggregate(rows, internal_reference,
            descriptor_value, budget, gates, diagnostic(
                internal_orders[treatment], common, budget, range(76, 152),
                internal_ms[treatment])))
    passing = [row for row in internal_summaries
        if row.get("passes_registered_gate") and row["id"] != "global_fp32_k8"]
    selected = (min(passing, key=lambda row: (row["address_budget"],
        row["total_ms"]["p95"], row["id"])) if passing else None)
    result = {"schema_version": 1,
        "family": "neuroute_fixed_top_m_router_frontier_result",
        "claim_scope": contract["claim_scope"],
        "inputs": {"contract_sha256": sha256(args.contract),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "configuration_protocol_closure_sha256": replay.protocol_closure(
                args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt},
        "configuration": config_summaries,
        "locked_internal": internal_summaries,
        "internal_opened_from_configuration": [row["id"] for row in opened],
        "configuration_shortlist_manifests": configuration_manifest_bindings,
        "internal_shortlist_manifests": internal_manifests,
        "decision": {"selected": selected,
            "fixed_top_m_passed": selected is not None,
            "activate_shortlist_generator_bakeoff": True,
            "native_integration_licensed": selected is not None,
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    train = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    coefficients = ridge_coefficients(train, train, 1.0e-6)
    require(np.allclose(coefficients, np.eye(2), atol=2.0e-6),
            "fixed Top-M ridge self-test failed")
    target = np.asarray([[1.0, 0.0, 1.0], [0.0, 1.0, 1.0]], dtype=np.float32)
    predicted = low_rank_prediction(np.eye(2, dtype=np.float32), target, 2)
    require(np.allclose(predicted, target, atol=1.0e-5),
            "fixed Top-M low-rank self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-fixed-top-m-router.example.json")
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
        "fixed Top-M router inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
