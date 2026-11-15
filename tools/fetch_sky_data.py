"""Fetch the real reference sky data the frontend point map draws.

Writes frontend/public/data/sky.json with:
  * bright stars (SIMBAD, V < STAR_LIMIT) -> ra, dec, V, B-V when known, name
  * named landmark targets                 -> ra, dec, type, parallax distance

Everything is ICRS, epoch J2000, in degrees (proper motions in mas/yr), exactly as SIMBAD reports it.
The output records its own provenance (service, ADQL, fetch time) so a reader
can tell this is archive data and not something typed in by hand.

Standard library only:   python tools/fetch_sky_data.py
It exits non-zero rather than silently dropping a landmark that did not resolve.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, UTC
from pathlib import Path

# The CDS Strasbourg mirror was returning errors when this was written;
# the Harvard mirror serves the same SIMBAD database.
TAP_URLS = (
    "https://simbad.harvard.edu/simbad/sim-tap/sync",
    "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
)
STAR_LIMIT = 4.5
OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "data" / "sky.json"

# SIMBAD identifier -> display label.  Identifiers are matched exactly through the
# `ident` table, so a typo fails loudly instead of resolving to the wrong object.
LANDMARKS = {
    "NAME Barnard's star": "Barnard's Star",
    "NAME Galactic Center": "Galactic Center",
    "M  31": "Andromeda Galaxy",
    "M  42": "Orion Nebula",
    "M   1": "Crab Nebula",
    "M  45": "Pleiades",
    "NAME LMC": "Large Magellanic Cloud",
    "NAME SMC": "Small Magellanic Cloud",
    "* alf CMa": "Sirius",
    "* alf Ori": "Betelgeuse",
    "* alf Lyr": "Vega",
    "* alf UMi": "Polaris",
    "* alf Cen": "Alpha Centauri",
}


def adql(query: str) -> list[dict[str, str]]:
    body = urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}
    ).encode()
    last_error: Exception | None = None
    for url in TAP_URLS:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=90) as response:
                text = response.read().decode("utf-8")
            if not text.startswith(("main_id", '"main_id"')) and "main_id" not in text.splitlines()[0]:
                raise RuntimeError(f"unexpected reply from {url}: {text[:120]!r}")
            return list(csv.DictReader(io.StringIO(text)))
        except Exception as exc:  # try the next mirror, report the last failure
            last_error = exc
    raise RuntimeError(f"all SIMBAD mirrors failed: {last_error}")


def number(value: str) -> float | None:
    try:
        return float(value) if value not in ("", None) else None
    except ValueError:
        return None


def fetch_stars() -> tuple[list[dict], str]:
    query = (
        "SELECT main_id, ra, dec, pmra, pmdec, V, B, plx_value FROM basic "
        "JOIN allfluxes ON basic.oid = allfluxes.oidref "
        f"WHERE V < {STAR_LIMIT} AND ra IS NOT NULL AND dec IS NOT NULL ORDER BY V"
    )
    stars = []
    for row in adql(query):
        ra, dec, v = number(row["ra"]), number(row["dec"]), number(row["V"])
        if ra is None or dec is None or v is None:
            continue
        b = number(row["B"])
        stars.append({
            "name": row["main_id"].strip(),
            "ra": round(ra, 5),
            "dec": round(dec, 5),
            "v": round(v, 2),
            "bv": round(b - v, 2) if b is not None else None,
            "plx": number(row["plx_value"]),
            "pmra": number(row["pmra"]),  # mas/yr, includes cos(dec)
            "pmdec": number(row["pmdec"]),
        })
    return stars, query


def fetch_landmarks() -> tuple[list[dict], str]:
    names = ", ".join("'" + key.replace("'", "''") + "'" for key in LANDMARKS)
    query = (
        "SELECT main_id, ra, dec, pmra, pmdec, otype, plx_value, id AS ident FROM basic "
        f"JOIN ident ON basic.oid = ident.oidref WHERE id IN ({names})"
    )
    by_ident = {row["ident"]: row for row in adql(query)}
    missing = [key for key in LANDMARKS if key not in by_ident]
    if missing:
        raise RuntimeError(f"landmarks did not resolve in SIMBAD: {missing}")
    out = []
    for key, label in LANDMARKS.items():
        row = by_ident[key]
        plx = number(row["plx_value"])
        out.append({
            "label": label,
            "simbad_id": row["main_id"].strip(),
            "ra": round(float(row["ra"]), 5),
            "dec": round(float(row["dec"]), 5),
            "otype": row["otype"],
            "pmra": number(row["pmra"]),
            "pmdec": number(row["pmdec"]),
            # 3261.56 light-years per parsec; only trusted for stars with a parallax.
            "distance_ly": round(3261.56 / plx, 2) if plx and plx > 1 else None,
        })
    return out, query


def main() -> int:
    try:
        stars, stars_query = fetch_stars()
        landmarks, landmarks_query = fetch_landmarks()
    except Exception as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    if len(stars) < 300:
        print(f"suspiciously few stars ({len(stars)}); refusing to write", file=sys.stderr)
        return 1
    payload = {
        "provenance": {
            "service": "SIMBAD TAP (CDS)",
            "frame": "ICRS, epoch J2000, degrees",
            "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "star_limit_v": STAR_LIMIT,
            "queries": {"stars": stars_query, "landmarks": landmarks_query},
        },
        "stars": stars,
        "landmarks": landmarks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: {len(stars)} stars, {len(landmarks)} landmarks")
    for item in landmarks:
        print(f"  {item['label']:<24} RA {item['ra']:>9.5f}  Dec {item['dec']:>9.5f}  {item['otype']}  d={item['distance_ly']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
