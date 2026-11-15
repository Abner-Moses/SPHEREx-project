"""SPHEREx image retrieval and processing.

Pipeline: archive search (IRSA SIA) -> resumable cached download -> FITS processing -> ``Product``.

A ``Product`` carries the display image plus everything a client needs to place it on the sky
and to interpret it: a WCS sample mesh (exact, including SIP distortion), pixel scale, wavelength
coverage, observation time, and the stretch limits that map the 8-bit PNG back to MJy/sr.

Design notes
------------
* Downloads stream to ``<cache>/<name>.part`` and resume from where they stopped, even across
  server restarts, so a slow link only has to succeed once.
* One download per product URL at a time (single flight); other callers wait on it.
* Every long step honours a ``threading.Event`` so an abandoned HTTP request can stop the work.
* The pixel mask follows the FLAGS extension of the real files: ``SOURCE`` (bit 21) is
  informational ("pixel mapped to a known source") and must NOT be masked; defect and artifact
  bits are.  ``FULLSAMPLE`` (bit 12) only says extra data exists, so it is not a defect either.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
import math
import os
import re
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import requests
from astropy.io import fits
from astropy.io.votable import parse_single_table
from astropy.wcs import WCS

LOG = logging.getLogger(__name__)
# astropy logs a long INFO note about SIP/CTYPE for the secondary wavelength WCS; it is harmless here.
logging.getLogger("astropy").setLevel(logging.WARNING)

IRSA_SIA_URL = os.getenv("SPHEREX_SIA_URL", "https://irsa.ipac.caltech.edu/SIA")
COLLECTION = os.getenv("SPHEREX_COLLECTION", "spherex_qr3")
MAX_FITS_BYTES = 250 * 1024 * 1024
DEFAULT_SELECT_RADIUS_DEG = 0.05
MAX_SEARCH_RADIUS_DEG = 2.0
MESH_N = 65  # 65x65 nodes: bilinear error on a real SIP WCS is ~0.02 arcsec (<0.01 pixel)
DEFAULT_DOWNLOAD_TIMEOUT_S = float(os.getenv("SPHEREX_DOWNLOAD_TIMEOUT", "900"))
SIA_TIMEOUT_S = 60.0
SEARCH_LIMIT = 500  # rows per archive search; the list endpoint and observe() share one cached search
SEARCH_CACHE_TTL_S = 600.0
SEARCH_CACHE_SIZE = 64
PRODUCT_CACHE_SIZE = 8

# FLAGS bit names, from the MSKN/MSKD cards of a real SPHEREx L2 file.
FLAG_BITS = {
    0: "TRANSIENT", 1: "OVERFLOW", 2: "SUR_ERROR", 6: "NONFUNC", 7: "DICHROIC", 9: "MISSING_DATA",
    10: "HOT", 11: "COLD", 12: "FULLSAMPLE", 14: "PHANMISS", 15: "NONLINEAR", 17: "PERSIST",
    19: "OUTLIER", 20: "CROSSTALK", 21: "SOURCE", 22: "GHOST", 23: "GHOST_FPA", 24: "GHOST_EXT",
    25: "STREAK", 26: "BLOOM", 27: "SNOWBALL", 28: "HALO",
}
INFORMATIONAL_BITS = {12, 21}  # FULLSAMPLE, SOURCE: not defects
BAD_MASK = sum(1 << b for b in FLAG_BITS if b not in INFORMATIONAL_BITS)

_MODES = ("auto", "live", "mock")


class PipelineError(RuntimeError):
    """Expected archive or image-processing failure whose message is safe to show to clients."""


class Cancelled(PipelineError):
    """The caller went away; work was stopped."""


class NotFound(PipelineError):
    """Nothing matched (no coverage, unknown observation id, ...)."""


# ----------------------------------------------------------------------------- HTTP

_REQUESTS = threading.local()


def _http() -> requests.Session:
    """One session per worker thread: connection reuse without sharing session state."""
    session = getattr(_REQUESTS, "session", None)
    if session is None:
        session = requests.Session()
        _REQUESTS.session = session
    return session


# ----------------------------------------------------------------------------- geometry


def _unit(ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation (haversine: stable for tiny angles)."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(ra2 - ra1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def parse_region(region: object) -> list[tuple[float, float]]:
    """Vertices (ra, dec) of an SIA ``POLYGON ICRS ra dec ra dec ...`` string; empty if not a polygon."""
    tokens = str(region).replace(",", " ").split()
    if not tokens or tokens[0].upper() != "POLYGON":
        return []
    numbers: list[float] = []
    for token in tokens[1:]:
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    if len(numbers) < 6 or len(numbers) % 2:
        return []
    return [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers), 2)]


def region_contains(vertices: list[tuple[float, float]], ra: float, dec: float) -> bool:
    """Whether a spherical polygon contains (ra, dec).

    Edges are great-circle arcs, which a gnomonic projection centred on the test point maps to
    straight lines, so a plain ray cast in that tangent plane is exact.  It is also free of the RA
    wrap and pole problems a flat (ra, dec) test has.  Polygons with any vertex 90 degrees or more
    from the point are rejected (SPHEREx footprints are ~3.5 degrees across).
    """
    if len(vertices) < 3:
        return False
    ra_r, dec_r = math.radians(ra), math.radians(dec)
    v0 = _unit(ra, dec)
    east = (-math.sin(ra_r), math.cos(ra_r), 0.0)
    north = (-math.sin(dec_r) * math.cos(ra_r), -math.sin(dec_r) * math.sin(ra_r), math.cos(dec_r))
    pts = []
    for lon, lat in vertices:
        v = _unit(lon, lat)
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


# ----------------------------------------------------------------------------- archive search


def mjd_to_iso(mjd: float | None) -> str | None:
    if mjd is None or not math.isfinite(mjd):
        return None
    return (dt.datetime(1858, 11, 17, tzinfo=dt.UTC) + dt.timedelta(days=float(mjd))).isoformat(timespec="seconds")


def _cell(row, name: str, cast=float):
    try:
        value = row[name]
        if np.ma.is_masked(value):
            return None
        return cast(value)
    except (KeyError, ValueError, TypeError):
        return None


def _parse_sia_table(content: bytes, ra: float, dec: float) -> list[dict]:
    table = parse_single_table(io.BytesIO(content)).to_table(use_names_over_ids=True)
    out = []
    for row in table:
        url = _cell(row, "access_url", str)
        if not url or url.strip().lower() in {"", "none", "nan"}:
            continue
        vertices = parse_region(_cell(row, "s_region", str) or "")
        band = _cell(row, "energy_bandpassname", str) or ""
        detector = re.search(r"D(\d)\s*$", band)
        em_min, em_max = _cell(row, "em_min"), _cell(row, "em_max")
        s_ra, s_dec = _cell(row, "s_ra"), _cell(row, "s_dec")
        t_min, t_max = _cell(row, "t_min"), _cell(row, "t_max")
        out.append({
            "obs_id": _cell(row, "obs_id", str),
            "detector": int(detector.group(1)) if detector else None,
            "band": band or None,
            "wavelength_um": [em_min * 1e6, em_max * 1e6] if em_min is not None and em_max is not None else None,
            "resolving_power": _cell(row, "em_res_power"),
            "mjd_start": t_min,
            "mjd_end": t_max,
            "date_obs": mjd_to_iso(t_min),
            "exposure_s": _cell(row, "t_exptime"),
            "pixel_scale_arcsec": _cell(row, "s_pixel_scale"),
            "fov_deg": _cell(row, "s_fov"),
            "size_px": [_cell(row, "s_xel1", int), _cell(row, "s_xel2", int)],
            "target_name": _cell(row, "target_name", str),
            "center": [s_ra, s_dec] if s_ra is not None and s_dec is not None else None,
            "footprint": [[lon, lat] for lon, lat in vertices],
            "covers": region_contains(vertices, ra, dec),
            "separation_deg": separation_deg(ra, dec, s_ra, s_dec) if s_ra is not None and s_dec is not None else None,
            "access_url": url.strip(),
        })
    return out


_SEARCH_CACHE: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
_SEARCH_LOCK = threading.Lock()


def _local_observations(ra: float, dec: float) -> list[dict]:
    """Adapt the mounted science index to the sky overlay's existing product contract."""
    import sys
    local = sys.modules.get("local_science")
    if local is None or not local.ROOT.exists():
        return []
    records = local.discover(ra, dec)["observations"]
    indexed = {row["id"]: row for row in local.index() if row["kind"] == "observations"}
    rows = []
    for record in records:
        row = indexed[record["id"]]
        with fits.open(row["path"], memmap=True) as hdus:
            header = hdus["IMAGE"].header
            wcs = WCS(header).celestial
            detector = int(header["DETECTOR"])
            center = [float(header["CRVAL1"]), float(header["CRVAL2"])]
            rows.append({"obs_id": row["observation_id"], "detector": detector,
                "band": f"SPHEREx-D{detector}", "wavelength_um": None,
                "mjd_start": header.get("MJD-OBS"), "date_obs": header.get("DATE-OBS"),
                "center": center, "footprint": wcs.calc_footprint().tolist(),
                "covers": True, "separation_deg": separation_deg(ra, dec, *center),
                "access_url": row["source_url"], "collection": record["collection"], "data_origin": "local dataset"})
    return rows


def search_observations(ra: float, dec: float, radius_deg: float = DEFAULT_SELECT_RADIUS_DEG, maxrec: int = SEARCH_LIMIT) -> list[dict]:
    """Every SPHEREx L2 image whose footprint is within ``radius_deg`` of the point (any date, any band)."""
    _check_coordinates(ra, dec)
    if not (0.0 < radius_deg <= MAX_SEARCH_RADIUS_DEG):
        raise PipelineError(f"Search radius must be between 0 and {MAX_SEARCH_RADIUS_DEG:g} degrees.")
    maxrec = max(1, min(int(maxrec), 500))
    local_rows = _local_observations(ra, dec)
    if local_rows:
        return local_rows[:maxrec]
    key = (round(ra, 5), round(dec, 5), round(radius_deg, 5), maxrec)

    def cached() -> list[dict] | None:
        with _SEARCH_LOCK:
            hit = _SEARCH_CACHE.get(key)
            if hit and time.monotonic() - hit[0] < SEARCH_CACHE_TTL_S:
                _SEARCH_CACHE.move_to_end(key)
                return hit[1]
        return None

    if (rows := cached()) is not None:
        return rows
    # Single flight: the UI asks for the list and for the image at the same moment; the second caller waits for the first.
    with _lock_for(f"search:{key}"):
        if (rows := cached()) is not None:
            return rows
        rows = _query_archive(ra, dec, radius_deg, maxrec)
        with _SEARCH_LOCK:
            _SEARCH_CACHE[key] = (time.monotonic(), rows)
            while len(_SEARCH_CACHE) > SEARCH_CACHE_SIZE:
                _SEARCH_CACHE.popitem(last=False)
    return rows


def _query_archive(ra: float, dec: float, radius_deg: float, maxrec: int) -> list[dict]:
    if os.getenv("SPHEREX_OFFLINE", "0") == "1":
        raise NotFound("Offline: no indexed local SPHEREx image covers this position.")
    try:
        response = _http().get(
            IRSA_SIA_URL,
            params={
                "COLLECTION": COLLECTION,
                "POS": f"CIRCLE {ra:.8f} {dec:.8f} {radius_deg:.8f}",
                "CALIB": "2",
                "MAXREC": str(maxrec),
                "RESPONSEFORMAT": "VOTABLE",
            },
            timeout=(5.0, SIA_TIMEOUT_S),
        )
        response.raise_for_status()
        return _parse_sia_table(response.content, ra, dec)
    except requests.RequestException as exc:
        raise PipelineError(f"SPHEREx archive request failed: {exc}") from exc
    except (ValueError, KeyError) as exc:
        raise PipelineError(f"The archive returned a reply this server could not read: {exc}") from exc


def choose_observation(candidates: list[dict], obs_id: str | None = None, detector: int | None = None) -> dict:
    """The requested observation, else the covering one whose centre is nearest the point.

    ``obs_id`` alone is NOT unique: one exposure yields an image per detector (one band each), so a
    specific image is (obs_id, detector).
    """
    if obs_id:
        matches = [c for c in candidates if c["obs_id"] == obs_id and c["covers"] and (detector is None or c["detector"] == detector)]
        if not matches:
            where = f"{obs_id} (detector {detector})" if detector is not None else obs_id
            raise NotFound(f"Observation {where} was not found covering this position.")
        return min(matches, key=lambda c: (c["detector"] or 0, c["access_url"]))
    covering = [c for c in candidates if c["covers"]]
    if not candidates:
        raise NotFound("IRSA found no SPHEREx images at these coordinates.")
    if not covering:
        raise NotFound("IRSA found nearby SPHEREx products, but none cover the requested coordinate.")
    return min(covering, key=lambda c: (c["separation_deg"] if c["separation_deg"] is not None else math.inf, c["access_url"]))


# ----------------------------------------------------------------------------- download cache

_URL_LOCKS: dict[str, threading.Lock] = {}
_URL_LOCKS_GUARD = threading.Lock()


def cache_dir() -> Path:
    root = Path(os.getenv("SPHEREX_CACHE_DIR") or Path(__file__).resolve().with_name(".cache"))
    path = root / "fits"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_name(url: str) -> str:
    """Stable file name for a product URL (hash prefix avoids collisions between versions)."""
    base = Path(urlparse(url).path).name or "product.fits"
    return f"{hashlib.sha1(url.encode()).hexdigest()[:10]}_{re.sub(r'[^A-Za-z0-9_.-]', '_', base)}"


def _lock_for(url: str) -> threading.Lock:
    with _URL_LOCKS_GUARD:
        return _URL_LOCKS.setdefault(url, threading.Lock())


def _check_alive(cancel: threading.Event | None, deadline: float) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("The request was cancelled.")
    if time.monotonic() > deadline:
        raise PipelineError("The SPHEREx download hit its time limit. The partial file is kept, so trying again resumes it.")


def download_product(url: str, timeout: float = DEFAULT_DOWNLOAD_TIMEOUT_S, cancel: threading.Event | None = None) -> Path:
    """Download (or resume) one FITS product into the cache and return its path."""
    import sys
    local = sys.modules.get("local_science")
    if local is not None and local.ROOT.exists():
        match = next((row for row in local.index() if row["kind"] == "observations" and row["source_url"] == url), None)
        if match is not None:
            return match["path"]
    final = cache_dir() / cache_name(url)
    if final.exists():
        return final
    if os.getenv("SPHEREX_OFFLINE", "0") == "1":
        raise NotFound("Offline: this SPHEREx product is not available locally.")
    deadline = time.monotonic() + timeout
    lock = _lock_for(url)
    while not lock.acquire(timeout=0.5):  # another request is already fetching this product
        _check_alive(cancel, deadline)
    try:
        if final.exists():  # finished by the request we waited for
            return final
        part = final.with_name(final.name + ".part")
        last_error: Exception | None = None
        for attempt in range(1, 6):
            _check_alive(cancel, deadline)
            start = part.stat().st_size if part.exists() else 0
            try:
                response = _http().get(url, headers={"Range": f"bytes={start}-"} if start else {}, timeout=(5.0, 30.0), stream=True)
                if response.status_code == 416:  # the .part file is already the whole product
                    os.replace(part, final)
                    return final
                response.raise_for_status()
                if start and response.status_code == 200:  # server ignored Range: start over
                    start = 0
                total = None
                content_range = response.headers.get("Content-Range", "")
                if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
                    total = int(content_range.rsplit("/", 1)[1])
                elif response.headers.get("Content-Length", "").isdigit():
                    total = start + int(response.headers["Content-Length"])
                if total is not None and total > MAX_FITS_BYTES:
                    raise PipelineError("The selected FITS product exceeds the download limit.")
                with part.open("ab" if start else "wb") as handle:
                    for chunk in response.iter_content(1 << 20):
                        _check_alive(cancel, deadline)
                        handle.write(chunk)
                        if handle.tell() > MAX_FITS_BYTES:
                            raise PipelineError("The selected FITS product exceeds the download limit.")
                size = part.stat().st_size
                if total is None or size >= total:
                    os.replace(part, final)
                    return final
                last_error = RuntimeError(f"stopped at {size} of {total} bytes")
            except requests.RequestException as exc:
                last_error = exc
            LOG.warning("SPHEREx download interrupted (%s); resuming, attempt %d/5", last_error, attempt + 1)
        raise PipelineError(f"SPHEREx download failed after 5 attempts: {last_error}")
    finally:
        lock.release()


# ----------------------------------------------------------------------------- processing


@dataclass
class Product:
    id: str
    source: str  # "irsa" | "mock"
    image: np.ndarray  # uint8, row 0 = top
    mask: np.ndarray  # bool, True where a defect/artifact flag is set
    wcs: WCS
    lo: float  # flux (MJy/sr) mapped to 0
    hi: float  # flux (MJy/sr) mapped to 255
    info: dict = field(default_factory=dict)
    fallback_reason: str | None = None
    flux: np.ndarray | None = field(default=None, repr=False)  # float32 MJy/sr, zodi-subtracted, NaN where masked (archive only)
    _png: bytes | None = field(default=None, repr=False)
    _mask_png: bytes | None = field(default=None, repr=False)

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    def png_bytes(self) -> bytes:
        if self._png is None:
            ok, encoded = cv2.imencode(".png", self.image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            if not ok:
                raise RuntimeError("PNG encoding failed")
            self._png = encoded.tobytes()
        return self._png

    def mask_png_bytes(self) -> bytes:
        if self._mask_png is None:
            ok, encoded = cv2.imencode(".png", self.mask.astype(np.uint8) * 255, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            if not ok:
                raise RuntimeError("PNG encoding failed")
            self._mask_png = encoded.tobytes()
        return self._mask_png

    @cached_property
    def mesh(self) -> dict:
        """Sky position of an n x n lattice of pixel centres (0-based pixel coordinates).

        The lattice is computed with the full WCS (SIP distortion included), so a client that
        interpolates between nodes reproduces the true mapping to a small fraction of a pixel.
        """
        n = MESH_N
        xs = np.linspace(0, self.width - 1, n)
        ys = np.linspace(0, self.height - 1, n)
        gx, gy = np.meshgrid(xs, ys)
        ra, dec = self.wcs.all_pix2world(gx, gy, 0)
        return {
            "n": n,
            "x": [round(float(v), 3) for v in xs],
            "y": [round(float(v), 3) for v in ys],
            "ra": [round(float(v), 7) for v in np.mod(ra, 360.0).ravel()],
            "dec": [round(float(v), 7) for v in dec.ravel()],
        }

    @cached_property
    def footprint(self) -> list[list[float]]:
        corners_x = [-0.5, self.width - 0.5, self.width - 0.5, -0.5]
        corners_y = [-0.5, -0.5, self.height - 0.5, self.height - 0.5]
        ra, dec = self.wcs.all_pix2world(corners_x, corners_y, 0)
        return [[round(float(a) % 360.0, 6), round(float(b), 6)] for a, b in zip(ra, dec, strict=True)]

    def photometry(self, x: float, y: float, r_ap: float = 3.0, r_in: float = 6.0, r_out: float = 10.0) -> dict:
        """Background-subtracted aperture photometry in physical units.

        ``x, y`` are image coordinates (0,0 = top-left corner of the top-left pixel). The background is
        the median of the annulus ``r_in..r_out``; masked pixels are ignored. Uses the unclipped data,
        unlike the 8-bit PNG. Flux density = sum(MJy/sr - background) * pixel solid angle.
        """
        if self.flux is None or not self.info.get("pixel_scale_arcsec"):
            raise PipelineError("Photometry needs a real SPHEREx image; simulated images have no physical units.")
        if not (0 < r_ap < r_in < r_out <= 100):
            raise PipelineError("Radii must satisfy 0 < aperture < inner < outer <= 100 pixels.")
        cx, cy = x - 0.5, y - 0.5  # 0-based pixel-centre coordinates
        if not (-0.5 <= cx < self.width - 0.5 and -0.5 <= cy < self.height - 0.5):
            raise NotFound("That position is outside the image.")
        x0, x1 = max(0, int(cx - r_out) - 1), min(self.width, int(cx + r_out) + 2)
        y0, y1 = max(0, int(cy - r_out) - 1), min(self.height, int(cy + r_out) + 2)
        sub = self.flux[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        dist = np.hypot(xx - cx, yy - cy)
        ap = (dist <= r_ap) & np.isfinite(sub)
        ann = (dist >= r_in) & (dist <= r_out) & np.isfinite(sub)
        if ap.sum() < 3 or ann.sum() < 10:
            raise PipelineError("Too many masked pixels around that position for a measurement.")
        background = float(np.median(sub[ann]))
        sigma = float(1.4826 * np.median(np.abs(sub[ann] - background)))  # robust per-pixel noise
        omega = (self.info["pixel_scale_arcsec"] / 206264.806) ** 2  # sr per pixel
        net = float(np.sum(sub[ap] - background))
        peak = float(sub[ap].max())
        masked = int(((dist <= r_ap) & ~np.isfinite(sub)).sum())
        masked_fraction = masked / (masked + int(ap.sum()))
        return {
            # Masked pixels (saturated cores, flagged defects) are skipped, so any masked pixel in the aperture
            # makes this a LOWER LIMIT on the true flux, not a measurement.
            "lower_limit": masked > 0,
            "masked_fraction": round(masked_fraction, 3),
            "flux_jy": net * 1e6 * omega,
            "flux_err_jy": sigma * math.sqrt(float(ap.sum())) * 1e6 * omega,
            "background_mjy_sr": background,
            "peak_mjy_sr": peak,
            "aperture_pixels": int(ap.sum()),
            "masked_pixels_in_aperture": masked,
            "snr": (net / (sigma * math.sqrt(float(ap.sum())))) if sigma > 0 else None,
            "wavelength_um": (self.info.get("wavelength") or {}).get("center"),
            "radii_px": [r_ap, r_in, r_out],
            "pixel_solid_angle_sr": omega,
        }

    def pixel_of(self, ra: float, dec: float) -> list[float] | None:
        """Image coordinates (0,0 = top-left corner of the top-left pixel) of a sky position, if inside."""
        try:
            x, y = self.wcs.all_world2pix(ra, dec, 0)
            x, y = float(x), float(y)
        except Exception:  # noqa: BLE001  astropy raises assorted errors off the footprint
            return None
        if not (math.isfinite(x) and math.isfinite(y)) or not (-0.5 <= x < self.width - 0.5 and -0.5 <= y < self.height - 0.5):
            return None
        return [round(x + 0.5, 3), round(y + 0.5, 3)]

    def meta(self, target: tuple[float, float] | None = None) -> dict:
        info = dict(self.info)
        meta = {
            "id": self.id,
            "source": self.source,
            "fallback_reason": self.fallback_reason,
            "width": self.width,
            "height": self.height,
            "bunit": info.get("bunit", "MJy / sr" if self.source == "irsa" else None),
            "stretch": {
                "kind": "linear",
                "lo": self.lo,
                "hi": self.hi,
                "percentiles": [1, 99],
                "unit": info.get("bunit"),
                "zodi_subtracted": bool(info.get("zodi_subtracted")),
                "note": "pixel_value = 255 * (flux - lo) / (hi - lo), clipped to 0..255",
            },
            "bad_fraction": round(float(self.mask.mean()), 5),
            "mesh": self.mesh,
            "footprint": self.footprint,
            "image_url": f"/api/products/{self.id}/image.png",
            "mask_url": f"/api/products/{self.id}/mask.png",
            "images_are": "simulated" if self.source == "mock" else "SPHEREx level-2 detector image",
        }
        meta.update({k: v for k, v in info.items() if k not in {"bunit", "zodi_subtracted"}})
        if target is not None:
            meta["target"] = {"ra": target[0], "dec": target[1], "pixel": self.pixel_of(*target)}
        return meta


_PRODUCTS: OrderedDict[str, Product] = OrderedDict()
_PRODUCTS_LOCK = threading.Lock()


def remember(product: Product) -> Product:
    with _PRODUCTS_LOCK:
        _PRODUCTS[product.id] = product
        _PRODUCTS.move_to_end(product.id)
        while len(_PRODUCTS) > PRODUCT_CACHE_SIZE:
            _PRODUCTS.popitem(last=False)
    return product


def get_product(product_id: str) -> Product | None:
    with _PRODUCTS_LOCK:
        product = _PRODUCTS.get(product_id)
        if product is not None:
            _PRODUCTS.move_to_end(product_id)
        return product


def _plane(data: np.ndarray, name: str) -> np.ndarray:
    array = np.squeeze(np.asarray(data))
    if array.ndim != 2 or min(array.shape) < 2:
        raise PipelineError(f"The FITS {name} plane has unexpected dimensions {np.shape(data)}.")
    return array


def _wavelength_info(hdus: fits.HDUList, header, shape: tuple[int, int]) -> dict | None:
    """Wavelength (um) per pixel from the file's WCS-WAVE lookup table; None if absent."""
    try:
        wave_wcs = WCS(header, hdus, key="W")
        n = 33
        xs = np.linspace(0, shape[1] - 1, n)
        ys = np.linspace(0, shape[0] - 1, n)
        gx, gy = np.meshgrid(xs, ys)
        wave, bandwidth = wave_wcs.pixel_to_world_values(gx, gy)
        wave = np.asarray(wave, dtype=float)
        if not np.all(np.isfinite(wave)):
            return None
        return {
            "min": round(float(wave.min()), 5),
            "max": round(float(wave.max()), 5),
            "center": round(float(np.median(wave)), 5),
            "bandwidth_median": round(float(np.median(bandwidth)), 5),
            "mesh": [round(float(v), 5) for v in wave.ravel()],
            "unit": "um",
        }
    except Exception as exc:  # noqa: BLE001  optional extension, never fatal
        LOG.info("No usable WCS-WAVE table: %s", exc)
        return None


def _stretch(image: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, float, float]:
    """1st-99th percentile linear stretch to uint8; masked pixels take the median so they are not black holes."""
    good = image[np.isfinite(image) & ~mask]
    if good.size == 0:
        raise PipelineError("The selected image contains no usable pixels.")
    lo, hi = (float(v) for v in np.percentile(good, (1, 99)))
    if hi <= lo:
        lo, hi = float(good.min()), float(good.max())
    fill = float(np.median(good))
    filled = np.where(np.isfinite(image) & ~mask, image, fill)
    if hi <= lo:
        return np.zeros(image.shape, dtype=np.uint8), lo, hi
    scaled = np.clip((filled - lo) / (hi - lo), 0, 1)
    return np.rint(scaled * 255).astype(np.uint8), lo, hi


def build_archive_product(path: Path, url: str, obs: dict | None = None) -> Product:
    """Process one downloaded L2 FITS file into a ``Product``."""
    try:
        hdus = fits.open(path, memmap=False)
    except (OSError, ValueError) as exc:
        raise PipelineError(f"The cached FITS file could not be read ({exc}). Delete {path.name} to refetch it.") from exc
    with hdus:
        names = {str(h.name).upper(): h for h in hdus if getattr(h, "data", None) is not None}
        image_hdu = names.get("IMAGE") or next((h for h in hdus if getattr(h, "data", None) is not None and np.ndim(h.data) >= 2), None)
        if image_hdu is None:
            raise PipelineError("The archive FITS file contains no image plane.")
        image = _plane(image_hdu.data, "IMAGE").astype(np.float32, copy=True)
        header = image_hdu.header
        wcs = WCS(header).celestial
        flags = names.get("FLAGS")
        if flags is not None and np.shape(flags.data) == image.shape:
            bad = (np.asarray(flags.data).astype(np.int64) & BAD_MASK) != 0
        else:
            bad = np.zeros(image.shape, dtype=bool)
        zodi = names.get("ZODI")
        zodi_subtracted = False
        if zodi is not None and np.shape(zodi.data) == image.shape:
            image -= np.asarray(zodi.data, dtype=np.float32)  # IMAGE is not zodiacal-light subtracted (IRSA docs)
            zodi_subtracted = True
        bad |= ~np.isfinite(image)
        display, lo, hi = _stretch(image, bad)
        primary = hdus[0].header
        pick = lambda key: header.get(key, primary.get(key))  # noqa: E731
        info = {
            "bunit": str(pick("BUNIT") or "MJy / sr"),
            "zodi_subtracted": zodi_subtracted,
            "obs_id": pick("OBSID"),
            "detector": int(pick("DETECTOR")) if pick("DETECTOR") is not None else None,
            "date_obs": pick("DATE-OBS"),
            "mjd_avg": float(pick("MJD-AVG")) if pick("MJD-AVG") is not None else None,
            "exposure_s": float(pick("XPOSURE")) if pick("XPOSURE") is not None else None,
            "pixel_scale_arcsec": round(float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600), 4) if wcs.has_celestial else None,
            "product_url": url,
            "wavelength": _wavelength_info(hdus, header, image.shape),
        }
        if obs:
            info["band"] = obs.get("band")
            info["wavelength_range_um"] = obs.get("wavelength_um")
            info["target_name"] = obs.get("target_name")
    flux = np.where(bad, np.nan, image).astype(np.float32)
    return Product(
        id=hashlib.sha1(url.encode()).hexdigest()[:12], source="irsa", image=display, mask=bad, wcs=wcs, lo=lo, hi=hi, info=info, flux=flux,
    )


# ----------------------------------------------------------------------------- mock data


def build_mock_product(ra: float, dec: float, size: int = 256) -> Product:
    """Deterministic simulated image, seeded by the coordinates. Never mistakable for archive data."""
    seed = zlib.crc32(f"{ra:.7f},{dec:.7f}".encode("ascii"))
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    image = rng.normal(0.0, 0.025, (size, size)).astype(np.float32)
    for _ in range(9):
        x = float(rng.integers(20, size - 20))
        y = float(rng.integers(20, size - 20))
        sigma = float(rng.uniform(1.5, 4.0))
        amplitude = float(rng.uniform(0.25, 1.0))
        image += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [(size + 1) / 2, (size + 1) / 2]  # FITS is 1-based: this is the exact array centre
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cdelt = [-0.00025, 0.00025]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    mask = np.zeros(image.shape, dtype=bool)
    display, lo, hi = _stretch(image, mask)
    info = {"bunit": None, "zodi_subtracted": False, "obs_id": None, "detector": None, "date_obs": None, "mjd_avg": None,
            "exposure_s": None, "pixel_scale_arcsec": 0.9, "product_url": None, "wavelength": None}
    return Product(id=f"mock-{seed:08x}", source="mock", image=display, mask=mask, wcs=wcs, lo=lo, hi=hi, info=info)


# ----------------------------------------------------------------------------- orchestration


def _check_coordinates(ra: float, dec: float) -> None:
    if not (math.isfinite(ra) and 0 <= ra <= 360 and math.isfinite(dec) and -90 <= dec <= 90):
        raise PipelineError("RA must be 0-360 degrees and Dec must be -90 to 90 degrees.")


def observe(
    ra: float,
    dec: float,
    mode: str | None = None,
    obs_id: str | None = None,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT_S,
    cancel: threading.Event | None = None,
    detector: int | None = None,
) -> Product:
    """Return the product for a position: live archive data, or (auto mode only) a labelled simulation."""
    _check_coordinates(ra, dec)
    selected_mode = (mode or os.getenv("SPHEREX_DATA_MODE", "auto")).strip().lower()
    if selected_mode not in _MODES:
        raise PipelineError("Mode must be auto, live, or mock.")
    if selected_mode == "mock":
        return remember(build_mock_product(ra, dec))
    try:
        candidates = search_observations(ra, dec, DEFAULT_SELECT_RADIUS_DEG, SEARCH_LIMIT)
        obs = choose_observation(candidates, obs_id, detector)
        product = get_product(hashlib.sha1(obs["access_url"].encode()).hexdigest()[:12])
        if product is None:
            path = download_product(obs["access_url"], timeout, cancel)
            product = build_archive_product(path, obs["access_url"], obs)
        # Final guard, on cached and fresh products alike: the real WCS must place the point inside the detector.
        if product.pixel_of(ra, dec) is None:
            raise NotFound("The selected SPHEREx detector does not contain the requested coordinate.")
        return remember(product)
    except Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001  live failures either surface (live) or degrade visibly (auto)
        if selected_mode == "live" or os.getenv("SPHEREX_OFFLINE", "0") == "1":
            raise exc if isinstance(exc, PipelineError) else PipelineError(str(exc)) from exc
        LOG.warning("Live retrieval failed; serving simulated data: %s", exc)
        reason = str(exc) if isinstance(exc, PipelineError) else f"Unexpected error: {type(exc).__name__}"
        product = build_mock_product(ra, dec)
        product.fallback_reason = reason
        return remember(product)
