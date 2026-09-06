#!/usr/bin/env python3
"""Continuous ray/segment geometry oracle for K8 prototype routing.

This deliberately does not use THQ codes.  It tests whether relevant
prototypes lie near a shared semantic segment before any discrete surrogate or
index is designed.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--prototype-source', type=Path, required=True)
    p.add_argument('--prototype-targets', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--anchors', type=int, default=1)
    p.add_argument('--queries', type=int, default=8)
    p.add_argument('--query-start', type=int, default=0)
    p.add_argument('--top-budget', type=int, default=10000)
    p.add_argument('--geometry', choices=['linear', 'spherical'], default='linear')
    p.add_argument('--budgets', type=str, default='256,512,1024,2048,5000,10000')
    p.add_argument('--block-size', type=int, default=100000)
    a = p.parse_args()
    with np.load(a.prototype_source, mmap_mode='r', allow_pickle=False) as z:
        x = np.asarray(z['prototype_vectors'], dtype=np.float32)
        q = np.asarray(z['queries'], dtype=np.float32)
    with np.load(a.prototype_targets, allow_pickle=False) as z:
        targets = np.asarray(z['prototype_targets'], dtype=np.int64)
    n, d = x.shape; qn = q.shape[0]; start = max(0, min(a.query_start, qn)); count = min(a.queries, qn-start)
    rows=[]; begun=time.perf_counter()
    for qi in range(start, start+count):
        query=q[qi]; qnorm=float(np.dot(query,query)); best=np.full(n,np.inf,dtype=np.float32)
        for anchor in targets[qi,:max(1,a.anchors)]:
            v=x[int(anchor)]-query; vv=float(np.dot(v,v))
            if vv <= 1.0e-12: continue
            score=np.empty(n,dtype=np.float32)
            for b in range(0,n,a.block_size):
                e=min(n,b+a.block_size); block=np.asarray(x[b:e],dtype=np.float32)
                if a.geometry == 'linear':
                    diff=block-query; d2=np.einsum('ij,ij->i',diff,diff,optimize=True)
                    alpha=np.clip((diff@v)/vv,0.0,1.0); score[b:e]=d2-alpha*alpha*vv
                else:
                    # Unit E5 vectors: the normalized chord and slerp trace
                    # the same great-circle arc.  Project x onto span(q,p),
                    # then clamp to the minor arc endpoints.
                    c=float(np.dot(query,x[int(anchor)])); den=max(1.0-c*c,1.0e-8)
                    xq=block@query; xp=block@x[int(anchor)]
                    aa=(xq-c*xp)/den; bb=(xp-c*xq)/den
                    yn=np.sqrt(np.maximum(aa*aa+bb*bb+2.0*c*aa*bb,1.0e-12))
                    arc=(aa>=0.0)&(bb>=0.0)
                    cos_arc=(aa*xq+bb*xp)/yn
                    cos_end=np.maximum(xq,xp)
                    score[b:e]=1.0-np.where(arc,cos_arc,cos_end)
            best=np.minimum(best,score)
        budgets=sorted({min(int(v), n) for v in a.budgets.split(',') if int(v)>0})
        limit=max(budgets+[min(a.top_budget,n)]); ranked=np.argpartition(best,limit-1)[:limit]
        # Deterministic ordering only matters inside the reported budget; the
        # metric is set membership and is robust to ties in this oracle.
        out={'query':qi,'anchors':int(max(1,a.anchors)),'ranked_budget':int(limit),
             'teacher_top10_survival':float(np.isin(targets[qi],ranked[:min(a.top_budget,n)]).sum()/10.0),
             'survival_by_budget':{str(k):float(np.isin(targets[qi], ranked[:k]).sum()/10.0) for k in budgets},
             'teacher_top1_rank':int(np.flatnonzero(np.argsort(best,kind='stable')==targets[qi,0])[0]) if n < 100000 else None}
        rows.append(out)
    result={'schema_version':1,'family':'thq_ray_segment_geometry_oracle_v1','geometry':a.geometry,'queries':count,'query_start':start,'anchors':a.anchors,'top_budget':a.top_budget,'rows':rows,'elapsed_seconds':time.perf_counter()-begun}
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'queries':count,'anchors':a.anchors,'mean_teacher_top10_survival':float(np.mean([r['teacher_top10_survival'] for r in rows])), 'survival_by_budget':{k:float(np.mean([r['survival_by_budget'][k] for r in rows])) for k in sorted(rows[0]['survival_by_budget'])}},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
