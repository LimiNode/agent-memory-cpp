#!/usr/bin/env python3
"""Evaluate bounded secondary-assignment controls for frozen semantic R4 routes.

This is an offline assignment oracle/control, not a production SPANN/SOAR
implementation.  Primary postings remain unchanged; selected documents are
replicated into a second independent R4 view and queried with frozen route
orders.  Replication is measured explicitly.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
import numpy as np

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
 p=argparse.ArgumentParser(); p.add_argument('--thq-manifest',type=Path,required=True); p.add_argument('--r4-manifest',type=Path,required=True); p.add_argument('--r4-root',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--raw-output',type=Path,required=True); p.add_argument('--budgets',default='20000,50000,100000'); p.add_argument('--query-count',type=int,default=152); a=p.parse_args()
 frozen=json.loads(a.thq_manifest.read_text()); manifest=json.loads(a.r4_manifest.read_text()); n=int(frozen['documents']); dim=int(frozen['dimension']); qcount=min(152,max(1,a.query_count)); budgets=[int(x) for x in a.budgets.split(',') if x]; q=np.memmap(Path(frozen['references']['queries']['path']),mode='r',dtype='<f4',shape=(152,dim)); teachers=np.memmap(Path(frozen['references']['teacher_ids']['path']),mode='r',dtype='<i8',shape=(152,10)); comp=load(Path(__file__).with_name('run-r4-frozen-comparator.py'),'spill_comp'); seeds={}; assignments={}
 for seed in (2026082701,2026082702):
  rec=next(x for x in manifest['seeds'] if int(x['seed'])==seed); root=a.r4_root/'materialized'/f'seed-{seed}'; m={x['role']:x for x in rec['mappings']}; counts=np.fromfile(root/m['address_counts']['file'],dtype='<u4'); offs=np.fromfile(root/m['address_offsets']['file'],dtype='<u4'); phys=np.fromfile(root/m['physical_to_document']['file'],dtype='<i4'); postings=[phys[int(o):int(o+c)] for o,c in zip(offs,counts)]; order=np.fromfile(root/m['shortlist_rows']['file'],dtype='<u4').reshape(152,1024); seeds[seed]={'order':order,'postings':postings,'counts':counts,'doc_to_address':np.empty(n,dtype=np.int32)}; 
  for ad,ids in enumerate(postings): seeds[seed]['doc_to_address'][ids]=ad
 # Query-independent assignment scores use each document's primary/secondary
 # address representatives, so no teacher labels influence replication.
 primary=seeds[2026082701]['doc_to_address']; secondary=seeds[2026082702]['doc_to_address']; disagreement=np.abs(primary.astype(np.int64)-secondary.astype(np.int64)); rng=np.random.default_rng(20260913); random_order=rng.permutation(n); boundary_order=np.argsort(disagreement,kind='stable'); complement_order=np.argsort(-disagreement,kind='stable')
 factors=[1.0,1.25,1.5,2.0]; policies={'primary_only':None,'random_secondary':random_order,'boundary_closure':boundary_order,'disagreement_secondary':complement_order}; rows=[]
 for policy,selection in policies.items():
  for factor in factors:
   count=min(n,max(0,int(round((factor-1.0)*n)))); selected=np.zeros(n,bool); 
   if selection is not None and count: selected[selection[:count]]=True
   spill_lists=[[] for _ in range(len(seeds[2026082702]['postings']))]
   for doc_id in np.flatnonzero(selected): spill_lists[int(secondary[doc_id])].append(int(doc_id))
   spill=[np.asarray(x,dtype=np.int32) for x in spill_lists]
   for qi in range(qcount):
    seen=np.zeros(n,bool); touched=entries=0; unique=0; order=seeds[2026082701]['order'][qi].tolist()+seeds[2026082702]['order'][qi].tolist()
    for source,ad in [(2026082701,int(x)) for x in seeds[2026082701]['order'][qi]]+[(2026082702,int(x)) for x in seeds[2026082702]['order'][qi]]:
     ids=seeds[source]['postings'][ad] if source==2026082701 else spill[ad]
     if source==2026082702 and ids.size==0: continue
     touched+=1; entries+=int(ids.size); fresh=ids[~seen[ids]]; seen[fresh]=True; unique+=int(fresh.size)
     if unique>=max(budgets): break
    # Record all budget crossings from the single traversal.
    snapshots={}; seenb=np.zeros(n,bool); u=e=t=0
    for source,ad in [(2026082701,int(x)) for x in seeds[2026082701]['order'][qi]]+[(2026082702,int(x)) for x in seeds[2026082702]['order'][qi]]:
     ids=seeds[source]['postings'][ad] if source==2026082701 else spill[ad]
     if source==2026082702 and ids.size==0: continue
     t+=1; e+=int(ids.size); fresh=ids[~seenb[ids]]; seenb[fresh]=True; u+=int(fresh.size)
     for b in budgets:
      if b not in snapshots and u>=b: snapshots[b]=(u,e,t,float(np.count_nonzero(seenb[teachers[qi]])/len(teachers[qi])))
     if len(snapshots)==len(budgets): break
    for b in budgets:
     su,se,st,sr=snapshots.get(b,(u,e,t,float(np.count_nonzero(seenb[teachers[qi]])/len(teachers[qi])))); rows.append({'policy':policy,'replication_factor':factor,'query':qi,'budget':b,'unique_candidates':su,'posting_entries':se,'postings_touched':st,'teacher_recall':sr})
 summary=[]
 for policy in policies:
  for factor in factors:
   for b in budgets:
    s=[r for r in rows if r['policy']==policy and r['replication_factor']==factor and r['budget']==b]; summary.append({'policy':policy,'replication_factor':factor,'budget':b,'query_count':len(s),'teacher_recall':agg([r['teacher_recall'] for r in s]),'unique_candidates':agg([r['unique_candidates'] for r in s]),'posting_entries':agg([r['posting_entries'] for r in s]),'postings_touched':agg([r['postings_touched'] for r in s])})
 raw=(json.dumps({'schema_version':1,'rows':rows},sort_keys=True,separators=(',',':'))+'\n').encode(); a.raw_output.parent.mkdir(parents=True,exist_ok=True); a.raw_output.write_bytes(raw); out={'schema_version':1,'family':'r4_selective_spill_control_v1','execution_status':'EXECUTED','production_activation':False,'fixture_manifest_sha256':sha256(a.thq_manifest),'r4_manifest_sha256':sha256(a.r4_manifest),'runner_sha256':sha256(Path(__file__)),'budgets':budgets,'query_count':qcount,'replication_factors':factors,'summary':summary,'raw_output':{'path':str(a.raw_output),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'protocol':{'primary_seed':2026082701,'secondary_seed':2026082702,'assignment_teacher_free':True,'policies':['primary_only','random_secondary','boundary_closure','disagreement_secondary'],'budget_definition':'unique document ids','physical_page_bytes':'not measured','scope':'offline secondary-assignment control; not a full SPANN/SOAR implementation','payload_replication':'doc IDs only; canonical payload remains single'}}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2)+'\n')
if __name__=='__main__': main()
