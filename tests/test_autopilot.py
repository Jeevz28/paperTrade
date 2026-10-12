"""Synthetic integration fixtures; no claims about actual market profitability."""
import json
import threading
from datetime import timedelta
from types import SimpleNamespace
import pytest
from backend.app.autopilot import AutoPaper, DEFAULT_COSTS
from backend.app.core import Policy, DomainError, now
from backend.app.providers import UpstoxProvider
from backend.app.paper import PaperBroker
from backend.app.risk import charges
from backend.app.strategies import Signal
from backend.app import autopilot
from test_learning import history, ISIN

class Stream:
    def __init__(self):
        self.thread=None; self.state='STOPPED'; self.accounts=[]; self.keys={}; self.quotes={}; self.bar_sessions={}; self.session='test'
    def start(self,ids,accounts,**kwargs):
        self.ids=ids; self.accounts=accounts; self.state='CONNECTED'; self.keys={str(i):i for i in ids}; self.batch_cursor=0
    def rotate_if_due(self): return False
    def stop(self): self.state='STOPPED'
    def status(self): return {'state':self.state}

def setup(db, monkeypatch):
    ds,_=history(db,1)
    provider=UpstoxProvider(Policy('licensed_private','SYNTHETIC TEST'))
    monkeypatch.setattr(provider,'get_instruments',lambda:[dict(segment='NSE_EQ',instrument_type='EQ',isin=ISIN,instrument_key='NSE_EQ|'+ISIN,tick_size=5,lot_size=1,trading_symbol='TEST')])
    stream=Stream()
    auto=AutoPaper(db,provider,stream,threading.RLock())
    account=auto.prepare(ds,5000)['account_id']
    i=db.rows('SELECT instrument_id FROM auto_universe')[0]['instrument_id']
    # Avoid background worker in deterministic software tests.
    auto.thread=SimpleNamespace(is_alive=lambda:True)
    auto.start(account)
    return auto,stream,account,i

def test_percentage_brokerage_cap_and_legacy():
    cfg={'brokerage_bps':5,'brokerage_cap_paise':2000}
    assert charges(100000,'BUY',cfg)==50
    assert charges(10000000,'BUY',cfg)==2000
    assert charges(100000,'BUY',{'brokerage_paise':100})==100

def test_one_day_csv_resolves_and_binds_account(db,monkeypatch):
    auto,stream,a,i=setup(db,monkeypatch)
    assert stream.accounts==[a] and stream.ids==[i]
    inst=db.rows('SELECT * FROM instruments WHERE id=?',(i,))[0]
    assert inst['provider_key']=='NSE_EQ|'+ISIN and inst['tick_paise']==5
    snap=PaperBroker(db).snapshot(a)
    assert snap['initial_capital']==500000
    assert json.loads(snap['cost_json'])==DEFAULT_COSTS
    assert db.rows('SELECT provider_key FROM instruments WHERE id=(SELECT source_id FROM auto_universe)')[0]['provider_key'] is None
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    assert db.rows('SELECT status FROM auto_universe')[0]['status']=='WAITING_FOR_FRESH_PRICE'

def test_unmatched_master_creates_no_account(db,monkeypatch):
    ds,_=history(db,1)
    provider=UpstoxProvider(Policy('licensed_private','SYNTHETIC'))
    monkeypatch.setattr(provider,'get_instruments',lambda:[])
    auto=AutoPaper(db,provider,Stream(),threading.RLock())
    with pytest.raises(DomainError,match='No CSV'):
        auto.prepare(ds,5000)
    assert db.rows('SELECT * FROM paper_accounts')==[]

def bars(db,stream,i):
    end=now().replace(second=0,microsecond=0)
    with db.connect(write=True) as c:
        for n in range(200):
            at=end-timedelta(minutes=200-n)
            c.execute('INSERT INTO stream_bars(instrument_id,session,timestamp,available_at,open,high,low,close,volume) VALUES(?,?,?,?,10000,10100,9900,10000,1000)',(i,'test',at.isoformat(),(at+timedelta(minutes=1)).isoformat()))
    stream.quotes[i]={'instrument_id':i,'price_paise':10000,'timestamp':now().isoformat()}

def test_scanner_queues_one_order_and_pause_cancels(db,monkeypatch):
    auto,stream,a,i=setup(db,monkeypatch); bars(db,stream,i)
    monkeypatch.setattr(autopilot,'rank',lambda *args:[{'eligible':True,'strategy':'trend'}])
    monkeypatch.setattr(autopilot.REGISTRY['trend'],'signal',lambda *args:Signal('trend','LONG',100,98,104,'TEST','TREND',now().isoformat()))
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    orders=db.rows('SELECT * FROM paper_orders')
    assert len(orders)==1 and orders[0]['quantity']>0
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    assert len(db.rows('SELECT * FROM paper_orders'))==1
    auto.pause(a)
    assert db.rows('SELECT status FROM paper_orders')[0]['status']=='CANCELLED'
    assert stream.state=='CONNECTED'

def test_unaffordable_and_no_qualified_strategy_wait(db,monkeypatch):
    auto,stream,a,i=setup(db,monkeypatch); bars(db,stream,i)
    stream.quotes[i]['price_paise']=600000
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    assert db.rows('SELECT status FROM auto_universe')[0]['status']=='UNAFFORDABLE'
    stream.quotes[i]['price_paise']=10000
    monkeypatch.setattr(autopilot,'rank',lambda *args:[])
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    assert db.rows('SELECT status FROM auto_universe')[0]['status']=='NO_QUALIFYING_STRATEGY'
    assert db.rows('SELECT * FROM paper_orders')==[]

def test_fill_loss_cooldown_and_restart_preserve_risk_state(db,monkeypatch):
    auto,stream,a,i=setup(db,monkeypatch); bars(db,stream,i)
    monkeypatch.setattr(autopilot,'rank',lambda *args:[{'eligible':True,'strategy':'trend'}])
    monkeypatch.setattr(autopilot.REGISTRY['trend'],'signal',lambda *args:Signal('trend','LONG',100,98,104,'TEST','TREND',now().isoformat()))
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    b=PaperBroker(db)
    at=(now()+timedelta(seconds=1)).isoformat()
    b.execute_observation(a,i,10000,at,'TEST_ONLY',{i:10000})
    assert b.snapshot(a,{i:10000})['positions']
    at=(now()+timedelta(seconds=2)).isoformat()
    b.protective_orders(a,i,9800,at)
    b.execute_observation(a,i,9800,at,'TEST_ONLY_LOSS',{i:9800})
    assert db.rows('SELECT net_pnl FROM paper_trades')[0]['net_pnl']<0
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    s=json.loads(db.rows('SELECT state FROM auto_sessions')[0]['state'])
    assert s['risk_scale']==.5 and 'trend' in s['excluded']
    assert db.rows('SELECT status FROM auto_universe')[0]['status']=='LOSS_COOLDOWN'
    AutoPaper(db,auto.provider,stream,threading.RLock())
    saved=db.rows('SELECT * FROM auto_sessions')[0]
    assert saved['enabled']==0 and json.loads(saved['state'])['risk_scale']==.5

def test_drawdown_halt_cannot_be_reset_by_start(db,monkeypatch):
    auto,stream,a,i=setup(db,monkeypatch); bars(db,stream,i)
    with db.connect(write=True) as c:
        c.execute('UPDATE auto_sessions SET state=?',(json.dumps({'peak':600000}),))
    auto.scan(db.rows('SELECT * FROM auto_sessions')[0])
    assert db.rows('SELECT enabled FROM auto_sessions')[0]['enabled']==0
    with pytest.raises(DomainError,match='drawdown'):
        auto.start(a)
