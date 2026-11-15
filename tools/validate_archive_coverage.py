"""Validate that the product-selection rule covers representative coordinates.

Stdlib only, so it runs before the project dependencies are installed:
    python tools/validate_archive_coverage.py

It applies the SAME rule as the server (spherex_pipeline.choose_observation): among the archive
products whose footprint covers the point, take the one whose centre is nearest. The footprint test
is the same exact spherical (gnomonic ray-cast) test; tests/test_validate_tool.py keeps the two
implementations in agreement.
"""

from __future__ import annotations

import html
import math
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

IRSA_SIA_URL = "https://irsa.ipac.caltech.edu/SIA"
COLLECTION = "spherex_qr3"
CASES = {
    "Barnard's Star": (269.45208, 4.69339),
    "Galactic Center": (266.40510, -28.93618),
    "Andromeda Galaxy": (10.68471, 41.26875),
}


def angular_distance_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1, ra2, dec1, dec2 = map(math.radians, (ra1, ra2, dec1, dec2))
    cosine = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def parse_region(region: str) -> list[tuple[float, float]]:
    tokens = region.replace(",", " ").split()
    if not tokens or tokens[0].upper() != "POLYGON":
        return []
    numbers = []
    for token in tokens[1:]:
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    if len(numbers) < 6 or len(numbers) % 2:
        return []
    return [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers), 2)]


def contains_region(region: str | list[tuple[float, float]], ra: float, dec: float) -> bool:
    """Exact for great-circle edges: ray cast in the gnomonic plane centred on the point."""
    vertices = parse_region(region) if isinstance(region, str) else region
    if len(vertices) < 3:
        return False

    def unit(lon: float, lat: float):
        lon, lat = math.radians(lon), math.radians(lat)
        return math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)

    ra_r, dec_r = math.radians(ra), math.radians(dec)
    v0 = unit(ra, dec)
    east = (-math.sin(ra_r), math.cos(ra_r), 0.0)
    north = (-math.sin(dec_r) * math.cos(ra_r), -math.sin(dec_r) * math.sin(ra_r), math.cos(dec_r))
    pts = []
    for lon, lat in vertices:
        v = unit(lon, lat)
        d = sum(a * b for a, b in zip(v, v0, strict=True))
        if d <= 1e-9:
            return False
        pts.append((sum(a * b for a, b in zip(v, east, strict=True)) / d, sum(a * b for a, b in zip(v, north, strict=True)) / d))
    inside = False
    prev = pts[-1]
    for cur in pts:
        if (cur[1] > 0) != (prev[1] > 0):
            x_cross = cur[0] + (0 - cur[1]) * (prev[0] - cur[0]) / (prev[1] - cur[1])
            if x_cross > 0:
                inside = not inside
        prev = cur
    return inside


def archive_rows(ra: float, dec: float) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({
        "COLLECTION": COLLECTION,
        "POS": f"CIRCLE {ra:.8f} {dec:.8f} 0.05000000",
        "CALIB": "2",
        "MAXREC": "100",
        "RESPONSEFORMAT": "VOTABLE",
    })
    with urllib.request.urlopen(f"{IRSA_SIA_URL}?{params}", timeout=60) as response:
        root = ET.fromstring(response.read())
    fields = [field.attrib.get("name", "") for field in root.findall(".//{*}FIELD")]
    rows = []
    for tr in root.findall(".//{*}TR"):
        values = [(td.text or "").strip() for td in tr.findall("{*}TD")]
        if len(values) == len(fields):
            rows.append(dict(zip(fields, values, strict=True)))
    if not rows:
        raise RuntimeError("No SPHEREx products returned")
    return rows


def selected_product(ra: float, dec: float) -> tuple[dict[str, str] | None, int, int]:
    """(chosen row or None, number covering, number returned) using the server's rule."""
    rows = archive_rows(ra, dec)
    covering = [r for r in rows if contains_region(html.unescape(r["s_region"]), ra, dec)]
    if not covering:
        return None, 0, len(rows)
    best = min(covering, key=lambda r: (angular_distance_deg(ra, dec, float(r["s_ra"]), float(r["s_dec"])), r["access_url"]))
    return best, len(covering), len(rows)


def main() -> int:
    failed = False
    for name, (ra, dec) in CASES.items():
        product, covering, total = selected_product(ra, dec)
        print(f"{name}: {'PASS' if product else 'FAIL'}")
        print(f"  requested: RA {ra:.5f}, Dec {dec:.5f}")
        if product:
            separation = angular_distance_deg(ra, dec, float(product["s_ra"]), float(product["s_dec"]))
            print(f"  selected: {product.get('obs_id', 'unknown')} | {product.get('energy_bandpassname', '?')} | {covering} of {total} returned products cover it")
            print(f"  centre separation: {separation:.3f} deg")
            print(f"  product: {product['access_url']}")
        else:
            print(f"  none of the {total} returned products covers the target")
        failed |= product is None
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
