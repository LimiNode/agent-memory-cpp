"""Source-bound documentation catalogue; Python 3.10+, no library builds.

Use --verify without a compiler to independently validate committed anchors.
Generation additionally requires Clang's JSON AST and usable C++17 headers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "acd5ad5545386b3feeaa79f8a587855b60801b4d"
OUT = ROOT / "guides/audits/2026-11-public-type-catalogue.json"
LOCAL = ROOT / ".artifacts/domain-identity-audit"
RECORD_KINDS = {"CXXRecordDecl", "RecordDecl", "ClassTemplateSpecializationDecl", "ClassTemplatePartialSpecializationDecl"}
DECL_KINDS = RECORD_KINDS | {"EnumDecl", "TypeAliasDecl", "TypedefDecl"}
KINDS = {"CXXRecordDecl": "class", "RecordDecl": "struct", "EnumDecl": "enum",
         "TypeAliasDecl": "alias", "TypedefDecl": "typedef",
         "ClassTemplateSpecializationDecl": "class_specialization",
         "ClassTemplatePartialSpecializationDecl": "class_partial_specialization"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def included_headers() -> list[str]:
    return [p for p in git("ls-tree", "-r", "--name-only", BASE, "src").splitlines()
            if p.endswith((".hpp", ".h", ".ipp"))
            and not p.endswith("/CanonicalContentStoreInternal.hpp")]


def canonical_path(file: str | None) -> str | None:
    if not file:
        return None
    path = Path(file)
    if not path.is_absolute():
        path = ROOT / path
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def load_objects(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    objects = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        value, position = decoder.raw_decode(text, position)
        objects.append(value)
    return objects


def owner_candidate(header: str) -> str:
    """One proposed primary owner per header; cross-cutting rules still apply."""
    name = Path(header).name
    rules = [
        (("CanonicalFlat",), "canonical-flat-storage-roadmap.md"),
        (("StorageIdentity",), "storage-identity-mapping.md"),
        (("CanonicalNavigation",), "canonical-navigation-roadmap.md"),
        (("CanonicalContent", "Message.hpp"), "canonical-content-storage-roadmap.md"),
        (("PromptComposition", "PromptAssembly"), "prompt-composition-roadmap.md"),
        (("Metadata.hpp", "MetadataFilter.hpp"), "adr/2026-11-typed-metadata-compatibility.md"),
        (("KnowledgeUnit", "Fact.hpp", "SearchProjection", "ProjectionStore"), "knowledge-units-roadmap.md"),
        (("MemoryObject",), "memory-stacks-roadmap.md"),
        (("TypedRepresentation", "Artifact", "RawBlob"), "artifact-provenance-roadmap.md"),
        (("DerivedPublication", "ResourceFrontier"), "linked-derived-knowledge.md"),
        (("Kb0",), "kb0-retrieval-facade.md"),
        (("FederatedCanonical",), "federated-retrieval-roadmap.md"),
        (("SnapshotRevisionBound",), "mdbx-snapshot-revision-bound-retrieval.md"),
        (("RevisionBound", "AuthoritativeArticle"), "kb0-revision-bound-retrieval.md"),
        (("RawMarkdownProduct",), "raw-markdown-product-ingestion.md"),
        (("VerifiedArtifactHydration",), "verified-artifact-hydration.md"),
        (("StructuralSegmentSet",), "structural-segment-set.md"),
        (("ResourceIndexer", "ResourceImporter", "ResourceManifest", "ResourceIndexRecord", "Resource.hpp"), "resource-reindexing.md"),
        (("ProjectionAware", "OriginalProjection"), "projection-aware-lexical-retrieval.md"),
        (("LexicalSegmentEncoding",), "lexical-segment-physical-contract.md"),
        (("SegmentedLexical",), "segmented-lexical-mdbx-adapter.md"),
        (("PersistentLexical",), "persistent-lexical-index-foundation.md"),
        (("AutoencoderBinary",), "multilingual-autoencoder-evaluation.md"),
    ]
    for fragments, guide in rules:
        if any(fragment in name for fragment in fragments):
            return "guides/" + guide
    module = header.split("/")[2] if header.startswith("src/agent_memory/") else "aggregate"
    return "guides/" + {
        "embedding": "embedding.md", "eval": "evaluation-roadmap.md",
        "index": "optimization-roadmap.md", "lexical": "lexical-search-roadmap.md",
        "retrieval": "retrieval-execution-roadmap.md", "ingestion": "resource-reindexing.md",
        "infrastructure": "storage-backend-integration-roadmap.md",
        "storage": "storage-backend-integration-roadmap.md",
    }.get(module, "architecture.md")


def compiler_ast(headers: list[str], clang: str, mingw: Path | None) -> tuple[list[dict], dict]:
    LOCAL.mkdir(parents=True, exist_ok=True)
    unit = LOCAL / "all_public_headers.cpp"
    # Generated translation unit: mechanical artifact, not a product source edit.
    unit.write_text("\n".join(f'#include <{p[4:]}>' for p in headers) + "\n", encoding="utf-8")
    command = [clang, "-std=c++17",
               "-fsyntax-only", "-Wno-pragma-once-outside-header", "-Isrc",
               "-DAGENT_MEMORY_HAS_MDBX=1", "-DAGENT_MEMORY_ENABLE_JSON=1",
               "-DAGENT_MEMORY_ENABLE_ZSTD=1", "-Xclang", "-ast-dump=json",
               "-Xclang", "-ast-dump-filter=agent_memory"]
    if mingw:
        command.append("--target=x86_64-w64-windows-gnu")
        # This reproduces the recorded Windows profile, not arbitrary GCC releases.
        for path in ["lib/gcc/x86_64-w64-mingw32/15.2.0/include/c++",
                     "lib/gcc/x86_64-w64-mingw32/15.2.0/include/c++/x86_64-w64-mingw32",
                     "lib/gcc/x86_64-w64-mingw32/15.2.0/include/c++/backward",
                     "x86_64-w64-mingw32/include"]:
            assert (mingw / path).is_dir(), f"Missing recorded standard headers: {path}"
            command.extend(["-isystem", str(mingw / path)])
    command.append(str(unit))
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    (LOCAL / "clang-diagnostics.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"AST extraction failed ({result.returncode}): {result.stderr[:12000]}")
    (LOCAL / "clang-ast.jsons").write_text(result.stdout, encoding="utf-8")
    # Standard-library specializations are project declarations too; the
    # agent_memory namespace filter alone deliberately cannot see them.
    hash_command = list(command)
    hash_command[hash_command.index("-ast-dump-filter=agent_memory")] = "-ast-dump-filter=std::hash"
    hash_result = subprocess.run(hash_command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if hash_result.returncode:
        raise RuntimeError(hash_result.stderr)
    (LOCAL / "clang-hash-ast.jsons").write_text(hash_result.stdout, encoding="utf-8")
    objects = load_objects(result.stdout)
    objects.append({"kind": "NamespaceDecl", "name": "std", "inner": load_objects(hash_result.stdout)})
    return objects, {
        "tool": subprocess.check_output([clang, "--version"], text=True).splitlines()[0],
        "language": "C++17", "target": "x86_64-w64-windows-gnu" if mingw else "host-default",
        "mode": "syntax-only AST; no objects, library, executable, or CMake build",
        "definitions": {"AGENT_MEMORY_HAS_MDBX": 1, "AGENT_MEMORY_ENABLE_JSON": 1,
                        "AGENT_MEMORY_ENABLE_ZSTD": 1},
        "standard_library": "MinGW GCC 15.2.0 headers" if mingw else "host-default headers",
        "diagnostics_sha256": sha(result.stderr.encode()),
    }


def make_catalogue(objects: list[dict], headers: list[str], extractor: dict) -> dict:
    source = {p: (ROOT / p).read_bytes() for p in headers}
    types = {}
    fields = {}

    def location(node: dict, inherited_file: str | None) -> tuple[str | None, int | None, int | None]:
        loc = node.get("loc", {})
        loc = loc.get("spellingLoc", loc)
        file = canonical_path(loc.get("file")) or inherited_file
        offset = loc.get("offset")
        line = loc.get("line")
        if line is None and file in source and offset is not None:
            line = source[file][:offset].count(b"\n") + 1
        return file, line, offset

    def walk(node: dict, namespace: list[str], records: list[str], inherited_file: str | None,
             inherited_access: str = "public") -> None:
        kind = node.get("kind", "")
        file, line, offset = location(node, inherited_file)
        name = node.get("name", "")
        if node.get("isImplicit"):
            return
        if kind == "NamespaceDecl":
            namespace = namespace + ([name] if name else ["<anonymous>"])
        if kind in DECL_KINDS and file in source and name:
            # Instantiations reuse the primary template's source location.
            # Written specializations have an explicit template-id after name.
            if kind in {"ClassTemplateSpecializationDecl", "ClassTemplatePartialSpecializationDecl"}:
                suffix = source[file][offset + len(name):].lstrip()
                if not suffix.startswith(b"<"):
                    return
            arguments = [c.get("type", {}).get("qualType", "?") for c in node.get("inner", []) if c.get("kind") == "TemplateArgument"]
            display_name = name + ("<" + ",".join(arguments) + ">" if arguments else "")
            qualified = "::".join(namespace + records + [display_name])
            identity = f"{file}:{offset}:{kind}"
            start = node.get("range", {}).get("begin", {}).get("offset", offset or 0)
            end_node = node.get("range", {}).get("end", {})
            end = end_node.get("offset", start) + end_node.get("tokLen", 1)
            declaration = source[file][start:end]
            is_definition = bool(node.get("completeDefinition") or kind == "EnumDecl" and node.get("inner"))
            types[identity] = {
                "qualified_name": qualified, "name": name,
                "kind": node.get("tagUsed", KINDS[kind]), "header": file,
                "ast_kind": kind,
                "line": line, "offset": offset,
                "range_begin": start, "range_end": end,
                "scope": "nested" if records else "namespace",
                "access": inherited_access,
                "api_role": ("enum" if kind == "EnumDecl" else "alias" if kind in {"TypeAliasDecl", "TypedefDecl"}
                             else "interface" if any(c.get("pure") for c in node.get("inner", []))
                             else "options" if name.endswith(("Options", "Config"))
                             else "result" if name.endswith(("Result", "Receipt", "Response"))
                             else "value_or_dto" if node.get("tagUsed") == "struct" else "class_contract"),
                "definition": is_definition if kind in {"CXXRecordDecl", "RecordDecl", "EnumDecl"} else True,
                "type": node.get("type", {}).get("qualType"), "template_arguments": arguments,
                "semantic_owner": file.split("/")[2] if file.startswith("src/agent_memory/") else "aggregate",
                "primary_contract_owner_candidate": owner_candidate(file),
                "owner_status": "RECOMMENDED_REVIEW_PENDING_NOT_NEW_AUTHORITY",
                "declaration_sha256": sha(declaration),
            }
            if kind in RECORD_KINDS:
                records = records + [name]
        if kind in {"FieldDecl", "VarDecl"} and file in source and name and records:
            value_type = node.get("type", {}).get("qualType", "")
            if (re.search(r"(?:^|_)(?:id|key|hash|revision)(?:$|_)", name)
                    or name == "m_value" and records[-1].endswith(("Id", "Key", "Hash"))):
                fields[f"{file}:{offset}:{name}"] = {
                    "qualified_name": "::".join(namespace + records + [name]),
                    "name": name, "owner_type": "::".join(namespace + records),
                    "header": file, "line": line, "offset": offset, "access": inherited_access,
                    "type": value_type, "raw_string": bool(re.search(r"\b(?:std::)?(?:basic_string|string)\b", value_type)),
                    "kind": "identity_candidate_not_semantic_proof",
                }
        access = ("private" if node.get("tagUsed") == "class" else "public") if kind in RECORD_KINDS else inherited_access
        for child in node.get("inner", []):
            if child.get("kind") == "AccessSpecDecl":
                access = child.get("access", access)
                continue
            # Function bodies cannot declare public API types; method-local types
            # are outside the public/nested declaration inventory.
            if child.get("kind") in {"CompoundStmt", "LambdaExpr"}:
                continue
            walk(child, namespace, records, file, access)

    for node in objects:
        walk(node, [], [], None)
    decls = sorted(types.values(), key=lambda v: (v["header"], v["offset"] or 0, v["kind"]))
    identity_fields = sorted(fields.values(), key=lambda v: (v["header"], v["offset"] or 0))
    for field in identity_fields:
        field["decision_ref"] = field_policy(field)
    policies = identity_policies(decls)
    return {
        "schema": "agent-memory.public-type-audit.v1",
        "source_commit": BASE, "status": "descriptive_snapshot_proposals_require_review",
        "extraction": extractor,
        "scope": "Project-owned named type declarations in agent_memory and project std::hash specializations across every installed source header at the source commit, including optional declarations in the declared profile; private nested declarations are labelled, not called public API.",
        "excluded": [{"header": "src/agent_memory/storage/CanonicalContentStoreInternal.hpp", "reason": "Explicitly excluded by CMake install rule; internal implementation header."}],
        "limitations": ["No claim of complete semantic identity inference from names: candidate fields include keys/revisions/hash material and require the decision table.",
                        "Declarations in third-party namespaces, such as forward-declared mdbxc::Connection, are not project-owned public model types and are excluded.",
                        "semantic_owner labels the source module; primary_contract_owner_candidate is a review-pending proposed owner pointer, not proof that its guide specifies every current member.",
                        "Function-local declarations, implicit compiler-generated declarations and template instantiations are not public named type definitions.",
                        "Catalogue covers the stated enabled feature profile; it does not prove arbitrary macro configurations, ABI stability or runtime behavior.",
                        "Forward declarations and namespace/nested/private declarations are explicit; nested class names are not independent namespace API types."],
        "headers": [{"path": p, "sha256": sha(source[p]), "bytes": len(source[p]),
                     "primary_contract_owner_candidate": owner_candidate(p),
                     "module": p.split("/")[2] if p.startswith("src/agent_memory/") else "aggregate"} for p in headers],
        "counts": {"headers": len(headers), "type_declarations": len(decls),
                   "namespace_types": sum(d["scope"] == "namespace" for d in decls),
                   "nested_types": sum(d["scope"] == "nested" for d in decls),
                   "private_or_protected_types": sum(d["access"] != "public" for d in decls),
                   "identity_fields": len(identity_fields),
                   "raw_string_identity_fields": sum(f["raw_string"] and f["access"] == "public" for f in identity_fields)},
        "types": decls, "identity_fields": identity_fields,
        "identity_decisions": policies,
    }


def field_policy(field: dict) -> str:
    name, owner, file = field["name"], field["owner_type"], field["header"]
    if not field["raw_string"]:
        return "TYPED_OR_NUMERIC_MEMBER_REVIEW_IN_OWNER"
    if "MetadataFilter" in owner or "PromptTemplateBinding" in owner:
        return "NON_ID_LOOKUP_KEY_KEEP_STRING"
    if "/eval/" in file:
        return "EVAL_PORTABLE_LABEL_OR_EXACT_DIGEST_TEXT_KEEP_STRING"
    if any(part in owner for part in ("TableColumn", "TableCell", "TableRow", "TableRepresentation", "TableCellLocator")):
        return "REPRESENTATION_LOCAL_COMPOSITE_ID"
    if name in {"external_video_id", "speaker_id"}:
        return "SCOPED_EXTERNAL_ID_REVIEW_REQUIRED"
    if name.endswith(("_hash", "_digest")):
        return "DIGEST_TEXT_REVIEW_ALGORITHM_AND_WIRE_BEFORE_BINARY"
    if name == "key":
        return "OPAQUE_BINARY_OR_TEXT_KEY_REVIEW_REQUIRED"
    if "CanonicalFlat" in owner:
        return "LOGICAL_TEXT_ID_PHYSICAL_NUMERIC_MAPPING"
    return "OPAQUE_DESCRIPTOR_REVISION_OR_RECEIPT_TEXT_KEEP_STRING_REVIEW_NAMESPACE"


def identity_policies(decls: list[dict]) -> list[dict]:
    families = {
        "KnowledgeUnitId": ("KEEP_NUMERIC", "LOCAL_OCCURRENCE"),
        "TokenId": ("KEEP_NUMERIC", "TOKEN_DICTIONARY_LOCAL"),
        "SectionId": ("KEEP_NUMERIC", "RESOURCE_LOCAL_SECTION"),
        "DocumentStorageId": ("KEEP_NUMERIC", "PHYSICAL_DOCUMENT_MAPPING"),
        "BlockStorageId": ("KEEP_NUMERIC", "PHYSICAL_DOCUMENT_BLOCK_MAPPING"),
        "ResourceId": ("KEEP_STRING", "LOGICAL_RESOURCE"),
        "DocumentId": ("KEEP_STRING", "LOGICAL_DOCUMENT"),
        "ChunkId": ("KEEP_STRING", "LOGICAL_DERIVED_CHUNK"),
        "ContentBlockId": ("KEEP_STRING", "DOCUMENT_SCOPED_CONTENT_BLOCK"),
        "SourceRevisionId": ("KEEP_STRING", "PORTABLE_SOURCE_REVISION"),
        "ArtifactId": ("BINARY_DIGEST_CANDIDATE", "CONTENT_ADDRESSED_ARTIFACT"),
        "ArtifactBindingId": ("COMPOSITE_ID", "REVISION_ARTIFACT_RELATIONSHIP"),
        "ScopeId": ("KEEP_STRING", "EXPLICIT_AUTHORIZATION_NAMESPACE"),
        "RepresentationId": ("KEEP_STRING", "VERSIONED_REPRESENTATION"),
        "SegmentSetId": ("KEEP_STRING", "VERSIONED_SEGMENTATION"),
        "SegmentId": ("KEEP_STRING", "REPRESENTATION_SCOPED_SEGMENT"),
        "EvidenceAnchorId": ("KEEP_STRING", "PORTABLE_EVIDENCE_ANCHOR"),
        "MessageId": ("SCOPED_EXTERNAL_ID", "LEGACY_CHAT_ID_REQUIRES_NEW_MODEL"),
        "ThreadId": ("SCOPED_EXTERNAL_ID", "LEGACY_CHAT_ID_REQUIRES_NEW_MODEL"),
        "AuthorId": ("SCOPED_EXTERNAL_ID", "LEGACY_CHAT_ID_REQUIRES_NEW_MODEL"),
        "FactId": ("REVIEW_REQUIRED", "LEGACY_FACT_COMPATIBILITY"),
        "MemoryObjectId": ("REVIEW_REQUIRED", "LEGACY_MEMORY_COMPATIBILITY"),
        "BlobDigest": ("KEEP_BINARY", "BINARY_ALGORITHM_TAGGED_DIGEST"),
        "ContentHash": ("KEEP_BINARY", "BINARY_RECIPE_BOUND_HASH"),
        "ResourceBodyDigest": ("KEEP_BINARY", "BINARY_ALGORITHM_TAGGED_DIGEST"),
        "CanonicalFlatDigest": ("KEEP_BINARY", "BINARY_FLAT_DIGEST"),
        "KnowledgeUnitKey": ("COMPOSITE_ID", "KIND_SCOPE_CONTENT_HASH"),
        "LexicalSegmentPhysicalKey": ("COMPOSITE_ID", "VERSIONED_PHYSICAL_SEGMENT_KEY"),
    }
    result = []
    for declaration in decls:
        name = declaration["name"]
        if declaration["scope"] != "namespace" or name not in families:
            continue
        decision, family = families[name]
        mentions = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "-w", name,
                                   BASE, "--", "src", "tests", "examples", "tools"],
                                  text=True, capture_output=True, check=False)
        assert mentions.returncode in {0, 1}, mentions.stderr
        result.append({"type": declaration["qualified_name"], "source_header": declaration["header"],
                       "source_line": declaration["line"], "decision": decision, "family": family,
                       "status": "RECOMMENDED_NO_REPRESENTATION_CHANGE_IN_THIS_PR",
                       "semantic_contract": "2026-11-domain-identity-public-api-audit.md#per-identity-decision-register",
                       "textual_mention_files_not_call_graph": [p.split(":", 1)[1] for p in mentions.stdout.splitlines()]})
    return result


def verify(catalogue: dict) -> None:
    assert catalogue["source_commit"] == BASE
    headers = included_headers()
    assert [h["path"] for h in catalogue["headers"]] == headers
    for entry in catalogue["headers"]:
        original = subprocess.check_output(["git", "-C", str(ROOT), "show", f'{BASE}:{entry["path"]}'])
        assert sha(original) == entry["sha256"]
        assert original == (ROOT / entry["path"]).read_bytes()
        assert (ROOT / entry["primary_contract_owner_candidate"]).is_file()
    for declaration in catalogue["types"] + catalogue["identity_fields"]:
        raw = (ROOT / declaration["header"]).read_bytes()
        assert declaration["line"] == raw[:declaration["offset"]].count(b"\n") + 1
        assert raw[declaration["offset"]:].decode("utf-8").startswith(declaration["name"])
        if "declaration_sha256" in declaration:
            assert sha(raw[declaration["range_begin"]:declaration["range_end"]]) == declaration["declaration_sha256"]
    assert len({(t["header"], t["offset"], t["kind"]) for t in catalogue["types"]}) == len(catalogue["types"])
    types, fields = catalogue["types"], catalogue["identity_fields"]
    assert catalogue["counts"] == {
        "headers": len(headers), "type_declarations": len(types),
        "namespace_types": sum(d["scope"] == "namespace" for d in types),
        "nested_types": sum(d["scope"] == "nested" for d in types),
        "private_or_protected_types": sum(d["access"] != "public" for d in types),
        "identity_fields": len(fields),
        "raw_string_identity_fields": sum(f["raw_string"] and f["access"] == "public" for f in fields),
    }
    assert all(t["scope"] == "nested" for t in types if t["access"] != "public")
    assert any(t["qualified_name"] == "std::hash<agent_memory::TokenId>"
               and t["ast_kind"] == "ClassTemplateSpecializationDecl" for t in types)
    assert all(any(t["header"].startswith(f"src/agent_memory/{module}/") for t in catalogue["types"]) for module in
               ["domain", "chat", "facts", "memory", "embedding", "index", "lexical", "retrieval", "storage", "ingestion", "eval", "infrastructure", "core"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Verify Git hashes, coverage, anchors and counts; no compiler")
    parser.add_argument("--clang", default=shutil.which("clang++"), help="Clang executable for generation")
    parser.add_argument("--mingw-root", type=Path, help="Optional recorded GCC 15.2.0 MinGW root for Windows")
    args = parser.parse_args()
    if args.verify:
        catalogue = json.loads(OUT.read_text(encoding="utf-8"))
        verify(catalogue)
        print(json.dumps({"verified": True, **catalogue["counts"]}))
    else:
        if not args.clang:
            parser.error("Generation requires --clang when clang++ is not in PATH")
        for header in included_headers():
            assert subprocess.check_output(["git", "-C", str(ROOT), "show", f"{BASE}:{header}"]) == (ROOT / header).read_bytes(), f"Modified source header: {header}"
        ast, extractor = compiler_ast(included_headers(), args.clang, args.mingw_root)
        catalogue = make_catalogue(ast, included_headers(), extractor)
        verify(catalogue)
        # Compact rows keep the requested complete documentary catalogue small.
        blocks = []
        for key, value in catalogue.items():
            if isinstance(value, list) and key in {"headers", "types", "identity_fields", "identity_decisions"}:
                encoded = "[\n" + ",\n".join("    " + json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in value) + "\n  ]"
            else:
                encoded = json.dumps(value, ensure_ascii=False, indent=2)
            blocks.append("  " + json.dumps(key) + ": " + encoded)
        OUT.write_text("{\n" + ",\n".join(blocks) + "\n}\n", encoding="utf-8")
        print(json.dumps(catalogue["counts"]))
