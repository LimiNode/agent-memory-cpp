#!/usr/bin/env python3
"""Measure nested random-ADC widths over eight predeclared projection seeds."""

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
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_nested_adc_planner", "plan-neuroute-nested-adc-replication.py")
parent = load("neuroute_nested_adc_parent", "run-neuroute-random-adc-ceiling.py")
conditional = parent.conditional
final = conditional.final


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(values: numpy.ndarray, dtype: str = "<f4") -> str:
    return hashlib.sha256(numpy.ascontiguousarray(values, dtype=dtype).tobytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-nested-adc-replication.py",
             "run-neuroute-nested-adc-replication.py",
             "run-neuroute-random-adc-ceiling.py",
             "run-neuroute-conditional-followups.py",
             "run-neuroute-final-representation.py")
    return {name: sha256(THIS / name) for name in names}


def projection(seed: int, maximum_width: int) -> numpy.ndarray:
    rng = numpy.random.default_rng(seed)
    return ((rng.integers(0, 2, size=(384, maximum_width), dtype=numpy.int8) * 2 - 1)
            .astype(numpy.float32) / math.sqrt(384.0))


def adc_stats(documents: numpy.ndarray, matrix: numpy.ndarray,
              column_batch: int) -> tuple[numpy.ndarray, numpy.ndarray, int]:
    count = min(len(documents), 100000)
    positions = numpy.linspace(0, len(documents) - 1, count, dtype=numpy.int64)
    sample = numpy.asarray(documents[positions], dtype=numpy.float32)
    threshold = numpy.empty(matrix.shape[1], dtype=numpy.float32)
    centroids = numpy.empty((matrix.shape[1], 2), dtype=numpy.float32)
    for start in range(0, matrix.shape[1], column_batch):
        stop = min(matrix.shape[1], start + column_batch)
        projected = sample @ matrix[:, start:stop]
        local_threshold = numpy.median(projected, axis=0).astype(numpy.float32)
        codes = projected >= local_threshold
        sums = numpy.stack((numpy.where(~codes, projected, 0.0).sum(axis=0, dtype=numpy.float64),
                            numpy.where(codes, projected, 0.0).sum(axis=0, dtype=numpy.float64)), axis=1)
        counts = numpy.stack(((~codes).sum(axis=0), codes.sum(axis=0)), axis=1)
        threshold[start:stop] = local_threshold
        centroids[start:stop] = numpy.asarray(sums / numpy.maximum(counts, 1), dtype=numpy.float32)
        del projected, codes, sums, counts
    del sample
    return threshold, centroids, count


def nested_rankings(documents: numpy.ndarray, query: numpy.ndarray, pool: numpy.ndarray,
                    ids: numpy.ndarray, matrix: numpy.ndarray, threshold: numpy.ndarray,
                    centroids: numpy.ndarray, widths: list[int]) -> dict[int, numpy.ndarray]:
    projected = numpy.asarray(documents[pool], dtype=numpy.float32) @ matrix
    query_projection = numpy.asarray(query, dtype=numpy.float32) @ matrix
    codes = projected >= threshold
    table = (query_projection[:, None] - centroids) ** 2
    per_bit = table[numpy.arange(matrix.shape[1])[None, :], codes.astype(numpy.uint8)]
    cumulative = numpy.cumsum(per_bit, axis=1, dtype=numpy.float32)
    return {width: pool[numpy.lexsort((ids[pool], cumulative[:, width - 1]))].astype(numpy.uint32)
            for width in widths}


def baseline_rows(data: dict[str, Any], positions: list[int], seed_pools: dict[int, numpy.ndarray]) -> tuple[
        list[dict[str, Any]], dict[tuple[int, int], float]]:
    rows = []
    values: dict[tuple[int, int], float] = {}
    for route_seed, pools in sorted(seed_pools.items()):
        queries = []
        for local, position in enumerate(positions):
            pool = pools[local]
            scores = numpy.asarray(data["documents"][pool], dtype=numpy.float32) @ data["queries"][position]
            ranking = pool[numpy.lexsort((data["document_ids"][pool], -scores))].astype(numpy.uint32)
            quality = final.scale.ndcg(data, position, ranking[:10])
            values[(route_seed, local)] = quality
            queries.append({"query": local, "ndcg_at_10": quality,
                            "ranked_sha256": conditional.sequence(ranking[:10])})
        rows.append({"route_seed": route_seed, "representation": "fp32", "query_count": len(queries),
                     "ndcg_at_10": float(numpy.mean([row["ndcg_at_10"] for row in queries])),
                     "queries": queries})
    return rows, values


def evaluate_seed(data: dict[str, Any], positions: list[int], seed_pools: dict[int, numpy.ndarray],
                  baseline: dict[tuple[int, int], float], matrix: numpy.ndarray,
                  threshold: numpy.ndarray, centroids: numpy.ndarray,
                  widths: list[int], projection_seed: int) -> list[dict[str, Any]]:
    accum = {(route_seed, width): [] for route_seed in seed_pools for width in widths}
    for route_seed, pools in sorted(seed_pools.items()):
        for local, position in enumerate(positions):
            rankings = nested_rankings(data["documents"], data["queries"][position], pools[local],
                                       data["document_ids"], matrix, threshold, centroids, widths)
            for width, ranking in rankings.items():
                quality = final.scale.ndcg(data, position, ranking[:10])
                accum[(route_seed, width)].append({
                    "query": local, "ndcg_at_10": quality,
                    "loss_vs_fp32": baseline[(route_seed, local)] - quality,
                    "ranked_sha256": conditional.sequence(ranking[:10])})
    rows = []
    for (route_seed, width), queries in sorted(accum.items()):
        rows.append({"projection_seed": projection_seed, "route_seed": route_seed,
                     "representation": f"adc{width}", "width": width,
                     "query_count": len(queries),
                     "ndcg_at_10": float(numpy.mean([row["ndcg_at_10"] for row in queries])),
                     "mean_loss_vs_fp32": float(numpy.mean([row["loss_vs_fp32"] for row in queries])),
                     "queries": queries})
    return rows


def global_exact_pools(data: dict[str, Any], positions: list[int], size: int) -> numpy.ndarray:
    pools = numpy.empty((len(positions), size), dtype=numpy.uint32)
    for local, position in enumerate(positions):
        scores = numpy.asarray(data["documents"] @ data["queries"][position], dtype=numpy.float32)
        pools[local] = final.scale.select_largest(scores, data["document_ids"], size)
    return pools


def calibration_rows(data: dict[str, Any], positions: list[int], pools: numpy.ndarray,
                     matrices: dict[int, numpy.ndarray], stats: dict[int, tuple[numpy.ndarray, numpy.ndarray, int]],
                     widths: list[int]) -> list[dict[str, Any]]:
    baseline = []
    for local, position in enumerate(positions):
        pool = pools[local]
        scores = numpy.asarray(data["documents"][pool], dtype=numpy.float32) @ data["queries"][position]
        ranking = pool[numpy.lexsort((data["document_ids"][pool], -scores))]
        baseline.append(final.scale.ndcg(data, position, ranking[:10]))
    rows = []
    for seed, matrix in sorted(matrices.items()):
        threshold, centroids, _ = stats[seed]
        losses = {width: [] for width in widths}
        digests = {width: hashlib.sha256() for width in widths}
        for local, position in enumerate(positions):
            rankings = nested_rankings(data["documents"], data["queries"][position], pools[local],
                                       data["document_ids"], matrix, threshold, centroids, widths)
            for width, ranking in rankings.items():
                quality = final.scale.ndcg(data, position, ranking[:10])
                losses[width].append(baseline[local] - quality)
                digests[width].update(local.to_bytes(4, "little"))
                digests[width].update(numpy.asarray(ranking[:10], dtype="<u4").tobytes())
        rows.extend({"projection_seed": seed, "width": width, "query_count": len(positions),
                     "mean_loss_vs_fp32": float(numpy.mean(losses[width])),
                     "ranking_sequence_sha256": digests[width].hexdigest()}
                    for width in widths)
    return rows


def load_datasets(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    roots = {language: {kind: getattr(args, f"{language}_{kind}_root")
                        for kind in ("result", "e5", "input")}
             for language in ("de", "fr", "ja")}
    v4_contract = final.exact.v4.planner.load_contract(args.v4_contract)
    by_id = {row["id"]: row for row in v4_contract["datasets"]}
    datasets: list[dict[str, Any]] = []
    training: dict[str, list[str]] = {}
    for dataset_id, language in (("de-25k", "de"), ("fr-25k", "fr"), ("ja-25k", "ja")):
        data, _, split = final.exact.v4.base.load_dataset(by_id[dataset_id], roots[language])
        datasets.append({"id": dataset_id, "data": data,
                         "query_ids": split["configuration_selection_query_ids"]})
        training[dataset_id] = split["training_query_ids"]
    scale_contract = final.scale.planner.load_contract(args.scale_contract)
    config = next(row for row in scale_contract["scales"] if row["id"] == "de-1m")
    data = final.scale.load_scale(config, args.de_1m_e5_root, args.de_1m_input_root)
    split = json.loads(args.german_split_result.read_text(encoding="utf-8"))["split"]
    datasets.append({"id": "de-1m", "data": data,
                     "query_ids": split["configuration_selection_query_ids"]})
    return datasets, training


def positions_for(data: dict[str, Any], query_ids: list[str]) -> list[int]:
    by_id = {str(value): index for index, value in enumerate(data["query_ids"])}
    require(all(value in by_id for value in query_ids), "nested ADC query IDs differ")
    return [by_id[value] for value in query_ids]


def summarize(calibration: list[dict[str, Any]], datasets: list[dict[str, Any]],
              contract: dict[str, Any]) -> dict[str, Any]:
    selected = {width: min((row for row in calibration if row["width"] == width),
                           key=lambda row: (row["mean_loss_vs_fp32"], row["projection_seed"]))
                for width in contract["widths"]}
    dataset_losses: dict[tuple[int, int, str], float] = {}
    for dataset in datasets:
        for seed in contract["projection_seeds"]:
            for width in contract["widths"]:
                rows = [row for row in dataset["rows"]
                        if row.get("projection_seed") == seed and row.get("width") == width]
                dataset_losses[(seed, width, dataset["id"])] = float(numpy.mean(
                    [row["mean_loss_vs_fp32"] for row in rows]))
    width_rows = []
    eligible = []
    for width in contract["widths"]:
        seed_means = []
        for seed in contract["projection_seeds"]:
            losses = [dataset_losses[(seed, width, dataset_id)] for dataset_id in contract["datasets"]]
            seed_means.append({"projection_seed": seed, "dataset_losses": losses,
                               "mean_loss": float(numpy.mean(losses))})
        values = numpy.asarray([row["mean_loss"] for row in seed_means], dtype=numpy.float64)
        selected_seed = int(selected[width]["projection_seed"])
        selected_losses = [dataset_losses[(selected_seed, width, dataset_id)]
                           for dataset_id in contract["datasets"]]
        gate = (float(numpy.mean(selected_losses))
                <= contract["quality"]["maximum_selected_seed_cross_dataset_mean_loss"]
                and max(selected_losses)
                <= contract["quality"]["maximum_selected_seed_per_dataset_loss"])
        if gate:
            eligible.append(width)
        width_rows.append({"width": width, "calibration_selected_seed": selected_seed,
                           "calibration_loss": selected[width]["mean_loss_vs_fp32"],
                           "selected_seed_dataset_losses": selected_losses,
                           "selected_seed_mean_loss": float(numpy.mean(selected_losses)),
                           "selected_seed_quality_eligible": gate,
                           "all_seed_mean_loss": float(values.mean()),
                           "all_seed_std_loss": float(values.std()),
                           "all_seed_p10_loss": float(numpy.quantile(values, 0.1)),
                           "all_seed_p50_loss": float(numpy.quantile(values, 0.5)),
                           "all_seed_p90_loss": float(numpy.quantile(values, 0.9)),
                           "per_dataset_all_seed": [{"dataset": dataset_id,
                               "mean_loss": float(numpy.mean([dataset_losses[(seed, width, dataset_id)]
                                                              for seed in contract["projection_seeds"]])),
                               "std_loss": float(numpy.std([dataset_losses[(seed, width, dataset_id)]
                                                            for seed in contract["projection_seeds"]]))}
                               for dataset_id in contract["datasets"]],
                           "seeds": seed_means})
    return {"widths": width_rows, "selected_candidate_width": min(eligible) if eligible else None,
            "physical_benchmark_licensed": bool(eligible),
            "production_selection_licensed": False,
            "held_out_seed_cherry_picking_performed": False}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"random_ceiling_result_sha256": sha256(args.random_ceiling_result),
              "random_ceiling_evidence_sha256": sha256(args.random_ceiling_evidence),
              "final_materialization_sha256": sha256(args.final_materialization_root / "manifest.json")}
    require(actual == contract["activation"], "nested ADC activation differs")
    parent_result = json.loads(args.random_ceiling_result.read_text(encoding="utf-8"))
    parent_evidence = json.loads(args.random_ceiling_evidence.read_text(encoding="utf-8"))
    require(parent_result.get("family") == "neuroute_random_overcomplete_adc_ceiling_result"
            and parent_evidence.get("passed") is True,
            "nested ADC parent gate differs")
    manifest = json.loads((args.final_materialization_root / "manifest.json").read_text(encoding="utf-8"))
    manifest_by_id = {row["id"]: row for row in manifest["datasets"]}
    datasets, training = load_datasets(args)
    maximum = max(contract["widths"])
    matrices = {seed: projection(seed, maximum) for seed in contract["projection_seeds"]}
    projection_provenance = [{"projection_seed": seed,
                              "master_sha256": bytes_sha256(matrix),
                              "prefixes": [{"width": width,
                                            "sha256": bytes_sha256(matrix[:, :width])}
                                           for width in contract["widths"]]}
                             for seed, matrix in sorted(matrices.items())]
    reports, calibration = [], []
    for dataset in datasets:
        dataset_id, data = dataset["id"], dataset["data"]
        positions = positions_for(data, dataset["query_ids"])
        seed_pools = conditional.pools(manifest_by_id[dataset_id], args.final_materialization_root)
        baseline, baseline_values = baseline_rows(data, positions, seed_pools)
        stats: dict[int, tuple[numpy.ndarray, numpy.ndarray, int]] = {}
        stats_provenance = []
        rows = list(baseline)
        for seed, matrix in sorted(matrices.items()):
            threshold, centroids, count = adc_stats(
                data["documents"], matrix, contract["projection"]["statistics_column_batch"])
            stats[seed] = (threshold, centroids, count)
            stats_provenance.append({"projection_seed": seed, "sample_count": count,
                                     "threshold_sha256": bytes_sha256(threshold),
                                     "centroids_sha256": bytes_sha256(centroids)})
            rows.extend(evaluate_seed(data, positions, seed_pools, baseline_values, matrix,
                                      threshold, centroids, contract["widths"], seed))
        if dataset_id == "de-25k":
            calibration_positions = positions_for(data, training[dataset_id])
            exact_pools = global_exact_pools(data, calibration_positions,
                                             contract["frozen_input"]["pool_size"])
            calibration = calibration_rows(data, calibration_positions, exact_pools,
                                           matrices, stats, contract["widths"])
        reports.append({"id": dataset_id, "query_count": len(positions),
                        "rows": rows, "adc_statistics": stats_provenance})
        del data, stats, rows, baseline, baseline_values
        gc.collect()
    require(len(calibration) == planner.plan(contract)["calibration_rows"],
            "nested ADC calibration matrix differs")
    result = {"schema_version": 1, "family": "neuroute_nested_multiseed_adc_replication_result",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
              "activation": actual, "source_files_sha256": source_hashes(),
              "matrix": planner.plan(contract), "projection_provenance": projection_provenance,
              "calibration": calibration, "datasets": reports}
    result["decision"] = summarize(calibration, reports, contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-nested-adc-replication.example.json")
    matrix = projection(contract["projection_seeds"][0], max(contract["widths"]))
    prefix = matrix[:, :512]
    require(matrix.shape == (384, 4096)
            and prefix.shape == (384, 512) and numpy.shares_memory(matrix, prefix)
            and planner.plan(contract)["held_out_adc_rows"] == 672,
            "nested ADC self-test differs")
    print("NeuRoute nested ADC replication self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-nested-adc-replication.example.json")
    parser.add_argument("--random-ceiling-result", type=Path)
    parser.add_argument("--random-ceiling-evidence", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{kind}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all nested ADC paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, MemoryError) as error:
        print(f"run-neuroute-nested-adc-replication: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
