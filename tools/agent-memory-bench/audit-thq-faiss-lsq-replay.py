#!/usr/bin/env python3
"""Independent audit for persisted Faiss LSQ32/LSQ48 replay artifacts."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
PAYLOADS = (32, 48)

def require(ok, msg):
    if not ok: raise RuntimeError(msg)
def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()
def unpack_thq(codes):
    v = np.asarray(codes, dtype=np.uint8); out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]; out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out
def top_ids(scores, ids): return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]
def cosine(values, query):
    x, q = np.asarray(values, dtype=np.float64), np.asarray(query, dtype=np.float64); den = np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny); return (x @ q) / den
def ndcg10(ids, qids, grades):
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) )
    idcg = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / idcg) if idcg else 0.0
def decode(codebooks, offsets, codes):
    m = len(offsets) - 1; out = np.zeros((len(codes), D), dtype=np.float64)
    require(codebooks.shape[1] == D, "LSQ codebooks must contain full-dimensional additive vectors")
    for j in range(m): out += codebooks[offsets[j] + codes[:, j]]
    return out
def fit_centroids(train, thresholds):
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8); out = np.empty((D, 4), dtype=np.float32); fallback = np.mean(train, axis=0).astype(np.float32)
    for d in range(D):
        for level in range(4):
            v = train[levels[:, d] == level, d]; out[d, level] = float(np.mean(v)) if len(v) else fallback[d]
    return out
def interval_top(query, ids, codes, thresholds):
    levels = unpack_thq(np.asarray(codes[ids])); lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level-1]; high = np.inf if level == 3 else thresholds[d, level]; delta = low-query[d] if query[d] < low else query[d]-high if query[d] > high else 0.0; lut[d, level] = delta * delta
    return ids[np.lexsort((ids, np.sum(lut[np.arange(D)[None, :], levels], axis=1)))[:min(TOP, len(ids))]]
def self_test():
    cb = np.zeros((512, D), dtype=np.float32); cb[7, 0] = 1.0; cb[260, 0] = -2.0; codes = np.asarray([[7, 4]], dtype=np.uint8); out = decode(cb, np.asarray([0, 256, 512]), codes); require(out[0, 0] == -1.0, "LSQ additive decoder self-test failed"); print("THQ Faiss LSQ audit self-test: PASS")
def main():
    p = argparse.ArgumentParser(); p.add_argument("--self-test", action="store_true")
    names = ("result", "runner", "models", "codes", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output")
    for n in names: p.add_argument(f"--{n}", dest=n.replace("-", "_"), type=Path)
    a = p.parse_args()
    if a.self_test: self_test(); return
    input_names = names[:-1]
    req = [getattr(a, n.replace("-", "_")) for n in input_names]; require(all(x is not None and x.is_file() for x in req), "all audit inputs must be files")
    require(a.output is not None, "--output is required")
    result = json.loads(a.result.read_text(encoding="utf-8")); require(result.get("family") == "thq_faiss_lsq_replay_v2" and result.get("status") == "EXECUTED" and result.get("source_replay") is True, "result is not LSQ source-replay evidence"); hardened_provenance = int(result.get("schema_version", 0)) >= 6; require(result.get("runner_sha256") == sha256(a.runner) if hardened_provenance else isinstance(result.get("runner_sha256"), str), "runner SHA differs"); require(result.get("query_count") == QUERY_COUNT and result.get("metric") == "cosine" and result.get("faiss_version") == "1.15.0", "LSQ protocol differs"); config = result.get("lsq_config", {}); require(set(config) == {"train_iters", "train_ils_iters", "encode_ils_iters", "icm_iters", "nperts", "random_seed", "train_rows", "base_train_rows"}, "LSQ effective configuration is missing"); require(all(int(config[key]) > 0 for key in config), "LSQ effective configuration is not positive")
    payloads = tuple(int(value) for value in result.get("payloads", ()))
    require(payloads and len(set(payloads)) == len(payloads) and all(value in PAYLOADS for value in payloads), "LSQ payload declaration differs")
    require(int(result.get("lsq_seed_base")) == int(config["random_seed"]), "LSQ seed base differs")
    require(result.get("effective_seed_by_payload") == {str(value): int(config["random_seed"]) for value in payloads}, "effective LSQ seed provenance differs")
    for timing_name in ("fit_seconds_by_payload", "candidate_union_encode_seconds_by_payload"):
        timings = result.get(timing_name, {})
        require(set(timings) == {str(value) for value in payloads} and all(np.isfinite(float(value)) and float(value) >= 0.0 for value in timings.values()), f"LSQ timing declaration differs: {timing_name}")
    require(int(result.get("candidate_union_documents", 0)) > 0, "candidate union cardinality is missing")
    require(set(result.get("candidate_union_encode_docs_per_second_by_payload", {})) == {str(value) for value in payloads}, "LSQ encode throughput declaration differs")
    require(all(np.isfinite(float(value)) and float(value) >= 0.0 for value in result["candidate_union_encode_docs_per_second_by_payload"].values()), "LSQ encode throughput is invalid")
    hardware = result.get("hardware", {})
    hardened_provenance = int(result.get("schema_version", 0)) >= 6
    if hardened_provenance:
        require(isinstance(result.get("faiss_compile_options"), str) and result["faiss_compile_options"], "Faiss compile options are missing")
        require(isinstance(hardware.get("cpu_model"), str) and hardware["cpu_model"], "CPU model is missing")
        physical_cores = int(hardware.get("cpu_physical_cores", 0))
        logical_cores = int(hardware.get("cpu_logical_cores", 0))
        require(0 < physical_cores <= logical_cores, "CPU core provenance differs")
        require(0 < int(result.get("faiss_omp_threads", 0)) <= logical_cores, "Faiss OMP thread provenance differs")
    source_names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt")
    source_hashes = {n: sha256(getattr(a, n.replace("-", "_"))) for n in source_names}; require(result.get("source_hashes") == source_hashes, "source SHA binding differs"); require(result.get("artifact_hashes") == {"models": sha256(a.models), "codes": sha256(a.codes)}, "artifact SHA binding differs")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D)); train_n = a.train_vectors.stat().st_size // (D * 4); train_rows = int(result.get("lsq_train_rows", config["train_rows"])); base_train_rows = int(result.get("base_train_rows", config["base_train_rows"])); require(256 <= train_rows <= train_n and 256 <= base_train_rows <= train_n, "LSQ train row count differs"); train_all = np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(train_n, D)); train = np.asarray(train_all[:train_rows], dtype=np.float32); base_train = np.asarray(train_all[:base_train_rows], dtype=np.float32); queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)); qids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)); qscores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)); teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)); thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(D, 3); thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    raw = json.loads(a.candidate_raw.read_text(encoding="utf-8")); counts = np.asarray([int(r["candidate_count"]) for r in raw["rows"]], dtype=np.int64); offsets = np.concatenate(([0], np.cumsum(counts))); rec = json.loads(a.candidate_receipt.read_text(encoding="utf-8")); record_bytes = int(rec.get("flat_file", {}).get("record_bytes", 148)); require(record_bytes in (100, 148) and a.candidate_flat.stat().st_size == int(offsets[-1]) * record_bytes, "candidate record layout differs"); records = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), record_bytes)); candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64); require(rec.get("raw_sha256") == sha256(a.candidate_raw) and rec.get("flat_file", {}).get("sha256") == sha256(a.candidate_flat), "candidate receipt differs")
    with np.load(a.models, allow_pickle=False) as m: centroids = np.asarray(m["centroids"], dtype=np.float32); models = {x: (np.asarray(m[f"lsq{x}_codebooks"], dtype=np.float64), np.asarray(m[f"lsq{x}_offsets"], dtype=np.int64)) for x in payloads}
    source_centroids = fit_centroids(base_train, thresholds)
    require(np.allclose(centroids, source_centroids, rtol=0.0, atol=1e-6), "persisted centroid table differs from source replay")
    require(all(models[x][0].shape[1] == D for x in payloads), "persisted LSQ codebooks are not full-dimensional")
    require(all(np.array_equal(models[x][1], np.arange(len(models[x][1]), dtype=np.int64) * 256) for x in payloads), "persisted LSQ offsets do not match the native stage*256 contract")
    with np.load(a.codes, allow_pickle=False) as c:
        selected_saved = np.asarray(c["selected_ids"], dtype=np.int64)
        codes_saved = {x: np.asarray(c[f"codes_{x}"], dtype=np.uint8) for x in payloads}
        norms_saved = {x: np.asarray(c[f"final_norms_{x}"], dtype=np.float32) for x in payloads}
    require(centroids.shape == (D, 4) and selected_saved.shape == (QUERY_COUNT, TOP), "artifact shape differs")
    require(all(norms_saved[x].shape == (QUERY_COUNT, TOP) and np.isfinite(norms_saved[x]).all() and (norms_saved[x] > 0).all() for x in payloads), "persisted norm sidecars are invalid")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT * len(payloads), "LSQ row cardinality differs")
    row_map = {}
    for row in rows:
        key = (int(row["query"]), row["arm"])
        require(key not in row_map, f"duplicate LSQ row: {key}")
        require(0 <= key[0] < QUERY_COUNT and key[1] in {f"faiss_lsq{m}" for m in payloads}, f"invalid LSQ row key: {key}")
        row_map[key] = row
    require(len(row_map) == QUERY_COUNT * len(payloads), "LSQ rows are not unique by query and arm")
    metrics = {m: [] for m in payloads}; overlaps = {m: [] for m in payloads}; teachers = {m: [] for m in payloads}
    for qi, query in enumerate(queries):
        ids = candidate_ids[offsets[qi]:offsets[qi+1]]; selected = interval_top(query, ids, thq, thresholds); require(np.array_equal(selected_saved[qi], selected), f"query {qi}: THQ selection differs"); lv = unpack_thq(np.asarray(thq[selected])); base = centroids[np.arange(D)[None, :], lv]; exact = top_ids(cosine(np.asarray(docs[ids]), query), ids)
        for m in payloads:
            reconstructed = base + decode(models[m][0], models[m][1], codes_saved[m][qi]); recomputed_norms = np.linalg.norm(reconstructed, axis=1).astype(np.float32); require(np.allclose(norms_saved[m][qi], recomputed_norms, rtol=0.0, atol=2e-6), f"query {qi}: persisted LSQ{m} norm sidecar differs"); norm_scores = (np.asarray(reconstructed, dtype=np.float64) @ np.asarray(query, dtype=np.float64)) / (np.maximum(norms_saved[m][qi], np.finfo(np.float64).tiny) * max(float(np.linalg.norm(query)), np.finfo(np.float64).tiny)); ranked = top_ids(norm_scores, selected); row = row_map[(qi, f"faiss_lsq{m}")]; require(row["top10_ids"] == ranked.astype(int).tolist() and row["thq4_top128_ids"] == selected.astype(int).tolist() and row["candidate_fp32_top10_ids"] == exact.astype(int).tolist(), f"query {qi}: LSQ{m} row differs"); require(int(row["base_train_rows"]) == base_train_rows and int(row["side_payload_bytes"]) == m + 4 and int(row["final_norm_sidecar_bytes"]) == 4 and int(row["cascade_total_bytes"]) == THQ_BYTES + m + 4, f"query {qi}: LSQ storage/protocol differs"); value = ndcg10(ranked, qids[qi], qscores[qi]); teacher_overlap = float(np.isin(teacher[qi], ranked).sum() / 10); candidate_overlap = float(row["candidate_fp32_overlap"]); require(abs(float(row["qrels_ndcg10"]) - value) < 1e-12, f"query {qi}: nDCG differs"); require(abs(candidate_overlap - float(np.isin(exact, ranked).sum() / 10)) < 1e-12, f"query {qi}: candidate overlap differs"); require(abs(float(row["teacher_overlap"]) - teacher_overlap) < 1e-12, f"query {qi}: teacher overlap differs"); metrics[m].append(value); overlaps[m].append(candidate_overlap); teachers[m].append(teacher_overlap)
    summaries = {}
    for m in payloads:
        key = f"faiss_lsq{m}"
        summaries[key] = {"mean_qrels_ndcg10": float(np.mean(metrics[m])), "p05_qrels_ndcg10": float(np.percentile(metrics[m], 5)), "worst_qrels_ndcg10": float(np.min(metrics[m])), "mean_candidate_fp32_overlap": float(np.mean(overlaps[m])), "mean_teacher_overlap": float(np.mean(teachers[m]))}
        recorded = result.get("summaries", {}).get(key)
        require(isinstance(recorded, dict), f"missing recorded summary: {key}")
        for field, value in summaries[key].items():
            require(abs(float(recorded.get(field, np.nan)) - value) < 1e-12, f"summary differs: {key}/{field}")
    audit = {"schema_version": 6, "family": "thq_faiss_lsq_replay_audit_v2", "status": "PASS", "source_replay": True, "persisted_code_decode_replay": True, "centroid_source_replay": True, "result_sha256": sha256(a.result), "runner_sha256": sha256(a.runner), "input_hashes": source_hashes, "artifact_hashes": {"models": sha256(a.models), "codes": sha256(a.codes)}, "query_count": QUERY_COUNT, "payloads": list(payloads), "lsq_seed_base": int(result["lsq_seed_base"]), "effective_seed_by_payload": result["effective_seed_by_payload"], "candidate_union_documents": int(result["candidate_union_documents"]), "fit_seconds_by_payload": result["fit_seconds_by_payload"], "candidate_union_encode_seconds_by_payload": result["candidate_union_encode_seconds_by_payload"], "candidate_union_encode_docs_per_second_by_payload": result["candidate_union_encode_docs_per_second_by_payload"], "faiss_omp_threads": int(result["faiss_omp_threads"]), "faiss_compile_options": result.get("faiss_compile_options"), "hardware": hardware, "performance_provenance": "HARDENED" if hardened_provenance else "LEGACY_MISSING_CPU_FIELDS", "row_count": len(rows), "unique_query_arm_count": len(row_map), "summaries": summaries, "checks": ["source/result/artifact SHA binding", "explicit effective Faiss seed provenance", "source-derived 25k THQ centroid equality", "independent THQ top128 replay", "independent LSQ subcodebook decode without Faiss", "cosine top10, overlap, teacher and nDCG replay", "final-norm sidecar, timing, union cardinality and logical storage replay", "summary replay"] + (["CPU/core, Faiss build and OMP-thread provenance"] if hardened_provenance else []), "limitations": ["Faiss fit/assignment is hash-bound, not independently retrained", "research LSQ control, not AVQ/AAQ/QINCo", "candidate-local side-code assignment benchmark; held-out confirmation pending"] + ([] if hardened_provenance else ["legacy result lacks CPU model, core counts, Faiss compile options and OMP provenance; timing is not suitable for cross-run speed comparison"])}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print("THQ Faiss LSQ replay audit PASS")
if __name__ == "__main__": main()
