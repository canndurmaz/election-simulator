"""Room/Game <-> JSON-safe dict conversion.

Pure functions, no I/O, so they can be unit-tested without a database (see
`store.py` for the SQLite side). Three things in the game state are not JSON
types and need explicit handling on the way out *and* back in:

  * `Game.coalitions`     list[set]            -> list[list]
  * `Game.proposals`      set[tuple[str, str]] -> list[[from, to]]
  * `Game.cabinets` / `cabinet_proposals`      keyed by frozenset, and
    `cabinet_proposals[k]["accepted"]` is a set that engine.py compares with
    `>= key` (set superset), so it MUST come back as a real set.

`map_data` (60-136 KB of geometry) is deliberately *not* stored: only `map_id`
is, and `maps.load_map` (lru_cached) rebuilds it on restore.
"""
from __future__ import annotations

import random
from dataclasses import asdict, fields

from .engine import Game, Player, Rules
from .maps import load_map

SCHEMA_VERSION = 1


# ---------- random.Random ----------
def _rng_out(rng: random.Random) -> list:
    """getstate() is (version, tuple[625 ints], gauss_next) -> JSON-safe nesting."""
    version, internal, gauss = rng.getstate()
    return [version, list(internal), gauss]


def _rng_in(data, fallback: random.Random | None = None) -> random.Random:
    rng = random.Random()
    try:
        version, internal, gauss = data
        rng.setstate((version, tuple(internal), gauss))
    except (TypeError, ValueError):
        return fallback or random.Random()   # corrupt state: a fresh stream is harmless
    return rng


# ---------- dataclasses ----------
def _known(cls, data: dict) -> dict:
    """Drop unknown keys so an older snapshot still loads after a field is removed."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


# ---------- Game ----------
def game_to_dict(game: Game, map_id: str) -> dict:
    return {
        "map_id": map_id,
        "seed": game.seed,
        "rules": asdict(game.rules),
        "players": {pid: asdict(p) for pid, p in game.players.items()},
        "rng": _rng_out(game.rng),
        "round": game.round,
        "finished": game.finished,
        "support": game.support,
        "region_invest": game.region_invest,
        "sector_invest": game.sector_invest,
        "sector_owner": game.sector_owner,
        "region_owner": game.region_owner,
        "leaders": game.leaders,
        "seats": game.seats,
        "seat_history": game.seat_history,
        "pending": game.pending,
        "reports": game.reports,
        "coalitions": [sorted(c) for c in game.coalitions],
        "proposals": [list(p) for p in sorted(game.proposals)],
        "sector_market": game.sector_market,
        "event": game.event,
        "news": game.news,
        "government": game.government,
        "cabinets": [{"members": sorted(k), "alloc": v} for k, v in game.cabinets.items()],
        "cabinet_proposals": [{"members": sorted(k), "by": v["by"], "alloc": v["alloc"],
                               "accepted": sorted(v["accepted"])} for k, v in game.cabinet_proposals.items()],
    }


def game_from_dict(data: dict) -> Game:
    players = {pid: Player(**_known(Player, p)) for pid, p in data["players"].items()}
    game = Game(load_map(data["map_id"]), players, Rules(**_known(Rules, data["rules"])), data.get("seed"))
    game.rng = _rng_in(data.get("rng"), game.rng)
    game.round = data["round"]
    game.finished = data["finished"]
    game.support = data["support"]
    game.region_invest = data["region_invest"]
    game.sector_invest = data["sector_invest"]
    game.sector_owner = data["sector_owner"]
    game.region_owner = data["region_owner"]
    game.leaders = data["leaders"]
    game.seats = data["seats"]
    game.seat_history = data["seat_history"]
    game.pending = data["pending"]
    game.reports = data["reports"]
    game.coalitions = [set(c) for c in data["coalitions"]]
    game.proposals = {(a, b) for a, b in data["proposals"]}
    game.sector_market = data["sector_market"]
    game.event = data["event"]
    game.news = data["news"]
    game.government = data["government"]
    game.cabinets = {frozenset(c["members"]): c["alloc"] for c in data["cabinets"]}
    game.cabinet_proposals = {frozenset(c["members"]): {"by": c["by"], "alloc": c["alloc"],
                                                        "accepted": set(c["accepted"])}
                              for c in data["cabinet_proposals"]}
    # Player.money is overwritten with start_money by Game.__post_init__: restore the real balances.
    for pid, p in data["players"].items():
        players[pid].money = p["money"]
    return game


# ---------- Room ----------
def room_to_dict(room) -> dict:
    return {
        "v": SCHEMA_VERSION,
        "code": room.code, "name": room.name, "map_id": room.map_id,
        "max_players": room.max_players, "turn_seconds": room.turn_seconds, "rounds": room.rounds,
        "private": room.private, "host_id": room.host_id,
        "members": [{k: v for k, v in asdict(m).items() if k != "ws"} for m in room.members.values()],
        "game": game_to_dict(room.game, room.map_id) if room.game else None,
        "deadline": room.deadline, "phase": room.phase, "acks": sorted(room.acks),
        "chat": room.chat[-50:], "last_activity": room.last_activity,
        "rng": _rng_out(room.rng),
    }


def room_from_dict(data: dict, room_cls, member_cls):
    """Rebuild a Room. `ws` is left None: nobody is connected after a restart."""
    room = room_cls(code=data["code"], name=data["name"], map_id=data["map_id"],
                    max_players=data["max_players"], turn_seconds=data["turn_seconds"],
                    rounds=data["rounds"], private=data["private"], host_id=data["host_id"])
    for m in data["members"]:
        member = member_cls(**_known(member_cls, m))
        member.ws = None
        room.members[member.id] = member
    room.game = game_from_dict(data["game"]) if data["game"] else None
    room.deadline = data["deadline"]
    room.phase = data["phase"]
    room.acks = set(data["acks"])
    room.chat = data["chat"]
    room.last_activity = data["last_activity"]
    room.rng = _rng_in(data.get("rng"), room.rng)
    return room


__all__ = ["SCHEMA_VERSION", "game_to_dict", "game_from_dict", "room_to_dict", "room_from_dict"]
