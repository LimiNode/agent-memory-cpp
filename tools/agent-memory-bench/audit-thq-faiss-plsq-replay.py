#!/usr/bin/env python3
"""Independent persisted-code and PLSQ decode/ranking audit."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

D, THQ_BYTES, QUERY_COUNT, TOP = 384, 96, 152, 128

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def unpack_thq(codes: np.ndarray) -> np.ndarray:
    v = np.asarray(codes, dtype=np.uint8)
    out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]
        out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3,
                                      (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out

def top_ids(values: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(values, dtype=np.float64)))[:10]]

def ndcg10(ids: list[int], qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(grade) for doc, grade in zip(qids, grades)
           if int(doc) >= 0 and float(grade) > 0}
    gains = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** grade - 1.0 for grade in rel.values()]))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    idcg = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / idcg) if idcg else 0.0

def decode_plsq(codes: np.ndarray, model: dict, nsplits: int, msub: int) -> np.ndarray:
    n = len(codes); dsub = D // nsplits
    out = np.zeros((n, D), dtype=np.float32)
    for split in range(nsplits):
        cb = np.asarray(model[f"split_{split}_codebooks"], dtype=np.float32).reshape(-1, dsub)
        offsets = np.asarray(model[f"split_{split}_offsets"], dtype=np.int64)
        local = np.zeros((n, dsub), dtype=np.float32)
        for j in range(msub):
            ids = np.asarray(codes[:, split * msub + j], dtype=np.int64)
            if np.any(ids < 0) or np.any(ids >= offsets[j + 1] - offsets[j]):
                raise RuntimeError("PLSQ code outside persisted codebook")
            local += cb[offsets[j] + ids]
        out[:, split * dsub:(split + 1) * dsub] = local
    return out

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--result", type=Path); p.add_argument("--runner", type=Path); p.add_argument("--codes", type=Path)
    p.add_argument("--model", type=Path); p.add_argument("--documents", type=Path)
    p.add_argument("--queries", type=Path); p.add_argument("--qrel-ids", type=Path)
    p.add_argument("--qrel-scores", type=Path); p.add_argument("--thq4-codes", type=Path)
    p.add_argument("--output", type=Path); p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        x = np.zeros((1, 384), dtype=np.uint8); assert unpack_thq(np.zeros((1, 96), dtype=np.uint8)).shape == x.shape
        model = {}
        for split in range(2):
            cb = np.zeros((512, 192), dtype=np.float32)
            cb[7, 0] = 1.0 + split; cb[256 + 4, 0] = 3.0 + split
            model[f"split_{split}_codebooks"] = cb.reshape(-1)
            model[f"split_{split}_offsets"] = np.asarray([0, 256, 512])
        decoded = decode_plsq(np.asarray([[7, 4, 7, 4]], dtype=np.uint8), model, 2, 2)
        assert decoded.shape == (1, D) and decoded[0, 0] == 4.0 and decoded[0, 192] == 6.0
        assert ndcg10([10], np.asarray([10]), np.asarray([1.0])) == 1.0
        print("audit-thq-faiss-plsq-replay self-test: PASS"); return
    if any(x is None for x in (a.result, a.runner, a.codes, a.model, a.documents, a.queries,
                               a.qrel_ids, a.qrel_scores, a.thq4_codes, a.output)):
        p.error("all source, artifact and output paths are required")
    result = json.loads(a.result.read_text(encoding="utf-8"))
    if result.get("status") != "EXECUTED" or result.get("quality_status") != "BOUNDED_PLSQ_CONTROL" or result.get("schema_version") != 4 or result.get("family") != "thq_faiss_plsq_replay_v4":
        raise RuntimeError("unexpected PLSQ status/schema")
    profile = result.get("plsq", {}); expected = {"8x4x8": (8, 4, 8), "8x6x8": (8, 6, 8)}.get(profile.get("profile"))
    if expected is None: raise RuntimeError("unknown PLSQ profile")
    nsplits, msub, nbits = expected; expected_bytes = nsplits * msub * nbits // 8
    if (profile.get("nsplits"), profile.get("msub"), profile.get("nbits"), profile.get("code_bytes")) != (nsplits, msub, nbits, expected_bytes):
        raise RuntimeError("PLSQ constructor/code-byte provenance differs")
    seeds = profile.get("random_seed_by_split")
    if not isinstance(seeds, list) or len(seeds) != nsplits or any(not isinstance(seed, int) for seed in seeds):
        raise RuntimeError("PLSQ split seed provenance differs")
    if result.get("faiss_version") != "1.15.0" or int(result.get("faiss_omp_threads", 0)) != int(profile.get("faiss_threads", -1)):
        raise RuntimeError("PLSQ Faiss provenance differs")
    hardware = result.get("hardware", {})
    if not isinstance(hardware.get("cpu_model"), str) or not hardware["cpu_model"]:
        raise RuntimeError("PLSQ hardware provenance differs")
    if result.get("model_sha256") != sha256(a.model) or result.get("codes_sha256") != sha256(a.codes):
        raise RuntimeError("PLSQ artifact SHA differs")
    if result.get("runner_sha256") != sha256(a.runner):
        raise RuntimeError("PLSQ runner SHA differs")
    source_hashes = result.get("source_hashes", {})
    audit_sources = {"documents": a.documents, "queries": a.queries,
                     "qrel-ids": a.qrel_ids, "qrel-scores": a.qrel_scores,
                     "thq4-codes": a.thq4_codes}
    if any(source_hashes.get(name) != sha256(path) for name, path in audit_sources.items()):
        raise RuntimeError("PLSQ audit source SHA differs")
    with np.load(a.model, allow_pickle=False) as payload: model = {k: np.asarray(payload[k]) for k in payload.files}
    centroids = np.asarray(model.get("centroids"), dtype=np.float32)
    if centroids.shape != (D, 4) or str(np.asarray(model.get("profile")).reshape(-1)[0]) != profile["profile"]: raise RuntimeError("PLSQ model metadata differs")
    with np.load(a.codes, allow_pickle=False) as payload:
        selected = np.asarray(payload["selected_ids"], dtype=np.int64); codes = np.asarray(payload["codes"], dtype=np.uint8)
        persisted_norms = np.asarray(payload["final_norms"], dtype=np.float32)
    if selected.shape != (QUERY_COUNT, TOP) or codes.shape != (QUERY_COUNT, TOP, expected_bytes) or persisted_norms.shape != (QUERY_COUNT, TOP): raise RuntimeError("PLSQ persisted shape differs")
    if np.any(selected < 0) or np.any(selected >= 1_000_000) or any(len(np.unique(row)) != TOP for row in selected): raise RuntimeError("PLSQ selected IDs differ")
    if not np.isfinite(persisted_norms).all() or np.any(persisted_norms <= 0): raise RuntimeError("PLSQ persisted norms differ")
    if a.documents.stat().st_size != 1_000_000 * D * 4: raise RuntimeError("PLSQ document source size differs")
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D)); qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)); qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)); thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    rows = result.get("rows", []); mismatches = []; ndcg_values = []
    if len(rows) != QUERY_COUNT: raise RuntimeError("PLSQ result row count differs")
    for qi in range(QUERY_COUNT):
        ids = selected[qi]; decoded = decode_plsq(codes[qi], model, nsplits, msub)
        levels = unpack_thq(np.asarray(thq[ids])); base = centroids[np.arange(D)[None, :], levels]; reconstructed = base + decoded
        norms = np.linalg.norm(np.asarray(reconstructed, dtype=np.float64), axis=1).astype(np.float32)
        if not np.allclose(norms, persisted_norms[qi], rtol=1e-6, atol=1e-6): raise RuntimeError(f"PLSQ persisted norm mismatch at query {qi}")
        q = np.asarray(queries[qi], dtype=np.float64); x = np.asarray(reconstructed, dtype=np.float64); scores = (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny)
        ranked = top_ids(scores, ids).astype(int).tolist(); expected_ids = rows[qi].get("top10_ids")
        if ranked != expected_ids: mismatches.append({"query": qi, "expected": expected_ids, "actual": ranked})
        score = ndcg10(ranked, qrel_ids[qi], qrel_scores[qi]); ndcg_values.append(score)
        if not np.isclose(score, float(rows[qi]["qrels_ndcg10"]), rtol=0.0, atol=1e-12): raise RuntimeError(f"PLSQ nDCG mismatch at query {qi}")
    if mismatches: raise RuntimeError(f"independent PLSQ decode ranking mismatches: {mismatches[:2]}")
    mean_ndcg = float(np.mean(ndcg_values))
    if not np.isclose(mean_ndcg, float(result["summary"]["mean_qrels_ndcg10"]), rtol=0.0, atol=1e-12): raise RuntimeError("PLSQ summary nDCG mismatch")
    audit = {"schema_version": 4, "family": "thq_faiss_plsq_replay_audit_v4", "status": "PASS", "result_sha256": sha256(a.result), "codes_sha256": sha256(a.codes), "model_sha256": sha256(a.model), "quality_status": result["quality_status"], "profile": profile, "rows": QUERY_COUNT, "code_shape": list(codes.shape), "selected_shape": list(selected.shape), "independent_decode": True, "ranking_mismatches": 0, "recomputed_mean_qrels_ndcg10": mean_ndcg, "persisted_norms_verified": True, "summary": result.get("summary", {}), "limitations": ["independent additive decode/ranking/nDCG replay uses persisted PLSQ codebooks", "bounded candidate-local control; no production selection claim"]}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps({"status": "PASS", "rows": QUERY_COUNT, "independent_decode": True}, sort_keys=True))

if __name__ == "__main__": main()
