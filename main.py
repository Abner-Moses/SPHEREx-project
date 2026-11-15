"""FastAPI server for the SPHEREx Sky Explorer.

  GET  /health
  POST /api/observe            {ra, dec, mode, obs_id?, detector?} -> product metadata (WCS mesh, wavelength, epoch, ...)
  GET  /api/products/{id}/image.png | mask.png | photometry
  GET  /api/observations       ?ra&dec&radius&limit              -> every archive image near a position
  GET  /api/resolve | /api/suggest | /api/stars                   -> SIMBAD name resolution, autocomplete, cone search
  POST /api/voyager            {question, humor, detail, targets, context, history} -> one JSON command for the guide

Long requests stop when the client disconnects: a watcher sets a cancel flag that the download loop checks,
so abandoned requests do not pile up 60 MB transfers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import sky_services
import voyager
import spherex_pipeline as pipeline
from spherex_pipeline import Cancelled, NotFound, PipelineError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("spherex.api")

DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173"
ORIGINS = [o.strip() for o in os.getenv("SPHEREX_CORS_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]

# ---- SphereX2 science backend (science/): imported here, mounted below. Its modules use flat imports and
# relative cache paths, so its folder joins sys.path and its caches are pinned inside science/ before import.
import sys
from contextlib import asynccontextmanager
from pathlib import Path

SCIENCE_DIR = Path(__file__).resolve().parent / "science"
# The submission includes its read-only dataset alongside this server.
os.environ.setdefault("SPHEREX_DATA_ROOT", str(SCIENCE_DIR.parent / "SphereX-data"))
os.environ.setdefault("SPHEREX_CACHE", str(SCIENCE_DIR / "cache" / "observations"))
os.environ.setdefault("SPHEREX_HIPS_CACHE", str(SCIENCE_DIR / "cache" / "hips"))
if str(SCIENCE_DIR) not in sys.path:
    sys.path.append(str(SCIENCE_DIR))  # appended: our own root modules keep precedence
SCIENCE: dict = {"available": False, "reason": "not loaded"}
try:
    import sci_main  # SphereX2 FastAPI app: local dataset, IRSA retrieval, research, lenses, sequences
    import demo_targets
    SCIENCE = {"available": True, "reason": None}
except Exception as exc:  # missing scientific dependency: the rest of the app keeps working
    sci_main = demo_targets = None
    SCIENCE = {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    logging.getLogger("spherex.api").warning("science backend unavailable: %s", SCIENCE["reason"])


@asynccontextmanager
async def lifespan(_app):
    # A mounted sub-application's lifespan never runs on its own, so run the science app's here
    # (it owns the IRSA discovery broker and shuts the sequence workers down).
    if sci_main is not None:
        async with sci_main.lifespan(sci_main.app):
            yield
    else:
        yield


app = FastAPI(title="SPHEREx Sky Explorer API", version="0.5.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"])
if sci_main is not None:
    app.include_router(demo_targets.router)  # /api/demo/*  (the five demo targets)
    app.mount("/api/sci", sci_main.app)  # /api/sci/api/*  (the full science API, unchanged)


@app.get("/api/science/status")
async def science_status() -> dict:
    """Whether the science backend loaded, and where its local dataset is expected."""
    out = dict(SCIENCE)
    if sci_main is not None:
        import local_science
        out.update(offline=local_science.OFFLINE, data_root=str(local_science.ROOT), dataset_installed=bool(local_science.index()))
    return out


@app.get("/api/dataset/observations")
async def dataset_observations(
    ra: float = Query(ge=0, lt=360),
    dec: float = Query(ge=-90, le=90),
) -> dict:
    """Search only unchanged local Level-2 footprints; never contact an archive."""
    if sci_main is None:
        raise HTTPException(503, "The local science backend is unavailable.")
    import local_science
    import observation_service
    result = await run_in_threadpool(local_science.discover, ra, dec)
    await run_in_threadpool(observation_service.register_catalog, result["observations"])
    return result


class VoyagerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=600)
    humor: int = Field(default=50, ge=0, le=100)
    detail: int = Field(default=60, ge=0, le=100)
    targets: list[dict] = Field(default_factory=list, max_length=60)
    context: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list, max_length=20)


class ObserveRequest(BaseModel):
    ra: float = Field(ge=0, le=360, description="Right ascension, degrees (ICRS)")
    dec: float = Field(ge=-90, le=90, description="Declination, degrees (ICRS)")
    mode: str = Field(default="auto", pattern="^(auto|live|mock)$")
    obs_id: str | None = Field(default=None, max_length=64, description="Pick one specific observation at this position")
    detector: int | None = Field(default=None, ge=1, le=6, description="With obs_id: which detector (band). obs_id alone is not unique")


def _http_error(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, NotFound | sky_services.UnknownObject) else 502
    return HTTPException(status_code=status, detail=str(exc))


async def _cancellable(request: Request, work):
    """Run blocking ``work(cancel_event)`` in a thread; set the event if the client disconnects."""
    cancel = threading.Event()
    done = asyncio.Event()

    async def watch() -> None:
        while not done.is_set():
            if await request.is_disconnected():
                cancel.set()
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=0.5)

    watcher = asyncio.create_task(watch())
    try:
        return await run_in_threadpool(work, cancel)
    finally:
        done.set()
        await watcher


def _product_or_404(product_id: str) -> pipeline.Product:
    product = pipeline.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Unknown or expired product. Observe the position again.")
    return product


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/observe")
async def observe(request: Request, req: ObserveRequest) -> JSONResponse:
    try:
        product = await _cancellable(
            request, lambda cancel: pipeline.observe(req.ra, req.dec, req.mode, req.obs_id, cancel=cancel, detector=req.detector)
        )
    except Cancelled as exc:
        raise HTTPException(status_code=499, detail=str(exc)) from exc
    except PipelineError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        logger.exception("Image generation failed")
        raise HTTPException(status_code=500, detail="Image generation failed.") from exc
    return JSONResponse(product.meta(target=(req.ra, req.dec)))


_PNG_HEADERS = {"Cache-Control": "public, max-age=3600"}


@app.get("/api/products/{product_id}/image.png")
async def product_image(product_id: str) -> Response:
    return Response(_product_or_404(product_id).png_bytes(), media_type="image/png", headers=_PNG_HEADERS)


@app.get("/api/products/{product_id}/mask.png")
async def product_mask(product_id: str) -> Response:
    return Response(_product_or_404(product_id).mask_png_bytes(), media_type="image/png", headers=_PNG_HEADERS)


@app.get("/api/products/{product_id}/photometry")
async def product_photometry(
    product_id: str,
    x: float = Query(description="Image x (0 = left edge of the left pixel)"),
    y: float = Query(description="Image y (0 = top edge of the top pixel)"),
    r: float = Query(default=3.0, gt=0, le=50, description="Aperture radius, pixels"),
    r_in: float = Query(default=6.0, gt=0, le=90),
    r_out: float = Query(default=10.0, gt=0, le=100),
) -> dict:
    product = _product_or_404(product_id)
    try:
        return await run_in_threadpool(product.photometry, x, y, r, r_in, r_out)
    except PipelineError as exc:
        raise _http_error(exc) from exc


@app.get("/api/observations")
async def observations(
    ra: float = Query(ge=0, le=360),
    dec: float = Query(ge=-90, le=90),
    radius: float = Query(default=pipeline.DEFAULT_SELECT_RADIUS_DEG, gt=0, le=pipeline.MAX_SEARCH_RADIUS_DEG),
    limit: int = Query(default=pipeline.SEARCH_LIMIT, ge=1, le=500),
) -> dict:
    try:
        rows = await run_in_threadpool(pipeline.search_observations, ra, dec, radius, limit)
    except PipelineError as exc:
        raise _http_error(exc) from exc
    local = bool(rows) and all(row.get("data_origin") == "local dataset" for row in rows)
    collections = sorted({row.get("collection", pipeline.COLLECTION) for row in rows})
    return {
        "ra": ra, "dec": dec, "radius_deg": radius,
        "collection": collections[0] if len(collections) == 1 else "mixed" if collections else pipeline.COLLECTION,
        "collections": collections, "scope": "indexed local covering images only" if local else "IRSA archive search",
        "count": len(rows), "truncated": len(rows) >= limit,
        "observations": rows,
    }


@app.get("/api/resolve")
async def resolve(q: str = Query(min_length=1, max_length=100)) -> dict:
    if demo_targets is not None and os.getenv("SPHEREX_OFFLINE", "0") == "1":
        tid = demo_targets.resolve_alias(q)
        if tid:
            astro = demo_targets._astrometry(tid)
            target = demo_targets.TARGETS[tid]
            result = sky_services._obj(target["name"], astro["ra"], astro["dec"], astro["pmra"], astro["pmdec"], astro["plx_mas"], None, None)
            return {**result, "astrometry_source": astro["source"]}
    try:
        return await run_in_threadpool(sky_services.resolve_name, q)
    except sky_services.ServiceError as exc:
        raise _http_error(exc) from exc


@app.get("/api/suggest")
async def suggest(q: str = Query(min_length=1, max_length=60)) -> dict:
    if demo_targets is not None and os.getenv("SPHEREX_OFFLINE", "0") == "1":
        matches = [t["name"] for t in demo_targets.TARGETS.values()
                   if any(q.lower() in alias.lower() for alias in [t["name"], *t["aliases"]])]
        return {"query": q, "results": [await resolve(name) for name in matches]}
    try:
        return {"query": q, "results": await run_in_threadpool(sky_services.suggest, q)}
    except sky_services.ServiceError as exc:
        raise _http_error(exc) from exc


@app.get("/api/stars")
async def stars(
    ra: float = Query(ge=0, le=360),
    dec: float = Query(ge=-90, le=90),
    radius: float = Query(gt=0, le=sky_services.MAX_CONE_RADIUS_DEG),
    maglim: float = Query(default=13.0, ge=-2, le=25),
    limit: int = Query(default=2000, ge=1, le=sky_services.MAX_CONE_ROWS),
) -> dict:
    if os.getenv("SPHEREX_OFFLINE", "0") == "1":
        return {"epoch": "J2000.0", "count": 0, "objects": [], "scope": "Offline: bundled HYG stars are drawn by the frontend; SIMBAD enrichment is unavailable."}
    try:
        rows = await run_in_threadpool(sky_services.cone_stars, ra, dec, radius, maglim, limit)
    except sky_services.ServiceError as exc:
        raise _http_error(exc) from exc
    return {"epoch": "J2000.0", "count": len(rows), "objects": rows}


@app.post("/api/voyager")
async def voyager_ask(req: VoyagerRequest) -> dict:
    """The guide's next move. 503 when no model key is configured, so the client can say so plainly."""
    try:
        return await run_in_threadpool(voyager.ask, req.question, req.humor, req.detail, req.targets, req.context, req.history)
    except voyager.NoKey as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except voyager.VoyagerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    # Loopback by default: this server can trigger 60 MB downloads and is unauthenticated.
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
