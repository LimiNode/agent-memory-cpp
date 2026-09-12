#!/usr/bin/env python3
"""Fail-closed audit for the IMI/SPANN wave-two receipt."""
import hashlib, json, re
from pathlib import Path
ROOT=Path(__file__).parents[2]/'guides'/'experiments'; HEX=re.compile(r'^[0-9a-f]{64}$')
def main():
 p=ROOT/'2026-09-12-thq-index-wave2-result.json'; e=[]
 if not p.is_file(): e.append('missing receipt')
 else:
  d=json.loads(p.read_text())
  for k in ('fixture_manifest_sha256','runner_sha256'):
   if not HEX.fullmatch(str(d.get(k,''))): e.append(f'{k} must be SHA-256')
  if d.get('production_activation') is not False: e.append('production_activation must be false')
  if d.get('execution_status') not in ('EXECUTED','EXECUTED_SMOKE'): e.append('invalid execution_status')
  if len(d.get('rows',[]))!=d.get('queries'): e.append('rows/queries mismatch')
 out={'schema_version':1,'family':'thq_index_wave2_audit_v1','errors':e,'status':'pass' if not e else 'fail'}; print(json.dumps(out,indent=2)); return 0 if not e else 1
if __name__=='__main__': raise SystemExit(main())
