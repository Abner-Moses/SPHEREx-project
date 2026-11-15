import numpy as np
from research_analysis import continuum

def test_continuum_handles_no_samples():
    assert continuum([])['status']=='unavailable'

def test_constant_continuum_is_exact():
    fit=continuum([{'wavelength_um':w,'flux_jy':2.,'flux_err_jy':.1} for w in [2.7,2.8,3.,3.3,3.4]],2.9,3.1)
    np.testing.assert_allclose(fit['continuum_jy'],2,atol=1e-10)

def test_feature_integral_and_covariance():
    points=[{'wavelength_um':x,'flux_jy':1. if 2.9<=x<=3.1 else 2.,'flux_err_jy':.1} for x in [2.6,2.7,2.8,2.9,3.,3.1,3.2,3.3,3.4]]
    result=continuum(points,2.9,3.1);f=result['feature_measurements']
    np.testing.assert_allclose(f['signed_excess_jy_um'],-.2,atol=1e-10)
    np.testing.assert_allclose(f['equivalent_width_um'],.1,atol=1e-10)
    assert f['signed_excess_sigma_jy_um']>0
    assert f['equivalent_width_sigma_um']>0
    assert all(v>=0 for v in result['residual_sigma_jy'])

def test_motion_fit_wraps_ra_and_preserves_covariance():
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    from native_analysis import motion_fit
    origin=SkyCoord(359.9999,60,unit='deg');rows=[]
    for year in [2020,2021,2022,2023]:
        p=origin.spherical_offsets_by((year-2020)*.2*u.arcsec,(year-2020)*-.1*u.arcsec)
        rows.append({'ra':p.ra.deg,'dec':p.dec.deg,'timestamp':f'{year}-01-01T00:00:00','covariance_arcsec2':[[.01,.002],[.002,.02]]})
    result=motion_fit(rows)
    assert result['status']=='exploratory'
    np.testing.assert_allclose(result['east_north_rate_mas_year'],[200,-100],rtol=.002)
    assert result['degrees_of_freedom']==4
    rows[0]['covariance_arcsec2']=[[0,0],[0,0]]
    assert motion_fit(rows)['status']=='unavailable'

def test_real_local_cutout_preserves_measurements_and_recovers_missing_asset(tmp_path,monkeypatch):
    import pytest
    import local_science as local
    import observation_service
    from astropy.io import fits
    if not local.ROOT.exists():pytest.skip('Local NASA dataset not installed')
    r=next((r for r in local.index() if r['kind']=='observations' and r['record']),None)
    if r is None:pytest.skip('No local Level-2 product')
    from sequence_service import _celestial
    with fits.open(r['path'],memmap=False) as original:
        ny,nx=original['IMAGE'].shape
        position=_celestial(original['IMAGE'].header).pixel_to_world(nx//2,ny//2).icrs
        monkeypatch.setattr(observation_service,'CACHE',tmp_path)
        result=local.retrieve(r['id'],float(position.ra.deg),float(position.dec.deg),.02)
        path=tmp_path/(result['id']+'.fits')
        with fits.open(path,memmap=False) as derived:
            y=int(original['IMAGE'].header.get('CRPIX2A',1)-derived['IMAGE'].header['CRPIX2A']);x=int(original['IMAGE'].header.get('CRPIX1A',1)-derived['IMAGE'].header['CRPIX1A']);ny,nx=derived['IMAGE'].shape
            from sequence_spectral import original_pixel
            np.testing.assert_allclose(original_pixel(derived['IMAGE'].header,0,0),original_pixel(original['IMAGE'].header,x,y))
            for name in ['IMAGE','FLAGS','VARIANCE','ZODI']:
                np.testing.assert_array_equal(derived[name].data,original[name].data[y:y+ny,x:x+nx])
        assert local.retrieve(r['id'],float(position.ra.deg),float(position.dec.deg),.02)['id']==result['id']
        path.unlink()  # Only this test's isolated derivative, never the source.
        restored=local.retrieve(r['id'],float(position.ra.deg),float(position.dec.deg),.02)
        assert path.exists() and restored['id']==result['id'] and len(restored['sha256'])==64
