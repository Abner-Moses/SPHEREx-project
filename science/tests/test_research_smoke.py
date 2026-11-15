import numpy as np
from research_analysis import continuum

def test_joint_continuum_covariance_and_residuals():
    pts=[{'wavelength_um':x,'flux_jy':2+3*x,'flux_err_jy':.1} for x in [.8,1.,1.2,1.4,1.6]]
    fit=continuum(pts,1.1,1.3)
    assert fit['status']=='calculated'
    np.testing.assert_allclose(fit['residual_jy'],0,atol=1e-12)
    assert all(x>=0 for x in fit['continuum_sigma_jy'])

def test_insufficient_continuum_is_not_fabricated():
    assert continuum([{'wavelength_um':1.,'flux_jy':1.,'flux_err_jy':.1}])['status']=='unavailable'
