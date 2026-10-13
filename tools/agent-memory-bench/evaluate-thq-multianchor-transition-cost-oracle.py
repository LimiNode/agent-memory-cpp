#!/usr/bin/env python3
"""Multi-anchor ordinal transition-cost oracle for THQ4."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--prototype-source',type=Path,required=True); p.add_argument('--prototype-targets',type=Path,required=True)
    p.add_argument('--prototype-codes',type=Path,required=True); p.add_argument('--thresholds',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--anchors',type=int,default=4)
    p.add_argument('--queries',type=int,default=8); p.add_argument('--query-start',type=int,default=0)
    p.add_argument('--prefilter',type=int,default=50000); p.add_argument('--block-size',type=int,default=100000)
    a=p.parse_args()
    with np.load(a.prototype_source,mmap_mode='r',allow_pickle=False) as z:
        x=np.asarray(z['prototype_vectors'],dtype=np.float32); q=np.asarray(z['queries'],dtype=np.float32)
    with np.load(a.prototype_targets,allow_pickle=False) as z: targets=np.asarray(z['prototype_targets'],dtype=np.int64)
    n,d=x.shape; qn=q.shape[0]; t=np.fromfile(a.thresholds,dtype='<f4').reshape(d,3); codes=np.memmap(a.prototype_codes,mode='r',dtype=np.uint8,shape=(n,144))
    start=max(0,min(a.query_start,qn)); count=min(a.queries,qn-start); rows=[]; begun=time.perf_counter()
    lut=np.unpackbits(np.arange(256,dtype=np.uint8)[:,None],axis=1).sum(axis=1)
    for qi in range(start,start+count):
        qbits=(q[qi,:,None]>t).reshape(1,-1); qpacked=np.packbits(qbits,axis=1,bitorder='little')[0]
        dist=np.empty(n,dtype=np.uint16); proj_union=[]
        for begin in range(0,n,a.block_size):
            end=min(n,begin+a.block_size); dist[begin:end]=lut[np.bitwise_xor(codes[begin:end],qpacked)].sum(axis=1)
        top=np.argpartition(dist,256)[:256]
        for anchor in targets[qi,:max(1,a.anchors)]:
            direction=x[int(anchor)]-q[qi]; projection=np.empty(n,dtype=np.float32)
            for begin in range(0,n,a.block_size):
                end=min(n,begin+a.block_size); projection[begin:end]=np.asarray(x[begin:end])@direction
            lim=min(a.prefilter,n); proj_union.append(np.argpartition(projection,-lim)[-lim:])
        pre=np.unique(np.concatenate(proj_union)); values=np.asarray(x[pre],dtype=np.float32)
        levels=(values[:,:,None]>t[None,:,:]).sum(axis=2).astype(np.int8); qlevel=(q[qi,:,None]>t).sum(axis=1).astype(np.int8); delta=levels-qlevel[None,:]
        best=np.full(len(pre),np.inf,dtype=np.float32)
        for anchor in targets[qi,:max(1,a.anchors)]:
            direction=x[int(anchor)]-q[qi]; aligned=np.sign(direction)[None,:]*delta>0; step=1.0/np.maximum(np.abs(direction),1e-4)
            cost=np.where(aligned,np.abs(delta)*step[None,:],np.abs(delta)*(1.0+step[None,:])).sum(axis=1); best=np.minimum(best,cost)
        ranked=pre[np.argsort(best,kind='stable')]; out={}
        for k in (256,512,1024,2048,5000,10000):
            s=ranked[:k]; out[str(k)]={'thq_top256_recall':float(np.isin(top,s).sum()/256.0),'teacher_top10_survival':float(np.isin(targets[qi],s).sum()/10.0)}
        rows.append({'query':qi,'anchors':int(max(1,a.anchors)),'prefilter_union':int(len(pre)),'ranked':out})
    result={'schema_version':1,'family':'thq_multianchor_transition_cost_oracle_v1','anchors':a.anchors,'queries':count,'query_start':start,'prefilter':a.prefilter,'rows':rows,'elapsed_seconds':time.perf_counter()-begun}
    a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8'); keys=('256','512','1024','2048','5000','10000')
    print(json.dumps({'queries':count,'anchors':a.anchors,'mean_thq_top256_recall':{k:float(np.mean([r['ranked'][k]['thq_top256_recall'] for r in rows])) for k in keys}},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
