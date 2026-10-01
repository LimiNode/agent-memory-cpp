#!/usr/bin/env python3
"""Fail-closed audit for the packed candidate-local RSLM1 scorer."""
from __future__ import annotations
import argparse, hashlib, json, tempfile
from pathlib import Path

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def require(ok: bool, message: str) -> None:
    if not ok: raise RuntimeError(message)

def audit(output: Path, expected: Path) -> dict:
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    refs = [json.loads(line) for line in expected.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(len(rows) == len(refs) == 152, "RSLM1 query cardinality differs")
    for row, ref in zip(rows, refs):
        require(row.get("query") == ref.get("query"), "query order differs")
        require(row.get("candidate_count") == 128, "candidate width differs")
        require(row.get("top10_ids") == ref.get("top10_ids"), f"RSLM1 top-10 parity failed at query {row.get('query')}")
        timing = row.get("timing_ms", {}).get("codec_rerank")
        require(isinstance(timing, (int, float)) and timing >= 0 and timing == timing, "invalid RSLM1 timing")
    return {"status": "PASS", "query_count": 152, "candidate_count": 128,
            "top10_exact": 152, "output_sha256": sha256(output),
            "expected_sha256": sha256(expected), "audit_runner_sha256": sha256(Path(__file__))}

def self_test() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d); rows = [{"query": i, "candidate_count": 128, "top10_ids": list(range(10)), "timing_ms": {"codec_rerank": 0.1}} for i in range(152)]
        out = root / "out.jsonl"; ref = root / "ref.jsonl"
        text = "".join(json.dumps(row) + "\n" for row in rows); out.write_text(text, encoding="utf-8"); ref.write_text(text, encoding="utf-8")
        audit(out, ref)
        bad = list(rows); bad[0] = dict(bad[0], top10_ids=[99]); ref.write_text("".join(json.dumps(row) + "\n" for row in bad), encoding="utf-8")
        try: audit(out, ref)
        except RuntimeError: pass
        else: raise RuntimeError("mutated RSLM1 output was accepted")
    print("audit-native-rslm1-benchmark self-test PASS")

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path); parser.add_argument("--expected", type=Path); parser.add_argument("--result", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test: self_test(); return
    if not args.output or not args.expected or not args.result: parser.error("--output, --expected and --result are required")
    result = audit(args.output, args.expected); args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps(result, sort_keys=True))

if __name__ == "__main__": main()
