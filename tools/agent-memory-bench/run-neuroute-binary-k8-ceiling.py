#!/usr/bin/env python3
"""Measure exhaustive binary K8 address-ranking ceilings through full R4."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
SEEDS = (2026082701, 2026082702, 2026082703)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load("neuroute_binary_k8_base", "run-neuroute-shortlist-generator-bakeoff.py")
fixed = base.fixed
replay = base.replay
exact = base.exact


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


def source_hashes() -> dict[str, str]:
    names = ("run-neuroute-binary-k8-ceiling.py",
             "run-neuroute-shortlist-generator-bakeoff.py",
             "run-neuroute-fixed-top-m-router.py",
             "run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(THIS / name) for name in names}


replay.source_hashes = source_hashes


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("family") == "neuroute_binary_k8_representation_ceiling" and
            value["prototype_prefixes"] == [1, 2, 4, 8] and
            value["address_budgets"] == [1024, 2048, 4096, 8192] and
            value["partitions"]["locked_internal_may_not_select"] is True and
            value["decision"]["production_selection_forbidden"] is True,
            "binary K8 ceiling contract differs")
    return value


def stable_order(scores: np.ndarray, occupied: np.ndarray,
                 maximum: int) -> np.ndarray:
    rows = np.arange(len(scores), dtype=np.uint32)
    order = np.lexsort((rows, occupied, -scores))
    return order[:maximum].astype(np.uint32, copy=False)


def pack_words(values: np.ndarray) -> np.ndarray:
    packed = np.packbits(values >= 0.0, axis=1, bitorder="little")
    require(packed.shape[1] % 8 == 0, "binary K8 packed width differs")
    return np.ascontiguousarray(packed).view("<u8")


def random_orthogonal(dimension: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    matrix = rng.standard_normal((dimension, dimension), dtype=np.float32)
    q, r = np.linalg.qr(matrix)
    signs = np.where(np.diag(r) < 0.0, -1.0, 1.0).astype(np.float32)
    return np.ascontiguousarray(q * signs, dtype=np.float32)


def train_transform(faiss: Any, codec: dict[str, Any], sample: np.ndarray,
                    seed: int, iterations: int) -> Any:
    transform = codec["transform"]
    if transform == "identity":
        return None
    if transform == "seeded_random_orthogonal":
        return random_orthogonal(sample.shape[1], seed)
    if transform == "faiss_itq":
        result = faiss.ITQMatrix(sample.shape[1])
        result.seed = int(seed & 0x7fffffff)
        result.max_iter = iterations
        result.train(np.ascontiguousarray(sample, dtype=np.float32))
        return result
    if transform == "faiss_pca_then_itq":
        result = faiss.ITQTransform(sample.shape[1], int(codec["bits"]), True)
        result.itq.seed = int(seed & 0x7fffffff)
        result.itq.max_iter = iterations
        result.train(np.ascontiguousarray(sample, dtype=np.float32))
        return result
    raise ValueError("binary K8 transform differs")


def apply_transform(transform: Any, values: np.ndarray) -> np.ndarray:
    if transform is None:
        return np.ascontiguousarray(values, dtype=np.float32)
    if isinstance(transform, np.ndarray):
        return np.ascontiguousarray(values @ transform, dtype=np.float32)
    return np.ascontiguousarray(transform.apply_py(
        np.ascontiguousarray(values, dtype=np.float32)), dtype=np.float32)


def hamming_orders(codes: np.ndarray, query_codes: np.ndarray,
                   offsets: np.ndarray, active: np.ndarray,
                   occupied: np.ndarray, maximum: int,
                   prefixes: list[int]) -> dict[int, np.ndarray]:
    result = {prefix: np.empty((len(query_codes), maximum), dtype=np.uint32)
              for prefix in prefixes}
    for query_index, query in enumerate(query_codes):
        best = np.full(len(active), np.iinfo(np.uint16).max, dtype=np.uint16)
        for slot in range(max(prefixes)):
            valid = active > slot
            indices = offsets[:-1][valid] + slot
            distance = np.bitwise_count(codes[indices] ^ query).sum(
                axis=1, dtype=np.uint16)
            best[valid] = np.minimum(best[valid], distance)
            if slot + 1 in result:
                result[slot + 1][query_index] = stable_order(
                    -best.astype(np.float32), occupied, maximum)
    return result


def weighted_orders(signs: np.ndarray, queries: np.ndarray,
                    prototype_scale: np.ndarray | None, offsets: np.ndarray,
                    active: np.ndarray, occupied: np.ndarray, maximum: int,
                    prefixes: list[int]) -> dict[int, np.ndarray]:
    result = {prefix: np.empty((len(queries), maximum), dtype=np.uint32)
              for prefix in prefixes}
    best = np.full((len(queries), len(active)), -np.inf, dtype=np.float32)
    for slot in range(max(prefixes)):
        valid = active > slot
        indices = offsets[:-1][valid] + slot
        weights = signs[indices].astype(np.float32)
        if prototype_scale is not None:
            weights *= prototype_scale[indices, None]
        score = queries @ weights.T
        best[:, valid] = np.maximum(best[:, valid], score)
        if slot + 1 in result:
            for query_index in range(len(queries)):
                result[slot + 1][query_index] = stable_order(
                    best[query_index], occupied, maximum)
        del weights, score
    return result


def codec_orders(faiss: Any, codec: dict[str, Any], prototypes: np.ndarray,
                 queries: np.ndarray, offsets: np.ndarray, active: np.ndarray,
                 occupied: np.ndarray, contract: dict[str, Any], seed: int
                 ) -> tuple[dict[int, np.ndarray], dict[str, Any]]:
    training = contract["training"]
    count = min(int(training["sample_count"]), len(prototypes))
    sample_rows = np.linspace(0, len(prototypes) - 1, count, dtype=np.int64)
    sample = np.ascontiguousarray(prototypes[sample_rows], dtype=np.float32)
    transform_seed = int(training["base_seed"]) ^ int(seed) ^ int(codec["bits"])
    started = time.perf_counter()
    transform = train_transform(faiss, codec, sample, transform_seed,
                                int(training["itq_iterations"]))
    training_ms = (time.perf_counter() - started) * 1000.0
    projected_queries = apply_transform(transform, queries)
    projected = apply_transform(transform, prototypes)
    kind = codec["kind"]
    started = time.perf_counter()
    if kind == "hamming":
        orders = hamming_orders(pack_words(projected),
            pack_words(projected_queries), offsets, active, occupied,
            max(contract["address_budgets"]), contract["prototype_prefixes"])
        code_bytes = int(len(prototypes) * int(codec["bits"]) // 8)
        correction_bytes = 0
    else:
        signs = np.where(projected >= 0.0, 1, -1).astype(np.int8)
        prototype_scale = None
        if kind == "rabitq_ratio":
            l1_norm = np.sum(np.abs(projected), axis=1, dtype=np.float32)
            l2_norm = np.linalg.norm(projected, axis=1).astype(np.float32)
            require(np.all(l1_norm > 0.0), "RaBitQ denominator differs")
            prototype_scale = l2_norm / l1_norm
        orders = weighted_orders(signs, projected_queries, prototype_scale,
            offsets, active, occupied, max(contract["address_budgets"]),
            contract["prototype_prefixes"])
        code_bytes = int(signs.size // 8)
        correction_bytes = (0 if prototype_scale is None else
                            int(prototype_scale.nbytes))
        del signs
    scan_ms = (time.perf_counter() - started) * 1000.0 / len(queries)
    return orders, {"training_ms": training_ms,
        "directional_exhaustive_scan_ms_per_query": scan_ms,
        "prototype_code_bytes": code_bytes,
        "correction_bytes": correction_bytes,
        "bits_per_prototype": int(codec["bits"]),
        "active_prototypes": int(len(prototypes)),
        "transform_seed": transform_seed,
        "estimator": codec.get("estimator")}


def materialize(root: Path, treatment: str, arrays: dict[int, np.ndarray],
                counts: dict[int, int], args: argparse.Namespace,
                metadata: dict[int, dict[str, Any]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in SEEDS:
        path = (root / f"seed-{seed}.rows.u32le").resolve()
        arrays[seed].astype("<u4", copy=False).tofile(path)
        rows.append({"seed": seed, "occupied_addresses": counts[seed],
            "dtype": "<u4", "shape": list(arrays[seed].shape),
            "path": str(path), "bytes": path.stat().st_size,
            "sha256": sha256(path), "codec_diagnostics": metadata[seed]})
    value = {"schema_version": 1,
        "family": "neuroute_local_k8_address_shortlist_materialization",
        "treatment": treatment, "contract_sha256": sha256(args.contract),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "source_files_sha256": source_hashes(), "seeds": rows}
    path = root / "manifest.json"
    path.write_bytes(canonical(value))
    return path


def set_overlap(left: np.ndarray, right: np.ndarray, count: int) -> float:
    return len(set(map(int, left[:count])) & set(map(int, right[:count]))) / count


def offline_diagnostic(arrays: dict[int, np.ndarray], common: dict[int, Any],
                       budget: int, query_range: range,
                       metadata: dict[int, dict[str, Any]]) -> dict[str, Any]:
    same = []
    k8 = []
    for seed in SEEDS:
        for query in query_range:
            current = arrays[seed][query]
            same.append(set_overlap(current, common[seed]["fp32_same"][query],
                                    budget))
            k8.append(set_overlap(current, common[seed]["fp32_k8"][query],
                                  min(budget, 1024)))
    return {"mean_fp32_same_k_address_overlap": float(np.mean(same)),
        "mean_fp32_k8_top1024_address_recall": float(np.mean(k8)),
        "directional_exhaustive_scan_ms_per_query": float(np.mean([
            metadata[seed]["directional_exhaustive_scan_ms_per_query"]
            for seed in SEEDS])),
        "mean_prototype_code_bytes": float(np.mean([
            metadata[seed]["prototype_code_bytes"] +
            metadata[seed]["correction_bytes"] for seed in SEEDS]))}


def run(args: argparse.Namespace) -> None:
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("Faiss is required for binary K8 transforms") from error
    faiss.omp_set_num_threads(max(1, int(args.faiss_threads)))
    contract = load_contract(args.contract)
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    k8 = json.loads(args.k8_manifest.read_text(encoding="utf-8"))
    config_value = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    parent = exact.parent_protocol(config_value)
    internal_value = dict(config_value)
    internal_value["partition"] = "locked_internal"
    internal_value["requests"] = parent["requests"]
    args.output_root.mkdir(parents=True, exist_ok=True)
    internal_source = args.output_root / "internal-source-protocol.json"
    internal_source.write_bytes(canonical(internal_value))
    args.authoritative_e5_receipt = exact.authoritative_receipt(parent)
    data = exact.load_data(parent)
    doc_rows = exact.layout_doc_rows(args.layout_manifest)
    maximum = max(contract["address_budgets"])
    orders: dict[str, dict[int, np.ndarray]] = {}
    metadata: dict[str, dict[int, dict[str, Any]]] = {}
    common: dict[int, dict[str, Any]] = {}
    occupied_counts = {}
    for seed in SEEDS:
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        occupied = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "occupied_addresses")["file"], dtype="<u4")
        counts = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "address_counts")["file"], dtype="<u4")
        queries = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "query_vectors")["file"], dtype="<f4").reshape(152, 384)
        fp32_k8 = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "shortlist_rows")["file"], dtype="<u4").reshape(152, 1024)
        active = np.minimum(counts, 8).astype(np.int64)
        offsets = np.empty(len(active) + 1, dtype=np.uint64)
        offsets[0] = 0
        np.cumsum(active, out=offsets[1:])
        prototypes = np.memmap(Path(k8_seed["path"]), mode="r", dtype="<f4",
            shape=(int(k8_seed["active_prototypes"]), 384))
        common[seed] = {"occupied": occupied, "fp32_k8": fp32_k8,
                        "active": active, "offsets": offsets}
        occupied_counts[seed] = len(occupied)
        for codec in contract["codecs"]:
            codec_result, codec_metadata = codec_orders(faiss, codec,
                prototypes, queries, offsets, active, occupied, contract, seed)
            for prefix, values in codec_result.items():
                treatment = f"{codec['id']}-k{prefix}"
                orders.setdefault(treatment, {})[seed] = values
                metadata.setdefault(treatment, {})[seed] = dict(
                    codec_metadata, codec=codec["id"], prototype_prefix=prefix)
        del prototypes
        gc.collect()
    # Exact same-K controls are derived once from the frozen FP32 records.
    for seed in SEEDS:
        k8_seed = next(row for row in k8["seeds"] if row["seed"] == seed)
        layout_seed = next(row for row in layout["seeds"] if row["seed"] == seed)
        root = args.layout_manifest.parent / f"seed-{seed}"
        queries = np.fromfile(root / fixed.descriptor(layout_seed["mappings"],
            "query_vectors")["file"], dtype="<f4").reshape(152, 384)
        prototypes = np.memmap(Path(k8_seed["path"]), mode="r", dtype="<f4",
            shape=(int(k8_seed["active_prototypes"]), 384))
        active = common[seed]["active"]
        offsets = common[seed]["offsets"]
        occupied = common[seed]["occupied"]
        controls = {prefix: np.empty((152, maximum), dtype=np.uint32)
                    for prefix in contract["prototype_prefixes"]}
        best = np.full((152, len(active)), -np.inf, dtype=np.float32)
        for slot in range(8):
            valid = active > slot
            indices = offsets[:-1][valid] + slot
            best[:, valid] = np.maximum(best[:, valid], queries @
                np.ascontiguousarray(prototypes[indices], dtype=np.float32).T)
            if slot + 1 in controls:
                for query in range(152):
                    controls[slot + 1][query] = stable_order(
                        best[query], occupied, maximum)
        for prefix, values in controls.items():
            common[seed][f"fp32_k{prefix}"] = values
        del prototypes, best
        gc.collect()
    manifests = {}
    for treatment, arrays in orders.items():
        prefix = int(treatment.rsplit("-k", 1)[1])
        for seed in SEEDS:
            common[seed]["fp32_same"] = common[seed][f"fp32_k{prefix}"]
        manifests[treatment] = materialize(args.output_root / "shortlists" /
            treatment, treatment, arrays, occupied_counts, args,
            metadata[treatment])
    config_reference_protocol = replay.protocol(args.configuration_protocol,
        None, None, args.output_root / "protocols" / "configuration-reference.json",
        contract)
    internal_reference_protocol = replay.protocol(internal_source, None, None,
        args.output_root / "protocols" / "internal-reference.json", contract)
    config_inputs = replay.partition_inputs(config_reference_protocol, data, doc_rows)
    internal_inputs = replay.partition_inputs(internal_reference_protocol, data, doc_rows)
    reference_treatment = {"id": "global_fp32_k8", "kind": "fp32",
                           "record_bytes": 1536}
    config_references: dict[int, list[dict[str, Any]]] = {}
    config_reference = replay.run_point(args, contract, "configuration",
        config_reference_protocol, "global-fp32-k8", reference_treatment,
        config_inputs, config_references, True)
    config_summaries = [replay.aggregate(config_reference, config_reference,
        reference_treatment, None, contract["quality_gates"], None)]
    for treatment, manifest in manifests.items():
        prefix = int(treatment.rsplit("-k", 1)[1])
        for seed in SEEDS:
            common[seed]["fp32_same"] = common[seed][f"fp32_k{prefix}"]
        for budget in contract["address_budgets"]:
            point = f"{treatment}-m{budget}"
            protocol = replay.protocol(args.configuration_protocol, manifest,
                budget, args.output_root / "protocols" /
                f"configuration-{point}.json", contract)
            current = {"id": point, "kind": "shortlist_generator",
                       "record_bytes": 0}
            rows = replay.run_point(args, contract, "configuration", protocol,
                point, current, config_inputs, config_references, False)
            config_summaries.append(replay.aggregate(rows, config_reference,
                current, budget, contract["quality_gates"], offline_diagnostic(
                    orders[treatment], common, budget, range(76),
                    metadata[treatment])))
    candidates = [row for row in config_summaries
        if row.get("address_budget") is not None and
        row["address_budget"] <= contract["product_address_budget_maximum"]]
    candidates.sort(key=lambda row: (not row["passes_registered_gate"],
        replay.gate_distance(row, contract["quality_gates"]),
        row["address_budget"], row["offline_router_diagnostics"][
            "mean_prototype_code_bytes"], row["offline_router_diagnostics"][
            "directional_exhaustive_scan_ms_per_query"], row["id"]))
    opened = []
    families = set()
    for row in candidates:
        family = row["id"].rsplit("-m", 1)[0]
        if family in families:
            continue
        opened.append(row)
        families.add(family)
        if len(opened) == 2:
            break
    internal_references: dict[int, list[dict[str, Any]]] = {}
    internal_reference = replay.run_point(args, contract, "locked_internal",
        internal_reference_protocol, "global-fp32-k8", reference_treatment,
        internal_inputs, internal_references, True)
    internal_summaries = [replay.aggregate(internal_reference,
        internal_reference, reference_treatment, None,
        contract["quality_gates"], None)]
    internal_bindings = {}
    for selected in opened:
        treatment, budget_text = selected["id"].rsplit("-m", 1)
        budget = int(budget_text)
        prefix = int(treatment.rsplit("-k", 1)[1])
        for seed in SEEDS:
            common[seed]["fp32_same"] = common[seed][f"fp32_k{prefix}"]
        manifest = manifests[treatment]
        internal_bindings[treatment] = {"path": str(manifest.resolve()),
                                        "sha256": sha256(manifest)}
        protocol = replay.protocol(internal_source, manifest, budget,
            args.output_root / "protocols" / f"internal-{selected['id']}.json",
            contract)
        current = {"id": selected["id"], "kind": "shortlist_generator",
                   "record_bytes": 0}
        rows = replay.run_point(args, contract, "locked_internal", protocol,
            selected["id"], current, internal_inputs, internal_references, False)
        internal_summaries.append(replay.aggregate(rows, internal_reference,
            current, budget, contract["quality_gates"], offline_diagnostic(
                orders[treatment], common, budget, range(76, 152),
                metadata[treatment])))
    passed = [row for row in internal_summaries
        if row.get("address_budget") is not None and
        row.get("passes_registered_gate")]
    near = [row for row in candidates if row["mean_ndcg_loss"] <=
        contract["near_miss_gate"]["maximum_mean_downstream_ndcg_loss"] and
        row["mean_final_top10_overlap"] >=
        contract["near_miss_gate"]["minimum_mean_final_top10_overlap"]]
    result = {"schema_version": 1,
        "family": "neuroute_binary_k8_representation_ceiling_result",
        "claim_scope": contract["claim_scope"],
        "inputs": {"contract_sha256": sha256(args.contract),
            "layout_manifest_sha256": sha256(args.layout_manifest),
            "k8_manifest_sha256": sha256(args.k8_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "configuration_protocol_closure_sha256":
                replay.protocol_closure(args.configuration_protocol),
            "native_executable_sha256": sha256(args.native_executable),
            "source_files_sha256": source_hashes(),
            "authoritative_e5_receipt": args.authoritative_e5_receipt,
            "faiss_version": faiss.__version__},
        "faiss_threads": int(args.faiss_threads),
        "configuration": config_summaries,
        "locked_internal": internal_summaries,
        "internal_opened_from_configuration": [row["id"] for row in opened],
        "configuration_shortlist_manifests": {name: {
            "path": str(path.resolve()), "sha256": sha256(path)}
            for name, path in manifests.items()},
        "internal_shortlist_manifests": internal_bindings,
        "decision": {"selected": (min(passed, key=lambda row: (
            row["address_budget"], row["offline_router_diagnostics"][
                "mean_prototype_code_bytes"], row["id"])) if passed else None),
            "backend_followup_licensed": bool(passed),
            "learned_query_followup_licensed": not passed and bool(near),
            "configuration_near_miss_ids": [row["id"] for row in near],
            "production_licensed": False}}
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    scores = np.asarray([1.0, 3.0, 3.0, 2.0], dtype=np.float32)
    occupied = np.asarray([9, 8, 7, 6], dtype=np.uint32)
    require(stable_order(scores, occupied, 4).tolist() == [2, 1, 3, 0],
            "binary K8 stable order self-test failed")
    values = np.tile(np.asarray([[1.0, -1.0, 0.0, 2.0, -3.0, 4.0,
                                  -5.0, 6.0]], dtype=np.float32), (1, 8))
    require(pack_words(values).shape == (1, 1),
            "binary K8 packing self-test failed")
    hamming_values = np.asarray([
        [1.0] * 62 + [-1.0, -1.0],
        [1.0] * 64,
        [1.0] * 63 + [-1.0]], dtype=np.float32)
    hamming = hamming_orders(pack_words(hamming_values),
        pack_words(np.ones((1, 64), dtype=np.float32)),
        np.asarray([0, 2, 3], dtype=np.uint64),
        np.asarray([2, 1], dtype=np.int64),
        np.asarray([10, 20], dtype=np.uint32), 2, [1, 2])
    require(hamming[1][0].tolist() == [1, 0] and
            hamming[2][0].tolist() == [0, 1],
            "binary K8 min-over-prototypes Hamming self-test failed")
    weighted = weighted_orders(np.asarray([[-1, 1], [1, 1], [1, -1]],
        dtype=np.int8), np.asarray([[1.0, 0.0]], dtype=np.float32), None,
        np.asarray([0, 2, 3], dtype=np.uint64),
        np.asarray([2, 1], dtype=np.int64),
        np.asarray([10, 20], dtype=np.uint32), 2, [1, 2])
    require(weighted[1][0].tolist() == [1, 0] and
            weighted[2][0].tolist() == [0, 1],
            "binary K8 max-over-prototypes asymmetric self-test failed")
    scaled = weighted_orders(np.asarray([[1, 0], [1, 0]], dtype=np.int8),
        np.asarray([[1.0, 0.0]], dtype=np.float32),
        np.asarray([0.25, 0.75], dtype=np.float32),
        np.asarray([0, 1, 2], dtype=np.uint64),
        np.asarray([1, 1], dtype=np.int64),
        np.asarray([10, 20], dtype=np.uint32), 2, [1])
    require(scaled[1][0].tolist() == [1, 0],
            "binary K8 scalar-correction self-test failed")
    rotation = random_orthogonal(8, 7)
    require(np.allclose(rotation.T @ rotation, np.eye(8), atol=1e-5),
            "binary K8 orthogonal transform self-test failed")


def faiss_self_test() -> None:
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("Faiss is required for the ITQ self-test") from error
    rng = np.random.default_rng(7)
    sample = np.ascontiguousarray(rng.standard_normal((512, 384),
        dtype=np.float32))
    for codec in ({"transform": "faiss_itq", "bits": 384},
                  {"transform": "faiss_pca_then_itq", "bits": 256}):
        transform = train_transform(faiss, codec, sample, 11, 2)
        projected = apply_transform(transform, sample[:3])
        require(projected.shape == (3, int(codec["bits"])) and
                projected.dtype == np.float32 and
                np.all(np.isfinite(projected)),
                "binary K8 Faiss ITQ self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-binary-k8-ceiling.example.json")
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--faiss-threads", type=int, default=18)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--faiss-self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.faiss_self_test:
        faiss_self_test()
        return 0
    require(all(getattr(args, name) is not None for name in (
        "configuration_protocol", "layout_manifest", "k8_manifest",
        "native_executable", "output_root", "output")),
        "binary K8 ceiling inputs are required")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
