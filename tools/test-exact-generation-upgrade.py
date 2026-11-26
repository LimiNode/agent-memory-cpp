#!/usr/bin/env python3
"""Negative controls: requested or forged evidence is not numeric execution."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-exact-generation-upgrade.py")
SPEC = importlib.util.spec_from_file_location("exact_generation_upgrade", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
UPGRADE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPGRADE)


class DependencyAuditTest(unittest.TestCase):
    OLD_TREE = "b949d6822582edc1f445837442b91aabf5093ac6"
    NEW_TREE = "b816ddeccafa0375c15b977e71d4a36f6728f59f"
    OLD_CONTAINERS = "4ab32b5eab3cdfce95d1a6c0f05f566019793ec4"
    NEW_CONTAINERS = "c2a2326273a3d85f29283e2cd34942c7b8d72cbd"
    MERGED_TREE = "f5679c6faf286af65e637301aed320c4e433f918"
    MERGED_CONTAINERS = "b28ce3a969ccde427e699cbb3ece74d35acc08c5"
    OLD_ENTRIES = "\0".join((
        "160000 commit 3f3429661187e4c24a490a0f148fc6bc89042b3d\thnswlib",
        "160000 commit fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f\tlibmdbx",
        f"160000 commit {OLD_CONTAINERS}\tmdbx-containers",
        "160000 commit 009c67807670d16f8984c0534aef0e630e5465a4\tsimdcomp",
        "160000 commit f8745da6ff1ad1e7bab384bd1f9d742439278e99\tzstd",
        "",
    ))
    NEW_ENTRIES = OLD_ENTRIES.replace(OLD_CONTAINERS, NEW_CONTAINERS)
    MERGED_ENTRIES = OLD_ENTRIES.replace(OLD_CONTAINERS, MERGED_CONTAINERS)

    def test_exact_directional_audit(self) -> None:
        self.assertEqual(UPGRADE.audit_dependency_transition(
            self.OLD_TREE, self.NEW_TREE, self.OLD_ENTRIES, self.NEW_ENTRIES
        ), "mdbxc-db-load-private-template-2026-11-20")

    def test_identical_dependencies_remain_supported(self) -> None:
        self.assertEqual(UPGRADE.audit_dependency_transition(
            self.OLD_TREE, self.OLD_TREE, self.OLD_ENTRIES, self.OLD_ENTRIES
        ), "identical-external-trees")

    def test_exact_merged_corrections_audit(self) -> None:
        self.assertEqual(UPGRADE.audit_dependency_transition(
            self.OLD_TREE, self.MERGED_TREE, self.OLD_ENTRIES, self.MERGED_ENTRIES
        ), "mdbxc-335-336-merged-corrections-2026-11-27")

    def test_merged_audit_does_not_admit_other_transitions(self) -> None:
        controls = {
            "wrong_pin": (self.OLD_TREE, self.MERGED_TREE, self.OLD_ENTRIES, self.NEW_ENTRIES),
            "wrong_tree": (self.OLD_TREE, self.NEW_TREE, self.OLD_ENTRIES, self.MERGED_ENTRIES),
            "reverse": (self.MERGED_TREE, self.OLD_TREE, self.MERGED_ENTRIES, self.OLD_ENTRIES),
            "incremental": (self.NEW_TREE, self.MERGED_TREE, self.NEW_ENTRIES, self.MERGED_ENTRIES),
            "changed_libmdbx": (
                self.OLD_TREE, self.MERGED_TREE, self.OLD_ENTRIES,
                self.MERGED_ENTRIES.replace("fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f", "1" * 40)
            ),
            "extra_entry": (
                self.OLD_TREE, self.MERGED_TREE, self.OLD_ENTRIES,
                self.MERGED_ENTRIES + "160000 commit " + "2" * 40 + "\tunaudited\0"
            ),
        }
        for name, values in controls.items():
            with self.subTest(control=name), self.assertRaisesRegex(
                RuntimeError, "no explicit audit admits"
            ):
                UPGRADE.audit_dependency_transition(*values)

    def test_unaudited_transitions_fail_closed(self) -> None:
        changed_libmdbx = self.NEW_ENTRIES.replace(
            "fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f", "1" * 40
        )
        changed_containers = self.NEW_ENTRIES.replace(self.NEW_CONTAINERS, "2" * 40)
        extra_entry = "160000 commit " + "3" * 40 + "\tunaudited\0"
        controls = {
            "libmdbx": (self.OLD_TREE, self.NEW_TREE, self.OLD_ENTRIES, changed_libmdbx),
            "containers": (self.OLD_TREE, self.NEW_TREE, self.OLD_ENTRIES, changed_containers),
            "reversed": (self.NEW_TREE, self.OLD_TREE, self.NEW_ENTRIES, self.OLD_ENTRIES),
            "extra_entry": (
                self.OLD_TREE, self.NEW_TREE, self.OLD_ENTRIES, self.NEW_ENTRIES + extra_entry
            ),
            "unknown_tree": (self.OLD_TREE, "4" * 40, self.OLD_ENTRIES, self.NEW_ENTRIES),
            "forged_identical_tree": (
                self.OLD_TREE, self.OLD_TREE, self.OLD_ENTRIES, changed_libmdbx
            ),
        }
        for name, values in controls.items():
            with self.subTest(control=name), self.assertRaisesRegex(
                RuntimeError, "no explicit audit admits"
            ):
                UPGRADE.audit_dependency_transition(*values)


class UpgradeEvidenceTest(unittest.TestCase):
    def test_numeric_gate_never_promotes_unexecuted_evidence(self) -> None:
        build_root = SCRIPT.resolve().parents[1] / ".build"
        build_root.mkdir(exist_ok=True)
        for control in ("cli_flag", "manifest_text", "forged_evidence"):
            with self.subTest(control=control), tempfile.TemporaryDirectory(
                prefix="upgrade-evidence-", dir=build_root
            ) as temporary:
                root = Path(temporary)
                source = root / "fake-source"
                source.mkdir()
                if control != "cli_flag":
                    (source / "guides").mkdir()
                    (source / "guides/dbi-manifest.yaml").write_text(
                        "knowledge_units_numeric_v1: POST_NUMERIC_UPGRADE\n",
                        encoding="utf-8",
                    )
                if control == "forged_evidence":
                    (source / "receipt.json").write_text(json.dumps({
                        "result": "PASS", "post_migration_upgrade_proof": True,
                        "fresh_process_reopen": True,
                        "logical_row_validation": ["counts", "no_reuse"],
                    }), encoding="utf-8")
                artifacts = root / "artifacts"
                result = subprocess.run([
                    sys.executable, str(SCRIPT), "--post-numeric-upgrade",
                    "--old-source", str(source), "--new-source", str(source),
                    "--old-commit", "1" * 40, "--new-commit", "2" * 40,
                    "--build-root", str(root / "build"),
                    "--artifacts", str(artifacts),
                ], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 3, result.stderr)
                receipt = json.loads((artifacts / "receipt.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["result"], "NOT_EXECUTED")
                self.assertEqual(receipt["evidence_class"], "NOT_EXECUTED")
                self.assertIs(receipt["migration_proof"], False)
                self.assertIs(receipt["post_migration_upgrade_proof"], False)
                self.assertIs(receipt["fresh_process_reopen"], False)
                self.assertEqual(receipt["logical_row_validation"], [])
                self.assertEqual(receipt["processes"], [])
                self.assertEqual(receipt["commands"], [])
                self.assertFalse((root / "build").exists())


if __name__ == "__main__":
    unittest.main()
