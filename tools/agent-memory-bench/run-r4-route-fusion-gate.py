#!/usr/bin/env python3
"""Evaluate budgeted fusion of shallow R4 seeds and a deep R4 route."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_external(record: dict[str, Any]) -> dict[str, Any]:
    path = Path(record["path"])
    actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    expected = {"bytes": int(record["bytes"]), "sha256": record["sha256"]}
    if actual != expected:
        raise ValueError(f"frozen input mismatch for {path}: {actual} != {expected}")
    return {"path": str(path), **actual}


def aggregate(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    return {"min": float(arr.min()), "mean": float(arr.mean()),
            "p05": float(np.percentile(arr, 5)), "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)), "max": float(arr.max())}


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--r4-manifest", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=8192)
    parser.add_argument("--budgets", default="5000,10000,20000,50000,100000")
    args = parser.parse_args()
    frozen = json.loads(args.thq_manifest.read_text(encoding="utf-8"))
    manifest = json.loads(args.r4_manifest.read_text(encoding="utf-8"))
    n = int(frozen["documents"])
    queries = np.memmap(Path(frozen["references"]["queries"]["path"]), mode="r", dtype="<f4", shape=(152, 384))
    teachers = np.memmap(Path(frozen["references"]["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(152, 10))
    budgets = [int(x) for x in args.budgets.split(",") if x]
    input_artifacts = {
        role: validate_external(frozen["references"][role])
        for role in ("document_vectors", "queries", "teacher_ids")
    }
    comparator = load("r4_fusion_comparator", "run-r4-frozen-comparator.py")
    coverage = load("r4_fusion_coverage", "run-neuroute-r4-coverage-saturation.py")
    seed_data: dict[int, dict[str, Any]] = {}
    for seed_record in manifest["seeds"]:
        seed = int(seed_record["seed"])
        root = args.r4_root / "materialized" / f"seed-{seed}"
        mappings = {x["role"]: x for x in seed_record["mappings"]}
        counts = np.fromfile(root / mappings["address_counts"]["file"], dtype="<u4")
        offsets = np.fromfile(root / mappings["address_offsets"]["file"], dtype="<u4")
        physical = np.fromfile(root / mappings["physical_to_document"]["file"], dtype="<i4")
        postings = [physical[int(offset):int(offset + count)]
                    for offset, count in zip(offsets, counts)]
        shortlist = np.fromfile(root / mappings["shortlist_rows"]["file"], dtype="<u4").reshape(152, 1024)
        fp32 = next(x for x in seed_record["layouts"] if x["role"] == "address_major_fp32")
        validated = [comparator.file_record(root, record)
                     for record in [*seed_record["mappings"], fp32, *seed_record["model"]]]
        records = np.memmap(root / fp32["file"], mode="r", dtype="<f4", shape=(n, 384))
        doc_to_physical = np.fromfile(root / mappings["document_to_physical"]["file"], dtype="<u4")
        ordered, _ = comparator.model_order(root, seed_record, np.asarray(queries), shortlist,
                                            records, doc_to_physical)
        if any(np.unique(row).size != row.size for row in ordered):
            raise ValueError(f"duplicate model-ranked shortlist address for seed {seed}")
        seed_data[seed] = {"order": ordered, "postings": postings, "counts": counts,
                           "validated_artifacts": validated}

    # Reconstruct the deep route using actual address IDs, then map back to
    # the persisted row IDs used by the comparator's posting arrays.
    deep_seed = next(x for x in manifest["seeds"] if int(x["seed"]) == 2026082701)
    deep_root = args.r4_root / "materialized" / "seed-2026082701"
    dm = {x["role"]: x for x in deep_seed["mappings"]}
    occupied = np.fromfile(deep_root / dm["occupied_addresses"]["file"], dtype="<u4")
    counts = np.fromfile(deep_root / dm["address_counts"]["file"], dtype="<u4")
    physical = np.fromfile(deep_root / dm["physical_to_document"]["file"], dtype="<i4")
    offsets = np.fromfile(deep_root / dm["address_offsets"]["file"], dtype="<u4")
    address_by_doc = np.empty(n, dtype=np.uint32)
    for row, (offset, count) in enumerate(zip(offsets, counts)):
        address_by_doc[physical[int(offset):int(offset + count)]] = occupied[row]
    index = coverage.scale.build_index(address_by_doc, 16)
    documents = np.memmap(Path(frozen["references"]["document_vectors"]["path"]), mode="r", dtype="<f4", shape=(n, 384))
    occupied_check, prototypes, effective, _ = coverage.multi.build_nested_prototypes(
        documents, address_by_doc, index, 8)
    if not np.array_equal(np.sort(occupied), np.sort(occupied_check)):
        raise ValueError("deep occupied address set mismatch")
    if not np.array_equal(occupied, occupied_check):
        row_by_address = {int(address): row for row, address in enumerate(occupied_check)}
        reorder = np.asarray([row_by_address[int(address)] for address in occupied], dtype=np.int64)
        prototypes, effective = prototypes[reorder], effective[reorder]
    contract = coverage.base.planner.load_contract(THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    deep_shortlists, _ = coverage.base.prepare_query_features(
        np.asarray(queries), occupied, prototypes, effective, index["counts"], n,
        args.depth, contract["training"]["feature_query_batch_size"])
    lookup = coverage.fine.address_lookup(occupied)
    deep_rows = lookup[np.asarray(deep_shortlists, dtype=np.uint32)]
    old_rows = np.fromfile(deep_root / dm["shortlist_rows"]["file"], dtype="<u4").reshape(152, 1024)
    model_prefix, _ = comparator.model_order(
        deep_root, deep_seed, np.asarray(queries), old_rows,
        np.memmap(deep_root / next(x for x in deep_seed["layouts"] if x["role"] == "address_major_fp32")["file"], mode="r", dtype="<f4", shape=(n, 384)),
        np.fromfile(deep_root / dm["document_to_physical"]["file"], dtype="<u4"))
    deep_orders = []
    for qi in range(152):
        prefix = model_prefix[qi].tolist()
        seen = set(prefix)
        tail = [int(row) for row in deep_rows[qi] if int(row) not in seen]
        order = np.asarray(prefix + tail, dtype=np.uint32)
        if order.size != args.depth or np.unique(order).size != order.size:
            raise ValueError("deep route does not contain the requested unique depth")
        deep_orders.append(order)
    deep_postings = seed_data[2026082701]["postings"]
    deep_counts = seed_data[2026082701]["counts"]
    routes: dict[str, dict[str, Any]] = {f"seed-{seed}": seed_data[seed] for seed in sorted(seed_data)}
    routes["deep-8192"] = {"order": np.asarray(deep_orders), "postings": deep_postings, "counts": deep_counts}

    raw_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    route_sets = [("seed-2026082701",), ("seed-2026082702",), ("seed-2026082703",),
                  ("seed-2026082701", "seed-2026082702", "seed-2026082703"),
                  ("seed-2026082702", "seed-2026082703", "deep-8192"),
                  ("seed-2026082701", "seed-2026082702", "seed-2026082703", "deep-8192")]
    for route_set in route_sets:
        for qi in range(152):
            streams = [routes[name]["order"][qi] for name in route_set]
            postings = [routes[name]["postings"] for name in route_set]
            snapshots = comparator_union.merge_snapshot(streams, postings,
                [routes[name]["counts"] for name in route_set], np.asarray(teachers[qi]), budgets, n)
            for row in snapshots:
                raw_rows.append({"routes": list(route_set), "query": qi, **row})
        for budget in budgets:
            selected = [row for row in raw_rows if row["routes"] == list(route_set)
                        and row["requested_candidate_budget"] == budget]
            summaries.append({"routes": list(route_set), "requested_candidate_budget": budget,
                              "query_count": 152,
                              **{metric: aggregate([float(x[metric]) for x in selected])
                                 for metric in ("actual_unique_candidates", "budget_overshoot",
                                                "posting_entries_touched", "postings_touched",
                                                "overlap_entries", "duplication_ratio", "teacher_recall")}})
    raw_bytes = (json.dumps({"schema_version": 1, "rows": raw_rows},
                            separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_bytes(raw_bytes)
    output = {"schema_version": 1, "family": "semantic_r4_route_fusion_gate_v1",
              "execution_status": "EXECUTED", "production_activation": False,
              "fixture_manifest_sha256": sha256(args.thq_manifest),
              "r4_manifest_sha256": sha256(args.r4_manifest), "runner_sha256": sha256(Path(__file__)),
              "documents": n, "queries": 152, "depth": args.depth, "budgets": budgets,
              "summaries": summaries,
              "input_artifacts": {"frozen": input_artifacts,
                                  "r4": {str(seed): seed_data[seed]["validated_artifacts"] for seed in seed_data}},
              "raw_output": {"path": str(args.raw_output), "sha256": hashlib.sha256(raw_bytes).hexdigest(), "rows": len(raw_rows)},
              "protocol": {"merge": "deterministic equal-consumed-entry round robin; ties by route order",
                           "candidate_budget_definition": "unique document IDs",
                           "teacher_ids_used_for_index": False, "physical_page_bytes": "not measured",
                           "deep_tail": "regenerated coarse after frozen 1024 model-ranked prefix",
                           "production_activation": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    # Importing here avoids a circular import when this file is loaded by tools.
    comparator_union = load("r4_union_helpers", "run-r4-seed-union-gate.py")
    main()
