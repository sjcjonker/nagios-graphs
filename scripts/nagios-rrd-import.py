#!/usr/bin/env python3
# Import a sealed Nagios perfdata batch into bounded RRD archives.
# Author: Stijn Jonker (sjcjonker) | https://github.com/sjcjonker
# Usage: python3 scripts/nagios-rrd-import.py /path/to/sealed-batch --rrd-dir /var/lib/nagios-graphs/rrd
# Requires RRDTool; input is epoch, host, service and perfdata separated by tabs. Help: --help.
# Nagios exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""Import selected Nagios service perfdata into bounded RRD files.

Input is a sealed text batch with tab-separated epoch, host, service and
Nagios plugin performance data. The sender must rotate the batch atomically.
This program never reads secrets or changes Nagios configuration.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
from pathlib import Path


def configured_catalog():
    import runpy
    global _catalog_module
    if _catalog_module is None:
        source = Path(__file__).resolve().with_name("nagios_graphs_config.py")
        _catalog_module = runpy.run_path(str(source))
    _catalog_module["load"]()
    return _catalog_module


_catalog_module = None

# check_snmp.pl emits load metrics separated by ';' without whitespace.
# Do not absorb threshold/separator fields into the following metric label.
METRIC = re.compile(r"(?:'([^']+)'|([^\s=;]+))=([^\s;]+)")
DEFAULT_STEP = 60
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_LINE_LENGTH = 16384


def parse_perfdata(value: str) -> dict[str, float | None]:
    result = {}
    for match in METRIC.finditer(value):
        name = match.group(1) or match.group(2)
        if match.group(3) == 'U':
            result[name] = None
            continue
        number = re.match(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", match.group(3))
        if not number:
            continue
        measured = float(number.group())
        if math.isfinite(measured):
            result[name] = measured
    return result


def classify(host: str, service: str, key: str) -> str | None:
    metric = configured_catalog()["metric_for"](host, service, key)
    return metric["type"] if metric else None


def identifier(host: str, service: str, key: str) -> str:
    catalog = configured_catalog()
    metric = catalog["metric_for"](host, service, key)
    if metric is None:
        raise ValueError("Unknown metric")
    return catalog["archive_id"](metric)


def rrd_create(path: Path, data_type: str, rrdtool: str, heartbeat: int, timestamp: int) -> None:
    subprocess.run([rrdtool, "create", str(path), "--step", str(DEFAULT_STEP),
                    "--start", str(timestamp - 1),
                    f"DS:value:GAUGE:{heartbeat}:U:U" if data_type == "GAUGE_SIGNED" else f"DS:value:{data_type}:{heartbeat}:0:U",
                    "RRA:AVERAGE:0.5:1:1440", "RRA:AVERAGE:0.5:5:2016",
                    "RRA:AVERAGE:0.5:60:8760"], check=True)


def import_batch(source: Path, destination: Path, rrdtool: str = "rrdtool") -> tuple[int, int]:
    configured_catalog()  # Reject missing/invalid configuration before any writes.
    destination.mkdir(parents=True, exist_ok=True, mode=0o750)
    if source.stat().st_size > MAX_BATCH_BYTES:
        raise ValueError("batch exceeds safety limit")
    imported = rejected = 0
    with source.open(encoding="utf-8", errors="replace") as data:
        for raw in data:
            if len(raw) > MAX_LINE_LENGTH:
                rejected += 1
                continue
            parts = raw.rstrip("\n").split("\t", 3)
            if len(parts) != 4:
                rejected += 1
                continue
            epoch, host, service, perf = parts
            if not epoch.isdigit() or len(epoch) > 11:
                rejected += 1
                continue
            timestamp = int(epoch)
            metrics = parse_perfdata(perf)
            for key, value in metrics.items():
                kind = classify(host, service, key)
                if kind is None:
                    continue
                catalog = configured_catalog()
                configured_metric = catalog["metric_for"](host, service, key)
                path = destination / f"{identifier(host, service, key)}.rrd"
                if not path.exists():
                    rrd_create(path, kind, rrdtool,
                               configured_metric["heartbeat"],
                               timestamp)
                # Values outside monotonic RRD time ordering should be rejected,
                # not counted as a successful import.
                sample = ('U' if value is None else
                          format(value, '.0f') if kind == 'COUNTER' else format(value, '.12g'))
                proc = subprocess.run([rrdtool, "update", str(path),
                                       f"{timestamp}:{sample}"],
                                      capture_output=True, text=True)
                if proc.returncode == 0:
                    imported += 1
                else:
                    # A batch may have been partly imported before a restart.
                    # Only ignore an update when RRD confirms the timestamp
                    # is already committed (or superseded by newer samples).
                    last = subprocess.run([rrdtool, "last", str(path)],
                                          capture_output=True, text=True)
                    if (last.returncode != 0 or not last.stdout.strip().isdigit()
                            or int(last.stdout.strip()) < timestamp
                            or "illegal attempt to update" not in proc.stderr.lower()):
                        rejected += 1
    return imported, rejected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path, help="sealed Nagios perfdata batch")
    parser.add_argument("--rrd-dir", type=Path, default=Path("/var/lib/nagios-graphs/rrd"))
    arguments = parser.parse_args()
    success, errors = import_batch(arguments.batch, arguments.rrd_dir)
    print(f"RRD samples imported={success} rejected={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())




