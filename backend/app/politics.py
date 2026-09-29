"""Ideology & government: policy platforms, ministries.

Pure data + small helpers used by the engine, bots and (via public state) the frontend.
"""
from __future__ import annotations

# ---------- policy platform ----------
# Each party takes a stance per issue: "left", "right" or "center" (no effect).
# `sectors`: campaign effectiveness bonus/malus in regions of that sector.
# `lean`: bonus with voters leaning that way, malus with the other side.
ISSUES = {
    "economy": {
        "name": "Economy", "icon": "💶",
        "left": {"label": "Welfare state", "sectors": {"agriculture": 0.15, "trade": -0.10}},
        "right": {"label": "Tax cuts", "sectors": {"trade": 0.15, "agriculture": -0.10}},
    },
    "environment": {
        "name": "Environment", "icon": "🌳",
        "left": {"label": "Green transition", "sectors": {"tourism": 0.15, "technology": 0.10, "industry": -0.10}},
        "right": {"label": "Industry first", "sectors": {"industry": 0.15, "tourism": -0.10}},
    },
    "globalisation": {
        "name": "Globalisation", "icon": "🌍",
        "left": {"label": "Protect local jobs", "sectors": {"industry": 0.10, "agriculture": 0.10, "trade": -0.10}},
        "right": {"label": "Open markets", "sectors": {"trade": 0.15, "technology": 0.10, "agriculture": -0.10}},
    },
    "society": {
        "name": "Society", "icon": "⚖️",
        "left": {"label": "Progressive values", "lean": "left"},
        "right": {"label": "Traditional values", "lean": "right"},
    },
}
STANCES = ("left", "center", "right")
LEAN_BONUS, LEAN_MALUS = 0.12, 0.08
CONTRADICTION_WEIGHT = 0.5   # a stance against your own ideology only half convinces voters


def leaning(region_right: float) -> str | None:
    return "right" if region_right > 0.55 else "left" if region_right < 0.45 else None


def contradicts(party_left: int, side: str) -> bool:
    return (side == "right" and party_left >= 60) or (side == "left" and party_left <= 40)


def platform_mult(platform: dict, party_left: int, sector: str, region_right: float) -> float:
    m = 1.0
    for issue, side in (platform or {}).items():
        if side not in ("left", "right") or issue not in ISSUES:
            continue
        opt = ISSUES[issue][side]
        eff = opt.get("sectors", {}).get(sector, 0.0)
        if "lean" in opt:
            lean = leaning(region_right)
            eff += LEAN_BONUS if lean == opt["lean"] else -LEAN_MALUS if lean else 0.0
        m *= 1 + eff * (CONTRADICTION_WEIGHT if contradicts(party_left, side) else 1.0)
    return m


def clean_platform(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("bad platform")
    out = {}
    for issue in ISSUES:
        side = data.get(issue, "center")
        if side not in STANCES:
            raise ValueError("bad stance")
        out[issue] = side
    return out


def default_platform(party_left: int) -> dict:
    side = "left" if party_left >= 60 else "right" if party_left <= 40 else "center"
    return {issue: side for issue in ISSUES}


# ---------- government ----------
MINISTRIES = {
    "finance":     {"name": "Finance", "icon": "💰", "text": "+100K income every round."},
    "economy":     {"name": "Economy & Industry", "icon": "🏭", "text": "+30% income from the industry and technology sectors."},
    "agriculture": {"name": "Agriculture", "icon": "🌾", "text": "+30% agriculture sector income, +15% rallies in agricultural regions."},
    "trade":       {"name": "Trade & Tourism", "icon": "🚢", "text": "+30% income from the trade and tourism sectors."},
    "media":       {"name": "Communications", "icon": "📺", "text": "TV and social media ads +25%."},
    "interior":    {"name": "Interior", "icon": "🛡️", "text": "+20% rallies in right-leaning regions; attack ads against you are halved."},
    "social":      {"name": "Social Affairs", "icon": "🤲", "text": "+20% rallies in left-leaning regions, +50K income."},
    "foreign":     {"name": "Foreign Affairs", "icon": "🌐", "text": "+10% to all your campaigning (international prestige)."},
}
MINISTRY_SECTORS = {"economy": ("industry", "technology"), "agriculture": ("agriculture",), "trade": ("trade", "tourism")}

# ---------- coalition tension ----------
TENSION_FREE_GAP = 0.25   # partners up to 25 points apart get along
TENSION_RATE = 0.30       # support lost per round per point of gap beyond that


def coalition_tension(lefts: list[int]) -> float:
    """Fraction of support every partner loses per round because their voters dislike the deal."""
    gap = (max(lefts) - min(lefts)) / 100 if lefts else 0
    return max(0.0, gap - TENSION_FREE_GAP) * TENSION_RATE
