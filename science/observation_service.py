"""Metadata discovery, lazy FITS assets, single-flight caches and provenance."""
import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import numpy as np
from spherical_geometry.polygon import SphericalPolygon
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from archive_client import DataError,discover,download,completeness_status
from fits_processor import validate,POLICY_VERSION

CACHE=Path(os.getenv("SPHEREX_CACHE","cache/observations")).resolve()
MAX_CACHE=int(os.getenv("SPHEREX_CACHE_BYTES",str(512*1024*1024)))
TTL=float(os.getenv("SPHEREX_METADATA_TTL","180"))
LOCK=threading.RLock()
FLIGHTS={}
VERIFIED={}
PINNED={}
LEASES={}
LEASE_SECONDS=float(os.getenv("SPHEREX_ASSET_LEASE_SECONDS","120"))
LOG=logging.getLogger("spherex.timings")

def identifier(value):
    if not re.fullmatch("[a-f0-9]{32}",value): raise DataError("Unknown observation/query identifier.",404)
    return value

@contextmanager
def db():
    CACHE.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(CACHE/"catalog.sqlite",timeout=30)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, record TEXT, updated REAL)")
        with conn:yield conn
    finally:conn.close()

def register_catalog(rows):
    with db() as conn:
        conn.executemany("INSERT OR REPLACE INTO products VALUES (?,?,?)",
              [(r["id"],json.dumps(r,allow_nan=False),time.time()) for r in rows])
        conn.execute("DELETE FROM products WHERE updated < ?",(time.time()-86400,))
        conn.execute("DELETE FROM products WHERE id IN (SELECT id FROM products ORDER BY updated DESC LIMIT -1 OFFSET ?)",
                     (max(len(rows),int(os.getenv("SPHEREX_CATALOG_ROWS","50000"))),))

def singleflight(key,operation):
    with LOCK:
        future=FLIGHTS.get(key)
        owner=future is None
        if owner:
            future=Future(); FLIGHTS[key]=future
    if not owner:
        result=copy.deepcopy(future.result())
        result["inflight_reused"]=True
        return result
    try:
        result=operation(); future.set_result(result); return copy.deepcopy(result)
    except Exception as exc: future.set_exception(exc); raise
    finally:
        with LOCK: FLIGHTS.pop(key,None)

def _polygon(points,center):
    pts=np.asarray(points,float)
    return SphericalPolygon.from_radec(pts[:,0],pts[:,1],center=center)

def contained_search(queries,ra,dec,deep,polygon):
    """Reuse complete archive results only when spherical containment is proven.

    Only documented POLYGON ICRS footprints are filtered; unfamiliar geometry
    forces a fresh archive query, never silently excludes a record.
    """
    if not polygon:return None
    small=_polygon(polygon,(ra,dec))
    for path in sorted(queries.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True):
        if time.time()-path.stat().st_mtime>=TTL:continue
        parent=json.loads(path.read_text());q=parent["query"]
        if parent.get("completeness",{}).get("status")!="complete" or q.get("include_deep")!=deep or not q.get("polygon"):continue
        proof=parent.get("votable_metadata",{}).get("query_status",[])
        if completeness_status(proof,parent.get("archive_row_count",0),parent.get("completeness",{}).get("maxrec"))!="complete":continue
        large=_polygon(q["polygon"],(q["ra"],q["dec"]))
        area=small.area()
        if area<=0 or not np.isclose(large.intersection(small).area(),area,rtol=1e-6,atol=1e-14):continue
        rows=[]
        try:
            for row in parent["observations"]:
                parts=row["sia"]["s_region"].split()
                if parts[:2]!=["POLYGON","ICRS"]:raise ValueError("Unsupported footprint")
                points=np.array([float(v) for v in parts[2:]]).reshape(-1,2)
                footprint=_polygon(points,(row["sia"]["s_ra"],row["sia"]["s_dec"]))
                if footprint.intersects_poly(small):rows.append(row)
        except (KeyError,ValueError,TypeError):
            continue
        parent["derived_from_query"]={"query_id":parent["query_id"],"query":q,"original_votable_scope":"parent archive query"}
        parent["observations"]=rows;parent["cache_hit"]=True
        parent["query"]={**q,"ra":ra,"dec":dec,"polygon":polygon}
        parent["completeness"]={**parent["completeness"],"returned":len(rows),
            "scope":"Spherical footprint intersection filtered from complete containing archive query"}
        return parent
    return None

def search(ra,dec,radius,size=.1,deep=True,all_results=False,polygon=None,force_refresh=False,discoverer=None):
    started=time.perf_counter()
    key=json.dumps(["metadata-v3",ra,dec,radius,deep,all_results,polygon],separators=(",",":"))
    query_id=hashlib.sha256(key.encode()).hexdigest()[:32]
    queries=CACHE/"queries"; queries.mkdir(parents=True,exist_ok=True)
    json_path=queries/(query_id+".json")
    def run():
        if not force_refresh and json_path.exists() and time.time()-json_path.stat().st_mtime<TTL:
            result=json.loads(json_path.read_text())
            register_catalog(result["observations"])
            result["cache_hit"]=True
            result["discovery_timings"]=result["timings"]
            result["timings"]={"metadata_cache_lookup_s":time.perf_counter()-started}
            return result
        if not force_refresh:
            t=time.perf_counter();contained=contained_search(queries,ra,dec,deep,polygon)
            if contained:
                register_catalog(contained["observations"])
                contained["query"]["all_results"]=all_results
                contained["query"]["search_radius_deg"]=radius
                contained["discovery_timings"]=contained["timings"]
                contained["timings"]={"containing_region_cache_s":time.perf_counter()-t}
                return contained
        result=(discoverer or discover)(ra,dec,radius,deep,all_results,polygon)
        raw=result.pop("_votable")
        result.update(query_id=query_id,cache_hit=False,discovered_at=datetime.now(timezone.utc).isoformat())
        register_catalog(result["observations"])
        with TemporaryDirectory(dir=queries) as temp:
            tmp=Path(temp)
            (tmp/"votable.xml").write_bytes(raw)
            (tmp/"query.json").write_text(json.dumps(result,allow_nan=False))
            (tmp/"votable.xml").replace(queries/(query_id+".xml"))
            (tmp/"query.json").replace(json_path)
        # Bound query snapshots by count and combined bytes, preserving exports for
        # the newest queries. Expired/evicted exports explicitly return 404.
        files=sorted(queries.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)
        total=0
        for index,path in enumerate(files):
            xml=path.with_suffix(".xml")
            total+=path.stat().st_size+(xml.stat().st_size if xml.exists() else 0)
            if index>0 and (index>=32 or total>128*1024*1024):
                path.unlink(missing_ok=True); xml.unlink(missing_ok=True)
        result["timings"]["search_total_s"]=time.perf_counter()-started
        return result
    return singleflight("query:"+query_id,run)

def get_observation(key):
    identifier(key)
    path=CACHE/(key+".json")
    if path.exists():
        record=json.loads(path.read_text())
        record["cache_hit"]=True
        return record  # Metadata-only: never read or hash FITS here.
    with db() as conn:
        row=conn.execute("SELECT record FROM products WHERE id=?",(key,)).fetchone()
    if not row: raise DataError("Observation not found; repeat archive discovery.",404)
    return json.loads(row[0])

def fits_path(key):
    record=get_observation(key)
    path=CACHE/(key+".fits")
    if not path.exists() or "sha256" not in record: raise DataError("Retrieve this observation's FITS first.",409)
    stat=path.stat(); stamp=(stat.st_size,stat.st_mtime_ns,stat.st_ino)
    with LOCK: known=VERIFIED.get(str(path))
    if known!=stamp:
        # Check once per process or changed file, never on routine metadata reads.
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record["sha256"]:
            raise DataError("Cached FITS integrity check failed.")
        validation=validate(path,record["coordinates"]["ra"],record["coordinates"]["dec"],allow_rejected_pixels=True)
        if record.get("validation",{}).get("policy_version")!=POLICY_VERSION:
            # Reclassify authentic cached bytes once under the current policy.
            # Download/retrieval time and raw FITS content are never changed.
            record["validation"]=validation
            record["validation_status"]=validation["scientific_acceptance"]["status"]
            with TemporaryDirectory(dir=CACHE) as tmp:
                metadata=Path(tmp)/"metadata.json";metadata.write_text(json.dumps(record,allow_nan=False))
                metadata.replace(CACHE/(key+".json"))
        with LOCK: VERIFIED[str(path)]=stamp
    return path

def _protected(key):
    return PINNED.get(key,0)>0 or LEASES.get(key,0)>time.monotonic()

@contextmanager
def asset_lease(key):
    """Pin a validated file for the full duration of inspection or response streaming."""
    with LOCK:
        PINNED[key]=PINNED.get(key,0)+1
        try:path=fits_path(key)
        except BaseException:
            PINNED[key]-=1;raise
    try:yield path
    finally:
        with LOCK:
            PINNED[key]-=1
            if not PINNED[key]:PINNED.pop(key,None)

def prune(required_bytes=0):
    """Called under LOCK; admission fails before publishing an unstoreable asset."""
    for key,expiry in list(LEASES.items()):
        if expiry<=time.monotonic() and not PINNED.get(key):LEASES.pop(key,None)
    if required_bytes>MAX_CACHE:raise DataError("FITS product exceeds the configured cache budget; no asset was accepted.",507)
    files=sorted((p for p in CACHE.glob("*.fits") if len(p.stem)==32),key=lambda p:p.stat().st_mtime)
    total=sum(p.stat().st_size for p in files)
    for p in files:
        if total+required_bytes<=MAX_CACHE: break
        if _protected(p.stem):continue
        total-=p.stat().st_size; p.unlink()
        p.with_suffix(".json").unlink(missing_ok=True)
        VERIFIED.pop(str(p),None)
        LEASES.pop(p.stem,None)
    if total+required_bytes>MAX_CACHE:
        raise DataError("Cache storage is occupied by active/leased observations; retry later or increase SPHEREX_CACHE_BYTES.",507)

def accept(candidate,ra,dec,size):
    key=hashlib.sha256(f'policy-v3:{candidate["source_url"]}:{ra:.10f}:{dec:.10f}:{size:.8f}'.encode()).hexdigest()[:32]
    CACHE.mkdir(parents=True,exist_ok=True)
    def run():
        if (CACHE/(key+".json")).exists():
            with asset_lease(key):
                LEASES[key]=time.monotonic()+LEASE_SECONDS
                return get_observation(key)
        if MAX_CACHE<2880:raise DataError("Configured cache budget cannot store a FITS product.",507)
        timing={}
        with TemporaryDirectory(dir=CACHE) as tmp:
            temp=Path(tmp)/"product.fits"
            t=time.perf_counter()
            retrieved_url=download(candidate["source_url"],temp,ra,dec,size)
            timing["fits_download_s"]=time.perf_counter()-t
            t=time.perf_counter(); validation=validate(temp,ra,dec,allow_rejected_pixels=True)
            timing["fits_validation_s"]=time.perf_counter()-t
            header=validation["header"]
            if header.get("OBSID") and header["OBSID"]!=candidate["observation_id"]:
                raise DataError("FITS observation ID does not match SIA metadata.")
            if candidate["collection"] not in {"spherex_dr1","spherex_qr3","spherex_dr1_deep","spherex_qr3_deep"}:
                raise DataError("Unexpected collection.")
            record={**candidate,"id":key,"product_id":candidate.get("id"),
                "coordinates":{"ra":ra,"dec":dec,"frame":"ICRS"},"cutout_size_deg":size,
                "retrieved_url":retrieved_url,"retrieved_at":datetime.now(timezone.utc).isoformat(),
                "cache_hit":False,"sha256":hashlib.sha256(temp.read_bytes()).hexdigest(),
                "validation":validation,"validation_status":validation["scientific_acceptance"]["status"],
                "detector":header.get("DETECTOR") or candidate.get("sia",{}).get("energy_bandpassname"),
                "timestamp":header.get("DATE-OBS"),"processing_version":header.get("PROCVER") or header.get("PIPEVER") or header.get("VERSION"),
                "timings":timing}
            with LOCK:
                prune(required_bytes=temp.stat().st_size)
                temp.replace(CACHE/(key+".fits"))
                meta=Path(tmp)/"metadata.json";meta.write_text(json.dumps(record,allow_nan=False))
                meta.replace(CACHE/(key+".json"))
                stat=(CACHE/(key+".fits")).stat()
                VERIFIED[str(CACHE/(key+".fits"))]=(stat.st_size,stat.st_mtime_ns,stat.st_ino)
                LEASES[key]=time.monotonic()+LEASE_SECONDS
        LOG.info(json.dumps({"operation":"retrieve","asset_id":key,**timing}))
        return record
    return singleflight("fits:"+key,run)

def retrieve(key,ra,dec,size,viewport=None):
    import local_science
    if local_science.ROOT.exists() and any(r['id']==key for r in local_science.index()):
        context=None
        if viewport:
            from sky_geometry import retrieval_target,verify_cutout_region
            candidate=local_science.product(key)['record']
            if not candidate.get('sia',{}).get('s_region'):raise DataError('Local region retrieval requires a documented exposure footprint; select a point instead.',422)
            (ra,dec),context=retrieval_target(candidate,(ra,dec),viewport)
        record=singleflight('local:'+key+str((ra,dec,size)),lambda:local_science.retrieve(key,ra,dec,size))
        if context:
            context['fits_overlap_verified']=verify_cutout_region(record['validation'],(ra,dec),viewport)
            record['retrieval_context']=context
        return record
    candidate=get_observation(key)
    match=local_science.matching_product(candidate)
    if match:
        result=retrieve(match['id'],ra,dec,size,viewport);result['requested_product_id']=key;return result
    context=None
    if viewport:
        from sky_geometry import retrieval_target
        (ra,dec),context=retrieval_target(candidate,(ra,dec),viewport)
    cached_key=hashlib.sha256(f'policy-v3:{candidate["source_url"]}:{ra:.10f}:{dec:.10f}:{size:.8f}'.encode()).hexdigest()[:32]
    if not local_science.remote_allowed('fits') and not ((CACHE/(cached_key+'.json')).exists() and (CACHE/(cached_key+'.fits')).exists()):
        raise DataError('Offline or remote FITS unavailable: this exact product/cutout is not locally indexed or cached. Select a local observation.',503)
    try:record=accept(candidate,ra,dec,size)
    except Exception as exc:
        if not isinstance(exc,DataError) or exc.status>=500 or exc.status in (408,429):local_science.remote_failed('fits')
        raise
    if context:
        from sky_geometry import verify_cutout_region
        context["fits_overlap_verified"]=verify_cutout_region(record["validation"],(ra,dec),viewport)
        record["retrieval_context"]=context
    return record

def votable_path(key):
    identifier(key)
    path=CACHE/"queries"/(key+".xml")
    if not path.exists(): raise DataError("Query export expired; repeat discovery.",404)
    return path
