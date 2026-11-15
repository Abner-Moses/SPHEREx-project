"""Bounded exploratory arithmetic on leased, already aligned sequence IMAGEs.

No retrieval, resampling, PSF/background matching, uncertainty propagation or
change detection is performed. ORIGVAR is deliberately never read as a weight.
Products are stateless: no second science cache or retained derivative files.
The ID hashes the actual input FITS bytes, ordered selection and full policy;
repeating the request while its sequence exists reproduces the product.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import io
import json
import math
import re
import threading
from types import MappingProxyType
from typing import Literal

import numpy as np
from astropy import units as u
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from archive_client import DataError
import sequence_service as sequences
import sequence_spectral as spectral

Operation = Literal["addition", "mean", "subtraction", "weighted_mean"]
Output = Literal["fits", "preview", "metadata"]
POLICY = "sequence-composition-v1"
MAX_FRAMES = 16
MAX_PIXELS = 1024 * 1024
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_FRAME_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_PROVENANCE_BYTES = 512 * 1024
MAX_MEMORY_BYTES = 192 * 1024 * 1024
MAX_CONCURRENT = 2
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)
WARNING = ("Exploratory image arithmetic only; no change/variability claim, SNR or significance. "
           "No PSF matching, astrometric correction, photometric matching or background matching/subtraction. "
           "Spectral agreement is established only at the requested target, not across the image. "
           "Aligned variance/covariance is unavailable; ORIGVAR is on the original grid and is not a weight.")
WEIGHT_REASON = ("Weighted mean is unsupported: aligned frames do not propagate validated aligned "
                 "variance/covariance. Original-grid ORIGVAR and COVERAGE are not uncertainty weights.")


@dataclass(frozen=True)
class OperationInfo:
    id: Operation
    minimum_frames: int
    maximum_frames: int
    formula: str
    available: bool = True
    reason: str | None = None


OPERATIONS = MappingProxyType({item.id: item for item in (
    OperationInfo("addition", 2, MAX_FRAMES, "I1 + I2 + ... + In"),
    OperationInfo("mean", 2, MAX_FRAMES, "(I1 + I2 + ... + In) / n"),
    OperationInfo("subtraction", 2, 2, "I1 - I2; request frame order is significant"),
    OperationInfo("weighted_mean", 2, MAX_FRAMES, "inverse-variance mean", False, WEIGHT_REASON),
)})


def capabilities():
    return {"policy": POLICY, "operations": [vars(item).copy() for item in OPERATIONS.values()],
            "prerequisites": ["Existing ready sequence in comparable mode",
                "Distinct ready frame IDs belonging to that sequence",
                "Current scientific acceptance and calibrated target CWAVE/CBAND policy",
                "Consistent authoritative observation timestamps, surface-brightness units, shapes and WCS",
                "Nonempty intersection of finite, covered, unmasked pixels"],
            "limits": {"frames": MAX_FRAMES, "pixels": MAX_PIXELS, "input_bytes": MAX_INPUT_BYTES,
                "frame_bytes": MAX_FRAME_BYTES, "output_bytes": MAX_OUTPUT_BYTES,
                "estimated_working_memory_bytes": MAX_MEMORY_BYTES, "concurrent": MAX_CONCURRENT},
            "outputs": ["fits", "metadata", "preview"], "output_query_required": True,
            "route": "/api/sequences/{id}/combine?output=fits|metadata|preview",
            "stateless": True, "derivative_cache": False, "warning": WARNING}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def _require(condition, message, status=409):
    if not condition:
        raise DataError(message, status)


def _identifier(value, length=32):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{%d}" % length, value) is not None


def _selection(state, frame_ids):
    _require(state.get("status") == "ready" and not state.get("cancel_requested"),
             "Sequence must be ready and not cancelled/deleting.")
    req = state["request"]
    _require(req.get("mode") == "comparable", "Only comparable-mode sequences may be combined.")
    tolerance = req.get("spectral_tolerance_fraction")
    _require(type(tolerance) in (float, int) and math.isfinite(tolerance) and 0 <= tolerance <= .1,
             "Sequence spectral tolerance is invalid.")
    ready = state.get("frames", [])
    states = {frame["id"]: frame for frame in state.get("frame_states", [])}
    indexed = {frame["id"]: frame for frame in ready}
    _require(len(indexed) == len(ready) and len(states) == len(state.get("frame_states", [])),
             "Sequence manifest has duplicate frame IDs.")
    _require(all(value in indexed for value in frame_ids), "Frame IDs must belong to this ready sequence.")
    selected = [indexed[value] for value in frame_ids]
    _require(len({frame.get("product_id") for frame in selected}) == len(selected),
             "Repeated source products cannot be treated as independent frames.")
    reference = min(ready, key=lambda frame: (frame["mjd"], frame["candidate_index"]))["spectral"]
    for frame in selected:
        _require(frame.get("status") == "ready" and states.get(frame["id"]) == frame,
                 "Selected frame is not actually ready in the sequence manifest.")
        quality = frame.get("quality", {})
        _require(quality.get("accepted") is True and not quality.get("reasons")
                 and quality.get("scientific_acceptance", {}).get("accepted") is True
                 and quality.get("policy_version") == sequences.fits_processor.POLICY_VERSION
                 and quality.get("sequence_target_astrometry", {}).get("status") == "good",
                 "Selected frame lacks current accepted scientific/astrometric quality.")
        _require(spectral.compatible(frame.get("spectral", {}), reference, tolerance),
                 "Selected frame violates the sequence target wavelength/bandwidth policy.")
        _require(frame.get("time_scale") == "UTC" and frame.get("time_source") in
                 {f"{hdu}.{key}" for hdu in ("IMAGE", "PRIMARY") for key in ("DATE-OBS", "MJD-OBS", "MJD_OBS")},
                 "Selected frame lacks an authoritative UTC observation timestamp.")
        moment = Time(frame["timestamp"].removesuffix("Z"), scale="utc")
        _require(math.isfinite(frame["mjd"]) and abs(float(moment.mjd) - frame["mjd"]) < 1e-8,
                 "Selected frame timestamps disagree.")
    return selected


def _grid(state):
    shape = state["common_wcs"]["shape"]
    _require(isinstance(shape, list) and len(shape) == 2 and all(type(n) is int and n > 0 for n in shape),
             "Invalid common image shape.")
    pixels = math.prod(shape)
    _require(pixels <= MAX_PIXELS, "Composition output pixel limit exceeded.", 413)
    # Conservative allowance for live arrays, FITS/PNG encoding and bytes copies.
    _require(pixels * 160 + 4 * 1024 * 1024 <= MAX_MEMORY_BYTES,
             "Composition estimated working memory limit exceeded.", 413)
    _require(pixels * 10 + 1024 * 1024 <= MAX_OUTPUT_BYTES,
             "Composition output byte limit exceeded.", 413)
    req = state["request"]
    _require(shape == [req["dimension"]] * 2, "Common shape disagrees with prepared sequence grid.")
    # Compare serialized WCSes alike: wcslib initializes ancillary DATEREF
    # differently on programmatic construction and FITS-header construction.
    expected = WCS(sequences.output_wcs(req["ra"], req["dec"], req["dimension"]).to_header(), fix=False)
    _check_wcs(fits.Header(state["common_wcs"]["header"]), expected)
    return tuple(shape), expected


def _check_wcs(header, expected):
    # DATE-OBS/MJD-OBS are checked independently. All spatial WCS fields, including
    # the reference pixel and celestial frame, must agree (no tiling tolerance).
    header = header.copy()
    for key in ("DATE-OBS", "MJD-OBS", "MJD_OBS", "DATE", "TIMESYS"):
        header.remove(key, ignore_missing=True, remove_all=True)
    wcs = WCS(header, fix=False)
    _require(wcs.pixel_n_dim == 2 and wcs.world_n_dim == 2 and wcs.has_celestial
             and not wcs.has_distortion and wcs.wcs.compare(expected.wcs, tolerance=1e-12),
             "Prepared IMAGE/MASK/COVERAGE WCS does not match the common grid.")


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_frame(path, frame, state, shape, wcs):
    """Load only three bounded aligned planes, never original-grid variance."""
    with fits.open(path, mode="readonly", memmap=False, lazy_load_hdus=True) as hdus:
        _require(len(hdus) <= 12, "Unexpected prepared FITS structure.")
        _require(hdus[0].header.get("DERIVED") is True
                 and hdus[0].header.get("PRODTYPE") == "ALIGNED_SEQUENCE"
                 and hdus[0].header.get("VARPROP") == "OMITTED", "Expected an aligned sequence FITS product.")
        for name in ("IMAGE", "MASK", "COVERAGE", "PROVENANCE"):
            _require(sum(hdu.name == name for hdu in hdus) == 1, "Missing/duplicate prepared FITS plane: " + name)
        for name in ("IMAGE", "MASK", "COVERAGE"):
            hdu = hdus[name]
            _require(type(hdu) is fits.ImageHDU and hdu.shape == shape
                     and hdu.header.get("BITPIX") in (8, 16, 32, 64, -32, -64),
                     "Prepared plane shape/type disagrees with common grid.")
            _check_wcs(hdu.header, wcs)
        _require(hdus["PROVENANCE"].header.get("NAXIS2") == 1
                 and 0 < hdus["PROVENANCE"].header.get("NAXIS1", 0) <= 65536,
                 "Prepared provenance exceeds limits or is missing.")
        provenance = json.loads(hdus["PROVENANCE"].data["JSON"][0])
        _require(isinstance(provenance, dict), "Prepared provenance must be a JSON object.")
        source_sha = provenance.get("source_sha256")
        _require(_identifier(source_sha, 64) and hdus[0].header.get("ORIGSHA") == source_sha
                 and provenance.get("asset_id") == frame.get("asset_id")
                 and provenance.get("source_product_id") == frame.get("product_id"),
                 "Prepared FITS identity/provenance disagrees with selected frame.")
        policy = provenance["policy"]
        _require(isinstance(policy, dict) and policy.get("version") == sequences.POLICY
                 and policy.get("wcs") == state["common_wcs"]
                 and policy.get("variance") == "omitted; original grid retained"
                 and policy.get("mask") == "any bad bilinear contributor or missing coverage",
                 "Prepared FITS alignment/quality policy is unsupported or stale.")
        key = hashlib.sha256(json.dumps([source_sha, policy], sort_keys=True).encode()).hexdigest()
        _require(key == frame.get("aligned_cache_key"), "Prepared frame cache identity disagrees with provenance.")
        stamp = sequences.observation_time(hdus)
        original_time = provenance["observation_time"]
        _require(abs(stamp["mjd"] - frame["mjd"]) < 1e-8
                 and isinstance(original_time, dict)
                 and all(original_time.get(key) == frame.get(key) for key in ("timestamp", "time_source", "time_scale"))
                 and abs(original_time["mjd"] - frame["mjd"]) < 1e-8,
                 "Prepared FITS observation timestamps disagree with selected frame.")
        unit = u.Unit(hdus["IMAGE"].header.get("BUNIT", ""))
        _require(unit.is_equivalent(u.MJy / u.sr), "Prepared IMAGE requires calibrated surface-brightness units.")
        factor = unit.to(u.MJy / u.sr)
        data = np.array(hdus["IMAGE"].data, dtype=np.float64, copy=True)
        data *= factor
        mask, coverage = hdus["MASK"].data, hdus["COVERAGE"].data
        _require(np.issubdtype(mask.dtype, np.integer) and np.all((mask == 0) | (mask == 1)),
                 "Prepared MASK must contain binary invalid-pixel flags.")
        _require(np.all(~np.isfinite(coverage) | ((coverage >= 0) & (coverage <= 1))),
                 "Prepared COVERAGE must be a footprint in [0, 1], never a weight.")
        valid = (mask == 0) & np.isfinite(data) & np.isfinite(coverage) & (coverage > 0)
        return data, valid, {"frame_id": frame["id"], "product_id": frame["product_id"],
            "asset_id": frame["asset_id"], "source_sha256": source_sha, "aligned_sha256": _sha(path),
            "aligned_cache_key": key, "timestamp": frame["timestamp"], "mjd": frame["mjd"],
            "time_source": frame["time_source"], "time_scale": "UTC", "input_unit": str(unit),
            "scale_to_mjy_sr": factor, "spectral": frame["spectral"], "quality": frame["quality"]}


class _BoundedBuffer(io.BytesIO):
    def write(self, value):
        # FITS passes multidimensional memoryviews, whose len() counts rows.
        _require(self.tell() + memoryview(value).nbytes <= MAX_OUTPUT_BYTES,
                 "Composition output byte limit exceeded.", 413)
        return super().write(value)


@dataclass(frozen=True)
class Product:
    id: str
    metadata: dict
    content: bytes | None
    media_type: str


def _encode(data, valid, wcs, metadata, output):
    if output == "metadata":
        _require(len(_json(metadata).encode()) <= MAX_OUTPUT_BYTES, "Metadata output limit exceeded.", 413)
        return None, "application/json"
    with _BoundedBuffer() as buffer:
        if output == "preview":
            display = metadata["preview_display"]
            values = np.zeros(data.shape, dtype=np.float64)
            values[valid] = np.clip((data[valid] - display["low"]) / (display["high"] - display["low"]), 0, 1)
            gray = np.rint(values * 255).astype(np.uint8)
            rgba = np.dstack([gray, gray, gray, valid.astype(np.uint8) * 255])
            info = PngInfo()
            info.add_text("Product", "DERIVED DISPLAY ONLY; " + WARNING)
            info.add_text("CompositionID", metadata["id"])
            info.add_text("Display", _json(display))
            Image.fromarray(rgba[::-1]).save(buffer, format="PNG", pnginfo=info)
            media = "image/png"
        else:
            header = fits.Header({"DERIVED": True, "PRODTYPE": "SEQUENCE_COMPOSITION", "COMPID": metadata["id"],
                "SEQID": metadata["sequence_id"], "COMPOP": metadata["operation"], "NINPUT": len(metadata["inputs"]),
                "POLICY": POLICY, "VARPROP": "UNAVAILABLE", "PSFMATCH": False, "BKGMATCH": False,
                "CHGCLAIM": False, "MASKPOL": "INTERSECTION", "BUNIT": "MJy/sr",
                "MJD-BEG": min(frame["mjd"] for frame in metadata["inputs"]),
                "MJD-END": max(frame["mjd"] for frame in metadata["inputs"]), "TIMESYS": "UTC"})
            header["HISTORY"] = "MJD-BEG/END bound input observation starts, not exposure duration."
            header["HISTORY"] = WARNING
            for index, frame in enumerate(metadata["inputs"], 1):
                header[f"FRM{index:03d}"] = frame["frame_id"]
            image_header = wcs.to_header()
            image_header["BUNIT"] = "MJy/sr"
            mask_header = wcs.to_header()
            mask_header["BUNIT"] = "1"
            mask_header["HISTORY"] = "MASK=1 is invalid. COVERAGE=1 only on the common valid intersection; not a weight."
            text = _json(metadata)
            with fits.HDUList([fits.PrimaryHDU(header=header),
                    fits.ImageHDU(data, header=image_header, name="IMAGE"),
                    fits.ImageHDU((~valid).astype(np.uint8), header=mask_header, name="MASK"),
                    fits.ImageHDU(valid.astype(np.uint8), header=mask_header, name="COVERAGE"),
                    fits.BinTableHDU.from_columns([fits.Column(name="JSON", format=f"{len(text)}A", array=[text])],
                                                 name="PROVENANCE")]) as hdus:
                # No wall-clock DATE or checksum comments: identical inputs produce identical bytes.
                hdus.writeto(buffer)
            media = "application/fits"
        return buffer.getvalue(), media


def combine(identifier: str, frame_ids: list[str], op: Operation, output: Output, *, service=None) -> Product:
    """Compute from local leased FITS only; fail fast on bounded worker admission."""
    _require(_identifier(identifier), "Invalid sequence ID.", 422)
    _require(isinstance(op, str) and op in OPERATIONS, "Unknown composition operation.", 422)
    operation = OPERATIONS[op]
    _require(operation.available, operation.reason, 422)
    _require(output in ("fits", "metadata", "preview"), "Explicit output must be fits, metadata or preview.", 422)
    _require(isinstance(frame_ids, list) and operation.minimum_frames <= len(frame_ids) <= operation.maximum_frames,
             f"{op} requires {operation.minimum_frames}..{operation.maximum_frames} frame IDs; no truncation.", 422)
    _require(all(_identifier(value) for value in frame_ids) and len(set(frame_ids)) == len(frame_ids),
             "Supply distinct valid frame IDs.", 422)
    _require(_SLOTS.acquire(blocking=False), "Composition concurrency capacity reached; retry later.", 503)
    service = sequences if service is None else service
    try:
        with ExitStack() as leases:
            state = service.status(identifier)
            selected = _selection(state, frame_ids)
            paths = [leases.enter_context(service.frame_lease(identifier, frame["id"], "fits")) for frame in selected]
            # Recheck state after acquiring every lease; deletion cannot remove the files during use.
            state = service.status(identifier)
            selected = _selection(state, frame_ids)
            shape, wcs = _grid(state)
            sizes = [path.stat().st_size for path in paths]
            _require(max(sizes) <= MAX_FRAME_BYTES and sum(sizes) <= MAX_INPUT_BYTES,
                     "Composition input byte limit exceeded.", 413)
            result, intersection, inputs = np.zeros(shape, dtype=np.float64), np.ones(shape, dtype=bool), []
            for index, (path, frame) in enumerate(zip(paths, selected)):
                data, valid, provenance = _read_frame(path, frame, state, shape, wcs)
                intersection &= valid
                # Invalid values never enter arithmetic; the final mask is the strict intersection.
                with np.errstate(over="ignore", invalid="ignore"):
                    if op == "subtraction" and index == 1:
                        np.subtract(result, data, out=result, where=valid)
                    else:
                        np.add(result, data, out=result, where=valid)
                inputs.append(provenance)
            if op == "mean":
                result /= len(selected)
            _require(np.any(intersection), "Selected frames have no common valid pixels.")
            _require(np.all(np.isfinite(result[intersection])), "Composition arithmetic overflow; no product returned.")
            result[~intersection] = np.nan
            values = result[intersection]
            sample = values[::max(1, math.ceil(values.size / 10000))]
            low, high = map(float, np.percentile(sample, [1, 99]))
            if high <= low:
                high = low + max(1e-6, abs(low) * 1e-6)
            _require(math.isfinite(high) and high > low and math.isfinite(high - low),
                     "Cannot represent a finite preview display range.")
            metadata = {"policy": POLICY, "alignment_policy": sequences.POLICY, "sequence_id": identifier,
                "operation": op, "formula": operation.formula, "frame_ids": list(frame_ids), "inputs": inputs,
                "shape": list(shape), "wcs": dict(wcs.to_header()), "unit": "MJy/sr",
                "mask_policy": "Intersection of all finite IMAGE, MASK=0 and finite COVERAGE>0; no missing fill or extrapolation",
                "valid_pixels": int(values.size), "spectral_tolerance_fraction": state["request"]["spectral_tolerance_fraction"],
                "spectral_scope": "Requested target pixel only; no whole-field spectral equivalence",
                "variance_propagated": False, "psf_matching": False, "background_matching": False,
                "background_subtraction": False, "originals_modified": False, "change_claim": False,
                "exploratory": True, "warning": WARNING,
                "preview_display": {"display_only": True, "stretch": "linear", "low": low, "high": high,
                    "sampling": "1st/99th percentiles of <=10000 deterministic valid samples",
                    "png_origin": "upper; FITS rows reversed", "invalid_pixels": "transparent"},
                "reproduce": {"method": "POST", "route": f"/api/sequences/{identifier}/combine",
                    "body": {"frame_ids": list(frame_ids), "op": op}, "output_query": ["fits", "metadata", "preview"],
                    "availability": "While the source sequence remains available; no derivative cache"}}
            digest = hashlib.sha256(_json(metadata).encode()).hexdigest()
            metadata["id"] = digest
            _require(len(_json(metadata).encode()) <= MAX_PROVENANCE_BYTES,
                     "Composition provenance byte limit exceeded.", 413)
            content, media = _encode(result, intersection, wcs, metadata, output)
            return Product(digest, metadata, content, media)
    except DataError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, OSError, OverflowError) as exc:
        raise DataError("Prepared sequence/FITS failed composition validation: " + str(exc), 409) from exc
    finally:
        _SLOTS.release()
