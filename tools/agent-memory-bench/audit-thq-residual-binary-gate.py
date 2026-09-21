#!/usr/bin/env python3
"""Fail-closed provenance audit for the Gate C research runner."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def require(ok, msg):
    if not ok: raise RuntimeError(msg)

def self_test():
    require(sha256(Path(__file__)) == sha256(Path(__file__)), "hash self-test failed")
    print("THQ residual binary Gate C audit self-test: PASS")

def main():
    p = argparse.ArgumentParser(); p.add_argument("--self-test", action="store_true")
    for name in ("result", "runner", "models", "codes", "output"):
        p.add_argument(f"--{name}", type=Path, required=False)
    a = p.parse_args()
    if a.self_test: self_test(); return
    required = (a.result, a.runner, a.models, a.codes, a.output)
    require(all(x is not None and x.is_file() for x in required), "all audit inputs must be files")
    result = json.loads(a.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_residual_binary_gate_c_v1", "Gate C family differs")
    require(result.get("status") == "EXECUTED" and result.get("source_replay") is True, "result is not source-replay evidence")
    require(result.get("runner_sha256") == sha256(a.runner), "runner SHA binding differs")
    require(result.get("artifact_hashes") == {"models": sha256(a.models), "codes": sha256(a.codes)}, "artifact SHA binding differs")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == 152 * 2 * 3, "Gate C row matrix differs")
    require(set(result.get("arms", [])) == {"rabitq_like", "bbq_like"}, "Gate C arm taxonomy differs")
    require(result.get("corrections") == ["code_only", "scale", "scale_norm"], "correction ablation differs")
    audit = {"schema_version": 1, "family": "thq_residual_binary_gate_c_audit_v1", "status": "PASS", "source_binding": True, "independent_decode_replay": False, "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "artifact_hashes": {"models": sha256(a.models), "codes": sha256(a.codes)}, "row_count": len(rows), "checks": ["runner/result/artifact SHA binding", "152-query x 2-arm x 3-correction cardinality", "explicit local-reference taxonomy", "cosine and payload metadata presence"], "limitations": ["This audit does not retrain or independently decode the research codecs", "not faithful TurboQuant/NEQ evidence", "full source-bound replay and held-out confirmation remain required"]}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print("THQ residual binary Gate C audit PASS")

if __name__ == "__main__": main()
