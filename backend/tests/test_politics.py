import pytest

from app.engine import ActionError, Game, Player, Rules, event_card
from app.maps import load_map
from app.politics import MINISTRIES, coalition_tension, platform_mult


def game(lefts=(80, 70, 20)):
    ps = {k: Player(k, k, f"{k} Party", "#000", l) for k, l in zip("abc", lefts)}
    g = Game(load_map("germany"), ps, Rules(), seed=4)
    g.event = event_card("calm")
    return g


def play(g, plans=None):
    plans = plans or {}
    for pid in g.players:
        g.submit(pid, plans.get(pid, []))
    return g.resolve()


# ---------- platform ----------
def test_platform_sector_and_lean_effects():
    green = {"environment": "left"}
    assert platform_mult(green, 70, "tourism", 0.5) == pytest.approx(1.15)
    assert platform_mult(green, 70, "industry", 0.5) == pytest.approx(0.90)
    trad = {"society": "right"}
    assert platform_mult(trad, 30, "trade", 0.7) == pytest.approx(1.12)   # right-leaning region
    assert platform_mult(trad, 30, "trade", 0.3) == pytest.approx(0.92)   # left-leaning region
    assert platform_mult(trad, 30, "trade", 0.5) == pytest.approx(1.0)    # swing region


def test_contradicting_stance_only_half_effective():
    # a very left party pushing "Tax cuts"
    assert platform_mult({"economy": "right"}, 80, "trade", 0.5) == pytest.approx(1.075)


# ---------- repositioning ----------
def test_reposition_moves_ideology_and_costs_credibility():
    g = game()
    before = g.campaign_mult("a", "by")
    play(g, {"a": [{"type": "reposition", "direction": "right", "amount": g.rules.reposition_cost}]})
    assert g.players["a"].left == 70
    assert g.players["a"].credibility_until == 2
    with pytest.raises(ActionError):
        g.submit("a", [{"type": "reposition", "direction": "left", "amount": 150_000},
                       {"type": "reposition", "direction": "left", "amount": 150_000}])
    g.players["a"].credibility_until = 0
    after_ok = g.campaign_mult("a", "by")
    g.players["a"].credibility_until = g.round
    assert g.campaign_mult("a", "by") == pytest.approx(after_ok * 0.85)
    assert after_ok > before   # moved toward Bavaria's right-leaning voters


# ---------- loyal base ----------
def test_matching_voters_are_more_loyal():
    g = game(lefts=(35, 70, 50))  # a is right-leaning like Bavaria, b is left
    g.support["by"] = {"a": 1000.0, "b": 1000.0}
    play(g)
    assert g.support["by"]["a"] > g.support["by"]["b"]


# ---------- coalition tension ----------
def test_tension_only_for_distant_partners():
    assert coalition_tension([60, 75]) == 0
    assert coalition_tension([80, 20]) == pytest.approx(0.105)


def test_grand_coalition_bleeds_support():
    g = game()
    g.support["by"] = {"a": 1000.0, "c": 1000.0, "b": 1000.0}
    g.propose("a", "c"); g.respond("c", "a", True)       # 80% left + 20% left: 60-point gap
    rep = play(g)
    assert any("Coalition tension" in e for e in rep["events"])
    decay = lambda pid: 0.1 * (0.5 + abs(g.players[pid].right - g.regions["by"]["right"]))
    assert g.support["by"]["a"] == pytest.approx(1000 * (1 - decay("a")) * (1 - 0.105), rel=1e-3)
    assert g.support["by"]["b"] == pytest.approx(1000 * (1 - decay("b")), rel=1e-3)


# ---------- government & ministries ----------
def test_largest_single_party_governs_alone_with_all_ministries():
    g = game()
    play(g, {"c": [{"type": "rally", "region": "nw", "amount": 900_000}]})
    assert g.government == ["c"]
    assert all(g.ministry_holder(m) == "c" for m in MINISTRIES)
    rep = play(g)
    assert rep["income"]["c"]["ministries"] == 150_000   # finance + social


def test_coalition_must_negotiate_ministries():
    g = game()
    g.propose("a", "b"); g.respond("b", "a", True)
    play(g, {"a": [{"type": "rally", "region": "nw", "amount": 600_000}],
             "b": [{"type": "rally", "region": "nw", "amount": 600_000}]})
    assert g.government == ["a", "b"]
    assert g.ministry_holder("finance") is None            # no deal yet
    alloc = {m: ("a" if i % 2 else "b") for i, m in enumerate(MINISTRIES)}
    g.propose_cabinet("a", alloc)
    assert g.ministry_holder("finance") is None            # b hasn't agreed
    g.answer_cabinet("b", True)
    assert g.ministry_holder("finance") == alloc["finance"]
    with pytest.raises(ActionError):
        g.propose_cabinet("a", {**alloc, "finance": "c"})  # c isn't in the coalition


def test_rejected_deal_and_breakup_clear_cabinet():
    g = game()
    g.propose("a", "b"); g.respond("b", "a", True)
    alloc = {m: "a" for m in MINISTRIES}
    g.propose_cabinet("b", alloc)
    g.answer_cabinet("a", False)
    assert not g.cabinet_proposals
    g.propose_cabinet("b", alloc); g.answer_cabinet("a", True)
    assert g.cabinets
    g.leave_coalition("b")
    assert not g.cabinets


def test_interior_halves_attacks():
    hits = {}
    for gov in (["b"], ["c"]):
        g = game()
        g.government = gov
        g.support["by"] = {"b": 100_000.0}
        g.submit("a", [{"type": "ad", "kind": "negative", "region": "by", "target": "b", "amount": 10_000}])
        g.submit("b", []); g.submit("c", [])
        g.resolve()
        hits[gov[0]] = g.support["by"]["b"]
    decay = 1 - 0.1 * (0.5 + abs(0.3 - load_map("germany")["regions"][1]["right"]))
    assert hits["b"] == pytest.approx((100_000 - 4_000) * decay, rel=1e-3)   # b holds Interior
    assert hits["c"] == pytest.approx((100_000 - 8_000) * decay, rel=1e-3)
