"""Cancellable SIA transport, independent of HiPS and scientific FITS workers."""
import asyncio
import copy
import json
import os
import time
from functools import partial
import httpx2
from starlette.concurrency import run_in_threadpool
import archive_client as archive
import observation_service as observations

class CacheMiss(Exception):
    pass

def cache_only(*args):
    raise CacheMiss()

class DiscoveryBroker:
    def __init__(self, client=None, concurrency=None, total_timeout=None):
        self.limit = concurrency or int(os.getenv("SPHEREX_DISCOVERY_CONCURRENCY", "2"))
        self.timeout = total_timeout or float(os.getenv("SPHEREX_QUERY_TOTAL_TIMEOUT", "120"))
        self.semaphore = asyncio.Semaphore(max(1,self.limit))
        self.client = client or httpx2.AsyncClient(
            timeout=httpx2.Timeout(min(archive.QUERY_TIMEOUT,self.timeout),connect=5,pool=5),follow_redirects=False,
            limits=httpx2.Limits(max_connections=max(1,self.limit),max_keepalive_connections=max(1,self.limit)))
        self.flights = {}
        self.stats = {"started":0,"completed":0,"cancelled":0,"active":0,"deduplicated":0}

    async def close(self):
        tasks=[v["task"] for v in self.flights.values()]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await self.client.aclose()

    async def _fetch(self,args):
        ra,dec,radius,deep,all_results,polygon=args
        maxrec=None if all_results else int(os.getenv("SPHEREX_INITIAL_MAXREC","1000"))
        params=await run_in_threadpool(archive.query_parameters,ra,dec,radius,deep,maxrec,polygon)
        started=time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout):
                async with self.semaphore:
                    self.stats["active"]+=1; self.stats["started"]+=1
                    try:
                        for attempt in range(2):
                            request_started=time.perf_counter()
                            try:
                                async with self.client.stream("GET","https://irsa.ipac.caltech.edu/SIA",params=params) as response:
                                    if response.status_code in (429,502,503,504) and attempt==0:
                                        await response.aclose()
                                        await asyncio.sleep(.3)
                                        continue
                                    response.raise_for_status()
                                    data=bytearray()
                                    async for chunk in response.aiter_bytes():
                                        data.extend(chunk)
                                        if len(data)>archive.MAX_METADATA_BYTES:
                                            raise archive.DataError("IRSA metadata exceeds the transfer budget; completeness is unknown. Use a smaller region or a TAP job.",503)
                                result=await run_in_threadpool(partial(archive.discover,*args,raw_response=(bytes(data),time.perf_counter()-request_started)))
                                result["timings"]["discovery_transport_total_s"]=time.perf_counter()-started
                                result["transport"]={"cancellable":True,"total_timeout_s":self.timeout,"max_attempts":2,"concurrency":self.limit}
                                self.stats["completed"]+=1
                                return result
                            except (httpx2.TransportError,) as exc:
                                if attempt:raise archive.DataError(f"IRSA transport failed: {exc}") from exc
                                await asyncio.sleep(.3)
                    finally:self.stats["active"]-=1
        except asyncio.CancelledError:
            self.stats["cancelled"]+=1
            raise
        except TimeoutError as exc:
            raise archive.DataError("IRSA discovery exceeded its total time budget; completeness is unknown. Sky navigation remains available.",504) from exc
        except httpx2.HTTPStatusError as exc:
            raise archive.DataError(f"IRSA discovery returned HTTP {exc.response.status_code}.") from exc

    async def fetch(self,args):
        key=json.dumps(args,sort_keys=True,separators=(",",":"))
        flight=self.flights.get(key)
        if flight is None:
            flight={"task":asyncio.create_task(self._fetch(args)),"waiters":0};self.flights[key]=flight
        else:self.stats["deduplicated"]+=1
        flight["waiters"]+=1
        try:return copy.deepcopy(await asyncio.shield(flight["task"]))
        finally:
            flight["waiters"]-=1
            if not flight["waiters"]:
                if not flight["task"].done():
                    flight["task"].cancel()
                    await asyncio.gather(flight["task"],return_exceptions=True)
                if self.flights.get(key) is flight:self.flights.pop(key,None)

async def search(broker,req):
    args=(req.ra,req.dec,req.search_radius_deg,req.cutout_size_deg,req.include_deep,req.all_results,req.polygon,req.force_refresh)
    try:return await run_in_threadpool(partial(observations.search,*args,discoverer=cache_only))
    except CacheMiss:pass
    discovered=await broker.fetch((req.ra,req.dec,req.search_radius_deg,req.include_deep,req.all_results,req.polygon))
    return await run_in_threadpool(partial(observations.search,*args,discoverer=lambda *a:copy.deepcopy(discovered)))
