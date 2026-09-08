#!/usr/bin/env python3
"""Execute the frozen configuration-only NeuRoute training sanity matrix."""

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


planner = load("neuroute_training_sanity_planner", "plan-neuroute-training-sanity.py")
alignment = load("neuroute_training_sanity_alignment", "run-neuroute-v3-alignment-audit.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = (
        "plan-neuroute-training-sanity.py", "run-neuroute-training-sanity.py",
        "plan-neuroute-v3-alignment-audit.py", "run-neuroute-v3-alignment-audit.py",
        "plan-neuroute-dynamic-false-positive-v3.py", "run-neuroute-dynamic-false-positive-v3.py",
        "plan-neuroute-v3-external-confirmation.py", "run-neuroute-v3-external-confirmation.py",
        "plan-neuroute-v3-ja-external-confirmation.py", "run-neuroute-v3-ja-external-confirmation.py",
        "run-neuroute-inspired-semantic-address-v2.py", "run-direct-learned-semantic-address.py",
        "diagnose-neuroute-v2-collisions.py", "evaluate-projection-quantization.py",
    )
    return {name: sha256(THIS / name) for name in names}


def validate_activation(contract: dict[str, Any], audit_result: Path, audit_evidence: Path) -> None:
    activation = contract["activation"]
    require(audit_result.is_file() and sha256(audit_result) == activation["alignment_audit_result_sha256"],
            "training sanity audit result bytes differ")
    require(audit_evidence.is_file() and sha256(audit_evidence) == activation["alignment_audit_evidence_sha256"],
            "training sanity audit evidence bytes differ")
    receipt = json.loads(audit_evidence.read_text(encoding="utf-8"))
    require(receipt.get("integrity_replay_passed") is True
            and receipt.get("confirmation_claims_permitted") is False
            and receipt.get("result_sha256") == activation["alignment_audit_result_sha256"],
            "training sanity audit activation receipt differs")


def save_model(path: Path, arrays: dict[str, numpy.ndarray], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(path, metadata_json=numpy.asarray(json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), **arrays)
    loaded, loaded_metadata = read_model(path)
    require(loaded_metadata == metadata and all(numpy.array_equal(loaded[name], value) for name, value in arrays.items()),
            "training sanity model serialization differs")


def read_model(path: Path) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    with numpy.load(path, allow_pickle=False) as stored:
        metadata = json.loads(str(stored["metadata_json"].item()))
        arrays = {name: stored[name] for name in stored.files if name != "metadata_json"}
    return arrays, metadata


def infer(vectors: numpy.ndarray, arrays: dict[str, numpy.ndarray], batch_norm: bool) -> numpy.ndarray:
    values = (vectors - arrays["mean"]) / arrays["scale"]
    values = values @ arrays["weight1"].T + arrays["bias1"]
    if batch_norm:
        values = ((values - arrays["bn1_running_mean"]) / numpy.sqrt(arrays["bn1_running_var"] + arrays["bn_epsilon"]))
        values = values * arrays["bn1_weight"] + arrays["bn1_bias"]
    values = numpy.maximum(values, 0.0)
    values = values @ arrays["weight2"].T + arrays["bias2"]
    if batch_norm:
        values = ((values - arrays["bn2_running_mean"]) / numpy.sqrt(arrays["bn2_running_var"] + arrays["bn_epsilon"]))
        values = values * arrays["bn2_weight"] + arrays["bn2_bias"]
    values = numpy.maximum(values, 0.0)
    return (values @ arrays["weight3"].T + arrays["bias3"]).astype(numpy.float32)


def distance_scale(contract: dict[str, Any]) -> float:
    encoder = contract["encoder"]
    return contract["distance_objective"]["gamma"] * math.sqrt(encoder["bits"] / encoder["input_dimensions"])


def mine_query_false_positives(model: Any, document_features: Any, query_features: Any,
                               documents: numpy.ndarray, queries: numpy.ndarray,
                               training_positions: numpy.ndarray, pool: int,
                               selected: int) -> tuple[numpy.ndarray, numpy.ndarray, str]:
    import torch
    was_training = model.training
    model.eval()
    with torch.no_grad():
        document_latent = torch.cat([model(document_features[start:start + 1024])
                                     for start in range(0, document_features.shape[0], 1024)])
        query_latent = model(query_features[training_positions])
        document_latent = torch.nn.functional.normalize(document_latent, dim=1).numpy()
        query_latent = torch.nn.functional.normalize(query_latent, dim=1).numpy()
    model.train(was_training)
    indices = numpy.empty((len(queries), selected), dtype=numpy.int32)
    similarities = numpy.empty((len(queries), selected), dtype=numpy.float32)
    indices.fill(-1)
    similarities.fill(numpy.nan)
    for start in range(0, training_positions.size, 128):
        selected_queries = training_positions[start:start + 128]
        scores = query_latent[start:start + selected_queries.size] @ document_latent.T
        candidates = numpy.argpartition(-scores, pool - 1, axis=1)[:, :pool]
        for local, query_position in enumerate(selected_queries):
            source = documents[candidates[local]] @ queries[query_position]
            order = numpy.lexsort((candidates[local], source))[:selected]
            indices[query_position] = candidates[local, order]
            similarities[query_position] = source[order]
    digest = hashlib.sha256(indices[training_positions].tobytes()
                            + similarities[training_positions].tobytes()).hexdigest()
    return indices, similarities, digest


def build_torch_model(batch_norm: bool, contract: dict[str, Any]) -> Any:
    import torch
    encoder = contract["encoder"]

    class Encoder(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.linear1 = torch.nn.Linear(384, 96)
            self.bn1 = torch.nn.BatchNorm1d(96, eps=encoder["batch_norm_epsilon"],
                                           momentum=encoder["batch_norm_momentum"]) if batch_norm else None
            self.linear2 = torch.nn.Linear(96, 64)
            self.bn2 = torch.nn.BatchNorm1d(64, eps=encoder["batch_norm_epsilon"],
                                           momentum=encoder["batch_norm_momentum"]) if batch_norm else None
            self.linear3 = torch.nn.Linear(64, 12)

        def forward(self, values: Any) -> Any:
            values = self.linear1(values)
            if self.bn1 is not None:
                values = self.bn1(values)
            values = torch.relu(values)
            values = self.linear2(values)
            if self.bn2 is not None:
                values = self.bn2(values)
            return self.linear3(torch.relu(values))

    return Encoder()


def arrays_from_model(model: Any, mean: numpy.ndarray, scale: numpy.ndarray,
                      batch_norm: bool, contract: dict[str, Any]) -> dict[str, numpy.ndarray]:
    arrays = {"mean": mean, "scale": scale,
              "weight1": model.linear1.weight.detach().numpy().astype(numpy.float32),
              "bias1": model.linear1.bias.detach().numpy().astype(numpy.float32),
              "weight2": model.linear2.weight.detach().numpy().astype(numpy.float32),
              "bias2": model.linear2.bias.detach().numpy().astype(numpy.float32),
              "weight3": model.linear3.weight.detach().numpy().astype(numpy.float32),
              "bias3": model.linear3.bias.detach().numpy().astype(numpy.float32)}
    if batch_norm:
        arrays.update({"bn_epsilon": numpy.asarray(contract["encoder"]["batch_norm_epsilon"], dtype=numpy.float32),
                       "bn1_weight": model.bn1.weight.detach().numpy().astype(numpy.float32),
                       "bn1_bias": model.bn1.bias.detach().numpy().astype(numpy.float32),
                       "bn1_running_mean": model.bn1.running_mean.detach().numpy().astype(numpy.float32),
                       "bn1_running_var": model.bn1.running_var.detach().numpy().astype(numpy.float32),
                       "bn2_weight": model.bn2.weight.detach().numpy().astype(numpy.float32),
                       "bn2_bias": model.bn2.bias.detach().numpy().astype(numpy.float32),
                       "bn2_running_mean": model.bn2.running_mean.detach().numpy().astype(numpy.float32),
                       "bn2_running_var": model.bn2.running_var.detach().numpy().astype(numpy.float32)})
    return arrays


def source_distance(similarity: Any) -> Any:
    import torch
    return torch.sqrt(torch.clamp(2.0 - 2.0 * similarity, min=0.0))


def pair_loss(left: Any, right: Any, source_similarity: Any, scale: float) -> Any:
    import torch
    return torch.nn.functional.mse_loss(torch.linalg.vector_norm(left - right, dim=1),
                                        scale * source_distance(source_similarity))


def dual_mask_loss(source: Any, latent: Any, contract: dict[str, Any]) -> Any:
    import torch
    source_distances = torch.cdist(source, source)
    latent_distances = torch.cdist(latent, latent)
    diagonal = ~torch.eye(source.shape[0], dtype=torch.bool)
    off_diagonal = source_distances[diagonal]
    threshold = torch.quantile(off_diagonal, contract["distance_objective"]["source_mask_quantile"])
    scale = distance_scale(contract)
    mask = diagonal & ((source_distances <= threshold) | (latent_distances <= scale * threshold))
    require(bool(mask.any()), "training sanity dual mask is empty")
    return ((latent_distances[mask] - scale * source_distances[mask]) ** 2).mean()


def diversity_loss(raw: Any, contract: dict[str, Any]) -> Any:
    import torch
    diversity = contract["diversity"]
    standard = raw.std(dim=0)
    value = diversity["variance_weight"] * torch.relu(
        diversity["minimum_latent_standard_deviation"] - standard).mean()
    centered = raw - raw.mean(dim=0, keepdim=True)
    normalized = centered / (standard.unsqueeze(0) + 1.0e-6)
    covariance = normalized.T @ normalized / max(1, normalized.shape[0] - 1)
    covariance -= torch.diag(torch.diag(covariance))
    return value + diversity["covariance_weight"] * (covariance ** 2).mean()


def train_model(data: dict[str, Any], training_positions: numpy.ndarray,
                document_neighbours: numpy.ndarray, document_similarities: numpy.ndarray,
                query_neighbours: numpy.ndarray, query_similarities: numpy.ndarray,
                treatment: dict[str, Any], seed: int, contract: dict[str, Any]) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
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
    document_source = torch.from_numpy(documents.astype(numpy.float32, copy=False))
    model = build_torch_model(treatment["batch_norm"], contract)
    optimizer = torch.optim.AdamW(model.parameters(), lr=encoder["learning_rate"],
                                  weight_decay=encoder["weight_decay"])
    generator = torch.Generator().manual_seed(seed + 1)
    document_false, document_false_similarity = None, None
    query_false, query_false_similarity = None, None
    mining_rows: list[dict[str, Any]] = []
    losses: list[float] = []
    scale_factor = distance_scale(contract)
    started = time.perf_counter()
    for epoch in range(encoder["epochs"]):
        if epoch in mining["remine_epochs"] and treatment["id"] == "raw_euclidean_mined_pairs":
            document_false, document_false_similarity, digest = alignment.german.mine_false_positives(
                model, document_features, documents, mining["latent_neighbour_pool"], mining["selected_e5_farthest"])
            mining_rows.append({"epoch": epoch, "kind": "document", "sha256": digest})
        if epoch in mining["remine_epochs"] and treatment["query_false_positive_mining"]:
            query_false, query_false_similarity, digest = mine_query_false_positives(
                model, document_features, query_features, documents, queries, training_positions,
                mining["latent_neighbour_pool"], mining["selected_e5_farthest"])
            mining_rows.append({"epoch": epoch, "kind": "query_document", "sha256": digest})
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
            if treatment["id"] == "raw_euclidean_mined_pairs":
                pieces = [document_features[chosen], document_features[positive_documents],
                          query_features[selected_queries], document_features[positive_queries]]
                false_documents = None
                if document_false is not None:
                    false_documents = document_false[chosen_numpy, epoch % document_false.shape[1]]
                    pieces.append(document_features[false_documents])
                raw = model(torch.cat(pieces))
                count, query_count = chosen.numel(), selected_queries.size
                objective = pair_loss(raw[:count], raw[count:2 * count], torch.from_numpy(
                    document_similarities[chosen_numpy, epoch % document_neighbours.shape[1]]), scale_factor)
                objective += pair_loss(raw[2 * count:2 * count + query_count],
                                       raw[2 * count + query_count:2 * count + 2 * query_count],
                                       torch.from_numpy(query_similarities[selected_queries, epoch % query_neighbours.shape[1]]),
                                       scale_factor)
                objective += diversity_loss(raw, contract)
                if false_documents is not None:
                    objective += mining["document_false_positive_weight"] * pair_loss(
                        raw[:count], raw[-count:], torch.from_numpy(
                            document_false_similarity[chosen_numpy, epoch % document_false.shape[1]]), scale_factor)
            else:
                sampled = chosen[:encoder["pairwise_subbatch"]]
                pieces = [document_features[sampled], query_features[selected_queries],
                          document_features[positive_queries]]
                false_queries = None
                if query_false is not None:
                    false_queries = query_false[selected_queries, epoch % query_false.shape[1]]
                    pieces.append(document_features[false_queries])
                raw = model(torch.cat(pieces))
                count, query_count = sampled.numel(), selected_queries.size
                objective = dual_mask_loss(document_source[sampled], raw[:count], contract)
                objective += contract["distance_objective"]["query_positive_weight"] * pair_loss(
                    raw[count:count + query_count], raw[count + query_count:count + 2 * query_count],
                    torch.from_numpy(query_similarities[selected_queries, epoch % query_neighbours.shape[1]]),
                    scale_factor)
                if false_queries is not None:
                    objective += mining["query_false_positive_weight"] * pair_loss(
                        raw[count:count + query_count], raw[-query_count:], torch.from_numpy(
                            query_false_similarity[selected_queries, epoch % query_false.shape[1]]), scale_factor)
            optimizer.zero_grad(set_to_none=True)
            objective.backward()
            optimizer.step()
            total += float(objective.detach()) * chosen.numel()
        losses.append(total / documents.shape[0])
    model.eval()
    arrays = arrays_from_model(model, mean, standard, treatment["batch_norm"], contract)
    with torch.no_grad():
        expected = model(document_features[:1024]).numpy()
    require(numpy.allclose(infer(documents[:1024], arrays, treatment["batch_norm"]), expected,
                           rtol=3.0e-5, atol=3.0e-5), "training sanity serialization replay differs")
    return arrays, {"initial_loss": losses[0], "final_loss": losses[-1], "training_seconds": time.perf_counter() - started,
                    "mining": mining_rows, "torch_version": torch.__version__}


def qrels_positions(data: dict[str, Any]) -> dict[int, numpy.ndarray]:
    document_positions = {str(value): index for index, value in enumerate(data["document_ids"])}
    return {query_position: numpy.asarray(sorted(document_positions[document_id]
                                                  for document_id, grade in data["qrels"][query_id].items() if grade > 0),
                                           dtype=numpy.int32)
            for query_position, query_id in enumerate(data["query_ids"])}


def evaluate(data: dict[str, Any], positions: list[int], query_logits: numpy.ndarray,
             index: dict[str, Any], oracle: numpy.ndarray, probes: int,
             learned: bool, contract: dict[str, Any], qrels: dict[int, numpy.ndarray]) -> dict[str, Any]:
    rows = []
    digest = hashlib.sha256()
    for position in positions:
        requested = (alignment.german.diagnostic.addresses(query_logits[position], 12, probes) if learned
                     else alignment.german.direct.confidence_addresses(query_logits[position], 8, probes))
        posting_entries = sum(len(index["postings"].get(address, ())) for address in requested)
        candidates, accepted = alignment.german.direct.candidate_union(
            requested, index["postings"], len(data["document_ids"]), contract["routing"]["candidate_mass_target"])
        _, adc, ranked = alignment.german.direct.cascade(data, position, candidates)
        relevant = qrels[position]
        digest.update(int(position).to_bytes(4, "little"))
        digest.update(int(candidates.size).to_bytes(4, "little"))
        digest.update(candidates.astype("<i4", copy=False).tobytes())
        rows.append({"query_id": data["query_ids"][position], "candidate_count": int(candidates.size),
                     "accepted_probes": len(accepted), "posting_entries_requested": posting_entries,
                     "raw_e5_survival": float(numpy.isin(oracle[position], candidates).sum()) / oracle.shape[1],
                     "adc_e5_survival": float(numpy.isin(oracle[position], adc).sum()) / oracle.shape[1],
                     "raw_qrels_recall": float(numpy.isin(relevant, candidates).sum()) / max(1, relevant.size),
                     "adc_qrels_recall": float(numpy.isin(relevant, adc).sum()) / max(1, relevant.size),
                     "ndcg_at_10": alignment.german.quality.dcg_at_10(
                         data["document_ids"][ranked], data["qrels"][data["query_ids"][position]])})
    names = ("candidate_count", "accepted_probes", "posting_entries_requested", "raw_e5_survival",
             "adc_e5_survival", "raw_qrels_recall", "adc_qrels_recall", "ndcg_at_10")
    metrics = {name: float(numpy.mean([row[name] for row in rows], dtype=numpy.float64)) for name in names}
    metrics["candidate_fraction"] = metrics["candidate_count"] / len(data["document_ids"])
    return {"metrics": metrics, "candidate_sequence_sha256": digest.hexdigest(), "rows": rows}


def parameter_count(batch_norm: bool) -> int:
    value = 384 * 96 + 96 + 96 * 64 + 64 + 64 * 12 + 12
    return value + (2 * 96 + 2 * 64 if batch_norm else 0)


def load_dataset(dataset: dict[str, Any], roots: dict[str, Path]) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    module, old_contract_path, load_data, split_function = alignment.module_for(dataset["language"])
    result_path = roots["result"] / "result.json"
    require(result_path.is_file() and sha256(result_path) == dataset["result_sha256"],
            f"training sanity frozen result differs: {dataset['id']}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    old_contract = module.planner.load_contract(old_contract_path)
    data = load_data(roots["e5"], roots["input"], old_contract)
    split = split_function(data["query_ids"], old_contract)
    require(result.get("split") == split and len(data["query_ids"]) == dataset["queries"]
            and len(split["training_query_ids"]) == dataset["training_queries"]
            and len(split["configuration_selection_query_ids"]) == dataset["configuration_queries"],
            f"training sanity split differs: {dataset['id']}")
    return data, result, split


def model_path(output_root: Path, dataset: str, treatment: str, seed: int) -> Path:
    return output_root / dataset / f"model-{treatment}-{seed}.npz"


def dataset_run(dataset: dict[str, Any], roots: dict[str, Path], contract: dict[str, Any],
                contract_path: Path, output_root: Path, allow_training: bool) -> dict[str, Any]:
    data, frozen_result, split = load_dataset(dataset, roots)
    id_to_position = {value: index for index, value in enumerate(data["query_ids"])}
    training_positions = numpy.asarray([id_to_position[value] for value in split["training_query_ids"]], dtype=numpy.int32)
    configuration_positions = [id_to_position[value] for value in split["configuration_selection_query_ids"]]
    oracle, full_ndcg = alignment.german.direct.exact_oracle(data, contract["cascade"]["oracle_k"])
    qrels = qrels_positions(data)
    missing_training_model = any(
        treatment["source"] == "train"
        and not model_path(output_root, dataset["id"], treatment["id"], seed).is_file()
        for treatment in contract["treatments"] for seed in contract["encoder"]["seeds"])
    document_neighbours = document_similarities = query_neighbours = query_similarities = None
    if missing_training_model:
        require(allow_training, f"training sanity cached matrix is incomplete: {dataset['id']}")
        document_neighbours, document_similarities = alignment.german.v2.nearest(
            data["documents"], data["documents"], 16, numpy.arange(len(data["document_ids"]), dtype=numpy.int32))
        query_neighbours, query_similarities = alignment.german.v2.nearest(data["queries"], data["documents"], 10)
    frozen_models = alignment.dynamic_models(frozen_result, dataset, roots["result"], data)
    frozen_by_seed = {row["seed"]: row for row in frozen_models}
    models, quality_rows = [], []
    treatment_by_id = {row["id"]: row for row in contract["treatments"]}
    for treatment in contract["treatments"]:
        for seed in contract["encoder"]["seeds"]:
            if treatment["source"] == "reuse_frozen_v3_dynamic_model_bytes":
                frozen = frozen_by_seed[seed]
                arrays = frozen["artifact"]
                batch_norm = False
                metadata = {"source": treatment["source"], "source_model_sha256": frozen["model_sha256"]}
                artifact_sha = frozen["model_sha256"]
            else:
                path = model_path(output_root, dataset["id"], treatment["id"], seed)
                if path.is_file():
                    arrays, metadata = read_model(path)
                    require(metadata.get("contract_sha256") == sha256(contract_path)
                            and metadata.get("source_files_sha256") == source_hashes()
                            and metadata.get("dataset") == dataset["id"]
                            and metadata.get("treatment") == treatment["id"] and metadata.get("seed") == seed,
                            f"training sanity cached model binding differs: {dataset['id']} {treatment['id']} {seed}")
                else:
                    require(allow_training, f"training sanity model is missing in replay-only mode: {path}")
                    require(all(value is not None for value in (document_neighbours, document_similarities,
                                                                 query_neighbours, query_similarities)),
                            "training sanity neighbour matrix is unavailable")
                    arrays, training = train_model(data, training_positions, document_neighbours, document_similarities,
                                                   query_neighbours, query_similarities, treatment, seed, contract)
                    metadata = {"schema_version": 1, "family": "neuroute_training_sanity_model",
                                "contract_sha256": sha256(contract_path), "source_files_sha256": source_hashes(),
                                "dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
                                "batch_norm": treatment["batch_norm"], "training": training}
                    save_model(path, arrays, metadata)
                artifact_sha = sha256(path)
                batch_norm = treatment["batch_norm"]
            document_raw = infer(data["documents"], arrays, batch_norm)
            threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
            document_logits = document_raw - threshold
            query_raw = infer(data["queries"], arrays, batch_norm)
            query_logits = query_raw - threshold
            index = alignment.german.direct.build_index(document_logits, data["documents"], 12, 1)
            model_record = {"dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
                            "model_sha256": artifact_sha, "batch_norm": batch_norm,
                            "parameter_count": parameter_count(batch_norm), "threshold": threshold.tolist(),
                            "metadata": metadata,
                            "probing": alignment.probing_diagnostics(
                                {"document_logits": document_logits, "query_raw": query_raw, "threshold": threshold},
                                configuration_positions, oracle, contract["routing"]["probe_budgets"])}
            models.append(model_record)
            for probes in contract["routing"]["probe_budgets"]:
                quality_rows.append({"dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
                                     "probes": probes, **evaluate(data, configuration_positions, query_logits, index,
                                                                  oracle, probes, True, contract, qrels)})
            progress = {"schema_version": 1, "family": "neuroute_training_sanity_progress",
                        "contract_sha256": sha256(contract_path), "completed_models":
                            [{"treatment": row["treatment"], "seed": row["seed"], "model_sha256": row["model_sha256"]}
                             for row in models]}
            progress_path = output_root / dataset["id"] / "progress.json"
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_bytes(canonical(progress))

    pca_document, pca_artifact = alignment.german.direct.document_head(data["documents"])
    pca_query = ((data["queries"] - pca_artifact["document_mean"]) @ pca_artifact["document_projection"]
                 - pca_artifact["document_threshold"]).astype(numpy.float32)
    pca_index = alignment.german.direct.build_index(pca_document, data["documents"], contract["routing"]["pca_bits"],
                                                    contract["routing"]["pca_replication"])
    pca = {"dataset": dataset["id"], "treatment": "symmetric_pca_control",
           "probes": contract["routing"]["pca_probes"], "parameter_count": 384 * 16,
           **evaluate(data, configuration_positions, pca_query, pca_index, oracle,
                      contract["routing"]["pca_probes"], False, contract, qrels)}
    return {"id": dataset["id"], "language": dataset["language"], "configuration_query_count": len(configuration_positions),
            "full_exact_e5_ndcg_at_10": float(numpy.mean(full_ndcg[configuration_positions])),
            "e5_manifest_sha256": data["manifest_sha256"], "input_manifest_sha256": data["input_manifest_sha256"],
            "models": models, "quality_rows": quality_rows, "pca": pca}


def seed_mean(dataset: dict[str, Any], treatment: str, probes: int) -> dict[str, float]:
    rows = [row["metrics"] for row in dataset["quality_rows"]
            if row["treatment"] == treatment and row["probes"] == probes]
    require(len(rows) == 3, "training sanity seed mean differs")
    return {name: float(numpy.mean([row[name] for row in rows])) for name in rows[0]}


def decide(datasets: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    decision = contract["decision"]
    eligible = []
    trained = [row["id"] for row in contract["treatments"] if row["source"] == "train"]
    for treatment in trained:
        for probes in contract["routing"]["probe_budgets"]:
            if probes > decision["maximum_probe_budget_for_efficiency"]:
                continue
            comparisons = []
            for dataset in datasets:
                candidate = seed_mean(dataset, treatment, probes)
                baseline = seed_mean(dataset, "v3_cosine_dynamic_frozen_control", 512)
                pca = dataset["pca"]["metrics"]
                passed = (candidate["candidate_fraction"] <= decision["maximum_candidate_fraction"]
                          and candidate["adc_e5_survival"] >= baseline["adc_e5_survival"]
                              - decision["maximum_adc_survival_loss_vs_v3_512"]
                          and candidate["ndcg_at_10"] >= baseline["ndcg_at_10"]
                              - decision["maximum_ndcg_loss_vs_v3_512"]
                          and candidate["ndcg_at_10"] >= pca["ndcg_at_10"]
                              - decision["maximum_ndcg_loss_vs_pca"])
                comparisons.append({"dataset": dataset["id"], "passed": passed, "candidate": candidate,
                                    "v3_512": baseline, "pca_16": pca})
            if all(row["passed"] for row in comparisons):
                eligible.append({"treatment": treatment, "probes": probes, "comparisons": comparisons,
                                 "cross_language_mean_ndcg": float(numpy.mean(
                                     [row["candidate"]["ndcg_at_10"] for row in comparisons]))})
    eligible.sort(key=lambda row: (row["probes"], -row["cross_language_mean_ndcg"], row["treatment"]))
    selected = eligible[0] if eligible else None
    return {"eligible": eligible, "selected": selected,
            "next": decision["next_if_pass"] if selected is not None else decision["next_if_none"],
            "confirmation_claims_permitted": False}


def run(contract_path: Path, roots: dict[str, dict[str, Path]], audit_result: Path, audit_evidence: Path,
        output_root: Path, report_path: Path, allow_training: bool) -> None:
    contract = planner.load_contract(contract_path)
    validate_activation(contract, audit_result, audit_evidence)
    datasets = [dataset_run(dataset, roots[dataset["language"]], contract, contract_path, output_root, allow_training)
                for dataset in contract["datasets"]]
    report = {"schema_version": 1, "family": "neuroute_training_sanity_config_only_result",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(contract_path),
              "source_files_sha256": source_hashes(), "activation": contract["activation"],
              "datasets": datasets, "decision": decide(datasets, contract)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical(report))


def self_test() -> None:
    import torch
    contract = planner.load_contract(THIS / "neuroute-training-sanity.example.json")
    torch.manual_seed(7)
    vectors = numpy.random.default_rng(7).standard_normal((16, 384)).astype(numpy.float32)
    mean = numpy.zeros(384, dtype=numpy.float32)
    scale = numpy.ones(384, dtype=numpy.float32)
    for batch_norm in (False, True):
        model = build_torch_model(batch_norm, contract)
        model.eval()
        arrays = arrays_from_model(model, mean, scale, batch_norm, contract)
        with torch.no_grad():
            expected = model(torch.from_numpy(vectors)).numpy()
        require(numpy.allclose(infer(vectors, arrays, batch_norm), expected, rtol=3.0e-5, atol=3.0e-5),
                "training sanity inference self-test differs")
    source = torch.nn.functional.normalize(torch.from_numpy(vectors[:, :12]), dim=1)
    require(float(dual_mask_loss(source, torch.from_numpy(vectors[:, :12]), contract)) >= 0.0,
            "training sanity dual-mask self-test differs")
    smoke = json.loads(json.dumps(contract))
    smoke["encoder"].update({"epochs": 1, "batch_size": 16, "training_query_batch_size": 8,
                             "pairwise_subbatch": 8, "torch_threads": 1})
    smoke["mining"]["remine_epochs"] = []
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
    smoke_data = {"documents": documents, "queries": queries}
    for treatment in smoke["treatments"][1:]:
        arrays, training = train_model(smoke_data, numpy.arange(6, dtype=numpy.int32),
                                       document_neighbours, document_similarities, query_order,
                                       query_similarities, treatment, 17, smoke)
        require(arrays["weight3"].shape == (12, 64) and training["final_loss"] >= 0.0,
                "training sanity training smoke differs")
    require(abs(distance_scale(contract) - 0.6 * math.sqrt(12.0 / 384.0)) < 1.0e-12
            and parameter_count(False) == 43948 and parameter_count(True) == 44268
            and len(planner.matrix(contract)) == 270, "training sanity self-test differs")
    print("NeuRoute training sanity runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-training-sanity.example.json")
    parser.add_argument("--audit-result", type=Path)
    parser.add_argument("--audit-evidence", type=Path)
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
        roots = {language: {name: getattr(args, f"{language}_{name}_root") for name in ("result", "e5", "input")}
                 for language in ("de", "fr", "ja")}
        require(all(value is not None for value in (args.audit_result, args.audit_evidence, args.output_root, args.report))
                and all(path is not None for value in roots.values() for path in value.values()),
                "training sanity paths are required")
        run(args.contract, roots, args.audit_result, args.audit_evidence, args.output_root, args.report,
            not args.replay_only)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-training-sanity: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
