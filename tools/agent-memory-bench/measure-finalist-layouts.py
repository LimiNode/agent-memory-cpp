#!/usr/bin/env python3
"""Measure deterministic row/segment physical-layout models for finalists.

This is a persistence prototype: it computes page occupancy and amplification
from source-bound packed payload sizes, but it does not claim MDBX I/O latency
or production publication semantics.
"""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path

PAGE = 4096

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=1_000_000)
    parser.add_argument("--candidate-count", type=int, default=5000)
    parser.add_argument("--segment-rows", type=int, default=4096)
    parser.add_argument("--payload", action="append", required=True,
                        help="CODEC=bytes_per_doc|source_path")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.documents <= 0 or args.candidate_count <= 0 or args.segment_rows <= 0:
        parser.error("document, candidate and segment counts must be positive")
    codecs = {}
    for spec in args.payload:
        fields = spec.split("|", 1)
        if len(fields) < 2:
            parser.error("payload must be CODEC=bytes|source")
        name, width_text = fields[0].split("=", 1)
        width = int(width_text)
        source = Path(fields[1])
        if width <= 0:
            parser.error("payload width must be positive")
        codecs[name] = {"bytes_per_document": width,
                        "source": str(source),
                        "source_sha256": sha256(source) if source.is_file() else None,
                        "source_status": "PRESENT" if source.is_file() else "EXTERNAL_NOT_FOUND"}
    rows = []
    for name, item in codecs.items():
        width = item["bytes_per_document"]
        logical = args.documents * width
        row_value = width + 8  # numeric document key + value
        row_pages = math.ceil(args.documents * row_value / PAGE)
        segment_value = args.segment_rows * width + 16
        segment_count = math.ceil(args.documents / args.segment_rows)
        segment_pages = sum(math.ceil((min(args.segment_rows, args.documents - i * args.segment_rows) * width + 16) / PAGE)
                            for i in range(segment_count))
        candidate_row_pages = math.ceil(args.candidate_count * row_value / PAGE)
        candidate_segment_pages = math.ceil((args.candidate_count * width + 16) / PAGE)
        rows.append({"codec": name, "bytes_per_document": width,
                     "logical_1m_bytes": logical,
                     "row_kv": {"value_bytes": row_value, "pages": row_pages,
                                "physical_bytes": row_pages * PAGE,
                                "amplification": (row_pages * PAGE) / logical},
                     "segment_blob": {"segment_rows": args.segment_rows,
                                      "segments": segment_count, "pages": segment_pages,
                                      "physical_bytes": segment_pages * PAGE,
                                      "amplification": (segment_pages * PAGE) / logical},
                     "candidate_5k": {"row_pages": candidate_row_pages,
                                      "row_physical_bytes": candidate_row_pages * PAGE,
                                      "segment_pages": candidate_segment_pages,
                                      "segment_physical_bytes": candidate_segment_pages * PAGE},
                     "source": item})
    result = {"schema_version": 1, "family": "finalist_persistent_layout_model_v1",
              "status": "EXECUTED", "production_activation": False,
              "documents": args.documents, "candidate_count": args.candidate_count,
              "page_bytes": PAGE, "rows": rows,
              "limitations": ["page model only; no MDBX benchmark", "no cold/reopen/recovery/concurrency claim",
                               "candidate rows use the fresh Prototype-IVF budget, not Modern R4"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "codecs": len(rows),
                      "output": str(args.output)}, sort_keys=True))

if __name__ == "__main__":
    main()
