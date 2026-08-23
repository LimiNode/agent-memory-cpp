#!/usr/bin/env python3
"""Emit the pinned query partitions for task-aware static locator selection."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

PREFIX=b"task-aware-static-locator-v1\0"
def split(ids:list[str])->dict[str,list[str]]:
    ordered=sorted(ids,key=lambda value:(int.from_bytes(hashlib.sha256(PREFIX+value.encode('utf-8')).digest()[:8],'little'),value))
    if len(ordered)!=648 or len(set(ordered))!=648:raise ValueError('task-aware selector requires 648 unique query IDs')
    return {'selector_training':ordered[:324],'configuration_selection':ordered[324:486],'internal_evaluation':ordered[486:]}
def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--query-ids',type=Path);p.add_argument('--self-test',action='store_true');a=p.parse_args()
    try:
        if a.self_test:
            value=split([f'q{n}' for n in range(648)]);assert [len(x) for x in value.values()]==[324,162,162] and not(set(value['selector_training'])&set(value['configuration_selection']));print('task-aware static locator planner self-test passed');return 0
        if a.query_ids is None:p.error('--query-ids is required')
        print(json.dumps(split([line.strip() for line in a.query_ids.read_text(encoding='utf-8').splitlines() if line.strip()]),indent=2,ensure_ascii=False));return 0
    except (OSError,ValueError) as e:print(f'plan-task-aware-static-locator: {e}');return 1
if __name__=='__main__':raise SystemExit(main())
