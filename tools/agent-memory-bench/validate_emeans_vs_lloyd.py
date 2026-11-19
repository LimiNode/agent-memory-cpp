#!/usr/bin/env python3
"""Fail-closed validator for the offline Lloyd/E-means smoke receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def validate(raw_path: Path, receipt_path: Path, sidecar_path: Path) -> dict:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(raw.get("schema") == "r5.emeans_vs_lloyd.synthetic_smoke.v1", "schema differs")
    require(raw.get("status") == "PASS" and raw.get("execution_status") == "EXECUTED", "status differs")
    require(raw.get("dataset_kind") == "synthetic_smoke", "dataset kind differs")
    require(raw.get("leakage", "").startswith("train/base/query generated independently"), "split contract differs")
    require(raw.get("faiss") == "1.16.0", "Faiss pin differs")
    require({item.get("method") for item in raw.get("methods", [])} == {"faiss_lloyd", "faiss_numpy_emeans"},
            "method set differs")
    require(receipt.get("execution_status") == "EXECUTED" and receipt.get("production_activation") is False,
            "receipt status differs")
    require(receipt.get("raw_output", {}).get("sha256") == sha256(raw_path), "receipt digest differs")
    sidecar = sidecar_path.read_text(encoding="utf-8").split()[0]
    require(sidecar == sha256(raw_path), "sidecar digest differs")
    for method in raw["methods"]:
        require(len(method.get("retrieval", [])) == 4, "nprobe matrix differs")
        require(all(0.0 <= float(row["recall_at_10"]) <= 1.0 for row in method["retrieval"]),
                "recall range differs")
        require(all(float(row["scanned_documents_mean"]) > 0 for row in method["retrieval"]),
                "scan count differs")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    raw = validate(args.raw, args.receipt, args.sidecar)
    if args.self_test:
        with tempfile.TemporaryDirectory() as temporary:
            mutated = Path(temporary) / "mutated.json"
            payload = json.loads(args.raw.read_text(encoding="utf-8"))
            payload["faiss"] = "1.15.0"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            try:
                validate(mutated, args.receipt, args.sidecar)
            except RuntimeError:
                pass
            else:
                raise RuntimeError("mutated Faiss version was accepted")
    print(json.dumps({"status": "PASS", "methods": len(raw["methods"])}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"validate_emeans_vs_lloyd: {error}")
