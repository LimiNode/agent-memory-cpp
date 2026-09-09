#!/usr/bin/env python3
"""Evaluate one mature ANN baseline against the frozen DE-1M R4 queries."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

ENGINES = {"faiss_exact_flat", "faiss_float_ivf", "faiss_float_hnsw",
           "faiss_binary_flat", "faiss_binary_ivf", "faiss_binary_hnsw",
           "historical_mih"}
DIMENSIONS = 384
BITS = 256
CANDIDATES = 5000
HAMMING_LIMIT = 768
ADC_LIMIT = 64
TOP_K = 10
POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)],
                      dtype=np.uint8)


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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def timing(values: list[float]) -> dict[str, float]:
    return {"mean": statistics.fmean(values),
            "p50": percentile(values, .50),
            "p95": percentile(values, .95),
            "p99": percentile(values, .99)}


def ids(path: Path) -> list[str]:
    return [json.loads(line)["id"] for line in path.read_text(
        encoding="utf-8").splitlines() if line]


def qrels(path: Path) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        query, _, document, relevance = line.split()
        result.setdefault(query, {})[document] = float(relevance)
    return result


def ndcg(top: list[int], query_id: str, document_ids: list[str],
         relevance: dict[str, dict[str, float]]) -> float:
    gains = relevance.get(query_id, {})
    actual = sum((2.0 ** gains.get(document_ids[position], 0.0) - 1.0) /
                 math.log2(rank + 2.0) for rank, position in enumerate(top))
    ideal_values = sorted(gains.values(), reverse=True)[:TOP_K]
    ideal = sum((2.0 ** value - 1.0) / math.log2(rank + 2.0)
                for rank, value in enumerate(ideal_values))
    return actual / ideal if ideal else 0.0


def stable_top(candidates: np.ndarray, scores: np.ndarray,
               ranks: np.ndarray, count: int, higher: bool) -> np.ndarray:
    order = np.lexsort((ranks[candidates], -scores if higher else scores))
    return candidates[order[:count]].astype(np.int32, copy=False)


class Data:
    def __init__(self, manifest_path: Path, r4_protocol_path: Path) -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(manifest["document_count"] == 1_000_000 and
                manifest["query_count"] == 305 and
                manifest["embedding_dimension"] == DIMENSIONS and
                manifest["code_bits"] == BITS,
                "external baseline input shape differs")
        root = manifest_path.parent
        self.documents = np.memmap(root / manifest["document_vectors_file"],
            dtype="<f4", mode="r", shape=(1_000_000, DIMENSIONS))
        self.queries = np.memmap(root / manifest["query_vectors_file"],
            dtype="<f4", mode="r", shape=(305, DIMENSIONS))
        self.codes = np.memmap(root / manifest["document_codes_file"],
            dtype=np.uint8, mode="r", shape=(1_000_000, 32))
        self.query_codes = np.memmap(root / manifest["query_codes_file"],
            dtype=np.uint8, mode="r", shape=(305, 32))
        self.projections = np.memmap(root /
            manifest["query_itq_projections_file"], dtype="<f4", mode="r",
            shape=(305, BITS))
        self.centroids = np.memmap(root /
            manifest["binary_adc_centroids_file"], dtype="<f4", mode="r",
            shape=(BITS, 2))
        protocol = json.loads(r4_protocol_path.read_text(encoding="utf-8"))
        self.native_queries = np.asarray([row["native_query"] for row in
                                          protocol["requests"]], dtype=np.int64)
        self.request_rows = np.asarray([row["request"] for row in
                                        protocol["requests"]], dtype=np.int64)
        self.query_ids_path = Path(protocol["evaluation_query_ids"])
        self.document_ids_path = Path(protocol["evaluation_document_ids"])
        self.qrels_path = Path(protocol["evaluation_qrels"])
        self.ranks = np.fromfile(Path(protocol["document_id_rank_file"]),
                                 dtype="<u4")
        require(self.native_queries.size == 76 and self.ranks.size == 1_000_000,
                "external baseline R4 query map differs")
        self.query_ids = ids(self.query_ids_path)
        self.document_ids = ids(self.document_ids_path)
        self.relevance = qrels(self.qrels_path)

    def exact(self, candidates: np.ndarray, local_query: int,
              count: int = TOP_K) -> np.ndarray:
        native = int(self.native_queries[local_query])
        scores = np.asarray(self.documents[candidates] @ self.queries[native],
                            dtype=np.float32)
        return stable_top(candidates, scores, self.ranks, count, True)

    def hamming(self, candidates: np.ndarray, local_query: int) -> np.ndarray:
        native = int(self.native_queries[local_query])
        distances = POPCOUNT[np.bitwise_xor(self.codes[candidates],
                                             self.query_codes[native])].sum(
                                                 axis=1, dtype=np.uint16)
        return stable_top(candidates, distances, self.ranks,
                          min(HAMMING_LIMIT, candidates.size), False)

    def adc(self, candidates: np.ndarray, local_query: int) -> np.ndarray:
        native = int(self.native_queries[local_query])
        symbols = np.unpackbits(self.codes[candidates], axis=1,
                                bitorder="little")
        selected = self.centroids[np.arange(BITS)[None, :], symbols]
        scores = np.square(self.projections[native][None, :] - selected).sum(
            axis=1, dtype=np.float32)
        return stable_top(candidates, scores, self.ranks,
                          min(ADC_LIMIT, candidates.size), False)


def faiss_module() -> Any:
    try:
        import faiss
    except ImportError as error:
        raise ValueError("faiss-cpu is required") from error
    return faiss


def build_index(engine: str, data: Data, index_path: Path,
                rebuild: bool) -> tuple[Any, float, int, int]:
    try:
        import psutil
    except ImportError as error:
        raise ValueError("psutil is required for external build accounting") from error
    faiss = faiss_module()
    binary = "binary" in engine
    reader = faiss.read_index_binary if binary else faiss.read_index
    writer = faiss.write_index_binary if binary else faiss.write_index
    if index_path.is_file() and not rebuild:
        return reader(str(index_path)), 0.0, index_path.stat().st_size, 0
    process = psutil.Process()
    before = process.memory_info().rss
    begin = time.perf_counter()
    faiss.omp_set_num_threads(min(16, os.cpu_count() or 1))
    if engine == "faiss_exact_flat":
        index = faiss.IndexFlatIP(DIMENSIONS)
        index.add(np.asarray(data.documents, dtype=np.float32))
    elif engine == "faiss_float_ivf":
        quantizer = faiss.IndexFlatIP(DIMENSIONS)
        index = faiss.IndexIVFFlat(quantizer, DIMENSIONS, 4096,
                                   faiss.METRIC_INNER_PRODUCT)
        training = np.asarray(data.documents[::5][:200_000], dtype=np.float32)
        index.train(training)
        index.add(np.asarray(data.documents, dtype=np.float32))
    elif engine == "faiss_float_hnsw":
        index = faiss.IndexHNSWFlat(DIMENSIONS, 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = 200
        index.add(np.asarray(data.documents, dtype=np.float32))
    elif engine == "faiss_binary_flat":
        index = faiss.IndexBinaryFlat(BITS)
        index.add(np.asarray(data.codes, dtype=np.uint8))
    elif engine == "faiss_binary_ivf":
        quantizer = faiss.IndexBinaryFlat(BITS)
        index = faiss.IndexBinaryIVF(quantizer, BITS, 4096)
        training = np.asarray(data.codes[::5][:200_000], dtype=np.uint8)
        index.train(training)
        index.add(np.asarray(data.codes, dtype=np.uint8))
    elif engine == "faiss_binary_hnsw":
        index = faiss.IndexBinaryHNSW(BITS, 32)
        index.hnsw.efConstruction = 200
        index.add(np.asarray(data.codes, dtype=np.uint8))
    else:
        raise ValueError("historical MIH does not build a Faiss index")
    build_seconds = time.perf_counter() - begin
    peak_delta = max(0, process.memory_info().rss - before)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    writer(index, str(index_path))
    return index, build_seconds, index_path.stat().st_size, peak_delta


def parameters(engine: str) -> list[tuple[str, int]]:
    if engine in {"faiss_float_ivf", "faiss_binary_ivf"}:
        return [(f"nprobe_{value}", value) for value in (8, 32, 128, 512)]
    if engine in {"faiss_float_hnsw", "faiss_binary_hnsw"}:
        return [(f"ef_search_{value}", value) for value in (512, 2048, 8192)]
    return [("fixed", 0)]


def configure(index: Any, engine: str, value: int) -> None:
    if "ivf" in engine:
        index.nprobe = value
    if "hnsw" in engine:
        index.hnsw.efSearch = value


def search_one(index: Any, engine: str, data: Data,
               local_query: int) -> np.ndarray:
    native = int(data.native_queries[local_query])
    if "binary" in engine:
        query = np.ascontiguousarray(data.query_codes[native:native + 1])
        count = HAMMING_LIMIT if engine == "faiss_binary_flat" else CANDIDATES
    else:
        query = np.ascontiguousarray(data.queries[native:native + 1])
        count = TOP_K if engine == "faiss_exact_flat" else CANDIDATES
    _, labels = index.search(query, count)
    return labels[0][labels[0] >= 0].astype(np.int32, copy=False)


def downstream(engine: str, data: Data, local_query: int,
               candidates: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    if engine == "faiss_exact_flat":
        return candidates[:TOP_K], 0.0, 0.0, 0.0
    if "float" in engine:
        begin = time.perf_counter()
        top = data.exact(candidates, local_query)
        return top, 0.0, 0.0, (time.perf_counter() - begin) * 1000.0
    begin = time.perf_counter()
    hamming = data.hamming(candidates, local_query)
    hamming_ms = (time.perf_counter() - begin) * 1000.0
    begin = time.perf_counter()
    adc = data.adc(hamming, local_query)
    adc_ms = (time.perf_counter() - begin) * 1000.0
    begin = time.perf_counter()
    top = data.exact(adc, local_query)
    exact_ms = (time.perf_counter() - begin) * 1000.0
    return top, hamming_ms, adc_ms, exact_ms


def oracle(data: Data, root: Path) -> np.ndarray:
    path = root / "exact-top10.i32.npy"
    if path.is_file():
        return np.load(path)
    index, _, _, _ = build_index("faiss_exact_flat", data,
                                  root / "faiss-exact-flat.index", False)
    faiss_module().omp_set_num_threads(1)
    _, labels = index.search(np.ascontiguousarray(
        data.queries[data.native_queries]), TOP_K)
    np.save(path, labels.astype(np.int32))
    return labels.astype(np.int32)


def evaluate_row(index: Any, engine: str, parameter: str, value: int,
                 data: Data, exact_top10: np.ndarray,
                 repeats: int) -> dict[str, Any]:
    configure(index, engine, value)
    faiss_module().omp_set_num_threads(1)
    for local in range(len(data.native_queries)):
        candidates = search_one(index, engine, data, local)
        downstream(engine, data, local, candidates)
    generator_ms: list[float] = []
    hamming_ms: list[float] = []
    adc_ms: list[float] = []
    exact_ms: list[float] = []
    total_ms: list[float] = []
    query_rows = []
    quality_seen = False
    for repeat in range(repeats):
        for local in range(len(data.native_queries)):
            begin = time.perf_counter()
            candidates = search_one(index, engine, data, local)
            generated = time.perf_counter()
            top, h_ms, a_ms, e_ms = downstream(engine, data, local, candidates)
            end = time.perf_counter()
            generator_ms.append((generated - begin) * 1000.0)
            hamming_ms.append(h_ms)
            adc_ms.append(a_ms)
            exact_ms.append(e_ms)
            total_ms.append((end - begin) * 1000.0)
            if not quality_seen:
                query_id = data.query_ids[int(data.native_queries[local])]
                query_rows.append({"local_query": local,
                    "native_query": int(data.native_queries[local]),
                    "query_id": query_id,
                    "candidate_count": int(candidates.size),
                    "exact_top10_recall": len(set(map(int, candidates)) &
                        set(map(int, exact_top10[local]))) / TOP_K,
                    "top10_overlap": len(set(map(int, top)) &
                        set(map(int, exact_top10[local]))) / TOP_K,
                    "ndcg_at_10": ndcg(list(map(int, top)), query_id,
                        data.document_ids, data.relevance),
                    "top10": list(map(int, top))})
        quality_seen = True
    throughput = []
    queries = np.ascontiguousarray(data.queries[data.native_queries])
    query_codes = np.ascontiguousarray(data.query_codes[data.native_queries])
    batch_count = (HAMMING_LIMIT if engine == "faiss_binary_flat" else
        TOP_K if engine == "faiss_exact_flat" else CANDIDATES)
    for workers in (1, 8, 16):
        faiss_module().omp_set_num_threads(workers)
        begin = time.perf_counter()
        _, labels = index.search(query_codes if "binary" in engine else queries,
                                 batch_count)
        generated = time.perf_counter()
        label_rows = [row[row >= 0].astype(np.int32, copy=False) for row in labels]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda item: downstream(engine, data, item[0], item[1]),
                          enumerate(label_rows)))
        end = time.perf_counter()
        throughput.append({"workers": workers,
            "candidate_qps": 1000.0 * len(label_rows) /
                ((generated - begin) * 1000.0),
            "full_retrieval_qps": 1000.0 * len(label_rows) /
                ((end - begin) * 1000.0)})
    return {"parameter": parameter, "value": value,
        "queries": len(query_rows),
        "candidate_generation_ms": timing(generator_ms),
        "hamming_ms": timing(hamming_ms), "adc_ms": timing(adc_ms),
        "exact_ms": timing(exact_ms), "full_retrieval_ms": timing(total_ms),
        "mean_candidate_count": statistics.fmean(
            row["candidate_count"] for row in query_rows),
        "mean_exact_top10_recall": statistics.fmean(
            row["exact_top10_recall"] for row in query_rows),
        "mean_top10_overlap": statistics.fmean(
            row["top10_overlap"] for row in query_rows),
        "mean_ndcg_at_10": statistics.fmean(
            row["ndcg_at_10"] for row in query_rows),
        "throughput": throughput, "query_rows": query_rows}


def historical_mih(args: argparse.Namespace, data: Data,
                   exact_top10: np.ndarray) -> dict[str, Any]:
    shortlist = json.loads(args.historical_mih_shortlist.read_text(
        encoding="utf-8"))
    native = json.loads(args.historical_mih_report.read_text(encoding="utf-8"))
    by_query = {row["query_position"]: row for row in shortlist["rows"]}
    rows = []
    for local, native_query in enumerate(data.native_queries):
        hamming = np.asarray(by_query[int(native_query)][
            "hamming_shortlist_positions"], dtype=np.int32)
        adc = data.adc(hamming, local)
        top = data.exact(adc, local)
        query_id = data.query_ids[int(native_query)]
        rows.append({"local_query": local, "native_query": int(native_query),
            "query_id": query_id, "candidate_count": int(hamming.size),
            "exact_top10_recall": len(set(map(int, hamming)) &
                set(map(int, exact_top10[local]))) / TOP_K,
            "top10_overlap": len(set(map(int, top)) &
                set(map(int, exact_top10[local]))) / TOP_K,
            "ndcg_at_10": ndcg(list(map(int, top)), query_id,
                data.document_ids, data.relevance), "top10": list(map(int, top))})
    return {"schema_version": 1,
        "family": "neuroute_external_baseline_report",
        "engine": "historical_mih", "historical_control": True,
        "input_manifest_sha256": sha256(args.input_manifest),
        "rows": [{"parameter": "m19_fixed_r56", "value": 56,
            "queries": len(rows),
            "candidate_generation_ms": native["latency_ms_per_query"][
                "candidate_generator_total"],
            "full_retrieval_ms": native["latency_ms_per_query"][
                "cascade_total"],
            "mean_candidate_count": 768.0,
            "mean_exact_top10_recall": statistics.fmean(
                row["exact_top10_recall"] for row in rows),
            "mean_top10_overlap": statistics.fmean(
                row["top10_overlap"] for row in rows),
            "mean_ndcg_at_10": statistics.fmean(
                row["ndcg_at_10"] for row in rows),
            "throughput": [], "query_rows": rows}],
        "index": {"serialized_bytes": native["backend"][
            "backend_index_logical_bytes"], "build_seconds": None,
            "peak_build_rss_delta_bytes": None},
        "limitations": ["historical native latency uses the original 305-query run",
            "quality was reevaluated with the frozen Hamming768/ADC64/exact10 cascade"]}


def evaluate(args: argparse.Namespace) -> None:
    require(args.engine in ENGINES, "external baseline engine differs")
    data = Data(args.input_manifest, args.r4_protocol)
    exact_top10 = oracle(data, args.artifact_root)
    if args.engine == "historical_mih":
        result = historical_mih(args, data, exact_top10)
    else:
        index_path = args.artifact_root / f"{args.engine}.index"
        index, build_seconds, index_bytes, peak = build_index(
            args.engine, data, index_path, args.rebuild_index)
        rows = [evaluate_row(index, args.engine, name, value, data,
                             exact_top10, args.timing_repeats)
                for name, value in parameters(args.engine)]
        result = {"schema_version": 1,
            "family": "neuroute_external_baseline_report",
            "engine": args.engine, "historical_control": False,
            "input_manifest_sha256": sha256(args.input_manifest),
            "r4_protocol_sha256": sha256(args.r4_protocol),
            "matrix": {"candidate_limit": CANDIDATES,
                "hamming_limit": HAMMING_LIMIT, "adc_limit": ADC_LIMIT,
                "exact_limit": TOP_K, "timing_repeats": args.timing_repeats},
            "index": {"path": str(index_path.resolve()),
                "serialized_bytes": index_bytes,
                "build_seconds": build_seconds,
                "peak_build_rss_delta_bytes": peak},
            "runtime": {"python": sys.version, "numpy": np.__version__,
                "faiss": getattr(faiss_module(), "__version__", "unknown")},
            "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    require(set(parameters("faiss_float_ivf")) == {
        ("nprobe_8", 8), ("nprobe_32", 32), ("nprobe_128", 128),
        ("nprobe_512", 512)}, "external baseline IVF ladder differs")
    require(percentile([1.0, 2.0, 3.0], .5) == 2.0,
            "external baseline percentile differs")
    print("NeuRoute external baseline self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=sorted(ENGINES))
    for name in ("input-manifest", "r4-protocol", "artifact-root",
                 "historical-mih-report", "historical-mih-shortlist",
                 "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--timing-repeats", type=int, default=2)
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("engine", "input_manifest", "r4_protocol",
                    "artifact_root", "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("engine and baseline paths are required")
        if (args.engine == "historical_mih" and
                (args.historical_mih_report is None or
                 args.historical_mih_shortlist is None)):
            parser.error("historical MIH paths are required")
        evaluate(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"evaluate-neuroute-external-baseline: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
