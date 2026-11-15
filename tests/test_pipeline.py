from __future__ import annotations

import http.server
import os
import random
import threading
from pathlib import Path

import numpy as np
import pytest

import spherex_pipeline as pipeline
from spherex_pipeline import Cancelled, NotFound, PipelineError

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------ pixel mask and flux scale


def test_mask_follows_the_flags_table(l2_file):
    path, truth = l2_file
    product = pipeline.build_archive_product(path, "https://example.test/a.fits")
    size = product.width
    # SOURCE (bit 21) is informational: the left half carries it and must NOT be masked.
    assert not product.mask[100, 5]
    # Defect/artifact bits are masked.
    assert product.mask[10, 10] and product.mask[20, 30] and product.mask[31, 41]
    # FULLSAMPLE (bit 12) is informational too.
    assert not product.mask[50, 50]
    assert product.mask.sum() == 1 + 1 + 9
    assert product.meta()["bad_fraction"] == pytest.approx(11 / size**2, abs=1e-5)


def test_bad_mask_excludes_only_informational_bits():
    assert pipeline.BAD_MASK & (1 << 21) == 0
    assert pipeline.BAD_MASK & (1 << 12) == 0
    for bit in (0, 1, 2, 6, 7, 9, 10, 11, 14, 15, 17, 19, 20, 22, 23, 24, 25, 26, 27, 28):
        assert pipeline.BAD_MASK & (1 << bit)


def test_zodi_is_subtracted_and_png_inverts_to_flux(l2_file):
    path, truth = l2_file
    product = pipeline.build_archive_product(path, "https://example.test/a.fits")
    meta = product.meta()
    assert meta["stretch"]["zodi_subtracted"] is True
    lo, hi = meta["stretch"]["lo"], meta["stretch"]["hi"]
    # background (zodi removed) is ~2.0 MJy/sr, not 2.5
    assert 1.5 < lo < hi < 2.5
    good = ~product.mask
    decoded = lo + (hi - lo) * product.image.astype(float) / 255
    inside = good & (truth["sky"] > lo) & (truth["sky"] < hi)
    assert np.abs(decoded[inside] - truth["sky"][inside]).max() <= (hi - lo) / 255 / 2 + 1e-4


def test_metadata_comes_from_the_headers(l2_file):
    path, _ = l2_file
    meta = pipeline.build_archive_product(path, "https://example.test/a.fits").meta()
    assert (meta["detector"], meta["obs_id"], meta["date_obs"]) == (6, "TEST_0001_4", "2026-08-17T00:54:34.145")
    assert meta["mjd_avg"] == pytest.approx(61269.0386)
    assert meta["pixel_scale_arcsec"] == pytest.approx(6.15, abs=0.01)
    assert meta["bunit"] == "MJy / sr"
    assert meta["wavelength"] is None  # this synthetic file has no WCS-WAVE table


# ------------------------------------------------------------------ WCS mesh


def test_mesh_reproduces_the_wcs_including_sip(l2_file):
    path, _ = l2_file
    product = pipeline.build_archive_product(path, "https://example.test/a.fits")
    mesh = product.mesh
    n = mesh["n"]
    ra = np.array(mesh["ra"]).reshape(n, n)
    dec = np.array(mesh["dec"]).reshape(n, n)
    xs, ys = np.array(mesh["x"]), np.array(mesh["y"])
    rng = random.Random(3)
    worst = 0.0
    for _ in range(300):
        x, y = rng.uniform(0, product.width - 1), rng.uniform(0, product.height - 1)
        i, j = min(np.searchsorted(xs, x) - 1, n - 2), min(np.searchsorted(ys, y) - 1, n - 2)
        i, j = max(i, 0), max(j, 0)
        u, v = (x - xs[i]) / (xs[i + 1] - xs[i]), (y - ys[j]) / (ys[j + 1] - ys[j])
        def lerp(a, i=i, j=j, u=u, v=v):
            return (a[j, i] * (1 - u) + a[j, i + 1] * u) * (1 - v) + (a[j + 1, i] * (1 - u) + a[j + 1, i + 1] * u) * v

        truth_ra, truth_dec = product.wcs.all_pix2world(x, y, 0)
        err = np.hypot((lerp(ra) - float(truth_ra)) * np.cos(np.radians(float(truth_dec))), lerp(dec) - float(truth_dec)) * 3600
        worst = max(worst, err)
    assert worst < 0.1, f"bilinear mesh is off by {worst:.3f} arcsec"  # << one 6.15" pixel


def test_photometry_recovers_an_injected_star_in_physical_units(tmp_path):
    from conftest import make_l2_fits
    from astropy.io import fits

    path = tmp_path / "star.fits"
    make_l2_fits(path)
    with fits.open(path, mode="update") as hdul:
        yy, xx = np.mgrid[:200, :200]
        star = 50.0 * np.exp(-((xx - 120.0) ** 2 + (yy - 80.0) ** 2) / (2 * 1.5**2))  # MJy/sr, centred on pixel (120, 80)
        hdul["IMAGE"].data = (hdul["IMAGE"].data + star).astype(np.float32)
        hdul.flush()
    product = pipeline.build_archive_product(path, "https://example.test/star.fits")
    result = product.photometry(120.5, 80.5, r_ap=6.0, r_in=10.0, r_out=16.0)
    omega = (product.info["pixel_scale_arcsec"] / 206264.806) ** 2
    expected_jy = 50.0 * 2 * np.pi * 1.5**2 * 1e6 * omega  # analytic integral of the gaussian
    assert result["flux_jy"] == pytest.approx(expected_jy, rel=0.03)
    assert result["background_mjy_sr"] == pytest.approx(2.0, abs=0.1)  # sky 2.0 after the zodi subtraction
    assert result["snr"] > 50 and result["peak_mjy_sr"] > 45
    assert result["lower_limit"] is False and result["masked_fraction"] == 0
    # a star whose core is masked (saturated) can only give a lower limit
    product.flux[78:83, 118:123] = np.nan
    clipped = product.photometry(120.5, 80.5, r_ap=6.0, r_in=10.0, r_out=16.0)
    assert clipped["lower_limit"] is True and clipped["masked_fraction"] > 0.1
    assert clipped["flux_jy"] < result["flux_jy"] * 0.5
    with pytest.raises(PipelineError):
        product.photometry(120.5, 80.5, r_ap=9, r_in=5, r_out=12)
    with pytest.raises(NotFound):
        product.photometry(500, 500)
    with pytest.raises(PipelineError):
        pipeline.build_mock_product(1.0, 2.0).photometry(100, 100)


def test_pixel_of_round_trips_and_rejects_outside(l2_file):
    path, _ = l2_file
    product = pipeline.build_archive_product(path, "https://example.test/a.fits")
    ra, dec = product.wcs.all_pix2world(120.0, 80.0, 0)
    x, y = product.pixel_of(float(ra), float(dec))
    assert (x, y) == pytest.approx((120.5, 80.5), abs=1e-3)  # image coords = pixel centre + 0.5
    assert product.pixel_of(10.0, 60.0) is None


def test_footprint_is_a_closed_quad_that_contains_the_centre(l2_file):
    path, _ = l2_file
    footprint = pipeline.build_archive_product(path, "https://example.test/a.fits").footprint
    assert len(footprint) == 4
    assert pipeline.region_contains([tuple(p) for p in footprint], *pipeline.build_archive_product(path, "u").wcs.all_pix2world(99.5, 99.5, 0))


# ------------------------------------------------------------------ spherical polygon test


def square(ra, dec, half):
    return [(ra - half, dec - half), (ra + half, dec - half), (ra + half, dec + half), (ra - half, dec + half)]


def test_region_contains_basic_and_ra_wrap():
    box = square(10.0, 20.0, 1.0)
    assert pipeline.region_contains(box, 10.0, 20.0)
    assert not pipeline.region_contains(box, 12.5, 20.0)
    wrap = [(359.0, -1.0), (1.0, -1.0), (1.0, 1.0), (359.0, 1.0)]  # straddles RA = 0
    assert pipeline.region_contains(wrap, 0.0, 0.0)
    assert pipeline.region_contains(wrap, 359.5, 0.5)
    assert not pipeline.region_contains(wrap, 3.0, 0.0)


def test_region_contains_near_and_around_the_pole():
    near_pole = [(0.0, 87.0), (90.0, 87.0), (180.0, 87.0), (270.0, 87.0)]  # a cap around the pole
    assert pipeline.region_contains(near_pole, 45.0, 89.5)
    assert pipeline.region_contains(near_pole, 0.0, 90.0)
    assert not pipeline.region_contains(near_pole, 45.0, 80.0)


def test_region_rejects_far_polygons_and_degenerate_input():
    assert not pipeline.region_contains(square(200.0, 0.0, 1.0), 10.0, 0.0)
    assert not pipeline.region_contains([(0.0, 0.0), (1.0, 1.0)], 0.0, 0.0)
    assert pipeline.parse_region("not a polygon") == []
    assert len(pipeline.parse_region("POLYGON ICRS 1 2 3 4 5 6 7 8")) == 4


# ------------------------------------------------------------------ SIA parsing and selection


@pytest.fixture()
def sia_rows():
    # Barnard's Star as in the tests/fixtures capture of a real IRSA reply
    return pipeline._parse_sia_table((FIXTURES / "sia_sample.xml").read_bytes(), 269.45208, 4.69339)


def test_sia_rows_carry_real_archive_fields(sia_rows):
    assert len(sia_rows) == 4
    row = sia_rows[0]
    assert row["obs_id"] and row["access_url"].startswith("https://")
    assert row["detector"] in range(1, 7)
    lo, hi = row["wavelength_um"]
    assert 0.7 < lo < hi < 5.1
    assert row["pixel_scale_arcsec"] == pytest.approx(6.15)
    assert len(row["footprint"]) >= 4 and row["date_obs"].startswith("2026-")


def test_choose_observation_prefers_covering_then_nearest(sia_rows):
    covering = [r for r in sia_rows if r["covers"]]
    assert covering, "the sample was captured at this position, some products must cover it"
    chosen = pipeline.choose_observation(sia_rows)
    assert chosen["covers"]
    assert chosen["separation_deg"] == min(r["separation_deg"] for r in covering)
    assert pipeline.choose_observation(sia_rows, covering[0]["obs_id"])["obs_id"] == covering[0]["obs_id"]
    # obs_id alone is shared by every detector of an exposure: the detector must disambiguate
    twin = dict(covering[0], detector=(covering[0]["detector"] % 6) + 1, access_url="https://example.test/twin.fits")
    both = [covering[0], twin]
    assert pipeline.choose_observation(both, covering[0]["obs_id"], twin["detector"])["access_url"] == twin["access_url"]
    assert pipeline.choose_observation(both, covering[0]["obs_id"], covering[0]["detector"])["access_url"] == covering[0]["access_url"]
    with pytest.raises(NotFound):
        pipeline.choose_observation(sia_rows, "NOPE_0000_0")
    with pytest.raises(NotFound):
        pipeline.choose_observation([])
    with pytest.raises(NotFound):
        pipeline.choose_observation([dict(r, covers=False) for r in sia_rows])


# ------------------------------------------------------------------ downloads


class RangeHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    drop_first_after = None  # bytes to send before dropping the first full-body response
    calls = 0

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        cls = type(self)
        cls.calls += 1
        start = 0
        header = self.headers.get("Range")
        if header:
            start = int(header.split("=")[1].split("-")[0])
        body = cls.payload[start:]
        self.send_response(206 if header else 200)
        if header:
            self.send_header("Content-Range", f"bytes {start}-{len(cls.payload) - 1}/{len(cls.payload)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if cls.drop_first_after is not None and cls.calls == 1:
            self.wfile.write(body[: cls.drop_first_after])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)


@pytest.fixture()
def server():
    RangeHandler.payload = os.urandom(300_000)
    RangeHandler.drop_first_after = None
    RangeHandler.calls = 0
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/product.fits"
    httpd.shutdown()


def test_download_resumes_after_a_dropped_connection(cache, server):
    RangeHandler.drop_first_after = 120_000
    path = pipeline.download_product(server, timeout=30)
    assert path.read_bytes() == RangeHandler.payload
    assert RangeHandler.calls >= 2  # second call resumed with a Range header
    assert not list(cache.glob("**/*.part"))


def test_cached_product_needs_no_network(cache, monkeypatch):
    url = "https://example.test/level2_x.fits"
    (cache / "fits").mkdir(parents=True, exist_ok=True)
    (cache / "fits" / pipeline.cache_name(url)).write_bytes(b"x")
    monkeypatch.setattr(pipeline, "_http", lambda: (_ for _ in ()).throw(AssertionError("network used")))
    assert pipeline.download_product(url).read_bytes() == b"x"


def test_cancelled_download_stops_and_keeps_the_partial_file(cache, server):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        pipeline.download_product(server, cancel=cancel)


def test_concurrent_requests_for_one_url_download_once(cache, server):
    results = []
    threads = [threading.Thread(target=lambda: results.append(pipeline.download_product(server, timeout=30))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 4 and len({str(r) for r in results}) == 1
    assert RangeHandler.calls == 1


# ------------------------------------------------------------------ orchestration


def test_mock_is_deterministic_and_centred_on_the_target():
    a, b = pipeline.build_mock_product(10.0, 20.0), pipeline.build_mock_product(10.0, 20.0)
    assert np.array_equal(a.image, b.image) and a.id == b.id
    x, y = a.wcs.all_world2pix(10.0, 20.0, 0)
    assert (float(x), float(y)) == pytest.approx(((a.width - 1) / 2, (a.height - 1) / 2), abs=1e-6)  # exact array centre
    assert a.pixel_of(10.0, 20.0) == pytest.approx([a.width / 2, a.height / 2], abs=1e-3)


def test_auto_falls_back_visibly_and_live_does_not(monkeypatch):
    monkeypatch.setenv("SPHEREX_OFFLINE", "0")
    def boom(*args, **kwargs):
        raise PipelineError("archive down")

    monkeypatch.setattr(pipeline, "search_observations", boom)
    auto = pipeline.observe(10.0, 20.0, "auto")
    assert auto.source == "mock" and auto.fallback_reason == "archive down"
    with pytest.raises(PipelineError, match="archive down"):
        pipeline.observe(10.0, 20.0, "live")
    assert pipeline.observe(10.0, 20.0, "mock").fallback_reason is None


def test_live_observe_uses_cache_and_remembers_the_product(cache, l2_file, monkeypatch):
    path, _ = l2_file
    url = "https://irsa.example/level2_x.fits"
    (cache / "fits").mkdir(parents=True, exist_ok=True)
    (cache / "fits" / pipeline.cache_name(url)).write_bytes(path.read_bytes())
    obs = {"obs_id": "TEST_0001_4", "covers": True, "separation_deg": 0.0, "access_url": url, "detector": 6, "band": "SPHEREx-D6", "wavelength_um": [4.4, 5.0], "target_name": "t"}
    monkeypatch.setattr(pipeline, "search_observations", lambda *a, **k: [obs])
    product = pipeline.observe(269.45, 4.69, "live")
    assert product.source == "irsa" and pipeline.get_product(product.id) is product
    assert product.meta(target=(269.45, 4.69))["target"]["pixel"] is not None
    with pytest.raises(PipelineError):
        pipeline.observe(10.0, 20.0, "live")  # this detector does not contain that position


def test_coordinates_are_validated():
    for bad in [(-1, 0), (361, 0), (0, 91), (float("nan"), 0)]:
        with pytest.raises(PipelineError):
            pipeline.observe(*bad, "mock")
    with pytest.raises(PipelineError):
        pipeline.observe(1, 1, "bogus")


def test_concurrent_identical_searches_hit_the_archive_once(monkeypatch):
    pipeline._SEARCH_CACHE.clear()
    calls = []

    def slow_query(ra, dec, radius, maxrec):
        calls.append(1)
        import time as _t

        _t.sleep(0.2)
        return [{"obs_id": "A"}]

    monkeypatch.setattr(pipeline, "_query_archive", slow_query)
    out = []
    threads = [threading.Thread(target=lambda: out.append(pipeline.search_observations(10.0, 20.0, 0.05, 500))) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1 and len(out) == 5 and all(o == [{"obs_id": "A"}] for o in out)
    assert pipeline.search_observations(10.0, 20.0, 0.05, 500) == [{"obs_id": "A"}] and len(calls) == 1  # then served from cache
