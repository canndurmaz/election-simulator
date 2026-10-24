"""Snapshot -> SQLite -> restore round trips.

The dangerous parts are the containers JSON cannot represent: `coalitions` (list[set]),
`proposals` (set[tuple]), and the frozenset-keyed `cabinets` / `cabinet_proposals` whose
`accepted` value engine.py compares with `>=` (a set superset test). A game restored with
those as lists would raise or silently misbehave several rounds later, so they are asserted
by type here, not just by value.
"""
import asyncio

import pytest

from app import store
from app.engine import MINISTRIES, Player, Rules
from app.rooms import Member, Room, RoomManager
from app.serialize import game_from_dict, game_to_dict, room_from_dict, room_to_dict


def played_room(rounds=5, map_id="usa"):
    """A room a few rounds in, with a coalition, a ministry deal and a pending proposal."""
    mgr = RoomManager()
    room = mgr.create("Snapshot Night", map_id, 4, 60, rounds, False)
    a, b, c = room.add_member("A"), room.add_member("B"), room.add_member("C")
    for m in (a, b, c):
        m.ws = object()      # pretend connected
        m.ready = True
    room.start(a.id)
    g = room.game
    rid = next(iter(g.regions))
    room.submit(a.id, [{"type": "rally", "region": rid, "amount": 200_000}])
    room.submit(b.id, [{"type": "invest_sector", "sector": "industry", "amount": 150_000}])
    room.submit(c.id, [])
    for m in (a, b, c):
        room.ack_results(m.id)
    g.propose(a.id, b.id)
    g.respond(b.id, a.id, True)                       # a + b are now a coalition
    g.propose(a.id, c.id)                             # and an offer to c is still open
    g.government = sorted(g.coalition_of(a.id))
    g.propose_cabinet(a.id, {m: (a.id if i % 2 else b.id) for i, m in enumerate(MINISTRIES)})
    room.chat.append({"from": a.id, "name": "A", "color": "#f00", "text": "deal?", "at": 1.0})
    return room


def test_game_round_trip_preserves_state_and_container_types():
    room = played_room()
    g = room.game
    restored = game_from_dict(game_to_dict(g, room.map_id))

    assert restored.round == g.round and restored.finished == g.finished
    assert restored.support == g.support and restored.seats == g.seats
    assert restored.seat_history == g.seat_history and restored.reports == g.reports
    assert restored.region_invest == g.region_invest and restored.sector_invest == g.sector_invest
    assert restored.sector_owner == g.sector_owner and restored.region_owner == g.region_owner
    assert restored.leaders == g.leaders and restored.event == g.event
    assert restored.government == g.government and restored.sector_market == g.sector_market
    assert {p: v.money for p, v in restored.players.items()} == {p: v.money for p, v in g.players.items()}
    assert restored.rules == g.rules
    assert restored.regions.keys() == g.regions.keys()      # map_data rebuilt from map_id, not stored

    assert restored.coalitions == g.coalitions and all(isinstance(c, set) for c in restored.coalitions)
    assert restored.proposals == g.proposals and all(isinstance(p, tuple) for p in restored.proposals)
    assert set(restored.cabinet_proposals) == set(g.cabinet_proposals)
    for key, prop in restored.cabinet_proposals.items():
        assert isinstance(key, frozenset)
        assert isinstance(prop["accepted"], set)           # engine.py does `prop["accepted"] >= key`
        assert prop["alloc"] == g.cabinet_proposals[key]["alloc"]


def test_restored_game_keeps_playing_identically():
    """The rng state travels too, so both copies draw the same event card and the same bot plans."""
    room = played_room()
    restored = game_from_dict(game_to_dict(room.game, room.map_id))
    ids = list(room.game.players)
    for g in (room.game, restored):
        for pid in ids:
            g.submit(pid, [])
        g.resolve()
    assert restored.event == room.game.event
    assert restored.seat_history == room.game.seat_history
    assert {p: v.money for p, v in restored.players.items()} == {p: v.money for p, v in room.game.players.items()}


def test_cabinet_acceptance_still_works_after_a_restore():
    room = played_room()
    key = frozenset(room.game.coalition_of(next(iter(room.game.players))))
    restored = game_from_dict(game_to_dict(room.game, room.map_id))
    other = next(p for p in sorted(key) if p not in restored.cabinet_proposals[key]["accepted"])
    restored.answer_cabinet(other, True)                    # exercises the `accepted >= key` path
    assert key in restored.cabinets


def test_room_round_trip_drops_sockets_but_keeps_seats_and_tokens():
    room = played_room()
    restored = room_from_dict(room_to_dict(room), Room, Member)

    assert (restored.code, restored.name, restored.map_id) == (room.code, room.name, room.map_id)
    assert (restored.phase, restored.rounds, restored.turn_seconds) == (room.phase, room.rounds, room.turn_seconds)
    assert restored.host_id == room.host_id and restored.acks == room.acks
    assert restored.chat == room.chat
    assert restored.members.keys() == room.members.keys()
    for mid, m in restored.members.items():
        assert m.token == room.members[mid].token       # players rejoin with their saved token
        assert m.party == room.members[mid].party and m.trait == room.members[mid].trait
        assert m.ws is None                             # nobody is connected after a restart
    assert isinstance(restored.lock, asyncio.Lock)      # not serialised; a fresh one is created


def test_lobby_only_room_round_trips():
    mgr = RoomManager()
    room = mgr.create("lobby", "germany", 2, 45, 7, True)
    room.add_member("Solo")
    restored = room_from_dict(room_to_dict(room), Room, Member)
    assert restored.game is None and restored.private is True and restored.status == "lobby"


# ---------- SQLite ----------
def test_store_saves_loads_and_deletes(tmp_path):
    st = store.Store(tmp_path / "t.db").open()
    try:
        st.save([("ABCDE", "playing", '{"code":"ABCDE"}')])
        assert st.count() == 1
        assert st.load_all() == [{"code": "ABCDE"}]
        st.save([("ABCDE", "finished", '{"code":"ABCDE","x":2}')])   # upsert, not a second row
        assert st.count() == 1 and st.load_all()[0]["x"] == 2
        st.delete(["ABCDE"])
        assert st.count() == 0 and st.load_all() == []
    finally:
        st.close()


def test_store_uses_wal_and_drops_stale_and_corrupt_rows(tmp_path):
    st = store.Store(tmp_path / "t.db").open()
    try:
        assert st._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        st.save([("OLD", "lobby", "{}"), ("NEW", "lobby", "{}")])
        st._conn.execute("UPDATE rooms SET updated = 0 WHERE code = 'OLD'")
        st._conn.execute("UPDATE rooms SET data = X'00ff00' WHERE code = 'NEW'")   # not valid zlib
        assert st.load_all() == []                 # stale row pruned, corrupt row skipped
        assert st.count() == 1                     # ... and only the stale one was deleted
    finally:
        st.close()


def test_disabled_persistence_is_a_no_op():
    p = store.Persistence(None)
    assert p.enabled is False
    p.mark(played_room())                     # must not raise or try to open anything
    p.forget("ABCDE")
    assert p.restore(Room, Member) == []


@pytest.mark.parametrize("value,expected_off", [("off", True), ("", True), ("none", True), ("/tmp/x.db", False)])
def test_env_switch(monkeypatch, value, expected_off):
    monkeypatch.setenv("ELECTION_DB", value)
    assert (store.db_path_from_env() is None) is expected_off


def test_manager_restores_rooms_through_the_flusher(tmp_path):
    """The full path a server restart takes: mark -> flush -> new manager -> restore."""
    async def run():
        p = store.build(tmp_path / "rooms.db")
        mgr = RoomManager(p)
        room = mgr.create("Persisted", "usa", 4, 60, 3, False)
        a, b = room.add_member("A"), room.add_member("B")
        for m in (a, b):
            m.ws, m.ready = object(), True
        room.start(a.id)
        room.submit(a.id, [{"type": "rally", "region": next(iter(room.game.regions)), "amount": 50_000}])
        mgr.mark(room, urgent=True)
        await p.flush()

        p2 = store.build(tmp_path / "rooms.db")
        mgr2 = RoomManager(p2)
        assert mgr2.restore() == 1
        back = mgr2.rooms[room.code]
        assert back.game is not None and back.game.round == room.game.round
        assert back.members[a.id].token == a.token
        assert back.game.pending == room.game.pending        # the submitted plan survived
        assert back.deadline is None                         # timer waits for someone to reconnect

        # Deleting a room must also delete its snapshot, or it returns from the dead.
        await mgr2.close(room.code)
        await p2.flush()
        p3 = store.build(tmp_path / "rooms.db")
        assert RoomManager(p3).restore() == 0
        await p3.stop()
    asyncio.run(run())
