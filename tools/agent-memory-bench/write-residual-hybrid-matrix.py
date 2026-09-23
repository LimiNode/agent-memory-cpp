#!/usr/bin/env python3
"""Write a compact provenance-aware matrix from executed codec receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

THQ_BYTES = 96
DOCUMENT_COUNT = 1_000_000


def candidate_hash(payload: dict) -> str | None:
    sources = payload.get("source_hashes", {})
    return (
        payload.get("candidate_stream_hash")
        or sources.get("candidate-flat")
        or sources.get("candidate_flat")
    )


def representative_row(payload: dict, arm: str) -> dict:
    return next((row for row in payload.get("rows", []) if row.get("arm") == arm), {})


def storage(payload: dict, arm: str, summary: dict) -> dict:
    row = representative_row(payload, arm)
    raw_side = summary.get("side_payload_bytes", row.get("side_payload_bytes"))
    raw_total = summary.get("cascade_total_bytes", row.get("cascade_total_bytes"))
    global_bytes = summary.get("global_model_bytes", summary.get("global_codebook_bytes"))
    if global_bytes is None:
        for payload_bytes, value in payload.get("global_model_bytes_by_payload", {}).items():
            if arm.endswith(str(payload_bytes)):
                global_bytes = value
                break
    if global_bytes is None:
        global_bytes = payload.get("global_model_bytes", payload.get("payload_contract", {}).get("global_metadata_bytes", 0))
    if raw_side is None and raw_total is not None:
        raw_side = int(raw_total) - THQ_BYTES
    if raw_side is None:
        return {"raw_side_payload_bytes": None, "final_norm_bytes": None, "side_bytes": None, "thq_plus_side_bytes": None, "global_model_bytes": global_bytes, "full_1m_footprint_bytes": None}
    norm_included = bool(payload.get("payload_contract", {}).get("final_norm_included"))
    norm_included = norm_included or (arm.startswith("turboquant_plus1_") and int(raw_side) == 60)
    needs_norm = payload.get("metric") == "cosine" and not norm_included
    norm_bytes = 0 if not needs_norm else 4
    side_bytes = int(raw_side) + norm_bytes
    total = THQ_BYTES + side_bytes
    return {
        "raw_side_payload_bytes": int(raw_side),
        "final_norm_bytes": norm_bytes,
        "side_bytes": side_bytes,
        "thq_plus_side_bytes": total,
        "global_model_bytes": int(global_bytes or 0),
        "full_1m_footprint_bytes": total * DOCUMENT_COUNT + int(global_bytes or 0),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--entry", action="append", nargs=2, metavar=("LABEL", "JSON"), required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    entries = []
    for label, raw_path in args.entry:
        path = Path(raw_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        summaries = payload.get("summaries", {})
        stream_hash = candidate_hash(payload)
        rows = []
        for arm, summary in summaries.items():
            if not isinstance(summary, dict) or "mean_qrels_ndcg10" not in summary:
                continue
            rows.append({"arm": arm, "mean_qrels_ndcg10": summary["mean_qrels_ndcg10"], "p05_qrels_ndcg10": summary.get("p05_qrels_ndcg10"), "worst_qrels_ndcg10": summary.get("worst_qrels_ndcg10"), "source_replay": payload.get("source_replay"), "candidate_flat_sha256": stream_hash, **storage(payload, arm, summary)})
        entries.append({"label": label, "path": str(path), "family": payload.get("family"), "status": payload.get("status"), "metric": payload.get("metric"), "rows": rows})
    all_rows = [row for entry in entries for row in entry["rows"]]
    all_hashes_present = bool(all_rows) and all(row.get("candidate_flat_sha256") for row in all_rows)
    candidate_hashes = sorted({row["candidate_flat_sha256"] for row in all_rows if row.get("candidate_flat_sha256")})
    paired = all_hashes_present and len(candidate_hashes) == 1
    output = {"schema_version": 2, "family": "residual_hybrid_matrix_v2", "status": "EXECUTED", "entry_count": len(entries), "candidate_streams": candidate_hashes, "all_candidate_hashes_present": all_hashes_present, "paired_candidate_stream": paired, "storage_contract": {"thq_bytes_per_document": THQ_BYTES, "document_count": DOCUMENT_COUNT, "fp32_final_norm_bytes": 4, "fp16_final_norm_bytes": 2, "matrix_uses": "FP32 final norm unless already included by the source arm", "fp16_status": "separate quality control; not assumed parity-safe"}, "entries": entries, "limitations": ["matrix consumes existing executed artifacts; it does not refit codecs", "pairing is false unless every row carries the same candidate-stream hash", "FP32 final norm is charged to direct cosine arms when not already present", "no production selection claim"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
