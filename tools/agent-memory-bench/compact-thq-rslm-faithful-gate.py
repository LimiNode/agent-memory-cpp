#!/usr/bin/env python3
"""Create the committed compact summary from a full RSLM replay result."""
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
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    if raw.get("family") != "thq_rslm_faithful_gate_v1" or raw.get("status") != "EXECUTED":
        raise SystemExit("raw RSLM result contract differs")
    if raw.get("query_count") != 152 or len(raw.get("rows", [])) != 912:
        raise SystemExit("raw RSLM result cardinality differs")
    arms = {}
    for name, summary in sorted(raw["summaries"].items()):
        model = raw["models"][name]
        arms[name] = {
            "side_payload_bytes": model["side_payload_bytes"],
            "raw_codec_payload_bytes": model["raw_codec_payload_bytes"],
            "inner_scale_bytes": model["inner_scale_bytes"],
            "outer_scale_bytes": model["outer_scale_bytes"],
            "cascade_payload_bytes_per_document": 96 + model["side_payload_bytes"],
            **summary,
        }
    source_keys = ("candidate_flat_sha256", "candidate_raw_sha256", "candidate_receipt_sha256",
                   "documents_sha256", "training_sha256", "queries_sha256", "qrel_ids_sha256",
                   "qrel_scores_sha256", "teacher_ids_sha256", "thq4_codes_sha256",
                   "thq4_thresholds_sha256")
    compact = {
        "schema_version": 2,
        "family": raw["family"],
        "status": raw["status"],
        "raw_result_sha256": sha256(args.raw),
        "producer_hashes": {
            "runner_sha256": raw["runner_sha256"],
            "faithful_reference_sha256": raw["faithful_reference_sha256"],
            "local_helper_sha256": raw["local_helper_sha256"],
            "packed_codec_helper_sha256": raw["packed_codec_helper_sha256"],
        },
        "reference": {
            "paper": raw["paper"],
            "initial_commit": raw["reference_initial_commit"],
            "content_commit": raw["reference_content_commit"],
            "snapshot_commit": raw["reference_snapshot_commit"],
            "notebook_blob": raw["reference_notebook_blob"],
            "notebook_sha256": raw["reference_notebook_sha256"],
        },
        "query_count": raw["query_count"],
        "candidate_unique_documents": raw["candidate_unique_documents"],
        "local_fit_rows": raw["local_fit_rows"],
        "local_iterations": raw["local_iterations"],
        "norm_diagnostics": raw["norm_diagnostics"],
        "scoring_protocol": raw["scoring_protocol"],
        "input_hashes": {key: raw[key] for key in source_keys},
        "arms": arms,
        "limitations": raw["limitations"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
