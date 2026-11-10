#!/usr/bin/env python3
"""Generate interface graphs from Nagios configuration; never change RRD files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

SNMP = {"check_int", "check_int_32", "check_int_noregex", "check_int_down_noregex", "check_int_admin_noregex"}


def objects(root: Path):
    """Read only files enabled by nagios.cfg, remapping /etc/nagios4 for staging."""
    files = set()
    for line in (root / "nagios.cfg").read_text().splitlines():
        match = re.match(r"\s*(cfg_file|cfg_dir)\s*=\s*([^;#]+)", line)
        if not match:
            continue
        path = Path(match[2].strip())
        try:
            path = root / path.relative_to("/etc/nagios4")
        except ValueError:
            if not path.is_absolute():
                path = root / path
        if match[1] == "cfg_dir":
            files.update(path.rglob("*.cfg"))
        else:
            files.add(path)
    for path in sorted(files):
        text = "\n".join(line.split(";", 1)[0] for line in path.read_text().splitlines()
                         if not line.lstrip().startswith("#"))
        for kind, body in re.findall(r"define\s+(\w+)\s*\{([^{}]*)\}", text):
            fields = {}
            for line in body.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    fields[parts[0]] = parts[1]
            yield kind, fields


def generate(root: Path, seed: Path | None = None, exclude_hosts=()) -> list[dict]:
    definitions = list(objects(root))
    templates = {row["name"]: row for kind, row in definitions
                 if kind == "service" and "name" in row}

    def resolve(row, seen=()):
        result = {}
        # Nagios gives the first template precedence over later templates.
        for name in reversed(row.get("use", "").split(",")):
            name = name.strip()
            if not name:
                continue
            if name in seen or name not in templates:
                raise ValueError("unknown or cyclic service template: " + name)
            result.update(resolve(templates[name], (*seen, name)))
        result.update(row)
        return result

    legacy = {(row["host"], row["metric_in"]): row
              for row in (json.loads(seed.read_text()) if seed else [])}
    result, identities, keys = [], set(), set()
    for kind, raw in definitions:
        if kind != "service" or raw.get("register") == "0":
            continue
        row = resolve(raw)
        parts = row.get("check_command", "").split("!")
        if parts[0] not in SNMP | {"check_local_interface"}:
            continue
        if row.get("process_perf_data") == "0":
            continue
        if row.get("hostgroup_name") or not row.get("host_name"):
            raise ValueError("interface services require explicit host_name")
        index = 1 if parts[0] == "check_local_interface" else 2
        if len(parts) <= index:
            raise ValueError("interface argument missing: " + row.get("service_description", ""))
        words = shlex.split(parts[index])
        if len(words) != 1 or not re.fullmatch(r"[A-Za-z0-9_. -]{1,100}", words[0]):
            raise ValueError("interface must select one literal name: " + parts[index])
        interface = words[0]
        service = row.get("service_description", "")
        if (not service.strip() or len(service) > 256 or
                not service.isprintable() or ".." in service):
            raise ValueError("invalid interface service description")
        for host in row["host_name"].split(","):
            host = host.strip()
            if not re.fullmatch(r"[A-Za-z0-9_. -]{1,120}", host):
                raise ValueError("interface requires a literal host name: " + host)
            if host in exclude_hosts:
                continue
            identity = (host, interface)
            if identity in identities:
                raise ValueError("duplicate interface: " + repr(identity))
            identities.add(identity)
            old = legacy.get((host, interface + "_in_octet"))
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
            key = old["key"] if old else "if-" + digest
            if key in keys:
                raise ValueError("duplicate graph key: " + key)
            keys.add(key)
            result.append(dict(key=key, host=host, service=service,
                               metric_in=interface + "_in_octet", metric_out=interface + "_out_octet",
                               title=host + " — " + service,
                               archive_service=old.get("archive_service", old["service"]) if old
                               else "Interface " + interface))
    return sorted(result, key=lambda row: (row["host"], row["metric_in"]))


def catalog(base: dict, interfaces: list[dict]) -> dict:
    """Add explicit counter metrics and graphs to an administrator-owned base."""
    import copy
    result = copy.deepcopy(base)
    for row in interfaces:
        refs = {}
        prefix = row['metric_in'].removesuffix('_in_octet')
        for direction in ('in', 'out'):
            for kind in ('octet', 'error', 'discard'):
                metric = f'{prefix}_{direction}_{kind}'
                identity = [row['host'], row['service'], metric]
                key = 'interface-' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
                refs[(direction, kind)] = key
                result['metrics'].append(dict(id=key, host=row['host'], service=row['service'], metric=metric,
                                               type='COUNTER', archive_service=row.get('archive_service', row['service'])))
        lines = [dict(metric=refs[(d, 'octet')], label=label, color=color, scale=0.000008)
                 for d, label, color in (('in', 'Inbound', '#1682B2'), ('out', 'Outbound', '#D97706'))]
        faults = [('error', 'in', '#DC2626'), ('error', 'out', '#9333EA'),
                  ('discard', 'in', '#059669'), ('discard', 'out', '#475569')]
        for kind, direction, color in faults:
            lines.append(dict(metric=refs[(direction, kind)], label=f'{direction.title()} {kind}s (right)',
                              color=color, width=1, axis='right', unit='/s', optional=True))
        result['graphs'].append(dict(id=row['key'], title=row['title'], unit='Mbit/s', lower=0,
                                     units_exponent=0, left_format='%.3lf', right_format='%.4lf',
                                     right_axis=dict(unit='Errors / discards per second', scale='auto'), lines=lines,
                                     note='Traffic: left axis (Mbit/s). Errors and discards: right axis (per second).'))
        for kind, suffix, unit in (('error', 'errors', 'Errors/s'), ('discard', 'discards', 'Discards/s')):
            result['graphs'].append(dict(id=row['key'] + '-' + suffix, title=row['title'] + ' ' + suffix,
                                         unit=unit, lower=0, hidden=True, units_exponent=0, left_format='%.3lf',
                                         lines=[dict(metric=refs[(d, kind)], label=label, color=color)
                                                for d, label, color in (('in', 'Inbound', '#1682B2'), ('out', 'Outbound', '#D97706'))]))
    return result


def render(root: Path, seed: Path | None = None, exclude_hosts=()) -> bytes:
    return (json.dumps(generate(root, seed, exclude_hosts), ensure_ascii=False, indent=2) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', type=Path, required=True)
    parser.add_argument('--seed', type=Path, help='optional private legacy archive mapping')
    parser.add_argument('--exclude-host', action='append', default=[])
    parser.add_argument('--catalog-base', type=Path, help='emit complete catalog rather than interface manifest')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--stdout', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.stdout and (args.output or args.check):
        parser.error('--stdout cannot be combined with --output or --check')
    if not args.stdout and args.output is None:
        parser.error('choose --stdout or --output')
    try:
        rows = generate(args.config_dir, args.seed, args.exclude_host)
        data = catalog(json.loads(args.catalog_base.read_text()), rows) if args.catalog_base else rows
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
        if args.catalog_base:
            import runpy
            runpy.run_path(str(Path(__file__).with_name('nagios_graphs_config.py')))['validate'](data)
        if args.stdout:
            sys.stdout.buffer.write(encoded)
        elif args.check:
            matches = args.output.is_file() and args.output.read_bytes() == encoded
            print('OK: generated graphs match' if matches else 'DIFF: regenerate graphs')
            return 0 if matches else 1
        else:
            if args.output.is_symlink():
                raise ValueError('output must not be a symlink')
            fd, name = tempfile.mkstemp(prefix='.graphs-', dir=args.output.parent)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(name, 0o644)
                os.replace(name, args.output)
            finally:
                Path(name).unlink(missing_ok=True)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Interface graphs: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
