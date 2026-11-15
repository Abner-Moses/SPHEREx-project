"""Regression tests for audited defects; network substitutes are isolated here."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch
import httpx2
import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from fastapi.testclient import TestClient
from archive_client import DataError, completeness_status
from discovery_service import DiscoveryBroker
from main import app
import observation_service as service
from sky_geometry import footprint, polygon, retrieval_target
from test_discovery import response as votable_fixture

@pytest.fixture
def source(tmp_path):
    w=WCS(naxis=2);w.wcs.crval=[0,0];w.wcs.crpix=[10,10];w.wcs.cdelt=[-.01,.01];w.wcs.ctype=['RA---TAN','DEC--TAN']
    h=w.to_header();h['OBSID']='test';h['BUNIT']='MJy/sr';h['FINAST']=0
    image=np.arange(400,dtype=np.float32).reshape(20,20)
    path=tmp_path/'source.fits'
    table=fits.BinTableHDU.from_columns([fits.Column(name='VALUES',format='3E',array=np.ones((3,3)))],name='EPSF')
    fits.HDUList([fits.PrimaryHDU(),fits.ImageHDU(image,header=h,name='IMAGE'),fits.ImageHDU(np.zeros_like(image,dtype=np.int32),name='FLAGS'),fits.ImageHDU(np.ones_like(image),name='VARIANCE'),fits.ImageHDU(np.ones_like(image),name='ZODI'),table,fits.BinTableHDU(name='WCS-WAVE')]).writeto(path,checksum=True)
    return path

@pytest.mark.parametrize('body',['{"ra":NaN,"dec":0}','{"ra":Infinity,"dec":0}','{"ra":-Infinity,"dec":0}','{"ra":true,"dec":0}','{"ra":0,"dec":false}','{"ra":360,"dec":0}','{"ra":0,"dec":91}'])
def test_invalid_http_coordinates_are_client_errors(body):
    r=TestClient(app,raise_server_exceptions=False).post('/api/search',content=body,headers={'Content-Type':'application/json'})
    assert r.status_code==422 and isinstance(r.json()['detail'],list)

@pytest.mark.parametrize('statuses,count,cap,expected',[
    ([],0,None,'unknown'),(['UNRECOGNIZED'],1,None,'unknown'),(['OK','BAD'],1,None,'unknown'),
    (['OK'],0,None,'complete'),(['OK'],2,2,'unknown'),(['OVERFLOW'],2,2,'truncated'),
    (['OK','OVERFLOW'],2,None,'truncated'),(['OVERFLOW','OK'],2,None,'unknown')])
def test_protocol_completeness(statuses,count,cap,expected):
    assert completeness_status(statuses,count,cap)==expected

def test_protocol_error_never_complete():
    with pytest.raises(DataError):completeness_status(['OK','ERROR'],0,None)

def candidate():
    return {'id':'a'*32,'source_url':'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/test.fits','observation_id':'test','collection':'spherex_dr1',
            'sia':{'s_ra':0,'s_dec':0,'s_region':'POLYGON ICRS 359.9 -0.1 0.1 -0.1 0.1 0.1 359.9 0.1'}}

def test_cutout_target_uses_intersection_not_offscreen_center(source,tmp_path,monkeypatch):
    monkeypatch.setattr(service,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(service,'download',lambda url,path,*a:(path.write_bytes(source.read_bytes()) and url))
    row=candidate();service.register_catalog([row])
    view={'ra':.13,'dec':0,'polygon':[[.06,-.03],[.16,-.03],[.16,.03],[.06,.03]]}
    assert not polygon(view['polygon'],(.13,0)).contains_radec(0,0)
    r=service.retrieve(row['id'],.15,0,.1,view)
    assert .06<r['coordinates']['ra']<.1
    assert r['retrieval_context']['target_in_intersection'] and r['retrieval_context']['fits_overlap_verified']
    assert r['validation']['scientific_acceptance']['accepted']

def test_preferred_position_and_disjoint_region():
    view={'ra':.07,'dec':0,'polygon':[[.06,-.03],[.09,-.03],[.09,.03],[.06,.03]]}
    pos,ctx=retrieval_target(candidate(),(.08,0),view)
    assert pos==(.08,0) and ctx['method']=='preferred_position'
    with pytest.raises(DataError):retrieval_target(candidate(),(20,0),{'ra':20,'dec':0,'polygon':[[19,-1],[21,-1],[21,1],[19,1]]})

@pytest.mark.parametrize('center,points',[( (0,0), [[359,-1],[1,-1],[1,1],[359,1]]),((0,89.8),[[315,89.6],[45,89.6],[135,89.6],[225,89.6]])])
def test_wrap_and_polar_regions(center,points):
    assert polygon(points,center).contains_radec(*center)

def test_cache_admission_and_active_serving(source,tmp_path,monkeypatch):
    monkeypatch.setattr(service,'CACHE',tmp_path/'cache');monkeypatch.setattr(service,'MAX_CACHE',1)
    monkeypatch.setattr(service,'download',lambda url,path,*a:(path.write_bytes(source.read_bytes()) and url))
    with pytest.raises(DataError) as error:service.accept(candidate(),0,0,.1)
    assert error.value.status==507 and not list(service.CACHE.glob('*.fits'))
    monkeypatch.setattr(service,'MAX_CACHE',source.stat().st_size+100)
    first=service.accept(candidate(),0,0,.1)
    service.LEASES.clear()
    with service.asset_lease(first['id']):
        with pytest.raises(DataError) as error:service.accept(candidate(),.01,0,.1)
        assert error.value.status==507 and (service.CACHE/(first['id']+'.fits')).exists()
    second=service.accept(candidate(),.01,0,.1)
    assert (service.CACHE/(second['id']+'.fits')).exists()

def test_real_array_api_contract(source,tmp_path,monkeypatch):
    monkeypatch.setattr(service,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(service,'download',lambda url,path,*a:(path.write_bytes(source.read_bytes()) and url))
    record=service.accept(candidate(),0,0,.1);client=TestClient(app);url='/api/observations/'+record['id']+'/array'
    rows=client.get(url,params={'hdu':'EPSF','column':'VALUES','count':20})
    cell=client.get(url,params={'hdu':'EPSF','column':'VALUES','count':1,'cell_start':1,'cell_count':2})
    assert rows.status_code==cell.status_code==200
    assert rows.json()['returned']==3 and cell.json()['data'][0]['VALUES']['returned']==2

def test_transport_deduplicates_and_cancels_last_waiter():
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event();closed=asyncio.Event();calls=[]
        async def upstream(request):
            calls.append(request);entered.set()
            try:await release.wait()
            finally:closed.set()
            return httpx2.Response(200,content=votable_fixture())
        broker=DiscoveryBroker(client=httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)),total_timeout=2)
        args=(0,0,.001,False,True,None)
        one=asyncio.create_task(broker.fetch(args));two=asyncio.create_task(broker.fetch(args));await entered.wait();one.cancel()
        await asyncio.gather(one,return_exceptions=True);assert len(calls)==1 and not closed.is_set()
        release.set();assert len((await two)['observations'])==2
        release.clear();entered.clear();closed.clear()
        three=asyncio.create_task(broker.fetch(args));await entered.wait();three.cancel();await asyncio.gather(three,return_exceptions=True)
        assert closed.is_set() and broker.stats['cancelled']==1 and not broker.flights
        await broker.close()
    asyncio.run(scenario())

def test_transport_total_budget_and_concurrency():
    async def scenario():
        active=peak=0
        async def upstream(request):
            nonlocal active,peak
            active+=1;peak=max(active,peak)
            try:await asyncio.sleep(.03)
            finally:active-=1
            return httpx2.Response(200,content=votable_fixture())
        broker=DiscoveryBroker(client=httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)),concurrency=2,total_timeout=2)
        await asyncio.gather(*(broker.fetch((i,0,.001,False,True,None)) for i in range(5)))
        assert peak==2;await broker.close()
        async def hang(request):await asyncio.sleep(1)
        broker=DiscoveryBroker(client=httpx2.AsyncClient(transport=httpx2.MockTransport(hang)),total_timeout=.02)
        with pytest.raises(DataError) as error:await broker.fetch((0,0,.001,False,True,None))
        assert error.value.status==504 and broker.stats['active']==0
        await broker.close()
    asyncio.run(scenario())
