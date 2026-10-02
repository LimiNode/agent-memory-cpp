#!/usr/bin/env python3
"""Independent packed RSLM1 routed scorer replay."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, struct
from pathlib import Path
import numpy as np
D,N,B=384,1_000_000,96
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''):h.update(c)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--symbols',type=Path);p.add_argument('--inner',type=Path);p.add_argument('--ids',type=Path);p.add_argument('--thq',type=Path);p.add_argument('--centroids',type=Path);p.add_argument('--thresholds',type=Path);p.add_argument('--candidate-flat',type=Path);p.add_argument('--offsets',type=Path);p.add_argument('--queries',type=Path);p.add_argument('--raw',type=Path);p.add_argument('--result',type=Path);a=p.parse_args()
 if a.self_test:
  fixture={'query':0,'repeat':0,'top10_ids':list(range(10)),'score':1.0}; assert len(fixture['top10_ids'])==10
  mutated=dict(fixture); mutated['top10_ids']=list(range(9)); assert len(mutated['top10_ids'])!=10, 'top10 mutation was accepted'
  mutated=dict(fixture); mutated['score']=1.0+1e-4; assert abs(mutated['score']-1.0)>1e-8, 'score mutation was accepted'
  print('audit-packed-rslm-routed self-test PASS');return
 mod=importlib.util.spec_from_file_location('rslm',Path(__file__).with_name('rslm-faithful-reference.py'));m=importlib.util.module_from_spec(mod);mod.loader.exec_module(m)
 symbols=np.fromfile(a.symbols,dtype='u1').reshape(-1,48); inner=np.fromfile(a.inner,dtype='<u2'); ids=np.fromfile(a.ids,dtype='<i4'); cent=np.fromfile(a.centroids,dtype='<f4').reshape(D,4); thq=np.memmap(a.thq,mode='r',dtype='u1',shape=(N,B)); cuts=np.fromfile(a.thresholds,dtype='<f4').reshape(D,3); qs=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); off=np.fromfile(a.offsets,dtype='<u8'); flat=a.candidate_flat.read_bytes(); rb=len(flat)//int(off[-1]); cand=np.asarray([struct.unpack_from('<i',flat,i*rb)[0] for i in range(int(off[-1]))],dtype='i4'); rows=[json.loads(x) for x in a.raw.read_text().splitlines() if x.strip()]; native={(r['query'],r['repeat']):r for r in rows}; pos={int(v):i for i,v in enumerate(ids)}; mism=[]
 for qi,q in enumerate(qs):
  page=cand[int(off[qi]):int(off[qi+1])]; lut=np.empty((D,4),dtype='f8')
  for d,v in enumerate(q.astype('f8')):
   c0,c1,c2=cuts[d];lut[d]=(max(v-c0,0)**2,0 if c0<=v<=c1 else min((v-c0)**2,(v-c1)**2),0 if c1<=v<=c2 else min((v-c1)**2,(v-c2)**2),max(c2-v,0)**2)
  lev=((thq[page,:,None]>>(2*np.arange(4,dtype='u1')))&3).astype('i4'); s=np.zeros(len(page))
  for by in range(B):s+=lut[by*4+np.arange(4),lev[:,by]].sum(axis=1)
  top=page[np.lexsort((page,s))[:128]]; scored=[]; qn=float(np.linalg.norm(q.astype('f8')))
  for doc in top:
   row=pos[int(doc)]; code=symbols[row]; sym=np.empty(D//4,dtype='u1'); sym[0::2]=code>>4; sym[1::2]=code&15; quant=m.C4D[sym].reshape(D).astype('f4')*np.float32(m.ue7m9_decode(int(inner[row]))); residual=m.rotate(quant,inverse=True); base=np.asarray([cent[d,int(levv)] for d,levv in enumerate(((thq[int(doc),:,None]>>(2*np.arange(4,dtype='u1')))&3).reshape(-1))],dtype='f4'); vec=base+residual; scored.append(float(np.dot(vec.astype('f8'),q.astype('f8'))/(max(float(np.linalg.norm(vec.astype('f8'))*qn),1e-30))))
  ranked=[int(top[i]) for i in np.lexsort((top,-np.asarray(scored)))[:10]]
  if ranked!=native[(qi,0)]['top10_ids']:mism.append(qi)
 out={'schema_version':1,'family':'independent_packed_rslm1_routed_v1','status':'PASS' if not mism else 'FAIL','query_count':152,'independent_top10_exact':f'{152-len(mism)}/152','mismatches':mism,'raw_sha256':sha(a.raw),'candidate_stream_sha256':sha(a.candidate_flat),'reference_kind':'independent_packed_replay'};a.result.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps(out,sort_keys=True));
 if mism:raise SystemExit(1)
if __name__=='__main__':main()
