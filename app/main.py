import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .database import get_conn, init_db
from .scanner import scan_library
from .shuffle import pick_next

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/media/Music Videos - Alternative"))

templates = Jinja2Templates(directory="templates")

now_playing: dict | None = None
sse_queues: list[asyncio.Queue] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scan_library(MEDIA_DIR)
    yield


app = FastAPI(lifespan=lifespan)


async def _broadcast(data: dict):
    for q in sse_queues:
        await q.put(data)


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def mobile_view(request: Request):
    return templates.TemplateResponse("mobile.html", {"request": request})


@app.get("/tv", response_class=HTMLResponse)
async def tv_view(request: Request):
    return templates.TemplateResponse("tv.html", {"request": request})


@app.get("/artist/{name}", response_class=HTMLResponse)
async def artist_view(request: Request, name: str):
    with get_conn() as conn:
        videos = conn.execute(
            "SELECT * FROM videos WHERE artist = ? ORDER BY year, title",
            (name,),
        ).fetchall()
    return templates.TemplateResponse(
        "artist.html",
        {"request": request, "artist": name, "videos": [dict(v) for v in videos]},
    )


# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/now-playing")
async def get_now_playing():
    if now_playing is None:
        return JSONResponse({"playing": False})
    return JSONResponse({**now_playing, "playing": True})


@app.post("/api/advance")
async def advance():
    global now_playing
    video = pick_next()
    if video is None:
        raise HTTPException(503, "No videos in library")
    now_playing = video

    with get_conn() as conn:
        conn.execute("INSERT INTO play_history (video_id) VALUES (?)", (video["id"],))
        conn.execute(
            "UPDATE videos SET play_count = play_count + 1 WHERE id = ?", (video["id"],)
        )

    await _broadcast({"type": "now-playing", **video})
    return JSONResponse(video)


@app.post("/api/rate/{video_id}")
async def rate_video(video_id: int, request: Request):
    body = await request.json()
    rating = body.get("rating", 0)
    if rating not in (-1, 0, 1):
        raise HTTPException(400, "rating must be -1, 0, or 1")
    with get_conn() as conn:
        conn.execute("UPDATE videos SET rating = ? WHERE id = ?", (rating, video_id))
    return JSONResponse({"ok": True})


@app.get("/api/scan")
async def trigger_scan():
    count = scan_library(MEDIA_DIR)
    return JSONResponse({"new_files": count})


# ── SSE ───────────────────────────────────────────────────────────────────────

@app.get("/api/events")
async def sse(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    sse_queues.append(q)

    async def stream():
        try:
            # Send current state immediately on connect
            if now_playing:
                yield f"data: {json.dumps({'type': 'now-playing', **now_playing})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                sse_queues.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Video streaming ───────────────────────────────────────────────────────────

@app.get("/video/{video_id}")
async def stream_video(video_id: int, request: Request):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        raise HTTPException(404)

    file_path = MEDIA_DIR / row["filename"]
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range")

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not m:
            raise HTTPException(416)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        async def ranged():
            async with aiofiles.open(file_path, "rb") as f:
                await f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = await f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            ranged(),
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            },
        )

    async def full():
        async with aiofiles.open(file_path, "rb") as f:
            while chunk := await f.read(65536):
                yield chunk

    return StreamingResponse(
        full(),
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )
