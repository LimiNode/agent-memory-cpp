#!/usr/bin/env python3
"""Independent Gaussian-hyperplane cosine-LSH retrieval oracle."""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np

def main():
 p=argparse.ArgumentParser();p.add_argument('--thq-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--query-limit',type=int,default=8);p.add_argument('--tables',type=int,default=4);p.add_argument('--bits-per-table',type=int,default=16);p.add_argument('--chunk',type=int,default=50000);a=p.parse_args();m=json.loads(a.thq_manifest.read_text());n,d=int(m['documents']),int(m['dimension']);q=min(int(m['queries']),a.query_limit);refs=m['references'];docs=np.memmap(refs['document_vectors']['path'],mode='r',dtype='<f4',shape=(n,d));queries=np.asarray(np.memmap(refs['queries']['path'],mode='r',dtype='<f4',shape=(q,d)));teachers=np.memmap(refs['teacher_ids']['path'],mode='r',dtype='<i8',shape=(q,10));rng=np.random.default_rng(20260911);planes=rng.normal(size=(a.tables,a.bits_per_table,d)).astype(np.float32);planes/=np.linalg.norm(planes,axis=2,keepdims=True);codes=np.zeros((n,a.tables),np.uint16)
 for i in range(0,n,a.chunk):
  j=min(n,i+a.chunk);x=np.asarray(docs[i:j]);
  for tab in range(a.tables):
   bits=(x@planes[tab].T)>0;codes[i:j,tab]=np.packbits(bits,axis=1,bitorder='little').view(np.uint16).reshape(j-i)
 postings=[]
 for tab in range(a.tables):
  order=np.argsort(codes[:,tab],kind='stable');keys=codes[order,tab];u,st=np.unique(keys,return_index=True);postings.append({int(u[k]):order[st[k]:st[k+1] if k+1<len(st) else n] for k in range(len(st))})
 qcodes=np.zeros((q,a.tables),np.uint16)
 for tab in range(a.tables):qcodes[:,tab]=np.packbits((queries@planes[tab].T)>0,axis=1,bitorder='little').view(np.uint16).reshape(q)
 rows=[];budgets=(256,512,1000,2000,5000,10000)
 for qi in range(q):
  st=time.perf_counter();parts=[postings[t].get(int(qcodes[qi,t]),np.empty(0,np.int64)) for t in range(a.tables)];cand=np.unique(np.concatenate([x for x in parts if len(x)])) if any(len(x) for x in parts) else np.empty(0,np.int64);gen=(time.perf_counter()-st)*1000
  # Union is ranked by the same 64-bit LSH Hamming code, then ID.
  xor=np.bitwise_xor(codes[cand],qcodes[qi]);dist=np.unpackbits(xor.view(np.uint8),axis=1,bitorder='little').sum(1,np.uint16) if len(cand) else np.empty(0,np.uint16);order=np.lexsort((cand,dist))
  row={'query':qi,'candidate_count':int(len(cand)),'postings_touched':int(sum(len(x) for x in parts)),'generation_ms':gen}
  for b in budgets:row[f'survival_{b}']=float(np.isin(teachers[qi],cand[order[:min(b,len(order))]]).sum())/10
  rows.append(row)
 result={'schema_version':1,'family':'cosine_lsh_baseline_v1','documents':n,'queries':q,'dimension':d,'hash_family':'independent_gaussian_random_hyperplanes','tables':a.tables,'bits_per_table':a.bits_per_table,'probe_policy':'exact bucket union only','rows':rows,'summary':{'candidate_count_mean':float(np.mean([r['candidate_count'] for r in rows])),'survival_256_mean':float(np.mean([r['survival_256'] for r in rows])),'survival_256_worst':float(np.min([r['survival_256'] for r in rows]))},'production_activation':False}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
