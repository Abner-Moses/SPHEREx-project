"""Generated temporary FITS only: these tests make no real-data validation claim."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
import threading

from astropy.io import fits
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from archive_client import DataError
from composition_api import router
import sequence_composition as composition
from test_sequences import archive, done, no_network, request, service  # noqa: F401


@pytest.fixture
def prepared(service, archive):
    products = [archive["add"](201, value=2, date="2026-01-01T12:00:00"),
                archive["add"](202, value=7, date="2026-01-03T12:00:00")]
    state = done(service, service.prepare(request(products))["id"])
    assert state["status"] == "ready", state
    return state


def ids(state):
    return [frame["id"] for frame in state["frames"]]


def product(service, state, op="addition", output="fits", frame_ids=None):
    return composition.combine(state["id"], ids(state) if frame_ids is None else frame_ids, op, output, service=service)


def paths(service, state):
    return [service.frame_path(state["id"], frame["id"], "fits") for frame in state["frames"]]


def read(path):
    with fits.open(path) as hdus:
        image = hdus["IMAGE"].data.astype(float)
        valid = (hdus["MASK"].data == 0) & (hdus["COVERAGE"].data > 0) & np.isfinite(image)
        return image, valid


@pytest.mark.parametrize("op", ["addition", "mean", "subtraction"])
def test_arithmetic_fits_provenance_wcs_and_immutable_originals(service, archive, prepared, op):
    source_paths = paths(service, prepared)
    before = [path.read_bytes() for path in source_paths]
    (a, va), (b, vb) = map(read, source_paths)
    valid = va & vb
    expected = {"addition": a + b, "mean": (a + b) / 2, "subtraction": a - b}[op]
    result = product(service, prepared, op)
    assert result.media_type == "application/fits"
    with fits.open(io.BytesIO(result.content)) as hdus:
        assert hdus[0].header["DERIVED"]
        assert hdus[0].header["COMPOP"] == op
        assert hdus[0].header["COMPID"] == result.id
        assert not hdus[0].header["CHGCLAIM"] and not hdus[0].header["PSFMATCH"]
        assert not hdus[0].header["BKGMATCH"]
        assert hdus[0].header["VARPROP"] == "UNAVAILABLE"
        assert "DATE-OBS" not in hdus["IMAGE"].header
        assert hdus[0].header["MJD-BEG"] == prepared["frames"][0]["mjd"]
        assert hdus[0].header["MJD-END"] == prepared["frames"][1]["mjd"]
        assert hdus["IMAGE"].header["BUNIT"] == "MJy/sr"
        np.testing.assert_allclose(hdus["IMAGE"].data[valid], expected[valid])
        assert np.isnan(hdus["IMAGE"].data[~valid]).all()
        np.testing.assert_array_equal(hdus["MASK"].data, ~valid)
        np.testing.assert_array_equal(hdus["COVERAGE"].data, valid)
        assert "VARIANCE" not in hdus and "ORIGVAR" not in hdus
        for key, value in prepared["common_wcs"]["header"].items():
            assert hdus["IMAGE"].header[key] == value
        provenance = json.loads(hdus["PROVENANCE"].data["JSON"][0])
        assert provenance == result.metadata
        assert provenance["frame_ids"] == ids(prepared)
        assert provenance["valid_pixels"] == int(valid.sum())
        assert not provenance["variance_propagated"]
        assert provenance["exploratory"] and not provenance["change_claim"]
        assert "no change/variability claim" in provenance["warning"]
        assert len(provenance["inputs"]) == 2
        assert all(len(frame["aligned_sha256"]) == 64 for frame in provenance["inputs"])
        assert provenance["inputs"][0]["timestamp"].startswith("2026-01-01")
    assert [path.read_bytes() for path in source_paths] == before
    for asset_id, path in archive["paths"].items():
        assert path.read_bytes() == archive["original"][asset_id]
    assert service.jobs[prepared["id"]]["serving"] == 0


def test_three_frames_mean_uses_all_and_subtraction_order_is_explicit(service, archive):
    products = [archive["add"](index, value=value) for index, value in ((211, 1), (212, 4), (213, 10))]
    state = done(service, service.prepare(request(products))["id"])
    arrays = [read(path) for path in paths(service, state)]
    valid = np.logical_and.reduce([item[1] for item in arrays])
    result = product(service, state, "mean")
    with fits.open(io.BytesIO(result.content)) as hdus:
        np.testing.assert_allclose(hdus["IMAGE"].data[valid], sum(item[0][valid] for item in arrays) / 3)
    first = product(service, state, "subtraction", frame_ids=ids(state)[:2])
    reverse = product(service, state, "subtraction", frame_ids=ids(state)[1::-1])
    with fits.open(io.BytesIO(first.content)) as a, fits.open(io.BytesIO(reverse.content)) as b:
        np.testing.assert_allclose(a["IMAGE"].data, -b["IMAGE"].data, equal_nan=True)
    assert first.id != reverse.id


def test_masks_nonfinite_and_missing_coverage_intersect_without_fill(service, prepared):
    for index, path in enumerate(paths(service, prepared)):
        with fits.open(path, mode="update") as hdus:
            # These are known interior pixels of the generated common grid.
            hdus["MASK"].data[4, 4 + index] = 1
            hdus["IMAGE"].data[5, 4 + index] = np.nan
            hdus["COVERAGE"].data[6, 4 + index] = 0
            hdus["COVERAGE"].data[7, 4 + index] = np.nan
    result = product(service, prepared, "mean")
    with fits.open(io.BytesIO(result.content)) as hdus:
        assert np.isnan(hdus["IMAGE"].data[4:8, 4:6]).all()
        assert (hdus["MASK"].data[4:8, 4:6] == 1).all()
        assert (hdus["COVERAGE"].data[4:8, 4:6] == 0).all()


def test_no_intersection_is_rejected_and_releases_leases(service, prepared):
    with fits.open(paths(service, prepared)[1], mode="update") as hdus:
        hdus["MASK"].data[:] = 1
    with pytest.raises(DataError, match="no common valid"):
        product(service, prepared)
    assert service.jobs[prepared["id"]]["serving"] == 0


def test_preview_is_display_only_transparent_and_deterministically_reproducible(service, prepared):
    a, b = product(service, prepared), product(service, prepared)
    metadata = product(service, prepared, output="metadata")
    preview = product(service, prepared, output="preview")
    assert a.id == b.id == metadata.id == preview.id
    assert a.content == b.content
    assert metadata.content is None
    assert preview.content == product(service, prepared, output="preview").content
    with Image.open(io.BytesIO(preview.content)) as png, fits.open(io.BytesIO(a.content)) as hdus:
        assert png.mode == "RGBA"
        assert png.info["Product"].startswith("DERIVED DISPLAY ONLY")
        assert png.info["CompositionID"] == a.id
        alpha = np.array(png)[..., 3]
        np.testing.assert_array_equal(alpha, (hdus["MASK"].data[::-1] == 0) * 255)
    assert a.metadata["preview_display"]["display_only"]
    assert not list(service.root.rglob("composition-*"))


def test_unit_conversion_and_ids_track_actual_fits_bytes(service, prepared):
    a = product(service, prepared)
    with fits.open(paths(service, prepared)[0], mode="update") as hdus:
        hdus["IMAGE"].data *= 1e6
        hdus["IMAGE"].header["BUNIT"] = "Jy/sr"
    b = product(service, prepared)
    with fits.open(io.BytesIO(a.content)) as x, fits.open(io.BytesIO(b.content)) as y:
        np.testing.assert_allclose(x["IMAGE"].data, y["IMAGE"].data, equal_nan=True, rtol=1e-6)
    assert a.id != b.id
    assert b.metadata["inputs"][0]["scale_to_mjy_sr"] == 1e-6


@pytest.mark.parametrize("plane,key,value,match", [
    ("IMAGE", "BUNIT", "Jy/pixel", "units"),
    ("IMAGE", "CRVAL1", 11., "WCS"),
    ("MASK", "CRPIX1", 100., "WCS"),
    ("COVERAGE", "CDELT1", -.05, "WCS"),
    ("IMAGE", "RADESYS", "FK5", "WCS"),
    ("IMAGE", "DATE-OBS", "2026-01-02", "timestamp|time keywords disagree"),
    ("PRIMARY", "ORIGSHA", "0" * 64, "identity"),
    ("PRIMARY", "PRODTYPE", "NOT_ALIGNED", "aligned sequence"),
    ("PRIMARY", "VARPROP", "PROPAGATED", "aligned sequence"),
])
def test_fits_prerequisite_rejections(service, prepared, plane, key, value, match):
    with fits.open(paths(service, prepared)[0], mode="update") as hdus:
        hdus[plane].header[key] = value
    with pytest.raises(DataError, match=match) as error:
        product(service, prepared)
    assert error.value.status == 409
    assert service.jobs[prepared["id"]]["serving"] == 0


@pytest.mark.parametrize("plane", ["IMAGE", "MASK", "COVERAGE"])
def test_plane_shape_rejected_before_array_arithmetic(service, prepared, plane):
    with fits.open(paths(service, prepared)[0], mode="update") as hdus:
        hdus[plane].data = hdus[plane].data[:-1]
    with pytest.raises(DataError, match="shape"):
        product(service, prepared)


@pytest.mark.parametrize("mutation,match", [
    ("status", "ready"), ("mode", "comparable"), ("quality", "quality"),
    ("policy", "quality"), ("spectral", "wavelength/bandwidth"),
    ("bandwidth", "wavelength/bandwidth"), ("timestamp", "timestamps"),
    ("time_source", "authoritative"), ("frame_status", "belong|ready"),
    ("cache_key", "cache identity"), ("product_id", "identity"),
])
def test_manifest_rechecked_not_just_supplied_ids(service, prepared, mutation, match):
    job = service.jobs[prepared["id"]]
    frame = job["frame_states"][1]
    if mutation == "status": job["status"] = "failed"
    elif mutation == "mode": job["request"]["mode"] = "all"
    elif mutation == "quality": frame["quality"]["accepted"] = False
    elif mutation == "policy": frame["quality"]["policy_version"] = "obsolete"
    elif mutation == "spectral": frame["spectral"]["wavelength_um"] = 4.
    elif mutation == "bandwidth": frame["spectral"]["bandwidth_um"] = .2
    elif mutation == "timestamp": frame["timestamp"] = "2026-01-05T00:00:00Z"
    elif mutation == "time_source": frame["time_source"] = "IMAGE.DATE"
    elif mutation == "frame_status": frame["status"] = "failed"
    elif mutation == "cache_key": frame["aligned_cache_key"] = "0" * 64
    elif mutation == "product_id": frame["product_id"] = "b" * 32
    with pytest.raises(DataError, match=match):
        product(service, prepared)
    assert job["serving"] == 0


def test_all_mode_and_insufficient_sequences_rejected(service, archive):
    products = [archive["add"](221), archive["add"](222)]
    all_mode = done(service, service.prepare(request(products, mode="all"))["id"])
    with pytest.raises(DataError, match="comparable"):
        product(service, all_mode)
    one = done(service, service.prepare(request(products[:1]))["id"])
    with pytest.raises(DataError, match="ready"):
        composition.combine(one["id"], [ids(one)[0], "a" * 32], "mean", "fits", service=service)


def test_frame_from_another_sequence_rejected(service, archive, prepared):
    other = done(service, service.prepare(request([archive["add"](223), archive["add"](224)]))["id"])
    with pytest.raises(DataError, match="belong"):
        product(service, prepared, frame_ids=[ids(prepared)[0], ids(other)[0]])


@pytest.mark.parametrize("op,selection", [
    ("addition", []), ("mean", ["a" * 32]), ("subtraction", [f"{i:032x}" for i in range(3)]),
    ("addition", ["a" * 32] * 2), ("mean", ["../escape", "b" * 32]),
    ("addition", [f"{i:032x}" for i in range(composition.MAX_FRAMES + 1)]),
    ("snr", ["a" * 32, "b" * 32]),
])
def test_request_bounds_are_explicit(service, prepared, op, selection):
    with pytest.raises(DataError) as error:
        product(service, prepared, op, frame_ids=selection)
    assert error.value.status == 422


def test_weighted_mean_rejected_before_reading_even_if_variance_injected(service, prepared, monkeypatch):
    with fits.open(paths(service, prepared)[0], mode="update") as hdus:
        hdus.append(fits.ImageHDU(np.ones(prepared["common_wcs"]["shape"]), name="VARIANCE"))
    with monkeypatch.context() as patch:
        patch.setattr(service, "status", lambda *_: pytest.fail("Weighted mean should be rejected before input reads"))
        with pytest.raises(DataError, match="Weighted mean is unsupported") as error:
            product(service, prepared, "weighted_mean")
    assert error.value.status == 422


@pytest.mark.parametrize("limit", ["MAX_PIXELS", "MAX_INPUT_BYTES", "MAX_FRAME_BYTES", "MAX_OUTPUT_BYTES", "MAX_MEMORY_BYTES"])
def test_resource_bounds_and_lease_cleanup(service, prepared, monkeypatch, limit):
    monkeypatch.setattr(composition, limit, 1)
    with pytest.raises(DataError) as error:
        product(service, prepared)
    assert error.value.status == 413
    assert service.jobs[prepared["id"]]["serving"] == 0


def test_concurrency_admission_fails_fast_and_releases_slot(service, prepared, monkeypatch):
    monkeypatch.setattr(composition, "_SLOTS", threading.BoundedSemaphore(1))
    entered, release = threading.Event(), threading.Event()
    original = composition._read_frame
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(composition, "_read_frame", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(product, service, prepared)
        assert entered.wait(3)
        try:
            with pytest.raises(DataError, match="concurrency") as error:
                product(service, prepared)
            assert error.value.status == 503
        finally:
            release.set()
        assert pending.result(timeout=5).content
    assert product(service, prepared).content


def test_all_frame_leases_span_encoding_and_deletion(service, prepared, monkeypatch):
    original = composition._encode
    source_paths = paths(service, prepared)
    def deleting(*args):
        assert service.jobs[prepared["id"]]["serving"] == len(ids(prepared))
        assert service.delete(prepared["id"])["status"] == "deleting"
        assert all(path.exists() for path in source_paths)
        return original(*args)
    monkeypatch.setattr(composition, "_encode", deleting)
    assert product(service, prepared).content
    assert not any(path.exists() for path in source_paths)
    with pytest.raises(DataError) as error:
        service.status(prepared["id"])
    assert error.value.status == 404


def test_partial_lease_acquisition_failure_releases_earlier_leases(service, prepared, monkeypatch):
    original = service.frame_lease
    @contextmanager
    def fail_second(identifier, frame_id, kind):
        if frame_id == ids(prepared)[1]:
            raise DataError("Frame disappeared", 404)
        with original(identifier, frame_id, kind) as path:
            yield path
    monkeypatch.setattr(service, "frame_lease", fail_second)
    with pytest.raises(DataError, match="disappeared"):
        product(service, prepared)
    assert service.jobs[prepared["id"]]["serving"] == 0


@pytest.mark.parametrize("kind", ["json", "policy", "time", "missing", "duplicate", "mask", "coverage"])
def test_malformed_prepared_planes_and_provenance_are_rejected(service, prepared, kind):
    with fits.open(paths(service, prepared)[0], mode="update") as hdus:
        if kind in ("json", "policy", "time"):
            data = json.loads(hdus["PROVENANCE"].data["JSON"][0])
            if kind == "json": data = []
            elif kind == "policy": data["policy"] = []
            else: data["observation_time"]["timestamp"] = "2099-01-01T00:00:00Z"
            hdus["PROVENANCE"].data["JSON"][0] = json.dumps(data)
        elif kind == "missing": del hdus["MASK"]
        elif kind == "duplicate": hdus.append(hdus["MASK"].copy())
        elif kind == "mask": hdus["MASK"].data[4, 4] = 2
        elif kind == "coverage": hdus["COVERAGE"].data[4, 4] = 3
    with pytest.raises(DataError) as error:
        product(service, prepared)
    assert error.value.status == 409
    assert service.jobs[prepared["id"]]["serving"] == 0


def test_output_encoder_limits_actual_multidimensional_bytes(service, prepared, monkeypatch):
    # Exercise the encoder's actual byte guard, independently of preflight estimates.
    original = composition._encode
    def limited(*args):
        monkeypatch.setattr(composition, "MAX_OUTPUT_BYTES", 5000)
        return original(*args)
    monkeypatch.setattr(composition, "_encode", limited)
    with pytest.raises(DataError) as error:
        product(service, prepared)
    assert error.value.status == 413
    assert service.jobs[prepared["id"]]["serving"] == 0


def test_arithmetic_overflow_rejects_product(service, prepared):
    for path in paths(service, prepared):
        with fits.open(path, mode="update") as hdus:
            hdus["IMAGE"].data = np.full(hdus["IMAGE"].shape, 1.7e308, dtype=np.float64)
    with pytest.raises(DataError, match="overflow"):
        product(service, prepared)


def test_http_router_explicit_outputs_registry_and_errors(service, prepared, monkeypatch):
    # The parent owns main.py mounting. Exercise the actual router in a tiny app.
    monkeypatch.setattr(composition.sequences, "_DEFAULT", service)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        registry = client.get("/api/sequences/composition/operations")
        assert registry.status_code == 200
        operations = {item["id"]: item for item in registry.json()["operations"]}
        assert operations["addition"]["available"]
        assert not operations["weighted_mean"]["available"]
        url = f'/api/sequences/{prepared["id"]}/combine'
        body = {"frame_ids": ids(prepared), "op": "subtraction"}
        assert client.post(url, json=body).status_code == 422
        assert client.post(url + "?output=unknown", json=body).status_code == 422
        metadata = client.post(url + "?output=metadata", json=body)
        assert metadata.status_code == 200
        png = client.post(url + "?output=preview", json=body)
        scientific = client.post(url + "?output=fits", json=body)
        assert png.status_code == scientific.status_code == 200
        assert png.headers["content-type"] == "image/png"
        assert png.headers["X-Data-Product"] == "derived-display-only-preview"
        assert scientific.headers["content-type"] == "application/fits"
        assert scientific.headers["X-Composition-ID"] == metadata.json()["id"] == png.headers["X-Composition-ID"]
        assert client.post(url + "?output=fits", json={**body, "op": "weighted_mean"}).status_code == 422
        assert client.post(url + "?output=fits", json={**body, "extra": 1}).status_code == 422
        assert client.post(url + "?output=fits", json={**body, "frame_ids": ["b" * 32, "c" * 32]}).status_code == 409
        assert client.post("/api/sequences/" + "f" * 32 + "/combine?output=fits", json=body).status_code == 404
        assert client.post("/api/sequences/not-valid/combine?output=fits", json=body).status_code == 422
