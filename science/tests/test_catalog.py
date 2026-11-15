"""Isolated catalog protocol/API/astrometry tests; live checks are run separately."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io.votable import from_table, writeto
from astropy.io.votable.tree import Info
from astropy.table import Table
from astropy.time import Time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from urllib3.response import HTTPResponse

import catalog_service as catalog
from catalog_api import router


def row(**changes):
    return {'oid': 1, 'main_id': 'Test star', 'ra': 359.999, 'dec': 89.999,
            'pmra': 1000., 'pmdec': 1000., 'pm_err_maj': 4., 'pm_err_min': 2.,
            'pm_err_angle': 90., 'plx_value': 100., 'plx_err': 1., 'otype': '*',
            'vmag': 7., **changes}


def payload(rows=None, statuses=None, maxrec=1001):
    return {'rows': [row()] if rows is None else rows, 'maxrec': maxrec,
            'provenance': {'query_status': ['OK'] if statuses is None else statuses,
                'query': 'controlled test query', 'query_sha256': 'abc', 'response_sha256': 'def',
                'catalog': 'SIMBAD', 'service_url': catalog.ENDPOINTS[0], 'queried_at': '2026-11-14T00:00:00Z'}}


def votable(rows=None, overflow=False, error=False, status=True):
    rows = [row()] if rows is None else rows
    table = Table(rows=rows) if rows else Table(names=['oid', 'main_id', 'ra', 'dec'], dtype=['i8', 'U20', 'f8', 'f8'])
    vot = from_table(table)
    if status:
        vot.resources[0].infos.append(Info(name='QUERY_STATUS', value='ERROR' if error else 'OK'))
    out = io.BytesIO(); writeto(vot, out)
    body = out.getvalue()
    if overflow:
        body = body.replace(b'</RESOURCE>', b'<INFO name="QUERY_STATUS" value="OVERFLOW"/></RESOURCE>')
    return body


def wire_response(request, body=b'', status=200, headers=None):
    response = requests.Response(); response.status_code = status
    response.url = request.url; response.request = request; response.headers.update(headers or {})
    response.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False)
    return response


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    catalog._CACHE.clear(); catalog._FLIGHTS.clear()
    def blocked(*args, **kwargs):
        pytest.fail('Unit test attempted a real catalog request')
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', blocked)
    yield
    assert not catalog._FLIGHTS
    catalog._CACHE.clear()


@pytest.fixture
def client():
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize('bad', [True, False, float('nan'), float('inf'), '10', None])
def test_service_rejects_nonfinite_and_boolean_coordinates(bad):
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.stars(bad, 0, .01)
    assert exc.value.status == 422


@pytest.mark.parametrize('args', [(360, 0, .01), (0, 91, .01), (0, 0, 0), (0, 0, 5.1)])
def test_service_coordinate_bounds(args):
    with pytest.raises(catalog.CatalogError):
        catalog.stars(*args)


@pytest.mark.parametrize('query', ['ra=nan&dec=0&radius=.01', 'ra=true&dec=0&radius=.01',
                                  'ra=1&dec=inf&radius=.01', 'ra=1&dec=0&radius=true',
                                  'ra=1&dec=0&radius=.01&limit=true'])
def test_api_strict_query_validation(client, query):
    assert client.get('/api/catalog/stars?' + query).status_code == 422


def test_resolve_flat_reference_and_prediction_contract(client, monkeypatch):
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload())
    result = client.get('/api/catalog/resolve', params={'q': 'Test', 'epoch': 2026.87})
    assert result.status_code == 200
    obj = result.json()
    assert obj['frame'] == 'icrs' and obj['epoch'] == 'J2000'
    assert obj['ra'] == 359.999 and obj['dec'] == 89.999
    predicted = obj['propagated_position']
    assert predicted['epoch'] == 2026.87 and predicted['status'] == 'available'
    assert predicted['annual_parallax_included'] is False
    assert predicted['uncertainty_propagated'] is False
    assert 0 <= predicted['ra'] < 360 and -90 <= predicted['dec'] <= 90
    assert obj['pmra_error_mas_yr'] == pytest.approx(4)
    assert obj['pmdec_error_mas_yr'] == pytest.approx(2)
    assert obj['parallax_error_mas'] == 1
    assert obj['distance_estimate']['value_pc'] == 10
    assert 'objects' not in obj


def test_polar_space_motion_agrees_with_astropy_and_crosses_pole(monkeypatch):
    values = row(ra=10, dec=89.999, pmra=0, pmdec=1000, plx_value=None, plx_err=None)
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload([values]))
    obj = catalog.resolve('Test', 2026)
    with pytest.warns(Warning):
        truth = SkyCoord(ra=10*u.deg, dec=89.999*u.deg, pm_ra_cosdec=0*u.mas/u.yr,
            pm_dec=1000*u.mas/u.yr, obstime=Time(2000, format='jyear', scale='tt')).apply_space_motion(new_obstime=Time(2026, format='jyear', scale='tt'))
    assert obj['propagated_position']['ra'] == pytest.approx(truth.ra.deg, abs=1e-9)
    assert obj['propagated_position']['dec'] < 89.999
    assert obj['propagated_position']['ra'] == pytest.approx(190)


def test_missing_motion_is_not_fabricated_as_zero(monkeypatch):
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload([row(pmra=None)]))
    obj = catalog.resolve('Test', 2026.87)
    assert obj['propagated_position']['status'] == 'unavailable'
    assert obj['propagated_position']['ra'] is None
    assert obj['pmra_mas_yr'] is None


@pytest.mark.parametrize('plx,error,quality', [(-1, .1, 'nonpositive_parallax'), (1, 1, 'low_snr'),
                                             (1, None, 'unknown_uncertainty'), (None, None, 'unavailable')])
def test_uncertain_parallaxes_do_not_turn_into_distances(monkeypatch, plx, error, quality):
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload([row(plx_value=plx, plx_err=error)]))
    distance = catalog.resolve('Test')['distance_estimate']
    assert distance['value_pc'] is None and distance['quality'] == quality


def test_perspective_motion_uses_only_supported_distance_and_rv(monkeypatch):
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload([row(rvz_radvel=-100)]))
    obj = catalog.resolve('Test', 2026)
    assert obj['propagated_position']['perspective_acceleration_included'] is True
    assert obj['propagated_position']['annual_parallax_included'] is False


def test_exact_cone_cache_has_no_quantized_collision_or_mutation(monkeypatch):
    calls = []
    def tap(query, maxrec):
        calls.append(query)
        return payload([row(ra=10.001 if '10.000999' in query else 10.004)], maxrec=maxrec)
    monkeypatch.setattr(catalog, '_tap', tap)
    a = catalog.stars(10.001, 0, .0005)
    b = catalog.stars(10.004, 0, .0005)
    assert len(calls) == 2 and calls[0] != calls[1]
    assert 'Star..' in calls[0] and 'TOP' not in calls[0]
    a['objects'][0]['name'] = 'mutated'
    again = catalog.stars(10.001, 0, .0005)
    assert len(calls) == 2 and again['cache_hit']
    assert again['objects'][0]['name'] == 'Test star'
    catalog.stars(10.001, 0, .0006)
    assert len(calls) == 3


def test_epoch_prediction_does_not_poison_reference_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(catalog, '_tap', lambda *args: (calls.append(args) or payload()))
    a = catalog.resolve('Test', 2020)
    b = catalog.resolve('Test', 2030)
    c = catalog.resolve('Test')
    assert len(calls) == 1
    assert a['ra'] == b['ra'] == c['ra']
    assert a['propagated_position']['ra'] != b['propagated_position']['ra']
    assert 'propagated_position' not in c


def test_cache_expiry_and_entry_budget(monkeypatch):
    monkeypatch.setattr(catalog, 'CACHE_TTL', .01)
    monkeypatch.setattr(catalog, 'MAX_CACHE_ENTRIES', 1)
    calls = []
    monkeypatch.setattr(catalog, '_tap', lambda *args: (calls.append(args) or payload()))
    catalog.resolve('A'); catalog.resolve('B')
    assert len(catalog._CACHE) == 1
    catalog.resolve('A')
    time.sleep(.02)
    catalog.resolve('A')
    assert len(calls) == 4


def test_singleflight_shares_one_query_and_returns_independent_results(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def tap(*args):
        calls.append(args); entered.set(); assert release.wait(1); return payload()
    monkeypatch.setattr(catalog, '_tap', tap)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(catalog.resolve, 'same'); assert entered.wait(1)
        b = pool.submit(catalog.resolve, 'same'); time.sleep(.02); release.set()
        x, y = a.result(), b.result()
    assert len(calls) == 1
    x['name'] = 'local'
    assert y['name'] == 'Test star'


def test_overflow_and_limited_results_are_not_claimed_complete(monkeypatch):
    monkeypatch.setattr(catalog, '_tap', lambda query, cap: payload([row(oid=1), row(oid=2)], ['OK', 'OVERFLOW'], cap))
    result = catalog.stars(0, 0, .01, limit=1)
    assert len(result['objects']) == 1
    assert result['completeness']['status'] == 'truncated'
    assert result['completeness']['overflow']
    assert result['query']['selection_epoch'] == 'J2000'
    assert result['completeness']['scope'].endswith('predicted-epoch membership.')


def test_exact_identifier_escaping_and_suggestion_validation(monkeypatch):
    queries = []
    monkeypatch.setattr(catalog, '_tap', lambda query, cap: (queries.append(query) or payload()))
    catalog.resolve("Barnard's star")
    assert "'NAME Barnard''s star'" in queries[0]
    assert ' LIKE ' not in queries[0]
    catalog.resolve('M31')
    assert "'M  31'" in queries[1]
    with pytest.raises(catalog.CatalogError):
        catalog.suggest('a%')
    with pytest.raises(catalog.CatalogError):
        catalog.resolve('a\nquery')


def test_unknown_and_ambiguous_identifiers(client, monkeypatch):
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload([]))
    assert client.get('/api/catalog/resolve?q=missing').status_code == 404
    catalog._CACHE.clear()
    monkeypatch.setattr(catalog, '_tap', lambda *args: payload([row(), row(oid=2)]))
    assert client.get('/api/catalog/resolve?q=ambiguous').status_code == 409


def test_suggest_and_stars_router_shapes(client, monkeypatch):
    monkeypatch.setattr(catalog, '_tap', lambda query, cap: payload(maxrec=cap))
    for url in ['/api/catalog/suggest?prefix=Te', '/api/catalog/stars?ra=1&dec=0&radius=.001&limit=1000']:
        result = client.get(url)
        assert result.status_code == 200
        assert result.json()['objects'][0]['epoch'] == 'J2000'


def test_real_requests_votable_protocol_and_trailing_status(monkeypatch):
    calls = []
    def send(self, request, **kwargs):
        calls.append((request, kwargs)); return wire_response(request, votable(overflow=True))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    result = catalog.stars(359.999, 90, .0005)
    body = parse_qs(urlsplit(calls[0][0].url).query)
    assert body['MAXREC'] == ['1001'] and body['FORMAT'] == ['votable']
    assert result['provenance']['query_status'] == ['OK', 'OVERFLOW']
    assert result['completeness']['overflow']
    assert calls[0][1]['timeout'][1] <= catalog.REQUEST_TIMEOUT
    assert result['objects'][0]['provenance']['pmra_convention'] == 'mu_alpha_cosdec'


def test_transient_mirror_retry_is_bounded(monkeypatch):
    calls = []
    def send(self, request, **kwargs):
        calls.append(request.url)
        return wire_response(request, b'busy', 503) if len(calls) < 2 else wire_response(request, votable())
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    obj = catalog.resolve('Test')
    assert len(calls) == 2 and obj['name'] == 'Test star'
    assert obj['provenance']['service_url'] == catalog.ENDPOINTS[1]


def test_error_votable_is_not_an_empty_result_or_cached(monkeypatch):
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', lambda self, req, **kwargs: wire_response(req, votable(error=True)))
    with pytest.raises(catalog.CatalogError, match='rejected'):
        catalog.resolve('Test')
    assert not catalog._CACHE


def test_unknown_query_status_never_claims_complete(monkeypatch):
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', lambda self, req, **kwargs: wire_response(req, votable(status=False)))
    assert catalog.stars(0, 0, .1)['completeness']['status'] == 'unknown'

def test_joined_queries_are_bounded_without_global_distinct_ordering(monkeypatch):
    queries=[]
    def tap(query,maxrec):
        queries.append(query)
        return payload([],maxrec=maxrec)
    monkeypatch.setattr(catalog,'_tap',tap)
    catalog.suggest('Barnard');catalog.stars(279.2,38.7,.02)
    assert all('ORDER BY' not in q and 'SELECT DISTINCT' not in q for q in queries)


def test_redirect_to_untrusted_host_is_blocked_before_network(monkeypatch):
    calls = []
    def send(self, request, **kwargs):
        calls.append(request.url)
        return wire_response(request, status=303, headers={'Location': 'https://evil.example/metadata'})
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    with pytest.raises(catalog.CatalogError, match='Untrusted'):
        catalog.resolve('Test')
    assert len(calls) == 1


@pytest.mark.parametrize('declared', [True, False])
def test_response_size_bound(monkeypatch, declared):
    monkeypatch.setattr(catalog, 'MAX_BYTES', 4)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', lambda self, req, **kwargs: wire_response(req, b'too-large', headers={'Content-Length': '9'} if declared else {}))
    with pytest.raises(catalog.CatalogError, match='transfer limit'):
        catalog.resolve('Test')


def test_total_deadline_checked_after_response(monkeypatch):
    monkeypatch.setattr(catalog, 'TOTAL_TIMEOUT', .01)
    def send(self, req, **kwargs):
        time.sleep(.02); return wire_response(req, votable())
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.resolve('Test')
    assert error.value.status == 504
