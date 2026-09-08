#!/usr/bin/env python3
"""Measure centroid-free ranking objectives on the frozen #176 substrate."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
WIDTH = 8
ADDRESS_COUNT = 1 << WIDTH
REPLICATION = 4
PROBES = 16
MASS_TARGET = 0.1


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("ranking_runner", "run-direct-learned-semantic-address.py")
splitter = load("ranking_splitter", "materialize-direct-semantic-address-splits.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def percentile(values: list[float], fraction: float) -> float:
    require(values and 0.0 <= fraction <= 1.0, "centroid-free ranking percentile differs")
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction))


def save_artifact(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    numpy.savez_compressed(path, metadata_json=numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), **arrays)
    with numpy.load(path, allow_pickle=False) as stored:
        require(json.loads(str(stored["metadata_json"].item())) == metadata, "centroid-free ranking artifact metadata differs")
        for name, value in arrays.items():
            require(numpy.array_equal(stored[name], value), f"centroid-free ranking artifact differs: {name}")


def load_artifact(path: Path) -> tuple[dict[str, Any], dict[str, numpy.ndarray]]:
    with numpy.load(path, allow_pickle=False) as stored:
        metadata = json.loads(str(stored["metadata_json"].item()))
        arrays = {name: numpy.asarray(stored[name]) for name in stored.files if name != "metadata_json"}
    return metadata, arrays


def infer(queries: numpy.ndarray, artifact: dict[str, numpy.ndarray]) -> numpy.ndarray:
    normalized = (queries - artifact["query_mean"]) / artifact["query_scale"]
    hidden = runner.gelu_tanh(normalized @ artifact["weight1"].T + artifact["bias1"])
    return (hidden @ artifact["weight2"].T + artifact["bias2"]).astype(numpy.float32)


def train(queries: numpy.ndarray, targets: numpy.ndarray, weights: numpy.ndarray | None,
          seed: int, config: dict[str, Any], family: str) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for the centroid-free ranking run") from error
    require(queries.shape == (324, 384) and targets.shape[0] == 324 and targets.shape[1] in (ADDRESS_COUNT, ADDRESS_COUNT - 1),
            "centroid-free ranking training tensors differ")
    if weights is not None:
        require(weights.shape == targets.shape, "centroid-free ranking training weights differ")
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(config["torch_threads"])
    mean = queries.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale = queries.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale[scale < 1.0e-6] = 1.0
    features = torch.from_numpy(((queries - mean) / scale).astype(numpy.float32))
    labels = torch.from_numpy(targets.astype(numpy.float32))
    torch_weights = torch.from_numpy(weights.astype(numpy.float32)) if weights is not None else None
    model = torch.nn.Sequential(torch.nn.Linear(384, config["hidden_dimensions"]), torch.nn.GELU(approximate="tanh"), torch.nn.Linear(config["hidden_dimensions"], targets.shape[1]))
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"])
    generator = torch.Generator().manual_seed(seed + 1)
    losses: list[float] = []
    started = time.perf_counter()
    for _ in range(config["epochs"]):
        order = torch.randperm(features.shape[0], generator=generator)
        total = 0.0
        for start in range(0, features.shape[0], config["batch_size"]):
            chosen = order[start:start + config["batch_size"]]
            logits = model(features[chosen])
            optimizer.zero_grad(set_to_none=True)
            if family == "address_multilabel_bce":
                loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, labels[chosen])
            elif family == "address_listwise_marginal_gain":
                loss = -(labels[chosen] * torch.nn.functional.log_softmax(logits, dim=1)).sum(dim=1).mean()
            else:
                require(family == "semantic_tree_beam" and torch_weights is not None, "centroid-free ranking loss family differs")
                per_node = torch.nn.functional.binary_cross_entropy_with_logits(logits, labels[chosen], reduction="none")
                weight = torch_weights[chosen]
                loss = (per_node * weight).sum() / torch.clamp(weight.sum(), min=1.0)
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * chosen.numel()
        losses.append(total / features.shape[0])
    first, second = model[0], model[2]
    artifact = {"query_mean": mean, "query_scale": scale,
                "weight1": first.weight.detach().numpy().astype(numpy.float32), "bias1": first.bias.detach().numpy().astype(numpy.float32),
                "weight2": second.weight.detach().numpy().astype(numpy.float32), "bias2": second.bias.detach().numpy().astype(numpy.float32)}
    replay = infer(queries, artifact)
    with torch.no_grad():
        expected = model(features).numpy()
    require(numpy.allclose(replay, expected, rtol=2.0e-5, atol=2.0e-5), "centroid-free ranking serialization replay differs")
    return artifact, {"family": family, "seed": seed, "initial_loss": losses[0], "final_loss": losses[-1],
                       "training_seconds": time.perf_counter() - started, **config}


def document_address_sets(index: dict[str, Any], document_count: int) -> list[set[int]]:
    result = [set() for _ in range(document_count)]
    for address, posting in index["postings"].items():
        for document in posting:
            result[int(document)].add(address)
    return result


def rank_weighted_addresses(oracle: numpy.ndarray, address_sets: list[set[int]]) -> numpy.ndarray:
    target = numpy.zeros(ADDRESS_COUNT, dtype=numpy.float32)
    for rank, document in enumerate(oracle):
        weight = float(len(oracle) - rank) / len(oracle)
        for address in address_sets[int(document)]:
            target[address] += weight
    maximum = float(target.max())
    if maximum:
        target /= maximum
    return target


def greedy_marginal_target(oracle: numpy.ndarray, address_sets: list[set[int]], postings: dict[int, numpy.ndarray]) -> numpy.ndarray:
    oracle_weights = {int(document): float(len(oracle) - rank) / len(oracle) for rank, document in enumerate(oracle)}
    candidates = sorted(set().union(*(address_sets[int(document)] for document in oracle)))
    marked = numpy.zeros(25000, dtype=numpy.bool_)
    covered: set[int] = set()
    ranks: list[int] = []
    for _ in range(PROBES):
        best: tuple[float, float, int] | None = None
        for address in candidates:
            if address in ranks:
                continue
            fresh = postings[address][~marked[postings[address]]]
            if not fresh.size:
                continue
            gain = sum(weight for document, weight in oracle_weights.items() if document not in covered and document in set(fresh.tolist()))
            if gain <= 0.0:
                continue
            score = gain / math.sqrt(float(fresh.size))
            candidate = (score, gain, -address)
            if best is None or candidate > best:
                best = candidate
        if best is None:
            break
        address = -best[2]
        fresh = postings[address][~marked[postings[address]]]
        marked[fresh] = True
        covered.update(int(document) for document in fresh if int(document) in oracle_weights)
        ranks.append(address)
    target = numpy.zeros(ADDRESS_COUNT, dtype=numpy.float32)
    for position, address in enumerate(ranks):
        target[address] = 1.0 / (position + 1)
    total = float(target.sum())
    require(total > 0.0, "centroid-free ranking greedy target is empty")
    return target / total


def build_semantic_tree(centroids: dict[int, numpy.ndarray]) -> numpy.ndarray:
    def split(addresses: list[int]) -> list[int]:
        if len(addresses) == 1:
            return addresses
        matrix = numpy.stack([centroids[address] for address in addresses]).astype(numpy.float64)
        centered = matrix - matrix.mean(axis=0)
        _, _, right = numpy.linalg.svd(centered, full_matrices=False)
        projection = centered @ right[0]
        ordered = [address for _, address in sorted(zip(projection.tolist(), addresses), key=lambda pair: (pair[0], pair[1]))]
        middle = len(ordered) // 2
        return split(ordered[:middle]) + split(ordered[middle:])
    leaves = numpy.asarray(split(sorted(centroids)), dtype=numpy.int32)
    require(leaves.shape == (ADDRESS_COUNT,) and len(set(leaves.tolist())) == ADDRESS_COUNT,
            "centroid-free semantic tree leaves differ")
    return leaves


def tree_targets(address_targets: numpy.ndarray, leaves: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    targets = numpy.zeros((address_targets.shape[0], ADDRESS_COUNT - 1), dtype=numpy.float32)
    weights = numpy.zeros_like(targets)
    values = address_targets[:, leaves]
    for node in range(ADDRESS_COUNT - 1):
        depth = int(math.floor(math.log2(node + 1)))
        index_at_depth = node - ((1 << depth) - 1)
        span = ADDRESS_COUNT >> depth
        begin = index_at_depth * span
        middle = begin + span // 2
        end = begin + span
        left = values[:, begin:middle].sum(axis=1)
        right = values[:, middle:end].sum(axis=1)
        total = left + right
        nonzero = total > 0.0
        targets[nonzero, node] = left[nonzero] / total[nonzero]
        weights[:, node] = total
    return targets, weights


def tree_beam_addresses(logits: numpy.ndarray, leaves: numpy.ndarray) -> list[int]:
    require(logits.shape == (ADDRESS_COUNT - 1,), "centroid-free semantic tree logits differ")
    heap: list[tuple[float, int, int]] = [(0.0, 0, 0)]
    result: list[int] = []
    while heap and len(result) < PROBES:
        negative_score, node, depth = heapq.heappop(heap)
        if depth == WIDTH:
            result.append(int(leaves[node - (ADDRESS_COUNT - 1)]))
            continue
        probability = 1.0 / (1.0 + math.exp(-float(logits[node])))
        left_score = negative_score - math.log(max(probability, 1.0e-12))
        right_score = negative_score - math.log(max(1.0 - probability, 1.0e-12))
        heapq.heappush(heap, (left_score, 2 * node + 1, depth + 1))
        heapq.heappush(heap, (right_score, 2 * node + 2, depth + 1))
    require(len(result) == PROBES and len(set(result)) == PROBES, "centroid-free semantic tree beam differs")
    return result


def ordered_addresses(treatment: str, logits: numpy.ndarray, symmetric_logits: numpy.ndarray,
                      leaves: numpy.ndarray | None) -> list[int]:
    if treatment == "symmetric_document_head_control":
        return runner.confidence_addresses(symmetric_logits, WIDTH, PROBES)
    if treatment == "bitwise_bce_v1_control":
        return runner.confidence_addresses(logits, WIDTH, PROBES)
    if treatment in ("address_multilabel_bce", "address_listwise_marginal_gain"):
        addresses = numpy.arange(ADDRESS_COUNT, dtype=numpy.int32)
        return [int(addresses[position]) for position in numpy.lexsort((addresses, -logits))[:PROBES]]
    require(treatment == "semantic_tree_beam" and leaves is not None, "centroid-free ranking treatment differs")
    return tree_beam_addresses(logits, leaves)


def evaluate(data: dict[str, Any], positions: list[int], treatment: str, logits: numpy.ndarray,
             symmetric_logits: numpy.ndarray, index: dict[str, Any], oracle: numpy.ndarray,
             leaves: numpy.ndarray | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for position in positions:
        requested = ordered_addresses(treatment, logits[position], symmetric_logits[position], leaves)
        candidates, accepted = runner.candidate_union(requested, index["postings"], len(data["document_ids"]), MASS_TARGET)
        hamming, adc, ranked = runner.cascade(data, position, candidates)
        raw = float(numpy.isin(oracle[position], candidates).sum()) / oracle.shape[1]
        hamming_survival = float(numpy.isin(oracle[position], hamming).sum()) / oracle.shape[1]
        adc_survival = float(numpy.isin(oracle[position], adc).sum()) / oracle.shape[1]
        ndcg = runner.quality.dcg_at_10(data["document_ids"][ranked], data["qrels"][data["query_ids"][position]])
        rows.append({"query_position": position, "query_id": data["query_ids"][position], "requested_addresses": requested,
                     "accepted_addresses": accepted, "candidate_count": int(candidates.size),
                     "e5_oracle_raw_union_survival": raw, "e5_oracle_hamming_survival": hamming_survival,
                     "e5_oracle_survival_after_adc": adc_survival, "reranked_ndcg_at_10": ndcg})
    metric_names = ("candidate_count", "e5_oracle_raw_union_survival", "e5_oracle_hamming_survival",
                    "e5_oracle_survival_after_adc", "reranked_ndcg_at_10")
    metrics = {name: float(numpy.mean([row[name] for row in rows], dtype=numpy.float64)) for name in metric_names}
    metrics["candidate_fraction"] = metrics.pop("candidate_count") / len(data["document_ids"])
    metrics["candidate_count_p95"] = percentile([float(row["candidate_count"]) for row in rows], 0.95)
    metrics["query_count"] = len(rows)
    return metrics, rows


def bootstrap_delta(reference: list[dict[str, Any]], candidate: list[dict[str, Any]], metric: str) -> dict[str, float]:
    require([row["query_id"] for row in reference] == [row["query_id"] for row in candidate], "centroid-free paired rows differ")
    delta = numpy.asarray([candidate[position][metric] - reference[position][metric] for position in range(len(reference))], dtype=numpy.float64)
    generator = numpy.random.default_rng(2026082706)
    samples = numpy.asarray([delta[generator.integers(0, len(delta), len(delta))].mean() for _ in range(2000)], dtype=numpy.float64)
    return {"mean": float(delta.mean()), "bootstrap_p025": float(numpy.quantile(samples, 0.025)), "bootstrap_p975": float(numpy.quantile(samples, 0.975))}


def validate_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    require(contract.get("schema_version") == 1 and contract.get("family") == "centroid_free_semantic_address_ranking_v1"
            and contract.get("substrate") == {"semantic_prefix_bits": 8, "document_replication": 4, "max_address_lookups": 16, "candidate_mass_target": 0.1}
            and contract.get("treatments") == ["symmetric_document_head_control", "bitwise_bce_v1_control", "address_multilabel_bce", "address_listwise_marginal_gain", "semantic_tree_beam"],
            "centroid-free ranking contract differs")
    return contract


def run(contract_path: Path, baseline_root: Path, e5_root: Path, input_root: Path, output_root: Path) -> None:
    contract = validate_contract(contract_path)
    data = runner.load_inputs(e5_root, input_root)
    oracle, _ = runner.exact_oracle(data, contract["cascade"]["oracle_k"])
    baseline_result = json.loads((baseline_root / "result.json").read_text(encoding="utf-8"))
    baseline_metadata, baseline_artifact = load_artifact(baseline_root / "model.npz")
    require(baseline_result.get("model_sha256") == sha256(baseline_root / "model.npz")
            and baseline_metadata.get("contract_sha256") == baseline_result.get("contract_sha256")
            and baseline_result.get("e5_manifest_sha256") == data["manifest_sha256"]
            and baseline_result.get("input_manifest_sha256") == data["input_manifest_sha256"],
            "centroid-free ranking baseline binding differs")
    source_contract = json.loads((THIS / "direct-learned-semantic-address.example.json").read_text(encoding="utf-8"))
    split_ids = splitter.materialize(data["query_ids"], source_contract)
    position = {query_id: value for value, query_id in enumerate(data["query_ids"])}
    partitions = {name: [position[query_id] for query_id in split_ids[f"{name}_query_ids"]]
                  for name in ("training", "configuration_selection", "internal_evaluation")}
    document_logits, document_artifact = runner.document_head(data["documents"])
    for name, value in document_artifact.items():
        require(numpy.array_equal(value, baseline_artifact[name]), f"centroid-free ranking document artifact differs: {name}")
    index = runner.build_index(document_logits, data["documents"], WIDTH, REPLICATION)
    address_sets = document_address_sets(index, len(data["document_ids"]))
    multi_targets = numpy.stack([rank_weighted_addresses(oracle[item], address_sets) for item in partitions["training"]])
    listwise_targets = numpy.stack([greedy_marginal_target(oracle[item], address_sets, index["postings"]) for item in partitions["training"]])
    leaves = build_semantic_tree(index["centroids"])
    tree_target, tree_weights = tree_targets(listwise_targets, leaves)
    symmetric_logits = ((data["queries"] - document_artifact["document_mean"]) @ document_artifact["document_projection"] - document_artifact["document_threshold"]).astype(numpy.float32)
    bitwise_logits = runner.infer_mlp(data["queries"], baseline_artifact)
    output_root.mkdir(parents=True, exist_ok=True)
    models: dict[tuple[str, int], dict[str, numpy.ndarray]] = {}
    training: list[dict[str, Any]] = []
    for treatment, target, weights in (("address_multilabel_bce", multi_targets, None),
                                       ("address_listwise_marginal_gain", listwise_targets, None),
                                       ("semantic_tree_beam", tree_target, tree_weights)):
        for seed in contract["training"]["seeds"]:
            artifact, metadata = train(data["queries"][partitions["training"]], target, weights, seed, contract["training"], treatment)
            path = output_root / f"model-{treatment}-{seed}.npz"
            save_artifact(path, artifact, {"schema_version": 1, "family": "centroid_free_semantic_address_ranking_model_v1",
                                           "contract_sha256": sha256(contract_path), "treatment": treatment, "training": metadata,
                                           "tree_leaves": leaves.tolist() if treatment == "semantic_tree_beam" else None})
            models[(treatment, seed)] = artifact
            training.append({"treatment": treatment, "seed": seed, "model_sha256": sha256(path), **metadata})
    report: dict[str, Any] = {"schema_version": 1, "family": "centroid_free_semantic_address_ranking_result_v1",
                              "contract_sha256": sha256(contract_path), "baseline_result_sha256": sha256(baseline_root / "result.json"),
                              "baseline_model_sha256": sha256(baseline_root / "model.npz"), "e5_manifest_sha256": data["manifest_sha256"],
                              "input_manifest_sha256": data["input_manifest_sha256"], "tree_leaves": leaves.tolist(),
                              "split_ids": split_ids, "training": training, "partitions": {}}
    for partition_name in ("configuration_selection", "internal_evaluation"):
        results: dict[str, list[dict[str, Any]]] = {name: [] for name in contract["treatments"]}
        audits: dict[tuple[str, int], list[dict[str, Any]]] = {}
        baseline_metrics, baseline_audit = evaluate(data, partitions[partition_name], "symmetric_document_head_control", symmetric_logits, symmetric_logits, index, oracle, None)
        results["symmetric_document_head_control"].append({"seed": None, **baseline_metrics})
        audits[("symmetric_document_head_control", 0)] = baseline_audit
        bitwise_metrics, bitwise_audit = evaluate(data, partitions[partition_name], "bitwise_bce_v1_control", bitwise_logits, symmetric_logits, index, oracle, None)
        results["bitwise_bce_v1_control"].append({"seed": 20260825, **bitwise_metrics})
        audits[("bitwise_bce_v1_control", 20260825)] = bitwise_audit
        for treatment in ("address_multilabel_bce", "address_listwise_marginal_gain", "semantic_tree_beam"):
            for seed in contract["training"]["seeds"]:
                metrics, audit = evaluate(data, partitions[partition_name], treatment, infer(data["queries"], models[(treatment, seed)]), symmetric_logits, index, oracle, leaves if treatment == "semantic_tree_beam" else None)
                results[treatment].append({"seed": seed, **metrics})
                audits[(treatment, seed)] = audit
        summaries: list[dict[str, Any]] = []
        reference = baseline_audit
        for treatment, rows in results.items():
            metric_names = ("candidate_fraction", "e5_oracle_raw_union_survival", "e5_oracle_hamming_survival", "e5_oracle_survival_after_adc", "reranked_ndcg_at_10")
            summary = {"treatment": treatment, "seed_count": len(rows), "metrics": {name: {"mean": float(numpy.mean([row[name] for row in rows], dtype=numpy.float64)), "p05": percentile([row[name] for row in rows], 0.05), "p95": percentile([row[name] for row in rows], 0.95)} for name in metric_names}}
            if treatment != "symmetric_document_head_control":
                candidate_audit = audits[(treatment, rows[0]["seed"] or 0)] if len(rows) == 1 else [
                    {key: float(numpy.mean([audits[(treatment, row["seed"])][position][key] for row in rows], dtype=numpy.float64)) if key in ("e5_oracle_survival_after_adc", "reranked_ndcg_at_10") else audits[(treatment, rows[0]["seed"])][position][key]
                     for key in audits[(treatment, rows[0]["seed"])][position]}
                    for position in range(len(reference))]
                summary["paired_bootstrap_vs_symmetric"] = {name: bootstrap_delta(reference, candidate_audit, name) for name in ("e5_oracle_survival_after_adc", "reranked_ndcg_at_10")}
            summaries.append(summary)
        for (treatment, seed), audit in audits.items():
            suffix = "baseline" if seed == 0 else str(seed)
            (output_root / f"{partition_name}-audit-{treatment}-{suffix}.json").write_bytes(canonical({"schema_version": 1, "treatment": treatment, "seed": None if seed == 0 else seed, "rows": audit}))
        report["partitions"][partition_name] = {"per_seed": results, "summaries": summaries}
    (output_root / "result.json").write_bytes(canonical(report))


def self_test() -> None:
    require(len(set(range(ADDRESS_COUNT))) == ADDRESS_COUNT, "centroid-free ranking address count differs")
    leaves = numpy.arange(ADDRESS_COUNT, dtype=numpy.int32)
    logits = numpy.zeros(ADDRESS_COUNT - 1, dtype=numpy.float32)
    addresses = tree_beam_addresses(logits, leaves)
    require(len(addresses) == PROBES and len(set(addresses)) == PROBES, "centroid-free semantic tree self-test differs")
    target = numpy.zeros((1, ADDRESS_COUNT), dtype=numpy.float32)
    target[0, 3] = 1.0
    labels, weights = tree_targets(target, leaves)
    require(labels.shape == weights.shape == (1, ADDRESS_COUNT - 1) and weights[0, 0] == 1.0,
            "centroid-free tree target differs")
    print("centroid-free semantic address ranking self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "centroid-free-semantic-address-ranking.example.json")
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
        print(f"run-centroid-free-semantic-address-ranking: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
