#!/usr/bin/env python3
"""Calibration-only probe-frontier and collision diagnostics for v2."""
from __future__ import annotations
import argparse, hashlib, heapq, importlib.util, itertools, json, sys
from pathlib import Path
from typing import Any
import numpy
sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
PROBES = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)

def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module
v2 = load("neuroute_v2_diagnostic", "run-neuroute-inspired-semantic-address-v2.py")
direct = v2.runner
splitter = v2.splitter

def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value: Any) -> bytes: return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

def addresses(logits: numpy.ndarray, width: int, count: int) -> list[int]:
    base = int(direct.code_values(logits[None, :], width)[0]); order = numpy.argsort(numpy.abs(logits), kind="stable"); margins = numpy.abs(logits[order]); masks=[0]
    if count > 1:
        queue=[(float(margins[0]), 1 << int(order[0]), 0)]
        while queue and len(masks) < count:
            cost, mask, last=heapq.heappop(queue); masks.append(mask); following=last+1
            if following < width:
                next_bit=1 << int(order[following]); heapq.heappush(queue, (cost-float(margins[last])+float(margins[following]), mask ^ (1 << int(order[last])) ^ next_bit, following)); heapq.heappush(queue, (cost+float(margins[following]), mask | next_bit, following))
    return [base ^ mask for mask in masks]

def frontier(data: dict[str, Any], positions: list[int], logits: numpy.ndarray, index: dict[str, Any], oracle: numpy.ndarray, full_ndcg: numpy.ndarray, width: int, probes: int) -> dict[str, float | int]:
    rows=[]
    for position in positions:
        candidates,_=direct.candidate_union(addresses(logits[position,:width], width, probes), index["postings"], len(data["document_ids"]), .1); _,adc,ranked=direct.cascade(data, position, candidates)
        rows.append((candidates.size, float(numpy.isin(oracle[position], adc).sum()) / oracle.shape[1], direct.quality.dcg_at_10(data["document_ids"][ranked], data["qrels"][data["query_ids"][position]])))
    values=numpy.asarray(rows, dtype=numpy.float64)
    return {"probes": probes, "candidate_fraction": float(values[:,0].mean()/len(data["document_ids"])), "adc_survival": float(values[:,1].mean()), "ndcg_at_10": float(values[:,2].mean())}

def collision(documents: numpy.ndarray, codes: numpy.ndarray, neighbours: numpy.ndarray, similarities: numpy.ndarray) -> dict[str, float]:
    pairs=[]
    for code in numpy.unique(codes):
        members=numpy.flatnonzero(codes == code)
        if members.size > 1:
            pairs.extend((int(members[a]), int(members[b])) for a,b in itertools.combinations(range(members.size), 2))
    if pairs:
        left=numpy.asarray([p[0] for p in pairs]); right=numpy.asarray([p[1] for p in pairs]); within=float((documents[left] * documents[right]).sum(axis=1).mean())
    else: within=float("nan")
    top10_same=float((codes[:,None] == codes[neighbours]).mean())
    hamming=numpy.asarray([[int(int(codes[a]) ^ int(codes[b])).bit_count() for b in row] for a,row in enumerate(neighbours)], dtype=numpy.float64)
    return {"same_address_pair_count": len(pairs), "mean_e5_cosine_within_address": within, "e5_top10_same_address_fraction": top10_same, "e5_top10_hamming_p50": float(numpy.quantile(hamming,.5)), "e5_top10_hamming_p95": float(numpy.quantile(hamming,.95)), "source_top10_mean_cosine": float(similarities.mean())}

def run(result_root: Path, e5_root: Path, input_root: Path, output: Path) -> None:
    result_path=result_root / "result.json"; result=json.loads(result_path.read_text(encoding="utf-8")); require(result.get("family") == "neuroute_inspired_semantic_address_result_v2", "v2 result family differs")
    selected=result["selected_full_configuration"]; require(selected == {"loss":"full", "bits":12, "query_order":"independent_logit_best_first_v2", "maximum_probes":256, "selection_mean":selected["selection_mean"]}, "v2 selected configuration differs")
    data=direct.load_inputs(e5_root,input_root); require(result["e5_manifest_sha256"]==data["manifest_sha256"] and result["input_manifest_sha256"]==data["input_manifest_sha256"], "v2 frozen roots differ")
    source_contract=json.loads((THIS / "direct-learned-semantic-address.example.json").read_text(encoding="utf-8")); split=splitter.materialize(data["query_ids"],source_contract); positions={v:i for i,v in enumerate(data["query_ids"])}; configuration=[positions[v] for v in split["configuration_selection_query_ids"]]
    oracle,full_ndcg=direct.exact_oracle(data,10); documents=data["documents"]; neighbours,similarities=v2.nearest(documents,documents,10,numpy.arange(documents.shape[0],dtype=numpy.int32))
    rows=[]; collision_rows=[]
    for model in result["models"]:
        if model["loss"] != "full" or model["bits"] != 12: continue
        artifact_path=result_root / f"model-full-12-bit-{model['seed']}.npz"
        with numpy.load(artifact_path,allow_pickle=False) as saved: artifact={key:saved[key] for key in ("mean","scale","weight1","bias1","weight2","bias2","weight3","bias3")}
        document_logits=v2.infer(documents,artifact)-numpy.asarray(model["threshold"],dtype=numpy.float32); query_logits=v2.infer(data["queries"],artifact)-numpy.asarray(model["threshold"],dtype=numpy.float32); index=direct.build_index(document_logits,documents,12,1)
        rows.extend({"seed":model["seed"], **frontier(data,configuration,query_logits,index,oracle,full_ndcg,12,probe)} for probe in PROBES); collision_rows.append({"seed":model["seed"], **collision(documents,direct.code_values(document_logits,12),neighbours,similarities)})
    control_logits,control=direct.document_head(documents); control_collision=collision(documents,direct.code_values(control_logits,8),neighbours,similarities)
    report={"schema_version":1,"family":"neuroute_v2_calibration_collision_diagnostic_v1","v2_result_sha256":sha256(result_path),"e5_manifest_sha256":data["manifest_sha256"],"input_manifest_sha256":data["input_manifest_sha256"],"partition":"configuration_selection_only","probes":list(PROBES),"frontier":rows,"v2_collision":collision_rows,"pca_control_collision":control_collision}
    output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(canonical(report))

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--result-root",type=Path); parser.add_argument("--e5-root",type=Path); parser.add_argument("--input-root",type=Path); parser.add_argument("--output",type=Path); args=parser.parse_args()
    try:
        if any(value is None for value in (args.result_root,args.e5_root,args.input_root,args.output)): parser.error("--result-root, --e5-root, --input-root, and --output are required")
        run(args.result_root,args.e5_root,args.input_root,args.output); return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,numpy.linalg.LinAlgError) as error: print(f"diagnose-neuroute-v2-collisions: {error}",file=sys.stderr); return 1
if __name__ == "__main__": raise SystemExit(main())
