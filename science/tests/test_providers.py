from fastapi.testclient import TestClient
from main import app
import archive_client
import data_providers

def test_provider_registry_is_canonical_for_point_and_deep_queries():
    for deep in (True,False):
        assert archive_client.query_parameters(1,2,0,deep,None)["COLLECTION"]==data_providers.collections(deep)
        assert data_providers.collections(deep)==['spherex_dr1','spherex_qr3']+(['spherex_dr1_deep','spherex_qr3_deep'] if deep else [])

def test_provider_manifest_does_not_promise_fresh_connectivity_or_scientific_png():
    with TestClient(app) as client:
        r=client.get('/api/providers');assert r.status_code==200
        d=r.json();assert d['coordinate_frame']=='ICRS'
        assert 'not yet' in d['cube_scientific_access']
        assert len(d['releases'])==4
