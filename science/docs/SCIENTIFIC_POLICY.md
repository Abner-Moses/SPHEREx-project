# Scientific acceptance and data products

Policy `spherex-level2-v3` follows SPHEREx Explanatory Supplement v2.1,
2026-11-10, Table 16 and section 3.2.8:
https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf

This is a conservative acceptance policy for the tested DR1/QR3 Level 2 MEFs,
not a universal photometry prescription or certification of every scientific use.
Original FITS bytes, calibrated values and flags are never rewritten.

## Pixel policy

Exclude nonfinite IMAGE, nonfinite VARIANCE, negative VARIANCE and these flags:

| Bit | Flag |
|---|---|
| 0 | TRANSIENT |
| 1 | OVERFLOW |
| 2 | SUR_ERROR |
| 4 | PHANTOM (defensive reserved/reference handling when present in product header) |
| 5 | REFERENCE (defensive reference handling) |
| 6 | NONFUNC |
| 7 | DICHROIC |
| 9 | MISSING_DATA |
| 10 | HOT |
| 11 | COLD |
| 12 | FULLSAMPLE — informational but explicitly not usable for science |
| 14 | PHANMISS |
| 15 | NONLINEAR |
| 17 | PERSIST |
| 19 | OUTLIER |
| 20 | CROSSTALK |
| 22 | GHOST |
| 23 | GHOST_FPA |
| 24 | GHOST_EXT |
| 25 | STREAK |
| 26 | BLOOM |
| 27 | SNOWBALL |
| 28 | HALO |
| 29 | SATELLITE_HALO |

The FLAGS HDU's declared MP_* bit indices override fallback indices. SOURCE (21)
identifies a known source: it neither proves usability nor independently excludes
a pixel. Zero flags may be valid. Undefined bits are retained in raw FLAGS;
the inspector reports bits lacking a definition. The mask does not independently
certify conditions not described by this policy.

`quality_mask` is the science acceptance mask. `display_mask` is a separate
visualization policy, currently equally conservative. Neither alters the source.
Optional single-observation PNG previews use independently calculated 1st/99.5th
percentiles and asinh(a=0.1), or explicitly selected preview parameters. Such
previews do not establish a common photometric scale between observations.

## Validation versus inspection

Validation separately reports structural validity, numerical WCS consistency,
target coverage, astrometric quality, scientific acceptance and quality warnings.
A WCS round trip checks numerical consistency; it is not absolute astrometric
accuracy. FINAST=0 is good, positive integral values are poor, absent values are
missing, and negative/nonintegral/unparseable/boolean values are unrecognized.
Only good astrometry plus a usable target pixel permits acceptance under this
policy. Poor/unknown astrometry and flagged target pixels remain inspectable when
FITS structure, calibration units and celestial coverage are valid.

Missing required HDUs, broken/truncated FITS, invalid IMAGE calibration units,
incompatible companion dimensions and invalid/outside-target WCS are errors.
Raw IMAGE remains MJy/sr; VARIANCE is variance, not standard deviation. ZODI is
not subtracted automatically. Existing authentic cached files are reclassified
under the current policy when opened; retrieval provenance remains unchanged.

## Spatial retrieval

Point retrieval uses the requested ICRS position. Region retrieval intersects the
authoritative exposure footprint and active viewer polygon on the sphere. It
prefers the explicitly supplied position, then a covered viewport center, then a
verified intersection interior. There is no exposure-center fallback. The actual
FITS target and returned cutout overlap are checked and returned in provenance.
Archive corner footprints are approximations; successful metadata discovery is
not a substitute for selected-file WCS/quality validation.

## Chronological sequences

Chronology uses original DATE-OBS/MJD-OBS/MJD-BEG, with consistency checks, not
release/processing/ingestion dates. HiPS's 102 frames are spectral, not temporal.

Comparable mode requires scientific acceptance and genuine target-pixel CWAVE
and CBAND calibration. Both central wavelength and bandwidth must match the
earliest accepted frame within the explicit relative tolerance (default 1%).
Detector identity and a broad SIA wavelength interval are insufficient. The
policy is target-specific, not a claim of spectral identity across every pixel
of the displayed cutout, matched PSFs or equivalent background conditions.

Associated calibration comes from the product's official DataLink. The declared
alternate-A mapping links the cutout's nearest sampled pixel to its original
detector pixel. Calibration source URL/hash and lookup position are recorded.
WCS-WAVE interpolation is a visualization estimate and cannot qualify a frame
for Comparable mode. Missing calibration or insufficient compatible frames is
reported explicitly. All Observations is exploratory and persistently warns
that spectral differences can resemble temporal variability.

Each prepared frame uses one fixed ICRS TAN grid, north up, 6.15 arcsec/pixel,
bounded at 1024 pixels per side. Reprojection follows sky coordinates, without
aligning on a moving object. No extrapolation, background subtraction/matching,
PSF matching, temporal detection or higher-resolution information is introduced.
FLAGS are gathered as original integers using nearest-neighbor pixel indices.
MASK excludes missing coverage and any bad bilinear contributor. COVERAGE is an
interpolation footprint, not an exposure weight or uncertainty estimate.

Aligned uncertainty propagation/covariance is not implemented. Derived FITS
explicitly omit an aligned VARIANCE extension and set VARPROP=OMITTED. ORIGVAR
retains original source-grid variance with its own original celestial WCS. Use
the original FITS for quantitative uncertainty analysis. All other source HDUs
remain accessible through the original asset.

One shared physical-value stretch is calculated for prepared frames from valid
samples (bounded deterministic sampling), or supplied manually. Display-only PNGs
carry alpha masks; brightness/contrast controls apply equally to every frame.
Original and derived floating-point FITS remain separate from 8-bit previews.
Frame rate changes only display timing; observation-time intervals remain visible.
