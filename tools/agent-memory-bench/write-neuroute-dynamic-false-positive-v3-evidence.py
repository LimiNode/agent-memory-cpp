#!/usr/bin/env python3
"""Replay saved v3 models and write a fail-closed compact evidence receipt."""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path
from typing import Any
import numpy
sys.dont_write_bytecode=True
THIS=Path(__file__).resolve().parent
def load(name:str,filename:str)->Any:
    spec=importlib.util.spec_from_file_location(name,THIS/filename)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {filename}")
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
runner=load("v3_evidence_runner","run-neuroute-dynamic-false-positive-v3.py")
def require(value:bool,message:str)->None:
    if not value:raise ValueError(message)
def canonical(value:Any)->bytes:return (json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode("utf-8")
def artifact(path:Path)->dict[str,numpy.ndarray]:
    with numpy.load(path,allow_pickle=False) as stored:return {name:stored[name] for name in ("mean","scale","weight1","bias1","weight2","bias2","weight3","bias3")}
def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--contract",type=Path,default=THIS/"neuroute-dynamic-false-positive-v3.example.json");parser.add_argument("--result-root",type=Path,required=True);parser.add_argument("--e5-root",type=Path,required=True);parser.add_argument("--input-root",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    try:
        contract=runner.planner.load_contract(args.contract);result_path=args.result_root/"result.json";result=json.loads(result_path.read_text(encoding="utf-8"));require(result.get("family")=="neuroute_dynamic_false_positive_result_v3" and result.get("contract_sha256")==runner.sha256(args.contract),"v3 evidence result binding differs")
        data=runner.load_data(args.e5_root,args.input_root,contract);split=runner.partitions(data["query_ids"],contract);position={value:index for index,value in enumerate(data["query_ids"])};internal_positions=[position[value] for value in split["internal_evaluation_query_ids"]];configuration_positions=[position[value] for value in split["configuration_selection_query_ids"]];oracle,full_ndcg=runner.direct.exact_oracle(data,10); recomputed_internal=[];recomputed_configuration=[]
        for model in result["models"]:
            path=args.result_root/f"model-{model['treatment']}-{model['seed']}.npz";require(path.is_file() and runner.sha256(path)==model["model_sha256"],"v3 model bytes differ");document_logits=runner.v2.infer(data["documents"],artifact(path))-numpy.asarray(model["threshold"],dtype=numpy.float32);query_logits=runner.v2.infer(data["queries"],artifact(path))-numpy.asarray(model["threshold"],dtype=numpy.float32);index=runner.direct.build_index(document_logits,data["documents"],12,1)
            metrics,rows=runner.evaluate(data,internal_positions,query_logits,index,oracle,full_ndcg,512,True);stored=next(value for value in result["internal"] if value["treatment"]==model["treatment"] and value["seed"]==model["seed"]);require(canonical({"metrics":metrics,"rows":rows})==canonical({"metrics":stored["metrics"],"rows":stored["rows"]}),"v3 internal replay differs");recomputed_internal.append({"treatment":model["treatment"],"seed":model["seed"],"metrics":metrics,"rows":rows})
            for probes in contract["routing"]["configuration_frontier_probes"]:
                metrics,_=runner.evaluate(data,configuration_positions,query_logits,index,oracle,full_ndcg,probes,False);stored=next(value for value in result["configuration_frontier"] if value["treatment"]==model["treatment"] and value["seed"]==model["seed"] and value["probes"]==probes);require(canonical(metrics)==canonical({key:stored[key] for key in metrics}),"v3 configuration replay differs");recomputed_configuration.append({"treatment":model["treatment"],"seed":model["seed"],"probes":probes,"metrics":metrics})
        control_document,control_artifact=runner.direct.document_head(data["documents"]);control_query=((data["queries"]-control_artifact["document_mean"])@control_artifact["document_projection"]-control_artifact["document_threshold"]).astype(numpy.float32);control_index=runner.direct.build_index(control_document,data["documents"],8,4);control_metrics,control_rows=runner.direct.evaluate(data,internal_positions,control_query,control_index,oracle,full_ndcg,"symmetric_document_head_control",8,16,.1,False,True);require(canonical({"metrics":control_metrics,"rows":control_rows})==canonical(result["symmetric_control"]),"v3 PCA control replay differs")
        def averaged(treatment:str)->list[dict[str,Any]]:
            seeds=[value["rows"] for value in recomputed_internal if value["treatment"]==treatment];return [{**seeds[0][i],"e5_oracle_survival_after_adc":float(numpy.mean([seed[i]["e5_oracle_survival_after_adc"] for seed in seeds])),"reranked_ndcg_at_10":float(numpy.mean([seed[i]["reranked_ndcg_at_10"] for seed in seeds]))} for i in range(len(seeds[0]))]
        dynamic=averaged("dynamic_false_positive");positive=averaged("positive_only_control");mechanism=runner.paired(positive,dynamic,contract);architecture=runner.paired(control_rows,dynamic,contract);gates=contract["gates"];architecture["passed"]=architecture["e5_oracle_survival_after_adc"]["delta"]>=gates["architecture_minimum_survival_gain"] and architecture["e5_oracle_survival_after_adc"]["ci95"][0]>0 and architecture["reranked_ndcg_at_10"]["delta"]>=-gates["maximum_ndcg_loss"] and architecture["reranked_ndcg_at_10"]["ci95"][0]>=-gates["maximum_ndcg_loss"]
        receipt={"schema_version":1,"family":"neuroute_dynamic_false_positive_v3_evidence_v1","contract_sha256":runner.sha256(args.contract),"result_sha256":runner.sha256(result_path),"model_count":len(result["models"]),"configuration_row_count":len(recomputed_configuration),"internal_replayed":True,"configuration_replayed":True,"pca_control_replayed":True,"mechanism_gate":mechanism,"architecture_gate":architecture,"passed":True};args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(canonical(receipt));return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,numpy.linalg.LinAlgError) as error:print(f"write-neuroute-dynamic-false-positive-v3-evidence: {error}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
