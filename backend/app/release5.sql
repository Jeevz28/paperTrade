CREATE TABLE IF NOT EXISTS bhav_securities (
 instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
 dataset_id INTEGER NOT NULL REFERENCES datasets(id), isin TEXT NOT NULL,
 series TEXT NOT NULL, ticker TEXT NOT NULL,
 UNIQUE(dataset_id,isin,series)
);
CREATE INDEX IF NOT EXISTS bhav_dataset ON bhav_securities(dataset_id);
CREATE INDEX IF NOT EXISTS candles_dataset_time ON candles(dataset_id,timestamp);
CREATE TABLE IF NOT EXISTS learning_jobs (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
 progress INTEGER NOT NULL DEFAULT 0, parameters TEXT NOT NULL, result TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS learning_profiles (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES learning_jobs(id),
 dataset_id INTEGER NOT NULL REFERENCES datasets(id), instrument_id INTEGER NOT NULL REFERENCES instruments(id),
 created_at TEXT NOT NULL, trained_through TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS profile_no_update BEFORE UPDATE ON learning_profiles BEGIN SELECT RAISE(ABORT,'Learning evidence immutable'); END;
CREATE TRIGGER IF NOT EXISTS profile_no_delete BEFORE DELETE ON learning_profiles BEGIN SELECT RAISE(ABORT,'Learning evidence immutable'); END;
CREATE TABLE IF NOT EXISTS learning_daily_cache (
 profile_id INTEGER PRIMARY KEY REFERENCES learning_profiles(id),
 fetched_at TEXT NOT NULL, payload TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations VALUES(5,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
PRAGMA user_version=5;
