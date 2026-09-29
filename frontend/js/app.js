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
  tab: 'region', plan: [], planRound: null, targets: [],
  view: localStorage.getItem('view') || '2d', board: null, boardLoading: false,
};

// ---------- layout ----------
const mobileMq = matchMedia('(max-width: 820px)');
const isMobile = () => mobileMq.matches;
mobileMq.addEventListener('change', () => {
  if (!isMobile() && S.tab === 'map') S.tab = 'region';
  if (S.room) render();
});
const TAB_ICON = { map: '🗺️', region: '📍', campaign: '📣', economy: '🏭', plan: '📝', diplomacy: '🤝', standings: '🏆', report: '📰', chat: '💬' };

// ---------- utils ----------
function stanceText(opt) {
  const parts = Object.entries(opt.sectors || {}).map(([sec, v]) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}% in ${sec} regions`);
  if (opt.lean) parts.push(`+12% with ${opt.lean}-leaning voters, −8% with the other side`);
  return parts.join(', ');
}
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
  const trim = t => t.includes('.') ? t.replace(/0+$/, '').replace(/\.$/, '') : t;  // 1.50 -> 1.5, 100 stays 100
  const fmt = (v, suf) => `${sign}${c}${trim(v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2))}${suf}`;
  if (a >= 1e6) return fmt(a / 1e6, 'M');
  if (a >= 1e3) return fmt(a / 1e3, 'K');
  return `${sign}${c}${Math.round(a)}`;
}
const pct = x => `${Math.round(x * 100)}%`;
const player = id => S.room?.members.find(m => m.id === id);
const partyName = id => id === IND ? 'Independents' : (player(id)?.party ?? '?');
const partyColor = id => id === IND ? IND_COLOR : (player(id)?.color ?? '#666');
const me = () => player(S.you);
const partyLeft = id => S.game?.ideology?.[id] ?? player(id)?.left ?? 50;   // parties can reposition mid-game
const myRight = () => (100 - partyLeft(S.you)) / 100;
const region = id => S.map.regions.find(r => r.id === id);
function mix(a, b, t) {
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return '#' + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
const ideoMult = (pr, rr) => 0.4 + 0.8 * (1 - Math.abs(pr - rr));
// Same as the server's effective_multiplier: ideology fit, adjusted by mood-swing events and the Populist trait.
function effMult(pid, r) {
  let rr = r.right;
  const ev = S.game?.event?.kind;
  if (ev === 'wave_left') rr = Math.max(0, rr - 0.15);
  if (ev === 'wave_right') rr = Math.min(1, rr + 0.15);
  const m = ideoMult((100 - partyLeft(pid)) / 100, rr);
  return S.game?.traits?.[pid] === 'populist' ? Math.max(m, 0.8) : m;
}
const traitOf = id => S.room?.traits?.[S.game?.traits?.[id] ?? player(id)?.trait];
const traitIcon = id => traitOf(id) ? `<span title="${esc(traitOf(id).name)}: ${esc(traitOf(id).text)}">${traitOf(id).icon}</span>` : '';
const partnersOf = id => (S.game?.coalitions.find(c => c.includes(id)) || [id]).filter(x => x !== id);
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
// Economic control of a sector / region economy: an owner keeps 80% for the rest of the game.
// Otherwise report who is closest and how much more they must invest to take it.
// Stake ratio of `id`: their investment ÷ everyone else's combined (Infinity when nobody else invested).
function stakeRatio(stakes, id) {
  const mine = stakes[id] || 0, others = Object.entries(stakes).reduce((t, [k, v]) => t + (k === id ? 0 : v), 0);
  return others ? mine / others : (mine ? Infinity : 0);
}
const fmtRatio = r => r === Infinity ? 'only investor' : `${r >= 10 ? r.toFixed(0) : r.toFixed(1)}×`;

// Economic control of a sector / region economy: an owner keeps 80% for the rest of the game.
// Otherwise report who is closest, their current ratio, and how much more they must invest to take it.
function control(stakes, owner, pool) {
  const R = S.game.rules, e = Object.entries(stakes).sort((a, b) => b[1] - a[1]);
  const mine = stakes[S.you] ? stakeRatio(stakes, S.you) : null;
  if (owner) return { owner, ratio: stakeRatio(stakes, owner), mine };
  const floor = R.control_min_pools * pool;
  if (!e.length) return { need: floor, ratio: 0, mine };
  const others = e.slice(1).reduce((t, [, v]) => t + v, 0);
  return { id: e[0][0], ratio: stakeRatio(stakes, e[0][0]), mine, need: Math.max(R.dominance_ratio * others, floor) - e[0][1] };
}
function ratioBar(c) {
  const R = S.game.rules, target = R.dominance_ratio;
  const lead = c.owner || c.id;
  const fill = c.ratio === Infinity ? 100 : Math.min(100, 100 * c.ratio / target);
  return `<div class="ratio">
    <div class="between"><span>Current ratio${lead ? ` · <b style="color:${partyColor(lead)}">${esc(partyName(lead))}</b>` : ''}</span>
      <b>${lead ? fmtRatio(c.ratio) : '—'} <span class="muted">/ ${target}× needed</span></b></div>
    <div class="ratio-bar"><div style="width:${fill}%;background:${lead ? partyColor(lead) : 'transparent'}"></div></div>
    ${c.mine !== null && lead !== S.you ? `<div class="muted" style="font-size:12px">Your ratio: <b>${fmtRatio(c.mine)}</b></div>` : ''}
  </div>`;
}
function controlNote(c) {
  const R = S.game.rules;
  if (c.owner) return `${ratioBar(c)}<div class="own-note">🔒 <b style="color:${partyColor(c.owner)}">${esc(partyName(c.owner))}</b> owns this — ${pct(R.dominance_share)} of the income for the rest of the game${c.owner === S.you ? ' (you!)' : '. Others share the remaining ' + pct(1 - R.dominance_share) + '.'}</div>`;
  const who = c.id ? `${esc(partyName(c.id))} needs` : 'Anyone needs';
  return `${ratioBar(c)}<div class="muted" style="font-size:12px;margin-top:4px">${who} ${money(c.need)} more to take <b>permanent</b> control (≥${R.dominance_ratio}× everyone else combined${c.id && c.ratio >= R.dominance_ratio ? ' and at least one round of its income' : ''}).</div>`;
}

// ---------- session / routing ----------
function saveSession(s) { S.session = s; localStorage.setItem('session:' + s.code, JSON.stringify(s)); history.replaceState(null, '', '?room=' + s.code); }
function forgetSession() {
  if (S.session) localStorage.removeItem('session:' + S.session.code);
  S.session = null; S.room = S.game = null; S.mapView = null; S.dismissedFinal = false;
  disposeBoard();
  ['results-ov', 'final-ov'].forEach(id => document.getElementById(id)?.remove());
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
  $('#topbar-extra').innerHTML = `<button class="small" id="howto">❓ How to play</button>`;
  $('#howto').onclick = () => showTutorial(0);
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
  $('#topbar-extra').innerHTML = `<button class="small" id="howto">❓ How to play</button><button class="small danger" id="leave">Leave room</button>`;
  $('#howto').onclick = () => showTutorial(0);
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
          <div><b>${esc(m.party)}</b> <span class="muted">— ${esc(m.name)}</span> ${m.id === r.host_id ? '<span class="badge">host</span>' : ''} ${m.is_bot ? `<span class="badge">${esc(m.bot_level)} bot</span>` : ''} <span class="badge" title="${esc(r.traits[m.trait]?.text)}">${r.traits[m.trait]?.icon} ${esc(r.traits[m.trait]?.name)}</span> ${!m.online ? '<span class="badge">offline</span>' : ''}
            ${ideoBar(m.left)}</div>
          <div class="row auto">${m.ready ? '<span class="badge ok">ready</span>' : '<span class="badge">not ready</span>'}
            ${host && m.id !== S.you ? `<button class="small danger" data-kick="${m.id}">✕</button>` : ''}</div>
        </div>`).join('')}
      ${host ? `<div class="row" style="margin-top:12px">
        ${r.bot_levels.map(l => `<button class="small" data-addbot="${l}" ${r.members.length >= r.max_players ? 'disabled' : ''}>+ ${l[0].toUpperCase() + l.slice(1)} bot</button>`).join('')}
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
        <p class="muted" style="font-size:12px">Campaigns work best where voters match your ideology. Winning a region that doesn't match costs you part of its leadership income.</p>
        <label>Policy platform <span class="muted">— wins or loses voters by sector and leaning</span></label>
        <div class="platform">${Object.entries(r.issues).map(([k, iss]) => {
          const cur = (mine.platform || {})[k] || 'center';
          const warn = side => (side === 'right' && mine.left >= 60) || (side === 'left' && mine.left <= 40);
          return `<div class="issue"><span class="iname">${iss.icon} ${esc(iss.name)}</span>
            <span class="seg3">${['left', 'center', 'right'].map(side => `<button class="small ${cur === side ? 'on' : ''}" data-stance="${k}:${side}"
              title="${side === 'center' ? 'No effect' : esc(stanceText(iss[side]))}${side !== 'center' && warn(side) ? ' — against your ideology: only half as convincing' : ''}">${side === 'center' ? 'Neutral' : esc(iss[side].label)}${side !== 'center' && warn(side) ? ' ½' : ''}</button>`).join('')}</span></div>`;
        }).join('')}</div>
        <label>Party leader's strength</label>
        <div class="traits">${Object.entries(r.traits).map(([k, t]) => `<button class="trait ${k === mine.trait ? 'sel' : ''}" data-trait="${k}"><b>${t.icon} ${esc(t.name)}</b><span>${esc(t.text)}</span></button>`).join('')}</div>
        <button class="${mine.ready ? '' : 'primary'}" id="ready" style="width:100%">${mine.ready ? 'Not ready' : "I'm ready"}</button>
      </div>
      <div class="card chat" style="margin-top:14px">${chatHtml()}</div>
    </div>
  </div>`;
  $('#copy').onclick = () => navigator.clipboard?.writeText(location.origin + '/?room=' + r.code).then(() => toast('Link copied', true));
  $('#saveparty').onclick = () => send({ t: 'profile', party: $('#party').value });
  $('#party').onkeydown = e => e.key === 'Enter' && send({ t: 'profile', party: $('#party').value });
  document.querySelectorAll('[data-color]').forEach(b => b.onclick = () => send({ t: 'profile', color: b.dataset.color }));
  document.querySelectorAll('[data-trait]').forEach(b => b.onclick = () => send({ t: 'profile', trait: b.dataset.trait }));
  document.querySelectorAll('[data-stance]').forEach(b => b.onclick = () => {
    const [issue, side] = b.dataset.stance.split(':');
    send({ t: 'profile', platform: { ...(mine.platform || {}), [issue]: side } });
  });
  $('#left').oninput = e => { const l = +e.target.value; $('#ideolabel').textContent = `${l}% left / ${100 - l}% right`; };
  $('#left').onchange = e => send({ t: 'profile', left: +e.target.value });
  $('#ready').onclick = () => send({ t: 'ready', ready: !mine.ready });
  document.querySelectorAll('[data-kick]').forEach(b => b.onclick = () => send({ t: 'kick', id: b.dataset.kick }));
  if (host) {
    document.querySelectorAll('[data-addbot]').forEach(b => b.onclick = () => send({ t: 'add_bot', level: b.dataset.addbot }));
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
const RL = () => S.map?.region_label || 'Region';                 // "Province" / "State"
const rls = () => RL().toLowerCase() + 's';
const planTotal = () => S.plan.reduce((s, a) => s + a.amount, 0);
const budget = () => (S.game.standings.find(s => s.id === S.you)?.money ?? 0) - planTotal();
const canEdit = () => S.room.phase === 'planning' && !S.game.my_pending && !S.game.finished;
const actionKey = a => [a.type, a.kind, a.region, a.sector, a.target].filter(Boolean).join('|');
const planned = a => S.plan.find(p => actionKey(p) === actionKey(a));
const CHIPS = [10e3, 25e3, 50e3, 100e3, 250e3, 500e3, 1e6, 2.5e6, 5e6];

// Add (delta > 0), remove (delta < 0), 'max' (all remaining funds) or 'clear' money on one action.
function spend(a, delta) {
  if (!canEdit()) return toast(S.game.my_pending ? 'Plan submitted — click "Edit plan" to change it' : 'Wait for the next turn');
  const min = S.game.rules.min_action, cur = planned(a);
  if (delta === 'clear') { if (cur) S.plan.splice(S.plan.indexOf(cur), 1); return renderGame(); }
  let amt = delta === 'max' ? Math.floor(budget() / 1000) * 1000 : delta;
  if (amt > 0) amt = Math.min(amt, Math.floor(budget() / 1000) * 1000);
  if (delta !== 'max' && delta > 0 && amt <= 0) return toast('No funds left to plan');
  if (cur) {
    cur.amount += amt;
    if (cur.amount < min) S.plan.splice(S.plan.indexOf(cur), 1);
  } else {
    if (amt < min) return toast(`Not enough funds (minimum ${money(min)})`);
    S.plan.push({ ...a, amount: amt });
  }
  renderGame();
}

function spender(a) {
  const i = S.targets.push(a) - 1;
  const cur = planned(a)?.amount || 0, b = budget();
  const chips = CHIPS.filter(c => c <= b).slice(-4);
  const step = chips[0] || CHIPS[0];
  const dis = canEdit() ? '' : 'disabled';
  return `<div class="spender">
    <div class="between"><span>${cur ? `Planned: <b class="good">${money(cur)}</b>` : '<span class="muted">Nothing planned</span>'}</span>
      ${cur ? `<span class="row auto"><button class="small" data-spend="${i}" data-delta="${-step}" ${dis}>−${money(step)}</button><button class="small danger" data-spend="${i}" data-delta="clear" ${dis}>✕</button></span>` : ''}</div>
    <div class="chips">${chips.map(c => `<button class="chip" data-spend="${i}" data-delta="${c}" ${dis}>+${money(c)}</button>`).join('')}
      <button class="chip max" data-spend="${i}" data-delta="max" ${dis || (b < S.game.rules.min_action ? 'disabled' : '')}>All left</button></div>
  </div>`;
}

function describe(a) {
  const rn = id => esc(region(id)?.name);
  switch (a.type) {
    case 'rally': return `📣 Rally in ${rn(a.region)}`;
    case 'invest_region': return `🏗️ Invest in ${rn(a.region)}`;
    case 'invest_sector': return `${SECTOR_ICON[a.sector]} Invest in ${a.sector}`;
    case 'reposition': return `🧭 Shift ${S.game.rules.reposition_step} points ${a.direction}`;
    case 'transfer': return `💸 Send money to ${esc(partyName(a.target))}`;
    case 'ad': return a.kind === 'tv' ? '📺 National TV ad' : a.kind === 'social' ? `📱 Social ads → ${a.sector}` : `🗯️ Attack ad vs ${esc(partyName(a.target))} in ${rn(a.region)}`;
  }
}

function colorOf(id) {
  const g = S.game.regions[id], r = region(id);
  if (S.mapMode === 'sector') return SECTOR_COLOR[r.sector];
  if (S.mapMode === 'ideology') return mix('#e05263', '#3d8bd4', r.right);
  if (S.mapMode === 'invest') {
    return g.owner ? partyColor(g.owner) : Object.keys(g.invest).length ? '#555c6b' : '#2a3244';
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
  if (!e || (e.pointerType && e.pointerType !== 'mouse')) return tt.classList.add('hidden');  // hover info is mouse-only
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
    <div class="stat opt"><b>${g.unclaimed.length}</b><span>${rls()} without a winner</span></div>
    <div class="stat"><b id="timer">–</b><span>time left</span></div>
    <div class="stat opt"><b>${st?.seats ?? 0}</b><span>your ${esc(S.map.seat_label)}</span></div>
    <div class="stat"><b>${money(st?.money ?? 0)}</b><span>funds</span></div>
    <div class="stat"><b class="${budget() < 0 ? 'bad' : 'good'}">${money(budget())}</b><span>left to plan</span></div>
    <button class="small" id="howto" title="How to play">❓<span class="lbl"> How to play</span></button>
    <button class="small danger" id="leave" title="Leave">⎋<span class="lbl"> Leave</span></button>`;
  $('#howto').onclick = () => showTutorial(0);
  $('#leave').onclick = () => { if (confirm('Leave the game? Your party stays and you can rejoin with the same browser.')) { send({ t: 'leave' }); forgetSession(); } };
  clearInterval(timerInterval);
  const tick = () => { const t = $('#timer'); if (!t) return; const s = S.room.deadline ? Math.max(0, Math.round(S.room.deadline - Date.now() / 1000)) : null; t.textContent = s == null ? '–' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; t.className = s != null && s < 15 ? 'bad' : ''; };
  tick(); timerInterval = setInterval(tick, 1000);
}

function seatDelta(id) {
  const h = S.game.seat_history;
  if (h.length < 1) return 0;
  return (h[h.length - 1][id] || 0) - (h.length > 1 ? (h[h.length - 2][id] || 0) : 0);
}
const deltaHtml = d => d > 0 ? `<span class="good">▲${d}</span>` : d < 0 ? `<span class="bad">▼${-d}</span>` : '<span class="muted">–</span>';

function scoreboard() {
  const g = S.game;
  return g.standings.map((s, i) => `<div class="sb-row ${s.id === S.you ? 'me' : ''}">
    <span class="sb-rank">${i + 1}</span><span class="dot" style="background:${partyColor(s.id)}"></span>
    <span class="sb-name">${traitIcon(s.id)} ${esc(partyName(s.id))}${partnersOf(s.id).length ? ' <span title="in a coalition">🤝</span>' : ''}${g.government.includes(s.id) ? ' <span title="in government">🏛️</span>' : ''}</span>
    <span class="sb-seats"><b>${s.seats}</b> ${deltaHtml(seatDelta(s.id))}</span>
    <span class="sb-money">${money(s.money)}</span>
    <span class="sb-status">${S.room.phase === 'planning' ? (g.submitted.includes(s.id) ? '✓' : '…') : ''}</span></div>`).join('');
}

function renderGame() {
  const g = S.game;
  if (S.planRound !== g.round) {
    S.plan = g.my_pending ? [...g.my_pending] : []; S.planRound = g.round; S.resultsHidden = false;
  }
  S.targets = [];
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
        <div class="event-card" id="eventcard"></div>
        <div class="scoreboard" id="scoreboard"></div>
        <div class="tabs" id="tabs"></div>
        <div class="panel" id="panel"></div>
        <div class="submit-bar" id="submitbar"></div>
      </div>
    </div>`;
    S.mapView = new MapView($('#mapbox'), S.map, { onSelect: id => { S.selected = id; S.tab = 'region'; renderGame(); $('#panel').scrollTop = 0; }, tooltip });
  }
  if (!isMobile() && S.tab === 'map') S.tab = 'region';
  const gameEl = $('.game');
  gameEl.classList.toggle('mobile', isMobile());
  gameEl.dataset.tab = S.tab;
  const modes = { leader: 'Leaders', sector: 'Sectors', ideology: 'Ideology', match: 'My fit', invest: 'Investors' };
  $('#maptools').innerHTML = Object.entries(modes).map(([k, v]) => `<button class="small ${S.mapMode === k ? 'on' : ''}" data-mode="${k}">${v}</button>`).join('')
    + `<span class="muted hint" style="margin-left:auto;font-size:12px">${isMobile() ? 'Tap' : 'Click'} a ${RL().toLowerCase()} to campaign there · ${isMobile() ? 'pinch' : 'scroll'} to zoom</span>`;
  $('#maptools').insertAdjacentHTML('afterbegin', `<span class="seg"><button class="small ${S.view === '2d' ? 'on' : ''}" data-view="2d">2D map</button><button class="small ${S.view === '3d' ? 'on' : ''}" data-view="3d">🎲 3D board</button></span>`);
  $('#maptools').querySelectorAll('[data-mode]').forEach(b => b.onclick = () => { S.mapMode = b.dataset.mode; renderGame(); });
  $('#maptools').querySelectorAll('[data-view]').forEach(b => b.onclick = () => {
    S.view = b.dataset.view; try { localStorage.setItem('view', S.view); } catch {}
    renderGame();
  });
  S.mapView.paint(colorOf, S.selected);
  syncBoard();

  const totals = g.last_report?.seats || {};
  const allSeats = S.map.regions.reduce((s, r) => s + r.seats, 0);
  $('#seatbar').innerHTML = [...g.standings.map(s => [s.id, s.seats]), [IND, totals[IND] ?? allSeats]]
    .filter(([, n]) => n > 0).map(([id, n]) => `<div title="${esc(partyName(id))}: ${n}" style="width:${100 * n / allSeats}%;background:${partyColor(id)}"></div>`).join('');
  $('#legend').innerHTML = S.mapMode === 'sector'
    ? SECTORS.map(s => `<span><span class="dot" style="background:${SECTOR_COLOR[s]}"></span>${SECTOR_ICON[s]} ${s}</span>`).join('')
    : S.mapMode === 'ideology' ? '<span><span class="dot" style="background:#e05263"></span>left</span><span><span class="dot" style="background:#3d8bd4"></span>right</span>'
    : S.mapMode === 'match' ? '<span><span class="dot" style="background:#2d8f5c"></span>matches your ideology</span><span><span class="dot" style="background:#5a1f28"></span>opposite</span>'
    : [...g.standings.map(s => `<span><span class="dot" style="background:${partyColor(s.id)}"></span>${esc(partyName(s.id))} ${s.seats}</span>`), `<span><span class="dot" style="background:${IND_COLOR}"></span>Independent ${totals[IND] ?? allSeats}</span>`].join('')
      + (S.mapMode === 'invest' ? '<span class="muted">(colored = owner of the economy, grey = invested but nobody owns it yet)</span>' : '');

  $('#scoreboard').innerHTML = scoreboard();
  const ev = g.event;
  $('#eventcard').innerHTML = `<span class="ev-icon">${ev.icon}</span><div><b>Round ${g.round}: ${esc(ev.title)}</b><div>${esc(ev.text)}</div></div>`;
  $('#eventcard').onclick = () => $('#eventcard').classList.toggle('open');
  const incoming = g.proposals.filter(([, to]) => to === S.you).length
    + g.cabinet_proposals.filter(c => c.members.includes(S.you) && !c.accepted.includes(S.you)).length;
  const tabs = { ...(isMobile() ? { map: 'Map' } : {}), region: RL(), campaign: isMobile() ? 'Ads' : 'Campaign', economy: 'Economy',
    plan: `Plan${S.plan.length ? ` (${S.plan.length})` : ''}`, diplomacy: `Diplomacy${incoming ? ` (${incoming}!)` : ''}`,
    standings: 'Standings', report: isMobile() ? 'News' : 'Report', chat: 'Chat' };
  $('#tabs').innerHTML = Object.entries(tabs).map(([k, v]) => `<button class="${S.tab === k ? 'on' : ''}" data-tab="${k}"><span class="ti">${TAB_ICON[k]}</span><span class="tl">${v}</span></button>`).join('');
  $('#tabs').querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { S.tab = b.dataset.tab; tooltip(null); renderGame(); $('#panel').scrollTop = 0; });
  $('#tabs .on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });

  const panel = $('#panel');
  const typing = S.tab === 'chat' && document.activeElement?.id === 'chatin' ? $('#chatin').value : null;
  const scroll = panel.scrollTop;
  panel.innerHTML = ({ map: () => '', region: regionPanel, campaign: campaignPanel, economy: economyPanel, plan: planPanel, diplomacy: diplomacyPanel, standings: standingsPanel, report: reportPanel, chat: () => `<div class="chat" style="height:100%">${chatHtml()}</div>` })[S.tab]();
  panel.scrollTop = scroll;
  bindPanel();
  if (typing !== null) { $('#chatin').value = typing; $('#chatin').focus(); }
  renderSubmitBar();
  renderResults();
  if (g.finished) renderFinal();
  if (!g.finished && !localStorage.getItem('tut:' + S.room.code)) { localStorage.setItem('tut:' + S.room.code, '1'); showTutorial(0); }
}

function regionPanel() {
  if (!S.selected) return `${isMobile() ? '<button class="primary" data-tab-go="map" style="margin-bottom:10px">🗺️ Open the map</button>' : ''}<h2>Pick a ${RL().toLowerCase()}</h2><p class="muted">Click any ${RL().toLowerCase()} on the map to rally there, invest in its economy or run attack ads. National and sector-wide ads are in the <b>Campaign</b> tab; industry investments in <b>Economy</b>.</p>`;
  const r = region(S.selected), g = S.game.regions[S.selected];
  const back = isMobile() ? `<button class="small" data-tab-go="map" style="margin-bottom:8px">← Back to map</button>` : '';
  const votes = Object.entries(g.support).concat([[IND, g.independent]]).sort((a, b) => b[1] - a[1]);
  const vtotal = votes.reduce((s, [, v]) => s + v, 0);
  const inv = Object.entries(g.invest).sort((a, b) => b[1] - a[1]);
  const itotal = inv.reduce((s, [, v]) => s + v, 0);
  const ctl = control(g.invest, g.owner, g.pool);
  const mult = S.game.my_mult?.[r.id] ?? effMult(S.you, r), pen = mismatch(myRight(), r.right) * (S.game.traits[S.you] === 'populist' ? 0.5 : 1);
  const bonus = r.seats * S.game.rules.leader_bonus_per_seat;
  const opponents = S.game.standings.filter(s => s.id !== S.you && !partnersOf(S.you).includes(s.id));
  if (!opponents.some(o => o.id === S.negTarget)) S.negTarget = opponents[0]?.id;
  return `
    ${back}<div class="between"><h2>${esc(r.name)} <span class="muted" style="font-weight:500">· ${r.seats} ${esc(S.map.seat_label)}</span></h2><span class="tag" style="background:${SECTOR_COLOR[r.sector]}">${SECTOR_ICON[r.sector]} ${r.sector}</span></div>
    ${ideoBar(Math.round((1 - r.right) * 100))}
    <dl class="kv" style="margin-top:10px">
      <dt>Leader</dt><dd>${g.leader ? `<b style="color:${partyColor(g.leader)}">${esc(partyName(g.leader))}</b>` : 'none'}</dd>
      <dt>Your ideology fit</dt><dd class="${mult >= 1 ? 'good' : mult < 0.8 ? 'bad' : ''}">×${mult.toFixed(2)} campaign effect</dd>
      <dt>If you lead it</dt><dd>${money(bonus * (1 - pen))}/round ${pen > 0 ? `<span class="bad">(−${pct(pen)} mismatch)</span>` : ''}</dd>
    </dl>
    <div class="action"><div class="title">📣 Rally & ground campaign</div><div class="desc">Wins votes here (×${mult.toFixed(2)} for your ideology fit).</div>${spender({ type: 'rally', region: r.id })}</div>
    <div class="action"><div class="title">🏗️ Invest in the local economy</div><div class="desc">Permanent stake in ${money(g.pool)}/round. First to ≥${S.game.rules.dominance_ratio}× everyone else combined owns it: ${pct(S.game.rules.dominance_share)} for good.</div>${controlNote(ctl)}${spender({ type: 'invest_region', region: r.id })}</div>
    ${opponents.length ? `<div class="action"><div class="title">🗯️ Attack ad</div><div class="desc">Removes a rival's support here.</div>
      <div class="chips" style="margin-bottom:6px">${opponents.map(o => `<button class="chip ${o.id === S.negTarget ? 'on' : ''}" data-negtarget="${o.id}"><span class="dot" style="background:${partyColor(o.id)};width:9px;height:9px"></span> ${esc(partyName(o.id))}</button>`).join('')}</div>
      ${spender({ type: 'ad', kind: 'negative', region: r.id, target: S.negTarget })}</div>` : ''}
    <h3>Polls</h3>${bars(votes, vtotal, v => pct(v / vtotal))}
    <h3>Seats (last election)</h3>${Object.keys(g.seats).length ? bars(Object.entries(g.seats), r.seats, v => v) : '<div class="muted">No election yet</div>'}
    <h3>Investors</h3>${bars(inv, itotal, v => money(v))}`;
}

function campaignPanel() {
  S.socialSector ??= 'trade';
  return `<h2>Campaign ads</h2>
    <div class="action"><div class="title">📺 National TV ad</div><div class="desc">Reaches every ${RL().toLowerCase()} at once, weighted by ${esc(S.map.seat_label)}.</div>${spender({ type: 'ad', kind: 'tv' })}</div>
    <div class="action"><div class="title">📱 Social media ads</div><div class="desc">Targets every ${RL().toLowerCase()} of one sector — more efficient than TV.</div>
      <div class="chips" style="margin-bottom:6px">${SECTORS.map(s => `<button class="chip ${s === S.socialSector ? 'on' : ''}" data-social="${s}">${SECTOR_ICON[s]} ${s}</button>`).join('')}</div>
      ${spender({ type: 'ad', kind: 'social', sector: S.socialSector })}</div>
    <p class="muted" style="font-size:12px">Rallies and attack ads: click a ${RL().toLowerCase()} on the map.</p>
    ${ideologyPanel()}`;
}

function ideologyPanel() {
  const g = S.game, R = g.rules, left = partyLeft(S.you), plat = g.platforms[S.you] || {};
  const planned = S.plan.find(a => a.type === 'reposition');
  const doubting = g.credibility[S.you] >= g.round;
  const issues = S.room.issues;
  return `<h2 style="margin-top:18px">🧭 Ideology</h2>
    <div class="action">${ideoBar(left)}
      <div class="desc" style="margin-top:8px">Voters who share your ideology stay loyal (their support fades half as fast); the others drift away faster.</div>
      <div class="title" style="margin-top:6px">Your platform</div>
      ${Object.entries(issues).map(([k, iss]) => {
        const side = plat[k] || 'center';
        return `<div style="font-size:13px">${iss.icon} ${esc(iss.name)}: <b>${side === 'center' ? 'Neutral' : esc(iss[side].label)}</b>${side !== 'center' ? ` <span class="muted">(${esc(stanceText(iss[side]))})</span>` : ''}</div>`;
      }).join('')}
    </div>
    <div class="action"><div class="title">Reposition the party</div>
      <div class="desc">Move ${R.reposition_step} points left or right for ${money(R.reposition_cost)}. Voters are wary of a flip-flop: your campaigns are ${pct(1 - R.credibility_penalty)} weaker this round and next.</div>
      ${doubting ? `<div class="bad" style="font-size:12px;margin-bottom:6px">Voters still doubt your last repositioning (until round ${g.credibility[S.you]}).</div>` : ''}
      ${planned ? `<div class="between"><span>Planned: shift <b>${planned.direction}</b> → ${left + (planned.direction === 'left' ? R.reposition_step : -R.reposition_step)}% left</span><button class="small danger" data-unrepos>✕</button></div>`
        : `<div class="row"><button data-repos="left" ${left >= 100 || !canEdit() ? 'disabled' : ''}>◀ Shift left</button><button data-repos="right" ${left <= 0 || !canEdit() ? 'disabled' : ''}>Shift right ▶</button></div>`}
    </div>`;
}

function economyPanel() {
  const g = S.game;
  return `<h2>Sector economy</h2>
    <p class="muted" style="font-size:12px">Each sector pays every round. The first to invest ≥${g.rules.dominance_ratio}× everyone else combined <b>owns it for good</b>: ${pct(g.rules.dominance_share)} of its income every round, whatever others invest later. Market shifts each round.</p>
    ${SECTORS.map(s => {
      const inv = Object.entries(g.sectors[s]).sort((a, b) => b[1] - a[1]);
      const total = inv.reduce((a, [, v]) => a + v, 0);
      const ctl = control(g.sectors[s], g.sector_owner[s], g.sector_pools[s]);
      const m = g.market[s];
      return `<div class="sector-card">
        <div class="between"><b>${SECTOR_ICON[s]} ${s}${g.sector_owner[s] ? ` <span class="dot" title="owner" style="background:${partyColor(g.sector_owner[s])}"></span>🔒` : ''}</b><span>${money(g.sector_pools[s])}/round <span class="${m >= 1 ? 'good' : 'bad'}">${m >= 1 ? '▲' : '▼'}${Math.abs(Math.round((m - 1) * 100))}%</span></span></div>
        ${inv.length ? bars(inv, total, v => money(v)) : ''}
        ${controlNote(ctl)}
        ${spender({ type: 'invest_sector', sector: s })}</div>`;
    }).join('')}`;
}

function planPanel() {
  return `<h2>This round's plan</h2>
    ${S.plan.length ? S.plan.map(a => `<div class="plan-block"><div class="between"><span>${describe(a)}</span>${a.type === 'reposition' ? `<span>${money(a.amount)} ${canEdit() ? '<button class="small danger" data-unrepos>✕</button>' : ''}</span>` : ''}</div>${a.type === 'reposition' ? '' : spender({ ...a, amount: undefined })}</div>`).join('')
      + `<div class="plan-item"><b>Total</b><b>${money(planTotal())}</b></div>` : '<p class="muted">Nothing planned yet. Unspent funds carry over to the next round.</p>'}`;
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
  const majority = Math.floor(maxSeats / 2) + 1;
  const bySector = id => SECTORS.map(sec => S.map.regions.filter(r => r.sector === sec && g.regions[r.id].leader === id).length);
  return `<h2>Standings</h2>
    <div class="table-wrap"><table class="table"><tr><th>#</th><th>Party</th><th>${esc(S.map.seat_label)}</th><th>Δ</th><th>Led</th><th>Funds</th></tr>
    ${g.standings.map((s, i) => `<tr class="${s.id === S.you ? 'me' : ''}"><td>${i === 0 && hist.length ? '<span class="crown">♛</span>' : i + 1}</td><td style="text-align:left"><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}${s.id === S.you ? ' (you)' : ''}</td>
      <td><b>${s.seats}</b></td><td>${deltaHtml(seatDelta(s.id))}</td><td>${s.regions_led}</td><td>${money(s.money)}</td></tr>`).join('')}</table></div>
    <div class="muted" style="font-size:12px;margin-top:6px">Majority: ${majority}. ${g.standings[0]?.seats >= majority ? `<b style="color:${partyColor(g.standings[0].id)}">${esc(partyName(g.standings[0].id))} holds a majority.</b>` : 'Nobody holds a majority.'}</div>
    <h3>${esc(S.map.seat_label)} over time</h3>
    ${hist.length ? `<svg viewBox="-4 -4 ${W + 8} ${H + 8}" style="width:100%"><line x1="0" x2="${W}" y1="${H - majority / maxSeats * H}" y2="${H - majority / maxSeats * H}" stroke="#8c98b3" stroke-dasharray="4 4" stroke-width="1"/>${lines}</svg>` : '<p class="muted">After the first election.</p>'}
    <h3>${RL()}s led by sector</h3>
    <table class="table"><tr><th>Party</th>${SECTORS.map(s => `<th title="${s}">${SECTOR_ICON[s]}</th>`).join('')}</tr>
    ${g.standings.map(s => `<tr><td><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}</td>${bySector(s.id).map(n => `<td>${n}</td>`).join('')}</tr>`).join('')}</table>
    <p class="muted" style="font-size:12px">Game ends after round ${g.rounds} once every ${RL().toLowerCase()} has a winner (overtime until then, max ${g.max_rounds} rounds). Most ${esc(S.map.seat_label)} wins.</p>
    ${g.unclaimed.length ? `<h3>No winner yet (${g.unclaimed.length})</h3><div style="font-size:12px">${g.unclaimed.map(id => esc(region(id).name)).join(', ')}</div>` : ''}`;
}

function diplomacyPanel() {
  const g = S.game, totals = Object.fromEntries(g.standings.map(s => [s.id, s.seats]));
  const all = S.map.regions.reduce((t, r) => t + r.seats, 0), majority = Math.floor(all / 2) + 1;
  const mine = partnersOf(S.you), group = [S.you, ...mine];
  const groupSeats = group.reduce((t, id) => t + (totals[id] || 0), 0);
  const incoming = g.proposals.filter(([, to]) => to === S.you).map(([from]) => from);
  const outgoing = g.proposals.filter(([from]) => from === S.you).map(([, to]) => to);
  const others = g.standings.filter(s => s.id !== S.you && !mine.includes(s.id));
  return `<h2>Diplomacy</h2>
    ${governmentHtml()}
    <p class="muted" style="font-size:12px">A coalition that holds a <b>majority (${majority} ${esc(S.map.seat_label)})</b> at the end of the game wins together. Partners can't run attack ads against each other. Anyone can leave a coalition at any time.</p>
    ${incoming.length ? `<h3>Offers to you</h3>${incoming.map(id => `<div class="action between"><span>🤝 <b style="color:${partyColor(id)}">${esc(partyName(id))}</b> invites you into a coalition</span>
      <span class="row auto"><button class="small primary" data-accept="${id}">Accept</button><button class="small" data-decline="${id}">Decline</button></span></div>`).join('')}` : ''}
    <h3>Your coalition</h3>
    ${mine.length ? `<div class="action"><div>${group.map(id => `<span class="dot" style="background:${partyColor(id)};width:9px;height:9px"></span> ${esc(partyName(id))} (${totals[id] || 0})`).join(' + ')}</div>
      <div class="between" style="margin-top:6px"><span>${groupSeats}/${majority} for a majority ${groupSeats >= majority ? '<span class="good">✓ majority!</span>' : ''}</span><button class="small danger" id="leavecoal">Leave coalition</button></div>
      <div class="ideo-bar" style="margin-top:6px"><div style="width:${Math.min(100, 100 * groupSeats / majority)}%;background:var(--good)"></div></div>
      ${tensionHtml(group)}</div>
      ${cabinetHtml(group)}`
      : '<p class="muted">You are on your own for now.</p>'}
    <h3>Other parties</h3>
    ${others.map(s => `<div class="action between"><span><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${traitIcon(s.id)} <b>${esc(partyName(s.id))}</b> · ${s.seats} · ${partyLeft(s.id)}% left${partnersOf(s.id).length ? ' · 🤝 in a coalition' : ''}</span>
      ${outgoing.includes(s.id) ? '<span class="muted">offer sent…</span>' : `<button class="small" data-propose="${s.id}">Propose coalition</button>`}</div>`).join('') || '<p class="muted">Everyone is already in your coalition.</p>'}
    <h3>💸 Send money</h3>
    <p class="muted" style="font-size:12px">Pay another party — to seal a deal, support a partner or buy a favour. Arrives when the round resolves. Agree terms in Chat.</p>
    <div class="chips" style="margin-bottom:6px">${g.standings.filter(s => s.id !== S.you).map(s => `<button class="chip ${s.id === S.transferTo ? 'on' : ''}" data-transferto="${s.id}"><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}</button>`).join('')}</div>
    ${S.transferTo ? spender({ type: 'transfer', target: S.transferTo }) : '<p class="muted" style="font-size:12px">Pick a party first.</p>'}`;
}

function governmentHtml() {
  const g = S.game, M = S.room.ministries;
  const gov = g.government;
  const govSeats = gov.reduce((t, id) => t + (g.standings.find(s => s.id === id)?.seats || 0), 0);
  return `<h3>🏛️ Government</h3>
    <p class="muted" style="font-size:12px">After every election the largest bloc governs and holds the ministries. A single party holds them all; a coalition must agree how to share them.</p>
    ${gov.length ? `<div style="margin-bottom:6px">${gov.map(id => `<b style="color:${partyColor(id)}">${esc(partyName(id))}</b>`).join(' + ')} <span class="muted">· ${govSeats} ${esc(S.map.seat_label)}</span></div>`
      : '<div class="muted" style="margin-bottom:6px">No government yet (caretaker period).</div>'}
    <div class="ministries">${Object.entries(M).map(([k, m]) => {
      const h = g.ministries[k];
      return `<div class="ministry" title="${esc(m.text)}"><span>${m.icon} ${esc(m.name)}</span>
        <span>${h ? `<span class="dot" style="background:${partyColor(h)};width:9px;height:9px"></span> ${esc(partyName(h))}` : '<span class="muted">vacant</span>'}</span>
        <small class="muted">${esc(m.text)}</small></div>`;
    }).join('')}</div>
    ${gov.length > 1 && !Object.values(g.ministries).some(Boolean) ? '<p class="bad" style="font-size:12px">The governing coalition has no ministry deal yet, so nobody gets the ministry bonuses.</p>' : ''}`;
}

function tensionHtml(group) {
  const t = S.game.tension.find(x => x.members.includes(S.you));
  const lefts = group.map(partyLeft), gap = Math.max(...lefts) - Math.min(...lefts);
  return `<div style="font-size:12px;margin-top:8px">Ideological gap: <b>${gap} points</b> ${t && t.loss > 0
    ? `<span class="bad">— every partner loses ${Math.round(t.loss * 100)}% of its support each round (voters dislike the deal)</span>`
    : '<span class="good">— compatible partners, no tension</span>'}</div>`;
}

function cabinetHtml(group) {
  const g = S.game, M = S.room.ministries;
  const key = [...group].sort().join();
  const agreed = g.cabinets.find(c => c.members.join() === key);
  const prop = g.cabinet_proposals.find(c => c.members.join() === key);
  // the editor starts from the pending proposal, else the agreed deal, else a seat-proportional suggestion
  S.cabinetDraft = S.cabinetDraft && S.cabinetDraft.key === key ? S.cabinetDraft : { key, alloc: { ...(prop?.alloc || agreed?.alloc || suggestCabinet(group)) } };
  const draft = S.cabinetDraft.alloc;
  const count = alloc => group.map(id => `${esc(partyName(id))} ${Object.values(alloc).filter(x => x === id).length}`).join(' · ');
  return `<h3>🤝 Ministry negotiation</h3>
    ${agreed ? `<div class="action"><div class="title">Current deal</div><div style="font-size:12px">${count(agreed.alloc)}</div></div>` : '<p class="muted" style="font-size:12px">No deal yet. Propose how to share the ministries; every partner must accept.</p>'}
    ${prop ? `<div class="action"><div class="title">Proposal by ${esc(partyName(prop.by))}</div>
      ${Object.entries(prop.alloc).map(([m, id]) => `<div class="between" style="font-size:13px"><span>${M[m].icon} ${esc(M[m].name)}</span><span style="color:${partyColor(id)}">${esc(partyName(id))}</span></div>`).join('')}
      <div class="muted" style="font-size:12px;margin-top:6px">${count(prop.alloc)} · accepted by ${prop.accepted.map(id => esc(partyName(id))).join(', ')}</div>
      ${prop.accepted.includes(S.you) ? '<div class="good" style="font-size:12px">✓ You accepted. Waiting for the others.</div>'
        : '<div class="row" style="margin-top:6px"><button class="primary" data-cab="yes">Accept deal</button><button data-cab="no">Reject</button></div>'}</div>` : ''}
    <div class="action"><div class="title">${prop ? 'Counter-proposal' : 'Propose a split'}</div>
      ${Object.entries(M).map(([m, info]) => `<div class="between" style="font-size:13px;margin:3px 0"><span title="${esc(info.text)}">${info.icon} ${esc(info.name)}</span>
        <select data-cabm="${m}" style="width:auto;padding:4px 6px">${group.map(id => `<option value="${id}" ${draft[m] === id ? 'selected' : ''}>${esc(partyName(id))}</option>`).join('')}</select></div>`).join('')}
      <div class="between" style="margin-top:6px"><span class="muted" style="font-size:12px">${count(draft)}</span><button class="primary" data-cab="propose">Propose</button></div></div>`;
}

function suggestCabinet(group) {
  // seat-proportional (D'Hondt) split, in ministry order
  const seats = Object.fromEntries(group.map(id => [id, (S.game.standings.find(s => s.id === id)?.seats || 0) + 1]));
  const got = Object.fromEntries(group.map(id => [id, 0])), alloc = {};
  for (const m of Object.keys(S.room.ministries)) {
    const id = group.reduce((a, b) => seats[b] / (got[b] + 1) > seats[a] / (got[a] + 1) ? b : a);
    alloc[m] = id; got[id]++;
  }
  return alloc;
}

function incomeHtml(inc) {
  return `<dl class="kv"><dt>Base</dt><dd>${money(inc.base)}</dd><dt>Leadership bonuses</dt><dd>${money(inc.leadership)}</dd>
    <dt>Ideology mismatch</dt><dd class="bad">${money(inc.penalty)}</dd><dt>${RL()} economies</dt><dd>${money(inc.regions)}</dd>
    <dt>Sectors</dt><dd>${money(inc.sectors)}</dd>${inc.ministries ? `<dt>Ministries</dt><dd class="good">${money(inc.ministries)}</dd>` : ''}${inc.event ? `<dt>Event bonus</dt><dd class="good">${money(inc.event)}</dd>` : ''}<dt><b>Total</b></dt><dd><b class="good">${money(inc.total)}</b></dd></dl>`;
}

function reportPanel() {
  const rep = S.game.last_report;
  if (!rep) return '<p class="muted">The first results come in after round 1.</p>';
  return `<h2>Round ${rep.round} results</h2><h3>Your income</h3>${incomeHtml(rep.income[S.you] || {})}
    <h3>News</h3>${rep.events.length ? rep.events.map(e => `<div>• ${esc(e)}</div>`).join('') : '<div class="muted">A quiet round.</div>'}`;
}

function bindPanel() {
  const p = $('#panel');
  p.querySelectorAll('[data-spend]').forEach(b => b.onclick = () => {
    const d = b.dataset.delta;
    spend(S.targets[+b.dataset.spend], d === 'max' || d === 'clear' ? d : +d);
  });
  p.querySelectorAll('[data-negtarget]').forEach(b => b.onclick = () => { S.negTarget = b.dataset.negtarget; renderGame(); });
  p.querySelectorAll('[data-social]').forEach(b => b.onclick = () => { S.socialSector = b.dataset.social; renderGame(); });
  p.querySelectorAll('[data-tab-go]').forEach(b => b.onclick = () => { S.tab = b.dataset.tabGo; renderGame(); });
  p.querySelectorAll('[data-repos]').forEach(b => b.onclick = () => {
    if (!canEdit()) return;
    const cost = S.game.rules.reposition_cost;
    if (cost > budget()) return toast('Not enough funds');
    S.plan.push({ type: 'reposition', direction: b.dataset.repos, amount: cost }); renderGame();
  });
  p.querySelectorAll('[data-unrepos]').forEach(b => b.onclick = () => { S.plan = S.plan.filter(a => a.type !== 'reposition'); renderGame(); });
  p.querySelectorAll('[data-cabm]').forEach(sel => sel.onchange = () => { S.cabinetDraft.alloc[sel.dataset.cabm] = sel.value; renderGame(); });
  p.querySelectorAll('[data-cab]').forEach(b => b.onclick = () => {
    const v = b.dataset.cab;
    if (v === 'propose') send({ t: 'cabinet_propose', alloc: S.cabinetDraft.alloc });
    else send({ t: 'cabinet_answer', accept: v === 'yes' });
  });
  p.querySelectorAll('[data-propose]').forEach(b => b.onclick = () => send({ t: 'propose', target: b.dataset.propose }));
  p.querySelectorAll('[data-accept]').forEach(b => b.onclick = () => send({ t: 'respond', from: b.dataset.accept, accept: true }));
  p.querySelectorAll('[data-decline]').forEach(b => b.onclick = () => send({ t: 'respond', from: b.dataset.decline, accept: false }));
  p.querySelectorAll('[data-transferto]').forEach(b => b.onclick = () => { S.transferTo = b.dataset.transferto; renderGame(); });
  $('#leavecoal')?.addEventListener('click', () => confirm('Leave your coalition?') && send({ t: 'leave_coalition' }));
  if (S.tab === 'chat') bindChat();
}

function renderSubmitBar() {
  const g = S.game, bar = $('#submitbar');
  if (g.finished) { bar.innerHTML = '<b>Game over.</b>'; return; }
  if (S.room.phase === 'results') {
    const done = S.room.acks.includes(S.you);
    bar.innerHTML = `<div class="between"><span>${done ? '<span class="good">✓ Waiting for the others…</span>' : 'Round results are in.'}</span>
      <span class="row auto"><button class="small" id="showres">Show results</button>${done ? '' : '<button class="primary" id="nextturn">Go to next turn ▶</button>'}</span></div>`;
    $('#showres').onclick = () => { S.resultsHidden = false; renderGame(); };
    $('#nextturn')?.addEventListener('click', () => send({ t: 'next' }));
    return;
  }
  const waiting = g.standings.filter(s => !g.submitted.includes(s.id)).map(s => partyName(s.id));
  bar.innerHTML = g.my_pending
    ? `<div class="between"><span class="good">✓ Plan submitted. Waiting for: ${esc(waiting.join(', ') || '—')}</span><button class="small" id="unsubmit">Edit plan</button></div>`
    : `<div class="between"><span>${S.plan.length} actions · ${money(planTotal())} · <span class="muted">${money(budget())} left</span></span><button class="primary" id="submit">End turn</button></div>`;
  $('#unsubmit')?.addEventListener('click', () => send({ t: 'unsubmit' }));
  $('#submit')?.addEventListener('click', () => {
    if (!S.plan.length && !confirm('End your turn without spending anything?')) return;
    send({ t: 'submit', actions: S.plan });
  });
}

function overlay(id, html) {
  let o = document.getElementById(id);
  if (!html) { o?.remove(); return null; }
  if (!o) { o = document.createElement('div'); o.id = id; o.className = 'overlay'; document.body.appendChild(o); }
  o.innerHTML = html;
  return o;
}

function renderResults() {
  const g = S.game, rep = g.last_report;
  if (S.room.phase !== 'results' || g.finished || S.resultsHidden || !rep) return overlay('results-ov', null);
  const done = S.room.acks.includes(S.you);
  const waiting = S.room.members.filter(m => !S.room.acks.includes(m.id) && m.online).map(m => m.party);
  overlay('results-ov', `<div class="card wide">
    <div class="between"><h2>🗳️ Round ${rep.round} results</h2><span class="muted">${g.overtime ? `<span class="bad">Overtime</span> · ` : ''}${g.unclaimed.length} ${rls()} without a winner</span></div>
    <div class="table-wrap"><table class="table"><tr><th>#</th><th>Party</th><th>${esc(S.map.seat_label)}</th><th>Change</th><th class="opt">${RL()}s led</th><th>Income</th><th class="opt">Funds</th></tr>
    ${g.standings.map((s, i) => `<tr class="${s.id === S.you ? 'me' : ''}"><td>${i === 0 ? '<span class="crown">♛</span>' : i + 1}</td>
      <td style="text-align:left"><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}${s.id === S.you ? ' (you)' : ''}</td>
      <td><b>${s.seats}</b></td><td>${deltaHtml(seatDelta(s.id))}</td><td class="opt">${s.regions_led}</td><td class="good">+${money(rep.income[s.id]?.total ?? 0)}</td><td class="opt">${money(s.money)}</td></tr>`).join('')}
      <tr><td></td><td style="text-align:left"><span class="dot" style="background:${IND_COLOR};width:9px;height:9px"></span> Independents</td><td>${rep.seats[IND] || 0}</td><td>${deltaHtml(seatDelta(IND))}</td><td class="opt"></td><td></td><td class="opt"></td></tr></table></div>
    <div class="grid2">
      <div><h3>Your income</h3>${incomeHtml(rep.income[S.you] || {})}</div>
      <div><h3>News</h3><div class="news">${rep.events.length ? rep.events.map(e => `<div>• ${esc(e)}</div>`).join('') : '<div class="muted">A quiet round.</div>'}</div></div>
    </div>
    <div class="event-card" style="margin-top:12px"><span class="ev-icon">${g.event.icon}</span><div><b>Next round: ${esc(g.event.title)}</b><div>${esc(g.event.text)}</div></div></div>
    <div class="between" style="margin-top:14px">
      <span class="muted" style="font-size:12px">${done ? `Waiting for: ${esc(waiting.join(', ') || '—')}` : `${S.room.acks.length}/${S.room.members.length} ready`}</span>
      <span class="row auto"><button id="hideres">View map</button>${done ? '<button disabled>✓ Ready</button>' : '<button class="primary" id="nextturn2">Go to next turn ▶</button>'}</span>
    </div></div>`);
  $('#hideres').onclick = () => { S.resultsHidden = true; renderGame(); };
  $('#nextturn2')?.addEventListener('click', () => send({ t: 'next' }));
}

function renderFinal() {
  if (S.dismissedFinal) return overlay('final-ov', null);
  overlay('results-ov', null);
  const g = S.game, res = g.result, w = g.standings.find(s => s.id === res.lead);
  const names = res.winners.map(id => `<b style="color:${partyColor(id)}">${esc(partyName(id))}</b>`).join(' + ');
  overlay('final-ov', `<div class="card wide"><h2>🏆 ${res.coalition ? `The ${names} coalition governs!` : `${names} wins the election!`}</h2>
    <p class="muted">${res.coalition ? `${res.seats} ${esc(S.map.seat_label)} together — a majority is ${res.majority}. ${esc(partyName(res.lead))} leads the government.` : `${w.seats} ${esc(S.map.seat_label)} after ${g.seat_history.length} rounds${w.seats >= res.majority ? ' — an outright majority' : ' (no coalition reached a majority)'}.`}</p>
    <table class="table"><tr><th>Party</th><th>${esc(S.map.seat_label)}</th><th>${RL()}s led</th><th>Funds</th></tr>
    ${g.standings.map(s => `<tr><td><span class="dot" style="background:${partyColor(s.id)};width:9px;height:9px"></span> ${esc(partyName(s.id))}</td><td>${s.seats}</td><td>${s.regions_led}</td><td>${money(s.money)}</td></tr>`).join('')}</table>
    <div class="row" style="margin-top:14px"><button id="viewmap">View map</button><button class="primary" id="newgame">Back to lobby list</button></div></div>`);
  $('#viewmap').onclick = () => { S.dismissedFinal = true; overlay('final-ov', null); };
  $('#newgame').onclick = () => { overlay('final-ov', null); send({ t: 'leave' }); forgetSession(); };
}

// ---------- 3D board ----------
function disposeBoard() {
  S.board?.dispose(); S.board = null;
  if (S.mapView) { S.mapView.svg.style.display = ''; S.mapView.zoomUi.style.display = ''; }
}
async function syncBoard() {
  if (S.board && !document.contains(S.board.el)) disposeBoard();
  if (S.view !== '3d') { if (S.board) disposeBoard(); return; }
  if (!S.board) {
    if (S.boardLoading) return;
    S.boardLoading = true;
    try {
      const { Board3D } = await import('./board3d.js');  // three.js loads only when the 3D view is used
      if (S.view === '3d' && S.mapView && !S.board) {
        S.board = new Board3D($('#mapbox'), S.map, { onSelect: id => { S.selected = id; S.tab = 'region'; renderGame(); $('#panel').scrollTop = 0; }, tooltip });
      }
    } catch (e) {
      console.warn(e); toast('Could not load the 3D view');
      S.view = '2d'; S.boardLoading = false; return renderGame();
    }
    S.boardLoading = false;
  }
  if (!S.board) return;
  S.mapView.svg.style.display = 'none';
  S.mapView.zoomUi.style.display = 'none';
  S.board.paint(colorOf, S.selected, { regions: S.game.regions, partyColor });
}

// ---------- tutorial ----------
function tutorialSteps() {
  const rl = (S.map?.region_label || 'province').toLowerCase(), seats = S.map?.seat_label || 'seats';
  return [
    ['🗳️ Welcome to Election Night', `You lead a political party. Win the most <b>${esc(seats)}</b> across the map. The game lasts <b>${S.room?.rounds ?? 20} rounds</b>, and only ends once every ${rl} has a winner (overtime until then).`],
    ['⚖️ Your ideology matters', `Every ${rl} leans left or right. Campaigning where voters share your ideology works up to <b>3× better</b> than where they don't. Use the <b>My fit</b> map view to see where you're strong.<br><br>If you win a ${rl} that doesn't match your ideology, you lose part of its leadership income.`],
    ['📣 Spend money to campaign', `Click a ${rl} on the map, then tap the <b>+money</b> buttons to plan a rally, invest, or run an attack ad. The <b>Campaign</b> tab has national TV ads and social ads that target one sector. Your plan and remaining budget are always shown.`],
    ['🗳️ Every round is an election', `When everyone ends their turn, every ${rl} votes. The party with the most support <b>leads</b> it, and ${esc(seats)} are shared by vote share. You must beat the grey <b>Independents</b> to win anything. Support fades 10% per round, so keep campaigning.`],
    ['💰 Earning money', `Each round you earn:<br>• a base income<br>• a <b>leadership bonus</b> for every ${rl} you lead<br>• income from <b>${rl} economies</b> and <b>sectors</b> (🌾🏭🚢🏖️💻) you invested in.<br><br>Be the first to invest <b>3× more than everyone else combined</b> in a ${rl} or sector and you <b>own it for the rest of the game</b>: 80% of its income every round, no matter what others do.`],
    ['🃏 Events, leaders & coalitions', `Each round starts with an <b>event card</b> (a sector boom, a scandal, a TV debate…) shown above the scoreboard, so plan around it.<br><br>Your party leader has a <b>strength</b> you picked in the lobby (hover the icons to see everyone's).<br><br>In <b>Diplomacy</b> you can form <b>coalitions</b>: if your coalition holds a majority at the end, you win together. You can also send money to seal deals.`],
    ['🧭 Ideology & government', `Your <b>platform</b> (picked in the lobby) wins or loses voters in certain sectors and with left/right-leaning voters. Voters who share your ideology are <b>loyal</b>; you can <b>reposition</b> the party mid-game, but voters distrust flip-flops for a while.<br><br>After each election the largest bloc forms the <b>government</b> and holds 8 <b>ministries</b> with real bonuses. Coalitions must <b>negotiate</b> who gets which (Diplomacy tab) and ideologically distant partners suffer <b>coalition tension</b>.`],
    ['▶️ Results & next turn', `After each round you'll see the standings and your income. Every player clicks <b>Go to next turn</b> to continue. Good luck!`],
  ];
}
function showTutorial(i) {
  const steps = tutorialSteps();
  if (i < 0 || i >= steps.length) return overlay('tut-ov', null);
  const [title, body] = steps[i];
  const o = overlay('tut-ov', `<div class="card tutorial">
    <div class="muted" style="font-size:12px">How to play · ${i + 1}/${steps.length}</div>
    <h2>${title}</h2><p>${body}</p>
    <div class="dots">${steps.map((_, j) => `<span class="${j === i ? 'on' : ''}"></span>`).join('')}</div>
    <div class="between"><button id="tutskip">Skip</button>
      <span class="row auto">${i ? '<button id="tutprev">Back</button>' : ''}<button class="primary" id="tutnext">${i === steps.length - 1 ? "Let's go!" : 'Next'}</button></span></div></div>`);
  o.style.zIndex = 60;
  $('#tutskip').onclick = () => showTutorial(-1);
  $('#tutprev')?.addEventListener('click', () => showTutorial(i - 1));
  $('#tutnext').onclick = () => showTutorial(i + 1);
}

// ---------- boot ----------
if (isMobile()) S.tab = 'map';
const code = new URLSearchParams(location.search).get('room')?.toUpperCase();
const saved = code && JSON.parse(localStorage.getItem('session:' + code) || 'null');
if (saved) { S.session = saved; connect(); } else renderHome();
