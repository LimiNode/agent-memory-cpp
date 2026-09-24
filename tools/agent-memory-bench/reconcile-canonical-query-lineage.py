#!/usr/bin/env python3
"""Bind the historical 152-query arrays to the recovered canonical 305-query source.

The historical query vectors have no embedded IDs.  This tool therefore requires
the persisted query-mapping artifact used by the original materializer and
proves, rather than guesses, the ordered 305 -> 152 relationship by bytewise
vector equality and qrels replay.  It deliberately does not classify the
153-row complement as an untouched holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


D, LEGACY_Q, CANONICAL_Q = 384, 152, 305


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_ids(path: Path) -> list[str]:
    values = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(len(values) == len(set(values)), f"duplicate IDs in {path}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    canonical = args.canonical_root
    legacy = args.legacy_root
    canonical_queries_path = canonical / "evaluation-query-vectors.f32"
    canonical_query_ids_path = canonical / "evaluation-query-ids.jsonl"
    canonical_document_ids_path = canonical / "evaluation-document-ids.jsonl"
    canonical_qrels_path = canonical / "evaluation-qrels.tsv"
    legacy_queries_path = legacy / "eval_queries.bin"
    legacy_qrel_ids_path = legacy / "eval_qrel_ids.bin"
    legacy_qrel_scores_path = legacy / "eval_qrel_scores.bin"
    legacy_teacher_path = legacy / "eval_teacher_ids.bin"
    mapping_path = legacy / "query-mapping.i32"
    manifest_path = canonical / "manifest.json"
    for path in (canonical_queries_path, canonical_query_ids_path, canonical_document_ids_path,
                 canonical_qrels_path, legacy_queries_path, legacy_qrel_ids_path,
                 legacy_qrel_scores_path, legacy_teacher_path, mapping_path, manifest_path):
        require(path.is_file(), f"missing lineage input: {path}")

    canonical_queries = np.memmap(canonical_queries_path, mode="r", dtype="<f4", shape=(CANONICAL_Q, D))
    legacy_queries = np.memmap(legacy_queries_path, mode="r", dtype="<f4", shape=(LEGACY_Q, D))
    mapping = np.fromfile(mapping_path, dtype="<i4")
    require(mapping.shape == (LEGACY_Q,), "legacy query mapping must contain exactly 152 rows")
    require(np.all((mapping >= 0) & (mapping < CANONICAL_Q)), "query mapping contains out-of-range rows")
    require(len(set(mapping.tolist())) == LEGACY_Q, "query mapping must be injective")
    max_abs_error = 0.0
    duplicate_candidates: dict[str, list[int]] = {}
    for legacy_row, canonical_row in enumerate(mapping):
        legacy_bytes = np.asarray(legacy_queries[legacy_row]).view(np.uint8)
        exact = np.asarray([
            index for index in range(CANONICAL_Q)
            if np.array_equal(np.asarray(canonical_queries[index]).view(np.uint8), legacy_bytes)
        ], dtype=np.int64)
        require(len(exact) > 0 and int(canonical_row) in set(exact.tolist()),
                f"query vector mismatch at legacy row {legacy_row}")
        if len(exact) > 1:
            duplicate_candidates[str(legacy_row)] = [int(value) for value in exact]
        max_abs_error = max(max_abs_error, float(np.max(np.abs(
            canonical_queries[int(canonical_row)] - legacy_queries[legacy_row]))))

    query_ids = load_ids(canonical_query_ids_path)
    document_ids = load_ids(canonical_document_ids_path)
    require(len(query_ids) == CANONICAL_Q, "canonical query ID count differs")
    require(len(document_ids) == 1_000_000, "canonical document ID count differs")
    query_id_set = set(query_ids)
    document_id_set = set(document_ids)
    qrel_ids = np.fromfile(legacy_qrel_ids_path, dtype="<i8").reshape(LEGACY_Q, 20)
    qrel_scores = np.fromfile(legacy_qrel_scores_path, dtype="<f4").reshape(LEGACY_Q, 20)
    teacher_ids = np.fromfile(legacy_teacher_path, dtype="<i8").reshape(LEGACY_Q, 10)
    require(np.all((teacher_ids >= 0) & (teacher_ids < len(document_ids))),
            "legacy teacher IDs contain out-of-range document rows")
    qrels: dict[str, dict[str, float]] = {}
    qrel_row_count = 0
    for line in canonical_qrels_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        require(len(parts) == 4 and parts[1] == "Q0", f"invalid qrels row: {line!r}")
        query_id, document_id, grade = parts[0], parts[2], float(parts[3])
        require(query_id in query_id_set, f"qrels query ID is unknown: {query_id}")
        require(document_id in document_id_set, f"qrels document ID is unknown: {document_id}")
        require(np.isfinite(grade) and grade >= 0.0, f"invalid qrels grade: {line!r}")
        bucket = qrels.setdefault(query_id, {})
        require(document_id not in bucket, f"duplicate qrels pair: {query_id}/{document_id}")
        bucket[document_id] = grade
        qrel_row_count += 1
    require(qrel_row_count == 3144, f"canonical qrels row count differs: {qrel_row_count}")

    matched_qrels = 0
    legacy_pairs: set[tuple[str, str, float]] = set()
    for legacy_row, canonical_row in enumerate(mapping):
        bucket = qrels.get(query_ids[int(canonical_row)], {})
        for document_row, grade in zip(qrel_ids[legacy_row], qrel_scores[legacy_row]):
            if int(document_row) < 0:
                continue
            require(int(document_row) < len(document_ids), "legacy qrels document row is out of range")
            actual = bucket.get(document_ids[int(document_row)])
            require(actual is not None and np.isclose(actual, float(grade), rtol=0.0, atol=1e-6),
                    f"qrels mismatch at legacy row {legacy_row}, document {int(document_row)}")
            matched_qrels += 1
            legacy_pairs.add((query_ids[int(canonical_row)], document_ids[int(document_row)], float(grade)))
    canonical_mapped_pairs = {
        (query_ids[int(canonical_row)], document_id, float(grade))
        for canonical_row in mapping
        for document_id, grade in qrels.get(query_ids[int(canonical_row)], {}).items()
    }
    require(legacy_pairs == canonical_mapped_pairs,
            "legacy qrels are not equal to the complete canonical mapped qrels set")

    complement = sorted(set(range(CANONICAL_Q)) - set(int(value) for value in mapping))
    mapped_ids = [query_ids[int(value)] for value in mapping]
    complement_ids = [query_ids[value] for value in complement]
    receipt = {
        "schema_version": 1,
        "family": "canonical_query_lineage_305_to_152_v1",
        "status": "PASS",
        "selection_status": "historical_mapping_reproduced; complement_not_classified_as_blind_holdout",
        "canonical": {
            "manifest_sha256": sha256(manifest_path),
            "query_vectors_sha256": sha256(canonical_queries_path),
            "query_ids_sha256": sha256(canonical_query_ids_path),
            "document_ids_sha256": sha256(canonical_document_ids_path),
            "qrels_sha256": sha256(canonical_qrels_path),
            "query_count": CANONICAL_Q,
            "document_count": len(document_ids),
            "qrels_row_count": qrel_row_count,
        },
        "legacy": {
            "query_vectors_sha256": sha256(legacy_queries_path),
            "qrel_ids_sha256": sha256(legacy_qrel_ids_path),
            "qrel_scores_sha256": sha256(legacy_qrel_scores_path),
            "teacher_ids_sha256": sha256(legacy_teacher_path),
            "mapping_sha256": sha256(mapping_path),
            "query_count": LEGACY_Q,
        },
        "mapping": {
            "ordered_canonical_row_by_legacy_row": [int(value) for value in mapping],
            "mapping_sha256": hashlib.sha256(mapping.astype("<i4", copy=False).tobytes()).hexdigest(),
            "mapped_query_ids_sha256": canonical_json_sha256(mapped_ids),
            "complement_canonical_rows": complement,
            "complement_query_ids_sha256": canonical_json_sha256(complement_ids),
            "vector_equality": {
                "comparison": "float32 row bytes / exact byte equality",
                "max_abs_error": max_abs_error,
                "duplicate_vector_candidates": duplicate_candidates,
            },
            "qrels": {"matched_pairs": matched_qrels, "legacy_pair_count": len(legacy_pairs),
                      "canonical_mapped_pair_count": len(canonical_mapped_pairs), "set_equality": True,
                      "comparison": "document ID and grade exact within 1e-6"},
        },
        "limitations": [
            "The persisted mapping proves historical row identity, not how the original 152 rows were selected.",
            "The 153-row complement is not an untouched IID holdout until selection lineage is established.",
            "Teacher IDs are range-checked but are not independently regenerated by this receipt.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
