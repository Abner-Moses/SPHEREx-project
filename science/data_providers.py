"""Documented release registry; source identities are part of all existing cache keys.

A newly announced collection is not scientifically accepted by guessing its URL.
Add/validate its product policy here before enabling it. No remote URL is input.
"""
from dataclasses import asdict,dataclass
from types import MappingProxyType
from fastapi import APIRouter

@dataclass(frozen=True)
class Release:
    collection:str
    release:str
    product:str
    deep:bool=False
    science_policy:str='spherex-level2-v3'

RELEASES=MappingProxyType({r.collection:r for r in (
    Release('spherex_dr1','DR1','Level 2 spectral image'),
    Release('spherex_qr3','QR3','Level 2 spectral image'),
    Release('spherex_dr1_deep','DR1','Level 2 spectral image',True),
    Release('spherex_qr3_deep','QR3','Level 2 spectral image',True),
)})
def collections(include_deep=True):
    return [r.collection for r in RELEASES.values() if include_deep or not r.deep]

router=APIRouter(tags=['providers'])
@router.get('/api/providers')
def providers():
    return {'provider':'NASA/IPAC IRSA','protocol':'SIA2','endpoint':'https://irsa.ipac.caltech.edu/SIA',
            'verified_documentation_date':'2026-11-15','releases':[asdict(r) for r in RELEASES.values()],
            'documentation':'https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-releases/',
            'future_releases':'Require product-policy verification; never guessed from filenames or substituted into an old cache key.',
            'calibrated_products':['original spectral-image MEF cutouts','associated DataLink calibration products'],
            'visualization_product':'SPHEREx DR1 102-channel HiPS cube; blank zero-based layers 50 and 51',
            'cube_scientific_access':'Full original mosaic cubes are not yet a quantitative analysis input in this application.',
            'cache_identity':'collection + official source URL + requested spatial subset; immutable product hashes; metadata TTL; scientific-policy migration',
            'coordinate_frame':'ICRS','science_pixel_policy':'spherex-level2-v3'}
