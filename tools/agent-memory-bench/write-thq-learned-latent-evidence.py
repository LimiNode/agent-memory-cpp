#!/usr/bin/env python3
"""Write compact provenance for the conditional learned-latent probe."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if result.get("family") != "thq_learned_latent_stage_local_v1":
        raise SystemExit("unexpected learned-latent family")
    compact = {key: result.get(key) for key in (
        "schema_version", "family", "status", "evidence_status", "documents", "training_count",
        "query_count", "hidden", "epochs", "seed", "prefilter", "latent_bytes", "loss_history",
        "training_loss_final", "latent_low_sha256", "latent_high_sha256", "summaries", "limitations")}
    compact["model_state_sha256"] = result.get("model_state_sha256")
    compact["latent_diagnostics"] = result.get("latent_diagnostics")
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "family": "thq_learned_latent_stage_local_receipt_v1",
        "status": "EXECUTED",
        "runner_sha256": sha256(Path(__file__).with_name("run-thq-learned-latent-stage-local.py")),
        "result_sha256": sha256(args.result),
        "compact_result_sha256": sha256(args.output),
        "model_state_sha256": result.get("model_state_sha256"),
        "input_hashes": {key: result.get(key) for key in (
            "documents_sha256", "training_sha256", "queries_sha256", "query_ids_sha256",
            "document_ids_sha256", "qrels_sha256", "thq_sha256")},
        "scope": {"query_count": result.get("query_count"), "canonical_152_query_payload": False,
                  "native_latency": False, "conditional_latent": True},
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
