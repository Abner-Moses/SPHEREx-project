"""Bounded, anonymous IRSA TAP cones; no user endpoint, table, or ADQL input.

Verified against official TAP_SCHEMA on 2026-11-15: gaia_dr3_source (157
columns), allwise_p3as_psd (298). SELECT * deliberately retains the full schema.
Docs: https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html
https://irsa.ipac.caltech.edu/data/Gaia/dr3/gaia_dr3_source_colDescriptions.html
https://irsa.ipac.caltech.edu/data/WISE/docs/release/AllWISE/expsup/sec2_1.html
https://irsa.ipac.caltech.edu/data/WISE/docs/release/AllWISE/faq.html
"""
from collections import OrderedDict, deque
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import threading
import time
from types import MappingProxyType
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import numpy as np
import pyvo
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io.votable import parse

from coordinate_service import CoordinateError, finite_number

TAP_URL = 'https://irsa.ipac.caltech.edu/TAP'
SYNC_URL = TAP_URL + '/sync'
CATALOGS = MappingProxyType({'gaia_dr3': 'gaia_dr3_source', 'allwise': 'allwise_p3as_psd'})
MAX_RADIUS_DEG = .1
MAX_LIMIT = 1000
MAX_BYTES = 16 * 1024 * 1024
TOTAL_TIMEOUT = 25.0
REQUEST_TIMEOUT = 8.0
MAX_INFLIGHT = 2
RATE_REQUESTS = 12
RATE_WINDOW = 60.0
CACHE_TTL = 300.0
MAX_CACHE_ENTRIES = 64
MAX_CACHE_BYTES = 32 * 1024 * 1024
_LOCK = threading.RLock()
_CACHE = OrderedDict()
_FLIGHTS = {}
_STARTS = deque()


class OverlayError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _trusted(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'irsa.ipac.caltech.edu'
            or parts.path != '/TAP/sync' or parts.query or parts.fragment):
        raise OverlayError('Untrusted IRSA TAP endpoint or redirect rejected.')


class _Session(requests.Session):
    """PyVO transport with fixed origin, no credentials, byte and time budgets."""
    def __init__(self):
        super().__init__()
        self.trust_env = False  # No .netrc credentials, environment auth or proxies.
        self.deadline = time.monotonic() + TOTAL_TIMEOUT
        self.hooks['response'].append(self._buffer)

    def remaining(self):
        budget = self.deadline - time.monotonic()
        if budget <= 0:
            raise OverlayError('IRSA TAP total deadline exceeded.', 504)
        return budget

    def send(self, request, **kwargs):
        _trusted(request.url)
        if request.method != 'POST' or 'Authorization' in request.headers or 'Cookie' in request.headers:
            raise OverlayError('Only anonymous IRSA TAP POST queries are permitted.')
        budget = min(REQUEST_TIMEOUT, self.remaining())
        kwargs.update(timeout=(min(3., budget), budget), stream=True, allow_redirects=False)
        return super().send(request, **kwargs)

    def _buffer(self, response, **kwargs):
        try:
            if 300 <= response.status_code < 400:
                raise OverlayError('IRSA TAP redirects are not permitted.')
            length = response.headers.get('Content-Length')
            if length is not None:
                try:
                    length = int(length)
                except ValueError as exc:
                    raise OverlayError('Invalid IRSA TAP content length.') from exc
                if length < 0 or length > MAX_BYTES:
                    raise OverlayError('IRSA TAP exceeds the metadata transfer limit.')
            body = bytearray()
            for block in response.iter_content(65536):
                self.remaining()
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise OverlayError('IRSA TAP exceeds the metadata transfer limit.')
            self.remaining()
        finally:
            response.close()
        response._content = bytes(body)
        response._content_consumed = True
        return response


def _scalar(value):
    """JSON lossless integers, null masks/nonfinite values, array-valued columns."""
    if value is None or value is np.ma.masked:
        return None
    if isinstance(value, np.ndarray):
        return [_scalar(v) for v in value]
    if np.ma.is_masked(value):
        return None
    if isinstance(value, np.generic):
        return _scalar(value.item())
    if isinstance(value, bytes):
        return value.decode('utf8', errors='replace')
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)
    return value


def _decode(raw, maxrec):
    try:
        xml = ET.fromstring(raw)
        if any(e.tag.split('}')[-1] == 'STREAM' and any(k.split('}')[-1] == 'href' for k in e.attrib)
               for e in xml.iter()):
            raise OverlayError('External VOTable data streams are not permitted.')
        infos = [{**e.attrib, 'content': e.text} for e in xml.iter() if e.tag.split('}')[-1] == 'INFO']
        statuses = [i.get('value') for i in infos if i.get('name') == 'QUERY_STATUS']
        if 'ERROR' in statuses:
            raise OverlayError('IRSA TAP declared a query error.')
        vot = parse(io.BytesIO(raw))
        element = vot.get_first_table()
        table = element.to_table(use_names_over_ids=True)
        if len(table) > maxrec:
            raise OverlayError('IRSA TAP exceeded the requested row limit.')
        if not {'ra', 'dec', 'source_id', 'designation'} <= set(table.colnames):
            raise OverlayError('IRSA TAP response is missing verified catalog columns.')
        columns = [{'name': name, 'id': field.ID, 'unit': str(field.unit) if field.unit else None,
                    'description': field.description, 'dtype': str(table[name].dtype),
                    **{key: getattr(field, key, None) for key in
                       ('datatype', 'arraysize', 'ucd', 'utype', 'xtype', 'ref', 'precision', 'width')},
                    'null': field.values.null if field.values else None}
                   for name, field in zip(table.colnames, element.fields)]
        # Keep the original metadata XML (including PARAM/GROUP/COOSYS/TIMESYS,
        # links, field constraints and trailing overflow), without duplicating data.
        for child in xml.iter():
            for node in list(child):
                if node.tag.split('}')[-1] == 'DATA':
                    child.remove(node)
        return {'rows': [{name: _scalar(row[name]) for name in table.colnames} for row in table],
                'columns': columns, 'votable_metadata': {'infos': infos, 'query_status': statuses,
                    'version': vot.version, 'description': element.description,
                    'xml_without_data': ET.tostring(xml, encoding='unicode'),
                    'json_encoding': 'Masked/nonfinite values are null; integers beyond 2^53-1 are exact decimal strings.'}}
    except OverlayError:
        raise
    except (ValueError, TypeError, IndexError, KeyError, ET.ParseError) as exc:
        raise OverlayError('Invalid IRSA TAP VOTable response.') from exc


def _tap(query, maxrec):
    started = time.monotonic()
    try:
        with _Session() as session:
            # PyVO builds TAP protocol parameters; only server-owned ADQL reaches here.
            response = pyvo.dal.TAPService(TAP_URL, session=session).create_query(
                query, maxrec=maxrec).submit()
            if response.status_code == 429:
                raise OverlayError('IRSA TAP rate limited this request; retry later.', 429)
            if not response.ok:
                raise OverlayError(f'IRSA TAP returned HTTP {response.status_code}.')
            raw = response.content
            payload = _decode(raw, maxrec)
            session.remaining()
            payload['provenance'] = {'service_url': SYNC_URL, 'queried_at': datetime.now(timezone.utc).isoformat(),
                'server_date': response.headers.get('Date'), 'query': query,
                'query_sha256': hashlib.sha256(query.encode()).hexdigest(),
                'response_sha256': hashlib.sha256(raw).hexdigest(), 'response_bytes': len(raw),
                'request_s': time.monotonic() - started, 'metadata_only': True, 'credentials_used': False,
                'freshness': 'queried_at is retrieval time, not observation epoch, catalog release time or ingestion time.'}
            return payload
    except requests.Timeout as exc:
        raise OverlayError('IRSA TAP request timed out.', 504) from exc
    except requests.RequestException as exc:
        raise OverlayError('IRSA TAP transport failed.') from exc


def _cached(key, produce):
    with _LOCK:
        now = time.monotonic()
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            _CACHE.move_to_end(key)
            return {**deepcopy(hit[1]), 'cache_hit': True}
        _CACHE.pop(key, None)
        future = _FLIGHTS.get(key)
        owner = future is None
        if owner:
            if len(_FLIGHTS) >= MAX_INFLIGHT:
                raise OverlayError('Overlay concurrency capacity is occupied; retry later.', 503)
            while _STARTS and now - _STARTS[0] >= RATE_WINDOW:
                _STARTS.popleft()
            if len(_STARTS) >= RATE_REQUESTS:
                raise OverlayError('Overlay request rate exceeded; retry later.', 429)
            _STARTS.append(now)
            future = _FLIGHTS[key] = Future()
    if not owner:
        try:
            return {**deepcopy(future.result(timeout=TOTAL_TIMEOUT + 1)), 'inflight_reused': True}
        except FutureTimeout as exc:
            raise OverlayError('Shared overlay query deadline exceeded.', 504) from exc
    try:
        result = {**produce(), 'cache_hit': False}
        size = len(json.dumps(result, allow_nan=False).encode())
        with _LOCK:
            if size <= MAX_CACHE_BYTES:
                _CACHE[key] = (time.monotonic(), deepcopy(result), size)
                while len(_CACHE) > MAX_CACHE_ENTRIES or sum(x[2] for x in _CACHE.values()) > MAX_CACHE_BYTES:
                    _CACHE.popitem(last=False)
        future.set_result(deepcopy(result))
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _LOCK:
            _FLIGHTS.pop(key, None)


def overlay(catalog, ra, dec, radius=.01, limit=1000):
    """Exact cone at native catalog positions, capped at .1 degrees/1000 rows.

    Cache/rate/concurrency bounds apply per process; parent deployments with
    multiple workers should apply their usual shared ingress rate limit as well.
    """
    if not isinstance(catalog, str) or catalog not in CATALOGS:
        raise OverlayError('catalog must be gaia_dr3 or allwise.', 422)
    try:
        ra = finite_number(ra, 'ra', 0, 360, upper_open=True)
        dec = finite_number(dec, 'dec', -90, 90)
        radius = finite_number(radius, 'radius', 0, MAX_RADIUS_DEG)
    except CoordinateError as exc:
        raise OverlayError(str(exc), 422) from exc
    if radius <= 0:
        raise OverlayError('radius must be greater than zero and at most .1 degrees.', 422)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise OverlayError('limit must be an integer from 1 to 1000.', 422)
    table = CATALOGS[catalog]
    maxrec = limit + 1  # One sentinel distinguishes a full page from exhaustion.
    query = (f'SELECT TOP {maxrec} * FROM {table} WHERE '
             f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.17g}, {dec:.17g}, {radius:.17g})) = 1")

    def produce():
        result = _tap(query, maxrec)
        rows = result['rows']
        # Recheck the true spherical cone, including the RA seam and polar caps.
        if rows:
            try:
                ras = [finite_number(r['ra'], 'catalog ra', 0, 360, upper_open=True) for r in rows]
                decs = [finite_number(r['dec'], 'catalog dec', -90, 90) for r in rows]
                distances = SkyCoord(ra*u.deg, dec*u.deg).separation(SkyCoord(ras*u.deg, decs*u.deg)).deg
                if np.any(distances > np.nextafter(radius, math.inf)):
                    raise OverlayError('IRSA returned a source outside the requested exact cone.')
            except CoordinateError as exc:
                raise OverlayError('IRSA returned invalid source coordinates.') from exc
        statuses = result['votable_metadata']['query_status']
        overflow = 'OVERFLOW' in statuses
        more = len(rows) > limit or overflow
        status = 'truncated' if more else 'complete' if statuses and all(s == 'OK' for s in statuses) and len(rows) < maxrec else 'unknown'
        epochs = ({'reference_epoch_column': 'ref_epoch', 'reference_epoch_jyear': 2016.0, 'scale': 'tcb'}
                  if catalog == 'gaia_dr3' else
                  {'position_model': 'stationary fit across multiple exposures; J2000 is the coordinate equinox, not observation epoch',
                   'mean_observation_mjd_columns': [f'w{i}mjdmean' for i in range(1, 5)],
                   'motion_fit_position_columns': ['ra_pm', 'dec_pm'], 'motion_fit_epoch_mjd': 55400.0, 'scale': 'utc'})
        selected = rows[:limit]
        objects = [{'ra': row['ra'], 'dec': row['dec'],
                    'name': str(row.get('designation') or row.get('source_id') or catalog),
                    'catalog': catalog, 'catalog_id': row.get('source_id'), 'raw': deepcopy(row)}
                   for row in selected]
        return {**result, 'rows': selected, 'objects': objects, 'catalog': catalog,
            'query': {'ra': ra, 'dec': dec, 'radius_deg': radius, 'limit': limit, 'frame': 'icrs', 'table': table},
            'completeness': {'status': status, 'overflow': overflow, 'has_more': more,
                'returned': min(len(rows), limit), 'archive_rows': len(rows), 'maxrec': maxrec,
                'scope': 'Native catalog cone only; no nearest-first ordering or complete astronomical census is implied.'},
            'catalog_epoch': epochs,
            'epoch_disclaimer': 'Gaia DR3, AllWISE, SIMBAD J2000 and SPHEREx observation epochs differ. Positions are not propagated or crossmatched; proper motion, parallax, blending and sensitivity can cause offsets or missing matches.',
            'provenance': {**result['provenance'], 'catalog': catalog, 'table': table,
                'schema_verified_on': '2026-11-15', 'documentation_url': 'https://irsa.ipac.caltech.edu/docs/program_interface/TAP.html'},
            'limits': {'max_radius_deg': MAX_RADIUS_DEG, 'max_rows': MAX_LIMIT, 'max_response_bytes': MAX_BYTES,
                       'total_timeout_s': TOTAL_TIMEOUT, 'request_timeout_s': REQUEST_TIMEOUT,
                       'concurrency_per_process': MAX_INFLIGHT, 'requests_per_window': RATE_REQUESTS,
                       'rate_window_s': RATE_WINDOW, 'cache_ttl_s': CACHE_TTL}}

    # No coordinate rounding or cone enlargement is allowed in the cache key.
    return _cached((catalog, ra, dec, radius, limit), produce)
