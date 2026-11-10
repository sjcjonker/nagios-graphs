#!/usr/bin/env python3
# Check the dedicated ext4 graph spool mount and free space.
# Author: Stijn Jonker (sjcjonker) | https://github.com/sjcjonker
# Usage: python3 scripts/check-nagios-graphs-spool.py --mount /var/spool/nagios-graphs
# Default free-space warning/critical: 20%/10%. Help: --help.
# Nagios exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""Nagios check for the dedicated persistent graph spool mount and free space."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess

EXPECTED = "/var/spool/nagios-graphs"

def assess(path: Path, warning: float = 20, critical: float = 10,
           filesystem: str = "ext4", allow_shared: bool = False):
    if not 0 <= critical < warning <= 100:
        return 3, "UNKNOWN - require 0 <= critical < warning <= 100"
    fstype = "shared filesystem"
    if not allow_shared:
        mount = subprocess.run(["findmnt", "-rn", "-M", str(path), "-o", "TARGET,FSTYPE"],
                               text=True, capture_output=True, check=False)
        if mount.returncode or len(mount.stdout.strip().split()) != 2:
            return 2, f"CRITICAL - graph spool is not a dedicated mount: {path}"
        target, fstype = mount.stdout.strip().split()
        if target != str(path) or (filesystem != "any" and fstype != filesystem):
            return 2, f"CRITICAL - unexpected graph spool mount: {target} ({fstype})"
    stat = os.statvfs(path)
    if stat.f_blocks <= 0:
        return 3, "UNKNOWN - filesystem reports zero blocks"
    free = 100.0 * stat.f_bavail / stat.f_blocks
    state = 2 if free < critical else 1 if free < warning else 0
    level = ("OK", "WARNING", "CRITICAL")[state]
    return state, f"{level} - graph spool {free:.1f}% free on {fstype} | free_pct={free:.2f}%;{warning};{critical};0;100"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mount", type=Path, default=Path(EXPECTED))
    p.add_argument("--filesystem", default="ext4", help="expected type, or any; still requires a dedicated mount")
    p.add_argument("--allow-shared", action="store_true", help="check free space without requiring a dedicated mount")
    p.add_argument("--warning", type=float, default=20)
    p.add_argument("--critical", type=float, default=10)
    args = p.parse_args()
    try:
        result, message = assess(args.mount, args.warning, args.critical, args.filesystem, args.allow_shared)
    except OSError as e:
        result, message = 3, f"UNKNOWN - spool check failed: {e}"
    print(message)
    return result

if __name__ == "__main__":
    raise SystemExit(main())

