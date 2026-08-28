#!/usr/bin/env python3
"""Replay and bind the frozen final-codec quality and native evidence."""
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


runner = load("neuroute_final_codec_evidence_runner", "run-neuroute-final-codec.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def quality_command(args: argparse.Namespace, output: Path) -> list[str]:
    command = [sys.executable, str(THIS / "run-neuroute-final-codec.py"),
               "--contract", str(args.contract),
               "--conditional-result", str(args.conditional_result),
               "--conditional-evidence", str(args.conditional_evidence),
               "--final-materialization-root", str(args.final_materialization_root),
               "--v4-contract", str(args.v4_contract),
               "--scale-contract", str(args.scale_contract),
               "--german-split-result", str(args.german_split_result),
               "--de-1m-e5-root", str(args.de_1m_e5_root),
               "--de-1m-input-root", str(args.de_1m_input_root),
               "--output", str(output)]
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            command.extend([f"--{language}-{kind}-root",
                            str(getattr(args, f"{language}_{kind}_root"))])
    return command


def choose_layout(quality: dict[str, Any], native: dict[str, Any]) -> tuple[str, dict[str, float]]:
    bits = int(quality["decision"]["selected_quantizer"][3])
    rows = [row for row in native["rows"] if int(row["bits"]) == bits]
    require(rows and all(row["timing"] is not None for row in rows),
            "final-codec selected timing is absent")
    maxima: dict[str, float] = {}
    for row in rows:
        layout = row["layout"]
        value = float(row["timing"]["rank_top10_ms_per_query"]["p95"])
        maxima[layout] = max(maxima.get(layout, 0.0), value)
    return min(maxima, key=lambda layout: (maxima[layout], layout)), maxima


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    quality = json.loads(args.quality_result.read_text(encoding="utf-8"))
    require(quality["contract_sha256"] == runner.sha256(args.contract),
            "final-codec evidence quality binding differs")
    require(quality["source_files_sha256"] == runner.source_hashes(),
            "final-codec evidence quality sources differ")
    require([row["id"] for row in quality["datasets"]] == contract["datasets"],
            "final-codec evidence datasets differ")
    require(quality["decision"]["selected_quantizer"] in
            {row["id"] for row in contract["quantizers"]},
            "final-codec evidence decision differs")

    authoritative_roots = authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ])

    with tempfile.TemporaryDirectory(prefix="neuroute-final-codec-replay-") as directory:
        replay_path = Path(directory) / "quality.json"
        completed = subprocess.run(quality_command(args, replay_path), check=False,
                                   capture_output=True, text=True)
        require(completed.returncode == 0,
                f"final-codec quality replay failed: {completed.stderr.strip()}")
        require(replay_path.read_bytes() == args.quality_result.read_bytes(),
                "final-codec quality replay bytes differ")
    require(authoritative.validate_roots([
        ("de-25k", args.de_e5_root), ("fr-25k", args.fr_e5_root),
        ("ja-25k", args.ja_e5_root), ("de-1m", args.de_1m_e5_root),
    ]) == authoritative_roots,
            "final-codec authoritative roots changed during replay")

    manifest_path = args.native_materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["contract_sha256"] == runner.sha256(args.contract),
            "final-codec native materialization binding differs")
    require(manifest["quality_result_sha256"] == runner.sha256(args.quality_result),
            "final-codec native quality binding differs")
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    require(native["family"] == "neuroute_final_codec_native_result" and
            native["materialization_sha256"] == runner.sha256(manifest_path),
            "final-codec native report binding differs")
    require(native["simdcomp_available"] is True,
            "final-codec SIMDComp treatment was not measured")
    require(len(native["rows"]) == 84, "final-codec native matrix differs")
    completed = subprocess.run([str(args.native_executable), "--validate",
                                str(manifest_path), str(args.native_report)],
                               check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"final-codec native replay failed: {completed.stderr.strip()}")
    selected_layout, layout_maxima = choose_layout(quality, native)

    output = {
        "schema_version": 1,
        "family": "neuroute_final_codec_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "quality_result_sha256": runner.sha256(args.quality_result),
        "native_materialization_sha256": runner.sha256(manifest_path),
        "native_report_sha256": runner.sha256(args.native_report),
        "source_files_sha256": runner.source_hashes(),
        "authoritative_qrels_validator_sha256": runner.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_roots": authoritative_roots,
        "authoritative_qrels_to_quality_replay_passed": True,
        "simdcomp": contract["simdcomp"],
        "decision": {
            "selected_quantizer": quality["decision"]["selected_quantizer"],
            "selected_layout": selected_layout,
            "maximum_rank_top10_p95_ms_by_layout": layout_maxima,
            "full_corpus_storage_followup_licensed": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-final-codec.example.json")
    require(contract["simdcomp"]["commit"].startswith("009c678"),
            "final-codec evidence self-test differs")
    print("NeuRoute final-codec evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-final-codec.example.json")
    parser.add_argument("--quality-result", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--conditional-evidence", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--native-materialization-root", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--scale-contract", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for kind in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{kind}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all final-codec evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-final-codec-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
