#!/usr/bin/env python3
"""Rebind an already persisted QINCo replay to its immutable training plan."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--result", type=Path, required=True); p.add_argument("--codes", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True); p.add_argument("--trace", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(); result = json.loads(a.result.read_text(encoding="utf-8")); plan = json.loads(a.plan.read_text(encoding="utf-8")); trace = json.loads(a.trace.read_text(encoding="utf-8"))
    domain = plan.get("training_domain"); epochs = max(int(item["epoch"]) for item in trace["epoch_trace"])
    if plan.get("status") != "PREFIT_IMMUTABLE" or domain not in {"raw", "thq_residual"} or int(result["training"]["checkpoint_epoch"]) != epochs + 1:
        raise RuntimeError("immutable plan/trace/checkpoint contract differs")
    prefix = f"qinco2_official_{result['config']['M']}b"
    mapping = {f"{prefix}_residual_mismatch": f"{prefix}_train_{domain}_eval_residual", f"{prefix}_raw_vector": f"{prefix}_train_{domain}_eval_raw"}
    for row in result["rows"]:
        if row["arm"] not in mapping: raise RuntimeError("unexpected legacy QINCo arm")
        row["arm"] = mapping[row["arm"]]
    result["summaries"] = {mapping.get(name, name): value for name, value in result.get("summaries", {}).items()}
    result["schema_version"] = 4; result["family"] = "thq_qinco2_official_replay_v4"; result["quality_status"] = "BOUNDED_MATCHED_DOMAIN_CONTROL" if domain == "thq_residual" else "BOUNDED_TRAINING_DOMAIN_MISMATCH_CONTROL"
    result["arms"] = {mapping[f"{prefix}_residual_mismatch"]: f"{domain}-trained checkpoint evaluated on THQ residuals", mapping[f"{prefix}_raw_vector"]: f"{domain}-trained checkpoint evaluated directly on raw vectors"}
    result["training"].update({"completed_full_epochs": epochs, "training_domain": domain, "dataset_semantics": f"{domain} canonical training matrix; provenance read from immutable training plan", "training_plan_sha256": sha256(a.plan), "training_trace_sha256": sha256(a.trace), "runner_sha256": sha256(a.runner), "exact_command": "replay artifact is bound to immutable pre-fit plan"})
    result["codes_artifact_sha256"] = sha256(a.codes); result["source_hashes"]["training-plan"] = sha256(a.plan); result["source_hashes"]["training-trace"] = sha256(a.trace)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
