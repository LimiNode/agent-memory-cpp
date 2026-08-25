#!/usr/bin/env python3
"""Fail-closed archival replay for the direct semantic-address study."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
ROOT = THIS.parents[1]
SOURCES = (
    "CMakeLists.txt",
    "guides/experiments/2026-08-25-direct-learned-semantic-address-protocol.md",
    "tools/agent-memory-bench/direct-learned-semantic-address.example.json",
    "tools/agent-memory-bench/plan-direct-learned-semantic-address.py",
    "tools/agent-memory-bench/materialize-direct-semantic-address-splits.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
    "tools/agent-memory-bench/run-direct-learned-semantic-address.py",
    "tools/agent-memory-bench/write-direct-learned-semantic-address-evidence.py",
)
TREATMENTS = (
    "symmetric_document_head_control",
    "learned_direct_address_postings",
    "learned_address_then_float_bucket_centroid_refinement",
    "exact_float_bucket_centroid_scan_same_postings",
)
TIMING_FIELDS = {"routing_cascade_p50_ms", "routing_cascade_p95_ms"}


def load_runner() -> Any:
    path = THIS / "run-direct-learned-semantic-address.py"
    spec = importlib.util.spec_from_file_location("direct_semantic_address_evidence_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load direct semantic address runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def add(files: dict[str, bytes], name: str, path: Path) -> None:
    require(path.is_file(), f"direct semantic address evidence member missing: {name}")
    files[name] = path.read_bytes()


def stable_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in TIMING_FIELDS}


def validate_finite_timing(row: dict[str, Any]) -> None:
    for field in TIMING_FIELDS:
        value = row.get(field)
        require(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0.0,
                f"direct semantic address timing differs: {field}")


def load_model(path: Path) -> tuple[dict[str, Any], dict[str, numpy.ndarray]]:
    expected = {
        "metadata_json",
        "document_mean",
        "document_projection",
        "document_threshold",
        "query_mean",
        "query_scale",
        "weight1",
        "bias1",
        "weight2",
        "bias2",
    }
    with numpy.load(path, allow_pickle=False) as archive:
        require(set(archive.files) == expected, "direct semantic address model membership differs")
        metadata = json.loads(str(archive["metadata_json"].item()))
        arrays = {name: archive[name].copy() for name in expected - {"metadata_json"}}
    shapes = {
        "document_mean": (384,),
        "document_projection": (384, 16),
        "document_threshold": (16,),
        "query_mean": (384,),
        "query_scale": (384,),
        "weight1": (128, 384),
        "bias1": (128,),
        "weight2": (16, 128),
        "bias2": (16,),
    }
    require(all(arrays[name].shape == shape and arrays[name].dtype == numpy.float32 for name, shape in shapes.items()),
            "direct semantic address model tensor differs")
    return metadata, arrays


def compare_metrics(recorded: dict[str, Any], replayed: dict[str, Any], context: str) -> None:
    validate_finite_timing(recorded)
    require(stable_row(recorded) == stable_row(replayed), f"direct semantic address replay differs: {context}")


def validate(args: argparse.Namespace) -> dict[str, Any]:
    contract = runner.planner.load_contract(args.contract)
    data = runner.load_inputs(args.e5_root, args.input_root)
    result_path = args.result_root / "result.json"
    model_path = args.result_root / "model.npz"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    require(result.get("schema_version") == 1
            and result.get("family") == "direct_learned_semantic_address_result_v1"
            and result.get("contract_sha256") == sha256(args.contract)
            and result.get("e5_manifest_sha256") == data["manifest_sha256"]
            and result.get("input_manifest_sha256") == data["input_manifest_sha256"]
            and result.get("model_sha256") == sha256(model_path),
            "direct semantic address result identity differs")

    split_ids = runner.splitter.materialize(data["query_ids"], contract)
    require(result.get("splits") == split_ids, "direct semantic address split replay differs")
    id_to_query = {value: index for index, value in enumerate(data["query_ids"])}
    partitions = {
        name: [id_to_query[value] for value in split_ids[f"{name}_query_ids"]]
        for name in ("training", "configuration_selection", "internal_evaluation")
    }
    oracle, full_ndcg = runner.exact_oracle(data, contract["cascade"]["oracle_k"])
    document_logits, expected_document_artifact = runner.document_head(data["documents"])
    metadata, artifact = load_model(model_path)
    require(metadata.get("schema_version") == 1
            and metadata.get("family") == "direct_learned_semantic_address_model_v1"
            and metadata.get("contract_sha256") == sha256(args.contract)
            and metadata.get("e5_manifest_sha256") == data["manifest_sha256"]
            and metadata.get("input_manifest_sha256") == data["input_manifest_sha256"]
            and metadata.get("training_query_ids") == split_ids["training_query_ids"]
            and metadata.get("training") == result.get("training"),
            "direct semantic address model metadata differs")
    for name, value in expected_document_artifact.items():
        require(numpy.array_equal(artifact[name], value), f"direct semantic address document placement replay differs: {name}")

    target_probabilities = numpy.asarray([
        (document_logits[oracle[position]] >= 0.0).mean(axis=0)
        for position in partitions["training"]
    ], dtype=numpy.float32)
    replay_artifact, replay_training = runner.train_mlp(
        data["queries"][partitions["training"]], target_probabilities, contract["router_training"]
    )
    for name in ("query_mean", "query_scale", "weight1", "bias1", "weight2", "bias2"):
        require(numpy.array_equal(artifact[name], replay_artifact[name]), f"direct semantic address learned checkpoint replay differs: {name}")
    for field, value in replay_training.items():
        if field != "training_seconds":
            require(result["training"].get(field) == value, f"direct semantic address training provenance differs: {field}")
    require(isinstance(result["training"].get("training_seconds"), (int, float))
            and result["training"]["training_seconds"] > 0.0,
            "direct semantic address training duration differs")

    symmetric_logits = ((data["queries"] - artifact["document_mean"]) @ artifact["document_projection"] - artifact["document_threshold"]).astype(numpy.float32)
    learned_logits = runner.infer_mlp(data["queries"], artifact)
    indexes: dict[tuple[int, int], dict[str, Any]] = {}
    expected_rows: list[dict[str, Any]] = []
    for planned in runner.planner.plan(contract)["rows"]:
        width = planned["semantic_prefix_bits"]
        replication = planned["document_replication"]
        index = indexes.setdefault((width, replication), runner.build_index(document_logits, data["documents"], width, replication))
        for treatment, logits in (("symmetric_document_head_control", symmetric_logits),
                                  ("learned_direct_address_postings", learned_logits)):
            metrics, _ = runner.evaluate(
                data, partitions["configuration_selection"], logits, index, oracle, full_ndcg,
                treatment, width, planned["query_probes"], planned["candidate_mass_target"], False, False
            )
            expected_rows.append({"treatment": treatment, **planned, **metrics})
    recorded_rows = result.get("selection_rows")
    require(isinstance(recorded_rows, list) and len(recorded_rows) == len(expected_rows) == 450,
            "direct semantic address selection row count differs")
    for position, (recorded, replayed) in enumerate(zip(recorded_rows, expected_rows)):
        compare_metrics(recorded, replayed, f"selection row {position}")

    selected_by_budget: list[dict[str, Any]] = []
    for mass_target in contract["candidate_mass_targets"]:
        eligible = [
            row for row in expected_rows
            if row["treatment"] == contract["selection"]["headline_treatment"]
            and row["candidate_mass_target"] == mass_target
            and row["candidate_fraction"] <= mass_target
        ]
        require(eligible, f"direct semantic address replay has no candidate at budget {mass_target}")
        quality_key = lambda row: (
            row["e5_oracle_survival_after_adc"], row["reranked_ndcg_at_10"], -row["candidate_fraction"],
            -row["semantic_prefix_bits"], -row["query_probes"], -row["document_replication"],
        )
        selected_by_budget.append(max(eligible, key=quality_key))
    recorded_selected = result.get("selected_headline_by_budget")
    require(isinstance(recorded_selected, list) and len(recorded_selected) == 3,
            "direct semantic address selected frontier differs")
    for position, (recorded, replayed) in enumerate(zip(recorded_selected, selected_by_budget)):
        compare_metrics(recorded, replayed, f"selected budget {position}")
    selected = next(row for row in selected_by_budget if row["candidate_mass_target"] == contract["selection"]["candidate_mass_target"])
    compare_metrics(result.get("selected_headline", {}), selected, "headline selection")

    width = selected["semantic_prefix_bits"]
    replication = selected["document_replication"]
    probes = selected["query_probes"]
    mass_target = selected["candidate_mass_target"]
    index = indexes[(width, replication)]
    recorded_selection_controls = result.get("matched_selection_controls")
    recorded_internal_controls = result.get("internal_evaluation_controls")
    require(isinstance(recorded_selection_controls, list) and isinstance(recorded_internal_controls, list)
            and [row.get("treatment") for row in recorded_selection_controls] == list(TREATMENTS)
            and [row.get("treatment") for row in recorded_internal_controls] == list(TREATMENTS),
            "direct semantic address matched control ordering differs")

    files: dict[str, bytes] = {
        "bundle/contract.json": args.contract.read_bytes(),
        "bundle/result.json": result_path.read_bytes(),
        "bundle/model.npz": model_path.read_bytes(),
        "bundle/frozen/e5-manifest.json": (args.e5_root / "manifest.json").read_bytes(),
        "bundle/frozen/cascade-manifest.json": (args.input_root / "manifest.json").read_bytes(),
    }
    for source in SOURCES:
        add(files, f"bundle/measured-source/{source}", ROOT / source)

    for control_position, treatment in enumerate(TREATMENTS):
        logits = symmetric_logits if treatment == "symmetric_document_head_control" else learned_logits
        for partition_name, positions, recorded_controls in (
            ("selection", partitions["configuration_selection"], recorded_selection_controls),
            ("internal", partitions["internal_evaluation"], recorded_internal_controls),
        ):
            metrics, audit = runner.evaluate(
                data, positions, logits, index, oracle, full_ndcg, treatment,
                width, probes, mass_target, False, True
            )
            replayed = {"treatment": treatment, **metrics}
            compare_metrics(recorded_controls[control_position], replayed, f"{partition_name} {treatment}")
            audit_path = args.result_root / f"{partition_name}-audit-{treatment}.json"
            expected_audit = {"schema_version": 1, "treatment": treatment, "rows": audit}
            require(audit_path.read_bytes() == canonical(expected_audit),
                    f"direct semantic address per-query audit differs: {partition_name} {treatment}")
            add(files, f"bundle/audits/{audit_path.name}", audit_path)

    router_timing = result.get("query_router_inference")
    require(isinstance(router_timing, dict)
            and router_timing.get("implementation") == "numpy_float32_single_query_mlp_excluding_e5_v1"
            and router_timing.get("samples") == 3240
            and all(isinstance(router_timing.get(name), (int, float)) and math.isfinite(router_timing[name]) and router_timing[name] >= 0.0 for name in ("p50_us", "p95_us")),
            "direct semantic address router timing differs")
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    return {
        "schema_version": 1,
        "family": "direct_learned_semantic_address_evidence_v1",
        "contract_sha256": sha256(args.contract),
        "result_sha256": sha256(result_path),
        "model_sha256": sha256(model_path),
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "selection_row_count": len(expected_rows),
        "headline": stable_row(selected),
        "internal_evaluation_controls": [stable_row(row) for row in recorded_internal_controls],
        "members": members,
        "_files": files,
    }


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    files["bundle/evidence-manifest.json"] = canonical(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)


def self_test() -> None:
    row = {"candidate_fraction": 0.1, "routing_cascade_p50_ms": 1.0, "routing_cascade_p95_ms": 2.0}
    require(runner.canonical(stable_row(row)) == b'{\n  "candidate_fraction": 0.1\n}\n',
            "direct semantic address stable row differs")
    with tempfile.TemporaryDirectory() as directory:
        first = Path(directory) / "first.zip"
        second = Path(directory) / "second.zip"
        payload = {
            "schema_version": 1,
            "family": "direct_learned_semantic_address_evidence_v1",
            "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}},
            "_files": {"bundle/value": b"value"},
        }
        write_archive(first, payload.copy())
        write_archive(second, payload.copy())
        require(first.read_bytes() == second.read_bytes(), "direct semantic address archive determinism differs")
    print("direct learned semantic address evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "direct-learned-semantic-address.example.json")
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.result_root, args.e5_root, args.input_root, args.output)):
            parser.error("--result-root, --e5-root, --input-root, and --output are required")
        write_archive(args.output, validate(args))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError, zipfile.BadZipFile) as error:
        print(f"write-direct-learned-semantic-address-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
