import random

import pytest

from app import bots
from app.engine import TRAITS, ActionError, Game, Player, Rules, event_card
from app.maps import load_map


def game(n=3, **kw):
    ps = {k: Player(k, k, f"{k} Party", "#000", l) for k, l in zip("abc"[:n], (80, 70, 20))}
    return Game(load_map("germany"), ps, Rules(**kw), seed=4)


# ---------- coalitions ----------
def test_coalition_propose_accept_and_no_attacks_between_partners():
    g = game()
    g.propose("a", "b")
    assert ("a", "b") in g.proposals
    g.respond("b", "a", True)
    assert g.partners("a") == {"b"} and not g.proposals
    with pytest.raises(ActionError):
        g.submit("a", [{"type": "ad", "kind": "negative", "region": "by", "target": "b", "amount": 50_000}])
    g.leave_coalition("b")
    assert g.partners("a") == set()


def test_mutual_proposals_merge_immediately():
    g = game()
    g.propose("a", "b")
    assert g.propose("b", "a") is True
    assert g.partners("b") == {"a"}


def test_majority_coalition_wins_together():
    g = game()
    g.propose("a", "b"); g.respond("b", "a", True)
    # hand a and b 40% of seats each via direct seat assignment
    for rid, r in g.regions.items():
        g.seats[rid] = {"a": r["seats"] // 2, "b": r["seats"] - r["seats"] // 2} if rid != "by" else {"c": r["seats"]}
    res = g.result()
    assert res["coalition"] and set(res["winners"]) == {"a", "b"}


def test_no_majority_means_largest_party_wins():
    g = game()
    res = g.result()
    assert not res["coalition"] and len(res["winners"]) == 1


# ---------- transfers ----------
def test_transfer_moves_money():
    g = game()
    g.event = event_card("calm")
    g.submit("a", [{"type": "transfer", "target": "b", "amount": 200_000}]); g.submit("b", []); g.submit("c", [])
    g.resolve()
    inc = g.reports[-1]["income"]
    assert g.players["a"].money == 1_000_000 - 200_000 + inc["a"]["total"]
    assert g.players["b"].money == 1_000_000 + 200_000 + inc["b"]["total"]


# ---------- traits ----------
def test_fundraiser_base_income_and_grassroots_decay():
    g = game()
    g.event = event_card("calm")
    g.players["a"].trait = "fundraiser"
    g.players["b"].trait = "grassroots"
    raw = 100_000 * g.campaign_mult("b", "nw")
    g.submit("a", []); g.submit("b", [{"type": "rally", "region": "nw", "amount": 100_000}]); g.submit("c", [])
    g.resolve()
    assert g.reports[-1]["income"]["a"]["base"] == pytest.approx(160_000)
    decay = 0.1 * (0.5 + abs(g.players["b"].right - g.regions["nw"]["right"])) / 2   # grassroots halves it
    assert g.support["nw"]["b"] == pytest.approx(raw * (1 - decay))


def test_orator_rallies_harder():
    g = game()
    g.event = event_card("calm")
    g.players["a"].trait = "tycoon"
    g.players["b"].trait = "orator"
    ma, mb = g.campaign_mult("a", "by"), g.campaign_mult("b", "by")
    g.submit("a", [{"type": "rally", "region": "by", "amount": 100_000}])
    g.submit("b", [{"type": "rally", "region": "by", "amount": 100_000}]); g.submit("c", [])
    g.resolve()
    da = 0.1 * (0.5 + abs(g.players["a"].right - g.regions["by"]["right"]))
    db = 0.1 * (0.5 + abs(g.players["b"].right - g.regions["by"]["right"]))
    ratio = (g.support["by"]["b"] / mb / (1 - db)) / (g.support["by"]["a"] / ma / (1 - da))
    assert ratio == pytest.approx(1.25, rel=0.01)


def test_all_traits_have_text():
    assert all({"name", "icon", "text"} <= set(t) for t in TRAITS.values())


# ---------- events ----------
def test_events_drawn_each_round_and_scandal_hits_support():
    g = game()
    assert g.event["kind"] == "opening"
    g.submit("a", [{"type": "rally", "region": "by", "amount": 200_000}]); g.submit("b", []); g.submit("c", [])
    g.resolve()
    assert g.event["kind"] != "opening" and g.event["title"]
    before = g.support["by"]["a"]
    g.event = event_card("scandal", party="a", party_name="a Party")
    g.submit("a", []); g.submit("b", []); g.submit("c", [])
    g.resolve()
    decay = 0.1 * (0.5 + abs(g.players["a"].right - g.regions["by"]["right"]))
    assert g.support["by"]["a"] == pytest.approx(before * 0.75 * (1 - decay))


def test_boom_and_donors():
    g = game()
    g.sector_invest["industry"] = {"a": 10_000_000}
    g.event = event_card("boom", sector="industry")
    g.sector_market["industry"] = 1.0
    g.submit("a", []); g.submit("b", []); g.submit("c", [])
    boom = g.resolve()["income"]["a"]["sectors"]
    g.event = event_card("donors")
    g.sector_market["industry"] = 1.0
    g.submit("a", []); g.submit("b", []); g.submit("c", [])
    rep = g.resolve()
    assert rep["income"]["b"]["event"] == 150_000
    assert boom == pytest.approx(rep["income"]["a"]["sectors"] * 1.6, rel=0.01)


# ---------- bots ----------
@pytest.mark.parametrize("level", bots.LEVELS)
def test_bot_levels_produce_valid_plans(level):
    g = game()
    rng = random.Random(1)
    while not g.finished:
        for pid in g.players:
            g.submit(pid, bots.plan(g, pid, rng, level))  # raises if invalid
        g.resolve()


def test_hard_beats_easy_usually():
    wins = 0
    for seed in range(4):
        ps = {"e": Player("e", "e", "E", "#000", 50), "h": Player("h", "h", "H", "#000", 50)}
        g = Game(load_map("turkey"), ps, seed=seed)
        rng = random.Random(seed)
        while not g.finished:
            g.submit("e", bots.plan(g, "e", rng, "easy")); g.submit("h", bots.plan(g, "h", rng, "hard"))
            g.resolve()
        wins += g.standings()[0]["id"] == "h"
    assert wins >= 3
