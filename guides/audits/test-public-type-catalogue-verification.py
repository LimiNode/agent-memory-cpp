"""Focused source-selection controls; real catalogue verification runs separately."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "catalogue", Path(__file__).with_name("extract-public-type-catalogue.py"))
cat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cat)


class SourceVerificationTest(unittest.TestCase):
    def setUp(self):
        modules = ["domain", "chat", "facts", "memory", "embedding", "index", "lexical",
                   "retrieval", "storage", "ingestion", "eval", "infrastructure", "core"]
        self.headers = sorted(f"src/agent_memory/{name}/Fixture.hpp" for name in modules)
        self.original = {path: b"struct Value {};\n" for path in self.headers}
        self.current = dict(self.original)
        types = [{"header": path, "name": "Value", "line": 1, "offset": 7,
                  "range_begin": 0, "range_end": len(self.original[path]),
                  "declaration_sha256": cat.sha(self.original[path]), "kind": "class",
                  "scope": "namespace", "access": "public", "qualified_name": path,
                  "ast_kind": "CXXRecordDecl"} for path in self.headers]
        types[0].update(qualified_name="std::hash<agent_memory::TokenId>",
                        ast_kind="ClassTemplateSpecializationDecl")
        self.catalogue = {"source_commit": cat.BASE,
                          "headers": [{"path": p, "sha256": cat.sha(raw), "bytes": len(raw),
                                       "primary_contract_owner_candidate": "guides/architecture.md"}
                                      for p, raw in self.original.items()],
                          "types": types, "identity_fields": [],
                          "counts": {"headers": 13, "type_declarations": 13, "namespace_types": 13,
                                     "nested_types": 0, "private_or_protected_types": 0,
                                     "identity_fields": 0, "raw_string_identity_fields": 0}}

    def verify(self, catalogue, *, historical_source=False):
        with patch.object(cat, "included_headers", return_value=self.headers), \
                patch.object(cat.subprocess, "check_output",
                             side_effect=lambda command: self.original[command[-1].split(":", 1)[1]]), \
                patch.object(Path, "read_bytes", autospec=True,
                             side_effect=lambda path: self.current[path.relative_to(cat.ROOT).as_posix()]), \
                patch.object(Path, "is_file", return_value=True):
            cat.verify(catalogue, historical_source=historical_source)

    def test_default_strict_accepts_matching_current_source(self):
        self.verify(self.catalogue)

    def test_default_strict_rejects_current_source_drift(self):
        self.current[self.headers[0]] += b"// Later source change\n"
        with self.assertRaisesRegex(AssertionError, "Current source differs from BASE"):
            self.verify(self.catalogue)

    def test_historical_source_does_not_read_current_headers(self):
        self.current.clear()  # Any accidental current-header access raises KeyError.
        self.verify(self.catalogue, historical_source=True)

    def test_both_modes_reject_corrupt_historical_receipt(self):
        mutations = [lambda c: c.update(source_commit="wrong-source"),
                     lambda c: c["headers"].pop(),
                     lambda c: c["headers"][0].update(sha256="wrong-hash"),
                     lambda c: c["headers"][0].update(bytes=0),
                     lambda c: c["types"][0].update(line=2),
                     lambda c: c["types"][0].update(name="WrongAnchor"),
                     lambda c: c["types"][0].update(declaration_sha256="wrong-range-hash"),
                     lambda c: c["counts"].update(type_declarations=0)]
        for historical_source in (False, True):
            for mutation in mutations:
                with self.subTest(historical_source=historical_source, mutation=mutation):
                    changed = copy.deepcopy(self.catalogue)
                    mutation(changed)
                    with self.assertRaises(AssertionError):
                        self.verify(changed, historical_source=historical_source)


if __name__ == "__main__":
    unittest.main()
