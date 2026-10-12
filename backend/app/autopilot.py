"""CSV universe discovery and one shared-capital, read-only-data paper scanner."""
import json
import threading
from datetime import timedelta
from fastapi import APIRouter
from pydantic import Field
from .schemas import Strict, CostConfig, RiskConfig
from .core import DomainError, now, timestamp, money
from .paper import PaperBroker
from .risk import size_position
from .adaptive import rank
from .strategies import REGISTRY
from .data import analysis_frame
from .indicators import compute
from .activity import record

# Explicit simulation assumptions, not an exact broker invoice/tax calculator.
DEFAULT_COSTS = CostConfig(brokerage_bps=5, brokerage_cap_paise=2000,
    exchange_bps=0.3, stt_buy_bps=10, stt_sell_bps=10, gst_pct=18,
    stamp_buy_bps=1.5, sebi_bps=0.01, slippage_bps=5).model_dump()

class Setup(Strict):
    dataset_id: int
    initial_capital: float = Field(default=5000, ge=1, le=100000000)

class AutoPaper:
    def __init__(self, db, provider, stream, lock):
        self.db, self.provider, self.stream, self.lock = db, provider, stream, lock
        self.stop_event = threading.Event()
        self.cache = {}
        with db.connect(write=True) as c:
            c.execute('UPDATE auto_sessions SET enabled=0')
            c.execute("UPDATE paper_orders SET status='CANCELLED',reason='Auto Paper stopped on restart' WHERE status='PENDING' AND request_key LIKE 'auto:scan:%'")
        self.thread = None

    def prepare(self, dataset_id, capital):
        self.provider.policy.require_live()
        source = self.db.rows('SELECT * FROM bhav_securities WHERE dataset_id=?', (dataset_id,))
        if not source:
            raise DomainError('Import an NSE bhavcopy CSV first (one day or combined history).')
        master = self.provider.get_instruments()
        matches = {}
        for x in master:
            if x.get('segment') == 'NSE_EQ' and x.get('instrument_type') == 'EQ':
                matches.setdefault(x.get('isin'), []).append(x)
        resolved, skipped = [], []
        for x in source:
            found = matches.get(x['isin'], [])
            if len(found) != 1:
                skipped.append({'symbol': x['ticker'], 'reason': 'No unique current Upstox EQ match'})
                continue
            m = found[0]
            try:
                key = self.provider.key(m['instrument_key'])
                tick, lot = int(m['tick_size']), int(m['lot_size'])
                if key != 'NSE_EQ|' + x['isin'] or tick < 1 or lot < 1 or tick != float(m['tick_size']):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                skipped.append({'symbol': x['ticker'], 'reason': 'Invalid master metadata'})
                continue
            resolved.append((x, m, key, tick, lot))
        if not resolved:
            raise DomainError('No CSV securities matched the current Upstox equity master.')
        with self.lock:
            if self.db.rows('SELECT 1 FROM auto_sessions WHERE enabled=1'):
                raise DomainError('Pause the current Auto Paper account before preparing another.')
            broker = PaperBroker(self.db)
            account = broker.create_account(dict(name='Auto Paper ' + now().strftime('%d %b %H:%M'),
                initial_capital=capital, mode='LIVE', costs=DEFAULT_COSTS,
                risk=RiskConfig(max_positions=1, max_trades=10).model_dump()))
            with self.db.connect(write=True) as c:
                c.execute('INSERT INTO auto_sessions(account_id,dataset_id,state,created_at) VALUES(?,?,?,?)',
                    (account, dataset_id, json.dumps({'status':'READY','skipped':skipped,'peak':money(capital),'last_trade':0,'risk_scale':1,'excluded':{}}),now().isoformat()))
                for src, m, key, tick, lot in resolved:
                    # Isolated live symbols never overwrite historical instrument metadata.
                    symbol = 'AUTO:' + src['isin']
                    c.execute("INSERT INTO instruments(symbol,kind,lot_size,tick_paise,settlement,provider_key,metadata_source,created_at) VALUES(?,'SPOT',?,?,'CASH',?,?,?) ON CONFLICT(symbol) DO UPDATE SET lot_size=excluded.lot_size,tick_paise=excluded.tick_paise,provider_key=excluded.provider_key,metadata_source=excluded.metadata_source",
                        (symbol,lot,tick,key,'Upstox NSE master '+now().date().isoformat()+' | '+m.get('trading_symbol',src['ticker']),now().isoformat()))
                    inst = c.execute('SELECT id FROM instruments WHERE symbol=?',(symbol,)).fetchone()[0]
                    c.execute('INSERT INTO auto_universe(account_id,instrument_id,source_id,detail) VALUES(?,?,?,?)',
                        (account,inst,src['instrument_id'],json.dumps({'symbol':m.get('trading_symbol',src['ticker'])})))
            return {'account_id':account, 'matched':len(resolved), 'skipped':skipped}

    def start(self, account):
        if self.db.rows('SELECT 1 FROM fno_sessions WHERE enabled=1'):
            raise DomainError('Pause managed F&O entries before switching automation')
        sessions = self.db.rows('SELECT * FROM auto_sessions WHERE account_id=?',(account,))
        if not sessions:
            raise DomainError('Prepare an Auto Paper account first.')
        if json.loads(sessions[0]['state']).get('halted'):
            raise DomainError('This account reached its drawdown stop; entries remain disabled.')
        ids = [x['instrument_id'] for x in self.db.rows('SELECT instrument_id FROM auto_universe WHERE account_id=?',(account,))]
        reuse = self.stream.state == 'CONNECTED' and self.stream.accounts == [account] and set(ids) == set((getattr(self.stream,"universe_keys",None) or self.stream.keys).values())
        # Replace an idle/manual data-only stream without requiring account binding steps.
        if not reuse and self.stream.thread and self.stream.thread.is_alive():
            for bound in self.stream.accounts:
                snap = PaperBroker(self.db).snapshot(bound)
                if bound != account and (snap['positions'] or snap['basket_positions'] or self.db.rows("SELECT 1 FROM paper_orders WHERE account_id=? AND status='PENDING'",(bound,))):
                    raise DomainError('Another streamed account has open exposure. Close it before switching accounts.')
            if self.db.rows('SELECT 1 FROM auto_sessions WHERE enabled=1 AND account_id<>?',(account,)):
                raise DomainError('Pause the other Auto Paper account first.')
            self.stream.stop()
        with self.lock:
            sessions = self.db.rows('SELECT * FROM auto_sessions WHERE account_id=?',(account,))
            if not sessions:
                raise DomainError('Prepare an Auto Paper account first.')
            if json.loads(sessions[0]['state']).get('halted'):
                raise DomainError('This account reached its drawdown stop; entries remain disabled.')
            if not reuse and self.stream.thread and self.stream.thread.is_alive():
                raise DomainError('Stop the existing live stream first, then press Start automatic paper trading.')
            if self.db.rows('SELECT 1 FROM automation_rules WHERE account_id=? AND enabled=1',(account,)):
                raise DomainError('Stop the manual automation rule before using Auto Paper.')
            ids = [x['instrument_id'] for x in self.db.rows('SELECT instrument_id FROM auto_universe WHERE account_id=?',(account,))]
            if not reuse:
                saved = json.loads(sessions[0]['state'])
                self.stream.start(ids,[account],universe=True,cursor=saved.get('next_batch_cursor',0))
                saved['next_batch_cursor'] = self.stream.batch_cursor
                with self.db.connect(write=True) as c:
                    c.execute('UPDATE auto_sessions SET state=? WHERE account_id=?',(json.dumps(saved),account))
            with self.db.connect(write=True) as c:
                c.execute('UPDATE auto_sessions SET enabled=1 WHERE account_id=?',(account,))
            if not self.thread or not self.thread.is_alive():
                self.stop_event.clear()
                self.thread = threading.Thread(target=self.loop, daemon=True, name='paperlab-scanner')
                self.thread.start()
            record(self.db,'autopilot','STARTED',{'stocks':len(ids)},account)

    def pause(self, account):
        with self.lock, self.db.connect(write=True) as c:
            c.execute('UPDATE auto_sessions SET enabled=0 WHERE account_id=?',(account,))
            c.execute("UPDATE paper_orders SET status='CANCELLED',reason='Auto Paper paused' WHERE account_id=? AND status='PENDING' AND request_key LIKE 'auto:scan:%'",(account,))
        # Feed remains connected to process protective exits.

    def detail(self, account, inst, status, **detail):
        with self.db.connect(write=True) as c:
            c.execute('UPDATE auto_universe SET status=?,detail=? WHERE account_id=? AND instrument_id=?',
                (status,json.dumps(detail),account,inst))

    def loop(self):
        while not self.stop_event.wait(2):
            for session in self.db.rows('SELECT * FROM auto_sessions WHERE enabled=1'):
                try:
                    self.scan(session)
                except Exception as e:
                    self.pause(session['account_id'])
                    record(self.db,'autopilot','PAUSED_ERROR',{'reason':str(e) if isinstance(e,DomainError) else type(e).__name__},session['account_id'])

    def scan(self, session):
        account = session['account_id']
        with self.lock:
            if self.stream.rotate_if_due():
                current = self.db.rows('SELECT state FROM auto_sessions WHERE account_id=?',(account,))[0]
                state = json.loads(current['state'])
                state['next_batch_cursor'] = self.stream.batch_cursor
                with self.db.connect(write=True) as c:
                    c.execute('UPDATE auto_sessions SET state=? WHERE account_id=?',(json.dumps(state),account))
        for member in self.db.rows('SELECT u.*,i.lot_size,i.tick_paise,i.kind,i.symbol FROM auto_universe u JOIN instruments i ON i.id=u.instrument_id WHERE account_id=? ORDER BY u.instrument_id',(account,)):
            if self.stop_event.is_set():
                return
            i = member['instrument_id']
            with self.lock:
                current = self.db.rows('SELECT * FROM auto_sessions WHERE account_id=?',(account,))[0]
                if not current['enabled']:
                    return
                if self.stream.state != 'CONNECTED' or account not in self.stream.accounts:
                    self.detail(account,i,'WAITING_FOR_STREAM')
                    continue
                if i not in self.stream.keys.values():
                    self.detail(account,i,'QUEUED_FOR_NEXT_BATCH'); continue
                marks = {j:q['price_paise'] for j,q in self.stream.quotes.items() if 0 <= (now()-timestamp(q['timestamp'])).total_seconds() <= 10}
                if i not in marks:
                    self.detail(account,i,'WAITING_FOR_FRESH_PRICE')
                    continue
                a = PaperBroker(self.db).snapshot(account,marks)
                state = json.loads(current['state'])
                state['peak'] = max(state.get('peak',a['initial_capital']),a['equity_paise'] or 0)
                if a['halted'] or (a['equity_paise'] is not None and a['equity_paise'] <= state['peak']*.95):
                    state['halted'] = True
                    self.pause(account)
                for t in self.db.rows('SELECT * FROM paper_trades WHERE account_id=? AND id>? ORDER BY id',(account,state.get('last_trade',0))):
                    state['last_trade'] = t['id']
                    if t['net_pnl'] < 0:
                        state['cooldown'] = (now()+timedelta(minutes=10)).isoformat()
                        state.setdefault('excluded',{})[t['strategy']] = (now()+timedelta(minutes=100)).isoformat()
                        state['risk_scale'] = max(.125,state.get('risk_scale',1)/2)
                        self.cache = {}
                with self.db.connect(write=True) as c:
                    c.execute('UPDATE auto_sessions SET state=? WHERE account_id=?',(json.dumps(state),account))
                if state.get('halted'):
                    self.detail(account,i,'DRAWDOWN_STOP'); return
                if state.get('cooldown') and timestamp(state['cooldown']) > now():
                    self.detail(account,i,'LOSS_COOLDOWN'); continue
                if a['positions'] or a['basket_positions'] or self.db.rows("SELECT 1 FROM paper_orders WHERE account_id=? AND status='PENDING'",(account,)):
                    self.detail(account,i,'POSITION_OR_ORDER_ACTIVE'); continue
                if marks[i]*member['lot_size'] >= a['available_cash_paise']:
                    self.detail(account,i,'UNAFFORDABLE'); continue
                stream_session = self.stream.bar_sessions.get(i,self.stream.session)
                rows = self.db.rows('SELECT * FROM stream_bars WHERE instrument_id=? AND session=? ORDER BY timestamp DESC LIMIT 300',(i,stream_session))[::-1]
                if len(rows) < 200:
                    self.detail(account,i,'WARMING_UP',bars=len(rows),required=200); continue
                last = rows[-1]['available_at']
                key = (account,i,stream_session)
                cached = self.cache.get(key)
                if cached and cached['last'] == last:
                    continue
            # Backtests run outside the execution lock so the live feed can continue.
            if not cached or (timestamp(last)-timestamp(cached['assessed'])).total_seconds() >= 1200:
                excluded = {s for s,t in state.get('excluded',{}).items() if timestamp(t)>now()}
                ranking = rank(rows,{**member,'id':i},a,{'atr_stop':1.5},excluded)
                eligible = [r for r in ranking if r['eligible']]
                cached = {'strategy':eligible[0]['strategy'] if eligible else None,'assessed':last}
            cached['last'] = last
            self.cache[key] = cached
            name = cached['strategy']
            if not name:
                self.detail(account,i,'NO_QUALIFYING_STRATEGY',bars=len(rows)); continue
            signal = REGISTRY[name].signal(compute(analysis_frame(rows)),{'atr_stop':1.5})
            if not signal:
                self.detail(account,i,'WAITING_FOR_SIGNAL',strategy=name,bars=len(rows)); continue
            with self.lock:
                # Recheck state and prices after potentially slow research.
                if not self.db.rows('SELECT 1 FROM auto_sessions WHERE account_id=? AND enabled=1',(account,)) or self.stream.state!='CONNECTED' or account not in self.stream.accounts or self.stream.bar_sessions.get(i,self.stream.session)!=stream_session:
                    continue
                q = self.stream.quotes.get(i)
                if not q or not 0 <= (now()-timestamp(q['timestamp'])).total_seconds() <= 10 or (now()-timestamp(last)).total_seconds()>120:
                    self.detail(account,i,'STALE_SIGNAL'); continue
                marks = {j:x['price_paise'] for j,x in self.stream.quotes.items() if 0 <= (now()-timestamp(x['timestamp'])).total_seconds()<=10}
                a = PaperBroker(self.db).snapshot(account,marks)
                if a['positions'] or self.db.rows("SELECT 1 FROM paper_orders WHERE account_id=? AND status='PENDING'",(account,)):
                    continue
                tick = member['tick_paise']
                stop = money(signal.stop_loss)//tick*tick
                target = money(signal.target)//tick*tick
                price = q['price_paise']
                if not stop < price < target:
                    self.detail(account,i,'PRICE_MOVED'); continue
                risk = json.loads(a['risk_json']); risk['risk_pct'] *= state.get('risk_scale',1)
                qty = size_position(a['equity_paise'] or 0,a['available_cash_paise'],price,stop,member['lot_size'],risk,json.loads(a['cost_json']))
                if not qty:
                    self.detail(account,i,'COST_OR_RISK_LIMIT',strategy=name); continue
                try:
                    order = PaperBroker(self.db).place_paper_order(account,dict(instrument_id=i,side='BUY',quantity=qty,stop_loss=stop/100,target=target/100,strategy=name,request_key=f'auto:scan:{i}:{last}'))
                    self.detail(account,i,'ORDER_QUEUED',strategy=name,quantity=qty,order_id=order['id'])
                    record(self.db,'autopilot','ORDER_QUEUED',{'instrument_id':i,'strategy':name,'quantity':qty},account)
                except DomainError as e:
                    self.detail(account,i,'BLOCKED',reason=str(e))

    def status(self, account=None):
        sessions = self.db.rows('SELECT s.*,a.name,a.initial_capital,a.cost_json FROM auto_sessions s JOIN paper_accounts a ON a.id=s.account_id ORDER BY account_id DESC')
        for s in sessions:
            s['state']=json.loads(s['state']); s['costs']=json.loads(s.pop('cost_json'))
        if account is None and sessions:
            account = sessions[0]['account_id']
        stocks = self.db.rows('SELECT u.*,i.provider_key,b.ticker FROM auto_universe u JOIN instruments i ON i.id=u.instrument_id JOIN bhav_securities b ON b.instrument_id=u.source_id WHERE account_id=?',(account,)) if account else []
        for s in stocks:
            s['detail']=json.loads(s['detail'])
        orders = self.db.rows('SELECT o.id,o.side,o.quantity,o.status,o.reason,o.strategy,i.metadata_source AS symbol FROM paper_orders o JOIN instruments i ON i.id=o.instrument_id WHERE o.account_id=? ORDER BY o.id DESC LIMIT 50',(account,)) if account else []
        return {'sessions':sessions,'stocks':stocks,'orders':orders,'costs':DEFAULT_COSTS,'stream':self.stream.status()}

    def shutdown(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=30)


def register_autopilot(app, auto):
    router=APIRouter(prefix='/api/auto-paper')
    @router.get('')
    def status(account_id:int|None=None):
        return auto.status(account_id)
    @router.post('/prepare')
    def prepare(v:Setup):
        return auto.prepare(v.dataset_id,v.initial_capital)
    @router.post('/{account}/start')
    def start(account:int):
        auto.start(account); return {'started':True}
    @router.post('/{account}/pause')
    def pause(account:int):
        auto.pause(account); return {'paused':True}
    app.include_router(router)
