#!/usr/bin/env python3
"""Safe-pruning progressive ordinal THQ scan oracle."""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np

def main():
 p=argparse.ArgumentParser();p.add_argument('--thq-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--query-limit',type=int,default=8);p.add_argument('--chunk',type=int,default=50000);a=p.parse_args();m=json.loads(a.thq_manifest.read_text());n,d=int(m['documents']),int(m['dimension']);q=min(int(m['queries']),a.query_limit);o=m['outputs'];dc=np.memmap(o['thq4_document_codes']['path'],mode='r',dtype=np.uint8,shape=(n,144));qc=np.memmap(o['thq4_query_codes']['path'],mode='r',dtype=np.uint8,shape=(q,144));t=np.memmap(m['references']['teacher_ids']['path'],mode='r',dtype='<i8',shape=(q,10));lev=np.empty((n,d),np.uint8)
 for i in range(0,n,a.chunk):j=min(n,i+a.chunk);lev[i:j]=np.unpackbits(np.asarray(dc[i:j]),axis=1,bitorder='little')[:,:d*3].reshape(j-i,d,3).sum(2)
 ql=np.unpackbits(np.asarray(qc),axis=1,bitorder='little')[:,:d*3].reshape(q,d,3).sum(2).astype(np.uint8);var=np.var(lev.astype(np.float32),axis=0);bud=256;systems={'fixed':[],'variance':[],'query_adaptive':[]}
 for qi in range(q):
  full=np.abs(lev.astype(np.int16)-ql[qi].astype(np.int16)).sum(1,np.uint16);cut=int(np.partition(full,bud-1)[bud-1]);orders={'fixed':np.arange(d),'variance':np.argsort(-var),'query_adaptive':np.argsort(-np.abs(lev[:min(n,100000)].mean(0)-ql[qi]))};
  for name,order in orders.items():
   partial=np.zeros(n,np.uint16); checkpoints=[];st=time.perf_counter()
   for end in (32,64,128,256,384):
    cols=order[len(order[:end-32]):end] if end>32 else order[:32];partial += np.abs(lev[:,cols].astype(np.int16)-ql[qi,cols].astype(np.int16)).sum(1,np.uint16);active=int(np.count_nonzero(partial<=cut));checkpoints.append({'coordinates':end,'active':active,'evaluated_fraction':active/n})
   top=np.lexsort((np.arange(n),full))[:bud];systems[name].append({'query':qi,'cutoff':cut,'checkpoints':checkpoints,'survival_256':float(np.isin(t[qi],top).sum())/10,'scan_ms':(time.perf_counter()-st)*1000})
 result={'schema_version':1,'family':'progressive_thq_scan_oracle_v1','documents':n,'queries':q,'dimension':d,'metric':'ordinal_l1','prune_rule':'partial_distance > exact top-256 cutoff','systems':systems,'production_activation':False}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
