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

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--candidate-raw", type=Path)
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
    if args.candidate_flat and args.candidate_raw:
        raw_rows = json.loads(args.candidate_raw.read_text(encoding="utf-8"))["rows"]
        counts = [int(row["candidate_count"]) for row in raw_rows]
        offsets = [0]
        for count in counts:
            offsets.append(offsets[-1] + count)
        records = np.memmap(args.candidate_flat, mode="r", dtype="<i4",
                            shape=(offsets[-1], 37))
        for row in rows:
            shell = set(int(value) for value in records[offsets[row["query"]]:offsets[row["query"] + 1], 0])
            require(set(row["top10_ids"]).issubset(shell), "top10 ID escaped candidate shell")
    output = {"schema_version": 1, "family": "thq_learned_adc_gate_audit_v1", "status": "PASS",
              "result_sha256": sha(args.result), "runner_sha256": sha(args.runner),
              "candidate_receipt_sha256": sha(args.candidate_receipt),
              "checks": ["runner/result binding", "candidate provenance binding", "held-out split",
                         "rate-matched 2/4/8-bit arms", "row cardinality", "scope split",
                         "unique top10 IDs", "payload accounting", "direct ADC quality scope"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ learned ADC evidence audit PASS")

if __name__ == "__main__":
    main()
