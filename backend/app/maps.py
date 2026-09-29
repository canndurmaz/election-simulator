"""Map loading. A map is a JSON file in backend/maps/ with a list of regions
(provinces, states, ...), each carrying seats, sector, ideology lean and geometry.
Adding a country = dropping another JSON file with the same shape."""
import json
from functools import lru_cache
from pathlib import Path

MAPS_DIR = Path(__file__).resolve().parent.parent / "maps"
SECTORS = ["agriculture", "industry", "trade", "tourism", "technology"]


@lru_cache
def load_map(map_id: str) -> dict:
    path = MAPS_DIR / f"{map_id}.json"
    if not path.is_file() or path.parent != MAPS_DIR:
        raise KeyError(map_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data["regions"]:
        assert r["sector"] in SECTORS, r
        assert 0 <= r["right"] <= 1, r
    return data


def list_maps() -> list[dict]:
    out = []
    for p in sorted(MAPS_DIR.glob("*.json")):
        m = load_map(p.stem)
        out.append({"id": m["id"], "name": m["name"], "regions": len(m["regions"]),
                    "seats": sum(r["seats"] for r in m["regions"])})
    return out
