// YTKit front end. No frameworks, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem('ytkit:' + key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem('ytkit:' + key, JSON.stringify(value)); } catch { /* storage blocked */ }
  },
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const fmt = {
  bytes(n) {
    if (!n || n <= 0) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
  },
  duration(sec) {
    if (sec == null) return '';
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  },
  longDuration(sec) {
    if (sec == null) return '';
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.round(sec % 60);
    if (h) return `${h} hr ${m} min`;
    if (m) return `${m} min ${s} sec`;
    return `${s} sec`;
  },
  views(n) {
    if (n == null) return '';
    return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n) + ' views';
  },
  date(yyyymmdd) {
    if (!yyyymmdd || yyyymmdd.length !== 8) return '';
    const d = new Date(Date.UTC(+yyyymmdd.slice(0, 4), +yyyymmdd.slice(4, 6) - 1, +yyyymmdd.slice(6, 8)));
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
  },
  speed(bps) { return bps ? `${fmt.bytes(bps)}/s` : ''; },
  eta(sec) {
    if (sec == null || sec < 0) return '';
    if (sec < 60) return `${Math.max(1, Math.round(sec))}s left`;
    return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s left`;
  },
};

const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'style') node.style.cssText = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
};

const icon = (name, cls) => {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  if (cls) svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
};

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(path, { method = 'GET', body, signal } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = 'That request was not valid.';
    throw new ApiError(msg || `Server error (${res.status}).`, res.status);
  }
  return data;
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

function toast(message, kind = 'ok', ms = 3200) {
  const t = el('div', { class: `toast ${kind}`, role: 'status' }, icon(kind === 'err' ? 'alert' : 'check'), el('span', {}, message));
  $('#toasts').append(t);
  setTimeout(() => {
    t.classList.add('out');
    t.addEventListener('animationend', () => t.remove(), { once: true });
    setTimeout(() => t.remove(), 600);
  }, ms);
}

// ---------------------------------------------------------------------------
// Lookup flow
// ---------------------------------------------------------------------------

const form = $('#lookup-form');
const input = $('#q');
const omnibox = $('.omnibox');
const clearBtn = $('#clear-btn');

const state = {
  info: null,
  container: store.get('container', 'mp4'),
  audioFormat: store.get('audioFormat', 'mp3'),
  bitrate: store.get('bitrate', null),
  tab: 'video',
  lookup: null, // AbortController of the in-flight lookup
  lastQuery: '',
};

const looksLikeYouTube = (s) => /youtu\.?be|youtube-nocookie/i.test(s) || /^\s*[A-Za-z0-9_-]{11}\s*$/.test(s);

function show(id) {
  for (const name of ['loading', 'error', 'result']) {
    const node = $('#' + name);
    const on = name === id;
    if (on && node.hidden) {
      node.hidden = false;
      node.classList.remove('enter');
      void node.offsetWidth; // restart animation
      node.classList.add('enter');
    } else if (!on) {
      node.hidden = true;
    }
  }
}

let loadingTimer = null;
function startLoadingAnimation() {
  const messages = [
    'Contacting YouTube…',
    'Reading the stream manifest…',
    'Ranking every quality…',
    'Almost there…',
  ];
  const steps = $$('#loading-steps .step');
  const title = $('#loading-title');
  let i = 0;
  const tick = () => {
    title.textContent = messages[Math.min(i, messages.length - 1)];
    title.classList.remove('swap'); void title.offsetWidth; title.classList.add('swap');
    steps.forEach((s, j) => {
      s.classList.toggle('done', j < i);
      s.classList.toggle('active', j === Math.min(i, steps.length - 1) && i < steps.length);
    });
    i++;
  };
  tick();
  clearInterval(loadingTimer);
  loadingTimer = setInterval(tick, 1600);
}
function stopLoadingAnimation() { clearInterval(loadingTimer); loadingTimer = null; }

function setBusy(busy) {
  omnibox.classList.toggle('busy', busy);
  $('#go-btn').setAttribute('aria-busy', String(busy));
}

async function lookup(raw, { scroll = true } = {}) {
  const q = (raw ?? input.value).trim();
  if (!q) {
    shakeInput();
    input.focus();
    toast('Paste a YouTube link first.', 'err');
    return;
  }
  if (state.lookup) state.lookup.abort();
  const ctrl = new AbortController();
  state.lookup = ctrl;
  state.lastQuery = q;

  setBusy(true);
  show('loading');
  startLoadingAnimation();
  if (scroll) scrollToStage();

  try {
    const info = await api(`/api/info?q=${encodeURIComponent(q)}`, { signal: ctrl.signal });
    if (ctrl.signal.aborted) return;
    state.info = info;
    renderResult(info);
    show('result');
    addRecent(info);
    const url = new URL(location.href);
    url.searchParams.set('v', info.id);
    history.replaceState(null, '', url);
    document.title = `${info.title} · YTKit`;
  } catch (err) {
    if (err.name === 'AbortError') return;
    $('#error-text').textContent = err.message || 'Something went wrong.';
    show('error');
    if (err.status === 400) shakeInput();
  } finally {
    if (state.lookup === ctrl) {
      state.lookup = null;
      setBusy(false);
      stopLoadingAnimation();
    }
  }
}

function scrollToStage() {
  const stage = $('#stage');
  const rect = stage.getBoundingClientRect();
  if (rect.top > window.innerHeight * 0.7 || rect.top < 0) {
    window.scrollTo({ top: window.scrollY + rect.top - 120, behavior: reduceMotion ? 'auto' : 'smooth' });
  }
}

function shakeInput() {
  omnibox.classList.remove('shake'); void omnibox.offsetWidth; omnibox.classList.add('shake');
}

// ---------------------------------------------------------------------------
// Rendering the result card
// ---------------------------------------------------------------------------

function renderResult(info) {
  const img = $('#r-thumb');
  img.classList.remove('loaded');
  img.onload = () => img.classList.add('loaded');
  img.onerror = () => {
    if (!img.dataset.fallback) { img.dataset.fallback = '1'; img.src = `https://i.ytimg.com/vi/${info.id}/hqdefault.jpg`; }
  };
  delete img.dataset.fallback;
  img.src = info.thumbnail || `https://i.ytimg.com/vi/${info.id}/hqdefault.jpg`;
  img.alt = `Thumbnail of ${info.title}`;

  $('#r-thumb-link').href = info.webpage_url || `https://www.youtube.com/watch?v=${info.id}`;
  $('#r-duration').textContent = fmt.duration(info.duration);
  $('#r-duration').hidden = !info.duration;
  $('#r-short').hidden = !info.is_short;
  $('#r-title').textContent = info.title;

  const ch = $('#r-channel');
  ch.replaceChildren(info.channel || 'Unknown channel');
  if (info.channel_verified) ch.append(icon('check', 'verified'));

  $('#r-views').textContent = fmt.views(info.view_count);
  $('#r-date').textContent = fmt.date(info.upload_date);
  $('#r-length').textContent = fmt.longDuration(info.duration);
  $('#r-thumb-dl').href = `/api/thumbnail/${info.id}`;

  if (!info.video.length) setTab('audio');
  $('#tab-video').disabled = !info.video.length;
  $('#tab-audio').disabled = !info.audio.available;
  if (!info.audio.available) setTab('video');

  renderVideo();
  renderAudio();
}

const containerNotes = {
  mp4: '<b>MP4</b> plays everywhere. H.264 + AAC up to 1080p, VP9 or AV1 above that.',
  webm: '<b>WebM</b> uses VP9 or AV1 with Opus audio. Great for browsers and the web.',
  mkv: '<b>MKV</b> gets the absolute best streams, HDR included. Ideal for archiving.',
};

function renderVideo() {
  const info = state.info;
  if (!info) return;
  if (!info.video.some((o) => o.variants[state.container])) state.container = 'mp4';

  $$('#container-seg button').forEach((b) => {
    const c = b.dataset.container;
    b.setAttribute('aria-checked', String(c === state.container));
    b.tabIndex = c === state.container ? 0 : -1;
    b.disabled = !info.video.some((o) => o.variants[c]);
  });
  $('#container-note').innerHTML = containerNotes[state.container];

  const list = $('#video-list');
  list.replaceChildren();
  const rows = info.video.filter((o) => o.variants[state.container]);
  if (!rows.length) {
    list.append(el('li', { class: 'q-empty' }, `No ${state.container.toUpperCase()} streams for this video. Try another format.`));
    return;
  }
  rows.forEach((o, i) => {
    const v = o.variants[state.container];
    const fps = v.fps && v.fps > 30 ? v.fps : null;
    const badges = [
      i === 0 ? el('span', { class: 'badge best' }, 'Best') : null,
      o.badge ? el('span', { class: `badge${o.quality >= 1440 ? ' hot' : ''}` }, o.badge) : null,
      v.hdr ? el('span', { class: 'badge hdr' }, 'HDR') : null,
      v.vcodec ? el('span', { class: 'badge' }, v.vcodec) : null,
      v.acodec ? el('span', { class: 'badge' }, v.acodec) : el('span', { class: 'badge' }, 'Muted'),
    ];
    const label = `${o.quality}p${fps ? fps : ''} ${state.container.toUpperCase()}`;
    const btn = el('button', {
      class: 'dl-btn', type: 'button', 'aria-label': `Download ${label}`,
      onclick: () => startJob({ kind: 'video', quality: o.quality, container: state.container }, label, btn),
    }, icon('download'), el('span', {}, 'Download'));
    list.append(el('li', { class: `q-row${i === 0 ? ' top' : ''}`, style: `--i:${i}` },
      el('div', { class: 'q-res' }, `${o.quality}p`, fps ? el('small', {}, fps) : null),
      el('div', { class: 'q-info' }, badges),
      el('div', { class: 'q-size' }, v.size ? `~${fmt.bytes(v.size)}` : ''),
      btn,
    ));
  });
}

function audioFormats() { return state.info?.audio?.formats || []; }

function estimateAudioSize(f, bitrate) {
  const info = state.info;
  if (!info || !info.duration) return null;
  const src = info.audio.source || {};
  if (f.id === 'original') return src.size || null;
  if (f.id === 'wav') return info.duration * (src.asr || 48000) * 2 * 2;
  if (f.id === 'flac') return info.duration * (src.asr || 48000) * 2 * 2 * 0.55;
  return (info.duration * (bitrate || f.default) * 1000) / 8;
}

function renderAudio() {
  const formats = audioFormats();
  if (!formats.length) return;
  if (!formats.some((f) => f.id === state.audioFormat)) state.audioFormat = 'mp3';
  const current = formats.find((f) => f.id === state.audioFormat);
  if (!current.bitrates.includes(state.bitrate)) state.bitrate = current.default;

  const grid = $('#audio-grid');
  grid.replaceChildren(...formats.map((f, i) => el('button', {
    class: 'a-card', type: 'button', role: 'radio', 'aria-checked': String(f.id === state.audioFormat),
    tabindex: f.id === state.audioFormat ? '0' : '-1', style: `--i:${i}`, 'data-id': f.id,
    onclick: () => { state.audioFormat = f.id; state.bitrate = f.default; store.set('audioFormat', f.id); renderAudio(); },
  },
  el('span', { class: 'tick' }, icon('check')),
  el('strong', {}, f.label),
  el('span', {}, f.desc),
  f.lossless ? el('em', { class: 'lossless' }, 'LOSSLESS') : null)));

  const seg = $('#bitrate-seg');
  if (current.bitrates.length) {
    seg.replaceChildren(...current.bitrates.map((b) => el('button', {
      type: 'button', role: 'radio', 'aria-checked': String(b === state.bitrate), tabindex: b === state.bitrate ? '0' : '-1',
      onclick: () => { state.bitrate = b; store.set('bitrate', b); renderAudio(); },
    }, `${b} kbps`)));
  } else {
    const text = current.id === 'original' ? 'As uploaded' : 'Lossless';
    seg.replaceChildren(el('button', { type: 'button', role: 'radio', 'aria-checked': 'true', tabindex: '0' }, text));
  }

  const size = estimateAudioSize(current, state.bitrate);
  $('#audio-size').textContent = size ? `~${fmt.bytes(size)}` : '–';
  $('#audio-dl span').textContent = `Download ${current.label}`;

  const src = state.info.audio.source;
  const note = $('#audio-source');
  if (src) {
    const bits = [src.codec, src.abr ? `${src.abr} kbps` : null, src.asr ? `${(src.asr / 1000).toFixed(1)} kHz` : null].filter(Boolean).join(' · ');
    note.innerHTML = '';
    note.append(el('b', {}, 'Source audio: '), bits, '. Converting can\'t add detail the source lacks, so WAV, FLAC or Original keep it perfectly.');
  } else {
    note.textContent = '';
  }
}

function setTab(name) {
  state.tab = name;
  $('.tabs').dataset.active = name;
  for (const t of ['video', 'audio']) {
    const tab = $(`#tab-${t}`);
    tab.setAttribute('aria-selected', String(t === name));
    tab.tabIndex = t === name ? 0 : -1;
    const panel = $(`#panel-${t}`);
    panel.hidden = t !== name;
  }
}

// ---------------------------------------------------------------------------
// Download jobs + dock
// ---------------------------------------------------------------------------

const jobs = new Map(); // id -> { data, node, es, meta }
const dock = $('#dock');

function updateDockCount() {
  const active = [...jobs.values()].filter((j) => !['done', 'error', 'cancelled'].includes(j.data.stage)).length;
  $('#dock-count').textContent = active || jobs.size;
  dock.hidden = jobs.size === 0;
}

async function startJob(choice, label, button) {
  const info = state.info;
  if (!info) return;
  const body = { video_id: info.id, ...choice };
  if (choice.kind === 'audio') { body.format = state.audioFormat; body.bitrate = audioFormats().find((f) => f.id === state.audioFormat)?.bitrates.length ? state.bitrate : null; }
  if (button) button.disabled = true;
  try {
    const job = await api('/api/jobs', { method: 'POST', body });
    const meta = { title: info.title, thumb: `https://i.ytimg.com/vi/${info.id}/mqdefault.jpg`, label };
    addJob(job, meta);
    dock.classList.remove('collapsed');
    $('#dock-toggle').setAttribute('aria-expanded', 'true');
    toast(`Started ${label}`, 'ok', 2000);
  } catch (err) {
    toast(err.message, 'err', 5000);
  } finally {
    if (button) setTimeout(() => { button.disabled = false; }, 800);
  }
}

function addJob(data, meta) {
  const bar = el('div', { class: 'bar indeterminate' }, el('i'));
  const sub = el('p', { class: 'job-sub' }, 'Queued');
  const actions = el('div', { class: 'job-actions' });
  const node = el('li', { class: 'job' },
    el('div', { class: 'job-top' },
      el('img', { class: 'job-thumb', src: meta.thumb, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }),
      el('div', { class: 'job-text' }, el('p', { class: 'job-title', title: meta.title }, meta.title), sub),
      actions),
    bar);
  $('#dock-list').prepend(node);
  const entry = { data, node, bar, sub, actions, meta, es: null, poll: null, saved: false };
  jobs.set(data.id, entry);
  renderJob(entry);
  updateDockCount();
  subscribe(entry);
}

function subscribe(entry) {
  const id = entry.data.id;
  if ('EventSource' in window) {
    const es = new EventSource(`/api/jobs/${id}/events`);
    entry.es = es;
    es.onmessage = (e) => { try { onJobUpdate(entry, JSON.parse(e.data)); } catch { /* ignore */ } };
    es.onerror = () => {
      // Fall back to polling if the stream drops (some proxies buffer SSE).
      es.close(); entry.es = null;
      if (!isTerminal(entry.data.stage)) startPolling(entry);
    };
  } else {
    startPolling(entry);
  }
}

function startPolling(entry) {
  if (entry.poll) return;
  entry.poll = setInterval(async () => {
    try {
      onJobUpdate(entry, await api(`/api/jobs/${entry.data.id}`));
    } catch (err) {
      if (err.status === 404) onJobUpdate(entry, { ...entry.data, stage: 'error', error: err.message });
    }
  }, 1000);
}

const isTerminal = (stage) => ['done', 'error', 'cancelled'].includes(stage);

function onJobUpdate(entry, data) {
  entry.data = data;
  renderJob(entry);
  if (isTerminal(data.stage)) {
    entry.es?.close(); entry.es = null;
    clearInterval(entry.poll); entry.poll = null;
    updateDockCount();
    if (data.stage === 'done' && !entry.saved) {
      entry.saved = true;
      saveFile(data);
      toast(`${entry.meta.label} is ready`, 'ok');
    } else if (data.stage === 'error') {
      toast(data.error || 'Download failed.', 'err', 6000);
    }
  }
}

function saveFile(data) {
  const a = el('a', { href: data.download_url, download: data.filename || '' });
  document.body.append(a);
  a.click();
  a.remove();
}

function renderJob(entry) {
  const { data, node, bar, sub, actions, meta } = entry;
  node.className = `job ${data.stage}`;
  const pct = data.percent || 0;
  const indeterminate = data.stage === 'queued' || data.stage === 'preparing' || data.stage === 'processing' || (data.stage === 'downloading' && pct <= 0);
  bar.classList.toggle('indeterminate', indeterminate);
  bar.firstChild.style.setProperty('--p', `${data.stage === 'done' ? 100 : pct}%`);

  let text = meta.label + ' · ';
  switch (data.stage) {
    case 'queued': text += 'Waiting in queue'; break;
    case 'preparing': text += 'Preparing'; break;
    case 'downloading': text += [`${Math.round(pct)}%`, fmt.speed(data.speed), fmt.eta(data.eta)].filter(Boolean).join(' · '); break;
    case 'processing': text += data.message || 'Processing'; break;
    case 'done': text += `Ready${data.size ? ' · ' + fmt.bytes(data.size) : ''}`; break;
    case 'cancelled': text += 'Cancelled'; break;
    case 'error': text = data.error || 'Failed'; break;
    default: text += data.message || '';
  }
  sub.textContent = text;
  sub.title = text;

  actions.replaceChildren();
  if (data.stage === 'done') {
    actions.append(el('button', { class: 'job-act save', type: 'button', 'aria-label': 'Save file again', title: 'Save again', onclick: () => saveFile(data) }, icon('download')));
  }
  if (!isTerminal(data.stage)) {
    actions.append(el('button', { class: 'job-act', type: 'button', 'aria-label': 'Cancel download', title: 'Cancel', onclick: () => cancelJob(entry) }, icon('x')));
  } else {
    actions.append(el('button', { class: 'job-act', type: 'button', 'aria-label': 'Remove from list', title: 'Remove', onclick: () => removeJob(entry) }, icon('x')));
  }
}

async function cancelJob(entry) {
  try { await api(`/api/jobs/${entry.data.id}`, { method: 'DELETE' }); } catch { /* already gone */ }
  if (!entry.es && !entry.poll) onJobUpdate(entry, { ...entry.data, stage: 'cancelled' });
}

function removeJob(entry) {
  entry.es?.close(); clearInterval(entry.poll);
  entry.node.style.transition = 'opacity .25s, transform .25s';
  entry.node.style.opacity = '0';
  entry.node.style.transform = 'translateX(20px)';
  setTimeout(() => { entry.node.remove(); jobs.delete(entry.data.id); updateDockCount(); }, 250);
}

// ---------------------------------------------------------------------------
// Recent lookups (stored in this browser only)
// ---------------------------------------------------------------------------

function addRecent(info) {
  const list = store.get('recent', []).filter((r) => r.id !== info.id);
  list.unshift({ id: info.id, title: info.title });
  store.set('recent', list.slice(0, 6));
  renderRecent();
}

function renderRecent() {
  const list = store.get('recent', []);
  $('#recent').hidden = !list.length;
  $('#recent-list').replaceChildren(...list.map((r, i) => el('button', {
    class: 'recent-item', type: 'button', title: r.title, style: `animation-delay:${i * 50}ms`,
    onclick: () => { input.value = `https://youtu.be/${r.id}`; syncClear(); lookup(); },
  }, el('img', { src: `https://i.ytimg.com/vi/${r.id}/default.jpg`, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }), el('span', {}, r.title))));
}

// ---------------------------------------------------------------------------
// Chrome: theme, header, nav, reveal, spotlight, status
// ---------------------------------------------------------------------------

function initTheme() {
  const btn = $('#theme-toggle');
  const sync = () => {
    const t = document.documentElement.dataset.theme;
    btn.setAttribute('aria-label', t === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  };
  btn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('ytkit:theme', next); } catch { /* ignore */ }
    sync();
  });
  sync();
}

function initHeader() {
  const header = $('.site-header');
  const onScroll = () => header.classList.toggle('scrolled', window.scrollY > 8);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  const nav = $('#site-nav');
  const menu = $('#menu-toggle');
  const setMenu = (open) => {
    nav.classList.toggle('open', open);
    menu.setAttribute('aria-expanded', String(open));
    menu.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  };
  menu.addEventListener('click', (e) => { e.stopPropagation(); setMenu(!nav.classList.contains('open')); });
  nav.addEventListener('click', (e) => { if (e.target.closest('a')) setMenu(false); });
  document.addEventListener('click', (e) => { if (!nav.contains(e.target)) setMenu(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });

  // Highlight the section in view.
  const links = new Map($$('.nav a').map((a) => [a.getAttribute('href').slice(1), a]));
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting) {
        links.forEach((a) => a.classList.remove('active'));
        links.get(en.target.id)?.classList.add('active');
      }
    }
  }, { rootMargin: '-45% 0px -50% 0px' });
  links.forEach((_, id) => { const s = document.getElementById(id); if (s) io.observe(s); });
}

function initReveal() {
  const items = $$('.reveal');
  if (reduceMotion || !('IntersectionObserver' in window)) { items.forEach((n) => n.classList.add('in')); return; }
  // Stagger siblings that reveal together.
  const groups = new Map();
  items.forEach((n) => {
    const idx = groups.get(n.parentElement) || 0;
    n.style.setProperty('--d', `${Math.min(idx, 6) * 70}ms`);
    groups.set(n.parentElement, idx + 1);
  });
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
  items.forEach((n) => io.observe(n));
}

function initSpotlight() {
  document.addEventListener('pointermove', (e) => {
    const card = e.target.closest?.('.spot');
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', `${e.clientX - r.left}px`);
    card.style.setProperty('--my', `${e.clientY - r.top}px`);
  }, { passive: true });
}

async function initStatus() {
  const box = $('#status');
  const text = $('#status-text');
  try {
    const h = await api('/api/health');
    const problems = [];
    if (!h.ffmpeg) problems.push('ffmpeg missing');
    if (!h.js_runtime) problems.push('JS runtime missing');
    box.className = `status ${problems.length ? 'warn' : 'ok'}`;
    text.textContent = problems.length ? `Online · ${problems.join(', ')}` : `All systems go · v${h.version}`;
    box.title = `yt-dlp ${h.yt_dlp}`;
  } catch {
    box.className = 'status err';
    text.textContent = 'Server unreachable';
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function syncClear() { clearBtn.hidden = !input.value; }

function initInput() {
  form.addEventListener('submit', (e) => { e.preventDefault(); lookup(); });
  input.addEventListener('input', syncClear);
  input.addEventListener('paste', () => {
    // Fetch automatically when a link is pasted.
    setTimeout(() => { syncClear(); if (looksLikeYouTube(input.value)) lookup(); }, 0);
  });
  clearBtn.addEventListener('click', () => { input.value = ''; syncClear(); input.focus(); });

  const pasteBtn = $('#paste-btn');
  if (!navigator.clipboard?.readText) pasteBtn.hidden = true;
  pasteBtn.addEventListener('click', async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (!text) { toast('Your clipboard is empty.', 'err'); return; }
      input.value = text;
      syncClear();
      lookup();
    } catch {
      input.focus();
      toast('Clipboard access was blocked. Press Ctrl+V (or ⌘V) instead.', 'err', 4500);
    }
  });

  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      input.focus();
      input.select();
    } else if (e.key === 'Escape' && document.activeElement === input && input.value) {
      input.value = ''; syncClear();
    }
  });

  $$('[data-focus-input]').forEach((a) => a.addEventListener('click', () => setTimeout(() => input.focus({ preventScroll: true }), 400)));
  $('#retry-btn').addEventListener('click', () => lookup(state.lastQuery || input.value));
}

function initResultControls() {
  $('#tab-video').addEventListener('click', () => setTab('video'));
  $('#tab-audio').addEventListener('click', () => setTab('audio'));
  $('.tabs').addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const next = state.tab === 'video' ? 'audio' : 'video';
    if ($(`#tab-${next}`).disabled) return;
    setTab(next);
    $(`#tab-${next}`).focus();
  });

  $('#container-seg').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-container]');
    if (!b || b.disabled) return;
    state.container = b.dataset.container;
    store.set('container', state.container);
    renderVideo();
  });

  // Arrow keys move between options in radio groups.
  for (const group of ['#container-seg', '#audio-grid', '#bitrate-seg']) {
    $(group).addEventListener('keydown', (e) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
      const items = $$('[role="radio"]:not(:disabled)', $(group));
      const i = items.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      const next = items[(i + (e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length];
      next.click();
      requestAnimationFrame(() => {
        const sel = group === '#audio-grid' ? `[data-id="${next.dataset.id}"]` : null;
        (sel ? $(sel, $(group)) : $$('[role="radio"]', $(group)).find((n) => n.textContent === next.textContent))?.focus();
      });
    });
  }

  $('#audio-dl').addEventListener('click', (e) => {
    const f = audioFormats().find((x) => x.id === state.audioFormat);
    const label = f.label + (f.bitrates.length ? ` ${state.bitrate} kbps` : '');
    startJob({ kind: 'audio' }, label, e.currentTarget);
  });

  $('#r-copy').addEventListener('click', async () => {
    const url = `https://youtu.be/${state.info?.id}`;
    try { await navigator.clipboard.writeText(url); toast('Link copied'); } catch { toast(url, 'ok', 5000); }
  });

  $('#dock-toggle').addEventListener('click', () => {
    const collapsed = dock.classList.toggle('collapsed');
    $('#dock-toggle').setAttribute('aria-expanded', String(!collapsed));
  });
}

function hideBrokenImages() {
  // Thumbnails come from YouTube's CDN; if one fails, show the gradient placeholder instead of a broken icon.
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img instanceof HTMLImageElement && img.closest('.job, .recent-item')) img.removeAttribute('src');
  }, true);
}

function init() {
  hideBrokenImages();
  $('#year').textContent = new Date().getFullYear();
  initTheme();
  initHeader();
  initReveal();
  initSpotlight();
  initInput();
  initResultControls();
  renderRecent();
  initStatus();

  // Deep links: /?v=VIDEO_ID or /?url=...
  const params = new URLSearchParams(location.search);
  const deep = params.get('v') || params.get('url') || params.get('q');
  if (deep) {
    input.value = deep;
    syncClear();
    lookup(deep, { scroll: false });
  }
}

init();
