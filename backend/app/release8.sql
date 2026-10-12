CREATE TABLE IF NOT EXISTS market_history (
 instrument_id INTEGER NOT NULL REFERENCES instruments(id), timestamp TEXT NOT NULL,
 available_at TEXT NOT NULL, open INTEGER NOT NULL, high INTEGER NOT NULL,
 low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER NOT NULL,
 source TEXT NOT NULL, recorded_at TEXT NOT NULL,
 PRIMARY KEY(instrument_id,timestamp,source)
);
CREATE TABLE IF NOT EXISTS runtime_preferences (
 name TEXT PRIMARY KEY, value TEXT NOT NULL
);
PRAGMA user_version=8;
CREATE TABLE IF NOT EXISTS fno_reservations (
 basket_id INTEGER PRIMARY KEY REFERENCES baskets(id),
 premium INTEGER NOT NULL, margin INTEGER NOT NULL, estimated_fee INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS quotes_instrument_time ON quotes(instrument_id,timestamp);
