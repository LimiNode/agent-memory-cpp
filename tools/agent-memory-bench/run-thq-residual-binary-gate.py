#!/usr/bin/env python3
"""Source-bound THQ4 -> residual binary Gate C (cosine lane)."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
ARMS = ("rabitq_like", "bbq_like")
CORRECTIONS = ("code_only", "scale")
SOURCE_NAMES = ("documents", "train_vectors", "queries", "qrel_ids", "qrel_scores", "teacher_ids", "thq4_codes", "thq4_thresholds", "candidate_flat", "candidate_raw", "candidate_receipt")

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()
def top_ids(scores, ids, n=10): return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:n]]
def cosine(values, query):
    x, q = np.asarray(values, dtype=np.float64), np.asarray(query, dtype=np.float64); return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny)
def unpack_thq(codes):
    v = np.asarray(codes, dtype=np.uint8); out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]; out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out
def interval_top(query, ids, codes, thresholds):
    levels = unpack_thq(np.asarray(codes[ids])); lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level-1]; high = np.inf if level == 3 else thresholds[d, level]; delta = low-query[d] if query[d] < low else query[d]-high if query[d] > high else 0.0; lut[d, level] = delta * delta
    return ids[np.lexsort((ids, np.sum(lut[np.arange(D)[None, :], levels], axis=1)))[:min(TOP, len(ids))]]
def ndcg10(ids, qids, grades):
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}; gains = np.asarray([2.0 ** rel.get(int(d), 0.0)-1 for d in ids[:10]]); ideal = np.sort(np.asarray([2.0 ** g-1 for g in rel.values()]))[::-1][:10]; den = np.sum(ideal/np.log2(np.arange(2,2+len(ideal)))) if len(ideal) else 0.0; return float(np.sum(gains/np.log2(np.arange(2,2+len(gains)))/den)) if den else 0.0
def self_test():
    bits = np.packbits(np.asarray([[True, False, True, False, True, False, True, False]], dtype=bool), bitorder="little"); assert bits[0] == 0x55; print("THQ residual binary Gate C self-test: PASS")
def main():
    p = argparse.ArgumentParser(); p.add_argument("--self-test", action="store_true")
    names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "models-output", "codes-output")
    for n in names: p.add_argument(f"--{n}", dest=n.replace("-", "_"), type=Path)
    p.add_argument("--seed", type=int, default=20260921); a = p.parse_args()
    if a.self_test: self_test(); return
    if any(getattr(a, n.replace("-", "_")) is None for n in names): p.error("all source and output paths are required")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000,D)); train_n = a.train_vectors.stat().st_size // (D*4); train = np.asarray(np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(train_n,D)), dtype=np.float32); queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT,D)); qids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT,20)); qscores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT,20)); teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT,10)); thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000,THQ_BYTES)); thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(D,3)
    raw = json.loads(a.candidate_raw.read_text(encoding="utf-8")); counts = np.asarray([int(r["candidate_count"]) for r in raw["rows"]], dtype=np.int64); offsets = np.concatenate(([0], np.cumsum(counts))); records = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]),148)); candidate_ids = np.asarray(records[:,:4]).copy().view("<i4").reshape(-1).astype(np.int64); receipt = json.loads(a.candidate_receipt.read_text(encoding="utf-8"));
    if receipt.get("execution_status") != "EXECUTED" or receipt.get("raw_sha256") != sha256(a.candidate_raw) or receipt.get("flat_file",{}).get("sha256") != sha256(a.candidate_flat): raise RuntimeError("candidate receipt binding differs")
    train_levels = np.sum(train[:,:,None] > thresholds[None,:,:], axis=2, dtype=np.uint8); centroids = np.empty((D,4),np.float32); fallback=np.mean(train,0)
    for d in range(D):
        for level in range(4):
            v=train[train_levels[:,d]==level,d]; centroids[d,level]=np.mean(v) if len(v) else fallback[d]
    unique = np.unique(candidate_ids); unique_levels=unpack_thq(np.asarray(thq[unique])); base=centroids[np.arange(D)[None,:],unique_levels]; residual=np.asarray(docs[unique],np.float32)-base; rng=np.random.default_rng(a.seed); rotation=np.linalg.qr(rng.normal(size=(D,D)))[0].astype(np.float32); transformed=residual@rotation
    signs_r=np.packbits(transformed>=0,axis=1,bitorder="little"); blocks=transformed.reshape(len(unique),8,48); signs_b=np.packbits(blocks>=0,axis=2,bitorder="little"); scale_r=np.divide(np.sum(transformed*transformed,1),np.sum(np.abs(transformed),1),out=np.zeros(len(unique)),where=np.sum(np.abs(transformed),1)>0).astype(np.float16); scale_b=np.divide(np.sum(blocks*blocks,2),np.sum(np.abs(blocks),2),out=np.zeros((len(unique),8)),where=np.sum(np.abs(blocks),2)>0).astype(np.float16); id_to_pos={int(x):i for i,x in enumerate(unique)}
    rows=[]; selected_all=[]
    for qi,q in enumerate(queries):
        ids=candidate_ids[offsets[qi]:offsets[qi+1]]; selected=interval_top(q,ids,thq,thresholds); selected_all.append(selected); pos=np.asarray([id_to_pos[int(x)] for x in selected]); selected_levels=unpack_thq(np.asarray(thq[selected])); selected_base=centroids[np.arange(D)[None,:],selected_levels]
        for arm in ARMS:
            sign = np.unpackbits(signs_r[pos],axis=1,bitorder="little")[:,:D].astype(np.float32) if arm == "rabitq_like" else np.unpackbits(signs_b[pos],axis=2,bitorder="little")[:,:,:48].reshape(len(pos),D).astype(np.float32); sign = sign*2.0-1.0; scale = scale_r[pos,None] if arm == "rabitq_like" else np.repeat(scale_b[pos],48,axis=1)
            for correction in CORRECTIONS:
                decoded=(sign if correction == "code_only" else sign*scale)@rotation.T; ranked=top_ids(cosine(selected_base+decoded,q),selected); payload=48+2 if arm == "rabitq_like" else 48+16; rows.append({"query":qi,"arm":arm,"correction":correction,"top10_ids":ranked.astype(int).tolist(),"thq4_top128_ids":selected.astype(int).tolist(),"qrels_ndcg10":ndcg10(ranked,qids[qi],qscores[qi]),"teacher_overlap":float(np.isin(teacher[qi],ranked).sum()/10),"side_payload_bytes":payload,"cascade_total_bytes":THQ_BYTES+payload})
    a.models_output.parent.mkdir(parents=True,exist_ok=True); a.codes_output.parent.mkdir(parents=True,exist_ok=True); np.savez_compressed(a.models_output,centroids=centroids,rotation=rotation,unique_ids=unique); np.savez_compressed(a.codes_output,rabitq_codes=signs_r,bbq_codes=signs_b,rabitq_scales=scale_r,bbq_scales=scale_b,selected_ids=np.stack(selected_all))
    sources={n:getattr(a,n.replace("-","_")) for n in SOURCE_NAMES}; summaries={}
    for arm in ARMS:
        for correction in CORRECTIONS:
            r=[x for x in rows if x["arm"]==arm and x["correction"]==correction]; summaries[f"{arm}:{correction}"]={"mean_qrels_ndcg10":float(np.mean([x["qrels_ndcg10"] for x in r])),"p05_qrels_ndcg10":float(np.percentile([x["qrels_ndcg10"] for x in r],5)),"worst_qrels_ndcg10":float(np.min([x["qrels_ndcg10"] for x in r]))}
    result={"schema_version":2,"family":"thq_residual_binary_gate_c_v2","status":"EXECUTED","source_replay":True,"metric":"cosine","runner_sha256":sha256(Path(__file__)),"seed":a.seed,"arms":list(ARMS),"corrections":list(CORRECTIONS),"scale_storage":"fp16","rotation_storage":"fp32","rotation_bytes":D*D*4,"source_hashes":{n:sha256(v) for n,v in sources.items()},"artifact_hashes":{"models":sha256(a.models_output),"codes":sha256(a.codes_output)},"query_count":QUERY_COUNT,"rows":rows,"summaries":summaries,"limitations":["local RaBitQ/BBQ-like residual references; not faithful TurboQuant/NEQ","cosine lane intentionally excludes norm-explicit IP correction","candidate-local materialization; held-out confirmation pending"]}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
if __name__ == "__main__": main()
