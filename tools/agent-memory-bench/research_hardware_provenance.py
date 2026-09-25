"""Portable hardware provenance for offline research benchmarks."""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path


def _windows_cpu() -> tuple[str, int | None, int | None, str]:
    model = ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            model = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except (ImportError, OSError):
        pass

    physical = None
    logical = None
    source = "windows_registry+os.cpu_count"
    command = (
        "@(Get-CimInstance Win32_Processor | "
        "Select-Object Name,NumberOfCores,NumberOfLogicalProcessors) | "
        "ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        parsed = json.loads(completed.stdout)
        processors = parsed if isinstance(parsed, list) else [parsed]
        physical = sum(int(item["NumberOfCores"]) for item in processors)
        logical = sum(int(item["NumberOfLogicalProcessors"]) for item in processors)
        if not model and processors:
            model = str(processors[0].get("Name", "")).strip()
        source = "windows_cim"
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    return model, physical, logical, source


def _linux_cpu() -> tuple[str, int | None, int | None, str]:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.is_file():
        return "", None, None, "platform"
    model = ""
    packages_and_cores: set[tuple[str, str]] = set()
    current: dict[str, str] = {}
    for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines() + [""]:
        if not line.strip():
            if current:
                if not model:
                    model = current.get("model name", current.get("hardware", "")).strip()
                if "physical id" in current and "core id" in current:
                    packages_and_cores.add((current["physical id"], current["core id"]))
                current = {}
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current[key.strip().lower()] = value.strip()
    physical = len(packages_and_cores) or None
    return model, physical, os.cpu_count(), "linux_proc_cpuinfo"


def _darwin_physical_cores() -> int | None:
    try:
        completed = subprocess.run(
            ["sysctl", "-n", "hw.physicalcpu"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(completed.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        return None


def hardware_snapshot() -> dict[str, object]:
    system = platform.system()
    model = platform.processor().strip()
    physical = None
    logical = os.cpu_count()
    source = "platform+os.cpu_count"
    if system == "Windows":
        detected_model, physical, detected_logical, source = _windows_cpu()
        model = detected_model or model or os.environ.get("PROCESSOR_IDENTIFIER", "").strip()
        logical = detected_logical or logical
    elif system == "Linux":
        detected_model, physical, detected_logical, source = _linux_cpu()
        model = detected_model or model
        logical = detected_logical or logical
    elif system == "Darwin":
        physical = _darwin_physical_cores()
        source = "darwin_sysctl+os.cpu_count"

    if not model:
        model = platform.machine().strip()
    if physical is None:
        try:
            import psutil

            physical = psutil.cpu_count(logical=False)
            source += "+psutil"
        except (ImportError, OSError):
            pass
    if not model or not isinstance(logical, int) or logical <= 0:
        raise RuntimeError("CPU model and logical core count must be discoverable")
    if not isinstance(physical, int) or physical <= 0 or physical > logical:
        raise RuntimeError("physical CPU core count must be discoverable and consistent")
    return {
        "cpu_model": model,
        "cpu_physical_cores": physical,
        "cpu_logical_cores": logical,
        "cpu_architecture": platform.machine(),
        "operating_system": system,
        "platform": platform.platform(),
        "detection_source": source,
    }


if __name__ == "__main__":
    print(json.dumps(hardware_snapshot(), indent=2, sort_keys=True))

