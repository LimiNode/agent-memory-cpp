#!/usr/bin/env python3
"""Write compact fail-closed evidence for the fixed Top-M router frontier."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def protocol_closure(path: Path) -> dict[str, str]:
    pending = [path.resolve()]
    seen: set[Path] = set()
    result: dict[str, str] = {}
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        result[str(current)] = sha256(current)
        value = json.loads(current.read_text(encoding="utf-8"))
        for field in ("routing_kernel_protocol", "parent_protocol",
                      "final_int8_layout_manifest", "coarse_k8_manifest"):
            if field in value:
                pending.append(Path(value[field]).resolve())
        for field in ("native_input_manifest", "document_id_rank_file",
                      "evaluation_document_ids", "evaluation_query_ids",
                      "evaluation_qrels"):
            if field in value:
                payload = Path(value[field]).resolve()
                result[str(payload)] = sha256(payload)
    return dict(sorted(result.items()))


def source_hashes(root: Path) -> dict[str, str]:
    names = ("run-neuroute-fixed-top-m-router.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(root / name) for name in names}


def passes(row: dict[str, Any], gates: dict[str, Any]) -> bool:
    return bool(row["mean_ndcg_loss"] <= gates[
        "maximum_mean_downstream_ndcg_loss"] and
        row["maximum_stratum_mean_ndcg_loss"] <= gates[
            "maximum_every_seed_downstream_ndcg_loss"] and
        row["mean_final_top10_overlap"] >= gates[
            "minimum_mean_final_top10_overlap"] and
        row["mean_candidate_reference_retention"] >= gates[
            "minimum_mean_fp32_stage_retention_at_candidate"] and
        row["mean_hamming_reference_overlap"] >= gates[
            "minimum_mean_fp32_stage_overlap_at_hamming768"] and
        row["mean_adc_reference_overlap"] >= gates[
            "minimum_mean_fp32_stage_overlap_at_adc64"])


def gate_distance(row: dict[str, Any], gates: dict[str, Any]) -> float:
    return (max(0.0, row["mean_ndcg_loss"] - gates[
                "maximum_mean_downstream_ndcg_loss"]) +
            max(0.0, row["maximum_stratum_mean_ndcg_loss"] - gates[
                "maximum_every_seed_downstream_ndcg_loss"]) +
            max(0.0, gates["minimum_mean_final_top10_overlap"] - row[
                "mean_final_top10_overlap"]) +
            max(0.0, gates["minimum_mean_fp32_stage_retention_at_candidate"] -
                row["mean_candidate_reference_retention"]) +
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_hamming768"] -
                row["mean_hamming_reference_overlap"]) +
            max(0.0, gates["minimum_mean_fp32_stage_overlap_at_adc64"] -
                row["mean_adc_reference_overlap"]))


def validate_manifest(binding: dict[str, Any]) -> None:
    path = Path(binding["path"])
    require(path.is_absolute() and sha256(path) == binding["sha256"],
            "fixed Top-M shortlist manifest differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") ==
            "neuroute_local_k8_address_shortlist_materialization",
            "fixed Top-M shortlist family differs")
    for row in value["seeds"]:
        payload = Path(row["path"])
        require(payload.is_absolute() and payload.stat().st_size == row["bytes"] and
                sha256(payload) == row["sha256"],
                "fixed Top-M shortlist payload differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        gates = {"maximum_mean_downstream_ndcg_loss": .004,
            "maximum_every_seed_downstream_ndcg_loss": .004,
            "minimum_mean_final_top10_overlap": .99,
            "minimum_mean_fp32_stage_retention_at_candidate": .99,
            "minimum_mean_fp32_stage_overlap_at_hamming768": .98,
            "minimum_mean_fp32_stage_overlap_at_adc64": .95}
        row = {"mean_ndcg_loss": .001, "maximum_stratum_mean_ndcg_loss": .002,
            "mean_final_top10_overlap": .995,
            "mean_candidate_reference_retention": .995,
            "mean_hamming_reference_overlap": .99,
            "mean_adc_reference_overlap": .97}
        require(passes(row, gates), "fixed Top-M evidence self-test failed")
        return 0
    require(all(getattr(args, name) is not None for name in (
        "contract", "result", "layout_manifest", "k8_manifest",
        "configuration_protocol", "native_executable", "output")),
        "fixed Top-M evidence inputs are required")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") ==
            "neuroute_fixed_top_m_router_frontier_result",
            "fixed Top-M result family differs")
    expected = {"contract_sha256": sha256(args.contract),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "configuration_protocol_sha256": sha256(args.configuration_protocol),
        "configuration_protocol_closure_sha256": protocol_closure(
            args.configuration_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "source_files_sha256": source_hashes(Path(__file__).resolve().parent)}
    require(all(result["inputs"].get(name) == value
                for name, value in expected.items()),
            "fixed Top-M input bytes differ")
    for binding in result["configuration_shortlist_manifests"].values():
        validate_manifest(binding)
    for binding in result["internal_shortlist_manifests"].values():
        validate_manifest(binding)
    gates = contract["quality_gates"]
    for partition in ("configuration", "locked_internal"):
        for row in result[partition]:
            require(row["passes_quality_gates"] == passes(row, gates),
                    f"fixed Top-M gate differs: {partition}/{row['id']}")
            if row.get("address_budget") is not None:
                require(row["passes_registered_gate"] ==
                        (passes(row, gates) and row["address_budget"] <=
                         gates["maximum_native_address_budget"]),
                        "fixed Top-M registered gate differs")
    candidates = []
    for treatment in contract["treatments"]:
        rows = [row for row in result["configuration"]
            if row.get("address_budget") and
               row["id"].startswith(treatment + "-m")]
        passing_rows = [row for row in rows if row["passes_registered_gate"]]
        candidates.append(min(passing_rows, key=lambda row: (
            row["address_budget"], row["total_ms"]["p95"], row["id"]))
            if passing_rows else min(rows, key=lambda row: (
                gate_distance(row, gates), row["address_budget"], row["id"])))
    candidates.sort(key=lambda row: (not row["passes_registered_gate"],
        gate_distance(row, gates), row["address_budget"],
        row["total_ms"]["p95"], row["id"]))
    require(result["internal_opened_from_configuration"] ==
            [row["id"] for row in candidates[:2]],
            "fixed Top-M configuration selection differs")
    passing_internal = [row for row in result["locked_internal"]
        if row.get("passes_registered_gate") and row["id"] != "global_fp32_k8"]
    selected = (min(passing_internal, key=lambda row: (row["address_budget"],
        row["total_ms"]["p95"], row["id"])) if passing_internal else None)
    require(result["decision"]["selected"] == selected and
            result["decision"]["fixed_top_m_passed"] == (selected is not None) and
            result["decision"]["production_licensed"] is False,
            "fixed Top-M decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_fixed_top_m_router_frontier_evidence",
        "result_sha256": sha256(args.result), "inputs": expected,
        "configuration_points": len(result["configuration"]),
        "locked_internal_points": len(result["locked_internal"]),
        "selected": selected["id"] if selected else None,
        "fixed_top_m_passed": selected is not None,
        "activate_shortlist_generator_bakeoff": True,
        "result_byte_replay_passed": True, "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
