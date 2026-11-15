"""Synthetic FITS fixtures exist ONLY in tests. No real archive/network access."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest
import requests
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import Sip, WCS
from PIL import Image
from urllib3.response import HTTPResponse

from archive_client import DataError
import sequence_service as sequences
import sequence_spectral as spectral

RA, DEC = 10.0, 20.0
CAL_URL = "https://irsa.ipac.caltech.edu/data/SPHEREx/calibration/spectral_wcs_D1_spx_cal-wcs-v4-2025-254.fits"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("Network is forbidden in sequence unit tests")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fail)
    monkeypatch.setattr(spectral.archive_enrichment, "enrich", fail)
    monkeypatch.setattr(spectral.archive_enrichment, "datalink", lambda record: {"rows": [], "status": "not_available"}, raising=False)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    records, paths, original, retrievals = {}, {}, {}, []
    leases = {"active": 0, "max": 0}
    lock = threading.Lock()
    def add(index, *, date="2026-01-03T12:00:00", wave=1.0, band=.025, accepted=True,
            embedded=True, rotation=0, flags=None, value=1., center=(RA, DEC), times=True,
            parent_offset=(0, 0), detector=1, mjd_only=False, sip=False):
        product_id, asset_id = f"{index:032x}", f"{10000 + index:032x}"
        wcs = sequences.output_wcs(*center, 9)
        angle = np.radians(rotation)
        wcs.wcs.pc = [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
        if sip:
            a, b = np.zeros((3, 3)), np.zeros((3, 3))
            a[2, 0], b[0, 2] = 5e-4, -4e-4
            wcs.sip = Sip(a, b, None, None, wcs.wcs.crpix)
            wcs.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        header = wcs.to_header(relax=True)
        header["BUNIT"] = "MJy/sr"
        header["FINAST"] = 0
        header["DETECTOR"] = detector
        header["CRPIX1A"], header["CRPIX2A"] = 1-parent_offset[0], 1-parent_offset[1]
        for axis in (1, 2):
            header[f"CRVAL{axis}A"] = 0.
            header[f"CDELT{axis}A"] = 1.
            header[f"CTYPE{axis}A"] = "LINEAR"
        header["DATE"] = "2099-12-31T00:00:00"
        if times:
            header["MJD-OBS" if mjd_only else "DATE-OBS"] = float(Time(date).mjd) if mjd_only else date
        data = np.full((9, 9), value, dtype=np.float32)
        # A stable directional feature checks celestial alignment, not array rotation.
        data[6, 4] = value + 5
        bitmap = np.zeros((9, 9), dtype=np.uint64) if flags is None else np.array(flags, dtype=np.uint64)
        variance = np.arange(81, dtype=np.float32).reshape(9, 9) + 1
        hdus = [fits.PrimaryHDU(), fits.ImageHDU(data, header=header, name="IMAGE"),
                fits.ImageHDU(bitmap, name="FLAGS"), fits.ImageHDU(variance, name="VARIANCE")]
        if embedded:
            cal_header = fits.Header({"BUNIT": "um"})
            hdus.extend([fits.ImageHDU(np.full((9, 9), wave), header=cal_header, name="CWAVE"),
                         fits.ImageHDU(np.full((9, 9), band), header=cal_header, name="CBAND")])
        path = tmp_path / f"level2_{index}_processed2099.fits"
        fits.HDUList(hdus).writeto(path, checksum=True)
        raw = path.read_bytes()
        record = {"id": asset_id, "product_id": product_id, "observation_id": "shared-observation",
                  "source_url": "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/" + path.name,
                  "coordinates": {"ra": RA, "dec": DEC}, "sha256": hashlib.sha256(raw).hexdigest(),
                  "collection": "spherex_dr1", "detector": detector, "timestamp": "2098-01-01",
                  "sia": {"obs_publisher_did": f"ivo://irsa.ipac/spherex/product/{index}", "t_min": 999999},
                  "validation": {"policy_version": sequences.fits_processor.POLICY_VERSION,
                      "scientific_acceptance": {"accepted": accepted, "status": "accepted" if accepted else "inspection_only",
                                                "reasons": [] if accepted else ["Target quality rejected"]},
                      "astrometric_quality": {"status": "good", "finast": 0}}}
        records[product_id], paths[asset_id], original[asset_id] = record, path, raw
        return product_id
    def retrieve(product_id, ra, dec, size):
        assert (ra, dec) == (RA, DEC)
        retrievals.append((product_id, ra, dec, size))
        if product_id not in records:
            raise DataError("Archive cache storage is occupied by leased observations", 507)
        return deepcopy(records[product_id])
    @contextmanager
    def lease(asset_id):
        with lock:
            leases["active"] += 1
            leases["max"] = max(leases["max"], leases["active"])
        try:
            yield paths[asset_id]
        finally:
            with lock:
                leases["active"] -= 1
    monkeypatch.setattr(sequences.observations, "retrieve", retrieve)
    monkeypatch.setattr(sequences.observations, "asset_lease", lease)
    return {"add": add, "records": records, "paths": paths, "original": original,
            "retrievals": retrievals, "leases": leases, "retrieve": retrieve}


@pytest.fixture
def service(tmp_path):
    instance = sequences.SequenceService(tmp_path / "sequence-cache")
    yield instance
    instance.shutdown()


def request(ids, **kwargs):
    return {"ra": RA, "dec": DEC, "observation_ids": list(ids), "cutout_size_deg": .0205, **kwargs}


def done(service, identifier):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = service.status(identifier)
        if status["status"] != "preparing":
            # Wait for final cleanup to finish before assertions/deletion.
            service.jobs[identifier]["future"].result(timeout=3)
            return service.status(identifier)
        time.sleep(.01)
    pytest.fail("Sequence job did not finish")


def test_common_wcs_chronology_provenance_original_variance_and_raw_immutability(service, archive):
    ids = [archive["add"](1, date="2026-01-03T12:00:00"),
           archive["add"](2, date="2026-01-01T12:00:00", rotation=20, value=3)]
    result = done(service, service.prepare(request(ids))["id"])
    assert result["status"] == "ready", result
    assert [f["product_id"] for f in result["frames"]] == ids[::-1]
    assert result["progress"] == {"completed": 2, "total": 2}
    assert result["frames"][0]["timestamp"].startswith("2026-01-01")
    assert len({f["id"] for f in result["frames"]}) == 2
    assert {f["observation_id"] for f in result["frames"]} == {"shared-observation"}
    common = result["common_wcs"]
    assert common["shape"] == [12, 12]
    assert common["field_of_view_arcsec"] == [12 * 6.15] * 2
    expected = sequences.output_wcs(RA, DEC, 12).to_header()
    for frame in result["frames"]:
        path = service.frame_path(result["id"], frame["id"], "fits")
        with fits.open(path, checksum=True) as hdus:
            assert hdus[0].header["DERIVED"]
            assert hdus["IMAGE"].data.dtype.kind == "f"
            assert hdus["IMAGE"].header["BUNIT"] == "MJy/sr"
            assert "VARIANCE" not in hdus and "ORIGVAR" in hdus
            assert hdus["ORIGVAR"].header["ALIGNED"] is False
            assert hdus["IMAGE"].data.shape == (12, 12)
            for key in expected:
                assert hdus["IMAGE"].header[key] == expected[key]
            uncovered = hdus["COVERAGE"].data == 0
            assert uncovered.any() and np.isnan(hdus["IMAGE"].data[uncovered]).all()
            assert np.all(hdus["MASK"].data[uncovered] == 1)
            assert 0 < frame["coverage_fraction"] < 1
            provenance = json.loads(hdus["PROVENANCE"].data["JSON"][0])
            assert provenance["source_sha256"] == archive["records"][frame["product_id"]]["sha256"]
            assert provenance["policy"]["variance"].startswith("omitted")
            assert provenance["observation_time"]["mjd"] == frame["mjd"]
            with fits.open(archive["paths"][frame["asset_id"]]) as raw:
                np.testing.assert_array_equal(hdus["ORIGVAR"].data, raw["VARIANCE"].data)
        png = np.array(Image.open(service.frame_path(result["id"], frame["id"])))
        with fits.open(path) as hdus:
            assert np.all(png[:, :, 3][hdus["COVERAGE"].data[::-1] == 0] == 0)
        assert service.frame_path(result["id"], frame["id"], "mask").suffix == ".png"
    assert archive["leases"]["active"] == 0
    for asset, raw in archive["original"].items():
        assert archive["paths"][asset].read_bytes() == raw
    json.dumps(result, allow_nan=False)


def test_exact_integer_flags_and_bad_bilinear_contributors(service, archive):
    flags = np.zeros((9, 9), np.uint64)
    flags[2, 3] = (1 << 60) | (1 << 21)  # Must survive floating-point reprojection exactly.
    flags[6, 4] = 1 << 6
    pid = archive["add"](3, rotation=17, flags=flags)
    result = done(service, service.prepare(request([pid], mode="all"))["id"])
    assert result["status"] == "insufficient", result
    frame = result["frames"][0]
    with fits.open(service.frame_path(result["id"], frame["id"], "fits")) as hdus:
        assert set(np.unique(hdus["FLAGS"].data)) <= set(np.unique(flags))
        assert np.any(hdus["FLAGS"].data == ((1 << 60) | (1 << 21)))
        assert np.all(hdus["MASK"].data[(hdus["FLAGS"].data & (1 << 6)) != 0] == 1)
        assert np.count_nonzero(hdus["MASK"].data) > np.count_nonzero(hdus["FLAGS"].data & (1 << 6))


def test_original_variance_grid_preserves_sip_wcs(service, archive):
    pid = archive["add"](4, sip=True)
    result = done(service, service.prepare(request([pid]))["id"])
    assert result["status"] == "insufficient", result
    frame = result["frames"][0]
    with fits.open(service.frame_path(result["id"], frame["id"], "fits")) as derived, fits.open(archive["paths"][frame["asset_id"]]) as raw:
        source_wcs = sequences._celestial(raw["IMAGE"].header)
        retained_wcs = WCS(derived["ORIGVAR"].header)
        assert source_wcs.sip is not None and retained_wcs.sip is not None
        assert WCS(derived["IMAGE"].header).sip is None  # Output is the fixed common TAN grid.
        pixels = np.array([[0., 1.], [2.2, 3.], [4., 4.], [7.1, 8.]])
        sky = source_wcs.all_pix2world(pixels, 0)
        np.testing.assert_allclose(retained_wcs.all_pix2world(pixels, 0), sky, atol=1e-10, rtol=0)
        np.testing.assert_allclose(retained_wcs.all_world2pix(sky, 0), pixels, atol=1e-5, rtol=0)


def test_quality_spectral_grouping_both_wave_and_bandwidth(service, archive):
    ids = [archive["add"](10, date="2026-01-01", wave=1., band=.025),
           archive["add"](11, date="2026-01-02", wave=1.005, band=.0251),
           archive["add"](12, date="2026-01-03", wave=1.2),
           archive["add"](13, date="2026-01-04", wave=1., band=.04),
           archive["add"](14, date="2026-01-05", accepted=False)]
    result = done(service, service.prepare(request(ids))["id"])
    assert result["status"] == "ready", result
    assert [f["product_id"] for f in result["frames"]] == ids[:2]
    reasons = {f["product_id"]: f["reason_code"] for f in result["excluded_frames"]}
    assert reasons == {ids[2]: "spectral_mismatch", ids[3]: "spectral_mismatch", ids[4]: "quality_not_accepted"}
    assert result["frames"][0]["quality"]["scientific_acceptance"]["accepted"] is True


def test_all_mode_never_claims_spectral_comparability(service, archive):
    ids = [archive["add"](20, wave=1), archive["add"](21, wave=4, accepted=False)]
    result = done(service, service.prepare(request(ids, mode="all"))["id"])
    assert result["status"] == "ready"
    assert len(result["frames"]) == 2
    assert sequences.SPECTRAL_WARNING in result["warnings"]
    assert result["frames"][1]["quality"]["accepted"] is False


def test_date_wave_filters_and_missing_times_do_not_use_processing_dates(service, archive):
    ids = [archive["add"](30, date="2026-01-01"), archive["add"](31, date="2026-01-02T23:59:00", mjd_only=True),
           archive["add"](32, date="2026-01-02", wave=2), archive["add"](33, times=False)]
    result = done(service, service.prepare(request(ids, date_start="2026-01-02", date_end="2026-01-02",
                                wavelength_min_um=.9, wavelength_max_um=1.1))["id"])
    assert result["status"] == "insufficient", result
    assert [f["product_id"] for f in result["frames"]] == [ids[1]]
    assert {f["reason_code"] for f in result["excluded_frames"]} == {"date_filter", "wavelength_filter"}
    assert "Missing authoritative FITS" in result["failed_frames"][0]["reason"]
    assert result["frames"][0]["time_source"] == "IMAGE.MJD-OBS"


def test_selection_cap_retains_every_candidate_and_downloads_only_selected(service, archive):
    ids = [archive["add"](i) for i in range(40, 52)]
    result = done(service, service.prepare(request(ids))["id"])
    assert len(archive["retrievals"]) == 10
    assert len(result["frames"]) == 10 and len(result["frame_states"]) == 12
    assert {f["reason_code"] for f in result["excluded_frames"]} == {"max_frames"}
    assert result["selection"]["supplied"] == 12
    assert result["selection"]["archive_table_modified"] is False


def test_repeated_product_id_is_not_a_second_time_frame(service, archive):
    pid = archive["add"](55)
    result = done(service, service.prepare(request([pid, pid]))["id"])
    assert result["status"] == "insufficient"
    assert len(result["frames"]) == 1 and len(archive["retrievals"]) == 1
    assert result["excluded_frames"][0]["reason_code"] == "duplicate_product"


def test_strict_mode_rechecks_target_quality_even_if_record_says_accepted(service, archive):
    flags = np.zeros((9, 9), dtype=np.uint64)
    flags[4, 4] = 1 << 6
    pid = archive["add"](56, flags=flags, accepted=True)
    result = done(service, service.prepare(request([pid]))["id"])
    assert result["status"] == "insufficient" and not result["frames"]
    frame = result["excluded_frames"][0]
    assert frame["reason_code"] == "quality_not_accepted"
    assert frame["quality"]["accepted"] is False
    assert frame["quality"]["scientific_acceptance"]["accepted"] is True  # Original validation retained.


def test_backend_grid_limit_and_finite_spectral_pair():
    config = sequences._request(request(["1" * 32], cutout_size_deg=1024 * 6.15 / 3600))
    assert config["dimension"] == 1024
    reference = {"status": "calibrated", "wavelength_um": 1., "bandwidth_um": .025}
    for key in ("wavelength_um", "bandwidth_um"):
        assert not spectral.compatible({**reference, key: float("nan")}, reference)
        assert not spectral.compatible({**reference, key: 0}, reference)


@pytest.mark.parametrize("overrides", [{"max_frames": 101}, {"max_frames": 0}, {"max_frames": True},
    {"cutout_size_deg": 10}, {"ra": 360}, {"dec": float("nan")}, {"mode": "fake"},
    {"date_start": "not-a-date"}, {"date_start": "2026-01-03", "date_end": "2026-01-01"},
    {"wavelength_min_um": 3, "wavelength_max_um": 2}, {"spectral_tolerance_fraction": .5},
    {"display": {"low": 1}}, {"display": {"low": 2, "high": 1}}, {"display": {"stretch": "fake"}},
    {"observation_ids": ["../evil"]}])
def test_request_bounds_are_explicit_and_no_network(service, overrides):
    with pytest.raises(DataError) as exc:
        service.prepare(request(["1" * 32], **overrides))
    assert exc.value.status == 422
    assert not service.jobs


def test_common_display_manual_settings_do_not_change_scientific_cache(service, archive):
    ids = [archive["add"](60, value=1), archive["add"](61, value=10)]
    first = done(service, service.prepare(request(ids))["id"])
    second = done(service, service.prepare(request(ids, display={"low": -5, "high": 100, "stretch": "linear"}))["id"])
    assert first["display"]["low"] != second["display"]["low"]
    assert second["display"]["display_only"]
    assert second["display"]["stretch"] == "linear"
    assert all(f["aligned_cache_hit"] for f in second["frames"])
    assert [f["aligned_cache_key"] for f in first["frames"]] == [f["aligned_cache_key"] for f in second["frames"]]
    for a, b in zip(first["frames"], second["frames"]):
        assert service.frame_path(first["id"], a["id"], "fits").read_bytes() == service.frame_path(second["id"], b["id"], "fits").read_bytes()


def test_prepare_immediate_two_workers_cancel_and_no_leaked_leases(service, archive, monkeypatch):
    ids = [archive["add"](i) for i in range(70, 76)]
    entered, release = threading.Event(), threading.Event()
    counter = {"active": 0, "maximum": 0}
    lock = threading.Lock()
    def slow(*args):
        with lock:
            counter["active"] += 1
            counter["maximum"] = max(counter["maximum"], counter["active"])
            if counter["active"] == 2:
                entered.set()
        try:
            assert release.wait(3)
            return archive["retrieve"](*args)
        finally:
            with lock:
                counter["active"] -= 1
    monkeypatch.setattr(sequences.observations, "retrieve", slow)
    start = time.monotonic()
    job = service.prepare(request(ids))
    assert time.monotonic() - start < .3
    assert entered.wait(2)
    try:
        before = service.cancel(job["id"])
        assert before["cancel_requested"]
    finally:
        release.set()
    result = done(service, job["id"])
    assert result["status"] == "cancelled"
    assert counter["maximum"] == 2
    assert len(archive["retrievals"]) == 2
    assert archive["leases"]["active"] == 0
    assert not result["frames"]


def test_cache_budget_failure_is_per_frame_explicit(service, archive, monkeypatch):
    pid = archive["add"](80)
    monkeypatch.setattr(sequences, "MAX_ALIGNED_BYTES", 100)
    result = done(service, service.prepare(request([pid]))["id"])
    assert result["status"] == "failed"
    assert result["failed_frames"][0]["reason_code"] == "storage_limit"
    assert archive["leases"]["active"] == 0


def test_original_storage_error_reported_without_synthetic_replacement(service, archive):
    result = done(service, service.prepare(request(["f" * 32]))["id"])
    assert result["status"] == "failed"
    assert not result["frames"]
    assert result["failed_frames"][0]["reason_code"] == "storage_limit"


def test_frame_lease_defers_delete_playback_is_local_and_paths_authorized(service, archive, monkeypatch):
    pid = archive["add"](90)
    result = done(service, service.prepare(request([pid]))["id"])
    frame = result["frames"][0]
    monkeypatch.setattr(sequences.observations, "retrieve", lambda *a: pytest.fail("Playback downloaded data"))
    with service.frame_lease(result["id"], frame["id"]) as path:
        assert path.read_bytes().startswith(b"\x89PNG")
        service.jobs[result["id"]]["touched"] = time.monotonic() - sequences.JOB_TTL - 1
        service._prune_jobs()
        assert path.exists() and result["id"] in service.jobs
        assert service.delete(result["id"])["status"] == "deleting"
        assert path.exists()
    assert not path.exists()
    with pytest.raises(DataError):
        service.status(result["id"])
    with pytest.raises(DataError):
        service.frame_path("../escape", frame["id"])


def test_shutdown_resets_module_default_and_allows_fresh_lifespan(tmp_path, monkeypatch):
    monkeypatch.setattr(sequences.observations, "CACHE", tmp_path)
    sequences.shutdown()
    first = sequences._service()
    sequences.shutdown()
    assert sequences._DEFAULT is None and first.closed
    second = sequences._service()
    assert second is not first and not second.closed
    sequences.shutdown()


def test_original_pixel_mapping_is_documented_not_guessed():
    header = fits.Header({"CRPIX1A": -221., "CRPIX2A": -2004., "CRVAL1A": 0., "CRVAL2A": 0.})
    assert spectral.original_pixel(header, 2.1, 2.3) == pytest.approx([224.1, 2007.3])
    header["CRVAL1A"] = 1
    with pytest.raises(ValueError, match="Non-unit"):
        spectral.original_pixel(header, 2, 2)
    with pytest.raises(ValueError, match="Missing"):
        spectral.original_pixel(fits.Header(), 2, 2)


@pytest.mark.parametrize("identifier", ["ID", "id"])
def test_calibration_link_validates_association_detector_and_authentic_irsa(identifier):
    row = {identifier: "did", "semantics": "#calibration", "access_url": CAL_URL, "error_message": ""}
    assert spectral.calibration_link({"datalink": {"rows": [row]}}, "did", 1) == CAL_URL
    for changed in [{identifier: "wrong"}, {"access_url": CAL_URL.replace("irsa.ipac.caltech.edu", "evil.example")},
                    {"access_url": CAL_URL + "?redirect=evil"}, {"semantics": "#this"}]:
        with pytest.raises(ValueError):
            spectral.calibration_link({"datalink": {"rows": [{**row, **changed}]}}, "did", 1)
    with pytest.raises(ValueError):
        spectral.calibration_link({"datalink": {"rows": [row]}}, "did", 2)
    with pytest.raises(ValueError):
        spectral.calibration_link({"datalink": {"rows": [row, {**row, "access_url": CAL_URL.replace("v4", "v5")}]}}, "did", 1)


def test_external_calibration_uses_public_datalink_and_parent_pixel_only(service, archive, monkeypatch):
    pid = archive["add"](100, embedded=False, parent_offset=(10, 20))
    rec = archive["records"][pid]
    calls = []
    def links(record):
        calls.append(record["sia"]["obs_publisher_did"])
        return {"rows": [{"id": calls[-1], "semantics": "#calibration", "access_url": CAL_URL}]}
    def sample(url, x, y, event):
        assert url == CAL_URL
        assert [x, y] == pytest.approx([14, 24], abs=1e-8)
        return {"status": "calibrated", "wavelength_um": 1.2, "bandwidth_um": .03,
                "source": "DataLink CWAVE/CBAND calibration", "parent_pixel": [x, y]}
    monkeypatch.setattr(spectral.archive_enrichment, "datalink", links)
    monkeypatch.setattr(service.calibrations, "sample", sample)
    result = done(service, service.prepare(request([pid]))["id"])
    assert result["status"] == "insufficient", result
    assert calls == [rec["sia"]["obs_publisher_did"]]
    assert result["frames"][0]["spectral"]["source"].startswith("DataLink")


def test_approximation_excluded_comparable_but_labeled_in_all(service, archive, monkeypatch):
    pid = archive["add"](110, embedded=False)
    actual_pixel = spectral.inspector.pixel
    def approximate(*args, **kwargs):
        inspection = actual_pixel(*args, **kwargs)
        inspection["spectral"] = {"status": "available", "axes": [
            {"value": 1.1, "unit": "um"}, {"value": .03, "unit": "um"}]}
        return inspection
    monkeypatch.setattr(spectral.inspector, "pixel", approximate)
    comparable = done(service, service.prepare(request([pid]))["id"])
    assert comparable["status"] == "insufficient" and not comparable["frames"]
    assert comparable["excluded_frames"][0]["spectral"]["status"] == "approximate"
    exploratory = done(service, service.prepare(request([pid], mode="all"))["id"])
    assert exploratory["status"] == "insufficient"
    assert exploratory["frames"][0]["spectral"]["status"] == "approximate"
    assert "Visualization approximation" in exploratory["frames"][0]["spectral"]["warning"]


def test_all_mode_never_initiates_external_calibration_lookup(service, archive, monkeypatch):
    pid = archive["add"](111, embedded=False)
    def fail(*args):
        pytest.fail("Exploratory mode initiated a calibration lookup/download")
    monkeypatch.setattr(spectral.archive_enrichment, "datalink", fail)
    monkeypatch.setattr(service.calibrations, "_download", fail)
    result = done(service, service.prepare(request([pid], mode="all"))["id"])
    assert result["status"] == "insufficient", result
    assert result["frames"][0]["spectral"]["status"] == "unavailable"
    assert sequences.SPECTRAL_WARNING in result["warnings"]


def test_all_mode_reuses_only_cached_product_associated_calibration(service, archive, monkeypatch):
    pid = archive["add"](112, embedded=False, parent_offset=(10, 20))
    rec = archive["records"][pid]
    body = calibration_bytes()
    calls = mock_download(monkeypatch, body)
    monkeypatch.setattr(spectral.archive_enrichment, "datalink", lambda record: {"rows": [
        {"id": rec["sia"]["obs_publisher_did"], "semantics": "#calibration", "access_url": CAL_URL}]})
    strict = done(service, service.prepare(request([pid]))["id"])
    assert strict["status"] == "insufficient", strict
    assert len(calls) == 1
    monkeypatch.setattr(spectral.archive_enrichment, "datalink", lambda *a: pytest.fail("Cached exploration queried DataLink"))
    exploratory = done(service, service.prepare(request([pid], mode="all"))["id"])
    assert exploratory["frames"][0]["spectral"]["status"] == "calibrated"
    assert len(calls) == 1


def calibration_bytes(wave=1.2, band=.04):
    output = io.BytesIO()
    hdr = fits.Header({"BUNIT": "um"})
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(np.full((40, 40), wave), header=hdr, name="CWAVE"),
                  fits.ImageHDU(np.full((40, 40), band), header=hdr, name="CBAND")]).writeto(output, checksum=True)
    return output.getvalue()


def mock_download(monkeypatch, body, status=200, headers=None):
    calls = []
    def send(self, request, **kwargs):
        calls.append((request.url, kwargs))
        response = requests.Response()
        response.status_code = status
        response.request, response.url = request, request.url
        response.headers.update(headers or {})
        response.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False)
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return calls


def test_trusted_calibration_download_sampling_cache_and_no_mutation(tmp_path, monkeypatch):
    body = calibration_bytes()
    calls = mock_download(monkeypatch, body, headers={"Content-Length": str(len(body))})
    store = spectral.CalibrationStore(tmp_path)
    first = store.sample(CAL_URL, 14, 24, threading.Event())
    second = store.sample(CAL_URL, 14, 24, threading.Event())
    assert first == second and first["wavelength_um"] == 1.2
    assert first["calibration_sha256"] == hashlib.sha256(body).hexdigest()
    assert len(calls) == 1
    assert next(tmp_path.glob("*.fits")).read_bytes() == body
    with pytest.raises(ValueError, match="outside"):
        store.sample(CAL_URL, 80, 80, threading.Event())


def test_calibration_inflight_dedup_and_cache_byte_cap(tmp_path, monkeypatch):
    body = calibration_bytes()
    calls = mock_download(monkeypatch, body)
    store = spectral.CalibrationStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.sample(CAL_URL, 1, 1, threading.Event()), range(4)))
    assert len(calls) == 1 and len({r["calibration_sha256"] for r in results}) == 1
    assert not store.pins and not store.flights
    monkeypatch.setattr(spectral, "MAX_CALIBRATION_CACHE", 1)
    with pytest.raises(ValueError, match="bounded cache"):
        spectral.CalibrationStore(tmp_path / "small").sample(CAL_URL, 1, 1, threading.Event())


@pytest.mark.parametrize("case", ["redirect", "oversize", "truncated", "nonfinite", "cancelled", "untrusted"])
def test_calibration_failure_bounds_are_explicit(tmp_path, monkeypatch, case):
    body, headers, status, url = calibration_bytes(), {}, 200, CAL_URL
    event = threading.Event()
    if case == "redirect":
        status, headers = 303, {"Location": "https://evil.example/file"}
    elif case == "oversize":
        monkeypatch.setattr(spectral, "MAX_CALIBRATION_BYTES", 10)
    elif case == "truncated":
        body = body[:3000]
    elif case == "nonfinite":
        body = calibration_bytes(wave=np.nan)
    elif case == "cancelled":
        event.set()
    elif case == "untrusted":
        url = "https://evil.example/file.fits"
    calls = mock_download(monkeypatch, body, status=status, headers=headers)
    with pytest.raises((ValueError, InterruptedError)):
        spectral.CalibrationStore(tmp_path).sample(url, 1, 1, event)
    if case in ("cancelled", "untrusted"):
        assert not calls


def test_observation_time_conflict_is_explicit(tmp_path):
    header = fits.Header({"DATE-OBS": "2026-01-01", "MJD-OBS": float(Time("2026-01-02").mjd)})
    with fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(np.ones((2, 2)), header=header, name="IMAGE")]) as hdus:
        with pytest.raises(ValueError, match="disagree"):
            sequences.observation_time(hdus)


def test_comparable_group_can_start_after_isolated_earliest_wavelength(service, archive):
    ids = [archive['add'](900, date='2026-01-01', wave=1.),
           archive['add'](901, date='2026-01-02', wave=1.20),
           archive['add'](902, date='2026-01-03', wave=1.205)]
    result = done(service, service.prepare(request(ids))['id'])
    assert result['status'] == 'ready'
    assert [f['product_id'] for f in result['frames']] == ids[1:]
    assert result['excluded_frames'][0]['product_id'] == ids[0]


def test_comparable_group_does_not_chain_incompatible_bandpasses(service, archive):
    ids = [archive['add'](910+i, date=f'2026-01-0{i+1}', wave=w) for i,w in enumerate([1.,1.009,1.018])]
    result = done(service, service.prepare(request(ids))['id'])
    assert len(result['frames']) == 2
    for a in result['frames']:
        for b in result['frames']:
            assert spectral.compatible(a['spectral'],b['spectral'],.01)
            assert spectral.compatible(b['spectral'],a['spectral'],.01)


def test_calibration_failure_keeps_actual_upstream_cause():
    with pytest.raises(ValueError, match='upstream/request_failed.*HTTP 502'):
        spectral.calibration_link({'datalink':{'status':'upstream','rows':[],
            'error':{'code':'request_failed','message':'IRSA HTTP 502'}}},'verified-did',1)


def test_adaptive_tries_past_initial_quality_failures_and_publishes(service,archive):
    ids=[archive['add'](970,accepted=False),archive['add'](971,date='2026-01-01',wave=1),archive['add'](972,date='2026-06-01',wave=1.001)]
    result=done(service,service.prepare(request(ids,adaptive=True,candidate_budget=3,max_frames=2))['id'])
    assert result['status']=='ready',result
    assert len(result['frames'])==2
    assert any(f['product_id']==ids[0] for f in result['excluded_frames'])
