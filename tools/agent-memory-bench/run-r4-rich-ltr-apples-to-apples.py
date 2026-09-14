#!/usr/bin/env python3
"""Evaluate a sampled pointwise R4 posting baseline on a matched split."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, platform
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression

def sha256(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def agg(v):
 a=np.asarray(v,dtype=float); return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}
def load(path,name):
 s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def main():
 p=argparse.ArgumentParser(); p.add_argument('--thq-manifest',type=Path,required=True); p.add_argument('--r4-manifest',type=Path,required=True); p.add_argument('--r4-root',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--raw-output',type=Path,required=True); p.add_argument('--train-queries',type=int,default=114); p.add_argument('--depth',type=int,default=8192); a=p.parse_args()
 frozen=json.loads(a.thq_manifest.read_text()); manifest=json.loads(a.r4_manifest.read_text()); n=int(frozen['documents']); qcount=152; dim=int(frozen['dimension']); budgets=[20000,50000,100000]
 q=np.memmap(Path(frozen['references']['queries']['path']),mode='r',dtype='<f4',shape=(qcount,dim)); teachers=np.memmap(Path(frozen['references']['teacher_ids']['path']),mode='r',dtype='<i8',shape=(qcount,10))
 fusion=load(Path(__file__).with_name('run-r4-fusion-upper-bounds.py'),'fusion'); routes=fusion.reconstruct(a,frozen,manifest); names=['seed-2026082702','seed-2026082703','deep-8192']; deep=routes['deep-8192'];
 # Build address centroids and per-query route features.
 cent=np.empty((len(deep['counts']),dim),dtype=np.float32)
 rec=next(x for x in manifest['seeds'] if int(x['seed'])==2026082701); root=a.r4_root/'materialized'/'seed-2026082701'; m={x['role']:x for x in rec['mappings']}; fp=next(x for x in rec['layouts'] if x['role']=='address_major_fp32'); records=np.memmap(root/fp['file'],mode='r',dtype='<f4',shape=(n,dim)); offs=np.fromfile(root/m['address_offsets']['file'],dtype='<u4'); counts=np.fromfile(root/m['address_counts']['file'],dtype='<u4')
 for i,(o,c) in enumerate(zip(offs,counts)): cent[i]=np.asarray(records[int(o):int(o+c)],dtype=np.float32).mean(axis=0)
 cn=np.linalg.norm(cent,axis=1); cn[cn==0]=1; cent/=cn[:,None]; qn=np.array(q,dtype=np.float32,copy=True); norms=np.linalg.norm(qn,axis=1,keepdims=True); norms[norms==0]=1; qn/=norms
 orders={name:routes[name]['order'] for name in names}
 aligned_address={name:np.asarray([routes[name]['doc_address'][int(ids[0])] if len(ids) else 0 for ids in deep['postings']],dtype=np.int32) for name in names[:2]}
 positions={}
 for name in names[:2]:
  table=np.full((qcount,len(routes[name]['postings'])),a.depth+1,dtype=np.int32)
  for qi in range(qcount): table[qi,orders[name][qi]]=np.arange(len(orders[name][qi]),dtype=np.int32)+1
  positions[name]=table
 # Feature vector: centroid cosine, inverse rank, log posting size, normalized rank,
 # two shallow-route ranks (or sentinel), and cross-route agreement count.
 def features(qi,ad,rank):
  vals=[float(np.dot(qn[qi],cent[int(ad)])),1.0/(1.0+rank),float(np.log1p(deep['counts'][int(ad)])),float(rank/max(1,a.depth))]
  ranks=[]
  for name in names[:2]:
   mapped=int(aligned_address[name][int(ad)]); r=float(positions[name][qi,mapped]) if mapped < positions[name].shape[1] else float(a.depth+1); ranks.append(r); vals.extend([1.0/r, float(r<=1024)])
  vals.append(float(sum(r<=1024 for r in ranks)))
  return vals
 train=np.arange(min(a.train_queries,qcount),dtype=np.int32); ev=np.arange(min(a.train_queries,qcount),qcount,dtype=np.int32); X=[]; y=[]; rng=np.random.default_rng(20260913)
 for qi in train:
  pos=[]; neg=[]; tset=set(int(x) for x in teachers[qi])
  for rank,ad in enumerate(deep['order'][qi]):
   if tset.intersection(int(x) for x in deep['postings'][int(ad)]): pos.append((rank,int(ad)))
   else: neg.append((rank,int(ad)))
  for rank,ad in pos:
   X.append(features(int(qi),ad,rank)); y.append(1)
   for nr,na in (neg if len(neg)<=8 else [neg[int(i)] for i in rng.choice(len(neg),8,replace=False)]): X.append(features(int(qi),na,nr)); y.append(0)
 model=LogisticRegression(max_iter=300,class_weight='balanced',random_state=20260913); model.fit(np.asarray(X,dtype=np.float32),np.asarray(y,dtype=np.int8))
 rows=[]
 for qi in ev:
  teachers_q=np.asarray(teachers[qi],dtype=np.int64); tset=set(int(x) for x in teachers_q); seen=np.zeros(n,bool); selected=0; entries=0; touched=0; order=deep['order'][qi]; fs=np.asarray([features(int(qi),int(ad),r) for r,ad in enumerate(order)],dtype=np.float32); pred=model.predict_proba(fs)[:,1]; ranked=order[np.lexsort((order,-pred))]
  # Rich ranker and original model order are evaluated with identical accounting.
  methods={'sampled_pointwise_logistic':ranked,'model_prefix_plus_coarse_tail':order}
  for method,stream in methods.items():
   seen[:]=False; selected=entries=touched=0
   for ad in stream:
    ids=deep['postings'][int(ad)]; touched+=1; entries+=len(ids); fresh=ids[~seen[ids]]; seen[fresh]=True; selected+=len(fresh)
    for b in budgets:
     if selected>=b and not any(r['query']==int(qi) and r['method']==method and r['budget']==b for r in rows): rows.append({'query':int(qi),'method':method,'budget':b,'teacher_recall':float(np.count_nonzero(seen[teachers_q])/len(teachers_q)),'unique_candidates':int(selected),'posting_entries':int(entries),'postings_touched':int(touched)})
    if len([r for r in rows if r['method']==method and r['query']==int(qi)])==len(budgets): break
 # Matched controls from canonical fusion raw: same 38 eval queries.
 fusion_raw=Path(r'E:\_repoz\agent-memory-workspaces\r4-fusion-upper-bounds-raw.json')
 fusion_raw_sha256=sha256(fusion_raw) if fusion_raw.exists() else None
 if fusion_raw.exists():
  fr=json.loads(fusion_raw.read_text());
  for key,label in [('prefix_allocation_oracle','prefix_oracle'),('arbitrary_posting_oracle','arbitrary_oracle')]:
   for row in fr.get(key,[]):
    if int(row['query']) in set(int(x) for x in ev): rows.append({'query':int(row['query']),'method':label,'budget':int(row['budget']),'teacher_recall':float(row['recall']),'unique_candidates':int(row.get('unique_candidates',0)),'posting_entries':int(row['posting_entries']),'postings_touched':0})
 expected_methods={'sampled_pointwise_logistic','model_prefix_plus_coarse_tail','prefix_oracle','arbitrary_oracle'}
 expected_keys={(int(qi),method,int(budget)) for qi in ev for method in expected_methods for budget in budgets}
 actual_keys={(int(r['query']),r['method'],int(r['budget'])) for r in rows}
 if actual_keys != expected_keys:
  raise ValueError('sampled pointwise method matrix is incomplete or mislabeled')
 summary=[]
 for method in sorted({r['method'] for r in rows}):
  for b in budgets:
   s=[r for r in rows if r['method']==method and r['budget']==b];
   if len(s)!=len(ev): continue
   summary.append({'method':method,'budget':b,'query_count':len(s),'teacher_recall':agg([r['teacher_recall'] for r in s]),'unique_candidates':agg([r['unique_candidates'] for r in s]),'posting_entries':agg([r['posting_entries'] for r in s]),'postings_touched':agg([r['postings_touched'] for r in s])})
 raw=(json.dumps({'schema_version':1,'rows':rows},sort_keys=True,separators=(',',':'))+'\n').encode(); a.raw_output.parent.mkdir(parents=True,exist_ok=True); a.raw_output.write_bytes(raw)
 out={'schema_version':1,'family':'r4_sampled_pointwise_ltr_apples_to_apples_v2','execution_status':'EXECUTED','production_activation':False,'fixture_manifest_sha256':sha256(a.thq_manifest),'r4_manifest_sha256':sha256(a.r4_manifest),'runner_sha256':sha256(Path(__file__)),'train_query_ids':[int(x) for x in train],'eval_query_ids':[int(x) for x in ev],'feature_columns':['deep_centroid_cosine','inverse_deep_rank','log_posting_size','normalized_deep_rank','aligned_seed2702_inverse_rank','aligned_seed2702_present','aligned_seed2703_inverse_rank','aligned_seed2703_present','aligned_route_agreement'],'model':{'type':'sampled_pointwise_logistic','numpy_version':np.__version__,'sklearn_version':__import__('sklearn').__version__,'python_version':platform.python_version(),'coef':model.coef_.tolist(),'intercept':model.intercept_.tolist(),'positive_label_count':int(sum(y)),'negative_label_count':int(len(y)-sum(y))},'budgets':budgets,'summary':summary,'external_fusion_raw':{'path':str(fusion_raw),'sha256':fusion_raw_sha256},'raw_output':{'path':str(a.raw_output),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'protocol':{'teacher_ids_used_for_training_labels':True,'teacher_ids_used_at_inference':False,'split_semantics':'research split; matched controls restricted to the same 38 eval queries','budget_definition':'unique document ids','physical_page_bytes':'not measured','scope':'sampled pointwise logistic control; not pairwise/listwise LTR and not production activation','cross_route_alignment':'deep posting representative document mapped to each shallow route address'}}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2)+'\n')
if __name__=='__main__': main()
