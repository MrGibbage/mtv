import random
from collections import deque

from .database import get_conn

_recent: deque[int] = deque(maxlen=50)


def pick_next() -> dict | None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, artist, title, year, rating, lastfm_art_url FROM videos"
        ).fetchall()

    if not rows:
        return None

    videos = [dict(r) for r in rows]
    recent_set = set(_recent)

    weights = []
    for v in videos:
        if v['rating'] == 1:
            w = 1.5
        elif v['rating'] == -1:
            w = 0.1
        else:
            w = 1.0
        if v['id'] in recent_set:
            w *= 0.1
        weights.append(w)

    chosen = random.choices(videos, weights=weights, k=1)[0]
    _recent.append(chosen['id'])
    return chosen
