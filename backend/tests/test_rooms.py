import pytest

from app.rooms import RoomError, RoomManager


def setup_room(rounds=3):
    mgr = RoomManager()
    room = mgr.create("t", "usa", 4, 60, rounds, False)
    a, b = room.add_member("A"), room.add_member("B")
    room.add_member("", is_bot=True)
    for m in (a, b):
        m.ws = object()  # pretend connected
        m.ready = True
    room.start(a.id)
    return room, a, b


def test_results_phase_waits_for_every_player():
    room, a, b = setup_room()
    room.submit(a.id, [])
    assert room.phase == "planning"
    room.submit(b.id, [])
    assert room.phase == "results" and room.deadline is None and room.game.round == 2
    with pytest.raises(RoomError):
        room.submit(a.id, [])
    room.ack_results(a.id)
    assert room.phase == "results"
    room.ack_results(b.id)
    assert room.phase == "planning" and room.deadline is not None
    assert len(room.game.pending) == 1  # the bot already planned


def test_disconnected_player_does_not_block_next_turn():
    room, a, b = setup_room()
    room.submit(a.id, []); room.submit(b.id, [])
    room.ack_results(a.id)
    b.ws = None
    room.maybe_next_turn()
    assert room.phase == "planning"
