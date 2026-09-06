#!/usr/bin/env python3
"""Evaluate nearest-E5 anchor selected inside each IVF prototype pool."""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np

def main()->int:
 p=argparse.ArgumentParser(); p.add_argument('--prototype-source',type=Path,required=True); p.add_argument('--prototype-targets',type=Path,required=True); p.add_argument('--ivf-source',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--queries',type=int,default=8); p.add_argument('--query-start',type=int,default=0); p.add_argument('--block-size',type=int,default=100000)
 a=p.parse_args()
 with np.load(a.prototype_source,mmap_mode='r',allow_pickle=False) as z: x=np.asarray(z['prototype_vectors'],dtype=np.float32); q=np.asarray(z['queries'],dtype=np.float32)
 with np.load(a.prototype_targets,allow_pickle=False) as z: targets=np.asarray(z['prototype_targets'],dtype=np.int64)
 with np.load(a.ivf_source,allow_pickle=False) as z: cent=np.asarray(z['centroids'],dtype=np.float32); assign=np.asarray(z['assignments'],dtype=np.int32)
 groups=[np.flatnonzero(assign==i) for i in range(len(cent))]; n=len(x); start=max(0,min(a.query_start,len(q))); count=min(a.queries,len(q)-start); rows=[]; begun=time.perf_counter(); ms=(1,2,4,8,16,32,64); budgets=(256,512,1024,2048,5000,10000)
 for qi in range(start,start+count):
  order=np.argsort(cent@q[qi])[::-1]; row={}
  for m in ms:
   ids=np.concatenate([groups[int(c)] for c in order[:m] if len(groups[int(c)])]); sims=np.asarray(x[ids])@q[qi]; anchor=int(ids[np.argmax(sims)]); v=x[anchor]-q[qi]; vv=float(np.dot(v,v)); best=np.empty(n,dtype=np.float32)
   for b in range(0,n,a.block_size):
    e=min(n,b+a.block_size); block=np.asarray(x[b:e]); diff=block-q[qi]; d2=np.einsum('ij,ij->i',diff,diff,optimize=True); alpha=np.clip((diff@v)/max(vv,1e-12),0.0,1.0); best[b:e]=d2-alpha*alpha*vv
   lim=max(budgets); ranked=np.argpartition(best,lim-1)[:lim]; row[str(m)]={'pool_prototypes':int(len(ids)),'anchor':anchor,'teacher_anchor_in_pool':bool(np.isin(targets[qi,0],ids)),'survival_by_budget':{str(k):float(np.isin(targets[qi],ranked[:k]).sum()/10.0) for k in budgets}}
  rows.append({'query':qi,'row':row})
 result={'schema_version':1,'family':'ivf_pool_nearest_anchor_ray_oracle_v1','queries':count,'query_start':start,'rows':rows,'elapsed_seconds':time.perf_counter()-begun}; a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'queries':count,'mean':{str(m):{k:float(np.mean([r['row'][str(m)]['survival_by_budget'][k] for r in rows])) for k in map(str,budgets)} for m in ms}},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
