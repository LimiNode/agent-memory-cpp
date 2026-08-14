#!/usr/bin/env python3
"""Train a query-only projection against frozen ITQ document codes and MIH false positives."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from pathlib import Path
from typing import Any
THIS=Path(__file__).resolve(); RADIUS=56; BANDS=16
class TrainingError(RuntimeError): pass
def load(name:str,key:str)->Any:
 spec=importlib.util.spec_from_file_location(key,THIS.with_name(name)); assert spec and spec.loader
 module=importlib.util.module_from_spec(spec); sys.modules[key]=module; spec.loader.exec_module(module); return module
nlb=load('train-nlb-qrels-supervised.py','asymmetric_nlb'); banding=load('evaluate-mih-banding.py','asymmetric_banding')
def digest(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value:bool,message:str)->None:
 if not value: raise TrainingError(message)
def write_f32(path:Path,value:Any)->None:path.write_bytes(value.astype('<f4',copy=False).tobytes())
def source_hashes()->dict[str,str]:
 names=(THIS.name,'train-nlb-qrels-supervised.py','evaluate-mih-banding.py','requirements-binary-autoencoder-trainer.txt')
 return {name:digest(THIS.with_name(name)) for name in names}
def train(args:Any)->None:
 require(args.output_root is not None and not args.output_root.exists() and args.epochs>0 and args.hard_negative_count>0,'invalid asymmetric trainer arguments')
 base=nlb.load_base(); base.verify_environment(); import numpy; import torch; import torch.nn.functional as functional
 data=nlb.load_supervised_materialization(args.materialization_root,base,numpy)
 prepared=json.loads((args.materialization_root/'prepared-study-manifest.json').read_text()); exclusion=prepared['split']['external_excluded_document_ids_set_sha256']
 frozen,bias=nlb.initialize_itq_median(numpy.asarray(data['train_vectors']),256,args.seed,args.itq_iterations,numpy)
 doc_codes=(numpy.clip(data['document_vectors'],-1,1)@frozen.T+bias)>=0
 index=banding.build_index(doc_codes,banding.band_ranges(256,BANDS)); radii=banding.global_radius_schedule(RADIUS,BANDS)
 doc_rows={value:i for i,value in enumerate(data['document_ids'])}; query_rows={value:i for i,value in enumerate(data['query_ids'])}
 positives={q:sorted(values,key=lambda d:(-values[d],d))[0] for q,values in data['positive'].items()}
 mined={}
 for q,row in query_rows.items():
  query_code=(numpy.clip(data['query_vectors'][row],-1,1)@frozen.T+bias)>=0
  candidates,_=banding.candidate_union(index,query_code,banding.band_ranges(256,BANDS),radii)
  forbidden=set(data['positive'][q]); mined[q]=[int(i) for i in candidates if data['document_ids'][int(i)] not in forbidden][:args.hard_negative_count]
  require(len(mined[q])==args.hard_negative_count,'insufficient MIH false positives')
 torch.set_num_threads(1); torch.manual_seed(args.seed); torch.use_deterministic_algorithms(True)
 weight=torch.nn.Parameter(torch.from_numpy(frozen.copy())); anchor=weight.detach().clone(); opt=torch.optim.AdamW((weight,),lr=args.learning_rate,weight_decay=0.0)
 train_ids=sorted(data['query_ids'],key=lambda q:hashlib.sha256(f'{args.seed}\0{q}'.encode()).digest())
 for epoch in range(args.epochs):
  rng=numpy.random.default_rng(int.from_bytes(hashlib.sha256(f'{args.seed}\0{epoch}'.encode()).digest()[:16],'big')); order=numpy.arange(len(train_ids)); rng.shuffle(order)
  for start in range(0,len(order),args.batch_size):
   chosen=[train_ids[int(i)] for i in order[start:start+args.batch_size]]; q=torch.from_numpy(numpy.asarray(data['query_vectors'][[query_rows[x] for x in chosen]],dtype=numpy.float32).copy()); pos=torch.from_numpy(numpy.asarray(data['document_vectors'][[doc_rows[positives[x]] for x in chosen]],dtype=numpy.float32).copy()); neg=torch.from_numpy(numpy.asarray(data['document_vectors'][[mined[x][epoch%args.hard_negative_count] for x in chosen]],dtype=numpy.float32).copy())
   def code(x:Any,w:Any)->Any:
    soft=torch.tanh(torch.clamp(x,-1,1)@w.T+torch.from_numpy(bias)); hard=torch.where(soft>=0,torch.ones_like(soft),-torch.ones_like(soft)); return soft+(hard-soft).detach()
   qc,pc,nc=code(q,weight),code(pos,torch.from_numpy(frozen)),code(neg,torch.from_numpy(frozen)); pd=.5*(256-(qc*pc).sum(1)); nd=.5*(256-(qc*nc).sum(1)); loss=functional.softplus((pd-RADIUS)/4).mean()+functional.softplus((80-nd)/4).mean()+args.anchor_weight*((weight-anchor)**2).mean(); opt.zero_grad(set_to_none=True); loss.backward();opt.step()
 args.output_root.mkdir(parents=True); query_path=args.output_root/'query-projection-weights.f32'; doc_path=args.output_root/'projection-weights.f32'; threshold_path=args.output_root/'thresholds.f32'; write_f32(query_path,weight.detach().numpy()); write_f32(doc_path,frozen);write_f32(threshold_path,bias)
 artifact={'schema_version':1,'trainer':{'id':'agent-memory-cpp:mih-asymmetric-query-projection-trainer','source_files_sha256':source_hashes()},'input_materialization_manifest_sha256':data['manifest_sha256'],'prepared_study_manifest_sha256':data['manifest']['prepared_study_manifest_sha256'],'architecture':{'family':'mih_query_aware_asymmetric_projection_v1','input_dimension':384,'bit_count':256,'band_count':16,'band_width_bits':16,'shared_projection':False,'document_side':'frozen_full_itq_w0_v1','query_side':'learned_train_qrels_projection_v1'},'training':{'seed':args.seed,'epochs':args.epochs,'batch_size':args.batch_size,'learning_rate':args.learning_rate,'anchor_weight':args.anchor_weight,'queries_or_qrels_used':True,'objective':'frozen_document_itq_query_projection_with_train_mih_false_positive_mining_v1','itq_iterations':args.itq_iterations,'positive_radius':56,'negative_radius':80,'negative_mining_scope':'static_initial_w0_candidate_union_first_materialized_rows_v1','hard_negative_mining':{'id':'train_mih_candidate_union_false_positive_v1','count':args.hard_negative_count},'held_out_exclusion':{'id':'external_excluded_document_ids_set_v1','document_ids_set_sha256':exclusion},'frozen_document_anchor':{'train_ids_sha256':data['manifest']['outputs']['train_ids']['sha256'],'train_vectors_sha256':data['manifest']['outputs']['train_vectors']['sha256']},'checkpoint_selection':'final_epoch_only_no_train_validation_gate_v1'},'weights':{}}
 for key,path,shape in (('projection_weights',doc_path,[256,384]),('query_projection_weights',query_path,[256,384]),('thresholds',threshold_path,[256])): artifact['weights'][key]={'path':path.name,'sha256':digest(path),'shape':shape,'layout':'row_major_out_by_in' if len(shape)==2 else None,'dtype':'float32_le'}
 (args.output_root/'artifact.json').write_text(json.dumps(artifact,indent=2,sort_keys=True)+'\n')
def main(argv:list[str])->int:
 p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--materialization-root',type=Path);p.add_argument('--output-root',type=Path);p.add_argument('--seed',type=int,default=52);p.add_argument('--epochs',type=int,default=4);p.add_argument('--batch-size',type=int,default=192);p.add_argument('--learning-rate',type=float,default=1e-5);p.add_argument('--itq-iterations',type=int,default=50);p.add_argument('--hard-negative-count',type=int,default=4);p.add_argument('--anchor-weight',type=float,default=50.);a=p.parse_args(argv)
 try:
  if a.self_test:
   require(source_hashes()==source_hashes(),'trainer source digest is unstable')
   print('MIH asymmetric query-projection trainer self-test passed');return 0
  train(a)
 except (TrainingError,OSError,ValueError,json.JSONDecodeError) as e: print(f'train-mih-asymmetric-query-projection: {e}',file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
