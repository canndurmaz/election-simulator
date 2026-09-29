"""Builds backend/maps/germany.json from a German states (Bundesländer) GeoJSON + the game data table below.

Seats: members of the 21st Bundestag per state, 2025 federal election (630 total).
right: 2025 party-list vote share of CDU/CSU + AfD + FDP among the seven main parties
       (vs SPD, Greens, Linke, BSW), rounded.
sector: gameplay assignment.

Usage: python tools/build_germany.py path/to/bundeslaender.geo.json
       (e.g. isellsoap/deutschlandGeoJSON 2_bundeslaender/3_mittel.geo.json)
"""
import json, pathlib, sys

# GeoJSON name: (abbr, seats, sector, right)
DATA = {
    "Baden-Württemberg": ("BW", 79, "technology", 0.60), "Bayern": ("BY", 101, "technology", 0.65),
    "Berlin": ("BE", 24, "technology", 0.39), "Brandenburg": ("BB", 21, "agriculture", 0.56),
    "Bremen": ("HB", 5, "trade", 0.40), "Hamburg": ("HH", 13, "trade", 0.37),
    "Hessen": ("HE", 45, "trade", 0.54), "Mecklenburg-Vorpommern": ("MV", 13, "tourism", 0.58),
    "Niedersachsen": ("NI", 65, "agriculture", 0.52), "Nordrhein-Westfalen": ("NW", 136, "industry", 0.53),
    "Rheinland-Pfalz": ("RP", 31, "agriculture", 0.58), "Saarland": ("SL", 8, "industry", 0.55),
    "Sachsen": ("SN", 30, "technology", 0.63), "Sachsen-Anhalt": ("ST", 16, "agriculture", 0.61),
    "Schleswig-Holstein": ("SH", 25, "tourism", 0.52), "Thüringen": ("TH", 18, "industry", 0.61),
}
# city states too small to click: clickable box in the North Sea, (lon, lat)
CALLOUTS = {"HB": (6.3, 54.35), "HH": (7.3, 54.95)}
# label position overrides (lon, lat): Brandenburg's centre is Berlin
LABELS = {"BB": (13.4, 51.95)}


def rnd(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 3), round(coords[1], 3)]
    return [rnd(c) for c in coords]


def main(src):
    gj = json.load(open(src, encoding="utf-8"))
    regions = []
    for f in gj["features"]:
        name = f["properties"]["name"]
        abbr, seats, sector, right = DATA[name]
        r = {"id": abbr.lower(), "name": name, "abbr": abbr, "seats": seats, "sector": sector, "right": right,
             "geometry": {"type": f["geometry"]["type"], "coordinates": rnd(f["geometry"]["coordinates"])}}
        if abbr in LABELS:
            r["label"] = list(LABELS[abbr])
        if abbr in CALLOUTS:
            r["callout"] = list(CALLOUTS[abbr])
        regions.append(r)
    total = sum(r["seats"] for r in regions)
    assert len(regions) == 16 and total == 630, (len(regions), total)
    out = {"id": "germany", "name": "Deutschland", "seat_label": "Seats", "region_label": "State",
           "currency": "€", "projection": "equirect", "regions": sorted(regions, key=lambda r: r["name"])}
    dest = pathlib.Path(__file__).resolve().parent.parent / "backend" / "maps" / "germany.json"
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("wrote", dest, total, "seats")


if __name__ == "__main__":
    main(sys.argv[1])
