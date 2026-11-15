import pytest
from fastapi.testclient import TestClient
from main import app
import main
import catalog_service

@pytest.mark.parametrize('prediction',[True,False])
def test_object_search_reuses_discovery_and_keeps_fixed_epoch_position(monkeypatch,prediction):
    obj={'name':'Barnard test only','ra':269.452,'dec':4.693,'epoch':'J2000','propagated_position':{'status':'available','ra':269.446,'dec':4.770,'epoch':2026.65} if prediction else {'status':'unavailable'}}
    monkeypatch.setattr(catalog_service,'resolve',lambda name,epoch:obj)
    called=[]
    async def discovery(broker,query):
        called.append(query)
        return {'observations':[],'columns':[],'completeness':{'status':'complete'},'timings':{}}
    monkeypatch.setattr(main.discovery_service,'search',discovery)
    with TestClient(app) as client:
        result=client.post('/api/object-search',json={'name':'Barnard star','epoch':2026.65})
    assert result.status_code==200
    point=obj['propagated_position'] if prediction else obj
    assert called[0].ra==point['ra'] and called[0].dec==point['dec']
    assert called[0].search_radius_deg==0 and not result.json()['object_search']['object_following_sequence']
    assert bool(result.json()['object_search']['warnings']) is not prediction
    assert result.json()['catalog_object']==obj

def test_object_search_failure_does_not_query_irsa(monkeypatch):
    def fail(*args):raise catalog_service.CatalogError('SIMBAD unavailable',502)
    monkeypatch.setattr(catalog_service,'resolve',fail)
    def unexpected(*args):pytest.fail('No archive query after name failure')
    monkeypatch.setattr(main.discovery_service,'search',unexpected)
    assert TestClient(app).post('/api/object-search',json={'name':'test'}).status_code==502

@pytest.mark.parametrize('epoch',[True,'NaN',float('inf'),1700])
def test_invalid_object_epoch_is_a_client_error(epoch):
    body='{"name":"test","epoch":Infinity}' if epoch==float('inf') else None
    client=TestClient(app,raise_server_exceptions=False)
    response=client.post('/api/object-search',content=body,headers={'Content-Type':'application/json'}) if body else client.post('/api/object-search',json={'name':'test','epoch':epoch})
    assert response.status_code==422
