"""Large universe and rotating subscription checks, all synthetic."""
import json
import threading
from types import SimpleNamespace
import pytest
from backend.app.subscriptions import batch, ROTATE_AFTER_SECONDS
from backend.app.streaming import MarketStream
from backend.app.core import DomainError, now
from backend.app.database import Database
from backend.app.providers import UpstoxProvider
from backend.app.autopilot import AutoPaper
from backend.app.core import Policy
from backend.app.paper import PaperBroker


def test_all_2597_keys_are_retained_and_reached():
    universe={f'NSE_EQ|TEST{i}':i for i in range(2597)}
    first,cursor=batch(universe)
    second,_=batch(universe, {0},cursor)
    assert len(first)==len(second)==2000
    assert 0 in second.values()
    assert set(first)|set(second)==set(universe)
    assert len(set(second)-set(first))==597


def test_batch_pins_and_empty_and_invalid():
    u={str(i):i for i in range(7)}
    a,c=batch(u,{0,6},cursor=4,limit=3)
    assert set(a.values())=={0,4,6}
    assert batch({},limit=3)==({},0)
    with pytest.raises(DomainError): batch(u,{99})
    with pytest.raises(DomainError): batch(u,{0,1,2,3},limit=3)


def stream_fixture(db):
    s=MarketStream(db,SimpleNamespace(),threading.RLock())
    s.universe_keys={f'NSE_EQ|TEST{i}':i for i in range(2597)}
    s.keys,s.batch_cursor=batch(s.universe_keys)
    s.state='CONNECTED';s.batch_seconds=ROTATE_AFTER_SECONDS
    sent=[]
    s.socket=SimpleNamespace(send=lambda x:sent.append(json.loads(x)),close=lambda:None)
    return s,sent


def test_rotation_unsubscribes_first_and_discards_only_removed_history(db):
    s,sent=stream_fixture(db)
    removed=1000; retained=10
    # Second batch is IDs 2000..2596 then 0..1402: ID 1500 leaves.
    removed=1500
    s.quotes={removed:{'test':1},retained:{'test':2}}
    s.bars={removed:{},retained:{'kept':True}}
    s.bar_sessions={removed:'old',retained:'old'}
    assert s.rotate_if_due()
    assert [x['method'] for x in sent]==['unsub','sub']
    count=2000
    for x in sent:
        count += len(x['data']['instrumentKeys'])*(1 if x['method']=='sub' else -1)
        assert count<=2000
    assert removed not in s.quotes and removed not in s.bars
    assert s.bars[retained]['kept'] and s.bar_sessions[retained]=='old'
    assert s.bar_sessions[2000]!='old'
    assert s.batch_seconds==0 and s.rotation_count==1
    assert not s.rotate_if_due()


def test_rotation_failure_clears_quotes_and_stops_entries(db):
    s,_=stream_fixture(db)
    calls=[]
    def fail(x): raise OSError('synthetic')
    s.socket=SimpleNamespace(send=fail,close=lambda:calls.append('closed'))
    s.quotes={1:{'test':1}}
    with pytest.raises(DomainError,match='Batch subscription failed'):
        s.rotate_if_due()
    assert s.state=='RECONNECTING' and not s.quotes and calls==['closed']


def test_prepare_large_csv_keeps_all_matches(db,monkeypatch):
    # Synthetic registered CSV identities emulate a one-day file without a 3-year payload.
    n=2597;at=now().isoformat(); master=[]
    with db.connect(write=True) as c:
        ds=c.execute("INSERT INTO datasets(name,sha256,source,created_at,timeframe,rows,warnings) VALUES('TEST','test-large','SYNTHETIC',?,'1d',?,'[]')",(at,n)).lastrowid
        for j in range(n):
            isin=f'IN{j:010d}'
            i=c.execute("INSERT INTO instruments(symbol,kind,lot_size,tick_paise,settlement,metadata_source,created_at) VALUES(?,'SPOT',1,5,'CASH','SYNTHETIC',?)",(f'HIST{j}',at)).lastrowid
            c.execute("INSERT INTO bhav_securities(instrument_id,dataset_id,isin,series,ticker) VALUES(?,?,?,'EQ',?)",(i,ds,isin,f'TEST{j}'))
            master.append(dict(segment='NSE_EQ',instrument_type='EQ',isin=isin,instrument_key='NSE_EQ|'+isin,tick_size=5,lot_size=1,trading_symbol=f'TEST{j}'))
    provider=UpstoxProvider(Policy('licensed_private','SYNTHETIC'))
    monkeypatch.setattr(provider,'get_instruments',lambda:master)
    auto=AutoPaper(db,provider,SimpleNamespace(),threading.RLock())
    result=auto.prepare(ds,5000)
    assert result['matched']==2597 and result['skipped']==[]
    assert len(db.rows('SELECT * FROM auto_universe'))==2597
    # Confirm the actual stream uses 2000, retaining all 2597 for later rotation.
    monkeypatch.setattr(provider,'enabled',lambda:None)
    stream=MarketStream(db,provider,threading.RLock())
    monkeypatch.setattr(stream,'_run',lambda:None)
    ids=[r['instrument_id'] for r in db.rows('SELECT * FROM auto_universe')]
    stream.start(ids,[result['account_id']],universe=True)
    stream.thread.join()
    assert len(stream.keys)==2000 and len(stream.universe_keys)==2597
    assert stream.status()['queued_count']==597
