"""Same-byte adapter for the official SPHEREx DR1 102-frame HiPS tree.

IRSA nests cube tiles as NpixN/NpixN_frame.png. Aladin's ordinary single-frame
HiPS API can consume each frame through this path adapter. Pixels are never
synthesized, resampled, or relabeled as calibrated measurements.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import threading
import requests
from archive_client import session,DataError
from observation_service import singleflight

ROOT="https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube"
CACHE=Path(os.getenv("SPHEREX_HIPS_CACHE","cache/hips")).resolve()
BUDGET=int(os.getenv("SPHEREX_HIPS_BYTES",str(256*1024*1024)))
PROPERTIES=None
PROPERTY_TIME=0
TILE_LOCK=threading.RLock()
DIAGNOSTICS={}

def diagnostic(channel,path,outcome,error=None):
    if not 0<=channel<102:return
    with TILE_LOCK:
        resources=DIAGNOSTICS.setdefault(channel,{})
        resource=resources.setdefault(path,{"requests":0,"successes":0,"failures":0,"errors":[]})
        resource[outcome]+=1
        if outcome in ('successes','failures'):resource['last_outcome']='success' if outcome=='successes' else 'failure'
        if error:resource["errors"]=(resource["errors"]+[str(error)])[-3:]
        while len(resources)>128:resources.pop(next(iter(resources)))

def status(channel):
    import copy
    with TILE_LOCK:return {"channel":channel,"resources":copy.deepcopy(DIAGNOSTICS.get(channel,{}))}

def metadata():
    global PROPERTIES,PROPERTY_TIME
    if os.getenv('SPHEREX_OFFLINE','0')=='1':
        if PROPERTIES is not None:return PROPERTIES
        saved=CACHE/'properties.json'
        if saved.exists():
            PROPERTIES=json.loads(saved.read_text());PROPERTY_TIME=time.time();return PROPERTIES
        raise DataError('Offline mode: HiPS properties are not cached; use the local FITS/cube views.',503)
    if PROPERTIES is None or time.time()-PROPERTY_TIME>3600:
        try:
            response=session().get(ROOT+"/properties");response.raise_for_status()
        except requests.RequestException as exc:
            raise DataError("Official SPHEREx HiPS properties could not be retrieved.") from exc
        props={}
        for line in response.text.splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k,v=line.split("=",1); props[k.strip()]=v.strip()
        if props.get("dataproduct_type")!="cube" or int(props.get("hips_cube_depth","0"))!=102:
            raise DataError("Official HiPS cube properties changed; refusing an unverified layer.")
        wavelengths=[float(v) for v in props["data_cube_crval3_arr"].split()]
        if len(wavelengths)!=102: raise DataError("Incomplete official wavelength metadata.")
        PROPERTIES={"source_url":ROOT,"properties":props,"wavelengths_um":wavelengths,
                    "blank_channels_zero_based":[50,51],"product_type":"visualization_only_hips_cube",
                    "spectral_not_temporal":True}
        CACHE.mkdir(parents=True,exist_ok=True)
        (CACHE/"properties.json").write_text(json.dumps(PROPERTIES))
        PROPERTY_TIME=time.time()
    return PROPERTIES

def properties(channel):
    meta=metadata()
    if not 0<=channel<102 or channel in [50,51]: raise DataError("This spectral channel is unavailable/blank.",404)
    props=dict(meta["properties"])
    for name in list(props):
        if name.startswith(("hips_cube","data_cube","custom_tile")) or name in ("hips_service_url","hips_progenitor_url"):
            props.pop(name)
    props["dataproduct_type"]="image"
    props["creator_did"]+=f"/frame/{channel}"
    props["obs_title"]=f"SPHEREx DR1 cube frame {channel} ({meta['wavelengths_um'][channel]} um); visualization only"
    props["obs_description"]=f"Same published pixels from {ROOT}; fixed spectral frame {channel}. Original cube properties: /api/hips/metadata"
    props["em_min"]=props["em_max"]=str(meta["wavelengths_um"][channel]*1e-6)
    return "\n".join(f"{k} = {v}" for k,v in props.items())

def tile_path(channel,path):
    if not 0<=channel<102 or channel in [50,51]: raise DataError("Blank/unavailable SPHEREx channel.",404)
    suffix="" if channel==0 else f"_{channel}"
    m=re.fullmatch(r"Norder([0-5])/Dir(\d+)/Npix(\d+)\.png",path)
    if m:
        order,directory,pixel=map(int,m.groups())
        if pixel>=12*4**order or directory!=pixel//10000*10000: raise DataError("Invalid HiPS tile.",404)
        return f"Norder{order}/Dir{directory}/Npix{pixel}/Npix{pixel}{suffix}.png"
    m=re.fullmatch(r"Norder([0-3])/Allsky\.png",path)
    if m: return f"Norder{m[1]}/Allsky{suffix}.png"
    if path=="Moc.fits":return path
    raise DataError("Unknown HiPS resource.",404)

def resource(channel,path):
    remote=tile_path(channel,path)
    url=ROOT+"/"+remote
    CACHE.mkdir(parents=True,exist_ok=True)
    release=metadata()["properties"].get("hips_release_date","unspecified")
    key=hashlib.sha256((release+":"+url).encode()).hexdigest()
    cache=CACHE/key
    def fetch():
        try:return cache.read_bytes()
        except FileNotFoundError:pass
        if os.getenv('SPHEREX_OFFLINE','0')=='1':raise DataError('Offline mode: tile is not cached; local cube imagery remains available.',404)
        with session().get(url,stream=True) as response:
            if response.status_code==404: raise DataError("No published SPHEREx tile at this location/channel.",404)
            if response.status_code!=200: raise DataError(f"IRSA HiPS retrieval failed (HTTP {response.status_code}).")
            data=bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data)>8*1024*1024:raise DataError("HiPS resource too large.")
        data=bytes(data)
        if path.endswith(".png") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DataError("IRSA did not return a PNG tile.")
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(dir=CACHE,delete=False) as temp:
            temp.write(data);temporary=Path(temp.name)
        temporary.replace(cache)
        with TILE_LOCK:
            files=sorted((p for p in CACHE.iterdir() if len(p.name)==64),key=lambda p:p.stat().st_mtime)
            total=sum(p.stat().st_size for p in files)
            for p in files:
                if total<=BUDGET:break
                total-=p.stat().st_size;p.unlink(missing_ok=True)
        return data
    try:data=singleflight("hips:"+key,fetch)
    except requests.RequestException as exc:raise DataError("IRSA HiPS tile retrieval failed.") from exc
    return data,url
