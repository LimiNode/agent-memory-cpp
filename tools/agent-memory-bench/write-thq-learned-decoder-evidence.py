#!/usr/bin/env python3
"""Write compact provenance for the bounded learned-decoder diagnostic."""
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
    if result.get("family") != "thq_learned_decoder_stage_local_v1":
        raise SystemExit("unexpected learned decoder family")
    compact = {key: result.get(key) for key in (
        "schema_version", "family", "status", "evidence_status", "documents", "training_count",
        "query_count", "prefilter", "hidden", "max_iter", "centroid_pretrain_iter", "target", "seed", "norm_range_from_training",
        "decoder_coef_sha256", "target_mean_sha256", "target_scale_sha256", "training_loss_final",
        "training_prediction_norm_range", "summaries", "limitations")}
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "family": "thq_learned_decoder_stage_local_receipt_v1",
        "status": "EXECUTED",
        "runner_sha256": sha256(Path(__file__).with_name("run-thq-learned-decoder-stage-local.py")),
        "result_sha256": sha256(args.result),
        "compact_result_sha256": sha256(args.output),
        "input_hashes": {key: result.get(key) for key in (
            "documents_sha256", "training_sha256", "queries_sha256", "thq_sha256")},
        "scope": {"query_count": result.get("query_count"), "canonical_152_query_payload": False,
                  "native_latency": False, "retrieval_oriented_loss": False},
    }
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
