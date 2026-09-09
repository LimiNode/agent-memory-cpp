#!/usr/bin/env python3
"""Materialize teacher-blind actual-document representatives for frozen DE-1M."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy


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


planner = load("neuroute_r4_document_representatives_planner",
               "plan-neuroute-r4-document-representatives.py")
parent = load("neuroute_r4_document_representatives_parent",
              "run-neuroute-replication-topology.py")
scale = parent.scale
task = parent.task
multi = parent.multi
centroid = multi.centroid


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
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def array_sha256(value: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.asarray(value).tobytes(order="C")).hexdigest()


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-r4-document-representatives.py",
        "run-neuroute-r4-document-representatives.py",
        "run-neuroute-replication-topology.py",
        "run-neuroute-address-multi-prototype.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace
                        ) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = {
        "replication_topology_result_sha256": sha256(args.replication_result),
        "replication_topology_evidence_sha256": sha256(args.replication_evidence),
        "feasible_frontier_result_sha256": sha256(args.feasible_result),
        "feasible_frontier_evidence_sha256": sha256(args.feasible_evidence),
        "width_materialization_sha256": sha256(
            args.width_materialization_root / "manifest.json"),
        "de_1m_e5_manifest_sha256": sha256(args.de_1m_e5_root / "manifest.json"),
        "de_1m_input_manifest_sha256": sha256(
            args.de_1m_input_root / "manifest.json"),
    }
    require(actual == contract["activation"],
            f"R4 representative activation bytes differ: {actual!r}")
    replication = json.loads(args.replication_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.replication_evidence.read_text(encoding="utf-8"))
    feasible = json.loads(args.feasible_result.read_text(encoding="utf-8"))
    feasible_evidence = json.loads(args.feasible_evidence.read_text(encoding="utf-8"))
    require(replication.get("family") == "neuroute_replication_topology_result"
            and replication.get("decision", {}).get(
                "replication_topology_gate_passed") is False
            and evidence.get("passed") is True
            and evidence.get("result_byte_replay_passed") is True,
            "R4 representative replication parent differs")
    require(feasible.get("family") == "neuroute_feasible_candidate_frontier_result"
            and feasible_evidence.get("passed") is True
            and feasible_evidence.get("result_byte_replay_passed") is True,
            "R4 representative feasible parent differs")
    materialization = json.loads((args.width_materialization_root /
                                  "manifest.json").read_text(encoding="utf-8"))
    return materialization, feasible_evidence


def normalized_rows(values: numpy.ndarray) -> numpy.ndarray:
    result = numpy.asarray(values, dtype=numpy.float32).copy()
    norms = numpy.linalg.norm(result, axis=1).astype(numpy.float32)
    require(numpy.all(norms > 0.0), "R4 representative zero document differs")
    result /= norms[:, None]
    return result


def grouped_choice(values: numpy.ndarray, sorted_positions: numpy.ndarray,
                   starts: numpy.ndarray, repeated_counts: numpy.ndarray,
                   choose_maximum: bool, sentinel: numpy.int32) -> numpy.ndarray:
    ordered = values[sorted_positions]
    extreme = ((numpy.maximum.reduceat if choose_maximum else numpy.minimum.reduceat)(
        ordered, starts))
    matches = ordered == numpy.repeat(extreme, repeated_counts)
    candidates = numpy.where(matches, sorted_positions, sentinel)
    return numpy.minimum.reduceat(candidates, starts).astype(numpy.int32)


def build_actual_representatives(documents: numpy.ndarray,
                                 addresses: numpy.ndarray,
                                 index: dict[str, Any], maximum: int,
                                 batch_size: int = 32768) -> tuple[
                                     numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    counts = index["counts"]
    occupied, means = centroid.build_centroids(
        documents, addresses, counts, batch_size)
    occupied_counts = counts[occupied].astype(numpy.int64)
    effective = numpy.minimum(occupied_counts, maximum).astype(numpy.uint8)
    representatives = numpy.full((maximum, len(occupied)), -1, dtype=numpy.int32)
    address_to_row = numpy.full(counts.size, -1, dtype=numpy.int32)
    address_to_row[occupied] = numpy.arange(len(occupied), dtype=numpy.int32)
    sorted_positions = numpy.asarray(index["order"], dtype=numpy.int32)
    starts = index["offsets"][occupied].astype(numpy.int64)
    repeated_counts = occupied_counts.astype(numpy.int64)
    sentinel = numpy.int32(len(addresses))

    similarity = numpy.empty(len(addresses), dtype=numpy.float32)
    for start in range(0, len(addresses), batch_size):
        stop = min(len(addresses), start + batch_size)
        rows = address_to_row[numpy.asarray(addresses[start:stop], dtype=numpy.uint32)]
        similarity[start:stop] = numpy.sum(
            numpy.asarray(documents[start:stop], dtype=numpy.float32) * means[rows],
            axis=1, dtype=numpy.float32)
    chosen = grouped_choice(similarity, sorted_positions, starts,
                            repeated_counts, True, sentinel)
    require(numpy.all(chosen < len(addresses)),
            "R4 first representative selection failed")
    representatives[0] = chosen
    selected = numpy.zeros(len(addresses), dtype=numpy.bool_)
    selected[chosen] = True
    best_similarity = numpy.full(len(addresses), -numpy.inf, dtype=numpy.float32)

    for slot in range(1, maximum):
        active = effective > slot
        reference = numpy.zeros((len(occupied), documents.shape[1]),
                                dtype=numpy.float32)
        reference[active] = normalized_rows(documents[chosen[active]])
        for start in range(0, len(addresses), batch_size):
            stop = min(len(addresses), start + batch_size)
            rows = address_to_row[numpy.asarray(
                addresses[start:stop], dtype=numpy.uint32)]
            current = numpy.sum(
                numpy.asarray(documents[start:stop], dtype=numpy.float32)
                * reference[rows], axis=1, dtype=numpy.float32)
            best_similarity[start:stop] = numpy.maximum(
                best_similarity[start:stop], current)
        best_similarity[selected] = numpy.inf
        chosen = grouped_choice(best_similarity, sorted_positions, starts,
                                repeated_counts, False, sentinel)
        chosen_active = chosen[active]
        require(numpy.all(chosen_active < len(addresses)),
                f"R4 representative selection failed at slot {slot}")
        representatives[slot, active] = chosen_active
        selected[chosen_active] = True
        del reference
    return occupied, representatives, effective


def audit_representatives(addresses: numpy.ndarray, occupied: numpy.ndarray,
                          representatives: numpy.ndarray,
                          effective: numpy.ndarray,
                          counts: numpy.ndarray) -> dict[str, Any]:
    expected = numpy.minimum(counts[occupied], representatives.shape[0]).astype(
        numpy.uint8)
    require(numpy.array_equal(effective, expected),
            "R4 effective representative counts differ")
    active_count = 0
    for row in range(len(occupied)):
        count = int(effective[row])
        values = representatives[:count, row]
        require(numpy.all(values >= 0)
                and len(numpy.unique(values)) == count
                and numpy.all(addresses[values] == occupied[row])
                and numpy.all(representatives[count:, row] == -1),
                f"R4 representative audit differs at row {row}")
        active_count += count
    return {
        "active_representative_count": active_count,
        "all_active_representatives_unique_within_address": True,
        "all_representative_primary_addresses_match": True,
        "effective_count_matches_min_posting_count_32": True,
    }


def artifact(path: Path, role: str, value: numpy.ndarray) -> dict[str, Any]:
    numpy.save(path, value, allow_pickle=False)
    return {
        "role": role,
        "path": path.name,
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "dtype": str(value.dtype),
        "shape": [int(current) for current in value.shape],
    }


def materialize(contract: dict[str, Any], materialization: dict[str, Any],
                args: argparse.Namespace) -> list[dict[str, Any]]:
    scale_config = next(row for row in parent.prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    rows = []
    maximum = int(contract["representatives"][
        "maximum_actual_documents_per_address"])
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, current_k8, current_effective, current_members = (
            multi.build_nested_prototypes(data["documents"], addresses, index, 8))
        actual_occupied, representatives, effective = build_actual_representatives(
            data["documents"], addresses, index, maximum)
        require(numpy.array_equal(actual_occupied, occupied),
                "R4 occupied address order differs")
        audit = audit_representatives(
            addresses, occupied, representatives, effective, index["counts"])
        root = args.materialization_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        artifacts = [
            artifact(root / "occupied-addresses.npy", "occupied_addresses", occupied),
            artifact(root / "actual-document-positions-k32.npy",
                     "actual_document_positions_k32", representatives),
            artifact(root / "actual-document-effective-count.npy",
                     "actual_document_effective_count", effective),
            artifact(root / "current-k8-document-members.npy",
                     "current_k8_document_members", current_members),
        ]
        prefix_counts = {
            str(prefix): int(numpy.minimum(effective, prefix).sum(dtype=numpy.int64))
            for prefix in contract["representatives"]["reported_prefixes"]}
        rows.append({
            "seed": seed,
            "document_addresses_sha256": route["document_addresses"]["sha256"],
            "occupied_address_count": len(occupied),
            "posting_count": int(index["counts"].sum(dtype=numpy.int64)),
            "current_k8_prototypes_sha256": array_sha256(current_k8),
            "current_k8_effective_sha256": array_sha256(current_effective),
            "actual_representative_prefix_counts": prefix_counts,
            "actual_representative_audit": audit,
            "artifacts": artifacts,
        })
        del addresses, index, occupied, current_k8, current_effective
        del current_members, actual_occupied, representatives, effective
        gc.collect()
    return rows


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    materialization, parent_evidence = validate_activation(contract, args)
    rows = materialize(contract, materialization, args)
    result = {
        "schema_version": 1,
        "family": "neuroute_r4_document_representatives_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "matrix": planner.plan(contract),
        "seeds": rows,
        "decision": {
            "materialization_audit_passed": True,
            "fine_grained_interaction_ladder_licensed": True,
            "teacher_trained_selection_used": False,
            "native_confirmation_licensed": False,
            "production_selection_licensed": False,
        },
        "authoritative_roots": parent_evidence["authoritative_roots"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    documents = normalized_rows(numpy.asarray([
        [1.0, 0.0], [0.8, 0.2], [0.0, 1.0], [-1.0, 0.0], [-0.8, 0.2]],
        dtype=numpy.float32))
    addresses = numpy.asarray([1, 1, 1, 2, 2], dtype=numpy.uint32)
    index = scale.build_index(addresses, 2)
    occupied, representatives, effective = build_actual_representatives(
        documents, addresses, index, 3, 2)
    audit = audit_representatives(
        addresses, occupied, representatives, effective, index["counts"])
    require(occupied.tolist() == [1, 2]
            and effective.tolist() == [3, 2]
            and representatives[:, 0].tolist() == [1, 2, 0]
            and audit["active_representative_count"] == 5,
            "R4 representative runner self-test differs")
    print("NeuRoute R4 document-representative runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-document-representatives.example.json")
    for name in [
            "replication-result", "replication-evidence",
            "feasible-result", "feasible-evidence",
            "width-materialization-root", "de-1m-e5-root",
            "de-1m-input-root", "materialization-root", "output"]:
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 representative paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"run-neuroute-r4-document-representatives: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
