#!/usr/bin/env python3
"""Pinned offline synthetic Lloyd/E-means comparison; no production adoption."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

# Set these before loading either native numerical library.
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
for variable in THREAD_VARIABLES:
    os.environ[variable] = "1"

import faiss
import numpy as np
from faiss.contrib.e_means import NumpyEmeans

SEED = 20261119
DIMENSION, CENTROIDS = 16, 8
TRAIN_ROWS, BASE_ROWS, QUERY_ROWS, HELDOUT_ROWS = 4096, 4096, 128, 1024
PASSES, TRAIN_REPEATS, SEARCH_REPEATS, SEARCH_WARMUPS = 10, 4, 3, 1
SCAN_CAPS, NPROBES = (256, 512, 1024, 4096), (1, 2, 4, 8)
SOURCES = ("run_emeans_vs_lloyd.py", "validate_emeans_vs_lloyd.py")


def cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if platform.system() == "Linux" and cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or f"{platform.machine()} (model unavailable)"


def digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<f4").tobytes()).hexdigest()


def ids_digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<i8").tobytes()).hexdigest()


def make_fixture() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(SEED)
    means = rng.normal(0.0, 2.0, size=(CENTROIDS, DIMENSION)).astype(np.float32)
    fixture = {}
    for name, rows in (("train", TRAIN_ROWS), ("base", BASE_ROWS),
                       ("query", QUERY_ROWS), ("heldout", HELDOUT_ROWS)):
        assignments = rng.integers(0, CENTROIDS, size=rows)
        noise = rng.normal(0.0, 0.35, size=(rows, DIMENSION)).astype(np.float32)
        fixture[name] = np.ascontiguousarray(means[assignments] + noise)
    return fixture


def split_evidence(fixture: dict[str, np.ndarray]) -> dict:
    row_sets = {name: {row.tobytes() for row in values} for name, values in fixture.items()}
    overlap = {f"{a}/{b}": len(row_sets[a] & row_sets[b])
               for a in row_sets for b in row_sets if a < b}
    if any(overlap.values()):
        raise RuntimeError("train/base/query/heldout rows overlap")
    return {"encoding": "row-major little-endian float32", "splits": {
        name: {"rows": len(values), "sha256": digest(values)}
        for name, values in fixture.items()}, "cross_split_duplicate_rows": overlap}


def nearest(values: np.ndarray, centroids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    distances = np.sum((values.astype(np.float64)[:, None, :]
                        - centroids.astype(np.float64)[None, :, :]) ** 2, axis=2)
    labels = np.argmin(distances, axis=1)
    return labels, distances[np.arange(len(values)), labels]


def exact_top(base: np.ndarray, query: np.ndarray, candidates: np.ndarray | None = None) -> np.ndarray:
    ids = np.arange(len(base)) if candidates is None else candidates
    distance = np.sum((base[ids].astype(np.float64) - query.astype(np.float64)) ** 2, axis=1)
    return ids[np.lexsort((ids, distance))[:100]]


def recalls(actual: np.ndarray, expected: np.ndarray) -> dict:
    return {f"recall_at_{k}": [len(set(a[:k]) & set(e[:k])) / k
                               for a, e in zip(actual, expected)] for k in (10, 100)}


def summary(samples: list[float]) -> dict:
    return {"samples_ms": samples, "sample_count": len(samples),
            "p50_ms": float(np.percentile(samples, 50)),
            "p95_ms": float(np.percentile(samples, 95))}


def occupancy(values: np.ndarray, centroids: np.ndarray) -> dict:
    labels, _ = nearest(values, centroids)
    counts = np.bincount(labels, minlength=CENTROIDS)
    return {"counts": counts.tolist(), "empty_clusters": int(np.sum(counts == 0)),
            "imbalance_factor": float(np.sum(counts.astype(np.float64) ** 2) * CENTROIDS / len(values) ** 2)}


def make_ivf(centroids: np.ndarray, base: np.ndarray) -> faiss.IndexIVFFlat:
    quantizer = faiss.IndexFlatL2(DIMENSION)
    quantizer.add(centroids)
    index = faiss.IndexIVFFlat(quantizer, DIMENSION, CENTROIDS, faiss.METRIC_L2)
    index.is_trained = True
    before = digest(index.quantizer.reconstruct_n(0, CENTROIDS))
    if before != digest(centroids):
        raise RuntimeError("installed centroids differ")
    index.add(base)
    if digest(index.quantizer.reconstruct_n(0, CENTROIDS)) != before:
        raise RuntimeError("hidden centroid retraining during index.add")
    labels, _ = nearest(base, centroids)
    for cell in range(CENTROIDS):
        count = index.invlists.list_size(cell)
        actual = np.sort(faiss.rev_swig_ptr(index.invlists.get_ids(cell), count).copy())
        if not np.array_equal(actual, np.flatnonzero(labels == cell)):
            raise RuntimeError("IVF list membership differs from independent reference")
    return index


def evaluate_native(index: faiss.IndexIVFFlat, queries: np.ndarray, expected: np.ndarray, nprobe: int) -> dict:
    index.nprobe = nprobe
    actual, timings, scans, distance_counts = [], [], [], []
    for query in queries:
        row = query.reshape(1, -1)
        for _ in range(SEARCH_WARMUPS):
            index.search(row, 100)
        _, cells = index.quantizer.search(row, nprobe)
        list_size_sum = sum(index.invlists.list_size(int(c)) for c in cells[0])
        samples, query_counts = [], []
        for _ in range(SEARCH_REPEATS):
            faiss.cvar.indexIVF_stats.reset()
            start = time.perf_counter()
            _, ids = index.search(row, 100)
            samples.append((time.perf_counter() - start) * 1000)
            query_counts.append(int(faiss.cvar.indexIVF_stats.ndis))
            if query_counts[-1] != list_size_sum:
                raise RuntimeError("native IVF ndis differs from probed list-size sum")
        actual.append(ids[0])
        timings.append(samples)
        scans.append(list_size_sum)
        distance_counts.append(query_counts)
    actual = np.asarray(actual)
    return {"nprobe": nprobe, "scope": "native_IndexIVFFlat_search_top100",
            "scanned_documents": scans, "result_ids_sha256": ids_digest(actual),
            "per_query_sample_ndis": distance_counts,
            **recalls(actual, expected), "per_query_samples_ms": timings,
            "timing": summary([t for row in timings for t in row])}


def capped_candidates(query: np.ndarray, centroids: np.ndarray, lists: list[np.ndarray], cap: int) -> np.ndarray:
    distance = np.sum((centroids.astype(np.float64) - query.astype(np.float64)) ** 2, axis=1)
    order = np.lexsort((np.arange(CENTROIDS), distance))
    # The boundary list is truncated by stable base ID, not hidden exact scores.
    return np.concatenate([lists[int(cell)] for cell in order])[:cap]


def evaluate_cap(centroids: np.ndarray, base: np.ndarray, queries: np.ndarray,
                 expected: np.ndarray, cap: int) -> dict:
    labels, _ = nearest(base, centroids)
    lists = [np.flatnonzero(labels == cell) for cell in range(CENTROIDS)]
    actual, timings, scans = [], [], []
    for query in queries:
        for _ in range(SEARCH_WARMUPS):
            exact_top(base, query, capped_candidates(query, centroids, lists, cap))
        samples = []
        for _ in range(SEARCH_REPEATS):
            start = time.perf_counter()
            candidates = capped_candidates(query, centroids, lists, cap)
            ids = exact_top(base, query, candidates)
            samples.append((time.perf_counter() - start) * 1000)
        actual.append(ids)
        timings.append(samples)
        scans.append(len(candidates))
    actual = np.asarray(actual)
    return {"scan_cap": cap, "scope": "python_preassigned_list_prefix_cap_exact_fp64_rerank_top100",
            "scanned_documents": scans, "result_ids_sha256": ids_digest(actual),
            **recalls(actual, expected), "per_query_samples_ms": timings,
            "timing": summary([t for row in timings for t in row])}


class CountedEmeans(NumpyEmeans):
    """Count assignment input rows without changing the training rule."""
    def _assign_rows(self, values, centroids):
        self.assignment_rows += len(values)
        return super()._assign_rows(values, centroids)


def train_method(name: str, train: np.ndarray, init: np.ndarray) -> tuple[np.ndarray, dict]:
    start = time.perf_counter()
    if name == "faiss_lloyd":
        trainer = faiss.Kmeans(DIMENSION, CENTROIDS, niter=PASSES, nredo=1, seed=SEED,
                               max_points_per_centroid=TRAIN_ROWS, verbose=False, gpu=False)
        trainer.train(train, init_centroids=init.copy())
        centroids = trainer.centroids.copy()
        rows = len(trainer.iteration_stats) * TRAIN_ROWS
        count_kind = "full_train_rows_times_observed_Lloyd_iterations"
        recipe = {"iterations": len(trainer.iteration_stats),
                  "max_points_per_centroid": trainer.cp.max_points_per_centroid}
    else:
        trainer = CountedEmeans(DIMENSION, CENTROIDS, nepochs=PASSES, seed=SEED,
                                capture_assignments=False, spherical=False,
                                max_points_per_centroid=TRAIN_ROWS)
        trainer.assignment_rows = 0
        centroids, _ = trainer.train(train, init_centroids=init.copy())
        rows, count_kind, recipe = trainer.assignment_rows, "observed_assign_rows_input_rows", trainer.recipe
    elapsed = (time.perf_counter() - start) * 1000
    if rows != PASSES * TRAIN_ROWS:
        raise RuntimeError(f"assignment work differs for {name}: {rows}")
    return np.ascontiguousarray(centroids, dtype=np.float32), {
        "elapsed_ms": elapsed, "assignment_rows": rows,
        "training_input_rows": len(train), "training_input_sha256": digest(train),
        "centroid_distance_evaluations": rows * CENTROIDS,
        "assignment_count_kind": count_kind, "recipe": recipe}


def source_evidence(expected_head: str | None) -> dict:
    root = Path(__file__).resolve().parents[2]
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    files = {f"tools/agent-memory-bench/{name}": hashlib.sha256(
        Path(__file__).with_name(name).read_bytes()).hexdigest() for name in SOURCES}
    if expected_head is not None:
        if re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
            raise RuntimeError("expected PR HEAD must be a full lowercase Git SHA")
        if expected_head != checkout:
            raise RuntimeError("expected PR HEAD differs from the execution checkout")
        for path, observed in files.items():
            committed = subprocess.check_output(["git", "show", f"{expected_head}:{path}"], cwd=root)
            if hashlib.sha256(committed).hexdigest() != observed:
                raise RuntimeError(f"executed source differs from expected PR HEAD: {path}")
    return {"actual_checkout_sha": checkout, "expected_pr_head_sha": expected_head,
            "source_files_sha256": files,
            "identity_semantics": "checkout before execution; source hashes bind uncommitted edits; PR HEAD may be unassigned"}


def write_evidence(raw: dict, output: Path, receipt: Path, sidecar: Path) -> None:
    for path in (output, receipt, sidecar):
        path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(raw, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    raw_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt.write_text(json.dumps({
        "schema": "r5.emeans_vs_lloyd.receipt.v2", "execution_status": "EXECUTED",
        "production_activation": False, "disposition": "DEFER",
        "raw_output": {"path": str(output), "sha256": raw_hash},
        "provenance": raw["provenance"], "dataset": raw["dataset"],
        "environment": raw["environment"]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar.write_text(f"{raw_hash}  {output.name}\n", encoding="utf-8")


def run(output: Path, receipt: Path, sidecar: Path, expected_head: str | None) -> None:
    provenance = source_evidence(expected_head)  # Reject a stale/mismatched HEAD before training.
    if (np.__version__, faiss.__version__) != ("2.4.6", "1.16.0"):
        raise RuntimeError("requires numpy==2.4.6 and faiss-cpu==1.16.0")
    faiss.omp_set_num_threads(1)
    fixture = make_fixture()
    train, base, queries = (fixture[name] for name in ("train", "base", "query"))
    init = train[:CENTROIDS].copy()
    expected = np.asarray([exact_top(base, query) for query in queries])
    exact = faiss.IndexFlatL2(DIMENSION)
    exact.add(base)
    if not np.array_equal(exact.search(queries, 100)[1], expected):
        raise RuntimeError("Faiss exact top100 differs from independent FP64 oracle")
    names = ("faiss_lloyd", "faiss_numpy_emeans")
    trained = {name: [] for name in names}
    models = {}
    for name in names:
        train_method(name, train, init)  # Excluded warmup.
    for repeat in range(TRAIN_REPEATS):
        for name in (names if repeat % 2 == 0 else names[::-1]):
            centroids, observation = train_method(name, train, init)
            trained[name].append(observation)
            if name in models and not np.array_equal(models[name], centroids):
                raise RuntimeError("training repeats produced different centroids")
            models[name] = centroids
    methods = []
    for name in names:
        centroids = models[name]
        _, train_error = nearest(train, centroids)
        _, heldout_error = nearest(fixture["heldout"], centroids)
        start = time.perf_counter()
        index = make_ivf(centroids, base)
        build_ms = (time.perf_counter() - start) * 1000
        methods.append({"method": name, "centroids": centroids.tolist(), "centroids_sha256": digest(centroids),
            "training": trained[name], "training_timing": summary([r["elapsed_ms"] for r in trained[name]]),
            "ivf_build_ms": build_ms, "serialized_index_bytes": int(faiss.serialize_index(index).nbytes),
            "base_vector_array_bytes": int(base.nbytes), "centroid_array_bytes": int(centroids.nbytes),
            "peak_rss": "NOT_MEASURED",
            "train_mse": float(np.mean(train_error)), "heldout_mse": float(np.mean(heldout_error)),
            "train_occupancy": occupancy(train, centroids), "base_occupancy": occupancy(base, centroids),
            "native_ivf": [evaluate_native(index, queries, expected, n) for n in NPROBES],
            "equal_scan_proxy": [evaluate_cap(centroids, base, queries, expected, cap) for cap in SCAN_CAPS]})
        if digest(index.quantizer.reconstruct_n(0, CENTROIDS)) != digest(centroids):
            raise RuntimeError("quantizer changed during search")
    raw = {"schema": "r5.emeans_vs_lloyd.synthetic_smoke.v2", "status": "PASS",
        "execution_status": "EXECUTED", "production_activation": False, "disposition": "DEFER",
        "dataset_kind": "synthetic_smoke", "dataset": split_evidence(fixture),
        "configuration": {"seed": SEED, "dimension": DIMENSION, "centroids": CENTROIDS,
            "passes": PASSES, "assignment_rows_budget": PASSES * TRAIN_ROWS,
            "distance_evaluation_budget": PASSES * TRAIN_ROWS * CENTROIDS,
            "init_centroids_sha256": digest(init), "train_repeats": TRAIN_REPEATS,
            "train_warmups_per_method": 1, "search_repeats": SEARCH_REPEATS,
            "search_warmups_per_query": SEARCH_WARMUPS, "top_ks": [10, 100]},
        "provenance": provenance,
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "faiss": faiss.__version__,
            "platform": platform.platform(), "python_implementation": platform.python_implementation(),
            "faiss_compile_options": faiss.get_compile_options(),
            "numpy_build_configuration": np.__config__.show(mode="dicts"),
            "cpu": cpu_model(), "threads": 1, "faiss_threads": faiss.omp_get_max_threads(),
            "blas_thread_env": {name: os.environ[name] for name in THREAD_VARIABLES}},
        "training_budget_semantics": "ten passes happen to equal the measured full-row centroid-distance assignment budget here; not equal update count or wall time",
        "exact_oracle": "independent NumPy FP64 squared L2 with stable base-ID ties, checked against IndexFlatL2",
        "timing_semantics": "per-query repeated warm samples, warmups excluded; not independent requests or stable p99; scopes not compared as speedups",
        "methods": methods, "limitations": [
            "Synthetic fixture only; no representative R4 corpus or production recommendation.",
            "Native IVF nprobe rows have unequal scans; equal_scan_proxy is separate Python routing plus exact rerank, not native IVF latency.",
            "Assignment work excludes update/revival arithmetic, memory traffic and backend overhead; wall time is not matched.",
            "O-means, GPU training, peak RSS and portable native serving were not measured."]}
    write_evidence(raw, output, receipt, sidecar)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=(1,), default=1)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    run(args.output, args.receipt, args.sidecar, args.expected_head)
    print(json.dumps({"status": "PASS", "output": str(args.output)}))


if __name__ == "__main__":
    main()
