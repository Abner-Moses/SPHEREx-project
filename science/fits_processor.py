"""Scientific validation; original FITS bytes are never rewritten."""
import io
import warnings
import numpy as np
from astropy.io import fits
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from astropy.visualization import AsinhStretch, LinearStretch
from astropy import units as u
from PIL import Image
from spherical_geometry.polygon import SphericalPolygon
from archive_client import DataError

# Explanatory Supplement flag names; MP_* header values are bit indices.
# Supplement v2.1 (2026-11-10), Table 16. SOURCE is not a defect.
# FULLSAMPLE is informational but explicitly not usable for science.
POLICY_VERSION = "spherex-level2-v3"
FLAG_REFERENCE = "https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf"
BAD_FLAGS = {
    "TRANSIENT": 0, "OVERFLOW": 1, "SUR_ERROR": 2, "PHANTOM": 4,
    "REFERENCE": 5, "NONFUNC": 6, "DICHROIC": 7, "MISSING_DATA": 9,
    "HOT": 10, "COLD": 11, "FULLSAMPLE": 12, "PHANMISS": 14, "NONLINEAR": 15,
    "PERSIST": 17, "OUTLIER": 19,
    "CROSSTALK": 20, "GHOST": 22, "GHOST_FPA": 23, "GHOST_EXT": 24,
    "STREAK": 25, "BLOOM": 26, "SNOWBALL": 27, "HALO": 28, "SATELLITE_HALO": 29,
}
REQUIRED = {"IMAGE", "FLAGS", "VARIANCE", "ZODI", "EPSF", "WCS-WAVE"}

def quality_mask(image, flags, variance, header=None):
    """True means unsuitable for scientific acceptance; never edits an array."""
    bits = {name: int((header or {}).get("MP_" + name, bit)) for name, bit in BAD_FLAGS.items()}
    mask_value = sum(1 << bit for bit in set(bits.values()))
    return (~np.isfinite(image) | ~np.isfinite(variance) | (variance < 0)
            | ((flags.astype(np.int64) & mask_value) != 0))

def astrometric_quality(value):
    """FINAST=0 is good; positive integral values are poor, not WCS failures."""
    if value is None:
        status = "missing"
    elif isinstance(value, (bool, np.bool_)):
        status = "unrecognized"
    else:
        try:
            number = float(value)
            status = ("good" if number == 0 else "poor" if number > 0 else "unrecognized") if np.isfinite(number) and number.is_integer() else "unrecognized"
        except (ValueError, TypeError, OverflowError):
            status = "unrecognized"
    return {"status": status, "finast": value.item() if isinstance(value, np.generic) else value,
            "reference": FLAG_REFERENCE + " section 3.2.8"}

def display_mask(image, flags, variance, header=None):
    """Separate visualization policy; currently uses the conservative science mask."""
    return quality_mask(image, flags, variance, header)

def validate(path, ra, dec, *, allow_rejected_pixels=False):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", fits.verify.VerifyWarning)
            with fits.open(path, memmap=False, lazy_load_hdus=False, checksum=True) as hdus:
                hdus.verify("exception")
                names = {h.name for h in hdus}
                if not REQUIRED <= names:
                    raise ValueError("Missing extensions: " + ", ".join(sorted(REQUIRED - names)))
                # Force reads of every HDU to detect truncation, including tables.
                for h in hdus:
                    _ = h.data
                    if "CHECKSUM" in h.header and h.verify_checksum() != 1:
                        raise ValueError("Invalid FITS checksum")
                image = hdus["IMAGE"].data
                flags, variance = hdus["FLAGS"].data, hdus["VARIANCE"].data
                if image.ndim != 2 or min(image.shape) < 2 or not np.issubdtype(image.dtype, np.floating):
                    raise ValueError("Expected a calibrated 2D floating IMAGE")
                if flags.shape != image.shape or variance.shape != image.shape or hdus["ZODI"].data.shape != image.shape:
                    raise ValueError("Companion extension dimensions differ")
                if not np.issubdtype(flags.dtype, np.integer):
                    raise ValueError("FLAGS must be an integer bitmap")
                header = hdus["IMAGE"].header
                wcs = WCS(header).celestial
                if not wcs.has_celestial:
                    raise ValueError("Missing celestial WCS")
                coord = SkyCoord(ra*u.deg, dec*u.deg, frame="icrs")
                x, y = map(float, wcs.world_to_pixel(coord))
                height, width = image.shape
                if not (np.isfinite(x) and np.isfinite(y) and 0 <= x <= width-1 and 0 <= y <= height-1):
                    raise ValueError("Requested coordinate is outside the image footprint")
                error = coord.separation(wcs.pixel_to_world(x,y)).arcsec
                if error > 0.01:
                    raise ValueError("WCS round trip failed")
                bad = quality_mask(image, flags, variance, hdus["FLAGS"].header)
                target_usable = not bool(bad[int(np.floor(y+.5)), int(np.floor(x+.5))])
                if not allow_rejected_pixels and (bad.all() or not target_usable):
                    raise ValueError("Requested coordinate has no usable image/variance coverage")
                footprint = wcs.calc_footprint(axes=(width, height)).tolist()
                poly = SphericalPolygon.from_radec(np.array(footprint)[:,0], np.array(footprint)[:,1],
                                                   center=tuple(wcs.pixel_to_world_values((width-1)/2,(height-1)/2)))
                if not poly.contains_radec(ra,dec):
                    raise ValueError("Requested coordinate is outside spherical footprint geometry")
                if not u.Unit(header.get("BUNIT", "")).is_equivalent(u.MJy/u.sr):
                    raise ValueError("IMAGE lacks calibrated surface-brightness units")
                astrometry = astrometric_quality(header.get("FINAST", hdus[0].header.get("FINAST")))
                reasons = []
                if not target_usable: reasons.append("Target pixel is excluded by the scientific pixel-quality policy.")
                if astrometry["status"] != "good": reasons.append("Absolute astrometric quality is " + astrometry["status"] + "; a numerical WCS round trip is not an accuracy certification.")
                warnings_list = list(reasons)
                if bad.any(): warnings_list.append("Some pixels are excluded from scientific acceptance; original values and flags remain accessible.")
                return {
                    "policy_version": POLICY_VERSION,
                    "structural_validity": {"status": "passed"},
                    "numerical_wcs": {"status": "passed", "roundtrip_arcsec": float(error)},
                    "target_coverage": {"status": "covered", "pixel": [x,y]},
                    "astrometric_quality": astrometry,
                    "scientific_acceptance": {"accepted": not reasons, "status": "accepted" if not reasons else "inspection_only", "reasons": reasons},
                    "measurement_quality": {"target_usable": target_usable},
                    "warnings": warnings_list,
                    "extensions": sorted(names), "shape": [height,width],
                    "wcs_passed": True, "target_pixel": [x,y], "roundtrip_arcsec": float(error),
                    "footprint_icrs_deg": footprint, "usable_pixels": int((~bad).sum()),
                    "masked_pixels": int(bad.sum()), "bunit": header.get("BUNIT"),
                    "header": {k: header.get(k, hdus[0].header.get(k)) for k in
                               ("OBSID","OBS_ID","OBSTIME","DETECTOR","BAND","DATE-OBS","MJD-OBS","MJD-BEG","MJD-END","PROCVER","VERSION","PIPEVER","FINAST")},
                    "zodi_subtracted": False,
                    "quality_policy": BAD_FLAGS,
                    "display_limits": np.percentile(image[~bad], [1,99.5]).tolist() if (~bad).any() else None,
                }
    except Exception as exc:
        raise DataError(f"The retrieved observation failed scientific data validation: {exc}") from exc

def image_fits(path):
    """TAN reprojection for Aladin visualization; raw FITS remains independent.

    Aladin 3.9.2 misplaces the IRSA cutout's SIP WCS. Reproject with the
    established reproject package into a plain TAN WCS it can interpret.
    Interpolated values must not be used as original measurements.
    """
    from reproject import reproject_interp
    from astropy.wcs.utils import proj_plane_pixel_scales
    with fits.open(path, memmap=False) as hdus:
        original = hdus["IMAGE"]
        source_wcs = WCS(original.header).celestial
        height,width = original.data.shape
        center = source_wcs.pixel_to_world((width-1)/2,(height-1)/2)
        target = WCS(naxis=2)
        target.wcs.ctype = ["RA---TAN","DEC--TAN"]
        target.wcs.crval = [center.ra.deg,center.dec.deg]
        target.wcs.crpix = [(width+1)/2,(height+1)/2]
        scale = proj_plane_pixel_scales(source_wcs)
        target.wcs.cdelt = [-abs(scale[0]),abs(scale[1])]
        data, footprint = reproject_interp((original.data,source_wcs),target,shape_out=(height,width))
        data[footprint==0] = np.nan
        header = target.to_header()
        header["BUNIT"] = original.header.get("BUNIT","MJy/sr")
        header["HISTORY"] = "Visualization derivative: reproject_interp SIP to TAN; use original MEF for measurements."
        out = io.BytesIO()
        fits.PrimaryHDU(data.astype(np.float32),header=header).writeto(out)
        return out.getvalue()

def preview(path, stretch="asinh", low=1, high=99.5):
    with fits.open(path, memmap=False) as hdus:
        data = hdus["IMAGE"].data.astype(float)
        bad = display_mask(data, hdus["FLAGS"].data, hdus["VARIANCE"].data, hdus["FLAGS"].header)
        if bad.all(): raise DataError("No display-usable pixels; inspect original FITS values and flags instead.",422)
        lo, hi = np.percentile(data[~bad], [low,high])
        scaled = np.zeros_like(data) if hi <= lo else np.clip((data-lo)/(hi-lo),0,1)
        scaled[bad] = 0
        pixels = (AsinhStretch(0.1)(scaled) if stretch == "asinh" else LinearStretch()(scaled))
        out = io.BytesIO()
        Image.fromarray((pixels*255).astype(np.uint8)).save(out, format="PNG")
        return out.getvalue()
