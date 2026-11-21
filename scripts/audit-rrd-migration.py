#!/usr/bin/env python3
"""Read-only migration audit: effective Nagios services, catalog and RRD paths."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit(objects_cache, catalog_path, rrd_dir):
    objects = load_module("nagios_objects")
    names = load_module("rrd_names")
    config = load_module("nagios_graphs_config")
    catalog = config.validate(json.loads(catalog_path.read_text(encoding="utf-8")))
    active = {(row["host_name"], row["service_description"])
              for row in objects.perfdata_services(objects_cache)}
    mapped = {(metric["host"], metric["service"]) for metric in catalog["metrics"].values()}
    graphical = set()
    for graph in catalog["graphs"].values():
        for line in graph["lines"]:
            metric = catalog["metrics"][line["metric"]]
            graphical.add((metric["host"], metric["service"]))
    missing = sorted(active - graphical)
    orphaned = sorted(mapped - active)
    planned = []
    destinations = {}
    for metric in catalog["metrics"].values():
        old = rrd_dir / (config.archive_id(metric) + ".rrd")
        new = rrd_dir / names.filename(metric["host"], metric["archive_service"], metric["metric"])
        if new in destinations and destinations[new] != old:
            raise ValueError("RRD filename collision: " + str(new))
        destinations[new] = old
        if old.is_file():
            if new.exists() and new != old:
                raise ValueError("Destination already exists: " + str(new))
            planned.append({"old": str(old), "new": str(new)})
    return {"effective_perfdata_services": len(active), "catalog_services": len(mapped),
            "services_without_graph": missing, "catalog_services_not_enabled": orphaned,
            "existing_rrd_renames": sorted(planned, key=lambda row: row["old"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects-cache", type=Path, default=Path("/var/lib/nagios4/objects.cache"))
    parser.add_argument("--catalog", type=Path, default=Path("/var/lib/nagios-graphs/catalog.json"))
    parser.add_argument("--rrd-dir", type=Path, default=Path("/var/lib/nagios-graphs/rrd"))
    args = parser.parse_args()
    try:
        result = audit(args.objects_cache, args.catalog, args.rrd_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Migration audit failed: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 1 if result["services_without_graph"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
