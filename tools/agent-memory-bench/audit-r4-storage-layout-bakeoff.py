#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1<<20),b''): h.update(x)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--receipt',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); a=p.parse_args(); r=json.loads(a.receipt.read_text()); raw=json.loads(a.raw.read_text()); assert r['family']==raw['family']=='semantic_r4_storage_layout_bakeoff_v1'; assert r['raw_sha256']==sha(a.raw) and r['runner_sha256']==sha(a.runner) and r['input_sha256']==raw['input']['sha256']; assert len(raw['rows'])==8; assert {x['representation'] for x in raw['rows']}=={'thermometer','packed_ordinal'}; assert {x['layout'] for x in raw['rows']}=={'flat','page_blocked','mdbx_blob_model','mdbx_chunk_model'}; assert all(x['physical_bytes']>=x['logical_payload_bytes'] for x in raw['rows']); print(json.dumps({'family':'semantic_r4_storage_layout_bakeoff_audit_v1','status':'PASS','rows':len(raw['rows'])},sort_keys=True))
if __name__=='__main__': main()
