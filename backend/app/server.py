"""HTTP + WebSocket API.

REST:  GET  /api/maps, GET /api/maps/{id}, GET /api/rooms
       POST /api/rooms            {room_name, player_name, map_id, max_players, turn_seconds, rounds, private}
       POST /api/rooms/{code}/join {player_name}
       -> {code, player_id, token}
WS:    /ws/{code}?token=...   client -> {"t": "profile"|"ready"|"start"|"add_bot"|"kick"|"leave"|"submit"|"unsubmit"|"next"|"chat"|
                                        "propose"|"respond"|"leave_coalition"|"cabinet_propose"|"cabinet_answer", ...}
                              server -> {"t": "state", room, game, you} | {"t": "error", msg}
"""
import asyncio
import contextlib
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .engine import ActionError
from .maps import list_maps, load_map
from .rooms import RoomError, RoomManager

FRONTEND = Path(os.environ.get("ELECTION_FRONTEND") or Path(__file__).resolve().parent.parent.parent / "frontend")
# Where invite links should point when the server's own origin is not what friends can open
# (the desktop app's window talks to 127.0.0.1, but players join via the LAN address).
PUBLIC_URL = os.environ.get("ELECTION_PUBLIC_URL", "").strip().rstrip("/")
VERSION = "1.1.0"
persistence = store.build()
manager = RoomManager(persistence)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or secrets.token_urlsafe(12)
if not os.environ.get("ADMIN_TOKEN"):
    print(f"\n  Admin page: /admin   token: {ADMIN_TOKEN}   (set ADMIN_TOKEN to choose your own)\n", flush=True)


async def ticker():
    """Resolves rounds whose timer expired and garbage-collects idle rooms."""
    while True:
        await asyncio.sleep(1)
        now = time.time()
        for room in list(manager.rooms.values()):
            humans_online = any(m.ws is not None for m in room.members.values() if not m.is_bot)
            if room.game and room.phase == "planning" and not room.game.finished and room.deadline \
                    and now >= room.deadline and humans_online:
                async with room.lock:
                    if room.phase == "planning" and room.deadline and now >= room.deadline and not room.game.finished:
                        room.resolve()
                        manager.mark(room, urgent=True)   # a resolved round is worth not losing
                await room.broadcast()
        manager.cleanup()


@contextlib.asynccontextmanager
async def lifespan(app):
    if persistence.enabled:
        restored = manager.restore()
        print(f"  Persistence: {store.db_path_from_env()}" + (f"  ({restored} room(s) restored)" if restored else ""),
              flush=True)
        persistence.start()
    task = asyncio.create_task(ticker())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await persistence.stop()          # last flush, then close the database cleanly


app = FastAPI(title="Election Game", lifespan=lifespan)

# Map JSON is 60-136 KB and the vendored three.js is 672 KB: gzip cuts both by ~75%,
# which is the single biggest win on a phone's connection. Small responses are left alone.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# The Android build serves its own assets from https://localhost inside the WebView, so every
# /api call from the packaged app is cross-origin and needs CORS (WebSockets do not). Set
# ALLOWED_ORIGINS (comma-separated) to add your own web origin.
MOBILE_ORIGINS = ["capacitor://localhost", "https://localhost", "http://localhost", "ionic://localhost"]
ALLOWED_ORIGINS = MOBILE_ORIGINS + [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^https?://localhost(:\d+)?$",
    allow_credentials=False,          # the session token travels in the body/query, never a cookie
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Token"],
    max_age=86400,
)


class CreateRoom(BaseModel):
    room_name: str = ""
    player_name: str
    map_id: str = "turkey"
    max_players: int = 6
    turn_seconds: int = 120
    rounds: int = 20
    private: bool = False


class JoinRoom(BaseModel):
    player_name: str


@app.get("/api/maps")
def maps():
    return list_maps()


@app.get("/api/maps/{map_id}")
def map_detail(map_id: str):
    try:
        return load_map(map_id)
    except KeyError:
        raise HTTPException(404, "unknown map")


@app.get("/api/health")
def health():
    """Cheap liveness probe. The Android app pings this to check a server address."""
    return {"ok": True, "version": VERSION, "rooms": len(manager.rooms), "persistent": persistence.enabled}


@app.get("/api/rooms")
def rooms():
    return manager.public_list()


@app.post("/api/rooms")
def create_room(req: CreateRoom):
    try:
        room = manager.create(req.room_name, req.map_id, req.max_players, req.turn_seconds, req.rounds, req.private)
    except KeyError:
        raise HTTPException(404, "unknown map")
    m = room.add_member(req.player_name)
    manager.mark(room, urgent=True)
    return {"code": room.code, "player_id": m.id, "token": m.token}


@app.post("/api/rooms/{code}/join")
def join_room(code: str, req: JoinRoom):
    try:
        room = manager.get(code)
        m = room.add_member(req.player_name)
    except RoomError as e:
        raise HTTPException(400, str(e))
    room.last_activity = time.time()
    manager.mark(room, urgent=True)
    return {"code": room.code, "player_id": m.id, "token": m.token}


def require_admin(token: str | None) -> None:
    if not token or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "bad admin token")


@app.get("/api/admin/rooms")
def admin_rooms(x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    return manager.admin_list()


@app.delete("/api/admin/rooms/{code}")
async def admin_delete_room(code: str, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    try:
        await manager.close(code.upper())
    except RoomError as e:
        raise HTTPException(404, str(e))
    return {"deleted": code.upper()}


async def handle(room, me, msg: dict) -> bool:
    """Apply one client message. Returns False when the client left."""
    t = msg.get("t")
    if t == "profile":
        room.set_profile(me.id, msg)
    elif t == "ready":
        me.ready = bool(msg.get("ready"))
    elif t == "start":
        room.start(me.id)
    elif t == "add_bot":
        if me.id != room.host_id:
            raise RoomError("only the host can add bots")
        room.add_member("", is_bot=True, level=str(msg.get("level", "normal")))
    elif t == "kick":
        if me.id != room.host_id or room.game:
            raise RoomError("cannot kick now")
        target = room.members.get(msg.get("id"))
        if target and target.id != me.id:
            if target.ws:
                await room.send(target, {"t": "kicked"})
                with contextlib.suppress(Exception):
                    await target.ws.close()
            room.remove_member(target.id)
    elif t == "leave":
        if room.game and not room.game.finished:
            me.ws = None  # keep their party in the game; they can rejoin with the token
            if room.ready_to_resolve():
                room.resolve()
            room.maybe_next_turn()
        else:
            room.remove_member(me.id)
        return False
    elif t == "submit":
        if not room.game:
            raise RoomError("game not started")
        room.submit(me.id, msg.get("actions", []))
    elif t == "unsubmit":
        if room.game and room.phase == "planning":
            room.game.pending.pop(me.id, None)
    elif t == "next":
        if not room.game:
            raise RoomError("game not started")
        room.ack_results(me.id)
    elif t == "propose":
        room.propose_coalition(me.id, str(msg.get("target", "")))
    elif t == "respond":
        if not room.game:
            raise RoomError("no game running")
        room.game.respond(me.id, str(msg.get("from", "")), bool(msg.get("accept")))
    elif t == "leave_coalition":
        if not room.game:
            raise RoomError("no game running")
        room.game.leave_coalition(me.id)
    elif t == "cabinet_propose":
        room.propose_cabinet(me.id, msg.get("alloc"))
    elif t == "cabinet_answer":
        if not room.game:
            raise RoomError("no game running")
        room.game.answer_cabinet(me.id, bool(msg.get("accept")))
    elif t == "chat":
        text = str(msg.get("text", "")).strip()[:300]
        if text:
            room.chat.append({"from": me.id, "name": me.party, "color": me.color, "text": text, "at": time.time()})
    else:
        raise RoomError("unknown message")
    return True


@app.websocket("/ws/{code}")
async def ws_endpoint(ws: WebSocket, code: str, token: str = ""):
    await ws.accept()
    try:
        room = manager.get(code)
    except RoomError:
        await ws.send_json({"t": "error", "msg": "room not found", "fatal": True})
        await ws.close()
        return
    me = room.by_token(token)
    if not me:
        await ws.send_json({"t": "error", "msg": "invalid session", "fatal": True})
        await ws.close()
        return
    if me.ws is not None:  # same player opened a second tab: newest connection wins
        with contextlib.suppress(Exception):
            await me.ws.close()
    me.ws = ws
    room.last_activity = time.time()
    # A room restored from disk has no deadline (nobody was connected): restart the clock now that
    # someone is back, so a server restart mid-round does not freeze the game.
    if room.game and room.phase == "planning" and not room.game.finished and room.deadline is None:
        room.deadline = time.time() + room.turn_seconds
    manager.mark(room)
    await room.broadcast()
    try:
        while True:
            msg = await ws.receive_json()
            room.last_activity = time.time()
            async with room.lock:
                try:
                    alive = await handle(room, me, msg if isinstance(msg, dict) else {})
                except (RoomError, ActionError, ValueError, TypeError) as e:
                    await ws.send_json({"t": "error", "msg": str(e)})
                    continue
                # Write-behind: this only flags the room, the flusher batches the actual write.
                manager.mark(room, urgent=msg.get("t") in ("start", "submit", "next") if isinstance(msg, dict) else False)
            await room.broadcast()
            if not alive:
                await ws.close()
                return
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if me.ws is ws:
            me.ws = None
            async with room.lock:
                if not room.game:
                    me.ready = False
                elif not room.game.finished:
                    if room.ready_to_resolve():
                        room.resolve()
                    room.maybe_next_turn()
                manager.mark(room)
            await room.broadcast()


NO_CACHE = {"Cache-Control": "no-cache"}


class CachedStatic(StaticFiles):
    """Vendored libraries never change, so they get a year; game code must revalidate every load
    (`no-cache` still answers 304 from the ETag, it just refuses to serve stale JS after a deploy)."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        path = str(getattr(resp, "path", ""))
        resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable"
                                         if "/vendor/" in path else "no-cache")
        return resp


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html", headers=NO_CACHE)


if PUBLIC_URL:
    @app.get("/js/server-config.js")
    def server_config():
        """Shadows the static file: same-origin API, but invite links use PUBLIC_URL."""
        body = f'window.__ELECTION_SERVER__ = "";\nwindow.__ELECTION_WEB__ = {json.dumps(PUBLIC_URL)};\n'
        return Response(body, media_type="text/javascript", headers=NO_CACHE)


@app.get("/admin")
def admin_page():
    return FileResponse(FRONTEND / "admin.html", headers=NO_CACHE)


app.mount("/", CachedStatic(directory=FRONTEND), name="static")
