"""Versioned scientific lens contract and complete, conditional capability matrix.

Statuses describe implementations, not a promise that a particular asset has
the necessary data. Execution resolves prerequisites without retrieval or AI.
"""
from copy import deepcopy
import math
from typing import Annotated, Any, Literal

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

VERSION = 'scientific-lenses-v1'
MAX_NATIVE_PIXELS = 1024 * 1024
LensID = Literal['spatial', 'spectral', 'temporal', 'object', 'statistical',
                 'three-dimensional', 'discovery', 'quality']
Status = Literal['available', 'experimental', 'unavailable']
Category = Literal['calibrated_measurement', 'visualization_estimate',
                   'catalog_supplied', 'sequence_descriptor', 'unavailable']
ResultCategory = Literal['observed', 'calculated', 'model-derived',
                         'externally-sourced', 'ml-predicted', 'unknown']
Identifier = Annotated[str, Field(strict=True, pattern=r'^[a-f0-9]{32}$')]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Use a finite JSON number, excluding booleans.')
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError('JSON number exceeds finite floating-point bounds.') from exc
    if not math.isfinite(value):
        raise ValueError('Use a finite JSON number, excluding booleans.')
    return value


Finite = Annotated[float, BeforeValidator(_number), Field(allow_inf_nan=False)]
RA = Annotated[Finite, Field(ge=0, lt=360)]
DEC = Annotated[Finite, Field(ge=-90, le=90)]
Extent = Annotated[Finite, Field(gt=0, le=5)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


def tangent_coordinates(ra, dec, anchor_ra, anchor_dec):
    """Gnomonic plane about an ICRS anchor; straight edges are great circles.

    SkyCoord supplies the spherical rotation, including RA wrap and poles.
    The back hemisphere is excluded, never folded onto the selection.
    """
    anchor = SkyCoord(anchor_ra * u.deg, anchor_dec * u.deg, frame='icrs')
    sky = SkyCoord(np.asarray(ra) * u.deg, np.asarray(dec) * u.deg, frame='icrs')
    local = sky.transform_to(anchor.skyoffset_frame()).cartesian
    front = np.asarray(local.x.value) > 0
    with np.errstate(divide='ignore', invalid='ignore'):
        x = np.asarray(local.y.value) / np.asarray(local.x.value)
        y = np.asarray(local.z.value) / np.asarray(local.x.value)
    return x, y, front


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


class Selection(Contract):
    frame: Literal['icrs'] = 'icrs'
    ra: RA
    dec: DEC
    shape: Literal['circle', 'rectangle', 'polygon']
    radius_deg: Extent | None = None
    width_deg: Extent | None = None
    height_deg: Extent | None = None
    vertices: list[tuple[RA, DEC]] | None = Field(default=None, min_length=3, max_length=64)

    @model_validator(mode='after')
    def geometry(self):
        fields = {k for k in ('radius_deg', 'width_deg', 'height_deg', 'vertices')
                  if getattr(self, k) is not None}
        expected = {'circle': {'radius_deg'}, 'rectangle': {'width_deg', 'height_deg'},
                    'polygon': {'vertices'}}[self.shape]
        if fields != expected:
            raise ValueError(f'{self.shape} requires exactly {sorted(expected)} geometry fields.')
        if self.shape != 'polygon':
            return self
        ra, dec = np.array(self.vertices).T
        anchor = SkyCoord(self.ra*u.deg, self.dec*u.deg)
        if np.any(anchor.separation(SkyCoord(ra*u.deg, dec*u.deg)).deg > 5):
            raise ValueError('Polygon vertices must lie within 5 degrees of the ICRS anchor.')
        x, y, _ = tangent_coordinates(ra, dec, self.ra, self.dec)
        points = np.column_stack((x, y))
        if len(set(self.vertices)) != len(points):
            raise ValueError('Polygon vertices must be distinct; do not repeat the closing vertex.')
        area = abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2
        if area < 1e-16:
            raise ValueError('Polygon area is degenerate.')
        n = len(points)
        for i in range(n):
            a, b = points[i], points[(i+1) % n]
            if abs(_cross(a, b, points[(i+2) % n])) < 1e-16:
                raise ValueError('Polygon has degenerate adjacent edges.')
            for j in range(i+1, n):
                if j == i+1 or (i == 0 and j == n-1):
                    continue
                c, d = points[j], points[(j+1) % n]
                # Includes touching/collinear nonadjacent edges: reject ambiguity.
                if (_cross(a,b,c)*_cross(a,b,d) <= 0 and
                        _cross(c,d,a)*_cross(c,d,b) <= 0 and
                        np.all(np.maximum(np.minimum(a,b), np.minimum(c,d)) <=
                               np.minimum(np.maximum(a,b), np.maximum(c,d)))):
                    raise ValueError('Polygon must be simple without crossing or touching edges.')
        return self

    def contains(self, ra, dec):
        if self.shape == 'circle':
            sky = SkyCoord(np.asarray(ra)*u.deg, np.asarray(dec)*u.deg, frame='icrs')
            return SkyCoord(self.ra*u.deg, self.dec*u.deg).separation(sky).deg <= self.radius_deg + 1e-12
        x, y, front = tangent_coordinates(ra, dec, self.ra, self.dec)
        if self.shape == 'rectangle':
            return (front & (np.abs(x) <= math.tan(math.radians(self.width_deg/2)) + 1e-14)
                    & (np.abs(y) <= math.tan(math.radians(self.height_deg/2)) + 1e-14))
        vr, vd = np.array(self.vertices).T
        vx, vy, _ = tangent_coordinates(vr, vd, self.ra, self.dec)
        inside = np.zeros(np.shape(x), dtype=bool)
        boundary = np.zeros(np.shape(x), dtype=bool)
        for i in range(len(vx)):
            j = (i+1) % len(vx)
            dx, dy = vx[j]-vx[i], vy[j]-vy[i]
            cross = (x-vx[i])*dy - (y-vy[i])*dx
            boundary |= ((np.abs(cross) <= 1e-14 * math.hypot(dx,dy)) &
                         (x >= min(vx[i],vx[j])-1e-14) & (x <= max(vx[i],vx[j])+1e-14) &
                         (y >= min(vy[i],vy[j])-1e-14) & (y <= max(vy[i],vy[j])+1e-14))
            if dy != 0:
                inside ^= ((vy[i] > y) != (vy[j] > y)) & (x < dx*(y-vy[i])/dy+vx[i])
        return front & (inside | boundary)


class CatalogRow(Contract):
    id: str = Field(strict=True, min_length=1, max_length=160)
    ra: RA
    dec: DEC
    object_type: str | None = Field(default=None, strict=True, max_length=160)
    uncertainty_arcsec: Annotated[Finite, Field(ge=0)] | None = None


class SuppliedCatalog(Contract):
    source: str = Field(strict=True, min_length=1, max_length=500)
    reference_epoch: str = Field(strict=True, min_length=1, max_length=80)
    rows: list[CatalogRow] = Field(max_length=1000)


class Parameters(Contract):
    products: list[Annotated[str, Field(strict=True, min_length=1, max_length=80)]] | None = Field(
        default=None, min_length=1, max_length=32)
    bins: int = Field(default=64, strict=True, ge=2, le=256)
    maximum: int = Field(default=32, strict=True, ge=2, le=32)
    aperture_radius_px: Annotated[Finite, Field(gt=0, le=50)] = 3.
    annulus_inner_px: Annotated[Finite, Field(gt=0, le=99)] = 6.
    annulus_outer_px: Annotated[Finite, Field(gt=0, le=100)] = 10.
    catalog: SuppliedCatalog | None = None

    @model_validator(mode='after')
    def bounded_photometry(self):
        if not self.aperture_radius_px+2 <= self.annulus_inner_px < self.annulus_outer_px:
            raise ValueError('Require aperture_radius_px+2 <= annulus_inner_px < annulus_outer_px.')
        if self.products is not None and len(set(self.products)) != len(self.products):
            raise ValueError('Requested products must be distinct.')
        return self


class LensRequest(Contract):
    lens_id: LensID
    selection: Selection
    asset_id: Identifier | None = None
    sequence_id: Identifier | None = None
    parameters: Parameters = Field(default_factory=Parameters)

    @model_validator(mode='after')
    def compatible_sources(self):
        if self.sequence_id is not None and self.lens_id != 'temporal':
            raise ValueError('sequence_id is supported only by the temporal lens.')
        if self.parameters.catalog is not None and self.lens_id != 'object':
            raise ValueError('Supplied catalog belongs to the object lens.')
        if self.parameters.products:
            supported = {p['id'] for p in LENSES[self.lens_id]['products']}
            if not set(self.parameters.products) <= supported:
                raise ValueError('Unknown product for this lens; consult /api/lenses/capabilities.')
        return self


class ProductStatus(Contract):
    id: str
    status: Status
    reason: str
    prerequisites: list[str]


class VisualizationDescriptor(Contract):
    # Descriptor fields vary by kind; all emitted values are finite JSON.
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    kind: Literal['heatmap', 'native_map', 'selection_overlay', 'histogram',
                  'table', 'catalog', 'sequence', 'spectrum_point', 'brightness_surface']
    product_id: str
    selection: Selection
    scope: str


class AnalysisResult(Contract):
    analysis_id: Identifier
    schema_version: str = VERSION
    lens_id: LensID
    selection: Selection
    result_status: Literal['completed', 'partial', 'unavailable']
    category: Category
    result_category: ResultCategory
    measurement_values: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    visualization_descriptors: list[VisualizationDescriptor] = Field(default_factory=list)
    products: list[ProductStatus]
    provenance: dict[str, Any]
    uncertainties: dict[str, Any]
    method: str
    warnings: list[str] = Field(default_factory=list)
    original_data_modified: Literal[False] = False
    inference_performed: Literal[False] = False
    retrieval_started: Literal[False] = False


def _product(id, status, reason, *prerequisites):
    return dict(id=id, status=status, reason=reason, prerequisites=list(prerequisites))


ASSET = 'Existing leased original FITS asset_id with IMAGE, VARIANCE, FLAGS, calibrated units and celestial WCS'
REGION = 'Selection overlaps native IMAGE pixel centers; at most 1048576 native pixels'
SCIENCE = 'Science-usable selected pixels under the current flag/variance policy'
P = _product
LENSES = {
    'spatial': dict(id='spatial', label='Spatial', description='Original-grid brightness, uncertainty and geometry.', products=[
        P('brightness-map','available','Selected original-pixel means on a bounded display grid; lazy native data also described.',ASSET,REGION,SCIENCE),
        P('uncertainty-map','available','Uncertainty of each selected block mean from original variance, assuming independent pixels.',ASSET,REGION,SCIENCE),
        P('snr-map','available','Block mean divided by its propagated error; zero error gives null. No background or source detection.',ASSET,REGION,SCIENCE),
        P('aperture-photometry','available','Target aperture estimate, never a regional flux map.',ASSET,SCIENCE,'Anchor plus conservative full annulus support inside selection and IMAGE','At least 10 usable background samples'),
        P('line-profile','unavailable','Specify and validate an ICRS line selection; existing pixel-profile service is separate.',ASSET,'Two line endpoints and sample policy'),
        P('radial-profile','unavailable','No validated annular radial estimator is installed.',ASSET,'Center, bins, masking and covariance propagation'),
        P('contours','unavailable','No contour extraction is installed; render the labeled brightness grid if desired.',ASSET,'Validated contour levels and original-grid interpolation policy'),
        P('segmentation','unavailable','No validated source segmentation or morphology algorithm is installed.',ASSET,'Validated detection and deblending method')]),
    'spectral': dict(id='spectral',label='Spectral',description='Target wavelength estimate with explicit calibration limits.',products=[
        P('target-estimate','experimental','Declared WCS-WAVE interpolation at the anchor; visualization estimate only.',ASSET,'Anchor inside selection and IMAGE','Valid declared WCS-WAVE lookup table'),
        P('calibrated-bandpass','unavailable','This bounded executor never fetches calibration; use explicit calibrated spectral service separately.',ASSET,'Genuine local CWAVE/CBAND at original detector position'),
        P('spectrum','unavailable','One target bandpass is not a spectrum.', 'Multiple genuine calibrated spectral samples','Matched apertures, PSFs, units and uncertainties'),
        P('sed','unavailable','No multiband SED builder or fitter is installed.','Associated calibrated photometry and bandpasses','Validated SED and uncertainty model'),
        P('spectral-lines','unavailable','No line extraction or significance estimator is installed.','Resolved calibrated spectrum and instrument line response'),
        P('redshift','unavailable','No validated redshift estimator is installed.','Resolved spectral features or validated multiband model'),
        P('color-map','unavailable','A single varying-bandpass exposure cannot establish a color map.','Registered comparable multiband assets and propagated errors'),
        P('spectral-index','unavailable','No spectral-index estimator is installed.','Matched calibrated frequency samples and uncertainty model')]),
    'temporal': dict(id='temporal',label='Temporal',description='Existing prepared chronology; no variability inference.',products=[
        P('sequence','available','Lazy descriptors for an existing ready sequence; selection overlay is required.','Existing ready sequence_id with at least two frames','Valid original asset/product/observation binding for every frame','Selection overlaps each original frame'),
        P('light-curve','unavailable','Playback is not a quantitative light curve.','Matched bandpasses, apertures, PSFs and propagated aligned uncertainties'),
        P('difference-map','unavailable','No validated image subtraction is installed.','Matched PSFs/backgrounds and registered propagated variance'),
        P('variability','unavailable','No significance-tested variability algorithm is installed.','Comparable light curve and systematic-error model'),
        P('periodicity','unavailable','No period estimator is installed.','Sufficient timestamped samples, window function and false-alarm model'),
        P('motion','unavailable','Fixed-sky playback does not measure motion.','Validated image centroids, cross-epoch associations and astrometric uncertainties'),
        P('transients','unavailable','No transient detector is installed.','Validated subtraction, quality vetoes and significance calibration')]),
    'object': dict(id='object',label='Object',description='Filter an explicitly supplied catalog without inventing detections.',products=[
        P('supplied-catalog','available','Selection filter at supplied reference epoch; no remote query or image detection.','parameters.catalog with source, reference_epoch and at most 1000 typed rows'),
        P('crossmatch','unavailable','No probabilistic association is installed.','Two catalogs, epochs, covariance and association policy'),
        P('classification','unavailable','No classifier is installed; supplied object_type remains attributed catalog metadata.','Validated features/model and uncertainty calibration'),
        P('source-detection','unavailable','SOURCE flags mark catalog membership; they are not a detection catalog.',ASSET,'Validated completeness, deblending and false-positive model'),
        P('distance','unavailable','No distance inference from surface brightness is possible.','Independent parallax/redshift and validated distance model'),
        P('physical-properties','unavailable','Mass, luminosity, radius and age are not inferred.','Distance, calibrated SED, extinction and validated physical model')]),
    'statistical': dict(id='statistical',label='Statistical',description='Statistics over selected science-usable original pixels.',products=[
        P('histogram','available','Native pixel-center selected IMAGE histogram and descriptive moments.',ASSET,REGION,SCIENCE),
        P('summary','available','Minimum, maximum, mean, median, standard deviation and counts; no inferential significance.',ASSET,REGION,SCIENCE),
        P('correlation','unavailable','No spatial or cross-band correlation estimator is installed.',ASSET,'Pair selection, masking and covariance estimator'),
        P('power-spectrum','unavailable','No mask/window-corrected power spectrum is installed.',ASSET,'Window, beam and noise transfer functions'),
        P('distribution-fit','unavailable','No distribution fitting or goodness-of-fit implementation is installed.','Explicit likelihood, sample selection and error model'),
        P('significance','unavailable','Descriptive statistics do not establish a detection significance.','Validated null hypothesis, trials correction and systematic errors')]),
    'three-dimensional': dict(id='three-dimensional',label='Three-dimensional',description='Local spectral cubes, scientific surfaces and measured-result geometry; not physical depth.',products=[
        P('local-cube-3d','experimental','Shared local-cube scientific 3D engine: surfaces, sampled clouds, ray-marched volumes, isosurfaces, waterfall and continuum objective.','A local cube covering the selection','Coverage/FLAGS selection; cube variance unavailable'),
        P('brightness-surface','available','Display-grid height is calibrated brightness, never distance or physical depth.',ASSET,REGION,SCIENCE),
        P('distance-volume','unavailable','A 2D exposure has no physical depth axis.','Independent distances and uncertainties'),
        P('spectral-cube','unavailable','One exposure is not a registered spectral cube.','Multiple spatially registered calibrated spectral samples'),
        P('tomography','unavailable','No physical reconstruction is installed.','Validated 3D forward model and independent constraints'),
        P('velocity-field','unavailable','Brightness height is not velocity.','Resolved Doppler features and validated wavelength calibration'),
        P('point-cloud','unavailable','No physical 3D point cloud is inferred.','Independent object distances and ICRS positions')]),
    'discovery': dict(id='discovery',label='Discovery',description='Explicit boundary for unimplemented discovery algorithms.',products=[
        P('native-peaks','available','Variance-aware selected-region native peaks; exploratory only.',ASSET,REGION),
        P('anomaly','unavailable','No validated anomaly scorer is installed.',ASSET,'Validated algorithm, baseline and false-positive calibration'),
        P('clustering','unavailable','No scientific clustering implementation is installed.','Defined features, metric, selection function and validation'),
        P('similarity','unavailable','No embedding or similarity index is installed.','Validated representation and reference population'),
        P('candidate-ranking','unavailable','No scientifically calibrated candidate ranker is installed.','Real candidates and documented ranking/uncertainty model'),
        P('ai-inference','unavailable','No AI/model runner is invoked or fabricated.','Separately installed and scientifically validated algorithm'),
        P('archive-search','unavailable','This executor never performs network discovery.','Use the existing explicit archive search API outside lens execution')]),
    'quality': dict(id='quality',label='Quality',description='Original flags and reported variance, not learned artifact claims.',products=[
        P('quality-map','available','Fraction of selected pixels excluded by the scientific pixel policy.',ASSET,REGION),
        P('flags','available','Regional raw flag-bit counts and policy exclusions; SOURCE is not a detection.',ASSET,REGION),
        P('variance-map','available','Mean reported variance of selected science-usable original pixels; not variance of the mean.',ASSET,REGION,SCIENCE),
        P('uncertainty-map','available','Independent-pixel uncertainty of a selected block mean.',ASSET,REGION,SCIENCE),
        P('astrometry','available','Original FINAST classification, not a numerical-WCS proof of absolute accuracy.',ASSET,REGION),
        P('artifact-classification','unavailable','Flags are retained; no new artifact classifier is installed.',ASSET,'Validated artifact inference and confidence calibration'),
        P('empirical-noise','unavailable','Reported VARIANCE is not an empirical sky-noise estimate.',ASSET,'Validated background selection and covariance estimator'),
        P('aligned-uncertainty','unavailable','Existing aligned sequence products omit propagated variance/covariance.','Validated interpolation-weight covariance propagation'),
        P('completeness','unavailable','No completeness or purity estimate is inferred.','Validated injection/recovery or reference-population analysis')]),
}

# Each representation has an individual machine-readable row even where it
# shares an implementation/prerequisite set with a grouped card product.
REPRESENTATIONS = {
    ('spatial','brightness-map'): ['brightness-heatmap','native-brightness-map'],
    ('spatial','uncertainty-map'): ['block-mean-uncertainty','native-reported-sigma'],
    ('spatial','snr-map'): ['block-mean-snr','native-pixel-snr'],
    ('spatial','aperture-photometry'): ['aperture-flux','aperture-flux-error','aperture-snr','annulus-background'],
    ('spatial','segmentation'): ['source-segmentation','deblending','morphology','size','ellipticity','position-angle'],
    ('spectral','target-estimate'): ['target-wavelength-estimate','target-bandwidth-estimate'],
    ('spectral','spectral-lines'): ['line-identification','line-flux','equivalent-width','line-ratio'],
    ('object','supplied-catalog'): ['catalog-overlay','supplied-catalog-count','supplied-object-types'],
    ('object','physical-properties'): ['mass','luminosity','radius','age','temperature','extinction','metallicity','density'],
    ('temporal','sequence'): ['chronological-playback','original-frame-binding'],
    ('temporal','motion'): ['proper-motion','trajectory','measured-velocity'],
    ('statistical','summary'): ['minimum','maximum','mean','median','population-standard-deviation','selected-pixel-count'],
    ('statistical','histogram'): ['brightness-histogram'],
    ('three-dimensional','brightness-surface'): ['brightness-height-surface'],
    ('quality','quality-map'): ['invalid-fraction-map','selection-coverage-counts'],
    ('quality','flags'): ['raw-flag-bit-counts','scientific-exclusion-counts'],
    ('quality','variance-map'): ['mean-reported-variance-map','native-variance-map'],
    ('quality','uncertainty-map'): ['block-mean-uncertainty','native-reported-sigma'],
}


def capabilities():
    matrix = [dict(lens_id=lens['id'], representation_id=representation, product_id=product['id'],
                   status=product['status'], reason=product['reason'], prerequisites=list(product['prerequisites']))
              for lens in LENSES.values() for product in lens['products']
              for representation in REPRESENTATIONS.get((lens['id'],product['id']),[product['id']])]
    return {'schema_version': VERSION, 'lenses': deepcopy(list(LENSES.values())),
            'coverage_matrix': matrix,
            'requested_representation_matrix': requested_coverage(),
            'execution': 'synchronous; existing local sources only; no retrieval or AI',
            'limits': {'native_pixels': MAX_NATIVE_PIXELS, 'grid_side': 32,
                       'polygon_vertices': 64, 'catalog_rows': 1000, 'histogram_bins': 256,
                       'sequence_total_native_pixels': 4 * MAX_NATIVE_PIXELS,
                       'concurrent_executions': 2, 'selection_extent_deg': 5},
            'result_category_labels': {'observed':'Observed','calculated':'Calculated',
                                       'model-derived':'Model-derived','externally-sourced':'Externally sourced',
                                       'ml-predicted':'ML-predicted','unknown':'Unknown'},
            'selection_semantics': 'ICRS anchored; circle great-circle radius; rectangle gnomonic width/height; '
                                   'polygon simple great-circle edges. Membership uses native pixel centers; '
                                   'outside-asset coverage is never extrapolated.',
            'request_schema': LensRequest.model_json_schema(),
            'result_schema': AnalysisResult.model_json_schema()}

# Explicit accounting for the requested research matrix. These names are not
# automatic scientific detections. A representation without a verified provider
# remains unavailable even when a generic plot widget could draw it.
REQUESTED_REPRESENTATIONS = {
 'spatial': ['individual-wavelength-image','false-colour-composite','water-ice-map','carbon-dioxide-map','carbon-monoxide-map','molecular-abundance-contours','molecular-column-density','chemical-ratio-map','extinction-map','circumstellar-dust-markers','galaxy-clustering-density','galaxy-overdensity-contours','cosmological-density-map','variability-map','proper-motion-vector-map','observation-footprints','measurement-uncertainty-map','detection-significance-map','background-radiation-map'],
 'spectral': ['source-spectrum','regional-spectrum','overlaid-source-spectra','molecular-absorption-spectrum','molecular-emission-spectrum','molecular-identification','stellar-atmosphere-temperature-fit','extinction-curve','infrared-excess-spectrum','unresolved-binary-spectrum-fit','brown-dwarf-chemistry','atmospheric-anomaly-residual','planetary-reflectance','hydrated-mineral-absorption','disk-chemistry','dust-excess-residual','galaxy-redshift-template-fit','detector-response-curve'],
 'temporal': ['position-versus-time','direction-vector-map','proper-motion-parallax-fit','moving-source-epoch-viewer','orbit-fit-residual','stellar-light-curve','agn-light-curve','brown-dwarf-light-curve','phase-folded-light-curve','periodogram','transient-difference-image','quantitative-variability-statistics','cometary-emission-movie','chronological-observation-animation','astrometric-uncertainty-ellipse'],
 'object': ['object-identification','ranked-stellar-classification','brown-dwarf-lty-probabilities','galaxy-classification','effective-temperature-fit','luminosity-estimate','stellar-mass-radius','stellar-age-posterior','distance-posterior','metallicity','atmospheric-temperature-posterior','planetary-temperature-constraint','diameter-estimate','orbital-distance-posterior','exoplanet-host-properties','stellar-transit-radius-model','galaxy-redshift-posterior','galaxy-mass-sfr','catalogue-associations','measurement-provenance'],
 'statistical': ['hertzsprung-russell-diagram','isochrone-diagram','brown-dwarf-colour-colour','colour-magnitude-diagram','abundance-scatter','ice-dust-correlation','molecular-ratio-scatter','galaxy-mass-histogram','population-sfr-scatter','redshift-physical-properties','extinction-distribution','two-point-correlation','matter-power-spectrum','angular-power-spectrum','cosmological-model-comparison','cosmological-corner-plot','calibration-residual-histogram','matched-archive-plot-builder'],
 'three-dimensional': ['planetary-orbit','candidate-orbital-constraints','galaxy-spatial-distribution','galaxy-environments','large-scale-structure','volumetric-galaxy-density','filaments-voids','spatial-distance-uncertainty'],
 'discovery': ['unknown-moving-source-candidates','planet-x-evidence','unidentified-brown-dwarfs','unresolved-binary-candidates','circumstellar-dust-candidates','follow-up-ranking','transient-candidates','agn-candidate-distribution','galaxy-cluster-overdensity','spectral-anomaly-embedding','population-clustering','calibrated-classification-probabilities','novelty-scores'],
 'quality': ['psf-interactive-map','optical-radial-profile','defective-pixel-mask','noise-histogram','reported-variance-map','detector-sensitivity','calibration-residual-scatter','photometric-residual-histogram','observation-frequency','revisit-pattern','artifact-mask-statistics','reconstruction-comparison','model-precision-recall','inference-latency','resource-utilization','rate-distortion','data-quality-tables'],
}
RESEARCH_PREREQUISITES = {
 'spatial':'Calibrated spectral coverage, validated chemical/continuum/selection model, response-aware units and uncertainty propagation; brightness alone is not abundance or detection significance.',
 'spectral':'Multi-wavelength calibrated CWAVE/CBAND measurements with response templates, continuum/error models and sufficient resolution; an interpolated WCS-WAVE estimate is insufficient.',
 'temporal':'Scientifically compatible epochs, PSF/background-aware comparisons, uncertainty propagation and validated source association/model identifiability; playback alone measures no motion or variability.',
 'object':'Verified catalog association and uncertainties, suitable distance/stellar/planetary/galaxy physical models and observations; no missing property is invented.',
 'statistical':'Matched calibrated population measurements, selection functions/survey masks, redshifts where needed and justified uncertainty/cosmological models; arbitrary rows do not constrain cosmology.',
 'three-dimensional':'Validated distance/orbit posterior, reference frame and uncertainty-aware physical reconstruction; 2D brightness is not distance.',
 'discovery':'Validated algorithms with independent real-data evaluation, calibrated scores and supporting original observations; no trained model or discovery evidence is fabricated.',
 'quality':'Applicable original calibration product, validated estimator and measured benchmark; public flags are not spacecraft telemetry or model performance.',
}
# External interfaces are identified separately from numerical lens execution.
VERIFIED_RESEARCH_INTERFACES = {
 ('spatial','observation-footprints'): ('available','SIA footprints in Aladin; archive geometry, not per-pixel good coverage.'),
 ('spatial','measurement-uncertainty-map'): ('available','Quality/spatial lens independent-pixel block-mean uncertainty; covariance/systematics omitted.'),
 ('temporal','chronological-observation-animation'): ('available','Existing calibrated common-WCS sequence service and global timeline; requires a ready comparable sequence.'),
 ('object','object-identification'): ('available','Authoritative SIMBAD resolution, reference epoch and supplied-catalog filtering; no independent classification.'),
 ('object','measurement-provenance'): ('available','Original asset, policy, uncertainty and method records; unsupported quantities remain absent.'),
 ('statistical','matched-archive-plot-builder'): ('available','Descriptive matched-row plot with reported units; no physical or uncertainty-aware inference.'),
 ('quality','defective-pixel-mask'): ('available','Original FLAGS-derived scientific exclusion mask; SOURCE is informational, FULLSAMPLE is excluded.'),
 ('quality','reported-variance-map'): ('available','Original variance and masked reduced display maps; not aligned covariance.'),
 ('quality','artifact-mask-statistics'): ('available','Original quality bits and selected-pixel exclusion counts; no learned artifact identification.'),
 ('quality','data-quality-tables'): ('available','Original FLAGS/VARIANCE and independent FINAST suitability status.'),
 ('spatial','individual-wavelength-image'): ('experimental','102-channel official HiPS visualization browser; low dynamic range, no scientific pixel measurements; blank channels masked.'),
 ('temporal','transient-difference-image'): ('experimental','Prepared-sequence subtraction is exploratory arithmetic; no PSF matching, propagated significance or transient detection.'),
}
def requested_coverage():
    return [
        {'lens_id':family,'representation_id':name,
         'status':VERIFIED_RESEARCH_INTERFACES.get((family,name),('unavailable',None))[0],
         'implementation':VERIFIED_RESEARCH_INTERFACES.get((family,name),(None,None))[1],
         'prerequisites':RESEARCH_PREREQUISITES[family]}
        for family,names in REQUESTED_REPRESENTATIONS.items() for name in names]
