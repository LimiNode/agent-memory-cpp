#!/usr/bin/env python3
"""Explain component, dot, argmax, and boundary effects of R4 INT5."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
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


planner = load("neuroute_r4_int5_anatomy_runner_planner",
               "plan-neuroute-r4-int5-quantization-anatomy.py")
codec_runner = load("neuroute_r4_int5_anatomy_codec_runner",
                    "run-neuroute-r4-representative-codec.py")


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


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {
        "nonlinear_result_sha256": sha256(args.nonlinear_result),
        "nonlinear_evidence_sha256": sha256(args.nonlinear_evidence),
        "nonlinear_materialization_sha256": sha256(
            args.nonlinear_materialization_root / "manifest.json"),
        "representative_codec_materialization_sha256": sha256(
            args.representative_codec_root / "manifest.json"),
        "layout_manifest_sha256": sha256(args.layout_root / "manifest.json"),
        "coverage_saturation_result_sha256": sha256(args.saturation_result),
        "layout_stress_result_sha256": sha256(args.layout_stress_result),
        "layout_stress_evidence_sha256": sha256(args.layout_stress_evidence),
        "native_executable_sha256": sha256(args.native_executable),
    }


def role(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(row for row in rows if row["role"] == name)


def representation(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(row for row in rows if row["id"] == name)


def read_array(root: Path, row: dict[str, Any], dtype: str) -> numpy.ndarray:
    path = root / row["file"]
    require(path.is_file() and sha256(path) == row["sha256"],
            f"R4 INT5 anatomy payload differs: {row['role']}")
    return numpy.fromfile(path, dtype=dtype).reshape(row["shape"])


def quantized(values: numpy.ndarray, codec: str
             ) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    amplitudes = numpy.max(numpy.abs(values), axis=1).astype(numpy.float32)
    amplitudes[amplitudes == 0] = 1.0
    normalized = numpy.asarray(values / amplitudes[:, None], dtype=numpy.float32)
    if codec == "int5_uniform":
        transformed = normalized
    else:
        require(codec == "int5_power_050",
                "R4 INT5 anatomy codec differs")
        transformed = numpy.copysign(numpy.sqrt(numpy.abs(normalized)),
                                     normalized)
    signed = numpy.clip(numpy.rint(transformed * 15.0), -15, 15).astype(
        numpy.int16)
    if codec == "int5_uniform":
        reconstructed_normalized = signed.astype(numpy.float32) / 15.0
    else:
        current = signed.astype(numpy.float32) / 15.0
        reconstructed_normalized = numpy.copysign(current * current, current)
    reconstructed = numpy.asarray(
        reconstructed_normalized * amplitudes[:, None], dtype=numpy.float32)
    return signed, reconstructed, numpy.abs(normalized)


def histogram_quantile(histogram: numpy.ndarray, quantile: float) -> float:
    target = quantile * float(numpy.sum(histogram, dtype=numpy.uint64))
    index = int(numpy.searchsorted(numpy.cumsum(
        histogram, dtype=numpy.uint64), target, side="left"))
    return float(min(index + 1, len(histogram))) / float(len(histogram))


def entropy(counts: numpy.ndarray) -> dict[str, Any]:
    total = float(numpy.sum(counts, dtype=numpy.uint64))
    probabilities = counts[counts > 0].astype(numpy.float64) / total
    bits = float(-numpy.sum(probabilities * numpy.log2(probabilities)))
    return {"bits": bits, "normalized_to_five_bits": bits / 5.0,
            "active_levels": int(numpy.count_nonzero(counts)),
            "effective_levels": float(2.0 ** bits)}


def component_anatomy(contract: dict[str, Any],
                      representative_manifest: dict[str, Any],
                      args: argparse.Namespace) -> dict[str, Any]:
    bins = contract["component_diagnostics"][
        "normalized_magnitude_histogram_bins"]
    histogram = numpy.zeros(bins, dtype=numpy.uint64)
    occupancy = {row["id"]: numpy.zeros(31, dtype=numpy.uint64)
                 for row in contract["codecs"]}
    vector_count = 0
    component_count = 0
    for seed_row in representative_manifest["seeds"]:
        root = args.representative_codec_root / f"seed-{seed_row['seed']}"
        fp32 = representation(seed_row["representations"], "fp32")
        vectors = numpy.memmap(root / fp32["file"], mode="r", dtype="<f4",
            shape=(int(seed_row["representative_count"]), 384))
        vector_count += len(vectors)
        component_count += len(vectors) * 384
        for start in range(0, len(vectors), args.chunk_rows):
            current = numpy.asarray(vectors[start:start + args.chunk_rows],
                                    dtype=numpy.float32)
            for codec in occupancy:
                signed, _, magnitude = quantized(current, codec)
                occupancy[codec] += numpy.bincount(
                    (signed.reshape(-1) + 15).astype(numpy.int64),
                    minlength=31).astype(numpy.uint64)
                if codec == "int5_uniform":
                    indices = numpy.minimum(
                        (magnitude.reshape(-1) * bins).astype(numpy.int64),
                        bins - 1)
                    histogram += numpy.bincount(indices, minlength=bins).astype(
                        numpy.uint64)
    require(int(numpy.sum(histogram, dtype=numpy.uint64)) == component_count,
            "R4 INT5 anatomy component histogram count differs")
    reported = {str(value): histogram_quantile(histogram, float(value))
                for value in contract["component_diagnostics"][
                    "reported_quantiles"]}
    decile_upper = [histogram_quantile(histogram, value / 10.0)
                    for value in range(1, 10)] + [1.0]
    per_codec = {row["id"]: {
        "absolute_error_sum": numpy.zeros(10, dtype=numpy.float64),
        "squared_error_sum": numpy.zeros(10, dtype=numpy.float64),
        "source_squared_sum": numpy.zeros(10, dtype=numpy.float64),
        "components": numpy.zeros(10, dtype=numpy.uint64)}
        for row in contract["codecs"]}
    for seed_row in representative_manifest["seeds"]:
        root = args.representative_codec_root / f"seed-{seed_row['seed']}"
        fp32 = representation(seed_row["representations"], "fp32")
        vectors = numpy.memmap(root / fp32["file"], mode="r", dtype="<f4",
            shape=(int(seed_row["representative_count"]), 384))
        for start in range(0, len(vectors), args.chunk_rows):
            current = numpy.asarray(vectors[start:start + args.chunk_rows],
                                    dtype=numpy.float32)
            for codec, values in per_codec.items():
                _, reconstructed, magnitude = quantized(current, codec)
                deciles = numpy.searchsorted(numpy.asarray(decile_upper),
                    magnitude.reshape(-1), side="left")
                deciles = numpy.minimum(deciles, 9)
                error = numpy.asarray(current - reconstructed,
                                      dtype=numpy.float32).reshape(-1)
                source = current.reshape(-1)
                values["components"] += numpy.bincount(
                    deciles, minlength=10).astype(numpy.uint64)
                values["absolute_error_sum"] += numpy.bincount(deciles,
                    weights=numpy.abs(error).astype(numpy.float64),
                    minlength=10)
                values["squared_error_sum"] += numpy.bincount(deciles,
                    weights=(error.astype(numpy.float64) ** 2), minlength=10)
                values["source_squared_sum"] += numpy.bincount(deciles,
                    weights=(source.astype(numpy.float64) ** 2), minlength=10)
    codec_rows = []
    thresholds = contract["component_diagnostics"][
        "central_absolute_code_thresholds"]
    for codec, values in per_codec.items():
        counts = occupancy[codec]
        total_squared = float(numpy.sum(values["source_squared_sum"]))
        deciles = []
        for index in range(10):
            count = int(values["components"][index])
            deciles.append({"decile": index + 1,
                "maximum_normalized_magnitude": decile_upper[index],
                "components": count,
                "mean_absolute_error": float(
                    values["absolute_error_sum"][index] / count),
                "root_mean_squared_error": float(math.sqrt(
                    values["squared_error_sum"][index] / count)),
                "source_squared_norm_fraction": float(
                    values["source_squared_sum"][index] / total_squared)})
        codec_rows.append({"codec": codec,
            "level_counts": counts.tolist(), "entropy": entropy(counts),
            "central_code_fraction": {str(threshold): float(numpy.sum(
                counts[15 - threshold:16 + threshold],
                dtype=numpy.uint64) / numpy.sum(counts, dtype=numpy.uint64))
                for threshold in thresholds},
            "mean_absolute_error": float(numpy.sum(
                values["absolute_error_sum"]) / component_count),
            "root_mean_squared_error": float(math.sqrt(numpy.sum(
                values["squared_error_sum"]) / component_count)),
            "magnitude_deciles": deciles})
    return {"representative_vectors": vector_count,
        "components": component_count,
        "histogram_bins": bins,
        "histogram_resolution": 1.0 / bins,
        "normalized_magnitude_quantile_upper_bounds": reported,
        "equal_frequency_decile_upper_bounds": decile_upper,
        "codecs": codec_rows}


def validate_component_cache(contract: dict[str, Any],
                             representative_manifest: dict[str, Any],
                             component: dict[str, Any]) -> None:
    representatives = sum(int(row["representative_count"])
                          for row in representative_manifest["seeds"])
    require(component["representative_vectors"] == representatives and
            component["components"] == representatives * 384,
            "R4 INT5 anatomy component cache cardinality differs")
    require(component["histogram_bins"] == contract[
                "component_diagnostics"][
                    "normalized_magnitude_histogram_bins"] and
            [row["codec"] for row in component["codecs"]] ==
                [row["id"] for row in contract["codecs"]],
            "R4 INT5 anatomy component cache contract differs")


def decode_stores(contract: dict[str, Any],
                  representative_manifest: dict[str, Any],
                  nonlinear_manifest: dict[str, Any],
                  args: argparse.Namespace) -> dict[int, dict[str, Path]]:
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    nonlinear_by_seed = {int(row["seed"]): row
                         for row in nonlinear_manifest["seeds"]}
    result = {}
    for seed_row in representative_manifest["seeds"]:
        seed = int(seed_row["seed"])
        rows = int(seed_row["representative_count"])
        parent_root = args.representative_codec_root / f"seed-{seed}"
        nonlinear_root = args.nonlinear_materialization_root / f"seed-{seed}"
        uniform = representation(seed_row["representations"], "int5")
        nonlinear = representation(nonlinear_by_seed[seed]["representations"],
                                   "int5_power_050")
        outputs = {
            "int5_uniform": args.scratch_root / f"{seed}-int5-uniform.f32le",
            "int5_power_050": args.scratch_root / f"{seed}-int5-power-050.f32le"}
        commands = {
            "int5_uniform": [str(args.native_executable), "--unpack", "5",
                str(parent_root / uniform["file"]), str(rows),
                str(outputs["int5_uniform"])],
            "int5_power_050": [str(args.native_executable),
                "--unpack-nonlinear", "5", "power", "0.5",
                str(nonlinear_root / nonlinear["file"]), str(rows),
                str(outputs["int5_power_050"])]}
        for codec, output in outputs.items():
            if not args.reuse_scratch or not output.is_file():
                completed = subprocess.run(commands[codec], check=False,
                    capture_output=True, text=True)
                require(completed.returncode == 0,
                    f"R4 INT5 anatomy decode failed: {completed.stderr}")
            require(output.stat().st_size == rows * 384 * 4,
                    "R4 INT5 anatomy decoded store size differs")
        result[seed] = outputs
    return result


def conditioned(margins: numpy.ndarray, agreement: numpy.ndarray,
                boundaries: list[float]) -> list[dict[str, Any]]:
    result = []
    lower = -numpy.inf
    for upper in [*boundaries, numpy.inf]:
        selected = (margins > lower) & (margins <= upper)
        result.append({"minimum_exclusive": None if not numpy.isfinite(lower)
                       else lower,
            "maximum_inclusive": None if not numpy.isfinite(upper) else upper,
            "pairs": int(numpy.count_nonzero(selected)),
            "agreement": float(numpy.mean(agreement[selected]))
                if numpy.any(selected) else None})
        lower = upper
    return result


def conditioned_mean(margins: numpy.ndarray, values: numpy.ndarray,
                     boundaries: list[float]) -> list[dict[str, Any]]:
    result = []
    lower = -numpy.inf
    for upper in [*boundaries, numpy.inf]:
        selected = (margins > lower) & (margins <= upper)
        result.append({"minimum_exclusive": None if not numpy.isfinite(lower)
                       else lower,
            "maximum_inclusive": None if not numpy.isfinite(upper) else upper,
            "pairs": int(numpy.count_nonzero(selected)),
            "mean": float(numpy.mean(values[selected]))
                if numpy.any(selected) else None})
        lower = upper
    return result


def ordered(scores: numpy.ndarray, addresses: numpy.ndarray) -> numpy.ndarray:
    return numpy.lexsort((addresses.astype(numpy.int64),
                          -scores.astype(numpy.float64)))


def accepted(order: numpy.ndarray, addresses: numpy.ndarray,
             posting_counts: numpy.ndarray, limit: int) -> tuple[set[int], int]:
    result: set[int] = set()
    total = 0
    accepted_count = 0
    for local in order:
        count = int(posting_counts[local])
        if total + count > limit:
            break
        total += count
        accepted_count += 1
        result.add(int(addresses[local]))
    return result, accepted_count


def routing_anatomy(contract: dict[str, Any],
                    representative_manifest: dict[str, Any],
                    layout_manifest: dict[str, Any],
                    saturation: dict[str, Any],
                    decoded: dict[int, dict[str, Path]],
                    component: dict[str, Any],
                    args: argparse.Namespace) -> dict[str, Any]:
    representative_by_seed = {int(row["seed"]): row
                              for row in representative_manifest["seeds"]}
    layout_by_seed = {int(row["seed"]): row
                      for row in layout_manifest["seeds"]}
    model_by_seed = {int(row["seed"]): row for row in saturation[
        "frozen_k32_parent_models"]}
    codecs = [row["id"] for row in contract["codecs"]]
    accumulator = {codec: {"margins": [], "argmax_agreement": [],
        "top128_margins": [], "top128_agreement": [], "top128_overlap": [],
        "boundary_margins": [], "boundary_agreement": [],
        "accepted_jaccard": [], "score_error": [],
        "query_dot_error_deciles": numpy.zeros(10, dtype=numpy.float64)}
        for codec in codecs}
    query_true_contribution = numpy.zeros(10, dtype=numpy.float64)
    query_component_count = numpy.zeros(10, dtype=numpy.uint64)
    decile_upper = numpy.asarray(
        component["equal_frequency_decile_upper_bounds"], dtype=numpy.float32)
    seed_rows = []
    for seed in contract["route"]["seeds"]:
        representative_row = representative_by_seed[int(seed)]
        layout_row = layout_by_seed[int(seed)]
        representative_root = args.representative_codec_root / f"seed-{seed}"
        layout_root = args.layout_root / f"seed-{seed}"
        rep_mappings = {row["role"]: row
                        for row in representative_row["mappings"]}
        layout_mappings = {row["role"]: row
                           for row in layout_row["mappings"]}
        occupied = read_array(representative_root,
            rep_mappings["occupied_addresses"], "<u4")
        offsets = read_array(representative_root,
            rep_mappings["address_offsets"], "<u4")
        counts = read_array(representative_root,
            rep_mappings["address_counts"], "u1")
        documents = read_array(representative_root,
            rep_mappings["representative_document_positions"], "<i4")
        queries_all = read_array(layout_root,
            layout_mappings["query_vectors"], "<f4")
        shortlist_rows_all = read_array(layout_root,
            layout_mappings["shortlist_rows"], "<u4")
        scalar_all = read_array(layout_root,
            layout_mappings["scalar_features"], "<f4")
        posting_counts = read_array(layout_root,
            layout_mappings["address_counts"], "<u4")
        begin = contract["route"]["layout_request_offset"]
        end = begin + contract["route"]["queries_per_seed"]
        queries = numpy.asarray(queries_all.reshape(-1, 384)[begin:end],
                                dtype=numpy.float32)
        shortlist_rows = numpy.asarray(shortlist_rows_all.reshape(
            -1, 1024)[begin:end], dtype=numpy.uint32)
        shortlists = occupied[shortlist_rows]
        scalar_features = numpy.asarray(scalar_all.reshape(
            -1, 1024, 22)[begin:end], dtype=numpy.float32)
        rows = int(representative_row["representative_count"])
        fp32_row = representation(representative_row["representations"], "fp32")
        vectors = {"fp32": numpy.memmap(
            representative_root / fp32_row["file"], mode="r", dtype="<f4",
            shape=(rows, 384))}
        for codec in codecs:
            vectors[codec] = numpy.memmap(decoded[int(seed)][codec], mode="r",
                                          dtype="<f4", shape=(rows, 384))
        maximums = {}
        top2 = {}
        winners = {}
        for name, values in vectors.items():
            maximums[name], top2[name], winners[name] = codec_runner.maximums(
                queries, shortlists, values, occupied, offsets, counts, documents)
        model = model_by_seed[int(seed)]
        model_path = args.model_root / model["file"]
        require(model_path.is_file() and sha256(model_path) == model["sha256"],
                "R4 INT5 anatomy frozen model differs")
        score_rows = {name: numpy.asarray(codec_runner.model_order(
            queries, scalar_features, maximum, model_path), dtype=numpy.float32)
            for name, maximum in maximums.items()}
        physical_by_document = numpy.full(1000000, -1, dtype=numpy.int32)
        physical_by_document[documents] = numpy.arange(
            len(documents), dtype=numpy.int32)
        local_seed_rows = []
        for query_index in range(len(queries)):
            addresses = shortlists[query_index]
            local_postings = posting_counts[shortlist_rows[query_index]]
            fp_order = ordered(score_rows["fp32"][query_index], addresses)
            fp_accepted, fp_accepted_count = accepted(fp_order, addresses,
                local_postings, contract["route"]["candidate_limit"])
            require(0 < fp_accepted_count < len(fp_order),
                    "R4 INT5 anatomy FP32 boundary differs")
            fp_boundary_margin = float(
                score_rows["fp32"][query_index, fp_order[fp_accepted_count - 1]] -
                score_rows["fp32"][query_index, fp_order[fp_accepted_count]])
            top_count = contract["routing_diagnostics"]["top_address_count"]
            fp_top = set(int(value) for value in addresses[fp_order[:top_count]])
            fp_top_margin = float(
                score_rows["fp32"][query_index, fp_order[top_count - 1]] -
                score_rows["fp32"][query_index, fp_order[top_count]])
            fp_winner_physical = physical_by_document[
                winners["fp32"][query_index]]
            require(numpy.all(fp_winner_physical >= 0),
                    "R4 INT5 anatomy FP32 winner mapping differs")
            winner_vectors = numpy.asarray(
                vectors["fp32"][fp_winner_physical], dtype=numpy.float32)
            amplitude = numpy.max(numpy.abs(winner_vectors), axis=1)
            amplitude[amplitude == 0] = 1.0
            magnitude = numpy.abs(winner_vectors / amplitude[:, None])
            deciles = numpy.minimum(numpy.searchsorted(decile_upper,
                magnitude.reshape(-1), side="left"), 9)
            true_contribution = numpy.abs(
                winner_vectors * queries[query_index][None, :]).reshape(-1)
            query_true_contribution += numpy.bincount(deciles,
                weights=true_contribution.astype(numpy.float64), minlength=10)
            query_component_count += numpy.bincount(deciles, minlength=10).astype(
                numpy.uint64)
            query_row = {"query": query_index,
                "fp32_top128_margin": fp_top_margin,
                "fp32_candidate_boundary_margin": fp_boundary_margin,
                "codecs": []}
            for codec in codecs:
                margin = maximums["fp32"][query_index] - top2["fp32"][query_index]
                agreement = winners[codec][query_index] == winners["fp32"][query_index]
                accumulator[codec]["margins"].append(margin)
                accumulator[codec]["argmax_agreement"].append(agreement)
                current_order = ordered(score_rows[codec][query_index], addresses)
                current_top = set(int(value) for value in
                                  addresses[current_order[:top_count]])
                current_accepted, _ = accepted(current_order, addresses,
                    local_postings, contract["route"]["candidate_limit"])
                top_agreement = current_top == fp_top
                top_overlap = len(current_top & fp_top) / float(top_count)
                boundary_agreement = current_accepted == fp_accepted
                accumulator[codec]["top128_margins"].append(fp_top_margin)
                accumulator[codec]["top128_agreement"].append(top_agreement)
                accumulator[codec]["top128_overlap"].append(top_overlap)
                accumulator[codec]["boundary_margins"].append(fp_boundary_margin)
                accumulator[codec]["boundary_agreement"].append(
                    boundary_agreement)
                union = len(current_accepted | fp_accepted)
                jaccard = len(current_accepted & fp_accepted) / max(union, 1)
                accumulator[codec]["accepted_jaccard"].append(jaccard)
                accumulator[codec]["score_error"].append(numpy.abs(
                    score_rows[codec][query_index] -
                    score_rows["fp32"][query_index]))
                _, reconstructed, _ = quantized(winner_vectors, codec)
                dot_error = numpy.abs(queries[query_index][None, :] *
                                      (winner_vectors - reconstructed)).reshape(-1)
                accumulator[codec]["query_dot_error_deciles"] += numpy.bincount(
                    deciles, weights=dot_error.astype(numpy.float64), minlength=10)
                query_row["codecs"].append({"codec": codec,
                    "top128_set_identity": top_agreement,
                    "top128_set_overlap": top_overlap,
                    "accepted_set_identity": boundary_agreement,
                    "accepted_set_jaccard": jaccard})
            local_seed_rows.append(query_row)
        seed_rows.append({"seed": seed, "queries": local_seed_rows})
    total_true = float(numpy.sum(query_true_contribution))
    component_rows = [{"decile": index + 1,
        "true_absolute_dot_contribution_fraction": float(
            query_true_contribution[index] / total_true),
        "components": int(query_component_count[index])}
        for index in range(10)]
    boundaries = contract["routing_diagnostics"]["fp32_margin_bins"]
    stable = contract["routing_diagnostics"]["stable_margin_threshold"]
    codec_rows = []
    component_by_codec = {row["codec"]: row for row in component["codecs"]}
    for codec, values in accumulator.items():
        margins = numpy.concatenate(values["margins"])
        argmax = numpy.concatenate(values["argmax_agreement"])
        top_margins = numpy.asarray(values["top128_margins"],
                                    dtype=numpy.float64)
        top_agreement = numpy.asarray(values["top128_agreement"], dtype=bool)
        top_overlap = numpy.asarray(values["top128_overlap"],
                                    dtype=numpy.float64)
        boundary_margins = numpy.asarray(values["boundary_margins"],
                                         dtype=numpy.float64)
        boundary_agreement = numpy.asarray(values["boundary_agreement"],
                                           dtype=bool)
        score_error = numpy.concatenate(values["score_error"]).astype(
            numpy.float64)
        query_error = values["query_dot_error_deciles"]
        total_error = float(numpy.sum(query_error))
        query_deciles = [{"decile": index + 1,
            "absolute_dot_error_fraction": float(query_error[index] / total_error),
            "absolute_dot_error_sum": float(query_error[index])}
            for index in range(10)]
        stable_argmax = margins > stable
        stable_boundary = boundary_margins > stable
        accepted_jaccard = numpy.asarray(values["accepted_jaccard"],
                                         dtype=numpy.float64)
        material_boundary = accepted_jaccard < contract[
            "learned_codebook_license"]["maximum_material_boundary_jaccard"]
        codec_rows.append({"codec": codec,
            "component_entropy": component_by_codec[codec]["entropy"],
            "representative_argmax_agreement": float(numpy.mean(argmax)),
            "argmax_agreement_by_fp32_margin":
                conditioned(margins, argmax, boundaries),
            "stable_margin_argmax_disagreement": float(
                numpy.mean(~argmax[stable_argmax])) if numpy.any(stable_argmax)
                else 0.0,
            "address_score_absolute_error": {
                "mean": float(numpy.mean(score_error)),
                "p50": float(numpy.quantile(score_error, .5)),
                "p95": float(numpy.quantile(score_error, .95)),
                "p99": float(numpy.quantile(score_error, .99)),
                "maximum": float(numpy.max(score_error))},
            "top128_set_identity_fraction": float(numpy.mean(top_agreement)),
            "top128_set_mean_overlap": float(numpy.mean(top_overlap)),
            "top128_identity_by_fp32_boundary_margin":
                conditioned(top_margins, top_agreement, boundaries),
            "top128_overlap_by_fp32_boundary_margin":
                conditioned_mean(top_margins, top_overlap, boundaries),
            "accepted_set_identity_fraction": float(
                numpy.mean(boundary_agreement)),
            "accepted_set_mean_jaccard": float(numpy.mean(
                values["accepted_jaccard"])),
            "accepted_identity_by_fp32_boundary_margin":
                conditioned(boundary_margins, boundary_agreement, boundaries),
            "stable_boundary_query_count": int(numpy.count_nonzero(
                stable_boundary)),
            "stable_boundary_material_difference_fraction": float(
                numpy.mean(material_boundary[stable_boundary]))
                if numpy.any(stable_boundary) else 0.0,
            "query_weighted_error_by_magnitude_decile": query_deciles,
            "maximum_single_decile_query_error_fraction": max(
                row["absolute_dot_error_fraction"] for row in query_deciles)})
    return {"query_address_pairs":
        contract["route"]["queries_per_seed"] *
        contract["route"]["shortlist_addresses"] *
        len(contract["route"]["seeds"]),
        "query_component_true_contribution_by_magnitude_decile": component_rows,
        "codecs": codec_rows, "per_seed_query_rows": seed_rows}


def decision(contract: dict[str, Any],
             routing: dict[str, Any]) -> dict[str, Any]:
    selected = next(row for row in routing["codecs"]
                    if row["codec"] == "int5_power_050")
    gates = contract["learned_codebook_license"]
    signals = {
        "low_code_entropy": selected["component_entropy"][
            "normalized_to_five_bits"] <=
            gates["maximum_normalized_code_entropy"],
        "single_decile_query_error_concentration":
            selected["maximum_single_decile_query_error_fraction"] >=
            gates["minimum_single_decile_query_error_fraction"],
        "stable_margin_argmax_error":
            selected["stable_margin_argmax_disagreement"] >=
            gates["minimum_stable_margin_argmax_disagreement"],
        "stable_candidate_boundary_error":
            selected["stable_boundary_query_count"] >=
            gates["minimum_stable_boundary_query_count"] and
            selected["stable_boundary_material_difference_fraction"] >=
            gates["minimum_stable_boundary_query_difference_fraction"],
    }
    return {"license_signals": signals,
        "learned_codebook_frontier_licensed": any(signals.values()),
        "power_half_remains_selected_codec": True,
        "production_selection_licensed": False}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = activation(args)
    require(actual == contract["activation"],
            "R4 INT5 anatomy activation differs")
    nonlinear_result = json.loads(args.nonlinear_result.read_text(
        encoding="utf-8"))
    nonlinear_evidence = json.loads(args.nonlinear_evidence.read_text(
        encoding="utf-8"))
    stress = json.loads(args.layout_stress_result.read_text(encoding="utf-8"))
    stress_evidence = json.loads(args.layout_stress_evidence.read_text(
        encoding="utf-8"))
    require(nonlinear_result["decision"]["selected_representation"] ==
            "int5_power_050" and
            nonlinear_evidence["selected_internal_passes_gates"] is True and
            stress["decision"]["selected_pressure_layout"] == "int5_mixed" and
            stress_evidence["passed"] is True,
            "R4 INT5 anatomy parent decision differs")
    representative_manifest = json.loads((args.representative_codec_root /
        "manifest.json").read_text(encoding="utf-8"))
    nonlinear_manifest = json.loads((args.nonlinear_materialization_root /
        "manifest.json").read_text(encoding="utf-8"))
    layout_manifest = json.loads((args.layout_root / "manifest.json").read_text(
        encoding="utf-8"))
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    component_cache = args.scratch_root / "component-distribution.json"
    if args.reuse_scratch and component_cache.is_file():
        component = json.loads(component_cache.read_text(encoding="utf-8"))
    else:
        component = component_anatomy(contract, representative_manifest, args)
        component_cache.write_bytes(canonical(component))
    validate_component_cache(contract, representative_manifest, component)
    decoded = decode_stores(contract, representative_manifest,
                            nonlinear_manifest, args)
    routing = routing_anatomy(contract, representative_manifest,
        layout_manifest, saturation, decoded, component, args)
    result = {"schema_version": 1,
        "family": "neuroute_r4_int5_quantization_anatomy_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": actual, "matrix": planner.plan(contract),
        "component_distribution": component,
        "routing_mechanism": routing,
        "decision": decision(contract, routing)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    values = numpy.asarray([[1.0, -.25, 0.0]], dtype=numpy.float32)
    signed, reconstructed, magnitude = quantized(values, "int5_power_050")
    require(signed.tolist() == [[15, -8, 0]] and
            reconstructed.shape == values.shape and
            magnitude.tolist() == [[1.0, .25, 0.0]],
            "R4 INT5 anatomy quantizer self-test differs")
    histogram = numpy.asarray([1, 2, 7], dtype=numpy.uint64)
    require(histogram_quantile(histogram, .5) == 1.0,
            "R4 INT5 anatomy histogram quantile differs")
    print("NeuRoute R4 INT5 quantization-anatomy runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-quantization-anatomy.example.json")
    for name in ("nonlinear-result", "nonlinear-evidence",
                 "nonlinear-materialization-root", "representative-codec-root",
                 "layout-root", "saturation-result", "model-root",
                 "layout-stress-result", "layout-stress-evidence",
                 "native-executable", "scratch-root", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--reuse-scratch", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "reuse_scratch", "contract", "chunk_rows"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 INT5 anatomy paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, subprocess.SubprocessError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r4-int5-quantization-anatomy: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
