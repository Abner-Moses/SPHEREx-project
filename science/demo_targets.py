"""Five-target science bridge for the demo (Barnard's Star, Epsilon Indi B, WISE 0855-0714, RW Aurigae, R Draconis).

Everything scientific is computed by the SphereX2 science modules in this folder; this file only:
  * pins the five targets (aliases, J2000 astrometry, which position to measure at),
  * decides which data source to use, in a fixed order: local dataset (SPHEREX_DATA_ROOT) -> persistent IRSA cache
    (science/cache) -> live IRSA (only if SPHEREX_OFFLINE is not 1),
  * runs one bounded analysis per target in a background thread and stores the bundle on disk, so the demo is instant
    after the first run and still works with the network off.

Routes (mounted by main.py under /api/demo):
  GET  /targets                      the five targets + what data exists for each
  GET  /match?ra=&dec=&radius=        which of the five fall inside a capture frame (epoch-propagated positions)
  POST /targets/{id}/analysis         start (or reuse) the analysis job; ?refresh=1 recomputes
  GET  /targets/{id}/analysis         job status, then the bundle
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

LOG = logging.getLogger("spherex.demo")
router = APIRouter(prefix="/api/demo", tags=["demo targets"])
HERE = Path(__file__).resolve().parent
OUT = Path(os.getenv("SPHEREX_DEMO_CACHE", str(HERE / "cache" / "demo"))).resolve()
MAX_PRODUCTS = int(os.getenv("SPHEREX_DEMO_MAX_PRODUCTS", "12"))  # observations measured per target (bounded for the demo)
EPOCH_YEAR = float(os.getenv("SPHEREX_DEMO_EPOCH", "2025.8"))  # SPHEREx survey mid-epoch used to place high proper-motion targets

# J2000 ICRS astrometry. Proper motion in mas/yr (pmra includes cos dec). SIMBAD is consulted first at run time; these
# values are the offline fallback and are marked as such in every bundle.
TARGETS = {
    "barnards-star": {"name": "Barnard's Star", "aliases": ["barnard's star", "barnards star", "gj 699", "v2500 oph"], "simbad": "Barnard's star",
                      "ra": 269.452083, "dec": 4.693364, "pmra": -801.551, "pmdec": 10362.394, "plx_mas": 546.976, "kind": "Red dwarf (M4V), highest proper motion known"},
    "eps-indi-b": {"name": "Epsilon Indi B", "aliases": ["epsilon indi b", "eps ind b", "eps indi b", "epsilon indi ba"], "simbad": "eps Ind B",
                   "ra": 331.043833, "dec": -56.782694, "pmra": 3982.0, "pmdec": -2470.0, "plx_mas": 274.8, "kind": "Binary brown dwarf (T1 + T6)"},
    "wise-0855": {"name": "WISE 0855-0714", "aliases": ["wise 0855", "wise 0855-0714", "wise j085510.83-071442.5", "wise0855"], "simbad": "WISE J085510.83-071442.5",
                  "ra": 133.7958, "dec": -7.2451, "pmra": -8118.0, "pmdec": 680.0, "plx_mas": 438.0, "kind": "Coldest known brown dwarf (Y4)"},
    "rw-aurigae": {"name": "RW Aurigae", "aliases": ["rw aurigae", "rw aur"], "simbad": "RW Aur",
                   "ra": 76.956417, "dec": 30.401417, "pmra": 4.0, "pmdec": -23.0, "plx_mas": 6.4, "kind": "Young T Tauri binary with a disk"},
    "r-draconis": {"name": "R Draconis", "aliases": ["r draconis", "r dra"], "simbad": "R Dra",
                   "ra": 248.167375, "dec": 66.754978, "pmra": -14.0, "pmdec": 28.0, "plx_mas": 1.5, "kind": "Mira variable (long-period pulsating giant)"},
}

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_slot = threading.BoundedSemaphore(1)  # one analysis at a time: the science modules hold a single research slot


def _propagate(t: dict, year: float) -> tuple[float, float]:
    dt = year - 2000.0
    dec = t["dec"] + t["pmdec"] * dt / 3.6e6
    ra = t["ra"] + t["pmra"] * dt / 3.6e6 / max(1e-9, math.cos(math.radians(t["dec"])))
    return ra % 360.0, max(-90.0, min(90.0, dec))


def _sep_deg(a1, d1, a2, d2) -> float:
    r = math.radians
    c = math.sin(r(d1)) * math.sin(r(d2)) + math.cos(r(d1)) * math.cos(r(d2)) * math.cos(r(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _target(tid: str) -> dict:
    t = TARGETS.get(tid)
    if not t:
        raise HTTPException(404, f"Unknown demo target '{tid}'. Known: {', '.join(TARGETS)}")
    return t


def _astrometry(tid: str) -> dict:
    """SIMBAD first (cached on disk), else the pinned values; never silently mixed."""
    t = _target(tid)
    path = OUT / f"{tid}.astrometry.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    astro = {**{k: t[k] for k in ("ra", "dec", "pmra", "pmdec", "plx_mas")}, "source": "pinned J2000 values (offline fallback)"}
    if os.getenv("SPHEREX_OFFLINE", "0") != "1":
        try:
            import sky_services  # our SIMBAD client (project root)
            o = sky_services.resolve_name(t["simbad"])
            if o and _sep_deg(o["ra"], o["dec"], t["ra"], t["dec"]) < 0.5:
                astro = {"ra": o["ra"], "dec": o["dec"], "pmra": o.get("pmra_mas_yr") or t["pmra"], "pmdec": o.get("pmdec_mas_yr") or t["pmdec"],
                         "plx_mas": o.get("parallax_mas") or t["plx_mas"], "source": "SIMBAD (CDS)"}
        except Exception as exc:  # network / SIMBAD outage: keep the pinned values
            LOG.info("demo astrometry fallback for %s: %s", tid, exc)
    OUT.mkdir(parents=True, exist_ok=True)
    if astro["source"].startswith("SIMBAD"):
        path.write_text(json.dumps(astro))
    return astro


def _local_status(tid: str) -> dict:
    """What the local dataset holds for this target (0 when the dataset is not installed yet)."""
    try:
        import local_science
        if not local_science.ROOT.exists():
            return {"installed": False, "root": str(local_science.ROOT), "cubes": 0, "observations": 0}
        name = "WISE 0855" if tid == "wise-0855" else TARGETS[tid]["name"]
        key = lambda s: "".join(c for c in s.lower() if c.isalnum())
        canonical = local_science._TARGET_NAMES
        rows = [r for r in local_science.index() if (canonical.get(key(name)) is not None and canonical.get(key(r["target"])) == canonical.get(key(name))) or key(r["target"]) == key(name)]
        return {"installed": True, "root": str(local_science.ROOT), "cubes": sum(r["kind"] == "cubes" for r in rows), "observations": sum(r["kind"] == "observations" for r in rows)}
    except Exception as exc:
        return {"installed": False, "error": str(exc), "cubes": 0, "observations": 0}


@router.get("/targets")
def targets():
    out = []
    for tid, t in TARGETS.items():
        ra, dec = _propagate(t, EPOCH_YEAR)
        bundle = OUT / f"{tid}.json"
        out.append({"id": tid, "name": t["name"], "kind": t["kind"], "ra_j2000": t["ra"], "dec_j2000": t["dec"], "ra_epoch": ra, "dec_epoch": dec,
                    "epoch": EPOCH_YEAR, "local": _local_status(tid), "bundle_cached": bundle.exists(),
                    "bundle_age_s": time.time() - bundle.stat().st_mtime if bundle.exists() else None})
    return {"targets": out, "offline": os.getenv("SPHEREX_OFFLINE", "0") == "1", "max_products": MAX_PRODUCTS}


@router.get("/match")
def match(ra: float = Query(ge=0, lt=360), dec: float = Query(ge=-90, le=90), radius: float = Query(gt=0, le=90)):
    """Targets inside a capture circle. Checked at J2000, at the survey epoch and today, so fast movers still match."""
    hits = []
    year_now = 1970 + time.time() / 31557600
    for tid, t in TARGETS.items():
        d = min(_sep_deg(ra, dec, *_propagate(t, y)) for y in (2000.0, EPOCH_YEAR, year_now))
        if d <= radius:
            hits.append({"id": tid, "name": t["name"], "separation_deg": d})
    return {"targets": sorted(hits, key=lambda h: h["separation_deg"])}


def resolve_alias(text: str) -> str | None:
    k = text.strip().lower().replace("–", "-").replace("−", "-")
    for tid, t in TARGETS.items():
        if k == t["name"].lower() or k in t["aliases"]:
            return tid
    return None


def _try(label: str, fn, errors: list):
    try:
        return fn()
    except HTTPException as exc:
        errors.append({"step": label, "reason": str(exc.detail)})
    except Exception as exc:
        errors.append({"step": label, "reason": str(exc) or exc.__class__.__name__})
    return None


def _analyse(tid: str) -> dict:
    """One bounded analysis. Each step is independent: a failure is recorded with its reason, never replaced by made-up data."""
    import local_science
    import observation_service
    import research_analysis
    import native_analysis
    import measurements_api
    from pydantic import ValidationError  # noqa: F401  (surface model errors as reasons)

    t = TARGETS[tid]
    astro = _astrometry(tid)
    ra, dec = _propagate({**t, **astro}, EPOCH_YEAR)
    errors: list[dict] = []
    started = time.time()
    bundle = {"id": tid, "name": t["name"], "kind": t["kind"], "astrometry": astro, "measure_at": {"ra": ra, "dec": dec, "epoch": EPOCH_YEAR,
              "note": "J2000 position moved by proper motion to the survey mid-epoch; apertures are 3 px (about 18 arcsec)."},
              "sources": [], "errors": errors}

    # 1) local spectral cube spectrum (local dataset only)
    field = _try("local field", lambda: local_science.target_field("WISE 0855" if tid == "wise-0855" else t["name"]), errors) if local_science.ROOT.exists() else None
    if field and field.get("provenance", {}).get("product_id"):
        key = field["provenance"]["product_id"]
        spec = _try("local cube spectrum", lambda: local_science.spectrum(key, ra, dec, radius=2), errors)
        if spec:
            bundle["cube_spectrum"] = spec
            bundle["sources"].append("local dataset: DR1 spectral cube")

    # 2) discovery: local dataset -> exact persistent cache -> live IRSA (unless offline)
    found = None
    if local_science.ROOT.exists():
        found = _try("local discovery", lambda: local_science.discover(ra, dec), errors)
        if found and found.get("observations"):
            bundle["sources"].append("local dataset: Level-2 observations")
    if not (found and found.get("observations")):
        if os.getenv("SPHEREX_OFFLINE", "0") == "1":
            errors.append({"step": "discovery", "reason": "Offline and no local observations for this target."})
        else:
            for attempt in range(3):  # IRSA drops connections now and then: retry with a short backoff
                step_errors: list = []
                found = _try("IRSA discovery", lambda: observation_service.search(ra, dec, 0, .1, True, False), step_errors)
                if found is not None or attempt == 2:
                    errors.extend(step_errors)
                    break
                time.sleep(2 + attempt * 3)
            if found and found.get("observations"):
                bundle["sources"].append("IRSA archive (cached after first use)" + (" · cache hit" if found.get("cache_hit") else ""))
    if not (found and found.get("observations")):
        near = _nearby_cached_query(ra, dec)
        if near:
            found = near
            bundle["sources"].append("persistent IRSA query cache (nearby position)")
    rows = (found or {}).get("observations", [])
    observation_service.register_catalog(rows) if rows else None
    ids = [r.get("id") or r.get("product_id") for r in rows]
    ids = [i for i in ids if isinstance(i, str) and len(i) == 32][:MAX_PRODUCTS]
    bundle["observations"] = {"found": len(rows), "measured_limit": MAX_PRODUCTS, "used": len(ids),
                              "rows": [{k: r.get(k) for k in ("id", "obs_id", "observation_id", "t_min", "date_obs", "detector", "collection", "s_ra", "s_dec") if k in r} for r in rows[:200]]}

    # 3) calibrated aperture spectrophotometry across those observations (SED + time samples + continuum fit)
    if ids:
        req = research_analysis.Extract(product_ids=ids, ra=ra, dec=dec, radius_px=3)
        sp = _try("spectrophotometry", lambda: research_analysis.extract(req), errors)
        if sp:
            bundle["spectrophotometry"] = sp
            assets = [p["asset_id"] for p in sp.get("points", [])]
            # frames the strict policy rejected but that were retrieved: measured again, labelled exploratory
            inspect_only = [r["asset_id"] for r in sp.get("rejected", []) if r.get("asset_id")]
            if inspect_only:
                bundle["exploratory"] = _exploratory(inspect_only, ra, dec, errors)
            assets = assets or [p["asset_id"] for p in bundle.get("exploratory", {}).get("points", [])] or inspect_only
            # 4) image-level measurements on the first accepted asset
            if assets:
                a = assets[0]
                bundle["asset_id"] = a
                bundle["radial_profile"] = _try("radial profile", lambda: native_analysis.radial_profile(a, ra, dec, 15), errors)
                bundle["epsf"] = _try("ePSF", lambda: native_analysis.epsf(a, ra, dec), errors)
                bundle["diagnostics"] = _try("diagnostics", lambda: native_analysis.diagnostics(a), errors)
                bundle["statistics"] = _try("pixel statistics", lambda: measurements_api.statistics(a, 64), errors)
                bundle["surface"] = _try("surface", lambda: measurements_api.surface(a, 96), errors)
                bundle["quality"] = _try("quality summary", lambda: measurements_api.quality_summary(a), errors)
            if len(assets) >= 2:
                creq = research_analysis.Extract(asset_ids=assets[:MAX_PRODUCTS], ra=ra, dec=dec, radius_px=3)
                bundle["centroids"] = _try("centroids", lambda: native_analysis.centroids(creq), errors)
    bundle["elapsed_s"] = round(time.time() - started, 2)
    bundle["computed_at"] = time.time()
    accepted = len((bundle.get("spectrophotometry") or {}).get("points", []))
    # Cube coverage is real, but its flags/uncertainty have no established acceptance.
    cube_values = (bundle.get("cube_spectrum") or {}).get("surface_brightness", [])
    exploratory = len((bundle.get("exploratory") or {}).get("points", [])) + sum(
        isinstance(value, (int, float)) and math.isfinite(value) for value in cube_values
    )
    bundle["status"] = "science" if accepted else "exploratory" if exploratory else "unavailable"
    return bundle


def _nearby_cached_query(ra: float, dec: float, within_deg: float = 0.05) -> dict | None:
    """Any earlier archive query centred within `within_deg` of the target (IRSA outage fallback)."""
    import observation_service
    best = None
    for path in (observation_service.CACHE / "queries").glob("*.json"):
        try:
            d = json.loads(path.read_text())
            q = d.get("query") or {}
            if not isinstance(q.get("ra"), (int, float)) or not isinstance(q.get("dec"), (int, float)) or not d.get("observations"):
                continue
            sep = _sep_deg(ra, dec, q["ra"], q["dec"])
            if sep <= within_deg and (best is None or sep < best[0]):
                best = (sep, d)
        except (OSError, ValueError):
            continue
    if not best:
        return None
    return {**best[1], "cache_hit": True, "fallback": {"reason": f"IRSA unavailable; reused a cached query {best[0] * 3600:.0f} arcsec away"}}


def _exploratory(asset_ids: list[str], ra: float, dec: float, errors: list) -> dict:
    """Aperture photometry + bandpass on frames that failed scientific acceptance. Same code path as the strict
    measurement, minus the acceptance gate; every point carries its frame's quality flags so nothing is overstated."""
    import observation_service as assets
    import scientific_measurements as measures
    import sequence_service as sequences
    import sequence_spectral as spectral
    from astropy.io import fits

    points, skipped = [], []
    for aid in asset_ids[:MAX_PRODUCTS]:
        try:
            with assets.asset_lease(aid) as path:
                record = assets.get_observation(aid)
                quantity = "aperture_flux_jy"
                try:
                    flux = measures.photometry(aid, ra=ra, dec=dec, radius=4, annulus_inner=7, annulus_outer=12)
                    value, lower = flux.get("flux_jy"), False
                    if value is None:  # flagged core pixels (bright or saturated star): keep the valid-pixel sum as a lower limit
                        value, lower = flux.get("valid_contribution_flux_jy"), True
                except Exception:
                    # saturated star (no usable aperture at all): innermost unflagged ring of the native radial profile,
                    # a surface brightness in MJy/sr and a lower limit on the true peak
                    import native_analysis
                    ring = (native_analysis.radial_profile(aid, ra, dec, 15).get("visualization_descriptors") or [{}])[0].get("points") or []
                    if not ring:
                        raise
                    flux, value, lower, quantity = {"flux_err_jy": ring[0].get("error"), "ring_radius_px": ring[0].get("x")}, ring[0].get("y"), True, "ring_surface_brightness_mjy_sr"
                if value is None or not math.isfinite(value):
                    raise ValueError("No finite aperture flux")
                try:
                    band = spectral.resolve(path, record, ra, dec, sequences._service().calibrations)
                except Exception:
                    band = {"status": "unavailable"}
                if band.get("wavelength_um") is None:
                    # calibration lookup is cooling down after an upstream failure: use the frame's own WCS-WAVE estimate,
                    # exactly as sequence_spectral.resolve labels it ("approximate", visualisation only)
                    try:
                        from astropy import units as u
                        axes = (spectral.inspector.pixel(path, ra=ra, dec=dec).get("spectral") or {}).get("axes") or []
                        if len(axes) == 2:
                            wl, bw = [(float(a["value"]) * u.Unit(a["unit"])).to_value(u.um) for a in axes]
                            if math.isfinite(wl) and wl > 0:
                                band = {"status": "approximate", "wavelength_um": wl, "bandwidth_um": bw if math.isfinite(bw) else None}
                    except Exception:
                        pass
                flux = {**flux, "flux_jy": value, "lower_limit": lower or bool(flux.get("lower_limit"))}
                with fits.open(path, memmap=False) as hdus:
                    when = sequences.observation_time(hdus)
                acc = (record.get("validation") or {}).get("scientific_acceptance") or {}
                points.append({"asset_id": aid, "observation_id": record.get("observation_id"), "flux_jy": flux["flux_jy"], "flux_err_jy": flux.get("flux_err_jy"),
                               "usable": bool(flux.get("scientifically_usable")), "lower_limit": flux["lower_limit"], "quantity": quantity, **when, "wavelength_um": band.get("wavelength_um"), "bandwidth_um": band.get("bandwidth_um"),
                               "band_status": band.get("status"), "rejection": acc.get("reasons") or acc.get("reason")})
        except Exception as exc:
            skipped.append({"asset_id": aid, "reason": str(exc) or exc.__class__.__name__})
    if skipped and not points:
        errors.append({"step": "exploratory photometry", "reason": skipped[0]["reason"]})
    points.sort(key=lambda p: (p.get("wavelength_um") or 0, p.get("mjd") or 0))
    return {"status": "exploratory", "points": points, "skipped": skipped,
            "warning": "Frames failed SPHEREx scientific acceptance (quality flags). Values are shown for exploration only and are not calibrated science measurements."}


def _jsonable(x):
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if np is not None and isinstance(x, np.generic):
        return _jsonable(x.item())
    if np is not None and isinstance(x, np.ndarray):
        return _jsonable(x.tolist())
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    if isinstance(x, (bytes, bytearray)):
        return None
    if hasattr(x, "model_dump"):
        return _jsonable(x.model_dump())
    return str(x)


def _run(tid: str):
    with _slot:
        try:
            bundle = _jsonable(_analyse(tid))
            OUT.mkdir(parents=True, exist_ok=True)
            tmp = OUT / f"{tid}.json.tmp"
            tmp.write_text(json.dumps(bundle))
            tmp.replace(OUT / f"{tid}.json")
            state = {"state": "done"}
        except Exception as exc:
            LOG.exception("demo analysis failed for %s", tid)
            state = {"state": "failed", "reason": str(exc) or exc.__class__.__name__}
    with _lock:
        _jobs[tid] = {**_jobs.get(tid, {}), **state, "finished": time.time()}


@router.post("/targets/{tid}/analysis")
def start(tid: str, refresh: bool = False):
    _target(tid)
    path = OUT / f"{tid}.json"
    with _lock:
        job = _jobs.get(tid)
        if job and job.get("state") == "running":
            return {"state": "running", "started": job["started"]}
        if path.exists() and not refresh:
            return {"state": "done", "cached": True}
        _jobs[tid] = {"state": "running", "started": time.time()}
    threading.Thread(target=_run, args=(tid,), daemon=True, name=f"demo-{tid}").start()
    return {"state": "running", "started": _jobs[tid]["started"]}


@router.get("/targets/{tid}/analysis")
def result(tid: str):
    _target(tid)
    with _lock:
        job = dict(_jobs.get(tid) or {})
    path = OUT / f"{tid}.json"
    if job.get("state") == "running":
        return {"state": "running", "elapsed_s": round(time.time() - job["started"], 1)}
    if job.get("state") == "failed":
        return {"state": "failed", "reason": job.get("reason")}
    if path.exists():
        try:
            return {"state": "done", "bundle": json.loads(path.read_text())}
        except ValueError:
            return {"state": "failed", "reason": "Stored bundle is unreadable; start a refresh."}
    return {"state": "idle"}
