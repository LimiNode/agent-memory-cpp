#!/usr/bin/env python3
"""Train and evaluate the frozen relevance-aware NeuRoute v4 matrix."""

from __future__ import annotations

import argparse
import hashlib
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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_relevance_aware_v4_planner", "plan-neuroute-relevance-aware-v4.py")
base = load("neuroute_relevance_aware_v4_training_base", "run-neuroute-training-sanity.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    result = base.source_hashes()
    for name in ("plan-neuroute-relevance-aware-v4.py", "run-neuroute-relevance-aware-v4.py"):
        result[name] = sha256(THIS / name)
    return dict(sorted(result.items()))


def validate_activation(contract: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    activation = contract["activation"]
    paths = {
        "training_sanity_result_sha256": args.training_result,
        "training_sanity_evidence_sha256": args.training_evidence,
        "native_cost_result_sha256": args.native_result,
        "native_cost_evidence_sha256": args.native_evidence,
        "native_cost_materialization_sha256": args.native_materialization,
    }
    for name, path in paths.items():
        require(path.is_file() and sha256(path) == activation[name],
                f"relevance-aware v4 activation bytes differ: {name}")
    training_result = json.loads(args.training_result.read_text(encoding="utf-8"))
    training_evidence = json.loads(args.training_evidence.read_text(encoding="utf-8"))
    require(training_result.get("family") == "neuroute_training_sanity_config_only_result"
            and training_evidence.get("integrity_replay_passed") is True
            and training_evidence.get("result_sha256") == activation["training_sanity_result_sha256"]
            and training_evidence.get("model_set_sha256") == activation["training_sanity_model_set_sha256"],
            "relevance-aware v4 training activation receipt differs")
    native_evidence = json.loads(args.native_evidence.read_text(encoding="utf-8"))
    require(native_evidence.get("family") == "neuroute_native_mdbx_cost_evidence"
            and native_evidence.get("integrity_replay_passed") is True
            and native_evidence.get("report_sha256") == activation["native_cost_result_sha256"]
            and native_evidence.get("materialization_sha256") == activation["native_cost_materialization_sha256"],
            "relevance-aware v4 native activation receipt differs")
    return training_result


def build_relevance_positions(data: dict[str, Any], training_positions: numpy.ndarray) -> list[numpy.ndarray]:
    document_positions = {str(value): index for index, value in enumerate(data["document_ids"])}
    result = [numpy.empty(0, dtype=numpy.int32) for _ in data["query_ids"]]
    for query_position in training_positions:
        query_id = data["query_ids"][query_position]
        ranked = sorted(((int(grade), str(document_id))
                         for document_id, grade in data["qrels"][query_id].items() if grade > 0),
                        key=lambda row: (-row[0], row[1]))
        require(ranked, f"relevance-aware v4 training qrels are empty: {query_id}")
        result[int(query_position)] = numpy.asarray(
            [document_positions[document_id] for _, document_id in ranked], dtype=numpy.int32)
    return result


def sigmoid(values: numpy.ndarray) -> numpy.ndarray:
    clipped = numpy.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + numpy.exp(-clipped))


def mine_relevance_negatives(model: Any, document_features: Any, query_features: Any,
                             training_positions: numpy.ndarray,
                             relevant: list[numpy.ndarray], contract: dict[str, Any]
                             ) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray, str]:
    import torch
    objective = contract["relevance_objective"]
    was_training = model.training
    model.eval()
    with torch.no_grad():
        document_raw = torch.cat([model(document_features[start:start + 1024])
                                  for start in range(0, document_features.shape[0], 1024)]).numpy()
        query_raw = model(query_features[training_positions]).numpy()
    model.train(was_training)
    center = numpy.median(document_raw, axis=0).astype(numpy.float32)
    scale = document_raw.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    scale[scale < 1.0e-4] = 1.0e-4
    documents = (document_raw - center) / scale
    queries = (query_raw - center) / scale
    selected_count = objective["selected_negatives"]
    pool = objective["negative_pool"]
    indices = numpy.full((len(relevant), selected_count), -1, dtype=numpy.int32)
    for local, query_position in enumerate(training_positions):
        query = queries[local]
        costs = numpy.mean(numpy.abs(query)[None, :] * sigmoid(
            -query[None, :] * documents / objective["soft_mismatch_temperature"]), axis=1)
        costs[relevant[int(query_position)]] = numpy.inf
        require(numpy.isfinite(costs).sum() >= pool, "relevance-aware v4 negative pool is too large")
        candidates = numpy.argpartition(costs, pool - 1)[:pool]
        order = numpy.lexsort((candidates, costs[candidates]))
        indices[int(query_position)] = candidates[order[:selected_count]]
    digest = hashlib.sha256()
    for query_position in training_positions:
        digest.update(int(query_position).to_bytes(4, "little"))
        digest.update(indices[int(query_position)].astype("<i4", copy=False).tobytes())
    return indices, center, scale, digest.hexdigest()


def soft_address_cost(query: Any, document: Any, center: Any, scale: Any,
                      temperature: float) -> Any:
    import torch
    query = (query - center) / scale
    document = (document - center) / scale
    return (torch.abs(query) * torch.sigmoid(-query * document / temperature)).mean(dim=1)


def ranking_loss(query: Any, positive: Any, negative: Any, center: Any, scale: Any,
                 contract: dict[str, Any]) -> Any:
    import torch
    objective = contract["relevance_objective"]
    positive_cost = soft_address_cost(query, positive, center, scale,
                                      objective["soft_mismatch_temperature"])
    negative_cost = soft_address_cost(query, negative, center, scale,
                                      objective["soft_mismatch_temperature"])
    value = (positive_cost - negative_cost + objective["pairwise_ranking_margin"])
    return torch.nn.functional.softplus(value / objective["pairwise_ranking_temperature"]).mean() \
        * objective["pairwise_ranking_temperature"]


def train_model(data: dict[str, Any], training_positions: numpy.ndarray,
                document_neighbours: numpy.ndarray, document_similarities: numpy.ndarray,
                query_neighbours: numpy.ndarray, query_similarities: numpy.ndarray,
                relevant: list[numpy.ndarray], treatment: dict[str, Any], seed: int,
                contract: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    import torch
    encoder, mining = contract["encoder"], contract["mining"]
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(encoder["torch_threads"])
    documents, queries = data["documents"], data["queries"]
    mean = documents.mean(axis=0, dtype=numpy.float64).astype(numpy.float32)
    standard = documents.std(axis=0, dtype=numpy.float64).astype(numpy.float32)
    standard[standard < 1.0e-6] = 1.0
    document_features = torch.from_numpy(((documents - mean) / standard).astype(numpy.float32))
    query_features = torch.from_numpy(((queries - mean) / standard).astype(numpy.float32))
    model = base.build_torch_model(False, contract)
    optimizer = torch.optim.AdamW(model.parameters(), lr=encoder["learning_rate"],
                                  weight_decay=encoder["weight_decay"])
    generator = torch.Generator().manual_seed(seed + 1)
    document_false = document_false_similarity = None
    query_false = query_false_similarity = None
    relevance_false = relevance_center = relevance_scale = None
    mining_rows: list[dict[str, Any]] = []
    losses: list[float] = []
    scale_factor = base.distance_scale(contract)
    started = time.perf_counter()
    for epoch in range(encoder["epochs"]):
        if epoch in mining["document_remine_epochs"]:
            document_false, document_false_similarity, digest = base.alignment.german.mine_false_positives(
                model, document_features, documents, mining["latent_neighbour_pool"],
                mining["selected_e5_farthest"])
            mining_rows.append({"epoch": epoch, "kind": "document", "sha256": digest})
        if treatment["query_false_positive_mining"] and epoch in mining["query_remine_epochs"]:
            query_false, query_false_similarity, digest = base.mine_query_false_positives(
                model, document_features, query_features, documents, queries, training_positions,
                mining["latent_neighbour_pool"], mining["selected_e5_farthest"])
            mining_rows.append({"epoch": epoch, "kind": "query_document_e5_far", "sha256": digest})
        if treatment["relevance_ranking"] and epoch in contract["relevance_objective"]["negative_remine_epochs"]:
            relevance_false, relevance_center, relevance_scale, digest = mine_relevance_negatives(
                model, document_features, query_features, training_positions, relevant, contract)
            mining_rows.append({"epoch": epoch, "kind": "query_document_relevance", "sha256": digest})
        model.train()
        order = torch.randperm(documents.shape[0], generator=generator)
        query_order = training_positions[torch.randperm(training_positions.size, generator=generator).numpy()]
        total = 0.0
        for batch_number, start in enumerate(range(0, documents.shape[0], encoder["batch_size"])):
            chosen = order[start:start + encoder["batch_size"]]
            chosen_numpy = chosen.numpy()
            query_start = (batch_number * encoder["training_query_batch_size"]) % query_order.size
            selected_queries = numpy.take(query_order, numpy.arange(
                query_start, query_start + encoder["training_query_batch_size"]) % query_order.size)
            positive_documents = document_neighbours[chosen_numpy, epoch % document_neighbours.shape[1]]
            positive_queries = query_neighbours[selected_queries, epoch % query_neighbours.shape[1]]
            document_left = model(document_features[chosen])
            document_positive = model(document_features[positive_documents])
            query_left = model(query_features[selected_queries])
            query_positive = model(document_features[positive_queries])
            objective = base.pair_loss(document_left, document_positive, torch.from_numpy(
                document_similarities[chosen_numpy, epoch % document_neighbours.shape[1]]), scale_factor)
            objective += contract["distance_objective"]["query_positive_weight"] * base.pair_loss(
                query_left, query_positive,
                torch.from_numpy(query_similarities[selected_queries, epoch % query_neighbours.shape[1]]),
                scale_factor)
            objective += base.diversity_loss(torch.cat((document_left, document_positive,
                                                        query_left, query_positive)), contract)
            if document_false is not None:
                false_documents = document_false[chosen_numpy, epoch % document_false.shape[1]]
                objective += mining["document_false_positive_weight"] * base.pair_loss(
                    document_left, model(document_features[false_documents]), torch.from_numpy(
                        document_false_similarity[chosen_numpy, epoch % document_false.shape[1]]), scale_factor)
            if treatment["query_false_positive_mining"] and query_false is not None:
                false_queries = query_false[selected_queries, epoch % query_false.shape[1]]
                objective += mining["query_false_positive_weight"] * base.pair_loss(
                    query_left, model(document_features[false_queries]), torch.from_numpy(
                        query_false_similarity[selected_queries, epoch % query_false.shape[1]]), scale_factor)
            if treatment["relevance_ranking"]:
                require(relevance_false is not None and relevance_center is not None and relevance_scale is not None,
                        "relevance-aware v4 ranking state is unavailable")
                positive_relevance = numpy.asarray([
                    relevant[int(position)][epoch % relevant[int(position)].size]
                    for position in selected_queries
                ], dtype=numpy.int32)
                negative_relevance = relevance_false[
                    selected_queries, epoch % relevance_false.shape[1]]
                center = torch.from_numpy(relevance_center)
                scale = torch.from_numpy(relevance_scale)
                objective += contract["relevance_objective"]["ranking_weight"] * ranking_loss(
                    query_left, model(document_features[positive_relevance]),
                    model(document_features[negative_relevance]), center, scale, contract)
            optimizer.zero_grad(set_to_none=True)
            objective.backward()
            optimizer.step()
            total += float(objective.detach()) * chosen.numel()
        losses.append(total / documents.shape[0])
    model.eval()
    arrays = base.arrays_from_model(model, mean, standard, False, contract)
    with torch.no_grad():
        expected = model(document_features[:1024]).numpy()
    require(numpy.allclose(base.infer(documents[:1024], arrays, False), expected,
                           rtol=3.0e-5, atol=3.0e-5),
            "relevance-aware v4 serialization replay differs")
    return arrays, {
        "initial_loss": losses[0], "final_loss": losses[-1],
        "training_seconds": time.perf_counter() - started, "mining": mining_rows,
        "training_relevant_pair_count": int(sum(relevant[int(position)].size
                                                  for position in training_positions)),
        "torch_version": torch.__version__,
    }


def update_sequence(digest: Any, query: int, values: numpy.ndarray) -> None:
    digest.update(int(query).to_bytes(4, "little"))
    digest.update(int(values.size).to_bytes(4, "little"))
    digest.update(numpy.asarray(values, dtype="<u4").tobytes())


def sequence_sha256(values: Any) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<u4").tobytes()).hexdigest()


def evaluate(data: dict[str, Any], positions: list[int], query_logits: numpy.ndarray,
             index: dict[str, Any], oracle: numpy.ndarray, probes: int, learned: bool,
             contract: dict[str, Any], qrels: dict[int, numpy.ndarray]) -> dict[str, Any]:
    digests = {name: hashlib.sha256() for name in ("address", "candidate", "hamming", "adc")}
    rows = []
    for local_query, position in enumerate(positions):
        requested = (base.alignment.german.diagnostic.addresses(query_logits[position], 12, probes)
                     if learned else base.alignment.german.direct.confidence_addresses(
                         query_logits[position], 8, probes))
        posting_entries = sum(len(index["postings"].get(address, ())) for address in requested)
        candidates, accepted = base.alignment.german.direct.candidate_union(
            requested, index["postings"], len(data["document_ids"]),
            contract["routing"]["candidate_mass_target"])
        hamming, adc, ranked = base.alignment.german.direct.cascade(data, position, candidates)
        values = {
            "address": numpy.asarray(requested, dtype=numpy.uint32),
            "candidate": candidates, "hamming": hamming, "adc": adc,
        }
        for name, sequence in values.items():
            update_sequence(digests[name], local_query, sequence)
        relevant = qrels[position]
        rows.append({
            "query_id": data["query_ids"][position], "source_query_position": int(position),
            "requested_address_count": len(requested),
            "requested_address_sha256": sequence_sha256(values["address"]),
            "accepted_probes": len(accepted), "posting_entries_requested": posting_entries,
            "candidate_count": int(candidates.size), "candidate_sha256": sequence_sha256(candidates),
            "hamming_count": int(hamming.size), "hamming_sha256": sequence_sha256(hamming),
            "adc_count": int(adc.size), "adc_sha256": sequence_sha256(adc),
            "raw_e5_survival": float(numpy.isin(oracle[position], candidates).sum()) / oracle.shape[1],
            "adc_e5_survival": float(numpy.isin(oracle[position], adc).sum()) / oracle.shape[1],
            "raw_qrels_recall": float(numpy.isin(relevant, candidates).sum()) / max(1, relevant.size),
            "adc_qrels_recall": float(numpy.isin(relevant, adc).sum()) / max(1, relevant.size),
            "ndcg_at_10": base.alignment.german.quality.dcg_at_10(
                data["document_ids"][ranked], data["qrels"][data["query_ids"][position]]),
        })
    names = ("candidate_count", "accepted_probes", "posting_entries_requested",
             "raw_e5_survival", "adc_e5_survival", "raw_qrels_recall",
             "adc_qrels_recall", "ndcg_at_10")
    metrics = {name: float(numpy.mean([row[name] for row in rows], dtype=numpy.float64)) for name in names}
    metrics["candidate_fraction"] = metrics["candidate_count"] / len(data["document_ids"])
    return {
        "metrics": metrics, "rows": rows,
        **{f"{name}_sequence_sha256": digest.hexdigest() for name, digest in digests.items()},
    }


def model_path(output_root: Path, dataset: str, treatment: str, seed: int) -> Path:
    return output_root / dataset / f"model-{treatment}-{seed}.npz"


def dataset_run(dataset: dict[str, Any], roots: dict[str, Path], contract: dict[str, Any],
                contract_path: Path, output_root: Path, training_model_root: Path,
                training_result: dict[str, Any], allow_training: bool) -> dict[str, Any]:
    data, _, split = base.load_dataset(dataset, roots)
    id_to_position = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = numpy.asarray([id_to_position[value] for value in split["training_query_ids"]],
                                       dtype=numpy.int32)
    configuration_positions = [id_to_position[value]
                               for value in split["configuration_selection_query_ids"]]
    relevant = build_relevance_positions(data, training_positions)
    require(sum(relevant[int(position)].size for position in training_positions)
            == dataset["training_relevant_pairs"],
            f"relevance-aware v4 training pair count differs: {dataset['id']}")
    oracle, full_ndcg = base.alignment.german.direct.exact_oracle(data, contract["cascade"]["oracle_k"])
    qrels = base.qrels_positions(data)
    missing = any(treatment["source"] == "train"
                  and not model_path(output_root, dataset["id"], treatment["id"], seed).is_file()
                  for treatment in contract["treatments"] for seed in contract["encoder"]["seeds"])
    document_neighbours = document_similarities = query_neighbours = query_similarities = None
    if missing:
        require(allow_training, f"relevance-aware v4 cached matrix is incomplete: {dataset['id']}")
        document_neighbours, document_similarities = base.alignment.german.v2.nearest(
            data["documents"], data["documents"], 16,
            numpy.arange(len(data["document_ids"]), dtype=numpy.int32))
        query_neighbours, query_similarities = base.alignment.german.v2.nearest(
            data["queries"], data["documents"], 10)
    frozen_dataset = next(row for row in training_result["datasets"] if row["id"] == dataset["id"])
    frozen_models = {(row["treatment"], row["seed"]): row for row in frozen_dataset["models"]}
    models, quality_rows = [], []
    for treatment in contract["treatments"]:
        for seed in contract["encoder"]["seeds"]:
            if treatment["source"] == "reuse_frozen_training_sanity_raw_euclidean_model_bytes":
                path = training_model_root / dataset["id"] / f"model-raw_euclidean_mined_pairs-{seed}.npz"
                frozen = frozen_models[("raw_euclidean_mined_pairs", seed)]
                require(path.is_file() and sha256(path) == frozen["model_sha256"],
                        f"relevance-aware v4 frozen model differs: {dataset['id']} {seed}")
                arrays, source_metadata = base.read_model(path)
                metadata = {"source": treatment["source"], "source_model_sha256": sha256(path),
                            "source_metadata": source_metadata}
                artifact_sha = sha256(path)
            else:
                path = model_path(output_root, dataset["id"], treatment["id"], seed)
                if path.is_file():
                    arrays, metadata = base.read_model(path)
                    require(metadata.get("contract_sha256") == sha256(contract_path)
                            and metadata.get("source_files_sha256") == source_hashes()
                            and metadata.get("dataset") == dataset["id"]
                            and metadata.get("treatment") == treatment["id"]
                            and metadata.get("seed") == seed,
                            f"relevance-aware v4 cached model binding differs: {dataset['id']} {treatment['id']} {seed}")
                else:
                    require(allow_training, f"relevance-aware v4 model is missing in replay-only mode: {path}")
                    require(all(value is not None for value in (document_neighbours, document_similarities,
                                                                 query_neighbours, query_similarities)),
                            "relevance-aware v4 neighbour matrices are unavailable")
                    arrays, training = train_model(
                        data, training_positions, document_neighbours, document_similarities,
                        query_neighbours, query_similarities, relevant, treatment, seed, contract)
                    metadata = {
                        "schema_version": 1, "family": "neuroute_relevance_aware_v4_model",
                        "contract_sha256": sha256(contract_path), "source_files_sha256": source_hashes(),
                        "dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
                        "batch_norm": False, "training": training,
                    }
                    base.save_model(path, arrays, metadata)
                artifact_sha = sha256(path)
            document_raw = base.infer(data["documents"], arrays, False)
            threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
            document_logits = document_raw - threshold
            query_raw = base.infer(data["queries"], arrays, False)
            query_logits = query_raw - threshold
            index = base.alignment.german.direct.build_index(document_logits, data["documents"], 12, 1)
            models.append({
                "dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
                "model_sha256": artifact_sha, "parameter_count": base.parameter_count(False),
                "threshold": threshold.tolist(), "metadata": metadata,
                "probing": base.alignment.probing_diagnostics(
                    {"document_logits": document_logits, "query_raw": query_raw, "threshold": threshold},
                    configuration_positions, oracle, contract["routing"]["probe_budgets"]),
            })
            for probes in contract["routing"]["probe_budgets"]:
                quality_rows.append({
                    "dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
                    "probes": probes,
                    **evaluate(data, configuration_positions, query_logits, index, oracle,
                               probes, True, contract, qrels),
                })
            progress_path = output_root / dataset["id"] / "progress.json"
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_bytes(canonical({
                "schema_version": 1, "family": "neuroute_relevance_aware_v4_progress",
                "contract_sha256": sha256(contract_path),
                "completed_models": [{"treatment": row["treatment"], "seed": row["seed"],
                                      "model_sha256": row["model_sha256"]} for row in models],
            }))
    pca_document, pca_artifact = base.alignment.german.direct.document_head(data["documents"])
    pca_query = ((data["queries"] - pca_artifact["document_mean"]) @ pca_artifact["document_projection"]
                 - pca_artifact["document_threshold"]).astype(numpy.float32)
    pca_index = base.alignment.german.direct.build_index(
        pca_document, data["documents"], contract["routing"]["pca_bits"],
        contract["routing"]["pca_replication"])
    pca = {
        "dataset": dataset["id"], "treatment": "symmetric_pca_control",
        "probes": contract["routing"]["pca_probes"], "parameter_count": 384 * 16,
        **evaluate(data, configuration_positions, pca_query, pca_index, oracle,
                   contract["routing"]["pca_probes"], False, contract, qrels),
    }
    return {
        "id": dataset["id"], "language": dataset["language"],
        "training_query_count": len(training_positions),
        "training_relevant_pair_count": int(sum(relevant[int(position)].size
                                                  for position in training_positions)),
        "configuration_query_count": len(configuration_positions),
        "full_exact_e5_ndcg_at_10": float(numpy.mean(full_ndcg[configuration_positions])),
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "models": models, "quality_rows": quality_rows, "pca": pca,
    }


def seed_mean(dataset: dict[str, Any], treatment: str, probes: int) -> dict[str, float]:
    rows = [row["metrics"] for row in dataset["quality_rows"]
            if row["treatment"] == treatment and row["probes"] == probes]
    require(len(rows) == 3, "relevance-aware v4 seed mean differs")
    return {name: float(numpy.mean([row[name] for row in rows])) for name in rows[0]}


def quality_comparisons(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    control = planner.TREATMENTS[0]
    for probes in contract["routing"]["probe_budgets"]:
        for treatment in planner.TREATMENTS[1:]:
            rows = []
            for dataset in datasets:
                candidate = seed_mean(dataset, treatment, probes)
                baseline = seed_mean(dataset, control, probes)
                rows.append({
                    "dataset": dataset["id"], "candidate": candidate, "control": baseline,
                    "ndcg_delta": candidate["ndcg_at_10"] - baseline["ndcg_at_10"],
                    "adc_e5_survival_delta": candidate["adc_e5_survival"] - baseline["adc_e5_survival"],
                })
            result.append({
                "treatment": treatment, "probes": probes, "languages": rows,
                "cross_language_mean_ndcg_delta": float(numpy.mean([row["ndcg_delta"] for row in rows])),
            })
    return result


def run(contract_path: Path, roots: dict[str, dict[str, Path]], args: argparse.Namespace,
        output_root: Path, report_path: Path, allow_training: bool) -> None:
    contract = planner.load_contract(contract_path)
    training_result = validate_activation(contract, args)
    datasets = [dataset_run(
        dataset, roots[dataset["language"]], contract, contract_path, output_root,
        args.training_model_root, training_result, allow_training) for dataset in contract["datasets"]]
    report = {
        "schema_version": 1, "family": "neuroute_relevance_aware_v4_quality_result",
        "claim_scope": contract["claim_scope"], "contract_sha256": sha256(contract_path),
        "source_files_sha256": source_hashes(), "activation": contract["activation"],
        "datasets": datasets, "quality_comparisons": quality_comparisons(datasets, contract),
        "selection_deferred_until_native_cost": True,
        "confirmation_claims_permitted": False, "scale_transfer_permitted": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical(report))


def self_test() -> None:
    import torch
    contract = planner.load_contract(THIS / "neuroute-relevance-aware-v4.example.json")
    torch.manual_seed(7)
    vectors = numpy.random.default_rng(7).standard_normal((16, 384)).astype(numpy.float32)
    model = base.build_torch_model(False, contract)
    model.eval()
    arrays = base.arrays_from_model(model, numpy.zeros(384, dtype=numpy.float32),
                                    numpy.ones(384, dtype=numpy.float32), False, contract)
    with torch.no_grad():
        expected = model(torch.from_numpy(vectors)).numpy()
    require(numpy.allclose(base.infer(vectors, arrays, False), expected, rtol=3.0e-5, atol=3.0e-5),
            "relevance-aware v4 inference self-test differs")
    query = torch.tensor([[1.0, -1.0]])
    center = torch.zeros(2)
    scale = torch.ones(2)
    positive = torch.tensor([[1.0, -1.0]])
    negative = torch.tensor([[-1.0, 1.0]])
    require(float(soft_address_cost(query, positive, center, scale, 0.5)[0])
            < float(soft_address_cost(query, negative, center, scale, 0.5)[0]),
            "relevance-aware v4 soft address order differs")
    smoke = json.loads(json.dumps(contract))
    smoke["encoder"].update({"epochs": 1, "batch_size": 16,
                             "training_query_batch_size": 6, "torch_threads": 1})
    smoke["mining"].update({"document_remine_epochs": [], "query_remine_epochs": []})
    smoke["relevance_objective"].update({"negative_remine_epochs": [0], "negative_pool": 8,
                                         "selected_negatives": 2})
    generator = numpy.random.default_rng(11)
    documents = generator.standard_normal((32, 384)).astype(numpy.float32)
    documents /= numpy.linalg.norm(documents, axis=1, keepdims=True)
    queries = generator.standard_normal((12, 384)).astype(numpy.float32)
    queries /= numpy.linalg.norm(queries, axis=1, keepdims=True)
    document_neighbours = numpy.asarray([[(row + offset + 1) % 32 for offset in range(16)]
                                         for row in range(32)], dtype=numpy.int32)
    document_similarities = numpy.take_along_axis(documents @ documents.T, document_neighbours, axis=1)
    query_order = numpy.argsort(-(queries @ documents.T), axis=1, kind="stable")[:, :10].astype(numpy.int32)
    query_similarities = numpy.take_along_axis(queries @ documents.T, query_order, axis=1)
    relevant = [numpy.asarray([(row * 3) % 32, (row * 3 + 1) % 32], dtype=numpy.int32)
                for row in range(12)]
    smoke_data = {"documents": documents, "queries": queries}
    for treatment in smoke["treatments"][1:]:
        arrays, training = train_model(
            smoke_data, numpy.arange(6, dtype=numpy.int32), document_neighbours,
            document_similarities, query_order, query_similarities, relevant,
            treatment, 17, smoke)
        require(arrays["weight3"].shape == (12, 64) and training["final_loss"] >= 0.0,
                "relevance-aware v4 training smoke differs")
    require(len(planner.matrix(contract)) == 108 and base.parameter_count(False) == 43948,
            "relevance-aware v4 matrix self-test differs")
    print("NeuRoute relevance-aware v4 runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-relevance-aware-v4.example.json")
    parser.add_argument("--training-result", type=Path)
    parser.add_argument("--training-evidence", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    parser.add_argument("--native-result", type=Path)
    parser.add_argument("--native-evidence", type=Path)
    parser.add_argument("--native-materialization", type=Path)
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = (args.training_result, args.training_evidence, args.training_model_root,
                    args.native_result, args.native_evidence, args.native_materialization,
                    args.output_root, args.report)
        roots = {language: {name: getattr(args, f"{language}_{name}_root")
                            for name in ("result", "e5", "input")}
                 for language in ("de", "fr", "ja")}
        require(all(value is not None for value in required)
                and all(path is not None for value in roots.values() for path in value.values()),
                "relevance-aware v4 paths are required")
        run(args.contract, roots, args, args.output_root, args.report, not args.replay_only)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-relevance-aware-v4: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
