#!/usr/bin/env python3
"""Collect fresh-process samples for the frozen full-corpus codec benchmark."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
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


planner = load("neuroute_full_corpus_codec_runner_planner",
               "plan-neuroute-full-corpus-codec.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-full-corpus-codec.py",
        "materialize-neuroute-full-corpus-codec.py",
        "run-neuroute-full-corpus-codec.py",
        "neuroute_full_corpus_codec.cpp",
    )
    return {name: sha256(THIS / name) for name in names}


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def summary(values: list[float]) -> dict[str, Any]:
    require(bool(values), "full-corpus codec timing samples are absent")
    return {"mean": statistics.fmean(values), "p50": quantile(values, 0.50),
            "p95": quantile(values, 0.95), "p99": quantile(values, 0.99),
            "samples": len(values)}


def selected_requests(representation: str, contract: dict[str, Any]) -> list[int]:
    cold = contract["process_cold"]
    prefix = (cold["selection_prefix_utf8"] + "\n" + representation + "\n").encode()
    ordered = sorted(range(228), key=lambda value: (
        hashlib.sha256(prefix + str(value).encode()).digest(), value))
    return ordered[:cold["samples_per_representation"]]


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> None:
    actual = {
        "final_codec_quality_sha256": sha256(args.final_codec_quality),
        "final_codec_evidence_sha256": sha256(args.final_codec_evidence),
        "final_codec_native_sha256": sha256(args.final_codec_native),
        "final_codec_materialization_sha256": sha256(
            args.final_codec_materialization_root / "manifest.json"),
        "final_representation_materialization_sha256": sha256(
            args.final_representation_root / "manifest.json"),
        "conditional_result_sha256": sha256(args.conditional_result),
    }
    require(actual == contract["activation"], "full-corpus codec activation differs")
    receipt = json.loads(args.final_codec_evidence.read_text(encoding="utf-8"))
    require(receipt.get("decision", {}).get("selected_quantizer") == "int5_document" and
            receipt["decision"].get("selected_layout") == "simdcomp_bp128" and
            receipt["decision"].get("full_corpus_storage_followup_licensed") is True,
            "full-corpus codec parent receipt differs")


def validate_native(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    native_input = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    storage = json.loads(args.storage_manifest.read_text(encoding="utf-8"))
    warm = json.loads(args.warm_report.read_text(encoding="utf-8"))
    require(storage.get("input_manifest_sha256") == sha256(args.input_manifest) and
            warm.get("storage_manifest_sha256") == sha256(args.storage_manifest) and
            warm.get("input_manifest_sha256") == sha256(args.input_manifest),
            "full-corpus codec native binding differs")
    ids = [row["id"] for row in storage.get("representations", [])]
    require(ids == [row["id"] for row in native_input["representations"]] and
            [row["id"] for row in warm.get("rows", [])] == ids and
            storage.get("simdcomp_available") is True and
            warm.get("simdcomp_available") is True,
            "full-corpus codec native matrix differs")
    completed = subprocess.run([
        str(args.native_executable), "--validate", str(args.storage_manifest),
        str(args.input_manifest), str(args.warm_report),
    ], check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"full-corpus codec native validation failed: {completed.stderr.strip()}")
    return storage, warm, ids


def collect_cold(contract: dict[str, Any], ids: list[str],
                 args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    samples = []
    with tempfile.TemporaryDirectory(prefix="neuroute-full-corpus-cold-") as directory:
        root = Path(directory)
        for representation in ids:
            for request in selected_requests(representation, contract):
                output = root / f"{representation}-{request}.json"
                started = time.perf_counter()
                completed = subprocess.run([
                    str(args.native_executable), "--cold-sample",
                    str(args.storage_manifest), str(args.input_manifest),
                    representation, str(request), str(output),
                ], check=False, capture_output=True, text=True)
                launch_ms = (time.perf_counter() - started) * 1000.0
                require(completed.returncode == 0,
                        f"full-corpus codec cold sample failed: {completed.stderr.strip()}")
                row = json.loads(output.read_text(encoding="utf-8"))
                require(row.get("passed") is True and row.get("representation") == representation
                        and int(row.get("request", -1)) == request,
                        "full-corpus codec cold sample receipt differs")
                samples.append({**row, "process_launch_total_ms": launch_ms})
    summaries = []
    for representation in ids:
        rows = [row for row in samples if row["representation"] == representation]
        require([row["request"] for row in rows] == selected_requests(representation, contract),
                "full-corpus codec cold sample selection differs")
        summaries.append({
            "representation": representation,
            "samples": len(rows),
            "logical_fetch_bytes": rows[0]["logical_fetch_bytes"],
            "random_reads": rows[0]["random_reads"],
            "fetch_ms": summary([row["fetch_ms"] for row in rows]),
            "decode_and_dot_ms": summary([row["decode_and_dot_ms"] for row in rows]),
            "rank_top10_ms": summary([row["rank_top10_ms"] for row in rows]),
            "total_ms": summary([row["total_ms"] for row in rows]),
            "process_launch_total_ms": summary([row["process_launch_total_ms"] for row in rows]),
            "page_fault_delta": {
                key: summary([float(row["page_fault_delta"][key]) for row in rows])
                for key in ("minor", "major", "total")
            },
        })
    return samples, summaries


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    validate_activation(contract, args)
    storage, warm, ids = validate_native(args)
    samples, summaries = collect_cold(contract, ids, args)
    output = {
        "schema_version": 1,
        "family": "neuroute_full_corpus_codec_io_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "input_manifest_sha256": sha256(args.input_manifest),
        "storage_manifest_sha256": sha256(args.storage_manifest),
        "warm_report_sha256": sha256(args.warm_report),
        "environment": {
            "platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor(), "python": platform.python_version(),
            "processor_identifier": os.environ.get("PROCESSOR_IDENTIFIER", ""),
            "logical_cpu_count": os.cpu_count(),
        },
        "storage": storage,
        "warm_page_cache": warm,
        "process_cold": {
            "definition": contract["process_cold"]["definition"],
            "os_page_cache_controlled": False,
            "samples": samples,
            "summaries": summaries,
        },
        "decision": {
            "quality_replayed_all_requests": True,
            "production_storage_selection_deferred": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-full-corpus-codec.example.json")
    values = selected_requests("int5_simdcomp_bp128", contract)
    require(len(values) == 31 and len(set(values)) == 31 and
            summary([1.0, 2.0, 3.0])["p50"] == 2.0,
            "full-corpus codec runner self-test differs")
    print("NeuRoute full-corpus codec runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-full-corpus-codec.example.json")
    parser.add_argument("--final-codec-quality", type=Path)
    parser.add_argument("--final-codec-evidence", type=Path)
    parser.add_argument("--final-codec-native", type=Path)
    parser.add_argument("--final-codec-materialization-root", type=Path)
    parser.add_argument("--final-representation-root", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--storage-manifest", type=Path)
    parser.add_argument("--warm-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items() if name not in ignored):
            parser.error("all full-corpus codec result paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"run-neuroute-full-corpus-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
