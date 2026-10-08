"""Black-Scholes helpers and the HIP-4 implied-volatility solver."""

import math
from statistics import NormalDist

_ND = NormalDist()
N = _ND.cdf
inv_n = _ND.inv_cdf


def phi(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs(S: float, K: float, T: float, sig: float, call: bool) -> float:
    if T <= 0:
        return max(S - K, 0) if call else max(K - S, 0)
    sT = sig * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sT * sT) / sT
    d2 = d1 - sT
    return S * N(d1) - K * N(d2) if call else K * N(-d2) - S * N(-d1)


def delta(S: float, K: float, T: float, sig: float, call: bool) -> float:
    if T <= 0:
        return (1.0 if S > K else 0.0) if call else (-1.0 if S < K else 0.0)
    sT = sig * math.sqrt(T)
    d = N((math.log(S / K) + 0.5 * sT * sT) / sT)
    return d if call else d - 1


def prob_above(S: float, K: float, T: float, sig: float) -> float:
    """Risk-neutral P(S_T >= K), i.e. the fair price of a Yes binary."""
    if T <= 0:
        return 1.0 if S >= K else 0.0
    sT = sig * math.sqrt(T)
    return N((math.log(S / K) - 0.5 * sT * sT) / sT)


def implied_vol_from_binary(p_yes: float, S: float, K: float, T: float) -> float:
    """Solve N(d2) = p for sigma. Below the line d2 rises with sigma up to sigma*sqrt(T) = sqrt(2|ln S/K|)."""
    if T <= 0 or not 0 < p_yes < 1:
        return float("nan")
    z, a, rt = inv_n(p_yes), math.log(S / K), math.sqrt(T)
    rising = a < 0
    d2 = lambda s: (a - 0.5 * s * s * T) / (s * rt)
    lo, hi = 0.02, (min(3.0, math.sqrt(2 * abs(a)) / rt) if rising else 3.0)
    for _ in range(80):
        m = (lo + hi) / 2
        if (d2(m) > z) == rising:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2
