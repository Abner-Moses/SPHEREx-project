"""Name resolution and star catalogue queries (SIMBAD).

Everything returned is ICRS at epoch J2000.0 with proper motion in mas/yr, so a client can move
an object to any observation epoch.  Failures raise ``ServiceError`` with a client-safe message.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict

import requests

LOG = logging.getLogger(__name__)

SIMBAD_TAP_URLS = (
    "https://simbad.harvard.edu/simbad/sim-tap/sync",
    "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
)
SIMBAD_SCRIPT_URLS = (
    "https://simbad.harvard.edu/simbad/sim-script",
    "https://simbad.cds.unistra.fr/simbad/sim-script",
)
TIMEOUT = (5.0, 45.0)
CACHE_TTL_S = 600.0
CACHE_SIZE = 256
MAX_CONE_RADIUS_DEG = 5.0
MAX_CONE_ROWS = 4000
LY_PER_PARSEC = 3261.56


class ServiceError(RuntimeError):
    """A catalogue lookup failed in a way worth telling the user about."""


class UnknownObject(ServiceError):
    """The name or position matched nothing."""


_session = threading.local()
_cache: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
_cache_lock = threading.Lock()


def _http() -> requests.Session:
    if os.getenv("SPHEREX_OFFLINE", "0") == "1":
        raise ServiceError("SIMBAD is unavailable in offline mode; use the bundled sky catalogue or a demo target.")
    s = getattr(_session, "s", None)
    if s is None:
        s = requests.Session()
        _session.s = s
    return s


def _cached(key: tuple, producer):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            _cache.move_to_end(key)
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (now, value)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return value


def _adql(query: str) -> list[dict[str, str]]:
    last: Exception | None = None
    for url in SIMBAD_TAP_URLS:
        try:
            response = _http().post(url, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}, timeout=TIMEOUT)
            response.raise_for_status()
            lines = response.text.splitlines()
            if not lines or "main_id" not in lines[0]:
                raise ServiceError(f"unexpected SIMBAD reply: {response.text[:100]!r}")
            return list(csv.DictReader(io.StringIO(response.text)))
        except (requests.RequestException, ServiceError) as exc:
            last = exc
    raise ServiceError(f"SIMBAD is unreachable right now ({last}).")


def _num(value: str | None) -> float | None:
    try:
        if value is None or value.strip() in {"", "~"}:
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except ValueError:
        return None


def _clean_name(raw: str) -> str:
    return " ".join(re.sub(r"^NAME\s+", "", raw.strip()).split())


def _obj(name: str, ra: float, dec: float, pmra, pmdec, plx, otype, vmag) -> dict:
    return {
        "name": _clean_name(name),
        "ra": round(ra % 360.0, 7),
        "dec": round(dec, 7),
        "pmra_mas_yr": pmra,
        "pmdec_mas_yr": pmdec,
        "parallax_mas": plx,
        "otype": (otype or "").strip() or None,
        "vmag": vmag,
        "distance_ly": round(LY_PER_PARSEC / plx, 2) if plx and plx > 1 else None,
    }


def _colours(item: dict, row: dict[str, str]) -> dict:
    """B, J, H, K magnitudes when SIMBAD has them (for colour-magnitude and colour-colour diagrams)."""
    for band in ("B", "J", "H", "K"):
        v = _num(row.get(band))
        if v is not None:
            item[band.lower() + "mag"] = v
    return item


def _from_row(row: dict[str, str]) -> dict | None:
    ra, dec = _num(row.get("ra")), _num(row.get("dec"))
    if ra is None or dec is None:
        return None
    return _colours(_obj(row["main_id"], ra, dec, _num(row.get("pmra")), _num(row.get("pmdec")), _num(row.get("plx_value")), row.get("otype"), _num(row.get("V"))), row)


def _escape(text: str) -> str:
    return text.replace("'", "''")


def resolve_name(query: str) -> dict:
    """Resolve 'M31', 'Barnard star', 'HD 10700', 'Sgr A*' ... with SIMBAD's own name parser."""
    q = " ".join(query.split())
    if not q or len(q) > 100 or any(c in q for c in "\n\r|\\"):
        raise UnknownObject("Enter an object name.")

    def run() -> dict:
        script = (
            "output console=off script=off\n"
            'format object "%IDLIST(1)|%COO(d;A|D)|%PM(A|D)|%PLX(V)|%OTYPE(S)|%FLUXLIST(V;N=F)"\n'
            f"query id {q}"
        )
        last: Exception | None = None
        for url in SIMBAD_SCRIPT_URLS:
            try:
                response = _http().get(url, params={"script": script}, timeout=TIMEOUT)
                response.raise_for_status()
                lines = [ln.strip() for ln in response.text.splitlines() if ln.strip() and not ln.startswith("::")]
                if not lines or lines[0].count("|") < 6:
                    raise UnknownObject(f"SIMBAD does not know “{q}”.")
                p = [part.strip() for part in lines[0].split("|")]
                ra, dec = _num(p[1]), _num(p[2])
                if ra is None or dec is None:
                    raise UnknownObject(f"SIMBAD has no position for “{q}”.")
                vmag = _num(p[7].replace("V=", "")) if len(p) > 7 else None
                return _obj(p[0], ra, dec, _num(p[3]), _num(p[4]), _num(p[5]), p[6], vmag)
            except UnknownObject:
                raise
            except (requests.RequestException, ServiceError) as exc:
                last = exc
        raise ServiceError(f"SIMBAD is unreachable right now ({last}).")

    return _cached(("resolve", q.lower()), run)


_SELECT = "SELECT {top} main_id, ra, dec, pmra, pmdec, plx_value, otype, V, B, J, H, K FROM basic LEFT JOIN allfluxes ON basic.oid = allfluxes.oidref"


def suggest(prefix: str, limit: int = 8) -> list[dict]:
    """Autocomplete: objects whose identifier starts with the typed text."""
    q = " ".join(prefix.split())
    if len(q) < 2 or len(q) > 60:
        return []
    limit = max(1, min(limit, 20))

    def run() -> list[dict]:
        # SIMBAD identifiers are case-sensitive and prefix-indexed: try the spellings people type
        # (as typed, UPPER, Title Case), bare and as a common "NAME ..." identifier.
        spellings = list(dict.fromkeys([q, q.upper(), q.title()]))
        likes = " OR ".join(f"ident.id LIKE '{_escape(s)}%'" for s in spellings)
        likes += " OR " + " OR ".join(f"ident.id LIKE 'NAME {_escape(s)}%'" for s in spellings)
        query = (
            f"SELECT TOP {max(limit * 25, 200)} main_id, ra, dec, pmra, pmdec, plx_value, otype, V FROM basic "
            "JOIN ident ON basic.oid = ident.oidref LEFT JOIN allfluxes ON basic.oid = allfluxes.oidref "
            f"WHERE {likes}"
        )
        seen: dict[str, dict] = {}
        for row in _adql(query):
            item = _from_row(row)
            if item and item["name"] not in seen:
                seen[item["name"]] = item
        low = q.lower()
        ranked = sorted(
            seen.values(),
            key=lambda i: (not i["name"].lower().startswith(low), i["vmag"] is None, i["vmag"] or 99, len(i["name"])),
        )
        return ranked[:limit]

    return _cached(("suggest", q.lower(), limit), run)


def cone_stars(ra: float, dec: float, radius_deg: float, mag_limit: float = 13.0, limit: int = 2000) -> list[dict]:
    """Catalogued objects within a cone, brightest first. Used for deep-zoom star fields."""
    if not (math.isfinite(ra) and math.isfinite(dec) and 0 <= ra <= 360 and -90 <= dec <= 90):
        raise ServiceError("Invalid position.")
    if not (0 < radius_deg <= MAX_CONE_RADIUS_DEG):
        raise ServiceError(f"Cone radius must be between 0 and {MAX_CONE_RADIUS_DEG:g} degrees.")
    limit = max(1, min(limit, MAX_CONE_ROWS))

    def run() -> list[dict]:
        query = (
            _SELECT.format(top=f"TOP {limit}")
            + f" WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f})) = 1"
            + f" AND V < {float(mag_limit):.2f} ORDER BY V"
        )
        return [item for item in (_from_row(r) for r in _adql(query)) if item]

    # round the cache key so nearby pans reuse a result
    return _cached(("cone", round(ra, 2), round(dec, 2), round(radius_deg, 2), round(float(mag_limit), 1), limit), run)  # type: ignore[return-value]
