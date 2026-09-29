"""Builds backend/maps/turkey.json from a province GeoJSON + the game data table below.

Seats: 2023 Grand National Assembly allocation (600 total).
right: approximate share of right-leaning vote (0..1), rough 2023-based estimate for gameplay.
sector: one of agriculture / industry / trade / tourism / technology.

Usage: python tools/build_turkey.py path/to/tr-cities-utf8.json
"""
import json, sys, pathlib

# name: (seats, sector, right)
DATA = {
    "Adana": (15, "agriculture", 0.50), "Adıyaman": (5, "agriculture", 0.62), "Afyon": (6, "industry", 0.70),
    "Ağrı": (4, "agriculture", 0.35), "Amasya": (3, "agriculture", 0.64), "Ankara": (36, "technology", 0.55),
    "Antalya": (17, "tourism", 0.50), "Artvin": (2, "tourism", 0.60), "Aydın": (8, "tourism", 0.42),
    "Balıkesir": (9, "agriculture", 0.55), "Bilecik": (2, "industry", 0.58), "Bingöl": (3, "agriculture", 0.65),
    "Bitlis": (3, "agriculture", 0.55), "Bolu": (3, "tourism", 0.62), "Burdur": (3, "agriculture", 0.62),
    "Bursa": (20, "industry", 0.60), "Çanakkale": (4, "tourism", 0.45), "Çankırı": (2, "agriculture", 0.78),
    "Çorum": (4, "agriculture", 0.70), "Denizli": (7, "industry", 0.55), "Diyarbakır": (12, "trade", 0.32),
    "Edirne": (4, "trade", 0.40), "Elazığ": (5, "technology", 0.72), "Erzincan": (2, "agriculture", 0.68),
    "Erzurum": (6, "technology", 0.76), "Eskişehir": (6, "technology", 0.45), "Gaziantep": (14, "industry", 0.62),
    "Giresun": (4, "agriculture", 0.66), "Gümüşhane": (2, "agriculture", 0.78), "Hakkari": (3, "trade", 0.22),
    "Hatay": (11, "trade", 0.48), "Isparta": (4, "technology", 0.66), "Mersin": (13, "trade", 0.45),
    "İstanbul": (98, "trade", 0.52), "İzmir": (28, "trade", 0.35), "Kars": (3, "tourism", 0.50),
    "Kastamonu": (3, "tourism", 0.72), "Kayseri": (10, "industry", 0.72), "Kırklareli": (3, "industry", 0.40),
    "Kırşehir": (2, "agriculture", 0.55), "Kocaeli": (14, "industry", 0.58), "Konya": (15, "agriculture", 0.76),
    "Kütahya": (5, "industry", 0.74), "Malatya": (6, "agriculture", 0.70), "Manisa": (10, "industry", 0.55),
    "Kahramanmaraş": (8, "industry", 0.78), "Mardin": (6, "tourism", 0.40), "Muğla": (7, "tourism", 0.38),
    "Muş": (3, "agriculture", 0.35), "Nevşehir": (3, "tourism", 0.70), "Niğde": (3, "agriculture", 0.65),
    "Ordu": (6, "agriculture", 0.66), "Rize": (3, "agriculture", 0.80), "Sakarya": (8, "technology", 0.68),
    "Samsun": (9, "trade", 0.66), "Siirt": (3, "agriculture", 0.50), "Sinop": (2, "tourism", 0.60),
    "Sivas": (5, "agriculture", 0.74), "Tekirdağ": (8, "industry", 0.45), "Tokat": (5, "agriculture", 0.68),
    "Trabzon": (6, "trade", 0.72), "Tunceli": (1, "tourism", 0.15), "Şanlıurfa": (14, "agriculture", 0.60),
    "Uşak": (3, "industry", 0.58), "Van": (8, "trade", 0.35), "Yozgat": (4, "agriculture", 0.78),
    "Zonguldak": (5, "industry", 0.55), "Aksaray": (4, "agriculture", 0.74), "Bayburt": (1, "agriculture", 0.82),
    "Karaman": (3, "agriculture", 0.70), "Kırıkkale": (3, "industry", 0.72), "Batman": (5, "industry", 0.35),
    "Şırnak": (4, "trade", 0.28), "Bartın": (2, "tourism", 0.58), "Ardahan": (2, "agriculture", 0.42),
    "Iğdır": (2, "trade", 0.42), "Yalova": (3, "technology", 0.52), "Karabük": (3, "industry", 0.68),
    "Kilis": (2, "trade", 0.66), "Osmaniye": (4, "industry", 0.72), "Düzce": (3, "industry", 0.70),
}
DISPLAY = {"Afyon": "Afyonkarahisar"}


TR = str.maketrans("çğıöşüÇĞİÖŞÜâ", "cgiosuCGIOSUa")


def slug(name):
    return name.translate(TR).lower().replace(" ", "_")


def rnd(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 3), round(coords[1], 3)]
    return [rnd(c) for c in coords]


def main(src):
    gj = json.load(open(src, encoding="utf-8"))
    cities = []
    for f in gj["features"]:
        name = f["properties"]["name"]
        seats, sector, right = DATA[name]
        geom = f["geometry"]
        cities.append({
            "id": slug(DISPLAY.get(name, name)),
            "name": DISPLAY.get(name, name),
            "seats": seats, "sector": sector, "right": right,
            "geometry": {"type": geom["type"], "coordinates": rnd(geom["coordinates"])},
        })
    total = sum(c["seats"] for c in cities)
    assert len(cities) == 81 and total == 600, (len(cities), total)
    out = {"id": "turkey", "name": "Türkiye", "seat_label": "MPs", "region_label": "Province",
           "currency": "₺", "projection": "equirect", "regions": sorted(cities, key=lambda c: c["name"])}
    dest = pathlib.Path(__file__).resolve().parent.parent / "backend" / "maps" / "turkey.json"
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("wrote", dest, total, "seats")


if __name__ == "__main__":
    main(sys.argv[1])
