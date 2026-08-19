#!/usr/bin/env python3
"""Prepare, materialize and measure the frozen scale-aware native MIH sweep."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
FAMILY = "scale_aware_native_mih_protocol_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(filename: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preflight = load_module("preflight-scale-aware-native-mih.py", "scale_aware_preflight")


def load_contract(path: Path) -> dict[str, Any]:
    value = preflight.load_contract(path)
    require(value.get("family") == FAMILY, "scale-aware sweep contract identity differs")
    gates = value["selection_gates"]
    require(gates.get("bootstrap_replicates") == 10000 and gates.get("bootstrap_seed_base") == 20260827 and gates.get("confidence_level") == 0.95, "scale-aware bootstrap contract differs")
    return value


def scale(contract: dict[str, Any], identifier: str) -> dict[str, Any]:
    for current in contract["scales"]:
        if current["id"] == identifier:
            return current
    raise ValueError(f"unknown scale: {identifier}")


def qrel_document_count(source_root: Path, contract: dict[str, Any]) -> int:
    layout = contract["miracl_source"]["layout"]
    path = source_root / layout["qrels"].format(language=contract["calibration_dataset"]["language"])
    identifiers: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.split()
            require(len(fields) >= 3, "scale-aware qrels row is invalid")
            identifiers.add(fields[2])
    require(identifiers, "scale-aware qrels are empty")
    return len(identifiers)


def preparation_config(contract: dict[str, Any], current: dict[str, Any], qrel_count: int) -> dict[str, Any]:
    calibration = contract["calibration_dataset"]
    document_count = current["documents"]
    require(qrel_count <= document_count, "scale-aware target scale cannot retain all qrels")
    return {
        "schema_version": 1,
        "dataset": contract["miracl_source"],
        "languages": [calibration["language"]],
        "layout": contract["miracl_source"]["layout"],
        "sampling": {
            "strategy": "balanced_stable_hash",
            "seed": calibration["sampling_seed"],
            "train_documents_per_language": calibration["train_documents"],
            "evaluation_distractors_per_language": document_count - qrel_count,
            "evaluation_queries_per_language": calibration["evaluation_queries"],
        },
        "split": {"purpose": "evaluation", "evaluation_qrels_split": calibration["split"]},
        "embedding": {
            "model_id": contract["representation"]["model_id"],
            "model_revision": contract["representation"]["model_revision"],
            "document_prefix": "passage: ",
            "query_prefix": "query: ",
            "normalized": True,
        },
    }


def read_ids(path: Path) -> set[str]:
    # JSONL records are delimited by physical LF/CRLF bytes.  str.splitlines()
    # additionally treats valid Unicode text characters such as U+0085 as a
    # record boundary, which corrupts documents containing those characters.
    with path.open("r", encoding="utf-8", newline=None) as stream:
        return {json.loads(line)["id"] for line in stream if line.strip()}


def prepare(args: Any, contract: dict[str, Any]) -> None:
    qrel_count = qrel_document_count(args.source_root, contract)
    previous: set[str] | None = None
    for current in contract["scales"]:
        root = args.output_root / current["id"]
        config_path = root / "prepared-config.json"
        prepared = root / "prepared"
        root.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(preparation_config(contract, current, qrel_count), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        subprocess.run([str(args.python), str(THIS / "prepare-miracl-ae-study.py"), "--config", str(config_path), "--input-root", str(args.source_root), "--output-root", str(prepared)], check=True)
        manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
        require(manifest["outputs"]["evaluation_documents"]["count"] == current["documents"], "scale-aware prepared document count differs")
        identifiers = read_ids(prepared / "evaluation-documents.jsonl")
        require(len(identifiers) == current["documents"], "scale-aware prepared identifiers differ")
        if previous is not None:
            require(previous.issubset(identifiers), "scale-aware corpus sets are not nested")
        previous = identifiers


def materialize(args: Any, contract: dict[str, Any]) -> None:
    previous_train_sha: str | None = None
    for current in contract["scales"]:
        root = args.output_root / current["id"]
        subprocess.run([str(args.python), str(THIS / "materialize-prepared-e5.py"), "--prepared-root", str(root / "prepared"), "--output-root", str(root / "e5"), "--batch-size", str(args.batch_size), "--thread-count", str(args.thread_count), "--cache-dir", str(args.cache_dir), "--local-files-only"], check=True)
        manifest = json.loads((root / "e5" / "manifest.json").read_text(encoding="utf-8"))
        train_sha = manifest["outputs"]["train_vectors"]["sha256"]
        if previous_train_sha is not None:
            require(previous_train_sha == train_sha, "scale-aware ITQ training vectors differ across scales")
        previous_train_sha = train_sha


def bootstrap(values: numpy.ndarray, reference: numpy.ndarray | None, replicates: int, seed: int, confidence: float) -> float:
    rng = numpy.random.default_rng(seed)
    samples: list[numpy.ndarray] = []
    for begin in range(0, replicates, 250):
        count = min(250, replicates - begin)
        positions = rng.integers(0, values.size, size=(count, values.size), endpoint=False)
        estimate = values[positions].mean(axis=1)
        if reference is not None:
            estimate = estimate / reference[positions].mean(axis=1)
        samples.append(estimate)
    return float(numpy.quantile(numpy.concatenate(samples), (1.0 - confidence) / 2.0, method="higher"))


def mih_treatments(contract: dict[str, Any], current: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = {(row["scale_id"], row["m"]): row for row in report["rows"]}
    result: list[dict[str, Any]] = []
    for band_count in current["mih_m_values"]:
        row = rows[(current["id"], band_count)]
        if row["status"] != "admissible_for_native_matrix":
            continue
        for implementation in contract["native_implementation_matrix"]:
            result.append({"id": f"mih-m{band_count}-{implementation['directory_mode']}-{implementation['deduplication_mode']}", "backend": "mih", "band_widths": row["band_widths"], "local_radii": row["local_radii"], **implementation})
    return result


def treatments(contract: dict[str, Any], current: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    control_widths = preflight.near_equal_widths(256, 19)
    control_radii = preflight.minimum_probe_radii(control_widths)
    result = mih_treatments(contract, current, report)
    result.append({"id": "binary-flat-256", "backend": "flat", "band_widths": control_widths, "local_radii": control_radii, "directory_mode": "sorted_lower_bound", "deduplication_mode": "two_pass_generation_array"})
    for connectivity in contract["hnsw_calibration"]["connectivity"]:
        for ef_search in contract["hnsw_calibration"]["ef_search"]:
            result.append({"id": f"binary-hnsw-m{connectivity}-ef{ef_search}", "backend": "hnsw", "band_widths": control_widths, "local_radii": control_radii, "directory_mode": "sorted_lower_bound", "deduplication_mode": "two_pass_generation_array", "hnsw_connectivity": connectivity, "hnsw_ef_construction": contract["hnsw_calibration"]["ef_construction"], "hnsw_ef_search": ef_search, "hnsw_seed": contract["hnsw_calibration"]["seed"]})
    return result


def native_config(contract: dict[str, Any], input_root: Path, output: Path, treatment: dict[str, Any]) -> dict[str, Any]:
    cascade, timing = contract["cascade"], contract["native_timing"]
    result = {"input_directory": str(input_root.resolve()), "backend": treatment["backend"], "band_widths": treatment["band_widths"], "local_radii": treatment["local_radii"], "query_count": 0, "query_seed": contract["calibration_dataset"]["sampling_seed"], "warmup_count": timing["warmup_count"], "repeat_count": timing["repeat_count"], "hamming_limit": cascade["hamming_limit"], "adc_limit": cascade["adc_limit"], "exact_limit": cascade["exact_limit"], "directory_mode": treatment["directory_mode"], "deduplication_mode": treatment["deduplication_mode"], "shortlist_output": str(output.resolve())}
    for name in ("hnsw_connectivity", "hnsw_ef_construction", "hnsw_ef_search", "hnsw_seed"):
        if name in treatment:
            result[name] = treatment[name]
    return result


def completed_row(
    config_path: Path,
    report_path: Path,
    shortlist_path: Path,
    quality_path: Path,
    contributions_path: Path,
    input_manifest_sha256: str,
) -> bool:
    """Return whether a prior row is complete and bound to this exact input.

    This permits an interrupted sweep to continue without overwriting a
    completed timing sample.  Missing or malformed artifacts deliberately
    return false, so the caller regenerates the whole row instead.
    """
    if not all(path.is_file() for path in (report_path, shortlist_path, quality_path, contributions_path)):
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        shortlist = json.loads(shortlist_path.read_text(encoding="utf-8"))
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        with numpy.load(contributions_path, allow_pickle=False) as archive:
            required = {"e5_oracle_survival_after_adc", "reranked_ndcg_at_10", "full_e5_ndcg_at_10"}
            if not required.issubset(archive.files):
                return False
        return (
            report.get("benchmark_config_sha256") == sha256(config_path)
            and report.get("input_manifest_sha256") == input_manifest_sha256
            and shortlist.get("input_manifest_sha256") == input_manifest_sha256
            and quality.get("shortlist_export_sha256") == sha256(shortlist_path)
            and quality.get("per_query_contributions_sha256") == sha256(contributions_path)
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run(args: Any, contract: dict[str, Any]) -> None:
    preflight_path = args.output_root / "preflight.json"
    subprocess.run([str(args.python), str(THIS / "preflight-scale-aware-native-mih.py"), "--contract", str(args.contract), "--output", str(preflight_path)], check=True)
    probe_report = json.loads(preflight_path.read_text(encoding="utf-8"))
    artifact = args.output_root / "itq-256-artifact.npz"
    for current in contract["scales"]:
        root = args.output_root / current["id"]
        input_root = root / "input"
        materialize_command = [str(args.python), str(THIS / "materialize-mih-storage-input.py"), "materialize", "--calibration-root", str(root / "e5"), "--evaluation-root", str(root / "e5"), "--output", str(input_root), "--code-bits", "256", "--seed", str(contract["representation"]["itq_seed"]), "--itq-iterations", str(contract["representation"]["itq_iterations"])]
        if artifact.exists():
            materialize_command.extend(["--itq-artifact", str(artifact)])
        else:
            materialize_command.extend(["--write-itq-artifact", str(artifact)])
        subprocess.run(materialize_command, check=True)
        rows: list[dict[str, Any]] = []
        for ordinal, treatment in enumerate(treatments(contract, current, probe_report)):
            output = root / "results"
            config_path, report_path, shortlist = output / "configs" / f"{treatment['id']}.json", output / "native-reports" / f"{treatment['id']}.json", output / "shortlists" / f"{treatment['id']}.json"
            config_path.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True); shortlist.parent.mkdir(parents=True, exist_ok=True)
            config = native_config(contract, input_root, shortlist, treatment)
            input_manifest = json.loads((input_root / "manifest.json").read_text(encoding="utf-8"))
            config["query_count"] = input_manifest["query_count"]
            config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            quality = output / "quality" / f"{treatment['id']}.json"; contributions = output / "contributions" / f"{treatment['id']}.npz"
            quality.parent.mkdir(parents=True, exist_ok=True); contributions.parent.mkdir(parents=True, exist_ok=True)
            input_manifest_sha256 = sha256(input_root / "manifest.json")
            if completed_row(config_path, report_path, shortlist, quality, contributions, input_manifest_sha256):
                print(f"[{current['id']} {ordinal + 1}] {treatment['id']} (resume)", flush=True)
            else:
                print(f"[{current['id']} {ordinal + 1}] {treatment['id']}", flush=True)
                subprocess.run([str(args.executable), str(config_path), str(report_path)], check=True)
                subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(root / "e5"), "--shortlist-export", str(shortlist), "--output", str(quality), "--contributions-output", str(contributions), "--hamming-limit", str(contract["cascade"]["hamming_limit"]), "--adc-limit", str(contract["cascade"]["adc_limit"]), "--oracle-k", str(contract["cascade"]["oracle_k"]), "--oracle-cache", str(output / "quality" / "full-e5-oracle.npz")], check=True)
            with numpy.load(contributions, allow_pickle=False) as archive:
                adc, reranked, full = (numpy.asarray(archive[name], dtype=numpy.float64) for name in ("e5_oracle_survival_after_adc", "reranked_ndcg_at_10", "full_e5_ndcg_at_10"))
            gates = contract["selection_gates"]
            report_value = json.loads(report_path.read_text(encoding="utf-8"))
            auxiliary = int(report_value["backend"]["backend_index_logical_bytes"])
            rows.append({"id": treatment["id"], "backend": treatment["backend"], "native_config_sha256": sha256(config_path), "native_report_sha256": sha256(report_path), "shortlist_export_sha256": sha256(shortlist), "quality_report_sha256": sha256(quality), "contributions_sha256": sha256(contributions), "auxiliary_resident_bytes": auxiliary, "auxiliary_resident_bytes_per_document": auxiliary / input_manifest["document_count"], "adc_oracle_lb95": bootstrap(adc, None, gates["bootstrap_replicates"], gates["bootstrap_seed_base"] + ordinal * 2, gates["confidence_level"]), "ndcg_retention_lb95": bootstrap(reranked, full, gates["bootstrap_replicates"], gates["bootstrap_seed_base"] + ordinal * 2 + 1, gates["confidence_level"]), "candidate_generator_p50_ms_per_query": report_value["latency_ms_per_query"]["candidate_generator_total"]["p50"], "cascade_p50_ms_per_query": report_value["latency_ms_per_query"]["cascade_total"]["p50"]})
        for row in rows:
            gates = contract["selection_gates"]
            row["admissible"] = row["adc_oracle_lb95"] >= gates["adc_oracle_lb95_min"] and row["ndcg_retention_lb95"] >= gates["ndcg_retention_lb95_min"] and row["auxiliary_resident_bytes_per_document"] <= gates["auxiliary_resident_bytes_per_document_max"]
        result = {"schema_version": 1, "family": "scale_aware_native_mih_calibration_v1", "contract_sha256": sha256(args.contract), "preflight_sha256": sha256(preflight_path), "scale": current, "input_manifest_sha256": sha256(input_root / "manifest.json"), "itq_artifact_sha256": sha256(artifact), "rows": rows}
        result_path = root / "results" / "result.json"; result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        contract_path = THIS / "scale-aware-native-mih-protocol.example.json"
        contract = load_contract(contract_path)
        report = preflight.preflight(contract, sha256(contract_path))
        current = scale(contract, "es-25k")
        prepared = preparation_config(contract, current, 100)
        require(prepared["sampling"]["evaluation_distractors_per_language"] == 24900, "scale-aware preparation cardinality differs")
        values = treatments(contract, current, report)
        require(len(values) == 35 and values[0]["id"] == "mih-m15-sorted_lower_bound-two_pass_generation_array", "scale-aware MIH treatment matrix differs")
        require({value["hnsw_ef_search"] for value in values if value["backend"] == "hnsw"} == {768, 1024}, "scale-aware HNSW treatment grid differs")
        lower = bootstrap(numpy.asarray([0.8, 0.9, 1.0], dtype=numpy.float64), None, 100, 7, 0.95)
        require(0.0 < lower <= 1.0, "scale-aware bootstrap differs")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ids_path = root / "documents.jsonl"
            ids_path.write_text(
                json.dumps({"id": "first", "text": "valid U+0085: \u0085"}, ensure_ascii=False) + "\n"
                + json.dumps({"id": "second"}) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            require(read_ids(ids_path) == {"first", "second"}, "scale-aware JSONL reader splits Unicode document text")
            config_path, report_path, shortlist_path = root / "config.json", root / "report.json", root / "shortlist.json"
            quality_path, contributions_path = root / "quality.json", root / "contributions.npz"
            config_path.write_text("{}", encoding="utf-8")
            shortlist_path.write_text(json.dumps({"input_manifest_sha256": "input"}), encoding="utf-8")
            numpy.savez(contributions_path, e5_oracle_survival_after_adc=numpy.asarray([1.0]), reranked_ndcg_at_10=numpy.asarray([1.0]), full_e5_ndcg_at_10=numpy.asarray([1.0]))
            report_path.write_text(json.dumps({"benchmark_config_sha256": sha256(config_path), "input_manifest_sha256": "input"}), encoding="utf-8")
            quality_path.write_text(json.dumps({"shortlist_export_sha256": sha256(shortlist_path), "per_query_contributions_sha256": sha256(contributions_path)}), encoding="utf-8")
            require(completed_row(config_path, report_path, shortlist_path, quality_path, contributions_path, "input"), "scale-aware complete row was not resumed")
            report_path.write_text("{}", encoding="utf-8")
            require(not completed_row(config_path, report_path, shortlist_path, quality_path, contributions_path, "input"), "scale-aware invalid row was resumed")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-scale-aware-native-mih self-test failed: {error}", file=sys.stderr)
        return 1
    print("run-scale-aware-native-mih self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    for name in ("prepare", "materialize", "run"):
        command = commands.add_parser(name)
        command.add_argument("--contract", type=Path, default=THIS / "scale-aware-native-mih-protocol.example.json")
        command.add_argument("--output-root", type=Path, required=True)
        command.add_argument("--python", type=Path, default=Path(sys.executable))
        if name == "prepare":
            command.add_argument("--source-root", type=Path, required=True)
        if name == "materialize":
            command.add_argument("--batch-size", type=int, default=96)
            command.add_argument("--thread-count", type=int, default=36)
            command.add_argument("--cache-dir", type=Path, required=True)
        if name == "run":
            command.add_argument("--executable", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        contract = load_contract(args.contract)
        if args.command == "prepare": prepare(args, contract)
        elif args.command == "materialize": materialize(args, contract)
        else: run(args, contract)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-scale-aware-native-mih: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
