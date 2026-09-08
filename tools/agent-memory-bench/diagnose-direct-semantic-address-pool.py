#!/usr/bin/env python3
"""Diagnose whether the #176 confidence pool is better than matched pools."""

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
POOL_SIZE = 64
RANDOM_SEEDS = tuple(range(2026082601, 2026082609))


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("direct_address_pool_runner", "run-direct-learned-semantic-address.py")
splitter = load("direct_address_pool_splitter", "materialize-direct-semantic-address-splits.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def percentile(values: list[float], fraction: float) -> float:
    require(values and 0.0 <= fraction <= 1.0, "direct semantic address pool percentile differs")
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction))


def load_model(path: Path) -> tuple[dict[str, Any], dict[str, numpy.ndarray]]:
    with numpy.load(path, allow_pickle=False) as stored:
        metadata = json.loads(str(stored["metadata_json"].item()))
        arrays = {name: numpy.asarray(stored[name]) for name in stored.files if name != "metadata_json"}
    require(metadata.get("schema_version") == 1
            and metadata.get("family") == "direct_learned_semantic_address_model_v1",
            "direct semantic address pool model identity differs")
    required = {"query_mean", "query_scale", "weight1", "bias1", "weight2", "bias2"}
    require(required <= arrays.keys(), "direct semantic address pool model arrays differ")
    return metadata, arrays


def random_pool(query_id: str, width: int, seed: int) -> list[int]:
    require(width == 8, "direct semantic address random pool width differs")
    pairs = []
    for address in range(1 << width):
        payload = f"direct-semantic-address-pool-v1\\0{seed}\\0{query_id}\\0{address}".encode("utf-8")
        pairs.append((hashlib.sha256(payload).digest(), address))
    pairs.sort()
    return [address for _, address in pairs[:POOL_SIZE]]


def exact_centroid_addresses(index: dict[str, Any], query: numpy.ndarray, probes: int) -> list[int]:
    addresses = index["occupied_addresses"]
    scores = numpy.asarray([index["centroids"][int(address)] @ query for address in addresses], dtype=numpy.float32)
    return [int(addresses[position]) for position in numpy.lexsort((addresses, -scores))[:probes]]


def pool_for(treatment: str, query_id: str, logits: numpy.ndarray, width: int, seed: int | None) -> list[int]:
    if treatment == "learned_confidence_pool":
        return runner.confidence_addresses(logits, width, POOL_SIZE)
    if treatment == "symmetric_confidence_pool":
        return runner.confidence_addresses(logits, width, POOL_SIZE)
    require(treatment == "deterministic_random_pool" and seed is not None,
            "direct semantic address pool treatment differs")
    return random_pool(query_id, width, seed)


def evaluate_pool(data: dict[str, Any], query_positions: list[int], logits: numpy.ndarray,
                  index: dict[str, Any], oracle: numpy.ndarray, full_ndcg: numpy.ndarray,
                  treatment: str, seed: int | None, probes: int, mass_target: float,
                  retain_audit: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_survivals: list[float] = []
    adc_survivals: list[float] = []
    ndcgs: list[float] = []
    candidate_counts: list[float] = []
    pool_exact_centroid_coverages: list[float] = []
    pool_oracle_address_coverages: list[float] = []
    audits: list[dict[str, Any]] = []
    document_address_map = [set() for _ in range(len(data["document_ids"]))]
    for address, posting in index["postings"].items():
        for document in posting:
            document_address_map[int(document)].add(address)
    for position in query_positions:
        pool = pool_for(treatment, data["query_ids"][position], logits[position], 8, seed)
        exact = exact_centroid_addresses(index, data["queries"][position], probes)
        ordered_pool = [address for address in pool if address in index["centroids"]]
        ordered_pool.sort(key=lambda address: (-float(index["centroids"][address] @ data["queries"][position]), address))
        requested = ordered_pool[:probes]
        candidates, accepted = runner.candidate_union(requested, index["postings"], len(data["document_ids"]), mass_target)
        _, adc, ranked = runner.cascade(data, position, candidates)
        raw_survival = float(numpy.isin(oracle[position], candidates).sum()) / oracle.shape[1]
        adc_survival = float(numpy.isin(oracle[position], adc).sum()) / oracle.shape[1]
        ndcg = runner.quality.dcg_at_10(data["document_ids"][ranked], data["qrels"][data["query_ids"][position]])
        oracle_addresses = set()
        for document in oracle[position]:
            oracle_addresses.update(document_address_map[int(document)])
        pool_set = set(pool)
        exact_coverage = float(len(pool_set.intersection(exact))) / probes
        oracle_coverage = float(len(pool_set.intersection(oracle_addresses))) / max(1, len(oracle_addresses))
        raw_survivals.append(raw_survival)
        adc_survivals.append(adc_survival)
        ndcgs.append(ndcg)
        candidate_counts.append(float(candidates.size))
        pool_exact_centroid_coverages.append(exact_coverage)
        pool_oracle_address_coverages.append(oracle_coverage)
        if retain_audit:
            audits.append({
                "query_position": position,
                "query_id": data["query_ids"][position],
                "pool_addresses": pool,
                "exact_centroid_top_addresses": exact,
                "requested_addresses": requested,
                "accepted_addresses": accepted,
                "candidate_positions": candidates.tolist(),
                "adc_positions": adc.tolist(),
                "reranked_positions": ranked.tolist(),
                "pool_exact_centroid_top_address_coverage": exact_coverage,
                "pool_oracle_address_coverage": oracle_coverage,
                "e5_oracle_raw_union_survival": raw_survival,
                "e5_oracle_survival_after_adc": adc_survival,
                "reranked_ndcg_at_10": ndcg,
                "full_e5_ndcg_at_10": float(full_ndcg[position]),
            })
    return {
        "treatment": treatment,
        "random_seed": seed,
        "query_count": len(query_positions),
        "pool_size": POOL_SIZE,
        "candidate_fraction": float(numpy.mean(candidate_counts, dtype=numpy.float64)) / len(data["document_ids"]),
        "candidate_count_p95": percentile(candidate_counts, 0.95),
        "pool_exact_centroid_top_address_coverage": float(numpy.mean(pool_exact_centroid_coverages, dtype=numpy.float64)),
        "pool_oracle_address_coverage": float(numpy.mean(pool_oracle_address_coverages, dtype=numpy.float64)),
        "e5_oracle_raw_union_survival": float(numpy.mean(raw_survivals, dtype=numpy.float64)),
        "e5_oracle_survival_after_adc": float(numpy.mean(adc_survivals, dtype=numpy.float64)),
        "reranked_ndcg_at_10": float(numpy.mean(ndcgs, dtype=numpy.float64)),
    }, audits


def summarize_random(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = ("candidate_fraction", "pool_exact_centroid_top_address_coverage", "pool_oracle_address_coverage",
               "e5_oracle_raw_union_survival", "e5_oracle_survival_after_adc", "reranked_ndcg_at_10")
    return {
        "treatment": "deterministic_random_pool_summary",
        "replicate_count": len(rows),
        "metrics": {
            name: {
                "mean": float(numpy.mean([row[name] for row in rows], dtype=numpy.float64)),
                "p05": percentile([row[name] for row in rows], 0.05),
                "p95": percentile([row[name] for row in rows], 0.95),
            }
            for name in metrics
        },
    }


def run(contract_path: Path, baseline_root: Path, e5_root: Path, input_root: Path, output_root: Path) -> None:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(contract == {"schema_version": 1, "family": "direct_semantic_address_pool_diagnostic_v1",
                         "baseline_commit": "ba3345914c7594f92ec91b31db36b0f405a0da05", "pool_size": 64,
                         "random_seeds": list(RANDOM_SEEDS)}, "direct semantic address pool contract differs")
    result_path = baseline_root / "result.json"
    model_path = baseline_root / "model.npz"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    metadata, artifact = load_model(model_path)
    require(result.get("family") == "direct_learned_semantic_address_result_v1"
            and result.get("model_sha256") == sha256(model_path)
            and metadata.get("contract_sha256") == result.get("contract_sha256"),
            "direct semantic address pool baseline binding differs")
    selected = result.get("selected_headline")
    require(isinstance(selected, dict)
            and selected.get("semantic_prefix_bits") == 8
            and selected.get("query_probes") == 16
            and selected.get("document_replication") == 4
            and selected.get("candidate_mass_target") == 0.1,
            "direct semantic address pool baseline headline differs")
    data = runner.load_inputs(e5_root, input_root)
    require(result.get("e5_manifest_sha256") == data["manifest_sha256"]
            and result.get("input_manifest_sha256") == data["input_manifest_sha256"],
            "direct semantic address pool frozen roots differ")
    oracle, full_ndcg = runner.exact_oracle(data, 10)
    split_ids = splitter.materialize(data["query_ids"], json.loads((THIS / "direct-learned-semantic-address.example.json").read_text(encoding="utf-8")))
    positions = {query_id: position for position, query_id in enumerate(data["query_ids"])}
    partitions = {name: [positions[query_id] for query_id in split_ids[f"{name}_query_ids"]]
                  for name in ("configuration_selection", "internal_evaluation")}
    document_logits, document_artifact = runner.document_head(data["documents"])
    for name, value in document_artifact.items():
        require(numpy.array_equal(value, artifact[name]), f"direct semantic address pool document artifact differs: {name}")
    learned_logits = runner.infer_mlp(data["queries"], artifact)
    symmetric_logits = ((data["queries"] - document_artifact["document_mean"]) @ document_artifact["document_projection"] - document_artifact["document_threshold"]).astype(numpy.float32)
    index = runner.build_index(document_logits, data["documents"], 8, 4)
    output_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "family": "direct_semantic_address_pool_diagnostic_result_v1",
        "contract_sha256": sha256(contract_path),
        "baseline_result_sha256": sha256(result_path),
        "baseline_model_sha256": sha256(model_path),
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "partitions": {},
    }
    for partition_name, query_positions in partitions.items():
        rows: list[dict[str, Any]] = []
        for treatment, logits in (("learned_confidence_pool", learned_logits), ("symmetric_confidence_pool", symmetric_logits)):
            row, audit = evaluate_pool(data, query_positions, logits, index, oracle, full_ndcg, treatment, None, 16, 0.1, True)
            rows.append(row)
            (output_root / f"{partition_name}-audit-{treatment}.json").write_bytes(canonical({"schema_version": 1, "treatment": treatment, "rows": audit}))
        random_rows: list[dict[str, Any]] = []
        for seed in RANDOM_SEEDS:
            row, audit = evaluate_pool(data, query_positions, learned_logits, index, oracle, full_ndcg, "deterministic_random_pool", seed, 16, 0.1, True)
            random_rows.append(row)
            (output_root / f"{partition_name}-audit-deterministic-random-pool-{seed}.json").write_bytes(canonical({"schema_version": 1, "treatment": "deterministic_random_pool", "seed": seed, "rows": audit}))
        report["partitions"][partition_name] = {"matched_pool_controls": rows, "random_pool_replicates": random_rows,
                                                   "random_pool_summary": summarize_random(random_rows)}
    (output_root / "pool-diagnostic.json").write_bytes(canonical(report))


def self_test() -> None:
    first = random_pool("es:test#0", 8, RANDOM_SEEDS[0])
    second = random_pool("es:test#0", 8, RANDOM_SEEDS[0])
    require(first == second and len(first) == len(set(first)) == POOL_SIZE,
            "direct semantic address random pool differs")
    require(random_pool("es:test#0", 8, RANDOM_SEEDS[1]) != first,
            "direct semantic address random pool seed differs")
    print("direct semantic address pool diagnostic self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "direct-semantic-address-pool-diagnostic.example.json")
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.baseline_root, args.e5_root, args.input_root, args.output_root)):
            parser.error("--baseline-root, --e5-root, --input-root, and --output-root are required")
        run(args.contract, args.baseline_root, args.e5_root, args.input_root, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"diagnose-direct-semantic-address-pool: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
