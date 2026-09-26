#!/usr/bin/env python3
"""Run the pinned official multi-bit RaBitQ scorer on canonical THQ top-128."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from research_hardware_provenance import hardware_snapshot


D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
RABITQ_REVISION = "a010649f8faabc286070e5ed18c7dc121e01ffe3"


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


def interval_top(
    query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray
) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for dimension in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[dimension, level - 1]
            high = np.inf if level == 3 else thresholds[dimension, level]
            delta = (
                low - query[dimension]
                if query[dimension] < low
                else query[dimension] - high
                if query[dimension] > high
                else 0.0
            )
            lut[dimension, level] = delta * delta
    distances = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, distances))[: min(TOP, len(ids))]]


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = json.loads(raw.read_text(encoding="utf-8")).get("rows")
    if not isinstance(rows, list) or len(rows) != QUERY_COUNT:
        raise RuntimeError("candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    bound = json.loads(receipt.read_text(encoding="utf-8"))
    record_bytes = int(bound.get("flat_file", {}).get("record_bytes", 148))
    if record_bytes not in (100, 148):
        raise RuntimeError("candidate receipt has an unsupported record size")
    if flat.stat().st_size != int(offsets[-1]) * record_bytes:
        raise RuntimeError("candidate flat/raw cardinality differs")
    if (
        bound.get("execution_status") != "EXECUTED"
        or bound.get("raw_sha256") != sha256(raw)
        or bound.get("flat_file", {}).get("sha256") != sha256(flat)
    ):
        raise RuntimeError("candidate receipt binding differs")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), record_bytes))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate ID is outside the corpus")
    return ids, offsets


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    vector = np.asarray(query, dtype=np.float64)
    denominator = np.maximum(
        np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector), np.finfo(np.float64).tiny
    )
    return (matrix @ vector) / denominator


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def ndcg10(ids: np.ndarray, qrel_ids: np.ndarray, grades: np.ndarray) -> float:
    relevance = {
        int(document): float(grade)
        for document, grade in zip(qrel_ids, grades)
        if int(document) >= 0 and float(grade) > 0
    }
    gains = np.asarray([2.0 ** relevance.get(int(document), 0.0) - 1.0 for document in ids])
    ideal = np.sort(np.asarray([2.0**grade - 1.0 for grade in relevance.values()]))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    idcg = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / idcg) if idcg else 0.0


def parse_widths_for_self_test() -> tuple[int, ...]:
    return (2, 3, 4)


def deterministic_rotation(seed: int) -> np.ndarray:
    gaussian = np.random.default_rng(seed).standard_normal((D, D))
    orthogonal, upper = np.linalg.qr(gaussian)
    signs = np.where(np.diag(upper) < 0.0, -1.0, 1.0)
    orthogonal *= signs[None, :]
    return np.ascontiguousarray(orthogonal.T, dtype="<f4")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    source_names = (
        "documents",
        "queries",
        "qrel-ids",
        "qrel-scores",
        "teacher-ids",
        "thq4-codes",
        "thq4-thresholds",
        "candidate-flat",
        "candidate-raw",
        "candidate-receipt",
    )
    for name in source_names:
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--native-runner", type=Path)
    parser.add_argument("--rabitq-root", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--widths", default="2,3,4")
    parser.add_argument("--rotation-seed", type=int, default=20260925)
    args = parser.parse_args()
    if args.self_test:
        rotation = deterministic_rotation(20260925).astype(np.float64)
        if float(np.max(np.abs(rotation @ rotation.T - np.eye(D)))) >= 2e-6:
            raise RuntimeError("official RaBitQ rotation self-test failed")
        if set(parse_widths_for_self_test()) != {2, 3, 4}:
            raise RuntimeError("official RaBitQ width self-test failed")
        print("official multi-bit RaBitQ runner self-test: PASS")
        return
    for name in source_names + ("native_runner", "rabitq_root", "artifact_dir", "output"):
        if getattr(args, name.replace("-", "_")) is None:
            parser.error(f"--{name} is required")

    widths = tuple(int(value) for value in args.widths.split(",") if value.strip())
    if not widths or len(set(widths)) != len(widths) or any(value not in (2, 3, 4) for value in widths):
        raise RuntimeError("--widths must be a non-empty unique subset of 2,3,4")
    revision = subprocess.run(
        ["git", "-C", str(args.rabitq_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != RABITQ_REVISION:
        raise RuntimeError(f"RaBitQ source revision differs: {revision}")
    if not args.native_runner.is_file():
        raise RuntimeError("native official RaBitQ runner is missing")
    if args.artifact_dir.exists():
        stale = [
            path
            for path in args.artifact_dir.iterdir()
            if path.is_file() and path.suffix.lower() not in {".log"}
        ]
        if stale:
            raise RuntimeError("artifact directory contains stale non-log artifacts")
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    candidate_ids, candidate_offsets = load_candidates(
        args.candidate_flat, args.candidate_raw, args.candidate_receipt
    )
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    thq_codes = np.memmap(
        args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES)
    )
    selected_rows = []
    for query_index, query in enumerate(queries):
        begin, end = candidate_offsets[query_index : query_index + 2]
        selected_rows.append(
            interval_top(query, candidate_ids[begin:end], thq_codes, thresholds)
        )
    selected = np.stack(selected_rows).astype("<i8")
    if selected.shape != (QUERY_COUNT, TOP):
        raise RuntimeError("canonical THQ selection did not produce 128 rows per query")

    selected_path = args.artifact_dir / "selected-ids.i64"
    rotation_path = args.artifact_dir / "rotation-matrix.f32"
    native_metadata_path = args.artifact_dir / "native-metadata.json"
    selected.tofile(selected_path)
    deterministic_rotation(args.rotation_seed).tofile(rotation_path)
    subprocess.run(
        [
            str(args.native_runner),
            str(args.documents),
            str(args.queries),
            str(selected_path),
            str(rotation_path),
            ",".join(str(value) for value in widths),
            str(args.artifact_dir),
            str(native_metadata_path),
        ],
        check=True,
    )

    native = json.loads(native_metadata_path.read_text(encoding="utf-8"))
    if (
        native.get("dimensions") != D
        or native.get("query_count") != QUERY_COUNT
        or native.get("candidates_per_query") != TOP
        or native.get("unique_documents") != int(len(np.unique(selected)))
    ):
        raise RuntimeError("native RaBitQ metadata cardinality differs")
    native_widths = {int(row["bits"]): row for row in native.get("widths", [])}
    if set(native_widths) != set(widths):
        raise RuntimeError("native RaBitQ widths differ")

    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    rows: list[dict[str, object]] = []
    summaries: dict[str, dict[str, object]] = {}
    for bits in widths:
        score_path = args.artifact_dir / f"rabitq-b{bits}.compact-scores.f32"
        scores = np.fromfile(score_path, dtype="<f4").reshape(QUERY_COUNT, TOP)
        metrics, candidate_overlaps, teacher_overlaps = [], [], []
        for query_index, query in enumerate(queries):
            begin, end = candidate_offsets[query_index : query_index + 2]
            shell_ids = candidate_ids[begin:end]
            exact = top_ids(cosine(np.asarray(documents[shell_ids]), query), shell_ids)
            ranked = top_ids(scores[query_index], selected[query_index])
            metric = ndcg10(ranked, qrel_ids[query_index], qrel_scores[query_index])
            candidate_overlap = float(np.isin(exact, ranked).sum() / 10)
            teacher_overlap = float(np.isin(teacher_ids[query_index], ranked).sum() / 10)
            metrics.append(metric)
            candidate_overlaps.append(candidate_overlap)
            teacher_overlaps.append(teacher_overlap)
            rows.append(
                {
                    "query": query_index,
                    "arm": f"official_rabitq_b{bits}",
                    "top10_ids": ranked.astype(int).tolist(),
                    "thq4_top128_ids": selected[query_index].astype(int).tolist(),
                    "candidate_fp32_top10_ids": exact.astype(int).tolist(),
                    "qrels_ndcg10": metric,
                    "candidate_fp32_overlap": candidate_overlap,
                    "teacher_overlap": teacher_overlap,
                }
            )
        native_row = native_widths[bits]
        side_bytes = int(native_row["logical_side_bytes"])
        summaries[f"official_rabitq_b{bits}"] = {
            "mean_qrels_ndcg10": float(np.mean(metrics)),
            "p05_qrels_ndcg10": float(np.percentile(metrics, 5)),
            "worst_qrels_ndcg10": float(np.min(metrics)),
            "mean_candidate_fp32_overlap": float(np.mean(candidate_overlaps)),
            "mean_teacher_overlap": float(np.mean(teacher_overlaps)),
            "side_payload_bytes": side_bytes,
            "cascade_total_bytes": THQ_BYTES + side_bytes,
            "global_rotation_bytes": D * D * 4,
            "candidate_union_compact_encode_seconds": float(native_row["compact_encode_seconds"]),
            "candidate_union_compact_encode_docs_per_second": float(
                native["unique_documents"] / native_row["compact_encode_seconds"]
            ),
            "candidate_score_seconds": float(native_row["score_seconds"]),
            "full_compact_max_abs_score_difference": float(
                native_row["full_compact_max_abs_score_difference"]
            ),
        }

    artifact_paths = [
        path
        for path in sorted(args.artifact_dir.iterdir())
        if path.is_file() and path != args.output
    ]
    sources = {name: getattr(args, name.replace("-", "_")) for name in source_names}
    native_source_dir = Path(__file__).resolve().parent / "official-rabitq-runner"
    result = {
        "schema_version": 1,
        "family": "official_rabitq_multibit_thq_replay_v1",
        "status": "EXECUTED",
        "source_replay": True,
        "metric": "cosine_via_normalized_ip",
        "query_count": QUERY_COUNT,
        "candidate_count": TOP,
        "widths": list(widths),
        "rotation": {
            "type": "deterministic_gaussian_orthogonal_matrix",
            "seed": args.rotation_seed,
            "bytes": D * D * 4,
            "sha256": sha256(rotation_path),
        },
        "upstream": {
            "repository": "VectorDB-NTU/RaBitQ-Library",
            "revision": revision,
            "license": "Apache-2.0",
        },
        "native_build": {
            "compiler": native["compiler"],
            "build_type": native["build_type"],
            "native_optimization": native["native_optimization"],
            "runner_sha256": sha256(args.native_runner),
        },
        "implementation_hashes": {
            "producer": sha256(Path(__file__)),
            "native_source": sha256(native_source_dir / "main.cpp"),
            "native_cmake": sha256(native_source_dir / "CMakeLists.txt"),
        },
        "hardware": hardware_snapshot(),
        "native_threads": 1,
        "native_metadata": native,
        "source_hashes": {name: sha256(path) for name, path in sources.items()},
        "artifact_hashes": {path.name: sha256(path) for path in artifact_paths},
        "summaries": summaries,
        "rows": rows,
        "limitations": [
            "historical 152-query engineering fold, not untouched product-selection evidence",
            "candidate-union timing excludes source-file reads and normalization/rotation; those are recorded separately",
            "official split compact layout retains bound factors for incremental/error-bound use",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
