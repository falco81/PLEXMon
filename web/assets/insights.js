/* ---- Insights (plexmon 2.12.0) --------------------------------------------------------
   Who watched what and how, what makes Plex transcode, the episodes missing and coming,
   subtitles and audio, the same thing twice, each new file read once, and Plex Media
   Server itself: is it answering, a new version, its caches. Plex is only read here - its
   API and a copy of its database - and a sleeping disk is never woken. And Settings →
   Notifications: how a message is sent, a test, what was sent. */
var NX = {d: null, w: null, h: null, nf: null, sub: 'watching', days: 30, poll: null, live: null, on: false,
  q: {}, open: {}};
try { NX.sub = localStorage.getItem('plexmon-nx-sub') || 'watching'; NX.days = +(localStorage.getItem('plexmon-nx-days') || 30); } catch (e) { }
var NX_SUBS = [
  ['watching', 'Watching', 'play', 'Who watched what, when and how - with what is playing now.'],
  ['transcode', 'Transcoding', 'chart', 'What made Plex transcode, and which files of the library are likely to.'],
  ['episodes', 'Episodes', 'calendar', 'Aired episodes that are not in Plex, and what comes next - from TVmaze.'],
  ['langs', 'Subtitles and audio', 'chat', 'Where the subtitles or the audio in the languages wanted are missing.'],
  ['dups', 'Duplicates', 'copy', 'One film or episode in more than one version - which to keep.'],
  ['newfiles', 'New files', 'film', 'Each file Plex took in, read once by ffprobe - only on a disk that is awake.'],
  ['server', 'Plex server', 'server', 'Is it answering, how hard it works, a new version, its caches.']
];
var NX_DEC = {'direct play': ['Direct play', 'ok'], 'direct stream': ['Direct stream', 'info'], 'transcode': ['Transcode', 'warn']};

function nxTab(on) {
  NX.on = on;
  clearInterval(NX.live);
  NX.live = null;
  if (!on) return;
  nxLoad();
  NX.live = setInterval(function () {
    if (!NX.on || document.hidden) return;
    if (NX.sub === 'watching') nxLoadPart('watching', true);
    if (NX.sub === 'server') nxLoadPart('health', true);
  }, 20000);
}
function nxLoad() {
  api('insights').then(function (d) {
    NX.d = d;
    nxDraw();
    if (d.job && d.job.running) nxWatch();
    nxLoadSub();
  }).catch(oops);
}
function nxLoadSub() {
  if (NX.sub === 'watching' || NX.sub === 'transcode') nxLoadPart('watching');
  if (NX.sub === 'server') nxLoadPart('health');
  if (NX.sub === 'newfiles') nxLoadPart('newfiles');
}
function nxLoadPart(part, quiet) {
  var q = 'insights&part=' + part + (part === 'watching' ? '&days=' + NX.days : '');
  return api(q).then(function (x) {
    if (part === 'watching') NX.w = x;
    if (part === 'health') NX.h = x;
    if (part === 'newfiles') NX.nf = x;
    nxDrawBody();
  }).catch(function (e) { if (!quiet) oops(e); });
}
function nxWatch() {
  if (NX.poll) return;
  var step = function () {
    api('insights', {action: 'job'}).then(function (x) {
      var j = x.job || {};
      if (NX.d) NX.d.job = j;
      nxDrawJob();
      if (j.running) { NX.poll = setTimeout(step, 1000); return; }
      NX.poll = null;
      if (j.says === 'stopped') toast(txT(j.what) + ': ' + txT('stopped'), 'good');
      else if (j.error) toast(j.error, 'bad');
      else if (j.what) toast(txT(j.what) + ': ' + txT('done'), 'good');
      if (NX.on) nxLoad();
      if (PANE === 'notify' && TAB === 'settings') loadNotify();
    }).catch(function () { NX.poll = setTimeout(step, 3000); });
  };
  step();
}
function nxDo(body, after) {
  return api('insights', body).then(function (x) {
    if (x.job) { if (NX.d) NX.d.job = x.job; toast(x.message || 'started', 'good'); nxDrawJob(); nxWatch(); }
    else if (x.message && !x.confirm) toast(x.message, 'good');
    if (after) after(x);
    return x;
  }).catch(oops);
}
function nxRunning() { return !!(NX.d && NX.d.job && NX.d.job.running); }
function nxIcon(key, size) {
  var s = el('span', 'ix-ic');
  s.innerHTML = iconMarkup(key, size || 18);
  return s;
}
function nxPct(a, b) { return b ? Math.round(100 * a / b) : 0; }
function nxSE(s, e) { return 'S' + pad2(+s || 0) + 'E' + pad2(+e || 0); }
function nxHours(h) {
  var v = Number(h || 0);
  var t = v >= 100 ? Math.round(v).toString() : v.toFixed(1);
  if (numDecimal() === ',') t = t.replace('.', ',');
  return t;
}

/* ---- the frame: a title, the pills, the job, the body ------------------------------ */
function nxDraw() {
  var host = $('nx-host');
  if (!host) return;
  clear(host);
  var head = el('div', 'nx-head');
  var ht = el('div', 'nx-head-t');
  ht.appendChild(el('h2', 'ctl-title', 'Insights'));
  ht.appendChild(el('p', 'ctl-lede', 'What happens on this Plex server - read from its own API and a copy of its database. Nothing of Plex is changed by looking, and a sleeping disk is never woken.'));
  head.appendChild(ht);
  host.appendChild(head);
  var nav = el('div', 'nx-nav');
  nav.setAttribute('role', 'tablist');
  NX_SUBS.forEach(function (s) {
    var b = el('button', 'nx-pill' + (NX.sub === s[0] ? ' on' : ''));
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', NX.sub === s[0] ? 'true' : 'false');
    b.appendChild(nxIcon(s[2], 16));
    b.appendChild(el('span', '', s[1]));
    b.title = txT(s[3]);
    var badge = nxBadge(s[0]);
    if (badge) b.appendChild(badge);
    b.onclick = function () {
      NX.sub = s[0];
      try { localStorage.setItem('plexmon-nx-sub', s[0]); } catch (e) { }
      nav.querySelectorAll('.nx-pill').forEach(function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b ? 'true' : 'false'); });
      nxDrawBody();
      nxLoadSub();
    };
    nav.appendChild(b);
  });
  host.appendChild(nav);
  var jb = el('div', 'nx-jobhost');
  jb.id = 'nx-job';
  host.appendChild(jb);
  var body = el('div', 'nx-body');
  body.id = 'nx-body';
  host.appendChild(body);
  nxDrawJob();
  nxDrawBody();
}
function nxBadge(sub) {
  var d = NX.d || {}, n = 0, cls = 'dim';
  if (sub === 'episodes' && d.episodes) { n = d.episodes.totals.missing; cls = n ? 'warn' : 'dim'; }
  if (sub === 'dups' && d.library) { n = (d.library.dups || []).length; cls = 'dim'; }
  if (sub === 'watching' && NX.w) { n = (NX.w.live || []).length; cls = n ? 'ok live' : 'dim'; }
  if (!n) return null;
  return noI18n(el('span', 'nx-badge ' + cls, num(n)));
}
function nxDrawBody() {
  var body = $('nx-body');
  if (!body) return;
  clear(body);
  var fn = {watching: nxWatching, transcode: nxTranscode, episodes: nxEpisodes, langs: nxLangs, dups: nxDups,
    newfiles: nxNewfiles, server: nxServer}[NX.sub] || nxWatching;
  fn(body);
  dressButtons(body);
  explainWhatWasDrawn && explainWhatWasDrawn(body);
}

/* The job: a bar, what it says, Stop. */
function nxDrawJob() {
  var host = $('nx-job');
  if (!host || !NX.d) return;
  var j = NX.d.job || {};
  clear(host);
  if (!j.what || (!j.running && !j.error && (Date.now() / 1000 - (j.ended || 0)) > 120)) return;
  var c = el('div', 'card full ix-job nx-job' + (j.running ? ' on' : j.error ? ' failed' : ' done'));
  var h = el('div', 'ix-job-h');
  h.appendChild(el('b', '', j.what));
  h.appendChild(el('span', 'tag ' + (j.running ? 'info' : j.says === 'stopped' ? 'dim' : j.error ? 'crit' : 'ok'),
    j.running ? 'under way' : j.says === 'stopped' ? 'stopped' : j.error ? 'failed' : 'done'));
  if (j.running) {
    h.appendChild(noI18n(el('span', 'muted small', Math.round(Date.now() / 1000 - j.began) + ' s')));
    if (ADMIN) {
      var stp = cmdButton('Stop', 'cancel', 'small');
      stp.classList.add('right');
      stp.onclick = function () { busy(stp, true); api('insights', {action: 'stop'}).then(function (x) { toast(x.message, 'good'); }).catch(oops); };
      h.appendChild(stp);
    }
  }
  c.appendChild(h);
  var pc = j.whole ? Math.round(100 * j.done / j.whole) : (j.running ? 4 : 100);
  if (!j.running && !j.error) pc = 100;
  var bar = el('div', 'ix-bar');
  var fill = el('span', '');
  fill.style.width = Math.max(2, Math.min(100, pc)) + '%';
  bar.appendChild(fill);
  c.appendChild(bar);
  var says = el('div', 'ix-says');
  says.appendChild(el('span', '', j.error ? j.error : (j.says || '')));
  says.appendChild(noI18n(el('span', 'right muted', pc + ' %')));
  c.appendChild(says);
  host.appendChild(c);
  dressButtons(c);
}

/* ---- pieces drawn everywhere ------------------------------------------------------- */
function nxTiles(list) {
  var row = el('div', 'nx-tiles');
  list.forEach(function (t) {
    var x = el('div', 'nx-tile ' + (t.cls || 'info') + (t.zero ? ' zero' : ''));
    x.appendChild(nxIcon(t.icon || 'info', 20));
    var w = el('div', 'nx-tile-t');
    var b = noI18n(el('b', '', t.value));
    w.appendChild(b);
    w.appendChild(el('span', '', t.label));
    if (t.sub) w.appendChild(el('small', '', t.sub));
    x.appendChild(w);
    if (t.why) explain(x, t.why);
    row.appendChild(x);
  });
  return row;
}
function nxCard(title, icon, extra) {
  var c = el('div', 'card full nx-card');
  var h = el('div', 'nx-card-h');
  if (icon) h.appendChild(nxIcon(icon, 18));
  h.appendChild(el('h3', '', title));
  if (extra) { var r = el('div', 'nx-card-x'); extra.forEach(function (x) { r.appendChild(x); }); h.appendChild(r); }
  c.appendChild(h);
  return c;
}
function nxEmpty(icon, title, words, button) {
  var c = el('div', 'card full nx-empty');
  c.appendChild(nxIcon(icon, 30));
  c.appendChild(el('b', '', title));
  if (words) c.appendChild(el('p', '', words));
  if (button) c.appendChild(button);
  return c;
}
function nxMeta(at, by, took) {
  var m = el('div', 'nx-meta');
  m.appendChild(nxIcon('clock', 13));
  m.appendChild(el('span', '', 'Looked through'));
  m.appendChild(el('span', '', ago(at)));
  if (by || took) m.appendChild(noI18n(el('span', '', '· ' + (by || '') + (took ? ' · ' + took + ' s' : ''))));
  return m;
}
/* A bar per item: a label, a bar, a number. */
function nxBars(items, opts) {
  opts = opts || {};
  var box = el('div', 'nx-bars');
  var max = Math.max.apply(null, items.map(function (i) { return i.n; }).concat([1]));
  items.forEach(function (it) {
    var row = el('div', 'nx-barrow');
    var lab = el('span', 'nx-bar-l', it.label);
    if (opts.raw || it.raw) noI18n(lab);
    lab.title = it.label;
    row.appendChild(lab);
    var tr = el('span', 'nx-bar-t');
    var f = el('span', 'nx-bar-f ' + (it.cls || opts.cls || 'info'));
    f.style.width = Math.max(2, 100 * it.n / max) + '%';
    tr.appendChild(f);
    row.appendChild(tr);
    row.appendChild(noI18n(el('span', 'nx-bar-n', it.text || num(it.n))));
    box.appendChild(row);
  });
  return box;
}
/* A ring of parts: [{n, cls, label}]. */
function nxDonut(parts, mid, sub) {
  var total = parts.reduce(function (a, p) { return a + p.n; }, 0);
  var box = el('div', 'nx-donut');
  var r = 46, c = 2 * Math.PI * r, at = 0;
  var svg = '<svg viewBox="0 0 120 120"><circle class="bg" cx="60" cy="60" r="' + r + '"/>';
  parts.forEach(function (p) {
    if (!p.n || !total) return;
    var len = c * p.n / total;
    svg += '<circle class="seg ' + p.cls + '" cx="60" cy="60" r="' + r + '" style="stroke-dasharray:' + Math.max(0, len - 2).toFixed(1) + ' ' + c.toFixed(1) + ';stroke-dashoffset:' + (-at).toFixed(1) + '"/>';
    at += len;
  });
  svg += '</svg>';
  var ring = el('div', 'nx-donut-ring');
  ring.innerHTML = svg;
  var m = el('div', 'nx-donut-mid');
  m.appendChild(noI18n(el('b', '', mid)));
  if (sub) m.appendChild(el('span', '', sub));
  ring.appendChild(m);
  box.appendChild(ring);
  var leg = el('div', 'nx-legend');
  parts.forEach(function (p) {
    var row = el('div', 'nx-leg');
    row.appendChild(el('i', 'nx-sw ' + p.cls));
    row.appendChild(el('span', '', p.label));
    row.appendChild(noI18n(el('b', '', num(p.n))));
    row.appendChild(noI18n(el('small', 'muted', total ? nxPct(p.n, total) + ' %' : '')));
    leg.appendChild(row);
  });
  box.appendChild(leg);
  return box;
}
/* Columns per day, stacked: series [{key, cls, label}]. */
function nxColumns(rows, series, opts) {
  opts = opts || {};
  var W = 760, H = 190, pad = 26, n = rows.length || 1;
  var totals = rows.map(function (r) { return series.reduce(function (a, s) { return a + (r[s.key] || 0); }, 0); });
  var max = Math.max.apply(null, totals.concat([1]));
  var nice = Math.max(1, Math.ceil(max / 4)) * 4;
  var bw = (W - pad) / n, g = Math.min(4, bw * 0.25);
  var svg = '<svg viewBox="0 0 ' + W + ' ' + (H + 22) + '" preserveAspectRatio="none" class="nx-cols">';
  for (var i = 0; i <= 4; i++) {
    var y = H - (H - 8) * i / 4;
    svg += '<line class="grid" x1="' + pad + '" x2="' + W + '" y1="' + y + '" y2="' + y + '"/>';
    svg += '<text class="ax" x="' + (pad - 6) + '" y="' + (y + 4) + '" text-anchor="end">' + Math.round(nice * i / 4) + '</text>';
  }
  rows.forEach(function (r, k) {
    var x = pad + k * bw + g / 2, y0 = H, tip = (r.label || r.day) + ': ';
    series.forEach(function (s) {
      var v = r[s.key] || 0;
      if (!v) return;
      var hgt = (H - 8) * v / nice;
      y0 -= hgt;
      svg += '<rect class="b ' + s.cls + '" x="' + x.toFixed(1) + '" y="' + y0.toFixed(1) + '" width="' + Math.max(1, bw - g).toFixed(1) + '" height="' + Math.max(0, hgt - 0.5).toFixed(1) + '" rx="2"/>';
      tip += txT(s.label) + ' ' + v + '  ';
    });
    svg += '<rect class="hit" x="' + (pad + k * bw).toFixed(1) + '" y="0" width="' + bw.toFixed(1) + '" height="' + H + '"><title>' + htmlText(tip) + '</title></rect>';
    var every = Math.ceil(n / 8);
    if (k % every === 0) svg += '<text class="ax" x="' + (x + (bw - g) / 2).toFixed(1) + '" y="' + (H + 16) + '" text-anchor="middle">' + htmlText(r.short || '') + '</text>';
  });
  svg += '</svg>';
  var box = noI18n(el('div', 'nx-chart'));
  box.innerHTML = svg;
  if (opts.legend !== false) {
    var leg = el('div', 'nx-legend row');
    series.forEach(function (s) {
      var row = el('div', 'nx-leg');
      row.appendChild(el('i', 'nx-sw ' + s.cls));
      row.appendChild(el('span', '', s.label));
      leg.appendChild(row);
    });
    var wrap = el('div', 'nx-chartwrap');
    wrap.appendChild(box);
    wrap.appendChild(leg);
    return wrap;
  }
  return box;
}
function nxSpark(points, cls) {
  var W = 300, H = 60;
  if (!points.length) return el('div', 'nx-spark none');
  var max = Math.max(100, Math.max.apply(null, points.map(function (p) { return p[1]; })));
  var t0 = points[0][0], t1 = points[points.length - 1][0] || t0 + 1;
  var d = points.map(function (p, i) {
    var x = (W * (p[0] - t0) / Math.max(1, t1 - t0)).toFixed(1), y = (H - 2 - (H - 4) * p[1] / max).toFixed(1);
    return (i ? 'L' : 'M') + x + ' ' + y;
  }).join(' ');
  var box = noI18n(el('div', 'nx-spark ' + (cls || '')));
  box.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none"><path class="area" d="' + d + ' L' + W + ' ' + H + ' L0 ' + H + ' Z"/><path class="ln" d="' + d + '"/></svg>';
  return box;
}
function nxDecTag(dec) {
  var x = NX_DEC[dec] || [dec || '?', 'dim'];
  return el('span', 'nx-dec ' + x[1], x[0]);
}
function nxFilter(key, placeholder, after) {
  var i = el('input');
  i.type = 'search';
  i.className = 'nx-find';
  i.placeholder = txT(placeholder || 'Find…');
  i.value = NX.q[key] || '';
  i.oninput = function () { NX.q[key] = i.value; clearTimeout(i._t); i._t = setTimeout(after, 200); };
  return i;
}
function nxMatch(key, text) {
  var q = (NX.q[key] || '').trim().toLowerCase();
  return !q || String(text || '').toLowerCase().indexOf(q) >= 0;
}
function nxMore(list, cap, draw, host, key) {
  var shown = NX.open[key] ? list : list.slice(0, cap);
  shown.forEach(function (x, i) { host.appendChild(draw(x, i)); });
  if (list.length > cap) {
    var b = el('button', 'btn small nx-more', NX.open[key] ? 'Show fewer' : 'Show all');
    b.type = 'button';
    b.appendChild(noI18n(el('span', 'muted', ' (' + num(list.length) + ')')));
    b.onclick = function () { NX.open[key] = !NX.open[key]; nxDrawBody(); };
    return b;
  }
  return null;
}

/* ---- Watching --------------------------------------------------------------------- */
function nxWatching(body) {
  var w = NX.w;
  body.appendChild(nxLive(w));
  var bar = el('div', 'nx-toolbar');
  var rg = el('div', 'nx-seg');
  [[7, '7 days'], [30, '30 days'], [90, '90 days'], [365, 'A year']].forEach(function (o) {
    var b = el('button', 'nx-seg-b' + (NX.days === o[0] ? ' on' : ''), o[1]);
    b.type = 'button';
    b.onclick = function () {
      NX.days = o[0];
      try { localStorage.setItem('plexmon-nx-days', String(o[0])); } catch (e) { }
      NX.w = null; nxDrawBody(); nxLoadPart('watching');
    };
    rg.appendChild(b);
  });
  bar.appendChild(rg);
  bar.appendChild(el('span', 'muted small', 'The plays from Plex\'s own history; how they were played from plexmon\'s look at Plex every minute (from 2.12.0 on).'));
  body.appendChild(bar);
  if (!w) { body.appendChild(el('div', 'nx-loading', 'Loading…')); return; }
  if (w.error) {
    var b0 = el('div', 'ix-banner crit');
    b0.appendChild(nxIcon('info'));
    b0.appendChild(el('div', '', w.error));
    body.appendChild(b0);
  }
  var t = w.totals || {};
  var seen = t.direct + t.stream + t.transcode;
  body.appendChild(nxTiles([
    {cls: 'info', icon: 'play', value: num(t.plays), label: 'Plays', sub: txT('in Plex\'s history'), zero: !t.plays},
    {cls: 'violet', icon: 'clock', value: nxHours(t.hours) + ' h', label: 'Hours watched', sub: txT('seen by plexmon'), zero: !t.hours},
    {cls: 'ok', icon: 'people', value: num(t.users), label: 'People', zero: !t.users},
    {cls: t.transcode ? 'warn' : 'ok', icon: 'chart', value: (seen ? nxPct(t.transcode, seen) : 0) + ' %', label: 'Transcoded', sub: num(t.transcode) + ' / ' + num(seen), zero: !seen,
      why: 'Of the plays plexmon saw, how many Plex had to transcode the picture of. Direct play and direct stream cost the processor almost nothing.'},
    {cls: 'dim', icon: 'globe', value: num(t.remote), label: 'From outside', zero: !t.remote, why: 'Plays from a player that is not on this network.'}
  ]));
  var grid = el('div', 'nx-grid2');
  var c1 = nxCard('Plays per day', 'chart');
  var rows = (w.series || []).map(function (r) {
    var p = r.day.split('-');
    return {day: r.day, label: p[2] + '-' + p[1] + '-' + p[0], short: p[2] + '.' + p[1] + '.', plays: r.plays, direct: r.direct, stream: r.stream, transcode: r.transcode};
  });
  var anySample = rows.some(function (r) { return r.direct + r.stream + r.transcode > 0; });
  if (anySample) {
    rows.forEach(function (r) { r.other = Math.max(0, r.plays - r.direct - r.stream - r.transcode); });
    c1.appendChild(nxColumns(rows, [{key: 'direct', cls: 'ok', label: 'Direct play'}, {key: 'stream', cls: 'info', label: 'Direct stream'}, {key: 'transcode', cls: 'warn', label: 'Transcode'},
      {key: 'other', cls: 'dim', label: 'Not seen by plexmon'}]));
  } else {
    c1.appendChild(nxColumns(rows, [{key: 'plays', cls: 'info', label: 'Plays'}]));
  }
  grid.appendChild(c1);
  var c2 = nxCard('How it was played', 'play');
  if (seen) {
    c2.appendChild(nxDonut([{n: t.direct, cls: 'ok', label: 'Direct play'}, {n: t.stream, cls: 'info', label: 'Direct stream'}, {n: t.transcode, cls: 'warn', label: 'Transcode'}],
      nxPct(t.direct + t.stream, seen) + ' %', 'without transcoding'));
  } else c2.appendChild(el('p', 'muted small', 'Nothing seen yet - plexmon looks at Plex once a minute, from now on.'));
  if ((w.why || []).length) {
    c2.appendChild(el('div', 'nx-sub-h', 'Why Plex transcoded'));
    c2.appendChild(nxBars(w.why.map(function (x) { return {label: x.what, n: x.n, cls: 'warn', raw: true}; })));
  }
  grid.appendChild(c2);
  body.appendChild(grid);
  var grid2 = el('div', 'nx-grid2');
  var c3 = nxCard('Watched most', 'star');
  if ((w.top || []).length) {
    var lst = el('ol', 'nx-rank');
    var max = w.top[0].plays || 1;
    w.top.forEach(function (x, i) {
      var li = el('li', '');
      li.appendChild(noI18n(el('span', 'nx-rank-n', String(i + 1))));
      var mid = el('div', 'nx-rank-b');
      var tt = el('div', 'nx-rank-t');
      tt.appendChild(noI18n(el('b', '', x.title)));
      tt.appendChild(el('span', 'tag dim', x.kind === 'show' ? 'TV show' : 'Film'));
      mid.appendChild(tt);
      var tr = el('span', 'nx-bar-t thin');
      var f = el('span', 'nx-bar-f ' + (i < 3 ? 'violet' : 'info'));
      f.style.width = Math.max(3, 100 * x.plays / max) + '%';
      tr.appendChild(f);
      mid.appendChild(tr);
      li.appendChild(mid);
      li.appendChild(noI18n(el('span', 'nx-rank-c', num(x.plays) + '×')));
      lst.appendChild(li);
    });
    c3.appendChild(lst);
  } else c3.appendChild(el('p', 'muted small', 'Nothing watched in this time.'));
  grid2.appendChild(c3);
  var c4 = nxCard('People', 'people');
  if ((w.users || []).length) {
    var tb = el('table', 'rows nx-table');
    var th = el('thead');
    var hr = el('tr');
    ['Who', 'Plays', 'Hours', 'Transcoded', 'Last'].forEach(function (x, i) { hr.appendChild(el('th', i ? 'n' : '', x)); });
    th.appendChild(hr);
    tb.appendChild(th);
    var tbd = el('tbody');
    w.users.forEach(function (u) {
      var tr2 = el('tr');
      var who = el('td', 'nx-who');
      who.appendChild(noI18n(el('span', 'nx-avatar', (u.user || '?').charAt(0).toUpperCase())));
      who.appendChild(noI18n(el('span', '', u.user)));
      tr2.appendChild(who);
      tr2.appendChild(noI18n(el('td', 'n', num(u.plays))));
      tr2.appendChild(noI18n(el('td', 'n', nxHours(u.hours))));
      tr2.appendChild(noI18n(el('td', 'n' + (u.transcode ? ' warnt' : ''), num(u.transcode))));
      tr2.appendChild(el('td', 'n muted', ago(u.last)));
      tbd.appendChild(tr2);
    });
    tb.appendChild(tbd);
    var sc = el('div', 'nx-scroll');
    sc.appendChild(tb);
    c4.appendChild(sc);
  } else c4.appendChild(el('p', 'muted small', 'Nobody yet.'));
  grid2.appendChild(c4);
  body.appendChild(grid2);
  if ((w.players || []).length) {
    var c5 = nxCard('Players', 'tv');
    var pl = el('div', 'nx-players');
    w.players.forEach(function (p) {
      var all = p.direct + p.stream + p.transcode || 1;
      var row = el('div', 'nx-player');
      row.appendChild(noI18n(el('span', 'nx-player-n', p.player)));
      var st = el('span', 'nx-stack');
      [['direct', 'ok'], ['stream', 'info'], ['transcode', 'warn']].forEach(function (k) {
        if (!p[k[0]]) return;
        var s = el('span', 'nx-bar-f ' + k[1]);
        s.style.width = (100 * p[k[0]] / all) + '%';
        s.title = p[k[0]] + '';
        st.appendChild(s);
      });
      row.appendChild(st);
      row.appendChild(noI18n(el('span', 'nx-bar-n', num(all))));
      pl.appendChild(row);
    });
    c5.appendChild(pl);
    body.appendChild(c5);
  }
  var c6 = nxCard('What was played lately', 'history');
  if ((w.recent || []).length) {
    var list = el('div', 'nx-feed');
    w.recent.forEach(function (r) {
      var row = el('div', 'nx-feed-r');
      row.appendChild(nxDecTag(r.decision));
      var b = el('div', 'nx-feed-b');
      var t1 = el('div', 'nx-feed-t');
      t1.appendChild(noI18n(el('b', '', r.show ? r.show + ' · ' + nxSE(r.season, r.ep) : r.title)));
      if (r.show) t1.appendChild(noI18n(el('span', 'muted', r.title)));
      b.appendChild(t1);
      var t2 = el('div', 'nx-feed-s');
      t2.appendChild(noI18n(el('span', '', r.user + ' · ' + (r.product || r.player || '') + (r.local ? '' : ' · ↗'))));
      if (r.reasons) t2.appendChild(noI18n(el('span', 'nx-why', r.reasons)));
      b.appendChild(t2);
      row.appendChild(b);
      var rt = el('div', 'nx-feed-at');
      rt.appendChild(noI18n(el('span', '', stamp(r.started))));
      if (r.watched) rt.appendChild(noI18n(el('span', 'muted', span(r.watched))));
      row.appendChild(rt);
      list.appendChild(row);
    });
    c6.appendChild(list);
  } else c6.appendChild(el('p', 'muted small', 'plexmon has not seen anything played yet - it looks at Plex once a minute.'));
  body.appendChild(c6);
}
function nxLive(w) {
  var c = el('div', 'card full nx-live');
  var h = el('div', 'nx-card-h');
  var dot = el('span', 'nx-livedot' + (w && (w.live || []).length ? ' on' : ''));
  h.appendChild(dot);
  h.appendChild(el('h3', '', 'Playing now'));
  if (w && w.live_at) h.appendChild(el('span', 'muted small nx-card-x', ago(w.live_at)));
  c.appendChild(h);
  var live = (w && w.live) || [];
  if (!live.length) {
    c.appendChild(el('p', 'muted small nx-live-none', w ? 'Nobody is watching.' : 'Loading…'));
    return c;
  }
  var g = el('div', 'nx-live-g');
  live.forEach(function (s) {
    var x = el('div', 'nx-stream ' + ((NX_DEC[s.decision] || [])[1] || 'dim'));
    var top = el('div', 'nx-stream-t');
    top.appendChild(nxIcon(s.kind === 'episode' ? 'tv' : s.kind === 'track' ? 'music' : 'film', 18));
    var tt = el('div', 'nx-stream-n');
    tt.appendChild(noI18n(el('b', '', s.show || s.title)));
    tt.appendChild(noI18n(el('span', '', s.show ? nxSE(s.season, s.ep) + ' · ' + s.title : (s.res ? s.res.toUpperCase() : ''))));
    top.appendChild(tt);
    if (s.state === 'paused') top.appendChild(el('span', 'tag dim', 'paused'));
    x.appendChild(top);
    var who = el('div', 'nx-stream-w');
    who.appendChild(noI18n(el('span', 'nx-avatar small', (s.user || '?').charAt(0).toUpperCase())));
    who.appendChild(noI18n(el('span', '', s.user + ' · ' + (s.product || '') + (s.player ? ' (' + s.player + ')' : ''))));
    if (!s.local) who.appendChild(el('span', 'tag info', 'outside'));
    x.appendChild(who);
    var dec = el('div', 'nx-stream-d');
    dec.appendChild(nxDecTag(s.decision));
    if (s.hw) dec.appendChild(el('span', 'tag ok', 'hardware'));
    if (s.reasons) dec.appendChild(noI18n(el('span', 'nx-why', s.reasons)));
    x.appendChild(dec);
    var bar = el('div', 'nx-prog');
    var f = el('span', '');
    f.style.width = (s.duration ? Math.min(100, 100 * s.offset / s.duration) : 0) + '%';
    bar.appendChild(f);
    x.appendChild(bar);
    var ft = noI18n(el('div', 'nx-stream-f'));
    ft.appendChild(el('span', '', span(s.offset) + ' / ' + span(s.duration)));
    ft.appendChild(el('span', '', [s.vcodec, s.acodec, s.container].filter(Boolean).join(' · ').toUpperCase() + (s.bandwidth ? ' · ' + (s.bandwidth / 1000).toFixed(1) + ' Mb/s' : '')));
    x.appendChild(ft);
    g.appendChild(x);
  });
  c.appendChild(g);
  return c;
}

/* ---- Transcoding ------------------------------------------------------------------ */
var NX_ADVICE = [
  [/HEVC|H\.265|AV1|VP9/i, 'The picture is in a codec the player cannot play: a player that knows it (Apple TV 4K, Shield, a recent TV app) plays it directly; otherwise only a version in H.264 helps (Plex → Optimize, or ffmpeg).'],
  [/4K/, 'A 4K picture to a 1080p player, or over a slow line: a 1080p version beside it (Plex → Optimize → Optimized for TV), or the player set to original quality.'],
  [/Mb\/s/, 'A bit rate higher than the player or the line allows: the quality of the player set to original / maximum, or a version with a lower bit rate.'],
  [/TrueHD|DTS|MLP/i, 'The audio is TrueHD or DTS, which few players decode: a second audio track in AC-3/E-AC-3 beside it costs nothing to play - ffmpeg -i in.mkv -map 0 -c copy -map 0:a:0 -c:a:1 eac3 out.mkv'],
  [/PGS|VOBSUB|DVD_SUB|DVB/i, 'Picture subtitles (PGS, VobSub) are burned into the picture for most players - the whole film transcoded. Subtitles as text (SRT) beside them, or turned into text (Subtitle Edit, OCR).'],
  [/AVI|WMV|ASF|MPEG|MPG|FLV|VOB|M2TS|\bTS\b|container/i, 'An old container: remuxed to MKV or MP4 without touching the picture - ffmpeg -i in.avi -c copy out.mkv']
];
function nxAdvice(reasons) {
  var said = [];
  NX_ADVICE.forEach(function (a) {
    if (reasons.some(function (r) { return a[0].test(r); }) && said.indexOf(a[1]) < 0) said.push(a[1]);
  });
  return said;
}
function nxTranscode(body) {
  var lib = NX.d && NX.d.library, w = NX.w;
  var top = el('div', 'card full nx-hero');
  var ht = el('div', 'nx-hero-t');
  ht.appendChild(el('h2', '', 'What makes Plex transcode'));
  ht.appendChild(el('p', 'ix-lede', 'Transcoding costs the processor - a 4K picture may be more than a small one keeps up with. Below: what Plex really transcoded, seen once a minute - and which files of the library are likely to be, read from a copy of its database.'));
  if (lib) ht.appendChild(nxMeta(lib.at, lib.by, lib.took));
  top.appendChild(ht);
  if (ADMIN) {
    var go = cmdButton(lib ? 'Look through the files again' : 'Look through the files', 'search', 'primary');
    go.disabled = nxRunning();
    explain(go, 'One look through a copy of Plex\'s database gives Transcoding, Subtitles and audio, and Duplicates together. Plex goes on; no disk is read.');
    go.onclick = function () { nxDo({action: 'analyse'}); };
    top.appendChild(go);
  }
  body.appendChild(top);
  var rk = lib ? lib.risk : null;
  var t = (w && w.totals) || {};
  body.appendChild(nxTiles([
    {cls: t.transcode ? 'warn' : 'ok', icon: 'play', value: num(t.transcode || 0), label: 'Transcoded plays', sub: txT('seen by plexmon'), zero: !t.transcode},
    {cls: rk && rk.count ? 'warn' : 'ok', icon: 'film', value: rk ? num(rk.count) : '–', label: 'Files likely to be transcoded', sub: lib ? num(lib.items) + ' ' + txT('files in all') : '', zero: !rk || !rk.count},
    {cls: 'violet', icon: 'eye', value: rk ? num(rk.uhd) : '–', label: '4K files', zero: !rk || !rk.uhd},
    {cls: 'info', icon: 'chat', value: rk ? num(Object.keys(rk.subs).reduce(function (a, k) { return a + rk.subs[k]; }, 0)) : '–', label: 'With picture subtitles', zero: !rk}
  ]));
  var grid = el('div', 'nx-grid2');
  var c1 = nxCard('Transcoded lately', 'history');
  var tl = (w && w.transcoded) || [];
  if (tl.length) {
    var lst = el('div', 'nx-list');
    tl.forEach(function (x) {
      var row = el('div', 'nx-row');
      row.appendChild(noI18n(el('span', 'nx-count warn', num(x.n) + '×')));
      var b = el('div', 'nx-row-b');
      b.appendChild(noI18n(el('b', '', x.title)));
      var ch = el('div', 'ix-chips');
      x.reasons.forEach(function (r) { ch.appendChild(noI18n(el('span', 'ix-chip', r))); });
      x.players.forEach(function (r) { ch.appendChild(noI18n(el('span', 'ix-chip dim', r))); });
      b.appendChild(ch);
      nxAdvice(x.reasons).forEach(function (a) { b.appendChild(el('div', 'nx-advice', a)); });
      row.appendChild(b);
      lst.appendChild(row);
    });
    c1.appendChild(lst);
  } else c1.appendChild(el('p', 'muted small', w ? 'Nothing transcoded in this time - or not seen yet.' : 'Loading…'));
  grid.appendChild(c1);
  var c2 = nxCard('The library, by what may need transcoding', 'chart');
  if (rk) {
    var items = [];
    Object.keys(rk.video).forEach(function (k) { items.push({label: k, n: rk.video[k], cls: 'warn', raw: true}); });
    if (rk.uhd) items.push({label: '4K', n: rk.uhd, cls: 'violet', raw: true});
    if (rk.bitrate) items.push({label: '> 40 Mb/s', n: rk.bitrate, cls: 'violet', raw: true});
    Object.keys(rk.audio).forEach(function (k) { items.push({label: k, n: rk.audio[k], cls: 'info', raw: true}); });
    Object.keys(rk.subs).forEach(function (k) { items.push({label: 'Subtitles ' + k, n: rk.subs[k], cls: 'crit', raw: true}); });
    Object.keys(rk.container).forEach(function (k) { items.push({label: k, n: rk.container[k], cls: 'dim', raw: true}); });
    items.sort(function (a, b) { return b.n - a.n; });
    if (items.length) c2.appendChild(nxBars(items));
    else c2.appendChild(el('p', 'muted small', 'Nothing in the library is likely to be transcoded on a common player.'));
    var tips = el('details', 'ix-why');
    tips.appendChild(el('summary', '', 'What helps'));
    NX_ADVICE.forEach(function (a) { tips.appendChild(el('p', '', a[1])); });
    c2.appendChild(tips);
  } else c2.appendChild(el('p', 'muted small', 'Not looked through yet.'));
  grid.appendChild(c2);
  body.appendChild(grid);
  if (lib && (lib.risky || []).length) {
    var c3 = nxCard('Files likely to be transcoded', 'film', [nxFilter('risky', 'Find a title or a reason…', nxDrawBody)]);
    var list = el('div', 'nx-list');
    var rows = lib.risky.filter(function (x) { return nxMatch('risky', x.show + ' ' + x.title + ' ' + x.reasons.join(' ') + ' ' + x.file); });
    var more = nxMore(rows, 40, function (x) {
      var row = el('div', 'nx-row');
      row.appendChild(nxIcon(x.kind === 'episode' ? 'tv' : 'film', 16));
      var b = el('div', 'nx-row-b');
      var tt = el('div', 'nx-row-t');
      tt.appendChild(noI18n(el('b', '', x.show ? x.show + ' · ' + nxSE(x.s, x.e) : x.title)));
      var ch = el('span', 'ix-chips');
      x.reasons.forEach(function (r) { ch.appendChild(noI18n(el('span', 'ix-chip ' + (/4K|Mb/.test(r) ? 'violet' : /Subtitles|subtitles/.test(r) ? 'crit' : /TrueHD|DTS/.test(r) ? 'info' : 'warn'), r))); });
      tt.appendChild(ch);
      b.appendChild(tt);
      b.appendChild(noI18n(el('span', 'mono ix-file muted', x.file)));
      row.appendChild(b);
      return row;
    }, list, 'risky');
    c3.appendChild(list);
    if (more) c3.appendChild(more);
    body.appendChild(c3);
  }
}

/* ---- Episodes (TVmaze) ------------------------------------------------------------ */
function nxEpisodes(body) {
  var ep = NX.d && NX.d.episodes;
  var top = el('div', 'card full nx-hero');
  var ht = el('div', 'nx-hero-t');
  ht.appendChild(el('h2', '', 'Missing and coming episodes'));
  ht.appendChild(el('p', 'ix-lede', 'Every show of Plex looked up on TVmaze (no key, no account): the episodes that have aired and are not in Plex, and the ones that come next. Specials (season 0) are left out. A show is asked again after a day - a show that has ended after a week.'));
  if (ep) ht.appendChild(nxMeta(ep.at, ep.by, ep.took));
  top.appendChild(ht);
  if (ADMIN) {
    var go = cmdButton(ep ? 'Look them up again' : 'Look them up on TVmaze', 'search', 'primary');
    go.disabled = nxRunning() || (NX.d && !NX.d.tvmaze);
    if (NX.d && !NX.d.tvmaze) explain(go, 'TVmaze is switched off - Settings → Plex library.');
    go.onclick = function () { nxDo({action: 'episodes'}); };
    top.appendChild(go);
  }
  body.appendChild(top);
  if (!ep) {
    body.appendChild(nxEmpty('calendar', 'Not looked up yet', 'The first look asks TVmaze about every show - a few minutes for a big library; after that only what is not fresh.'));
    return;
  }
  var t = ep.totals;
  body.appendChild(nxTiles([
    {cls: 'info', icon: 'tv', value: num(t.shows), label: 'Shows found on TVmaze', sub: t.unknown ? num(t.unknown) + ' ' + txT('not found') : ''},
    {cls: 'ok', icon: 'check', value: num(t.complete), label: 'Complete', zero: !t.complete},
    {cls: t.missing ? 'warn' : 'ok', icon: 'download', value: num(t.missing), label: 'Aired episodes missing', zero: !t.missing},
    {cls: 'violet', icon: 'calendar', value: num((ep.upcoming || []).length), label: 'Coming in 60 days', zero: !(ep.upcoming || []).length}
  ]));
  var grid = el('div', 'nx-grid2 wide-left');
  var c1 = nxCard('Shows with episodes missing', 'list', [nxFilter('eps', 'Find a show…', nxDrawBody)]);
  var miss = ep.shows.filter(function (s) { return s.n_missing && nxMatch('eps', s.title); });
  if (!miss.length) c1.appendChild(el('p', 'muted small', 'Every aired episode is in Plex.'));
  var list = el('div', 'nx-shows');
  var more = nxMore(miss, 25, function (s) {
    var x = el('div', 'nx-show');
    var h = el('div', 'nx-show-h');
    h.appendChild(noI18n(el('b', '', s.title)));
    h.appendChild(el('span', 'tag ' + (s.status === 'Running' ? 'ok' : s.status === 'Ended' ? 'dim' : 'info'), s.status === 'Running' ? 'running' : s.status === 'Ended' ? 'ended' : (s.status || '?')));
    if (s.network) h.appendChild(noI18n(el('span', 'muted small', s.network)));
    if (s.url) {
      var a = el('a', 'nx-ext', 'TVmaze');
      a.href = s.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      h.appendChild(noI18n(a));
    }
    x.appendChild(h);
    var pr = el('div', 'nx-show-p');
    var tr = el('span', 'nx-bar-t');
    var f = el('span', 'nx-bar-f ok');
    f.style.width = nxPct(s.have, s.aired) + '%';
    tr.appendChild(f);
    pr.appendChild(tr);
    pr.appendChild(noI18n(el('span', 'nx-bar-n', num(s.have) + ' / ' + num(s.aired))));
    x.appendChild(pr);
    var ch = el('div', 'ix-chips');
    var key = 'ep:' + s.title;
    var ms = NX.open[key] ? s.missing : s.missing.slice(0, 18);
    ms.forEach(function (e) {
      var c = noI18n(el('span', 'ix-chip warn', nxSE(e[0], e[1])));
      c.title = (e[2] || '') + (e[3] ? ' · ' + e[3] : '');
      ch.appendChild(c);
    });
    if (s.n_missing > ms.length) {
      var mb = noI18n(el('button', 'ix-chip found', '+' + (s.n_missing - ms.length)));
      mb.type = 'button';
      mb.onclick = function () { NX.open[key] = true; nxDrawBody(); };
      ch.appendChild(mb);
    }
    x.appendChild(ch);
    if (s.next) {
      var nx = el('div', 'nx-show-next');
      nx.appendChild(nxIcon('calendar', 13));
      nx.appendChild(el('span', '', 'Next:'));
      nx.appendChild(noI18n(el('span', '', nxSE(s.next[0], s.next[1]) + ' · ' + nxDate(s.next[3]) + (s.next[2] ? ' · ' + s.next[2] : ''))));
      x.appendChild(nx);
    }
    return x;
  }, list, 'eps');
  c1.appendChild(list);
  if (more) c1.appendChild(more);
  grid.appendChild(c1);
  var c2 = nxCard('Coming', 'calendar');
  var up = ep.upcoming || [];
  if (!up.length) c2.appendChild(el('p', 'muted small', 'Nothing in the next 60 days.'));
  var agenda = el('div', 'nx-agenda');
  var lastDay = '';
  up.slice(0, NX.open.up ? 200 : 40).forEach(function (u) {
    if (u.date !== lastDay) {
      lastDay = u.date;
      var dh = el('div', 'nx-day');
      var d = new Date(u.date + 'T12:00:00');
      var big = noI18n(el('b', '', String(d.getDate())));
      dh.appendChild(big);
      dh.appendChild(el('span', '', ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][d.getDay()]));
      dh.appendChild(noI18n(el('small', 'muted', nxDate(u.date))));
      var days = Math.round((d - new Date()) / 86400000);
      if (days <= 1) dh.appendChild(el('span', 'tag ok', days <= 0 ? 'today' : 'tomorrow'));
      agenda.appendChild(dh);
    }
    var row = el('div', 'nx-agenda-r');
    row.appendChild(noI18n(el('b', '', u.show)));
    row.appendChild(noI18n(el('span', 'ix-chip', nxSE(u.s, u.e))));
    if (u.name) row.appendChild(noI18n(el('span', 'muted small', u.name)));
    agenda.appendChild(row);
  });
  c2.appendChild(agenda);
  if (up.length > 40) {
    var b2 = el('button', 'btn small nx-more', NX.open.up ? 'Show fewer' : 'Show all');
    b2.type = 'button';
    b2.onclick = function () { NX.open.up = !NX.open.up; nxDrawBody(); };
    c2.appendChild(b2);
  }
  grid.appendChild(c2);
  body.appendChild(grid);
  if ((ep.unknown || []).length) {
    var c3 = el('details', 'card full nx-fold');
    c3.appendChild(el('summary', '', 'Not found on TVmaze'));
    c3.appendChild(el('p', 'muted small', 'Plex has no TheTVDB or IMDb number for them, or TVmaze does not know them - fix the match in Plex.'));
    var ch2 = el('div', 'ix-chips');
    ep.unknown.forEach(function (u) { ch2.appendChild(noI18n(el('span', 'ix-chip', u.title))); });
    c3.appendChild(ch2);
    body.appendChild(c3);
  }
}
function nxDate(s) {
  if (!s) return '';
  var p = String(s).split('-');
  return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : s;
}

/* ---- Subtitles and audio ---------------------------------------------------------- */
function nxLangs(body) {
  var lib = NX.d && NX.d.library;
  var top = nxLibHero('Subtitles and audio', 'Which episodes and films miss the subtitles or the audio in the languages wanted - read from the tracks Plex knows of (a subtitle file beside the video counts too).');
  body.appendChild(top);
  var want = el('div', 'nx-want');
  var L = (lib && lib.langs) || {};
  want.appendChild(el('span', '', 'Subtitles wanted:'));
  want.appendChild(noI18n(el('b', '', (NX.d && NX.d.langs.subs) || '–')));
  want.appendChild(el('span', '', 'Audio wanted:'));
  want.appendChild(noI18n(el('b', '', (NX.d && NX.d.langs.audio) || '–')));
  if (ADMIN) {
    var a = el('a', 'nx-ext', 'Change it');
    a.href = '#settings/notify';
    a.onclick = function (e) { e.preventDefault(); showTab('settings'); showPane('notify'); };
    want.appendChild(a);
  }
  body.appendChild(want);
  if (!lib) { body.appendChild(nxEmpty('chat', 'Not looked through yet', 'One look through a copy of Plex\'s database gives this, Transcoding and Duplicates together.')); return; }
  if (NX.d && (L.want_subs || '') !== (NX.d.langs.subs || '') || (L.want_audio || '') !== (NX.d.langs.audio || '')) {
    var bn = el('div', 'ix-banner');
    bn.appendChild(nxIcon('info'));
    bn.appendChild(el('div', '', 'The languages wanted were changed since the last look - look through the files again.'));
    body.appendChild(bn);
  }
  var e = L.episodes || {n: 0, subs: 0, audio: 0}, m = L.movies || {n: 0, subs: 0, audio: 0};
  var tiles = [];
  if ((L.subs || []).length) {
    tiles.push({cls: 'info', icon: 'chat', value: nxPct(e.subs, e.n) + ' %', label: 'Episodes with subtitles', sub: num(e.subs) + ' / ' + num(e.n)});
    tiles.push({cls: 'info', icon: 'film', value: nxPct(m.subs, m.n) + ' %', label: 'Films with subtitles', sub: num(m.subs) + ' / ' + num(m.n)});
  }
  if ((L.audio || []).length) {
    tiles.push({cls: 'violet', icon: 'music', value: nxPct(e.audio, e.n) + ' %', label: 'Episodes with the audio', sub: num(e.audio) + ' / ' + num(e.n)});
    tiles.push({cls: 'violet', icon: 'music', value: nxPct(m.audio, m.n) + ' %', label: 'Films with the audio', sub: num(m.audio) + ' / ' + num(m.n)});
  }
  if (!tiles.length) { body.appendChild(nxEmpty('chat', 'No language wanted', 'Set the languages of the subtitles and the audio in Settings → Notifications → Plex Media Server watched, then look through the files again.')); return; }
  body.appendChild(nxTiles(tiles));
  var c1 = nxCard('Shows', 'tv', [nxFilter('langs', 'Find a show…', nxDrawBody)]);
  var shows = (L.shows || []).filter(function (s) { return nxMatch('langs', s.show); });
  var list = el('div', 'nx-shows');
  var more = nxMore(shows, 30, function (s) {
    var x = el('div', 'nx-show');
    var h = el('div', 'nx-show-h');
    h.appendChild(noI18n(el('b', '', s.show)));
    h.appendChild(noI18n(el('span', 'muted small', num(s.n))));
    x.appendChild(h);
    [['subs', 'Subtitles', 'info', L.subs], ['audio', 'Audio', 'violet', L.audio]].forEach(function (k) {
      if (!(k[3] || []).length) return;
      var pr = el('div', 'nx-show-p');
      pr.appendChild(el('span', 'nx-show-k', k[1]));
      var tr = el('span', 'nx-bar-t');
      var f = el('span', 'nx-bar-f ' + (s[k[0]] === s.n ? 'ok' : k[2]));
      f.style.width = nxPct(s[k[0]], s.n) + '%';
      tr.appendChild(f);
      pr.appendChild(tr);
      pr.appendChild(noI18n(el('span', 'nx-bar-n', nxPct(s[k[0]], s.n) + ' %')));
      x.appendChild(pr);
      var miss = s['miss_' + k[0]] || [];
      if (miss.length && s[k[0]] < s.n) {
        var ch = el('div', 'ix-chips');
        var seasons = {};
        miss.forEach(function (m2) { (seasons[m2[0]] = seasons[m2[0]] || []).push(m2[1]); });
        Object.keys(seasons).forEach(function (sn) {
          var eps = seasons[sn];
          var c = noI18n(el('span', 'ix-chip', 'S' + pad2(+sn) + (eps.length > 4 ? ' · ' + eps.length + '×' : ' · E' + eps.map(function (z) { return pad2(+z); }).join(', E'))));
          ch.appendChild(c);
        });
        x.appendChild(ch);
      }
    });
    return x;
  }, list, 'langs');
  c1.appendChild(list);
  if (more) c1.appendChild(more);
  body.appendChild(c1);
  if ((L.movies_missing || []).length) {
    var c2 = nxCard('Films', 'film');
    var l2 = el('div', 'nx-list');
    var more2 = nxMore(L.movies_missing, 30, function (f2) {
      var row = el('div', 'nx-row');
      row.appendChild(nxIcon('film', 16));
      var b = el('div', 'nx-row-b');
      var tt = el('div', 'nx-row-t');
      tt.appendChild(noI18n(el('b', '', f2.title)));
      if (!f2.subs) tt.appendChild(el('span', 'tag info', 'no subtitles wanted'));
      if (!f2.audio) tt.appendChild(el('span', 'tag violet', 'no audio wanted'));
      b.appendChild(tt);
      var have = el('div', 'nx-row-s');
      if (f2.have_subs.length) have.appendChild(noI18n(el('span', '', '💬 ' + f2.have_subs.join(', '))));
      if (f2.have_audio.length) have.appendChild(noI18n(el('span', '', '🔊 ' + f2.have_audio.join(', '))));
      b.appendChild(have);
      row.appendChild(b);
      return row;
    }, l2, 'mmiss');
    c2.appendChild(l2);
    if (more2) c2.appendChild(more2);
    body.appendChild(c2);
  }
}
function nxLibHero(title, lede) {
  var lib = NX.d && NX.d.library;
  var top = el('div', 'card full nx-hero');
  var ht = el('div', 'nx-hero-t');
  ht.appendChild(el('h2', '', title));
  ht.appendChild(el('p', 'ix-lede', lede));
  if (lib) ht.appendChild(nxMeta(lib.at, lib.by, lib.took));
  top.appendChild(ht);
  if (ADMIN) {
    var go = cmdButton(lib ? 'Look through the files again' : 'Look through the files', 'search', 'primary');
    go.disabled = nxRunning();
    go.onclick = function () { nxDo({action: 'analyse'}); };
    top.appendChild(go);
  }
  return top;
}

/* ---- Duplicates ------------------------------------------------------------------- */
function nxDups(body) {
  var lib = NX.d && NX.d.library;
  body.appendChild(nxLibHero('The same thing more than once', 'A film or an episode in more than one version, and a film twice in one library on the same match. The version worth keeping is marked: the highest resolution, then the highest bit rate. Nothing is deleted here - only shown.'));
  if (!lib) { body.appendChild(nxEmpty('copy', 'Not looked through yet', '')); return; }
  var dups = lib.dups || [], twins = lib.twins || [];
  body.appendChild(nxTiles([
    {cls: dups.length ? 'warn' : 'ok', icon: 'copy', value: num(dups.length), label: 'In more than one version', zero: !dups.length},
    {cls: 'violet', icon: 'db', value: bytes(lib.dup_spare || 0), label: 'Taken by the other versions', zero: !lib.dup_spare},
    {cls: twins.length ? 'warn' : 'ok', icon: 'film', value: num(twins.length), label: 'Films twice on one match', zero: !twins.length}
  ]));
  if (!dups.length && !twins.length) {
    var ok = el('div', 'card full ix-allgood');
    ok.appendChild(nxIcon('check', 26));
    var t2 = el('div');
    t2.appendChild(el('b', '', 'Nothing twice'));
    t2.appendChild(el('span', '', 'Every film and episode is there once.'));
    ok.appendChild(t2);
    body.appendChild(ok);
    return;
  }
  if (dups.length) {
    var c1 = nxCard('In more than one version', 'copy', [nxFilter('dups', 'Find…', nxDrawBody)]);
    var list = el('div', 'nx-dups');
    var rows = dups.filter(function (d) { return nxMatch('dups', d.show + ' ' + d.title); });
    var more = nxMore(rows, 20, function (d) {
      var x = el('div', 'nx-dup');
      var h = el('div', 'nx-show-h');
      h.appendChild(nxIcon(d.kind === 'episode' ? 'tv' : 'film', 16));
      h.appendChild(noI18n(el('b', '', d.show ? d.show + ' · ' + nxSE(d.s, d.e) + ' · ' + d.title : d.title)));
      if (d.spare) {
        var sp = el('span', 'tag violet nx-right');
        sp.appendChild(noI18n(el('span', '', bytes(d.spare) + ' ')));
        sp.appendChild(el('span', '', 'to gain'));
        h.appendChild(sp);
      }
      x.appendChild(h);
      var vs = el('div', 'nx-vers');
      d.versions.forEach(function (v) {
        var c = el('div', 'nx-ver' + (v.keep ? ' keep' : ''));
        var top = el('div', 'nx-ver-t');
        top.appendChild(noI18n(el('b', '', (v.height >= 2000 ? '4K' : v.height >= 1000 ? '1080p' : v.height >= 700 ? '720p' : v.height ? v.height + 'p' : '?'))));
        top.appendChild(el('span', 'tag ' + (v.keep ? 'ok' : 'dim'), v.keep ? 'keep' : 'spare'));
        c.appendChild(top);
        c.appendChild(noI18n(el('span', 'nx-ver-s', [v.res, (v.codec || '').toUpperCase(), (v.audio || '').toUpperCase(), v.bitrate ? (v.bitrate / 1000).toFixed(1) + ' Mb/s' : '', v.bytes ? bytes(v.bytes) : ''].filter(Boolean).join(' · '))));
        c.appendChild(noI18n(el('span', 'mono ix-file muted', v.file)));
        vs.appendChild(c);
      });
      x.appendChild(vs);
      return x;
    }, list, 'dups');
    c1.appendChild(list);
    if (more) c1.appendChild(more);
    body.appendChild(c1);
  }
  if (twins.length) {
    var c2 = nxCard('Films twice on one match', 'film');
    c2.appendChild(el('p', 'muted small', 'Two items of one library with the same match - two copies Plex did not merge. Merge them in Plex (Merge), or delete the one not wanted.'));
    var l2 = el('div', 'nx-list');
    twins.forEach(function (t) {
      var row = el('div', 'nx-row');
      row.appendChild(nxIcon('copy', 16));
      var b = el('div', 'nx-row-b');
      b.appendChild(noI18n(el('b', '', t.items[0].title)));
      t.items.forEach(function (i) { b.appendChild(noI18n(el('span', 'mono ix-file muted', i.file + (i.bytes ? '  ·  ' + bytes(i.bytes) : '')))); });
      row.appendChild(b);
      l2.appendChild(row);
    });
    c2.appendChild(l2);
    body.appendChild(c2);
  }
}

/* ---- New files -------------------------------------------------------------------- */
var NX_NF = {ok: ['reads right', 'ok', 'check'], warn: ['reads, with a warning', 'warn', 'info'], damaged: ['damaged', 'crit', 'cancel'],
  'short': ['shorter than it should be', 'crit', 'cancel'], waiting: ['its disk sleeps', 'dim', 'clock'], busy: ['still being written', 'info', 'clock'],
  gone: ['not there', 'dim', 'trash']};
function nxNewfiles(body) {
  var v = NX.nf;
  var top = el('div', 'card full nx-hero');
  var ht = el('div', 'nx-hero-t');
  ht.appendChild(el('h2', '', 'New files'));
  ht.appendChild(el('p', 'ix-lede', 'Every hour the files Plex has taken in lately are read once by ffprobe: does the file open, is there a picture, is it as long as Plex says (a download not finished)? Only on a disk that is awake - a file on a sleeping one waits for it - and never a file written less than ten minutes ago.'));
  if (v && v.state && v.state.at) ht.appendChild(nxMeta(v.state.at));
  top.appendChild(ht);
  if (ADMIN && v) {
    var go = cmdButton('Read the new files now', 'search', 'primary');
    go.disabled = nxRunning() || !v.ffprobe;
    go.onclick = function () { nxDo({action: 'newfiles'}, function () { setTimeout(function () { nxLoadPart('newfiles'); }, 4000); }); };
    top.appendChild(go);
  }
  body.appendChild(top);
  if (!v) { body.appendChild(el('div', 'nx-loading', 'Loading…')); return; }
  if (!v.ffprobe) {
    var bn = el('div', 'ix-banner crit');
    bn.appendChild(nxIcon('info'));
    var bt = el('div');
    bt.appendChild(el('b', '', 'ffprobe is not on this machine'));
    bt.appendChild(el('span', '', 'It comes with ffmpeg: dnf install ffmpeg (from RPM Fusion) - then the new files are read by themselves.'));
    bn.appendChild(bt);
    body.appendChild(bn);
  }
  if (!v.on) body.appendChild(el('p', 'muted small', 'Switched off in Settings → Notifications - read only when asked here.'));
  var c = v.counts || {};
  body.appendChild(nxTiles([
    {cls: 'ok', icon: 'check', value: num(c.ok || 0), label: 'Read right', zero: !c.ok},
    {cls: 'warn', icon: 'info', value: num(c.warn || 0), label: 'With a warning', zero: !c.warn},
    {cls: 'crit', icon: 'cancel', value: num((c.damaged || 0) + (c['short'] || 0)), label: 'Damaged or short', zero: !((c.damaged || 0) + (c['short'] || 0))},
    {cls: 'dim', icon: 'clock', value: num((c.waiting || 0) + (c.busy || 0)), label: 'Waiting', sub: txT('a sleeping disk, or still written'), zero: !((c.waiting || 0) + (c.busy || 0))}
  ]));
  var card = nxCard('What was read', 'film', [nxFilter('nf', 'Find…', nxDrawBody)]);
  var rows = (v.rows || []).filter(function (r) { return nxMatch('nf', r.title + ' ' + r.file + ' ' + r.said); });
  rows.sort(function (a, b) {
    var o = {damaged: 0, 'short': 0, warn: 1, busy: 2, waiting: 3, ok: 4, gone: 5};
    return (o[a.state] - o[b.state]) || (b.added - a.added);
  });
  if (!rows.length) card.appendChild(el('p', 'muted small', 'Nothing read yet.'));
  var list = el('div', 'nx-list');
  var more = nxMore(rows, 40, function (r) {
    var s = NX_NF[r.state] || [r.state, 'dim', 'info'];
    var row = el('div', 'nx-row nf-' + s[1]);
    var ic = nxIcon(s[2], 16);
    ic.classList.add('t-' + s[1]);
    row.appendChild(ic);
    var b = el('div', 'nx-row-b');
    var tt = el('div', 'nx-row-t');
    tt.appendChild(noI18n(el('b', '', r.title)));
    tt.appendChild(el('span', 'tag ' + s[1], s[0]));
    tt.appendChild(noI18n(el('span', 'muted small', r.section)));
    b.appendChild(tt);
    b.appendChild(noI18n(el('span', 'nx-row-s', r.said)));
    b.appendChild(noI18n(el('span', 'mono ix-file muted', r.file)));
    row.appendChild(b);
    var rt = el('div', 'nx-feed-at');
    rt.appendChild(noI18n(el('span', '', stamp(r.added))));
    if (r.bytes) rt.appendChild(noI18n(el('span', 'muted', bytes(r.bytes))));
    if (ADMIN && r.state !== 'ok' && r.state !== 'waiting') {
      var again = iconButton('refresh', txT('Read it again at the next look'));
      again.onclick = function () { api('insights', {action: 'newfiles-again', part: r.part}).then(function (x) { toast(x.message, 'good'); nxLoadPart('newfiles'); }).catch(oops); };
      rt.appendChild(again);
    }
    row.appendChild(rt);
    return row;
  }, list, 'nf');
  card.appendChild(list);
  if (more) card.appendChild(more);
  body.appendChild(card);
}

/* ---- Plex server ------------------------------------------------------------------ */
function nxServer(body) {
  var v = NX.h;
  if (!v) { body.appendChild(el('div', 'nx-loading', 'Loading…')); return; }
  var h = v.health || {}, info = v.info || {};
  var hero = el('div', 'card full ix-hero nx-srv');
  var tone = h.answers ? 'ok' : h.active ? 'crit' : 'none';
  var ring = el('div', 'ix-ring ' + tone);
  var cpu = h.cpu == null ? 0 : h.cpu;
  ring.innerHTML = '<svg viewBox="0 0 120 120"><circle class="bg" cx="60" cy="60" r="52"/><circle class="fg" cx="60" cy="60" r="52" style="stroke-dasharray:' + Math.round(327 * Math.max(h.cpu == null && h.answers ? 0 : 0.02, Math.min(1, h.answers ? cpu / 100 : 1))) + ' 327"/></svg>';
  var mid = el('div', 'ix-ring-mid');
  if (h.answers) { mid.appendChild(noI18n(el('b', '', (h.cpu == null ? '–' : Math.round(cpu)) + '%'))); mid.appendChild(el('span', '', 'processor')); }
  else mid.appendChild(nxIcon(h.active ? 'cancel' : 'off', 32));
  ring.appendChild(mid);
  hero.appendChild(ring);
  var ht = el('div', 'ix-hero-t');
  ht.appendChild(el('h2', '', h.answers ? 'Plex Media Server answers' : h.active ? 'Plex Media Server runs, but does not answer' : 'Plex Media Server is not running'));
  var m = el('div', 'nx-facts');
  [['Version', info.version || h.version || '–'], ['Running for', h.started ? span(Date.now() / 1000 - h.started) : '–'],
   ['Memory', h.rss ? bytes(h.rss) : '–'], ['Playing', String(v.live || 0) + (v.transcodes ? ' (' + v.transcodes + ' transcoded)' : '')]].forEach(function (f) {
    var x = el('div', 'nx-fact');
    x.appendChild(el('span', '', f[0]));
    x.appendChild(noI18n(el('b', '', f[1])));
    m.appendChild(x);
  });
  ht.appendChild(m);
  if (h.fails) ht.appendChild(el('div', 'nx-meta warnt', 'It has not answered for ' + h.fails + ' min'));
  hero.appendChild(ht);
  var tools = el('div', 'ix-hero-tools');
  if ((h.cpu_hist || []).length > 1) {
    var sp = nxSpark(h.cpu_hist, 'info');
    explain(sp, 'The processor used by Plex Media Server, once a minute, the last two hours - of all its cores together.');
    tools.appendChild(sp);
  }
  if (ADMIN) {
    var rs = cmdButton('Restart Plex Media Server', 'refresh', 'small');
    rs.onclick = function () {
      confirmThen('Restart Plex Media Server?', htmlText('It is stopped and started again - about half a minute. Whoever is watching is stopped.'), 'Restart it', function () {
        nxAsk({action: 'plex-restart'}, 'Restart it anyway', function () { nxLoadPart('health'); });
      }, true);
    };
    tools.appendChild(rs);
  }
  hero.appendChild(tools);
  body.appendChild(hero);
  if ((v.activities || []).length) {
    var ca = nxCard('Plex is busy with', 'clock');
    var la = el('div', 'nx-list');
    v.activities.forEach(function (a) {
      var row = el('div', 'nx-row');
      row.appendChild(nxIcon('refresh', 15));
      var b = el('div', 'nx-row-b');
      b.appendChild(noI18n(el('b', '', a.title || a.type)));
      if (a.subtitle) b.appendChild(noI18n(el('span', 'nx-row-s', a.subtitle)));
      if (a.progress != null && a.progress >= 0) {
        var pr = el('div', 'nx-prog');
        var f = el('span', '');
        f.style.width = a.progress + '%';
        pr.appendChild(f);
        b.appendChild(pr);
      }
      row.appendChild(b);
      la.appendChild(row);
    });
    ca.appendChild(la);
    body.appendChild(ca);
  }
  var grid = el('div', 'nx-grid2');
  /* Watchdog */
  var cw = nxCard('The watchdog', 'shield');
  var modeW = {notify: 'A message when it stops answering', restart: 'Restarted when it stops answering', off: 'Not watched'}[v.mode] || v.mode;
  var st = el('div', 'nx-state ' + (v.mode === 'off' ? 'dim' : 'ok'));
  st.appendChild(nxIcon(v.mode === 'off' ? 'off' : 'check', 16));
  st.appendChild(el('span', '', modeW));
  if (v.mode !== 'off') st.appendChild(noI18n(el('span', 'muted', '· ' + v.minutes + ' min')));
  cw.appendChild(st);
  cw.appendChild(el('p', 'muted small', 'Plex\'s own address is asked once a minute. Only when it has not answered for the whole time set, while its service runs, is it taken for hung - never while plexmon itself has stopped Plex, puts its database back, or in maintenance.'));
  if ((v.restarts || []).length) {
    cw.appendChild(el('div', 'nx-sub-h', 'Restarts'));
    var tl = el('div', 'ix-timeline');
    v.restarts.forEach(function (r) {
      var row = el('div', 'ix-tl warn');
      row.appendChild(noI18n(el('span', 'ix-tl-at', stamp(r.at))));
      row.appendChild(el('span', '', r.words));
      tl.appendChild(row);
    });
    cw.appendChild(tl);
  }
  if (ADMIN) {
    var lk = el('a', 'nx-ext', 'Settings of the watchdog');
    lk.href = '#settings/notify';
    lk.onclick = function (e) { e.preventDefault(); showTab('settings'); showPane('notify'); };
    cw.appendChild(lk);
  }
  grid.appendChild(cw);
  /* Update */
  var cu = nxCard('A new version', 'download');
  var pk = v.package || {};
  if (!v.dnf) cu.appendChild(el('p', 'muted small', 'Plex Media Server is not looked after by dnf on this machine.'));
  else {
    var vs = el('div', 'nx-versions');
    var a1 = el('div', 'nx-vbox');
    a1.appendChild(el('span', '', 'This server'));
    a1.appendChild(noI18n(el('b', '', pk.have || info.version || '–')));
    vs.appendChild(a1);
    var arrow = el('div', 'nx-varrow' + (pk['new'] ? ' on' : ''));
    arrow.innerHTML = iconMarkup('next', 20);
    vs.appendChild(arrow);
    var a2 = el('div', 'nx-vbox' + (pk['new'] ? ' new' : ''));
    a2.appendChild(el('span', '', pk['new'] ? 'New' : 'The newest'));
    a2.appendChild(noI18n(el('b', '', pk['new'] || (pk.at ? pk.have || '–' : '?'))));
    vs.appendChild(a2);
    cu.appendChild(vs);
    if (pk.at) cu.appendChild(nxMeta(pk.at, pk.by));
    if (pk.error) cu.appendChild(el('p', 'warnt small', pk.error));
    if (ADMIN) {
      var row = el('div', 'btnrow');
      var ck = cmdButton('Look now', 'search', 'small');
      ck.disabled = nxRunning();
      ck.onclick = function () { nxDo({action: 'plex-check'}); };
      row.appendChild(ck);
      if (pk['new']) {
        var up = cmdButton('Update Plex Media Server', 'download', 'small primary');
        up.disabled = nxRunning();
        up.onclick = function () {
          confirmThen('Update Plex Media Server to ' + pk['new'] + '?', htmlText('dnf upgrade plexmediaserver - Plex is restarted by it, about a minute; whoever is watching is stopped. Its database is left as it is (Plex updates it itself when it starts).'), 'Update it', function () {
            nxAsk({action: 'plex-update'}, 'Update it anyway');
          }, false);
        };
        row.appendChild(up);
      }
      cu.appendChild(row);
    }
  }
  grid.appendChild(cu);
  body.appendChild(grid);
  /* Plex's folder and its caches */
  var cl = v.cleanup;
  var cc = nxCard('Plex\'s folder', 'db', ADMIN ? [(function () {
    var b = cmdButton(cl ? 'Measure again' : 'Measure it', 'chart', 'small');
    b.disabled = nxRunning();
    b.onclick = function () { nxDo({action: 'sizes'}); };
    return b;
  }())] : null);
  if (!cl) cc.appendChild(el('p', 'muted small', 'Not measured yet - its size, folder by folder, and what of its caches can go. Takes a minute on a big library (only Plex\'s own folder is read, never a data disk).'));
  else {
    cc.appendChild(nxMeta(cl.at, cl.by));
    var g2 = el('div', 'nx-grid2 inner');
    var left = el('div', '');
    var tot = el('div', 'nx-total');
    tot.appendChild(noI18n(el('b', '', bytes(cl.total))));
    tot.appendChild(el('span', '', 'in all'));
    if (cl.disk) tot.appendChild(noI18n(el('span', 'muted', '· ' + bytes(cl.disk.free) + ' ' + txT('free on its disk'))));
    left.appendChild(tot);
    left.appendChild(nxBars(cl.dirs.slice(0, 10).map(function (d) { return {label: d.name, n: d.bytes, text: bytes(d.bytes), cls: 'violet', raw: true}; })));
    g2.appendChild(left);
    var right = el('div', 'nx-clean');
    right.appendChild(el('div', 'nx-sub-h', 'What can go'));
    var picks = {};
    var sumEl = noI18n(el('b', '', '0 B'));
    var redo = function () {
      var t = 0;
      cl.clean.forEach(function (x) { if (picks[x.rel]) t += x.can; });
      sumEl.textContent = bytes(t);
      cbtn.disabled = !t || nxRunning();
    };
    cl.clean.forEach(function (x) {
      var lab = el('label', 'nx-clean-r' + (x.can ? '' : ' off'));
      var cb = el('input');
      cb.type = 'checkbox';
      cb.disabled = !x.can || !ADMIN;
      cb.checked = !!x.can && x.rel !== 'Cache/Transcode';
      picks[x.rel] = cb.checked;
      cb.onchange = function () { picks[x.rel] = cb.checked; redo(); };
      lab.appendChild(cb);
      var t = el('div', 'nx-clean-t');
      var tt = el('div', 'nx-row-t');
      tt.appendChild(el('b', '', x.title));
      tt.appendChild(noI18n(el('span', 'mono muted small', x.rel)));
      t.appendChild(tt);
      var s2 = el('div', 'nx-row-s');
      s2.appendChild(noI18n(el('span', '', bytes(x.can) + ' / ' + bytes(x.bytes))));
      s2.appendChild(el('span', '', x.days ? 'older than ' + x.days + ' days' : 'every file'));
      s2.appendChild(el('span', 'muted', x.why));
      t.appendChild(s2);
      lab.appendChild(t);
      right.appendChild(lab);
    });
    var foot = el('div', 'nx-clean-f');
    foot.appendChild(el('span', '', 'Chosen:'));
    foot.appendChild(sumEl);
    var cbtn = cmdButton('Clean what is chosen', 'trash', 'small');
    cbtn.onclick = function () {
      var which = Object.keys(picks).filter(function (k) { return picks[k]; });
      confirmThen('Clean Plex\'s caches?', htmlText('Only these folders of Plex\'s own, and in them only files older than said: ' + which.join(', ') + '. Never the library, never its database, never a picture or a poster of the library itself - Plex makes again what it needs.'), 'Clean them', function () {
        nxDo({action: 'clean', which: which});
      }, false);
    };
    if (ADMIN) foot.appendChild(cbtn);
    right.appendChild(foot);
    g2.appendChild(right);
    cc.appendChild(g2);
    redo();
  }
  body.appendChild(cc);
}
/* A button that stops whoever watches: asked first when somebody does. */
function nxAsk(body, anyway, after) {
  api('insights', body).then(function (x) {
    if (x.confirm === 'playing') {
      confirmThen('Somebody is watching', htmlText((x.playing === 1 ? '1 person is' : x.playing + ' people are') + ' watching now - they are stopped.'), anyway, function () {
        var b2 = {};
        Object.keys(body).forEach(function (k) { b2[k] = body[k]; });
        b2.force = 1;
        nxDo(b2, after);
      }, true);
      return;
    }
    if (x.job) { if (NX.d) NX.d.job = x.job; nxDrawJob(); nxWatch(); }
    if (x.message) toast(x.message, 'good');
    if (after) after(x);
  }).catch(oops);
}

/* ---- Settings → Notifications ----------------------------------------------------- */
function loadNotify() {
  api('insights').then(function (d) { NX.d = d; drawNotify(); if (d.job && d.job.running) nxWatch(); }).catch(oops);
}
function drawNotify() {
  var top = $('nf-top'), logh = $('nf-log');
  if (!top || !NX.d) return;
  clear(top);
  var d = NX.d;
  var c = el('div', 'card full nx-nf');
  var chans = [['email', 'E-mail', 'mail'], ['telegram', 'Telegram', 'chat'], ['ntfy', 'ntfy', 'bell']];
  var g = el('div', 'nx-nf-g');
  chans.forEach(function (ch) {
    var on = (d.channels || []).indexOf(ch[0]) >= 0;
    var x = el('div', 'nx-nf-c' + (on ? ' on' : ''));
    var ic = el('div', 'nx-nf-ic');
    ic.innerHTML = iconMarkup(ch[2], 22);
    x.appendChild(ic);
    var t = el('div', 'nx-nf-t');
    t.appendChild(noI18n(el('b', '', ch[1])));
    t.appendChild(el('span', '', on ? 'switched on' : 'off, or not filled in'));
    x.appendChild(t);
    if (ADMIN) {
      var b = cmdButton('Send a test', 'send', 'small');
      b.disabled = !on;
      if (!on) explain(b, 'Switch it on below, fill it in and save first.');
      b.onclick = function () {
        busy(b, true);
        api('insights', {action: 'notify-test', channels: [ch[0]]}).then(function (r) {
          busy(b, false);
          var ok = r.res && r.res[ch[0]] === 'sent';
          toast(ch[1] + ': ' + (ok ? txT('sent') : r.res[ch[0]]), ok ? 'good' : 'bad');
          if (r.notified) { NX.d.notified = r.notified; drawNotify(); }
        }).catch(function (e) { busy(b, false); oops(e); });
      };
      x.appendChild(b);
    }
    g.appendChild(x);
  });
  c.appendChild(g);
  var ia = d.ix_auto || {};
  var s = el('div', 'nx-nf-sched');
  s.appendChild(nxIcon('calendar', 16));
  if (ia.on) {
    s.appendChild(el('span', '', 'Library integrity is looked through by itself:'));
    s.appendChild(el('b', '', ia.every === 'weekly' ? 'every week' : 'every night'));
    s.appendChild(noI18n(el('span', '', ia.at)));
  } else s.appendChild(el('span', 'muted', 'Library integrity is looked through only when asked.'));
  c.appendChild(s);
  top.appendChild(c);
  dressButtons(top);
  if (logh) {
    clear(logh);
    var n = d.notified || [];
    if (!n.length) { logh.appendChild(el('p', 'muted small', 'Nothing sent yet.')); return; }
    var tl = el('div', 'ix-timeline');
    n.forEach(function (x) {
      var bad = Object.keys(x.res || {}).some(function (k) { return x.res[k] !== 'sent'; });
      var row = el('div', 'ix-tl' + (bad ? ' bad' : ''));
      row.appendChild(noI18n(el('span', 'ix-tl-at', stamp(x.at))));
      var b = el('div', 'nx-row-b');
      b.appendChild(noI18n(el('b', '', x.title)));
      b.appendChild(noI18n(el('span', 'nx-row-s', x.text)));
      var ch = el('div', 'ix-chips');
      Object.keys(x.res || {}).forEach(function (k) { ch.appendChild(noI18n(el('span', 'ix-chip ' + (x.res[k] === 'sent' ? 'ok' : 'crit'), k + ': ' + x.res[k]))); });
      b.appendChild(ch);
      row.appendChild(b);
      tl.appendChild(row);
    });
    logh.appendChild(tl);
  }
}
