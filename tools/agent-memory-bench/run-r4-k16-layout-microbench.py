#!/usr/bin/env python3
"""Measure row-major versus dimension-major K16 intra-address scoring."""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path
import numpy as np

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--codec-root',type=Path,required=True)
    p.add_argument('--codec-manifest',type=Path,required=True); p.add_argument('--query-file',type=Path,required=True)
    p.add_argument('--output-root',type=Path,required=True); p.add_argument('--sample',type=int,default=65536)
    a=p.parse_args(); m=json.loads(a.codec_manifest.read_text()); rec=next(x for x in m['seeds'][0]['representations'] if x['id']=='int8')
    path=a.codec_root/'seed-2026082701'/rec['file']; total=int(rec['bytes'])//int(rec['record_bytes']); count=min(total,a.sample)
    raw=np.memmap(path,mode='r',dtype=np.uint8,shape=(total,int(rec['record_bytes'])))[:count]
    codes=raw[:,:384].astype(np.float32); scales=raw[:,384].astype(np.float32)/255.0+1e-3
    vectors=(codes-127.5)*scales[:,None]; rows=vectors.reshape(count//16,16,384); rows=rows[:count//16*16]
    dim=rows.transpose(0,2,1).copy(); q=np.memmap(a.query_file,mode='r',dtype='<f4',shape=(152,384))
    out=[]
    for name,data in [('row_major',rows),('dimension_major',dim)]:
        t=time.perf_counter(); checksum=0.0
        for query in q:
            scores=(np.einsum('d,gld->gl',query,data,optimize=True) if name=='row_major'
                    else np.einsum('d,gdl->gl',query,data,optimize=True))
            checksum+=float(scores[:,:16].sum())
        out.append({'layout':name,'groups':int(len(rows)),'lanes':16,'elapsed_ms':(time.perf_counter()-t)*1000,'checksum':checksum})
    a.output_root.mkdir(parents=True,exist_ok=True); raw_out=a.output_root/'k16-layout.raw.json'
    payload={'schema_version':1,'family':'semantic_r4_k16_layout_microbench_v1','rows':out,'source_sha256':sha256(a.codec_manifest)}
    raw_out.write_text(json.dumps(payload,indent=2)+'\n'); (a.output_root/'k16-layout.receipt.json').write_text(json.dumps({'family':payload['family'],'execution_status':'EXECUTED','production_activation':False,'raw_sha256':sha256(raw_out),'runner_sha256':sha256(Path(__file__)),'rows':out},indent=2)+'\n')

if __name__=='__main__': main()

