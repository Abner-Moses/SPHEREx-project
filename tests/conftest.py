"""Shared fixtures. Nothing here touches the network."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import spherex_pipeline as pipeline  # noqa: E402

SIZE = 200
CRVAL = (269.45, 4.69)


def make_l2_fits(path: Path, size: int = SIZE) -> dict:
    """A small file shaped like a SPHEREx L2 product: IMAGE (TAN-SIP), FLAGS, ZODI, with known truth."""
    rng = np.random.default_rng(7)
    zodi = np.full((size, size), 0.5, dtype=np.float32)
    sky = rng.normal(2.0, 0.2, (size, size)).astype(np.float32)
    image = sky + zodi  # IMAGE is NOT zodi-subtracted in the real files
    flags = np.zeros((size, size), dtype=np.int32)
    flags[:, : size // 2] |= 1 << 21  # SOURCE: set on half the frame, must not be masked
    flags[10, 10] |= 1 << 6  # NONFUNC
    flags[20, 30] |= 1 << 10  # HOT
    flags[30:33, 40:43] |= 1 << 25  # STREAK
    flags[50, 50] |= 1 << 12  # FULLSAMPLE: informational, must not be masked
    header = fits.Header()
    header["CTYPE1"], header["CTYPE2"] = "RA---TAN-SIP", "DEC--TAN-SIP"
    header["CRPIX1"], header["CRPIX2"] = (size + 1) / 2, (size + 1) / 2
    header["CRVAL1"], header["CRVAL2"] = CRVAL
    header["CD1_1"], header["CD1_2"], header["CD2_1"], header["CD2_2"] = -6.15 / 3600, 0.0, 0.0, 6.15 / 3600
    header["CUNIT1"] = header["CUNIT2"] = "deg"
    header["RADESYS"] = "ICRS"
    header["A_ORDER"] = header["B_ORDER"] = 2
    header["A_2_0"], header["B_0_2"] = 3e-5, -2e-5  # visible distortion at the frame edge
    header["AP_ORDER"] = header["BP_ORDER"] = 2
    header["AP_2_0"], header["BP_0_2"] = -3e-5, 2e-5
    header["BUNIT"] = "MJy / sr"
    primary = fits.PrimaryHDU()
    primary.header.update({"DETECTOR": 6, "OBSID": "TEST_0001_4", "DATE-OBS": "2026-08-17T00:54:34.145", "MJD-AVG": 61269.0386, "XPOSURE": 113.5})
    hdul = fits.HDUList([
        primary,
        fits.ImageHDU(image, header=header, name="IMAGE"),
        fits.ImageHDU(flags, name="FLAGS"),
        fits.ImageHDU(zodi, name="ZODI"),
    ])
    hdul.writeto(path, overwrite=True)
    return {"sky": sky, "flags": flags, "zodi": zodi}


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SPHEREX_CACHE_DIR", str(tmp_path / "cache"))
    # Download tests use a loopback HTTP fixture, independent of demo offline mode.
    monkeypatch.setenv("SPHEREX_OFFLINE", "0")
    pipeline._PRODUCTS.clear()
    return tmp_path / "cache"


@pytest.fixture()
def l2_file(tmp_path):
    path = tmp_path / "level2_test.fits"
    truth = make_l2_fits(path)
    return path, truth
