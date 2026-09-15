#!/usr/bin/env python3
"""Compare K16 vector duplication with rep_doc_id/shared-code accounting.

This is a storage/identity gate. It refuses to call a latency result
authoritative unless a native receipt is supplied separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def validate_items(root: Path, items: list[dict], label: str) -> None:
    for item in items:
        file = (root.parent if item.get("external_root") else root) / item["file"]
        if not file.is_file() or file.stat().st_size != int(item["bytes"]) or sha(file) != item["sha256"]:
            raise RuntimeError(f"{label} artifact binding mismatch: {file}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--native-receipt", type=Path)
    a = p.parse_args()
    m = json.loads((a.manifest / "manifest.json").read_text())
    seeds = m["seeds"]
    for seed in seeds:
        validate_items(a.manifest, seed["layouts"], "layout")
        validate_items(a.manifest, seed.get("mappings", []), "mapping")
        validate_items(a.manifest, seed.get("model", []), "model")
    validate_items(a.manifest, m.get("global_layouts", []), "global layout")
    representative_bytes = sum(next(int(item["bytes"]) for item in seed["mappings"]
                                    if item["role"] == "representative_documents")
                               for seed in seeds)
    mapping_sidecars = sum(sum(int(item["bytes"]) for item in [*seed.get("mappings", []), *seed.get("model", [])]
                               if item.get("role") != "representative_documents") for seed in seeds)
    shared_rows = [item for item in m.get("global_layouts", []) if item.get("role") in
                   {"document_major_int8", "shared_document_int8", "document_table"}]
    if len(shared_rows) != 1:
        raise RuntimeError("manifest must identify exactly one shared document table by role")
    shared_doc_bytes = int(shared_rows[0]["bytes"])
    duplicated_vector_bytes = sum(next(int(item["bytes"]) for item in seed["layouts"] if item["role"] == "address_major_int8") for seed in seeds)
    shared_total = shared_doc_bytes + representative_bytes + mapping_sidecars
    duplicated_total = duplicated_vector_bytes + mapping_sidecars
    result = {
        "schema_version": 1,
        "family": "k16_shared_representation_storage_gate_v1",
        "execution_status": "EXECUTED",
        "production_activation": False,
        "source_manifest_sha256": sha(a.manifest / "manifest.json"),
        "seeds": len(seeds),
        "representative_doc_id_bytes": representative_bytes,
        "mapping_and_model_sidecars_bytes": mapping_sidecars,
        "architectures": {
            "duplicated_int8_vectors": {"vector_bytes": duplicated_vector_bytes, "total_persistent_bytes": duplicated_total},
            "shared_document_int8_plus_rep_doc_ids": {"shared_vector_bytes": shared_doc_bytes, "rep_doc_id_bytes": representative_bytes, "total_persistent_bytes": shared_total},
        },
        "savings": {"bytes": duplicated_total - shared_total,
                    "fraction": (duplicated_total - shared_total) / duplicated_total if duplicated_total else 0.0},
        "quality_and_latency": {"status": "PENDING_NATIVE_REPLAY",
                                "native_receipt": str(a.native_receipt) if a.native_receipt else None},
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
