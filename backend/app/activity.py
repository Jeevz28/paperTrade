import json
from .core import now


def record(db, category, action, details, account_id=None):
    with db.connect(write=True) as c:
        c.execute(
            "INSERT INTO activity_events(timestamp,account_id,category,action,details) VALUES(?,?,?,?,?)",
            (
                now().isoformat(),
                account_id,
                category,
                action,
                json.dumps(details, sort_keys=True),
            ),
        )
