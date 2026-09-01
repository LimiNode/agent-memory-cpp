#!/usr/bin/env python3
"""Evaluate a K1/K2 prefilter followed by exact K8 refinement."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]
MODES = ["int8", "nonlinear_int5_power_half"]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exact = load("neuroute_approximate_k8_exact",
             "run-neuroute-exact-k8-codec-frontier.py")


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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    amendment = value.get("analysis_amendment", {})
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_approximate_k8_frontier" and
            value["prefilter"] == {"treatment": "int8_uniform",
                "prototype_limits": [1, 2], "physical_layout":
                "dedicated_compact_address_major_prefix_per_limit"} and
            value["refine"]["treatments"] == ["fp32", "int9_mulaw_15",
                "int9_power_625", "int9_uniform"] and
            value["refine"]["address_counts"] == [2048, 4096, 8192] and
            "opened-internal exact controls" in
                value["refine"]["amendment"] and
            amendment.get("raw_native_grid_unchanged") is True and
            amendment.get("reuse_matching_previous_checkpoints") is True and
            amendment.get("selection_gates_unchanged") is True and
            len(amendment.get("previous_contract_sha256", "")) == 64 and
            len(amendment.get("previous_runner_sha256", "")) == 64,
            "approximate K8 contract differs")
    return value


def materialize(args: argparse.Namespace, treatment: str, root: Path) -> Path:
    if treatment == "fp32":
        return args.source_manifest
    manifest = root / "manifest.json"
    if manifest.is_file():
        return manifest
    subprocess.run([sys.executable,
        str(THIS / "materialize-neuroute-k8-codec.py"), "--contract",
        str(args.exact_contract), "--source-manifest", str(args.source_manifest),
        "--layout-manifest", str(args.layout_manifest), "--native-executable",
        str(args.codec_executable), "--treatment", treatment,
        "--prototype-limit", "8", "--output-root", str(root)], check=True)
    return manifest


def materialize_prefilter(args: argparse.Namespace, prototype_limit: int,
                          root: Path) -> Path:
    manifest = root / "manifest.json"
    if manifest.is_file():
        return manifest
    subprocess.run([sys.executable,
        str(THIS / "materialize-neuroute-k8-prefilter.py"), "--contract",
        str(args.exact_contract), "--source-manifest", str(args.source_manifest),
        "--layout-manifest", str(args.layout_manifest), "--native-executable",
        str(args.codec_executable), "--treatment", "int8_uniform",
        "--prototype-limit", str(prototype_limit), "--output-root", str(root)],
        check=True)
    return manifest


def protocol(source: Path, refine_manifest: Path, refine_treatment: str,
             prefilter_manifest: Path, prefilter_treatment: str,
             prefilter_prototypes: int, refine_addresses: int, warmup: int,
             output: Path) -> Path:
    value = json.loads(source.read_text(encoding="utf-8"))
    value["coarse_k8_manifest"] = str(refine_manifest.resolve())
    value["coarse_k8_treatment"] = refine_treatment
    value["coarse_k8_query_arithmetic"] = "fp32"
    value["coarse_k8_prefilter_manifest"] = str(prefilter_manifest.resolve())
    value["coarse_k8_prefilter_treatment"] = prefilter_treatment
    value["coarse_k8_prefilter_query_arithmetic"] = "fp32"
    value["coarse_k8_prefilter_prototypes"] = prefilter_prototypes
    value["coarse_k8_refine_addresses"] = refine_addresses
    value["workers"] = [1]
    value["trace_repetitions"] = 1
    value["warmup_batches"] = warmup
    value["measured_batches"] = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(value))
    return output


def exact_protocol(source: Path, warmup: int, output: Path) -> Path:
    value = json.loads(source.read_text(encoding="utf-8"))
    for name in ("coarse_k8_prefilter_manifest",
                 "coarse_k8_prefilter_treatment",
                 "coarse_k8_prefilter_query_arithmetic",
                 "coarse_k8_prefilter_prototypes",
                 "coarse_k8_refine_addresses"):
        value.pop(name, None)
    value["workers"] = [1]
    value["trace_repetitions"] = 1
    value["warmup_batches"] = warmup
    value["measured_batches"] = 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(value))
    return output


def run_native(args: argparse.Namespace, partition: str, point: str,
               source_protocol: Path, request_rows: list[dict[str, Any]],
               oracle: np.ndarray, qrel_docs: list[np.ndarray],
               doc_rows: dict[int, np.ndarray], query_ids: list[str],
               document_ids: list[str], qrels: dict[str, dict[str, float]],
               modes: list[str], references: dict[tuple[int, str],
                                                   list[dict[str, Any]]],
               store_bytes: int, is_reference: bool) -> list[dict[str, Any]]:
    rows = []
    for seed in SEEDS:
        for mode in modes:
            report = (args.output_root / "reports" / partition /
                      f"{point}-{seed}-{mode}.json")
            report.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(args.native_executable),
                "--external-comparison-r4", str(source_protocol), str(seed),
                mode, args.execution, "1", str(report)], check=True)
            reference = None if is_reference else references[(seed, mode)]
            current = exact.query_metrics(report, request_rows, oracle,
                qrel_docs, doc_rows[seed], query_ids, document_ids, qrels,
                reference)
            rows.extend({"partition": partition, "prototype_limit": 8,
                "treatment": point, "seed": seed,
                "routing_storage_mode": mode, "query_arithmetic": "fp32",
                "coarse_store_bytes": store_bytes, **row} for row in current)
            if is_reference:
                references[(seed, mode)] = current
            exact.cleanup_report(report)
    return rows


def checkpoint(args: argparse.Namespace, partition: str, point: str,
               identity: dict[str, Any], compute: Any) -> list[dict[str, Any]]:
    path = args.output_root / "checkpoints" / f"{partition}-{point}.json"
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        amendment = args.contract_value.get("analysis_amendment", {})
        previous_identity = dict(identity)
        previous_identity["contract_sha256"] = amendment.get(
            "previous_contract_sha256")
        previous_identity["runner_sha256"] = amendment.get(
            "previous_runner_sha256")
        identity_matches = value.get("identity") == identity
        previous_identity_matches = bool(
            amendment.get("raw_native_grid_unchanged") is True and
            amendment.get("reuse_matching_previous_checkpoints") is True and
            value.get("identity") == previous_identity)
        if identity_matches or previous_identity_matches:
            return value["rows"]
    rows = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical({"identity": identity, "rows": rows}))
    return rows


def average_store_bytes(manifest_path: Path, treatment: str) -> int:
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if value["family"] == "neuroute_current_k8_physical_materialization":
        require(treatment == "fp32", "approximate K8 source treatment differs")
        return sum(int(row["bytes"]) for row in value["seeds"]) // len(SEEDS)
    require(value["family"] == "neuroute_k8_codec_materialization",
            "approximate K8 materialization differs")
    return sum(int(next(rep["bytes"] for rep in row["representations"]
                        if rep["id"] == treatment))
               for row in value["seeds"]) // len(SEEDS)


def partition_inputs(protocol_path: Path, data: dict[str, Any],
                     parent: dict[str, Any], layout_manifest: Path
                     ) -> tuple[Any, ...]:
    protocol_value = json.loads(protocol_path.read_text(encoding="utf-8"))
    request_rows = exact.requests(protocol_value, parent)
    positions = [int(row["native_query"]) for row in request_rows]
    oracle_by_position, _ = exact.scale.exact_oracle(data, positions,
                                                      exact.TOP_K)
    oracle = np.asarray([oracle_by_position[position] for position in positions],
                        dtype=np.int32)
    qrel_docs = exact.qrel_positions(parent, request_rows, data)
    return (request_rows, oracle, qrel_docs,
            exact.layout_doc_rows(layout_manifest),
            *exact.ndcg_rows(parent, request_rows))


def evaluate(args: argparse.Namespace, contract: dict[str, Any],
             partition: str, source_protocol: Path,
             data: dict[str, Any], parent: dict[str, Any],
             treatments: list[dict[str, Any]] | None,
             prefilter_manifests: dict[int, Path],
             refine_manifests: dict[str, Path]) -> tuple[list[dict[str, Any]],
                                                         list[dict[str, Any]]]:
    request_rows, oracle, qrel_docs, doc_rows, query_ids, document_ids, qrels = \
        partition_inputs(source_protocol, data, parent, args.layout_manifest)
    modes = ["int8"] if partition == "configuration" else MODES
    gates = {**contract["quality_gates"], "exact_scan_target_p95_ms":
        contract["timing"]["exact_scan_target_p95_ms"]}
    warmup = (contract["timing"]["configuration_warmup_batches"] if
              partition == "configuration" else
              contract["timing"]["selected_warmup_batches"])
    references: dict[tuple[int, str], list[dict[str, Any]]] = {}
    reference_protocol = exact_protocol(source_protocol, warmup,
        args.output_root / "physical" / partition / "reference-protocol.json")
    reference_identity = {"schema_version": 1, "partition": partition,
        "point": "exact_fp32_k8", "protocol_sha256": sha256(source_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "contract_sha256": sha256(args.contract), "modes": modes,
        "warmup_batches": warmup,
        "runner_sha256": sha256(Path(__file__)),
        "exact_runner_sha256": sha256(THIS /
            "run-neuroute-exact-k8-codec-frontier.py")}
    reference_rows = checkpoint(args, partition, "exact_fp32_k8",
        reference_identity, lambda: run_native(args, partition,
            "exact_fp32_k8", reference_protocol, request_rows, oracle,
            qrel_docs, doc_rows, query_ids, document_ids, qrels, modes,
            references, average_store_bytes(args.source_manifest, "fp32"),
            True))
    if not references:
        for seed in SEEDS:
            for mode in modes:
                references[(seed, mode)] = [row for row in reference_rows
                    if row["seed"] == seed and
                       row["routing_storage_mode"] == mode]
    reference_aggregate = exact.aggregate(reference_rows, reference_rows,
        {"id": "exact_fp32_k8"}, 8, gates)
    reference_aggregate["coarse_logical_bytes_touched"] = exact.summary.timing(
        [float(row["coarse_logical_bytes_touched"])
         for row in reference_rows])
    summaries = [{"id": "exact_fp32_k8", "prefilter_prototypes": 8,
        "refine_addresses": None, **reference_aggregate}]
    all_rows = list(reference_rows)
    points = treatments
    if points is None:
        points = [{"id": f"p{limit}-r{addresses}-{refine}",
            "prefilter_prototypes": limit, "refine_addresses": addresses,
            "refine_treatment": refine}
            for limit in contract["prefilter"]["prototype_limits"]
            for addresses in contract["refine"]["address_counts"]
            for refine in contract["refine"]["treatments"]]
    for treatment in points:
        point = treatment["id"]
        refine_manifest = refine_manifests[treatment["refine_treatment"]]
        prefilter_manifest = prefilter_manifests[
            treatment["prefilter_prototypes"]]
        physical = args.output_root / "physical" / partition / point
        current_protocol = protocol(source_protocol, refine_manifest,
            treatment["refine_treatment"], prefilter_manifest,
            contract["prefilter"]["treatment"],
            treatment["prefilter_prototypes"],
            treatment["refine_addresses"], warmup, physical / "protocol.json")
        prefilter_bytes = average_store_bytes(prefilter_manifest,
            contract["prefilter"]["treatment"])
        refine_bytes = average_store_bytes(refine_manifest,
                                             treatment["refine_treatment"])
        store_bytes = prefilter_bytes + refine_bytes
        identity = {"schema_version": 1, "partition": partition,
            "point": point, "protocol_sha256": sha256(source_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "contract_sha256": sha256(args.contract), "modes": modes,
            "warmup_batches": warmup,
            "runner_sha256": sha256(Path(__file__)),
            "exact_runner_sha256": sha256(THIS /
                "run-neuroute-exact-k8-codec-frontier.py"),
            "prefilter_materializer_sha256": sha256(THIS /
                "materialize-neuroute-k8-prefilter.py"),
            "prefilter_manifest_sha256": sha256(prefilter_manifest),
            "refine_manifest_sha256": sha256(refine_manifest)}
        rows = checkpoint(args, partition, point, identity,
            lambda p=current_protocol, s=store_bytes: run_native(args,
                partition, point, p, request_rows, oracle, qrel_docs, doc_rows,
                query_ids, document_ids, qrels, modes, references, s, False))
        all_rows.extend(rows)
        aggregate = exact.aggregate(rows, reference_rows, treatment, 8, gates)
        aggregate["coarse_logical_bytes_touched"] = exact.summary.timing([
            float(row["coarse_logical_bytes_touched"]) for row in rows])
        summaries.append({**treatment, **aggregate})
    return summaries, all_rows


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    args.contract_value = contract
    activation = contract["activation"]
    require(sha256(args.exact_contract) ==
                activation["exact_k8_contract_sha256"] and
            sha256(args.source_manifest) ==
                activation["coarse_k8_manifest_sha256"] and
            sha256(args.layout_manifest) ==
                activation["layout_manifest_sha256"] and
            sha256(args.configuration_protocol) ==
                activation["configuration_protocol_sha256"] and
            sha256(args.internal_protocol) ==
                activation["internal_protocol_sha256"],
            "approximate K8 activation differs")
    prefilter_manifests = {limit: materialize_prefilter(args, limit,
        args.output_root / "stores" / f"int8_uniform_k{limit}")
        for limit in contract["prefilter"]["prototype_limits"]}
    refine_manifests = {name: materialize(args, name,
        args.output_root / "stores" / name)
        for name in contract["refine"]["treatments"]}
    parent = exact.parent_protocol(json.loads(args.internal_protocol.read_text(
        encoding="utf-8")))
    data = exact.load_data(parent)
    configuration, configuration_rows = evaluate(args, contract,
        "configuration", args.configuration_protocol, data, parent, None,
        prefilter_manifests, refine_manifests)
    passing = [row for row in configuration
               if row.get("passes_quality_gates") and
                  row["id"] != "exact_fp32_k8"]
    common_inputs = {"native_executable_sha256": sha256(args.native_executable),
        "runner_sha256": sha256(Path(__file__)),
        "prefilter_materializer_sha256": sha256(THIS /
            "materialize-neuroute-k8-prefilter.py"),
        "prefilter_manifests_sha256": {str(limit): sha256(path)
            for limit, path in prefilter_manifests.items()},
        "refine_manifests_sha256": {name: sha256(path)
            for name, path in refine_manifests.items()}}
    if not passing:
        result = {"schema_version": 1,
            "family": "neuroute_approximate_k8_frontier_result",
            "contract_sha256": sha256(args.contract),
            "inputs": common_inputs,
            "configuration": {"summaries": configuration,
                              "selected_candidate": None},
            "internal_locked_replay": {"summaries": []},
            "selected_candidate": None,
            "post_hoc_stage_diagnostics": {
                "configuration": exact.stage_diagnostics(configuration_rows,
                    [row for row in configuration_rows
                     if row["treatment"] == "exact_fp32_k8"]),
                "internal": None},
            "decision": {"quality_licensed": False,
                "physical_target_met": False,
                "fallback_policy": "exact_fp32_k8",
                "production_licensed": False,
                "reason": "no_configuration_treatment_passed_quality_gates"}}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(result))
        return
    candidate = min(passing, key=lambda row: (row["mean_store_bytes"],
        row["coarse_ms"]["p95"], row["mean_ndcg_loss"], row["id"]))
    selected = {name: candidate[name] for name in
        ("id", "prefilter_prototypes", "refine_addresses",
         "refine_treatment")}
    internal, internal_rows = evaluate(args, contract,
        "internal_locked_replay", args.internal_protocol, data, parent,
        [selected], prefilter_manifests, refine_manifests)
    internal_candidate = next(row for row in internal
                              if row["id"] == selected["id"])
    result = {"schema_version": 1,
        "family": "neuroute_approximate_k8_frontier_result",
        "contract_sha256": sha256(args.contract),
        "inputs": {**common_inputs,
            "prefilter_manifest_sha256": sha256(prefilter_manifests[
                selected["prefilter_prototypes"]]),
            "refine_manifest_sha256": sha256(
                refine_manifests[selected["refine_treatment"]])},
        "configuration": {"summaries": configuration,
                           "selected_candidate": selected},
        "internal_locked_replay": {"summaries": internal},
        "selected_candidate": internal_candidate,
        "post_hoc_stage_diagnostics": {
            "configuration": exact.stage_diagnostics(configuration_rows,
                [row for row in configuration_rows
                 if row["treatment"] == "exact_fp32_k8"]),
            "internal": exact.stage_diagnostics(internal_rows,
                [row for row in internal_rows
                 if row["treatment"] == "exact_fp32_k8"])},
        "decision": {"quality_licensed":
            internal_candidate["passes_quality_gates"],
            "physical_target_met": internal_candidate["coarse_ms"]["p95"] <=
                contract["timing"]["exact_scan_target_p95_ms"],
            "production_licensed": False,
            "reason": "internal_partition_was_previously_opened"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = load_contract(THIS /
        "neuroute-approximate-k8-frontier.example.json")
    require(len(contract["prefilter"]["prototype_limits"]) *
            len(contract["refine"]["address_counts"]) *
            len(contract["refine"]["treatments"]) == 24,
            "approximate K8 matrix differs")
    print("NeuRoute approximate K8 frontier self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-approximate-k8-frontier.example.json")
    parser.add_argument("--exact-contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("source-manifest", "layout-manifest",
                 "configuration-protocol", "internal-protocol",
                 "native-executable", "codec-executable", "output-root",
                 "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--execution", choices=("portable", "sse2", "avx2"),
                        default="avx2")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("source_manifest", "layout_manifest",
                    "configuration_protocol", "internal_protocol",
                    "native_executable", "codec_executable", "output_root",
                    "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("all approximate K8 paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-approximate-k8-frontier: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
