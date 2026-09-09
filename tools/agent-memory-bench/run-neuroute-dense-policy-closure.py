#!/usr/bin/env python3
"""Bind #259--#262 evidence into the conditional dense-policy closure."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


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


def load(path: Path, family: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == family, f"dense closure {family} differs")
    return value


def contract(path: Path) -> dict[str, Any]:
    value = load(path, "neuroute_dense_policy_closure")
    require(value["routing_storage"] == {
                "selection": "explicit_user_configuration",
                "modes": ["int8", "nonlinear_int5_power_half"],
                "automatic_ram_based_switching": False} and
            value["execution"]["portable_default"] is True and
            value["execution"]["avx2_requires_cmake_opt_in"] is True and
            value["final_rerank"]["codec"] ==
                "symmetric_per_document_int8" and
            value["final_rerank"][
                "uniform_int5_is_negative_transfer_control"] is True and
            value["final_rerank"][
                "independent_heldout_revalidation_required"] is True and
            value["coarse_stage"]["must_be_in_full_timer"] is True and
            value["closure"]["status"] ==
                "closed_for_current_exact_k8_design",
            "dense closure policy contract differs")
    return value


def evidence_binding(result_path: Path, evidence_path: Path,
                     result_family: str, evidence_family: str
                     ) -> tuple[dict[str, Any], dict[str, Any]]:
    result = load(result_path, result_family)
    evidence = load(evidence_path, evidence_family)
    require(evidence.get("passed") is True and
            evidence.get("result_sha256") == sha256(result_path),
            f"dense closure {evidence_family} binding differs")
    return result, evidence


def point(result: dict[str, Any], identifier: str) -> dict[str, Any]:
    matches = [row for row in result["points"] if row["id"] == identifier]
    require(len(matches) == 1, f"dense closure point differs: {identifier}")
    return matches[0]


def run(args: argparse.Namespace) -> None:
    policy = contract(args.contract)
    audit, _ = evidence_binding(args.audit_result, args.audit_evidence,
        "neuroute_dense_performance_audit_result",
        "neuroute_dense_performance_audit_evidence")
    ceiling, _ = evidence_binding(args.ceiling_result, args.ceiling_evidence,
        "neuroute_final_rerank_ceiling_result",
        "neuroute_final_rerank_ceiling_evidence")
    storage, _ = evidence_binding(args.storage_result, args.storage_evidence,
        "neuroute_storage_execution_separation_result",
        "neuroute_storage_execution_separation_evidence")
    comparison, _ = evidence_binding(args.comparison_result,
        args.comparison_evidence,
        "neuroute_external_ann_comparison_result",
        "neuroute_external_ann_comparison_evidence")
    transfer = load(args.final_codec_transfer,
                    "neuroute_r4_final_codec_transfer_result")

    require(audit["decision"]["audit_gates_passed"] is True and
            audit["decision"]["audited_hot_path_frozen_for_followups"] is True,
            "dense closure #259 audit decision differs")
    require(ceiling["decision"]["implementation_ceiling_closed"] is True and
            ceiling["decision"]["implementation_frontier_continues"] is False,
            "dense closure #260 ceiling decision differs")
    require(storage["decision"]["gates_passed"] is True and
            storage["decision"]["safe_portable_build_is_default"] is True and
            storage["decision"]["avx2_is_explicit_cmake_opt_in"] is True and
            storage["decision"][
                "persisted_bytes_are_execution_independent"] is True and
            storage["decision"][
                "storage_mode_is_explicit_user_configuration"] is True,
            "dense closure #261 storage policy differs")
    require(comparison["methodology"][
                "neuroute_full_timer_includes_persisted_k8_coarse_stage"] is True and
            comparison["decision"]["single_universal_winner_selected"] is False and
            comparison["decision"]["routing_codec_is_user_selected"] is True and
            comparison["decision"]["automatic_ram_based_codec_switching"] is False and
            comparison["decision"]["final_document_codec"] ==
                "symmetric_per_document_int8",
            "dense closure #262 comparison policy differs")
    require(transfer["decision"]["int8_corrective_gate_passed"] is True and
            transfer["decision"]["uniform_int5_transfer_gate_passed"] is False and
            transfer["decision"]["requires_independent_heldout_revalidation"] is True,
            "dense closure final-codec transfer differs")

    r4_int8 = point(comparison, "neuroute_r4/int8")
    r4_int5 = point(comparison, "neuroute_r4/nonlinear_int5_power_half")
    exact = point(comparison, "faiss_exact_flat/fixed")
    require(r4_int8["id"] not in comparison["pareto"][
                "p95_latency_vs_ndcg"] and
            r4_int5["id"] not in comparison["pareto"][
                "p95_latency_vs_ndcg"] and
            r4_int8["id"] in comparison["pareto"][
                "artifact_bytes_vs_ndcg"] and
            r4_int5["id"] in comparison["pareto"][
                "artifact_bytes_vs_ndcg"],
            "dense closure R4 Pareto interpretation differs")
    require(r4_int8["coarse_stage_ms"]["p95"] >
                r4_int8["post_shortlist_cascade_ms"]["p95"] and
            r4_int5["coarse_stage_ms"]["p95"] >
                r4_int5["post_shortlist_cascade_ms"]["p95"],
            "dense closure K8 latency dominance differs")

    activation = {name + "_sha256": sha256(getattr(args, name)) for name in (
        "audit_result", "audit_evidence", "ceiling_result", "ceiling_evidence",
        "storage_result", "storage_evidence", "comparison_result",
        "comparison_evidence", "final_codec_transfer")}
    result = {"schema_version": 1,
        "family": "neuroute_dense_policy_closure_result",
        "contract_sha256": sha256(args.contract), "activation": activation,
        "measured_policy": {
            "routing_storage_modes": policy["routing_storage"]["modes"],
            "routing_storage_selection": "explicit_user_configuration",
            "automatic_ram_based_switching": False,
            "execution_default": "portable",
            "optional_execution_kernels": ["sse2", "avx2"],
            "avx2_requires_cmake_opt_in": True,
            "persisted_bytes_are_execution_independent": True,
            "final_document_codec": "symmetric_per_document_int8",
            "current_coarse_stage": policy["coarse_stage"][
                "current_implementation"]},
        "key_measurements": {
            "r4_int8_p95_ms": r4_int8["single_worker_ms"]["p95"],
            "r4_int5_p95_ms": r4_int5["single_worker_ms"]["p95"],
            "r4_int8_ndcg_at_10": r4_int8["quality"]["mean_ndcg_at_10"],
            "r4_int5_ndcg_at_10": r4_int5["quality"]["mean_ndcg_at_10"],
            "faiss_exact_flat_p95_ms": exact["single_worker_ms"]["p95"],
            "int8_coarse_p95_ms": r4_int8["coarse_stage_ms"]["p95"],
            "int8_post_shortlist_p95_ms": r4_int8[
                "post_shortlist_cascade_ms"]["p95"],
            "final_int8_mean_ndcg_loss_vs_fp32": transfer["aggregate"][
                "mean_ndcg_loss_vs_fp32"]["int8"],
            "final_uniform_int5_mean_ndcg_loss_vs_fp32": transfer[
                "aggregate"]["mean_ndcg_loss_vs_fp32"]["uniform_int5"]},
        "decision": {"gates_passed": True,
            "status": "closed_for_current_exact_k8_design",
            "single_universal_winner_selected": False,
            "current_r4_is_latency_ndcg_pareto": False,
            "current_r4_is_artifact_ndcg_pareto": True,
            "downstream_implementation_ceiling_closed": True,
            "final_int8_generalization_is_fully_licensed": False,
            "lexical_work_started": False,
            "reopen_conditions": policy["closure"]["reopen_conditions"]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    value = contract(THIS / "neuroute-dense-policy-closure.example.json")
    require(value["closure"]["universal_winner_claimed"] is False and
            value["closure"]["lexical_work_started"] is False and
            len(value["closure"]["reopen_conditions"]) == 4,
            "dense closure self-test differs")
    print("NeuRoute dense policy closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-dense-policy-closure.example.json")
    for name in ("audit-result", "audit-evidence", "ceiling-result",
                 "ceiling-evidence", "storage-result", "storage-evidence",
                 "comparison-result", "comparison-evidence",
                 "final-codec-transfer", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"contract", "self_test"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all dense policy closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"run-neuroute-dense-policy-closure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
