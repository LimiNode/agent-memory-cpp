#!/usr/bin/env python3
"""Replay and bind the frozen nonlinear listwise reranker study."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_nonlinear_listwise_evidence_runner",
              "run-neuroute-nonlinear-listwise-reranker.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def expected_model_keys(contract: dict[str, Any]) -> set[tuple[int, str, int]]:
    return {
        (int(seed), variant, int(count))
        for seed in contract["route"]["seeds"]
        for variant in contract["models"]["variants"]
        for count in contract["training"]["nested_query_counts"]
    }


def model_sha_map(models: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for model in models:
        name = model.get("file")
        digest = model.get("sha256")
        require(isinstance(name, str) and name and isinstance(digest, str)
                and len(digest) == 64 and name not in result,
                "nonlinear listwise evidence model manifest differs")
        result[name] = digest
    return result


def validate_budget_rows(rows: list[dict[str, Any]], expected_budgets: list[int],
                         expected_queries: int | None, label: str) -> None:
    for row in rows:
        budgets = row.get("budgets")
        require(isinstance(budgets, list)
                and [entry.get("address_budget") for entry in budgets] == expected_budgets,
                f"nonlinear listwise {label} budget matrix differs")
        if expected_queries is not None:
            queries = row.get("queries")
            require(row.get("query_count") == expected_queries
                    and isinstance(queries, list) and len(queries) == expected_queries,
                    f"nonlinear listwise {label} query matrix differs")


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    model_root: Path) -> dict[str, str]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_nonlinear_listwise_reranker_result"
            and result.get("matrix") == plan,
            "nonlinear listwise evidence result family differs")
    require(result.get("activation") == contract["activation"],
            "nonlinear listwise evidence activation differs")

    models = result.get("models")
    require(isinstance(models, list) and len(models) == plan["trained_models"],
            "nonlinear listwise evidence model count differs")
    keys = {
        (int(row.get("seed")), row.get("variant"), int(row.get("training_query_count")))
        for row in models
    }
    require(keys == expected_model_keys(contract),
            "nonlinear listwise evidence model matrix differs")
    hashes = model_sha_map(models)
    for name, digest in hashes.items():
        path = model_root / name
        require(path.is_file() and runner.sha256(path) == digest,
                f"nonlinear listwise evidence model bytes differ: {name}")

    configuration = result.get("configuration_rows")
    internal = result.get("internal_rows")
    require(isinstance(configuration, list)
            and len(configuration) == plan["configuration_rows"],
            "nonlinear listwise evidence configuration matrix differs")
    require({(int(row.get("seed")), row.get("variant"),
                 int(row.get("training_query_count"))) for row in configuration}
            == expected_model_keys(contract),
            "nonlinear listwise evidence configuration keys differ")
    require(isinstance(internal, list) and len(internal) == plan["internal_rows"],
            "nonlinear listwise evidence internal matrix differs")
    treatments = set(contract["models"]["variants"]) | {
        "prototype_order", "privileged_teacher"}
    require({(int(row.get("seed")), row.get("treatment")) for row in internal}
            == {(int(seed), treatment) for seed in contract["route"]["seeds"]
                for treatment in treatments},
            "nonlinear listwise evidence internal keys differ")
    budgets = contract["evaluation"]["address_budgets"]
    validate_budget_rows(configuration, budgets, None, "configuration")
    validate_budget_rows(internal, budgets,
                         contract["query_partitions"]["internal_evaluation"]["queries"],
                         "internal")
    require(sum(len(row["budgets"]) for row in configuration)
            == plan["configuration_budget_measurements"]
            and sum(len(row["budgets"]) for row in internal)
            == plan["internal_budget_measurements"],
            "nonlinear listwise evidence budget cardinality differs")
    decision = result.get("decision", {})
    require(decision.get("internal_evaluation_opened_after_configuration_selection") is True
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "nonlinear listwise evidence decision differs")
    return hashes


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result_bytes = args.result.read_bytes()
    result = json.loads(result_bytes)
    original_models = validate_result(result, contract, args.model_root)

    parent_result = json.loads(args.prototype_gain_density_result.read_text(
        encoding="utf-8"))
    parent_evidence = json.loads(args.prototype_gain_density_evidence.read_text(
        encoding="utf-8"))
    require(runner.sha256(args.prototype_gain_density_result)
            == contract["activation"]["prototype_gain_density_result_sha256"]
            and runner.sha256(args.prototype_gain_density_evidence)
            == contract["activation"]["prototype_gain_density_evidence_sha256"],
            "nonlinear listwise parent activation bytes differ")
    require(parent_result.get("family")
            == "neuroute_prototype_gain_density_reranker_result"
            and parent_evidence.get("family")
            == "neuroute_prototype_gain_density_reranker_evidence"
            and parent_evidence.get("passed") is True
            and parent_evidence.get("result_sha256")
            == contract["activation"]["prototype_gain_density_result_sha256"]
            and parent_evidence.get("authoritative_qrels_to_quality_replay_passed") is True
            and isinstance(parent_evidence.get("authoritative_roots"), list),
            "nonlinear listwise authoritative parent evidence differs")

    with tempfile.TemporaryDirectory(
            prefix="neuroute-nonlinear-listwise-evidence-") as directory:
        root = Path(directory)
        replay_result = root / "result.json"
        replay_models = root / "models"
        replay_cache = root / "cache"
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = replay_result
        replay_args.model_root = replay_models
        replay_args.cache_root = replay_cache
        runner.run(replay_args)
        require(replay_result.read_bytes() == result_bytes,
                "nonlinear listwise result is not byte-replayable")
        replay = json.loads(replay_result.read_text(encoding="utf-8"))
        regenerated_models = validate_result(replay, contract, replay_models)
        require(regenerated_models == original_models,
                "nonlinear listwise regenerated model SHA map differs")
        require(replay_cache.is_dir() and any(path.is_file()
                                             for path in replay_cache.rglob("*")),
                "nonlinear listwise replay did not recreate its cache")

    evidence = {
        "schema_version": 1,
        "family": "neuroute_nonlinear_listwise_reranker_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-nonlinear-listwise-reranker-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "model_archives": [
            {"file": name, "sha256": original_models[name]}
            for name in sorted(original_models)
        ],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "cache_recreation_passed": True,
        "model_archive_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "internal_evaluation_opened_after_configuration_selection": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "decision": result["decision"],
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    plan = runner.planner.plan(contract)
    require(plan["trained_models"] == 45
            and plan["configuration_rows"] == 45
            and plan["internal_rows"] == 15
            and len(expected_model_keys(contract)) == 45,
            "nonlinear listwise evidence self-test differs")
    synthetic = [{"file": "a.npz", "sha256": "a" * 64},
                 {"file": "b.npz", "sha256": "b" * 64}]
    require(model_sha_map(synthetic) == {"a.npz": "a" * 64,
                                         "b.npz": "b" * 64},
            "nonlinear listwise evidence model-map self-test differs")
    print("NeuRoute nonlinear listwise reranker evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-nonlinear-listwise-reranker.example.json")
    parser.add_argument("--prototype-gain-density-result", type=Path)
    parser.add_argument("--prototype-gain-density-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all nonlinear listwise evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-nonlinear-listwise-reranker-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
