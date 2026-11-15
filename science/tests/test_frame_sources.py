from contextlib import contextmanager
from fastapi.testclient import TestClient
from main import app
import main
import sequence_service

SID='a'*32;FID='b'*32;PID='c'*32;AID='d'*32

def test_prepared_frame_binds_exact_original_asset_without_retrieval(monkeypatch):
    frame={'id':FID,'product_id':PID,'asset_id':AID,'observation_id':'real-observation'}
    record={'id':AID,'product_id':PID,'observation_id':'real-observation','source_url':'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/test.fits'}
    monkeypatch.setattr(sequence_service,'status',lambda sid:{'id':sid,'status':'ready','frames':[frame]})
    @contextmanager
    def lease(aid):
        assert aid==AID
        yield 'leased file'
    monkeypatch.setattr(main.service,'asset_lease',lease)
    monkeypatch.setattr(main.service,'get_observation',lambda aid:record)
    monkeypatch.setattr(main.service,'retrieve',lambda *args:(_ for _ in ()).throw(AssertionError('No download on frame binding')))
    client=TestClient(app);url=f'/api/sequences/{SID}/frames/{FID}/source'
    response=client.get(url)
    assert response.status_code==200 and response.json()['asset']['id']==AID
    assert not response.json()['retrieval_started']
    record['product_id']='e'*32
    assert client.get(url).status_code==409
    assert client.get(f'/api/sequences/{SID}/frames/unknown/source').status_code==404
