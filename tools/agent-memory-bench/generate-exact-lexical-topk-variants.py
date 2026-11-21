#!/usr/bin/env python3
"""Generate research-only search variants from a pinned production source."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CPP_SHA256 = "97d50c06acc17724adc37afe24dd3d1664f05471314309da9a29038d07b28971"
HPP_SHA256 = "81f6cad5048f1a502536e6b99cf4253bb8f582ee0297fc3fa261754eaf20b471"
VARIANTS = {
    "partial_sort": "ResearchPartialSortExactLexicalIndex",
    "nth_element_then_sort": "ResearchNthElementExactLexicalIndex",
    "bounded_heap": "ResearchBoundedHeapExactLexicalIndex",
}
PROFILED_VARIANTS = {
    "full_sort": "ResearchProfiledFullSortExactLexicalIndex",
    "partial_sort": "ResearchProfiledPartialSortExactLexicalIndex",
    "nth_element_then_sort": "ResearchProfiledNthElementExactLexicalIndex",
    "bounded_heap": "ResearchProfiledBoundedHeapExactLexicalIndex",
}
SORT_BLOCK = """        std::sort(
            results.begin(),
            results.end(),
            [](const auto& lhs, const auto& rhs) {
                if(lhs.score == rhs.score) {
                    return lhs.chunk_id < rhs.chunk_id;
                }
                return lhs.score > rhs.score;
            }
        );"""
COMPARATOR = """        const auto better = [](const auto& lhs, const auto& rhs) {
            if(lhs.score == rhs.score) return lhs.chunk_id < rhs.chunk_id;
            return lhs.score > rhs.score;
        };
        const auto effective = std::min(query.limit, results.size());
"""
REPLACEMENTS = {
    "partial_sort": COMPARATOR + """        if(effective > 0U) {
            std::partial_sort(results.begin(), results.begin() + effective, results.end(), better);
        }""",
    "nth_element_then_sort": COMPARATOR + """        if(effective > 0U && effective < results.size()) {
            std::nth_element(results.begin(), results.begin() + effective, results.end(), better);
        }
        std::sort(results.begin(), results.begin() + effective, better);""",
    "bounded_heap": COMPARATOR + """        std::vector<LexicalSearchResult> selected;
        selected.reserve(effective);
        if(effective > 0U) {
            for(auto& hit : results) {
                if(selected.size() < effective) {
                    selected.push_back(std::move(hit));
                    std::push_heap(selected.begin(), selected.end(), better);
                } else if(better(hit, selected.front())) {
                    std::pop_heap(selected.begin(), selected.end(), better);
                    selected.back() = std::move(hit);
                    std::push_heap(selected.begin(), selected.end(), better);
                }
            }
        }
        std::sort(selected.begin(), selected.end(), better);
        results = std::move(selected);""",
}


def profile_transforms(selection: str) -> list[tuple[str, str]]:
    """Explicit reversible instrumentation; never infer a stage by subtraction."""
    return [
        ('#include "ExactLexicalIndex.hpp"', '#include "ExactLexicalIndex.hpp"\n#include "lexical_topk_stage_profile.hpp"'),
        ("        if(query.limit == 0) {", "        lexical_topk_research::ScopedStage full_search_timer(lexical_topk_research::Stage::FullSearch);\n        if(query.limit == 0) {"),
        ("        for(const auto& item : m_records) {\n            const auto& record = item.second;",
         "        auto next_record = m_records.begin();\n        while(next_record != m_records.end()) {\n            const auto traversal_started = lexical_topk_research::ProfileClock::now();\n            const auto& record = (next_record++)->second;\n            lexical_topk_research::record_elapsed(lexical_topk_research::Stage::CandidateGeneration, traversal_started);"),
        ("            if(!matches_metadata_filters(record.record.metadata, query.metadata_filters)) {",
         "            bool matches = false;\n            {\n                lexical_topk_research::ScopedStage filtering_timer(lexical_topk_research::Stage::MetadataFiltering);\n                matches = matches_metadata_filters(record.record.metadata, query.metadata_filters);\n            }\n            if(!matches) {"),
        ("            const auto score = score_record(record, query);",
         "            float score = 0.0F;\n            {\n                lexical_topk_research::ScopedStage scoring_timer(lexical_topk_research::Stage::Bm25Scoring);\n                score = score_record(record, query);\n            }"),
        ("            if(score > 0.0F) {\n                LexicalSearchResult hit;",
         "            if(score > 0.0F) {\n                lexical_topk_research::ScopedStage construction_timer(lexical_topk_research::Stage::CandidateConstruction);\n                LexicalSearchResult hit;"),
        (selection, "        {\n            lexical_topk_research::ScopedStage selection_timer(lexical_topk_research::Stage::TopKSelection);\n" + selection + "\n        }"),
        ("        if(results.size() > query.limit) {\n            results.resize(query.limit);\n        }",
         "        {\n            lexical_topk_research::ScopedStage materialization_timer(lexical_topk_research::Stage::ResultMaterialization);\n            if(results.size() > query.limit) {\n                results.resize(query.limit);\n            }\n        }"),
    ]


def add_profile(source: str, selection: str) -> str:
    for original, instrumented in profile_transforms(selection):
        if source.count(original) != 1:
            raise ValueError("production profile anchor is not unique")
        source = source.replace(original, instrumented)
    return source


def generate(source: str, header: str, fixture: bytes | None = None) -> dict[str, str]:
    for text, digest, label in ((source, CPP_SHA256, "implementation"), (header, HPP_SHA256, "header")):
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            raise ValueError(f"pinned production {label} changed; independent source-transform review required")
    guard = "AGENT_MEMORY_HEADER_LEXICAL_EXACT_LEXICAL_INDEX_HPP_INCLUDED"
    if source.count(SORT_BLOCK) != 1 or header.count(guard) != 2:
        raise ValueError("production selection/header anchor is not unique")
    generated = {}
    for variant, name in VARIANTS.items():
        changed_source = source.replace(SORT_BLOCK, REPLACEMENTS[variant])
        # Class, options type, and own-header include are renamed together.
        # All remaining production code, including scoring and construction,
        # remains byte-identical after reversing these identifier substitutions.
        generated[f"{name}.cpp"] = changed_source.replace("ExactLexicalIndex", name)
        variant_guard = f"AGENT_MEMORY_HEADER_LEXICAL_RESEARCH_{variant.upper()}_EXACT_LEXICAL_INDEX_HPP_INCLUDED"
        generated[f"{name}.hpp"] = header.replace("ExactLexicalIndex", name).replace(guard, variant_guard)
    if fixture is not None:
        # The raw recipe is embedded rather than loaded from a mutable path at
        # execution time. Its digest is a recipe identity, not an RSS/corpus hash.
        json.loads(fixture)
        generated["lexical_topk_profile_fixture.hpp"] = (
            "#pragma once\n#ifndef AGENT_MEMORY_HEADER_TOOLS_LEXICAL_TOPK_PROFILE_FIXTURE_HPP_INCLUDED\n"
            "#define AGENT_MEMORY_HEADER_TOOLS_LEXICAL_TOPK_PROFILE_FIXTURE_HPP_INCLUDED\n"
            'inline constexpr const char* kLexicalProfileFixture = R"profile(' + fixture.decode("utf-8") + ')profile";\n'
            'inline constexpr const char* kLexicalProfileFixtureSha256 = "' + hashlib.sha256(fixture).hexdigest() + '";\n'
            "#endif\n"
        )
    for variant, name in PROFILED_VARIANTS.items():
        selection = SORT_BLOCK if variant == "full_sort" else REPLACEMENTS[variant]
        changed_source = source.replace(SORT_BLOCK, selection)
        generated[f"{name}.cpp"] = add_profile(changed_source, selection).replace("ExactLexicalIndex", name)
        variant_guard = f"AGENT_MEMORY_HEADER_LEXICAL_RESEARCH_PROFILED_{variant.upper()}_EXACT_LEXICAL_INDEX_HPP_INCLUDED"
        generated[f"{name}.hpp"] = header.replace("ExactLexicalIndex", name).replace(guard, variant_guard)
    return generated


def generated_manifest_sha256(files: dict[str, str]) -> str:
    manifest = "".join(f"{name}:{hashlib.sha256(content.encode('utf-8')).hexdigest()}\n" for name, content in sorted(files.items()))
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def self_test(root: Path) -> None:
    source = (root / "src/agent_memory/lexical/ExactLexicalIndex.cpp").read_text(encoding="utf-8")
    header = (root / "src/agent_memory/lexical/ExactLexicalIndex.hpp").read_text(encoding="utf-8")
    generated = generate(source, header)
    for variant, name in VARIANTS.items():
        if generated[f"{name}.hpp"].count("_HPP_INCLUDED") != 2:
            raise AssertionError("generated include guard does not retain the project naming suffix")
        restored = generated[f"{name}.cpp"].replace(name, "ExactLexicalIndex").replace(REPLACEMENTS[variant], SORT_BLOCK)
        if restored != source:
            raise AssertionError("variant changes production code outside selection and identifier rename")
    for variant, name in PROFILED_VARIANTS.items():
        restored = generated[f"{name}.cpp"].replace(name, "ExactLexicalIndex")
        selection = SORT_BLOCK if variant == "full_sort" else REPLACEMENTS[variant]
        for original, instrumented in reversed(profile_transforms(selection)):
            if restored.count(instrumented) != 1:
                raise AssertionError("profile instrumentation cannot be reversed uniquely")
            restored = restored.replace(instrumented, original)
        restored = restored.replace(selection, SORT_BLOCK)
        if restored != source:
            raise AssertionError("profile changes production code outside explicit timers/traversal/selection")
    for bad_source, bad_header in ((source + "\n", header), (source, header + "\n"), (source.replace(SORT_BLOCK, ""), header)):
        try:
            generate(bad_source, bad_header)
        except ValueError:
            pass
        else:
            raise AssertionError("changed source/header was accepted")
    print("exact lexical top-k source transform: PASS (3 negative controls)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(args.source_root)
            return 0
        if args.output_dir is None:
            parser.error("--output-dir is required unless --self-test is used")
        files = generate(
            (args.source_root / "src/agent_memory/lexical/ExactLexicalIndex.cpp").read_text(encoding="utf-8"),
            (args.source_root / "src/agent_memory/lexical/ExactLexicalIndex.hpp").read_text(encoding="utf-8"),
            (args.source_root / "tools/agent-memory-bench/lexical-topk-stage-profiles.json").read_bytes(),
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (args.output_dir / name).write_bytes(content.encode("utf-8"))
        print(generated_manifest_sha256(files))
        return 0
    except (OSError, ValueError) as error:
        print(f"source generation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
