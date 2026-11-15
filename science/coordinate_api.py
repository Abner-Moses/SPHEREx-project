"""Parent integration: ``app.include_router(coordinate_api.router)``.

Includes POST /api/coordinates/parse and GET /api/catalog/overlay. No app startup.
"""
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, Request

import catalog_overlays
import coordinate_service

router = APIRouter(tags=['coordinates', 'catalog overlays'])


@router.post('/api/coordinates/parse')
def parse_coordinates(body: Annotated[Any, Body()]):
    # Validate ourselves: Pydantic unions coerce booleans and its default error
    # serialization can fail for literal NaN/Infinity in a plain FastAPI app.
    allowed = {'ra', 'dec', 'format', 'ra_unit'}
    if not isinstance(body, dict) or set(body) - allowed or not {'ra', 'dec'} <= set(body):
        raise HTTPException(422, 'Supply ra, dec, and optionally format and ra_unit only.')
    try:
        return coordinate_service.parse_coordinates(**body)
    except coordinate_service.CoordinateError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/api/catalog/overlay')
def overlay(request: Request, catalog: Literal['gaia_dr3', 'allwise'], ra: str, dec: str,
            radius: str = '.01', limit: str = '1000'):
    if set(request.query_params) - {'catalog', 'ra', 'dec', 'radius', 'limit'}:
        raise HTTPException(422, 'Only catalog, ra, dec, radius and limit are supported.')
    if len(request.query_params.multi_items()) != len(request.query_params):
        raise HTTPException(422, 'Repeated overlay parameters are not supported.')
    from local_science import OFFLINE
    if OFFLINE:raise HTTPException(503, "External catalogue overlays are unavailable offline.")
    try:
        point = coordinate_service.parse_coordinates(ra, dec, format='decimal')
        # Service validation is authoritative; only HTTP numeric syntax is decoded.
        if len(limit) > 4 or not limit.isascii() or not limit.isdecimal():
            raise coordinate_service.CoordinateError('limit must be an integer from 1 to 1000.')
        size = coordinate_service._decimal(radius, 'radius', 0, .1)
        return catalog_overlays.overlay(catalog, point['ra'], point['dec'], size, int(limit))
    except (coordinate_service.CoordinateError, catalog_overlays.OverlayError) as exc:
        raise HTTPException(exc.status, str(exc)) from exc
