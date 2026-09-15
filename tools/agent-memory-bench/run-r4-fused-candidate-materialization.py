#!/usr/bin/env python3
"""Materialize the actual three-seed AoSoA candidate stream as fused THQ records."""
from __future__ import annotations
import argparse, hashlib, itertools, json, struct
from pathlib import Path
from typing import Any
import numpy as np

SEEDS=(2026082701,2026082702,2026082703); QUERIES=152; A=8192; BUDGET=5000; PAGE=4096; RECORD=148
def req(v,m):
    if not v: raise RuntimeError(m)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for x in iter(lambda:f.read(1<<20),b''): h.update(x)
    return h.hexdigest()
def validate_file(path, metadata, label):
    req(path.is_file(), f"missing {label}: {path}")
    if metadata.get('sha256') is not None:
        req(sha(path) == str(metadata['sha256']), f"{label} SHA differs")
    if metadata.get('bytes') is not None:
        req(path.stat().st_size == int(metadata['bytes']), f"{label} size differs")
def read_order(path):
    raw=path.read_bytes(); magic,ver,q,ac,passes=struct.unpack_from('<5I',raw,0); req((magic,ver,q,passes)==(0x314f5243,1,QUERIES,1),'order header')
    av=struct.unpack_from(f'<{ac}I',raw,20); req(A in av,'A absent'); off=20+4*ac; dt=np.dtype([('address','<u4'),('score','<f4')]); out=[]
    for _ in range(QUERIES):
        chosen=None
        for a in av:
            x=np.frombuffer(raw,dtype=dt,count=a,offset=off).copy(); off+=a*8
            if a==A: chosen=x
        out.append((chosen['address'].astype(np.int64),chosen['score'].astype(np.float32)))
    return out
def route(root,record,documents):
    rr=root/f"seed-{int(record['seed'])}"; m={x['role']:x for x in record['mappings']}
    for role in ('address_offsets','address_counts','physical_to_document'):
        validate_file(rr/m[role]['file'],m[role],f"{record['seed']}/{role}")
    off=np.fromfile(rr/m['address_offsets']['file'],dtype='<u4'); cnt=np.fromfile(rr/m['address_counts']['file'],dtype='<u4'); phys=np.fromfile(rr/m['physical_to_document']['file'],dtype='<i4'); req(int(cnt.sum())==len(phys),'route size')
    return [phys[int(o):int(o+c)] for o,c in zip(off,cnt)]
def main():
 p=argparse.ArgumentParser(); p.add_argument('--thq-manifest',type=Path,required=True); p.add_argument('--r4-layout-manifest',type=Path,required=True); p.add_argument('--r4-layout-root',type=Path,required=True); p.add_argument('--native-receipt',type=Path,required=True); p.add_argument('--output-root',type=Path,required=True); a=p.parse_args()
 thq=json.loads(a.thq_manifest.read_text()); layout=json.loads(a.r4_layout_manifest.read_text()); native=json.loads(a.native_receipt.read_text()); n=int(thq['documents']); codes=np.memmap(Path(thq['outputs']['thq4_document_codes']['path']),mode='r',dtype=np.uint8,shape=(n,144)); queries=np.memmap(Path(thq['references']['queries']['path']),mode='r',dtype='<f4',shape=(QUERIES,int(thq['dimension'])))
 routes=[]; orders=[]
 for rec in layout['seeds']:
  routes.append(route(a.r4_layout_root,rec,n)); b=next(x for x in native['native_outputs'] if int(x['seed'])==int(rec['seed']) and x['layout']=='aosoa_avx2' and int(x['lanes'])==32); orders.append(read_order(Path(b['order'])))
 a.output_root.mkdir(parents=True,exist_ok=True); flat=a.output_root/'candidate-fused-flat.bin'; blocked=a.output_root/'candidate-fused-page-blocked.bin'; rows=[]; flat_bytes=blocked_bytes=0; flat_sha=hashlib.sha256(); blocked_sha=hashlib.sha256()
 with flat.open('wb') as ff, blocked.open('wb') as bf:
  for qi in range(QUERIES):
   seen=np.zeros(n,dtype=np.bool_); selected=[]; pos=[0,0,0]; touched=0; entries=0
   while len(selected)<BUDGET:
    stream=max(range(3),key=lambda i: (float(orders[i][qi][1][pos[i]]) if pos[i]<A else -np.inf,-i)); req(pos[stream]<A,'route exhausted'); address=int(orders[stream][qi][0][pos[stream]]); pos[stream]+=1; ids=routes[stream][address]; fresh=ids[~seen[ids]]; seen[ids]=True; selected.extend(int(x) for x in fresh); touched+=1; entries+=len(ids)
   arr=np.asarray(selected,dtype=np.int32); req(len(arr)>=BUDGET,'whole-posting budget not reached'); payload=np.empty((len(arr),RECORD),dtype=np.uint8); payload[:,:4]=arr.view(np.uint8).reshape(len(arr),4); payload[:,4:]=np.asarray(codes[arr]); data=payload.tobytes(); ff.write(data); flat_sha.update(data); pad=(-len(data))%PAGE
   for start in range(0,len(payload),27):
    chunk=payload[start:start+27].tobytes(); chunk+=b'\0'*(PAGE-len(chunk)); bf.write(chunk); blocked_sha.update(chunk)
   flat_bytes+=len(data); blocked_bytes+=((len(payload)+26)//27)*PAGE
   rows.append({'query':qi,'candidate_count':len(arr),'postings_touched':touched,'posting_entries_touched':entries,'mean_abs_doc_delta':float(np.abs(np.diff(arr.astype(np.int64))).mean()),'flat_pages':(flat_bytes+PAGE-1)//PAGE,'blocked_pages':blocked_bytes//PAGE})
 raw={'schema_version':1,'family':'semantic_r4_fused_candidate_materialization_v1','rows':rows,'protocol':{'seeds':list(SEEDS),'A':A,'budget':BUDGET,'record':'[int32 doc_id][144-byte THQ4]','stream':'AoSoA32 K1 -> K16 score order -> three-seed global fusion','page_block_records':27}}
 rawp=a.output_root/'candidate-fused.raw.json'; rawp.write_text(json.dumps(raw,indent=2)+'\n'); receipt={'family':raw['family'],'execution_status':'EXECUTED','production_activation':False,'raw_sha256':sha(rawp),'runner_sha256':sha(Path(__file__)),'flat_file':{'path':str(flat),'bytes':flat_bytes,'sha256':flat_sha.hexdigest(),'pages':(flat_bytes+PAGE-1)//PAGE},'blocked_file':{'path':str(blocked),'bytes':blocked_bytes,'sha256':blocked_sha.hexdigest(),'pages':blocked_bytes//PAGE},'thq_manifest_sha256':sha(a.thq_manifest),'layout_manifest_sha256':sha(a.r4_layout_manifest),'native_receipt_sha256':sha(a.native_receipt)}; (a.output_root/'candidate-fused.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
if __name__=='__main__': main()
