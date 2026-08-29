#!/usr/bin/env python3
"""Materialize and audit teacher-blind R3 document-distribution summaries."""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any, Iterator

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


planner = load("neuroute_r3_document_summary_planner",
               "plan-neuroute-r3-document-summary.py")
parent = load("neuroute_r3_document_summary_parent",
              "run-neuroute-matched-representation-ladder.py")
multi = parent.multi
scale = parent.scale
task = parent.task


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return parent.sha256(path)


def canonical(value: Any) -> bytes:
    return parent.canonical(value)


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-r3-document-summary.py",
        "run-neuroute-r3-document-summary.py",
        "run-neuroute-matched-representation-ladder.py",
        "run-neuroute-address-multi-prototype.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[
        dict[str, Any], dict[str, Any]]:
    actual = {
        "matched_representation_result_sha256": sha256(
            args.matched_representation_result),
        "matched_representation_evidence_sha256": sha256(
            args.matched_representation_evidence),
    }
    require(actual == contract["activation"],
            f"R3 document-summary activation bytes differ: {actual!r}")
    result = json.loads(args.matched_representation_result.read_text(
        encoding="utf-8"))
    evidence = json.loads(args.matched_representation_evidence.read_text(
        encoding="utf-8"))
    require(result.get("family") == "neuroute_matched_representation_ladder_result"
            and result.get("decision", {}).get("r3_document_summary_licensed") is True
            and evidence.get("passed") is True
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("model_archive_sha_map_replay_passed") is True,
            "R3 document-summary matched parent differs")
    parent_contract = parent.planner.load_contract(
        THIS / "neuroute-matched-representation-ladder.example.json")
    materialization, split, _, _ = parent.validate_activation(parent_contract, args)
    return materialization, split


def normalized_block(values: numpy.ndarray) -> numpy.ndarray:
    result = numpy.asarray(values, dtype=numpy.float32).copy()
    norms = numpy.linalg.norm(result, axis=1).astype(numpy.float32)
    nonzero = norms > 0.0
    result[nonzero] /= norms[nonzero, None]
    return result


def assign_documents(documents: numpy.ndarray, addresses: numpy.ndarray,
                     occupied: numpy.ndarray, prototypes: numpy.ndarray,
                     effective: numpy.ndarray, output: Path,
                     batch_size: int = 8192) -> numpy.memmap:
    lookup = parent.address_lookup(occupied)
    assignment = numpy.lib.format.open_memmap(
        output, mode="w+", dtype=numpy.uint8, shape=(len(addresses),))
    for start in range(0, len(addresses), batch_size):
        stop = min(len(addresses), start + batch_size)
        rows = lookup[numpy.asarray(addresses[start:stop], dtype=numpy.uint32)]
        require(numpy.all(rows >= 0), "R3 document address is unoccupied")
        block = normalized_block(documents[start:stop])
        scores = numpy.full((stop - start, prototypes.shape[0]), -numpy.inf,
                            dtype=numpy.float32)
        for slot in range(prototypes.shape[0]):
            active = effective[rows] > slot
            if numpy.any(active):
                scores[active, slot] = numpy.sum(
                    block[active] * prototypes[slot, rows[active]], axis=1,
                    dtype=numpy.float32)
        assignment[start:stop] = numpy.argmax(scores, axis=1).astype(numpy.uint8)
    assignment.flush()
    return assignment


def grouped_batches(group_counts: numpy.ndarray, group_order: numpy.ndarray,
                    maximum_groups: int = 2048) -> Iterator[
                        tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]]:
    offsets = numpy.zeros(len(group_counts) + 1, dtype=numpy.int64)
    numpy.cumsum(group_counts, dtype=numpy.int64, out=offsets[1:])
    active = numpy.flatnonzero(group_counts).astype(numpy.int64)
    for start in range(0, len(active), maximum_groups):
        groups = active[start:start + maximum_groups]
        begin = int(offsets[groups[0]])
        stop = int(offsets[groups[-1] + 1])
        positions = numpy.asarray(group_order[begin:stop], dtype=numpy.int64)
        reduce_starts = numpy.asarray(offsets[groups] - begin, dtype=numpy.int64)
        yield groups, positions, reduce_starts


def residual_block(documents: numpy.ndarray, positions: numpy.ndarray,
                   group_ids: numpy.ndarray, prototypes: numpy.ndarray
                   ) -> tuple[numpy.ndarray, numpy.ndarray]:
    local_groups = numpy.asarray(group_ids[positions], dtype=numpy.int64)
    rows = local_groups // prototypes.shape[0]
    slots = local_groups % prototypes.shape[0]
    values = normalized_block(documents[positions])
    values -= prototypes[slots, rows]
    return values, local_groups


def canonicalize_directions(values: numpy.ndarray) -> numpy.ndarray:
    result = numpy.asarray(values, dtype=numpy.float32)
    norms = numpy.linalg.norm(result, axis=1).astype(numpy.float32)
    valid = norms > 1.0e-12
    result[valid] /= norms[valid, None]
    result[~valid] = 0.0
    if numpy.any(valid):
        current = result[valid]
        pivots = numpy.argmax(numpy.abs(current), axis=1)
        signs = current[numpy.arange(len(current)), pivots] < 0.0
        current[signs] *= -1.0
        result[valid] = current
    return result


def write_array(path: Path, dtype: Any, shape: tuple[int, ...]) -> numpy.memmap:
    return numpy.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=shape)


def materialize_seed(documents: numpy.ndarray, addresses: numpy.ndarray,
                     occupied: numpy.ndarray, prototypes: numpy.ndarray,
                     effective: numpy.ndarray, index: dict[str, Any], root: Path,
                     contract: dict[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    slots = prototypes.shape[0]
    dimensions = documents.shape[1]
    require(slots == 8 and dimensions == 384,
            "R3 document-summary dimensions differ")
    assignment_path = root / ".work-assignment.npy"
    assignment = assign_documents(
        documents, addresses, occupied, prototypes, effective, assignment_path)
    lookup = parent.address_lookup(occupied)
    rows = lookup[numpy.asarray(addresses, dtype=numpy.uint32)]
    assignment_values = numpy.array(assignment, dtype=numpy.int64, copy=True)
    assignment.flush()
    assignment._mmap.close()
    del assignment
    assignment_path.unlink()
    group_ids = rows.astype(numpy.int64) * slots + assignment_values
    group_count = len(occupied) * slots
    group_counts = numpy.bincount(group_ids, minlength=group_count).astype(numpy.int64)
    require(int(group_counts.sum(dtype=numpy.int64)) == len(documents),
            "R3 document assignment count differs")
    group_order = numpy.argsort(group_ids, kind="stable").astype(numpy.int32)
    shape = (len(occupied), slots, dimensions)
    count_path = root / "local-document-count.npy"
    count_output = write_array(count_path, numpy.int32, (len(occupied), slots))
    count_output[:] = group_counts.reshape(len(occupied), slots).astype(numpy.int32)
    count_output.flush()

    mean_path = root / "mean-residual.npy"
    mean = write_array(mean_path, numpy.float32, shape)
    mean[:] = 0.0
    zero_residual_documents = 0
    mean_flat = mean.reshape(group_count, dimensions)
    for groups, positions, starts in grouped_batches(group_counts, group_order):
        residuals, _ = residual_block(
            documents, positions, group_ids, prototypes)
        zero_residual_documents += int(numpy.count_nonzero(
            numpy.sum(residuals * residuals, axis=1, dtype=numpy.float32)
            <= 1.0e-12))
        sums = numpy.add.reduceat(residuals, starts, axis=0, dtype=numpy.float32)
        mean_flat[groups] = sums / group_counts[groups, None].astype(numpy.float32)
    mean.flush()

    variance_path = root / "diagonal-residual-variance.npy"
    variance = write_array(variance_path, numpy.float32, shape)
    variance[:] = 0.0
    variance_flat = variance.reshape(group_count, dimensions)
    for groups, positions, starts in grouped_batches(group_counts, group_order):
        residuals, local_groups = residual_block(
            documents, positions, group_ids, prototypes)
        centered = residuals - mean_flat[local_groups]
        sums = numpy.add.reduceat(centered * centered, starts, axis=0,
                                  dtype=numpy.float32)
        variance_flat[groups] = sums / group_counts[groups, None].astype(
            numpy.float32)
    numpy.maximum(variance, 0.0, out=variance)
    variance.flush()

    energy_path = root / "total-residual-energy.npy"
    energy = write_array(energy_path, numpy.float32, (len(occupied), slots))
    energy[:] = variance.sum(axis=2, dtype=numpy.float32)
    energy.flush()
    minimum = int(contract["summaries"]["top_direction"][
        "minimum_local_documents"])
    eligible = ((group_counts >= minimum)
                & (energy.reshape(group_count) > 1.0e-12))

    direction_paths = [root / ".work-direction-a.npy",
                       root / ".work-direction-b.npy"]
    direction = write_array(direction_paths[0], numpy.float32, shape)
    direction[:] = 0.0
    eligible_groups = numpy.flatnonzero(eligible)
    if len(eligible_groups):
        axes = numpy.argmax(variance_flat[eligible_groups], axis=1)
        direction.reshape(group_count, dimensions)[eligible_groups, axes] = 1.0
    direction.flush()
    current_path = direction_paths[0]
    direction._mmap.close()
    del direction
    for iteration in range(int(contract["summaries"]["top_direction"][
            "iterations"])):
        current = numpy.load(current_path, mmap_mode="r")
        next_path = direction_paths[(iteration + 1) % 2]
        next_direction = write_array(next_path, numpy.float32, shape)
        next_direction[:] = 0.0
        current_flat = current.reshape(group_count, dimensions)
        next_flat = next_direction.reshape(group_count, dimensions)
        for groups, positions, starts in grouped_batches(group_counts, group_order):
            retained = eligible[groups]
            if not numpy.any(retained):
                continue
            residuals, local_groups = residual_block(
                documents, positions, group_ids, prototypes)
            centered = residuals - mean_flat[local_groups]
            products = numpy.sum(centered * current_flat[local_groups], axis=1,
                                 dtype=numpy.float32)
            updates = numpy.add.reduceat(centered * products[:, None], starts,
                                         axis=0, dtype=numpy.float32)
            selected_groups = groups[retained]
            next_flat[selected_groups] = canonicalize_directions(updates[retained])
        next_direction.flush()
        del current_flat, next_flat
        current._mmap.close()
        next_direction._mmap.close()
        del current, next_direction
        gc.collect()
        if current_path != next_path and current_path.exists():
            current_path.unlink()
        current_path = next_path

    direction_path = root / "top-centered-residual-direction.npy"
    current_path.replace(direction_path)
    direction = numpy.load(direction_path, mmap_mode="r")
    direction_flat = direction.reshape(group_count, dimensions)
    eigenvalue_path = root / "top-residual-eigenvalue.npy"
    eigenvalue = write_array(eigenvalue_path, numpy.float32,
                             (len(occupied), slots))
    eigenvalue[:] = 0.0
    eigenvalue_flat = eigenvalue.reshape(group_count)
    for groups, positions, starts in grouped_batches(group_counts, group_order):
        retained = eligible[groups]
        if not numpy.any(retained):
            continue
        residuals, local_groups = residual_block(
            documents, positions, group_ids, prototypes)
        centered = residuals - mean_flat[local_groups]
        products = numpy.sum(centered * direction_flat[local_groups], axis=1,
                             dtype=numpy.float32)
        sums = numpy.add.reduceat(products * products, starts,
                                  dtype=numpy.float32)
        eigenvalue_flat[groups[retained]] = (
            sums[retained] / group_counts[groups[retained]].astype(numpy.float32))
    eigenvalue.flush()

    require(numpy.all(numpy.isfinite(mean))
            and numpy.all(numpy.isfinite(variance))
            and numpy.all(numpy.isfinite(direction))
            and numpy.all(numpy.isfinite(eigenvalue))
            and numpy.all(numpy.isfinite(energy)),
            "R3 document summary is not finite")
    insufficient = group_counts < minimum
    require(numpy.count_nonzero(direction_flat[insufficient]) == 0
            and numpy.count_nonzero(eigenvalue_flat[insufficient]) == 0,
            "R3 insufficient-group fallback differs")

    address_counts = numpy.asarray(index["counts"], dtype=numpy.int64)[occupied]
    local_counts = group_counts.reshape(len(occupied), slots)
    local_energy = energy.reshape(len(occupied), slots)
    buckets = []
    definitions = (
        ("le_8", address_counts <= 8),
        ("9_16", (address_counts >= 9) & (address_counts <= 16)),
        ("17_32", (address_counts >= 17) & (address_counts <= 32)),
        ("gt_32", address_counts > 32),
    )
    for name, mask in definitions:
        values = local_counts[mask]
        energies = local_energy[mask]
        buckets.append({
            "occupancy_bucket": name,
            "address_count": int(numpy.count_nonzero(mask)),
            "document_count": int(address_counts[mask].sum(dtype=numpy.int64)),
            "effective_prototype_count": int(effective[mask].sum(dtype=numpy.int64)),
            "nonempty_local_group_count": int(numpy.count_nonzero(values)),
            "singleton_local_group_count": int(numpy.count_nonzero(values == 1)),
            "insufficient_local_group_count": int(numpy.count_nonzero(
                (values > 0) & (values < minimum))),
            "zero_energy_local_group_count": int(numpy.count_nonzero(
                (values > 0) & (energies <= 1.0e-12))),
        })

    del assignment_values, group_order, mean, variance, direction, eigenvalue, energy
    gc.collect()
    artifacts = []
    for role, path in (
            ("local_document_count", count_path),
            ("mean_residual", mean_path),
            ("diagonal_residual_variance", variance_path),
            ("top_centered_residual_direction", direction_path),
            ("top_residual_eigenvalue", eigenvalue_path),
            ("total_residual_energy", energy_path)):
        array = numpy.load(path, mmap_mode="r")
        artifacts.append({
            "role": role,
            "path": path.name,
            "dtype": str(array.dtype),
            "shape": list(array.shape),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
        del array
    return {
        "occupied_address_count": len(occupied),
        "document_count": len(documents),
        "assigned_document_count": int(group_counts.sum(dtype=numpy.int64)),
        "nonempty_local_group_count": int(numpy.count_nonzero(group_counts)),
        "insufficient_local_group_count": int(numpy.count_nonzero(
            (group_counts > 0) & (group_counts < minimum))),
        "zero_residual_document_count": zero_residual_documents,
        "centroid_slot_is_not_a_document_representative": True,
        "occupancy_buckets": buckets,
        "artifacts": artifacts,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    materialization, _ = validate_activation(contract, args)
    scale_config = next(row for row in parent.prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root, args.de_1m_input_root)
    require(len(data["documents"]) == contract["route"]["documents"],
            "R3 document-summary corpus differs")
    manifest_dataset = next(row for row in materialization["datasets"]
                            if row["id"] == "de-1m")
    rows = []
    for seed in contract["route"]["seeds"]:
        route = task.route_entry(manifest_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, members = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        seed_root = args.materialization_root / f"seed-{seed}"
        audit = materialize_seed(
            data["documents"], addresses, occupied, prototypes, effective,
            index, seed_root, contract)
        rows.append({
            "seed": seed,
            "document_addresses_sha256": route["document_addresses"]["sha256"],
            "occupied_addresses_sha256": parent.array_sha256(occupied),
            "effective_prototypes_sha256": parent.array_sha256(effective),
            "prototype_state_sha256": parent.array_sha256(prototypes),
            **audit,
        })
        del addresses, index, occupied, prototypes, effective, members
        gc.collect()
    result = {
        "schema_version": 1,
        "family": "neuroute_r3_document_summary_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "source_files_sha256": source_hashes(),
        "execution": {
            "numpy_version": numpy.__version__,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "assignment_batch_documents": 8192,
            "aggregation_batch_groups": 2048,
        },
        "matrix": planner.plan(contract),
        "seeds": rows,
        "decision": {
            "every_document_assigned_once": all(
                row["assigned_document_count"] == row["document_count"]
                for row in rows),
            "finite_summary_audit_passed": True,
            "zero_fallback_semantics_passed": True,
            "summary_is_query_independent": True,
            "summary_is_teacher_blind": True,
            "matched_r3_ladder_licensed": True,
            "stateful_policy_licensed": False,
            "native_confirmation_licensed": False,
            "production_selection_licensed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(
        THIS / "neuroute-r3-document-summary.example.json")
    documents = numpy.zeros((6, 384), dtype=numpy.float32)
    documents[:, 0] = 1.0
    documents[2, :4] = numpy.asarray([0.8, 0.6, 0.0, 0.0])
    documents[4:, 1] = 1.0
    addresses = numpy.asarray([1, 1, 1, 2, 2, 2], dtype=numpy.uint32)
    index = scale.build_index(addresses, 16)
    occupied, prototypes, effective, _ = multi.build_nested_prototypes(
        documents, addresses, index, 8)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="neuroute-r3-summary-self-test-") as value:
        root = Path(value)
        assignment = assign_documents(
            documents, addresses, occupied, prototypes, effective,
            root / "assignment.npy", batch_size=2)
        require(assignment.shape == (6,) and numpy.all(assignment < 3),
                "R3 document-summary assignment self-test differs")
        del assignment
        gc.collect()
        directions = canonicalize_directions(numpy.asarray([
            [-2.0, 0.0], [0.0, -3.0], [0.0, 0.0]], dtype=numpy.float32))
        require(numpy.array_equal(directions, numpy.asarray([
            [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]], dtype=numpy.float32)),
                "R3 document-summary direction self-test differs")
        audit = materialize_seed(
            documents, addresses, occupied, prototypes, effective, index,
            root / "summary", contract)
        require(audit["assigned_document_count"] == 6
                and len(audit["artifacts"]) == 6
                and audit["centroid_slot_is_not_a_document_representative"] is True,
                "R3 document-summary materialization self-test differs")
    require(planner.plan(contract)["document_assignments"] == 3000000,
            "R3 document-summary matrix self-test differs")
    print("NeuRoute R3 document-summary runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r3-document-summary.example.json")
    parser.add_argument("--matched-representation-result", type=Path)
    parser.add_argument("--matched-representation-evidence", type=Path)
    parser.add_argument("--ambiguity-result", type=Path)
    parser.add_argument("--ambiguity-evidence", type=Path)
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--prototype-gain-density-result", type=Path)
    parser.add_argument("--prototype-gain-density-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R3 document-summary paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-r3-document-summary: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
