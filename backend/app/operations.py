import sqlite3
import uuid
from pathlib import Path
from .core import DomainError


def backup_database(db):
    directory = Path(db.path).resolve().parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"paperlab-{uuid.uuid4().hex}.sqlite"
    with db.connect() as source, sqlite3.connect(destination) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DomainError("Backup integrity check failed")
    return destination


def restore_copy(source, destination):
    """Restore to a NEW path only; never overwrite a running experiment."""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise DomainError("Restore destination already exists")
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as old:
        if old.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise DomainError("Backup is corrupt")
        if old.execute("PRAGMA user_version").fetchone()[0] > 8:
            raise DomainError("Backup version is newer than this application")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as new:
            old.backup(new)
    return destination
