#!/usr/bin/env python3
"""Execute the bounded NeuRoute-inspired shared semantic-address v2 study."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import importlib.util
import itertools
import json
import sys
import time
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


runner = load("neuroute_v2_runner", "run-direct-learned-semantic-address.py")
splitter = load("neuroute_v2_splitter", "materialize-direct-semantic-address-splits.py")
planner = load("neuroute_v2_planner", "plan-neuroute-inspired-semantic-address-v2.py")
normalization_audit = load("neuroute_v2_normalization_audit", "audit-shared-learned-semantic-address-normalization.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def save(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    numpy.savez_compressed(path, metadata_json=numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), **arrays)
    with numpy.load(path, allow_pickle=False) as stored:
        require(json.loads(str(stored["metadata_json"].item())) == metadata, "NeuRoute-inspired model metadata differs")
        for name, values in arrays.items():
            require(numpy.array_equal(stored[name], values), f"NeuRoute-inspired model array differs: {name}")


def infer(vectors: numpy.ndarray, artifact: dict[str, numpy.ndarray]) -> numpy.ndarray:
    normalized = (vectors - artifact["mean"]) / artifact["scale"]
    first = numpy.maximum(normalized @ artifact["weight1"].T + artifact["bias1"], 0.0)
    second = numpy.maximum(first @ artifact["weight2"].T + artifact["bias2"], 0.0)
    return (second @ artifact["weight3"].T + artifact["bias3"]).astype(numpy.float32)


def nearest(source: numpy.ndarray, documents: numpy.ndarray, count: int, self_positions: numpy.ndarray | None = None) -> tuple[numpy.ndarray, numpy.ndarray]:
    require(source.ndim == documents.ndim == 2 and source.shape[1] == documents.shape[1] and 0 < count < documents.shape[0],
            "NeuRoute-inspired source-neighbour inputs differ")
    result = numpy.empty((source.shape[0], count), dtype=numpy.int32)
    similarities = numpy.empty((source.shape[0], count), dtype=numpy.float32)
    for start in range(0, source.shape[0], 128):
        stop = min(start + 128, source.shape[0])
        scores = source[start:stop] @ documents.T
        if self_positions is not None:
            rows = numpy.arange(stop - start)
            scores[rows, self_positions[start:stop]] = -numpy.inf
        candidates = numpy.argpartition(-scores, count - 1, axis=1)[:, :count]
        for local, values in enumerate(candidates):
            order = numpy.lexsort((values, -scores[local, values]))
            result[start + local] = values[order]
            similarities[start + local] = scores[local, values[order]]
    return result, similarities


def train(documents: numpy.ndarray, queries: numpy.ndarray, document_neighbours: numpy.ndarray,
          document_similarities: numpy.ndarray, query_neighbours: numpy.ndarray,
          query_similarities: numpy.ndarray, training_positions: numpy.ndarray,
          bits: int, seed: int, loss_name: str, contract: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for the NeuRoute-inspired v2 run") from error
    encoder, mining, losses = contract["encoder"], contract["pair_mining"], contract["losses"]
    loss = losses[loss_name]
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(encoder["torch_threads"])
    mean = documents.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale = documents.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale[scale < 1.0e-6] = 1.0
    document_features = torch.from_numpy(((documents - mean) / scale).astype(numpy.float32))
    query_features = torch.from_numpy(((queries - mean) / scale).astype(numpy.float32))
    model = torch.nn.Sequential(torch.nn.Linear(384, 96), torch.nn.ReLU(), torch.nn.Linear(96, 64), torch.nn.ReLU(), torch.nn.Linear(64, bits))
    optimizer = torch.optim.AdamW(model.parameters(), lr=encoder["learning_rate"], weight_decay=encoder["weight_decay"])
    generator = torch.Generator().manual_seed(seed + 1)
    losses_by_epoch: list[float] = []
    started = time.perf_counter()
    for epoch in range(encoder["epochs"]):
        order = torch.randperm(documents.shape[0], generator=generator)
        query_order = training_positions[torch.randperm(training_positions.size, generator=generator).numpy()]
        total = 0.0
        for batch_number, start in enumerate(range(0, documents.shape[0], encoder["batch_size"])):
            chosen = order[start:start + encoder["batch_size"]]
            chosen_numpy = chosen.numpy()
            document_positive = document_neighbours[chosen_numpy, epoch % mining["document_neighbours"]]
            query_start = (batch_number * mining["training_query_batch_size"]) % query_order.size
            query_selected = numpy.take(query_order, numpy.arange(query_start, query_start + mining["training_query_batch_size"]) % query_order.size)
            query_positive = query_neighbours[query_selected, epoch % mining["training_query_document_neighbours"]]
            raw = model(torch.cat((document_features[chosen], document_features[document_positive], query_features[query_selected], document_features[query_positive])))
            latent = torch.nn.functional.normalize(raw, dim=1)
            document_count = chosen.numel()
            query_count = query_selected.size
            document_similarity = torch.from_numpy(document_similarities[chosen_numpy, epoch % mining["document_neighbours"]])
            query_similarity = torch.from_numpy(query_similarities[query_selected, epoch % mining["training_query_document_neighbours"]])
            learned_document = (latent[:document_count] * latent[document_count:2 * document_count]).sum(dim=1)
            learned_query = (latent[2 * document_count:2 * document_count + query_count] * latent[2 * document_count + query_count:]).sum(dim=1)
            geometry = torch.nn.functional.mse_loss(learned_document, document_similarity) + torch.nn.functional.mse_loss(learned_query, query_similarity)
            standard_deviation = raw.std(dim=0)
            variance = torch.relu(loss["minimum_latent_standard_deviation"] - standard_deviation).mean()
            centered = raw - raw.mean(dim=0, keepdim=True)
            normalized = centered / (standard_deviation.unsqueeze(0) + 1.0e-6)
            covariance = normalized.T @ normalized / max(1, normalized.shape[0] - 1)
            covariance = covariance - torch.diag(torch.diag(covariance))
            covariance_penalty = (covariance ** 2).mean()
            objective = loss["geometry"] * geometry + loss["variance"] * variance + loss["covariance"] * covariance_penalty
            optimizer.zero_grad(set_to_none=True)
            objective.backward()
            optimizer.step()
            total += float(objective.detach()) * document_count
        losses_by_epoch.append(total / documents.shape[0])
    first, third, fifth = model[0], model[2], model[4]
    artifact = {"mean": mean, "scale": scale, "weight1": first.weight.detach().numpy().astype(numpy.float32), "bias1": first.bias.detach().numpy().astype(numpy.float32),
                "weight2": third.weight.detach().numpy().astype(numpy.float32), "bias2": third.bias.detach().numpy().astype(numpy.float32),
                "weight3": fifth.weight.detach().numpy().astype(numpy.float32), "bias3": fifth.bias.detach().numpy().astype(numpy.float32)}
    with torch.no_grad():
        expected = model(document_features).numpy()
    require(numpy.allclose(infer(documents, artifact), expected, rtol=2.0e-5, atol=2.0e-5), "NeuRoute-inspired model serialization replay differs")
    return artifact, {"bits": bits, "seed": seed, "loss": loss_name, "initial_loss": losses_by_epoch[0], "final_loss": losses_by_epoch[-1],
                      "training_seconds": time.perf_counter() - started, "torch_version": torch.__version__}


def logit_addresses(logits: numpy.ndarray, width: int, count: int) -> list[int]:
    require(logits.shape == (width,) and 0 < count <= 256, "NeuRoute-inspired logit probe differs")
    base = int(runner.code_values(logits[None, :], width)[0])
    order = numpy.argsort(numpy.abs(logits), kind="stable")
    margins = numpy.abs(logits[order])
    masks = [0]
    if count > 1:
        heap: list[tuple[float, int, int]] = [(float(margins[0]), 1 << int(order[0]), 0)]
        while heap and len(masks) < count:
            cost, mask, last = heapq.heappop(heap)
            masks.append(mask)
            following = last + 1
            if following < width:
                next_bit = 1 << int(order[following])
                replaced = mask ^ (1 << int(order[last])) ^ next_bit
                heapq.heappush(heap, (cost - float(margins[last]) + float(margins[following]), replaced, following))
                heapq.heappush(heap, (cost + float(margins[following]), mask | next_bit, following))
    return [base ^ mask for mask in masks]


def hamming_addresses(logits: numpy.ndarray, width: int, count: int) -> list[int]:
    base = int(runner.code_values(logits[None, :], width)[0])
    result = [base]
    for distance in range(1, width + 1):
        masks = sorted(sum(1 << bit for bit in bits) for bits in itertools.combinations(range(width), distance))
        for mask in masks:
            result.append(base ^ mask)
            if len(result) == count:
                return result
    return result


def diagnostics(document_logits: numpy.ndarray, query_logits: numpy.ndarray, width: int, index: dict[str, Any], document_neighbours: numpy.ndarray,
                document_similarities: numpy.ndarray, query_neighbours: numpy.ndarray) -> dict[str, Any]:
    codes = runner.code_values(document_logits, width)
    _, sizes = numpy.unique(codes, return_counts=True)
    probabilities = sizes / float(sizes.sum())
    sorted_sizes = numpy.sort(sizes.astype(numpy.float64))
    gini = float(numpy.dot(2.0 * numpy.arange(1, sizes.size + 1) - sizes.size - 1, sorted_sizes) / (sizes.size * sorted_sizes.sum()))
    normalized = document_logits / numpy.maximum(numpy.linalg.norm(document_logits, axis=1, keepdims=True), 1.0e-12)
    learned = (normalized * normalized[document_neighbours[:, 0]]).sum(axis=1)
    correlation = float(numpy.corrcoef(document_similarities[:, 0], learned)[0, 1])
    centered = document_logits - document_logits.mean(axis=0, keepdims=True)
    covariance = numpy.corrcoef(centered, rowvar=False)
    off_diagonal = covariance - numpy.eye(width, dtype=numpy.float64)
    query_codes = runner.code_values(query_logits, width)
    hamming = numpy.asarray([int(int(query_codes[position]) ^ int(codes[query_neighbours[position, 0]])).bit_count() for position in range(query_codes.size)], dtype=numpy.int32)
    return {"latent_pairwise_cosine_correlation": correlation, "per_bit_marginals": (document_logits >= 0.0).mean(axis=0, dtype=numpy.float64).tolist(),
            "off_diagonal_latent_covariance_mean_absolute": float(numpy.abs(off_diagonal).sum() / max(1, width * (width - 1))),
            "off_diagonal_latent_covariance_maximum_absolute": float(numpy.abs(off_diagonal).max()), "occupied_address_count": int(sizes.size),
            "address_entropy_bits": float(-(probabilities * numpy.log2(probabilities)).sum()), "bucket_size_p50": float(numpy.quantile(sizes, 0.5)),
            "bucket_size_p95": float(numpy.quantile(sizes, 0.95)), "bucket_size_maximum": int(sizes.max()), "bucket_size_gini": gini,
            "query_nearest_document_code_hamming_p50": float(numpy.quantile(hamming, 0.5)), "query_nearest_document_code_hamming_p95": float(numpy.quantile(hamming, 0.95)),
            "posting_ids": index["posting_ids"], "posting_payload_bytes": index["payload_bytes"]}


def evaluate(data: dict[str, Any], positions: list[int], logits: numpy.ndarray, index: dict[str, Any], oracle: numpy.ndarray, full_ndcg: numpy.ndarray,
             width: int, probes: int, order_name: str, mass: float, retain: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    for position in positions:
        requested = logit_addresses(logits[position, :width], width, probes) if order_name == "independent_logit_best_first_v2" else hamming_addresses(logits[position, :width], width, probes)
        candidates, accepted = runner.candidate_union(requested, index["postings"], len(data["document_ids"]), mass)
        hamming, adc, ranked = runner.cascade(data, position, candidates)
        rows.append({"query_position": position, "query_id": data["query_ids"][position], "candidate_count": int(candidates.size), "probes_requested": probes,
                     "probes_accepted": len(accepted), "e5_oracle_raw_union_survival": float(numpy.isin(oracle[position], candidates).sum()) / oracle.shape[1],
                     "e5_oracle_hamming_survival": float(numpy.isin(oracle[position], hamming).sum()) / oracle.shape[1],
                     "e5_oracle_survival_after_adc": float(numpy.isin(oracle[position], adc).sum()) / oracle.shape[1],
                     "reranked_ndcg_at_10": runner.quality.dcg_at_10(data["document_ids"][ranked], data["qrels"][data["query_ids"][position]]),
                     "requested_addresses": requested if retain else None, "accepted_addresses": accepted if retain else None,
                     "candidate_positions": candidates.tolist() if retain else None, "hamming_positions": hamming.tolist() if retain else None,
                     "adc_positions": adc.tolist() if retain else None, "reranked_positions": ranked.tolist() if retain else None})
    def average(name: str) -> float:
        return float(numpy.mean([row[name] for row in rows], dtype=numpy.float64))
    return {"query_count": len(rows), "candidate_fraction": average("candidate_count") / len(data["document_ids"]), "candidate_count_p95": runner.percentile([float(row["candidate_count"]) for row in rows], 0.95),
            "probes_accepted_mean": average("probes_accepted"), "e5_oracle_raw_union_survival": average("e5_oracle_raw_union_survival"),
            "e5_oracle_hamming_survival": average("e5_oracle_hamming_survival"), "e5_oracle_survival_after_adc": average("e5_oracle_survival_after_adc"),
            "reranked_ndcg_at_10": average("reranked_ndcg_at_10"), "full_e5_ndcg_at_10": float(numpy.mean(full_ndcg[positions], dtype=numpy.float64))}, rows


def aggregate(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    return (float(numpy.mean([row["e5_oracle_survival_after_adc"] for row in rows], dtype=numpy.float64)),
            float(numpy.mean([row["reranked_ndcg_at_10"] for row in rows], dtype=numpy.float64)),
            float(numpy.mean([row["candidate_fraction"] for row in rows], dtype=numpy.float64)))


def bootstrap(control: list[dict[str, Any]], treatments: list[list[dict[str, Any]]], gate: dict[str, Any]) -> dict[str, Any]:
    require(all([row["query_id"] for row in rows] == [row["query_id"] for row in control] for rows in treatments), "NeuRoute-inspired bootstrap query order differs")
    control_survival = numpy.asarray([row["e5_oracle_survival_after_adc"] for row in control], dtype=numpy.float64)
    control_ndcg = numpy.asarray([row["reranked_ndcg_at_10"] for row in control], dtype=numpy.float64)
    survival = numpy.mean(numpy.asarray([[row["e5_oracle_survival_after_adc"] for row in rows] for rows in treatments], dtype=numpy.float64), axis=0)
    ndcg = numpy.mean(numpy.asarray([[row["reranked_ndcg_at_10"] for row in rows] for rows in treatments], dtype=numpy.float64), axis=0)
    generator = numpy.random.default_rng(gate["bootstrap_seed"])
    samples = generator.integers(0, control_survival.size, size=(gate["bootstrap_resamples"], control_survival.size))
    survival_delta = survival - control_survival
    ndcg_delta = ndcg - control_ndcg
    survival_means = survival_delta[samples].mean(axis=1)
    ndcg_means = ndcg_delta[samples].mean(axis=1)
    survival_mean = float(survival_delta.mean())
    ndcg_mean = float(ndcg_delta.mean())
    return {"adc_survival_delta_mean": survival_mean, "adc_survival_delta_ci95": [float(numpy.quantile(survival_means, 0.025)), float(numpy.quantile(survival_means, 0.975))],
            "ndcg_at_10_delta_mean": ndcg_mean, "ndcg_at_10_delta_ci95": [float(numpy.quantile(ndcg_means, 0.025)), float(numpy.quantile(ndcg_means, 0.975))],
            "passed": survival_mean >= gate["minimum_adc_survival_absolute_gain"] and float(numpy.quantile(survival_means, 0.025)) > 0.0
                      and ndcg_mean >= -gate["maximum_ndcg_at_10_absolute_loss"] and float(numpy.quantile(ndcg_means, 0.025)) >= -gate["maximum_ndcg_at_10_absolute_loss"]}


def run(contract_path: Path, e5_root: Path, input_root: Path, output_root: Path) -> None:
    contract = planner.load_contract(contract_path)
    data = runner.load_inputs(e5_root, input_root)
    documents, queries = data["documents"], data["queries"]
    document_norms = normalization_audit.summarize(documents, 1.0e-5)
    query_norms = normalization_audit.summarize(queries, 1.0e-5)
    source_contract = json.loads((THIS / "direct-learned-semantic-address.example.json").read_text(encoding="utf-8"))
    split_ids = splitter.materialize(data["query_ids"], source_contract)
    positions = {value: index for index, value in enumerate(data["query_ids"])}
    partitions = {name: [positions[value] for value in split_ids[f"{name}_query_ids"]] for name in ("training", "configuration_selection", "internal_evaluation")}
    train_positions = numpy.asarray(partitions["training"], dtype=numpy.int32)
    document_neighbours, document_similarities = nearest(documents, documents, contract["pair_mining"]["document_neighbours"], numpy.arange(documents.shape[0], dtype=numpy.int32))
    query_neighbours, query_similarities = nearest(queries, documents, contract["pair_mining"]["training_query_document_neighbours"])
    oracle, full_ndcg = runner.exact_oracle(data, contract["cascade"]["oracle_k"])
    output_root.mkdir(parents=True, exist_ok=True)
    models, selection_rows, internal, no_covariance_internal = [], [], [], []
    runtime: dict[tuple[str, int, int], dict[str, Any]] = {}
    for planned in planner.plan(contract):
        artifact, training = train(documents, queries, document_neighbours, document_similarities, query_neighbours, query_similarities,
                                   train_positions, planned["bits"], planned["seed"], planned["loss"], contract)
        key = (planned["loss"], planned["bits"], planned["seed"])
        path = output_root / f"model-{planned['loss']}-{planned['bits']}-bit-{planned['seed']}.npz"
        save(path, artifact, {"schema_version": 1, "family": "neuroute_inspired_semantic_address_model_v2", "contract_sha256": sha256(contract_path), "training": training})
        document_logits = infer(documents, artifact)
        query_logits = infer(queries, artifact)
        threshold = numpy.median(document_logits, axis=0).astype(numpy.float32)
        document_logits = document_logits - threshold
        query_logits = query_logits - threshold
        index = runner.build_index(document_logits, documents, planned["bits"], 1)
        runtime[key] = {"query_logits": query_logits, "index": index, "diagnostics": diagnostics(document_logits, query_logits, planned["bits"], index, document_neighbours, document_similarities, query_neighbours)}
        models.append({"loss": planned["loss"], "bits": planned["bits"], "seed": planned["seed"], "model_sha256": sha256(path), "training": training, "threshold": threshold.tolist(), "diagnostics": runtime[key]["diagnostics"]})
        for order_name in contract["routing"]["query_orders"]:
            for probes in contract["routing"]["maximum_probes"]:
                metrics, _ = evaluate(data, partitions["configuration_selection"], query_logits, index, oracle, full_ndcg, planned["bits"], probes, order_name, contract["routing"]["candidate_mass_target"], False)
                selection_rows.append({"loss": planned["loss"], "bits": planned["bits"], "seed": planned["seed"], "query_order": order_name, "maximum_probes": probes, **metrics})
    full_groups: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
    for row in selection_rows:
        if row["loss"] == "full": full_groups.setdefault((row["bits"], row["query_order"], row["maximum_probes"]), []).append(row)
    eligible = [(key, values) for key, values in full_groups.items() if len(values) == len(contract["encoder"]["seeds"])
                and max(row["candidate_fraction"] for row in values) <= contract["routing"]["candidate_mass_target"]]
    require(eligible, "NeuRoute-inspired v2 has no eligible full selection configuration")
    selected_key, selected_rows = max(eligible, key=lambda item: (aggregate(item[1])[0], aggregate(item[1])[1], -aggregate(item[1])[2], -item[0][0], -item[0][2]))
    selected = {"loss": "full", "bits": selected_key[0], "query_order": selected_key[1], "maximum_probes": selected_key[2], "selection_mean": {"adc_survival": aggregate(selected_rows)[0], "ndcg_at_10": aggregate(selected_rows)[1], "candidate_fraction": aggregate(selected_rows)[2]}}
    for seed in contract["encoder"]["seeds"]:
        key = ("full", selected_key[0], seed)
        metrics, rows = evaluate(data, partitions["internal_evaluation"], runtime[key]["query_logits"], runtime[key]["index"], oracle, full_ndcg, selected_key[0], selected_key[2], selected_key[1], contract["routing"]["candidate_mass_target"], True)
        internal.append({"loss": "full", "bits": selected_key[0], "seed": seed, "query_order": selected_key[1], "maximum_probes": selected_key[2], "metrics": metrics, "rows": rows})
    ablation_groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in selection_rows:
        if row["loss"] == "no_covariance_ablation":
            ablation_groups.setdefault((row["query_order"], row["maximum_probes"]), []).append(row)
    ablation_eligible = [(key, values) for key, values in ablation_groups.items() if len(values) == len(contract["encoder"]["seeds"])
                         and max(row["candidate_fraction"] for row in values) <= contract["routing"]["candidate_mass_target"]]
    require(ablation_eligible, "NeuRoute-inspired v2 has no eligible no-covariance ablation configuration")
    ablation_key, ablation_rows = max(ablation_eligible, key=lambda item: (aggregate(item[1])[0], aggregate(item[1])[1], -aggregate(item[1])[2], -item[0][1]))
    ablation_selected = {"loss": "no_covariance_ablation", "bits": 16, "query_order": ablation_key[0], "maximum_probes": ablation_key[1],
                         "selection_mean": {"adc_survival": aggregate(ablation_rows)[0], "ndcg_at_10": aggregate(ablation_rows)[1], "candidate_fraction": aggregate(ablation_rows)[2]}}
    for seed in contract["encoder"]["seeds"]:
        key = ("no_covariance_ablation", 16, seed)
        metrics, rows = evaluate(data, partitions["internal_evaluation"], runtime[key]["query_logits"], runtime[key]["index"], oracle, full_ndcg, 16, ablation_key[1], ablation_key[0], contract["routing"]["candidate_mass_target"], True)
        no_covariance_internal.append({"loss": "no_covariance_ablation", "bits": 16, "seed": seed, "query_order": ablation_key[0], "maximum_probes": ablation_key[1], "metrics": metrics, "rows": rows})
    control_document_logits, control_artifact = runner.document_head(documents)
    control_query_logits = ((queries - control_artifact["document_mean"]) @ control_artifact["document_projection"] - control_artifact["document_threshold"]).astype(numpy.float32)
    control_index = runner.build_index(control_document_logits, documents, 8, 4)
    control_metrics, control_rows = runner.evaluate(data, partitions["internal_evaluation"], control_query_logits, control_index, oracle, full_ndcg,
                                                    "symmetric_document_head_control", 8, 16, 0.1, False, True)
    comparison = bootstrap(control_rows, [row["rows"] for row in internal], contract["success_gate"])
    report = {"schema_version": 1, "family": "neuroute_inspired_semantic_address_result_v2", "contract_sha256": sha256(contract_path),
              "e5_manifest_sha256": data["manifest_sha256"], "input_manifest_sha256": data["input_manifest_sha256"], "split_ids": split_ids,
              "source_l2_audit": {"documents": document_norms, "queries": query_norms, "passed": True},
              "pair_mining": {"document_neighbour_sha256": hashlib.sha256(document_neighbours.tobytes()).hexdigest(), "query_neighbour_sha256": hashlib.sha256(query_neighbours.tobytes()).hexdigest()},
              "models": models, "selection_rows": selection_rows, "selected_full_configuration": selected,
              "internal_full": internal, "selected_no_covariance_ablation": ablation_selected, "internal_no_covariance_ablation": no_covariance_internal,
              "symmetric_control_internal": {"metrics": control_metrics, "rows": control_rows}, "success_gate": comparison}
    (output_root / "result.json").write_bytes(canonical(report))


def self_test() -> None:
    values = numpy.asarray([0.1, -0.2, 0.3], dtype=numpy.float32)
    require(len(logit_addresses(values, 3, 8)) == 8 and len(set(logit_addresses(values, 3, 8))) == 8, "NeuRoute-inspired logit order differs")
    require(len(hamming_addresses(values, 3, 8)) == 8 and len(set(hamming_addresses(values, 3, 8))) == 8, "NeuRoute-inspired Hamming order differs")
    print("NeuRoute-inspired semantic-address v2 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-inspired-semantic-address-v2.example.json")
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if any(value is None for value in (args.e5_root, args.input_root, args.output_root)):
            parser.error("--e5-root, --input-root, and --output-root are required")
        run(args.contract, args.e5_root, args.input_root, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-inspired-semantic-address-v2: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
