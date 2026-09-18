#!/usr/bin/env python3
"""Fail-closed audit for the score-aware learned ADC gate."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int) -> np.ndarray:
    order = np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))
    return np.asarray(ids, dtype=np.int64)[order[:limit]]


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    shifts = np.asarray((0, 2, 4, 6), dtype=np.uint8)
    return ((np.asarray(codes, dtype=np.uint8)[:, :, None] >>
             shifts[None, None, :]) & 3).reshape(len(codes), 384)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--qrel-ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", type=Path, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--score-baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_learned_adc_gate_v1", "learned ADC family differs")
    require(result.get("status") == "EXECUTED", "learned ADC status differs")
    require(result.get("runner_sha256") == sha(args.runner), "learned ADC runner binding differs")
    require(result.get("candidate_receipt_sha256") == sha(args.candidate_receipt), "candidate receipt binding differs")
    require(candidate.get("family") == "semantic_r4_fused_candidate_materialization_v1",
            "candidate receipt family differs")
    require(candidate.get("execution_status") == "EXECUTED", "candidate receipt is not executed")
    require(result.get("query_count") == 152, "canonical query count differs")
    require(result.get("train_query_count") == 120, "held-out split differs")
    arms = set(result.get("summaries", {}))
    expected = {f"learned-adc-{budget}B-{bits}bit{suffix}"
                for budget in (8, 16, 32) for bits in (2, 4, 8)
                for suffix in ("", "+norm2")}
    require(arms == expected, "learned ADC arm set differs")
    rows = result.get("rows", [])
    require(len(rows) == 152 * len(expected) * 2, "learned ADC row cardinality differs")
    require(all(row.get("timing_scope") == "numpy_reference_direct_adc_quality_only" for row in rows),
            "learned ADC timing scope differs")
    require(all(len(row.get("top10_ids", [])) == 10 for row in rows), "learned ADC top10 cardinality differs")
    require({row.get("scope") for row in rows} == {"full-shell", "thq4-top128"},
            "learned ADC scope set differs")
    keys = {(row.get("query"), row.get("arm"), row.get("scope")) for row in rows}
    require(len(keys) == len(rows), "learned ADC rows are not unique")
    require(all(len(set(row["top10_ids"])) == 10 for row in rows), "learned ADC top10 IDs duplicated")
    require(all(row.get("total_payload_bytes") == row.get("side_payload_bytes") + 96 +
                (2 if row["arm"].endswith("+norm2") else 0) for row in rows),
            "learned ADC payload accounting differs")
    indexed = {(row["query"], row["arm"], row["scope"]): row for row in rows}
    for arm in expected:
        if not arm.endswith("+norm2"):
            require(all(indexed[q, arm, "full-shell"]["top10_ids"] ==
                        indexed[q, arm, "thq4-top128"]["top10_ids"] for q in range(152)),
                    f"stage-local top10 differs for {arm}")
    scopes = result.get("summaries_by_scope", {})
    require(set(scopes) == {"full-shell", "thq4-top128"}, "scope summaries missing")
    for scope in scopes:
        for arm in expected:
            scoped = [row for row in rows if row["scope"] == scope and row["arm"] == arm]
            require(len(scoped) == 152, "scope summary row count differs")
            recorded = scopes[scope][arm]["all"]
            for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap", "pairwise_order",
                           "pairwise_top32", "pairwise_top10_boundary"):
                require(abs(float(recorded[metric]) - float(np.mean([row[metric] for row in scoped]))) < 1e-6,
                        f"summary mismatch for {scope}/{arm}/{metric}")
    raw_rows = json.loads(args.candidate_raw.read_text(encoding="utf-8"))["rows"]
    counts = [int(row["candidate_count"]) for row in raw_rows]
    offsets = [0]
    for count in counts:
        offsets.append(offsets[-1] + count)
    require(len(counts) == result["query_count"], "candidate query count differs")
    require(args.candidate_flat.stat().st_size == offsets[-1] * 148,
            "candidate flat byte count differs")
    records = np.memmap(args.candidate_flat, mode="r", dtype="<i4",
                        shape=(offsets[-1], 37))
    document_count = args.documents.stat().st_size // (4 * 384)
    query_count = args.queries.stat().st_size // (4 * 384)
    require(query_count == result["query_count"], "query source count differs")
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, 384))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(384, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, 384))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    source_hashes = {
        "documents_sha256": sha(args.documents), "training_sha256": sha(args.training),
        "thq4_codes_sha256": sha(args.thq4_codes), "thq4_thresholds_sha256": sha(args.thq4_thresholds),
        "queries_sha256": sha(args.queries),
        "qrel_ids_sha256": sha(args.qrel_ids), "qrel_scores_sha256": sha(args.qrel_scores),
        "teacher_ids_sha256": sha(args.teacher_ids), "candidate_flat_sha256": sha(args.candidate_flat),
        "candidate_raw_sha256": sha(args.candidate_raw), "candidate_receipt_sha256": sha(args.candidate_receipt),
    }
    for field, value in source_hashes.items():
        require(result.get(field) == value, f"learned ADC source binding differs: {field}")
    for query in range(query_count):
        shell = np.asarray(records[offsets[query]:offsets[query + 1], 0], dtype=np.int64)
        require(np.all((shell >= 0) & (shell < document_count)), "candidate ID out of range")
        require(len(np.unique(shell)) == len(shell), "candidate shell contains duplicate IDs")
        exact_top = top_ids(documents[shell] @ queries[query], shell, 10)
        levels = unpack_thq(np.asarray(thq_codes[shell]))
        interval_lut = np.empty((384, 4), dtype=np.float32)
        for coordinate in range(384):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - queries[query, coordinate] if queries[query, coordinate] < low else (
                    queries[query, coordinate] - high if queries[query, coordinate] > high else 0.0)
                interval_lut[coordinate, level] = delta * delta
        interval = np.sum(interval_lut[np.arange(384)[None, :], levels], axis=1)
        thq_top = shell[np.lexsort((shell, interval))[:min(128, len(shell))]]
        thq_top_sha = hashlib.sha256(np.asarray(thq_top, dtype="<u4").tobytes()).hexdigest()
        for row in rows:
            if int(row["query"]) != query:
                continue
            selected = np.asarray(row["top10_ids"], dtype=np.int64)
            require(np.all(np.isin(selected, shell)), "top10 ID escaped candidate shell")
            require(int(row.get("thq4_top128_count", -1)) == min(128, len(shell)),
                    f"THQ4 top128 count differs: {row['arm']}/{row['scope']}/{query}")
            require(row.get("thq4_top128_sequence_sha256") == thq_top_sha,
                    f"independent THQ4 top128 sequence differs: {row['arm']}/{row['scope']}/{query}")
            expected_metrics = {
                "qrels_ndcg10": ndcg(selected, qrel_ids[query], qrel_scores[query]),
                "teacher_overlap": float(np.isin(teacher_ids[query], selected).sum() / 10.0),
                "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
            }
            for metric, expected_value in expected_metrics.items():
                require(abs(float(row[metric]) - expected_value) < 1e-12,
                        f"independent metric differs: {row['arm']}/{row['scope']}/{query}/{metric}")
    paired_baseline = None
    if args.score_baseline:
        baseline = json.loads(args.score_baseline.read_text(encoding="utf-8"))
        require(baseline.get("family") == "thq_score_only_codec_gate_v1",
                "score baseline family differs")
        require(baseline.get("status") == "EXECUTED", "score baseline status differs")
        for field in ("candidate_receipt_sha256", "documents_sha256", "queries_sha256",
                      "qrel_ids_sha256", "qrel_scores_sha256", "teacher_ids_sha256"):
            require(baseline.get(field) == result.get(field),
                    f"score baseline input differs: {field}")
        baseline_rows = {(int(row["query"]), row["arm"]): row for row in baseline["rows"]
                         if row.get("scope", "full-shell") == "full-shell"}
        paired_baseline = {}
        rng = np.random.default_rng(20260918)
        for arm in sorted(result["summaries"]):
            heldout = [row for row in rows if row["arm"] == arm and
                       row["scope"] == "thq4-top128" and int(row["query"]) >= result["train_query_count"]]
            paired_baseline[arm] = {}
            for control in ("candidate-fp32", "direct-int8", "rslm3-direct-score"):
                delta = np.asarray([row["qrels_ndcg10"] - baseline_rows[(int(row["query"]), control)]["qrels_ndcg10"]
                                    for row in heldout], dtype=float)
                bootstrap = delta[rng.integers(0, len(delta), size=(5000, len(delta)))].mean(axis=1)
                paired_baseline[arm][control] = {
                    "mean_delta": float(delta.mean()),
                    "bootstrap_ci95": [float(np.quantile(bootstrap, .025)),
                                        float(np.quantile(bootstrap, .975))],
                    "worst_query_delta": float(delta.min())}

    output = {"schema_version": 1, "family": "thq_learned_adc_gate_audit_v1", "status": "PASS",
              "result_sha256": sha(args.result), "runner_sha256": sha(args.runner),
              "candidate_receipt_sha256": sha(args.candidate_receipt),
              "training_sha256": sha(args.training),
              "thq4_codes_sha256": sha(args.thq4_codes),
              "thq4_thresholds_sha256": sha(args.thq4_thresholds),
              "score_baseline_sha256": sha(args.score_baseline) if args.score_baseline else None,
              "paired_scope": "thq4-top128" if paired_baseline is not None else None,
              "paired_qrels_ndcg10": paired_baseline,
              "checks": ["runner/result binding", "candidate provenance binding", "source SHA bindings",
                         "independent qrels nDCG, teacher overlap and candidate-FP32 overlap",
                         "held-out split",
                         "rate-matched 2/4/8-bit arms", "row cardinality", "scope split",
                         "unique top10 IDs", "payload accounting", "direct ADC quality scope"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ learned ADC evidence audit PASS")

if __name__ == "__main__":
    main()
