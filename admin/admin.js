// ASTRA Control Centre. Served only from the private admin route; every API call is re-authorised server-side.
const root = $('#root');
const A = '/api/admin';
let ME = null, META = null, timers = [];
const every = (fn, ms) => { const t = setInterval(fn, ms); timers.push(t); return t; };
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };
S.onUnauthorized = () => { ME = null; loginScreen(); };

const ICON = {
  dashboard: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z', competitions: 'M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3',
  questions: 'M4 4h16v16H4zM8 8h8M8 12h8M8 16h5', practice: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM10 8l6 4-6 4z', pdf: 'M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h6', ai: 'M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z',
  tests: 'M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9', participants: 'M16 21v-2a4 4 0 0 0-8 0v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M2 21v-2a4 4 0 0 1 3-3.9',
  submissions: 'M8 9l-4 3 4 3M16 9l4 3-4 3M14 5l-4 14', leaderboards: 'M6 20V10M12 20V4M18 20v-7', semesters: 'M3 5h18v16H3zM3 10h18M8 3v4M16 3v4',
  reports: 'M12 3v12M7 10l5 5 5-5M4 19h16', admins: 'M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z', audit: 'M12 8v4l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18', database: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6', settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2',
};
const MOD = { dashboard: 'Dashboard', competitions: 'Competitions', questions: 'Question Bank', practice: 'Practice', pdf: 'Question Import', ai: 'AI Studio', tests: 'Test Cases', participants: 'Participants',
  submissions: 'Submissions', leaderboards: 'Leaderboards', semesters: 'Semesters', reports: 'Reports', admins: 'Admin Users', audit: 'Audit Logs', database: 'Database', settings: 'System Settings' };
// ---------- competition assistant (chat) ----------
// Reference document for AI writing (PDF, Word, PowerPoint, text, image). Returns a small UI bound to `holder.ref`.
function refPicker(holder) {
  const id = 'rf' + Math.random().toString(36).slice(2, 8);
  const html = `<label class="btn sm refbtn" for="${id}" title="PDF, Word, PowerPoint, text or an image of notes or an old question paper">${ico('upload', 13)} Attach reference</label>
    <input type="file" id="${id}" hidden accept=".pdf,.docx,.odt,.pptx,.rtf,.txt,.md,.html,.htm,.c,.cpp,.py,.java,.png,.jpg,.jpeg,.webp"><span class="refchip" data-chip="${id}"></span>`;
  const bind = (root) => {
    const inp = $('#' + id, root), chip = $(`[data-chip="${id}"]`, root);
    const draw = () => { chip.innerHTML = holder.ref ? `<span class="chip">${ico('file', 12)} ${esc(holder.refName)} · ${holder.refChars >= 1000 ? Math.round(holder.refChars / 1000) + 'k' : holder.refChars} characters read <button type="button" class="linkbtn" data-unref>${ico('x', 11)}</button></span>` : '';
      $('[data-unref]', chip)?.addEventListener('click', () => { holder.ref = ''; draw(); }); };
    inp.onchange = async () => { const f = inp.files[0]; if (!f) return; const fd = new FormData(); fd.append('file', f);
      chip.innerHTML = '<span class="small muted"><span class="spin" style="width:11px;height:11px"></span> Reading the document…</span>';
      try { const r = await api('POST', `${A}/ai/reference`, fd); Object.assign(holder, { ref: r.ref, refName: r.name, refChars: r.chars }); draw();
        toast(`Read ${r.name}: questions will follow its topics.${r.warnings.length ? ' ' + r.warnings[0] : ''}`, 'ok'); }
      catch (e) { holder.ref = ''; draw(); fail(e); } inp.value = ''; };
    draw();
  };
  return { html, bind };
}

function assistantCard(after) {
  let plan = {};
  const refs = { ref: '' }, rp = refPicker(refs);
  const TY = { standard: 'coding', debug: 'fix the bug', complete: 'complete the code', output: 'predict the output', trace: 'code tracing', prompt: 'prompt', mcq: 'multiple choice' };
  const el = document.createElement('section'); el.className = 'card asst';
  el.innerHTML = `<div class="asst-head"><div class="asst-ico">${ico('sparkles', 18)}</div><div><h3>Describe a competition</h3><div class="small muted">Say it in plain words. I build the plan and pick matching questions from the bank. You review it, then create the draft.</div></div></div>
    <div class="asst-log" id="asLog"></div>
    <div class="asst-chips" id="asChips">${['60 minute C round for 1st years tomorrow at 10am, 3 easy coding, 2 predict the output, 1 debug', '90 min Python and C round for 2nd and 3rd year, 2 medium coding, 2 code tracing, full screen, no internet', 'Placement mock: 10 moderate mcq, 5 predict the output, 2 medium coding, 60 minutes, full screen, no internet'].map((x) => `<button type="button" class="chipbtn">${esc(x)}</button>`).join('')}</div>
    <form class="asst-form" id="asForm"><textarea class="input" id="asIn" rows="2" placeholder="For example: 45 minute C round for 1st years on Monday 2pm, 4 easy coding and 2 predict the output, auto submit after 3 warnings"></textarea><button class="btn primary">${ico('right', 14)} Send</button></form>
    <div class="row" style="gap:8px;margin-top:8px">${rp.html}<span class="tiny muted">Optional: notes or an old paper. When the AI writes missing questions, they follow it.</span></div>`;
  const log = $('#asLog', el), say = (who, html) => { log.insertAdjacentHTML('beforeend', `<div class="asst-msg ${who}">${html}</div>`); log.scrollTop = log.scrollHeight; };
  const show = (r) => {
    plan = r.plan;
    say('bot', `<dl class="kv tight">${r.lines.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
      ${r.questions.length ? `<div class="small" style="font-weight:600;margin:10px 0 4px">Questions (${r.questions.length})</div><div class="asst-qs">${r.questions.map((q, i) => `<div class="row between"><span><b>${String.fromCharCode(65 + i)}.</b> ${esc(q.title)}</span><span class="row" style="gap:4px"><span class="badge">${TY[q.qtype] || q.qtype}</span><span class="badge ${{ Easy: 'green', Intermediate: 'amber', Challenging: 'red' }[q.difficulty]}">${q.difficulty}</span></span></div>`).join('')}</div>` : ''}
      ${r.problems.map((x) => `<div class="notice warn small" style="margin-top:8px">${esc(x)}</div>`).join('')}${r.todo.map((x) => `<div class="small muted" style="margin-top:6px">${esc(x)}</div>`).join('')}
      ${(r.ask || []).map((x) => `<div class="asst-ask"><b>${esc(x.q)}</b><div class="row wrap" style="gap:6px;margin-top:6px">${x.chips.map((ch) => `<button type="button" class="chipbtn" data-reply="${esc(ch)}">${esc(ch)}</button>`).join('')}</div></div>`).join('')}
      ${r.filling ? `<div class="notice ok small" style="margin-top:8px">The AI is writing ${r.filling.started} question(s). They appear in <a href="#/ai">AI Studio</a> for you to approve; then come back and press "Pick different questions".${r.filling.skipped.length ? ' It cannot write ' + esc(r.filling.skipped.join(', ')) + ' questions: add those by hand.' : ''}</div>`
        : r.missing?.length ? `<div class="row" style="margin-top:8px"><button class="btn sm" data-as="fill">${ico('sparkles', 13)} Write the ${r.missing.reduce((a, m) => a + m.count, 0)} missing question(s) with AI</button></div>` : ''}
      ${r.ready ? `<div class="row" style="margin-top:12px;gap:8px"><button class="btn primary sm" data-as="create">${ico('check', 13)} Create draft competition</button><button class="btn sm" data-as="shuffle">Pick different questions</button><span class="small muted">or type a change, e.g. "make it 90 minutes"</span></div>` : ''}`);
    const last = log.lastElementChild;
    $('[data-as=create]', last)?.addEventListener('click', (e) => busy(e.target, async () => { const x = await POST(`${A}/assistant/competition`, { message: '', plan, create: true });
      say('bot', `<div class="notice ok">${ico('check2', 15)} Draft created: <b>${esc(x.created.title)}</b>. It is not visible to students yet. <a href="#/competitions/${x.created.id}">Open it to review and publish</a>.</div>`); plan = {}; after && after(); }));
    $$('[data-reply]', last).forEach((b) => b.addEventListener('click', () => send(b.dataset.reply)));
    $('[data-as=fill]', last)?.addEventListener('click', (e) => busy(e.target, async () => show(await POST(`${A}/assistant/competition`, { message: '', plan, fill: true, ref: refs.ref }))));
    $('[data-as=shuffle]', last)?.addEventListener('click', async () => { const pl = { ...plan }; delete pl.picked; show(await POST(`${A}/assistant/competition`, { message: '', plan: pl })); });
  };
  const send = async (text) => { if (!text.trim()) return; say('me', esc(text)); $('#asIn', el).value = ''; $('#asChips', el).style.display = 'none';
    try { show(await POST(`${A}/assistant/competition`, { message: text, plan })); } catch (e) { say('bot', `<div class="notice err small">${esc(e.message)}</div>`); } };
  $('#asForm', el).onsubmit = (e) => { e.preventDefault(); send($('#asIn', el).value); };
  $('#asIn', el).onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send($('#asIn', el).value); } };
  $$('.chipbtn', el).forEach((b) => b.onclick = () => send(b.textContent));
  rp.bind(el);
  return el;
}
const ldText = (l = {}) => [{ flag: 'internet reported', block: 'internet blocked', allow: 'internet allowed' }[l.internet || 'flag'], l.fullscreen === 'off' ? 'full screen optional' : 'full screen required',
  l.leave === 'allow' ? 'leaving allowed' : 'leaving warned', l.max_violations ? `auto-submit at ${l.max_violations} warnings` : '', l.no_copy ? 'no copy' : '', l.no_paste ? 'no paste' : ''].filter(Boolean).join(' · ');
const icon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${ICON[k]}"/></svg>`;
const ROLE = { super_admin: 'Super Admin', event_admin: 'Event Admin', reviewer: 'Question Reviewer' };
const can = (m) => ME.modules.includes(m);
const diffBadge = (d) => `<span class="badge ${{ Easy: 'green', Intermediate: 'amber', Challenging: 'red' }[d] || ''}">${esc(d)}</span>`;
const qstatus = (s) => `<span class="badge ${{ approved: 'green', draft: '', archived: 'red' }[s] || ''}">${esc(s)}</span>`;
const fld = (label, inner, hint = '') => `<label class="field"><span class="lbl" title="${String(label).replace(/<[^>]*>/g, '').replace(/"/g, '&quot;')}">${label}</span>${inner}${hint ? `<span class="hint">${hint}</span>` : ''}</label>`;
const inp = (name, v = '', attrs = '') => `<input class="input" name="${name}" value="${esc(v ?? '')}" ${attrs}>`;
const ta = (name, v = '', attrs = '') => `<textarea class="textarea" name="${name}" ${attrs}>${esc(v ?? '')}</textarea>`;
const sel = (name, opts, v, attrs = '') => `<select class="select" name="${name}" ${attrs}>${opts.map((o) => { const [val, lab] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(val)}" ${String(val) === String(v) ? 'selected' : ''}>${esc(lab)}</option>`; }).join('')}</select>`;
const chk = (name, on, label, attrs = '') => `<label class="check"><input type="checkbox" name="${name}" ${on ? 'checked' : ''} ${attrs}> ${label}</label>`;
const pager = (total, limit, offset, cb) => total > limit ? `<div class="row between" style="padding:10px 14px"><span class="small muted">${offset + 1}–${Math.min(total, offset + limit)} of ${total}</span><div class="row">
  <button class="btn sm" ${offset ? '' : 'disabled'} data-pg="${offset - limit}">${ico('left')} Prev</button><button class="btn sm" ${offset + limit < total ? '' : 'disabled'} data-pg="${offset + limit}">Next ${ico('right')}</button></div></div>` : '';
const bindPager = (el, cb) => $$('[data-pg]', el).forEach((b) => b.onclick = () => cb(Number(b.dataset.pg)));

// ================= boot / auth =================
try { document.body.dataset.theme = localStorage.getItem('astra:atheme') || 'light'; } catch (e) { document.body.dataset.theme = 'light'; }
async function boot() {
  try { ME = await GET(`${A}/me`); S.csrf = ME.csrf; S.tz = ME.timezone; } catch (e) { return loginScreen(); }
  META = await GET(`${A}/questions/meta`).catch(() => null);
  S.info = { languages: META?.languages || {} };
  if (ME.must_change_pw) return forcePw();
  start();
}
function loginScreen() {
  clearTimers();
  root.innerHTML = `<div class="auth" style="min-height:100vh;background:radial-gradient(circle at 70% 10%,rgba(124,58,237,.10),transparent 50%)"><form class="card stack" id="lf">
    <div><img class="logo" src="${LOGO}" alt=""><h2>ASTRA Control Centre</h2><p class="muted small">Authorised organisers only. All access is logged.</p></div>
    ${fld('Username or email', '<input class="input" name="username" autocomplete="username" required autofocus>')}
    ${fld('Password', `<div class="pw-wrap"><input class="input" type="password" name="password" autocomplete="current-password" required><button type="button" class="btn ghost xs" id="sh">Show</button></div>`)}
    <div id="err"></div><button class="btn primary lg block" type="submit">Sign in</button>
    <p class="tiny muted center" style="margin:0">Locked out or forgot your password? Ask a Super Admin to reset it.</p></form></div>`;
  $('#sh').onclick = () => { const i = $('#lf [name=password]'); i.type = i.type === 'password' ? 'text' : 'password'; $('#sh').textContent = i.type === 'password' ? 'Show' : 'Hide'; };
  $('#lf').onsubmit = (e) => {
    e.preventDefault();
    busy($('button[type=submit]', e.target), async () => {
      try { ME = await POST(`${A}/login`, formData(e.target)); S.csrf = ME.csrf; S.tz = ME.timezone; META = await GET(`${A}/questions/meta`); S.info = { languages: META.languages }; ME.must_change_pw ? forcePw() : start(); }
      catch (er) { $('#err').innerHTML = `<div class="notice err">${esc(er.message)}</div>`; }
    });
  };
}
function forcePw() {
  root.innerHTML = `<div class="auth" style="min-height:100vh"><form class="card stack" id="pf"><h2>Set your password</h2><p class="muted small">You signed in with a temporary password. Choose a new one to continue.</p>
    ${fld('Temporary password', '<input class="input" type="password" name="current" required>')}${fld('New password', '<input class="input" type="password" name="new" minlength="8" required>', 'at least 8 characters')}
    <button class="btn primary lg block">Save and continue</button></form></div>`;
  $('#pf').onsubmit = (e) => { e.preventDefault(); busy($('button', e.target), async () => { await POST(`${A}/me/password`, formData(e.target)); ME.must_change_pw = false; toast('Password updated', 'ok'); start(); }); };
}

let goRoute;
function start() {
  root.innerHTML = `<div class="shell"><aside class="side" id="side"><a class="brand" href="#/"><img src="${LOGO}" alt=""><span>ASTRA<small>Control Centre</small></span></a>
    ${ME.modules.map((m) => `<a href="#/${m === 'dashboard' ? '' : m}" data-m="${m}">${icon(m)}${MOD[m]}</a>`).join('')}
    <div class="grow"></div><div class="tiny faint" style="padding:10px">Server time ${esc(S.tz)}</div></aside>
    <div class="main"><div class="mainbar"><button class="btn ghost sm" id="sideBtn" aria-label="Menu">${ico('menu')}</button><div class="grow" id="crumb"></div>
      <button class="btn ghost sm icon-btn" id="athemeBtn" title="Switch theme">${ico(document.body.dataset.theme === 'dark' ? 'sun' : 'moon', 18)}</button><div class="dropdown"><button class="btn ghost" id="ub"><span class="avatar">${esc(initials(ME.name))}</span><span class="small" style="text-align:left;line-height:1.2"><b>${esc(ME.name)}</b><br><span class="muted">${ROLE[ME.role]}</span></span></button>
      <div class="menu hidden" id="um"><button id="cpw">Change password</button><button id="lo">Sign out</button></div></div></div>
      <div class="content" id="view"></div></div></div>`;
  $('#sideBtn').onclick = (e) => { e.stopPropagation(); $('#side').classList.toggle('open'); };
  $('#side').addEventListener('click', (e) => { if (e.target.closest('a')) $('#side').classList.remove('open'); });
  $('.main').addEventListener('click', () => $('#side').classList.remove('open'));
  $('#athemeBtn').onclick = () => { const t = document.body.dataset.theme === 'dark' ? 'light' : 'dark'; try { localStorage.setItem('astra:atheme', t); } catch (e) { /* ignore */ } document.body.dataset.theme = t; $('#athemeBtn').innerHTML = ico(t === 'dark' ? 'sun' : 'moon', 18); };
  $('#ub').onclick = (e) => { e.stopPropagation(); $('#um').classList.toggle('hidden'); };
  document.addEventListener('click', () => $('#um')?.classList.add('hidden'));
  $('#lo').onclick = async () => { await POST(`${A}/logout`).catch(() => {}); ME = null; loginScreen(); };
  $('#cpw').onclick = () => modal({ title: 'Change password', body: `<form class="stack" id="cp">${fld('Current password', '<input class="input" type="password" name="current">')}${fld('New password', '<input class="input" type="password" name="new">', 'min 8 chars')}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Update', kind: 'primary', onClick: async (m) => { await POST(`${A}/me/password`, formData($('#cp', m))); toast('Password updated', 'ok'); } }] });
  window.addEventListener('hashchange', () => { clearTimers(); $('#side')?.classList.remove('open'); });
  goRoute = router([
    ['/', dashboard], ['/competitions', competitions], ['/competitions/new', (p) => compForm(null)], ['/competitions/:id/edit', (p) => compForm(Number(p.id))],
    ['/competitions/:id', compDetail], ['/questions', questions], ['/questions/new', () => questionEditor(null)], ['/questions/:id', questionEditor],
    ['/practice', practicePage], ['/pdf', pdfPage], ['/pdf/:id', pdfDoc], ['/ai', aiPage], ['/ai/tools', toolsLab], ['/tests', testsPage], ['/participants', participants], ['/submissions', submissionsPage],
    ['/leaderboards', leaderboards], ['/semesters', semesters], ['/reports', reports], ['/admins', adminsPage], ['/audit', auditPage], ['/database', databasePage], ['/settings', settingsPage],
  ], () => view('Not found', empty('Page not found')));
  goRoute();
}
function view(title, html, mod) {
  $$('#side a[data-m]').forEach((a) => a.classList.toggle('on', a.dataset.m === (mod || (location.hash.split('/')[1] || 'dashboard').split('?')[0])));
  $('#crumb').innerHTML = `<b>${esc(title)}</b>`;
  $('#view').innerHTML = html;
  return $('#view');
}
const ph = (title, sub = '', actions = '') => `<div class="ph"><div><h1>${esc(title)}</h1>${sub ? `<p>${sub}</p>` : ''}</div><div class="row wrap">${actions}</div></div>`;

// ================= dashboard =================
const ACT_ICON = { competition: 'trophy', question: 'book', test: 'check', student: 'user', admin: 'shield', semester: 'calendar', document: 'file', draft: 'file', ai: 'sparkles', ai_tool: 'wand', settings: 'settings', system: 'cpu', report: 'upload', announcement: 'megaphone', submission: 'code', participant: 'users' };
const actLabel = (a) => a.replace(/[._]/g, ' ').replace(/^\w/, (m) => m.toUpperCase());
async function dashboard() {
  const v = view('Dashboard', skeleton(6), 'dashboard');
  const d = await GET(`${A}/dashboard`);
  const c = d.counts, s = d.system;
  const tile = (icon, k, n, sub, href) => `<a class="card stat tile" ${href ? `href="${href}"` : ''}><span class="lsi">${ico(icon, 18)}</span><div><div class="k">${k}</div><div class="v">${n}</div><div class="s">${sub}</div></div></a>`;
  v.innerHTML = ph(`Hello, ${ME.name.split(' ')[0]}`, `${ROLE[ME.role]} · ${fmtTime(Math.floor(Date.now() / 1000))}`, can('competitions') && ME.role !== 'reviewer' ? `<a class="btn primary" href="#/competitions/new">${ico('plus', 14)} New competition</a>` : '') + `
    ${(s.alerts || []).map((a) => `<div class="notice ${a.level === 'err' ? 'err' : 'warn'} row" style="margin-bottom:10px;gap:10px">${ico('alert', 16)}<span>${esc(a.text)}</span></div>`).join('')}
    <div class="grid g4">
      ${tile('trophy', 'Competitions', c.competitions, `${c.active} live · ${c.upcoming} upcoming · ${c.drafts} drafts`, '#/competitions')}
      ${tile('users', 'Students', c.students, 'registered accounts', can('participants') ? '#/participants' : '')}
      ${tile('code', 'Submissions', c.submissions, `${c.completed} rounds completed`, can('submissions') ? '#/submissions' : '')}
      <a class="card stat tile" ${can('settings') ? 'href="#/settings"' : ''}><span class="lsi" style="background:${s.workers_alive ? 'var(--green-soft)' : 'var(--red-soft)'};color:${s.workers_alive ? 'var(--green)' : 'var(--red)'}">${ico(s.workers_alive ? 'check2' : 'alert', 18)}</span>
        <div><div class="k">System</div><div class="v" style="font-size:19px;color:${s.workers_alive ? 'var(--green)' : 'var(--red)'}">${s.workers_alive ? 'Healthy' : 'Judge offline'}</div>
        <div class="s">${s.workers_alive} judge worker${s.workers_alive === 1 ? '' : 's'} · queue ${s.queue.queued + s.queue.running}${s.host?.cpu != null ? ` · CPU ${s.host.cpu}%` : ''}${s.host?.ram_pct != null ? ` · RAM ${s.host.ram_pct}%` : ''}</div></div></a>
    </div>
    <div class="dash-row">
      <section class="card flexcard"><div class="card-head"><h3 class="row" style="gap:8px">${ico('zap', 17)} ${d.live.length ? 'Live now' : 'Next up'}</h3>${d.live.length ? `<span class="badge green live dot">${d.live.length} live</span>` : ''}</div>
        <div class="card-body grow stack">${d.live.length ? d.live.map((x) => `<div class="livebox">
          <div class="row between"><div><b>${esc(x.title)}</b><div class="tiny mono muted">${esc(x.code)}</div></div><div class="right">${badge(x.status)}<div class="mono" style="font-size:20px;font-weight:700;margin-top:4px" data-left="${Math.floor(Date.now() / 1000) + (x.remaining_sec || 0)}">${fmtDur(x.remaining_sec)}</div></div></div>
          <div class="bar" style="margin:10px 0"><i style="width:${x.total_sec ? Math.min(100, Math.max(0, 100 - (x.remaining_sec / x.total_sec) * 100)) : 0}%"></i></div>
          <div class="minis"><div><b>${x.online}</b><span>online</span></div><div><b>${x.participants}</b><span>registered</span></div><div><b>${x.submissions}</b><span>submissions</span></div><div><b>${x.finished}</b><span>finished</span></div></div>
          <div class="row" style="margin-top:12px"><a class="btn sm primary" href="#/competitions/${x.id}?t=monitor">${ico('eye', 14)} Live monitor</a><a class="btn sm" href="#/competitions/${x.id}?t=integrity">${ico('shield', 14)} Integrity</a><a class="btn sm" href="#/competitions/${x.id}?t=leaderboard">${ico('chart', 14)} Leaderboard</a></div></div>`).join('')
          : d.upcoming_list.length ? d.upcoming_list.map((x) => `<a class="recrow" href="#/competitions/${x.id}"><span class="lsi sm">${ico('calendar', 15)}</span><div class="grow"><b class="small">${esc(x.title)}</b><div class="tiny muted">${fmtTime(x.start_at)}</div></div>${badge(x.status)}</a>`).join('')
            : `<div class="empty grow" style="display:grid;place-items:center"><div><div class="emptyi">${ico('calendar', 24)}</div><div class="t">Nothing live or scheduled</div><div class="small">Create a round and publish it to see it here.</div></div></div>`}</div></section>
      <section class="card flexcard"><div class="card-head"><h3 class="row" style="gap:8px">${ico('trophy', 17)} Recent results</h3>${can('leaderboards') ? `<a class="small row" style="gap:4px" href="#/leaderboards">All ${ico('right', 13)}</a>` : ''}</div>
        <div class="grow">${d.results.length ? d.results.map((r) => `<a class="resrow" href="#/competitions/${r.id}?t=leaderboard"><div class="row between"><b>${esc(r.title)}</b><span class="tiny muted">${r.participants} participants</span></div>
          <div class="toprow">${r.top.map((t, i) => `<span class="row" style="gap:6px;min-width:0">${rankBadge(i + 1)}<span class="ellip small" title="${esc(t.name)}">${esc(t.name)}</span><b class="small">${t.score}</b></span>`).join('')}</div></a>`).join('')
          : `<div class="empty"><div class="emptyi">${ico('trophy', 24)}</div><div class="t">No results yet</div><div class="small">Finished rounds show their top three here.</div></div>`}</div></section>
    </div>
    <section class="card" style="margin-top:16px"><div class="card-head"><h3 class="row" style="gap:8px">${ico('clock', 17)} Recent activity</h3>${can('audit') ? `<a class="small row" style="gap:4px" href="#/audit">Audit log ${ico('right', 13)}</a>` : ''}</div>
      ${d.recent_activity.length ? `<div class="actlist">${d.recent_activity.map((a) => `<div class="actrow"><span class="lsi sm">${ico(ACT_ICON[(a.action || '').split('.')[0]] || 'dot', 14)}</span>
        <span class="grow"><b class="small">${esc(actLabel(a.action))}</b>${a.entity ? `<span class="tiny muted"> · ${esc(a.entity)} #${a.entity_id}</span>` : ''}</span><span class="small">${esc(a.name || 'System')}</span><span class="tiny muted actwhen">${ago(a.created_at)}</span></div>`).join('')}</div>`
        : empty('No activity yet')}</section>`;
  every(() => $$('[data-left]').forEach((el) => { el.textContent = fmtDur(Number(el.dataset.left) - Date.now() / 1000); }), 1000);
}

// ================= competitions =================
async function competitions() {
  const v = view('Competitions', skeleton(5));
  const rows = await GET(`${A}/competitions`);
  v.innerHTML = ph('Competitions', 'Every round is fully independent: its own questions, participants, submissions and results.', can('competitions') && ME.role !== 'reviewer' ? `<button class="btn" id="quickBtn">${ico('zap', 14)} Instant test round</button><a class="btn primary" href="#/competitions/new">+ New competition</a>` : '') +
    `<div class="card">${rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Competition</th><th>Status</th><th>Starts</th><th>Duration</th><th>Languages</th><th class="num">Questions</th><th class="num">Participants</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="click" data-id="${r.id}"><td><b>${esc(r.title)}</b><div class="tiny muted mono">${esc(r.code)}${r.round_no ? ' · Round ' + r.round_no : ''}${r.academic_year ? ' · ' + esc(r.academic_year) : ''}</div></td><td>${badge(r.display_status)}</td>
      <td class="small nowrap">${fmtTime(r.start_at)}</td><td class="small">${fmtMins(r.duration_min)}</td><td class="small">${r.languages.map(langName).join(', ')}</td><td class="num">${r.questions}</td><td class="num">${r.participants}</td></tr>`).join('')}</tbody></table></div>`
      : empty('No competitions yet', 'Create your first round. It starts as a draft so nothing is public until you publish.')}</div>`;
  $$('[data-id]', v).forEach((tr) => tr.onclick = () => nav('/competitions/' + tr.dataset.id));
  if (can('competitions') && ME.role !== 'reviewer') $('.card', v).before(assistantCard());
  if ($('#quickBtn')) $('#quickBtn').onclick = () => modal({ title: 'Instant test round', body: `<p class="small muted" style="margin-top:0">Starts right now with questions picked from the bank, so you can try the whole flow (join, solve, finish, leaderboard) from a student account in a minute.</p>
    <form class="stack" id="qr"><div class="grid g3">${fld('Multiple choice', '<input class="input" type="number" name="mcq" value="3" min="0" max="30">')}${fld('Coding', '<input class="input" type="number" name="coding" value="1" min="0" max="10">')}${fld('Minutes', '<input class="input" type="number" name="minutes" value="15" min="5" max="180">')}</div>
      ${fld('Difficulty', sel('difficulty', [['Easy', 'Easy'], ['Intermediate', 'Moderate'], ['Challenging', 'Hard']], 'Easy'))}${chk('strict', false, 'Use exam rules (full screen, warnings, auto-submit after 3)')}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Start test round', kind: 'primary', onClick: async (m) => { const r = await POST(`${A}/competitions/quick`, formData($('#qr', m))); toast(`Started ${r.code} with ${r.questions} questions. Students can join now.`, 'ok'); nav('/competitions/' + r.id); } }] });
}

const AVAIL =() => META?.available || {};
async function compForm(id) {
  const c = id ? await GET(`${A}/competitions/${id}`) : { lockdown: {}, title: '', code: '', description: '', instructions: '## Rules\n- Individual participation only.\n- Read from standard input, write to standard output.\n- Do not share code during the contest.', academic_year: '', semester_label: '', round_no: 1, category: 'General', eligible_years: [], eligible_departments: [], registration_mode: 'open', duration_min: 60, languages: ['c', 'python'], max_marks: 100, scoring: { mode: 'partial', negative_marks: 0, first_blood_bonus: 0, penalty_minutes: 10, tie_break: 'penalty', violation_pct: 5 }, leaderboard_visibility: 'hidden', show_test_counts: true, allow_late_join: true, auto_start: true, paste_policy: 'log', status: 'draft', draw: 0, shuffle: true, ai_pool: true,
    academic_year: (() => { const d = new Date(), y = d.getMonth() >= 5 ? d.getFullYear() : d.getFullYear() - 1; return `${y}-${String(y + 1).slice(2)}`; })() };
  const LDc = { internet: 'flag', fullscreen: 'required', leave: 'warn', max_violations: 0, ...(c.lockdown || {}) };
  const locked = ['live', 'paused', 'ended'].includes(c.status);
  const v = view(id ? 'Edit competition' : 'New competition', '');
  const sc = c.scoring;
  const steps = ['Basics', 'When & who', 'Questions & scoring', 'Rules', 'Review'];
  const GUIDE = [
    'Give the round a name. The code is filled in for you; students use it in the share link. Everything else here is optional.',
    'Pick when it starts with one click and how long it runs. With "Start automatically" on, it opens by itself at that time once published. Tick years or departments only if the round is not for everyone.',
    'Choose the languages students may use. "Questions for each student" gives every student their own random set from your list (same mix of easy / medium / hard for everyone), so neighbours do not have the same questions. Ties are broken the same way for everyone.',
    'Write the rules students read before starting. The default text is fine for most rounds.',
    'Check everything, then press Save as draft. Next you add questions and press Publish. Nothing is visible to students until you publish.'];
  let guideOn = localStorage.getItem('astra:guide') !== 'off';
  v.innerHTML = ph(id ? `Edit · ${c.title}` : 'New competition', id ? `Status: ${c.display_status}${locked ? ' · timing, languages and scoring are locked while running (use Pause/Extend)' : ''}` : 'Saved as a draft. Add questions next, then publish when ready.') +
    `<div class="steps" id="steps">${steps.map((s, i) => `<button type="button" data-s="${i}" class="${i ? '' : 'on'}"><span class="n">${i + 1}</span>${s}</button>`).join('')}</div>
    <div class="guide" id="guide"><span class="lsi">${ico('bulb', 17)}</span><div class="grow"><b id="gStep"></b><div class="small" id="gText"></div></div><button type="button" class="btn ghost sm" id="gOff">Skip guide</button></div>
    <button type="button" class="btn ghost sm hidden" id="gOn" style="margin-bottom:10px">${ico('bulb', 14)} Show guide</button>
    <form class="card pad" id="cf" style="max-width:1080px">
    <div data-step="0" class="stack">${fld('Title', inp('title', c.title, 'required maxlength=150 placeholder="Code Arena — Round 01" id="ctitle"'))}
      <div class="grid g3">${fld('Competition code', inp('code', c.code, 'required placeholder="filled from the title" style="text-transform:uppercase" id="ccode"'), 'unique, used in the student link')}${fld('Round number', inp('round_no', c.round_no, 'type=number min=0'))}${fld('Category', inp('category', c.category, 'list="cats"'))}</div>
      <datalist id="cats"><option>General</option><option>Beginner</option><option>Intermediate</option><option>Open</option><option>Practice round</option></datalist>
      <div class="grid g2">${fld('Academic year', inp('academic_year', c.academic_year, 'placeholder="2026-27"'))}${fld('Semester', inp('semester_label', c.semester_label, 'placeholder="Odd semester"'))}</div>
      ${fld('Short description', ta('description', c.description, 'rows=3 maxlength=5000'), 'shown on cards')}
      <div class="notice small">Participation: <b>Individual</b>. Each participant competes with their own account.</div></div>
    <div data-step="1" class="stack hidden">
      <div><div class="small" style="font-weight:600;margin-bottom:8px">When does it start? <span class="muted" style="font-weight:400">(${esc(S.tz)})</span></div>
        <div class="row wrap" id="quickStart">${locked ? '' : [['now', 'Right now'], ['15', 'In 15 minutes'], ['today14', 'Today 2:00 PM'], ['tom10', 'Tomorrow 10:00 AM'], ['mon10', 'Next Monday 10:00 AM']].map(([k, l]) => `<button type="button" class="chipbtn" data-when="${k}">${l}</button>`).join('')}</div>
        <div class="grid g3" style="margin-top:10px">${fld('Date', `<input class="input" type="date" id="sDate" ${locked ? 'disabled' : ''}>`)}${fld('Time', `<select class="select" id="sTime" ${locked ? 'disabled' : ''}>${Array.from({ length: 96 }, (_, k) => { const h = Math.floor(k / 4), m = (k % 4) * 15; return `<option value="${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}">${(h % 12) || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}</option>`; }).join('')}</select>`)}
          <div class="fcheck">${chk('auto_start', c.auto_start, 'Start automatically at this time')}</div></div>
        <input type="hidden" name="start_at" id="sAt" value="${toLocalInput(c.start_at)}"><div class="small muted" id="sSay" style="margin-top:4px"></div></div>
      <div><div class="small" style="font-weight:600;margin-bottom:8px">How long?</div><div class="row wrap">${[30, 45, 60, 90, 120, 180].map((m) => `<button type="button" class="chipbtn" data-dur="${m}">${fmtMins(m)}</button>`).join('')}
        <label class="row small" style="gap:6px">or <input class="input" name="duration_min" type="number" min="5" max="1440" value="${c.duration_min}" style="width:90px" ${locked ? 'disabled' : ''}> minutes</label></div></div>
      <div><div class="small" style="font-weight:550;margin-bottom:6px">Eligible years <span class="muted">(none = all)</span></div><div class="row wrap">${[1, 2, 3, 4].map((y) => chk('eligible_years', c.eligible_years.includes(y), `Year ${y}`, `value="${y}" data-multi="1" data-num="1"`)).join('')}</div></div>
      <div><div class="small" style="font-weight:550;margin-bottom:6px">Eligible departments <span class="muted">(none = all)</span></div><div class="row wrap">${[...new Set([...(META.departments || []), ...c.eligible_departments])].map((d) => chk('eligible_departments', c.eligible_departments.includes(d), esc(d), `value="${esc(d)}" data-multi="1"`)).join('') || '<span class="small muted">Add departments in System Settings.</span>'}</div></div>
      <details class="more"><summary>Joining rules (optional)</summary><div class="grid g2" style="margin-top:10px">${fld('Who can join', sel('registration_mode', [['open', 'Eligible students join themselves'], ['invite_only', 'Only students the organisers add']], c.registration_mode))}
        <div class="fcheck">${chk('allow_late_join', c.allow_late_join, 'Students can still join after it has started')}</div></div></details></div>
    <div data-step="2" class="stack hidden">
      <div class="card pad stack" style="background:var(--surface-2,transparent)"><div><h4 style="margin:0">Questions for each student</h4><div class="small muted">Stops copying from a neighbour: everyone gets a different set and order, with the same mix of kinds and difficulty.</div></div>
        <div class="grid g3">${fld('Questions per student', inp('draw', c.draw || 0, `type=number min=0 max=200 ${locked ? 'disabled' : ''}`), '0 = everyone gets every question. Example: add 15 questions, set 5.')}
          <div class="fcheck">${chk('shuffle', c.shuffle, 'Shuffle question and answer-option order for each student')}</div>
          <div class="fcheck">${chk('ai_pool', c.ai_pool, 'Let the AI add checked variants as students register')}</div></div>
        ${fld('What should the AI write for this competition? (optional)', ta('ai_prompt', c.ai_prompt || '', 'rows=2 maxlength=1000 placeholder="e.g. easy array and string problems with college-life stories; output questions on loops and pointers"'), 'Used for every variant it adds to this competition.')}
        <div class="small muted">AI variants are added only before the start, only for coding and predict-the-output questions (the server proves each answer by running it), one for every ${4} registrations, at most 15.</div></div>
      <div><div class="small" style="font-weight:550;margin-bottom:6px">Programming languages for this competition</div><div class="row wrap">${Object.entries(META.languages).filter(([k]) => k !== 'answer' && k !== 'prompt').map(([k, l]) => chk('languages', c.languages.includes(k), `${esc(l.name)}${AVAIL()[k] ? '' : ' <span class="badge red">not installed on judge</span>'}`, `value="${k}" data-multi="1" ${locked ? 'disabled' : ''}`)).join('')}</div></div>
      <div class="grid g3">${fld('Maximum marks', inp('max_marks', c.max_marks, 'type=number min=1'), 'for semester scaling')}${fld('Scoring mode', sel('sc_mode', [['partial', 'Partial marks by test weight'], ['all_or_nothing', 'Fixed marks: all tests must pass']], sc.mode, locked ? 'disabled' : ''))}
        ${fld('Tie-break', sel('sc_tie_break', [['penalty', 'Penalty time (time + wrong attempts)'], ['time', 'Earliest time to reach score']], sc.tie_break, locked ? 'disabled' : ''))}</div>
      <div class="grid g3">${fld('Negative marks per wrong submission', inp('sc_negative_marks', sc.negative_marks, `type=number min=0 step=0.5 ${locked ? 'disabled' : ''}`), '0 = off')}${fld('First-correct bonus', inp('sc_first_blood_bonus', sc.first_blood_bonus, `type=number min=0 ${locked ? 'disabled' : ''}`), '0 = off')}
        ${fld('Penalty minutes per wrong attempt', inp('sc_penalty_minutes', sc.penalty_minutes, `type=number min=0 ${locked ? 'disabled' : ''}`), 'tie-break only')}</div>
      <div class="grid g3">${fld('Marks lost per fair-play warning', `<div class="row" style="gap:6px;flex-wrap:nowrap">${inp('sc_violation_pct', sc.violation_pct ?? 5, `type=number min=0 max=50 step=0.5 style="width:100px" ${locked ? 'disabled' : ''}`)}<span class="small muted">% of the student's maximum</span></div>`, 'Leaving the test, leaving full screen or using the internet. 0 = no deduction.')}</div>
      <div class="grid g3">${fld('Leaderboard visibility', sel('leaderboard_visibility', [['live', 'Live during the contest'], ['after_end', 'Automatically when it ends'], ['hidden', 'Manually: I publish results']], c.leaderboard_visibility))}
        ${fld('Fair-play paste policy', sel('paste_policy', [['log', 'Allow paste, record it'], ['block_external', 'Block pastes from outside the editor'], ['allow', 'Allow, do not flag']], c.paste_policy))}
        <div class="fcheck">${chk('show_test_counts', c.show_test_counts, 'Show students how many tests passed')}</div></div>
      <div class="card pad stack" style="background:var(--surface-2,transparent)"><div><h4 style="margin:0">Exam lockdown</h4><div class="small muted">What students may do during this round. Use stricter rules for exams, and allow internet for online, research or prompt rounds.</div></div>
        <div class="grid g3">${fld('Internet', sel('ld_internet', [['flag', 'Detect and report'], ['block', 'Block (test hidden while online)'], ['allow', 'Allowed']], LDc.internet))}
          ${fld('Full screen', sel('ld_fullscreen', [['required', 'Required (test hidden outside it)'], ['off', 'Not required']], LDc.fullscreen))}
          ${fld('Leaving the test (other tab or app)', sel('ld_leave', [['warn', 'Hide test + give a warning'], ['allow', 'Allowed, only recorded']], LDc.leave))}</div>
        <div class="grid g3">${fld('Auto-submit after warnings', inp('ld_max_violations', LDc.max_violations, 'type=number min=0 max=50'), '0 = never. Leaving full screen or the test window counts as one warning.')}
          <div class="fcheck">${chk('ld_no_copy', LDc.no_copy, 'Block copying (problem text and code)')}</div><div class="fcheck">${chk('ld_no_paste', LDc.no_paste, 'Block all pasting, even inside the editor')}</div></div>
        <div class="notice small">${ico('alert', 14)} <b>Block internet</b> only works if the lab network itself has no internet during the round (for example a firewall rule for the exam). Otherwise every student's test stays hidden. Run a round with <b>Detect</b> first: the Fair-play tab shows how many PCs can reach the internet. A web page cannot switch off a PC's internet by itself.</div></div></div>
    <div data-step="3" class="stack hidden"><div class="grid g2" style="align-items:start">${fld('Instructions / rules (Markdown)', ta('instructions', c.instructions, 'rows=16 id="instr"'))}<div><div class="small muted" style="margin-bottom:5px">Preview</div><div class="card pad prose" id="instrPrev" style="min-height:300px"></div></div></div></div>
    <div data-step="4" class="hidden" id="review"></div>
    <div class="row between" style="margin-top:20px"><button type="button" class="btn" id="prev">${ico('left')} Back</button><div class="row"><a class="btn ghost" href="#/competitions${id ? '/' + id : ''}">Cancel</a><button type="button" class="btn" id="next">Next ${ico('right')}</button><button type="submit" class="btn primary" id="save">${id ? 'Save changes' : 'Save as draft'}</button></div></div></form>`;
  let step = 0;
  const drawGuide = () => { $('#guide').classList.toggle('hidden', !guideOn); $('#gOn').classList.toggle('hidden', guideOn); $('#gStep').textContent = `Step ${step + 1} of ${steps.length}: ${steps[step]}`; $('#gText').textContent = GUIDE[step]; };
  $('#gOff').onclick = () => { guideOn = false; localStorage.setItem('astra:guide', 'off'); drawGuide(); };
  $('#gOn').onclick = () => { guideOn = true; localStorage.removeItem('astra:guide'); drawGuide(); };
  let codeTouched = !!c.code;
  $('#ccode').oninput = () => { codeTouched = true; };
  $('#ctitle').oninput = (e) => { if (!codeTouched) $('#ccode').value = e.target.value.toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 30); };
  const setStart = (iso) => { $('#sAt').value = iso || ''; if (iso) { $('#sDate').value = iso.slice(0, 10); $('#sTime').value = iso.slice(11, 16); }
    $('#sSay').textContent = iso ? `Starts ${new Date(iso + ':00').toLocaleString('en-IN', { weekday: 'long', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}` : 'No start time yet: you can also start it by hand from the competition page.';
    $$('[data-when]').forEach((b) => b.classList.remove('on')); };
  const nowIso = (addMin = 0) => toLocalInput(Math.floor(Date.now() / 1000) + addMin * 60);
  const at = (days, hh, weekday) => { const d = new Date(nowIso().slice(0, 10) + 'T00:00'); if (weekday != null) d.setDate(d.getDate() + ((weekday + 7 - d.getDay()) % 7 || 7)); else d.setDate(d.getDate() + days);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(hh).padStart(2, '0')}:00`; };
  $$('[data-when]', v).forEach((b) => b.onclick = () => { const k = b.dataset.when;
    const iso = k === 'now' ? nowIso(1) : k === '15' ? nowIso(15) : k === 'today14' ? at(0, 14) : k === 'tom10' ? at(1, 10) : at(0, 10, 1);
    const ts = Math.floor(Date.now() / 1000) + 60;
    if (k === 'today14' && iso <= nowIso()) return toast('2:00 PM has already passed today. Pick another time.', 'info');
    setStart(iso.slice(0, 14) + (k === 'now' || k === '15' ? iso.slice(14, 16) : '00')); b.classList.add('on'); $('[name=auto_start]').checked = true; void ts; });
  const fromParts = () => { if ($('#sDate').value) setStart(`${$('#sDate').value}T${$('#sTime').value || '10:00'}`); };
  $('#sDate').onchange = fromParts; $('#sTime').onchange = fromParts;
  $$('[data-dur]', v).forEach((b) => b.onclick = () => { if (!locked) $('[name=duration_min]').value = b.dataset.dur; $$('[data-dur]').forEach((x) => x.classList.toggle('on', x === b)); });
  setStart($('#sAt').value);
  const show = (i) => { step = Math.max(0, Math.min(steps.length - 1, i)); drawGuide(); $$('[data-step]', v).forEach((d) => d.classList.toggle('hidden', Number(d.dataset.step) !== step)); $$('#steps button').forEach((b) => b.classList.toggle('on', Number(b.dataset.s) === step)); $('#prev').disabled = !step; $('#next').classList.toggle('hidden', step === steps.length - 1); if (step === 4) review(); };
  $$('#steps button').forEach((b) => b.onclick = () => show(Number(b.dataset.s)));
  $('#prev').onclick = () => show(step - 1); $('#next').onclick = () => show(step + 1);
  const prev = () => { $('#instrPrev').innerHTML = md($('#instr').value); }; $('#instr').oninput = prev; prev();
  const body = () => {
    const f = formData($('#cf'));
    const b = { ...f, eligible_departments: f.eligible_departments || [], eligible_years: f.eligible_years || [], languages: locked ? c.languages : (f.languages || []),
      scoring: locked ? c.scoring : { mode: f.sc_mode, tie_break: f.sc_tie_break, negative_marks: f.sc_negative_marks || 0, first_blood_bonus: f.sc_first_blood_bonus || 0, penalty_minutes: f.sc_penalty_minutes ?? 10, violation_pct: f.sc_violation_pct ?? 5 },
      draw: locked ? c.draw : (f.draw || 0), shuffle: locked ? c.shuffle : !!f.shuffle, ai_pool: !!f.ai_pool,
      start_at: locked ? c.start_at : (f.start_at || null), duration_min: locked ? c.duration_min : f.duration_min, reg_start: null, reg_end: null };
    Object.keys(b).filter((k) => k.startsWith('sc_')).forEach((k) => delete b[k]);
    b.lockdown = { internet: f.ld_internet, fullscreen: f.ld_fullscreen, leave: f.ld_leave, max_violations: f.ld_max_violations || 0, no_copy: !!f.ld_no_copy, no_paste: !!f.ld_no_paste };
    Object.keys(b).filter((k) => k.startsWith('ld_')).forEach((k) => delete b[k]);
    return b;
  };
  const review = () => {
    const b = body();
    $('#review').innerHTML = `<h3>Review</h3><dl class="kv"><dt>Title</dt><dd>${esc(b.title)}</dd><dt>Code</dt><dd class="mono">${esc((b.code || '').toUpperCase())}</dd><dt>Round</dt><dd>${esc(b.round_no)} · ${esc(b.category)} · ${esc(b.academic_year)} ${esc(b.semester_label)}</dd>
      <dt>Start</dt><dd>${esc(b.start_at || 'not set')} (${esc(S.tz)}) · ${esc(b.duration_min)} min${b.auto_start ? ' · auto-start' : ' · manual start'}</dd><dt>Joining</dt><dd>${b.registration_mode === 'open' ? 'Students join themselves' : 'Organisers add students'}${b.allow_late_join ? ', also after the start' : ', until the start'}</dd>
      <dt>Questions</dt><dd>${b.draw ? `${b.draw} per student, drawn from your list` : 'Every student gets every question'}${b.shuffle ? ' · own order' : ''}${b.ai_pool ? ' · AI adds variants' : ''}</dd>
      <dt>Eligibility</dt><dd>Years: ${b.eligible_years.length ? b.eligible_years.join(', ') : 'all'} · Departments: ${b.eligible_departments.length ? esc(b.eligible_departments.join(', ')) : 'all'}</dd>
      <dt>Languages</dt><dd>${b.languages.map(langName).join(', ') || '<span style="color:var(--red)">none selected</span>'}</dd><dt>Scoring</dt><dd>${esc(b.scoring.mode)} · tie-break ${esc(b.scoring.tie_break)} · negative ${b.scoring.negative_marks} · bonus ${b.scoring.first_blood_bonus} · penalty ${b.scoring.penalty_minutes} min · −${b.scoring.violation_pct}% per warning</dd>
      <dt>Leaderboard</dt><dd>${esc(b.leaderboard_visibility)}${b.show_test_counts ? ' · test counts shown' : ''}</dd><dt>Paste policy</dt><dd>${esc(b.paste_policy)}</dd><dt>Lockdown</dt><dd>${ldText(b.lockdown)}</dd><dt>Max marks</dt><dd>${esc(b.max_marks)}</dd></dl>`;
  };
  $('#cf').onsubmit = (e) => {
    e.preventDefault();
    busy($('#save'), async () => {
      const r = id ? await PUT(`${A}/competitions/${id}`, body()) : await POST(`${A}/competitions`, body());
      toast(id ? 'Saved' : 'Draft created. Now add questions.', 'ok'); nav(`/competitions/${r.id}${id ? '' : '?t=questions'}`);
    });
  };
  show(0);
}

async function compDetail(p) {
  const id = Number(p.id), tab = p.query.t || 'overview';
  const v = view('Competition', skeleton(6), 'competitions');
  const c = await GET(`${A}/competitions/${id}`);
  const tabs = [['overview', 'Overview'], ['questions', `Questions (${c.questions.length})`], ['participants', `Participants (${c.participant_count})`], ['monitor', 'Live monitor'], ['integrity', 'Integrity'], ['leaderboard', 'Leaderboard'], ['submissions', `Submissions (${c.submission_count})`], ['reports', 'Reports']];
  v.innerHTML = `<a class="small" href="#/competitions">${ico('left')} Competitions</a>` + ph(c.title, `<span class="mono">${esc(c.code)}</span> · ${badge(c.display_status)} ${c.remaining_sec != null ? `<span class="mono">${fmtDur(c.remaining_sec)} left</span>` : ''}`, `<span id="hdrActions"></span>`) +
    `<div class="tabs" style="margin-bottom:18px">${tabs.map(([k, l]) => `<a href="#/competitions/${id}?t=${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</div><div id="tab"></div>`;
  $('#crumb').innerHTML = `<b>${esc(c.code)}</b>`;
  const T = $('#tab');
  ({ overview: compOverview, questions: compQuestions, participants: compParticipants, monitor: compMonitor, integrity: compIntegrity, leaderboard: compLeaderboard, submissions: (T, c) => submissionsTable(T, { competition_id: c.id }), reports: (T, c) => reportButtons(T, c) }[tab] || compOverview)(T, c);
}

const ACTIONS = {
  draft: [['publish', 'Publish', 'primary'], ['cancel', 'Cancel', 'danger']], published: [['start', 'Start now', 'success'], ['unpublish', 'Back to draft', ''], ['cancel', 'Cancel', 'danger']],
  live: [['pause', 'Pause', ''], ['extend', 'Extend time', ''], ['end', 'End competition', 'danger solid']], paused: [['resume', 'Resume', 'success'], ['extend', 'Extend time', ''], ['end', 'End competition', 'danger solid']],
  ended: [['finalize', 'Finalise results', 'primary'], ['archive', 'Archive', '']], finalized: [['archive', 'Archive', '']], cancelled: [['archive', 'Archive', '']], archived: [],
};
async function doAction(c, action) {
  const label = { publish: 'Publish', start: 'Start the competition now? The timer starts for everyone.', pause: 'Pause the competition? The timer freezes and submissions stop for everyone.', resume: 'Resume the competition?', end: 'End the competition now? Submissions close immediately for everyone.', finalize: 'Finalise results? The leaderboard is frozen as the official record.', archive: 'Archive this competition? It disappears from student listings; results stay intact.', cancel: 'Cancel this competition?', unpublish: 'Move back to draft? It disappears from the student portal.' }[action];
  let body = { action };
  if (action === 'extend') {
    const r = await new Promise((res) => modal({ title: 'Extend time', body: `<form class="stack" id="ef">${fld('Extra minutes for everyone', '<input class="input" type="number" name="minutes" min="1" max="600" value="10" data-num="1">')}${fld('Reason (shown to students)', '<input class="input" name="reason" maxlength="300">')}</form>`,
      actions: [{ label: 'Cancel', onClick: () => res(null) }, { label: 'Extend', kind: 'primary', onClick: (m) => res(formData($('#ef', m))) }] }));
    if (!r) return; body = { action, ...r };
  } else if (action === 'publish') {
    if (!(await previewComp(c, true))) return;
  } else if (!(await confirmBox(label, { ok: action[0].toUpperCase() + action.slice(1), danger: ['end', 'cancel'].includes(action) }))) return;
  if (action === 'pause') { const reason = ''; body.reason = reason; }
  await POST(`${A}/competitions/${c.id}/action`, body);
  toast(`Done: ${action}`, 'ok'); goRoute();
}
function previewComp(c, publishing) {
  return new Promise((res) => {
    modal({ title: publishing ? 'Preview before publishing' : 'Student preview', size: 'wide', body: `<div class="notice info small" style="margin-bottom:12px">This is what students will see. Publishing pins the exact current version of each question.</div>
      <h2>${esc(c.title)}</h2><div class="row small muted" style="margin-bottom:10px"><span class="mono">${esc(c.code)}</span> · Round ${c.round_no} · ${fmtTime(c.start_at)} · ${fmtMins(c.duration_min)} · ${c.languages.map(langName).join(', ')}</div>
      ${c.description ? `<div class="prose">${md(c.description)}</div>` : ''}${c.instructions ? `<div class="card pad prose" style="margin:12px 0">${md(c.instructions)}</div>` : ''}
      <h3>Problems</h3>${c.questions.map((q, i) => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--border)"><b>${String.fromCharCode(65 + i)}.</b><span class="grow">${esc(q.title)} <span class="tiny muted">v${q.version_no}</span></span>${q.q_status === 'approved' ? '<span class="badge green">approved</span>' : '<span class="badge red">not approved</span>'}${q.validated_at || q.latest_version_id !== q.version_id ? '' : '<span class="badge red">not validated</span>'}<b>${q.marks}</b></div>`).join('') || empty('No questions added')}`,
      actions: publishing ? [{ label: 'Cancel', onClick: () => res(false) }, { label: 'Publish', kind: 'primary', onClick: () => res(true) }] : [{ label: 'Close', kind: 'primary', onClick: () => res(false) }] });
  });
}
function compOverview(T, c) {
  const acts = ACTIONS[c.status] || [];
  $('#hdrActions').innerHTML = `${['draft', 'published', 'live', 'paused', 'ended'].includes(c.status) ? `<a class="btn" href="#/competitions/${c.id}/edit">Edit</a>` : ''}<button class="btn" id="pv">Preview</button>
    ${can('competitions') ? '<button class="btn" id="dup">Duplicate</button>' : ''}${acts.map(([a, l, k]) => `<button class="btn ${k}" data-act="${a}">${l}</button>`).join('')}
    ${ME.role === 'super_admin' && ['draft', 'cancelled'].includes(c.status) ? '<button class="btn danger" id="del">Delete</button>' : ''}
    ${['ended', 'finalized', 'archived'].includes(c.status) ? (c.results_published_at ? `<button class="btn" id="unpub">${ico('eye', 14)} Hide results</button>` : `<button class="btn success" id="pub">${ico('megaphone', 14)} Publish results</button>`) : ''}`;
  const pubToggle = (on) => async () => {
    if (!(await confirmBox(on ? 'Publish the results now? Every participant sees their score and rank, the leaderboard opens and they get a "results are out" notification.' : 'Hide the results from students again?', { ok: on ? 'Publish results' : 'Hide results' }))) return;
    await POST(`${A}/competitions/${c.id}/results`, { publish: on }).then(() => { toast(on ? 'Results published' : 'Results hidden', 'ok'); goRoute(); }).catch(fail);
  };
  if ($('#pub')) $('#pub').onclick = pubToggle(true);
  if ($('#pub2')) $('#pub2').onclick = pubToggle(true);
  if ($('#explainT')) $('#explainT').onchange = (e) => POST(`${A}/competitions/${c.id}/explain`, { on: e.target.checked }).then(() => toast(e.target.checked ? 'Students will see how ranks were decided' : 'Rank reasons hidden', 'ok')).catch(fail);
  if ($('#unpub2')) $('#unpub2').onclick = pubToggle(false);
  if ($('#sched')) $('#sched').onclick = () => modal({ title: 'Schedule the results', body: `<p class="small muted" style="margin-top:0">Results are released by themselves at this time, but never before the round has ended and every submission is judged.</p>
    <form id="sf">${fld('Release at', `<input class="input" type="datetime-local" name="at" required value="${toLocalInput(c.results_at || (c.end_at ? c.end_at + 15 * 60 : Math.floor(Date.now() / 1000) + 3600))}">`, esc(S.tz))}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Schedule', kind: 'primary', onClick: async (m) => { await POST(`${A}/competitions/${c.id}/results`, { publish: true, at: formData($('#sf', m)).at }); toast('Results scheduled', 'ok'); goRoute(); } }] });
  if ($('#gradeSaved')) $('#gradeSaved').onclick = () => busy($('#gradeSaved'), async () => { const r = await POST(`${A}/competitions/${c.id}/grade-saved`); toast(r.message, r.queued ? 'ok' : 'info'); });
  if ($('#unpub')) $('#unpub').onclick = pubToggle(false);
  $$('[data-act]').forEach((b) => b.onclick = () => doAction(c, b.dataset.act).catch(fail));
  $('#pv').onclick = () => previewComp(c, false);
  if ($('#dup')) $('#dup').onclick = async () => { if (await confirmBox('Create a new draft with the same settings and questions? Participants and submissions are NOT copied.', { ok: 'Duplicate' })) { const r = await POST(`${A}/competitions/${c.id}/duplicate`).catch(fail); if (r) { toast('Duplicated as ' + r.code, 'ok'); nav('/competitions/' + r.id + '/edit'); } } };
  if ($('#del')) $('#del').onclick = async () => { if (await confirmBox('Permanently delete this competition? This cannot be undone.', { danger: true, ok: 'Delete' })) { await DEL(`${A}/competitions/${c.id}`).catch(fail); nav('/competitions'); } };
  const sc = c.scoring;
  T.innerHTML = `<div class="grid side2" style="--side:340px;align-items:start;gap:18px">
    <div class="stack loose">${c.questions.length ? (() => { const v = c.variety, n = Math.max(c.participant_count, 1), fmt = (x) => x >= 1e6 ? 'over a million' : x.toLocaleString('en-IN');
        const papers = v.sets * v.orders;
        return `<section class="card pad variety"><div class="row" style="gap:12px;align-items:flex-start"><span class="lsi">${ico('layers', 17)}</span><div class="grow"><b>${c.draw ? `Each student gets ${v.per_student} of the ${c.questions.length} questions` : `Everyone gets the same ${c.questions.length} questions`}</b>
          <div class="small muted" style="margin-top:2px">${c.draw ? `${fmt(v.sets)} different question sets, each in ${fmt(v.orders)} possible orders: ${papers >= n ? `enough for every one of the ${c.participant_count || 'registered'} students to get a different paper.` : `only ${fmt(papers)} different papers for ${n} students: add questions or number variants.`}`
            : c.shuffle ? `In a rotated order: students who start one after another begin with different questions (${fmt(v.orders)} different orders before one repeats). For different questions too, set "Questions per student" lower than ${c.questions.length}.` : 'In the same order for everyone (shuffle is off).'}
          New questions written by the AI: none needed. Copy check: the Integrity tab compares every pair of submissions after the round.</div></div></div></section>`; })() : ''}
      ${['draft', 'published'].includes(c.status) ? `<section class="card pad"><h3 style="margin:0 0 10px">What to do next</h3><ol class="checklist">
        <li class="${c.questions.length ? 'done' : ''}"><b>Add questions</b> <a href="#/competitions/${c.id}?t=questions">Questions tab</a>${c.draw ? ` · each student gets ${c.draw} of them` : ''}</li>
        <li class="${c.start_at ? 'done' : ''}"><b>Set a start time</b> <a href="#/competitions/${c.id}/edit">Edit</a>${c.start_at ? ` · ${fmtTime(c.start_at)}${c.auto_start ? ', starts by itself' : ''}` : ''}</li>
        <li class="${c.status === 'published' ? 'done' : ''}"><b>Publish</b> so eligible students see it and can join</li>
        <li><b>Share the student link</b> below, then watch the Live monitor tab during the round</li></ol></section>` : ''}
      ${c.draw && ['draft', 'published'].includes(c.status) ? `<section class="card pad"><div class="row between wrap"><div><h3 style="margin:0">Question pool</h3><div class="small muted">${c.questions.length} questions in the list, ${c.draw} per student${c.pool.added ? ` · ${c.pool.added} added by the AI` : ''}${c.pool.working ? ' · <b>AI is writing one now</b>' : ''}${c.ai_pool ? ` · grows by itself as students register` : ''}</div></div>
        <div class="row" style="gap:8px"><button class="btn" id="numVar" title="Changes the numbers in predict-the-output programs and re-runs them: same question and level, new answer. Instant, no AI.">${ico('rotate', 14)} Make number variants</button>
        <button class="btn" id="poolBtn">${ico('sparkles', 14)} Add 3 AI variants</button></div></div>${c.questions.length < c.draw * 2 ? `<div class="notice warn small" style="margin-top:10px">With only ${c.questions.length} questions for ${c.draw} per student, many students get the same ones. Aim for at least ${c.draw * 3}.</div>` : ''}</section>` : ''}
      <section class="card pad"><dl class="kv"><dt>Schedule</dt><dd>${fmtTime(c.start_at)} ${ico('right')} ${fmtTime(c.end_at)} (${fmtMins(c.duration_min)}${c.extra_min ? ` + ${c.extra_min} extended` : ''}${c.paused_total_sec ? `, paused ${fmtDur(c.paused_total_sec)}` : ''})${c.auto_start ? ' · auto-start' : ''}</dd>
        <dt>Joining</dt><dd>${c.registration_mode === 'open' ? 'Students join themselves' : 'Organisers add students'}${c.allow_late_join ? ', also after the start' : ', until the start'}</dd>
        <dt>Questions</dt><dd>${c.draw ? `${c.draw} per student from ${c.questions.length}` : `All ${c.questions.length} for everyone`}${c.shuffle ? ' · shuffled per student' : ''}</dd>
        <dt>Student link</dt><dd class="row" style="gap:6px"><input class="input mono small" readonly id="shareLink" value="${esc(location.origin + '/#/c/' + encodeURIComponent(c.code))}" style="flex:1;min-width:0" onclick="this.select()"><button class="btn sm" type="button" onclick="const i=document.getElementById('shareLink');i.select();document.execCommand('copy');toast('Link copied: share it with students','ok')">${ico('copy', 13)} Copy</button></dd>
        <dt>Lockdown</dt><dd>${ldText(c.lockdown)}</dd>
        <dt>Eligibility</dt><dd>Years ${c.eligible_years.join(', ') || 'all'} · ${c.eligible_departments.join(', ') || 'all departments'}</dd><dt>Languages</dt><dd>${c.languages.map(langName).join(', ')}</dd>
        <dt>Scoring</dt><dd>${sc.mode === 'partial' ? 'Partial marks' : 'All-or-nothing'} · tie-break ${esc(sc.tie_break)}${sc.negative_marks ? ` · −${sc.negative_marks}/wrong` : ''}${sc.first_blood_bonus ? ` · +${sc.first_blood_bonus} first solve` : ''}${sc.violation_pct ? ` · −${sc.violation_pct}% per fair-play warning` : ''}</dd>
        <dt>Results</dt><dd>${c.results_published_at ? `<span class="badge green">published ${ago(c.results_published_at)}</span>` : { live: 'Live leaderboard', after_end: 'Shown automatically at the end', hidden: '<span class="badge amber">Waiting for you to publish</span>' }[c.leaderboard_visibility]}${c.show_test_counts ? ' · test counts visible' : ''}</dd><dt>Fair play</dt><dd>${esc(c.paste_policy)}</dd>
        <dt>Semester</dt><dd>${c.semester ? `${esc(c.semester.name)} · ${c.semester.points} pts` : '<span class="muted">not counted towards a semester</span>'}</dd><dt>Created by</dt><dd>${esc(c.creator || '—')} · ${fmtDate(c.created_at)}</dd></dl></section>
      <section class="card"><div class="card-head"><h3>Announcements</h3></div><div class="card-body"><form class="row" id="af"><input class="input" name="title" placeholder="Announcement to all participants" required maxlength="200"><input class="input" name="body" placeholder="Details (optional)" maxlength="5000"><button class="btn primary">Post</button></form></div>
        ${c.announcements.length ? `<table class="table"><tbody>${c.announcements.map((a) => `<tr><td><b>${esc(a.title)}</b>${a.body ? `<div class="small muted">${esc(a.body)}</div>` : ''}</td><td class="small muted nowrap">${fmtTime(a.created_at)}</td><td class="right"><button class="btn xs danger" data-da="${a.id}">Delete</button></td></tr>`).join('')}</tbody></table>` : ''}</section></div>
    <div class="stack">${c.status !== 'draft' && c.status !== 'cancelled' ? `<section class="card pad resultscard"><h3>Results</h3>
        <div class="row" style="gap:8px;margin-bottom:10px">${c.results_published_at ? `<span class="badge green">${ico('check', 12)} Published ${ago(c.results_published_at)}</span>`
          : c.results_at ? `<span class="badge blue">${ico('clock', 12)} Scheduled for ${fmtTime(c.results_at)}</span>` : c.leaderboard_visibility === 'live' ? '<span class="badge violet">Live leaderboard</span>'
          : c.leaderboard_visibility === 'after_end' ? '<span class="badge">Shown when it ends</span>' : '<span class="badge amber">Private until you publish</span>'}</div>
        <div class="small muted" style="margin-bottom:10px">${c.results_published_at ? 'Students can see their scores, ranks and the leaderboard.' : ['ended', 'finalized', 'archived'].includes(c.status) ? 'The round has ended. Release the results now or at a time you choose.' : 'Students do not see scores or ranks until you release them. You can set the release time now.'}</div>
        <label class="check small" style="margin-bottom:10px"><input type="checkbox" id="explainT" ${c.explain ? 'checked' : ''}> Show students how ranks were decided (rules + a reason on each row)</label>
        <div class="row wrap" style="gap:8px">${c.results_published_at ? `<button class="btn sm" id="unpub2">${ico('eye', 13)} Hide results</button>`
          : `${['ended', 'finalized', 'archived'].includes(c.status) ? `<button class="btn success sm" id="pub2">${ico('megaphone', 13)} Publish now</button>` : ''}<button class="btn sm" id="sched">${ico('clock', 13)} ${c.results_at ? 'Change time' : 'Schedule release'}</button>`}</div></section>` : ''}
      ${['paused', 'ended', 'finalized'].includes(c.status) ? `<section class="card pad"><h3>Saved work</h3><div class="small muted" style="margin-bottom:10px">If a PC, browser or the network failed during the round, grade what each student had in the editor (saved every 30 seconds) where they had not submitted it.</div><button class="btn sm" id="gradeSaved">${ico('rotate', 13)} Grade saved answers</button></section>` : ''}
      <section class="card pad"><h3>At a glance</h3><div class="grid g2"><div><div class="stat" style="padding:0"><div class="k">Participants</div><div class="v">${c.participant_count}</div></div></div><div><div class="stat" style="padding:0"><div class="k">Submissions</div><div class="v">${c.submission_count}</div></div></div></div></section>
      <section class="card pad"><h3>Organisers</h3>${c.admins.map((a) => `<div class="small">${esc(a.name)} <span class="muted">@${esc(a.username)}</span></div>`).join('')}${ME.role === 'super_admin' ? '<button class="btn sm" id="setAdmins" style="margin-top:8px">Manage organisers</button>' : ''}</section></div></div>`;
  if ($('#numVar')) $('#numVar').onclick = () => busy($('#numVar'), async () => { const r = await POST(`${A}/competitions/${c.id}/variants`, { per: 1 });
    toast(`Making a number variant of ${r.questions} question(s). Each answer is checked by running the program. Reload in a minute to see them.`, 'info'); });
  if ($('#poolBtn')) $('#poolBtn').onclick = () => busy($('#poolBtn'), async () => { const r = await POST(`${A}/competitions/${c.id}/pool`, { count: 3 }); toast(r.started ? 'The AI is writing 3 variants. Each is added only if its answer checks out. This page shows the count.' : 'Already working on it.', 'info'); });
  $('#af').onsubmit = (e) => { e.preventDefault(); busy($('button', e.target), async () => { await POST(`${A}/announcements`, { ...formData(e.target), competition_id: c.id }); toast('Announcement posted', 'ok'); goRoute(); }); };
  $$('[data-da]').forEach((b) => b.onclick = async () => { await DEL(`${A}/announcements/${b.dataset.da}`).catch(fail); goRoute(); });
  if ($('#setAdmins')) $('#setAdmins').onclick = async () => {
    const admins = (await GET(`${A}/users`)).filter((a) => a.role !== 'reviewer');
    const cur = new Set(c.admins.map((a) => a.id));
    modal({ title: 'Organisers of this competition', body: `<div class="stack tight" id="ad">${admins.map((a) => chk('u', cur.has(a.id), `${esc(a.name)} <span class="muted small">${ROLE[a.role]}</span>`, `value="${a.id}" data-multi="1" data-num="1"`)).join('')}</div>`,
      actions: [{ label: 'Cancel' }, { label: 'Save', kind: 'primary', onClick: async (m) => { await PUT(`${A}/competitions/${c.id}/admins`, { user_ids: formData($('#ad', m)).u || [] }); toast('Saved', 'ok'); goRoute(); } }] });
  };
}

async function compQuestions(T, c) {
  $('#hdrActions').innerHTML = '';
  let list = c.questions.map((q) => ({ question_id: q.question_id, marks: q.marks, title: q.title, version_no: q.version_no, validated: !!q.validated_at, status: q.q_status, latest: q.latest_version_id === q.version_id, difficulty: q.difficulty }));
  const editable = c.status === 'draft';
  const draw = () => {
    T.innerHTML = `${editable ? '' : `<div class="notice small" style="margin-bottom:12px">Questions are pinned to the exact versions shown. Move the competition back to draft to change them.</div>`}
      <div class="card"><div class="card-head"><h3>Question set</h3><div class="row"><span class="small muted">Total marks: <b>${list.reduce((s, q) => s + Number(q.marks || 0), 0)}</b></span>${editable ? '<button class="btn sm" id="addQ">+ Add from bank</button><button class="btn primary sm" id="saveQ">Save question set</button>' : ''}</div></div>
      <div class="card-body stack tight">${list.length ? list.map((q, i) => `<div class="qrow"><b>${String.fromCharCode(65 + i)}</b><div><a href="#/questions/${q.question_id}"><b>${esc(q.title)}</b></a> <span class="tiny muted">v${q.version_no}</span> ${diffBadge(q.difficulty)} ${qstatus(q.status)} ${q.validated ? '<span class="badge green">validated</span>' : '<span class="badge amber">needs validation</span>'}</div>
        <input class="input" type="number" min="1" value="${q.marks}" data-mk="${i}" ${editable ? '' : 'disabled'} title="Marks">
        <div class="row" style="gap:2px">${editable ? `<button class="btn xs ghost" data-up="${i}" title="Move up">${ico('up')}</button><button class="btn xs ghost" data-rm="${i}" title="Remove">${ico('x')}</button>` : ''}</div></div>`).join('') : empty('No questions yet', editable ? 'Add approved questions from the question bank.' : '')}</div></div>`;
    $$('[data-mk]').forEach((x) => x.oninput = () => { list[x.dataset.mk].marks = Number(x.value); });
    $$('[data-up]').forEach((b) => b.onclick = () => { const i = Number(b.dataset.up); if (i) { [list[i - 1], list[i]] = [list[i], list[i - 1]]; draw(); } });
    $$('[data-rm]').forEach((b) => b.onclick = () => { list.splice(Number(b.dataset.rm), 1); draw(); });
    if ($('#saveQ')) $('#saveQ').onclick = () => busy($('#saveQ'), async () => { await PUT(`${A}/competitions/${c.id}/questions`, { questions: list.map((q) => ({ question_id: q.question_id, marks: q.marks })) }); toast('Question set saved', 'ok'); goRoute(); });
    if ($('#addQ')) $('#addQ').onclick = async () => {
      const bank = await GET(`${A}/questions`);
      const have = new Set(list.map((q) => q.question_id));
      const m = modal({ title: 'Add questions from the bank', size: 'wide', body: `<input class="input" id="qs" placeholder="Search title or tag" style="margin-bottom:10px"><div id="ql" style="max-height:60vh;overflow:auto"></div>`,
        actions: [{ label: 'Done', kind: 'primary' }] });
      const drawBank = () => {
        const s = $('#qs', m.el).value.toLowerCase();
        $('#ql', m.el).innerHTML = `<table class="table"><thead><tr><th>Question</th><th>Category</th><th>Difficulty</th><th>Status</th><th class="num">Tests</th><th></th></tr></thead><tbody>${bank.filter((q) => !s || q.title.toLowerCase().includes(s) || q.tags.join(' ').toLowerCase().includes(s)).map((q) => `<tr>
          <td><b>${esc(q.title)}</b><div class="tiny muted">used in ${q.used} competition(s)</div></td><td class="small">${esc(q.category)}</td><td>${diffBadge(q.difficulty)}</td><td>${qstatus(q.status)} ${q.validated_at ? '' : '<span class="badge amber">unvalidated</span>'}</td><td class="num">${q.tests}</td>
          <td class="right">${have.has(q.id) ? '<span class="small muted">Added</span>' : `<button class="btn xs primary" data-add="${q.id}">Add</button>`}</td></tr>`).join('')}</tbody></table>`;
        $$('[data-add]', m.el).forEach((b) => b.onclick = () => { const q = bank.find((x) => x.id == b.dataset.add); list.push({ question_id: q.id, marks: q.max_marks, title: q.title, version_no: q.version_no, validated: !!q.validated_at, status: q.status, difficulty: q.difficulty, latest: true }); have.add(q.id); draw(); drawBank(); });
      };
      $('#qs', m.el).oninput = drawBank; drawBank();
    };
  };
  draw();
}

async function compParticipants(T, c) {
  $('#hdrActions').innerHTML = `<button class="btn" id="addP">+ Assign participants</button><button class="btn" id="impP">Import CSV</button><a class="btn" href="${A}/reports/participants.csv?competition_id=${c.id}">Export CSV</a>`;
  const rows = await GET(`${A}/competitions/${c.id}/participants`);
  const draw = (q = '') => {
    const list = rows.filter((r) => !q || `${r.name} ${r.participant_id} ${r.usn} ${r.department}`.toLowerCase().includes(q));
    $('#pt').innerHTML = list.length ? `<table class="table"><thead><tr><th>Participant</th><th>Dept / Year</th><th>Joined</th><th>Status</th><th class="num">Subs</th><th class="num">AC</th><th>Last IP</th><th></th></tr></thead><tbody>${list.map((r) => `<tr>
      <td>${r.online ? '<span style="color:var(--green)" title="online">' + ico('dot') + '</span> ' : ''}<b>${esc(r.name)}</b><div class="tiny muted mono">${esc(r.participant_id)} · ${esc(r.usn)}</div></td><td class="small">${esc(r.department)} · Y${r.year}${r.section ? ' · ' + esc(r.section) : ''}</td>
      <td class="small muted">${fmtTime(r.joined_at)}<div class="tiny">${esc(r.source)}</div></td><td>${r.status === 'disqualified' ? '<span class="badge red">disqualified</span>' : r.finished_at ? `<span class="badge blue" title="Final submission ${fmtTime(r.finished_at)}">${ico('flag', 12)} finished</span>` : '<span class="badge green">registered</span>'}</td>
      <td class="num">${r.submissions}</td><td class="num">${r.accepted}</td><td class="small mono muted">${esc(r.last_login_ip || '')}</td>
      <td class="right nowrap"><button class="btn xs" data-act="${r.user_id}">Activity</button> ${r.status === 'disqualified' ? `<button class="btn xs" data-st="registered" data-u="${r.user_id}">Reinstate</button>` : `<button class="btn xs danger" data-st="disqualified" data-u="${r.user_id}">Disqualify</button>`} <button class="btn xs ghost" data-st="removed" data-u="${r.user_id}" title="Remove">${ico('x')}</button></td></tr>`).join('')}</tbody></table>`
      : empty(rows.length ? 'No match' : 'No participants yet', 'Students join themselves while registration is open, or you can assign them.');
    $$('[data-act]', T).forEach((b) => b.onclick = () => activityModal(c, Number(b.dataset.act)));
    $$('[data-st]', T).forEach((b) => b.onclick = async () => {
      const st = b.dataset.st;
      if (!(await confirmBox(st === 'removed' ? 'Remove this participant from the competition?' : st === 'disqualified' ? 'Disqualify this participant? They are hidden from the leaderboard and cannot submit.' : 'Reinstate this participant?', { danger: st !== 'registered' }))) return;
      await POST(`${A}/competitions/${c.id}/participants/${b.dataset.u}`, { status: st }).then(() => { toast('Updated', 'ok'); goRoute(); }).catch(fail);
    });
  };
  T.innerHTML = `<div class="card"><div class="card-head"><input class="input" id="pq" placeholder="Search name, ID, USN, department" style="max-width:340px"><span class="small muted">${rows.filter((r) => r.online).length} online now</span></div><div class="table-wrap" id="pt"></div></div>`;
  $('#pq').oninput = (e) => draw(e.target.value.toLowerCase()); draw();
  $('#addP').onclick = () => modal({ title: 'Assign participants', body: `<p class="small muted">Paste participant IDs or USNs, one per line or comma separated. Duplicates are ignored.</p><textarea class="textarea mono" id="ids" rows="8"></textarea>`,
    actions: [{ label: 'Cancel' }, { label: 'Assign', kind: 'primary', onClick: async (m) => { const r = await POST(`${A}/competitions/${c.id}/participants`, { identifiers: $('#ids', m).value.split(/[\s,]+/).filter(Boolean) }); toast(`${r.added} added${r.missing.length ? `, ${r.missing.length} not found: ${r.missing.slice(0, 5).join(', ')}` : ''}`, r.missing.length ? 'err' : 'ok'); goRoute(); } }] });
  $('#impP').onclick = () => importCsvModal(c.id);
}

async function activityModal(c, uid) {
  const d = await GET(`${A}/competitions/${c.id}/activity/${uid}`);
  const snaps = d.events.filter((e) => e.type === 'snapshot');
  const evs = d.events.filter((e) => e.type !== 'snapshot' && e.type !== 'heartbeat');
  const desc = (e) => { const x = e.detail || {}; switch (e.type) {
    case 'paste': return `${x.chars} chars, ${x.lines} lines${x.internal ? ' (from inside the page)' : ' <b style="color:var(--red)">from outside</b>'}${x.preview ? `<pre style="max-height:120px;margin-top:4px">${esc(x.preview)}</pre>` : ''}`;
    case 'paste_blocked': return `<b style="color:var(--red)">blocked</b> ${x.chars} chars<pre style="max-height:120px;margin-top:4px">${esc(x.preview || '')}</pre>`;
    case 'copy': case 'cut': return `${x.chars} chars from ${esc(x.from)}`; case 'keystrokes': return `${x.n} keys in 30s`; case 'lang_change': return esc(langName(x.language));
    case 'submit': case 'run': return `#${x.submission} · ${langName(x.language)} · ${x.chars} chars`; default: return x && Object.keys(x).length ? `<span class="mono tiny">${esc(JSON.stringify(x).slice(0, 160))}</span>` : ''; } };
  const tcol = { paste: 'amber', paste_blocked: 'red', tab_hidden: 'red', tab_visible: 'green', blur: '', copy: 'blue', submit: 'violet', run: '', violation: 'red', auto_finished: 'red', reopened: 'green' };
  const m = modal({ title: `Activity · ${d.person.name}`, size: 'xwide', body: `<div class="row wrap small muted" style="margin-bottom:12px"><span class="mono">${esc(d.person.participant_id || '')}</span><span>${esc(d.person.usn || '')}</span><span>${esc(d.person.department || '')}</span><span>Last login IP ${esc(d.person.last_login_ip || '—')}</span></div>
    <div class="tabs" id="at"><button class="on" data-t="tl">Timeline (${evs.length})</button><button data-t="sn">Code replay (${snaps.length} snapshots)</button><button data-t="sb">Submissions (${d.submissions.length})</button></div><div id="ab" style="margin-top:12px"></div>`, actions: [...(d.finished_at ? [{ label: 'Reopen test', onClick: async () => { if (!await confirmBox(`Let ${d.person.name} continue the test? Their warning count starts again from zero.`, { ok: 'Reopen' })) return false; await POST(`${A}/competitions/${c.id}/participants/${uid}/reopen`); toast('Reopened: the student\'s screen opens again within a few seconds', 'ok'); } }] : []), { label: 'Close', kind: 'primary' }] });
  const show = (t) => {
    $$('#at button', m.el).forEach((b) => b.classList.toggle('on', b.dataset.t === t));
    const ab = $('#ab', m.el);
    if (t === 'tl') ab.innerHTML = evs.length ? `<div class="timeline">${evs.map((e) => `<div class="ev"><span class="mono muted">${fmtClock(e.created_at)}</span><span><span class="badge ${tcol[e.type] ?? ''}">${esc(e.type.replace('_', ' '))}</span></span><span>${desc(e)} <span class="tiny faint">${esc(e.ip || '')}</span></span></div>`).join('')}</div>` : empty('No activity recorded');
    if (t === 'sn') {
      ab.innerHTML = snaps.length ? `<div class="row" style="margin-bottom:8px"><input type="range" id="sr" min="0" max="${snaps.length - 1}" value="${snaps.length - 1}" style="flex:1"><span class="mono small" id="sl"></span></div><pre id="sc" style="max-height:55vh"></pre>` : empty('No code snapshots yet', 'Snapshots are saved every 30 seconds while the participant edits.');
      if (snaps.length) { const upd = () => { const s = snaps[$('#sr', m.el).value]; $('#sl', m.el).textContent = `${fmtClock(s.created_at)} · Q${s.question_id} · ${langName(s.detail?.language)}`; $('#sc', m.el).textContent = s.detail?.code || ''; }; $('#sr', m.el).oninput = upd; upd(); }
    }
    if (t === 'sb') ab.innerHTML = d.submissions.length ? d.submissions.map((s) => `<details style="border-bottom:1px solid var(--border);padding:6px 0"><summary class="row" style="cursor:pointer"><span class="mono small">#${s.id}</span>${vbadge(s.verdict, 'done')}<span class="small">${langName(s.language)} · score ${s.score}</span><span class="small muted">${fmtClock(s.created_at)}</span></summary><pre style="max-height:300px">${esc(s.code)}</pre></details>`).join('') : empty('No submissions');
  };
  $$('#at button', m.el).forEach((b) => b.onclick = () => show(b.dataset.t)); show('tl');
}

async function compMonitor(T, c) {
  $('#hdrActions').innerHTML = (ACTIONS[c.status] || []).filter(([a]) => ['pause', 'resume', 'extend', 'end', 'start'].includes(a)).map(([a, l, k]) => `<button class="btn ${k}" data-act="${a}">${l}</button>`).join('');
  $$('[data-act]').forEach((b) => b.onclick = () => doAction(c, b.dataset.act).catch(fail));
  const load = async () => {
    const d = await GET(`${A}/competitions/${c.id}/monitor`).catch(() => null);
    if (!d || !$('#tab')) return;
    const qmap = Object.fromEntries(c.questions.map((q, i) => [q.question_id, String.fromCharCode(65 + i)]));
    T.innerHTML = `<div class="grid g4">${[['Status', badge(d.display_status)], ['Time left', `<span class="mono">${fmtDur(d.remaining_sec)}</span>`], ['Online now', `${d.online} / ${d.participants}<div class="tiny muted">${d.finished} finished</div>`], ['Judge queue', `${d.queue} · ${d.last_5min} subs in 5 min`]].map(([k, val]) => `<div class="card stat"><div class="k">${k}</div><div class="v" style="font-size:20px">${val}</div></div>`).join('')}</div>
      <div class="grid g3" style="margin-top:16px">${c.questions.map((q, i) => { const pq = d.per_question.find((x) => x.question_id === q.question_id) || { attempts: 0, solvers: 0 }; return `<div class="card pad"><div class="small muted">${String.fromCharCode(65 + i)} · ${esc(q.title)}</div><div class="row between"><b style="font-size:20px">${pq.solvers}</b><span class="small muted">solved · ${pq.attempts} attempts</span></div><div class="bar"><i style="width:${d.participants ? pq.solvers / d.participants * 100 : 0}%"></i></div></div>`; }).join('')}</div>
      <div class="grid g2" style="margin-top:16px;align-items:start"><section class="card"><div class="card-head"><h3>Submission feed</h3><span class="tiny muted">auto-refresh 5s</span></div>${d.feed.length ? `<table class="table"><tbody>${d.feed.map((s) => `<tr class="click" data-sub="${s.id}"><td class="mono small">${fmtClock(s.created_at)}</td><td>${esc(s.name)}</td><td>${qmap[s.question_id] || ''}</td><td class="small">${langName(s.language)}</td><td>${vbadge(s.verdict, s.status)}</td><td class="num">${s.score}</td></tr>`).join('')}</tbody></table>` : empty('No submissions yet')}</section>
      <section class="card"><div class="card-head"><h3>Fair-play flags</h3><a class="small" href="#/competitions/${c.id}?t=integrity">Full report ${ico('right')}</a></div>${d.flags.length ? `<table class="table"><tbody>${d.flags.map((f) => { const x = JSON.parse(f.detail || '{}') || {}; return `<tr class="click" data-u="${f.user_id}"><td class="mono small">${fmtClock(f.created_at)}</td><td>${esc(f.name)}</td><td><span class="badge ${f.type.includes('paste') ? 'amber' : 'red'}">${esc(f.type.replace('_', ' '))}</span></td><td class="small muted">${x.chars ? x.chars + ' chars' + (x.internal ? ' (internal)' : '') : ''}</td></tr>`; }).join('')}</tbody></table>` : empty('No flags')}</section></div>`;
    $$('[data-sub]', T).forEach((tr) => tr.onclick = () => subModal(Number(tr.dataset.sub)));
    $$('[data-u]', T).forEach((tr) => tr.onclick = () => activityModal(c, Number(tr.dataset.u)));
  };
  T.innerHTML = skeleton(5); await load(); every(load, 5000);
}

async function compIntegrity(T, c) {
  $('#hdrActions').innerHTML = `<button class="btn" id="sim">Check code similarity</button><a class="btn" href="${A}/reports/integrity.csv?competition_id=${c.id}">Export CSV</a>`;
  const rows = await GET(`${A}/competitions/${c.id}/integrity`);
  T.innerHTML = `<div class="notice small" style="margin-bottom:12px">Risk combines large external pastes, blocked pastes, tab switches, time away, full-screen exits, devices with internet access and shared IP addresses. It is a pointer for review, not proof. Open a participant to see the full timeline, paste contents and code replay.</div>
    <div class="card"><div class="table-wrap">${rows.length ? `<table class="table"><thead><tr><th>Participant</th><th>Risk</th><th class="num">Pastes</th><th class="num">Large ext.</th><th class="num">Blocked</th><th class="num">Copies</th><th class="num">Tab switches</th><th class="num">Away</th><th class="num">Internet</th><th class="num">AI uses</th><th class="num">Keys</th><th>IPs</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="click" data-u="${r.user_id}"><td><b>${esc(r.name)}</b><div class="tiny muted mono">${esc(r.participant_id || '')}</div></td><td><span class="risk"><i style="width:${r.risk}%;background:${r.risk > 50 ? 'var(--red)' : r.risk > 20 ? 'var(--amber)' : 'var(--green)'}"></i></span> <b class="small">${r.risk}</b></td>
      <td class="num">${r.pastes}</td><td class="num">${r.large_pastes ? `<b style="color:var(--red)">${r.large_pastes}</b>` : 0}</td><td class="num">${r.blocked_pastes}</td><td class="num">${r.copies}</td><td class="num">${r.tab_switches}</td><td class="num">${fmtDur(r.away_sec)}</td><td class="num">${r.internet ? `<b style="color:var(--red)">${ico('wifi', 13)} ${r.internet}</b>` : '0'}</td><td class="num">${r.ai_uses}</td><td class="num">${r.keystrokes}</td>
      <td class="small mono">${r.ips.map(esc).join('<br>')}${r.shared_ip_with.length ? `<div class="tiny" style="color:var(--red)">shared with ${esc(r.shared_ip_with.slice(0, 3).join(', '))}</div>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('No participants')}</div></div><div id="simres" style="margin-top:16px"></div>`;
  $$('[data-u]', T).forEach((tr) => tr.onclick = () => activityModal(c, Number(tr.dataset.u)));
  $('#sim').onclick = () => busy($('#sim'), async () => {
    const pairs = await GET(`${A}/competitions/${c.id}/similarity`);
    $('#simres').innerHTML = `<section class="card"><div class="card-head"><h3>Similar submissions (≥ 85% after removing comments and whitespace)</h3></div>${pairs.length ? `<table class="table"><thead><tr><th>Question</th><th>Language</th><th>Participant A</th><th>Participant B</th><th class="num">Similarity</th></tr></thead><tbody>${pairs.map((p) => `<tr><td>${esc(p.question)}</td><td>${langName(p.language)}</td><td><a href="#" data-u="${p.a.user_id}">${esc(p.a.name)}</a></td><td><a href="#" data-u="${p.b.user_id}">${esc(p.b.name)}</a></td><td class="num"><b style="color:${p.similarity > 95 ? 'var(--red)' : 'var(--amber)'}">${p.similarity}%</b></td></tr>`).join('')}</tbody></table>` : empty('No suspicious pairs found')}</section>`;
    $$('#simres [data-u]').forEach((a) => a.onclick = (e) => { e.preventDefault(); activityModal(c, Number(a.dataset.u)); });
  });
}

async function compLeaderboard(T, c) {
  $('#hdrActions').innerHTML = `<a class="btn" href="${A}/reports/scores.csv?competition_id=${c.id}">Export rankings CSV</a>${['live', 'paused', 'ended'].includes(c.status) ? '<button class="btn" id="rj">Re-judge errors</button>' : ''}`;
  if ($('#rj')) $('#rj').onclick = async () => { const r = await POST(`${A}/competitions/${c.id}/rejudge?only_errors=true`).catch(fail); if (r) toast(`${r.queued} submission(s) re-queued`, 'ok'); };
  const load = async () => {
    const d = await GET(`${A}/competitions/${c.id}/leaderboard`).catch(() => null);
    if (!d || !$('#tab')) return;
    T.innerHTML = `<div class="pubbar ${d.results_visible ? 'on' : ''}"><span class="lsi sm">${ico(d.results_visible ? 'eye' : 'lock', 15)}</span><div class="grow"><b>${d.results_visible ? 'Students can see these results' : 'Results are hidden from students'}</b>
        <div class="tiny muted">${d.results_published_at ? 'Published ' + fmtTime(d.results_published_at) : d.visibility === 'live' ? 'Live leaderboard' : d.visibility === 'after_end' ? 'Shown automatically when the round ends' : 'You publish them manually after checking submissions'} · ${d.frozen ? 'final frozen snapshot' : 'computed live from submissions'} · tie-break ${esc(d.tie_break)}</div></div>
        ${['ended', 'finalized', 'archived'].includes(d.status) ? (d.results_published_at ? `<button class="btn sm" id="lbUnpub">Hide results</button>` : `<button class="btn sm success" id="lbPub">${ico('megaphone', 13)} Publish results</button>`) : '<span class="tiny muted">Publishing is available once the round ends.</span>'}</div>
      <div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>Rank</th><th>Participant</th><th class="num">Score</th><th class="num">Solved</th><th class="num">Penalty</th><th class="num" title="Fair-play warnings and marks deducted">Warnings</th>${d.questions.map((q) => `<th class="num" title="${esc(q.title)}">${q.label}<div class="tiny faint">${q.marks}</div></th>`).join('')}</tr></thead><tbody>
      ${d.rows.length ? d.rows.map((r) => `<tr><td>${rankBadge(r.rank)}</td><td><b>${esc(r.name)}</b><div class="tiny muted mono">${esc(r.participant_id || '')} · ${esc(r.department || '')}</div></td><td class="num"><b>${r.score}</b></td><td class="num">${r.solved}</td><td class="num muted">${r.penalty}</td><td class="num">${r.warnings ? `<span style="color:var(--red);font-weight:650">${r.warnings}</span><div class="tiny faint">−${r.deducted}</div>` : '<span class="faint">0</span>'}</td>
        ${d.questions.map((q) => { const x = r.per[q.id]; return `<td class="num">${x ? `<span style="color:${x.ac ? 'var(--green)' : x.score > 0 ? 'var(--amber)' : 'var(--red)'};font-weight:650">${x.score}</span><div class="tiny faint">${x.tries}×</div>` : '<span class="faint">·</span>'}</td>`; }).join('')}</tr>`).join('') : `<tr><td colspan="${6 + d.questions.length}">${empty('No scores yet')}</td></tr>`}</tbody></table></div></div>`;
  };
  T.addEventListener('click', async (e) => {
    const b = e.target.closest('#lbPub, #lbUnpub'); if (!b) return;
    const on = b.id === 'lbPub';
    if (!(await confirmBox(on ? 'Publish these results to all participants now?' : 'Hide the results from students again?', { ok: on ? 'Publish results' : 'Hide results' }))) return;
    await POST(`${A}/competitions/${c.id}/results`, { publish: on }).then(() => { toast(on ? 'Results published' : 'Results hidden', 'ok'); load(); }).catch(fail);
  });
  T.innerHTML = skeleton(5); await load(); if (['live', 'paused'].includes(c.status)) every(load, 10000);
}

function reportButtons(T, c) {
  $('#hdrActions').innerHTML = '';
  const r = [['participants', 'Participant list', 'Everyone registered, with department, status and submission counts.'], ['scores', 'Final rankings & scores', 'Rank, score, solved and per-question marks. Suitable for college records.'],
    ['questions', 'Question-wise performance', 'Attempts, solvers, average score and verdict breakdown per question.'], ['submissions', 'Submission statistics', 'Every submission with verdict, time, memory and IP.'], ['integrity', 'Fair-play report', 'Paste, tab-switch and IP signals per participant.']];
  T.innerHTML = `<div class="grid g2">${r.map(([k, t, d]) => `<div class="card pad row between"><div><b>${t}</b><div class="small muted">${d}</div></div><a class="btn" href="${A}/reports/${k}.csv?competition_id=${c.id}">Download CSV</a></div>`).join('')}</div>`;
}

// ================= questions =================
async function questions(p) {
  const qy = p.query;
  const v = view('Question Bank', skeleton(5), 'questions');
  const rows = await GET(`${A}/questions?` + new URLSearchParams({ q: qy.q || '', category: qy.category || '', difficulty: qy.difficulty || '', status: qy.status || '' }));
  v.innerHTML = ph('Question bank', 'Reusable across competitions. Editing a question that was already used creates a new version, so past results never change.',
    `${ME.role !== 'reviewer' ? '<a class="btn primary" href="#/questions/new">+ New question</a>' : ''}${can('pdf') ? '<a class="btn" href="#/pdf">Import from PDF</a>' : ''}${can('ai') ? '<a class="btn" href="#/ai">AI assistant</a>' : ''}`) +
    `<form class="card pad row wrap" id="qf" style="margin-bottom:14px">${inp('q', qy.q, 'placeholder="Search title, tag or text" style="max-width:300px"')}${sel('category', [['', 'All categories'], ...META.categories || []].map((x) => Array.isArray(x) ? x : [x, x]), qy.category, 'style="width:auto"')}
      ${sel('difficulty', [['', 'All levels'], 'Easy', 'Intermediate', 'Challenging'], qy.difficulty, 'style="width:auto"')}${sel('status', [['', 'Active'], ['draft', 'Draft'], ['approved', 'Approved'], ['archived', 'Archived']], qy.status, 'style="width:auto"')}<button class="btn">Filter</button></form>
    <div class="card">${rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Question</th><th>Category</th><th>Difficulty</th><th>Status</th><th class="num">Tests</th><th class="num">Used</th><th>Updated</th></tr></thead><tbody>${rows.map((q) => `<tr class="click" data-id="${q.id}">
      <td><b>${esc(q.title)}</b> <span class="tiny muted">v${q.version_no}</span>${q.practice ? ' <span class="badge violet">practice</span>' : ''}<div class="row wrap" style="gap:4px;margin-top:3px">${q.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join('')}<span class="tiny muted">${esc(q.source)} · ${esc(q.creator || '')}</span></div></td>
      <td class="small">${esc(q.category)}</td><td>${diffBadge(q.difficulty)}</td><td>${qstatus(q.status)}<div>${q.validated_at ? '<span class="tiny" style="color:var(--green)">' + ico('check') + ' validated</span>' : '<span class="tiny" style="color:var(--amber)">not validated</span>'}</div></td>
      <td class="num">${q.tests}<div class="tiny muted">${q.hidden} hidden</div></td><td class="num">${q.used}</td><td class="small muted">${ago(q.updated_at)}</td></tr>`).join('')}</tbody></table></div>` : empty('No questions found', 'Create one manually, import a PDF or use the AI assistant.')}</div>`;
  $('#qf').onsubmit = (e) => { e.preventDefault(); nav('/questions?' + new URLSearchParams(formData(e.target))); };
  $$('[data-id]', v).forEach((tr) => tr.onclick = () => nav('/questions/' + tr.dataset.id));
}

function questionBodyFrom(form, Q, refs) {
  const f = formData(form);
  return { title: f.title, slug: f.slug || null, category: f.category, difficulty: f.difficulty, tags: (f.tags || '').split(',').map((x) => x.trim()).filter(Boolean), statement: f.statement, input_format: f.input_format,
    output_format: f.output_format, constraints: f.constraints, languages: f.languages?.length ? f.languages : Object.keys(META.languages).filter((k) => k !== 'prompt' && k !== 'answer'), time_limit_ms: f.time_limit_ms, memory_mb: f.memory_mb, max_marks: f.max_marks, practice: !!f.practice, references: refs,
    qtype: f.qtype || 'standard', starter: Q.version.starter || {},
    options: f.qtype === 'mcq' ? [0, 1, 2, 3].map((i) => (f['opt' + i] || '').trim()).filter(Boolean) : [],
    answer: f.qtype === 'mcq' ? (f['opt' + (form.querySelector('[name=correct]:checked')?.value ?? 0)] || '').trim() : ['output', 'trace'].includes(f.qtype) ? (f.answer || '') : '' };
}

async function questionEditor(p) {
  const id = p ? Number(p.id) : null;
  const tab = p?.query?.t || 'statement';
  const v = view(id ? 'Question' : 'New question', skeleton(6), 'questions');
  const Q = id ? await GET(`${A}/questions/${id}`) : { version: { title: '', statement: '', input_format: '', output_format: '', constraints: '', languages: Object.keys(META.languages).filter((k) => k !== 'prompt' && k !== 'answer'), time_limit_ms: 2000, memory_mb: 256, max_marks: 100, validation: {}, qtype: 'standard', starter: {} }, category: 'Problem Solving', difficulty: 'Easy', tags: [], tests: [], references: {}, status: 'draft', practice: 0 };
  const V = Q.version;
  const tabs = id ? [['statement', 'Statement'], ['tests', `Test cases (${Q.tests.length})`], ['refs', 'Reference & validation'], ['preview', 'Student preview'], ['history', 'Versions & usage']] : [['statement', 'Statement']];
  const val = V.validation || {};
  v.innerHTML = `<a class="small" href="#/questions">${ico('left')} Question bank</a>` + ph(id ? V.title : 'New question', id ? `${qstatus(Q.status)} v${V.version_no} · ${V.validated_at ? '<span style="color:var(--green)">' + ico('check') + ' validated ' + ago(V.validated_at) + '</span>' : '<span style="color:var(--amber)">not validated</span>'} · created by ${esc(Q.creator || '')}${Q.locked ? ' · <span class="badge blue">in use: edits create a new version</span>' : ''}` : 'Fill in the statement, then add test cases and a reference solution.',
    id ? `${Q.status !== 'approved' && ME.can_approve ? '<button class="btn primary" id="approve">Approve</button>' : ''}${Q.status === 'approved' ? '<button class="btn" id="unapprove">Move to draft</button>' : ''}<button class="btn" id="dupq">Duplicate</button>${can('ai') ? '<button class="btn" id="varq">AI variation</button>' : ''}${Q.status !== 'archived' ? '<button class="btn danger" id="arch">Archive</button>' : '<button class="btn" id="unapprove">Restore</button>'}` : '') +
    `<div class="tabs" style="margin-bottom:18px">${tabs.map(([k, l]) => `<a href="#/questions/${id || 'new'}?t=${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</div><div id="qtab"></div>`;
  if (id) {
    const setSt = (s) => async () => { await POST(`${A}/questions/${id}/status`, { status: s }).then(() => { toast('Status: ' + s, 'ok'); goRoute(); }).catch(fail); };
    if ($('#approve')) $('#approve').onclick = setSt('approved');
    if ($('#unapprove')) $('#unapprove').onclick = setSt('draft');
    if ($('#arch')) $('#arch').onclick = async () => { if (await confirmBox('Archive this question? It stays in past competitions but cannot be added to new ones.')) setSt('archived')(); };
    $('#dupq').onclick = async () => { const r = await POST(`${A}/questions/${id}/duplicate`).catch(fail); if (r) { toast('Duplicated', 'ok'); nav('/questions/' + r.id); } };
    if ($('#varq')) $('#varq').onclick = async () => { const r = await POST(`${A}/ai/variation`, { question_id: id }).catch(fail); if (r) { toast('AI is writing a variation. It will appear as a draft in the AI assistant.', 'info'); nav('/ai'); } };
  }
  const T = $('#qtab');
  if (tab === 'statement') {
    T.innerHTML = `<form id="qform" class="grid side2" style="--side:300px;align-items:start;gap:18px"><div class="card pad stack">
      ${fld('Title', inp('title', V.title, 'required maxlength=150'))}
      ${fld('Problem statement', ta('statement', V.statement, 'rows=10'), 'Markdown: **bold**, `code`, ``` blocks ```, - lists')}
      <div class="grid g2">${fld('Input format', ta('input_format', V.input_format, 'rows=4'))}${fld('Output format', ta('output_format', V.output_format, 'rows=4'))}</div>
      ${fld('Constraints', ta('constraints', V.constraints, 'rows=3'))}
      ${!id ? `<div class="notice small">Sample cases are test cases marked <b>sample</b>; add them in the Test cases tab after saving.</div>` : ''}
      <div class="divider"></div>
      <div class="grid g2">${fld('Question type', sel('qtype', [['standard', 'Standard: write the full program'], ['debug', 'Fix the bug: starter code has a bug'], ['complete', 'Complete the code: fill in the missing part'], ['output', 'Predict the output: students type what the code prints'], ['trace', 'Code tracing: students follow the code and type the final value'], ['mcq', 'Multiple choice: students pick one of the options'], ['prompt', 'Prompt challenge: students write an AI prompt (needs the AI model)']], V.qtype || 'standard'))}
        ${fld('Starter code language', sel('stlang', Object.keys(META.languages).map((k) => [k, META.languages[k].name]), 'c', 'id="stlang"'))}</div>
      <div id="mcqbox" class="stack tight"><div class="small" style="font-weight:600">Options <span class="muted" style="font-weight:400">· select the circle next to the correct one</span></div>
        ${[0, 1, 2, 3].map((i) => `<div class="optrow"><input type="radio" name="correct" value="${i}" ${(V.options || [])[i] !== undefined && (V.options || [])[i] === (Q.references.answer || '').trim() || (!Q.references.answer && i === 0) ? 'checked' : ''} aria-label="Option ${i + 1} is correct"><input class="input" name="opt${i}" value="${esc((V.options || [])[i] || '')}" placeholder="Option ${'ABCD'[i]}"></div>`).join('')}
        <div class="hint">Put the code or question in the Problem statement above. In a competition the student's first answer is final.</div></div>
      <div id="ansbox">${fld('Correct answer', ta('answer', Q.references.answer || '', 'rows=3'), 'Exactly what the student must type. Spaces at line ends and blank lines at the end are ignored.')}</div>
      <div id="stbox">${fld('Starter code (optional, per language)', '<textarea class="textarea mono" id="stcode" rows="8" placeholder="Leave empty to give students the normal template. For Fix the bug, paste the buggy program; for Complete the code, mark the gap with a TODO comment."></textarea>')}</div></div>
      <div class="stack"><div class="card pad stack">${fld('Category', sel('category', META.categories, Q.category))}${fld('Difficulty', sel('difficulty', ['Easy', 'Intermediate', 'Challenging'], Q.difficulty))}
        ${fld('Topic tags', inp('tags', Q.tags.join(', '), 'placeholder="arrays, two pointers"'), 'comma separated')}${fld('Slug', inp('slug', Q.slug || '', 'placeholder="auto"'))}
        <div class="grid g2">${fld('Time limit (ms)', inp('time_limit_ms', V.time_limit_ms, 'type=number min=100 max=20000'))}${fld('Memory (MB)', inp('memory_mb', V.memory_mb, 'type=number min=16 max=2048'))}</div>
        ${fld('Default marks', inp('max_marks', V.max_marks, 'type=number min=1'))}${chk('practice', Q.practice, 'Show in student Practice when not in an active competition')}</div>
        <div class="card pad"><div class="small" style="font-weight:550;margin-bottom:6px">Allowed languages</div><div class="stack tight">${Object.entries(META.languages).filter(([k]) => k !== 'answer' && k !== 'prompt').map(([k, l]) => chk('languages', V.languages.includes(k), esc(l.name), `value="${k}" data-multi="1"`)).join('')}</div></div>
        <button class="btn primary lg" id="saveq">${id ? 'Save changes' : 'Create question'}</button></div></form>`;
    if (innerWidth < 1000) $('#qform').style.gridTemplateColumns = '1fr';
    Q.version.starter = { ...(V.starter || {}) };
    let stl = 'c';
    const stash = () => { const v = $('#stcode').value; if (v.trim()) Q.version.starter[stl] = v; else delete Q.version.starter[stl]; };
    $('#stcode').value = Q.version.starter[stl] || '';
    $('#stlang').onchange = (e) => { stash(); stl = e.target.value; $('#stcode').value = Q.version.starter[stl] || ''; };
    $('#stcode').oninput = stash;
    const qt = $('[name=qtype]', T), typeUI = () => { $('#mcqbox').hidden = qt.value !== 'mcq'; $('#ansbox').hidden = !['output', 'trace'].includes(qt.value); $('#stbox').hidden = ['mcq', 'output', 'trace'].includes(qt.value); };
    qt.addEventListener('change', typeUI); typeUI();
    $('#qform').onsubmit = (e) => { e.preventDefault(); busy($('#saveq'), async () => { const b = questionBodyFrom(e.target, Q, Q.references); const r = id ? await PUT(`${A}/questions/${id}`, b) : await POST(`${A}/questions`, b); toast('Saved', 'ok'); nav(`/questions/${r.id}?t=${id ? 'statement' : 'tests'}`); }); };
  } else if (tab === 'tests') testsTab(T, Q);
  else if (tab === 'refs') refsTab(T, Q);
  else if (tab === 'preview') {
    const samples = Q.tests.filter((t) => t.visibility === 'sample');
    T.innerHTML = `<div class="card pad" style="max-width:820px"><h2>${esc(V.title)}</h2><div class="row small muted" style="margin-bottom:12px"><span>${ico('clock')} ${V.time_limit_ms / 1000}s</span><span>${ico('cpu')} ${V.memory_mb} MB</span><span>${V.languages.map(langName).join(', ')}</span></div>
      <div class="prose">${md(V.statement)}<h4>Input format</h4>${md(V.input_format)}<h4>Output format</h4>${md(V.output_format)}${V.constraints ? `<h4>Constraints</h4>${md(V.constraints)}` : ''}</div>
      ${samples.map((s, i) => `<div class="sample"><b class="small">Example ${i + 1}</b><div class="io" style="margin-top:6px"><div><div class="lbl">Input</div><pre>${esc(s.input)}</pre></div><div><div class="lbl">Output</div><pre>${esc(s.expected)}</pre></div></div>${s.explanation ? `<div class="prose small" style="margin-top:6px"><b>Explanation:</b> ${md(s.explanation)}</div>` : ''}</div>`).join('') || '<div class="notice warn">No sample cases yet.</div>'}
      <div class="small muted">${Q.tests.filter((t) => t.visibility === 'hidden').length} hidden test cases (never shown to students).</div></div>`;
  } else if (tab === 'history') {
    T.innerHTML = `<div class="grid g2" style="align-items:start"><section class="card"><div class="card-head"><h3>Versions</h3></div><table class="table"><tbody>${Q.versions.map((x) => `<tr><td><b>v${x.version_no}</b> ${x.id === V.id ? '<span class="badge violet">current</span>' : ''}</td><td>${esc(x.title)}</td><td class="small muted">${fmtTime(x.created_at)}</td><td>${x.validated_at ? '<span class="badge green">validated</span>' : ''}</td><td><button class="btn xs" data-ver="${x.id}">View</button></td></tr>`).join('')}</tbody></table></section>
      <section class="card"><div class="card-head"><h3>Used in competitions</h3></div>${Q.used_in.length ? `<table class="table"><tbody>${Q.used_in.map((u) => `<tr class="click" onclick="nav('/competitions/${u.id}')"><td><b>${esc(u.title)}</b><div class="tiny mono muted">${esc(u.code)}</div></td><td><span class="badge">${esc(u.status)}</span></td><td class="small">v${Q.versions.find((x) => x.id === u.version_id)?.version_no ?? '?'}</td></tr>`).join('')}</tbody></table>` : empty('Not used yet')}</section></div>`;
    $$('[data-ver]', T).forEach((b) => b.onclick = async () => { const x = await GET(`${A}/questions/${id}/versions/${b.dataset.ver}`); modal({ title: `Version ${x.version_no}`, size: 'wide', body: `<h3>${esc(x.title)}</h3><div class="prose">${md(x.statement)}</div><div class="small muted">${x.tests.length} tests · TL ${x.time_limit_ms} ms · ${x.memory_mb} MB · references: ${Object.keys(x.references).map(langName).join(', ') || 'none'}</div>` }); });
  }
}

function testModal(Q, t) {
  const isNew = !t;
  t = t || { input: '', expected: '', visibility: 'hidden', kind: 'normal', weight: 1, explanation: '', description: '' };
  modal({ title: isNew ? 'Add test case' : `Edit test ${t.ordinal}`, size: 'wide', body: `<form class="stack" id="tf"><div class="grid g2">${fld('Input', ta('input', t.input, 'rows=10 class="textarea mono"').replace('class="textarea"', 'class="textarea mono"'))}${fld('Expected output', ta('expected', t.expected, 'rows=10').replace('class="textarea"', 'class="textarea mono"'), 'leave empty to fill from the reference solution')}</div>
    <div class="grid g4">${fld('Visibility', sel('visibility', [['hidden', 'Hidden'], ['sample', 'Sample (shown)']], t.visibility))}${fld('Type', sel('kind', ['normal', 'boundary', 'edge', 'stress'], t.kind))}${fld('Weight', inp('weight', t.weight, 'type=number min=0 max=1000'))}${fld('Organiser note', inp('description', t.description))}</div>
    ${fld('Explanation (samples only, shown to students)', ta('explanation', t.explanation, 'rows=2'))}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', kind: 'primary', onClick: async (m) => { const b = formData($('#tf', m)); isNew ? await POST(`${A}/questions/${Q.id}/tests`, b) : await PUT(`${A}/questions/${Q.id}/tests/${t.id}`, b); toast('Saved. Re-run validation.', 'ok'); goRoute(); } }] });
}
function testsTab(T, Q) {
  T.innerHTML = `<div class="row wrap" style="margin-bottom:12px"><button class="btn primary" id="addT">+ Add test</button><button class="btn" id="bulkT">Bulk import</button><button class="btn" id="genT">${ico('settings')} Generate with script</button>${can('ai') ? '<button class="btn" id="aiT">' + ico('sparkles') + ' AI suggest tests</button>' : ''}<button class="btn" id="valT">Validate with reference</button>
    <span class="small muted grow">${Q.tests.filter((t) => t.visibility === 'sample').length} sample · ${Q.tests.filter((t) => t.visibility === 'hidden').length} hidden · ${Q.tests.filter((t) => !t.expected.trim()).length} without expected output</span></div>
    ${Q.locked ? '<div class="notice small" style="margin-bottom:12px">This version is used by a published competition or by submissions. Your first change creates a new version automatically.</div>' : ''}
    <div class="card">${Q.tests.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>Visibility</th><th>Type</th><th class="num">Weight</th><th>Input</th><th>Expected</th><th>Validated</th><th></th></tr></thead><tbody>${Q.tests.map((t) => `<tr>
      <td class="mono">${t.ordinal}</td><td>${t.visibility === 'sample' ? '<span class="badge violet">sample</span>' : '<span class="badge">hidden</span>'}</td><td class="small">${esc(t.kind)}</td><td class="num">${t.weight}</td>
      <td><pre style="margin:0;max-height:70px;max-width:280px;font-size:12px">${esc(t.input.slice(0, 300))}</pre></td><td><pre style="margin:0;max-height:70px;max-width:220px;font-size:12px">${t.expected.trim() ? esc(t.expected.slice(0, 300)) : '<span style="color:var(--amber)">(empty)</span>'}</pre></td>
      <td>${t.validated ? '<span style="color:var(--green)">' + ico('check') + '</span>' : '<span class="faint">—</span>'}${t.description ? `<div class="tiny muted">${esc(t.description.slice(0, 60))}</div>` : ''}</td>
      <td class="right nowrap"><button class="btn xs" data-e="${t.id}">Edit</button> <button class="btn xs ghost" data-d="${t.id}">${ico('x')}</button></td></tr>`).join('')}</tbody></table></div>` : empty('No test cases yet', 'Add samples (shown to students) and hidden tests (used for scoring).')}</div>`;
  $('#addT').onclick = () => testModal(Q);
  $$('[data-e]', T).forEach((b) => b.onclick = () => testModal(Q, Q.tests.find((t) => t.id == b.dataset.e)));
  $$('[data-d]', T).forEach((b) => b.onclick = async () => { if (await confirmBox('Delete this test case?', { danger: true, ok: 'Delete' })) { await DEL(`${A}/questions/${Q.id}/tests/${b.dataset.d}`).catch(fail); goRoute(); } });
  $('#bulkT').onclick = () => modal({ title: 'Bulk import test cases', size: 'wide', body: `<p class="small muted">Paste blocks like below. Add a line <code>### sample</code> or <code>### hidden</code> to switch visibility for the following tests. Leave output empty to fill it from the reference solution.</p>
    <textarea class="textarea mono" id="bt" rows="16">### hidden\n### input\n5\n1 2 3 4 5\n### output\n15\n### input\n1\n-7\n### output\n-7</textarea><label class="check" style="margin-top:8px"><input type="checkbox" id="rep"> Replace all existing hidden tests</label>`,
    actions: [{ label: 'Cancel' }, { label: 'Import', kind: 'primary', onClick: async (m) => { await POST(`${A}/questions/${Q.id}/tests/bulk`, { text: $('#bt', m).value, replace: $('#rep', m).checked }); toast('Imported', 'ok'); goRoute(); } }] });
  if ($('#aiT')) $('#aiT').onclick = () => modal({ title: 'AI test-case suggestions', body: `<form class="stack" id="af"><div class="small" style="font-weight:550">Kinds</div><div class="row wrap">${['normal', 'boundary', 'edge', 'stress'].map((k) => chk('kinds', k !== 'stress', k, `value="${k}" data-multi="1"`)).join('')}</div>${fld('How many', '<input class="input" type="number" name="count" value="6" min="1" max="20">')}
    <div class="notice small">Only <b>inputs</b> come from the AI. Expected outputs are computed by running your reference solution in the sandbox, then cross-checked by any other reference language.</div></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Generate', kind: 'primary', onClick: async (m) => { const f = formData($('#af', m)); const r = await POST(`${A}/ai/tests`, { question_id: Q.id, kinds: f.kinds, count: f.count }); toast(`${r.added} tests added${r.validation_jobs.length ? '; validating…' : '. Add a reference solution to fill outputs.'}`, 'ok'); nav(`/questions/${Q.id}?t=refs`); } }] });
  $('#valT').onclick = () => runValidation(Q);
  $('#genT').onclick = () => genModal(Q);
}
const GEN_TEMPLATES = {
  'Array of n integers': `import random, sys
seed = int(sys.stdin.readline())          # the platform passes 1, 2, 3 ... (one run per test)
random.seed(seed)
n = random.randint(1, 10) if seed <= 3 else random.randint(1, 1000)   # small cases first, then bigger
if seed == 4: n = 1000                    # a maximum-size case
lo, hi = -10**6, 10**6
print(n)
print(*[random.randint(lo, hi) for _ in range(n)])
`,
  'Single integer': `import random, sys
seed = int(sys.stdin.readline())
random.seed(seed)
edge = [0, 1, 2, 10**9]                   # boundary values first
print(edge[seed - 1] if seed <= len(edge) else random.randint(0, 10**9))
`,
  'Lowercase string': `import random, string, sys
seed = int(sys.stdin.readline())
random.seed(seed)
n = 1 if seed == 1 else random.randint(2, 1000)
print(''.join(random.choice(string.ascii_lowercase) for _ in range(n)))
`,
  'Grid / matrix': `import random, sys
seed = int(sys.stdin.readline())
random.seed(seed)
r, c = random.randint(1, 50), random.randint(1, 50)
print(r, c)
for _ in range(r):
    print(*[random.randint(0, 9) for _ in range(c)])
`,
};
function genModal(Q) {
  const names = Object.keys(GEN_TEMPLATES);
  const m = modal({ title: 'Generate test cases with a script', size: 'wide', body: `<p class="small muted">The platform runs this Python generator in the sandbox once per test, passing the seed (1, 2, 3 …) on standard input. Whatever it prints becomes a hidden test input.
      Expected outputs are then computed by your reference solution and cross-checked by the other references. No AI needed.</p>
    <div class="row wrap" style="margin-bottom:8px">${sel('tpl', names, names[0], 'id="gtpl" style="width:auto"')}<input class="input" type="number" id="gcount" value="10" min="1" max="50" style="width:110px" title="How many tests">
      ${sel('kind', ['normal', 'boundary', 'edge', 'stress'], 'normal', 'id="gkind" style="width:auto"')}</div>
    <textarea class="textarea mono" id="gcode" rows="14">${esc(GEN_TEMPLATES[names[0]])}</textarea><div id="gout" class="small" style="margin-top:8px"></div>`,
    actions: [{ label: 'Cancel' }, { label: 'Generate tests', kind: 'primary', onClick: async (mm) => {
      const r = await POST(`${A}/questions/${Q.id}/generate`, { code: $('#gcode', mm).value, count: Number($('#gcount', mm).value) });
      const out = $('#gout', mm);
      for (let i = 0; i < 300; i++) {
        const st = await Promise.all(r.jobs.map((j) => GET(`${A}/runs/${j}`)));
        const done = st.filter((x) => ['done', 'error'].includes(x.status)).length;
        out.innerHTML = `<span class="spin"></span> Running generator… ${done}/${r.jobs.length}`;
        if (done === r.jobs.length) break;
        await new Promise((x) => setTimeout(x, 700));
      }
      const c = await POST(`${A}/questions/${Q.id}/generate/collect`, { jobs: r.jobs, kind: $('#gkind', mm).value });
      if (!c.added) { out.innerHTML = `<div class="notice err">No tests produced. ${c.failed.slice(0, 3).map((f) => `seed ${esc(f.seed)}: ${esc(f.error)}`).join('<br>')}</div>`; return false; }
      toast(`${c.added} tests added${c.duplicates ? `, ${c.duplicates} duplicates skipped` : ''}${c.failed.length ? `, ${c.failed.length} seeds failed` : ''}${c.validation_jobs.length ? '. Computing outputs…' : '. Add a reference solution to compute outputs.'}`, 'ok');
      nav(`/questions/${Q.id}?t=${c.validation_jobs.length ? 'refs' : 'tests'}`);
    } }] });
  $('#gtpl', m.el).onchange = (e) => { $('#gcode', m.el).value = GEN_TEMPLATES[e.target.value]; };
}
async function runValidation(Q) {
  try {
    const r = await POST(`${A}/questions/${Q.id}/validate`, { fill_expected: true });
    toast('Validation queued', 'info');
    if (location.hash.includes('t=refs')) goRoute(); else nav(`/questions/${Q.id}?t=refs`);
    return r;
  } catch (e) { fail(e); }
}
async function refsTab(T, Q) {
  const V = Q.version, val = V.validation || {};
  const langs = Object.keys(META.languages);
  let curLang = Object.keys(Q.references)[0] || 'c';
  const refs = { ...Q.references };
  T.innerHTML = `<div class="grid side2" style="--side:380px;gap:18px;align-items:start" id="rg"><div class="card"><div class="card-head"><div class="row"><select class="select" id="rl" style="width:auto">${langs.map((l) => `<option value="${l}">${esc(META.languages[l].name)}${refs[l] ? ' (saved)' : ''}</option>`).join('')}</select>
      <button class="btn sm" id="tpl">Insert template</button><button class="btn sm ghost" id="clr">Remove this language</button></div><div class="row"><button class="btn sm" id="tryB" title="Run this code on all tests without changing validation">Try on all tests</button><button class="btn primary sm" id="saveR">Save references</button></div></div>
      <div id="red" style="height:460px"></div></div>
    <div class="stack"><section class="card pad"><div class="row between"><h3 style="margin:0">Validation</h3><button class="btn primary sm" id="valB">Validate now</button></div>
      <p class="small muted">Runs every reference solution on every test in the sandbox. Empty expected outputs are filled from the first reference (C preferred), then other languages must reproduce them exactly. Approval and publishing require a pass.</p>
      ${Q.pending_validation.length ? `<div class="notice info small row"><span class="spin"></span> ${Q.pending_validation.length} validation job(s) running…</div>` : ''}
      ${Object.keys(val).length ? Object.entries(val).map(([l, x]) => `<div class="row between" style="padding:6px 0;border-bottom:1px solid var(--border)"><b>${langName(l)}</b>${vbadge(x.verdict, 'done')}<span class="small">${x.passed}/${x.total}</span><span class="tiny muted">${ago(x.at)}</span></div>
        ${x.failed?.length ? `<div class="tiny" style="color:var(--red)">Failed tests: ${x.failed.join(', ')}</div>` : ''}${x.compile_output ? `<pre style="color:var(--red);max-height:140px">${esc(x.compile_output)}</pre>` : ''}`).join('') : '<div class="small muted">Not run yet.</div>'}
      <div style="margin-top:10px">${V.validated_at ? '<span class="badge green">' + ico('check') + ' All references agree on all tests</span>' : '<span class="badge amber">Not validated</span>'}</div>
      ${Object.keys(refs).length > 1 ? '' : '<div class="notice small" style="margin-top:10px">Tip: add both a C and a Python reference. Disagreement between them catches wrong expected outputs.</div>'}</section>
      <section class="card pad" id="tryOut"><h3>Try results</h3><div class="small muted">Use "Try on all tests" to see how any solution would be judged.</div></section></div></div>`;
  if (innerWidth < 1100) $('#rg').style.gridTemplateColumns = '1fr';
  const ed = await codeEditor($('#red'), { value: refs[curLang] || '', language: META.languages[curLang].monaco });
  timers.push(setInterval(() => { if (!$('#red')) { ed.dispose(); clearTimers(); } }, 2000));
  $('#rl').value = curLang;
  const stash = () => { const vv = ed.getValue(); if (vv.trim()) refs[curLang] = vv; else delete refs[curLang]; };
  $('#rl').onchange = (e) => { stash(); curLang = e.target.value; monaco.editor.setModelLanguage(ed.getModel(), META.languages[curLang].monaco); ed.setValue(refs[curLang] || ''); };
  $('#tpl').onclick = () => ed.setValue(META.languages[curLang].template);
  $('#clr').onclick = () => { ed.setValue(''); delete refs[curLang]; };
  const save = async () => { stash(); const b = { title: V.title, slug: Q.slug, category: Q.category, difficulty: Q.difficulty, tags: Q.tags, statement: V.statement, input_format: V.input_format, output_format: V.output_format, constraints: V.constraints, languages: V.languages, time_limit_ms: V.time_limit_ms, memory_mb: V.memory_mb, max_marks: V.max_marks, practice: !!Q.practice, references: refs, qtype: V.qtype || 'standard', starter: V.starter || {} }; await PUT(`${A}/questions/${Q.id}`, b); };
  $('#saveR').onclick = () => busy($('#saveR'), async () => { await save(); toast('References saved', 'ok'); goRoute(); });
  $('#valB').onclick = () => busy($('#valB'), async () => { await save(); await runValidation(Q); });
  $('#tryB').onclick = () => busy($('#tryB'), async () => {
    const r = await POST(`${A}/questions/${Q.id}/try`, { language: curLang, code: ed.getValue() });
    let s; for (let i = 0; i < 240; i++) { await new Promise((x) => setTimeout(x, 800)); s = await GET(`${A}/runs/${r.id}`); if (['done', 'error'].includes(s.status)) break; }
    $('#tryOut').innerHTML = `<h3>Try results · ${langName(curLang)}</h3><div class="row" style="margin-bottom:8px">${vbadge(s.verdict, s.status)}<span class="small">${s.passed}/${s.total} passed · max ${s.time_ms ?? '—'} ms</span></div>${s.compile_output ? `<pre style="color:var(--red)">${esc(s.compile_output)}</pre>` : ''}<div class="tc-grid">${s.results.map((x) => `<span class="tc ${['AC', 'OK'].includes(x.verdict) ? x.verdict : 'bad'}" title="Test ${x.ordinal} (${x.visibility}) ${x.time_ms}ms">${['AC', 'OK'].includes(x.verdict) ? '' + ico('check') + '' : esc(x.verdict)}</span>`).join('')}</div>`;
  });
  if (Q.pending_validation.length) every(async () => { const q = await GET(`${A}/questions/${Q.id}`).catch(() => null); if (q && !q.pending_validation.length) { clearTimers(); toast(q.version.validated_at ? 'Validation passed' : 'Validation finished with problems', q.version.validated_at ? 'ok' : 'err'); goRoute(); } }, 2000);
}

async function testsPage() {
  const v = view('Test Case Manager', skeleton(4), 'tests');
  const rows = await GET(`${A}/questions`);
  v.innerHTML = ph('Test case manager', 'Pick a question to manage its samples, hidden, boundary and stress cases and to validate them against reference solutions.') +
    `<div class="card"><table class="table"><thead><tr><th>Question</th><th class="num">Tests</th><th class="num">Hidden</th><th>Validation</th><th></th></tr></thead><tbody>${rows.map((q) => `<tr class="click" onclick="nav('/questions/${q.id}?t=tests')"><td><b>${esc(q.title)}</b> <span class="tiny muted">v${q.version_no}</span></td><td class="num">${q.tests}</td><td class="num">${q.hidden}</td><td>${q.validated_at ? '<span class="badge green">validated</span>' : '<span class="badge amber">pending</span>'}</td><td class="right"><span class="small">Manage ${ico('right')}</span></td></tr>`).join('') || `<tr><td colspan="5">${empty('No questions yet')}</td></tr>`}</tbody></table></div>`;
}

// ================= practice manager =================
async function practicePage() {
  const v = view('Practice', skeleton(5), 'practice');
  const rows = await GET(`${A}/practice`);
  const on = rows.filter((r) => r.practice), off = rows.filter((r) => !r.practice);
  const tr = (r) => `<tr><td><b>${esc(r.title)}</b><div class="tiny muted">${esc(r.category)}</div></td><td>${diffBadge(r.difficulty)}</td>
    <td class="num">${r.tried}</td><td class="num">${r.solved}</td><td class="num">${r.attempts}</td>
    <td>${r.reserved ? '<span class="badge amber" title="Hidden from practice while an upcoming or live competition uses it">reserved</span>' : r.practice ? '<span class="badge green">visible</span>' : ''}</td>
    <td class="right nowrap"><a class="btn xs" href="#/questions/${r.id}">Open</a> ${r.practice ? `<button class="btn xs danger" data-off="${r.id}">Remove</button>` : `<button class="btn xs primary" data-on="${r.id}">${ico('plus', 12)} Add</button>`}</td></tr>`;
  const head = '<thead><tr><th>Problem</th><th>Difficulty</th><th class="num">Students tried</th><th class="num">Solved</th><th class="num">Attempts</th><th>Status</th><th></th></tr></thead>';
  v.innerHTML = ph('Practice', 'Problems students can solve any time to learn and earn XP. Any approved question can be added. A problem is automatically hidden while an upcoming or live competition uses it.', `<a class="btn" href="#/questions/new">${ico('plus', 14)} New question</a>`) +
    `<div class="grid g4" style="margin-bottom:16px">${[['code', 'In practice', on.length], ['users', 'Students practising', Math.max(0, ...on.map((r) => r.tried), 0)], ['check2', 'Total solves', on.reduce((a, r) => a + r.solved, 0)], ['zap', 'Attempts', on.reduce((a, r) => a + r.attempts, 0)]]
      .map(([i, k, n]) => `<div class="card stat tile"><span class="lsi">${ico(i, 18)}</span><div><div class="k">${k}</div><div class="v">${n}</div><div class="s">&nbsp;</div></div></div>`).join('')}</div>
    <section class="card" style="margin-bottom:16px"><div class="card-head"><h3>In practice (${on.length})</h3></div>${on.length ? `<div class="table-wrap"><table class="table">${head}<tbody>${on.map(tr).join('')}</tbody></table></div>` : empty('No practice problems yet', 'Add approved questions from the list below.', 'code')}</section>
    <section class="card"><div class="card-head"><h3>Approved questions you can add (${off.length})</h3><input class="input" id="pq" placeholder="Search" style="max-width:260px"></div><div class="table-wrap"><table class="table">${head}<tbody id="offrows">${off.map(tr).join('') || `<tr><td colspan="7">${empty('Every approved question is already in practice')}</td></tr>`}</tbody></table></div></section>`;
  $('#pq').oninput = (e) => { const q = e.target.value.toLowerCase(); $$('#offrows tr').forEach((t) => { t.style.display = t.textContent.toLowerCase().includes(q) ? '' : 'none'; }); };
  $$('[data-on],[data-off]', v).forEach((b) => b.onclick = () => busy(b, async () => { await POST(`${A}/practice`, { question_id: Number(b.dataset.on || b.dataset.off), on: !!b.dataset.on }); toast(b.dataset.on ? 'Added to practice' : 'Removed from practice', 'ok'); goRoute(); }));
}

// ================= drafts (PDF + AI) =================
function draftEditor(g, after) {
  const p = g.payload;
  p.samples = p.samples || []; p.tests = p.tests || []; p.reference = p.reference || {};
  const sampleRow = (s, i) => `<div class="card flat pad stack tight" data-si="${i}"><div class="row between"><b class="small">Sample ${i + 1}</b><button type="button" class="btn xs ghost" data-rs="${i}">Remove</button></div><div class="grid g2"><textarea class="textarea mono" data-k="input" rows="3" placeholder="input">${esc(s.input)}</textarea><textarea class="textarea mono" data-k="output" rows="3" placeholder="output">${esc(s.output)}</textarea></div><input class="input" data-k="explanation" value="${esc(s.explanation || '')}" placeholder="explanation"></div>`;
  const m = modal({ title: `Review draft #${g.id}`, size: 'xwide', body: `${g.warnings.length ? `<div class="notice warn small" style="margin-bottom:12px">${g.warnings.map(esc).join('<br>')}</div>` : ''}
    <form class="stack" id="df"><div class="grid g3">${fld('Title', inp('title', p.title))}${fld('Category', sel('category', META.categories, p.category || 'Problem Solving'))}${fld('Difficulty', sel('difficulty', ['Easy', 'Intermediate', 'Challenging'], p.difficulty || 'Easy'))}</div>
    ${fld('Statement', ta('statement', p.statement, 'rows=7'))}<div class="grid g3">${fld('Input format', ta('input_format', p.input_format, 'rows=3'))}${fld('Output format', ta('output_format', p.output_format, 'rows=3'))}${fld('Constraints', ta('constraints', p.constraints, 'rows=3'))}</div>
    ${fld('Tags', inp('tags', (p.tags || []).join(', ')))}
    <div><div class="row between"><b>Sample cases</b><button type="button" class="btn xs" id="addS">+ Sample</button></div><div class="stack tight" id="sams" style="margin-top:6px">${p.samples.map(sampleRow).join('')}</div></div>
    <div class="grid g2">${fld('Reference solution: C', `<textarea class="textarea mono" name="ref_c" rows="10">${esc(p.reference.c || '')}</textarea>`)}${fld('Reference solution: Python', `<textarea class="textarea mono" name="ref_python" rows="10">${esc(p.reference.python || '')}</textarea>`)}</div>
    ${fld(`Hidden test inputs (${p.tests.length})`, `<textarea class="textarea mono" name="tests" rows="6" placeholder="one test per block, separated by a line with ---">${esc(p.tests.map((t) => t.input).join('---\n'))}</textarea>`, 'outputs are computed by the reference solution')}</form>`,
    actions: [{ label: 'Reject', kind: 'danger', onClick: async () => { await POST(`${A}/drafts/${g.id}/reject`); toast('Rejected', 'ok'); after(); } }, { label: 'Save draft', onClick: async (mm) => { await save(mm); toast('Draft saved', 'ok'); after(); } },
      ...(ME.can_approve ? [{ label: 'Save to question bank', kind: 'primary', onClick: async (mm) => { await save(mm); const r = await POST(`${A}/drafts/${g.id}/approve`); toast(`Added to the bank${r.validation_jobs.length ? ', validation running' : ''}`, 'ok'); nav(`/questions/${r.question_id}?t=refs`); } }] : [])] });
  const bindS = () => $$('[data-rs]', m.el).forEach((b) => b.onclick = () => { b.closest('[data-si]').remove(); });
  $('#addS', m.el).onclick = () => { $('#sams', m.el).insertAdjacentHTML('beforeend', sampleRow({ input: '', output: '' }, $$('[data-si]', m.el).length)); bindS(); }; bindS();
  const save = async (mm) => {
    const f = formData($('#df', mm));
    const payload = { ...p, title: f.title, category: f.category, difficulty: f.difficulty, statement: f.statement, input_format: f.input_format, output_format: f.output_format, constraints: f.constraints, tags: f.tags.split(',').map((x) => x.trim()).filter(Boolean),
      samples: $$('[data-si]', mm).map((el) => ({ input: $('[data-k=input]', el).value, output: $('[data-k=output]', el).value, explanation: $('[data-k=explanation]', el).value })),
      reference: { ...(f.ref_c.trim() ? { c: f.ref_c } : {}), ...(f.ref_python.trim() ? { python: f.ref_python } : {}) },
      tests: f.tests.split(/^---\s*$/m).map((x) => x.replace(/^\n/, '')).filter((x) => x.trim()).map((input, i) => ({ input: input.endsWith('\n') ? input : input + '\n', kind: p.tests[i]?.kind || 'normal', description: p.tests[i]?.description || '' })) };
    await PUT(`${A}/drafts/${g.id}`, { payload });
  };
}
const draftRow = (g) => `<tr class="click" data-g="${g.id}"><td><b>${esc(g.payload.title || (g.status === 'generating' ? 'Generating…' : 'Untitled'))}</b><div class="tiny muted">#${g.id} · ${esc(g.source)} · ${esc(g.creator || '')}</div></td>
  <td><span class="badge ${{ pending: 'blue', approved: 'green', rejected: '', failed: 'red', generating: 'violet' }[g.status]}">${g.status === 'generating' ? '<span class="spin" style="width:10px;height:10px"></span> ' : ''}${esc(g.status)}</span></td>
  <td class="small">${(g.payload.samples || []).length} samples · ${(g.payload.tests || []).length} tests · ${Object.keys(g.payload.reference || {}).map(langName).join(', ') || 'no reference'}</td><td class="small muted">${g.warnings.length ? `${ico('alert')} ${g.warnings.length}` : ''}</td>
  <td class="right">${g.question_id ? `<a href="#/questions/${g.question_id}" onclick="event.stopPropagation()">Open question ${ico('right')}</a>` : ''}</td></tr>`;

async function pdfPage() {
  const v = view('PDF Import', skeleton(4), 'pdf');
  const docs = await GET(`${A}/pdf`);
  v.innerHTML = ph('Question import', 'Upload a question paper in any common format. Text is extracted (OCR for scans and photos), split into questions and turned into drafts for review. Nothing is published automatically.',
    '<button class="btn" id="tplBtn">Paper template (.md)</button><button class="btn" id="jsonTpl">Question set (.json)</button><button class="btn" id="csvTpl">Question set (.csv)</button>') +
    `<label class="card pad center" id="drop" style="display:block;border:2px dashed var(--border-strong);cursor:pointer;margin-bottom:18px"><input type="file" accept=".pdf,.docx,.odt,.pptx,.rtf,.html,.htm,.txt,.md,.c,.cpp,.py,.java,.js,.json,.csv,.tsv,.xlsx,.zip,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.gif" id="pf" class="hidden"><div style="font-size:28px">${ico('file')}</div><b>Drop a file here or click to choose</b>
      <div class="small muted">Documents: PDF · Word · OpenDocument · PowerPoint · RTF · HTML · Text/Markdown · code files<br>Question sets: JSON · CSV/TSV · Excel (.xlsx), one question per record, with samples, tests and solutions<br>Images (OCR): PNG · JPG · WEBP · TIFF · BMP · GIF · or a ZIP of any of these. Max 20 MB. Nothing inside a file is ever executed.</div></label>
    <div class="card">${docs.length ? `<table class="table"><thead><tr><th>Document</th><th>Status</th><th class="num">Pages</th><th class="num">Drafts</th><th>Uploaded</th></tr></thead><tbody>${docs.map((d) => `<tr class="click" onclick="nav('/pdf/${d.id}')"><td><b>${esc(d.filename)}</b><div class="tiny muted">${(d.size / 1024).toFixed(0)} KB · ${esc(d.uploader || '')}</div></td><td><span class="badge ${{ done: 'green', processing: 'blue', failed: 'red' }[d.status]}">${d.status}</span></td><td class="num">${d.pages ?? '—'}</td><td class="num">${d.drafts}</td><td class="small muted">${ago(d.created_at)}</td></tr>`).join('')}</tbody></table>` : empty('No documents uploaded yet')}</div>`;
  $('#tplBtn').onclick = () => download('question-paper-template.md', `Question 1: Sum of Digits
Given a non-negative integer N, print the sum of its digits.

Input Format
A single integer N.

Output Format
One integer: the sum of the digits of N.

Constraints
0 <= N <= 10^18

Sample Input
1234
Sample Output
10
Explanation
1 + 2 + 3 + 4 = 10

Question 2: Reverse Words
(same headings again for every question)
`, 'text/markdown');
  $('#jsonTpl').onclick = () => download('question-set.json', JSON.stringify({ questions: [{ title: 'Sum of Digits', category: 'Basic Mathematics', difficulty: 'Easy', tags: ['math'],
    statement: 'Given a non-negative integer N, print the sum of its digits.', input_format: 'A single integer N.', output_format: 'One integer.', constraints: '0 <= N <= 10^18',
    samples: [{ input: '1234', output: '10', explanation: '1+2+3+4' }], tests: [{ input: '0' }, { input: '999999999999999999' }, { input: '5', output: '5' }],
    reference: { python: 'print(sum(map(int, input().strip())))', c: '#include <stdio.h>\nint main(){char s[32];scanf("%31s",s);int t=0;for(char*p=s;*p;p++)t+=*p-48;printf("%d\\n",t);}' },
    qtype: 'standard' }] }, null, 2), 'application/json');
  $('#csvTpl').onclick = () => download('question-set.csv', toCSV([['title', 'statement', 'input_format', 'output_format', 'constraints', 'difficulty', 'category', 'tags', 'sample_input', 'sample_output', 'explanation', 'solution_python', 'solution_c'],
    ['Square It', 'Print N*N.', 'One integer N.', 'One integer.', '1 <= N <= 1000', 'Easy', 'Basic Mathematics', 'math', '4', '16', '4*4 = 16', 'n=int(input())\nprint(n*n)', '']]));
  const up = (file) => { if (!file) return; const fd = new FormData(); fd.append('file', file); toast('Uploading…', 'info'); api('POST', `${A}/pdf`, fd).then((r) => { toast('Uploaded. Extracting…', 'ok'); nav('/pdf/' + r.id); }).catch(fail); };
  $('#pf').onchange = (e) => up(e.target.files[0]);
  const d = $('#drop');
  d.ondragover = (e) => { e.preventDefault(); d.style.borderColor = 'var(--accent)'; }; d.ondragleave = () => d.style.borderColor = ''; d.ondrop = (e) => { e.preventDefault(); up(e.dataTransfer.files[0]); };
}
async function pdfDoc(p) {
  const v = view('PDF Import', skeleton(4), 'pdf');
  const load = async () => {
    const d = await GET(`${A}/pdf/${p.id}`);
    const text = d.extracted_text || '';
    let marked = '', last = 0;
    d.drafts.filter((g) => g.payload.boundary).sort((a, b) => a.payload.boundary[0] - b.payload.boundary[0]).forEach((g, i) => { const [a, b] = g.payload.boundary; marked += esc(text.slice(last, a)) + `<mark style="background:${i % 2 ? '#efe7ff' : '#e7f3ff'};display:block;border-left:3px solid var(--accent);padding-left:6px"><b class="tiny">▼ Question ${i + 1} ${ico('right')} draft #${g.id}</b>\n${esc(text.slice(a, b))}</mark>`; last = b; });
    marked += esc(text.slice(last));
    v.innerHTML = `<a class="small" href="#/pdf">${ico('left')} Question import</a>` + ph(d.filename, `${d.pages ?? '?'} pages · <span class="badge ${{ done: 'green', processing: 'blue', failed: 'red' }[d.status]}">${d.status}</span>`) +
      `${d.warnings.length ? `<div class="notice warn small" style="margin-bottom:14px">${d.warnings.map(esc).join('<br>')}</div>` : ''}
      <section class="card" style="margin-bottom:16px"><div class="card-head"><h3>Detected questions (${d.drafts.length})</h3><div class="row"><span class="small muted">Click to review and edit by hand, or</span>
      ${can('ai') ? `<button class="btn sm primary" id="aiConv" ${d.status === 'done' ? '' : 'disabled'}>${ico('sparkles', 14)} Build questions with AI</button>` : ''}</div></div>${d.drafts.length ? `<table class="table"><tbody>${d.drafts.map(draftRow).join('')}</tbody></table>` : empty(d.status === 'processing' ? 'Extracting…' : 'No questions detected')}</section>
      <div class="grid g2" style="align-items:start"><section class="card"><div class="card-head"><h3>Original file</h3><a class="small" href="${A}/pdf/${d.id}/file" target="_blank" rel="noopener">Open ${ico('external')}</a></div>
          ${d.kind === 'pdf' ? `<iframe src="${A}/pdf/${d.id}/file" style="width:100%;height:75vh;border:0" title="PDF preview"></iframe>` : d.kind === 'image' ? `<img src="${A}/pdf/${d.id}/file" alt="Uploaded image" style="width:100%;max-height:75vh;object-fit:contain">`
            : `<div class="card-body small muted">${esc((d.filename.split('.').pop() || '').toUpperCase())} file: its text${['json', 'csv', 'xlsx'].includes(d.kind) ? ' and question records are' : ' is'} shown on the right. Use "Open" to download the original.</div>`}</section>
        <section class="card"><div class="card-head"><h3>Extracted text &amp; boundaries</h3></div><pre style="margin:0;border:0;max-height:75vh;white-space:pre-wrap">${marked || '<span class="muted">No text</span>'}</pre></section></div>`;
    $$('[data-g]', v).forEach((tr) => tr.onclick = () => { const g = d.drafts.find((x) => x.id == tr.dataset.g); if (g.question_id) return nav(`/questions/${g.question_id}?t=refs`); if (['pending', 'failed'].includes(g.status)) draftEditor(g, load); else toast(`Draft is ${g.status}`, 'info'); });
    if ($('#aiConv')) $('#aiConv').onclick = () => busy($('#aiConv'), async () => {
      const r = await POST(`${A}/pdf/${d.id}/ai`);
      toast(`AI is building ${r.converting.length} question(s): statement, C + Python solutions and tests, each checked in the sandbox.`, 'info');
      clearTimers(); every(async () => { if ((await load()) !== 'processing' && !(await GET(`${A}/pdf/${d.id}`)).drafts.some((g) => g.status === 'generating')) clearTimers(); }, 4000);
    });
    return d.drafts.some((g) => g.status === 'generating') ? 'processing' : d.status;
  };
  if ((await load()) === 'processing') every(async () => { if ((await load()) !== 'processing') clearTimers(); }, 2000);
}

const aiTabs = (on) => `<div class="tabs" style="margin-bottom:16px"><a class="${on === 'q' ? 'on' : ''}" href="#/ai">${ico('sparkles', 14)} Create questions</a><a class="${on === 't' ? 'on' : ''}" href="#/ai/tools">${ico('wand', 14)} Student helpers</a></div>`;
const aiOff = (st) => `<div class="card pad aioff"><h3 style="margin:0">${ico('alert', 17)} The AI is switched off on this computer</h3><p class="small muted" style="margin:6px 0 10px">${esc(st.reason || '')}</p>
  <ol class="small" style="margin:0;padding-left:18px;line-height:1.7"><li>On the server PC, open the <b>ASTRA Code Arena</b> app.</li><li>Press <b>Install AI model</b> and wait for it to finish (about 1 GB download).</li><li>Come back here and reload. Nothing else to set up.</li></ol>
  <p class="tiny muted" style="margin:10px 0 0">Already use Ollama? Any model you have pulled is found by itself. Everything except this page works without AI.</p></div>`;
async function aiPage() {
  const v = view('AI Studio', skeleton(4), 'ai');
  const [st, drafts, bank] = await Promise.all([GET(`${A}/ai/status`), GET(`${A}/drafts?source=ai`), GET(`${A}/questions`)]);
  const on = st.enabled && st.reachable !== false;
  const kind = sessionStorage.getItem('ai:kind') || 'code';
  const KINDS = [['code', 'code', 'Coding problem', 'A full problem with solutions and hidden tests'], ['mcq', 'check', 'Multiple-choice set', 'Predict-the-output or concept questions'], ['var', 'layers', 'New version of a question', 'Same idea, new story and numbers']];
  const topic = fld('Topic', `<input class="input" name="topic" list="topics" required placeholder="e.g. arrays, loops, pointers">`), diff = fld('Difficulty', sel('difficulty', [['Easy', 'Easy'], ['Intermediate', 'Moderate'], ['Challenging', 'Hard']], 'Easy'));
  const STAT = { generating: ['Writing…', 'violet'], pending: ['Ready to review', 'blue'], approved: ['In question bank', 'green'], failed: ['Failed', 'red'], rejected: ['Rejected', ''] };
  v.innerHTML = aiTabs('q') + ph('AI Studio', 'Tell the AI what you need. It writes the questions, the server checks them by really running the code, and nothing reaches students until you approve it.') +
    (on ? `<div class="card pad aibar"><div class="row" style="gap:10px"><span class="dot ok"></span><div><b>AI is ready</b><div class="tiny muted">${st.auto ? 'Model chosen automatically from what is installed.' : 'Using the model you chose.'}</div></div></div>
        <label class="row small" style="gap:8px">Model <select class="select" id="aiModel" style="width:auto;min-width:200px">${(st.models.length ? st.models : [st.model]).map((m) => `<option ${m === st.model ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></label>
        <label class="check" title="When a student has almost no practice left at their level, the AI writes one new problem. It is published only if both of its solutions agree on every test. At most 4 a day, never during a live round.">
          <input type="checkbox" id="aiGrow" ${st.autogrow ? 'checked' : ''}> Grow practice automatically</label></div>` : st.enabled ? `<div class="notice warn" style="margin-bottom:16px">The AI service is not answering: ${esc(st.reason)}</div>` : aiOff(st)) +
    `<form class="card pad askbox" id="askf"><h3 style="margin:0">Ask for questions in plain words</h3><div class="small muted" style="margin:2px 0 10px">Say how many, what kind, how hard and the topic. The AI writes them, the server checks every answer it can by running code, and they wait here for your approval.</div>
      <div class="row" style="flex-wrap:nowrap;gap:8px"><input class="input lg" name="text" required maxlength="1000" placeholder="e.g. 6 mcq on loops, 2 fix the bug on arrays, 2 coding on strings" style="flex:1;min-width:0">
        <select class="select" name="level" style="width:auto;height:44px" title="Level for every part that does not name one"><option value="">Any level</option><option value="Easy">Easy</option><option value="Intermediate">Intermediate</option><option value="Challenging">Hard</option></select>
        <button class="btn primary lg" ${on ? '' : 'disabled'}>${ico('sparkles', 15)} Write</button></div>
      <div class="row" style="gap:8px;margin-top:8px" id="askRef"></div>
      <div class="row wrap" style="margin-top:10px;gap:6px">${['10 easy predict the output on loops', '5 logical reasoning mcq', '3 fix the bug on arrays', '6 moderate mcq on data structures', '2 easy coding on strings', '8 questions from the attached notes'].map((x) => `<button type="button" class="chipbtn" data-ask="${esc(x)}">${esc(x)}</button>`).join('')}</div><div id="askOut"></div></form>
    <div class="how3" style="margin-top:16px"><div><b>1</b> You describe it</div><div><b>2</b> The AI writes it and the server tests it</div><div><b>3</b> You review and approve</div></div>
    <section class="card pad" style="margin-top:16px"><h3 style="margin:0 0 10px">Or fill in the details yourself</h3>
      <div class="pick3">${KINDS.map(([k, ic, name, sub]) => `<button type="button" class="${k === kind ? 'on' : ''}" data-kind="${k}">${ico(ic, 18)}<span><b>${name}</b><small>${sub}</small></span></button>`).join('')}</div>
      <datalist id="topics">${META.categories.map((c) => `<option>${esc(c)}</option>`).join('')}</datalist>
      <form class="stack" id="gf" data-for="code" style="margin-top:14px"><div class="grid g2">${topic}${diff}</div>
        <details><summary class="small muted">More options</summary><div class="grid g2" style="margin-top:10px">${fld('Solutions written in', sel('language', ['C and Python', 'C', 'Python'], 'C and Python'))}${fld('Examples shown to students', '<input class="input" type="number" name="samples" value="2" min="1" max="5">')}
          ${fld('Story style', '<input class="input" name="style" placeholder="campus story, short and clear">')}${fld('Size limits', '<input class="input" name="constraints" placeholder="n up to 1000">')}</div></details>
        <div class="row"><button class="btn primary" ${on ? '' : 'disabled'}>${ico('sparkles')} Write this problem</button><span class="small muted">Takes one to three minutes on a small PC.</span></div></form>
      <form class="stack" id="mf" data-for="mcq" style="margin-top:14px"><div class="grid g2">${topic}${diff}</div>
        <div class="grid g2">${fld('Kind', sel('style', [['output', 'Predict the output of a C program (answer checked by running it)'], ['concept', 'Concept / theory questions (you confirm the answer)']], 'output'))}${fld('How many', '<input class="input" type="number" name="count" value="5" min="1" max="10">')}</div>
        <div class="row"><button class="btn primary" ${on ? '' : 'disabled'}>${ico('sparkles')} Write these questions</button></div></form>
      <div class="stack" data-for="var" style="margin-top:14px">${fld('Question to make a new version of', `<select class="select" id="vq">${bank.map((q) => `<option value="${q.id}">${esc(q.title)}</option>`).join('')}</select>`)}
        <div class="row"><button class="btn primary" id="vb" ${on && bank.length ? '' : 'disabled'}>${ico('sparkles')} Write a new version</button></div></div></section>
    <section class="card" style="margin-top:18px"><div class="card-head"><h3>Your AI questions</h3><span class="small muted">Click one to see the checks, then open it to approve.</span></div>${drafts.length ? `<table class="table"><tbody>${drafts.map((g) => `<tr class="click" data-g="${g.id}">
      <td><b>${esc(g.payload.title || g.payload.request?.topic || 'Untitled')}</b><div class="tiny muted">${fmtTime(g.created_at)}${g.creator ? ' · ' + esc(g.creator) : ' · automatic'}</div></td>
      <td><span class="badge ${STAT[g.status][1]}">${g.status === 'generating' ? '<span class="spin" style="width:10px;height:10px"></span> ' : ''}${STAT[g.status][0]}</span></td>
      <td class="right">${g.question_id ? `<a href="#/questions/${g.question_id}" onclick="event.stopPropagation()">Open question ${ico('right')}</a>` : ''}</td></tr>`).join('')}</tbody></table>` : empty('Nothing here yet', 'Questions the AI writes appear here.')}</section>`;
  const showKind = (k) => { sessionStorage.setItem('ai:kind', k); $$('[data-kind]', v).forEach((b) => b.classList.toggle('on', b.dataset.kind === k)); $$('[data-for]', v).forEach((f) => { f.hidden = f.dataset.for !== k; }); };
  $$('[data-kind]', v).forEach((b) => b.onclick = () => showKind(b.dataset.kind)); showKind(kind);
  $$('[data-ask]', v).forEach((b) => b.onclick = () => { $('#askf [name=text]').value = b.dataset.ask; $('#askf [name=text]').focus(); });
  const askRef = { ref: '' }, arp = refPicker(askRef);
  $('#askRef').innerHTML = arp.html + '<span class="tiny muted">Optional: attach notes, a syllabus or an old question paper; new questions follow its topics and style.</span>'; arp.bind($('#askf'));
  const askSend = async (extra = {}) => { const r = await POST(`${A}/ai/prompt`, { ...formData($('#askf')), ref: askRef.ref, ...extra });
    if (r.ask) {   // the AI needs one answer first (for example: hidden test cases or simple examples)
      $('#askOut').innerHTML = `<div class="asst-ask" style="margin-top:12px"><b>${esc(r.ask.q)}</b><div class="row wrap" style="gap:6px;margin-top:8px">${r.ask.options.map((o, i) => `<button type="button" class="chipbtn" data-opt-i="${i}">${esc(o.label)}</button>`).join('')}</div></div>`;
      $$('[data-opt-i]', $('#askOut')).forEach((b) => b.onclick = () => busy(b, async () => { $('#askOut').innerHTML = ''; await askSend({ tests: r.ask.options[b.dataset.optI].tests }); }));
      return;
    }
    $('#askOut').innerHTML = '';
    const ok = r.plan.filter((x) => x.started);
    toast(ok.length ? `Writing ${ok.reduce((a, x) => a + x.started, 0)} question(s). They appear below as they finish.` : 'That kind of question cannot be written by the AI yet. Try mcq, predict the output, fix the bug or coding.', ok.length ? 'info' : 'err');
    if (ok.length) setTimeout(goRoute, 600); };
  $('#askf').onsubmit = (e) => { e.preventDefault(); busy($('button.primary', e.target), async () => { await askSend(); }); };
  const setAI = (body) => PUT(`${A}/ai/settings`, body).then(() => toast('Saved', 'ok')).catch(fail);
  if ($('#aiModel')) $('#aiModel').onchange = (e) => setAI({ model: e.target.value });
  if ($('#aiGrow')) $('#aiGrow').onchange = (e) => setAI({ autogrow: e.target.checked });
  $('#gf').onsubmit = (e) => { e.preventDefault(); busy($('button', e.target), async () => { await POST(`${A}/ai/generate`, formData(e.target)); toast('The AI is writing it. This page updates by itself.', 'info'); goRoute(); }); };
  $('#mf').onsubmit = (e) => { e.preventDefault(); busy($('button', e.target), async () => { await POST(`${A}/ai/mcq`, formData(e.target)); toast('The AI is writing the questions. This page updates by itself.', 'info'); goRoute(); }); };
  $('#vb').onclick = () => busy($('#vb'), async () => { await POST(`${A}/ai/variation`, { question_id: Number($('#vq').value) }); toast('The AI is writing a new version.', 'info'); goRoute(); });
  $$('[data-g]', v).forEach((tr) => tr.onclick = () => {
    const g = drafts.find((x) => x.id == tr.dataset.g);
    modal({ title: g.payload.title || 'AI question', body: `<div class="row" style="margin-bottom:10px"><span class="badge ${STAT[g.status][1]}">${STAT[g.status][0]}</span></div>
      <div class="small muted" style="margin-bottom:6px">What the checks found</div><div class="stack tight">${g.warnings.map((w) => `<div class="notice small ${/passed|Published|proved|Fix the bug: the starter/.test(w) ? 'ok' : /fail|Still|timed|wrong|could not|Not published/i.test(w) ? 'warn' : ''}">${esc(w)}</div>`).join('') || '<span class="small muted">Still working…</span>'}</div>`,
      actions: [...(g.question_id ? [{ label: 'Open question to review & approve', kind: 'primary', onClick: () => nav(`/questions/${g.question_id}`) }] : []), ...(!['rejected', 'approved'].includes(g.status) ? [{ label: 'Reject', kind: 'danger', onClick: async () => { await POST(`${A}/drafts/${g.id}/reject`); goRoute(); } }] : []), { label: 'Close' }] });
  });
  if (drafts.some((g) => g.status === 'generating')) every(async () => { const d = await GET(`${A}/drafts?source=ai`).catch(() => []); if (!d.some((g) => g.status === 'generating')) { clearTimers(); goRoute(); } }, 3000);
}


// ================= AI Tools Lab =================
const TOOL_ICONS = ['bulb', 'bot', 'sparkles', 'book', 'target', 'code', 'alert', 'chart', 'wand'];
async function toolsLab() {
  const v = view('AI Studio', skeleton(4), 'ai');
  const [d, comps] = await Promise.all([GET(`${A}/ai/tools`), GET(`${A}/competitions`)]);
  const aiOn = d.ai.enabled && d.ai.reachable !== false;
  v.innerHTML = aiTabs('t') + ph('Student helpers', 'Small AI helpers students can use while solving, such as a hint button or an error explainer. You decide where each one is switched on (practice, or a chosen competition). Every use is logged.') +
    `${aiOn ? '' : aiOff(d.ai.enabled ? { reason: 'The AI service is not answering right now.' } : await GET(`${A}/ai/status`))}
    <form class="card pad onebox" id="qf"><div class="row" style="gap:10px;margin-bottom:10px"><span class="lsi">${ico('wand', 18)}</span><div><h3 style="margin:0">Describe a new helper</h3><div class="small muted">Say what it should do. Choose where students can use it, then create it.</div></div></div>
      <textarea class="textarea" name="request" rows="3" required placeholder="e.g. After a wrong answer, explain which edge case the student probably missed, in two sentences, without code"></textarea>
      <div class="row wrap" style="margin-top:10px;gap:14px">${chk('practice', true, 'Practice')}<select class="select" name="competition" style="width:auto;min-width:240px"><option value="">No competition</option>${comps.filter((c) => ['draft', 'published', 'live', 'paused'].includes(c.status)).map((c) => `<option value="${c.id}">${esc(c.code)} · ${esc(c.title)}</option>`).join('')}</select>
        <div class="grow"></div><button type="button" class="btn" id="df" ${aiOn ? '' : 'disabled'}>Preview before saving</button><button class="btn primary lg" ${aiOn ? '' : 'disabled'}>${ico('sparkles', 15)} Create &amp; switch on</button></div><div id="qout"></div></form>
    <div><div class="card pad stack" style="margin-top:16px"><h3 style="margin:0">Ready-made helpers</h3><div class="small muted">Add one, then open it to switch it on.</div>${Object.entries(d.presets).map(([k, p]) => `<div class="row between"><div class="row"><span class="lsi sm">${ico(p.icon, 15)}</span><div><b class="small">${esc(p.name)}</b><div class="tiny muted">${esc(p.description)}</div></div></div><button class="btn sm" data-preset="${k}">Add</button></div>`).join('')}</div></div>
    <section class="card" style="margin-top:18px"><div class="card-head"><h3>Your helpers</h3></div>${d.tools.length ? `<table class="table"><thead><tr><th>Tool</th><th>Status</th><th>Switched on for</th><th class="num">Uses</th><th></th></tr></thead><tbody>${d.tools.map((t) => `<tr>
      <td><div class="row"><span class="lsi sm">${ico(t.spec.icon || 'sparkles', 15)}</span><div><b>${esc(t.name)}</b><div class="tiny muted">${esc(t.spec.description || '')}</div></div></div></td>
      <td><span class="badge ${{ active: 'green', draft: '', off: 'amber' }[t.status]}">${esc(t.status)}</span></td>
      <td class="small">${[t.scope.practice ? 'Practice' : '', ...t.scope.competitions.map((id) => (comps.find((c) => c.id === id) || {}).code || '#' + id)].filter(Boolean).map(esc).join(', ') || '<span class="muted">nowhere yet</span>'}</td>
      <td class="num">${t.uses}</td><td class="right nowrap"><button class="btn xs" data-edit="${t.id}">Open</button> <button class="btn xs" data-uses="${t.id}">Usage</button> <button class="btn xs ghost" data-del="${t.id}">${ico('x', 13)}</button></td></tr>`).join('')}</tbody></table>`
      : empty('No helpers yet', 'Add a ready-made helper or describe a new one.')}</section>`;
  $('#qf').onsubmit = (e) => { e.preventDefault(); busy($('button.lg', e.target), async () => { const f = formData(e.target); const t = await POST(`${A}/ai/tools/quick`, { request: f.request, scope: { practice: f.practice, competitions: f.competition ? [Number(f.competition)] : [] } });
    toast(`"${t.name}" is ${t.status === 'active' ? 'live for students' : 'saved as a draft (choose where to deploy it)'}`, 'ok'); goRoute(); }); };
  $('#df').onclick = () => { const request = $('#qf [name=request]').value.trim(); if (!request) return toast('Describe the helper first.', 'info'); busy($('#df'), async () => { const spec = await POST(`${A}/ai/tools/design`, { request }); toolEditor({ spec, status: 'draft', scope: { competitions: [], practice: false } }, comps); }); };
  $$('[data-preset]', v).forEach((b) => b.onclick = () => busy(b, async () => { await POST(`${A}/ai/tools/presets/${b.dataset.preset}`); toast('Tool added as a draft. Open it to switch it on.', 'ok'); goRoute(); }));
  $$('[data-edit]', v).forEach((b) => b.onclick = () => toolEditor(d.tools.find((t) => t.id == b.dataset.edit), comps));
  $$('[data-del]', v).forEach((b) => b.onclick = async () => { if (await confirmBox('Delete this tool and its usage log?', { danger: true, ok: 'Delete' })) { await DEL(`${A}/ai/tools/${b.dataset.del}`); goRoute(); } });
  $$('[data-uses]', v).forEach((b) => b.onclick = async () => {
    const u = await GET(`${A}/ai/tools/${b.dataset.uses}/uses`);
    modal({ title: 'Tool usage', size: 'wide', body: u.length ? u.map((x) => `<details style="border-bottom:1px solid var(--border);padding:6px 0"><summary class="row" style="cursor:pointer"><b>${esc(x.name)}</b><span class="tiny muted">${esc(x.participant_id || '')} · ${esc(x.competition || 'practice')} · Q${x.question_id} · ${fmtTime(x.created_at)} · ${x.ms} ms</span></summary><div class="prose small">${md(x.output)}</div></details>`).join('') : empty('Not used yet') });
  });
}
function toolEditor(t, comps) {
  const sp = t.spec;
  const m = modal({ title: t.id ? `Tool · ${t.name}` : 'New tool (designed by AI)', size: 'xwide', body: `<div class="grid g2" style="align-items:start"><form class="stack" id="tf">
      <div class="grid g2">${fld('Name', inp('name', sp.name))}${fld('Button label', inp('button_label', sp.button_label))}</div>
      <div class="grid g2">${fld('Icon', sel('icon', TOOL_ICONS, sp.icon))}${fld('Max uses per problem', inp('max_uses_per_problem', sp.max_uses_per_problem, 'type=number min=1 max=20'))}</div>
      ${fld('Description (students see this)', inp('description', sp.description))}${fld('Instructions for the AI (system prompt)', `<textarea class="textarea" name="system_prompt" rows="7">${esc(sp.system_prompt)}</textarea>`)}
      <div class="small" style="font-weight:550">Context sent to the AI</div><div class="row wrap">${chk('inc_problem', sp.include.problem, 'Problem statement')}${chk('inc_code', sp.include.code, "Student's code")}${chk('inc_last', sp.include.last_result, 'Last verdict (samples only)')}</div>
      ${chk('allow_full_solutions', sp.allow_full_solutions, 'Allow full solutions (off = hints only)')}
      ${fld('Form fields (JSON)', `<textarea class="textarea mono" name="inputs" rows="4">${esc(JSON.stringify(sp.inputs, null, 1))}</textarea>`, 'type: text / textarea / select')}
      <div class="divider"></div><div class="small" style="font-weight:550">Switch on for</div><div class="stack tight" style="max-height:150px;overflow:auto">${chk('practice', t.scope.practice, 'Practice (all practice problems)')}
      ${comps.filter((c) => !['archived', 'cancelled'].includes(c.status)).map((c) => chk('competitions', t.scope.competitions.includes(c.id), `${esc(c.code)} · ${esc(c.title)} <span class="badge">${esc(c.display_status)}</span>`, `value="${c.id}" data-multi="1" data-num="1"`)).join('')}</div>
      ${fld('Status', sel('status', [['draft', 'Draft (hidden)'], ['active', 'Active'], ['off', 'Off']], t.status))}</form>
    <div class="card pad stack"><h3 style="margin:0">Student preview &amp; test</h3><div class="toolcard"><div class="row"><span class="lsi sm">${ico(sp.icon, 15)}</span><b>${esc(sp.name)}</b></div><div class="small muted">${esc(sp.description)}</div>
      ${sp.inputs.map((x) => `<input class="input" data-in="${esc(x.key)}" placeholder="${esc(x.placeholder || x.label)}">`).join('')}
      <textarea class="textarea mono" id="tcode" rows="5" placeholder="Paste sample student code to test with (optional)"></textarea><button class="btn primary sm" id="tbtn" type="button">${ico('play', 13)} ${esc(sp.button_label)}</button><div class="toolout prose small" id="tout"></div></div>
      <div class="tiny muted">Testing uses the saved tool; save first if you changed it.</div></div></div>`,
    actions: [{ label: 'Cancel' }, { label: t.id ? 'Save' : 'Create tool', kind: 'primary', onClick: async (mm) => {
      const f = formData($('#tf', mm));
      let inputs; try { inputs = JSON.parse(f.inputs || '[]'); } catch (e) { throw new Error('Form fields must be valid JSON.'); }
      const spec = { name: f.name, button_label: f.button_label, icon: f.icon, description: f.description, system_prompt: f.system_prompt, max_uses_per_problem: Number(f.max_uses_per_problem),
        include: { problem: f.inc_problem, code: f.inc_code, last_result: f.inc_last }, allow_full_solutions: f.allow_full_solutions, inputs };
      const body = { spec, status: f.status, scope: { practice: f.practice, competitions: f.competitions || [] } };
      t.id ? await PUT(`${A}/ai/tools/${t.id}`, body) : await POST(`${A}/ai/tools`, body);
      toast('Tool saved', 'ok'); goRoute();
    } }] });
  $('#tbtn', m.el).onclick = () => {
    if (!t.id) return toast('Create the tool first, then test it.', 'info');
    busy($('#tbtn', m.el), async () => { const inputs = Object.fromEntries($$('[data-in]', m.el).map((i) => [i.dataset.in, i.value])); const r = await POST(`${A}/ai/tools/${t.id}/test`, { code: $('#tcode', m.el).value, inputs }); $('#tout', m.el).innerHTML = md(r.output) + `<div class="tiny muted">${r.ms} ms</div>`; });
  };
}

// ================= participants =================
function importCsvModal(compId) {
  modal({ title: 'Import students from CSV', body: `<p class="small muted">Header row required: <code>name,usn,dob,department,year,semester,section,email</code>. Only name and usn are mandatory. <code>dob</code> may be YYYY-MM-DD or DD-MM-YYYY; the password is then the date of birth (DDMMYYYY). Rows without dob get a temporary password (download the credentials sheet afterwards).</p>
    <input type="file" accept=".csv,text/csv" class="input" id="cf" style="padding-top:6px">${compId ? '<div class="notice small" style="margin-top:10px">Imported and existing students in the file are also assigned to this competition.</div>' : ''}
    <button type="button" class="btn sm ghost" style="margin-top:8px" onclick="download('students-template.csv','name,usn,dob,department,year,semester,section,email\\nAsha Rao,4PM23CS001,15-08-2005,CSE,2,3,A,asha@example.com\\n')">Download template</button>`,
    actions: [{ label: 'Cancel' }, { label: 'Import', kind: 'primary', onClick: async (m) => {
      const f = $('#cf', m).files[0]; if (!f) throw new Error('Choose a CSV file');
      const fd = new FormData(); fd.append('file', f);
      const r = await api('POST', `${A}/students/import${compId ? '?competition_id=' + compId : ''}`, fd);
      modal({ title: 'Import finished', size: 'wide', body: `<p><b>${r.created}</b> created · <b>${r.skipped.length}</b> skipped</p>${r.skipped.length ? `<table class="table"><tbody>${r.skipped.slice(0, 50).map((s) => `<tr><td>Row ${s.row}</td><td class="mono">${esc(s.usn)}</td><td class="small">${esc(s.reason)}</td></tr>`).join('')}</tbody></table>` : ''}`,
        actions: [...(r.credentials.length ? [{ label: 'Download credentials CSV', kind: 'primary', onClick: () => { download('astra-credentials.csv', toCSV([['name', 'usn', 'participant_id', 'password'], ...r.credentials.map((c) => [c.name, c.usn, c.participant_id, c.password])])); return false; } }] : []), { label: 'Close', onClick: () => goRoute() }] });
    } }] });
}
async function participants(p) {
  const q = p.query;
  const v = view('Participants', skeleton(5), 'participants');
  const offset = Number(q.o || 0), limit = 50;
  const d = await GET(`${A}/students?` + new URLSearchParams({ q: q.q || '', department: q.d || '', year: q.y || '', limit, offset }));
  v.innerHTML = ph('Participants', `${d.total} students. Personal details are visible only to organisers.`, `<button class="btn" id="imp">Import CSV</button><button class="btn primary" id="add">+ Add student</button>`) +
    `<form class="card pad row wrap" id="sf" style="margin-bottom:14px"><input class="input" name="q" value="${esc(q.q || '')}" placeholder="Search name, USN, ID, email" style="max-width:300px">${sel('d', [['', 'All departments'], ...d.departments], q.d || '', 'style="width:auto"')}${sel('y', [['', 'All years'], ['1', 'Year 1'], ['2', 'Year 2'], ['3', 'Year 3'], ['4', 'Year 4']], q.y || '', 'style="width:auto"')}<button class="btn">Filter</button></form>
    <div class="card"><div class="table-wrap">${d.rows.length ? `<table class="table"><thead><tr><th>Student</th><th>USN</th><th>Department</th><th class="num">Year</th><th class="num">Competitions</th><th class="num">Solved</th><th>Status</th><th>Last login</th></tr></thead><tbody>${d.rows.map((r) => `<tr class="click" data-u="${r.id}">
      <td><b>${esc(r.name)}</b><div class="tiny muted mono">${esc(r.participant_id)}</div></td><td class="mono small">${esc(r.usn)}</td><td>${esc(r.department)}${r.section ? ' · ' + esc(r.section) : ''}</td><td class="num">${r.year}${r.semester ? `<div class="tiny muted">sem ${r.semester}</div>` : ''}</td><td class="num">${r.competitions}</td><td class="num">${r.solved}</td>
      <td>${r.status === 'active' ? '<span class="badge green">active</span>' : '<span class="badge red">suspended</span>'}</td><td class="small muted">${r.last_login_at ? ago(r.last_login_at) : 'never'}</td></tr>`).join('')}</tbody></table>` : empty('No students found')}</div>${pager(d.total, limit, offset)}</div>`;
  bindPager(v, (o) => nav('/participants?' + new URLSearchParams({ ...q, o })));
  $('#sf').onsubmit = (e) => { e.preventDefault(); nav('/participants?' + new URLSearchParams(formData(e.target))); };
  $('#imp').onclick = () => importCsvModal(null);
  const depSel = (v = '') => sel('department', [['', 'Choose…'], ...(META.departments || [])], v);
  $('#add').onclick = () => modal({ title: 'Add student', body: `<form class="stack" id="nf">${fld('Full name', inp('name', '', 'required'))}<div class="grid g2">${fld('USN', inp('usn', '', 'required'))}${fld('Date of birth', '<input class="input" type="date" name="dob">', 'password = DDMMYYYY')}</div>
    <div class="grid g3">${fld('Department', depSel())}${fld('Year', sel('year', [1, 2, 3, 4], 1, 'data-num="1"'))}${fld('Semester', sel('semester', [1, 2, 3, 4, 5, 6, 7, 8], 1, 'data-num="1"'))}</div>
    <div class="grid g2">${fld('Section', inp('section'))}${fld('Email', inp('email', '', 'type=email'), 'college or personal')}</div>${fld('Password', inp('password', '', 'type=password'), 'empty = date of birth, or a temporary one')}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create', kind: 'primary', onClick: async (m) => { const f = formData($('#nf', m)); if (!f.password) delete f.password; if (!f.dob) delete f.dob; const r = await POST(`${A}/students`, f); r.temporary_password ? secretBox('Student created', `Participant ID ${r.participant_id} · username ${r.username}. Temporary password:`, r.temporary_password) : toast(`Student created. Password: ${f.dob ? 'date of birth (DDMMYYYY)' : 'as set'}`, 'ok'); goRoute(); } }] });
  $$('[data-u]', v).forEach((tr) => tr.onclick = () => studentModal(Number(tr.dataset.u)));
}
async function studentModal(uid) {
  const s = await GET(`${A}/students/${uid}`);
  modal({ title: s.name, size: 'wide', body: `<div class="grid g2" style="align-items:start"><form class="stack" id="ef">${fld('Name', inp('name', s.name))}<div class="grid g2">${fld('Department', inp('department', s.department))}${fld('Section', inp('section', s.section))}</div>
      <div class="grid g3">${fld('Year', sel('year', [1, 2, 3, 4, 5], s.year, 'data-num="1"'))}${fld('Semester', sel('semester', [['', '—'], 1, 2, 3, 4, 5, 6, 7, 8], s.semester || '', 'data-num="1"'))}${fld('Status', sel('status', ['active', 'suspended'], s.status))}</div>
      <div class="grid g2">${fld('Email', inp('email', s.email || ''))}${fld('Date of birth', `<input class="input" type="date" name="dob" value="${esc(s.dob || '')}">`)}</div>
      <div class="small muted">ID <span class="mono">${esc(s.participant_id)}</span> · USN <span class="mono">${esc(s.usn)}</span> · joined ${fmtDate(s.created_at)} · last login ${s.last_login_at ? fmtTime(s.last_login_at) + ' from ' + esc(s.last_login_ip) : 'never'}</div></form>
    <div><h4>Competition history</h4>${s.history.length ? `<table class="table"><tbody>${s.history.map((h) => `<tr><td>${esc(h.title)}<div class="tiny muted">${esc(h.status)}</div></td><td class="num">${h.rank ? `#${h.rank}/${h.of}` : '—'}</td><td class="num"><b>${h.score ?? '—'}</b></td></tr>`).join('')}</tbody></table>` : '<p class="small muted">None</p>'}
      <h4 style="margin-top:14px">Recent submissions</h4>${s.submissions.slice(0, 10).map((x) => `<div class="row small" style="padding:3px 0">${vbadge(x.verdict, 'done')}<span class="grow">${esc(x.title)}</span><span class="muted">${esc(x.competition || 'practice')}</span></div>`).join('') || '<p class="small muted">None</p>'}</div></div>`,
    actions: [...(s.avatar ? [{ label: 'Remove photo', kind: 'danger', onClick: async () => { await DEL(`${A}/students/${uid}/avatar`); toast('Photo removed', 'ok'); return false; } }] : []), { label: 'Export history CSV', onClick: () => { location.href = `${A}/reports/student.csv?user_id=${uid}`; return false; } }, { label: 'Reset password', onClick: async () => { if (await confirmBox('Reset this password (to the date of birth if known, otherwise a temporary one)? The student is signed out everywhere.')) { const r = await POST(`${A}/students/${uid}/reset-password`); r.is_dob ? toast(`${s.name}'s password is reset to their date of birth (DDMMYYYY).`, 'ok') : secretBox('Temporary password', `Give this to ${s.name}. They must change it after signing in.`, r.temporary_password); } return false; } },
      { label: 'Save', kind: 'primary', onClick: async (m) => { const f = formData($('#ef', m)); if (!f.dob) f.dob = null; await PUT(`${A}/students/${uid}`, f); toast('Saved', 'ok'); goRoute(); } }] });
}

// ================= submissions =================
async function subModal(sid) {
  const s = await GET(`${A}/submissions/${sid}`);
  const m = modal({ title: `Submission #${s.id}`, size: 'xwide', body: `<div class="row wrap" style="margin-bottom:12px">${vbadge(s.verdict, s.status)}<b>${esc(s.name)}</b><span class="small muted">${esc(s.title)} · ${langName(s.language)} · ${s.kind} · ${fmtTime(s.created_at)} · IP ${esc(s.ip || '—')} · score ${s.score} · ${s.passed}/${s.total} · ${s.time_ms ?? '—'} ms · ${s.memory_kb ? (s.memory_kb / 1024).toFixed(1) + ' MB' : '—'}${s.attempts > 1 ? ` · judged ${s.attempts}×` : ''}</span></div>
    <div class="grid g2" style="align-items:start"><div><div id="sed" style="height:56vh;border:1px solid var(--border);border-radius:8px"></div></div><div style="max-height:56vh;overflow:auto">${s.compile_output ? `<pre style="color:var(--red)">${esc(s.compile_output)}</pre>` : ''}
      ${s.results.map((r) => `<details style="border-bottom:1px solid var(--border);padding:6px 0"><summary class="row" style="cursor:pointer"><span class="mono small">#${r.ordinal}</span>${vbadge(r.verdict, 'done')}<span class="small muted">${r.visibility} · w${r.weight} · ${r.time_ms} ms · ${(r.memory_kb / 1024).toFixed(1)} MB</span></summary>
        <div class="io" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-top:6px"><div><div class="lbl">Input</div><pre>${esc(r.input)}</pre></div><div><div class="lbl">Expected</div><pre>${esc(r.expected)}</pre></div><div><div class="lbl">Output</div><pre>${esc(r.output)}</pre></div></div>${r.stderr ? `<pre style="color:var(--red)">${esc(r.stderr)}</pre>` : ''}</details>`).join('') || empty('No per-test results')}</div></div>`,
    actions: [{ label: 'Re-judge', onClick: async () => { await POST(`${A}/submissions/${sid}/rejudge`); toast('Re-queued', 'ok'); } }, { label: 'Close', kind: 'primary' }] });
  const ed = await codeEditor($('#sed', m.el), { value: s.code, language: META.languages[s.language]?.monaco || 'plaintext', readOnly: true, fontSize: 13 });
  const obs = new MutationObserver(() => { if (!m.el.isConnected) { ed.dispose(); obs.disconnect(); } }); obs.observe(document.body, { childList: true });
}
async function submissionsTable(T, filters) {
  const draw = async (offset = 0) => {
    const d = await GET(`${A}/submissions?` + new URLSearchParams(Object.fromEntries(Object.entries({ ...filters, limit: 50, offset }).filter(([, x]) => x !== '' && x != null))));
    T.innerHTML = `<div class="card"><div class="table-wrap">${d.rows.length ? `<table class="table"><thead><tr><th>#</th><th>When</th><th>Participant</th><th>Question</th>${filters.competition_id ? '' : '<th>Competition</th>'}<th>Lang</th><th>Verdict</th><th class="num">Score</th><th class="num">Tests</th><th class="num">Time</th><th>IP</th></tr></thead><tbody>
      ${d.rows.map((r) => `<tr class="click" data-s="${r.id}"><td class="mono small">${r.id}</td><td class="small nowrap">${fmtTime(r.created_at)}</td><td><b>${esc(r.name)}</b><div class="tiny muted mono">${esc(r.participant_id || '')}</div></td><td>${esc(r.title)}</td>${filters.competition_id ? '' : `<td class="small mono">${esc(r.competition || 'practice')}</td>`}
        <td class="small">${langName(r.language)}</td><td>${vbadge(r.verdict, r.status)}</td><td class="num">${r.score}</td><td class="num">${r.passed}/${r.total}</td><td class="num small">${r.time_ms ?? '—'}</td><td class="small mono muted">${esc(r.ip || '')}</td></tr>`).join('')}</tbody></table>` : empty('No submissions')}</div>${pager(d.total, 50, offset)}</div>`;
    $$('[data-s]', T).forEach((tr) => tr.onclick = () => subModal(Number(tr.dataset.s)));
    bindPager(T, draw);
  };
  T.innerHTML = skeleton(5); await draw();
}
async function submissionsPage(p) {
  const q = p.query;
  const v = view('Submissions', '', 'submissions');
  const comps = await GET(`${A}/competitions`);
  v.innerHTML = ph('Submission monitor', 'Every judged submission with per-test results. Hidden test data is visible here to organisers only.') +
    `<form class="card pad row wrap" id="ff" style="margin-bottom:14px">${sel('competition_id', [['', 'All competitions'], ...comps.map((c) => [c.id, `${c.code} · ${c.title}`])], q.competition_id || '', 'style="width:auto;max-width:360px"')}
    ${sel('kind', [['submit', 'Contest submissions'], ['practice', 'Practice'], ['run', 'Sample runs'], ['validate', 'Validation jobs']], q.kind || 'submit', 'style="width:auto"')}
    ${sel('verdict', [['', 'Any verdict'], 'AC', 'WA', 'CE', 'RE', 'TLE', 'MLE', 'IE', ['queued', 'Queued'], ['running', 'Running'], ['error', 'Judge error']], q.verdict || '', 'style="width:auto"')}<button class="btn">Filter</button></form><div id="st"></div>`;
  $('#ff').onsubmit = (e) => { e.preventDefault(); nav('/submissions?' + new URLSearchParams(formData(e.target))); };
  submissionsTable($('#st'), { competition_id: q.competition_id || '', kind: q.kind || 'submit', verdict: q.verdict || '' });
}

// ================= leaderboards / semesters / reports =================
async function leaderboards() {
  const v = view('Leaderboards', skeleton(3), 'leaderboards');
  const comps = (await GET(`${A}/competitions`)).filter((c) => c.status !== 'draft');
  const sems = await GET(`${A}/semesters`).catch(() => []);
  v.innerHTML = ph('Leaderboards', 'Competition leaderboards are computed from stored submissions; finalised rounds use a frozen snapshot.') +
    `<div class="grid g2" style="align-items:start"><section class="card"><div class="card-head"><h3>Competitions</h3></div>${comps.length ? `<table class="table"><tbody>${comps.map((c) => `<tr class="click" onclick="nav('/competitions/${c.id}?t=leaderboard')"><td><b>${esc(c.title)}</b><div class="tiny mono muted">${esc(c.code)}</div></td><td>${badge(c.display_status)}</td><td class="num">${c.participants}</td></tr>`).join('')}</tbody></table>` : empty('No published competitions')}</section>
    <section class="card"><div class="card-head"><h3>Semesters</h3></div>${sems.length ? `<table class="table"><tbody>${sems.map((s) => `<tr class="click" data-sem="${s.id}"><td><b>${esc(s.name)}</b><div class="tiny muted">${esc(s.academic_year)} · ${s.competitions.length} rounds</div></td><td><span class="badge">${esc(s.status)}</span></td></tr>`).join('')}</tbody></table>` : empty('No semesters')}</section></div>`;
  $$('[data-sem]', v).forEach((tr) => tr.onclick = () => semLbModal(Number(tr.dataset.sem)));
}
async function semLbModal(sid) {
  const d = await GET(`${A}/semesters/${sid}/leaderboard`);
  modal({ title: `${d.semester.name} leaderboard`, size: 'xwide', body: d.rows.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Rank</th><th>Participant</th><th class="num">Points</th><th class="num">Solved</th><th class="num">Rounds</th>${d.competitions.map((c) => `<th class="num">${esc(c.code)}<div class="tiny faint">${c.points}</div></th>`).join('')}</tr></thead><tbody>
    ${d.rows.map((r) => `<tr><td>${rankBadge(r.rank)}</td><td><b>${esc(r.name)}</b><div class="tiny muted">${esc(r.participant_id || '')} · ${esc(r.department || '')}</div></td><td class="num"><b>${r.points}</b></td><td class="num">${r.solved}</td><td class="num">${r.played}</td>${d.competitions.map((c) => `<td class="num">${r.rounds[c.id] ?? '·'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : empty('No points yet'),
    actions: [{ label: 'Export CSV', onClick: () => { location.href = `${A}/reports/semester.csv?semester_id=${sid}`; return false; } }, { label: 'Close', kind: 'primary' }] });
}
async function semesters() {
  const v = view('Semesters', skeleton(4), 'semesters');
  const [sems, comps] = await Promise.all([GET(`${A}/semesters`), GET(`${A}/competitions`)]);
  const w = ME.role === 'super_admin';
  v.innerHTML = ph('Semester management', 'Only competitions you assign here count. Each round contributes (score ÷ round max) × its points.', w ? '<button class="btn primary" id="ns">+ New semester</button>' : '') +
    (sems.length ? `<div class="stack">${sems.map((s) => `<section class="card"><div class="card-head"><div><h3>${esc(s.name)}</h3><div class="small muted">${esc(s.academic_year)} ${esc(s.term)} · <span class="badge">${esc(s.status)}</span></div></div>
      <div class="row wrap"><button class="btn sm" data-lb="${s.id}">Leaderboard</button>${w ? { active: `<button class="btn sm" data-sa="freeze" data-s="${s.id}">Freeze</button><button class="btn sm primary" data-sa="finalize" data-s="${s.id}">Finalise</button>`, frozen: `<button class="btn sm" data-sa="unfreeze" data-s="${s.id}">Unfreeze</button><button class="btn sm primary" data-sa="finalize" data-s="${s.id}">Finalise</button>`, finalized: `<button class="btn sm" data-sa="archive" data-s="${s.id}">Archive</button>`, archived: '' }[s.status] : ''}</div></div>
      <div class="card-body">${s.competitions.length ? `<table class="table"><tbody>${s.competitions.map((c) => `<tr><td><b>${esc(c.title)}</b><div class="tiny mono muted">${esc(c.code)}</div></td><td><span class="badge">${esc(c.status)}</span></td><td class="num"><b>${c.points}</b> pts</td><td class="right">${w && s.status === 'active' ? `<button class="btn xs ghost" data-rm="${c.id}" data-s="${s.id}">Remove</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<p class="small muted">No competitions assigned.</p>'}
      ${w && s.status === 'active' ? `<form class="row" data-add="${s.id}" style="margin-top:10px">${sel('competition_id', comps.map((c) => [c.id, `${c.code} · ${c.title}`]), '', 'data-num="1"')}<input class="input" name="points" type="number" value="100" min="0" style="width:110px" data-num="1"><button class="btn">Add / update points</button></form>` : ''}</div></section>`).join('')}</div>` : `<div class="card">${empty('No semesters yet')}</div>`);
  if ($('#ns')) $('#ns').onclick = () => modal({ title: 'New semester', body: `<form class="stack" id="sf">${fld('Name', inp('name', '', 'placeholder="Odd Semester 2026-27" required'))}<div class="grid g2">${fld('Academic year', inp('academic_year', '', 'placeholder="2026-27" required'))}${fld('Term', inp('term', '', 'placeholder="Odd"'))}</div></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create', kind: 'primary', onClick: async (m) => { await POST(`${A}/semesters`, formData($('#sf', m))); goRoute(); } }] });
  $$('[data-lb]', v).forEach((b) => b.onclick = () => semLbModal(Number(b.dataset.lb)));
  $$('[data-sa]', v).forEach((b) => b.onclick = async () => { if (await confirmBox(`${b.dataset.sa[0].toUpperCase() + b.dataset.sa.slice(1)} this semester?${b.dataset.sa === 'freeze' || b.dataset.sa === 'finalize' ? ' The current standings are saved as a snapshot.' : ''}`)) { await POST(`${A}/semesters/${b.dataset.s}/action`, { action: b.dataset.sa }).catch(fail); goRoute(); } });
  $$('[data-rm]', v).forEach((b) => b.onclick = async () => { await DEL(`${A}/semesters/${b.dataset.s}/competitions/${b.dataset.rm}`).catch(fail); goRoute(); });
  $$('[data-add]', v).forEach((f) => f.onsubmit = (e) => { e.preventDefault(); busy($('button', f), async () => { await POST(`${A}/semesters/${f.dataset.add}/competitions`, formData(f)); toast('Saved', 'ok'); goRoute(); }); });
}
async function reports() {
  const v = view('Reports', skeleton(3), 'reports');
  const [comps, sems] = await Promise.all([GET(`${A}/competitions`), GET(`${A}/semesters`).catch(() => [])]);
  v.innerHTML = ph('Reports & exports', 'CSV files open in Excel / Google Sheets and are suitable for college records.') +
    `<div class="grid g2" style="align-items:start"><section class="card pad stack"><h3 style="margin:0">Competition reports</h3>${sel('c', comps.map((c) => [c.id, `${c.code} · ${c.title}`]), '', 'id="rc"')}<div id="rb"></div></section>
    <section class="card pad stack"><h3 style="margin:0">Semester leaderboard</h3>${sems.length ? `${sel('s', sems.map((s) => [s.id, s.name]), '', 'id="rs"')}<a class="btn" id="rsb">Download semester CSV</a>` : '<p class="small muted">No semesters.</p>'}
      <div class="divider"></div><p class="small muted" style="margin:0">Individual student reports: open a student in <a href="#/participants">Participants</a> ${ico('right')} Export history CSV.</p></section></div>`;
  const drawB = () => { const id = $('#rc').value; $('#rb').innerHTML = id ? [['participants', 'Participant list'], ['scores', 'Final rankings & scores'], ['questions', 'Question-wise performance'], ['submissions', 'Submission statistics'], ['integrity', 'Fair-play report']].map(([k, l]) => `<a class="btn block" style="margin-bottom:6px;justify-content:flex-start" href="${A}/reports/${k}.csv?competition_id=${id}">⬇ ${l}</a>`).join('') : empty('No competitions'); };
  if ($('#rc')) { $('#rc').onchange = drawB; drawB(); }
  if ($('#rs')) { const upd = () => $('#rsb').href = `${A}/reports/semester.csv?semester_id=${$('#rs').value}`; $('#rs').onchange = upd; upd(); }
}

// ================= database =================
async function databasePage(p) {
  const v = view('Database', skeleton(6), 'database');
  const tbl = p.query.t || 'competitions';
  const o = await GET(`${A}/db/overview`);
  const integ = o.integrity;
  v.innerHTML = ph('Database', `SQLite · ${esc(o.path)} · schema v${o.schema_version}`, `<button class="btn" id="upB">${ico('upload', 14)} Upload a backup</button><button class="btn primary" id="bkB">${ico('download', 14)} Back up now</button>`) + `
    <div class="minis big" style="margin-bottom:16px"><div><b>${o.size_mb} MB</b><span>database size</span></div><div><b>${o.wal_mb} MB</b><span>write log (WAL)</span></div>
      <div><b>${o.tables.reduce((a, t) => a + t.rows, 0).toLocaleString('en-IN')}</b><span>rows in ${o.tables.length} tables</span></div><div><b>${o.disk_free_gb} GB</b><span>disk free</span></div>
      <div><b style="color:${!integ ? 'var(--muted)' : integ.ok ? 'var(--green)' : 'var(--red)'}">${!integ ? 'Not checked' : integ.ok ? 'Healthy' : 'Problem'}</b><span>${integ ? 'integrity checked ' + ago(integ.at) : 'run an integrity check'}</span></div>
      <div><b>${o.backups.length}</b><span>backups (keeps ${o.backup_keep})</span></div></div>
    ${integ && !integ.ok ? `<div class="notice err" style="margin-bottom:14px">${esc(integ.detail)}. Restore the most recent healthy backup below.</div>` : ''}
    <div class="grid g2" style="align-items:stretch">
      <section class="card flexcard"><div class="card-head"><h3 class="row" style="gap:8px">${ico('settings', 17)} Maintenance</h3><span class="tiny muted">runs automatically too</span></div><div class="card-body stack">
        ${[['integrity', 'check2', 'Integrity check', 'Verifies every page of the database file. Automatic weekly.'], ['checkpoint', 'layers', 'Checkpoint write log', 'Folds the WAL into the main file. Automatic hourly.'],
          ['optimize', 'zap', 'Optimise queries', 'Refreshes query statistics so pages stay fast. Automatic daily.'], ['vacuum', 'cpu', 'Compact database', 'Reclaims free space. Only when no round is live.'],
          ['purge_sessions', 'logout', 'Clear expired sign-ins', 'Removes old session records.']].map(([a, i, t, d]) => `<div class="mrow"><span class="lsi sm">${ico(i, 15)}</span><div class="grow"><b class="small">${t}</b><div class="tiny muted">${d}</div></div><button class="btn sm" data-m="${a}">Run</button></div>`).join('')}
        <div class="mrow"><span class="lsi sm">${ico('x', 15)}</span><div class="grow"><b class="small">Clean up code snapshots</b><div class="tiny muted">${o.snapshots.rows.toLocaleString('en-IN')} snapshots (${o.snapshots.mb} MB). Only finished competitions; results, pastes and tab switches are kept.</div></div>
          <input class="input" id="pDays" type="number" value="90" min="1" style="width:80px" title="older than (days)"><button class="btn sm" data-m="purge_snapshots">Clean</button></div>
      </div></section>
      <section class="card flexcard"><div class="card-head"><h3 class="row" style="gap:8px">${ico('download', 17)} Backups</h3><span class="tiny muted mono">${esc(o.backup_dir)}</span></div>
        <div class="grow" style="max-height:360px;overflow:auto">${o.backups.length ? o.backups.map((b) => `<div class="mrow" style="padding:10px 18px;border-bottom:1px solid var(--border)"><div class="grow"><b class="small mono">${esc(b.name)}</b><div class="tiny muted">${b.size_mb} MB · ${fmtTime(b.at)}</div></div>
          <a class="btn xs" href="${A}/system/backups/${encodeURIComponent(b.name)}">${ico('download', 12)} Download</a><button class="btn xs" data-rs="${esc(b.name)}">${ico('rotate', 12)} Restore</button><button class="btn xs ghost" data-rm="${esc(b.name)}" title="Delete">${ico('x', 12)}</button></div>`).join('')
          : `<div class="empty"><div class="emptyi">${ico('download', 24)}</div><div class="t">No backups yet</div><div class="small">A backup is taken automatically every day. Click "Back up now" for one immediately.</div></div>`}</div></section>
    </div>
    <section class="card" style="margin-top:16px"><div class="card-head"><h3 class="row" style="gap:8px">${ico('grid', 17)} Browse data</h3><span class="tiny muted">Read-only. Passwords and session secrets are never shown.</span></div>
      <div class="dbbrowse"><nav class="dbtabs">${o.tables.map((t) => `<a href="#/database?t=${t.name}" class="${t.name === tbl ? 'on' : ''}"><span>${esc(t.name)}</span><span class="tiny muted">${t.rows.toLocaleString('en-IN')}</span></a>`).join('')}</nav>
      <div class="dbview"><div class="row" style="padding:12px 14px;border-bottom:1px solid var(--border)"><input class="input" id="dq" placeholder="Search ${esc(tbl)}" style="max-width:320px"><div class="grow"></div><a class="btn sm" href="${A}/db/export/${tbl}">${ico('download', 13)} Export CSV</a></div><div id="drows">${skeleton(4)}</div></div></div></section>`;
  const loadRows = async (offset = 0) => {
    const d = await GET(`${A}/db/table/${tbl}?` + new URLSearchParams({ q: $('#dq').value, limit: 50, offset }));
    $('#drows').innerHTML = `<div class="table-wrap"><table class="table dbt"><thead><tr>${d.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${d.rows.map((r) => `<tr>${r.map((x) => `<td>${x === null ? '<span class="faint">null</span>' : esc(x)}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${d.columns.length}">${empty('No rows')}</td></tr>`}</tbody></table></div>${pager(d.total, 50, offset)}`;
    bindPager($('#drows'), loadRows);
  };
  let dt; $('#dq').oninput = () => { clearTimeout(dt); dt = setTimeout(() => loadRows(0), 250); };
  loadRows();
  $('#bkB').onclick = () => busy($('#bkB'), async () => { const r = await POST(`${A}/db/backup`); toast('Backup written: ' + r.file, 'ok'); goRoute(); });
  $('#upB').onclick = () => modal({ title: 'Upload a backup file', body: `<p class="small muted">Adds a <code>.db</code> backup (for example copied from another server) to the list. It is checked before it can be restored.</p><input type="file" class="input" id="uf" accept=".db" style="padding-top:6px">`,
    actions: [{ label: 'Cancel' }, { label: 'Upload', kind: 'primary', onClick: async (m) => { const f = $('#uf', m).files[0]; if (!f) throw new Error('Choose a file'); const fd = new FormData(); fd.append('file', f); const r = await api('POST', `${A}/db/upload-backup`, fd); toast('Uploaded as ' + r.file, 'ok'); goRoute(); } }] });
  $$('[data-m]', v).forEach((b) => b.onclick = async () => {
    if (b.dataset.m === 'vacuum' && !(await confirmBox('Compact the database now? It takes a few seconds and pauses other requests briefly.', { ok: 'Compact' }))) return;
    if (b.dataset.m === 'purge_snapshots' && !(await confirmBox(`Delete code snapshots older than ${$('#pDays').value} days from finished competitions? This cannot be undone (take a backup first if unsure).`, { ok: 'Clean up', danger: true }))) return;
    busy(b, async () => { const r = await POST(`${A}/db/maintenance`, { action: b.dataset.m, days: Number($('#pDays').value) || 90 }); toast(r.message, 'ok'); goRoute(); });
  });
  $$('[data-rm]', v).forEach((b) => b.onclick = async () => { if (await confirmBox(`Delete backup ${b.dataset.rm}?`, { danger: true, ok: 'Delete' })) { await DEL(`${A}/db/backups/${encodeURIComponent(b.dataset.rm)}`).catch(fail); goRoute(); } });
  $$('[data-rs]', v).forEach((b) => b.onclick = () => modal({ title: 'Restore this backup', body: `<div class="notice warn small">This replaces <b>all</b> current data with <span class="mono">${esc(b.dataset.rs)}</span>. A safety backup of the current data is taken first, so you can undo it. Do this only when no competition is running.</div>
      <p class="small" style="margin:12px 0 6px">Type <b>RESTORE</b> to confirm.</p><input class="input" id="rc" autocomplete="off">`,
    actions: [{ label: 'Cancel' }, { label: 'Restore', kind: 'danger solid', onClick: async (m) => { const r = await POST(`${A}/db/restore`, { name: b.dataset.rs, confirm: $('#rc', m).value }); toast(`Restored. Safety backup: ${r.safety_backup}`, 'ok'); goRoute(); } }] }));
}

// ================= admin users / audit / settings =================
async function adminsPage() {
  const v = view('Admin Users', skeleton(4), 'admins');
  const rows = await GET(`${A}/users`);
  v.innerHTML = ph('Admin users', 'There is no public admin registration. Accounts are created here by a Super Admin.', '<button class="btn primary" id="na">+ New admin</button>') +
    `<div class="card"><table class="table"><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Status</th><th>Last login</th><th></th></tr></thead><tbody>${rows.map((r) => `<tr><td><b>${esc(r.name)}</b><div class="tiny muted">${esc(r.email || '')}</div></td><td class="mono small">${esc(r.username)}</td>
      <td>${ROLE[r.role]}${r.role === 'reviewer' && r.can_approve ? ' <span class="badge violet">can approve</span>' : ''}</td><td>${r.status === 'active' ? '<span class="badge green">active</span>' : '<span class="badge red">suspended</span>'}${r.locked_until > Date.now() / 1000 ? ' <span class="badge amber">locked</span>' : ''}</td>
      <td class="small muted">${r.last_login_at ? ago(r.last_login_at) + ' · ' + esc(r.last_login_ip || '') : 'never'}</td><td class="right nowrap"><button class="btn xs" data-e="${r.id}">Edit</button> <button class="btn xs" data-r="${r.id}">Reset password</button></td></tr>`).join('')}</tbody></table></div>`;
  const roles = [['event_admin', 'Event Admin'], ['reviewer', 'Question Reviewer'], ['super_admin', 'Super Admin']];
  $('#na').onclick = () => modal({ title: 'New admin', body: `<form class="stack" id="af">${fld('Full name', inp('name', '', 'required'))}<div class="grid g2">${fld('Username', inp('username', '', 'required'))}${fld('Email', inp('email', '', 'type=email'))}</div>${fld('Role', sel('role', roles, 'event_admin'))}${chk('can_approve', false, 'Reviewer may approve questions')}${fld('Password', inp('password', '', 'type=password'), 'empty = generate a temporary one')}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Create', kind: 'primary', onClick: async (m) => { const f = formData($('#af', m)); if (!f.password) delete f.password; const r = await POST(`${A}/users`, f); r.temporary_password ? secretBox('Admin created', 'Temporary password (must be changed at first sign-in):', r.temporary_password) : toast('Admin created', 'ok'); goRoute(); } }] });
  $$('[data-e]', v).forEach((b) => b.onclick = () => { const r = rows.find((x) => x.id == b.dataset.e); modal({ title: `Edit ${r.username}`, body: `<form class="stack" id="ef">${fld('Name', inp('name', r.name))}${fld('Email', inp('email', r.email || ''))}${fld('Role', sel('role', roles, r.role))}${chk('can_approve', r.can_approve, 'Reviewer may approve questions')}${fld('Status', sel('status', ['active', 'suspended'], r.status))}</form>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', kind: 'primary', onClick: async (m) => { await PUT(`${A}/users/${r.id}`, formData($('#ef', m))); toast('Saved', 'ok'); goRoute(); } }] }); });
  $$('[data-r]', v).forEach((b) => b.onclick = async () => { if (await confirmBox('Generate a new temporary password for this admin? They are signed out everywhere.')) { const r = await POST(`${A}/users/${b.dataset.r}/reset-password`).catch(fail); if (r) secretBox('Temporary password', 'Share it securely:', r.temporary_password); } });
}
async function auditPage(p) {
  const q = p.query, offset = Number(q.o || 0);
  const v = view('Audit Logs', skeleton(5), 'audit');
  const d = await GET(`${A}/audit?` + new URLSearchParams({ action: q.action || '', limit: 100, offset }));
  v.innerHTML = ph('Audit logs', 'Every organiser action with who, when, what and from which IP.') +
    `<form class="card pad row" id="af" style="margin-bottom:14px"><input class="input" name="action" value="${esc(q.action || '')}" placeholder="Filter by action, e.g. competition. or login" style="max-width:320px"><button class="btn">Filter</button></form>
    <div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Details</th><th>IP</th></tr></thead><tbody>${d.rows.map((r) => `<tr><td class="small nowrap">${fmtTime(r.created_at)}</td><td>${esc(r.name || 'system')}</td><td class="mono small">${esc(r.action)}</td>
      <td class="small">${r.entity ? esc(r.entity) + ' #' + r.entity_id : ''}${r.competition_id ? `<div class="tiny muted">competition #${r.competition_id}</div>` : ''}</td><td class="mono tiny muted" style="max-width:380px;word-break:break-all">${esc((r.detail || '').slice(0, 240))}</td><td class="mono small muted">${esc(r.ip || '')}</td></tr>`).join('') || `<tr><td colspan="6">${empty('No entries')}</td></tr>`}</tbody></table></div>${pager(d.total, 100, offset)}</div>`;
  bindPager(v, (o) => nav('/audit?' + new URLSearchParams({ ...q, o })));
  $('#af').onsubmit = (e) => { e.preventDefault(); nav('/audit?' + new URLSearchParams(formData(e.target))); };
}
async function settingsPage() {
  const v = view('System Settings', skeleton(5), 'settings');
  const load = async () => {
    const [st, sy, anns, dbi] = await Promise.all([GET(`${A}/settings`), GET(`${A}/system`), GET(`${A}/announcements`), GET(`${A}/system/database`)]);
    const s = st.settings;
    v.innerHTML = ph('System settings & health') + `<div class="grid g2" style="align-items:start"><div class="stack">
      <form class="card pad stack" id="sf"><h3 style="margin:0">Platform</h3>${chk('registration_open', s.registration_open === 'true', 'Students can self-register')}${chk('highlights_enabled', s.highlights_enabled === 'true', 'Show past highlights on the landing page')}
        ${fld('Names on public leaderboards', sel('name_display', [['full', 'Full name'], ['initials', 'Initials + surname'], ['participant_id', 'Participant ID only']], s.name_display))}
        ${fld('Submission cooldown (seconds)', inp('submit_cooldown_sec', s.submit_cooldown_sec, 'type=number min=0 max=300'))}${fld('Landing page banner', inp('site_banner', s.site_banner, 'maxlength=300'))}
<button class="btn primary">Save settings</button></form>
      <section class="card pad stack"><div class="row between"><div><h3 style="margin:0">Opening hours</h3><div class="small muted">When students can use Code Arena. Outside these hours they see a "closed, opens at…" page. Admins always have access, and it never closes during a live round.</div></div>
        <label class="check"><input type="checkbox" id="schOn" ${s.schedule.enabled ? 'checked' : ''}> On</label></div>
        <div id="schRows" class="stack tight"></div>
        <div class="row"><button class="btn sm" type="button" id="schAdd">${ico('plus', 13)} Add time window</button><input class="input" id="schMsg" placeholder="Message on the closed page (optional)" value="${esc(s.schedule.message || '')}"></div>
        <button class="btn primary" type="button" id="schSave">Save opening hours</button></section>
      <section class="card pad stack"><div><h3 style="margin:0">Departments</h3><div class="small muted">Students choose from this list when they register; competitions use it for eligibility.</div></div>
        <div class="row"><input class="input" id="depIn" placeholder="e.g. CSE (DS)" maxlength="40"><button class="btn primary" id="depAdd" type="button">${ico('plus', 14)} Add</button></div>
        <div class="chips" id="depList"></div></section>
      <section class="card"><div class="card-head"><h3>Global announcements</h3></div><div class="card-body"><form class="row" id="gf"><input class="input" name="title" required placeholder="Shown on the landing page"><button class="btn">Post</button></form></div>
        ${anns.filter((a) => !a.competition_id).map((a) => `<div class="row between" style="padding:8px 18px;border-top:1px solid var(--border)"><span>${esc(a.title)} <span class="tiny muted">${ago(a.created_at)}</span></span><button class="btn xs ghost" data-da="${a.id}">${ico('x')}</button></div>`).join('')}</section>
      <section class="card pad"><h3>Deployment</h3><dl class="kv">${Object.entries(st.env).map(([k, x]) => `<dt class="mono small">${esc(k)}</dt><dd class="small">${esc(String(x))}</dd>`).join('')}</dl><p class="tiny muted" style="margin:8px 0 0">These come from the server's .env file and are changed by the server operator.</p></section></div>
      <div class="stack"><section class="card pad"><div class="row between"><h3 style="margin:0">System health</h3><span class="tiny muted">${fmtClock(sy.server_time)}</span></div>
        <dl class="kv" style="margin-top:10px"><dt>Backend</dt><dd style="color:var(--green)">${ico('dot')} ${esc(sy.backend)}</dd><dt>Database</dt><dd style="color:var(--green)">${ico('dot')} ${esc(sy.database)} · ${sy.db_size_mb} MB</dd>
        <dt>Judge workers</dt><dd>${sy.workers.length ? sy.workers.map((w) => `<div style="color:${w.alive ? 'var(--green)' : 'var(--red)'}">${ico('dot')} ${esc(w.name)} <span class="muted small">${esc(w.state || '')} · ${w.age_sec}s ago</span></div>`).join('') : '<span style="color:var(--red)">' + ico('dot') + ' No worker running. Start it: python -m app.worker</span>'}${sy.workers[0] ? `<div class="tiny muted">Sandbox: ${esc(sy.workers[0].sandbox || '')}</div>` : ''}</dd>
        <dt>Machine</dt><dd>${sy.host?.cpu != null ? `CPU ${sy.host.cpu}%` : 'CPU —'} · ${sy.host?.ram_pct != null ? `RAM ${sy.host.ram_pct}% of ${Math.round(sy.host.ram_total_mb / 1024)} GB` : 'RAM —'}</dd>
        <dt>Queue</dt><dd>${sy.queue.queued} queued · ${sy.queue.running} running · ${sy.queue.judge_errors} judge errors</dd><dt>Disk</dt><dd>${sy.disk.free_gb} GB free of ${sy.disk.total_gb} GB (${sy.disk.used_pct}% used)</dd>
        <dt>Last backup</dt><dd>${sy.backup.last_at ? `${fmtTime(sy.backup.last_at)}<div class="tiny muted mono">${esc(sy.backup.file)}</div>` : '<span style="color:var(--amber)">never</span>'}</dd>
        <dt>Languages</dt><dd>${Object.values(sy.languages).map((l) => `<span class="badge ${l.available ? 'green' : 'red'}" style="margin:2px">${esc(l.name)}</span>`).join('')}</dd><dt>AI</dt><dd>${sy.ai.enabled ? `${esc(sy.ai.model || '')} ${sy.ai.reachable === false ? '<span style="color:var(--red)">unreachable</span>' : '<span style="color:var(--green)">ok</span>'}` : 'not configured (optional)'}</dd></dl>
        <button class="btn" id="bk" style="margin-top:10px">Back up database now</button></section>
        <section class="card"><div class="card-head"><h3 class="row" style="gap:8px">${ico('layers', 17)} Database</h3><button class="btn sm" id="dbCheck">${ico('check2', 14)} Run integrity check</button></div>
          <div class="card-body stack"><a class="small row" style="gap:4px" href="#/database">Open the Database page ${ico('right', 13)}</a>
            <div class="minis"><div><b>${dbi.size_mb} MB</b><span>database</span></div><div><b>${dbi.wal_mb} MB</b><span>write log</span></div><div><b>v${dbi.schema_version}</b><span>schema</span></div>
              <div><b style="color:${!dbi.integrity ? 'var(--muted)' : dbi.integrity.ok ? 'var(--green)' : 'var(--red)'}">${!dbi.integrity ? '—' : dbi.integrity.ok ? 'OK' : 'Problem'}</b><span>${dbi.integrity ? 'checked ' + ago(dbi.integrity.at) : 'not checked yet'}</span></div></div>
            ${dbi.integrity && !dbi.integrity.ok ? `<div class="notice err small">${esc(dbi.integrity.detail)}. Restore the latest backup (see docs/BACKUP_RECOVERY.md).</div>` : ''}
            <div class="small muted">Automatic: write-log checkpoint hourly, optimisation daily, integrity check weekly, backup daily (keeps the newest ${dbi.backup_keep}).</div>
            <details><summary class="small" style="cursor:pointer">Table sizes</summary><div class="dbtables">${dbi.tables.map((t) => `<span><span class="muted">${esc(t.name)}</span><b>${Number(t.rows).toLocaleString('en-IN')}</b></span>`).join('')}</div></details>
            <div><div class="small" style="font-weight:600;margin-bottom:6px">Backups</div>${dbi.backups.length ? `<div class="stack tight" style="max-height:200px;overflow:auto">${dbi.backups.map((b) => `<div class="row between small"><span class="mono">${esc(b.name)}</span><span class="muted">${b.size_mb} MB · ${ago(b.at)}</span><a class="btn xs" href="${A}/system/backups/${encodeURIComponent(b.name)}">${ico('download', 12)} Download</a></div>`).join('')}</div>` : '<div class="small muted">No backups yet. Use "Back up database now".</div>'}</div>
          </div></section>
        <section class="card"><div class="card-head"><h3>Recent errors</h3></div>${sy.recent_errors.length ? `<div style="max-height:360px;overflow:auto">${sy.recent_errors.map((e) => `<div style="padding:8px 18px;border-bottom:1px solid var(--border)"><div class="row between small"><b>${esc(e.source)}</b><span class="muted">${ago(e.created_at)}</span></div><pre style="font-size:11px;max-height:90px;margin:4px 0 0">${esc(e.message)}</pre></div>`).join('')}</div>` : empty('No errors logged')}</section></div></div>`;
    $('#sf').onsubmit = (e) => { e.preventDefault(); busy($('button', e.target), async () => { await PUT(`${A}/settings`, formData(e.target)); toast('Settings saved', 'ok'); }); };
    let wins = (s.schedule.windows || []).map((w) => ({ ...w }));
    const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const drawWins = () => {
      $('#schRows').innerHTML = wins.length ? wins.map((w, i) => `<div class="row wrap schrow">${DAYS.map((d, k) => `<label class="daychip ${w.days.includes(k) ? 'on' : ''}"><input type="checkbox" data-w="${i}" data-d="${k}" ${w.days.includes(k) ? 'checked' : ''} hidden>${d}</label>`).join('')}
        <input class="input" type="time" data-ws="${i}" value="${w.start}" style="width:120px"><span class="muted">to</span><input class="input" type="time" data-we="${i}" value="${w.end}" style="width:120px"><button class="btn xs ghost" type="button" data-wr="${i}">${ico('x', 12)}</button></div>`).join('')
        : '<div class="small muted">No windows yet: add one, e.g. Mon–Fri 09:00 to 17:00.</div>';
      $$('[data-w]').forEach((x) => x.onchange = () => { const w = wins[x.dataset.w], d = Number(x.dataset.d); w.days = x.checked ? [...w.days, d] : w.days.filter((y) => y !== d); drawWins(); });
      $$('[data-ws]').forEach((x) => x.onchange = () => { wins[x.dataset.ws].start = x.value; });
      $$('[data-we]').forEach((x) => x.onchange = () => { wins[x.dataset.we].end = x.value; });
      $$('[data-wr]').forEach((x) => x.onclick = () => { wins.splice(Number(x.dataset.wr), 1); drawWins(); });
    };
    drawWins();
    $('#schAdd').onclick = () => { wins.push({ days: [0, 1, 2, 3, 4], start: '09:00', end: '17:00' }); drawWins(); };
    $('#schSave').onclick = () => busy($('#schSave'), async () => {
      if ($('#schOn').checked && !wins.length) throw new Error('Add at least one time window, or switch opening hours off.');
      await PUT(`${A}/settings`, { schedule: { enabled: $('#schOn').checked, windows: wins, message: $('#schMsg').value } });
      toast('Opening hours saved (applies within 15 seconds)', 'ok');
    });
    let deps = [...(s.departments || [])];
    const saveDeps = async () => { await PUT(`${A}/settings`, { departments: deps }); META = await GET(`${A}/questions/meta`); drawDeps(); };
    const drawDeps = () => {
      $('#depList').innerHTML = deps.length ? deps.map((d, i) => `<span class="chipx">${esc(d)}<button type="button" data-rd="${i}" aria-label="Remove ${esc(d)}">${ico('x', 12)}</button></span>`).join('') : '<span class="small muted">No departments yet.</span>';
      $$('[data-rd]').forEach((b) => b.onclick = async () => { const d = deps[b.dataset.rd]; if (await confirmBox(`Remove department ${d}? Existing students keep it.`, { ok: 'Remove', danger: true })) { deps.splice(Number(b.dataset.rd), 1); await saveDeps(); toast(`${d} removed`, 'ok'); } });
    };
    const addDep = async () => { const v = $('#depIn').value.trim().toUpperCase(); if (!v) return; if (deps.includes(v)) return toast('Already in the list', 'info'); deps.push(v); $('#depIn').value = ''; await saveDeps(); toast(`${v} added`, 'ok'); $('#depIn').focus(); };
    $('#depAdd').onclick = () => addDep().catch(fail);
    $('#depIn').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); addDep().catch(fail); } };
    drawDeps();
    $('#gf').onsubmit = (e) => { e.preventDefault(); busy($('button', e.target), async () => { await POST(`${A}/announcements`, formData(e.target)); load(); }); };
    $$('[data-da]', v).forEach((b) => b.onclick = async () => { await DEL(`${A}/announcements/${b.dataset.da}`).catch(fail); load(); });
    $('#dbCheck').onclick = () => busy($('#dbCheck'), async () => { const r = await POST(`${A}/system/database/check`); toast(r.ok ? 'Database integrity: OK' : 'Database integrity problem found', r.ok ? 'ok' : 'err'); load(); });
    $('#bk').onclick = () => busy($('#bk'), async () => { const r = await POST(`${A}/system/backup`); toast('Backup written: ' + r.file, 'ok'); load(); });
  };
  await load();
}

boot();
