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


def test_bot_answers_coalition_proposal_immediately():
    room, a, b = setup_room()
    bot = next(m for m in room.members.values() if m.is_bot)
    bot.bot_level = "easy"
    room.game.players[bot.id].left = room.game.players[a.id].left
    room.propose_coalition(a.id, bot.id)
    assert (a.id, bot.id) not in room.game.proposals  # answered, one way or the other


def test_traits_and_bot_levels_in_room_state():
    mgr = RoomManager()
    room = mgr.create("t", "turkey", 4, 60, 3, False)
    m = room.add_member("A")
    room.set_profile(m.id, {"trait": "tycoon"})
    bot = room.add_member("", is_bot=True, level="hard")
    st = room.room_state()
    assert "tycoon" in st["traits"] and st["bot_levels"] == ["easy", "normal", "hard"]
    assert [x for x in st["members"] if x["id"] == bot.id][0]["bot_level"] == "hard"
    with pytest.raises(RoomError):
        room.set_profile(m.id, {"trait": "wizard"})


def test_bots_negotiate_ministries_in_coalition():
    room, a, b = setup_room()
    bot = next(m for m in room.members.values() if m.is_bot)
    room.game.propose(bot.id, a.id)
    room.game.respond(a.id, bot.id, True)
    room.submit(a.id, []); room.submit(b.id, [])
    room.ack_results(a.id); room.ack_results(b.id)          # next planning phase: bot tables a deal
    prop = room.game.cabinet_proposals[frozenset({a.id, bot.id})]
    assert set(prop["alloc"].values()) <= {a.id, bot.id} and bot.id in prop["accepted"]
    room.game.answer_cabinet(a.id, True)
    assert frozenset({a.id, bot.id}) in room.game.cabinets
    assert "my_mult" in room.game_state_for(a.id)


def test_platform_profile_validation():
    mgr = RoomManager()
    room = mgr.create("t", "usa", 4, 60, 3, False)
    m = room.add_member("A")
    room.set_profile(m.id, {"platform": {"economy": "left", "society": "right"}})
    assert m.platform["economy"] == "left" and m.platform["environment"] == "center"
    with pytest.raises(RoomError):
        room.set_profile(m.id, {"platform": {"economy": "sideways"}})
