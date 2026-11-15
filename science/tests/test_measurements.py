"""Original-array measurements, API contracts and adversarial scientific cases."""
from contextlib import contextmanager
import hashlib
import math
import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from fastapi.testclient import TestClient
from main import app
from archive_client import DataError
import scientific_measurements as science
import measurements_api

@pytest.fixture
def product(tmp_path,monkeypatch):
    size=80;yy,xx=np.mgrid[:size,:size]
    image=(2+50*np.exp(-((xx-40)**2+(yy-40)**2)/(2*1.5**2))).astype(np.float32)
    w=WCS(naxis=2);w.wcs.ctype=['RA---TAN','DEC--TAN'];w.wcs.crval=[359.999,85];w.wcs.crpix=[40,40];w.wcs.cdelt=[-6.15/3600,6.15/3600]
    h=w.to_header();h['BUNIT']='MJy/sr';h['FINAST']=0;h['DATE-OBS']='2026-08-17T00:54:34.145'
    flags=np.full((size,size),1<<21,dtype=np.int32)
    variance=np.full((size,size),.04,dtype=np.float32)
    path=tmp_path/'original.fits'
    fits.HDUList([fits.PrimaryHDU(),fits.ImageHDU(image,header=h,name='IMAGE'),fits.ImageHDU(flags,name='FLAGS'),
      fits.ImageHDU(variance,header=fits.Header({'BUNIT':'MJy2/sr2'}),name='VARIANCE'),
      fits.ImageHDU(np.full((size,size),.5,dtype=np.float32),header=fits.Header({'BUNIT':'MJy/sr'}),name='ZODI'),
      fits.BinTableHDU(name='EPSF'),fits.BinTableHDU(name='WCS-WAVE')]).writeto(path)
    @contextmanager
    def lease(identifier):yield path
    monkeypatch.setattr(science.observations,'asset_lease',lease)
    monkeypatch.setattr(science.observations,'get_observation',lambda identifier:{'id':identifier,'source_url':'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/test.fits','sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return path

def test_gaussian_flux_and_variance_with_spherical_pixel_area(product):
    before=product.read_bytes()
    p=science.photometry('a'*32,x=40,y=40,radius=6,annulus_inner=10,annulus_outer=15)
    omega=(6.15*math.pi/(180*3600))**2
    expected=50*2*math.pi*1.5**2*omega*1e6
    assert p['flux_jy']==pytest.approx(expected,rel=.005)
    assert p['flux_err_jy']>0 and p['uncertainty']['measurement_variance_jy2']>0
    assert p['background']['value_mjy_sr']==pytest.approx(2,abs=1e-5)
    assert p['scientifically_usable'] and not p['lower_limit']
    assert product.read_bytes()==before

@pytest.mark.parametrize('bit',[12,29,6])
def test_masked_pixels_never_establish_lower_limit_or_saturation(product,bit):
    with fits.open(product,mode='update') as h:h['FLAGS'].data[40,40]|=1<<bit
    p=science.photometry('a'*32,x=40,y=40,radius=6,annulus_inner=10,annulus_outer=15)
    assert p['flux_jy'] is None and not p['lower_limit'] and not p['scientifically_usable']
    assert p['aperture']['scientific_valid_fraction']<1
    assert p['valid_contribution_flux_jy'] is not None

def test_edge_clipped_aperture_reported_as_incomplete(product):
    p=science.photometry('a'*32,x=1,y=1,radius=3,annulus_inner=6,annulus_outer=10)
    assert not p['aperture']['complete'] and p['aperture']['geometric_coverage_fraction']<1
    assert p['flux_jy'] is None and not p['lower_limit']

def test_bad_astrometry_does_not_get_scientific_acceptance(product):
    with fits.open(product,mode='update') as h:h['IMAGE'].header['FINAST']=1
    p=science.photometry('a'*32,x=40,y=40)
    assert not p['scientifically_usable'] and p['astrometric_quality']['status']=='poor'

def test_variance_not_mad_controls_error(product):
    a=science.photometry('a'*32,x=40,y=40)
    with fits.open(product,mode='update') as h:h['VARIANCE'].data*=4
    b=science.photometry('a'*32,x=40,y=40)
    assert b['uncertainty']['measurement_variance_jy2']==pytest.approx(4*a['uncertainty']['measurement_variance_jy2'])
    assert b['flux_err_jy']>a['flux_err_jy']

def test_wrong_units_rejected(product):
    with fits.open(product,mode='update') as h:h['IMAGE'].header['BUNIT']='Jy/pixel'
    with pytest.raises(DataError,match='units'):science.photometry('a'*32,x=40,y=40)

def test_overlapping_background_and_boolean_positions_rejected(product):
    with pytest.raises(DataError):science.photometry('a'*32,x=40,y=40,radius=5,annulus_inner=6)
    with pytest.raises(DataError):science.photometry('a'*32,x=True,y=40)

def test_histogram_and_profile_use_original_values_and_quality(product):
    with fits.open(product,mode='update') as h:
      h['FLAGS'].data[0,0]|=1<<12;h['IMAGE'].data[0,1]=np.nan
    stats=science.statistics('a'*32,bins=16)
    assert stats['valid_pixels']==80*80-2 and sum(stats['histogram']['counts'])==stats['valid_pixels']
    assert stats['maximum']>50 and stats['unit']=='MJy/sr'
    p=science.profile('a'*32,x0=0,y0=0,x1=5,y1=0,samples=6)
    assert p['points'][0]['value'] is None and p['points'][1]['value'] is None
    assert p['points'][2]['value']==2

def test_real_http_contracts_and_sky_position(product):
    with fits.open(product) as h:
      ra,dec=WCS(h['IMAGE'].header).celestial.all_pix2world(40,40,0)
    client=TestClient(app)
    url='/api/observations/'+'a'*32
    result=client.get(url+'/photometry',params={'ra':float(ra),'dec':float(dec)})
    assert result.status_code==200 and result.json()['position']['x']==pytest.approx(40,abs=1e-7)
    assert client.get(url+'/statistics?bins=257').status_code==422
    assert client.get(url+'/photometry?x=NaN&y=0').status_code==422
    assert client.get(url+'/profile?x0=0&y0=0&x1=4&y1=0&samples=3').status_code==200
    spectral=client.get(url+'/spectral-point',params={'ra':float(ra),'dec':float(dec)})
    assert spectral.status_code==200 and not spectral.json()['science_grade_spectral_calibration']

def test_explicit_zodi_operation_is_labeled_and_raw_bytes_preserved(product):
    before=product.read_bytes();a=science.photometry('a'*32,x=40,y=40)
    b=science.photometry('a'*32,x=40,y=40,subtract_zodi=True)
    assert b['background']['value_mjy_sr']==pytest.approx(a['background']['value_mjy_sr']-.5)
    assert b['flux_jy']==pytest.approx(a['flux_jy'])
    assert b['zodi_subtracted'] and not a['zodi_subtracted'] and product.read_bytes()==before

def test_derived_mask_summary_and_surface_remain_tied_to_original_data(product):
    import io
    before=product.read_bytes()
    with fits.open(product,mode='update') as h:h['FLAGS'].data[0,0]|=1<<12
    before=product.read_bytes();client=TestClient(app);url='/api/observations/'+'a'*32
    summary=client.get(url+'/quality-summary').json()
    assert summary['flag_counts']['FULLSAMPLE']==1 and summary['flag_counts']['SOURCE']==80*80
    assert summary['median_reported_sigma_mjy_sr']==pytest.approx(.2)
    mask=client.get(url+'/quality-mask')
    assert mask.headers['X-Data-Product']=='derived-scientific-invalid-mask'
    with fits.open(io.BytesIO(mask.content)) as h:
        assert h[0].data[0,0]==1 and h[0].data[40,40]==0
        assert h[0].header['DERIVED'] and h[0].header['BUNIT']=='1'
    grid=client.get(url+'/surface?maximum=8').json()
    assert len(grid['values'])==8 and len(grid['values'][0])==8
    assert 'not physical' in grid['height_meaning'] and grid['scientific_valid_fraction'][0][0]<1
    assert product.read_bytes()==before
    manifest=client.get('/api/analysis/inputs/'+'a'*32)
    assert manifest.status_code==200 and not manifest.json()['inference_performed']
    assert len(manifest.json()['inventory']['hdus'])==7

def test_unit_conversion_preserves_aperture_flux(product):
    a=science.photometry('a'*32,x=40,y=40)
    with fits.open(product,mode='update') as h:
        h['IMAGE'].data*=1e6;h['IMAGE'].header['BUNIT']='Jy/sr'
        h['VARIANCE'].data*=1e12;h['VARIANCE'].header['BUNIT']='Jy2/sr2'
    b=science.photometry('a'*32,x=40,y=40)
    assert b['flux_jy']==pytest.approx(a['flux_jy'],rel=1e-6)
    assert b['flux_err_jy']==pytest.approx(a['flux_err_jy'],rel=1e-6)

def test_spherical_pixel_areas_include_full_wcs_at_ra_wrap(product):
    from spherical_geometry.polygon import SphericalPolygon
    with fits.open(product) as h:w=WCS(h['IMAGE'].header).celestial
    area=science._areas(w,np.array([0,40,79]),np.array([0,40,79]))
    for j,y in enumerate([0,40,79]):
      for i,x in enumerate([0,40,79]):
        ra,dec=w.all_pix2world([x-.5,x+.5,x+.5,x-.5],[y-.5,y-.5,y+.5,y+.5],0)
        expected=SphericalPolygon.from_radec(ra,dec).area()
        assert area[j,i]==pytest.approx(expected,rel=5e-5)
