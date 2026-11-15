"""Metadata-first SIA2 discovery with lossless VOTable access."""
import hashlib
import io
import json
import logging
import os
import threading
import time
from urllib.parse import urlsplit, urlencode
import numpy as np
import requests
from astropy.io.votable import parse
from pyvo.dal.sia2 import SIA2Query
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

COLLECTIONS = ("spherex_dr1", "spherex_qr3")
TIMEOUT = float(os.getenv("SPHEREX_TIMEOUT", "45"))
QUERY_TIMEOUT = float(os.getenv("SPHEREX_QUERY_TIMEOUT", "120"))
MAX_BYTES = int(os.getenv("SPHEREX_MAX_BYTES", str(100*1024*1024)))
MAX_METADATA_BYTES = int(os.getenv("SPHEREX_METADATA_BYTES", str(64*1024*1024)))
LOG = logging.getLogger("spherex.timings")
LOCAL = threading.local()

class DataError(Exception):
    def __init__(self,message,status=502):
        super().__init__(message); self.status=status

class Session(requests.Session):
    def __init__(self):
        super().__init__()
        retry=Retry(total=2,read=0,connect=1,backoff_factor=.3,status_forcelist=[429,502,503,504],allowed_methods=["GET"])
        self.mount("https://",HTTPAdapter(max_retries=retry,pool_connections=8,pool_maxsize=16))
    def request(self,*args,**kwargs):
        kwargs.setdefault("timeout",(5,TIMEOUT))
        kwargs.setdefault("allow_redirects",False)
        return super().request(*args,**kwargs)

def session():
    if not hasattr(LOCAL,"session"): LOCAL.session=Session()
    return LOCAL.session

def trusted_url(url):
    p=urlsplit(url)
    if p.scheme!="https" or p.hostname!="irsa.ipac.caltech.edu" or p.port not in (None,443) or p.username or p.password:
        raise DataError("Untrusted archive URL rejected.")
    if not p.path.startswith("/ibe/data/spherex/") or ".." in p.path or p.query or p.fragment:
        raise DataError("Untrusted product path rejected.")
    return url

def json_value(value):
    if value is None or value is np.ma.masked: return None
    if isinstance(value,np.ma.MaskedArray):
        return [json_value(v) for v in value]
    if isinstance(value,np.ndarray): return [json_value(v) for v in value]
    if isinstance(value,np.generic): return json_value(value.item())
    if isinstance(value,bytes): return value.decode("utf-8",errors="replace")
    if isinstance(value,float) and not np.isfinite(value): return None
    if isinstance(value,(str,int,float,bool)): return value
    if isinstance(value,(list,tuple)): return [json_value(v) for v in value]
    return str(value)

def scalar(row,name):
    try: return json_value(row[name])
    except (KeyError,ValueError): return None

def product_id(collection,url):
    return hashlib.sha256(f"product:{collection}:{url}".encode()).hexdigest()[:32]

def parse_results(table):
    records={}
    for row in table:
        values={name:scalar(row,name) for name in table.colnames}
        url=values.get("access_url")
        if not url: raise DataError("Archive row is missing access_url; original VOTable cannot be represented as an observation.")
        trusted_url(url)
        collection=values.get("obs_collection")
        identifier=product_id(collection,url)
        if identifier in records:
            record=records[identifier]
            record["archive_occurrences"]+=1
            if values!=record["sia"] and values not in record["archive_row_variants"]:
                record["archive_row_variants"].append(values)
            continue
        records[identifier]={"id":identifier,"source_url":url,"observation_id":values.get("obs_id"),
                             "collection":collection,"sia":values,"validation_status":"not_retrieved",
                             "archive_occurrences":1,"archive_row_variants":[]}
    return sorted(records.values(),key=lambda r:(str(r["collection"]),str(r["observation_id"]),r["source_url"]))

def parse_votable(raw):
    t=time.perf_counter()
    vot=parse(io.BytesIO(raw))
    table_element=vot.get_first_table()
    table=table_element.to_table(use_names_over_ids=True)
    parse_seconds=time.perf_counter()-t
    infos=[]
    def walk(resource):
        infos.extend({"name":i.name,"value":i.value,"content":i.content} for i in resource.infos)
        for child in resource.resources: walk(child)
    infos.extend({"name":i.name,"value":i.value,"content":i.content} for i in vot.infos)
    for resource in vot.resources: walk(resource)
    statuses=[i["value"] for i in infos if i["name"]=="QUERY_STATUS"]
    if "ERROR" in statuses: raise DataError("IRSA SIA query failed: "+str(infos))
    fields=list(table_element.fields)
    columns=[{"name":name,"id":field.ID,"unit":str(field.unit) if field.unit else None,
              "description":field.description,"dtype":str(table[name].dtype),
              "datatype":field.datatype,"arraysize":field.arraysize,"ucd":field.ucd,
              "utype":field.utype,"xtype":field.xtype}
             for name,field in zip(table.colnames,fields)]
    t=time.perf_counter()
    records=parse_results(table)
    return {"observations":records,"columns":columns,"votable_metadata":{"infos":infos,"version":vot.version,
            "description":vot.description,"query_status":statuses},
            "archive_row_count":len(table),"timings":{"votable_parse_s":parse_seconds,
            "metadata_transform_s":time.perf_counter()-t}}

def completeness_status(statuses, count, maxrec):
    """A successful protocol declaration is required, including trailing INFOs."""
    if "ERROR" in statuses: raise DataError("IRSA declared QUERY_STATUS=ERROR.")
    if any(s not in {"OK", "OVERFLOW"} for s in statuses): return "unknown"
    if "OVERFLOW" in statuses:
        # OK followed by OVERFLOW, or OVERFLOW alone, is a valid truncated result.
        return "unknown" if statuses[-1] != "OVERFLOW" else "truncated"
    if not statuses or (maxrec is not None and count >= maxrec): return "unknown"
    return "complete"

def query_parameters(ra,dec,radius,deep,maxrec,polygon=None):
    from data_providers import collections as release_collections
    collections=release_collections(deep)
    if polygon:
        from spherical_geometry.polygon import SphericalPolygon
        from astropy.coordinates import SkyCoord
        from astropy import units as u
        points=np.asarray(polygon,float)
        sphere=SkyCoord(points[:,0]*u.deg,points[:,1]*u.deg).cartesian.xyz.value.T
        center=SkyCoord(ra*u.deg,dec*u.deg).cartesian.xyz.value
        if np.all(sphere@center>1e-8):
            hull=SphericalPolygon.convex_hull(sphere)
            boundary=SphericalPolygon.from_radec(points[:,0],points[:,1],center=(ra,dec))
            if abs(hull.area()-boundary.area())<max(1e-9,boundary.area()*1e-5):
                # A single convex spherical polygon avoids the expensive OR of
                # many overlapping triangle queries for ordinary viewports.
                lon,lat=next(hull.to_radec())
                pos=tuple(np.column_stack((lon[:-1],lat[:-1])).ravel())
            else:
                pos=[(ra,dec,*a,*b) for a,b in zip(polygon,polygon[1:]+polygon[:1]) if not np.allclose(a,b)]
        else:
            pos=[(ra,dec,*a,*b) for a,b in zip(polygon,polygon[1:]+polygon[:1]) if not np.allclose(a,b)]
        if not len(pos): raise DataError("Empty visible-region boundary.",422)
    else:
        # PyVO rejects zero-radius circles. Query a conservative documented
        # one-arcsecond discovery cone, then filter actual spherical footprints
        # to the requested point below. The effective SIA parameters are exposed.
        pos=(ra,dec,1/3600 if radius==0 else radius)
    return dict(SIA2Query("https://irsa.ipac.caltech.edu/SIA",pos=pos,
                collection=collections,calib_level=2,maxrec=maxrec))

def discover(ra,dec,radius,deep=True,all_results=False,polygon=None,*,raw_response=None):
    maxrec=None if all_results else int(os.getenv("SPHEREX_INITIAL_MAXREC","1000"))
    params=query_parameters(ra,dec,radius,deep,maxrec,polygon)
    t=time.perf_counter()
    try:
        if raw_response is not None:
            raw, elapsed = raw_response
        else:
          with session().get("https://irsa.ipac.caltech.edu/SIA",params=params,stream=True,
                           timeout=(5,QUERY_TIMEOUT)) as response:
            response.raise_for_status()
            chunks=[]; total=0
            for chunk in response.iter_content(65536):
                total+=len(chunk)
                if total>MAX_METADATA_BYTES or time.perf_counter()-t>QUERY_TIMEOUT:
                    raise DataError("IRSA metadata query exceeded configured transfer/time bounds; completeness is unknown. Use a smaller region or IRSA TAP.",503)
                chunks.append(chunk)
            raw=b"".join(chunks)
          elapsed=time.perf_counter()-t
        result=parse_votable(raw)
        if polygon is None and radius==0:
            point_started=time.perf_counter()
            from spherical_geometry.polygon import SphericalPolygon
            retained=[]
            for row in result["observations"]:
                parts=str(row["sia"].get("s_region","")).split()
                if parts[:2]!=["POLYGON","ICRS"]:
                    raise DataError("Exact point filtering needs a supported archive footprint; no records have been claimed complete.")
                pts=np.array([float(v) for v in parts[2:]]).reshape(-1,2)
                footprint=SphericalPolygon.from_radec(pts[:,0],pts[:,1],
                              center=(row["sia"]["s_ra"],row["sia"]["s_dec"]))
                if footprint.contains_radec(ra,dec):retained.append(row)
            result["observations"]=retained
            result["point_filter"]="Exact ICRS point inside spherical SIA footprint; original VOTable is the enclosing one-arcsecond discovery cone."
            result["timings"]["point_footprint_filter_s"]=time.perf_counter()-point_started
        result["timings"]["irsa_request_s"]=elapsed
        statuses=result["votable_metadata"]["query_status"]
        overflow="OVERFLOW" in statuses
        status=completeness_status(statuses,result["archive_row_count"],maxrec)
        result["completeness"]={"status":status,
             "overflow":overflow,"returned":len(result["observations"]),"archive_rows":result["archive_row_count"],
             "maxrec":maxrec,"collections":params["COLLECTION"],
             "scope":"SIA2 intersection geometry for these parameters at query time",
             "limitation":"IRSA SIA defaults unlimited; server cancels queries over five minutes. Larger jobs require TAP. No cursor pagination is claimed."}
        result["query"]={"ra":ra,"dec":dec,"search_radius_deg":radius,"polygon":polygon,
                          "include_deep":deep,"all_results":all_results,"sia_parameters":params}
        result["archive_source"]={"protocol":"SIA2","service_url":"https://irsa.ipac.caltech.edu/SIA",
            "freshness_note":"IRSA documents SIA ingestion may lag TAP/Explorer by approximately one day; cached_at and discovery time are preserved."}
        if "point_filter" in result:result["query"]["point_filter"]=result["point_filter"]
        result["_votable"]=raw
        LOG.info(json.dumps({"operation":"discovery",**result["timings"],"rows":len(result["observations"])}))
        return result
    except DataError: raise
    except Exception as exc:
        raise DataError(f"IRSA retrieval failed. Completeness is unknown: {exc}") from exc

def download(url, destination, ra, dec, size):
    trusted_url(url)
    query = urlencode({"center": f"{ra:.10f},{dec:.10f}", "size": f"{size:.8f}"})
    retrieved_url = url + "?" + query
    for attempt in range(3):
        try:
            total = 0
            started = time.monotonic()
            with session().get(retrieved_url, stream=True) as response:
                if response.status_code != 200:
                    raise DataError(f"IRSA cutout returned HTTP {response.status_code}.")
                content_type = response.headers.get("Content-Type", "").lower()
                if any(x in content_type for x in ("text/html", "text/xml", "json")):
                    raise DataError("IRSA returned an error document instead of FITS.")
                declared = int(response.headers.get("Content-Length", "0"))
                if declared > MAX_BYTES:
                    raise DataError("Archive product exceeds download size limit.")
                with destination.open("wb") as output:
                    for block in response.iter_content(65536):
                        total += len(block)
                        if total > MAX_BYTES or time.monotonic() - started > TIMEOUT:
                            raise DataError("IRSA download exceeded size or time limit.")
                        output.write(block)
                if declared and total != declared:
                    raise DataError("Incomplete FITS download.")
                if total < 2880 or total % 2880:
                    raise DataError("Truncated FITS download.")
            return retrieved_url
        except (requests.RequestException, DataError) as exc:
            if attempt == 2:
                raise DataError(f"IRSA retrieval failed: {exc}") from exc
            time.sleep(0.25 * 2**attempt)
