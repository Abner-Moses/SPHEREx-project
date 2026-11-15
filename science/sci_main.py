"""Real IRSA metadata discovery and independently retrieved scientific FITS."""
import logging
import time
import asyncio
import math
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated,Literal
from fastapi import FastAPI,Query,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,FileResponse,Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,ConfigDict,Field,PrivateAttr,model_validator,BeforeValidator
from starlette.concurrency import run_in_threadpool
from archive_client import DataError
import observation_service as service
import hips_service
import discovery_service
from fits_processor import image_fits,preview,BAD_FLAGS

FLAG_DEFINITIONS={**{str(bit):name for name,bit in BAD_FLAGS.items()},
                  "12":"FULLSAMPLE (informational; excluded from scientific acceptance)","21":"SOURCE (known source; not a general good-pixel flag)"}

logging.basicConfig(level=logging.INFO)
LOG=logging.getLogger("spherex")
@asynccontextmanager
async def lifespan(app):
    app.state.discovery=discovery_service.DiscoveryBroker()
    try:yield
    finally:
        await app.state.discovery.close()
        import sequence_service
        await run_in_threadpool(sequence_service.shutdown)

app=FastAPI(title="SphereX scientific backend",version="2.2",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[origin.strip() for origin in os.getenv("SPHEREX_CORS_ORIGINS","http://localhost:5173,http://127.0.0.1:5173").split(',') if origin.strip()],
                   allow_methods=["GET","POST","DELETE"],allow_headers=["Content-Type"])
from runtime_api import router as runtime_router
from catalog_api import router as catalog_router
from analysis_api import router as analysis_router
from measurements_api import router as measurements_router
app.include_router(runtime_router)
app.include_router(catalog_router)
app.include_router(analysis_router)
app.include_router(measurements_router)
from coordinate_api import router as coordinate_router
from composition_api import router as composition_router
from lens_api import router as lens_router
app.include_router(coordinate_router)
app.include_router(composition_router)
app.include_router(lens_router)
from data_providers import router as providers_router
app.include_router(providers_router)
from model_runtime import router as model_router
app.include_router(model_router)
from research_analysis import router as research_router
app.include_router(research_router)
from local_science import router as local_router
app.include_router(local_router)
from native_analysis import router as native_router
app.include_router(native_router)
def coordinate_number(value):
    if isinstance(value,bool):raise ValueError("Boolean values are not celestial coordinates.")
    return value
RA=Annotated[float,BeforeValidator(coordinate_number),Field(ge=0,lt=360,allow_inf_nan=False)]
DEC=Annotated[float,BeforeValidator(coordinate_number),Field(ge=-90,le=90,allow_inf_nan=False)]

class SearchRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    ra:RA
    dec:DEC
    search_radius_deg:float=Field(default=0,ge=0,le=180)
    cutout_size_deg:float=Field(default=.1,ge=.005,le=4.8)
    include_deep:bool=True
    all_results:bool=False
    force_refresh:bool=False
    polygon:list[tuple[RA,DEC]]|None=Field(default=None,min_length=3,max_length=128)
    _validation_s:float=PrivateAttr(default=0)
    @model_validator(mode="wrap")
    @classmethod
    def timed_validation(cls,data,handler):
        t=time.perf_counter(); result=handler(data);result._validation_s=time.perf_counter()-t
        if result.polygon:
            from sky_geometry import polygon
            try:polygon(result.polygon,(result.ra,result.dec))
            except DataError as exc:raise ValueError(str(exc)) from exc
        return result

class Viewport(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    ra:RA
    dec:DEC
    polygon:list[tuple[RA,DEC]]=Field(min_length=3,max_length=128)

class RetrieveRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    ra:RA
    dec:DEC
    cutout_size_deg:float=Field(default=.1,ge=.005,le=4.8)
    viewport:Viewport|None=None

class ObjectSearchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    name:str=Field(min_length=1,max_length=100)
    epoch:Annotated[float,BeforeValidator(coordinate_number),Field(ge=1800,le=2200,allow_inf_nan=False)]|None=None
    cutout_size_deg:float=Field(default=.1,ge=.005,le=4.8)
    include_deep:bool=True
    all_results:bool=False

class SequenceRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    ra:RA
    dec:DEC
    observation_ids:list[Annotated[str,Field(pattern="^[a-f0-9]{32}$")]]=Field(min_length=1,max_length=5000)
    cutout_size_deg:float=Field(default=.1,ge=.005,le=4.8)
    max_frames:int=Field(default=12,ge=1,le=100,strict=True)
    adaptive:bool=False
    candidate_budget:int=Field(default=40,ge=2,le=100,strict=True)
    total_timeout_s:float=Field(default=600,ge=10,le=900)
    mode:Literal['comparable','all']='comparable'
    wavelength_min_um:float|None=Field(default=None,gt=0)
    wavelength_max_um:float|None=Field(default=None,gt=0)
    date_start:str|None=None
    date_end:str|None=None
    spectral_tolerance_fraction:float=Field(default=.01,ge=0,le=.1)
    display:dict|None=None

@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    def safe(value):
        if isinstance(value,dict):return {str(k):safe(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [safe(v) for v in value]
        if isinstance(value,float) and not math.isfinite(value):return str(value)
        if value is None or isinstance(value,(str,int,float,bool)):return value
        return str(value)
    return JSONResponse(status_code=422,content={"detail":safe(exc.errors())})

@app.exception_handler(DataError)
async def data_error(request,exc):
    LOG.warning("data_request_failed path=%s status=%s detail=%s",request.url.path,exc.status,str(exc))
    return JSONResponse(status_code=exc.status,content={"detail":str(exc)})

@app.exception_handler(Exception)
async def internal_error(request,exc):
    LOG.exception("internal_failure path=%s",request.url.path,exc_info=exc)
    return JSONResponse(status_code=500,content={"detail":"Internal processing failed. No substitute data has been displayed."})

@app.get("/health")
@app.get("/api/health")
async def health():
    return {"status":"ok","irsa_connectivity":"not_checked"}

@app.post("/api/search")
async def search(req:SearchRequest,request:Request):
    import local_science
    if local_science.ROOT.exists():
        local=await run_in_threadpool(local_science.discover,req.ra,req.dec,req.polygon)
        if local['observations']:
            service.register_catalog(local['observations'])
            return local
    cached=await run_in_threadpool(local_science.cached_discovery,req)
    if cached is not None and (local_science.OFFLINE or not local_science.remote_allowed('discovery') or not req.force_refresh and not cached.get('cache_stale')):return cached
    if local_science.OFFLINE:
        local=await run_in_threadpool(local_science.discover,req.ra,req.dec,req.polygon)
        local['fallback']={'reason':'Offline mode: no matching local observations or persistent query cache','archive_completeness':'unknown'}
        return local
    if not local_science.remote_allowed('discovery'):
        local=await run_in_threadpool(local_science.discover,req.ra,req.dec,req.polygon)
        local['fallback']={'reason':'Remote archive cooling down after failure','archive_completeness':'unknown'}
        service.register_catalog(local['observations']);return local
    started=time.perf_counter()
    broker=getattr(request.app.state,"discovery",None)
    if broker is None: # ASGI clients without lifespan; normal startup creates one.
        broker=request.app.state.discovery=discovery_service.DiscoveryBroker()
    task=asyncio.create_task(discovery_service.search(broker,req))
    try:
        while not task.done():
            await asyncio.wait([task],timeout=.1)
            if await request.is_disconnected():
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                raise DataError("Obsolete archive request cancelled.",499)
        result=await task
    except asyncio.CancelledError:
        task.cancel();await asyncio.gather(task,return_exceptions=True);raise
    except Exception as exc:
        if isinstance(exc,DataError) and exc.status<500 and exc.status not in (408,429):raise
        local_science.remote_failed('discovery')
        local=cached if cached is not None else await run_in_threadpool(local_science.discover,req.ra,req.dec,req.polygon)
        local['fallback']={'reason':str(exc),'archive_completeness':'unknown'}
        service.register_catalog(local['observations']);return local
    result["timings"]["coordinate_validation_s"]=req._validation_s
    if "discovery_transport_total_s" in result["timings"]:
        result["timings"]["cache_commit_s"]=result["timings"].get("search_total_s",0)
    result["timings"]["api_search_total_s"]=time.perf_counter()-started
    return result

@app.get("/api/discovery/status")
async def discovery_status(request:Request):
    broker=getattr(request.app.state,"discovery",None)
    return {"stats":broker.stats if broker else {},"active_queries":len(broker.flights) if broker else 0}

@app.post('/api/object-search')
async def object_search(req:ObjectSearchRequest,request:Request):
    """Name resolution plus the existing metadata-first discovery pipeline.

    Select one epoch-adjusted fixed sky center, never recenter individual frames.
    """
    from local_science import target_field
    local_object=target_field(req.name)
    from astropy.time import Time
    import catalog_service
    epoch=req.epoch if req.epoch is not None else float(Time.now().tt.jyear)
    try:
        if local_object:obj=local_object
        else:
            from catalog_api import _call
            obj=await run_in_threadpool(_call,catalog_service.resolve,req.name,epoch)
    except catalog_service.CatalogError as exc:raise DataError(str(exc),exc.status) from exc
    predicted=obj.get('propagated_position',{})
    usable=predicted.get('status')=='available'
    position=predicted if usable else obj
    query=SearchRequest(ra=position['ra'],dec=position['dec'],cutout_size_deg=req.cutout_size_deg,
                        include_deep=req.include_deep,all_results=req.all_results)
    result=await search(query,request)
    result['catalog_object']=obj
    result['object_search']={'epoch_jyear_tt':epoch,'center':{'ra':position['ra'],'dec':position['dec'],'frame':'icrs'},
        'policy':'fixed sky center at selected predicted epoch' if usable else 'fixed catalog reference position; motion unavailable',
        'object_following_sequence':False,
        'warnings':[] if usable else ['Proper motion is unavailable; no zero motion or epoch-adjusted position was fabricated.']}
    return result

@app.post("/api/sequences/prepare",status_code=202)
def prepare_sequence(req:SequenceRequest):
    import sequence_service
    return sequence_service.prepare(req.model_dump(exclude_none=True))

@app.get("/api/sequences/{identifier}")
def sequence_status(identifier:str):
    import sequence_service
    return sequence_service.status(identifier)

@app.get("/api/sequences/{identifier}/metadata")
def sequence_metadata(identifier:str):
    import sequence_service
    return JSONResponse(sequence_service.status(identifier),headers={"Content-Disposition":f'attachment; filename="sequence-{service.identifier(identifier)}.json"'})

@app.get("/api/sequences/{identifier}/frames/{frame_id}/source")
def sequence_source(identifier:str,frame_id:str):
    """Bind table selection, aligned frame and original scientific inspection.

    This is metadata/cache access only; it never starts another IRSA retrieval.
    """
    import sequence_service
    state=sequence_service.status(identifier)
    frame=next((f for f in state.get('frames',[]) if f.get('id')==frame_id),None)
    if frame is None:raise DataError('No prepared frame with this identity belongs to the sequence.',404)
    asset_id=frame.get('asset_id')
    if not isinstance(asset_id,str) or not isinstance(frame.get('product_id'),str):
        raise DataError('Prepared frame has no valid original-asset binding.',409)
    with service.asset_lease(asset_id):
        record=service.get_observation(asset_id)
        if record.get('product_id')!=frame.get('product_id') or record.get('observation_id')!=frame.get('observation_id'):
            raise DataError('Sequence frame and original scientific asset identities disagree.',409)
        return {'sequence_id':identifier,'frame_id':frame_id,'product_id':frame['product_id'],
                'observation_id':frame['observation_id'],'asset':record,'frame':frame,
                'original_fits_url':f'/api/observations/{asset_id}/fits',
                'aligned_fits_url':f'/api/sequences/{identifier}/frames/{frame_id}?kind=fits',
                'retrieval_started':False}

@app.get("/api/sequences/{identifier}/frames/{frame_id}")
def sequence_frame(identifier:str,frame_id:str,kind:Literal['preview','fits','mask']='preview'):
    return LeasedSequenceResponse(identifier,frame_id,kind)

class LeasedSequenceResponse(FileResponse):
    def __init__(self,identifier,frame_id,kind):
        import sequence_service
        self.lease=sequence_service.frame_lease(identifier,frame_id,kind)
        path=self.lease.__enter__()
        try:super().__init__(path,media_type="application/fits" if kind=='fits' else 'image/png',
                            headers={"Cache-Control":"private,max-age=3600","X-Data-Product":"aligned-scientific-derivative" if kind=='fits' else 'sequence-display-derivative'})
        except BaseException:self.lease.__exit__(None,None,None);raise
    async def __call__(self,scope,receive,send):
        try:await super().__call__(scope,receive,send)
        finally:self.lease.__exit__(None,None,None)

@app.delete("/api/sequences/{identifier}")
def cancel_sequence(identifier:str,delete:bool=False):
    import sequence_service
    return sequence_service.delete(identifier) if delete else sequence_service.cancel(identifier)

@app.get("/api/queries/{identifier}/votable")
def votable(identifier:str):
    return FileResponse(service.votable_path(identifier),media_type="application/x-votable+xml",filename=identifier+".xml")

@app.get("/api/observations/{identifier}")
def metadata(identifier:str):
    return service.get_observation(identifier)

@app.post("/api/observations/{identifier}/retrieve")
async def retrieve(identifier:str,req:RetrieveRequest):
    return await run_in_threadpool(service.retrieve,identifier,req.ra,req.dec,req.cutout_size_deg,
                                  req.viewport.model_dump() if req.viewport else None)

@app.get("/api/observations/{identifier}/irsa-metadata")
def irsa_metadata(identifier:str,source:Literal['all','datalink']='all'):
    import local_science
    record=service.get_observation(identifier)
    if local_science.matching_product(record) or not local_science.remote_allowed('enrichment'):
        return local_science.metadata(record,'Local products available or remote enrichment disabled.')
    from archive_enrichment import enrich,datalink
    try:
        result=enrich(record) if source=='all' else {'datalink':datalink(record),'tap':{'status':'not_requested','columns':[],'row':None},'errors':[]}
        if result.get('errors') or result.get('datalink',{}).get('status')=='upstream' or result.get('tap',{}).get('status')=='upstream':
            local_science.remote_failed('enrichment');result['local']=local_science.metadata(record)['local']
        return result
    except Exception as exc:
        local_science.remote_failed('enrichment');return local_science.metadata(record,str(exc))

@app.get("/api/observations/{identifier}/fits")
def original(identifier:str):
    return LeasedFITSResponse(identifier)

class LeasedFITSResponse(FileResponse):
    def __init__(self,identifier):
        self.lease=service.asset_lease(identifier)
        path=self.lease.__enter__()
        try:super().__init__(path,media_type="application/fits",filename=identifier+".fits")
        except BaseException:
            self.lease.__exit__(None,None,None);raise
    async def __call__(self,scope,receive,send):
        try:await super().__call__(scope,receive,send)
        finally:self.lease.__exit__(None,None,None)

@lru_cache(maxsize=12)
def derivative(path,stamp):
    return image_fits(path)

@app.get("/api/observations/{identifier}/image.fits")
def image(identifier:str):
    with service.asset_lease(identifier) as path:
      return Response(derivative(str(path),path.stat().st_mtime_ns),media_type="application/fits",
                    headers={"X-Data-Product":"visualization-reprojection"})

@lru_cache(maxsize=16)
def preview_cached(path,stamp,stretch,low,high):
    t=time.perf_counter(); result=preview(path,stretch,low,high)
    LOG.info("preview_generation_s=%f",time.perf_counter()-t)
    return result

@app.get("/api/observations/{identifier}/preview")
def png(identifier:str,stretch:Annotated[str,Query(pattern="^(asinh|linear)$")]="asinh",
        low:Annotated[float,Query(ge=0,lt=100)]=1,high:Annotated[float,Query(gt=0,le=100)]=99.5):
    if high<=low:raise DataError("high must exceed low",422)
    with service.asset_lease(identifier) as path:
      return Response(preview_cached(str(path),path.stat().st_mtime_ns,stretch,low,high),media_type="image/png",
                    headers={"X-Data-Product":"visualization-derivative"})

@app.get("/api/observations/{identifier}/inspect")
def inspect_fits(identifier:str):
    from scientific_inspector import inventory
    with service.asset_lease(identifier) as path:
      return {"provenance":service.get_observation(identifier),**inventory(path),
            "flag_definitions":FLAG_DEFINITIONS,
            "flag_reference":"https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf Table 16"}

@app.get("/api/observations/{identifier}/pixel")
def pixel(identifier:str,x:float|None=None,y:float|None=None,ra:RA|None=None,dec:DEC|None=None):
    from scientific_inspector import pixel
    with service.asset_lease(identifier) as path:result=pixel(path,x=x,y=y,ra=ra,dec=dec)
    flag=result["values"].get("FLAGS",{}).get("value")
    if isinstance(flag,int):
        result["flag_bits_set"]=[{"bit":bit,"meaning":FLAG_DEFINITIONS.get(str(bit),"not defined in current policy")}
                                for bit in range(32) if flag & (1<<bit)]
    return result

@app.get("/api/observations/{identifier}/array")
def array(identifier:str,hdu:str="IMAGE",start:Annotated[int,Query(ge=0)]=0,
          count:Annotated[int,Query(ge=1,le=1000)]=100,column:str|None=None,
          cell_start:Annotated[int,Query(ge=0)]|None=None,cell_count:Annotated[int,Query(ge=1,le=1000)]=100):
    from scientific_inspector import array_slice
    with service.asset_lease(identifier) as path:
      return array_slice(path,int(hdu) if hdu.isdigit() else hdu,start,count,column,
                       cell_start=cell_start,cell_count=cell_count)

@app.get("/api/hips/metadata")
def hips_metadata():
    return hips_service.metadata()

@app.get("/api/hips/status")
def hips_status(channel:Annotated[int,Query(ge=0,lt=102)]):
    return hips_service.status(channel)

@app.get("/api/hips/{channel}/properties")
def hips_properties(channel:int):
    return tracked_hips(channel,"properties",lambda:Response(hips_service.properties(channel),media_type="text/plain"))

def tracked_hips(channel,path,operation):
    hips_service.diagnostic(channel,path,"requests")
    try:
        response=operation();hips_service.diagnostic(channel,path,"successes");return response
    except Exception as exc:
        hips_service.diagnostic(channel,path,"failures",exc);raise

@app.get("/api/hips/{channel}/{path:path}")
def hips_resource(channel:int,path:str):
    def response():
      data,url=hips_service.resource(channel,path)
      return Response(data,media_type="image/png" if path.endswith(".png") else "application/fits",
                    headers={"Cache-Control":"public,max-age=86400","X-IRSA-Source":url,
                             "X-Data-Product":"published-SPHEREx-HiPS-visualization"})
    return tracked_hips(channel,path,response)

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=8000)
