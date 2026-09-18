#!/usr/bin/env python3
"""Train/held-out teacher-score diagnostics on the frozen R4 candidate shell."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
try:
    import torch
    from torch import nn
except ModuleNotFoundError:  # pragma: no cover - CI syntax/self-test environment
    torch = None
    nn = None
try:
    from scipy import sparse
    from sklearn.linear_model import Ridge
except ModuleNotFoundError:  # pragma: no cover - CI syntax/self-test environment
    sparse = None
    Ridge = None

D = 384
RECORD_BYTES = 148


def load_helpers():
    path = Path(__file__).with_name("run-thq4-residual-frontier.py")
    spec = importlib.util.spec_from_file_location("thq4_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load THQ4 helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_helpers()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def feature_tensor(levels: np.ndarray) -> torch.Tensor:
    return torch.nn.functional.one_hot(
        torch.from_numpy(levels.astype(np.int64)), num_classes=4).reshape(len(levels), -1).float()


if nn is None:
    class Decoder:  # type: ignore[no-redef]
        def __init__(self, hidden: int) -> None:
            raise RuntimeError("PyTorch is required for the teacher replay")
else:
    class Decoder(nn.Module):
        def __init__(self, hidden: int) -> None:
            super().__init__()
            self.layers = nn.Sequential(nn.Linear(D * 4, hidden), nn.ReLU(), nn.Linear(hidden, D))
            nn.init.zeros_(self.layers[-1].weight)
            nn.init.zeros_(self.layers[-1].bias)

        def forward(self, value: torch.Tensor) -> torch.Tensor:
            return self.layers(value)


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int = 10) -> np.ndarray:
    order = np.lexsort((ids, -scores))
    return ids[order[:limit]]


def load_ids(flat: Path, raw: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = json.loads(raw.read_text(encoding="utf-8"))["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * RECORD_BYTES:
        raise RuntimeError("candidate flat size differs")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, RECORD_BYTES))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    return ids, offsets


def validate_candidate_receipt(receipt_path: Path, raw: Path, flat: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    raw_sha = sha(raw)
    flat_sha = sha(flat)
    if receipt.get("family") != "semantic_r4_fused_candidate_materialization_v1":
        raise RuntimeError("candidate receipt family differs")
    if receipt.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not executed")
    if receipt.get("raw_sha256") != raw_sha:
        raise RuntimeError("candidate receipt/raw binding differs")
    flat_entry = receipt.get("flat_file", {})
    if flat_entry.get("sha256") != flat_sha or int(flat_entry.get("bytes", -1)) != flat.stat().st_size:
        raise RuntimeError("candidate receipt/flat binding differs")
    for field in ("runner_sha256", "thq_manifest_sha256", "layout_manifest_sha256", "native_receipt_sha256"):
        if not receipt.get(field):
            raise RuntimeError(f"candidate receipt missing {field}")
    return {
        "receipt_sha256": sha(receipt_path),
        "raw_sha256": raw_sha,
        "flat_sha256": flat_sha,
        "runner_sha256": receipt["runner_sha256"],
        "thq_manifest_sha256": receipt["thq_manifest_sha256"],
        "layout_manifest_sha256": receipt["layout_manifest_sha256"],
        "native_receipt_sha256": receipt["native_receipt_sha256"],
    }


def overlap(predicted: np.ndarray, teacher: np.ndarray) -> float:
    return float(np.isin(teacher, predicted).sum() / 10.0)


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        if torch is None:
            print("run-thq-r4-teacher-diagnostics self-test PASS (PyTorch replay dependency unavailable)")
            return
        model = Decoder(8)
        probe = torch.zeros((2, D * 4), dtype=torch.float32)
        if model(probe).shape != (2, D):
            raise RuntimeError("teacher decoder shape differs")
        if not all(torch.count_nonzero(parameter).item() == 0
                   for parameter in model.layers[-1].parameters()):
            raise RuntimeError("teacher residual head is not zero initialized")
        print("run-thq-r4-teacher-diagnostics self-test PASS")
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--train-query-count", type=int, default=120)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                          shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    query_count = args.queries.stat().st_size // (4 * D)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = load_ids(args.candidate_flat, args.candidate_raw)
    candidate_provenance = validate_candidate_receipt(args.candidate_receipt, args.candidate_raw,
                                                      args.candidate_flat)
    train_queries = min(args.train_query_count, query_count - 1)
    heldout_queries = list(range(train_queries, query_count))

    centroids = h.fit_centroids(train, thresholds)
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    rows: list[tuple[int, int]] = []
    for qi in range(train_queries):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        levels = h.unpack_thq(np.asarray(thq_codes[ids]))
        rows.extend((qi, int(doc)) for doc, _ in zip(ids, levels))
    row_array = np.asarray(rows, dtype=np.int64)
    source = np.asarray(documents[row_array[:, 1]], dtype=np.float32)
    levels = h.unpack_thq(np.asarray(thq_codes[row_array[:, 1]]))
    query_indices = row_array[:, 0].copy()
    query_values = np.asarray(queries[query_indices], dtype=np.float32)
    teacher_scores = np.sum(source * query_values, axis=1).astype(np.float32)
    teacher_scores /= np.maximum(np.linalg.norm(source, axis=1), np.finfo(np.float32).tiny)
    del query_values

    # Ridge is the linear one-hot control, fit only on the detached training vectors.
    train_features = sparse.csr_matrix((np.ones(train_count * D, dtype=np.float32),
                                         (np.repeat(np.arange(train_count), D),
                                          np.arange(train_count * D) % D * 4 + train_levels.reshape(-1))),
                                        shape=(train_count, D * 4))
    ridge = Ridge(alpha=1e-3, fit_intercept=True, solver="lsqr")
    ridge.fit(train_features, train)

    torch.manual_seed(20260918)
    model = Decoder(args.hidden)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    order = np.arange(len(row_array))
    rng = np.random.default_rng(20260918)
    loss_history: list[float] = []
    level_values = levels
    for _ in range(args.epochs):
        rng.shuffle(order)
        total = 0.0
        for start in range(0, len(order), args.batch_size):
            indices = order[start:start + args.batch_size]
            batch_features = feature_tensor(level_values[indices])
            batch_source = torch.from_numpy(source[indices])
            batch_base = torch.from_numpy(
                centroids[np.arange(D)[None, :], level_values[indices]])
            batch_queries = torch.from_numpy(np.asarray(queries[query_indices[indices]], dtype=np.float32))
            batch_scores = torch.from_numpy(teacher_scores[indices])
            prediction = batch_base + model(batch_features)
            predicted_score = torch.sum(torch.nn.functional.normalize(prediction, dim=1) * batch_queries, dim=1)
            loss = torch.mean((prediction - batch_source) ** 2) * 0.01 + \
                torch.mean((predicted_score - batch_scores) ** 2)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * len(indices)
        loss_history.append(total / len(order))

    diagnostics: dict[str, dict[str, list[float]]] = {
        split: {arm: {metric: [] for metric in ("score_mse", "top10_overlap")}
                for arm in ("centroid", "ridge", "decoder")}
        for split in ("train", "heldout")
    }
    rows_out = []
    for qi in range(query_count):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        source_values = np.asarray(documents[ids], dtype=np.float32)
        levels_q = h.unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels_q]
        with torch.no_grad():
            decoded = base + model(feature_tensor(levels_q)).numpy()
        ridge_values = ridge.predict(feature_tensor(levels_q).numpy()).astype(np.float32)
        teacher = np.asarray(teacher_ids[qi], dtype=np.int64)
        query = np.asarray(queries[qi], dtype=np.float32)
        teacher_score = source_values @ query
        teacher_score /= np.maximum(np.linalg.norm(source_values, axis=1), np.finfo(np.float32).tiny)
        split = "train" if qi < train_queries else "heldout"
        for arm, values in (("centroid", base), ("ridge", ridge_values), ("decoder", decoded)):
            predicted_score = values @ query
            predicted_score /= np.maximum(np.linalg.norm(values, axis=1), np.finfo(np.float32).tiny)
            exact_top = top_ids(teacher_score, ids)
            selected = top_ids(predicted_score, ids)
            diagnostics[split][arm]["score_mse"].append(float(np.mean((predicted_score - teacher_score) ** 2)))
            diagnostics[split][arm]["top10_overlap"].append(overlap(selected, exact_top))
            rows_out.append({"query": qi, "split": split, "arm": arm,
                             "score_mse": diagnostics[split][arm]["score_mse"][-1],
                             "top10_overlap": diagnostics[split][arm]["top10_overlap"][-1],
                             "teacher_overlap": overlap(selected, teacher),
                             "top10": selected.astype(int).tolist()})

    summaries = {}
    for split, arms in diagnostics.items():
        summaries[split] = {}
        for arm, metrics in arms.items():
            summaries[split][arm] = {metric: {"mean": float(np.mean(values)),
                                              "p05": float(np.quantile(values, 0.05)),
                                              "min": float(np.min(values))}
                                     for metric, values in metrics.items()}
    result = {
        "schema_version": 1,
        "family": "thq_r4_teacher_diagnostics_v1",
        "status": "EXECUTED",
        "runner_sha256": sha(Path(__file__)),
        "evidence_status": "candidate_shell_train_heldout_teacher_score_diagnostics",
        "documents": document_count,
        "training_count": train_count,
        "query_count": query_count,
        "train_query_count": train_queries,
        "heldout_query_count": len(heldout_queries),
        "candidate_flat_sha256": sha(args.candidate_flat),
        "candidate_raw_sha256": sha(args.candidate_raw),
        "candidate_receipt_sha256": candidate_provenance["receipt_sha256"],
        "candidate_provenance": candidate_provenance,
        "documents_sha256": sha(args.documents),
        "training_sha256": sha(args.train_vectors),
        "thq4_codes_sha256": sha(args.thq4_codes),
        "thq4_thresholds_sha256": sha(args.thq4_thresholds),
        "queries_sha256": sha(args.queries),
        "teacher_ids_sha256": sha(args.teacher_ids),
        "model_state_sha256": hashlib.sha256(b"".join(
            parameter.detach().cpu().numpy().astype("<f4").tobytes()
            for parameter in model.parameters())).hexdigest(),
        "ridge_coef_sha256": hashlib.sha256(np.asarray(ridge.coef_, dtype="<f4").tobytes()).hexdigest(),
        "loss_history": loss_history,
        "summaries": summaries,
        "rows": rows_out,
        "limitations": [
            "train and held-out splits are query-disjoint within the frozen 152-query R4 shell",
            "documents may recur across the query-disjoint train and held-out shells",
            "teacher targets are exact FP32 scores only for candidate documents",
            "no full-corpus routing or native latency claim",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
