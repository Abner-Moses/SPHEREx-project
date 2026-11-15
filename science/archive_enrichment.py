"""Optional, metadata-only IRSA enrichment; never called by normal discovery.

``enrich(record)`` accepts an observation_service/archive_client record and returns
JSON-safe ``tap``, ``datalink`` and ``errors`` objects. Each source has status
``ok``, ``missing``, ``not_available`` or ``upstream``; failures carry an explicit
``error`` (also included in ``errors``). Only sia.obs_publisher_did is authoritative.
No observation ID, FITS header, URL or filename is used to infer that identifier.

Call this synchronous interface from an API worker, not an async event loop.
Concurrent callers share bounded daemon workers, a total deadline, and a bounded
TTL/LRU cache. Timeouts return partial source results; cancellation/remote job
cleanup is best effort. Python cannot forcibly interrupt a blocked DNS resolver
or parser, so timed-out workers retain their capacity slot until they exit.
"""
from collections import OrderedDict
from collections.abc import Mapping
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
import threading
import time
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

import requests
from pyvo.dal import TAPService
from pyvo.dal.adhoc import DatalinkResults

from archive_client import json_value

TAP_URL = "https://irsa.ipac.caltech.edu/TAP"
DATALINK_URL = "https://irsa.ipac.caltech.edu/datalink/links/spherex"
TOTAL_TIMEOUT = float(os.getenv("SPHEREX_ENRICHMENT_TIMEOUT","60"))
REQUEST_TIMEOUT = float(os.getenv("SPHEREX_ENRICHMENT_READ_TIMEOUT","20"))
CLEANUP_TIMEOUT = 2.0
CACHE_TTL = 300.0
ERROR_TTL = 15.0
MAX_CACHE_ENTRIES = 128
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_INFLIGHT = 4
MAX_TAP_ROWS = 1000

_LOCK = threading.RLock()
_CACHE = OrderedDict()
_FLIGHTS = {}
_DL_FLIGHTS = {}


class _InvalidSource(ValueError):
    pass


def _error(source, status, code, message):
    return {"source": source, "status": status, "code": code, "message": message}


def _fail(payload, source, status, code, message):
    payload["status"] = status
    payload["error"] = _error(source, status, code, message)


def _base(did):
    query = ("SELECT * FROM spherex.obscore WHERE obs_publisher_did = '"
             + did.replace("'", "''") + "'") if did else None
    return {
        "tap": {"status": "pending", "columns": [], "row": None,
                "queried_at": None, "service_url": TAP_URL, "query": query},
        "datalink": {"status": "pending", "columns": [], "rows": [],
                     "resources": [], "service_descriptors": [],
                     "source_url": DATALINK_URL + "?" + urlencode({"ID": did}) if did else None},
        "errors": [],
    }


def _finish(result):
    result["errors"] = [result[source]["error"] for source in ("tap", "datalink")
                        if "error" in result[source]]
    return result


def _official_url(url):
    """Allow authentic HTTPS IRSA URLs, including non-science calibration files."""
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("Invalid IRSA URL")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "irsa.ipac.caltech.edu"
            or parts.port not in (None, 443) or parts.username is not None
            or parts.password is not None or parts.fragment or "\\" in url):
        raise ValueError("Only official HTTPS IRSA URLs are allowed")
    path = parts.path
    for _ in range(6):
        if (any(segment in (".", "..") for segment in path.split("/")) or "\\" in path
                or any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError("Invalid IRSA path")
        decoded = unquote(path)
        if decoded == path:
            return parts
        path = decoded
    raise ValueError("Excessively encoded IRSA path")


def _datalink_source(row, did):
    canonical = DATALINK_URL + "?" + urlencode({"ID": did})
    url = row.get("access_url")
    content_type = str(row.get("access_format") or "").lower()
    # Direct image access URLs are not DataLink documents. Use the fixed links
    # endpoint instead; never pass FITS products to a VOTable parser.
    if not url or ("datalink" not in content_type and "/datalink/" not in str(url)):
        return canonical
    try:
        parts = _official_url(url)
        if parts.path != urlsplit(DATALINK_URL).path:
            raise ValueError("Unexpected DataLink service endpoint")
        params = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        if params != [("ID", did)]:
            raise ValueError("DataLink URL must identify exactly the requested publisher DID")
    except ValueError as exc:
        raise _InvalidSource(str(exc)) from exc
    return url


class _DecodedBytes(io.BytesIO):
    def read(self, *args, **kwargs):
        # PyVO's stream_decode_content passes this urllib3 keyword to raw.read.
        kwargs.pop("decode_content", None)
        return super().read(*args, **kwargs)


class _DeadlineSession(requests.Session):
    """A normal redirect-following Session with guarded URLs and bounded I/O.

    A response hook bounds even redirect bodies before Requests consumes them.
    PyVO still handles TAP/UWS and Astropy handles VOTable parsing.
    """
    def __init__(self, deadline, cancelled):
        super().__init__()
        self.deadline = deadline
        self.cancelled = cancelled
        self.hooks["response"].append(self._buffer)

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise TimeoutError("Enrichment total deadline exceeded")
        return remaining

    def send(self, request, **kwargs):
        _official_url(request.url)  # Also guards each Requests redirect target.
        budget = min(REQUEST_TIMEOUT, self.remaining())
        supplied = kwargs.get("timeout")
        if isinstance(supplied, tuple):
            kwargs["timeout"] = tuple(min(budget, v) if v is not None else budget for v in supplied)
        else:
            kwargs["timeout"] = min(budget, supplied) if supplied is not None else budget
        kwargs["stream"] = True
        # Keep Requests' allow_redirects default: PyVO gets the created UWS job
        # URL from the 303 redirect. archive_client.Session disables this.
        return super().send(request, **kwargs)

    def _buffer(self, response, **kwargs):
        try:
            self.remaining()
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_METADATA_BYTES:
                raise ValueError("IRSA metadata exceeds transfer limit")
            body = bytearray()
            for chunk in response.iter_content(65536):
                self.remaining()
                body.extend(chunk)
                if len(body) > MAX_METADATA_BYTES:
                    raise ValueError("IRSA metadata exceeds transfer limit")
        finally:
            response.close()
        response._content = bytes(body)
        response._content_consumed = True
        response.raw = _DecodedBytes(response._content)
        return response

    @contextmanager
    def cleanup(self):
        original = self.deadline, self.cancelled
        self.deadline = time.monotonic() + CLEANUP_TIMEOUT
        self.cancelled = threading.Event()
        try:
            yield
        finally:
            self.deadline, self.cancelled = original


def _attributes(element, names):
    return {name: json_value(getattr(element, name, None)) for name in names}


def _field(field):
    result = _attributes(field, ("name", "ID", "datatype", "arraysize", "description",
                                  "ucd", "utype", "xtype", "ref", "width", "precision", "type"))
    result["id"] = result.pop("ID")
    unit = getattr(field, "unit", None)
    result["unit"] = str(unit) if unit is not None else None
    if hasattr(field, "value"):
        result["value"] = json_value(field.value)
    values = getattr(field, "values", None)
    if values is not None:
        result["values"] = _attributes(values, ("ID", "ref", "type", "null", "min", "max",
                                                "min_inclusive", "max_inclusive", "options"))
    result["links"] = [_attributes(link, ("ID", "href", "title", "value", "content_role",
                                           "content_type", "action")) for link in field.links]
    return result


def _table(table_element):
    table = table_element.to_table(use_names_over_ids=True)
    columns = []
    for name, field in zip(table.colnames, table_element.fields):
        column = _field(field)
        column.update(source_name=field.name, name=name, dtype=str(table[name].dtype))
        columns.append(column)
    rows = [{name: json_value(row[name]) for name in table.colnames} for row in table]
    return {"columns": columns, "rows": rows}


def _group(group):
    result = _attributes(group, ("ID", "name", "ref", "ucd", "utype", "description"))
    result["entries"] = []
    for entry in group.entries:
        if hasattr(entry, "entries"):
            value = _group(entry)
        elif hasattr(entry, "datatype"):
            value = _field(entry)
        else:
            value = _attributes(entry, ("ref", "ucd", "utype"))
        result["entries"].append({"kind": type(entry).__name__, **value})
    return result


def _resource(resource):
    return {
        **_attributes(resource, ("ID", "name", "type", "utype", "description")),
        "params": [_field(p) for p in resource.params],
        "groups": [_group(g) for g in resource.groups],
        "infos": [_attributes(i, ("name", "value", "content")) for i in resource.infos],
        "links": [_attributes(link, ("href", "title", "content_role", "content_type"))
                  for link in resource.links],
        "tables": [_table(t) for t in resource.tables],
        "resources": [_resource(r) for r in resource.resources],
    }


def _overflow(result):
    def walk(element):
        if any(i.name == "QUERY_STATUS" and i.value == "OVERFLOW" for i in element.infos):
            return True
        return any(walk(child) for name in ("resources", "tables")
                   for child in getattr(element, name, ()))
    return walk(result.votable)


def _fetch_tap(did, query, session):
    job = None
    completed = False
    try:
        job = TAPService(TAP_URL, session=session).submit_job(query, maxrec=MAX_TAP_ROWS)
        job.run()
        while True:
            session.remaining()
            phase = job.phase  # PyVO parses UWS; no version-specific wait semantics.
            if phase == "COMPLETED":
                completed = True
                break
            if phase in ("ERROR", "ABORTED"):
                job.raise_if_error()
                raise RuntimeError("IRSA TAP job ended in " + phase)
            session.cancelled.wait(min(.25, session.remaining()))
        result = job.fetch_result()
        payload = _table(result.resultstable)
        rows = payload.pop("rows")
        payload["row"] = None
        if _overflow(result):
            payload["rows"] = rows
            _fail(payload, "tap", "upstream", "overflow", "TAP metadata response was truncated (OVERFLOW)")
        elif not rows:
            _fail(payload, "tap", "not_available", "no_match", "No TAP row for the publisher DID")
        elif len(rows) != 1 or rows[0].get("obs_publisher_did") != did:
            payload["rows"] = rows  # Preserve ambiguous upstream metadata without choosing a row.
            _fail(payload, "tap", "upstream", "identifier_mismatch",
                  "TAP must return exactly one row matching the publisher DID")
        else:
            payload.update(status="ok", row=rows[0])
        return payload
    finally:
        if job is not None:
            # Cleanup has its own small grace period and never extends the
            # caller's deadline. A timed-out daemon keeps its slot until done.
            with session.cleanup():
                if not completed:
                    try:
                        job.abort()
                    except Exception:
                        pass
                try:
                    job.delete()
                except Exception:
                    pass


def _fetch_datalink(did, url, session):
    result = DatalinkResults.from_result_url(url, session=session)
    payload = _table(result.resultstable)
    payload["resources"] = [_resource(r) for r in result.votable.resources]
    iterator = getattr(result, "iter_adhocservices", None)
    payload["service_descriptors"] = [_resource(r) for r in iterator()] if callable(iterator) else []
    payload["service_descriptors_supported"] = callable(iterator)
    payload["link_validation"] = []
    for index, row in enumerate(payload["rows"]):
        url = row.get("access_url")
        if not url:
            continue  # Service-def and error rows legitimately have no access URL.
        try:
            _official_url(url)
            validation = {"row": index, "status": "ok"}
        except ValueError as exc:
            validation = {"row": index, "status": "not_available", "code": "untrusted_url",
                          "message": str(exc)}
        payload["link_validation"].append(validation)
    payload["service_validation"] = []
    for descriptor in payload["service_descriptors"]:
        for param in descriptor["params"]:
            if param["name"] != "accessURL":
                continue
            try:
                _official_url(param["value"])
                validation = {"service_def": descriptor["ID"], "status": "ok"}
            except ValueError as exc:
                validation = {"service_def": descriptor["ID"], "status": "not_available",
                              "code": "untrusted_url", "message": str(exc)}
            payload["service_validation"].append(validation)
    if _overflow(result):
        _fail(payload, "datalink", "upstream", "overflow", "DataLink metadata is truncated")
    elif any(row.get("ID",row.get("id")) != did for row in payload["rows"]):
        _fail(payload, "datalink", "upstream", "identifier_mismatch", "DataLink returned another publisher DID")
    elif any(row.get("error_message") for row in payload["rows"]):
        missing = all(str(row.get("error_message", "")).startswith("NotFoundFault")
                      for row in payload["rows"])
        _fail(payload, "datalink", "not_available" if missing else "upstream", "datalink_error",
              "DataLink returned error_message; inspect original rows")
    elif any(v["status"] != "ok" for v in payload["link_validation"] + payload["service_validation"]):
        _fail(payload, "datalink", "upstream", "untrusted_url",
              "DataLink contains untrusted product/service links; inspect link_validation and service_validation")
    elif not payload["rows"]:
        _fail(payload, "datalink", "not_available", "no_links", "DataLink returned no links")
    else:
        payload["status"] = "ok"
    return payload

def datalink(record):
    """Independent DataLink lookup: calibration access never waits for TAP jobs.

    The fixed official resolver plus authoritative publisher DID is sufficient;
    this does not follow an arbitrary advertised URL or download any science file.
    """
    did=record.get("sia",{}).get("obs_publisher_did")
    if not isinstance(did,str) or not did.strip() or len(did)>4096 or any(ord(c)<32 for c in did):
        payload={"status":"missing","rows":[],"columns":[]}
        _fail(payload,"datalink","missing","missing_identifier","Authoritative publisher DID is missing.")
        return payload
    key="datalink:"+did
    with _LOCK:
        cached=_CACHE.get(key)
        if cached and cached[0]>time.monotonic():return deepcopy(cached[2])
        future=_DL_FLIGHTS.get(did);owner=future is None
        if owner:
            if len(_DL_FLIGHTS)>=MAX_INFLIGHT:
                payload={"rows":[],"columns":[]};_fail(payload,"datalink","upstream","busy","DataLink worker capacity reached.");return payload
            future=Future();_DL_FLIGHTS[did]=future
    if not owner:
        try:return deepcopy(future.result(timeout=25))
        except FutureTimeout:
            payload={"rows":[],"columns":[]};_fail(payload,"datalink","upstream","timeout","DataLink lookup exceeded its deadline.");return payload
    source=DATALINK_URL+"?"+urlencode({"ID":did})
    try:
        with _DeadlineSession(time.monotonic()+20,threading.Event()) as session:
            payload=_fetch_datalink(did,source,session)
        payload.update(source_url=source,queried_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        payload={"source_url":source,"rows":[],"columns":[]};_fail(payload,"datalink","upstream","request_failed",str(exc))
    with _LOCK:
        encoded=len(json.dumps(payload,allow_nan=False).encode())
        if encoded<=MAX_CACHE_BYTES:
            _CACHE[key]=(time.monotonic()+(CACHE_TTL if payload["status"]=="ok" else ERROR_TTL),encoded,deepcopy(payload))
            while len(_CACHE)>MAX_CACHE_ENTRIES or sum(v[1] for v in _CACHE.values())>MAX_CACHE_BYTES:_CACHE.popitem(last=False)
        future.set_result(payload);_DL_FLIGHTS.pop(did,None)
    return deepcopy(payload)


class _Flight:
    def __init__(self, did):
        self.future = Future()
        self.deadline = time.monotonic() + TOTAL_TIMEOUT
        self.cancelled = threading.Event()
        self.result = _base(did)


def _publish(did, flight):
    # Called with _LOCK held. First completion (worker or deadline) wins.
    if flight.future.done():
        return
    result = _finish(deepcopy(flight.result))
    now = time.monotonic()
    ttl = CACHE_TTL if not result["errors"] else ERROR_TTL
    size = len(json.dumps(result, allow_nan=False).encode("utf-8"))
    if size <= MAX_CACHE_BYTES:
        _CACHE[did] = (now + ttl, size, result)
        _CACHE.move_to_end(did)
        while (len(_CACHE) > MAX_CACHE_ENTRIES
               or sum(entry[1] for entry in _CACHE.values()) > MAX_CACHE_BYTES):
            _CACHE.popitem(last=False)
    flight.future.set_result(result)


def _worker(did, flight):
    try:
        with _DeadlineSession(flight.deadline, flight.cancelled) as session:
            for source in ("tap", "datalink"):
                if flight.cancelled.is_set():
                    break
                with _LOCK:
                    snapshot = deepcopy(flight.result)
                    if source == "tap":
                        flight.result[source]["queried_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    session.remaining()
                    if source == "tap":
                        payload = _fetch_tap(did, snapshot["tap"]["query"], session)
                    else:
                        row = snapshot["tap"].get("row") or {}
                        url = _datalink_source(row, did)
                        payload = _fetch_datalink(did, url, session)
                        payload["source_url"] = url
                except Exception as exc:
                    payload = {}
                    timeout = isinstance(exc, (TimeoutError, requests.Timeout)) or time.monotonic() >= flight.deadline
                    code = "timeout" if timeout else "request_failed"
                    if isinstance(exc, _InvalidSource):
                        code = "invalid_source"
                    _fail(payload, source, "upstream", code, str(exc) or type(exc).__name__)
                with _LOCK:
                    if not flight.future.done():
                        flight.result[source].update(payload)
    finally:
        with _LOCK:
            if not flight.future.done():
                for source in ("tap", "datalink"):
                    if flight.result[source]["status"] == "pending":
                        _fail(flight.result[source], source, "upstream", "timeout", "Enrichment total deadline exceeded")
                _publish(did, flight)
            if _FLIGHTS.get(did) is flight:
                del _FLIGHTS[did]


def enrich(record):
    """Return source-separated metadata without mutating ``record`` or fetching FITS.

    The only network entry points are fixed official TAP/DataLink services;
    advertised DataLink URLs must match both the service and authoritative DID.
    Original columns/rows are kept even when a returned link is untrusted.
    Returned values are independent deep copies, including cache/singleflight hits.
    """
    sia = record.get("sia") if isinstance(record, Mapping) else None
    did = sia.get("obs_publisher_did") if isinstance(sia, Mapping) else None
    if not isinstance(did, str) or not did.strip() or len(did) > 4096 or any(ord(c) < 32 for c in did):
        result = _base(None)
        for source in ("tap", "datalink"):
            _fail(result[source], source, "missing", "missing_identifier",
                  "A valid authoritative sia.obs_publisher_did is required")
        return _finish(result)
    with _LOCK:
        now = time.monotonic()
        for key in list(_CACHE):
            if _CACHE[key][0] <= now:
                del _CACHE[key]
        if did in _CACHE:
            _CACHE.move_to_end(did)
            return deepcopy(_CACHE[did][2])
        flight = _FLIGHTS.get(did)
        if flight is None:
            flight = _Flight(did)
            if len(_FLIGHTS) >= MAX_INFLIGHT:
                for source in ("tap", "datalink"):
                    _fail(flight.result[source], source, "upstream", "busy", "Enrichment worker capacity reached; retry later")
                return _finish(flight.result)
            _FLIGHTS[did] = flight
            threading.Thread(target=_worker, args=(did, flight), daemon=True,
                             name="irsa-enrichment").start()
    try:
        result = flight.future.result(timeout=max(0, flight.deadline - time.monotonic()))
    except FutureTimeout:
        with _LOCK:
            flight.cancelled.set()
            if not flight.future.done():
                for source in ("tap", "datalink"):
                    if flight.result[source]["status"] == "pending":
                        _fail(flight.result[source], source, "upstream", "timeout", "Enrichment total deadline exceeded")
                _publish(did, flight)
            result = flight.future.result()
    return deepcopy(result)
