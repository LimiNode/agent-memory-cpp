#!/usr/bin/env python3
"""Compare direct THQ ranking and compact rerankers against candidate-local FP32."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
import numpy as np

QUERIES=152; RECORD=148; TOP_K=10; RANK_K=256

def require(value: bool, message: str) -> None:
    if not value: raise RuntimeError(message)
def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1<<20),b""): digest.update(chunk)
    return digest.hexdigest()
def validate(path: Path, metadata: dict, label: str) -> None:
    require(path.is_file(),f"missing {label}: {path}")
    require(sha(path)==metadata["sha256"],f"{label} SHA differs")
    require(path.stat().st_size==int(metadata["bytes"]),f"{label} size differs")
def top(ids: np.ndarray, scores: np.ndarray, k: int, descending: bool) -> np.ndarray:
    return ids[np.lexsort((ids, -scores if descending else scores))[:min(k,len(ids))]]
def aggregate(values: list[float]) -> dict[str,float]:
    a=np.asarray(values,dtype=np.float64)
    return {"min":float(a.min()),"mean":float(a.mean()),"p05":float(np.percentile(a,5)),
            "p50":float(np.percentile(a,50)),"p95":float(np.percentile(a,95)),"max":float(a.max())}
def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades={int(doc):float(score) for doc,score in zip(qrel_ids,qrel_scores) if int(doc)>=0 and float(score)>0}
    gains=np.asarray([2.0**grades.get(int(doc),0.0)-1.0 for doc in ids[:TOP_K]],dtype=np.float64)
    discounts=np.log2(np.arange(2,2+len(gains),dtype=np.float64))
    dcg=float(np.sum(gains/discounts))
    ideal=np.sort(np.asarray([2.0**score-1.0 for score in grades.values()],dtype=np.float64))[::-1][:TOP_K]
    ideal_dcg=float(np.sum(ideal/np.log2(np.arange(2,2+len(ideal),dtype=np.float64))))
    return dcg/ideal_dcg if ideal_dcg else 0.0
def interval_lut(thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    t1,t2,t3=thresholds[:,0],thresholds[:,1],thresholds[:,2]
    l1=np.stack((np.maximum(query-t1,0.0),
                 np.where(query<t1,t1-query,np.where(query>=t2,query-t2,0.0)),
                 np.where(query<t2,t2-query,np.where(query>=t3,query-t3,0.0)),
                 np.maximum(t3-query,0.0)),axis=1)
    return (l1*l1).astype(np.float32)
def packed_ordinal(levels: np.ndarray) -> np.ndarray:
    return (levels[:,0::4] | (levels[:,1::4]<<2) |
            (levels[:,2::4]<<4) | (levels[:,3::4]<<6)).astype(np.uint8)
def quantized_score(vectors: np.ndarray, query: np.ndarray, bits: int) -> np.ndarray:
    limit=(1<<(bits-1))-1
    scale=np.maximum(np.max(np.abs(vectors),axis=1,keepdims=True)/limit,1e-8)
    codes=np.rint(vectors/scale).clip(-limit,limit)
    if bits==8:
        return np.sum((codes*scale)*query[None,:],axis=1,dtype=np.float32)
    levels=np.rint((codes+limit)/(2*limit)*15.0).clip(0,15)
    restored=(levels/15.0*2.0-1.0)*(scale*limit)
    return np.sum(restored*query[None,:],axis=1,dtype=np.float32)
def inversions_in_reference_order(reference_ids: np.ndarray, candidate_scores: np.ndarray,
                                  reference_scores: np.ndarray) -> int:
    order=np.argsort(-candidate_scores,kind="stable")
    ranks=np.empty(len(order),dtype=np.int64); ranks[order]=np.arange(len(order))
    return int(np.triu(ranks[:,None] > ranks[None,:],1).sum())
def main() -> None:
    parser=argparse.ArgumentParser()
    for name in ("thq-manifest","candidate-receipt","candidate-raw","candidate-flat","output","raw-output"):
        parser.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads(args.thq_manifest.read_text()); receipt=json.loads(args.candidate_receipt.read_text()); candidate_raw=json.loads(args.candidate_raw.read_text())
    require(receipt["raw_sha256"]==sha(args.candidate_raw) and receipt["flat_file"]["sha256"]==sha(args.candidate_flat),"candidate provenance differs")
    validate(args.thq_manifest,{"sha256":manifest.get("sha256",sha(args.thq_manifest)),"bytes":args.thq_manifest.stat().st_size},"THQ manifest")
    n=int(manifest["documents"]); d=int(manifest["dimension"]); q=int(manifest["queries"])
    require(q==QUERIES and int(receipt["flat_file"]["bytes"])==sum(int(row["candidate_count"]) for row in candidate_raw["rows"])*RECORD,"candidate shape differs")
    queries=np.memmap(Path(manifest["references"]["queries"]["path"]),mode="r",dtype="<f4",shape=(q,d))
    docs=np.memmap(Path(manifest["references"]["document_vectors"]["path"]),mode="r",dtype="<f4",shape=(n,d))
    teachers=np.memmap(Path(manifest["references"]["teacher_ids"]["path"]),mode="r",dtype="<i8",shape=(q,10))
    qrel_ids=np.memmap(Path(manifest["references"]["qrel_ids"]["path"]),mode="r",dtype="<i8",shape=(q,20))
    qrel_scores=np.memmap(Path(manifest["references"]["qrel_scores"]["path"]),mode="r",dtype="<f4",shape=(q,20))
    thresholds=np.memmap(Path(manifest["outputs"]["thq4_thresholds"]["path"]),mode="r",dtype="<f4",shape=(d,3))
    flat=np.memmap(args.candidate_flat,mode="r",dtype=np.uint8,shape=(sum(int(row["candidate_count"]) for row in candidate_raw["rows"]),RECORD))
    rows=[]; offset=0
    for qi,row in enumerate(candidate_raw["rows"]):
        count=int(row["candidate_count"]); payload=np.asarray(flat[offset:offset+count]); offset+=count
        ids=np.frombuffer(payload[:,:4].tobytes(),dtype="<i4").astype(np.int64); codes=payload[:,4:]
        require(np.all(ids>=0) and np.all(ids<n),"candidate IDs differ")
        levels=np.unpackbits(codes,axis=1,bitorder="little")[:,:d*3].reshape(count,d,3).sum(axis=2).astype(np.uint8)
        lut=interval_lut(np.asarray(thresholds),np.asarray(queries[qi]))
        thq_score=lut[np.arange(d)[None,:],levels].sum(axis=1,dtype=np.float32)
        ordinal_codes=packed_ordinal(levels)
        ordinal_levels=np.unpackbits(ordinal_codes,axis=1,bitorder="little")[:,:d*2].reshape(count,d,2).sum(axis=2).astype(np.uint8)
        ordinal_score=lut[np.arange(d)[None,:],ordinal_levels].sum(axis=1,dtype=np.float32)
        fp32_score=np.asarray(docs[ids],dtype=np.float32)@np.asarray(queries[qi])
        int8_score=quantized_score(np.asarray(docs[ids]),np.asarray(queries[qi]),8)
        int4_score=quantized_score(np.asarray(docs[ids]),np.asarray(queries[qi]),4)
        rankings={"thq_thermometer":top(ids,thq_score,TOP_K,False),"packed_ordinal":top(ids,ordinal_score,TOP_K,False),
                  "int8_reranker":top(ids,int8_score,TOP_K,True),"int4_reranker":top(ids,int4_score,TOP_K,True),
                  "fp32_exact":top(ids,fp32_score,TOP_K,True)}
        fp32_rank=top(ids,fp32_score,RANK_K,True); teacher=np.asarray(teachers[qi])
        reference_indices=np.asarray([int(np.flatnonzero(ids==doc)[0]) for doc in fp32_rank],dtype=np.int64)
        candidate_teacher_survival=float(np.isin(teacher,ids).sum()/len(teacher))
        for name,ranked in rankings.items():
            rows.append({"query":qi,"candidate_count":count,"representation":name,
                         "exact_top10_overlap":float(np.intersect1d(ranked,rankings["fp32_exact"]).size/TOP_K),
                         "candidate_teacher_survival":candidate_teacher_survival,
                         "teacher_top10_recall":float(np.isin(teacher,ranked).sum()/len(teacher)),
                         "qrels_ndcg10":ndcg(ranked,np.asarray(qrel_ids[qi]),np.asarray(qrel_scores[qi])),
                         "rank_inversions_top256":inversions_in_reference_order(
                             fp32_rank,
                             {"thq_thermometer":thq_score,"packed_ordinal":ordinal_score,
                              "int8_reranker":int8_score,"int4_reranker":int4_score,
                              "fp32_exact":fp32_score}[name][reference_indices],
                             fp32_score[reference_indices]) if name!="fp32_exact" else 0,
                         "payload_bytes_per_document":{"thq_thermometer":144,"packed_ordinal":96,"int8_reranker":388,"int4_reranker":196,"fp32_exact":1536}[name]})
    summaries=[]
    for name in ("thq_thermometer","packed_ordinal","int8_reranker","int4_reranker","fp32_exact"):
        selected=[row for row in rows if row["representation"]==name]
        summaries.append({"representation":name,"query_count":len(selected),
                          **{field:aggregate([float(row[field]) for row in selected]) for field in
                             ("candidate_count","candidate_teacher_survival","exact_top10_overlap",
                              "teacher_top10_recall","qrels_ndcg10","rank_inversions_top256")}})
    raw={"schema_version":1,"family":"semantic_thq_fp32_removal_gate_v1","rows":rows,
         "protocol":{"candidate_semantics":"whole-posting fused stream","scope":"full-candidate diagnostic; no THQ shortlist stage","fp32_comparator":"exact within same candidate set","qrels_metric":"nDCG@10","rank_inversions":"top-256 permutation disagreement"}}
    args.raw_output.parent.mkdir(parents=True,exist_ok=True); payload=(json.dumps(raw,separators=(",",":"),sort_keys=True)+"\n").encode(); args.raw_output.write_bytes(payload)
    receipt_out={"schema_version":1,"family":raw["family"],"execution_status":"EXECUTED","production_activation":False,
                 "thq_manifest_sha256":sha(args.thq_manifest),"candidate_receipt_sha256":sha(args.candidate_receipt),
                 "candidate_raw_sha256":sha(args.candidate_raw),"candidate_flat_sha256":sha(args.candidate_flat),
                 "runner_sha256":sha(Path(__file__)),"summaries":summaries,
                 "raw_output":{"path":str(args.raw_output),"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest(),"rows":len(rows)}}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(receipt_out,indent=2,sort_keys=True)+"\n")
if __name__=="__main__": main()
