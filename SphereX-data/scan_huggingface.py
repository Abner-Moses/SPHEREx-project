import json
import math
from io import BytesIO
from pathlib import Path

import requests
from huggingface_hub import HfApi, hf_hub_url
from astropy.io import fits
from astropy.wcs import WCS, NoConvergence
from astropy.coordinates import SkyCoord

REPO = "AkshithSurya/spherex-projects"

targets = {
    "barnards_star": (269.4521, 4.6934),
    "epsilon_indi_b": (331.04414583, -56.78284417),
    "wise_0855": (133.79512917, -7.24514722),
    "rw_aurigae": (76.956525, 30.401438),
    "r_draconis": (248.1676125, 66.75497944)
}

coords = {
    name: SkyCoord(ra, dec, unit="deg")
    for name, (ra, dec) in targets.items()
}

api = HfApi()
revision = api.dataset_info(REPO).sha

paths = [
    p for p in api.list_repo_files(
        REPO, repo_type="dataset", revision=revision
    )
    if p.startswith("SPHEREx-D") and p.endswith(".fits")
]

print("FITS files available:", len(paths))
print("Revision:", revision)

matches = {name: [] for name in targets}
session = requests.Session()
failed = 0

for i, path in enumerate(paths, 1):
    url = hf_hub_url(
        REPO, path,
        repo_type="dataset", revision=revision
    )

    try:
        # Read only the beginning of the FITS file.
        with session.get(
            url,
            headers={"Range": "bytes=0-262143"},
            stream=True,
            timeout=(15, 30)
        ) as response:
            response.raise_for_status()
            raw = response.raw.read(262144)

        source = BytesIO(raw)
        primary = fits.Header.fromfile(source)

        if primary.get("NAXIS", 0) != 0:
            header = primary
        else:
            header = fits.Header.fromfile(source)

        width = header.get(
            "ZNAXIS1", header.get("NAXIS1", 0)
        )
        height = header.get(
            "ZNAXIS2", header.get("NAXIS2", 0)
        )

        wcs = WCS(header, naxis=2).celestial

        if not wcs.has_celestial or not width or not height:
            raise ValueError("No usable image WCS")

        for name, c in coords.items():
            center = wcs.pixel_to_world(
                (width - 1) / 2,
                (height - 1) / 2
            )

            corners = wcs.pixel_to_world(
                [0, width-1, width-1, 0],
                [0, 0, height-1, height-1]
            )

            radius = center.separation(corners).deg.max()

            # Reject distant targets before iterative inversion.
            if center.separation(c).deg > radius + 0.2:
                continue

            print("NEAR FOOTPRINT:", name, path, flush=True)

            try:
                x, y = wcs.all_world2pix(
                    c.ra.deg, c.dec.deg, 0,
                    ra_dec_order=True,
                    maxiter=50,
                    detect_divergence=True
                )
            except NoConvergence:
                print("WCS inversion failed:", name, path)
                continue
            x, y = float(x), float(y)

            if (
                math.isfinite(x)
                and math.isfinite(y)
                and 0 <= x < width
                and 0 <= y < height
            ):
                matches[name].append({
                    "file": path,
                    "url": url,
                    "revision": revision,
                    "pixel": [x, y]
                })
                print("MATCH:", name, path, flush=True)

    except Exception as exc:
        failed += 1
        print("Header unavailable:", path, str(exc))

    if i % 10 == 0 or i == len(paths):
        print(f"Scanned {i}/{len(paths)}", flush=True)
        Path("hf_matches.json").write_text(
            json.dumps(matches, indent=2)
        )

Path("hf_matches.json").write_text(
    json.dumps(matches, indent=2)
)

print("\n=== RESULTS ===")
for name, rows in matches.items():
    print(name, len(rows), "matching observations")

print("Header failures:", failed)
