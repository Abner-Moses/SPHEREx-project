"""Bounded SIMBAD TAP metadata, exact-query caches and ICRS/J2000 astrometry.

No IRSA pipeline, product downloads, user-supplied service URLs or disk cache.
SIMBAD pmra is mu_alpha*cos(dec); original errors/quality/references are retained.
"""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import re
import threading
import time
from urllib.parse import urlsplit
import warnings
import xml.etree.ElementTree as ET

import numpy as np
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io.votable import parse
from astropy.time import Time

ENDPOINTS = (
    'https://simbad.cfa.harvard.edu/simbad/sim-tap/sync',
    'https://simbad.harvard.edu/simbad/sim-tap/sync',
    'https://simbad.cds.unistra.fr/simbad/sim-tap/sync',
)
TOTAL_TIMEOUT = 20.0
REQUEST_TIMEOUT = 8.0
MAX_BYTES = 8 * 1024 * 1024
CACHE_TTL = 300.0
MAX_CACHE_ENTRIES = 128
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_INFLIGHT = 4
_LOCK = threading.RLock()
_CACHE = OrderedDict()
_FLIGHTS = {}
FIELDS = ('oid', 'main_id', 'ra', 'dec', 'pmra', 'pmdec', 'pm_err_maj', 'pm_err_min',
          'pm_err_angle', 'plx_value', 'plx_err', 'coo_err_maj', 'coo_err_min',
          'coo_err_angle', 'coo_qual', 'pm_qual', 'plx_qual', 'coo_bibcode',
          'pm_bibcode', 'plx_bibcode', 'rvz_radvel', 'otype')
SELECT = 'SELECT DISTINCT ' + ', '.join('b.' + key for key in FIELDS) + ', f.V AS vmag FROM basic AS b LEFT JOIN allfluxes AS f ON b.oid = f.oidref'


class CatalogError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def finite(value, name, minimum=None, maximum=None, exclusive_min=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)):
        raise CatalogError(f'{name} must be a finite number, not a boolean.', 422)
    value = float(value)
    if (not math.isfinite(value) or minimum is not None and (value < minimum or exclusive_min and value == minimum)
            or maximum is not None and value > maximum):
        raise CatalogError(f'{name} is outside its finite allowed range.', 422)
    return value


def integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CatalogError(f'{name} must be an integer between {low} and {high}.', 422)
    return value


def epoch_value(epoch):
    return None if epoch is None else finite(epoch, 'epoch (Julian year)', 1800, 2200)


def _name(value, prefix=False):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CatalogError('Identifier must be text without control characters.', 422)
    value = ' '.join(value.split())
    if not (2 if prefix else 1) <= len(value) <= 100:
        raise CatalogError('Identifier must contain 1–100 characters (prefix at least 2).', 422)
    if prefix and any(c in value for c in '%_\\'):
        raise CatalogError('Suggestion prefixes do not accept wildcard characters % _ or backslash.', 422)
    return value


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _identifiers(value):
    """Exact documented identifiers/common-name aliases, never a fuzzy resolve."""
    candidates = [value]
    messier = re.fullmatch(r'[mM]\s*0*(\d{1,3})', value)
    if messier:
        candidates.append(f'M {int(messier[1]):3d}')
    if not value.upper().startswith('NAME '):
        candidates.extend(['NAME ' + value, 'NAME ' + value.title()])
    if not value.startswith(('V* ', '* ')):
        candidates.extend(['V* ' + value, '* ' + value])
    return list(dict.fromkeys(candidates))


def _trusted(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) or parts.fragment
            or parts.hostname not in {urlsplit(endpoint).hostname for endpoint in ENDPOINTS}
            or parts.path != '/simbad/sim-tap/sync'):
        raise CatalogError('Untrusted SIMBAD endpoint/redirect rejected.')


class _Session(requests.Session):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline
        self.max_redirects = 3
        self.hooks['response'].append(self._buffer)

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CatalogError('SIMBAD total deadline exceeded.', 504)
        return remaining

    def send(self, request, **kwargs):
        _trusted(request.url)  # Also executed before following each redirect.
        budget = min(REQUEST_TIMEOUT, self.remaining())
        kwargs['timeout'] = (min(3.0, budget), budget)
        kwargs['stream'] = True
        return super().send(request, **kwargs)

    def _buffer(self, response, **kwargs):
        try:
            length = response.headers.get('Content-Length')
            if length is not None and int(length) > MAX_BYTES:
                raise CatalogError('SIMBAD metadata exceeds transfer limit.')
            body = bytearray()
            for block in response.iter_content(65536):
                self.remaining()
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise CatalogError('SIMBAD metadata exceeds transfer limit.')
            self.remaining()
        finally:
            response.close()
        response._content = bytes(body)
        response._content_consumed = True
        return response


def _scalar(value):
    if value is None or np.ma.is_masked(value):
        return None
    if isinstance(value, np.generic):
        return _scalar(value.item())
    if isinstance(value, bytes):
        return value.decode('utf8', errors='replace').strip()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _tap(query, maxrec):
    # MAXREC bounds serialization, but does not reliably push a SQL LIMIT
    # through SIMBAD's DISTINCT/ORDER BY plans. Bound execution explicitly too.
    if query.startswith('SELECT DISTINCT '):
        query=query.replace('SELECT DISTINCT ',f'SELECT DISTINCT TOP {int(maxrec)} ',1)
    elif query.startswith('SELECT '):
        query=query.replace('SELECT ',f'SELECT TOP {int(maxrec)} ',1)
    started = time.monotonic()
    errors = []
    with _Session(started + TOTAL_TIMEOUT) as session:
        for attempt, endpoint in enumerate(ENDPOINTS):
            try:
                response = session.get(endpoint, params={'REQUEST': 'doQuery', 'LANG': 'ADQL',
                    'FORMAT': 'votable', 'QUERY': query, 'MAXREC': str(maxrec)}, allow_redirects=True)
                if response.status_code in (404, 405, 429, 500, 502, 503, 504):
                    errors.append(f'{urlsplit(endpoint).hostname}: HTTP {response.status_code}')
                    continue
                if not response.ok:
                    raise CatalogError(f'SIMBAD returned HTTP {response.status_code}.')
                raw = response.content
                infos = [{'name': e.get('name'), 'value': e.get('value'), 'content': e.text}
                         for e in ET.fromstring(raw).iter() if e.tag.split('}')[-1] == 'INFO']
                statuses = [item['value'] for item in infos if item['name'] == 'QUERY_STATUS']
                if 'ERROR' in statuses:
                    raise CatalogError('SIMBAD rejected the TAP query: ' + str(infos))
                table = parse(io.BytesIO(raw)).get_first_table().to_table(use_names_over_ids=True)
                if len(table) > maxrec:
                    raise CatalogError('SIMBAD exceeded the requested row bound.')
                rows = [{name: _scalar(row[name]) for name in table.colnames} for row in table]
                session.remaining()
                provenance = {'catalog': 'SIMBAD', 'service_url': response.url.split('?')[0],
                    'queried_at': datetime.now(timezone.utc).isoformat(), 'server_date': response.headers.get('Date'),
                    'query': query, 'query_sha256': hashlib.sha256(query.encode()).hexdigest(),
                    'response_sha256': hashlib.sha256(raw).hexdigest(), 'query_status': statuses,
                    'metadata_only': True, 'frame': 'icrs', 'reference_epoch': 'J2000',
                    'pmra_convention': 'mu_alpha_cosdec',
                    'freshness': 'Query time is not catalog ingestion time; mirrors can lag. See https://simbad.cds.unistra.fr/',
                    'mirror_snapshot_note': 'Official notice dated 2026-11-09 reports Harvard data ingested through 2026-10-14; this is not a live ingestion check.' if 'harvard.edu' in response.url else None,
                    'request_s': time.monotonic() - started, 'prior_attempt_errors': errors}
                return {'rows': rows, 'provenance': provenance, 'maxrec': maxrec}
            except requests.RequestException as error:
                errors.append(f'{urlsplit(endpoint).hostname}: {type(error).__name__}')
                if attempt + 1 < len(ENDPOINTS):
                    session.remaining()
            except (ET.ParseError, ValueError, IndexError) as error:
                raise CatalogError('Invalid SIMBAD VOTable response.') from error
    raise CatalogError('SIMBAD unavailable after bounded mirror retries: ' + '; '.join(errors))


def _cached(key, produce):
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            _CACHE.move_to_end(key)
            result = deepcopy(hit[1]); result['cache_hit'] = True
            return result
        if hit:
            _CACHE.pop(key)
        future = _FLIGHTS.get(key)
        owner = future is None
        if owner:
            if len(_FLIGHTS) >= MAX_INFLIGHT:
                raise CatalogError('Catalog query capacity is occupied; retry later.', 503)
            future = _FLIGHTS[key] = Future()
    if not owner:
        try:
            result = deepcopy(future.result(timeout=TOTAL_TIMEOUT + 1))
            result['inflight_reused'] = True
            return result
        except FutureTimeout as error:
            raise CatalogError('Shared SIMBAD query deadline exceeded.', 504) from error
    try:
        result = produce()
        result['cache_hit'] = False
        size = len(json.dumps(result, allow_nan=False).encode())
        with _LOCK:
            if size <= MAX_CACHE_BYTES:
                _CACHE[key] = (time.monotonic(), deepcopy(result), size)
                while len(_CACHE) > MAX_CACHE_ENTRIES or sum(entry[2] for entry in _CACHE.values()) > MAX_CACHE_BYTES:
                    _CACHE.popitem(last=False)
        future.set_result(deepcopy(result))
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _LOCK:
            _FLIGHTS.pop(key, None)


def _number(row, key, nonnegative=False):
    value = row.get(key)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and (not nonnegative or value >= 0) else None


def _object(row, provenance):
    ra, dec = _number(row, 'ra'), _number(row, 'dec')
    name = row.get('main_id')
    if not isinstance(name, str) or not name.strip() or ra is None or dec is None or not 0 <= ra < 360 or not -90 <= dec <= 90:
        raise CatalogError('SIMBAD returned an invalid object position/identifier.')
    parallax, error = _number(row, 'plx_value'), _number(row, 'plx_err', True)
    snr = parallax / error if parallax is not None and error is not None and error > 0 else None
    if snr is not None and not math.isfinite(snr):
        snr = None
    quality = ('unavailable' if parallax is None else 'nonpositive_parallax' if parallax <= 0
               else 'unknown_uncertainty' if snr is None else 'high_snr_proxy' if snr >= 5 else 'low_snr')
    distance = 1000 / parallax if quality == 'high_snr_proxy' else None
    if distance is not None and not math.isfinite(distance):
        distance = None; quality = 'nonfinite_proxy'
    major, minor, angle = (_number(row, key, True) for key in ('pm_err_maj', 'pm_err_min', 'pm_err_angle'))
    pmra_error = pmdec_error = None
    if major is not None and minor is not None and angle is not None and major >= minor:
        sine, cosine = math.sin(math.radians(angle)), math.cos(math.radians(angle))
        pmra_error = math.hypot(major * sine, minor * cosine)
        pmdec_error = math.hypot(major * cosine, minor * sine)
    object_provenance = {key: deepcopy(provenance.get(key)) for key in
        ('catalog', 'service_url', 'queried_at', 'query_sha256', 'freshness', 'mirror_snapshot_note', 'pmra_convention')}
    object_provenance.update(references={key: row.get(key) for key in ('coo_bibcode', 'pm_bibcode', 'plx_bibcode')},
                             quality_codes={key: row.get(key) for key in ('coo_qual', 'pm_qual', 'plx_qual')})
    return {'name': name.strip(), 'catalog_id': str(row.get('oid')) if row.get('oid') is not None else None,
        'ra': ra, 'dec': dec, 'frame': 'icrs', 'epoch': 'J2000', 'epoch_scale': 'tt',
        'pmra_mas_yr': _number(row, 'pmra'), 'pmdec_mas_yr': _number(row, 'pmdec'),
        'pmra_error_mas_yr': pmra_error, 'pmdec_error_mas_yr': pmdec_error,
        'proper_motion_error_ellipse': {'major_mas_yr': major, 'minor_mas_yr': minor,
            'position_angle_deg': angle, 'axis_convention': 'mu_alpha_cosdec, mu_delta; PA east of north'},
        'coordinate_error_ellipse': {'major_mas': _number(row, 'coo_err_maj', True),
            'minor_mas': _number(row, 'coo_err_min', True), 'position_angle_deg': _number(row, 'coo_err_angle', True)},
        'parallax_mas': parallax, 'parallax_error_mas': error,
        'distance_estimate': {'value_pc': distance,
            'quality': quality, 'parallax_snr': snr, 'method': 'inverse_parallax_proxy',
            'limitation': 'Not a posterior distance; no parallax zero-point correction. No estimate for low/unknown SNR or nonpositive parallax.'},
        'radial_velocity_km_s': _number(row, 'rvz_radvel'), 'otype': row.get('otype'),
        'vmag': _number(row, 'vmag'), 'provenance': object_provenance}


def _propagate(obj, epoch):
    if epoch is None:
        return obj
    base = {'epoch': epoch, 'epoch_format': 'jyear', 'time_scale': 'tt', 'frame': 'icrs',
            'annual_parallax_included': False, 'uncertainty_propagated': False,
            'method': 'astropy.SkyCoord.apply_space_motion', 'ra': None, 'dec': None}
    if obj['pmra_mas_yr'] is None or obj['pmdec_mas_yr'] is None:
        obj['propagated_position'] = {**base, 'status': 'unavailable', 'reason': 'Both proper-motion components are required; unknown motion is not zero motion.'}
        return obj
    distance = obj['distance_estimate']['value_pc']
    rv = obj['radial_velocity_km_s']
    args = dict(ra=obj['ra'] * u.deg, dec=obj['dec'] * u.deg,
        pm_ra_cosdec=obj['pmra_mas_yr'] * u.mas/u.yr, pm_dec=obj['pmdec_mas_yr'] * u.mas/u.yr,
        frame='icrs', obstime=Time(2000., format='jyear', scale='tt'))
    if distance is not None:
        args['distance'] = distance * u.pc
        if rv is not None:
            args['radial_velocity'] = rv * u.km/u.s
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        predicted = SkyCoord(**args).apply_space_motion(new_obstime=Time(epoch, format='jyear', scale='tt'))
    obj['propagated_position'] = {**base, 'status': 'available', 'ra': float(predicted.ra.deg) % 360,
        'dec': float(predicted.dec.deg), 'distance_used_pc': distance,
        'perspective_acceleration_included': distance is not None and rv is not None,
        'warnings': [str(item.message) for item in caught],
        'limitation': 'Barycentric prediction, not an apparent observer position. Catalog errors/covariances are not propagated; perspective acceleration requires distance and radial velocity.'}
    return obj


def _objects(payload, epoch):
    objects = [_propagate(_object(row, payload['provenance']), epoch) for row in payload['rows']]
    return objects


def _complete(payload, returned, limit):
    statuses = payload['provenance']['query_status']
    overflow = 'OVERFLOW' in statuses
    more = len(payload['rows']) > limit or overflow
    status = 'truncated' if more else 'complete' if statuses and all(s == 'OK' for s in statuses) and len(payload['rows']) < payload['maxrec'] else 'unknown'
    return {'status': status, 'overflow': overflow, 'has_more': more, 'returned': returned,
            'archive_rows': len(payload['rows']), 'maxrec': payload['maxrec'],
            'scope': 'SIMBAD catalog membership at reference epoch J2000 under supplied filters; not a complete census of stars or predicted-epoch membership.'}


def resolve(name, epoch=None):
    name, epoch = _name(name), epoch_value(epoch)
    query = SELECT + ' JOIN ident AS i ON b.oid = i.oidref WHERE i.id IN (' + ', '.join(_literal(s) for s in _identifiers(name)) + ')'
    payload = _cached(('resolve', query), lambda: _tap(query, 3))
    if not payload['rows']:
        if _complete(payload, 0, 2)['status'] != 'complete':
            raise CatalogError('SIMBAD returned no match with unknown completeness.')
        raise CatalogError('SIMBAD identifier not found. Use an exact catalog identifier or a common NAME alias.', 404)
    objects = _objects(payload, epoch)
    if len(objects) != 1 or 'OVERFLOW' in payload['provenance']['query_status']:
        raise CatalogError('Identifier is ambiguous; choose a more specific catalog identifier.', 409)
    obj = objects[0]
    obj['provenance'].update(query=payload['provenance']['query'], response_sha256=payload['provenance']['response_sha256'])
    obj['cache_hit'] = payload.get('cache_hit', False)
    return obj


def suggest(prefix, limit=8, epoch=None):
    prefix, limit, epoch = _name(prefix, True), integer(limit, 'limit', 1, 20), epoch_value(epoch)
    variants = _identifiers(prefix)
    predicate = ' OR '.join('i.id LIKE ' + _literal(value + '%') for value in variants)
    # DISTINCT+ORDER BY on this joined wildcard query can force a global sort
    # before applying a service limit. Fetch a bounded candidate page, then
    # deduplicate and sort locally. Completeness remains explicit.
    query = SELECT.replace('SELECT DISTINCT ','SELECT ',1) + ' JOIN ident AS i ON b.oid = i.oidref WHERE (' + predicate + ')'
    payload = _cached(('suggest', query, limit), lambda: _tap(query, limit * 4 + 1))
    unique={}
    for obj in _objects(payload,epoch):
        unique.setdefault(obj.get('catalog_id') or (obj['name'],obj['ra'],obj['dec']),obj)
    objects = sorted(unique.values(),key=lambda obj:obj['name'].casefold())[:limit]
    return {'objects': objects, 'completeness': _complete(payload, len(objects), limit),
            'provenance': payload['provenance'], 'cache_hit': payload.get('cache_hit', False)}


def stars(ra, dec, radius, mag_limit=17., limit=1000, epoch=None):
    ra, dec = finite(ra, 'ra', 0, 360), finite(dec, 'dec', -90, 90)
    if ra == 360:
        raise CatalogError('ra must be below 360 degrees.', 422)
    radius = finite(radius, 'radius', 0, 5, True)
    mag_limit, limit, epoch = finite(mag_limit, 'mag_limit', -30, 25), integer(limit, 'limit', 1, 4000), epoch_value(epoch)
    query = (SELECT.replace('SELECT DISTINCT ','SELECT ',1) + f" WHERE CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra:.17g}, {dec:.17g}, {radius:.17g})) = 1"
             + f" AND b.otype = 'Star..' AND f.V <= {mag_limit:.17g}")
    payload = _cached(('stars', ra, dec, radius, mag_limit, limit), lambda: _tap(query, limit + 1))
    objects = sorted(_objects(payload, epoch)[:limit],key=lambda obj:(obj['vmag'] is None,obj['vmag'] or 0,obj['name']))
    return {'objects': objects, 'query': {'ra': ra, 'dec': dec, 'radius_deg': radius,
        'magnitude_band': 'V', 'magnitude_limit': mag_limit, 'selection_epoch': 'J2000',
        'proper_motion_does_not_change_selection': True, 'stellar_type_filter': 'SIMBAD Star.. hierarchy'},
        'completeness': _complete(payload, len(objects), limit),
        'provenance': payload['provenance'], 'cache_hit': payload.get('cache_hit', False)}
