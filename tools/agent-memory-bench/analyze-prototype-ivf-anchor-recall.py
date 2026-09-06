#!/usr/bin/env python3
"""Measure prototype-anchor recall of a frozen IVF cell generator."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--prototype-source',type=Path,required=True); p.add_argument('--prototype-targets',type=Path,required=True); p.add_argument('--ivf-source',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--queries',type=int,default=152); p.add_argument('--query-start',type=int,default=0); p.add_argument('--max-cells',type=int,default=64)
    a=p.parse_args()
    with np.load(a.prototype_source,mmap_mode='r',allow_pickle=False) as z: x=np.asarray(z['prototype_vectors'],dtype=np.float32); q=np.asarray(z['queries'],dtype=np.float32)
    with np.load(a.prototype_targets,allow_pickle=False) as z: targets=np.asarray(z['prototype_targets'],dtype=np.int64)
    with np.load(a.ivf_source,allow_pickle=False) as z: cent=np.asarray(z['centroids'],dtype=np.float32); assign=np.asarray(z['assignments'],dtype=np.int32)
    groups=[np.flatnonzero(assign==i) for i in range(len(cent))]; start=max(0,min(a.query_start,len(q))); count=min(a.queries,len(q)-start); rows=[]; begun=time.perf_counter()
    for qi in range(start,start+count):
        cell_order=np.argsort(cent@q[qi])[::-1]; row={}
        for m in (1,2,4,8,16,32,64):
            if m>a.max_cells: continue
            ids=np.concatenate([groups[int(c)] for c in cell_order[:m] if len(groups[int(c)])])
            if not len(ids): row[str(m)]={'candidate_prototypes':0,'teacher_top1_recall':0.0,'teacher_top10_recall':0.0,'teacher_top1_cosine_rank':None}; continue
            scores=np.asarray(x[ids])@q[qi]; order=np.argsort(scores)[::-1]; ranked=ids[order]; t1=int(targets[qi,0]); row[str(m)]={'candidate_prototypes':int(len(ids)),'teacher_top1_recall':float(t1 in set(ids.tolist())),'teacher_top10_recall':float(np.isin(targets[qi],ids).sum()/10.0),'teacher_top1_cosine_rank':int(np.flatnonzero(ranked==t1)[0]+1) if t1 in set(ids.tolist()) else None}
        rows.append({'query':qi,'row':row})
    result={'schema_version':1,'family':'prototype_ivf_anchor_recall_oracle_v1','queries':count,'query_start':start,'max_cells':a.max_cells,'rows':rows,'elapsed_seconds':time.perf_counter()-begun}; a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'queries':count,'mean':{m:{'top1_recall':float(np.mean([r['row'][str(m)]['teacher_top1_recall'] for r in rows])),'top10_recall':float(np.mean([r['row'][str(m)]['teacher_top10_recall'] for r in rows])),'candidate_prototypes':float(np.mean([r['row'][str(m)]['candidate_prototypes'] for r in rows]))} for m in (1,2,4,8,16,32,64) if str(m) in rows[0]['row']}},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
