import random

import pytest

from app import bots
from app.engine import (INDEPENDENT, ActionError, Game, Player, Rules, dhondt, ideology_multiplier,
                        mismatch_penalty, split_income)
from app.maps import load_map

R = Rules()


def game(**kw):
    players = {"a": Player("a", "A", "Left Party", "#f00", 80), "b": Player("b", "B", "Right Party", "#00f", 20)}
    return Game(load_map("turkey"), players, Rules(**kw), seed=1)


def test_turkey_map_has_600_seats_81_provinces():
    m = load_map("turkey")
    assert len(m["regions"]) == 81
    assert sum(r["seats"] for r in m["regions"]) == 600
    assert {r["sector"] for r in m["regions"]} == {"agriculture", "industry", "trade", "tourism", "technology"}


def test_dominance_takes_80_percent():
    out = split_income(100, {"a": 300, "b": 60, "c": 40}, R, saturation=1)
    assert out["a"] == pytest.approx(80)
    assert out["b"] == pytest.approx(12)
    assert out["c"] == pytest.approx(8)


def test_below_dominance_is_proportional():
    out = split_income(100, {"a": 290, "b": 100}, R, saturation=1)
    assert out["a"] == pytest.approx(100 * 290 / 390)


def test_sole_investor_gets_all_but_scaled_by_saturation():
    assert split_income(100, {"a": 200}, R, saturation=4) == {"a": pytest.approx(50)}


def test_dhondt():
    assert dhondt({"a": 100, "b": 80, "c": 30}, 7) == {"a": 3, "b": 3, "c": 1}


def test_ideology():
    assert ideology_multiplier(0.5, 0.5) == pytest.approx(1.2)
    assert ideology_multiplier(0.0, 1.0) == pytest.approx(0.4)
    assert mismatch_penalty(0.5, 0.55, R) == 0
    assert mismatch_penalty(0.2, 0.8, R) == pytest.approx(0.75)


def test_validation():
    g = game()
    with pytest.raises(ActionError):
        g.submit("a", [{"type": "rally", "region": "ankara", "amount": 5_000_000}])
    with pytest.raises(ActionError):
        g.submit("a", [{"type": "rally", "region": "atlantis", "amount": 50_000}])
    with pytest.raises(ActionError):
        g.submit("a", [{"type": "ad", "kind": "negative", "region": "ankara", "target": "a", "amount": 50_000}])


def test_rally_wins_region_and_pays_leader_with_penalty():
    g = game()
    # a is 20% right; Rize is ~80% right -> big mismatch penalty
    g.submit("a", [{"type": "rally", "region": "rize", "amount": 500_000}])
    g.submit("b", [])
    rep = g.resolve()
    assert g.leaders["rize"] == "a"
    inc = rep["income"]["a"]
    assert inc["leadership"] == 3 * R.leader_bonus_per_seat
    assert inc["penalty"] < 0
    assert g.players["a"].money == pytest.approx(1_000_000 - 500_000 + inc["total"])


def test_independents_hold_seats_without_campaigns():
    g = game()
    g.submit("a", []); g.submit("b", [])
    rep = g.resolve()
    assert rep["seats"][INDEPENDENT] == 600
    assert all(l is None for l in g.leaders.values())


def test_full_game_with_bots_finishes():
    g = game(rounds=20)
    rng = random.Random(3)
    while not g.finished:
        for pid in g.players:
            g.submit(pid, bots.plan(g, pid, rng))
        g.resolve()
    st = g.public_state()
    assert st["winner"] in g.players
    assert 20 <= len(st["seat_history"]) <= g.rules.max_rounds
    assert not st["unclaimed"] or len(st["seat_history"]) == g.rules.max_rounds
    assert all(p.money >= 0 for p in g.players.values())


def test_overtime_until_every_region_has_a_winner():
    g = game(rounds=2, max_rounds=10)
    for _ in range(2):
        g.submit("a", [{"type": "rally", "region": "ankara", "amount": 500_000}]); g.submit("b", [])
        g.resolve()
    assert not g.finished and g.round == 3 and g.public_state()["overtime"]
    # a sweeps every region -> game ends at the end of this round
    g.players["a"].money = 10**9
    g.submit("a", [{"type": "rally", "region": rid, "amount": 1_000_000} for rid in list(g.regions)[:60]]); g.submit("b", [])
    g.resolve()
    assert not g.finished
    g.submit("a", [{"type": "rally", "region": rid, "amount": 1_000_000} for rid in list(g.regions)[60:]]); g.submit("b", [])
    g.resolve()
    assert g.finished and g.unclaimed() == []


def test_overtime_capped():
    g = game(rounds=2, max_rounds=4)
    while not g.finished:
        g.submit("a", []); g.submit("b", [])
        g.resolve()
    assert len(g.seat_history) == 4 and len(g.unclaimed()) == 81
