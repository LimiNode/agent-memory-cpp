#!/usr/bin/env python3
"""Fail closed unless the task-aware static protocol authorizes learned routing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
UPSTREAM_CONTRACT_SHA256 = "62224c26f930512a26db2575eb576953f532af9988b8978b48c6be15b3cae480"
PREDICATE = "task_aware_static_strictly_beats_both_comparators_on_e5_survival_within_its_candidate_and_fresh_latency_budgets_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    require(contract.get("schema_version") == 1 and contract.get("family") == "learned_locator_protocol_v1", "learned locator contract identity differs")
    require(contract.get("upstream_task_aware_contract_sha256") == UPSTREAM_CONTRACT_SHA256, "learned locator upstream contract differs")
    guard = contract.get("execution_guard")
    require(guard == {"required_permission_family": "task_aware_static_locator_permission_v1", "required_predicate": PREDICATE, "required_field": "learned_locator_permission", "required_value": True}, "learned locator execution guard differs")
    return contract


def verify(permission_path: Path, contract_path: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    permission = json.loads(permission_path.read_text(encoding="utf-8"))
    require(permission.get("schema_version") == 1 and permission.get("family") == "task_aware_static_locator_permission_v1", "task-aware permission artifact identity differs")
    require(permission.get("task_aware_contract_sha256") == contract["upstream_task_aware_contract_sha256"], "task-aware permission contract differs")
    require(permission.get("permission_predicate") == PREDICATE and permission.get("learned_locator_permission") is True, "task-aware static result does not authorize learned locator execution")
    return {"schema_version": 1, "family": "learned_locator_execution_permission_receipt_v1", "learned_locator_contract_sha256": sha256(contract_path), "task_aware_permission_sha256": sha256(permission_path), "authorized": True}


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        contract = THIS / "learned-locator.example.json"
        allowed = root / "allowed.json"
        allowed.write_text(json.dumps({"schema_version": 1, "family": "task_aware_static_locator_permission_v1", "task_aware_contract_sha256": UPSTREAM_CONTRACT_SHA256, "permission_predicate": PREDICATE, "learned_locator_permission": True}), encoding="utf-8")
        require(verify(allowed, contract)["authorized"] is True, "learned locator authorization self-test differs")
        denied = root / "denied.json"
        denied.write_text(json.dumps({"schema_version": 1, "family": "task_aware_static_locator_permission_v1", "task_aware_contract_sha256": UPSTREAM_CONTRACT_SHA256, "permission_predicate": PREDICATE, "learned_locator_permission": False}), encoding="utf-8")
        try:
            verify(denied, contract)
        except ValueError:
            return
        raise ValueError("denied task-aware permission was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "learned-locator.example.json")
    parser.add_argument("--task-aware-permission", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print("learned locator permission guard self-test passed")
            return 0
        if args.task_aware_permission is None:
            parser.error("--task-aware-permission is required")
        print(json.dumps(verify(args.task_aware_permission, args.contract), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"verify-learned-locator-permission: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
