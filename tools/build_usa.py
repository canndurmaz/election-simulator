"""Builds backend/maps/usa.json from a US states GeoJSON + the game data table below.

Seats: electoral votes, 2024-2028 apportionment (538 total; Maine/Nebraska treated as winner-take-all units).
right: approximate 2024 Republican two-party share (0..1), rounded for gameplay.
Geometry is pre-projected (Albers equal-area, Alaska/Hawaii as insets), so the map uses projection "planar".

Usage: python tools/build_usa.py path/to/us-states.json
"""
import json, math, pathlib, sys

# name: (abbr, electoral votes, sector, right)
DATA = {
    "Alabama": ("AL", 9, "industry", 0.65), "Alaska": ("AK", 3, "tourism", 0.56), "Arizona": ("AZ", 11, "tourism", 0.53),
    "Arkansas": ("AR", 6, "agriculture", 0.65), "California": ("CA", 54, "technology", 0.39), "Colorado": ("CO", 10, "technology", 0.44),
    "Connecticut": ("CT", 7, "trade", 0.42), "Delaware": ("DE", 3, "trade", 0.42), "District of Columbia": ("DC", 3, "trade", 0.07),
    "Florida": ("FL", 30, "tourism", 0.57), "Georgia": ("GA", 16, "trade", 0.51), "Hawaii": ("HI", 4, "tourism", 0.38),
    "Idaho": ("ID", 4, "agriculture", 0.68), "Illinois": ("IL", 19, "trade", 0.44), "Indiana": ("IN", 11, "industry", 0.59),
    "Iowa": ("IA", 6, "agriculture", 0.57), "Kansas": ("KS", 6, "agriculture", 0.58), "Kentucky": ("KY", 8, "industry", 0.65),
    "Louisiana": ("LA", 8, "industry", 0.61), "Maine": ("ME", 4, "tourism", 0.47), "Maryland": ("MD", 10, "trade", 0.35),
    "Massachusetts": ("MA", 11, "technology", 0.37), "Michigan": ("MI", 15, "industry", 0.51), "Minnesota": ("MN", 10, "agriculture", 0.48),
    "Mississippi": ("MS", 6, "agriculture", 0.62), "Missouri": ("MO", 10, "agriculture", 0.59), "Montana": ("MT", 4, "agriculture", 0.60),
    "Nebraska": ("NE", 5, "agriculture", 0.60), "Nevada": ("NV", 6, "tourism", 0.52), "New Hampshire": ("NH", 4, "tourism", 0.48),
    "New Jersey": ("NJ", 14, "trade", 0.47), "New Mexico": ("NM", 5, "tourism", 0.47), "New York": ("NY", 28, "trade", 0.44),
    "North Carolina": ("NC", 16, "technology", 0.52), "North Dakota": ("ND", 3, "agriculture", 0.68), "Ohio": ("OH", 17, "industry", 0.56),
    "Oklahoma": ("OK", 7, "industry", 0.67), "Oregon": ("OR", 8, "technology", 0.42), "Pennsylvania": ("PA", 19, "industry", 0.51),
    "Rhode Island": ("RI", 4, "tourism", 0.43), "South Carolina": ("SC", 9, "industry", 0.59), "South Dakota": ("SD", 3, "agriculture", 0.64),
    "Tennessee": ("TN", 11, "industry", 0.65), "Texas": ("TX", 40, "industry", 0.57), "Utah": ("UT", 6, "technology", 0.62),
    "Vermont": ("VT", 3, "tourism", 0.33), "Virginia": ("VA", 13, "technology", 0.47), "Washington": ("WA", 12, "technology", 0.40),
    "West Virginia": ("WV", 4, "industry", 0.71), "Wisconsin": ("WI", 10, "industry", 0.50), "Wyoming": ("WY", 3, "industry", 0.73),
}
# small eastern states get a clickable box in the Atlantic: (lon, lat) of the box
CALLOUTS = {"VT": (-66.8, 45.2), "NH": (-66.8, 44.0), "MA": (-66.8, 42.8), "RI": (-66.8, 41.6), "CT": (-66.8, 40.4),
            "NJ": (-66.8, 39.2), "DE": (-66.8, 38.0), "MD": (-66.8, 36.8), "DC": (-66.8, 35.6)}


def albers(lat1, lat2, lon0, lat0):
    p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
    n = (math.sin(p1) + math.sin(p2)) / 2
    C = math.cos(p1) ** 2 + 2 * n * math.sin(p1)
    rho0 = math.sqrt(C - 2 * n * math.sin(p0)) / n

    def f(lon, lat):
        if lon > 0:  # Aleutians across the antimeridian
            lon -= 360
        rho = math.sqrt(C - 2 * n * math.sin(math.radians(lat))) / n
        t = n * math.radians(lon - lon0)
        return rho * math.sin(t), rho0 - rho * math.cos(t)
    return f


MAIN = albers(29.5, 45.5, -96, 37.5)
AK = albers(55, 65, -154, 50)
HI = albers(8, 18, -157, 3)


def walk(coords, fn):
    if isinstance(coords[0], (int, float)):
        return fn(*coords)
    return [walk(c, fn) for c in coords]


def points(coords):
    if isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords:
            yield from points(c)


def bbox(pts):
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def main(src):
    feats = {f["properties"]["name"]: f["geometry"] for f in json.load(open(src, encoding="utf-8"))["features"]}
    proj = {}
    for name in DATA:
        fn = AK if name == "Alaska" else HI if name == "Hawaii" else MAIN
        g = feats[name]
        proj[name] = {"type": g["type"], "coordinates": walk(g["coordinates"], lambda x, y: list(fn(x, y)))}
    main_pts = [p for n, g in proj.items() if n not in ("Alaska", "Hawaii") for p in points(g["coordinates"])]
    mx0, my0, mx1, my1 = bbox(main_pts)
    W = mx1 - mx0

    def place(name, width_frac, left_frac, bottom_pad):
        g = proj[name]
        x0, y0, x1, y1 = bbox(list(points(g["coordinates"])))
        s = W * width_frac / (x1 - x0)
        ox, oy = mx0 + W * left_frac, my0 - bottom_pad * W
        g["coordinates"] = walk(g["coordinates"], lambda x, y: [ox + (x - x0) * s, oy + (y - y0) * s])
    place("Alaska", 0.18, 0.0, 0.01)
    place("Hawaii", 0.10, 0.22, 0.0)

    scale = 1000 / W  # normalise to ~1000 units wide, keeps rounding simple
    regions = []
    for name, (abbr, ev, sector, right) in DATA.items():
        g = proj[name]
        r = {"id": abbr.lower(), "name": name, "abbr": abbr, "seats": ev, "sector": sector, "right": right,
             "geometry": {"type": g["type"], "coordinates": walk(g["coordinates"], lambda x, y: [round(x * scale, 1), round(y * scale, 1)])}}
        if abbr in CALLOUTS:
            x, y = MAIN(*CALLOUTS[abbr])
            r["callout"] = [round(x * scale, 1), round(y * scale, 1)]
        regions.append(r)
    total = sum(r["seats"] for r in regions)
    assert len(regions) == 51 and total == 538, (len(regions), total)
    out = {"id": "usa", "name": "United States", "seat_label": "Electoral votes", "region_label": "State",
           "currency": "$", "projection": "planar", "regions": sorted(regions, key=lambda r: r["name"])}
    dest = pathlib.Path(__file__).resolve().parent.parent / "backend" / "maps" / "usa.json"
    json.dump(out, open(dest, "w", encoding="utf-8"), separators=(",", ":"))
    print("wrote", dest, total, "electoral votes")


if __name__ == "__main__":
    main(sys.argv[1])
