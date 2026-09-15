#!/usr/bin/env python3
"""Compare actual fused candidate-stream storage representations and page models."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
import numpy as np

PAGE=4096; QUERIES=152; BUDGET=5000; RECORD_THQ=144; RECORD_ID=4
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1<<20),b''): h.update(x)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--candidate-flat',type=Path,required=True); p.add_argument('--output-root',type=Path,required=True); a=p.parse_args()
 total=QUERIES*BUDGET; raw=np.memmap(a.candidate_flat,mode='r',dtype=np.uint8,shape=(total,148)); ids=np.frombuffer(np.asarray(raw[:,:4]).tobytes(),dtype='<i4').reshape(total); codes=np.asarray(raw[:,4:],dtype=np.uint8)
 levels=np.unpackbits(codes,axis=1,bitorder='little')[:,:384*3].reshape(total,384,3).sum(axis=2).astype(np.uint8)
 ordinal=(levels[:,0::4] | (levels[:,1::4] << 2) |
          (levels[:,2::4] << 4) | (levels[:,3::4] << 6)).astype(np.uint8)
 a.output_root.mkdir(parents=True,exist_ok=True); rows=[]
 for name,payload in [('thermometer',148),('packed_ordinal',100)]:
  flat=total*payload; flat_pages=math.ceil(flat/PAGE); blocked_pages=QUERIES*math.ceil(BUDGET*payload/PAGE); blob_pages=QUERIES*math.ceil((16+BUDGET*payload)/PAGE); chunk_records=max(1,(PAGE-16)//payload); chunk_pages=QUERIES*math.ceil(BUDGET/chunk_records)
  rows.extend([{'representation':name,'layout':'flat','records':total,'record_bytes':payload,'logical_payload_bytes':flat,'physical_bytes':flat,'pages':flat_pages,'padding_bytes':flat_pages*PAGE-flat},
               {'representation':name,'layout':'page_blocked','records':total,'record_bytes':payload,'logical_payload_bytes':flat,'physical_bytes':blocked_pages*PAGE,'pages':blocked_pages,'padding_bytes':blocked_pages*PAGE-flat},
               {'representation':name,'layout':'mdbx_blob_model','records':total,'record_bytes':payload,'logical_payload_bytes':flat,'physical_bytes':blob_pages*PAGE,'pages':blob_pages,'padding_bytes':blob_pages*PAGE-flat,'key_overhead_bytes':QUERIES*16},
               {'representation':name,'layout':'mdbx_chunk_model','records':total,'record_bytes':payload,'logical_payload_bytes':flat,'physical_bytes':chunk_pages*PAGE,'pages':chunk_pages,'padding_bytes':chunk_pages*PAGE-flat,'key_overhead_bytes':chunk_pages*16,'records_per_chunk':chunk_records}])
 raw_out={'schema_version':1,'family':'semantic_r4_storage_layout_bakeoff_v1','rows':rows,'input':{'path':str(a.candidate_flat),'sha256':sha(a.candidate_flat),'records':total,'candidate_unique_ids':int(np.unique(ids).size)}}; rp=a.output_root/'storage-bakeoff.raw.json'; rp.write_text(json.dumps(raw_out,indent=2)+'\n'); (a.output_root/'storage-bakeoff.receipt.json').write_text(json.dumps({'family':raw_out['family'],'execution_status':'EXECUTED','production_activation':False,'raw_sha256':sha(rp),'runner_sha256':sha(Path(__file__)),'rows':rows,'input_sha256':sha(a.candidate_flat)},indent=2)+'\n')
if __name__=='__main__': main()
