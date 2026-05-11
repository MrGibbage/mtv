# Personal MTV

A self-hosted music video jukebox with a TV view and a mobile companion. One Docker container, deployed on a homelab VM, proxied through Caddy.

## Views

| Route | Description |
|-------|-------------|
| `/tv` | Fullscreen video player — auto-advances through the library in weighted-random order |
| `/` | Mobile companion — shows album art, artist, title, thumbs up/down; syncs live to the TV via SSE |
| `/artist/<name>` | All videos by an artist, with ratings |

## Stack

- **Python / FastAPI** — API and page serving
- **SQLite** — metadata, ratings, play history
- **Server-Sent Events** — real-time sync from TV to mobile companion
- **Vanilla JS** — no build step, no framework
- **Docker** — single container with a named volume for the DB

## Video library

MP4 files on a Synology NAS, mounted read-only into the container. The scanner runs at startup and upserts any new files — just drop videos in the folder and restart the container.

Filename format (mixed styles handled):
```
Artist - Song Title (Year).mp4
Artist - Song Title [Year].mp4
Artist - Song Title.mp4
```

## Shuffle weighting

- Thumbs up → 1.5× weight
- Thumbs down → 0.1× weight
- Played in last 50 videos → 0.1× multiplier (avoids repeats within a session)

## Deployment

```yaml
# compose.yml
services:
  mtv:
    build: .
    container_name: mtv
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - mtv-data:/app/data
      - /mnt/pierhouse:/mnt/pierhouse:ro
    environment:
      - DATA_DIR=/app/data
      - MEDIA_DIR=/mnt/pierhouse/media/Music Videos - Alternative

volumes:
  mtv-data:
```

```bash
docker compose up --build -d
```

## Metadata enrichment

Run `enrich.py` (Phase 2) to pull canonical metadata from MusicBrainz and album art / bio from Last.fm. The app works without it — enrichment just fills in the album art on the mobile companion.

```bash
docker exec mtv python enrich.py
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/advance` | Pick next video (weighted shuffle), broadcast via SSE |
| `GET` | `/api/now-playing` | Current video state |
| `POST` | `/api/rate/{id}` | Rate a video: `{"rating": 1\|-1\|0}` |
| `GET` | `/api/events` | SSE stream — `now-playing` events |
| `GET` | `/api/scan` | Rescan the media directory |
| `GET` | `/video/{id}` | Stream video with HTTP Range support |
