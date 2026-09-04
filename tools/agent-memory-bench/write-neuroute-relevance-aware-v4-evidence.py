#!/usr/bin/env python3
"""Replay relevance-aware v4 quality/native evidence and emit its receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
ROOT = THIS.parent.parent
CPP = THIS / "neuroute_native_mdbx_cost.cpp"


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_relevance_aware_v4_evidence_runner",
              "run-neuroute-relevance-aware-v4.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def source_manifest_sha256() -> str:
    relative = CPP.relative_to(ROOT).as_posix()
    return hashlib.sha256(f"{relative}:{runner.sha256(CPP)}\n".encode("utf-8")).hexdigest()


def materializer_source_hashes() -> dict[str, str]:
    names = (
        "materialize-neuroute-relevance-aware-v4.py",
        "materialize-neuroute-native-mdbx-cost.py",
        "run-neuroute-relevance-aware-v4.py",
    )
    return {name: runner.sha256(THIS / name) for name in names}


def gitlink(path: str) -> str:
    result = subprocess.run(["git", "rev-parse", f"HEAD:{path}"], cwd=ROOT,
                            check=True, capture_output=True, text=True)
    value = result.stdout.strip()
    require(len(value) == 40, f"relevance-aware v4 gitlink differs: {path}")
    return value


def validate_quality(result: dict[str, Any], contract: dict[str, Any]) -> None:
    require(result.get("family") == "neuroute_relevance_aware_v4_quality_result"
            and result.get("claim_scope") == contract["claim_scope"],
            "relevance-aware v4 quality identity differs")
    expected_models = {(dataset["id"], treatment["id"], seed)
                       for dataset in contract["datasets"] for treatment in contract["treatments"]
                       for seed in contract["encoder"]["seeds"]}
    actual_models = {(dataset["id"], row["treatment"], row["seed"])
                     for dataset in result.get("datasets", []) for row in dataset.get("models", [])}
    require(actual_models == expected_models and len(actual_models) == 27,
            "relevance-aware v4 quality model matrix differs")
    expected_rows = {(dataset["id"], treatment["id"], seed, probes)
                     for dataset in contract["datasets"] for treatment in contract["treatments"]
                     for seed in contract["encoder"]["seeds"]
                     for probes in contract["routing"]["probe_budgets"]}
    actual_rows = {(dataset["id"], row["treatment"], row["seed"], row["probes"])
                   for dataset in result["datasets"] for row in dataset.get("quality_rows", [])}
    require(actual_rows == expected_rows and len(actual_rows) == 108,
            "relevance-aware v4 quality row matrix differs")
    for dataset in result["datasets"]:
        require(dataset.get("pca", {}).get("probes") == 16
                and dataset.get("training_relevant_pair_count")
                == next(row["training_relevant_pairs"] for row in contract["datasets"]
                        if row["id"] == dataset["id"]),
                "relevance-aware v4 quality dataset evidence differs")
        for row in dataset["quality_rows"] + [dataset["pca"]]:
            require(all(isinstance(row.get(name), str) and len(row[name]) == 64
                        for name in ("address_sequence_sha256", "candidate_sequence_sha256",
                                     "hamming_sequence_sha256", "adc_sequence_sha256")),
                    "relevance-aware v4 quality sequence evidence differs")


def validate_native(report: dict[str, Any], manifest: dict[str, Any], result: dict[str, Any],
                    contract: dict[str, Any], contract_path: Path,
                    manifest_path: Path) -> None:
    require(report.get("schema_version") == 1
            and report.get("family") == "neuroute_relevance_aware_v4_native_result"
            and report.get("claim_scope") == contract["claim_scope"]
            and report.get("contract_sha256") == runner.sha256(contract_path)
            and report.get("materialization_sha256") == runner.sha256(manifest_path)
            and report.get("evaluator_source_manifest_sha256") == source_manifest_sha256(),
            "relevance-aware v4 native report binding differs")
    require(manifest.get("family") == "neuroute_relevance_aware_v4_native_materialization"
            and manifest.get("contract_sha256") == runner.sha256(contract_path)
            and manifest.get("quality_result_sha256") == runner.sha256(Path(result["_path"]))
            and manifest.get("materializer_source_files_sha256") == materializer_source_hashes(),
            "relevance-aware v4 native materialization binding differs")
    stack = report.get("storage_stack", {})
    require(stack.get("provenance_authoritative") is True
            and stack.get("provenance_reason") == "repository_pinned_external_submodules"
            and stack.get("libmdbx_commit") == gitlink("external/libmdbx")
            and stack.get("mdbx_containers_commit") == gitlink("external/mdbx-containers"),
            "relevance-aware v4 native storage provenance differs")
    expected = {(dataset["id"], f"{treatment['id']}-{seed}", seed, probes)
                for dataset in contract["datasets"] for treatment in contract["treatments"]
                for seed in contract["encoder"]["seeds"]
                for probes in contract["routing"]["probe_budgets"]}
    expected |= {(dataset["id"], "pca", None, 16) for dataset in contract["datasets"]}
    rows = report.get("rows", [])
    actual = {(row.get("dataset"), row.get("route"), row.get("seed"), row.get("probes"))
              for row in rows}
    require(actual == expected and len(rows) == len(expected) == 111,
            "relevance-aware v4 native timing matrix differs")
    stages = contract["native_timing"]["stages"]
    for row in rows:
        require(all(isinstance(row.get(name), str) and len(row[name]) == 64
                    for name in ("candidate_sequence_sha256", "hamming_sequence_sha256",
                                 "adc_sequence_sha256")),
                "relevance-aware v4 native sequence digest differs")
        timing = row.get("timing_ms", {})
        require(set(timing) == set(stages), "relevance-aware v4 native timing stages differ")
        for stage in stages:
            value = timing[stage]
            require(all(isinstance(value.get(name), (int, float))
                        and math.isfinite(value[name]) and value[name] >= 0.0
                        for name in ("p50", "p95", "p99"))
                    and value["p50"] <= value["p95"] <= value["p99"]
                    and len(value.get("per_query_median", [])) == row["query_count"],
                    "relevance-aware v4 native timing sample differs")


def mean_quality(dataset: dict[str, Any], treatment: str, probes: int) -> dict[str, float]:
    rows = [row["metrics"] for row in dataset["quality_rows"]
            if row["treatment"] == treatment and row["probes"] == probes]
    require(len(rows) == 3, "relevance-aware v4 decision quality seed matrix differs")
    return {name: float(numpy.mean([row[name] for row in rows])) for name in rows[0]}


def mean_native_p95(native: dict[str, Any], dataset: str, treatment: str, probes: int) -> float:
    prefix = f"{treatment}-"
    rows = [row for row in native["rows"] if row["dataset"] == dataset
            and row["route"].startswith(prefix) and row["probes"] == probes]
    require(len(rows) == 3, "relevance-aware v4 decision native seed matrix differs")
    return float(numpy.mean([row["timing_ms"]["total"]["p95"] for row in rows]))


def decide(result: dict[str, Any], native: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    decision = contract["decision"]
    probes = decision["primary_probe_budget"]
    control_id = contract["treatments"][0]["id"]
    eligible = []
    all_comparisons = []
    for treatment in contract["treatments"][1:]:
        comparisons = []
        for dataset in result["datasets"]:
            candidate = mean_quality(dataset, treatment["id"], probes)
            control = mean_quality(dataset, control_id, probes)
            native_p95 = mean_native_p95(native, dataset["id"], treatment["id"], probes)
            pca_row = next(row for row in native["rows"]
                           if row["dataset"] == dataset["id"] and row["route"] == "pca")
            pca_p95 = float(pca_row["timing_ms"]["total"]["p95"])
            comparisons.append({
                "dataset": dataset["id"], "candidate": candidate, "control": control,
                "ndcg_delta": candidate["ndcg_at_10"] - control["ndcg_at_10"],
                "native_total_p95_ms": native_p95, "pca_total_p95_ms": pca_p95,
                "native_p95_ratio_vs_pca": native_p95 / pca_p95,
            })
        mean_gain = float(numpy.mean([row["ndcg_delta"] for row in comparisons]))
        mean_ndcg = float(numpy.mean([row["candidate"]["ndcg_at_10"] for row in comparisons]))
        mean_p95 = float(numpy.mean([row["native_total_p95_ms"] for row in comparisons]))
        passed = (all(row["candidate"]["candidate_fraction"] <= decision["maximum_candidate_fraction"]
                      and row["native_p95_ratio_vs_pca"] <= decision["maximum_native_p95_ratio_vs_pca"]
                      and row["ndcg_delta"] >= -decision["maximum_per_language_ndcg_loss_vs_control"]
                      for row in comparisons)
                  and mean_gain >= decision["minimum_cross_language_mean_ndcg_gain_vs_control"])
        record = {"treatment": treatment["id"], "probes": probes, "passed": passed,
                  "cross_language_mean_ndcg_gain": mean_gain,
                  "cross_language_mean_ndcg": mean_ndcg,
                  "cross_language_mean_native_p95_ms": mean_p95,
                  "comparisons": comparisons}
        all_comparisons.append(record)
        if passed:
            eligible.append(record)
    eligible.sort(key=lambda row: (-row["cross_language_mean_ndcg"],
                                   row["cross_language_mean_native_p95_ms"], row["treatment"]))
    selected = eligible[0] if eligible else None
    return {"comparisons": all_comparisons, "eligible": eligible, "selected": selected,
            "next": decision["next_if_pass"] if selected is not None else decision["next_if_none"],
            "confirmation_claims_permitted": False, "scale_transfer_permitted": False}


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-relevance-aware-v4.example.json")
    require(len(runner.planner.matrix(contract)) == 108
            and len(runner.planner.matrix(contract)) + len(contract["datasets"]) == 111,
            "relevance-aware v4 evidence matrix self-test differs")
    print("NeuRoute relevance-aware v4 evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-relevance-aware-v4.example.json")
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--training-evidence", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--activation-native-result", dest="native_result", type=Path)
    parser.add_argument("--activation-native-evidence", dest="native_evidence", type=Path)
    parser.add_argument("--activation-native-materialization", dest="native_materialization", type=Path)
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--replay-mdbx-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = (args.training_result, args.training_evidence, args.training_model_root,
                    args.native_result, args.native_evidence, args.native_materialization,
                    args.model_root, args.result, args.manifest, args.native_report,
                    args.executable, args.replay_mdbx_root, args.output)
        roots = {language: {name: getattr(args, f"{language}_{name}_root")
                            for name in ("result", "e5", "input")}
                 for language in ("de", "fr", "ja")}
        require(all(value is not None for value in required)
                and all(path is not None for values in roots.values() for path in values.values()),
                "relevance-aware v4 evidence paths are required")
        contract = runner.planner.load_contract(args.contract)
        result = json.loads(args.result.read_text(encoding="utf-8"))
        result["_path"] = str(args.result)
        require(result.get("contract_sha256") == runner.sha256(args.contract)
                and result.get("source_files_sha256") == runner.source_hashes(),
                "relevance-aware v4 evidence result binding differs")
        validate_quality(result, contract)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        native_report = json.loads(args.native_report.read_text(encoding="utf-8"))
        validate_native(native_report, manifest, result, contract, args.contract, args.manifest)
        with tempfile.TemporaryDirectory(prefix="neuroute-relevance-aware-v4-evidence-") as directory:
            replay = Path(directory) / "result.json"
            runner.run(args.contract, roots, args, args.model_root, replay, False)
            require(replay.read_bytes() == args.result.read_bytes(),
                    "relevance-aware v4 quality replay differs")
        subprocess.run([
            str(args.executable), "--validate", str(args.contract), str(args.manifest),
            str(args.replay_mdbx_root), str(args.native_report),
        ], cwd=ROOT, check=True)
        model_hashes = sorted(row["model_sha256"] for dataset in result["datasets"]
                              for row in dataset["models"])
        receipt = {
            "schema_version": 1, "family": "neuroute_relevance_aware_v4_evidence",
            "claim_scope": contract["claim_scope"], "contract_sha256": runner.sha256(args.contract),
            "quality_result_sha256": runner.sha256(args.result),
            "native_materialization_sha256": runner.sha256(args.manifest),
            "native_report_sha256": runner.sha256(args.native_report),
            "source_files_sha256": runner.source_hashes(),
            "evaluator_source_manifest_sha256": source_manifest_sha256(),
            "materializer_source_files_sha256": manifest["materializer_source_files_sha256"],
            "evidence_writer_sha256": runner.sha256(Path(__file__)),
            "model_set_sha256": hashlib.sha256("\n".join(model_hashes).encode("ascii")).hexdigest(),
            "model_count": 27, "quality_row_count": 108, "native_timing_row_count": 111,
            "quality_integrity_replay_passed": True, "native_integrity_replay_passed": True,
            "timings_replayed": False, "decision": decide(result, native_report, contract),
            "configuration_only": True, "confirmation_claims_permitted": False,
            "scale_transfer_permitted": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(runner.canonical(receipt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        print(f"write-neuroute-relevance-aware-v4-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
