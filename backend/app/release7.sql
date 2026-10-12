CREATE TABLE IF NOT EXISTS fno_sessions (
 account_id INTEGER PRIMARY KEY REFERENCES paper_accounts(id),
 market TEXT NOT NULL CHECK(market IN ('NIFTY','STOCK')),
 underlying_key TEXT NOT NULL, label TEXT NOT NULL,
 product TEXT NOT NULL CHECK(product IN ('AUTO','OPTIONS','FUTURES')),
 underlying_id INTEGER REFERENCES instruments(id),
 enabled INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT '{}', prepared_day TEXT,
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_nifty_default ON fno_sessions(market) WHERE market='NIFTY';
CREATE TABLE IF NOT EXISTS fno_contracts (
 account_id INTEGER NOT NULL REFERENCES fno_sessions(account_id),
 instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 PRIMARY KEY(account_id,instrument_id)
);
CREATE TABLE IF NOT EXISTS fno_tickets (
 basket_id INTEGER PRIMARY KEY REFERENCES baskets(id),
 signal TEXT NOT NULL, signal_at TEXT NOT NULL,
 stop_paise INTEGER NOT NULL, target_paise INTEGER NOT NULL,
 expires_at TEXT NOT NULL, reason TEXT
);
PRAGMA user_version=7;
