CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
INSERT OR IGNORE INTO users VALUES(1,'Local researcher');
CREATE TABLE IF NOT EXISTS instruments(
 id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('SPOT','INDEX','FUTURE','CALL','PUT')),
 lot_size INTEGER NOT NULL CHECK(lot_size>0), tick_paise INTEGER NOT NULL CHECK(tick_paise>0),
 expiry TEXT, strike_paise INTEGER, underlying TEXT, settlement TEXT NOT NULL DEFAULT 'CASH',
 provider_key TEXT, metadata_source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS instrument_expiry ON instruments(expiry,strike_paise,kind);
CREATE TABLE IF NOT EXISTS datasets(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
 created_at TEXT NOT NULL, timeframe TEXT NOT NULL, rows INTEGER NOT NULL, warnings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candles(
 id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets(id), instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 timestamp TEXT NOT NULL, available_at TEXT NOT NULL, timeframe TEXT NOT NULL,
 open INTEGER NOT NULL CHECK(open>0), high INTEGER NOT NULL CHECK(high>0), low INTEGER NOT NULL CHECK(low>0), close INTEGER NOT NULL CHECK(close>0),
 volume INTEGER NOT NULL CHECK(volume>=0), open_interest INTEGER,
 UNIQUE(instrument_id,timeframe,timestamp), CHECK(high>=open AND high>=close AND low<=open AND low<=close AND high>=low)
);
CREATE INDEX IF NOT EXISTS candles_lookup ON candles(instrument_id,timeframe,available_at);
CREATE TABLE IF NOT EXISTS paper_accounts(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id), name TEXT NOT NULL,
 initial_capital INTEGER NOT NULL CHECK(initial_capital>0), cash_balance INTEGER NOT NULL,
 realized_pnl INTEGER NOT NULL DEFAULT 0, costs INTEGER NOT NULL DEFAULT 0,
 halted INTEGER NOT NULL DEFAULT 0, halt_reason TEXT, mode TEXT NOT NULL CHECK(mode IN ('REPLAY','LIVE')),
 clock TEXT, dataset_id INTEGER REFERENCES datasets(id), cursor INTEGER NOT NULL DEFAULT -1,
 risk_json TEXT NOT NULL, cost_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS capital_immutable BEFORE UPDATE OF initial_capital,mode,dataset_id ON paper_accounts BEGIN SELECT RAISE(ABORT,'Initial capital and experiment identity are immutable'); END;
CREATE TABLE IF NOT EXISTS paper_orders(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_accounts(id), instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 side TEXT NOT NULL CHECK(side IN ('BUY','SELL')), quantity INTEGER NOT NULL CHECK(quantity>0),
 order_type TEXT NOT NULL CHECK(order_type IN ('MARKET','LIMIT')), limit_paise INTEGER, stop_paise INTEGER, target_paise INTEGER, trail_paise INTEGER,
 status TEXT NOT NULL CHECK(status IN ('PENDING','FILLED','CANCELLED','REJECTED')), reason TEXT, strategy TEXT NOT NULL,
 created_at TEXT NOT NULL, filled_at TEXT, price INTEGER, fees INTEGER, source_ref TEXT,
 request_key TEXT NOT NULL, UNIQUE(account_id,request_key)
);
CREATE TABLE IF NOT EXISTS paper_positions(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_accounts(id), instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 quantity INTEGER NOT NULL CHECK(quantity>0), cost_basis INTEGER NOT NULL, entry_price INTEGER NOT NULL, entry_fees INTEGER NOT NULL,
 stop_paise INTEGER NOT NULL, target_paise INTEGER, trail_paise INTEGER, high_water INTEGER NOT NULL,
 opened_at TEXT NOT NULL, strategy TEXT NOT NULL, UNIQUE(account_id,instrument_id)
);
CREATE TABLE IF NOT EXISTS paper_trades(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_accounts(id), instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 entry_time TEXT NOT NULL, exit_time TEXT NOT NULL, entry_price INTEGER NOT NULL, exit_price INTEGER NOT NULL,
 quantity INTEGER NOT NULL, gross_pnl INTEGER NOT NULL, fees INTEGER NOT NULL, net_pnl INTEGER NOT NULL, strategy TEXT NOT NULL,
 order_id INTEGER NOT NULL REFERENCES paper_orders(id)
);
CREATE TABLE IF NOT EXISTS account_ledger(
 event_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_accounts(id), timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
 symbol TEXT, quantity INTEGER NOT NULL, price INTEGER NOT NULL, gross_amount INTEGER NOT NULL, fees INTEGER NOT NULL, slippage INTEGER NOT NULL,
 realized_pnl INTEGER NOT NULL, balance_before INTEGER NOT NULL, balance_after INTEGER NOT NULL,
 source TEXT NOT NULL, reference_id TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
 UNIQUE(account_id,reference_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_initial_event ON account_ledger(account_id) WHERE event_type='INITIAL_CAPITAL';
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON account_ledger BEGIN SELECT RAISE(ABORT,'Ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON account_ledger BEGIN SELECT RAISE(ABORT,'Ledger is append-only'); END;
CREATE INDEX IF NOT EXISTS ledger_timestamp ON account_ledger(account_id,timestamp);
CREATE TABLE IF NOT EXISTS risk_events(id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES paper_accounts(id), timestamp TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY, trade_id INTEGER NOT NULL UNIQUE REFERENCES paper_trades(id), notes TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES paper_accounts(id), timestamp TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS data_quality(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, filename TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backtests(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, dataset_id INTEGER NOT NULL REFERENCES datasets(id), strategy TEXT NOT NULL, parameters TEXT NOT NULL, results TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backtest_trades(id INTEGER PRIMARY KEY, backtest_id INTEGER NOT NULL REFERENCES backtests(id), payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_analysis_logs(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL, snapshot_id TEXT NOT NULL, response TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS option_chain(id INTEGER PRIMARY KEY, underlying TEXT NOT NULL, expiry TEXT NOT NULL, timestamp TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chain_lookup ON option_chain(underlying,expiry,timestamp);
CREATE TABLE IF NOT EXISTS indicators(id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id), timestamp TEXT NOT NULL, parameters TEXT NOT NULL, values_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_regimes(id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id), timestamp TEXT NOT NULL, regime TEXT NOT NULL, reasons TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategies(id TEXT PRIMARY KEY, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategy_parameters(id INTEGER PRIMARY KEY, strategy_id TEXT NOT NULL REFERENCES strategies(id), parameters TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id), timestamp TEXT NOT NULL, strategy TEXT NOT NULL, payload TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS option_contracts AS SELECT * FROM instruments WHERE kind IN ('CALL','PUT');
CREATE VIEW IF NOT EXISTS portfolio AS SELECT a.id AS account_id,a.initial_capital,a.cash_balance,a.realized_pnl,a.costs,COALESCE(SUM(p.cost_basis),0) AS invested_cost FROM paper_accounts a LEFT JOIN paper_positions p ON p.account_id=a.id GROUP BY a.id;
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id), timestamp TEXT NOT NULL, received_at TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>0), source TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS quote_no_update BEFORE UPDATE ON quotes BEGIN SELECT RAISE(ABORT,'Quote evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS candle_no_update BEFORE UPDATE ON candles BEGIN SELECT RAISE(ABORT,'Imported candles are immutable'); END;
