"""Room management: lobby, seats, host, readiness, reconnect tokens, turn timer, broadcasting."""
from __future__ import annotations

import asyncio
import random
import secrets
import string
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

from . import bots
from .engine import DEFAULT_TRAIT, TRAITS, ActionError, Game, Player, Rules
from .politics import ISSUES, MINISTRIES, clean_platform, default_platform
from .maps import load_map

COLORS = ["#e63946", "#1d7fd6", "#f4a300", "#2a9d8f", "#8e44ad", "#e76f51", "#43aa8b", "#d63384"]
BOT_PARTIES = ["Progress Party", "Homeland Party", "Unity Party", "Green Future", "People's Voice", "Liberty Union"]
IDLE_ROOM_TTL = 30 * 60


class RoomError(ValueError):
    pass


@dataclass
class Member:
    id: str
    token: str
    name: str
    party: str
    color: str
    left: int = 50
    ready: bool = False
    is_bot: bool = False
    bot_level: str = "normal"
    trait: str = DEFAULT_TRAIT
    platform: dict = field(default_factory=dict)
    ws: WebSocket | None = None

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "party": self.party, "color": self.color, "left": self.left,
                "ready": self.ready, "is_bot": self.is_bot, "online": self.is_bot or self.ws is not None,
                "bot_level": self.bot_level if self.is_bot else None, "trait": self.trait,
                "platform": self.platform or default_platform(self.left)}


@dataclass
class Room:
    code: str
    name: str
    map_id: str
    max_players: int
    turn_seconds: int
    rounds: int
    private: bool
    host_id: str = ""
    members: dict[str, Member] = field(default_factory=dict)
    game: Game | None = None
    deadline: float | None = None
    phase: str = "planning"          # planning -> results (everyone clicks "next turn") -> planning ...
    acks: set[str] = field(default_factory=set)
    chat: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_activity: float = field(default_factory=time.time)
    rng: random.Random = field(default_factory=random.Random)

    @property
    def status(self) -> str:
        if not self.game:
            return "lobby"
        return "finished" if self.game.finished else "playing"

    def summary(self) -> dict:
        return {"code": self.code, "name": self.name, "map_id": self.map_id, "status": self.status,
                "players": len(self.members), "max_players": self.max_players,
                "host": self.members[self.host_id].name if self.host_id in self.members else None}

    def room_state(self) -> dict:
        return {**self.summary(), "traits": TRAITS, "bot_levels": bots.LEVELS, "issues": ISSUES, "ministries": MINISTRIES, "host_id": self.host_id, "turn_seconds": self.turn_seconds, "rounds": self.rounds,
                "members": [m.public() for m in self.members.values()], "chat": self.chat[-50:],
                "deadline": self.deadline, "phase": self.phase, "acks": sorted(self.acks)}

    # ---------- membership ----------
    def add_member(self, name: str, is_bot: bool = False, level: str = "normal") -> Member:
        if self.game:
            raise RoomError("game already started")
        if len(self.members) >= self.max_players:
            raise RoomError("room is full")
        name = (name or "").strip()[:24] or "Player"
        used = {m.color for m in self.members.values()}
        color = next((c for c in COLORS if c not in used), COLORS[0])
        mid = secrets.token_hex(4)
        party = f"{name}'s Party"
        left = 50
        if is_bot:
            taken = {m.party for m in self.members.values()}
            party = next((p for p in BOT_PARTIES if p not in taken), f"Bot Party {len(self.members)}")
            name, left = f"Bot {len(self.members) + 1}", self.rng.choice([20, 35, 50, 65, 80])
        if is_bot and level not in bots.LEVELS:
            raise RoomError("unknown bot level")
        trait = self.rng.choice(list(TRAITS)) if is_bot else DEFAULT_TRAIT
        m = Member(mid, secrets.token_urlsafe(16), name, party, color, left, ready=is_bot, is_bot=is_bot,
                   bot_level=level, trait=trait, platform=bots.choose_platform(left, self.rng) if is_bot else {})
        self.members[mid] = m
        if not self.host_id:
            self.host_id = mid
        return m

    def remove_member(self, mid: str) -> None:
        self.members.pop(mid, None)
        if self.host_id == mid:
            humans = [m for m in self.members.values() if not m.is_bot]
            self.host_id = humans[0].id if humans else ""

    def by_token(self, token: str) -> Member | None:
        return next((m for m in self.members.values() if secrets.compare_digest(m.token, token)), None)

    def set_profile(self, mid: str, data: dict) -> None:
        if self.game:
            raise RoomError("profile is locked once the game starts")
        m = self.members[mid]
        if "party" in data:
            party = str(data["party"]).strip()[:32]
            if not party:
                raise RoomError("party name required")
            if any(o.party.lower() == party.lower() for o in self.members.values() if o.id != mid):
                raise RoomError("party name taken")
            m.party = party
        if "left" in data:
            m.left = max(0, min(100, int(data["left"])))
        if "platform" in data:
            try:
                m.platform = clean_platform(data["platform"])
            except ValueError as e:
                raise RoomError(str(e))
        if "trait" in data:
            if data["trait"] not in TRAITS:
                raise RoomError("unknown trait")
            m.trait = data["trait"]
        if "color" in data:
            color = str(data["color"])
            if color not in COLORS:
                raise RoomError("bad color")
            if any(o.color == color for o in self.members.values() if o.id != mid):
                raise RoomError("color taken")
            m.color = color
        m.ready = False

    # ---------- game ----------
    def start(self, mid: str) -> None:
        if mid != self.host_id:
            raise RoomError("only the host can start")
        if self.game:
            raise RoomError("already started")
        if len(self.members) < 2:
            raise RoomError("need at least 2 players (add a bot?)")
        if not all(m.ready for m in self.members.values()):
            raise RoomError("everyone must be ready")
        players = {m.id: Player(m.id, m.name, m.party, m.color, m.left, is_bot=m.is_bot, trait=m.trait,
                                platform=dict(m.platform) or default_platform(m.left))
                   for m in self.members.values()}
        self.game = Game(load_map(self.map_id), players, Rules(rounds=self.rounds, max_rounds=self.rounds * 2))
        self._begin_planning()

    def _begin_planning(self) -> None:
        self.phase = "planning"
        self.acks = set()
        self._bots_negotiate()
        self._bots_submit()
        self.deadline = time.time() + self.turn_seconds

    def _active_ids(self) -> list[str]:
        """Bots and connected humans. Disconnected players never block the game."""
        return [m.id for m in self.members.values() if m.is_bot or m.ws is not None]

    def _bots_submit(self) -> None:
        for m in self.members.values():
            if m.is_bot:
                self.game.submit(m.id, bots.plan(self.game, m.id, self.rng, m.bot_level))

    def ready_to_resolve(self) -> bool:
        """All connected humans submitted (disconnected players just skip their turn)."""
        active = self._active_ids()
        return self.phase == "planning" and bool(active) and self.game.all_submitted(active)

    def resolve(self) -> None:
        self.game.resolve()
        self.deadline = None
        self.phase = "results"  # show standings; the next turn starts when everyone clicks "next"
        self.acks = {m.id for m in self.members.values() if m.is_bot}

    def ack_results(self, mid: str) -> None:
        if self.phase != "results" or self.game.finished:
            raise RoomError("nothing to continue")
        self.acks.add(mid)
        self.maybe_next_turn()

    def maybe_next_turn(self) -> None:
        if self.phase == "results" and not self.game.finished and set(self._active_ids()) <= self.acks:
            self._begin_planning()

    def submit(self, mid: str, actions: list) -> None:
        if self.phase != "planning":
            raise RoomError("the round is over, waiting for everyone to continue")
        self.game.submit(mid, actions)
        if self.ready_to_resolve():
            self.resolve()

    # ---------- diplomacy ----------
    def _bot_members(self, pids) -> list[Member]:
        return [self.members[p] for p in pids if p in self.members and self.members[p].is_bot]

    def _bots_answer_cabinet(self, key: frozenset) -> None:
        """Bots in the coalition answer a pending ministry proposal right away."""
        g = self.game
        prop = g.cabinet_proposals.get(key)
        for b in self._bot_members(sorted(key)):
            if not prop or b.id in prop["accepted"]:
                continue
            ok = bots.respond_to_cabinet(g, b.id, prop["alloc"], b.bot_level, self.rng)
            g.answer_cabinet(b.id, ok)
            if not ok:
                self.chat.append({"from": b.id, "name": b.party, "color": b.color,
                                  "text": "We deserve more ministries than that.", "at": time.time()})
            prop = g.cabinet_proposals.get(key)

    def _bots_negotiate(self) -> None:
        """Coalitions with bots and no ministry deal: the strongest bot tables a seat-proportional split."""
        g = self.game
        for c in list(g.coalitions):
            key = frozenset(c)
            if key in g.cabinets or key in g.cabinet_proposals:
                continue
            members = self._bot_members(sorted(c))
            if not members:
                continue
            totals = g.seat_totals()
            lead = max(members, key=lambda m: totals.get(m.id, 0))
            g.propose_cabinet(lead.id, bots.propose_cabinet(g, lead.id))
            if key in g.cabinet_proposals:
                self._bots_answer_cabinet(key)

    def propose_cabinet(self, mid: str, alloc: dict) -> None:
        if not self.game or self.game.finished:
            raise RoomError("no game running")
        self.game.propose_cabinet(mid, alloc)
        key = frozenset(self.game.coalition_of(mid))
        if key in self.game.cabinet_proposals:
            self._bots_answer_cabinet(key)

    def propose_coalition(self, mid: str, target: str) -> None:
        if not self.game or self.game.finished:
            raise RoomError("no game running")
        self.game.propose(mid, target)
        t = self.members.get(target)
        if t and t.is_bot and (mid, target) in self.game.proposals:  # bots answer right away
            self.game.respond(target, mid, bots.respond_to_coalition(self.game, target, mid, t.bot_level, self.rng))
            if (mid, target) not in self.game.proposals and target not in self.game.partners(mid):
                self.chat.append({"from": t.id, "name": t.party, "color": t.color,
                                  "text": "No thanks, we'll go it alone.", "at": time.time()})

    def game_state_for(self, mid: str) -> dict | None:
        if not self.game:
            return None
        st = self.game.public_state()
        st["my_pending"] = self.game.pending.get(mid)
        st["deadline"] = self.deadline
        if mid in self.game.players:  # everything that scales my campaigning, per region (platform, ministries, ...)
            st["my_mult"] = {rid: round(self.game.campaign_mult(mid, rid), 3) for rid in self.game.regions}
        return st

    # ---------- networking ----------
    async def send(self, m: Member, msg: dict) -> None:
        if m.ws is None:
            return
        try:
            await m.ws.send_json(msg)
        except Exception:
            m.ws = None

    async def broadcast(self) -> None:
        room = self.room_state()
        for m in list(self.members.values()):
            if m.ws is not None:
                await self.send(m, {"t": "state", "room": room, "game": self.game_state_for(m.id), "you": m.id})


class RoomManager:
    def __init__(self):
        self.rooms: dict[str, Room] = {}

    def _code(self) -> str:
        while True:
            code = "".join(random.choices(string.ascii_uppercase.replace("O", "").replace("I", ""), k=5))
            if code not in self.rooms:
                return code

    def create(self, name: str, map_id: str, max_players: int, turn_seconds: int, rounds: int,
               private: bool) -> Room:
        load_map(map_id)  # raises KeyError on unknown map
        room = Room(self._code(), (name or "").strip()[:40] or "Election Night", map_id,
                    max(2, min(8, max_players)), max(20, min(600, turn_seconds)), max(1, min(50, rounds)), private)
        self.rooms[room.code] = room
        return room

    def get(self, code: str) -> Room:
        room = self.rooms.get((code or "").upper())
        if not room:
            raise RoomError("room not found")
        return room

    def public_list(self) -> list[dict]:
        return [r.summary() for r in self.rooms.values() if not r.private and r.status == "lobby"]

    async def close(self, code: str, reason: str = "This room was closed by an admin") -> None:
        """Delete a room, telling any connected players why."""
        room = self.rooms.pop(code, None)
        if not room:
            raise RoomError("room not found")
        for m in room.members.values():
            ws, m.ws = m.ws, None  # detach first so disconnect handlers don't touch the room
            if ws is not None:
                try:
                    await ws.send_json({"t": "error", "msg": reason, "fatal": True})
                    await ws.close()
                except Exception:
                    pass

    def admin_list(self) -> list[dict]:
        now = time.time()
        out = []
        for r in self.rooms.values():
            out.append({**r.summary(), "private": r.private, "phase": r.phase if r.game else None,
                        "round": r.game.round if r.game else None, "rounds": r.rounds,
                        "online": sum(1 for m in r.members.values() if m.ws is not None),
                        "idle_seconds": round(now - r.last_activity),
                        "members": [{"name": m.name, "party": m.party, "is_bot": m.is_bot, "online": m.ws is not None}
                                    for m in r.members.values()]})
        return sorted(out, key=lambda r: r["idle_seconds"])

    def cleanup(self) -> None:
        now = time.time()
        for code, r in list(self.rooms.items()):
            humans_online = any(m.ws is not None for m in r.members.values() if not m.is_bot)
            if not humans_online and now - r.last_activity > IDLE_ROOM_TTL or not r.members:
                del self.rooms[code]


__all__ = ["RoomManager", "Room", "RoomError", "ActionError"]
