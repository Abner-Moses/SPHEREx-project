"""Bounded, optional chronological sequence preparation, independent of API/UI.

prepare({ra, dec, observation_ids: [product_id], cutout_size_deg: .1,
         max_frames: 10, mode: 'comparable'|'all', date_start: None,
         date_end: None, wavelength_min_um: None, wavelength_max_um: None,
         spectral_tolerance_fraction: .01,
         display: {low: ..., high: ..., stretch: 'asinh'|'linear'}})
returns immediately {id, status: 'preparing'}. status(id) exposes ready frames,
failed_frames, excluded_frames, frame_states, progress, common_wcs and display.
frame_path(id, frame_id, 'preview'|'fits'|'mask') is local-only after preparation.

The cap bounds retrieval attempts in supplied candidate order, not an archive
query or an earliest-observation claim. Every supplied candidate remains in the
job report. DATE-OBS/MJD-OBS from the original FITS determine final chronology.
No PSF matching, photometric equivalence, covariance or variability inference is
claimed. Only PNGs have a stretch. Derived FITS preserve physical brightness.
"""
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

import numpy as np
from astropy import units as u
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp

from archive_client import DataError
import fits_processor
import observation_service as observations
import scientific_inspector as inspector
import sequence_spectral as spectral

PIXEL_SCALE_ARCSEC = 6.15
MAX_DIMENSION = 1024
MAX_FRAMES = 100
MAX_CANDIDATES = 5000
MAX_ACTIVE_JOBS = 8
MAX_RETAINED_JOBS = 32
MAX_INPUT_PIXELS = 4096 * 4096
MAX_ALIGNED_BYTES = 512 * 1024 * 1024
MAX_JOB_BYTES = 1024 * 1024 * 1024
JOB_TTL = 3600.0
JOB_TIMEOUT = 600.0
ALIGNED_TTL = 86400.0
POLICY = "sequence-tan-v1:" + fits_processor.POLICY_VERSION
VARIANCE_WARNING = ("Aligned variance omitted: interpolation weights/covariance have not been validated. "
                    "ORIGVAR retains the original source-grid variance; it is not the uncertainty of aligned IMAGE.")
SPECTRAL_WARNING = ("Exploratory all mode: frames may sample different wavelengths/bandwidths or lack calibration. "
                    "Apparent changes must not be interpreted as time variability.")
COMPARISON_WARNING = ("Spectral agreement is tested only at the requested target pixel, not across the image. "
                      "WCS reprojection does not correct astrometric errors or match PSFs; no variability inference is provided.")


def _finite(value, name, minimum=None, maximum=None):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
        if not math.isfinite(result) or (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
            raise ValueError()
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataError(f"Invalid {name}.", 422) from exc


def _date(value, end=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataError("Date filters must be ISO UTC strings.", 422)
    try:
        result = float(Time(value, scale="utc").mjd)
        if not math.isfinite(result):
            raise ValueError()
        return result + (1 if end and len(value) == 10 else 0)
    except Exception as exc:
        raise DataError("Invalid ISO observation date filter.", 422) from exc


def _request(request):
    if not isinstance(request, dict):
        raise DataError("Sequence request must be an object.", 422)
    ids = request.get("observation_ids", request.get("product_ids"))
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_CANDIDATES:
        raise DataError(f"Supply 1..{MAX_CANDIDATES} observation_ids (stable product IDs).", 422)
    if any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{32}", v) for v in ids):
        raise DataError("observation_ids must contain stable archive product IDs.", 422)
    ra = _finite(request.get("ra"), "ra", 0, 360)
    dec = _finite(request.get("dec"), "dec", -90, 90)
    if ra == 360:
        raise DataError("ra must be less than 360 degrees.", 422)
    size = _finite(request.get("cutout_size_deg", .1), "cutout_size_deg", 1e-6)
    dimension = max(2, math.ceil(size * 3600 / PIXEL_SCALE_ARCSEC))
    if dimension > MAX_DIMENSION:
        raise DataError(f"Output exceeds {MAX_DIMENSION} pixels at fixed 6.15 arcsec/pixel; reduce cutout_size_deg.", 422)
    limit = request.get("max_frames", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FRAMES:
        raise DataError(f"max_frames must be an integer in 1..{MAX_FRAMES}; nothing was silently truncated.", 422)
    mode = request.get("mode", "comparable")
    if mode not in ("comparable", "all"):
        raise DataError("mode must be comparable or all.", 422)
    tolerance = _finite(request.get("spectral_tolerance_fraction", .01), "spectral_tolerance_fraction", 0, .1)
    low, high = request.get("wavelength_min_um"), request.get("wavelength_max_um")
    low = _finite(low, "wavelength_min_um", 1e-12) if low is not None else None
    high = _finite(high, "wavelength_max_um", 1e-12) if high is not None else None
    if low is not None and high is not None and low > high:
        raise DataError("Wavelength filter bounds are reversed.", 422)
    start, end = _date(request.get("date_start")), _date(request.get("date_end"), end=True)
    if start is not None and end is not None and start > end:
        raise DataError("Observation date filter bounds are reversed.", 422)
    display = request.get("display") or {}
    if not isinstance(display, dict) or display.get("stretch", "asinh") not in ("asinh", "linear"):
        raise DataError("display requires an asinh or linear stretch.", 422)
    dlow, dhigh = display.get("low", display.get("vmin")), display.get("high", display.get("vmax"))
    if (dlow is None) != (dhigh is None):
        raise DataError("Supply both display.low and display.high.", 422)
    if dlow is not None:
        dlow, dhigh = _finite(dlow, "display.low"), _finite(dhigh, "display.high")
        if dlow >= dhigh:
            raise DataError("display.low must be less than display.high.", 422)
    adaptive=request.get('adaptive',False)
    if not isinstance(adaptive,bool):raise DataError('adaptive must be boolean',422)
    budget=request.get('candidate_budget',40) if adaptive else limit
    if isinstance(budget,bool) or not isinstance(budget,int) or not 1<=budget<=100:raise DataError('candidate_budget must be 1..100',422)
    timeout=_finite(request.get('total_timeout_s',JOB_TIMEOUT),'total_timeout_s',10,900)
    return {"adaptive":adaptive,"candidate_budget":budget,"total_timeout_s":timeout,"ra": ra, "dec": dec, "cutout_size_deg": size, "dimension": dimension,
            "observation_ids": list(ids), "max_frames": limit, "mode": mode,
            "spectral_tolerance_fraction": tolerance, "wavelength_min_um": low, "wavelength_max_um": high,
            "date_start": request.get("date_start"), "date_end": request.get("date_end"),
            "mjd_start": start, "mjd_end": end,
            "end_exclusive": isinstance(request.get("date_end"), str) and len(request["date_end"]) == 10,
            "display": {"low": dlow, "high": dhigh, "stretch": display.get("stretch", "asinh"),
                        "asinh_a": _finite(display.get("asinh_a", .1), "display.asinh_a", .001, 10)}}


def output_wcs(ra, dec, dimension):
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [(dimension + 1) / 2] * 2
    wcs.wcs.cdelt = [-PIXEL_SCALE_ARCSEC / 3600, PIXEL_SCALE_ARCSEC / 3600]
    wcs.wcs.radesys = "ICRS"
    return wcs


def _celestial(header):
    header = header.copy()
    # Do not ask wcslib to resolve the independent alternate W -TAB calibration.
    for key in list(header):
        if re.fullmatch(r"(WCSAXES|WCSNAME|CTYPE\d+|CNAME\d+|CUNIT\d+|CRPIX\d+|CRVAL\d+|CDELT\d+|PC\d+_\d+|CD\d+_\d+|PS\d+_\d+|PV\d+_\d+|LONPOLE|LATPOLE)W", key):
            del header[key]
    wcs = WCS(header).celestial
    if not wcs.has_celestial:
        raise ValueError("No usable celestial WCS")
    return wcs


def observation_time(hdus):
    """Only observation-start FITS keywords; never DATE, PROCVER or filenames."""
    values = []
    for label, header in (("IMAGE", hdus["IMAGE"].header), ("PRIMARY", hdus[0].header)):
        scale = str(header.get("TIMESYS", hdus[0].header.get("TIMESYS", "UTC"))).lower()
        for key in ("MJD-OBS", "MJD_OBS", "DATE-OBS"):
            if key not in header:
                continue
            value = Time(float(header[key]), format="mjd", scale=scale) if key != "DATE-OBS" else Time(str(header[key]), scale=scale)
            if not math.isfinite(float(value.utc.mjd)):
                raise ValueError("Nonfinite FITS observation time")
            values.append((value.utc, f"{label}.{key}"))
    if not values:
        raise ValueError("Missing authoritative FITS DATE-OBS/MJD-OBS; processing dates are not observation times")
    first, source = values[0]
    if any(abs(float((value - first).sec)) > 1 for value, _ in values[1:]):
        raise ValueError("FITS observation time keywords disagree by more than one second")
    return {"timestamp": first.isot + "Z", "mjd": float(first.mjd), "time_source": source, "time_scale": "UTC"}


def _reason(frame, status, code, message):
    frame.update(status=status, reason=message, reason_code=code)
    return frame


class SequenceService:
    """Two coordinators and exactly two shared download/processing workers.

    There are at most eight admitted active jobs and two frame futures per
    coordinator. Raw leases span reads/reprojection only. Cancellation is
    cooperative; a running observation_service.retrieve cannot be interrupted.
    """
    def __init__(self, cache_dir=None):
        self.root = Path(cache_dir) if cache_dir is not None else observations.CACHE / "sequences"
        self.lock = threading.RLock()
        self.jobs = {}
        self.align_flights = {}
        self.closed = False
        self.coordinators = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sequence-job")
        self.workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sequence-frame")
        self.calibrations = spectral.CalibrationStore(self.root / "calibrations")

    def _job(self, identifier):
        if not isinstance(identifier, str) or identifier not in self.jobs:
            raise DataError("Unknown or expired sequence job.", 404)
        return self.jobs[identifier]

    def _check(self, job):
        spectral.check_cancel(job["event"])
        if time.monotonic() > job["deadline"]:
            raise TimeoutError("Sequence total preparation deadline exceeded")

    def _prune_jobs(self):
        terminal = [j for j in self.jobs.values() if j["finished"] and not j["serving"]]
        for job in sorted(terminal, key=lambda j: j["touched"]):
            if time.monotonic() - job["touched"] > JOB_TTL or len(self.jobs) >= MAX_RETAINED_JOBS:
                shutil.rmtree(self.root / "jobs" / job["id"], ignore_errors=True)
                self.jobs.pop(job["id"], None)

    def prepare(self, request):
        req = _request(request)  # No archive resolution, filesystem reads or network.
        identifier = uuid4().hex
        if req['adaptive']:
            from sequence_planning import rank_candidates
            ranked=rank_candidates(req['observation_ids'],req['ra'],req['dec'],req['candidate_budget'])
            req['observation_ids']=ranked+[key for key in req['observation_ids'] if key not in set(ranked)]
        frames = []
        seen = set()
        for index, product_id in enumerate(req["observation_ids"]):
            frame = {"id": uuid4().hex, "product_id": product_id, "candidate_index": index, "status": "pending"}
            if index >= req["candidate_budget"]:
                _reason(frame, "excluded", "max_frames", "Outside explicit max_frames retrieval-attempt cap in supplied order")
            elif product_id in seen:
                _reason(frame, "excluded", "duplicate_product", "Repeated product ID is not a separate observation")
            seen.add(product_id)
            frames.append(frame)
        wcs = output_wcs(req["ra"], req["dec"], req["dimension"])
        job = {"id": identifier, "status": "preparing", "request": req, "event": threading.Event(),
               "deadline": time.monotonic() + req["total_timeout_s"], "touched": time.monotonic(),
               "created_at": datetime.now(timezone.utc).isoformat(), "frame_states": frames,
               "finished": False, "serving": 0,
               "common_wcs": {"header": dict(wcs.to_header()), "shape": [req["dimension"]] * 2,
                              "pixel_scale_arcsec": PIXEL_SCALE_ARCSEC, "north_up": True,
                              "field_of_view_arcsec": [req["dimension"] * PIXEL_SCALE_ARCSEC] * 2,
                              "field_of_view_deg": [req["dimension"] * PIXEL_SCALE_ARCSEC / 3600] * 2,
                              "center": {"ra": req["ra"], "dec": req["dec"], "frame": "icrs"}},
               "display": None, "warnings": [VARIANCE_WARNING, COMPARISON_WARNING]
                   + ([SPECTRAL_WARNING] if req["mode"] == "all" else []), "delete_requested": False}
        with self.lock:
            if self.closed:
                raise DataError("Sequence service has been shut down.", 503)
            self._prune_jobs()
            if len(self.jobs) >= MAX_RETAINED_JOBS:
                raise DataError("Retained sequence jobs are in use; delete jobs or retry later.", 503)
            if sum(not j["finished"] for j in self.jobs.values()) >= MAX_ACTIVE_JOBS:
                raise DataError("Sequence preparation capacity reached; retry later.", 503)
            self.jobs[identifier] = job
            job["future"] = self.coordinators.submit(self._run, job)
        return {"id": identifier, "status": "preparing"}

    def status(self, identifier):
        with self.lock:
            job = self._job(identifier)
            job["touched"] = time.monotonic()
            frames = deepcopy(job["frame_states"])
            selected = frames[:job["request"]["candidate_budget"]]
            ready = sorted((f for f in frames if f["status"] == "ready"), key=lambda f: (f["mjd"], f["candidate_index"]))
            return {"id": identifier, "status": job["status"], "cancel_requested": job["event"].is_set(),
                    "progress": {"completed": sum(f["status"] in ("aligned", "ready", "failed", "excluded", "cancelled") for f in selected),
                                 "total": len(selected)}, "frames": ready,
                    "failed_frames": [f for f in frames if f["status"] == "failed"],
                    "excluded_frames": [f for f in frames if f["status"] == "excluded"],
                    "frame_states": frames, "display": deepcopy(job["display"]),
                    "common_wcs": deepcopy(job["common_wcs"]), "warnings": list(job["warnings"]),
                    "request": deepcopy(job["request"]), "error": job.get("error"),
                    "selection": {"supplied": len(frames), "max_frames": job["request"]["max_frames"],
                                  "backend_cap": MAX_FRAMES, "archive_table_modified": False,
                                  "scope": "Supplied candidate order bounded before retrieval; prepared frames sorted by FITS observation time"}}

    def _update(self, job, frame_id, **changes):
        with self.lock:
            frame = next(f for f in job["frame_states"] if f["id"] == frame_id)
            frame.update(changes)

    def _one(self, job, frame):
        frame = deepcopy(frame)
        req = job["request"]
        try:
            self._check(job)
            self._update(job, frame["id"], status="retrieving")
            record = observations.retrieve(frame["product_id"], req["ra"], req["dec"], req["cutout_size_deg"])
            self._check(job)
            frame.update(asset_id=record["id"], observation_id=record.get("observation_id"),
                         source_url=record.get("source_url"), detector=record.get("detector"), collection=record.get("collection"),
                         retrieval_coordinates=deepcopy(record.get("coordinates")), warnings=[VARIANCE_WARNING])
            self._update(job, frame["id"], status="inspecting", asset_id=record["id"])
            with observations.asset_lease(record["id"]) as source:
                inspection = inspector.pixel(source, ra=req["ra"], dec=req["dec"])
                with fits.open(source, mode="readonly", memmap=False) as hdus:
                    frame.update(observation_time(hdus))
                    shape = hdus["IMAGE"].shape
                    if len(shape) != 2 or math.prod(shape) > MAX_INPUT_PIXELS:
                        raise ValueError("Original IMAGE exceeds the bounded 2D input pixel limit")
                    acceptance = deepcopy(record.get("validation", {}).get("scientific_acceptance", {}))
                    reasons = list(acceptance.get("reasons", []))
                    raw_values = inspection["values"]
                    if any(raw_values.get(k, {}).get("status") != "available" or raw_values[k].get("value") is None
                           for k in ("IMAGE", "VARIANCE", "FLAGS")):
                        reasons.append("Original target IMAGE/VARIANCE/FLAGS sample is unavailable")
                    else:
                        bad_target = fits_processor.quality_mask(
                            np.array([raw_values["IMAGE"]["value"]]), np.array([raw_values["FLAGS"]["value"]], dtype=np.uint64),
                            np.array([raw_values["VARIANCE"]["value"]]), hdus["FLAGS"].header)[0]
                        if bad_target:
                            reasons.append("Requested target is rejected by the current original-pixel quality policy")
                    raw_astrometry = fits_processor.astrometric_quality(hdus["IMAGE"].header.get("FINAST", hdus[0].header.get("FINAST")))
                    if raw_astrometry["status"] != "good":
                        reasons.append("Original FITS absolute astrometric quality is not good")
                    if record.get("validation", {}).get("policy_version") != fits_processor.POLICY_VERSION:
                        reasons.append("Original validation policy version is not current")
                    accepted = acceptance.get("accepted") is True and not reasons
                    frame["quality"] = {**deepcopy(record.get("validation", {})), **acceptance,
                                        "accepted": accepted, "reasons": reasons,
                                        "status": "accepted" if accepted else "inspection_only",
                                        "sequence_target_astrometry": raw_astrometry,
                                        "astrometric_quality": deepcopy(record.get("validation", {}).get("astrometric_quality", {})),
                                        "policy_version": record.get("validation", {}).get("policy_version")}
                    if req["mode"] == "comparable" and not accepted:
                        return _reason(frame, "excluded", "quality_not_accepted", "; ".join(map(str, reasons)) or "Scientific quality is not accepted")
                    if not frame["quality"]["accepted"]:
                        frame["warnings"].append("Inspection-only scientific quality; see quality.reasons")
                if ((req["mjd_start"] is not None and frame["mjd"] < req["mjd_start"])
                        or (req["mjd_end"] is not None and (frame["mjd"] >= req["mjd_end"] if req["end_exclusive"] else frame["mjd"] > req["mjd_end"]))):
                    return _reason(frame, "excluded", "date_filter", "FITS observation time is outside the requested date range")
                self._check(job)
                spec = spectral.resolve(source, record, req["ra"], req["dec"], self.calibrations, job["event"], inspection,
                                        allow_calibration_download=req["mode"] == "comparable")
                frame.update(spectral=spec, wavelength_um=spec.get("wavelength_um"), bandwidth_um=spec.get("bandwidth_um"))
                if req["mode"] == "comparable" and not spectral.compatible(spec, spec, req["spectral_tolerance_fraction"]):
                    return _reason(frame, "excluded", "spectral_calibration_unavailable", spec.get("reason", spec.get("warning", "Genuine CWAVE/CBAND target calibration required")))
                if spec.get("status") != "calibrated":
                    frame["warnings"].append(spec.get("warning", spec.get("reason", "Target spectral calibration unavailable")))
                wave = spec.get("wavelength_um")
                if req["wavelength_min_um"] is not None or req["wavelength_max_um"] is not None:
                    if (wave is None or not math.isfinite(wave) or (req["wavelength_min_um"] is not None and wave < req["wavelength_min_um"])
                            or (req["wavelength_max_um"] is not None and wave > req["wavelength_max_um"])):
                        return _reason(frame, "excluded", "wavelength_filter", "Target wavelength is unavailable or outside the requested range")
                self._check(job)
                self._update(job, frame["id"], status="aligning")
                self._align(job, frame, record, source)
            frame["status"] = "aligned"
            return frame
        except InterruptedError as exc:
            return _reason(frame, "cancelled", "cancelled", str(exc))
        except Exception as exc:
            code = "storage_limit" if isinstance(exc, DataError) and exc.status == 507 else "preparation_failed"
            return _reason(frame, "failed", code, str(exc) or type(exc).__name__)

    def _cache_room(self, required):
        directory = self.root / "aligned"
        directory.mkdir(parents=True, exist_ok=True)
        files = sorted(directory.glob("*.fits"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total + required <= MAX_ALIGNED_BYTES and time.time() - path.stat().st_mtime < ALIGNED_TTL:
                continue
            total -= path.stat().st_size
            path.unlink(missing_ok=True)  # Active jobs hold their own hardlinks.
        if total + required > MAX_ALIGNED_BYTES:
            raise DataError("Aligned sequence cache byte budget exceeded.", 507)
        self._job_room(required)

    def _job_room(self, required):
        job_bytes = sum(p.stat().st_size for p in (self.root / "jobs").rglob("*") if p.is_file())
        if job_bytes + required > MAX_JOB_BYTES:
            raise DataError("Prepared sequence storage budget exceeded; delete previous jobs or reduce frames.", 507)

    def _align(self, job, frame, record, source):
        self._check(job)
        source_sha = record.get("sha256")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", source_sha):
            raise ValueError("Validated source SHA256 is required for scientific frame caching")
        policy = {"version": POLICY, "wcs": job["common_wcs"], "image_interpolation": "bilinear",
                  "flags": "nearest-neighbor original integer values", "variance": "omitted; original grid retained",
                  "mask": "any bad bilinear contributor or missing coverage", "unit": "MJy/sr"}
        key = hashlib.sha256(json.dumps([source_sha, policy], sort_keys=True).encode()).hexdigest()
        cached = self.root / "aligned" / (key + ".fits")
        destination = self.root / "jobs" / job["id"] / (frame["id"] + ".fits")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if cached.exists() and time.time() - cached.stat().st_mtime < ALIGNED_TTL:
                self._cache_room(0)
                if cached.exists():
                    self._job_room(cached.stat().st_size)
                    self._link(cached, destination)
                    frame.update(aligned_cache_key=key, aligned_cache_hit=True)
                    self._aligned_summary(destination, frame)
                    return
            future = self.align_flights.get(key)
            owner = future is None
            if owner:
                future = self.align_flights[key] = Future()
        if not owner:
            while True:
                self._check(job)
                done, _ = wait([future], timeout=.1)
                if done:
                    try:
                        future.result()
                    except InterruptedError:
                        self._check(job)
                        with self.lock:
                            if self.align_flights.get(key) is future:
                                self.align_flights.pop(key)
                        return self._align(job, frame, record, source)
                    with self.lock:
                        if not cached.exists():
                            raise DataError("Aligned cache expired before it could be pinned; retry preparation.", 503)
                        self._job_room(cached.stat().st_size)
                        self._link(cached, destination)
                    frame.update(aligned_cache_key=key, aligned_cache_hit=True)
                    self._aligned_summary(destination, frame)
                    return
        temporary = destination.with_suffix(".part")
        try:
            self._build_science(job, record, source, policy, temporary)
            self._check(job)
            with self.lock:
                self._cache_room(temporary.stat().st_size)
                temporary.replace(cached)
                self._link(cached, destination)
                future.set_result(True)
            frame.update(aligned_cache_key=key, aligned_cache_hit=False)
            self._aligned_summary(destination, frame)
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            with self.lock:
                if self.align_flights.get(key) is future:
                    self.align_flights.pop(key, None)

    @staticmethod
    def _link(source, destination):
        if not destination.exists():
            os.link(source, destination)

    def _build_science(self, job, record, source, policy, destination):
        req = job["request"]
        wcs = output_wcs(req["ra"], req["dec"], req["dimension"])
        shape = (req["dimension"], req["dimension"])
        with fits.open(source, mode="readonly", memmap=False) as hdus:
            image = np.asarray(hdus["IMAGE"].data)
            variance = np.array(hdus["VARIANCE"].data, copy=True)
            flags = np.asarray(hdus["FLAGS"].data)
            header = hdus["IMAGE"].header
            if image.ndim != 2 or flags.shape != image.shape or variance.shape != image.shape or not np.issubdtype(flags.dtype, np.integer):
                raise ValueError("Original IMAGE/FLAGS/VARIANCE shapes or types disagree")
            factor = u.Unit(header.get("BUNIT", "")).to(u.MJy / u.sr)
            celestial = _celestial(header)
            bad = fits_processor.quality_mask(image, flags, variance, hdus["FLAGS"].header)
            aligned, footprint = reproject_interp((image.astype(float) * factor, celestial), wcs,
                                                  shape_out=shape, order="bilinear", parallel=False)
            self._check(job)
            # Reproject pixel IDs with nearest-neighbor, then gather flags as
            # integers: even uint64 bitmaps never pass through floating values.
            indices, nn_footprint = reproject_interp((np.arange(image.size, dtype=float).reshape(image.shape), celestial),
                                                      wcs, shape_out=shape, order="nearest-neighbor", parallel=False)
            nearest_flags = np.zeros(shape, dtype=flags.dtype)
            covered = np.isfinite(indices) & (nn_footprint > 0) & (footprint > 0) & np.isfinite(aligned)
            nearest_flags[covered] = flags.ravel()[np.rint(indices[covered]).astype(np.int64)]
            quality_support, _ = reproject_interp((bad.astype(float), celestial), wcs,
                                                  shape_out=shape, order="bilinear", parallel=False)
            mask = ~covered | ~np.isfinite(quality_support) | (quality_support > 1e-10)
            aligned[~covered] = np.nan
            footprint[~covered] = 0
            provenance = {"source_sha256": record["sha256"], "source_url": record.get("source_url"),
                          "asset_id": record["id"], "source_product_id": record.get("product_id"),
                          "policy": policy, "observation_time": observation_time(hdus),
                          "variance_warning": VARIANCE_WARNING, "comparison_warning": COMPARISON_WARNING,
                          "processing": "Astropy WCS and reproject_interp; calibrated surface brightness, no zodiacal subtraction",
                          "coverage_semantics": "Interpolation footprint, not exposure weight or uncertainty",
                          "flags_semantics": "Nearest original bitmap; zero outside coverage has no meaning; consult MASK/COVERAGE"}
            primary = fits.Header()
            primary["DERIVED"] = True
            primary["PRODTYPE"] = "ALIGNED_SEQUENCE"
            primary["ORIGSHA"] = record["sha256"]
            primary["VARPROP"] = "OMITTED"
            primary["DATE"] = datetime.now(timezone.utc).isoformat()
            common = wcs.to_header()
            common["BUNIT"] = "MJy/sr"
            common["DATE-OBS"] = provenance["observation_time"]["timestamp"].removesuffix("Z")
            common["MJD-OBS"] = provenance["observation_time"]["mjd"]
            common["HISTORY"] = "Derived bilinear IMAGE; original FITS bytes were not modified."
            original_header = celestial.to_header(relax=True)
            original_header["ALIGNED"] = False
            if "BUNIT" in hdus["VARIANCE"].header:
                original_header["BUNIT"] = hdus["VARIANCE"].header["BUNIT"]
            original_header["HISTORY"] = "Original source-grid variance. NOT the uncertainty of aligned IMAGE."
            flag_header = wcs.to_header()
            for card in hdus["FLAGS"].header.cards:
                if card.keyword.startswith("MP_"):
                    flag_header.append(card)
            flag_header["HISTORY"] = "Nearest original integer bitmap. Outside COVERAGE=0, values are undefined."
            text = json.dumps(provenance, sort_keys=True, allow_nan=False)
            result = fits.HDUList([
                fits.PrimaryHDU(header=primary), fits.ImageHDU(aligned.astype(np.float32), header=common, name="IMAGE"),
                fits.ImageHDU(footprint.astype(np.float32), header=wcs.to_header(), name="COVERAGE"),
                fits.ImageHDU(nearest_flags, header=flag_header, name="FLAGS"),
                fits.ImageHDU(mask.astype(np.uint8), header=wcs.to_header(), name="MASK"),
                fits.ImageHDU(variance, header=original_header, name="ORIGVAR"),
                fits.BinTableHDU.from_columns([fits.Column(name="JSON", format=f"{len(text)}A", array=[text])], name="PROVENANCE")])
            self._check(job)
            result.writeto(destination, checksum=True)

    @staticmethod
    def _aligned_summary(path, frame):
        with fits.open(path, memmap=False) as hdus:
            frame["coverage_fraction"] = float(np.mean(hdus["COVERAGE"].data > 0))
            frame["usable_fraction"] = float(np.mean(hdus["MASK"].data == 0))
            frame["original_variance"] = {"asset_id": frame["asset_id"], "hdu": "VARIANCE",
                                           "retained_in_derived_fits": "ORIGVAR", "grid": "original source pixels"}

    def _render(self, job, ready):
        if not ready:return
        display = dict(job["request"]["display"])
        if display["low"] is None:
            samples = []
            for frame in ready:
                self._check(job)
                path = self.root / "jobs" / job["id"] / (frame["id"] + ".fits")
                with fits.open(path, memmap=False) as hdus:
                    image, mask = hdus["IMAGE"].data, hdus["MASK"].data
                    values = image[(mask == 0) & np.isfinite(image)]
                    if values.size:
                        samples.append(values[::max(1, math.ceil(values.size / 10000))])
            if not samples:
                raise ValueError("No finite quality-accepted pixels for a common display stretch")
            low, high = np.percentile(np.concatenate(samples), [1, 99])
            if high <= low:
                high = low + max(1e-6, abs(low) * 1e-6)
            display.update(low=float(low), high=float(high), sample_policy="Shared 1st/99th percentiles; <=10000 deterministic samples/frame")
        else:
            display["sample_policy"] = "Manual common physical-value display bounds"
        display.update(unit="MJy/sr", display_only=True, png_origin="upper; FITS rows reversed so celestial north is up")
        for frame in ready:
            self._check(job)
            path = self.root / "jobs" / job["id"] / (frame["id"] + ".fits")
            with fits.open(path, memmap=False) as hdus:
                data, mask = hdus["IMAGE"].data, hdus["MASK"].data.astype(bool)
                valid = np.isfinite(data) & ~mask
                value = np.clip(np.nan_to_num((data.astype(float) - display["low"]) / (display["high"] - display["low"])), 0, 1)
                if display["stretch"] == "asinh":
                    value = np.arcsinh(value / display["asinh_a"]) / np.arcsinh(1 / display["asinh_a"])
                byte = np.rint(value * 255).astype(np.uint8)
                rgba = np.dstack([byte, byte, byte, valid.astype(np.uint8) * 255])
                with self.lock:
                    self._job_room(rgba.nbytes + mask.size + 8192)
                Image.fromarray(rgba[::-1]).save(path.with_suffix(".png"))
                Image.fromarray(mask[::-1].astype(np.uint8) * 255).save(path.with_suffix(".mask.png"))
        with self.lock:
            job["display"] = display

    def _publish_progress(self,job):
        frames=sorted((f for f in job['frame_states'] if f['status'] in ('aligned','ready')),key=lambda f:(f['mjd'],f['candidate_index']))
        published=[f for f in frames if f['status']=='ready']
        if job['request']['mode']=='comparable':
            seeds=frames
            groups=[]
            for seed in seeds:
                group=[seed]
                for f in frames:
                    if f in group:continue
                    if all(f.get('detector')==g.get('detector') and spectral.compatible(f['spectral'],g['spectral'],job['request']['spectral_tolerance_fraction']) and spectral.compatible(g['spectral'],f['spectral'],job['request']['spectral_tolerance_fraction']) for g in group):group.append(f)
                groups.append(group)
            frames=max(groups,key=lambda g:(len(g),max(f['mjd'] for f in g)-min(f['mjd'] for f in g)),default=[])
        if len(frames)<2:return
        chosen={f['id'] for f in frames}
        for old in published:
            if old['id'] not in chosen:old['status']='aligned'
        fresh=[f for f in frames if f['status']!='ready']
        if not fresh:return
        # First pair establishes one physical stretch, retained for every added epoch.
        self._render(job,frames if not published else fresh)
        if not published:
            job['request']['display'].update(low=job['display']['low'],high=job['display']['high'])
            job['warnings'].append('Progressive display limits frozen from first accepted group; no per-frame normalization.')
        with self.lock:
            for f in frames:f['status']='ready'

    def _run(self, job):
        pending = iter(deepcopy([f for f in job["frame_states"][:job["request"]["candidate_budget"]] if f["status"] == "pending"]))
        active = {}
        try:
            while True:
                self._check(job)
                while len(active) < 2:
                    frame = next(pending, None)
                    if frame is None:
                        break
                    active[self.workers.submit(self._one, job, frame)] = frame
                if not active:
                    break
                done, _ = wait(active, timeout=.1, return_when=FIRST_COMPLETED)
                for future in done:
                    original = active.pop(future)
                    try:
                        frame = future.result()
                    except Exception as exc:
                        frame = _reason(original, "failed", "preparation_failed", str(exc))
                    self._update(job, frame["id"], **{k: v for k, v in frame.items() if k != "id"})
                if job['request']['adaptive']:
                    self._publish_progress(job)
                    if len([f for f in job['frame_states'] if f['status']=='ready'])>=job['request']['max_frames']:
                        break
            self._check(job)
            aligned = sorted((f for f in job["frame_states"] if f["status"] in ("aligned","ready")), key=lambda f: (f["mjd"], f["candidate_index"]))
            ready = aligned
            if job["request"]["mode"] == "comparable" and aligned:
                tolerance = job["request"]["spectral_tolerance_fraction"]
                # Bounded complete-link seed search. Every accepted pair must
                # pass the existing target CWAVE/CBAND rule in both directions.
                # This avoids anchoring to an isolated earliest wavelength while
                # an actually comparable multi-epoch group is already prepared.
                groups = []
                for seed in aligned:
                    group = [seed]
                    for candidate in aligned:
                        if candidate is seed:
                            continue
                        if all(spectral.compatible(candidate["spectral"], f["spectral"], tolerance)
                               and spectral.compatible(f["spectral"], candidate["spectral"], tolerance)
                               for f in group):
                            group.append(candidate)
                    groups.append(sorted(group, key=lambda f: (f["mjd"], f["candidate_index"])))
                ready = min(groups, key=lambda g: (-len(g), -(g[-1]['mjd']-g[0]['mjd']), tuple((f["mjd"], f["candidate_index"]) for f in g)))
                chosen = {f["id"] for f in ready}
                for frame in aligned:
                    if frame["id"] not in chosen:
                        self._update(job, frame["id"], status="excluded", reason_code="spectral_mismatch",
                                     reason="Outside deterministically selected largest seed-built pairwise CWAVE/CBAND-compatible group; all prepared pairs checked in both directions")
                with self.lock:
                    job["warnings"].append("Comparable group selection: largest deterministic complete-link seed group among the explicitly prepared candidate subset; not a claim of globally exhaustive spectral grouping.")
            if not ready:
                with self.lock:
                    job["status"] = "failed" if any(f["status"] == "failed" for f in job["frame_states"]) else "insufficient"
                    job["error"] = "No sequence frames satisfy the requested quality, date and spectral policy"
                return
            self._render(job, [f for f in ready if f["status"]!="ready"])
            with self.lock:
                self._check(job)
                for frame in ready:
                    frame["status"] = "ready"
                job["status"] = "ready" if len(ready) >= 2 else "insufficient"
                if len(ready) < 2:
                    job["warnings"].append("Only one frame satisfies the sequence policy; inspect it individually. Time playback needs at least two frames.")
        except InterruptedError:
            with self.lock:
                job["status"] = "cancelled"
        except Exception as exc:
            with self.lock:
                accepted=sum(f['status']=='ready' for f in job['frame_states'])
                job["status"] = ('ready' if accepted>=2 else 'insufficient') if accepted else 'failed'
                job["error"] = str(exc) or type(exc).__name__
                if accepted:job['warnings'].append('Preparation ended early; only previously validated published frames remain available. '+str(exc))
        finally:
            # Running archive retrieval cannot be killed safely. Keep capacity
            # and raw leases until workers finish; no late result is published.
            for future in active:
                future.cancel()
            if active:
                wait(active)
            with self.lock:
                for frame in job["frame_states"]:
                    if frame["status"] in ("pending", "retrieving", "inspecting", "aligning", "aligned"):
                        if job['status']=='ready':
                            _reason(frame,'excluded','budget_or_goal','Not included in accepted group before preparation budget/goal ended')
                            continue
                        _reason(frame, "cancelled" if job["status"] == "cancelled" else "failed",
                                "cancelled" if job["status"] == "cancelled" else "job_failed",
                                "Sequence cancelled" if job["status"] == "cancelled" else job.get("error", "Job did not complete"))
                job["touched"] = time.monotonic()
                job["finished"] = True
                if job["delete_requested"] and not job["serving"]:
                    shutil.rmtree(self.root / "jobs" / job["id"], ignore_errors=True)
                    self.jobs.pop(job["id"], None)

    def frame_path(self, identifier, frame_id, kind="preview"):
        extension = {"preview": ".png", "fits": ".fits", "mask": ".mask.png"}.get(kind)
        if extension is None:
            raise DataError("Frame kind must be preview, fits or mask.", 422)
        with self.lock:
            job = self._job(identifier)
            if job["delete_requested"] or job["status"] not in ("preparing", "ready", "insufficient", "cancelled") or not any(f["id"] == frame_id and f["status"] == "ready" for f in job["frame_states"]):
                raise DataError("Sequence frame is not ready or does not belong to this job.", 409)
            job["touched"] = time.monotonic()
            path = self.root / "jobs" / identifier / (frame_id + extension)
            if not path.is_file():
                raise DataError("Prepared frame expired; prepare the sequence again.", 404)
            return path

    @contextmanager
    def frame_lease(self, identifier, frame_id, kind="preview"):
        """Pin a job for the full HTTP response lifetime, including streaming."""
        with self.lock:
            path = self.frame_path(identifier, frame_id, kind)
            job = self._job(identifier)
            job["serving"] += 1
        try:
            yield path
        finally:
            with self.lock:
                job["serving"] -= 1
                if job["delete_requested"] and job["finished"] and not job["serving"]:
                    shutil.rmtree(self.root / "jobs" / identifier, ignore_errors=True)
                    self.jobs.pop(identifier, None)

    def cancel(self, identifier):
        with self.lock:
            job = self._job(identifier)
            if job["status"] == "preparing":
                job["event"].set()
                if job["future"].cancel():
                    job["status"] = "cancelled"
                    job["finished"] = True
                    for frame in job["frame_states"]:
                        if frame["status"] == "pending":
                            _reason(frame, "cancelled", "cancelled", "Cancelled before preparation")
        return self.status(identifier)

    def delete(self, identifier):
        with self.lock:
            job = self._job(identifier)
            job["delete_requested"] = True
            job["event"].set()
            if (not job["finished"] and not job["future"].cancel()) or job["serving"]:
                return {"id": identifier, "status": "deleting"}
            shutil.rmtree(self.root / "jobs" / identifier, ignore_errors=True)
            self.jobs.pop(identifier, None)
        return {"id": identifier, "status": "deleted"}

    def shutdown(self, wait=True):
        with self.lock:
            self.closed = True
            identifiers = list(self.jobs)
        for identifier in identifiers:
            try:
                self.cancel(identifier)
            except DataError:
                pass
        self.coordinators.shutdown(wait=wait, cancel_futures=True)
        self.workers.shutdown(wait=wait, cancel_futures=True)


_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def _service():
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = SequenceService()
        return _DEFAULT


def prepare(request_dict):
    return _service().prepare(request_dict)


def status(identifier):
    return _service().status(identifier)


def frame_path(identifier, frame_id, kind="preview"):
    return _service().frame_path(identifier, frame_id, kind)


def frame_lease(identifier, frame_id, kind="preview"):
    return _service().frame_lease(identifier, frame_id, kind)


def cancel(identifier):
    return _service().cancel(identifier)


def delete(identifier):
    return _service().delete(identifier)


def shutdown(wait=True):
    global _DEFAULT
    with _DEFAULT_LOCK:
        previous, _DEFAULT = _DEFAULT, None
    if previous is not None:
        previous.shutdown(wait=wait)
