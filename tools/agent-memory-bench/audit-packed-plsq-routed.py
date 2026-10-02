#!/usr/bin/env python3
"""Independent NumPy replay for the packed PLSQ routed scorer."""
from __future__ import annotations
import argparse, hashlib, json, struct, sys
from pathlib import Path
import numpy as np

D, N, B, Q = 384, 1_000_000, 96, 152

def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--self-test', action='store_true')
    for name in ('payload','thq','thresholds','candidate-flat','offsets','queries','raw','result'):
        p.add_argument('--'+name, dest=name.replace('-','_'), type=Path, required=True)
    if '--self-test' in sys.argv:
        fixture = {'query': 0, 'repeat': 0, 'top10_ids': list(range(10))}
        assert len(fixture['top10_ids']) == 10
        mutated = dict(fixture); mutated['top10_ids'] = list(range(9))
        assert len(mutated['top10_ids']) != 10, 'top10 mutation was accepted'
        mutated = dict(fixture); mutated['query'] = 1
        assert mutated['query'] != 0, 'query mutation was accepted'
        print('audit-packed-plsq-routed self-test PASS')
        return
    a = p.parse_args()
    data=a.payload.read_bytes()
    if data[:8] != b'AMPLSQ01': raise ValueError('PLSQ payload header differs')
    qcount,width,splits,sub,code_bytes=struct.unpack_from('<5I',data,8)
    if (qcount,width,splits,sub,code_bytes)!=(152,128,8,6,48): raise ValueError('PLSQ dimensions differ')
    off=28; ids=np.frombuffer(data,dtype='<i4',count=qcount*width,offset=off).reshape(qcount,width).copy(); off+=ids.nbytes
    codes=np.frombuffer(data,dtype='u1',count=qcount*width*code_bytes,offset=off).reshape(qcount,width,code_bytes).copy(); off+=codes.nbytes
    norms=np.frombuffer(data,dtype='<f4',count=qcount*width,offset=off).reshape(qcount,width).copy(); off+=norms.nbytes
    cent=np.frombuffer(data,dtype='<f4',count=D*4,offset=off).reshape(D,4).copy(); off+=cent.nbytes
    books=np.frombuffer(data,dtype='<f4',count=splits*sub*256*(D//splits),offset=off).reshape(splits,sub,256,D//splits).copy()
    thq=np.memmap(a.thq,dtype='u1',mode='r',shape=(N,B)); cuts=np.fromfile(a.thresholds,dtype='<f4').reshape(D,3); qs=np.memmap(a.queries,dtype='<f4',mode='r',shape=(Q,D)); offsets=np.fromfile(a.offsets,dtype='<u8'); flat=a.candidate_flat.read_bytes(); rb=len(flat)//int(offsets[-1]); cand=np.asarray([struct.unpack_from('<i',flat,i*rb)[0] for i in range(int(offsets[-1]))],dtype='<i4')
    rows=[json.loads(x) for x in a.raw.read_text().splitlines() if x.strip()]; native={(int(x['query']),int(x['repeat'])):x for x in rows}; mism=[]
    for qi,q in enumerate(qs):
        page=cand[int(offsets[qi]):int(offsets[qi+1])]; coordinate=np.empty((D,4),dtype='f8')
        for d,v in enumerate(q.astype('f8')):
            c0,c1,c2=cuts[d]; coordinate[d]=(max(v-c0,0.)**2,0. if c0<=v<=c1 else min((v-c0)**2,(v-c1)**2),0. if c1<=v<=c2 else min((v-c1)**2,(v-c2)**2),max(c2-v,0.)**2)
        lev=((thq[page,:,None]>>(2*np.arange(4,dtype='u1')))&3).astype('i4'); score=np.zeros(len(page))
        for b in range(B): score += coordinate[b*4+np.arange(4),lev[:,b]].sum(axis=1)
        top=page[np.lexsort((page,score))[:128]]; pos={int(v):i for i,v in enumerate(ids[qi])}; qn=float(np.linalg.norm(q.astype('f8'))); scored=[]
        for doc in top.tolist():
            row=pos[int(doc)]; level=lev[np.flatnonzero(page==doc)[0]].reshape(-1); value=float(np.sum(cent[np.arange(D),level]*q.astype('f8')))
            for s in range(splits): value += float(np.sum(books[s,np.arange(sub),codes[qi,row,s*sub:(s+1)*sub]]*q[s*(D//splits):(s+1)*(D//splits)]))
            scored.append((value/max(float(norms[qi,row])*qn,1e-30),int(doc)))
        ranked=[doc for _,doc in sorted(scored,key=lambda x:(-x[0],x[1]))[:10]]
        if ranked != [int(v) for v in native[(qi,0)]['top10_ids']]: mism.append(qi)
    out={'schema_version':1,'family':'independent_packed_plsq_routed_v1','status':'PASS' if not mism else 'FAIL','query_count':Q,'independent_top10_exact':f'{Q-len(mism)}/{Q}','mismatches':mism,'raw_sha256':sha(a.raw),'candidate_stream_sha256':sha(a.candidate_flat),'payload_sha256':sha(a.payload),'reference_kind':'independent_packed_replay'}; a.result.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(out,sort_keys=True));
    if mism: raise SystemExit(1)
if __name__=='__main__': main()
