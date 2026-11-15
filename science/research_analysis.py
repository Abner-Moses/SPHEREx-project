"""Original-grid aperture spectrophotometry and conservative time-series statistics."""
import numpy as np
from astropy.io import fits
from astropy.time import Time
from astropy.timeseries import LombScargle
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
import threading
import observation_service as assets
import scientific_measurements as measures
import sequence_service as sequences
import sequence_spectral as spectral
from catalog_api import Number
router=APIRouter(prefix='/api/research',tags=['quantitative research'])
SLOTS=threading.BoundedSemaphore(1)
class Extract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    asset_ids:list[str]=Field(default_factory=list,max_length=100)
    product_ids:list[str]=Field(default_factory=list,max_length=100)
    ra:Number=Field(ge=0,lt=360)
    dec:Number=Field(ge=-90,le=90)
    radius_px:Number=Field(default=3,gt=0,le=20)
    feature_min_um:Number|None=Field(default=None,ge=.7,le=5.1)
    feature_max_um:Number|None=Field(default=None,ge=.7,le=5.1)

def continuum(points,low=None,high=None):
    if not points:return {"status":"unavailable","reason":"No calibrated spectral measurements"}
    x=np.array([p['wavelength_um'] for p in points]);y=np.array([p['flux_jy'] for p in points]);err=np.array([p['flux_err_jy'] for p in points])
    use=np.ones(len(x),bool) if low is None else (x<low)|(x>high)
    if use.sum()<3 or len(np.unique(x[use]))<3:return {'status':'unavailable','reason':'At least three distinct continuum wavelengths outside the feature are required.'}
    X=np.column_stack([np.ones(len(x)),x-np.mean(x)])
    # Weighted linear estimator; retain covariance between fitted continuum and data.
    A=np.zeros((2,len(x)));A[:,use]=np.linalg.pinv(X[use]/err[use,None])/err[use]
    H=X@A;c=H@y;cov=(H*err**2)@H.T
    residual_sigma=np.sqrt(np.sum(((np.eye(len(x))-H)*err)**2,axis=1))
    residual=y-c;positive=(y>0)&(c>0);tau=np.full(len(x),np.nan);tau[positive]=np.log(c[positive]/y[positive])
    J=np.zeros_like(H);J[positive]=H[positive]/c[positive,None];J[np.arange(len(x))[positive],np.arange(len(x))[positive]]-=1/y[positive]
    tau_sigma=np.sqrt(np.sum((J*err)**2,axis=1))
    features={'status':'unavailable','reason':'Select a feature interval with at least two distinct ordered measured wavelengths.'}
    if low is not None:
        ids=np.flatnonzero((x>=low)&(x<=high))
        if len(ids)>=2 and np.all(np.diff(x)>0):
            weights=np.zeros(len(x));dx=np.diff(x[ids]);weights[ids[:-1]]+=dx/2;weights[ids[1:]]+=dx/2
            grad=weights@(np.eye(len(x))-H)
            features={'status':'calculated','sampled_interval_um':[float(x[ids[0]]),float(x[ids[-1]])],
              'signed_excess_jy_um':float(weights@residual),'signed_excess_sigma_jy_um':float(np.linalg.norm(grad*err)),
              'method':'Trapezoidal integration over measured centers only; joint continuum/data covariance propagated; no response deconvolution.'}
            if positive[ids].all():
                ratio_gradient=(y/c**2)[:,None]*H-np.diag(1/c) if (c>0).all() else None
                if ratio_gradient is not None:
                    features['equivalent_width_um']=float(weights@(1-y/c));features['equivalent_width_sigma_um']=float(np.linalg.norm((weights@ratio_gradient)*err))
                features['apparent_optical_depth_integral_um']=float(weights[ids]@tau[ids]);features['apparent_optical_depth_integral_sigma_um']=float(np.linalg.norm((weights@J)*err))
    return {'status':'calculated','feature_measurements':features,'method':'inverse-variance linear continuum; joint data/continuum covariance retained',
      'continuum_jy':c.tolist(),'continuum_sigma_jy':np.sqrt(np.diag(cov)).tolist(),'residual_jy':residual.tolist(),'residual_sigma_jy':residual_sigma.tolist(),
      'optical_depth':[float(v) if np.isfinite(v) else None for v in tau],
      'optical_depth_sigma':[float(v) if positive[i] else None for i,v in enumerate(tau_sigma)],
      'column_density':{'status':'unavailable','reason':'Requires validated band strength, response-aware feature coverage and foreground geometry.'},
      'feature_interval_um':[low,high] if low is not None else None,'identification':'No molecule is identified from feature wavelength alone.'}

@router.post('/spectrum')
def extract(req:Extract):
    if not SLOTS.acquire(False):raise HTTPException(503,'Research computation occupied.')
    try:
        if not req.asset_ids and not req.product_ids:raise HTTPException(422,'Select original assets or discovered products.')
        if (req.feature_min_um is None)!=(req.feature_max_um is None) or req.feature_min_um is not None and req.feature_min_um>=req.feature_max_um:raise HTTPException(422,'Provide an ordered pair of feature bounds.')
        points=[];rejected=[]
        identifiers=list(req.asset_ids)
        for product_id in dict.fromkeys(req.product_ids):
            try:identifiers.append(assets.retrieve(product_id,req.ra,req.dec,.1)['id'])
            except Exception as e:rejected.append({'product_id':product_id,'reason':str(e)})
        for identifier in dict.fromkeys(identifiers):
            try:
                with assets.asset_lease(identifier) as path:
                    record=assets.get_observation(identifier)
                    if record['validation']['scientific_acceptance']['accepted'] is not True:raise ValueError('Inspection-only quality')
                    flux=measures.photometry(identifier,ra=req.ra,dec=req.dec,radius=req.radius_px,annulus_inner=req.radius_px+3,annulus_outer=req.radius_px+7)
                    if not flux['scientifically_usable'] or flux['flux_jy'] is None or flux['flux_err_jy']<=0:raise ValueError('Unusable aperture or uncertainty')
                    band=spectral.resolve(path,record,req.ra,req.dec,sequences._service().calibrations)
                    if band.get('status')!='calibrated':raise ValueError('CWAVE/CBAND calibration unavailable')
                    with fits.open(path,memmap=False) as hdus:when=sequences.observation_time(hdus)
                    points.append({'asset_id':identifier,'observation_id':record.get('observation_id'),'collection':record.get('collection'),
                        'source_url':record.get('source_url'),'flux_jy':flux['flux_jy'],'flux_err_jy':flux['flux_err_jy'],
                        **when,'wavelength_um':band['wavelength_um'],'bandwidth_um':band['bandwidth_um'],'calibration':band})
            except Exception as e:rejected.append({'asset_id':identifier,'reason':str(e)})
        points.sort(key=lambda p:(p['wavelength_um'],p['mjd']))
        result={'status':'calculated' if points else 'insufficient','points':points,'rejected':rejected,
          'continuum':continuum(points,req.feature_min_um,req.feature_max_um) if len(points)>=3 else {'status':'unavailable','reason':'Need at least three calibrated spectral samples'},
          'visualization_descriptors':[{'kind':'scatter','title':'Calibrated aperture spectrophotometry','x_label':'Wavelength','x_unit':'µm','y_label':'Aperture flux density','y_unit':'Jy','points':[{'x':p['wavelength_um'],'y':p['flux_jy'],'error':p['flux_err_jy']} for p in points]}],
          'warnings':['Aperture flux, not PSF-corrected total flux.','Target CWAVE/CBAND is not the full response integrated across an extended aperture.','Samples may be noncontemporaneous; variability can contaminate a spectral interpretation.','Independent native-pixel errors; calibration systematics and inter-observation covariance omitted.'],
          'original_data_modified':False}
        fit=result['continuum']
        if fit.get('status')=='calculated':
            for key,title,unit in [('continuum_jy','Fitted continuum','Jy'),('residual_jy','Continuum-subtracted aperture spectrum','Jy'),('optical_depth','Apparent optical depth','1')]:
                result['visualization_descriptors'].append({'kind':'scatter','title':title,'x_label':'Wavelength','x_unit':'µm','y_label':title,'y_unit':unit,'points':[{'x':p['wavelength_um'],'y':value} for p,value in zip(points,fit[key]) if value is not None]})
        return result
    finally:SLOTS.release()

@router.post('/sequences/{identifier}/lightcurve')
def lightcurve(identifier:str,req:Extract):
    state=sequences.status(identifier)
    if state['request']['mode']!='comparable' or len(state['frames'])<2:raise HTTPException(409,'A comparable sequence with at least two accepted frames is required.')
    ids=[f['asset_id'] for f in state['frames']]
    result=extract(req.model_copy(update={'asset_ids':ids,'product_ids':[],'ra':state['request']['ra'],'dec':state['request']['dec']}))
    pts=sorted(result['points'],key=lambda p:p['mjd']);result['points']=pts
    if len(pts)>=2:
        t=np.array([p['mjd'] for p in pts]);y=np.array([p['flux_jy'] for p in pts]);e=np.array([p['flux_err_jy'] for p in pts]);w=1/e**2;mean=float(np.sum(w*y)/w.sum())
        result['variability']={'weighted_mean_jy':mean,'chi_square_constant_flux':float(np.sum(((y-mean)/e)**2)),'degrees_of_freedom':len(y)-1,'systematic_uncertainties_included':False,'confirmed_variability':False}
        distinct=np.unique(t);span=np.ptp(t)
        result['periodogram']={'status':'unavailable','reason':'Need at least 10 distinct epochs and a baseline supporting at least three cycles.'}
        if len(distinct)>=10 and span>6*np.median(np.diff(distinct)):
            ls=LombScargle(t,y,e);f=np.linspace(3/span,1/(2*np.median(np.diff(distinct))),min(10000,max(50,int(span/np.median(np.diff(distinct))*5))))
            power=ls.power(f)
            best=int(np.argmax(power));period=float(1/f[best]);result['periodogram']={'status':'exploratory','frequency_per_day':f.tolist(),'power':power.tolist(),'best_period_days':period,'phase':((t-t.min())/period%1).tolist(),'warning':'Sampling aliases, spectral mismatch and systematics may mimic periodic signals; no period detection claimed.'}
    result['visualization_descriptors']=[{'kind':'scatter','title':'Measured aperture light curve','x_label':'Observation MJD (UTC)','x_unit':'day','y_label':'Aperture flux density','y_unit':'Jy','points':[{'x':p['mjd'],'y':p['flux_jy'],'error':p['flux_err_jy']} for p in pts]}]
    if result.get('periodogram',{}).get('status')=='exploratory':
        period=result['periodogram'];result['visualization_descriptors'].extend([
          {'kind':'line','title':'Exploratory Lomb–Scargle periodogram','x_label':'Frequency','x_unit':'1/day','y_label':'Normalized power','y_unit':'1','points':list(map(list,zip(period['frequency_per_day'],period['power'])))},
          {'kind':'scatter','title':'Exploratory phase fold','x_label':'Phase','x_unit':'cycle','y_label':'Aperture flux','y_unit':'Jy','points':[{'x':phase,'y':p['flux_jy'],'error':p['flux_err_jy']} for phase,p in zip(period['phase'],pts)]}])
    return result
