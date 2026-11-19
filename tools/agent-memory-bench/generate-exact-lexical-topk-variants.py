#!/usr/bin/env python3
"""Generate research-only search variants from a pinned production source."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


CPP_SHA256 = "97d50c06acc17724adc37afe24dd3d1664f05471314309da9a29038d07b28971"
HPP_SHA256 = "81f6cad5048f1a502536e6b99cf4253bb8f582ee0297fc3fa261754eaf20b471"
VARIANTS = {
    "partial_sort": "ResearchPartialSortExactLexicalIndex",
    "nth_element_then_sort": "ResearchNthElementExactLexicalIndex",
    "bounded_heap": "ResearchBoundedHeapExactLexicalIndex",
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


def generate(source: str, header: str) -> dict[str, str]:
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
