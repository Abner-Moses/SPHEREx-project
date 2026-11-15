import os,json,sys
from pathlib import Path
os.environ['SPHEREX_OFFLINE']='1'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main,demo_targets
import requests
from fastapi.testclient import TestClient
attempts=[]
def forbidden(*a,**k):
    attempts.append(str(a[1:3]));raise AssertionError('unexpected outbound request')
requests.Session.request=forbidden
results=[]
with TestClient(main.app) as c:
    for tid,t in demo_targets.TARGETS.items():
        astro=demo_targets._astrometry(tid)
        ra,dec=demo_targets._propagate({**t,**astro},demo_targets.EPOCH_YEAR)
        rows=c.get('/api/observations',params={'ra':ra,'dec':dec});assert rows.status_code==200,rows.text
        obs=c.post('/api/observe',json={'ra':ra,'dec':dec,'mode':'live'});assert obs.status_code==200,obs.text
        meta=obs.json();assert meta['source']=='irsa' and meta['images_are']=='SPHEREx level-2 detector image',meta.get('images_are')
        png=c.get(meta['image_url']);mask=c.get(meta['mask_url']);assert png.content[:8]==b'\x89PNG\r\n\x1a\n' and mask.status_code==200
        assert meta['target']['pixel'] is not None
        results.append({'id':tid,'observations':rows.json()['count'],'source':meta['source'],'images_are':meta['images_are'],'size':[meta['width'],meta['height']],'target_pixel':meta['target']['pixel'],'image_png_bytes':len(png.content),'mask_status':mask.status_code})
    missing=c.post('/api/observe',json={'ra':1,'dec':1,'mode':'auto'})
    assert missing.status_code==404,missing.text
assert not attempts,attempts
Path('audit-evidence/sky-integration.json').write_text(json.dumps({'pass':True,'offline':True,'outbound_attempts':attempts,'targets':results,'missing_image_status':missing.status_code},indent=2))
print(json.dumps(results,indent=2))
