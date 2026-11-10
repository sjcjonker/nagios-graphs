#!/usr/bin/env python3
# Atomically enqueue Nagios' closed perfdata file in the graph spool.
# Author: Stijn Jonker (sjcjonker) | https://github.com/sjcjonker
# Usage: python3 scripts/nagios-rrd-seal.py /path/to/closed-perfdata
# Invoke after Nagios closes the file; source and spool must share a filesystem. Help: --help.
# Nagios exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""Seal Nagios' closed service-perfdata file into a bounded local spool.

Called by Nagios service_perfdata_file_processing_command, after Nagios closes
its perfdata file. The same filesystem is required for atomic rename.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
import time

MAX_SPOOL_FILES = 200
MAX_BYTES = 8 * 1024 * 1024


def seal(source: Path, spool: Path) -> Path | None:
    if not spool.is_dir():
        raise ValueError("spool directory missing")
    if source.is_symlink():
        raise ValueError("refusing symlink input")
    if not source.exists():
        return None
    if not source.is_file():
        raise ValueError("input is not a regular file")
    if source.stat().st_size == 0:
        source.unlink()
        return None
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("input is too large; refusing rotation")
    if len(list(spool.glob("*.batch"))) >= MAX_SPOOL_FILES:
        raise ValueError("backlog full; refusing rotation")
    # Rename is atomic on the same filesystem; never overwrite an existing batch.
    candidate = spool / f"{time.time_ns()}-{os.getpid()}.batch"
    if source.stat().st_dev != spool.stat().st_dev:
        raise ValueError("source and spool must share a filesystem")
    if candidate.exists():
        raise ValueError("batch collision")
    os.rename(source, candidate)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--spool", type=Path, default=Path("/var/spool/nagios-graphs"))
    args = parser.parse_args()
    try:
        result = seal(args.source, args.spool)
    except (OSError, ValueError) as error:
        print(f"Nagios RRD spool seal failed: {error}", file=sys.stderr)
        return 2
    print(f"Nagios RRD batch: {result or 'empty'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
