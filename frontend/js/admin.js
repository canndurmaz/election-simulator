const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let token = sessionStorage.getItem('adminToken') || '';
let rooms = [];

function toast(msg, info = false) {
  const d = document.createElement('div');
  d.textContent = msg; if (info) d.className = 'info';
  $('#toast').appendChild(d); setTimeout(() => d.remove(), 3000);
}

async function api(path, method = 'GET') {
  const res = await fetch(path, { method, headers: { 'X-Admin-Token': token } });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { logout(); throw new Error('Wrong admin token'); }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

const idle = s => s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${(s / 3600).toFixed(1)}h`;

function render() {
  $('#count').textContent = `(${rooms.length})`;
  $('#rows').innerHTML = rooms.length ? rooms.map(r => `<tr>
    <td style="text-align:left"><span class="code">${r.code}</span>${r.private ? ' <span class="badge">private</span>' : ''}</td>
    <td style="text-align:left">${esc(r.name)}</td>
    <td>${esc(r.map_id)}</td>
    <td>${r.status}${r.round ? ` · round ${r.round}/${r.rounds}${r.phase === 'results' ? ' (results)' : ''}` : ''}</td>
    <td class="members">${r.online} online / ${r.players}<br>${r.members.map(m => `${m.online ? '🟢' : m.is_bot ? '🤖' : '⚪'} ${esc(m.party)} <span>(${esc(m.name)})</span>`).join('<br>')}</td>
    <td>${idle(r.idle_seconds)}</td>
    <td><button class="small danger" data-del="${r.code}">Delete</button></td></tr>`).join('')
    : '<tr><td colspan="7" class="muted" style="text-align:center">No rooms</td></tr>';
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = () => del([b.dataset.del], true));
}

async function load() {
  try { rooms = await api('/api/admin/rooms'); render(); } catch (e) { toast(e.message); }
}

async function del(codes, ask) {
  if (!codes.length) return toast('Nothing to delete', true);
  if (ask && !confirm(`Delete room${codes.length > 1 ? 's' : ''} ${codes.join(', ')}? Connected players will be sent back to the home screen.`)) return;
  for (const c of codes) {
    try { await api('/api/admin/rooms/' + c, 'DELETE'); } catch (e) { toast(`${c}: ${e.message}`); }
  }
  toast(`Deleted ${codes.length} room${codes.length > 1 ? 's' : ''}`, true);
  load();
}

function logout() {
  token = ''; sessionStorage.removeItem('adminToken');
  $('#panel').classList.add('hidden'); $('#login').classList.remove('hidden');
}

async function login() {
  token = $('#token').value.trim() || token;
  try {
    rooms = await api('/api/admin/rooms');
    sessionStorage.setItem('adminToken', token);
    $('#login').classList.add('hidden'); $('#panel').classList.remove('hidden');
    render();
  } catch (e) { toast(e.message); }
}

$('#go').onclick = login;
$('#token').onkeydown = e => e.key === 'Enter' && login();
$('#refresh').onclick = load;
$('#logout').onclick = logout;
$('#purge').onclick = () => del(rooms.filter(r => r.idle_seconds > 600).map(r => r.code), true);
setInterval(() => { if (token && $('#auto').checked && !$('#panel').classList.contains('hidden')) load(); }, 5000);
if (token) login();
