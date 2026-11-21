#!/usr/bin/env python3
"""Negative controls: requested or forged evidence is not numeric execution."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-exact-generation-upgrade.py")


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
                self.assertIs(receipt["post_migration_upgrade_proof"], False)
                self.assertIs(receipt["fresh_process_reopen"], False)
                self.assertEqual(receipt["logical_row_validation"], [])
                self.assertEqual(receipt["processes"], [])
                self.assertEqual(receipt["commands"], [])
                self.assertFalse((root / "build").exists())


if __name__ == "__main__":
    unittest.main()
