#!/usr/bin/env python3
"""Read-only preflight for a future RRD filename migration.

Never renames, deletes, or modifies an RRD. Reports shared archive identities,
unmapped files, and collisions before any collector/viewer cutover.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

from importlib.util import module_from_spec, spec_from_file_location

HERE = Path(__file__).resolve().parent


def module(name):
    spec = spec_from_file_location(name, HERE / (name + ".py"))
    obj = module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def inspect(catalog_path, rrd_dir):
    config = module("nagios_graphs_config")
    names = module("rrd_names")
    validated = config.validate(json.loads(catalog_path.read_text(encoding="utf-8")))
    by_old = defaultdict(list)
    by_new = defaultdict(set)
    for metric in validated["metrics"].values():
        old = config.archive_id(metric) + ".rrd"
        new = names.filename(metric["host"], metric["archive_service"], metric["metric"])
        by_old[old].append({"id": metric["id"], "new": new})
        by_new[new].add(old)

    collisions = {new: sorted(olds) for new, olds in by_new.items() if len(olds) > 1}
    inconsistent = {
        old: entries for old, entries in by_old.items()
        if len({entry["new"] for entry in entries}) > 1
    }
    shared = {old: entries for old, entries in by_old.items() if len(entries) > 1}
    existing = {p.name for p in rrd_dir.glob("*.rrd") if p.is_file() and not p.is_symlink()}
    expected = set(by_old)
    already_named = {new for entries in by_old.values() for new in
                     (entry["new"] for entry in entries)}
    conflicts = sorted(name for name in already_named if name in existing and name not in expected)
    return {
        "catalog_archive_identities": len(expected),
        "existing_catalog_archives": len(expected & existing),
        "unmapped_archives": sorted(existing - expected),
        "shared_archive_identities": shared,
        "inconsistent_shared_identities": inconsistent,
        "destination_collisions": collisions,
        "destination_exists": conflicts,
        "safe_to_plan": not (inconsistent or collisions or conflicts),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("/var/lib/nagios-graphs/catalog.json"))
    parser.add_argument("--rrd-dir", type=Path, default=Path("/var/lib/nagios-graphs/rrd"))
    parser.add_argument("--summary", action="store_true", help="omit the full unmapped filename list")
    args = parser.parse_args()
    try:
        report = inspect(args.catalog, args.rrd_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 2
    if args.summary:
        report["unmapped_archives"] = len(report["unmapped_archives"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["safe_to_plan"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
