#!/usr/bin/env python3
# Import sealed graph batches and retain failed batches for retry.
# Author: Stijn Jonker (sjcjonker) | https://github.com/sjcjonker
# Usage: python3 scripts/nagios-rrd-drain.py --spool /var/spool/nagios-graphs
# Run as the dedicated graph user; never rotates the live perfdata file. Help: --help.
# Nagios exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
"""Process sealed Nagios performance-data batches, retaining failures for retry.

Runs as a dedicated local user under a systemd timer.  This command NEVER
rotates Nagios' live append file: the Nagios file-processing command must seal
and enqueue batches separately before enabling global performance processing.
"""
from __future__ import annotations

import argparse
import fcntl
from pathlib import Path
import subprocess
import sys

MAX_SPOOL_FILES = 200
MAX_BATCH_BYTES = 8 * 1024 * 1024


def drain(spool: Path, importer: Path, destination: Path, rrdtool: str) -> tuple[int, int]:
    if not spool.is_dir():
        raise ValueError("spool directory does not exist")
    processed = failures = 0
    lock = spool / ".drain.lock"
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entries = sorted(spool.glob("*.batch"))
        if len(entries) > MAX_SPOOL_FILES:
            raise ValueError("spool backlog exceeds safe limit")
        for path in entries:
            if path.is_symlink() or not path.is_file():
                failures += 1
                continue
            if path.stat().st_size > MAX_BATCH_BYTES:
                failures += 1
                continue
            outcome = subprocess.run(
                [sys.executable, str(importer), str(path), "--rrd-dir", str(destination)],
                capture_output=True, text=True, timeout=120,
            )
            if outcome.returncode:
                failures += 1
                # Stop on the first failure to retain ordering.
                break
            path.unlink()
            processed += 1
    return processed, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spool", type=Path, default=Path("/var/spool/nagios-graphs"))
    parser.add_argument("--importer", type=Path, default=Path("/usr/local/libexec/nagios-rrd-import.py"))
    parser.add_argument("--rrd-dir", type=Path, default=Path("/var/lib/nagios-graphs/rrd"))
    args = parser.parse_args()
    try:
        complete, failed = drain(args.spool, args.importer, args.rrd_dir, "rrdtool")
    except (OSError, ValueError, BlockingIOError, subprocess.TimeoutExpired) as error:
        print(f"Nagios RRD spool ERROR: {error}", file=sys.stderr)
        return 2
    print(f"Nagios RRD spool: processed={complete} failures={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
