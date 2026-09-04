#!/usr/bin/env python3
"""Materialize pool-local INT6 bytes for the licensed native codec timing."""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path
import numpy
sys.dont_write_bytecode=True; THIS=Path(__file__).resolve().parent
def load(n,f):
 s=importlib.util.spec_from_file_location(n,THIS/f); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m
r=load("int6_conditional","run-neuroute-conditional-followups.py")
def require(v,m):
 if not v: raise ValueError(m)
def write(p,v,dtype):
 x=numpy.ascontiguousarray(v,dtype=dtype); p.parent.mkdir(parents=True,exist_ok=True); x.tofile(p); return {"file":p.name,"sha256":r.sha256(p),"shape":list(x.shape),"dtype":dtype}
def materialize_dataset(dsid,data,positions,ps,quality,root):
 dr=r.final.exact.native.lexicographic_ranks(data["document_ids"]); dsroot=root/dsid; routes=[]
 for seed,pools in ps.items():
  codes=[]; scales=[]; ranks=[]
  for local,pos in enumerate(positions):
   values=numpy.asarray(data["documents"][pools[local]],dtype=numpy.float32); scale=numpy.max(numpy.abs(values),axis=1).astype(numpy.float32)/31.; scale[scale==0]=1.; q=numpy.clip(numpy.rint(values/scale[:,None]),-31,31).astype(numpy.int16); codes.append(r.final.pack_codes((q+31).astype(numpy.uint8),6)); scales.append(scale); ranks.append(dr[pools[local]])
  expected=next(x for x in quality if x["seed"]==seed and x["representation"]=="int6_document")
  rr=dsroot/str(seed); routes.append({"seed":seed,"pools":write(rr/"pools.u32le",pools,"<u4"),"codes":write(rr/"codes.u6",numpy.asarray(codes),"u1"),"scales":write(rr/"scales.f32le",numpy.asarray(scales),"<f4"),"ranks":write(rr/"ranks.u32le",numpy.asarray(ranks),"<u4"),"expected":[{"query":q["query"],"ranked_sha256":q["ranked_sha256"]} for q in expected["queries"]]})
 return {"id":dsid,"query_count":len(positions),"query_vectors":write(dsroot/"queries.f32le",data["queries"][positions],"<f4"),"routes":routes}
def run(a):
 c=r.planner.load_contract(a.contract); result=json.loads(a.result.read_text()); require(result["decision"]["selected_codec"]=="int6_document" and result["decision"]["native_codec_timing_licensed"],"int6 native timing not licensed")
 fm=json.loads((a.final_materialization_root/"manifest.json").read_text()); by={x["id"]:x for x in fm["datasets"]}; quality={x["id"]:x["rows"] for x in result["datasets"]}; out=[]; roots={l:{n:getattr(a,f"{l}_{n}_root") for n in ("result","e5","input")} for l in ("de","fr","ja")}; v4=r.final.exact.v4.planner.load_contract(a.v4_contract); vb={x["id"]:x for x in v4["datasets"]}
 for dsid,lang in (("de-25k","de"),("fr-25k","fr"),("ja-25k","ja")):
  data,_,sp=r.final.exact.v4.base.load_dataset(vb[dsid],roots[lang]); b={v:i for i,v in enumerate(data["query_ids"])}; pos=[b[v] for v in sp["configuration_selection_query_ids"]]; out.append(materialize_dataset(dsid,data,pos,r.pools(by[dsid],a.final_materialization_root),quality[dsid],a.output_root))
 sc=next(x for x in r.final.scale.planner.load_contract(a.scale_contract)["scales"] if x["id"]=="de-1m"); data=r.final.scale.load_scale(sc,a.de_1m_e5_root,a.de_1m_input_root); sp=json.loads(a.german_split_result.read_text())["split"]; b={v:i for i,v in enumerate(data["query_ids"])}; pos=[b[v] for v in sp["configuration_selection_query_ids"]]; out.append(materialize_dataset("de-1m",data,pos,r.pools(by["de-1m"],a.final_materialization_root),quality["de-1m"],a.output_root))
 m={"schema_version":1,"family":"neuroute_int6_codec_native_materialization","contract_sha256":r.sha256(a.contract),"quality_result_sha256":r.sha256(a.result),"timing":{"warmups":3,"passes":21,"microbatch":64},"datasets":out}; (a.output_root/"manifest.json").write_bytes(r.canonical(m))
def main():
 p=argparse.ArgumentParser(); p.add_argument("--contract",type=Path,default=THIS/"neuroute-conditional-followups.example.json"); p.add_argument("--result",type=Path); p.add_argument("--final-materialization-root",type=Path); p.add_argument("--v4-contract",type=Path); p.add_argument("--scale-contract",type=Path); p.add_argument("--german-split-result",type=Path)
 for l in ("de","fr","ja"):
  for n in ("result","e5","input"): p.add_argument(f"--{l}-{n}-root",type=Path)
 p.add_argument("--de-1m-e5-root",type=Path); p.add_argument("--de-1m-input-root",type=Path); p.add_argument("--output-root",type=Path); a=p.parse_args()
 try: run(a); return 0
 except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as e: print(f"materialize-neuroute-int6-codec: {e}",file=sys.stderr); return 1
if __name__=="__main__":raise SystemExit(main())
