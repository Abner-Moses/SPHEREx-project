"""Lens contracts and scientific selections on isolated original-array fixtures.

Like test_measurements/test_inspector, these synthetic FITS are test inputs only;
production never creates substitute observations or inferred detections.
"""
from contextlib import contextmanager
import hashlib
import json
import math

import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from fastapi import FastAPI
from fastapi.testclient import TestClient

from archive_client import DataError
from fits_processor import quality_mask
import lens_api as lenses
from lens_registry import AnalysisResult, LENSES, Selection

ASSET = 'a'*32
SEQUENCE = 'b'*32
ASSET2 = 'c'*32


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(lenses.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def product(tmp_path, monkeypatch):
    size = 80
    yy,xx = np.mgrid[:size,:size]
    image = (2 + .1*xx + .2*yy).astype(np.float32)
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ['RA---TAN','DEC--TAN']
    wcs.wcs.crval = [359.999,85]
    wcs.wcs.crpix = [41,41]
    wcs.wcs.cdelt = [-6.15/3600,6.15/3600]
    header = wcs.to_header()
    header['BUNIT'] = 'MJy/sr'; header['FINAST'] = 0
    header['DATE-OBS'] = '2026-08-17T00:54:34.145'
    flags = np.full((size,size),1<<21,dtype=np.int32)
    flags[40,40] |= 1<<12
    variance = np.full((size,size),.04,dtype=np.float32)
    path = tmp_path/'original.fits'
    fits.HDUList([fits.PrimaryHDU(),fits.ImageHDU(image,header=header,name='IMAGE'),
                  fits.ImageHDU(flags,name='FLAGS'),
                  fits.ImageHDU(variance,header=fits.Header({'BUNIT':'MJy2/sr2'}),name='VARIANCE'),
                  fits.BinTableHDU(name='WCS-WAVE')]).writeto(path)
    calls = []
    @contextmanager
    def lease(identifier):
        assert identifier in (ASSET,ASSET2)
        calls.append(('lease',identifier))
        try:
            yield path
        finally:
            calls.append(('release',identifier))
    def metadata(identifier):
        return {'id':identifier,'product_id':'product-'+identifier,'observation_id':'observation-'+identifier,
                'source_url':'https://irsa.ipac.caltech.edu/fixture.fits',
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(lenses.observations,'asset_lease',lease)
    monkeypatch.setattr(lenses.observations,'get_observation',metadata)
    def forbidden(*args,**kwargs):
        pytest.fail('Lens execution must never retrieve observations or calibration')
    monkeypatch.setattr(lenses.observations,'retrieve',forbidden)
    monkeypatch.setattr(lenses.sequence_service,'prepare',forbidden)
    return {'path':path,'wcs':wcs,'calls':calls,'record':metadata}


def selection(product, shape='circle', x=40, y=40, **fields):
    world = product['wcs'].pixel_to_world(x,y).icrs
    result = {'frame':'icrs','ra':float(world.ra.deg),'dec':float(world.dec.deg),'shape':shape}
    if shape == 'circle':
        result['radius_deg'] = fields.pop('radius_deg',.02)
    elif shape == 'rectangle':
        result.update(width_deg=.04,height_deg=.03)
    elif shape == 'polygon':
        world = product['wcs'].pixel_to_world([30,51,47,32],[31,33,52,48]).icrs
        result['vertices'] = np.column_stack([world.ra.deg,world.dec.deg]).tolist()
    result.update(fields)
    return result


def request(product, lens_id='spatial', products=None, **fields):
    result = {'lens_id':lens_id,'asset_id':ASSET,'selection':selection(product),'parameters':{}}
    if products:
        result['parameters']['products'] = products
    result.update(fields)
    return result


def execute(client, body):
    response = client.post('/api/lenses/execute',json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    AnalysisResult.model_validate(result)
    json.dumps(result,allow_nan=False)
    return result


def heatmap(result, product='brightness-map'):
    return next(d for d in result['visualization_descriptors'] if d['kind']=='heatmap' and d['product_id']==product)


def test_complete_manifest_and_contract_are_machine_readable(client):
    response = client.get('/api/lenses/capabilities')
    assert response.status_code == 200
    body = response.json()
    ids = {'spatial','spectral','temporal','object','statistical','three-dimensional','discovery','quality'}
    assert {lens['id'] for lens in body['lenses']} == ids
    assert 'result_category' in body['result_schema']['required']
    assert body['limits']['grid_side'] == 32
    rows = body['coverage_matrix']
    assert len(rows) > sum(len(lens['products']) for lens in body['lenses'])
    for lens in body['lenses']:
        assert lens['label'] and lens['description'] and lens['products']
        for p in lens['products']:
            assert p['status'] in ('available','experimental','unavailable')
            assert p['reason'] and p['prerequisites']
            assert any(row['lens_id']==lens['id'] and row['product_id']==p['id'] for row in rows)
    assert next(r for r in rows if r['representation_id']=='temperature')['status']=='unavailable'
    body['lenses'][0]['products'][0]['status'] = 'unavailable'
    assert client.get('/api/lenses/capabilities').json()['lenses'][0]['products'][0]['status']=='available'


@pytest.mark.parametrize('lens_id',list(LENSES))
def test_all_cards_have_honest_actionable_missing_prerequisites(client, lens_id):
    result = execute(client,{'lens_id':lens_id,'selection':{'ra':0,'dec':0,'shape':'circle','radius_deg':.1}})
    assert result['result_status']=='unavailable' and result['result_category']=='unknown'
    assert result['measurement_values']=={} and result['visualization_descriptors']==[]
    assert all(p['status']=='unavailable' and p['reason'] and p['prerequisites'] for p in result['products'])
    assert result['inference_performed'] is result['original_data_modified'] is result['retrieval_started'] is False


@pytest.mark.parametrize('shape',['circle','rectangle','polygon'])
def test_reduced_heatmap_is_exact_selected_original_pixel_mean_at_polar_ra_wrap(client,product,shape):
    before = product['path'].read_bytes()
    body = request(product,products=['brightness-map'],selection=selection(product,shape))
    body['parameters']['maximum'] = 8
    result = execute(client,body)
    assert result['result_status']=='completed' and result['result_category']=='calculated'
    grid = heatmap(result)
    assert grid['unit']=='MJy/sr' and grid['reduced_display_grid'] and grid['selection_clip_required']
    assert max(len(grid['values']),len(grid['values'][0])) <= 8
    yy,xx = np.mgrid[:80,:80]
    sky = product['wcs'].pixel_to_world(xx,yy).icrs
    mask = Selection.model_validate(body['selection']).contains(sky.ra.deg,sky.dec.deg)
    with fits.open(product['path']) as hdus:
        image = hdus['IMAGE'].data
        bad = quality_mask(image,hdus['FLAGS'].data,hdus['VARIANCE'].data,hdus['FLAGS'].header)
        for j,(ya,yb) in enumerate(zip(grid['original_pixel_y_edges'][:-1],grid['original_pixel_y_edges'][1:])):
            for i,(xa,xb) in enumerate(zip(grid['original_pixel_x_edges'][:-1],grid['original_pixel_x_edges'][1:])):
                selected = mask[ya:yb,xa:xb]
                good = selected & ~bad[ya:yb,xa:xb]
                assert grid['selected_counts'][j][i] == int(selected.sum())
                assert grid['usable_counts'][j][i] == int(good.sum())
                value = grid['values'][j][i]
                assert value == pytest.approx(float(image[ya:yb,xa:xb][good].mean()),rel=1e-6) if good.any() else value is None
                corners = np.asarray(grid['corners'][j][i])
                xy = product['wcs'].world_to_pixel(SkyCoord(corners[:,0]*u.deg,corners[:,1]*u.deg))
                assert xy[0] == pytest.approx([xa-.5,xb-.5,xb-.5,xa-.5],abs=1e-8)
    assert sum(map(sum,grid['selected_counts'])) == int(mask.sum()) < 80*80
    native = next(d for d in result['visualization_descriptors'] if d['kind']=='native_map')
    assert native['selection_filter']['required'] and native['source_arrays_are_unfiltered']
    assert native['array_urls']['IMAGE'].startswith('/api/observations/'+ASSET+'/array?')
    assert result['provenance']['asset']['sha256']==hashlib.sha256(before).hexdigest()
    assert result['provenance']['selection_coverage']['full_region_coverage_certified'] is False
    assert product['path'].read_bytes()==before
    assert sum(c[0]=='lease' for c in product['calls'])==sum(c[0]=='release' for c in product['calls'])


def test_uncertainty_snr_and_variance_have_distinct_propagation(client,product):
    body = request(product,products=['brightness-map','uncertainty-map','snr-map'])
    body['parameters']['maximum']=2
    result = execute(client,body)
    brightness,error,snr = (heatmap(result,id) for id in ('brightness-map','uncertainty-map','snr-map'))
    for j,row in enumerate(error['values']):
        for i,sigma in enumerate(row):
            n = error['usable_counts'][j][i]
            assert sigma == pytest.approx(.2/math.sqrt(n),rel=1e-6)
            assert snr['values'][j][i]==pytest.approx(brightness['values'][j][i]/sigma)
    assert 'independent' in error['reduction']
    quality = execute(client,request(product,'quality',['variance-map']))
    values = heatmap(quality,'variance-map')['values']
    assert all(v is None or v==pytest.approx(.04,rel=1e-6) for row in values for v in row)


def test_quality_map_counts_flags_and_bad_variance_without_inventing_artifacts(client,product):
    with fits.open(product['path'],mode='update') as hdus:
        hdus['VARIANCE'].data[41,40]=-1
        hdus['IMAGE'].data[42,40]=np.nan
        hdus['IMAGE'].header['FINAST']=1
    result = execute(client,request(product,'quality',['quality-map','flags','astrometry']))
    grid = heatmap(result,'quality-map')
    n = sum(map(sum,grid['selected_counts']))
    assert sum(map(sum,grid['usable_counts']))==n-3
    counts = result['measurement_values']['flags']
    assert counts['bit_counts']['12']==1 and counts['bit_counts']['21']==n
    assert counts['excluded_pixels']==3 and result['measurement_values']['astrometry']['status']=='poor'
    assert max(v for row in grid['values'] for v in row if v is not None)>0
    assert result['result_status']=='completed'
    observed = execute(client,request(product,'quality',['flags','astrometry']))
    assert observed['result_category']=='observed'


def test_no_usable_pixels_still_gives_invalid_fraction_not_fake_brightness(client,product):
    with fits.open(product['path'],mode='update') as hdus:
        hdus['FLAGS'].data[:] |= 1<<12
    quality = execute(client,request(product,'quality',['quality-map','variance-map']))
    assert quality['result_status']=='partial'
    assert all(v is None or v==1 for row in heatmap(quality,'quality-map')['values'] for v in row)
    spatial = execute(client,request(product,products=['brightness-map']))
    assert spatial['result_status']=='unavailable' and spatial['measurement_values']=={}


def test_zero_variance_has_null_snr_and_finite_json(client,product):
    with fits.open(product['path'],mode='update') as hdus:
        hdus['VARIANCE'].data[:]=0
    result = execute(client,request(product,products=['snr-map','uncertainty-map']))
    assert all(v is None for row in heatmap(result,'snr-map')['values'] for v in row)
    assert all(v is None or v==0 for row in heatmap(result,'uncertainty-map')['values'] for v in row)


def test_statistics_are_region_scoped_and_exclude_invalid_original_values(client,product):
    body = request(product,'statistical',['histogram','summary'])
    body['parameters']['bins']=7
    result = execute(client,body)
    stats = result['measurement_values']['summary']
    hist = result['measurement_values']['histogram']
    assert len(hist['counts'])==7 and len(hist['edges'])==8
    assert sum(hist['counts'])==stats['usable_pixels']==stats['selected_pixels']-1
    assert stats['selected_pixels'] < 80*80
    assert stats['mean']==pytest.approx(14,abs=1e-5)
    assert stats['minimum'] > 2 and stats['maximum'] < 25


def test_unresolvable_histogram_numeric_range_returns_actionable_status(client,product):
    with fits.open(product['path'],mode='update') as hdus:
        hdus['IMAGE'].data=np.full((80,80),1e100,dtype=np.float64)
    result=execute(client,request(product,'statistical',['histogram','summary']))
    assert result['result_status']=='partial'
    assert result['measurement_values']['summary']['mean']==pytest.approx(1e100)
    assert 'histogram' not in result['measurement_values']
    assert next(p for p in result['products'] if p['id']=='histogram')['status']=='unavailable'


def test_aperture_cannot_claim_selection_containment_from_anchor_alone(client,product):
    tiny = request(product,products=['aperture-photometry'],selection=selection(product,radius_deg=.002))
    result = execute(client,tiny)
    assert result['result_status']=='unavailable'
    assert 'support' in result['products'][0]['reason']
    assert result['measurement_values']=={}
    enough = request(product,products=['aperture-photometry'],selection=selection(product,radius_deg=.04))
    result = execute(client,enough)
    p = result['measurement_values']['aperture-photometry']
    assert p['position']['x']==pytest.approx(40,abs=1e-7)
    assert p['flux_jy'] is None and not p['lower_limit'] # flagged native center remains excluded
    assert not p['scientifically_usable']
    assert result['visualization_descriptors']==[] # target measurement never masquerades as map
    assert 'Not a region map' in result['products'][0]['reason']


def test_edge_aperture_and_nonoverlapping_selection_are_explicit(client,product):
    edge = request(product,products=['aperture-photometry'],selection=selection(product,x=1,y=1,radius_deg=.04))
    assert execute(client,edge)['result_status']=='unavailable'
    outside = request(product,products=['brightness-map'],selection={'ra':12,'dec':0,'shape':'circle','radius_deg':.01})
    result = execute(client,outside)
    assert result['result_status']=='unavailable' and result['measurement_values']=={}


def test_partial_asset_intersection_is_reported_not_full_region_coverage(client,product):
    body = request(product,products=['brightness-map'],selection=selection(product,x=1,y=1,radius_deg=.02))
    result = execute(client,body)
    assert result['result_status']=='completed'
    assert result['provenance']['selection_coverage']['full_region_coverage_certified'] is False
    assert any('intersection' in text for text in result['warnings'])


def add_spectral(path):
    x = np.array([1.,80.]); y = np.array([1.,80.])
    xx,yy = np.meshgrid(x,y)
    values = np.stack([1+xx*.001,.025+yy*.00001],axis=-1)
    table = fits.BinTableHDU.from_columns([
        fits.Column(name='X',format='2D',array=[x]),fits.Column(name='Y',format='2D',array=[y]),
        fits.Column(name='VALUES',format='8D',dim='(2,2,2)',array=[values])],name='WCS-WAVE')
    with fits.open(path,mode='update') as hdus:
        del hdus['WCS-WAVE'];hdus.append(table)
        h=hdus['IMAGE'].header;h['WCSAXESW']=2
        for i,axis in enumerate(('X','Y'),1):
            h[f'CTYPE{i}W']='WAVE-TAB';h[f'CUNIT{i}W']='um'
            h[f'CRPIX{i}W']=1.;h[f'CRVAL{i}W']=1.;h[f'CDELT{i}W']=1.
            h[f'PS{i}_0W']='WCS-WAVE';h[f'PS{i}_1W']='VALUES';h[f'PS{i}_2W']=axis;h[f'PV{i}_3W']=i


def test_target_spectral_estimate_is_experimental_and_not_a_spectrum(client,product):
    body = request(product,'spectral',['target-estimate'])
    assert execute(client,body)['result_status']=='unavailable'
    add_spectral(product['path'])
    before=product['path'].read_bytes()
    result=execute(client,body)
    assert result['products'][0]['status']=='experimental'
    point=result['measurement_values']['target-estimate']
    assert point['spectral']['axes'][0]['value']==pytest.approx(1.041)
    assert not point['science_grade_spectral_calibration']
    assert result['category']=='visualization_estimate' and 'target' in result['visualization_descriptors'][0]['scope']
    assert product['path'].read_bytes()==before
    assert execute(client,request(product,'spectral',['spectrum']))['result_status']=='unavailable'


def test_brightness_height_never_becomes_physical_depth(client,product):
    result=execute(client,request(product,'three-dimensional',['brightness-surface','distance-volume']))
    assert result['result_status']=='partial'
    surface=next(d for d in result['visualization_descriptors'] if d['kind']=='brightness_surface')
    assert surface['height_meaning']=='surface brightness, not physical distance'
    assert result['products'][1]['status']=='unavailable'


def test_supplied_catalog_retains_attribution_epoch_and_uncertainties(client,product):
    region=selection(product)
    rows=[{'id':'known','ra':region['ra'],'dec':region['dec'],'object_type':'star','uncertainty_arcsec':.3},
          {'id':'outside','ra':20,'dec':0}]
    body={'lens_id':'object','selection':region,'parameters':{'products':['supplied-catalog'],
          'catalog':{'source':'caller test catalog','reference_epoch':'J2000','rows':rows}}}
    result=execute(client,body)
    assert result['result_category']=='externally-sourced' and not product['calls']
    catalog=result['measurement_values']['supplied-catalog']
    assert catalog['rows']==rows[:1] and catalog['selected_count']==1
    assert not catalog['image_detections'] and not catalog['motion_propagated']
    assert catalog['completeness'].startswith('unknown')
    body['parameters']['catalog']['rows']=[]
    assert execute(client,body)['measurement_values']['supplied-catalog']['selected_count']==0


def sequence_state():
    return {'status':'ready','request':{'mode':'comparable'},'warnings':[],
            'common_wcs':{'frame':'icrs'},'display':{'stretch':'asinh'},
            'frames':[{'id':'d'*32,'asset_id':ASSET,'product_id':'product-'+ASSET,
                       'observation_id':'observation-'+ASSET,'mjd':60001.},
                      {'id':'e'*32,'asset_id':ASSET2,'product_id':'product-'+ASSET2,
                       'observation_id':'observation-'+ASSET2,'mjd':60000.}]}


def test_ready_sequence_reuses_original_leases_and_only_describes_playback(client,product,monkeypatch):
    monkeypatch.setattr(lenses.sequence_service,'status',lambda _:sequence_state())
    body={'lens_id':'temporal','selection':selection(product),'sequence_id':SEQUENCE,
          'parameters':{'products':['sequence']}}
    result=execute(client,body)
    assert result['result_category']=='observed' and result['result_status']=='completed'
    descriptor=next(d for d in result['visualization_descriptors'] if d['kind']=='sequence')
    assert [f['mjd'] for f in descriptor['frames']]==[60000.,60001.]
    assert descriptor['selection_clip_required'] and descriptor['source_images_are_unfiltered']
    assert descriptor['aligned_variance_propagated'] is False
    assert result['measurement_values']['sequence']['variability_measured'] is False
    assert product['calls']==[('lease',ASSET),('release',ASSET),('lease',ASSET2),('release',ASSET2)]


@pytest.mark.parametrize('change',['preparing','identity','timestamp','no-overlap'])
def test_sequence_prerequisite_failures_never_fake_results(client,product,monkeypatch,change):
    state=sequence_state()
    body={'lens_id':'temporal','selection':selection(product),'sequence_id':SEQUENCE,'parameters':{'products':['sequence']}}
    if change=='preparing':state['status']='preparing'
    if change=='identity':state['frames'][0]['product_id']='incorrect'
    if change=='timestamp':state['frames'][0]['mjd']=float('nan')
    if change=='no-overlap':body['selection']={'ra':20,'dec':0,'shape':'circle','radius_deg':.01}
    monkeypatch.setattr(lenses.sequence_service,'status',lambda _:state)
    response=client.post('/api/lenses/execute',json=body)
    if change=='identity':
        assert response.status_code==409
    else:
        assert response.status_code==200 and response.json()['result_status']=='unavailable'
        assert response.json()['measurement_values']=={}
    assert sum(c[0]=='lease' for c in product['calls'])==sum(c[0]=='release' for c in product['calls'])


@pytest.mark.parametrize('field,value',[
    ('ra',True),('dec','3'),('ra',360),('dec',91),('radius_deg',0),('radius_deg',6),
    ('radius_deg',True),('frame','galactic'),('shape','ellipse'),
    ('ra',10**400),
])
def test_strict_selection_validation_happens_before_asset_access(client,product,field,value):
    body=request(product);body['selection'][field]=value
    assert client.post('/api/lenses/execute',json=body).status_code==422
    assert not product['calls']


@pytest.mark.parametrize('region',[
    {'shape':'circle'},
    {'shape':'circle','radius_deg':.1,'vertices':[[0,0],[1,0],[1,1]]},
    {'shape':'rectangle','width_deg':1},
    {'shape':'rectangle','width_deg':True,'height_deg':1},
    {'shape':'polygon','vertices':[[0,0],[1,0]]},
    {'shape':'polygon','vertices':[[0,0],[1,0],[0,0]]},
    {'shape':'polygon','vertices':[[0,0],[1,0],[2,0]]},
    {'shape':'polygon','vertices':[[0,0],[1,1],[0,1],[1,0]]},
    {'shape':'polygon','vertices':[[0,0],[30,0],[1,1]]},
    {'shape':'polygon','vertices':[[0,0],[1,0],[0,1]]*22},
    {'shape':'polygon','vertices':[[False,0],[1,0],[0,1]]},
])
def test_malformed_and_unbounded_regions_rejected(client,product,region):
    body=request(product);body['selection']={'ra':0,'dec':0,**region}
    assert client.post('/api/lenses/execute',json=body).status_code==422
    assert not product['calls']


@pytest.mark.parametrize('changes',[
    {'asset_id':'/tmp/original.fits'},{'asset_id':'https://example.com/file.fits'},{'asset_id':True},
    {'url':'https://example.com/fits'},{'path':'/tmp/data.fits'},{'lens_id':'fake'},
    {'sequence_id':SEQUENCE},{'parameters':{'bins':True}},{'parameters':{'maximum':33}},
    {'parameters':{'bins':257}},{'parameters':{'products':['does-not-exist']}},
    {'parameters':{'products':['brightness-map','brightness-map']}},
    {'parameters':{'aperture_radius_px':True}},
    {'parameters':{'aperture_radius_px':5,'annulus_inner_px':6}},
    {'parameters':{'catalog':{'source':'x','reference_epoch':'J2000','rows':[]}}},
])
def test_invalid_source_and_parameter_contracts_are_rejected_before_reads(client,product,changes):
    body=request(product);body.update(changes)
    assert client.post('/api/lenses/execute',json=body).status_code==422
    assert not product['calls']


@pytest.mark.parametrize('bad',['NaN','Infinity','-Infinity'])
def test_nonfinite_json_is_rejected_with_finite_error_response(client,product,bad):
    # Replace via marker so the test doesn't rely on decimal formatting by WCS.
    value=request(product);value['selection']['ra']='MARKER'
    body=json.dumps(value).replace('"MARKER"',bad)
    result=client.post('/api/lenses/execute',content=body,headers={'Content-Type':'application/json'})
    assert result.status_code==422
    json.dumps(result.json(),allow_nan=False)
    assert not product['calls']


def test_oversized_original_is_rejected_from_header_before_science_array_load(client,product,monkeypatch):
    with fits.open(product['path'],mode='update') as hdus:
        hdus['IMAGE'].data=np.zeros((1025,1024),dtype=np.float32)
    def forbidden(*args,**kwargs):pytest.fail('Preflight must bound arrays first')
    monkeypatch.setattr(lenses.science,'source',forbidden)
    result=client.post('/api/lenses/execute',json=request(product,products=['brightness-map']))
    assert result.status_code==422 and 'smaller cutout' in result.json()['detail']
    assert product['calls']==[('lease',ASSET),('release',ASSET)]


def test_mismatched_companion_rejected_before_scientific_source(client,product,monkeypatch):
    with fits.open(product['path'],mode='update') as hdus:hdus['VARIANCE'].data=np.zeros((900,900),dtype=np.float32)
    def forbidden(*args,**kwargs):pytest.fail('Companion shape must be bounded before arrays')
    monkeypatch.setattr(lenses.science,'source',forbidden)
    assert client.post('/api/lenses/execute',json=request(product)).status_code==422


@pytest.mark.parametrize('status',[404,409,502,503])
def test_service_failures_preserve_status_and_do_not_retrieve(client,product,monkeypatch,status):
    @contextmanager
    def unavailable(identifier):
        raise DataError('Owned asset failure',status)
        yield
    monkeypatch.setattr(lenses.observations,'asset_lease',unavailable)
    result=client.post('/api/lenses/execute',json=request(product))
    assert result.status_code==status and result.json()['detail']=='Owned asset failure'


def test_bounded_capacity_returns_retryable_status_without_asset_access(client,product):
    assert lenses.SLOTS.acquire(False) and lenses.SLOTS.acquire(False)
    try:
        result=client.post('/api/lenses/execute',json=request(product))
        assert result.status_code==503 and not product['calls']
    finally:
        lenses.SLOTS.release();lenses.SLOTS.release()


def test_failure_releases_slots_and_leases(client,product):
    with fits.open(product['path'],mode='update') as hdus:hdus['IMAGE'].header['BUNIT']='Jy/pixel'
    assert client.post('/api/lenses/execute',json=request(product)).status_code==422
    assert product['calls'][-1]==('release',ASSET)
    assert lenses.SLOTS.acquire(False) and lenses.SLOTS.acquire(False)
    lenses.SLOTS.release();lenses.SLOTS.release()


def test_polar_circle_and_concave_spherical_polygon_are_not_flat_ra_boxes():
    circle=Selection(ra=359.99,dec=89.9,shape='circle',radius_deg=.3)
    assert circle.contains(.01,89.9) and circle.contains(180,89.9)
    polygon=Selection(ra=0,dec=0,shape='polygon',vertices=[(359.,-1.),(1.,-1.),(1.,1.),(0.,0.),(359.,1.)])
    assert polygon.contains(0,-.5) and not polygon.contains(.3,.8)


def test_requested_research_matrix_does_not_confuse_widgets_with_validated_methods():
    from lens_registry import capabilities
    manifest=capabilities()
    rows={(r['lens_id'],r['representation_id']):r for r in manifest['requested_representation_matrix']}
    assert len(rows)>120
    assert rows['spatial','water-ice-map']['status']=='unavailable'
    assert rows['object','distance-posterior']['status']=='unavailable'
    assert rows['discovery','planet-x-evidence']['status']=='unavailable'
    assert rows['temporal','chronological-observation-animation']['status']=='available'
    assert rows['temporal','transient-difference-image']['status']=='experimental'
    assert all(r['prerequisites'] for r in rows.values())
