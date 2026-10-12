from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Database:
    def __init__(self, path="data/trading.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 8:
                raise RuntimeError("Database schema is newer than this application")
            db.executescript(Path(__file__).with_name("schema.sql").read_text())
            db.executescript(
                "BEGIN IMMEDIATE;\n"
                + Path(__file__).with_name("extensions.sql").read_text()
                + Path(__file__).with_name("release3.sql").read_text()
                + Path(__file__).with_name("release4.sql").read_text()
                + Path(__file__).with_name("release5.sql").read_text()
                + Path(__file__).with_name("release6.sql").read_text()
                + Path(__file__).with_name("release7.sql").read_text()
                + Path(__file__).with_name("release8.sql").read_text()
                + "\nCOMMIT;"
            )

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, params=()):
        with self.connect() as db:
            return [dict(x) for x in db.execute(sql, params)]
