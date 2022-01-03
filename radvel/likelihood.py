import numpy as np
import radvel.model
from radvel import gp
from scipy.linalg import cho_factor, cho_solve
import warnings


_has_celerite = gp._try_celerite()
if _has_celerite:
    import celerite


def custom_formatwarning(msg, *args, **kwargs):
    # ignore everything except the message
    return str(msg) + '\n'


warnings.formatwarning = custom_formatwarning


class Likelihood(object):
    """
    Generic Likelihood
    """
    def __init__(self, model, x, y, yerr, extra_params=[], decorr_params=[],
                 decorr_vectors=[]):
        self.model = model
        self.vector = model.vector
        self.params = model.params
        self.x = np.array(x)  # Variables must be arrays.
        self.y = np.array(y)  # Pandas data structures lead to problems.
        self.yerr = np.array(yerr)
        self.dvec = [np.array(d) for d in decorr_vectors]
        n = self.vector.vector.shape[0]
        for key in extra_params:
            if key not in self.params.keys():
                self.params[key] = radvel.model.Parameter(value=0.0)
            if key not in self.vector.indices:
                self.vector.indices.update({key:n})
                n += 1
        for key in decorr_params:
            if key not in self.params.keys():
                self.params[key] = radvel.model.Parameter(value=0.0)
            if key not in self.vector.indices:
                self.vector.indices.update({key:n})
                n += 1
        self.uparams = None

        self.vector.dict_to_vector()
        self.vector.vector_names()

    def __repr__(self):
        s = ""
        if self.uparams is None:
            s += "{:<20s}{:>15s}{:>10s}\n".format(
                'parameter', 'value', 'vary'
                )
            keys = self.params.keys()
            for key in keys:
                try:
                    vstr = str(self.params[key].vary)
                    if (key.startswith('tc') or key.startswith('tp')) and self.params[key].value > 1e6:
                        par = self.params[key].value - self.model.time_base
                    else:
                        par = self.params[key].value

                    s += "{:20s}{:15g} {:>10s}\n".format(
                        key, par, vstr
                        )
                except TypeError:
                    pass

            try:
                synthbasis = self.params.basis.to_synth(self.params, noVary=True)
                for key in synthbasis.keys():
                    if key not in keys:
                        try:
                            vstr = str(synthbasis[key].vary)
                            if (key.startswith('tc') or key.startswith('tp')) and synthbasis[key].value > 1e6:
                                par = synthbasis[key].value - self.model.time_base
                            else:
                                par = synthbasis[key].value

                            s += "{:20s}{:15g} {:>10s}\n".format(
                                key, par, vstr
                                )
                        except TypeError:
                            pass
            except TypeError:
                pass

        else:
            s = ""
            s += "{:<20s}{:>15s}{:>10s}{:>10s}\n".format(
                'parameter', 'value', '+/-', 'vary'
                )
            keys = self.params.keys()
            for key in keys:
                try:
                    vstr = str(self.params[key].vary)
                    if key in self.uparams.keys():
                        err = self.uparams[key]
                    else:
                        err = 0
                    if (key.startswith('tc') or key.startswith('tp')) and \
                            self.params[key].value > 1e6:
                        par = self.params[key].value - self.model.time_base
                    else:
                        par = self.params[key].value

                    s += "{:20s}{:15g}{:10g}{:>10s}\n".format(
                        key, par, err, vstr
                        )
                except TypeError:
                    pass

            try:
                synthbasis = self.params.basis.to_synth(self.params, noVary=True)
                for key in synthbasis.keys():
                    if key not in keys:
                        try:
                            vstr = str(synthbasis[key].vary)
                            if key in self.uparams.keys():
                                err = self.uparams[key]
                            else:
                                err = 0
                            if (key.startswith('tc') or key.startswith('tp')) and synthbasis[key].value > 1e6:
                                par = synthbasis[key].value - self.model.time_base
                            else:
                                par = synthbasis[key].value

                            s += "{:20s}{:15g}{:10g}{:>10s}\n".format(
                                key, par, err, vstr
                            )
                        except TypeError:
                            pass
            except TypeError:
                pass

        return s

    def set_vary_params(self, param_values_array):
        param_values_array = list(param_values_array)
        i = 0
        try:
            if len(self.vary_params) != len(param_values_array):
                self.list_vary_params()
        except AttributeError:
            self.list_vary_params()
        for index in self.vary_params:
            self.vector.vector[index][0] = param_values_array[i]
            i += 1
        assert i == len(param_values_array), \
            "Length of array must match number of varied parameters"

    def get_vary_params(self):
        try:
            return self.vector.vector[self.vary_params][:,0]
        except AttributeError:
            self.list_vary_params()
            return self.vector.vector[self.vary_params][:, 0]

    def list_vary_params(self):
        self.vary_params = np.where(self.vector.vector[:,1] == True)[0]

    def name_vary_params(self):
        list = []
        try:
            for i in self.vary_params:
                list.append(self.vector.names[i])
            return list
        except AttributeError:
            self.list_vary_params()
            for i in self.vary_params:
                list.append(self.vector.names[i])
            return list

    def residuals(self):
        return self.y - self.model(self.x)

    def neglogprob(self):
        return -1.0 * self.logprob()

    def neglogprob_array(self, params_array):
        return -self.logprob_array(params_array)

    def logprob_array(self, params_array):
        self.set_vary_params(params_array)
        _logprob = self.logprob()
        return _logprob

    def bic(self):
        """
        Calculate the Bayesian information criterion
        Returns:
            float: BIC
        """

        n = len(self.y)
        k = len(self.get_vary_params())
        _bic = np.log(n) * k - 2.0 * self.logprob()
        return _bic

    def aic(self):
        """
        Calculate the Aikike information criterion
        The Small Sample AIC (AICC) is returned because for most RV data sets n < 40 * k
        (see Burnham & Anderson 2002 S2.4).
        Returns:
            float: AICC
        """

        n = len(self.y)
        k = len(self.get_vary_params())
        aic = - 2.0 * self.logprob() + 2.0 * k
        # Small sample correction
        _aicc = aic
        denom = (n - k - 1.0)
        if denom > 0:
            _aicc += (2.0 * k * (k + 1.0)) / denom
        else:
            print("Warning: The number of free parameters is greater than or equal to")
            print("         the number of data points. The AICc comparison calculations")
            print("         will fail in this case.")
            _aicc = np.inf
        return _aicc


class CompositeLikelihood(Likelihood):
    """Composite Likelihood
    A thin wrapper to combine multiple `Likelihood`
    objects. One `Likelihood` applies to a dataset from
    a particular instrument.
    Args:
        like_list (list): list of `radvel.likelihood.RVLikelihood` objects
    """
    def __init__(self, like_list, **kwargs):
        self.nlike = len(like_list)

        like0 = like_list[0]
        params = like0.params
        vector = like0.vector
        self.model = like0.model
        self.x = like0.x
        self.y = like0.y
        self.yerr = like0.yerr
        self.telvec = like0.telvec
        self.extra_params = like0.extra_params
        self.suffixes = like0.suffix
        self.uparams = like0.uparams
        self.hnames = []

        for i in range(1, self.nlike):
            like = like_list[i]

            self.x = np.append(self.x, like.x)
            self.y = np.append(self.y, like.y - like.vector.vector[like.vector.indices[like.gamma_param]][0])
            self.yerr = np.append(self.yerr, like.yerr)
            self.telvec = np.append(self.telvec, like.telvec)
            self.extra_params = np.append(self.extra_params, like.extra_params)
            self.suffixes = np.append(self.suffixes, like.suffix)
            if hasattr(like, 'hnames'):
                self.hnames.extend(like.hnames)
            try:
                self.uparams = self.uparams.update(like.uparams)
            except AttributeError:
                self.uparams = None

            for k in like.params:
                if k in params:
                    assert like.params[k]._equals(params[k]), "Name={} {} != {}".format(k, like.params[k], params[k])
                else:
                    params[k] = like.params[k]

            assert like.vector is vector, \
                "Likelihoods must hold the same vector"

        self.extra_params = list(set(self.extra_params))
        self.params = params
        self.vector = vector
        self.like_list = like_list

    def logprob(self):
        """
        See `radvel.likelihood.RVLikelihood.logprob`
        """
        _logprob = 0
        for like in self.like_list:
            _logprob += like.logprob()
        return _logprob

    def residuals(self):
        """
        See `radvel.likelihood.RVLikelihood.residuals`
        """

        res = self.like_list[0].residuals()
        for like in self.like_list[1:]:
            res = np.append(res, like.residuals())

        return res

    def errorbars(self):
        """
        See `radvel.likelihood.RVLikelihood.errorbars`
        """
        err = self.like_list[0].errorbars()
        for like in self.like_list[1:]:
            err = np.append(err, like.errorbars())

        return err


class RVLikelihood(Likelihood):
    """RV Likelihood
    The Likelihood object for a radial velocity dataset
    Args:
        model (radvel.model.RVModel): RV model object
        t (array): time array
        vel (array): array of velocities
        errvel (array): array of velocity uncertainties
        suffix (string): suffix to identify this Likelihood object
           useful when constructing a `CompositeLikelihood` object.
    """
    def __init__(self, model, t, vel, errvel, suffix='', decorr_vars=[],
                 decorr_vectors=[], **kwargs):
        self.gamma_param = 'gamma'+suffix
        self.jit_param = 'jit'+suffix
        self.extra_params = [self.gamma_param, self.jit_param]

        if suffix.startswith('_'):
            self.suffix = suffix[1:]
        else:
            self.suffix = suffix

        self.telvec = np.array([self.suffix]*len(t))

        self.decorr_params = []
        self.decorr_vectors = decorr_vectors
        if len(decorr_vars) > 0:
            self.decorr_params += ['c1_'+d+suffix for d in decorr_vars]

        super(RVLikelihood, self).__init__(
            model, t, vel, errvel, extra_params=self.extra_params,
            decorr_params=self.decorr_params, decorr_vectors=self.decorr_vectors
            )

        self.gamma_index = self.vector.indices[self.gamma_param]
        self.jit_index = self.vector.indices[self.jit_param]

    def residuals(self):
        """Residuals
        Data minus model
        """
        mod = self.model(self.x)

        if self.vector.vector[self.gamma_index][3] and not self.vector.vector[self.gamma_index][1]:
            ztil = np.sum((self.y - mod)/(self.yerr**2 + self.vector.vector[self.jit_index][0]**2)) / \
                   np.sum(1/(self.yerr**2 + self.vector.vector[self.jit_index][0]**2))
            if np.isnan(ztil):
                 ztil = 0.0
            self.vector.vector[self.gamma_index][0] = ztil

        res = self.y - self.vector.vector[self.gamma_index][0] - mod

        if len(self.decorr_params) > 0:
            for parname in self.decorr_params:
                var = parname.split('_')[1]
                pars = []
                for par in self.decorr_params:
                    if var in par:
                        pars.append(self.vector.vector[self.vector.indices[par]][0])
                pars.append(0.0)
                if np.isfinite(self.decorr_vectors[var]).all():
                    vec = self.decorr_vectors[var] - np.mean(self.decorr_vectors[var])
                    p = np.poly1d(pars)
                    res -= p(vec)
        return res

    def errorbars(self):
        """
        Return uncertainties with jitter added
        in quadrature.
        Returns:
            array: uncertainties
        """
        return np.sqrt(self.yerr**2 + self.vector.vector[self.jit_index][0]**2)

    def logprob(self):
        """
        Return log-likelihood given the data and model.
        Priors are not applied here.
        Returns:
            float: Natural log of likelihood
        """

        sigma_jit = self.vector.vector[self.jit_index][0]
        residuals = self.residuals()
        loglike = loglike_jitter(residuals, self.yerr, sigma_jit)

        if self.vector.vector[self.gamma_index][3] \
                and not self.vector.vector[self.gamma_index][1]:
            sigz = 1/np.sum(1 / (self.yerr**2 + sigma_jit**2))
            loglike += np.log(np.sqrt(2 * np.pi * sigz))

        return loglike

class GPLikelihood(CompositeLikelihood):
    """
    The likelihood object for a Gaussian Process. Built off of CompositeLikelihood

    Args:
        like_list: list of `radvel.likelihood.RVLikelihood` objects
        hnames (list of str): names of all parameters to be passed to the gp.Kernel
            class to compute covariance matrices.
        kernel_name (str): one of gp.KERNELS.keys()

    """
    def __init__(self, like_list, hnames=None, kernel_name="QuasiPer", **kwargs):

        super(GPLikelihood, self).__init__(like_list)

        self.hnames = hnames  # list of string names of hyperparameters
        self.hyperparams = {k: self.params[k] for k in self.hnames}
        self.N = len(self.x)

        self.inst_indices = np.arange(self.N)
        self.insts = np.unique(self.telvec)
        for i in range(len(self.insts)):
            self.inst_indices[self.telvec == self.insts[i]] = i

        amp_array = np.empty(self.N, dtype=object)
        for i in range(len(self.insts)):
            amp_array[self.inst_indices == i] = 'gp_amp_{}'.format(self.insts[i])

        self.kernel_call = getattr(gp, kernel_name + "Kernel")
        self.kernel = self.kernel_call(self.hyperparams, amp_array)

        self.kernel.compute_distances(self.x, self.x)

    def update_kernel_params(self):

        for key in self.vector.indices:
            if key in self.hnames:
                hparams_key = key
                self.kernel.hparams[hparams_key].value = self.vector.vector[self.vector.indices[key]][0]

    def _resids(self):

        gammas = np.empty(self.N)
        for i in range(len(self.insts)):
            gamma_key = 'gamma_{}'.format(self.insts[i])
            gammas[self.inst_indices == i] = self.vector.vector[self.vector.indices[gamma_key]][0]

        res = self.y  - self.model(self.x) - gammas
        return res

    def residuals(self):

        gammas = np.empty(self.N)
        for i in range(len(self.insts)):
            gamma_key = 'gamma_{}'.format(self.insts[i])
            gammas[self.inst_indices == i] = self.vector.vector[self.vector.indices[gamma_key]][0]

        mu_pred, _ = self.predict(self.x)
        res = self.y - self.model(self.x) - mu_pred - gammas
        return res

    def logprob(self):
        # update the Kernel object hyperparameter values
        self.update_kernel_params()

        r = self._resids()

        self.kernel.compute_covmatrix(self.errorbars())

        K = self.kernel.covmatrix

        # solve alpha = inverse(K)*r
        try:
            alpha = cho_solve(cho_factor(K),r)

            # compute determinant of K
            (s,d) = np.linalg.slogdet(K)

            # calculate likelihood
            like = -.5 * (np.dot(r, alpha) + d + self.N*np.log(2.*np.pi))
            return like

        except (np.linalg.linalg.LinAlgError, ValueError):
            warnings.warn("Non-positive definite kernel detected.", RuntimeWarning)
            return -np.inf


    def predict(self, xpred, amp_param_name=None):
        """
        Compute a GP prediction at new times given the current parameter values
        stored in this GPLikelihood object.

        Args:
            xpred (np.array of float): times at which to compute prediction
            amp_param_name (str): if set, the name of the GP amplitude parameter
                that we'll use to make the prediction (i.e. we'll use the GP to 
                predict the RV value measured by this instrument at all `xpred`).
                If None, then xpred must be equal to the timestamps of all
                input data points (i.e. self.x). Default None. 

        Returns:
            tuple of:
                - GP mean function prediction at each input time
                - GP standard deviation of prediction at each input time
        """

        self.update_kernel_params()

        r = np.array([self._resids()]).T

        self.kernel.compute_distances(self.x, self.x)
        K = self.kernel.compute_covmatrix(self.errorbars())

        self.kernel.compute_distances(xpred, self.x)

        # set the GP amplitude for every predicted location to be
        #   that of the instrument we're making the prediction for
        if amp_param_name is not None:
            xpred_amps = (
                np.ones(len(xpred)) * 
                self.kernel.hparams[amp_param_name].value
            )
            data_amps = np.array([
                self.kernel.hparams[par].value for par in self.kernel.amps_array
            ])
            amp_matrix = (
                xpred_amps.reshape(len(xpred),1) @ 
                data_amps.reshape(1,self.N)
            )
        else:
            amp_matrix=None

        Ks = self.kernel.compute_covmatrix(0., amp_matrix=amp_matrix)

        L = cho_factor(K)
        alpha = cho_solve(L, r)
        mu = np.dot(Ks, alpha).flatten()

        self.kernel.compute_distances(xpred, xpred)

        # set the GP amplitude for every covariance matrix point to be 
        #   that of the instrument we're making the prediction for
        if amp_param_name is not None:
            amp_matrix = [self.kernel.hparams[amp_param_name].value**2]
        else:
            amp_matrix=None

        Kss = self.kernel.compute_covmatrix(0., amp_matrix=amp_matrix)
        B = cho_solve(L, Ks.T)
        var = np.array(np.diag(Kss - np.dot(Ks, B))).flatten()
        stdev = np.sqrt(var)

        # set the default distances back to their regular values
        self.kernel.compute_distances(self.x, self.x)

        return mu, stdev

# class CeleriteLikelihood(GPLikelihood):
#     """Celerite GP Likelihood
#     The Likelihood object for a radial velocity dataset modeled with a GP
#     whose kernel is an approximation to the quasi-periodic kernel.
#     See celerite.readthedocs.io and Foreman-Mackey et al. 2017. AJ, 154, 220
#     (equation 56) for more details.
#     See `radvel/example_planets/k2-131_celerite.py` for an example of a setup
#     file that uses this Likelihood object.
#     Args:
#         model (radvel.model.RVModel): RVModel object
#         t (array): time array
#         vel (array): array of velocities
#         errvel (array): array of velocity uncertainties
#         hnames (list of string): keys corresponding to radvel.Parameter
#            objects in model.params that are GP hyperparameters
#         suffix (string): suffix to identify this Likelihood object;
#            useful when constructing a `CompositeLikelihood` object
#     """

#     def __init__(self, model, t, vel, errvel, hnames, suffix='', **kwargs):

#         super(CeleriteLikelihood, self).__init__(
#             model, t, vel, errvel, hnames,
#             suffix=suffix, kernel_name='Celerite'
#         )

#         # Sort inputs in time order. Required for celerite calculations.
#         order = np.argsort(self.x)
#         self.x = self.x[order]
#         self.y = self.y[order]
#         self.yerr = self.yerr[order]
#         self.N = len(self.x)

#     def logprob(self):

#         self.update_kernel_params()

#         try:
#             solver = self.kernel.compute_covmatrix(self.errorbars())

#             # calculate log likelihood
#             lnlike = -0.5 * (solver.dot_solve(self._resids()) + solver.log_determinant() + self.N*np.log(2.*np.pi))

#             return lnlike

#         except celerite.solver.LinAlgError:
#             warnings.warn("Non-positive definite kernel detected.", RuntimeWarning)
#             return -np.inf

#     def predict(self,xpred):
#         """ Realize the GP using the current values of the hyperparameters at values x=xpred.
#             Used for making GP plots. Wrapper for `celerite.GP.predict()`.
#             Args:
#                 xpred (np.array): numpy array of x values for realizing the GP
#             Returns:
#                 tuple: tuple containing:
#                     np.array: numpy array of predictive means \n
#                     np.array: numpy array of predictive standard deviations
#         """

#         self.update_kernel_params()

#         B = self.kernel.hparams['gp_B'].value
#         C = self.kernel.hparams['gp_C'].value
#         L = self.kernel.hparams['gp_L'].value
#         Prot = self.kernel.hparams['gp_Prot'].value

#         # build celerite kernel with current values of hparams
#         kernel = celerite.terms.JitterTerm(
#                 log_sigma = np.log(self.vector.vector[self.jit_index][0])
#                 )

#         kernel += celerite.terms.RealTerm(
#             log_a=np.log(B*(1+C)/(2+C)),
#             log_c=np.log(1/L)
#         )

#         kernel += celerite.terms.ComplexTerm(
#             log_a=np.log(B/(2+C)),
#             log_b=-np.inf,
#             log_c=np.log(1/L),
#             log_d=np.log(2*np.pi/Prot)
#         )

#         gp = celerite.GP(kernel)
#         gp.compute(self.x, self.yerr)
#         mu, var = gp.predict(self._resids(), xpred, return_var=True)

#         stdev = np.sqrt(var)

#         return mu, stdev

def loglike_jitter(residuals, sigma, sigma_jit):
    """
    Log-likelihood incorporating jitter
    See equation (1) in Howard et al. 2014. Returns loglikelihood, where
    sigma**2 is replaced by sigma**2 + sigma_jit**2. It penalizes
    excessively large values of jitter
    Args:
        residuals (array): array of residuals
        sigma (array): array of measurement errors
        sigma_jit (float): jitter
    Returns:
        float: log-likelihood
    """
    sum_sig_quad = sigma**2 + sigma_jit**2
    penalty = np.sum( np.log( np.sqrt( 2 * np.pi * sum_sig_quad ) ) )
    chi2 = np.sum(residuals**2 / sum_sig_quad)
    loglike = -0.5 * chi2 - penalty

    return loglike