# -*- coding: utf-8 - vim: tw=80
"""
Evaluation of convergent D-finite series by direct summation
"""

# TODO:
# - support summing a given number of terms rather than until a target accuracy
# is reached?
# - cythonize critical parts?

import collections, itertools, logging

from sage.categories.pushout import pushout
from sage.matrix.constructor import identity_matrix, matrix
from sage.modules.free_module_element import vector
from sage.rings.all import ZZ, QQ, RR, QQbar, infinity
from sage.rings.complex_arb import ComplexBallField, CBF, ComplexBall
from sage.rings.integer import Integer
from sage.rings.polynomial import polynomial_element
from sage.rings.polynomial.polynomial_ring_constructor import PolynomialRing
from sage.rings.real_arb import RealBallField, RBF, RealBall

from .. import ore_algebra
from . import accuracy, bounds, utilities
from .local_solutions import (backward_rec, FundamentalSolution,
        LogSeriesInitialValues, map_local_basis)
from .safe_cmp import *
from .shiftless import my_shiftless_decomposition
from .utilities import short_str

logger = logging.getLogger(__name__)

################################################################################
# Argument processing etc. (common to the ordinary and the regular case)
################################################################################

class EvaluationPoint(object):
    r"""
    A ring element (a complex number, a polynomial indeterminate, perhaps
    someday a matrix) where to evaluate the partial sum of a series, along with
    a “jet order” used to compute derivatives and a bound on the norm of the
    mathematical quantity it represents that can be used to bound the truncation
    error.
    """

    # XXX: choose a single place to set the default value for jet_order
    def __init__(self, pt, rad=None, jet_order=1):
        self.pt = pt
        self.rad = (bounds.IR.coerce(rad) if rad is not None
                    else bounds.IC(pt).above_abs())
        self.jet_order = jet_order

        self.is_numeric = utilities.is_numeric_parent(pt.parent())

    def __repr__(self):
        fmt = "{} + η + O(η^{}) (with |.| ≤ {})"
        return fmt.format(self.pt, self.jet_order + 1, self.rad)

    def jet(self, Intervals):
        base_ring = (Intervals if self.is_numeric
                     else pushout(self.pt.parent(), Intervals))
        Pol = PolynomialRing(base_ring, 'eta')
        return Pol([self.pt, 1]).truncate(self.jet_order)

    def is_real(self):
        return utilities.is_real_parent(self.pt.parent())

    def accuracy(self):
        if self.pt.parent().is_exact():
            return bounds.IR.maximal_accuracy()
        elif isinstance(self.pt.parent(), (RealBallField, ComplexBallField)):
            return self.pt.accuracy()
        else:
            raise ValueError

def series_sum(dop, ini, pt, tgt_error, maj=None, bwrec=None,
        stride=50, record_bounds_in=None, max_prec=100000):
    r"""
    EXAMPLES::

        sage: from sage.rings.real_arb import RealBallField, RBF
        sage: from sage.rings.complex_arb import ComplexBallField, CBF
        sage: QQi.<i> = QuadraticField(-1)

        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.naive_sum import series_sum, EvaluationPoint
        sage: Dops, x, Dx = DifferentialOperators()

        sage: dop = ((4*x^2 + 3/58*x - 8)*Dx^10 + (2*x^2 - 2*x)*Dx^9 +
        ....:       (x^2 - 1)*Dx^8 + (6*x^2 - 1/2*x + 4)*Dx^7 +
        ....:       (3/2*x^2 + 2/5*x + 1)*Dx^6 + (-1/6*x^2 + x)*Dx^5 +
        ....:       (-1/5*x^2 + 2*x - 1)*Dx^4 + (8*x^2 + x)*Dx^3 +
        ....:       (-1/5*x^2 + 9/5*x + 5/2)*Dx^2 + (7/30*x - 12)*Dx +
        ....:       8/7*x^2 - x - 2)
        sage: ini = [CBF(-1/16, -2), CBF(-17/2, -1/2), CBF(-1, 1), CBF(5/2, 0),
        ....:       CBF(1, 3/29), CBF(-1/2, -2), CBF(0, 0), CBF(80, -30),
        ....:       CBF(1, -5), CBF(-1/2, 11)]

    Funny: on the following example, both the evaluation point and most of the
    initial values are exact, so that we end up with a significantly better
    approximation than requested::

        sage: series_sum(dop, ini, 1/2, RBF(1e-16))
        ([-3.575140703474456...] + [-2.2884877202396862...]*I)

        sage: import logging; logging.basicConfig()
        sage: series_sum(dop, ini, 1/2, RBF(1e-30))
        WARNING:ore_algebra.analytic.naive_sum:input intervals may be too wide
        ...
        ([-3.5751407034...] + [-2.2884877202...]*I)

    In normal usage ``pt`` should be an object coercible to a complex ball or an
    :class:`EvaluationPoint` that wraps such an object. Polynomials (wrapped in
    EvaluationPoints) are also supported to some extent (essentially, this is
    intended for use with polynomial indeterminates, and anything else that
    works does so by accident). ::

        sage: from ore_algebra.analytic.accuracy import AbsoluteError
        sage: series_sum(Dx - 1, [RBF(1)],
        ....:         EvaluationPoint(x, rad=RBF(1), jet_order=2),
        ....:         AbsoluteError(1e-3), stride=1)
        (... + [0.0083...]*x^5 + [0.0416...]*x^4 + [0.1666...]*x^3
        + 0.5000...*x^2 + x + [1.000...],
        ... + [0.0083...]*x^5 + [0.0416...]*x^4 + [0.1666...]*x^3
        + [0.5000...]*x^2 + x + [1.000...])

    TESTS::

        sage: b = series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [RBF(0), RBF(1)],
        ....:                         7/10, RBF(1e-30))
        sage: b.parent()
        Vector space of dimension 1 over Real ball field with ... precision
        sage: b[0].rad().exact_rational() < 10^(-30)
        True
        sage: b[0].overlaps(RealBallField(130)(7/10).arctan())
        True

        sage: b = series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [CBF(0), CBF(1)],
        ....:                         (i+1)/2, RBF(1e-30))
        sage: b.parent()
        Vector space of dimension 1 over Complex ball field with ... precision
        sage: b[0].overlaps(ComplexBallField(130)((1+i)/2).arctan())
        True

        sage: series_sum(x*Dx^2 + Dx + x, [0], 1/2, 1e-10)
        Traceback (most recent call last):
        ...
        ValueError: invalid initial data for x*Dx^2 + Dx + x at 0

        sage: iv = RBF(RIF(-10^(-6), 10^(-6)))
        sage: series_sum(((6+x)^2 + 1)*Dx^2+2*(6+x)*Dx, [iv, iv], 4, RBF(1e-10))
        WARNING:...
        ([+/- ...])

    Test that automatic precision increases do something reasonable::

        sage: logger = logging.getLogger('ore_algebra.analytic.naive_sum')
        sage: logger.setLevel(logging.INFO)

        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, 1/3], 5/7, 1e-16, max_prec=10**1000)
        INFO:...
        ([0.20674982866094049...])

        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, RBF(1/3)], 5/7, 1e-16, max_prec=10**1000)
        WARNING:ore_algebra.analytic.naive_sum:input intervals may be too wide compared to requested accuracy
        ...
        ([0.206749828660940...])

        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, RBF(1/3)], RBF(5/7), 1e-12, max_prec=10**1000)
        INFO:...
        ([0.2067498286609...])

        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, RBF(1/3)], RBF(5/7), 1e-20, max_prec=10**1000)
        WARNING:ore_algebra.analytic.naive_sum:input intervals may be too wide compared to requested accuracy
        ...
        INFO:ore_algebra.analytic.naive_sum:lost too much precision, giving up
        ([0.20674982866094...])

        sage: xx = EvaluationPoint(x, rad=RBF(1/4), jet_order=2)
        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, 1/3], xx, 1e-30)[0](1/6)
        INFO:...
        [0.05504955913820894609304276321...]

        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, RBF(1/3)], xx, 1e-16)[0](1/6)
        WARNING:ore_algebra.analytic.naive_sum:input intervals may be too wide compared to requested accuracy
        ...
        [0.055049559138208...]

        sage: series_sum((x^2 + 1)*Dx^2 + 2*x*Dx, [0, RBF(1/3)], xx, 1e-30)[0](1/6)
        WARNING:ore_algebra.analytic.naive_sum:input intervals may be too wide compared to requested accuracy
        ...
        INFO:ore_algebra.analytic.naive_sum:lost too much precision, giving up
        [0.055049559138208...]

        sage: logger.setLevel(logging.WARNING)
    """

    # The code that depends neither on the numeric precision nor on the
    # ordinary/regsing dichotomy goes here.

    if not isinstance(ini, LogSeriesInitialValues):
        ini = LogSeriesInitialValues(ZZ.zero(), ini, dop)

    if not isinstance(pt, EvaluationPoint):
        pt = EvaluationPoint(pt)

    input_accuracy = min(pt.accuracy(), ini.accuracy())

    if isinstance(tgt_error, accuracy.RelativeError) and pt.jet_order > 1:
        raise TypeError("relative error not supported when computing derivatives")
    if not isinstance(tgt_error, accuracy.OldStoppingCriterion):
        tgt_error = accuracy.AbsoluteError(tgt_error)
        if input_accuracy < -tgt_error.eps.upper().log2().floor():
            logger.warn("input intervals may be too wide "
                        "compared to requested accuracy")
    logger.log(logging.INFO - 1, "target error = %s", tgt_error)

    if maj is None:
        maj = bounds.DiffOpBound(dop, ini.expo,
                [] if dop.leading_coefficient().valuation() == 0
                else [(s, len(v)) for s, v in ini.shift.iteritems()])

    if bwrec is None:
        bwrec = backward_rec(dop, shift=ini.expo)

    ivs = (RealBallField
           if ini.is_real(dop) and (pt.is_real() or not pt.is_numeric)
           else ComplexBallField)
    doit = (series_sum_ordinary if dop.leading_coefficient().valuation() == 0
            else series_sum_regular)

    # Now do the actual computation, automatically increasing the precision as
    # necessary

    bit_prec = utilities.prec_from_eps(tgt_error.eps)
    # Roughly speaking, the computation of a new coefficient of the series
    # *multiplies* the diameter by the order of the recurrence, so it is not
    # unreasonable that the loss of precision is of the order of
    # log2(ordrec^nterms)... but this observation is far from explaining
    # everything; in particular, it completely ignores the size of the
    # coefficients of the recurrence. Anyhow, this formula seems to work well in
    # practice.
    bit_prec = 8 + bit_prec*(1 + ZZ(bwrec.order - 2).nbits())
    max_prec = min(max_prec, bit_prec + 2*input_accuracy) # XXX: only if None?
    logger.info("initial precision = %s bits", bit_prec)
    for attempt in itertools.count():
        try:
            # ask for a slightly higher accuracy each time to avoid situations
            # where doit would be happy with the result and stop at the same
            # point despite the higher bit_prec
            psum = doit(ivs(bit_prec), dop, bwrec, ini, pt,
                    tgt_error >> (4*attempt), maj, stride, record_bounds_in)
            err = max(_get_error(c) for c in psum)
            logger.debug("bit_prec=%s, err=%s (tgt=%s)", bit_prec, err,
                    tgt_error)
            bit_prec *= 2
            abs_sum = abs(psum[0]) if pt.is_numeric else None
            if tgt_error.reached(err, abs_sum):
                return psum
            elif bit_prec > max_prec:
                logger.info("lost too much precision, giving up")
                return psum
        except accuracy.PrecisionError:
            logger.debug("bit_prec=%s, PrecisionError", bit_prec)
            bit_prec *= 2
            if bit_prec > max_prec:
                logger.info("lost too much precision, giving up")
                raise
        logger.info("lost too much precision, restarting with %d bits",
                    bit_prec)

################################################################################
# Ordinary points
################################################################################

def series_sum_ordinary(Intervals, dop, bwrec, ini, pt,
        tgt_error, maj, stride, record_bounds_in):

    if record_bounds_in:
        record_bounds_in[:] = []

    jet = pt.jet(Intervals)
    Jets = jet.parent() # polynomial ring!
    ord = pt.jet_order
    jetpow = Jets.one()
    radpow = bounds.IR.one()

    ordrec = bwrec.order
    assert ini.expo.is_zero()
    last = collections.deque([Intervals.zero()]*(ordrec - dop.order() + 1))
    last.extend(Intervals(ini.shift[n][0])
                for n in xrange(dop.order() - 1, -1, -1))
    assert len(last) == ordrec + 1 # not ordrec!
    psum = Jets.zero()

    tail_bound = bounds.IR(infinity)
    bit_prec = Intervals.precision()
    ini_are_accurate = 2*min(pt.accuracy(), ini.accuracy()) > bit_prec

    start = dop.order()
    # Evaluate the coefficients a bit in advance as we are going to need them to
    # compute the residuals. This is not ideal at high working precision, but
    # already saves a lot of time compared to doing the evaluations twice.
    bwrec_nplus = collections.deque(
            (bwrec.eval_int_ball(Intervals, start+i) for i in xrange(ordrec)),
            maxlen=ordrec)

    stopping_criterion = accuracy.StoppingCriterion(
            maj=maj, eps=tgt_error.eps,
            get_residuals=(lambda:
                [maj.normalized_residual(n, [[c] for c in last][1:],
                        [[[c] for c in l] for l in bwrec_nplus])]),
            get_bound=(lambda(resid):
                maj.matrix_sol_tail_bound(n, pt.rad, resid, ord)),
            fast_fail=ini_are_accurate,
            force=(record_bounds_in is not None))

    for n in range(start): # Initial values (“singular part”)
        last.rotate(1)
        term = Jets(last[0])._mul_trunc_(jetpow, ord)
        psum += term
        jetpow = jetpow._mul_trunc_(jet, ord)
        radpow *= pt.rad
    for n in itertools.count(start):
        last.rotate(1)
        #last[0] = None
        # At this point last[0] should be considered undefined (it will hold
        # the coefficient of z^n later in the loop body) and last[1], ...
        # last[ordrec] are the coefficients of z^(n-1), ..., z^(n-ordrec)
        if n%stride == 0:
            radpowest = abs(jetpow[0] if pt.is_numeric
                            else Intervals(pt.rad**n))
            done, tail_bound = stopping_criterion.check(n, tail_bound,
                    est=sum(abs(a) for a in last)*radpowest,
                    next_stride=stride)
            if record_bounds_in is not None:
                record_bounds_in.append((n, psum, tail_bound))
            if done:
                break
        bwrec_n = (bwrec_nplus[0] if bwrec_nplus
                   else bwrec.eval_int_ball(Intervals, n))
        comb = sum(bwrec_n[k]*last[k] for k in xrange(1, ordrec+1))
        last[0] = -~bwrec_n[0]*comb
        # logger.debug("n = %s, [c(n), c(n-1), ...] = %s", n, list(last))
        term = Jets(last[0])._mul_trunc_(jetpow, ord)
        psum += term
        jetpow = jetpow._mul_trunc_(jet, ord)
        radpow *= pt.rad
        bwrec_nplus.append(bwrec.eval_int_ball(Intervals, n+bwrec.order))
    logger.info("summed %d terms, tail <= %s, coeffwise error <= %s", n,
            tail_bound,
            max(x.rad() for x in psum) if pt.is_numeric else "n/a")
    # Account for the dropped high-order terms in the intervals we return
    # (tail_bound is actually a bound on the Frobenius norm of the error matrix,
    # so there is some overestimation). WARNING: For symbolic x, the resulting
    # polynomials have to be interpreted with some care: in particular, it would
    # be incorrect to evaluate a polynomial result with real coefficients at a
    # complex point. Our current mechanism to choose whether to add a real or
    # complex error bound in this case is pretty fragile.
    tail_bound = tail_bound.abs()
    res = vector(_add_error(psum[i], tail_bound) for i in xrange(ord))
    return res

# XXX: pass ctx (→ real/complex?)?
def fundamental_matrix_ordinary(dop, pt, eps, rows, maj, max_prec=100000):
    eps_col = bounds.IR(eps)/bounds.IR(dop.order()).sqrt()
    evpt = EvaluationPoint(pt, jet_order=rows)
    inis = [
        LogSeriesInitialValues(ZZ.zero(), ini, dop, check=False)
        for ini in identity_matrix(dop.order())]
    cols = [
        series_sum(dop, ini, evpt, eps_col, maj=maj, max_prec=max_prec)
        for ini in inis]
    return matrix(cols).transpose()

################################################################################
# Regular singular points
################################################################################

def fundamental_matrix_regular(dop, pt, eps, rows):
    r"""
    TESTS::

        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.naive_sum import *
        sage: Dops, x, Dx = DifferentialOperators()

        sage: fundamental_matrix_regular(x*Dx^2 + (1-x)*Dx, 1, RBF(1e-10), 2)
        [[1.317902...] 1.000000...]
        [[2.718281...]           0]

        sage: dop = (x+1)*(x^2+1)*Dx^3-(x-1)*(x^2-3)*Dx^2-2*(x^2+2*x-1)*Dx
        sage: fundamental_matrix_regular(dop, 1/3, RBF(1e-10), 3)
        [1.0000000...  [0.321750554...]  [0.147723741...]]
        [           0  [0.900000000...]  [0.991224850...]]
        [           0  [-0.27000000...]  [1.935612425...]]

        sage: dop = (
        ....:     (2*x^6 - x^5 - 3*x^4 - x^3 + x^2)*Dx^4
        ....:     + (-2*x^6 + 5*x^5 - 11*x^3 - 6*x^2 + 6*x)*Dx^3
        ....:     + (2*x^6 - 3*x^5 - 6*x^4 + 7*x^3 + 8*x^2 - 6*x + 6)*Dx^2
        ....:     + (-2*x^6 + 3*x^5 + 5*x^4 - 2*x^3 - 9*x^2 + 9*x)*Dx)
        sage: fundamental_matrix_regular(dop, RBF(1/3), RBF(1e-10), 4)
        [ [3.1788470...] [-1.064032...]  [1.000...] [0.3287250...]]
        [ [-8.981931...] [3.2281834...]    [+/-...] [0.9586537...]]
        [  [26.18828...] [-4.063756...]    [+/-...] [-0.123080...]]
        [ [-80.24671...]  [9.190740...]    [+/-...] [-0.119259...]]

        sage: dop = x*Dx^3 + 2*Dx^2 + x*Dx
        sage: ini = [1, CBF(euler_gamma), 0]
        sage: dop.numerical_solution(ini, [0, RBF(1/3)], 1e-14)
        [-0.549046117782...]
    """
    evpt = EvaluationPoint(pt, jet_order=rows)
    eps_col = bounds.IR(eps)/bounds.IR(dop.order()).sqrt()
    col_tgt_error = accuracy.AbsoluteError(eps_col)
    def get_maj(leftmost, shifts):
        return {'maj': bounds.DiffOpBound(dop, leftmost, shifts,
                                        pol_part_len=4, bound_inverse="solve") }
    def get_value(ini, bwrec, maj):
        return series_sum(dop, ini, evpt, col_tgt_error, maj=maj, bwrec=bwrec)
    cols = map_local_basis(dop, get_value, get_maj)
    return matrix([sol.value for sol in cols]).transpose()

def _pow_trunc(a, n, ord):
    pow = a.parent().one()
    pow2k = a
    while n:
        if n & 1:
            pow = pow._mul_trunc_(pow2k, ord)
        pow2k = pow2k._mul_trunc_(pow2k, ord)
        n = n >> 1
    return pow

def log_series_value(Jets, derivatives, expo, psum, pt):
    log_prec = psum.length()
    if log_prec > 1 or expo not in ZZ:
        pt = pt.parent().complex_field()(pt)
        Jets = Jets.change_ring(Jets.base_ring().complex_field())
        psum = psum.change_ring(Jets)
    # hardcoded series expansions of log(pt) = log(a+η) and pt^λ = (a+η)^λ (too
    # cumbersome to compute directly in Sage at the moment)
    high = Jets([0] + [(-1)**(k+1)*~pt**k/k
                       for k in xrange(1, derivatives)])
    logpt = Jets([pt.log()]) + high
    logger.debug("logpt=%s", logpt)
    aux = high*expo
    logger.debug("aux=%s", aux)
    inipow = pt**expo*sum(_pow_trunc(aux, k, derivatives)/Integer(k).factorial()
                          for k in xrange(derivatives))
    logger.debug("inipow=%s", inipow)
    val = inipow.multiplication_trunc(
            sum(psum[p]._mul_trunc_(_pow_trunc(logpt, p, derivatives), derivatives)
                        /Integer(p).factorial()
                for p in xrange(log_prec)),
            derivatives)
    return val

# This function only handles the case of a “single” series, i.e. a series where
# all indices differ from each other by integers. But since we need logic to go
# past singular indices anyway, we can allow for general initial conditions (at
# roots of the indicial equation belonging to the same shift-equivalence class),
# not just initial conditions associated to canonical solutions.
def series_sum_regular(Intervals, dop, bwrec, ini, pt, tgt_error,
        maj, stride=50, record_bounds_in=None):
    r"""
    TESTS::

        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.naive_sum import *
        sage: Dops, x, Dx = DifferentialOperators()

    Test that we correctly compute solutions of large valuations, and that when
    there are several solutions with very different valuations, we can stop
    before reaching the largest one if the initial values there are zero.
    (Unfortunately, the bounds in this kind of situation are currently so
    pessimistic that this ability rarely helps in practice!) ::

        sage: #dop = (Dx-1).lclm(x*Dx-1000)
        sage: dop = (x^2-1000*x)*Dx^2 + (-x^2+999000)*Dx + 1000*x - 999000
        sage: logger = logging.getLogger('ore_algebra.analytic.naive_sum')
        sage: logger.setLevel(logging.INFO) # TBI
        sage: dop.numerical_transition_matrix([0,1/10000000])
        INFO:ore_algebra.analytic.naive_sum:...
        INFO:ore_algebra.analytic.naive_sum:summed 50 terms, ...
        [ [1.000000100000005...] [1.0000000000000000e-7000...]]
        [ [1.000000100000005...] [1.0000000000000000e-6990...]]
        sage: logger.setLevel(logging.WARNING)
        sage: series_sum(dop, {0: (1,), 1000: (1/1000,)}, 1, 1e-10)
        ([2.719281828...])

    Test that we correctly take into account the errors on terms of polynomials
    that are not represented because they are zero::

        sage: dop = x*Dx^2 + Dx + x
        sage: ini = LogSeriesInitialValues(0, {0: (1, 0)})
        sage: maj = bounds.DiffOpBound(dop, max_effort=0)
        sage: series_sum(dop, ini, QQ(2), 1e-8, stride=1, record_bounds_in=[],
        ....:            maj=maj)
        ([0.2238907...])

    Some simple tests involving large non-integer valuations::

        sage: dop = (x*Dx-1001/2).symmetric_product(Dx-1)
        sage: dop = dop._normalize_base_ring()[-1]
        sage: (exp(CBF(1/2))/RBF(2)^(1001/2)).overlaps(dop.numerical_transition_matrix([0, 1/2], 1e-10)[0,0])
        True
        sage: (exp(CBF(2))/RBF(1/2)^(1001/2)).overlaps(dop.numerical_transition_matrix([0, 2], 1e-10)[0,0])
        True

        sage: dop = (x*Dx+1001/2).symmetric_product(Dx-1)
        sage: dop = dop._normalize_base_ring()[-1]
        sage: (CBF(1/2)^(-1001/2)*exp(CBF(1/2))).overlaps(dop.numerical_transition_matrix([0, 1/2], 1e-10)[0,0])
        True
        sage: (CBF(2)^(-1001/2)*exp(CBF(2))).overlaps(dop.numerical_transition_matrix([0, 2], 1e-10)[0,0])
        True

        sage: h = CBF(1/2)
        sage: #dop = (Dx-1).lclm(x^2*Dx^2 - x*(2*x+1999)*Dx + (x^2 + 1999*x + 1000^2))
        sage: dop = x^2*Dx^3 + (-3*x^2 - 1997*x)*Dx^2 + (3*x^2 + 3994*x + 998001)*Dx - x^2 - 1997*x - 998001
        sage: mat = dop.numerical_transition_matrix([0,1/2], 1e-5) # XXX: long time with the simplified bounds on rational functions
        sage: mat[0,0].overlaps(exp(h)) # long time
        True
        sage: mat[0,1].overlaps(exp(h)*h^1000*log(h)) # long time
        True
        sage: mat[0,2].overlaps(exp(h)*h^1000) # long time
        True

        sage: dop = (x^3 + x^2)*Dx^3 + (-1994*x^2 - 1997*x)*Dx^2 + (994007*x + 998001)*Dx + 998001
        sage: mat = dop.numerical_transition_matrix([0, 1/2], 1e-5)
        sage: mat[0,0].overlaps(1/(1+h))
        True
        sage: mat[0,1].overlaps(h^1000/(1+h)*log(h))
        True
        sage: mat[0,2].overlaps(h^1000/(1+h))
        True
    """

    jet = pt.jet(Intervals)
    Jets = jet.parent()
    ord = pt.jet_order
    jetpow = Jets.one()
    radpow = bounds.IR.one() # bound on abs(pt)^n in the series part (=> starts
                             # at 1 regardless of ini.expo)

    log_prec = sum(len(v) for v in ini.shift.itervalues())
    last_index_with_ini = max([dop.order()]
            + [s for s, vals in ini.shift.iteritems()
                 if not all(v.is_zero() for v in vals)])
    last = collections.deque([vector(Intervals, log_prec)
                              for _ in xrange(bwrec.order + 1)])
    psum = vector(Jets, log_prec)

    # Every few iterations, heuristically check if we have converged and if
    # we still have enough precision. If it looks like the target error may
    # be reached, perform a rigorous check. Our stopping criterion currently
    # (1) only works at “generic” indices, and (2) assumes that the initial
    # values at exceptional indices larger than n are zero, so we also
    # ensure that we are in this case. (Both assumptions could be lifted,
    # (1) by using a slightly more complicated formula for the tail bound,
    # and (2) if we had code to compute lower bounds on coefficients of
    # series expansions of majorants.)
    tail_bound = bounds.IR(infinity)
    bit_prec = Intervals.precision()
    ini_are_accurate = 2*min(pt.accuracy(), ini.accuracy()) > bit_prec

    # TODO: improve the automatic increase of precision for large x^λ:
    # we currently check the series part only (which would sort of make
    # sense in a relative error setting)
    val = [None] # XXX could be more elegant :-)
    def get_bound(resid):
        tb = maj.matrix_sol_tail_bound(n, pt.rad, resid, rows=pt.jet_order)
        tb = tb.abs()
        my_psum = vector(Jets, [[t[i].add_error(tb)
                                for i in range(ord)] for t in psum])
        val[0] = log_series_value(Jets, ord, ini.expo, my_psum, jet[0])
        return max([RBF.zero()] + [_get_error(c) for c in val[0]])
    stopping_criterion = accuracy.StoppingCriterion(
            maj=maj, eps=tgt_error.eps,
            get_residuals=lambda:
                [maj.normalized_residual(n, list(last)[1:], bwrec_nplus)],
            get_bound=get_bound, fast_fail=ini_are_accurate,
            force=(record_bounds_in is not None))

    precomp_len = max(1, bwrec.order) # hack for recurrences of order zero
    bwrec_nplus = collections.deque(
            (bwrec.eval_series(Intervals, i, log_prec)
                for i in xrange(precomp_len)),
            maxlen=precomp_len)
    for n in itertools.count():
        last.rotate(1)
        logger.log(logging.DEBUG - 2, "n = %s, [c(n), c(n-1), ...] = %s", n, list(last))
        logger.log(logging.DEBUG - 1, "n = %s, sum = %s", n, psum)
        mult = len(ini.shift.get(n, ()))

        if n%stride == 0 and n > last_index_with_ini and mult == 0:
            radpowest = abs(jetpow[0])
            est = sum(abs(a) for log_jet in last for a in log_jet) * radpowest
            done, tail_bound = stopping_criterion.check(n, tail_bound, est,
                                                        stride)
            if record_bounds_in is not None:
                # TODO: record all partial sums, not just [log(z)^0]
                # (requires improvements to plot_bounds)
                record_bounds_in.append((n, psum[0], tail_bound))
            if done:
                break

        for p in xrange(log_prec - mult - 1, -1, -1):
            combin  = sum(bwrec_nplus[0][i][j]*last[i][p+j]
                          for j in xrange(log_prec - p)
                          for i in xrange(bwrec.order, 0, -1))
            combin += sum(bwrec_nplus[0][0][j]*last[0][p+j]
                          for j in xrange(mult + 1, log_prec - p))
            last[0][mult + p] = - ~bwrec_nplus[0][0][mult] * combin
        for p in xrange(mult - 1, -1, -1):
            last[0][p] = ini.shift[n][p]
        psum += last[0]*jetpow
        jetpow = jetpow._mul_trunc_(jet, ord)
        radpow *= pt.rad
        bwrec_nplus.append(bwrec.eval_series(Intervals, n+precomp_len, log_prec))
    logger.info("summed %d terms, global tail bound = %s", n, tail_bound)
    result = vector(val[0][i] for i in xrange(ord))
    return result

################################################################################
# Miscellaneous utilities
################################################################################

# Temporary: later on, polynomials with ball coefficients could implement
# add_error themselves.
def _add_error(approx, error):
    if isinstance(approx, polynomial_element.Polynomial):
        return approx[0].add_error(error) + ((approx >> 1) << 1)
    else:
        return approx.add_error(error)

def _get_error(approx):
    if isinstance(approx, polynomial_element.Polynomial):
        return approx[0].abs().rad_as_ball()
    else:
        return approx.abs().rad_as_ball()

def _random_ini(dop):
    import random
    from sage.all import VectorSpace, QQ
    ind = dop.indicial_polynomial(dop.base_ring().gen())
    sl_decomp = my_shiftless_decomposition(ind)
    pol, shifts = random.choice(sl_decomp)
    expo = random.choice(pol.roots(QQbar))[0]
    values = {
        shift: tuple(VectorSpace(QQ, mult).random_element(10))
        for shift, mult in shifts
    }
    return LogSeriesInitialValues(expo, values, dop)

def plot_bounds(dop, ini=None, pt=None, eps=None, **kwds):
    r"""
    EXAMPLES::

        sage: from ore_algebra import *
        sage: from ore_algebra.analytic.naive_sum import *
        sage: Dops, x, Dx = DifferentialOperators()

        sage: plot_bounds(Dx - 1, [CBF(1)], CBF(i)/2, RBF(1e-20))
        Graphics object consisting of 5 graphics primitives

        sage: plot_bounds(x*Dx^3 + 2*Dx^2 + x*Dx, eps=1e-8)
        Graphics object consisting of 5 graphics primitives

        sage: dop = x*Dx^2 + Dx + x
        sage: plot_bounds(dop, eps=1e-8,
        ....:       ini=LogSeriesInitialValues(0, {0: (1, 0)}, dop))
        Graphics object consisting of 5 graphics primitives

        sage: dop = ((x^2 + 10*x + 50)*Dx^10 + (5/9*x^2 + 50/9*x + 155/9)*Dx^9
        ....: + (-10/3*x^2 - 100/3*x - 190/3)*Dx^8 + (30*x^2 + 300*x + 815)*Dx^7
        ....: + (145*x^2 + 1445*x + 3605)*Dx^6 + (5/2*x^2 + 25*x + 115/2)*Dx^5
        ....: + (20*x^2 + 395/2*x + 1975/4)*Dx^4 + (-5*x^2 - 50*x - 130)*Dx^3
        ....: + (5/4*x^2 + 25/2*x + 105/4)*Dx^2 + (-20*x^2 - 195*x - 480)*Dx
        ....: + 5*x - 10)
        sage: plot_bounds(dop, pol_part_len=4, bound_inverse="solve", eps=1e-10) # long time
        Graphics object consisting of 5 graphics primitives
    """
    import sage.plot.all as plot
    from sage.all import VectorSpace, QQ, RIF
    from ore_algebra.analytic.bounds import abs_min_nonzero_root
    if ini is None:
        ini = _random_ini(dop)
    if pt is None:
        rad = abs_min_nonzero_root(dop.leading_coefficient())
        pt = QQ(2) if rad == infinity else RIF(rad/2).simplest_rational()
    if eps is None:
        eps = RBF(1e-50)
    logger.info("point: %s", pt)
    logger.info("initial values: %s", ini)
    recd = []
    maj = bounds.DiffOpBound(dop, max_effort=0, **kwds)
    series_sum(dop, ini, pt, eps, stride=1, record_bounds_in=recd, maj=maj)
    ref_sum = recd[-1][1][0].add_error(recd[-1][2])
    recd[-1:] = []
    # Note: this won't work well when the errors get close to the double
    # precision underflow threshold.
    err = [(psum[0]-ref_sum).abs() for n, psum, _ in recd]
    large = float(1e200) # plot() is not robust to large values
    error_plot_upper = plot.line(
            [(n, v.upper()) for (n, v) in enumerate(err)
                            if abs(float(v.upper())) < large],
            color="lightgray", scale="semilogy")
    error_plot = plot.line(
            [(n, v.lower()) for (n, v) in enumerate(err)
                            if abs(float(v.lower())) < large],
            color="black", scale="semilogy")
    bound_plot_lower = plot.line(
            [(n, bound.lower()) for n, _, bound in recd
                                if abs(float(bound.lower())) < large],
            color="lightblue", scale="semilogy")
    bound_plot = plot.line(
            [(n, bound.upper()) for n, _, bound in recd
                                if abs(float(bound.upper())) < large],
            color="blue", scale="semilogy")
    title = repr(dop) + " @ x=" + repr(pt)
    title = title if len(title) < 80 else title[:77]+"..."
    myplot = error_plot_upper + error_plot + bound_plot_lower + bound_plot
    ymax = myplot.ymax()
    if ymax < float('inf'):
        txt = plot.text(title, (myplot.xmax(), ymax),
                        horizontal_alignment='right', vertical_alignment='top')
        myplot += txt
    return myplot

