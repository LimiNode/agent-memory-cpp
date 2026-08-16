#!/usr/bin/env python3
"""Run the frozen native ANN backends on an untouched, real-document scale set."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
FAMILY = "native_ann_confirmation_scale_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "native ANN confirmation contract identity differs")
    frozen = value.get("frozen_calibration")
    require(frozen == {"measured_source_ref": "7022e5927f26c0c3ef36e31edc5ca9dab1722de0", "selection_sha256": "368d3dce5c8fc566c234c033f9f581e4df24800f65ee495238d55ae075c6d083", "evidence_zip_sha256": "e7dcb9f0c56e10f5794f24a1f630b5fb5a699bbcc498e76040d9a43abd8be220"}, "native ANN frozen calibration provenance differs")
    require(value.get("frozen_representation") == {"itq_seed": 52, "itq_iterations": 50, "code_bits": 256}, "native ANN frozen representation differs")
    backends = value.get("frozen_backends", {})
    require(backends.get("mih", {}).get("band_widths") == [14] * 9 + [13] * 10 and backends["mih"].get("local_radii") == [2] * 19, "native ANN frozen MIH differs")
    require(backends.get("flat") == {"id": "binary-flat-256"}, "native ANN frozen Flat differs")
    require(backends.get("hnsw") == {"id": "binary-hnsw-m16-ef768", "connectivity": 16, "ef_construction": 200, "ef_search": 768, "seed": 20260815, "pinned_revision": "3f3429661187e4c24a490a0f148fc6bc89042b3d"}, "native ANN frozen HNSW differs")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "native ANN frozen cascade differs")
    require(value.get("native_timing") == {"query_seed": 20260822, "warmup_count": 1, "repeat_count": 7}, "native ANN confirmation timing differs")
    fresh = value.get("fresh_miracl", {})
    require(fresh.get("language") == "de" and fresh.get("sampling", {}).get("train_documents_per_language") == 25000 and fresh["sampling"].get("seed") == 20260822, "native ANN fresh split differs")
    require(value.get("scales") == [{"id": "de-25k", "evaluation_distractors_per_language": 21897, "expected_evaluation_documents": 25000}, {"id": "de-100k", "evaluation_distractors_per_language": 96897, "expected_evaluation_documents": 100000}, {"id": "de-1m", "evaluation_distractors_per_language": 996897, "expected_evaluation_documents": 1000000}], "native ANN scale contract differs")
    require(value.get("selection") == "forbidden_on_fresh_data_or_per_scale" and value.get("comparison") == "one_frozen_backend_comparison_per_scale_after_materialization", "native ANN confirmation decision rule differs")
    return value


def scale(contract: dict[str, Any], identifier: str) -> dict[str, Any]:
    for value in contract["scales"]:
        if value["id"] == identifier:
            return value
    raise ValueError(f"unknown scale: {identifier}")


def preparation_config(contract: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    fresh = contract["fresh_miracl"]
    return {"schema_version": 1, "dataset": fresh["dataset"], "languages": [fresh["language"]], "layout": fresh["layout"], "sampling": {**fresh["sampling"], "evaluation_distractors_per_language": current["evaluation_distractors_per_language"]}, "split": {"purpose": "evaluation", "evaluation_qrels_split": "dev"}, "embedding": fresh["embedding"]}


def preparation_config_hash(config: dict[str, Any]) -> str:
    canonical = {**config, "sampling": {**config["sampling"]}}
    canonical["sampling"].setdefault("evaluation_queries_per_language", 0)
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_prepared_contract(contract: dict[str, Any], current: dict[str, Any], prepared_config: dict[str, Any], prepared_manifest: dict[str, Any]) -> None:
    expected_config = preparation_config(contract, current)
    require(prepared_config == expected_config, "native ANN prepared config differs")
    require(prepared_manifest.get("input_config_hash") == preparation_config_hash(prepared_config), "native ANN prepared manifest config binding differs")
    require(prepared_manifest.get("outputs", {}).get("evaluation_documents", {}).get("count") == current["expected_evaluation_documents"], "native ANN prepared document count differs")


def read_ids(path: Path) -> set[str]:
    return {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line}


def validate_materialized_scale(manifest: dict[str, Any], prepared_manifest: dict[str, Any], prepared_sha: str, expected_documents: int) -> None:
    outputs = manifest.get("outputs", {})
    require(manifest.get("prepared_study_manifest_sha256") == prepared_sha and outputs.get("prepared_study_manifest", {}).get("sha256") == prepared_sha, "native ANN fresh materialization provenance differs")
    require(outputs.get("evaluation_document_ids", {}).get("count") == expected_documents and outputs.get("evaluation_document_vectors", {}).get("count") == expected_documents and prepared_manifest.get("outputs", {}).get("evaluation_documents", {}).get("count") == expected_documents, "native ANN fresh document count differs")


def validate_fresh_root(calibration_root: Path, fresh_root: Path, contract: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    manifest = json.loads((fresh_root / "manifest.json").read_text(encoding="utf-8"))
    prepared_path = fresh_root / "prepared-study-manifest.json"
    prepared_manifest = json.loads(prepared_path.read_text(encoding="utf-8"))
    scale_root = fresh_root.parent
    prepared_config = json.loads((scale_root / "prepared-config.json").read_text(encoding="utf-8"))
    original_prepared_path = scale_root / "prepared" / "manifest.json"
    require(sha256(original_prepared_path) == sha256(prepared_path), "native ANN copied prepared manifest differs")
    validate_prepared_contract(contract, current, prepared_config, prepared_manifest)
    validate_materialized_scale(manifest, prepared_manifest, sha256(prepared_path), current["expected_evaluation_documents"])
    calibration_documents = read_ids(calibration_root / "evaluation-document-ids.jsonl")
    calibration_queries = {json.loads(line)["id"] for line in (calibration_root / "evaluation-query-ids.jsonl").read_text(encoding="utf-8").splitlines() if line}
    fresh_documents = read_ids(fresh_root / "evaluation-document-ids.jsonl")
    fresh_queries = {json.loads(line)["id"] for line in (fresh_root / "evaluation-query-ids.jsonl").read_text(encoding="utf-8").splitlines() if line}
    require(not calibration_documents.intersection(fresh_documents) and not calibration_queries.intersection(fresh_queries), "native ANN fresh split overlaps calibration identifiers")
    return manifest


def backend_treatments(contract: dict[str, Any]) -> list[dict[str, Any]]:
    backends = contract["frozen_backends"]
    return [{"id": backends["mih"]["id"], "backend": "mih", "band_widths": backends["mih"]["band_widths"], "local_radii": backends["mih"]["local_radii"]}, {"id": backends["flat"]["id"], "backend": "flat", "band_widths": backends["mih"]["band_widths"], "local_radii": backends["mih"]["local_radii"]}, {"id": backends["hnsw"]["id"], "backend": "hnsw", "band_widths": backends["mih"]["band_widths"], "local_radii": backends["mih"]["local_radii"], "hnsw_connectivity": backends["hnsw"]["connectivity"], "hnsw_ef_construction": backends["hnsw"]["ef_construction"], "hnsw_ef_search": backends["hnsw"]["ef_search"], "hnsw_seed": backends["hnsw"]["seed"]}]


def native_config(contract: dict[str, Any], input_root: Path, export: Path, query_count: int, treatment: dict[str, Any]) -> dict[str, Any]:
    timing, cascade = contract["native_timing"], contract["cascade"]
    config = {"input_directory": str(input_root.resolve()), "backend": treatment["backend"], "band_widths": treatment["band_widths"], "local_radii": treatment["local_radii"], "query_count": query_count, "query_seed": timing["query_seed"], "warmup_count": timing["warmup_count"], "repeat_count": timing["repeat_count"], "hamming_limit": cascade["hamming_limit"], "adc_limit": cascade["adc_limit"], "exact_limit": cascade["exact_limit"], "shortlist_output": str(export.resolve())}
    for name in ("hnsw_connectivity", "hnsw_ef_construction", "hnsw_ef_search", "hnsw_seed"):
        if name in treatment:
            config[name] = treatment[name]
    return config


def prepare(args: Any, contract: dict[str, Any], current: dict[str, Any]) -> Path:
    root = args.output_root / current["id"] / "prepared"
    config_path = args.output_root / current["id"] / "prepared-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(preparation_config(contract, current), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    subprocess.run([str(args.python), str(THIS / "prepare-miracl-ae-study.py"), "--config", str(config_path), "--input-root", str(args.source_root), "--output-root", str(root)], check=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    validate_prepared_contract(contract, current, json.loads(config_path.read_text(encoding="utf-8")), manifest)
    return root


def materialize(args: Any, prepared_root: Path, current: dict[str, Any]) -> Path:
    root = args.output_root / current["id"] / "e5"
    subprocess.run([str(args.python), str(THIS / "materialize-prepared-e5.py"), "--prepared-root", str(prepared_root), "--output-root", str(root), "--batch-size", str(args.batch_size), "--thread-count", str(args.thread_count), "--cache-dir", str(args.cache_dir), "--local-files-only"], check=True)
    return root


def write_result(contract_path: Path, contract: dict[str, Any], current: dict[str, Any], fresh_root: Path, output: Path, rows: list[dict[str, Any]]) -> None:
    result = {"schema_version": 1, "family": "native_ann_confirmation_scale_result_v1", "contract_sha256": sha256(contract_path), "scale": current, "fresh_e5_manifest_sha256": sha256(fresh_root / "manifest.json"), "fresh_prepared_manifest_sha256": sha256(fresh_root.parent / "prepared" / "manifest.json"), "fresh_identifier_disjointness_checked": True, "frozen_backends": backend_treatments(contract), "rows": rows, "selection": "forbidden"}
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def completed_rows(contract: dict[str, Any], output: Path, input_root: Path, query_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for treatment in backend_treatments(contract):
        identifier = treatment["id"]
        config_path = output / "configs" / f"{identifier}.json"
        report_path = output / "native-reports" / f"{identifier}.json"
        export_path = output / "shortlists" / f"{identifier}.json"
        quality_path = output / "quality" / f"{identifier}.json"
        contributions = output / "contributions" / f"{identifier}.npz"
        config = native_config(contract, input_root, export_path, query_count, treatment)
        require(json.loads(config_path.read_text(encoding="utf-8")) == config, f"confirmation native config differs: {identifier}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report.get("hamming_shortlist_export", {}).get("sha256") == sha256(export_path) and report.get("backend", {}).get("name") == treatment["backend"], f"confirmation native report differs: {identifier}")
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        require(quality.get("shortlist_export_sha256") == sha256(export_path) and quality.get("per_query_contributions_sha256") == sha256(contributions), f"confirmation quality provenance differs: {identifier}")
        rows.append({"id": identifier, "backend": treatment["backend"], "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(report_path), "shortlist_export_sha256": sha256(export_path), "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions), "backend_specific_bytes": report["backend"]["backend_index_logical_bytes"], "candidate_generator_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"], "cascade_ms_per_query": report["latency_ms_per_query"]["cascade_total"]})
    return rows


def compare(args: Any, contract: dict[str, Any], current: dict[str, Any], fresh_root: Path) -> None:
    manifest = validate_fresh_root(args.calibration_root, fresh_root, contract, current)
    output = args.output_root / current["id"] / "comparison"; output.mkdir(parents=True, exist_ok=True)
    input_root = output / "input"
    representation = contract["frozen_representation"]
    subprocess.run([str(args.python), str(THIS / "materialize-mih-storage-input.py"), "materialize", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(fresh_root), "--output", str(input_root), "--code-bits", str(representation["code_bits"]), "--seed", str(representation["itq_seed"]), "--itq-iterations", str(representation["itq_iterations"])], check=True)
    input_manifest = json.loads((input_root / "manifest.json").read_text(encoding="utf-8"))
    query_count = int(input_manifest["query_count"])
    rows: list[dict[str, Any]] = []
    for treatment in backend_treatments(contract):
        config_path = output / "configs" / f"{treatment['id']}.json"; report_path = output / "native-reports" / f"{treatment['id']}.json"; export_path = output / "shortlists" / f"{treatment['id']}.json"; quality_path = output / "quality" / f"{treatment['id']}.json"; contributions = output / "contributions" / f"{treatment['id']}.npz"
        config_path.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True); export_path.parent.mkdir(parents=True, exist_ok=True)
        config = native_config(contract, input_root, export_path, query_count, treatment)
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        subprocess.run([str(args.executable), str(config_path), str(report_path)], check=True)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report.get("hamming_shortlist_export", {}).get("sha256") == sha256(export_path) and report.get("backend", {}).get("name") == treatment["backend"], f"confirmation native report differs: {treatment['id']}")
        subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(fresh_root), "--shortlist-export", str(export_path), "--output", str(quality_path), "--contributions-output", str(contributions), "--hamming-limit", str(contract["cascade"]["hamming_limit"]), "--adc-limit", str(contract["cascade"]["adc_limit"]), "--oracle-k", str(contract["cascade"]["oracle_k"]), "--oracle-cache", str(output / "quality" / "full-e5-oracle.npz")], check=True)
        rows.append({"id": treatment["id"], "backend": treatment["backend"], "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(report_path), "shortlist_export_sha256": sha256(export_path), "quality_report_sha256": sha256(quality_path), "contributions_sha256": sha256(contributions), "backend_specific_bytes": report["backend"]["backend_index_logical_bytes"], "candidate_generator_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"], "cascade_ms_per_query": report["latency_ms_per_query"]["cascade_total"]})
    write_result(args.contract, contract, current, fresh_root, output, rows)


def finalize(args: Any, contract: dict[str, Any], current: dict[str, Any], fresh_root: Path) -> None:
    validate_fresh_root(args.calibration_root, fresh_root, contract, current)
    output = args.output_root / current["id"] / "comparison"
    input_manifest = json.loads((output / "input" / "manifest.json").read_text(encoding="utf-8"))
    rows = completed_rows(contract, output, output / "input", int(input_manifest["query_count"]))
    write_result(args.contract, contract, current, fresh_root, output, rows)


def self_test() -> int:
    try:
        contract = load_contract(THIS / "native-ann-confirmation-scale.example.json")
        current = scale(contract, "de-25k")
        config = preparation_config(contract, current)
        require(config["sampling"]["evaluation_distractors_per_language"] == 21897, "confirmation preparation scale differs")
        require([item["id"] for item in backend_treatments(contract)] == ["mih-m19-fixed-r56", "binary-flat-256", "binary-hnsw-m16-ef768"], "confirmation frozen backend order differs")
        test_current = {**current, "expected_evaluation_documents": 2}
        prepared_sha = "prepared"
        prepared_manifest = {"input_config_hash": preparation_config_hash(config), "outputs": {"evaluation_documents": {"count": 2}}}
        validate_prepared_contract(contract, test_current, config, prepared_manifest)
        validate_materialized_scale({"prepared_study_manifest_sha256": prepared_sha, "outputs": {"prepared_study_manifest": {"sha256": prepared_sha}, "evaluation_document_ids": {"count": 2}, "evaluation_document_vectors": {"count": 2}}}, prepared_manifest, prepared_sha, 2)
        for mutated in ({**config, "sampling": {**config["sampling"], "seed": config["sampling"]["seed"] + 1}}, {**config, "sampling": {**config["sampling"], "evaluation_distractors_per_language": config["sampling"]["evaluation_distractors_per_language"] + 1}}):
            try:
                validate_prepared_contract(contract, current, mutated, prepared_manifest)
            except ValueError:
                pass
            else:
                raise ValueError("confirmation prepared config mutation was accepted")
        wrong_manifest = {**prepared_manifest, "input_config_hash": "0" * 64}
        try:
            validate_prepared_contract(contract, current, config, wrong_manifest)
        except ValueError:
            pass
        else:
            raise ValueError("confirmation prepared manifest mutation was accepted")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-native-ann-confirmation-scale self-test failed: {error}", file=sys.stderr)
        return 1
    print("run-native-ann-confirmation-scale self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    for name in ("prepare", "materialize", "compare", "finalize"):
        command = commands.add_parser(name)
        command.add_argument("--scale", required=True)
        command.add_argument("--output-root", type=Path, required=True)
        command.add_argument("--python", type=Path, default=Path(sys.executable))
        command.add_argument("--contract", type=Path, default=THIS / "native-ann-confirmation-scale.example.json")
        if name == "prepare": command.add_argument("--source-root", type=Path, required=True)
        if name == "materialize": command.add_argument("--batch-size", type=int, default=64); command.add_argument("--thread-count", type=int, default=8); command.add_argument("--cache-dir", type=Path, required=True)
        if name in ("compare", "finalize"): command.add_argument("--calibration-root", type=Path, required=True)
        if name == "compare": command.add_argument("--executable", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test": return self_test()
        contract = load_contract(args.contract); current = scale(contract, args.scale)
        if args.command == "prepare": prepare(args, contract, current)
        elif args.command == "materialize": materialize(args, args.output_root / current["id"] / "prepared", current)
        elif args.command == "compare": compare(args, contract, current, args.output_root / current["id"] / "e5")
        else: finalize(args, contract, current, args.output_root / current["id"] / "e5")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-native-ann-confirmation-scale: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
