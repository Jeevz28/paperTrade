"""Synthetic F&O fixtures; verify execution mechanics, not strategy profitability."""
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from backend.app.fno import FnoAuto, Setup, NIFTY, contract_master
from backend.app.core import DomainError, Policy
from backend.app.paper import PaperBroker
from backend.app.derivatives import DerivativeBroker
from backend.app.providers import UpstoxProvider
from backend.app.streaming import MarketStream
from backend.app import fno, derivatives, paper, streaming

class Stream:
    def __init__(self):
        self.accounts=[];self.quotes={};self.keys={};self.state='STOPPED';self.bar_sessions={};self.session='synthetic';self.thread=None
    def stop(self): self.state='STOPPED'
    def start(self,ids,accounts):
        self.accounts=accounts;self.keys={str(i):i for i in ids};self.state='CONNECTED'

@pytest.fixture
def rig(db,monkeypatch):
    clock=[datetime(2026,10,12,5,0,tzinfo=timezone.utc)]
    for module in (fno,derivatives,paper,streaming): monkeypatch.setattr(module,'now',lambda:clock[0])
    master=[]
    for key,label in [(NIFTY,'NIFTY'),('NSE_EQ|TESTISIN','TESTSTOCK'),('NSE_INDEX|Nifty Bank','BANK')]:
        for n,(kind,price) in enumerate([('FUT',None),('CE',10000),('PE',10000)]):
            master.append(dict(segment='NSE_FO',instrument_type=kind,underlying_key=key,underlying_symbol=label,
                instrument_key=f'NSE_FO|{label}{n}',trading_symbol=f'{label} {kind} SYNTHETIC',expiry='2026-10-29',lot_size=10,tick_size=5,strike_price=price))
    history=[dict(timestamp=(clock[0]-timedelta(minutes=100-j)).isoformat(),open=10000+j,high=10002+j,low=9999+j,close=10001+j,volume=0) for j in range(90)]
    provider=UpstoxProvider(Policy('licensed_private','SYNTHETIC TEST'))
    monkeypatch.setattr(provider,'enabled',lambda:None)
    monkeypatch.setattr(provider,'get_instruments',lambda:master)
    monkeypatch.setattr(provider,'get_candles',lambda *a,**kw:history)
    monkeypatch.setattr(provider,'get_intraday',lambda *a:[])
    stream=Stream();manager=FnoAuto(db,provider,stream,threading.RLock())
    manager.thread=SimpleNamespace(is_alive=lambda:True)
    a=manager.setup(Setup(initial_capital=100000))['account_id'];manager.start(a)
    def quote(i,price,seconds=1):
        clock[0]+=timedelta(seconds=seconds)
        q=dict(instrument_id=i,timestamp=clock[0].isoformat(),received_at=clock[0].isoformat(),price_paise=price)
        stream.quotes[i]=q
        with db.connect(write=True) as c:
            qid=c.execute("INSERT INTO quotes(instrument_id,timestamp,received_at,price,source,payload) VALUES(?,?,?,?,'UPSTOX_V3','{}')",(i,q['timestamp'],q['received_at'],price)).lastrowid
        manager.tick(q,qid)
        return q,qid
    return manager,stream,a,clock,quote

def queue(rig,product='OPTIONS',direction=1):
    m,stream,a,clock,quote=rig
    with m.db.connect(write=True) as c: c.execute('UPDATE fno_sessions SET product=? WHERE account_id=?',(product,a))
    s=m.session(a)
    stream.quotes[s['underlying_id']]={'price_paise':1000000,'timestamp':clock[0].isoformat()}
    for i in m.db.rows('SELECT i.* FROM instruments i JOIN fno_contracts f ON f.instrument_id=i.id WHERE account_id=?',(a,)):
        stream.quotes[i['id']]={'price_paise':1000000 if i['kind']=='FUTURE' else 1000,'timestamp':clock[0].isoformat()}
    m.queue(s,dict(name='trend',direction=direction,atr=1000,regime='TREND'),(clock[0]-timedelta(minutes=1)).isoformat(),PaperBroker(m.db).snapshot(a))
    return m.db.rows('SELECT * FROM baskets ORDER BY id DESC')[0] if m.db.rows('SELECT 1 FROM baskets') else None

def test_default_index_only_and_separate_stock_account(rig):
    m,stream,a,clock,quote=rig
    assert len(m.db.rows('SELECT * FROM fno_contracts WHERE account_id=?',(a,)))==3
    assert all('NIFTY' in x['symbol'] and 'BANK' not in x['symbol'] for x in m.db.rows('SELECT i.symbol FROM instruments i JOIN fno_contracts c ON i.id=c.instrument_id WHERE account_id=?',(a,)))
    assert m.setup(Setup(initial_capital=5000))=={'account_id':a,'reused':True}
    b=m.setup(Setup(market='STOCK',underlying_key='NSE_EQ|TESTISIN',initial_capital=5000))['account_id'];m.start(b)
    assert set(stream.accounts)=={a,b}
    assert PaperBroker(m.db).snapshot(a)['initial_capital']==10000000
    assert PaperBroker(m.db).snapshot(b)['initial_capital']==500000
    assert all(i['settlement']=='PHYSICAL' for i in m.db.rows('SELECT i.* FROM instruments i JOIN fno_contracts c ON c.instrument_id=i.id WHERE account_id=?',(b,)))
    with pytest.raises(DomainError,match='eligible'):m.setup(Setup(market='STOCK',underlying_key=NIFTY))

def test_full_lot_fresh_quote_entry_target_exit_and_audit(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id']
    assert b['status']=='PENDING' and json.loads(b['legs_json'])[0]['quantity']==10
    assert not PaperBroker(m.db).snapshot(a)['basket_positions']
    quote(i,1000)
    assert m.active(a)[0]['status']=='OPEN'
    assert m.status(a)['orders'][0]['entry_paise']==1000
    quote(i,1500);quote(i,1500)
    assert m.status(a)['orders'][0]['status']=='CLOSED'
    assert m.status(a)['orders'][0]['exit_paise']==1500
    assert PaperBroker(m.db).reconcile(a)['ok']
    evidence=m.db.rows("SELECT payload FROM basket_events WHERE kind='BASKET_OPEN'")[0]
    assert 'quote_id' in json.loads(evidence['payload'])['legs'][0]

def test_loss_cooldown_risk_reduction_and_strategy_exclusion(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id']
    quote(i,1000);quote(i,700);quote(i,700)
    m.scan(m.session(a));s=json.loads(m.session(a)['state'])
    assert s['status']=='LOSS_COOLDOWN' and s['risk_scale']==.5 and 'trend' in s['excluded']

def test_short_future_margin_and_reconciliation(rig):
    m,stream,a,clock,quote=rig;b=queue(rig,'FUTURES',-1);leg=json.loads(b['legs_json'])[0]
    assert leg['quantity']==-10
    quote(leg['instrument_id'],1000000)
    p=PaperBroker(m.db).snapshot(a,{leg['instrument_id']:1000000})['basket_positions'][0]
    assert p['margin']==2500000
    m.close(a);quote(leg['instrument_id'],999000)
    assert m.status(a)['orders'][0]['status']=='CLOSED'
    assert PaperBroker(m.db).reconcile(a)['ok']

def test_small_capital_blocks_instead_of_fractional_lot(rig):
    m,stream,a,clock,quote=rig
    # A separately created small stock account uses the same sizing rules.
    b=m.setup(Setup(market='STOCK',underlying_key='NSE_EQ|TESTISIN',initial_capital=5000))['account_id'];m.start(b)
    small=(m,stream,b,clock,quote)
    assert queue(small) is None
    assert json.loads(m.session(b)['state'])['status']=='BLOCKED'
    assert not m.db.rows('SELECT * FROM basket_positions')

def test_expiry_and_foreign_index_filtered():
    assert contract_master([dict(segment='NSE_FO',underlying_key=NIFTY,instrument_type='CE',expiry='2026-10-12',lot_size=10,tick_size=5,instrument_key='NSE_FO|1',strike_price=100)],NIFTY,'2026-10-12')==[]

def test_untrusted_or_stale_live_observation_cannot_fill(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id']
    with pytest.raises(DomainError,match='fresh stored'):
        DerivativeBroker(m.db).process(a,[dict(id=999,quote_id=999,instrument_id=i,timestamp=clock[0].isoformat(),open=1000)])
    assert m.active(a)[0]['status']=='PENDING'

def test_pause_cancels_entry_but_keeps_open_protection(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id']
    quote(i,1000);m.pause(a);quote(i,700);quote(i,700)
    assert m.status(a)['orders'][0]['status']=='CLOSED' and stream.state=='CONNECTED'

def test_clock_cutoff_queues_exit_without_fake_fill(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id'];quote(i,1000)
    clock[0]=clock[0].replace(hour=9,minute=46);stream.state='STOPPED';stream.quotes={}
    m.scan(m.session(a))
    assert m.active(a)[0]['status']=='CLOSING' and m.status(a)['orders'][0]['exit_paise'] is None
    stream.state='CONNECTED';quote(i,1100)
    assert m.status(a)['orders'][0]['status']=='CLOSED'

def test_standalone_spot_cannot_enter_managed_account(rig):
    m,stream,a,clock,quote=rig
    with pytest.raises(DomainError,match='F&O Auto'):
        PaperBroker(m.db).place_paper_order(a,dict(instrument_id=m.session(a)['underlying_id'],side='BUY',quantity=1,stop_loss=1,request_key='test-standalone'))

def test_restart_cancels_pending_and_preserves_identity(rig):
    m,stream,a,clock,quote=rig;queue(rig)
    new=FnoAuto(m.db,m.provider,stream,m.lock)
    assert new.session(a)['enabled']==0
    assert new.status(a)['orders'][0]['status']=='CANCELLED'
    assert new.setup(Setup())['account_id']==a

def test_index_stream_without_volume_builds_bars(rig):
    m,_,a,clock,_=rig;s=m.session(a);stream=MarketStream(m.db,m.provider,m.lock)
    stream.keys={NIFTY:s['underlying_id']};stream.state='CONNECTED';stream.session='test-index';stream.segments={'NSE_EQ':'NORMAL_OPEN'}
    for _ in range(4):
        clock[0]+=timedelta(minutes=1)
        ts=int(clock[0].timestamp()*1000)
        stream.ingest({'currentTs':ts,'feeds':{NIFTY:{'fullFeed':{'indexFF':{'ltpc':{'ltp':10000,'ltt':ts}}}}}})
    bars=m.db.rows('SELECT * FROM stream_bars WHERE instrument_id=?',(s['underlying_id'],))
    assert len(bars)==2 and all(b['volume']==0 for b in bars)

def test_scan_runs_real_rules_from_history_and_completed_live_bars(rig):
    m,stream,a,clock,quote=rig;s=m.session(a);uid=s['underlying_id']
    stream.quotes[uid]={'price_paise':1010000,'timestamp':clock[0].isoformat()}
    for i in m.db.rows('SELECT instrument_id FROM fno_contracts WHERE account_id=?',(a,)):
        stream.quotes[i['instrument_id']]={'price_paise':1000,'timestamp':clock[0].isoformat()}
    m.scan(s)
    assert json.loads(m.session(a)['state'])['status']=='WARMING_UP'
    with m.db.connect(write=True) as c:
        for n in (2,1):
            at=clock[0]-timedelta(minutes=n)
            p=1010000+(2-n)*100
            c.execute('INSERT INTO stream_bars(instrument_id,session,timestamp,available_at,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?,0)',(uid,stream.session,at.isoformat(),(at+timedelta(minutes=1)).isoformat(),p,p+200,p-100,p+100))
    m.scan(m.session(a))
    assert m.active(a)[0]['status']=='PENDING'
    assert m.status(a)['orders'][0]['signal'] in ('breakout','trend')
    assert PaperBroker(m.db).snapshot(a)['basket_positions']==[]

def test_stale_stored_quote_is_rejected(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id']
    old=clock[0]-timedelta(seconds=11)
    with m.db.connect(write=True) as c:
        qid=c.execute("INSERT INTO quotes(instrument_id,timestamp,received_at,price,source,payload) VALUES(?,?,?,1000,'UPSTOX_V3','{}')",(i,old.isoformat(),old.isoformat())).lastrowid
    with pytest.raises(DomainError,match='fresh stored'):
        DerivativeBroker(m.db).process(a,[dict(id=qid,quote_id=qid,instrument_id=i,timestamp=old.isoformat(),open=1000)])
    assert m.active(a)[0]['status']=='PENDING'

def test_drawdown_restart_restores_protection_without_reenabling_entries(rig,monkeypatch):
    m,stream,a,clock,quote=rig;b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id'];quote(i,1000)
    m.state(a,halted=True);m.pause(a)
    monkeypatch.setattr(m,'prepare',lambda *_:pytest.fail('Existing position protection must not require history download'))
    m.start(a)
    assert m.session(a)['enabled']==0 and stream.state=='CONNECTED'
    quote(i,700);quote(i,700)
    assert m.status(a)['orders'][0]['status']=='CLOSED'

def test_additive_schema_upgrade_preserves_accounts(rig):
    from backend.app.database import Database
    m,stream,a,clock,quote=rig
    with m.db.connect(write=True) as c:c.execute('PRAGMA user_version=6')
    upgraded=Database(m.db.path)
    assert upgraded.rows('PRAGMA user_version')[0]['user_version']==8
    assert PaperBroker(upgraded).snapshot(a)['initial_capital']==10000000
