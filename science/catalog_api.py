"""Native catalog routes; parent application includes ``router`` explicitly."""
from typing import Annotated
from fastapi import APIRouter, HTTPException, Query
from pydantic import BeforeValidator, Field
import catalog_service as catalog

router = APIRouter(prefix='/api/catalog', tags=['catalog'])


def _no_bool(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a numeric catalog parameter.')
    return value


Number = Annotated[float, BeforeValidator(_no_bool), Field(allow_inf_nan=False)]


def _call(function, *args):
    import local_science as local
    if function is catalog.resolve and args:
        result=local.target_field(args[0])
        if result:return result
    if function is catalog.suggest and args:
        names=sorted(set(local._TARGET_NAMES.values()));found=[local.target_field(n) for n in names if args[0].lower() in n.lower()]
        if found:return {'objects':[o for o in found if o],'provenance':{'source':'local downloaded field names; not catalogue identifications'}}
    if not local.remote_allowed('catalog'):raise HTTPException(503,'External catalogue unavailable; use local downloaded field names or coordinates.')
    try:return function(*args)
    except catalog.CatalogError as error:
        if error.status>=500:local.remote_failed('catalog')
        raise HTTPException(error.status,str(error)) from error


@router.get('/resolve')
def resolve(q: Annotated[str, Query(min_length=1, max_length=100)], epoch: Number | None = None):
    return _call(catalog.resolve, q, epoch)


@router.get('/suggest')
def suggest(prefix: Annotated[str, Query(min_length=2, max_length=100)], limit: int = 8, epoch: Number | None = None):
    return _call(catalog.suggest, prefix, limit, epoch)


@router.get('/stars')
def stars(ra: Number, dec: Number, radius: Number, mag_limit: Number = 17., limit: int = 1000, epoch: Number | None = None):
    return _call(catalog.stars, ra, dec, radius, mag_limit, limit, epoch)
