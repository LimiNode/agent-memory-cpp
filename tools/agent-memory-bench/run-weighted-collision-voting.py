#!/usr/bin/env python3
"""Weighted collision-voting oracle over packed ordinal postings."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np

def stable_top(score, ids, k):
    k=min(k,len(ids))
    if not k:return ids[:0]
    if len(ids)<=k:return ids[np.lexsort((ids,-score))]
    cut=np.partition(score,-k)[-k]; mask=score>cut; ties=np.sort(ids[score==cut])[:max(0,k-int(mask.sum()))]
    chosen=np.concatenate((ids[mask],ties)); vals=np.concatenate((score[mask],np.full(len(ties),cut,dtype=score.dtype)))
    return chosen[np.lexsort((chosen,-vals))]

def pack(a):
    out=np.zeros(a.shape[0],np.uint64)
    for i in range(a.shape[1]): out |= a[:,i].astype(np.uint64) << np.uint64(2*i)
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--thq-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--query-limit',type=int,default=152);p.add_argument('--block-width',type=int,default=8);p.add_argument('--radius',type=int,default=1);p.add_argument('--chunk',type=int,default=50000);a=p.parse_args()
    m=json.loads(a.thq_manifest.read_text());n,d=int(m['documents']),int(m['dimension']);q=min(int(m['queries']),a.query_limit);refs,out=m['references'],m['outputs']
    codes=np.memmap(out['thq4_document_codes']['path'],mode='r',dtype=np.uint8,shape=(n,144));qc=np.memmap(out['thq4_query_codes']['path'],mode='r',dtype=np.uint8,shape=(q,144));teachers=np.memmap(refs['teacher_ids']['path'],mode='r',dtype='<i8',shape=(q,10))
    levels=np.empty((n,d),np.uint8)
    for i in range(0,n,a.chunk):
        j=min(n,i+a.chunk);levels[i:j]=np.unpackbits(np.asarray(codes[i:j]),axis=1,bitorder='little')[:,:d*3].reshape(j-i,d,3).sum(2)
    ql=np.unpackbits(np.asarray(qc),axis=1,bitorder='little')[:,:d*3].reshape(q,d,3).sum(2).astype(np.uint8);blocks=d//a.block_width
    postings=[]
    for b in range(blocks):
        lo,hi=b*a.block_width,(b+1)*a.block_width; keys=pack(levels[:,lo:hi]); order=np.argsort(keys,kind='stable'); sk=keys[order]; uniq,first=np.unique(sk,return_index=True); postings.append((uniq,[order[first[k]:first[k+1] if k+1<len(first) else len(order)] for k in range(len(first))]))
    budgets=(256,512,1000,2000,5000,10000); systems={x:[] for x in ('union_exact','collision_count','inverse_frequency','rank_decay')};ids=np.arange(n,dtype=np.int64)
    for qi in range(q):
        pieces=[]; votes={x:{} for x in systems if x!='union_exact'}; probes=0;started=time.perf_counter()
        for b,(uniq,lists) in enumerate(postings):
            lo,hi=b*a.block_width,(b+1)*a.block_width;base=ql[qi,lo:hi]; ps=[(base,1.0)]
            if a.radius:
                for c in range(a.block_width):
                    if base[c]>0:
                        z=base.copy();z[c]-=1;ps.append((z,.5))
                    if base[c]<3:
                        z=base.copy();z[c]+=1;ps.append((z,.5))
            for probe,decay in ps:
                probes+=1; key=int(pack(probe[None,:])[0]); pos=int(np.searchsorted(uniq,key));
                if pos>=len(uniq) or int(uniq[pos])!=key: continue
                arr=lists[pos];pieces.append(arr); freq=len(arr)
                for doc in arr:
                    di=int(doc);votes['collision_count'][di]=votes['collision_count'].get(di,0)+1;votes['inverse_frequency'][di]=votes['inverse_frequency'].get(di,0)+1.0/max(1,freq);votes['rank_decay'][di]=votes['rank_decay'].get(di,0)+decay
        cand=np.unique(np.concatenate(pieces)) if pieces else np.empty(0,np.int64);gen=(time.perf_counter()-started)*1000
        if len(cand):dist=np.abs(levels[cand].astype(np.int16)-ql[qi].astype(np.int16)).sum(1,np.uint16)
        else:dist=np.empty(0,np.uint16)
        score_arrays={'union_exact':-dist.astype(np.float32)}
        for name in ('collision_count','inverse_frequency','rank_decay'):score_arrays[name]=np.asarray([votes[name].get(int(x),0.0) for x in cand],np.float32)
        for name,score in score_arrays.items():
            row={'query':qi,'probe_count':probes,'candidate_count':int(len(cand)),'postings_touched':int(sum(len(x) for x in pieces)),'generation_ms':gen}
            for b in budgets: row[f'survival_{b}']=float(np.isin(teachers[qi],stable_top(score,cand,b)).sum())/10
            systems[name].append(row)
    result={'schema_version':1,'family':'weighted_collision_voting_oracle_v1','documents':n,'queries':q,'dimension':d,'representation':{'name':'THQ4-384','levels':4,'bits':1152},'probe_policy':'packed width 8, radius 1; exact and ±1 ordinal level probes','budgets':list(budgets),'production_activation':False,'systems':{k:{'rows':v,'summary':{x:{'mean':float(np.mean([r[x] for r in v])),'p50':float(np.quantile([r[x] for r in v],.5)),'min':float(np.min([r[x] for r in v]))} for x in ['candidate_count','postings_touched']+[f'survival_{b}' for b in budgets]}} for k,v in systems.items()}}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
