"""
Interface between the models and the fitters.

:class:`Fitness` defines the interface that new model definitions must follow.

:class:`FitProblem` defines the fitness function(s) for use in the fitters.
"""
from __future__ import division, with_statement

import sys
import time

from numpy import inf, isnan
import numpy

from . import parameter, bounds as mbounds
from .formatnum import format_uncertainty


def preview(models=[], weights=None):
    """Preview the models in preparation for fitting"""
    problem = _make_problem(models=models, weights=weights)
    result = Result(problem, problem.getp())
    result.show()
    return result

def mesh(models=[], weights=None, vars=None, n=40):
    problem = _make_problem(models=models, weights=weights)

    #print "initial chisq",problem.chisq()
    x,y = [numpy.linspace(p.bounds.limits[0],p.bounds.limits[1],n) for p in vars]
    p1, p2 = vars
    def fn(xi,yi):
        p1.value, p2.value = xi,yi
        problem.model_update()
        #print problem.summarize()
        return problem.chisq()
    z = [[fn(xi,yi) for xi in x] for yi in y]
    return x,y,numpy.asarray(z)


def fit(models=[], weights=None, fitter=None, **kw):
    """
    Perform a fit
    """
    problem = _make_problem(models=models, weights=weights)
    if fitter is not None:
        t0 = time.clock()
        opt = fitter(problem)
        x = opt.solve(**kw)
        print("time %g"%(time.clock() - t0))
    else:
        x = problem.getp()
    result = Result(problem, x)
    result.show()

    return result

def show_chisq(chisq, fid=None):
    """
    Show chisq statistics on a drawing from the likelihood function.

    dof is the number of degrees of freedom, required for showing the
    normalized chisq.
    """
    if fid is None: fid = sys.stdout
    v,dv = numpy.mean(chisq), numpy.std(chisq, ddof=1)
    lo, hi = min(chisq), max(chisq)

    valstr = format_uncertainty(v, dv)
    fid.write("Chisq for samples: %s,  [min,max] = [%g,%g]\n" % (valstr,lo,hi))

def show_stats(pars, points, fid=None):
    """
    Print a stylized list of parameter names and values with range bars.

    Report mean +/- std of the samples as the parameter values.
    """
    if fid is None: fid = sys.stdout

    val,err = numpy.mean(points, axis=0), numpy.std(points, axis=0, ddof=1)
    data = [(p.name, p.bounds, v, dv) for p,v,dv in zip(pars,val,err)]
    for name,bounds,v,dv in sorted(data, cmp=lambda x,y: cmp(x[0],y[0])):
        position = int(bounds.get01(v)*9.999999999)
        bar = ['.']*10
        if position < 0: bar[0] = '<'
        elif position > 9: bar[9] = '>'
        else: bar[position] = '|'
        bar = "".join(bar)
        valstr = format_uncertainty(v,dv)
        fid.write("%40s %s %-15s in %s\n"%(name,bar,valstr,bounds))

def show_correlations(pars, points, fid=None):
    """
    List correlations between parameters in descending order.
    """
    R = numpy.corrcoef(points.T)
    corr = [(i,j,R[i,j])
            for i in range(len(pars))
            for j in range(i+1, len(pars))]
    # Trim those which are not significant
    corr = [(i,j,r) for i,j,r in corr if abs(r) > 0.2]
    corr = list(sorted(corr, cmp=lambda x,y: cmp(abs(y[2]),abs(x[2]))))

    # Print the remaining correlations
    if len(corr) > 0:
        fid.write("== Parameter correlations ==\n")
        for i,j,r in corr:
            fid.write("%s X %s: %g\n"%(pars[i].name, pars[j].name, r))



from . import pytwalk
class TWalk:
    def __init__(self, problem):
        self.twalk = pytwalk.pytwalk(n=len(problem.getp()),
                                     U=problem.nllf,
                                     Supp=problem.valid)
    def run(self, N, x0, x1):
        self.twalk.Run(T=N, x0=x0, xp0=x1)
        return numpy.roll(self.twalk.Output, 1, axis=1)

class Result:
    def __init__(self, problem, solution):
        nllf = problem.nllf(solution) # TODO: Shouldn't have to recalculate!
        self.problem = problem
        self.solution = numpy.array(solution)
        self.points = numpy.array([numpy.hstack((nllf,solution))], 'd')

    def mcmc(self, samples=1e5, burnin=None, walker=TWalk):
        """
        Markov Chain Monte Carlo resampler.
        """
        if burnin is None: burnin = int(samples / 10)
        if burnin >= samples: raise ValueError("burnin must be smaller than samples")

        opt = walker(self.problem)
        x0 = numpy.array(self.solution)
        self.problem.randomize()
        x1 = self.problem.getp()
        points = opt.run(N=samples, x0=x0, x1=x1)
        self.points = numpy.vstack((self.points, points[burnin:]))
        self.problem.setp(self.solution)

    def resample(self, samples=100, restart=False, fitter=None, **kw):
        """
        Refit the result multiple times with resynthesized data, building
        up an array in Result.samples which contains the best fit to the
        resynthesized data.  *samples* is the number of samples to generate.
        *fitter* is the (local) optimizer to use. The kw are the parameters
        for the optimizer.
        """
        opt = fitter(self.problem)
        points = []
        try: # TODO: some solvers already catch KeyboardInterrupt
            for i in range(samples):
                print("== resynth %d of %d" % (i, samples))
                self.problem.resynth_data()
                if restart:
                    self.problem.randomize()
                else:
                    self.problem.setp(self.solution)
                x = opt.solve(**kw)
                nllf = self.problem.nllf(x) # TODO: don't recalculate!
                points.append(numpy.hstack((nllf,x)))
                print(self.problem.summarize())
                print("[chisq=%g]" % (nllf*2/self.problem.dof))
        except KeyboardInterrupt:
            pass
        self.points = numpy.vstack([self.points] + points)

        # Restore the original solution
        self.problem.restore_data()
        self.problem.setp(self.solution)

    def show_stats(self):
        if self.points.shape[0] > 1:
            self.problem.setp(self.solution)
            show_chisq(self.points[:,0]*2/self.problem.dof)
            show_stats(self.problem.parameters, self.points[:,1:])
            show_correlations(self.problem.parameters, self.points[:,1:])

    def save(self, basename):
        """
        Save the parameter table and the fitted model.
        """
        # TODO: need to do problem.setp(solution) in case the problem has
        # changed since result was created (e.g., when comparing multiple
        # fits). Same in showmodel()
        self.problem.setp(self.solution)
        fid = open(basename + ".par", "w")
        fid.write(self.problem.summarize()+"\n")
        fid.close()
        self.problem.save(basename)
        if self.points.shape[0] > 1:
            fid = open(basename + ".mc", "w")
            parhead = "\t".join(p.name for p in self.problem.parameters)
            fid.write("# nllf\t%s\n"%parhead)
            numpy.savetxt(fid, self.points, delimiter="\t")
            fid.close()
        return self

    def show(self):
        """
        Show the model parameters and plots
        """
        self.showmodel()
        self.showpars()
        return self

    def plot(self):
        self.problem.plot()
        return self

    def showmodel(self):
        print("== Model parameters ==")
        self.problem.setp(self.solution)
        self.problem.show()

    def showpars(self):
        print("== Fitted parameters ==")
        self.problem.setp(self.solution)
        print(self.problem.summarize())


def _make_problem(models=[], weights=None):
    if isinstance(models, (tuple, list)):
        if len(models) > 1:
            problem = MultiFitProblem(models, weights=weights)
        else:
            problem = FitProblem(models[0])
    else:
        problem = FitProblem(models)
    return problem


# Abstract base class
class Fitness(object):
    def parameters(self):
        """
        Return the set of parameters in the model.
        """
        raise NotImplementedError
    def update(self):
        """
        Called when parameters have been updated.  Any cached values will need to
        be cleared and the model reevaluated.
        """
        raise NotImplementedError
    def numpoints(self):
        """
        Return the number of data points.
        """
        raise NotImplementedError
    def nllf(self):
        """
        Return the negative log likelihood value of the current parameter set.
        """
        raise NotImplementedError
    def resynth_data(self):
        """
        Generate fake data based on uncertainties in the real data.  For Monte Carlo
        resynth-refit uncertainty analysis.  Bootstrapping?
        """
        raise NotImplementedError
    def restore_data(self):
        """
        Restore the original data in the model (after resynth).
        """
        raise NotImplementedError
    def residiuals(self):
        """
        Return residuals for current theory minus data.  For levenburg-marquardt.
        """
        raise NotImplementedError
    def save(self, basename):
        """
        Save the model to a file based on basename+extension.  This will point to
        a path to a directory on a remote machine; don't make any assumptions about
        information stored on the server.  Return the set of files saved so that
        the monitor software can make a pretty web page.
        """
        pass

    def plot(self):
        """
        Plot the model to the current figure.  You only get one figure, but you
        can make it as complex as you want.  This will be saved as a png on
        the server, and composed onto a results webpage.
        """
        pass


def no_constraints(): 
    """default constraints function for FitProblem"""
    return 0

# TODO: refactor FitProblem definition
# deprecate the direct use of MultiFitProblem
def FitProblem(*args, **kw):
    """
    Return a fit problem instance for the fitness function(s).

    For an individual model:

        *fitness* is a :class:`Fitness` instance.

    For a set of models:

        *models* is a sequence of :class:`Fitness` instances.

        *weights* is an optional scale factor for each model

        *freevars* is :class:`parameter.FreeVariables` instance defining the
        per-model parameter assignments.  See `freevariables`_ for details.


    Additional parameters:

        *name* name of the problem

        *constraints* is a function which returns the negative log likelihood
        of seeing the parameters independent from the fitness function.  Use
        this for example to check for feasible regions of the search space, or
        to add constraints that cannot be easily calculated per parameter.
        Ideally, the constraints nllf will increase as you go farther from
        the feasible region so that the fit will be directed toward feasible
        values.

        *soft_limit* is the constraints function cutoff, beyond which the
        *penalty_nllf* will be used and *fitness* nllf will not be calculated.

        *penalty_nllf* is the nllf to use for *fitness* when *constraints*
        is greater than *soft_limit*.

    Total nllf is the sum of the parameter nllf, the constraints nllf and the
    depending on whether constraints is greater than soft_limit, either the
    fitness nllf or the penalty nllf.
    """
    if len(args)>0:
        try:
            models = list(args[0])
        except TypeError:
            models = args[0]
        if isinstance(models, list):
            return MultiFitProblem(models, *args[1:], **kw)
        else:
            return BaseFitProblem(*args, **kw)
    else:
        if 'fitness' in kw:
            return BaseFitProblem(*args, **kw)
        else:
            return MultiFitProblem(*args, **kw)

class BaseFitProblem(object):
    """
    See :func:`FitProblem`
    """
    def __init__(self, fitness, name=None, constraints=no_constraints, 
                 penalty_nllf=1e6, soft_limit=numpy.inf, partial=False):
        self.constraints = constraints
        self.fitness = fitness
        self.partial = partial
        if name is not None:
            self.name = name
        else:
            try:
                self.name = fitness.name
            except:
                self.name = 'FitProblem'
               
        self.soft_limit = soft_limit
        self.penalty_nllf = penalty_nllf
        self.model_reset()

    def model_reset(self):
        """
        Prepare for the fit.

        This sets the parameters and the bounds properties that the
        solver is expecting from the fittable object.  We also compute
        the degrees of freedom so that we can return a normalized fit
        likelihood.

        If the set of fit parameters changes, then model_reset must
        be called.
        """
        #print self.model_parameters()
        all_parameters = parameter.unique(self.model_parameters())
        #print "all_parameters",all_parameters
        self._parameters = parameter.varying(all_parameters)
        #print "varying",self._parameters
        self.bounded = [p for p in all_parameters
                        if not isinstance(p.bounds, mbounds.Unbounded)]
        self.dof = self.model_points()
        if not self.partial: self.dof -= len(self._parameters)
        if self.dof <= 0:
            raise ValueError("Need more data points than fitting parameters")
        #self.constraints = pars.constraints()
    def model_parameters(self):
        """
        Parameters associated with the model.
        """
        return self.fitness.parameters()
    def model_points(self):
        """
        Number of data points associated with the model.
        """
        return self.fitness.numpoints()
    def model_update(self):
        """
        Update the model according to the changed parameters.
        """
        if hasattr(self.fitness, 'update'):
            self.fitness.update()
    def model_nllf(self):
        """
        Negative log likelihood of seeing data given model.
        """
        return self.fitness.nllf()

    def simulate_data(self, noise=None):
        """Simulate data with added noise"""
        self.fitness.simulate_data(noise=noise)
    def resynth_data(self):
        """Resynthesize data with noise from the uncertainty estimates."""
        self.fitness.resynth_data()
    def restore_data(self):
        """Restore original data after resynthesis."""
        self.fitness.restore_data()
    def valid(self, pvec):
        return all(v in p.bounds for p,v in zip(self._parameters,pvec))

    def setp(self, pvec):
        """
        Set a new value for the parameters into the model.  If the model
        is valid, calls model_update to signal that the model should be
        recalculated.

        Returns True if the value is valid and the parameters were set,
        otherwise returns False.
        """
        #TODO: do we have to leave the model in an invalid state?
        # WARNING: don't try to conditionally update the model
        # depending on whether any model parameters have changed.
        # For one thing, the model_update below probably calls
        # the subclass MultiFitProblem.model_update, which signals
        # the individual models.  Furthermore, some parameters may
        # related to others via expressions, and so a dependency
        # tree needs to be generated.  Whether this is better than
        # clicker() from SrFit I do not know.
        for v, p in zip(pvec, self._parameters):
            p.value = v
        # TODO: setp_hook is a hack to support parameter expressions in sasview
        # Don't depend on this existing long term.
        setp_hook = getattr(self, 'setp_hook', no_constraints)
        setp_hook()
        self.model_update()
    def getp(self):
        """
        Returns the current value of the parameter vector.
        """
        return numpy.array([p.value for p in self._parameters], 'd')

    def bounds(self):
        return numpy.array([p.bounds.limits for p in self._parameters],'d').T

    def randomize(self, N=None):
        """
        Generates a random model.

        *randomize(N)* returns a population of N random models.
        """
        # TODO: split into two: randomize and random_pop
        if N is not None:
            return numpy.array([p.bounds.random(N) for p in self._parameters]).T
        else:
            # Need to go through setp when updating model.
            self.setp([p.bounds.random(1)[0] for p in self._parameters])

    def parameter_nllf(self):
        """
        Returns negative log likelihood of seeing parameters p.
        """
        s = sum(p.nllf() for p in self.bounded)
        #print "; ".join("%s %g %g"%(p,p.value,p.nllf()) for p in self.bounded)
        return s

    def constraints_nllf(self):
        """
        Returns the cost of all constraints.
        """
        return self.constraints()

    def parameter_residuals(self):
        """
        Returns negative log likelihood of seeing parameters p.
        """
        return [p.residual() for p in self.bounded]

    def residuals(self):
        """
        Return the model residuals.
        """
        return self.fitness.residuals()

    def chisq(self):
        """
        Return sum squared residuals normalized by the degrees of freedom.

        In the context of a composite fit, the reduced chisq on the individual
        models only considers the points and the fitted parameters within
        the individual model.

        Note that this does not include cost factors due to constraints on
        the parameters, such as sample_offset ~ N(0,0.01).
        """
        return numpy.sum(self.residuals()**2) / self.dof
        #return 2*self.nllf()/self.dof
    def nllf(self, pvec=None):
        """
        Compute the cost function for a new parameter set p.

        Note that this is not simply the sum-squared residuals, but instead
        is the negative log likelihood of seeing the data given the model plus
        the negative log likelihood of seeing the model.  The individual
        likelihoods are scaled by 1/max(P) so that normalization constants
        can be ignored.

        The model is not actually calculated if the parameter nllf plus the
        constraint nllf are bigger than *soft_limit*, but instead it is
        assigned a value of *penalty_nllf*.
        """
        if pvec is not None:
            if self.valid(pvec):
                self.setp(pvec)
            else:
                return inf

        try:
            if isnan(self.parameter_nllf()):
                print("Parameter nllf is wrong")
                for p in self.bounded:
                    print("%s %g"%(p, p.nllf()))
            pparameter = self.parameter_nllf()
            pconstraint = self.constraints_nllf()
            pmodel = self.model_nllf() if pparameter+pconstraint <= self.soft_limit else self.penalty_nllf
            cost = pparameter + pconstraint + pmodel
            #print "cost",cost,"=",pparameter,pconstraint,pmodel,self.penalty_nllf
        except KeyboardInterrupt:
            raise
        except:
            #TODO: make sure errors get back to the user
            import traceback
            traceback.print_exc()
            print(parameter.summarize(self._parameters))
            return inf
        if isnan(cost):
            #TODO: make sure errors get back to the user
            #print "point evaluates to NaN"
            #print parameter.summarize(self._parameters)
            return inf
        # print pvec, "cost",cost,"=",pparameter,"+",pconstraint,"+",pmodel
        return cost

    def __call__(self, pvec=None):
        """
        Problem cost function.

        Returns the negative log likelihood scaled by DOF so that
        the result looks like the familiar normalized chi-squared.  These
        scale factors will not affect the value of the minimum, though some
        care will be required when interpreting the uncertainty.
        """
        return 2*self.nllf(pvec)/self.dof

    def show(self):
        print(parameter.format(self.model_parameters()))
        print("[chisq=%g, nllf=%g]" % (self.chisq(), self.nllf()))
        print(self.summarize())
    def summarize(self):
        return parameter.summarize(self._parameters)
    def labels(self):
        return [p.name for p in self._parameters]

    def save(self, basename):
        if hasattr(self.fitness, 'save'):
            self.fitness.save(basename)

    def plot(self, p=None, fignum=None, figfile=None):
        if not hasattr(self.fitness, 'plot'):
            return

        import pylab
        if fignum != None: pylab.figure(fignum)
        if p != None: self.setp(p)
        self.fitness.plot()
        pylab.text(0, 0, 'chisq=%g' % self.chisq(),
                   transform=pylab.gca().transAxes)
        if figfile != None:
            pylab.savefig(figfile+"-model.png", format='png')

    def stderr(self):
        from lsqerror import jacobian, cov, corr, stderr
        C = cov(jacobian(self))
        return stderr(C), corr(C)

    def __getstate__(self):
        return self.fitness,self.partial,self.name,self.penalty_nllf,self.soft_limit,self.constraints
    def __setstate__(self, state):
        self.fitness,self.partial,self.name,self.penalty_nllf,self.soft_limit,self.constraints = state
        self.model_reset()


class MultiFitProblem(BaseFitProblem):
    """
    Weighted fits for multiple models.
    """
    def __init__(self, models, weights=None, name=None,
                 constraints=no_constraints, 
                 soft_limit=numpy.inf, penalty_nllf=1e6,
                 freevars=None):
        self.partial = False
        self.constraints = constraints
        if freevars is None:
            freevars = parameter.FreeVariables(names=["M%d"%i for i,_ in enumerate(models)])
        self.freevars = freevars
        self._models = [BaseFitProblem(m,partial=True) for m in models]
        if weights is None:
            weights = [1 for m in models]
        self.weights = weights
        self.penalty_nllf = penalty_nllf
        self.soft_limit = soft_limit
        self.set_active_model(0) # Set the active model to model 0
        self.model_reset()
        self.name = name

    @property
    def models(self):
        """Iterate over models, with free parameters set from model specific values"""
        for i,f in enumerate(self._models):
            self.freevars.set_model(i)
            yield f
        # Restore the active model after cycling
        self.freevars.set_model(self._active_model_index)

    def set_active_model(self, i):
        """Use free parameters from model *i*"""
        self._active_model_index = i
        self.active_model = self._models[i]
        self.freevars.set_model(i)

    def model_parameters(self):
        """Return parameters from all models"""
        pars = { 'models':[f.model_parameters() for f in self.models] }
        free = self.freevars.parameters()
        if free: pars['freevars'] = free
        return pars

    def model_points(self):
        """Return number of points in all models"""
        return sum(f.model_points() for f in self.models)
    def model_update(self):
        """Let all models know they need to be recalculated"""
        # TODO: consider an "on changed" signal for model updates.
        # The update function would be associated with model parameters
        # rather than always recalculating everything.  This
        # allows us to set up fits with 'fast' and 'slow' parameters,
        # where the fit can quickly explore a subspace where the
        # computation is cheap before jumping to a more expensive
        # subspace.  SrFit does this.
        for f in self.models:
            f.model_update()
    def model_nllf(self):
        """Return cost function for all data sets"""
        return sum(f.model_nllf() for f in self.models)
    def constraints_nllf(self):
        """Return the cost function for all constraints"""
        return sum(f.constraints_nllf() for f in self.models) \
            + BaseFitProblem.constraints_nllf(self)
    def simulate_data(self, noise=None):
        """Simulate data with added noise"""
        for f in self.models: f.simulate_data(noise=noise)
    def resynth_data(self):
        """Resynthesize data with noise from the uncertainty estimates."""
        for f in self.models: f.resynth_data()
    def restore_data(self):
        """Restore original data after resynthesis."""
        for f in self.models: f.restore_data()
    def residuals(self):
        resid = numpy.hstack([w * f.residuals()
                              for w, f in zip(self.weights, self.models)])
        return resid

    def save(self, basename):
        for i, f in enumerate(self.models):
            f.save(basename + "-%d" % (i + 1))

    def show(self):
        for i, f in enumerate(self.models):
            print("-- Model %d %s"%(i, f.name))
            f.show()
        print("[overall chisq=%g, nllf=%g]" % (self.chisq(), self.nllf()))

    def plot(self,fignum=1,figfile=None):
        import pylab
        for i, f in enumerate(self.models):
            f.plot(fignum=i+fignum)
            pylab.suptitle('Model %d - %s'%(i,f.name))
            if figfile != None:
                pylab.savefig(figfile+"-model%d.png"%i, format='png')

    # Note: restore default behaviour of getstate/setstate rather than
    # inheriting from BaseFitProblem
    def __getstate__(self):
        return self.__dict__

    def __setstate__(self, state):
        self.__dict__ = state

def load_problem(file, options=[]):
    """
    Load a problem definition from a python script file.

    sys.argv is set to ``[file] + options`` within the context of the script.

    The user must define ``problem=FitProblem(...)`` within the script.

    Raises ValueError if the script does not define problem.
    """
    ctx = dict(__file__=file,__name__="bumps_model")
    old_argv = sys.argv
    sys.argv = [file] + options
    source = open(file).read()
    code = compile(source, file, 'exec')
    exec(code, ctx)
    sys.argv = old_argv
    try:
        problem = ctx["problem"]
    except KeyError:
        raise ValueError(file+" does not define 'problem=FitProblem(...)'")

    return problem
