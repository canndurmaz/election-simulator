"""Simple AI opponent: spends most of its money each round, favouring regions that match its ideology."""
import random

from .engine import Game, ideology_multiplier
from .maps import SECTORS


def plan(game: Game, pid: str, rng: random.Random) -> list[dict]:
    me = game.players[pid]
    budget = int(me.money * rng.uniform(0.6, 0.9))
    lo = game.rules.min_action
    if budget < lo:
        return []
    actions = []

    def take(frac):
        return max(lo, int(budget * frac))

    # economy: sector + a couple of regions
    s = rng.choice(SECTORS)
    actions.append({"type": "invest_sector", "sector": s, "amount": take(0.15)})
    regions = list(game.regions.values())
    weights = [r["seats"] * ideology_multiplier(me.right, r["right"]) ** 3 for r in regions]
    for r in rng.choices(regions, weights, k=2):
        actions.append({"type": "invest_region", "region": r["id"], "amount": take(0.08)})
    # campaign
    if rng.random() < 0.3:
        actions.append({"type": "ad", "kind": "tv", "amount": take(0.15)})
    if rng.random() < 0.3:
        actions.append({"type": "ad", "kind": "social", "sector": rng.choice(SECTORS), "amount": take(0.1)})
    for r in rng.choices(regions, weights, k=4):
        actions.append({"type": "rally", "region": r["id"], "amount": take(0.08)})

    total = 0
    out = []
    for a in actions:
        if total + a["amount"] <= me.money:
            out.append(a)
            total += a["amount"]
    return out
