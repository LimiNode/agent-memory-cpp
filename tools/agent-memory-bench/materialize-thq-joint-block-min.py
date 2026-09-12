#!/usr/bin/env python3
"""Materialize joint THQ level-presence summaries (pairwise or 4-way)."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1<<20), b''): h.update(c)
    return h.hexdigest()

def unpack(raw, docs, width):
    packed=raw.reshape(docs,(width+3)//4); out=np.empty((docs,width),dtype=np.uint8)
    for c in range(width): out[:,c]=(packed[:,c//4]>>(2*(c%4)))&3
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--layout-manifest',type=Path,required=True); ap.add_argument('--output-root',type=Path,required=True); ap.add_argument('--group-size',type=int,choices=(2,4),default=2); args=ap.parse_args()
    layout=json.loads(args.layout_manifest.read_text(encoding='utf-8')); g=args.group_size; width=int(layout['coords_per_block']); groups=width//g
    args.output_root.mkdir(parents=True,exist_ok=False); tiles=[]; grouped={}
    for r in layout['blocks']: grouped.setdefault(int(r['tile']),[]).append(r)
    bytes_per_group = (4 ** g) // 8
    for tile, rs in sorted(grouped.items()):
        rs.sort(key=lambda r:int(r['block'])); summary=np.zeros((len(rs),groups,bytes_per_group),dtype=np.uint8)
        for bi,r in enumerate(rs):
            source=Path(r['path'])
            if source.stat().st_size != int(r['bytes']):
                raise RuntimeError(f"block size mismatch: {source}")
            if sha256(source) != r['sha256']:
                raise RuntimeError(f"block sha256 mismatch: {source}")
            levels=unpack(np.fromfile(source,dtype=np.uint8),int(r['document_count']),int(r.get('coordinate_count',width)))
            for gi in range(groups):
                code=(levels[:,gi*g:gi*g+g].astype(np.uint32)* (4**np.arange(g,dtype=np.uint32))).sum(axis=1)
                for state in np.unique(code):
                    summary[bi, gi, int(state)//8] |= np.uint8(1 << (int(state)%8))
        p=args.output_root/f'tile-{tile:05d}.joint{g}.bin'; summary.tofile(p)
        tiles.append({'tile':tile,'blocks':len(rs),'groups':groups,'bytes':int(p.stat().st_size),'sha256':sha256(p),'path':str(p)})
    out={'schema_version':1,'family':f'thq_block_min_joint{g}_presence_metadata_v1','source_layout_manifest':str(args.layout_manifest),'source_layout_manifest_sha256':sha256(args.layout_manifest),'tile_docs':layout['tile_docs'],'coords_per_block':width,'group_size':g,'states':4**g,'encoding':f'bitset_{bytes_per_group}_bytes_per_group','tiles':tiles,'lower_bound':f'sum_groups min_(joint state in S) sum_i ADC_i(query, level_i)','physical_bytes_semantics':'logical metadata bytes','production_activation':False}
    (args.output_root/'summary-manifest.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n',encoding='utf-8')
if __name__=='__main__': main()
