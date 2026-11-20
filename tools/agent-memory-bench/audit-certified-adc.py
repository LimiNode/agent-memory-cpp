#!/usr/bin/env python3
"""Replay bounded ADC raw-result invariants; not a source-data quality oracle."""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import struct
from collections import defaultdict
from pathlib import Path


ARMS = ('pair_fp32_scalar', 'pair_fp32_avx2_block32', 'certified_pair_fixed_tau')
COUNTS = (4096, 65536, 1000000)
TOPS = (16, 128)
CONFIG = dict(document_counts=list(COUNTS), top_k=list(TOPS), query_indices=list(range(8)),
              repeats=5, warmups=1, order_seed=20261120, arms=list(ARMS),
              order_policy='mt19937_raw_modulo_6_permutations', quantile='nearest_rank',
              maximum_median_ratio=0.9, maximum_p95_ratio=1.0,
              near_exhaustive_mean_fraction=0.90)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def mt19937(seed):
    """Standard 32-bit engine; Python random.Random has different seeding."""
    state = [seed]
    for i in range(1, 624):
        state.append((1812433253 * (state[-1] ^ (state[-1] >> 30)) + i) & 0xffffffff)
    while True:
        for i in range(624):
            value = (state[i] & 0x80000000) | (state[(i + 1) % 624] & 0x7fffffff)
            state[i] = state[(i + 397) % 624] ^ (value >> 1) ^ (0x9908b0df if value & 1 else 0)
        for value in state:
            value ^= value >> 11
            value ^= (value << 7) & 0x9d2c5680
            value ^= (value << 15) & 0xefc60000
            yield (value ^ (value >> 18)) & 0xffffffff


def near(actual, expected, label):
    require(isinstance(actual, (int, float)) and not isinstance(actual, bool) and
            math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15), label)


def quantile(values, percent):
    return sorted(values)[math.ceil(len(values) * percent / 100) - 1]


def payload(row, count, top):
    ids, bits = row['selected_ids'], row['selected_score_bits']
    require(len(ids) == len(bits) == top and len(set(ids)) == top, 'top-K cardinality/duplicates')
    require(all(type(i) is int and 0 <= i < count for i in ids), 'invalid selected ID')
    require(all(type(b) is int and 0 <= b <= 0xffffffff for b in bits), 'invalid score bits')
    scores = [struct.unpack('<f', struct.pack('<I', b))[0] for b in bits]
    require(all(math.isfinite(s) for s in scores), 'nonfinite selected score')
    require(list(zip(scores, ids)) == sorted(zip(scores, ids)), 'score/ID order')
    return ids, bits


def validate(raw):
    require(raw['schema_version'] == 1 and raw['family'] == 'r6_certified_adc_dense' and
            raw['status'] == 'EXECUTED' and raw['quality_status'] == 'NOT_EXECUTED', 'result identity/scope')
    require(raw['configuration'] == CONFIG, 'frozen configuration changed')
    require(raw['source_identity'] == 'EXTERNAL_MANIFEST_REQUIRED' and
            raw['compiler']['id'] in ('GNU', 'Clang', 'MSVC') and bool(raw['compiler']['version']), 'compiler/source metadata')
    require(all(type(raw['dependencies']['nlohmann_json'][key]) is int and
                raw['dependencies']['nlohmann_json'][key] >= 0 for key in ('major', 'minor', 'patch')), 'dependency identity')
    require(raw['avx2_compiled'] is True and raw['flt_eval_method'] == 0, 'FP/SIMD eligibility')
    word_bytes = raw['sizeof_size_t']
    require(word_bytes in (4, 8), 'unsupported size_t accounting')
    for role, expected_bytes in (('codes', 96000000), ('thresholds', 4608), ('queries', 233472)):
        require(raw['inputs'][role]['bytes'] == expected_bytes and
                isinstance(raw['inputs'][role]['path'], str) and bool(raw['inputs'][role]['path']), 'input shape metadata')
    memory = raw['common_memory']
    require(memory['codes_capacity_bytes'] >= 96000000 and memory['thresholds_capacity_bytes'] >= 4608 and
            memory['queries_capacity_bytes'] >= 233472 and memory['reference_scores_capacity_bytes'] >= 32000000,
            'common retained vector payloads')
    require(raw['preflight'] == dict(status='PASS', score_comparisons=8557056,
            enclosure_checks=8557056, certified_cases=48, all_refined_score_bits='PASS',
            pair_avx2_bitwise_eligibility='PASS'), 'preflight incomplete')
    require((raw['trial_count'], raw['sample_count'], raw['warmup_executions']) == (240, 720, 144), 'execution counts')
    references, reference_evaluations = {}, {}
    for row in raw['reference_cases']:
        n, k, q = row['documents'], row['top_k'], row['query_index']
        key = n, k, q
        require(key not in references and n in COUNTS and k in TOPS and q in range(8), 'duplicate/foreign reference')
        references[key] = payload(row, n, k)
        require(k <= row['certified_exact_evaluations'] <= n, 'reference evaluation count')
        reference_evaluations[key] = row['certified_exact_evaluations']
    require(len(references) == 48, 'reference coverage')
    groups, trials = defaultdict(list), defaultdict(list)
    require(len(raw['raw_samples']) == 720, 'missing/extra raw samples')
    for row in raw['raw_samples']:
        n, k, q, rep, arm = (row[x] for x in ('documents', 'top_k', 'query_index', 'repeat', 'arm'))
        require((n, k, q) in references and type(rep) is int and 0 <= rep < 5 and arm in ARMS, 'foreign sample')
        trial = (((COUNTS.index(n) * 2 + TOPS.index(k)) * 8 + q) * 5 + rep)
        require(row['trial_id'] == trial and row['order_position'] in (0, 1, 2), 'trial identity/order')
        require(payload(row, n, k) == references[n, k, q], 'ordered ID/score-bit parity')
        require(math.isfinite(row['total_ms']) and row['total_ms'] > 0, 'invalid enclosing time')
        evaluations = row['exact_evaluations']
        require(type(evaluations) is int and k <= evaluations <= n, 'evaluation count')
        if arm != ARMS[2]:
            require(evaluations == n, 'exhaustive baseline is not exhaustive')
        else:
            require(evaluations == reference_evaluations[n, k, q], 'deterministic refinement count changed')
        near(row['refinement_fraction'], evaluations / n, 'fraction/count mismatch')
        require(row['coordinate_lut_bytes'] == 6144 and row['pair_lut_bytes'] == 12288, 'LUT accounting')
        require(row['global_u8_lut_bytes'] == (3072 if arm == ARMS[2] else 0) and
                row['global_u8_constructed_bytes'] == (9216 if arm == ARMS[2] else 0), 'global LUT accounting')
        minimum_vectors = n * (10 + 2 * word_bytes if arm == ARMS[2] else 4 + word_bytes)
        require(row['query_tables_allocated_bytes'] >= 135984 and row['vector_capacity_bytes'] >= minimum_vectors,
                'missing allocated-table/vector payload accounting')
        require(row['seed_count'] == (k if arm == ARMS[2] else 0) and
                row['nonseed_refinements'] == (evaluations - k if arm == ARMS[2] else 0),
                'seed/refinement accounting')
        groups[n, k, arm].append(row)
        trials[trial].append(row)
    engine = mt19937(20261120)
    permutations = list(itertools.permutations(ARMS))
    require(set(trials) == set(range(240)), 'trial coverage')
    for trial in range(240):
        rows = sorted(trials[trial], key=lambda r: r['order_position'])
        require(len(rows) == 3 and [r['order_position'] for r in rows] == [0, 1, 2], 'incomplete paired trial')
        require(tuple(r['arm'] for r in rows) == permutations[next(engine) % 6], 'randomized arm order changed')
    summaries = {}
    require(len(raw['summaries']) == 18, 'summary coverage')
    for row in raw['summaries']:
        key = row['documents'], row['top_k'], row['arm']
        require(key in groups and key not in summaries and len(groups[key]) == 40 and row['sample_count'] == 40, 'summary identity/count')
        values = [s['total_ms'] for s in groups[key]]
        expected = (quantile(values, 50), quantile(values, 95),
                    sum(s['exact_evaluations'] for s in groups[key]) / (40 * key[0]))
        for field, value in zip(('p50_ms', 'p95_ms', 'mean_refinement_fraction'), expected):
            near(row[field], value, 'summary differs from raw samples: ' + field)
        summaries[key] = expected
    decisions = {}
    for row in raw['profile_decisions']:
        n, k = row['documents'], row['top_k']
        require(n in COUNTS and k in TOPS and (n, k) not in decisions, 'profile decision identity')
        baseline = min(ARMS[:2], key=lambda arm: summaries[n, k, arm][0])
        cert, base = summaries[n, k, ARMS[2]], summaries[n, k, baseline]
        ratios = cert[0] / base[0], cert[1] / base[1]
        require(row['baseline_arm'] == baseline, 'not fastest eligible baseline')
        near(row['certified_p50_ratio'], ratios[0], 'median ratio')
        near(row['certified_p95_ratio'], ratios[1], 'p95 ratio')
        near(row['mean_refinement_fraction'], cert[2], 'profile fraction')
        exact_sum = sum(s['exact_evaluations'] for s in groups[n, k, ARMS[2]])
        expected = 'CONTINUE_RESEARCH' if cert[0] <= .9 * base[0] and cert[1] <= base[1] and exact_sum * 10 < 40 * n * 9 else 'KEEP_CURRENT'
        require(row['decision'] == expected, 'profile decision violates predeclared gate')
        decisions[n, k] = expected
    require(len(decisions) == 6, 'missing profile decisions')
    profiles = {}
    for row in raw['persistent_profiles']:
        n = row['documents']
        require(n in COUNTS and n not in profiles and row['ids_count'] == n, 'persistent profile identity')
        require(row['padded_documents'] == (n + 31) // 32 * 32 and
                row['prefix_code_capacity_bytes'] >= n * 96 and row['ids_capacity_bytes'] >= n * 4 and
                row['plane_major_capacity_bytes'] >= row['padded_documents'] * 96 and
                row['block32_capacity_bytes'] >= row['padded_documents'] * 96, 'persistent memory accounting')
        require(all(math.isfinite(row[f]) and row[f] >= 0 for f in ('prefix_copy_ms', 'ids_setup_ms', 'layout_build_ms')), 'invalid build time')
        profiles[n] = row
    require(len(profiles) == 3, 'persistent profile coverage')
    return decisions


def synthetic_fixture():
    """Validator-only data, never emitted as executed benchmark evidence."""
    raw = dict(schema_version=1, family='r6_certified_adc_dense', status='EXECUTED',
               quality_status='NOT_EXECUTED', configuration=copy.deepcopy(CONFIG),
               avx2_compiled=True, flt_eval_method=0, sizeof_size_t=8, trial_count=240, sample_count=720,
               warmup_executions=144, preflight=dict(status='PASS', score_comparisons=8557056,
               enclosure_checks=8557056, certified_cases=48, all_refined_score_bits='PASS',
               pair_avx2_bitwise_eligibility='PASS'))
    raw['inputs'] = {role: dict(path=role, bytes=size) for role, size in
                     (('codes', 96000000), ('thresholds', 4608), ('queries', 233472))}
    raw['common_memory'] = dict(codes_capacity_bytes=96000000, thresholds_capacity_bytes=4608,
                               queries_capacity_bytes=233472, reference_scores_capacity_bytes=32000000)
    raw.update(source_identity='EXTERNAL_MANIFEST_REQUIRED', compiler=dict(id='GNU', version='validator-fixture-only'),
               dependencies=dict(nlohmann_json=dict(major=3, minor=11, patch=3)))
    for field in ('reference_cases', 'raw_samples', 'summaries', 'profile_decisions', 'persistent_profiles'):
        raw[field] = []
    engine, trial = mt19937(20261120), 0
    orders = list(itertools.permutations(ARMS))
    totals = dict(zip(ARMS, (1.0, .8, 1.2)))
    for n in COUNTS:
        raw['persistent_profiles'].append(dict(documents=n, ids_count=n, padded_documents=n,
            prefix_code_capacity_bytes=n * 96, ids_capacity_bytes=n * 4,
            plane_major_capacity_bytes=n * 96, block32_capacity_bytes=n * 96,
            prefix_copy_ms=1, ids_setup_ms=1, layout_build_ms=1))
        for k in TOPS:
            result = dict(selected_ids=list(range(k)), selected_score_bits=[0x3f800000] * k)
            for q in range(8):
                raw['reference_cases'].append(dict(documents=n, top_k=k, query_index=q,
                    certified_exact_evaluations=k + 2, **result))
                for rep in range(5):
                    for position, arm in enumerate(orders[next(engine) % 6]):
                        evaluations = k + 2 if arm == ARMS[2] else n
                        raw['raw_samples'].append(dict(trial_id=trial, documents=n, top_k=k,
                            query_index=q, repeat=rep, arm=arm, order_position=position,
                            total_ms=totals[arm], exact_evaluations=evaluations,
                            refinement_fraction=evaluations / n, vector_capacity_bytes=n * 26,
                            coordinate_lut_bytes=6144, pair_lut_bytes=12288,
                            global_u8_lut_bytes=3072 if arm == ARMS[2] else 0,
                            global_u8_constructed_bytes=9216 if arm == ARMS[2] else 0,
                            query_tables_allocated_bytes=135984, seed_count=k if arm == ARMS[2] else 0,
                            nonseed_refinements=2 if arm == ARMS[2] else 0,
                            selected_ids=list(result['selected_ids']), selected_score_bits=list(result['selected_score_bits'])))
                    trial += 1
            for arm in ARMS:
                raw['summaries'].append(dict(documents=n, top_k=k, arm=arm, sample_count=40,
                    p50_ms=totals[arm], p95_ms=totals[arm],
                    mean_refinement_fraction=(k + 2) / n if arm == ARMS[2] else 1))
            raw['profile_decisions'].append(dict(documents=n, top_k=k, baseline_arm=ARMS[1],
                certified_p50_ratio=1.5, certified_p95_ratio=1.5,
                mean_refinement_fraction=(k + 2) / n, decision='KEEP_CURRENT'))
    return raw


def mutation_controls(raw):
    def changed_count(result):
        row = next(s for s in result['raw_samples'] if s['arm'] == ARMS[2])
        row['exact_evaluations'] = row['documents'] if row['exact_evaluations'] < row['documents'] else row['top_k']
        row['nonseed_refinements'] = row['exact_evaluations'] - row['top_k']
        row['refinement_fraction'] = row['exact_evaluations'] / row['documents']

    changes = [
        lambda r: r['raw_samples'][0]['selected_score_bits'].__setitem__(0, r['raw_samples'][0]['selected_score_bits'][0] ^ 1),
        lambda r: r['raw_samples'][0]['selected_ids'].__setitem__(0, -1),
        lambda r: r['raw_samples'].pop(),
        lambda r: r['raw_samples'][0].__setitem__('trial_id', 239),
        lambda r: r['raw_samples'][0].__setitem__('order_position', (r['raw_samples'][0]['order_position'] + 1) % 3),
        lambda r: r['raw_samples'][0].__setitem__('refinement_fraction', -1),
        lambda r: r['preflight'].__setitem__('enclosure_checks', 1),
        lambda r: r['configuration'].__setitem__('maximum_median_ratio', 2),
        lambda r: r['summaries'][0].__setitem__('p50_ms', -1),
        lambda r: r['profile_decisions'][0].__setitem__('baseline_arm', 'invalid'),
        lambda r: r['profile_decisions'][0].__setitem__('decision', 'PRODUCTION_READY'),
        lambda r: r['persistent_profiles'].pop(),
        changed_count,
        lambda r: r.pop('common_memory'),
        lambda r: r['inputs']['queries'].__setitem__('bytes', 4),
        lambda r: r['raw_samples'][0].__setitem__('vector_capacity_bytes', 1),
        lambda r: r.pop('compiler'),
    ]
    for change in changes:
        altered = copy.deepcopy(raw)
        change(altered)
        try:
            validate(altered)
        except (ValueError, KeyError):
            continue
        raise ValueError('mutation was accepted')
    return len(changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--mutation-self-test', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        engine = mt19937(5489)
        require([next(engine) for _ in range(3)] == [3499211612, 581869302, 3890346734], 'MT19937 golden')
        fixture = synthetic_fixture()
        validate(fixture)
        controls = mutation_controls(fixture)
        print(f'Certified ADC validator-only synthetic self-test: PASS; {controls} rejection controls; no benchmark')
        return
    require(args.raw is not None and args.manifest is not None, '--raw and --manifest required')
    raw = json.loads(args.raw.read_text(encoding='utf-8-sig'))
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    require(manifest['schema'] == 'r6_certified_adc_local_execution_v1' and
            manifest['execution_status'] == 'EXECUTED', 'manifest identity')
    require(hashlib.sha256(args.raw.read_bytes()).hexdigest() == manifest['raw_sha256'], 'raw/manifest digest')
    require(manifest['samples_are_independent_queries'] is False and manifest['independent_queries'] == 8 and
            manifest['applied_quality'] == 'NOT_EXECUTED' and manifest['production_activation'] is False, 'manifest scope')
    decisions = validate(raw)
    mutations = mutation_controls(raw) if args.mutation_self_test else 0
    print(json.dumps(dict(status='PASS', samples=720, profiles=6, mutation_controls=mutations,
                         decisions={f'{n}/{k}': value for (n, k), value in decisions.items()},
                         scope='raw-result replay; full source-data replay requires the frozen inputs'), indent=2))


if __name__ == '__main__':
    main()
