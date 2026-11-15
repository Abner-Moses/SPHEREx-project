"""Read-only local IRSA products; small derived cutouts, never dataset duplication."""
from pathlib import Path
from functools import lru_cache
import os,json,hashlib,io
import numpy as np
from astropy.io import fits
from astropy.coordinates import SkyCoord
from astropy.nddata import Cutout2D
from astropy import units as u
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from catalog_api import Number
from archive_client import DataError
ROOT=Path(os.getenv('SPHEREX_DATA_ROOT',str(Path.home()/'SphereX-data'))).expanduser().resolve()
OFFLINE=os.getenv('SPHEREX_OFFLINE','0')=='1'
router=APIRouter(prefix='/api/local',tags=['local science'])
@lru_cache(maxsize=1)
def index():
    records=json.loads((ROOT/'cached_products.json').read_text()) if (ROOT/'cached_products.json').exists() else []
    byname={r['source_url'].rsplit('/',1)[-1]:r for r in records}
    manifests={}
    regions={}
    centers={}
    for manifest in ROOT.rglob('manifest.jsonl'):
        for line in manifest.read_text().splitlines():
            try:
                item=json.loads(line)
                if 'source_url' in item:manifests[(manifest.parent/item['file']).resolve()]=item
                if 'region' in item:
                    regions[item['file']]=item['region']
                    if 'ra' in item and 'dec' in item:centers[item['file']]={'ra':item['ra'],'dec':item['dec'],'frame':'ICRS','epoch':'not supplied; downloaded field center'}
            except (ValueError,KeyError):continue
    result=[]
    for kind in ('observations','cubes'):
        for path in sorted((ROOT/kind).rglob('*.fits')):
            with fits.open(path,memmap=True) as h:
                header=h['IMAGE'].header;obs=header.get('OBSID');base=path.name
                source=next((r for n,r in byname.items() if base.endswith(n)),None)
                if source is None and kind=='observations' and path.resolve() in manifests:
                    m=manifests[path.resolve()];url=m['source_url'];release='dr1' if '/dr1/' in url else 'qr3' if '/qr3/' in url else None
                    if release:
                        source={'id':hashlib.sha256(url.encode()).hexdigest()[:32],'source_url':url,'observation_id':obs,'collection':'spherex_'+release,'sia':{'obs_id':obs,'t_min':header.get('MJD-OBS'),'s_ra':header.get('CRVAL1'),'s_dec':header.get('CRVAL2'),'energy_bandpassname':f"SPHEREx-D{header.get('DETECTOR')}"},'metadata_origin':'Local FITS headers and download provenance manifest; not full SIA row'}
                key=source['id'] if source else hashlib.sha256(str(path.relative_to(ROOT)).encode()).hexdigest()[:32]
                result.append({'id':key,'kind':kind,'field_center':centers.get(path.name),'target':regions.get(path.name,path.parent.name),'path':path,'source_url':manifests.get(path.resolve(),{}).get('source_url',source.get('source_url') if source else None),'download_sha256':manifests.get(path.resolve(),{}).get('sha256'),'file':str(path.relative_to(ROOT)),'record':source,'observation_id':obs,'shape':list(h['IMAGE'].shape)})
    return result
@router.get('/products')
def products():
    cubes=[r for r in index() if r['kind']=='cubes']
    wavelengths=[]
    if cubes:
        with fits.open(cubes[0]['path'],memmap=True) as h:wavelengths=channels(h).tolist()
    return {'wavelengths_um':wavelengths,'offline':OFFLINE,'root':str(ROOT),'products':[{k:v for k,v in r.items() if k not in ('path','record')} for r in index()]}
def product(key):
    row=next((r for r in index() if r['id']==key),None)
    if row is None:raise DataError('Unknown indexed local product',404)
    return row

def discover(ra,dec,polygon=None):
    from sequence_service import _celestial
    rows=[]
    if polygon:
        from sky_geometry import polygon as region
        from spherical_geometry.polygon import SphericalPolygon
        view=region(polygon,(ra,dec))
    for r in index():
        if r['kind']!='observations' or not r['record']:continue
        with fits.open(r['path'],memmap=True) as h:
            w=_celestial(h['IMAGE'].header)
            try:
                x,y=w.all_world2pix(ra,dec,0,ra_dec_order=True)
                covered=np.isfinite([x,y]).all() and -.5<=x<h['IMAGE'].shape[1]-.5 and -.5<=y<h['IMAGE'].shape[0]-.5
                if covered:covered=SkyCoord(ra,dec,unit='deg').separation(w.pixel_to_world(x,y)).arcsec<=.01
            except Exception:covered=False
            if polygon:
                corners=_celestial(h['IMAGE'].header).calc_footprint(axes=(h['IMAGE'].shape[1],h['IMAGE'].shape[0]))
                covered=SphericalPolygon.from_radec(corners[:,0],corners[:,1]).intersects_poly(view)
            if covered:rows.append(r['record'])
    return {'observations':rows,'columns':[],'query':{'ra':ra,'dec':dec,'polygon':polygon,'source':'local indexed footprints'},'completeness':{'status':'complete','scope':'Indexed local Level-2 files only; not the entire archive','collections':sorted({r['collection'] for r in rows})},'timings':{},'cache_hit':True,'archive_source':{'source':'local authentic IRSA products','root':str(ROOT)}}

def retrieve(key,ra,dec,size):
    import observation_service as service
    from sequence_service import _celestial
    from fits_processor import validate,POLICY_VERSION
    r=product(key)
    if not r['record']:raise DataError('Local product lacks archive provenance',422)
    stat=r['path'].stat()
    asset=hashlib.sha256(f'local-cutout-v3:{POLICY_VERSION}:{key}:{stat.st_size}:{stat.st_mtime_ns}:{ra:.10f}:{dec:.10f}:{size:.8f}'.encode()).hexdigest()[:32]
    service.CACHE.mkdir(parents=True,exist_ok=True);out=service.CACHE/(asset+'.fits');meta=out.with_suffix('.json')
    with service.LOCK:
        if meta.exists() and out.exists():
            try:
                cached=json.loads(meta.read_text())
                if cached.get('id')==asset and cached.get('sha256'):
                    with service.asset_lease(asset):
                        service.LEASES[asset]=time.monotonic()+service.LEASE_SECONDS
                        return cached
            except (ValueError,OSError):pass
    with fits.open(r['path'],memmap=False) as h:
        w=_celestial(h['IMAGE'].header);cut=Cutout2D(h['IMAGE'].data,SkyCoord(ra,dec,unit='deg'),size*u.deg,wcs=w,mode='trim')
        ys,xs=cut.slices_original;x0,y0=xs.start,ys.start
        result=[fits.PrimaryHDU(header=h[0].header)]
        for original in h[1:]:
            if original.name in ('IMAGE','FLAGS','VARIANCE','ZODI'):
                data=original.data[ys,xs].copy();copy=fits.ImageHDU(data=data,header=original.header.copy(),name=original.name);copy.header.update(cut.wcs.to_header(relax=True));copy.header['CRPIX1A']=original.header.get('CRPIX1A',1)-x0;copy.header['CRPIX2A']=original.header.get('CRPIX2A',1)-y0
            else:copy=original.copy()
            if original.name=='WCS-WAVE':
                for name,offset in [('X',x0),('Y',y0)]:copy.data[name]-=offset
            result.append(copy)
        tmp=out.with_suffix('.tmp');fits.HDUList(result).writeto(tmp,overwrite=True,checksum=True);tmp.replace(out)
    v=validate(out,ra,dec,allow_rejected_pixels=True)
    record={**r['record'],'id':asset,'product_id':key,'coordinates':{'ra':ra,'dec':dec,'frame':'ICRS'},'cutout_size_deg':size,'validation':v,'detector':v['header'].get('DETECTOR'),
       'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'retrieved_at':None,'cache_hit':True,'local_source':str(r['path']),'retrieved_url':r['record']['source_url'],'processing':'Local WCS-preserving cutout derivative; full original unchanged'}
    with service.LOCK:
        service.LEASES[asset]=time.monotonic()+service.LEASE_SECONDS
        try:
            service.prune()
            temp_meta=meta.with_suffix('.json.tmp');temp_meta.write_text(json.dumps(record,allow_nan=False));temp_meta.replace(meta)
        except Exception:
            service.LEASES.pop(asset,None);out.unlink(missing_ok=True);meta.unlink(missing_ok=True);raise
    return record

def cube(key):
    r=product(key)
    if r['kind']!='cubes':raise DataError('Select a spectral cube',422)
    return r

def channels(h):
    table=h['SPECTRAL_CHANNELS'].data
    name=next(n for n in table.names if n.upper() in ('WAVELENGTH','WAVE','LAMBDA','LAMBDA_C','WAVE_CENTER'))
    return np.asarray(table[name],float)

def cube_wcs(h):
    from astropy.wcs import WCS
    header=h['IMAGE'].header.copy()
    for k in list(header):
        if k in ('CTYPE3','CUNIT3','CRVAL3','CDELT3','CRPIX3','NAXIS3') or k.startswith(('PS3_','PV3_')):del header[k]
    header['WCSAXES']=2;header['NAXIS']=2
    return WCS(header).celestial

def sky_pixel(h,ra,dec):
    if not 0<=ra<360 or not -90<=dec<=90:raise DataError('ICRS coordinates out of range',422)
    w=cube_wcs(h)
    try:x,y=w.all_world2pix(ra,dec,0,ra_dec_order=True)
    except Exception as exc:raise DataError('Position outside cube WCS coverage',422) from exc
    if np.isfinite([x,y]).all() and SkyCoord(ra,dec,unit='deg').separation(w.pixel_to_world(x,y)).arcsec>.01:raise DataError('Cube coordinate round trip failed',422)
    if not np.isfinite([x,y]).all():raise DataError('Position outside cube projection',422)
    x,y=int(round(float(x))),int(round(float(y)))
    if not 0<=x<512 or not 0<=y<512:raise DataError('Position outside this cube',422)
    return x,y
@router.get('/cubes/{key}/spectrum')
def spectrum(key:str,ra:Number,dec:Number,radius:int=0):
    if not 0<=radius<=20:raise HTTPException(422,'Aperture radius must be 0..20 pixels')
    r=cube(key)
    with fits.open(r['path'],memmap=True) as h:
        x,y=sky_pixel(h,ra,dec);data=h['IMAGE'].data[:,max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]
        hits=h['NHITS'].data[:,max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1]
        good=np.isfinite(data)&(hits>0);good[[50,51]]=False
        values=np.divide(np.where(good,data,0).sum((1,2)),good.sum((1,2)),out=np.full(102,np.nan),where=good.sum((1,2))>0);wave=channels(h)
        return {'product_id':key,'source_url':r['source_url'],'download_manifest_sha256':r['download_sha256'],'origin':'DR1 combined spectral cube; not a temporal epoch','unit':'MJy/sr','wavelength_um':wave.tolist(),'surface_brightness':[float(v) if np.isfinite(v) else None for v in values],
          'uncertainty':None,'raw_target_flags':h['FLAGS'].data[:,y,x].astype(int).tolist(),'cube_quality_acceptance':'Not established from Level-2 flags; cube flags retained separately.', 'limitation':'No variance supplied; NHITS is coverage, never an uncertainty estimate. Square aperture mean, ZODI already subtracted.',
          'visualization_descriptors':[{'kind':'scatter','title':'Local calibrated cube spectrum','x_label':'Wavelength','x_unit':'µm','y_label':'Surface brightness','y_unit':'MJy/sr','points':[{'x':float(w),'y':float(v)} for w,v in zip(wave,values) if np.isfinite(v)]}]}
@router.get('/cubes/{key}/slice')
def slice_image(key:str,channel:int=0):
    if not 0<=channel<102 or channel in (50,51):raise HTTPException(422,'Invalid or blank spectral channel')
    from PIL import Image
    with fits.open(cube(key)['path'],memmap=True) as h:
        a=np.asarray(h['IMAGE'].data[channel],float);good=np.isfinite(a)&(h['NHITS'].data[channel]>0);values=a[good]
        if not values.size:raise HTTPException(404,'No covered finite pixels')
        lo,hi=np.percentile(values,[1,99]);v=np.arcsinh(np.clip((np.nan_to_num(a)-lo)/max(hi-lo,1e-12),0,1)*10)/np.arcsinh(10)
        b=io.BytesIO();Image.fromarray(np.dstack([v*255]*3+[good*255]).astype('uint8')[::-1]).save(b,format='PNG')
        return Response(b.getvalue(),media_type='image/png',headers={'X-Data-Product':'display-derivative-only'})
@router.get('/cubes/{key}/feature')
def feature(key:str,feature_min:Number,feature_max:Number,continuum_min:Number,continuum_max:Number):
    if not .7<=continuum_min<feature_min<feature_max<continuum_max<=5.1:raise HTTPException(422,'Continuum bounds must bracket ordered feature bounds')
    with fits.open(cube(key)['path'],memmap=True) as h:
        wave=channels(h);select=(wave>=continuum_min)&(wave<=continuum_max);fi=(wave>=feature_min)&(wave<=feature_max);co=select&~fi
        select[[50,51]]=False;fi[[50,51]]=False;co[[50,51]]=False
        if fi.sum()<2 or co.sum()<3:raise HTTPException(422,'Insufficient measured feature or continuum channels')
        # Display-grid native stride; never interpolate nonexistent coverage.
        a=np.asarray(h['IMAGE'].data[select,::4,::4],float);hits=h['NHITS'].data[select,::4,::4]
        good=np.isfinite(a)&(hits>0);valid=good.all(0);x=wave[select];f=(x>=feature_min)&(x<=feature_max);c=~f
        X=np.column_stack([np.ones(c.sum()),x[c]]);beta=np.linalg.pinv(X)@np.where(good,a,0)[c].reshape(c.sum(),-1)
        continuum=(np.column_stack([np.ones(len(x)),x])@beta).reshape(a.shape)
        integral=np.trapezoid((a-continuum)[f],x[f],axis=0);residual=np.sqrt(np.mean((a[c]-continuum[c])**2,axis=0))
        result=np.where(valid,integral,np.nan)
        positive=valid&np.all((a[f]>0)&(continuum[f]>0),axis=0)
        ratio=np.divide(a[f],continuum[f],out=np.full_like(a[f],np.nan),where=continuum[f]>0)
        optical_depth=np.where(positive,np.trapezoid(-np.log(np.where(ratio>0,ratio,np.nan)),x[f],axis=0),np.nan)
        equivalent_width=np.where(positive,np.trapezoid(1-ratio,x[f],axis=0),np.nan)
        maps=[(result,'Continuum-subtracted feature strength','MJy/sr µm'),(np.where(valid,residual,np.nan),'Continuum residual RMS','MJy/sr'),(optical_depth,'Apparent optical-depth integral (positive intensities only)','µm'),(equivalent_width,'Equivalent width (positive intensities only)','µm')]
        return {'product_id':key,'source_url':cube(key)['source_url'],'download_manifest_sha256':cube(key)['download_sha256'],'status':'exploratory','feature_interval_um':[feature_min,feature_max],'continuum_bounds_um':[continuum_min,continuum_max],'method':'Unweighted linear continuum; native stride=4; signed trapezoidal feature excess, no molecular identification',
          'unit':'MJy/sr µm','uncertainty':None,'limitations':['No cube variance or instrument-response covariance; no significance or column density claimed.','Feature coincidence is not molecular identification.','Apparent optical depth assumes the fitted continuum is appropriate; emission, mixing, foregrounds and blended features can invalidate absorption interpretation.','Integrals use measured channel centers, not requested interval endpoints or deconvolved instrument response.'],
          'sampled_feature_interval_um':[float(x[f][0]),float(x[f][-1])],
          'visualization_descriptors':[{'kind':'heatmap','title':title,'values':[[float(v) if np.isfinite(v) else None for v in row] for row in array],'unit':unit} for array,title,unit in maps]}

def local_bandpass(path,record,ra,dec):
    import re
    import sequence_spectral as spectral
    from sequence_service import _celestial
    with fits.open(path,memmap=False) as h:
        header=h['IMAGE'].header;x,y=_celestial(header).world_to_pixel(SkyCoord(ra,dec,unit='deg'))
        detector=int(header.get('DETECTOR'));history=str(h['WCS-WAVE'].header.get('HISTORY',''))
        version=re.search(r'spectral_wcs_'+str(detector)+r'_v([\d.]+)\.fits',history)
        if not version:raise ValueError('No authoritative embedded calibration version')
        px,py=spectral.original_pixel(header,float(x),float(y))
    release=record['collection'].removeprefix('spherex_').removesuffix('_deep')
    matches=[]
    for candidate in (ROOT/'calibrations'/release/'spectral_wcs').rglob(f'spectral_wcs_D{detector}_*.fits'):
        with fits.open(candidate,memmap=True) as h:
            if re.search(r'Version\s+'+re.escape(version[1])+r'(?:\s|$)',str(h[0].header.get('HISTORY',''))):matches.append(candidate)
    if len(matches)!=1:raise ValueError('No unique release/detector/version-matched local spectral calibration')
    with fits.open(matches[0],memmap=True) as h:result=spectral._pair(h,px,py)
    result.update(source='local original-version matched CWAVE/CBAND',source_url=str(matches[0]),calibration_version=version[1],parent_pixel=[px,py],association='Embedded WCS-WAVE calibration version, release and detector; not inferred from detector alone')
    return result

@router.get('/cubes/{key}/rgb')
def rgb(key:str,red:int=90,green:int=65,blue:int=20):
    from PIL import Image
    selected=[red,green,blue]
    if any(c<0 or c>=102 or c in (50,51) for c in selected):raise HTTPException(422,'Select three nonblank channels')
    with fits.open(cube(key)['path'],memmap=True) as h:
        a=np.stack([h['IMAGE'].data[c] for c in selected]).astype(float);good=np.isfinite(a)&np.stack([h['NHITS'].data[c]>0 for c in selected]);values=a[good]
        if not len(values):raise HTTPException(404,'No covered pixels')
        lo,hi=np.percentile(values,[1,99]);v=np.arcsinh(np.clip((np.nan_to_num(a)-lo)/max(hi-lo,1e-12),0,1)*10)/np.arcsinh(10)
        pixels=np.dstack([np.moveaxis(v,0,-1)*255,good.all(0)*255]).astype('uint8');out=io.BytesIO();Image.fromarray(pixels[::-1]).save(out,format='PNG')
        return Response(out.getvalue(),media_type='image/png',headers={'X-Data-Product':'false-colour-display-derivative','X-Channels':','.join(map(str,selected))})

@router.get('/cubes/{key}/volume')
def volume(key:str):
    """Bounded 32x32x34 sample, not a complete JSON cube or physical distance."""
    from astropy.wcs import WCS
    from sequence_service import _celestial
    with fits.open(cube(key)['path'],memmap=True) as h:
        header=h['IMAGE'].header.copy()
        for k in list(header):
            if k in ('CTYPE3','CUNIT3','CRVAL3','CDELT3','CRPIX3','NAXIS3') or k.startswith(('PS3_','PV3_')):del header[k]
        header['WCSAXES']=2;header['NAXIS']=2;w=WCS(header).celestial
        yy,xx=np.mgrid[0:512:16,0:512:16];world=w.pixel_to_world(xx,yy).icrs;center=w.pixel_to_world(256,256).icrs;offset=world.transform_to(center.skyoffset_frame())
        rows=[];wave=channels(h)
        for c in range(0,102,3):
            if c in (50,51):continue
            image=np.asarray(h['IMAGE'].data[c,::16,::16]);good=np.isfinite(image)&(h['NHITS'].data[c,::16,::16]>0)
            rows.append(np.column_stack([offset.lon.deg[good],offset.lat.deg[good],np.full(good.sum(),wave[c]),image[good]]))
        output=np.concatenate(rows).astype('<f4') if rows else np.empty((0,4),dtype='<f4')
        return Response(output.tobytes(),media_type='application/octet-stream',headers={'X-Layout':'float32-le: sky-offset-longitude-deg,sky-offset-latitude-deg,wavelength-um,MJy-per-sr','X-Point-Count':str(len(output)),'X-Data-Product':'subsampled-spatial-spectral-volume-not-physical-distance'})

@router.get('/cubes/{key}/position')
def cube_position(key:str,x:int,y:int):
    with fits.open(cube(key)['path'],memmap=True) as h:
        if not 0<=x<h['IMAGE'].shape[2] or not 0<=y<h['IMAGE'].shape[1]:raise HTTPException(422,'Pixel outside cube')
        p=cube_wcs(h).pixel_to_world(x,y).icrs
        return {'ra':float(p.ra.deg),'dec':float(p.dec.deg),'frame':'ICRS','pixel':[x,y]}

# Shared, bounded failure cooldown; supported local products bypass this entirely.
import threading,time
_REMOTE_LOCK=threading.Lock()
_REMOTE_FAILURES={}
def remote_allowed(scope):
    with _REMOTE_LOCK:return not OFFLINE and time.monotonic()>=_REMOTE_FAILURES.get(scope,0)
def remote_failed(scope):
    with _REMOTE_LOCK:_REMOTE_FAILURES[scope]=time.monotonic()+120

def matching_product(record):
    """Same immutable filename/release only: never replace an exposure with a neighbor."""
    from urllib.parse import urlsplit,unquote
    name=unquote(urlsplit(record.get('source_url') or '').path).rsplit('/',1)[-1]
    matches=[r for r in index() if r['kind']=='observations' and r['record'] and
             (r['id']==record.get('id') or name and r['record'].get('collection')==record.get('collection') and
              unquote(urlsplit(r['source_url'] or '').path).rsplit('/',1)[-1]==name)]
    return matches[0] if len(matches)==1 else None

def metadata(record,reason=None):
    match=matching_product(record)
    reason=reason or 'Local-first operation; remote enrichment not requested.'
    missing={'status':'not_available','reason':reason,'columns':[]}
    return {'tap':{**missing,'row':None},'datalink':{**missing,'rows':[],'resources':[],'service_descriptors':[]},
      'errors':[],'local':{'status':'available' if match else 'metadata_only','archive_metadata':record,
      'product_id':match['id'] if match else None,'original_source_url':match['source_url'] if match else record.get('source_url'),
      'download_manifest_sha256':match['download_sha256'] if match else None,
      'calibration_policy':'Embedded EPSF and version/release/detector-matched local CWAVE/CBAND are resolved when scientific measurements are requested.',
      'limitation':'Absent TAP/DataLink columns and associated remote links are unavailable, not invented.'}}

_TARGET_NAMES={'barnardsstar':"Barnard's Star",'barnardstar':"Barnard's Star",'epsilonindib':'Epsilon Indi B','wise0855':'WISE 0855','rwaurigae':'RW Aurigae','rwaur':'RW Aurigae','rdraconis':'R Draconis','rdra':'R Draconis'}
def target_field(name):
    import re
    normalized=re.sub('[^a-z0-9]','',name.lower());canonical=_TARGET_NAMES.get(normalized)
    if not canonical:return None
    for r in index():
        target=re.sub('[^a-z0-9]','',r['target'].lower())
        if r['kind']=='cubes' and r['field_center'] and _TARGET_NAMES.get(target)==canonical:
            return {'name':canonical,**r['field_center'],'epoch':None,'position_kind':'downloaded_field_center','cache_hit':True,
              'propagated_position':{'status':'unavailable','reason':'Download manifest supplies a field center, not an epoch-qualified object astrometric solution.'},
              'provenance':{'source':'local acquisition manifest','source_url':r['source_url'],'product_id':r['id']},
              'warning':'Local downloaded field center; not a SIMBAD resolution, propagated position or observed source detection.'}
    return None

def cached_discovery(req):
    """Exact cached query, including expired results explicitly labelled stale."""
    import observation_service as service
    key=json.dumps(['metadata-v3',req.ra,req.dec,req.search_radius_deg,req.include_deep,req.all_results,req.polygon],separators=(',',':'))
    path=service.CACHE/'queries'/(hashlib.sha256(key.encode()).hexdigest()[:32]+'.json')
    if not path.exists():return None
    try:
        result=json.loads(path.read_text())
        if not isinstance(result.get('observations'),list):return None
        age=max(0,time.time()-path.stat().st_mtime)
        result.update(cache_hit=True,cache_stale=age>=service.TTL,metadata_freshness={'source':'persistent exact-query cache','age_seconds':age,'fresh_retrieval':False})
        service.register_catalog(result['observations']);return result
    except (OSError,ValueError,KeyError):return None

@router.get('/sky')
def local_sky(ra:Number,dec:Number):
    for r in index():
        if r['kind']!='cubes':continue
        try:
            with fits.open(r['path'],memmap=True) as h:sky_pixel(h,ra,dec)
            return {'available':True,'cube_id':r['id'],'source_url':r['source_url'],'origin':'Local DR1 combined spectral cube; zodiacal light subtracted; not a temporal epoch'}
        except (DataError,ValueError):continue
    return {'available':False,'reason':'No indexed local cube covers this sky center; no substitute imagery.'}

@router.get('/cubes/{key}/channel.fits')
def channel_fits(key:str,channel:int=0):
    if not 0<=channel<102 or channel in (50,51):raise HTTPException(422,'Invalid or blank channel')
    r=cube(key)
    with fits.open(r['path'],memmap=True) as h:
        data=np.array(h['IMAGE'].data[channel],copy=True)
        if not np.any(np.isfinite(data)&(h['NHITS'].data[channel]>0)):raise HTTPException(404,'No valid channel coverage')
        data[h['NHITS'].data[channel]<=0]=np.nan
        header=cube_wcs(h).to_header(relax=True);header['BUNIT']=h['IMAGE'].header.get('BUNIT','MJy/sr');header['SPXDERIV']=True
        header['WAVEUM']=float(channels(h)[channel]);header['CUBESRC']=r['source_url'] or r['file']
        header.add_history('Local single-channel derivative; unchanged covered intensities, original celestial WCS. Not a temporal epoch.')
        b=io.BytesIO();fits.PrimaryHDU(data,header).writeto(b,checksum=True)
    return Response(b.getvalue(),media_type='application/fits',headers={'X-Data-Product':'local-single-spectral-channel-derivative'})

_RENDER_SLOTS=threading.BoundedSemaphore(1)
@router.get('/cubes/{key}/render-data')
def render_data(key:str,stride:int=16,spectral_step:int=1,x0:int=0,x1:int=512,y0:int=0,y1:int=512,exclude_flags:int=0,selection_json:str|None=None):
    """Aligned columnar arrays; offsets relative to an 8-byte-aligned payload."""
    if stride not in (1,2,4,8,16,32) or not 1<=spectral_step<=8 or not 0<=x0<x1<=512 or not 0<=y0<y1<=512 or not 0<=exclude_flags<=0xffffffff:raise HTTPException(422,'Invalid bounded cube subset')
    if not _RENDER_SLOTS.acquire(False):raise HTTPException(503,'Cube rendering preparation occupied; retry shortly')
    try:
        from scientific_measurements import _areas
        r=cube(key);columns=[];chunks=[];offset=0
        def column(name,array,dtype,unit,category='observed'):
            nonlocal offset
            a=np.ascontiguousarray(array,dtype=np.dtype(dtype).newbyteorder('<')).reshape(-1)
            pad=(-offset)%8;chunks.append(b'\0'*pad);offset+=pad
            columns.append({'name':name,'dtype':dtype,'offset':offset,'length':a.size,'unit':unit,'category':category})
            b=a.tobytes();chunks.append(b);offset+=len(b)
        with fits.open(r['path'],memmap=True) as h:
            w=cube_wcs(h);selection=None;requested_stride=stride
            if selection_json:
                if len(selection_json)>16000:raise HTTPException(422,'Selection too large')
                from lens_registry import Selection
                try:selection=Selection.model_validate_json(selection_json)
                except ValueError as exc:raise HTTPException(422,'Invalid celestial selection') from exc
                gy,gx=np.mgrid[y0:y1,x0:x1];world=w.pixel_to_world(gx,gy).icrs;inside=selection.contains(world.ra.deg,world.dec.deg);py,px=np.nonzero(inside)
                if not len(px):raise HTTPException(404,'No native cube pixels inside this celestial lens region')
                x0,x1=x0+int(px.min()),x0+int(px.max())+1;y0,y1=y0+int(py.min()),y0+int(py.max())+1
            stride=max(stride,int(np.ceil((x1-x0)/64)),int(np.ceil((y1-y0)/64)))
            xs=np.arange(x0,x1,stride);ys=np.arange(y0,y1,stride);cs=np.arange(0,h['IMAGE'].shape[0],spectral_step)
            xx,yy=np.meshgrid(xs,ys);sky=w.pixel_to_world(xx,yy).icrs;center=w.pixel_to_world(256,256).icrs;relative=sky.transform_to(center.skyoffset_frame());shape=(len(cs),len(ys),len(xs))
            image=np.asarray(h['IMAGE'].data[np.ix_(cs,ys,xs)],dtype='<f4');hits=np.asarray(h['NHITS'].data[np.ix_(cs,ys,xs)]);flags=np.asarray(h['FLAGS'].data[np.ix_(cs,ys,xs)],dtype='<u4')
            valid=np.isfinite(image)&(hits>0)&((flags&exclude_flags)==0);valid[np.isin(cs,[50,51])]=False
            if selection:valid&=selection.contains(sky.ra.deg,sky.dec.deg)[None,:,:]
            wave=channels(h);table=h['SPECTRAL_CHANNELS'].data
            def tile(a):return np.broadcast_to(a,shape)
            column('east',tile(relative.lon.deg),'f4','deg','calculated');column('north',tile(relative.lat.deg),'f4','deg','calculated')
            column('wavelength',tile(wave[cs,None,None]),'f4','µm');column('intensity',image,'f4','MJy/sr');column('coverage',hits,'f4','contributing input pixels')
            column('flags',flags,'u4','bitmask');column('ra',tile(sky.ra.deg),'f8','deg ICRS');column('dec',tile(sky.dec.deg),'f8','deg ICRS')
            area=_areas(w,xs,ys);column('flux',image*tile(area)*1e6,'f4','Jy/native pixel','calculated')
            column('pixel_x',tile(xx),'u2','native pixel');column('pixel_y',tile(yy),'u2','native pixel');column('channel',tile(cs[:,None,None]),'u2','zero-based channel');column('valid',valid,'u1','mask')
            metadata={'version':1,'shape':shape,'columns':columns,'channels':cs.tolist(),'wavelengths_um':wave[cs].tolist(),'wave_min_um':np.asarray(table['WL_MIN'][cs],float).tolist(),'wave_max_um':np.asarray(table['WL_MAX'][cs],float).tolist(),
                'pixel_stride':stride,'requested_stride':requested_stride,'spectral_step':spectral_step,'crop':[x0,x1,y0,y1],'center_icrs':[center.ra.deg,center.dec.deg],'selection':selection.model_dump() if selection else None,
                'product_id':key,'source_url':r['source_url'],'download_manifest_sha256':r['download_sha256'],'original_data_modified':False,
                'limitations':['Combined spectral cube, not independent observation epochs or physical 3D depth.','Coverage/finite/blank-channel masking plus explicit excluded flag bits; cube FLAGS are propagated and are not Level-2 science acceptance.','No measurement variance supplied; NHITS is not uncertainty.','Flux is surface brightness times native WCS pixel solid angle, not PSF-corrected point-source flux.','Spatial and spectral downsampling are display samples, not an average or resolution enhancement.']}
        header=json.dumps(metadata,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode();prefix=len(header).to_bytes(4,'little')+header
        return Response(prefix+b'\0'*((-len(prefix))%8)+b''.join(chunks),media_type='application/octet-stream',headers={'X-Data-Product':'columnar-local-cube-scientific-display-samples'})
    finally:_RENDER_SLOTS.release()
