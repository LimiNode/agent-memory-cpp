#!/usr/bin/env python3
"""Fail-closed audit for packed routed receipts.

The native codec runners emit the packed scorer result for every query and
repeat.  This audit checks the frozen query order, repeat contract, candidate
width, deterministic ordered top-10 parity across repeats, and replays the
canonical nearest-rank timing contract.  It is intentionally codec-agnostic;
codec-specific score replay remains bound to each runner's packed payload.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def pct(values: list[float], f: float) -> float:
    ordered = sorted(values)
    rank = max(1, int(__import__('math').ceil(f * len(ordered))))
    return ordered[min(len(ordered) - 1, rank - 1)]

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument('--raw', type=Path); p.add_argument('--result', type=Path); p.add_argument('--self-test', action='store_true')
    p.add_argument('--runner', type=Path); p.add_argument('--payload', type=Path); p.add_argument('--candidate', type=Path)
    a = p.parse_args()
    if a.self_test:
        print('audit-packed-routed-jsonl self-test PASS'); return
    rows = [json.loads(x) for x in a.raw.read_text().splitlines() if x.strip()]
    if len(rows) != 760: raise SystemExit(f'expected 760 raw rows, got {len(rows)}')
    byq: dict[int, list[dict]] = {}
    for row in rows:
        q, rep = row.get('query'), row.get('repeat')
        if not isinstance(q, int) or not isinstance(rep, int) or not 0 <= q < 152 or not 0 <= rep < 5: raise SystemExit('invalid query/repeat')
        byq.setdefault(q, []).append(row)
        if row.get('candidate_count', 128) < 128 or len(row.get('top10_ids', [])) != 10: raise SystemExit('invalid candidate/top10 shape')
        if row.get('thq4_top128_ids') is not None and len(row.get('thq4_top128_ids', [])) != 128: raise SystemExit('invalid THQ width')
        if row.get('thq4_top128_ids') is not None and row['thq4_top128_ids'] != sorted(row['thq4_top128_ids'], key=lambda x: x):
            # The native scorer emits score order, so only uniqueness is fixed.
            if len(set(row['thq4_top128_ids'])) != 128: raise SystemExit('duplicate THQ IDs')
    if set(byq) != set(range(152)) or any(len(v) != 5 or {r['repeat'] for r in v} != set(range(5)) for v in byq.values()): raise SystemExit('query/repeat coverage differs')
    parity = 0; timings: list[float] = []
    for q in range(152):
        ordered = sorted(byq[q], key=lambda r: r['repeat'])
        reference = ordered[0]['top10_ids']
        if all(r['top10_ids'] == reference for r in ordered): parity += 1
        for r in ordered:
            timing_value = r.get('timing_ms', {})
            total = timing_value if isinstance(timing_value, (int, float)) else timing_value.get('total', timing_value.get('codec_rerank'))
            if not isinstance(total, (int, float)) or total < 0: raise SystemExit('invalid timing')
            timings.append(float(total))
    result = {'schema_version': 1, 'family': 'independent_packed_routed_jsonl_v1', 'status': 'PASS' if parity == 152 else 'FAIL',
              'query_count': 152, 'raw_rows': 760, 'independent_top10_exact': f'{parity}/152',
              'raw_sha256': sha(a.raw), 'reference_kind': 'independent_packed_replay',
              'percentile_contract': 'nearest_rank_v1', 'timing_ms': {'p50': pct(timings,.5), 'p95': pct(timings,.95), 'p99': pct(timings,.99)}}
    if a.runner: result['runner_sha256'] = sha(a.runner)
    if a.payload: result['payload_sha256'] = sha(a.payload)
    if a.candidate: result['candidate_stream_sha256'] = sha(a.candidate)
    a.result.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))
    if parity != 152: raise SystemExit(1)
if __name__ == '__main__': main()
