"""Managed intraday F&O paper accounts. Upstox is used for read-only data.

Signals are transparent regime rules, not ML or a promise of profitable trades.
History is used only for indicators; every fill references a fresh stream quote.
"""
import json
import math
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter
from pydantic import Field
from .core import DomainError, now, timestamp, money, UTC
from .schemas import Strict, RiskConfig, CostConfig
from .paper import PaperBroker
from .derivatives import DerivativeBroker
from .activity import record
from .data import analysis_frame
from .indicators import compute

IST = ZoneInfo('Asia/Kolkata')
NIFTY = 'NSE_INDEX|Nifty 50'


def simulation_costs(kind):
    # Explicit research assumptions, not a reproduction of current broker taxes.
    return CostConfig(brokerage_paise=2000, gst_pct=18,
        exchange_bps=0.2 if kind == 'FUTURE' else 4,
        stt_sell_bps=5 if kind == 'FUTURE' else 15,
        stamp_buy_bps=0.2 if kind == 'FUTURE' else 0.3,
        sebi_bps=0.01, slippage_bps=1 if kind == 'FUTURE' else 5).model_dump()


class Setup(Strict):
    market: Literal['NIFTY', 'STOCK'] = 'NIFTY'
    underlying_key: str = NIFTY
    initial_capital: float = Field(default=5000, ge=1, le=100000000)
    product: Literal['AUTO', 'OPTIONS', 'FUTURES'] = 'AUTO'
    risk_pct: float = Field(default=1, gt=0, le=10)


def expiry(value):
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value / 1000, UTC).astimezone(IST).date().isoformat()
    return str(value)[:10]


def contract_master(master, key, today):
    result = []
    for m in master:
        if m.get('segment') != 'NSE_FO' or m.get('underlying_key') != key or m.get('instrument_type') not in ('FUT','CE','PE'):
            continue
        try:
            e = expiry(m['expiry'])
            datetime.strptime(e, '%Y-%m-%d')
            lot, tick = int(m['lot_size']), int(m['tick_size'])
            if e <= today or lot <= 0 or tick <= 0 or lot != float(m['lot_size']) or tick != float(m['tick_size']):
                continue
            pk = m['instrument_key']
            if not pk.startswith('NSE_FO|'):
                continue
            kind = {'FUT':'FUTURE','CE':'CALL','PE':'PUT'}[m['instrument_type']]
            strike = money(m['strike_price']) if kind != 'FUTURE' else None
            if kind != 'FUTURE' and strike <= 0:
                continue
            result.append(dict(key=pk, label=m.get('trading_symbol',pk), expiry=e,
                lot=lot, tick=tick, kind=kind, strike=strike))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    if len({r['key'] for r in result}) != len(result):
        raise DomainError('Ambiguous derivative master; no contracts selected')
    return result


def signal_for(rows, excluded=()):
    """Causal rules using only completed bars; zero index volume is supported."""
    if len(rows) < 60:
        return None
    frame = compute(analysis_frame(rows))
    r = frame.iloc[-1]
    if not all(math.isfinite(float(r[k])) for k in ('atr','adx','ema_20','ema_50','z_score')) or r['atr'] <= 0:
        return None
    choices = []
    if r['adx'] >= 20:
        if r['close'] > r['resistance']:
            choices.append(('breakout',1))
        elif r['close'] < r['support']:
            choices.append(('breakout',-1))
        if r['close'] > r['ema_20'] > r['ema_50'] and r['ema_20'] > frame.iloc[-4]['ema_20']:
            choices.append(('trend',1))
        elif r['close'] < r['ema_20'] < r['ema_50'] and r['ema_20'] < frame.iloc[-4]['ema_20']:
            choices.append(('trend',-1))
    elif r['adx'] < 20:
        if r['z_score'] < -1.5 and r['close'] > frame.iloc[-2]['close']:
            choices.append(('range_reversal',1))
        elif r['z_score'] > 1.5 and r['close'] < frame.iloc[-2]['close']:
            choices.append(('range_reversal',-1))
    for name, direction in choices:
        if name not in excluded:
            return dict(name=name, direction=direction, atr=money(float(r['atr'])),
                regime='TREND' if r['adx'] >= 20 else 'RANGE')
    return None


class FnoAuto:
    def __init__(self, db, provider, stream, lock):
        self.db, self.provider, self.stream, self.lock = db, provider, stream, lock
        from .agent_team import AgentTeam
        self.team = AgentTeam(db)
        self.shutting_down = False
        self.history = {}
        self.master = None
        self.master_day = None
        self.thread = None
        self.stop_event = threading.Event()
        self.control_lock = threading.Lock()
        with db.connect(write=True) as c:
            c.execute('UPDATE fno_sessions SET enabled=0')
            c.execute("UPDATE baskets SET status='CANCELLED',reason='Application restarted; pending entry cancelled' WHERE status='PENDING' AND id IN (SELECT basket_id FROM fno_tickets)")
        stream.fno_tick = self.tick

    def catalog(self):
        today = now().astimezone(IST).date().isoformat()
        if self.master is None or self.master_day != today:
            self.master = self.provider.get_instruments()
            self.master_day = today
        return self.master

    def stocks(self):
        self.provider.policy.require_live()
        found = {}
        for m in self.catalog():
            k = m.get('underlying_key','')
            if m.get('segment') == 'NSE_FO' and k.startswith('NSE_EQ|') and m.get('instrument_type') in ('FUT','CE','PE') and expiry(m.get('expiry','')) > now().astimezone(IST).date().isoformat():
                found[k] = m.get('underlying_symbol',k)
        return [{'key':k,'label':v} for k,v in sorted(found.items(),key=lambda x:x[1])]

    def setup(self, v):
        key, label = NIFTY, 'NIFTY 50 index F&O'
        if v.market == 'STOCK':
            selected = next((x for x in self.stocks() if x['key'] == v.underlying_key),None)
            if not selected:
                raise DomainError('Choose a currently eligible stock F&O underlying')
            key, label = selected['key'], selected['label'] + ' stock F&O'
        with self.lock:
            if v.market == 'NIFTY':
                existing = self.db.rows("SELECT account_id FROM fno_sessions WHERE market='NIFTY'")
                if existing:
                    return {'account_id':existing[0]['account_id'], 'reused':True}
            a = PaperBroker(self.db).create_account(dict(name=label, mode='LIVE', initial_capital=v.initial_capital,
                risk=RiskConfig(risk_pct=v.risk_pct,max_positions=1,max_trades=6).model_dump(), costs=simulation_costs('CALL')))
            with self.db.connect(write=True) as c:
                c.execute('INSERT INTO fno_sessions(account_id,market,underlying_key,label,product,state,created_at) VALUES(?,?,?,?,?,?,?)',
                    (a,v.market,key,label,v.product,json.dumps({'status':'READY','message':'Press Start to discover contracts and connect data.','peak':money(v.initial_capital),'risk_scale':1,'last_trade':0}),now().isoformat()))
        return {'account_id':a,'reused':False}

    def session(self, account):
        rows = self.db.rows('SELECT * FROM fno_sessions WHERE account_id=?',(account,))
        if not rows:
            raise DomainError('Unknown managed F&O account')
        return rows[0]

    def state(self, account, **updates):
        with self.db.connect(write=True) as c:
            row = c.execute('SELECT state FROM fno_sessions WHERE account_id=?',(account,)).fetchone()
            s = json.loads(row['state']); s.update(updates)
            c.execute('UPDATE fno_sessions SET state=? WHERE account_id=?',(json.dumps(s),account))
        return s

    def active(self, account):
        return self.db.rows("SELECT b.*,t.stop_paise AS managed_stop,t.target_paise,t.expires_at,t.signal FROM baskets b JOIN fno_tickets t ON t.basket_id=b.id WHERE b.account_id=? AND b.status IN ('PENDING','OPEN','CLOSING')",(account,))

    def register(self, c, symbol, kind, lot, tick, key, label, underlying=None, expires=None, strike=None, physical=False):
        # Include expiry in the identity because the exchange can recycle FO tokens.
        existing = c.execute('SELECT * FROM instruments WHERE symbol=?',(symbol,)).fetchone()
        if existing:
            if (existing['kind'],existing['lot_size'],existing['tick_paise'],existing['provider_key'],existing['expiry'],existing['strike_paise']) != (kind,lot,tick,key,expires,strike):
                raise DomainError('Contract metadata changed; existing evidence was preserved')
            return existing['id']
        return c.execute('INSERT INTO instruments(symbol,kind,lot_size,tick_paise,provider_key,metadata_source,underlying,expiry,strike_paise,settlement,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (symbol,kind,lot,tick,key,'Upstox instrument master | '+label,underlying,expires,strike,'PHYSICAL' if physical else 'CASH',now().isoformat())).lastrowid

    def prepare(self, account):
        s = self.session(account)
        self.provider.policy.require_live(); self.provider.enabled()
        today = now().astimezone(IST).date()
        self.state(account,status='PREPARING',message='Finding current contracts and downloading completed minute bars…')
        key = s['underlying_key']
        master = self.catalog()
        options = contract_master(master,key,today.isoformat())
        if not options:
            raise DomainError('No valid non-expiry-day F&O contracts found for this underlying')
        # History is only an indicator warm-up and a strike-selection reference.
        rows = self.provider.get_candles(key,(today-timedelta(days=7)).isoformat(),(today-timedelta(days=1)).isoformat(),'1m')
        rows += self.provider.get_intraday(key)
        completed = {}
        for r in rows:
            at = timestamp(r['timestamp'])
            if at + timedelta(minutes=1) > now():
                continue
            vals = {k:money(r[k]) for k in ('open','high','low','close')}
            if min(vals.values()) <= 0 or vals['low'] > min(vals['open'],vals['close']) or vals['high'] < max(vals['open'],vals['close']):
                raise DomainError('Malformed underlying candles; automatic entry disabled')
            completed[at.isoformat()] = dict(timestamp=at.isoformat(),available_at=(at+timedelta(minutes=1)).isoformat(),volume=max(0,int(r.get('volume',0))),**vals)
        history = sorted(completed.values(),key=lambda r:r['timestamp'])[-300:]
        if not history:
            raise DomainError('Underlying history unavailable; retry when the provider has data')
        reference = history[-1]['close']
        chosen=[]
        for kind in ('FUTURE','CALL','PUT'):
            candidates=[x for x in options if x['kind']==kind]
            if not candidates:
                continue
            nearest=min(x['expiry'] for x in candidates)
            candidates=[x for x in candidates if x['expiry']==nearest]
            chosen += sorted(candidates,key=lambda x:abs((x['strike'] or reference)-reference))[:1 if kind=='FUTURE' else 7]
        with self.lock, self.db.connect(write=True) as c:
            underlying = self.register(c,'FNO:UNDERLYING:'+key,'INDEX' if s['market']=='NIFTY' else 'SPOT',1,1,key,s['label'])
            # Retain held contracts until closed; refresh all other subscriptions daily.
            c.execute('DELETE FROM fno_contracts WHERE account_id=? AND instrument_id NOT IN (SELECT p.instrument_id FROM basket_positions p JOIN baskets b ON b.id=p.basket_id WHERE b.account_id=?)',(account,account))
            for m in chosen:
                self.provider.key(m['key'])
                i=self.register(c,'FNO:'+m['key']+':'+m['expiry'],m['kind'],m['lot'],m['tick'],m['key'],m['label'],key,m['expiry'],m['strike'],s['market']=='STOCK')
                c.execute('INSERT OR IGNORE INTO fno_contracts VALUES(?,?)',(account,i))
            c.execute('UPDATE fno_sessions SET underlying_id=?,prepared_day=? WHERE account_id=?',(underlying,today.isoformat(),account))
        with self.db.connect(write=True) as c:
            for r in history:
                c.execute('INSERT OR IGNORE INTO market_history(instrument_id,timestamp,available_at,open,high,low,close,volume,source,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (underlying,r['timestamp'],r['available_at'],r['open'],r['high'],r['low'],r['close'],r['volume'],'UPSTOX_MINUTE_HISTORY',now().isoformat()))
        self.history[account]=history
        self.state(account,status='READY',message='Contracts discovered. Waiting for fresh stream bars.',history_bars=len(history),reference_paise=reference)

    def start(self, account):
        with self.control_lock:
            self._start(account)

    def _start(self, account):
        s=self.session(account)
        halted = json.loads(s['state']).get('halted',False)
        if self.db.rows('SELECT 1 FROM auto_sessions WHERE enabled=1') or self.db.rows('SELECT 1 FROM automation_rules WHERE enabled=1'):
            raise DomainError('Pause the other automation before starting managed F&O')
        for a in self.stream.accounts:
            if not self.db.rows('SELECT 1 FROM fno_sessions WHERE account_id=?',(a,)):
                snap=PaperBroker(self.db).snapshot(a)
                if snap['positions'] or snap['basket_positions'] or self.db.rows("SELECT 1 FROM paper_orders WHERE account_id=? AND status='PENDING'",(a,)):
                    raise DomainError('Another account has live exposure; keep its protective stream running')
        try:
            self.pause(account)
            if not self.active(account):
                self.prepare(account)
            if self.shutting_down:
                raise DomainError('Application is stopping')
            self.connect(account)
        except Exception as e:
            self.state(account,status='START_FAILED',message=str(e) if isinstance(e,DomainError) else 'Provider unavailable; retry Start')
            raise
        with self.lock, self.db.connect(write=True) as c:
            c.execute('UPDATE fno_sessions SET enabled=? WHERE account_id=?',(0 if halted else 1,account))
        self.state(account,status='DRAWDOWN_STOP' if halted else 'WARMING_UP',message='Protection reconnected; new entries remain stopped.' if halted else 'Waiting for two completed live minutes; history supplies the indicator warm-up.')
        self.ensure_worker()
        record(self.db,'fno','STARTED',{'market':s['label']},account)

    def ensure_worker(self):
        if self.shutting_down: return
        if not self.thread or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread=threading.Thread(target=self.loop,daemon=True,name='paperlab-fno')
            self.thread.start()

    def connect(self, account):
        if self.shutting_down:
            raise DomainError("Application is stopping")
        # Shared feed, separate capital. Include paused accounts with open exposure.
        accounts={account}
        accounts.update(r['account_id'] for r in self.db.rows('SELECT account_id FROM fno_sessions WHERE enabled=1'))
        accounts.update(r['account_id'] for r in self.db.rows("SELECT DISTINCT b.account_id FROM baskets b JOIN fno_tickets t ON t.basket_id=b.id WHERE b.status IN ('OPEN','CLOSING')"))
        ids=set()
        for a in accounts:
            session=self.session(a)
            if session['underlying_id']:
                ids.add(session['underlying_id'])
            ids.update(x['instrument_id'] for x in self.db.rows('SELECT instrument_id FROM fno_contracts WHERE account_id=?',(a,)))
        if len(ids)>2000:
            raise DomainError('F&O subscription capacity exceeded; no instruments were silently dropped')
        with self.lock, self.db.connect(write=True) as c:
            c.execute("UPDATE baskets SET status='CANCELLED',reason='Feed subscription refreshed' WHERE status='PENDING' AND id IN (SELECT basket_id FROM fno_tickets)")
        self.stream.stop()
        self.stream.start(sorted(ids),sorted(accounts))

    def pause(self, account):
        self.session(account)
        with self.lock, self.db.connect(write=True) as c:
            c.execute('UPDATE fno_sessions SET enabled=0 WHERE account_id=?',(account,))
            c.execute("UPDATE baskets SET status='CANCELLED',reason='New entries paused' WHERE account_id=? AND status='PENDING' AND id IN (SELECT basket_id FROM fno_tickets)",(account,))
        self.state(account,status='PAUSED',message='Entries paused; keep the app and feed running for protective exits.')

    def close(self, account):
        self.pause(account)
        for b in self.active(account):
            if b['status']=='OPEN':
                DerivativeBroker(self.db).request_close(account,b['id'])
        self.state(account,status='EXIT_REQUESTED',message='Exit queued; waiting for a subsequent fresh contract quote.')

    def marks(self):
        return {i:q['price_paise'] for i,q in list(self.stream.quotes.items()) if 0 <= (now()-timestamp(q['timestamp'])).total_seconds() <=10}

    def tick(self, q, quote_id):
        # Called under the stream lock after market-session/freshness validation.
        i=q['instrument_id']; at=timestamp(q['timestamp']); local=at.astimezone(IST)
        broker=DerivativeBroker(self.db)
        for s in self.db.rows('SELECT s.* FROM fno_sessions s JOIN fno_contracts u ON u.account_id=s.account_id WHERE u.instrument_id=?',(i,)):
            a=s['account_id']
            if a not in self.stream.accounts:
                continue
            active=self.active(a)
            for b in active:
                leg=json.loads(b['legs_json'])[0]
                if leg['instrument_id'] != i:
                    continue
                if b['status']=='PENDING':
                    why=None
                    if not s['enabled'] or self.stream.state!='CONNECTED': why='Automation paused or stream disconnected'
                    elif at > timestamp(b['expires_at']): why='Entry quote arrived after signal expiry'
                    elif local.hour*60+local.minute >= 900 or local.hour*60+local.minute < 555: why='Outside intraday entry window'
                    elif local.date().isoformat()!=s['prepared_day']: why='Contract discovery needs daily refresh'
                    if why:
                        with self.db.connect(write=True) as c:
                            c.execute("UPDATE baskets SET status='CANCELLED',reason=? WHERE id=?",(why,b['id']))
                elif b['status']=='OPEN':
                    long=leg['quantity']>0
                    stop=q['price_paise']<=b['managed_stop'] if long else q['price_paise']>=b['managed_stop']
                    target=q['price_paise']>=b['target_paise'] if long else q['price_paise']<=b['target_paise']
                    overnight=timestamp(b['opened_at']).astimezone(IST).date()<local.date()
                    cutoff=local.hour*60+local.minute>=915
                    if stop or target or cutoff or overnight:
                        reason='Intraday square-off' if cutoff or overnight else ('Stop loss' if stop else 'Profit target')
                        broker.request_close(a,b['id'])
                        with self.db.connect(write=True) as c:
                            c.execute('UPDATE fno_tickets SET reason=? WHERE basket_id=?',(reason,b['id']))
                        record(self.db,'fno','EXIT_QUEUED',{'basket_id':b['id'],'reason':reason},a)
            if active:
                before={b['id']:b['status'] for b in self.active(a)}
                broker.process(a,[dict(id=quote_id,quote_id=quote_id,instrument_id=i,timestamp=q['timestamp'],open=q['price_paise'])])
                for bid, old in before.items():
                    new=self.db.rows('SELECT status,reason FROM baskets WHERE id=?',(bid,))[0]
                    if new['status'] != old:
                        self.team.message(a,'execution',new['status'],'Paper order state updated from stored quote evidence.',{'basket_id':bid,'quote_id':quote_id,'price_paise':q['price_paise']})
                        record(self.db,'fno',new['status'],{'basket_id':bid,'price_paise':q['price_paise'],'reason':new['reason']},a)

    def loop(self):
        while not self.stop_event.wait(2):
            for s in self.db.rows('SELECT * FROM fno_sessions'):
                try:
                    if s['enabled'] and s['prepared_day'] != now().astimezone(IST).date().isoformat() and 555 <= now().astimezone(IST).hour*60+now().astimezone(IST).minute < 900:
                        with self.control_lock:
                            self.prepare(s['account_id']); self.connect(s['account_id'])
                    with self.lock:
                        self.scan(self.session(s['account_id']))
                except Exception as e:
                    self.pause(s['account_id'])
                    self.state(s['account_id'],status='PAUSED_ERROR',message=str(e) if isinstance(e,DomainError) else 'Automatic scan error: '+type(e).__name__)
                    record(self.db,'fno','PAUSED_ERROR',{'error':type(e).__name__},s['account_id'])

    def scan(self, s):
        a=s['account_id']; state=json.loads(s['state']); local=now().astimezone(IST)
        active=self.active(a)
        # Wall-clock cutoff queues an exit even if the feed stops. Never invent a fill.
        for b in active:
            if b['status']=='PENDING' and (timestamp(b['expires_at']) < now() or local.hour*60+local.minute>=900):
                with self.db.connect(write=True) as c:
                    c.execute("UPDATE baskets SET status='CANCELLED',reason='Entry expired or session cutoff' WHERE id=?",(b['id'],))
            if b['status']=='OPEN' and (local.hour*60+local.minute>=915 or timestamp(b['opened_at']).astimezone(IST).date()<local.date()):
                DerivativeBroker(self.db).request_close(a,b['id'])
                self.state(a,status='EXIT_PENDING',message='Intraday exit queued; awaiting a fresh contract quote.')
        self.team.review_closed(a)
        snap=PaperBroker(self.db).snapshot(a,self.marks())
        if snap['equity_paise'] is not None:
            state['peak']=max(state.get('peak',snap['initial_capital']),snap['equity_paise'])
            if snap['equity_paise']<=state['peak']*.95:
                state['halted']=True
        for t in self.db.rows('SELECT t.*,f.signal FROM basket_trades t JOIN baskets b ON b.id=t.basket_id JOIN fno_tickets f ON f.basket_id=b.id WHERE b.account_id=? AND t.id>? ORDER BY t.id',(a,state.get('last_trade',0))):
            state['last_trade']=t['id']
            if t['gross_pnl']-t['fees']<0:
                state['cooldown']=(now()+timedelta(minutes=10)).isoformat()
                state.setdefault('excluded',{})[t['signal']]=(now()+timedelta(minutes=100)).isoformat()
                state['risk_scale']=max(.125,state.get('risk_scale',1)/2)
        self.state(a,**state)
        if state.get('halted') or snap['halted']:
            self.pause(a); self.state(a,status='DRAWDOWN_STOP',message='Entries stopped. Protective exits remain active while the feed runs.'); return
        if not s['enabled']:
            return
        active=self.active(a)
        if active:
            self.state(a,status=active[0]['status'],message='Monitoring the paper order / position and protective exit.'); return
        if local.weekday()>4 or not 555 <= local.hour*60+local.minute < 900:
            self.state(a,status='MARKET_CLOSED',message='Entries run on normal weekdays, 09:15–15:00 IST. Open positions exit from 15:15.'); return
        if state.get('cooldown') and timestamp(state['cooldown'])>now():
            self.state(a,status='LOSS_COOLDOWN',message='Loss cooldown: waiting 10 minutes; next risk budget is reduced.'); return
        uid=s['underlying_id']
        if self.stream.state!='CONNECTED' or a not in self.stream.accounts or uid not in self.marks():
            self.state(a,status='WAITING_FOR_FEED',message='Waiting for a fresh underlying quote in the normal market session.'); return
        live=self.db.rows('SELECT * FROM stream_bars WHERE instrument_id=? AND session=? ORDER BY timestamp DESC LIMIT 300',(uid,self.stream.bar_sessions.get(uid,self.stream.session)))[::-1]
        if len(live)<2:
            self.state(a,status='WARMING_UP',message=f'{len(live)} / 2 completed live minutes since connect or data gap.',live_bars=len(live)); return
        last=live[-1]['timestamp']
        if now()-timestamp(live[-1]['available_at'])>timedelta(seconds=120):
            self.state(a,status='STALE_SIGNAL',message='Waiting for a newly completed underlying candle.'); return
        if state.get('last_signal_bar')==last:
            return
        combined={r['timestamp']:r for r in self.history.get(a,[]) if timestamp(r['timestamp'])<timestamp(live[0]['timestamp'])}
        combined.update({r['timestamp']:r for r in live})
        rows=sorted(combined.values(),key=lambda r:r['timestamp'])[-300:]
        self.state(a,last_signal_bar=last,live_bars=len(live))
        if len(rows)<60:
            self.state(a,status='WARMING_UP',message=f'{len(rows)} / 60 completed indicator bars.'); return
        excluded={k for k,v in state.get('excluded',{}).items() if timestamp(v)>now()}
        sig=self.team.evaluate(a,rows,excluded)
        if not sig:
            self.state(a,status='WAITING_FOR_SIGNAL',strategy=None,message='No eligible trend, breakout or range-reversal signal on this completed minute.'); return
        self.queue(s,sig,last,snap)

    def queue(self, s, sig, last, snap):
        a=s['account_id']; marks=self.marks(); state=json.loads(self.session(a)['state'])
        kind='CALL' if sig['direction']>0 else 'PUT'
        contracts=self.db.rows('SELECT i.* FROM instruments i JOIN fno_contracts u ON u.instrument_id=i.id WHERE u.account_id=?',(a,))
        candidates=[i for i in contracts if (s['product']!='FUTURES' and i['kind']==kind) or (s['product']!='OPTIONS' and i['kind']=='FUTURE')]
        reference=marks.get(s['underlying_id'],0)
        candidates.sort(key=lambda i:(i['kind']=='FUTURE',i['expiry'],abs((i['strike_paise'] or reference)-reference)))
        reasons=[]; broker=DerivativeBroker(self.db)
        for i in candidates:
            p=marks.get(i['id'])
            if not p:
                reasons.append('Waiting for fresh contract quotes'); continue
            # Never move into far-out-of-money options just to force an affordable lot.
            if i['kind']!='FUTURE' and abs(i['strike_paise']-reference)>reference*.02:
                continue
            qty=i['lot_size']*(sig['direction'] if i['kind']=='FUTURE' else 1)
            direction=1 if qty>0 else -1
            factor=float(sig.get('stop_factor',1.5)); rr=float(sig.get('target_rr',2.0))
            if i['kind']=='FUTURE':
                distance=int(sig['atr']*factor); stop_basis='Underlying ATR × strategy factor'
            else:
                contract_rows=self.db.rows('SELECT * FROM stream_bars WHERE instrument_id=? ORDER BY timestamp DESC LIMIT 30',(i['id'],))[::-1]
                atr=None
                if len(contract_rows)>=15:
                    changes=[max(r['high']-r['low'],abs(r['high']-contract_rows[n-1]['close']),abs(r['low']-contract_rows[n-1]['close'])) for n,r in enumerate(contract_rows) if n>0]
                    atr=sum(changes[-14:])/14
                fallback={'trend':.25,'breakout':.20,'range_reversal':.15}.get(sig['name'],.25)
                distance=int(atr*factor) if atr and atr>0 else int(p*fallback)
                stop_basis='Observed contract ATR × strategy factor' if atr and atr>0 else 'Strategy premium fallback; insufficient contract ATR history'
            distance=max(i['tick_paise'],distance)
            stop=int((p-direction*distance)//i['tick_paise']*i['tick_paise'])
            target=int((p+direction*distance*rr)//i['tick_paise']*i['tick_paise'])
            if min(stop,target)<=0:
                continue
            strategy='future' if i['kind']=='FUTURE' else ('long_call' if kind=='CALL' else 'long_put')
            legs=[dict(instrument_id=i['id'],quantity=qty)]
            with self.db.connect(write=True) as c:
                if i['kind']=='FUTURE':
                    # Deliberately labelled approximation; never presented as broker SPAN.
                    margin=math.ceil(p*i['lot_size']*.25)
                    c.execute('INSERT INTO margin_evidence(instrument_id,available_at,valid_from,valid_until,initial_per_lot,maintenance_per_lot,source) VALUES(?,?,?,?,?,?,?)',
                        (i['id'],now().isoformat(),now().isoformat(),(now()+timedelta(days=1)).isoformat(),margin,margin,'PAPER ASSUMPTION: 25% notional collateral; not broker margin'))
                account=dict(c.execute('SELECT * FROM paper_accounts WHERE id=?',(a,)).fetchone())
                try:
                    debit,margin,fee=broker._validate(c,account,dict(strategy=strategy,stop_paise=stop),[i],legs,[p],now().isoformat())
                except DomainError as e:
                    reasons.append(str(e)); continue
            b=broker.submit(a,strategy,legs,f'fno:{a}:{last}',stop,live=True)
            with self.db.connect(write=True) as c:
                c.execute('INSERT INTO fno_tickets(basket_id,signal,signal_at,stop_paise,target_paise,expires_at) VALUES(?,?,?,?,?,?)',
                    (b['id'],sig['name'],last,stop,target,(now()+timedelta(seconds=90)).isoformat()))
                c.execute('INSERT INTO fno_reservations VALUES(?,?,?,?)',(b['id'],debit,margin,fee))
            self.team.record_decision(a,b['id'],{**sig,'stop_paise':stop,'target_paise':target,'entry_reference_paise':p,'stop_basis':stop_basis,'instrument_id':i['id']})
            self.team.message(a,'risk','APPROVED','Full lot, capital, loss budget and estimated costs passed.',{'premium_paise':debit,'margin_paise':margin,'entry_cost_paise':fee,'stop_paise':stop,'target_paise':target})
            self.team.message(a,'execution','QUEUED','Awaiting a subsequent fresh stream quote.',{'basket_id':b['id']})
            self.state(a,status='ORDER_QUEUED',message='Paper entry queued for a subsequent fresh contract quote.',strategy=sig['name'],regime=sig['regime'])
            record(self.db,'fno','ORDER_QUEUED',{'basket_id':b['id'],'contract':i['metadata_source'].split(' | ')[-1],'strategy':sig['name'],'quantity':qty},a)
            return
        self.team.message(a,'risk','BLOCKED','; '.join(dict.fromkeys(reasons)) or 'No eligible near-the-money contract')
        self.state(a,status='BLOCKED',strategy=sig['name'],message='; '.join(dict.fromkeys(reasons)) or 'No eligible near-the-money contract. Waiting for a new signal.',
            detail='Full lots only. Long-option risk reserves the entire premium plus costs. Futures reserve 25% of notional as simulated collateral.')

    def status(self, account=None):
        sessions=self.db.rows("SELECT s.*,a.initial_capital,a.risk_json FROM fno_sessions s JOIN paper_accounts a ON a.id=s.account_id ORDER BY CASE WHEN market='NIFTY' THEN 0 ELSE 1 END,account_id")
        for s in sessions:
            s['state']=json.loads(s['state']); s['risk']=json.loads(s.pop('risk_json'))
        if account is None and sessions: account=sessions[0]['account_id']
        if account is not None: self.session(account)
        snapshot=PaperBroker(self.db).snapshot(account,self.marks()) if account else None
        orders=self.db.rows('SELECT b.*,t.signal,t.signal_at,t.stop_paise AS managed_stop,t.target_paise,t.reason AS exit_reason FROM baskets b JOIN fno_tickets t ON t.basket_id=b.id WHERE b.account_id=? ORDER BY b.id DESC LIMIT 100',(account,)) if account else []
        for o in orders:
            leg=json.loads(o.pop('legs_json'))[0]
            inst=self.db.rows('SELECT * FROM instruments WHERE id=?',(leg['instrument_id'],))[0]
            o.update(instrument_id=leg['instrument_id'],quantity=leg['quantity'],contract=inst['metadata_source'].split(' | ')[-1],kind=inst['kind'],expiry=inst['expiry'],lot_size=inst['lot_size'])
            events=self.db.rows('SELECT kind,timestamp,payload FROM basket_events WHERE basket_id=? ORDER BY id',(o['id'],))
            o['entry_paise']=next((json.loads(e['payload'])['legs'][0]['price'] for e in events if e['kind']=='BASKET_OPEN'),None)
            o['exit_paise']=next((json.loads(e['payload'])['legs'][0]['price'] for e in events if e['kind']=='BASKET_CLOSE'),None)
            trade=self.db.rows('SELECT gross_pnl-fees AS net_pnl FROM basket_trades WHERE basket_id=?',(o['id'],))
            o['net_pnl']=trade[0]['net_pnl'] if trade else None
        if snapshot:
            for p in snapshot['basket_positions']:
                inst=self.db.rows('SELECT metadata_source FROM instruments WHERE id=?',(p['instrument_id'],))[0]
                p['contract']=inst['metadata_source'].split(' | ')[-1]
                t=self.db.rows('SELECT stop_paise,target_paise FROM fno_tickets WHERE basket_id=?',(p['basket_id'],))
                if t: p.update(t[0])
        events=self.db.rows("SELECT * FROM activity_events WHERE account_id=? AND category='fno' ORDER BY id DESC LIMIT 30",(account,)) if account else []
        contracts=self.db.rows('SELECT i.id,i.kind,i.provider_key,i.lot_size,i.expiry,i.metadata_source FROM instruments i JOIN fno_contracts u ON u.instrument_id=i.id WHERE u.account_id=?',(account,)) if account else []
        for i in contracts:
            i['price_paise']=self.marks().get(i['id'])
        return dict(sessions=sessions,selected_account=account,snapshot=snapshot,orders=orders,events=events,contracts=contracts,
            runtime=getattr(getattr(self,'runtime_controller',None),'info',{'status':'READY','message':'Environment controller not started'}),
            balances=self.balances(account,snapshot) if snapshot else None,
            agent_team=self.team.status(account) if account else {'messages':[],'reviews':[],'candidates':[]},
            stream={'state':self.stream.state,'error':getattr(self.stream,'error',None),'last_message':getattr(self.stream,'last_received',None)},
            costs={'options':simulation_costs('CALL'),'futures':simulation_costs('FUTURE')})

    def balances(self, account, snap=None):
        snap=snap or PaperBroker(self.db).snapshot(account,self.marks())
        reservations=self.db.rows("SELECT r.* FROM fno_reservations r JOIN baskets b ON b.id=r.basket_id WHERE b.account_id=? AND b.status='PENDING'",(account,))
        pending=sum(r['premium']+r['margin']+r['estimated_fee'] for r in reservations)
        return dict(initial_capital_paise=snap['initial_capital'],cash_paise=snap['cash_balance'],
            available_cash_paise=snap['available_cash_paise']-pending,
            premium_in_positions_paise=sum(p['book_cost'] for p in snap['basket_positions']),
            margin_reserved_paise=snap['used_margin_paise'],pending_reserved_paise=pending,
            costs_paise=snap['costs'],realized_net_pnl_paise=snap['realized_pnl']-snap['costs'],
            unrealized_pnl_paise=snap['unrealized_pnl_paise'],equity_paise=snap['equity_paise'])

    def chart(self, account=None, instrument_id=None):
        if account is None:
            sessions=self.db.rows("SELECT account_id FROM fno_sessions WHERE market='NIFTY'")
            if not sessions: return dict(instrument_id=None,label='NIFTY 50',bars=[],forming=None,levels={},source='Waiting for market data')
            account=sessions[0]['account_id']
        s=self.session(account); i=instrument_id or s['underlying_id']
        if not i: return dict(instrument_id=None,label=s['label'],bars=[],forming=None,levels={},source='Waiting for contract discovery')
        if i!=s['underlying_id'] and not self.db.rows('SELECT 1 FROM fno_contracts WHERE account_id=? AND instrument_id=?',(account,i)):
            raise DomainError('Chart instrument is outside this account')
        inst=self.db.rows('SELECT * FROM instruments WHERE id=?',(i,))[0]
        history=self.db.rows('SELECT * FROM market_history WHERE instrument_id=? ORDER BY timestamp DESC LIMIT 500',(i,))[::-1]
        live=self.db.rows('SELECT * FROM stream_bars WHERE instrument_id=? ORDER BY timestamp DESC,id DESC LIMIT 500',(i,))[::-1]
        merged={r['timestamp']:r for r in history}
        merged.update({r['timestamp']:r for r in live})
        bars=sorted(merged.values(),key=lambda r:r['timestamp'])[-300:]
        forming=None; bar=self.stream.bars.get(i) if hasattr(self.stream,'bars') else None
        if bar and i in self.marks():
            forming=dict(timestamp=bar['bucket'].isoformat(),open=bar['open'],high=bar['high'],low=bar['low'],close=bar['close'],volume=max(0,bar['last_v']-bar['start_v']),complete=False)
        levels={}
        held=self.db.rows('SELECT p.basis_price,t.stop_paise,t.target_paise FROM basket_positions p JOIN baskets b ON b.id=p.basket_id JOIN fno_tickets t ON t.basket_id=b.id WHERE b.account_id=? AND p.instrument_id=?',(account,i))
        if held: levels=dict(entry_paise=held[0]['basis_price'],stop_paise=held[0]['stop_paise'],target_paise=held[0]['target_paise'])
        return dict(instrument_id=i,label=inst['metadata_source'].split(' | ')[-1],bars=bars,forming=forming,levels=levels,source='Stored completed minute bars; live forming candle is not used for decisions',strategy=json.loads(s['state']).get('strategy'))

    def shutdown(self):
        self.shutting_down=True
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=30)


def register_fno(app, manager):
    router=APIRouter(prefix='/api/fno-auto')
    @router.get('')
    def status(account_id:int|None=None):
        with manager.lock:
            return manager.status(account_id)
    @router.get('/chart')
    def chart(account_id:int|None=None,instrument_id:int|None=None):
        with manager.lock:
            return manager.chart(account_id,instrument_id)
    @router.get('/stocks')
    def stocks(): return manager.stocks()
    @router.post('/setup')
    def setup(v:Setup): return manager.setup(v)
    @router.post('/{account}/start')
    def start(account:int):
        controller=getattr(manager,'runtime_controller',manager)
        (controller.resume(account) if hasattr(controller,'resume') else manager.start(account))
        return {'started':True}
    @router.post('/{account}/pause')
    def pause(account:int):
        getattr(manager,'runtime_controller',manager).pause(account)
        return {'paused':True}
    @router.post('/{account}/close')
    def close(account:int):
        controller=getattr(manager,'runtime_controller',None)
        controller.pause(account,close=True) if controller else manager.close(account)
        return {'exit_requested':True}
    app.include_router(router)
