"""Isolated synthetic fixtures for the read-only scientific inspector."""
import json

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from archive_client import DataError
from scientific_inspector import inventory, pixel, array_slice


@pytest.fixture
def product(tmp_path):
    w = WCS(naxis=2)
    w.wcs.crpix = [3, 3]
    w.wcs.crval = [359.999, 30]
    w.wcs.cdelt = [-.01, .01]
    w.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    header = w.to_header()
    header['BUNIT'] = 'MJy/sr'
    header['HISTORY'] = 'first'
    header['HISTORY'] = 'second'
    header['COMMENT'] = 'one'
    header['COMMENT'] = 'two'
    image = np.arange(30, dtype=np.float32).reshape(5, 6)
    image[0, 0] = np.nan
    table = fits.BinTableHDU.from_columns([
        fits.Column(name='ID', format='J', array=[1, 2]),
        fits.Column(name='VECTOR', format='6E', dim='(3,2)', unit='um',
                    array=np.array([[[1, np.nan, 3], [4, 5, 6]], np.ones((2, 3))])),
        fits.Column(name='HUGE', format='10000E', array=np.tile(np.arange(10000), (2, 1))),
    ], name='EXTRA')
    table.header.comments['TTYPE2'] = 'vector description'
    p = tmp_path / 'fixture.fits'
    fits.HDUList([
        fits.PrimaryHDU(), fits.ImageHDU(image, header=header, name='IMAGE'),
        fits.ImageHDU(np.full((5, 6), 1 << 21, dtype=np.uint32), name='FLAGS'),
        fits.ImageHDU(np.full((5, 6), -1., dtype=np.float32), name='VARIANCE'),
        fits.ImageHDU(np.full((5, 6), 2., dtype=np.float32), name='ZODI'),
        table, fits.ImageHDU(np.arange(24).reshape(2, 3, 4), name='OTHER'),
    ]).writeto(p)
    return p


def add_spectral(path):
    # FITS table shape is (ny,nx,2); alternate W's CRPIX sets the grid
    # transform, rather than assuming the lookup uses Python pixel coordinates.
    x = np.array([1., 3., 6.])
    y = np.array([1., 3., 5.])
    xx, yy = np.meshgrid(x, y)
    values = np.stack([xx + 10*yy, 2*xx + yy], axis=-1)
    table = fits.BinTableHDU.from_columns([
        fits.Column(name='X', format='3D', array=[x]),
        fits.Column(name='Y', format='3D', array=[y]),
        fits.Column(name='VALUES', format='18D', dim='(2,3,3)', array=[values]),
    ], name='WCS-WAVE')
    with fits.open(path, mode='update') as hdus:
        h = hdus['IMAGE'].header
        h['WCSAXESW'] = 2
        for i, axis in enumerate(('X', 'Y'), 1):
            h[f'CTYPE{i}W'] = 'WAVE-TAB'
            h[f'CUNIT{i}W'] = 'um'
            h[f'CNAME{i}W'] = ('Wavelength', 'Bandwidth')[i-1]
            h[f'CRPIX{i}W'] = 1.
            h[f'CRVAL{i}W'] = 1.
            h[f'CDELT{i}W'] = 1.
            h[f'PS{i}_0W'] = 'WCS-WAVE'
            h[f'PS{i}_1W'] = 'VALUES'
            h[f'PS{i}_2W'] = axis
            h[f'PV{i}_3W'] = i
        hdus.append(table)


def test_inventory_all_hdus_cards_and_columns(product):
    inv = inventory(product)
    assert len(inv['hdus']) == 7
    image = inv['hdus'][1]
    assert image['shape'] == [5, 6]
    assert image['dtype'] == 'float32'
    assert image['unit'] == 'MJy/sr'
    assert [c['value'] for c in image['header'] if c['keyword'] == 'HISTORY'] == ['first', 'second']
    assert [c['value'] for c in image['header'] if c['keyword'] == 'COMMENT'] == ['one', 'two']
    column = inv['hdus'][5]['columns'][1]
    assert column['unit'] == 'um'
    assert column['dim'] == '(3,2)'
    assert column['description'] == 'vector description'
    json.dumps(inv, allow_nan=False)


def test_inventory_does_not_access_image_or_table_data(product, monkeypatch):
    def forbidden(self):
        raise AssertionError('Inventory loaded an array')
    monkeypatch.setattr(fits.ImageHDU, 'data', property(forbidden))
    monkeypatch.setattr(fits.BinTableHDU, 'data', property(forbidden))
    assert len(inventory(product)['hdus']) == 7


def test_pixel_original_values_and_no_mask_or_subtraction(product):
    p = pixel(product, 2.2, 2.1)
    assert p['values']['IMAGE']['value'] == 14
    assert p['values']['VARIANCE']['value'] == -1
    assert p['values']['FLAGS']['value'] == 1 << 21
    assert p['values']['ZODI']['value'] == 2
    assert p['celestial']['status'] == 'available'
    assert p['spectral']['status'] == 'unavailable'
    assert pixel(product, 0, 0)['values']['IMAGE']['value'] is None
    json.dumps(p, allow_nan=False)


def test_pixel_sky_and_wraparound(product):
    p = pixel(product, ra=359.999, dec=30)
    assert p['pixel']['x'] == pytest.approx(2, abs=1e-8)
    assert p['pixel']['y'] == pytest.approx(2, abs=1e-8)
    assert pixel(product, ra=.001, dec=30)['celestial']['ra'] == pytest.approx(.001)


@pytest.mark.parametrize('kwargs', [dict(x=1), dict(x=1, y=1, ra=1, dec=1),
    dict(ra=1), dict(x=-1, y=0), dict(x=6, y=0), dict(x=np.nan, y=0),
    dict(ra=360, dec=0), dict(ra=0, dec=91), dict(ra=180, dec=0)])
def test_bad_coordinates(product, kwargs):
    with pytest.raises(DataError) as exc:
        pixel(product, **kwargs)
    assert exc.value.status == 422


def test_mismatched_layer_is_explicit(product):
    with fits.open(product, mode='update') as h:
        h['ZODI'].data = np.zeros((2, 2))
    assert pixel(product, 2, 2)['values']['ZODI']['status'] == 'unavailable'


def test_spectral_tab_interpolation(product):
    add_spectral(product)
    spectral = pixel(product, 1.5, 1.5)['spectral']
    assert spectral['status'] == 'available'
    assert [a['value'] for a in spectral['axes']] == pytest.approx([27.5, 7.5])
    assert [a['unit'] for a in spectral['axes']] == ['um', 'um']


def test_spectral_ignores_celestial_sip(product):
    add_spectral(product)
    with fits.open(product, mode='update') as h:
        header = h['IMAGE'].header
        header['CTYPE1'] = 'RA---TAN-SIP'
        header['CTYPE2'] = 'DEC--TAN-SIP'
        header['A_ORDER'] = header['B_ORDER'] = 2
        header['A_2_0'] = .5
        header['B_0_2'] = .5
    assert [a['value'] for a in pixel(product, 1.5, 1.5)['spectral']['axes']] == pytest.approx([27.5, 7.5])


def test_spectral_declared_shift_scale_and_rotation(product):
    add_spectral(product)
    with fits.open(product, mode='update') as h:
        header = h['IMAGE'].header
        header['CRPIX1W'] = 2.
        header['CRPIX2W'] = 2.
        header['CDELT1W'] = 2.
        header['PC1_1W'] = 0.
        header['PC1_2W'] = 1.
        header['PC2_1W'] = 1.
        header['PC2_2W'] = 0.
    # x=2,y=2: delta=(1,1), lookup=(3,2), wave=23,bw=8.
    axes = pixel(product, 2, 2)['spectral']['axes']
    assert [a['value'] for a in axes] == pytest.approx([23, 8])


def test_spectral_no_extrapolation(product):
    add_spectral(product)
    with fits.open(product, mode='update') as h:
        h['IMAGE'].header['CRVAL1W'] = 1000.
    p = pixel(product, 2, 2)
    assert p['spectral']['status'] == 'unavailable'
    assert 'axes' not in p['spectral']


def test_spectral_table_without_mapping_is_not_guessed(product):
    add_spectral(product)
    with fits.open(product, mode='update') as h:
        del h['IMAGE'].header['CRPIX1W']
    assert pixel(product, 2, 2)['spectral']['status'] == 'unavailable'


def test_spectral_reference_version_must_match(product):
    add_spectral(product)
    with fits.open(product, mode='update') as h:
        h['IMAGE'].header['PV1_1W'] = 9
    assert pixel(product, 2, 2)['spectral']['status'] == 'unavailable'


def test_missing_celestial_keeps_pixel_access(product):
    with fits.open(product, mode='update') as h:
        h['IMAGE'].header['CTYPE1'] = 'LINEAR'
        h['IMAGE'].header['CTYPE2'] = 'LINEAR'
    p = pixel(product, 2, 2)
    assert p['celestial']['status'] == 'unavailable'
    assert p['values']['IMAGE']['value'] == 14
    with pytest.raises(DataError):
        pixel(product, ra=0, dec=0)


def test_broken_spectral_is_unavailable_with_table_access(product):
    add_spectral(product)
    with fits.open(product, mode='update') as h:
        h['IMAGE'].header['PS1_1W'] = 'NONEXISTENT'
    p = pixel(product, 2, 2)
    assert p['spectral']['status'] == 'unavailable'
    assert p['spectral']['tables'][0]['name'] == 'WCS-WAVE'
    assert array_slice(product, 'WCS-WAVE', column='X')['returned'] == 1


def test_flattened_image_and_cube_pages(product):
    assert array_slice(product, 'IMAGE', start=5, count=3)['data'] == [5, 6, 7]
    p = array_slice(product, 'OTHER', start=3, count=8)
    assert p['data'] == list(range(3, 11))
    assert p['total'] == 24
    assert p['next_start'] == 11
    assert p['has_more']
    end = array_slice(product, 'IMAGE', start=29, count=100)
    assert end['data'] == [29]
    assert end['next_start'] is None
    assert not array_slice(product, 'IMAGE', start=100)['data']
    assert array_slice(product, 0)['total'] == 0


def test_images_use_lazy_section(product, monkeypatch):
    monkeypatch.setattr(fits.ImageHDU, 'data', property(lambda self: (_ for _ in ()).throw(AssertionError('Full image loaded'))))
    assert array_slice(product, 'IMAGE', start=2, count=2)['data'] == [2, 3]


def test_table_nested_nan_and_budget(product):
    p = array_slice(product, 'EXTRA', count=2)
    assert p['truncated']
    assert p['has_more']  # One huge first-row cell consumes the shared budget.
    assert p['data'][0]['VECTOR']['values'] == [1, None, 3, 4, 5, 6]
    assert len(p['data'][0]['HUGE']['values']) == 4089
    json.dumps(p, allow_nan=False)


def test_full_large_cell_can_be_paged(product):
    values = []
    for start in range(0, 10000, 1000):
        p = array_slice(product, 'EXTRA', count=1, column='HUGE', cell_start=start, cell_count=1000)
        values.extend(p['data'][0]['HUGE']['values'])
    assert values == list(range(10000))


def test_variable_length_table(product):
    with fits.open(product, mode='update') as h:
        h.append(fits.BinTableHDU.from_columns([
            fits.Column(name='V', format='PJ()', array=np.array([np.arange(5000), np.arange(3)], dtype=object))
        ], name='VARIABLE'))
    p = array_slice(product, 'VARIABLE', start=1, count=1)
    assert p['data'][0]['V']['values'] == [0, 1, 2]
    assert array_slice(product, 'VARIABLE', count=1)['data'][0]['V']['truncated']


def test_complex_and_nonfinite_serialization(product):
    with fits.open(product, mode='update') as h:
        h.append(fits.BinTableHDU.from_columns([
            fits.Column(name='VALUE', format='C', array=[complex(np.inf, np.nan)]),
            fits.Column(name='FLOAT', format='D', array=[np.inf]),
        ], name='COMPLEX'))
    p = array_slice(product, 'COMPLEX')
    assert p['data'][0]['VALUE'] == {'real': None, 'imag': None}
    assert p['data'][0]['FLOAT'] is None
    json.dumps(p, allow_nan=False)


@pytest.mark.parametrize('kwargs', [dict(hdu=-1), dict(hdu=True), dict(hdu='../IMAGE'),
    dict(hdu=100), dict(hdu='missing'), dict(hdu='IMAGE', column='x'),
    dict(hdu='EXTRA', column='missing'), dict(hdu=1, count=1001),
    dict(hdu=1, count=0), dict(hdu=1, start=-1), dict(hdu=1, start=1.5),
    dict(hdu='EXTRA', cell_start=0)])
def test_invalid_selection(product, kwargs):
    with pytest.raises(DataError) as exc:
        array_slice(product, **kwargs)
    assert exc.value.status == 422


def test_ambiguous_hdu_requires_index(product):
    with fits.open(product, mode='update') as h:
        h.append(fits.ImageHDU(np.ones((2, 2)), name='OTHER'))
    with pytest.raises(DataError, match='Ambiguous'):
        array_slice(product, 'OTHER')
    assert array_slice(product, 6)['returned'] == 24


def test_unsigned_and_scaled_values(product):
    assert array_slice(product, 'FLAGS', count=1)['data'] == [1 << 21]
    with fits.open(product, mode='update') as h:
        h.append(fits.ImageHDU(np.array([[1, 2]], dtype=np.int16), name='SCALED'))
        h['SCALED'].header['BSCALE'] = .5
        h['SCALED'].header['BZERO'] = 10.
    assert array_slice(product, 'SCALED')['data'] == [10.5, 11]


def test_read_only(product):
    add_spectral(product)
    before = product.read_bytes()
    inventory(product)
    pixel(product, 2, 2)
    array_slice(product, 'EXTRA')
    assert product.read_bytes() == before


def test_corrupt_file(tmp_path):
    path = tmp_path / 'broken.fits'
    path.write_bytes(b'not FITS')
    with pytest.raises(DataError):
        inventory(path)
