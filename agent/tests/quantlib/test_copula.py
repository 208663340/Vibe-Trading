"""Tests for Copula models and tail dependence analytics."""

from __future__ import annotations

import numpy as np
import pytest

from src.research.quantlib.copula import (
    clayton_copula_cdf,
    clayton_tail_dependence,
    fit_copula_from_tau,
    frank_copula_cdf,
    gaussian_copula_cdf,
    gumbel_copula_cdf,
    gumbel_tail_dependence,
    pseudo_observations,
)


class TestCopulaAnalytics:
    """Validate copula CDF properties, tail dependencies, and tau calibration."""

    def test_pseudo_observations(self) -> None:
        data = np.array([10.0, 50.0, 20.0, 40.0])
        u = pseudo_observations(data)
        # ranks: 10->1, 20->2, 40->3, 50->4. divided by (4+1=5) -> [0.2, 0.8, 0.4, 0.6]
        assert np.allclose(u, [0.2, 0.8, 0.4, 0.6])

    def test_clayton_copula_properties(self) -> None:
        # C(u, 1) = u, C(1, v) = v
        u = 0.4
        theta = 2.0
        assert clayton_copula_cdf(u, 1.0, theta) == pytest.approx(u)
        assert clayton_copula_cdf(1.0, u, theta) == pytest.approx(u)

        # Tail dependence for theta=2.0 -> 2^{-1/2} = 1/sqrt(2) ~ 0.7071
        tail = clayton_tail_dependence(2.0)
        assert tail["lambda_lower"] == pytest.approx(np.sqrt(0.5))
        assert tail["lambda_upper"] == 0.0

    def test_gumbel_copula_properties(self) -> None:
        u = 0.6
        theta = 1.5
        assert gumbel_copula_cdf(u, 1.0, theta) == pytest.approx(u)

        tail = gumbel_tail_dependence(2.0)
        # lambda_U = 2 - 2^{1/2} = 2 - sqrt(2) ~ 0.5858
        assert tail["lambda_upper"] == pytest.approx(2.0 - np.sqrt(2.0))
        assert tail["lambda_lower"] == 0.0

    def test_frank_copula_properties(self) -> None:
        u = 0.5
        theta = 3.0
        assert frank_copula_cdf(u, 1.0, theta) == pytest.approx(u)

    def test_gaussian_copula_properties(self) -> None:
        u, v = 0.5, 0.5
        # For rho=0, independent copula C(0.5, 0.5) = 0.25
        val = gaussian_copula_cdf(u, v, rho=0.0)
        assert val == pytest.approx(0.25, abs=1e-4)

    def test_fit_copula_from_tau(self) -> None:
        # Clayton: tau = 0.5 -> theta = 2*0.5/(1-0.5) = 2.0
        res_clay = fit_copula_from_tau(0.5, family="clayton")
        assert res_clay["theta"] == pytest.approx(2.0)

        # Gumbel: tau = 0.5 -> theta = 1/(1-0.5) = 2.0
        res_gum = fit_copula_from_tau(0.5, family="gumbel")
        assert res_gum["theta"] == pytest.approx(2.0)

        # Gaussian: tau = 0.5 -> rho = sin(pi/4) ~ 0.7071
        res_gauss = fit_copula_from_tau(0.5, family="gaussian")
        assert res_gauss["rho"] == pytest.approx(np.sin(np.pi / 4))

    def test_frank_copula_is_stable_for_large_theta(self) -> None:
        # theta=80 used to return inf (catastrophic cancellation); the correct
        # value converges to 0.4913356602430007 (verified at 60-digit precision).
        val = frank_copula_cdf(0.5, 0.5, 80.0)
        assert np.isfinite(val)
        assert val == pytest.approx(0.4913356602430007, abs=1e-12)

    def test_clayton_copula_is_stable_for_large_theta(self) -> None:
        # theta=10000 used to return 0.0 (u^{-theta} overflowed to inf).
        val = clayton_copula_cdf(0.5, 0.5, 10000.0)
        assert val == pytest.approx(0.4999653438420768, abs=1e-12)

    def test_gumbel_copula_is_stable_for_large_theta(self) -> None:
        # theta=3000 used to return 1.0 ((-ln u)^theta underflowed to 0.0).
        val = gumbel_copula_cdf(0.5, 0.5, 3000.0)
        assert val == pytest.approx(0.4999199216595084, abs=1e-12)

    def test_archimedean_cdfs_converge_to_min_under_perfect_dependence(self) -> None:
        # Under perfect positive dependence every Archimedean copula converges
        # to min(u, v).
        assert frank_copula_cdf(0.3, 0.7, 200.0) == pytest.approx(0.3, abs=1e-6)
        assert clayton_copula_cdf(0.3, 0.7, 10000.0) == pytest.approx(0.3, abs=1e-6)
        assert gumbel_copula_cdf(0.3, 0.7, 3000.0) == pytest.approx(0.3, abs=1e-6)

    def test_frank_copula_is_stable_for_large_negative_theta(self) -> None:
        # Strong NEGATIVE dependence is the mirror of the large-theta case and
        # was left broken: (e^{|theta|u} - 1)(e^{|theta|v} - 1) overflows to
        # inf from |theta| ~ 355, so the CDF returned inf and then nan.
        # References computed at 2500-digit precision.
        assert frank_copula_cdf(0.5, 0.5, -700.0) == pytest.approx(
            0.000990210257942779, abs=1e-12
        )
        assert frank_copula_cdf(0.99, 0.99, -700.0) == pytest.approx(0.98, abs=1e-12)
        assert frank_copula_cdf(0.5, 0.5, -5000.0) == pytest.approx(
            0.00013862943611198905, abs=1e-12
        )

    def test_frank_copula_approaches_independence_for_tiny_theta(self) -> None:
        # theta -> 0 is the independence copula (C = u*v). The log-space form
        # spelled with plain exp cancelled to ~6 digits there and the 1/theta
        # factor blew that up: C(0.3, 0.7) came out 0.20974, below the Frechet
        # lower bound. Exact value at 400-digit precision: 0.2100000220499994.
        # 1e-8 is the float64 floor here: the 1/theta factor is 1e6, so a
        # last-bit error in the logs lands at ~1e-9. The pre-fix error was
        # 2.6e-4, five orders coarser.
        assert frank_copula_cdf(0.3, 0.7, 1e-6) == pytest.approx(
            0.2100000220499994, abs=1e-8
        )
        assert frank_copula_cdf(0.3, 0.7, -1e-6) == pytest.approx(0.21, abs=1e-6)

    def test_archimedean_cdfs_respect_frechet_bounds(self) -> None:
        # max(u+v-1, 0) <= C(u, v) <= min(u, v) for every copula, at every
        # parameter — the property each numerical failure above violated.
        grid = np.linspace(0.02, 1.0, 15)
        cases = (
            (frank_copula_cdf, [-5000.0, -700.0, -1.0, 1e-6, 5.0, 700.0, 5000.0]),
            (clayton_copula_cdf, [1e-3, 5.0, 1e4, 1e6]),
            (gumbel_copula_cdf, [1.0, 5.0, 3000.0, 1e5]),
        )
        for fn, thetas in cases:
            for theta in thetas:
                for u in grid:
                    for v in grid:
                        val = fn(float(u), float(v), theta)
                        assert np.isfinite(val), (fn.__name__, theta, u, v)
                        assert max(u + v - 1.0, 0.0) - 1e-9 <= val <= min(u, v) + 1e-9, (
                            fn.__name__,
                            theta,
                            u,
                            v,
                            val,
                        )
