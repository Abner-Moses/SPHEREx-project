"""Discovery completeness, cache behavior, viewport geometry and authentic HiPS adapter."""
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from astropy.table import Table,Column
from astropy.io.votable import from_table,writeto
from astropy.io.votable.tree import Info
from fastapi.testclient import TestClient
import archive_client as archive
import observation_service as service
from main import app,SearchRequest
from hips_service import tile_path,properties

def response(overflow=False):
    t=Table()
    t["obs_id"]=["same","same","same"]
    t["obs_collection"]=["spherex_dr1"]*3
    t["access_url"]=["https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/v1.fits",
                     "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/v1.fits",
                     "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/v2.fits"]
    t["unusual_column"]=Column([1.,2.,3.],unit="s",description="Preserve me")
    t["missing"]=np.ma.array([1,2,3],mask=[True,False,False])
    vot=from_table(t);vot.resources[0].infos.append(Info(name="QUERY_STATUS",value="OK"))
    if overflow:vot.resources[0].infos.append(Info(name="QUERY_STATUS",value="OVERFLOW"))
    out=io.BytesIO();writeto(vot,out)
    return out.getvalue()

def test_all_columns_and_metadata_preserved():
    parsed=archive.parse_votable(response())
    assert len(parsed["columns"])==5
    col=next(c for c in parsed["columns"] if c["name"]=="unusual_column")
    assert col["unit"]=="s" and col["description"]=="Preserve me"
    assert parsed["observations"][0]["sia"]["missing"] in (None,2)
    assert len(parsed["observations"])==2  # same version duplicate, separate versions preserved
    assert parsed["archive_row_count"]==3

def test_trailing_overflow_detected():
    parsed=archive.parse_votable(response(True))
    assert parsed["votable_metadata"]["query_status"]==["OK","OVERFLOW"]

@pytest.mark.parametrize("ra,dec,poly",[
 (0.,0.,[[359.,-1.],[1.,-1.],[1.,1.],[359.,1.]]),
 (0.,89.,[[315.,88.],[45.,88.],[135.,88.],[225.,88.]]),
 (180.,0.,[[120.,-40.],[240.,-40.],[240.,40.],[120.,40.]])])
def test_viewport_spherical_parameters(ra,dec,poly):
    SearchRequest(ra=ra,dec=dec,polygon=poly)
    params=archive.query_parameters(ra,dec,.001,True,None,poly)
    assert 1<=len(params["POS"])<=4
    assert all(p.startswith("POLYGON") for p in params["POS"])
    assert "MAXREC" not in params

def test_initial_limit_is_explicit():
    assert archive.query_parameters(0,0,.001,True,1000)["MAXREC"]=="1000"

def test_discovery_never_downloads_fits_and_metadata_is_lazy(tmp_path,monkeypatch):
    monkeypatch.setattr(service,"CACHE",tmp_path)
    parsed=archive.parse_votable(response());parsed.update(_votable=response(),completeness={"status":"complete"})
    called=[]
    monkeypatch.setattr(service,"discover",lambda *a:(called.append(1) or parsed))
    monkeypatch.setattr(service,"download",lambda *a:pytest.fail("Discovery downloaded FITS"))
    monkeypatch.setattr(service,"validate",lambda *a:pytest.fail("Metadata validated FITS"))
    result=service.search(0,0,.001)
    again=service.search(0,0,.001)
    assert len(called)==1 and again["cache_hit"]
    product=service.get_observation(result["observations"][0]["id"])
    assert product["validation_status"]=="not_retrieved"
    assert service.votable_path(result["query_id"]).read_bytes()==response()

def test_inflight_deduplication():
    calls=[]
    def operation():calls.append(1);time.sleep(.05);return {"value":1}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:service.singleflight("unique-test",operation),range(4)))
    assert calls==[1]
    assert all(r["value"]==1 for r in results)

def test_hips_cube_tile_mapping():
    assert tile_path(0,"Norder0/Dir0/Npix0.png")=="Norder0/Dir0/Npix0/Npix0.png"
    assert tile_path(101,"Norder5/Dir10000/Npix10001.png")=="Norder5/Dir10000/Npix10001/Npix10001_101.png"
    assert tile_path(2,"Norder3/Allsky.png")=="Norder3/Allsky_2.png"
    for channel,path in [(50,"Norder0/Dir0/Npix0.png"),(0,"../../evil"),(0,"Norder7/Dir0/Npix0.png")]:
        with pytest.raises(archive.DataError):tile_path(channel,path)

def test_mode_rejected_and_retrieve_is_separate():
    client=TestClient(app)
    assert client.post("/api/search",json={"ra":0,"dec":0,"mode":"mock"}).status_code==422
    assert client.post("/api/observations/"+"0"*32+"/retrieve",json={"ra":0,"dec":0}).status_code==404

def test_complete_containing_query_reused_with_spherical_filter(tmp_path,monkeypatch):
    import json
    monkeypatch.setattr(service,"CACHE",tmp_path)
    directory=tmp_path/"queries";directory.mkdir()
    parent={"query_id":"a"*32,"query":{"ra":0,"dec":0,"include_deep":False,
        "polygon":[[358,-2],[2,-2],[2,2],[358,2]]},"timings":{},
        "completeness":{"status":"complete"},"votable_metadata":{"query_status":["OK"]},"observations":[
            {"id":"b"*32,"sia":{"s_ra":0,"s_dec":0,"s_region":"POLYGON ICRS 359.8 -0.2 0.2 -0.2 0.2 0.2 359.8 0.2"}},
            {"id":"c"*32,"sia":{"s_ra":1.5,"s_dec":0,"s_region":"POLYGON ICRS 1.4 -0.1 1.6 -0.1 1.6 0.1 1.4 0.1"}}]}
    (directory/("a"*32+".json")).write_text(json.dumps(parent))
    monkeypatch.setattr(service,"discover",lambda *a:pytest.fail("Containing cache was ignored"))
    result=service.search(0,0,.001,.1,False,False,[[359.9,-.1],[.1,-.1],[.1,.1],[359.9,.1]])
    assert [r["id"] for r in result["observations"]]==["b"*32]
    assert result["derived_from_query"]["query_id"]=="a"*32

def test_point_search_filters_actual_footprints(monkeypatch):
    t=Table(rows=[
        ["cover","spherex_dr1","https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/cover.fits",0.,0.,
         "POLYGON ICRS 359.9 -0.1 0.1 -0.1 0.1 0.1 359.9 0.1"],
        ["nearby","spherex_dr1","https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/near.fits",.2,0.,
         "POLYGON ICRS 0.1 -0.1 0.3 -0.1 0.3 0.1 0.1 0.1"]],
        names=["obs_id","obs_collection","access_url","s_ra","s_dec","s_region"])
    v=from_table(t);v.resources[0].infos.append(Info(name="QUERY_STATUS",value="OK"))
    out=io.BytesIO();writeto(v,out);raw=out.getvalue()
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_content(self,*a):yield raw
    class Client:
        def get(self,*a,**kw):return Response()
    monkeypatch.setattr(archive,"session",lambda:Client())
    result=archive.discover(0,0,0,False,True)
    assert [r["observation_id"] for r in result["observations"]]==["cover"]
    assert result["archive_row_count"]==2
    assert result["query"]["search_radius_deg"]==0
    assert result["point_filter"]

