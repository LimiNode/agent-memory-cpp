#!/usr/bin/env python3
"""Fail-closed audit for the packed candidate-local RSLM1 scorer."""
from __future__ import annotations
import argparse, hashlib, json, tempfile
from pathlib import Path

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): digest.update(chunk)
    return digest.hexdigest()

def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)

def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]

def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]

def audit(output: Path, expected: Path, receipt: Path | None = None, runner: Path | None = None) -> dict:
    actual, refs = rows(output), rows(expected)
    require(len(actual) == len(refs) == 152, "RSLM1 query cardinality differs")
    seen: set[int] = set(); timings: list[float] = []
    for row, ref in zip(actual, refs):
        query = row.get("query")
        require(isinstance(query, int) and query == ref.get("query"), "query order differs")
        require(query not in seen, "duplicate query"); seen.add(query)
        require(row.get("candidate_count") == 128, "candidate width differs")
        require(row.get("top10_ids") == ref.get("top10_ids"), f"RSLM1 top-10 parity failed at query {query}")
        timing = row.get("timing_ms", {}).get("codec_rerank")
        require(isinstance(timing, (int, float)) and timing >= 0 and timing == timing, "invalid RSLM1 timing")
        timings.append(float(timing))
    require(seen == set(range(152)), "missing query")
    result = {"status": "PASS", "query_count": 152, "candidate_count": 128,
              "top10_exact": 152, "output_sha256": sha256(output),
              "expected_reference_sha256": sha256(expected),
              "timing_ms": {"p50": percentile(timings, .5), "p95": percentile(timings, .95), "p99": percentile(timings, .99)},
              "audit_runner_sha256": sha256(Path(__file__))}
    if runner is not None: result["runner_sha256"] = sha256(runner)
    if receipt is not None:
        committed = json.loads(receipt.read_text(encoding="utf-8"))
        require(committed.get("schema_version") == 1 and committed.get("family") == "native_rslm1_packed_candidate_gate_v1", "receipt schema differs")
        require(committed.get("output_sha256") == result["output_sha256"], "receipt output_sha256 differs")
        require(committed.get("expected_reference_sha256") == result["expected_reference_sha256"], "receipt expected_reference_sha256 differs")
        require(committed.get("audit_runner_sha256") == result["audit_runner_sha256"], "receipt audit_runner_sha256 differs")
        if runner is not None: require(committed.get("runner_sha256") == result["runner_sha256"], "receipt runner_sha256 differs")
        for field, measured in result["timing_ms"].items():
            require(abs(float(committed.get("timing_ms", {}).get(field, -1)) - measured) <= 1e-6, f"receipt timing {field} differs from raw samples")
    return result

def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); out, ref = root / "out.jsonl", root / "ref.jsonl"
        base = [{"query": i, "candidate_count": 128, "top10_ids": list(range(10)), "timing_ms": {"codec_rerank": 0.1 + i / 1000}} for i in range(152)]
        text = "".join(json.dumps(row) + "\n" for row in base); out.write_text(text, encoding="utf-8"); ref.write_text(text, encoding="utf-8")
        result = audit(out, ref); require(result["timing_ms"]["p50"] == percentile([0.1 + i / 1000 for i in range(152)], .5), "percentile self-test differs")
        receipt = root / "receipt.json"; committed = {"schema_version": 1, "family": "native_rslm1_packed_candidate_gate_v1", **result}; receipt.write_text(json.dumps(committed), encoding="utf-8")
        def rejects(mutated: list[dict], message: str) -> None:
            out.write_text("".join(json.dumps(row) + "\n" for row in mutated), encoding="utf-8")
            try: audit(out, ref)
            except (ValueError, json.JSONDecodeError): return
            raise ValueError(message)
        mutated = [dict(row) for row in base]; mutated[0] = dict(mutated[0], top10_ids=[99]); rejects(mutated, "mutated top10 was accepted")
        mutated = [dict(row) for row in base]; mutated[0] = dict(mutated[0], timing_ms={"codec_rerank": -1}); rejects(mutated, "malformed timing was accepted")
        rejects(base[1:], "missing query was accepted")
        mutated = [dict(row) for row in base]; mutated[1] = dict(mutated[1], query=0); rejects(mutated, "duplicate query was accepted")
        mutated = [dict(row) for row in base]; mutated[0] = dict(mutated[0], timing_ms={"codec_rerank": 9.9})
        out.write_text("".join(json.dumps(row) + "\n" for row in mutated), encoding="utf-8")
        bad_receipt = dict(committed, output_sha256=sha256(out))
        receipt.write_text(json.dumps(bad_receipt), encoding="utf-8")
        try: audit(out, ref, receipt=receipt)
        except ValueError: pass
        else: raise ValueError("wrong percentile receipt was accepted")
    print("audit-native-rslm1-benchmark self-test PASS")

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path); parser.add_argument("--expected", type=Path); parser.add_argument("--receipt", type=Path); parser.add_argument("--runner", type=Path); parser.add_argument("--result", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test: self_test(); return
    if not args.output or not args.expected: parser.error("--output and --expected are required")
    result = audit(args.output, args.expected, args.receipt, args.runner)
    if args.result: args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))

if __name__ == "__main__": main()
