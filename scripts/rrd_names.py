"""Deterministic, human-readable RRD names; no filesystem changes.

The importer and renderer share this active naming policy. Legacy hash-based
archive IDs are retained separately for migration and audit.
"""
import hashlib
import re
import unicodedata


def _slug(value):
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", ascii_value).strip(".-_").lower()
    return slug[:64] or "unnamed"


def filename(host, archive_service, metric):
    """Readable identity plus short collision-resistant suffix."""
    parts = (host, archive_service, metric)
    if not all(isinstance(p, str) and p and len(p) <= 256 for p in parts):
        raise ValueError("Invalid archive identity")
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:12]
    return "__".join(_slug(part) for part in parts) + "__" + digest + ".rrd"
