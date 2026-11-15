"""Offline TAP wire, metadata, bounded admission, cache and router tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
import threading
from urllib.parse import parse_qs

import numpy as np
import pytest
import requests
from astropy.io.votable import from_table, writeto
from astropy.table import MaskedColumn, Table
from fastapi import FastAPI
from fastapi.testclient import TestClient
from urllib3.response import HTTPResponse

import catalog_overlays as overlays
from coordinate_api import router


def votable(positions=((10., 20.),), statuses=('OK',)):
    table = Table({'source_id': [9007199254740993 + i for i, _ in enumerate(positions)],
                   'designation': ['real-field-' + str(i) for i, _ in enumerate(positions)],
                   'ra': [p[0] for p in positions], 'dec': [p[1] for p in positions],
                   'ref_epoch': [2016.] * len(positions), 'extra_science_column': [1.5] * len(positions)})
    table['extra_science_column'] = MaskedColumn(table['extra_science_column'], mask=[True] * len(positions))
    table['ra'].unit = 'deg'; table['dec'].unit = 'deg'; table['ref_epoch'].unit = 'yr'
    table['extra_science_column'].unit = 'mag'
    table['extra_science_column'].description = 'Preserved science metadata'
    vot = from_table(table)
    out = io.BytesIO(); writeto(vot, out)
    raw = out.getvalue()
    infos = ''.join(f'<INFO name="QUERY_STATUS" value="{s}"/>' for s in statuses).encode()
    return raw.replace(b'</RESOURCE>', infos + b'</RESOURCE>')


def response(request, body, status=200, headers=None):
    result = requests.Response()
    result.status_code = status; result.url = request.url; result.request = request
    result.headers.update(headers or {})
    result.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False)
    return result


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    overlays._CACHE.clear(); overlays._FLIGHTS.clear(); overlays._STARTS.clear()
    def blocked(*args, **kwargs):
        pytest.fail('Unit test attempted a live HTTP request')
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', blocked)
    yield
    assert not overlays._FLIGHTS
    overlays._CACHE.clear(); overlays._STARTS.clear()


@pytest.fixture
def client():
    app = FastAPI(); app.include_router(router)
    return TestClient(app)


def wire(monkeypatch, body=None, status=200, headers=None):
    calls = []
    def send(adapter, request, **kwargs):
        calls.append((request, kwargs))
        return response(request, votable() if body is None else body, status, headers)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    return calls


@pytest.mark.parametrize('catalog,table', [('gaia_dr3', 'gaia_dr3_source'), ('allwise', 'allwise_p3as_psd')])
def test_fixed_pyvo_tap_protocol_all_columns_units_and_provenance(monkeypatch, catalog, table):
    calls = wire(monkeypatch, headers={'Date': 'Sun, 15 Nov 2026 00:00:00 GMT'})
    result = overlays.overlay(catalog, 10, 20, .01, 10)
    request, options = calls[0]
    assert request.url == overlays.SYNC_URL and request.method == 'POST'
    assert 'Authorization' not in request.headers and 'Cookie' not in request.headers
    params = parse_qs(request.body)
    query = params['QUERY'][0]
    assert f'SELECT TOP 11 * FROM {table}' in query
    assert "CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', 10, 20," in query
    assert params['MAXREC'] == ['11'] and params['LANG'] == ['ADQL']
    assert options['timeout'] == (3., 8.)
    assert result['rows'][0]['source_id'] == '9007199254740993'
    obj = result['objects'][0]
    assert obj == {'ra': 10., 'dec': 20., 'name': 'real-field-0', 'catalog': catalog,
                   'catalog_id': '9007199254740993', 'raw': result['rows'][0]}
    assert result['rows'][0]['extra_science_column'] is None
    columns = {c['name']: c for c in result['columns']}
    assert columns['ra']['unit'] == 'deg' and columns['extra_science_column']['unit'] == 'mag'
    assert columns['extra_science_column']['description'] == 'Preserved science metadata'
    assert '<' in result['votable_metadata']['xml_without_data']
    assert 'TABLEDATA' not in result['votable_metadata']['xml_without_data']
    assert result['provenance']['response_sha256'] and result['provenance']['query_sha256']
    assert result['provenance']['queried_at'].endswith('+00:00')
    assert result['completeness']['status'] == 'complete'
    assert 'epochs differ' in result['epoch_disclaimer']
    assert result['catalog_epoch']
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('statuses,count,expected', [(('OK',), 0, 'complete'), (('OK',), 1, 'complete'),
    (('OK',), 2, 'truncated'), (('OK', 'OVERFLOW'), 1, 'truncated'), ((), 1, 'unknown'), (('OTHER',), 1, 'unknown')])
def test_limits_trailing_overflow_empty_and_unknown(monkeypatch, statuses, count, expected):
    wire(monkeypatch, votable(tuple((10., 20.) for _ in range(count)), statuses))
    result = overlays.overlay('gaia_dr3', 10, 20, .01, 1)
    assert len(result['rows']) <= 1
    assert result['completeness']['status'] == expected
    assert result['completeness']['archive_rows'] == count
    assert result['completeness']['overflow'] == ('OVERFLOW' in statuses)


@pytest.mark.parametrize('kwargs', [{'catalog': 'https://evil.test'}, {'catalog': 'gaia_dr3_source'}, {'catalog': {}},
    {'ra': True}, {'dec': np.bool_(True)}, {'ra': float('nan')}, {'dec': float('inf')}, {'ra': '10'},
    {'ra': 360}, {'ra': -1}, {'dec': -91}, {'radius': 0}, {'radius': .100001}, {'radius': True},
    {'limit': True}, {'limit': 1.5}, {'limit': 1001}, {'limit': 0}])
def test_strict_service_parameters_never_reach_network(kwargs):
    with pytest.raises(overlays.OverlayError) as error:
        overlays.overlay(**{'catalog': 'gaia_dr3', 'ra': 10, 'dec': 20, **kwargs})
    assert error.value.status == 422


@pytest.mark.parametrize('params', [{'ra': 'nan'}, {'ra': 'true'}, {'dec': 'inf'}, {'radius': '.11'}, {'radius': '0'},
    {'limit': 'true'}, {'limit': '1.0'}, {'limit': '1001'}, {'limit': '9'*5000}, {'url': 'https://evil.test'}, {'query': 'SELECT *'}, {'catalog': 'gaia_dr3_source'}])
def test_router_rejects_invalid_parameters(client, params):
    result = client.get('/api/catalog/overlay', params={'catalog': 'gaia_dr3', 'ra': 10, 'dec': 20, **params})
    assert result.status_code == 422


def test_router_success_and_error_translation(client, monkeypatch):
    wire(monkeypatch)
    assert client.get('/api/catalog/overlay?catalog=gaia_dr3&ra=10&dec=20&limit=10').status_code == 200
    def fail(*args):
        raise overlays.OverlayError('bounded timeout', 504)
    monkeypatch.setattr(overlays, 'overlay', fail)
    assert client.get('/api/catalog/overlay?catalog=allwise&ra=10&dec=20').status_code == 504
    assert client.get('/api/catalog/overlay?catalog=allwise&ra=10&ra=11&dec=20').status_code == 422


@pytest.mark.parametrize('url', ['http://irsa.ipac.caltech.edu/TAP/sync', 'https://evil.test/TAP/sync',
    'https://irsa.ipac.caltech.edu.evil.test/TAP/sync', 'https://user:pass@irsa.ipac.caltech.edu/TAP/sync',
    'https://irsa.ipac.caltech.edu/TAP/async', 'https://irsa.ipac.caltech.edu/TAP/sync?x=1',
    'https://irsa.ipac.caltech.edu:444/TAP/sync', 'https://irsa.ipac.caltech.edu/TAP/sync#fragment'])
def test_untrusted_origins_and_paths_rejected_before_transport(url):
    with overlays._Session() as session, pytest.raises(overlays.OverlayError):
        session.post(url)


@pytest.mark.parametrize('body,status,headers,expected', [(b'', 302, {'Location': 'https://evil.test'}, 502),
    (b'', 429, {}, 429), (b'', 503, {}, 502), (b'not XML', 200, {}, 502),
    (votable(statuses=('OK', 'ERROR')), 200, {}, 502),
    (votable(), 200, {'Content-Length': str(overlays.MAX_BYTES + 1)}, 502),
    (votable(), 200, {'Content-Length': 'garbage'}, 502)])
def test_failures_are_not_cached(monkeypatch, body, status, headers, expected):
    calls = wire(monkeypatch, body, status, headers)
    for _ in range(2):
        with pytest.raises(overlays.OverlayError) as exc:
            overlays.overlay('gaia_dr3', 10, 20)
        assert exc.value.status == expected
    assert len(calls) == 2 and not overlays._CACHE


def test_byte_bound_without_content_length_and_read_timeout(monkeypatch):
    monkeypatch.setattr(overlays, 'MAX_BYTES', 32)
    wire(monkeypatch, b'X'*33)
    with pytest.raises(overlays.OverlayError, match='transfer limit'):
        overlays.overlay('gaia_dr3', 10, 20)
    def timeout(*args, **kwargs):
        raise requests.ReadTimeout('timeout')
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', timeout)
    with pytest.raises(overlays.OverlayError) as exc:
        overlays.overlay('allwise', 10, 20)
    assert exc.value.status == 504


def test_total_deadline(monkeypatch):
    with overlays._Session() as session:
        session.deadline = 0
        with pytest.raises(overlays.OverlayError) as exc:
            session.post(overlays.SYNC_URL)
    assert exc.value.status == 504


@pytest.mark.parametrize('ra,dec,points', [(359.999, 0, ((.001, 0.),)), (123, 90, ((300., 89.999),)),
                                         (0, -90, ((180., -89.999),))])
def test_exact_spherical_cone_wrap_and_poles(monkeypatch, ra, dec, points):
    wire(monkeypatch, votable(points))
    assert overlays.overlay('allwise', ra, dec, .01)['rows']


def test_outside_cone_and_upstream_row_bound_rejected(monkeypatch):
    wire(monkeypatch, votable(((10.02, 20.),)))
    with pytest.raises(overlays.OverlayError, match='outside'):
        overlays.overlay('allwise', 10, 20, .01)
    wire(monkeypatch, votable(((10., 20.),)*3))
    with pytest.raises(overlays.OverlayError, match='row limit'):
        overlays.overlay('gaia_dr3', 10, 20, .01, 1)


def test_exact_cache_keys_copy_isolation_and_expiry(monkeypatch):
    calls = wire(monkeypatch)
    first = overlays.overlay('gaia_dr3', 10, 20)
    first['rows'][0]['ra'] = 999
    hit = overlays.overlay('gaia_dr3', 10, 20)
    assert hit['cache_hit'] and hit['rows'][0]['ra'] == 10
    assert not overlays.overlay('gaia_dr3', 10 + 1e-10, 20)['cache_hit']
    monkeypatch.setattr(overlays, 'CACHE_TTL', 0)
    assert not overlays.overlay('gaia_dr3', 10, 20)['cache_hit']
    assert len(calls) == 3


def test_cache_count_and_byte_budgets(monkeypatch):
    wire(monkeypatch)
    monkeypatch.setattr(overlays, 'MAX_CACHE_ENTRIES', 1)
    overlays.overlay('gaia_dr3', 10, 20); overlays.overlay('allwise', 10, 20)
    assert len(overlays._CACHE) == 1
    monkeypatch.setattr(overlays, 'MAX_CACHE_BYTES', 1)
    overlays._CACHE.clear()
    overlays.overlay('gaia_dr3', 10, 20)
    assert not overlays._CACHE


def test_rate_applies_to_unique_starts_but_not_cache(monkeypatch):
    wire(monkeypatch)
    monkeypatch.setattr(overlays, 'RATE_REQUESTS', 1)
    overlays.overlay('gaia_dr3', 10, 20)
    assert overlays.overlay('gaia_dr3', 10, 20)['cache_hit']
    with pytest.raises(overlays.OverlayError) as exc:
        overlays.overlay('allwise', 10, 20)
    assert exc.value.status == 429
    monkeypatch.setattr(overlays, 'RATE_WINDOW', 0)
    assert overlays.overlay('allwise', 10, 20)


def test_deduplication_and_concurrency_admission(monkeypatch):
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    calls = []
    data = overlays._decode(votable(), 1001)
    data['provenance'] = {}
    def tap(*args):
        calls.append(args); entered.set()
        assert release.wait(3)
        return deepcopy(data)
    original = overlays.Future.result
    def observed(future, *args, **kwargs):
        waiting.set()
        return original(future, *args, **kwargs)
    monkeypatch.setattr(overlays.Future, 'result', observed)
    monkeypatch.setattr(overlays, '_tap', tap)
    monkeypatch.setattr(overlays, 'MAX_INFLIGHT', 1)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(overlays.overlay, 'gaia_dr3', 10, 20)
        assert entered.wait(2)
        second = pool.submit(overlays.overlay, 'gaia_dr3', 10, 20)
        assert waiting.wait(2)
        try:
            with pytest.raises(overlays.OverlayError) as exc:
                overlays.overlay('allwise', 10, 20)
            assert exc.value.status == 503
        finally:
            release.set()
        assert not first.result()['cache_hit']
        assert second.result()['inflight_reused']
    assert len(calls) == 1


def test_scalar_arrays_masks_nonfinite_and_exact_ids():
    assert overlays._scalar(np.ma.array([1., np.nan], mask=[False, True])) == [1., None]
    assert overlays._scalar(np.int64(9007199254740993)) == '9007199254740993'
    assert overlays._scalar(np.float64(np.inf)) is None


def test_votable_cannot_redirect_astropy_to_external_data():
    raw = b'<VOTABLE><RESOURCE><TABLE><DATA><BINARY><STREAM href="file:///etc/passwd"/></BINARY></DATA></TABLE></RESOURCE></VOTABLE>'
    with pytest.raises(overlays.OverlayError, match='External VOTable'):
        overlays._decode(raw, 1001)
