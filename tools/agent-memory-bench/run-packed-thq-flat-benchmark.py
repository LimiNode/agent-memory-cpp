#!/usr/bin/env python3
"""Packed two-bit THQ flat-scan benchmark and equality control."""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import numpy as np

def main():
 p=argparse.ArgumentParser();p.add_argument('--thq-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--query-limit',type=int,default=8);p.add_argument('--chunk',type=int,default=50000);a=p.parse_args();m=json.loads(a.thq_manifest.read_text());n,d=int(m['documents']),int(m['dimension']);q=min(int(m['queries']),a.query_limit);o=m['outputs'];qc=np.memmap(o['thq4_query_codes']['path'],mode='r',dtype=np.uint8,shape=(q,144));dc=np.memmap(o['thq4_document_codes']['path'],mode='r',dtype=np.uint8,shape=(n,144));t=np.memmap(m['references']['teacher_ids']['path'],mode='r',dtype='<i8',shape=(q,10))
 levels=np.empty((n,d),np.uint8)
 for i in range(0,n,a.chunk):
  j=min(n,i+a.chunk);levels[i:j]=np.unpackbits(np.asarray(dc[i:j]),axis=1,bitorder='little')[:,:d*3].reshape(j-i,d,3).sum(2)
 ql=np.unpackbits(np.asarray(qc),axis=1,bitorder='little')[:,:d*3].reshape(q,d,3).sum(2).astype(np.uint8)
 packed=np.empty((n,96),np.uint8);packed[:]=((levels[:,0::4]&3)|((levels[:,1::4]&3)<<2)|((levels[:,2::4]&3)<<4)|((levels[:,3::4]&3)<<6));qp=((ql[:,0::4]&3)|((ql[:,1::4]&3)<<2)|((ql[:,2::4]&3)<<4)|((ql[:,3::4]&3)<<6)).astype(np.uint8)
 lut=np.empty((256,4),np.uint8)
 for x in range(256):lut[x]=[(x>>s)&3 for s in (0,2,4,6)]
 rows=[];equal=True
 for qi in range(q):
  st=time.perf_counter();dist=np.empty(n,np.uint16)
  for i in range(0,n,a.chunk):
   j=min(n,i+a.chunk);decoded=lut[packed[i:j]].reshape(j-i,d);dist[i:j]=np.abs(decoded.astype(np.int16)-ql[qi].astype(np.int16)).sum(1,np.uint16)
  ms=(time.perf_counter()-st)*1000; top=np.lexsort((np.arange(n),dist))[:256];rows.append({'query':qi,'scan_ms':ms,'survival_256':float(np.isin(t[qi],top).sum())/10});
  if qi<min(2,q): equal &= bool(np.array_equal(dist, np.unpackbits(np.bitwise_xor(np.asarray(dc),qc[qi]),axis=1,bitorder='little').sum(1)))
 result={'schema_version':1,'family':'packed_thq_flat_benchmark_v1','documents':n,'queries':q,'dimension':d,'representations':{'thermometer':{'bits':1152,'bytes_per_document':144},'packed_ordinal':{'bits':768,'bytes_per_document':96}},'packed_exact_equality_first_two_queries':equal,'rows':rows,'summary':{'scan_ms_p50':float(np.quantile([r['scan_ms'] for r in rows],.5)),'scan_ms_p95':float(np.quantile([r['scan_ms'] for r in rows],.95)),'survival_256_mean':float(np.mean([r['survival_256'] for r in rows]))},'production_activation':False}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
