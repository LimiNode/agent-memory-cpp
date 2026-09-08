#!/usr/bin/env python3
"""Fail closed audit for the v1 shared-address cosine source vectors."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("shared_address_normalization_runner", "run-direct-learned-semantic-address.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def summarize(vectors: numpy.ndarray, tolerance: float) -> dict[str, float | int]:
    require(vectors.ndim == 2 and vectors.shape[0] > 0, "normalization audit vectors differ")
    norms = numpy.linalg.norm(vectors.astype(numpy.float64), axis=1)
    report = {
        "count": int(norms.size),
        "minimum": float(numpy.min(norms)),
        "maximum": float(numpy.max(norms)),
        "mean": float(numpy.mean(norms, dtype=numpy.float64)),
        "maximum_absolute_error_from_one": float(numpy.max(numpy.abs(norms - 1.0))),
    }
    require(report["maximum_absolute_error_from_one"] <= tolerance,
            "shared learned-address cosine source vectors are not L2-normalized")
    return report


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == {
        "schema_version": 2,
        "family": "shared_learned_semantic_address_normalization_audit_v2",
        "source_result_family": "shared_learned_semantic_address_result_v2",
        "source_result_sha256": "ae6dc029ee809c62b553b5b8b59ec64c9fe67b8bd16ba87bd8fd9bd23955f601",
        "e5_manifest_sha256": "f020bc77f7b534e45a596683eabfb30fcd71220268b0cf244f29152abd262c84",
        "input_manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987",
        "expected_document_count": 25000,
        "expected_query_count": 648,
        "expected_dimensions": 384,
        "l2_tolerance": 1.0e-5,
    }, "shared learned-address normalization-audit contract differs")
    return value


def run(contract_path: Path, result_root: Path, e5_root: Path, input_root: Path, output: Path) -> None:
    contract = load_contract(contract_path)
    result_path = result_root / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    require(result.get("family") == contract["source_result_family"] and sha256(result_path) == contract["source_result_sha256"],
            "shared learned-address source result differs")
    data = runner.load_inputs(e5_root, input_root)
    documents = data["documents"]
    queries = data["queries"]
    require(documents.shape == (contract["expected_document_count"], contract["expected_dimensions"]),
            "shared learned-address document shape differs")
    require(queries.shape == (contract["expected_query_count"], contract["expected_dimensions"]),
            "shared learned-address query shape differs")
    require(result.get("e5_manifest_sha256") == data["manifest_sha256"] == contract["e5_manifest_sha256"]
            and result.get("input_manifest_sha256") == data["input_manifest_sha256"] == contract["input_manifest_sha256"],
            "shared learned-address audit frozen roots differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical({
        "schema_version": 2,
        "family": contract["family"],
        "contract_sha256": sha256(contract_path),
        "source_result_sha256": sha256(result_path),
        "audit_writer_sha256": sha256(Path(__file__)),
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "l2_tolerance": contract["l2_tolerance"],
        "documents": summarize(documents, contract["l2_tolerance"]),
        "queries": summarize(queries, contract["l2_tolerance"]),
        "integrity_replay_passed": True,
    }))


def self_test() -> None:
    report = summarize(numpy.asarray([[3.0, 4.0], [5.0, 12.0]], dtype=numpy.float32) / numpy.asarray([[5.0], [13.0]], dtype=numpy.float32), 1.0e-6)
    require(report["count"] == 2 and report["maximum_absolute_error_from_one"] < 1.0e-6,
            "shared learned-address normalization audit self-test differs")
    try:
        summarize(numpy.asarray([[2.0, 0.0]], dtype=numpy.float32), 1.0e-6)
    except ValueError:
        print("shared learned semantic-address normalization audit self-test passed")
        return
    raise ValueError("shared learned-address normalization audit accepted non-unit input")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "shared-learned-semantic-address-normalization-audit.example.json")
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.result_root, args.e5_root, args.input_root, args.output)):
            parser.error("--result-root, --e5-root, --input-root, and --output are required")
        run(args.contract, args.result_root, args.e5_root, args.input_root, args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"audit-shared-learned-semantic-address-normalization: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
