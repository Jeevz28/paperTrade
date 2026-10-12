from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class InstrumentInput(Strict):
    symbol: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_. |:-]+$")
    kind: Literal["SPOT", "INDEX", "FUTURE", "CALL", "PUT"] = "SPOT"
    lot_size: int = Field(default=1, ge=1, le=1000000)
    tick_size: float = Field(default=0.05, ge=0.01)
    expiry: date | None = None
    strike: float | None = Field(default=None, gt=0)
    underlying: str | None = None
    settlement: Literal["CASH", "PHYSICAL"] = "CASH"
    provider_key: str | None = None
    metadata_source: str = Field(min_length=3, max_length=500)


class RiskConfig(Strict):
    risk_pct: float = Field(default=1, gt=0, le=10)
    daily_loss_pct: float = Field(default=2, gt=0, le=100)
    max_trades: int = Field(default=10, ge=1, le=1000)
    max_positions: int = Field(default=3, ge=1, le=100)
    max_position_pct: float = Field(default=100, gt=0, le=100)


class CostConfig(Strict):
    brokerage_paise: int = Field(default=0, ge=0)
    brokerage_bps: float = Field(default=0, ge=0, le=1000)
    brokerage_cap_paise: int | None = Field(default=None, ge=0)
    exchange_bps: float = Field(default=0, ge=0, le=1000)
    stt_buy_bps: float = Field(default=0, ge=0, le=1000)
    stt_sell_bps: float = Field(default=0, ge=0, le=1000)
    gst_pct: float = Field(default=0, ge=0, le=100)
    stamp_buy_bps: float = Field(default=0, ge=0, le=1000)
    sebi_bps: float = Field(default=0, ge=0, le=1000)
    slippage_bps: float = Field(default=0, ge=0, le=1000)


class AccountInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    initial_capital: float = Field(default=1000, gt=0, le=100000000)
    mode: Literal["REPLAY", "LIVE"] = "REPLAY"
    dataset_id: int | None = None
    risk: RiskConfig = Field(default_factory=RiskConfig)
    costs: CostConfig = Field(default_factory=CostConfig)


class OrderInput(Strict):
    instrument_id: int
    side: Literal["BUY", "SELL"]
    quantity: int = Field(ge=1, le=10000000)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    trailing_distance: float | None = Field(default=None, gt=0)
    strategy: str = Field(default="manual", max_length=100)
    request_key: str = Field(min_length=8, max_length=100)


class BacktestInput(Strict):
    dataset_id: int
    instrument_id: int
    strategy: str = "breakout"
    initial_capital: float = Field(default=1000, gt=0, le=100000000)
    start: str | None = None
    end: str | None = None
    parameters: dict = Field(default_factory=dict)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
