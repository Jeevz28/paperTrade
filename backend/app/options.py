import math
from scipy.stats import norm
from scipy.optimize import brentq
from .core import DomainError


def greeks(spot, strike, years, rate, vol, kind="CALL", dividend=0):
    if (
        kind not in ("CALL", "PUT")
        or not all(math.isfinite(x) for x in (spot, strike, years, rate, vol, dividend))
        or min(spot, strike, years, vol) <= 0
    ):
        raise DomainError(
            "Positive spot, strike, time and volatility required; type CALL or PUT"
        )
    d1 = (math.log(spot / strike) + (rate - dividend + vol * vol / 2) * years) / (
        vol * math.sqrt(years)
    )
    d2 = d1 - vol * math.sqrt(years)
    sign = 1 if kind == "CALL" else -1
    dq = math.exp(-dividend * years)
    dr = math.exp(-rate * years)
    price = sign * (spot * dq * norm.cdf(sign * d1) - strike * dr * norm.cdf(sign * d2))
    theta = (
        -spot * dq * norm.pdf(d1) * vol / (2 * math.sqrt(years))
        - sign * rate * strike * dr * norm.cdf(sign * d2)
        + sign * dividend * spot * dq * norm.cdf(sign * d1)
    )
    return {
        "theoretical_value": float(price),
        "delta": float(sign * dq * norm.cdf(sign * d1)),
        "gamma": float(dq * norm.pdf(d1) / (spot * vol * math.sqrt(years))),
        "theta_per_day": float(theta / 365),
        "vega_per_vol_point": float(spot * dq * norm.pdf(d1) * math.sqrt(years) / 100),
        "label": "Black-Scholes analytical estimate, NOT a market quote or execution source",
    }


def implied_vol(premium, spot, strike, years, rate, kind="CALL"):
    try:
        return brentq(
            lambda v: greeks(spot, strike, years, rate, v, kind)["theoretical_value"]
            - premium,
            0.00001,
            5,
        )
    except ValueError as e:
        raise DomainError("No valid implied volatility within model bounds") from e


def chain_statistics(chain):
    if not chain:
        raise DomainError("INSUFFICIENT OPTION DATA")
    calls = sum(x["call_oi"] for x in chain)
    puts = sum(x["put_oi"] for x in chain)
    pain = {
        r["strike"]: sum(
            max(0, r["strike"] - x["strike"]) * x["call_oi"]
            + max(0, x["strike"] - r["strike"]) * x["put_oi"]
            for x in chain
        )
        for r in chain
    }
    return {
        "pcr": puts / calls if calls else None,
        "max_pain_strike": min(pain, key=pain.get),
        "meaning": "OI-weighted analytical statistic; not a forecast",
    }


def payoff(legs, spots):
    if not 1 <= len(legs) <= 4:
        raise DomainError("Provide 1 to 4 explicitly specified legs")
    out = []
    for spot in spots:
        if not math.isfinite(spot) or spot < 0:
            raise DomainError("Underlying scenarios must be finite and nonnegative")
        value = 0
        for leg in legs:
            kind = leg["kind"]
            qty = leg["quantity"]
            premium = leg["premium"]
            intrinsic = (
                spot
                if kind == "SPOT"
                else (
                    max(spot - leg["strike"], 0)
                    if kind == "CALL"
                    else max(leg["strike"] - spot, 0) if kind == "PUT" else None
                )
            )
            if (
                intrinsic is None
                or not all(math.isfinite(v) for v in (spot, qty, premium))
                or premium < 0
            ):
                raise DomainError("Invalid payoff leg")
            value += (intrinsic - premium) * qty
        out.append({"underlying_scenario": spot, "payoff": value})
    return {
        "scenarios": out,
        "label": "Hypothetical expiry payoff only; not market data, not execution, excludes fees and physical delivery. Negative quantities are analytics-only.",
    }
