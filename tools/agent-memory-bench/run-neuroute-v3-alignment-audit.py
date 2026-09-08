#!/usr/bin/env python3
"""Replay frozen v3 models and diagnose loss, probing, and relevance alignment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

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


planner = load("neuroute_v3_alignment_planner", "plan-neuroute-v3-alignment-audit.py")
german = load("neuroute_v3_alignment_german", "run-neuroute-dynamic-false-positive-v3.py")
french = load("neuroute_v3_alignment_french", "run-neuroute-v3-external-confirmation.py")
japanese = load("neuroute_v3_alignment_japanese", "run-neuroute-v3-ja-external-confirmation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-v3-alignment-audit.py", "run-neuroute-v3-alignment-audit.py")
    return {name: sha256(THIS / name) for name in names}


def artifact(path: Path) -> dict[str, numpy.ndarray]:
    names = ("mean", "scale", "weight1", "bias1", "weight2", "bias2", "weight3", "bias3")
    with numpy.load(path, allow_pickle=False) as stored:
        return {name: stored[name] for name in names}


def average_ranks(values: numpy.ndarray) -> numpy.ndarray:
    order = numpy.argsort(values, kind="stable")
    result = numpy.empty(values.size, dtype=numpy.float64)
    start = 0
    while start < order.size:
        stop = start + 1
        while stop < order.size and values[order[stop]] == values[order[start]]:
            stop += 1
        result[order[start:stop]] = (start + stop - 1) / 2.0
        start = stop
    return result


def correlation(left: numpy.ndarray, right: numpy.ndarray) -> dict[str, float]:
    require(left.shape == right.shape and left.ndim == 1 and left.size > 1, "alignment correlation inputs differ")
    return {
        "pearson": float(numpy.corrcoef(left, right)[0, 1]),
        "spearman": float(numpy.corrcoef(average_ranks(left), average_ranks(right))[0, 1]),
    }


def module_for(language: str) -> tuple[Any, Path, Callable[..., Any], Callable[..., Any]]:
    if language == "de":
        return german, THIS / "neuroute-dynamic-false-positive-v3.example.json", german.load_data, german.partitions
    if language == "fr":
        return french, THIS / "neuroute-v3-external-confirmation.example.json", french.base.load_data, french.partitions
    require(language == "ja", "alignment audit language differs")
    return japanese, THIS / "neuroute-v3-ja-external-confirmation.example.json", japanese.base.load_data, japanese.partitions


def dynamic_models(result: dict[str, Any], dataset: dict[str, Any], result_root: Path,
                   data: dict[str, Any]) -> list[dict[str, Any]]:
    expected_seeds = [2026082701, 2026082702, 2026082703]
    rows = [row for row in result.get("models", []) if row.get("treatment") == "dynamic_false_positive"]
    require(sorted(row.get("seed") for row in rows) == expected_seeds and all(row.get("bits") == 12 for row in rows),
            f"alignment audit model matrix differs: {dataset['id']}")
    output = []
    for row in sorted(rows, key=lambda value: value["seed"]):
        path = result_root / f"model-dynamic_false_positive-{row['seed']}.npz"
        require(path.is_file() and sha256(path) == row["model_sha256"],
                f"alignment audit model bytes differ: {dataset['id']} seed {row['seed']}")
        arrays = artifact(path)
        document_raw = german.v2.infer(data["documents"], arrays)
        threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
        require(numpy.array_equal(threshold, numpy.asarray(row["threshold"], dtype=numpy.float32)),
                f"alignment audit threshold differs: {dataset['id']} seed {row['seed']}")
        output.append({"seed": row["seed"], "artifact": arrays, "threshold": threshold,
                       "document_raw": document_raw, "document_logits": document_raw - threshold,
                       "query_raw": german.v2.infer(data["queries"], arrays),
                       "model_sha256": row["model_sha256"]})
    return output


def relevant_positions(data: dict[str, Any], query_position: int) -> tuple[numpy.ndarray, dict[int, int]]:
    positions = data["_document_position_by_id"]
    values = {positions[document_id]: int(grade)
              for document_id, grade in data["qrels"][data["query_ids"][query_position]].items()
              if grade > 0}
    return numpy.asarray(sorted(values), dtype=numpy.int32), values


def pipeline(data: dict[str, Any], query_position: int, query_logits: numpy.ndarray,
             index: dict[str, Any], oracle: numpy.ndarray, learned: bool,
             probes: int, mass_target: float) -> dict[str, Any]:
    if learned:
        requested = german.diagnostic.addresses(query_logits[query_position], 12, probes)
    else:
        requested = german.direct.confidence_addresses(query_logits[query_position], 8, probes)
    candidates, accepted = german.direct.candidate_union(
        requested, index["postings"], len(data["document_ids"]), mass_target)
    _, adc, ranked = german.direct.cascade(data, query_position, candidates)
    relevant, _ = relevant_positions(data, query_position)
    denominator = max(1, relevant.size)
    return {
        "candidates": candidates,
        "adc": adc,
        "ranked": ranked,
        "accepted_probes": len(accepted),
        "raw_e5_survival": float(numpy.isin(oracle[query_position], candidates).sum()) / oracle.shape[1],
        "adc_e5_survival": float(numpy.isin(oracle[query_position], adc).sum()) / oracle.shape[1],
        "raw_qrels_recall": float(numpy.isin(relevant, candidates).sum()) / denominator,
        "adc_qrels_recall": float(numpy.isin(relevant, adc).sum()) / denominator,
        "ndcg_at_10": german.quality.dcg_at_10(
            data["document_ids"][ranked], data["qrels"][data["query_ids"][query_position]]),
    }


def exclusive_accumulator() -> dict[str, float | int]:
    return {"candidate_observations": 0, "e5_top10_observations": 0, "qrels_relevant_observations": 0,
            "relevance_grade_sum": 0.0, "e5_score_sum": 0.0}


def add_exclusive(accumulator: dict[str, float | int], values: numpy.ndarray, data: dict[str, Any],
                  query_position: int, oracle: numpy.ndarray) -> None:
    if not values.size:
        return
    _, grades = relevant_positions(data, query_position)
    accumulator["candidate_observations"] += int(values.size)
    accumulator["e5_top10_observations"] += int(numpy.isin(values, oracle[query_position]).sum())
    relevant = [grades[int(value)] for value in values if int(value) in grades]
    accumulator["qrels_relevant_observations"] += len(relevant)
    accumulator["relevance_grade_sum"] += float(sum(relevant))
    accumulator["e5_score_sum"] += float((data["documents"][values] @ data["queries"][query_position]).sum())


def finish_exclusive(value: dict[str, float | int]) -> dict[str, float | int]:
    count = int(value["candidate_observations"])
    relevant = int(value["qrels_relevant_observations"])
    return {**value,
            "e5_top10_rate": float(value["e5_top10_observations"]) / max(1, count),
            "qrels_relevant_rate": relevant / max(1, count),
            "mean_relevance_all": float(value["relevance_grade_sum"]) / max(1, count),
            "mean_relevance_when_relevant": float(value["relevance_grade_sum"]) / max(1, relevant),
            "mean_e5_score": float(value["e5_score_sum"]) / max(1, count)}


def pair_geometry(source: numpy.ndarray, raw: numpy.ndarray, left: numpy.ndarray,
                  right: numpy.ndarray) -> dict[str, Any]:
    source_cosine = numpy.sum(source[left] * source[right], axis=1, dtype=numpy.float64)
    normalized = raw / numpy.maximum(numpy.linalg.norm(raw, axis=1, keepdims=True), 1.0e-12)
    latent_cosine = numpy.sum(normalized[left] * normalized[right], axis=1, dtype=numpy.float64)
    source_distance = numpy.sqrt(numpy.maximum(0.0, 2.0 - 2.0 * source_cosine))
    raw_distance = numpy.linalg.norm(raw[left] - raw[right], axis=1).astype(numpy.float64)
    return {"pairs": int(left.size), "normalized_cosine_vs_e5_cosine": correlation(latent_cosine, source_cosine),
            "raw_euclidean_vs_e5_euclidean": correlation(raw_distance, source_distance)}


def geometry_diagnostics(data: dict[str, Any], model: dict[str, Any], positions: list[int],
                         oracle: numpy.ndarray, sample_count: int, neighbour_count: int) -> dict[str, Any]:
    query_left = numpy.repeat(numpy.asarray(positions, dtype=numpy.int32), oracle.shape[1])
    query_right = oracle[numpy.asarray(positions, dtype=numpy.int32)].reshape(-1)
    query_source = numpy.concatenate((data["queries"], data["documents"]), axis=0)
    query_raw = numpy.concatenate((model["query_raw"], model["document_raw"]), axis=0)
    query_pairs = pair_geometry(query_source, query_raw, query_left,
                                query_right + len(data["query_ids"]))

    order = sorted(range(len(data["document_ids"])),
                   key=lambda index: (hashlib.sha256(str(data["document_ids"][index]).encode("utf-8")).digest(),
                                      str(data["document_ids"][index])))[:sample_count]
    sampled = numpy.asarray(order, dtype=numpy.int32)
    neighbours, _ = german.v2.nearest(data["documents"][sampled], data["documents"], neighbour_count, sampled)
    document_left = numpy.repeat(sampled, neighbour_count)
    document_right = neighbours.reshape(-1)
    return {"query_to_document": query_pairs,
            "document_to_document": pair_geometry(data["documents"], model["document_raw"],
                                                    document_left, document_right),
            "document_sample_sha256": hashlib.sha256(sampled.tobytes()).hexdigest()}


def probing_diagnostics(model: dict[str, Any], positions: list[int], oracle: numpy.ndarray,
                        budgets: list[int]) -> dict[str, Any]:
    mismatch = numpy.zeros(12, dtype=numpy.int64)
    observations = numpy.zeros(12, dtype=numpy.int64)
    reachable = {budget: 0 for budget in budgets}
    total_neighbours = 0
    document_codes = german.direct.code_values(model["document_logits"], 12)
    query_logits = model["query_raw"] - model["threshold"]
    for position in positions:
        order = numpy.argsort(numpy.abs(query_logits[position]), kind="stable")
        rank_by_bit = numpy.empty(12, dtype=numpy.int32)
        rank_by_bit[order] = numpy.arange(12, dtype=numpy.int32)
        query_code = int(german.direct.code_values(query_logits[position:position + 1], 12)[0])
        addresses = german.diagnostic.addresses(query_logits[position], 12, 1 << 12)
        address_rank = {address: rank for rank, address in enumerate(addresses)}
        for document_position in oracle[position]:
            differing = query_code ^ int(document_codes[document_position])
            for bit in range(12):
                rank = int(rank_by_bit[bit])
                observations[rank] += 1
                if differing & (1 << bit):
                    mismatch[rank] += 1
            probe_rank = address_rank[int(document_codes[document_position])]
            for budget in budgets:
                reachable[budget] += int(probe_rank < budget)
            total_neighbours += 1
    return {"neighbour_observations": total_neighbours,
            "bit_mismatch_probability_by_query_margin_rank":
                [float(mismatch[index]) / max(1, int(observations[index])) for index in range(12)],
            "exact_address_reachability": {str(budget): reachable[budget] / max(1, total_neighbours)
                                           for budget in budgets}}


def partition_audit(data: dict[str, Any], positions: list[int], models: list[dict[str, Any]],
                    oracle: numpy.ndarray, full_ndcg: numpy.ndarray, contract: dict[str, Any]) -> dict[str, Any]:
    routing = contract["routing"]
    pca_document, pca_artifact = german.direct.document_head(data["documents"])
    pca_query = ((data["queries"] - pca_artifact["document_mean"]) @ pca_artifact["document_projection"]
                 - pca_artifact["document_threshold"]).astype(numpy.float32)
    pca_index = german.direct.build_index(pca_document, data["documents"], routing["pca_bits"],
                                          routing["pca_replication"])
    pca_rows = {position: pipeline(data, position, pca_query, pca_index, oracle, False,
                                   routing["pca_probes"], routing["candidate_mass_target"])
                for position in positions}
    dynamic_by_seed: list[dict[int, dict[str, Any]]] = []
    dynamic_only, pca_only = exclusive_accumulator(), exclusive_accumulator()
    probing, geometry = [], []
    for model in models:
        query_logits = model["query_raw"] - model["threshold"]
        index = german.direct.build_index(model["document_logits"], data["documents"], 12, 1)
        rows = {position: pipeline(data, position, query_logits, index, oracle, True,
                                   routing["learned_probes"], routing["candidate_mass_target"])
                for position in positions}
        dynamic_by_seed.append(rows)
        for position in positions:
            add_exclusive(dynamic_only, numpy.setdiff1d(rows[position]["candidates"],
                                                        pca_rows[position]["candidates"], assume_unique=True),
                          data, position, oracle)
            add_exclusive(pca_only, numpy.setdiff1d(pca_rows[position]["candidates"],
                                                    rows[position]["candidates"], assume_unique=True),
                          data, position, oracle)
        probing.append({"seed": model["seed"], **probing_diagnostics(
            model, positions, oracle, routing["probe_reachability_budgets"])})
        geometry.append({"seed": model["seed"], **geometry_diagnostics(
            data, model, positions, oracle, contract["diagnostics"]["document_sample"],
            contract["diagnostics"]["document_neighbours"])})

    metric_names = ("raw_e5_survival", "adc_e5_survival", "raw_qrels_recall", "adc_qrels_recall", "ndcg_at_10")
    pca_means = {name: float(numpy.mean([pca_rows[position][name] for position in positions])) for name in metric_names}
    dynamic_means = {name: float(numpy.mean([[rows[position][name] for position in positions]
                                             for rows in dynamic_by_seed])) for name in metric_names}
    survival_delta, ndcg_delta = [], []
    per_query = []
    for position in positions:
        dynamic_survival = float(numpy.mean([rows[position]["adc_e5_survival"] for rows in dynamic_by_seed]))
        dynamic_ndcg = float(numpy.mean([rows[position]["ndcg_at_10"] for rows in dynamic_by_seed]))
        survival_delta.append(dynamic_survival - pca_rows[position]["adc_e5_survival"])
        ndcg_delta.append(dynamic_ndcg - pca_rows[position]["ndcg_at_10"])
        relevant, grades = relevant_positions(data, position)
        oracle_grades = [grades.get(int(value), 0) for value in oracle[position]]
        per_query.append({"query_id": data["query_ids"][position],
                          "delta_adc_e5_survival": survival_delta[-1], "delta_ndcg_at_10": ndcg_delta[-1],
                          "e5_top10_qrels_relevant_count": sum(grade > 0 for grade in oracle_grades),
                          "e5_top10_relevance_grade_sum": sum(oracle_grades),
                          "qrels_relevant_count": int(relevant.size)})
    return {"query_count": len(positions), "full_exact_e5_ndcg_at_10": float(numpy.mean(full_ndcg[positions])),
            "dynamic_mean": dynamic_means, "pca": pca_means,
            "delta_e5_survival_vs_delta_ndcg": correlation(numpy.asarray(survival_delta), numpy.asarray(ndcg_delta)),
            "exclusive_candidate_observations": {"dynamic_only": finish_exclusive(dynamic_only),
                                                  "pca_only": finish_exclusive(pca_only)},
            "probing_by_seed": probing, "geometry_by_seed": geometry, "per_query": per_query}


def analyze_dataset(dataset: dict[str, Any], roots: dict[str, Path], contract: dict[str, Any]) -> dict[str, Any]:
    module, old_contract_path, load_data, split_function = module_for(dataset["language"])
    result_path = roots["result"] / "result.json"
    require(result_path.is_file() and sha256(result_path) == dataset["result_sha256"],
            f"alignment audit result bytes differ: {dataset['id']}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    require(result.get("family") == dataset["result_family"]
            and result.get("contract_sha256") == dataset["result_contract_sha256"]
            and sha256(old_contract_path) == dataset["result_contract_sha256"],
            f"alignment audit result binding differs: {dataset['id']}")
    old_contract = module.planner.load_contract(old_contract_path)
    data = load_data(roots["e5"], roots["input"], old_contract)
    require(len(data["document_ids"]) == dataset["documents"] and len(data["query_ids"]) == dataset["queries"],
            f"alignment audit cardinality differs: {dataset['id']}")
    data["_document_position_by_id"] = {str(value): index for index, value in enumerate(data["document_ids"])}
    split = split_function(data["query_ids"], old_contract)
    require(result.get("split") == split, f"alignment audit split differs: {dataset['id']}")
    id_to_position = {value: index for index, value in enumerate(data["query_ids"])}
    partitions = {name: [id_to_position[value] for value in split[f"{name}_query_ids"]]
                  for name in contract["diagnostics"]["partitions"]}
    require(len(partitions["configuration_selection"]) == dataset["configuration_queries"]
            and len(partitions["internal_evaluation"]) == dataset["internal_queries"],
            f"alignment audit partition cardinality differs: {dataset['id']}")
    oracle, full_ndcg = german.direct.exact_oracle(data, contract["diagnostics"]["oracle_k"])
    models = dynamic_models(result, dataset, roots["result"], data)
    return {"id": dataset["id"], "language": dataset["language"], "result_sha256": dataset["result_sha256"],
            "e5_manifest_sha256": data["manifest_sha256"], "input_manifest_sha256": data["input_manifest_sha256"],
            "model_sha256": [row["model_sha256"] for row in models],
            "partitions": {name: partition_audit(data, positions, models, oracle, full_ndcg, contract)
                           for name, positions in partitions.items()}}


def run(contract_path: Path, roots: dict[str, dict[str, Path]], output: Path) -> None:
    contract = planner.load_contract(contract_path)
    datasets = [analyze_dataset(dataset, roots[dataset["language"]], contract)
                for dataset in contract["datasets"]]
    report = {"schema_version": 1, "family": "neuroute_v3_posthoc_alignment_audit_result",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(contract_path),
              "source_files_sha256": source_hashes(), "datasets": datasets}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(report))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-v3-alignment-audit.example.json")
    values = correlation(numpy.asarray([1.0, 2.0, 3.0]), numpy.asarray([3.0, 2.0, 1.0]))
    exclusive = exclusive_accumulator()
    require(values == {"pearson": -1.0, "spearman": -1.0}
            and contract["routing"]["probe_reachability_budgets"][-1] == 512
            and finish_exclusive(exclusive)["qrels_relevant_rate"] == 0.0,
            "alignment audit self-test differs")
    print("NeuRoute v3 alignment audit self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-v3-alignment-audit.example.json")
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        roots = {language: {name: getattr(args, f"{language}_{name}_root")
                            for name in ("result", "e5", "input")}
                 for language in ("de", "fr", "ja")}
        require(args.output is not None and all(path is not None for value in roots.values() for path in value.values()),
                "alignment audit roots are required")
        run(args.contract, roots, args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-v3-alignment-audit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
