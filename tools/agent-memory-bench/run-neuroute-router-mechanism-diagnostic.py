#!/usr/bin/env python3
"""Diagnose frozen NeuRoute document partitions and query probe ordering."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_router_mechanism_planner",
               "plan-neuroute-router-mechanism-diagnostic.py")
width = load("neuroute_router_mechanism_width", "run-neuroute-width-scale-budget.py")
probe = load("neuroute_router_mechanism_probe", "diagnose-neuroute-v2-collisions.py")
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
    names = ("plan-neuroute-router-mechanism-diagnostic.py",
             "run-neuroute-router-mechanism-diagnostic.py",
             "run-neuroute-width-scale-budget.py",
             "run-neuroute-frozen-scale-transfer.py",
             "diagnose-neuroute-v2-collisions.py")
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> None:
    actual = {
        "width_result_sha256": sha256(args.width_result),
        "width_evidence_sha256": sha256(args.width_evidence),
        "width_materialization_sha256": sha256(args.width_materialization_root / "manifest.json"),
        "wider_result_sha256": sha256(args.wider_result),
        "wider_evidence_sha256": sha256(args.wider_evidence),
    }
    require(actual == contract["activation"], "router mechanism activation bytes differ")
    for path, family in (
            (args.width_evidence, "neuroute_width_scale_budget_evidence"),
            (args.wider_evidence, "neuroute_wider_training_sufficiency_evidence")):
        evidence = json.loads(path.read_text(encoding="utf-8"))
        require(evidence.get("family") == family and evidence.get("passed") is True,
                f"router mechanism parent evidence differs: {family}")


def array_path(root: Path, descriptor: dict[str, Any]) -> Path:
    path = Path(descriptor["file"])
    return path if descriptor.get("external_frozen_root") is True else root / path


def read_array(root: Path, descriptor: dict[str, Any]) -> numpy.ndarray:
    path = array_path(root, descriptor)
    require(path.is_file() and sha256(path) == descriptor["sha256"],
            f"router mechanism payload differs: {path}")
    dtype = numpy.dtype(descriptor["dtype"])
    shape = tuple(int(value) for value in descriptor["shape"])
    require(path.stat().st_size == int(numpy.prod(shape, dtype=numpy.int64)) * dtype.itemsize,
            f"router mechanism payload size differs: {path}")
    return numpy.memmap(path, dtype=dtype, mode="r", shape=shape)


def exact_top_k(documents: numpy.ndarray, queries: numpy.ndarray, limit: int,
                batch_size: int = 32768) -> numpy.ndarray:
    query_count = queries.shape[0]
    best_scores = numpy.full((query_count, limit), -numpy.inf, dtype=numpy.float32)
    best_positions = numpy.full((query_count, limit), -1, dtype=numpy.int64)
    query_matrix = numpy.asarray(queries, dtype=numpy.float32).T
    for start in range(0, documents.shape[0], batch_size):
        stop = min(documents.shape[0], start + batch_size)
        scores = numpy.asarray(documents[start:stop], dtype=numpy.float32) @ query_matrix
        local_limit = min(limit, stop - start)
        for query in range(query_count):
            local = numpy.argpartition(scores[:, query], -local_limit)[-local_limit:]
            positions = local.astype(numpy.int64) + start
            values = scores[local, query]
            merged_scores = numpy.concatenate((best_scores[query], values))
            merged_positions = numpy.concatenate((best_positions[query], positions))
            valid = merged_positions >= 0
            order = numpy.lexsort((merged_positions[valid], -merged_scores[valid]))[:limit]
            best_scores[query].fill(-numpy.inf)
            best_positions[query].fill(-1)
            best_scores[query, :order.size] = merged_scores[valid][order]
            best_positions[query, :order.size] = merged_positions[valid][order]
        del scores
    require(numpy.all(best_positions >= 0), "router mechanism exact top-k is incomplete")
    return best_positions


def count_entropy(values: numpy.ndarray) -> float:
    _, counts = numpy.unique(values, return_counts=True)
    probabilities = counts.astype(numpy.float64) / float(counts.sum())
    return float(-(probabilities * numpy.log2(probabilities)).sum())


def minimum_posting_cost(relevant_addresses: numpy.ndarray, posting_counts: numpy.ndarray,
                         target: int) -> tuple[int, int]:
    addresses, gains = numpy.unique(relevant_addresses, return_counts=True)
    total = int(gains.sum())
    costs = posting_counts[addresses].astype(numpy.int64)
    infinity = numpy.iinfo(numpy.int64).max // 4
    best = numpy.full(total + 1, infinity, dtype=numpy.int64)
    best[0] = 0
    for gain, cost in zip(gains.tolist(), costs.tolist()):
        updated = best.copy()
        for covered in range(total + 1):
            if best[covered] == infinity:
                continue
            next_covered = min(total, covered + int(gain))
            updated[next_covered] = min(updated[next_covered], best[covered] + int(cost))
        best = updated
    minimum_cost = int(best[target:].min())
    descending = numpy.sort(gains)[::-1]
    minimum_probes = int(numpy.searchsorted(numpy.cumsum(descending), target, side="left") + 1)
    return minimum_cost, minimum_probes


def rank_values(values: numpy.ndarray) -> numpy.ndarray:
    order = numpy.argsort(values, kind="stable")
    ranks = numpy.empty(values.size, dtype=numpy.float64)
    start = 0
    while start < values.size:
        stop = start + 1
        while stop < values.size and values[order[stop]] == values[order[start]]:
            stop += 1
        ranks[order[start:stop]] = (start + stop - 1) / 2.0
        start = stop
    return ranks


def spearman(left: list[float], right: list[float]) -> float:
    x = rank_values(numpy.asarray(left, dtype=numpy.float64))
    y = rank_values(numpy.asarray(right, dtype=numpy.float64))
    if float(x.std()) == 0.0 or float(y.std()) == 0.0:
        return 0.0
    return float(numpy.corrcoef(x, y)[0, 1])


def query_uncertainty(logits: numpy.ndarray) -> float:
    clipped = numpy.clip(logits.astype(numpy.float64), -30.0, 30.0)
    probability = 1.0 / (1.0 + numpy.exp(-clipped))
    entropy = -(probability * numpy.log2(probability) +
                (1.0 - probability) * numpy.log2(1.0 - probability))
    return float(entropy.mean())


def summarize(values: list[float]) -> dict[str, float]:
    array = numpy.asarray(values, dtype=numpy.float64)
    return {"mean": float(array.mean()), "p50": float(numpy.quantile(array, 0.5)),
            "p95": float(numpy.quantile(array, 0.95))}


def diagnose_route(regime: str, scale_id: str, width_bits: int, seed: int,
                   model_sha256: str, addresses: numpy.ndarray, logits: numpy.ndarray,
                   top_positions: numpy.ndarray, evaluation: dict[str, Any]) -> dict[str, Any]:
    require(addresses.ndim == 1 and logits.shape == (top_positions.shape[0], width_bits),
            "router mechanism route shape differs")
    posting_counts = numpy.bincount(addresses, minlength=1 << width_bits).astype(numpy.int64)
    document_count = addresses.size
    query_rows = []
    uncertainties: list[float] = []
    early_utilities: list[float] = []
    for query in range(logits.shape[0]):
        requested = numpy.asarray(
            probe.addresses(logits[query], width_bits, 1 << width_bits), dtype=numpy.uint32)
        require(numpy.unique(requested).size == 1 << width_bits,
                "router mechanism probe order is not a permutation")
        inverse = numpy.empty(1 << width_bits, dtype=numpy.int64)
        inverse[requested] = numpy.arange(requested.size, dtype=numpy.int64)
        cumulative_mass = numpy.cumsum(posting_counts[requested], dtype=numpy.int64)
        base_address = int(requested[0])
        uncertainty = query_uncertainty(logits[query])
        row: dict[str, Any] = {"query": query, "logit_uncertainty": uncertainty, "top_k": {}}
        for top_k in evaluation["exact_e5_top_k"]:
            relevant = addresses[top_positions[query, :top_k]]
            ranks = inverse[relevant]
            distances = numpy.asarray(
                [(int(value) ^ base_address).bit_count() for value in relevant],
                dtype=numpy.float64)
            metrics: dict[str, Any] = {
                "distinct_address_count": int(numpy.unique(relevant).size),
                "address_entropy_bits": count_entropy(relevant),
                "hamming_distance_p50": float(numpy.quantile(distances, 0.5)),
                "hamming_distance_p95": float(numpy.quantile(distances, 0.95)),
                "radius_coverage": {
                    str(radius): float((distances <= radius).mean())
                    for radius in evaluation["hamming_radii"]
                },
                "coverage": {},
            }
            sorted_ranks = numpy.sort(ranks)
            for target_fraction in evaluation["coverage_targets"]:
                target = int(math.ceil(top_k * target_fraction))
                current_rank = int(sorted_ranks[target - 1])
                current_mass = int(cumulative_mass[current_rank])
                oracle_mass, oracle_probes = minimum_posting_cost(
                    relevant, posting_counts, target)
                metrics["coverage"][str(target_fraction)] = {
                    "target_documents": target,
                    "current_probe_count": current_rank + 1,
                    "current_candidate_fraction": current_mass / document_count,
                    "oracle_min_probe_count": oracle_probes,
                    "oracle_min_candidate_fraction": oracle_mass / document_count,
                    "current_over_oracle_mass": current_mass / max(oracle_mass, 1),
                }
            row["top_k"][str(top_k)] = metrics
        early = requested[:evaluation["early_probe_budget"]]
        early_mass = int(posting_counts[early].sum()) / document_count
        early_relevant = addresses[top_positions[query, :100]]
        early_coverage = float(numpy.isin(early_relevant, early).mean())
        early_utility = early_coverage / max(early_mass, 1.0 / document_count)
        row["early_probe_candidate_fraction"] = early_mass
        row["early_probe_top100_coverage"] = early_coverage
        row["early_probe_utility"] = early_utility
        query_rows.append(row)
        uncertainties.append(uncertainty)
        early_utilities.append(early_utility)
    summary: dict[str, Any] = {
        "occupied_address_count": int((posting_counts > 0).sum()),
        "mean_posting_length": float(posting_counts[posting_counts > 0].mean()),
        "logit_uncertainty_vs_early_utility_spearman": spearman(uncertainties, early_utilities),
        "early_probe_candidate_fraction": summarize(
            [row["early_probe_candidate_fraction"] for row in query_rows]),
        "early_probe_top100_coverage": summarize(
            [row["early_probe_top100_coverage"] for row in query_rows]),
        "top_k": {},
    }
    for top_k in evaluation["exact_e5_top_k"]:
        key = str(top_k)
        top_summary: dict[str, Any] = {
            "distinct_address_count": summarize(
                [row["top_k"][key]["distinct_address_count"] for row in query_rows]),
            "address_entropy_bits": summarize(
                [row["top_k"][key]["address_entropy_bits"] for row in query_rows]),
            "hamming_distance_p50": summarize(
                [row["top_k"][key]["hamming_distance_p50"] for row in query_rows]),
            "hamming_distance_p95": summarize(
                [row["top_k"][key]["hamming_distance_p95"] for row in query_rows]),
            "radius_coverage_mean": {
                str(radius): float(numpy.mean([
                    row["top_k"][key]["radius_coverage"][str(radius)] for row in query_rows
                ])) for radius in evaluation["hamming_radii"]
            },
            "coverage": {},
        }
        for target_fraction in evaluation["coverage_targets"]:
            target_key = str(target_fraction)
            top_summary["coverage"][target_key] = {
                metric: summarize([
                    row["top_k"][key]["coverage"][target_key][metric] for row in query_rows
                ]) for metric in (
                    "current_probe_count", "current_candidate_fraction",
                    "oracle_min_probe_count", "oracle_min_candidate_fraction",
                    "current_over_oracle_mass")
            }
        summary["top_k"][key] = top_summary
    return {
        "regime": regime, "scale": scale_id, "width": width_bits, "seed": seed,
        "model_sha256": model_sha256, "query_count": logits.shape[0],
        "summary": summary, "queries": query_rows,
    }


def model_map(result: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    return {(int(row["width"]), int(row["seed"])): row for row in result["models"]}


def old_route_rows(contract: dict[str, Any], result: dict[str, Any], manifest: dict[str, Any],
                   root: Path) -> tuple[list[dict[str, Any]], dict[str, numpy.ndarray]]:
    models = model_map(result)
    rows: list[dict[str, Any]] = []
    top_by_scale: dict[str, numpy.ndarray] = {}
    datasets = {row["id"]: row for row in manifest["datasets"]}
    for scale_id in contract["old_strong_routes"]["scales"]:
        dataset = datasets[scale_id]
        dataset_root = root / scale_id
        documents = read_array(dataset_root, dataset["common"]["document_vectors"])
        queries = read_array(dataset_root, dataset["common"]["query_vectors"])
        require(queries.shape[0] == contract["evaluation"]["queries"],
                "router mechanism query count differs")
        top_positions = exact_top_k(documents, queries,
                                    max(contract["evaluation"]["exact_e5_top_k"]))
        top_by_scale[scale_id] = top_positions
        routes = {row["id"]: row for row in dataset["routes"]}
        for width_bits in contract["old_strong_routes"]["widths"]:
            for seed in contract["old_strong_routes"]["seeds"]:
                route = routes[f"width-{width_bits}-seed-{seed}"]
                model = models[(width_bits, seed)]
                require(route["model_sha256"] == model["sha256"],
                        "router mechanism old model binding differs")
                route_root = dataset_root / route["id"]
                addresses = read_array(route_root, route["document_addresses"])
                logits = read_array(route_root, route["query_logits"])
                rows.append(diagnose_route(
                    contract["old_strong_routes"]["regime"], scale_id, width_bits, seed,
                    model["sha256"], addresses, logits, top_positions, contract["evaluation"]))
        del documents, queries
        gc.collect()
    return rows, top_by_scale


def scalable_rows(contract: dict[str, Any], wider_result: dict[str, Any], root: Path,
                  manifest: dict[str, Any], materialization_root: Path,
                  top_positions: numpy.ndarray) -> list[dict[str, Any]]:
    scale_id = contract["scalable_controls"]["scale"]
    dataset = next(row for row in manifest["datasets"] if row["id"] == scale_id)
    dataset_root = materialization_root / scale_id
    documents = read_array(dataset_root, dataset["common"]["document_vectors"])
    queries = read_array(dataset_root, dataset["common"]["query_vectors"])
    by_key = {(row["regime"], int(row["width"]), int(row["seed"])): row
              for row in wider_result["models"]}
    rows = []
    for regime in contract["scalable_controls"]["regimes"]:
        for width_bits in contract["scalable_controls"]["widths"]:
            for seed in contract["scalable_controls"]["seeds"]:
                entry = by_key[(regime, width_bits, seed)]
                path = root / entry["file"]
                require(path.is_file() and sha256(path) == entry["sha256"],
                        "router mechanism scalable model bytes differ")
                arrays, _ = trainer.read_model(path)
                raw_documents = width.scale.infer_batched(documents, arrays)
                threshold = numpy.median(raw_documents, axis=0).astype(numpy.float32)
                addresses = width.addresses_from_logits(raw_documents - threshold)
                logits = width.scale.infer_batched(queries, arrays) - threshold
                rows.append(diagnose_route(
                    regime, scale_id, width_bits, seed, entry["sha256"], addresses,
                    logits, top_positions, contract["evaluation"]))
                del raw_documents, addresses, logits
                gc.collect()
    del documents, queries
    gc.collect()
    return rows


def decision(rows: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rule = contract["decision"]
    top_key = str(rule["top_k"])
    coverage_key = str(rule["coverage_target"])
    summaries = []
    qualifying_widths = []
    for width_bits in (14, 16):
        seeds = []
        for row in rows:
            if (row["regime"] != contract["old_strong_routes"]["regime"] or
                    row["scale"] != rule["scale"] or row["width"] != width_bits):
                continue
            coverage = row["summary"]["top_k"][top_key]["coverage"][coverage_key]
            oracle_p95 = coverage["oracle_min_candidate_fraction"]["p95"]
            current_p50 = coverage["current_candidate_fraction"]["p50"]
            oracle_p50 = coverage["oracle_min_candidate_fraction"]["p50"]
            qualifies = (oracle_p95 <= rule["maximum_oracle_p95_candidate_fraction"] and
                         current_p50 - oracle_p50 >=
                         rule["minimum_current_minus_oracle_p50_candidate_fraction"])
            seeds.append({"seed": row["seed"], "oracle_p95_candidate_fraction": oracle_p95,
                          "current_p50_candidate_fraction": current_p50,
                          "oracle_p50_candidate_fraction": oracle_p50,
                          "current_minus_oracle_p50": current_p50 - oracle_p50,
                          "qualifies": qualifies})
        fraction = float(numpy.mean([row["qualifies"] for row in seeds]))
        width_qualifies = fraction >= rule["minimum_qualifying_seed_fraction"]
        if width_qualifies:
            qualifying_widths.append(width_bits)
        summaries.append({"width": width_bits, "qualifying_seed_fraction": fraction,
                          "qualifies": width_qualifies, "seeds": seeds})
    return {
        "widths": summaries, "qualifying_widths": qualifying_widths,
        "scheduler_followup_activated": bool(qualifying_widths),
        "document_geometry_followup_required": not bool(qualifying_widths),
        "production_selection_licensed": False,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    validate_activation(contract, args)
    width_result = json.loads(args.width_result.read_text(encoding="utf-8"))
    wider_result = json.loads(args.wider_result.read_text(encoding="utf-8"))
    manifest_path = args.width_materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(width_result.get("family") == "neuroute_width_scale_budget_quality_result" and
            wider_result.get("family") == "neuroute_wider_training_sufficiency_result" and
            manifest.get("family") == "neuroute_width_scale_budget_native_materialization",
            "router mechanism parent family differs")
    require(manifest.get("quality_result_sha256") == sha256(args.width_result),
            "router mechanism materialization result binding differs")
    old, top_by_scale = old_route_rows(
        contract, width_result, manifest, args.width_materialization_root)
    scalable = scalable_rows(
        contract, wider_result, args.wider_model_root, manifest,
        args.width_materialization_root,
        top_by_scale[contract["scalable_controls"]["scale"]])
    rows = old + scalable
    output = {
        "schema_version": 1, "family": "neuroute_router_mechanism_diagnostic_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": contract["activation"], "source_files_sha256": source_hashes(),
        "matrix": planner.plan(contract), "rows": rows,
        "decision": decision(rows, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-router-mechanism-diagnostic.example.json")
    relevant = numpy.asarray([1, 1, 2, 3], dtype=numpy.uint32)
    counts = numpy.asarray([0, 10, 1, 2], dtype=numpy.int64)
    cost, probes = minimum_posting_cost(relevant, counts, 3)
    require((cost, probes) == (11, 2), "router mechanism oracle self-test differs")
    require(planner.plan(contract) == {"old_route_rows": 27, "scalable_control_rows": 12,
                                       "queries_per_row": 76, "exact_neighbour_sets": 3},
            "router mechanism matrix self-test differs")
    print("NeuRoute router mechanism diagnostic self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-router-mechanism-diagnostic.example.json")
    parser.add_argument("--width-result", type=Path)
    parser.add_argument("--width-evidence", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--wider-result", type=Path)
    parser.add_argument("--wider-evidence", type=Path)
    parser.add_argument("--wider-model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all router mechanism paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-router-mechanism-diagnostic: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
