import sys
import os
import json
import math
import hashlib
from pathlib import Path
from collections import defaultdict

import boto3
import requests
import astropy.units as u

from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy_healpix import HEALPix
from astroquery.ipac.irsa import Irsa
from botocore import UNSIGNED
from botocore.config import Config
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path.home() / "SphereX-data"
BUCKET = "nasa-irsa-spherex"
AWS = f"https://{BUCKET}.s3.amazonaws.com/"
IRSA = "https://irsa.ipac.caltech.edu/"
MB = 1_000_000

BUDGET = float(os.environ.get("SPHEREX_BUDGET_GB", "5"))
SCALE = BUDGET / 5
LIMIT = int(BUDGET * 0.92 * 1e9)

IMAGE_LIMIT = int(2500 * MB * SCALE)
CUBE_LIMIT = int(1200 * MB * SCALE)
CAL_LIMIT = int(900 * MB * SCALE)

TARGETS = [
    ("barnards_star", (269.4521, 4.6934), 0.15),
    ("epsilon_indi_b", "Epsilon Indi B", 0.15),
    ("wise_0855", (133.79512917, -7.24514722), 0.15),
    ("rw_aurigae", "RW Aurigae", 0.15),
    ("r_draconis", "R Draconis", 0.15),
    ("cygnus_x", "DR 21", 0.25),
    ("north_deep_field", (270.0, 66.5607), 0.25),
    ("south_polar_field", (90.0, -66.5607), 0.25),
    ("orion", "M42", 0.25),
    ("cosmos", (150.1163, 2.2058), 0.25),
    ("galactic_centre", "Sgr A*", 0.25),
]

CUBE_TARGETS = [
    ("cygnus_x", "DR 21"),
    ("orion", "M42"),
    ("cosmos", (150.1163, 2.2058)),
    ("galactic_centre", "Sgr A*"),
    ("andromeda", "M31"),
    ("taurus", (67.5, 25.0)),
    ("rho_ophiuchi", "rho Ophiuchi"),
    ("m101", "M101"),
    ("north_deep_field", (270.0, 66.5607)),
    ("south_polar_field", (90.0, -66.5607)),
]

s3 = boto3.client(
    "s3",
    region_name="us-east-1",
    config=Config(signature_version=UNSIGNED)
)

session = requests.Session()
session.mount("https://", HTTPAdapter(
    max_retries=Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[429, 502, 503, 504],
        allowed_methods=["GET"]
    )
))

def coord(target):
    if isinstance(target, tuple):
        return SkyCoord(*target, unit="deg", frame="icrs")
    return SkyCoord.from_name(target).icrs

def used(folder):
    return sum(
        p.stat().st_size
        for p in folder.rglob("*.fits")
        if p.is_file()
    )

def record(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "manifest.jsonl").open("a") as f:
        f.write(json.dumps(data, default=str) + "\n")

def download(url, path, allowance, required):
    if path.exists():
        return True

    allowance = min(allowance, LIMIT - used(ROOT))
    if allowance <= 0:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")

    try:
        with session.get(
            url, stream=True, timeout=(20, 90)
        ) as response:
            response.raise_for_status()

            expected = int(
                response.headers.get("Content-Length", 0)
            )
            if expected > allowance:
                return False

            received = 0
            digest = hashlib.sha256()

            with tmp.open("wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    received += len(chunk)
                    if received > allowance:
                        raise ValueError("Storage budget reached")
                    digest.update(chunk)
                    out.write(chunk)

        if expected and received != expected:
            raise ValueError("Incomplete transfer")

        with fits.open(tmp, memmap=True) as hdus:
            hdus.verify("exception")
            names = {h.name.upper() for h in hdus}
            if not required.issubset(names):
                raise ValueError(
                    f"Missing extensions: {required - names}"
                )

        tmp.replace(path)

        record(path.parent, {
            "file": path.name,
            "source_url": url,
            "bytes": received,
            "sha256": digest.hexdigest()
        })

        print(
            f"  Saved {path.name}: {received/MB:.2f} MB",
            flush=True
        )
        return True

    except Exception as exc:
        print(f"  Download failed: {exc}", flush=True)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def cloud_url(row):
    try:
        cloud = json.loads(str(row["cloud_access"]))["aws"]
        return (
            f"https://{cloud['bucket_name']}.s3.amazonaws.com/"
            f"{cloud['key']}"
        )
    except Exception:
        return None


def metadata(c, deep=False):
    collections = ["spherex_dr1", "spherex_qr3"]

    if deep:
        collections += [
            "spherex_dr1_deep",
            "spherex_qr3_deep"
        ]

    try:
        table = Irsa.query_sia(
            pos=(c, 1 * u.arcsec),
            collection=collections
        )

        return [{
            "url": str(r["access_url"]),
            "aws": cloud_url(r),
            "time": float(r["t_min"]),
            "detector": str(r["energy_bandpassname"])
        } for r in table]

    except Exception as exc:
        print(f"  SIA failed; trying TAP: {exc}")

        query = f"""
        SELECT a.uri, p.time_bounds_lower,
               p.energy_bandpassname
        FROM spherex.artifact a
        JOIN spherex.plane p
             ON a.planeid = p.planeid
        WHERE 1 = CONTAINS(
            POINT('ICRS', {c.ra.deg}, {c.dec.deg}), p.poly
        )
        ORDER BY p.time_bounds_lower
        """

        table = Irsa.query_tap(query).to_table()
        rows = []

        for r in table:
            uri = str(r["uri"]).strip()
            url = IRSA + uri.lstrip("/")
            if "/ibe/data/spherex/" not in url:
                continue

            key = url.split("/ibe/data/spherex/", 1)[1]
            rows.append({
                "url": url,
                "aws": AWS + key,
                "time": float(r["time_bounds_lower"]),
                "detector": str(r["energy_bandpassname"])
            })

        return rows


def rank(rows):
    groups = defaultdict(list)
    seen = set()

    for r in rows:
        url = r["url"]
        if not url.endswith(".fits"):
            continue
        if "/dr1/" not in url and "/qr3/" not in url:
            continue
        if url in seen or not math.isfinite(r["time"]):
            continue

        seen.add(url)
        groups[r["detector"]].append(r)

    # Prefer observations separated widely in time.
    ranked = []

    for group in groups.values():
        remaining = sorted(group, key=lambda r: r["time"])
        chosen = []

        while remaining:
            if not chosen:
                i = 0
            else:
                i = max(
                    range(len(remaining)),
                    key=lambda j: min(
                        abs(remaining[j]["time"] - r["time"])
                        for r in chosen
                    )
                )
            chosen.append(remaining.pop(i))

        ranked.append(chosen)

    # Interleave the six detectors.
    return [
        g[i]
        for i in range(max(map(len, ranked), default=0))
        for g in ranked
        if i < len(g)
    ]


def images():
    base = ROOT / "observations"

    for index, (name, target, width) in enumerate(TARGETS):
        folder = base / name
        folder.mkdir(parents=True, exist_ok=True)

        allocation = int(
            (300 if index < 5 else 1000/6) * MB * SCALE
        )

        if used(folder) >= allocation:
            continue

        print(f"\n=== IMAGES: {name} ===", flush=True)

        try:
            c = coord(target)
            metadata_file = folder / "archive_records.json"

            try:
                rows = metadata(c, deep="field" in name)
                if rows:
                    metadata_file.write_text(json.dumps(rows))
            except Exception:
                if not metadata_file.exists():
                    raise
                rows = json.loads(metadata_file.read_text())

            candidates = rank(rows)

            print(f"  {len(candidates)} archive candidates")

            successes = 0
            failures = 0
            attempts = 0

            required = {
                "IMAGE", "FLAGS", "VARIANCE",
                "ZODI", "EPSF", "WCS-WAVE"
            }

            for r in candidates:
                available = min(
                    allocation - used(folder),
                    IMAGE_LIMIT - used(base)
                )

                if available < MB:
                    break

                release = "dr1" if "/dr1/" in r["url"] else "qr3"
                filename = release + "_" + r["url"].split("/")[-1]
                path = folder / filename

                if path.exists():
                    continue

                if attempts >= int(400 * SCALE):
                    break

                attempts += 1

                cutout = (
                    r["url"] +
                    f"?center={c.ra.deg},{c.dec.deg}d"
                    f"&size={width}"
                )

                # Prefer small original-grid IRSA cutouts.
                ok = download(
                    cutout, path, available, required
                )

                kind = "cutout"

                # AWS fallback for failed IRSA requests.
                if not ok and r["aws"] and available >= 75 * MB:
                    ok = download(
                        r["aws"], path, available, required
                    )
                    kind = "full_detector"

                if ok:
                    successes += 1
                    failures = 0

                    record(folder, {
                        "file": filename,
                        "original_url": r["url"],
                        "aws_url": r["aws"],
                        "mjd": r["time"],
                        "detector": r["detector"],
                        "ra": c.ra.deg,
                        "dec": c.dec.deg,
                        "release": release,
                        "product_kind": kind,
                        "requested_cutout_deg": width
                    })
                else:
                    failures += 1
                    if failures >= 5:
                        print("  Repeated failures; skipping region.")
                        break

            print(
                f"  New images: {successes}; "
                f"region size: {used(folder)/MB:.1f} MB"
            )

        except Exception as exc:
            print(f"  Region failed: {exc}")


def cubes():
    folder = ROOT / "cubes"
    folder.mkdir(exist_ok=True)

    healpix = HEALPix(
        nside=32, order="nested", frame="icrs"
    )

    required = {
        "IMAGE", "NHITS", "FLAGS",
        "SPECTRAL_CHANNELS", "WCS-WAVE"
    }

    for name, target in CUBE_TARGETS:
        print(f"\n=== CUBE: {name} ===")

        try:
            c = coord(target)
            pix = int(healpix.skycoord_to_healpix(c))
            directory = (pix // 10000) * 10000

            key = (
                "dr1/hips5_cube/hips_20261026/"
                f"Norder5/Dir{directory}/Npix{pix}_cube.fits"
            )

            path = folder / f"Npix{pix}_cube.fits"
            already_present = path.exists()

            if not already_present:
                remaining = CUBE_LIMIT - used(folder)

                if remaining < 320 * MB:
                    print("  Cube allocation nearly exhausted.")
                    break

                ok = download(AWS + key, path, remaining, required)

                if not ok:
                    ok = download(
                        IRSA + "ibe/data/spherex/" + key,
                        path, remaining, required
                    )

                if not ok:
                    continue

            record(folder, {
                "region": name,
                "file": path.name,
                "healpix": pix,
                "ra": c.ra.deg,
                "dec": c.dec.deg,
                "release": "dr1"
            })

        except Exception as exc:
            print(f"  Cube failed: {exc}")


def calibrations():
    folder = ROOT / "calibrations"
    folder.mkdir(exist_ok=True)

    products = [
        ("spectral_wcs", {"CWAVE", "CBAND"}),
        ("solid_angle_pixel_map", set()),
        ("epsf", set())
    ]

    for product, required in products:
        for release in ["dr1", "qr3"]:
            print(f"\n=== CALIBRATION: {release}/{product} ===")

            try:
                pages = s3.get_paginator(
                    "list_objects_v2"
                ).paginate(
                    Bucket=BUCKET,
                    Prefix=f"{release}/{product}/"
                )

                for page in pages:
                    for item in page.get("Contents", []):
                        key = item["Key"]

                        if not key.endswith(".fits"):
                            continue

                        remaining = CAL_LIMIT - used(folder)
                        path = folder / key

                        if path.exists():
                            continue

                        if item["Size"] > remaining:
                            continue

                        download(
                            AWS + key, path, remaining, required
                        )

            except Exception as exc:
                print(f"  Calibration listing failed: {exc}")


def summary():
    print("\n========== DATASET SUMMARY ==========")

    for name in ["observations", "cubes", "calibrations"]:
        folder = ROOT / name
        count = len(list(folder.rglob("*.fits")))
        size = used(folder)

        print(f"{name:16} {count:5} FITS  {size/1e9:.3f} GB")

    print(f"Total FITS: {used(ROOT)/1e9:.3f} GB")
    print(f"FITS limit: {LIMIT/1e9:.3f} GB")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode not in {"images", "cubes", "calibrations", "all", "status"}:
        sys.exit("Use images, cubes, calibrations, all or status")

    if mode in ("all", "images"):
        images()

    if mode in ("all", "cubes"):
        cubes()

    if mode in ("all", "calibrations"):
        calibrations()

    summary()
