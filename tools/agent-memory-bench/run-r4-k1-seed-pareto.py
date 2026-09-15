#!/usr/bin/env python3
"""Evaluate 1/2/3-seed Pareto points from frozen AoSoA K1->K16 streams."""
from __future__ import annotations
import argparse, hashlib, itertools, json, struct
from pathlib import Path
from typing import Any
import numpy as np

SEEDS=(2026082701,2026082702,2026082703); A_VALUES=(8192,16384); BUDGET=5000; QUERIES=152

def require(v: bool, m: str) -> None:
    if not v: raise RuntimeError(m)

def sha256(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for x in iter(lambda:f.read(1<<20),b''): h.update(x)
    return h.hexdigest()

def aggregate(v: list[float]) -> dict[str,float]:
    a=np.asarray(v,dtype=np.float64)
    return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),
            'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}

def read_orders(path: Path) -> dict[int,list[tuple[np.ndarray,np.ndarray]]]:
    raw=path.read_bytes(); magic,version,q,ac,passes=struct.unpack_from('<5I',raw,0)
    require((magic,version,q,passes)==(0x314f5243,1,QUERIES,1),'order header differs')
    av=struct.unpack_from(f'<{ac}I',raw,20); require(tuple(av)==A_VALUES,'A grid differs')
    off=20+4*ac; dt=np.dtype([('address','<u4'),('score','<f4')]); out={a:[] for a in A_VALUES}
    for _ in range(QUERIES):
        for a in av:
            x=np.frombuffer(raw,dtype=dt,count=a,offset=off).copy(); off+=a*8
            require(np.unique(x['address']).size==a,'duplicate address')
            out[a].append((x['address'].astype(np.int64),x['score'].astype(np.float32)))
    require(off==len(raw),'trailing order bytes'); return out

def load_route(root: Path, record: dict[str,Any], documents: int) -> tuple[list[np.ndarray],np.ndarray]:
    rr=root/f"seed-{int(record['seed'])}"; m={str(x['role']):x for x in record['mappings']}
    offsets=np.fromfile(rr/m['address_offsets']['file'],dtype='<u4'); counts=np.fromfile(rr/m['address_counts']['file'],dtype='<u4')
    physical=np.fromfile(rr/m['physical_to_document']['file'],dtype='<i4'); require(int(counts.sum())==len(physical),'route size differs')
    postings=[physical[int(o):int(o+c)] for o,c in zip(offsets,counts)]
    doc_to_address=np.full(documents,-1,dtype=np.int32)
    for address,ids in enumerate(postings): doc_to_address[ids]=address
    require(np.all(doc_to_address>=0),'document/address mapping incomplete')
    return postings,doc_to_address

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--thq-manifest',type=Path,required=True); p.add_argument('--r4-layout-manifest',type=Path,required=True)
    p.add_argument('--r4-layout-root',type=Path,required=True); p.add_argument('--native-receipt',type=Path,required=True)
    p.add_argument('--raw-output',type=Path,required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    thq=json.loads(a.thq_manifest.read_text()); layout=json.loads(a.r4_layout_manifest.read_text()); native=json.loads(a.native_receipt.read_text())
    n=int(thq['documents']); teachers=np.memmap(Path(thq['references']['teacher_ids']['path']),mode='r',dtype='<i8',shape=(QUERIES,10))
    routes={}; inverse={}; orders={}; bindings=[]
    for rec in layout['seeds']:
        seed=int(rec['seed']); routes[seed],inverse[seed]=load_route(a.r4_layout_root,rec,n)
        b=next(x for x in native['native_outputs'] if int(x['seed'])==seed and x['layout']=='aosoa_avx2' and int(x['lanes'])==32)
        op=Path(b['order']); require(sha256(op)==b['order_sha256'],'native order SHA differs'); orders[seed]=read_orders(op)
        bindings.append({'seed':seed,'order':str(op),'sha256':sha256(op)})
    rows=[]
    subsets=[x for r in (1,2,3) for x in itertools.combinations(SEEDS,r)]
    for subset in subsets:
      for av in A_VALUES:
       for qi in range(QUERIES):
        seen=np.zeros(n,dtype=np.bool_); selected=[]; positions=[0]*len(subset); entries=touched=0
        teacher=np.asarray(teachers[qi],dtype=np.int64); first_rank={}
        while len(selected)<BUDGET:
            available=[i for i,s in enumerate(subset) if positions[i]<av]; require(bool(available),'route exhausted')
            stream=max(available,key=lambda i:(float(orders[subset[i]][av][qi][1][positions[i]]),-i)); seed=subset[stream]
            address=int(orders[seed][av][qi][0][positions[stream]]); positions[stream]+=1
            ids=routes[seed][address]; fresh=ids[~seen[ids]]; seen[ids]=True
            base=len(selected); selected.extend(int(x) for x in fresh); entries+=len(ids); touched+=1
            for j,doc in enumerate(fresh):
                if int(doc) in teacher and int(doc) not in first_rank: first_rank[int(doc)]=base+j
        hit=np.isin(teacher,np.asarray(selected,dtype=np.int64)); provenance=[]
        for doc,ok in zip(teacher,hit):
            per_seed=[]
            for seed in subset:
                address=int(inverse[seed][int(doc)]); order=orders[seed][av][qi][0]; where=np.flatnonzero(order==address)
                per_seed.append({'seed':seed,'address':address,'in_coarse_A':bool(where.size),
                                 'k16_rank_in_A':int(where[0]) if where.size else None})
            provenance.append({'teacher_id':int(doc),'hit':bool(ok),'candidate_entry_rank':first_rank.get(int(doc)),'seeds':per_seed})
        rows.append({'query':qi,'seeds':list(subset),'seed_count':len(subset),'addresses_refined_per_seed':av,
                     'requested_candidate_budget':BUDGET,'candidate_count':len(selected),'postings_touched':touched,
                     'posting_entries_touched':entries,'teacher_recall':float(hit.sum()/10),'teacher_provenance':provenance})
    summaries=[]
    for subset in subsets:
      for av in A_VALUES:
        x=[r for r in rows if tuple(r['seeds'])==subset and r['addresses_refined_per_seed']==av]
        summaries.append({'seeds':list(subset),'seed_count':len(subset),'addresses_refined_per_seed':av,'query_count':len(x),
                          **{k:aggregate([float(y[k]) for y in x]) for k in ('teacher_recall','candidate_count','postings_touched','posting_entries_touched')}})
    raw={'schema_version':1,'family':'semantic_r4_k1_seed_pareto_v1','rows':rows}; payload=(json.dumps(raw,separators=(',',':'),sort_keys=True)+'\n').encode(); a.raw_output.parent.mkdir(parents=True,exist_ok=True); a.output.parent.mkdir(parents=True,exist_ok=True); a.raw_output.write_bytes(payload)
    receipt={'schema_version':1,'family':raw['family'],'execution_status':'EXECUTED','production_activation':False,'summaries':summaries,
             'thq_manifest_sha256':sha256(a.thq_manifest),'r4_layout_manifest_sha256':sha256(a.r4_layout_manifest),'native_receipt_sha256':sha256(a.native_receipt),
             'native_bindings':bindings,'runner_sha256':sha256(Path(__file__)),'raw_output':{'path':str(a.raw_output),'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'rows':len(rows)}}
    a.output.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')

if __name__=='__main__': main()
