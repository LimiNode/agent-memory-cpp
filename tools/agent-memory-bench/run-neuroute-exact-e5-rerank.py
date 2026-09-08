#!/usr/bin/env python3
"""Replay frozen A@256 and measure the marginal value of exact E5 reranking."""

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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_exact_e5_planner", "plan-neuroute-exact-e5-rerank.py")
v4 = load("neuroute_exact_e5_v4", "run-neuroute-relevance-aware-v4.py")
native = load("neuroute_exact_e5_native_helpers", "materialize-neuroute-native-mdbx-cost.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-exact-e5-rerank.py",
        "run-neuroute-exact-e5-rerank.py",
        "run-neuroute-relevance-aware-v4.py",
        "run-neuroute-training-sanity.py",
        "run-direct-learned-semantic-address.py",
        "materialize-neuroute-native-mdbx-cost.py",
    )
    return {name: sha256(THIS / name) for name in names}


def update_sequence(digest: Any, query: int, values: numpy.ndarray) -> None:
    digest.update(int(query).to_bytes(4, "little"))
    digest.update(int(values.size).to_bytes(4, "little"))
    digest.update(numpy.asarray(values, dtype="<u4").tobytes())


def sequence_sha256(values: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<u4").tobytes()).hexdigest()


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    activation = contract["activation"]
    bound = {
        "v4_contract_sha256": sha256(args.v4_contract),
        "v4_quality_result_sha256": sha256(args.v4_result),
        "v4_evidence_sha256": sha256(args.v4_evidence),
        "v4_native_result_sha256": sha256(args.v4_native_result),
        "v4_native_materialization_sha256": sha256(args.v4_native_materialization),
    }
    require(bound == activation, "exact-E5 activation bytes differ")
    v4_contract = v4.planner.load_contract(args.v4_contract)
    v4_result = json.loads(args.v4_result.read_text(encoding="utf-8"))
    require(v4_result.get("family") == "neuroute_relevance_aware_v4_quality_result"
            and v4_result.get("contract_sha256") == activation["v4_contract_sha256"],
            "exact-E5 v4 quality binding differs")
    evidence = json.loads(args.v4_evidence.read_text(encoding="utf-8"))
    native_result = json.loads(args.v4_native_result.read_text(encoding="utf-8"))
    native_manifest = json.loads(args.v4_native_materialization.read_text(encoding="utf-8"))
    require(evidence.get("quality_integrity_replay_passed") is True
            and evidence.get("native_integrity_replay_passed") is True
            and evidence.get("quality_result_sha256") == activation["v4_quality_result_sha256"],
            "exact-E5 v4 evidence differs")
    require(native_result.get("family") == "neuroute_relevance_aware_v4_native_result"
            and native_result.get("materialization_sha256")
            == activation["v4_native_materialization_sha256"], "exact-E5 v4 native result differs")
    require(native_manifest.get("quality_result_sha256")
            == activation["v4_quality_result_sha256"], "exact-E5 v4 materialization differs")
    return v4_contract, v4_result


def rank_hamming_adc(data: dict[str, Any], query_position: int,
                     candidates: numpy.ndarray, hamming_limit: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    xor = numpy.bitwise_xor(data["document_codes"][candidates], data["query_codes"][query_position])
    distances = v4.base.alignment.german.direct.POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)
    hamming = candidates[numpy.lexsort((data["document_ids"][candidates], distances))[:hamming_limit]]
    table = (data["query_projection"][query_position, :, None] - data["adc_centroids"]) ** 2
    adc_distances = table[numpy.arange(256)[None, :], data["document_bits"][hamming]].sum(axis=1)
    adc = hamming[numpy.lexsort((data["document_ids"][hamming], adc_distances))]
    return hamming.astype(numpy.int32), adc.astype(numpy.int32)


def ndcg(data: dict[str, Any], query_position: int, ranked: numpy.ndarray) -> float:
    return v4.base.alignment.german.quality.dcg_at_10(
        data["document_ids"][ranked], data["qrels"][data["query_ids"][query_position]])


def exact_rank(data: dict[str, Any], query_position: int,
               pool: numpy.ndarray, result_k: int) -> numpy.ndarray:
    scores = data["documents"][pool] @ data["queries"][query_position]
    return pool[numpy.lexsort((data["document_ids"][pool], -scores))[:result_k]].astype(numpy.int32)


def mean_rows(rows: list[dict[str, Any]], field: str) -> float:
    return float(numpy.mean([row[field] for row in rows], dtype=numpy.float64))


def evaluate_seed(data: dict[str, Any], positions: list[int], query_logits: numpy.ndarray,
                  index: dict[str, Any], oracle: numpy.ndarray, seed: int,
                  contract: dict[str, Any]) -> tuple[dict[str, Any], numpy.ndarray]:
    route = contract["frozen_route"]
    cascade = contract["cascade"]
    stage_names = ["hamming_only", "adc_only"] + [f"exact_e5_on_adc_{limit}"
                                                              for limit in cascade["adc_limits"]]
    digests = {name: hashlib.sha256() for name in ("candidate", "hamming", "adc") + tuple(stage_names)}
    query_rows: list[dict[str, Any]] = []
    materialized_adc: list[numpy.ndarray] = []
    for local_query, position in enumerate(positions):
        addresses = v4.base.alignment.german.diagnostic.addresses(
            query_logits[position], route["bits"], route["probes"])
        candidates, accepted = v4.base.alignment.german.direct.candidate_union(
            addresses, index["postings"], len(data["document_ids"]), route["candidate_mass_target"])
        hamming, adc = rank_hamming_adc(data, position, candidates, cascade["hamming_limit"])
        require(hamming.size == cascade["hamming_limit"] and adc.size == cascade["hamming_limit"],
                "exact-E5 fixed Hamming frontier is incomplete")
        materialized_adc.append(adc[:max(cascade["adc_limits"])])
        rankings = {
            "hamming_only": hamming[:cascade["result_k"]],
            "adc_only": adc[:cascade["result_k"]],
        }
        for limit in cascade["adc_limits"]:
            rankings[f"exact_e5_on_adc_{limit}"] = exact_rank(
                data, position, adc[:limit], cascade["result_k"])
        for name, values in (("candidate", candidates), ("hamming", hamming), ("adc", adc)):
            update_sequence(digests[name], local_query, values)
        stages: dict[str, Any] = {}
        for name, ranked in rankings.items():
            update_sequence(digests[name], local_query, ranked)
            stages[name] = {
                "ranked_sha256": sequence_sha256(ranked),
                "ndcg_at_10": ndcg(data, position, ranked),
                "e5_oracle_overlap_at_10": float(numpy.isin(oracle[position], ranked).sum()) / oracle.shape[1],
            }
        query_rows.append({
            "query_id": data["query_ids"][position], "source_query_position": int(position),
            "requested_address_count": len(addresses), "accepted_probe_count": len(accepted),
            "candidate_count": int(candidates.size), "candidate_sha256": sequence_sha256(candidates),
            "hamming_sha256": sequence_sha256(hamming), "adc_sha256": sequence_sha256(adc),
            "stages": stages,
        })
    metrics = {
        "candidate_count": mean_rows(query_rows, "candidate_count"),
        "candidate_fraction": mean_rows(query_rows, "candidate_count") / len(data["document_ids"]),
        "accepted_probes": mean_rows(query_rows, "accepted_probe_count"),
        "stages": {
            name: {
                "ndcg_at_10": float(numpy.mean([row["stages"][name]["ndcg_at_10"] for row in query_rows])),
                "e5_oracle_overlap_at_10": float(numpy.mean([
                    row["stages"][name]["e5_oracle_overlap_at_10"] for row in query_rows])),
            } for name in stage_names
        },
    }
    row = {
        "seed": seed, "query_count": len(positions), "metrics": metrics, "queries": query_rows,
        **{f"{name}_sequence_sha256": digest.hexdigest() for name, digest in digests.items()},
    }
    return row, numpy.asarray(materialized_adc, dtype=numpy.uint32)


def write_array(path: Path, values: numpy.ndarray, dtype: str) -> dict[str, Any]:
    packed = numpy.ascontiguousarray(values, dtype=dtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    packed.tofile(path)
    return {"file": path.name, "sha256": sha256(path), "shape": list(packed.shape), "dtype": dtype}


def dataset_run(dataset: dict[str, Any], v4_dataset: dict[str, Any], roots: dict[str, Path],
                v4_result_dataset: dict[str, Any], contract: dict[str, Any],
                model_root: Path, materialization_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data, _, split = v4.base.load_dataset(v4_dataset, roots)
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [by_id[value] for value in split["configuration_selection_query_ids"]]
    require(len(positions) == dataset["configuration_queries"],
            f"exact-E5 configuration partition differs: {dataset['id']}")
    oracle, _ = v4.base.alignment.german.direct.exact_oracle(data, contract["cascade"]["oracle_k"])
    frozen_models = {(row["treatment"], row["seed"]): row for row in v4_result_dataset["models"]}
    result_rows, routes = [], []
    dataset_root = materialization_root / dataset["id"]
    common = {
        "document_vectors": write_array(dataset_root / "document-vectors.f32le", data["documents"], "<f4"),
        "query_vectors": write_array(dataset_root / "query-vectors.f32le", data["queries"][positions], "<f4"),
        "document_id_rank": write_array(dataset_root / "document-id-rank.u32le",
                                          native.lexicographic_ranks(data["document_ids"]), "<u4"),
    }
    for seed in contract["frozen_route"]["seeds"]:
        model_path = model_root / dataset["id"] / f"model-raw_euclidean_mined_pairs-{seed}.npz"
        frozen = frozen_models[(contract["frozen_route"]["treatment"], seed)]
        require(model_path.is_file() and sha256(model_path) == frozen["model_sha256"],
                f"exact-E5 frozen model differs: {dataset['id']} {seed}")
        arrays, _ = v4.base.read_model(model_path)
        document_raw = v4.base.infer(data["documents"], arrays, False)
        threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
        require(numpy.array_equal(threshold, numpy.asarray(frozen["threshold"], dtype=numpy.float32)),
                f"exact-E5 frozen threshold differs: {dataset['id']} {seed}")
        query_logits = v4.base.infer(data["queries"], arrays, False) - threshold
        index = v4.base.alignment.german.direct.build_index(
            document_raw - threshold, data["documents"], contract["frozen_route"]["bits"], 1)
        row, adc = evaluate_seed(data, positions, query_logits, index, oracle, seed, contract)
        row["model_sha256"] = sha256(model_path)
        result_rows.append(row)
        route_root = dataset_root / str(seed)
        routes.append({
            "seed": seed, "model_sha256": row["model_sha256"],
            "adc_positions": write_array(route_root / "adc-positions.u32le", adc, "<u4"),
            "expected": [{
                "adc_limit": limit,
                "exact_sequence_sha256": row[f"exact_e5_on_adc_{limit}_sequence_sha256"],
                "queries": [{"ranked_sha256": query["stages"][f"exact_e5_on_adc_{limit}"]["ranked_sha256"]}
                            for query in row["queries"]],
            } for limit in contract["cascade"]["adc_limits"]],
        })
    report_dataset = {
        "id": dataset["id"], "language": dataset["language"], "document_count": len(data["document_ids"]),
        "configuration_query_count": len(positions), "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"], "rows": result_rows,
    }
    materialized_dataset = {
        "id": dataset["id"], "language": dataset["language"], "document_count": len(data["document_ids"]),
        "query_count": len(positions), "source_query_positions": positions, "common": common, "routes": routes,
    }
    return report_dataset, materialized_dataset


def decide(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    language_rows = []
    for dataset in datasets:
        adc = float(numpy.mean([row["metrics"]["stages"]["adc_only"]["ndcg_at_10"]
                                for row in dataset["rows"]]))
        exact = float(numpy.mean([row["metrics"]["stages"]["exact_e5_on_adc_256"]["ndcg_at_10"]
                                  for row in dataset["rows"]]))
        language_rows.append({"dataset": dataset["id"], "adc_ndcg_at_10": adc,
                              "exact_256_ndcg_at_10": exact, "exact_gain": exact - adc})
    gains = [row["exact_gain"] for row in language_rows]
    rule = contract["decision"]
    removable = (float(numpy.mean(gains))
                 <= rule["maximum_cross_language_mean_ndcg_gain_from_exact_e5_at_256_for_removal"]
                 and max(gains) <= rule["maximum_per_language_ndcg_gain_from_exact_e5_at_256_for_removal"])
    selected_limit = None
    limit_rows = []
    for limit in contract["cascade"]["adc_limits"]:
        losses = []
        for dataset in datasets:
            baseline = float(numpy.mean([
                row["metrics"]["stages"]["exact_e5_on_adc_256"]["ndcg_at_10"]
                for row in dataset["rows"]]))
            current = float(numpy.mean([
                row["metrics"]["stages"][f"exact_e5_on_adc_{limit}"]["ndcg_at_10"]
                for row in dataset["rows"]]))
            losses.append(baseline - current)
        eligible = max(losses) <= rule["maximum_per_language_ndcg_loss_vs_full_exact_e5_at_selected_k"]
        limit_rows.append({"adc_limit": limit, "maximum_per_language_ndcg_loss_vs_exact_256": max(losses),
                           "eligible": eligible})
        if selected_limit is None and eligible:
            selected_limit = limit
    return {"exact_e5_removable": removable, "cross_language_mean_exact_gain": float(numpy.mean(gains)),
            "selected_exact_adc_limit": selected_limit, "limit_comparisons": limit_rows,
            "languages": language_rows}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    v4_contract, v4_result = validate_activation(contract, args)
    roots = {language: {name: getattr(args, f"{language}_{name}_root")
                        for name in ("result", "e5", "input")} for language in ("de", "fr", "ja")}
    require(all(path is not None for values in roots.values() for path in values.values()),
            "exact-E5 dataset roots are required")
    v4_by_id = {row["id"]: row for row in v4_contract["datasets"]}
    result_by_id = {row["id"]: row for row in v4_result["datasets"]}
    datasets, materialized = [], []
    for dataset in contract["datasets"]:
        report, payload = dataset_run(dataset, v4_by_id[dataset["id"]], roots[dataset["language"]],
                                      result_by_id[dataset["id"]], contract,
                                      args.training_model_root, args.materialization_root)
        datasets.append(report)
        materialized.append(payload)
    report = {
        "schema_version": 1, "family": "neuroute_exact_e5_rerank_quality_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "activation": contract["activation"], "source_files_sha256": source_hashes(),
        "datasets": datasets, "decision": decide(datasets, contract),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(report))
    manifest = {
        "schema_version": 1, "family": "neuroute_exact_e5_rerank_materialization",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
        "quality_result_sha256": sha256(args.output), "source_files_sha256": source_hashes(),
        "native_timing": contract["native_timing"], "datasets": materialized,
    }
    args.materialization_root.mkdir(parents=True, exist_ok=True)
    (args.materialization_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    values = numpy.asarray([7, 2, 99], dtype=numpy.uint32)
    require(sequence_sha256(values) == "1673c447a7acb075da4fcf6fceaae46afa50428aa1b77fdc6a2868c3248120c1",
            "exact-E5 sequence self-test differs")
    documents = numpy.asarray([[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]], dtype=numpy.float32)
    query = numpy.asarray([1.0, 0.0], dtype=numpy.float32)
    scores = documents @ query
    order = numpy.lexsort((numpy.asarray([2, 1, 0]), -scores))
    require(order.tolist() == [0, 1, 2], "exact-E5 ordering self-test differs")
    print("NeuRoute exact-E5 rerank self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-exact-e5-rerank.example.json")
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--v4-result", type=Path)
    parser.add_argument("--v4-evidence", type=Path)
    parser.add_argument("--v4-native-result", type=Path)
    parser.add_argument("--v4-native-materialization", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    for language in ("de", "fr", "ja"):
        for name in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{name}-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = (args.v4_contract, args.v4_result, args.v4_evidence, args.v4_native_result,
                    args.v4_native_materialization, args.training_model_root, args.output,
                    args.materialization_root)
        if any(value is None for value in required):
            parser.error("activation, model, output, and materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-exact-e5-rerank: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
