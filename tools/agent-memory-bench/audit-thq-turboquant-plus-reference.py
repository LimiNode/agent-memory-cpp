#!/usr/bin/env python3
"""Independent persisted decode audit for the bounded TurboQuant+ control."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from importlib.util import module_from_spec, spec_from_file_location

_normal_path = Path(__file__).with_name("run-thq-turboquant-reference.py")
_spec = spec_from_file_location("tq_normal_audit", _normal_path)
_normal = module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_normal)

D, QUERY_COUNT = _normal.D, _normal.QUERY_COUNT
REVISION = _normal.UPSTREAM_REVISION


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def unit_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(rows.astype(np.float64), axis=1)
    return (rows / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    return _normal.ndcg10(ids, qids, grades)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", dest="train_vectors", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--qrel-ids", dest="qrel_ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", dest="qrel_scores", type=Path, required=True)
    parser.add_argument("--teacher-ids", dest="teacher_ids", type=Path, required=True)
    parser.add_argument("--thq4-codes", dest="thq4_codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", dest="thq4_thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", dest="candidate_flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", dest="candidate_raw", type=Path, required=True)
    parser.add_argument("--candidate-receipt", dest="candidate_receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    rows = result.get("rows", [])
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True and result.get("schema_version") == 1, "result is not the TurboQuant+ source replay")
    require(result.get("upstream_revision") == REVISION and result.get("bits") == 1, "TurboQuant+ source or bit contract differs")
    require(len(rows) == 304 and all(int(row.get("side_payload_bytes")) == 56 for row in rows), "TurboQuant+ row or payload accounting differs")
    require(result.get("runner_sha256") == sha256(args.runner), "result runner binding differs")
    expected_sources = {"documents": args.documents, "train-vectors": args.train_vectors, "queries": args.queries, "qrel-ids": args.qrel_ids, "qrel-scores": args.qrel_scores, "teacher-ids": args.teacher_ids, "thq4-codes": args.thq4_codes, "thq4-thresholds": args.thq4_thresholds, "candidate-flat": args.candidate_flat, "candidate-raw": args.candidate_raw, "candidate-receipt": args.candidate_receipt}
    require(result.get("artifact_sha256") == sha256(args.artifact) and all(result.get("source_hashes", {}).get(name) == sha256(path) for name, path in expected_sources.items()), "artifact or source binding differs")
    with np.load(args.artifact, allow_pickle=False) as payload:
        document_ids = np.asarray(payload["document_ids"], dtype=np.int64)
        base = np.asarray(payload["base"], dtype=np.float32)
        codes = np.asarray(payload["codes"], dtype=np.uint8)
        lengths = np.asarray(payload["lengths"], dtype=np.float32)
        ec = np.asarray(payload["ec_correction"], dtype=np.float32)
        shift = np.asarray(payload["shift"], dtype=np.float32)
        scale = np.asarray(payload["scale"], dtype=np.float32)
        row_ids = np.asarray(payload["row_ids"], dtype=np.int64)
        row_offsets = np.asarray(payload["row_offsets"], dtype=np.int64)
    require(base.shape == (len(document_ids), D) and codes.shape == base.shape, "persisted TurboQuant+ shapes differ")
    require(lengths.shape == ec.shape == (len(document_ids),) and shift.shape == scale.shape == (D,), "persisted correction shapes differ")
    require(np.isfinite(shift).all() and np.isfinite(scale).all() and np.all(scale > 0), "invalid shift/scale")
    require(row_offsets.shape == (QUERY_COUNT + 1,) and row_offsets[-1] == len(row_ids), "persisted row offsets differ")
    positions = {int(doc): i for i, doc in enumerate(document_ids)}
    queries = unit_rows(np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)), dtype=np.float32))
    transformed = ((2.0 * codes.astype(np.float32) - 1.0) / scale[None, :]) - shift[None, :]
    decoded_residual = _normal.inverse_rotate(transformed * (lengths / np.sqrt(float(D)))[:, None])
    mismatches = {"turboquant_plus1_direct": 0, "turboquant_plus1_asymmetric": 0}
    for row in rows:
        qi = int(row["query"]); ids = np.asarray(row["thq4_top128_ids"], dtype=np.int64); persisted = row_ids[row_offsets[qi]:row_offsets[qi + 1]]
        require(np.array_equal(ids, persisted), f"persisted row IDs differ at query {qi}")
        indexes = np.asarray([positions[int(doc)] for doc in ids]); query = queries[qi]
        if row["arm"] == "turboquant_plus1_direct":
            values = base[indexes] + decoded_residual[indexes]
            scores = (values @ query) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(query), np.finfo(np.float64).tiny)
        else:
            rotated_q = _normal.rotate(query[None, :])[0]; q_plus = rotated_q / scale; qm = float(np.dot(rotated_q, -shift))
            residual_scores = (codes[indexes] * 2.0 - 1.0) @ q_plus + qm
            scores = (base[indexes] @ query) + residual_scores * (lengths[indexes] / np.sqrt(float(D)))
        ranked = top_ids(scores, ids, 10)
        mismatches[row["arm"]] += int(not np.array_equal(ranked, np.asarray(row["top10_ids"], dtype=np.int64)))
    require(all(value == 0 for value in mismatches.values()), f"independent TurboQuant+ top10 mismatches: {mismatches}")
    audit = {"schema_version": 1, "family": "thq_turboquant_plus_reference_audit_v1", "status": "PASS", "source_binding": True, "independent_decode_replay": True, "result_sha256": sha256(args.result), "runner_sha256": sha256(args.runner), "artifact_sha256": sha256(args.artifact), "query_sha256": sha256(args.queries), "row_count": len(rows), "independent_decode_top10_mismatch_count": mismatches, "checks": ["Qdrant revision binding", "all canonical source SHA-256 bindings", "persisted shift/scale validation", "independent inverse-rotation decode", "direct and asymmetric top10 parity", "56-byte payload accounting"], "limitations": ["bounded algebraic control, not Qdrant wire compatibility", "no QJL/native SIMD", "candidate-local THQ top128 replay"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("TurboQuant+ reference audit PASS")


if __name__ == "__main__":
    main()
