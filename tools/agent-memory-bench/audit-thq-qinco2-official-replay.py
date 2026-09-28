#!/usr/bin/env python3
"""Independent persisted-code contract audit for the QINCo2 replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    parser.add_argument("--codes", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        selected = np.asarray([[1, 2], [3, 4]], dtype=np.int64)
        require(selected.shape == (2, 2) and len(np.unique(selected)) == 4, "QINCo audit self-test failed")
        print("audit-thq-qinco2-official-replay self-test: PASS")
        return
    if any(value is None for value in (args.result, args.codes, args.output)):
        parser.error("--result, --codes and --output are required unless --self-test is used")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("status") == "EXECUTED", "QINCo result is not executed")
    require(result.get("quality_status") == "BOUNDED_TRAINING_DOMAIN_MISMATCH_CONTROL",
            "unexpected QINCo quality status")
    with np.load(args.codes, allow_pickle=False) as payload:
        selected = np.asarray(payload["selected_ids"], dtype=np.int64)
        unique = np.asarray(payload["unique_ids"], dtype=np.int64)
        codes = np.asarray(payload["codes"], dtype=np.uint8)
        norms = np.asarray(payload["final_norms"], dtype=np.float32)
        raw_codes = np.asarray(payload["raw_codes"], dtype=np.uint8)
        raw_norms = np.asarray(payload["raw_final_norms"], dtype=np.float32)
    require(selected.shape == (152, 128), "selected candidate shape differs")
    require(codes.ndim == 2 and codes.shape[0] == 16 and codes.shape[1] == len(unique),
            "QINCo code matrix shape differs")
    require(raw_codes.shape == codes.shape, "raw code matrix shape differs")
    require(len(np.unique(unique)) == len(unique), "unique IDs contain duplicates")
    require(np.all((selected >= 0) & (selected < 1_000_000)), "selected ID out of range")
    require(np.isfinite(norms).all() and np.all(norms > 0), "invalid final norms")
    require(np.isfinite(raw_norms).all() and np.all(raw_norms > 0), "invalid raw norms")
    rows = result.get("rows", [])
    require(len(rows) == 304, "expected two 152-query QINCo arms")
    for row in rows:
        require(len(row.get("top10_ids", [])) == 10, "QINCo top10 shape differs")
        require(np.all(np.isin(np.asarray(row["top10_ids"], dtype=np.int64),
                               selected[int(row["query"])])),
                "QINCo top10 escapes THQ candidate shell")
        require(np.isfinite(float(row["qrels_ndcg10"])), "non-finite QINCo nDCG")
    expected_codes_sha = result.get("codes_artifact_sha256")
    require(expected_codes_sha == sha256(args.codes), "codes artifact SHA differs")
    audit = {
        "schema_version": 1,
        "family": "thq_qinco2_official_replay_audit_v1",
        "status": "PASS",
        "result_sha256": sha256(args.result),
        "codes_sha256": sha256(args.codes),
        "selected_shape": list(selected.shape),
        "unique_documents": int(len(unique)),
        "code_shape": list(codes.shape),
        "raw_code_shape": list(raw_codes.shape),
        "norm_contract": "finite positive float32 sidecars",
        "quality_status": result["quality_status"],
        "limitations": [
            "This is an independent persisted-code/receipt audit; it does not claim a second neural decode implementation.",
            "The checkpoint was trained on raw canonical vectors and applied to THQ residuals; no production claim is made."
        ]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": "PASS", "unique_documents": len(unique)}, sort_keys=True))


if __name__ == "__main__":
    main()
