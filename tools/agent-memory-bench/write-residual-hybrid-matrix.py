#!/usr/bin/env python3
"""Write a compact provenance-aware matrix from executed codec receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


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
        rows = []
        for arm, summary in summaries.items():
            if not isinstance(summary, dict) or "mean_qrels_ndcg10" not in summary:
                continue
            rows.append({"arm": arm, "mean_qrels_ndcg10": summary["mean_qrels_ndcg10"], "p05_qrels_ndcg10": summary.get("p05_qrels_ndcg10"), "worst_qrels_ndcg10": summary.get("worst_qrels_ndcg10"), "source_replay": payload.get("source_replay"), "candidate_flat_sha256": payload.get("source_hashes", {}).get("candidate-flat")})
        entries.append({"label": label, "path": str(path), "family": payload.get("family"), "status": payload.get("status"), "metric": payload.get("metric"), "rows": rows})
    candidate_hashes = sorted({row["candidate_flat_sha256"] for e in entries for row in e["rows"] if row.get("candidate_flat_sha256")})
    output = {"schema_version": 1, "family": "residual_hybrid_matrix_v1", "status": "EXECUTED", "entry_count": len(entries), "candidate_streams": candidate_hashes, "paired_candidate_stream": len(candidate_hashes) <= 1, "entries": entries, "limitations": ["matrix consumes existing executed artifacts; it does not refit codecs", "rows with different candidate stream hashes are directional, not paired", "no production selection claim"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
