"""Full-feed budget planner. Pins are never displaced by a rotation."""
from .core import DomainError

FULL_LIMIT = 2000
ROTATE_AFTER_SECONDS = 220 * 60

def batch(universe, pinned=(), cursor=0, limit=FULL_LIMIT):
    items = list(universe.items())
    pinned = set(pinned)
    if not pinned.issubset(set(universe.values())):
        raise DomainError('The stock universe must include every held or pending instrument.')
    if len(pinned) > limit:
        raise DomainError('Open exposure exceeds the full-feed subscription budget.')
    selected = {key: i for key, i in items if i in pinned}
    if not items:
        return selected, 0
    cursor %= len(items)
    walked = 0
    while len(selected) < min(limit, len(items)) and walked < len(items):
        key, i = items[(cursor + walked) % len(items)]
        selected[key] = i
        walked += 1
    return selected, (cursor + walked) % len(items)
