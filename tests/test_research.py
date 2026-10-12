import io
import pandas as pd
import numpy as np
import pytest
from conftest import test_csv as fixture_csv
from backend.app.indicators import compute, regime
from backend.app.strategies import REGISTRY
from backend.app.backtesting import run, metrics
from backend.app.data import import_data, candles, analysis_frame
from backend.app.core import Policy
from backend.app.options import greeks, implied_vol, payoff
from backend.app.risk import size_position
from backend.app.schemas import RiskConfig, CostConfig


def test_every_indicator_prefix_invariant():
    f = pd.read_csv(io.BytesIO(fixture_csv(count=120)))
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    full = compute(f)
    for n in (35, 60, 95):
        prefix = compute(f.iloc[:n])
        pd.testing.assert_frame_equal(full.iloc[:n], prefix)
    changed = f.copy()
    changed.loc[80:, ["open", "high", "low", "close", "volume"]] *= 2
    pd.testing.assert_frame_equal(compute(changed).iloc[:80], full.iloc[:80])


@pytest.mark.parametrize("name", list(REGISTRY))
def test_strategy_no_future_input(name):
    f = pd.read_csv(io.BytesIO(fixture_csv(count=100)))
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    original = compute(f)
    f.loc[80:, "close"] *= 4
    modified = compute(f)
    assert REGISTRY[name].signal(original.iloc[:80]) == REGISTRY[name].signal(
        modified.iloc[:80]
    )
    assert regime(original.iloc[:80]) == regime(modified.iloc[:80])


def test_known_indicators():
    f = pd.read_csv(io.BytesIO(fixture_csv(count=60)))
    f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    f[["open", "high", "low", "close"]] = 100
    out = compute(f)
    assert (
        out.iloc[-1]["sma"] == 100
        and out.iloc[-1]["vwap"] == 100
        and out.iloc[-1]["rsi"] == 50
    )
    assert out.iloc[-1]["atr"] == 0


def test_backtest_repeatable_and_prefix(db, instrument):
    d = import_data(
        db, fixture_csv(count=120), "TEST_ONLY.csv", "5m", "TEST DATA ONLY", Policy()
    )["id"]
    rows = candles(db, instrument, dataset_id=d)
    inst = db.rows("SELECT * FROM instruments")[0]
    a = run(rows, inst, "mean_reversion")
    b = run(rows, inst, "mean_reversion")
    assert a == b
    prefix = run(rows[:90], inst, "mean_reversion")
    assert a["equity_curve"][:90] == prefix["equity_curve"]


def test_greeks_and_iv():
    g = greeks(100, 100, 1, 0.05, 0.2)
    assert g["theoretical_value"] == pytest.approx(10.45058357)
    assert implied_vol(g["theoretical_value"], 100, 100, 1, 0.05) == pytest.approx(0.2)
    assert g["gamma"] > 0 and g["vega_per_vol_point"] > 0


def test_put_call_parity():
    c = greeks(100, 95, 0.5, 0.05, 0.3)["theoretical_value"]
    p = greeks(100, 95, 0.5, 0.05, 0.3, "PUT")["theoretical_value"]
    assert c - p == pytest.approx(100 - 95 * np.exp(-0.05 * 0.5))


def test_small_account_respects_lots():
    assert (
        size_position(
            100000,
            100000,
            10000,
            9800,
            75,
            RiskConfig().model_dump(),
            CostConfig().model_dump(),
        )
        == 0
    )


def test_long_option_payoff():
    x = payoff(
        [{"kind": "CALL", "quantity": 75, "premium": 8, "strike": 100}], [90, 110]
    )
    assert x["scenarios"][0]["payoff"] == -600 and x["scenarios"][1]["payoff"] == 150
