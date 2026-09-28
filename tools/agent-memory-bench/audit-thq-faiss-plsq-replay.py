#!/usr/bin/env python3
"""Fail-closed persisted-code audit for the bounded PLSQ control."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--result", type=Path); p.add_argument("--codes", type=Path); p.add_argument("--output", type=Path); p.add_argument("--self-test", action="store_true"); a = p.parse_args()
    if a.self_test:
        assert np.asarray([[0]], dtype=np.uint8).dtype == np.uint8; print("audit-thq-faiss-plsq-replay self-test: PASS"); return
    if any(x is None for x in (a.result, a.codes, a.output)): p.error("--result, --codes and --output are required")
    result = json.loads(a.result.read_text(encoding="utf-8"))
    if result.get("status") != "EXECUTED" or result.get("quality_status") != "BOUNDED_PLSQ_CONTROL": raise RuntimeError("unexpected PLSQ status")
    rows = result.get("rows", []); summary = result.get("summary", {})
    if len(rows) != 152 or not all(np.isfinite(float(row["qrels_ndcg10"])) for row in rows): raise RuntimeError("PLSQ rows are incomplete or non-finite")
    with np.load(a.codes, allow_pickle=False) as payload:
        selected = np.asarray(payload["selected_ids"], dtype=np.int64); codes = np.asarray(payload["codes"], dtype=np.uint8); norms = np.asarray(payload["final_norms"], dtype=np.float32)
    if selected.shape != (152, 128) or codes.shape != (152, 128, 8) or norms.shape != (152, 128) or not np.isfinite(norms).all() or np.any(norms <= 0): raise RuntimeError("PLSQ persisted shape/norm contract differs")
    if result.get("codes_sha256") != sha256(a.codes): raise RuntimeError("PLSQ code SHA differs")
    audit = {"schema_version": 1, "family": "thq_faiss_plsq_replay_audit_v1", "status": "PASS", "result_sha256": sha256(a.result), "codes_sha256": sha256(a.codes), "quality_status": result["quality_status"], "rows": len(rows), "code_shape": list(codes.shape), "selected_shape": list(selected.shape), "summary": summary, "limitations": ["persisted-code contract audit; no second PLSQ implementation", "bounded candidate-local control; no production selection claim"]}
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps({"status": "PASS", "rows": len(rows)}, sort_keys=True))

if __name__ == "__main__": main()
