#!/usr/bin/env python3
"""Offline, reversible RRD filename migration.

Dry-run is default. Stop the RRD drain and any other writers before --apply
and keep them stopped until the reader/writer deployment is complete.
Unknown archives are never modified. The backup directory must not exist.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from importlib.util import module_from_spec, spec_from_file_location

HERE = Path(__file__).resolve().parent


def module(name):
    spec = spec_from_file_location(name, HERE / (name + ".py"))
    obj = module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(catalog, directory):
    config = module("nagios_graphs_config")
    names = module("rrd_names")
    validated = config.validate(json.loads(catalog.read_text(encoding="utf-8")))
    pairs = {}
    destinations = {}
    for metric in validated["metrics"].values():
        old = config.archive_id(metric) + ".rrd"
        new = names.filename(metric["host"], metric["archive_service"], metric["metric"])
        if old in pairs and pairs[old] != new:
            raise ValueError("Shared archive maps to different destinations: " + old)
        if new in destinations and destinations[new] != old:
            raise ValueError("Destination collision: " + new)
        pairs[old], destinations[new] = new, old
    moves = []
    for old, new in sorted(pairs.items()):
        source, target = directory / old, directory / new
        if source.is_symlink() or target.is_symlink():
            raise ValueError("Symlink in migration paths: " + old)
        if source.is_file():
            if target.exists() and target != source:
                raise ValueError("Destination already exists: " + new)
            moves.append({"old": old, "new": new})
        elif source.exists():
            raise ValueError("Source is not a regular file: " + old)
    return moves


def backup_and_move(directory, backup, moves):
    # Backup is deliberately outside the archive directory, on any filesystem.
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    archive = backup / "originals"
    archive.mkdir(mode=0o700)
    manifest = {"version": 1, "state": "prepared", "files": []}
    manifest_path = backup / "manifest.json"
    try:
        for item in moves:
            source = directory / item["old"]
            saved = archive / item["old"]
            shutil.copy2(source, saved)
            if digest(source) != digest(saved):
                raise ValueError("Backup checksum mismatch: " + item["old"])
            manifest["files"].append({**item, "sha256": digest(source)})
        write_manifest(manifest_path, manifest)
        # All backup copies must be complete before changing a single archive.
        for item in manifest["files"]:
            os.rename(directory / item["old"], directory / item["new"])
        manifest["state"] = "applied"
        write_manifest(manifest_path, manifest)
    except Exception:
        # On partial failures, restore the original names from verified backup.
        for item in manifest["files"]:
            old, new = directory / item["old"], directory / item["new"]
            if new.is_file() and not old.exists():
                os.rename(new, old)
            if not old.is_file() or digest(old) != item["sha256"]:
                shutil.copy2(archive / item["old"], old)
        manifest["state"] = "recovered"
        write_manifest(manifest_path, manifest)
        raise


def write_manifest(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".manifest-", delete=False) as handle:
        json.dump(value, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


def rollback(directory, backup):
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("state") != "applied":
        raise ValueError("No applied migration manifest")
    entries = manifest["files"]
    # Fail closed if any file has changed after migration. Never overwrite samples.
    for item in entries:
        old, new = directory / item["old"], directory / item["new"]
        if old.exists() or not new.is_file() or new.is_symlink():
            raise ValueError("Rollback paths changed: " + item["new"])
        if digest(new) != item["sha256"]:
            raise ValueError("Archive changed since migration; refusing rollback: " + item["new"])
        if digest(backup / "originals" / item["old"]) != item["sha256"]:
            raise ValueError("Backup checksum mismatch: " + item["old"])
    for item in entries:
        os.rename(directory / item["new"], directory / item["old"])
    manifest["state"] = "rolled_back"
    write_manifest(backup / "manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("/var/lib/nagios-graphs/catalog.json"))
    parser.add_argument("--rrd-dir", type=Path, default=Path("/var/lib/nagios-graphs/rrd"))
    parser.add_argument("--backup-dir", type=Path, help="new directory for complete pre-migration backups")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    try:
        if args.rollback:
            if not args.backup_dir:
                parser.error("--rollback requires --backup-dir")
            rollback(args.rrd_dir, args.backup_dir)
            print("Rollback completed; restart old collector/viewer only after verification")
            return 0
        moves = plan(args.catalog, args.rrd_dir)
        print(json.dumps({"mode": "apply" if args.apply else "dry-run",
                          "archives_to_rename": len(moves),
                          "first_five": moves[:5]}, indent=2))
        if args.apply:
            if not args.backup_dir:
                parser.error("--apply requires --backup-dir")
            if args.backup_dir.resolve() == args.rrd_dir.resolve() or args.rrd_dir.resolve() in args.backup_dir.resolve().parents:
                raise ValueError("Backup must be outside the RRD directory")
            backup_and_move(args.rrd_dir, args.backup_dir, moves)
            print("Applied; preserve backup and deploy matching reader/writer before restarting drain")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("RRD migration failed: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
