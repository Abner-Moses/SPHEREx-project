"""Target-pixel spectral calibration for sequences, never inferred from detector band.

IRSA documents CWAVE/CBAND in microns as calibration; WCS-WAVE is only a
visualization approximation. Cutout-to-parent mapping is explicitly documented:
    x_parent = 1 + x_cutout - CRPIX1A (likewise y), all returned pixels zero based.
References: https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/
https://caltech-ipac.github.io/irsa-tutorials/spherex-psf/
"""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote
from uuid import uuid4

import numpy as np
import requests
from astropy import units as u
from astropy.io import fits

import archive_enrichment
import scientific_inspector as inspector

REFERENCE = "https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/"
MAX_CALIBRATION_BYTES = 64 * 1024 * 1024
MAX_CALIBRATION_CACHE = 128 * 1024 * 1024
CALIBRATION_TIMEOUT = min(180.0, max(1.0, float(os.getenv("SPHEREX_SEQUENCE_CALIBRATION_TIMEOUT", "90"))))
CALIBRATION_TTL = 86400.0
MAX_CALIBRATION_PIXELS = 4096 * 4096


def check_cancel(event):
    if event is not None and event.is_set():
        raise InterruptedError("Sequence cancellation requested")


def _detector(value):
    match = re.fullmatch(r"(?:SPHEREx-)?D?([1-6])", str(value), re.IGNORECASE)
    return int(match[1]) if match else None


def original_pixel(header, x, y):
    """The documented IRSA translation; refuse undeclared/non-unit transforms."""
    if any(k not in header for k in ("CRPIX1A", "CRPIX2A")):
        raise ValueError("Missing CRPIX1A/CRPIX2A cutout-to-parent mapping")
    for i in (1, 2):
        if str(header.get(f"CTYPE{i}A", "LINEAR")).upper() != "LINEAR":
            raise ValueError("Unsupported alternate A pixel transform")
        if str(header.get(f"CUNIT{i}A", "pixel")).lower() not in ("pixel", "pix"):
            raise ValueError("Alternate A coordinates are not declared pixel units")
        if float(header.get(f"CDELT{i}A", 1)) != 1 or float(header.get(f"CRVAL{i}A", 0)) != 0:
            raise ValueError("Non-unit alternate A transform requires independent calibration mapping")
        for j in (1, 2):
            if float(header.get(f"PC{i}_{j}A", int(i == j))) != int(i == j) or f"CD{i}_{j}A" in header:
                raise ValueError("Rotated/scaled alternate A transform is not supported")
    pixel = [1 + float(x) - float(header["CRPIX1A"]),
             1 + float(y) - float(header["CRPIX2A"])]
    if not np.isfinite(pixel).all():
        raise ValueError("Nonfinite parent pixel mapping")
    return pixel


def _pair(hdus, x, y):
    values = []
    for name in ("CWAVE", "CBAND"):
        matches = [h for h in hdus if h.name.upper() == name]
        if len(matches) != 1 or len(matches[0].shape) != 2:
            raise ValueError("Calibration requires unique 2D CWAVE and CBAND images")
        hdu = matches[0]
        if math.prod(hdu.shape) > MAX_CALIBRATION_PIXELS:
            raise ValueError("Calibration image exceeds pixel bound")
        ix, iy = math.floor(x + .5), math.floor(y + .5)
        if not (0 <= x <= hdu.shape[1]-1 and 0 <= y <= hdu.shape[0]-1):
            raise ValueError("Mapped target is outside calibration pixel centers")
        # IRSA's named CWAVE/CBAND products explicitly document microns.
        unit = u.Unit(hdu.header.get("BUNIT", "um"))
        value = (float(hdu.section[iy:iy+1, ix:ix+1][0, 0]) * unit).to_value(u.um)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("CWAVE/CBAND must be finite positive lengths")
        values.append(value)
    if hdus["CWAVE"].shape != hdus["CBAND"].shape:
        raise ValueError("CWAVE/CBAND shapes disagree")
    return {"status": "calibrated", "wavelength_um": values[0], "bandwidth_um": values[1],
            "sampling": "nearest original detector pixel; no spectral interpolation",
            "calibration_pixel": [math.floor(x + .5), math.floor(y + .5)], "reference": REFERENCE}


def calibration_link(enriched, did, detector):
    """Only the unique product-associated official spectral calibration link."""
    links = enriched.get("datalink", {})
    if links.get("status") not in (None, "ok"):
        error = links.get("error") or {}
        raise ValueError(f"DataLink calibration association unavailable ({links.get('status')}/{error.get('code', 'unknown')}): {error.get('message', 'upstream metadata did not validate')}")
    candidates = set()
    for row in links.get("rows", []):
        if row.get("ID", row.get("id")) != did or row.get("error_message"):
            continue
        if str(row.get("semantics", "")).rsplit("#", 1)[-1] not in ("calibration", "auxiliary"):
            continue
        url = row.get("access_url")
        try:
            parts = archive_enrichment._official_url(url)
        except ValueError:
            continue
        match = re.fullmatch(r"spectral_wcs_D([1-6])_.+\.fits", unquote(parts.path).rsplit("/", 1)[-1])
        if not match or parts.query or int(match[1]) != detector:
            continue
        candidates.add(url)
    if len(candidates) != 1:
        raise ValueError("No unique DataLink-associated CWAVE/CBAND calibration for this detector")
    return candidates.pop()


class CalibrationStore:
    """Small disk/TTL cache with inflight dedup, bounded transfers and no redirects."""
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.entries = OrderedDict()
        self.flights = {}
        self.pins = {}
        self.associations = OrderedDict()
        try:
            saved=json.loads((self.root/'associations.json').read_text())
            for entry in saved[-1024:]:
                archive_enrichment._official_url(entry['url'])
                self.associations[(entry['did'],entry['detector'])]=entry['url']
        except (OSError,ValueError,KeyError,TypeError):pass

    def _prune(self, protected):
        files = sorted(self.root.glob("*.fits"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        pinned = {self.root / (hashlib.sha256(url.encode()).hexdigest() + ".fits")
                  for url, count in self.pins.items() if count}
        for path in files:
            if path == protected or path in pinned:
                continue
            if total <= MAX_CALIBRATION_CACHE and time.time() - path.stat().st_mtime < CALIBRATION_TTL:
                continue
            total -= path.stat().st_size
            path.unlink(missing_ok=True)
            for key in list(self.entries):
                if self.entries[key]["path"] == path:
                    self.entries.pop(key)
        if total > MAX_CALIBRATION_CACHE:
            protected.unlink(missing_ok=True)
            raise ValueError("Calibration cannot fit within the bounded cache")

    def _download(self, url, event):
        archive_enrichment._official_url(url)
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.root / (uuid4().hex + ".part")
        destination = self.root / (hashlib.sha256(url.encode()).hexdigest() + ".fits")
        started = time.monotonic()
        digest = hashlib.sha256()
        try:
            with requests.Session() as session:
                with session.get(url, stream=True, allow_redirects=False,
                                 timeout=(3, min(20, CALIBRATION_TIMEOUT))) as response:
                    if response.status_code != 200:
                        raise ValueError(f"Calibration download returned HTTP {response.status_code}; redirects refused")
                    if any(t in response.headers.get("Content-Type", "").lower() for t in ("html", "xml", "json")):
                        raise ValueError("Calibration link returned an error document")
                    declared = int(response.headers.get("Content-Length", "0"))
                    if declared > MAX_CALIBRATION_BYTES:
                        raise ValueError("Calibration transfer exceeds byte bound")
                    total = 0
                    with temp.open("wb") as output:
                        for chunk in response.iter_content(65536):
                            check_cancel(event)
                            if time.monotonic() - started > CALIBRATION_TIMEOUT:
                                raise TimeoutError("Calibration total transfer timeout")
                            total += len(chunk)
                            if total > MAX_CALIBRATION_BYTES:
                                raise ValueError("Calibration transfer exceeds byte bound")
                            output.write(chunk)
                            digest.update(chunk)
                    if total < 2880 or total % 2880 or (declared and total != declared):
                        raise ValueError("Incomplete calibration FITS download")
            check_cancel(event)
            temp.replace(destination)
            return {"path": destination, "source_url": url, "sha256": digest.hexdigest(),
                    "bytes": total, "expires": time.monotonic() + CALIBRATION_TTL}
        finally:
            temp.unlink(missing_ok=True)

    def get(self, url, event, allow_download=True):
        check_cancel(event)
        with self.lock:
            entry = self.entries.get(url)
            if entry and entry["expires"] > time.monotonic() and entry["path"].exists():
                self.entries.move_to_end(url)
                return dict(entry)
            path=self.root/(hashlib.sha256(url.encode()).hexdigest()+'.fits')
            try:
                meta=json.loads(path.with_suffix('.json').read_text())
                if meta['source_url']!=url or time.time()-meta['retrieved_at']>CALIBRATION_TTL or path.stat().st_size!=meta['bytes']:raise ValueError('Stale calibration')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('Corrupt calibration')
                entry={**meta,'path':path,'expires':time.monotonic()+max(0,CALIBRATION_TTL-(time.time()-meta['retrieved_at'])),'cache_hit':True}
                self.entries[url]=entry
                return dict(entry)
            except (OSError,ValueError,KeyError,TypeError):pass
            if not allow_download:
                raise ValueError("Calibration is not already cached; exploratory mode does not initiate a download")
            future = self.flights.get(url)
            owner = future is None
            if owner:
                future = self.flights[url] = Future()
        if not owner:
            deadline = time.monotonic() + CALIBRATION_TIMEOUT + 15
            while True:
                check_cancel(event)
                if time.monotonic() > deadline:
                    raise TimeoutError("Waiting for calibration download exceeded deadline")
                try:
                    return dict(future.result(timeout=.1))
                except FutureTimeout:
                    continue
                except InterruptedError:
                    check_cancel(event)
                    # A different job cancelled the shared download; this
                    # caller is still active and may become its new owner.
                    with self.lock:
                        if self.flights.get(url) is future:
                            self.flights.pop(url)
                    return self.get(url, event)
        try:
            for attempt in range(2):
                try:
                    entry = self._download(url, event);break
                except (requests.RequestException,ValueError,TimeoutError):
                    check_cancel(event)
                    if attempt:raise
                    if event is not None and event.wait(.4):check_cancel(event)
            entry['retrieved_at']=time.time();entry['cache_hit']=False
            meta={k:entry[k] for k in ('source_url','sha256','bytes','retrieved_at')}
            target=entry['path'].with_suffix('.json');tmp=target.with_suffix('.'+uuid4().hex+'.tmp')
            tmp.write_text(json.dumps(meta));tmp.replace(target)
            with self.lock:
                self._prune(entry["path"])
                self.entries[url] = entry
                self.entries.move_to_end(url)
                future.set_result(entry)
            return dict(entry)
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                if self.flights.get(url) is future:
                    self.flights.pop(url, None)

    def associate(self, did, detector, url):
        with self.lock:
            self.associations[(did, detector)] = url
            self.associations.move_to_end((did, detector))
            while len(self.associations) > 1024:
                self.associations.popitem(last=False)
            self.root.mkdir(parents=True,exist_ok=True)
            target=self.root/'associations.json';tmp=self.root/(uuid4().hex+'.tmp')
            tmp.write_text(json.dumps([{'did':key[0],'detector':key[1],'url':value} for key,value in self.associations.items()]));tmp.replace(target)

    def cached_url(self, did, detector):
        with self.lock:
            url = self.associations.get((did, detector))
            if url and url not in self.entries:
                self.get(url,None,allow_download=False)
            entry = self.entries.get(url)
            if not entry or entry["expires"] <= time.monotonic() or not entry["path"].exists():
                raise ValueError("No cached product-associated calibration; exploratory mode skips external calibration lookup/download")
            return url

    def sample(self, url, x, y, event, allow_download=True):
        with self.lock:  # Protect this short section read from cache eviction.
            self.pins[url] = self.pins.get(url, 0) + 1
        try:
            entry = self.get(url, event, allow_download=allow_download)
            check_cancel(event)
            with self.lock:
                with fits.open(entry["path"], mode="readonly", memmap=False) as hdus:
                    expected = re.search(r"spectral_wcs_D([1-6])_", url)
                    for hdu in (hdus[0], hdus["CWAVE"], hdus["CBAND"]):
                        if "DETECTOR" in hdu.header and (not expected or _detector(hdu.header["DETECTOR"]) != int(expected[1])):
                            raise ValueError("Calibration detector header disagrees with its associated product")
                    result = _pair(hdus, x, y)
            result.update(source="DataLink CWAVE/CBAND calibration", source_url=url,
                          calibration_sha256=entry["sha256"], calibration_cache_hit=entry.get("cache_hit",True), calibration_retrieved_at=entry.get("retrieved_at"), parent_pixel=[x, y])
            return result
        finally:
            with self.lock:
                self.pins[url] -= 1
                if not self.pins[url]:
                    self.pins.pop(url)


def resolve(path, record, ra, dec, calibrations, event=None, inspection=None, *, allow_calibration_download=True):
    """Return calibrated values or explicitly labeled approximation/unavailability.

    Approximation is reported for exploration only. The sequence service never
    accepts it as a calibrated comparable frame and never derives band centers
    from SIA wavelength intervals or a detector label.
    """
    check_cancel(event)
    inspection = inspection or inspector.pixel(path, ra=ra, dec=dec)
    x, y = inspection["pixel"]["x"], inspection["pixel"]["y"]
    failures = []
    with fits.open(path, mode="readonly", memmap=False) as hdus:
        header = hdus["IMAGE"].header.copy()
        detector = _detector(header.get("DETECTOR", hdus[0].header.get("DETECTOR", record.get("detector"))))
        embedded = all(name in hdus for name in ("CWAVE", "CBAND"))
        if embedded:
            try:
                if hdus["CWAVE"].shape != hdus["IMAGE"].shape or hdus["CBAND"].shape != hdus["IMAGE"].shape:
                    raise ValueError("Embedded calibration is not on the IMAGE grid")
                result = _pair(hdus, x, y)
                result.update(source="embedded CWAVE/CBAND", source_sha256=record.get("sha256"))
                return result
            except Exception as exc:
                failures.append(str(exc))
    import local_science
    if local_science.ROOT.exists():
        try:return local_science.local_bandpass(path,record,ra,dec)
        except (ValueError,KeyError,TypeError,OSError) as exc:failures.append(str(exc))
    try:
        cached_url=calibrations.cached_url(record.get('sia',{}).get('obs_publisher_did'),detector)
        parent_x,parent_y=original_pixel(header,x,y)
        return calibrations.sample(cached_url,parent_x,parent_y,event,allow_download=False)
    except (ValueError,OSError,KeyError,TypeError,AttributeError):pass
    if not local_science.remote_allowed('calibration'):
        return {'status':'unavailable','reason':'; '.join(failures) or 'No matched local calibration'}
    try:
        check_cancel(event)
        if detector is None:
            raise ValueError("A documented detector identity is required for external calibration")
        parent_x, parent_y = original_pixel(header, x, y)
        did = record.get("sia", {}).get("obs_publisher_did")
        if not allow_calibration_download:
            url = calibrations.cached_url(did, detector)
            return calibrations.sample(url, parent_x, parent_y, event, allow_download=False)
        datalink = getattr(archive_enrichment, "datalink", None)
        if not callable(datalink):
            raise ValueError("Independent DataLink calibration lookup is unavailable")
        try:
            url=calibrations.cached_url(did,detector)
            return calibrations.sample(url,parent_x,parent_y,event,allow_download=False)
        except (ValueError,OSError):pass
        links = datalink(record)
        if links.get('status')=='upstream':
            local_science.remote_failed('calibration')
            raise ValueError('Remote DataLink unavailable; no repeated retry. No matched local calibration was found.')
        check_cancel(event)
        url = calibration_link({"datalink": links}, did, detector)
        result = calibrations.sample(url, parent_x, parent_y, event)
        calibrations.associate(did, detector, url)
        return result
    except InterruptedError:
        raise
    except Exception as exc:
        if isinstance(exc,(requests.RequestException,TimeoutError)):local_science.remote_failed('calibration')
        failures.append(str(exc))
    approximation = inspection.get("spectral", {})
    axes = approximation.get("axes", [])
    if approximation.get("status") == "available" and len(axes) == 2:
        try:
            values = [(float(a["value"]) * u.Unit(a["unit"])).to_value(u.um) for a in axes]
            if not all(math.isfinite(v) and v > 0 for v in values):
                raise ValueError("Nonfinite visualization spectral estimate")
            return {"status": "approximate", "wavelength_um": values[0], "bandwidth_um": values[1],
                    "source": "WCS-WAVE visualization estimate", "reference": REFERENCE,
                    "warning": "Visualization approximation only; excluded from calibrated comparable mode",
                    "calibration_unavailable_reasons": failures}
        except Exception as exc:
            failures.append(str(exc))
    return {"status": "unavailable", "reason": "; ".join(failures), "reference": REFERENCE}


def compatible(candidate, reference, tolerance=.01):
    """Both target CWAVE and CBAND must agree to the declared relative tolerance."""
    if candidate.get("status") != "calibrated" or reference.get("status") != "calibrated":
        return False
    for key in ("wavelength_um", "bandwidth_um"):
        a, b = candidate.get(key), reference.get(key)
        if (not isinstance(a, (int, float)) or not isinstance(b, (int, float))
                or not math.isfinite(a) or not math.isfinite(b) or min(a, b) <= 0
                or abs(a - b) > tolerance * b):
            return False
    return True
