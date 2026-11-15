"""Read-only scientific lens router. Mount with app.include_router(router).

Only existing asset/sequence identifiers are accepted. No file/URL resolution,
network retrieval, scientific image rewriting, background jobs or AI occurs.
Heatmaps are reduced display products on original pixel blocks, with ICRS
corners for a viewer's world2pix projection and an explicit selection clip.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import math
import re
import threading
from uuid import uuid4

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from archive_client import DataError
from fits_processor import POLICY_VERSION, astrometric_quality
from lens_registry import (AnalysisResult, LENSES, LensRequest, MAX_NATIVE_PIXELS,
                           VERSION, capabilities as capability_manifest)
import observation_service as observations
import scientific_inspector as inspector
import scientific_measurements as science
import sequence_service

SLOTS = threading.BoundedSemaphore(2)
MAX_SEQUENCE_NATIVE_PIXELS = 4 * MAX_NATIVE_PIXELS


def _json_safe(value):
    """Finite JSON for inspector metadata and validation errors, including NaN inputs."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class LensRoute(APIRoute):
    """Self-contained error handling, also when mounted outside main.app."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content={'detail': _json_safe(exc.errors())})
            except DataError as exc:
                return JSONResponse(status_code=exc.status, content={'detail': str(exc)})
        return handler


router = APIRouter(prefix='/api/lenses', tags=['scientific lenses'], route_class=LensRoute)


@router.get('/capabilities')
def capabilities():
    return capability_manifest()


def _celestial(header):
    # Match the inspector's protection against malformed alternate spectral WCS.
    clean = header.copy()
    for key in list(clean):
        if re.fullmatch(r'(WCSAXES|WCSNAME|CTYPE\d+|CNAME\d+|CUNIT\d+|CRPIX\d+|CRVAL\d+|CDELT\d+|PC\d+_\d+|CD\d+_\d+|PS\d+_\d+|PV\d+_\d+|LONPOLE|LATPOLE)W', key):
            del clean[key]
    try:
        wcs = WCS(clean).celestial
        if not wcs.has_celestial:
            raise ValueError('Missing celestial axes')
        return wcs
    except Exception as exc:
        raise DataError('Original celestial WCS is unavailable; inspect or retrieve a valid asset.', 422) from exc


def _preflight(path):
    """Bound dimensions before scientific_measurements.source materializes arrays."""
    with fits.open(path, mode='readonly', memmap=None, lazy_load_hdus=True) as hdus:
        matches = [h for h in hdus if h.name.upper() == 'IMAGE']
        if len(matches) != 1:
            raise DataError('One original IMAGE extension is required.', 422)
        if isinstance(matches[0], (fits.BinTableHDU, fits.TableHDU)):
            raise DataError('IMAGE must be an image plane, not a table.',422)
        shape = matches[0].shape
        if len(shape) != 2 or min(shape) < 1 or math.prod(shape) > MAX_NATIVE_PIXELS:
            raise DataError(f'Lens execution supports at most {MAX_NATIVE_PIXELS} native pixels; retrieve a smaller cutout explicitly.', 422)
        for name in ('FLAGS','VARIANCE'):
            companions = [h for h in hdus if h.name.upper() == name]
            if (len(companions) != 1 or isinstance(companions[0], (fits.BinTableHDU, fits.TableHDU))
                    or companions[0].shape != shape):
                raise DataError('Matching unique IMAGE, FLAGS and VARIANCE dimensions are required before array access.',422)
        return tuple(shape), _celestial(matches[0].header)


def _selection_mask(selection, wcs, shape):
    selected = np.zeros(shape, dtype=bool)
    # Bound transform working memory without a full sky-coordinate mesh.
    rows = max(1, 32768 // shape[1])
    for y0 in range(0, shape[0], rows):
        yy, xx = np.mgrid[y0:min(y0+rows, shape[0]), :shape[1]]
        world = wcs.pixel_to_world(xx, yy).icrs
        finite = np.isfinite(world.ra.deg) & np.isfinite(world.dec.deg)
        # SkyCoord can represent nonfinite RA/Dec; outside coverage is excluded.
        selected[y0:y0+rows] = finite & selection.contains(world.ra.deg, world.dec.deg)
    return selected


def _descriptor(req, kind, product_id, scope, **fields):
    return dict(kind=kind, product_id=product_id, scope=scope,
                selection=req.selection.model_dump(exclude_none=True), **fields)


def _native_descriptor(req, product, shape, bounds, unit):
    base = f'/api/observations/{req.asset_id}'
    sources = {hdu: f'{base}/array?hdu={hdu}&count=1000'
               for hdu in ('IMAGE', 'VARIANCE', 'FLAGS')}
    expression = {'brightness-map': 'IMAGE converted to MJy/sr',
                  'uncertainty-map': 'sqrt(VARIANCE converted to (MJy/sr)^2)',
                  'snr-map': 'IMAGE/sqrt(VARIANCE); null where VARIANCE <= 0',
                  'variance-map': 'VARIANCE converted to (MJy/sr)^2',
                  'quality-map': 'scientific invalid mask (1 invalid, 0 usable)'}[product]
    return _descriptor(req, 'native_map', product, 'native pixels selected by ICRS pixel-center predicate',
                       lazy=True, array_urls=sources, original_fits_url=base+'/fits',
                       inventory_url=base+'/inspect', scientific_invalid_mask_url=base+'/quality-mask',
                       original_shape=list(shape), pixel_origin=0, order='C', bounds=bounds,
                       unit=unit, expression=expression, quality_policy=POLICY_VERSION,
                       source_arrays_are_unfiltered=True,
                       selection_filter={'required': True, 'rule': 'Use original celestial WCS pixel centers and the exact supplied ICRS region; exclude outside selection and policy-invalid pixels. Quality map retains invalid selected pixels.',
                                         'geometry': 'circle: great-circle; rectangle: anchor gnomonic; polygon: great-circle edges'},
                       warning='These original array links are not cropped or masked maps; apply the selection predicate before rendering or measuring.')


def _grid(req, image, variance, bad, selected, wcs):
    ys, xs = np.nonzero(selected)
    x0, x1 = int(xs.min()), int(xs.max())+1
    y0, y1 = int(ys.min()), int(ys.max())+1
    maximum = req.parameters.maximum
    xe = np.linspace(x0, x1, min(maximum, x1-x0)+1, dtype=int)
    ye = np.linspace(y0, y1, min(maximum, y1-y0)+1, dtype=int)
    quantities = {name: [] for name in ('brightness-map','uncertainty-map','snr-map','quality-map','variance-map')}
    corners, selected_counts, usable_counts, selected_fractions = [], [], [], []
    for ya, yb in zip(ye[:-1], ye[1:]):
        row_values = {name: [] for name in quantities}
        row_corners, counts, good_counts, fractions = [], [], [], []
        for xa, xb in zip(xe[:-1], xe[1:]):
            region = selected[ya:yb, xa:xb]
            valid = region & ~bad[ya:yb, xa:xb]
            n, good = int(region.sum()), int(valid.sum())
            vals = image[ya:yb, xa:xb][valid]
            variances = variance[ya:yb, xa:xb][valid]
            mean = float(vals.mean()) if good else None
            error = float(np.sqrt(np.sum(variances))/good) if good else None
            row_values['brightness-map'].append(mean)
            row_values['uncertainty-map'].append(error)
            row_values['snr-map'].append(mean/error if error is not None and error > 0 else None)
            row_values['quality-map'].append((n-good)/n if n else None)
            row_values['variance-map'].append(float(variances.mean()) if good else None)
            world = wcs.pixel_to_world([xa-.5,xb-.5,xb-.5,xa-.5], [ya-.5,ya-.5,yb-.5,yb-.5]).icrs
            coords = np.column_stack((world.ra.deg, world.dec.deg))
            if not np.isfinite(coords).all():
                raise DataError('Display cell corners lack finite ICRS coverage; use the original inspector.',422)
            row_corners.append(coords.tolist())
            counts.append(n); good_counts.append(good); fractions.append(n / region.size)
        for name in quantities:
            quantities[name].append(row_values[name])
        corners.append(row_corners); selected_counts.append(counts)
        usable_counts.append(good_counts); selected_fractions.append(fractions)
    return quantities, dict(corners=corners, selected_counts=selected_counts, usable_counts=usable_counts,
                           selected_fractions=selected_fractions, original_pixel_x_edges=xe.tolist(),
                           original_pixel_y_edges=ye.tolist(),
                           bounds={'x_start':x0,'x_stop':x1,'y_start':y0,'y_stop':y1})


def _set(products, id, status, reason):
    for product in products:
        if product['id'] == id:
            product.update(status=status, reason=reason)


def _photometry(req, wcs, shape, products, values, warnings):
    p = req.parameters
    anchor = wcs.world_to_pixel(SkyCoord(req.selection.ra, req.selection.dec, unit='deg', frame='icrs'))
    x, y = map(float, anchor)
    outer = p.annulus_outer_px
    # Conservative full-pixel rectangular support: no claim of containment based
    # on the center alone, nor an angular aperture silently replaced by pixels.
    if not np.isfinite([x,y]).all():
        _set(products,'aperture-photometry','unavailable','Anchor has no finite IMAGE position.')
        return
    x0, x1 = math.floor(x-outer-1), math.ceil(x+outer+1)
    y0, y1 = math.floor(y-outer-1), math.ceil(y+outer+1)
    if x0 < 0 or y0 < 0 or x1 >= shape[1] or y1 >= shape[0]:
        _set(products,'aperture-photometry','unavailable','Full aperture/background support is outside IMAGE; choose an interior anchor or smaller radii.')
        return
    yy,xx = np.mgrid[y0:y1+2,x0:x1+2]
    world = wcs.pixel_to_world(xx-.5,yy-.5).icrs
    if not np.all(req.selection.contains(world.ra.deg,world.dec.deg)):
        _set(products,'aperture-photometry','unavailable','Selection does not contain the conservative full-pixel aperture and annulus support; enlarge selection or reduce pixel radii.')
        return
    try:
        result = science.photometry(req.asset_id,ra=req.selection.ra,dec=req.selection.dec,
                                   radius=p.aperture_radius_px,annulus_inner=p.annulus_inner_px,
                                   annulus_outer=p.annulus_outer_px,subtract_zodi=False)
    except DataError as exc:
        if exc.status != 422:
            raise
        _set(products,'aperture-photometry','unavailable',str(exc))
        return
    values['aperture-photometry'] = result
    warnings.extend(result['warnings'])
    _set(products,'aperture-photometry','available','Target circular pixel aperture and annulus conservatively contained by ICRS selection. Not a region map or an angular-aperture integral.')


def _asset_products(req, path, record, products, values, descriptors, provenance, warnings):
    shape,wcs = _preflight(path)
    requested = {p['id'] for p in products}
    if req.lens_id == 'spectral':
        if 'target-estimate' not in requested:
            return
        if not bool(req.selection.contains(req.selection.ra, req.selection.dec)):
            _set(products,'target-estimate','unavailable','ICRS anchor must lie inside the selection for a target estimate.')
            return
        try:
            result = inspector.pixel(path,ra=req.selection.ra,dec=req.selection.dec)
        except DataError as exc:
            if exc.status != 422:
                raise
            _set(products,'target-estimate','unavailable',str(exc))
            return
        spectral = result['spectral']
        if spectral['status'] != 'available':
            _set(products,'target-estimate','unavailable',spectral.get('reason','WCS-WAVE unavailable.'))
            return
        values['target-estimate'] = {'position': result['celestial'], 'pixel':result['pixel'],
                                     'spectral': spectral,'science_grade_spectral_calibration':False}
        descriptors.append(_descriptor(req,'spectrum_point','target-estimate','ICRS target anchor only; not a regional spectrum',data=values['target-estimate']))
        _set(products,'target-estimate','experimental','Interpolated target WCS-WAVE visualization estimate; use genuine CWAVE/CBAND for science.')
        return
    if req.lens_id not in ('spatial','statistical','quality','three-dimensional'):
        return
    with science.source(req.asset_id) as (_,_,hdus,image,variance,bad):
        bad = bad | ~np.isfinite(image) | ~np.isfinite(variance) | (variance < 0)
        # Protect reductions/histogram arithmetic from overflow after calibrated
        # unit conversion. Reject, rather than null out an uncomputed result.
        finite_image, finite_variance = image[~bad], variance[~bad]
        safe_limit = math.sqrt(np.finfo(float).max / MAX_NATIVE_PIXELS) / 4
        if (np.any(np.abs(finite_image) > safe_limit) or np.any(finite_variance > safe_limit**2)):
            raise DataError('Calibrated samples exceed safe numeric reduction bounds; inspect original values.',422)
        selected = _selection_mask(req.selection,wcs,shape)
        n = int(selected.sum())
        good = selected & ~bad
        count = int(good.sum())
        provenance['selection_coverage'] = {'selected_native_pixels':n,'usable_selected_pixels':count,
            'asset_shape':list(shape),'rule':'original pixel centers; no fractional boundary pixels',
            'full_region_coverage_certified':False,'outside_asset':'not sampled or extrapolated'}
        if not n:
            for product in products:
                if product['status'] != 'unavailable' and product['id'] not in values:
                    product.update(status='unavailable',reason='Selection contains no original IMAGE pixel centers; choose an overlapping region or retrieve a larger cutout explicitly.')
            return
        warnings.append('Selection statistics cover only original pixel centers in the asset intersection. Full sky-region coverage is not certified; no extrapolation.')
        if req.lens_id == 'statistical' and requested & {'histogram','summary'}:
            if count:
                samples = image[good]
                summary = {'selected_pixels':n,'usable_pixels':count,'excluded_pixels':n-count,
                           'minimum':float(samples.min()),'maximum':float(samples.max()),
                           'mean':float(samples.mean()),'median':float(np.median(samples)),
                           'std':float(samples.std()),'std_definition':'population standard deviation, ddof=0; not error on mean'}
                if 'summary' in requested:
                    values['summary'] = summary
                if 'histogram' in requested:
                    try:
                        hist,edges = np.histogram(samples,bins=req.parameters.bins)
                    except (ValueError, OverflowError, FloatingPointError):
                        _set(products,'histogram','unavailable','Selected numeric range cannot resolve finite histogram bins; inspect original values or request summary statistics.')
                    else:
                        values['histogram'] = {'counts':hist.tolist(),'edges':edges.tolist(),**summary}
                        descriptors.append(_descriptor(req,'histogram','histogram','science-usable selected native pixel centers',
                                                       unit='MJy/sr',data=values['histogram']))
            else:
                for name in ('summary','histogram'):
                    _set(products,name,'unavailable','No science-usable selected pixels; inspect quality flags.')
        if req.lens_id == 'quality':
            if 'flags' in requested:
                flags = hdus['FLAGS'].data[selected].astype(np.uint64)
                bit_counts = {str(bit):int(np.count_nonzero(flags & (np.uint64(1) << np.uint64(bit)))) for bit in range(64)}
                definitions = {str(int(card.value)):card.keyword[3:] for card in hdus['FLAGS'].header.cards
                               if card.keyword.startswith('MP_') and isinstance(card.value,int) and not isinstance(card.value,bool) and 0 <= card.value < 64}
                values['flags'] = {'selected_pixels':n,'usable_pixels':count,'excluded_pixels':n-count,
                                  'bit_counts':bit_counts,'header_bit_definitions':definitions,
                                  'source_flag_interpretation':'Catalog membership; neither detection count nor quality approval',
                                  'quality_policy':POLICY_VERSION}
            if 'astrometry' in requested:
                values['astrometry'] = astrometric_quality(hdus['IMAGE'].header.get('FINAST',hdus[0].header.get('FINAST')))
        map_products = requested & {'brightness-map','uncertainty-map','snr-map','quality-map','variance-map','brightness-surface'}
        if map_products:
            grids, geometry = _grid(req,image,variance,bad,selected,wcs)
            for name in sorted(map_products):
                if not count and name != 'quality-map':
                    _set(products,name,'unavailable','No science-usable selected pixels; inspect quality flags.')
                    continue
                source_name = 'brightness-map' if name == 'brightness-surface' else name
                unit = {'brightness-map':'MJy/sr','uncertainty-map':'MJy/sr','snr-map':'1',
                        'quality-map':'1','variance-map':'(MJy/sr)^2'}[source_name]
                reduction = {'brightness-map':'Arithmetic mean of science-usable selected original IMAGE pixels; no background subtraction',
                             'uncertainty-map':'sqrt(sum(original VARIANCE))/usable_count; independent pixels; uncertainty of block mean',
                             'snr-map':'Selected block mean divided by independent-pixel error of that mean; null for zero error; no background subtraction',
                             'variance-map':'Arithmetic mean of selected usable original VARIANCE; not variance of block mean',
                             'quality-map':'Excluded selected pixels / selected pixels; null if no selected pixels'}[source_name]
                descriptors.append(_descriptor(req,'brightness_surface' if name == 'brightness-surface' else 'heatmap',
                    name,'selected original pixel centers in reduced display grid',values=grids[source_name],unit=unit,
                    **geometry,reduction=reduction,reduced_display_grid=True,
                    selection_clip_required=True, corner_order='bottom-left, bottom-right, top-right, top-left in original pixel axes; ICRS degrees',
                    display_rule='Project ICRS corners with viewer world2pix; clip cells to the supplied selection overlay. Null cells are transparent. Never interpolate across unselected or invalid pixels.',
                    height_meaning='surface brightness, not physical distance' if name == 'brightness-surface' else None))
                native = _native_descriptor(req,source_name,shape,geometry['bounds'],unit)
                native['product_id'] = name
                descriptors.append(native)
                values[name] = {'selected_pixels':n,'usable_pixels':count,'grid_shape':[len(grids[source_name]),len(grids[source_name][0])],
                                'measurement_scope':'selected original pixels reduced for display; no full-resolution map in JSON'}
    # Release scientific_measurements' slot before its photometry wrapper takes one.
    if req.lens_id == 'spatial' and 'aperture-photometry' in requested:
        _photometry(req,wcs,shape,products,values,warnings)


def _catalog(req, products, values, descriptors, provenance):
    if not any(p['id']=='supplied-catalog' for p in products):
        return
    catalog = req.parameters.catalog
    if catalog is None:
        _set(products,'supplied-catalog','unavailable','Supply parameters.catalog with source, reference_epoch and typed rows; no catalog is queried automatically.')
        return
    rows = [row.model_dump(exclude_none=True) for row in catalog.rows
            if bool(req.selection.contains(row.ra,row.dec))]
    result = {'rows':rows,'selected_count':len(rows),'supplied_count':len(catalog.rows),
              'source':catalog.source,'reference_epoch':catalog.reference_epoch,
              'completeness':'unknown; only supplied rows filtered at their stated epoch',
              'image_detections':False,'motion_propagated':False}
    values['supplied-catalog'] = result
    provenance['catalog'] = {'source':catalog.source,'reference_epoch':catalog.reference_epoch,'origin':'caller supplied; not independently verified'}
    descriptors.append(_descriptor(req,'catalog','supplied-catalog','supplied catalog positions inside ICRS selection',data=result))


def _sequence(req, products, values, descriptors, provenance, warnings):
    if not any(p['id']=='sequence' for p in products):
        return
    if req.sequence_id is None:
        _set(products,'sequence','unavailable','Supply an existing ready sequence_id; this endpoint never prepares or retrieves a sequence.')
        return
    state = sequence_service.status(req.sequence_id)
    frames = state.get('frames',[])
    if state.get('status') != 'ready' or not 2 <= len(frames) <= 100:
        _set(products,'sequence','unavailable','Sequence must already be ready with 2–100 prepared frames.')
        return
    if req.asset_id and req.asset_id not in {f.get('asset_id') for f in frames}:
        raise DataError('Supplied asset_id is not bound to a frame in the sequence.',409)
    bindings = []
    total_pixels = 0
    for frame in frames:
        asset_id = frame.get('asset_id')
        frame_id = frame.get('id')
        if not isinstance(asset_id,str) or not re.fullmatch('[a-f0-9]{32}',asset_id) or not isinstance(frame_id,str) or not re.fullmatch('[a-f0-9]{32}',frame_id):
            raise DataError('Prepared frame lacks valid asset/frame identifiers.',409)
        with observations.asset_lease(asset_id) as path:
            record = observations.get_observation(asset_id)
            if (record.get('product_id') != frame.get('product_id') or
                    record.get('observation_id') != frame.get('observation_id') or
                    not frame.get('product_id') or not frame.get('observation_id')):
                raise DataError('Sequence and original asset identities disagree.',409)
            shape,wcs = _preflight(path)
            total_pixels += math.prod(shape)
            if total_pixels > MAX_SEQUENCE_NATIVE_PIXELS:
                raise DataError('Sequence selection validation exceeds 4194304 native pixels; use a smaller prepared sequence or cutouts.',422)
            if not _selection_mask(req.selection,wcs,shape).any():
                _set(products,'sequence','unavailable','Selection has no native pixel-center overlap in at least one sequence frame; choose a common overlapping region.')
                return
        mjd = frame.get('mjd')
        if isinstance(mjd,bool) or not isinstance(mjd,(int,float)) or not math.isfinite(mjd):
            _set(products,'sequence','unavailable','Sequence frame lacks a finite original observation time; prepare a valid sequence.')
            return
        base = f'/api/sequences/{req.sequence_id}/frames/{frame_id}'
        bindings.append({'frame_id':frame_id,'asset_id':asset_id,'product_id':frame['product_id'],
                         'observation_id':frame['observation_id'],'mjd':mjd,
                         'original_sha256':record.get('sha256'),
                         'original_source_url':record.get('source_url'),
                         'observation_time':frame.get('observation_time'),
                         'quality':frame.get('quality'), 'spectral':frame.get('spectral'),
                         'source_url':base+'/source','aligned_fits_url':base+'?kind=fits',
                         'preview_url':base+'?kind=preview','mask_url':base+'?kind=mask',
                         'original_fits_url':f'/api/observations/{asset_id}/fits'})
    bindings.sort(key=lambda f:f['mjd'])
    values['sequence'] = {'sequence_id':req.sequence_id,'frame_count':len(bindings),
                          'mode':state.get('request',{}).get('mode'),'variability_measured':False}
    descriptors.append(_descriptor(req,'sequence','sequence','existing aligned frames with selection overlay; not selection-cropped imagery',
                                   frames=bindings,common_wcs=state.get('common_wcs'),display=state.get('display'),
                                   selection_clip_required=True,source_images_are_unfiltered=True,
                                   aligned_variance_propagated=False))
    provenance['sequence_id'] = req.sequence_id
    warnings.extend(state.get('warnings',[]))
    warnings.append('Sequence display is not variability or motion inference. Target bandpass agreement does not certify the whole selected region; aligned variance/covariance is unavailable.')


def execute_lens(req: LensRequest) -> AnalysisResult:
    if not SLOTS.acquire(blocking=False):
        raise DataError('Lens execution capacity is occupied; retry later.',503)
    try:
        return _execute(req)
    finally:
        SLOTS.release()


def _execute(req):
    products = [dict(p) for p in LENSES[req.lens_id]['products']
                if req.parameters.products is None or p['id'] in req.parameters.products]
    values, descriptors, warnings = {}, [], []
    provenance = {'schema_version':VERSION,'quality_policy':POLICY_VERSION,
                  'executed_at':datetime.now(timezone.utc).isoformat(),
                  'selection':req.selection.model_dump(exclude_none=True),
                  'asset_id':req.asset_id,'sequence_id':req.sequence_id,
                  'original_data_modified':False,'retrieval_started':False,'inference_performed':False}
    if req.lens_id=='three-dimensional' and any(p['id']=='local-cube-3d' for p in products):
        from local_science import local_sky
        local=local_sky(req.selection.ra,req.selection.dec)
        if local['available']:
            values['local-cube-3d']={'cube_id':local['cube_id'],'source_url':local['source_url'],'selection':req.selection.model_dump(),'engine':'Shared interactive scientific 3D viewer in this research inspector','result_category':'observed cube values and explicitly calculated display/continuum products'}
            descriptors.append(_descriptor(req,'table','local-cube-3d','Actual local spectral cube; native WCS and wavelength, not physical depth',rows=[{'cube_id':local['cube_id'],'source_url':local['source_url'],'origin':local['origin']}]))
            warnings.append('Cube NHITS is coverage, not variance. Derived feature and objective products do not establish molecular identification or a physical 3D reconstruction.')
    with ExitStack() as stack:
        if req.asset_id and not (len(products)==1 and products[0]['id']=='local-cube-3d'):
            path = stack.enter_context(observations.asset_lease(req.asset_id))
            record = observations.get_observation(req.asset_id)
            provenance['asset'] = {k:record.get(k) for k in ('id','product_id','observation_id','collection','sha256','source_url','retrieved_url','retrieved_at','validation')}
            if req.lens_id=='discovery' and any(p['id']=='native-peaks' for p in products):
                from native_analysis import source_candidates
                found=source_candidates(req.asset_id,req.selection)
                values['native-peaks']=found
                descriptors.append(_descriptor(req,'table','native-peaks','Science-usable selected original pixels; not confirmed detections',rows=found['candidates'],columns=['id','ra','dec','nominal_pixel_snr','surface_brightness_mjy_sr','sigma_mjy_sr']))
                warnings.extend(found['limitations'])
            if req.lens_id not in ('object','temporal'):

                _asset_products(req,path,record,products,values,descriptors,provenance,warnings)
        elif req.lens_id not in ('object','temporal'):
            for product in products:
                if product['status'] != 'unavailable' and product['id'] not in values:
                    product.update(status='unavailable',reason='Supply an existing retrieved asset_id; no FITS is retrieved by lens execution.')
        if req.lens_id == 'object':
            _catalog(req,products,values,descriptors,provenance)
        if req.lens_id == 'temporal':
            _sequence(req,products,values,descriptors,provenance,warnings)
    # Only products with actual values/descriptors may retain executable status.
    for product in products:
        if product['status'] != 'unavailable' and product['id'] not in values:
            product.update(status='unavailable',reason='Required scientific inputs are unavailable; inspect product prerequisites.')
    completed = [p for p in products if p['status'] != 'unavailable']
    status = 'unavailable' if not completed else 'completed' if len(completed)==len(products) else 'partial'
    category = ('unavailable' if not completed else
                'catalog_supplied' if req.lens_id=='object' else
                'sequence_descriptor' if req.lens_id=='temporal' else
                'visualization_estimate' if req.lens_id in ('spectral','three-dimensional') else
                'calibrated_measurement')
    result_category = ('unknown' if not completed else
                       'externally-sourced' if req.lens_id == 'object' else
                       'observed' if req.lens_id == 'temporal' or
                       (req.lens_id == 'quality' and set(values) <= {'flags','astrometry'}) else
                       'calculated')
    if descriptors:
        descriptors.append(_descriptor(req,'selection_overlay','selection','exact ICRS selection boundary for viewer projection and clipping',
                                       geometry_semantics='Circle: small circle on sphere. Rectangle: anchor gnomonic bounds. Polygon: great-circle edges.',
                                       projection='Use the active sky viewer world2pix, including RA wrap.'))
    units = {key: ('Jy' if key=='aperture-photometry' else '1' if key in ('snr-map','quality-map','flags')
                   else '(MJy/sr)^2' if key=='variance-map' else 'MJy/sr')
             for key in values if key not in ('sequence','supplied-catalog','target-estimate','astrometry')}
    if 'local-cube-3d' in values:units['local-cube-3d']='Axis-specific; see typed binary column metadata'
    if 'target-estimate' in values:
        units['target-estimate'] = 'See original WCS-WAVE axis units in measurement_values'
    uncertainties = {'policy':'Reported original VARIANCE; independent-pixel propagation only.',
                     'omitted':['pixel covariance','calibration systematics','PSF mismatch','zodiacal-model uncertainty'],
                     'block_mean_sigma':'sqrt(sum of selected usable native variances)/usable_count',
                     'selection_boundary':'Native pixel-center inclusion; not fractional-area regional integration.',
                     'spectral_estimate':'Not science-grade CWAVE/CBAND; spectral uncertainty is unavailable.',
                     'catalog':'Only supplied uncertainties retained; no covariance or epoch propagation.',
                     'aligned_variance':'Unavailable; no aligned uncertainty propagation inferred.'}
    return AnalysisResult(**_json_safe(dict(analysis_id=uuid4().hex,lens_id=req.lens_id,
        selection=req.selection.model_dump(exclude_none=True),result_status=status,category=category,
        result_category=result_category,
        measurement_values=values,units=units,visualization_descriptors=descriptors,products=products,
        provenance=provenance,uncertainties=uncertainties,
        method='Original calibrated FITS on native WCS grid; exact spherical selection by pixel centers; bounded display reduction; explicit target-aperture scope. No background subtraction except the separately labeled aperture estimator.',
        warnings=list(dict.fromkeys(warnings)))))


@router.post('/execute', response_model=AnalysisResult)
def execute(req: LensRequest):
    return execute_lens(req)
