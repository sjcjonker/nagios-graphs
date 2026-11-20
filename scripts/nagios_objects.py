"""Read Nagios' effective object cache without reimplementing template inheritance."""
from pathlib import Path
import re

DEFAULT_PATH = Path("/var/lib/nagios4/objects.cache")
HEADER = re.compile(r"define\s+(\w+)\s*\{")
FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def read_objects(path=DEFAULT_PATH):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Nagios objects.cache missing or symlinked")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Nagios objects.cache too large")
    result, kind, fields = [], None, {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if kind is None:
            match = HEADER.fullmatch(line)
            if match:
                kind, fields = match.group(1), {}
            continue
        if line == "}":
            result.append((kind, fields))
            kind, fields = None, {}
            continue
        if not line or line.startswith(("#", ";")):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not FIELD.fullmatch(parts[0]) or parts[0] in fields:
            raise ValueError(f"Invalid Nagios object at line {number}")
        fields[parts[0]] = parts[1]
    if kind is not None:
        raise ValueError("Unterminated Nagios object")
    return result


def perfdata_services(path=DEFAULT_PATH):
    seen = set()
    for kind, row in read_objects(path):
        if kind != "service" or row.get("process_perf_data") != "1":
            continue
        identity = row.get("host_name"), row.get("service_description")
        if not all(identity) or identity in seen:
            raise ValueError("Invalid or duplicate perfdata service: " + repr(identity))
        seen.add(identity)
        yield row
