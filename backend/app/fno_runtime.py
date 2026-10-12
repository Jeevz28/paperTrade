"""Environment-owned capital and unattended paper startup; never a broker order."""
import json
import os
import threading
from decimal import Decimal, InvalidOperation
from .core import DomainError, money
from .fno import Setup

class FnoRuntime:
    def __init__(self, manager):
        self.manager=manager; self.db=manager.db
        self.stop_event=threading.Event(); self.thread=None
        self.info=dict(status='STARTING',message='Reading initial capital from .env.',account_id=None,configured_capital_paise=None,capital_mismatch=False)
        manager.runtime_controller=self

    def configured_capital(self):
        value=os.getenv('PAPER_INITIAL_CAPITAL','5000')
        try:
            d=Decimal(value)
            if not d.is_finite() or d<1 or d>100000000 or d.as_tuple().exponent < -2: raise ValueError()
            return float(d)
        except (InvalidOperation, ValueError):
            raise DomainError('PAPER_INITIAL_CAPITAL must be INR 1–100000000 with at most two decimal places')

    def paused(self):
        rows=self.db.rows("SELECT value FROM runtime_preferences WHERE name='paused'")
        return bool(rows and rows[0]['value']=='true')

    def set_pause(self, value):
        with self.db.connect(write=True) as c:
            c.execute("INSERT INTO runtime_preferences VALUES('paused',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(value),))

    def configure(self):
        capital=self.configured_capital()
        a=self.manager.setup(Setup(initial_capital=capital))['account_id']
        old=self.db.rows('SELECT initial_capital FROM paper_accounts WHERE id=?',(a,))[0]['initial_capital']
        mismatch=old!=money(capital)
        self.info.update(account_id=a,configured_capital_paise=money(capital),capital_mismatch=mismatch)
        if mismatch:
            raise DomainError(f'Existing account began with INR {old/100:.2f}; .env capital is INR {capital:.2f}. Restore the original value. Existing capital and loss history cannot be reset by editing .env.')
        return a

    def boot(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        self.thread=threading.Thread(target=self.loop,daemon=True,name='paperlab-autostart')
        self.thread.start()

    def protect_existing(self):
        held=self.db.rows("SELECT DISTINCT b.account_id FROM baskets b JOIN fno_tickets t ON t.basket_id=b.id WHERE b.status IN ('OPEN','CLOSING')")
        if held and self.manager.stream.state not in ('CONNECTED','CONNECTING','RECONNECTING'):
            self.manager.connect(held[0]['account_id'])
            self.manager.ensure_worker()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                a=self.configure()
                if self.paused():
                    self.info.update(status='PAUSED',message='Your pause is retained. Recording/protection can continue; Resume enables entries.')
                    if self.manager.stream.state not in ('CONNECTED','CONNECTING','RECONNECTING'):
                        if not self.manager.active(a): self.manager.prepare(a)
                        self.manager.connect(a)
                        self.manager.ensure_worker()
                else:
                    self.manager.provider.policy.require_live()
                    self.manager.provider.enabled()
                    self.manager.start(a)
                    if self.paused():
                        self.manager.pause(a)
                        continue
                    self.info.update(status='RUNNING',message='Automatic F&O paper monitoring started. Waiting for valid data, strategy and risk approval.')
                    return
            except Exception as e:
                self.info.update(status='CONFIGURATION_REQUIRED' if isinstance(e,DomainError) else 'RETRYING',message=str(e) if isinstance(e,DomainError) else 'Startup failed: '+type(e).__name__)
                try: self.protect_existing()
                except Exception: pass
            if self.stop_event.wait(30): return

    def resume(self, account):
        a=self.configure()
        if account!=a: raise DomainError('Only the environment-configured default account accepts entry controls')
        self.set_pause(False)
        self.manager.start(account)
        self.info.update(status='RUNNING',message='Automatic paper entries enabled.')

    def pause(self, account, close=False):
        self.set_pause(True)
        if close: self.manager.close(account)
        else: self.manager.pause(account)
        self.info.update(status='PAUSED',message='New entries paused. Keep the app running for recording and protective exits.')

    def shutdown(self):
        self.manager.shutting_down=True
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=35)
