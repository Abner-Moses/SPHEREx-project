"""Honest future analysis boundary: validate existing sources, never fake inference."""
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from archive_client import DataError
import observation_service
import sequence_service
from fits_processor import POLICY_VERSION

router = APIRouter(prefix='/api/analysis', tags=['analysis'])
Task = Literal['moving', 'anomaly', 'noise', 'spectrum', 'detect']
Identifier = Annotated[str, Field(strict=True, pattern=r'^[a-f0-9]{32}$')]
REASON = 'No validated analysis algorithm/model is installed. Original FITS inspection remains available.'


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: Task
    asset_id: Identifier | None = None
    sequence_id: Identifier | None = None

    @model_validator(mode='after')
    def one_source(self):
        if (self.asset_id is None) == (self.sequence_id is None):
            raise ValueError('Supply exactly one existing asset_id or sequence_id.')
        return self


@router.get('/capabilities')
def capabilities():
    return {'tasks': [{'id': name, 'available': False, 'reason': REASON}
                      for name in ('moving', 'anomaly', 'noise', 'spectrum', 'detect')],
            'jobs_supported': False, 'inference_available': False,
            'raw_inspector_available': True,
            'scientific_tools':[
                {'id':'aperture-photometry','available':True,'route':'/api/observations/{asset_id}/photometry','kind':'calibrated measurement with explicit limitations'},
                {'id':'histogram','available':True,'route':'/api/observations/{asset_id}/statistics','kind':'original IMAGE statistics'},
                {'id':'line-profile','available':True,'route':'/api/observations/{asset_id}/profile','kind':'nearest original pixel values'},
                {'id':'spectral-point','available':True,'route':'/api/observations/{asset_id}/spectral-point','kind':'target-position spectral calibration or labeled approximation'},
                {'id':'quality-summary','available':True,'route':'/api/observations/{asset_id}/quality-summary','kind':'original FLAGS and VARIANCE; no learned artifact inference'},
                {'id':'brightness-surface','available':True,'route':'/api/observations/{asset_id}/surface','kind':'bounded brightness grid; no physical distance'},
            ]}

@router.get('/schema')
def output_schema():
    from analysis_contracts import AnalysisResult
    return {'implemented_inference':False,'schema':AnalysisResult.model_json_schema(),
            'input_contract':'GET /api/analysis/inputs/{asset_id}',
            'purpose':'Contract for separately implemented and validated algorithms, not an example detection or model result.'}

@router.get('/inputs/{asset_id}')
def inputs(asset_id:str):
    """Lazy original-data contract for future Python analysis; no inference."""
    from scientific_inspector import inventory
    with observation_service.asset_lease(asset_id) as path:
        record=observation_service.get_observation(asset_id)
        hdus=inventory(path)
        return {'asset_id':asset_id,'provenance':record,'inventory':hdus,
                'original_fits_url':f'/api/observations/{asset_id}/fits',
                'scientific_invalid_mask_url':f'/api/observations/{asset_id}/quality-mask',
                'pixel_url':f'/api/observations/{asset_id}/pixel',
                'array_url':f'/api/observations/{asset_id}/array',
                'quality_policy':POLICY_VERSION,'background_subtraction':{'status':'not_applied'},
                'tensor_preprocessing':{'status':'not_implemented','reason':'Model-specific channel order, normalization and variance propagation must be versioned and validated.'},
                'inference_performed':False,'original_data_modified':False}


def _source(req):
    if req.asset_id is not None:
        # The existing service owns ID resolution, retrieval/integrity checks,
        # validation-policy migration and the lifetime of the local asset.
        with observation_service.asset_lease(req.asset_id):
            record = observation_service.get_observation(req.asset_id)
            validation = record.get('validation', {})
            if (validation.get('wcs_passed') is not True
                    or validation.get('policy_version') != POLICY_VERSION
                    or validation.get('scientific_acceptance', {}).get('accepted') is not True):
                raise HTTPException(409, 'Asset is not scientifically accepted under the current validation policy; use the original inspector.')
            return {'kind': 'asset', 'id': req.asset_id, 'validation_scope': 'Existing local FITS integrity and current scientific acceptance'}
    state = sequence_service.status(req.sequence_id)
    frames = state.get('frames', [])
    if state.get('status') != 'ready' or len(frames) < 2:
        raise HTTPException(409, 'Sequence must be ready with at least two prepared frames.')
    if any(frame.get('quality', {}).get('accepted') is not True for frame in frames):
        raise HTTPException(409, 'Sequence includes frames without scientific acceptance.')
    return {'kind': 'sequence', 'id': req.sequence_id, 'frames': len(frames),
            'validation_scope': 'Existing ready sequence manifest and frame acceptance; no new retrieval or inference'}


@router.post('/jobs')
def jobs(req: AnalysisRequest):
    try:
        source = _source(req)
    except DataError as error:
        raise HTTPException(error.status, str(error)) from error
    raise HTTPException(501, {'status': 'unavailable', 'task': req.task, 'reason': REASON,
                              'inference_performed': False, 'job_created': False,
                              'validated_source': source})
