"""Read-only, lazy access to caller-resolved, validated local FITS assets.

Coordinates use zero-based IMAGE pixels. Pixel values are nearest pixel
(floor(p + .5)); WCS is evaluated at the requested fractional position.
FITS BSCALE/BZERO are applied by Astropy: these are original physical values,
without display stretches, masks, clipping or zodiacal subtraction.

No paths or URLs are resolved here; the service must authorize asset IDs before
calling. Inventory reads headers, not arrays. Image pages use HDU.section, and
table pages decode only selected rows/fields. Nested cells have a shared 4096
value budget, with explicit truncation. A large cell can be paged separately
using column, one row, and cell_start/cell_count.
"""
from contextlib import contextmanager
import math
import re

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

from archive_client import DataError

MAX_PAGE = 1000
MAX_VALUES = 4096


def _scalar(value):
    if value is None or np.ma.is_masked(value) or isinstance(value, fits.card.Undefined):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, complex):
        return {'real': _scalar(value.real), 'imag': _scalar(value.imag)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _cards(header):
    # Dict conversion loses duplicate HISTORY, COMMENT and other cards.
    return [{'keyword': c.keyword, 'value': _scalar(c.value), 'comment': c.comment}
            for c in header.cards]


@contextmanager
def _open(path):
    try:
        # Astropy's default memmap mode permits scaling of bounded sections;
        # strict memmap=True rejects even a single scaled uint FLAGS pixel.
        with fits.open(path, mode='readonly', memmap=None, lazy_load_hdus=True) as hdus:
            yield hdus
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f'FITS inspection failed: {exc}') from exc


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise DataError(f'{name} must be an integer >= {minimum}.', status=422)
    return int(value)


def _select(hdus, selector):
    if isinstance(selector, (int, np.integer)) and not isinstance(selector, bool):
        index = _integer(selector, 'hdu')
        if index >= len(hdus):
            raise DataError('HDU index does not exist.', status=422)
        return index, hdus[index]
    if isinstance(selector, str) and selector and len(selector) <= 80:
        matches = [(i, h) for i, h in enumerate(hdus) if h.name.upper() == selector.upper()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise DataError('Ambiguous HDU name; use its integer index.', status=422)
    raise DataError('Select an existing HDU by integer index or exact name.', status=422)


def _table(hdu):
    return isinstance(hdu, (fits.BinTableHDU, fits.TableHDU))


def _shape(hdu):
    if _table(hdu):
        return [int(hdu.header.get('NAXIS2', 0))]
    return list(hdu.shape) if hasattr(hdu, 'shape') else []


def _columns(hdu):
    if not _table(hdu):
        return []
    return [{'name': c.name, 'format': str(c.format), 'dtype': str(c.dtype),
             'unit': c.unit, 'dim': c.dim, 'null': _scalar(c.null),
             'bscale': _scalar(c.bscale), 'bzero': _scalar(c.bzero),
             'description': hdu.header.comments[f'TTYPE{i}'] if f'TTYPE{i}' in hdu.header else ''}
            for i, c in enumerate(hdu.columns, 1)]


def inventory(path):
    """All HDUs and header cards, with storage types and physical-value units.

    Header cards include TUNIT/TFORM/TDIM, scaling and WCS keywords verbatim.
    No data array is accessed, including variable-length table cells.
    """
    with _open(path) as hdus:
        result = []
        for index, hdu in enumerate(hdus):
            bitpix = hdu.header.get('BITPIX')
            dtype = ({8: 'uint8', 16: 'int16', 32: 'int32', 64: 'int64',
                      -32: 'float32', -64: 'float64'}.get(bitpix))
            result.append({'index': index, 'name': hdu.name,
                           'extver': hdu.header.get('EXTVER'), 'type': type(hdu).__name__,
                           'shape': _shape(hdu), 'dtype': 'table' if _table(hdu) else dtype,
                           'dtype_semantics': 'FITS storage; reads apply declared scaling',
                           'unit': hdu.header.get('BUNIT'),
                           'bscale': hdu.header.get('BSCALE', 1),
                           'bzero': hdu.header.get('BZERO', 0),
                           'columns': _columns(hdu), 'header': _cards(hdu.header)})
        return {'hdus': result, 'read_only': True, 'pixel_origin': 0}


def _unavailable(reason, **extra):
    return {'status': 'unavailable', 'reason': reason, **extra}


def _spectral(hdus, image, x, y):
    tables = [{'hdu': i, 'name': h.name, 'columns': _columns(h)}
              for i, h in enumerate(hdus) if h.name.upper() == 'WCS-WAVE']
    access = {'tables': tables, 'table_access': 'array_slice(path, hdu, column=...)'}
    try:
        header = image.header.copy()
        if not any(str(header.get(f'CTYPE{i}W', '')).endswith('-TAB') for i in (1, 2)):
            return _unavailable('No declared alternate W tabular spectral WCS.', **access)
        # Evaluate the documented SPHEREx two-axis table with SciPy rather
        # than wcslib: some native readers crash on malformed -TAB references.
        from scipy.interpolate import RegularGridInterpolator
        for axis in (1, 2):
            _, table = _select(hdus, header.get(f'PS{axis}_0W'))
            if not _table(table) or table.header.get('NAXIS2', 0) != 1:
                return _unavailable('Expected a one-row spectral lookup table.', **access)
            if (header.get(f'PV{axis}_1W', 1) != table.header.get('EXTVER', 1)
                or header.get(f'PV{axis}_2W', 1) != table.header.get('EXTLEVEL', 1)):
                return _unavailable('Spectral lookup version/level reference does not match table.', **access)
            for suffix in (1, 2):
                if header.get(f'PS{axis}_{suffix}W') not in table.columns.names:
                    return _unavailable('Spectral WCS references a missing table column.', **access)
        required = [f'{key}{i}W' for key in ('CRPIX', 'CRVAL', 'CDELT', 'CUNIT') for i in (1, 2)]
        if any(key not in header for key in required):
            return _unavailable('Incomplete declared spectral coordinate transform or units.', **access)
        if any(not u.Unit(header[f'CUNIT{i}W']).is_equivalent(u.m) for i in (1, 2)):
            return _unavailable('Spectral wavelength/bandwidth units are not lengths.', **access)
        if (header.get('WCSAXESW') != 2
            or any(header.get(f'CTYPE{i}W') != 'WAVE-TAB' for i in (1, 2))
            or header['PS1_0W'] != header['PS2_0W']
            or header['PS1_1W'] != header['PS2_1W']
            or [header.get(f'PV{i}_3W') for i in (1, 2)] != [1, 2]
            or any(re.fullmatch(r'CD\d+_\d+W', key) for key in header)):
            return _unavailable('Unsupported spectral lookup layout; inspect original table.', **access)
        row = table.data[0:1]
        gx = row[header['PS1_2W']][0]
        gy = row[header['PS2_2W']][0]
        grid = row[header['PS1_1W']][0]
        if (gx.ndim != 1 or gy.ndim != 1 or grid.shape != (len(gy), len(gx), 2)
            or grid.size > 1_000_000):
            return _unavailable('Unsupported or oversized spectral lookup grid.', **access)
        pc = np.array([[header.get(f'PC{i}_{j}W', float(i == j)) for j in (1, 2)] for i in (1, 2)])
        delta = np.array([x + 1 - header['CRPIX1W'], y + 1 - header['CRPIX2W']])
        lookup = np.array([header[f'CRVAL{i}W'] for i in (1, 2)]) + np.array([header[f'CDELT{i}W'] for i in (1, 2)]) * (pc @ delta)
        # FITS TDIM=(2,nx,ny) appears in NumPy as (ny,nx,2).
        values = RegularGridInterpolator((gy, gx), grid, bounds_error=True)([[lookup[1], lookup[0]]])[0]
        if not np.all(np.isfinite(values)):
            return _unavailable('Spectral lookup is undefined or outside its grid.', **access)
        axes = [{'name': header.get(f'CNAME{i}W'), 'ctype': header[f'CTYPE{i}W'],
                 'unit': header[f'CUNIT{i}W'], 'value': _scalar(v)}
                for i, v in enumerate(values, 1)]
        return {'status': 'available', 'method': 'scipy RegularGridInterpolator; declared alternate W transform',
                'axes': axes, 'purpose': 'Approximate visualization wavelength/bandwidth; '
                'use calibration CWAVE/CBAND for science analysis.', **access}
    except Exception as exc:
        return _unavailable(f'Spectral WCS could not be evaluated: {exc}', **access)


def pixel(path, x=None, y=None, *, ra=None, dec=None):
    """Original IMAGE/VARIANCE/FLAGS/ZODI sample and celestial/spectral mapping.

    Choose exactly one coordinate pair. Sky inputs are ICRS degrees. Invalid
    WCS, unmatched layers and undefined spectral values are reported explicitly.
    This inspector reports flags as stored; it does not classify/mask pixels.
    """
    sky = ra is not None or dec is not None
    if (sky and (x is not None or y is not None)) or (sky and (ra is None or dec is None)) or (not sky and (x is None or y is None)):
        raise DataError('Provide either x/y or ra/dec, as a complete pair.', status=422)
    try:
        a, b = map(float, (ra, dec) if sky else (x, y))
        if not np.isfinite([a, b]).all() or (sky and not (0 <= a < 360 and -90 <= b <= 90)):
            raise ValueError()
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError('Invalid finite pixel or ICRS coordinates.', status=422) from exc
    with _open(path) as hdus:
        _, image = _select(hdus, 'IMAGE')
        shape = _shape(image)
        if _table(image) or len(shape) != 2:
            raise DataError('Pixel inspection requires a 2D IMAGE HDU.', status=422)
        celestial = _unavailable('No usable celestial WCS.')
        try:
            celestial_header = image.header.copy()
            # wcslib also parses alternates during primary WCS construction;
            # a broken spectral table must not compromise celestial access.
            for key in list(celestial_header):
                if re.fullmatch(r'(WCSAXES|WCSNAME|CTYPE\d+|CNAME\d+|CUNIT\d+|CRPIX\d+|CRVAL\d+|CDELT\d+|PC\d+_\d+|CD\d+_\d+|PS\d+_\d+|PV\d+_\d+|LONPOLE|LATPOLE)W', key):
                    del celestial_header[key]
            wcs = WCS(celestial_header, fobj=hdus).celestial
            if not wcs.has_celestial:
                raise ValueError('Missing celestial axes')
            if sky:
                a, b = map(float, wcs.world_to_pixel(SkyCoord(a*u.deg, b*u.deg, frame='icrs')))
            coord = wcs.pixel_to_world(a, b).icrs
            if not np.isfinite([coord.ra.deg, coord.dec.deg]).all():
                raise ValueError('Nonfinite celestial transformation')
            celestial = {'status': 'available', 'frame': 'icrs',
                         'ra': float(coord.ra.deg), 'dec': float(coord.dec.deg)}
        except Exception as exc:
            if sky:
                raise DataError(f'Sky-to-pixel WCS unavailable: {exc}', status=422) from exc
            celestial = _unavailable(str(exc))
        if not np.isfinite([a, b]).all() or not (0 <= a <= shape[1]-1 and 0 <= b <= shape[0]-1):
            raise DataError('Position outside IMAGE pixel-center bounds.', status=422)
        ix, iy = math.floor(a + .5), math.floor(b + .5)
        values = {}
        for name in ('IMAGE', 'VARIANCE', 'FLAGS', 'ZODI'):
            matches = [h for h in hdus if h.name.upper() == name]
            if len(matches) != 1 or _table(matches[0]) or _shape(matches[0]) != shape:
                values[name] = _unavailable('Missing, ambiguous or dimensions do not match IMAGE.')
                continue
            h = matches[0]
            value = h.section[iy:iy+1, ix:ix+1][0, 0]
            values[name] = {'status': 'available', 'value': _scalar(value),
                            'unit': h.header.get('BUNIT'), 'finite': bool(np.isfinite(value))}
        return {'pixel': {'x': a, 'y': b, 'sample_x': ix, 'sample_y': iy, 'origin': 0},
                'values': values, 'celestial': celestial,
                'spectral': _spectral(hdus, image, a, b), 'processing': 'original physical values'}


def _cell(value, budget, start=0, count=None):
    """Bound nested cells without first converting a complete ndarray to list."""
    if isinstance(value, (np.ndarray, list, tuple)):
        arr = np.asanyarray(value)
        total = int(arr.size)
        length = min(total - min(start, total), budget[0], count if count is not None else MAX_VALUES)
        items = []
        for i in range(start, start + length):
            if budget[0] <= 0:
                break
            items.append(_cell(arr.flat[i], budget))
        returned = len(items)
        return {'values': items, 'shape': list(arr.shape), 'total_elements': total,
                'start': start, 'returned': returned,
                'next_start': start + returned if start + returned < total else None,
                'truncated': start > 0 or start + returned < total}
    if budget[0] <= 0:
        return {'unavailable': 'Response value budget exhausted; select this column/row separately.'}
    if isinstance(value, (complex, np.complexfloating)):
        if budget[0] < 2:
            return {'unavailable': 'Complex value requires two remaining budget entries.'}
        budget[0] -= 2
        return _scalar(value)
    budget[0] -= 1
    return _scalar(value)


def array_slice(path, hdu, start=0, count=100, column=None, *, cell_start=None, cell_count=100):
    """Page flattened images (C order) or table rows, at most 1000 per page.

    Table vector cells are independently described with shape/truncation. For
    complete access to a huge vector: select its column and one row, then page
    cell_start/cell_count (at most 1000 values). Total response data value budget
    is 4096, shared across rows and columns. Headers are served by inventory.
    """
    start = _integer(start, 'start')
    count = _integer(count, 'count', 1)
    if count > MAX_PAGE:
        raise DataError(f'count exceeds {MAX_PAGE}.', status=422)
    if cell_start is not None:
        cell_start = _integer(cell_start, 'cell_start')
        cell_count = _integer(cell_count, 'cell_count', 1)
        if count != 1 or column is None or cell_count > MAX_PAGE:
            raise DataError('Cell paging requires a column, count=1, cell_count<=1000.', status=422)
    with _open(path) as hdus:
        index, selected = _select(hdus, hdu)
        shape = _shape(selected)
        tabular = _table(selected)
        total = shape[0] if tabular else math.prod(shape) if shape else 0
        stop = min(start + count, total)
        budget = [MAX_VALUES]
        truncated = False
        if tabular:
            names = list(selected.columns.names)
            if column is not None and (not isinstance(column, str) or column not in names):
                raise DataError('Unknown table column.', status=422)
            names = [column] if column is not None else names
            data = []
            for row in range(start, stop):
                if budget[0] == 0:
                    break
                # Slice rows BEFORE decoding fields (especially variable arrays).
                record = selected.data[row:row+1]
                data.append({name: _cell(record[name][0], budget, cell_start or 0,
                                         cell_count if cell_start is not None else None)
                             for name in names})
            def cut(value):
                return isinstance(value, dict) and (value.get('truncated', False)
                    or 'unavailable' in value or any(cut(v) for v in value.values())) or isinstance(value, list) and any(cut(v) for v in value)
            truncated = cut(data)
        else:
            if column is not None or cell_start is not None:
                raise DataError('Column/cell selection requires a table HDU.', status=422)
            data = []
            # Read contiguous runs on the last axis; never reshape/load .data.
            cursor = start
            while cursor < stop:
                coords = np.unravel_index(cursor, tuple(shape))
                run = min(stop - cursor, shape[-1] - coords[-1])
                section = tuple(slice(int(c), int(c)+1) for c in coords[:-1]) + (slice(int(coords[-1]), int(coords[-1])+run),)
                data.extend(_scalar(v) for v in selected.section[section].flat)
                cursor += run
        returned = len(data)
        next_start = start + returned if start + returned < total else None
        return {'hdu': index, 'name': selected.name, 'kind': 'table' if tabular else 'image',
                'shape': shape, 'unit': selected.header.get('BUNIT'), 'column': column,
                'start': start, 'requested_count': count, 'returned': returned,
                'total': total, 'next_start': next_start, 'has_more': next_start is not None,
                'truncated': truncated, 'value_limit': MAX_VALUES, 'data': data}
