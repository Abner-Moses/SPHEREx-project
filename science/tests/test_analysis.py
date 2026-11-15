"""The unavailable analysis boundary still authorizes/validates real source handles."""
from contextlib import contextmanager
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from archive_client import DataError
import analysis_api as analysis

ASSET = 'a' * 32
SEQUENCE = 'b' * 32


@pytest.fixture
def client():
    app = FastAPI(); app.include_router(analysis.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def accepted(monkeypatch):
    calls = []
    record = {'id': ASSET, 'validation': {'wcs_passed': True, 'policy_version': analysis.POLICY_VERSION,
                                        'scientific_acceptance': {'accepted': True}}}
    @contextmanager
    def lease(identifier):
        calls.append(('lease', identifier))
        try:
            yield 'owned local path, never opened by the unavailable analysis router'
        finally:
            calls.append(('release', identifier))
    def get(identifier):
        calls.append(('metadata', identifier)); return deepcopy(record)
    monkeypatch.setattr(analysis.observation_service, 'asset_lease', lease)
    monkeypatch.setattr(analysis.observation_service, 'get_observation', get)
    return calls, record


def test_capabilities_are_explicit_and_match_parent_task_ids(client):
    result = client.get('/api/analysis/capabilities')
    assert result.status_code == 200
    body = result.json()
    assert {task['id'] for task in body['tasks']} == {'moving', 'anomaly', 'noise', 'spectrum', 'detect'}
    assert all(task['available'] is False and task['reason'] for task in body['tasks'])
    assert body['inference_available'] is False and body['jobs_supported'] is False
    assert body['raw_inspector_available'] is True


@pytest.mark.parametrize('task', ['moving', 'anomaly', 'noise', 'spectrum', 'detect'])
def test_validated_asset_is_leased_released_and_returns_501_without_results(client, accepted, task):
    calls, _ = accepted
    result = client.post('/api/analysis/jobs', json={'task': task, 'asset_id': ASSET})
    assert result.status_code == 501
    body = result.json()['detail']
    assert body['status'] == 'unavailable'
    assert body['inference_performed'] is False and body['job_created'] is False
    assert body['validated_source']['id'] == ASSET
    assert 'results' not in body and 'job_id' not in body
    assert calls == [('lease', ASSET), ('metadata', ASSET), ('release', ASSET)]


@pytest.mark.parametrize('change', [{'wcs_passed': False}, {'policy_version': 'old'},
                                   {'scientific_acceptance': {'accepted': False}},
                                   {'scientific_acceptance': {}}])
def test_rejected_or_stale_quality_does_not_get_validated_source(client, accepted, change):
    calls, record = accepted; record['validation'].update(change)
    result = client.post('/api/analysis/jobs', json={'task': 'detect', 'asset_id': ASSET})
    assert result.status_code == 409
    assert calls[-1] == ('release', ASSET)


@pytest.mark.parametrize('body', [
    {'task': 'moving'},
    {'task': 'moving', 'asset_id': ASSET, 'sequence_id': SEQUENCE},
    {'task': 'moving', 'asset_id': '../file'},
    {'task': 'moving', 'asset_id': True},
    {'task': 'moving', 'asset_id': 3},
    {'task': 'imaginary', 'asset_id': ASSET},
    {'task': 'detect', 'asset_id': ASSET, 'url': 'https://example.com/a.fits'},
    {'task': 'noise', 'sequence_id': SEQUENCE, 'path': '/tmp/arbitrary.fits'},
])
def test_invalid_requests_are_rejected_before_asset_access(client, accepted, body):
    calls, _ = accepted
    assert client.post('/api/analysis/jobs', json=body).status_code == 422
    assert not calls


def test_missing_asset_uses_existing_service_validation_without_retrieving(client, monkeypatch, tmp_path):
    monkeypatch.setattr(analysis.observation_service, 'CACHE', tmp_path)
    result = client.post('/api/analysis/jobs', json={'task': 'detect', 'asset_id': ASSET})
    assert result.status_code == 404
    assert not list(tmp_path.glob('*.fits'))
    assert ASSET not in analysis.observation_service.PINNED or analysis.observation_service.PINNED[ASSET] == 0


@pytest.mark.parametrize('status', [404, 409, 502])
def test_source_service_failures_are_preserved(client, monkeypatch, status):
    @contextmanager
    def missing(identifier):
        raise DataError('Source rejected by owner', status)
        yield
    monkeypatch.setattr(analysis.observation_service, 'asset_lease', missing)
    result = client.post('/api/analysis/jobs', json={'task': 'detect', 'asset_id': ASSET})
    assert result.status_code == status


def test_ready_sequence_manifest_is_checked_but_no_inference_runs(client, monkeypatch):
    calls = []
    def status(identifier):
        calls.append(identifier)
        return {'status': 'ready', 'frames': [{'quality': {'accepted': True}}, {'quality': {'accepted': True}}]}
    monkeypatch.setattr(analysis.sequence_service, 'status', status)
    result = client.post('/api/analysis/jobs', json={'task': 'moving', 'sequence_id': SEQUENCE})
    assert result.status_code == 501
    assert calls == [SEQUENCE]
    assert result.json()['detail']['validated_source']['frames'] == 2


@pytest.mark.parametrize('manifest', [
    {'status': 'preparing', 'frames': []},
    {'status': 'ready', 'frames': [{'quality': {'accepted': True}}]},
    {'status': 'ready', 'frames': [{'quality': {'accepted': True}}, {'quality': {'accepted': False}}]},
])
def test_unready_or_rejected_sequences_are_not_accepted(client, monkeypatch, manifest):
    monkeypatch.setattr(analysis.sequence_service, 'status', lambda identifier: manifest)
    assert client.post('/api/analysis/jobs', json={'task': 'moving', 'sequence_id': SEQUENCE}).status_code == 409


def test_unknown_sequence_remains_404(client, monkeypatch):
    def missing(identifier):
        raise DataError('Sequence not found', 404)
    monkeypatch.setattr(analysis.sequence_service, 'status', missing)
    assert client.post('/api/analysis/jobs', json={'task': 'moving', 'sequence_id': SEQUENCE}).status_code == 404
