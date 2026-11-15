"""Native-grid Gaussian centroids/profiles; no catalogue motion passed off as data."""
import numpy as np
from astropy.modeling import models,fitting
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy import units as u
from fastapi import APIRouter,HTTPException
import scientific_measurements as science
import sequence_service as seq
from research_analysis import Extract
from catalog_api import Number
router=APIRouter(prefix='/api/research',tags=['native measurements'])

@router.post('/centroids')
def centroids(req:Extract):
    records=[];rejected=[]
    for key in dict.fromkeys(req.asset_ids):
        try:
            with science.source(key) as (_,record,h,image,var,bad):
                if not record['validation']['scientific_acceptance']['accepted']:raise ValueError('Scientific inspection-only quality')
                from sequence_service import _celestial
                w=_celestial(h['IMAGE'].header);x,y=w.world_to_pixel(SkyCoord(req.ra,req.dec,unit='deg'));x,y=float(x),float(y)
                x0,y0=int(round(x))-5,int(round(y))-5
                if x0<0 or y0<0 or x0+11>image.shape[1] or y0+11>image.shape[0]:raise ValueError('Centroid window outside coverage')
                a=image[y0:y0+11,x0:x0+11];v=var[y0:y0+11,x0:x0+11];good=~bad[y0:y0+11,x0:x0+11]&(v>0)
                if good.sum()<80:raise ValueError('Centroid window has insufficient usable pixels')
                yy,xx=np.indices(a.shape);background=float(np.median(a[good]));peak=np.unravel_index(np.argmax(np.where(good,a,-np.inf)),a.shape)
                model=models.Gaussian2D(max(float(a[peak]-background),1e-9),peak[1],peak[0],1.2,1.2,bounds={'amplitude':(0,None),'x_mean':(0,10),'y_mean':(0,10),'x_stddev':(.4,4),'y_stddev':(.4,4)})+models.Const2D(background)
                fitter=fitting.TRFLSQFitter(calc_uncertainties=True);fit=fitter(model,xx[good],yy[good],a[good],weights=1/np.sqrt(v[good]))
                px,py=float(fit.x_mean_0.value+x0),float(fit.y_mean_0.value+y0);world=w.pixel_to_world(px,py).icrs
                covariance=fitter.fit_info.get('param_cov');indices=[fit.param_names.index('x_mean_0'),fit.param_names.index('y_mean_0')]
                if covariance is None:raise ValueError('Formal centroid covariance unavailable')
                amplitude_index=fit.param_names.index('amplitude_0')
                if covariance[amplitude_index,amplitude_index]<=0 or fit.amplitude_0.value/np.sqrt(covariance[amplitude_index,amplitude_index])<5:raise ValueError('Gaussian amplitude significance below 5 formal sigma')
                cov=np.asarray(covariance)[np.ix_(indices,indices)]
                if not np.isfinite(cov).all() or np.linalg.eigvalsh(cov).min()<=0:raise ValueError('Centroid covariance is singular')
                columns=[]
                for dx,dy in [(1e-3,0),(0,1e-3)]:
                    off=world.spherical_offsets_to(w.pixel_to_world(px+dx,py+dy).icrs);columns.append([off[0].arcsec/1e-3,off[1].arcsec/1e-3])
                J=np.array(columns).T;sky_cov=J@cov@J.T;moment=seq.observation_time(h)
                records.append({'asset_id':key,'observation_id':record.get('observation_id'),**moment,'ra':world.ra.deg,'dec':world.dec.deg,'x':px,'y':py,'covariance_arcsec2':sky_cov.tolist(),'fwhm_px':[float(fit.x_stddev_0.value*2.35482),float(fit.y_stddev_0.value*2.35482)],'residual_chi_square':float(np.sum(((a-fit(xx,yy))[good])**2/v[good])),'source_url':record.get('source_url')})
        except Exception as e:rejected.append({'asset_id':key,'reason':str(e)})
    result={'status':'calculated' if records else 'insufficient','measurements':records,'rejected':rejected,'method':'Bounded elliptical Gaussian plus constant background, variance-weighted fit; formal covariance',
      'limitations':['No instrument EPSF matching or deblending; crowded/multiple peaks can invalidate a single-Gaussian centroid.','Formal covariance excludes absolute astrometric/calibration systematics.','Measurements are independently centered near the selected position; association is not established merely by window overlap.'], 'motion_fit':{'status':'unavailable','reason':'Requires unique source association and at least three comparable epochs.'}}
    result['visualization_descriptors']=[{'kind':'table','title':'Independently fitted native centroids; association not established','rows':records,'columns':['observation_id','timestamp','ra','dec','x','y','fwhm_px','covariance_arcsec2']}]
    return result

@router.get('/assets/{key}/radial-profile')
def radial_profile(key:str,ra:Number,dec:Number,radius:int=15):
    if not 1<=radius<=50:raise HTTPException(422,'Radius must be 1..50 native pixels')
    with science.source(key) as (_,record,h,a,var,bad):
        from sequence_service import _celestial
        x,y=_celestial(h['IMAGE'].header).world_to_pixel(SkyCoord(ra,dec,unit='deg'));yy,xx=np.indices(a.shape);r=np.hypot(xx-x,yy-y);points=[]
        for i in range(radius):
            valid=(r>=i)&(r<i+1)&~bad;n=int(valid.sum())
            if n:points.append({'x':i+.5,'y':float(a[valid].mean()),'error':float(np.sqrt(var[valid].sum())/n),'pixels':n})
        return {'visualization_descriptors':[{'kind':'scatter','title':'Native radial intensity profile','x_label':'Radius','x_unit':'pixel','y_label':'Surface brightness','y_unit':'MJy/sr','points':points}],'asset_id':key,'source_url':record.get('source_url'),'limitation':'Independent-pixel uncertainty of annular mean; not PSF-corrected source flux.'}

@router.get('/assets/{key}/epsf')
def epsf(key:str,ra:Number,dec:Number):
    if not 0<=ra<360 or not -90<=dec<=90:raise HTTPException(422,'ICRS coordinates out of range')
    import observation_service as assets
    from sequence_service import _celestial
    from sequence_spectral import original_pixel
    with assets.asset_lease(key) as path,fits.open(path,memmap=False) as h:
        table=h['EPSF'].data;header=h['EPSF'].header
        if not {'XCENTER','YCENTER','EPSF','XWIDTH','YWIDTH'}<=set(table.names):raise HTTPException(422,'EPSF zone geometry unavailable')
        x,y=_celestial(h['IMAGE'].header).world_to_pixel(SkyCoord(ra,dec,unit='deg'));px,py=original_pixel(h['IMAGE'].header,float(x),float(y))
        zone=(np.abs(table['XCENTER']-px)<=table['XWIDTH']/2)&(np.abs(table['YCENTER']-py)<=table['YWIDTH']/2)
        ids=np.flatnonzero(zone)
        if len(ids)!=1:raise HTTPException(422,'No unique EPSF zone contains the target')
        i=int(ids[0]);a=np.asarray(table['EPSF'][i],float)
        if not np.isfinite(a).all() or not np.any(a>0):raise HTTPException(422,'Selected EPSF response is not finite/usable')
        sx=int(header.get('OVSMPX',0));sy=int(header.get('OVSMPY',0))
        if sx<1 or sy<1:raise HTTPException(422,'EPSF sampling is undocumented')
        yy,xx=np.indices(a.shape);radius=np.sqrt(((xx-(a.shape[1]-1)/2)/sx)**2+((yy-(a.shape[0]-1)/2)/sy)**2);points=[]
        for lo in np.arange(0,float(radius.max()),.25):
            v=(radius>=lo)&(radius<lo+.25)&np.isfinite(a)
            if v.any():points.append({'x':float(lo+.125),'y':float(a[v].mean())})
        return {'asset_id':key,'zone':i,'parent_pixel':[px,py],'oversampling':[sx,sy],'native_sum':float(a.sum()),'calibration_history':str(header.get('HISTORY','')),
          'visualization_descriptors':[{'kind':'heatmap','title':'Actual target-zone effective PSF','values':a.tolist(),'unit':'stored EPSF response'},
            {'kind':'line','title':'Effective PSF radial profile','x_label':'Radius','x_unit':'native pixel','y_label':'Mean stored EPSF response','points':points}],
          'limitations':['Original oversampled EPSF response, not a physical depth or total-source aperture correction.','No deconvolution, PSF matching or error covariance inferred.']}

@router.get('/assets/{key}/diagnostics')
def diagnostics(key:str):
    with science.source(key) as (_,record,h,a,var,bad):
        valid=~bad&(var>0);x=a[valid];v=var[valid]
        if not x.size:raise HTTPException(422,'No usable finite positive-variance pixels')
        background=float(np.median(x));z=(x-background)/np.sqrt(v);descriptors=[]
        for values,title,unit in [(x,'Surface-brightness distribution','MJy/sr'),(v,'Reported variance distribution','(MJy/sr)^2'),(z,'Background-centered standardized intensity','1')]:
            counts,edges=np.histogram(values,bins=64);descriptors.append({'kind':'histogram','title':title,'unit':unit,'data':{'counts':counts.tolist(),'edges':edges.tolist()}})
        w=1/v;mean=float(np.sum(w*x)/w.sum());sigma=float(1/np.sqrt(w.sum()))
        quantiles=np.percentile(x,[0,25,50,75,100]).tolist()
        summary={'usable_pixels':len(x),'excluded_pixels':int(a.size-len(x)),'weighted_mean_mjy_sr':mean,'formal_independent_pixel_sigma_mjy_sr':sigma,'brightness_quantiles_mjy_sr':quantiles,'standardized_excursions_gt5':int(np.sum(np.abs(z)>5))}
        descriptors.append({'kind':'table','title':'Native measurement statistics','rows':[{'quantity':k,'value':str(v)} for k,v in summary.items()]})
        return {'status':'calculated','asset_id':key,'summary':summary,'visualization_descriptors':descriptors,'source_url':record.get('source_url'),'limitations':['Native cutout sample, not a population catalogue.','Standardized excursions are not source detections, artifact identifications or a multiple-trial-corrected significance.','Independent-pixel formal error omits calibration and covariance systematics.']}

def source_candidates(key,selection):
    from scipy.ndimage import maximum_filter
    from sequence_service import _celestial
    with science.source(key) as (_,record,h,image,var,bad):
        if image.size>1024*1024:raise HTTPException(422,'Use a local cutout for source discovery')
        w=_celestial(h['IMAGE'].header);yy,xx=np.indices(image.shape);world=w.pixel_to_world(xx,yy).icrs
        inside=selection.contains(world.ra.deg,world.dec.deg);good=inside&~bad&(var>0)
        if not good.any():return {'candidates':[],'background_mjy_sr':None,'limitations':['No usable selected pixels']}
        background=float(np.median(image[good]));z=np.where(good,(image-background)/np.sqrt(np.where(var>0,var,1)),-np.inf)
        peaks=good&(z>=5)&(z==maximum_filter(z,size=5,mode='constant',cval=-np.inf));pixels=np.column_stack(np.nonzero(peaks));pixels=sorted(pixels,key=lambda p:-z[tuple(p)])
        candidates=[]
        for i,(y,x) in enumerate(pixels[:200]):
            p=world[y,x];candidates.append({'id':f'{key}-peak-{int(x)}-{int(y)}','name':f'Exploratory peak {i+1}','ra':float(p.ra.deg),'dec':float(p.dec.deg),'x':int(x),'y':int(y),'nominal_pixel_snr':float(z[y,x]),'surface_brightness_mjy_sr':float(image[y,x]),'sigma_mjy_sr':float(np.sqrt(var[y,x])),'kind':'variance-aware exploratory image peak, not confirmed source','asset_id':key})
        return {'candidates':candidates,'total_local_maxima':len(pixels),'truncated':len(pixels)>200,'background_mjy_sr':background,
          'limitations':['Median selected-pixel background; spatially varying nebulosity can produce candidates.','Nominal pixel SNR is not a calibrated source detection probability or multiple-trial-corrected significance.','No deblending, cross-epoch association or motion identification is claimed.'], 'observation_quality':record.get('validation',{}).get('scientific_acceptance'), 'source_url':record.get('source_url')}

def motion_fit(measurements):
    """Conditional common-source GLS fit; source identity is not inferred here."""
    from astropy.time import Time
    if len(measurements)<3:return {'status':'unavailable','reason':'At least three accepted centroid epochs are required.'}
    times=np.array([Time(r['timestamp']).mjd for r in measurements]);t0=float(times.mean());years=(times-t0)/365.25
    if len(np.unique(times))<3:return {'status':'unavailable','reason':'At least three distinct observation epochs are required.'}
    sky=SkyCoord([r['ra'] for r in measurements],[r['dec'] for r in measurements],unit='deg');origin=sky[0];offsets=sky.transform_to(origin.skyoffset_frame());positions=np.column_stack([offsets.lon.arcsec,offsets.lat.arcsec])
    normal=np.zeros((4,4));rhs=np.zeros(4);systems=[]
    for dt,position,row in zip(years,positions,measurements):
        cov=np.asarray(row['covariance_arcsec2'],float)
        if cov.shape!=(2,2) or not np.isfinite(cov).all() or np.min(np.linalg.eigvalsh(cov))<=0:return {'status':'unavailable','reason':'A finite positive-definite centroid covariance is required for every epoch.'}
        A=np.array([[1,0,dt,0],[0,1,0,dt]]);W=np.linalg.inv(cov);normal+=A.T@W@A;rhs+=A.T@W@position;systems.append((A,W))
    if np.linalg.cond(normal)>1e12:return {'status':'unavailable','reason':'Insufficient temporal leverage for a stable motion fit.'}
    covariance=np.linalg.inv(normal);beta=covariance@rhs;pred=np.array([A@beta for A,W in systems]);residuals=positions-pred;chi2=float(sum(r@W@r for r,(A,W) in zip(residuals,systems)))
    descriptors=[]
    for axis,label in enumerate(['Local east','Local north']):
        descriptors.extend([{'kind':'scatter','title':f'{label} measured displacement','x_label':'Observation MJD (UTC)','x_unit':'day','y_label':label,'y_unit':'arcsec','points':[{'x':float(t),'y':float(p[axis]),'error':float(np.sqrt(row['covariance_arcsec2'][axis][axis]))} for t,p,row in zip(times,positions,measurements)]},
          {'kind':'scatter','title':f'{label} motion-fit residual','x_label':'Observation MJD (UTC)','x_unit':'day','y_label':'Residual','y_unit':'arcsec','points':[{'x':float(t),'y':float(r[axis])} for t,r in zip(times,residuals)]}])
    return {'status':'exploratory','reference_mjd':t0,'east_north_rate_mas_year':(beta[2:]*1000).tolist(),'formal_rate_covariance_mas2_year2':(covariance[2:,2:]*1e6).tolist(),'chi_square':chi2,'degrees_of_freedom':2*len(times)-4,'baseline_days':float(np.ptp(times)),
      'method':'Full 2-D formal centroid covariance weighted linear tangent-plane fit; conditional on common-source identity.','visualization_descriptors':descriptors,
      'limitations':['Source identity must be independently established; window-based fits are not temporal source association.','No parallax, orbital motion, absolute astrometric systematic or inter-epoch covariance included.','Local east/north rates approximate proper motion only for a compact sky region.']}

@router.post('/sequences/{identifier}/motion')
def sequence_motion(identifier:str,req:Extract):
    state=seq.status(identifier)
    if state['request']['mode']!='comparable':raise HTTPException(409,'Motion fitting requires a spectrally comparable accepted sequence.')
    point=state['request'];result=centroids(req.model_copy(update={'asset_ids':[f['asset_id'] for f in state['frames']],'product_ids':[],'ra':point['ra'],'dec':point['dec']}))
    fit=motion_fit(result['measurements']);result['motion_fit']=fit;result['visualization_descriptors'].extend(fit.get('visualization_descriptors',[]));return result
