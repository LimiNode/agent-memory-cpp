#!/usr/bin/env python3
"""Fail-closed audit for deep ranker and topology follow-up receipts."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
def digest(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--result',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); a=p.parse_args(); d=json.loads(a.result.read_text())
 checks={'execution_status':d.get('execution_status')=='EXECUTED','production_activation':d.get('production_activation') is False,'runner_sha256':d.get('runner_sha256')==digest(a.runner),'raw_sha256':d.get('raw_output',{}).get('sha256')==digest(a.raw),'raw_bytes':d.get('raw_output',{}).get('bytes')==a.raw.stat().st_size,'held_out':d.get('held_out_queries',0)>0}
 if not all(checks.values()): raise SystemExit(json.dumps({'status':'FAIL','checks':checks},indent=2))
 print(json.dumps({'status':'PASS','checks':checks},indent=2))
if __name__=='__main__': main()
