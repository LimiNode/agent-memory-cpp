#!/usr/bin/env python3
"""Generate research-only copies; reject source drift before any transformation."""

import argparse
import hashlib
from pathlib import Path


SOURCE_SHA256 = "77e3dda6f814265d0476c5bdc02af69ee2a18159c6cb5244171a26b7813a5efb"
HEADER_SHA256 = "b2a83f2c2665073f47d3556d51774276133f915dfa67d2fdba1b51a7cc763915"
ANCHOR = "        [[nodiscard]] std::vector<LexicalSearchResult> admit_reranked_hits("


def generate(source: str, header: str) -> dict[str, str]:
    # Text is LF-normalized by Path.read_text; checkout line endings are not semantics.
    for label, text, expected in (
        ("source", source, SOURCE_SHA256), ("header", header, HEADER_SHA256)
    ):
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
            raise ValueError(f"pinned HybridRetrievalEngine {label} SHA-256 mismatch")
    if source.count(ANCHOR) != 1:
        raise ValueError("admission function anchor must occur exactly once")
    opening = source.index("{", source.index(ANCHOR))
    depth = 1
    closing = opening + 1
    while depth:
        depth += (source[closing] == "{") - (source[closing] == "}")
        closing += 1
    output = {}
    for label, admission in (("Linear", None), ("Sorted", "admit_sorted"), ("Hash", "admit_hash")):
        class_name = f"{label}ResearchHybridRetrievalEngine"
        transformed = source
        if admission:
            transformed = (
                source[:opening]
                + "{\n            return agent_memory_research::"
                + admission
                + "(admitted, reranked);\n        }"
                + source[closing:]
            )
            declaration = (
                "namespace agent_memory_research {\n"
                "    std::vector<agent_memory::LexicalSearchResult> " + admission + "(\n"
                "        const std::vector<agent_memory::LexicalSearchResult>& admitted,\n"
                "        const std::vector<agent_memory::LexicalSearchResult>& reranked);\n"
                "}\n\n"
            )
            transformed = transformed.replace("namespace agent_memory {", declaration + "namespace agent_memory {", 1)
        transformed = transformed.replace("HybridRetrievalEngine", class_name)
        transformed_header = header.replace("HybridRetrievalEngine", class_name).replace(
            "AGENT_MEMORY_HEADER_RETRIEVAL_HYBRID_RETRIEVAL_ENGINE_HPP_INCLUDED",
            f"AGENT_MEMORY_HEADER_RESEARCH_{label.upper()}_HYBRID_RETRIEVAL_ENGINE_HPP_INCLUDED",
        )
        output[f"{class_name}.cpp"] = transformed
        output[f"{class_name}.hpp"] = transformed_header
    return output


def self_test() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/agent_memory/retrieval/HybridRetrievalEngine.cpp").read_text(encoding="utf-8")
    header = (root / "src/agent_memory/retrieval/HybridRetrievalEngine.hpp").read_text(encoding="utf-8")
    copies = generate(source, header)
    assert len(copies) == 6
    assert copies["LinearResearchHybridRetrievalEngine.cpp"].replace(
        "LinearResearchHybridRetrievalEngine", "HybridRetrievalEngine"
    ) == source
    retrieve_start = source.index("    RetrievalResponse HybridRetrievalEngine::retrieve(")
    for label in ("Sorted", "Hash"):
        restored = copies[f"{label}ResearchHybridRetrievalEngine.cpp"].replace(
            f"{label}ResearchHybridRetrievalEngine", "HybridRetrievalEngine"
        )
        assert restored.endswith(source[retrieve_start:]), "retrieve/materialization drift"
    for altered_source, altered_header in ((source + "\n", header), (source, header + "\n")):
        try:
            generate(altered_source, altered_header)
        except ValueError:
            pass
        else:
            raise AssertionError("source/header drift was accepted")
    print("production-shaped reranker source generator self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.output_dir is None:
        parser.error("--output-dir is required")
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/agent_memory/retrieval/HybridRetrievalEngine.cpp").read_text(encoding="utf-8")
    header = (root / "src/agent_memory/retrieval/HybridRetrievalEngine.hpp").read_text(encoding="utf-8")
    copies = generate(source, header)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, contents in copies.items():
        (args.output_dir / name).write_text(contents, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
