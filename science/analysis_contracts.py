"""Schema only for future scientific algorithms; no model or inference runtime."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime

Finite=Annotated[float,Field(allow_inf_nan=False)]
Identifier=Annotated[str,Field(pattern=r'^[a-f0-9]{32}$',strict=True)]

class ProcessingProvenance(BaseModel):
    model_config=ConfigDict(extra='forbid')
    algorithm:str
    algorithm_version:str
    preprocessing_version:str
    model_version:str|None=None
    quality_policy:str
    input_asset_ids:list[Identifier]
    input_sequence_id:Identifier|None=None
    limitations:list[str]

class DetectionMeasurement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str
    asset_id:Identifier
    observation_id:str
    x:Finite
    y:Finite
    pixel_origin:Literal[0]=0
    ra:Annotated[float,Field(ge=0,lt=360,allow_inf_nan=False)]
    dec:Annotated[float,Field(ge=-90,le=90,allow_inf_nan=False)]
    frame:Literal['icrs']='icrs'
    confidence:Annotated[float,Field(ge=0,le=1,allow_inf_nan=False)]|None=None
    confidence_definition:str|None=None
    uncertainty_arcsec:Annotated[float,Field(ge=0,allow_inf_nan=False)]|None=None
    association_id:str|None=None
    observation_timestamp:AwareDatetime
    wavelength_um:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None
    bandwidth_um:Annotated[float,Field(gt=0,allow_inf_nan=False)]|None=None

class CandidateMotion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    association_id:str
    source_ids:list[str]
    angular_speed_arcsec_year:Annotated[float,Field(ge=0,allow_inf_nan=False)]
    position_angle_deg:Annotated[float,Field(ge=0,lt=360,allow_inf_nan=False)]
    uncertainty_arcsec_year:Annotated[float,Field(ge=0,allow_inf_nan=False)]|None=None
    interpretation:Literal['candidate measured image association']='candidate measured image association'

class AnomalyRegion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    asset_id:Identifier
    polygon_pixels:list[tuple[Finite,Finite]]=Field(min_length=3,max_length=128)
    score:Finite|None=None
    score_definition:str

class AnalysisResult(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['completed']
    task:Literal['moving','anomaly','noise','spectrum','detect']
    sources:list[DetectionMeasurement]=Field(default_factory=list)
    candidate_motion:list[CandidateMotion]=Field(default_factory=list)
    anomaly_regions:list[AnomalyRegion]=Field(default_factory=list)
    provenance:ProcessingProvenance
