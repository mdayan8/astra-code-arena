// ASTRA Code Arena: student portal. No admin routes or links exist in this file by design.
const app = $('#app');
let cleanup = [];               // timers/listeners of the current page
const onLeave = (fn) => cleanup.push(fn);
function leave() { cleanup.forEach((f) => { try { f(); } catch (e) { /* ignore */ } }); cleanup = []; }
S.onUnauthorized = () => { S.user = null; if (!/^#\/(login|register)?$/.test(location.hash) && location.hash !== '' && location.hash !== '#/') nav('/login?next=' + encodeURIComponent(location.hash.slice(1))); };

async function boot() {
  try {
    const [info, me] = await Promise.all([GET('/api/public/info'), GET('/api/me')]);
    S.info = info; S.tz = info.timezone; S.user = me.user; if (me.user) S.csrf = me.user.csrf;
  } catch (e) { app.innerHTML = `<div class="container page">${empty('Cannot reach ASTRA Code Arena', esc(e.message))}</div>`; return; }
  window.addEventListener('hashchange', () => { leave(); window.scrollTo(0, 0); });  // registered before the router so old pages clean up first
  const go = router([
    ['/', landing], ['/login', loginPage], ['/register', registerPage], ['/dashboard', authed(() => nav('/profile'))], ['/home', authed(profilePage)], ['/competitions', competitionsPage],
    ['/c/:code', compDetail], ['/arena/:id', authed((p) => workspace({ mode: 'contest', compId: Number(p.id), q: p.query.q }))],
    ['/practice', authed(practicePage)], ['/practice/:qid', authed((p) => workspace({ mode: 'practice', qid: Number(p.qid) }))],
    ['/leaderboard/:id', leaderboardPage], ['/semester', semesterPage], ['/semester/:id', semesterPage], ['/profile', authed(profilePage)], ['/rules', rulesPage],
  ], () => { shell(''); app.innerHTML = `<div class="container page">${empty('Page not found', '<a href="#/">Go home</a>')}</div>`; });
  go();
}
const authed = (fn) => async (p) => { if (!S.user) return nav('/login?next=' + encodeURIComponent(location.hash.slice(1))); return fn(p); };

const theme = () => { try { return localStorage.getItem('astra:theme') || 'light'; } catch (e) { return 'light'; } };
function shell(active, forceTheme) {
  document.body.dataset.theme = forceTheme || theme();
  document.body.classList.toggle('landing-body', active === 'home');
  const t = $('#topnav'); t.classList.remove('hidden');
  const side = !!S.user && active !== 'home';
  document.body.classList.toggle('with-side', side);
  const themeBtn = `<button class="btn ghost sm icon-btn" id="themeToggle" title="Switch to ${theme() === 'dark' ? 'light' : 'dark'} mode" aria-label="Switch theme">${ico(theme() === 'dark' ? 'sun' : 'moon', 18)}</button>`;
  if (side) {
    const items = [['profile', 'Home', 'user'], ['competitions', 'Competitions', 'trophy'], ['practice', 'Practice', 'code'], ['semester', 'Leaderboards', 'chart'], ['rules', 'Rules', 'book']];
    t.className = 'sidewrap';
    t.innerHTML = `<div class="mbar"><button class="btn ghost sm icon-btn" id="sideOpen" aria-label="Open menu">${ico('menu', 20)}</button><a class="brand" href="#/profile"><img src="${LOGO}" alt=""><span>Code Arena</span></a><div class="grow"></div>${themeBtn}</div>
      <aside class="sside" id="sside"><a class="brand" href="#/profile"><img src="${LOGO}" alt=""><span>Code Arena<small>PESITM Shivamogga</small></span></a>
      <nav class="snav">${items.map(([h, l, i]) => `<a href="#/${h}" class="${active === h ? 'on' : ''}">${ico(i, 18)}<span>${l}</span></a>`).join('')}</nav>
      <div class="grow"></div>
      <div class="sfoot"><div class="row" style="gap:10px">${avatar(S.user.name, '', S.user.avatar)}<div class="grow" style="min-width:0"><b class="small ellip">${esc(S.user.name)}</b><div class="tiny muted mono">${esc(S.user.participant_id || '')}</div></div></div>
        <div class="row" style="gap:6px;margin-top:10px"><button class="btn ghost sm grow" id="themeSide">${ico(theme() === 'dark' ? 'sun' : 'moon', 16)} ${theme() === 'dark' ? 'Light' : 'Dark'} mode</button><button class="btn ghost sm icon-btn" id="logoutBtn" title="Sign out">${ico('logout', 16)}</button></div></div></aside>
      <div class="sscrim" id="sscrim"></div>`;
    const close = () => { $('#sside').classList.remove('open'); $('#sscrim').classList.remove('open'); };
    $('#sideOpen').onclick = () => { $('#sside').classList.add('open'); $('#sscrim').classList.add('open'); };
    $('#sscrim').onclick = close;
    $$('.snav a').forEach((a) => a.addEventListener('click', close));
    $('#logoutBtn').onclick = async () => { await POST('/api/auth/logout').catch(() => {}); S.user = null; S.csrf = ''; nav('/'); };
  } else {
    const links = [['competitions', 'Competitions'], ['semester', 'Leaderboards'], ['rules', 'Rules']];
    t.className = 'topnav';
    t.innerHTML = `<div class="container"><a class="brand" href="#/"><img src="${LOGO}" alt=""><span>Code Arena<small>PESITM Shivamogga</small></span></a>
      <nav class="navlinks" id="navlinks">${links.map(([h, l]) => `<a href="#/${h}" class="${active === h ? 'on' : ''}">${l}</a>`).join('')}</nav><div class="grow"></div>${themeBtn}
      <button class="btn ghost sm menu-btn icon-btn" id="menuBtn" aria-label="Menu">${ico('menu', 20)}</button>
      ${S.user ? `<a class="btn primary" href="#/profile">My profile ${ico('right', 14)}</a>` : `<a class="btn ghost" href="#/login">Sign in</a><a class="btn primary" href="#/register">Register</a>`}</div>`;
    $('#menuBtn').onclick = () => $('#navlinks').classList.toggle('open');
  }
  const flip = () => { try { localStorage.setItem('astra:theme', theme() === 'dark' ? 'light' : 'dark'); } catch (e) { /* private mode */ } window.dispatchEvent(new HashChangeEvent('hashchange')); };
  $$('#themeToggle, #themeSide').forEach((b) => b.onclick = flip);
}
const footer = () => `<footer class="strip"><div class="container row between wrap"><span>Created with <span class="heart">${ico('heart')}</span> by <a href="https://www.astraclub.space/" target="_blank" rel="noopener">ASTRA Club</a></span><span class="muted">PES Institute of Technology and Management, Shivamogga</span></div></footer>`;

// ---------------- competition cards ----------------
function compActions(c) {
  const ds = c.display_status;
  if ((ds === 'Live' || ds === 'Paused') && c.joined) return `<a class="btn primary sm" href="#/arena/${c.id}">Enter arena ${ico('right')}</a>`;
  if (c.can_join) return `<button class="btn primary sm" data-join="${c.id}">Join competition</button>`;
  if (ds === 'Completed' || ds === 'Archived') return `<a class="btn sm" href="#/leaderboard/${c.id}">Results</a>`;
  if (ds === 'Live' && c.leaderboard_visibility === 'live') return `<a class="btn sm" href="#/leaderboard/${c.id}">Live standings</a>`;
  return '';
}
function compCard(c) {
  return `<article class="card comp-card">
    <div class="row between"><div class="row small muted"><span class="mono">${esc(c.code)}</span>${c.round_no ? `<span>· Round ${String(c.round_no).padStart(2, '0')}</span>` : ''}</div>${badge(c.display_status)}</div>
    <h3><a href="#/c/${encodeURIComponent(c.code)}" style="color:inherit">${esc(c.title)}</a></h3>
    ${c.description ? `<div class="desc">${esc(c.description)}</div>` : ''}
    <div class="meta"><span>${ico('calendar')} <b>${c.start_at ? fmtTime(c.start_at) : 'Date TBA'}</b></span><span>${ico('clock')} <b>${fmtMins(c.duration_min + (c.extra_min || 0))}</b></span>
      <span>{ } <b>${c.languages.map(langName).join(', ')}</b></span>${c.question_count ? `<span>${ico('file')} <b>${c.question_count}</b> problems</span>` : ''}</div>
    <div class="row between" style="margin-top:4px"><span class="small muted">${c.joined ? '' + ico('check') + ' You are registered' : c.registration_mode === 'invite_only' ? 'Invitation only' : c.reg_end ? 'Registration till ' + fmtTime(c.reg_end) : ''}</span>
      <div class="row">${c.display_status === 'Live' && c.remaining_sec != null ? `<span class="small muted mono">${fmtDur(c.remaining_sec)} left</span>` : ''}${S.user || !c.can_join ? compActions(c) : ''}
      ${!S.user && ['Registration Open', 'Live'].includes(c.display_status) ? `<a class="btn primary sm" href="#/login?next=/c/${encodeURIComponent(c.code)}">Sign in to join</a>` : ''}</div></div>
  </article>`;
}
function bindJoin(root, after) {
  $$('[data-join]', root).forEach((b) => b.onclick = () => busy(b, async () => {
    await POST(`/api/student/competitions/${b.dataset.join}/join`); toast('You are registered. Good luck!', 'ok'); after();
  }));
}

// ---------------- landing (one screen, live data) ----------------
function podium(rows, unit = 'pts') {
  const order = [rows[1], rows[0], rows[2]].filter(Boolean);
  const medal = { 1: 'gold', 2: 'silver', 3: 'bronze' };
  return `<div class="podium2">${order.map((r) => { const m = medal[Math.min(r.rank, 3)]; return `<div class="pcard2 ${m} ${r.rank === 1 ? 'first' : ''}">
    <div class="pmedal ${m}">${r.rank === 1 ? ico('crown', 16) : ''}<span>${r.rank}</span></div>
    ${avatar(r.name, 'lg ring-' + m, r.avatar)}<div class="pname" title="${esc(r.name)}">${esc(r.name)}</div>
    <div class="pscore">${r.points ?? r.score}<small> ${unit}</small></div>${r.solved != null ? `<div class="tiny muted">${r.solved} solved</div>` : ''}</div>`; }).join('')}</div>`;
}
async function landing() {
  shell('home');
  app.innerHTML = `<section class="landing"><div class="landing-bg" aria-hidden="true"><i class="blob b1"></i><i class="blob b2"></i><i class="blob b3"></i></div>
    <div class="container landing-grid">
      <div class="landing-copy">
        <div class="college"><img src="/static/img/pesitm-emblem.png" alt="PESITM emblem"><div><b>PES Institute of Technology &amp; Management</b><span>Shivamogga · ASTRA Club</span></div></div>
        <h1>Code. Compete.<br><span class="grad">Climb the board.</span></h1>
        <p class="lead">The campus coding arena. Solve problems in C, Python and more right in your browser, get judged in seconds, and rise through every round of the semester.</p>
        <div class="row wrap cta">${S.user ? `<a class="btn primary lg" href="#/profile">Open dashboard ${ico('right')}</a><a class="btn lg" href="#/practice">${ico('code')} Practice</a>`
          : `<a class="btn primary lg" href="#/login">${ico('user')} Sign in with USN</a><a class="btn lg" href="#/register">Create account ${ico('right')}</a>`}</div>
        <div class="lstats" id="lstats">${[['users', 'Coders'], ['book', 'Problems'], ['trophy', 'Rounds'], ['zap', 'Submissions']].map(([i, k]) => `<div class="ls"><span class="lsi">${ico(i, 18)}</span><div><b>—</b><span>${k}</span></div></div>`).join('')}</div>
      </div>
      <div class="hero-panel" id="stage"><div class="round-card"><div class="skel" style="height:220px"></div></div></div>
    </div></section>${footer()}`;
  const d = await GET('/api/public/landing');
  const st = d.stats;
  $('#lstats').innerHTML = [['users', st.students, 'Coders'], ['book', st.problems, 'Problems'], ['trophy', st.rounds, 'Rounds'], ['zap', st.submissions, 'Submissions']]
    .map(([i, n, k]) => `<div class="ls"><span class="lsi">${ico(i, 18)}</span><div><b>${Number(n).toLocaleString('en-IN')}</b><span>${k}</span></div></div>`).join('');
  const f = d.feature;
  const live = f && f.remaining_sec != null;
  const total = f ? f.duration_min * 60 : 1;
  const cta = !f ? `<a class="btn primary block" href="#/${S.user ? 'practice' : 'register'}">${S.user ? 'Warm up in Practice' : 'Create your account'} ${ico('right', 14)}</a>`
    : live ? `<a class="btn primary block" href="#/${S.user ? 'arena/' + f.id : 'login?next=/arena/' + f.id}">${ico('play', 13)} ${S.user ? 'Enter the arena' : 'Sign in to compete'}</a>`
      : `<a class="btn primary block" href="#/c/${encodeURIComponent(f.code)}">View round &amp; register ${ico('right', 14)}</a>`;
  const feats = [['code', 'Browser IDE', 'C, C++, Python, Java. Nothing to install.'], ['zap', 'Verdicts in seconds', 'Hidden tests in a sandboxed judge.'],
    ['shield', 'Fair play', 'Full-screen arena with activity checks.'], ['trophy', 'Semester points', 'Every round counts towards your rank.']];
  $('#stage').innerHTML = `<div class="round-card">
      ${f ? `<div class="row between"><span class="row" style="gap:8px">${badge(f.display_status)}<span class="tiny mono muted">${esc(f.code)}</span></span><span class="tiny muted">${live ? 'Happening now' : 'Next round'}</span></div>
        <h3 class="rtitle">${esc(f.title)}</h3>
        <div class="rcount"><div><span class="tiny muted">${live ? 'Ends in' : 'Starts'}</span><b class="mono" id="fcount" ${live ? `data-end="${Math.floor(Date.now() / 1000) + f.remaining_sec}"` : ''}>${live ? fmtDur(f.remaining_sec) : fmtTime(f.start_at)}</b></div>
          ${live ? `<div class="grow"><div class="bar"><i id="fprog" style="width:${Math.min(100, Math.max(0, 100 - f.remaining_sec / total * 100))}%"></i></div></div>` : ''}</div>
        <div class="rmeta"><span>${ico('clock', 14)} ${fmtMins(f.duration_min)}</span><span>${ico('book', 14)} ${f.questions} problem${f.questions === 1 ? '' : 's'}</span><span>${ico('users', 14)} ${f.participants} registered</span><span>${ico('code', 14)} ${f.languages.map(langName).join(', ')}</span></div>
        ${cta}${d.more ? `<a class="tiny muted center" style="display:block;margin-top:8px" href="#/competitions">+${d.more} more round${d.more > 1 ? 's' : ''} scheduled</a>` : ''}`
      : `<div class="center" style="padding:18px 6px"><div class="emptyi">${ico('calendar', 26)}</div><h3 class="rtitle">No round scheduled yet</h3><p class="small muted">The next ASTRA round will appear here as soon as organisers publish it.</p>${cta}</div>`}
    </div>
    <div class="feat-grid">${feats.map(([i, t, x]) => `<div class="feat"><span class="lsi">${ico(i, 18)}</span><div><b>${t}</b><span>${x}</span></div></div>`).join('')}</div>`;
  const fc = $('#fcount');
  if (fc && fc.dataset.end) { const t = setInterval(() => { if (!fc.isConnected) return clearInterval(t); const left = Number(fc.dataset.end) - Date.now() / 1000; fc.textContent = fmtDur(left); const pg = $('#fprog'); if (pg) pg.style.width = Math.min(100, Math.max(0, 100 - left / total * 100)) + '%'; }, 1000); onLeave(() => clearInterval(t)); }
}

// ---------------- auth ----------------
function pwField(name = 'password', ph = 'Password', auto = 'current-password') {
  return `<div class="pw-wrap"><input class="input" type="password" name="${name}" placeholder="${ph}" autocomplete="${auto}" required>
    <button type="button" class="btn ghost xs" onclick="const i=this.previousElementSibling;i.type=i.type==='password'?'text':'password';this.textContent=i.type==='password'?'Show':'Hide'">Show</button></div>`;
}
function forgotModal() {
  modal({ title: 'Forgot password', body: `<form class="stack" id="ff"><p class="small muted" style="margin:0">Enter your USN, your full name exactly as registered and your date of birth. If they match, your password is reset to your date of birth (DDMMYYYY).</p>
    <label class="field">USN<input class="input" name="usn" required style="text-transform:uppercase"></label><label class="field">Full name<input class="input" name="name" required></label>
    <label class="field">Date of birth<input class="input" type="date" name="dob" required></label><div id="ferr"></div></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Reset password', kind: 'primary', onClick: async (m) => {
      try { const r = await POST('/api/auth/forgot', formData($('#ff', m))); toast(r.message, 'ok'); }
      catch (e) { $('#ferr', m).innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return false; }
    } }] });
}
function loginPage(p) {
  shell('');
  if (S.user) return nav(p.query.next || '/profile');
  app.innerHTML = `<div class="auth"><form class="card stack" id="f"><div><img class="logo" src="${LOGO}" alt=""><h2>Sign in to Code Arena</h2><p class="muted small">Use your USN (or participant ID).</p></div>
    <label class="field">USN<input class="input" name="username" autocomplete="username" required autofocus placeholder="4PM23CS001" style="text-transform:uppercase"></label>
    <label class="field"><span class="row between">Password <a href="#" id="forgot" class="small" style="font-weight:500">Forgot password?</a></span>${pwField('password', 'Date of birth, e.g. 15082005')}
      <span class="hint">Your password is your date of birth as DDMMYYYY, unless you changed it.</span></label><div id="err"></div>
    <button class="btn primary lg block" type="submit">Sign in</button>
    ${S.info.registration_open ? '<p class="small muted center" style="margin:0">New here? <a href="#/register">Create an account</a></p>' : ''}</form></div>`;
  $('#forgot').onclick = (e) => { e.preventDefault(); forgotModal(); };
  $('#f').onsubmit = (e) => {
    e.preventDefault();
    busy($('button[type=submit]', e.target), async () => {
      try {
        const u = await POST('/api/auth/login', formData(e.target));
        S.user = u; S.csrf = u.csrf;
        if (u.must_change_pw) { toast('Please set a new password now.', 'info'); return nav('/profile?pw=1'); }
        nav(p.query.next || '/profile');
      } catch (er) { $('#err').innerHTML = `<div class="notice err">${esc(er.message)}</div>`; }
    });
  };
}
function registerPage() {
  shell('');
  if (S.user) return nav('/profile');
  if (!S.info.registration_open) { app.innerHTML = `<div class="auth"><div class="card">${empty('Registration is closed', 'Organisers create participant accounts for this event. Contact ASTRA Club.')}</div></div>`; return; }
  const deps = S.info.departments || [];
  app.innerHTML = `<div class="auth"><form class="card stack" id="f" style="width:min(540px,100%)"><div><img class="logo" src="${LOGO}" alt=""><h2>Create your account</h2><p class="muted small">One account for every round, practice and the semester leaderboard.</p></div>
    <label class="field">Full name <span class="hint">as on your college ID; needed to recover your password</span><input class="input" name="name" required minlength="2" autocomplete="name"></label>
    <div class="grid g2"><label class="field">USN<input class="input" name="usn" required placeholder="4PM23CS001" style="text-transform:uppercase"></label>
      <label class="field">Date of birth<input class="input" type="date" name="dob" required max="${new Date().toISOString().slice(0, 10)}"></label></div>
    <div class="grid g3"><label class="field">Department${deps.length ? `<select class="select" name="department" required><option value="">Choose…</option>${deps.map((d) => `<option>${esc(d)}</option>`).join('')}</select>` : '<input class="input" name="department" required>'}</label>
      <label class="field">Year<select class="select" name="year" data-num="1"><option value="1">1st</option><option value="2">2nd</option><option value="3">3rd</option><option value="4">4th</option></select></label>
      <label class="field">Semester<select class="select" name="semester" data-num="1">${[1, 2, 3, 4, 5, 6, 7, 8].map((n) => `<option value="${n}">Sem ${n}</option>`).join('')}</select></label></div>
    <div class="grid g2"><label class="field">Section<input class="input" name="section" placeholder="A"></label><label class="field">Email <span class="hint">college or personal</span><input class="input" type="email" name="email"></label></div>
    <div class="notice info small">Your password will be your <b>date of birth as DDMMYYYY</b> (e.g. 15 Aug 2005 ${ico('right')} <span class="mono">15082005</span>). You can change it later in your profile.</div>
    <div id="err"></div><button class="btn primary lg block" type="submit">Create account</button><p class="small muted center" style="margin:0">Already registered? <a href="#/login">Sign in</a></p></form></div>`;
  $('[name=year]').onchange = (e) => { $('[name=semester]').value = String(Number(e.target.value) * 2 - 1); };
  $('#f').onsubmit = (e) => {
    e.preventDefault();
    busy($('button[type=submit]', e.target), async () => {
      try { const u = await POST('/api/auth/register', formData(e.target)); S.user = u; S.csrf = u.csrf; toast(`Welcome! Your participant ID is ${u.participant_id}`, 'ok'); nav('/profile'); }
      catch (er) { $('#err').innerHTML = `<div class="notice err">${esc(er.message)}</div>`; }
    });
  };
}

// ---------------- dashboard ----------------
async function dashboard() {
  shell('dashboard');
  app.innerHTML = `<div class="container page">${skeleton(5)}</div>`;
  const [d, gx] = await Promise.all([GET('/api/student/dashboard'), GET('/api/student/stats')]);
  const st = d.stats;
  app.innerHTML = `<div class="container page stack loose">
    <div class="row between wrap"><div><h1 style="margin:0">Welcome back, ${esc(d.me.name.split(' ')[0])}</h1><div class="muted">Participant ID <b class="mono">${esc(d.me.participant_id)}</b> · ${esc(d.me.department)} · Year ${esc(d.me.year)}</div></div>
      <div class="row"><a class="btn" href="#/practice">Practice</a><a class="btn primary" href="#/competitions">Browse competitions</a></div></div>
    <div class="grid g4"><div class="card stat"><div class="k">Semester points</div><div class="v">${d.semester ? d.semester.points : 0}</div><div class="s">${d.semester ? esc(d.semester.name) : 'No active semester'}</div></div>
      <div class="card stat"><div class="k">Semester rank</div><div class="v">${d.semester?.rank ? '#' + d.semester.rank : '—'}</div><div class="s">${d.semester?.rank ? 'of ' + d.semester.of : 'Play a round to get ranked'}</div></div>
      <div class="card stat"><div class="k">Competitions</div><div class="v">${d.competitions_joined}</div><div class="s">joined</div></div>
      <div class="card stat"><div class="k">Problems solved</div><div class="v">${d.solved}</div><div class="s">${st.accepted}/${st.submissions} submissions accepted</div></div></div>
    <a class="card pad row wrap" href="#/profile" style="color:inherit;text-decoration:none;gap:18px"><span>${ico('flame')} <b>${gx.streak}</b> day streak</span><span class="muted small">longest ${gx.longest_streak}</span>
      <div class="row grow" style="gap:6px;justify-content:flex-end">${gx.badges.filter((b) => b.earned).slice(0, 6).map((b) => hexBadge(b, true)).join('') || '<span class="small muted">Earn your first badge by solving a problem ' + ico('right') + '</span>'}</div></a>
    ${d.active.length ? `<section><h2>Live now</h2><div class="grid g2">${d.active.map(compCard).join('')}</div></section>` : ''}
    <section class="card pad" id="dIns"><div class="skel" style="height:80px"></div></section>
    <section><h2>Upcoming</h2>${d.upcoming.length ? `<div class="grid g2">${d.upcoming.map(compCard).join('')}</div>` : `<div class="card">${empty('Nothing scheduled yet', 'New rounds appear here as soon as organisers publish them.')}</div>`}</section>
    <div class="grid g2"><section class="card"><div class="card-head"><h3>Recent submissions</h3></div>${d.recent.length ? `<div class="table-wrap"><table class="table"><tbody>
      ${d.recent.map((r) => `<tr><td><b>${esc(r.title)}</b><div class="tiny muted">${esc(r.competition || 'Practice')} · ${langName(r.language)}</div></td><td>${vbadge(r.verdict, r.status)}</td><td class="right tiny muted nowrap">${ago(r.created_at)}</td></tr>`).join('')}
      </tbody></table></div>` : empty('No submissions yet')}</section>
      <section class="card"><div class="card-head"><h3>Performance</h3><a class="small" href="#/profile">History ${ico('right')}</a></div><div class="card-body stack">
        <div><div class="row between small"><span>Acceptance rate</span><b>${st.submissions ? Math.round(st.accepted / st.submissions * 100) : 0}%</b></div><div class="bar"><i style="width:${st.submissions ? st.accepted / st.submissions * 100 : 0}%"></i></div></div>
        ${st.by_language.length ? `<div class="small muted">Languages used</div><div class="row wrap">${st.by_language.map((l) => `<span class="chip">${langName(l.language)} · ${l.n}</span>`).join('')}</div>` : ''}
        ${d.previous.length ? `<div class="small muted">Previous competitions</div>${d.previous.slice(0, 4).map((c) => `<a class="row between" href="#/leaderboard/${c.id}" style="color:inherit"><span>${esc(c.title)}</span><span class="small">Results ${ico('right')}</span></a>`).join('')}` : ''}
      </div></section></div></div>${footer()}`;
  bindJoin(app, dashboard);
  GET('/api/student/insights').then((ins) => { if ($('#dIns')) $('#dIns').innerHTML = insightsHTML(ins); }).catch(() => $('#dIns')?.remove());
}

// ---------------- competitions ----------------
async function competitionsPage(p) {
  shell('competitions');
  const f = p.query.f || 'all';
  app.innerHTML = `<div class="container page"><div class="row between wrap" style="margin-bottom:16px"><div><h1 style="margin:0">Competitions</h1><div class="muted">Every ASTRA round, past and upcoming.</div></div>
    <div class="seg" id="seg">${[['all', 'All'], ['live', 'Live'], ['upcoming', 'Upcoming'], ['done', 'Completed']].map(([k, l]) => `<button data-f="${k}" class="${f === k ? 'on' : ''}">${l}</button>`).join('')}</div></div><div id="list">${skeleton()}</div></div>${footer()}`;
  $$('#seg button').forEach((b) => b.onclick = () => nav('/competitions?f=' + b.dataset.f));
  const all = await GET('/api/public/competitions');
  const pick = { all: () => true, live: (c) => ['Live', 'Paused'].includes(c.display_status), upcoming: (c) => ['Upcoming', 'Registration Open', 'Registration Closed'].includes(c.display_status), done: (c) => c.display_status === 'Completed' }[f] || (() => true);
  const list = all.filter(pick);
  $('#list').innerHTML = list.length ? `<div class="grid g2">${list.map(compCard).join('')}</div>` : `<div class="card">${empty('No competitions here yet')}</div>`;
  bindJoin($('#list'), () => competitionsPage(p));
}

async function compDetail(p) {
  shell('competitions');
  app.innerHTML = `<div class="container page">${skeleton(6)}</div>`;
  const c = await GET(`/api/public/competitions/${encodeURIComponent(p.code)}`);
  const sc = c.scoring;
  const scoringText = [sc.mode === 'partial' ? 'Partial marks: each problem awards marks in proportion to the hidden test cases you pass.' : 'All-or-nothing: a problem awards full marks only when every test case passes.',
    'Only your best submission per problem counts; resubmitting an accepted problem never adds extra points.',
    sc.negative_marks ? `Negative marking: −${sc.negative_marks} marks per wrong submission on a problem (never below 0 for that problem).` : 'No negative marking.',
    sc.first_blood_bonus ? `First correct solver of each problem earns a +${sc.first_blood_bonus} bonus.` : '',
    sc.tie_break === 'time' ? 'Ties are broken by who reached their score earliest.' : `Ties are broken by penalty time: minutes from the start to each scoring submission + ${sc.penalty_minutes} minutes per wrong attempt before it. Lower is better.`].filter(Boolean);
  app.innerHTML = `<div class="container page"><a href="#/competitions" class="small">${ico('left')} All competitions</a>
    <div class="row between wrap" style="margin:10px 0 18px"><div><div class="row small muted"><span class="mono">${esc(c.code)}</span>${c.round_no ? `· Round ${String(c.round_no).padStart(2, '0')}` : ''}${c.academic_year ? ` · ${esc(c.academic_year)}` : ''}${c.semester_label ? ` · ${esc(c.semester_label)}` : ''}</div>
      <h1 style="margin:4px 0">${esc(c.title)}</h1><div class="row">${badge(c.display_status)}<span class="small muted">${c.participant_count} registered</span></div></div>
      <div class="row" id="cta"></div></div>
    <div class="grid" style="grid-template-columns:minmax(0,1fr) 340px;align-items:start" id="cgrid">
      <div class="stack loose">${c.description ? `<section class="card pad prose">${md(c.description)}</section>` : ''}
        ${c.instructions ? `<section class="card pad"><h3>Rules &amp; instructions</h3><div class="prose">${md(c.instructions)}</div></section>` : ''}
        <section class="card pad"><h3>Scoring</h3><ul class="prose" style="margin:0">${scoringText.map((s) => `<li>${esc(s)}</li>`).join('')}</ul></section>
        ${c.announcements.length ? `<section class="card pad"><h3>Announcements</h3>${c.announcements.map((a) => `<div style="padding:6px 0;border-bottom:1px solid var(--border)"><b>${esc(a.title)}</b> <span class="tiny muted">${fmtTime(a.created_at)}</span>${a.body ? `<div class="small muted">${esc(a.body)}</div>` : ''}</div>`).join('')}</section>` : ''}</div>
      <aside class="card pad"><dl class="kv" style="grid-template-columns:110px 1fr">
        <dt>Starts</dt><dd>${fmtTime(c.start_at)}</dd><dt>Duration</dt><dd>${fmtMins(c.duration_min)}${c.extra_min ? ` + ${c.extra_min} min extension` : ''}</dd>
        ${c.end_at ? `<dt>Ends</dt><dd>${fmtTime(c.end_at)}</dd>` : ''}<dt>Problems</dt><dd>${c.question_count}</dd><dt>Max marks</dt><dd>${c.max_marks}</dd>
        <dt>Languages</dt><dd>${c.languages.map(langName).join(', ')}</dd>
        <dt>Registration</dt><dd>${c.registration_mode === 'invite_only' ? 'Organiser invitation' : `${c.reg_start ? fmtTime(c.reg_start) : 'Now'} ${ico('right')} ${c.reg_end ? fmtTime(c.reg_end) : 'start'}`}${c.allow_late_join ? '<div class="tiny muted">Late joining allowed</div>' : ''}</dd>
        ${c.eligible_years.length ? `<dt>Years</dt><dd>${c.eligible_years.join(', ')}</dd>` : ''}${c.eligible_departments.length ? `<dt>Departments</dt><dd>${c.eligible_departments.map(esc).join(', ')}</dd>` : ''}
        <dt>Leaderboard</dt><dd>${{ live: 'Live during the contest', after_end: 'Revealed after the end', hidden: 'Published by organisers' }[c.leaderboard_visibility]}</dd></dl>
        ${c.paste_policy !== 'allow' ? `<div class="notice small" style="margin-top:14px">Fair play: activity in the coding workspace (pasting, switching tabs) is recorded.${c.paste_policy === 'block_external' ? ' Pasting code from outside the editor is disabled.' : ''}</div>` : ''}
        ${(() => { const l = c.lockdown || {}; const r = [l.fullscreen !== 'off' && 'The test runs in full screen and is hidden outside it.', l.leave !== 'allow' && 'Switching to another tab or app hides the test and gives a warning.',
          l.max_violations && `After ${l.max_violations} warnings your test is submitted automatically.`, l.internet === 'block' && 'Internet is not allowed: the test is hidden while your computer can reach the internet.',
          l.internet === 'allow' && 'Internet use is allowed in this round.', l.no_copy && 'Copying is disabled.', l.no_paste && 'Pasting is disabled.'].filter(Boolean);
          return r.length ? `<div class="notice warn small" style="margin-top:10px"><b>Exam rules</b><ul style="margin:6px 0 0;padding-left:18px">${r.map((x) => `<li>${x}</li>`).join('')}</ul></div>` : ''; })()}</aside></div></div>${footer()}`;
  if (innerWidth < 900) $('#cgrid').style.gridTemplateColumns = '1fr';
  const cta = $('#cta');
  if (!S.user) cta.innerHTML = ['Registration Open', 'Live'].includes(c.display_status) ? `<a class="btn primary lg" href="#/login?next=/c/${encodeURIComponent(c.code)}">Sign in to join</a>` : '';
  else if (c.joined && ['Live', 'Paused'].includes(c.display_status)) cta.innerHTML = `<a class="btn primary lg" href="#/arena/${c.id}">Enter arena ${ico('right')}</a>`;
  else if (c.joined) cta.innerHTML = `<span class="badge green">${ico('check')} Registered</span>${c.display_status === 'Completed' ? `<a class="btn" href="#/leaderboard/${c.id}">Results</a>` : ''}`;
  else if (c.can_join) { cta.innerHTML = `<button class="btn primary lg" data-join="${c.id}">Join competition</button>`; bindJoin(cta, () => compDetail(p)); }
  else cta.innerHTML = `<span class="small muted">${esc(c.join_reason || '')}</span>${c.display_status === 'Completed' ? `<a class="btn" href="#/leaderboard/${c.id}">Results</a>` : ''}`;
  if (c.display_status === 'Live' && c.leaderboard_visibility === 'live') cta.insertAdjacentHTML('beforeend', `<a class="btn" href="#/leaderboard/${c.id}">Live standings</a>`);
}

// ---------------- leaderboards ----------------
async function leaderboardPage(p) {
  shell('semester');
  const load = async () => {
    const d = await GET(`/api/competitions/${p.id}/leaderboard`);
    if (d.hidden) { app.innerHTML = `<div class="container page"><h1>${esc(d.competition.title)}</h1><div class="card">${empty('Leaderboard not available yet', esc(d.reason))}</div></div>`; return false; }
    const c = d.competition;
    app.innerHTML = `<div class="container page"><a class="small" href="#/c/${encodeURIComponent(c.code)}">${ico('left')} ${esc(c.title)}</a>
      <div class="row between wrap" style="margin:8px 0 16px"><div><h1 style="margin:0">${d.frozen ? 'Final results' : 'Leaderboard'}</h1><div class="row small muted">${badge(c.display_status)}${c.remaining_sec != null ? `<span class="mono">${fmtDur(c.remaining_sec)} remaining</span><span>Updates automatically</span>` : ''}</div></div></div>
      ${d.rows.length ? `<div class="card pad podium-card">${podium(d.rows.slice(0, 3).map((r) => ({ ...r, points: r.score })), 'marks')}</div>` : ''}
      <div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>Rank</th><th>Participant</th><th class="num">Score</th><th class="num">Solved</th>${d.tie_break === 'penalty' ? '<th class="num" title="Tie-break penalty minutes">Penalty</th>' : ''}
        ${d.questions.map((q) => `<th class="num" title="${esc(q.title)}">${q.label}<div class="tiny faint">${q.marks}</div></th>`).join('')}</tr></thead><tbody>
        ${d.rows.length ? d.rows.map((r) => `<tr class="${r.me ? 'me' : ''}"><td>${rankBadge(r.rank)}</td><td><div class="row">${avatar(r.name, 'sm', r.avatar)}<div><b>${esc(r.name)}</b>${r.me ? ' <span class="badge violet">You</span>' : ''}<div class="tiny muted">${esc(r.department || '')}</div></div></div></td>
          <td class="num"><b>${r.score}</b></td><td class="num">${r.solved}</td>${d.tie_break === 'penalty' ? `<td class="num muted">${r.penalty}</td>` : ''}
          ${d.questions.map((q) => { const x = r.per[q.id]; return `<td class="num">${x ? `<span style="color:${x.ac ? 'var(--green)' : x.score > 0 ? 'var(--amber)' : 'var(--red)'};font-weight:650">${x.score}</span><div class="tiny faint">${x.tries} try</div>` : '<span class="faint">·</span>'}</td>`; }).join('')}</tr>`).join('')
          : `<tr><td colspan="${5 + d.questions.length}">${empty('No scores yet')}</td></tr>`}</tbody></table></div></div>
      <p class="small muted" style="margin-top:10px">${d.tie_break === 'penalty' ? `Tie-break: lower penalty wins (minutes to each scoring submission + ${d.penalty_minutes} min per earlier wrong attempt).` : 'Tie-break: earliest time to reach the score wins.'}</p></div>`;
    return c.display_status === 'Live' || c.display_status === 'Paused';
  };
  if (await load()) { const t = setInterval(load, 15000); onLeave(() => clearInterval(t)); }
}

async function semesterPage(p) {
  shell('semester');
  const sems = await GET('/api/semesters');
  if (!sems.length) { app.innerHTML = `<div class="container page"><h1>Semester leaderboard</h1><div class="card">${empty('No semester leaderboard yet', 'It appears once organisers set up the semester.')}</div></div>`; return; }
  const sid = p.id || sems[0].id;
  const d = await GET(`/api/semesters/${sid}/leaderboard`);
  app.innerHTML = `<div class="container page"><div class="row between wrap" style="margin-bottom:16px"><div><h1 style="margin:0">Semester leaderboard</h1><div class="muted">${esc(d.semester.academic_year)} ${esc(d.semester.term)} · ${d.semester.status === 'active' ? 'Live totals' : esc(d.semester.status)}</div></div>
    <div class="row"><select class="select" id="semSel" style="width:auto">${sems.map((s) => `<option value="${s.id}" ${s.id == sid ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
    <a class="btn" href="#" id="yearBtn">Academic year view</a></div></div>
    ${d.rows.length ? `<div class="card pad podium-card" style="margin-bottom:18px">${podium(d.rows.slice(0, 3))}</div>` : ''}
    <div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>Rank</th><th>Participant</th><th class="num">Points</th><th class="num">Solved</th><th class="num">Rounds</th>${d.competitions.map((c) => `<th class="num" title="${esc(c.title)}">${esc(c.round_no ? 'R' + String(c.round_no).padStart(2, '0') : c.code)}<div class="tiny faint">${c.points} pts</div></th>`).join('')}</tr></thead><tbody>
    ${d.rows.length ? d.rows.map((r) => `<tr class="${r.me ? 'me' : ''}"><td>${rankBadge(r.rank)}</td><td><div class="row">${avatar(r.name, 'sm', r.avatar)}<div><b>${esc(r.name)}</b>${r.me ? ' <span class="badge violet">You</span>' : ''}<div class="tiny muted">${esc(r.department || '')}</div></div></div></td><td class="num"><b>${r.points}</b></td><td class="num">${r.solved}</td><td class="num">${r.played}</td>
      ${d.competitions.map((c) => `<td class="num">${r.rounds[c.id] ?? '<span class="faint">·</span>'}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${5 + d.competitions.length}">${empty('No points yet')}</td></tr>`}</tbody></table></div></div>
    <p class="small muted" style="margin-top:10px">Each round contributes (your score ÷ round maximum) × the round's semester points. Only rounds assigned to this semester count.</p></div>`;
  $('#semSel').onchange = (e) => nav('/semester/' + e.target.value);
  $('#yearBtn').onclick = async (e) => {
    e.preventDefault();
    const y = await GET(`/api/academic-years/${encodeURIComponent(d.semester.academic_year)}/leaderboard`);
    modal({ title: `Academic year ${y.academic_year}`, size: 'wide', body: y.rows.length ? `<table class="table"><thead><tr><th>Rank</th><th>Name</th><th class="num">Points</th><th class="num">Solved</th></tr></thead><tbody>${y.rows.map((r) => `<tr class="${r.me ? 'me' : ''}"><td>${rankBadge(r.rank)}</td><td>${esc(r.name)}</td><td class="num"><b>${r.points}</b></td><td class="num">${r.solved}</td></tr>`).join('')}</tbody></table>` : empty('No data yet') });
  };
}

// ---------------- practice / profile / rules ----------------
async function practicePage() {
  shell('practice');
  const rows = await GET('/api/student/practice');
  const solved = rows.filter((r) => r.solved).length;
  app.innerHTML = `<div class="container page"><div class="row between wrap" style="margin-bottom:16px"><div><h1 style="margin:0">Practice</h1><div class="muted">Work through each module in order, at your own pace. ${solved} of ${rows.length} solved so far.</div></div>
    <div class="row"><input class="input" id="q" placeholder="Search problems" style="width:220px"><select class="select" id="d" style="width:auto"><option value="">All levels</option><option>Easy</option><option>Intermediate</option><option>Challenging</option></select></div></div>
    <div class="modgrid" id="mods"></div>
    <div id="pl"></div></div>${footer()}`;
  const TYPE = { debug: ['Fix the bug', 'amber'], complete: ['Complete the code', 'blue'], output: ['Predict the output', 'violet'], trace: ['Code tracing', 'violet'], prompt: ['Prompt', 'violet'] };
  const cats = [...new Set(rows.map((r) => r.category))];
  let mod = '';
  const draw = () => {
    const q = $('#q').value.toLowerCase(), d = $('#d').value;
    $('#mods').innerHTML = cats.length > 1 ? [['', 'All modules', rows], ...cats.map((c) => [c, c, rows.filter((r) => r.category === c)])].map(([k, name, rs]) => { const s = rs.filter((r) => r.solved).length;
      return `<button class="modcard ${mod === k ? 'on' : ''}" data-mod="${esc(k)}"><b>${esc(name)}</b><span class="small muted">${s} / ${rs.length} solved</span><div class="bar"><i style="width:${rs.length ? s / rs.length * 100 : 0}%"></i></div></button>`; }).join('') : '';
    const list = rows.filter((r) => (!mod || r.category === mod) && (!q || r.title.toLowerCase().includes(q) || r.tags.join(' ').toLowerCase().includes(q) || r.category.toLowerCase().includes(q)) && (!d || r.difficulty === d));
    const row = (r, n) => `<tr class="click" data-q="${r.id}"><td class="faint num" style="width:36px">${n}</td>
      <td style="width:34px">${r.solved ? '<span style="color:var(--green)">' + ico('check') + '</span>' : r.tried ? '<span style="color:var(--amber)">' + ico('dot') + '</span>' : '<span class="faint">' + ico('circle') + '</span>'}</td>
      <td><b>${esc(r.title)}</b><div class="row wrap" style="gap:4px;margin-top:3px">${TYPE[r.qtype] ? `<span class="badge ${TYPE[r.qtype][1]}">${TYPE[r.qtype][0]}</span>` : ''}${r.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join('')}</div></td>
      <td style="width:130px"><span class="badge ${{ Easy: 'green', Intermediate: 'amber', Challenging: 'red' }[r.difficulty]}">${r.difficulty}</span></td></tr>`;
    $('#pl').innerHTML = list.length ? [...new Set(list.map((r) => r.category))].map((c) => { const rs = list.filter((r) => r.category === c);
      return `<section class="card" style="margin-bottom:16px"><div class="row between" style="padding:16px 18px 10px"><h3 style="margin:0">${esc(c)}</h3><span class="small muted">${rs.filter((r) => r.solved).length} / ${rs.length} solved</span></div>
        <div class="table-wrap"><table class="table"><tbody>${rs.map((r, k) => row(r, k + 1)).join('')}</tbody></table></div></section>`; }).join('')
      : `<div class="card">${empty(rows.length ? 'No match' : 'No practice problems yet', rows.length ? '' : 'Organisers will add problems here after each round.')}</div>`;
    $$('[data-q]').forEach((tr) => tr.onclick = () => nav('/practice/' + tr.dataset.q));
    $$('[data-mod]').forEach((b) => b.onclick = () => { mod = b.dataset.mod; draw(); });
  };
  $('#q').oninput = draw; $('#d').onchange = draw; draw();
}

const BADGE_ICON = {
  spark: 'M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8', check: 'M5 12l5 5L20 7', cpu: 'M7 7h10v10H7zM10 2v3M14 2v3M10 19v3M14 19v3M2 10h3M2 14h3M19 10h3M19 14h3',
  star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z', flag: 'M5 21V4M5 4h11l-2 4 2 4H5', shield: 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
  medal: 'M8 3l4 6 4-6M12 21a6 6 0 1 0 0-12 6 6 0 0 0 0 12z', crown: 'M3 8l4 4 5-7 5 7 4-4-2 11H5z', target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18', flame: 'M12 22c4 0 7-3 7-7 0-5-5-7-5-12-3 2-4 5-4 7-1-1-2-2-2-4-2 2-3 5-3 9 0 4 3 7 7 7z',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
};
let _bid = 0;
const BADGE_COLORS = { // ring light, ring mid, ring dark, extrusion, face top, face bottom
  first_ac: ['#ecfdf5', '#34d399', '#047857', '#064e3b', '#10b981', '#065f46'], solver10: ['#eff6ff', '#60a5fa', '#1d4ed8', '#1e3a8a', '#3b82f6', '#1e40af'],
  solver25: ['#ecfeff', '#22d3ee', '#0e7490', '#164e63', '#06b6d4', '#155e75'], solver50: ['#fefce8', '#facc15', '#a16207', '#713f12', '#eab308', '#854d0e'],
  debut: ['#f5f3ff', '#a78bfa', '#6d28d9', '#3b0764', '#8b5cf6', '#4c1d95'], veteran: ['#f1f5f9', '#94a3b8', '#334155', '#0f172a', '#64748b', '#1e293b'],
  podium: ['#fff7ed', '#fb923c', '#c2410c', '#7c2d12', '#f97316', '#9a3412'], champion: ['#fefce8', '#fde047', '#ca8a04', '#713f12', '#facc15', '#a16207'],
  perfect: ['#fdf2f8', '#f472b6', '#be185d', '#831843', '#ec4899', '#9d174d'], polyglot: ['#f0fdfa', '#2dd4bf', '#0f766e', '#134e4a', '#14b8a6', '#115e59'],
  streak7: ['#fef2f2', '#f87171', '#b91c1c', '#7f1d1d', '#ef4444', '#991b1b'], speed: ['#eef2ff', '#818cf8', '#4338ca', '#312e81', '#6366f1', '#3730a3'],
  default: ['#f5f3ff', '#a78bfa', '#6d28d9', '#3b0764', '#8b5cf6', '#4c1d95'] };
const hexBadge = (b, small) => {
  const id = `hb${++_bid}`, on = b.earned;
  const pal = BADGE_COLORS[b.key] || BADGE_COLORS.default;
  const c = on ? pal : ['#f8fafc', '#cbd5e1', '#94a3b8', '#475569', '#cbd5e1', '#94a3b8'];
  return `<div class="hexb ${on ? 'on' : ''} ${small ? 'small' : ''}" title="${esc(b.name)}: ${esc(b.description)}${on ? ' (earned)' : ' (locked)'}">
  <svg viewBox="0 0 100 116" aria-hidden="true"><defs>
    <linearGradient id="${id}r" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c[0]}"/><stop offset=".45" stop-color="${c[1]}"/><stop offset="1" stop-color="${c[2]}"/></linearGradient>
    <linearGradient id="${id}f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c[4]}"/><stop offset="1" stop-color="${c[5]}"/></linearGradient>
    <linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>
    <path d="M50 10l42 24v54L50 112 8 88V34z" fill="${c[3]}"/>
    <path d="M50 4l42 24v54L50 106 8 82V28z" fill="url(#${id}r)"/>
    <path d="M50 14l33 19v42L50 94 17 75V33z" fill="url(#${id}f)" stroke="rgba(0,0,0,.18)" stroke-width="1.5"/>
    <path d="M50 14l33 19v12C66 36 34 36 17 45V33z" fill="url(#${id}g)"/>
    <g transform="translate(32 36) scale(1.5)" fill="none" stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" ${on ? '' : 'opacity=".75"'}><path d="${BADGE_ICON[b.icon]}"/></g>
    ${on ? '' : `<g transform="translate(64 74)"><circle r="11" cx="11" cy="11" fill="#475569" stroke="#fff" stroke-width="2"/><g transform="translate(5 4.5) scale(.5)" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"><path d="${ICONS.lock}"/></g></g>`}
  </svg>${small ? '' : `<span>${esc(b.name)}</span>`}</div>`;
};
function heatmap(h) {
  const days = [], today = new Date(); const start = new Date(today); start.setDate(start.getDate() - 7 * 22 + 1 - start.getDay());
  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) days.push(new Date(d));
  const key = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: S.tz }).format(d);
  const lvl = (n) => (!n ? 0 : n < 3 ? 1 : n < 6 ? 2 : n < 10 ? 3 : 4);
  const cols = []; for (let i = 0; i < days.length; i += 7) cols.push(days.slice(i, i + 7));
  return `<div class="heat">${cols.map((c) => `<div>${c.map((d) => { const n = h[key(d)] || 0; return `<i class="l${lvl(n)}" title="${key(d)}: ${n} submission${n === 1 ? '' : 's'}"></i>`; }).join('')}</div>`).join('')}</div>
    <div class="row between tiny muted" style="margin-top:6px"><span>${fmtX(start.getTime() / 1000, { month: 'short' })}</span><span class="row" style="gap:3px">Less <i class="hk l0"></i><i class="hk l1"></i><i class="hk l2"></i><i class="hk l3"></i><i class="hk l4"></i> More</span><span>Today</span></div>`;
}
function insightsHTML(ins) {
  return `<div class="row between"><h3 style="margin:0" class="row">${ico('bulb', 18)} Insights for you</h3><span class="badge violet">Your level: ${esc(ins.level)}</span></div>
    <div class="grid g2" style="margin-top:12px;align-items:start"><div><div class="small muted" style="margin-bottom:6px">Topic mastery</div>
      ${ins.topics.length ? ins.topics.slice(0, 6).map((t) => `<div style="margin-bottom:8px"><div class="row between small"><span>${esc(t.category)}</span><span class="muted">${t.solved}/${t.tried} solved</span></div><div class="bar"><i style="width:${t.mastery}%"></i></div></div>`).join('') : '<div class="small muted">Solve a few problems to see your strengths.</div>'}
      <div class="notice small" style="margin-top:10px">${ico('bulb', 14)} ${esc(ins.tip)}</div></div>
      <div><div class="small muted" style="margin-bottom:6px">Recommended next</div>${ins.recommendations.length ? ins.recommendations.map((r) => `<a class="recrow" href="#/practice/${r.id}"><span class="lsi sm">${ico('code', 15)}</span><div class="grow"><b class="small">${esc(r.title)}</b><div class="tiny muted">${esc(r.reason)} · ${esc(r.difficulty)}</div></div>${ico('right', 14)}</a>`).join('')
        : '<div class="small muted">No practice problems left to suggest. Great work!</div>'}</div></div>`;
}
function spotlight(c, live, open, h) {
  const done = live && h && h.finished;
  const tone = done ? 'is-done' : live ? 'is-live' : open ? 'is-open' : 'is-next';
  const left = c.remaining_sec || 0, total = (c.duration_min + (c.extra_min || 0)) * 60 || 1;
  const parts = (sec) => { sec = Math.max(0, Math.floor(sec)); return [Math.floor(sec / 3600), Math.floor(sec % 3600 / 60), sec % 60].map((x) => String(x).padStart(2, '0')); };
  const [hh, mm, ss] = parts(left);
  const icon = done ? 'check2' : live ? 'zap' : open ? 'flag' : 'calendar';
  const title = done ? 'Assessment submitted' : live ? 'Live now' : open ? 'Registration open' : 'You are registered';
  const sub = done ? `You finished <b>${esc(c.title)}</b>. Results appear here when the organisers publish them.`
    : live ? `${c.question_count} problem${c.question_count === 1 ? '' : 's'} · ${c.languages.map(langName).join(', ')}` : `Starts ${fmtTime(c.start_at)} · ${fmtMins(c.duration_min)} · ${c.question_count} problems`;
  const btn = done ? `<a class="btn lg sp-btn" href="#/c/${encodeURIComponent(c.code)}">${ico('eye', 14)} View round</a>`
    : live ? `<a class="btn lg sp-btn" href="#/arena/${c.id}">${ico('play', 14)} Enter arena</a>` : open ? `<button class="btn lg sp-btn" data-join="${c.id}">${ico('plus', 14)} Join now</button>`
      : `<a class="btn lg sp-btn" href="#/c/${encodeURIComponent(c.code)}">Details ${ico('right', 14)}</a>`;
  return `<section class="spotlight ${tone}"><div class="sp-icon">${ico(icon, 26)}</div>
    <div class="sp-main"><div class="row wrap" style="gap:8px">${badge(c.display_status)}<span class="tiny mono sp-code">${esc(c.code)}</span><span class="sp-kicker">${title}</span></div>
      <h2>${esc(c.title)}</h2><div class="small sp-sub">${sub}</div>
      ${live && !done ? `<div class="sp-bar"><i data-prog style="width:${Math.min(100, Math.max(0, 100 - left / total * 100))}%"></i></div>` : ''}</div>
    ${live && !done ? `<div class="sp-clock" data-clock="${Math.floor(Date.now() / 1000) + left}" data-total="${total}">${[[hh, 'hrs'], [mm, 'min'], [ss, 'sec']].map(([v, l]) => `<div><b>${v}</b><span>${l}</span></div>`).join('')}</div>` : ''}
    <div class="sp-cta">${btn}</div></section>`;
}
function xpOf(st) { return st.solved * 20 + st.accepted * 2 + st.rounds * 30 + st.badges.filter((b) => b.earned).length * 25 + st.longest_streak * 5; }
async function profilePage(p) {
  shell('profile');
  app.innerHTML = `<div class="container page">${skeleton(6)}</div>`;
  const [d, st, hist, comps] = await Promise.all([GET('/api/student/profile'), GET('/api/student/stats'), GET('/api/student/history'), GET('/api/public/competitions')]);
  const m = d.me, sem = st.semester, earned = st.badges.filter((b) => b.earned);
  const lv = levelOf(xpOf(st));
  const live = comps.find((c) => c.joined && ['Live', 'Paused'].includes(c.display_status));
  const open = !live && comps.find((c) => c.can_join);
  const next = !live && !open && comps.find((c) => c.joined && ['Upcoming', 'Registration Open', 'Registration Closed'].includes(c.display_status));
  const hl = live || open || next;
  const diff = [['Easy', 'green'], ['Intermediate', 'amber'], ['Challenging', 'red']];
  app.innerHTML = `<div class="container page stack loose">
    ${m.must_change_pw ? '<div class="notice warn">You are using a temporary password. Set your own from the profile card.</div>' : ''}
    ${hl ? spotlight(hl, live, open, d.history.find((h) => h.id === hl.id)) : ''}
    <div class="profile-grid">
      <aside class="card pcard"><div class="pbanner"></div><div class="pbody">
        <div class="phead"><label class="avwrap" title="Change profile picture">${avatar(m.name, 'xl', m.avatar)}<span class="avcam">${ico('upload', 15)}</span><input type="file" id="avIn" accept="image/png,image/jpeg,image/webp" hidden></label><h2 class="ellip">${esc(m.name)}</h2><div class="small muted ellip">${esc(m.email || m.participant_id)}</div></div>
        <div class="lvl"><div class="row between"><b class="row" style="gap:6px">${ico('star', 15)} Level ${lv.level} · ${esc(lv.name)}</b><span class="tiny muted">${lv.xp} XP</span></div>
          <div class="bar" style="margin-top:6px"><i style="width:${lv.pct}%"></i></div><div class="tiny muted" style="margin-top:4px">${lv.to - lv.xp} XP to level ${lv.level + 1}</div></div>
        <div class="grid g3" style="margin-top:12px;gap:8px"><div class="mini"><span>Solved</span><b>${st.solved}</b></div><div class="mini"><span>Rounds</span><b>${st.rounds}</b></div><div class="mini"><span>Badges</span><b>${earned.length}</b></div></div>
        <div class="sec">Basic information</div><dl class="kv" style="grid-template-columns:104px 1fr"><dt>Participant ID</dt><dd class="mono">${esc(m.participant_id)}</dd><dt>USN</dt><dd class="mono">${esc(m.usn || '')}</dd>
          <dt>Department</dt><dd>${esc(m.department)}</dd><dt>Year</dt><dd>${esc(m.year)}</dd><dt>Semester</dt><dd>${m.semester ? 'Sem ' + esc(m.semester) : '<span class="muted">not set</span>'}</dd><dt>Section</dt><dd>${esc(m.section || '—')}</dd></dl>
        <div class="sec">Languages</div><div class="row wrap" style="gap:6px">${st.languages.length ? st.languages.map((l) => `<span class="chip">${langName(l)}</span>`).join('') : '<span class="small muted">Solve a problem to show your languages</span>'}</div>
        <div class="row" style="margin-top:16px;gap:8px"><button class="btn grow" id="editP">${ico('pencil', 14)} Edit profile</button><button class="btn ghost grow" id="pwBtn">${ico('lock', 14)} Password</button></div></div></aside>
      <div class="stack">
        <div class="grid g4">${[['medal', 'Rank', sem?.rank ? '#' + sem.rank : '—', sem ? `of ${sem.of} this semester` : 'no semester yet'], ['star', 'Points', sem ? sem.points : 0, sem?.percentile != null ? `top ${Math.max(1, Math.round(100 - sem.percentile + 1))}%` : 'play a round'],
          ['check2', 'Solved', st.solved, `${st.accepted}/${st.submissions} accepted`], ['flame', 'Streak', st.streak, `days · best ${st.longest_streak}`]].map(([i, k, v, x]) => `<div class="card stat tile"><span class="lsi">${ico(i, 18)}</span><div><div class="k">${k}</div><div class="v">${v}</div><div class="s">${x}</div></div></div>`).join('')}</div>
        <div class="grid g2" style="align-items:stretch"><section class="card pad"><div class="row between"><h3 style="margin:0">Achievements</h3><span class="small muted">${earned.length}/${st.badges.length}</span></div>
          <div class="badges">${st.badges.map((b) => hexBadge(b)).join('')}</div><div class="tiny muted">Hover a badge to see how to earn it.</div></section>
          <section class="card pad"><div class="row between"><h3 style="margin:0">Activity</h3><span class="small row" style="gap:4px">${ico('flame', 14)} <b>${st.streak}</b> day streak</span></div><div style="margin-top:14px">${heatmap(st.heatmap)}</div>
          <div class="grid g3" style="margin-top:14px;gap:8px">${diff.map(([k, c]) => `<div class="mini"><span class="badge ${c}" style="height:18px">${k}</span><b>${st.solved_by_difficulty[k]}<small class="muted" style="font-size:12px;font-weight:500"> / ${st.totals_by_difficulty[k]}</small></b></div>`).join('')}</div></section></div>
        <section class="card pad" id="insights"><div class="skel" style="height:120px"></div></section>
      </div></div>
    <section class="card"><div class="card-head"><h3 class="row" style="gap:8px">${ico('code', 17)} My problems</h3><div class="seg" id="pf">${[['all', 'All'], ['solved', 'Solved'], ['trying', 'In progress']].map(([k, l], i) => `<button data-f="${k}" class="${i ? '' : 'on'}">${l}</button>`).join('')}</div></div><div id="plist"></div></section>
    <section class="card"><div class="card-head"><h3 class="row" style="gap:8px">${ico('trophy', 17)} Competition history</h3><span class="small muted">${d.practice_solved} practice problems solved</span></div>
      ${d.history.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Competition</th><th>Status</th><th class="num">Rank</th><th class="num">Score</th><th class="num">Solved</th></tr></thead><tbody>
      ${d.history.map((h) => `<tr class="click" onclick="nav('/leaderboard/${h.id}')"><td><b>${esc(h.title)}</b><div class="tiny muted mono">${esc(h.code)} · ${fmtDate(h.start_at)}</div></td><td>${badge(h.display_status)}</td>
        <td class="num">${h.rank ? `${rankBadge(h.rank)}<span class="faint"> / ${h.of}</span>` : h.results_pending ? '<span class="badge amber">results pending</span>' : h.finished ? '<span class="badge blue">submitted</span>' : '—'}</td><td class="num"><b>${h.score ?? '—'}</b></td><td class="num">${h.solved ?? '—'}</td></tr>`).join('')}</tbody></table></div>` : empty('No competitions yet', 'Join a round from Competitions.')}</section>
  </div>${footer()}`;
  // my problems
  const drawP = (f) => {
    const list = hist.problems.filter((x) => f === 'all' || (f === 'solved' ? x.solved : !x.solved));
    $('#plist').innerHTML = list.length ? `<div class="table-wrap"><table class="table"><thead><tr><th style="width:36px"></th><th>Problem</th><th>Where</th><th>Difficulty</th><th class="num">Attempts</th><th>Languages</th><th class="num">Best</th><th>Last tried</th><th></th></tr></thead><tbody>
      ${list.map((x) => `<tr><td>${x.solved ? `<span class="okdot">${ico('check', 14)}</span>` : `<span class="trydot">${ico('dot', 14)}</span>`}</td><td><b>${esc(x.title)}</b><div class="tiny muted">${esc(x.category)}</div></td>
        <td class="small">${x.competition_id ? `<a href="#/leaderboard/${x.competition_id}">${esc(x.where)}</a>` : `<a href="#/practice/${x.question_id}">Practice</a>`}</td><td><span class="badge ${{ Easy: 'green', Intermediate: 'amber', Challenging: 'red' }[x.difficulty]}">${esc(x.difficulty)}</span></td>
        <td class="num">${x.attempts}</td><td class="small">${x.languages.map(langName).join(', ')}</td><td class="num">${x.competition_id ? x.best_score : (x.solved ? ico('check', 14) : '—')}</td><td class="small muted nowrap">${fmtTime(x.last_at)}</td>
        <td class="right"><button class="btn xs" data-code="${x.last_submission}">${ico('eye', 12)} Code</button></td></tr>`).join('')}</tbody></table></div>`
      : empty(f === 'solved' ? 'Nothing solved yet' : 'No problems attempted yet', 'Try a practice problem: they are the fastest way to earn XP.');
    $$('[data-code]', $('#plist')).forEach((b) => b.onclick = async () => { const sub = await GET(`/api/student/submissions/${b.dataset.code}`); modal({ title: `Your latest submission · ${langName(sub.language)}`, size: 'wide', body: `<div class="row" style="margin-bottom:10px">${vbadge(sub.verdict, sub.status)}<span class="small muted">${fmtTime(sub.created_at)}</span></div><pre style="max-height:60vh">${esc(sub.code)}</pre>` }); });
  };
  $$('#pf button').forEach((b) => b.onclick = () => { $$('#pf button').forEach((x) => x.classList.toggle('on', x === b)); drawP(b.dataset.f); });
  drawP('all');
  bindJoin(app, () => profilePage(p));
  const tick = setInterval(() => {
    $$('[data-end]').forEach((el) => { el.textContent = fmtDur(Number(el.dataset.end) - Date.now() / 1000); });
    $$('[data-clock]').forEach((el) => {
      const left = Math.max(0, Number(el.dataset.clock) - Date.now() / 1000), t = Number(el.dataset.total) || 1;
      const v = [Math.floor(left / 3600), Math.floor(left % 3600 / 60), Math.floor(left % 60)].map((x) => String(x).padStart(2, '0'));
      el.querySelectorAll('b').forEach((b, i) => { b.textContent = v[i]; });
      const pg = $('[data-prog]'); if (pg) pg.style.width = Math.min(100, 100 - left / t * 100) + '%';
    });
  }, 1000);
  onLeave(() => clearInterval(tick));
  GET('/api/student/insights').then((ins) => { if ($('#insights')) $('#insights').innerHTML = insightsHTML(ins); }).catch(() => $('#insights')?.remove());
  $('#editP').onclick = () => modal({ title: 'Edit profile', body: `<form class="stack" id="pf2"><label class="field">Current semester<select class="select" name="semester" data-num="1">${[1, 2, 3, 4, 5, 6, 7, 8].map((n) => `<option value="${n}" ${m.semester == n ? 'selected' : ''}>Semester ${n}</option>`).join('')}</select></label>
      <label class="field">Section<input class="input" name="section" value="${esc(m.section || '')}"></label><label class="field">Email <span class="hint">college or personal</span><input class="input" type="email" name="email" value="${esc(m.email || '')}"></label>
      <p class="tiny muted" style="margin:0">Name, USN, department and date of birth can only be corrected by an organiser.</p></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Save', kind: 'primary', onClick: async (mm) => { await PUT('/api/me/profile', formData($('#pf2', mm))); toast('Profile updated', 'ok'); profilePage(p); } }] });
  const pw = () => modal({ title: 'Change password', body: `<form class="stack" id="cp"><label class="field">Current password${pwField('current')}</label><label class="field">New password${pwField('new', 'At least 8 characters', 'new-password')}</label></form>`,
    actions: [{ label: 'Cancel' }, { label: 'Update', kind: 'primary', onClick: async (mm) => { await POST('/api/me/password', formData($('#cp', mm))); S.user.must_change_pw = false; toast('Password updated', 'ok'); } }] });
  $('#pwBtn').onclick = pw;
  $('#avIn').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    if (f.size > 700 * 1024) return toast('Choose an image smaller than 700 KB.', 'err');
    const fd = new FormData(); fd.append('file', f);
    try { const r = await api('POST', '/api/me/avatar', fd); S.user.avatar = r.avatar; toast('Profile picture updated', 'ok'); profilePage(p); } catch (er) { fail(er); }
  };
  if (m.avatar) $('.phead').insertAdjacentHTML('beforeend', `<button class="btn xs ghost" id="avDel" style="margin-top:4px">${ico('x', 12)} Remove photo</button>`);
  if ($('#avDel')) $('#avDel').onclick = async () => { await DEL('/api/me/avatar').catch(fail); S.user.avatar = null; profilePage(p); };
  if (p.query.pw) return pw();
  // celebrations: results published, new badges, level up (each shown once per browser)
  const seen = (k) => { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } };
  const key = `astra:${m.participant_id}`;
  const seenRes = seen(key + ':results') || [];
  const fresh = hist.results.find((r) => !seenRes.includes(r.id));
  const seenBadges = seen(key + ':badges');
  const newBadges = earned.filter((b) => seenBadges && !seenBadges.includes(b.key));
  const lastLevel = seen(key + ':level');
  if (fresh) {
    save(key + ':results', [...seenRes, ...hist.results.map((r) => r.id)]);
    const top = fresh.rank <= 3;
    celebrate({ icon: top ? 'crown' : 'trophy', tone: top ? 'gold' : 'violet', title: `Results are out: you ranked #${fresh.rank}`, text: `${esc(fresh.title)} · ${fresh.score} / ${fresh.max} marks · ${fresh.of} participants${top ? '. Podium finish!' : '. Keep climbing!'}`,
      action: { label: 'See leaderboard', onClick: () => nav('/leaderboard/' + fresh.id) } });
  } else if (newBadges.length) {
    celebrate({ icon: 'award', tone: 'gold', title: newBadges.length > 1 ? `${newBadges.length} new badges unlocked!` : `Badge unlocked: ${newBadges[0].name}`, text: newBadges.map((b) => `<b>${esc(b.name)}</b>: ${esc(b.description)}`).join('<br>') });
  } else if (lastLevel && lv.level > lastLevel) {
    celebrate({ icon: 'star', tone: 'sky', title: `Level up! You are now level ${lv.level}`, text: `${esc(lv.name)} · ${lv.xp} XP` });
  }
  save(key + ':badges', earned.map((b) => b.key));
  save(key + ':level', lv.level);
}

function rulesPage() {
  shell('rules');
  app.innerHTML = `<div class="container page" style="max-width:820px"><h1>Rules &amp; guidelines</h1><div class="card pad prose">
    <h4>Accounts</h4><ul><li>Use only your own account. Sharing accounts or competing for someone else leads to disqualification.</li><li>Keep your password private. Organisers can reset it if you forget it.</li></ul>
    <h4>During a competition</h4><ul><li>Problems open when the competition goes live and lock when the timer ends. Pauses and extensions apply to everyone equally.</li>
      <li>Read input from <b>standard input</b> and print to <b>standard output</b>. Print exactly what the output format asks for; extra text makes the answer wrong.</li>
      <li><b>Run</b> tests your code on the sample cases (or your own custom input). It never affects your score.</li><li><b>Submit</b> judges your code on hidden test cases. Only your best submission per problem counts.</li>
      <li>Hidden test inputs are never shown. If a sample fails, you will see it.</li></ul>
    <h4>Verdicts</h4><ul><li><b>Accepted</b>: all tests passed. <b>Wrong Answer</b>: output differs. <b>Compilation Error</b>: fix the errors shown.</li><li><b>Runtime Error</b>: your program crashed or returned non-zero. <b>Time / Memory Limit Exceeded</b>: too slow or too much memory.</li>
      <li><b>Internal Judge Error</b>: a problem on our side. It never counts against you; organisers will re-judge it.</li></ul>
    <h4>Fair play</h4><ul><li>Work on your own. Do not copy code from other participants, AI tools or websites unless the round allows it.</li><li>The workspace records activity such as pasting, copying and switching tabs, and organisers review similar submissions.</li>
      <li>Do not attempt to attack the judge or the network. Submissions are run in an isolated sandbox with no network access and are logged.</li></ul></div></div>${footer()}`;
}

// ================= coding workspace =================
async function workspace({ mode, compId, qid, q: startQ }) {
  $('#topnav').classList.add('hidden');
  document.body.classList.remove('with-side', 'landing-body');
  app.innerHTML = `<div class="ws"><div class="ws-top">${skeleton(1)}</div><div></div></div>`;
  const contest = mode === 'contest';
  let data, problems;
  try {
    if (contest) { data = await GET(`/api/student/competitions/${compId}/arena`); problems = data.problems; }
    else { const pr = await GET(`/api/student/practice/${qid}`); problems = [pr]; data = { competition: null }; }
  } catch (e) {
    $('#topnav').classList.remove('hidden'); shell('');
    app.innerHTML = `<div class="container page"><div class="card">${empty(e.message, contest ? `<a href="#/profile">Back to dashboard</a>` : '<a href="#/practice">Back to practice</a>')}</div></div>`;
    return;
  }
  const comp = data.competition;
  if (!problems.length) { $('#topnav').classList.remove('hidden'); shell(''); app.innerHTML = `<div class="container page"><div class="card">${empty('No problems in this competition yet')}</div></div>`; return; }
  const pref = JSON.parse(localStorage.getItem('astra:prefs') || '{}');
  pref.font = pref.font || 14; pref.theme = pref.theme || 'light';
  let cur = problems.find((p) => String(p.id) === String(startQ)) || problems[0];
  const codeKey = (p, l) => `astra:code:${contest ? compId : 'p'}:${p.id}:${l}`;
  const langOf = (p) => { const l = localStorage.getItem(`astra:lang:${contest ? compId : 'p'}:${p.id}`) || pref.lang; return p.languages.includes(l) ? l : p.languages[0]; };
  let lang = langOf(cur);
  let ended = false;

  app.innerHTML = `<div class="ws" id="ws" data-theme="${pref.theme}">
    <div class="ws-top"><a href="${contest ? '#/profile' : '#/practice'}" class="brand" title="Leave workspace"><img src="${LOGO}" alt="" style="height:24px"></a>
      <div class="small" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${esc(contest ? comp.title : 'Practice')}</b></div>
      <div class="ptabs" id="ptabs"></div>
      ${contest ? `<span id="cstatus"></span><span class="timer" id="timer">--:--</span><button class="btn sm success" id="finishBtn" title="Final submission: end your test">${ico('flag', 14)} Finish test</button><a class="btn ghost sm" href="#/leaderboard/${compId}" target="_blank" title="Leaderboard">${ico('trophy')}</a><button class="btn ghost sm" id="annBtn" title="Announcements">${ico('bell')}<span id="annDot"></span></button>` : ''}
      <button class="btn ghost sm" id="fsBtn" title="Focus mode (fullscreen)">${ico('maximize')}</button></div>
    <div class="ws-main" id="wsmain" style="--left:${pref.left || '42%'}">
      <div class="ws-left" id="prob"></div><div class="gutter" id="vg"></div>
      <div class="ws-right" id="wsright" style="--bottom:${pref.bottom || '240px'};position:relative">
        <div class="ws-bar"><select class="select" id="lang" aria-label="Language"></select>
          <select class="select" id="font" aria-label="Font size" title="Font size">${[12, 13, 14, 15, 16, 18, 20].map((n) => `<option value="${n}" ${n === pref.font ? 'selected' : ''}>${n}px</option>`).join('')}</select>
          <button class="btn ghost sm" id="themeBtn" title="Toggle theme">${pref.theme === 'dark' ? '' + ico('sun') + '' : '' + ico('moon') + ''}</button><button class="btn ghost sm" id="resetBtn" title="Reset to starter code">${ico('rotate')} Reset</button>
          <div class="grow"></div><span class="tiny muted" id="saved"></span>
          <button class="btn sm" id="runBtn" title="Ctrl/Cmd + Enter">▶ Run</button><button class="btn primary sm" id="subBtn" title="Ctrl/Cmd + Shift + Enter">Submit</button></div>
        <div id="editor"></div><div class="hgutter" id="hg"></div>
        <div class="ws-bottom"><div class="tabs" id="btabs"><button data-t="res" class="on">Results</button><button data-t="custom">Custom input</button><button data-t="subs">Submissions</button><button data-t="ai" id="aiTab" class="hidden">${ico('sparkles', 14)} Assistant</button></div>
          <div class="ws-out" id="out"></div></div></div></div></div>`;
  const ws = $('#ws');
  const editor = await codeEditor($('#editor'), { value: '', language: 'c', theme: pref.theme === 'dark' ? 'astra-dark' : 'astra-light', fontSize: pref.font });
  const models = {};
  const getModel = (p, l) => {
    const k = `${p.id}:${l}`;
    if (!models[k]) {
      models[k] = monaco.editor.createModel(localStorage.getItem(codeKey(p, l)) ?? p.starter?.[l] ?? S.info.languages[l]?.template ?? '', S.info.languages[l]?.monaco || 'plaintext');
      models[k].onDidChangeContent(() => { localStorage.setItem(codeKey(p, l), models[k].getValue()); $('#saved').textContent = 'Saved locally'; });
    }
    return models[k];
  };
  const savePref = () => localStorage.setItem('astra:prefs', JSON.stringify(pref));
  onLeave(() => { editor.dispose(); Object.values(models).forEach((m) => m.dispose()); document.fullscreenElement && document.exitFullscreen?.(); });

  // ---------- activity tracking (contest only; transparent to students) ----------
  const queue = [];
  let internalClip = '', keys = 0, lastSnap = {};
  const track = (type, detail, question_id = cur.id) => { if (!contest) return; queue.push({ type, detail, question_id, at: Math.floor(Date.now() / 1000) }); if (queue.length >= 40) flush(); };
  const flush = (beacon) => {
    if (!contest || !queue.length) return;
    const events = queue.splice(0, queue.length);
    fetch('/api/student/activity', { method: 'POST', keepalive: !!beacon, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': S.csrf }, body: JSON.stringify({ competition_id: compId, events }) })
      .then((r) => r.json()).then((j) => { if (j && j.finished) autoSubmitted(j); }).catch(() => {});
  };
  const LD = (contest && comp.lockdown) || {};
  if (LD.no_copy || LD.no_paste) { editor.updateOptions({ contextmenu: false, dragAndDrop: false }); if (LD.no_copy) $('#ws').classList.add('nocopy'); }
  // ---------- exam lockdown: cover the test while a rule is broken; warnings are counted on the server ----------
  const isOver = () => { try { return ended || finished; } catch (e) { return ended; } };
  const why = {};
  let warnings = 0, lastWarn = 0, awayAt = 0;
  const lockDraw = () => {
    let o = $('#ldlock'); const html = isOver() ? null : (why.net || why.fs || why.away);
    if (!html) return o?.remove();
    if (!o) { o = document.createElement('div'); o.id = 'ldlock'; o.className = 'wsmsg'; o.style.position = 'fixed'; o.style.zIndex = '95'; ws.appendChild(o); }
    o.innerHTML = `<div class="card pad center" style="max-width:460px">${html}</div>`;
    $('[data-ld=fs]', o)?.addEventListener('click', () => ws.requestFullscreen?.().catch(() => toast('Allow full screen to continue', 'err')));
    $('[data-ld=ok]', o)?.addEventListener('click', () => { why.away = null; lockDraw(); if (LD.fullscreen === 'required' && !document.fullscreenElement) ws.requestFullscreen?.().catch(() => {}); });
  };
  const warnLine = () => LD.max_violations ? `<p class="small" style="color:var(--red)"><b>Warning ${warnings} of ${LD.max_violations}.</b> At ${LD.max_violations} your test is submitted automatically.</p>` : '<p class="small muted">This is recorded for the organisers.</p>';
  const warn = (kind) => { if (!contest || isOver() || Date.now() - lastWarn < 3000) return false; lastWarn = Date.now(); warnings++; track('violation', { kind, n: warnings }); flush(); return true; };
  const autoSubmitted = (j) => { if (isOver()) return; ended = true; try { finished = true; } catch (e) { /* not declared yet */ } why.net = why.fs = why.away = null; lockDraw();
    $('#subBtn').disabled = true; $('#finishBtn')?.remove(); document.fullscreenElement && document.exitFullscreen?.();
    overlay(`<div class="emptyi" style="background:var(--red-soft);color:var(--red)">${ico('alert', 28)}</div><h3>Test submitted automatically</h3><p class="muted">You reached ${j.limit} fair-play warnings (leaving the test or full screen). Your answers so far are kept. If this was a mistake, talk to the invigilator: they can reopen your test.</p><a class="btn primary" href="#/profile">Back to dashboard</a>`); };
  if (contest) {
    const policy = comp.paste_policy;
    const edEl = $('#editor');
    edEl.addEventListener('paste', (e) => {
      const text = e.clipboardData?.getData('text/plain') || '';
      const internal = !!text && text === internalClip;
      if (LD.no_paste || (policy === 'block_external' && !internal && text.length > 1)) {
        e.preventDefault(); e.stopImmediatePropagation();
        toast(LD.no_paste ? 'Pasting is disabled in this competition.' : 'Pasting from outside the editor is disabled in this competition.', 'err');
        track('paste_blocked', { chars: text.length, preview: text.slice(0, 300) });
        return;
      }
      track('paste', { chars: text.length, lines: text.split('\n').length, internal, preview: text.slice(0, 500) });
    }, true);
    const onCopy = (e) => {
      const inEd = edEl.contains(e.target);
      let t = inEd ? editor.getModel().getValueInRange(editor.getSelection()) : String(window.getSelection() || '');
      if (LD.no_copy) { e.preventDefault(); toast('Copying is disabled in this competition.', 'err'); track(e.type, { chars: t.length, from: inEd ? 'editor' : 'page', blocked: true }); return; }
      internalClip = t;
      track(e.type, { chars: t.length, from: inEd ? 'editor' : 'page' });
    };
    document.addEventListener('copy', onCopy, true); document.addEventListener('cut', onCopy, true);
    const vis = () => track(document.hidden ? 'tab_hidden' : 'tab_visible', null);
    const blur = () => track('blur', null), focus = () => track('focus', null), ctx = (e) => { track('right_click', null); if (LD.no_copy || LD.no_paste) e.preventDefault(); };
    const unload = () => flush(true);
    document.addEventListener('visibilitychange', vis); window.addEventListener('blur', blur); window.addEventListener('focus', focus); document.addEventListener('contextmenu', ctx); window.addEventListener('pagehide', unload);
    editor.onKeyDown(() => keys++);
    const tick = setInterval(() => {
      if (keys) { track('keystrokes', { n: keys }); keys = 0; }
      const k = `${cur.id}:${lang}`, code = editor.getValue();
      if (lastSnap[k] !== code) { lastSnap[k] = code; track('snapshot', { language: lang, code }); }
      track('heartbeat', null); flush();
    }, 30000);
    const fl = setInterval(() => flush(), 10000);
    // Contest devices should be on the campus network only. If the public internet is reachable, record it for organisers.
    let netState = null, np = null;
    const probe = async () => {
      const hit = async (u) => { try { await fetch(u, { mode: 'no-cors', cache: 'no-store', signal: AbortSignal.timeout(4000) }); return true; } catch (e) { return false; } };
      const online = (await Promise.all(['https://connectivitycheck.gstatic.com/generate_204', 'https://www.cloudflare.com/favicon.ico'].map(hit))).some(Boolean);
      if (online && netState !== true) { track('internet', { reachable: true }); flush();
        if (!$('#netWarn')) $('.ws-top').insertAdjacentHTML('beforeend', `<span class="badge red" id="netWarn" title="Your device can reach the internet. This is reported to the organisers.">${ico('wifi', 13)} Internet detected</span>`); }
      if (!online) $('#netWarn')?.remove();
      netState = online;
      if (LD.internet === 'block') {
        why.net = online ? `<div class="emptyi" style="background:var(--red-soft);color:var(--red)">${ico('wifi', 28)}</div><h3>Internet is not allowed in this round</h3><p class="muted">This computer can reach the internet. Turn off Wi-Fi, mobile hotspot or any other internet connection. The test opens again by itself within a few seconds. The timer keeps running.</p>` : null;
        lockDraw(); clearInterval(np); np = setInterval(probe, online ? 5000 : 20000);
      }
    };
    if (LD.internet !== 'allow') { probe(); np = setInterval(probe, LD.internet === 'block' ? 20000 : 45000); }
    onLeave(() => clearInterval(np));
    // leaving the test window (other tab or app): cover the test on return and count a warning
    const away = () => { if (!awayAt) awayAt = Date.now(); };
    const back = () => { if (!awayAt) return; const secs = Math.round((Date.now() - awayAt) / 1000); awayAt = 0;
      if (LD.leave !== 'warn' || secs < 1 || isOver()) return;
      if (warn('left_test')) { why.away = `<div class="emptyi" style="background:var(--amber-soft);color:var(--amber)">${ico('alert', 28)}</div><h3>You left the test</h3><p class="muted">You switched to another tab or app for ${secs}s.</p>${warnLine()}<button class="btn primary lg" data-ld="ok">Continue the test</button>`; lockDraw(); } };
    const onVis = () => (document.hidden ? away() : back());
    document.addEventListener('visibilitychange', onVis); window.addEventListener('blur', away); window.addEventListener('focus', back);
    onLeave(() => { document.removeEventListener('visibilitychange', onVis); window.removeEventListener('blur', away); window.removeEventListener('focus', back); });
    onLeave(() => { flush(true); clearInterval(tick); clearInterval(fl); document.removeEventListener('copy', onCopy, true); document.removeEventListener('cut', onCopy, true);
      document.removeEventListener('visibilitychange', vis); window.removeEventListener('blur', blur); window.removeEventListener('focus', focus); document.removeEventListener('contextmenu', ctx); window.removeEventListener('pagehide', unload); });
    track('heartbeat', { ua: navigator.userAgent.slice(0, 120), screen: `${screen.width}x${screen.height}` });
    flush();
  }
  $('#fsBtn').onclick = () => document.fullscreenElement ? document.exitFullscreen() : ws.requestFullscreen?.().catch(() => toast('Fullscreen not available', 'err'));
  if (contest && LD.fullscreen !== 'off' && document.fullscreenEnabled) {
    const fsGate = (left) => `<img src="${LOGO}" alt="" style="height:44px"><h3 style="margin:10px 0 6px">${left ? 'You left full screen' : esc(comp.title)}</h3>
      <p class="muted small">${left ? 'The test stays hidden until you return to full screen.' : 'This test runs in full screen. It stays hidden outside full screen, and leaving it or switching to another tab or app counts as a warning.'}</p>${left ? warnLine() : ''}
      <button class="btn primary lg" data-ld="fs">${left ? 'Return to full screen' : 'Enter full screen &amp; start'}</button>`;
    const onFs = () => {
      if (document.fullscreenElement) { why.fs = null; return lockDraw(); }
      track('fullscreen_exit', null);
      if (isOver()) return;
      warn('fullscreen_exit');
      why.fs = fsGate(true); lockDraw();
    };
    document.addEventListener('fullscreenchange', onFs); onLeave(() => document.removeEventListener('fullscreenchange', onFs));
    if (!document.fullscreenElement) ws.requestFullscreen().catch(() => { why.fs = fsGate(false); lockDraw(); });
  }

  // ---------- problem rendering ----------
  const drawTabs = () => {
    $('#ptabs').innerHTML = problems.map((p) => `<button class="ptab ${p.id === cur.id ? 'on' : ''}" data-p="${p.id}" title="${esc(p.title)}">${esc(p.label)}${p.solved ? '<span class="ok">' + ico('check') + '</span>' : p.best > 0 ? `<span class="part">${p.best}</span>` : ''}
      <span style="font-weight:500;max-width:150px;overflow:hidden;text-overflow:ellipsis">${problems.length <= 6 ? esc(p.title) : ''}</span></button>`).join('');
    $$('#ptabs [data-p]').forEach((b) => b.onclick = () => select(problems.find((p) => String(p.id) === b.dataset.p)));
  };
  const drawProblem = () => {
    const p = cur;
    $('#prob').innerHTML = `<div class="row between" style="margin-bottom:6px"><span class="small muted">${contest ? `Problem ${esc(p.label)}` : esc(p.category || '')}</span>
        ${p.solved ? '<span class="badge green">' + ico('check') + ' Solved</span>' : p.best > 0 ? `<span class="badge amber">Best ${p.best}/${p.marks}</span>` : ''}</div>
      <h2 style="font-size:21px">${esc(p.title)}</h2>
      ${p.qtype === 'debug' ? `<div class="notice warn small" style="margin-bottom:12px">${ico('alert', 14)} <b>Fix the bug.</b> The editor starts with a program that has a mistake. Find it and fix it.</div>`
        : p.qtype === 'complete' ? `<div class="notice info small" style="margin-bottom:12px">${ico('pencil', 14)} <b>Complete the code.</b> Fill in the missing part marked in the starter code.</div>`
        : p.qtype === 'output' || p.qtype === 'trace' ? `<div class="notice info small" style="margin-bottom:12px">${ico('eye', 14)} <b>${p.qtype === 'output' ? 'Predict the output.' : 'Trace the code.'}</b> Do not run anything: read the code, work it out, and type your answer in the box on the right exactly as it would appear. Then press Submit.</div>`
        : p.qtype === 'prompt' ? `<div class="notice info small" style="margin-bottom:12px">${ico('sparkles', 14)} <b>Prompt challenge.</b> Write instructions for the AI, not code. Each test input is sent to the AI with your prompt, and its reply must exactly match the expected output. Use Run to try it on your own input.</div>` : ''}
      <div class="row wrap small muted" style="gap:14px;margin-bottom:14px">${p.marks ? `<span>${ico('medal')} ${p.marks} marks</span>` : ''}${p.difficulty ? `<span>${esc(p.difficulty)}</span>` : ''}<span>${ico('clock')} ${p.time_limit_ms / 1000}s</span><span>${ico('cpu')} ${p.memory_mb} MB</span></div>
      <div class="prose">${md(p.statement)}${p.input_format ? `<h4>Input format</h4>${md(p.input_format)}` : ''}${p.output_format ? `<h4>Output format</h4>${md(p.output_format)}` : ''}${p.constraints ? `<h4>Constraints</h4>${md(p.constraints)}` : ''}</div>
      ${p.samples.length ? `<h4 class="exh">${ico('layers', 14)} Examples</h4>` : ''}${p.samples.map((s, i) => `<div class="sample"><div class="small" style="font-weight:650;margin-bottom:8px">Example ${i + 1}</div><div class="io"><div><div class="lbl">Input <button class="btn xs ghost" data-cp="${i}">Copy</button></div><pre>${esc(s.input)}</pre></div><div><div class="lbl">Output</div><pre>${esc(s.output)}</pre></div></div>
        ${s.explanation ? `<div class="prose small" style="margin-top:8px"><b>Explanation:</b> ${md(s.explanation)}</div>` : ''}</div>`).join('')}`;
    $$('[data-cp]').forEach((b) => b.onclick = () => { const t = cur.samples[b.dataset.cp].input; internalClip = t; navigator.clipboard?.writeText(t); toast('Sample input copied', 'ok'); });
  };
  const drawLang = () => {
    $('#lang').innerHTML = cur.languages.map((l) => `<option value="${l}" ${l === lang ? 'selected' : ''}>${esc(S.info.languages[l]?.name || l)}</option>`).join('');
    editor.setModel(getModel(cur, lang));
  };
  const select = (p) => {
    cur = p; lang = langOf(p);
    drawTabs(); drawProblem(); drawLang();
    if (!p.samples.length) { showTab('custom'); toast('This problem has no examples: try your own input in the Custom input tab.', 'info'); }
    else { showTab('res'); $('#out').innerHTML = `<div class="muted small">Run your code on the examples, then Submit to be judged on hidden tests.</div>`; }
    track('problem_open', null, p.id);
    history.replaceState(null, '', contest ? `#/arena/${compId}?q=${p.id}` : `#/practice/${p.id}`);
  };
  $('#lang').onchange = (e) => { lang = e.target.value; pref.lang = lang; savePref(); localStorage.setItem(`astra:lang:${contest ? compId : 'p'}:${cur.id}`, lang); editor.setModel(getModel(cur, lang)); track('lang_change', { language: lang }); };
  $('#font').onchange = (e) => { pref.font = Number(e.target.value); savePref(); editor.updateOptions({ fontSize: pref.font }); };
  $('#themeBtn').onclick = () => { pref.theme = pref.theme === 'dark' ? 'light' : 'dark'; savePref(); ws.dataset.theme = pref.theme; monaco.editor.setTheme(pref.theme === 'dark' ? 'astra-dark' : 'astra-light'); $('#themeBtn').innerHTML = ico(pref.theme === 'dark' ? 'sun' : 'moon'); };
  $('#resetBtn').onclick = async () => { if (await confirmBox('Replace your code with the starter code? Your current code for this problem and language will be lost.', { ok: 'Reset code', danger: true })) { editor.setValue(cur.starter?.[lang] || S.info.languages[lang]?.template || ''); track('reset_code', { language: lang }); } };

  // ---------- resizers ----------
  const drag = (el, onMove, done) => el.addEventListener('mousedown', (e) => {
    e.preventDefault(); el.classList.add('drag');
    const mv = (ev) => onMove(ev), up = () => { el.classList.remove('drag'); document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); done(); };
    document.addEventListener('mousemove', mv); document.addEventListener('mouseup', up);
  });
  drag($('#vg'), (e) => { const w = Math.min(75, Math.max(22, e.clientX / innerWidth * 100)); $('#wsmain').style.setProperty('--left', w + '%'); pref.left = w + '%'; }, savePref);
  drag($('#hg'), (e) => { const r = $('#wsright').getBoundingClientRect(); const h = Math.min(r.height - 120, Math.max(90, r.bottom - e.clientY)); $('#wsright').style.setProperty('--bottom', h + 'px'); pref.bottom = h + 'px'; }, savePref);

  // ---------- bottom panel ----------
  let customInput = '';
  const showTab = (t) => {
    $$('#btabs button').forEach((b) => b.classList.toggle('on', b.dataset.t === t));
    if (t === 'custom') {
      $('#out').innerHTML = `<div class="stack tight"><label class="check"><input type="checkbox" id="useCustom" ${customInput ? 'checked' : ''}> Use this input when I press Run</label>
        <textarea class="textarea mono" id="cin" style="min-height:110px" placeholder="Type input exactly as the program reads it">${esc(customInput)}</textarea></div>`;
      $('#cin').oninput = (e) => { customInput = e.target.value; $('#useCustom').checked = !!customInput; };
      $('#useCustom').onchange = (e) => { if (!e.target.checked) { customInput = ''; $('#cin').value = ''; } };
    }
    if (t === 'subs') loadSubs();
    if (t === 'ai') loadTools();
  };
  let toolCache = null;
  const loadTools = async () => {
    const out = $('#out');
    out.innerHTML = skeleton(2);
    toolCache = await GET(`/api/student/tools?question_id=${cur.id}${contest ? `&competition_id=${compId}` : ''}`).catch(() => []);
    if (!toolCache.length) { out.innerHTML = empty('No assistant tools here', 'Organisers can switch tools on for practice or a competition.'); return; }
    out.innerHTML = `<div class="tools">${toolCache.map((t) => `<form class="toolcard" data-tool="${t.id}"><div class="row between"><b class="row" style="gap:8px"><span class="lsi sm">${ico(t.spec.icon || 'sparkles', 16)}</span>${esc(t.spec.name)}</b><span class="tiny muted">${t.uses_left} left</span></div>
      <div class="small muted">${esc(t.spec.description || '')}</div>${(t.spec.inputs || []).map((x) => x.type === 'textarea' ? `<textarea class="textarea" name="${esc(x.key)}" rows="2" placeholder="${esc(x.placeholder || x.label)}"></textarea>`
        : x.type === 'select' ? `<select class="select" name="${esc(x.key)}">${x.options.map((o) => `<option>${esc(o)}</option>`).join('')}</select>` : `<input class="input" name="${esc(x.key)}" placeholder="${esc(x.placeholder || x.label)}">`).join('')}
      <button class="btn primary sm" ${t.uses_left ? '' : 'disabled'}>${ico('sparkles', 14)} ${esc(t.spec.button_label || 'Ask')}</button><div class="toolout prose small"></div></form>`).join('')}</div>`;
    $$('[data-tool]', out).forEach((f) => f.onsubmit = (e) => {
      e.preventDefault();
      busy($('button', f), async () => {
        const r = await POST(`/api/student/tools/${f.dataset.tool}/run`, { competition_id: contest ? compId : null, question_id: cur.id, code: editor.getValue(), language: lang, inputs: formData(f) });
        $('.toolout', f).innerHTML = md(r.output);
        $('.tiny.muted', f).textContent = `${r.uses_left} left`;
        if (!r.uses_left) $('button', f).disabled = true;
      });
    });
  };
  GET(`/api/student/tools?question_id=0${contest ? `&competition_id=${compId}` : ''}`).then((t) => { if (t.length && $('#aiTab')) $('#aiTab').classList.remove('hidden'); }).catch(() => {});
  $$('#btabs button').forEach((b) => b.onclick = () => showTab(b.dataset.t));
  const loadSubs = async () => {
    $('#out').innerHTML = skeleton(2);
    const rows = await GET(`/api/student/submissions?question_id=${cur.id}${contest ? `&competition_id=${compId}` : ''}`).catch(fail);
    if (!rows) return;
    $('#out').innerHTML = rows.length ? `<table class="table"><thead><tr><th>#</th><th>Verdict</th><th>Language</th><th class="num">Score</th><th class="num">Tests</th><th class="num">Time</th><th>When</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="click" data-s="${r.id}"><td class="mono small">${r.id}</td><td>${vbadge(r.verdict, r.status)}</td><td>${langName(r.language)}</td><td class="num">${r.kind === 'practice' ? '—' : r.score}</td><td class="num">${r.passed != null ? `${r.passed}/${r.total}` : '—'}</td><td class="num">${r.time_ms != null ? r.time_ms + ' ms' : '—'}</td><td class="small muted">${fmtClock(r.created_at)}</td></tr>`).join('')}</tbody></table>` : empty('No submissions for this problem yet');
    $$('[data-s]').forEach((tr) => tr.onclick = async () => {
      const s = await GET(`/api/student/submissions/${tr.dataset.s}`);
      modal({ title: `Submission #${s.id}`, size: 'wide', body: `<div class="row" style="margin-bottom:10px">${vbadge(s.verdict, s.status)}<span class="small muted">${langName(s.language)} · ${fmtTime(s.created_at)}</span></div>${resultHTML(s)}<div class="lbl small muted" style="margin:10px 0 4px">Code</div><pre style="max-height:340px">${esc(s.code)}</pre>`,
        actions: [{ label: 'Load into editor', onClick: () => { editor.setValue(s.code); } }, { label: 'Close', kind: 'primary' }] });
    });
  };
  const resultHTML = (s) => {
    if (s.status === 'queued' || s.status === 'running') return `<div class="row"><span class="spin"></span><span>${s.status === 'running' ? 'Judging your code…' : `In queue${s.queue_ahead ? ` (${s.queue_ahead} ahead)` : ''}…`}</span></div>`;
    let h = `<div class="row wrap" style="gap:16px;margin-bottom:10px"><span class="verdict ${esc(s.verdict)}">${esc(s.verdict_name)}</span>
      ${s.kind === 'submit' ? `<span class="small">Score <b>${s.score}</b></span>` : ''}${s.passed != null && s.kind !== 'run' ? `<span class="small">${s.passed}/${s.total} tests passed</span>` : ''}
      ${s.time_ms != null ? `<span class="small muted">${ico('clock')} ${s.time_ms} ms</span>` : ''}${s.memory_kb ? `<span class="small muted">${ico('cpu')} ${(s.memory_kb / 1024).toFixed(1)} MB</span>` : ''}</div>`;
    if (s.compile_output) h += `<div class="lbl small muted">${s.verdict === 'CE' ? 'Compiler output' : 'Details'}</div><pre style="color:var(--red);max-height:240px">${esc(s.compile_output)}</pre>`;
    if (s.tests?.length) h += `<div class="tc-grid" style="margin-bottom:10px">${s.tests.map((t) => `<span class="tc ${['AC', 'OK'].includes(t.verdict) ? t.verdict : 'bad'}" title="Test ${t.n}${t.sample ? ' (sample)' : ''}: ${esc(VERDICT_NAME[t.verdict] || t.verdict)}">${['AC', 'OK'].includes(t.verdict) ? '' + ico('check') + '' : esc(t.verdict)}</span>`).join('')}</div>`;
    if (s.failed_sample) h += `<div class="small" style="font-weight:650;margin:6px 0">Failed on a sample case</div><div class="io"><div><div class="lbl">Input</div><pre>${esc(s.failed_sample.input)}</pre></div><div><div class="lbl">Expected</div><pre>${esc(s.failed_sample.expected)}</pre></div></div>
      <div style="margin-top:8px"><div class="lbl small muted">Your output</div><pre>${esc(s.failed_sample.output) || '<span class="faint">(no output)</span>'}</pre>${s.failed_sample.stderr ? `<pre style="color:var(--red)">${esc(s.failed_sample.stderr)}</pre>` : ''}</div>`;
    if (s.results) h += s.results.map((r, i) => `<div class="sample"><div class="row between" style="margin-bottom:8px"><b class="small">${r.visibility === 'custom' ? 'Custom input' : `Sample ${i + 1}`}</b>${vbadge(r.verdict, 'done')}</div>
      <div class="io" style="grid-template-columns:repeat(${r.expected != null ? 3 : 2},minmax(0,1fr))"><div><div class="lbl">Input</div><pre>${esc(r.input)}</pre></div>${r.expected != null ? `<div><div class="lbl">Expected</div><pre>${esc(r.expected)}</pre></div>` : ''}
      <div><div class="lbl">Your output</div><pre>${esc(r.output) || '<span class="faint">(no output)</span>'}</pre></div></div>${r.stderr ? `<pre style="color:var(--red);margin-top:8px">${esc(r.stderr)}</pre>` : ''}</div>`).join('');
    if (s.verdict === 'IE') h += `<div class="notice warn">This is a judge problem, not your fault. It does not count against you.</div>`;
    return h;
  };
  let polling = false;
  const runOrSubmit = async (kind) => {
    if (polling) return toast('Please wait for the current run to finish.', 'info');
    if (ended && contest) return toast('Submissions are closed.', 'err');
    const btn = kind === 'run' ? $('#runBtn') : $('#subBtn');
    const code = editor.getValue();
    if (!code.trim()) return toast('Your code is empty.', 'err');
    showTab('res');
    polling = true; $('#runBtn').disabled = $('#subBtn').disabled = true;
    const old = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>';
    try {
      const body = { competition_id: contest ? compId : null, question_id: cur.id, language: lang, code, kind };
      if (kind === 'run' && (customInput || !cur.samples.length)) body.custom_input = customInput || '\n';
      const r = await POST('/api/student/submissions', body);
      $('#out').innerHTML = resultHTML({ status: 'queued', queue_ahead: r.queue_ahead });
      let s, waited = 0;
      while (true) {
        await new Promise((res) => setTimeout(res, waited < 5000 ? 700 : 1500)); waited += 700;
        s = await GET(`/api/student/submissions/${r.id}`);
        if (!$('#out')) return;
        $('#out').innerHTML = resultHTML(s);
        if (s.status === 'done' || s.status === 'error') break;
        if (waited > 180000) { $('#out').insertAdjacentHTML('beforeend', '<div class="notice warn">Still judging. Check the Submissions tab in a moment.</div>'); break; }
      }
      if (kind === 'submit' && s.status === 'done') {
        toast(`${s.verdict_name}${s.kind === 'submit' ? ` · ${s.score} marks` : ''}`, s.verdict === 'AC' ? 'ok' : s.verdict === 'IE' ? 'info' : 'err');
        if (s.verdict === 'AC') { const first = !cur.solved; cur.solved = true; if (first) confetti(1600); }
        if (contest) await refresh(); else drawTabs();
        const nxt = nextProblem();
        if (s.verdict === 'AC') {
          if (nxt) {
            $('#out').insertAdjacentHTML('afterbegin', `<div class="notice ok row between" style="margin-bottom:10px"><span>${ico('check2', 16)} Solved! Moving to <b>${esc(nxt.label)}. ${esc(nxt.title)}</b>…</span><button class="btn sm primary" id="goNext">Go now ${ico('right', 14)}</button></div>`);
            const t = setTimeout(() => { if ($('#goNext')) select(nxt); }, 2500);
            $('#goNext').onclick = () => { clearTimeout(t); select(nxt); };
          } else if (contest) {
            $('#out').insertAdjacentHTML('afterbegin', `<div class="notice ok row between" style="margin-bottom:10px"><span>${ico('trophy', 16)} All problems solved. You can finish your test now.</span><button class="btn sm success" id="finNow">${ico('flag', 14)} Finish test</button></div>`);
            $('#finNow').onclick = () => finishTest();
          }
        } else if (nxt) {
          $('#out').insertAdjacentHTML('beforeend', `<div class="row" style="margin-top:10px"><button class="btn sm" id="skipNext">Skip to ${esc(nxt.label)}. ${esc(nxt.title)} ${ico('right', 14)}</button></div>`);
          $('#skipNext').onclick = () => select(nxt);
        }
      }
    } catch (e) { fail(e); $('#out').innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
    finally { polling = false; btn.innerHTML = old; $('#runBtn').disabled = false; $('#subBtn').disabled = ended && contest; }
  };
  $('#runBtn').onclick = () => runOrSubmit('run');
  $('#subBtn').onclick = () => runOrSubmit('submit');
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => runOrSubmit('run'));
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Enter, () => runOrSubmit('submit'));

  // ---------- next problem / final submission ----------
  const nextProblem = () => { const i = problems.indexOf(cur); return [...problems.slice(i + 1), ...problems.slice(0, i)].find((p) => !p.solved) || null; };
  const finishTest = async () => {
    const solved = problems.filter((p) => p.solved);
    const partial = problems.filter((p) => !p.solved && p.tries > 0);
    const untouched = problems.filter((p) => !p.solved && !p.tries);
    const ok = await new Promise((res) => modal({ title: 'Final submission', body: `${solved.length === problems.length ? `<div class="notice ok">${ico('check2', 15)} You solved all ${problems.length} problems. Great work!</div>`
      : `<div class="notice warn">${ico('alert', 15)} You still have <b>${problems.length - solved.length}</b> of ${problems.length} problem(s) left. Once you finish you can't submit again.</div>`}
      <div class="finlist">${problems.map((p) => `<div class="row between"><span><b>${esc(p.label)}.</b> ${esc(p.title)}</span>${p.solved ? `<span class="badge green">${ico('check', 12)} solved</span>` : p.tries ? `<span class="badge amber">attempted · ${p.best} marks</span>` : '<span class="badge red">not attempted</span>'}</div>`).join('')}</div>
      ${ended ? '' : `<p class="small muted" style="margin:10px 0 0">Time left: <b class="mono">${$('#timer')?.textContent || ''}</b>. ${untouched.length ? `${untouched.length} problem(s) not even attempted.` : partial.length ? 'Some problems are only partly solved.' : ''}</p>`}`,
      actions: [{ label: 'Keep working', onClick: () => res(false) }, { label: 'Finish test', kind: solved.length === problems.length ? 'primary' : 'danger solid', onClick: () => res(true) }] }));
    if (!ok) return;
    try {
      const r = await POST(`/api/student/competitions/${compId}/finish`);
      finished = true; ended = true; ws.dataset.fin = Date.now();
      $('#subBtn').disabled = true; $('#finishBtn')?.remove();
      showFinished(r);
      document.fullscreenElement && document.exitFullscreen?.();
    } catch (e) { fail(e); }
  };
  const showFinished = (r) => overlay(`<div class="emptyi" style="background:var(--green-soft);color:var(--green)">${ico('check2', 28)}</div><h3>Test submitted</h3>
    ${r && !r.hidden ? `<p class="muted">Score <b>${r.score}</b> / ${r.max} · solved ${r.solved} · current rank ${r.rank ?? '—'} of ${r.of}</p>` : `<p class="muted">Your answers are final.${r ? ` You solved ${r.solved}.` : ''} Results appear on your profile when the organisers publish them.</p>`}
    <div class="row" style="justify-content:center"><a class="btn" href="#/leaderboard/${compId}">Leaderboard</a><a class="btn primary" href="#/profile">Back to dashboard</a></div>`);
  let finished = false;
  if (contest) $('#finishBtn').onclick = finishTest;

  // ---------- live contest state ----------
  let endAt = null, paused = false, seenAnn = Number(localStorage.getItem(`astra:ann:${compId}`) || 0), anns = [];
  const overlay = (html) => { let o = $('#wsmsg'); if (!html) return o?.remove(); if (!o) { o = document.createElement('div'); o.id = 'wsmsg'; o.className = 'wsmsg'; $('#wsright').appendChild(o); } o.innerHTML = `<div class="card pad center" style="max-width:380px">${html}</div>`; };
  const applyState = (d) => {
    const c = d.competition;
    paused = c.status === 'paused';
    ended = !['live', 'paused'].includes(c.status) || c.remaining_sec <= 0;
    endAt = c.remaining_sec != null ? Date.now() / 1000 + c.remaining_sec : null;
    $('#cstatus').innerHTML = badge(c.display_status);
    $('#subBtn').disabled = ended || paused; $('#runBtn').disabled = ended;
    if (d.finished_at) { finished = true; ended = true; $('#subBtn').disabled = true; $('#finishBtn')?.remove(); if (!$('#wsmsg')) showFinished(null); return; }
    if (finished && !d.finished_at && Date.now() - (ws.dataset.fin || 0) > 15000) return location.reload();  // the invigilator reopened this test
    if (d.disqualified) { ended = true; overlay('<h3>Disqualified</h3><p class="muted">The organisers have disqualified this account from the competition.</p>'); }
    else if (ended) overlay(`<h3>Competition ended</h3><p class="muted">Submissions are closed. Thank you for participating!</p><a class="btn primary" href="#/leaderboard/${compId}">View results</a>`);
    else if (paused) overlay('<h3>' + ico('pause') + ' Paused by organisers</h3><p class="muted">The timer is frozen. You can keep reading and writing code; submissions resume when the competition resumes.</p><button class="btn" onclick="this.closest(\'.wsmsg\').remove()">Keep editing</button>');
    else overlay(null);
    anns = d.announcements;
    const fresh = anns.filter((a) => a.id > seenAnn);
    if (fresh.length) { fresh.reverse().forEach((a) => toast(`${a.title}${a.body ? ' — ' + a.body : ''}`, 'info')); seenAnn = Math.max(...anns.map((a) => a.id)); localStorage.setItem(`astra:ann:${compId}`, seenAnn); }
    $('#annDot').textContent = anns.length ? ` ${anns.length}` : '';
  };
  const refresh = async () => {
    try {
      const d = await GET(`/api/student/competitions/${compId}/arena`);
      d.problems.forEach((np) => { const op = problems.find((p) => p.id === np.id); if (op) Object.assign(op, { best: np.best, solved: np.solved, tries: np.tries }); });
      applyState(d); drawTabs();
      const pos = $('#prob').scrollTop; drawProblem(); $('#prob').scrollTop = pos;
    } catch (e) { /* transient network error: keep going */ }
  };
  if (contest) {
    applyState(data);
    const tick = setInterval(() => {
      const t = $('#timer'); if (!t) return;
      if (endAt == null) { t.textContent = '--:--'; return; }
      const left = paused ? Math.max(0, endAt - Date.now() / 1000) : endAt - Date.now() / 1000;
      if (paused) { t.innerHTML = ico('pause', 14) + ' ' + fmtDur(left); t.className = 'timer'; return; }
      t.textContent = fmtDur(left);
      t.className = 'timer' + (left < 60 ? ' crit' : left < 600 ? ' warn' : '');
      if (left <= 0 && !ended) refresh();
    }, 500);
    const rs = setInterval(refresh, 15000);
    onLeave(() => { clearInterval(tick); clearInterval(rs); });
    $('#annBtn').onclick = () => modal({ title: 'Announcements', body: anns.length ? anns.map((a) => `<div style="padding:8px 0;border-bottom:1px solid var(--border)"><div class="row between"><b>${esc(a.title)}</b><span class="tiny muted">${fmtClock(a.created_at)}</span></div>${a.body ? `<div class="small muted">${esc(a.body)}</div>` : ''}</div>`).join('') : empty('No announcements yet') });
    if (comp.paste_policy !== 'allow') toast(comp.paste_policy === 'block_external' ? 'Fair play: pasting from outside the editor is disabled and activity is recorded.' : 'Fair play: workspace activity (paste, tab switches) is recorded.', 'info');
  }
  select(cur);
}

boot();
