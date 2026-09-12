#!/usr/bin/env python3
"""Fail-closed audit for the semantic K-means routing oracle."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

HEX64 = re.compile(r'^[0-9a-f]{64}$')
ROOT = Path(__file__).parents[2] / 'guides' / 'experiments'
RECEIPT = ROOT / '2026-09-12-semantic-k8-r4-spann-oracle-result.json'
RUNNER = Path(__file__).with_name('run-semantic-kmeans-routing-oracle.py')

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--thq-manifest', type=Path, required=True)
    args = ap.parse_args()
    errors = []
    if not RECEIPT.is_file():
        errors.append('missing receipt')
    else:
        data = json.loads(RECEIPT.read_text(encoding='utf-8'))
        for key in ('fixture_manifest_sha256', 'runner_sha256', 'sample_ids_sha256'):
            if not HEX64.fullmatch(str(data.get(key, ''))): errors.append(f'{key} must be SHA-256')
        if data.get('fixture_manifest_sha256') != sha256(args.thq_manifest): errors.append('fixture hash mismatch')
        if data.get('runner_sha256') != sha256(RUNNER): errors.append('runner hash mismatch')
        expected = len(data.get('clusters', [])) * len(data.get('replications', [])) * int(data.get('queries', -1)) * len(data.get('nprobe', []))
        if len(data.get('rows', [])) != expected: errors.append('rows do not match protocol matrix')
        if data.get('production_activation') is not False: errors.append('production_activation must be false')
        if data.get('protocol', {}).get('teacher_ids_used_for_index') is not False: errors.append('teacher IDs must be evaluation-only')
    result = {'schema_version': 1, 'family': 'semantic_kmeans_routing_oracle_audit_v1', 'errors': errors, 'status': 'pass' if not errors else 'fail'}
    print(json.dumps(result, indent=2, sort_keys=True)); return 0 if not errors else 1

if __name__ == '__main__': raise SystemExit(main())
