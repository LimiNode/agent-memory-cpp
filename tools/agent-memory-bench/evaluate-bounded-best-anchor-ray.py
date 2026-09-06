#!/usr/bin/env python3
"""Bounded best-anchor-in-pool ray oracle.

The full best-anchor oracle is expensive.  This runner screens anchors by
query cosine inside each IVF pool, then evaluates the best of that bounded
screen by actual segment survival.  It is explicitly not the full-pool oracle.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--prototype-source',type=Path,required=True); p.add_argument('--prototype-targets',type=Path,required=True); p.add_argument('--ivf-source',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--queries',type=int,default=8); p.add_argument('--query-start',type=int,default=0); p.add_argument('--screen',type=int,default=4); p.add_argument('--block-size',type=int,default=100000)
    a=p.parse_args()
    with np.load(a.prototype_source,mmap_mode='r',allow_pickle=False) as z: x=np.asarray(z['prototype_vectors'],dtype=np.float32); q=np.asarray(z['queries'],dtype=np.float32)
    with np.load(a.prototype_targets,allow_pickle=False) as z: targets=np.asarray(z['prototype_targets'],dtype=np.int64)
    with np.load(a.ivf_source,allow_pickle=False) as z: cent=np.asarray(z['centroids'],dtype=np.float32); assign=np.asarray(z['assignments'],dtype=np.int32)
    groups=[np.flatnonzero(assign==i) for i in range(len(cent))]; n=len(x); start=max(0,min(a.query_start,len(q))); count=min(a.queries,len(q)-start); ms=(1,4,8,16,32,64); budgets=(5000,10000,15000,20000,40000); rows=[]; begun=time.perf_counter()
    for qi in range(start,start+count):
        cell_order=np.argsort(cent@q[qi])[::-1]; row={}
        for m in ms:
            ids=np.concatenate([groups[int(c)] for c in cell_order[:m] if len(groups[int(c)])]); sims=np.asarray(x[ids])@q[qi]; anchors=ids[np.argsort(sims)[::-1][:min(a.screen,len(ids))]]; per_anchor={}
            for anchor in anchors:
                v=x[int(anchor)]-q[qi]; vv=float(np.dot(v,v)); best=np.empty(n,dtype=np.float32)
                for b in range(0,n,a.block_size):
                    e=min(n,b+a.block_size); block=np.asarray(x[b:e]); diff=block-q[qi]; d2=np.einsum('ij,ij->i',diff,diff,optimize=True); alpha=np.clip((diff@v)/max(vv,1e-12),0.0,1.0); best[b:e]=d2-alpha*alpha*vv
                limit=max(budgets); ranked=np.argpartition(best,limit-1)[:limit]; ranked=ranked[np.argsort(best[ranked],kind='stable')]; per_anchor[int(anchor)]={str(k):float(np.isin(targets[qi],ranked[:k]).sum()/10.0) for k in budgets}
            row[str(m)]={'pool_prototypes':int(len(ids)),'screened_anchors':[int(v) for v in anchors], 'nearest_anchor':int(anchors[0]), 'nearest_survival':per_anchor[int(anchors[0])], 'best_screen_survival':{str(k):max(per_anchor[int(v)][str(k)] for v in anchors) for k in budgets}, 'best_screen_anchor':{str(k):int(max(anchors,key=lambda v:per_anchor[int(v)][str(k)])) for k in budgets}}
        rows.append({'query':qi,'row':row})
    result={'schema_version':1,'family':'bounded_best_anchor_ray_oracle_v1','screen':a.screen,'queries':count,'query_start':start,'rows':rows,'elapsed_seconds':time.perf_counter()-begun}; a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    summary={'queries':count,'screen':a.screen,'mean':{}}
    for m in ms:
        summary['mean'][str(m)]={}
        for k in budgets:
            summary['mean'][str(m)][f'nearest@{k}']=float(np.mean([r['row'][str(m)]['nearest_survival'][str(k)] for r in rows]))
            summary['mean'][str(m)][f'best_screen@{k}']=float(np.mean([r['row'][str(m)]['best_screen_survival'][str(k)] for r in rows]))
    print(json.dumps(summary,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
