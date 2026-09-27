#!/usr/bin/env python3
"""Independent audit for candidate-local packed TQ1/PQ8 native scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def validate_score_replay(score_replay: dict, native_jsonl: Path,
                          payload: Path, queries: Path, thq: Path) -> None:
    rows = load_jsonl(native_jsonl)
    require(score_replay.get("schema_version") == 1 and
            score_replay.get("status") == "PASS",
            "independent packed-score replay did not pass")
    require(score_replay.get("rows") == len(rows),
            "score replay row count differs")
    expected_scores = sum(len(row["thq4_top128_ids"]) for row in rows)
    require(score_replay.get("scores") == expected_scores and expected_scores == 19456,
            "score replay count does not match the frozen 19,456 candidates")
    tolerance = score_replay.get("score_tolerance")
    require(isinstance(tolerance, (int, float)) and tolerance == 1e-8,
            "score replay tolerance is not the frozen 1e-8 contract")
    for name in ("max_abs_pq8_score_error",):
        value = score_replay.get(name)
        require(isinstance(value, (int, float)) and value == value and
                value >= 0.0 and value <= tolerance,
                f"invalid {name}")
    if any(row.get("has_tq_intermediate_norm") for row in rows):
        value = score_replay.get("max_abs_tq_score_error")
        require(isinstance(value, (int, float)) and value == value and
                value >= 0.0 and value <= tolerance,
                "invalid max_abs_tq_score_error")
    expected_hashes = {
        "payload_sha256": sha256(payload),
        "native_jsonl_sha256": sha256(native_jsonl),
        "queries_sha256": sha256(queries),
        "thq_sha256": sha256(thq),
        "replay_runner_sha256": sha256(Path(__file__).with_name("replay-native-tq1-pq-scores.py")),
    }
    for name, expected in expected_hashes.items():
        require(score_replay.get(name) == expected,
                f"score replay {name} binding differs")


def audit_self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        native = root / "native.jsonl"; payload = root / "payload"; queries = root / "queries"; thq = root / "thq"
        native.write_text("\n".join(json.dumps({"query": query,
                                      "thq4_top128_ids": list(range(128)),
                                      "has_tq_intermediate_norm": False})
                                      for query in range(152)) + "\n", encoding="utf-8")
        payload.write_bytes(b"payload"); queries.write_bytes(b"queries"); thq.write_bytes(b"thq")
        score = {"schema_version": 1, "status": "PASS", "rows": 152,
                 "scores": 19456, "score_tolerance": 1e-8,
                 "max_abs_pq8_score_error": 0.0,
                 "payload_sha256": sha256(payload), "native_jsonl_sha256": sha256(native),
                 "queries_sha256": sha256(queries), "thq_sha256": sha256(thq),
                 "replay_runner_sha256": sha256(Path(__file__).with_name("replay-native-tq1-pq-scores.py"))}
        validate_score_replay(score, native, payload, queries, thq)
        mutated = dict(score); mutated["payload_sha256"] = "mutated"
        try:
            validate_score_replay(mutated, native, payload, queries, thq)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("mutated payload hash was accepted")
        mutated = dict(score); mutated["native_jsonl_sha256"] = "mutated"
        try:
            validate_score_replay(mutated, native, payload, queries, thq)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("mutated JSONL hash was accepted")
        mutated = dict(score); mutated["max_abs_pq8_score_error"] = 1e-3
        try:
            validate_score_replay(mutated, native, payload, queries, thq)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("oversized score error was accepted")
        execution_receipt = {
            "status": "EXECUTED",
            "runner_source_sha256": "runner",
            "payload_sha256": "payload",
            "payload_receipt_sha256": "receipt",
            "native_jsonl_sha256": "native",
        }
        require("expected_pq_result_sha256" not in execution_receipt and
                "expected_tq_result_sha256" not in execution_receipt,
                "execution receipt contains audit-only reference hashes")
    require(sha256(Path(__file__)) == sha256(Path(__file__)), "hash self-test failed")
    print("native-tq1-pq-direct audit self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--native-jsonl", type=Path)
    parser.add_argument("--run-receipt", type=Path)
    parser.add_argument("--score-replay", type=Path)
    parser.add_argument("--materializer", type=Path)
    parser.add_argument("--native-runner", type=Path)
    parser.add_argument("--native-binary", type=Path)
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--expected-pq-result", type=Path)
    parser.add_argument("--expected-tq-result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        audit_self_test()
        return
    for value in (args.payload, args.receipt, args.native_jsonl,
                  args.materializer, args.native_runner,
                  args.native_binary, args.build_manifest,
                  args.run_receipt,
                  args.score_replay,
                  args.expected_pq_result, args.expected_tq_result, args.output):
        if value is None:
            parser.error("all audit paths are required")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    require(receipt["status"] == "EXECUTED" and receipt["candidate_local"],
            "payload receipt is not executed candidate-local evidence")
    require(receipt["payload_sha256"] == sha256(args.payload), "payload hash mismatch")
    require(receipt["materializer_sha256"] == sha256(args.materializer),
            "materializer hash mismatch")
    run_receipt = json.loads(args.run_receipt.read_text(encoding="utf-8"))
    receipt_bindings = {
        "runner_sha256": sha256(args.native_runner),
        "payload_sha256": sha256(args.payload),
        "payload_receipt_sha256": sha256(args.receipt),
        "native_jsonl_sha256": sha256(args.native_jsonl),
    }
    require(run_receipt.get("status") == "EXECUTED" and
            run_receipt.get("argv") and
            run_receipt.get("runner_source_sha256") == sha256(args.native_runner),
            "native execution receipt provenance is incomplete")
    for name, actual in receipt_bindings.items():
        require(run_receipt.get(name) == actual,
                f"native execution receipt {name} binding differs")
    require(isinstance(run_receipt.get("argv"), list),
            "native execution receipt argv is not recorded")
    require(run_receipt.get("runner_binary_sha256") == sha256(args.native_binary),
            "native execution receipt binary binding differs")
    require(run_receipt.get("build_manifest_sha256") == sha256(args.build_manifest),
            "native execution receipt build binding differs")
    require(run_receipt.get("payload_receipt_path") == str(args.receipt) and
            run_receipt.get("runner_source_path") == str(args.native_runner) and
            run_receipt.get("runner_binary_path") == str(args.native_binary) and
            run_receipt.get("build_manifest_path") == str(args.build_manifest),
            "native execution receipt declared paths differ")
    for field, expected_path in {
        "thq_sha256": "thq_path",
        "thresholds_sha256": "thresholds_path",
        "candidate_flat_sha256": "candidate_flat_path",
        "offsets_sha256": "offsets_path",
        "queries_sha256": "queries_path",
        "native_jsonl_sha256": "native_jsonl_path",
    }.items():
        declared = run_receipt.get(expected_path)
        require(isinstance(declared, str) and Path(declared).is_file(),
                f"native execution receipt missing {expected_path}")
        require(run_receipt.get(field) == sha256(Path(declared)),
                f"native execution receipt {field} path binding differs")
    for field, expected_path in {
        "payload_receipt_sha256": args.receipt,
        "runner_source_sha256": args.native_runner,
    }.items():
        require(run_receipt.get(field) == sha256(expected_path),
                f"native execution receipt {field} binding differs")
    score_replay = json.loads(args.score_replay.read_text(encoding="utf-8"))
    validate_score_replay(score_replay, args.native_jsonl, args.payload,
                          Path(run_receipt["queries_path"]), Path(run_receipt["thq_path"]))
    rows = load_jsonl(args.native_jsonl)
    require(len(rows) > 0 and len({int(row["query"]) for row in rows}) == len(rows),
            "native output must contain one row per query")
    pq = json.loads(args.expected_pq_result.read_text(encoding="utf-8"))
    tq = json.loads(args.expected_tq_result.read_text(encoding="utf-8"))
    require(receipt["source_hashes"]["pq-artifact"] == pq["artifact_sha256"],
            "PQ artifact binding differs")
    require(receipt["source_hashes"]["canonical-tq-payload"] == tq["artifact_sha256"],
            "TQ artifact binding differs")
    pq_rows = {int(row["query"]): row for row in pq["rows"] if row["arm"] == "tq1_pq8_k128"}
    tq_rows = {int(row["query"]): row for row in tq["rows"] if row["arm"] == "turboquant1"}
    require(set(pq_rows) == {int(row["query"]) for row in rows}, "PQ reference query set differs")
    require(set(tq_rows) == set(pq_rows), "TQ reference query set differs")
    pq_exact = tq_exact = thq_exact = 0
    for row in rows:
        query = int(row["query"])
        require(row["side_bytes_per_document"] == receipt["side_bytes_per_document"],
                "side-byte accounting differs")
        for name in ("thq4_prefilter", "query_prepare", "score_top128", "total"):
            value = float(row["timing_ms"][name])
            require(value >= 0.0 and value == value, f"invalid timing: {name}")
        thq_exact += row["thq4_top128_ids"] == pq_rows[query]["thq4_top128_ids"]
        pq_exact += row["tq1_pq8_top10_ids"] == pq_rows[query]["top10_ids"]
        if row["has_tq_intermediate_norm"]:
            tq_exact += row["tq1_top10_ids"] == tq_rows[query]["top10_ids"]
    require(thq_exact == len(rows), "THQ top-128 parity failed")
    require(pq_exact == len(rows), "PQ8 top-10 parity failed")
    if rows[0]["has_tq_intermediate_norm"]:
        require(tq_exact == len(rows), "TQ1 top-10 parity failed")
    result = {
        "status": "PASS",
        "source_binding": True,
        "query_count": len(rows),
        "thq_top128_exact": thq_exact,
        "pq8_top10_exact": pq_exact,
        "tq1_top10_exact": tq_exact if rows[0]["has_tq_intermediate_norm"] else None,
        "side_bytes_per_document": receipt["side_bytes_per_document"],
        "payload_sha256": receipt["payload_sha256"],
        "materializer_sha256": receipt["materializer_sha256"],
        "native_runner_sha256": sha256(args.native_runner),
        "native_run_receipt_sha256": sha256(args.run_receipt),
        "score_replay_sha256": sha256(args.score_replay),
        "expected_pq_result_sha256": sha256(args.expected_pq_result),
        "expected_tq_result_sha256": sha256(args.expected_tq_result),
        "max_abs_tq_score_error": score_replay.get("max_abs_tq_score_error"),
        "max_abs_pq8_score_error": score_replay.get("max_abs_pq8_score_error"),
        "native_jsonl_sha256": sha256(args.native_jsonl),
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
