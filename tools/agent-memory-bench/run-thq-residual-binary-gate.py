#!/usr/bin/env python3
"""THQ4 -> residual binary Gate C (research references, cosine semantics).

The residual codecs here are deliberately named ``rabitq_like`` and
``bbq_like``.  They are not claims of faithful TurboQuant/NEQ reproduction.
Each arm is an alternative scorer after the same THQ4 top-128 filter, with
three correction ablations: code-only, gain/scale, and gain+norm correction.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

D, TOP, QCOUNT, THQ_BYTES = 384, 128, 152, 96
ARMS = ("rabitq_like", "bbq_like")

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
def top_ids(scores, ids, n=10): return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:n]]
def cosine(x, q):
    x, q = np.asarray(x, dtype=np.float64), np.asarray(q, dtype=np.float64); return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), np.finfo(np.float64).tiny)
def unpack(c):
    v = np.asarray(c, dtype=np.uint8); out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]; out[:, 4*b:4*b+4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out
def ndcg(ids, qids, grades):
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}; gains = np.asarray([2.0 ** rel.get(int(d), 0.0)-1 for d in ids]); ideal = np.sort(np.asarray([2.0 ** g - 1 for g in rel.values()]))[::-1][:10]; den = np.sum(ideal / np.log2(np.arange(2, 2+len(ideal)))) if len(ideal) else 0; return float(np.sum(gains / np.log2(np.arange(2, 2+len(gains))) / den)) if den else 0.0
def self_test():
    x = np.asarray([[1., -2., 3., -4.]], dtype=np.float32); packed = np.packbits(x >= 0, axis=1, bitorder="little"); assert packed.shape == (1, 1); print("THQ residual binary Gate C self-test: PASS")
def main():
    p = argparse.ArgumentParser(); p.add_argument("--self-test", action="store_true")
    names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output", "models-output", "codes-output")
    for n in names: p.add_argument(f"--{n}", dest=n.replace("-", "_"), type=Path)
    p.add_argument("--seed", type=int, default=20260921); a = p.parse_args()
    if a.self_test: self_test(); return
    vals = [getattr(a, n.replace("-", "_")) for n in names];
    if any(v is None for v in vals): p.error("all source and output paths are required")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D)); ntrain = a.train_vectors.stat().st_size // (D*4); train = np.asarray(np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(ntrain, D)), dtype=np.float32); queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QCOUNT, D)); qids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QCOUNT,20)); qscores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QCOUNT,20)); teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QCOUNT,10)); thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES)); thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(D,3)
    raw = json.loads(a.candidate_raw.read_text(encoding="utf-8")); counts = np.asarray([int(r["candidate_count"]) for r in raw["rows"]]); offsets = np.concatenate(([0], np.cumsum(counts))); records = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]),148)); ids_all = np.asarray(records[:,:4]).copy().view("<i4").reshape(-1).astype(np.int64); unique = np.unique(ids_all)
    levels_train = np.sum(train[:,:,None] > thresholds[None,:,:], axis=2, dtype=np.uint8); centroids = np.empty((D,4), np.float32); fallback = np.mean(train,0)
    for d in range(D):
        for l in range(4):
            v=train[levels_train[:,d]==l,d]; centroids[d,l]=np.mean(v) if len(v) else fallback[d]
    lv_unique = unpack(np.asarray(thq[unique])); base_unique = centroids[np.arange(D)[None,:], lv_unique]; residual = np.asarray(docs[unique], np.float32)-base_unique
    rng=np.random.default_rng(a.seed); rotation,_=np.linalg.qr(rng.normal(size=(D,D))); transformed=residual@rotation; codes={"rabitq_like":np.packbits(transformed>=0,axis=1,bitorder="little"),"bbq_like":np.packbits(transformed.reshape(len(unique),8,48)>=0,axis=2,bitorder="little")}; scales={"rabitq_like":np.divide(np.sum(transformed*transformed,1),np.sum(np.abs(transformed),1),out=np.zeros(len(unique)),where=np.sum(np.abs(transformed),1)>0),"bbq_like":np.divide(np.sum(transformed.reshape(len(unique),8,48)**2,2),np.sum(np.abs(transformed.reshape(len(unique),8,48)),2),out=np.zeros((len(unique),8)),where=np.sum(np.abs(transformed.reshape(len(unique),8,48)),2)>0)}; id_to_pos={int(d):i for i,d in enumerate(unique)}
    rows=[]
    for qi,q in enumerate(queries):
        ids=ids_all[offsets[qi]:offsets[qi+1]]; lv=unpack(np.asarray(thq[ids])); dist=np.sum((centroids[np.arange(D)[None,:],lv]-q)**2,1); selected=ids[np.argsort(dist,kind="stable")[:TOP]]; pos=np.asarray([id_to_pos[int(d)] for d in selected]); base=centroids[np.arange(D)[None,:],unpack(np.asarray(thq[selected]))]
        for arm in ARMS:
            z = (np.where(np.unpackbits(codes[arm][pos], axis=2, bitorder="little")[:, :, :48], 1.0, -1.0)
                 .reshape(len(pos), D).astype(np.float32) if arm == "bbq_like" else
                 np.where(np.unpackbits(codes[arm][pos], axis=1, bitorder="little")[:, :D], 1.0, -1.0).astype(np.float32))
            scaled = z * (np.repeat(scales[arm][pos], 48, axis=1) if arm == "bbq_like" else scales[arm][pos, None])
            decoded=z@rotation.T
            for correction in ("code_only","scale","scale_norm"):
                decoded = z @ rotation.T if correction == "code_only" else scaled @ rotation.T
                recon=base+decoded if correction!="scale_norm" else (base+decoded)* (np.linalg.norm(np.asarray(docs[selected]),axis=1)/np.maximum(np.linalg.norm(base+decoded,axis=1),1e-12))[:,None]
                ranked=top_ids(cosine(recon,q),selected); rows.append({"query":qi,"arm":arm,"correction":correction,"top10_ids":ranked.astype(int).tolist(),"thq4_top128_ids":selected.astype(int).tolist(),"qrels_ndcg10":ndcg(ranked,qids[qi],qscores[qi]),"teacher_overlap":float(np.isin(teacher[qi],ranked).sum()/10),"side_payload_bytes":48,"cascade_total_bytes":144})
    a.models_output.parent.mkdir(parents=True,exist_ok=True); a.codes_output.parent.mkdir(parents=True,exist_ok=True); np.savez_compressed(a.models_output,centroids=centroids,rotation=rotation,unique_ids=unique); np.savez_compressed(a.codes_output,**codes,**{f"scale_{k}":v for k,v in scales.items()})
    sources={n:getattr(a,n.replace("-","_")) for n in names[:11]}; result={"schema_version":1,"family":"thq_residual_binary_gate_c_v1","status":"EXECUTED","source_replay":True,"metric":"cosine","runner_sha256":sha256(Path(__file__)),"source_hashes":{n:sha256(v) for n,v in sources.items()},"artifact_hashes":{"models":sha256(a.models_output),"codes":sha256(a.codes_output)},"query_count":QCOUNT,"arms":list(ARMS),"corrections":["code_only","scale","scale_norm"],"rows":rows,"limitations":["local RaBitQ/BBQ-like residual references; not faithful TurboQuant/NEQ","candidate-local materialization","held-out confirmation pending"]}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
if __name__ == "__main__": main()
