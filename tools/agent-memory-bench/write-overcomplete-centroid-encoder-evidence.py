#!/usr/bin/env python3
"""Fail-closed archival replay for the overcomplete centroid-code ensemble."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
ROOT = THIS.parents[1]
SOURCES = ("tools/agent-memory-bench/centroid-encoder-intrinsic.example.json", "tools/agent-memory-bench/run-centroid-encoder-intrinsic.py", "tools/agent-memory-bench/overcomplete-centroid-encoder.example.json", "tools/agent-memory-bench/plan-overcomplete-centroid-encoder.py", "tools/agent-memory-bench/run-overcomplete-centroid-encoder.py", "tools/agent-memory-bench/write-overcomplete-centroid-encoder-evidence.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load() -> Any:
    path = THIS / "run-overcomplete-centroid-encoder.py"; spec = importlib.util.spec_from_file_location("overcomplete_centroid_evidence_runner", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load overcomplete centroid runner")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


runner = load()


def add(files: dict[str, bytes], name: str, path: Path) -> None:
    require(path.is_file(), f"overcomplete centroid evidence member missing: {name}"); files[name] = path.read_bytes()


def validate(args: argparse.Namespace) -> dict[str, Any]:
    contract = runner.planner.load_contract(args.contract); runner.parent_evidence(args.parent_intrinsic_evidence, contract["parent_intrinsic_evidence_sha256"])
    parent_contract = runner.base.planner.load_contract(THIS / "centroid-encoder-intrinsic.example.json")
    centroids, _, float_identity = runner.base.load_float_artifact(args.float_root, args.float_evidence)
    _, scale_identity = runner.base.load_train(args.scale_root, parent_contract)
    queries, query_identity = runner.base.load_calibration_queries(args.calibration_query_root, parent_contract)
    summary_path = args.result_root / "summary.json"; summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == contract["family"] and summary.get("contract_sha256") == sha256(args.contract) and summary.get("query_count") == 2162 and summary.get("selection_gate") == contract["selection"], "overcomplete centroid summary identity differs")
    float_orders = numpy.empty((2162, 16), dtype=numpy.int16)
    for position, query in enumerate(queries): float_orders[position] = runner.base.stable_score_order(centroids @ query)[:16]
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/summary.json": summary_path.read_bytes(), "bundle/parent-intrinsic-evidence.zip": args.parent_intrinsic_evidence.read_bytes(), "bundle/frozen-float-evidence.zip": args.float_evidence.read_bytes()}
    for source in SOURCES: add(files, f"bundle/measured-source/{source}", ROOT / source)
    for name in ("manifest.json", "query-ids.jsonl", "query-vectors.f32"): add(files, f"bundle/calibration-queries/{name}", args.calibration_query_root / name)
    expected: list[dict[str, Any]] = []
    for encoder in contract["encoders"]:
        for bit_count in contract["bit_counts"]:
            identifier = f"{encoder}-b{bit_count}"; root = args.result_root / identifier; report_path, artifact_path, audit_path = root / "report.json", root / "artifact.npz", root / "audit.npz"
            require(all(path.is_file() for path in (report_path, artifact_path, audit_path)), f"overcomplete centroid output missing: {identifier}")
            config = {"schema_version": 1, "family": contract["family"], "id": identifier, "encoder": encoder, "bit_count": bit_count, "seed": contract["seed"], "itq_iterations": contract["itq_iterations"], "parent_intrinsic_evidence_sha256": contract["parent_intrinsic_evidence_sha256"], **float_identity, **scale_identity, **query_identity, "float_oracle": "exact_float_inner_product_top16_centroids_v1", "binary_tie_rule": "hamming_ascending_then_centroid_id_ascending_v1"}
            mean, projection, training = runner.ensemble(encoder, bit_count, contract["seed"], contract["itq_iterations"], centroids, queries); codes = runner.base.pack((centroids - mean) @ projection.T)
            with numpy.load(artifact_path, allow_pickle=False) as artifact:
                metadata = {"config": config, "training": training, "projection_shape": list(projection.shape), "mean_shape": list(mean.shape), "centroid_code_shape": list(codes.shape)}
                require(json.loads(str(artifact["metadata_json"].item())) == metadata and numpy.array_equal(artifact["mean"], mean) and numpy.array_equal(artifact["projection"], projection) and numpy.array_equal(artifact["centroid_codes"], codes), f"overcomplete centroid artifact replay differs: {identifier}")
            query_codes = runner.base.pack((queries - mean) @ projection.T); orders = numpy.empty((2162, 128), dtype=numpy.int16)
            for position, code in enumerate(query_codes): orders[position] = numpy.lexsort((numpy.arange(4096), runner.base.hamming_distances(codes, code)))[:128]
            with numpy.load(audit_path, allow_pickle=False) as audit: require(set(audit.files) == {"float_top16", "binary_top128"} and numpy.array_equal(audit["float_top16"], float_orders) and numpy.array_equal(audit["binary_top128"], orders), f"overcomplete centroid audit replay differs: {identifier}")
            metrics = {f"float_top16_recall_at_binary_top{shortlist}": float(numpy.mean([numpy.isin(float_orders[position], orders[position, :shortlist]).sum() / 16.0 for position in range(2162)], dtype=numpy.float64)) for shortlist in contract["shortlist_sizes"]}
            report = json.loads(report_path.read_text(encoding="utf-8")); require(report.get("schema_version") == 1 and report.get("family") == contract["family"] and report.get("config") == config and report.get("training") == training and report.get("artifact_sha256") == sha256(artifact_path) and report.get("audit_sha256") == sha256(audit_path) and report.get("query_count") == 2162 and all(report.get(key) == value for key, value in metrics.items()), f"overcomplete centroid report differs: {identifier}")
            expected.append(report)
            for category, path in (("reports", report_path), ("artifacts", artifact_path), ("audits", audit_path)): add(files, f"bundle/{category}/{identifier}/{path.name}", path)
    expected.sort(key=lambda row: row["config"]["id"]); require(summary.get("rows") == expected, "overcomplete centroid summary rows differ")
    gate = contract["selection"]; selected = [row for row in expected if row["float_top16_recall_at_binary_top64"] >= gate["minimum_top64_recall"] and row["float_top16_recall_at_binary_top32"] >= gate["minimum_top32_recall"]]; selected.sort(key=lambda row: (-row["float_top16_recall_at_binary_top64"], -row["float_top16_recall_at_binary_top32"], row["config"]["bit_count"], row["config"]["encoder"]))
    expected_selected = [{"id": row["config"]["id"], "report_sha256": sha256(args.result_root / row["config"]["id"] / "report.json"), "artifact_sha256": row["artifact_sha256"], "top32": row["float_top16_recall_at_binary_top32"], "top64": row["float_top16_recall_at_binary_top64"]} for row in selected[:gate["maximum_selected_configurations"]]]
    require(summary.get("selected") == expected_selected, "overcomplete centroid selection replay differs")
    return {"schema_version": 1, "family": "overcomplete_centroid_encoder_evidence_v1", "contract_sha256": sha256(args.contract), "parent_intrinsic_evidence_sha256": sha256(args.parent_intrinsic_evidence), "row_count": len(expected), "selected_count": len(expected_selected), "rows": expected, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files"); files["bundle/evidence-manifest.json"] = canonical(manifest); path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_STORED if name.endswith(".zip") else zipfile.ZIP_DEFLATED; archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        first, second = Path(directory) / "one.zip", Path(directory) / "two.zip"; payload = {"schema_version": 1, "family": "overcomplete_centroid_encoder_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": {"bundle/value": b"value"}}
        write_archive(first, payload.copy()); write_archive(second, payload.copy()); require(first.read_bytes() == second.read_bytes(), "overcomplete centroid archive determinism differs")
    print("overcomplete centroid encoder evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "overcomplete-centroid-encoder.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--parent-intrinsic-evidence", type=Path); parser.add_argument("--scale-root", type=Path); parser.add_argument("--float-root", type=Path); parser.add_argument("--float-evidence", type=Path); parser.add_argument("--calibration-query-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if any(value is None for value in (args.result_root, args.parent_intrinsic_evidence, args.scale_root, args.float_root, args.float_evidence, args.calibration_query_root, args.output)): parser.error("all overcomplete evidence inputs are required")
        write_archive(args.output, validate(args)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-overcomplete-centroid-encoder-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
