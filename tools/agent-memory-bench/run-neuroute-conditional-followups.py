#!/usr/bin/env python3
"""Measure conditional compact-codec and overcomplete ADC quality screens."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, sys
from pathlib import Path
from typing import Any
import numpy

sys.dont_write_bytecode=True
THIS=Path(__file__).resolve().parent

def load(name, filename):
    s=importlib.util.spec_from_file_location(name,THIS/filename)
    if s is None or s.loader is None: raise RuntimeError(filename)
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

planner=load("conditional_planner","plan-neuroute-conditional-followups.py")
final=load("conditional_final","run-neuroute-final-representation.py")

def require(v,m):
    if not v: raise ValueError(m)
def sha256(p):
    d=hashlib.sha256()
    with p.open("rb") as f:
        while c:=f.read(8*1024*1024): d.update(c)
    return d.hexdigest()
def canonical(v): return (json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode()
def source_hashes(): return {n:sha256(THIS/n) for n in ("plan-neuroute-conditional-followups.py","run-neuroute-conditional-followups.py","run-neuroute-final-representation.py")}
def sequence(values): return hashlib.sha256(numpy.asarray(values,dtype="<u4").tobytes()).hexdigest()

def pools(manifest_ds, root):
    out={}
    for route in manifest_ds["routes"]:
        item=route["pool"]; p=root/manifest_ds["id"]/str(route["seed"])/item["file"]
        require(sha256(p)==item["sha256"],"conditional pool bytes differ")
        out[int(route["seed"])]=numpy.fromfile(p,dtype="<u4").reshape(manifest_ds["query_count"],64)
    return out

def quantized_scores(values, query, bits, blocks):
    maximum=(1<<(bits-1))-1; size=384//blocks; scores=numpy.zeros(len(values),dtype=numpy.float32)
    for block in range(blocks):
        part=values[:,block*size:(block+1)*size]
        scale=numpy.max(numpy.abs(part),axis=1).astype(numpy.float32)/maximum; scale[scale==0]=1
        codes=numpy.clip(numpy.rint(part/scale[:,None]),-maximum,maximum).astype(numpy.int16)
        scores+=(codes.astype(numpy.float32)*query[None,block*size:(block+1)*size]).sum(axis=1)*scale
    return scores

def rank_codec(codec, documents, query, pool, ids):
    blocks=int(codec.get("blocks",1)); scores=quantized_scores(numpy.asarray(documents[pool]),query,int(codec["bits"]),blocks)
    return pool[numpy.lexsort((ids[pool],-scores))].astype(numpy.uint32)

def projection(width,seed):
    rng=numpy.random.default_rng(seed+width)
    return (rng.integers(0,2,size=(384,width),dtype=numpy.int8)*2-1).astype(numpy.float32)/math.sqrt(384.0)

def adc_stats(documents,matrix):
    count=min(len(documents),100000); positions=numpy.linspace(0,len(documents)-1,count,dtype=numpy.int64)
    projected=numpy.asarray(documents[positions])@matrix
    threshold=numpy.median(projected,axis=0).astype(numpy.float32); codes=projected>=threshold
    sums=numpy.stack((numpy.where(~codes,projected,0).sum(axis=0,dtype=numpy.float64),numpy.where(codes,projected,0).sum(axis=0,dtype=numpy.float64)),axis=1)
    counts=numpy.stack(((~codes).sum(axis=0),codes.sum(axis=0)),axis=1)
    return threshold,numpy.asarray(sums/numpy.maximum(counts,1),dtype=numpy.float32),count

def rank_adc(documents,query,pool,ids,matrix,threshold,centroids):
    dp=numpy.asarray(documents[pool])@matrix; qp=query@matrix; codes=dp>=threshold
    table=(qp[:,None]-centroids)**2
    scores=-table[numpy.arange(matrix.shape[1])[None,:],codes.astype(numpy.uint8)].sum(axis=1)
    return pool[numpy.lexsort((ids[pool],-scores))].astype(numpy.uint32)

def evaluate_dataset(data,positions,seed_pools,contract):
    codecs=contract["codec_screen"]; rows=[]; baseline={}
    matrices={w:projection(w,contract["overcomplete_screen"]["projection_seed"]) for w in contract["overcomplete_screen"]["widths"]}
    stats={w:adc_stats(data["documents"],m) for w,m in matrices.items()}
    for seed,ps in seed_pools.items():
        per={c["id"]:[] for c in codecs}; per.update({f"adc{w}":[] for w in matrices}); per["fp32"]=[]
        for local,pos in enumerate(positions):
            pool=ps[local]; values=numpy.asarray(data["documents"][pool]); q=numpy.asarray(data["queries"][pos])
            exact_scores=(values*q[None,:]).sum(axis=1); fp=pool[numpy.lexsort((data["document_ids"][pool],-exact_scores))].astype(numpy.uint32)
            rankings={"fp32":fp}
            for c in codecs: rankings[c["id"]]=rank_codec(c,data["documents"],q,pool,data["document_ids"])
            for w,m in matrices.items(): rankings[f"adc{w}"]=rank_adc(data["documents"],q,pool,data["document_ids"],m,stats[w][0],stats[w][1])
            for name,ranking in rankings.items(): per[name].append({"query":local,"ndcg_at_10":final.scale.ndcg(data,pos,ranking[:10]),"ranked_sha256":sequence(ranking[:10])})
        for name,qs in per.items():
            rows.append({"seed":seed,"representation":name,"query_count":len(qs),"ndcg_at_10":float(numpy.mean([q["ndcg_at_10"] for q in qs])),"queries":qs})
    return rows,{str(w):{"sample_count":stats[w][2],"projection_sha256":hashlib.sha256(matrices[w].astype("<f4").tobytes()).hexdigest()} for w in matrices}

def decide(datasets,contract):
    names=["fp32"]+[x["id"] for x in contract["codec_screen"]]+[f"adc{w}" for w in contract["overcomplete_screen"]["widths"]]
    comparisons=[]
    for name in names:
        losses=[]; ds_losses=[]
        for ds in datasets:
            base={(r["seed"],q["query"]):q["ndcg_at_10"] for r in ds["rows"] if r["representation"]=="fp32" for q in r["queries"]}
            current=[base[(r["seed"],q["query"])]-q["ndcg_at_10"] for r in ds["rows"] if r["representation"]==name for q in r["queries"]]
            ds_losses.append(float(numpy.mean(current))); losses.extend(current)
        eligible=float(numpy.mean(ds_losses))<=contract["quality"]["maximum_cross_dataset_mean_ndcg_loss_vs_fp32"] and max(ds_losses)<=contract["quality"]["maximum_per_dataset_ndcg_loss_vs_fp32"]
        comparisons.append({"representation":name,"dataset_losses":ds_losses,"mean_loss":float(numpy.mean(ds_losses)),"quality_eligible":eligible})
    eligible={x["representation"] for x in comparisons if x["quality_eligible"]}
    codec_candidates=[x for x in contract["codec_screen"] if x["id"] in eligible and "bytes_per_document" in x]
    codec=min(codec_candidates,key=lambda x:(x["bytes_per_document"],x["id"]))["id"] if codec_candidates else None
    adc_candidates=[w for w in contract["overcomplete_screen"]["widths"] if f"adc{w}" in eligible]
    return {"comparisons":comparisons,"selected_codec":codec,"native_codec_timing_licensed":codec is not None,"selected_overcomplete_width":min(adc_candidates) if adc_candidates else None,"overcomplete_native_licensed":bool(adc_candidates)}

def run(args):
    c=planner.load_contract(args.contract)
    actual={"final_result_sha256":sha256(args.final_result),"final_materialization_sha256":sha256(args.final_materialization_root/"manifest.json"),"final_evidence_sha256":sha256(args.final_evidence)}
    require(actual==c["activation"],"conditional activation differs")
    receipt=json.loads(args.final_evidence.read_text()); require(receipt["decision"]["codec_layout_followup_licensed"] and receipt["decision"]["overcomplete_adc_followup_licensed"],"conditional gates are not licensed")
    manifest=json.loads((args.final_materialization_root/"manifest.json").read_text()); by={x["id"]:x for x in manifest["datasets"]}; datasets=[]
    roots={l:{n:getattr(args,f"{l}_{n}_root") for n in ("result","e5","input")} for l in ("de","fr","ja")}
    v4=final.exact.v4.planner.load_contract(args.v4_contract); v4by={x["id"]:x for x in v4["datasets"]}
    for dsid,lang in (("de-25k","de"),("fr-25k","fr"),("ja-25k","ja")):
        data,_,split=final.exact.v4.base.load_dataset(v4by[dsid],roots[lang]); posby={v:i for i,v in enumerate(data["query_ids"])}; positions=[posby[v] for v in split["configuration_selection_query_ids"]]
        rows,prov=evaluate_dataset(data,positions,pools(by[dsid],args.final_materialization_root),c); datasets.append({"id":dsid,"rows":rows,"overcomplete":prov})
    sc=next(x for x in final.scale.planner.load_contract(args.scale_contract)["scales"] if x["id"]=="de-1m"); data=final.scale.load_scale(sc,args.de_1m_e5_root,args.de_1m_input_root); split=json.loads(args.german_split_result.read_text())["split"]; posby={v:i for i,v in enumerate(data["query_ids"])}; positions=[posby[v] for v in split["configuration_selection_query_ids"]]
    rows,prov=evaluate_dataset(data,positions,pools(by["de-1m"],args.final_materialization_root),c); datasets.append({"id":"de-1m","rows":rows,"overcomplete":prov})
    out={"schema_version":1,"family":"neuroute_conditional_representation_quality_result","contract_sha256":sha256(args.contract),"activation":c["activation"],"source_files_sha256":source_hashes(),"datasets":datasets}; out["decision"]=decide(datasets,c); args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_bytes(canonical(out))

def self_test():
    c=planner.load_contract(THIS/"neuroute-conditional-followups.example.json"); v=numpy.asarray([[1.,-.5]],dtype=numpy.float32); s=quantized_scores(v,numpy.asarray([1.,1.],dtype=numpy.float32),7,1); require(s.shape==(1,),"conditional quantizer differs"); print("NeuRoute conditional follow-ups self-test passed")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--contract",type=Path,default=THIS/"neuroute-conditional-followups.example.json"); p.add_argument("--final-result",type=Path); p.add_argument("--final-materialization-root",type=Path); p.add_argument("--final-evidence",type=Path); p.add_argument("--v4-contract",type=Path); p.add_argument("--scale-contract",type=Path); p.add_argument("--german-split-result",type=Path)
    for l in ("de","fr","ja"):
        for n in ("result","e5","input"): p.add_argument(f"--{l}-{n}-root",type=Path)
    p.add_argument("--de-1m-e5-root",type=Path); p.add_argument("--de-1m-input-root",type=Path); p.add_argument("--output",type=Path); p.add_argument("--self-test",action="store_true"); a=p.parse_args()
    try:
        if a.self_test: self_test(); return 0
        if any(v is None for n,v in vars(a).items() if n not in ("self_test","contract")): p.error("all paths required")
        run(a); return 0
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError,numpy.linalg.LinAlgError) as e: print(f"run-neuroute-conditional-followups: {e}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
