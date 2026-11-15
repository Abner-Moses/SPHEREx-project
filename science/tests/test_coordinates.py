"""Isolated canonical coordinate and HTTP contracts, with no astronomy network."""
import json
import math

import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord
from fastapi import FastAPI
from fastapi.testclient import TestClient

from coordinate_api import router
from coordinate_service import CoordinateError, format_coordinates, parse_coordinates


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize('ra,dec', [(0, 0), (359.999999999, -0.), (123.4, 90), (1, -90), (133.79512916666667, -7.245147222222222)])
def test_decimal_display_roundtrip_and_seam(ra, dec):
    result = parse_coordinates(ra, dec)
    assert result['ra'] == ra and result['dec'] == dec
    assert result['frame'] == 'icrs' and result['epoch'] is None
    assert not result['ra_hms'].startswith('24:')
    display = result['sexagesimal']
    back = parse_coordinates(display['ra'], display['dec'], 'sexagesimal', 'hours')
    separation = SkyCoord(ra*u.deg, dec*u.deg).separation(SkyCoord(back['ra']*u.deg, back['dec']*u.deg))
    assert separation.arcsec < .01
    if math.copysign(1, dec) < 0:
        assert result['dec_dms'].startswith('-')


@pytest.mark.parametrize('ra,dec', [('08h55m10.831s', '-7d14m42.53s'), ('08:55:10.831', '-07:14:42.53'),
                                  ('08 55 10.831', '-07 14 42.53')])
def test_given_wise_position_uses_hours(ra, dec):
    result = parse_coordinates(ra, dec)
    assert result['ra'] == pytest.approx(133.79512916666667)
    assert result['dec'] == pytest.approx(-7.245147222222222)
    assert result['input_ra_unit'] == 'hourangle'
    assert result['ra_hms'] == '08:55:10.831'
    assert result['dec_dms'] == '-07:14:42.53'


def test_exact_24_hours_wrap_and_decimal_strings():
    assert parse_coordinates('24:00:00', '+90:00:00')['ra'] == 0
    assert parse_coordinates('24h00m00s', '-00d00m00s')['dec_dms'] == '-00:00:00.00'
    assert parse_coordinates('1.25e2', '-.25')['ra'] == 125


@pytest.mark.parametrize('value', [True, False, np.bool_(True), None, [], {}, float('nan'), float('inf'), -float('inf'),
                                 'NaN', 'inf', 'Infinity', 'true', '1e999', complex(1, 2), 10**400])
@pytest.mark.parametrize('axis', ['ra', 'dec'])
def test_reject_bool_nonfinite_and_nonscalars(axis, value):
    with pytest.raises(CoordinateError):
        parse_coordinates(**{'ra': 1, 'dec': 2, axis: value})


@pytest.mark.parametrize('ra,dec', [(-.001, 0), (360, 0), (720, 0), (0, 90.001), (0, -90.001),
    ('24:00:00.01', '0:00:00'), ('25:00:00', '0:00:00'), ('-01:00:00', '0:00:00'),
    ('01:60:00', '0:00:00'), ('01:00:60', '0:00:00'), ('01:00:00', '90:00:01'),
    ('01:00:00', '-90:00:01'), ('01d00m00s', '0d00m00s'), ('1:00:00', '1h00m00s'),
    ('1:00', '0:00:00'), ('1.5:00:00', '0:00:00'), ('1:00:00', '1'), ('1', '1:00:00')])
def test_ranges_and_ambiguous_mixed_inputs(ra, dec):
    with pytest.raises(CoordinateError):
        parse_coordinates(ra, dec)


@pytest.mark.parametrize('fmt,unit', [('decimal', 'hours'), ('sexagesimal', 'deg'), ('galactic', None), (True, None), ('decimal', True)])
def test_explicit_format_and_units(fmt, unit):
    with pytest.raises(CoordinateError):
        parse_coordinates(1, 2, fmt, unit)


def test_formatting_does_not_wrap_invalid_decimal():
    with pytest.raises(CoordinateError):
        format_coordinates(360, 0)


def test_http_parse_contract(client):
    response = client.post('/api/coordinates/parse', json={'ra': '08h55m10.831s', 'dec': '-7d14m42.53s', 'ra_unit': 'hours'})
    assert response.status_code == 200
    assert response.json()['ra'] == pytest.approx(133.79512916666667)
    assert response.json()['frame'] == 'icrs'


@pytest.mark.parametrize('body', [{'ra': True, 'dec': 1}, {'ra': 1, 'dec': False}, {'ra': 360, 'dec': 0},
    {'ra': 1}, {'ra': 1, 'dec': 2, 'frame': 'galactic'}, {'ra': 1, 'dec': 2, 'format': None},
    {'ra': float('nan'), 'dec': 0}, {'ra': 0, 'dec': float('inf')}, float('nan'), False, [], '1 2'])
def test_http_invalid_inputs_are_422_in_unmodified_fastapi(client, body):
    response = client.post('/api/coordinates/parse', content=json.dumps(body), headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
