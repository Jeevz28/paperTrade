CREATE TABLE IF NOT EXISTS adaptive_state (
 rule_id INTEGER PRIMARY KEY REFERENCES automation_rules(id),
 payload TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations VALUES(4,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
PRAGMA user_version=4;
