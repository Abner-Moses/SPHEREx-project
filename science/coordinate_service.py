"""Canonical scalar ICRS input. Decimal RA is degrees; sexagesimal RA is hours.

ICRS is a frame, not an observation epoch. No epoch or motion is inferred.
Decimal RA must be in [0, 360); exactly 24h00m00s wraps to 0h.
"""
import math
import re
import warnings

import numpy as np
from astropy import units as u
from astropy.coordinates import Angle, SkyCoord


class CoordinateError(ValueError):
    status = 422


_DECIMAL = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')
_SEX = re.compile(r'([+-]?)(\d{1,3})(?:\s*:\s*|\s+)(\d{1,2})(?:\s*:\s*|\s+)(\d{1,2}(?:\.\d*)?)\Z')


def finite_number(value, name, minimum, maximum, *, upper_open=False):
    """Validate numeric scalars without bool coercion or coordinate wrapping."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise CoordinateError(f'{name} must be a finite number, not a boolean.')
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise CoordinateError(f'{name} is outside its finite allowed range.') from exc
    if not math.isfinite(value) or value < minimum or value > maximum or upper_open and value == maximum:
        raise CoordinateError(f'{name} is outside its finite allowed range.')
    return value


def _decimal(value, name, low, high, upper_open=False):
    if isinstance(value, str):
        if len(value) > 80 or not _DECIMAL.fullmatch(value.strip()):
            raise CoordinateError(f'{name} must be decimal degrees.')
        value = float(value)
    return finite_number(value, name, low, high, upper_open=upper_open)


def _sexagesimal(value, name):
    if not isinstance(value, str) or len(value) > 80:
        raise CoordinateError(f'{name} must be sexagesimal text.')
    text = value.strip()
    # Accept h/m/s, d/m/s, colon, and three whitespace-separated components.
    unit = 'h' if name == 'ra' else 'd'
    marked = re.fullmatch(r'([+-]?\d{1,3})\s*' + unit + r'\s*(\d{1,2})\s*m\s*(\d{1,2}(?:\.\d*)?)\s*s', text, re.I)
    normalized = ':'.join(marked.groups()) if marked else text
    match = _SEX.fullmatch(normalized)
    if not match:
        raise CoordinateError(f'{name} requires three sexagesimal components; RA uses hours, Dec degrees.')
    sign, major, minute, second = match.groups()
    major, minute, second = int(major), int(minute), float(second)
    bound = 24 if name == 'ra' else 90
    if minute >= 60 or second >= 60 or major > bound or major == bound and (minute or second):
        raise CoordinateError(f'{name} has an out-of-range sexagesimal component.')
    if name == 'ra' and sign == '-':
        raise CoordinateError('Sexagesimal RA must be nonnegative hours.')
    if name == 'ra' and major == 24:
        normalized = '00:00:00'  # Validated exact wrap; avoid Astropy's 24h warning.
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        try:
            angle = Angle(normalized, unit=u.hourangle if name == 'ra' else u.deg)
        except (ValueError, Warning) as exc:
            raise CoordinateError(f'Invalid {name} angle.') from exc
    return float(angle.deg) % 360 if name == 'ra' else float(angle.deg)


def format_coordinates(ra, dec):
    """Return full-precision degrees and display strings with correct 24h carry."""
    ra = finite_number(ra, 'ra', 0, 360, upper_open=True)
    dec = finite_number(dec, 'dec', -90, 90)
    point = SkyCoord(ra=ra*u.deg, dec=dec*u.deg, frame='icrs')
    # Round before formatting so Astropy cannot print 24:00:00 at the seam.
    display_ra = (round(point.ra.hour * 3600, 3) % 86400) / 3600
    hms = Angle(display_ra, unit=u.hourangle).to_string(unit=u.hourangle, sep=':', precision=3, pad=True)
    dms = point.dec.to_string(unit=u.deg, sep=':', precision=2, pad=True, alwayssign=True)
    return {'ra': float(point.ra.deg), 'dec': float(point.dec.deg), 'frame': 'icrs',
            'ra_unit': 'deg', 'dec_unit': 'deg', 'ra_hms': hms, 'dec_dms': dms,
            'sexagesimal': {'ra': hms, 'dec': dms, 'ra_unit': 'hourangle', 'dec_unit': 'deg'},
            'epoch': None, 'epoch_note': 'ICRS frame only; no coordinate epoch or proper motion was supplied.'}


def parse_coordinates(ra, dec, format='auto', ra_unit=None):
    """Parse decimal degrees or HMS/DMS (explicit Astropy hourangle for RA).

    Auto recognizes decimal numeric strings, otherwise requires HMS/DMS pairs.
    ``ra_unit`` can make the convention explicit: deg/degree/degrees for decimal,
    hourangle/hour/hours for sexagesimal. Mixed formats are rejected.
    """
    if not isinstance(format, str) or format not in ('auto', 'decimal', 'sexagesimal'):
        raise CoordinateError('format must be auto, decimal, or sexagesimal.')
    if format == 'auto':
        decimal = lambda v: not isinstance(v, str) or bool(_DECIMAL.fullmatch(v.strip()))
        format = 'decimal' if decimal(ra) and decimal(dec) else 'sexagesimal'
    allowed = ('deg', 'degree', 'degrees') if format == 'decimal' else ('hourangle', 'hour', 'hours')
    if ra_unit is not None and (not isinstance(ra_unit, str) or ra_unit not in allowed):
        raise CoordinateError('Decimal RA uses degrees; sexagesimal RA explicitly uses hours.')
    if format == 'decimal':
        ra, dec = _decimal(ra, 'ra', 0, 360, True), _decimal(dec, 'dec', -90, 90)
    else:
        ra, dec = _sexagesimal(ra, 'ra'), _sexagesimal(dec, 'dec')
    return {**format_coordinates(ra, dec), 'input_format': format,
            'input_ra_unit': 'deg' if format == 'decimal' else 'hourangle'}
