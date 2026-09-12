#!/usr/bin/env python3
"""Fail-closed audit for the THQ physical-frontier wave-one receipts."""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path

ROOT = Path(__file__).parents[2] / 'guides' / 'experiments'
FROZEN = Path(r'E:\_repoz\agent-memory-cpp\tmp\thq-full-scan-v2\manifest.json')
LAYOUT = Path(r'E:\_repoz\agent-memory-workspaces\materialized-progressive-thq-aosoa-512x32-v2\layout-manifest.json')
MARGINAL = Path(r'E:\_repoz\agent-memory-workspaces\thq-block-min-512x32\summary-manifest.json')
JOINT2 = Path(r'E:\_repoz\agent-memory-workspaces\thq-block-min-joint2-512x32\summary-manifest.json')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
NAMES = ('2026-09-12-thq-block-min-oracle-result.json',
         '2026-09-12-thq-physical-frontier-wave1-result.json',
         '2026-09-12-thq-joint-bound-diagnostic-result.json',
         '2026-09-12-thq-joint4-bound-diagnostic-result.json')
RUNNERS = {
    NAMES[0]: Path(__file__).with_name('run-thq-block-min-oracle.py'),
    NAMES[1]: Path(__file__).with_name('run-thq-physical-frontier-wave1.py'),
    NAMES[2]: Path(__file__).with_name('run-thq-joint-bound-diagnostic.py'),
    NAMES[3]: Path(__file__).with_name('run-thq-joint-bound-diagnostic.py'),
}

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

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
        if data.get('runner_sha256') != sha256(RUNNERS[name]): local.append('runner_sha256 does not match current runner')
        if data.get('fixture_manifest_sha256') != sha256(FROZEN): local.append('fixture_manifest_sha256 does not match canonical frozen manifest')
        if name in (NAMES[0], NAMES[1], NAMES[2], NAMES[3]) and data.get('layout_manifest_sha256') != sha256(LAYOUT): local.append('layout_manifest_sha256 does not match canonical layout')
        expected_summary = MARGINAL if name in (NAMES[0], NAMES[1]) else (JOINT2 if name == NAMES[2] else Path(r'E:\_repoz\agent-memory-workspaces\thq-block-min-joint4-512x32\summary-manifest.json'))
        if name == NAMES[0] and data.get('summary_manifest_sha256') != sha256(MARGINAL): local.append('summary manifest hash mismatch')
        if name == NAMES[1] and data.get('summary_manifest_sha256') != sha256(MARGINAL): local.append('summary manifest hash mismatch')
        if name in (NAMES[2], NAMES[3]) and data.get('joint_manifest_sha256') != sha256(expected_summary): local.append('joint manifest hash mismatch')
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
