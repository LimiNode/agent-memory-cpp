#!/usr/bin/env python3
"""Compare raw and OPQ-rotated float product routing on the frozen 1M corpus."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, sys, time, zipfile
from pathlib import Path
from typing import Any
import faiss, numpy
sys.dont_write_bytecode=True
THIS=Path(__file__).resolve().parent
def require(v:bool,m:str)->None:
    if not v: raise ValueError(m)
def sha256(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(v:Any)->bytes:return (json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode("utf-8")
def load(name:str,file:str)->Any:
    s=importlib.util.spec_from_file_location(name,THIS/file)
    if s is None or s.loader is None: raise RuntimeError(f"cannot load {file}")
    m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m
planner=load("rotated_product_planner","plan-rotated-product-diagnostic.py")
base=load("rotated_product_base","run-data-dependent-product-locator.py")
def percentile(v:list[float],q:float)->float:return float(numpy.quantile(numpy.asarray(v,dtype=numpy.float64),q,method="linear"))
def parent(path:Path,expected:str)->None:
    require(path.is_file() and sha256(path)==expected,"rotated product parent evidence differs")
    with zipfile.ZipFile(path) as z:m=json.loads(z.read("bundle/evidence-manifest.json"))
    require(m.get("family")=="data_dependent_product_locator_evidence_v1" and m.get("row_count")==54,"rotated product parent evidence identity differs")
def opq_artifact(train:numpy.ndarray,contract:dict[str,Any],path:Path)->tuple[numpy.ndarray,numpy.ndarray]:
    meta={"opq":contract["opq"],"train_sha256":hashlib.sha256(numpy.asarray(train,dtype="<f4").tobytes()).hexdigest()}
    if path.is_file():
        with numpy.load(path,allow_pickle=False) as a:
            require(json.loads(str(a["metadata_json"].item()))==meta,"OPQ serialized metadata differs");return a["matrix"].copy(),a["bias"].copy()
    opq=faiss.OPQMatrix(384,contract["opq"]["subquantizers"]);opq.pq=faiss.ProductQuantizer(384,contract["opq"]["subquantizers"],contract["opq"]["bits_per_subquantizer"]);opq.niter=contract["opq"]["iterations"];opq.verbose=False;numpy.random.seed(contract["opq"]["seed"]);opq.train(train)
    matrix=faiss.vector_to_array(opq.A).reshape(384,384).astype(numpy.float32);raw_bias=faiss.vector_to_array(opq.b).astype(numpy.float32);bias=raw_bias if raw_bias.size else numpy.zeros(384,dtype=numpy.float32)
    require(numpy.allclose(opq.apply_py(train[:16]),train[:16]@matrix.T+bias,atol=1e-5),"OPQ transform extraction differs")
    path.parent.mkdir(parents=True,exist_ok=True);numpy.savez_compressed(path,metadata_json=numpy.asarray(json.dumps(meta,sort_keys=True,separators=(",",":"))),matrix=matrix,bias=bias)
    return matrix,bias
def make_artifact(treatment:str,budget:int,train:numpy.ndarray,docs:numpy.ndarray,contract:dict[str,Any],root:Path)->tuple[list[numpy.ndarray],list[numpy.ndarray],numpy.ndarray,Path,dict[str,Any]]:
    count=int(round(math.log(budget,4))); positions=base.blocks(384,count);opq_path=root/"opq.npz";matrix,bias=opq_artifact(train,contract,opq_path)
    transform=(lambda x:x) if treatment=="raw_contiguous_e5_product_control" else (lambda x:x@matrix.T+bias)
    metadata={"schema_version":1,"family":"rotated_product_locator_artifact_v1","treatment":treatment,"budget":budget,"block_count":count,"opq_sha256":sha256(opq_path) if treatment!="raw_contiguous_e5_product_control" else None}
    path=root/"artifacts"/f"{treatment}-cells{budget}.npz"
    if path.is_file():
        with numpy.load(path,allow_pickle=False) as a:
            require(json.loads(str(a["metadata_json"].item()))==metadata,"rotated product artifact metadata differs");books=[a[f"book_{i}"].copy() for i in range(count)];cells=a["cells"].copy();return positions,books,cells,path,{"matrix":matrix,"bias":bias}
    current_train,current_docs=transform(train),transform(docs);books=base.float_centers(current_train,positions,count);cells=base.float_assign(current_docs,positions,books)
    path.parent.mkdir(parents=True,exist_ok=True);payload={"metadata_json":numpy.asarray(json.dumps(metadata,sort_keys=True,separators=(",",":"))),"cells":cells};payload.update({f"book_{i}":b for i,b in enumerate(books)});numpy.savez_compressed(path,**payload);return positions,books,cells,path,{"matrix":matrix,"bias":bias}
def run(a:argparse.Namespace)->None:
    c=planner.load_contract(a.contract);require(faiss.__version__==c["faiss_version"],"Faiss version differs");parent(a.parent_product_evidence,c["parent_product_evidence_sha256"])
    root=a.scale_root/"es-1m";inp=root/"input"/"manifest.json";e5=root/"e5"/"manifest.json";require(sha256(inp)==c["scale"]["input_manifest_sha256"] and sha256(e5)==c["scale"]["evaluation_manifest_sha256"],"rotated product frozen manifests differ")
    manifest=json.loads(inp.read_text(encoding="utf8"));data=base.evaluator.shared.load_root(root/"e5");train=numpy.fromfile(root/"e5"/"train-vectors.f32",dtype="<f4").reshape(25000,384);docs=numpy.asarray(data["documents"],dtype=numpy.float32);queries=numpy.asarray(data["queries"],dtype=numpy.float32);require(docs.shape==(1000000,384) and queries.shape==(648,384),"rotated product E5 data differs")
    codes=base.packed_codes(root/"input"/manifest["document_codes_file"],1000000);qcodes=base.packed_codes(root/"input"/manifest["query_codes_file"],648);bits=numpy.unpackbits(codes,bitorder="little",axis=1);proj=numpy.fromfile(root/"input"/manifest["query_itq_projections_file"],dtype="<f4").reshape(648,256);adc=numpy.fromfile(root/"input"/manifest["binary_adc_centroids_file"],dtype="<f4").reshape(256,2);target=50000;rows=[]
    for treatment in c["treatments"]:
      for budget in c["implicit_cell_budgets"]:
        pos,books,cells,artifact,opq=make_artifact(treatment,budget,train,docs,c,a.output_root);index=base.cell_index(cells);transformed_queries=queries if treatment=="raw_contiguous_e5_product_control" else queries@opq["matrix"].T+opq["bias"];out=a.output_root/f"{treatment}-cells{budget}";out.mkdir(parents=True,exist_ok=True);short=out/"shortlist.json";quality=out/"quality.json";contrib=out/"contributions.npz";audit=out/"audit.json";config={"schema_version":1,"family":c["family"],"treatment":treatment,"implicit_cell_budget":budget,"target_candidate_count":target,"artifact_sha256":sha256(artifact),"parent_product_evidence_sha256":c["parent_product_evidence_sha256"],"cascade":c["cascade"]};(out/"config.json").write_bytes(canonical(config));srows=[];arows=[];counts=[];times=[];probes=[]
        for i in range(648):
          local=base.local_float_costs(transformed_queries[i],pos,books);cand,visited,pcount,_,elapsed=base.route(local,index,target);ham=base.hamming_positions(codes,qcodes[i],cand);adcv=base.adc_positions(bits,proj[i],adc,ham);srows.append({"query_position":i,"selected_cell_keys":visited,"hamming_shortlist_positions":ham.tolist(),"binary_adc_positions":adcv.tolist()});arows.append({"query_position":i,"candidate_count":int(cand.size),"cell_probes":pcount,"selected_cell_keys":visited});counts.append(float(cand.size));times.append(elapsed);probes.append(float(pcount))
        short.write_bytes(canonical({"schema_version":1,"family":"native_ann_hamming_shortlist_export_v1","backend":"rotated_product_locator","input_manifest_sha256":sha256(inp),"hamming_limit":768,"config_sha256":sha256(out/"config.json"),"rows":srows}));audit.write_bytes(canonical({"schema_version":1,"family":c["family"],"config_sha256":sha256(out/"config.json"),"rows":arows}));measured=base.write_quality(data,short,contrib,quality,out/"oracle.npz");rows.append({"id":out.name,"treatment":treatment,"implicit_cell_budget":budget,"actual_candidate_fraction":float(numpy.mean(counts))/1000000,"candidate_count_p95":percentile(counts,.95),"cell_probes_p50":percentile(probes,.5),"routing_p50_ms_per_query":percentile(times,.5),"routing_p95_ms_per_query":percentile(times,.95),"artifact_sha256":sha256(artifact),"shortlist_sha256":sha256(short),"quality_sha256":sha256(quality),"contribution_sha256":sha256(contrib),"routing_audit_sha256":sha256(audit),"e5_oracle_survival_after_adc":measured["e5_oracle_survival_after_adc"],"reranked_ndcg_at_10":measured["reranked_ndcg_at_10"]})
    a.output_root.joinpath("summary.json").write_bytes(canonical({"schema_version":1,"family":c["family"],"contract_sha256":sha256(a.contract),"rows":rows}))
def self_test()->None: require(base.blocks(384,8)[0].size==48,"rotated product blocks differ");print("rotated product diagnostic runner self-test passed")
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--contract",type=Path,default=THIS/"rotated-product-diagnostic.example.json");p.add_argument("--parent-product-evidence",type=Path);p.add_argument("--scale-root",type=Path);p.add_argument("--output-root",type=Path);p.add_argument("--self-test",action="store_true");a=p.parse_args()
 try:
  if a.self_test:self_test();return 0
  if any(v is None for v in(a.parent_product_evidence,a.scale_root,a.output_root)):p.error("--parent-product-evidence, --scale-root, and --output-root are required")
  run(a);return 0
 except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,zipfile.BadZipFile,base.evaluator.EvaluationError) as e:print(f"run-rotated-product-diagnostic: {e}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
