#!/usr/bin/env python3
"""Fail-closed evidence archive for data-dependent product-locator results."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("data_dependent_product_locator_runner", "run-data-dependent-product-locator.py")
evaluator = load("data_dependent_product_locator_evaluator", "evaluate-native-ann-shortlists.py")


def evaluator_sources() -> dict[str, str]:
    return {
        "evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"),
        "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py"),
    }


def validate_quality(data: dict[str, Any], shortlist_path: Path, quality_path: Path, contribution_path: Path, oracle_path: Path) -> tuple[float, float]:
    _, rows = evaluator.load_export(shortlist_path, len(data["query_ids"]), len(data["document_ids"]), 768, 256)
    exact_top, full_ndcg = evaluator.load_or_create_oracle_cache(data, oracle_path, 10)
    report, expected = evaluator.evaluate(data, rows, 768, 256, 10, exact_top, full_ndcg)
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    with numpy.load(contribution_path, allow_pickle=False) as archive:
        fields = {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}
        require(set(archive.files) == fields and archive["query_ids"].tolist() == data["query_ids"] and json.loads(str(archive["identity_json"].item())) == identity, "product locator contribution identity differs")
        for name, value in expected.items():
            require(numpy.array_equal(archive[name], value), f"product locator contribution replay differs: {name}")
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    sources = evaluator_sources()
    source_bundle = hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("evaluation_materialization_manifest_sha256") == data["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == data["evaluation_qrels_sha256"] and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("shortlist_export_backend") == "data_dependent_product_locator" and quality.get("oracle_cache_sha256") == sha256(oracle_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == sources and quality.get("evaluator_source_bundle_sha256") == source_bundle and quality.get("e5_oracle_survival_after_adc") == report["e5_oracle_survival_after_adc"] and quality.get("reranked_ndcg_at_10") == report["reranked_ndcg_at_10"], "product locator quality binding differs")
    return float(report["e5_oracle_survival_after_adc"]), float(report["reranked_ndcg_at_10"])


def load_scale(scale: dict[str, Any], scale_root: Path, itq_artifact: Path) -> dict[str, Any]:
    root = scale_root / scale["id"]
    input_root, evaluation_root = root / "input", root / "e5"
    input_manifest_path, evaluation_manifest_path = input_root / "manifest.json", evaluation_root / "manifest.json"
    require(sha256(input_manifest_path) == scale["input_manifest_sha256"] and sha256(evaluation_manifest_path) == scale["evaluation_manifest_sha256"], f"product locator frozen manifests differ: {scale['id']}")
    manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    data = evaluator.shared.load_root(evaluation_root)
    train_path = evaluation_root / "train-vectors.f32"
    train = numpy.fromfile(train_path, dtype="<f4").reshape(-1, data["dimension"])
    require(data["manifest_sha256"] == scale["evaluation_manifest_sha256"] and len(data["document_ids"]) == scale["documents"] and len(data["query_ids"]) == 648 and sha256(train_path) == manifest["calibration_train_vectors_sha256"] and evaluator.shared.ordered_ids_sha256(data["train_ids"]) == manifest["calibration_train_ids_sha256"], f"product locator frozen E5 payload differs: {scale['id']}")
    train_bits, itq_hash = runner.train_binary_codes(train, itq_artifact, manifest["itq_artifact_sha256"], sha256(train_path), manifest["calibration_train_ids_sha256"])
    document_codes = runner.packed_codes(input_root / manifest["document_codes_file"], scale["documents"])
    query_codes = runner.packed_codes(input_root / manifest["query_codes_file"], 648)
    return {"root": root, "input_root": input_root, "evaluation_root": evaluation_root, "input_manifest_path": input_manifest_path, "evaluation_manifest_path": evaluation_manifest_path, "manifest": manifest, "data": data, "train": train, "train_bits": train_bits, "itq_hash": itq_hash, "document_codes": document_codes, "query_codes": query_codes, "document_bits": numpy.unpackbits(document_codes, bitorder="little", axis=1), "query_bits": numpy.unpackbits(query_codes, bitorder="little", axis=1), "query_projections": numpy.fromfile(input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256), "adc_centroids": numpy.fromfile(input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2), "train_path": train_path}


def validate_artifact(context: dict[str, Any], artifact_path: Path, scale: str, treatment: str, budget: int) -> tuple[list[numpy.ndarray], list[numpy.ndarray], numpy.ndarray, dict[int, numpy.ndarray]]:
    metadata = runner.artifact_metadata(scale, treatment, budget, sha256(context["input_manifest_path"]), sha256(context["evaluation_manifest_path"]), sha256(context["train_path"]), context["itq_hash"], context["data"]["dimension"])
    positions, books, cells, permutation = runner.load_artifact(artifact_path, metadata, len(context["data"]["document_ids"]))
    if treatment == "float_e5_product":
        require(permutation is None and numpy.array_equal(runner.float_assign(numpy.asarray(context["data"]["documents"], dtype=numpy.float32), positions, books), cells), "product locator float artifact cells differ")
    else:
        expected_permutation = runner.entropy_permutation(context["train_bits"]) if treatment == "permuted_binary_medoids" else numpy.arange(256, dtype=numpy.int16)
        require(permutation is not None and numpy.array_equal(permutation, expected_permutation) and numpy.array_equal(runner.binary_assign(context["document_bits"], positions, books), cells), "product locator binary artifact cells differ")
        for part, book in zip(positions, books, strict=True):
            require(all(numpy.any(numpy.all(context["train_bits"][:, part] == center, axis=1)) for center in book), "product locator binary codebook is not train-derived medoids")
    return positions, books, cells, runner.cell_index(cells)


def validate_row(context: dict[str, Any], output: Path, contract: dict[str, Any], scale: dict[str, Any], treatment: dict[str, Any], budget: int, fraction: float, files: dict[str, bytes]) -> dict[str, Any]:
    treatment_id, target = treatment["id"], int(numpy.ceil(fraction * scale["documents"]))
    identifier = f"{treatment_id}-cells{budget}-target{target}"
    artifact_path = output / "artifacts" / f"{treatment_id}-cells{budget}.npz"
    config_path, audit_path = output / "configs" / f"{identifier}.json", output / "routing-audits" / f"{identifier}.json"
    shortlist_path, quality_path, contribution_path = output / "shortlists" / f"{identifier}.json", output / "quality" / f"{identifier}.json", output / "contributions" / f"{identifier}.npz"
    require(all(path.is_file() for path in (artifact_path, config_path, audit_path, shortlist_path, quality_path, contribution_path)), f"product locator row member missing: {scale['id']}/{identifier}")
    positions, books, _, index = validate_artifact(context, artifact_path, scale["id"], treatment_id, budget)
    expected_config = {"schema_version": 1, "family": runner.FAMILY, "scale": scale["id"], "treatment": treatment, "implicit_cell_budget": budget, "block_count": int(round(numpy.log(budget) / numpy.log(4))), "target_candidate_fraction": fraction, "target_candidate_count": target, "input_manifest_sha256": sha256(context["input_manifest_path"]), "evaluation_manifest_sha256": sha256(context["evaluation_manifest_path"]), "train_vectors_sha256": sha256(context["train_path"]), "itq_artifact_sha256": context["itq_hash"], "artifact_sha256": sha256(artifact_path), "cascade": contract["cascade"], "cell_traversal": contract["routing"], "candidate_union_rule": "document_position_ascending_v1"}
    require(json.loads(config_path.read_text(encoding="utf-8")) == expected_config, f"product locator config differs: {scale['id']}/{identifier}")
    audit = json.loads(audit_path.read_text(encoding="utf-8")); shortlist = json.loads(shortlist_path.read_text(encoding="utf-8"))
    require(audit.get("schema_version") == 1 and audit.get("family") == runner.FAMILY and audit.get("config_sha256") == sha256(config_path) and audit.get("artifact_sha256") == sha256(artifact_path) and shortlist.get("schema_version") == 1 and shortlist.get("family") == "native_ann_hamming_shortlist_export_v1" and shortlist.get("backend") == "data_dependent_product_locator" and shortlist.get("input_manifest_sha256") == sha256(context["input_manifest_path"]) and shortlist.get("hamming_limit") == 768 and shortlist.get("config_sha256") == sha256(config_path) and shortlist.get("artifact_sha256") == sha256(artifact_path), f"product locator export identity differs: {scale['id']}/{identifier}")
    require(len(audit.get("rows", [])) == len(shortlist.get("rows", [])) == 648, f"product locator row count differs: {scale['id']}/{identifier}")
    counts: list[float] = []; probes: list[float] = []; nonempty: list[float] = []
    documents = numpy.asarray(context["data"]["documents"], dtype=numpy.float32)
    queries = numpy.asarray(context["data"]["queries"], dtype=numpy.float32)
    for position, (audit_row, shortlist_row) in enumerate(zip(audit["rows"], shortlist["rows"], strict=True)):
        require(contract["routing_by_treatment"].get(treatment_id) == runner.routing_rule(treatment_id), f"product locator routing metric differs: {treatment_id}")
        costs = runner.local_costs(treatment_id, queries[position], context["query_bits"][position], positions, books)
        candidates, visited, current_probes, current_nonempty, _ = runner.route(costs, index, target)
        require(audit_row == {"query_position": position, "selected_cell_keys": visited, "candidate_count": int(candidates.size), "target_candidate_count": target, "cell_probes": current_probes, "nonempty_cells": current_nonempty}, f"product locator audit replay differs: {scale['id']}/{identifier}/{position}")
        hamming = runner.hamming_positions(context["document_codes"], context["query_codes"][position], candidates)
        adc = runner.adc_positions(context["document_bits"], context["query_projections"][position], context["adc_centroids"], hamming)
        require(shortlist_row == {"query_position": position, "selected_cell_keys": visited, "hamming_shortlist_positions": hamming.tolist(), "binary_adc_positions": adc.tolist()}, f"product locator shortlist replay differs: {scale['id']}/{identifier}/{position}")
        counts.append(float(candidates.size)); probes.append(float(current_probes)); nonempty.append(float(current_nonempty))
    survival, ndcg = validate_quality(context["data"], shortlist_path, quality_path, contribution_path, output / "oracle.npz")
    row = {"scale": scale["id"], "id": identifier, "treatment": treatment_id, "implicit_cell_budget": budget, "target_candidate_fraction": fraction, "target_candidate_count": target, "status": "measured", "actual_candidate_fraction": float(numpy.mean(counts)) / scale["documents"], "candidate_count_p95": runner.percentile(counts, .95), "cell_probes_p50": runner.percentile(probes, .50), "cell_probes_p95": runner.percentile(probes, .95), "nonempty_cells_p50": runner.percentile(nonempty, .50), "nonempty_cells_p95": runner.percentile(nonempty, .95), "config_sha256": sha256(config_path), "artifact_sha256": sha256(artifact_path), "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "contribution_sha256": sha256(contribution_path), "routing_audit_sha256": sha256(audit_path), "e5_oracle_survival_after_adc": survival, "reranked_ndcg_at_10": ndcg}
    for name in ("routing_p50_ms_per_query", "routing_p95_ms_per_query"):
        require(isinstance(row.get(name), type(None)), "internal product locator validator error")
    for category, path in (("artifacts", artifact_path), ("configs", config_path), ("routing-audits", audit_path), ("shortlists", shortlist_path), ("quality", quality_path), ("contributions", contribution_path)):
        files[f"bundle/{scale['id']}/{category}/{path.name}"] = path.read_bytes()
    return row


def validate(result_root: Path, scale_root: Path, itq_artifact: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    summary_path = result_root / "summary.json"; summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") in {sha256(contract_path), contract["amends_measurement_contract_sha256"]} and summary.get("itq_artifact_sha256") == sha256(itq_artifact), "product locator summary identity differs")
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/itq-256-artifact.npz": itq_artifact.read_bytes(), "bundle/summary.json": summary_path.read_bytes()}
    expected: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        context = load_scale(scale, scale_root, itq_artifact); output = result_root / scale["id"]
        files[f"bundle/{scale['id']}/input/manifest.json"] = context["input_manifest_path"].read_bytes()
        files[f"bundle/{scale['id']}/e5/manifest.json"] = context["evaluation_manifest_path"].read_bytes()
        for treatment in contract["treatments"]:
            for budget in contract["implicit_cell_budgets"]:
                for fraction in contract["target_candidate_fractions"]:
                    row = validate_row(context, output, contract, scale, treatment, budget, fraction, files)
                    actual = next((item for item in summary["rows"] if item.get("scale") == row["scale"] and item.get("id") == row["id"]), None)
                    require(actual is not None and all(actual.get(name) == value for name, value in row.items()), f"product locator summary replay differs: {row['scale']}/{row['id']}")
                    for name in ("routing_p50_ms_per_query", "routing_p95_ms_per_query"):
                        require(isinstance(actual.get(name), (int, float)) and actual[name] >= 0.0, f"product locator timing differs: {row['scale']}/{row['id']}")
                    measurement_path = output / "measurements" / f"{row['id']}.json"
                    if measurement_path.is_file():
                        require(json.loads(measurement_path.read_text(encoding="utf-8")) == actual, f"product locator persisted measurement differs: {row['scale']}/{row['id']}")
                        files[f"bundle/{scale['id']}/measurements/{measurement_path.name}"] = measurement_path.read_bytes()
                    expected.append(actual)
    require(len(summary["rows"]) == len(expected) == 54, "product locator matrix differs")
    for filename in ("run-data-dependent-product-locator.py", "write-data-dependent-product-locator-evidence.py", "plan-data-dependent-product-locator.py", "evaluate-native-ann-shortlists.py", "evaluate-projection-quantization.py"):
        files[f"bundle/sources/{filename}"] = (THIS / filename).read_bytes()
    return {"schema_version": 1, "family": "data_dependent_product_locator_evidence_v1", "contract_sha256": sha256(contract_path), "summary_contract_sha256": summary["contract_sha256"], "row_count": len(expected), "rows": sorted(expected, key=lambda item: (item["scale"], item["id"])), "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, evidence: dict[str, Any]) -> None:
    files = dict(evidence.pop("_files"))
    files["bundle/evidence-manifest.json"] = canonical(evidence)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o100644 << 16
            archive.writestr(info, value, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def self_test() -> None:
    require(sha256_bytes(b"abc") == hashlib.sha256(b"abc").hexdigest(), "product locator evidence digest differs")
    print("data-dependent product locator evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "data-dependent-product-locator.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--scale-root", type=Path); parser.add_argument("--itq-artifact", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if any(value is None for value in (args.result_root, args.scale_root, args.itq_artifact, args.output)): parser.error("--result-root, --scale-root, --itq-artifact, and --output are required")
        write_archive(args.output, validate(args.result_root, args.scale_root, args.itq_artifact, args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, evaluator.EvaluationError) as error:
        print(f"write-data-dependent-product-locator-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
