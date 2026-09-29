# Election Night

Multiplayer election strategy game. Python (FastAPI + WebSockets) backend, plain-JS frontend (no build step).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend && ../.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000 — share the room code / invite link with friends
cd backend && ../.venv/bin/python -m pytest -q   # tests
```

## Rules (defaults in `backend/app/engine.py` → `Rules`)

- **Setup**: each player picks a party name, color and ideology (e.g. 50% left / 50% right). Host can add bots. 2–8 players.
- **20 rounds** (+ overtime, see End). Everyone plans at the same time; the round ends when all connected players submit or the timer expires.
- **Each round is an election** in all 81 provinces (600 seats, 2023 allocation). Plurality winner *leads* the province;
  seats are split with D'Hondt. A baseline "Independent" vote (₺4K per seat) must be beaten.
- **Actions**
  - 📣 Rally (one province) — support × ideology fit (1.2 when matching, down to 0.4 when opposite)
  - 📺 National TV ad — spread across all provinces by seats
  - 📱 Social media ad — targets all provinces of one sector
  - 🗯️ Attack ad — reduces a rival's support in a province
  - 🏗️ Invest in a province economy / 🏭 invest in a sector (permanent stakes)
- **Income per round** = base + leadership bonus (₺4K/seat in provinces you lead) − ideology-mismatch penalty
  + province economy shares + sector shares.
- **Dominance rule** (provinces and sectors): if your stake is ≥ 3× everyone else's combined, you take 80% of that
  income; the other 20% is split among the others by stake. Otherwise income is proportional.
  Money is in the thousands/millions (start ₺1M, base ₺100K/round); amount inputs are entered in K. Payout ramps to the full pool as total investment grows (prevents a 10-coin claim on a whole economy).
- **Ideology mismatch**: leading a province whose lean differs from yours by more than 10 points costs 1.5% of its
  leadership bonus per point beyond that (max 90%).
- Support decays 10% per round.
- **End**: after round 20 *and* once every province has a winner (a party beat the Independents). Until then the game
  goes into overtime, capped at 2× the round count. Winner = most seats (tie-break: cumulative seats, then funds).

## Adding a map (e.g. USA)

Maps are data files in `backend/maps/<id>.json`:

```json
{"id": "usa", "name": "United States", "seat_label": "Electoral votes", "region_label": "State", "currency": "$",
 "regions": [{"id": "ca", "name": "California", "seats": 54, "sector": "technology", "right": 0.38,
              "geometry": {"type": "MultiPolygon", "coordinates": [...]}}]}
```

It shows up automatically in the room creation dropdown. `tools/build_turkey.py` shows how the Turkey file is built
from a GeoJSON + data table; a USA version would be the same with a states GeoJSON. (For the USA, Alaska/Hawaii
will want an Albers-style projection in `frontend/js/map.js`; the renderer currently uses equirectangular.)

Note: province ideology leans and sectors are approximate gameplay values, not official data.
