#!/usr/bin/env python3
"""Replay qrels-sensitive K8/K32 decisions from persisted document identities."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import neuroute_authoritative_qrels as authoritative

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
SEEDS = [2026082701, 2026082702, 2026082703]


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exact = load("neuroute_k8_closure_exact",
             "run-neuroute-exact-k8-codec-frontier.py")
approximate = load("neuroute_k8_closure_approximate",
                   "run-neuroute-approximate-k8-frontier.py")
k32 = load("neuroute_k8_closure_k32",
           "run-neuroute-k32-physical-codec.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def checkpoint(path: Path, hashes: dict[str, str],
               receipt: dict[str, Any]) -> list[dict[str, Any]]:
    require(path.is_file(), f"K8 evidence checkpoint is absent: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value.get("identity"), dict) and
            isinstance(value.get("rows"), list) and value["rows"],
            f"K8 evidence checkpoint differs: {path.name}")
    require(value["identity"].get("authoritative_e5_receipt") == receipt,
            f"K8 evidence checkpoint receipt differs: {path.name}")
    hashes[path.name] = exact.sha256(path)
    return value["rows"]


EXACT_QUALITY_FIELDS = (
    "mean_ndcg_loss",
    "maximum_stratum_mean_ndcg_loss",
    "stratum_mean_ndcg_losses",
    "mean_final_top10_overlap",
    "mean_candidate_reference_retention",
    "mean_hamming_reference_overlap",
    "mean_adc_reference_overlap",
    "passes_quality_gates",
)

K32_QUALITY_FIELDS = (
    "mean_ndcg_loss",
    "maximum_seed_mean_ndcg_loss",
    "seed_mean_ndcg_losses",
    "mean_final_top10_overlap",
    "mean_candidate_reference_retention",
    "mean_hamming_reference_overlap",
    "mean_adc_reference_overlap",
    "worst_query_ndcg_loss",
    "passes_quality_gates",
)


def compare_fields(replayed: dict[str, Any], reported: dict[str, Any],
                   fields: tuple[str, ...], message: str) -> None:
    require({name: replayed[name] for name in fields} ==
            {name: reported[name] for name in fields}, message)


def require_result_receipt(result: dict[str, Any], receipt: dict[str, Any],
                           message: str) -> None:
    inputs = result.get("inputs", {})
    require(inputs.get("authoritative_e5_receipt") == receipt and
            inputs.get("authoritative_qrels_validator_sha256") == exact.sha256(
                THIS / "neuroute_authoritative_qrels.py"), message)


def quality_context(protocol_path: Path, parent: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], list[str], list[str],
                               dict[str, dict[str, float]]]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    request_rows = exact.requests(protocol, parent)
    query_ids, document_ids, qrels = exact.ndcg_rows(parent, request_rows)
    return request_rows, query_ids, document_ids, qrels


def rebound_rows(rows: list[dict[str, Any]],
                 context: tuple[list[dict[str, Any]], list[str], list[str],
                                dict[str, dict[str, float]]]
                 ) -> list[dict[str, Any]]:
    request_rows, query_ids, document_ids, qrels = context
    return exact.rebind_ndcg(rows, request_rows, query_ids, document_ids, qrels)


def exact_replay(args: argparse.Namespace, parent: dict[str, Any],
                 receipt: dict[str, Any],
                 hashes: dict[str, str]) -> dict[str, Any]:
    contract = exact.planner.load_contract(args.exact_contract)
    result = json.loads(args.exact_result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_exact_k8_codec_frontier_result" and
            result.get("contract_sha256") == exact.sha256(args.exact_contract),
            "K8 evidence exact result binding differs")
    require_result_receipt(result, receipt,
                           "K8 evidence exact result receipt differs")
    treatments = {row["id"]: row for row in exact.planner.treatments(contract)}
    root = args.exact_output_root / "checkpoints"
    contexts = {
        "configuration": quality_context(args.configuration_protocol, parent),
        "internal_locked_replay": quality_context(
            args.internal_protocol, parent)}

    references: dict[str, list[dict[str, Any]]] = {}
    for partition, protocol in (("configuration", args.configuration_protocol),
                                ("internal_locked_replay",
                                 args.internal_protocol)):
        path = root / f"{partition}-k8-fp32.json"
        references[partition] = rebound_rows(checkpoint(path, hashes, receipt),
                                             contexts[partition])

    replayed_configuration = []
    for reported in result["configuration"]["summaries"]:
        point = f"k{int(reported['prototype_limit'])}-{reported['id']}"
        rows = rebound_rows(checkpoint(
            root / f"configuration-{point}.json", hashes, receipt),
            contexts["configuration"])
        replayed = exact.aggregate(rows, references["configuration"],
            treatments[reported["id"]], int(reported["prototype_limit"]),
            contract["k8_gate"])
        compare_fields(replayed, reported, EXACT_QUALITY_FIELDS,
                       f"K8 exact configuration quality differs: {point}")
        replayed_configuration.append({**reported,
            **{name: replayed[name] for name in EXACT_QUALITY_FIELDS}})

    passing = [row for row in replayed_configuration
               if row["passes_quality_gates"] and row["id"] != "fp32"]
    require(passing, "K8 exact evidence has no configuration candidate")
    selected_configuration = min(passing, key=lambda row: (
        row["mean_store_bytes"], row["coarse_ms"]["p95"],
        row["mean_ndcg_loss"], row["id"]))
    require(result["configuration"]["selected_candidate"] == {
        "prototype_limit": selected_configuration["prototype_limit"],
        "id": selected_configuration["id"]},
        "K8 exact configuration selection differs")

    selected = result["selected_candidate"]
    selected_point = f"k{int(selected['prototype_limit'])}-{selected['id']}"
    selected_rows = rebound_rows(checkpoint(
        root / f"internal_locked_replay-{selected_point}.json", hashes,
        receipt),
        contexts["internal_locked_replay"])
    replayed_selected = exact.aggregate(selected_rows,
        references["internal_locked_replay"], treatments[selected["id"]],
        int(selected["prototype_limit"]), contract["k8_gate"])
    compare_fields(replayed_selected, selected, EXACT_QUALITY_FIELDS,
                   "K8 exact selected quality differs")

    execution = []
    for reported in result["selected_execution_frontier"]:
        arithmetic = reported["query_arithmetic"]
        path = root / ("internal_execution_closure-" + selected_point +
                       f"-q{arithmetic}-w1.json")
        rows = rebound_rows(checkpoint(path, hashes, receipt),
                            contexts["internal_locked_replay"])
        replayed = exact.aggregate(rows,
            references["internal_locked_replay"], treatments[selected["id"]],
            int(selected["prototype_limit"]), contract["k8_gate"])
        compare_fields(replayed, reported, EXACT_QUALITY_FIELDS,
                       f"K8 exact execution quality differs: {arithmetic}")
        execution.append({**reported,
            **{name: replayed[name] for name in EXACT_QUALITY_FIELDS}})
    passing_execution = [row for row in execution if row["passes_quality_gates"]]
    fastest = (min(passing_execution, key=lambda row: (
        row["coarse_ms"]["p95"], row["query_arithmetic"]))
        if passing_execution else None)
    decision = result["decision"]
    require(decision["quality_licensed"] ==
                replayed_selected["passes_quality_gates"] and
            decision["exact_scan_target_met"] ==
                bool(fastest and fastest["meets_exact_scan_target"]) and
            decision["fastest_quality_passing_query_arithmetic"] ==
                (None if fastest is None else fastest["query_arithmetic"]),
            "K8 exact decision replay differs")
    return {"selected_candidate": selected_point,
            "configuration_candidate": selected_configuration["id"],
            "quality_licensed": decision["quality_licensed"],
            "exact_scan_target_met": decision["exact_scan_target_met"]}


def approximate_replay(args: argparse.Namespace, parent: dict[str, Any],
                       receipt: dict[str, Any],
                       hashes: dict[str, str]) -> dict[str, Any]:
    contract = approximate.load_contract(args.approximate_contract)
    result = json.loads(args.approximate_result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_approximate_k8_frontier_result" and
            result.get("contract_sha256") ==
                exact.sha256(args.approximate_contract),
            "K8 evidence approximate result binding differs")
    require_result_receipt(result, receipt,
                           "K8 evidence approximate result receipt differs")
    root = args.approximate_output_root / "checkpoints"
    context = quality_context(args.configuration_protocol, parent)
    reference = rebound_rows(checkpoint(
        root / "configuration-exact_fp32_k8.json", hashes, receipt),
        context)
    gates = {**contract["quality_gates"], "exact_scan_target_p95_ms":
             contract["timing"]["exact_scan_target_p95_ms"]}
    passing = []
    for reported in result["configuration"]["summaries"]:
        if reported["id"] == "exact_fp32_k8":
            rows = reference
            treatment = {"id": "exact_fp32_k8"}
        else:
            rows = rebound_rows(checkpoint(
                root / f"configuration-{reported['id']}.json", hashes,
                receipt),
                context)
            treatment = {name: reported[name] for name in
                ("id", "prefilter_prototypes", "refine_addresses",
                 "refine_treatment")}
        replayed = exact.aggregate(rows, reference, treatment, 8, gates)
        compare_fields(replayed, reported, EXACT_QUALITY_FIELDS,
                       f"K8 approximate quality differs: {reported['id']}")
        if reported["id"] != "exact_fp32_k8" and replayed[
                "passes_quality_gates"]:
            passing.append(reported["id"])
    require(not passing and result["selected_candidate"] is None and
            result["decision"] == {"quality_licensed": False,
                "physical_target_met": False,
                "fallback_policy": "exact_fp32_k8",
                "production_licensed": False,
                "reason": "no_configuration_treatment_passed_quality_gates"},
            "K8 approximate negative decision replay differs")
    return {"passing_treatments": passing,
            "fallback_policy": result["decision"]["fallback_policy"]}


def k32_replay(args: argparse.Namespace, parent: dict[str, Any],
               receipt: dict[str, Any],
               hashes: dict[str, str]) -> dict[str, Any]:
    contract = k32.load_contract(args.k32_contract)
    result = json.loads(args.k32_result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_k32_physical_codec_result" and
            result.get("contract_sha256") == exact.sha256(args.k32_contract),
            "K32 evidence result binding differs")
    require_result_receipt(result, receipt,
                           "K32 evidence result receipt differs")
    protocol = json.loads(args.r4_protocol.read_text(encoding="utf-8"))
    request_rows = exact.requests(protocol, parent)
    reports = {}
    for seed in SEEDS:
        for treatment in contract["treatments"]:
            path = (args.k32_report_root / "resident" /
                    f"seed-{seed}-{treatment}-w1.json")
            require(path.is_file(), f"K32 evidence report is absent: {path.name}")
            hashes[f"k32/{path.name}"] = exact.sha256(path)
            reports[(seed, treatment)] = json.loads(
                path.read_text(encoding="utf-8"))
    replayed = k32.quality(reports, request_rows, parent,
                           contract["quality_gates"])
    reported = {row["treatment"]: row for row in result["quality"]}
    for row in replayed:
        compare_fields(row, reported[row["treatment"]], K32_QUALITY_FIELDS,
                       f"K32 quality differs: {row['treatment']}")
    passing = [row["treatment"] for row in replayed
               if row["passes_quality_gates"]]
    require(passing == result["decision"]["quality_passing"],
            "K32 quality decision replay differs")
    return {"quality_passing": passing}


def run(args: argparse.Namespace) -> None:
    parent = exact.parent_protocol(json.loads(
        args.internal_protocol.read_text(encoding="utf-8")))
    receipt = exact.authoritative_receipt(parent)
    require(receipt == authoritative.validate_e5_root(
        "de-1m", Path(parent["evaluation_document_ids"]).parent),
        "K8 authoritative receipt changed during evidence replay")
    hashes: dict[str, str] = {}
    exact_decision = exact_replay(args, parent, receipt, hashes)
    approximate_decision = approximate_replay(args, parent, receipt, hashes)
    k32_decision = k32_replay(args, parent, receipt, hashes)
    output = {"schema_version": 1,
        "family": "neuroute_k8_codec_closure_evidence",
        "passed": True,
        "authoritative_qrels_validator_sha256": exact.sha256(
            THIS / "neuroute_authoritative_qrels.py"),
        "authoritative_e5_receipt": receipt,
        "results_sha256": {
            "exact_k8": exact.sha256(args.exact_result),
            "approximate_k8": exact.sha256(args.approximate_result),
            "physical_k32": exact.sha256(args.k32_result)},
        "quality_artifacts_sha256": dict(sorted(hashes.items())),
        "writer_sha256": exact.sha256(Path(__file__)),
        "runner_sources_sha256": {
            "exact": exact.sha256(THIS /
                "run-neuroute-exact-k8-codec-frontier.py"),
            "approximate": exact.sha256(THIS /
                "run-neuroute-approximate-k8-frontier.py"),
            "k32": exact.sha256(THIS /
                "run-neuroute-k32-physical-codec.py")},
        "quality_replay": {"exact_k8": exact_decision,
            "approximate_k8": approximate_decision,
            "physical_k32": k32_decision},
        "authoritative_qrels_to_final_document_ids_replay_passed": True,
        "aggregate_and_decision_replay_passed": True,
        "native_latency_replayed": False,
        "receipt_bound_checkpoint_identities_verified": True,
        "receipt_bound_result_inputs_verified": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    reported = {name: 1.0 for name in EXACT_QUALITY_FIELDS}
    compare_fields(dict(reported), reported, EXACT_QUALITY_FIELDS,
                   "K8 evidence self-test differs")
    authoritative.self_test()
    print("NeuRoute K8 codec closure evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("exact-contract", "exact-result", "exact-output-root",
                 "approximate-contract", "approximate-result",
                 "approximate-output-root", "k32-contract", "k32-result",
                 "k32-report-root", "configuration-protocol",
                 "internal-protocol", "r4-protocol", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name != "self_test"):
            parser.error("all K8 codec closure evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError,
            json.JSONDecodeError) as error:
        print(f"write-neuroute-k8-codec-closure-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
