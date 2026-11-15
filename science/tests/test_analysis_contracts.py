import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from main import app
from analysis_contracts import DetectionMeasurement

def test_future_schema_does_not_claim_installed_inference():
    response=TestClient(app).get('/api/analysis/schema')
    assert response.status_code==200
    assert not response.json()['implemented_inference']
    assert 'ProcessingProvenance' in response.json()['schema']['$defs']

@pytest.mark.parametrize('field,value',[('confidence',1.1),('ra',360),('dec',91),('x',float('nan')),('uncertainty_arcsec',-1)])
def test_detection_contract_rejects_invalid_scientific_values(field,value):
    data={'source_id':'example-test-only','asset_id':'a'*32,'observation_id':'test','x':0,'y':0,'ra':0,'dec':0,'observation_timestamp':'2026-01-01T00:00:00Z'}
    data[field]=value
    with pytest.raises(ValidationError):DetectionMeasurement(**data)
