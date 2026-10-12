"""Explicit synthetic software fixtures, never claimed as profitable market data."""
import csv
import io
import json
from datetime import date, datetime, timedelta
import pytest
from backend.app import learning
from backend.app.bhavcopy import import_bhavcopy, daily_times, REQUIRED
from backend.app.core import DomainError, Policy, timestamp
from backend.app.data import create_instrument
from backend.app.paper import PaperBroker
from backend.app.automation import Automation
from backend.app.schemas import RiskConfig, CostConfig
from backend.app.strategies import REGISTRY, Signal

ISIN = "INE000A01010"


def csv_data(n=610, extra=None):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=sorted(REQUIRED))
    w.writeheader()
    for i in range(n):
        r = dict.fromkeys(REQUIRED, "")
        r.update(TradDt=(date(2022,1,1)+timedelta(days=i)).isoformat(), Sgmt="CM", Src="NSE", FinInstrmTp="STK",
                 FinInstrmId="1", ISIN=ISIN, TckrSymb="SYNTHETIC_TEST", SctySrs="EQ", OpnPric="100", HghPric="101", LwPric="99", ClsPric="100", TtlTradgVol="1000")
        if extra:
            r.update(extra(i))
        w.writerow(r)
    return out.getvalue().encode()


def history(db, n=610):
    result = import_bhavcopy(db, io.BytesIO(csv_data(n)), "TEST_ONLY.csv", "SYNTHETIC TEST ONLY", Policy())
    inst = db.rows("SELECT instrument_id FROM bhav_securities WHERE dataset_id=?", (result["id"],))[0]["instrument_id"]
    return result["id"], inst


def config(ds, inst):
    return dict(dataset_id=ds, instrument_id=inst, initial_capital_paise=100000, risk=RiskConfig().model_dump(),
                costs=CostConfig(brokerage_paise=1,slippage_bps=1).model_dump(), max_quantity=4, train_until=None)


def saved_profile(db, ds, inst, through=None, approved=True):
    cfg = config(ds, inst)
    rows = db.rows("SELECT * FROM candles WHERE dataset_id=? ORDER BY timestamp", (ds,))
    through = through or rows[-2]["available_at"]
    m = dict(total_trades=8, closed_trade_net_pnl_paise=1000, net_pnl_paise=1000,max_drawdown_pct=1,profit_factor=2)
    evidence = dict(approved=approved, trained_through=through, isin=ISIN, ticker="SYNTHETIC_TEST",series="EQ", config=cfg,
                    winners=[dict(regime="SIDEWAYS",strategy="trend",atr_stop=1.5,approved=True,validation_score=100,
                                  holdout=m,stress=m,regime_holdout={"trades":8,"net_pnl_paise":1000},reason="SYNTHETIC TEST")])
    with db.connect(write=True) as c:
        job = c.execute("INSERT INTO learning_jobs(created_at,status,parameters) VALUES(?,'COMPLETED','{}')",(through,)).lastrowid
        pid = c.execute("INSERT INTO learning_profiles(job_id,dataset_id,instrument_id,created_at,trained_through,payload) VALUES(?,?,?,?,?,?)",(job,ds,inst,through,through,json.dumps(evidence))).lastrowid
    return pid, evidence, rows


def test_nse_import_daily_identity_and_rollback(db):
    ds, inst = history(db, 3)
    rows = db.rows("SELECT * FROM candles ORDER BY timestamp")
    assert len(rows) == 3
    assert timestamp(rows[0]["available_at"]) > timestamp(rows[0]["timestamp"])
    assert rows[0]["available_at"] == "2022-01-01T18:30:00+00:00"
    assert db.rows("SELECT * FROM instruments")[0]["provider_key"] is None
    with pytest.raises(DomainError, match="Duplicate"):
        history(db, 3)
    assert len(db.rows("SELECT * FROM datasets")) == 1
    # A duplicate late in a batch must roll back every inserted row/instrument.
    raw = csv_data(2002, lambda i: {"TradDt": (date(2022,1,1)+timedelta(days=i//2 if i<2001 else 0)).isoformat(), "ISIN": ISIN if i%2==0 or i==2001 else "INE000A01011"})
    with pytest.raises(DomainError, match="repeated"):
        import_bhavcopy(db,io.BytesIO(raw),"duplicate.csv","TEST",Policy())
    assert len(db.rows("SELECT * FROM candles")) == 3


def test_nse_excluded_series_reported_and_price_validation(db):
    r = import_bhavcopy(db,io.BytesIO(csv_data(2,lambda i:{"SctySrs":"BE"} if i else {})),"series.csv","TEST",Policy())
    assert r["rows"] == 1 and r["excluded"] == 1
    with pytest.raises(DomainError,match="OHLC"):
        import_bhavcopy(db,io.BytesIO(csv_data(2,lambda i:{"HghPric":"90"} if i else {})),"bad.csv","TEST",Policy())
    assert len(db.rows("SELECT * FROM candles")) == 1


def test_learning_requires_history_and_nonzero_costs(db):
    ds, inst = history(db, 10)
    with pytest.raises(DomainError,match="600"):
        learning.study(db,config(ds,inst),Policy())
    ds, inst = history(db, 610)
    cfg = config(ds,inst)
    cfg["costs"] = CostConfig().model_dump()
    with pytest.raises(DomainError,match="slippage"):
        learning.study(db,cfg,Policy())


def test_real_study_no_invented_profits_and_disjoint_windows(db):
    ds, inst = history(db)
    result = learning.study(db,config(ds,inst),Policy())
    assert not result["approved"]
    assert len(result["candidates"]) == 2 * len(learning.DAILY_STRATEGIES)
    windows = result["windows"]
    assert windows[0]["end"] < windows[1]["start"]
    assert windows[1]["end"] < windows[2]["start"]
    assert not db.rows("SELECT * FROM paper_orders")


def test_holdout_failure_cannot_promote_another_unselected_candidate(db,monkeypatch):
    ds, inst = history(db)
    monkeypatch.setattr(learning,"DAILY_STRATEGIES",("trend","pullback","momentum"))
    calls=[]
    def fake_run(rows, instrument, name, initial, risk, costs, params, **kwargs):
        is_holdout = rows[0]["timestamp"] >= db.rows("SELECT timestamp FROM candles ORDER BY timestamp LIMIT 1 OFFSET 457")[0]["timestamp"]
        calls.append((name,is_holdout))
        pnl = -10 if is_holdout else {"trend":30,"pullback":20,"momentum":10}[name]
        return {"metrics":{"total_trades":6,"closed_trade_net_pnl_paise":pnl*6,"net_pnl_paise":pnl*6,"max_drawdown_pct":1,"profit_factor":2 if pnl>0 else .5},
                "trades":[{"regime":"SIDEWAYS","net_pnl":pnl}]*6}
    monkeypatch.setattr(learning,"run",fake_run)
    result=learning.study(db,config(ds,inst),Policy())
    assert not result["approved"]
    assert {w["strategy"] for w in result["winners"]}=={"trend","pullback"}
    assert ("momentum",True) not in calls


def test_profile_identity_risk_freshness_and_replay_time_gates(db):
    ds, inst = history(db)
    pid, evidence, rows = saved_profile(db,ds,inst)
    b=PaperBroker(db)
    aid=b.create_account({"name":"TEST","dataset_id":ds,"costs":evidence["config"]["costs"]})
    a=b.snapshot(aid)
    rule={"instrument_id":inst,"parameters":json.dumps({"learning_profile_id":pid}),"max_quantity":4}
    with pytest.raises(DomainError,match="before its holdout"):
        learning.daily_context(db,rule,a,rows[-2]["available_at"])
    p, visible=learning.daily_context(db,rule,a,rows[-1]["available_at"])
    assert all(r["available_at"]<=rows[-1]["available_at"] for r in visible)
    with pytest.raises(DomainError,match="90 days"):
        learning.daily_context(db,rule,a,(timestamp(rows[-1]["available_at"])+timedelta(days=91)).isoformat())
    a["cost_json"]="{}"
    with pytest.raises(DomainError,match="costs"):
        learning.daily_context(db,rule,a,rows[-1]["available_at"])


def test_learned_live_uses_daily_signal_fresh_fill_and_loss_controls(db,monkeypatch):
    ds, source=history(db)
    pid,evidence,rows=saved_profile(db,ds,source)
    at=timestamp(rows[-1]["available_at"])+timedelta(hours=10)
    monkeypatch.setattr("backend.app.paper.now",lambda:at)
    inst=create_instrument(db,{"symbol":"SYNTHETIC_LIVE","provider_key":"NSE_EQ|"+ISIN,"tick_size":.01,"metadata_source":"SYNTHETIC TEST"})
    b=PaperBroker(db)
    aid=b.create_account({"name":"TEST","mode":"LIVE","costs":evidence["config"]["costs"]})
    auto=Automation(db)
    auto.configure(aid,inst,"learned_daily",4,{"learning_profile_id":pid,"atr_stop":1.5})
    with db.connect(write=True) as c:
        c.execute("INSERT INTO learning_daily_cache VALUES(?,?,?)",(pid,at.isoformat(),"[]"))
    class Always:
        def signal(self,frame,p):
            assert len(frame)==300 and p["atr_stop"]==1.5
            return Signal("trend","LONG",100,99,103,"TEST","SIDEWAYS",str(frame.iloc[-1]["timestamp"]))
    monkeypatch.setitem(REGISTRY,"trend",Always())
    monkeypatch.setattr(learning,"learned_rank",lambda p,r,e:([{"strategy":"trend","atr_stop":1.5,"eligible":"trend" not in e,"score":1}],{"regime":"SIDEWAYS","reasons":[]}))
    minute=[{"available_at":at.isoformat()}]
    auto.evaluate(aid,minute,{inst:10000})
    auto.evaluate(aid,minute,{inst:10000})
    orders=db.rows("SELECT * FROM paper_orders")
    assert len(orders)==1 and orders[0]["status"]=="PENDING" and ":daily:" in orders[0]["request_key"]
    at+=timedelta(seconds=1)
    b.execute_observation(aid,inst,10000,at.isoformat(),"SYNTHETIC TEST",{inst:10000})
    assert b.snapshot(aid,{inst:10000})["positions"]
    at+=timedelta(minutes=1)
    b.protective_orders(aid,inst,9800,at.isoformat())
    b.execute_observation(aid,inst,9800,at.isoformat(),"SYNTHETIC EXIT",{inst:9800})
    auto.evaluate(aid,[{"available_at":at.isoformat()}],{inst:9800})
    state=json.loads(db.rows("SELECT payload FROM adaptive_state")[0]["payload"])
    assert state["status"]=="LOSS_COOLDOWN" and state["quantity_cap"]==2
    assert b.reconcile(aid)["ok"]


def test_expired_daily_order_never_fills(db,instrument,monkeypatch):
    at=timestamp("2024-01-02T04:00:00Z")
    monkeypatch.setattr("backend.app.paper.now",lambda:at)
    b=PaperBroker(db)
    aid=b.create_account({"name":"TEST","mode":"LIVE"})
    b.place_paper_order(aid,{"instrument_id":instrument,"side":"BUY","quantity":1,"stop_loss":99,"request_key":"auto:1:daily:TEST"})
    b.execute_observation(aid,instrument,10000,(at+timedelta(seconds=61)).isoformat(),"TEST",{instrument:10000})
    assert db.rows("SELECT status FROM paper_orders")[0]["status"]=="REJECTED"
    assert not b.snapshot(aid)["positions"]


def test_provider_refresh_failure_does_not_create_verified_cache(db,monkeypatch):
    ds,inst=history(db)
    pid,evidence,rows=saved_profile(db,ds,inst)
    at=timestamp(rows[-1]["available_at"])+timedelta(hours=10)
    monkeypatch.setattr(learning,"now",lambda:at)
    class Provider:
        def get_candles(self,**kwargs):
            return [{"timestamp":rows[-1]["timestamp"],"open":200,"high":201,"low":199,"close":200,"volume":1000}]
    jobs=learning.LearningJobs(db,Policy("licensed_private","SYNTHETIC TEST"),Provider())
    try:
        jobs._refresh(pid)
        assert not db.rows("SELECT * FROM learning_daily_cache")
        assert "disagree" in db.rows("SELECT details FROM activity_events WHERE action='DAILY_REFRESH_BLOCKED'")[0]["details"]
    finally:
        jobs.shutdown()
