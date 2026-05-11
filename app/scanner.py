import re
from pathlib import Path

from .database import get_conn

# Strip common YouTube/promo descriptors from titles
_JUNK = re.compile(
    r'\s*[\(\[]\s*(?:'
    r'official\s*(?:music\s*)?(?:video|audio|visuali[sz]er?|lyric\s*video|hd)?|'
    r'music\s*video|lyric\s*video|lyric\s*vid|audio|visuali[sz]er?|'
    r'live\s*[@\-\|].*?|'
    r'hd|4k|song'
    r')\s*[\)\]]',
    re.IGNORECASE,
)

_YEAR = re.compile(r'[\[\(]((?:19|20)\d{2})[\]\)]')

# Map full-width Unicode punctuation seen in video filenames to ASCII
_FW = str.maketrans({'：': ':', '＂': '"', '？': '?', '！': '!', '，': ',', '．': '.'})

_SEPS = [' - ', ' — ', '— ']


def parse_filename(filename: str) -> dict:
    stem = Path(filename).stem
    stem = stem.translate(_FW)
    stem = re.sub(r'\s+', ' ', stem).strip()

    # Extract year (last match wins so we get the rightmost [YYYY])
    year = None
    for m in _YEAR.finditer(stem):
        year = int(m.group(1))
        last_match = m
    if year is not None:
        stem = (stem[: last_match.start()] + stem[last_match.end() :]).strip()

    # Strip junk descriptors
    stem = _JUNK.sub('', stem).strip()

    # Split artist / title on first separator
    artist, title = None, stem
    for sep in _SEPS:
        if sep in stem:
            artist, title = stem.split(sep, 1)
            artist = artist.strip()
            title = title.strip()
            break

    # Second pass: strip any remaining junk from the title
    title = _JUNK.sub('', title).strip().strip(' -').strip()

    return {
        'filename': filename,
        'artist': artist or 'Unknown',
        'title': title or stem,
        'year': year,
    }


def scan_library(media_dir: Path) -> int:
    """Scan media_dir for .mp4 files and upsert into DB. Returns new file count."""
    if not media_dir.exists():
        print(f"[scanner] MEDIA_DIR not found: {media_dir}")
        return 0

    files = sorted(media_dir.glob('*.mp4'))
    new_count = 0

    with get_conn() as conn:
        existing = {row[0] for row in conn.execute('SELECT filename FROM videos')}
        for f in files:
            if f.name in existing:
                continue
            parsed = parse_filename(f.name)
            conn.execute(
                """INSERT INTO videos (filename, artist, title, year)
                   VALUES (:filename, :artist, :title, :year)
                   ON CONFLICT(filename) DO UPDATE SET
                     artist = excluded.artist,
                     title  = excluded.title,
                     year   = excluded.year""",
                parsed,
            )
            new_count += 1

    print(f"[scanner] {len(files)} files found, {new_count} new")
    return new_count
