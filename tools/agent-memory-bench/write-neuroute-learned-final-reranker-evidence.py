#!/usr/bin/env python3
"""Replay and bind the learned final binary reranker result."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import neuroute_authoritative_qrels as authoritative


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


runner = load("neuroute_learned_final_evidence_runner",
              "run-neuroute-learned-final-reranker.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [
        sys.executable, str(THIS / "run-neuroute-learned-final-reranker.py"),
        "--contract", str(args.contract),
        "--final-result", str(args.final_result),
        "--final-materialization-root", str(args.final_materialization_root),
        "--final-evidence", str(args.final_evidence),
        "--conditional-result", str(args.conditional_result),
        "--conditional-evidence", str(args.conditional_evidence),
        "--random-ceiling-result", str(args.random_ceiling_result),
        "--random-ceiling-evidence", str(args.random_ceiling_evidence),
        "--v4-contract", str(args.v4_contract),
        "--scale-contract", str(args.scale_contract),
        "--german-split-result", str(args.german_split_result),
        "--de-1m-e5-root", str(args.de_1m_e5_root),
        "--de-1m-input-root", str(args.de_1m_input_root),
        "--model-root", str(args.model_root),
        "--output", str(output),
    ]
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            command.extend([f"--{language}-{kind}-root",
                            str(getattr(args, f"{language}_{kind}_root"))])
    return command


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    args: argparse.Namespace) -> None:
    require(result.get("family") == "neuroute_learned_final_binary_reranker_result" and
            result.get("contract_sha256") == runner.sha256(args.contract),
            "learned-final evidence result binding differs")
    require(result.get("activation") == contract["activation"] and
            result.get("source_files_sha256") == runner.source_hashes(),
            "learned-final evidence sources differ")
    models = result.get("models", [])
    expected_models = [(width, seed) for width in contract["models"]["widths"]
                       for seed in contract["models"]["seeds"]]
    require([(row.get("width"), row.get("seed")) for row in models] == expected_models,
            "learned-final evidence model matrix differs")
    for row in models:
        path = args.model_root / row["file"]
        require(path.is_file() and runner.sha256(path) == row["sha256"],
                "learned-final evidence model bytes differ")
    datasets = result.get("datasets", [])
    require([(row.get("id"), row.get("query_count")) for row in datasets] ==
            [("de-25k", 26), ("fr-25k", 85), ("ja-25k", 215), ("de-1m", 26)],
            "learned-final evidence evaluation matrix differs")
    for dataset in datasets:
        rows = dataset.get("rows", [])
        require(len(rows) == 30 and
                sum(row.get("query_count", 0) for row in rows) ==
                30 * dataset["query_count"],
                "learned-final evidence query rows differ")
    comparisons = result.get("decision", {}).get("comparisons", [])
    require([row.get("width") for row in comparisons] == contract["models"]["widths"] and
            result["decision"].get("production_storage_selection_deferred") is True,
            "learned-final evidence decision differs")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    validate_result(result, contract, args)
    authoritative_roots = authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ])
    with tempfile.TemporaryDirectory(prefix="neuroute-learned-final-replay-") as directory:
        replay = Path(directory) / "result.json"
        completed = subprocess.run(replay_command(args, replay), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                f"learned-final replay failed: {completed.stderr.strip()}")
        require(replay.read_bytes() == args.result.read_bytes(),
                "learned-final replay bytes differ")
    require(authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ]) == authoritative_roots,
            "learned-final authoritative roots changed during replay")
    output = {
        "schema_version": 1,
        "family": "neuroute_learned_final_binary_reranker_evidence",
        "passed": True,
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "source_files_sha256": {
            **runner.source_hashes(),
            "write-neuroute-learned-final-reranker-evidence.py": runner.sha256(Path(__file__)),
        },
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": authoritative_roots,
        "authoritative_qrels_to_quality_replay_passed": True,
        "query_partition": result["query_partition"],
        "model_files": [{key: row[key] for key in
                         ("width", "seed", "file", "sha256", "bytes_per_document")}
                        for row in result["models"]],
        "decision": result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-learned-final-reranker.example.json")
    require(runner.planner.plan(contract) == {
        "family": "neuroute_learned_final_binary_reranker",
        "models": 9, "teacher_pool_rows": 150,
        "evaluation_query_rows_per_model_seed": 352, "native_rows": 0,
    }, "learned-final evidence self-test differs")
    print("NeuRoute learned-final evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-learned-final-reranker.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--final-result", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--final-evidence", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--conditional-evidence", type=Path)
    parser.add_argument("--random-ceiling-result", type=Path)
    parser.add_argument("--random-ceiling-evidence", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{kind}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all learned-final evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-learned-final-reranker-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
