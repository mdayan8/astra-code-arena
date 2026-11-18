// Shared helpers for both portals. Every dynamic value goes through esc() before reaching innerHTML.
const S = { csrf: '', tz: 'Asia/Kolkata', info: null, base: '' };
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(method, url, body, opts = {}) {
  const h = {};
  if (method !== 'GET') h['X-CSRF-Token'] = S.csrf;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { h['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let r;
  try { r = await fetch(url, { method, headers: h, body: payload, credentials: 'same-origin' }); }
  catch (e) { throw new Error('Cannot reach the server. Check your network connection.'); }
  if (opts.raw) return r;
  let data = null;
  try { data = await r.json(); } catch (e) { /* non-json */ }
  if (!r.ok) {
    let msg = data?.error || data?.detail || `Request failed (${r.status})`;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(-1)[0] || ''}: ${d.msg}`).join('; ');
    const err = new Error(msg); err.status = r.status;
    if (r.status === 401 && S.onUnauthorized) S.onUnauthorized();
    throw err;
  }
  return data;
}
const GET = (u) => api('GET', u), POST = (u, b = {}) => api('POST', u, b), PUT = (u, b) => api('PUT', u, b), DEL = (u) => api('DELETE', u);

function toast(msg, type = '') {
  let box = $('#toasts');
  if (!box) { box = document.createElement('div'); box.id = 'toasts'; box.setAttribute('role', 'status'); document.body.appendChild(box); }
  const t = document.createElement('div');
  t.className = `toast ${type}`; t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => t.remove(), type === 'err' ? 6500 : 3800);
}
const fail = (e) => toast(e.message || String(e), 'err');

function modal({ title, body = '', actions = [], size = '', onOpen, dismissable = true }) {
  const ov = document.createElement('div');
  ov.className = 'overlay';
  ov.innerHTML = `<div class="modal ${size}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="mh"><h3>${esc(title)}</h3>
    <button class="btn ghost sm icon-btn" data-x aria-label="Close">${ico('x', 16)}</button></div><div class="mb">${body}</div>
    ${actions.length ? `<div class="mf">${actions.map((a, i) => `<button class="btn ${a.kind || ''}" data-a="${i}">${esc(a.label)}</button>`).join('')}</div>` : ''}</div>`;
  const close = () => { ov.remove(); document.removeEventListener('keydown', key); };
  const key = (e) => { if (e.key === 'Escape' && dismissable) close(); };
  document.addEventListener('keydown', key);
  ov.addEventListener('mousedown', (e) => { if (e.target === ov && dismissable) close(); });
  $('[data-x]', ov).onclick = close;
  actions.forEach((a, i) => {
    const b = $(`[data-a="${i}"]`, ov);
    b.onclick = async () => {
      if (!a.onClick) return close();
      const old = b.innerHTML; b.disabled = true; b.innerHTML = '<span class="spin"></span>';
      try { if ((await a.onClick(ov, close)) !== false) close(); } catch (e) { fail(e); }
      finally { if (b.isConnected) { b.disabled = false; b.innerHTML = old; } }
    };
  });
  document.body.appendChild(ov);
  const f = $('input, textarea, select', $('.mb', ov)); if (f) setTimeout(() => f.focus(), 30);
  if (onOpen) onOpen(ov, close);
  return { el: ov, close };
}
function confirmBox(text, { title = 'Please confirm', ok = 'Confirm', danger = false } = {}) {
  return new Promise((res) => {
    const m = modal({ title, body: `<p>${esc(text)}</p>`, actions: [{ label: 'Cancel', onClick: () => res(false) }, { label: ok, kind: danger ? 'danger solid' : 'primary', onClick: () => res(true) }] });
    $('[data-x]', m.el).addEventListener('click', () => res(false));
  });
}
function secretBox(title, text, secret) {
  modal({ title, body: `<p>${esc(text)}</p><pre class="mono" style="font-size:16px">${esc(secret)}</pre><p class="small muted">Shown only once. Copy it now.</p>`,
    actions: [{ label: 'Copy', onClick: () => { navigator.clipboard?.writeText(secret); toast('Copied', 'ok'); return false; } }, { label: 'Done', kind: 'primary' }] });
}

// ---- forms ----
function formData(el) {
  const o = {};
  $$('[name]', el).forEach((i) => {
    const n = i.name;
    if (i.type === 'checkbox') {
      if (i.dataset.multi) { o[n] = o[n] || []; if (i.checked) o[n].push(i.dataset.num ? Number(i.value) : i.value); }
      else o[n] = i.checked;
    } else if (i.type === 'number' || i.dataset.num) o[n] = i.value === '' ? null : Number(i.value);
    else o[n] = i.value;
  });
  return o;
}
async function busy(btn, fn) {
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  try { return await fn(); } catch (e) { fail(e); } finally { btn.disabled = false; btn.innerHTML = old; }
}

// ---- time ----
const _fmt = {};
function fmtX(t, opts) {
  if (!t) return '—';
  const k = JSON.stringify(opts);
  _fmt[k] = _fmt[k] || new Intl.DateTimeFormat('en-IN', { timeZone: S.tz, ...opts });
  return _fmt[k].format(new Date(t * 1000));
}
const fmtTime = (t) => fmtX(t, { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
const fmtDate = (t) => fmtX(t, { day: 'numeric', month: 'short', year: 'numeric' });
const fmtClock = (t) => fmtX(t, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
function fmtDur(sec) {
  if (sec == null) return '—';
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return `${h ? h + ':' : ''}${String(m).padStart(h ? 2 : 1, '0')}:${String(s).padStart(2, '0')}`;
}
function fmtMins(m) { m = Number(m || 0); return m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? ' ' + (m % 60) + 'm' : ''}` : `${m} min`; }
function ago(t) {
  const d = Math.floor(Date.now() / 1000) - t;
  if (d < 60) return 'just now'; if (d < 3600) return `${Math.floor(d / 60)}m ago`; if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return fmtDate(t);
}
function toLocalInput(t) { // epoch -> 'YYYY-MM-DDTHH:MM' in the configured timezone (for <input type=datetime-local>)
  if (!t) return '';
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: S.tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
    .formatToParts(new Date(t * 1000)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour === '24' ? '00' : p.hour}:${p.minute}`;
}

// ---- tiny markdown (escape first, then a few safe patterns) ----
function md(src) {
  let s = esc(src || '').replace(/\r\n/g, '\n');
  const blocks = [];
  s = s.replace(/```[a-z]*\n?([\s\S]*?)```/g, (_, c) => { blocks.push(`<pre><code>${c.replace(/\n$/, '')}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>');
  s = s.split(/\n{2,}/).map((p) => {
    if (/^\u0000\d+\u0000$/.test(p.trim())) return p.trim();
    if (/^(\s*[-*] .*\n?)+$/.test(p)) return '<ul>' + p.trim().split('\n').map((l) => `<li>${l.replace(/^\s*[-*] /, '')}</li>`).join('') + '</ul>';
    return `<p>${p.replace(/\n/g, '<br>')}</p>`;
  }).join('');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
}

// ---- status badges ----
const STATUS_CLS = { Live: 'green live', Paused: 'amber', 'Registration Open': 'violet', Upcoming: 'blue', 'Registration Closed': 'blue', Completed: '', Archived: '', Draft: '', Cancelled: 'red' };
const badge = (s) => `<span class="badge dot ${STATUS_CLS[s] ?? ''}">${esc(s)}</span>`;
const VERDICT_CLS = { AC: 'green', WA: 'red', CE: 'amber', RE: 'red', TLE: 'red', MLE: 'red', OLE: 'red', IE: 'amber', OK: 'blue' };
const VERDICT_NAME = { AC: 'Accepted', WA: 'Wrong Answer', CE: 'Compilation Error', RE: 'Runtime Error', TLE: 'Time Limit Exceeded', MLE: 'Memory Limit Exceeded', OLE: 'Output Limit Exceeded', IE: 'Internal Judge Error', OK: 'Ran' };
function vbadge(v, status) {
  if (!v || status === 'queued' || status === 'running') return `<span class="badge blue"><span class="spin" style="width:10px;height:10px;border-width:2px"></span> ${status === 'running' ? 'Judging' : 'Pending'}</span>`;
  return `<span class="badge ${VERDICT_CLS[v] || ''}">${esc(VERDICT_NAME[v] || v)}</span>`;
}
const LANG_SHORT = { c: 'C', cpp: 'C++', python: 'Python', java: 'Java', javascript: 'JavaScript', go: 'Go', rust: 'Rust' };
const langName = (l) => LANG_SHORT[l] || l;
const initials = (n) => (n || '?').split(/\s+/).map((x) => x[0]).slice(0, 2).join('').toUpperCase();
const rankBadge = (r, big) => `<span class="rank ${r <= 3 ? 'r' + r : ''} ${big ? 'big' : ''}">${r <= 3 && big ? ico('crown', 14) : ''}${r}</span>`;
const avatar = (name, cls = '', url = null) => { let h = 0; for (const ch of name || '') h = (h * 31 + ch.charCodeAt(0)) % 360; return url ? `<span class="avatar ${cls} has-img" style="--h:${h}"><img src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">${esc(initials(name))}</span>` : `<span class="avatar ${cls}" style="--h:${h}">${esc(initials(name))}</span>`; };
const skeleton = (n = 3) => `<div class="stack">${Array.from({ length: n }, (_, i) => `<div class="skel" style="width:${90 - i * 12}%"></div>`).join('')}</div>`;
const empty = (t, s = '', icon = 'layers') => `<div class="empty"><div class="emptyi">${typeof ico === 'function' ? ico(icon, 24) : ''}</div><div class="t">${esc(t)}</div>${s ? `<div class="small">${s}</div>` : ''}</div>`;

// ---- router ----
function router(routes, notFound) {
  const go = async () => {
    const path = location.hash.slice(1) || '/';
    for (const [pat, fn] of routes) {
      const keys = [];
      const re = new RegExp('^' + pat.replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '/?$');
      const m = path.split('?')[0].match(re);
      if (m) {
        const params = Object.fromEntries(keys.map((k, i) => [k, decodeURIComponent(m[i + 1])]));
        params.query = Object.fromEntries(new URLSearchParams(path.split('?')[1] || ''));
        try { await fn(params); } catch (e) { if (e.status !== 401) fail(e); }
        return;
      }
    }
    notFound();
  };
  window.addEventListener('hashchange', go);
  return go;
}
const nav = (h) => { if (location.hash === '#' + h) window.dispatchEvent(new HashChangeEvent('hashchange')); else location.hash = h; };

// ---- Monaco (served locally, works offline) ----
let _monaco;
function loadMonaco() {
  if (_monaco) return _monaco;
  _monaco = new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/vs/loader.js';
    s.onload = () => {
      window.require.config({ paths: { vs: '/static/vendor/vs' } });
      window.MonacoEnvironment = { getWorkerUrl: () => '/static/vendor/monaco-worker.js' };
      window.require(['vs/editor/editor.main'], () => {
        monaco.editor.defineTheme('astra-light', { base: 'vs', inherit: true, rules: [], colors: { 'editor.background': '#ffffff', 'editorLineNumber.foreground': '#b4b4c2', 'editor.lineHighlightBackground': '#f7f5fe' } });
        monaco.editor.defineTheme('astra-dark', { base: 'vs-dark', inherit: true, rules: [], colors: { 'editor.background': '#16161d', 'editor.lineHighlightBackground': '#1f1c2b' } });
        res(window.monaco);
      }, rej);
    };
    s.onerror = () => rej(new Error('Editor failed to load'));
    document.head.appendChild(s);
  });
  return _monaco;
}
function codeEditor(el, { value = '', language = 'c', readOnly = false, theme = 'astra-light', fontSize = 14, minimap = false } = {}) {
  return loadMonaco().then((m) => m.editor.create(el, {
    value, language, readOnly, theme, fontSize, automaticLayout: true, minimap: { enabled: minimap }, scrollBeyondLastLine: false,
    fontFamily: 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace', tabSize: 4, insertSpaces: true, renderWhitespace: 'selection',
    padding: { top: 10 }, smoothScrolling: true, bracketPairColorization: { enabled: true }, wordWrap: 'off', stickyScroll: { enabled: false },
  }));
}
function download(name, text, type = 'text/csv') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function toCSV(rows) { return rows.map((r) => r.map((x) => { x = x == null ? '' : String(x); return /[",\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; }).join(',')).join('\n'); }
const LOGO = '/static/img/arena-logo.svg';

// ---- icon set (stroke SVG, 24x24). No emojis anywhere in the UI. ----
const ICONS = {
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z', menu: 'M4 6h16M4 12h16M4 18h16',
  heart: 'M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z',
  calendar: 'M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 2v4M8 2v4M3 10h18', clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h8', check: 'M20 6 9 17l-5-5',
  trophy: 'M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18M4 22h16M10 14.7V17c0 .6-.5 1-1 1.2C7.9 18.8 7 20.2 7 22M14 14.7V17c0 .6.5 1 1 1.2 1.2.6 2 2 2 3.8M18 2H6v7a6 6 0 0 0 12 0z',
  flame: 'M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3.3.4 1.7 1.3 2.8 2.5 2.8z',
  medal: 'M12 22a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM8.2 11.2 5 2h4l3 6 3-6h4l-3.2 9.2M12 14.5v4', dot: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  circle: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', pencil: 'M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z',
  star: 'M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z', chart: 'M3 3v18h18M18 17V9M13 17V5M8 17v-3',
  bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0', maximize: 'M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3',
  rotate: 'M3 12a9 9 0 1 0 9-9 9.8 9.8 0 0 0-6.7 2.7L3 8M3 3v5h5', cpu: 'M6 6h12v12H6zM9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4',
  pause: 'M7 4h3v16H7zM14 4h3v16h-3z', megaphone: 'M3 11v2a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1zM16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14', x: 'M18 6 6 18M6 6l12 12',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2',
  sparkles: 'M9.9 3.6 11 7.3a2 2 0 0 0 1.4 1.4l3.6 1.1-3.6 1.1a2 2 0 0 0-1.4 1.4L9.9 15.9l-1.1-3.6a2 2 0 0 0-1.4-1.4L3.8 9.8l3.6-1.1a2 2 0 0 0 1.4-1.4zM19 3v4M21 5h-4M17 17v3M18.5 18.5h-3',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01', external: 'M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
  left: 'M19 12H5M12 19l-7-7 7-7', right: 'M5 12h14M12 5l7 7-7 7', up: 'M12 19V5M5 12l7-7 7 7',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8', user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  code: 'M16 18l6-6-6-6M8 6l-6 6 6 6', zap: 'M13 2 4 14h7l-1 8 9-12h-7z', target: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18', shield: 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
  flag: 'M5 21V4M5 4h11l-2 4 2 4H5', crown: 'M3 8l4 4 5-7 5 7 4-4-2 11H5z', play: 'M7 4l13 8-13 8z', send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4', wifi: 'M5 13a10 10 0 0 1 14 0M8.5 16.4a5 5 0 0 1 7 0M2 8.8a15 15 0 0 1 20 0M12 20h.01', wifiOff: 'M2 2l20 20M8.5 16.4a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.7M2 8.8a15 15 0 0 1 4.2-2.5M12 20h.01',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', bot: 'M12 8V4H8M4 8h16v12H4zM2 14h2M20 14h2M15 13v2M9 13v2',
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5', award: 'M12 15a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM8.2 13.9 7 23l5-3 5 3-1.2-9.1',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9', trending: 'M22 7 13.5 15.5 8.5 10.5 2 17M16 7h6v6',
  bulb: 'M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z', plus: 'M12 5v14M5 12h14', download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3', upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  copy: 'M8 8h12v12H8zM16 8V4H4v12h4', grid: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z', layers: 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  check2: 'M22 11v1a10 10 0 1 1-6-9.2M22 4 12 14l-3-3', terminal: 'M4 17l6-6-6-6M12 19h8', wand: 'M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M17.8 6.2 19 5M3 21l9-9M12.2 6.2 11 5',
};
const FILLED_ICONS = new Set(['heart', 'dot', 'play', 'star']);
function ico(n, size = 16, cls = '') {
  const d = ICONS[n] || ICONS.circle;
  const f = FILLED_ICONS.has(n);
  return `<svg class="ico ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="${f ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="${f ? 0 : 2}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
}

// ---- celebrations (tiny canvas confetti; respects reduced-motion) ----
function confetti(ms = 2200) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const cv = document.createElement('canvas');
  cv.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:300';
  cv.width = innerWidth; cv.height = innerHeight; document.body.appendChild(cv);
  const ctx = cv.getContext('2d'), cols = ['#7c3aed', '#a78bfa', '#facc15', '#22c55e', '#38bdf8', '#f472b6', '#fb923c'];
  const ps = Array.from({ length: 160 }, () => ({ x: innerWidth / 2 + (Math.random() - .5) * 200, y: innerHeight * .35, vx: (Math.random() - .5) * 14, vy: -Math.random() * 14 - 4,
    s: 5 + Math.random() * 6, r: Math.random() * 6, vr: (Math.random() - .5) * .3, c: cols[Math.floor(Math.random() * cols.length)] }));
  const t0 = performance.now();
  const step = (t) => {
    ctx.clearRect(0, 0, cv.width, cv.height);
    ps.forEach((p) => { p.vy += .35; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.r += p.vr; ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); ctx.restore(); });
    if (t - t0 < ms) requestAnimationFrame(step); else cv.remove();
  };
  requestAnimationFrame(step);
}
function celebrate({ icon = 'trophy', title, text = '', tone = 'violet', action }) {
  confetti();
  modal({ title: 'Celebration', body: `<div class="celebrate"><div class="cel-badge cel-${tone}">${ico(icon, 34)}</div><h2>${esc(title)}</h2><p class="muted">${text}</p></div>`,
    actions: [...(action ? [{ label: action.label, kind: 'primary', onClick: action.onClick }] : []), { label: 'Awesome', kind: action ? '' : 'primary' }] });
}
const LEVELS = [0, 50, 120, 220, 350, 520, 740, 1000, 1320, 1700, 2150, 2700];
const LEVEL_NAMES = ['Newbie', 'Explorer', 'Coder', 'Solver', 'Builder', 'Hacker', 'Specialist', 'Expert', 'Master', 'Grandmaster', 'Legend', 'Astra'];
function levelOf(xp) {
  let i = 0; while (i + 1 < LEVELS.length && xp >= LEVELS[i + 1]) i++;
  const next = LEVELS[i + 1] ?? LEVELS[i] + 600;
  return { level: i + 1, name: LEVEL_NAMES[i], xp, from: LEVELS[i], to: next, pct: Math.min(100, (xp - LEVELS[i]) / (next - LEVELS[i]) * 100) };
}
