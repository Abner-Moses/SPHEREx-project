from __future__ import annotations


import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
import sky_services
import spherex_pipeline as pipeline


@pytest.fixture()
def client(cache):
    return TestClient(main.app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_observe_mock_returns_everything_a_client_needs(client):
    r = client.post("/api/observe", json={"ra": 269.452, "dec": 4.693, "mode": "mock"})
    assert r.status_code == 200
    meta = r.json()
    assert meta["source"] == "mock" and meta["images_are"] == "simulated"
    assert (meta["width"], meta["height"]) == (256, 256)
    mesh = meta["mesh"]
    assert mesh["n"] == 65 and len(mesh["ra"]) == len(mesh["dec"]) == 65 * 65
    assert meta["target"]["pixel"] == pytest.approx([128, 128], abs=0.01)
    assert len(meta["footprint"]) == 4
    png = client.get(meta["image_url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    decoded = cv2.imdecode(np.frombuffer(png.content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded.shape == (256, 256) and decoded.dtype == np.uint8
    mask = client.get(meta["mask_url"])
    assert mask.status_code == 200
    # one image per request, nothing written to a shared file any more
    assert not __import__("pathlib").Path("outputs").exists()


def test_observe_live_from_a_cached_product(client, cache, l2_file, monkeypatch):
    path, _ = l2_file
    url = "https://irsa.example/level2_api.fits"
    (cache / "fits").mkdir(parents=True, exist_ok=True)
    (cache / "fits" / pipeline.cache_name(url)).write_bytes(path.read_bytes())
    obs = {"obs_id": "TEST_0001_4", "covers": True, "separation_deg": 0.0, "access_url": url, "detector": 6, "band": "SPHEREx-D6", "wavelength_um": [4.4, 5.0], "target_name": "t"}
    monkeypatch.setattr(pipeline, "search_observations", lambda *a, **k: [obs])
    r = client.post("/api/observe", json={"ra": 269.45, "dec": 4.69, "mode": "live"})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["source"] == "irsa" and meta["detector"] == 6 and meta["stretch"]["zodi_subtracted"] is True
    assert meta["target"]["pixel"] == pytest.approx([100.0, 100.0], abs=0.6)
    png = client.get(meta["image_url"])
    assert cv2.imdecode(np.frombuffer(png.content, np.uint8), cv2.IMREAD_UNCHANGED).shape == (200, 200)


def test_live_failure_is_an_error_not_a_fake(client, monkeypatch):
    monkeypatch.setattr(pipeline, "search_observations", lambda *a, **k: (_ for _ in ()).throw(pipeline.PipelineError("archive down")))
    r = client.post("/api/observe", json={"ra": 1, "dec": 1, "mode": "live"})
    assert r.status_code == 502 and "archive down" in r.json()["detail"]
    r = client.post("/api/observe", json={"ra": 1, "dec": 1, "mode": "auto"})
    assert r.status_code == 200 and r.json()["source"] == "mock" and r.json()["fallback_reason"] == "archive down"


def test_no_coverage_is_a_404(client, monkeypatch):
    monkeypatch.setattr(pipeline, "search_observations", lambda *a, **k: [])
    assert client.post("/api/observe", json={"ra": 1, "dec": 1, "mode": "live"}).status_code == 404


def test_validation_and_unknown_product(client):
    assert client.post("/api/observe", json={"ra": 400, "dec": 0}).status_code == 422
    assert client.post("/api/observe", json={"ra": 0, "dec": 0, "mode": "weird"}).status_code == 422
    assert client.get("/api/products/nope/image.png").status_code == 404


def test_cors_allows_the_dev_and_preview_origins_only(client):
    ask = lambda origin: client.options("/api/observe", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})  # noqa: E731
    assert ask("http://localhost:5173").headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert ask("http://localhost:4173").headers.get("access-control-allow-origin") == "http://localhost:4173"
    assert ask("https://evil.example").headers.get("access-control-allow-origin") is None


def test_observations_endpoint_passes_archive_rows_through(client, monkeypatch):
    seen = {}

    def fake(ra, dec, radius, maxrec):
        seen.update(ra=ra, dec=dec, radius=radius, maxrec=maxrec)
        return [{"obs_id": "A", "covers": True}]

    monkeypatch.setattr(pipeline, "search_observations", fake)
    body = client.get("/api/observations", params={"ra": 10, "dec": 20, "radius": 0.5, "limit": 7}).json()
    assert body["count"] == 1 and seen == {"ra": 10.0, "dec": 20.0, "radius": 0.5, "maxrec": 7}
    assert client.get("/api/observations", params={"ra": 10, "dec": 20, "radius": 9}).status_code == 422


def test_sky_endpoints_translate_errors(client, monkeypatch):
    monkeypatch.setattr(sky_services, "resolve_name", lambda q: {"name": q, "ra": 1.0, "dec": 2.0})
    assert client.get("/api/resolve", params={"q": "x"}).json()["name"] == "x"

    def unknown(q):
        raise sky_services.UnknownObject("no such thing")

    monkeypatch.setattr(sky_services, "resolve_name", unknown)
    r = client.get("/api/resolve", params={"q": "x"})
    assert r.status_code == 404 and "no such thing" in r.json()["detail"]
    monkeypatch.setattr(sky_services, "cone_stars", lambda *a, **k: [{"name": "s"}])
    assert client.get("/api/stars", params={"ra": 1, "dec": 2, "radius": 0.5}).json()["count"] == 1
    assert client.get("/api/stars", params={"ra": 1, "dec": 2, "radius": 50}).status_code == 422
