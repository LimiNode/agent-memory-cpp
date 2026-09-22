#!/usr/bin/env python3
"""Independent persisted-code replay for the clustered Elastic BBQ control."""
from __future__ import annotations

import argparse
import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SPEC = spec_from_file_location("bbq_flat_audit", HERE / "audit-thq-elastic-bbq-reference.py")
BBQ = module_from_spec(SPEC); assert SPEC and SPEC.loader; SPEC.loader.exec_module(BBQ)
D, QUERY_COUNT = BBQ.D, BBQ.QUERY_COUNT


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok: raise RuntimeError(message)


def unit(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32); norms = np.linalg.norm(values.astype(np.float64), axis=1)
    return (values / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("result", "runner", "artifact", "documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = p.parse_args(); result = json.loads(args.result.read_text(encoding="utf-8")); rows = result.get("rows", [])
    require(result.get("schema_version") == 1 and result.get("family") == "thq_elastic_current_clustered_bbq_v1" and result.get("source_replay") is True, "result identity differs")
    require(result.get("runner_sha256") == sha256(args.runner) and result.get("artifact_sha256") == sha256(args.artifact), "runner or artifact binding differs")
    source_paths = {name.replace("_", "-"): getattr(args, name) for name in ("documents", "queries", "qrel_ids", "qrel_scores", "teacher_ids", "thq4_codes", "thq4_thresholds", "candidate_flat", "candidate_raw", "candidate_receipt")}
    require(all(result["source_hashes"].get(name) == sha256(path) for name, path in source_paths.items()), "source binding differs")
    require(len(rows) == QUERY_COUNT and sorted(int(r["query"]) for r in rows) == list(range(QUERY_COUNT)), "query identity differs")
    with np.load(args.artifact, allow_pickle=False) as pld:
        document_ids = np.asarray(pld["document_ids"], np.int64); rotation = np.asarray(pld["rotation"], np.float32); fine = np.asarray(pld["fine"], np.float32); coarse_ids = np.asarray(pld["coarse_ids"], np.int32); fine_ids = np.asarray(pld["fine_ids"], np.int32); codes = np.asarray(pld["codes"], np.uint8); lows = np.asarray(pld["lows"], np.float32); highs = np.asarray(pld["highs"], np.float32); corrections = np.asarray(pld["corrections"], np.float32); row_ids = np.asarray(pld["row_ids"], np.int64); offsets = np.asarray(pld["row_offsets"], np.int64)
    require(rotation.shape == (D, D) and np.max(np.abs(rotation.T @ rotation - np.eye(D))) < 5e-5, "preconditioner is not orthogonal")
    require(fine.shape == (64, 41, D) and codes.shape == (len(document_ids), D) and offsets.shape == (QUERY_COUNT + 1,), "payload shapes differ")
    require(np.allclose(np.linalg.norm(fine, axis=2), 1.0, atol=3e-5), "local centroid norms differ")
    positions = {int(doc): i for i, doc in enumerate(document_ids)}; queries = unit(np.memmap(args.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)))
    mismatch = 0
    for row in rows:
        qi = int(row["query"]); ids = np.asarray(row["thq4_top128_ids"], np.int64); require(np.array_equal(ids, row_ids[offsets[qi]:offsets[qi + 1]]), f"row IDs differ at query {qi}")
        indexes = np.asarray([positions[int(doc)] for doc in ids]); query = queries[qi] @ rotation; scores = np.empty(len(ids), np.float64)
        for key in np.unique(np.stack((coarse_ids[indexes], fine_ids[indexes]), axis=1), axis=0):
            mask = (coarse_ids[indexes] == key[0]) & (fine_ids[indexes] == key[1]); local = indexes[mask]; centroid = fine[key[0], key[1]]; qfit = BBQ.query_quantize(query, centroid)
            scores[mask] = BBQ.mixed_score(qfit, codes[local].astype(np.float64), lows[local].astype(np.float64), highs[local].astype(np.float64), corrections[local].astype(np.float64), centroid)
        mismatch += int(not np.array_equal(top_ids(scores, ids), np.asarray(row["top10_ids"], np.int64)))
    require(mismatch == 0, f"independent clustered BBQ top10 mismatches: {mismatch}")
    audit = {"schema_version": 1, "family": "thq_elastic_current_clustered_bbq_audit_v1", "status": "PASS", "source_binding": True, "independent_decode_replay": True, "result_sha256": sha256(args.result), "runner_sha256": sha256(args.runner), "artifact_sha256": sha256(args.artifact), "row_count": len(rows), "ordered_top10_mismatch_count": mismatch, "checks": ["runner/artifact/source SHA binding", "exact query identity", "preconditioner orthogonality", "local-centroid and payload shape contracts", "independent 1-bit/4-bit corrective-score replay"], "limitations": ["Faiss hierarchical fit is bound, not independently retrained", "candidate-local scorer; no IVF serving claim"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("clustered Elastic BBQ audit PASS")


if __name__ == "__main__": main()
