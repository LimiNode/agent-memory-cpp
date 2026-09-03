#!/usr/bin/env python3
"""Run frozen 12-bit A@256 over nested German 25k/100k/1M roots."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_frozen_scale_planner", "plan-neuroute-frozen-scale-transfer.py")
v4 = load("neuroute_frozen_scale_v4", "run-neuroute-relevance-aware-v4.py")
native = load("neuroute_frozen_scale_native", "materialize-neuroute-native-mdbx-cost.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-frozen-scale-transfer.py", "run-neuroute-frozen-scale-transfer.py",
        "run-neuroute-relevance-aware-v4.py", "run-neuroute-training-sanity.py",
        "run-direct-learned-semantic-address.py", "materialize-neuroute-native-mdbx-cost.py",
    )
    return {name: sha256(THIS / name) for name in names}


def read_ids(path: Path) -> numpy.ndarray:
    with path.open("r", encoding="utf-8") as stream:
        return numpy.asarray([json.loads(line)["id"] for line in stream], dtype=object)


def read_qrels(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            query, _, document, grade = line.split()
            result.setdefault(query, {})[document] = int(grade)
    return result


def payload(root: Path, manifest: dict[str, Any], prefix: str, dtype: str,
            shape: tuple[int, ...]) -> numpy.memmap:
    path = root / manifest[f"{prefix}_file"]
    require(path.is_file() and sha256(path) == manifest[f"{prefix}_sha256"],
            f"frozen scale payload differs: {prefix}")
    return numpy.memmap(path, mode="r", dtype=dtype, shape=shape)


def load_scale(scale: dict[str, Any], e5_root: Path, input_root: Path) -> dict[str, Any]:
    e5_manifest_path = e5_root / "manifest.json"
    input_manifest_path = input_root / "manifest.json"
    require(sha256(e5_manifest_path) == scale["e5_manifest_sha256"]
            and sha256(input_manifest_path) == scale["input_manifest_sha256"],
            f"frozen scale manifests differ: {scale['id']}")
    manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    documents = scale["documents"]
    queries = manifest["query_count"]
    require(manifest.get("document_count") == documents and manifest.get("embedding_dimension") == 384
            and manifest.get("code_bits") == 256, f"frozen scale input shape differs: {scale['id']}")
    document_ids = read_ids(e5_root / "evaluation-document-ids.jsonl")
    query_ids = read_ids(e5_root / "evaluation-query-ids.jsonl")
    require(document_ids.size == documents and query_ids.size == queries,
            f"frozen scale ID count differs: {scale['id']}")
    return {
        "document_ids": document_ids, "query_ids": query_ids,
        "qrels": read_qrels(e5_root / "evaluation-qrels.tsv"),
        "documents": payload(input_root, manifest, "document_vectors", "<f4", (documents, 384)),
        "queries": payload(input_root, manifest, "query_vectors", "<f4", (queries, 384)),
        "document_codes": payload(input_root, manifest, "document_codes", "u1", (documents, 32)),
        "query_codes": payload(input_root, manifest, "query_codes", "u1", (queries, 32)),
        "query_projection": payload(input_root, manifest, "query_itq_projections", "<f4", (queries, 256)),
        "adc_centroids": payload(input_root, manifest, "binary_adc_centroids", "<f4", (256, 2)),
        "e5_manifest_sha256": scale["e5_manifest_sha256"],
        "input_manifest_sha256": scale["input_manifest_sha256"],
        "document_vectors_path": str((input_root / manifest["document_vectors_file"]).resolve()),
        "document_vectors_sha256": manifest["document_vectors_sha256"],
        "query_vectors_sha256": manifest["query_vectors_sha256"],
        "query_codes_sha256": manifest["query_codes_sha256"],
        "query_projection_sha256": manifest["query_itq_projections_sha256"],
    }


def infer_batched(vectors: numpy.ndarray, arrays: dict[str, numpy.ndarray], batch: int = 65536) -> numpy.ndarray:
    result = numpy.empty((len(vectors), arrays["weight3"].shape[0]), dtype=numpy.float32)
    for start in range(0, len(vectors), batch):
        stop = min(len(vectors), start + batch)
        result[start:stop] = v4.base.infer(numpy.asarray(vectors[start:stop]), arrays, False)
    return result


def hash_ids(values: numpy.ndarray) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "little"))
        digest.update(encoded)
    return digest.hexdigest()


def hash_id_set(values: set[str]) -> str:
    return hash_ids(numpy.asarray(sorted(values), dtype=object))


def select_smallest(values: numpy.ndarray, document_ids: numpy.ndarray, limit: int) -> numpy.ndarray:
    if values.size <= limit:
        pool = numpy.arange(values.size, dtype=numpy.int32)
    else:
        boundary = numpy.partition(values, limit - 1)[limit - 1]
        pool = numpy.flatnonzero(values <= boundary)
    order = numpy.lexsort((document_ids[pool], values[pool]))
    return pool[order[:limit]].astype(numpy.int32)


def select_largest(values: numpy.ndarray, document_ids: numpy.ndarray, limit: int) -> numpy.ndarray:
    if values.size <= limit:
        pool = numpy.arange(values.size, dtype=numpy.int32)
    else:
        boundary = numpy.partition(values, values.size - limit)[values.size - limit]
        pool = numpy.flatnonzero(values >= boundary)
    order = numpy.lexsort((document_ids[pool], -values[pool]))
    return pool[order[:limit]].astype(numpy.int32)


def exact_oracle(data: dict[str, Any], positions: list[int], k: int) -> tuple[dict[int, numpy.ndarray], dict[int, float]]:
    oracle: dict[int, numpy.ndarray] = {}
    ndcgs: dict[int, float] = {}
    for position in positions:
        scores = numpy.asarray(data["documents"] @ data["queries"][position], dtype=numpy.float32)
        selected = select_largest(scores, data["document_ids"], k)
        oracle[position] = selected
        ndcgs[position] = v4.base.alignment.german.quality.dcg_at_10(
            data["document_ids"][selected], data["qrels"][data["query_ids"][position]])
    return oracle, ndcgs


def addresses_from_logits(logits: numpy.ndarray) -> numpy.ndarray:
    powers = (numpy.uint16(1) << numpy.arange(logits.shape[1], dtype=numpy.uint16))[None, :]
    return ((logits >= 0.0).astype(numpy.uint16) * powers).sum(axis=1, dtype=numpy.uint16)


def build_index(addresses: numpy.ndarray, bits: int) -> dict[str, Any]:
    counts = numpy.bincount(addresses, minlength=1 << bits).astype(numpy.int64)
    offsets = numpy.empty(counts.size + 1, dtype=numpy.int64)
    offsets[0] = 0
    numpy.cumsum(counts, out=offsets[1:])
    order = numpy.argsort(addresses, kind="stable").astype(numpy.int32)
    occupied = counts[counts > 0]
    return {"counts": counts, "offsets": offsets, "order": order,
            "occupied": occupied, "addresses": addresses}


def occupancy(index: dict[str, Any]) -> dict[str, Any]:
    values = index["occupied"].astype(numpy.float64)
    sorted_values = numpy.sort(values)
    count = len(sorted_values)
    gini = (2.0 * float(numpy.dot(numpy.arange(1, count + 1), sorted_values))
            / (count * float(sorted_values.sum())) - (count + 1.0) / count)
    return {"occupied_address_count": count, "mean_posting_length": float(values.mean()),
            "p95_posting_length": float(numpy.quantile(values, 0.95)),
            "max_posting_length": int(values.max()), "posting_gini": gini}


def candidate_union(requested: list[int], index: dict[str, Any], limit: int) -> tuple[numpy.ndarray, list[int], int]:
    selected: list[numpy.ndarray] = []
    accepted: list[int] = []
    count = 0
    requested_entries = 0
    for address in requested:
        start, stop = int(index["offsets"][address]), int(index["offsets"][address + 1])
        size = stop - start
        requested_entries += size
        if count + size <= limit:
            accepted.append(address)
            if size:
                selected.append(index["order"][start:stop])
                count += size
    candidates = numpy.concatenate(selected) if selected else numpy.empty(0, dtype=numpy.int32)
    candidates.sort()
    return candidates, accepted, requested_entries


def update_sequence(digest: Any, query: int, values: numpy.ndarray) -> None:
    digest.update(int(query).to_bytes(4, "little"))
    digest.update(int(values.size).to_bytes(4, "little"))
    digest.update(numpy.asarray(values, dtype="<u4").tobytes())


def sequence_sha256(values: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<u4").tobytes()).hexdigest()


def ndcg(data: dict[str, Any], position: int, ranked: numpy.ndarray) -> float:
    return v4.base.alignment.german.quality.dcg_at_10(
        data["document_ids"][ranked], data["qrels"][data["query_ids"][position]])


def evaluate_route(data: dict[str, Any], positions: list[int], query_logits: numpy.ndarray,
                   index: dict[str, Any], oracle: dict[int, numpy.ndarray], full_ndcg: dict[int, float],
                   contract: dict[str, Any]) -> dict[str, Any]:
    route, cascade = contract["route"], contract["cascade"]
    digests = {name: hashlib.sha256() for name in ("candidate", "hamming", "adc", "exact")}
    rows = []
    for local_query, position in enumerate(positions):
        requested = v4.base.alignment.german.diagnostic.addresses(
            query_logits[position], route["bits"], route["probes"])
        candidates, accepted, requested_entries = candidate_union(
            requested, index, int(math.floor(len(data["document_ids"]) * route["candidate_mass_target"])))
        xor = numpy.bitwise_xor(data["document_codes"][candidates], data["query_codes"][position])
        distances = POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
        local_hamming = select_smallest(distances, data["document_ids"][candidates], cascade["hamming_limit"])
        hamming = candidates[local_hamming]
        bits = numpy.unpackbits(data["document_codes"][hamming], axis=1, bitorder="little")
        table = (data["query_projection"][position, :, None] - data["adc_centroids"]) ** 2
        adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
        local_adc = select_smallest(adc_distances, data["document_ids"][hamming], cascade["adc_limit"])
        adc = hamming[local_adc]
        exact_scores = numpy.asarray(
            (data["documents"][adc] * data["queries"][position]).sum(axis=1), dtype=numpy.float32)
        local_exact = select_largest(exact_scores, data["document_ids"][adc], cascade["result_k"])
        exact = adc[local_exact]
        adc_top = adc[:cascade["result_k"]]
        for name, values in (("candidate", candidates), ("hamming", hamming), ("adc", adc), ("exact", exact)):
            update_sequence(digests[name], local_query, values)
        target = oracle[position]
        rows.append({
            "query_id": data["query_ids"][position], "source_query_position": int(position),
            "requested_address_count": len(requested), "accepted_probe_count": len(accepted),
            "requested_address_sha256": sequence_sha256(numpy.asarray(requested, dtype=numpy.uint32)),
            "posting_entries_requested": requested_entries, "candidate_count": int(candidates.size),
            "candidate_sha256": sequence_sha256(candidates), "hamming_sha256": sequence_sha256(hamming),
            "adc_sha256": sequence_sha256(adc), "exact_sha256": sequence_sha256(exact),
            "raw_e5_oracle_survival": float(numpy.isin(target, candidates).sum()) / cascade["oracle_k"],
            "hamming_e5_oracle_survival": float(numpy.isin(target, hamming).sum()) / cascade["oracle_k"],
            "adc64_e5_oracle_survival": float(numpy.isin(target, adc).sum()) / cascade["oracle_k"],
            "adc_only_ndcg_at_10": ndcg(data, position, adc_top),
            "exact64_ndcg_at_10": ndcg(data, position, exact),
            "full_exact_e5_ndcg_at_10": full_ndcg[position],
        })
    fields = ("accepted_probe_count", "posting_entries_requested", "candidate_count",
              "raw_e5_oracle_survival", "hamming_e5_oracle_survival", "adc64_e5_oracle_survival",
              "adc_only_ndcg_at_10", "exact64_ndcg_at_10", "full_exact_e5_ndcg_at_10")
    metrics = {name: float(numpy.mean([row[name] for row in rows], dtype=numpy.float64)) for name in fields}
    metrics["candidate_fraction"] = metrics["candidate_count"] / len(data["document_ids"])
    metrics["exact64_ndcg_retention_vs_full_e5"] = (metrics["exact64_ndcg_at_10"]
                                                     / metrics["full_exact_e5_ndcg_at_10"]
                                                     if metrics["full_exact_e5_ndcg_at_10"] else 1.0)
    return {"query_count": len(rows), "metrics": metrics, "queries": rows,
            **{f"{name}_sequence_sha256": digest.hexdigest() for name, digest in digests.items()}}


def write_array(path: Path, values: numpy.ndarray, dtype: str) -> dict[str, Any]:
    packed = numpy.ascontiguousarray(values, dtype=dtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    packed.tofile(path)
    return {"file": path.name, "sha256": sha256(path), "shape": list(packed.shape), "dtype": dtype}


def external_array(path: str, sha: str, shape: list[int], dtype: str) -> dict[str, Any]:
    return {"file": path, "sha256": sha, "shape": shape, "dtype": dtype, "external_frozen_root": True}


def route_manifest(dataset_root: Path, route_id: str, seed: int, policy: str,
                   addresses: numpy.ndarray, query_logits: numpy.ndarray,
                   index: dict[str, Any], evaluated: dict[str, Any]) -> dict[str, Any]:
    root = dataset_root / route_id
    return {
        "id": route_id, "kind": "learned", "seed": seed, "threshold_policy": policy,
        "bits": 12, "logit_dimensions": 12, "document_replication": 1,
        "document_addresses": write_array(root / "document-addresses.u16le", addresses, "<u2"),
        "query_logits": write_array(root / "query-logits.f32le", query_logits, "<f4"),
        "occupied_address_count": int(len(index["occupied"])), "posting_entry_count": int(addresses.size),
        "expected": [{
            "probes": 256, "candidate_sequence_sha256": evaluated["candidate_sequence_sha256"],
            "hamming_sequence_sha256": evaluated["hamming_sequence_sha256"],
            "adc_sequence_sha256": evaluated["adc_sequence_sha256"],
            "exact_sequence_sha256": evaluated["exact_sequence_sha256"],
            "queries": [{
                "query": query,
                "requested_address_count": row["requested_address_count"],
                "requested_address_sha256": row["requested_address_sha256"],
                "accepted_probe_count": row["accepted_probe_count"],
                "posting_entries_requested": row["posting_entries_requested"],
                "candidate_count": row["candidate_count"], "candidate_sha256": row["candidate_sha256"],
                "hamming_count": 768, "hamming_sha256": row["hamming_sha256"],
                "adc_count": 64, "adc_sha256": row["adc_sha256"],
                "exact_sha256": row["exact_sha256"],
            } for query, row in enumerate(evaluated["queries"])],
        }],
    }


def scale_run(scale: dict[str, Any], roots: dict[str, Path], positions_ids: list[str],
              models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]],
              frozen_thresholds: dict[int, numpy.ndarray], contract: dict[str, Any],
              materialization_root: Path, previous_document_ids: set[str] | None,
              anchor_document_ids: set[str] | None) -> tuple[
                  dict[str, Any], dict[str, Any], dict[int, numpy.ndarray], set[str], set[str]]:
    data = load_scale(scale, roots["e5"], roots["input"])
    document_ids = {str(value) for value in data["document_ids"]}
    require(len(document_ids) == scale["documents"],
            f"frozen scale duplicate document IDs: {scale['id']}")
    if previous_document_ids is not None:
        require(previous_document_ids.issubset(document_ids),
                f"frozen scale document sets are not nested: {scale['id']}")
    if anchor_document_ids is None:
        anchor_document_ids = document_ids.copy()
    require(anchor_document_ids.issubset(document_ids),
            f"frozen 25k anchor is absent: {scale['id']}")
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    require(all(value in by_id for value in positions_ids), f"frozen scale query IDs differ: {scale['id']}")
    positions = [by_id[value] for value in positions_ids]
    oracle, full_ndcg = exact_oracle(data, positions, contract["cascade"]["oracle_k"])
    dataset_root = materialization_root / scale["id"]
    ranks = native.lexicographic_ranks(data["document_ids"])
    common = {
        "document_codes": write_array(dataset_root / "document-codes.u8", data["document_codes"], "u1"),
        "query_codes": write_array(dataset_root / "query-codes.u8", data["query_codes"][positions], "u1"),
        "query_projection": write_array(dataset_root / "query-projection.f32le",
                                          data["query_projection"][positions], "<f4"),
        "adc_centroids": write_array(dataset_root / "adc-centroids.f32le", data["adc_centroids"], "<f4"),
        "document_id_rank": write_array(dataset_root / "document-id-rank.u32le", ranks, "<u4"),
        "document_vectors": external_array(data["document_vectors_path"], data["document_vectors_sha256"],
                                             [scale["documents"], 384], "<f4"),
        "query_vectors": write_array(dataset_root / "query-vectors.f32le", data["queries"][positions], "<f4"),
    }
    rows, routes = [], []
    for model, arrays in models:
        document_raw = infer_batched(data["documents"], arrays)
        per_scale = numpy.median(document_raw, axis=0).astype(numpy.float32)
        if scale["id"] == "de-25k":
            frozen_thresholds[model["seed"]] = per_scale.copy()
        require(model["seed"] in frozen_thresholds, "frozen 25k threshold is unavailable")
        query_raw = infer_batched(data["queries"], arrays)
        for policy in contract["route"]["threshold_policies"]:
            threshold = per_scale if policy == "per_scale_document_median" else frozen_thresholds[model["seed"]]
            document_logits = document_raw - threshold
            query_logits = query_raw - threshold
            addresses = addresses_from_logits(document_logits)
            index = build_index(addresses, contract["route"]["bits"])
            evaluated = evaluate_route(data, positions, query_logits, index, oracle, full_ndcg, contract)
            route_id = f"{policy}-{model['seed']}"
            rows.append({"seed": model["seed"], "model_sha256": model["sha256"],
                         "threshold_policy": policy, "threshold": threshold.tolist(),
                         "occupancy": occupancy(index), **evaluated})
            routes.append(route_manifest(dataset_root, route_id, model["seed"], policy,
                                         addresses, query_logits[positions], index, evaluated))
        del document_raw, query_raw
        gc.collect()
    report = {"id": scale["id"], "document_count": scale["documents"], "query_count": len(positions),
              "e5_manifest_sha256": data["e5_manifest_sha256"],
              "input_manifest_sha256": data["input_manifest_sha256"],
              "query_ids_sha256": hash_ids(data["query_ids"]),
              "query_vectors_sha256": data["query_vectors_sha256"],
              "query_codes_sha256": data["query_codes_sha256"],
              "query_projection_sha256": data["query_projection_sha256"],
              "configuration_query_ids_sha256": hash_ids(numpy.asarray(positions_ids, dtype=object)),
              "document_ids_set_sha256": hash_id_set(document_ids),
              "nested_de_25k_document_ids_set_sha256": hash_id_set(anchor_document_ids), "rows": rows}
    materialized = {"id": scale["id"], "document_count": scale["documents"], "query_count": len(positions),
                    "source_query_positions": positions, "common": common, "routes": routes}
    del data, ranks, oracle
    gc.collect()
    return report, materialized, frozen_thresholds, document_ids, anchor_document_ids


def decide(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    checks = []
    primary = contract["route"]["primary_threshold_policy"]
    rule = contract["decision"]
    for dataset in datasets:
        rows = [row for row in dataset["rows"] if row["threshold_policy"] == primary]
        for row in rows:
            metrics = row["metrics"]
            checks.append({"dataset": dataset["id"], "seed": row["seed"],
                           "candidate_pass": metrics["candidate_fraction"] <= rule["maximum_candidate_fraction"],
                           "survival_pass": metrics["adc64_e5_oracle_survival"]
                           >= rule["minimum_adc64_e5_oracle_survival"],
                           "quality_pass": metrics["exact64_ndcg_retention_vs_full_e5"]
                           >= rule["minimum_exact64_ndcg_retention_vs_full_e5"]})
    return {"quality_transfer_passed": all(row["candidate_pass"] and row["survival_pass"]
                                            and row["quality_pass"] for row in checks),
            "native_1m_gate_pending": True, "checks": checks}


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[list[str], list[tuple[dict[str, Any], dict[str, numpy.ndarray]]]]:
    activation = contract["activation"]
    require(sha256(args.exact_e5_result) == activation["exact_e5_quality_result_sha256"]
            and sha256(args.exact_e5_evidence) == activation["exact_e5_evidence_sha256"]
            and sha256(args.training_result) == activation["training_sanity_result_sha256"]
            and sha256(args.german_split_result) == activation["german_split_result_sha256"],
            "frozen scale activation bytes differ")
    exact_evidence = json.loads(args.exact_e5_evidence.read_text(encoding="utf-8"))
    require(exact_evidence.get("passed") is True and exact_evidence.get("decision", {}).get("selected_exact_adc_limit") == 64,
            "frozen scale exact-E5 activation differs")
    training = json.loads(args.training_result.read_text(encoding="utf-8"))
    de = next(row for row in training["datasets"] if row["id"] == "de-25k")
    training_models = {(row["treatment"], row["seed"]): row for row in de["models"]}
    models = []
    for model in contract["frozen_models"]:
        path = args.training_model_root / "de-25k" / f"model-raw_euclidean_mined_pairs-{model['seed']}.npz"
        source = training_models[("raw_euclidean_mined_pairs", model["seed"])]
        require(path.is_file() and sha256(path) == model["sha256"] == source["model_sha256"],
                f"frozen scale model differs: {model['seed']}")
        arrays, _ = v4.base.read_model(path)
        models.append((model, arrays))
    split_result = json.loads(args.german_split_result.read_text(encoding="utf-8"))
    positions_ids = split_result["split"]["configuration_selection_query_ids"]
    require(len(positions_ids) == contract["query_partition"]["queries"], "frozen scale query partition differs")
    return positions_ids, models


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    positions_ids, models = validate_activation(contract, args)
    datasets, materialized = [], []
    frozen_thresholds: dict[int, numpy.ndarray] = {}
    previous_document_ids: set[str] | None = None
    anchor_document_ids: set[str] | None = None
    for scale in contract["scales"]:
        roots = {"e5": getattr(args, f"{scale['id'].replace('-', '_')}_e5_root"),
                 "input": getattr(args, f"{scale['id'].replace('-', '_')}_input_root")}
        require(all(roots.values()), f"frozen scale roots are required: {scale['id']}")
        report, payload, frozen_thresholds, previous_document_ids, anchor_document_ids = scale_run(
            scale, roots, positions_ids, models, frozen_thresholds, contract,
            args.materialization_root, previous_document_ids, anchor_document_ids)
        datasets.append(report)
        materialized.append(payload)
    require(len({row["query_ids_sha256"] for row in datasets}) == 1
            and len({row["query_vectors_sha256"] for row in datasets}) == 1
            and len({row["query_codes_sha256"] for row in datasets}) == 1
            and len({row["query_projection_sha256"] for row in datasets}) == 1
            and len({row["configuration_query_ids_sha256"] for row in datasets}) == 1
            and len({row["nested_de_25k_document_ids_set_sha256"] for row in datasets}) == 1,
            "frozen scale nested identity differs")
    result = {"schema_version": 1, "family": "neuroute_frozen_scale_transfer_quality_result",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
              "activation": contract["activation"], "source_files_sha256": source_hashes(),
              "datasets": datasets, "decision": decide(datasets, contract)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    manifest = {"schema_version": 1, "family": "neuroute_frozen_scale_transfer_native_materialization",
                "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
                "quality_result_sha256": sha256(args.output), "source_files_sha256": source_hashes(),
                "storage": contract["storage"], "native_timing": contract["native_timing"],
                "datasets": materialized}
    args.materialization_root.mkdir(parents=True, exist_ok=True)
    (args.materialization_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    logits = numpy.asarray([[1.0, -1.0, 2.0], [-1.0, 1.0, 2.0]], dtype=numpy.float32)
    require(addresses_from_logits(logits).tolist() == [5, 6], "frozen scale address self-test differs")
    index = build_index(numpy.asarray([1, 1, 3, 2], dtype=numpy.uint16), 2)
    candidates, accepted, requested = candidate_union([1, 3, 2], index, 3)
    require(candidates.tolist() == [0, 1, 2] and accepted == [1, 3] and requested == 4,
            "frozen scale candidate self-test differs")
    values = numpy.asarray([0.2, 0.8, 0.8, 0.1], dtype=numpy.float32)
    ids = numpy.asarray(["d", "c", "b", "a"], dtype=object)
    require(select_largest(values, ids, 2).tolist() == [2, 1], "frozen scale top-k self-test differs")
    print("NeuRoute frozen scale-transfer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-frozen-scale-transfer.example.json")
    parser.add_argument("--exact-e5-result", type=Path)
    parser.add_argument("--exact-e5-evidence", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for scale in ("de-25k", "de-100k", "de-1m"):
        parser.add_argument(f"--{scale}-e5-root", type=Path)
        parser.add_argument(f"--{scale}-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items() if name not in ("self_test", "contract")):
            parser.error("all frozen scale-transfer paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            numpy.linalg.LinAlgError, MemoryError) as error:
        print(f"run-neuroute-frozen-scale-transfer: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
