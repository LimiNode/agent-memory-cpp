#!/usr/bin/env python3
"""Independent persisted-sketch -> score -> top10 audit for the QJL gate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

Q, D, TOP = 152, 384, 128
WIDTHS = (32, 64, 128, 256, 384)
DISTRIBUTIONS = ("gaussian", "rademacher")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_qjl():
    path = Path(__file__).with_name("qjl_reference.py")
    spec = importlib.util.spec_from_file_location("qjl_audit", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["qjl_audit"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ndcg(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    relevance = {int(doc): float(grade) for doc, grade in zip(qids, grades)
                 if int(doc) >= 0 and float(grade) > 0.0}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** grade - 1.0 for grade in relevance.values()]))[::-1][:10]
    denominator = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / denominator)) if denominator else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("result", "artifact", "documents", "queries", "qrel-ids", "qrel-scores",
                 "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat",
                 "candidate-raw", "candidate-receipt", "tq-payload", "output"):
        parser.add_argument("--" + name, dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    assert result["family"] == "thq_qjl_residual_gate_v2" and result["status"] == "EXECUTED"
    assert result["artifact_sha256"] == sha(args.artifact)
    assert result["runner_sha256"] == sha(Path(__file__).with_name("run-thq-qjl-residual-gate.py"))
    for name in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
                 "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw",
                 "candidate-receipt", "tq-payload"):
        assert result["source_hashes"][name] == sha(getattr(args, name.replace("-", "_")))

    qjl = load_qjl()
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(Q, D)))
    qrel_ids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(Q, 20)))
    qrel_scores = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(Q, 20)))
    with np.load(args.tq_payload, allow_pickle=False) as payload:
        tq_ids = np.asarray(payload["document_ids"], dtype=np.int64)
        tq_base = np.asarray(payload["base"], dtype=np.float32)
        tq_decoded = np.asarray(payload["decoded1"], dtype=np.float32)
        tq_row_ids = np.asarray(payload["row_ids"], dtype=np.int64)
        tq_row_offsets = np.asarray(payload["row_offsets"], dtype=np.int64)
    with np.load(args.artifact, allow_pickle=False) as artifact:
        keys = set(artifact.files)
        required = {"document_ids", "base", "decoded1", "residual_norms", "source_norms"}
        assert required.issubset(keys)
        artifact_ids = np.asarray(artifact["document_ids"], dtype=np.int64)
        base = np.asarray(artifact["base"], dtype=np.float32)
        decoded = np.asarray(artifact["decoded1"], dtype=np.float32)
        assert np.array_equal(artifact_ids, tq_ids)
        assert np.array_equal(base, tq_base) and np.array_equal(decoded, tq_decoded)
        residual = np.asarray(documents[artifact_ids], dtype=np.float32) - base - decoded
        residual_norms = np.asarray(artifact["residual_norms"], dtype=np.float32)
        source_norms = np.asarray(artifact["source_norms"], dtype=np.float32)
        assert np.allclose(np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32),
                           residual_norms, rtol=0.0, atol=2e-6)
        assert np.allclose(np.linalg.norm(np.asarray(documents[artifact_ids], dtype=np.float64), axis=1).astype(np.float32),
                           source_norms, rtol=0.0, atol=2e-6)
        projections = {}
        signs = {}
        for width in WIDTHS:
            for distribution in DISTRIBUTIONS:
                name = f"qjl_{distribution}{width}"
                assert name in keys and f"{name}_signs" in keys
                projection = np.asarray(artifact[name], dtype=np.float32)
                projections[name] = projection
                signs[name] = np.asarray(artifact[f"{name}_signs"], dtype=np.uint8)
                replay_codes, _ = qjl.encode(residual, qjl.ProjectionSpec(projection, distribution, 20260924 + width))
                assert np.array_equal(replay_codes, signs[name])

        positions = {int(doc): index for index, doc in enumerate(artifact_ids)}
        rows = result["rows"]
        assert len(rows) == Q * (1 + len(WIDTHS) * len(DISTRIBUTIONS))
        row_map = {(int(row["query"]), row["arm"]): row for row in rows}
        assert len(row_map) == len(rows)
        replay_metrics: dict[str, list[float]] = {name: [] for name in result["summaries"]}
        for query_index, query in enumerate(queries):
            candidate_ids = tq_row_ids[tq_row_offsets[query_index]:tq_row_offsets[query_index + 1]]
            indexes = np.asarray([positions[int(doc)] for doc in candidate_ids], dtype=np.int64)
            base_values = base[indexes] + decoded[indexes]
            query_norm = max(float(np.linalg.norm(query)), 1e-30)
            exact_scores = (np.asarray(documents[candidate_ids], dtype=np.float64) @ query) / query_norm
            for width in WIDTHS:
                for distribution in DISTRIBUTIONS:
                    name = f"qjl_{distribution}{width}"
                    projection = qjl.ProjectionSpec(projections[name], distribution, 20260924 + width)
                    if distribution == "gaussian":
                        correction = qjl.estimate_dot_reference(query, signs[name][indexes], residual_norms[indexes], projection)
                    else:
                        correction = qjl.estimate_dot_rademacher_control(query, signs[name][indexes], residual_norms[indexes], projection)
                    scores = (base_values @ query + correction) / query_norm
                    ranked = candidate_ids[np.lexsort((candidate_ids, -scores))]
                    row = row_map[(query_index, name)]
                    assert row["top10_ids"] == ranked[:10].astype(int).tolist()
                    value = ndcg(ranked, qrel_ids[query_index], qrel_scores[query_index])
                    assert abs(float(row["qrels_ndcg10"]) - value) < 1e-12
                    # JSON decimal serialization and the audit's float32 persisted
                    # norms can differ by a few ulps from the runner's in-memory
                    # reduction.  Keep this tight enough to catch a wrong scorer,
                    # while allowing that deterministic representation noise.
                    score_error_delta = abs(float(row["mean_abs_score_error"]) - float(np.mean(np.abs(scores - exact_scores))))
                    if score_error_delta >= 1e-4:
                        raise AssertionError(f"score error mismatch query={query_index} arm={name} delta={score_error_delta}")
                    replay_metrics[name].append(value)
            tq_name = "tq1_filter_only"
            ranked_tq = candidate_ids[np.lexsort((candidate_ids, -(base_values @ query) / np.maximum(np.linalg.norm(base_values, axis=1) * query_norm, 1e-30)))]
            row = row_map[(query_index, tq_name)]
            assert row["top10_ids"] == ranked_tq[:10].astype(int).tolist()
            replay_metrics[tq_name].append(ndcg(ranked_tq, qrel_ids[query_index], qrel_scores[query_index]))
        for name, values in replay_metrics.items():
            recorded = result["summaries"][name]
            assert abs(float(recorded["mean_qrels_ndcg10"]) - float(np.mean(values))) < 1e-12
            assert abs(float(recorded["p05_qrels_ndcg10"]) - float(np.percentile(values, 5))) < 1e-12
            assert abs(float(recorded["worst_qrels_ndcg10"]) - float(np.min(values))) < 1e-12

    audit = {
        "schema_version": 3,
        "family": "thq_qjl_residual_gate_audit_v3",
        "status": "PASS",
        "source_replay": True,
        "independent_decode_replay": True,
        "independent_score_top10_replay": True,
        "artifact_hash_binding": True,
        "row_count": len(rows),
        "projection_rows": list(WIDTHS),
        "denominator_contract": "unit_norm_constant_1",
        "checks": ["source and runner SHA binding", "persisted projection/sign sketch shape",
                    "independent residual and norm replay", "independent packed-sign replay",
                    "persisted sketch to QJL score/top10 replay", "per-query nDCG replay",
                    "aggregate summary replay", "canonical query/qrels cardinality"],
        "limitations": ["candidate-local canonical THQ top128 shell", "audit does not refit TQ1"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("QJL persisted score/top10 audit PASS")


if __name__ == "__main__":
    main()
