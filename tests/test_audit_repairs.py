"""Regressions for the merged dataset/sky contracts, with isolated inputs."""
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import demo_targets as demo
import local_science as local
import spherex_pipeline as pipeline


def test_wise_status_matches_only_its_own_canonical_target(monkeypatch, tmp_path):
    monkeypatch.setattr(local, 'ROOT', tmp_path)
    monkeypatch.setattr(local, 'index', lambda: [
        {'target': name, 'kind': kind} for name, kind in [
            ('wise_0855', 'cubes'), ('wise_0855', 'observations'),
            ('orion', 'cubes'), ('cosmos', 'cubes'), ('cygnus_x', 'cubes')]])
    assert demo._local_status('wise-0855')['cubes'] == 1
    assert demo._local_status('wise-0855')['observations'] == 1


@pytest.mark.parametrize('values,status', [([1., None, 2.], 'exploratory'), ([None, None], 'unavailable')])
def test_cube_without_quality_acceptance_never_implies_science(monkeypatch, tmp_path, values, status):
    monkeypatch.setenv('SPHEREX_OFFLINE', '1')
    monkeypatch.setattr(local, 'ROOT', tmp_path)
    monkeypatch.setattr(local, 'target_field', lambda name: {'provenance': {'product_id': 'cube'}})
    monkeypatch.setattr(local, 'spectrum', lambda *a, **kw: {'surface_brightness': values})
    monkeypatch.setattr(local, 'discover', lambda *a: {'observations': []})
    monkeypatch.setattr(demo, '_nearby_cached_query', lambda *a: None)
    monkeypatch.setattr(demo, '_astrometry', lambda tid: {**demo.TARGETS[tid], 'source': 'test fixture'})
    assert demo._analyse('wise-0855')['status'] == status


def test_failed_refresh_does_not_return_stale_success(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, 'OUT', tmp_path)
    monkeypatch.setattr(demo, '_jobs', {'barnards-star': {'state': 'failed', 'reason': 'refresh failed'}})
    (tmp_path / 'barnards-star.json').write_text('{"status":"science"}')
    assert demo.result('barnards-star') == {'state': 'failed', 'reason': 'refresh failed'}


def test_offline_auto_does_not_generate_substitute_image(monkeypatch):
    monkeypatch.setenv('SPHEREX_OFFLINE', '1')
    monkeypatch.setattr(pipeline, '_local_observations', lambda *a: [])
    pipeline._SEARCH_CACHE.clear()
    with pytest.raises(pipeline.NotFound, match='Offline'):
        pipeline.observe(1, 1, mode='auto')


def test_exact_local_download_uses_original_read_only_path(monkeypatch, tmp_path):
    path = tmp_path / 'real.fits'
    path.write_bytes(b'original bytes')
    fake = SimpleNamespace(ROOT=tmp_path, index=lambda: [
        {'kind': 'observations', 'source_url': 'https://irsa.example/real.fits', 'path': path}])
    monkeypatch.setitem(sys.modules, 'local_science', fake)
    monkeypatch.setattr(pipeline, '_http', lambda: pytest.fail('unexpected HTTP'))
    assert pipeline.download_product('https://irsa.example/real.fits') == path
    assert path.read_bytes() == b'original bytes'


def test_offline_name_resolution_and_catalogue_do_not_call_network(monkeypatch):
    monkeypatch.setenv('SPHEREX_OFFLINE', '1')
    import requests
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **k: pytest.fail('unexpected HTTP'))
    with TestClient(main.app) as client:
        target = client.get('/api/resolve', params={'q': 'WISE 0855'}).json()
        assert target['name'] == 'WISE 0855-0714'
        assert target['astrometry_source']
        suggestions = client.get('/api/suggest', params={'q': 'wise'}).json()['results']
        assert len(suggestions) == 1
        stars = client.get('/api/stars', params={'ra': 1, 'dec': 1, 'radius': .1}).json()
        assert stars['objects'] == [] and 'Offline' in stars['scope']


def test_local_index_feeds_real_sky_image_without_http(monkeypatch, l2_file, tmp_path):
    from astropy.io import fits
    path, _ = l2_file
    with fits.open(path, mode='update') as hdus:
        hdus['IMAGE'].header['DETECTOR'] = 6
    url = 'https://irsa.example/local-integration.fits'
    record = {'id': 'local', 'collection': 'spherex_dr1'}
    fake = SimpleNamespace(ROOT=tmp_path,
        discover=lambda *a: {'observations': [record]},
        index=lambda: [{'id': 'local', 'kind': 'observations', 'path': path,
                        'observation_id': 'TEST_0001_4', 'source_url': url}])
    monkeypatch.setitem(sys.modules, 'local_science', fake)
    monkeypatch.setenv('SPHEREX_OFFLINE', '1')
    monkeypatch.setattr(pipeline, '_http', lambda: pytest.fail('unexpected HTTP'))
    rows = pipeline.search_observations(269.45, 4.69)
    assert rows[0]['data_origin'] == 'local dataset'
    assert rows[0]['collection'] == 'spherex_dr1'
    product = pipeline.observe(269.45, 4.69, mode='live')
    assert product.source == 'irsa' and product.width == 200
    assert product.pixel_of(269.45, 4.69) == pytest.approx([100., 100.], abs=.6)


def test_dataset_observation_route_is_local_only(monkeypatch):
    import observation_service
    seen = []
    monkeypatch.setattr(local, 'discover', lambda ra, dec: {'observations': [], 'query': {'ra': ra, 'dec': dec, 'source': 'local indexed footprints'}})
    monkeypatch.setattr(observation_service, 'register_catalog', lambda rows: seen.append(rows))
    import requests
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **k: pytest.fail('unexpected external HTTP'))
    with TestClient(main.app) as client:
        r = client.get('/api/dataset/observations', params={'ra': 15, 'dec': -4})
        assert r.status_code == 200 and r.json()['query']['source'] == 'local indexed footprints'
        assert seen == [[]]
        assert client.get('/api/dataset/observations', params={'ra': 400, 'dec': 0}).status_code == 422
