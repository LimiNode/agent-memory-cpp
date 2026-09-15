#!/usr/bin/env python3
"""Compose an explicit total-index footprint table from gate receipts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--k16-gate", type=Path, required=True)
    p.add_argument("--layout-receipt", type=Path, required=True)
    p.add_argument("--k1-manifest", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    k = json.loads(a.k16_gate.read_text())
    l = json.loads(a.layout_receipt.read_text())
    rows = [
        {"component": "shared document-major INT8 table", "bytes": k["architectures"]["shared_document_int8_plus_rep_doc_ids"]["shared_vector_bytes"], "status": "measured"},
        {"component": "K16 representative doc_id sidecars", "bytes": k["representative_doc_id_bytes"], "status": "measured"},
        {"component": "K16 mapping/model sidecars", "bytes": k["mapping_and_model_sidecars_bytes"], "status": "measured"},
        {"component": "canonical packed THQ table", "bytes": l["representations"]["canonical"]["bytes"], "status": "measured"},
        {"component": "R4 postings/MDBX overhead", "bytes": None, "status": "PENDING_NATIVE_PERSISTENT_MATERIALIZATION"},
        {"component": "query-specific candidate slabs", "bytes": None, "status": "excluded_ephemeral"},
    ]
    if a.k1_manifest:
        manifest = json.loads(a.k1_manifest.read_text())
        rows_32 = [row for seed in manifest.get("seeds", []) for row in seed.get("layouts", [])
                   if row.get("id") == "aosoa32_int8"]
        scales = sum(int(seed.get("scale_bytes", 0)) for seed in manifest.get("seeds", []))
        if not rows_32:
            raise RuntimeError("K1 AoSoA-32 representation is absent from manifest")
        rows.append({"component": "K1 AoSoA-32 INT8 physical representation", "bytes": sum(int(row["bytes"]) for row in rows_32), "status": "measured"})
        rows.append({"component": "K1 AoSoA-32 scales", "bytes": scales, "status": "measured"})
    measured = sum(int(row["bytes"]) for row in rows if row["bytes"] is not None)
    result = {"schema_version": 1, "family": "total_index_footprint_v1",
              "execution_status": "PARTIAL_MEASURED",
              "measured_bytes": measured, "measured_mib": measured / (1024.0 * 1024.0),
              "rows": rows,
              "interpretation": "Measured subtotal is not a production total until persistent postings and MDBX overhead are materialized.",
              "source_k16": str(a.k16_gate), "source_layout": str(a.layout_receipt)}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
