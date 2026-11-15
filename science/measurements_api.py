"""Scientific tools migrated onto the single existing asset/cache system."""
from typing import Annotated
from fastapi import APIRouter, Query, Response
from catalog_api import Number
import scientific_measurements as science
import observation_service as observations
import scientific_inspector as inspector
import sequence_service
import sequence_spectral

router=APIRouter(prefix='/api/observations',tags=['scientific measurements'])

@router.get('/{identifier}/photometry')
def photometry(identifier:str,x:Number|None=None,y:Number|None=None,ra:Number|None=None,dec:Number|None=None,
               radius:Annotated[float,Query(gt=0,le=50,allow_inf_nan=False)]=3,
               annulus_inner:Annotated[float,Query(gt=0,le=99,allow_inf_nan=False)]=6,
               annulus_outer:Annotated[float,Query(gt=0,le=100,allow_inf_nan=False)]=10,subtract_zodi:bool=False):
    return science.photometry(identifier,x=x,y=y,ra=ra,dec=dec,radius=radius,annulus_inner=annulus_inner,annulus_outer=annulus_outer,subtract_zodi=subtract_zodi)

@router.get('/{identifier}/statistics')
def statistics(identifier:str,bins:Annotated[int,Query(ge=2,le=256)]=64):
    return science.statistics(identifier,bins=bins)

@router.get('/{identifier}/profile')
def profile(identifier:str,x0:Number,y0:Number,x1:Number,y1:Number,samples:Annotated[int,Query(ge=2,le=1000)]=100):
    return science.profile(identifier,x0=x0,y0=y0,x1=x1,y1=y1,samples=samples)

@router.get('/{identifier}/quality-summary')
def quality_summary(identifier:str):
    return science.quality_summary(identifier)

@router.get('/{identifier}/quality-mask')
def quality_mask(identifier:str):
    return Response(science.invalid_mask(identifier),media_type='application/fits',
                    headers={'X-Data-Product':'derived-scientific-invalid-mask'})

@router.get('/{identifier}/surface')
def surface(identifier:str,maximum:Annotated[int,Query(ge=2,le=256)]=128):
    return science.surface(identifier,maximum=maximum)

@router.get('/{identifier}/spectral-point')
def spectral_point(identifier:str,ra:Number,dec:Number,calibrated:bool=False):
    with observations.asset_lease(identifier) as path:
        record=observations.get_observation(identifier)
        pixel=inspector.pixel(path,ra=ra,dec=dec)
        if not calibrated:
            return {'asset_id':identifier,'position':pixel['celestial'],'spectral':pixel['spectral'],
                    'calibration_requested':False,'science_grade_spectral_calibration':False,'provenance':record}
        result=sequence_spectral.resolve(path,record,ra,dec,sequence_service._service().calibrations,inspection=pixel)
        return {'asset_id':identifier,'position':pixel['celestial'],'spectral':result,'calibration_requested':True,
                'science_grade_spectral_calibration':result.get('status')=='calibrated','provenance':record}
