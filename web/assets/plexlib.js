/* plexmon - Settings → Plex library (2.1.0)
   What plex_tools.py did, as pages: the posters of a library, a title with
   everything Plex knows about it, and the tools - the default audio and
   subtitles, watched, labels, titles in another language, the watchlist and
   the ratings. Everything asks the daemon; the daemon asks Plex on this
   machine. Whatever writes shows what it will do first. */
(function () {
'use strict';

/* ---- icons of its own ------------------------------------------------------ */
ICONS.film = '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>';
ICONS.tv = '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 21h8M9 2l3 4 3-4"/>';
ICONS.star = '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9 6.8 19.6l1-5.8-4.3-4.1 5.9-.9z"/>';
ICONS.tag = '<path d="M3 12V4h8l9.5 9.5-8 8z"/><circle cx="7.5" cy="8.5" r="1.4"/>';
ICONS.subs = '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M7 12h4M13 12h4M7 15.5h7M16 15.5h1"/>';
ICONS.audio = '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>';
ICONS.bookmark = '<path d="M6.5 3.5h11v17l-5.5-4-5.5 4z"/>';
ICONS.select = '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M8 12.3l2.8 2.7L16.3 9"/>';
ICONS.translate = '<path d="M4 5h9M8.5 3v2M6 5c.6 3.4 2.6 6 6 7.5M11 5c-.7 3.5-3 6.5-7 8"/><path d="M13 21l4-9 4 9M14.5 17.5h5"/>';
ICONS.grid = '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>';
ICONS.ext = '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V7.5A1.5 1.5 0 0 1 5.5 6H10"/>';
ICONS.chevron = '<path d="M7 4l9 8-9 8"/>';
ICONS.swap = '<path d="M4 8h13l-3.5-3.5M20 16H7l3.5 3.5"/>';
ICONS.invert = '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M3.5 20.5l17-17"/><path d="M12 3.5h5.5a3 3 0 0 1 3 3V12z" fill="currentColor" stroke="none"/>';
ICONS.minus = '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M8 12h8"/>';

var LOGO = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h5.2l6 9-6 9H7l6-9z" fill="#e5a00d"/></svg>';
var VIEWS = [['library', 'Library', 'grid'], ['watchlist', 'Watchlist', 'bookmark'],
             ['titles', 'Titles', 'translate'], ['ratings', 'Ratings', 'star'], ['labels', 'Labels', 'tag']];
var STORE_KEY = 'plexmon-plexlib';
var CHUNK = 96;

var PX = {st: null, view: 'library', lib: '', data: {}, q: '', show: 'all', sort: 'title',
          label: '', genre: '', country: '', norating: false, size: 168, selecting: false,
          sel: {}, lastPick: null, shown: [], drawn: 0, poll: null, jobWait: [], wl: null,
          root: null, loaded: false, started: false, tool: {}};

function remember() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({view: PX.view, lib: PX.lib, sort: PX.sort,
      size: PX.size, show: PX.show}));
  } catch (e) { }
}
(function recall() {
  try {
    var got = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    ['view', 'lib', 'sort', 'show'].forEach(function (k) { if (got[k]) PX[k] = String(got[k]); });
    if (got.size) PX.size = Math.max(110, Math.min(260, +got.size || 168));
  } catch (e) { }
}());

/* ---- little helpers -------------------------------------------------------- */
function txt(kind, cls, words) { return noI18n(el(kind, cls, words)); }
function ico(key, size) { var s = el('span'); s.innerHTML = iconMarkup(key, size || 16); return s.firstChild; }
function imgUrl(u, w, h) {
  return '?api=plex-img&u=' + encodeURIComponent(u) + '&w=' + Math.round(w) + '&h=' + Math.round(h);
}
var LAZY = ('IntersectionObserver' in window) ? new IntersectionObserver(function (seen) {
  seen.forEach(function (e) {
    if (!e.isIntersecting) return;
    LAZY.unobserve(e.target);
    load(e.target);
  });
}, {rootMargin: '700px 0px'}) : null;
function load(im) {
  if (!im.dataset.src) return;
  im.src = im.dataset.src;
  delete im.dataset.src;
}
function picture(u, w, h, alt) {
  var im = document.createElement('img');
  im.alt = alt || '';
  im.decoding = 'async';
  im.onload = function () { im.classList.add('in'); };
  im.onerror = function () { im.remove(); };
  if (!u) return im;
  var dpr = Math.min(2, window.devicePixelRatio || 1);
  im.dataset.src = imgUrl(u, w * dpr, h * dpr);
  if (LAZY) LAZY.observe(im); else load(im);
  return im;
}
function plain(s) {
  return String(s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
}
function matcher(q) {
  q = String(q || '').trim();
  if (!q) return function () { return true; };
  if (/[*?]/.test(q)) {
    var re = new RegExp('^' + plain(q).replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$');
    return function (t) { return re.test(plain(t)); };
  }
  var w = plain(q);
  return function (t) { return plain(t).indexOf(w) >= 0; };
}
function dur(ms) {
  if (!ms) return '';
  var m = Math.round(ms / 60000);
  return m >= 60 ? Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : m + ' min';
}
function res(m) {
  var r = String((m || {}).resolution || '').toLowerCase();
  if (r === '4k') return '4K';
  if (/^\d+$/.test(r)) return r + 'p';
  return r ? r.toUpperCase() : '';
}
function seenState(it) {
  if (it.type === 'show' || it.type === 'season') {
    if (!it.leaves) return {k: 'none', p: 0, left: 0};
    if (it.seen >= it.leaves) return {k: 'seen', p: 1, left: 0};
    return {k: it.seen ? 'part' : 'new', p: it.seen / it.leaves, left: it.leaves - it.seen};
  }
  if (it.views) return {k: 'seen', p: 1, left: 0};
  if (it.offset && it.duration) return {k: 'part', p: Math.min(0.97, it.offset / it.duration), left: 1};
  return {k: 'new', p: 0, left: 1};
}
function bestScore(it) {
  var r = it.rating || it.audience;
  return r ? (+r).toFixed(1) : '';
}
function libOf(k) {
  return ((PX.st || {}).sections || []).filter(function (s) { return s.key === String(k); })[0];
}
function plexLink(rk) {
  var m = ((PX.st || {}).server || {}).machine;
  if (!m) return '';
  return 'https://app.plex.tv/desktop/#!/server/' + m + '/details?key=' + encodeURIComponent('/library/metadata/' + rk);
}
function dotted(host, bits) {
  bits.filter(function (b) { return b !== '' && b !== null && b !== undefined; }).forEach(function (b, n) {
    if (n) host.appendChild(document.createTextNode(' · '));
    host.appendChild(typeof b === 'object' ? b : el('span', '', String(b)));
  });
  return host;
}
function note(kind, words, icon) {
  var n = el('div', 'px-note ' + (kind || ''));
  n.appendChild(ico(icon || (kind === 'safe' ? 'shield' : (kind === 'warn' ? 'flag' : 'info')), 18));
  var t = el('div');
  t.innerHTML = words;
  n.appendChild(t);
  return n;
}
function emptyState(icon, head, words) {
  var e = el('div', 'px-empty');
  e.appendChild(ico(icon, 46));
  e.appendChild(el('b', '', head));
  if (words) e.appendChild(el('div', '', words));
  return e;
}
function seg(pairs, now, pick) {
  var s = el('div', 'px-seg');
  pairs.forEach(function (p) {
    var b = el('button', p[0] === now ? 'on' : '', p[1]);
    b.type = 'button';
    b.onclick = function () { pick(p[0]); };
    s.appendChild(b);
  });
  return s;
}
function wideModal(title, build, buttons) {
  var m = modal(title, build, buttons, 'px-wide');
  return m;
}

/* ---- the job: one at a time, followed ------------------------------------- */
function jobChip(j) {
  var c = $('px-job');
  if (!c) return;
  c.classList.toggle('on', !!(j && j.running));
  if (!j || !j.running) return;
  var bar = c.querySelector('.bar');
  bar.classList.toggle('indet', !j.whole);
  bar.firstChild.style.width = j.whole ? Math.round(100 * j.done / j.whole) + '%' : '';
  var w = c.querySelector('.w');
  clear(w);
  w.appendChild(el('span', '', j.says || j.what || ''));
  if (j.whole) w.appendChild(txt('span', '', ' · ' + j.done + '/' + j.whole));
}
function pollJob() {
  if (PX.poll) return;
  PX.poll = setInterval(function () {
    api('plex-job').then(function (r) {
      var j = r.job;
      jobChip(j);
      PX.jobWait.forEach(function (w) { if (w.progress) w.progress(j); });
      if (!j.running) {
        clearInterval(PX.poll); PX.poll = null;
        var waiting = PX.jobWait; PX.jobWait = [];
        waiting.forEach(function (w) {
          if (w.id && j.id !== w.id) { w.fail(new Error('another job took its place')); return; }
          if (j.error) w.fail(new Error(j.error)); else w.done(j.result || {}, j);
        });
      }
    }).catch(function () { });
  }, 900);
}
/* A job started by `start` (a promise of {job: id}); `host`, if given, shows
   its progress, its last lines and a Stop. Resolves with what it found. */
function follow(start, host) {
  return start.then(function (r) {
    var id = r.job || (r && r.id);
    if (!id) return r;
    return new Promise(function (done, fail) {
      var box = null;
      if (host) {
        clear(host);
        box = el('div', 'px-follow');
        var bar = el('div', 'going wide');
        bar.innerHTML = '<div class="bar"><span></span></div>';
        var says = el('div', 'muted small');
        var saysW = el('span'), saysN = txt('span', '', '');
        says.appendChild(saysW); says.appendChild(saysN);
        var logb = el('div', 'px-log');
        logb.hidden = true;
        var stop = cmdButton('Stop', 'cancel', 'small');
        stop.onclick = function () { api('plex', {action: 'stop'}).then(function () { toast('stops after the item it is on', 'good'); }).catch(oops); };
        var row = el('div', 'btnrow');
        row.style.marginTop = '8px';
        row.appendChild(stop);
        box.appendChild(bar); box.appendChild(says); box.appendChild(logb); box.appendChild(row);
        host.appendChild(box);
        box._bar = bar; box._says = saysW; box._n = saysN; box._log = logb;
      }
      PX.jobWait.push({id: id, done: done, fail: fail, progress: function (j) {
        if (!box) return;
        box._bar.querySelector('span').style.width = j.whole ? Math.round(100 * j.done / j.whole) + '%' : '8%';
        box._says.textContent = j.says || '';
        box._n.textContent = j.whole ? ' - ' + j.done + ' / ' + j.whole : '';
        if ((j.lines || []).length) {
          box._log.hidden = false;
          box._log.textContent = j.lines.join('\n');
          noI18n(box._log);
          box._log.scrollTop = box._log.scrollHeight;
        }
      }});
      pollJob();
    });
  });
}

/* ---- the frame --------------------------------------------------------------- */
function frame() {
  var root = $('px-root');
  if (!root) return null;
  if (PX.root) return PX.root;
  clear(root);
  var top = el('div', 'px-top');
  var brand = el('div', 'px-brand');
  var logo = el('div', 'px-logo');
  logo.innerHTML = LOGO;
  brand.appendChild(logo);
  var who = el('div');
  who.appendChild(txt('b', '', 'Plex'));
  var sm = el('small');
  sm.id = 'px-srv';
  who.appendChild(sm);
  brand.appendChild(who);
  top.appendChild(brand);
  var job = el('div', 'px-job');
  job.id = 'px-job';
  job.innerHTML = '<div class="bar"><span></span></div><span class="w"></span>';
  job.title = 'What is being done in Plex now - one thing at a time.';
  top.appendChild(job);
  var views = el('div', 'px-views');
  views.setAttribute('role', 'tablist');
  VIEWS.forEach(function (v) {
    var b = el('button');
    b.type = 'button';
    b.dataset.view = v[0];
    b.innerHTML = iconMarkup(v[2], 15);
    b.appendChild(document.createTextNode(v[1]));
    b.onclick = function () { go(v[0]); };
    views.appendChild(b);
  });
  top.appendChild(views);
  root.appendChild(top);
  var body = el('div', 'px-main');
  body.id = 'px-main';
  root.appendChild(body);
  var dock = el('div', 'px-dock');
  dock.id = 'px-dock';
  document.body.appendChild(dock);
  PX.root = root;
  return root;
}
function paintTop() {
  var st = PX.st || {};
  var s = $('px-srv');
  if (s) {
    clear(s);
    var dot = el('span', 'px-dot ' + (st.reachable ? 'ok' : 'bad'));
    s.appendChild(dot);
    if (st.reachable) {
      s.appendChild(txt('span', '', (st.server || {}).name + ' · ' + (st.server || {}).version));
      if (st.playing) { s.appendChild(document.createTextNode(' · ')); s.appendChild(el('span', '', st.playing + ' playing now')); }
    } else {
      s.appendChild(el('span', '', 'not reachable'));
    }
  }
  document.querySelectorAll('.px-views button').forEach(function (b) {
    b.setAttribute('aria-selected', b.dataset.view === PX.view ? 'true' : 'false');
  });
  jobChip(st.job);
  if (st.job && st.job.running) pollJob();
}
function go(view, keep) {
  PX.view = view;
  remember();
  if (!keep) PX.tool = {};
  paintTop();
  if (typeof writeHash === 'function') writeHash();
  draw();
}
function main() { return $('px-main'); }

function draw() {
  var m = main();
  if (!m) return;
  dock();
  if (!PX.st) { clear(m); m.appendChild(skeleton()); return; }
  if (!PX.st.reachable) {
    clear(m);
    m.appendChild(emptyState('film', 'Plex Media Server does not answer',
      PX.st.error || 'plexmon asks it on 127.0.0.1:32400 with the server’s own token.'));
    var row = el('div', 'btnrow');
    row.style.justifyContent = 'center';
    var again = cmdButton('Ask again', 'refresh', 'primary');
    again.onclick = function () { start(true); };
    row.appendChild(again);
    m.appendChild(row);
    return;
  }
  if (PX.view === 'watchlist') return drawWatchlist();
  if (PX.view === 'titles') return drawTitles();
  if (PX.view === 'ratings') return drawRatings();
  if (PX.view === 'labels') return drawLabels();
  return drawLibrary();
}
function skeleton() {
  var g = el('div', 'px-grid');
  g.style.setProperty('--px-poster', PX.size + 'px');
  for (var i = 0; i < 18; i++) g.appendChild(el('div', 'px-skel'));
  return g;
}
function start(again) {
  frame();
  if (!again && PX.loaded) { paintTop(); draw(); return; }
  return api('plex').then(function (st) {
    PX.st = st;
    PX.loaded = true;
    var libs = st.sections || [];
    if (!libOf(PX.lib) && libs.length) PX.lib = libs[0].key;
    paintTop();
    draw();
  }).catch(function (e) {
    PX.st = {reachable: false, error: String(e.message || e)};
    paintTop();
    draw();
  });
}

/* ---- the library ------------------------------------------------------------- */
function items(lib, fresh) {
  if (!fresh && PX.data[lib] && Date.now() - PX.data[lib].got < 90000) return Promise.resolve(PX.data[lib]);
  return api('plex-items&lib=' + encodeURIComponent(lib) + (fresh ? '&fresh=1' : ''), null, 180).then(function (d) {
    d.got = Date.now();
    d.byRk = {};
    d.items.forEach(function (it) { d.byRk[it.rk] = it; });
    PX.data[lib] = d;
    return d;
  });
}
function libChips(host, pick) {
  var row = el('div', 'px-libs');
  (PX.st.sections || []).forEach(function (s) {
    var b = el('button', 'px-lib' + (s.key === PX.lib ? ' on' : ''));
    b.type = 'button';
    var ic = el('span', 'ic');
    ic.appendChild(ico(s.type === 'show' ? 'tv' : 'film', 18));
    b.appendChild(ic);
    var t = el('span');
    t.appendChild(txt('b', '', s.title));
    t.appendChild(el('small', '', s.count === null || s.count === undefined ? (s.type === 'show' ? 'shows' : 'movies')
      : s.count + (s.type === 'show' ? ' shows' : ' movies')));
    b.appendChild(t);
    b.onclick = function () { PX.lib = s.key; PX.sel = {}; remember(); if (typeof writeHash === 'function') writeHash(); pick(); };
    row.appendChild(b);
  });
  host.appendChild(row);
}
function drawLibrary() {
  var m = main();
  clear(m);
  libChips(m, drawLibrary);
  var lib = libOf(PX.lib);
  if (!lib) { m.appendChild(emptyState('film', 'No library of movies or shows', 'Plex has none yet.')); return; }
  var bar = el('div', 'px-bar');
  m.appendChild(bar);
  var gridHost = el('div');
  m.appendChild(gridHost);
  gridHost.appendChild(skeleton());
  items(PX.lib).then(function (d) {
    if (PX.view !== 'library' || PX.lib !== d.section.key) return;
    toolbar(bar, d);
    grid(gridHost, d);
  }).catch(function (e) {
    clear(gridHost);
    gridHost.appendChild(emptyState('film', 'The library could not be read', String(e.message || e)));
  });
}
function facets(d) {
  var lab = {}, gen = {}, cou = {};
  d.items.forEach(function (it) {
    (it.labels || []).forEach(function (x) { lab[x] = (lab[x] || 0) + 1; });
    (it.genres || []).forEach(function (x) { gen[x] = (gen[x] || 0) + 1; });
    (it.countries || []).forEach(function (x) { cou[x] = (cou[x] || 0) + 1; });
  });
  var list = function (o) { return Object.keys(o).sort(function (a, b) { return a.localeCompare(b); }).map(function (k) { return [k, k + ' (' + o[k] + ')']; }); };
  return {labels: list(lab), genres: list(gen), countries: list(cou)};
}
function toolbar(bar, d) {
  clear(bar);
  var sw = el('div', 'px-search');
  sw.appendChild(ico('search', 16));
  var q = el('input');
  q.type = 'search';
  q.placeholder = 'a title, a year… (* and ? as wildcards)';
  q.value = PX.q;
  q.oninput = debounce(function () { PX.q = q.value; grid(null, d); }, 160);
  sw.appendChild(q);
  bar.appendChild(sw);
  var show = seg([['all', 'All'], ['new', 'Unwatched'], ['part', 'In progress'], ['seen', 'Watched']], PX.show, function (v) {
    PX.show = v; remember(); toolbar(bar, d); grid(null, d);
  });
  bar.appendChild(show);
  var f = facets(d);
  var sel = function (now, pairs, empty, set) {
    var s = selectBox([['', empty]].concat(pairs), now);
    Array.prototype.slice.call(s.options, 1).forEach(function (o) { noI18n(o); });
    s.onchange = function () { set(s.value); grid(null, d); };
    return s;
  };
  /* "(items with no label)" as plex_tools has it (2.5.0). */
  var noLab = d.items.filter(function (it) { return !(it.labels || []).length; }).length;
  if (f.labels.length || noLab) {
    var ls = sel(PX.label, [['__none__', 'without a label (' + noLab + ')']].concat(f.labels), 'every label', function (v) { PX.label = v; });
    ls.options[1].removeAttribute('data-no-i18n');
    bar.appendChild(ls);
  }
  if (f.genres.length) bar.appendChild(sel(PX.genre, f.genres, 'every genre', function (v) { PX.genre = v; }));
  if (f.countries.length) bar.appendChild(sel(PX.country, f.countries, 'every country', function (v) { PX.country = v; }));
  /* On the watchlist or not (2.5.0, plex_tools' watchlist editor): the badge
     on the posters and this filter, from the owner's watchlist. */
  var wlf = selectBox([['', 'watchlist: all'], ['on', 'on the watchlist'], ['off', 'not on the watchlist']], PX.wlf || '');
  wlf.onchange = function () {
    PX.wlf = wlf.value;
    if (!PX.wlf) { grid(null, d); return; }
    wlKeys().then(function () { wlSet(); grid(null, d); }).catch(function (e) { PX.wlf = ''; wlf.value = ''; toast(String(e.message || e), 'bad'); });
  };
  bar.appendChild(wlf);
  var nr = el('button', 'px-chip' + (PX.norating ? ' on' : ''), 'without a rating');
  nr.type = 'button';
  nr.onclick = function () { PX.norating = !PX.norating; nr.classList.toggle('on', PX.norating); grid(null, d); };
  bar.appendChild(nr);
  var so = selectBox([['title', 'by title'], ['year', 'newest first'], ['added', 'lately added'],
                      ['rating', 'best rated'], ['viewed', 'lately watched']], PX.sort);
  so.onchange = function () { PX.sort = so.value; remember(); grid(null, d); };
  bar.appendChild(so);
  var size = el('input', 'px-size');
  size.type = 'range'; size.min = 110; size.max = 260; size.value = PX.size;
  size.title = 'The size of the posters';
  size.oninput = function () {
    PX.size = +size.value;
    var g = document.querySelector('#px-main .px-grid');
    if (g) g.style.setProperty('--px-poster', PX.size + 'px');
  };
  size.onchange = remember;
  bar.appendChild(size);
  var pick = cmdButton(PX.selecting ? 'Done' : 'Select', 'select', PX.selecting ? 'primary small' : 'small');
  explain(pick, 'Pick several titles - or Ctrl-click a poster - and change them together: watched, labels, the tracks, the ratings, the titles.');
  pick.onclick = function () { PX.selecting = !PX.selecting; if (!PX.selecting) PX.sel = {}; toolbar(bar, d); grid(null, d); };
  bar.appendChild(pick);
  var more = iconButton('refresh', 'Read the library again from Plex');
  more.onclick = function () { items(PX.lib, true).then(function (dd) { toolbar(bar, dd); grid(null, dd); toast('read again', 'good'); }).catch(oops); };
  bar.appendChild(more);
  if (ADMIN) {
    /* As plex_tools' F5 and F9 (2.4.4): the whole library, named and in
       sight - the keys work too while the library is in front. */
    var scan = cmdButton('Scan the library', 'search', 'small');
    explain(scan, 'F5 - Plex scans the files of ' + d.section.title + ': what is new on the disk comes in, what is gone goes. Nothing already there is changed.');
    scan.onclick = function () { libraryScan(d.section, false); };
    bar.appendChild(scan);
    var all = cmdButton('Refresh all metadata', 'refresh', 'small');
    explain(all, 'F9 - Plex downloads the metadata, posters and backgrounds of every item of ' + d.section.title + ' again (asked first).');
    all.onclick = function () { libraryScan(d.section, true); };
    bar.appendChild(all);
    PX.keysSection = d.section;
  }
  var c = el('span', 'px-count');
  c.id = 'px-count';
  bar.appendChild(c);
}
function filtered(d) {
  var mt = matcher(PX.q);
  var out = d.items.filter(function (it) {
    if (!(mt(it.title) || mt(it.orig) || mt(it.sort) || (it.year && mt(String(it.year))))) return false;
    if (PX.show !== 'all') {
      var s = seenState(it).k;
      if (PX.show === 'new' && s === 'seen') return false;
      if (PX.show === 'part' && s !== 'part') return false;
      if (PX.show === 'seen' && s !== 'seen') return false;
    }
    if (PX.label === '__none__') { if ((it.labels || []).length) return false; }
    else if (PX.label && (it.labels || []).indexOf(PX.label) < 0) return false;
    if (PX.wlf && PX.wlset) { var onw = !!PX.wlset[dkOf(it)]; if ((PX.wlf === 'on') !== onw) return false; }
    if (PX.genre && (it.genres || []).indexOf(PX.genre) < 0) return false;
    if (PX.country && (it.countries || []).indexOf(PX.country) < 0) return false;
    if (PX.norating && (it.rating || it.audience)) return false;
    return true;
  });
  var by = {
    title: function (a, b) { return (a.sort || a.title).localeCompare(b.sort || b.title); },
    year: function (a, b) { return (b.year || 0) - (a.year || 0); },
    added: function (a, b) { return (b.added || 0) - (a.added || 0); },
    rating: function (a, b) { return (+(b.rating || b.audience || 0)) - (+(a.rating || a.audience || 0)); },
    viewed: function (a, b) { return (b.viewed || 0) - (a.viewed || 0); }
  }[PX.sort] || null;
  if (by) out.sort(by);
  return out;
}
function grid(host, d) {
  host = host || (document.querySelector('#px-main .px-grid') || {}).parentNode;
  if (!host) return;
  clear(host);
  PX.shown = filtered(d);
  PX.drawn = 0;
  var c = $('px-count');
  if (c) c.textContent = PX.shown.length === d.items.length ? PX.shown.length + ' titles' : PX.shown.length + ' of ' + d.items.length;
  if (!PX.shown.length) {
    host.appendChild(emptyState('search', 'Nothing like that here', 'Another word, or fewer filters.'));
    dock();
    return;
  }
  var g = el('div', 'px-grid' + (PX.selecting ? ' px-selecting' : ''));
  g.style.setProperty('--px-poster', PX.size + 'px');
  host.appendChild(g);
  more(g, d);
  dock();
}
function more(g, d) {
  var end = Math.min(PX.shown.length, PX.drawn + CHUNK);
  var old = g.querySelector('.px-more');
  if (old) old.remove();
  for (var i = PX.drawn; i < end; i++) g.appendChild(card(PX.shown[i], i, d));
  PX.drawn = end;
  if (end < PX.shown.length) {
    var s = el('div', 'px-more');
    g.appendChild(s);
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (e) {
        if (e[0].isIntersecting) { io.disconnect(); more(g, d); }
      }, {rootMargin: '900px 0px'});
      io.observe(s);
    } else more(g, d);
  }
}
function card(it, i, d) {
  var c = el('div', 'px-card' + (PX.sel[it.rk] ? ' sel' : ''));
  c.tabIndex = 0;
  c.dataset.rk = it.rk;
  var p = el('div', 'px-poster');
  p.appendChild(txt('div', 'ph', it.title));
  p.appendChild(picture(it.thumb, PX.size + 40, (PX.size + 40) * 1.5, it.title));
  p.appendChild(el('div', 'shade'));
  var st = seenState(it);
  if (it.type === 'show' && st.left) p.appendChild(el('span', 'px-badge', String(st.left)));
  else if (st.k === 'seen') { var b = el('span', 'px-badge seen'); b.appendChild(ico('check', 13)); b.title = 'watched'; p.appendChild(b); }
  else if (it.type === 'movie' && st.k === 'new') { var nb = el('span', 'px-badge'); nb.style.cssText = 'min-width:12px;width:12px;height:12px;padding:0'; nb.title = 'unwatched'; p.appendChild(nb); }
  if (st.k === 'part' || (it.type === 'show' && st.p > 0 && st.p < 1)) {
    var pr = el('div', 'px-prog'); var ps = el('span'); ps.style.width = Math.round(st.p * 100) + '%'; pr.appendChild(ps); p.appendChild(pr);
  }
  var sc = bestScore(it);
  if (sc) { var r = el('span', 'px-rate'); r.textContent = '★ ' + sc; p.appendChild(r); }
  if (PX.wlset && PX.wlset[dkOf(it)]) { var wb = el('span', 'px-wlb'); wb.appendChild(ico('bookmark', 13)); wb.title = 'on the watchlist'; p.appendChild(wb); }
  var s = el('span', 'px-sel'); s.appendChild(ico('check', 15)); p.appendChild(s);
  c.appendChild(p);
  var cap = el('div', 'px-cap');
  cap.appendChild(txt('b', '', it.title));
  cap.appendChild(dotted(el('small'), [it.year ? txt('span', '', String(it.year)) : '',
    it.type === 'show' && it.seasons ? (it.seasons === 1 ? '1 season' : it.seasons + ' seasons') : '']));
  c.appendChild(cap);
  c.onclick = function (e) {
    if (PX.selecting || e.ctrlKey || e.metaKey || e.shiftKey) {
      if (window.getSelection) { try { window.getSelection().removeAllRanges(); } catch (er) { } }
      if (!PX.selecting) { PX.selecting = true; var g = c.parentNode; if (g) g.classList.add('px-selecting'); var bar = document.querySelector('#px-main .px-bar'); if (bar) toolbar(bar, d); }
      if (e.shiftKey && PX.lastPick !== null) {
        var a = Math.min(PX.lastPick, i), z = Math.max(PX.lastPick, i);
        for (var k = a; k <= z; k++) PX.sel[PX.shown[k].rk] = true;
        document.querySelectorAll('#px-main .px-card').forEach(function (x) { x.classList.toggle('sel', !!PX.sel[x.dataset.rk]); });
      } else {
        if (PX.sel[it.rk]) delete PX.sel[it.rk]; else PX.sel[it.rk] = true;
        c.classList.toggle('sel', !!PX.sel[it.rk]);
      }
      PX.lastPick = i;
      dock();
      return;
    }
    detail(it.rk);
  };
  c.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); c.onclick(e); } };
  return c;
}
function picked() { return Object.keys(PX.sel); }
function dock() {
  var dk = $('px-dock');
  if (!dk) return;
  var n = picked().length;
  var on = n > 0 && PX.view === 'library' && TAB === 'library';
  dk.classList.toggle('on', on);
  if (!on) return;
  clear(dk);
  dk.appendChild(el('span', 'n', n + ' selected'));
  var d = PX.data[PX.lib];
  var lib = libOf(PX.lib);
  var add = function (words, icon, fn, cls, adminOnly) {
    if (adminOnly && !ADMIN) return;
    var b = cmdButton(words, icon, 'small' + (cls ? ' ' + cls : ''));
    b.onclick = fn;
    dk.appendChild(b);
  };
  var icon = function (key, words, fn, adminOnly) {
    if (adminOnly && !ADMIN) return;
    var b = iconButton(key, words);
    b.onclick = fn;
    dk.appendChild(b);
  };
  add('Watched', 'check', function () { watched(picked(), true); }, '', true);
  add('Unwatched', 'eye', function () { watched(picked(), false); }, '', true);
  add('Flip each', 'swap', function () { watchedToggle(picked()); }, '', true);
  add('Audio & subtitles', 'subs', function () { tracksDialog(picked(), n + ' titles'); }, '', true);
  add('Labels', 'tag', function () { labelsDialog(PX.lib, picked()); }, '', true);
  add('Ratings', 'star', function () { PX.tool = {pre: picked()}; go('ratings', true); }, '', true);
  add('Titles', 'translate', function () { PX.tool = {pre: picked()}; go('titles', true); }, '', true);
  dk.appendChild(el('span', 'sep'));
  icon('bookmark', 'Put them on the watchlist', function () { watchlistAdd(picked()); }, true);
  icon('refresh', 'Refresh their metadata', function () { refreshItems(picked()); }, true);
  icon('download', 'Export their titles to a .txt file, one per line', function () { exportTitles(picked().map(function (k) { return d.byRk[k]; }).filter(Boolean), lib); });
  icon('select', 'Select every title shown', function () { PX.shown.forEach(function (it) { PX.sel[it.rk] = true; }); document.querySelectorAll('#px-main .px-card').forEach(function (x) { x.classList.add('sel'); }); dock(); });
  icon('close', 'Select none', function () { PX.sel = {}; document.querySelectorAll('#px-main .px-card.sel').forEach(function (x) { x.classList.remove('sel'); }); dock(); });
  /* plex_tools' i and - (2.5.0): the selection turned over among those shown,
     and those shown (a search, a filter) taken out of it. */
  var marks = function () { document.querySelectorAll('#px-main .px-card').forEach(function (x) { x.classList.toggle('sel', !!PX.sel[x.dataset.rk]); }); dock(); };
  icon('invert', 'Invert the selection of the titles shown', function () { PX.shown.forEach(function (it) { if (PX.sel[it.rk]) delete PX.sel[it.rk]; else PX.sel[it.rk] = true; }); marks(); });
  icon('minus', 'Unselect the titles shown (a search or a filter first)', function () { PX.shown.forEach(function (it) { delete PX.sel[it.rk]; }); marks(); });
}
function exportTitles(list, lib) {
  /* One per line; a variant of the same show ('X - Viki' beside 'X') is one line. */
  var titles = list.map(function (it) { return (it.title || '').trim(); }).filter(Boolean);
  var groups = {}, order = [];
  titles.forEach(function (t) {
    var parts = t.split(' - ');
    var base = parts.length > 1 && parts[parts.length - 1].trim() ? parts.slice(0, -1).join(' - ').trim() : t;
    var k = base.toLowerCase();
    if (!groups[k]) { groups[k] = {base: base, m: []}; order.push(k); }
    groups[k].m.push(t);
  });
  var out = order.map(function (k) {
    var g = groups[k];
    var plainOne = g.m.filter(function (x) { return x.toLowerCase() === g.base.toLowerCase(); })[0];
    return plainOne || g.m.slice().sort(function (a, b) { return a.length - b.length; })[0];
  });
  var blob = new Blob([out.join('\n') + '\n'], {type: 'text/plain;charset=utf-8'});
  var a = document.createElement('a');
  var stamp = new Date().toISOString().replace(/[-:]/g, '').slice(0, 15);
  a.download = 'plex_' + String((lib || {}).title || 'library').replace(/[^\w]+/g, '_') + '_' + stamp + '.txt';
  a.href = URL.createObjectURL(blob);
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  toast(out.length + ' titles exported', 'good');
}

/* ---- what is done to a few ---------------------------------------------------- */
function afterChange(lib) {
  var l = lib || PX.lib;
  delete PX.data[l];
  if (PX.view === 'library') items(l, true).then(function (d) { var bar = document.querySelector('#px-main .px-bar'); if (bar) toolbar(bar, d); grid(null, d); }).catch(function () { });
}
function watched(rks, on, after) {
  return follow(api('plex', {action: on ? 'watched' : 'unwatched', rks: rks})).then(function (r) {
    var got = r.done || r;
    toast((got.ok || 0) + (on ? ' marked watched' : ' marked unwatched') + (got.failed ? ', ' + got.failed + ' failed' : ''), got.failed ? 'bad' : 'good');
    afterChange();
    if (after) after();
  }).catch(oops);
}
/* Each one flipped (2.5.0, plex_tools' "toggle each"). */
function watchedToggle(rks, after) {
  confirmThen('Flip the watched state of each?',
    htmlText('Each movie and episode on its own: the watched ones become unwatched, the others watched. A show or a season is every episode of it.'),
    'Flip each', function () {
      follow(api('plex', {action: 'watched-toggle', rks: rks})).then(function (r) {
        toast((r.watched || 0) + ' now watched, ' + (r.unwatched || 0) + ' unwatched' + (r.failed ? ', ' + r.failed + ' failed' : ''), r.failed ? 'bad' : 'good');
        afterChange();
        if (after) after();
      }).catch(oops);
    }, false);
}
function refreshItems(rks) {
  confirmThen('Refresh the metadata of ' + rks.length + ' titles?',
    htmlText('Plex asks its agents again for each of them - the same match: watched state, positions, the files and their default tracks stay. It goes one after the other, gently.'),
    'Refresh', function () {
      follow(api('plex', {action: 'refresh', rks: rks})).then(function (r) { toast((r.ok || 0) + ' refreshed', 'good'); }).catch(oops);
    }, false);
}
function libraryScan(sec, force) {
  if (force) {
    confirmThen('Refresh all the metadata of ' + sec.title + '?',
      htmlText('Plex downloads the metadata, posters and backgrounds of every item again. On a big library that takes a long time and loads the server; the scan of the files is usually what you want.'),
      'Refresh it all', function () {
        api('plex', {action: 'scan', lib: sec.key, force: true}).then(function (r) { toast(r.message, 'good'); plexActivity(sec); }).catch(oops);
      });
    return;
  }
  api('plex', {action: 'scan', lib: sec.key}).then(function (r) { toast(r.message, 'good'); plexActivity(sec); }).catch(oops);
}
/* What Plex does after F5 / F9, followed to its end (2.5.0, as plex_tools'
   progress bar from /activities): a panel at the foot of the page with each
   of Plex's activities and how far it is; it goes away when Plex is done. */
var PXA = {timer: null, seen: false, quiet: 0, began: 0};
function plexActivity(sec) {
  var box = $('px-activity');
  if (!box) {
    box = el('div', 'px-activity');
    box.id = 'px-activity';
    document.body.appendChild(box);
  }
  clearTimeout(PXA.timer);
  PXA.seen = false; PXA.quiet = 0; PXA.began = Date.now();
  var stopIt = function () { clearTimeout(PXA.timer); PXA.timer = null; box.classList.remove('on'); };
  var look = function () {
    api('plex', {action: 'activities'}).then(function (r) {
      var acts = (r.activities || []).filter(function (a) { return a.type !== 'provider.subscriptions.process'; });
      if (acts.length) { PXA.seen = true; PXA.quiet = 0; } else PXA.quiet += 1;
      clear(box);
      if (!acts.length) {
        if ((PXA.seen && PXA.quiet >= 2) || (!PXA.seen && Date.now() - PXA.began > 20000)) {
          if (PXA.seen) { toast('Plex is done with ' + sec.title, 'good'); afterChange(sec.key); }
          stopIt();
          return;
        }
        var w = el('div', 'px-act');
        w.appendChild(el('b', '', 'Plex: waiting for it to begin…'));
        box.appendChild(w);
      }
      acts.slice(0, 4).forEach(function (a) {
        var w = el('div', 'px-act');
        var head = el('div', 'h');
        head.appendChild(txt('b', '', a.title || a.type || 'Plex'));
        var pct = typeof a.progress === 'number' && a.progress >= 0 ? Math.round(a.progress) : null;
        head.appendChild(el('span', 'muted small', pct === null ? '' : pct + ' %'));
        w.appendChild(head);
        if (a.subtitle) w.appendChild(txt('div', 'muted small', a.subtitle));
        var bar = el('div', 'px-prog2');
        var fill = el('span');
        fill.style.width = (pct === null ? 100 : pct) + '%';
        if (pct === null) bar.classList.add('busy');
        bar.appendChild(fill);
        w.appendChild(bar);
        box.appendChild(w);
      });
      var x = iconButton('close', 'Hide - Plex goes on');
      x.classList.add('px-act-x');
      x.onclick = stopIt;
      box.appendChild(x);
      box.classList.add('on');
      PXA.timer = setTimeout(look, 2000);
    }).catch(function () { PXA.timer = setTimeout(look, 5000); });
  };
  look();
}
function dkOf(it) { return it && /^plex:\/\//.test(it.guid || '') ? it.guid.split('/').pop() : ''; }
function wlSet() {
  var set = {};
  (WLK.keys || []).forEach(function (k) { set[k] = 1; });
  PX.wlset = set;
  return set;
}
/* Only what is not on it yet (2.5.0, plex_tools' "add"): those already on it
   and those Plex Discover does not know are counted, not sent. */
function watchlistAdd(rks) {
  var d = PX.data[PX.lib];
  wlKeys().then(function () {
    var set = wlSet();
    var already = 0, nomatch = 0, dks = [];
    rks.forEach(function (k) {
      var dk = dkOf(d.byRk[k]);
      if (!dk) nomatch++;
      else if (set[dk]) already++;
      else dks.push(dk);
    });
    var tail = (already ? ', ' + already + ' on it already' : '') + (nomatch ? ', ' + nomatch + ' without a match Plex Discover knows' : '');
    if (!dks.length) { toast('nothing to put on the watchlist' + tail, 'warn'); return; }
    api('plex', {action: 'watchlist-add', dks: dks}).then(function (r) {
      toast(r.ok + ' put on the watchlist' + (r.failed ? ', ' + r.failed + ' failed' : '') + tail, r.failed ? 'bad' : 'good');
      PX.wl = null; WLK.at = 0;
      wlKeys().then(function () { wlSet(); grid(null, d); }).catch(function () { });
    }).catch(oops);
  }).catch(oops);
}

/* ---- the detail of a title ----------------------------------------------------- */
function sheet() {
  var s = $('px-sheet');
  if (s) return s;
  s = el('div', 'px-sheet');
  s.id = 'px-sheet';
  /* A click beside it leaves it open: only its x (or Escape) closes it. */
  var veil = el('div', 'veil');
  s.appendChild(veil);
  var panel = el('div', 'px-panel');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  var x = el('button', 'px-x');
  x.type = 'button';
  x.setAttribute('aria-label', 'Close');
  x.appendChild(ico('close', 18));
  x.onclick = closeSheet;
  panel.appendChild(x);
  var sc = el('div', 'scroller');
  sc.id = 'px-sheet-body';
  panel.appendChild(sc);
  s.appendChild(panel);
  document.body.appendChild(s);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && s.classList.contains('on') && $('modal').hidden) closeSheet();
  });
  return s;
}
function closeSheet() {
  var s = $('px-sheet');
  if (s) s.classList.remove('on');
  document.documentElement.style.overflow = '';
  PX.open = null;
}
function detail(rk, season) {
  var s = sheet();
  var body = $('px-sheet-body');
  clear(body);
  var hero = el('div', 'px-hero');
  hero.appendChild(el('div', 'art'));
  var ps = el('div', 'pst');
  ps.appendChild(el('div', 'px-skel'));
  hero.appendChild(ps);
  body.appendChild(hero);
  s.classList.add('on');
  document.documentElement.style.overflow = 'hidden';
  PX.open = rk;
  return api('plex-item&rk=' + encodeURIComponent(rk), null, 60).then(function (r) {
    if (PX.open !== rk) return;
    var it = r.item;
    if (it.type === 'season' && it.parent && it.parent.rk) return detail(it.parent.rk, it.rk);
    drawDetail(it, season);
  }).catch(function (e) {
    clear(body);
    body.appendChild(emptyState('film', 'That title could not be read', String(e.message || e)));
  });
}
function scoreBadge(b) {
  var s = el('span', 'px-score ' + b.src);
  var i = el('i', '', b.label || '★');
  noI18n(i);
  s.appendChild(i);
  s.appendChild(txt('span', '', b.value));
  return s;
}
function drawDetail(it, seasonRk) {
  var body = $('px-sheet-body');
  clear(body);
  var hero = el('div', 'px-hero');
  var art = el('div', 'art');
  var artU = it.art || (it.grandparent || {}).art;
  if (artU) art.style.backgroundImage = 'url("' + imgUrl(artU, 1280, 720) + '")';
  hero.appendChild(art);
  var ep = it.type === 'episode';
  var ps = el('div', 'pst' + (ep ? ' wide' : ''));
  if (it.thumb) { var im = document.createElement('img'); im.src = imgUrl(it.thumb, ep ? 600 : 400, ep ? 338 : 600); im.alt = ''; ps.appendChild(im); }
  hero.appendChild(ps);
  var head = el('div', 'head');
  if (ep && it.grandparent && it.grandparent.title) {
    var up = el('button', 'px-link');
    up.type = 'button';
    up.textContent = it.grandparent.title + ' · S' + (it.parent.index || '?') + ' E' + (it.index || '?');
    noI18n(up);
    up.onclick = function () { detail(it.grandparent.rk, it.parent.rk); };
    head.appendChild(up);
  }
  head.appendChild(txt('h2', '', it.title));
  var sub = el('div', 'sub');
  var bits = [];
  if (it.year) bits.push(txt('span', '', String(it.year)));
  if (it.cr) bits.push(txt('span', '', it.cr));
  if (it.duration) bits.push(el('span', '', dur(it.duration)));
  if (it.type === 'show') {
    bits.push(el('span', '', (it.seasons || []).length === 1 ? '1 season' : (it.seasons || []).length + ' seasons'));
    bits.push(el('span', '', (it.leaves || 0) + ' episodes'));
  }
  if (it.studio) bits.push(txt('span', '', it.studio));
  bits.forEach(function (b, n) { if (n) sub.appendChild(el('span', 'sep', '•')); sub.appendChild(b); });
  head.appendChild(sub);
  var tags = el('div', 'px-tags');
  (it.media || []).slice(0, 1).forEach(function (md) {
    var r = res(md);
    if (r) tags.appendChild(txt('span', 'px-tag' + (r === '4K' ? ' k4' : ''), r));
    if (md.dr) tags.appendChild(txt('span', 'px-tag hdr', md.dr));
    if (md.vcodec) tags.appendChild(txt('span', 'px-tag', String(md.vcodec).toUpperCase()));
    if (md.acodec) tags.appendChild(txt('span', 'px-tag', String(md.acodec).toUpperCase() + (md.channels ? ' ' + (md.channels === 6 ? '5.1' : (md.channels === 8 ? '7.1' : md.channels + 'ch')) : '')));
  });
  (it.genres || []).slice(0, 4).forEach(function (g) { tags.appendChild(txt('span', 'px-tag', g)); });
  head.appendChild(tags);
  if ((it.ratings || []).length) {
    var sc = el('div', 'px-scores');
    it.ratings.forEach(function (b) { sc.appendChild(scoreBadge(b)); });
    head.appendChild(sc);
  }
  var acts = el('div', 'px-acts');
  if (ADMIN) {
    var seen = it.type === 'show' ? (it.leaves && it.seen >= it.leaves) : !!it.views;
    var w = cmdButton(seen ? 'Watched' : 'Mark watched', 'check', seen ? 'on' : 'primary');
    w.onclick = function () { watched([it.rk], !seen, function () { detail(it.rk); }); };
    acts.appendChild(w);
    if (it.type !== 'season') {
      var tr = cmdButton('Audio & subtitles', 'subs', '');
      tr.onclick = function () { tracksDialog([it.rk], it.title, function () { detail(it.rk); }); };
      acts.appendChild(tr);
    }
    if (it.type !== 'season') {
      var rt = cmdButton('Ratings', 'star', '');
      rt.onclick = function () { ratingsDialog([it.rk], it.title, it.type === 'show' ? 'the show, its seasons and every episode' : '', function () { detail(it.rk); }); };
      acts.appendChild(rt);
    }
    if (it.dk) {
      var wl = cmdButton('Watchlist', 'bookmark', 'wl');
      acts.appendChild(wl);
      wlKeys().then(function (keys) {
        var on = keys.indexOf(it.dk) >= 0;
        wl.classList.toggle('on', on);
        setButtonIcon(wl, 'bookmark', on ? 'On the watchlist' : 'Watchlist');
        wl.onclick = function () {
          api('plex', {action: on ? 'watchlist-remove' : 'watchlist-add', dks: [it.dk]}).then(function () {
            PX.wl = null; toast(on ? 'taken off the watchlist' : 'put on the watchlist', 'good'); detail(it.rk);
          }).catch(oops);
        };
      }).catch(function () { wl.disabled = true; wl.title = 'Plex Discover is not reachable from the server'; });
    }
    var rf = cmdButton('Refresh metadata', 'refresh', '');
    rf.onclick = function () { refreshItems([it.rk]); };
    acts.appendChild(rf);
  }
  var link = plexLink(it.rk);
  if (link) {
    var a = el('a', 'btn cmd');
    a.href = link; a.target = '_blank'; a.rel = 'noopener';
    a.innerHTML = iconMarkup('ext', 16);
    a.appendChild(document.createTextNode('Open in Plex'));
    acts.appendChild(a);
  }
  head.appendChild(acts);
  hero.appendChild(head);
  body.appendChild(hero);

  var grid2 = el('div', 'px-body');
  var left = el('div');
  var side = el('div', 'px-side');
  grid2.appendChild(left);
  grid2.appendChild(side);
  body.appendChild(grid2);
  if (it.tagline) left.appendChild(txt('p', 'px-tagline', it.tagline));
  if (it.summary) {
    var p = txt('p', 'px-summary clamp', it.summary);
    left.appendChild(p);
    setTimeout(function () {
      if (p.scrollHeight > p.clientHeight + 4) {
        var mo = el('button', 'px-link', 'read more');
        mo.type = 'button';
        mo.onclick = function () { p.classList.toggle('clamp'); mo.textContent = p.classList.contains('clamp') ? 'read more' : 'read less'; };
        left.insertBefore(mo, p.nextSibling);
      }
    }, 30);
  }
  if (it.type === 'show') seasonsBlock(left, it, seasonRk);
  if (it.type === 'movie' || it.type === 'episode') tracksBlock(left, it);
  if ((it.cast || []).length) {
    left.appendChild(el('h3', '', 'Cast'));
    var cast = el('div', 'px-cast');
    it.cast.forEach(function (c) {
      var pe = el('div', 'px-person');
      var f = el('div', 'face');
      f.appendChild(txt('span', '', (c.name || '?').split(/\s+/).map(function (x) { return x[0]; }).slice(0, 2).join('')));
      if (c.thumb) { var fi = picture(c.thumb, 120, 120, c.name); fi.onload = function () { fi.classList.add('in'); f.firstChild.remove(); }; f.appendChild(fi); }
      pe.appendChild(f);
      pe.appendChild(txt('b', '', c.name));
      if (c.role) pe.appendChild(txt('small', '', c.role));
      pe.onclick = function () { personDialog(c); };
      cast.appendChild(pe);
    });
    left.appendChild(cast);
  }
  /* The side: the facts. */
  side.appendChild(el('h3', '', 'About it'));
  var dl = el('dl', 'px-facts');
  var fact = function (k, v, raw) {
    if (!v || (Array.isArray(v) && !v.length)) return;
    dl.appendChild(el('dt', '', k));
    var dd = el('dd');
    if (v.nodeType) dd.appendChild(v); else { dd.textContent = Array.isArray(v) ? v.join(', ') : String(v); if (!raw) noI18n(dd); }
    dl.appendChild(dd);
  };
  fact('Director', it.directors);
  fact('Writers', it.writers);
  fact('Genres', it.genres);
  fact('Country', it.countries);
  fact('Studio', it.studio);
  fact('Released', it.aired);
  fact('Original title', it.orig && it.orig !== it.title ? it.orig : '');
  fact('Sort title', it.sort && it.sort !== it.title ? it.sort : '');
  fact('Added', it.added ? el('span', '', ago(it.added)) : '');
  fact('Last watched', it.viewed ? el('span', '', ago(it.viewed)) : '');
  fact('Metadata language', it.language ? txt('span', '', it.language) : (it.type === 'movie' || it.type === 'show' ? el('span', 'muted', 'the library’s') : ''));
  fact('Collections', it.collections);
  if ((it.locked || []).length) fact('Locked', it.locked.join(', '));
  (it.media || []).forEach(function (md) {
    fact('File', (md.files || []).join(', '));
    fact('Size', md.size ? el('span', '', bytes(md.size)) : '');
  });
  var ids = el('div', 'px-chips');
  var idl = {imdb: 'https://www.imdb.com/title/', tmdb: it.type === 'movie' ? 'https://www.themoviedb.org/movie/' : 'https://www.themoviedb.org/tv/', tvdb: 'https://thetvdb.com/?tab=series&id='};
  Object.keys(it.ids || {}).forEach(function (k) {
    if (!idl[k]) return;
    var a2 = el('a', 'px-chip', k.toUpperCase());
    noI18n(a2);
    a2.href = idl[k] + encodeURIComponent(it.ids[k]); a2.target = '_blank'; a2.rel = 'noopener';
    ids.appendChild(a2);
  });
  if (ids.childNodes.length) fact('Elsewhere', ids);
  side.appendChild(dl);
  if (it.type === 'movie' || it.type === 'show') labelsBlock(side, it);
}
var WLK = {at: 0, keys: null};
function wlKeys() {
  if (WLK.keys && Date.now() - WLK.at < 120000 && PX.wl !== null) return Promise.resolve(WLK.keys);
  return api('plex-watchlist', null, 60).then(function (r) { WLK.keys = r.keys || []; WLK.at = Date.now(); PX.wl = r; return WLK.keys; });
}
function labelsBlock(host, it) {
  host.appendChild(el('h3', '', 'Labels'));
  var row = el('div', 'px-chips');
  (it.labels || []).forEach(function (l) {
    var c = el('span', 'px-chip lab');
    c.appendChild(txt('span', '', l));
    if (ADMIN) {
      var x = el('button', '', '×');
      x.type = 'button';
      x.title = 'Take this label off';
      x.onclick = function () {
        api('plex', {action: 'labels', how: 'edit', lib: it.lib, rks: [it.rk], remove: [l]}).then(function () {
          toast('label taken off', 'good'); afterChange(it.lib); detail(it.rk);
        }).catch(oops);
      };
      c.appendChild(x);
    }
    row.appendChild(c);
  });
  if (!(it.labels || []).length) row.appendChild(el('span', 'muted small', 'no labels'));
  host.appendChild(row);
  if (!ADMIN) return;
  var add = el('div', 'btnrow');
  add.style.marginTop = '8px';
  var inp = textBox('', 'a new label…', '');
  inp.style.flex = '1';
  var go2 = cmdButton('Add', 'add', 'small');
  var put2 = function () {
    var v = inp.value.trim();
    if (!v) return;
    api('plex', {action: 'labels', how: 'edit', lib: it.lib, rks: [it.rk], add: [v]}).then(function () {
      toast('label added', 'good'); afterChange(it.lib); detail(it.rk);
    }).catch(oops);
  };
  go2.onclick = put2;
  inp.onkeydown = function (e) { if (e.key === 'Enter') put2(); };
  add.appendChild(inp); add.appendChild(go2);
  host.appendChild(add);
}
function tracksBlock(host, it) {
  var t = it.tracks || {};
  host.appendChild(el('h3', '', 'Audio and subtitles'));
  var g = el('div', 'px-tracks');
  var col = function (head, icon, list, offOn) {
    var c = el('div', 'px-tcol');
    var h = el('h4');
    h.appendChild(ico(icon, 15));
    h.appendChild(document.createTextNode(head));
    c.appendChild(h);
    if (offOn !== undefined) {
      var o = el('div', 'px-opt' + (offOn ? ' on' : ''));
      o.style.cursor = 'default';
      var l = el('div', 'l'); l.appendChild(el('b', '', 'Off')); o.appendChild(l);
      if (offOn) o.appendChild(el('span', 'now', 'default'));
      c.appendChild(o);
    }
    (list || []).forEach(function (v) {
      var o2 = el('div', 'px-opt' + (v.selected ? ' on' : ''));
      o2.style.cursor = 'default';
      var l2 = el('div', 'l');
      l2.appendChild(txt('b', '', v.label));
      var small = el('small');
      if (v.external) small.appendChild(el('span', 'px-mini ext', 'external'));
      if (v.forced) small.appendChild(el('span', 'px-mini forced', 'forced'));
      if (v.sdh) small.appendChild(el('span', 'px-mini', 'SDH'));
      l2.appendChild(small);
      o2.appendChild(l2);
      if (v.selected) o2.appendChild(el('span', 'now', 'default'));
      c.appendChild(o2);
    });
    if (!(list || []).length) c.appendChild(el('p', 'muted small', 'none'));
    return c;
  };
  g.appendChild(col('Audio', 'audio', t.audio));
  g.appendChild(col('Subtitles', 'subs', t.subs, !!t.subs_off));
  host.appendChild(g);
}
function seasonsBlock(host, it, seasonRk) {
  var seasons = it.seasons || [];
  if (!seasons.length) return;
  host.appendChild(el('h3', '', 'Seasons'));
  var strip = el('div', 'px-seasons');
  host.appendChild(strip);
  var bar = el('div', 'btnrow');
  bar.style.margin = '6px 0 10px';
  host.appendChild(bar);
  var eps = el('div', 'px-eps');
  host.appendChild(eps);
  var pick = {};
  var now = seasonRk || (seasons.filter(function (s) { return s.leaves && s.seen < s.leaves; })[0] || seasons[0]).rk;
  /* Ticked episodes stay ticked from season to season (2.5.0, plex_tools'
     episode list): watched, unwatched, each flipped, or their tracks. */
  var tickBar = el('div', 'btnrow px-ticked');
  tickBar.style.margin = '0 0 10px';
  host.insertBefore(tickBar, eps);
  var paintTick = function (sea) {
    clear(tickBar);
    var n = Object.keys(pick).length;
    tickBar.hidden = !ADMIN || !n;
    if (!n) return;
    var after = function () { pick = {}; detail(it.rk, sea.rk); };
    tickBar.appendChild(el('span', 'muted small', n === 1 ? '1 episode ticked:' : n + ' episodes ticked:'));
    var w1 = cmdButton('Watched', 'check', 'small');
    w1.onclick = function () { watched(Object.keys(pick), true, after); };
    var w2 = cmdButton('Unwatched', 'eye', 'small');
    w2.onclick = function () { watched(Object.keys(pick), false, after); };
    var w3 = cmdButton('Flip each', 'swap', 'small');
    w3.onclick = function () { watchedToggle(Object.keys(pick), after); };
    var w4 = cmdButton('Audio & subtitles', 'subs', 'small');
    w4.onclick = function () { tracksDialog(Object.keys(pick), it.title + ' · ' + Object.keys(pick).length + ' episodes', after); };
    var w5 = cmdButton('Untick all', 'close', 'small');
    w5.onclick = function () { pick = {}; eps.querySelectorAll('input.chk').forEach(function (c) { c.checked = false; c.parentNode.classList.remove('pick'); }); paintTick(sea); };
    [w1, w2, w3, w4, w5].forEach(function (b) { tickBar.appendChild(b); });
  };
  var drawEps = function (sea) {
    clear(eps); clear(bar);
    paintTick(sea);
    strip.querySelectorAll('.px-season').forEach(function (x) { x.classList.toggle('on', x.dataset.rk === sea.rk); });
    if (ADMIN) {
      var b1 = cmdButton('Season watched', 'check', 'small');
      b1.onclick = function () { watched([sea.rk], true, function () { detail(it.rk, sea.rk); }); };
      var b2 = cmdButton('Season unwatched', 'eye', 'small');
      b2.onclick = function () { watched([sea.rk], false, function () { detail(it.rk, sea.rk); }); };
      var b3 = cmdButton('Audio & subtitles of the season', 'subs', 'small');
      b3.onclick = function () { tracksDialog([sea.rk], it.title + ' · ' + sea.title, function () { detail(it.rk, sea.rk); }); };
      var b5 = cmdButton('Ratings of the season', 'star', 'small');
      b5.onclick = function () { ratingsDialog([sea.rk], it.title + ' · ' + sea.title, 'the season and its episodes', function () { detail(it.rk, sea.rk); }); };
      bar.appendChild(b5);
      var b6 = cmdButton('Season: flip each', 'swap', 'small');
      b6.onclick = function () { watchedToggle([sea.rk], function () { detail(it.rk, sea.rk); }); };
      [b1, b2, b6, b3].forEach(function (b) { bar.appendChild(b); });
    }
    eps.appendChild(el('div', 'px-skel'));
    eps.firstChild.style.cssText = 'aspect-ratio:auto;height:80px';
    api('plex-children&rk=' + encodeURIComponent(sea.rk)).then(function (r) {
      clear(eps);
      (r.items || []).forEach(function (e) {
        var row = el('div', 'px-ep');
        if (ADMIN) {
          var ck = el('input', 'chk'); ck.type = 'checkbox';
          ck.title = 'Tick episodes - in any season - to mark them watched, flip them or set their tracks together';
          ck.checked = !!pick[e.rk];
          row.classList.toggle('pick', ck.checked);
          ck.onchange = function () { if (ck.checked) pick[e.rk] = 1; else delete pick[e.rk]; row.classList.toggle('pick', ck.checked); paintTick(sea); };
          row.appendChild(ck);
        } else row.appendChild(el('span', 'num', String(e.index || '')));
        var shot = el('div', 'shot');
        shot.appendChild(picture(e.thumb, 300, 169, ''));
        if (e.offset && e.duration && !e.views) { var pr = el('div', 'px-prog'); var s = el('span'); s.style.width = Math.round(100 * e.offset / e.duration) + '%'; pr.appendChild(s); shot.appendChild(pr); }
        row.appendChild(shot);
        var t = el('div', 't');
        t.appendChild(txt('b', '', (e.index ? e.index + '. ' : '') + e.title));
        t.appendChild(dotted(el('small'), [e.aired ? txt('span', '', e.aired) : '', e.duration ? txt('span', '', dur(e.duration)) : '', e.rating ? txt('span', '', '★ ' + (+e.rating).toFixed(1)) : '']));
        if (e.summary) t.appendChild(txt('p', '', e.summary));
        t.style.cursor = 'pointer';
        t.onclick = function () { detail(e.rk); };
        row.appendChild(t);
        var sb = el('button', 'seen' + (e.views ? ' on' : ''));
        sb.type = 'button';
        sb.title = e.views ? 'Watched - press for unwatched' : 'Mark watched';
        sb.appendChild(ico('check', 16));
        sb.disabled = !ADMIN;
        sb.onclick = function () {
          var on = !e.views;
          api('plex', {action: on ? 'watched' : 'unwatched', rks: [e.rk]}).then(function () {
            e.views = on ? 1 : 0; sb.classList.toggle('on', on); afterChange(it.lib);
          }).catch(oops);
        };
        row.appendChild(sb);
        eps.appendChild(row);
      });
    }).catch(function (er) { clear(eps); eps.appendChild(el('p', 'note bad', String(er.message || er))); });
  };
  seasons.forEach(function (sea) {
    var c = el('div', 'px-season');
    c.dataset.rk = sea.rk;
    var p = el('div', 'px-poster');
    p.appendChild(txt('div', 'ph', sea.title));
    p.appendChild(picture(sea.thumb, 160, 240, sea.title));
    if (sea.leaves && sea.seen >= sea.leaves) { var b = el('span', 'px-badge seen'); b.appendChild(ico('check', 13)); p.appendChild(b); }
    else if (sea.leaves) p.appendChild(el('span', 'px-badge', String(sea.leaves - (sea.seen || 0))));
    if (sea.leaves && sea.seen && sea.seen < sea.leaves) { var pr = el('div', 'px-prog'); var s = el('span'); s.style.width = Math.round(100 * sea.seen / sea.leaves) + '%'; pr.appendChild(s); p.appendChild(pr); }
    c.appendChild(p);
    c.appendChild(txt('b', '', sea.title));
    c.appendChild(el('small', '', (sea.leaves || 0) + ' episodes'));
    c.onclick = function () { drawEps(sea); };
    strip.appendChild(c);
  });
  drawEps(seasons.filter(function (s) { return s.rk === now; })[0] || seasons[0]);
}
function personDialog(c) {
  modal(c.name, function (body) {
    noI18n($('modal-title'));
    var g = el('div', 'px-grid');
    g.style.setProperty('--px-poster', '120px');
    g.style.paddingBottom = '0';
    g.appendChild(el('div', 'px-skel'));
    body.appendChild(g);
    api('plex-person&id=' + encodeURIComponent(c.id) + '&name=' + encodeURIComponent(c.name)).then(function (r) {
      clear(g);
      if (!(r.items || []).length) { g.appendChild(el('p', 'muted', 'Nothing else with this person on this server.')); return; }
      r.items.forEach(function (it) {
        var cc = el('div', 'px-card');
        var p = el('div', 'px-poster');
        p.appendChild(txt('div', 'ph', it.title));
        p.appendChild(picture(it.thumb, 160, 240, it.title));
        cc.appendChild(p);
        var cap = el('div', 'px-cap');
        cap.appendChild(txt('b', '', it.title));
        cap.appendChild(dotted(el('small'), [it.year ? txt('span', '', String(it.year)) : '', it.lib_title ? txt('span', '', it.lib_title) : '']));
        cc.appendChild(cap);
        cc.onclick = function () { closeModal(); detail(it.rk); };
        g.appendChild(cc);
      });
    }).catch(function (e) { clear(g); g.appendChild(el('p', 'note bad', String(e.message || e))); });
  }, null, 'px-wide');
}

/* ---- the default audio and subtitles ------------------------------------------- */
function tracksDialog(rks, what, after) {
  var T = {scan: null, audio: 'keep', subs: 'keep', ma: 'skip', ms: 'skip'};
  var step1 = function () {
    wideModal('Audio and subtitles · ' + what, function (body) {
      body.appendChild(note('safe', 'Only <b>which track plays by default</b> changes - the files, the subtitles themselves, watched state and positions stay exactly as they are. Nothing is changed before you see the plan.'));
      var w = el('div');
      w.style.marginTop = '14px';
      w.appendChild(el('div', 'px-skel'));
      w.firstChild.style.cssText = 'aspect-ratio:auto;height:120px';
      body.appendChild(w);
      api('plex', {action: 'tracks-scan', rks: rks}, 180).then(function (r) {
        T.scan = r;
        choose(w);
      }).catch(function (e) { clear(w); w.appendChild(el('p', 'note bad', String(e.message || e))); });
    }, [{words: 'Close', close: true}]);
  };
  var choose = function (w) {
    clear(w);
    var r = T.scan;
    var rd = el('p', 'muted small');
    rd.appendChild(el('span', '', r.videos === 1 ? '1 video read' : r.videos + ' videos read'));
    rd.appendChild(document.createTextNode(' · '));
    rd.appendChild(el('span', '', 'a track is the same track in every one of them - an external .srt is told from one inside the file, forced from SDH.'));
    w.appendChild(rd);
    var g = el('div', 'px-tracks');
    var col = function (kind, head, icon, list) {
      var c = el('div', 'px-tcol');
      var h = el('h4'); h.appendChild(ico(icon, 15)); h.appendChild(document.createTextNode(head)); c.appendChild(h);
      var opts = [{key: 'keep', label: 'Leave it as it is'}];
      if (kind === 'subs') opts.push({key: 'off', label: 'Off - no subtitles'});
      opts = opts.concat(list);
      opts.forEach(function (v) {
        var o = el('label', 'px-opt' + (T[kind] === v.key ? ' on' : ''));
        var rd = el('input'); rd.type = 'radio'; rd.name = 'px-' + kind; rd.checked = T[kind] === v.key;
        rd.onchange = function () { T[kind] = v.key; choose(w); };
        o.appendChild(rd);
        var l = el('div', 'l');
        var b = el('b', '', v.label);
        if (v.count !== undefined) noI18n(b);
        l.appendChild(b);
        if (v.count !== undefined) {
          var sm = el('small');
          if (v.external) sm.appendChild(el('span', 'px-mini ext', 'external'));
          if (v.forced) sm.appendChild(el('span', 'px-mini forced', 'forced'));
          if (v.sdh) sm.appendChild(el('span', 'px-mini', 'SDH'));
          if (v.codec) sm.appendChild(txt('span', 'px-mini', v.codec.toUpperCase()));
          l.appendChild(sm);
        }
        o.appendChild(l);
        if (v.count !== undefined) {
          o.appendChild(el('span', 'cnt', 'in ' + v.count + ' of ' + r.videos));
          if (v.selected) o.appendChild(el('span', 'now', v.selected >= r.videos ? 'default' : 'default in ' + v.selected));
        }
        c.appendChild(o);
      });
      /* Where the one chosen is missing. */
      var chosen = list.filter(function (v) { return v.key === T[kind]; })[0];
      if (chosen && chosen.count < r.videos) {
        var miss = el('div', 'px-note warn');
        miss.style.marginTop = '8px';
        var mt = el('div');
        mt.appendChild(el('div', '', (r.videos - chosen.count) + ' of ' + r.videos + ' do not have it. There:'));
        /* Replacement after replacement (2.5.0, plex_tools' coverage loop):
           where the one chosen is missing the next is tried, then the next. */
        var tk = kind === 'audio' ? 'ma' : 'ms';
        var chainBox = el('div', 'px-chain');
        var paintChain = function () {
          clear(chainBox);
          var keys = String(T[tk] || 'skip').split(',').filter(Boolean);
          var used = [chosen.key];
          var shown = 0;
          for (var i = 0; i <= keys.length; i++) {
            var cur = keys[i] || 'skip';
            var pairs = [['skip', i ? 'and where that is missing too: leave them' : 'leave them as they are']];
            if (kind === 'subs') pairs.push(['off', 'subtitles off']);
            list.forEach(function (v) { if (used.indexOf(v.key) < 0 || v.key === cur) pairs.push([v.key, (i ? 'then ' : '') + v.label]); });
            (function (idx) {
              var sb = selectBox(pairs, cur);
              sb.style.marginTop = '6px';
              sb.onchange = function () {
                var k2 = keys.slice(0, idx);
                if (sb.value !== 'skip') k2.push(sb.value);
                T[tk] = k2.length ? k2.join(',') : 'skip';
                paintChain();
              };
              chainBox.appendChild(sb);
            }(i));
            shown++;
            if (cur === 'skip' || cur === 'off') break;
            used.push(cur);
          }
        };
        paintChain();
        mt.appendChild(chainBox);
        miss.appendChild(mt);
        c.appendChild(miss);
      }
      return c;
    };
    g.appendChild(col('audio', 'Audio', 'audio', r.audio));
    g.appendChild(col('subs', 'Subtitles', 'subs', r.subs));
    w.appendChild(g);
    var row = el('div', 'btnrow');
    row.style.marginTop = '14px';
    var rescan = cmdButton('Scan the files again', 'search', '');
    explain(rescan, 'A subtitle file just put beside the video is not a track until Plex has scanned its folder: Plex is asked to scan just the folders of these videos, and they are read again once it is done - up to two minutes.');
    rescan.onclick = function () {
      follow(api('plex', {action: 'tracks-rescan', scan: r.scan}), w).then(function (got) {
        T.scan = Object.assign({}, T.scan, got);
        choose(w);
        /* What the scan found (2.4.3): before, only "read again", whatever it was. */
        var n = (got && got.changed) || 0;
        toast(n ? n + ' video(s) have new tracks'
                : 'Plex scanned the folders and found no new tracks - a file beside the video needs the video\'s own name (Name.cs.srt, Name.forced.cz.srt)', n ? 'good' : 'warn');
      }).catch(function (e) { toast(String(e.message || e), 'bad'); choose(w); });
    };
    var next = cmdButton('Show the plan', 'next', 'primary');
    next.disabled = T.audio === 'keep' && T.subs === 'keep';
    next.onclick = function () { plan(w); };
    row.appendChild(rescan);
    row.appendChild(el('span', 'spacer'));
    row.appendChild(next);
    w.appendChild(row);
  };
  var plan = function (w) {
    api('plex', {action: 'tracks-plan', scan: T.scan.scan, audio: T.audio, subs: T.subs,
                 missing_audio: T.ma, missing_subs: T.ms}).then(function (p) {
      clear(w);
      var sum = el('div', 'px-sum');
      var met = function (v, k, cls) { var m = el('div', 'metric ' + (cls || '')); m.innerHTML = '<div class="label"></div><div class="value"></div>'; m.firstChild.textContent = k; m.lastChild.textContent = v; return m; };
      sum.appendChild(met(p.change, 'videos change', 'ok'));
      if (p.audio_missing) sum.appendChild(met(p.audio_missing, 'without that audio', 'warn'));
      if (p.subs_missing) sum.appendChild(met(p.subs_missing, 'without those subtitles', 'warn'));
      w.appendChild(sum);
      var box = el('div', 'px-plan');
      var tb = el('table');
      tb.innerHTML = '<thead><tr><th></th><th></th><th></th></tr></thead>';
      var th = tb.querySelectorAll('th');
      th[0].textContent = 'Video'; th[1].textContent = 'Audio'; th[2].textContent = 'Subtitles';
      var bodyT = el('tbody');
      p.rows.forEach(function (r) {
        var tr = el('tr');
        var parts = r.parts || [];
        var miss = parts.some(function (x) { return x.audio_missing || x.subs_missing; });
        if (miss) tr.className = 'miss';
        tr.appendChild(txt('td', '', r.label));
        var a = parts.some(function (x) { return x.audio_missing; }) ? el('td', 'gone', 'not there - left') : (parts.some(function (x) { return x.audio_alt; }) ? txt('td', 'to', '→ ' + parts[0].audio_alt) : (T.audio === 'keep' ? el('td', 'muted', 'as it is') : txt('td', 'to', '→ ' + p.words.audio)));
        var s = parts.some(function (x) { return x.subs_missing; }) ? el('td', 'gone', 'not there - left') : (parts.some(function (x) { return x.subs_alt; }) ? txt('td', 'to', '→ ' + (parts[0].subs_alt === 'off' ? 'off' : parts[0].subs_alt)) : (T.subs === 'keep' ? el('td', 'muted', 'as it is') : txt('td', 'to', '→ ' + p.words.subs)));
        tr.appendChild(a); tr.appendChild(s);
        bodyT.appendChild(tr);
      });
      tb.appendChild(bodyT);
      box.appendChild(tb);
      w.appendChild(box);
      var row = el('div', 'btnrow');
      row.style.marginTop = '14px';
      var back = cmdButton('Back', 'prev', '');
      back.onclick = function () { choose(w); };
      var goB = cmdButton('Set it on ' + p.change + ' videos', 'check', 'primary');
      goB.disabled = !p.change;
      goB.onclick = function () {
        var host = el('div');
        w.appendChild(host);
        row.hidden = true;
        follow(api('plex', {action: 'tracks-apply', scan: T.scan.scan}), host).then(function (res2) {
          clear(host);
          host.appendChild(note('safe', '<span><b>' + res2.ok + '</b> set.</span>' + (res2.failed ? ' <span><b>' + res2.failed + '</b> failed.</span>' : '') + ' <span>The apps show it the next time the video is opened.</span>'));
          toast(res2.ok + ' videos set', res2.failed ? 'bad' : 'good');
          if (after) after();
        }).catch(function (e) { row.hidden = false; toast(String(e.message || e), 'bad'); });
      };
      row.appendChild(back); row.appendChild(el('span', 'spacer')); row.appendChild(goB);
      w.appendChild(row);
    }).catch(oops);
  };
  step1();
}


/* ---- ratings of one title (2.1.3) ----------------------------------------------
   The badges Plex shows (IMDb, Rotten Tomatoes, TMDB) are its agent's: Plex
   lets a client write two numbers - the critic rating and the audience
   rating - and draws each with the badge of its source. So: either Plex is
   asked for its own again (a refresh), or the numbers of the other databases
   are written, each into the field whose badge is that database. */
var SRC_BADGE = {imdb: ['imdb', 'IMDb', 'ten'], rottentomatoes: ['rt', 'RT', 'pct'], tmdb: ['tmdb', 'TMDB', 'pct'],
                 tvmaze: ['tvmaze', 'TVmaze', 'ten'], trakt: ['trakt', 'Trakt', 'ten']};
function srcBadge(src, v) {
  var b = SRC_BADGE[src] || ['plex', '★', 'ten'];
  return scoreBadge({src: b[0], label: b[1], value: b[2] === 'pct' ? Math.round(v * 10) + '%' : (+v).toFixed(1)});
}
function ratingsRows(host, r, pick) {
  var list = el('div', 'px-rows');
  (r.rows || []).slice(0, 1500).forEach(function (e) {
    var can = e.status === 'add' || e.status === 'update';
    var row = el('label', 'px-row' + (pick[e.rk] ? ' pick' : ''));
    var ck = el('input'); ck.type = 'checkbox'; ck.checked = !!pick[e.rk]; ck.disabled = !can;
    ck.onchange = function () { if (ck.checked) pick[e.rk] = 1; else delete pick[e.rk]; row.classList.toggle('pick', ck.checked); if (host._count) host._count(); };
    row.appendChild(ck);
    var th = el('div', 'th'); th.appendChild(picture(e.thumb, 88, 132, '')); row.appendChild(th);
    var mm = el('div', 'main');
    mm.appendChild(txt('b', '', e.label));
    var line = el('div', 'px-rline');
    if ((e.badges || []).length) e.badges.forEach(function (bd) { line.appendChild(scoreBadge(bd)); });
    else line.appendChild(el('span', 'muted small', 'no rating now'));
    if (can) {
      line.appendChild(el('span', 'px-arrow', '→'));
      if (e.new !== undefined && e.new !== null) { line.appendChild(el('span', 'muted small', 'critics')); line.appendChild(srcBadge(e.new_src, e.new)); }
      if (e.new_aud !== undefined && e.new_aud !== null) { line.appendChild(el('span', 'muted small', 'audience')); line.appendChild(srcBadge(e.new_aud_src, e.new_aud)); }
    }
    mm.appendChild(line);
    var f = Object.keys(e.found || {});
    if (f.length) {
      var all = el('div', 'px-found');
      all.appendChild(el('span', 'muted small', 'found:'));
      f.forEach(function (k) { all.appendChild(srcBadge(k, e.found[k])); });
      mm.appendChild(all);
    }
    row.appendChild(mm);
    var side = el('div', 'side');
    side.appendChild(el('span', 'tag ' + (e.status === 'add' ? 'ok' : (e.status === 'update' ? 'warn' : '')), e.status === 'same' ? 'as it is' : e.status));
    row.appendChild(side);
    list.appendChild(row);
  });
  host.appendChild(list);
}
function ratingsDialog(rks, what, scope, after) {
  var S = PX.st.sources || {};
  var opt = {overwrite: true, lock: false};
  wideModal('Ratings · ' + what, function (body) {
    noI18n($('modal-title').lastChild || $('modal-title'));
    body.appendChild(note('', '<span>The badges in Plex (IMDb, Rotten Tomatoes, TMDB) come from its own agent; Plex lets a client write only <b>two numbers</b> - the critic rating and the audience rating - and draws each with the badge of its source.</span> <span>So either ask Plex for its own again, or write the numbers of the other databases: each goes into the field whose badge is that database.</span>'));
    var w = el('div');
    w.style.marginTop = '14px';
    body.appendChild(w);
    var start = function () {
      clear(w);
      var cards = el('div', 'cx-grid');
      var a = el('button', 'cx-fmt');
      a.type = 'button';
      var at = el('div', 'cx-top'); var ai = el('span', 'cx-fi'); ai.innerHTML = iconMarkup('refresh', 17); at.appendChild(ai); at.appendChild(el('b', '', 'Plex’s own ratings again'));
      a.appendChild(at);
      a.appendChild(el('p', '', 'Plex refreshes the metadata and fetches its badges anew - IMDb, Rotten Tomatoes, TMDB as its agent has them. The same match: watched state and tracks stay.'));
      a.onclick = function () {
        follow(api('plex', {action: 'refresh', rks: rks}), w).then(function (x) {
          clear(w); w.appendChild(note('safe', '<span>Plex is fetching them - the badges change within a minute.</span>'));
          toast((x.ok || 0) + ' refreshed', 'good'); if (after) setTimeout(after, 4000);
        }).catch(oops);
      };
      cards.appendChild(a);
      var bE = el('button', 'cx-fmt on');
      bE.type = 'button';
      var bt = el('div', 'cx-top'); var bi = el('span', 'cx-fi'); bi.innerHTML = iconMarkup('star', 17); bt.appendChild(bi); bt.appendChild(el('b', '', 'From the databases'));
      bE.appendChild(bt);
      var src = el('div', 'px-src');
      src.style.marginTop = '8px';
      [['imdb', 'IMDb'], ['rottentomatoes', 'Rotten Tomatoes'], ['tmdb', 'TMDB'], ['tvmaze', 'TVmaze'], ['trakt', 'Trakt']].forEach(function (x) {
        var sp = el('span', S[x[0]] ? 'on' : ''); sp.textContent = x[1]; noI18n(sp); src.appendChild(sp);
      });
      bE.appendChild(src);
      bE.appendChild(el('p', '', 'Looked up and shown first' + (scope ? ' - ' + scope : '') + '; written when you say so.'));
      cards.appendChild(bE);
      w.appendChild(cards);
      var opts = el('div', 'btnrow');
      opts.style.marginTop = '14px';
      var o1 = el('label', 'check small'); var c1 = el('input'); c1.type = 'checkbox'; c1.checked = opt.overwrite;
      c1.onchange = function () { opt.overwrite = c1.checked; }; o1.appendChild(c1); o1.appendChild(el('span', '', 'also where there is a rating already'));
      var o2 = el('label', 'check small'); var c2 = el('input'); c2.type = 'checkbox'; c2.checked = opt.lock;
      c2.onchange = function () { opt.lock = c2.checked; }; o2.appendChild(c2); o2.appendChild(el('span', '', 'lock them (a refresh by Plex keeps them)'));
      opts.appendChild(o1); opts.appendChild(o2);
      opts.appendChild(el('span', 'spacer'));
      var cfg = cmdButton('The sources…', 'gear', 'small');
      cfg.onclick = function () { closeModal(); closeSheet(); PANE = 'plex'; showTab('settings'); if (typeof showPane === 'function') showPane('plex'); };
      opts.appendChild(cfg);
      var look = cmdButton('Look them up', 'search', 'primary');
      look.disabled = !Object.keys(S).some(function (k) { return S[k]; });
      look.onclick = function () { lookup(); };
      opts.appendChild(look);
      w.appendChild(opts);
    };
    var lookup = function () {
      follow(api('plex', {action: 'ratings-preview', lib: '', rks: rks, overwrite: opt.overwrite}), w).then(function (r) {
        clear(w);
        var pick = {};
        r.rows.forEach(function (e) { if (e.status === 'add' || e.status === 'update') pick[e.rk] = 1; });
        var c = r.counts || {};
        var sum = el('div', 'px-sum');
        var met = function (v, k, cls) { var m = el('div', 'metric ' + (cls || '')); m.innerHTML = '<div class="label"></div><div class="value"></div>'; m.firstChild.textContent = k; m.lastChild.textContent = v; return m; };
        sum.appendChild(met(c.add || 0, 'added', 'ok'));
        sum.appendChild(met(c.update || 0, 'updated', 'warn'));
        sum.appendChild(met((c.same || 0) + (c.kept || 0), 'kept'));
        sum.appendChild(met(c['no match'] || 0, 'no match'));
        w.appendChild(sum);
        ratingsRows(w, r, pick);
        var row = el('div', 'btnrow');
        row.style.marginTop = '14px';
        var back = cmdButton('Back', 'prev', '');
        back.onclick = start;
        var goB = cmdButton('Write', 'check', 'primary');
        w._count = function () { var n = Object.keys(pick).length; setButtonIcon(goB, 'check', 'Write ' + n + ' ratings'); goB.disabled = !n; };
        goB.onclick = function () {
          var h2 = el('div'); w.appendChild(h2); row.hidden = true;
          follow(api('plex', {action: 'ratings-apply', plan: r.plan_id, lock: !!opt.lock, only: Object.keys(pick)}), h2).then(function (x) {
            toast(x.ok + ' ratings written' + (x.failed ? ', ' + x.failed + ' failed' : ''), x.failed ? 'bad' : 'good');
            closeModal(); afterChange(); if (after) after();
          }).catch(function (e) { row.hidden = false; toast(String(e.message || e), 'bad'); });
        };
        row.appendChild(back); row.appendChild(el('span', 'spacer')); row.appendChild(goB);
        w.appendChild(row);
        w._count();
      }).catch(function (e) { toast(String(e.message || e), 'bad'); start(); });
    };
    start();
  }, [{words: 'Close', close: true}]);
}

/* ---- labels of a few ------------------------------------------------------------ */
function labelsDialog(lib, rks, after) {
  var rows = [];
  wideModal('Labels of ' + rks.length + ' titles', function (body) {
    var w = el('div');
    w.appendChild(el('div', 'px-skel'));
    w.firstChild.style.cssText = 'aspect-ratio:auto;height:120px';
    body.appendChild(w);
    api('plex-labels&lib=' + encodeURIComponent(lib), null, 90).then(function (r) {
      var set = {};
      rks.forEach(function (k) { set[k] = 1; });
      rows = (r.labels || []).map(function (g) {
        var n = (g.items || []).filter(function (k) { return set[k]; }).length;
        var st = n === 0 ? 'off' : (n === rks.length ? 'on' : 'mixed');
        return {g: g, n: n, orig: st, st: st};
      });
      var draw2 = function () {
        clear(w);
        w.appendChild(el('p', 'muted small', 'Press a label: on all of them, on none, or back as it was (a dash: on some). The same label in another letter case is one row.'));
        var tri = el('div', 'px-tri');
        rows.forEach(function (x) {
          var b = el('button', x.st + (x.st !== x.orig ? ' chg' : ''));
          b.type = 'button';
          var box = el('span', 'box', x.st === 'on' ? '✓' : (x.st === 'mixed' ? '–' : ''));
          b.appendChild(box);
          b.appendChild(txt('span', '', x.g.title));
          b.appendChild(el('small', '', x.n + ' of ' + rks.length + (x.st !== x.orig ? ' → ' + (x.st === 'on' ? rks.length : (x.st === 'off' ? 0 : x.n)) : '')));
          b.onclick = function () {
            var cyc = x.orig === 'mixed' ? ['mixed', 'on', 'off'] : (x.orig === 'on' ? ['on', 'off'] : ['off', 'on']);
            x.st = cyc[(cyc.indexOf(x.st) + 1) % cyc.length];
            draw2();
          };
          tri.appendChild(b);
        });
        if (!rows.length) tri.appendChild(el('p', 'muted', 'This library has no labels yet.'));
        w.appendChild(tri);
        var add = el('div', 'btnrow');
        add.style.marginTop = '10px';
        var inp = textBox('', 'a new label…', '');
        inp.style.flex = '1';
        var ab = cmdButton('Add it', 'add', 'small');
        var put3 = function () {
          var v = inp.value.trim();
          if (!v) return;
          var have = rows.filter(function (x) { return x.g.title.toLowerCase() === v.toLowerCase(); })[0];
          if (have) have.st = 'on';
          else rows.unshift({g: {title: v, names: [v], items: []}, n: 0, orig: 'off', st: 'on', fresh: true});
          draw2();
        };
        ab.onclick = put3;
        inp.onkeydown = function (e) { if (e.key === 'Enter') put3(); };
        add.appendChild(inp); add.appendChild(ab);
        w.appendChild(add);
        /* Rename a label on these titles only (2.5.0, plex_tools' editor):
           the rest of the library keeps it as it is. */
        var carried = rows.filter(function (x) { return x.n > 0 && !x.fresh; });
        if (carried.length) {
          var rn = el('div', 'btnrow');
          rn.style.marginTop = '10px';
          var pickL = selectBox(carried.map(function (x) { return [x.g.title, x.g.title + ' (' + x.n + ')']; }), carried[0].g.title);
          var to = textBox('', 'its new name…', '');
          to.style.flex = '1';
          var rb = cmdButton('Rename it on these', 'edit', 'small');
          explain(rb, 'Only on these titles: the label is taken off them and the new one put on - elsewhere in the library it stays as it is.');
          rb.onclick = function () {
            var nv = to.value.trim();
            if (!nv) { toast('give the new name', 'warn'); return; }
            busy(rb, true);
            api('plex', {action: 'labels', how: 'rename', lib: lib, rks: rks, old: pickL.value, new: nv}).then(function (r) {
              busy(rb, false);
              toast('renamed on ' + r.done + ' titles', 'good');
              afterChange(lib);
              closeModal();
              if (after) after();
            }).catch(function (e) { busy(rb, false); oops(e); });
          };
          rn.appendChild(el('span', 'muted small', 'Rename on these only:'));
          rn.appendChild(pickL); rn.appendChild(to); rn.appendChild(rb);
          w.appendChild(rn);
        }
      };
      draw2();
    }).catch(function (e) { clear(w); w.appendChild(el('p', 'note bad', String(e.message || e))); });
  }, [{words: 'Close', close: true}, {words: 'Save the labels', cls: 'primary', icon: 'save', go: function () {
    var add = [], rem = [];
    rows.forEach(function (x) {
      if (x.st === x.orig) return;
      if (x.st === 'on') add.push(x.g.title);
      if (x.st === 'off') rem = rem.concat(x.g.names || [x.g.title]);
    });
    if (!add.length && !rem.length) { toast('nothing changed', ''); return; }
    api('plex', {action: 'labels', how: 'edit', lib: lib, rks: rks, add: add, remove: rem}).then(function (r) {
      closeModal();
      toast('labels changed on ' + r.done + ' titles', 'good');
      afterChange(lib);
      if (after) after();
    }).catch(oops);
  }}]);
}

/* ---- the watchlist ----------------------------------------------------------------- */
var WLS = {on: false, sel: {}};
/* A title not on this server, as Plex Discover knows it (2.5.0, plex_tools'
   watchlist detail): the plot, the ratings, genres, director and cast. */
function discoverDetail(w, after) {
  wideModal(w.title, function (body) {
    noI18n($('modal-title'));
    var row = el('div', 'px-row px-disc');
    row.style.gridTemplateColumns = '150px minmax(0,1fr)';
    var th = el('div', 'th'); th.style.width = '150px'; th.appendChild(picture(w.thumb, 300, 450, '')); row.appendChild(th);
    var mm = el('div', 'main');
    mm.appendChild(txt('b', '', w.title + (w.year ? ' (' + w.year + ')' : '')));
    mm.appendChild(el('small', '', 'not on this server'));
    var more2 = el('div');
    more2.appendChild(el('div', 'px-skel'));
    more2.firstChild.style.cssText = 'aspect-ratio:auto;height:90px;margin-top:10px';
    mm.appendChild(more2);
    row.appendChild(mm);
    body.appendChild(row);
    api('plex-discover&dk=' + encodeURIComponent(w.dk), null, 40).then(function (d) {
      clear(more2);
      var facts = [d.released || '', d.cr || '', d.duration ? dur(d.duration) : '', d.seasons ? d.seasons + ' seasons' : '', d.studio || ''].filter(Boolean);
      if (facts.length) more2.appendChild(dotted(el('small', 'muted'), facts.map(function (f) { return txt('span', '', String(f)); })));
      var rs = el('div', 'px-scores');
      (d.badges || []).forEach(function (b) { rs.appendChild(scoreBadge(b)); });
      if (rs.childNodes.length) more2.appendChild(rs);
      if ((d.genres || []).length) more2.appendChild(txt('div', 'muted small', d.genres.join(' · ')));
      if (d.tagline) more2.appendChild(txt('p', 'px-summary', d.tagline));
      var sp = txt('p', 'px-summary', d.summary || w.summary || '');
      sp.style.fontSize = '13.5px'; sp.style.marginTop = '8px';
      more2.appendChild(sp);
      var people = [['Director', d.directors], ['Writer', d.writers], ['Country', d.countries]];
      people.forEach(function (p) { if ((p[1] || []).length) { var l = el('div', 'small'); l.appendChild(el('span', 'muted', p[0] + ': ')); l.appendChild(txt('span', '', p[1].join(', '))); more2.appendChild(l); } });
      if ((d.cast || []).length) {
        var cs = el('div', 'px-cast');
        cs.style.marginTop = '10px';
        d.cast.forEach(function (c) {
          var pc = el('div', 'px-person');
          pc.style.cursor = 'default';
          var ph = el('div', 'face'); ph.appendChild(picture(c.thumb, 120, 120, c.name)); pc.appendChild(ph);
          pc.appendChild(txt('b', '', c.name));
          if (c.role) pc.appendChild(txt('small', '', c.role));
          cs.appendChild(pc);
        });
        more2.appendChild(cs);
      }
    }).catch(function (e) {
      clear(more2);
      if (w.summary) { var sp2 = txt('p', 'px-summary', w.summary); sp2.style.marginTop = '8px'; more2.appendChild(sp2); }
      more2.appendChild(el('p', 'muted small', 'Plex Discover has nothing more: ' + String(e.message || e)));
    });
  }, [{words: 'Close', close: true}, {words: 'Take it off the watchlist', cls: 'danger', icon: 'trash', admin: true, close: true, go: function () {
    api('plex', {action: 'watchlist-remove', dks: [w.dk]}).then(function () { toast('taken off the watchlist', 'good'); WLK.at = 0; if (after) after(); }).catch(oops);
  }}]);
}
function drawWatchlist() {
  var m = main();
  clear(m);
  var bar = el('div', 'px-bar');
  m.appendChild(bar);
  var host = el('div', 'px-wl');
  m.appendChild(host);
  host.appendChild(skeleton());
  var f = PX.tool.wlf || 'all';
  var load2 = function (fresh) {
    return api('plex-watchlist' + (fresh ? '&fresh=1' : ''), null, 90).then(function (r) {
      PX.wl = r; WLK.keys = r.keys; WLK.at = Date.now();
      paint(r);
    }).catch(function (e) {
      clear(host);
      bar.hidden = true;
      host.appendChild(emptyState('bookmark', 'The watchlist could not be read', String(e.message || e) + ' - the watchlist lives at Plex (discover.provider.plex.tv) and needs the server to reach the internet.'));
    });
  };
  var paint = function (r) {
    clear(bar);
    bar.hidden = false;
    bar.appendChild(seg([['all', 'Everything'], ['movie', 'Movies'], ['show', 'Shows'], ['here', 'On this server'], ['away', 'Not here']], f, function (v) { f = PX.tool.wlf = v; paint(r); }));
    var c = el('span', 'px-count');
    bar.appendChild(c);
    var rf = iconButton('refresh', 'Read the watchlist again');
    rf.onclick = function () { load2(true); };
    bar.appendChild(rf);
    /* Several taken off at once (2.5.0, plex_tools' "remove"): on this server or not. */
    if (ADMIN) {
      var selB = cmdButton(WLS.on ? 'Done' : 'Select', 'select', WLS.on ? 'primary small' : 'small');
      selB.onclick = function () { WLS.on = !WLS.on; WLS.sel = {}; paint(r); };
      bar.appendChild(selB);
    }
    var list = (r.items || []).filter(function (w) {
      if (f === 'movie' || f === 'show') return w.type === f;
      if (f === 'here') return !!w.here;
      if (f === 'away') return !w.here;
      return true;
    });
    c.textContent = list.length + ' on the watchlist';
    clear(host);
    if (WLS.on) {
      var acts = el('div', 'btnrow');
      acts.style.marginBottom = '12px';
      var n = Object.keys(WLS.sel).length;
      acts.appendChild(el('span', 'muted small', n + ' selected'));
      var a1 = cmdButton('Select every one shown', 'select', 'small');
      a1.onclick = function () { list.forEach(function (w) { WLS.sel[w.dk] = 1; }); paint(r); };
      var a2 = cmdButton('Invert', 'invert', 'small');
      a2.onclick = function () { list.forEach(function (w) { if (WLS.sel[w.dk]) delete WLS.sel[w.dk]; else WLS.sel[w.dk] = 1; }); paint(r); };
      var a3 = cmdButton('None', 'close', 'small');
      a3.onclick = function () { WLS.sel = {}; paint(r); };
      var a4 = cmdButton('Take ' + n + ' off the watchlist', 'trash', 'small danger');
      a4.disabled = !n;
      a4.onclick = function () {
        confirmThen('Take ' + n + ' titles off the watchlist?', htmlText('They go off the watchlist of the server\'s owner at Plex - nothing on this server changes.'), 'Take them off', function () {
          api('plex', {action: 'watchlist-remove', dks: Object.keys(WLS.sel)}).then(function (x) {
            toast((x.ok || 0) + ' taken off the watchlist' + (x.failed ? ', ' + x.failed + ' failed' : ''), x.failed ? 'bad' : 'good');
            WLS.sel = {}; WLS.on = false; WLK.at = 0; load2(true);
          }).catch(oops);
        });
      };
      [a1, a2, a3, a4].forEach(function (b) { acts.appendChild(b); });
      host.appendChild(acts);
    }
    if (!list.length) { host.appendChild(emptyState('bookmark', 'Nothing here', 'Put a title on it from its detail, or select several in the library.')); return; }
    var g = el('div', 'px-grid' + (WLS.on ? ' px-selecting' : ''));
    g.style.setProperty('--px-poster', PX.size + 'px');
    list.forEach(function (w) {
      var cc = el('div', 'px-card' + (WLS.sel[w.dk] ? ' sel' : ''));
      var p = el('div', 'px-poster');
      p.appendChild(txt('div', 'ph', w.title));
      p.appendChild(picture(w.thumb, PX.size + 40, (PX.size + 40) * 1.5, w.title));
      p.appendChild(el('div', 'shade'));
      if (w.here) { var b = el('span', 'px-badge here'); b.appendChild(ico('check', 13)); b.title = 'on this server: ' + w.here.lib_title; p.appendChild(b); }
      var sk = el('span', 'px-sel'); sk.appendChild(ico('check', 15)); p.appendChild(sk);
      cc.appendChild(p);
      var cap = el('div', 'px-cap');
      cap.appendChild(txt('b', '', w.title));
      cap.appendChild(dotted(el('small'), [w.year ? txt('span', '', String(w.year)) : '', w.type === 'show' ? 'series' : 'movie', w.here ? 'here' : '']));
      cc.appendChild(cap);
      cc.onclick = function () {
        if (WLS.on) {
          if (WLS.sel[w.dk]) delete WLS.sel[w.dk]; else WLS.sel[w.dk] = 1;
          paint(r);
          return;
        }
        if (w.here) { detail(w.here.rk); return; }
        discoverDetail(w, function () { load2(true); });
      };
      g.appendChild(cc);
    });
    host.appendChild(g);
  };
  load2(false);
}

/* ---- titles in another language -------------------------------------------------- */
function langName(code) {
  var got = ((PX.st || {}).langs || []).filter(function (l) { return l[0] === code; })[0];
  return got ? got[1] : code;
}
function isTarget(detected, target) {
  var d = String(detected || '').replace(/\?$/, '');
  if (d === 'library default' || d === 'the library') return true;
  if (target === 'English') return d === 'English' || d === 'unknown';
  return d === target || d.indexOf(target + '/') === 0 || d.slice(-target.length - 1) === '/' + target;
}
function libSelect(now, pick, libs) {
  var s = selectBox((libs || PX.st.sections || []).map(function (x) { return [x.key, x.title]; }), now);
  noI18n(s);
  s.onchange = function () { pick(s.value); };
  return s;
}
function drawTitles() {
  var m = main();
  clear(m);
  var T = PX.tool;
  T.lang = T.lang || PX.st.title_lang || 'en';
  T.sel = T.sel || {};
  T.filter = T.filter || 'foreign';
  var bar = el('div', 'px-bar');
  m.appendChild(bar);
  var info = note('safe', 'A title found to be in another language is put into <b>' + htmlText(langName(T.lang)) + '</b> the safest way first: <b>only the title is written</b> (and locked) - no new match; streams, subtitles, watched state and artwork stay. A locked title keeps its own ending (<i>- Viki</i>); a split entry (two items on one match) is held back.');
  m.appendChild(info);
  var host = el('div', 'px-tool');
  m.appendChild(host);
  host.appendChild(el('div', 'px-skel'));
  host.firstChild.style.cssText = 'aspect-ratio:auto;height:200px';
  var lib = T.lib || PX.lib;
  T.lib = lib;
  api('plex-titles&lib=' + encodeURIComponent(lib), null, 120).then(function (r) {
    T.rows = r.rows;
    T.libLang = r.library_language;
    if (T.pre) { T.pre.forEach(function (k) { T.sel[k] = 1; }); T.filter = 'picked'; T.pre = null; }
    (T.exact ? Object.keys(T.exact) : []).forEach(function (k) {
      var row = T.rows.filter(function (x) { return x.rk === k; })[0];
      if (row) Object.assign(row, T.exact[k]);
    });
    titlesList(bar, host);
  }).catch(function (e) { clear(host); host.appendChild(emptyState('translate', 'The titles could not be read', String(e.message || e))); });
}
function titlesList(bar, host) {
  var T = PX.tool;
  var target = langName(T.lang);
  clear(bar);
  bar.appendChild(libSelect(T.lib, function (v) { PX.tool = {lang: T.lang, lib: v}; drawTitles(); },
    PX.st.sections));
  var ls = selectBox((PX.st.langs || []).map(function (l) { return [l[0], l[1]]; }), T.lang);
  ls.title = 'The language the titles are put into';
  ls.onchange = function () { T.lang = ls.value; T.props = null; drawTitles(); };
  bar.appendChild(ls);
  var sw = el('div', 'px-search');
  sw.appendChild(ico('search', 16));
  var q = el('input'); q.type = 'search'; q.placeholder = 'a title…'; q.value = T.q || '';
  q.oninput = debounce(function () { T.q = q.value; paintRows(); }, 150);
  sw.appendChild(q);
  bar.appendChild(sw);
  var counts = {};
  T.rows.forEach(function (r) { counts[r.lang] = (counts[r.lang] || 0) + 1; });
  var foreign = T.rows.filter(function (r) { return !isTarget(r.lang, target); }).length;
  var chips = el('div', 'px-chips');
  var chip = function (k, words) {
    var c = el('button', 'px-chip' + (T.filter === k ? ' on' : ''), words);
    c.type = 'button';
    c.onclick = function () { T.filter = k; titlesList(bar, host); };
    chips.appendChild(c);
  };
  chip('foreign', 'not in ' + target + ' (' + foreign + ')');
  chip('all', 'all (' + T.rows.length + ')');
  if (Object.keys(T.sel).length) chip('picked', 'ticked (' + Object.keys(T.sel).length + ')');
  /* Which lock (2.5.0, plex_tools' scopes): the title, the sort title, either, any field. */
  chip('lockT', 'title locked');
  chip('lockS', 'sort title locked');
  chip('lockTS', 'title or sort locked');
  chip('locked', 'any field locked');
  chip('split', 'split entries');
  chip('sort', 'sort title differs');
  Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).slice(0, 10).forEach(function (k) {
    if (isTarget(k, target)) return;
    var c = el('button', 'px-chip' + (T.filter === 'lang:' + k ? ' on' : ''));
    c.type = 'button';
    c.appendChild(el('span', '', k.replace(/\?$/, '')));
    c.appendChild(txt('span', '', (/\?$/.test(k) ? '?' : '') + ' (' + counts[k] + ')'));
    c.onclick = function () { T.filter = 'lang:' + k; titlesList(bar, host); };
    chips.appendChild(c);
  });
  clear(host);
  host.appendChild(chips);
  var acts = el('div', 'btnrow');
  host.appendChild(acts);
  var list = el('div', 'px-rows');
  host.appendChild(list);
  var shown = [];
  var paintActs = function () {
    clear(acts);
    var n = Object.keys(T.sel).length;
    var all = cmdButton('Tick all shown', 'select', 'small');
    all.onclick = function () { shown.forEach(function (r) { T.sel[r.rk] = 1; }); paintRows(); };
    var none = cmdButton('None', 'close', 'small');
    none.onclick = function () { T.sel = {}; paintRows(); };
    var inv = cmdButton('Invert', 'invert', 'small');
    explain(inv, 'The ticks turned over among those shown (plex_tools\' i).');
    inv.onclick = function () { shown.forEach(function (r) { if (T.sel[r.rk]) delete T.sel[r.rk]; else T.sel[r.rk] = 1; }); paintRows(); };
    acts.appendChild(all); acts.appendChild(none); acts.appendChild(inv);
    if (ADMIN) {
      var rf = cmdButton('Refresh their metadata', 'refresh', 'small');
      explain(rf, 'Plex asks its agents again for each ticked one (plex_tools\' F9 in this list) - then read the list again.');
      rf.disabled = !n;
      rf.onclick = function () { refreshItems(Object.keys(T.sel)); };
      acts.appendChild(rf);
    }
    var read = cmdButton('Read their exact language', 'search', 'small');
    explain(read, 'The language of the metadata of each ticked item, from Plex (the item’s own, or the library’s) - not a guess from the title. One question each, so only for the ticked ones.');
    read.disabled = !n;
    read.onclick = function () {
      var keys = Object.keys(T.sel);
      follow(api('plex', {action: 'titles-read', lib: T.lib, rks: keys})).then(function (res2) {
        T.exact = Object.assign(T.exact || {}, res2.rows || {});
        Object.keys(res2.rows || {}).forEach(function (k) {
          var row = T.rows.filter(function (x) { return x.rk === k; })[0];
          if (row) Object.assign(row, res2.rows[k]);
        });
        titlesList(bar, host);
      }).catch(oops);
    };
    acts.appendChild(read);
    if (ADMIN) {
      acts.appendChild(el('span', 'spacer'));
      var find = cmdButton('Find the ' + target + ' titles of ' + n, 'translate', 'primary');
      find.disabled = !n;
      find.onclick = function () { titlesPropose(host, bar); };
      acts.appendChild(find);
      var other = cmdButton('Other ways…', 'gear', '');
      other.disabled = !n;
      other.onclick = function () { titlesOther(Object.keys(T.sel), bar, host); };
      acts.appendChild(other);
    }
  };
  var paintRows = function () {
    var mt = matcher(T.q);
    shown = T.rows.filter(function (r) {
      if (!mt(r.title) && !mt(r.sort)) return false;
      var f = T.filter;
      if (f === 'foreign') return !isTarget(r.lang, target);
      if (f === 'picked') return !!T.sel[r.rk];
      if (f === 'locked') return (r.locked || []).length > 0 || r.locked === undefined;
      if (f === 'lockT' || f === 'lockS' || f === 'lockTS') {
        if (r.locked === undefined) return true;
        var lt = (r.locked || []).indexOf('title') >= 0, lsrt = (r.locked || []).indexOf('titleSort') >= 0;
        return f === 'lockT' ? lt : (f === 'lockS' ? lsrt : (lt || lsrt));
      }
      if (f === 'split') return r.dup;
      if (f === 'sort') return r.sort_differs;
      if (f.indexOf('lang:') === 0) return r.lang === f.slice(5);
      return true;
    });
    if (/^lock/.test(T.filter) && T.rows.some(function (r) { return r.locked === undefined; })) {
      clear(list);
      list.appendChild(el('p', 'note', 'Which fields are locked is read with the exact language: tick the titles and press “Read their exact language”.'));
    } else {
      clear(list);
    }
    shown.slice(0, 600).forEach(function (r) {
      var row = el('label', 'px-row' + (T.sel[r.rk] ? ' pick' : ''));
      var ck = el('input'); ck.type = 'checkbox'; ck.checked = !!T.sel[r.rk];
      ck.disabled = !ADMIN && false;
      ck.onchange = function () { if (ck.checked) T.sel[r.rk] = 1; else delete T.sel[r.rk]; row.classList.toggle('pick', ck.checked); paintActs(); };
      row.appendChild(ck);
      var th = el('div', 'th'); th.appendChild(picture(r.thumb, 88, 132, '')); row.appendChild(th);
      var mm = el('div', 'main');
      mm.appendChild(txt('b', '', r.title));
      var sm2 = dotted(el('small'), [r.year ? txt('span', '', String(r.year)) : '']);
      if (r.sort_differs) { if (r.year) sm2.appendChild(document.createTextNode(' · ')); sm2.appendChild(el('span', '', 'sort title')); sm2.appendChild(document.createTextNode(': ')); sm2.appendChild(txt('span', '', r.sort)); }
      mm.appendChild(sm2);
      row.appendChild(mm);
      var side = el('div', 'side');
      var lg = el('span', 'px-lang' + (isTarget(r.lang, target) ? '' : ' foreign') + (r.certain ? '' : ' guess'));
      lg.appendChild(el('span', '', String(r.lang).replace(/\?$/, '')));
      lg.title = r.certain ? 'its metadata language (' + (r.src || '') + ')' : 'guessed from the title’s letters and words';
      if (!r.certain && /\?$/.test(r.lang)) lg.appendChild(document.createTextNode(' ?'));
      side.appendChild(lg);
      (r.locked || []).slice(0, 3).forEach(function (f) { side.appendChild(el('span', 'tag warn', 'locked ' + f)); });
      if (r.dup) side.appendChild(el('span', 'tag crit', 'split'));
      row.appendChild(side);
      list.appendChild(row);
    });
    if (shown.length > 600) list.appendChild(el('p', 'muted small', 'The first 600 of ' + shown.length + ' - narrow it with the search.'));
    if (!shown.length) list.appendChild(emptyState('translate', 'Nothing like that', T.filter === 'foreign' ? 'Every title looks like ' + target + ' already.' : ''));
    paintActs();
  };
  paintRows();
}
function titlesPropose(host, bar) {
  var T = PX.tool;
  var keys = Object.keys(T.sel);
  var w = el('div');
  clear(host);
  host.appendChild(w);
  follow(api('plex', {action: 'titles-propose', lib: T.lib, rks: keys, lang: T.lang}), w).then(function (r) {
    T.props = r.props || [];
    T.props.forEach(function (p) { p.pick = !!p.exact && !(p.risky || []).length; });
    proposals(host, bar, r.none || 0);
  }).catch(function (e) { toast(String(e.message || e), 'bad'); titlesList(bar, host); });
}
function proposals(host, bar, none) {
  var T = PX.tool;
  clear(host);
  var target = langName(T.lang);
  if (!T.props.length) {
    host.appendChild(emptyState('translate', 'No ' + target + ' title was found', 'The agent has none for them in ' + target + '.'));
    var back0 = cmdButton('Back', 'prev', '');
    back0.onclick = function () { titlesList(bar, host); };
    host.appendChild(back0);
    return;
  }
  var n = T.props.filter(function (p) { return p.pick; }).length;
  var other = T.props.filter(function (p) { return !p.exact; }).length;
  var risky = T.props.filter(function (p) { return (p.risky || []).length; }).length;
  host.appendChild(note(other || risky ? 'warn' : 'safe', '<span><b>' + n + '</b> of ' + T.props.length + ' are ticked: they come from the item’s own entry.</span>'
    + (other ? ' <span><b>' + other + '</b> belong to another entry of the agent - check them, or press <i>Find…</i> to pick the right one by hand.</span>' : '')
    + (risky ? ' <span><b>' + risky + '</b> have a locked title or are a split entry - tick them yourself if you mean it.</span>' : '')
    + (none ? ' <span>' + none + ' had no ' + htmlText(target) + ' title at all and are left alone.</span>' : '')));
  var list = el('div', 'px-rows');
  T.props.forEach(function (p) {
    var row = el('div', 'px-row' + (p.pick ? ' pick' : ''));
    var ck = el('input'); ck.type = 'checkbox'; ck.checked = !!p.pick;
    ck.onchange = function () { p.pick = ck.checked; row.classList.toggle('pick', ck.checked); count(); };
    row.appendChild(ck);
    var th = el('div', 'th'); th.appendChild(picture(p.thumb, 88, 132, '')); row.appendChild(th);
    var mm = el('div', 'main');
    var head = el('div');
    head.appendChild(txt('span', 'px-old', p.old));
    head.appendChild(el('span', 'px-arrow', '→'));
    head.appendChild(txt('span', 'px-new', p.new));
    mm.appendChild(head);
    if (!p.exact) mm.appendChild(el('div', 'px-warnline', 'another entry of the agent - check it'));
    (p.risky || []).forEach(function (x) { mm.appendChild(el('div', 'px-warnline', x === 'split' ? 'a split entry: two items share this match' : 'the title is locked: its ending is kept')); });
    var ed = el('div', 'px-edit');
    ed.appendChild(el('span', '', 'title'));
    var ti = textBox(p.new, '', ''); noI18n(ti);
    ti.oninput = function () { p.new = ti.value; head.lastChild.textContent = ti.value; if (!p.pick) { p.pick = true; ck.checked = true; row.classList.add('pick'); count(); } };
    ed.appendChild(ti);
    if (p.do_sort || p.old_sort) {
      ed.appendChild(el('span', '', 'sort title'));
      var si = textBox(p.new_sort, '', ''); noI18n(si);
      si.oninput = function () { p.new_sort = si.value; p.do_sort = true; };
      ed.appendChild(si);
    }
    mm.appendChild(ed);
    row.appendChild(mm);
    var side = el('div', 'side');
    var fb = cmdButton('Find…', 'search', 'small');
    fb.onclick = function () { findMatch(p, function () { proposals(host, bar, none); }); };
    side.appendChild(fb);
    row.appendChild(side);
    list.appendChild(row);
  });
  host.appendChild(list);
  var row2 = el('div', 'btnrow');
  var back = cmdButton('Back', 'prev', '');
  back.onclick = function () { T.props = null; titlesList(bar, host); };
  var goB = cmdButton('Write', 'check', 'primary');
  var count = function () { var k = T.props.filter(function (p) { return p.pick; }).length; setButtonIcon(goB, 'check', 'Write ' + k + ' titles'); goB.disabled = !k; };
  goB.onclick = function () {
    var todo = T.props.filter(function (p) { return p.pick; }).map(function (p) { return {rk: p.rk, old: p.old, new: p.new, new_sort: p.new_sort, do_sort: p.do_sort}; });
    var w = el('div');
    host.appendChild(w);
    row2.hidden = true;
    follow(api('plex', {action: 'titles-apply', lib: T.lib, how: 'title', items: todo, lang: T.lang}), w).then(function (r) {
      toast(r.ok + ' titles written' + (r.failed ? ', ' + r.failed + ' failed' : ''), r.failed ? 'bad' : 'good');
      T.props = null; T.sel = {};
      delete PX.data[T.lib];
      drawTitles();
    }).catch(function (e) { row2.hidden = false; toast(String(e.message || e), 'bad'); });
  };
  row2.appendChild(back); row2.appendChild(el('span', 'spacer')); row2.appendChild(goB);
  host.appendChild(row2);
  count();
}
function findMatch(p, after) {
  var T = PX.tool;
  var term = p.old;
  var draw3 = function (body) {
    var row = el('div', 'btnrow');
    var q = textBox(term, '', ''); q.style.flex = '1'; noI18n(q);
    var b = cmdButton('Search', 'search', 'primary');
    var res2 = el('div', 'px-rows');
    res2.style.marginTop = '10px';
    var run = function () {
      clear(res2);
      res2.appendChild(el('p', 'muted small', 'asking the agent…'));
      api('plex-matches&rk=' + encodeURIComponent(p.rk) + '&lang=' + encodeURIComponent(T.lang) + (q.value.trim() && q.value.trim() !== p.old ? '&title=' + encodeURIComponent(q.value.trim()) : ''), null, 90).then(function (r) {
        clear(res2);
        if (!(r.matches || []).length) { res2.appendChild(el('p', 'muted', 'Nothing found.')); return; }
        r.matches.forEach(function (c) {
          var rr = el('div', 'px-row');
          rr.style.gridTemplateColumns = 'minmax(0,1fr) auto';
          var mm = el('div', 'main');
          mm.appendChild(txt('b', '', c.name + (c.year ? ' (' + c.year + ')' : '')));
          mm.appendChild(el('small', '', 'score ' + c.score));
          rr.appendChild(mm);
          var pk = cmdButton('This one', 'check', 'small');
          pk.onclick = function () { p.new = c.name; if (p.do_sort) p.new_sort = c.name; p.exact = true; p.pick = true; closeModal(); after(); };
          rr.appendChild(pk);
          res2.appendChild(rr);
        });
      }).catch(function (e) { clear(res2); res2.appendChild(el('p', 'note bad', String(e.message || e))); });
    };
    b.onclick = run;
    q.onkeydown = function (e) { if (e.key === 'Enter') run(); };
    row.appendChild(q); row.appendChild(b);
    body.appendChild(el('p', 'muted small', 'Only the name of the entry picked is taken - no new match.'));
    body.appendChild(row);
    body.appendChild(res2);
    run();
  };
  modal('Find the right entry', draw3, [{words: 'Close', close: true}], true);
}
function titlesOther(rks, bar, host) {
  var T = PX.tool;
  var target = langName(T.lang);
  var how = 'relanguage', risky = false;
  modal('Other ways for ' + rks.length + ' titles', function (body) {
    var opt = function (k, head, words) {
      var o = el('label', 'px-opt' + (how === k ? ' on' : ''));
      var rd = el('input'); rd.type = 'radio'; rd.name = 'px-how'; rd.checked = how === k;
      rd.onchange = function () { how = k; body.querySelectorAll('.px-opt').forEach(function (x) { x.classList.toggle('on', x === o); }); };
      o.appendChild(rd);
      var l = el('div', 'l'); l.appendChild(el('b', '', head)); l.appendChild(el('small', '', words)); o.appendChild(l);
      body.appendChild(o);
    };
    opt('relanguage', 'The metadata language becomes ' + target + ', and a refresh',
      'Not a new match: the item stays on its entry, so watched state, positions and the tracks stay. The title, the episode titles and the summary come again in ' + target + ' - a locked title is unlocked first.');
    opt('fixmatch', 'Fix Match to its own entry, in ' + target,
      'Matched again to the very entry it has now (the same guid), with metadata in ' + target + '. Only where the agent has that entry; an item without one is left as it is.');
    var r = el('label', 'check small');
    var cb = el('input'); cb.type = 'checkbox';
    cb.onchange = function () { risky = cb.checked; };
    r.appendChild(cb);
    r.appendChild(el('span', '', 'also the split entries and the locked titles (not advised: it can undo a split made by hand, or a name given by hand)'));
    r.style.marginTop = '10px';
    body.appendChild(r);
  }, [{words: 'Close', close: true}, {words: 'Show what happens', cls: 'primary', icon: 'next', close: true, go: function () {
    titlesOtherPlan(rks, how, risky);
  }}], true);
}
/* Each title before it is changed (2.5.0, plex_tools' plan screen): now →
   after, the ones held back and those without an entry of their own. */
function titlesOtherPlan(rks, how, risky) {
  var T = PX.tool;
  var target = langName(T.lang);
  var plan = null;
  wideModal(how === 'fixmatch' ? 'Fix Match to their own entry, in ' + target : 'Their metadata again, in ' + target, function (body) {
    var w = el('div');
    body.appendChild(w);
    follow(api('plex', {action: 'titles-preview', lib: T.lib, how: how, rks: rks, lang: T.lang, risky: risky}), w).then(function (r) {
      plan = r.done || r;
      clear(w);
      var rows = plan.rows || [];
      var go = rows.filter(function (x) { return !x.held && !x.none && !x.error; }).length;
      w.appendChild(el('p', 'muted small', go + ' of ' + rows.length + ' change' + (plan.held ? ' · ' + plan.held + ' held back (a split entry or a locked title)' : '') + (plan.none ? ' · ' + plan.none + ' without an entry of their own in ' + target + ' - left as they are' : '')));
      w.appendChild(listTable(['Now', '', 'After'], rows, function (x) {
        var tr = el('tr');
        tr.appendChild(txt('td', '', x.title + (x.year ? ' (' + x.year + ')' : '')));
        tr.appendChild(el('td', 'muted', '→'));
        var a = el('td');
        if (x.held) a.appendChild(el('span', 'tag warn', 'held back: ' + x.held.join(', ')));
        else if (x.none) a.appendChild(el('span', 'muted', 'no entry of its own - left as it is'));
        else if (x.error) a.appendChild(txt('span', 'bad', x.error));
        else a.appendChild(txt('b', '', x.new));
        tr.appendChild(a);
        return tr;
      }, 'plain'));
      var goB = $('px-other-go');
      if (goB) { goB.disabled = !go; setButtonIcon(goB, 'check', 'Do it - ' + go + ' titles'); }
    }).catch(function (e) { clear(w); w.appendChild(el('p', 'note bad', String(e.message || e))); });
  }, [{words: 'Close', close: true}, {words: 'Do it', cls: 'primary', icon: 'check', close: true, go: function () {
    if (!plan) return;
    var keys = (plan.rows || []).filter(function (x) { return !x.held && !x.none && !x.error; }).map(function (x) { return x.rk; });
    if (!keys.length) return;
    follow(api('plex', {action: 'titles-apply', lib: T.lib, how: how, rks: keys, lang: T.lang, risky: risky})).then(function (res2) {
      toast((res2.ok || 0) + ' done' + ((res2.held || []).length ? ', ' + res2.held.length + ' held back' : '') + (res2.failed ? ', ' + res2.failed + ' failed' : ''), res2.failed ? 'bad' : 'good');
      delete PX.data[T.lib];
      T.sel = {};
      drawTitles();
    }).catch(oops);
  }}]);
  setTimeout(function () {
    var bs = document.querySelectorAll('#modal-foot button.primary');
    if (bs.length) { bs[bs.length - 1].id = 'px-other-go'; bs[bs.length - 1].disabled = true; }
  }, 0);
}

/* ---- ratings -------------------------------------------------------------------------- */
function drawRatings() {
  var m = main();
  clear(m);
  var T = PX.tool;
  T.lib = T.lib || PX.lib;
  T.sel = T.sel || null;
  var bar = el('div', 'px-bar');
  m.appendChild(bar);
  var src = el('div', 'px-src');
  var S = PX.st.sources || {};
  [['imdb', 'IMDb'], ['rottentomatoes', 'Rotten Tomatoes'], ['tmdb', 'TMDB'], ['tvmaze', 'TVmaze'], ['trakt', 'Trakt']].forEach(function (x) {
    var s = el('span', S[x[0]] ? 'on' : '');
    s.textContent = x[1];
    noI18n(s);
    s.title = S[x[0]] ? 'switched on' : 'needs its key in Settings → Plex library';
    src.appendChild(s);
  });
  var cfgB = cmdButton('The sources…', 'gear', 'small');
  cfgB.onclick = function () { PANE = 'plex'; showTab('settings'); if (typeof showPane === 'function') showPane('plex'); };
  var head = el('div', 'btnrow');
  head.appendChild(src);
  head.appendChild(el('span', 'spacer'));
  head.appendChild(cfgB);
  m.appendChild(head);
  m.appendChild(note('safe', 'The ratings are <b>looked up first and shown</b>: nothing is written until you press Write. A show is the show, its seasons and every episode. The fields are left unlocked unless you say so - a later refresh by Plex may then put its own back.'));
  var host = el('div', 'px-tool');
  m.appendChild(host);
  host.appendChild(el('div', 'px-skel'));
  host.firstChild.style.cssText = 'aspect-ratio:auto;height:200px';
  items(T.lib).then(function (d) {
    if (!T.sel) {
      T.sel = {};
      if (T.pre) T.pre.forEach(function (k) { T.sel[k] = 1; });
      else d.items.forEach(function (it) { if (!(it.rating || it.audience)) T.sel[it.rk] = 1; });
      T.pre = null;
    }
    ratingsList(bar, host, d);
  }).catch(function (e) { clear(host); host.appendChild(emptyState('star', 'The library could not be read', String(e.message || e))); });
}
function ratingsList(bar, host, d) {
  var T = PX.tool;
  T.f = T.f || 'none';
  clear(bar);
  bar.appendChild(libSelect(T.lib, function (v) { PX.tool = {lib: v}; drawRatings(); }));
  var sw = el('div', 'px-search');
  sw.appendChild(ico('search', 16));
  var q = el('input'); q.type = 'search'; q.placeholder = 'a title…'; q.value = T.q || '';
  q.oninput = debounce(function () { T.q = q.value; paint(); }, 150);
  sw.appendChild(q);
  bar.appendChild(sw);
  bar.appendChild(seg([['none', 'Without a rating'], ['all', 'All'], ['picked', 'Ticked']], T.f, function (v) { T.f = v; ratingsList(bar, host, d); }));
  var ow = el('label', 'check small');
  var owc = el('input'); owc.type = 'checkbox'; owc.checked = !!T.overwrite;
  owc.onchange = function () { T.overwrite = owc.checked; };
  ow.appendChild(owc); ow.appendChild(el('span', '', 'overwrite the ratings there are'));
  bar.appendChild(ow);
  clear(host);
  var acts = el('div', 'btnrow');
  host.appendChild(acts);
  var list = el('div', 'px-rows');
  host.appendChild(list);
  var shown = [];
  var paintActs = function () {
    clear(acts);
    var n = Object.keys(T.sel).length;
    var all = cmdButton('Tick all shown', 'select', 'small');
    all.onclick = function () { shown.forEach(function (r) { T.sel[r.rk] = 1; }); paint(); };
    var none = cmdButton('None', 'close', 'small');
    none.onclick = function () { T.sel = {}; paint(); };
    var inv = cmdButton('Invert', 'invert', 'small');
    explain(inv, 'The ticks turned over among those shown (plex_tools\' i).');
    inv.onclick = function () { shown.forEach(function (r) { if (T.sel[r.rk]) delete T.sel[r.rk]; else T.sel[r.rk] = 1; }); paint(); };
    acts.appendChild(all); acts.appendChild(none); acts.appendChild(inv);
    if (ADMIN) {
      var rf = cmdButton('Refresh their metadata', 'refresh', 'small');
      explain(rf, 'Plex asks its agents again for each ticked one - its own badges (IMDb, Rotten Tomatoes, TMDB) come again (plex_tools\' F9 in this list).');
      rf.disabled = !n;
      rf.onclick = function () { refreshItems(Object.keys(T.sel)); };
      acts.appendChild(rf);
    }
    acts.appendChild(el('span', 'spacer'));
    if (ADMIN) {
      var pv = cmdButton('Look up the ratings of ' + n, 'search', 'primary');
      pv.disabled = !n;
      pv.onclick = function () { ratingsPreview(host, bar, d); };
      acts.appendChild(pv);
    }
  };
  var paint = function () {
    var mt = matcher(T.q);
    shown = d.items.filter(function (it) {
      if (!mt(it.title)) return false;
      if (T.f === 'none') return !(it.rating || it.audience);
      if (T.f === 'picked') return !!T.sel[it.rk];
      return true;
    });
    clear(list);
    shown.slice(0, 600).forEach(function (it) {
      var row = el('label', 'px-row' + (T.sel[it.rk] ? ' pick' : ''));
      var ck = el('input'); ck.type = 'checkbox'; ck.checked = !!T.sel[it.rk];
      ck.onchange = function () { if (ck.checked) T.sel[it.rk] = 1; else delete T.sel[it.rk]; row.classList.toggle('pick', ck.checked); paintActs(); };
      row.appendChild(ck);
      var th = el('div', 'th'); th.appendChild(picture(it.thumb, 88, 132, '')); row.appendChild(th);
      var mm = el('div', 'main');
      mm.appendChild(txt('b', '', it.title));
      mm.appendChild(dotted(el('small'), [it.year ? txt('span', '', String(it.year)) : '', it.type === 'show' ? (it.leaves || 0) + ' episodes' : '']));
      row.appendChild(mm);
      var side = el('div', 'side');
      if (it.rating) side.appendChild(scoreBadge({src: /imdb/.test(it.rating_img) ? 'imdb' : (/rotten/.test(it.rating_img) ? 'rt' : 'plex'), label: /imdb/.test(it.rating_img) ? 'IMDb' : (/rotten/.test(it.rating_img) ? 'RT' : '★'), value: (+it.rating).toFixed(1)}));
      if (it.audience) side.appendChild(scoreBadge({src: /rotten/.test(it.audience_img) ? 'rt_audience' : (/themoviedb/.test(it.audience_img) ? 'tmdb' : 'plex'), label: /rotten/.test(it.audience_img) ? 'RT' : (/themoviedb/.test(it.audience_img) ? 'TMDB' : '★'), value: (+it.audience).toFixed(1)}));
      if (!it.rating && !it.audience) side.appendChild(el('span', 'tag', 'no rating'));
      row.appendChild(side);
      list.appendChild(row);
    });
    if (!shown.length) list.appendChild(emptyState('star', T.f === 'none' ? 'Every one has a rating' : 'Nothing like that', ''));
    paintActs();
  };
  paint();
}
function ratingsPreview(host, bar, d) {
  var T = PX.tool;
  var w = el('div');
  clear(host);
  host.appendChild(w);
  follow(api('plex', {action: 'ratings-preview', lib: T.lib, rks: Object.keys(T.sel), overwrite: !!T.overwrite}), w).then(function (r) {
    clear(host);
    var c = r.counts || {};
    var sum = el('div', 'px-sum');
    var met = function (v, k, cls) { var mm = el('div', 'metric ' + (cls || '')); mm.innerHTML = '<div class="label"></div><div class="value"></div>'; mm.firstChild.textContent = k; mm.lastChild.textContent = v; return mm; };
    sum.appendChild(met(c.add || 0, 'added', 'ok'));
    sum.appendChild(met(c.update || 0, 'updated', 'warn'));
    sum.appendChild(met((c.kept || 0) + (c.same || 0), 'kept'));
    sum.appendChild(met(c['no match'] || 0, 'no match'));
    host.appendChild(sum);
    var pick = {};
    r.rows.forEach(function (e) { if (e.status === 'add' || e.status === 'update') pick[e.rk] = 1; });
    var holder = el('div');
    ratingsRows(holder, r, pick);
    host.appendChild(holder);
    var row2 = el('div', 'btnrow');
    var lk = el('label', 'check small');
    var lkc = el('input'); lkc.type = 'checkbox'; lkc.checked = !!T.lock;
    lkc.onchange = function () { T.lock = lkc.checked; };
    lk.appendChild(lkc); lk.appendChild(el('span', '', 'lock the fields, so a refresh by Plex does not put its own back'));
    var back = cmdButton('Back', 'prev', '');
    back.onclick = function () { ratingsList(bar, host, d); };
    var goB = cmdButton('Write', 'check', 'primary');
    var cnt = function () { var n = Object.keys(pick).length; setButtonIcon(goB, 'check', 'Write ' + n + ' ratings'); goB.disabled = !n; };
    holder._count = cnt;
    goB.onclick = function () {
      var w2 = el('div');
      host.appendChild(w2);
      row2.hidden = true;
      follow(api('plex', {action: 'ratings-apply', plan: r.plan_id, lock: !!T.lock, only: Object.keys(pick)}), w2).then(function (x) {
        toast(x.ok + ' ratings written' + (x.failed ? ', ' + x.failed + ' failed' : ''), x.failed ? 'bad' : 'good');
        delete PX.data[T.lib];
        PX.tool = {lib: T.lib};
        drawRatings();
      }).catch(function (e) { row2.hidden = false; toast(String(e.message || e), 'bad'); });
    };
    row2.appendChild(back); row2.appendChild(lk); row2.appendChild(el('span', 'spacer')); row2.appendChild(goB);
    host.appendChild(row2);
    cnt();
  }).catch(function (e) { toast(String(e.message || e), 'bad'); ratingsList(bar, host, d); });
}

/* ---- labels of a library ------------------------------------------------------------- */
function drawLabels() {
  var m = main();
  clear(m);
  var T = PX.tool;
  T.lib = T.lib || PX.lib;
  var bar = el('div', 'px-bar');
  bar.appendChild(libSelect(T.lib, function (v) { PX.tool = {lib: v}; drawLabels(); }));
  /* plex_tools' "raw" (2.5.0): exactly what Plex keeps - to see case variants. */
  var rawB = cmdButton('As Plex keeps them', 'list', 'small');
  explain(rawB, 'The exact label strings and their ids as Plex returns them, with how many titles carry each - where one label exists in two letter cases (iPrima / IPrima), both are shown.');
  rawB.onclick = function () { labelsRaw(T.lib); };
  bar.appendChild(rawB);
  var c = el('span', 'px-count');
  bar.appendChild(c);
  m.appendChild(bar);
  var host = el('div');
  m.appendChild(host);
  host.appendChild(el('div', 'px-skel'));
  host.firstChild.style.cssText = 'aspect-ratio:auto;height:200px';
  Promise.all([api('plex-labels&lib=' + encodeURIComponent(T.lib), null, 120), items(T.lib)]).then(function (got) {
    var labels = got[0].labels || [], d = got[1];
    c.textContent = labels.length + ' labels';
    clear(host);
    if (!labels.length) { host.appendChild(emptyState('tag', 'No labels in this library', 'Select titles in the library and press Labels.')); return; }
    var g = el('div', 'px-labels');
    labels.forEach(function (lab) {
      var cd = el('div', 'px-labcard');
      var top = el('div', 'top');
      top.appendChild(ico('tag', 17));
      top.appendChild(txt('b', '', lab.title));
      top.appendChild(el('span', 'tag', String(lab.count)));
      cd.appendChild(top);
      if ((lab.names || []).length > 1) cd.appendChild(txt('div', 'muted small', 'spelt ' + lab.names.join(' · ')));
      var strip = el('div', 'strip');
      (lab.items || []).slice(0, 7).forEach(function (k) {
        var it = d.byRk[k];
        var th = el('div', 'th');
        if (it) { th.appendChild(picture(it.thumb, 88, 132, it.title)); th.title = it.title; th.style.cursor = 'pointer'; th.onclick = function () { detail(k); }; }
        strip.appendChild(th);
      });
      cd.appendChild(strip);
      var row = el('div', 'btnrow');
      var sh = cmdButton('Show them', 'eye', 'small');
      sh.onclick = function () { PX.lib = T.lib; PX.label = lab.title; PX.q = ''; go('library'); };
      row.appendChild(sh);
      if (ADMIN) {
        var rn = cmdButton('Rename…', 'edit', 'small');
        rn.onclick = function () { labelRename(T.lib, lab); };
        var pg = cmdButton('Clean it out…', 'trash', 'small danger');
        pg.onclick = function () {
          confirmThen('Take “' + lab.title + '” off everything?', htmlText('Off every item, season, episode and collection of this library that carries it - in every letter case. Plex has no way of deleting a label itself: once nothing carries it, it goes from the lists after Plex optimizes its database.'), 'Clean it out', function () {
            follow(api('plex', {action: 'labels', how: 'purge', lib: T.lib, old: lab.title})).then(function (r) {
              toast('taken off ' + (r.touched || 0), 'good'); delete PX.data[T.lib]; drawLabels();
            }).catch(oops);
          });
        };
        row.appendChild(rn); row.appendChild(pg);
      }
      cd.appendChild(row);
      g.appendChild(cd);
    });
    host.appendChild(g);
  }).catch(function (e) { clear(host); host.appendChild(emptyState('tag', 'The labels could not be read', String(e.message || e))); });
}
function labelsRaw(lib) {
  wideModal('Labels as Plex keeps them', function (body) {
    var w = el('div');
    w.appendChild(el('div', 'px-skel'));
    w.firstChild.style.cssText = 'aspect-ratio:auto;height:120px';
    body.appendChild(w);
    api('plex', {action: 'labels', how: 'raw', lib: lib}).then(function (r) {
      clear(w);
      var rows = r.labels || [];
      var twins = rows.filter(function (x) { return x.variants > 1; }).length;
      w.appendChild(el('p', 'muted small', rows.length + ' label strings' + (twins ? ' - ' + twins + ' of them in more than one letter case (marked)' : '') + '. The id is Plex\'s own; titles is how many carry that exact string.'));
      if (!rows.length) { w.appendChild(el('p', 'muted', 'This library has no labels.')); return; }
      w.appendChild(listTable(['Label', 'Id', 'Titles', ''], rows, function (x) {
        var tr = el('tr');
        tr.appendChild(txt('td', 'mono', '"' + x.title + '"'));
        tr.appendChild(txt('td', 'mono muted', String(x.id || '')));
        tr.appendChild(el('td', 'num', x.count === null ? '?' : String(x.count)));
        var t = el('td');
        if (x.variants > 1) t.appendChild(el('span', 'tag warn', 'another letter case too'));
        tr.appendChild(t);
        return tr;
      }, 'plain'));
    }).catch(function (e) { clear(w); w.appendChild(el('p', 'note bad', String(e.message || e))); });
  }, [{words: 'Close', close: true}]);
}
function labelRename(lib, lab) {
  modal('Rename “' + lab.title + '”', function (body) {
    body.appendChild(el('p', 'muted small', 'Plex cannot rename a label: the new one is put on the ' + lab.count + ' titles carrying it, and the old one taken off.'));
    var t = textBox(lab.title, '', '');
    noI18n(t);
    t.id = 'px-rename';
    body.appendChild(formField('The new name', t, ''));
  }, [{words: 'Close', close: true}, {words: 'Rename', cls: 'primary', icon: 'save', go: function () {
    var v = ($('px-rename').value || '').trim();
    if (!v || v === lab.title) { closeModal(); return; }
    api('plex', {action: 'labels', how: 'rename', lib: lib, old: lab.title, new: v}).then(function (r) {
      closeModal(); toast('renamed on ' + r.done + ' titles', 'good'); delete PX.data[lib]; drawLabels();
    }).catch(oops);
  }}], true);
}

/* F5 and F9 over the library in front (2.4.4), as in plex_tools: F5 scans its
   files, F9 refreshes all its metadata (asked first). Only on the Plex library
   tab, with no popup open and nothing being typed; Ctrl+F5 / Ctrl+R still
   reload the page. */
document.addEventListener('keydown', function (e) {
  if ((e.key !== 'F5' && e.key !== 'F9') || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  if (typeof TAB === 'undefined' || TAB !== 'library' || !ADMIN) return;
  if (PX.view !== 'library' || !PX.keysSection) return;
  var m = document.getElementById('modal');
  if (m && !m.hidden) return;
  var t = document.activeElement;
  if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
  e.preventDefault();
  libraryScan(PX.keysSection, e.key === 'F9');
});

/* ---- the page asks ---------------------------------------------------------------- */
window.PXLIB = {
  show: function () { start(false); },
  hide: function () { closeSheet(); dock(); },
  hash: function () { return '/' + PX.view + (PX.lib ? '/' + PX.lib : ''); },
  from: function (parts) {
    if (parts[1] && VIEWS.some(function (v) { return v[0] === parts[1]; })) PX.view = parts[1];
    if (parts[2]) PX.lib = parts[2];
  }
};
}());
