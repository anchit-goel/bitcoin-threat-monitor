"""Offline geo enrichment for IP addresses.

Everything here reads a local MaxMind database file. Nothing in this module
opens a socket, and that is a hard requirement rather than a preference: the
system is meant to run on an analyst's machine against traffic that must not
leave it, and a lookup service would leak the very addresses under
investigation to a third party.

The database is not committed - it is licensed and large. Download
GeoLite2-City.mmdb from MaxMind and place it at:

    backend/app/data/GeoLite2-City.mmdb

Without it, `lookup` returns None and the rest of the system carries on. Geo is
enrichment, not a dependency; a missing database must not stop a graph being
built or a wallet being scored.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "GeoLite2-City.mmdb"

_reader: Any = None
_reader_tried = False


def database_path() -> Path:
    return DB_PATH


def is_available() -> bool:
    """Is the local database present and openable?"""
    return _get_reader() is not None


def _get_reader():
    """Open the database once, and remember if it could not be opened.

    Retrying a missing file on every lookup would turn one misconfiguration
    into thousands of failed opens while a graph is being built.
    """
    global _reader, _reader_tried
    if _reader is not None or _reader_tried:
        return _reader

    _reader_tried = True
    if not DB_PATH.exists():
        return None
    try:
        import geoip2.database

        _reader = geoip2.database.Reader(str(DB_PATH))
    except Exception:
        _reader = None
    return _reader


def close() -> None:
    """Release the database handle. Called on application shutdown."""
    global _reader, _reader_tried
    if _reader is not None:
        try:
            _reader.close()
        except Exception:
            pass
    _reader = None
    _reader_tried = False


def is_public_ip(ip: str) -> bool:
    """Is this a routable address worth looking up at all?"""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def lookup(ip: str) -> dict[str, Any] | None:
    """Resolve an IP to a location using the local database only.

    Returns None when the database is absent, the address is not routable, or
    the address is simply not in the database. Never raises, and never touches
    the network.
    """
    reader = _get_reader()
    if reader is None or not is_public_ip(ip):
        return None

    try:
        response = reader.city(ip)
    except Exception:
        # geoip2 raises AddressNotFoundError for addresses it does not carry,
        # which is an ordinary outcome rather than a fault.
        return None

    return {
        "ip": ip,
        "country": response.country.iso_code,
        "country_name": response.country.name,
        "city": response.city.name,
        "latitude": response.location.latitude,
        "longitude": response.location.longitude,
    }


def enrich_graph(graph) -> int:
    """Attach location data to the IP nodes of a graph, in place.

    Returns how many nodes were enriched. A no-op when the database is absent.
    """
    from app.services.graph_builder import NODE_TYPE_IP

    if not is_available():
        return 0

    enriched = 0
    for node, data in graph.nodes(data=True):
        if data.get("type") != NODE_TYPE_IP:
            continue
        location = lookup(node)
        if location:
            data["geo"] = location
            enriched += 1
    return enriched
