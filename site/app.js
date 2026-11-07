/* Ham & Negroni: the whole app in one file. Vanilla JS, no build step.
   Backend: Supabase (auth, postgres with RLS, storage, realtime) via the vendored supabase-js UMD build.
   If config.js has no url/key (or ?demo is in the URL) an in-memory demo backend is used instead. */
(function () {
  'use strict';

  const CFG = window.HN_CONFIG || {};
  const params = new URLSearchParams(location.search);
  const DEMO = params.has('demo') || !CFG.url || !CFG.key || !window.supabase;

  // ------------------------------------------------------------------ helpers
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); }));

  function timeAgo(iso) {
    const t = new Date(iso).getTime();
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 45) return 'just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) { const h = Math.round(s / 3600); return h + (h === 1 ? ' hour ago' : ' hours ago'); }
    const d = Math.round(s / 86400);
    if (d === 1) return 'yesterday';
    if (d < 7) return d + ' days ago';
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function clock(iso) {
    return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function initials(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return parts.slice(0, 2).map((p) => Array.from(p)[0].toUpperCase()).join('');
  }
  function avatar(p, cls) {
    cls = cls || '';
    if (p && p.avatar_url) return '<img class="avatar ' + cls + '" src="' + esc(p.avatar_url) + '" alt="" loading="lazy">';
    return '<span class="avatar ' + cls + '" aria-hidden="true">' + esc(initials(p && p.display_name)) + '</span>';
  }
  function toast(msg, ms) {
    const el = $('#toast');
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, ms || 2600);
  }
  const nm = (p) => esc(p.display_name);
  const plainName = (p) => p.display_name;
  function friendlyError(err) {
    const m = (err && err.message) || String(err || 'Something went wrong');
    if (/already registered|already exists|already been registered/i.test(m)) return 'That email is already registered. Sign in instead.';
    if (/invalid login credentials|invalid_credentials/i.test(m)) return 'Wrong email or password.';
    if (/at least 6|password should be/i.test(m)) return 'Password needs at least 6 characters.';
    if (/rate limit/i.test(m)) return 'Too many attempts. Wait a minute and try again.';
    if (/email not confirmed/i.test(m)) return 'Your email still needs confirming. Check your inbox.';
    if (/invalid.*email|unable to validate email/i.test(m)) return 'That email address does not look right.';
    if (/Failed to fetch|NetworkError|network/i.test(m)) return 'Could not reach the server. Check your connection and try again.';
    return m;
  }
  function resizeImage(file, size) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const s = Math.min(img.naturalWidth, img.naturalHeight);
        const sx = (img.naturalWidth - s) / 2, sy = (img.naturalHeight - s) / 2;
        const c = document.createElement('canvas');
        c.width = size; c.height = size;
        c.getContext('2d').drawImage(img, sx, sy, s, s, 0, 0, size, size);
        URL.revokeObjectURL(url);
        c.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not read that image.'))), 'image/jpeg', 0.86);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read that image.')); };
      img.src = url;
    });
  }

  // ------------------------------------------------------------------ state
  const state = {
    user: null,              // { id, email }
    profiles: new Map(),     // id -> profile
    current: [],             // current_verdicts rows
    feed: [],                // verdict rows newest first
    msgs: [],                // my messages newest first
    view: 'people',
    peer: null,
    seen: {},
    realtime: false,
  };
  try { state.seen = JSON.parse(localStorage.getItem('hn.seen') || '{}'); } catch (e) { state.seen = {}; }
  const me = () => state.profiles.get(state.user && state.user.id) || null;

  // ------------------------------------------------------------------ backend: supabase
  function supabaseApi() {
    const client = window.supabase.createClient(CFG.url, CFG.key, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false } });
    let channel = null;
    const unwrap = (r) => { if (r.error) throw r.error; return r.data; };
    return {
      kind: 'supabase',
      async session() { return unwrap(await client.auth.getSession()).session; },
      onAuth(cb) { client.auth.onAuthStateChange((_e, s) => cb(s)); },
      async signUp(f) {
        const data = unwrap(await client.auth.signUp({ email: f.email, password: f.password, options: { data: { display_name: f.display_name } } }));
        return { session: data.session, user: data.user };
      },
      async signIn(f) { return unwrap(await client.auth.signInWithPassword({ email: f.email, password: f.password })).session; },
      async signOut() { if (channel) { client.removeChannel(channel); channel = null; } await client.auth.signOut(); },
      async profiles() { return unwrap(await client.from('profiles').select('*').order('created_at')); },
      async ensureProfile(user) {
        const rows = unwrap(await client.from('profiles').select('*').eq('id', user.id));
        if (rows.length) return rows[0];
        const md = (user.user_metadata || {});
        return unwrap(await client.from('profiles').insert({ id: user.id, display_name: md.display_name || (user.email || 'someone').split('@')[0] }).select().single());
      },
      async currentVerdicts() { return unwrap(await client.from('current_verdicts').select('*')); },
      async feed() { return unwrap(await client.from('verdicts').select('*').order('created_at', { ascending: false }).limit(150)); },
      async giveVerdict(target, kind) { return unwrap(await client.from('verdicts').insert({ actor: state.user.id, target, kind }).select().single()); },
      async myMessages() {
        const id = state.user.id;
        return unwrap(await client.from('messages').select('*').or('sender.eq.' + id + ',recipient.eq.' + id).order('created_at', { ascending: false }).limit(600));
      },
      async sendMessage(recipient, body) { return unwrap(await client.from('messages').insert({ sender: state.user.id, recipient, body }).select().single()); },
      async updateProfile(fields) { return unwrap(await client.from('profiles').update(fields).eq('id', state.user.id).select().single()); },
      async uploadAvatar(blob) {
        const path = state.user.id + '/avatar.jpg';
        unwrap(await client.storage.from('avatars').upload(path, blob, { upsert: true, contentType: 'image/jpeg', cacheControl: '60' }));
        const url = client.storage.from('avatars').getPublicUrl(path).data.publicUrl + '?v=' + Date.now();
        return unwrap(await client.from('profiles').update({ avatar_url: url }).eq('id', state.user.id).select().single());
      },
      subscribe(h) {
        if (channel) client.removeChannel(channel);
        client.auth.getSession().then((r) => { if (r.data && r.data.session) client.realtime.setAuth(r.data.session.access_token); }).catch(() => {});
        channel = client.channel('bar')
          .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'verdicts' }, (p) => h.verdict(p.new))
          .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'messages' }, (p) => h.message(p.new))
          .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'profiles' }, (p) => h.profile(p.new))
          .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'profiles' }, (p) => h.profile(p.new))
          .subscribe((status) => { state.realtime = status === 'SUBSCRIBED'; });
      },
    };
  }

  // ------------------------------------------------------------------ backend: demo (in memory)
  function demoApi() {
    const now = Date.now();
    const ago = (m) => new Date(now - m * 60000).toISOString();
    const P = (id, name, bio, min) => ({ id, display_name: name, bio, avatar_url: null, created_at: ago(min) });
    const ids = { carol: 'demo-carol', anderson: 'demo-anderson', thomas: 'demo-thomas', brian: 'demo-brian', timothy: 'demo-timothy' };
    const profiles = [
      P(ids.carol, 'Carol', 'Co-founder. Chooses the restaurant.', 900),
      P(ids.anderson, 'Anderson', 'Co-founder. Makes the Negronis.', 880),
      P(ids.thomas, 'Thomas', 'Co-founder. Good at first impressions.', 860),
      P(ids.brian, 'Brian', 'Co-founder. Keeps the feed running.', 840),
      P(ids.timothy, 'Timothy', 'Co-founder. Knows the last train times.', 820),
    ];
    let seq = 1;
    const verdicts = [
      { id: seq++, actor: ids.carol, target: ids.thomas, kind: 'round', created_at: ago(44) },
      { id: seq++, actor: ids.anderson, target: ids.brian, kind: 'night', created_at: ago(31) },
      { id: seq++, actor: ids.timothy, target: ids.carol, kind: 'round', created_at: ago(22) },
      { id: seq++, actor: ids.thomas, target: ids.carol, kind: 'round', created_at: ago(9) },
      { id: seq++, actor: ids.brian, target: ids.anderson, kind: 'night', created_at: ago(3) },
    ];
    let messages = [];
    let user = null;
    let authCb = null;
    let handlers = null;
    const tick = () => new Date().toISOString();
    return {
      kind: 'demo',
      async session() { return user ? { user } : null; },
      onAuth(cb) { authCb = cb; },
      async signUp(f) {
        user = { id: 'demo-' + uuid().slice(0, 8), email: f.email, user_metadata: { display_name: f.display_name } };
        profiles.push(P(user.id, f.display_name || 'You', '', 0));
        messages = seedMessages(user.id);
        return { session: { user }, user };
      },
      async signIn(f) {
        user = { id: 'demo-you', email: f.email, user_metadata: {} };
        if (!profiles.find((p) => p.id === user.id)) profiles.push(P(user.id, (f.email || 'You').split('@')[0], '', 0));
        messages = seedMessages(user.id);
        return { user };
      },
      async signOut() { user = null; if (authCb) authCb(null); },
      async profiles() { return profiles.slice(); },
      async ensureProfile(u) { return profiles.find((p) => p.id === u.id); },
      async currentVerdicts() {
        const seen = new Set(); const out = [];
        for (const v of verdicts.slice().sort((a, b) => b.created_at.localeCompare(a.created_at))) { const k = v.actor + '|' + v.target; if (!seen.has(k)) { seen.add(k); out.push(v); } }
        return out;
      },
      async feed() { return verdicts.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)); },
      async giveVerdict(target, kind) { const v = { id: seq++, actor: user.id, target, kind, created_at: tick() }; verdicts.push(v); setTimeout(() => handlers && handlers.verdict(v), 50); return v; },
      async myMessages() { return messages.filter((m) => m.sender === user.id || m.recipient === user.id).sort((a, b) => b.created_at.localeCompare(a.created_at)); },
      async sendMessage(recipient, body) {
        const m = { id: seq++, sender: user.id, recipient, body, created_at: tick() }; messages.push(m);
        setTimeout(() => { const r = { id: seq++, sender: recipient, recipient: user.id, body: 'Hey, nice to meet you.', created_at: tick() }; messages.push(r); handlers && handlers.message(r); }, 1800);
        return m;
      },
      async updateProfile(fields) { const p = profiles.find((x) => x.id === user.id); Object.assign(p, fields); return p; },
      async uploadAvatar(blob) { const p = profiles.find((x) => x.id === user.id); p.avatar_url = URL.createObjectURL(blob); return p; },
      subscribe(h) { handlers = h; state.realtime = true; },
    };
    function seedMessages(uid) {
      return [
        { id: seq++, sender: ids.carol, recipient: uid, body: 'Hey, welcome to the site.', created_at: ago(40) },
        { id: seq++, sender: uid, recipient: ids.carol, body: 'Thanks, glad to be here.', created_at: ago(38) },
        { id: seq++, sender: ids.carol, recipient: uid, body: 'Say hi to the others too.', created_at: ago(37) },
        { id: seq++, sender: ids.timothy, recipient: uid, body: 'Last train is 23:58, just so you know.', created_at: ago(12) },
      ];
    }
  }

  const api = DEMO ? demoApi() : supabaseApi();

  // ------------------------------------------------------------------ routing
  function route() {
    const h = location.hash.replace(/^#/, '');
    if (!state.user) {
      showLanding();
      if (h === 'join' || h === 'signin') openAuth(h);
      return;
    }
    const [view, arg] = h.split('/');
    state.view = ['people', 'feed', 'chats', 'me'].includes(view) ? view : 'people';
    state.peer = state.view === 'chats' && arg ? arg : null;
    showApp();
    render();
  }
  window.addEventListener('hashchange', route);
  function go(hash) { if (location.hash === '#' + hash) route(); else location.hash = hash; }

  function showLanding() {
    $('#landing').hidden = false;
    $('#app').hidden = true;
    document.body.style.background = '';
  }
  function showApp() {
    $('#landing').hidden = true;
    $('#app').hidden = false;
    $('[data-demo-notice]').hidden = !DEMO;
    closeAuth();
  }

  // ------------------------------------------------------------------ auth sheet
  function openAuth(mode) {
    const sheet = $('#auth-sheet');
    sheet.hidden = false;
    setAuthMode(mode || 'join');
    setTimeout(() => { const f = $(mode === 'signin' ? '#signin-email' : '#join-name'); if (f) f.focus(); }, 50);
  }
  function closeAuth() {
    $('#auth-sheet').hidden = true;
    if (!state.user && /^(#join|#signin)$/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search);
  }
  function setAuthMode(mode) {
    $$('.auth-switch [data-mode]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.mode === mode)));
    $('#join-form').hidden = mode !== 'join';
    $('#signin-form').hidden = mode !== 'signin';
    $('#auth-title').textContent = mode === 'join' ? 'Create an account' : 'Sign in';
  }
  $$('[data-auth]').forEach((b) => b.addEventListener('click', () => openAuth(b.dataset.auth)));
  $$('[data-close-auth]').forEach((b) => b.addEventListener('click', closeAuth));
  $$('.auth-switch [data-mode]').forEach((b) => b.addEventListener('click', () => setAuthMode(b.dataset.mode)));
  $('#auth-sheet').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeAuth(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeAuth(); closePerson(); } });

  function formError(form, msg) { const el = $('[data-error]', form); el.textContent = msg || ''; el.hidden = !msg; }
  function busy(form, on) { const b = $('button[type=submit]', form); b.disabled = on; b.dataset.label = b.dataset.label || b.textContent; b.textContent = on ? 'Please wait…' : b.dataset.label; }

  $('#join-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = Object.fromEntries(new FormData(form));
    f.display_name = (f.display_name || '').trim();
    f.email = (f.email || '').trim();
    if (!f.display_name) return formError(form, 'Enter your name.');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.email)) return formError(form, 'That email address does not look right.');
    if ((f.password || '').length < 6) return formError(form, 'Password needs at least 6 characters.');
    formError(form, '');
    busy(form, true);
    try {
      const r = await api.signUp(f);
      if (!r.session) {
        formError(form, '');
        $('[data-error]', form).className = 'form-ok';
        $('[data-error]', form).textContent = 'Check your email for a confirmation link, then sign in.';
        $('[data-error]', form).hidden = false;
      } else {
        await enter(r.session.user);
        toast('Welcome, ' + f.display_name + '.');
      }
    } catch (err) {
      formError(form, friendlyError(err));
    } finally { busy(form, false); }
  });

  $('#signin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = Object.fromEntries(new FormData(form));
    formError(form, '');
    busy(form, true);
    try {
      const s = await api.signIn({ email: (f.email || '').trim(), password: f.password || '' });
      await enter(s.user);
      toast('Welcome back.');
    } catch (err) {
      formError(form, friendlyError(err));
    } finally { busy(form, false); }
  });

  // ------------------------------------------------------------------ data
  async function enter(user) {
    state.user = { id: user.id, email: user.email, user_metadata: user.user_metadata };
    await loadAll(user);
    api.subscribe({
      verdict(v) { if (!state.feed.some((x) => x.id === v.id)) { state.feed.unshift(v); state.feed._fresh = v.id; applyCurrent(v); if (!state.profiles.has(v.actor) || !state.profiles.has(v.target)) refreshProfiles().then(render); else render(); } },
      message(m) {
        if (state.msgs.some((x) => x.id === m.id)) return;
        state.msgs.unshift(m);
        const peer = m.sender === state.user.id ? m.recipient : m.sender;
        if (state.view === 'chats' && state.peer === peer) markSeen(peer);
        if (!state.profiles.has(peer)) refreshProfiles().then(render); else render();
        if (m.sender !== state.user.id && !(state.view === 'chats' && state.peer === peer)) toast((state.profiles.get(m.sender) ? plainName(state.profiles.get(m.sender)) : 'Someone') + ' sent you a message');
      },
      profile(p) { state.profiles.set(p.id, p); render(); },
    });
    if (!/^#(people|feed|chats|me)/.test(location.hash)) history.replaceState(null, '', '#people');
    route();
  }
  async function loadAll(user) {
    const [profiles, current, feed, msgs] = await Promise.all([api.profiles(), api.currentVerdicts(), api.feed(), api.myMessages()]);
    state.profiles = new Map(profiles.map((p) => [p.id, p]));
    if (!state.profiles.has(user.id)) { const p = await api.ensureProfile(user); if (p) state.profiles.set(p.id, p); }
    state.current = current;
    state.feed = feed;
    state.msgs = msgs;
  }
  async function refreshProfiles() { const ps = await api.profiles(); state.profiles = new Map(ps.map((p) => [p.id, p])); }
  async function refreshAll() {
    if (!state.user) return;
    try { await loadAll(state.user); render(); } catch (e) { /* keep what we have */ }
  }
  setInterval(() => { if (!document.hidden && state.user && !state.realtime) refreshAll(); }, 6000);
  setInterval(() => { if (!document.hidden && state.user) refreshAll(); }, 45000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshAll(); });

  function applyCurrent(v) {
    const i = state.current.findIndex((c) => c.actor === v.actor && c.target === v.target);
    const row = { actor: v.actor, target: v.target, kind: v.kind, created_at: v.created_at };
    if (i >= 0) state.current[i] = row; else state.current.push(row);
  }
  function tally(id) {
    let round = 0, night = 0;
    for (const c of state.current) if (c.target === id) { if (c.kind === 'round') round++; else night++; }
    return { round, night };
  }
  function myVerdict(id) { const c = state.current.find((x) => x.actor === state.user.id && x.target === id); return c ? c.kind : null; }
  function markSeen(peer) { state.seen[peer] = new Date().toISOString(); try { localStorage.setItem('hn.seen', JSON.stringify(state.seen)); } catch (e) { /* ignore */ } }

  function conversations() {
    const map = new Map();
    for (const m of state.msgs) {
      const peer = m.sender === state.user.id ? m.recipient : m.sender;
      if (!map.has(peer)) map.set(peer, { peer, last: m, unread: false });
    }
    for (const c of map.values()) {
      c.unread = c.last.sender !== state.user.id && (!state.seen[c.peer] || c.last.created_at > state.seen[c.peer]);
    }
    return Array.from(map.values());
  }
  function unreadCount() { return conversations().filter((c) => c.unread).length; }

  // ------------------------------------------------------------------ rendering
  function render() {
    if (!state.user) return;
    $$('[data-nav]').forEach((a) => { if (a.dataset.nav === state.view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    const n = unreadCount();
    $$('[data-unread]').forEach((b) => { b.textContent = String(n); b.hidden = n === 0; });
    const mine = me();
    $('[data-head-me]').innerHTML = avatar(mine, 'sm') + '<span class="sr-only">Your profile</span>';
    const root = $('#view-root');
    const views = { people: renderPeople, feed: renderFeed, chats: renderChats, me: renderMe };
    // keep whatever the person is typing, and where the thread is scrolled, across re-renders
    const drafts = {};
    $$('input, textarea', root).forEach((el) => { const k = el.id || el.name; if (k && el.type !== 'file') drafts[k] = el.value; });
    const activeKey = document.activeElement && root.contains(document.activeElement) ? (document.activeElement.id || document.activeElement.name) : null;
    const oldBubbles = $('[data-bubbles]', root);
    const prevScroll = oldBubbles ? { top: oldBubbles.scrollTop, atBottom: oldBubbles.scrollHeight - oldBubbles.scrollTop - oldBubbles.clientHeight < 40 } : null;
    const prevThreadKey = state._threadKey;
    root.innerHTML = views[state.view]();
    if (state.view === 'me' || state.view === 'chats') {
      $$('input, textarea', root).forEach((el) => { const k = el.id || el.name; if (k && k in drafts && el.type !== 'file' && drafts[k] !== '') el.value = drafts[k]; });
      if (activeKey) { const el = $$('input, textarea', root).find((x) => (x.id || x.name) === activeKey); if (el) { el.focus(); try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* not a text field */ } } }
    }
    afterRender(prevScroll, prevThreadKey);
  }

  function renderPeople() {
    const others = Array.from(state.profiles.values()).filter((p) => p.id !== state.user.id);
    let html = '<div class="view-head"><div><h2>People</h2><p>' + (others.length ? others.length + (others.length === 1 ? ' other member' : ' other members') + '. Like or dislike anyone; it shows on the feed.' : 'Like or dislike anyone; it shows on the feed.') + ' Some members are AI.</p></div></div>';
    if (!others.length) {
      return html + '<div class="empty"><h3>You are the first one here.</h3><p>Invite your friends to get started.</p><button class="btn btn-soft" type="button" data-share>Copy invite link</button></div>';
    }
    html += '<div class="people-grid">' + others.map(personCard).join('') + '</div>';
    html += '<p class="me-note">You can edit your own profile under Profile.</p>';
    return html;
  }
  function personCard(p) {
    const t = tally(p.id);
    const mv = myVerdict(p.id);
    return '<article class="person" data-person="' + esc(p.id) + '">' +
      '<button class="person-photo" type="button" data-open-person="' + esc(p.id) + '" aria-label="Open ' + esc(p.display_name) + '">' + (p.avatar_url ? '<img src="' + esc(p.avatar_url) + '" alt="" loading="lazy">' : esc(initials(p.display_name))) + '</button>' +
      '<div><div class="person-name">' + nm(p) + '</div><div class="person-order">' + (p.bio ? esc(p.bio.length > 70 ? p.bio.slice(0, 67) + '…' : p.bio) : '<span class="muted">No bio yet</span>') + '</div></div>' +
      '<div class="tally"><span class="chip round" title="Likes">👍 ' + t.round + '</span><span class="chip night" title="Dislikes">👎 ' + t.night + '</span></div>' +
      '<div class="verdicts">' +
        '<button class="btn btn-round' + (mv === 'round' ? ' is-active' : '') + '" type="button" data-verdict="round" data-target="' + esc(p.id) + '"' + (mv === 'round' ? ' aria-pressed="true"' : '') + '>Like</button>' +
        '<button class="btn btn-night' + (mv === 'night' ? ' is-active' : '') + '" type="button" data-verdict="night" data-target="' + esc(p.id) + '"' + (mv === 'night' ? ' aria-pressed="true"' : '') + '>Dislike</button>' +
      '</div></article>';
  }

  function feedLine(v) {
    const a = state.profiles.get(v.actor) || { display_name: 'Someone' };
    const b = state.profiles.get(v.target) || { display_name: 'someone' };
    const verb = v.kind === 'round' ? ' <span class="verb">liked</span> ' : ' <span class="verb night">disliked</span> ';
    return '<div class="feed-item' + (state.feed._fresh === v.id ? ' pop' : '') + '"><span class="pair">' + avatar(a, 'sm') + avatar(b, 'sm') + '</span>' +
      '<p class="line"><b>' + nm(a) + '</b>' + verb + '<b>' + nm(b) + '</b></p>' +
      '<time datetime="' + esc(v.created_at) + '" title="' + esc(new Date(v.created_at).toLocaleString()) + '">' + esc(timeAgo(v.created_at)) + '</time></div>';
  }
  function renderFeed() {
    let html = '<div class="view-head"><div><h2>Feed</h2><p>Every like and dislike, newest first.' + (state.realtime ? ' Updates live.' : '') + '</p></div></div>';
    if (!state.feed.length) return html + '<div class="empty"><h3>Nothing here yet.</h3><p>Likes and dislikes will show up here. Find someone on People.</p><a class="btn btn-soft" href="#people">Go to People</a></div>';
    html += '<div class="feed">' + state.feed.map(feedLine).join('') + '</div>';
    return html;
  }

  function renderChats() {
    const convos = conversations();
    const peer = state.peer ? state.profiles.get(state.peer) : null;
    const listHtml = convos.length
      ? '<div class="convo-list">' + convos.map((c) => {
          const p = state.profiles.get(c.peer) || { display_name: 'Someone' };
          const preview = (c.last.sender === state.user.id ? 'You: ' : '') + c.last.body;
          return '<button class="convo' + (state.peer === c.peer ? ' is-active' : '') + '" type="button" data-open-chat="' + esc(c.peer) + '">' + avatar(p) +
            '<span class="convo-text"><span class="cname">' + nm(p) + '</span><span class="cpreview">' + esc(preview) + '</span></span>' +
            (c.unread ? '<span class="dot" aria-label="Unread"></span>' : '<time class="small">' + esc(timeAgo(c.last.created_at)) + '</time>') + '</button>';
        }).join('') + '</div>'
      : '';
    const picker = '<div class="field"><label for="chat-pick">New message</label><select id="chat-pick"><option value="">Choose someone…</option>' +
      Array.from(state.profiles.values()).filter((p) => p.id !== state.user.id).map((p) => '<option value="' + esc(p.id) + '">' + esc(plainName(p)) + '</option>').join('') + '</select></div>';
    let html = '<div class="view-head"><div><h2>Messages</h2><p>Private. Only you and the other person can read these.</p></div></div>';
    const left = '<div class="convo-side" ' + (peer ? 'data-mobile-hide' : '') + '>' + picker + (listHtml || '<div class="empty" style="padding:22px"><p>No conversations yet.</p></div>') + '</div>';
    let right;
    if (peer) {
      const thread = state.msgs.filter((m) => (m.sender === state.peer && m.recipient === state.user.id) || (m.sender === state.user.id && m.recipient === state.peer)).slice().reverse();
      right = '<section class="thread" aria-label="Conversation with ' + esc(peer.display_name) + '">' +
        '<div class="thread-head"><a class="icon-btn back" href="#chats" aria-label="Back to messages">‹</a>' + avatar(peer, 'sm') + '<span class="cname">' + nm(peer) + '</span></div>' +
        '<div class="bubbles" data-bubbles>' + (thread.length ? thread.map((m) => '<div class="bubble' + (m.sender === state.user.id ? ' mine' : '') + '">' + esc(m.body) + '<time>' + esc(clock(m.created_at)) + '</time></div>').join('') : '<p class="small" style="text-align:center;padding:20px 0">No messages yet. Say hi.</p>') + '</div>' +
        '<form class="composer" data-composer><input name="body" type="text" maxlength="2000" autocomplete="off" placeholder="Write a message" aria-label="Message" required><button class="btn btn-primary" type="submit">Send</button></form>' +
        '</section>';
    } else {
      right = '<div class="empty" data-mobile-hide><h3>Select a conversation</h3><p>Choose one from the list, or start a new one.</p></div>';
    }
    return html + '<div class="chat-layout">' + left + right + '</div>';
  }

  function renderMe() {
    const p = me() || { display_name: '', bio: '' };
    return '<div class="view-head"><div><h2>Your profile</h2><p>This is what other members see.</p></div></div>' +
      '<form class="me-card" data-me-form>' +
      '<div class="me-photo">' + avatar(p, 'xl') + '<div class="me-actions"><label class="btn btn-soft btn-sm" for="avatar-file">Change photo</label><input id="avatar-file" type="file" accept="image/*" class="sr-only"><span class="small">Cropped to a square.</span></div></div>' +
      '<div class="field"><label for="me-name">Name</label><input id="me-name" name="display_name" type="text" maxlength="40" required value="' + esc(p.display_name) + '"></div>' +
      '<div class="field"><label for="me-bio">About you</label><textarea id="me-bio" name="bio" maxlength="280" placeholder="A line or two about you">' + esc(p.bio) + '</textarea><span class="hint">Up to 280 characters.</span></div>' +
      '<div class="form-error" data-error hidden></div>' +
      '<div class="me-actions"><button class="btn btn-primary" type="submit">Save</button><button class="btn btn-ghost" type="button" data-signout>Sign out</button></div>' +
      '</form>' +
      '<p class="small">Signed in as ' + esc(state.user.email || '') + '. ' + (DEMO ? 'Demo mode: nothing is saved.' : 'Likes and dislikes are public to members. Messages are private.') + '</p>';
  }

  // ------------------------------------------------------------------ person sheet
  function openPerson(id) {
    const p = state.profiles.get(id);
    if (!p) return;
    const t = tally(id);
    const mv = myVerdict(id);
    $('#person-title').innerHTML = nm(p);
    $('[data-person-body]').innerHTML =
      '<div class="big">' + (p.avatar_url ? '<img src="' + esc(p.avatar_url) + '" alt="">' : esc(initials(p.display_name))) + '</div>' +
      (p.bio ? '<p>' + esc(p.bio) + '</p>' : '<p class="muted">No bio yet.</p>') +
      '<div class="tally"><span class="chip round">👍 ' + t.round + (t.round === 1 ? ' like' : ' likes') + '</span><span class="chip night">👎 ' + t.night + (t.night === 1 ? ' dislike' : ' dislikes') + '</span></div>' +
      '<div class="actions">' +
        '<button class="btn btn-round' + (mv === 'round' ? ' is-active' : '') + '" type="button" data-verdict="round" data-target="' + esc(id) + '">Like</button>' +
        '<button class="btn btn-night' + (mv === 'night' ? ' is-active' : '') + '" type="button" data-verdict="night" data-target="' + esc(id) + '">Dislike</button>' +
      '</div>' +
      '<a class="btn btn-soft btn-block" href="#chats/' + esc(id) + '" data-close-person>Message ' + esc(p.display_name) + '</a>';
    $('#person-sheet').hidden = false;
  }
  function closePerson() { $('#person-sheet').hidden = true; }
  $('#person-sheet').addEventListener('click', (e) => { if (e.target === e.currentTarget) closePerson(); });

  // ------------------------------------------------------------------ interactions
  document.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-verdict], [data-open-person], [data-open-chat], [data-signout], [data-close-person], [data-share]');
    if (!t) return;
    if (t.hasAttribute('data-close-person')) { closePerson(); return; }
    if (t.hasAttribute('data-open-person')) { openPerson(t.dataset.openPerson); return; }
    if (t.hasAttribute('data-open-chat')) { go('chats/' + t.dataset.openChat); return; }
    if (t.hasAttribute('data-share')) {
      try { await navigator.clipboard.writeText(location.origin + location.pathname); toast('Link copied.'); } catch (err) { toast(location.origin + location.pathname, 5000); }
      return;
    }
    if (t.hasAttribute('data-signout')) {
      await api.signOut();
      state.user = null; state.profiles = new Map(); state.current = []; state.feed = []; state.msgs = [];
      history.replaceState(null, '', location.pathname + location.search);
      showLanding();
      toast('Signed out.');
      return;
    }
    if (t.hasAttribute('data-verdict')) {
      const target = t.dataset.target, kind = t.dataset.verdict;
      if (myVerdict(target) === kind) { toast(kind === 'round' ? 'You already liked them.' : 'You already disliked them.'); return; }
      t.classList.remove('clink'); void t.offsetWidth; t.classList.add('clink');
      try {
        const v = await api.giveVerdict(target, kind);
        if (!state.feed.some((x) => x.id === v.id)) { state.feed.unshift(v); state.feed._fresh = v.id; }
        applyCurrent(v);
        const name = state.profiles.get(target) ? plainName(state.profiles.get(target)) : 'them';
        toast(kind === 'round' ? 'You liked ' + name + '.' : 'You disliked ' + name + '.');
        render();
        if (!$('#person-sheet').hidden) openPerson(target);
      } catch (err) { toast(friendlyError(err), 4000); }
    }
  });

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (form.hasAttribute('data-composer')) {
      e.preventDefault();
      const input = form.elements.body;
      const body = input.value.trim();
      if (!body || !state.peer) return;
      input.value = '';
      try {
        const m = await api.sendMessage(state.peer, body);
        if (!state.msgs.some((x) => x.id === m.id)) state.msgs.unshift(m);
        markSeen(state.peer);
        render();
        const again = $('[data-composer] input'); if (again) again.focus();
      } catch (err) { input.value = body; toast(friendlyError(err), 4000); }
    }
    if (form.hasAttribute('data-me-form')) {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(form));
      const fields = { display_name: (f.display_name || '').trim(), bio: (f.bio || '').trim() };
      if (!fields.display_name) return formError(form, 'Enter a name.');
      formError(form, '');
      busy(form, true);
      try { const p = await api.updateProfile(fields); state.profiles.set(p.id, p); render(); toast('Saved.'); }
      catch (err) { formError(form, friendlyError(err)); }
      finally { busy(form, false); }
    }
  });

  document.addEventListener('change', async (e) => {
    if (e.target.id === 'chat-pick' && e.target.value) { go('chats/' + e.target.value); return; }
    if (e.target.id === 'avatar-file' && e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      try {
        toast('Uploading…', 8000);
        const blob = await resizeImage(file, 512);
        const p = await api.uploadAvatar(blob);
        state.profiles.set(p.id, p);
        render();
        toast('Photo updated.');
      } catch (err) { toast(friendlyError(err), 4000); }
    }
  });

  function afterRender(prevScroll, prevThreadKey) {
    const b = $('[data-bubbles]');
    if (b) {
      const last = state.msgs.find((m) => (m.sender === state.peer && m.recipient === state.user.id) || (m.sender === state.user.id && m.recipient === state.peer));
      state._threadKey = state.peer + ':' + (last ? last.id : 0);
      if (!prevScroll || prevThreadKey !== state._threadKey || prevScroll.atBottom) b.scrollTop = b.scrollHeight;
      else b.scrollTop = prevScroll.top;
    }
    if (state.view === 'chats' && state.peer) markSeen(state.peer);
    // on phones, show either the list or the thread
    if (window.matchMedia('(max-width: 879px)').matches) {
      $$('[data-mobile-hide]').forEach((el) => { el.style.display = 'none'; });
    }
    state.feed._fresh = null;
  }

  // ------------------------------------------------------------------ boot
  (async function boot() {
    api.onAuth((s) => { if (!s && state.user) { state.user = null; showLanding(); } });
    try {
      const s = await api.session();
      if (s && s.user) { await enter(s.user); return; }
    } catch (e) { /* fall through to landing */ }
    route();
  })();
})();
