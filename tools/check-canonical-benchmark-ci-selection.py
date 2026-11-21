#!/usr/bin/env python3
"""Fail closed when CI registration or selection of quick canonical gates drifts."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest


PREFIX = "agent_memory_mdbx_canonical_flat_benchmark_"
REQUIRED = {PREFIX + suffix for suffix in (
    "self_test", "receipt_replay", "retained_receipt_replay",
    "validator_self_test", "launch_controls",
)}


def tests_by_name(document: dict) -> dict[str, dict]:
    tests = document.get("tests")
    if not isinstance(tests, list):
        raise ValueError("CTest inventory must contain a tests array")
    result = {}
    for test in tests:
        name = test.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ValueError("CTest names must be nonempty and unique")
        result[name] = test
    return result


def check_selection(inventory: dict, selection: dict) -> None:
    registered = tests_by_name(inventory)
    selected = tests_by_name(selection)
    family = {name for name in registered if name.startswith(PREFIX)}
    # An added/renamed family member needs an explicit quick/full classification.
    # Do not automatically execute an unknown, potentially full benchmark in CI.
    if family != REQUIRED:
        raise ValueError(f"canonical registration drift: missing={sorted(REQUIRED - family)}, "
                         f"unreviewed={sorted(family - REQUIRED)}")
    if set(selected) != REQUIRED:
        raise ValueError(f"canonical selection drift: missing={sorted(REQUIRED - selected.keys())}, "
                         f"unexpected={sorted(selected.keys() - REQUIRED)}")
    for name in REQUIRED:
        for test in (registered[name], selected[name]):
            if any(prop.get("name") == "DISABLED" and prop.get("value")
                   for prop in test.get("properties", [])):
                raise ValueError(f"canonical gate is disabled: {name}")
            if not test.get("command"):
                raise ValueError(f"canonical gate has no executable command: {name}")
    if "--self-test" not in registered[PREFIX + "self_test"]["command"]:
        raise ValueError("canonical smoke must use --self-test, never the full benchmark")


class SelectionControls(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = {"tests": [{"name": name, "command": ["fixture", "--self-test"]}
                                    for name in sorted(REQUIRED)]}
        self.selection = copy.deepcopy(self.inventory)

    def test_complete_selection(self) -> None:
        self.inventory["tests"].append({"name": "unrelated_test"})
        check_selection(self.inventory, self.selection)

    def test_omitted_gate(self) -> None:
        self.selection["tests"].pop()
        with self.assertRaisesRegex(ValueError, "selection drift"):
            check_selection(self.inventory, self.selection)

    def test_new_gate_omitted_by_old_filter(self) -> None:
        self.inventory["tests"].append({"name": PREFIX + "new_quick_gate"})
        with self.assertRaisesRegex(ValueError, "registration drift"):
            check_selection(self.inventory, self.selection)

    def test_renamed_gate_omitted_by_old_filter(self) -> None:
        for name in (PREFIX + "renamed_gate", "renamed_canonical_gate"):
            with self.subTest(name=name):
                renamed = copy.deepcopy(self.inventory)
                renamed["tests"][0]["name"] = name
                with self.assertRaisesRegex(ValueError, "registration drift"):
                    check_selection(renamed, self.selection)

    def test_full_or_unrelated_test_is_not_admitted(self) -> None:
        self.selection["tests"].append({"name": "full_canonical_benchmark"})
        with self.assertRaisesRegex(ValueError, "selection drift"):
            check_selection(self.inventory, self.selection)

    def test_full_benchmark_command_is_rejected(self) -> None:
        next(test for test in self.inventory["tests"]
             if test["name"] == PREFIX + "self_test")["command"] = ["benchmark"]
        with self.assertRaisesRegex(ValueError, "never the full benchmark"):
            check_selection(self.inventory, self.selection)

    def test_disabled_or_unbuilt_gate_is_rejected(self) -> None:
        for mutation in ({"command": []},
                         {"properties": [{"name": "DISABLED", "value": True}]}):
            with self.subTest(mutation=mutation):
                inventory = copy.deepcopy(self.inventory)
                inventory["tests"][0].update(mutation)
                with self.assertRaises(ValueError):
                    check_selection(inventory, self.selection)

    def test_missing_or_duplicate_inventory_is_rejected(self) -> None:
        for inventory in ({}, {"tests": []},
                          {"tests": self.inventory["tests"] * 2}):
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                check_selection(inventory, self.selection)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--config", default="Release")
    parser.add_argument("--test-regex", default="^" + PREFIX)
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SelectionControls)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.build_dir is None:
        parser.error("provide --build-dir or --self-test")
    command = ["ctest", "--test-dir", str(args.build_dir), "--build-config", args.config,
               "--show-only=json-v1"]
    try:
        documents = []
        for suffix, arguments in (("inventory", []), ("selection", ["-R", args.test_regex])):
            result = subprocess.run(command + arguments, text=True, capture_output=True,
                                    timeout=60, check=False)
            # Preserve the actual CTest output even if selection/validation fails.
            (args.build_dir / f"canonical-benchmark-{suffix}.json").write_text(
                result.stdout, encoding="utf-8")
            result.check_returncode()
            documents.append(json.loads(result.stdout))
        check_selection(*documents)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"canonical quick gates selected: {len(REQUIRED)}/{len(REQUIRED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
