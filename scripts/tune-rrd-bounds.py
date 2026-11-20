#!/usr/bin/env python3
"""Preview or apply bounded RRD datasources; preserve existing samples.

Uses explicit catalog bounds and interface speed from Nagios status.dat.
Unspecified limits remain unchanged. Default is read-only dry-run.
"""
import argparse
import importlib.util
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rrd_import", HERE / "nagios-rrd-import.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
spec = importlib.util.spec_from_file_location("graphs_config", HERE / "nagios_graphs_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


def status_speeds(path):
    speeds = {}
    if not path.exists():
        return speeds
    block = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line == "servicestatus {":
            block = {}
        elif line == "}":
            host, service = block.get("host_name"), block.get("service_description")
            if host and service:
                perf = collector.parse_perfdata(block.get("performance_data", ""))
                speeds[(host, service)] = perf
            block = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            if key in ("host_name", "service_description", "performance_data"):
                block[key] = value
    return speeds


def plan(catalog, root, perf, interface_faults_only=False):
    changes = []
    seen = set()
    for metric in catalog["metrics"].values():
        if interface_faults_only and not (metric["type"] == "COUNTER" and
                re.search(r"_(?:in|out)_(?:error|discard)$", metric["metric"])):
            continue
        name = config.archive_filename(metric)
        if name in seen:
            continue
        seen.add(name)
        path = root / name
        if not path.is_file() or path.is_symlink():
            continue
        values = perf.get((metric["host"], metric["service"]), {})
        low, high = collector.metric_bounds(metric, values)
        if low is None and high is None:
            continue
        result = subprocess.run(["rrdtool", "info", str(path)], capture_output=True, text=True, check=True)
        current = {}
        for field in ("min", "max"):
            match = re.search("^ds" + re.escape("[value].") + field + r" = (\S+)$", result.stdout, re.M)
            if not match:
                raise ValueError("Missing RRD datasource: " + str(path))
            current[field] = float(match.group(1))
        target_low = low if low is not None else current["min"]
        target_high = high if high is not None else current["max"]
        if math.isfinite(target_low) and math.isfinite(target_high) and target_low >= target_high:
            raise ValueError("Invalid bounds for " + name)
        def same(a, b):
            return (math.isnan(a) and math.isnan(b)) or a == b
        if same(target_low, current["min"]) and same(target_high, current["max"]):
            continue
        changes.append((path, target_low, target_high))
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("/var/lib/nagios-graphs/catalog.json"))
    parser.add_argument("--rrd-dir", type=Path, default=Path("/var/lib/nagios-graphs/rrd"))
    parser.add_argument("--status", type=Path, default=Path("/var/lib/nagios4/status.dat"))
    parser.add_argument("--interface-faults-only", action="store_true",
                        help="only tune interface error/discard counter limits; preserve samples")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error("--apply requires --backup-dir")
    if args.apply and args.backup_dir.exists():
        parser.error("backup directory must not already exist")
    data = json.loads(args.catalog.read_text())
    validated = config.validate(data)
    changes = plan(validated, args.rrd_dir, status_speeds(args.status), args.interface_faults_only)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run",
                      "changes": len(changes),
                      "first_ten": [{"file": p.name, "min": lo, "max": hi}
                                    for p, lo, hi in changes[:10]]}, indent=2))
    if not args.apply:
        return
    args.backup_dir.mkdir(parents=True, mode=0o700)
    for path, lo, hi in changes:
        shutil.copy2(path, args.backup_dir / path.name)
    for path, lo, hi in changes:
        cmd = ["rrdtool", "tune", str(path)]
        cmd.extend(["--minimum", "value:" + ("U" if math.isnan(lo) else format(lo, ".12g"))])
        cmd.extend(["--maximum", "value:" + ("U" if math.isnan(hi) else format(hi, ".12g"))])
        subprocess.run(cmd, check=True)
    print("Updated:", len(changes), "Backup:", args.backup_dir)


if __name__ == "__main__":
    main()
