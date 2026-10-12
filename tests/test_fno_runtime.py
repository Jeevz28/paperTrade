import json
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.core import Policy,DomainError
from backend.app.fno_runtime import FnoRuntime
from backend.app.paper import PaperBroker
from test_fno_auto import rig,queue


def test_env_capital_creates_once_and_mismatch_blocks(rig,monkeypatch):
    m,stream,a,clock,quote=rig
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL','100000')
    r=FnoRuntime(m)
    assert r.configure()==a
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL','5000')
    with pytest.raises(DomainError,match='Existing account'):r.configure()
    assert r.info['capital_mismatch'] and PaperBroker(m.db).snapshot(a)['initial_capital']==10000000
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL','NaN')
    with pytest.raises(DomainError,match='PAPER_INITIAL_CAPITAL'):r.configure()


def test_cash_premium_cost_and_reservation_reconcile(rig):
    m,stream,a,clock,quote=rig;b=queue(rig)
    before=m.balances(a)
    assert before['pending_reserved_paise']>0 and before['cash_paise']==10000000
    i=json.loads(b['legs_json'])[0]['instrument_id'];quote(i,1000)
    balances=m.balances(a)
    assert balances['premium_in_positions_paise']==10000
    assert balances['cash_paise']==10000000-10000-balances['costs_paise']
    assert balances['pending_reserved_paise']==0
    assert balances['available_cash_paise']==balances['cash_paise']
    assert PaperBroker(m.db).reconcile(a)['ok']


def test_persisted_chart_and_contract_levels(rig):
    m,stream,a,clock,quote=rig
    assert len(m.db.rows('SELECT * FROM market_history'))>0
    assert len(m.chart(a)['bars'])>0
    b=queue(rig);i=json.loads(b['legs_json'])[0]['instrument_id'];quote(i,1000)
    assert m.chart(a,i)['levels']['entry_paise']==1000
    assert m.chart(a)['levels']=={}
    with pytest.raises(DomainError,match='outside'):m.chart(a,999999)


def test_default_api_exposes_only_fno_and_health(tmp_path):
    app=create_app(tmp_path/'focused.db',Policy(),autostart=False)
    with TestClient(app) as c:
        assert c.get('/api/fno-auto').status_code==200
        assert c.get('/api/fno-auto/chart').status_code==200
        assert c.get('/api/datasets').status_code==404
        assert c.get('/api/accounts').status_code==404
        assert c.post('/api/fno-auto/setup',headers={'X-PaperLab':'local'},json={'initial_capital':9999}).status_code==404
        paths=c.get('/openapi.json').json()['paths']
        assert '/api/fno-auto/setup' not in paths


def test_pause_persists_and_retains_recording(rig,monkeypatch):
    m,stream,a,clock,quote=rig;r=FnoRuntime(m)
    r.pause(a)
    assert r.paused() and stream.state=='CONNECTED'
    assert FnoRuntime(m).paused()
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL','100000')
    r.resume(a)
    assert not r.paused() and m.session(a)['enabled']==1


def test_strategy_changes_option_stop_and_target(rig):
    m,stream,a,clock,quote=rig;b=queue(rig);m.pause(a)
    # Existing helper establishes fresh quotes; invoke another strategy next minute.
    from datetime import timedelta
    clock[0]+=timedelta(minutes=1)
    for q in stream.quotes.values():q['timestamp']=clock[0].isoformat()
    with m.db.connect(write=True) as c:c.execute('UPDATE fno_sessions SET enabled=1 WHERE account_id=?',(a,))
    m.queue(m.session(a),dict(name='range_reversal',direction=1,atr=1000,regime='RANGE',stop_factor=1.0,target_rr=1.5),clock[0].isoformat(),PaperBroker(m.db).snapshot(a))
    ticket=m.db.rows('SELECT * FROM fno_tickets ORDER BY basket_id DESC')[0]
    assert ticket['stop_paise']==850 and ticket['target_paise']==1225
    ctx=m.db.rows('SELECT payload FROM decision_context WHERE basket_id=?',(ticket['basket_id'],))[0]
    assert 'stop_basis' in ctx['payload']

def test_runtime_starts_automatically_from_env_without_ui(rig,monkeypatch):
    m,stream,a,clock,quote=rig
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL','100000')
    called=[]
    monkeypatch.setattr(m,'start',lambda account:called.append(account))
    runtime=FnoRuntime(m)
    runtime.loop()
    assert called==[a] and runtime.info['status']=='RUNNING'
    assert runtime.info['configured_capital_paise']==10000000


def test_shutdown_prevents_late_stream_restart(rig):
    m,stream,a,clock,quote=rig
    m.shutting_down=True
    with pytest.raises(DomainError,match='stopping'):m.connect(a)

def test_real_app_lifespan_creates_environment_account_even_when_feed_not_configured(tmp_path,monkeypatch):
    import time
    monkeypatch.setenv('PAPER_INITIAL_CAPITAL','5000')
    monkeypatch.setenv('UPSTOX_ENABLED','false')
    app=create_app(tmp_path/'boot.db',Policy())
    with TestClient(app) as c:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            data=c.get('/api/fno-auto').json()
            if data.get('balances') and data['runtime']['status']=='CONFIGURATION_REQUIRED':break
            time.sleep(.05)
        assert data['balances']['initial_capital_paise']==500000
        assert data['runtime']['status']=='CONFIGURATION_REQUIRED'
        assert data['orders']==[]
