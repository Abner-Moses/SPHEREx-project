"""Offline real-data routing, plus isolated upstream-failure regression."""
import pytest
from fastapi.testclient import TestClient
import local_science as local
import observation_service as assets
import discovery_service
from archive_client import DataError
from main import app

@pytest.fixture
def dataset():
    if not local.ROOT.exists():pytest.skip('Local NASA dataset not installed')
    return local

def test_five_fields_work_without_archive_or_catalogue(dataset,monkeypatch):
    import archive_enrichment,catalog_service
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected external data request')
    async def forbidden_async(*args,**kwargs):raise AssertionError('Unexpected external discovery')
    monkeypatch.setattr(local,'OFFLINE',True)
    monkeypatch.setattr(discovery_service,'search',forbidden_async)
    monkeypatch.setattr(assets,'download',forbidden)
    monkeypatch.setattr(archive_enrichment,'enrich',forbidden)
    monkeypatch.setattr(archive_enrichment,'datalink',forbidden)
    monkeypatch.setattr(catalog_service,'resolve',forbidden)
    with TestClient(app) as client:
        for name in ["Barnard's Star",'Epsilon Indi B','WISE 0855','RW Aurigae','R Draconis']:
            response=client.get('/api/catalog/resolve',params={'q':name});assert response.status_code==200,response.text
            point=response.json();assert point['position_kind']=='downloaded_field_center'
            assert point['epoch'] is None
            found=client.post('/api/search',json={'ra':point['ra'],'dec':point['dec']});assert found.status_code==200,found.text
            records=found.json()['observations'];assert records,name
            retrieved=client.post('/api/observations/'+records[0]['id']+'/retrieve',json={'ra':point['ra'],'dec':point['dec'],'cutout_size_deg':.04})
            assert retrieved.status_code==200,retrieved.text
            result=client.get('/api/observations/'+retrieved.json()['id']+'/irsa-metadata')
            assert result.status_code==200 and result.json()['local']['status']=='available'
        assert client.post('/api/object-search',json={'name':"Barnard's Star"}).status_code==200

def test_remote_identifier_reuses_exact_local_product(dataset,monkeypatch):
    from astropy.io import fits
    from sequence_service import _celestial
    row=next(r for r in local.index() if r['kind']=='observations' and r['record'])
    remote={**row['record'],'id':'a'*32,'source_url':'https://nasa-irsa-spherex.s3.amazonaws.com/'+row['source_url'].rsplit('/',1)[-1]}
    assets.register_catalog([remote]);monkeypatch.setattr(local,'OFFLINE',True)
    with fits.open(row['path']) as h:
        ny,nx=h['IMAGE'].shape;p=_celestial(h['IMAGE'].header).pixel_to_world(nx//2,ny//2).icrs
    result=assets.retrieve(remote['id'],float(p.ra.deg),float(p.dec.deg),.02)
    assert result['requested_product_id']==remote['id'] and result['product_id']==row['id']

def test_upstream_failure_has_local_fallback_and_cooldown(dataset,monkeypatch):
    monkeypatch.setattr(local,'OFFLINE',False);monkeypatch.setattr(local,'_REMOTE_FAILURES',{})
    monkeypatch.setattr(local,'cached_discovery',lambda req:None)
    calls=[]
    async def failed(*args):calls.append(1);raise DataError('Injected upstream unavailable',503)
    monkeypatch.setattr(discovery_service,'search',failed)
    with TestClient(app) as client:
        for _ in range(2):
            result=client.post('/api/search',json={'ra':0.,'dec':-90.})
            assert result.status_code==200 and result.json()['fallback']['archive_completeness']=='unknown'
    assert len(calls)==1

def test_local_sky_channel_retains_original_wcs_and_brightness(dataset):
    import io,numpy as np
    from astropy.io import fits
    from astropy.wcs import WCS
    row=next(r for r in local.index() if r['kind']=='cubes' and 'orion' in r['target'].lower())
    with fits.open(row['path'],memmap=True) as original:
        p=local.cube_wcs(original).pixel_to_world(256,256)
        assert local.local_sky(float(p.ra.deg),float(p.dec.deg))['available']
        response=local.channel_fits(row['id'],60)
        with fits.open(io.BytesIO(response.body)) as derived:
            good=original['NHITS'].data[60]>0
            np.testing.assert_array_equal(derived[0].data[good],original['IMAGE'].data[60][good])
            p2=WCS(derived[0].header).pixel_to_world(256,256)
            assert p.separation(p2).arcsec<1e-5
            assert derived[0].header['SPXDERIV'] and derived[0].header['BUNIT']=='MJy/sr'

def test_local_cache_budget_never_returns_an_evicted_asset(dataset,tmp_path,monkeypatch):
    from astropy.io import fits
    from sequence_service import _celestial
    row=next(r for r in local.index() if r['kind']=='observations' and r['record'])
    monkeypatch.setattr(assets,'CACHE',tmp_path);monkeypatch.setattr(assets,'MAX_CACHE',128)
    with fits.open(row['path']) as h:
        ny,nx=h['IMAGE'].shape;p=_celestial(h['IMAGE'].header).pixel_to_world(nx//2,ny//2).icrs
    with pytest.raises(DataError) as exc:local.retrieve(row['id'],float(p.ra.deg),float(p.dec.deg),.02)
    assert exc.value.status==507
    assert not list(tmp_path.glob('*.fits')) and not list(tmp_path.glob('*.json'))

def test_far_field_wcs_inversion_cannot_admit_unrelated_exposure(dataset):
    result=local.discover(337.8783221,-5.1891588)
    assert result['observations']==[]
