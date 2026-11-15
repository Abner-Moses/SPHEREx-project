from fastapi.testclient import TestClient
from main import app

def test_same_origin_firefly_configuration(monkeypatch):
    monkeypatch.setenv('SPHEREX_FIREFLY_FITS_BASE','http://api:8000')
    monkeypatch.setenv('SPHEREX_FIREFLY_LOADER','/firefly/firefly_loader.js')
    response=TestClient(app).get('/api/config')
    assert response.status_code==200
    assert response.json()['firefly']=={'loader_url':'/firefly/firefly_loader.js','fits_base_url':'http://api:8000'}
    assert response.json()['coordinate_frame']=='ICRS'

def test_invalid_operator_origin_does_not_disclose_credentials(monkeypatch):
    monkeypatch.setenv('SPHEREX_FIREFLY_FITS_BASE','http://username:private@api:8000')
    response=TestClient(app,raise_server_exceptions=False).get('/api/config')
    assert response.status_code==500
    assert 'private' not in response.text
