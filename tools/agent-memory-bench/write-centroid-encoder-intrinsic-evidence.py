#!/usr/bin/env python3
"""Fail-closed archive validator for the centroid-encoder intrinsic matrix."""

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
SOURCES = (
    "tools/agent-memory-bench/centroid-encoder-intrinsic.example.json",
    "tools/agent-memory-bench/plan-centroid-encoder-intrinsic.py",
    "tools/agent-memory-bench/materialize-centroid-calibration-queries.py",
    "tools/agent-memory-bench/run-centroid-encoder-intrinsic.py",
    "tools/agent-memory-bench/write-centroid-encoder-intrinsic-evidence.py",
    "tools/agent-memory-bench/materialize-prepared-e5.py",
)


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
    path = THIS / "run-centroid-encoder-intrinsic.py"
    spec = importlib.util.spec_from_file_location("centroid_encoder_intrinsic_evidence_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load centroid encoder intrinsic runner")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


runner = load()


def add(files: dict[str, bytes], name: str, path: Path) -> None:
    require(path.is_file(), f"centroid encoder evidence member missing: {name}")
    files[name] = path.read_bytes()


def validate(args: argparse.Namespace) -> dict[str, Any]:
    contract = runner.planner.load_contract(args.contract)
    raw_source = args.calibration_query_source
    require(raw_source.is_file() and sha256(raw_source) == contract["calibration_queries"]["sha256"], "centroid encoder raw calibration query source differs")
    centroids, _, float_identity = runner.load_float_artifact(args.float_root, args.float_evidence)
    train, scale_identity = runner.load_train(args.scale_root, contract)
    queries, query_identity = runner.load_calibration_queries(args.calibration_query_root, contract)
    summary_path = args.result_root / "summary.json"; summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == contract["family"] and summary.get("contract_sha256") == sha256(args.contract) and summary.get("query_count") == 2162 and summary.get("selection_gate") == contract["selection"], "centroid encoder summary identity differs")
    rows = summary.get("rows"); require(isinstance(rows, list) and len(rows) == 15, "centroid encoder summary row count differs")
    float_orders = numpy.empty((queries.shape[0], 16), dtype=numpy.int16)
    for position, query in enumerate(queries):
        float_orders[position] = runner.stable_score_order(centroids @ query)[:16]
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/summary.json": summary_path.read_bytes(), "bundle/frozen-float-semantic-ivf/evidence.zip": args.float_evidence.read_bytes(), "bundle/calibration-queries/raw-topics.tsv": raw_source.read_bytes()}
    for source in SOURCES:
        add(files, f"bundle/measured-source/{source}", ROOT / source)
    calibration_manifest = args.calibration_query_root / "manifest.json"; calibration_ids = args.calibration_query_root / "query-ids.jsonl"; calibration_vectors = args.calibration_query_root / "query-vectors.f32"
    for path in (calibration_manifest, calibration_ids, calibration_vectors): add(files, f"bundle/calibration-queries/{path.name}", path)
    index_path = args.float_root / "es-1m" / "indexes" / "centroids-4096.faiss"; assignment_path = args.float_root / "es-1m" / "assignments" / "centroids-4096.npy"
    add(files, f"bundle/frozen-float-semantic-ivf/{index_path.name}", index_path); add(files, f"bundle/frozen-float-semantic-ivf/{assignment_path.name}", assignment_path)
    expected_rows: list[dict[str, Any]] = []
    for encoder in contract["encoders"]:
        for bit_count in contract["bit_counts"]:
            identifier = f"{encoder}-b{bit_count}"; root = args.result_root / identifier
            report_path, artifact_path, audit_path = root / "report.json", root / "artifact.npz", root / "audit.npz"
            require(all(path.is_file() for path in (report_path, artifact_path, audit_path)), f"centroid encoder output missing: {identifier}")
            config = {"schema_version": 1, "family": contract["family"], "id": identifier, "encoder": encoder, "bit_count": bit_count, "seed": contract["seed"], "itq_iterations": contract["itq_iterations"], **float_identity, **scale_identity, **query_identity, "float_oracle": contract["frozen_float_semantic_ivf"]["selection_oracle"], "binary_tie_rule": "hamming_ascending_then_centroid_id_ascending_v1"}
            mean, projection, training = runner.artifact(encoder, bit_count, contract["seed"], contract["itq_iterations"], centroids, train, queries)
            expected_codes = runner.pack((centroids - mean) @ projection.T)
            with numpy.load(artifact_path, allow_pickle=False) as artifact:
                metadata = json.loads(str(artifact["metadata_json"].item()))
                expected_metadata = {"config": config, "training": training, "projection_shape": list(projection.shape), "mean_shape": list(mean.shape), "centroid_code_shape": list(expected_codes.shape)}
                require(metadata == expected_metadata and numpy.array_equal(artifact["mean"], mean) and numpy.array_equal(artifact["projection"], projection) and numpy.array_equal(artifact["centroid_codes"], expected_codes), f"centroid encoder artifact replay differs: {identifier}")
            query_codes = runner.pack((queries - mean) @ projection.T)
            expected_orders = numpy.empty((queries.shape[0], 128), dtype=numpy.int16)
            for position, query_code in enumerate(query_codes):
                distances = runner.hamming_distances(expected_codes, query_code)
                expected_orders[position] = numpy.lexsort((numpy.arange(centroids.shape[0]), distances))[:128]
            with numpy.load(audit_path, allow_pickle=False) as audit:
                require(set(audit.files) == {"float_top16", "binary_top128"} and numpy.array_equal(audit["float_top16"], float_orders) and numpy.array_equal(audit["binary_top128"], expected_orders), f"centroid encoder audit replay differs: {identifier}")
            metrics: dict[str, float] = {}
            for shortlist in contract["shortlist_sizes"]:
                coverage = [numpy.isin(float_orders[position], expected_orders[position, :shortlist]).sum() / 16.0 for position in range(queries.shape[0])]
                metrics[f"float_top16_recall_at_binary_top{shortlist}"] = float(numpy.mean(coverage, dtype=numpy.float64))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            require(report.get("schema_version") == 1 and report.get("family") == contract["family"] and report.get("config") == config and report.get("training") == training and report.get("artifact_sha256") == sha256(artifact_path) and report.get("audit_sha256") == sha256(audit_path) and report.get("query_count") == 2162 and all(report.get(key) == value for key, value in metrics.items()) and all(isinstance(report.get(key), (float, int)) and float(report[key]) >= 0.0 for key in ("binary_centroid_scan_p50_ms_per_query", "binary_centroid_scan_p95_ms_per_query")), f"centroid encoder report differs: {identifier}")
            expected_rows.append(report)
            for name, path in (("reports", report_path), ("artifacts", artifact_path), ("audits", audit_path)):
                add(files, f"bundle/{name}/{identifier}/{path.name}", path)
    expected_rows.sort(key=lambda row: row["config"]["id"])
    require(rows == expected_rows, "centroid encoder summary rows differ")
    gate = contract["selection"]
    selected_rows = [row for row in expected_rows if row["float_top16_recall_at_binary_top64"] >= gate["minimum_top64_recall"] and row["float_top16_recall_at_binary_top32"] >= gate["minimum_top32_recall"]]
    selected_rows.sort(key=lambda row: (-row["float_top16_recall_at_binary_top64"], -row["float_top16_recall_at_binary_top32"], row["config"]["bit_count"], row["config"]["encoder"]))
    expected_selected = [{"id": row["config"]["id"], "report_sha256": sha256(args.result_root / row["config"]["id"] / "report.json"), "artifact_sha256": row["artifact_sha256"], "top32": row["float_top16_recall_at_binary_top32"], "top64": row["float_top16_recall_at_binary_top64"]} for row in selected_rows[:gate["maximum_selected_configurations"]]]
    require(summary.get("selected") == expected_selected, "centroid encoder selection replay differs")
    strict_improvements: list[dict[str, Any]] = []
    for encoder in contract["encoders"]:
        if encoder == "rademacher_sign_control":
            continue
        at_256 = next(row for row in expected_rows if row["config"]["encoder"] == encoder and row["config"]["bit_count"] == 256)
        at_384 = next(row for row in expected_rows if row["config"]["encoder"] == encoder and row["config"]["bit_count"] == 384)
        if at_384["float_top16_recall_at_binary_top64"] > at_256["float_top16_recall_at_binary_top64"]:
            strict_improvements.append({"encoder": encoder, "from_id": at_256["config"]["id"], "from_top64": at_256["float_top16_recall_at_binary_top64"], "to_id": at_384["config"]["id"], "to_top64": at_384["float_top16_recall_at_binary_top64"]})
    permission = {"predicate": contract["overcomplete_follow_up"]["permitted_only_if"], "permitted": bool(strict_improvements), "supporting_rows": strict_improvements}
    frozen_inputs = {**float_identity, **scale_identity, **query_identity}
    return {"schema_version": 1, "family": "centroid_encoder_intrinsic_evidence_v1", "contract_sha256": sha256(args.contract), "frozen_inputs": frozen_inputs, "overcomplete_follow_up": permission, "row_count": len(expected_rows), "selected_count": len(expected_selected), "rows": expected_rows, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files"); files["bundle/evidence-manifest.json"] = canonical(manifest); path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        first, second = Path(directory) / "first.zip", Path(directory) / "second.zip"
        base = {"schema_version": 1, "family": "centroid_encoder_intrinsic_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": {"bundle/value": b"value"}}
        write_archive(first, base.copy()); write_archive(second, base.copy()); require(first.read_bytes() == second.read_bytes(), "centroid encoder deterministic archive differs")
    print("centroid encoder intrinsic evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "centroid-encoder-intrinsic.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--scale-root", type=Path); parser.add_argument("--float-root", type=Path); parser.add_argument("--float-evidence", type=Path); parser.add_argument("--calibration-query-source", type=Path); parser.add_argument("--calibration-query-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if any(value is None for value in (args.result_root, args.scale_root, args.float_root, args.float_evidence, args.calibration_query_source, args.calibration_query_root, args.output)): parser.error("all evidence inputs are required")
        write_archive(args.output, validate(args)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-centroid-encoder-intrinsic-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
