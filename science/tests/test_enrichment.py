"""Network-isolated tests for optional IRSA TAP/DataLink enrichment."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
import requests
from astropy.io.votable import from_table, writeto
from astropy.io.votable.tree import Group, Info, Param, Resource
from astropy.table import Column, MaskedColumn, Table
from pyvo.dal.adhoc import DatalinkResults
from pyvo.dal.tap import TAPResults
from urllib3.response import HTTPResponse

import archive_enrichment as enrichment

DID = "ivo://irsa.ipac/spherex/dr1/authoritative-product"
PRODUCT = "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/image.fits"
CALIBRATION = "https://irsa.ipac.caltech.edu/data/SPHEREx/calibration/flat.ecsv"


def record(did=DID):
    return {"id": "a" * 32, "observation_id": "not-the-publisher-DID",
            "source_url": PRODUCT, "sia": {"obs_publisher_did": did, "access_url": PRODUCT}}


def votable(table, descriptor=False, overflow=False):
    vot = from_table(table)
    vot.resources[0].infos.append(Info(name="QUERY_STATUS", value="OK"))
    if overflow:
        vot.resources[0].infos.append(Info(name="QUERY_STATUS", value="OVERFLOW"))
    if descriptor:
        resource = Resource(ID="cutout", type="meta", utype="adhoc:service")
        resource.description = "Original cutout service"
        resource.params.extend([
            Param(vot, name="standardID", datatype="char", arraysize="*",
                  value="ivo://ivoa.net/std/SODA#sync-1.0"),
            Param(vot, name="accessURL", datatype="char", arraysize="*", value=PRODUCT),
        ])
        group = Group(vot, name="inputParams")
        group.entries.append(Param(vot, name="ID", datatype="char", arraysize="*", value=DID))
        resource.groups.append(group)
        vot.resources.append(resource)
    return vot


def tap_result(did=DID, *, empty=False, duplicate=False, overflow=False, url=PRODUCT):
    table = Table()
    table["obs_publisher_did"] = [did]
    table["access_url"] = [url]
    table["access_format"] = ["application/x-votable+xml;content=datalink" if "/datalink/" in url else "image/fits"]
    table["unusual_tap_column"] = Column([1.25], unit="s", description="Full TAP metadata")
    table["masked"] = MaskedColumn([9], mask=[True])
    table["vector"] = [[1, 2, 3]]
    table["nonfinite"] = [np.nan]
    if empty:
        table = table[:0]
    if duplicate:
        table.add_row(table[0])
    return TAPResults(votable(table, overflow=overflow))


def datalink_result(did=DID, *, empty=False, urls=None, error="", overflow=False):
    urls = [PRODUCT, CALIBRATION] if urls is None else urls
    table = Table()
    table["ID"] = [did] * len(urls)
    table["access_url"] = urls
    table["service_def"] = [""] * len(urls)
    table["error_message"] = [error] * len(urls)
    table["semantics"] = ["#this" if i == 0 else "#calibration" for i in range(len(urls))]
    table["content_type"] = ["application/fits"] * len(urls)
    table["content_length"] = Column([2880] * len(urls), unit="byte", description="Original size")
    table["custom_datalink_column"] = ["Do not drop"] * len(urls)
    if empty:
        table = table[:0]
    return DatalinkResults(votable(table, descriptor=True, overflow=overflow))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Unit test attempted network access")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", no_network)
    with enrichment._LOCK:
        assert not enrichment._FLIGHTS
        enrichment._CACHE.clear()
    yield
    deadline = time.monotonic() + 2
    while enrichment._FLIGHTS and time.monotonic() < deadline:
        time.sleep(.005)
    assert not enrichment._FLIGHTS
    enrichment._CACHE.clear()


@pytest.fixture
def protocols(monkeypatch):
    calls = []
    state = {"tap": tap_result(), "datalink": datalink_result(), "phase": "COMPLETED"}

    class Job:
        def run(self):
            calls.append(("run",))

        @property
        def phase(self):
            calls.append(("phase",))
            return state["phase"]

        def fetch_result(self):
            calls.append(("fetch",))
            value = state["tap"]
            if isinstance(value, Exception):
                raise value
            return value

        def raise_if_error(self):
            raise RuntimeError("Upstream UWS job failed")

        def abort(self):
            calls.append(("abort",))

        def delete(self):
            calls.append(("delete",))

    class Service:
        def __init__(self, url, *, session):
            calls.append(("service", url, session))
            assert isinstance(session, requests.Session)
            assert not isinstance(session, __import__("archive_client").Session)

        def submit_job(self, query, *, maxrec):
            calls.append(("query", query, maxrec))
            return Job()

    def links(url, *, session):
        calls.append(("datalink", url, session))
        value = state["datalink"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(enrichment, "TAPService", Service)
    monkeypatch.setattr(enrichment.DatalinkResults, "from_result_url", links)
    return state, calls


def test_full_source_metadata_json_and_no_record_mutation(protocols):
    _, calls = protocols
    candidate = record()
    original = deepcopy(candidate)
    result = enrichment.enrich(candidate)
    assert candidate == original
    assert result["errors"] == []
    tap, datalink = result["tap"], result["datalink"]
    assert tap["status"] == datalink["status"] == "ok"
    assert tap["service_url"] == enrichment.TAP_URL
    assert DID in tap["query"] and "SELECT *" in tap["query"]
    assert tap["queried_at"].endswith("+00:00")
    assert tap["row"]["unusual_tap_column"] == 1.25
    assert tap["row"]["masked"] is None and tap["row"]["nonfinite"] is None
    assert tap["row"]["vector"] == [1, 2, 3]
    col = next(c for c in tap["columns"] if c["name"] == "unusual_tap_column")
    assert col["unit"] == "s" and col["description"] == "Full TAP metadata"
    assert col["dtype"] == "float64" and col["datatype"] == "double"
    assert {c["name"] for c in tap["columns"]} == set(tap["row"])
    assert datalink["rows"][1]["access_url"] == CALIBRATION
    assert datalink["rows"][1]["custom_datalink_column"] == "Do not drop"
    assert {c["name"] for c in datalink["columns"]} == set(datalink["rows"][0])
    assert all(v["status"] == "ok" for v in datalink["link_validation"] + datalink["service_validation"])
    descriptor = datalink["service_descriptors"][0]
    assert descriptor["ID"] == "cutout"
    assert descriptor["groups"][0]["entries"][0]["value"] == DID
    assert datalink["resources"][0]["tables"][0]["rows"] == datalink["rows"]
    json.dumps(result, allow_nan=False)
    assert len([c for c in calls if c[0] == "query"]) == 1
    assert ("delete",) in calls and ("abort",) not in calls


@pytest.mark.parametrize("candidate", [None, {}, {"sia": {}}, record(None), record(" "), record(3),
    {"obs_publisher_did": DID, "observation_id": DID, "source_url": PRODUCT}, record("bad\nID")])
def test_missing_authoritative_identifier_never_queries(candidate):
    result = enrichment.enrich(candidate)
    assert result["tap"]["status"] == result["datalink"]["status"] == "missing"
    assert {e["code"] for e in result["errors"]} == {"missing_identifier"}
    assert result["tap"]["query"] is None


def test_did_is_adql_escaped_not_inferred(protocols):
    state, calls = protocols
    did = "ivo://irsa.ipac/spherex/a'b"
    state["tap"] = tap_result(did)
    state["datalink"] = datalink_result(did)
    result = enrichment.enrich(record(did))
    query = next(c[1] for c in calls if c[0] == "query")
    assert query == "SELECT * FROM spherex.obscore WHERE obs_publisher_did = 'ivo://irsa.ipac/spherex/a''b'"
    assert "not-the-publisher-DID" not in query
    assert parse_qs(urlsplit(result["datalink"]["source_url"]).query) == {"ID": [did]}


@pytest.mark.parametrize("kind", ["tap", "datalink"])
def test_one_upstream_failure_keeps_other_source(protocols, kind):
    state, _ = protocols
    state[kind] = RuntimeError("Upstream temporarily unavailable")
    result = enrichment.enrich(record())
    other = "tap" if kind == "datalink" else "datalink"
    assert result[kind]["status"] == "upstream"
    assert result[other]["status"] == "ok"
    assert result["errors"][0]["source"] == kind
    assert "temporarily unavailable" in result["errors"][0]["message"]


def test_empty_results_are_not_available(protocols):
    state, _ = protocols
    state.update(tap=tap_result(empty=True), datalink=datalink_result(empty=True))
    result = enrichment.enrich(record())
    assert result["tap"]["status"] == result["datalink"]["status"] == "not_available"
    assert result["tap"]["columns"] and result["tap"]["row"] is None
    assert {e["code"] for e in result["errors"]} == {"no_match", "no_links"}


@pytest.mark.parametrize("result_factory", [lambda: tap_result("other"), lambda: tap_result(duplicate=True)])
def test_tap_does_not_select_wrong_or_ambiguous_row(protocols, result_factory):
    state, _ = protocols
    state["tap"] = result_factory()
    result = enrichment.enrich(record())
    assert result["tap"]["error"]["code"] == "identifier_mismatch"
    assert result["tap"]["row"] is None
    assert result["tap"]["rows"]
    assert result["datalink"]["status"] == "ok"


@pytest.mark.parametrize("kwargs,code", [({"did": "other"}, "identifier_mismatch"),
    ({"error": "NotFoundFault: missing"}, "datalink_error"), ({"overflow": True}, "overflow")])
def test_datalink_protocol_errors_are_explicit(protocols, kwargs, code):
    state, _ = protocols
    state["datalink"] = datalink_result(**kwargs)
    result = enrichment.enrich(record())
    assert result["datalink"]["error"]["code"] == code
    assert result["datalink"]["rows"]


def test_tap_overflow_not_claimed_complete(protocols):
    state, _ = protocols
    state["tap"] = tap_result(overflow=True)
    result = enrichment.enrich(record())
    assert result["tap"]["status"] == "upstream"
    assert "truncated" in result["tap"]["error"]["message"]
    assert result["tap"]["error"]["code"] == "overflow"
    assert result["tap"]["rows"][0]["unusual_tap_column"] == 1.25
    assert result["tap"]["columns"]


def test_datalink_not_found_fault_is_not_available(protocols):
    state, _ = protocols
    state["datalink"] = datalink_result(error="NotFoundFault: no product")
    result = enrichment.enrich(record())
    assert result["datalink"]["status"] == "not_available"
    assert result["datalink"]["rows"][0]["error_message"] == "NotFoundFault: no product"


def test_service_descriptor_untrusted_url_is_preserved_and_flagged(protocols):
    state, _ = protocols
    descriptor = next(state["datalink"].iter_adhocservices())
    descriptor.params[1].value = "https://evil.example/service"
    result = enrichment.enrich(record())["datalink"]
    assert result["error"]["code"] == "untrusted_url"
    assert result["service_validation"][0]["status"] == "not_available"
    assert result["service_descriptors"][0]["params"][1]["value"] == "https://evil.example/service"


def test_descriptors_remain_in_resources_without_pyvo_convenience_method(protocols, monkeypatch):
    state, _ = protocols
    monkeypatch.setattr(state["datalink"], "iter_adhocservices", None)
    result = enrichment.enrich(record())["datalink"]
    assert result["status"] == "ok"
    assert result["service_descriptors_supported"] is False
    assert result["service_descriptors"] == []
    assert result["resources"][1]["utype"] == "adhoc:service"


@pytest.mark.parametrize("url", ["http://irsa.ipac.caltech.edu/data/file.fits", "https://evil.example/file",
    "https://irsa.ipac.caltech.edu.evil.example/file", "https://user@irsa.ipac.caltech.edu/file",
    "https://irsa.ipac.caltech.edu:444/file", "https://irsa.ipac.caltech.edu/data/../file",
    "https://irsa.ipac.caltech.edu/data/%252e%252e/file", "//irsa.ipac.caltech.edu/file",
    "https://irsa.ipac.caltech.edu/file#fragment", "https://irsa.ipac.caltech.edu/evil\\file",
    "https://irsa.ipac.caltech.edu/data/%0afile"])
def test_untrusted_product_links_preserved_but_flagged(protocols, url):
    state, _ = protocols
    state["datalink"] = datalink_result(urls=[url, CALIBRATION])
    result = enrichment.enrich(record())
    links = result["datalink"]
    assert links["rows"][0]["access_url"] == url
    assert links["error"]["code"] == "untrusted_url"
    assert links["link_validation"][1]["status"] == "ok"


@pytest.mark.parametrize("url", ["https://evil.example/datalink/links/spherex?ID=x",
    enrichment.DATALINK_URL + "?ID=another",
    enrichment.DATALINK_URL + "?ID=" + DID + "&ID=" + DID,
    "https://irsa.ipac.caltech.edu/datalink/links/other?ID=" + DID,
    enrichment.DATALINK_URL + "?ID=" + DID + "&TARGET=https://evil.example"])
def test_bad_advertised_datalink_source_never_fetched(protocols, url):
    state, calls = protocols
    state["tap"] = tap_result(url=url)
    result = enrichment.enrich(record())
    assert result["tap"]["status"] == "ok"
    assert result["datalink"]["error"]["code"] == "invalid_source"
    assert not any(c[0] == "datalink" for c in calls)


def test_valid_advertised_datalink_source(protocols):
    state, calls = protocols
    url = enrichment.DATALINK_URL + "?ID=" + DID
    state["tap"] = tap_result(url=url)
    result = enrichment.enrich(record())
    assert result["datalink"]["source_url"] == url
    assert any(c[:2] == ("datalink", url) for c in calls)


def test_cache_copies_and_keys_by_did(protocols):
    _, calls = protocols
    first = enrichment.enrich(record())
    first["tap"]["row"]["unusual_tap_column"] = "mutated"
    first["datalink"]["rows"].clear()
    candidate = record()
    candidate["id"] = "b" * 32  # Cutouts of the same authoritative product reuse metadata.
    second = enrichment.enrich(candidate)
    assert second["tap"]["row"]["unusual_tap_column"] == 1.25
    assert len(second["datalink"]["rows"]) == 2
    assert len([c for c in calls if c[0] == "query"]) == 1


def test_cache_expiry_and_lru_bounds(protocols, monkeypatch):
    state, calls = protocols
    monkeypatch.setattr(enrichment, "MAX_CACHE_ENTRIES", 2)
    for did in ["a", "b", "a", "c"]:
        state["tap"], state["datalink"] = tap_result(did), datalink_result(did)
        enrichment.enrich(record(did))
    assert list(enrichment._CACHE) == ["a", "c"]
    expires, size, value = enrichment._CACHE["a"]
    enrichment._CACHE["a"] = (time.monotonic() - 1, size, value)
    state["tap"], state["datalink"] = tap_result("a"), datalink_result("a")
    enrichment.enrich(record("a"))
    assert len([c for c in calls if c[0] == "query"]) == 4


def test_cache_byte_bound(protocols, monkeypatch):
    monkeypatch.setattr(enrichment, "MAX_CACHE_BYTES", 1)
    enrichment.enrich(record())
    assert not enrichment._CACHE


def test_errors_have_shorter_cache_ttl(protocols):
    state, _ = protocols
    state["tap"] = RuntimeError("Retryable failure")
    enrichment.enrich(record())
    expires = enrichment._CACHE[DID][0]
    assert 0 < expires - time.monotonic() <= enrichment.ERROR_TTL
    assert enrichment.ERROR_TTL < enrichment.CACHE_TTL


def test_inflight_deduplication_and_independent_results(protocols, monkeypatch):
    original = enrichment._fetch_tap
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(1)
        return original(*args)
    monkeypatch.setattr(enrichment, "_fetch_tap", slow)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(enrichment.enrich, record()) for _ in range(6)]
        assert entered.wait(1)
        release.set()
        results = [f.result(timeout=2) for f in futures]
    assert len([c for c in protocols[1] if c[0] == "query"]) == 1
    results[0]["tap"]["row"]["obs_publisher_did"] = "changed"
    assert results[1]["tap"]["row"]["obs_publisher_did"] == DID


def test_total_deadline_bounded_even_for_stuck_worker(protocols, monkeypatch):
    release, exited = threading.Event(), threading.Event()
    def stuck(*args):
        try:
            release.wait(1)
            return {"status": "ok", "row": {"late": "must not overwrite timeout"}}
        finally:
            exited.set()
    monkeypatch.setattr(enrichment, "TOTAL_TIMEOUT", .05)
    monkeypatch.setattr(enrichment, "MAX_INFLIGHT", 1)
    monkeypatch.setattr(enrichment, "_fetch_tap", stuck)
    started = time.monotonic()
    try:
        result = enrichment.enrich(record())
        assert time.monotonic() - started < .4
        assert {e["code"] for e in result["errors"]} == {"timeout"}
        assert enrichment._FLIGHTS[DID].cancelled.is_set()
        assert enrichment.enrich(record()) == result
        assert enrichment.enrich(record("another"))["tap"]["error"]["code"] == "busy"
    finally:
        release.set()
        assert exited.wait(1)
    assert enrichment.enrich(record())["tap"]["status"] == "upstream"


def test_datalink_timeout_keeps_completed_tap(protocols, monkeypatch):
    release = threading.Event()
    def stuck(*args):
        release.wait(1)
        return {"status": "ok"}
    monkeypatch.setattr(enrichment, "TOTAL_TIMEOUT", .05)
    monkeypatch.setattr(enrichment, "_fetch_datalink", stuck)
    try:
        result = enrichment.enrich(record())
        assert result["tap"]["status"] == "ok"
        assert result["datalink"]["error"]["code"] == "timeout"
    finally:
        release.set()


def test_timeout_aborts_and_deletes_uws_job(protocols, monkeypatch):
    state, calls = protocols
    state["phase"] = "EXECUTING"
    monkeypatch.setattr(enrichment, "TOTAL_TIMEOUT", .03)
    result = enrichment.enrich(record())
    deadline = time.monotonic() + 1
    while enrichment._FLIGHTS and time.monotonic() < deadline:
        time.sleep(.005)
    assert result["tap"]["error"]["code"] == "timeout"
    assert ("abort",) in calls and ("delete",) in calls


def test_uws_error_aborts_and_deletes_without_breaking_datalink(protocols):
    state, calls = protocols
    state["phase"] = "ERROR"
    result = enrichment.enrich(record())
    assert result["tap"]["status"] == "upstream"
    assert result["datalink"]["status"] == "ok"
    assert ("abort",) in calls and ("delete",) in calls


def wire_response(request, body=b"", status=200, headers=None):
    response = requests.Response()
    response.status_code = status
    response.url = request.url
    response.request = request
    response.headers.update(headers or {})
    response.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False)
    return response


def test_real_pyvo_protocol_and_requests_follow_creation_redirect(monkeypatch):
    calls = []
    job_url = enrichment.TAP_URL + "/async/42"
    uws = (f'<uws:job xmlns:uws="http://www.ivoa.net/xml/UWS/v1.0" '
           f'xmlns:xlink="http://www.w3.org/1999/xlink"><uws:jobId>42</uws:jobId>'
           f'<uws:phase>COMPLETED</uws:phase><uws:results><uws:result id="result" '
           f'xlink:href="{job_url}/results/result"/></uws:results></uws:job>').encode()
    def xml(result):
        stream = io.BytesIO()
        writeto(result.votable, stream)
        return stream.getvalue()
    tap_xml, links_xml = xml(tap_result()), xml(datalink_result())
    def adapter_send(self, request, **kwargs):
        calls.append((request.method, request.url, kwargs))
        path = urlsplit(request.url).path
        if request.method == "POST" and path == "/TAP/async":
            assert "obs_publisher_did" in request.body
            return wire_response(request, status=303, headers={"Location": job_url})
        if request.method == "POST" and path.endswith("/phase"):
            return wire_response(request, status=303, headers={"Location": job_url})
        if request.method == "DELETE":
            return wire_response(request)
        if path == "/TAP/async/42":
            return wire_response(request, uws)
        if path.endswith("/results/result"):
            return wire_response(request, tap_xml)
        if path == "/datalink/links/spherex":
            return wire_response(request, links_xml)
        pytest.fail("Unexpected request: " + request.url)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", adapter_send)
    result = enrichment.enrich(record())
    assert result["errors"] == []
    assert ("GET", job_url) in [(m, u) for m, u, _ in calls]
    assert ("DELETE", job_url) in [(m, u) for m, u, _ in calls]
    assert all(0 < c[2]["timeout"] <= enrichment.REQUEST_TIMEOUT for c in calls)
    assert result["datalink"]["rows"][1]["access_url"] == CALIBRATION


def test_redirect_to_untrusted_host_never_reached(monkeypatch):
    calls = []
    def adapter_send(self, request, **kwargs):
        calls.append(request.url)
        return wire_response(request, status=303, headers={"Location": "https://evil.example/steal"})
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", adapter_send)
    with enrichment._DeadlineSession(time.monotonic() + 1, threading.Event()) as session:
        with pytest.raises(ValueError, match="official HTTPS"):
            session.post(enrichment.TAP_URL + "/async")
    assert calls == [enrichment.TAP_URL + "/async"]


@pytest.mark.parametrize("declared", [True, False])
def test_transfer_bound(monkeypatch, declared):
    monkeypatch.setattr(enrichment, "MAX_METADATA_BYTES", 4)
    def adapter_send(self, request, **kwargs):
        return wire_response(request, b"too-large", headers={"Content-Length": "9"} if declared else {})
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", adapter_send)
    with enrichment._DeadlineSession(time.monotonic() + 1, threading.Event()) as session:
        with pytest.raises(ValueError, match="transfer limit"):
            session.get(enrichment.DATALINK_URL)


def test_session_checks_deadline_and_cancellation_without_network():
    with enrichment._DeadlineSession(time.monotonic() - 1, threading.Event()) as session:
        with pytest.raises(TimeoutError):
            session.get(enrichment.TAP_URL)
    cancelled = threading.Event()
    cancelled.set()
    with enrichment._DeadlineSession(time.monotonic() + 1, cancelled) as session:
        with pytest.raises(TimeoutError):
            session.get(enrichment.TAP_URL)
