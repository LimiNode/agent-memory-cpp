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
    p=argparse.ArgumentParser(); p.add_argument('--receipt',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); p.add_argument('--layout-root',type=Path,required=True); a=p.parse_args()
    r=json.loads(a.receipt.read_text(encoding='utf-8')); raw=json.loads(a.raw.read_text(encoding='utf-8'))
    require(r['family']==raw['family']=='semantic_r4_fused_payload_layout_v1', 'family differs')
    require(r['execution_status']=='EXECUTED' and r['production_activation'] is False, 'status differs')
    require(r['raw_sha256']==sha256(a.raw) and r['runner_sha256']==sha256(a.runner), 'provenance differs')
    require(raw.get('protocol', {}).get('record_order') == 'document_id', 'record order differs')
    require(raw.get('protocol', {}).get('touch_semantics') == 'one_byte_per_record', 'touch scope differs')
    rows={x['layout']:x for x in raw['rows']}; require(set(rows)=={'fused-flat','fused-page-aligned'}, 'layout grid differs')
    require(rows['fused-flat']['page_amplification']==1.0, 'flat amplification differs')
    require(rows['fused-page-aligned']['file_bytes'] >= rows['fused-flat']['file_bytes'], 'paged size differs')
    require(rows['fused-flat']['checksum']==rows['fused-page-aligned']['checksum'], 'payload checksum differs')
    for row in rows.values():
        name = 'fused-flat.bin' if row['layout'] == 'fused-flat' else 'fused-page-aligned.bin'
        path = a.layout_root / name
        require(path.is_file(), f'layout output missing: {name}')
        require(path.stat().st_size == int(row['file_bytes']), f'layout size differs: {name}')
        require(sha256(path) == row['sha256'], f'layout SHA differs: {name}')
        expected = (int(row['records']) * 4096 if row['layout'] == 'fused-page-aligned'
                    else int(row['records']) * int(row['record_bytes']))
        require(int(row['file_bytes']) == expected, f'layout accounting differs: {name}')
    print(json.dumps({'family':'semantic_r4_fused_payload_layout_audit_v1','status':'PASS','rows':len(rows)},sort_keys=True))

if __name__=='__main__': main()
