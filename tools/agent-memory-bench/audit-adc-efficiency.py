#!/usr/bin/env python3
"""Audit ADC efficiency raw mechanics; never a held-out quality oracle."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import runpy
import subprocess
from collections import defaultdict


LEGACY = runpy.run_path(str(Path(__file__).with_name('audit-certified-adc.py')))
COUNTS, TOPS = (4096, 65536, 1000000), (16, 128)
ARMS = ['pair_fp32_scalar', 'pair_fp32_avx2_block32', 'A0_original',
        'A1_simd_integer', 'A2_integer_cutoff', 'A3_trusted_ids',
        'A4_reusable_workspace', 'A5_simd_refinement', 'A6_dynamic_tau']
CONFIRMATION_ARMS = ARMS + ['A7_combined', 'A0_template_control']
require, quantile = LEGACY['require'], LEGACY['quantile']


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def audit_manifest(directory, manifest, verify_inputs=False, repository=None, extra_inputs=None):
    require(manifest['status'] == 'EXECUTED' and
            manifest['applied_quality'] == 'NOT_EXECUTED' and
            manifest['production_activation'] is False and
            manifest['independent_queries'] == 8, 'manifest status/scope')
    require(len(manifest['source_commit']) == 40 and len(manifest['source_tree']) == 40,
            'source identity')
    require(manifest['compile_and_link_commands'], 'compile provenance')
    files = {}
    for record in manifest['files']:
        relative = Path(record['path'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe file locator')
        path = directory / relative
        require(record['path'] not in files and path.is_file(), 'duplicate/missing retained file')
        require(path.stat().st_size == record['bytes'] and digest(path) == record['sha256'],
                'retained file binding: ' + record['path'])
        files[record['path']] = record
    require({'measured.exe', 'source.cpp', 'producer.ps1', 'raw.json', 'correctness.json'} <= files.keys(),
            'missing measured binary/source/producer/raw/controls')
    source = (directory / 'source.cpp').read_bytes()
    source_blob = hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest()
    if 'source_blob' in manifest:
        require(source_blob == manifest['source_blob'], 'retained source/blob mismatch')
    if repository is not None:
        source_path = manifest.get('source_path', 'tools/agent-memory-bench/thq_fastscan_v3_benchmark.cpp')
        actual = subprocess.check_output(['git', '-C', str(repository), 'rev-parse',
                                          manifest['source_commit'] + ':' + source_path], text=True).strip()
        require(source_blob == actual, 'source bytes do not belong to named commit')
        actual_tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse',
                                               manifest['source_commit'] + '^{tree}'], text=True).strip()
        require(actual_tree == manifest['source_tree'], 'source commit/tree binding')
    expected = {
        'codes': (96000000, '0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70'),
        'thresholds': (4608, 'ceaa7ed312525bfa81a96b9f53aa79fb7f210387eb190a752051165dbdbd37a4'),
        'queries': (233472, 'abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191')}
    expected.update(extra_inputs or {})
    require(len(manifest['inputs']) == len(expected), 'input role coverage')
    seen = set()
    for record in manifest['inputs']:
        role = record['role']
        require(role in expected and role not in seen and
                (record['bytes'], record['sha256']) == expected[role], 'input binding')
        seen.add(role)
        if verify_inputs:
            path = Path(record['path'])
            require(path.stat().st_size == record['bytes'] and digest(path) == record['sha256'],
                    'actual frozen input mismatch')


def payload(row, count, top):
    return LEGACY['payload'](row, count, top)


def counts(row, count, top, arm):
    exact, physical = row['exact_evaluations'], row['physical_exact_evaluations']
    require(type(exact) is int and top <= exact <= count and
            type(physical) is int and exact <= physical <= count + 31 + top,
            'logical/physical scoring work')
    require(type(row['vector_capacity_bytes']) is int and row['vector_capacity_bytes'] >= 0,
            'workspace accounting')
    if arm.startswith('pair_'):
        require(exact == count and physical == ((count + 31) // 32 * 32
                if arm == 'pair_fp32_avx2_block32' else count), 'nonexhaustive baseline')
    return exact


def validate(raw):
    require(raw['schema_version'] == 1 and raw['family'] == 'r6_certified_adc_efficiency' and
            raw['status'] == 'EXECUTED' and raw['quality_status'] == 'NOT_EXECUTED' and
            raw['source_identity'] == 'EXTERNAL_MANIFEST_REQUIRED', 'raw identity/scope')
    config = raw['configuration']
    arms = config['arms']
    require(arms in (ARMS, CONFIRMATION_ARMS), 'unregistered arms')
    require(config['document_counts'] == list(COUNTS) and config['top_k'] == list(TOPS) and
            config['query_indices'] == list(range(8)) and config['repeats'] == 5 and
            config['warmups'] == 1 and config['order_seed'] == 20261121 and
            config['order_policy'] == 'mt19937_raw_modulo_fisher_yates' and
            config['quantile'] == 'nearest_rank', 'frozen configuration')
    components = config['combined_components']
    require((not components and arms == ARMS) or
            (arms == CONFIRMATION_ARMS and components == ARMS[3:8]), 'combined registration')
    mask = sum(1 << ARMS[3:].index(component) for component in components)
    require(config['combined_mask'] == mask, 'combined mask/component disagreement')
    require(raw['decision_status'] == 'INDEPENDENT_AUDIT_REQUIRED', 'self-certified conclusion')
    require(raw['avx2_compiled'] is True and raw['flt_eval_method'] == 0 and
            raw['compiler']['id'] in ('GNU', 'Clang', 'MSVC') and raw['compiler']['version'],
            'compiler/SIMD/FP eligibility')
    require(raw['preflight'] == dict(status='PASS', score_comparisons=8557056,
            integer_score_comparisons=8557056, enclosure_checks=8557056,
            arm_cases=48 * len(arms), all_refined_score_bits='PASS', pair_avx2_bitwise_eligibility='PASS'),
            'preflight coverage')
    word_bytes = raw['sizeof_size_t']
    require(word_bytes in (4, 8), 'unsupported size_t accounting')
    require(set(raw['inputs']) == {'codes', 'thresholds', 'queries'}, 'raw input role coverage')
    for role, size in (('codes', 96000000), ('thresholds', 4608), ('queries', 233472)):
        require(raw['inputs'][role]['bytes'] == size and isinstance(raw['inputs'][role]['path'], str) and
                raw['inputs'][role]['path'], 'raw input path/shape')
    references, expected_counts, expected_work = {}, {}, {}
    for row in raw['reference_cases']:
        key = row['documents'], row['top_k'], row['query_index']
        n, k, q = key
        require(n in COUNTS and k in TOPS and type(q) is int and 0 <= q < 8 and key not in references,
                'reference identity')
        references[key] = payload(row, n, k)
        expected_counts[key] = {}
        expected_work[key] = {}
        for result in row['arms']:
            arm = result['arm']
            require(arm in arms and arm not in expected_counts[key], 'reference arm identity')
            counts(result, n, k, arm)
            expected_counts[key][arm] = result['exact_evaluations']
            expected_work[key][arm] = (result['physical_exact_evaluations'], result['vector_capacity_bytes'])
        require(set(expected_counts[key]) == set(arms), 'reference arm coverage')
        original = expected_counts[key]['A0_original']
        require(all(expected_counts[key][arm] == original for arm in ARMS[3:8]) and
                expected_counts[key]['A6_dynamic_tau'] <= original, 'isolated algorithm/work drift')
        if arms == CONFIRMATION_ARMS:
            require(expected_counts[key]['A7_combined'] == original and
                    expected_counts[key]['A0_template_control'] == original,
                    'combined/template-control work drift')
    require(len(references) == 48, 'reference coverage')
    groups, trials = defaultdict(list), defaultdict(list)
    seen = set()
    for row in raw['raw_samples']:
        key = row['documents'], row['top_k'], row['query_index']
        arm, repeat = row['arm'], row['repeat']
        require(key in references and arm in arms and type(repeat) is int and 0 <= repeat < 5,
                'sample identity')
        identity = key + (repeat, arm)
        require(identity not in seen, 'duplicate sample')
        seen.add(identity)
        n, k, q = key
        trial = (((COUNTS.index(n) * 2 + TOPS.index(k)) * 8 + q) * 5 + repeat)
        require(row['trial_id'] == trial and type(row['order_position']) is int and
                0 <= row['order_position'] < len(arms), 'trial identity')
        require(payload(row, n, k) == references[key], 'ordered IDs/FP32 score-bit parity')
        exact = counts(row, n, k, arm)
        require(exact == expected_counts[key][arm], 'refinement count drift')
        require((row['physical_exact_evaluations'], row['vector_capacity_bytes']) == expected_work[key][arm],
                'physical work/workspace reference drift')
        LEGACY['near'](row['refinement_fraction'], exact / n, 'sample refinement fraction')
        seeds = 0 if arm.startswith('pair_') else k
        require(row['seed_count'] == seeds and
                row['nonseed_refinements'] == (0 if seeds == 0 else exact - seeds), 'seed/refinement work')
        minimum_vectors = {
            'pair_fp32_scalar': n * (4 + word_bytes),
            'pair_fp32_avx2_block32': n * (4 + word_bytes),
            'A0_original': n * (10 + 2 * word_bytes),
            'A1_simd_integer': n * (10 + 2 * word_bytes),
            'A2_integer_cutoff': n * (10 + 2 * word_bytes),
            'A3_trusted_ids': n * (6 + 2 * word_bytes),
            'A4_reusable_workspace': n * 10 + k * 2 * word_bytes,
            'A5_simd_refinement': n * (10 + 2 * word_bytes),
            'A6_dynamic_tau': n * (10 + 2 * word_bytes) + k * word_bytes,
            'A7_combined': n * 6 + k * 2 * word_bytes,
            'A0_template_control': n * (10 + 2 * word_bytes)}
        require(row['vector_capacity_bytes'] >= minimum_vectors[arm], 'underreported vector payload')
        require(isinstance(row['total_ms'], (int, float)) and not isinstance(row['total_ms'], bool) and
                math.isfinite(row['total_ms']) and row['total_ms'] > 0, 'invalid timing')
        require(type(row['workspace_reused']) is bool and
                row['workspace_reused'] == (arm in ('A4_reusable_workspace', 'A7_combined')),
                'workspace timing contract')
        groups[n, k, arm].append(row)
        trials[trial].append(row)
    require(len(seen) == 240 * len(arms) and len(trials) == 240, 'sample/trial coverage')
    require(raw['trial_count'] == 240 and raw['sample_count'] == len(seen) and
            raw['warmup_executions'] == 48 * len(arms), 'reported execution counts')
    engine = LEGACY['mt19937'](20261121)
    for trial in range(240):
        order = list(arms)
        for end in range(len(order) - 1, 0, -1):
            index = next(engine) % (end + 1)
            order[end], order[index] = order[index], order[end]
        rows = sorted(trials[trial], key=lambda row: row['order_position'])
        require([row['order_position'] for row in rows] == list(range(len(arms))) and
                [row['arm'] for row in rows] == order, 'randomized matched order')
    summary = {}
    for key, rows in groups.items():
        require(len(rows) == 40, 'group coverage')
        values = [row['total_ms'] for row in rows]
        summary[key] = dict(p50_ms=quantile(values, 50), p95_ms=quantile(values, 95),
                            vector_capacity_bytes=max(row['vector_capacity_bytes'] for row in rows),
                            near_exhaustive=10 * sum(row['exact_evaluations'] for row in rows) >= 9 * 40 * key[0],
                            mean_refinement_fraction=sum(row['exact_evaluations'] for row in rows) / (40 * key[0]))
    rows_seen = set()
    for row in raw['summaries']:
        key = row['documents'], row['top_k'], row['arm']
        require(key in summary and key not in rows_seen and row['sample_count'] == 40, 'summary identity')
        rows_seen.add(key)
        for field in ('p50_ms', 'p95_ms', 'mean_refinement_fraction'):
            LEGACY['near'](row[field], summary[key][field], 'raw/summary ' + field)
    require(len(rows_seen) == len(summary), 'summary coverage')
    validate_selectivity(raw['selectivity'], expected_counts)
    validate_profiles(raw['persistent_profiles'], arms)
    return summary


def validate_selectivity(rows, expected_counts):
    seen = set()
    for row in rows:
        key = row['documents'], row['top_k'], row['query_index']
        require(key in expected_counts and key not in seen, 'selectivity identity')
        seen.add(key)
        n, k, _ = key
        fixed, dynamic = expected_counts[key]['A0_original'], expected_counts[key]['A6_dynamic_tau']
        require(row['fixed_exact_evaluations'] == fixed and row['dynamic_exact_evaluations'] == dynamic and
                row['fixed_tau_excess_evaluations'] == fixed - dynamic and row['pruned_documents'] == n - fixed,
                'selectivity/work counts')
        require(type(row['ideal_final_tau_screened']) is int and k <= row['ideal_final_tau_screened'] <= fixed,
                'ideal incumbent screening count')
        overlap = row['integer_top_k_overlap']
        require(isinstance(overlap, (int, float)) and math.isfinite(overlap) and 0 <= overlap <= 1,
                'integer ordering overlap')
        widths = [row[field] for field in ('bound_width_p50', 'bound_width_p95', 'bound_width_max')]
        require(all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in widths) and
                widths == sorted(widths), 'bound width distribution')
    require(len(seen) == 48, 'selectivity coverage')


def validate_profiles(profiles, arms):
    seen = set()
    for row in profiles:
        n = row['documents']
        require(n in COUNTS and n not in seen and row['padded_documents'] == (n + 31) // 32 * 32,
                'persistent profile identity')
        seen.add(n)
        require(row['prefix_code_capacity_bytes'] >= 96 * n and row['ids_capacity_bytes'] >= 4 * n and
                row['plane_major_capacity_bytes'] >= row['padded_documents'] * 96 and
                row['block32_capacity_bytes'] >= row['padded_documents'] * 96, 'persistent layout payload')
        require(all(math.isfinite(row[field]) and row[field] >= 0 for field in
                    ('prefix_copy_ms', 'trusted_id_build_ms', 'layout_build_ms')), 'persistent build time')
        cold = set()
        for query in row['cold_workspace_queries']:
            identity = query['arm'], query['top_k']
            require(identity[0] in arms and identity[0] in ('A4_reusable_workspace', 'A7_combined') and
                    identity[1] in TOPS and identity not in cold and query['query_index'] == 0,
                    'cold-workspace identity')
            require(math.isfinite(query['full_first_use_query_ms']) and query['full_first_use_query_ms'] > 0 and
                    query['vector_capacity_bytes'] >= n * 6, 'cold query memory/time')
            cold.add(identity)
        expected = {(arm, k) for arm in arms if arm in ('A4_reusable_workspace', 'A7_combined') for k in TOPS}
        require(cold == expected, 'cold workspace coverage')
    require(len(seen) == 3, 'persistent profile coverage')


def validate_diagnostic(diagnostic, raw):
    require(diagnostic['family'] == 'r6_certified_adc_efficiency_diagnostic' and
            diagnostic['status'] == 'EXECUTED' and diagnostic['quality_status'] == 'NOT_EXECUTED' and
            diagnostic['configuration'] == raw['configuration'] and diagnostic['preflight'] == raw['preflight'] and
            diagnostic['reference_cases'] == raw['reference_cases'], 'diagnostic source/config/reference parity')
    require(diagnostic['raw_samples'] == [] and diagnostic['sample_count'] == 0 and
            diagnostic['trial_count'] == 0 and diagnostic['warmup_executions'] == 0,
            'instrumented work leaked into full-query samples')
    require(diagnostic['selectivity'] == raw['selectivity'], 'diagnostic selectivity parity')
    validate_profiles(diagnostic['persistent_profiles'], raw['configuration']['arms'])
    references = {(row['documents'], row['top_k'], row['query_index']): row for row in raw['reference_cases']}
    stage_names = {'id_validation_copy_sort', 'query_lut', 'error_envelope', 'integer_scan',
                   'initial_selection', 'incumbent_scoring', 'bound_checks', 'exact_refinement',
                   'final_selection', 'buffer_allocation'}
    seen = set()
    for row in diagnostic['diagnostic_rows']:
        key = row['documents'], row['top_k'], row['query_index']
        arm = row['arm']
        require(key in references and arm in raw['configuration']['arms'][2:] and (key, arm) not in seen,
                'diagnostic row identity')
        seen.add((key, arm))
        n, k, _ = key
        require(payload(row, n, k) == payload(references[key], n, k), 'diagnostic scoring parity')
        expected = next(value for value in references[key]['arms'] if value['arm'] == arm)
        require(type(row['workspace_reused']) is bool and
                row['workspace_reused'] == (arm in ('A4_reusable_workspace', 'A7_combined')),
                'diagnostic/full workspace contract')
        require(all(row[field] == expected[field] for field in
                    ('exact_evaluations', 'physical_exact_evaluations', 'vector_capacity_bytes')),
                'diagnostic work/memory parity')
        stages = row['stages_ms']
        require(set(stages) == stage_names and all(isinstance(value, (int, float)) and
                math.isfinite(value) and value >= 0 for value in stages.values()), 'diagnostic stage times')
        total = row['instrumented_total_ms']
        require(math.isfinite(total) and total > 0 and sum(stages.values()) <= total * 1.001 + 1e-4,
                'diagnostic enclosing time')
    require(len(seen) == 48 * (len(raw['configuration']['arms']) - 2), 'diagnostic coverage')


def decisions(summary):
    result = []
    for n in COUNTS:
        for k in TOPS:
            base = min(ARMS[:2], key=lambda arm: summary[n, k, arm]['p50_ms'])
            control = summary[n, k, base]
            for arm in sorted(key[2] for key in summary if key[:2] == (n, k) and key[2].startswith('A')):
                value = summary[n, k, arm]
                passed = (value['p50_ms'] <= .9 * control['p50_ms'] and
                          value['p95_ms'] <= control['p95_ms'] and
                          value['vector_capacity_bytes'] <= control['vector_capacity_bytes'] and
                          not value['near_exhaustive'])
                result.append(dict(documents=n, top_k=k, arm=arm, baseline=base,
                                   p50_ratio=value['p50_ms'] / control['p50_ms'],
                                   p95_ratio=value['p95_ms'] / control['p95_ms'],
                                   memory_payload_gate=value['vector_capacity_bytes'] <= control['vector_capacity_bytes'],
                                   near_exhaustive=value['near_exhaustive'],
                                   full_path_gate='PASS' if passed else 'KEEP_CURRENT'))
    return result


def mutation_controls(raw):
    changes = [
        lambda r: r['raw_samples'][0]['selected_score_bits'].__setitem__(0, r['raw_samples'][0]['selected_score_bits'][0] ^ 1),
        lambda r: r['raw_samples'][0]['selected_ids'].__setitem__(0, -1),
        lambda r: r['raw_samples'].pop(),
        lambda r: r['raw_samples'].append(copy.deepcopy(r['raw_samples'][0])),
        lambda r: r['raw_samples'][0].__setitem__('total_ms', float('nan')),
        lambda r: r['raw_samples'][0].__setitem__('trial_id', -1),
        lambda r: r['raw_samples'][0].__setitem__('order_position', -1),
        lambda r: r['raw_samples'][0].__setitem__('exact_evaluations', 0),
        lambda r: r['raw_samples'][0].__setitem__('physical_exact_evaluations', 0),
        lambda r: r['raw_samples'][0].__setitem__('vector_capacity_bytes', 1),
        lambda r: r['configuration'].__setitem__('repeats', 1),
        lambda r: r['configuration'].__setitem__('combined_components', ['unregistered']),
        lambda r: r['reference_cases'].pop(),
        lambda r: r['summaries'][0].__setitem__('p95_ms', -1),
        lambda r: r.__setitem__('quality_status', 'PASS'),
        lambda r: r.__setitem__('decision_status', 'PRODUCTION_READY')]
    changes.extend([
        lambda r: r['preflight'].__setitem__('integer_score_comparisons', 1),
        lambda r: r.__setitem__('avx2_compiled', False),
        lambda r: r.__setitem__('warmup_executions', 0),
        lambda r: r['selectivity'][0].__setitem__('fixed_tau_excess_evaluations', -1),
        lambda r: r['selectivity'][0].__setitem__('bound_width_max', -1),
        lambda r: r['selectivity'].pop(),
        lambda r: r['persistent_profiles'].pop(),
        lambda r: r['persistent_profiles'][0]['cold_workspace_queries'].pop(),
        lambda r: r['inputs']['codes'].__setitem__('bytes', 1),
        lambda r: r['inputs']['queries'].__setitem__('path', ''),
        lambda r: r['configuration'].__setitem__('combined_mask', 1),
        lambda r: next(row for row in r['raw_samples'] if row['arm'] == 'A0_original').__setitem__(
            'physical_exact_evaluations',
            next(row for row in r['raw_samples'] if row['arm'] == 'A0_original')['physical_exact_evaluations'] + 1),
        lambda r: r['raw_samples'][0].__setitem__('vector_capacity_bytes',
            r['raw_samples'][0]['vector_capacity_bytes'] + 1000000),
        lambda r: r['raw_samples'][0].__setitem__('refinement_fraction', -1),
        lambda r: r['raw_samples'][0].__setitem__('nonseed_refinements', -1)])
    if raw['configuration']['arms'] == CONFIRMATION_ARMS:
        changes.extend([
            lambda r: r['configuration']['arms'].pop(),
            lambda r: r['configuration']['combined_components'].pop(),
            lambda r: r['reference_cases'][0]['arms'][-1].__setitem__('exact_evaluations', 0)])
    for change in changes:
        altered = copy.deepcopy(raw)
        change(altered)
        try:
            validate(altered)
        except (ValueError, KeyError):
            continue
        raise ValueError('mutation accepted')
    return len(changes)


def synthetic_fixture(combined=False):
    """Validator-only fixture; it is not an executed performance measurement."""
    arms = CONFIRMATION_ARMS if combined else ARMS
    raw = dict(schema_version=1, family='r6_certified_adc_efficiency', status='EXECUTED',
               quality_status='NOT_EXECUTED', source_identity='EXTERNAL_MANIFEST_REQUIRED',
               decision_status='INDEPENDENT_AUDIT_REQUIRED', sizeof_size_t=8,
               avx2_compiled=True, flt_eval_method=0, compiler=dict(id='GNU', version='VALIDATOR_ONLY'),
               preflight=dict(status='PASS', score_comparisons=8557056, integer_score_comparisons=8557056,
                              enclosure_checks=8557056, arm_cases=48 * len(arms), all_refined_score_bits='PASS',
                              pair_avx2_bitwise_eligibility='PASS'),
               trial_count=240, sample_count=240 * len(arms), warmup_executions=48 * len(arms),
               configuration=dict(document_counts=list(COUNTS), top_k=list(TOPS), query_indices=list(range(8)),
                                  repeats=5, warmups=1, order_seed=20261121, arms=list(arms),
                                  order_policy='mt19937_raw_modulo_fisher_yates', quantile='nearest_rank',
                                  combined_components=ARMS[3:8] if combined else [], combined_mask=31 if combined else 0),
               reference_cases=[], raw_samples=[], summaries=[], selectivity=[], persistent_profiles=[])
    engine, trial = LEGACY['mt19937'](20261121), 0
    raw['inputs'] = {role: dict(path=role, bytes=size) for role, size in
                     (('codes', 96000000), ('thresholds', 4608), ('queries', 233472))}
    for n in COUNTS:
        raw['persistent_profiles'].append(dict(documents=n, padded_documents=n, prefix_code_capacity_bytes=n * 96,
            ids_capacity_bytes=n * 4, plane_major_capacity_bytes=n * 96, block32_capacity_bytes=n * 96,
            prefix_copy_ms=1, trusted_id_build_ms=1, layout_build_ms=1,
            cold_workspace_queries=[dict(arm=arm, top_k=k, query_index=0,
                full_first_use_query_ms=1, vector_capacity_bytes=n * 27)
                for arm in arms if arm in ('A4_reusable_workspace', 'A7_combined') for k in TOPS]))
        for k in TOPS:
            values = dict(selected_ids=list(range(k)), selected_score_bits=[0x3f800000] * k)
            for q in range(8):
                stats = {arm: dict(arm=arm, exact_evaluations=n if arm.startswith('pair_') else k + 2,
                                   physical_exact_evaluations=n if arm.startswith('pair_') else k + 2,
                                   vector_capacity_bytes=n * 27) for arm in arms}
                raw['reference_cases'].append(dict(documents=n, top_k=k, query_index=q,
                                                   arms=list(stats.values()), **values))
                raw['selectivity'].append(dict(documents=n, top_k=k, query_index=q, fixed_exact_evaluations=k + 2,
                    dynamic_exact_evaluations=k + 2, fixed_tau_excess_evaluations=0, pruned_documents=n - k - 2,
                    ideal_final_tau_screened=k, integer_top_k_overlap=1, bound_width_p50=1,
                    bound_width_p95=1, bound_width_max=1))
                for rep in range(5):
                    order = list(arms)
                    for end in range(len(order) - 1, 0, -1):
                        index = next(engine) % (end + 1)
                        order[end], order[index] = order[index], order[end]
                    for position, arm in enumerate(order):
                        raw['raw_samples'].append(dict(documents=n, top_k=k, query_index=q, repeat=rep,
                            order_position=position, trial_id=trial, total_ms=1 + arms.index(arm),
                            refinement_fraction=stats[arm]['exact_evaluations'] / n,
                            seed_count=0 if arm.startswith('pair_') else k,
                            nonseed_refinements=0 if arm.startswith('pair_') else 2,
                            workspace_reused=arm in ('A4_reusable_workspace', 'A7_combined'), **stats[arm], **values))
                    trial += 1
            for index, arm in enumerate(arms):
                raw['summaries'].append(dict(documents=n, top_k=k, arm=arm, sample_count=40,
                    p50_ms=1 + index, p95_ms=1 + index,
                    mean_refinement_fraction=1 if arm.startswith('pair_') else (k + 2) / n))
    return raw


def synthetic_diagnostic(raw):
    diagnostic = copy.deepcopy(raw)
    diagnostic.update(family='r6_certified_adc_efficiency_diagnostic', raw_samples=[], sample_count=0,
                      trial_count=0, warmup_executions=0, diagnostic_rows=[])
    for reference in raw['reference_cases']:
        for arm in reference['arms'][2:]:
            diagnostic['diagnostic_rows'].append(dict(documents=reference['documents'], top_k=reference['top_k'],
                query_index=reference['query_index'], selected_ids=reference['selected_ids'],
                selected_score_bits=reference['selected_score_bits'], instrumented_total_ms=11,
                workspace_reused=arm['arm'] in ('A4_reusable_workspace', 'A7_combined'),
                stages_ms={name: 1 for name in ('id_validation_copy_sort', 'query_lut', 'error_envelope',
                    'integer_scan', 'initial_selection', 'incumbent_scoring', 'bound_checks', 'exact_refinement',
                    'final_selection', 'buffer_allocation')}, **arm))
    return diagnostic


def diagnostic_mutations(diagnostic, raw):
    changes = [lambda r: r['diagnostic_rows'].pop(),
        lambda r: r['diagnostic_rows'][0]['stages_ms'].__setitem__('bound_checks', -1),
        lambda r: r['diagnostic_rows'][0]['selected_score_bits'].__setitem__(0, 0),
        lambda r: r['diagnostic_rows'][0].__setitem__('exact_evaluations', 0),
        lambda r: r.__setitem__('sample_count', 1)]
    for change in changes:
        altered = copy.deepcopy(diagnostic)
        change(altered)
        try:
            validate_diagnostic(altered, raw)
        except (ValueError, KeyError):
            continue
        raise ValueError('diagnostic mutation accepted')
    return len(changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--verify-inputs', action='store_true')
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--expected-source')
    parser.add_argument('--legacy-confirm', action='store_true')
    parser.add_argument('--mutation-self-test', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        controls = 0
        for combined in (False, True):
            raw = synthetic_fixture(combined)
            validate(raw)
            controls += mutation_controls(raw)
            diagnostic = synthetic_diagnostic(raw)
            validate_diagnostic(diagnostic, raw)
            controls += diagnostic_mutations(diagnostic, raw)
        print(f'ADC efficiency validator-only self-test: PASS; {controls} rejection controls; no benchmark')
        return
    require(args.directory is not None, '--directory required')
    manifest = read_json(args.directory / 'manifest.json')
    if args.expected_source:
        require(manifest['source_commit'] == args.expected_source, 'unexpected measured source')
    audit_manifest(args.directory, manifest, args.verify_inputs, args.repository)
    raw = read_json(args.directory / 'raw.json')
    for record in manifest['inputs']:
        require(raw['inputs'][record['role']] == dict(path=record['path'], bytes=record['bytes']),
                'raw/manifest input binding')
    if args.legacy_confirm:
        result = LEGACY['validate'](raw)
        mutations = LEGACY['mutation_controls'](raw) if args.mutation_self_test else 0
        report = dict(status='PASS', samples=len(raw['raw_samples']), mutations=mutations,
                      profile_decisions={f'{n}/{k}': value for (n, k), value in result.items()})
    else:
        result = validate(raw)
        mutations = mutation_controls(raw) if args.mutation_self_test else 0
        diagnostic = read_json(args.directory / 'diagnostic.json')
        validate_diagnostic(diagnostic, raw)
        if args.mutation_self_test:
            mutations += diagnostic_mutations(diagnostic, raw)
        report = dict(status='PASS', samples=len(raw['raw_samples']), mutations=mutations,
                      decisions=decisions(result))
    report['scope'] = 'Source-bound raw-result integrity; native preflight supplies scoring oracle; no applied quality'
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
