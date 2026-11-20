#!/usr/bin/env python3
"""Check the new rebound ADC comparison, not historical R4 or applied quality."""
from __future__ import annotations

import argparse
import copy
import json
import math
import mmap
from pathlib import Path
import runpy
import struct
from collections import defaultdict


EFF = runpy.run_path(str(Path(__file__).with_name('audit-adc-efficiency.py')))
require, quantile, payload = EFF['require'], EFF['quantile'], EFF['payload']
COUNTS = (5020, 5003, 5008, 5009, 5012, 5002, 5023, 5008)
ARMS = ['pair_fp32_scalar', 'pair_fp32_avx2_block32', 'A0_original',
        'A0_template_control', 'A7_combined', 'byte_lut_unrolled4']
CONFIG = dict(query_indices=list(range(8)), top_k=[16, 128], repeats=5, warmups=1,
              order_seed=20261121, arms=ARMS, order_policy='mt19937_raw_modulo_fisher_yates',
              quantile='nearest_rank', combined_mask=31)
EXTRA_INPUTS = {
    'candidate_flat': (76208200, 'b77d29f0435faf581576581ca7ccb812700ea87fe831610cae595fbdadb46096'),
    'candidate_raw': (37898, '4e98f84c39a617c36dd5e749a2165d5a1e187ee2a2039d96d748cdce32a0ada7')}
PREFLIGHT = dict(status='PASS', validated_groups=152, validated_records=762082,
                 canonical_payload_comparisons=762082, pair_score_comparisons=40085,
                 byte_score_comparisons=40085, integer_score_comparisons=40085,
                 enclosure_checks=40085, arm_cases=96, all_refined_score_bits='PASS',
                 byte_reference_kind='independent_exhaustive_byte_lut_unrolled4')


def check_records(metadata, codes, records, query_count=152):
    """Check the stream itself; the benchmark must still charge real gather."""
    require(metadata['query_count'] == query_count and len(metadata['rows']) == query_count and
            metadata['record_bytes'] == 100 and len(codes) % 96 == 0, 'candidate framing')
    offset, membership = 0, []
    for qi, row in enumerate(metadata['rows']):
        count = row['candidate_count']
        require(row['query'] == qi and type(count) is int and count > 0 and
                count <= len(codes) // 96 and (offset + count) * 100 <= len(records),
                'candidate group ordinal/count/range')
        seen = set()
        for index in range(offset, offset + count):
            position = index * 100
            document = struct.unpack_from('<i', records, position)[0]
            require(0 <= document < len(codes) // 96 and document not in seen,
                    'invalid/duplicate candidate ID')
            seen.add(document)
            require(records[position + 4:position + 100] == codes[document * 96:(document + 1) * 96],
                    'rebound payload/canonical code disagreement')
        if qi < 8:
            membership.append(seen)
        offset += count
    require(offset * 100 == len(records), 'trailing/missing candidate records')
    return membership


def actual_membership(manifest):
    paths = {record['role']: Path(record['path']) for record in manifest['inputs']}
    metadata = EFF['read_json'](paths['candidate_raw'])
    require(metadata['family'] == 'canonical_thq4_rebound_candidate_materialization_v1' and
            metadata['execution_status'] == 'EXECUTED' and
            metadata['canonical_thq4_sha256'] == next(record['sha256'] for record in manifest['inputs']
                                                     if record['role'] == 'codes'), 'rebound identity')
    require(tuple(row['candidate_count'] for row in metadata['rows'][:8]) == COUNTS,
            'exposed group mapping')
    with paths['codes'].open('rb') as code_file, paths['candidate_flat'].open('rb') as flat_file:
        with mmap.mmap(code_file.fileno(), 0, access=mmap.ACCESS_READ) as codes, \
                mmap.mmap(flat_file.fileno(), 0, access=mmap.ACCESS_READ) as flat:
            return check_records(metadata, codes, flat)


def input_controls():
    codes = b''.join(bytes([value]) * 96 for value in range(4))
    metadata = dict(query_count=2, record_bytes=100,
                    rows=[dict(query=0, candidate_count=2), dict(query=1, candidate_count=1)])
    records = b''.join(struct.pack('<i', document) + codes[document * 96:(document + 1) * 96]
                       for document in (3, 1, 0))
    require(check_records(metadata, codes, records, 2) == [{3, 1}, {0}], 'validator input fixture')
    changes = [
        lambda m, r: (m, r[:-1]),
        lambda m, r: (m, r + b'\0'),
        lambda m, r: (m, struct.pack('<i', -1) + r[4:]),
        lambda m, r: (m, struct.pack('<i', 4) + r[4:]),
        lambda m, r: (m, r[:100] + r[:100] + r[200:]),
        lambda m, r: (m, r[:4] + bytes([99]) + r[5:]),
        lambda m, r: (dict(m, record_bytes=148), r),
        lambda m, r: (dict(m, query_count=1), r),
        lambda m, r: (dict(m, rows=[dict(query=1, candidate_count=2), m['rows'][1]]), r),
        lambda m, r: (dict(m, rows=[dict(query=0, candidate_count=-1), m['rows'][1]]), r)]
    for change in changes:
        altered, data = change(copy.deepcopy(metadata), records)
        try:
            check_records(altered, codes, data, 2)
        except ValueError:
            continue
        raise ValueError('input corruption accepted')
    return len(changes)


def work(row, count, top, arm):
    EFF['counts'](row, count, top, arm)
    padded = (count + 31) // 32 * 32
    packed = arm in ('pair_fp32_avx2_block32', 'A7_combined')
    # Independent live vector payload minima, not just raw/reference agreement.
    minimum = count * (4 + 8)
    if arm in ('A0_original', 'A0_template_control'):
        minimum = count * (10 + 2 * 8)
    elif arm == 'A7_combined':
        minimum = 4 * count + 2 * padded + 2 * top * 8
    require(row['vector_capacity_bytes'] >= minimum, 'candidate scratch payload minimum')
    require(row['query_tables_allocated_bytes'] == 135984, 'candidate owned query-table payload')
    require(type(row['preparation_capacity_bytes']) is int and
            row['preparation_capacity_bytes'] >= count * 100 + (96 * padded if packed else 0),
            'candidate preparation payload')
    require(row['total_vector_capacity_bytes'] == row['preparation_capacity_bytes'] +
            row['vector_capacity_bytes'], 'total query vector capacity')
    return tuple(row[field] for field in ('exact_evaluations', 'physical_exact_evaluations',
                 'vector_capacity_bytes', 'preparation_capacity_bytes', 'total_vector_capacity_bytes'))


def validate(raw, membership=None):
    require(raw['schema_version'] == 1 and raw['family'] == 'r6_certified_adc_candidates' and
            raw['status'] == 'EXECUTED' and raw['quality_status'] == 'NOT_EXECUTED' and
            raw['source_identity'] == 'EXTERNAL_MANIFEST_REQUIRED' and
            raw['decision_status'] == 'INDEPENDENT_AUDIT_REQUIRED', 'candidate result identity/scope')
    require(raw['configuration'] == CONFIG, 'candidate frozen configuration')
    require(raw['avx2_compiled'] is True and raw['flt_eval_method'] == 0 and
            raw['compiler']['id'] in ('GNU', 'Clang', 'MSVC') and raw['compiler']['version'],
            'candidate compiler/FP eligibility')
    require(raw['sizeof_size_t'] == 8, 'candidate x64 size_t accounting')
    require(raw['candidate_counts'] == list(COUNTS), 'candidate group counts')
    require(raw['preflight'] == PREFLIGHT, 'candidate preflight coverage')
    require(raw['sample_count'] == 480 and raw['trial_count'] == 80 and
            raw['warmup_executions'] == 96, 'candidate execution coverage')
    references = {}
    for case in raw['reference_cases']:
        query, top, count = case['query_index'], case['top_k'], case['documents']
        require(type(query) is int and 0 <= query < 8 and top in (16, 128) and
                count == COUNTS[query] and (query, top) not in references, 'candidate reference identity')
        results = {}
        for row in case['arms']:
            arm = row['arm']
            require(arm in ARMS and arm not in results, 'candidate reference arm')
            selected = payload(row, 1000000, top)
            if membership is not None:
                require(set(selected[0]) <= membership[query], 'selected ID outside candidate group')
            results[arm] = selected, work(row, count, top, arm)
        require(set(results) == set(ARMS), 'candidate reference arm coverage')
        require(all(results[arm][0] == results[ARMS[0]][0] for arm in ARMS[1:5]),
                'candidate pair-bit parity')
        fixed = results['A0_original'][1][0]
        require(all(results[arm][1][0] == fixed for arm in ('A0_template_control', 'A7_combined')),
                'candidate fixed-bound work drift')
        require(results['byte_lut_unrolled4'][1][:2] == (count, count), 'nonexhaustive byte control')
        references[query, top] = results
    require(len(references) == 16, 'candidate reference coverage')
    groups, trials, seen = defaultdict(list), defaultdict(list), set()
    for row in raw['raw_samples']:
        query, top, arm, repeat = (row[field] for field in ('query_index', 'top_k', 'arm', 'repeat'))
        require((query, top) in references and arm in ARMS and type(repeat) is int and 0 <= repeat < 5,
                'candidate sample identity')
        identity = query, top, arm, repeat
        require(identity not in seen and row['documents'] == COUNTS[query], 'duplicate/foreign sample')
        seen.add(identity)
        require(payload(row, 1000000, top) == references[query, top][arm][0] and
                work(row, COUNTS[query], top, arm) == references[query, top][arm][1],
                'candidate score/work/reference drift')
        require(row['gather_included'] is True and row['workspace_reused'] is False and
                row['packing_included'] is (arm in ('pair_fp32_avx2_block32', 'A7_combined')),
                'candidate full-query preparation contract')
        require(isinstance(row['total_ms'], (int, float)) and not isinstance(row['total_ms'], bool) and
                math.isfinite(row['total_ms']) and row['total_ms'] > 0, 'candidate full-query time')
        trial = (([16, 128].index(top) * 8 + query) * 5 + repeat)
        require(row['trial_id'] == trial, 'candidate trial identity')
        groups[top, arm].append(row)
        trials[trial].append(row)
    require(len(seen) == 480 and len(trials) == 80, 'candidate sample/trial cardinality')
    engine = EFF['LEGACY']['mt19937'](20261121)
    for trial in range(80):
        order = list(ARMS)
        for end in range(len(order) - 1, 0, -1):
            index = next(engine) % (end + 1)
            order[end], order[index] = order[index], order[end]
        rows = sorted(trials[trial], key=lambda row: row['order_position'])
        require([row['order_position'] for row in rows] == list(range(6)) and
                [row['arm'] for row in rows] == order, 'candidate randomized order')
    summaries = {}
    for key, rows in groups.items():
        require(len(rows) == 40, 'candidate summary sample count')
        times = [row['total_ms'] for row in rows]
        summaries[key] = dict(p50_ms=quantile(times, 50), p95_ms=quantile(times, 95),
                              near_exhaustive=10 * sum(row['exact_evaluations'] for row in rows) >=
                                  9 * sum(row['documents'] for row in rows),
                              owned_payload_bytes=max(row['total_vector_capacity_bytes'] +
                                  row['query_tables_allocated_bytes'] for row in rows))
    seen_summaries = set()
    for row in raw['summaries']:
        key = row['top_k'], row['arm']
        require(key in summaries and key not in seen_summaries and row['sample_count'] == 40,
                'candidate summary identity')
        seen_summaries.add(key)
        for field in ('p50_ms', 'p95_ms'):
            EFF['LEGACY']['near'](row[field], summaries[key][field], 'candidate summary/' + field)
    require(len(seen_summaries) == 12, 'candidate summary coverage')
    return summaries


def decisions(summary):
    result = []
    for top in (16, 128):
        base = min(ARMS[:2], key=lambda arm: summary[top, arm]['p50_ms'])
        control, candidate = summary[top, base], summary[top, 'A7_combined']
        memory = candidate['owned_payload_bytes'] <= control['owned_payload_bytes']
        passed = (candidate['p50_ms'] <= .9 * control['p50_ms'] and
                  candidate['p95_ms'] <= control['p95_ms'] and memory and not candidate['near_exhaustive'])
        result.append(dict(top_k=top, baseline=base,
            p50_ratio=candidate['p50_ms'] / control['p50_ms'],
            p95_ratio=candidate['p95_ms'] / control['p95_ms'], memory_payload_gate=memory,
            near_exhaustive=candidate['near_exhaustive'], full_path_gate='PASS' if passed else 'KEEP_CURRENT',
            byte_lut_p50_ratio=summary[top, 'byte_lut_unrolled4']['p50_ms'] / control['p50_ms'],
            byte_lut_contract='SEPARATE_ENGINEERING_BASELINE_NOT_PAIR_BIT_EXACT'))
    return result


def synthetic_fixture():
    """Synthetic validator data, not an executed workload or score oracle."""
    raw = dict(schema_version=1, family='r6_certified_adc_candidates', status='EXECUTED',
        quality_status='NOT_EXECUTED', source_identity='EXTERNAL_MANIFEST_REQUIRED',
        decision_status='INDEPENDENT_AUDIT_REQUIRED', configuration=copy.deepcopy(CONFIG),
        avx2_compiled=True, flt_eval_method=0, sizeof_size_t=8,
        compiler=dict(id='GNU', version='VALIDATOR_ONLY'),
        candidate_counts=list(COUNTS), sample_count=480, trial_count=80, warmup_executions=96,
        preflight=copy.deepcopy(PREFLIGHT),
        reference_cases=[], raw_samples=[], summaries=[])
    engine, trial = EFF['LEGACY']['mt19937'](20261121), 0
    for top in (16, 128):
        for query, count in enumerate(COUNTS):
            arms = []
            for index, arm in enumerate(ARMS):
                exhaustive = arm in ARMS[:2] or arm == ARMS[-1]
                selected = list(range(query * 10000 + (top if arm == ARMS[-1] else 0),
                                      query * 10000 + (2 * top if arm == ARMS[-1] else top)))
                arms.append(dict(arm=arm, selected_ids=selected, selected_score_bits=[0x3f800000] * top,
                    exact_evaluations=count if exhaustive else top + 2,
                    physical_exact_evaluations=(count + 31) // 32 * 32 if arm == 'pair_fp32_avx2_block32'
                        else count if exhaustive else top + 2,
                    vector_capacity_bytes=count * 30, preparation_capacity_bytes=count * 300,
                    query_tables_allocated_bytes=135984,
                    total_vector_capacity_bytes=count * 330))
            raw['reference_cases'].append(dict(query_index=query, top_k=top, documents=count, arms=arms))
            for repeat in range(5):
                order = list(ARMS)
                for end in range(len(order) - 1, 0, -1):
                    index = next(engine) % (end + 1)
                    order[end], order[index] = order[index], order[end]
                for position, arm in enumerate(order):
                    stats = copy.deepcopy(arms[ARMS.index(arm)])
                    raw['raw_samples'].append(dict(query_index=query, top_k=top, documents=count,
                        repeat=repeat, trial_id=trial, order_position=position,
                        gather_included=True, packing_included=arm in ('pair_fp32_avx2_block32', 'A7_combined'),
                        workspace_reused=False,
                        total_ms=1 + ARMS.index(arm), **stats))
                trial += 1
        for index, arm in enumerate(ARMS):
            raw['summaries'].append(dict(top_k=top, arm=arm, sample_count=40, p50_ms=index + 1, p95_ms=index + 1))
    return raw


def mutation_controls(raw):
    def underreport_memory(result, field):
        for case in result['reference_cases']:
            for row in case['arms']:
                if row['arm'] == 'A7_combined':
                    row[field] = 0 if field == 'vector_capacity_bytes' else case['documents'] * 100
                    row['total_vector_capacity_bytes'] = row['vector_capacity_bytes'] + row['preparation_capacity_bytes']
        for row in result['raw_samples']:
            if row['arm'] == 'A7_combined':
                row[field] = 0 if field == 'vector_capacity_bytes' else row['documents'] * 100
                row['total_vector_capacity_bytes'] = row['vector_capacity_bytes'] + row['preparation_capacity_bytes']

    changes = [
        lambda r: underreport_memory(r, 'vector_capacity_bytes'),
        lambda r: underreport_memory(r, 'preparation_capacity_bytes'),
        lambda r: r.__setitem__('sizeof_size_t', 4),
        lambda r: r['raw_samples'][0]['selected_score_bits'].__setitem__(0, r['raw_samples'][0]['selected_score_bits'][0] ^ 1),
        lambda r: r['raw_samples'][0]['selected_ids'].__setitem__(0, -1),
        lambda r: r['raw_samples'].pop(),
        lambda r: r['raw_samples'].append(copy.deepcopy(r['raw_samples'][0])),
        lambda r: r['raw_samples'][0].__setitem__('trial_id', -1),
        lambda r: r['raw_samples'][0].__setitem__('order_position', -1),
        lambda r: r['raw_samples'][0].__setitem__('total_ms', float('nan')),
        lambda r: r['raw_samples'][0].__setitem__('physical_exact_evaluations', r['raw_samples'][0]['physical_exact_evaluations'] + 1),
        lambda r: r['raw_samples'][0].__setitem__('vector_capacity_bytes', r['raw_samples'][0]['vector_capacity_bytes'] + 1),
        lambda r: r['raw_samples'][0].__setitem__('preparation_capacity_bytes', r['raw_samples'][0]['preparation_capacity_bytes'] + 1),
        lambda r: r['raw_samples'][0].__setitem__('total_vector_capacity_bytes', r['raw_samples'][0]['total_vector_capacity_bytes'] + 1),
        lambda r: r['raw_samples'][0].__setitem__('query_tables_allocated_bytes', 0),
        lambda r: r['raw_samples'][0].__setitem__('exact_evaluations', 0),
        lambda r: r['configuration'].__setitem__('combined_mask', 63),
        lambda r: r['configuration'].__setitem__('order_seed', 1),
        lambda r: r['candidate_counts'].__setitem__(0, 1),
        lambda r: r['reference_cases'].pop(),
        lambda r: r['reference_cases'][0]['arms'].pop(),
        lambda r: r['summaries'][0].__setitem__('p95_ms', 0),
        lambda r: r['summaries'].pop(),
        lambda r: r.__setitem__('avx2_compiled', False),
        lambda r: r.__setitem__('warmup_executions', 0),
        lambda r: r['raw_samples'][0].__setitem__('gather_included', False),
        lambda r: r['raw_samples'][0].__setitem__('packing_included', not r['raw_samples'][0]['packing_included']),
        lambda r: r['raw_samples'][0].__setitem__('workspace_reused', True),
        lambda r: r['preflight'].__setitem__('integer_score_comparisons', 0),
        lambda r: r['preflight'].__setitem__('validated_records', 0),
        lambda r: r.__setitem__('quality_status', 'PASS'),
        lambda r: r.__setitem__('decision_status', 'PRODUCTION_QUALIFIED')]
    for change in changes:
        altered = copy.deepcopy(raw)
        change(altered)
        try:
            validate(altered)
        except (ValueError, KeyError):
            continue
        raise ValueError('candidate result corruption accepted')
    return len(changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--verify-inputs', action='store_true')
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--expected-source')
    parser.add_argument('--mutation-self-test', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        raw = synthetic_fixture()
        validate(raw)
        controls = mutation_controls(raw) + input_controls()
        print(f'ADC candidate validator-only self-test: PASS; {controls} rejection controls; no benchmark')
        return
    require(args.directory is not None, '--directory required')
    manifest = EFF['read_json'](args.directory / 'manifest.json')
    if args.expected_source:
        require(manifest['source_commit'] == args.expected_source, 'unexpected candidate measured source')
    EFF['audit_manifest'](args.directory, manifest, args.verify_inputs, args.repository, EXTRA_INPUTS)
    raw = EFF['read_json'](args.directory / 'raw.json')
    for record in manifest['inputs']:
        require(raw['inputs'][record['role']] == dict(path=record['path'], bytes=record['bytes']),
                'candidate raw/manifest input binding')
    membership = actual_membership(manifest) if args.verify_inputs else None
    result = validate(raw, membership)
    controls = mutation_controls(raw) + input_controls() if args.mutation_self_test else 0
    print(json.dumps(dict(status='PASS', samples=480, mutations=controls, decisions=decisions(result),
        scope='NEW rebound candidate-local mechanics; native scoring oracle; not historical replay or applied quality'), indent=2))


if __name__ == '__main__':
    main()
