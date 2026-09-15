#!/usr/bin/env python3
"""Materialize and measure immutable fused [doc_id + packed THQ] payload layouts."""
from __future__ import annotations
import argparse, hashlib, json, struct, time
from pathlib import Path
import numpy as np

RECORD_BYTES = 4 + 144
PAGE_BYTES = 4096

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--thq-manifest', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--sample-docs', type=int, default=200000)
    a = p.parse_args()
    m = json.loads(a.thq_manifest.read_text())
    n = int(m['documents']); width = int(m['outputs']['thq4_document_codes']['bytes']) // n
    src = Path(m['outputs']['thq4_document_codes']['path'])
    codes = np.memmap(src, mode='r', dtype=np.uint8, shape=(n, width))
    count = min(n, a.sample_docs)
    a.output_root.mkdir(parents=True, exist_ok=True)
    flat = a.output_root / 'fused-flat.bin'; paged = a.output_root / 'fused-page-aligned.bin'
    ids = np.arange(count, dtype=np.int32)
    flat_payload = np.empty((count, RECORD_BYTES), dtype=np.uint8)
    flat_payload[:, :4] = ids.view(np.uint8).reshape(count, 4)
    flat_payload[:, 4:] = np.asarray(codes[:count])
    flat.write_bytes(flat_payload.tobytes())
    with paged.open('wb') as f:
        for i in range(count):
            record = flat_payload[i].tobytes(); f.write(record)
            pad = (-len(record)) % PAGE_BYTES
            if pad: f.write(b'\0' * pad)
    rows = []
    for path, stride in ((flat, RECORD_BYTES), (paged, PAGE_BYTES)):
        mm = np.memmap(path, mode='r', dtype=np.uint8)
        t0 = time.perf_counter(); checksum = 0
        for i in range(count): checksum += int(mm[i * stride + 4])
        elapsed = (time.perf_counter() - t0) * 1000.0
        rows.append({'layout': path.stem, 'records': count, 'record_bytes': RECORD_BYTES,
                     'file_bytes': path.stat().st_size, 'logical_payload_bytes': count * RECORD_BYTES,
                     'read_ms': elapsed, 'checksum': checksum, 'sha256': sha256(path),
                     'page_amplification': path.stat().st_size / (count * RECORD_BYTES)})
    raw = {'schema_version': 1, 'family': 'semantic_r4_fused_payload_layout_v1', 'rows': rows,
           'protocol': {'record': '[int32 doc_id][144-byte THQ4]', 'record_order': 'document_id',
                        'touch_semantics': 'one_byte_per_record', 'page_bytes': PAGE_BYTES,
                        'source_manifest_sha256': sha256(a.thq_manifest)}}
    raw_path = a.output_root / 'fused-payload.raw.json'; raw_path.write_text(json.dumps(raw, indent=2) + '\n')
    receipt = {'schema_version': 1, 'family': raw['family'], 'execution_status': 'EXECUTED',
               'production_activation': False, 'raw_sha256': sha256(raw_path),
               'runner_sha256': sha256(Path(__file__)), 'rows': rows,
               'source_manifest_sha256': sha256(a.thq_manifest)}
    (a.output_root / 'fused-payload.receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

if __name__ == '__main__': main()
