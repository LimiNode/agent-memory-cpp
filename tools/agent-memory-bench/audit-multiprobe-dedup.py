#!/usr/bin/env python3
"""Audit source-bound synthetic MultiProbe mechanics, not retrieval quality."""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import runpy
import struct
import subprocess
import xml.etree.ElementTree as ET


ARMS = ['production', 'dense_copy', 'sparse_set', 'compact_set']
WORKLOADS = ['sparse_uniform', 'broad_uniform', 'projected_skew']
CONFIG = dict(widths=[128, 256], counts=[4096, 262144], workloads=WORKLOADS,
    filters=[False, True], queries=8, limit=10, table_count=8,
    candidate_multiplier=64, minimum_candidate_count=128,
    corpus_seed=20261122, query_seed=20261123, order_seed=20261124,
    rng_reset='per_width_per_profile', skew_unmodified_ordinal_mod8=7,
    warmups=1, repeats=5, arms=ARMS, quantile='nearest_rank',
    order_policy='mt19937_raw_modulo_fisher_yates')
SOURCE_PATHS = {
    'CMakeLists.txt',
    'cmake/AgentMemoryOptions.cmake',
    '.github/workflows/ci.yml',
    'tools/agent-memory-bench/CMakeLists.txt',
    'src/agent_memory/index/MultiProbeHammingIndex.cpp',
    'src/agent_memory/index/MultiProbeHammingIndex.hpp',
    'tools/agent-memory-bench/multiprobe_dedup.cpp',
    'tools/agent-memory-bench/MultiProbeResearchSupport.hpp',
    'tools/agent-memory-bench/generate-multiprobe-dedup.py',
    'tools/agent-memory-bench/audit-multiprobe-dedup.py',
    'tools/agent-memory-bench/run-multiprobe-dedup.ps1'}


def require(value, message):
    if not value:
        raise ValueError(message)


def unsigned(value, message):
    require(type(value) is int and value >= 0, message)
    return value


def positive_time(value, message):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0, message)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def compiler_binding(manifest, raw):
    require(raw['compiler']['id'] == manifest['compiler'] and
            raw['compiler']['version'] == manifest['compiler_version'], 'raw/manifest compiler binding')


def retained_record(record, size, sha256):
    name = record['path']
    relative = Path(name)
    require(type(name) is str and not relative.is_absolute() and ':' not in name and
            '\\' not in name and '..' not in relative.parts and relative.as_posix() == name,
            'unsafe retained path')
    require(type(record['bytes']) is int and record['bytes'] == size and
            re.fullmatch(r'[0-9a-f]{64}', record['sha256']) and record['sha256'] == sha256,
            'retained file size/digest: ' + name)


def mt19937(seed, wide=False):
    """Specified engine bits; no implementation-defined distribution adapter."""
    bits, n, m, r = (64, 312, 156, 31) if wide else (32, 624, 397, 31)
    multiplier = 6364136223846793005 if wide else 1812433253
    a = 0xB5026F5AA96619E9 if wide else 0x9908B0DF
    mask, lower = (1 << bits) - 1, (1 << r) - 1
    upper = mask ^ lower
    state = [seed]
    for index in range(1, n):
        previous = state[-1]
        state.append((multiplier * (previous ^ (previous >> (bits - 2))) + index) & mask)
    while True:
        for index in range(n):
            joined = (state[index] & upper) | (state[(index + 1) % n] & lower)
            state[index] = state[(index + m) % n] ^ (joined >> 1) ^ (a if joined & 1 else 0)
        for value in state:
            if wide:
                value ^= (value >> 29) & 0x5555555555555555
                value ^= (value << 17) & 0x71D67FFFEDA60000
                value ^= (value << 37) & 0xFFF7EEE000000000
                value ^= value >> 43
            else:
                value ^= value >> 11
                value ^= (value << 7) & 0x9D2C5680
                value ^= (value << 15) & 0xEFC60000
                value ^= value >> 18
            yield value & mask


def ordered_arms(engine):
    arms = list(ARMS)
    for end in range(3, 0, -1):
        position = next(engine) % (end + 1)
        arms[end], arms[position] = arms[position], arms[end]
    return arms


def profiles():
    result = []
    for width in CONFIG['widths']:
        for count in CONFIG['counts']:
            for workload in WORKLOADS:
                for filtered in (False, True):
                    result.append(dict(id=len(result), width=width, count=count, workload=workload,
                        filtered=filtered, bits_per_table=4 if workload == 'broad_uniform' else 12,
                        max_probe_radius=0 if workload == 'sparse_uniform' else 1))
    return result


def evidence(row, profile):
    count = profile['count']
    candidates = unsigned(row['candidates'], 'invalid accepted count')
    buckets = unsigned(row['buckets'], 'invalid bucket count')
    postings = unsigned(row['postings'], 'invalid posting count')
    require(candidates <= count and postings <= 8 * count and candidates <= postings,
            'impossible exposed search work')
    possible_buckets = {8}
    if profile['max_probe_radius']:
        possible_buckets.add(8 * (1 + profile['bits_per_table']))
    require(buckets in possible_buckets, 'incomplete radius/table probe boundary')
    results = row['results']
    require(isinstance(results, list) and len(results) == min(10, candidates), 'result count/candidates')
    previous, seen = None, set()
    for hit in results:
        identifier, distance = hit['id'], hit['distance']
        require(type(identifier) is str and re.fullmatch(r'mp:[0-9]{8}', identifier), 'result ID framing')
        position = int(identifier[3:])
        require(position < count and identifier not in seen, 'foreign/duplicate result ID')
        seen.add(identifier)
        unsigned(distance, 'invalid Hamming distance')
        require(distance <= profile['width'], 'distance exceeds signature width')
        require(hit['metadata'] == {'cohort': str(position % 16)}, 'result metadata/ID mismatch')
        require(not profile['filtered'] or position % 16 == 0, 'metadata filter escaped')
        key = distance, identifier
        require(previous is None or previous < key, 'distance/ChunkId order')
        previous = key
    return row


def trace(row, expected, profile, arm):
    if arm == 'production':
        require(row is None, 'production API does not expose a private trace')
        return None
    require(isinstance(row, dict) and re.fullmatch(r'[0-9a-f]{16}', row['fnv1a64']), 'trace identity')
    for field in ('first_seen', 'accepted', 'buckets', 'postings', 'distance_evaluations',
                  'completed_radii', 'visited_capacity', 'visited_payload_lower_bound'):
        unsigned(row[field], 'invalid trace count/' + field)
    require(row['accepted'] == row['distance_evaluations'] == expected['candidates'] and
            row['buckets'] == expected['buckets'] and row['postings'] == expected['postings'],
            'private trace/exposed production counters')
    seen, capacity = row['first_seen'], row['visited_capacity']
    require(row['accepted'] <= seen <= min(profile['count'], row['postings']), 'pre/post-filter first visits')
    if not profile['filtered']:
        require(seen == row['accepted'], 'unfiltered first-visit drift')
    radii = 1 if row['buckets'] == 8 else 2
    require(row['completed_radii'] == radii, 'trace probe-stop boundary')
    if arm == 'dense_copy':
        require(capacity >= profile['count'], 'dense visited allocation omitted')
        minimum = capacity
    elif arm == 'sparse_set':
        require(capacity >= max(1, seen), 'sparse bucket capacity')
        minimum = seen * 8 + capacity * 8
    else:
        require((seen == 0 and capacity == 0) or
                (capacity >= max(16, 2 * seen) and capacity & (capacity - 1) == 0),
                'compact capacity/load/power-of-two invariant')
        minimum = capacity * 8
    require(row['visited_payload_lower_bound'] == minimum, 'independent visited payload minimum')
    return tuple(row[field] for field in ('fnv1a64', 'first_seen', 'accepted', 'buckets',
                 'postings', 'distance_evaluations', 'completed_radii'))


def allocations(row, profile, expected, minimum):
    fields = ('ordinary_allocations', 'ordinary_requested_bytes',
              'ordinary_peak_live_bytes', 'ordinary_retained_bytes')
    for field in fields:
        unsigned(row[field], 'invalid ordinary allocation diagnostic')
    require(row[fields[0]] > 0 and row[fields[1]] >= row[fields[2]] >= row[fields[3]],
            'requested/peak/retained allocation identity')
    # Seen, accepted positions and full-distance positions coexist during scoring.
    candidate_slots = max(min(profile['count'], 640), expected['candidates'])
    minimum += candidate_slots * 8 + expected['candidates'] * 16
    require(row['ordinary_peak_live_bytes'] >= minimum, 'full search live allocation minimum')


def validate(timing, diagnostic):
    expected_profiles = profiles()
    reference = {}
    for raw, mode in ((timing, 'TIMING'), (diagnostic, 'DIAGNOSTIC')):
        require(raw['schema_version'] == 1 and raw['family'] == 'r6_multiprobe_dedup' and
                raw['status'] == 'EXECUTED' and raw['mode'] == mode, 'raw identity/mode')
        require(raw['configuration'] == CONFIG and raw['sizeof_size_t'] == 8, 'frozen matrix/x64 ABI')
        require(raw['compiler']['id'] in ('GNU', 'MSVC', 'Clang') and raw['compiler']['version'],
                'compiler identity')
        require(raw['quality'] == 'NOT_EXECUTED' and raw['production_activation'] is False and
                raw['source_identity'] == 'EXTERNAL_MANIFEST_REQUIRED' and
                raw['peak_rss'] == 'NOT_MEASURED', 'research evidence scope')
        require(raw['allocation_scope'] == ('ordinary_new_payload_only' if mode == 'DIAGNOSTIC'
                                            else 'NOT_MEASURED'), 'allocation instrumentation boundary')
        require(len(raw['profiles']) == 24, 'profile coverage')
        local_reference = {}
        for row, expected_profile in zip(raw['profiles'], expected_profiles):
            require(all(row[key] == value for key, value in expected_profile.items()), 'profile ordering/parameters')
            require(row['fixture_file'] == f"fixtures/profile-{row['id']}.bin", 'fixture locator')
            require(set(row['build_ms']) == set(ARMS), 'build arm coverage')
            for value in row['build_ms'].values():
                positive_time(value, 'invalid separate index build time')
            require(set(row['first_search_ms']) == (set(ARMS) if mode == 'TIMING' else set()),
                    'first-search scope/arm coverage')
            for value in row['first_search_ms'].values():
                positive_time(value, 'invalid first-search time')
            require(len(row['reference_cases']) == 8, 'query reference coverage')
            for query, case in enumerate(row['reference_cases']):
                require(case['query'] == query and case['arms_checked'] == 4, 'query reference identity')
                local_reference[row['id'], query] = evidence(case['evidence'], row)
        if mode == 'TIMING':
            reference = local_reference
        else:
            require(local_reference == reference, 'timing/diagnostic full-result reference drift')
    require(timing['compiler'] == diagnostic['compiler'], 'compiler pair mismatch')
    require(timing['sample_count'] == 3840 and timing['trial_count'] == 960 and
            timing['warmup_executions'] == 768 and timing['diagnostic_count'] == 0 and
            timing['diagnostic_rows'] == [], 'timing execution cardinality')
    require(diagnostic['sample_count'] == 0 and diagnostic['trial_count'] == 0 and
            diagnostic['warmup_executions'] == 0 and diagnostic['diagnostic_count'] == 768 and
            diagnostic['raw_samples'] == [], 'diagnostic execution cardinality')
    samples, trials, identities = defaultdict(list), defaultdict(list), set()
    for row in timing['raw_samples']:
        profile, query, repeat, arm = (row[key] for key in ('profile', 'query', 'repeat', 'arm'))
        require(type(profile) is int and 0 <= profile < 24 and type(query) is int and 0 <= query < 8 and
                type(repeat) is int and 0 <= repeat < 5 and arm in ARMS, 'timed row identity')
        key = profile, query, repeat, arm
        require(key not in identities and row['results'] == reference[profile, query]['results'],
                'duplicate sample/result reference drift')
        identities.add(key)
        trial = (profile * 8 + query) * 5 + repeat
        require(row['trial'] == trial and type(row['order_position']) is int, 'trial identity')
        positive_time(row['full_search_ms'], 'invalid full-search time')
        samples[profile, arm].append(row['full_search_ms'])
        trials[trial].append(row)
    require(len(identities) == 3840 and len(trials) == 960, 'timing sample/trial coverage')
    engine = mt19937(20261124)
    for trial in range(960):
        rows = sorted(trials[trial], key=lambda row: row['order_position'])
        require([row['order_position'] for row in rows] == list(range(4)) and
                [row['arm'] for row in rows] == ordered_arms(engine), 'matched randomized order')
    memory, traces = {}, {}
    for row in diagnostic['diagnostic_rows']:
        profile, query, arm = (row[key] for key in ('profile', 'query', 'arm'))
        require(type(profile) is int and 0 <= profile < 24 and type(query) is int and 0 <= query < 8 and
                arm in ARMS and (profile, query, arm) not in memory, 'diagnostic row identity')
        expected = reference[profile, query]
        require(row['evidence'] == expected, 'diagnostic evidence differs from production reference')
        observed = trace(row['trace'], expected, expected_profiles[profile], arm)
        if arm != 'production':
            previous = traces.setdefault((profile, query), observed)
            require(observed == previous, 'discovered identity/probe trace differs between private arms')
        minimum = expected_profiles[profile]['count'] if arm == 'production' else row['trace']['visited_payload_lower_bound']
        allocations(row['allocations'], expected_profiles[profile], expected, minimum)
        memory[profile, query, arm] = row['allocations']['ordinary_peak_live_bytes']
    require(len(memory) == 768 and len(traces) == 192, 'diagnostic/trace arm coverage')
    summary = {}
    for profile in range(24):
        for arm in ARMS:
            values = sorted(samples[profile, arm])
            require(len(values) == 40, 'profile/arm sample coverage')
            summary[profile, arm] = dict(p50_ms=values[19], p95_ms=values[37],
                ordinary_peak_live_bytes=max(memory[profile, query, arm] for query in range(8)))
    decisions = []
    for profile in range(24):
        for arm in ARMS[2:]:
            candidate = summary[profile, arm]
            ratios, memory_gate = {}, True
            for baseline in ARMS[:2]:
                control = summary[profile, baseline]
                ratios[baseline] = dict(p50=candidate['p50_ms'] / control['p50_ms'],
                                        p95=candidate['p95_ms'] / control['p95_ms'])
                memory_gate &= all(memory[profile, query, arm] <= memory[profile, query, baseline]
                                   for query in range(8))
            passed = memory_gate and all(value['p50'] <= .9 and value['p95'] <= 1 for value in ratios.values())
            decisions.append(dict(profile=profile, arm=arm, ratios=ratios,
                ordinary_allocation_payload_gate=memory_gate,
                result='CONDITIONAL' if passed else 'KEEP_CURRENT'))
    return dict(status='PASS', samples=3840, trials=960, diagnostic_rows=768,
                decisions=decisions, scope='synthetic full search; not held-out quality or production qualification')


def verify_fixture_stream(stream, profile):
    def exact(size):
        data = stream.read(size)
        require(len(data) == size, 'truncated fixture')
        return data

    def word():
        return struct.unpack('<Q', exact(8))[0]

    def text():
        length = word()
        require(length <= 64, 'fixture string framing')
        return exact(length).decode('utf-8')

    require(exact(8) == b'MPDEDUP1', 'fixture magic')
    header = tuple(word() for _ in range(11))
    require(header == (profile['width'], profile['count'], WORKLOADS.index(profile['workload']),
        int(profile['filtered']), 8, profile['bits_per_table'], profile['max_probe_radius'], 10, 64, 128, 8),
        'fixture frozen parameters')
    words = profile['width'] // 64
    masks = [(1 << 64) - 1] * words
    for projection in range(8 * profile['bits_per_table']):
        bit = projection * profile['width'] // (8 * profile['bits_per_table'])
        masks[bit // 64] &= ~(1 << (bit % 64))
    corpus = mt19937(20261122, True)
    for position in range(profile['count']):
        for lane in range(words):
            expected = next(corpus)
            if profile['workload'] == 'projected_skew' and position % 8 != 7:
                expected &= masks[lane]
            require(word() == expected, 'fixture corpus RNG/prefix/skew')
        require(text() == f'mp:{position:08}' and text() == 'cohort' and text() == str(position % 16),
                'fixture IDs/metadata')
    queries = mt19937(20261123, True)
    for _ in range(8):
        for lane in range(words):
            expected = next(queries)
            if profile['workload'] == 'projected_skew':
                expected &= masks[lane]
            require(word() == expected, 'fixture query RNG/skew')
    require(stream.read(1) == b'', 'trailing fixture bytes')


def audit_directory(directory, expected_source=None, repository=None, verify_fixtures=False):
    manifest = read_json(directory / 'manifest.json')
    require((directory / 'manifest.sha256').read_text(encoding='ascii').strip() ==
            digest(directory / 'manifest.json'), 'manifest SHA-256 sidecar')
    require(manifest['schema'] == 'r6_multiprobe_dedup_execution_v1' and
            manifest['status'] == 'EXECUTED' and manifest['production_activation'] is False and
            manifest['applied_quality'] == 'NOT_EXECUTED', 'manifest research identity')
    source = manifest['source_commit']
    require(re.fullmatch(r'[0-9a-f]{40}', source) and
            re.fullmatch(r'[0-9a-f]{40}', manifest['source_tree']), 'manifest source identity')
    require(expected_source is None or expected_source == source, 'unexpected measured source')
    require(manifest['compile_and_link_commands'], 'missing compile provenance')
    require(manifest['lto'] == 'DISABLED_VERIFIED' and manifest['effective_release_optimization'],
            'missing effective no-LTO/optimization gate')
    optimization = []
    if manifest['compiler'] == 'GNU':
        commands = manifest['compile_and_link_commands']
        require(all(not re.search(r'(?:^|\s)-flto(?:=\S*)?(?:\s|$)', command) for command in commands),
                'LTO command outside measured call boundary')
        for name, count in [('MultiProbeHammingIndex.cpp', 1), ('DenseResearchMultiProbe.cpp', 2),
                            ('SparseResearchMultiProbe.cpp', 2), ('CompactResearchMultiProbe.cpp', 2)]:
            found = [command for command in commands if re.search(r'\s-c\s', command) and name in command]
            require(len(found) == count, 'missing/separate translation-unit provenance')
            optimization.extend(tuple(re.findall(r'(?:^|\s)(-O[0-3sgz]|-f(?:no-)?(?:fast-math|exceptions|rtti)|'
                r'-m(?:arch=\S+|avx2|popcnt))(?=\s|$)', command)) for command in found)
        require(optimization[0] and all(value == optimization[0] for value in optimization),
                'production/private compilation optimization mismatch')
    else:
        require(manifest['compiler'] == 'MSVC', 'unregistered execution compiler')
        ns = {'m': 'http://schemas.microsoft.com/developer/msbuild/2003'}
        for name in ('agent_memory.vcxproj', 'agent-memory-multiprobe-dedup.vcxproj',
                     'agent-memory-multiprobe-dedup-diagnostic.vcxproj'):
            root = ET.parse(directory / name).getroot()
            compile_nodes = []
            for group in root:
                if 'Release' not in group.attrib.get('Condition', ''):
                    continue
                whole = group.find('m:WholeProgramOptimization', ns)
                require(whole is None or whole.text != 'true', 'MSVC Release LTO enabled')
                node = group.find('m:ClCompile', ns)
                if node is not None:
                    compile_nodes.append(node)
            require(len(compile_nodes) == 1, 'missing MSVC Release compile settings')
            node = compile_nodes[0]
            require(not re.search(r'/GL(?:\s|<|$)', ET.tostring(node, encoding='unicode'), re.I), 'hidden MSVC LTO')
            optimization.append(tuple(node.findtext('m:' + field, default='', namespaces=ns)
                for field in ('Optimization', 'InlineFunctionExpansion', 'FloatingPointModel', 'EnableEnhancedInstructionSet')))
        require(optimization[0][0] and all(value == optimization[0] for value in optimization),
                'MSVC production/private optimization mismatch')
    files, canonical_names = {}, set()
    for record in manifest['files']:
        retained_record(record, record['bytes'], record['sha256'])
        relative = Path(record['path'])
        require(record['path'].casefold() not in canonical_names, 'duplicate retained path')
        canonical_names.add(record['path'].casefold())
        actual = directory / relative
        require(actual.is_file(), 'missing retained file')
        retained_record(record, actual.stat().st_size, digest(actual))
        files[record['path']] = record
    required = {'timing/raw.json', 'diagnostic/raw.json', 'measured.exe', 'diagnostic.exe',
                'native-correctness.json', 'diagnostic-correctness.json',
                'generated/generation.json'}
    required.update('generated/' + name + suffix for name in
                    ('DenseResearchMultiProbe', 'SparseResearchMultiProbe', 'CompactResearchMultiProbe')
                    for suffix in ('.cpp', '.hpp'))
    require(required <= files.keys(), 'missing native/generated evidence')
    sources = {}
    for record in manifest['source_files']:
        path, retained = record['path'], record['retained_path']
        require(path not in sources and retained in files and retained.startswith('sources/'),
                'source file retention/identity')
        sources[path] = retained
        value = (directory / retained).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(value)).encode() + b'\0' + value).hexdigest()
        require(record['blob'] == blob, 'retained source blob')
        if repository is not None:
            actual = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', source + ':' + path],
                                             text=True).strip()
            require(actual == blob, 'source does not belong to measured commit')
    require(SOURCE_PATHS <= sources.keys(), 'source dependency coverage')
    if repository is not None:
        tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', source + '^{tree}'], text=True).strip()
        require(tree == manifest['source_tree'], 'source commit/tree binding')
    timing, diagnostic = read_json(directory / 'timing/raw.json'), read_json(directory / 'diagnostic/raw.json')
    compiler_binding(manifest, timing)
    compiler_binding(manifest, diagnostic)
    result = validate(timing, diagnostic)
    for profile in profiles():
        name = f"fixtures/profile-{profile['id']}.bin"
        first, second = files['timing/' + name], files['diagnostic/' + name]
        require((first['bytes'], first['sha256']) == (second['bytes'], second['sha256']),
                'timing/diagnostic synthetic fixture mismatch')
        if verify_fixtures:
            with (directory / 'timing' / name).open('rb') as fixture:
                verify_fixture_stream(fixture, profile)
    generation = read_json(directory / 'generated/generation.json')
    require(generation['reverse_transform'] == 'PASS' and generation['dedup_substitutions'] == [0, 2, 2] and
            generation['diagnostic_hooks'] == 7, 'generated source substitution contract')
    generator_path = Path(__file__).with_name('generate-multiprobe-dedup.py')
    require(generator_path.read_bytes() == (directory / sources[
        'tools/agent-memory-bench/generate-multiprobe-dedup.py']).read_bytes(),
        'audited generator differs from measured generator')
    generator = runpy.run_path(str(generator_path))
    expected_generated = generator['generate'](
        (directory / sources['src/agent_memory/index/MultiProbeHammingIndex.cpp']).read_bytes(),
        (directory / sources['src/agent_memory/index/MultiProbeHammingIndex.hpp']).read_bytes())
    require(set(generation['generated']) == set(expected_generated), 'generated source arm/file coverage')
    for name, expected in generation['generated'].items():
        require('generated/' + name in files and files['generated/' + name]['sha256'] == expected,
                'generated source manifest binding')
        require((directory / 'generated' / name).read_bytes() == expected_generated[name],
                'generated implementation differs from source-bound transformation')
    for name in ('native-correctness.json', 'diagnostic-correctness.json'):
        controls = read_json(directory / name)
        compiler_binding(manifest, controls)
        require(controls['status'] == 'PASS' and controls['native_control_cases'] > 0 and
                controls['literal_golden_cases'] == 3 and controls['compact_positions'] == 4096 and
                controls['independent_readers'] == 4 and controls['reopen'] == 'NOT_APPLICABLE' and
                controls['quality'] == 'NOT_EXECUTED', 'native semantic/failure controls')
    return result


def synthetic_fixture():
    """Validator-only records: not an executed corpus, native oracle or timing."""
    common = dict(schema_version=1, family='r6_multiprobe_dedup', status='EXECUTED',
        configuration=copy.deepcopy(CONFIG), sizeof_size_t=8, compiler=dict(id='GNU', version='VALIDATOR_ONLY'),
        quality='NOT_EXECUTED', production_activation=False, source_identity='EXTERNAL_MANIFEST_REQUIRED',
        peak_rss='NOT_MEASURED', profiles=[], raw_samples=[], diagnostic_rows=[])
    timing, diagnostic = copy.deepcopy(common), copy.deepcopy(common)
    timing.update(mode='TIMING', allocation_scope='NOT_MEASURED', sample_count=3840, trial_count=960,
                  warmup_executions=768, diagnostic_count=0)
    diagnostic.update(mode='DIAGNOSTIC', allocation_scope='ordinary_new_payload_only', sample_count=0,
                      trial_count=0, warmup_executions=0, diagnostic_count=768)
    engine, trial = mt19937(20261124), 0
    for profile in profiles():
        count = profile['count']
        row = dict(profile, fixture_file=f"fixtures/profile-{profile['id']}.bin",
                   build_ms={arm: 1 for arm in ARMS}, first_search_ms={arm: 1 for arm in ARMS}, reference_cases=[])
        for query in range(8):
            hits = [dict(id=f'mp:{position:08}', distance=1, metadata={'cohort': '0'})
                    for position in (0, 16)]
            expected = dict(results=hits, candidates=2, buckets=8, postings=8)
            row['reference_cases'].append(dict(query=query, arms_checked=4, evidence=expected))
            for arm in ARMS:
                capacity = count if arm == 'dense_copy' else 16
                minimum = count if arm == 'dense_copy' else 16 + 8 * capacity if arm == 'sparse_set' else 8 * capacity
                observed = None if arm == 'production' else dict(fnv1a64=f'{query + 1:016x}', first_seen=2,
                    accepted=2, buckets=8, postings=8, distance_evaluations=2, completed_radii=1,
                    visited_capacity=capacity, visited_payload_lower_bound=minimum)
                diagnostic['diagnostic_rows'].append(dict(profile=profile['id'], query=query, arm=arm,
                    evidence=copy.deepcopy(expected), trace=observed,
                    allocations=dict(ordinary_allocations=5, ordinary_requested_bytes=4 * count + 8192,
                                     ordinary_peak_live_bytes=count + 8192, ordinary_retained_bytes=128)))
            for repeat in range(5):
                for position, arm in enumerate(ordered_arms(engine)):
                    timing['raw_samples'].append(dict(profile=profile['id'], query=query, repeat=repeat,
                        trial=trial, order_position=position, arm=arm, full_search_ms=1 + ARMS.index(arm),
                        results=copy.deepcopy(hits)))
                trial += 1
        timing['profiles'].append(row)
        diagnostic['profiles'].append(dict(copy.deepcopy(row), first_search_ms={}))
    return timing, diagnostic


def mutation_controls(timing, diagnostic):
    def coherent_wrong_tie_order(first, second):
        for raw in (first, second):
            for profile in raw['profiles']:
                for case in profile['reference_cases']:
                    case['evidence']['results'].reverse()
            for row in raw['raw_samples']:
                row['results'].reverse()
            for row in raw['diagnostic_rows']:
                row['evidence']['results'].reverse()

    def memory_underreport(t, d):
        for row in d['diagnostic_rows']:
            if row['arm'] == 'compact_set':
                row['trace']['visited_capacity'] = row['trace']['visited_payload_lower_bound'] = 0
                row['allocations'].update(ordinary_requested_bytes=1, ordinary_peak_live_bytes=1,
                                          ordinary_retained_bytes=0)

    changes = [
        lambda t, d: t['raw_samples'].pop(),
        lambda t, d: t['raw_samples'].append(copy.deepcopy(t['raw_samples'][0])),
        lambda t, d: t['raw_samples'][0].__setitem__('trial', -1),
        lambda t, d: t['raw_samples'][0].__setitem__('order_position', -1),
        lambda t, d: t['raw_samples'][0].__setitem__('full_search_ms', float('nan')),
        lambda t, d: t['raw_samples'][0]['results'][0].__setitem__('distance', t['raw_samples'][0]['results'][0]['distance'] + 1),
        lambda t, d: t['raw_samples'][0]['results'][0].__setitem__('id', 'mp:99999999'),
        lambda t, d: t['raw_samples'][0]['results'][0]['metadata'].__setitem__('cohort', 'bad'),
        lambda t, d: t['raw_samples'][0]['results'].append(copy.deepcopy(t['raw_samples'][0]['results'][0])),
        lambda t, d: t['profiles'].reverse(),
        lambda t, d: t['profiles'][0].__setitem__('bits_per_table', 4),
        lambda t, d: t['profiles'][0]['reference_cases'][0]['evidence'].__setitem__('buckets', 7),
        lambda t, d: t['configuration'].__setitem__('order_seed', 0),
        lambda t, d: t.__setitem__('warmup_executions', 0),
        lambda t, d: d.__setitem__('allocation_scope', 'NOT_MEASURED'),
        lambda t, d: d.__setitem__('mode', 'TIMING'),
        lambda t, d: d['diagnostic_rows'].pop(),
        lambda t, d: d['diagnostic_rows'][0].__setitem__('trace', {}),
        lambda t, d: d['diagnostic_rows'][1]['trace'].__setitem__('first_seen', 0),
        lambda t, d: d['diagnostic_rows'][1]['trace'].__setitem__('completed_radii', 2),
        lambda t, d: d['diagnostic_rows'][2]['trace'].__setitem__('fnv1a64', '0' * 16),
        lambda t, d: d['diagnostic_rows'][3]['trace'].__setitem__('visited_capacity', 15),
        lambda t, d: d['diagnostic_rows'][0]['allocations'].__setitem__('ordinary_peak_live_bytes', 128),
        lambda t, d: d['diagnostic_rows'][1]['allocations'].__setitem__('ordinary_peak_live_bytes', 128),
        lambda t, d: d['diagnostic_rows'][2]['allocations'].__setitem__('ordinary_peak_live_bytes', 128),
        lambda t, d: d['diagnostic_rows'][3]['allocations'].__setitem__('ordinary_peak_live_bytes', 128),
        lambda t, d: d['diagnostic_rows'][1]['trace'].__setitem__('visited_payload_lower_bound', 0),
        lambda t, d: d['diagnostic_rows'][2]['allocations'].__setitem__('ordinary_peak_live_bytes', 0),
        lambda t, d: d['diagnostic_rows'][0]['allocations'].__setitem__('ordinary_requested_bytes', 0),
        lambda t, d: d['profiles'][0]['reference_cases'][0]['evidence']['results'][0].__setitem__('distance',
            d['profiles'][0]['reference_cases'][0]['evidence']['results'][0]['distance'] + 1),
        lambda t, d: t.__setitem__('quality', 'MEASURED'),
        lambda t, d: d.__setitem__('production_activation', True),
        coherent_wrong_tie_order,
        memory_underreport]
    for change in changes:
        first, second = copy.deepcopy(timing), copy.deepcopy(diagnostic)
        change(first, second)
        try:
            validate(first, second)
        except (ValueError, KeyError):
            continue
        raise ValueError('corrupt result evidence accepted')
    return len(changes)


def fixture_bytes(profile):
    output = io.BytesIO(b'MPDEDUP1')
    output.seek(8)
    def word(value):
        output.write(struct.pack('<Q', value))
    def text(value):
        value = value.encode()
        word(len(value)); output.write(value)
    for value in (profile['width'], profile['count'], WORKLOADS.index(profile['workload']),
                  int(profile['filtered']), 8, profile['bits_per_table'], profile['max_probe_radius'], 10, 64, 128, 8):
        word(value)
    masks = [(1 << 64) - 1] * (profile['width'] // 64)
    for projection in range(8 * profile['bits_per_table']):
        bit = projection * profile['width'] // (8 * profile['bits_per_table'])
        masks[bit // 64] &= ~(1 << (bit % 64))
    corpus = mt19937(20261122, True)
    for position in range(profile['count']):
        for mask in masks:
            value = next(corpus)
            word(value & mask if profile['workload'] == 'projected_skew' and position % 8 != 7 else value)
        text(f'mp:{position:08}'); text('cohort'); text(str(position % 16))
    queries = mt19937(20261123, True)
    for _ in range(8):
        for mask in masks:
            value = next(queries)
            word(value & mask if profile['workload'] == 'projected_skew' else value)
    return output.getvalue()


def fixture_controls():
    profile = dict(profiles()[0], count=2)
    value = fixture_bytes(profile)
    verify_fixture_stream(io.BytesIO(value), profile)
    alternatives = [value[:-1], value + b'\0', b'BADMAGIC' + value[8:],
                    value[:8] + struct.pack('<Q', 256) + value[16:],
                    value[:96] + bytes([value[96] ^ 1]) + value[97:],
                    value[:112] + struct.pack('<Q', 1000) + value[120:],
                    value[:125] + bytes([value[125] ^ 1]) + value[126:],
                    value.replace(b'cohort', b'Cohort', 1),
                    value[:-8] + struct.pack('<Q', 0)]
    for altered in alternatives:
        try:
            verify_fixture_stream(io.BytesIO(altered), profile)
        except (ValueError, UnicodeError):
            continue
        raise ValueError('corrupt fixture accepted')
    skew = dict(profile, count=9, workload='projected_skew', max_probe_radius=1)
    value = fixture_bytes(skew)
    verify_fixture_stream(io.BytesIO(value), skew)
    try:
        verify_fixture_stream(io.BytesIO(value[:96] + bytes([value[96] ^ 1]) + value[97:]), skew)
    except ValueError:
        return len(alternatives) + 1
    raise ValueError('corrupt projected skew accepted')


def retention_controls():
    record = dict(path='measured.exe', bytes=3, sha256=hashlib.sha256(b'abc').hexdigest())
    retained_record(record, 3, record['sha256'])
    alternatives = [dict(record, bytes=4), dict(record, sha256='0' * 64),
                    dict(record, path='../measured.exe'), dict(record, path='C:measured.exe'),
                    dict(record, path='folder\\measured.exe'), dict(record, path='folder//measured.exe')]
    for altered in alternatives:
        try:
            retained_record(altered, 3, record['sha256'])
        except ValueError:
            continue
        raise ValueError('corrupt retained-file binding accepted')
    return len(alternatives)


def compiler_controls():
    manifest = dict(compiler='GNU', compiler_version='CONTROL')
    raw = dict(compiler=dict(id='GNU', version='CONTROL'))
    compiler_binding(manifest, raw)
    for changed in (dict(id='Clang', version='CONTROL'), dict(id='GNU', version='OTHER')):
        try:
            compiler_binding(manifest, dict(compiler=changed))
        except ValueError:
            continue
        raise ValueError('compiler attribution corruption accepted')
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--expected-source')
    parser.add_argument('--verify-fixtures', action='store_true')
    parser.add_argument('--mutation-self-test', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        require([next(mt19937(5489)), next(mt19937(5489, True))] ==
                [3499211612, 14514284786278117030], 'independent engine golden values')
        timing, diagnostic = synthetic_fixture()
        validate(timing, diagnostic)
        count = mutation_controls(timing, diagnostic) + fixture_controls() + retention_controls() + compiler_controls()
        print(f'MultiProbe validator-only self-test: PASS; {count} rejection controls; no native benchmark')
        return
    require(args.directory is not None, '--directory required')
    result = audit_directory(args.directory, args.expected_source, args.repository, args.verify_fixtures)
    result['mutations'] = 0
    if args.mutation_self_test:
        result['mutations'] = mutation_controls(read_json(args.directory / 'timing/raw.json'),
                                                read_json(args.directory / 'diagnostic/raw.json')) + fixture_controls() + retention_controls() + compiler_controls()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
