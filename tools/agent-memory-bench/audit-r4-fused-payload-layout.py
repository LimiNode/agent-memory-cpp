#!/usr/bin/env python3
"""Fail-closed audit for fused payload layout control receipts."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1<<20), b''): h.update(chunk)
    return h.hexdigest()

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--receipt',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); a=p.parse_args()
    r=json.loads(a.receipt.read_text()); raw=json.loads(a.raw.read_text())
    require(r['family']==raw['family']=='semantic_r4_fused_payload_layout_v1', 'family differs')
    require(r['execution_status']=='EXECUTED' and not r['production_activation'], 'status differs')
    require(r['raw_sha256']==sha256(a.raw) and r['runner_sha256']==sha256(a.runner), 'provenance differs')
    require(raw.get('protocol', {}).get('record_order') == 'document_id', 'record order differs')
    rows={x['layout']:x for x in raw['rows']}
    require(set(rows)=={'fused-flat','fused-page-aligned'}, 'layout grid differs')
    require(rows['fused-flat']['page_amplification']==1.0, 'flat amplification differs')
    require(rows['fused-page-aligned']['file_bytes'] >= rows['fused-flat']['file_bytes'],
            'page-aligned size differs')
    require(rows['fused-flat']['checksum']==rows['fused-page-aligned']['checksum'],
            'payload checksum differs')
    print(json.dumps({'family':'semantic_r4_fused_payload_layout_audit_v1','status':'PASS','rows':len(rows)},sort_keys=True))

if __name__=='__main__': main()
