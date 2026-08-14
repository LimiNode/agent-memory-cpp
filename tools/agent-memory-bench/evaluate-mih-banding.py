#!/usr/bin/env python3
"""Reference in-memory MIH/banding evaluation for held-out binary retrieval.

This harness separates a fixed-radius MIH guarantee from approximate top-K
candidate generation.  It is intentionally a NumPy/Python research reference,
not an MDBX layout or production latency benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import importlib.util
import itertools
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


def _load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
EvaluationError = shared.EvaluationError
BALANCED_BAND_OBJECTIVE = "abs-correlation-plus-entropy-balance-v1"
ENTROPY_BALANCE_WEIGHT = 0.05
VARIABLE_WIDTH_BAND_OBJECTIVE = "collision-information-balanced-variable-width-v1"


def source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_files_sha256() -> dict[str, str]:
    shared_path = Path(__file__).with_name("evaluate-projection-quantization.py")
    return {
        Path(__file__).name: source_sha256(),
        shared_path.name: hashlib.sha256(shared_path.read_bytes()).hexdigest(),
    }


def source_bundle_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def band_ranges(code_bits: int, band_count: int) -> list[tuple[int, int]]:
    if code_bits <= 0 or band_count <= 1 or band_count > code_bits:
        raise EvaluationError("MIH band count is invalid")
    base, remainder = divmod(code_bits, band_count)
    widths = [base + (1 if index < remainder else 0) for index in range(band_count)]
    if max(widths) > 16:
        raise EvaluationError("MIH reference limits a band to 16 bits")
    result: list[tuple[int, int]] = []
    offset = 0
    for width in widths:
        result.append((offset, offset + width))
        offset += width
    return result


def variable_band_ranges(code_bits: int, widths: list[int]) -> list[tuple[int, int]]:
    if (
        not widths
        or sum(widths) != code_bits
        or any(not isinstance(width, int) or width <= 0 or width > 16 for width in widths)
    ):
        raise EvaluationError("MIH variable band widths are invalid")
    result: list[tuple[int, int]] = []
    offset = 0
    for width in widths:
        result.append((offset, offset + width))
        offset += width
    return result


def parse_band_widths(value: str | None, code_bits: int, band_count: int) -> list[int]:
    if value is None:
        return [stop - start for start, stop in band_ranges(code_bits, band_count)]
    try:
        widths = [int(part) for part in value.split(",")]
    except ValueError as error:
        raise EvaluationError("MIH variable band widths are invalid") from error
    if len(widths) != band_count:
        raise EvaluationError("MIH variable band count differs from the configured count")
    variable_band_ranges(code_bits, widths)
    return widths


def calibrated_band_permutation(calibration_codes: Any, band_count: int) -> numpy.ndarray:
    """Greedily separate correlated calibration bits into equal-size bands."""
    values = numpy.asarray(calibration_codes, dtype=numpy.float64)
    if values.ndim != 2 or values.shape[1] % band_count != 0:
        raise EvaluationError("calibration band layout dimensions are invalid")
    width = values.shape[1] // band_count
    probabilities = values.mean(axis=0)
    entropy = -(numpy.where(probabilities > 0.0, probabilities * numpy.log2(probabilities), 0.0) + numpy.where(probabilities < 1.0, (1.0 - probabilities) * numpy.log2(1.0 - probabilities), 0.0))
    centered = values - probabilities
    scale = numpy.sqrt(numpy.sum(centered * centered, axis=0))
    if numpy.any(scale == 0.0) or not numpy.all(numpy.isfinite(entropy)):
        raise EvaluationError("calibration band layout statistics are invalid")
    correlation = numpy.abs((centered.T @ centered) / numpy.outer(scale, scale))
    numpy.fill_diagonal(correlation, 0.0)
    target_entropy = float(numpy.sum(entropy) / band_count)
    bands: list[list[int]] = [[] for _ in range(band_count)]
    totals = [0.0] * band_count
    for bit in sorted(range(values.shape[1]), key=lambda index: (-float(entropy[index]), index)):
        candidates = [index for index in range(band_count) if len(bands[index]) < width]
        def score(index: int) -> tuple[float, int]:
            intra = float(numpy.mean(correlation[bit, bands[index]])) if bands[index] else 0.0
            balance = abs(totals[index] + float(entropy[bit]) - target_entropy)
            return intra + ENTROPY_BALANCE_WEIGHT * balance, index
        chosen = min(candidates, key=score)
        bands[chosen].append(bit)
        totals[chosen] += float(entropy[bit])
    permutation = numpy.asarray([bit for band in bands for bit in band], dtype=numpy.intp)
    if permutation.shape != (values.shape[1],) or not numpy.array_equal(numpy.sort(permutation), numpy.arange(values.shape[1])):
        raise EvaluationError("calibration band permutation is invalid")
    return permutation


def calibrated_variable_band_permutation(calibration_codes: Any, widths: list[int]) -> numpy.ndarray:
    """Assign calibration bits to fixed variable-width keys by collision information.

    Shorter keys receive the most discriminative remaining bits. Within a key,
    the next bit minimizes the distance from equal target collision information
    and then mean absolute correlation with already assigned bits. The procedure
    uses calibration codes only and is deterministic under ties.
    """
    values = numpy.asarray(calibration_codes, dtype=numpy.float64)
    ranges = variable_band_ranges(values.shape[1] if values.ndim == 2 else 0, widths)
    if values.ndim != 2 or len(widths) <= 1:
        raise EvaluationError("MIH variable calibration layout inputs are invalid")
    probabilities = values.mean(axis=0)
    collision_probability = numpy.square(probabilities) + numpy.square(1.0 - probabilities)
    information = -numpy.log2(collision_probability)
    centered = values - probabilities
    scale = numpy.sqrt(numpy.sum(centered * centered, axis=0))
    if numpy.any(scale == 0.0) or not numpy.all(numpy.isfinite(information)) or numpy.any(information <= 0.0):
        raise EvaluationError("MIH variable calibration information is invalid")
    correlation = numpy.abs((centered.T @ centered) / numpy.outer(scale, scale))
    numpy.fill_diagonal(correlation, 0.0)
    target_information = float(numpy.sum(information) / len(widths))
    remaining = set(range(values.shape[1]))
    bands: list[list[int]] = []
    for width in widths:
        selected: list[int] = []
        for _ in range(width):
            def score(bit: int) -> tuple[float, float, int]:
                collision_balance = abs(float(numpy.sum(information[selected])) + float(information[bit]) - target_information)
                intra_correlation = float(numpy.mean(correlation[bit, selected])) if selected else 0.0
                return collision_balance, intra_correlation, bit
            chosen = min(remaining, key=score)
            selected.append(chosen)
            remaining.remove(chosen)
        bands.append(selected)
    permutation = numpy.asarray([bit for band in bands for bit in band], dtype=numpy.intp)
    if ranges[-1][1] != values.shape[1] or permutation.shape != (values.shape[1],) or not numpy.array_equal(numpy.sort(permutation), numpy.arange(values.shape[1])):
        raise EvaluationError("MIH variable calibration layout is invalid")
    return permutation


def band_layout_sha256(permutation: Any) -> str:
    values = numpy.asarray(permutation, dtype="<u4")
    if values.ndim != 1:
        raise EvaluationError("band layout digest input is invalid")
    return hashlib.sha256(values.tobytes()).hexdigest()


def fixed_random_band_permutation(code_bits: int, seed: int) -> numpy.ndarray:
    if code_bits <= 0 or seed < 0:
        raise EvaluationError("fixed random band layout arguments are invalid")
    return numpy.random.default_rng(seed).permutation(code_bits).astype(numpy.intp)


def explicit_band_permutation(path: Path, code_bits: int) -> numpy.ndarray:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("explicit MIH band permutation is invalid") from error
    if not isinstance(value, list) or len(value) != code_bits or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise EvaluationError("explicit MIH band permutation is invalid")
    permutation = numpy.asarray(value, dtype=numpy.intp)
    if not numpy.array_equal(numpy.sort(permutation), numpy.arange(code_bits)):
        raise EvaluationError("explicit MIH band permutation is invalid")
    return permutation


def explicit_band_selection_provenance(path: Path, permutation: numpy.ndarray) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError("explicit MIH band selection provenance is invalid") from error
    expected = {
        "schema_version", "family", "optimizer_report_sha256", "seed", "selected_id",
        "selected_widths", "selected_permutation_sha256",
    }
    if (
        not isinstance(value, dict) or set(value) != expected
        or value["schema_version"] != 1 or value["family"] != "mih_static_width_optimizer_selection_v1"
        or not isinstance(value["optimizer_report_sha256"], str) or len(value["optimizer_report_sha256"]) != 64
        or not isinstance(value["seed"], int) or isinstance(value["seed"], bool)
        or not isinstance(value["selected_id"], str) or not value["selected_id"]
        or not isinstance(value["selected_widths"], list) or any(not isinstance(width, int) or isinstance(width, bool) or width <= 0 for width in value["selected_widths"])
        or sum(value["selected_widths"]) != permutation.size
        or value["selected_permutation_sha256"] != band_layout_sha256(permutation)
    ):
        raise EvaluationError("explicit MIH band selection provenance is invalid")
    return value


def mean_intraband_absolute_correlation(codes: Any, bands: int | list[tuple[int, int]]) -> float:
    values = numpy.asarray(codes, dtype=numpy.float64)
    centered = values - values.mean(axis=0)
    scale = numpy.sqrt(numpy.sum(centered * centered, axis=0))
    if numpy.any(scale == 0.0):
        raise EvaluationError("band correlation statistics are invalid")
    correlation = numpy.abs((centered.T @ centered) / numpy.outer(scale, scale))
    pairs = []
    ranges = band_ranges(values.shape[1], bands) if isinstance(bands, int) else bands
    if not ranges or ranges[-1][1] != values.shape[1]:
        raise EvaluationError("MIH correlation band ranges are invalid")
    for start, stop in ranges:
        width = stop - start
        pairs.append(correlation[start:stop, start:stop][numpy.triu_indices(width, 1)])
    nonempty = [pair for pair in pairs if pair.size]
    return float(numpy.concatenate(nonempty).mean()) if nonempty else 0.0


def band_key(code: Any, start: int, stop: int) -> int:
    values = numpy.asarray(code[start:stop], dtype=numpy.uint8)
    return int(numpy.dot(values, 1 << numpy.arange(values.size, dtype=numpy.uint32)))


def probe_keys(key: int, width: int, radius: int) -> list[int]:
    if radius < 0 or radius > width:
        raise EvaluationError("MIH probe radius is invalid")
    result = [key]
    for count in range(1, radius + 1):
        for positions in itertools.combinations(range(width), count):
            probe = key
            for position in positions:
                probe ^= 1 << position
            result.append(probe)
    return result


def build_index(codes: Any, ranges: list[tuple[int, int]]) -> list[dict[int, numpy.ndarray]]:
    buckets: list[dict[int, list[int]]] = [defaultdict(list) for _ in ranges]
    for index, code in enumerate(codes):
        for band, (start, stop) in enumerate(ranges):
            buckets[band][band_key(code, start, stop)].append(index)
    return [{key: numpy.asarray(values, dtype=numpy.int32) for key, values in band.items()} for band in buckets]


def global_radius_schedule(global_radius: int, band_count: int) -> list[int]:
    if global_radius < 0:
        raise EvaluationError("MIH global radius is invalid")
    quotient, remainder = divmod(global_radius, band_count)
    return [quotient] * (remainder + 1) + [quotient - 1] * (band_count - remainder - 1)


def candidate_union(index: list[dict[int, numpy.ndarray]], query: Any, ranges: list[tuple[int, int]], radii: list[int]) -> tuple[numpy.ndarray, int]:
    if len(radii) != len(ranges):
        raise EvaluationError("MIH probe schedule does not match bands")
    selected: set[int] = set()
    probes = 0
    for buckets, (start, stop), radius in zip(index, ranges, radii):
        if radius < 0:
            continue
        for key in probe_keys(band_key(query, start, stop), stop - start, radius):
            probes += 1
            selected.update(buckets.get(key, ()))
    return numpy.asarray(sorted(selected), dtype=numpy.int32), probes


def budgeted_confidence_candidate_union(
    index: list[dict[int, numpy.ndarray]],
    query: Any,
    query_projection: Any,
    ranges: list[tuple[int, int]],
    soft_candidate_target: int,
    soft_posting_visit_target: int | None = None,
) -> tuple[numpy.ndarray, int, int, int, list[int], list[int], str]:
    """Probe exact 32x8 buckets, then one-bit buckets until a soft target.

    The exact-bucket union is mandatory and can exceed ``soft_candidate_target``.
    The target therefore controls only optional one-bit expansion, not a hard
    candidate-set cap.
    """
    if soft_candidate_target <= 0 or len(ranges) != 32 or any(stop - start != 8 for start, stop in ranges):
        raise EvaluationError("budgeted confidence probing requires 32 equal 8-bit bands")
    selected: set[int] = set()
    probes = 0
    posting_visits = 0

    def add(bucket: dict[int, numpy.ndarray], key: int) -> None:
        nonlocal probes, posting_visits
        probes += 1
        positions = bucket.get(key)
        if positions is not None:
            posting_visits += int(positions.size)
            selected.update(positions.tolist())

    for buckets, (start, stop) in zip(index, ranges):
        add(buckets, band_key(query, start, stop))
    exact_bucket_floor_count = len(selected)
    flips = [
        (float(abs(query_projection[start + bit])), band, bit)
        for band, (start, stop) in enumerate(ranges)
        for bit in range(stop - start)
    ]
    optional_probes = 0; optional_visits = 0
    for _, band, bit in sorted(flips):
        if len(selected) >= soft_candidate_target or (soft_posting_visit_target is not None and posting_visits >= soft_posting_visit_target):
            break
        start, stop = ranges[band]
        before = posting_visits
        add(index[band], band_key(query, start, stop) ^ (1 << bit))
        optional_probes += 1; optional_visits += posting_visits - before
    reason = "candidate" if len(selected) >= soft_candidate_target else "posting" if soft_posting_visit_target is not None and posting_visits >= soft_posting_visit_target else "exhausted"
    return numpy.asarray(sorted(selected), dtype=numpy.int32), probes, posting_visits, exact_bucket_floor_count, [optional_probes, 0, 0], [optional_visits, 0, 0], reason


def budgeted_adc_candidate_union(
    index: list[dict[int, numpy.ndarray]],
    query: Any,
    query_projection: Any,
    centers: Any,
    ranges: list[tuple[int, int]],
    soft_candidate_target: int,
) -> tuple[numpy.ndarray, int, int, int, list[int], list[int], str]:
    """Probe exact buckets then least-cost one-bit ADC alternatives.

    The policy is intentionally restricted to the same 32x8, radius-one
    universe as budgeted confidence probing.  It changes only the order of
    optional probes, using query-side binary-ADC loss rather than a raw margin.
    """
    if soft_candidate_target <= 0 or len(ranges) != 32 or any(stop - start != 8 for start, stop in ranges):
        raise EvaluationError("budgeted ADC probing requires 32 equal 8-bit bands")
    values = numpy.asarray(query_projection, dtype=numpy.float32)
    code = numpy.asarray(query, dtype=numpy.uint8)
    calibration_centers = numpy.asarray(centers, dtype=numpy.float32)
    if values.shape != code.shape or calibration_centers.shape != (values.size, 2):
        raise EvaluationError("budgeted ADC probe inputs are invalid")
    selected: set[int] = set()
    probes = 0
    posting_visits = 0

    def add(bucket: dict[int, numpy.ndarray], key: int) -> None:
        nonlocal probes, posting_visits
        probes += 1
        positions = bucket.get(key)
        if positions is not None:
            posting_visits += int(positions.size)
            selected.update(positions.tolist())

    for buckets, (start, stop) in zip(index, ranges):
        add(buckets, band_key(query, start, stop))
    exact_bucket_floor_count = len(selected)
    lookup = numpy.square(values[:, None] - calibration_centers)
    flips = [
        (float(lookup[start + bit, 1 - code[start + bit]] - lookup[start + bit, code[start + bit]]), band, bit)
        for band, (start, stop) in enumerate(ranges)
        for bit in range(stop - start)
    ]
    optional_probes = 0
    optional_visits = 0
    for _, band, bit in sorted(flips):
        if len(selected) >= soft_candidate_target:
            break
        start, _ = ranges[band]
        before = posting_visits
        add(index[band], band_key(query, start, start + 8) ^ (1 << bit))
        optional_probes += 1
        optional_visits += posting_visits - before
    reason = "candidate" if len(selected) >= soft_candidate_target else "exhausted"
    return (
        numpy.asarray(sorted(selected), dtype=numpy.int32), probes, posting_visits,
        exact_bucket_floor_count, [optional_probes, 0, 0], [optional_visits, 0, 0], reason,
    )


def budgeted_adc_best_first_candidate_union(
    index: list[dict[int, numpy.ndarray]],
    query: Any,
    query_projection: Any,
    centers: Any,
    ranges: list[tuple[int, int]],
    soft_candidate_target: int,
    soft_posting_visit_target: int,
    max_probe_bit_flips: int,
) -> tuple[numpy.ndarray, int, int, int, list[int], list[int], str]:
    """Globally enumerate bounded multi-bit MIH buckets by query ADC cost.

    Exact buckets remain mandatory. Optional buckets are ordered by the sum of
    calibration-only binary-ADC symbol-change costs across one band, with a
    deterministic tie-break. The two resource targets stop optional probing;
    they are soft because an already selected exact-bucket floor may exceed
    either target and a final posting can cross a target.
    """
    if (
        soft_candidate_target <= 0 or soft_posting_visit_target <= 0
        or max_probe_bit_flips not in (2, 3) or len(ranges) != 32
        or any(stop - start != 8 for start, stop in ranges)
    ):
        raise EvaluationError("best-first ADC probing arguments are invalid")
    values = numpy.asarray(query_projection, dtype=numpy.float32)
    code = numpy.asarray(query, dtype=numpy.uint8)
    calibration_centers = numpy.asarray(centers, dtype=numpy.float32)
    if values.shape != code.shape or calibration_centers.shape != (values.size, 2):
        raise EvaluationError("best-first ADC probe inputs are invalid")
    selected: set[int] = set()
    probes = 0
    posting_visits = 0

    def add(bucket: dict[int, numpy.ndarray], key: int) -> None:
        nonlocal probes, posting_visits
        probes += 1
        positions = bucket.get(key)
        if positions is not None:
            posting_visits += int(positions.size)
            selected.update(positions.tolist())

    for buckets, (start, stop) in zip(index, ranges):
        add(buckets, band_key(query, start, stop))
    exact_bucket_floor_count = len(selected)
    lookup = numpy.square(values[:, None] - calibration_centers)
    frontier: list[tuple[float, int, int, int]] = []
    for band, (start, stop) in enumerate(ranges):
        deltas = [float(lookup[start + bit, 1 - code[start + bit]] - lookup[start + bit, code[start + bit]]) for bit in range(stop - start)]
        for count in range(1, max_probe_bit_flips + 1):
            for positions in itertools.combinations(range(stop - start), count):
                mask = sum(1 << position for position in positions)
                heapq.heappush(frontier, (sum(deltas[position] for position in positions), count, band, mask))
    depth_probes = [0, 0, 0]; depth_visits = [0, 0, 0]
    while frontier and len(selected) < soft_candidate_target and posting_visits < soft_posting_visit_target:
        _, count, band, mask = heapq.heappop(frontier)
        start, stop = ranges[band]
        before = posting_visits
        add(index[band], band_key(query, start, stop) ^ mask)
        depth_probes[count - 1] += 1; depth_visits[count - 1] += posting_visits - before
    reason = "candidate" if len(selected) >= soft_candidate_target else "posting" if posting_visits >= soft_posting_visit_target else "exhausted"
    return numpy.asarray(sorted(selected), dtype=numpy.int32), probes, posting_visits, exact_bucket_floor_count, depth_probes, depth_visits, reason


def calibrated_hamming_weights(calibration_projection: Any, calibration_codes: Any) -> numpy.ndarray:
    """Return mean-one bit weights from calibration-only binary centroids.

    A mismatch on a coordinate with farther-apart conditional reconstruction
    centroids loses more of the projected value than a mismatch on a weak
    coordinate.  The normalization preserves the scale of ordinary Hamming
    distance while keeping the learned values independent of held-out queries.
    """
    centers = shared.conditional_centers(
        calibration_projection, calibration_codes.astype(numpy.uint8), 2
    )
    weights = numpy.square(centers[:, 1] - centers[:, 0]).astype(numpy.float32)
    if not numpy.all(numpy.isfinite(weights)) or numpy.any(weights <= 0.0):
        raise EvaluationError("calibrated Hamming weights are invalid")
    return (weights / numpy.mean(weights, dtype=numpy.float64)).astype(numpy.float32)


def hamming_weights_sha256(weights: Any) -> str:
    values = numpy.asarray(weights, dtype="<f4")
    if values.ndim != 1 or not numpy.all(numpy.isfinite(values)):
        raise EvaluationError("Hamming weight digest input is invalid")
    return hashlib.sha256(values.tobytes()).hexdigest()


def stable_hamming_order(
    codes: Any,
    query: Any,
    document_ids: Any,
    candidates: Any | None = None,
    weights: Any | None = None,
) -> numpy.ndarray:
    if candidates is None:
        candidates = numpy.arange(codes.shape[0], dtype=numpy.int32)
    mismatches = codes[candidates] != query
    if weights is None:
        distances = numpy.count_nonzero(mismatches, axis=1)
    else:
        values = numpy.asarray(weights, dtype=numpy.float32)
        if values.shape != (codes.shape[1],) or not numpy.all(numpy.isfinite(values)) or numpy.any(values <= 0.0):
            raise EvaluationError("Hamming weights are invalid")
        distances = mismatches @ values
    return candidates[numpy.lexsort((document_ids[candidates], distances))]


def binary_adc_order(
    query_projection: Any,
    centers: Any,
    codes: Any,
    document_ids: Any,
    candidates: Any,
) -> numpy.ndarray:
    """Return a stable ADC order for binary document symbols."""
    lookup = (query_projection[:, None] - centers) ** 2
    symbols = numpy.asarray(codes[candidates].T, dtype=numpy.intp)
    scores = lookup[numpy.arange(codes.shape[1])[:, None], symbols].sum(axis=0)
    return candidates[numpy.lexsort((document_ids[candidates], scores))]


def validate_roots(calibration: dict[str, Any], data: dict[str, Any], code_bits: int) -> None:
    shared.validate_calibration_evaluation_pair(calibration, data)
    if calibration["dimension"] != data["dimension"] or code_bits > calibration["dimension"]:
        raise EvaluationError("MIH calibration and evaluation dimensions are incompatible")


def document_id_set_sha256(document_ids: Any) -> str:
    """Return the train/dev exclusion identity independent of source row order."""
    values = [str(value) for value in document_ids]
    if len(values) != len(set(values)):
        raise EvaluationError("evaluation document IDs are not unique")
    return hashlib.sha256("".join(f"{value}\n" for value in sorted(values)).encode("utf-8")).hexdigest()


def load_mih_aware_itq_artifact(path: Path, calibration: dict[str, Any], data: dict[str, Any], code_bits: int, band_count: int, band_widths: list[int]) -> tuple[dict[str, Any], Any, Any, Any]:
    """Load a document-only or train-query-supervised MIH projection safely."""
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read MIH-aware artifact: {exc}") from exc
    architecture = artifact.get("architecture") if isinstance(artifact, dict) else None
    training = artifact.get("training") if isinstance(artifact, dict) else None
    weights = artifact.get("weights") if isinstance(artifact, dict) else None
    if artifact.get("schema_version") != 1 or not isinstance(architecture, dict) or not isinstance(training, dict) or not isinstance(weights, dict):
        raise EvaluationError("MIH-aware artifact sections are invalid")
    family = architecture.get("family")
    repaired = family == "mih_aware_itq_repaired_control_v1"
    itq_anchor = family == "itq_anchor_projection_v1"
    query_aware = family == "mih_query_aware_hamming_target_shared_w_v1"
    asymmetric = family in ("mih_query_aware_asymmetric_projection_v1", "mih_query_trust_region_projection_v1")
    if family not in ("mih_aware_itq_v1", "mih_aware_itq_repaired_control_v1", "mih_query_aware_hamming_target_shared_w_v1", "itq_anchor_projection_v1", "mih_query_aware_asymmetric_projection_v1", "mih_query_trust_region_projection_v1") or architecture.get("input_dimension") != data["dimension"] or architecture.get("bit_count") != code_bits or (not repaired and architecture.get("band_count") != band_count):
        raise EvaluationError("MIH-aware artifact architecture differs from evaluation")
    if not repaired and band_widths != [architecture.get("band_width_bits")] * band_count:
        raise EvaluationError("MIH-aware artifact band widths differ from evaluation")
    if not query_aware and (artifact.get("input_materialization_manifest_sha256") != calibration["manifest_sha256"] or artifact.get("prepared_study_manifest_sha256") != calibration["prepared_study_manifest_sha256"]):
        raise EvaluationError("MIH-aware artifact calibration provenance differs")
    if query_aware or asymmetric:
        exclusion = training.get("held_out_exclusion")
        if (training.get("queries_or_qrels_used") is not True or
                training.get("objective") not in ("shared_w_qrels_hamming_radius_target_with_mih_frontier_gate_v1", "frozen_document_itq_query_projection_with_train_mih_false_positive_mining_v1") or
                (not asymmetric and not architecture.get("shared_projection")) or
                not isinstance(exclusion, dict) or
                exclusion.get("id") != "external_excluded_document_ids_set_v1" or
                exclusion.get("document_ids_set_sha256") != document_id_set_sha256(data["document_ids"])):
            raise EvaluationError("query-aware artifact train/dev exclusion differs")
    elif training.get("queries_or_qrels_used") is not False or training.get("objective") not in ("document_semantic_itq_quantization_radius_one_mih_work_surrogate_v1", "bipolar_hamming_semantic_full_itq_anchor_v1", "initial_full_itq_anchor_v1"):
        raise EvaluationError("MIH-aware artifact is not document-only")
    projection = shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [code_bits, data["dimension"]], "row_major_out_by_in", "projection_weights")
    thresholds = shared.require_artifact_weight(path.parent, weights.get("thresholds"), [code_bits], None, "thresholds")
    query_projection = projection if not asymmetric else shared.require_artifact_weight(path.parent, weights.get("query_projection_weights"), [code_bits, data["dimension"]], "row_major_out_by_in", "query_projection_weights")
    return artifact, projection, thresholds, query_projection


def write_result(args: Any, report: dict[str, Any], contributions: dict[str, Any], data: dict[str, Any]) -> None:
    """Write paired per-query metrics before their hash is recorded in JSON."""
    contribution_path = args.contributions_output
    contribution_path.parent.mkdir(parents=True, exist_ok=True)
    identity = shared.contribution_identity(data, args.candidate_limit, args.oracle_k)
    numpy.savez_compressed(
        contribution_path,
        **contributions,
        query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_),
        identity_json=numpy.asarray(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
    )
    report["per_query_contributions_path"] = contribution_path.name
    report["per_query_contributions_sha256"] = hashlib.sha256(contribution_path.read_bytes()).hexdigest()
    report["per_query_contribution_identity"] = identity
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate(args: Any) -> None:
    if (
        args.code_bits not in (128, 256)
        or args.itq_iterations <= 0
        or args.candidate_limit <= 0
        or args.hamming_limit <= 0
        or args.second_limit <= 0
        or args.second_limit > args.hamming_limit
        or args.candidate_limit > args.hamming_limit
        or args.oracle_k <= 0
    ):
        raise EvaluationError("MIH evaluation arguments are invalid")
    widths = parse_band_widths(args.band_widths, args.code_bits, args.band_count)
    ranges = variable_band_ranges(args.code_bits, widths)
    if args.global_radius is not None:
        radii = global_radius_schedule(args.global_radius, args.band_count)
    else:
        radii = [args.probe_radius] * args.band_count
    if max(radii) > min(stop - start for start, stop in ranges):
        raise EvaluationError("MIH probe radius exceeds a band width")
    if args.probe_policy in ("budgeted-confidence", "budgeted-adc", "budgeted-adc-best-first") and (
        args.global_radius is not None or args.probe_radius != 1 or args.soft_candidate_target <= 0
    ):
        raise EvaluationError("budgeted probe policies require local radius one and a candidate budget")
    if args.probe_policy == "budgeted-adc-best-first" and (
        args.soft_posting_visit_target <= 0 or args.max_probe_bit_flips not in (2, 3)
    ):
        raise EvaluationError("best-first ADC probing requires posting and flip budgets")
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    validate_roots(calibration, data, args.code_bits)
    artifact = None
    if args.encoder_artifact is None:
        weights = shared.itq_weights(numpy.asarray(calibration["train"]), args.code_bits, args.seed, args.itq_iterations)
        thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights)
    else:
        artifact, weights, thresholds, query_weights = load_mih_aware_itq_artifact(args.encoder_artifact, calibration, data, args.code_bits, args.band_count, widths)
    calibration_projection = numpy.asarray(calibration["train"]) @ weights.T + thresholds
    document_projection = numpy.asarray(data["documents"]) @ weights.T + thresholds
    query_projection = numpy.asarray(data["queries"]) @ (query_weights if artifact is not None else weights).T + thresholds
    calibration_codes = calibration_projection >= 0.0
    codes = document_projection >= 0.0
    query_codes = query_projection >= 0.0
    band_permutation = numpy.arange(args.code_bits, dtype=numpy.intp)
    if args.band_layout == "calibration-correlation-balanced":
        band_permutation = calibrated_band_permutation(calibration_codes, args.band_count)
    elif args.band_layout == "calibration-collision-balanced-variable":
        band_permutation = calibrated_variable_band_permutation(calibration_codes, widths)
    elif args.band_layout == "fixed-random":
        band_permutation = fixed_random_band_permutation(args.code_bits, args.band_layout_seed)
    elif args.band_layout == "explicit-permutation":
        if args.band_permutation is None:
            raise EvaluationError("explicit MIH band permutation path is required")
        band_permutation = explicit_band_permutation(args.band_permutation, args.code_bits)
    selection_provenance = None
    if args.band_layout == "explicit-permutation":
        if args.band_selection_provenance is None:
            raise EvaluationError("explicit MIH band selection provenance path is required")
        selection_provenance = explicit_band_selection_provenance(args.band_selection_provenance, band_permutation)
        if selection_provenance["seed"] != args.seed or selection_provenance["selected_widths"] != widths:
            raise EvaluationError("explicit MIH band selection provenance does not match evaluation")
    elif args.band_selection_provenance is not None:
        raise EvaluationError("MIH band selection provenance requires an explicit permutation")
    if args.band_layout != "contiguous":
        calibration_projection = calibration_projection[:, band_permutation]
        document_projection = document_projection[:, band_permutation]
        query_projection = query_projection[:, band_permutation]
        calibration_codes = calibration_codes[:, band_permutation]
        codes = codes[:, band_permutation]
        query_codes = query_codes[:, band_permutation]
    centers = shared.conditional_centers(calibration_projection, calibration_codes.astype(numpy.uint8), 2) if args.second_stage == "binary-adc" or args.probe_policy in ("budgeted-adc", "budgeted-adc-best-first") else None
    hamming_weights = None
    if args.hamming_policy == "calibrated-centroid-separation":
        hamming_weights = calibrated_hamming_weights(calibration_projection, calibration_codes)
    index = build_index(codes, ranges)
    document_ids = data["document_ids"]
    hamming_recall: list[float] = []
    e5_coverage: list[float] = []
    reranked_ndcg: list[float] = []
    full_e5_ndcg: list[float] = []
    candidate_counts: list[int] = []
    probe_counts: list[int] = []
    posting_visits: list[int] = []
    exact_bucket_floor_counts: list[int] = []
    hamming_scores: list[int] = []
    raw_union_oracle_coverage: list[float] = []
    hamming_oracle_coverage: list[float] = []
    second_oracle_coverage: list[float] = []
    oracle_hamming_distance_mean: list[float] = []
    oracle_hamming_within_48: list[float] = []
    oracle_hamming_within_56: list[float] = []
    oracle_hamming_within_64: list[float] = []
    probe_depths: list[list[int]] = []; posting_depths: list[list[int]] = []; stop_reasons: list[str] = []
    seconds = 0.0
    for row, query_id in enumerate(data["query_ids"]):
        full_hamming = stable_hamming_order(codes, query_codes[row], document_ids, weights=hamming_weights)
        start = time.perf_counter()
        if args.probe_policy == "budgeted-confidence":
            candidates, probes, visits, exact_bucket_floor_count, depth_probes, depth_visits, stop_reason = budgeted_confidence_candidate_union(
                index, query_codes[row], query_projection[row], ranges, args.soft_candidate_target, args.soft_posting_visit_target or None
            )
        elif args.probe_policy == "budgeted-adc":
            candidates, probes, visits, exact_bucket_floor_count, depth_probes, depth_visits, stop_reason = budgeted_adc_candidate_union(
                index, query_codes[row], query_projection[row], centers, ranges, args.soft_candidate_target
            )
        elif args.probe_policy == "budgeted-adc-best-first":
            candidates, probes, visits, exact_bucket_floor_count, depth_probes, depth_visits, stop_reason = budgeted_adc_best_first_candidate_union(
                index, query_codes[row], query_projection[row], centers, ranges,
                args.soft_candidate_target, args.soft_posting_visit_target, args.max_probe_bit_flips,
            )
        else:
            candidates, probes = candidate_union(index, query_codes[row], ranges, radii)
            visits = sum(
                int(index[band].get(key, numpy.empty(0, dtype=numpy.int32)).size)
                for band, ((start_bit, stop_bit), radius) in enumerate(zip(ranges, radii))
                if radius >= 0
                for key in probe_keys(band_key(query_codes[row], start_bit, stop_bit), stop_bit - start_bit, radius)
            )
            exact_bucket_floor_count = 0
            depth_probes = [0, 0, 0]; depth_visits = [0, 0, 0]; stop_reason = "fixed-radius"
        restricted = stable_hamming_order(codes, query_codes[row], document_ids, candidates, hamming_weights)[:args.hamming_limit]
        seconds += time.perf_counter() - start
        candidate_counts.append(int(candidates.size)); probe_counts.append(probes); posting_visits.append(visits); exact_bucket_floor_counts.append(exact_bucket_floor_count); hamming_scores.append(int(candidates.size))
        probe_depths.append(depth_probes); posting_depths.append(depth_visits); stop_reasons.append(stop_reason)
        hamming_recall.append(float(numpy.isin(full_hamming[:args.candidate_limit], restricted[:args.candidate_limit]).sum()) / args.candidate_limit)
        if args.second_stage == "binary-adc":
            second = binary_adc_order(query_projection[row], centers, codes, document_ids, restricted)[:args.second_limit]
        else:
            second = restricted[:args.second_limit]
        exact_scores = numpy.asarray(data["documents"])[second] @ numpy.asarray(data["queries"])[row]
        rerank = second[numpy.lexsort((document_ids[second], -exact_scores))]
        full_exact = numpy.asarray(data["documents"]) @ numpy.asarray(data["queries"])[row]
        exact_order = numpy.lexsort((document_ids, -full_exact))
        oracle = exact_order[:args.oracle_k]
        raw_union_oracle_coverage.append(float(numpy.isin(oracle, candidates).sum()) / args.oracle_k)
        hamming_oracle_coverage.append(float(numpy.isin(oracle, restricted).sum()) / args.oracle_k)
        second_oracle_coverage.append(float(numpy.isin(oracle, second).sum()) / args.oracle_k)
        oracle_distances = numpy.count_nonzero(codes[oracle] != query_codes[row], axis=1)
        oracle_hamming_distance_mean.append(float(oracle_distances.mean()))
        oracle_hamming_within_48.append(float(numpy.mean(oracle_distances <= 48)))
        oracle_hamming_within_56.append(float(numpy.mean(oracle_distances <= 56)))
        oracle_hamming_within_64.append(float(numpy.mean(oracle_distances <= 64)))
        e5_coverage.append(float(numpy.isin(oracle, rerank).sum()) / args.oracle_k)
        reranked_ndcg.append(shared.dcg_at_10(document_ids[rerank], data["qrels"][query_id]))
        full_e5_ndcg.append(shared.dcg_at_10(document_ids[exact_order], data["qrels"][query_id]))
    guarantee = args.global_radius is not None
    source_files = source_files_sha256()
    report = {
        "schema_version": 6, "family": "mih_banding_reference_v6",
        "evaluator_source_files_sha256": source_files,
        "evaluator_source_bundle_sha256": source_bundle_sha256(source_files),
        "evaluator_runtime": shared.evaluator_runtime(),
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "calibration_vector_count": len(calibration["train_ids"]),
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "code_bits": args.code_bits, "band_count": args.band_count, "band_width_bits": [stop - start for start, stop in ranges], "probe_radius": args.max_probe_bit_flips if args.probe_policy == "budgeted-adc-best-first" else args.probe_radius, "base_probe_radius": args.probe_radius, "global_radius": args.global_radius, "band_probe_radii": [args.max_probe_bit_flips] * args.band_count if args.probe_policy == "budgeted-adc-best-first" else radii,
        "fixed_radius": args.global_radius, "fixed_radius_exact_guarantee": guarantee, "candidate_limit": args.candidate_limit, "hamming_limit": args.hamming_limit, "second_limit": args.second_limit, "second_stage": args.second_stage, "oracle_k": args.oracle_k, "probe_policy": args.probe_policy, "soft_candidate_target": args.soft_candidate_target if args.probe_policy in ("budgeted-confidence", "budgeted-adc", "budgeted-adc-best-first") else None,
        "soft_posting_visit_target": args.soft_posting_visit_target if args.probe_policy in ("budgeted-confidence", "budgeted-adc-best-first") else None,
        "max_probe_bit_flips": args.max_probe_bit_flips if args.probe_policy == "budgeted-adc-best-first" else None,
        "hamming_policy": args.hamming_policy,
        "calibrated_hamming_weights_sha256": hamming_weights_sha256(hamming_weights) if hamming_weights is not None else None,
        "calibrated_hamming_weight_min": float(numpy.min(hamming_weights)) if hamming_weights is not None else None,
        "calibrated_hamming_weight_max": float(numpy.max(hamming_weights)) if hamming_weights is not None else None,
        "band_layout": args.band_layout,
        "band_layout_seed": args.band_layout_seed if args.band_layout == "fixed-random" else None,
        "band_layout_explicit_permutation_sha256": band_layout_sha256(band_permutation) if args.band_layout == "explicit-permutation" else None,
        "band_layout_selection_provenance": selection_provenance,
        "band_layout_objective": BALANCED_BAND_OBJECTIVE if args.band_layout == "calibration-correlation-balanced" else None,
        "band_layout_entropy_balance_weight": ENTROPY_BALANCE_WEIGHT if args.band_layout == "calibration-correlation-balanced" else None,
        "band_layout_variable_width_objective": VARIABLE_WIDTH_BAND_OBJECTIVE if args.band_layout == "calibration-collision-balanced-variable" else None,
        "band_layout_sha256": band_layout_sha256(band_permutation),
        "mean_intraband_absolute_correlation": mean_intraband_absolute_correlation(calibration_codes, ranges),
        "seed": args.seed, "itq_iterations": args.itq_iterations if artifact is None else None, "encoder_artifact_sha256": hashlib.sha256(args.encoder_artifact.read_bytes()).hexdigest() if artifact is not None else None, "encoder_artifact_family": artifact["architecture"]["family"] if artifact is not None else "itq_rotation_projection", "query_count": len(data["query_ids"]),
        "hamming_top_k_recall": float(numpy.mean(hamming_recall)), "exact_top_k_candidate_coverage": float(numpy.mean(e5_coverage)), "reranked_ndcg_at_10": float(numpy.mean(reranked_ndcg)), "full_e5_ndcg_at_10": float(numpy.mean(full_e5_ndcg)),
        "mean_candidates_per_query": float(numpy.mean(candidate_counts)), "mean_exact_bucket_floor_candidates_per_query": float(numpy.mean(exact_bucket_floor_counts)), "mean_bucket_probes_per_query": float(numpy.mean(probe_counts)), "mean_posting_visits_per_query": float(numpy.mean(posting_visits)), "mean_posting_bytes_per_query": float(numpy.mean(posting_visits) * numpy.dtype(numpy.int32).itemsize), "mean_full_hamming_scores_per_query": float(numpy.mean(hamming_scores)),
        "e5_oracle_survival": {"raw_union": float(numpy.mean(raw_union_oracle_coverage)), "hamming_top_k": float(numpy.mean(hamming_oracle_coverage)), "second_stage": float(numpy.mean(second_oracle_coverage)), "mean_full_hamming_distance": float(numpy.mean(oracle_hamming_distance_mean)), "hamming_within_radius": {"48": float(numpy.mean(oracle_hamming_within_48)), "56": float(numpy.mean(oracle_hamming_within_56)), "64": float(numpy.mean(oracle_hamming_within_64))}},
        "reference_candidate_generation_seconds": seconds,
        "mean_probe_count_by_flip_depth": [float(numpy.mean(numpy.asarray(probe_depths)[:, depth])) for depth in range(3)],
        "mean_posting_visits_by_flip_depth": [float(numpy.mean(numpy.asarray(posting_depths)[:, depth])) for depth in range(3)],
        "stop_reason_fractions": {reason: float(stop_reasons.count(reason)) / len(stop_reasons) for reason in ("candidate", "posting", "exhausted", "fixed-radius")},
    }
    contributions = {
        "hamming_top_k_recall": numpy.asarray(hamming_recall, dtype=numpy.float64),
        "coverage_at_candidate_limit": numpy.asarray(e5_coverage, dtype=numpy.float64),
        "reranked_ndcg_at_10": numpy.asarray(reranked_ndcg, dtype=numpy.float64),
        "full_e5_ndcg_at_10": numpy.asarray(full_e5_ndcg, dtype=numpy.float64),
        "candidate_count": numpy.asarray(candidate_counts, dtype=numpy.int32),
        "exact_bucket_floor_candidate_count": numpy.asarray(exact_bucket_floor_counts, dtype=numpy.int32),
        "bucket_probe_count": numpy.asarray(probe_counts, dtype=numpy.int32),
        "posting_visit_count": numpy.asarray(posting_visits, dtype=numpy.int32),
        "e5_oracle_raw_union_coverage": numpy.asarray(raw_union_oracle_coverage, dtype=numpy.float64),
        "e5_oracle_hamming_top_k_coverage": numpy.asarray(hamming_oracle_coverage, dtype=numpy.float64),
        "e5_oracle_second_stage_coverage": numpy.asarray(second_oracle_coverage, dtype=numpy.float64),
        "e5_oracle_mean_full_hamming_distance": numpy.asarray(oracle_hamming_distance_mean, dtype=numpy.float64),
        "e5_oracle_hamming_within_48": numpy.asarray(oracle_hamming_within_48, dtype=numpy.float64),
        "e5_oracle_hamming_within_56": numpy.asarray(oracle_hamming_within_56, dtype=numpy.float64),
        "e5_oracle_hamming_within_64": numpy.asarray(oracle_hamming_within_64, dtype=numpy.float64),
        "probe_count_by_flip_depth": numpy.asarray(probe_depths, dtype=numpy.int32),
        "posting_visit_count_by_flip_depth": numpy.asarray(posting_depths, dtype=numpy.int32),
        "stop_reason": numpy.asarray(stop_reasons, dtype=numpy.str_),
    }
    write_result(args, report, contributions, data)


def self_test() -> int:
    codes = numpy.asarray([[0, 0, 0, 0], [1, 0, 0, 0], [1, 1, 0, 0], [1, 1, 1, 1]], dtype=bool)
    ranges = band_ranges(4, 2); index = build_index(codes, ranges)
    selected, _ = candidate_union(index, numpy.asarray([1, 0, 0, 0], dtype=bool), ranges, [0, 0])
    if set(selected.tolist()) != {0, 1, 2}:
        print("self-test failed: exact-band union is incomplete", file=sys.stderr); return 1
    if set(probe_keys(0, 4, 1)) != {0, 1, 2, 4, 8}:
        print("self-test failed: multiprobe keys are invalid", file=sys.stderr); return 1
    if band_ranges(5, 2) != [(0, 3), (3, 5)]:
        print("self-test failed: uneven band partition is invalid", file=sys.stderr); return 1
    if variable_band_ranges(12, [2, 4, 6]) != [(0, 2), (2, 6), (6, 12)] or parse_band_widths("2,4,6", 12, 3) != [2, 4, 6]:
        print("self-test failed: variable band ranges are invalid", file=sys.stderr); return 1
    layout_codes = numpy.asarray([[False, False, False, False], [False, False, True, True], [True, True, False, False], [True, True, True, True]], dtype=bool)
    layout = calibrated_band_permutation(layout_codes, 2)
    if layout.shape != (4,) or not numpy.array_equal(numpy.sort(layout), numpy.arange(4)) or not band_layout_sha256(layout):
        print("self-test failed: calibrated band layout is invalid", file=sys.stderr); return 1
    with __import__("tempfile").TemporaryDirectory() as directory:
        path = Path(directory) / "permutation.json"; path.write_text("[2,0,3,1]", encoding="utf-8")
        if explicit_band_permutation(path, 4).tolist() != [2, 0, 3, 1]:
            print("self-test failed: explicit band permutation is invalid", file=sys.stderr); return 1
        provenance = Path(directory) / "provenance.json"
        provenance.write_text(json.dumps({"schema_version": 1, "family": "mih_static_width_optimizer_selection_v1", "optimizer_report_sha256": "a" * 64, "seed": 52, "selected_id": "candidate", "selected_widths": [2, 2], "selected_permutation_sha256": band_layout_sha256(numpy.asarray([2, 0, 3, 1], dtype=numpy.intp))}), encoding="utf-8")
        if explicit_band_selection_provenance(provenance, numpy.asarray([2, 0, 3, 1], dtype=numpy.intp))["seed"] != 52:
            print("self-test failed: explicit band selection provenance is invalid", file=sys.stderr); return 1
    if mean_intraband_absolute_correlation(layout_codes[:, layout], 2) >= mean_intraband_absolute_correlation(layout_codes, 2):
        print("self-test failed: calibrated band layout did not reduce correlation", file=sys.stderr); return 1
    variable_layout = calibrated_variable_band_permutation(layout_codes, [1, 3])
    if variable_layout.shape != (4,) or not numpy.array_equal(numpy.sort(variable_layout), numpy.arange(4)):
        print("self-test failed: calibrated variable-width layout is invalid", file=sys.stderr); return 1
    uneven_codes = numpy.asarray([[False, False, False, False, False], [False, True, False, True, False], [True, False, True, False, True], [True, True, True, True, True]], dtype=bool)
    if not numpy.isfinite(mean_intraband_absolute_correlation(uneven_codes, 2)):
        print("self-test failed: uneven band correlation is invalid", file=sys.stderr); return 1
    if mean_intraband_absolute_correlation(uneven_codes, 5) != 0.0:
        print("self-test failed: one-bit band correlation is invalid", file=sys.stderr); return 1
    random_layout = fixed_random_band_permutation(4, 20260812)
    if not numpy.array_equal(random_layout, fixed_random_band_permutation(4, 20260812)) or not numpy.array_equal(numpy.sort(random_layout), numpy.arange(4)):
        print("self-test failed: fixed random band layout is invalid", file=sys.stderr); return 1
    if global_radius_schedule(16, 16) != [1] + [0] * 15:
        print("self-test failed: global radius schedule is invalid", file=sys.stderr); return 1
    budget_codes = numpy.zeros((3, 256), dtype=bool)
    budget_codes[1, 0] = True
    budget_codes[2, 1] = True
    budget_ranges = band_ranges(256, 32)
    budget_index = build_index(budget_codes, budget_ranges)
    budgeted, budget_probes, budget_visits, exact_bucket_floor, _, _, _ = budgeted_confidence_candidate_union(
        budget_index, budget_codes[0], numpy.asarray([0.1, 0.2] + [1.0] * 254), budget_ranges, 2
    )
    if set(budgeted.tolist()) != {0, 1, 2} or exact_bucket_floor != 3 or budget_probes != 32 or budget_visits != 94:
        print("self-test failed: budgeted confidence exact-bucket lower bound is invalid", file=sys.stderr); return 1
    adc_probe_codes = numpy.zeros((3, 256), dtype=bool)
    adc_probe_codes[1, numpy.arange(0, 256, 8)] = True
    adc_probe_codes[2, numpy.arange(1, 256, 8)] = True
    adc_probe_index = build_index(adc_probe_codes, budget_ranges)
    adc_probe_centers = numpy.tile(numpy.asarray([[0.0, 1.0]], dtype=numpy.float32), (256, 1))
    adc_probe_centers[0] = numpy.asarray([1.0, 0.0], dtype=numpy.float32)
    adc_probe_centers[1] = numpy.asarray([0.0, 2.0], dtype=numpy.float32)
    adc_budgeted, adc_budget_probes, adc_budget_visits, adc_floor, adc_depth_probes, adc_depth_visits, adc_reason = budgeted_adc_candidate_union(
        adc_probe_index, adc_probe_codes[0], numpy.zeros(256, dtype=numpy.float32), adc_probe_centers, budget_ranges, 2,
    )
    if (
        set(adc_budgeted.tolist()) != {0, 1} or adc_floor != 1 or adc_budget_probes != 33
        or adc_budget_visits != 33 or adc_depth_probes != [1, 0, 0]
        or adc_depth_visits != [1, 0, 0] or adc_reason != "candidate"
    ):
        print("self-test failed: budgeted ADC optional probe ordering is invalid", file=sys.stderr); return 1
    best_first, best_first_probes, best_first_visits, best_first_floor, _, _, _ = budgeted_adc_best_first_candidate_union(
        adc_probe_index, adc_probe_codes[0], numpy.zeros(256, dtype=numpy.float32), adc_probe_centers,
        budget_ranges, 3, 10000, 2,
    )
    replay, replay_probes, replay_visits, replay_floor, _, _, _ = budgeted_adc_best_first_candidate_union(
        adc_probe_index, adc_probe_codes[0], numpy.zeros(256, dtype=numpy.float32), adc_probe_centers,
        budget_ranges, 3, 10000, 2,
    )
    if set(best_first.tolist()) != {0, 1, 2} or best_first_floor != 1 or best_first_probes <= 33 or (best_first.tolist(), best_first_probes, best_first_visits, best_first_floor) != (replay.tolist(), replay_probes, replay_visits, replay_floor):
        print("self-test failed: best-first multi-bit probing is invalid", file=sys.stderr); return 1
    multi_bit_codes = numpy.zeros((2, 256), dtype=bool)
    for band_start, _ in budget_ranges:
        multi_bit_codes[0, band_start:band_start + 2] = True
        multi_bit_codes[1, band_start:band_start + 3] = True
    multi_bit_index = build_index(multi_bit_codes, budget_ranges)
    multi_bit_centers = numpy.tile(numpy.asarray([[0.0, 1.0]], dtype=numpy.float32), (256, 1))
    two_bit, _, _, two_floor, two_depth_probes, _, two_reason = budgeted_adc_best_first_candidate_union(
        multi_bit_index, numpy.zeros(256, dtype=bool), numpy.zeros(256, dtype=numpy.float32),
        multi_bit_centers, budget_ranges, 2, 10000, 2,
    )
    three_bit, _, _, three_floor, three_depth_probes, _, three_reason = budgeted_adc_best_first_candidate_union(
        multi_bit_index, numpy.zeros(256, dtype=bool), numpy.zeros(256, dtype=numpy.float32),
        multi_bit_centers, budget_ranges, 2, 10000, 3,
    )
    if (
        two_floor != 0 or three_floor != 0
        or two_bit.tolist() != [0] or three_bit.tolist() != [0, 1]
        or two_depth_probes[1] == 0 or two_depth_probes[2] != 0 or two_reason != "exhausted"
        or three_depth_probes[1] == 0 or three_depth_probes[2] == 0 or three_reason != "candidate"
    ):
        print("self-test failed: best-first multi-bit depth reachability is invalid", file=sys.stderr); return 1
    adc_codes = numpy.asarray([[False, False], [True, False], [True, True]], dtype=bool)
    adc_centers = numpy.asarray([[-1.0, 1.0], [-1.0, 1.0]], dtype=numpy.float32)
    adc_order = binary_adc_order(
        numpy.asarray([0.8, -0.9], dtype=numpy.float32),
        adc_centers,
        adc_codes,
        numpy.asarray(["a", "b", "c"]),
        numpy.asarray([0, 1, 2], dtype=numpy.int32),
    )
    if adc_order.tolist() != [1, 0, 2]:
        print("self-test failed: binary ADC symbol indexing is invalid", file=sys.stderr); return 1
    weight_projection = numpy.asarray([[-3.0, -1.0], [-2.0, 1.0], [2.0, -1.0], [3.0, 1.0]], dtype=numpy.float32)
    weight_codes = weight_projection >= 0.0
    weights = calibrated_hamming_weights(weight_projection, weight_codes)
    if weights.shape != (2,) or not numpy.isclose(float(weights.mean()), 1.0) or weights[0] <= weights[1]:
        print("self-test failed: calibrated Hamming weights are invalid", file=sys.stderr); return 1
    weighted_order = stable_hamming_order(
        numpy.asarray([[False, True], [True, False]], dtype=bool),
        numpy.asarray([False, False], dtype=bool), numpy.asarray(["a", "b"]),
        weights=numpy.asarray([2.0, 1.0], dtype=numpy.float32),
    )
    if weighted_order.tolist() != [0, 1] or not hamming_weights_sha256(weights):
        print("self-test failed: weighted Hamming order is invalid", file=sys.stderr); return 1
    calibration = {"embedding_identity": {"model": "test"}, "train_ids": ["calibration"], "dimension": 4}
    evaluation = {"embedding_identity": {"model": "test"}, "document_ids": numpy.asarray(["evaluation"]), "dimension": 4}
    validate_roots(calibration, evaluation, 4)
    evaluation["document_ids"] = numpy.asarray(["calibration"])
    try:
        validate_roots(calibration, evaluation, 4)
        print("self-test failed: overlapping held-out roots accepted", file=sys.stderr); return 1
    except EvaluationError:
        pass
    files = source_files_sha256()
    if set(files) != {Path(__file__).name, "evaluate-projection-quantization.py"} or source_bundle_sha256(files) != source_bundle_sha256(dict(reversed(list(files.items())))):
        print("self-test failed: evaluator source bundle is invalid", file=sys.stderr); return 1
    print("MIH banding self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--calibration-root", type=Path, required=True); run.add_argument("--evaluation-root", type=Path, required=True); run.add_argument("--output", type=Path, required=True); run.add_argument("--contributions-output", type=Path, required=True)
    run.add_argument("--code-bits", type=int, required=True); run.add_argument("--band-count", type=int, required=True); run.add_argument("--probe-radius", type=int, default=0); run.add_argument("--global-radius", type=int); run.add_argument("--probe-policy", choices=("uniform-radius", "budgeted-confidence", "budgeted-adc", "budgeted-adc-best-first"), default="uniform-radius"); run.add_argument("--soft-candidate-target", type=int, default=0); run.add_argument("--soft-posting-visit-target", type=int, default=0); run.add_argument("--max-probe-bit-flips", type=int, default=0); run.add_argument("--hamming-policy", choices=("uniform", "calibrated-centroid-separation"), default="uniform"); run.add_argument("--band-layout", choices=("contiguous", "fixed-random", "calibration-correlation-balanced", "calibration-collision-balanced-variable", "explicit-permutation"), default="contiguous"); run.add_argument("--band-layout-seed", type=int, default=20260812); run.add_argument("--band-permutation", type=Path); run.add_argument("--band-selection-provenance", type=Path)
    run.add_argument("--band-widths"); run.add_argument("--seed", type=int, default=42); run.add_argument("--itq-iterations", type=int, default=50); run.add_argument("--encoder-artifact", type=Path); run.add_argument("--candidate-limit", type=int, default=512); run.add_argument("--hamming-limit", type=int, default=512); run.add_argument("--second-limit", type=int, default=512); run.add_argument("--second-stage", choices=("hamming", "binary-adc"), default="hamming"); run.add_argument("--oracle-k", type=int, default=10)
    sub.add_parser("self-test"); args = parser.parse_args(argv)
    try:
        if args.command == "evaluate": evaluate(args)
        else: return self_test()
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"evaluate-mih-banding: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
