#!/usr/bin/env python3
"""Run the fresh German dynamic false-positive semantic-address v3 study."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys, time
from pathlib import Path
from typing import Any
import numpy
sys.dont_write_bytecode=True
THIS=Path(__file__).resolve().parent
def load(name: str, filename: str) -> Any:
    spec=importlib.util.spec_from_file_location(name,THIS/filename)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {filename}")
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module); return module
planner=load("neuroute_v3_planner","plan-neuroute-dynamic-false-positive-v3.py")
v2=load("neuroute_v3_v2","run-neuroute-inspired-semantic-address-v2.py")
diagnostic=load("neuroute_v3_diagnostic","diagnose-neuroute-v2-collisions.py")
direct=v2.runner; quality=direct.quality
def require(value: bool,message: str)->None:
    if not value: raise ValueError(message)
def sha256(path: Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value: Any)->bytes: return (json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode("utf-8")

def load_data(e5_root: Path,input_root: Path,contract: dict[str,Any])->dict[str,Any]:
    data=quality.load_root(e5_root); manifest_path=input_root/"manifest.json"; manifest=json.loads(manifest_path.read_text(encoding="utf-8")); dataset=contract["dataset"]
    require(sha256(e5_root/"prepared-study-manifest.json")==dataset["prepared_manifest_sha256"] and data["manifest_sha256"]==dataset["e5_manifest_sha256"] and sha256(manifest_path)==dataset["input_manifest_sha256"],"v3 frozen manifest roots differ")
    require(len(data["document_ids"])==dataset["documents"] and len(data["query_ids"])==dataset["queries"] and data["dimension"]==dataset["dimension"],"v3 frozen cardinality differs")
    for file_key,hash_key in (("document_codes_file","document_codes_sha256"),("query_codes_file","query_codes_sha256"),("query_itq_projections_file","query_itq_projections_sha256"),("binary_adc_centroids_file","binary_adc_centroids_sha256"),("document_vectors_file","document_vectors_sha256"),("query_vectors_file","query_vectors_sha256")):
        path=input_root/manifest[file_key]; require(path.is_file() and sha256(path)==manifest[hash_key],f"v3 input payload differs: {file_key}")
    documents=numpy.asarray(data["documents"],dtype=numpy.float32); queries=numpy.asarray(data["queries"],dtype=numpy.float32); document_count=len(data["document_ids"]); query_count=len(data["query_ids"])
    codes=numpy.fromfile(input_root/manifest["document_codes_file"],dtype="<u8").reshape(document_count,4).view(numpy.uint8).reshape(document_count,32)
    return {**data,"documents":documents,"queries":queries,"input_manifest_sha256":sha256(manifest_path),"document_codes":codes,"document_bits":numpy.unpackbits(codes,axis=1,bitorder="little")[:,:256],"query_codes":numpy.fromfile(input_root/manifest["query_codes_file"],dtype="<u8").reshape(query_count,4).view(numpy.uint8).reshape(query_count,32),"query_projection":numpy.fromfile(input_root/manifest["query_itq_projections_file"],dtype="<f4").reshape(query_count,256),"adc_centroids":numpy.fromfile(input_root/manifest["binary_adc_centroids_file"],dtype="<f4").reshape(256,2)}

def partitions(query_ids: list[str],contract: dict[str,Any])->dict[str,Any]:
    split=contract["partitions"]; prefix=split["prefix_utf8"].encode("utf-8"); ordered=sorted(query_ids,key=lambda value:(hashlib.sha256(prefix+value.encode("utf-8")).digest(),value)); a=split["training"]; b=a+split["configuration_selection"]
    result={"training_query_ids":ordered[:a],"configuration_selection_query_ids":ordered[a:b],"internal_evaluation_query_ids":ordered[b:]}; require([len(result[k]) for k in result]==[153,76,76] and len(set(sum(result.values(),[])))==305,"v3 query split differs"); return result

def mine_false_positives(model: Any,features: Any,documents: numpy.ndarray,pool: int,selected: int)->tuple[numpy.ndarray,numpy.ndarray,str]:
    import torch
    with torch.no_grad():
        latent=[]
        for start in range(0,features.shape[0],1024): latent.append(torch.nn.functional.normalize(model(features[start:start+1024]),dim=1).numpy())
    values=numpy.concatenate(latent); indices=numpy.empty((values.shape[0],selected),dtype=numpy.int32); similarities=numpy.empty((values.shape[0],selected),dtype=numpy.float32)
    for start in range(0,values.shape[0],128):
        stop=min(start+128,values.shape[0]); scores=values[start:stop]@values.T; scores[numpy.arange(stop-start),numpy.arange(start,stop)]=-numpy.inf; candidates=numpy.argpartition(-scores,pool-1,axis=1)[:,:pool]
        for local,candidate in enumerate(candidates):
            source=documents[candidate]@documents[start+local]; order=numpy.lexsort((candidate,source))[:selected]; indices[start+local]=candidate[order]; similarities[start+local]=source[order]
    digest=hashlib.sha256(indices.tobytes()+similarities.tobytes()).hexdigest(); return indices,similarities,digest

def train(documents: numpy.ndarray,queries: numpy.ndarray,training_positions: numpy.ndarray,document_neighbours: numpy.ndarray,document_similarities: numpy.ndarray,query_neighbours: numpy.ndarray,query_similarities: numpy.ndarray,treatment: str,seed: int,contract: dict[str,Any])->tuple[dict[str,numpy.ndarray],dict[str,Any]]:
    import torch
    encoder=contract["encoder"]; positive=contract["positive_geometry"]; dynamic=contract["dynamic_false_positives"]; diversity=contract["diversity"]
    torch.manual_seed(seed); torch.use_deterministic_algorithms(True); torch.set_num_threads(encoder["torch_threads"]); mean=documents.mean(axis=0,dtype=numpy.float64).astype(numpy.float32); scale=documents.std(axis=0,dtype=numpy.float64).astype(numpy.float32); scale[scale<1e-6]=1.0
    document_features=torch.from_numpy(((documents-mean)/scale).astype(numpy.float32)); query_features=torch.from_numpy(((queries-mean)/scale).astype(numpy.float32)); model=torch.nn.Sequential(torch.nn.Linear(384,96),torch.nn.ReLU(),torch.nn.Linear(96,64),torch.nn.ReLU(),torch.nn.Linear(64,12)); optimizer=torch.optim.AdamW(model.parameters(),lr=encoder["learning_rate"],weight_decay=encoder["weight_decay"]); generator=torch.Generator().manual_seed(seed+1)
    false_indices=None; false_similarities=None; mining=[]; losses=[]; started=time.perf_counter()
    for epoch in range(encoder["epochs"]):
        if treatment=="dynamic_false_positive" and epoch in dynamic["remine_epochs"]:
            false_indices,false_similarities,digest=mine_false_positives(model,document_features,documents,dynamic["latent_neighbour_pool"],dynamic["selected_e5_farthest"]); mining.append({"epoch":epoch,"sha256":digest,"source_cosine_mean":float(false_similarities.mean())})
        order=torch.randperm(documents.shape[0],generator=generator); query_order=training_positions[torch.randperm(training_positions.size,generator=generator).numpy()]; total=0.0
        for batch_number,start in enumerate(range(0,documents.shape[0],encoder["batch_size"])):
            chosen=order[start:start+encoder["batch_size"]]; chosen_numpy=chosen.numpy(); positive_documents=document_neighbours[chosen_numpy,epoch%positive["document_neighbours"]]; query_start=(batch_number*encoder["training_query_batch_size"])%query_order.size; selected_queries=numpy.take(query_order,numpy.arange(query_start,query_start+encoder["training_query_batch_size"])%query_order.size); positive_queries=query_neighbours[selected_queries,epoch%positive["query_document_neighbours"]]
            pieces=[document_features[chosen],document_features[positive_documents],query_features[selected_queries],document_features[positive_queries]]; false_documents=None
            if false_indices is not None: false_documents=false_indices[chosen_numpy,epoch%dynamic["selected_e5_farthest"]]; pieces.append(document_features[false_documents])
            raw=model(torch.cat(pieces)); latent=torch.nn.functional.normalize(raw,dim=1); dc=chosen.numel(); qc=selected_queries.size; learned_document=(latent[:dc]*latent[dc:2*dc]).sum(dim=1); learned_query=(latent[2*dc:2*dc+qc]*latent[2*dc+qc:2*dc+2*qc]).sum(dim=1); source_document=torch.from_numpy(document_similarities[chosen_numpy,epoch%positive["document_neighbours"]]); source_query=torch.from_numpy(query_similarities[selected_queries,epoch%positive["query_document_neighbours"]]); objective=torch.nn.functional.mse_loss(learned_document,source_document)+torch.nn.functional.mse_loss(learned_query,source_query)
            standard=raw.std(dim=0); objective+=diversity["variance_weight"]*torch.relu(diversity["minimum_latent_standard_deviation"]-standard).mean(); centered=raw-raw.mean(dim=0,keepdim=True); normalized=centered/(standard.unsqueeze(0)+1e-6); covariance=normalized.T@normalized/max(1,normalized.shape[0]-1); covariance-=torch.diag(torch.diag(covariance)); objective+=diversity["covariance_weight"]*(covariance**2).mean()
            if false_documents is not None:
                learned_false=(latent[:dc]*latent[-dc:]).sum(dim=1); source_false=torch.from_numpy(false_similarities[chosen_numpy,epoch%dynamic["selected_e5_farthest"]]); objective+=dynamic["weight"]*(torch.relu(learned_false-source_false-dynamic["margin"])**2).mean()
            optimizer.zero_grad(set_to_none=True); objective.backward(); optimizer.step(); total+=float(objective.detach())*dc
        losses.append(total/documents.shape[0])
    first,third,fifth=model[0],model[2],model[4]; artifact={"mean":mean,"scale":scale,"weight1":first.weight.detach().numpy().astype(numpy.float32),"bias1":first.bias.detach().numpy().astype(numpy.float32),"weight2":third.weight.detach().numpy().astype(numpy.float32),"bias2":third.bias.detach().numpy().astype(numpy.float32),"weight3":fifth.weight.detach().numpy().astype(numpy.float32),"bias3":fifth.bias.detach().numpy().astype(numpy.float32)}
    require(numpy.allclose(v2.infer(documents,artifact),model(document_features).detach().numpy(),rtol=2e-5,atol=2e-5),"v3 serialization replay differs"); return artifact,{"treatment":treatment,"seed":seed,"initial_loss":losses[0],"final_loss":losses[-1],"training_seconds":time.perf_counter()-started,"mining":mining,"torch_version":torch.__version__}

def evaluate(data: dict[str,Any],positions_list:list[int],logits:numpy.ndarray,index:dict[str,Any],oracle:numpy.ndarray,full_ndcg:numpy.ndarray,probes:int,retain:bool)->tuple[dict[str,Any],list[dict[str,Any]]]:
    rows=[]
    for position in positions_list:
        requested=diagnostic.addresses(logits[position],12,probes); candidates,accepted=direct.candidate_union(requested,index["postings"],len(data["document_ids"]),.1); hamming,adc,ranked=direct.cascade(data,position,candidates); rows.append({"query_id":data["query_ids"][position],"candidate_count":int(candidates.size),"e5_oracle_survival_after_adc":float(numpy.isin(oracle[position],adc).sum())/10.0,"reranked_ndcg_at_10":quality.dcg_at_10(data["document_ids"][ranked],data["qrels"][data["query_ids"][position]]),"accepted_probe_count":len(accepted),"candidate_positions":candidates.tolist() if retain else None,"adc_positions":adc.tolist() if retain else None,"reranked_positions":ranked.tolist() if retain else None})
    def avg(name:str)->float:return float(numpy.mean([row[name] for row in rows],dtype=numpy.float64))
    return {"query_count":len(rows),"candidate_fraction":avg("candidate_count")/len(data["document_ids"]),"adc_survival":avg("e5_oracle_survival_after_adc"),"ndcg_at_10":avg("reranked_ndcg_at_10"),"full_e5_ndcg_at_10":float(numpy.mean(full_ndcg[positions_list]))},rows

def paired(left:list[dict[str,Any]],right:list[dict[str,Any]],contract:dict[str,Any])->dict[str,Any]:
    require([r["query_id"] for r in left]==[r["query_id"] for r in right],"v3 paired query order differs"); gates=contract["gates"]; rng=numpy.random.default_rng(gates["bootstrap_seed"]); n=len(left); samples=rng.integers(0,n,size=(gates["bootstrap_resamples"],n)); result={}
    for name in ("e5_oracle_survival_after_adc","reranked_ndcg_at_10"):
        delta=numpy.asarray([r[name] for r in right])-numpy.asarray([r[name] for r in left]); means=delta[samples].mean(axis=1); result[name]={"delta":float(delta.mean()),"ci95":[float(numpy.quantile(means,.025)),float(numpy.quantile(means,.975))]}
    return result

def run(contract_path:Path,e5_root:Path,input_root:Path,output_root:Path)->None:
    contract=planner.load_contract(contract_path); data=load_data(e5_root,input_root,contract); split=partitions(data["query_ids"],contract); id_to_position={value:index for index,value in enumerate(data["query_ids"])}; partition_positions={name:[id_to_position[value] for value in split[f"{name}_query_ids"]] for name in ("training","configuration_selection","internal_evaluation")}; training_positions=numpy.asarray(partition_positions["training"],dtype=numpy.int32); documents=data["documents"]; queries=data["queries"]
    document_neighbours,document_similarities=v2.nearest(documents,documents,16,numpy.arange(documents.shape[0],dtype=numpy.int32)); query_neighbours,query_similarities=v2.nearest(queries,documents,10); oracle,full_ndcg=direct.exact_oracle(data,10); output_root.mkdir(parents=True,exist_ok=True); models=[]; runtime={}; configuration=[]; internal=[]
    for planned in planner.plan(contract):
        artifact,training=train(documents,queries,training_positions,document_neighbours,document_similarities,query_neighbours,query_similarities,planned["treatment"],planned["seed"],contract); path=output_root/f"model-{planned['treatment']}-{planned['seed']}.npz"; v2.save(path,artifact,{"schema_version":1,"family":"neuroute_dynamic_false_positive_model_v3","contract_sha256":sha256(contract_path),"training":training}); document_logits=v2.infer(documents,artifact); query_logits=v2.infer(queries,artifact); threshold=numpy.median(document_logits,axis=0).astype(numpy.float32); document_logits-=threshold; query_logits-=threshold; index=direct.build_index(document_logits,documents,12,1); key=(planned["treatment"],planned["seed"]); runtime[key]=(query_logits,index); collision=diagnostic.collision(documents,direct.code_values(document_logits,12),document_neighbours[:,:10],document_similarities[:,:10]); models.append({**planned,"model_sha256":sha256(path),"training":training,"threshold":threshold.tolist(),"collision":collision})
        for probes in contract["routing"]["configuration_frontier_probes"]:
            metrics,_=evaluate(data,partition_positions["configuration_selection"],query_logits,index,oracle,full_ndcg,probes,False); configuration.append({**planned,"probes":probes,**metrics})
    for planned in planner.plan(contract):
        query_logits,index=runtime[(planned["treatment"],planned["seed"])]; metrics,rows=evaluate(data,partition_positions["internal_evaluation"],query_logits,index,oracle,full_ndcg,512,True); internal.append({**planned,"metrics":metrics,"rows":rows})
    control_document,control_artifact=direct.document_head(documents); control_query=((queries-control_artifact["document_mean"])@control_artifact["document_projection"]-control_artifact["document_threshold"]).astype(numpy.float32); control_index=direct.build_index(control_document,documents,8,4); control_metrics,control_rows=direct.evaluate(data,partition_positions["internal_evaluation"],control_query,control_index,oracle,full_ndcg,"symmetric_document_head_control",8,16,.1,False,True)
    def mean_rows(treatment:str)->dict[str,float]:
        values=[row["metrics"] for row in internal if row["treatment"]==treatment]; return {name:float(numpy.mean([value[name] for value in values])) for name in ("candidate_fraction","adc_survival","ndcg_at_10")}
    positive=[row["rows"] for row in internal if row["treatment"]=="positive_only_control"]; dynamic=[row["rows"] for row in internal if row["treatment"]=="dynamic_false_positive"]; averaged_positive=[{**positive[0][i],"e5_oracle_survival_after_adc":float(numpy.mean([seed[i]["e5_oracle_survival_after_adc"] for seed in positive])),"reranked_ndcg_at_10":float(numpy.mean([seed[i]["reranked_ndcg_at_10"] for seed in positive]))} for i in range(len(positive[0]))]; averaged_dynamic=[{**dynamic[0][i],"e5_oracle_survival_after_adc":float(numpy.mean([seed[i]["e5_oracle_survival_after_adc"] for seed in dynamic])),"reranked_ndcg_at_10":float(numpy.mean([seed[i]["reranked_ndcg_at_10"] for seed in dynamic]))} for i in range(len(dynamic[0]))]
    mechanism=paired(averaged_positive,averaged_dynamic,contract); mechanism["passed"]=mechanism["e5_oracle_survival_after_adc"]["delta"]>=contract["gates"]["mechanism_minimum_survival_gain"] and mechanism["e5_oracle_survival_after_adc"]["ci95"][0]>0
    report={"schema_version":1,"family":"neuroute_dynamic_false_positive_result_v3","contract_sha256":sha256(contract_path),"e5_manifest_sha256":data["manifest_sha256"],"input_manifest_sha256":data["input_manifest_sha256"],"split":split,"models":models,"configuration_frontier":configuration,"internal":internal,"internal_means":{"positive_only_control":mean_rows("positive_only_control"),"dynamic_false_positive":mean_rows("dynamic_false_positive")},"symmetric_control":{"metrics":control_metrics,"rows":control_rows},"mechanism_gate":mechanism}
    (output_root/"result.json").write_bytes(canonical(report))

def self_test()->None:
    contract=planner.load_contract(THIS/"neuroute-dynamic-false-positive-v3.example.json"); split=partitions([f"q{i}" for i in range(305)],contract); require(sum(len(v) for v in split.values())==305 and len(diagnostic.addresses(numpy.arange(12,dtype=numpy.float32)-6,12,512))==512,"v3 self-test differs"); print("NeuRoute dynamic false-positive v3 self-test passed")
def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--contract",type=Path,default=THIS/"neuroute-dynamic-false-positive-v3.example.json"); parser.add_argument("--e5-root",type=Path); parser.add_argument("--input-root",type=Path); parser.add_argument("--output-root",type=Path); parser.add_argument("--self-test",action="store_true"); args=parser.parse_args()
    try:
        if args.self_test:self_test();return 0
        if any(value is None for value in (args.e5_root,args.input_root,args.output_root)):parser.error("--e5-root, --input-root, and --output-root are required")
        run(args.contract,args.e5_root,args.input_root,args.output_root);return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,numpy.linalg.LinAlgError) as error:print(f"run-neuroute-dynamic-false-positive-v3: {error}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
