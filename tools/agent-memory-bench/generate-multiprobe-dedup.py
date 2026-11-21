#!/usr/bin/env python3
"""Generate private source-bound MultiProbe copies; never modify production."""
import argparse
import hashlib
import json
from pathlib import Path
import re

CPP_HASH = '3a87d4f0fae3aebe3715dece107fb0ea92b6058286d53e2e0debe0221d018b46'
HPP_HASH = '5457b8973ac4d9da22b50d466a8d0e2ce6bad6254098ae16cef0cd921e5093f5'
NAMES = ('DenseResearchMultiProbe', 'SparseResearchMultiProbe', 'CompactResearchMultiProbe')
DECLARATION = '        std::vector<unsigned char> seen(m_records.size(), 0);'
FIRST = '                if(seen[position] == 0) {\n                    seen[position] = 1;'


def require(value, message):
    if not value:
        raise ValueError(message)


def replace_once(source, old, new):
    require(source.count(old) == 1, 'source sentinel absent/duplicated: ' + old[:60])
    return source.replace(old, new, 1)


def generate(cpp, hpp):
    require(hashlib.sha256(cpp).hexdigest() == CPP_HASH, 'production cpp digest changed')
    require(hashlib.sha256(hpp).hexdigest() == HPP_HASH, 'production header digest changed')
    original, header = cpp.decode('utf-8').replace('\r\n', '\n'), hpp.decode('utf-8').replace('\r\n', '\n')
    start = header.index('    class MultiProbeHammingIndex final')
    end = header.index('\n} // namespace agent_memory', start)
    body = header[start:end]
    outputs = {}
    for index, name in enumerate(NAMES):
        source = original
        substitutions = []

        def change(old, new):
            nonlocal source
            source = replace_once(source, old, new)
            substitutions.append((new, old))

        if index == 1:
            change(DECLARATION, '        std::unordered_set<std::size_t> seen;\n        seen.reserve(candidate_target);')
            change(FIRST, '                if(seen.insert(position).second) {')
        elif index == 2:
            change(DECLARATION, '        multiprobe_research::CompactVisited seen;')
            change(FIRST, '                if(seen.insert(position)) {')
        # Diagnostics are erased by preprocessing in the normal timed target.
        diagnostic = [
            ('            ++output.probed_bucket_count;',
             '            multiprobe_research::trace_bucket(table, key);'),
            ('                ++output.visited_posting_count;',
             '                multiprobe_research::trace_posting(position);'),
            ('                    if(!query.metadata_filters.empty()',
             '                    multiprobe_research::trace_first(position);'),
            ('                    candidates.push_back(position);',
             '                    multiprobe_research::trace_accepted(position);'),
            ('            if(candidates.size() >= candidate_target) {',
             '            multiprobe_research::trace_radius(radius, candidates.size());'),
            ('        output.candidate_count = scored.size();',
             '        for(const auto& item : scored) multiprobe_research::trace_distance(item.position, item.distance);')]
        memory = ('seen.capacity(), seen.capacity()' if index == 0 else
                  'seen.bucket_count(), seen.size() * sizeof(std::size_t) + seen.bucket_count() * sizeof(void*)'
                  if index == 1 else 'seen.capacity(), seen.capacity() * sizeof(std::size_t)')
        diagnostic.append(('        return output;\n    }\n\n    bool MultiProbeHammingIndex::erase',
                           '        multiprobe_research::trace_memory(' + memory + ');'))
        for sentinel, hook in diagnostic:
            block = '#if defined(AGENT_MEMORY_MP_DIAGNOSTIC)\n' + hook + '\n#endif\n'
            change(sentinel, block + sentinel)
        restored = source
        for new, old in reversed(substitutions):
            restored = replace_once(restored, new, old)
        require(restored == original, 'non-dedup source drift')
        source = replace_once(source, '#include "MultiProbeHammingIndex.hpp"',
                              '#include "' + name + '.hpp"\n#include "MultiProbeResearchSupport.hpp"\n#include <unordered_set>')
        source = re.sub(r'\bMultiProbeHammingIndex(?=::|\s*\()', name, source)
        guard = 'AGENT_MEMORY_HEADER_TOOLS_' + name.upper() + '_HPP_INCLUDED'
        generated_header = '#pragma once\n#ifndef ' + guard + '\n#define ' + guard + '\n'
        generated_header += '#include <agent_memory/index/MultiProbeHammingIndex.hpp>\nnamespace agent_memory {\n'
        generated_header += re.sub(r'\bMultiProbeHammingIndex\b', name, body) + '\n}\n#endif\n'
        outputs[name + '.cpp'] = source.encode('utf-8')
        outputs[name + '.hpp'] = generated_header.encode('utf-8')
    return outputs


def self_test(repository):
    cpp = (repository / 'src/agent_memory/index/MultiProbeHammingIndex.cpp').read_bytes()
    hpp = (repository / 'src/agent_memory/index/MultiProbeHammingIndex.hpp').read_bytes()
    outputs = generate(cpp, hpp)
    require(len(outputs) == 6, 'generated arm coverage')
    for altered, other in ((cpp + b' ', hpp), (cpp, hpp + b' '), (cpp[:-1], hpp)):
        try:
            generate(altered, other)
        except ValueError:
            continue
        raise ValueError('source mutation accepted')
    try:
        replace_once('x x', 'x', 'y')
    except ValueError:
        print('MultiProbe source generator self-test: PASS; 4 rejection controls; no native build')
        return
    raise ValueError('duplicate sentinel accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    if args.self_test:
        self_test(repository)
        return
    require(args.output_dir is not None, '--output-dir required')
    outputs = generate((repository / 'src/agent_memory/index/MultiProbeHammingIndex.cpp').read_bytes(),
                       (repository / 'src/agent_memory/index/MultiProbeHammingIndex.hpp').read_bytes())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (args.output_dir / name).write_bytes(data)
    manifest = dict(production_cpp_sha256=CPP_HASH, production_hpp_sha256=HPP_HASH,
                    generated={name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()},
                    reverse_transform='PASS', arms=list(NAMES), diagnostic_hooks=7,
                    dedup_substitutions=[0, 2, 2])
    (args.output_dir / 'generation.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
