"""Election game rules. Pure logic, no networking.

Round flow (all players plan simultaneously, then the round resolves):
  1. Spend: campaign actions (rallies, ads) raise support; investments buy economic stakes.
  2. Election: every region votes. Plurality winner leads it; seats are split by D'Hondt.
  3. Income: base + leadership bonus (reduced by ideology mismatch) + region economy + sector economy.
  4. Support decays, next round. After the last round the party with the most seats wins.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .maps import SECTORS

INDEPENDENT = "_ind"


@dataclass
class Rules:
    """All money and support values are in currency units (campaigns are in the thousands/millions)."""
    rounds: int = 20
    max_rounds: int = 40             # safety cap: overtime can't go on forever
    start_money: int = 1_000_000
    base_income: int = 100_000
    min_action: int = 10_000
    support_decay: float = 0.10
    independent_support_per_seat: float = 4_000  # baseline "undecided/independent" vote per seat
    leader_bonus_per_seat: int = 4_000            # campaign bonus for leading a region
    region_pool_base: int = 10_000                # region economy per round = base + seats * per_seat
    region_pool_per_seat: int = 4_000
    region_saturation: float = 4.0                # full pool paid out once total investment >= pool * this
    sector_pool_per_seat: float = 3_000
    sector_saturation: float = 5.0
    dominance_ratio: float = 3.0                # rule: >= 3x all others combined ...
    dominance_share: float = 0.80               # ... takes 80% of the income
    tv_efficiency: float = 1.2
    social_efficiency: float = 1.4
    negative_efficiency: float = 0.8
    mismatch_grace: float = 0.10                # ideology gap tolerated without penalty
    mismatch_penalty_slope: float = 1.5
    mismatch_penalty_cap: float = 0.9


SECTOR_MULT = {"technology": 1.3, "industry": 1.2, "trade": 1.1, "tourism": 1.0, "agriculture": 0.9}
AD_KINDS = {"tv", "social", "negative"}


class ActionError(ValueError):
    pass


@dataclass
class Player:
    id: str
    name: str
    party: str
    color: str
    left: int  # 0..100 percent left; right = 100 - left
    money: float = 0
    is_bot: bool = False

    @property
    def right(self) -> float:
        return (100 - self.left) / 100


def ideology_multiplier(player_right: float, region_right: float) -> float:
    """Campaign effectiveness: 1.2 when ideologies match, down to 0.4 when opposite."""
    return 0.4 + 0.8 * (1 - abs(player_right - region_right))


def mismatch_penalty(player_right: float, region_right: float, rules: Rules) -> float:
    gap = max(0.0, abs(player_right - region_right) - rules.mismatch_grace)
    return min(rules.mismatch_penalty_cap, gap * rules.mismatch_penalty_slope)


def dhondt(votes: dict[str, float], seats: int) -> dict[str, int]:
    out = {k: 0 for k in votes}
    if not votes or seats <= 0:
        return out
    for _ in range(seats):
        best = max(votes, key=lambda k: (votes[k] / (out[k] + 1), k != INDEPENDENT, k))
        out[best] += 1
    return out


def split_income(pool: float, stakes: dict[str, float], rules: Rules, saturation: float) -> dict[str, float]:
    """Distribute an income pool among investors.

    - Payout scales up to the full pool as total investment reaches pool * saturation.
    - If the top investor holds >= dominance_ratio x everyone else combined, they take
      dominance_share; the rest is split among the others by stake.
    - Otherwise it's proportional to stake.
    """
    stakes = {k: v for k, v in stakes.items() if v > 0}
    total = sum(stakes.values())
    if total <= 0 or pool <= 0:
        return {}
    paid = pool * min(1.0, total / (pool * saturation))
    top = max(stakes, key=stakes.get)
    others = total - stakes[top]
    if others <= 0:
        return {top: paid}
    if stakes[top] >= rules.dominance_ratio * others:
        out = {top: paid * rules.dominance_share}
        rest = paid * (1 - rules.dominance_share)
        for k, v in stakes.items():
            if k != top:
                out[k] = rest * v / others
        return out
    return {k: paid * v / total for k, v in stakes.items()}


@dataclass
class Game:
    map_data: dict
    players: dict[str, Player]
    rules: Rules = field(default_factory=Rules)
    seed: int | None = None

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self.regions = {r["id"]: r for r in self.map_data["regions"]}
        self.round = 1
        self.finished = False
        self.support = {rid: {} for rid in self.regions}          # rid -> pid -> support
        self.region_invest = {rid: {} for rid in self.regions}    # rid -> pid -> invested
        self.sector_invest = {s: {} for s in SECTORS}             # sector -> pid -> invested
        self.leaders: dict[str, str | None] = {rid: None for rid in self.regions}
        self.seats: dict[str, dict[str, int]] = {rid: {} for rid in self.regions}
        self.seat_history: list[dict[str, int]] = []
        self.pending: dict[str, list[dict]] = {}
        self.reports: list[dict] = []
        self.sector_market = self._roll_market()
        for p in self.players.values():
            p.money = self.rules.start_money

    # ---------- planning ----------
    def _roll_market(self) -> dict[str, float]:
        return {s: round(self.rng.uniform(0.8, 1.2), 2) for s in SECTORS}

    def validate(self, pid: str, actions: list[dict]) -> list[dict]:
        if self.finished:
            raise ActionError("game is over")
        if not isinstance(actions, list) or len(actions) > 60:
            raise ActionError("bad action list")
        clean, total = [], 0
        for a in actions:
            if not isinstance(a, dict):
                raise ActionError("bad action")
            t = a.get("type")
            try:
                amount = int(a.get("amount", 0))
            except (TypeError, ValueError):
                raise ActionError("bad amount")
            if amount < self.rules.min_action:
                raise ActionError(f"minimum spend per action is {self.rules.min_action}")
            c = {"type": t, "amount": amount}
            if t in ("rally", "invest_region"):
                c["region"] = self._region(a.get("region"))
            elif t == "invest_sector":
                c["sector"] = self._sector(a.get("sector"))
            elif t == "ad":
                kind = a.get("kind")
                if kind not in AD_KINDS:
                    raise ActionError("unknown ad kind")
                c["kind"] = kind
                if kind == "social":
                    c["sector"] = self._sector(a.get("sector"))
                elif kind == "negative":
                    c["region"] = self._region(a.get("region"))
                    target = a.get("target")
                    if target not in self.players or target == pid:
                        raise ActionError("bad negative-ad target")
                    c["target"] = target
            else:
                raise ActionError(f"unknown action {t!r}")
            total += amount
            clean.append(c)
        if total > self.players[pid].money + 1e-9:
            raise ActionError("not enough funds")
        return clean

    def _region(self, rid):
        if rid not in self.regions:
            raise ActionError("unknown region")
        return rid

    def _sector(self, s):
        if s not in SECTORS:
            raise ActionError("unknown sector")
        return s

    def submit(self, pid: str, actions: list[dict]) -> None:
        self.pending[pid] = self.validate(pid, actions)

    def all_submitted(self, pids) -> bool:
        return all(p in self.pending for p in pids)

    # ---------- resolution ----------
    def _add_support(self, rid: str, pid: str, amount: float) -> None:
        mult = ideology_multiplier(self.players[pid].right, self.regions[rid]["right"])
        s = self.support[rid]
        s[pid] = s.get(pid, 0) + amount * mult

    def resolve(self) -> dict:
        if self.finished:
            raise ActionError("game is over")
        total_seats = sum(r["seats"] for r in self.regions.values())
        events = []
        # 1. spending
        for pid, actions in self.pending.items():
            p = self.players[pid]
            for a in actions:
                amt = a["amount"]
                if amt > p.money + 1e-9:
                    continue  # money can't go negative even if validation was stale
                p.money -= amt
                t = a["type"]
                if t == "rally":
                    self._add_support(a["region"], pid, amt)
                elif t == "invest_region":
                    inv = self.region_invest[a["region"]]
                    inv[pid] = inv.get(pid, 0) + amt
                elif t == "invest_sector":
                    inv = self.sector_invest[a["sector"]]
                    inv[pid] = inv.get(pid, 0) + amt
                elif a["kind"] == "tv":
                    for rid, r in self.regions.items():
                        self._add_support(rid, pid, amt * self.rules.tv_efficiency * r["seats"] / total_seats)
                    events.append(f"{p.party} ran a national TV ad campaign")
                elif a["kind"] == "social":
                    rs = [r for r in self.regions.values() if r["sector"] == a["sector"]]
                    seats = sum(r["seats"] for r in rs) or 1
                    for r in rs:
                        self._add_support(r["id"], pid, amt * self.rules.social_efficiency * r["seats"] / seats)
                    events.append(f"{p.party} targeted {a['sector']} regions with social media ads")
                else:  # negative
                    s = self.support[a["region"]]
                    tgt = a["target"]
                    s[tgt] = max(0.0, s.get(tgt, 0) - amt * self.rules.negative_efficiency)
                    events.append(f"{p.party} attacked {self.players[tgt].party} in {self.regions[a['region']]['name']}")
        self.pending = {}

        # 2. election
        prev_leaders = dict(self.leaders)
        flips = []
        for rid, r in self.regions.items():
            votes = {pid: v for pid, v in self.support[rid].items() if v > 0 and pid in self.players}
            votes[INDEPENDENT] = r["seats"] * self.rules.independent_support_per_seat
            self.seats[rid] = {k: v for k, v in dhondt(votes, r["seats"]).items() if v}
            ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
            leader = ranked[0][0]
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                leader = prev_leaders[rid] if prev_leaders[rid] in (ranked[0][0], ranked[1][0]) else None
            self.leaders[rid] = leader if leader in self.players else None
            if self.leaders[rid] != prev_leaders[rid] and self.leaders[rid]:
                flips.append(rid)
        seat_totals = self.seat_totals()
        self.seat_history.append(seat_totals)

        # 3. income
        income = {pid: {"base": self.rules.base_income, "leadership": 0.0, "penalty": 0.0,
                        "regions": 0.0, "sectors": 0.0} for pid in self.players}
        for rid, leader in self.leaders.items():
            if leader:
                r = self.regions[rid]
                bonus = r["seats"] * self.rules.leader_bonus_per_seat
                pen = bonus * mismatch_penalty(self.players[leader].right, r["right"], self.rules)
                income[leader]["leadership"] += bonus
                income[leader]["penalty"] -= pen
        for rid, r in self.regions.items():
            pool = self.rules.region_pool_base + r["seats"] * self.rules.region_pool_per_seat
            for pid, amt in split_income(pool, self.region_invest[rid], self.rules, self.rules.region_saturation).items():
                income[pid]["regions"] += amt
        for s in SECTORS:
            pool = self.sector_pool(s)
            for pid, amt in split_income(pool, self.sector_invest[s], self.rules, self.rules.sector_saturation).items():
                income[pid]["sectors"] += amt
        for pid, inc in income.items():
            for k in inc:
                inc[k] = round(inc[k])
            inc["total"] = round(sum(inc.values()))
            self.players[pid].money = round(self.players[pid].money + inc["total"])

        # 4. decay + advance
        for rid in self.regions:
            for pid in self.support[rid]:
                self.support[rid][pid] *= 1 - self.rules.support_decay
        for rid in flips:
            events.append(f"{self.players[self.leaders[rid]].party} now leads {self.regions[rid]['name']}")
        report = {"round": self.round, "seats": seat_totals, "income": income, "events": events,
                  "market": self.sector_market}
        self.reports.append(report)
        if self.round >= self.rules.max_rounds or (self.round >= self.rules.rounds and not self.unclaimed()):
            self.finished = True
        else:
            self.round += 1
            self.sector_market = self._roll_market()
        return report

    # ---------- queries ----------
    def unclaimed(self) -> list[str]:
        """Regions with no party leading them (still held by independents or tied)."""
        return [rid for rid, leader in self.leaders.items() if leader is None]

    def sector_pool(self, sector: str) -> float:
        seats = sum(r["seats"] for r in self.regions.values() if r["sector"] == sector)
        return seats * self.rules.sector_pool_per_seat * SECTOR_MULT[sector] * self.sector_market[sector]

    def seat_totals(self) -> dict[str, int]:
        totals = {pid: 0 for pid in self.players}
        totals[INDEPENDENT] = 0
        for s in self.seats.values():
            for k, v in s.items():
                totals[k] = totals.get(k, 0) + v
        return totals

    def standings(self) -> list[dict]:
        totals = self.seat_totals()
        cumulative = {pid: sum(h.get(pid, 0) for h in self.seat_history) for pid in self.players}
        rows = [{"id": pid, "seats": totals.get(pid, 0), "cumulative": cumulative[pid],
                 "money": round(p.money), "regions_led": sum(1 for l in self.leaders.values() if l == pid)}
                for pid, p in self.players.items()]
        rows.sort(key=lambda r: (r["seats"], r["cumulative"], r["money"]), reverse=True)
        return rows

    def public_state(self) -> dict:
        def rnd(d):
            return {k: round(v) for k, v in d.items() if v > 0}
        return {
            "round": self.round, "rounds": self.rules.rounds, "max_rounds": self.rules.max_rounds,
            "finished": self.finished, "overtime": self.round > self.rules.rounds, "unclaimed": self.unclaimed(),
            "market": self.sector_market,
            "regions": {rid: {"support": rnd(self.support[rid]), "invest": rnd(self.region_invest[rid]),
                              "leader": self.leaders[rid], "seats": self.seats[rid],
                              "independent": r["seats"] * self.rules.independent_support_per_seat,
                              "pool": self.rules.region_pool_base + r["seats"] * self.rules.region_pool_per_seat}
                        for rid, r in self.regions.items()},
            "sectors": {s: rnd(self.sector_invest[s]) for s in SECTORS},
            "sector_pools": {s: round(self.sector_pool(s)) for s in SECTORS},
            "saturation": {"region": self.rules.region_saturation, "sector": self.rules.sector_saturation},
            "standings": self.standings(),
            "submitted": sorted(self.pending),
            "last_report": self.reports[-1] if self.reports else None,
            "seat_history": self.seat_history,
            "winner": self.standings()[0]["id"] if self.finished else None,
            "rules": {k: getattr(self.rules, k) for k in (
                "min_action", "dominance_ratio", "dominance_share", "leader_bonus_per_seat", "mismatch_grace",
                "mismatch_penalty_slope", "mismatch_penalty_cap", "tv_efficiency", "social_efficiency",
                "negative_efficiency", "support_decay")},
        }
