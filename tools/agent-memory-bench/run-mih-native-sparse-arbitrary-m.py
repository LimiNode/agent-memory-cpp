#!/usr/bin/env python3
"""Run the predeclared native sparse arbitrary-m MIH latency matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "mih_native_sparse_arbitrary_m_matrix_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source_files() -> dict[str, str]:
    names = (Path(__file__).name, "mih-native-sparse-arbitrary-matrix.example.json", "materialize-mih-storage-input.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS / name) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "native sparse arbitrary-m contract identity differs")
    require(value.get("training_materialization_manifest_sha256") == "fb5af79a70a8f61e27c9615c178203599ed5dc10f287d0741d132d97f0218856", "native sparse arbitrary-m training root differs")
    require(value.get("evaluation_materialization_manifest_sha256") == "cd1987fdef63f5f6b4fd595d312648ea58f85aa502ed982958ebf02e99290e86", "native sparse arbitrary-m evaluation root differs")
    require(value.get("itq_seed") == 52 and value.get("itq_iterations") == 50, "native sparse arbitrary-m ITQ contract differs")
    require(value.get("m_values") == list(range(15, 22)), "native sparse arbitrary-m m matrix differs")
    require(value.get("schedule_rule") == {"name": "near_equal_width_minimum_enumerated_keys", "coverage": "sum_local_radius_plus_one_equals_57", "tie_break": "lexicographically_maximum_radius_vector_descending_widths"}, "native sparse arbitrary-m schedule contract differs")
    require(value.get("query_count") == 1252 and value.get("query_seed") == 20260815 and value.get("warmup_count") == 1 and value.get("repeat_count") == 7, "native sparse arbitrary-m timing contract differs")
    require(value.get("hamming_limit") == 768 and value.get("adc_limit") == 256 and value.get("exact_limit") == 256, "native sparse arbitrary-m cascade contract differs")
    require(value.get("decision_rule") == {"scope": "native_latency_frontier_for_fixed_itq_seed", "production_selection": "forbidden", "next_step": "calibration_only_native_cost_selection"}, "native sparse arbitrary-m decision contract differs")
    return value


def near_equal_widths(code_bits: int, band_count: int) -> list[int]:
    base, extra = divmod(code_bits, band_count)
    return [base + 1] * extra + [base] * (band_count - extra)


def local_key_count(width: int, radius: int) -> int:
    return sum(math.comb(width, depth) for depth in range(radius + 1))


def minimum_probe_radii(widths: list[int], global_radius: int = 56) -> list[int]:
    target = global_radius + 1 - len(widths)
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for width in widths:
        next_states: dict[int, tuple[int, tuple[int, ...]]] = {}
        for accumulated, current in states.items():
            for radius in range(width + 1):
                total = accumulated + radius
                if total > target:
                    break
                candidate = (current[0] + local_key_count(width, radius), current[1] + (radius,))
                incumbent = next_states.get(total)
                if incumbent is None or candidate[0] < incumbent[0] or (candidate[0] == incumbent[0] and candidate[1] > incumbent[1]):
                    next_states[total] = candidate
        states = next_states
    require(target in states, "native sparse arbitrary-m schedule cannot meet exact coverage")
    radii = list(states[target][1])
    require(sum(radius + 1 for radius in radii) == global_radius + 1, "native sparse arbitrary-m schedule coverage differs")
    return radii


def treatment(contract: dict[str, Any], band_count: int) -> dict[str, Any]:
    widths = near_equal_widths(256, band_count)
    radii = minimum_probe_radii(widths)
    return {"id": f"m{band_count}-minimum-probe-r56", "band_count": band_count, "widths": widths, "local_radii": radii, "local_key_count": sum(local_key_count(width, radius) for width, radius in zip(widths, radii))}


def materialize(contract: dict[str, Any], calibration_root: Path, evaluation_root: Path, output: Path, python: Path) -> dict[str, Any]:
    command = [str(python), str(THIS / "materialize-mih-storage-input.py"), "materialize", "--calibration-root", str(calibration_root), "--evaluation-root", str(evaluation_root), "--output", str(output), "--code-bits", "256", "--seed", str(contract["itq_seed"]), "--itq-iterations", str(contract["itq_iterations"])]
    subprocess.run(command, check=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("family") == "mih_storage_benchmark_input_v1" and manifest.get("code_bits") == 256 and manifest.get("seed") == contract["itq_seed"] and manifest.get("itq_iterations") == contract["itq_iterations"], "native sparse arbitrary-m materialized input differs")
    require(manifest.get("calibration_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] and manifest.get("evaluation_materialization_manifest_sha256") == contract["evaluation_materialization_manifest_sha256"], "native sparse arbitrary-m materialization provenance differs")
    return manifest


def config_for(contract: dict[str, Any], input_root: Path, value: dict[str, Any]) -> dict[str, Any]:
    return {"input_directory": str(input_root), "band_widths": value["widths"], "local_radii": value["local_radii"], "query_count": contract["query_count"], "query_seed": contract["query_seed"], "warmup_count": contract["warmup_count"], "repeat_count": contract["repeat_count"], "hamming_limit": contract["hamming_limit"], "adc_limit": contract["adc_limit"], "exact_limit": contract["exact_limit"]}


def complete(report: dict[str, Any], config: dict[str, Any], input_manifest_sha256: str) -> bool:
    return report.get("schema_version") == 1 and report.get("family") == "mih_native_sparse_arbitrary_m_v1" and report.get("input_manifest_sha256") == input_manifest_sha256 and report.get("band_widths") == config["band_widths"] and report.get("local_radii") == config["local_radii"] and report.get("query_count") == config["query_count"] and report.get("query_seed") == config["query_seed"] and report.get("warmup_count") == config["warmup_count"] and report.get("repeat_count") == config["repeat_count"] and report.get("hamming_limit") == config["hamming_limit"] and report.get("adc_limit") == config["adc_limit"] and report.get("exact_limit") == config["exact_limit"] and report.get("conformance") == {"candidate_union_fixed_r56_checked": True, "hamming_shortlist_checked": True, "checked_query_count": config["query_count"]}


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    args.output_root.mkdir(parents=True, exist_ok=True)
    input_root = args.output_root / "input"
    input_manifest = materialize(contract, args.calibration_root, args.evaluation_root, input_root, args.python)
    input_manifest_sha256 = sha256(input_root / "manifest.json")
    rows: list[dict[str, Any]] = []
    for number, band_count in enumerate(contract["m_values"], start=1):
        current = treatment(contract, band_count)
        config = config_for(contract, input_root, current)
        config_path = args.output_root / "configs" / f"{current['id']}.json"
        report_path = args.output_root / "reports" / f"{current['id']}.json"
        config_path.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"[{number}/{len(contract['m_values'])}] native {current['id']}", flush=True)
        subprocess.run([str(args.executable), str(config_path), str(report_path)], check=True)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(complete(report, config, input_manifest_sha256), f"native sparse arbitrary-m report differs: {current['id']}")
        rows.append({"id": current["id"], "band_count": band_count, "local_key_count": current["local_key_count"], "config_sha256": sha256(config_path), "report_sha256": sha256(report_path)})
    files = source_files()
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "input_manifest_sha256": input_manifest_sha256, "input_files_sha256": {name: sha256(input_root / name) for name in (input_manifest["document_codes_file"], input_manifest["query_codes_file"], input_manifest["document_vectors_file"], input_manifest["query_vectors_file"], input_manifest["query_itq_projections_file"], input_manifest["binary_adc_centroids_file"])}, "source_files_sha256": files, "source_bundle_sha256": source_bundle(files), "rows": rows}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        contract = load_contract(THIS / "mih-native-sparse-arbitrary-matrix.example.json")
        values = [treatment(contract, item) for item in contract["m_values"]]
        require([value["local_key_count"] for value in values] == [10488, 7232, 4803, 3060, 1874, 1554, 1267], "native sparse arbitrary-m schedule differs")
        require(values[0]["widths"] == [18] + [17] * 14 and values[-1]["local_radii"] == [1, 1, 1, 1] + [2] * 15 + [1, 1], "native sparse arbitrary-m schedule geometry differs")
        require(source_bundle(source_files()) == source_bundle(dict(reversed(source_files().items()))), "native sparse arbitrary-m source bundle is not canonical")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-mih-native-sparse-arbitrary-m self-test failed: {error}", file=sys.stderr)
        return 1
    print("run-mih-native-sparse-arbitrary-m self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    command = commands.add_parser("run")
    command.add_argument("--executable", type=Path, required=True)
    command.add_argument("--python", type=Path, default=Path(sys.executable))
    command.add_argument("--contract", type=Path, default=THIS / "mih-native-sparse-arbitrary-matrix.example.json")
    command.add_argument("--calibration-root", type=Path, required=True)
    command.add_argument("--evaluation-root", type=Path, required=True)
    command.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        run(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-native-sparse-arbitrary-m: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
