#!/usr/bin/env python3
"""Fail-closed audit for the THQ physical-frontier wave-one receipts."""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path

ROOT = Path(__file__).parents[2] / 'guides' / 'experiments'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
NAMES = ('2026-09-12-thq-block-min-oracle-result.json',
         '2026-09-12-thq-physical-frontier-wave1-result.json',
         '2026-09-12-thq-joint-bound-diagnostic-result.json',
         '2026-09-12-thq-joint4-bound-diagnostic-result.json')

def main() -> int:
    errors = []
    rows = []
    for name in NAMES:
        p = ROOT / name
        if not p.is_file():
            errors.append(f'missing receipt: {name}')
            continue
        try: data = json.loads(p.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'invalid JSON {name}: {exc}'); continue
        local = []
        for key in ('fixture_manifest_sha256', 'runner_sha256'):
            if not HEX64.fullmatch(str(data.get(key, ''))): local.append(f'{key} must be SHA-256')
        for key in ('schema_version', 'family', 'queries', 'execution_status'):
            if key not in data: local.append(f'missing {key}')
        if data.get('production_activation') is not False: local.append('production_activation must be false')
        if not isinstance(data.get('rows'), list) or len(data['rows']) != int(data.get('queries', -1)):
            local.append('rows/queries mismatch')
        if data.get('execution_status') not in ('EXECUTED', 'EXECUTED_SMOKE'):
            local.append('invalid execution_status')
        if local: errors.extend(f'{name}: {e}' for e in local)
        rows.append({'receipt': name, 'queries': data.get('queries'), 'errors': local})
    result = {'schema_version': 1, 'family': 'thq_physical_frontier_wave1_audit_v1',
              'receipts': rows, 'errors': errors, 'status': 'pass' if not errors else 'fail'}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1

if __name__ == '__main__': raise SystemExit(main())
