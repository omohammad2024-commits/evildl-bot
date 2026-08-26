'use strict';
// Downloader dashboard SPA. Vanilla JS, no build step. Talks to /api/*.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const content = $('#content');
const viewTitle = $('#viewTitle');
const TITLES = {
  overview: 'Overview', analytics: 'Analytics', users: 'Users',
  platforms: 'Platforms', cookies: 'Cookies', channels: 'Forced join',
  broadcast: 'Broadcast', logs: 'Live logs', control: 'Control',
};
let current = 'overview';
let logTimer = null, pulseTimer = null;

// ── helpers ──────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const r = await fetch('/api/' + path, opts);
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  const ct = r.headers.get('content-type') || '';
  const body = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw Object.assign(new Error('http'), { status: r.status, body });
  return body;
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function toast(msg, kind = '') {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast show ' + kind;
  setTimeout(() => t.className = 'toast', 2600);
}
function card(inner, cls = '') { return `<div class="card ${cls}">${inner}</div>`; }
function stat(label, val, sub = '') {
  return card(`<div class="lbl">${label}</div><div class="val">${esc(val)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}`, 'stat');
}
function barChart(rows, keyFn, valFn) {
  const peak = Math.max(1, ...rows.map(valFn));
  return rows.map(r => {
    const v = valFn(r), pct = Math.round(v / peak * 100);
    return `<div class="bar-row"><span class="k">${esc(keyFn(r))}</span><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="n">${v}</span></div>`;
  }).join('');
}

// ── views ────────────────────────────────────────────────────────────
const views = {
  async overview() {
    const o = await api('overview');
    const tunnel = o.tunnel_url ? `<div class="card" style="grid-column:1/-1;display:flex;align-items:center;gap:12px">
        <span>🌐</span><span class="hint" style="margin:0">Public URL:</span>
        <a href="${esc(o.tunnel_url)}" target="_blank" style="color:var(--acc);word-break:break-all">${esc(o.tunnel_url)}</a>
      </div>` : '';
    return `<div class="grid g4">
      ${tunnel}
      ${stat('👥 Users', o.users, `+${o.new_today} today`)}
      ${stat('⬇️ Downloads', o.downloads_total, `${o.downloads_today} today`)}
      ${stat('🔥 Active 24h', o.active_24h)}
      ${stat('💾 Total data', o.total_bytes)}
      ${stat('📅 This week', o.downloads_week)}
      ${stat('⚡️ Cache', o.cache_rows, `${o.cache_hits} hits`)}
      ${stat('❌ Failed', o.failed_total)}
      ${stat('🖴 Free disk', o.disk_free)}
    </div>
    <div class="grid g2 mt16">
      <div id="svcCard">${card('<h3>🔧 Services</h3><div class="loading">…</div>')}</div>
      <div id="ckCard">${card('<h3>🍪 Cookies</h3><div class="loading">…</div>')}</div>
    </div>`;
  },

  async analytics() {
    const c = await api('charts');
    const dl = card(`<h3>⬇️ Downloads · 14 days</h3>${barChart(c.daily, r => r.label, r => r.count)}`);
    const nu = card(`<h3>🆕 New users · 14 days</h3>${barChart(c.new_users, r => r.label, r => r.count)}`);
    const pf = card(`<h3>🏆 Top platforms</h3>${c.platforms.length ? barChart(c.platforms, r => r.platform, r => r.count || r.total || 0) : '<div class="empty">No data</div>'}`);
    const peak = card(`<h3>🕐 Peak hours</h3>${barChart(c.peak_hours.filter(h => h.hour % 2 === 0), r => r.hour + ':00', r => r.count)}`);
    return `<div class="grid g2">${dl}${nu}</div><div class="grid g2 mt16">${pf}${peak}</div>`;
  },

  async users() {
    const d = await api('users?limit=40');
    const rows = d.users.map(u => `<tr>
      <td class="mono">${u.user_id}</td>
      <td>${esc(u.first_name || '')} ${u.username ? '@' + esc(u.username) : ''}</td>
      <td>${u.total_downloads || 0}</td>
      <td class="row">
        <button class="btn btn-sm" onclick="viewUser(${u.user_id})">View</button>
        <button class="btn btn-sm btn-d" onclick="banUser(${u.user_id})">Ban</button>
      </td></tr>`).join('');
    return card(`<h3>👥 Top users</h3><table><thead><tr><th>ID</th><th>Name</th><th>Downloads</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan=4 class=empty>No users</td></tr>'}</tbody></table>`)
      + `<div id="userDetail" class="mt16"></div>`;
  },

  async platforms() {
    const d = await api('platforms');
    const rows = d.platforms.map(p => `<div class="svc">
      <span class="nm">${esc(p.platform)}</span>
      <label class="sw"><input type="checkbox" ${p.enabled ? 'checked' : ''} onchange="togglePlatform('${esc(p.platform)}')"><span></span></label>
    </div>`).join('');
    return card(`<h3>🎛 Platform control</h3><div class="hint">Turn a platform off and users are silently told it's temporarily unavailable — no restart needed.</div>${rows}`);
  },

  async cookies() {
    const d = await api('cookies');
    const plats = ['youtube', 'instagram', 'tiktok', 'twitter'];
    const cards = plats.map(p => {
      const c = d.cookies[p] || {};
      let badge = '<span class="badge b-warn">not installed</span>';
      if (c.present && c.ok) {
        const days = c.days_left || 0;
        const cls = days <= 3 ? 'b-bad' : days <= 7 ? 'b-warn' : 'b-ok';
        badge = `<span class="badge ${cls}">${days}d left · ${c.count} cookies</span>`;
      } else if (c.present) badge = `<span class="badge b-bad">${esc(c.reason || 'invalid')}</span>`;
      return card(`<h3>🍪 ${esc(p)}</h3><div class="mb16">${badge}</div>
        <div class="row">
          <input type="file" id="ck_${p}" accept=".txt" class="txt" style="padding:8px">
        </div>
        <div class="row mt8">
          <button class="btn btn-p btn-sm" onclick="uploadCookie('${p}')">Upload</button>
          ${c.present ? `<button class="btn btn-sm btn-d" onclick="deleteCookie('${p}')">Delete</button>` : ''}
        </div>`);
    }).join('');
    return `<div class="grid g2">${cards}</div>`;
  },

  async channels() {
    const d = await api('channels');
    const rows = (d.channels || []).map(c => `<tr>
      <td>${esc(c.title || c.handle || c.id)}</td>
      <td class="mono">${esc(c.handle || '')}</td>
      <td class="mono">${esc(c.id)}</td>
      <td><button class="btn btn-sm btn-d" onclick="removeChannel('${esc(c.id)}')">Remove</button></td>
    </tr>`).join('');
    const status = d.enabled ? '<span class="badge b-ok">enabled</span>' : '<span class="badge b-warn">off</span>';
    return card(`<h3>🔒 Forced-join channels ${status}</h3>
      <div class="hint">The bot must be an admin of a channel before it can gate on it — the server verifies this when you add one.</div>
      <table><thead><tr><th>Title</th><th>Handle</th><th>ID</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan=4 class=empty>No channels — the gate is off</td></tr>'}</tbody></table>`)
      + card(`<h3>➕ Add channel</h3><div class="row"><input class="txt" id="chIn" placeholder="@channel or -100...">
        <button class="btn btn-p" onclick="addChannel()">Verify & add</button></div>`, 'mt16');
  },

  async broadcast() {
    return card(`<h3>📢 Broadcast a message</h3>
      <div class="hint">Sent as plain text, slowly, in the background. Choose the audience below.</div>
      <textarea class="txt" id="bcText" placeholder="Your message to users…"></textarea>
      <div class="row mt16">
        <select class="txt" id="bcAud" style="max-width:220px">
          <option value="all">All users</option>
          <option value="active">Active users (7 days)</option>
        </select>
        <div class="spacer"></div>
        <button class="btn btn-p" onclick="sendBroadcast()">Send broadcast →</button>
      </div>`);
  },

  async logs() {
    if (logTimer) clearInterval(logTimer);
    setTimeout(() => {
      refreshLogs();
      logTimer = setInterval(refreshLogs, 4000);
    }, 30);
    return card(`<h3>📜 Live bot log <span class="hint" style="margin-left:auto">auto-refresh 4s</span></h3><div class="logbox" id="logbox"><div class="loading">…</div></div>`);
  },

  async control() {
    const l = await api('limits');
    const maint = l.maintenance;
    return `<div class="grid g2">
      ${card(`<h3>⚙️ Limits</h3>
        <div class="svc"><span class="nm">Global concurrent</span><span class="dt">${l.concurrent}</span></div>
        <div class="svc"><span class="nm">Per-user concurrent</span><span class="dt">${l.per_user}</span></div>
        <div class="svc"><span class="nm">Soft hourly threshold</span><span class="dt">${l.soft_hourly}</span></div>
        <div class="svc"><span class="nm">🔧 Maintenance mode</span>
          <label class="sw"><input type="checkbox" ${maint ? 'checked' : ''} onchange="toggleMaintenance()"><span></span></label>
        </div>`)}
      ${card(`<h3>🛠 Actions</h3>
        <div class="row mt8"><button class="btn" onclick="doAction('cache/clear','Cache cleared')">🗑 Clear cache</button></div>
        <div class="row mt8"><button class="btn" onclick="doAction('cleanup','Cleanup done')">🧹 Storage cleanup</button></div>
        <div class="row mt8"><a class="btn" href="/api/export/users" target="_blank">📄 Export users CSV</a></div>
        <div class="row mt16"><button class="btn btn-d" onclick="restartBot()">🔁 Restart bot</button></div>`)}
    </div>`;
  },
};

// ── post-render hooks (fill async sub-cards) ─────────────────────────
async function afterRender(view) {
  if (view === 'overview') { fillServices('svcCard'); fillCookiesMini('ckCard'); }
}
async function fillServices(id) {
  try {
    const d = await api('services');
    const rows = d.services.map(s => `<div class="svc"><span class="nm">${s.icon} ${esc(s.name)}</span><span class="dt">${esc(s.detail)}</span></div>`).join('');
    $('#' + id).innerHTML = card('<h3>🔧 Services</h3>' + rows);
  } catch (_) {}
}
async function fillCookiesMini(id) {
  try {
    const d = await api('services');
    const rows = d.cookies.map(c => `<div class="svc"><span class="nm">${c.icon} ${esc(c.platform)}</span><span class="dt">${esc(c.detail)}</span></div>`).join('');
    $('#' + id).innerHTML = card('<h3>🍪 Cookies</h3>' + (rows || '<div class="empty">none</div>'));
  } catch (_) {}
}
async function refreshLogs() {
  try {
    const d = await api('logs?lines=150');
    const box = $('#logbox'); if (!box) return;
    box.innerHTML = d.lines.map(l => {
      const low = l.toLowerCase();
      const cls = /error|traceback|exception/.test(low) ? 'err' : /warning|warn/.test(low) ? 'warn' : /online|ready|success/.test(low) ? 'ok' : '';
      return `<div class="ln ${cls}">${esc(l)}</div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  } catch (_) {}
}

// ── actions (global for inline onclick) ──────────────────────────────
window.viewUser = async id => {
  try {
    const d = await api('user/' + id);
    const u = d.user;
    const recent = d.recent.map(r => `<tr><td>${esc(r.platform)}</td><td class="mono">${esc((r.url || '').slice(0, 60))}</td></tr>`).join('');
    $('#userDetail').innerHTML = card(`<h3>👤 ${esc(u.first_name || '')} ${u.username ? '@' + esc(u.username) : ''} <span class="hint" style="margin-left:auto">${d.banned ? '<span class=\"badge b-bad\">banned</span>' : ''}</span></h3>
      <div class="grid g4 mb16">
        ${stat('Total', d.stats.total)}${stat('This week', d.stats.week)}${stat('Top', d.stats.top_platform || '—')}${stat('Invited', d.referrals)}
      </div>
      <div class="row mb16">
        ${d.banned ? `<button class="btn btn-p btn-sm" onclick="unbanUser(${id})">Unban</button>` : `<button class="btn btn-d btn-sm" onclick="banUser(${id})">Ban</button>`}
      </div>
      <table><thead><tr><th>Platform</th><th>URL</th></tr></thead><tbody>${recent || '<tr><td colspan=2 class=empty>No recent downloads</td></tr>'}</tbody></table>`);
  } catch (e) { toast('User not found', 'bad'); }
};
window.banUser = async id => {
  const hours = prompt('Ban for how many hours? (0 or empty = permanent)', '0');
  if (hours === null) return;
  try {
    await api('user/' + id + '/ban', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ hours: parseInt(hours) || 0 }) });
    toast('User banned', 'ok'); if (current === 'users') load('users');
  } catch (_) { toast('Ban failed', 'bad'); }
};
window.unbanUser = async id => {
  try { await api('user/' + id + '/unban', { method: 'POST' }); toast('User unbanned', 'ok'); if (current === 'users') load('users'); }
  catch (_) { toast('Unban failed', 'bad'); }
};
window.togglePlatform = async p => {
  try { const d = await api('platforms/' + p + '/toggle', { method: 'POST' }); toast(`${p} ${d.enabled ? 'enabled' : 'disabled'}`, 'ok'); }
  catch (_) { toast('Toggle failed', 'bad'); }
};
window.toggleMaintenance = async () => {
  try { const d = await api('maintenance/toggle', { method: 'POST' }); toast('Maintenance ' + (d.maintenance ? 'ON' : 'OFF'), 'ok'); }
  catch (_) { toast('Failed', 'bad'); }
};
window.uploadCookie = async p => {
  const f = $('#ck_' + p).files[0];
  if (!f) { toast('Pick a .txt file first', 'bad'); return; }
  const fd = new FormData(); fd.append('file', f);
  try {
    const d = await api('cookies/' + p, { method: 'POST', body: fd });
    if (d.ok) { toast('Cookies installed for ' + p, 'ok'); load('cookies'); }
  } catch (e) { toast('Rejected: ' + (e.body && e.body.error || 'invalid'), 'bad'); }
};
window.deleteCookie = async p => {
  if (!confirm('Delete ' + p + ' cookies?')) return;
  try { await api('cookies/' + p, { method: 'DELETE' }); toast('Deleted', 'ok'); load('cookies'); }
  catch (_) { toast('Failed', 'bad'); }
};
window.addChannel = async () => {
  const v = $('#chIn').value.trim(); if (!v) return;
  try {
    const d = await api('channels', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ channel: v }) });
    if (d.ok) { toast('Channel added', 'ok'); load('channels'); }
  } catch (e) { toast('Rejected: ' + (e.body && e.body.detail || e.body && e.body.error || 'not usable'), 'bad'); }
};
window.removeChannel = async id => {
  if (!confirm('Remove this channel from the gate?')) return;
  try { await api('channels/' + id, { method: 'DELETE' }); toast('Removed', 'ok'); load('channels'); }
  catch (_) { toast('Failed', 'bad'); }
};
window.sendBroadcast = async () => {
  const text = $('#bcText').value.trim(), audience = $('#bcAud').value;
  if (!text) { toast('Type a message', 'bad'); return; }
  if (!confirm('Send this broadcast to ' + (audience === 'active' ? 'active users' : 'ALL users') + '?')) return;
  try { const d = await api('broadcast', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text, audience }) }); toast('Queued to ' + d.queued + ' users', 'ok'); $('#bcText').value = ''; }
  catch (_) { toast('Broadcast failed', 'bad'); }
};
window.doAction = async (path, msg) => {
  try { await api(path, { method: 'POST' }); toast(msg, 'ok'); }
  catch (_) { toast('Failed', 'bad'); }
};
window.restartBot = async () => {
  if (!confirm('Restart the bot? It will drop for a few seconds.')) return;
  toast('Restarting bot…');
  try { const d = await api('restart-bot', { method: 'POST' }); toast(d.ok ? 'Bot restarted' : 'Restart failed', d.ok ? 'ok' : 'bad'); }
  catch (_) { toast('Restart failed', 'bad'); }
};

// ── router ───────────────────────────────────────────────────────────
async function load(view) {
  current = view;
  if (logTimer && view !== 'logs') { clearInterval(logTimer); logTimer = null; }
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.view === view));
  $$('#mobnav a').forEach(a => a.classList.toggle('active', a.dataset.view === view));
  viewTitle.textContent = TITLES[view] || view;
  content.innerHTML = '<div class="loading">Loading…</div>';
  try {
    content.innerHTML = await views[view]();
    afterRender(view);
  } catch (e) {
    if (e.message === 'auth') return;
    content.innerHTML = `<div class="card"><div class="empty">Failed to load: ${esc(e.message)}</div></div>`;
  }
}

async function refreshPulse() {
  try {
    const o = await api('overview');
    $('#uptime').textContent = '⏱ ' + o.uptime;
    $('#pulse').textContent = '● online · ' + o.users + ' users';
    $('#pulse').style.color = 'var(--ok)';
  } catch (_) {
    $('#pulse').textContent = '● offline';
    $('#pulse').style.color = 'var(--bad)';
  }
}

$$('#nav a').forEach(a => a.addEventListener('click', () => load(a.dataset.view)));
$$('#mobnav a').forEach(a => a.addEventListener('click', () => load(a.dataset.view)));
$('#refreshBtn').addEventListener('click', () => load(current));
load('overview');
refreshPulse();
pulseTimer = setInterval(refreshPulse, 15000);
