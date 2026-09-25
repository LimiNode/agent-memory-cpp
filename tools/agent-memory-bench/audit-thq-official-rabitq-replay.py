#!/usr/bin/env python3
"""Independent score and quality audit for the official multi-bit RaBitQ replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np


D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
RABITQ_REVISION = "a010649f8faabc286070e5ed18c7dc121e01ffe3"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    packed = np.asarray(codes, dtype=np.uint8)
    levels = np.empty((len(packed), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        value = packed[:, byte]
        levels[:, 4 * byte : 4 * byte + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3),
            axis=1,
        )
    return levels


def interval_top(query, ids, codes, thresholds):
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for dimension in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[dimension, level - 1]
            high = np.inf if level == 3 else thresholds[dimension, level]
            delta = low - query[dimension] if query[dimension] < low else query[dimension] - high if query[dimension] > high else 0.0
            lut[dimension, level] = delta * delta
    distances = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, distances))[: min(TOP, len(ids))]]


def load_candidates(flat, raw, receipt):
    rows = json.loads(raw.read_text(encoding="utf-8")).get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT, "candidate raw cardinality differs")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    bound = json.loads(receipt.read_text(encoding="utf-8"))
    record_bytes = int(bound.get("flat_file", {}).get("record_bytes", 148))
    require(record_bytes in (100, 148), "candidate record size differs")
    require(flat.stat().st_size == int(offsets[-1]) * record_bytes, "candidate flat size differs")
    require(bound.get("raw_sha256") == sha256(raw), "candidate raw receipt binding differs")
    require(bound.get("flat_file", {}).get("sha256") == sha256(flat), "candidate flat receipt binding differs")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), record_bytes))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    return ids, offsets


def cosine(values, query):
    matrix = np.asarray(values, dtype=np.float64)
    vector = np.asarray(query, dtype=np.float64)
    denominator = np.maximum(np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector), np.finfo(np.float64).tiny)
    return (matrix @ vector) / denominator


def top_ids(scores, ids):
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def ndcg10(ids, qrel_ids, grades):
    relevance = {int(document): float(grade) for document, grade in zip(qrel_ids, grades) if int(document) >= 0 and float(grade) > 0}
    gains = np.asarray([2.0 ** relevance.get(int(document), 0.0) - 1.0 for document in ids])
    ideal = np.sort(np.asarray([2.0**grade - 1.0 for grade in relevance.values()]))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    idcg = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / idcg) if idcg else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--native-source", type=Path, required=True)
    parser.add_argument("--native-cmake", type=Path, required=True)
    parser.add_argument("--rabitq-root", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    source_names = (
        "documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
        "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw",
        "candidate-receipt",
    )
    for name in source_names:
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "official_rabitq_multibit_thq_replay_v1", "result family differs")
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True, "result status differs")
    require(result.get("metric") == "cosine_via_normalized_ip", "metric differs")
    require(result.get("query_count") == QUERY_COUNT and result.get("candidate_count") == TOP, "protocol cardinality differs")
    widths = tuple(int(value) for value in result.get("widths", ()))
    require(widths and len(set(widths)) == len(widths) and all(value in (2, 3, 4) for value in widths), "widths differ")

    revision = subprocess.run(
        ["git", "-C", str(args.rabitq_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(revision == RABITQ_REVISION == result.get("upstream", {}).get("revision"), "RaBitQ revision differs")
    expected_implementation = {
        "producer": sha256(args.producer),
        "native_source": sha256(args.native_source),
        "native_cmake": sha256(args.native_cmake),
    }
    require(result.get("implementation_hashes") == expected_implementation, "implementation SHA binding differs")
    native_build = result.get("native_build", {})
    require(isinstance(native_build.get("compiler"), str) and native_build["compiler"], "native compiler is missing")
    require(isinstance(native_build.get("build_type"), str) and native_build["build_type"], "native build type is missing")
    require(native_build.get("native_optimization") is False, "native optimization contract differs")
    hardware = result.get("hardware", {})
    require(isinstance(hardware.get("cpu_model"), str) and hardware["cpu_model"], "CPU model is missing")
    physical = int(hardware.get("cpu_physical_cores", 0))
    logical = int(hardware.get("cpu_logical_cores", 0))
    require(0 < physical <= logical and result.get("native_threads") == 1, "CPU/thread provenance differs")

    sources = {name: getattr(args, name.replace("-", "_")) for name in source_names}
    require(result.get("source_hashes") == {name: sha256(path) for name, path in sources.items()}, "source SHA binding differs")
    artifact_paths = [
        path
        for path in sorted(args.artifact_dir.iterdir())
        if path.is_file() and path.resolve() not in {args.result.resolve(), args.output.resolve()}
    ]
    require(result.get("artifact_hashes") == {path.name: sha256(path) for path in artifact_paths}, "artifact SHA binding differs")

    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    selected = np.fromfile(args.artifact_dir / "selected-ids.i64", dtype="<i8").reshape(QUERY_COUNT, TOP)
    for query_index, query in enumerate(queries):
        begin, end = offsets[query_index : query_index + 2]
        require(np.array_equal(selected[query_index], interval_top(query, candidate_ids[begin:end], thq_codes, thresholds)), f"query {query_index}: THQ selection differs")

    unique_ids = np.fromfile(args.artifact_dir / "unique-ids.i64", dtype="<i8")
    require(np.array_equal(unique_ids, np.unique(selected)), "unique selected document IDs differ")
    position = {int(document): index for index, document in enumerate(unique_ids)}
    occurrence_positions = np.asarray([position[int(document)] for document in selected.reshape(-1)], dtype=np.int64)
    rotation = np.fromfile(args.artifact_dir / "rotation-matrix.f32", dtype="<f4").reshape(D, D)
    identity_error = float(np.max(np.abs(np.asarray(rotation, dtype=np.float64) @ np.asarray(rotation.T, dtype=np.float64) - np.eye(D))))
    require(identity_error < 2e-6, "persisted rotation is not orthogonal")
    normalized_queries = np.asarray(queries, dtype=np.float64)
    normalized_queries /= np.linalg.norm(normalized_queries, axis=1, keepdims=True)
    replayed_rotated_queries = normalized_queries @ np.asarray(rotation, dtype=np.float64)
    rotated_queries = np.fromfile(args.artifact_dir / "rotated-queries.f32", dtype="<f4").reshape(QUERY_COUNT, D)
    require(np.allclose(rotated_queries, replayed_rotated_queries, rtol=0.0, atol=3e-6), "rotated query replay differs")

    native = json.loads((args.artifact_dir / "native-metadata.json").read_text(encoding="utf-8"))
    native_widths = {int(row["bits"]): row for row in native["widths"]}
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    row_map = {(int(row["query"]), row["arm"]): row for row in result.get("rows", [])}
    require(len(row_map) == QUERY_COUNT * len(widths), "result row cardinality/uniqueness differs")
    audited_summaries = {}
    score_errors = {}
    compact_errors = {}
    for bits in widths:
        codes = np.fromfile(args.artifact_dir / f"rabitq-b{bits}.full-codes.u8", dtype=np.uint8).reshape(len(unique_ids), D)
        factors = np.fromfile(args.artifact_dir / f"rabitq-b{bits}.full-factors.f32", dtype="<f4").reshape(len(unique_ids), 3)
        native_full_scores = np.fromfile(args.artifact_dir / f"rabitq-b{bits}.full-scores.f32", dtype="<f4").reshape(QUERY_COUNT, TOP)
        compact_scores = np.fromfile(args.artifact_dir / f"rabitq-b{bits}.compact-scores.f32", dtype="<f4").reshape(QUERY_COUNT, TOP)
        replayed_scores = np.empty((QUERY_COUNT, TOP), dtype=np.float64)
        for query_index in range(QUERY_COUNT):
            rows_for_query = occurrence_positions[query_index * TOP : (query_index + 1) * TOP]
            query = np.asarray(rotated_queries[query_index], dtype=np.float32)
            integer_inner_products = np.sum(np.asarray(codes[rows_for_query], dtype=np.float32) * query[None, :], axis=1, dtype=np.float32)
            centered_sum = np.float32(-0.5) * np.sum(query, dtype=np.float32) * np.float32((1 << bits) - 1)
            distances = factors[rows_for_query, 0] + factors[rows_for_query, 1] * (integer_inner_products + centered_sum)
            replayed_scores[query_index] = 1.0 - np.asarray(distances, dtype=np.float64)
        score_error = float(np.max(np.abs(replayed_scores - native_full_scores)))
        compact_error = float(np.max(np.abs(native_full_scores - compact_scores)))
        require(score_error < 2e-5, f"B={bits}: independent expanded-code score replay differs")
        require(compact_error < 2e-5, f"B={bits}: compact/full official scorer parity differs")
        require(abs(compact_error - float(native_widths[bits]["full_compact_max_abs_score_difference"])) < 1e-7, f"B={bits}: native parity summary differs")
        score_errors[str(bits)] = score_error
        compact_errors[str(bits)] = compact_error

        metrics, candidate_overlaps, teacher_overlaps = [], [], []
        for query_index, query in enumerate(queries):
            begin, end = offsets[query_index : query_index + 2]
            shell_ids = candidate_ids[begin:end]
            exact = top_ids(cosine(np.asarray(documents[shell_ids]), query), shell_ids)
            ranked = top_ids(compact_scores[query_index], selected[query_index])
            row = row_map[(query_index, f"official_rabitq_b{bits}")]
            require(row["top10_ids"] == ranked.astype(int).tolist(), f"query {query_index}, B={bits}: top10 differs")
            require(row["thq4_top128_ids"] == selected[query_index].astype(int).tolist(), f"query {query_index}, B={bits}: THQ IDs differ")
            require(row["candidate_fp32_top10_ids"] == exact.astype(int).tolist(), f"query {query_index}, B={bits}: exact IDs differ")
            metric = ndcg10(ranked, qrel_ids[query_index], qrel_scores[query_index])
            candidate_overlap = float(np.isin(exact, ranked).sum() / 10)
            teacher_overlap = float(np.isin(teacher_ids[query_index], ranked).sum() / 10)
            require(abs(float(row["qrels_ndcg10"]) - metric) < 1e-12, f"query {query_index}, B={bits}: nDCG differs")
            require(abs(float(row["candidate_fp32_overlap"]) - candidate_overlap) < 1e-12, f"query {query_index}, B={bits}: candidate overlap differs")
            require(abs(float(row["teacher_overlap"]) - teacher_overlap) < 1e-12, f"query {query_index}, B={bits}: teacher overlap differs")
            metrics.append(metric)
            candidate_overlaps.append(candidate_overlap)
            teacher_overlaps.append(teacher_overlap)
        key = f"official_rabitq_b{bits}"
        audited = {
            "mean_qrels_ndcg10": float(np.mean(metrics)),
            "p05_qrels_ndcg10": float(np.percentile(metrics, 5)),
            "worst_qrels_ndcg10": float(np.min(metrics)),
            "mean_candidate_fp32_overlap": float(np.mean(candidate_overlaps)),
            "mean_teacher_overlap": float(np.mean(teacher_overlaps)),
        }
        recorded = result.get("summaries", {}).get(key, {})
        for field, value in audited.items():
            require(abs(float(recorded.get(field, np.nan)) - value) < 1e-12, f"summary differs: {key}/{field}")
        expected_side = int(native_widths[bits]["bin_bytes"]) + int(native_widths[bits]["ex_bytes"])
        require(int(recorded.get("side_payload_bytes", -1)) == expected_side, f"B={bits}: side storage differs")
        require(int(recorded.get("cascade_total_bytes", -1)) == THQ_BYTES + expected_side, f"B={bits}: cascade storage differs")
        audited_summaries[key] = audited

    audit = {
        "schema_version": 1,
        "family": "official_rabitq_multibit_thq_replay_audit_v1",
        "status": "PASS",
        "source_replay": True,
        "independent_expanded_code_score_replay": True,
        "official_compact_full_scorer_parity": True,
        "result_sha256": sha256(args.result),
        "implementation_hashes": expected_implementation,
        "upstream_revision": revision,
        "input_hashes": result["source_hashes"],
        "artifact_hashes": result["artifact_hashes"],
        "query_count": QUERY_COUNT,
        "row_count": len(row_map),
        "rotation_orthogonality_max_abs_error": identity_error,
        "expanded_score_max_abs_error_by_width": score_errors,
        "compact_full_max_abs_error_by_width": compact_errors,
        "summaries": audited_summaries,
        "checks": [
            "source, implementation, upstream and artifact SHA binding",
            "canonical THQ interval-squared top128 replay",
            "rotation orthogonality and normalized-query rotation replay",
            "independent expanded-code RaBitQ score equation",
            "official compact split scorer parity with expanded official quantizer",
            "top10, overlap, teacher and qrels nDCG replay",
            "CPU, compiler, build, thread and logical storage provenance",
        ],
        "limitations": [
            "official quantizer output is source-bound rather than independently re-fitted",
            "historical 152-query engineering fold; fresh confirmation remains required",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("official multi-bit RaBitQ audit PASS")


if __name__ == "__main__":
    main()
