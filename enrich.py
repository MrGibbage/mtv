#!/usr/bin/env python3
"""
Enrich video metadata with MusicBrainz genre tags and Last.fm artist art/bio.

- MusicBrainz: artist-level genre tags (no key needed, 1 req/sec limit)
- Last.fm:     artist image URL and bio snippet

Safe to re-run: skips already-enriched videos by default.

Usage:
  python enrich.py              # enrich un-enriched videos only
  python enrich.py --all        # force re-enrich everything
  python enrich.py --limit 20   # process at most N videos (good for testing)
"""

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


# ── Config ────────────────────────────────────────────────────────────────────

def _load_env(path: Path) -> dict:
    env = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"\'')
    except FileNotFoundError:
        pass
    return env


_env = _load_env(Path(__file__).parent / '.env')
LASTFM_KEY = os.getenv('LASTFM_API_KEY') or _env.get('LASTFM_API_KEY')
DB_PATH = Path(os.getenv('DATA_DIR', '/app/data')) / 'mtv.db'

LASTFM_BASE = 'https://ws.audioscrobbler.com/2.0/'
MB_BASE = 'https://musicbrainz.org/ws/2/'
MB_UA = 'PersonalMTV/1.0 (skip.morrow.mobile@gmail.com)'

# Last.fm placeholder image — not a real artist photo
_LFM_PLACEHOLDER = '2a96cbd8b46e442fc41c2b86b821562f'


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {url}") from e


def _lastfm(method: str, **params) -> dict:
    params.update({'method': method, 'api_key': LASTFM_KEY, 'format': 'json'})
    return _get(LASTFM_BASE + '?' + urllib.parse.urlencode(params))


def _mb(entity: str, **params) -> dict:
    params['fmt'] = 'json'
    url = MB_BASE + entity + '?' + urllib.parse.urlencode(params)
    return _get(url, headers={'User-Agent': MB_UA})


# ── Enrichment logic ──────────────────────────────────────────────────────────

def fetch_lastfm(artist: str) -> tuple[str | None, str | None]:
    """Return (image_url, bio) for an artist from Last.fm."""
    try:
        data = _lastfm('artist.getinfo', artist=artist, autocorrect=1)
        a = data.get('artist', {})

        # Pick largest non-placeholder image
        art_url = None
        for size in ('extralarge', 'large', 'medium'):
            for img in a.get('image', []):
                if img.get('size') == size and img.get('#text'):
                    url = img['#text']
                    if _LFM_PLACEHOLDER not in url:
                        art_url = url
                        break
            if art_url:
                break

        bio = a.get('bio', {}).get('summary', '') or ''
        # Strip trailing "Read more on Last.fm" link
        if '<a href=' in bio:
            bio = bio[:bio.index('<a href=')].strip()
        bio = bio.strip() or None

        return art_url, bio
    except Exception as e:
        print(f"    Last.fm error: {e}")
        return None, None


def fetch_mb_tags(artist: str) -> str | None:
    """Return comma-separated genre tags for an artist from MusicBrainz."""
    try:
        data = _mb('artist', query=f'artist:"{artist}"', limit=1, inc='tags')
        artists = data.get('artists', [])
        if not artists:
            return None
        tags = sorted(artists[0].get('tags', []), key=lambda t: t.get('count', 0), reverse=True)
        return ', '.join(t['name'] for t in tags[:5]) or None
    except Exception as e:
        print(f"    MusicBrainz error: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--all',   action='store_true', help='Re-enrich already-enriched videos')
    parser.add_argument('--limit', type=int, default=0, help='Stop after N videos (0 = no limit)')
    args = parser.parse_args()

    if not LASTFM_KEY:
        print("ERROR: LASTFM_API_KEY not set. Add it to .env or set the environment variable.")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    query = "SELECT id, artist, title FROM videos"
    if not args.all:
        query += " WHERE enriched_at IS NULL"
    query += " ORDER BY artist, title"
    rows = conn.execute(query).fetchall()

    if args.limit:
        rows = rows[:args.limit]

    total = len(rows)
    print(f"Enriching {total} videos (MusicBrainz + Last.fm)...\n")

    # Cache per artist — both APIs give per-artist data
    artist_cache: dict[str, tuple] = {}
    enriched = skipped = 0

    for i, row in enumerate(rows, 1):
        vid_id, artist, title = row['id'], row['artist'], row['title']
        print(f"[{i}/{total}] {artist} — {title}")

        if artist not in artist_cache:
            art_url, bio = fetch_lastfm(artist)
            time.sleep(0.25)                        # Last.fm: polite delay

            genre = fetch_mb_tags(artist)
            time.sleep(1.1)                         # MusicBrainz: max 1 req/sec

            artist_cache[artist] = (art_url, bio, genre)
            status = f"  art={'✓' if art_url else '✗'}  bio={'✓' if bio else '✗'}  genre={genre or '—'}"
            print(status)
        else:
            art_url, bio, genre = artist_cache[artist]
            print("  (cached)")

        conn.execute(
            "UPDATE videos SET lastfm_art_url=?, lastfm_bio=?, genre=?, enriched_at=datetime('now') WHERE id=?",
            (art_url, bio, genre, vid_id),
        )
        conn.commit()
        enriched += 1

    print(f"\nDone. {enriched} videos updated, {len(artist_cache)} unique artists looked up.")
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
