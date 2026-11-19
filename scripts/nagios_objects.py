"""Read effective Nagios objects from objects.cache (no template reimplementation).

This module is read-only. It deliberately does not infer that a service emits
metrics merely because process_perf_data is enabled.
"""
from __future__ import annotations

from pathlib import Path
import re

DEFAULT_OBJECTS_CACHE = Path("/var/lib/nagios4/objects.cache")
_HEADER = re.compile(r"^define\s+(host|service)\s*\{\s*$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_objects(path: Path = DEFAULT_OBJECTS_CACHE):
    """Return parsed host and service objects from Nagios' effective cache.

    Fail closed on malformed records; do not parse untrusted paths from CGI
    arguments. A caller must supply a trusted, administrator-controlled path.
    """
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Nagios object cache is unavailable or a symlink")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Nagios object cache exceeds 32 MiB")

    result = []
    kind = None
    fields = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if kind is None:
            match = _HEADER.fullmatch(stripped)
            if match:
                kind, fields = match.group(1), {}
            continue
        if stripped == "}":
            result.append((kind, fields))
            kind, fields = None, {}
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2 or not _NAME.fullmatch(parts[0]):
            raise ValueError(f"Malformed Nagios object field at line {number}")
        if parts[0] in fields:
            raise ValueError(f"Duplicate Nagios object field at line {number}")
        fields[parts[0]] = parts[1]
    if kind is not None:
        raise ValueError("Unterminated Nagios object")
    return result


def perfdata_services(path: Path = DEFAULT_OBJECTS_CACHE):
    """Yield effective services for which Nagios enables perfdata processing.

    Does not assert that the plugin emitted perfdata. That requires collector
    observations and must be validated separately.
    """
    seen = set()
    for kind, fields in read_objects(path):
        if kind != "service" or fields.get("process_perf_data") != "1":
            continue
        host = fields.get("host_name", "")
        service = fields.get("service_description", "")
        if not host or not service:
            raise ValueError("Perfdata service missing host or description")
        identity = (host, service)
        if identity in seen:
            raise ValueError("Duplicate effective Nagios service: " + repr(identity))
        seen.add(identity)
        yield fields
