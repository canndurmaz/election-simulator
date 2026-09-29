"""Election game rules. Pure logic, no networking.

Round flow (all players plan simultaneously, then the round resolves):
  1. Spend: campaign actions (rallies, ads) raise support; investments buy economic stakes.
  2. Election: every region votes. Plurality winner leads it; seats are split by D'Hondt.
  3. Income: base + leadership bonus (reduced by ideology mismatch) + region economy + sector economy.
  4. Support decays, a new event card is drawn, next round.
Game ends after the last round once every region has a winner. A coalition holding a majority wins
together; otherwise the largest party wins.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .maps import SECTORS
from .politics import (ISSUES, MINISTRIES, MINISTRY_SECTORS, coalition_tension, default_platform, leaning,
                       platform_mult)

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
    dominance_share: float = 0.80               # ... takes 80% of the income, permanently
    control_min_pools: float = 1.0              # ... and hold at least 1 round's worth of that economy's income
    permanent_region_control: bool = True       # region economies work like sectors (rule 7); False = contestable
    tv_efficiency: float = 1.2
    social_efficiency: float = 1.4
    negative_efficiency: float = 0.8
    mismatch_grace: float = 0.10                # ideology gap tolerated without penalty
    mismatch_penalty_slope: float = 1.5
    mismatch_penalty_cap: float = 0.9
    reposition_step: int = 10                     # ideology points moved per repositioning
    reposition_cost: int = 150_000
    credibility_penalty: float = 0.85             # campaign multiplier while voters doubt a repositioned party


SECTOR_MULT = {"technology": 1.3, "industry": 1.2, "trade": 1.1, "tourism": 1.0, "agriculture": 0.9}
AD_KINDS = {"tv", "social", "negative"}

# Party leader traits, picked in the lobby.
TRAITS = {
    "orator":     {"name": "Orator", "icon": "🎤", "text": "Rallies are 25% more effective."},
    "media":      {"name": "Media darling", "icon": "📺", "text": "TV and social media ads are 30% more effective."},
    "tycoon":     {"name": "Tycoon", "icon": "💼", "text": "+20% income from province/state economies and sectors."},
    "fundraiser": {"name": "Fundraiser", "icon": "💰", "text": "Base income +60%."},
    "populist":   {"name": "Populist", "icon": "🧲", "text": "Broad appeal: campaigns never drop below ×0.8 ideology fit, and mismatch penalties are halved."},
    "grassroots": {"name": "Grassroots", "icon": "🌱", "text": "Your support fades only 5% per round instead of 10%."},
}
DEFAULT_TRAIT = "orator"


def event_card(kind: str, **kw) -> dict:
    """Human-readable event card; effects are applied by Game via `kind` and params."""
    sec, reg, party = kw.get("sector"), kw.get("region_name"), kw.get("party_name")
    cards = {
        "opening":    ("🗞️", "Campaign season opens", "No special effects this round. Plan your opening moves!"),
        "calm":       ("☕", "A quiet week", "Nothing unusual happens this round."),
        "boom":       ("📈", f"{str(sec).title()} boom", f"The {sec} sector pays out 60% more this round."),
        "crash":      ("📉", f"{str(sec).title()} slump", f"The {sec} sector pays out 50% less this round."),
        "scandal":    ("🕵️", f"Scandal hits {party}", f"{party} loses 25% of its support everywhere before this round's vote."),
        "debate":     ("🎙️", "TV debate night", "TV and social media ads are 50% more effective this round."),
        "disaster":   ("🌪️", f"Disaster in {reg}", f"{reg}'s economy pays nothing this round, but rallies there are 50% more effective."),
        "apathy":     ("😴", "Voter apathy", "Independents are 50% stronger in every region this round."),
        "wave_left":  ("🌹", "Leftward mood swing", "Voters everywhere lean 15 points further left this round."),
        "wave_right": ("🦅", "Rightward mood swing", "Voters everywhere lean 15 points further right this round."),
        "donors":     ("🎁", "Donor season", "Every party receives an extra 150K in this round's income."),
        "underdog":   ("🤲", "Sympathy for the underdog", "The party with the fewest seats receives an extra 400K this round."),
        "econ_crisis":     ("🏚️", "Economic crisis", "Voters want protection: left-leaning parties campaign 25% more effectively this round."),
        "security_crisis": ("🚨", "Security crisis", "Voters want order: right-leaning parties campaign 25% more effectively this round."),
    }
    icon, title, text = cards[kind]
    return {"kind": kind, "icon": icon, "title": title, "text": text, **kw}


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
    trait: str = DEFAULT_TRAIT
    platform: dict = field(default_factory=dict)   # issue -> "left" | "center" | "right"
    credibility_until: int = 0                     # after repositioning, campaigns are weaker up to this round

    def __post_init__(self):
        if not self.platform:
            self.platform = default_platform(self.left)

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


def dominant(stakes: dict[str, float], rules: Rules, pool: float) -> str | None:
    """The investor holding >= dominance_ratio x everyone else combined and at least
    control_min_pools x the per-round pool (so a token investment can't claim an economy forever), or None."""
    stakes = {k: v for k, v in stakes.items() if v > 0}
    if not stakes:
        return None
    top = max(stakes, key=stakes.get)
    others = sum(stakes.values()) - stakes[top]
    ok = stakes[top] >= rules.dominance_ratio * others and stakes[top] >= rules.control_min_pools * pool
    return top if ok else None


def split_income(pool: float, stakes: dict[str, float], rules: Rules, saturation: float,
                 owner: str | None = None) -> dict[str, float]:
    """Distribute an income pool among investors.

    - Payout scales up to the full pool as total investment reaches pool * saturation.
    - An owner (who once reached >= dominance_ratio x everyone else combined) always takes
      dominance_share; the rest is split among the other investors by stake (all of it if there are none).
    - Without an owner it's proportional to stake.
    """
    stakes = {k: v for k, v in stakes.items() if v > 0}
    total = sum(stakes.values())
    if total <= 0 or pool <= 0:
        return {}
    paid = pool * min(1.0, total / (pool * saturation))
    if owner is None:
        return {k: paid * v / total for k, v in stakes.items()}
    others = {k: v for k, v in stakes.items() if k != owner}
    rest_total = sum(others.values())
    if rest_total <= 0:
        return {owner: paid}
    out = {owner: paid * rules.dominance_share}
    for k, v in others.items():
        out[k] = paid * (1 - rules.dominance_share) * v / rest_total
    return out


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
        self.sector_owner: dict[str, str | None] = {s: None for s in SECTORS}          # permanent 80% holder
        self.region_owner: dict[str, str | None] = {rid: None for rid in self.regions}
        self.leaders: dict[str, str | None] = {rid: None for rid in self.regions}
        self.seats: dict[str, dict[str, int]] = {rid: {} for rid in self.regions}
        self.seat_history: list[dict[str, int]] = []
        self.pending: dict[str, list[dict]] = {}
        self.reports: list[dict] = []
        self.coalitions: list[set[str]] = []            # groups of 2+ parties
        self.proposals: set[tuple[str, str]] = set()    # (from, to)
        self.sector_market = self._roll_market()
        self.event = event_card("opening")
        self.news: list[str] = []                       # diplomacy news, shown with the next round report
        self.government: list[str] = []                 # bloc with the most seats after the last election
        self.cabinets: dict[frozenset, dict] = {}       # coalition -> agreed {ministry: pid}
        self.cabinet_proposals: dict[frozenset, dict] = {}  # coalition -> {"by", "alloc", "accepted"}
        for p in self.players.values():
            p.money = self.rules.start_money

    # ---------- events ----------
    def _draw_event(self) -> dict:
        kinds = ["boom", "crash", "scandal", "debate", "disaster", "apathy", "wave_left", "wave_right",
                 "donors", "underdog", "calm", "econ_crisis", "security_crisis"]
        weights = [10, 8, 9, 9, 8, 7, 6, 6, 7, 7, 6, 6, 6]
        kind = self.rng.choices(kinds, weights)[0]
        if kind in ("boom", "crash"):
            return event_card(kind, sector=self.rng.choice(SECTORS))
        if kind == "disaster":
            r = self.rng.choice(list(self.regions.values()))
            return event_card(kind, region=r["id"], region_name=r["name"])
        if kind == "scandal":
            totals = self.seat_totals()
            pids = list(self.players)
            pid = self.rng.choices(pids, [1 + totals.get(p, 0) for p in pids])[0]  # front-runners attract scrutiny
            return event_card(kind, party=pid, party_name=self.players[pid].party)
        return event_card(kind)

    def _ev(self, kind: str) -> bool:
        return self.event["kind"] == kind

    # ---------- traits ----------
    def _trait(self, pid: str) -> str:
        return self.players[pid].trait

    def effective_multiplier(self, pid: str, rid: str) -> float:
        """Campaign effectiveness of pid in rid, including traits and this round's mood swing."""
        rr = self.regions[rid]["right"]
        if self._ev("wave_left"):
            rr = max(0.0, rr - 0.15)
        elif self._ev("wave_right"):
            rr = min(1.0, rr + 0.15)
        m = ideology_multiplier(self.players[pid].right, rr)
        if self._trait(pid) == "populist":
            m = max(m, 0.8)
        return m

    def independent_support(self, rid: str) -> float:
        base = self.regions[rid]["seats"] * self.rules.independent_support_per_seat
        return base * (1.5 if self._ev("apathy") else 1.0)

    def campaign_mult(self, pid: str, rid: str, kind: str = "rally") -> float:
        """Everything that scales a party's campaigning in a region (traits like Orator/Media are applied
        per action type in resolve)."""
        p, r = self.players[pid], self.regions[rid]
        m = self.effective_multiplier(pid, rid) * platform_mult(p.platform, p.left, r["sector"], r["right"])
        if p.credibility_until >= self.round:
            m *= self.rules.credibility_penalty
        if (self._ev("econ_crisis") and p.left >= 55) or (self._ev("security_crisis") and p.left <= 45):
            m *= 1.25
        if self.holds(pid, "foreign"):
            m *= 1.1
        if kind == "rally":
            lean = leaning(r["right"])
            if self.holds(pid, "agriculture") and r["sector"] == "agriculture":
                m *= 1.15
            if (self.holds(pid, "interior") and lean == "right") or (self.holds(pid, "social") and lean == "left"):
                m *= 1.2
        return m

    # ---------- government & ministries ----------
    def ministry_holder(self, ministry: str) -> str | None:
        if not self.government:
            return None
        if len(self.government) == 1:
            return self.government[0]          # single-party government holds every ministry
        return self.cabinets.get(frozenset(self.government), {}).get(ministry)

    def holds(self, pid: str, ministry: str) -> bool:
        return self.ministry_holder(ministry) == pid

    def _form_government(self, totals: dict) -> None:
        blocs = [sorted(c) for c in self.coalitions] + [[p] for p in self.players if not self.partners(p)]
        scored = sorted(((sum(totals.get(p, 0) for p in b), b) for b in blocs), key=lambda x: -x[0])
        new = scored[0][1] if scored and scored[0][0] > 0 and (len(scored) < 2 or scored[0][0] > scored[1][0]) else []
        if new != self.government and new:
            self.news.append(f"🏛️ New government: {' + '.join(self.players[p].party for p in new)}")
        self.government = new

    def propose_cabinet(self, pid: str, alloc: dict) -> None:
        group = self.coalition_of(pid)
        if len(group) < 2:
            raise ActionError("only coalitions negotiate ministries")
        if not isinstance(alloc, dict) or set(alloc) != set(MINISTRIES) or not set(alloc.values()) <= group:
            raise ActionError("assign every ministry to a coalition member")
        key = frozenset(group)
        self.cabinet_proposals[key] = {"by": pid, "alloc": dict(alloc), "accepted": {pid}}
        self._check_cabinet(key)

    def answer_cabinet(self, pid: str, accept: bool) -> None:
        key = frozenset(self.coalition_of(pid))
        prop = self.cabinet_proposals.get(key)
        if not prop:
            raise ActionError("no ministry proposal to answer")
        if accept:
            prop["accepted"].add(pid)
            self._check_cabinet(key)
        else:
            del self.cabinet_proposals[key]
            self.news.append(f"❌ {self.players[pid].party} rejected the ministry deal")

    def _check_cabinet(self, key: frozenset) -> None:
        prop = self.cabinet_proposals[key]
        if prop["accepted"] >= key:
            self.cabinets[key] = prop["alloc"]
            del self.cabinet_proposals[key]
            self.news.append(f"📜 {' + '.join(self.players[p].party for p in sorted(key))} agreed how to share the ministries")

    def _prune_cabinets(self) -> None:
        live = {frozenset(c) for c in self.coalitions}
        self.cabinets = {k: v for k, v in self.cabinets.items() if k in live}
        self.cabinet_proposals = {k: v for k, v in self.cabinet_proposals.items() if k in live}
        if self.government and len(self.government) > 1 and frozenset(self.government) not in live:
            self.government = []   # the governing coalition broke up: caretaker period until the next election

    # ---------- coalitions ----------
    def coalition_of(self, pid: str) -> set[str]:
        return next((c for c in self.coalitions if pid in c), {pid})

    def partners(self, pid: str) -> set[str]:
        return self.coalition_of(pid) - {pid}

    def propose(self, a: str, b: str) -> bool:
        """a invites b's group to join a's. Returns True if it merged immediately (b had also invited a)."""
        if a == b or b not in self.players or self.finished:
            raise ActionError("bad coalition target")
        if b in self.partners(a):
            raise ActionError("already in a coalition together")
        if (b, a) in self.proposals:
            self.respond(a, b, True)
            return True
        self.proposals.add((a, b))
        return False

    def respond(self, b: str, a: str, accept: bool) -> None:
        """b answers a's proposal."""
        if (a, b) not in self.proposals:
            raise ActionError("no such proposal")
        self.proposals.discard((a, b))
        if not accept:
            return
        self.proposals.discard((b, a))
        merged = self.coalition_of(a) | self.coalition_of(b)
        self.coalitions = [c for c in self.coalitions if not (c & merged)] + [merged]
        self._prune_cabinets()
        self.news.append(f"🤝 {' + '.join(self.players[p].party for p in sorted(merged))} formed a coalition")

    def leave_coalition(self, a: str) -> None:
        group = self.coalition_of(a)
        if len(group) < 2:
            raise ActionError("not in a coalition")
        rest = group - {a}
        self.coalitions = [c for c in self.coalitions if c is not group] + ([rest] if len(rest) > 1 else [])
        self._prune_cabinets()
        self.news.append(f"💔 {self.players[a].party} left its coalition")

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
            elif t == "reposition":
                if a.get("direction") not in ("left", "right"):
                    raise ActionError("reposition left or right")
                if any(x["type"] == "reposition" for x in clean):
                    raise ActionError("you can only reposition once per round")
                c["direction"] = a["direction"]
                c["amount"] = amount = self.rules.reposition_cost
            elif t == "transfer":
                target = a.get("target")
                if target not in self.players or target == pid:
                    raise ActionError("bad transfer target")
                c["target"] = target
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
                    if target in self.partners(pid):
                        raise ActionError("you can't attack a coalition partner")
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
    def _add_support(self, rid: str, pid: str, amount: float, kind: str = "rally") -> None:
        s = self.support[rid]
        s[pid] = s.get(pid, 0) + amount * self.campaign_mult(pid, rid, kind)

    def resolve(self) -> dict:
        if self.finished:
            raise ActionError("game is over")
        total_seats = sum(r["seats"] for r in self.regions.values())
        events = [f"{self.event['icon']} {self.event['title']}"] + self.news
        self.news = []
        ad_boost = 1.5 if self._ev("debate") else 1.0
        # scandal strikes before the vote
        if self._ev("scandal"):
            for sup in self.support.values():
                if self.event["party"] in sup:
                    sup[self.event["party"]] *= 0.75
        # 0. repositioning: the party moves, voters are wary for a while
        for pid, actions in self.pending.items():
            for a in actions:
                if a["type"] == "reposition" and a["amount"] <= self.players[pid].money:
                    p = self.players[pid]
                    step = self.rules.reposition_step
                    p.left = max(0, min(100, p.left + (step if a["direction"] == "left" else -step)))
                    p.credibility_until = self.round + 1
                    events.append(f"🧭 {p.party} repositioned to {p.left}% left / {100 - p.left}% right")
        # 1. spending
        for pid, actions in self.pending.items():
            p = self.players[pid]
            for a in actions:
                amt = a["amount"]
                if amt > p.money + 1e-9:
                    continue  # money can't go negative even if validation was stale
                p.money -= amt
                t = a["type"]
                tr = p.trait
                if t == "rally":
                    boost = (1.25 if tr == "orator" else 1.0) * (1.5 if self._ev("disaster") and a["region"] == self.event["region"] else 1.0)
                    self._add_support(a["region"], pid, amt * boost)
                elif t == "reposition":
                    pass  # applied above
                elif t == "transfer":
                    self.players[a["target"]].money += amt
                    events.append(f"💸 {p.party} sent money to {self.players[a['target']].party}")
                elif t == "invest_region":
                    inv = self.region_invest[a["region"]]
                    inv[pid] = inv.get(pid, 0) + amt
                elif t == "invest_sector":
                    inv = self.sector_invest[a["sector"]]
                    inv[pid] = inv.get(pid, 0) + amt
                elif a["kind"] == "tv":
                    eff = self.rules.tv_efficiency * ad_boost * (1.3 if tr == "media" else 1.0) * (1.25 if self.holds(pid, "media") else 1.0)
                    for rid, r in self.regions.items():
                        self._add_support(rid, pid, amt * eff * r["seats"] / total_seats, "ad")
                    events.append(f"{p.party} ran a national TV ad campaign")
                elif a["kind"] == "social":
                    rs = [r for r in self.regions.values() if r["sector"] == a["sector"]]
                    seats = sum(r["seats"] for r in rs) or 1
                    eff = self.rules.social_efficiency * ad_boost * (1.3 if tr == "media" else 1.0) * (1.25 if self.holds(pid, "media") else 1.0)
                    for r in rs:
                        self._add_support(r["id"], pid, amt * eff * r["seats"] / seats, "ad")
                    events.append(f"{p.party} targeted {a['sector']} regions with social media ads")
                else:  # negative
                    s = self.support[a["region"]]
                    tgt = a["target"]
                    hit = amt * self.rules.negative_efficiency * (0.5 if self.holds(tgt, "interior") else 1.0)
                    s[tgt] = max(0.0, s.get(tgt, 0) - hit)
                    events.append(f"{p.party} attacked {self.players[tgt].party} in {self.regions[a['region']]['name']}")
        self.pending = {}

        # 2. election
        prev_leaders = dict(self.leaders)
        flips = []
        for rid, r in self.regions.items():
            votes = {pid: v for pid, v in self.support[rid].items() if v > 0 and pid in self.players}
            votes[INDEPENDENT] = self.independent_support(rid)
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
        self._form_government(seat_totals)
        events += self.news
        self.news = []

        # 3. income
        income = {pid: {"base": self.rules.base_income * (1.6 if p.trait == "fundraiser" else 1.0),
                        "leadership": 0.0, "penalty": 0.0, "regions": 0.0, "sectors": 0.0, "event": 0.0,
                        "ministries": (100_000 if self.holds(pid, "finance") else 0) + (50_000 if self.holds(pid, "social") else 0)}
                  for pid, p in self.players.items()}
        for rid, leader in self.leaders.items():
            if leader:
                r = self.regions[rid]
                bonus = r["seats"] * self.rules.leader_bonus_per_seat
                pen = bonus * mismatch_penalty(self.players[leader].right, r["right"], self.rules)
                if self._trait(leader) == "populist":
                    pen /= 2
                income[leader]["leadership"] += bonus
                income[leader]["penalty"] -= pen
        # economic control: first to reach 3x everyone else combined owns it for the rest of the game
        for s_ in SECTORS:
            if self.sector_owner[s_] is None and (d := dominant(self.sector_invest[s_], self.rules, self.sector_pool(s_))):
                self.sector_owner[s_] = d
                events.append(f"{self.players[d].party} took permanent control of the {s_} sector")
        for rid, r in self.regions.items():
            owner = self.region_owner[rid]
            pool = self.rules.region_pool_base + r["seats"] * self.rules.region_pool_per_seat
            if owner is None and (d := dominant(self.region_invest[rid], self.rules, pool)):
                if self.rules.permanent_region_control:
                    self.region_owner[rid] = owner = d
                    events.append(f"{self.players[d].party} took control of {r['name']}'s economy")
                else:
                    owner = d
            if self._ev("disaster") and rid == self.event["region"]:
                continue
            for pid, amt in split_income(pool, self.region_invest[rid], self.rules, self.rules.region_saturation,
                                         owner).items():
                income[pid]["regions"] += amt
        for s_ in SECTORS:
            pool = self.sector_pool(s_)
            if self.event.get("sector") == s_:
                pool *= 1.6 if self._ev("boom") else 0.5
            for pid, amt in split_income(pool, self.sector_invest[s_], self.rules,
                                         self.rules.sector_saturation, self.sector_owner[s_]).items():
                if any(self.holds(pid, m) and s_ in secs for m, secs in MINISTRY_SECTORS.items()):
                    amt *= 1.3
                income[pid]["sectors"] += amt
        for pid, inc in income.items():
            if self._trait(pid) == "tycoon":
                inc["regions"] *= 1.2
                inc["sectors"] *= 1.2
        if self._ev("donors"):
            for inc in income.values():
                inc["event"] += 150_000
        if self._ev("underdog"):
            last = min(self.players, key=lambda p_: (seat_totals.get(p_, 0), self.players[p_].money))
            income[last]["event"] += 400_000
        for pid, inc in income.items():
            for k in inc:
                inc[k] = round(inc[k])
            inc["total"] = round(sum(inc.values()))
            self.players[pid].money = round(self.players[pid].money + inc["total"])

        # 4. decay + advance
        for rid, r in self.regions.items():
            for pid in self.support[rid]:
                # loyal base: voters who share your ideology stay (0.5x decay), opponents drift away (up to 1.5x)
                decay = self.rules.support_decay * (0.5 + abs(self.players[pid].right - r["right"]))
                if self._trait(pid) == "grassroots":
                    decay /= 2
                self.support[rid][pid] *= 1 - decay
        for c in self.coalitions:
            t = coalition_tension([self.players[p].left for p in c])
            if t > 0:
                for sup in self.support.values():
                    for p in c:
                        if p in sup:
                            sup[p] *= 1 - t
                events.append(f"😠 Coalition tension: {' + '.join(self.players[p].party for p in sorted(c))} "
                              f"each lost {round(t * 100)}% support (their voters dislike the ideological gap)")
        for rid in flips:
            events.append(f"{self.players[self.leaders[rid]].party} now leads {self.regions[rid]['name']}")
        report = {"round": self.round, "seats": seat_totals, "income": income, "events": events,
                  "market": self.sector_market, "event": self.event}
        self.reports.append(report)
        if self.round >= self.rules.max_rounds or (self.round >= self.rules.rounds and not self.unclaimed()):
            self.finished = True
        else:
            self.round += 1
            self.sector_market = self._roll_market()
            self.event = self._draw_event()
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

    def result(self) -> dict:
        """Who governs: a coalition holding a majority wins together; otherwise the largest party."""
        totals = self.seat_totals()
        majority = sum(r["seats"] for r in self.regions.values()) // 2 + 1
        for c in self.coalitions:
            seats = sum(totals.get(p, 0) for p in c)
            if seats >= majority:
                lead = max(c, key=lambda p: (totals.get(p, 0), self.players[p].money))
                return {"winners": sorted(c, key=lambda p: -totals.get(p, 0)), "lead": lead,
                        "coalition": True, "seats": seats, "majority": majority}
        lead = self.standings()[0]["id"]
        return {"winners": [lead], "lead": lead, "coalition": False, "seats": totals.get(lead, 0),
                "majority": majority}

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
                              "leader": self.leaders[rid], "seats": self.seats[rid], "owner": self.region_owner[rid],
                              "independent": r["seats"] * self.rules.independent_support_per_seat,
                              "pool": self.rules.region_pool_base + r["seats"] * self.rules.region_pool_per_seat}
                        for rid, r in self.regions.items()},
            "sectors": {s: rnd(self.sector_invest[s]) for s in SECTORS},
            "sector_owner": self.sector_owner,
            "sector_pools": {s: round(self.sector_pool(s)) for s in SECTORS},
            "saturation": {"region": self.rules.region_saturation, "sector": self.rules.sector_saturation},
            "standings": self.standings(),
            "submitted": sorted(self.pending),
            "last_report": self.reports[-1] if self.reports else None,
            "seat_history": self.seat_history,
            "winner": self.result()["lead"] if self.finished else None,
            "result": self.result() if self.finished else None,
            "event": self.event,
            "coalitions": [sorted(c) for c in self.coalitions],
            "proposals": sorted(self.proposals),
            "traits": {pid: p.trait for pid, p in self.players.items()},
            "ideology": {pid: p.left for pid, p in self.players.items()},
            "platforms": {pid: p.platform for pid, p in self.players.items()},
            "credibility": {pid: p.credibility_until for pid, p in self.players.items()},
            "government": self.government,
            "ministries": {m: self.ministry_holder(m) for m in MINISTRIES},
            "cabinets": [{"members": sorted(k), "alloc": v} for k, v in self.cabinets.items()],
            "cabinet_proposals": [{"members": sorted(k), "by": v["by"], "alloc": v["alloc"], "accepted": sorted(v["accepted"])}
                                  for k, v in self.cabinet_proposals.items()],
            "tension": [{"members": sorted(c), "loss": round(coalition_tension([self.players[p].left for p in c]), 3)}
                        for c in self.coalitions],
            "rules": {k: getattr(self.rules, k) for k in (
                "min_action", "dominance_ratio", "dominance_share", "leader_bonus_per_seat", "mismatch_grace",
                "mismatch_penalty_slope", "mismatch_penalty_cap", "tv_efficiency", "social_efficiency",
                "negative_efficiency", "support_decay", "control_min_pools", "reposition_step", "reposition_cost",
                "credibility_penalty")},
        }
