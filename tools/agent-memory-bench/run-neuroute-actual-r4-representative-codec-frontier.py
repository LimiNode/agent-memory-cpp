#!/usr/bin/env python3
"""Evaluate K32/R0 scalar codecs on physical full-R4 K8 snapshots."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
DIMENSIONS = 384
ADDRESSES = 1024
FEATURES = 22
MAXIMA_ALGORITHM = "actual_r4_scalar_codec_maxima_v1"
LEGACY_MAXIMA_SOURCE_SHA256 = (
    "1aecf6f03e6a511e3f1221fdb6aba87e0692c47dc68e78148237aebf63646fcd")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_actual_r4_rep_planner",
               "plan-neuroute-actual-r4-codec-frontier.py")
legacy = load("neuroute_actual_r4_rep_legacy",
              "run-neuroute-r4-representative-codec.py")
coverage = legacy.coverage
scale = legacy.scale
prototype = legacy.prototype


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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def array_sha256(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def read_descriptor(root: Path, row: dict[str, Any]) -> np.ndarray:
    path = root / row["file"]
    require(path.is_file() and path.stat().st_size == row["bytes"] and
            sha256(path) == row["sha256"],
            f"actual-R4 representative mapping differs: {row['role']}")
    return np.memmap(path, mode="r", dtype=row["dtype"],
                     shape=tuple(row["shape"]))


def read_snapshot(report_path: Path, requests: list[dict[str, Any]]) -> tuple[
        dict[str, Any], np.ndarray, np.ndarray]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(report.get("family") ==
                "neuroute_external_ann_comparison_r4_samples" and
            report.get("storage_mode") == "int8" and
            report.get("workers") == 1 and len(report["samples"]) == 1,
            "actual-R4 representative physical report identity differs")
    sample = report["samples"][0]
    rows = sample["queries"]
    require(len(rows) == len(requests) and all(
        (row["request"], row["native_query"]) ==
        (request["request"], request["native_query"])
        for row, request in zip(rows, requests)),
        "actual-R4 representative request order differs")
    snapshot = sample["coarse_snapshot"]
    values = []
    for role, dtype, shape in (("rows", "<u4", (len(rows), ADDRESSES)),
                               ("features", "<f4",
                                (len(rows), ADDRESSES, FEATURES))):
        descriptor = snapshot[role]
        path = Path(descriptor["path"])
        require(descriptor["dtype"] == dtype and
                descriptor["shape"] == list(shape) and path.is_file() and
                path.stat().st_size == descriptor["bytes"] and
                sha256(path) == descriptor["sha256"],
                f"actual-R4 representative coarse {role} differs")
        values.append(np.memmap(path, mode="r", dtype=dtype, shape=shape))
    return report, values[0], values[1]


def layout_seed(manifest: dict[str, Any], seed: int) -> dict[str, Any]:
    return next(row for row in manifest["seeds"] if int(row["seed"]) == seed)


def treatment_groups(treatments: list[dict[str, Any]]) -> dict[
        tuple[str, float], list[dict[str, Any]]]:
    result: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for row in treatments:
        if row["kind"] == "integer":
            compander = row["compander"]
            key = (compander["kind"], float(compander["parameter"]))
            result.setdefault(key, []).append(row)
    return result


def inverse_compand(quantized: np.ndarray, kind: str,
                     parameter: float) -> np.ndarray:
    if kind == "uniform":
        return quantized
    if kind == "power":
        return np.copysign(np.power(np.abs(quantized), 1.0 / parameter),
                           quantized)
    require(kind == "mulaw", "actual-R4 representative compander differs")
    return np.copysign(
        np.expm1(np.abs(quantized) * np.log1p(parameter)) / parameter,
        quantized)


def forward_compand(normalized: np.ndarray, kind: str,
                     parameter: float) -> np.ndarray:
    if kind == "uniform":
        return normalized
    if kind == "power":
        return np.copysign(np.power(np.abs(normalized), parameter), normalized)
    require(kind == "mulaw", "actual-R4 representative compander differs")
    return np.copysign(
        np.log1p(parameter * np.abs(normalized)) / np.log1p(parameter),
        normalized)


def maxima_for_treatments(queries: np.ndarray, shortlist_rows: np.ndarray,
                          vectors: np.ndarray, offsets: np.ndarray,
                          counts: np.ndarray,
                          treatments: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    result = {row["id"]: np.empty((len(queries), ADDRESSES), dtype=np.float32)
              for row in treatments}
    groups = treatment_groups(treatments)
    by_id = {row["id"]: row for row in treatments}
    for query_index, query in enumerate(queries):
        rows = np.asarray(shortlist_rows[query_index], dtype=np.int64)
        current_counts = np.asarray(counts[rows], dtype=np.int64)
        require(np.all(current_counts > 0),
                "actual-R4 representative shortlist contains empty address")
        starts = np.zeros(ADDRESSES, dtype=np.int64)
        starts[1:] = np.cumsum(current_counts[:-1], dtype=np.int64)
        physical = np.concatenate([
            np.arange(int(offsets[row]), int(offsets[row] + count),
                      dtype=np.int64)
            for row, count in zip(rows, current_counts)])
        source = np.asarray(vectors[physical], dtype=np.float32)
        query_value = np.asarray(query, dtype=np.float32)
        if "fp32" in result:
            scores = np.asarray(source @ query_value, dtype=np.float32)
            result["fp32"][query_index] = np.maximum.reduceat(scores, starts)
        if "fp16" in result:
            scores = np.asarray(source.astype(np.float16).astype(np.float32) @
                                query_value, dtype=np.float32)
            result["fp16"][query_index] = np.maximum.reduceat(scores, starts)
        integer_ids = {row["id"] for rows in groups.values() for row in rows}
        if integer_ids & result.keys():
            amplitudes = np.max(np.abs(source), axis=1).astype(np.float32)
            safe = np.where(amplitudes == 0.0, 1.0, amplitudes)
            normalized = np.asarray(source / safe[:, None], dtype=np.float32)
            for (kind, parameter), rows_for_compander in groups.items():
                active = [row for row in rows_for_compander
                          if row["id"] in result]
                if not active:
                    continue
                transformed = forward_compand(normalized, kind, parameter)
                for treatment in active:
                    levels = (1 << (int(treatment["bits"]) - 1)) - 1
                    quantized = np.rint(
                        transformed * np.float32(levels)).astype(
                            np.float32, copy=False)
                    np.clip(quantized, -levels, levels, out=quantized)
                    quantized *= np.float32(1.0 / levels)
                    reconstructed = inverse_compand(quantized, kind, parameter)
                    scores = np.asarray((reconstructed @ query_value) * safe,
                                        dtype=np.float32)
                    result[treatment["id"]][query_index] = np.maximum.reduceat(
                        scores, starts)
        del physical, source
    require(set(result) == set(by_id),
            "actual-R4 representative treatment maximum matrix differs")
    return result


def native_score_matrix(native_benchmark: Path, protocol_path: Path,
                        seed: int, coarse_rows: np.ndarray,
                        scalar_features: np.ndarray,
                        maximums: dict[str, np.ndarray],
                        treatments: list[dict[str, Any]], name: str,
                        checkpoint_root: Path) -> np.ndarray:
    shape = (len(treatments), len(coarse_rows), ADDRESSES)
    maxima_path = checkpoint_root / f"{name}-{seed}-native-maxima.f32le"
    scores_path = checkpoint_root / f"{name}-{seed}-native-scores.f32le"
    payload = np.memmap(maxima_path, mode="w+", dtype="<f4", shape=shape)
    for index, treatment in enumerate(treatments):
        payload[index] = maximums[treatment["id"]]
    payload.flush()
    del payload
    fp32_index = next(index for index, treatment in enumerate(treatments)
                      if treatment["id"] == "fp32")
    int8_index = next(index for index, treatment in enumerate(treatments)
                      if treatment["id"] == "int8_uniform")
    subprocess.run([str(native_benchmark), "--score-external-r4-maxima",
        str(protocol_path), str(seed), str(Path(coarse_rows.filename)),
        str(Path(scalar_features.filename)), str(maxima_path), str(fp32_index),
        str(int8_index), str(scores_path)], check=True)
    require(scores_path.is_file() and
            scores_path.stat().st_size == int(np.prod(shape)) * 4,
            "actual-R4 representative native score payload differs")
    return np.memmap(scores_path, mode="r", dtype="<f4", shape=shape)


def quality(rows: list[dict[str, Any]], treatments: list[dict[str, Any]],
            gates: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for treatment in treatments:
        current = [coverage.headline(row, .005) for row in rows
                   if row["treatment"] == treatment["id"]]
        reference = [coverage.headline(row, .005) for row in rows
                     if row["treatment"] == "fp32"]
        require(len(current) == len(reference) == 3,
                "actual-R4 representative quality seed matrix differs")
        actionable = [reference[index]["actionable_gain_coverage"] -
                      current[index]["actionable_gain_coverage"]
                      for index in range(3)]
        ndcg = [reference[index]["exact_ndcg_at_10"] -
                current[index]["exact_ndcg_at_10"] for index in range(3)]
        value = {**treatment,
            "mean_actionable_loss": statistics.fmean(actionable),
            "maximum_every_seed_actionable_loss": max(actionable),
            "mean_ndcg_loss": statistics.fmean(ndcg),
            "maximum_every_seed_ndcg_loss": max(ndcg),
            "per_seed_actionable_losses": actionable,
            "per_seed_ndcg_losses": ndcg}
        value["passes_gates"] = bool(
            value["mean_actionable_loss"] <=
                gates["maximum_mean_actionable_loss"] and
            value["maximum_every_seed_actionable_loss"] <=
                gates["maximum_every_seed_actionable_loss"] and
            value["mean_ndcg_loss"] <= gates["maximum_mean_ndcg_loss"] and
            value["maximum_every_seed_ndcg_loss"] <=
                gates["maximum_every_seed_ndcg_loss"])
        result.append(value)
    return result


def evaluate_partition(name: str, requests: list[dict[str, Any]],
                       protocol_path: Path, report_root: Path,
                       treatments: list[dict[str, Any]],
                       contract: dict[str, Any], materialization: dict[str, Any],
                       layout: dict[str, Any], data: dict[str, Any],
                       checkpoint_root: Path,
                       native_benchmark: Path) -> tuple[list[dict[str, Any]],
                                                        list[dict[str, Any]]]:
    positions = [int(row["native_query"]) for row in requests]
    oracle, _ = scale.exact_oracle(data, positions, 10)
    discounts = 1.0 / np.log2(np.arange(10, dtype=np.float64) + 2.0)
    materialized = {int(row["seed"]): row
                    for row in materialization["seeds"]}
    adapter = {"evaluation": {"candidate_fraction_budgets": [.003, .004, .005]},
               "cascade": {"hamming_limit": 768, "adc_limit": 64,
                           "result_k": 10}}
    output_rows = []
    validations = []
    require(sorted(materialized) == [2026082701, 2026082702, 2026082703],
            "actual-R4 representative route seed matrix differs")
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    for seed in sorted(materialized):
        report_path = report_root / f"{seed}-int8-w1.json"
        row_identity = {"schema_version": 1, "partition": name,
            "seed": seed, "report_sha256": sha256(report_path),
            "treatments": [row["id"] for row in treatments],
            "source_sha256": sha256(Path(__file__)),
            "native_benchmark_sha256": sha256(native_benchmark)}
        row_checkpoint = checkpoint_root / f"{name}-{seed}-rows.json"
        if row_checkpoint.is_file():
            cached = json.loads(row_checkpoint.read_text(encoding="utf-8"))
            if cached.get("identity") == row_identity:
                output_rows.extend(cached["rows"])
                validations.extend(cached["validations"])
                continue
        report, coarse_rows, scalar_features = read_snapshot(report_path,
                                                               requests)
        seed_layout = layout_seed(layout, seed)
        layout_root = Path(layout["root"]) / f"seed-{seed}"
        mappings = {row["role"]: row for row in seed_layout["mappings"]}
        occupied = np.asarray(read_descriptor(layout_root,
                              mappings["occupied_addresses"]), dtype=np.uint32)
        address_offsets = np.asarray(read_descriptor(
            layout_root, mappings["address_offsets"]), dtype=np.uint32)
        address_counts = np.asarray(read_descriptor(
            layout_root, mappings["address_counts"]), dtype=np.uint32)
        physical_to_document = np.asarray(read_descriptor(
            layout_root, mappings["physical_to_document"]), dtype=np.int32)
        addresses = np.empty(1_000_000, dtype=np.uint32)
        for row, address in enumerate(occupied):
            first = int(address_offsets[row])
            count = int(address_counts[row])
            addresses[physical_to_document[first:first + count]] = address
        index = scale.build_index(addresses, 16)
        rep_row = materialized[seed]
        rep_root = Path(materialization["root"]) / f"seed-{seed}"
        rep_mappings = {row["role"]: row for row in rep_row["mappings"]}
        rep_offsets = np.asarray(read_descriptor(
            rep_root, rep_mappings["address_offsets"]), dtype=np.uint32)
        rep_counts = np.asarray(read_descriptor(
            rep_root, rep_mappings["address_counts"]), dtype=np.uint8)
        fp32 = next(row for row in rep_row["representations"]
                    if row["id"] == "fp32")
        fp32_path = rep_root / fp32["file"]
        require(fp32_path.is_file() and sha256(fp32_path) == fp32["sha256"],
                "actual-R4 representative FP32 source differs")
        vectors = np.memmap(fp32_path, mode="r", dtype="<f4",
                            shape=(rep_row["representative_count"], DIMENSIONS))
        queries = np.asarray(data["queries"][positions], dtype=np.float32)
        maxima_path = checkpoint_root / f"{name}-{seed}-maxima.npz"
        maxima_meta = maxima_path.with_suffix(".json")
        maxima_identity = {"schema_version": 2, "partition": name,
            "seed": seed, "report_sha256": sha256(report_path),
            "algorithm": MAXIMA_ALGORITHM, "treatments": treatments,
            "query_vectors_sha256": array_sha256(queries),
            "representative_fp32_sha256": fp32["sha256"],
            "representative_offsets_sha256":
                rep_mappings["address_offsets"]["sha256"],
            "representative_counts_sha256":
                rep_mappings["address_counts"]["sha256"]}
        legacy_maxima_identity = {"schema_version": 1, "partition": name,
            "seed": seed, "report_sha256": sha256(report_path),
            "treatments": [row["id"] for row in treatments],
            "source_sha256": LEGACY_MAXIMA_SOURCE_SHA256}
        maxima_metadata = (json.loads(maxima_meta.read_text(encoding="utf-8"))
                           if maxima_meta.is_file() else None)
        if (maxima_path.is_file() and
                maxima_metadata in (maxima_identity, legacy_maxima_identity)):
            with np.load(maxima_path) as cached:
                require(set(cached.files) == {row["id"] for row in treatments},
                        "actual-R4 representative maxima checkpoint differs")
                maximums = {key: np.asarray(cached[key], dtype=np.float32)
                            for key in cached.files}
            require(all(value.shape == (len(queries), ADDRESSES) and
                        np.all(np.isfinite(value))
                        for value in maximums.values()),
                    "actual-R4 representative maxima checkpoint shape differs")
            if maxima_metadata == legacy_maxima_identity:
                maxima_meta.write_bytes(canonical(maxima_identity))
        else:
            maximums = maxima_for_treatments(queries, coarse_rows, vectors,
                                             rep_offsets, rep_counts, treatments)
            np.savez(maxima_path, **maximums)
            maxima_meta.write_bytes(canonical(maxima_identity))
        shortlists = occupied[np.asarray(coarse_rows, dtype=np.int64)]
        native_scores = native_score_matrix(native_benchmark, protocol_path,
            seed, coarse_rows, scalar_features, maximums, treatments, name,
            checkpoint_root)
        seed_rows: dict[str, dict[str, Any]] = {}
        for treatment_index, treatment in enumerate(treatments):
            scores = np.asarray(native_scores[treatment_index], dtype=np.float32)
            orders = legacy.order_rows(scores, shortlists)
            value = coverage.treatment_rows(treatment["id"], orders,
                shortlists, addresses, index, data, positions, oracle,
                discounts, adapter)
            row = {"partition": name, "dataset": "de-1m", "seed": seed,
                   **value}
            output_rows.append(row)
            seed_rows[treatment["id"]] = row
        control = seed_rows.get("int8_uniform")
        if control is not None:
            control_index = next(index for index, treatment in
                                 enumerate(treatments)
                                 if treatment["id"] == "int8_uniform")
            for query_index, physical in enumerate(report["samples"][0]["queries"]):
                budget = next(value for value in
                              control["queries"][query_index]["budgets"]
                              if value["candidate_fraction_budget"] == .005)
                boundary = budget["last_feasible"]
                score_matches = (array_sha256(
                    native_scores[control_index, query_index]) ==
                    physical["score_sha256"])
                boundary_matches = (
                    boundary["candidate_count"] == physical["candidate_count"] and
                    boundary["selected_address_sha256"] ==
                    physical["selected_address_sha256"])
                validations.append({"partition": name, "seed": seed,
                    "request": physical["request"],
                    "score_matches_native": score_matches,
                    "boundary_matches_native": boundary_matches,
                    "matches_native": score_matches and boundary_matches})
        seed_validations = [row for row in validations if row["seed"] == seed]
        row_checkpoint.write_bytes(canonical({"identity": row_identity,
            "rows": list(seed_rows.values()), "validations": seed_validations}))
        del addresses, index, vectors, maximums, queries, native_scores
        gc.collect()
    return output_rows, validations


def selected_per_width(values: list[dict[str, Any]],
                       bits: list[int]) -> dict[str, str]:
    result = {}
    for width in bits:
        rows = [row for row in values if row.get("bits") == width]
        result[str(width)] = min(rows, key=lambda row: (
            row["mean_ndcg_loss"], row["mean_actionable_loss"], row["id"]))["id"]
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    all_treatments = planner.treatments(contract)
    layout_path = args.layout_root / "manifest.json"
    rep_path = args.representative_root / "manifest.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    materialization = json.loads(rep_path.read_text(encoding="utf-8"))
    layout["root"] = str(args.layout_root)
    materialization["root"] = str(args.representative_root)
    require(layout.get("family") == "neuroute_r4_layout_materialization" and
            materialization.get("family") ==
                "neuroute_r4_representative_codec_materialization",
            "actual-R4 representative parent materialization differs")
    config_protocol = json.loads(args.configuration_protocol.read_text(
        encoding="utf-8"))
    internal_protocol = json.loads(args.internal_protocol.read_text(
        encoding="utf-8"))
    config_requests = config_protocol["requests"]
    parent = json.loads(Path(internal_protocol["routing_kernel_protocol"])
                        .read_text(encoding="utf-8"))
    while "requests" not in parent:
        parent = json.loads(Path(parent["parent_protocol"]).read_text(
            encoding="utf-8"))
    internal_requests = parent["requests"]
    while "native_input_manifest" not in parent:
        parent = json.loads(Path(parent["parent_protocol"]).read_text(
            encoding="utf-8"))
    e5_root = Path(parent["evaluation_document_ids"]).parent
    input_root = Path(parent["native_input_manifest"]).parent
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, e5_root, input_root)
    config_rows, config_validation = evaluate_partition(
        "configuration", config_requests, args.configuration_protocol,
        args.configuration_report_root,
        all_treatments, contract, materialization, layout, data,
        args.checkpoint_root, args.native_benchmark)
    require(all(row["matches_native"] for row in config_validation) and
            len(config_validation) == 228,
            "actual-R4 representative native INT8 configuration replay differs")
    config_quality = quality(config_rows, all_treatments,
                             contract["representative_gates"])
    per_width = selected_per_width(config_quality,
                                   contract["scalar_grid"]["integer_bits"])
    passing = [row for row in config_quality
               if row["id"] != "fp32" and row["passes_gates"]]
    require(passing, "actual-R4 representative configuration has no compact codec")
    candidate = min(passing, key=lambda row: (
        row["record_bytes"], row["mean_ndcg_loss"], row["id"]))
    internal_ids = {"fp32", "fp16", candidate["id"], *per_width.values(),
                    *(f"int{bits}_uniform"
                      for bits in contract["scalar_grid"]["integer_bits"])}
    internal_treatments = [row for row in all_treatments
                           if row["id"] in internal_ids]
    internal_rows, internal_validation = evaluate_partition(
        "internal_locked_replay", internal_requests, args.internal_protocol,
        args.internal_report_root, internal_treatments, contract,
        materialization, layout, data, args.checkpoint_root,
        args.native_benchmark)
    require(all(row["matches_native"] for row in internal_validation) and
            len(internal_validation) == 228,
            "actual-R4 representative native INT8 internal replay differs")
    internal_quality = quality(internal_rows, internal_treatments,
                               contract["representative_gates"])
    selected = next(row for row in internal_quality
                    if row["id"] == candidate["id"])
    result = {"schema_version": 1,
        "family": "neuroute_actual_r4_representative_codec_frontier_result",
        "contract_sha256": sha256(args.contract),
        "inputs": {"configuration_protocol_sha256":
            sha256(args.configuration_protocol),
            "internal_protocol_sha256": sha256(args.internal_protocol),
            "native_benchmark_sha256": sha256(args.native_benchmark),
            "layout_manifest_sha256": sha256(layout_path),
            "representative_manifest_sha256": sha256(rep_path)},
        "configuration": {"quality": config_quality,
            "selected_per_width": per_width,
            "selected_stage_candidate": candidate["id"],
            "native_int8_replay_rows": len(config_validation)},
        "internal_locked_replay": {"quality": internal_quality,
            "native_int8_replay_rows": len(internal_validation)},
        "selected_candidate": selected,
        "decision": {"candidate_for_physical_materialization": candidate["id"],
            "physical_materialization_licensed": selected["passes_gates"],
            "production_licensed": False,
            "reason": "internal_partition_was_previously_opened"},
        "configuration_rows": config_rows,
        "internal_rows": internal_rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-actual-r4-codec-frontier.example.json")
    rows = planner.treatments(contract)
    groups = treatment_groups(rows)
    require(len(groups) == 7 and all(len(value) == 8
                                     for value in groups.values()),
            "actual-R4 representative treatment grouping differs")
    values = np.asarray([[-1.0, -.25, 0.0, .25, 1.0]], dtype=np.float32)
    transformed = forward_compand(values, "power", .5)
    restored = inverse_compand(transformed, "power", .5)
    require(np.allclose(values, restored),
            "actual-R4 representative compander round trip differs")
    print("NeuRoute actual-R4 representative codec frontier self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("configuration-protocol", "internal-protocol",
                 "configuration-report-root", "internal-report-root",
                 "layout-root", "representative-root", "checkpoint-root",
                 "native-benchmark", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [name for name in vars(args)
                    if name not in {"self_test", "contract"}]
        if any(getattr(args, name) is None for name in required):
            parser.error("all actual-R4 representative paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            json.JSONDecodeError) as error:
        print(f"run-neuroute-actual-r4-representative-codec-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
