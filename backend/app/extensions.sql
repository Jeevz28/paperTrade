CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS margin_evidence(
 id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 available_at TEXT NOT NULL, valid_from TEXT NOT NULL, valid_until TEXT NOT NULL,
 initial_per_lot INTEGER NOT NULL CHECK(initial_per_lot>0), maintenance_per_lot INTEGER NOT NULL CHECK(maintenance_per_lot>0),
 source TEXT NOT NULL, CHECK(initial_per_lot>=maintenance_per_lot), CHECK(valid_until>valid_from)
);
CREATE TABLE IF NOT EXISTS settlement_evidence(
 id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 effective_at TEXT NOT NULL, available_at TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>=0),
 final INTEGER NOT NULL CHECK(final IN (0,1)), source TEXT NOT NULL,
 UNIQUE(instrument_id,effective_at), CHECK(available_at>=effective_at)
);
CREATE TABLE IF NOT EXISTS baskets(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_accounts(id), strategy TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','OPEN','CLOSING','CLOSED','CANCELLED','REJECTED')),
 legs_json TEXT NOT NULL, stop_paise INTEGER, created_at TEXT NOT NULL, opened_at TEXT, close_requested_at TEXT,
 request_key TEXT NOT NULL, reason TEXT, UNIQUE(account_id,request_key)
);
CREATE TABLE IF NOT EXISTS basket_positions(
 id INTEGER PRIMARY KEY, basket_id INTEGER NOT NULL REFERENCES baskets(id), instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 signed_quantity INTEGER NOT NULL CHECK(signed_quantity<>0), basis_price INTEGER NOT NULL CHECK(basis_price>=0),
 book_cost INTEGER NOT NULL, margin INTEGER NOT NULL DEFAULT 0 CHECK(margin>=0),
 entry_fees INTEGER NOT NULL, last_settlement_id INTEGER REFERENCES settlement_evidence(id),
 UNIQUE(basket_id,instrument_id)
);
CREATE TABLE IF NOT EXISTS basket_events(
 id INTEGER PRIMARY KEY, basket_id INTEGER NOT NULL REFERENCES baskets(id), timestamp TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS basket_trades(
 id INTEGER PRIMARY KEY, basket_id INTEGER NOT NULL REFERENCES baskets(id), timestamp TEXT NOT NULL,
 gross_pnl INTEGER NOT NULL, fees INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS option_history(
 id INTEGER PRIMARY KEY, instrument_id INTEGER NOT NULL REFERENCES instruments(id), timestamp TEXT NOT NULL,
 iv REAL NOT NULL CHECK(iv>0 AND iv<=5), open_interest INTEGER NOT NULL CHECK(open_interest>=0), source TEXT NOT NULL,
 UNIQUE(instrument_id,timestamp)
);
CREATE TABLE IF NOT EXISTS session_calendar(session_date TEXT PRIMARY KEY, opens_at TEXT NOT NULL, closes_at TEXT NOT NULL, source TEXT NOT NULL, CHECK(closes_at>opens_at));
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_accounts(id), timestamp TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(account_id,timestamp));
CREATE TRIGGER IF NOT EXISTS margin_immutable_update BEFORE UPDATE ON margin_evidence BEGIN SELECT RAISE(ABORT,'Evidence immutable'); END;
CREATE TRIGGER IF NOT EXISTS margin_immutable_delete BEFORE DELETE ON margin_evidence BEGIN SELECT RAISE(ABORT,'Evidence immutable'); END;
CREATE TRIGGER IF NOT EXISTS settlement_immutable_update BEFORE UPDATE ON settlement_evidence BEGIN SELECT RAISE(ABORT,'Evidence immutable'); END;
CREATE TRIGGER IF NOT EXISTS settlement_immutable_delete BEFORE DELETE ON settlement_evidence BEGIN SELECT RAISE(ABORT,'Evidence immutable'); END;
CREATE TRIGGER IF NOT EXISTS basket_event_immutable_update BEFORE UPDATE ON basket_events BEGIN SELECT RAISE(ABORT,'Evidence immutable'); END;
CREATE TRIGGER IF NOT EXISTS basket_event_immutable_delete BEFORE DELETE ON basket_events BEGIN SELECT RAISE(ABORT,'Evidence immutable'); END;
CREATE INDEX IF NOT EXISTS margin_asof ON margin_evidence(instrument_id,available_at,valid_from,valid_until);
CREATE INDEX IF NOT EXISTS settlement_asof ON settlement_evidence(instrument_id,available_at,effective_at);
INSERT OR IGNORE INTO schema_migrations VALUES(2,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
PRAGMA user_version=2;
