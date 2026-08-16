#!/usr/bin/env python3
"""Preflight exact-r56 MIH probe budgets before a scale-aware calibration run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "scale_aware_native_mih_protocol_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def near_equal_widths(code_bits: int, band_count: int) -> list[int]:
    base, extra = divmod(code_bits, band_count)
    return [base + 1] * extra + [base] * (band_count - extra)


def local_key_count(width: int, radius: int) -> int:
    return sum(math.comb(width, distance) for distance in range(radius + 1))


def minimum_probe_radii(widths: list[int], global_radius: int = 56) -> list[int]:
    target = global_radius + 1 - len(widths)
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for width in widths:
        updated: dict[int, tuple[int, tuple[int, ...]]] = {}
        for accumulated, current in states.items():
            for radius in range(width + 1):
                total = accumulated + radius
                if total > target:
                    break
                candidate = (current[0] + local_key_count(width, radius), current[1] + (radius,))
                incumbent = updated.get(total)
                if incumbent is None or candidate[0] < incumbent[0] or (candidate[0] == incumbent[0] and candidate[1] > incumbent[1]):
                    updated[total] = candidate
        states = updated
    require(target in states, "exact-r56 schedule cannot meet coverage")
    radii = list(states[target][1])
    require(sum(radius + 1 for radius in radii) == global_radius + 1, "exact-r56 schedule coverage differs")
    return radii


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "scale-aware protocol identity differs")
    require(value.get("purpose") == "calibration_only_scale_specific_m_and_hnsw_selection", "scale-aware protocol purpose differs")
    require(value.get("representation", {}).get("code_bits") == 256, "scale-aware protocol code width differs")
    require(value.get("fixed_radius_contract") == {"global_hamming_radius": 56, "coverage": "sum_local_radius_plus_one_equals_57", "schedule": "near_equal_width_minimum_enumerated_keys"}, "scale-aware exact-r56 contract differs")
    scales = value.get("scales")
    require(isinstance(scales, list) and [item.get("documents") for item in scales] == [25000, 100000, 1000000], "scale-aware scale order differs")
    require(scales[0].get("mih_m_values") == list(range(15, 22)), "scale-aware 25k m grid differs")
    require(scales[1].get("mih_m_values") == list(range(13, 20)), "scale-aware 100k m grid differs")
    require(scales[2].get("mih_m_values") == list(range(10, 17)), "scale-aware 1m m grid differs")
    require(value.get("native_implementation_matrix") == [{"directory_mode": "sorted_lower_bound", "deduplication_mode": "two_pass_generation_array"}, {"directory_mode": "flat_open_address", "deduplication_mode": "streaming_generation_array"}], "scale-aware native implementation matrix differs")
    return value


def preflight(contract: dict[str, Any], contract_sha256: str) -> dict[str, Any]:
    radius = contract["fixed_radius_contract"]["global_hamming_radius"]
    rows: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        for band_count in scale["mih_m_values"]:
            widths = near_equal_widths(contract["representation"]["code_bits"], band_count)
            radii = minimum_probe_radii(widths, radius)
            keys = sum(local_key_count(width, local_radius) for width, local_radius in zip(widths, radii))
            allowed = keys <= scale["maximum_exact_local_keys"]
            rows.append({
                "scale_id": scale["id"],
                "document_count": scale["documents"],
                "m": band_count,
                "band_widths": widths,
                "local_radii": radii,
                "exact_local_key_count": keys,
                "maximum_exact_local_keys": scale["maximum_exact_local_keys"],
                "status": "admissible_for_native_matrix" if allowed else "excluded_before_execution",
                "reason": "within_predeclared_exact_probe_budget" if allowed else "exceeds_predeclared_exact_probe_budget",
            })
    return {"schema_version": 1, "family": "scale_aware_native_mih_preflight_v1", "contract_sha256": contract_sha256, "fixed_radius_exact_inclusion": "sum_local_radius_plus_one_equals_57", "rows": rows}


def self_test() -> int:
    try:
        path = THIS / "scale-aware-native-mih-protocol.example.json"
        report = preflight(load_contract(path), digest(path))
        rows = {(item["scale_id"], item["m"]): item for item in report["rows"]}
        require(rows[("es-1m", 10)]["status"] == "excluded_before_execution", "m10 must be feasibility-excluded")
        require(rows[("es-1m", 11)]["status"] == "excluded_before_execution", "m11 must be feasibility-excluded")
        require(rows[("es-1m", 12)]["exact_local_key_count"] == 74867, "m12 exact probe count differs")
        require(rows[("es-1m", 13)]["local_radii"] == [4] + [3] * 8 + [4] * 4, "m13 exact schedule differs")
        require(all(sum(radius + 1 for radius in row["local_radii"]) == 57 for row in report["rows"]), "preflight exact coverage differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"preflight-scale-aware-native-mih self-test failed: {error}", file=sys.stderr)
        return 1
    print("preflight-scale-aware-native-mih self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "scale-aware-native-mih-protocol.example.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.output is None:
        parser.error("--output is required unless --self-test is used")
    try:
        contract = load_contract(args.contract)
        report = preflight(contract, digest(args.contract))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"preflight-scale-aware-native-mih: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
