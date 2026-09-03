#!/usr/bin/env python3
"""Measure compact final rerank representations on frozen ADC256 top-64 pools."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
DIMENSIONS = 384


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_final_representation_planner", "plan-neuroute-final-representation.py")
exact = load("neuroute_final_representation_exact", "run-neuroute-exact-e5-rerank.py")
scale = load("neuroute_final_representation_scale", "run-neuroute-frozen-scale-transfer.py")


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-final-representation.py", "run-neuroute-final-representation.py",
             "run-neuroute-exact-e5-rerank.py", "run-neuroute-frozen-scale-transfer.py")
    return {name: sha256(THIS / name) for name in names}


def sequence_sha256(values: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<u4").tobytes()).hexdigest()


def update_sequence(digest: Any, query: int, values: numpy.ndarray) -> None:
    digest.update(int(query).to_bytes(4, "little"))
    digest.update(int(values.size).to_bytes(4, "little"))
    digest.update(numpy.asarray(values, dtype="<u4").tobytes())


def payload(path: Path, shape: list[int], dtype: str, external: bool = False) -> dict[str, Any]:
    return {"file": path.as_posix() if external else path.name, "sha256": sha256(path),
            "shape": shape, "dtype": dtype, "external_frozen_root": external}


def write_array(path: Path, values: numpy.ndarray, dtype: str) -> dict[str, Any]:
    packed = numpy.ascontiguousarray(values, dtype=dtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    packed.tofile(path)
    return payload(path, list(packed.shape), dtype)


def resolve_source(root: Path, dataset: dict[str, Any], item: dict[str, Any]) -> Path:
    value = Path(item["file"])
    if value.is_absolute():
        return value
    return root / dataset["id"] / value


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = {
        "exact_contract_sha256": sha256(args.exact_contract),
        "exact_result_sha256": sha256(args.exact_result),
        "exact_evidence_sha256": sha256(args.exact_evidence),
        "exact_materialization_sha256": sha256(args.exact_materialization_root / "manifest.json"),
        "scale_contract_sha256": sha256(args.scale_contract),
        "scale_result_sha256": sha256(args.scale_result),
        "scale_evidence_sha256": sha256(args.scale_evidence),
        "scale_materialization_sha256": sha256(args.scale_materialization_root / "manifest.json"),
    }
    require(actual == contract["activation"], "final-representation activation bytes differ")
    exact_evidence = json.loads(args.exact_evidence.read_text(encoding="utf-8"))
    scale_evidence = json.loads(args.scale_evidence.read_text(encoding="utf-8"))
    require(exact_evidence.get("passed") is True
            and exact_evidence.get("decision", {}).get("selected_exact_adc_limit") == 64,
            "final-representation exact evidence differs")
    require(scale_evidence.get("passed") is True
            and scale_evidence.get("decision", {}).get("selected") == "frozen_A_12bit_256",
            "final-representation scale evidence differs")
    scale_contract = scale.planner.load_contract(args.scale_contract)
    require(sha256(args.training_result)
            == scale_contract["activation"]["training_sanity_result_sha256"],
            "final-representation training result differs")
    return (json.loads((args.exact_materialization_root / "manifest.json").read_text(encoding="utf-8")),
            json.loads((args.scale_materialization_root / "manifest.json").read_text(encoding="utf-8")))


def pack_codes(values: numpy.ndarray, bits: int) -> numpy.ndarray:
    rows, columns = values.shape
    output = numpy.zeros((rows, (columns * bits + 7) // 8), dtype=numpy.uint8)
    for column in range(columns):
        bit = column * bits
        byte, shift = divmod(bit, 8)
        current = values[:, column].astype(numpy.uint16)
        output[:, byte] |= numpy.asarray(current << shift, dtype=numpy.uint8)
        if shift + bits > 8:
            output[:, byte + 1] |= numpy.asarray(current >> (8 - shift), dtype=numpy.uint8)
    return output


def scalar_quantize(values: numpy.ndarray, maximum: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    scales = numpy.max(numpy.abs(values), axis=1).astype(numpy.float32) / float(maximum)
    scales[scales == 0.0] = 1.0
    codes = numpy.clip(numpy.rint(values / scales[:, None]), -maximum, maximum).astype(numpy.int16)
    return codes, scales


def materialize_representations(documents: numpy.ndarray, root: Path,
                                fp32_source: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    count = documents.shape[0]
    entries: list[dict[str, Any]] = [{
        "id": "fp32", "kind": "fp32", "payload_bytes_per_document": DIMENSIONS * 4,
        "encoded": payload(fp32_source, [count, DIMENSIONS], "<f4", True),
    }]
    specifications = (("fp16", "<f2", 16, None), ("int8_symmetric", "<i1", 8, 127),
                      ("int4_symmetric", "packed_u4_offset7", 4, 7),
                      ("ternary_2bit", "packed_u2_offset1", 2, 1),
                      ("five_level_3bit", "packed_u3_offset2", 3, 2))
    handles: dict[str, Any] = {}
    paths: dict[str, tuple[Path, Path | None]] = {}
    try:
        for name, _, _, maximum in specifications:
            code_path = root / f"{name}.bin"
            scale_path = None if maximum is None else root / f"{name}-scales.f32le"
            handles[name] = (code_path.open("wb"), None if scale_path is None else scale_path.open("wb"))
            paths[name] = (code_path, scale_path)
        for start in range(0, count, 65536):
            values = numpy.asarray(documents[start:start + 65536], dtype=numpy.float32)
            numpy.asarray(values, dtype="<f2").tofile(handles["fp16"][0])
            for name, _, bits, maximum in specifications[1:]:
                codes, scales = scalar_quantize(values, int(maximum))
                if bits == 8:
                    numpy.asarray(codes, dtype="<i1").tofile(handles[name][0])
                else:
                    pack_codes((codes + int(maximum)).astype(numpy.uint8), bits).tofile(handles[name][0])
                numpy.asarray(scales, dtype="<f4").tofile(handles[name][1])
    finally:
        for code, scale_handle in handles.values():
            code.close()
            if scale_handle is not None:
                scale_handle.close()
    for name, dtype, bits, maximum in specifications:
        code_path, scale_path = paths[name]
        code_bytes = (DIMENSIONS * bits + 7) // 8
        entry = {"id": name, "kind": name, "payload_bytes_per_document": code_bytes,
                 "encoded": payload(code_path, [count, code_bytes if bits < 8 else DIMENSIONS], dtype)}
        if maximum is not None:
            entry["scale"] = payload(scale_path, [count], "<f4")
            entry["metadata_bytes_per_document"] = 4
            entry["total_bytes_per_document"] = code_bytes + 4
        else:
            entry["metadata_bytes_per_document"] = 0
            entry["total_bytes_per_document"] = code_bytes
        entries.append(entry)

    thresholds = numpy.median(documents, axis=0).astype(numpy.float32)
    sums = numpy.zeros((DIMENSIONS, 2), dtype=numpy.float64)
    counts = numpy.zeros((DIMENSIONS, 2), dtype=numpy.int64)
    binary_path = root / "coordinate_binary_adc384.bin"
    with binary_path.open("wb") as stream:
        for start in range(0, count, 65536):
            values = numpy.asarray(documents[start:start + 65536], dtype=numpy.float32)
            codes = values >= thresholds[None, :]
            numpy.packbits(codes, axis=1, bitorder="little").tofile(stream)
            sums[:, 1] += numpy.where(codes, values, 0.0).sum(axis=0, dtype=numpy.float64)
            sums[:, 0] += numpy.where(~codes, values, 0.0).sum(axis=0, dtype=numpy.float64)
            counts[:, 1] += codes.sum(axis=0, dtype=numpy.int64)
            counts[:, 0] += (~codes).sum(axis=0, dtype=numpy.int64)
    centroids = numpy.asarray(sums / numpy.maximum(counts, 1), dtype=numpy.float32)
    centroid_path = root / "coordinate_binary_adc384-centroids.f32le"
    numpy.asarray(centroids, dtype="<f4").tofile(centroid_path)
    entries.extend([
        {"id": "binary_adc256", "kind": "existing_adc_order", "payload_bytes_per_document": 32,
         "metadata_bytes_per_document": 0, "total_bytes_per_document": 32},
        {"id": "coordinate_binary_adc384", "kind": "coordinate_binary_adc384",
         "payload_bytes_per_document": 48, "metadata_bytes_per_document": 0,
         "total_bytes_per_document": 48,
         "encoded": payload(binary_path, [count, 48], "packed_u1_little"),
         "centroids": payload(centroid_path, [DIMENSIONS, 2], "<f4")},
    ])
    return entries, {"thresholds": thresholds, "centroids": centroids}


def exact_pools(dataset: dict[str, Any], source: dict[str, Any], root: Path) -> dict[int, numpy.ndarray]:
    result = {}
    for route in source["routes"]:
        path = root / dataset["id"] / str(route["seed"]) / route["adc_positions"]["file"]
        require(sha256(path) == route["adc_positions"]["sha256"], "final-representation ADC pool bytes differ")
        values = numpy.fromfile(path, dtype="<u4").reshape(dataset["query_count"], -1)
        result[int(route["seed"])] = numpy.asarray(values[:, :64], dtype=numpy.uint32)
    return result


def scale_pools(data: dict[str, Any], positions: list[int], models: list[tuple[dict[str, Any], dict[str, numpy.ndarray]]],
                contract: dict[str, Any], source: dict[str, Any]) -> dict[int, numpy.ndarray]:
    expected_routes = {int(route["seed"]): route for route in source["routes"]
                       if route["threshold_policy"] == "per_scale_document_median"}
    result = {}
    for model, arrays in models:
        document_raw = scale.infer_batched(data["documents"], arrays)
        threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
        query_logits = scale.infer_batched(data["queries"], arrays) - threshold
        index = scale.build_index(scale.addresses_from_logits(document_raw - threshold), 12)
        rows = []
        for local_query, position in enumerate(positions):
            requested = exact.v4.base.alignment.german.diagnostic.addresses(query_logits[position], 12, 256)
            candidates, _, _ = scale.candidate_union(requested, index, int(len(data["document_ids"]) * 0.1))
            xor = numpy.bitwise_xor(data["document_codes"][candidates], data["query_codes"][position])
            distances = scale.POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
            hamming = candidates[scale.select_smallest(distances, data["document_ids"][candidates], 768)]
            bits = numpy.unpackbits(data["document_codes"][hamming], axis=1, bitorder="little")
            table = (data["query_projection"][position, :, None] - data["adc_centroids"]) ** 2
            adc_distances = table[numpy.arange(256)[None, :], bits].sum(axis=1)
            pool = hamming[scale.select_smallest(adc_distances, data["document_ids"][hamming], 64)]
            expected = expected_routes[int(model["seed"])]["expected"][0]["queries"][local_query]
            require(sequence_sha256(pool) == expected["adc_sha256"],
                    "final-representation scale ADC pool replay differs")
            rows.append(pool)
        result[int(model["seed"])] = numpy.asarray(rows, dtype=numpy.uint32)
    return result


def inversions(order: numpy.ndarray) -> int:
    return sum(int(numpy.count_nonzero(order[index + 1:] < order[index]))
               for index in range(order.size))


def rank_representation(name: str, documents: numpy.ndarray, query: numpy.ndarray,
                        pool: numpy.ndarray, document_ids: numpy.ndarray,
                        coordinate: dict[str, Any]) -> numpy.ndarray:
    if name == "binary_adc256":
        return numpy.asarray(pool, dtype=numpy.uint32)
    values = numpy.asarray(documents[pool], dtype=numpy.float32)
    if name == "fp32":
        scores = (values * query[None, :]).sum(axis=1, dtype=numpy.float32)
    elif name == "fp16":
        scores = (values.astype(numpy.float16).astype(numpy.float32) * query[None, :]).sum(
            axis=1, dtype=numpy.float32)
    elif name in ("int8_symmetric", "int4_symmetric", "ternary_2bit", "five_level_3bit"):
        maximum = {"int8_symmetric": 127, "int4_symmetric": 7,
                   "ternary_2bit": 1, "five_level_3bit": 2}[name]
        codes, scales = scalar_quantize(values, maximum)
        scores = (codes.astype(numpy.float32) * query[None, :]).sum(axis=1) * scales
    elif name == "coordinate_binary_adc384":
        codes = values >= coordinate["thresholds"][None, :]
        table = (query[:, None] - coordinate["centroids"]) ** 2
        scores = -table[numpy.arange(DIMENSIONS)[None, :], codes.astype(numpy.uint8)].sum(axis=1)
    else:
        raise ValueError(f"unknown final representation: {name}")
    return pool[numpy.lexsort((document_ids[pool], -scores))].astype(numpy.uint32)


def evaluate(data: dict[str, Any], positions: list[int], pools: dict[int, numpy.ndarray],
             representations: list[dict[str, Any]], coordinate: dict[str, Any],
             contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    names = [item["id"] for item in representations]
    for seed, seed_pools in pools.items():
        per_rep = {name: [] for name in names}
        ranked_all: dict[str, list[numpy.ndarray]] = {name: [] for name in names}
        for local_query, position in enumerate(positions):
            pool = seed_pools[local_query]
            rankings = {name: rank_representation(name, data["documents"], data["queries"][position],
                                                   pool, data["document_ids"], coordinate)
                        for name in names}
            baseline = rankings["fp32"]
            baseline_index = {int(value): index for index, value in enumerate(baseline)}
            for name, ranking in rankings.items():
                permutation = numpy.asarray([baseline_index[int(value)] for value in ranking], dtype=numpy.int32)
                tau = 1.0 - 4.0 * inversions(permutation) / (len(pool) * (len(pool) - 1))
                top = ranking[:10]
                per_rep[name].append({
                    "query": local_query, "source_query_position": int(position),
                    "ranked_sha256": sequence_sha256(top),
                    "ndcg_at_10": scale.ndcg(data, position, top),
                    "top10_overlap_with_fp32": float(numpy.isin(top, baseline[:10]).sum()) / 10.0,
                    "top1_match_with_fp32": bool(top[0] == baseline[0]),
                    "kendall_tau_b_on_pool": tau,
                })
                ranked_all[name].append(top)
        for representation in representations:
            name = representation["id"]
            rows = per_rep[name]
            digest = hashlib.sha256()
            for query, ranking in enumerate(ranked_all[name]):
                update_sequence(digest, query, ranking)
            result.append({
                "seed": seed, "representation": name, "query_count": len(rows),
                "payload_bytes_per_document": representation.get("payload_bytes_per_document", 0),
                "total_bytes_per_document": representation.get("total_bytes_per_document",
                                                                  representation.get("payload_bytes_per_document", 0)),
                "metrics": {
                    "ndcg_at_10": float(numpy.mean([row["ndcg_at_10"] for row in rows])),
                    "top10_overlap_with_fp32": float(numpy.mean([row["top10_overlap_with_fp32"] for row in rows])),
                    "top1_match_with_fp32": float(numpy.mean([row["top1_match_with_fp32"] for row in rows])),
                    "kendall_tau_b_on_pool": float(numpy.mean([row["kendall_tau_b_on_pool"] for row in rows])),
                },
                "ranked_sequence_sha256": digest.hexdigest(), "queries": rows,
            })
    return result


def decide(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    names = [item["id"] for item in contract["representations"]]
    comparisons = []
    rng = numpy.random.default_rng(contract["quality"]["bootstrap_seed"])
    for name in names:
        dataset_losses, paired = [], []
        for dataset in datasets:
            baselines = {(row["seed"], query["query"]): query["ndcg_at_10"]
                         for row in dataset["rows"] if row["representation"] == "fp32"
                         for query in row["queries"]}
            losses = [baselines[(row["seed"], query["query"])] - query["ndcg_at_10"]
                      for row in dataset["rows"] if row["representation"] == name
                      for query in row["queries"]]
            dataset_losses.append(float(numpy.mean(losses)))
            paired.extend(losses)
        samples = numpy.asarray(paired, dtype=numpy.float64)
        bootstrap = numpy.mean(rng.choice(samples, size=(contract["quality"]["bootstrap_replicates"],
                                                        samples.size), replace=True), axis=1)
        eligible = (float(numpy.mean(dataset_losses))
                    <= contract["decision"]["maximum_cross_dataset_mean_ndcg_loss_vs_fp32"]
                    and max(dataset_losses)
                    <= contract["decision"]["maximum_per_dataset_ndcg_loss_vs_fp32"])
        comparisons.append({"representation": name, "dataset_ndcg_losses": dataset_losses,
                            "cross_dataset_mean_ndcg_loss": float(numpy.mean(dataset_losses)),
                            "paired_mean_loss_ci95": [float(numpy.quantile(bootstrap, 0.025)),
                                                       float(numpy.quantile(bootstrap, 0.975))],
                            "quality_eligible": eligible})
    mean_ndcg = {name: float(numpy.mean([
        row["metrics"]["ndcg_at_10"] for dataset in datasets for row in dataset["rows"]
        if row["representation"] == name])) for name in names}
    gain = mean_ndcg["coordinate_binary_adc384"] - mean_ndcg["binary_adc256"]
    gap = mean_ndcg["fp32"] - mean_ndcg["coordinate_binary_adc384"]
    return {"quality_comparisons": comparisons, "native_selection_pending": True,
            "adc384_gain_vs_adc256": gain, "remaining_adc384_gap_vs_fp32": gap,
            "overcomplete_followup_quality_condition":
                gain >= contract["decision"]["overcomplete_followup_if_adc384_gain_vs_adc256_at_least"]
                and gap >= contract["decision"]["overcomplete_followup_if_remaining_gap_vs_fp32_at_least"]}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    exact_manifest, scale_manifest = validate_activation(contract, args)
    v4_contract = exact.v4.planner.load_contract(args.v4_contract)
    v4_by_id = {item["id"]: item for item in v4_contract["datasets"]}
    exact_by_id = {item["id"]: item for item in exact_manifest["datasets"]}
    report_datasets, materialized_datasets = [], []
    roots = {language: {name: getattr(args, f"{language}_{name}_root")
                        for name in ("result", "e5", "input")} for language in ("de", "fr", "ja")}
    for frozen in contract["datasets"][:3]:
        data, _, split = exact.v4.base.load_dataset(v4_by_id[frozen["id"]], roots[frozen["language"]])
        positions_by_id = {value: index for index, value in enumerate(data["query_ids"])}
        positions = [positions_by_id[value] for value in split["configuration_selection_query_ids"]]
        source = exact_by_id[frozen["id"]]
        pools = exact_pools(frozen, source, args.exact_materialization_root)
        dataset_root = args.materialization_root / frozen["id"]
        fp32_source = resolve_source(args.exact_materialization_root, source, source["common"]["document_vectors"])
        representations, coordinate = materialize_representations(data["documents"],
                                                                   dataset_root / "representations", fp32_source)
        route_entries = []
        for seed, values in pools.items():
            route_entries.append({"seed": seed, "pool": write_array(dataset_root / str(seed) / "pool.u32le",
                                                                       values, "<u4")})
        rows = evaluate(data, positions, pools, representations, coordinate, contract)
        for route in route_entries:
            route["expected"] = [{
                "representation": row["representation"],
                "ranked_sequence_sha256": row["ranked_sequence_sha256"],
                "queries": [{"query": query["query"], "ranked_sha256": query["ranked_sha256"]}
                            for query in row["queries"]],
            } for row in rows if row["seed"] == route["seed"]]
        report_datasets.append({"id": frozen["id"], "document_count": len(data["document_ids"]),
                                "query_count": len(positions), "rows": rows})
        materialized_datasets.append({"id": frozen["id"], "document_count": len(data["document_ids"]),
                                      "query_count": len(positions),
                                      "query_vectors": write_array(dataset_root / "query-vectors.f32le",
                                                                     data["queries"][positions], "<f4"),
                                      "document_id_rank": write_array(dataset_root / "document-id-rank.u32le",
                                                                        exact.native.lexicographic_ranks(data["document_ids"]), "<u4"),
                                      "routes": route_entries, "representations": representations})

    scale_contract = scale.planner.load_contract(args.scale_contract)
    scale_config = next(item for item in scale_contract["scales"] if item["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root, args.de_1m_input_root)
    split_result = json.loads(args.german_split_result.read_text(encoding="utf-8"))
    require(sha256(args.german_split_result) == scale_contract["activation"]["german_split_result_sha256"],
            "final-representation German split differs")
    position_by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [position_by_id[value] for value in split_result["split"]["configuration_selection_query_ids"]]
    models = []
    for model in scale_contract["frozen_models"]:
        path = args.training_model_root / "de-25k" / f"model-raw_euclidean_mined_pairs-{model['seed']}.npz"
        require(path.is_file() and sha256(path) == model["sha256"], "final-representation model bytes differ")
        arrays, _ = exact.v4.base.read_model(path)
        models.append((model, arrays))
    scale_source = next(item for item in scale_manifest["datasets"] if item["id"] == "de-1m")
    pools = scale_pools(data, positions, models, scale_contract, scale_source)
    dataset_root = args.materialization_root / "de-1m"
    fp32_source = resolve_source(args.scale_materialization_root, scale_source,
                                 scale_source["common"]["document_vectors"])
    representations, coordinate = materialize_representations(data["documents"],
                                                               dataset_root / "representations", fp32_source)
    route_entries = [{"seed": seed, "pool": write_array(dataset_root / str(seed) / "pool.u32le",
                                                           values, "<u4")} for seed, values in pools.items()]
    rows = evaluate(data, positions, pools, representations, coordinate, contract)
    for route in route_entries:
        route["expected"] = [{
            "representation": row["representation"],
            "ranked_sequence_sha256": row["ranked_sequence_sha256"],
            "queries": [{"query": query["query"], "ranked_sha256": query["ranked_sha256"]}
                        for query in row["queries"]],
        } for row in rows if row["seed"] == route["seed"]]
    report_datasets.append({"id": "de-1m", "document_count": len(data["document_ids"]),
                            "query_count": len(positions), "rows": rows})
    materialized_datasets.append({"id": "de-1m", "document_count": len(data["document_ids"]),
                                  "query_count": len(positions),
                                  "query_vectors": write_array(dataset_root / "query-vectors.f32le",
                                                                 data["queries"][positions], "<f4"),
                                  "document_id_rank": write_array(dataset_root / "document-id-rank.u32le",
                                                                    exact.native.lexicographic_ranks(data["document_ids"]), "<u4"),
                                  "routes": route_entries, "representations": representations})
    result = {"schema_version": 1, "family": "neuroute_final_representation_quality_result",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
              "activation": contract["activation"], "source_files_sha256": source_hashes(),
              "datasets": report_datasets, "decision": decide(report_datasets, contract)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    manifest = {"schema_version": 1, "family": "neuroute_final_representation_materialization",
                "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
                "quality_result_sha256": sha256(args.output), "source_files_sha256": source_hashes(),
                "datasets": materialized_datasets}
    args.materialization_root.mkdir(parents=True, exist_ok=True)
    (args.materialization_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    values = numpy.asarray([[0, 1, 2, 4]], dtype=numpy.uint8)
    packed = pack_codes(values, 3)
    require(packed.tolist() == [[136, 8]], "final-representation bit packing differs")
    documents = numpy.asarray([[1.0, -0.5], [0.0, 0.0]], dtype=numpy.float32)
    codes, scales = scalar_quantize(documents, 7)
    require(codes.tolist() == [[7, -3], [0, 0]] and scales[1] == 1.0,
            "final-representation scalar quantizer differs")
    require(sequence_sha256(numpy.asarray([7, 2, 99], dtype=numpy.uint32))
            == "1673c447a7acb075da4fcf6fceaae46afa50428aa1b77fdc6a2868c3248120c1",
            "final-representation sequence differs")
    print("NeuRoute final-representation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-final-representation.example.json")
    for prefix in ("exact", "scale"):
        parser.add_argument(f"--{prefix}-contract", type=Path)
        parser.add_argument(f"--{prefix}-result", type=Path)
        parser.add_argument(f"--{prefix}-evidence", type=Path)
        parser.add_argument(f"--{prefix}-materialization-root", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    for language in ("de", "fr", "ja"):
        for name in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{name}-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [value for key, value in vars(args).items()
                    if key not in ("self_test",) and key != "contract"]
        if any(value is None for value in required):
            parser.error("all activation, dataset, and output paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-final-representation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
