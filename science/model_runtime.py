"""Uploaded research model; scores are not calibrated discovery probabilities."""
import hashlib,json,threading,time,importlib.util
from pathlib import Path
from functools import lru_cache
import numpy as np
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
ROOT=Path(__file__).parent/'ml'/'uploaded'
router=APIRouter(prefix='/api/models',tags=['research inference'])
SLOT=threading.BoundedSemaphore(1)
@lru_cache(maxsize=1)
def load():
    import torch
    from ml.uploaded.inference_architecture import ImaginationSky
    torch.set_num_threads(2)
    model=ImaginationSky();model.load_state_dict(torch.load(ROOT/'attention_weights.pt',map_location='cpu',weights_only=True),strict=True);model.eval()
    return torch,model
@router.get('/status')
def status():
    config=json.loads((ROOT/'model_config.json').read_text())
    return {'weights_present':True,'runtime_available':importlib.util.find_spec('torch') is not None,'sha256':hashlib.sha256((ROOT/'attention_weights.pt').read_bytes()).hexdigest(),'config':config,
      'scientifically_validated':False,'training_domain':'Labelled injections on real backgrounds; only one training field reported',
      'preprocessing':'Training normalization and pair-head feature definitions were not supplied. Caller-normalized tensor inference is research only.',
      'pair_head':'Weights preserved; unavailable for association until the 15 feature definitions are supplied.'}
class TensorRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    research_only:bool
    preprocessing_description:str=Field(min_length=10,max_length=2000)
    channels:list=Field(min_length=4,max_length=4)
@router.post('/infer-tensor')
def infer(req:TensorRequest):
    if not req.research_only:raise HTTPException(422,'Unvalidated preprocessing permits research-only inference.')
    if not SLOT.acquire(False):raise HTTPException(503,'Inference capacity occupied.')
    try:
        # Bound nested JSON before allocating the numeric tensor.
        if any(not isinstance(c,list) or len(c)!=64 or any(not isinstance(r,list) or len(r)!=64 or any(type(v) not in (int,float) for v in r) for r in c) for c in req.channels):raise HTTPException(422,'Expected four finite numeric 64x64 channels.')
        x=np.asarray(req.channels,dtype=np.float32)
        if not np.isfinite(x).all() or np.any(np.abs(x)>1e6) or not np.isin(x[3],[0,1]).all():raise HTTPException(422,'Invalid tensor or validity mask.')
        try:torch,model=load()
        except ImportError:raise HTTPException(503,'Install requirements-ml.txt to enable inference.')
        started=time.perf_counter()
        with torch.inference_mode():out=model(torch.from_numpy(x[None])).sigmoid()[0].numpy()
        return {'status':'experimental','model_sha256':status()['sha256'],'input_sha256':hashlib.sha256(x.tobytes()).hexdigest(),
          'classes':status()['config']['classes'],'scores':out.tolist(),'score_semantics':'Uncalibrated sigmoid activations, not validated probabilities or confirmed detections',
          'seconds':time.perf_counter()-started,'preprocessing':req.preprocessing_description,'input_origin':'caller supplied, not independently science-validated'}
    finally:SLOT.release()
