# Election Night

Multiplayer election strategy game. Python (FastAPI + WebSockets) backend, plain-JS frontend (no build step).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend && ../.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000
# open http://localhost:8000 — share the room code / invite link with friends
# admin page: http://localhost:8000/admin (token = $ADMIN_TOKEN, or the random one printed at startup)
cd backend && ../.venv/bin/python -m pytest -q   # tests
```

## Rules (defaults in `backend/app/engine.py` → `Rules`)

- **Setup**: each player picks a party name, color, ideology (e.g. 50% left / 50% right) and a **leader strength**:
  🎤 Orator (rallies +25%), 📺 Media darling (ads +30%), 💼 Tycoon (+20% economy/sector income),
  💰 Fundraiser (base income +60%), 🧲 Populist (ideology fit never below ×0.8, half mismatch penalty),
  🌱 Grassroots (support fades 5% instead of 10%). Host can add **Easy / Normal / Hard** bots. 2–8 players.
- **Event cards**: every round opens with a card everyone sees while planning — sector boom/slump, scandal
  (−25% support for one party), TV debate (ads +50%), disaster (a region's economy pays nothing, rallies there +50%),
  voter apathy (independents +50%), left/right mood swings, donor season, sympathy for the underdog.
- **Diplomacy**: propose/accept coalitions at any time; partners can't attack each other; anyone can leave.
  **A coalition holding a majority at the end wins together** (otherwise the largest party wins).
  You can also send money to any party (a plan action, paid when the round resolves) to seal deals.
- **Maps**: Türkiye (81 provinces, 600 MPs), United States (50 states + DC, 538 electoral votes) and
  Deutschland (16 states, 630 Bundestag seats from the 2025 election; leans from the 2025 party-list vote).
- **20 rounds** (+ overtime, see End). Everyone plans at the same time; the round ends when all connected players submit or the timer expires.
  Then a results screen shows the standings, and **every player must click "Go to next turn"** (offline players don't block it).
- A 6-step tutorial opens when a game starts (and via ❓ How to play).
- **2D map / 🎲 3D board** toggle: the 3D view (three.js, loaded only when used) shows regions as tiles on a
  board of procedurally-textured wood (no image assets) with a painted wooden pawn for each region's leader (bigger pawn = more seats). Drag to rotate, scroll to zoom, right-drag to pan.
  three.js r169 is vendored in `frontend/vendor/three/` (MIT), so the game runs fully offline.
- **Desktop & mobile**: on desktop the map sits next to a tabbed side panel; on phones (≤ 820px) every screen —
  Map, Province/State, Ads, Economy, Plan, Diplomacy, Standings, News, Chat — is a tab in a bottom tab bar, dialogs
  become bottom sheets and touch targets are larger. The 2D map pans and zooms (mouse wheel/drag, pinch/drag, +/− buttons).
- **Admin page** (`/admin`): lists every room (including private and in-progress ones) with players and idle time;
  delete rooms one by one or all idle ones. Connected players are sent back to the home screen.
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
- **Economic control** (sectors and province/state economies): the first party whose stake is ≥ 3× everyone
  else's combined (and at least one round's worth of that economy's income) **owns it for the rest of the game**:
  80% of its income every round, whatever others invest later; the other 20% is split among the other investors by
  stake. Before anyone owns it, income is proportional to stake. Payout ramps to the full pool as total investment
  grows. (`Rules.permanent_region_control = False` makes province economies contestable again.)
- **Ideology mismatch**: leading a province whose lean differs from yours by more than 10 points costs 1.5% of its
  leadership bonus per point beyond that (max 90%).
- Support decays 10% per round.
- **End**: after round 20 *and* once every province has a winner (a party beat the Independents). Until then the game
  goes into overtime, capped at 2× the round count. Winner = most seats (tie-break: cumulative seats, then funds).

## Deploying

See [DEPLOY.md](DEPLOY.md): Docker + Caddy (automatic HTTPS) in three commands, plus non-Docker options.

## Adding a map

Maps are data files in `backend/maps/<id>.json` and show up automatically in the room creation dropdown.
`tools/build_turkey.py`, `tools/build_germany.py` (lon/lat, `"projection": "equirect"`) and `tools/build_usa.py` (pre-projected Albers with
Alaska/Hawaii insets, `"projection": "planar"`) build them from a GeoJSON plus a data table of seats, sector and lean.
Tiny regions can have a `"callout": [x, y]` to get a clickable box off the coast, and any region a
`"label": [x, y]` to move its label (e.g. Brandenburg, whose centre is Berlin).

Note: ideology leans and sectors are approximate gameplay values, not official data.
