"""AI opponents.

easy   - spends part of its money on random-ish campaigns (the original bot).
normal - goes after regions it can actually win, with some noise; invests loosely.
hard   - greedy seat-per-money optimiser: wins/defends the cheapest seats, uses attack ads when cheaper than
         outspending, and buys permanent control of sectors/economies while there is time to profit.
"""
import random

from .engine import SECTORS, Game, ideology_multiplier

LEVELS = ["easy", "normal", "hard"]


def plan(game: Game, pid: str, rng: random.Random, level: str = "normal") -> list[dict]:
    if level == "easy":
        return _easy(game, pid, rng)
    return _smart(game, pid, rng, hard=level == "hard")


MAX_ACTIONS = 40  # below the engine's per-turn limit; actions are in priority order


def _fit(actions: list[dict], money: float, lo: int) -> list[dict]:
    out, total = [], 0
    for a in actions:
        if len(out) >= MAX_ACTIONS:
            break
        a["amount"] = int(a["amount"] // 1000 * 1000)
        if a["amount"] >= lo and total + a["amount"] <= money:
            out.append(a)
            total += a["amount"]
    return out


def _easy(game: Game, pid: str, rng: random.Random) -> list[dict]:
    me = game.players[pid]
    budget = int(me.money * rng.uniform(0.4, 0.7))
    lo = game.rules.min_action
    if budget < lo:
        return []
    take = lambda frac: max(lo, int(budget * frac))  # noqa: E731
    actions = []
    open_sectors = [x for x in SECTORS if game.sector_owner[x] in (None, pid)] or SECTORS
    actions.append({"type": "invest_sector", "sector": rng.choice(open_sectors), "amount": take(0.15)})
    regions = list(game.regions.values())
    weights = [r["seats"] * ideology_multiplier(me.right, r["right"]) ** 3 for r in regions]
    econ = [r for r in regions if game.region_owner[r["id"]] in (None, pid)] or regions
    for r in rng.choices(econ, [r["seats"] for r in econ], k=2):
        actions.append({"type": "invest_region", "region": r["id"], "amount": take(0.08)})
    if rng.random() < 0.3:
        actions.append({"type": "ad", "kind": "tv", "amount": take(0.15)})
    if rng.random() < 0.3:
        actions.append({"type": "ad", "kind": "social", "sector": rng.choice(SECTORS), "amount": take(0.1)})
    for r in rng.choices(regions, weights, k=4):
        actions.append({"type": "rally", "region": r["id"], "amount": take(0.08)})
    return _fit(actions, me.money, lo)


def _control_cost(stakes: dict, pid: str, pool: float, game: Game, buffer: float) -> float:
    mine = stakes.get(pid, 0)
    others = sum(v for k, v in stakes.items() if k != pid)
    target = max(game.rules.dominance_ratio * others * buffer, game.rules.control_min_pools * pool * buffer)
    return max(game.rules.min_action, target - mine + 1000)


def _smart(game: Game, pid: str, rng: random.Random, hard: bool) -> list[dict]:
    me = game.players[pid]
    R = game.rules
    lo = R.min_action
    budget = me.money * (0.92 if hard else rng.uniform(0.6, 0.8))
    if budget < lo:
        return []
    actions: list[dict] = []
    rounds_left = max(1, R.rounds - game.round + 1)

    # --- economy: buy permanent control while there are enough rounds left to earn it back
    econ_budget = budget * (0.35 if game.round <= 3 else 0.2) if rounds_left > 4 else 0
    cands = []
    for s in SECTORS:
        if game.sector_owner[s] is None:
            pool = game.sector_pool(s)
            cost = _control_cost(game.sector_invest[s], pid, pool, game, 1.15 if hard else 1.05)
            cands.append((pool * R.dominance_share * rounds_left / cost, {"type": "invest_sector", "sector": s}, cost))
    for rid, r in game.regions.items():
        if game.region_owner[rid] is None:
            pool = R.region_pool_base + r["seats"] * R.region_pool_per_seat
            cost = _control_cost(game.region_invest[rid], pid, pool, game, 1.15 if hard else 1.05)
            cands.append((pool * R.dominance_share * rounds_left / cost, {"type": "invest_region", "region": rid}, cost))
    if not hard:
        cands = [(v * rng.uniform(0.5, 1.5), a, c) for v, a, c in cands]
    cands.sort(key=lambda x: x[0], reverse=True)
    for value, a, cost in cands:
        if value < 1.3 or econ_budget < lo:
            break
        if cost <= econ_budget:
            actions.append({**a, "amount": cost})
            econ_budget -= cost
    spent = sum(a["amount"] for a in actions)

    # --- campaign: cheapest seats first
    camp = budget - spent
    partners = game.partners(pid)
    options = []
    for rid, r in game.regions.items():
        sup = game.support[rid]
        mine = sup.get(pid, 0)
        rivals = {k: v for k, v in sup.items() if k != pid and k not in partners}
        top_rival = max(rivals, key=rivals.get) if rivals else None
        threat = max([game.independent_support(rid)] + list(rivals.values()))
        mult = game.effective_multiplier(pid, rid) * (1.25 if me.trait == "orator" else 1.0)
        margin = 1.2 if hard else 1.1
        need = threat * margin - mine
        if need <= 0:
            continue  # comfortably ahead
        rally_cost = need / mult
        seats_value = r["seats"] * (1 + 0.5 * (1 - abs(me.right - r["right"])))
        options.append((seats_value / rally_cost, {"type": "rally", "region": rid}, rally_cost))
        # attack ad: when the main threat is a rival (not independents) and hitting them is cheaper
        if hard and top_rival and rivals[top_rival] >= game.independent_support(rid):
            second = max([game.independent_support(rid)] + [v for k, v in rivals.items() if k != top_rival])
            if mine > second:  # knocking the rival down would leave us in front
                attack_cost = (rivals[top_rival] * 1.05 - mine) / R.negative_efficiency
                if 0 < attack_cost < rally_cost * 0.8:
                    options.append((seats_value / attack_cost,
                                    {"type": "ad", "kind": "negative", "region": rid, "target": top_rival}, attack_cost))
    if not hard:
        options = [(v * rng.uniform(0.6, 1.4), a, c) for v, a, c in options]
    options.sort(key=lambda x: x[0], reverse=True)
    used = set()
    for value, a, cost in options:
        key = a.get("region")
        if key in used or cost > camp:
            continue
        amount = max(lo, cost * 1.02)
        if amount > camp:
            continue
        actions.append({**a, "amount": amount})
        used.add(key)
        camp -= amount
    # leftover: broad support where it fits best (social ads for media darlings, rallies otherwise)
    if camp >= lo * 3:
        if me.trait == "media":
            best = max(SECTORS, key=lambda s: sum(game.effective_multiplier(pid, r["id"]) * r["seats"]
                                                  for r in game.regions.values() if r["sector"] == s))
            actions.append({"type": "ad", "kind": "social", "sector": best, "amount": camp})
        else:
            best = sorted(game.regions, key=lambda rid: game.regions[rid]["seats"] * game.effective_multiplier(pid, rid),
                          reverse=True)[:3]
            for rid in best:
                actions.append({"type": "rally", "region": rid, "amount": camp / 3})
    # merge duplicate rallies
    merged: dict[tuple, dict] = {}
    for a in actions:
        k = (a["type"], a.get("kind"), a.get("region"), a.get("sector"), a.get("target"))
        if k in merged:
            merged[k]["amount"] += a["amount"]
        else:
            merged[k] = dict(a)
    return _fit(list(merged.values()), me.money, lo)


def respond_to_coalition(game: Game, bot: str, proposer: str, level: str, rng: random.Random) -> bool:
    """Should the bot accept proposer's coalition offer?"""
    gap = abs(game.players[bot].left - game.players[proposer].left)
    if level == "easy":
        return rng.random() < 0.5
    if level == "normal":
        return gap <= 30
    totals = game.seat_totals()
    majority = sum(r["seats"] for r in game.regions.values()) // 2 + 1
    mine = sum(totals.get(p, 0) for p in game.coalition_of(bot))
    theirs = sum(totals.get(p, 0) for p in game.coalition_of(proposer))
    return gap <= 40 and mine < majority and mine + theirs > mine * 1.3
