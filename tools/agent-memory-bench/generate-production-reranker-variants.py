#!/usr/bin/env python3
"""Generate research-only copies; reject source drift before any transformation."""

import argparse
import hashlib
from pathlib import Path


SOURCE_SHA256 = "77e3dda6f814265d0476c5bdc02af69ee2a18159c6cb5244171a26b7813a5efb"
HEADER_SHA256 = "b2a83f2c2665073f47d3556d51774276133f915dfa67d2fdba1b51a7cc763915"
ANCHOR = "        [[nodiscard]] std::vector<LexicalSearchResult> admit_reranked_hits("

# These literal insertions add observation only; removing them restores the
# uninstrumented source byte-for-byte after LF normalization and class renaming.
PROFILE_INSERTIONS = (
    (
        "        std::vector<LexicalSearchResult> hits = m_index->search(lexical_query);",
        "        std::vector<LexicalSearchResult> hits;\n"
        "        {\n"
        "            agent_memory_research::AdmissionStageTimer timer(\n"
        "                agent_memory_research::AdmissionStage::IndexSearch);\n"
        "            hits = m_index->search(lexical_query);\n"
        "        }",
    ),
    (
        "        const auto admitted_hits = hits;",
        "        const auto admitted_hits = [&hits] {\n"
        "            agent_memory_research::AdmissionStageTimer timer(\n"
        "                agent_memory_research::AdmissionStage::TrustedPoolCopy);\n"
        "            return hits;\n"
        "        }();",
    ),
    (
        "        hits = m_reranker->rerank(request.query, std::move(hits));",
        "        {\n"
        "            agent_memory_research::AdmissionStageTimer timer(\n"
        "                agent_memory_research::AdmissionStage::Reranker);\n"
        "            hits = m_reranker->rerank(request.query, std::move(hits));\n"
        "        }",
    ),
    (
        "        hits = admit_reranked_hits(admitted_hits, hits);",
        "        {\n"
        "            agent_memory_research::AdmissionStageTimer timer(\n"
        "                agent_memory_research::AdmissionStage::AdmissionTotal);\n"
        "            hits = admit_reranked_hits(admitted_hits, hits);\n"
        "        }",
    ),
    (
        "        response.items.reserve(hits.size());",
        "        {\n"
        "        agent_memory_research::AdmissionStageTimer materialization_timer(\n"
        "            agent_memory_research::AdmissionStage::ResponseMaterialization);\n"
        "        response.items.reserve(hits.size());",
    ),
    ("        return response;\n    }", "        }\n        return response;\n    }"),
)

LINEAR_PROFILE_INSERTIONS = (
    (
        "            for(const auto& candidate : reranked) {",
        "            for(const auto& candidate : reranked) {\n"
        "                agent_memory_research::AdmissionStageTimer validation_timer(\n"
        "                    agent_memory_research::AdmissionStage::ValidationAndLookup);",
    ),
    (
        "                auto trusted = *matching;",
        "                validation_timer.finish();\n"
        "                {\n"
        "                agent_memory_research::AdmissionStageTimer copy_timer(\n"
        "                    agent_memory_research::AdmissionStage::TrustedResultCopy);\n"
        "                auto trusted = *matching;",
    ),
    (
        "                result.push_back(std::move(trusted));",
        "                result.push_back(std::move(trusted));\n                }",
    ),
)


def instrument(source: str) -> str:
    result = source
    for original, replacement in PROFILE_INSERTIONS:
        if result.count(original) != 1:
            raise ValueError("profile observation anchor must occur exactly once")
        result = result.replace(original, replacement, 1)
    first_line, remainder = result.split("\n", 1)
    result = first_line + '\n#include "RerankerResearchProfile.hpp"\n' + remainder
    return result


def strip_instrumentation(source: str) -> str:
    include = '#include "RerankerResearchProfile.hpp"\n'
    if source.count(include) != 1:
        raise ValueError("profile header insertion must occur exactly once")
    result = source.replace(include, "", 1)
    for original, replacement in reversed(PROFILE_INSERTIONS):
        if result.count(replacement) != 1:
            raise ValueError("profile observation changed its allowed transform")
        result = result.replace(replacement, original, 1)
    return result


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
        profiled_name = f"Profiled{class_name}"
        profiled_source = instrument(transformed).replace(class_name, profiled_name)
        if admission:
            profiled_source = profiled_source.replace(admission, f"{admission}_profiled")
        else:
            # The linear profile preserves the original predicate and ordering.
            for original, replacement in LINEAR_PROFILE_INSERTIONS:
                if profiled_source.count(original) != 1:
                    raise ValueError("linear admission profile anchor must be unique")
                profiled_source = profiled_source.replace(original, replacement, 1)
        output[f"{profiled_name}.cpp"] = profiled_source
        output[f"{profiled_name}.hpp"] = transformed_header.replace(
            class_name, profiled_name
        ).replace(f"RESEARCH_{label.upper()}_", f"RESEARCH_PROFILED_{label.upper()}_")
    return output


def self_test() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/agent_memory/retrieval/HybridRetrievalEngine.cpp").read_text(encoding="utf-8")
    header = (root / "src/agent_memory/retrieval/HybridRetrievalEngine.hpp").read_text(encoding="utf-8")
    copies = generate(source, header)
    assert len(copies) == 12
    for label in ("Linear", "Sorted", "Hash", "ProfiledLinear", "ProfiledSorted", "ProfiledHash"):
        copied_header = copies[f"{label}ResearchHybridRetrievalEngine.hpp"].replace(
            f"{label}ResearchHybridRetrievalEngine", "HybridRetrievalEngine"
        )
        guard_label = label.upper().replace("PROFILED", "PROFILED_")
        copied_header = copied_header.replace(
            f"AGENT_MEMORY_HEADER_RESEARCH_{guard_label}_HYBRID_RETRIEVAL_ENGINE_HPP_INCLUDED",
            "AGENT_MEMORY_HEADER_RETRIEVAL_HYBRID_RETRIEVAL_ENGINE_HPP_INCLUDED",
        )
        assert copied_header == header, "generated header escaped class/guard renaming"
    assert copies["LinearResearchHybridRetrievalEngine.cpp"].replace(
        "LinearResearchHybridRetrievalEngine", "HybridRetrievalEngine"
    ) == source
    retrieve_start = source.index("    RetrievalResponse HybridRetrievalEngine::retrieve(")
    for label in ("Sorted", "Hash"):
        restored = copies[f"{label}ResearchHybridRetrievalEngine.cpp"].replace(
            f"{label}ResearchHybridRetrievalEngine", "HybridRetrievalEngine"
        )
        assert restored.endswith(source[retrieve_start:]), "retrieve/materialization drift"
        # Restore the one permitted admission-body replacement and its declaration.
        body_start = source.index("{", source.index(ANCHOR))
        body_end = source.index("\n\n    } // namespace", body_start)
        copied_start = restored.index("{", restored.index(ANCHOR))
        copied_end = restored.index("\n\n    } // namespace", copied_start)
        restored = restored[:copied_start] + source[body_start:body_end] + restored[copied_end:]
        declaration_start = restored.index("namespace agent_memory_research {")
        declaration_end = restored.index("namespace agent_memory {", declaration_start)
        restored = restored[:declaration_start] + restored[declaration_end:]
        assert restored == source, "transform escaped admission body/class rename"
        profiled = copies[f"Profiled{label}ResearchHybridRetrievalEngine.cpp"].replace(
            f"Profiled{label}ResearchHybridRetrievalEngine", f"{label}ResearchHybridRetrievalEngine"
        ).replace(f"admit_{label.lower()}_profiled", f"admit_{label.lower()}")
        assert strip_instrumentation(profiled) == copies[f"{label}ResearchHybridRetrievalEngine.cpp"]
    linear_profiled = copies["ProfiledLinearResearchHybridRetrievalEngine.cpp"].replace(
        "ProfiledLinearResearchHybridRetrievalEngine", "LinearResearchHybridRetrievalEngine"
    )
    for original, replacement in reversed(LINEAR_PROFILE_INSERTIONS):
        assert linear_profiled.count(replacement) == 1
        linear_profiled = linear_profiled.replace(replacement, original, 1)
    assert strip_instrumentation(linear_profiled) == copies["LinearResearchHybridRetrievalEngine.cpp"]
    for altered_source, altered_header in ((source + "\n", header), (source, header + "\n")):
        try:
            generate(altered_source, altered_header)
        except ValueError:
            pass
        else:
            raise AssertionError("source/header drift was accepted")
    print("production-shaped reranker source generator self-test: PASS; 2 drift controls; exact allowed-region restoration; instrumented/uninstrumented copies")


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
