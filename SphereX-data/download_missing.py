import json
import math
from pathlib import Path
from collections import defaultdict
from io import BytesIO
from urllib.parse import urlsplit

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.io import fits
from astropy.wcs import WCS

import download as d

LIMIT = 480_000_000

TARGETS = [
    ("epsilon_indi_b", SkyCoord(
        "22h04m10.595s", "-56d46m58.239s")),
    ("wise_0855", SkyCoord(
        133.79512917, -7.24514722, unit="deg")),
    ("rw_aurigae", SkyCoord(
        "05h07m49.566s", "+30d24m05.177s")),
    ("r_draconis", SkyCoord(
        "16h32m40.227s", "+66d45m17.926s")),
]

REQUIRED = {
    "IMAGE", "FLAGS", "VARIANCE",
    "ZODI", "EPSF", "WCS-WAVE"
}

cache = d.ROOT / "cached_products.json"
records = json.loads(cache.read_text()) if cache.exists() else []

def normalize(record):
    try:
        r = {**record, **(record.get("sia") or {})}

        url = str(
            r.get("access_url")
            or r.get("source_url")
            or r.get("url")
            or ""
        )

        path = urlsplit(url).path
        marker = "/ibe/data/spherex/"

        if marker not in path:
            return None

        key = path.split(marker, 1)[1]

        if not key.startswith(("dr1/level2/", "qr3/level2/")):
            return None

        if not key.endswith(".fits"):
            return None

        t = float(r.get("t_min", r.get("time")))
        if not math.isfinite(t):
            return None

        return {
            "url": url,
            "aws": d.AWS + key,
            "time": t,
            "detector": str(r.get(
                "energy_bandpassname", r.get("detector", "")
            )),
            "s_ra": r.get("s_ra"),
            "s_dec": r.get("s_dec")
        }
    except (ValueError, TypeError):
        return None


def cached_candidates(c):
    result = []

    for record in records:
        r = normalize(record)
        if not r:
            continue

        try:
            center = SkyCoord(
                float(r["s_ra"]),
                float(r["s_dec"]),
                unit="deg"
            )

            # Broad candidate filter. Exact coverage
            # is verified against the downloaded FITS.
            if center.separation(c).deg <= 2.7:
                result.append(r)
        except (ValueError, TypeError):
            continue

    return result


def query_direct(c):
    response = d.session.get(
        "https://irsa.ipac.caltech.edu/SIA",
        params=[
            ("POS", f"CIRCLE {c.ra.deg} {c.dec.deg} 0.05"),
            ("COLLECTION", "spherex_dr1"),
            ("COLLECTION", "spherex_qr3")
        ],
        timeout=(15, 45)
    )
    response.raise_for_status()

    table = Table.read(
        BytesIO(response.content), format="votable"
    )

    result = []

    for row in table:
        r = normalize({
            k: row[k] for k in table.colnames
        })
        if r:
            result.append(r)

    return result


def rank_pairs(rows):
    groups = defaultdict(list)
    seen = set()

    for r in rows:
        if r["aws"] in seen:
            continue
        seen.add(r["aws"])
        groups[r["detector"]].append(r)

    ranked = []

    # Prefer detector groups with long temporal baselines.
    ordered = sorted(
        groups.values(),
        key=lambda g: max(r["time"] for r in g)
                      - min(r["time"] for r in g),
        reverse=True
    )

    for group in ordered:
        group.sort(key=lambda r: r["time"])

    # Retrieve early/late observations in pairs.
    while any(ordered):
        for group in ordered:
            if group:
                ranked.append(group.pop(0))
            if group:
                ranked.append(group.pop(-1))

    return ranked


def covers_target(path, c):
    with fits.open(path, memmap=True) as h:
        image = h["IMAGE"]
        w = WCS(image.header, fobj=h).celestial
        x, y = w.world_to_pixel(c)
        ny, nx = image.shape[-2:]

        return (
            math.isfinite(float(x))
            and math.isfinite(float(y))
            and 0 <= x < nx
            and 0 <= y < ny
        )


for name, c in TARGETS:
    print(f"\n=== {name.upper()} ===", flush=True)

    folder = d.ROOT / "observations" / name
    folder.mkdir(parents=True, exist_ok=True)

    metadata_file = folder / "archive_records.json"

    candidates = cached_candidates(c)

    if metadata_file.exists():
        candidates += json.loads(metadata_file.read_text())

    print(f"Cached candidates: {len(candidates)}")

    if len(candidates) < 10:
        try:
            candidates += query_direct(c)
        except Exception as e:
            print("Live IRSA metadata unavailable:", e)

    if not candidates:
        print("No observation metadata. Skipping.")
        continue

    candidates = rank_pairs(candidates)
    metadata_file.write_text(json.dumps(candidates, default=str))

    print(f"Available candidates: {len(candidates)}")

    staging = d.ROOT / ".staging"
    staging.mkdir(exist_ok=True)

    attempts = 0

    for row in candidates:
        remaining = min(
            LIMIT - d.used(folder),
            d.LIMIT - d.used(d.ROOT)
        )

        if remaining < 60_000_000:
            break

        url = row["aws"]
        release = "dr1" if "/dr1/" in url else "qr3"
        filename = release + "_" + url.rsplit("/", 1)[-1]
        path = folder / filename

        if path.exists():
            continue

        if attempts >= 30:
            break
        attempts += 1

        temp = staging / filename

        print("AWS:", filename, flush=True)

        if not d.download(url, temp, remaining, REQUIRED):
            continue

        try:
            if not covers_target(temp, c):
                print("  Target outside detector. Rejected.")
                continue

            temp.replace(path)

            d.record(folder, {
                "file": filename,
                "aws_url": url,
                "original_url": row["url"],
                "mjd": row["time"],
                "detector": row["detector"],
                "release": release,
                "ra": c.ra.deg,
                "dec": c.dec.deg,
                "product_kind": "full_detector"
            })

            print("  Accepted: target inside FITS footprint.")

        except Exception as e:
            print("  FITS coverage validation failed:", e)

        finally:
            temp.unlink(missing_ok=True)

    print(f"Partition storage: {d.used(folder)/1e6:.2f} MB")

d.summary()
