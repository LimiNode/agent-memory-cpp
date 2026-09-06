#!/usr/bin/env python3
"""Ray/segment oracle with non-privileged prototype anchor selection."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--prototype-source',type=Path,required=True); p.add_argument('--prototype-targets',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--anchor-source',choices=['teacher','nearest-prototype','ivf'],default='nearest-prototype')
    p.add_argument('--anchors',type=int,default=1); p.add_argument('--ivf-source',type=Path)
    p.add_argument('--queries',type=int,default=8); p.add_argument('--query-start',type=int,default=0); p.add_argument('--top-budget',type=int,default=10000); p.add_argument('--block-size',type=int,default=100000)
    a=p.parse_args()
    with np.load(a.prototype_source,mmap_mode='r',allow_pickle=False) as z:
        x=np.asarray(z['prototype_vectors'],dtype=np.float32); q=np.asarray(z['queries'],dtype=np.float32)
    with np.load(a.prototype_targets,allow_pickle=False) as z: targets=np.asarray(z['prototype_targets'],dtype=np.int64)
    assignments=centroids=None
    if a.anchor_source=='ivf':
        if not a.ivf_source: raise ValueError('--ivf-source is required for ivf anchors')
        with np.load(a.ivf_source,allow_pickle=False) as z:
            centroids=np.asarray(z['centroids'],dtype=np.float32); assignments=np.asarray(z['assignments'],dtype=np.int32)
    n,d=x.shape; qn=q.shape[0]; start=max(0,min(a.query_start,qn)); count=min(a.queries,qn-start); budgets=[256,512,1024,2048,5000,10000]; rows=[]; begun=time.perf_counter()
    for qi in range(start,start+count):
        query=q[qi]
        if a.anchor_source=='teacher': anchors=targets[qi,:a.anchors]
        elif a.anchor_source=='nearest-prototype':
            sims=np.empty(n,dtype=np.float32)
            for b in range(0,n,a.block_size):
                e=min(n,b+a.block_size); sims[b:e]=np.asarray(x[b:e])@query
            anchors=np.argpartition(sims,-a.anchors)[-a.anchors:]
        else:
            cells=np.argpartition(centroids@query,-a.anchors)[-a.anchors:]; chosen=[]
            for cell in cells:
                ids=np.flatnonzero(assignments==int(cell))
                if len(ids): chosen.append(int(ids[np.argmax(np.asarray(x[ids])@query)]))
            anchors=np.asarray(chosen,dtype=np.int64)
        best=np.full(n,np.inf,dtype=np.float32)
        for anchor in anchors:
            v=x[int(anchor)]-query; vv=float(np.dot(v,v)); score=np.empty(n,dtype=np.float32)
            for b in range(0,n,a.block_size):
                e=min(n,b+a.block_size); block=np.asarray(x[b:e],dtype=np.float32); diff=block-query; d2=np.einsum('ij,ij->i',diff,diff,optimize=True); alpha=np.clip((diff@v)/max(vv,1e-12),0.0,1.0); score[b:e]=d2-alpha*alpha*vv
            best=np.minimum(best,score)
        limit=min(a.top_budget,n); ranked=np.argpartition(best,limit-1)[:limit]
        rows.append({'query':qi,'anchor_source':a.anchor_source,'anchor_ids':[int(v) for v in anchors],'survival_by_budget':{str(k):float(np.isin(targets[qi],ranked[:k]).sum()/10.0) for k in budgets}})
    result={'schema_version':1,'family':'thq_runtime_anchor_ray_oracle_v1','anchor_source':a.anchor_source,'anchors':a.anchors,'queries':count,'query_start':start,'rows':rows,'elapsed_seconds':time.perf_counter()-begun}; a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'queries':count,'anchor_source':a.anchor_source,'anchors':a.anchors,'survival_by_budget':{k:float(np.mean([r['survival_by_budget'][k] for r in rows])) for k in map(str,budgets)}},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
