import io
import re
import json
import math
import random
import warnings
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from astropy.io import fits
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord, get_sun
from astropy.time import Time

import astropy.units as u
import download as d

warnings.filterwarnings("ignore")

BUCKET = "nasa-irsa-spherex"
WORKERS = 12
HEADER_BYTES = 65536
MAX_HEADERS_PER_PERIOD = 600
MAX_PERIODS_PER_SEASON = 3
TARGET_LIMIT = 480_000_000

TARGETS = {
    "epsilon_indi_b": (331.04414583, -56.78284417, 6),
    "wise_0855": (133.79512917, -7.24514722, 6),
    "rw_aurigae": (76.956525, 30.401438, 2),
    "r_draconis": (248.1676125, 66.75497944, 2),
}

REQUIRED = {
    "IMAGE", "FLAGS", "VARIANCE",
    "ZODI", "EPSF", "WCS-WAVE"
}

s3 = boto3.client(
    "s3",
    region_name="us-east-1",
    config=Config(
        signature_version=UNSIGNED,
        connect_timeout=10,
        read_timeout=20,
        max_pool_connections=WORKERS + 2,
        retries={"max_attempts": 2}
    )
)

rng = random.Random(42)

def prefixes(prefix):
    paginator = s3.get_paginator("list_objects_v2")
    result = []

    for page in paginator.paginate(
        Bucket=BUCKET,
        Prefix=prefix,
        Delimiter="/"
    ):
        result.extend(
            item["Prefix"]
            for item in page.get("CommonPrefixes", [])
        )

    return result


def fits_keys(prefix):
    paginator = s3.get_paginator("list_objects_v2")
    keys = []

    for page in paginator.paginate(
        Bucket=BUCKET, Prefix=prefix
    ):
        keys.extend(
            item["Key"]
            for item in page.get("Contents", [])
            if item["Key"].endswith(".fits")
        )

    return keys


def headers(raw):
    stream = io.BytesIO(raw)
    primary = fits.Header.fromfile(stream)

    if primary.get("NAXIS", 0) == 0:
        image = fits.Header.fromfile(stream)
    else:
        image = primary

    return primary, image


def footprint(header, coord):
    width = int(header.get(
        "ZNAXIS1", header.get("NAXIS1", 0)
    ))
    height = int(header.get(
        "ZNAXIS2", header.get("NAXIS2", 0)
    ))

    w = WCS(header, naxis=2).celestial

    if not w.has_celestial or width < 2 or height < 2:
        raise ValueError("Invalid celestial image WCS")

    center = w.pixel_to_world(
        (width - 1) / 2, (height - 1) / 2
    )

    # Individual detectors cover roughly 3.5 x 3.5 deg.
    # Reject distant coordinates before SIP inversion.
    if center.separation(coord).deg > 4:
        return None

    x, y = w.all_world2pix(
        coord.ra.deg, coord.dec.deg, 0,
        ra_dec_order=True,
        maxiter=50,
        detect_divergence=True
    )

    x, y = float(x), float(y)

    if not (
        math.isfinite(x) and math.isfinite(y)
        and 0 <= x < width and 0 <= y < height
    ):
        return None

    # Check the inverse solution against the sky position.
    reconstructed = w.pixel_to_world(x, y)

    if reconstructed.separation(coord).arcsec > 1:
        return None

    return x, y


def observation_mjd(primary, image):
    for header in (image, primary):
        for k in ("MJDOBS", "MJD-OBS", "MJD_OBS"):
            if k in header:
                try:
                    value = float(header[k])
                    if math.isfinite(value):
                        return value
                except (TypeError, ValueError):
                    pass

        for k in ("DATE-OBS", "DATEOBS"):
            if k in header:
                try:
                    return float(Time(header[k]).mjd)
                except Exception:
                    pass

    return None


def probe(key, coord):
    try:
        response = s3.get_object(
            Bucket=BUCKET,
            Key=key,
            Range=f"bytes=0-{HEADER_BYTES - 1}"
        )

        body = response["Body"]

        try:
            raw = body.read()
        finally:
            body.close()

        primary, image = headers(raw)
        pixel = footprint(image, coord)

        if pixel is None:
            return "outside", None

        mjd = observation_mjd(primary, image)

        if mjd is None:
            return "no_timestamp", None

        return "match", {
            "key": key,
            "aws": d.AWS + key,
            "mjd": mjd,
            "pixel": pixel,
            "release": key.split("/")[0]
        }

    except Exception as e:
        return "error", str(e)


# Verify header interpretation on existing observations.
print("=== LOCAL WCS CONTROL TEST ===", flush=True)

barnard = SkyCoord(269.4521, 4.6934, unit="deg")
local = list(
    (d.ROOT / "observations" / "barnards_star").glob("*.fits")
)

control_matches = 0

for path in local:
    try:
        with path.open("rb") as f:
            primary, image = headers(f.read(HEADER_BYTES))

        if footprint(image, barnard) is not None:
            control_matches += 1

    except Exception:
        pass

print(
    f"Barnard control: {control_matches}/{len(local)} "
    "matching local FITS",
    flush=True
)

if not control_matches:
    raise SystemExit(
        "WCS control failed. Do not scan the archive yet."
    )


print("\n=== LISTING AWS PLANNING PERIODS ===", flush=True)

periods = []

for release in ("dr1", "qr3"):
    for prefix in prefixes(f"{release}/level2/"):
        name = prefix.rstrip("/").split("/")[-1]
        match = re.match(r"(\d{4})W(\d{2})_([12])", name)

        if not match:
            continue

        year, week, half = map(int, match.groups())

        day = date.fromisocalendar(year, week, 1)
        day += timedelta(days=1 if half == 1 else 5)

        periods.append({
            "prefix": prefix,
            "date": day,
            "name": name,
            "release": release
        })

print("Planning periods:", len(periods), flush=True)


def solar_score(period, coord):
    sun = get_sun(Time(period["date"].isoformat()))

    target = coord.transform_to(sun.frame)

    elongation = target.separation(sun).deg
    return abs(elongation - 90)


def get_period_keys(period, detector):
    versions = prefixes(period["prefix"])

    versions = [
        v for v in versions if "/l2b-v27-" in v
    ]

    result = []

    for version in sorted(versions, reverse=True):
        result.extend(fits_keys(
            f"{version}{detector}/"
        ))

    result = list(set(result))
    rng.shuffle(result)

    return result


# Preserve discovered matches for future runs.
index_path = d.ROOT / "aws_target_matches.json"

if index_path.exists():
    index = json.loads(index_path.read_text())
else:
    index = {}

with ThreadPoolExecutor(max_workers=WORKERS) as pool:

    for name, (ra, dec, detector) in TARGETS.items():

        print(f"\n=== {name.upper()} ===", flush=True)

        coord = SkyCoord(ra, dec, unit="deg")

        folder = d.ROOT / "observations" / name
        folder.mkdir(parents=True, exist_ok=True)

        found = index.setdefault(name, [])
        seen = {r["key"] for r in found}

        # Divide the survey into broad six-month periods.
        seasons = {}

        for period in periods:
            season = (
                period["date"] - date(2025, 5, 1)
            ).days // 183

            seasons.setdefault(season, []).append(period)

        for season, candidates in sorted(seasons.items()):

            candidates.sort(
                key=lambda p: solar_score(p, coord)
            )

            season_matches = 0

            for period in candidates[:MAX_PERIODS_PER_SEASON]:

                if season_matches >= 2:
                    break

                print(
                    "\nScanning:",
                    period["name"],
                    f"D{detector}",
                    "solar score:",
                    round(solar_score(period, coord), 2),
                    flush=True
                )

                keys = get_period_keys(period, detector)
                keys = [k for k in keys if k not in seen]
                keys = keys[:MAX_HEADERS_PER_PERIOD]

                errors = 0

                for start in range(0, len(keys), 48):

                    if season_matches >= 2:
                        break

                    batch = keys[start:start + 48]

                    results = list(pool.map(
                        lambda k: probe(k, coord), batch
                    ))

                    for status, result in results:

                        if status == "error":
                            errors += 1

                        if status != "match":
                            continue

                        if result["key"] in seen:
                            continue

                        found.append(result)
                        seen.add(result["key"])

                        index_path.write_text(
                            json.dumps(index, indent=2)
                        )

                        print(
                            "MATCH:", name,
                            result["key"],
                            "MJD:", result["mjd"],
                            flush=True
                        )

                        season_matches += 1

                    print(
                        f"Scanned {min(start+48,len(keys))}"
                        f"/{len(keys)}; "
                        f"matches={season_matches}; "
                        f"errors={errors}",
                        flush=True
                    )

                    if errors >= 48 and errors > start / 2:
                        print("Excessive errors; skipping period.")
                        break

        # Select observations with widely separated timestamps.
        remaining = TARGET_LIMIT - d.used(folder)

        ordered = sorted(found, key=lambda r: r["mjd"])
        ranked = []

        if ordered:
            ranked.append(ordered.pop(0))

        while ordered:
            best = max(
                ordered,
                key=lambda r: min(
                    abs(r["mjd"] - q["mjd"])
                    for q in ranked
                )
            )
            ranked.append(best)
            ordered.remove(best)

        for row in ranked:

            remaining = min(
                TARGET_LIMIT - d.used(folder),
                d.LIMIT - d.used(d.ROOT)
            )

            if remaining < 60_000_000:
                break

            filename = (
                row["release"] + "_" +
                row["key"].rsplit("/", 1)[-1]
            )

            path = folder / filename

            if path.exists():
                continue

            if d.download(
                row["aws"], path, remaining, REQUIRED
            ):
                d.record(folder, {
                    **row,
                    "file": filename,
                    "detector": f"D{detector}",
                    "ra": ra,
                    "dec": dec,
                    "product_kind": "full_detector"
                })

        print(
            name, "total indexed observations:", len(found),
            flush=True
        )

print("\n=== FINAL RESULTS ===")

for name, rows in index.items():
    print(name, len(rows), "matching observations")

d.summary()
