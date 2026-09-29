import { MapView } from './map.js';

const $ = (sel, root = document) => root.querySelector(sel);
const app = $('#app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SECTORS = ['agriculture', 'industry', 'trade', 'tourism', 'technology'];
const SECTOR_COLOR = { agriculture: '#7cb342', industry: '#e07b39', trade: '#29b6f6', tourism: '#ffca28', technology: '#ab47bc' };
const SECTOR_ICON = { agriculture: '🌾', industry: '🏭', trade: '🚢', tourism: '🏖️', technology: '💻' };
const COLORS = ['#e63946', '#1d7fd6', '#f4a300', '#2a9d8f', '#8e44ad', '#e76f51', '#43aa8b', '#d63384'];
const IND = '_ind';
const IND_COLOR = '#4a5368';

const S = {
  name: localStorage.getItem('name') || '',
  session: null,   // {code, token, player_id}
  ws: null, room: null, game: null, you: null,
  map: null, mapView: null, mapMode: 'leader', selected: null,
  tab: 'region', plan: [], planRound: null, lastRound: null,
};

// ---------- utils ----------
function toast(msg, info = false) {
  const d = document.createElement('div');
  d.textContent = msg; if (info) d.className = 'info';
  $('#toast').appendChild(d); setTimeout(() => d.remove(), 3500);
}
async function api(path, body) {
  const res = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
function money(n) {
  const c = S.map?.currency || '$', sign = n < 0 ? '−' : '', a = Math.abs(n);
  const fmt = (v, suf) => `${sign}${c}${(v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)).replace(/\.?0+$/, '')}${suf}`;
  if (a >= 1e6) return fmt(a / 1e6, 'M');
  if (a >= 1e3) return fmt(a / 1e3, 'K');
  return `${sign}${c}${Math.round(a)}`;
}
const pct = x => `${Math.round(x * 100)}%`;
const player = id => S.room?.members.find(m => m.id === id);
const partyName = id => id === IND ? 'Independents' : (player(id)?.party ?? '?');
const partyColor = id => id === IND ? IND_COLOR : (player(id)?.color ?? '#666');
const me = () => player(S.you);
const myRight = () => (100 - (me()?.left ?? 50)) / 100;
const region = id => S.map.regions.find(r => r.id === id);
function mix(a, b, t) {
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return '#' + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
const ideoMult = (pr, rr) => 0.4 + 0.8 * (1 - Math.abs(pr - rr));
function mismatch(pr, rr) {
  const R = S.game.rules;
  return Math.min(R.mismatch_penalty_cap, Math.max(0, Math.abs(pr - rr) - R.mismatch_grace) * R.mismatch_penalty_slope);
}
function ideoBar(left) {
  return `<div class="between muted" style="font-size:12px"><span>Left ${left}%</span><span>Right ${100 - left}%</span></div>
    <div class="ideo-bar"><div class="l" style="width:${left}%"></div><div class="r" style="width:${100 - left}%"></div></div>`;
}
function bars(entries, total, fmt = v => v.toFixed(0)) {
  if (!entries.length) return '<div class="muted">None yet</div>';
  return `<div class="bars">${entries.map(([id, v]) => `
    <div class="bar"><span><span class="dot" style="background:${partyColor(id)};width:9px;height:9px"></span> ${esc(partyName(id))}</span>
    <div class="track"><div class="fill" style="width:${total ? 100 * v / total : 0}%;background:${partyColor(id)}"></div></div>
    <span style="text-align:right">${fmt(v)}</span></div>`).join('')}</div>`;
}
function dominance(stakes) {
  const e = Object.entries(stakes); if (!e.length) return null;
  e.sort((a, b) => b[1] - a[1]);
  const others = e.slice(1).reduce((s, [, v]) => s + v, 0);
  if (!others) return { id: e[0][0], solo: true };
  if (e[0][1] >= S.game.rules.dominance_ratio * others) return { id: e[0][0] };
  return { need: S.game.rules.dominance_ratio * others - e[0][1], id: e[0][0], contested: true };
}

// ---------- session / routing ----------
function saveSession(s) { S.session = s; localStorage.setItem('session:' + s.code, JSON.stringify(s)); history.replaceState(null, '', '?room=' + s.code); }
function forgetSession() {
  if (S.session) localStorage.removeItem('session:' + S.session.code);
  S.session = null; S.room = S.game = null; S.mapView = null;
  if (S.ws) { S.ws.onclose = null; S.ws.close(); S.ws = null; }
  history.replaceState(null, '', '/');
  renderHome();
}

function connect() {
  const { code, token } = S.session;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/${code}?token=${encodeURIComponent(token)}`);
  S.ws = ws;
  ws.onmessage = async ev => {
    const msg = JSON.parse(ev.data);
    if (msg.t === 'error') { toast(msg.msg); if (msg.fatal) forgetSession(); return; }
    if (msg.t === 'kicked') { toast('You were removed from the room'); forgetSession(); return; }
    if (msg.t === 'state') {
      S.room = msg.room; S.game = msg.game; S.you = msg.you;
      if (!S.map || S.map.id !== S.room.map_id) S.map = await api('/api/maps/' + S.room.map_id);
      render();
    }
  };
  ws.onclose = () => { if (S.ws === ws) { S.ws = null; setTimeout(() => S.session && connect(), 1500); } };
}
const send = msg => S.ws?.readyState === 1 ? S.ws.send(JSON.stringify(msg)) : toast('Not connected');

function render() {
  if (!S.room) return renderHome();
  if (!S.game) return renderLobby();
  renderGame();
}

// ---------- home ----------
async function renderHome() {
  $('#topbar-extra').innerHTML = '';
  S.mapView = null;
  const maps = await api('/api/maps').catch(() => []);
  const params = new URLSearchParams(location.search);
  app.innerHTML = `
  <div class="home">
    <div class="card">
      <h2>Start campaigning</h2>
      <label>Your name</label><input id="pname" maxlength="24" value="${esc(S.name)}" placeholder="e.g. Ayşe">
      <h3>Join by code</h3>
      <div class="row"><input id="jcode" maxlength="5" placeholder="ABCDE" value="${esc(params.get('room') || '')}" style="text-transform:uppercase"><button class="auto primary" id="jbtn">Join</button></div>
      <h3>Create a room</h3>
      <label>Room name</label><input id="rname" placeholder="Election Night">
      <label>Map</label><select id="rmap">${maps.map(m => `<option value="${m.id}">${esc(m.name)} — ${m.regions} regions, ${m.seats} seats</option>`).join('')}</select>
      <div class="row">
        <div><label>Max players</label><input id="rmax" type="number" min="2" max="8" value="6"></div>
        <div><label>Turn timer (s)</label><input id="rturn" type="number" min="20" max="600" value="120"></div>
        <div><label>Rounds</label><input id="rrounds" type="number" min="1" max="50" value="20"></div>
      </div>
      <label><input type="checkbox" id="rpriv"> Private (hidden from room list)</label>
      <button class="primary" id="cbtn" style="width:100%;margin-top:10px">Create room</button>
    </div>
    <div class="card room-list">
      <div class="between"><h2>Open rooms</h2><button class="small" id="refresh">Refresh</button></div>
      <div id="rooms"></div>
    </div>
  </div>`;
  const getName = () => {
    const n = $('#pname').value.trim();
    if (!n) { toast('Enter your name first'); $('#pname').focus(); return null; }
    S.name = n; localStorage.setItem('name', n); return n;
  };
  const join = async code => {
    const name = getName(); if (!name) return;
    code = code.trim().toUpperCase();
    const prev = JSON.parse(localStorage.getItem('session:' + code) || 'null');
    try {
      saveSession(prev || { code, ...(await api(`/api/rooms/${code}/join`, { player_name: name })) });
      connect();
    } catch (e) { toast(e.message); }
  };
  $('#jbtn').onclick = () => join($('#jcode').value);
  $('#cbtn').onclick = async () => {
    const name = getName(); if (!name) return;
    try {
      const r = await api('/api/rooms', {
        player_name: name, room_name: $('#rname').value, map_id: $('#rmap').value, private: $('#rpriv').checked,
        max_players: +$('#rmax').value, turn_seconds: +$('#rturn').value, rounds: +$('#rrounds').value,
      });
      saveSession(r); connect();
    } catch (e) { toast(e.message); }
  };
  const loadRooms = async () => {
    const rooms = await api('/api/rooms').catch(() => []);
    $('#rooms').innerHTML = rooms.length ? rooms.map(r => `
      <div class="item"><div><b>${esc(r.name)}</b> <span class="badge">${esc(r.map_id)}</span><div class="muted">Host ${esc(r.host)} · ${r.players}/${r.max_players} players · ${r.code}</div></div>
      <button data-code="${r.code}" ${r.players >= r.max_players ? 'disabled' : ''}>Join</button></div>`).join('')
      : '<div class="muted">No open rooms. Create one!</div>';
    $('#rooms').querySelectorAll('button').forEach(b => b.onclick = () => join(b.dataset.code));
  };
  $('#refresh').onclick = loadRooms;
  loadRooms();
}

// ---------- lobby ----------
function renderLobby() {
  S.mapView = null;
  const r = S.room, mine = me(), host = r.host_id === S.you;
  if (!mine) return;
  const focused = document.activeElement?.id;
  $('#topbar-extra').innerHTML = `<button class="small danger" id="leave">Leave room</button>`;
  $('#leave').onclick = () => { send({ t: 'leave' }); forgetSession(); };
  const taken = new Set(r.members.filter(m => m.id !== S.you).map(m => m.color));
  const partyDraft = $('#party')?.value;
  app.innerHTML = `
  <div class="lobby">
    <div class="card">
      <div class="between"><div><div class="muted">Room code — share it or the link</div><div class="code">${r.code}</div></div>
        <div style="text-align:right"><b>${esc(r.name)}</b><div class="muted">${esc(S.map?.name || r.map_id)} · ${r.rounds} rounds · ${r.turn_seconds}s turns</div>
        <button class="small" id="copy">Copy invite link</button></div></div>
      <h3>Parties (${r.members.length}/${r.max_players})</h3>
      ${r.members.map(m => `
        <div class="member"><span class="dot" style="background:${m.color}"></span>
          <div><b>${esc(m.party)}</b> <span class="muted">— ${esc(m.name)}</span> ${m.id === r.host_id ? '<span class="badge">host</span>' : ''} ${m.is_bot ? '<span class="badge">bot</span>' : ''} ${!m.online ? '<span class="badge">offline</span>' : ''}
            ${ideoBar(m.left)}</div>
          <div class="row auto">${m.ready ? '<span class="badge ok">ready</span>' : '<span class="badge">not ready</span>'}
            ${host && m.id !== S.you ? `<button class="small danger" data-kick="${m.id}">✕</button>` : ''}</div>
        </div>`).join('')}
      ${host ? `<div class="row" style="margin-top:12px">
        <button id="addbot" ${r.members.length >= r.max_players ? 'disabled' : ''}>+ Add bot</button>
        <button class="primary" id="start" ${r.members.length < 2 || !r.members.every(m => m.ready) ? 'disabled' : ''}>Start election</button></div>`
      : '<p class="muted">Waiting for the host to start…</p>'}
    </div>
    <div>
      <div class="card">
        <h2>Your party</h2>
        <label>Party name</label>
        <div class="row"><input id="party" maxlength="32" value="${esc(partyDraft ?? mine.party)}"><button class="auto" id="saveparty">Save</button></div>
        <label>Color</label>
        <div class="swatches">${COLORS.map(c => `<button class="swatch ${c === mine.color ? 'sel' : ''} ${taken.has(c) ? 'taken' : ''}" data-color="${c}" style="background:${c}" ${taken.has(c) ? 'disabled' : ''}></button>`).join('')}</div>
        <label>Ideology — <span id="ideolabel">${mine.left}% left / ${100 - mine.left}% right</span></label>
        <input type="range" id="left" min="0" max="100" step="5" value="${mine.left}" style="direction:rtl">
        <div class="between muted" style="font-size:12px"><span>◀ Right</span><span>Left ▶</span></div>
        <p class="muted" style="font-size:12px">Campaigns work best in provinces that match your ideology. Winning a province that doesn't match costs you part of its leadership income.</p>
        <button class="${mine.ready ? '' : 'primary'}" id="ready" style="width:100%">${mine.ready ? 'Not ready' : "I'm ready"}</button>
      </div>
      <div class="card chat" style="margin-top:14px">${chatHtml()}</div>
    </div>
  </div>`;
  $('#copy').onclick = () => navigator.clipboard?.writeText(location.origin + '/?room=' + r.code).then(() => toast('Link copied', true));
  $('#saveparty').onclick = () => send({ t: 'profile', party: $('#party').value });
  $('#party').onkeydown = e => e.key === 'Enter' && send({ t: 'profile', party: $('#party').value });
  document.querySelectorAll('[data-color]').forEach(b => b.onclick = () => send({ t: 'profile', color: b.dataset.color }));
  $('#left').oninput = e => { const l = +e.target.value; $('#ideolabel').textContent = `${l}% left / ${100 - l}% right`; };
  $('#left').onchange = e => send({ t: 'profile', left: +e.target.value });
  $('#ready').onclick = () => send({ t: 'ready', ready: !mine.ready });
  document.querySelectorAll('[data-kick]').forEach(b => b.onclick = () => send({ t: 'kick', id: b.dataset.kick }));
  if (host) {
    $('#addbot').onclick = () => send({ t: 'add_bot' });
    $('#start').onclick = () => send({ t: 'start' });
  }
  bindChat();
  if (focused) $('#' + focused)?.focus();
}

function chatHtml() {
  return `<div class="msgs" id="msgs">${S.room.chat.map(c => `<div><b style="color:${c.color}">${esc(c.name)}:</b> ${esc(c.text)}</div>`).join('')}</div>
    <div class="row"><input id="chatin" placeholder="Say something…" maxlength="300"><button class="auto" id="chatbtn">Send</button></div>`;
}
function bindChat() {
  const m = $('#msgs'); if (m) m.scrollTop = m.scrollHeight;
  const go = () => { const v = $('#chatin').value.trim(); if (v) { send({ t: 'chat', text: v }); $('#chatin').value = ''; } };
  $('#chatbtn').onclick = go;
  $('#chatin').onkeydown = e => e.key === 'Enter' && go();
}

// ---------- game ----------
const planTotal = () => S.plan.reduce((s, a) => s + a.amount, 0);
const budget = () => (S.game.standings.find(s => s.id === S.you)?.money ?? 0) - planTotal();

function addAction(a) {
  if (S.game.my_pending) return toast('Already submitted — click "Edit plan" to change it');
  if (!Number.isFinite(a.amount) || a.amount < S.game.rules.min_action) return toast(`Minimum is ${money(S.game.rules.min_action)}`);
  if (a.amount > budget()) return toast('Not enough funds');
  S.plan.push(a);
  renderGame();
}
function describe(a) {
  const rn = id => esc(region(id)?.name);
  switch (a.type) {
    case 'rally': return `📣 Rally in ${rn(a.region)}`;
    case 'invest_region': return `🏗️ Invest in ${rn(a.region)}`;
    case 'invest_sector': return `${SECTOR_ICON[a.sector]} Invest in ${a.sector}`;
    case 'ad': return a.kind === 'tv' ? '📺 National TV ad' : a.kind === 'social' ? `📱 Social ads → ${a.sector}` : `🗯️ Attack ad vs ${esc(partyName(a.target))} in ${rn(a.region)}`;
  }
}

function colorOf(id) {
  const g = S.game.regions[id], r = region(id);
  if (S.mapMode === 'sector') return SECTOR_COLOR[r.sector];
  if (S.mapMode === 'ideology') return mix('#e05263', '#3d8bd4', r.right);
  if (S.mapMode === 'invest') {
    const d = dominance(g.invest); return d ? (d.contested ? '#666' : partyColor(d.id)) : '#2a3244';
  }
  if (S.mapMode === 'match') return mix('#5a1f28', '#2d8f5c', 1 - Math.abs(myRight() - r.right));
  if (g.leader) {
    const total = Object.values(g.support).reduce((a, b) => a + b, 0) + g.independent;
    const share = g.support[g.leader] / total;
    return mix('#1a2030', partyColor(g.leader), Math.min(1, 0.35 + share));
  }
  return '#2a3244';
}

function tooltip(e, id) {
  const tt = $('#tooltip');
  if (!e) return tt.classList.add('hidden');
  const r = region(id), g = S.game.regions[id];
  tt.innerHTML = `<b>${esc(r.name)}</b> · ${r.seats} ${esc(S.map.seat_label)}<br>
    <span class="tag" style="background:${SECTOR_COLOR[r.sector]}">${r.sector}</span> ${pct(1 - r.right)} left / ${pct(r.right)} right<br>
    Leader: ${g.leader ? `<b style="color:${partyColor(g.leader)}">${esc(partyName(g.leader))}</b>` : '<span class="muted">none</span>'}<br>
    ${Object.entries(g.seats).map(([p, n]) => `<span style="color:${partyColor(p)}">${esc(partyName(p))} ${n}</span>`).join(' · ')}`;
  tt.classList.remove('hidden');
  tt.style.left = Math.min(e.clientX + 14, innerWidth - 270) + 'px';
  tt.style.top = e.clientY + 14 + 'px';
}

let timerInterval;
function renderTopbar() {
  const g = S.game, st = g.standings.find(s => s.id === S.you);
  $('#topbar-extra').innerHTML = `
    <div class="stat"><b class="${g.overtime && !g.finished ? 'bad' : ''}">${g.finished ? 'Final' : `Round ${g.round}/${g.rounds}`}</b><span>${g.finished ? 'results' : g.overtime ? 'overtime' : 'round'}</span></div>
    <div class="stat"><b>${g.unclaimed.length}</b><span>provinces without a winner</span></div>
    <div class="stat"><b id="timer">–</b><span>time left</span></div>
    <div class="stat"><b>${st?.seats ?? 0}</b><span>your seats</span></div>
    <div class="stat"><b>${money(st?.money ?? 0)}</b><span>funds</span></div>
    <div class="stat"><b class="${budget() < 0 ? 'bad' : 'good'}">${money(budget())}</b><span>left to plan</span></div>
    <button class="small danger" id="leave">Leave</button>`;
  $('#leave').onclick = () => { if (confirm('Leave the game? Your party stays and you can rejoin with the same browser.')) { send({ t: 'leave' }); forgetSession(); } };
  clearInterval(timerInterval);
  const tick = () => { const t = $('#timer'); if (!t) return; const s = S.game.deadline ? Math.max(0, Math.round(S.game.deadline - Date.now() / 1000)) : null; t.textContent = s == null ? '–' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; t.className = s != null && s < 15 ? 'bad' : ''; };
  tick(); timerInterval = setInterval(tick, 1000);
}

function renderGame() {
  const g = S.game;
  if (S.planRound !== g.round) { S.plan = g.my_pending ? [...g.my_pending] : []; S.planRound = g.round; }
  if (S.lastRound !== null && g.last_report && g.last_report.round !== S.lastRound) {
    toast(`Round ${g.last_report.round} results are in — you earned ${money(g.last_report.income[S.you]?.total ?? 0)}`, true);
  }
  S.lastRound = g.last_report?.round ?? 0;
  renderTopbar();

  if (!S.mapView || !document.contains(S.mapView.svg)) {
    app.innerHTML = `
    <div class="game">
      <div class="map-wrap">
        <div class="map-tools" id="maptools"></div>
        <div id="mapbox" style="flex:1;display:flex;min-height:0"></div>
        <div class="seatbar" id="seatbar"></div>
        <div class="legend" id="legend"></div>
      </div>
      <div class="side">
        <div class="tabs" id="tabs"></div>
        <div class="panel" id="panel"></div>
        <div class="submit-bar" id="submitbar"></div>
      </div>
    </div>`;
    S.mapView = new MapView($('#mapbox'), S.map, { onSelect: id => { S.selected = id; S.tab = 'region'; S.panelTab = null; renderGame(); $('#panel').scrollTop = 0; }, tooltip });
  }
  const modes = { leader: 'Leaders', sector: 'Sectors', ideology: 'Ideology', match: 'My fit', invest: 'Investors' };
  $('#maptools').innerHTML = Object.entries(modes).map(([k, v]) => `<button class="small ${S.mapMode === k ? 'on' : ''}" data-mode="${k}">${v}</button>`).join('')
    + `<span class="muted" style="margin-left:auto;font-size:12px">Click a province to campaign · numbers = ${esc(S.map.seat_label)}</span>`;
  $('#maptools').querySelectorAll('[data-mode]').forEach(b => b.onclick = () => { S.mapMode = b.dataset.mode; renderGame(); });
  S.mapView.paint(colorOf, S.selected);

  const totals = g.last_report?.seats || {};
  const allSeats = S.map.regions.reduce((s, r) => s + r.seats, 0);
  $('#seatbar').innerHTML = [...g.standings.map(s => [s.id, s.seats]), [IND, totals[IND] ?? allSeats]]
    .filter(([, n]) => n > 0).map(([id, n]) => `<div title="${esc(partyName(id))}: ${n}" style="width:${100 * n / allSeats}%;background:${partyColor(id)}"></div>`).join('');
  $('#legend').innerHTML = S.mapMode === 'sector'
    ? SECTORS.map(s => `<span><span class="dot" style="background:${SECTOR_COLOR[s]}"></span>${SECTOR_ICON[s]} ${s}</span>`).join('')
    : S.mapMode === 'ideology' ? '<span><span class="dot" style="background:#e05263"></span>left</span><span><span class="dot" style="background:#3d8bd4"></span>right</span>'
    : S.mapMode === 'match' ? '<span><span class="dot" style="background:#2d8f5c"></span>matches your ideology</span><span><span class="dot" style="background:#5a1f28"></span>opposite</span>'
    : [...g.standings.map(s => `<span><span class="dot" style="background:${partyColor(s.id)}"></span>${esc(partyName(s.id))} ${s.seats}</span>`), `<span><span class="dot" style="background:${IND_COLOR}"></span>Independent ${totals[IND] ?? allSeats}</span>`].join('')
      + (S.mapMode === 'invest' ? '<span class="muted">(colored = investor holds ≥3× the rest, grey = contested)</span>' : '');

  const tabs = { region: 'Province', campaign: 'Campaign', economy: 'Economy', plan: `Plan (${S.plan.length})`, standings: 'Standings', report: 'Report', chat: 'Chat' };
  $('#tabs').innerHTML = Object.entries(tabs).map(([k, v]) => `<button class="${S.tab === k ? 'on' : ''}" data-tab="${k}">${v}</button>`).join('');
  $('#tabs').querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { S.tab = b.dataset.tab; renderGame(); });

  // don't clobber a half-typed amount when someone else's action triggers a broadcast
  const active = document.activeElement;
  if (!(active && $('#panel')?.contains(active) && active.tagName === 'INPUT' && S.panelTab === S.tab)) {
    S.panelTab = S.tab;
    $('#panel').innerHTML = ({ region: regionPanel, campaign: campaignPanel, economy: economyPanel, plan: planPanel, standings: standingsPanel, report: reportPanel, chat: () => `<div class="chat" style="height:100%">${chatHtml()}</div>` })[S.tab]();
    bindPanel();
  }
  renderSubmitBar();
  if (g.finished) renderFinal();
}

function amountInput(key, def = 100) {
  // entered in thousands: 100 = 100K, 1500 = 1.5M
  return `<div class="row"><input type="number" min="${S.game.rules.min_action / 1000}" step="10" value="${def}" data-amount="${key}"><span class="auto muted">K</span><button class="auto primary" data-add="${key}">Add to plan</button></div>`;
}

function regionPanel() {
  if (!S.selected) return '<p class="muted">Select a province on the map to see polls, seats and investment, and to plan rallies there.</p>';
  const r = region(S.selected), g = S.game.regions[S.selected];
  const votes = Object.entries(g.support).concat([[IND, g.independent]]).sort((a, b) => b[1] - a[1]);
  const vtotal = votes.reduce((s, [, v]) => s + v, 0);
  const inv = Object.entries(g.invest).sort((a, b) => b[1] - a[1]);
  const itotal = inv.reduce((s, [, v]) => s + v, 0);
  const d = dominance(g.invest);
  const mult = ideoMult(myRight(), r.right), pen = mismatch(myRight(), r.right);
  const bonus = r.seats * S.game.rules.leader_bonus_per_seat;
  const opponents = S.game.standings.filter(s => s.id !== S.you);
  return `
    <div class="between"><h2>${esc(r.name)}</h2><span class="tag" style="background:${SECTOR_COLOR[r.sector]}">${SECTOR_ICON[r.sector]} ${r.sector}</span></div>
    ${ideoBar(Math.round((1 - r.right) * 100))}
    <dl class="kv" style="margin-top:10px">
      <dt>${esc(S.map.seat_label)}</dt><dd><b>${r.seats}</b></dd>
      <dt>Leader</dt><dd>${g.leader ? `<b style="color:${partyColor(g.leader)}">${esc(partyName(g.leader))}</b>` : 'none'}</dd>
      <dt>Your campaign effectiveness</dt><dd class="${mult >= 1 ? 'good' : mult < 0.8 ? 'bad' : ''}">×${mult.toFixed(2)}</dd>
      <dt>Leadership bonus if you lead</dt><dd>${money(bonus * (1 - pen))}/round ${pen > 0 ? `<span class="bad">(−${pct(pen)} ideology mismatch)</span>` : ''}</dd>
      <dt>Local economy</dt><dd>${money(g.pool)}/round, full payout at ${money(g.pool * S.game.saturation.region)} invested</dd>
    </dl>
    <h3>Polls (current support)</h3>${bars(votes, vtotal, v => pct(v / vtotal))}
    <h3>Seats (last election)</h3>${Object.keys(g.seats).length ? bars(Object.entries(g.seats), r.seats, v => v) : '<div class="muted">No election yet</div>'}
    <h3>Investors</h3>${bars(inv, itotal, v => money(v))}
    ${d ? `<div class="muted" style="font-size:12px;margin-top:6px">${d.contested ? `${esc(partyName(d.id))} needs ${money(d.need)} more to control 80% of this economy` : d.solo ? `${esc(partyName(d.id))} is the only investor` : `<b style="color:${partyColor(d.id)}">${esc(partyName(d.id))}</b> controls 80% of this economy`}</div>` : ''}
    <h3>Actions</h3>
    <div class="action"><div class="title">📣 Rally & ground campaign</div><div class="desc">Adds support here ×${mult.toFixed(2)} (ideology fit). Support fades ${pct(S.game.rules.support_decay)} per round.</div>${amountInput('rally')}</div>
    <div class="action"><div class="title">🏗️ Invest in local economy</div><div class="desc">Permanent stake. Hold ≥${S.game.rules.dominance_ratio}× everyone else combined to take ${pct(S.game.rules.dominance_share)} of the income.</div>${amountInput('invest_region')}</div>
    ${opponents.length ? `<div class="action"><div class="title">🗯️ Attack ad</div><div class="desc">Cuts a rival's support here by ${S.game.rules.negative_efficiency}× the spend.</div>
      <select id="negtarget" style="margin-bottom:6px">${opponents.map(o => `<option value="${o.id}">${esc(partyName(o.id))}</option>`).join('')}</select>${amountInput('negative')}</div>` : ''}`;
}

function campaignPanel() {
  return `<h2>Campaign ads</h2>
    <div class="action"><div class="title">📺 National TV ad</div><div class="desc">Reaches every province, weighted by seats (×${S.game.rules.tv_efficiency} total efficiency, times ideology fit per province).</div>${amountInput('tv', 200)}</div>
    <div class="action"><div class="title">📱 Social media ads</div><div class="desc">Targets all provinces of one sector (×${S.game.rules.social_efficiency} efficiency).</div>
      <select id="socialsector" style="margin-bottom:6px">${SECTORS.map(s => `<option value="${s}">${SECTOR_ICON[s]} ${s}</option>`).join('')}</select>${amountInput('social', 150)}</div>
    <p class="muted" style="font-size:12px">For rallies and attack ads, click a province on the map.</p>`;
}

function economyPanel() {
  const g = S.game;
  return `<h2>Sector economy</h2>
    <p class="muted" style="font-size:12px">Each sector pays out every round. Hold ≥${g.rules.dominance_ratio}× everyone else's combined investment to take ${pct(g.rules.dominance_share)}; the rest is split among the other investors. Market conditions shift each round.</p>
    ${SECTORS.map(s => {
      const inv = Object.entries(g.sectors[s]).sort((a, b) => b[1] - a[1]);
      const total = inv.reduce((a, [, v]) => a + v, 0);
      const d = dominance(g.sectors[s]);
      const m = g.market[s];
      return `<div class="sector-card">
        <div class="between"><b>${SECTOR_ICON[s]} ${s}</b><span>${money(g.sector_pools[s])}/round <span class="${m >= 1 ? 'good' : 'bad'}">${m >= 1 ? '▲' : '▼'}${Math.round((m - 1) * 100)}%</span></span></div>
        <div class="muted" style="font-size:12px">Full payout at ${money(g.sector_pools[s] * g.saturation.sector)} invested · currently ${money(total)}</div>
        ${bars(inv, total, v => money(v))}
        ${d && !d.solo ? `<div class="muted" style="font-size:12px">${d.contested ? `${esc(partyName(d.id))} needs ${money(d.need)} more for control` : `<b style="color:${partyColor(d.id)}">${esc(partyName(d.id))}</b> controls this sector`}</div>` : ''}
        <div style="margin-top:6px">${amountInput('sector:' + s)}</div></div>`;
    }).join('')}`;
}

function planPanel() {
  return `<h2>This round's plan</h2>
    ${S.plan.length ? S.plan.map((a, i) => `<div class="plan-item"><span>${describe(a)}</span><span>${money(a.amount)} ${S.game.my_pending ? '' : `<button class="small danger" data-rm="${i}">✕</button>`}</span></div>`).join('')
      + `<div class="plan-item"><b>Total</b><b>${money(planTotal())}</b></div>` : '<p class="muted">Nothing planned yet. Rally in provinces, run ads, and invest. Unspent funds carry over.</p>'}`;
}

function standingsPanel() {
  const g = S.game;
  const hist = g.seat_history;
  const maxSeats = S.map.regions.reduce((s, r) => s + r.seats, 0);
  const W = 360, H = 120;
  const lines = g.standings.map(s => {
    const pts = hist.map((h, i) => `${hist.length > 1 ? (i / (hist.length - 1)) * W : W / 2},${H - (h[s.id] || 0) / maxSeats * H}`).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${partyColor(s.id)}" stroke-width="2"/>`;
  }).join('');
  return `<h2>Standings</h2>
    <table class="table"><tr><th>Party</th><th>Seats</th><th>Led</th><th>Funds</th><th>Status</th></tr>
    ${g.standings.map((s, i) => `<tr><td>${i === 0 && hist.length ? '<span class="crown">♛</span> ' : ''}<span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}${s.id === S.you ? ' (you)' : ''}</td>
      <td>${s.seats}</td><td>${s.regions_led}</td><td>${money(s.money)}</td><td>${g.submitted.includes(s.id) ? '<span class="badge ok">ready</span>' : '<span class="badge">planning</span>'}</td></tr>`).join('')}</table>
    <h3>Seats over time</h3>
    ${hist.length ? `<svg viewBox="-4 -4 ${W + 8} ${H + 8}" style="width:100%">${lines}</svg>` : '<p class="muted">After the first election.</p>'}
    <p class="muted" style="font-size:12px">Majority: ${Math.floor(maxSeats / 2) + 1} seats. The game ends after round ${g.rounds} once every province has a winner (overtime until then, max ${g.max_rounds} rounds). Most seats wins.</p>
    ${g.unclaimed.length ? `<h3>No winner yet (${g.unclaimed.length})</h3><div style="font-size:12px">${g.unclaimed.map(id => esc(region(id).name)).join(', ')}</div>` : ''}`;
}

function reportPanel() {
  const rep = S.game.last_report;
  if (!rep) return '<p class="muted">The first results come in after round 1.</p>';
  const inc = rep.income[S.you] || {};
  return `<h2>Round ${rep.round} results</h2>
    <h3>Your income</h3>
    <dl class="kv"><dt>Base</dt><dd>${money(inc.base)}</dd><dt>Leadership bonuses</dt><dd>${money(inc.leadership)}</dd>
      <dt>Ideology mismatch</dt><dd class="bad">${money(inc.penalty)}</dd><dt>Province economies</dt><dd>${money(inc.regions)}</dd>
      <dt>Sectors</dt><dd>${money(inc.sectors)}</dd><dt><b>Total</b></dt><dd><b class="good">${money(inc.total)}</b></dd></dl>
    <h3>All parties</h3>
    <table class="table"><tr><th>Party</th><th>Seats</th><th>Income</th></tr>
    ${Object.entries(rep.income).map(([id, v]) => `<tr><td><span class="dot" style="background:${partyColor(id)};width:9px;height:9px"></span> ${esc(partyName(id))}</td><td>${rep.seats[id] || 0}</td><td>${money(v.total)}</td></tr>`).join('')}</table>
    <h3>News</h3>${rep.events.length ? rep.events.map(e => `<div>• ${esc(e)}</div>`).join('') : '<div class="muted">A quiet round.</div>'}`;
}

function bindPanel() {
  const p = $('#panel');
  p.querySelectorAll('[data-add]').forEach(b => b.onclick = () => {
    const key = b.dataset.add, amount = Math.round(+p.querySelector(`[data-amount="${key}"]`).value * 1000);
    if (key === 'rally' || key === 'invest_region') addAction({ type: key, region: S.selected, amount });
    else if (key === 'negative') addAction({ type: 'ad', kind: 'negative', region: S.selected, target: $('#negtarget').value, amount });
    else if (key === 'tv') addAction({ type: 'ad', kind: 'tv', amount });
    else if (key === 'social') addAction({ type: 'ad', kind: 'social', sector: $('#socialsector').value, amount });
    else if (key.startsWith('sector:')) addAction({ type: 'invest_sector', sector: key.slice(7), amount });
  });
  p.querySelectorAll('[data-amount]').forEach(i => i.onkeydown = e => e.key === 'Enter' && p.querySelector(`[data-add="${i.dataset.amount}"]`).click());
  p.querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { S.plan.splice(+b.dataset.rm, 1); renderGame(); });
  if (S.tab === 'chat') bindChat();
}

function renderSubmitBar() {
  const g = S.game;
  if (g.finished) { $('#submitbar').innerHTML = '<b>Game over.</b>'; return; }
  const waiting = g.standings.filter(s => !g.submitted.includes(s.id)).map(s => partyName(s.id));
  $('#submitbar').innerHTML = g.my_pending
    ? `<div class="between"><span class="good">✓ Plan submitted. Waiting for: ${esc(waiting.join(', ') || '—')}</span><button class="small" id="unsubmit">Edit plan</button></div>`
    : `<div class="between"><span>${S.plan.length} actions · ${money(planTotal())}</span><button class="primary" id="submit">End turn</button></div>`;
  $('#unsubmit')?.addEventListener('click', () => send({ t: 'unsubmit' }));
  $('#submit')?.addEventListener('click', () => send({ t: 'submit', actions: S.plan }));
}

function renderFinal() {
  if ($('.overlay') || S.dismissedFinal) return;
  const g = S.game, w = g.standings[0];
  const o = document.createElement('div');
  o.className = 'overlay';
  o.innerHTML = `<div class="card"><h2>🏆 ${esc(partyName(w.id))} wins the election!</h2>
    <p class="muted">${w.seats} seats after ${g.seat_history.length} rounds.</p>
    <table class="table"><tr><th>Party</th><th>Seats</th><th>Provinces led</th><th>Funds</th></tr>
    ${g.standings.map(s => `<tr><td><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}</td><td>${s.seats}</td><td>${s.regions_led}</td><td>${money(s.money)}</td></tr>`).join('')}</table>
    <div class="row" style="margin-top:14px"><button id="viewmap">View map</button><button class="primary" id="newgame">Back to lobby list</button></div></div>`;
  document.body.appendChild(o);
  $('#viewmap').onclick = () => { S.dismissedFinal = true; o.remove(); };
  $('#newgame').onclick = () => { o.remove(); send({ t: 'leave' }); forgetSession(); };
}

// ---------- boot ----------
const code = new URLSearchParams(location.search).get('room')?.toUpperCase();
const saved = code && JSON.parse(localStorage.getItem('session:' + code) || 'null');
if (saved) { S.session = saved; connect(); } else renderHome();
