"""HTTP + WebSocket API.

REST:  GET  /api/maps, GET /api/maps/{id}, GET /api/rooms
       POST /api/rooms            {room_name, player_name, map_id, max_players, turn_seconds, rounds, private}
       POST /api/rooms/{code}/join {player_name}
       -> {code, player_id, token}
WS:    /ws/{code}?token=...   client -> {"t": "profile"|"ready"|"start"|"add_bot"|"kick"|"leave"|"submit"|"unsubmit"|"next"|"chat", ...}
                              server -> {"t": "state", room, game, you} | {"t": "error", msg}
"""
import asyncio
import contextlib
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import ActionError
from .maps import list_maps, load_map
from .rooms import RoomError, RoomManager

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
manager = RoomManager()


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
                await room.broadcast()
        manager.cleanup()


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(ticker())
    yield
    task.cancel()


app = FastAPI(title="Election Game", lifespan=lifespan)


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
    return {"code": room.code, "player_id": m.id, "token": m.token}


@app.post("/api/rooms/{code}/join")
def join_room(code: str, req: JoinRoom):
    try:
        room = manager.get(code)
        m = room.add_member(req.player_name)
    except RoomError as e:
        raise HTTPException(400, str(e))
    room.last_activity = time.time()
    return {"code": room.code, "player_id": m.id, "token": m.token}


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
        room.add_member("", is_bot=True)
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
            await room.broadcast()


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/", StaticFiles(directory=FRONTEND), name="static")
