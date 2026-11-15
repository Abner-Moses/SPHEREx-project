import requests,time,json,hashlib
from pathlib import Path
base='http://127.0.0.1:8000';results=[]
for tid in ['barnards-star','eps-indi-b','wise-0855','rw-aurigae','r-draconis']:
 b=requests.get(f'{base}/api/demo/targets/{tid}/analysis').json()['bundle'];p=b['measure_at']
 d=requests.get(base+'/api/dataset/observations',params={'ra':p['ra'],'dec':p['dec']},timeout=20).json()
 ids=[r['id'] for r in d.get('observations',[])];assert ids,(tid,d)
 r=requests.post(base+'/api/sci/api/sequences/prepare',json={'ra':p['ra'],'dec':p['dec'],'observation_ids':ids,'max_frames':6,'mode':'all','cutout_size_deg':.05},timeout=10);r.raise_for_status();sid=r.json()['id']
 for _ in range(90):
  state=requests.get(base+'/api/sci/api/sequences/'+sid).json()
  if state['status']!='preparing':break
  time.sleep(1)
 hashes=[]
 for f in state.get('frames',[]):
  png=requests.get(f'{base}/api/sci/api/sequences/{sid}/frames/{f["id"]}?kind=preview');assert png.status_code==200,(tid,png.text)
  assert png.content.startswith(b'\x89PNG');hashes.append(hashlib.sha256(png.content).hexdigest())
 result={'target':tid,'sequence_id':sid,'status':state['status'],'frames':len(hashes),'distinct_previews':len(set(hashes)),'warnings':state['warnings'],'error':state.get('error')}
 results.append(result);print(json.dumps(result),flush=True)
Path('audit-evidence/blink-api.json').write_text(json.dumps(results,indent=2))
assert all(r['frames']>=2 and r['distinct_previews']>=2 for r in results)
