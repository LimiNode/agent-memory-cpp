#!/usr/bin/env python3
"""Materialize a deterministic packed-code-prefix physical-order surrogate."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''): h.update(c)
    return h.hexdigest()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--thq-manifest', type=Path, required=True)
    ap.add_argument('--output-root', type=Path, required=True)
    args = ap.parse_args()
    m = json.loads(args.thq_manifest.read_text(encoding='utf-8'))
    info = m['outputs']['thq4_document_codes']
    n, width = map(int, info['shape'])
    if Path(info['path']).stat().st_size != int(info['bytes']):
        raise ValueError('source document-code size mismatch')
    if sha256(Path(info['path'])) != info['sha256']:
        raise ValueError('source document-code SHA mismatch')
    src = np.memmap(info['path'], mode='r', dtype=np.uint8, shape=(n, width))
    key = np.asarray(src[:, :8], dtype=np.uint64)
    key = sum(key[:, i] << (8 * i) for i in range(8))
    order = np.argsort(key, kind='stable')
    args.output_root.mkdir(parents=True, exist_ok=False)
    out_path = args.output_root / 'thq4-document-codes-prefix-order.u8'
    out = np.memmap(out_path, mode='w+', dtype=np.uint8, shape=(n, width))
    for start in range(0, n, 16384):
        stop = min(n, start + 16384)
        out[start:stop] = src[order[start:stop]]
    out.flush()
    order_path = args.output_root / 'new-position-to-document-id.i8'
    np.asarray(order, dtype='<i8').tofile(order_path)
    manifest = {
        'schema_version': 1, 'family': 'thq_code_prefix_physical_order_control_v1',
        'source_manifest': str(args.thq_manifest),
        'source_manifest_sha256': sha256(args.thq_manifest),
        'source_document_codes_sha256': info['sha256'],
        'order': 'stable ascending uint64 little-endian first-8-byte key',
        'documents': n, 'bytes_per_document': width,
        'payload': {'path': str(out_path), 'bytes': int(out_path.stat().st_size), 'sha256': sha256(out_path)},
        'new_position_to_document_id': {'path': str(order_path), 'bytes': int(order_path.stat().st_size), 'sha256': sha256(order_path)},
        'physical_bytes_semantics': 'logical packed payload; no MDBX/OS page counters',
        'surrogate_not_r4': True, 'production_activation': False,
    }
    (args.output_root / 'layout-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')

if __name__ == '__main__': main()
