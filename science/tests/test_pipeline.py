"""Synthetic FITS fixtures exist only in this isolated test module."""
import io
import json
import os
from pathlib import Path
import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from fastapi.testclient import TestClient
from main import app, SearchRequest
from archive_client import DataError, parse_results, trusted_url
import observation_service as service
from fits_processor import validate, quality_mask, image_fits, preview

@pytest.fixture
def product(tmp_path):
    w=WCS(naxis=2)
    w.wcs.crpix=[10,10]; w.wcs.crval=[0,0]
    w.wcs.cdelt=[-.01,.01]; w.wcs.ctype=["RA---TAN","DEC--TAN"]
    h=w.to_header(); h["BUNIT"]="MJy/sr"; h["OBSID"]="test"; h["DETECTOR"]=1; h["FINAST"]=0
    data=np.arange(400,dtype=np.float32).reshape(20,20)
    path=tmp_path/"fixture.fits"
    fits.HDUList([fits.PrimaryHDU(),fits.ImageHDU(data,header=h,name="IMAGE"),
                  fits.ImageHDU(np.zeros((20,20),np.int32),name="FLAGS"),
                  fits.ImageHDU(np.ones((20,20),np.float32),name="VARIANCE"),
                  fits.ImageHDU(np.ones((20,20),np.float32),name="ZODI"),
                  fits.BinTableHDU(name="EPSF"),fits.BinTableHDU(name="WCS-WAVE")]).writeto(path,checksum=True)
    return path

@pytest.mark.parametrize("ra,dec",[(0,0),(359.999,90),(10,-90)])
def test_coordinates(ra,dec):
    assert SearchRequest(ra=ra,dec=dec).ra==ra

@pytest.mark.parametrize("ra,dec",[(360,0),(-1,0),(0,91),(0,-91),(float("nan"),0),(float("inf"),0)])
def test_invalid_coordinates(ra,dec):
    with pytest.raises(ValueError): SearchRequest(ra=ra,dec=dec)

def test_mock_rejected():
    r=TestClient(app).post("/api/search",json={"ra":0,"dec":0,"mode":"mock"})
    assert r.status_code==422

def test_flags():
    flags=np.array([0,1<<21,(1<<21)|(1<<6),1<<12,1<<20,1<<29])
    assert quality_mask(np.ones(6),flags,np.ones(6)).tolist()==[False,False,True,True,True,True]

@pytest.mark.parametrize("value,status",[(0,"good"),(1,"poor"),(2,"poor"),(None,"missing"),(-1,"unrecognized"),("bad","unrecognized"),(True,"unrecognized"),(0.5,"unrecognized")])
def test_astrometry_is_independent_of_numerical_wcs(product,value,status):
    with fits.open(product,mode="update",checksum=True) as h:
        if value is None:del h["IMAGE"].header["FINAST"]
        else:h["IMAGE"].header["FINAST"]=value
    before=product.read_bytes();r=validate(product,0,0)
    assert r["wcs_passed"] and r["target_coverage"]["status"]=="covered"
    assert r["astrometric_quality"]["status"]==status
    assert r["scientific_acceptance"]["accepted"]==(status=="good")
    assert product.read_bytes()==before

@pytest.mark.parametrize("flags,excluded",[(1<<12,True),(1<<21,False),((1<<12)|(1<<21),True),((1<<21)|(1<<6),True),((1<<10)|(1<<11),True)])
def test_acceptance_flags(flags,excluded):
    image=np.array([1.]);bitmap=np.array([flags]);variance=np.array([1.])
    assert bool(quality_mask(image,bitmap,variance)[0])==excluded
    assert image[0]==1 and bitmap[0]==flags

def test_flagged_target_available_for_inspection_only(product):
    with fits.open(product,mode="update",checksum=True) as h:h["FLAGS"].data[9,9]=1<<12
    r=validate(product,0,0,allow_rejected_pixels=True)
    assert not r["scientific_acceptance"]["accepted"] and r["wcs_passed"]

def test_nonfinite_and_variance():
    assert quality_mask(np.array([1,np.nan,1,1]),np.zeros(4,int),np.array([1,1,-1,np.nan])).tolist()==[False,True,True,True]

def test_wcs_and_wrap(product):
    assert validate(product,359.99,0)["wcs_passed"]
    with pytest.raises(DataError): validate(product,180,0)

def test_extensions(product):
    with fits.open(product) as h:
        fits.HDUList(list(h)[:-1]).writeto(product.parent/"bad.fits")
    with pytest.raises(DataError): validate(product.parent/"bad.fits",0,0)

def test_corrupt(product):
    bad=product.parent/"broken.fits"; bad.write_bytes(product.read_bytes()[:400])
    with pytest.raises(DataError): validate(bad,0,0)

def test_target_bad_pixel(product):
    with fits.open(product,mode="update",checksum=True) as h:
        h["FLAGS"].data[9,9]=1<<6
    with pytest.raises(DataError): validate(product,0,0)

@pytest.mark.parametrize("kind",["nonfinite","variance","dimensions","wcs"])
def test_invalid_scientific_data(product,kind):
    with fits.open(product,mode="update",checksum=True) as h:
        if kind=="nonfinite": h["IMAGE"].data[:]=np.nan
        elif kind=="variance": h["VARIANCE"].data[:]=-1
        elif kind=="dimensions": h["FLAGS"].data=np.zeros((3,3),np.int32)
        else: h["IMAGE"].header["CTYPE1"]="LINEAR"
    with pytest.raises(DataError): validate(product,0,0)

def test_unknown_observation():
    assert TestClient(app).get("/api/observations/"+"0"*32).status_code==404

def test_query_collections_and_radius(monkeypatch):
    import archive_client as archive
    called=archive.query_parameters(359.999,0,.001,True,None)
    assert called["POS"]==["CIRCLE 359.999 0.0 0.001"]
    assert set(called["COLLECTION"])=={"spherex_dr1","spherex_qr3","spherex_dr1_deep","spherex_qr3_deep"}
    assert "MAXREC" not in called

def test_derivatives_preserve_raw(product):
    before=product.read_bytes()
    out=image_fits(product)
    with fits.open(io.BytesIO(out)) as h, fits.open(product) as original:
        assert h[0].data.shape == original["IMAGE"].data.shape
        assert h[0].header["CTYPE1"]=="RA---TAN"
        assert "Visualization derivative" in str(h[0].header["HISTORY"])
    assert preview(product).startswith(b"\x89PNG")
    assert product.read_bytes()==before

def test_sia_parsing():
    t=Table(rows=[["obs","spherex_dr1","https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/test.fits"]],
            names=["obs_id","obs_collection","access_url"])
    assert parse_results(t)[0]["observation_id"]=="obs"
    assert parse_results(t[:0])==[]

@pytest.mark.parametrize("url",["http://localhost/test","https://evil.test/test","https://irsa.ipac.caltech.edu/../../secret"])
def test_ssrf(url):
    with pytest.raises(DataError): trusted_url(url)

def test_no_results(monkeypatch,tmp_path):
    monkeypatch.setattr(service,"CACHE",tmp_path)
    monkeypatch.setattr(service,"discover",lambda *a:{"_votable":b"<VOTABLE/>","observations":[],"timings":{}})
    assert service.search(0,0,.001,.1)["observations"]==[]

def test_upstream_failure_is_explicit_empty_fallback(monkeypatch,tmp_path):
    import discovery_service
    import local_science
    monkeypatch.setattr(service,"CACHE",tmp_path)
    monkeypatch.setattr(local_science,"OFFLINE",False)
    monkeypatch.setattr(local_science,"remote_allowed",lambda *a:True)
    monkeypatch.setattr(local_science,"cached_discovery",lambda *a:None)
    monkeypatch.setattr(local_science,"discover",lambda *a:{"observations":[]})
    async def fail(*a): raise DataError("IRSA retrieval failed.")
    monkeypatch.setattr(discovery_service,"search",fail)
    r=TestClient(app).post("/api/search",json={"ra":0,"dec":0})
    assert r.status_code==200 and r.json()["observations"]==[]
    assert r.json()["fallback"]=={"reason":"IRSA retrieval failed.","archive_completeness":"unknown"}

def test_cache_provenance(product,tmp_path,monkeypatch):
    monkeypatch.setattr(service,"CACHE",tmp_path/"cache")
    def download(url,path,*args): path.write_bytes(product.read_bytes()); return url+"?center=0,0&size=.1"
    monkeypatch.setattr(service,"download",download)
    candidate={"source_url":"https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/test.fits",
               "observation_id":"test","collection":"spherex_dr1","sia":{}}
    first=service.accept(candidate,0,0,.1); second=service.accept(candidate,0,0,.1)
    assert not first["cache_hit"] and second["cache_hit"]
    assert first["sha256"]==second["sha256"]
    assert first["retrieved_at"]==second["retrieved_at"]
    json.dumps(second,allow_nan=False)
    assert service.get_observation(first["id"])["source_url"]==candidate["source_url"]

@pytest.mark.skipif(os.getenv("SPHEREX_LIVE_TEST")!="1",reason="Opt-in real IRSA network test")
def test_live_official_example():
    # IRSA Introduction to SPHEREx Spectral Images, section 5.
    result=service.search(304.693508808,42.4436872991,1/3600,.1)
    assert len(result["columns"])>=51
    obs=service.retrieve(result["observations"][0]["id"],304.693508808,42.4436872991,.1)
    assert obs["validation"]["wcs_passed"]
    assert obs["collection"] in ("spherex_dr1","spherex_qr3")
    with fits.open(service.fits_path(obs["id"])) as h:
        assert h["IMAGE"].header["OBSID"]==obs["observation_id"]

