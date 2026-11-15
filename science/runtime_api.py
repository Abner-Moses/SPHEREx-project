"""Operator-controlled public URLs; no credentials or user-supplied upstream URLs."""
import os
from urllib.parse import urlsplit
from fastapi import APIRouter

router = APIRouter(prefix="/api")

def configuration():
    loader = os.getenv("SPHEREX_FIREFLY_LOADER", "/firefly/firefly_loader.js")
    fits_base = os.getenv("SPHEREX_FIREFLY_FITS_BASE", "http://host.docker.internal:8000").rstrip("/")
    parsed = urlsplit(fits_base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise RuntimeError("SPHEREX_FIREFLY_FITS_BASE must be a credential-free HTTP(S) origin reachable from Firefly.")
    public = urlsplit(loader)
    if public.username or public.password or public.query or public.fragment or public.path != "/firefly/firefly_loader.js" or (public.scheme and public.scheme not in ("http", "https")) or loader.startswith("//"):
        raise RuntimeError("SPHEREX_FIREFLY_LOADER must identify the Firefly loader over HTTP(S) or the same-origin /firefly path.")
    return {"api_version":"2.2", "coordinate_frame":"ICRS",
            "firefly":{"loader_url":loader,"fits_base_url":fits_base},
            "data_policy":"Authentic IRSA observations only; previews and aligned frames are explicit derivatives."}

@router.get("/config")
def public_configuration():
    return configuration()
