
CREATE TABLE IF NOT EXISTS auto_sessions (
 account_id INTEGER PRIMARY KEY REFERENCES paper_accounts(id),
 dataset_id INTEGER REFERENCES datasets(id), enabled INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auto_universe (
 account_id INTEGER NOT NULL REFERENCES auto_sessions(account_id),
 instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 source_id INTEGER NOT NULL REFERENCES instruments(id),
 status TEXT NOT NULL DEFAULT 'WAITING', detail TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(account_id,instrument_id)
);
PRAGMA user_version=6;
