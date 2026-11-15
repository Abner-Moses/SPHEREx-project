"""Read-only calibrated measurements; never use PNG or catalog predictions as data.

Aperture flux is a linear, fractional-pixel surface-brightness estimator, not
PSF-corrected total-source photometry. Errors assume independent detector pixels.
"""
from contextlib import contextmanager
import math
import threading
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.stats import sigma_clip
from astropy.wcs import WCS
from photutils.aperture import CircularAperture, CircularAnnulus
from archive_client import DataError
from fits_processor import quality_mask, astrometric_quality, POLICY_VERSION, BAD_FLAGS
import observation_service as observations
import scientific_inspector as inspector

SLOTS = threading.BoundedSemaphore(2)
MAX_PIXELS = 4096 * 4096
WARNINGS = ["No PSF/aperture correction or total-source flux is inferred.",
            "Uncertainties assume independent pixels; calibration systematics, covariance and PSF mismatch are not included."]

@contextmanager
def source(identifier):
    if not SLOTS.acquire(blocking=False):
        raise DataError('Scientific measurement capacity is occupied; retry later.',503)
    try:
        with observations.asset_lease(identifier) as path:
            record=observations.get_observation(identifier)
            with fits.open(path,mode='readonly',memmap=False) as hdus:
                names=[h.name.upper() for h in hdus]
                if any(names.count(name)!=1 for name in ('IMAGE','FLAGS','VARIANCE')):
                    raise DataError('Unique IMAGE, FLAGS and VARIANCE extensions are required.',422)
                image,flags,variance=(np.asarray(hdus[name].data) for name in ('IMAGE','FLAGS','VARIANCE'))
                if image.ndim!=2 or image.size>MAX_PIXELS or flags.shape!=image.shape or variance.shape!=image.shape or not np.issubdtype(flags.dtype,np.integer):
                    raise DataError('Invalid or unbounded scientific image planes.',422)
                try:
                    factor=u.Unit(hdus['IMAGE'].header.get('BUNIT','')).to(u.MJy/u.sr)
                    variance_factor=u.Unit(hdus['VARIANCE'].header.get('BUNIT','')).to((u.MJy/u.sr)**2)
                except Exception as exc:raise DataError('IMAGE/VARIANCE units are not calibrated surface brightness and variance.',422) from exc
                yield path,record,hdus,image.astype(float)*factor,variance.astype(float)*variance_factor,quality_mask(image,flags,variance,hdus['FLAGS'].header)
    finally:SLOTS.release()

def _position(header,shape,x,y,ra,dec):
    sky=ra is not None or dec is not None
    if sky and (x is not None or y is not None) or sky and (ra is None or dec is None) or not sky and (x is None or y is None):
        raise DataError('Supply either x/y or ra/dec as one complete coordinate pair.',422)
    try:
        values=(ra,dec) if sky else (x,y)
        if any(isinstance(v,bool) or not math.isfinite(float(v)) for v in values):raise ValueError()
        wcs=WCS(header).celestial
        if sky:
            if not 0<=ra<360 or not -90<=dec<=90:raise ValueError()
            x,y=map(float,wcs.world_to_pixel(SkyCoord(ra*u.deg,dec*u.deg,frame='icrs')))
        if not np.isfinite([x,y]).all() or not 0<=x<=shape[1]-1 or not 0<=y<=shape[0]-1:raise ValueError()
        world=wcs.pixel_to_world(x,y).icrs
        return float(x),float(y),wcs,{'ra':float(world.ra.deg),'dec':float(world.dec.deg),'frame':'icrs'}
    except Exception as exc:raise DataError('Position is invalid or outside original IMAGE coverage.',422) from exc

def _areas(wcs,xs,ys):
    """Spherical pixel quadrilaterals from full celestial WCS incl. SIP.

    Four-corner great-circle approximation; curved subpixel edges are not fitted.
    Stable spherical triangle solid angles are evaluated on unit vectors.
    """
    xx,yy=np.meshgrid(xs,ys)
    corners=[]
    for dx,dy in [(-.5,-.5),(.5,-.5),(.5,.5),(-.5,.5)]:
        ra,dec=wcs.all_pix2world(xx+dx,yy+dy,0);ra,dec=np.deg2rad(ra),np.deg2rad(dec)
        corners.append(np.stack([np.cos(dec)*np.cos(ra),np.cos(dec)*np.sin(ra),np.sin(dec)],axis=-1))
    def triangle(a,b,c):
        num=np.abs(np.einsum('...i,...i->...',a,np.cross(b-a,c-a)))
        den=1+np.einsum('...i,...i->...',a,b)+np.einsum('...i,...i->...',b,c)+np.einsum('...i,...i->...',c,a)
        return 2*np.arctan2(num,den)
    area=triangle(corners[0],corners[1],corners[2])+triangle(corners[0],corners[2],corners[3])
    if not np.isfinite(area).all() or np.any(area<=0):raise DataError('Pixel solid angle could not be established.',422)
    return area

def _provenance(record):
    return {k:record.get(k) for k in ('id','product_id','observation_id','collection','source_url','retrieved_url','sha256','retrieved_at')}

def photometry(identifier,*,x=None,y=None,ra=None,dec=None,radius=3.,annulus_inner=6.,annulus_outer=10.,subtract_zodi=False):
    if not 0<radius<=50 or not radius+2<=annulus_inner<annulus_outer<=100:
        raise DataError('Use radius ≤50 px and radius+2 ≤ annulus_inner < annulus_outer ≤100 px; separate source/background pixels are required.',422)
    with source(identifier) as (path,record,hdus,image,variance,bad):
        header=hdus['IMAGE'].header;x,y,wcs,world=_position(header,image.shape,x,y,ra,dec)
        warnings=list(WARNINGS)
        if subtract_zodi:
            if 'ZODI' not in hdus or hdus['ZODI'].shape!=image.shape:raise DataError('Matching ZODI model is unavailable.',422)
            try:factor=u.Unit(hdus['ZODI'].header.get('BUNIT','')).to(u.MJy/u.sr)
            except Exception as exc:raise DataError('ZODI units are not surface brightness.',422) from exc
            image=image-np.asarray(hdus['ZODI'].data)*factor;bad=bad|~np.isfinite(image)
            warnings.append('Explicit ZODI subtraction: model uncertainty is unavailable and omitted.')
        x0=max(0,math.floor(x-annulus_outer-1));x1=min(image.shape[1],math.ceil(x+annulus_outer+2))
        y0=max(0,math.floor(y-annulus_outer-1));y1=min(image.shape[0],math.ceil(y+annulus_outer+2))
        image,variance,bad=image[y0:y1,x0:x1],variance[y0:y1,x0:x1],bad[y0:y1,x0:x1]
        local=(x-x0,y-y0);shape=image.shape
        ap=CircularAperture(local,radius).to_mask(method='exact').to_image(shape)
        bg=CircularAnnulus(local,annulus_inner,annulus_outer).to_mask(method='exact').to_image(shape)
        area=_areas(wcs,np.arange(x0,x1),np.arange(y0,y1))
        ap_valid=np.where(bad,0,ap);bg_valid=np.where(bad,0,bg)
        in_bg=bg_valid>0
        if np.count_nonzero(in_bg)<10 or ap_valid.sum()<=0:raise DataError('Insufficient scientifically usable aperture/background pixels.',422)
        clipped=sigma_clip(image[in_bg],sigma=3,maxiters=5,masked=True)
        good=np.zeros(shape,dtype=bool);good[in_bg]=~np.ma.getmaskarray(clipped)
        bg_valid=np.where(good,bg_valid,0)
        bw=(bg_valid*area)[good];values=image[good];v=variance[good]
        if len(values)<10 or bw.sum()<=0:raise DataError('Too few background pixels remain after clipping.',422)
        normalized=bw/bw.sum();background=float(np.sum(normalized*values))
        effective=1/float(np.sum(normalized**2))
        formal_bg_var=float(np.sum(normalized**2*v))
        empirical_bg_var=float(np.sum(normalized*(values-background)**2))/max(1.,effective-1.)
        bg_var=max(formal_bg_var,empirical_bg_var)
        weights=ap_valid*area*1e6
        residual=np.where(bad,0,image-background)
        flux=float(np.sum(weights*residual));measurement_var=float(np.sum(weights**2*np.where(bad,0,variance)))
        background_var=float(weights.sum()**2*bg_var);error=math.sqrt(measurement_var+background_var)
        coverage=float(ap.sum()/(math.pi*radius**2));usable=float(ap_valid.sum()/(math.pi*radius**2))
        complete=usable>=1-1e-6
        quality=astrometric_quality(header.get('FINAST',hdus[0].header.get('FINAST')))
        if not complete:warnings.append('Incomplete aperture: flux applies only to valid covered contributions; it is not a total-source measurement or a lower bound.')
        flags=hdus['FLAGS'].data[y0:y1,x0:x1]
        active=ap>0;flag_or=int(np.bitwise_or.reduce(flags[active].astype(np.uint64)))
        return {'asset_id':identifier,'position':{'x':x,'y':y,'pixel_origin':0,**world},
            'method':'fractional pixel surface-brightness sum; photutils exact circular overlap; sigma-clipped area-weighted annulus mean',
            'flux_jy':flux if complete else None,'valid_contribution_flux_jy':flux,'flux_err_jy':error,
            'flux_scope':'aperture flux' if complete else 'partial valid contributions only',
            'snr':flux/error if complete and error>0 else None,'lower_limit':False,
            'aperture':{'radius_px':radius,'geometric_coverage_fraction':min(1.,coverage),'scientific_valid_fraction':min(1.,usable),'complete':complete,'flag_bitmap_or':flag_or},
            'background':{'value_mjy_sr':background,'variance_of_estimate':bg_var,'effective_pixels':effective,'formal_variance':formal_bg_var,'empirical_variance':empirical_bg_var,'uncertainty_policy':'larger of formal and empirical mean variance'},
            'uncertainty':{'measurement_variance_jy2':measurement_var,'background_variance_jy2':background_var,'pixel_area':'four-corner spherical quadrilaterals from full WCS; curved edges approximated'},
            'astrometric_quality':quality,'scientifically_usable':bool(complete and quality['status']=='good'),
            'zodi_subtracted':subtract_zodi,'aperture_correction':None,'spectral':inspector.pixel(path,x=x,y=y)['spectral'],
            'warnings':warnings,'quality_policy':POLICY_VERSION,'provenance':_provenance(record)}

def statistics(identifier,*,bins=64):
    with source(identifier) as (_,record,_,image,variance,bad):
        values=image[~bad]
        if not values.size:raise DataError('No scientifically usable IMAGE samples.',422)
        hist,edges=np.histogram(values,bins=bins)
        return {'asset_id':identifier,'unit':'MJy/sr','data':'original calibrated IMAGE; no zodiacal subtraction',
            'valid_pixels':int(values.size),'excluded_pixels':int(bad.sum()),'minimum':float(values.min()),'maximum':float(values.max()),
            'mean':float(values.mean()),'median':float(np.median(values)),'std':float(values.std()),
            'histogram':{'counts':hist.tolist(),'edges':edges.tolist()},'quality_policy':POLICY_VERSION,'provenance':_provenance(record)}

def profile(identifier,*,x0,y0,x1,y1,samples=100):
    with source(identifier) as (_,record,hdus,image,variance,bad):
        _position(hdus['IMAGE'].header,image.shape,x0,y0,None,None);_position(hdus['IMAGE'].header,image.shape,x1,y1,None,None)
        xs=np.linspace(x0,x1,samples);ys=np.linspace(y0,y1,samples);ix=np.floor(xs+.5).astype(int);iy=np.floor(ys+.5).astype(int)
        points=[]
        for x,y,i,j in zip(xs,ys,ix,iy):
            valid=not bool(bad[j,i]);points.append({'x':float(x),'y':float(y),'sample_x':int(i),'sample_y':int(j),'usable':valid,'value':float(image[j,i]) if valid else None,'variance':float(variance[j,i]) if valid else None,'flags':int(hdus['FLAGS'].data[j,i])})
        return {'asset_id':identifier,'unit':'MJy/sr','method':'nearest original pixel; no image interpolation','points':points,'provenance':_provenance(record)}

def quality_summary(identifier):
    with source(identifier) as (_,record,hdus,image,variance,bad):
        flags=hdus['FLAGS'].data.astype(np.uint64)
        definitions={**BAD_FLAGS,'SOURCE':21}
        counts={name:int(np.count_nonzero(flags & (1<<int(hdus['FLAGS'].header.get('MP_'+name,bit))))) for name,bit in definitions.items()}
        good=variance[~bad]
        return {'asset_id':identifier,'quality_policy':POLICY_VERSION,'flag_counts':counts,
                'usable_pixels':int((~bad).sum()),'excluded_pixels':int(bad.sum()),
                'median_reported_sigma_mjy_sr':float(np.median(np.sqrt(good))) if good.size else None,
                'variance_interpretation':'sqrt of per-pixel calibrated VARIANCE; not empirical sky noise or artifact classification',
                'source_flag_interpretation':'Known catalog source membership; neither a detection count nor an independent quality approval',
                'provenance':_provenance(record)}

def invalid_mask(identifier):
    import io
    with source(identifier) as (_,record,hdus,_,_,bad):
        header=hdus['IMAGE'].header.copy();header['BUNIT']='1';header['EXTNAME']='SCI_INVALID'
        header['DERIVED']=True;header['MASKTRUE']='invalid';header['ORIGSHA']=record.get('sha256','unknown')
        header['HISTORY']='Scientific invalid-pixel mask under '+POLICY_VERSION+'; original FITS unchanged.'
        output=io.BytesIO();fits.PrimaryHDU(bad.astype(np.uint8),header=header).writeto(output,checksum=True)
        return output.getvalue()

def surface(identifier,*,maximum=128):
    with source(identifier) as (_,record,_,image,_,bad):
        height,width=image.shape
        xe=np.linspace(0,width,min(maximum,width)+1,dtype=int);ye=np.linspace(0,height,min(maximum,height)+1,dtype=int)
        values=[];coverage=[]
        for y0,y1 in zip(ye[:-1],ye[1:]):
            row=[];fractions=[]
            for x0,x1 in zip(xe[:-1],xe[1:]):
                data=image[y0:y1,x0:x1];valid=~bad[y0:y1,x0:x1]
                row.append(float(data[valid].mean()) if valid.any() else None)
                fractions.append(float(valid.mean()))
            values.append(row);coverage.append(fractions)
        return {'asset_id':identifier,'unit':'MJy/sr','height_meaning':'Calibrated surface brightness, not physical astronomical distance',
                'processing':'Arithmetic block mean of science-usable original pixels; no interpolation or normalization',
                'values':values,'scientific_valid_fraction':coverage,'original_pixel_x_edges':xe.tolist(),'original_pixel_y_edges':ye.tolist(),
                'provenance':_provenance(record),'measurement_warning':'Derived visualization grid; use original FITS for quantitative analysis.'}
