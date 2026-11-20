/* ---- Settings → Library integrity (plexmon 2.11.0) ---------------------------------
   Where Plex's library no longer agrees with itself or with the disks - P1 to P8 of
   docs/library-integrity.md - found on a copy of its database, and put right where it can
   be put right safely: a checked copy of the database before anything is changed, a
   sleeping disk never woken. */
var IX = {d: null, poll: null, off: {}, open: {}, disks: true};
var IX_CLASS = {safe: ['Put right safely', 'ok'], confirm: ['Needs your yes', 'warn'], info: ['For your information', 'info']};
var IX_P = {
  P1: {cls: 'safe', title: 'Files Plex shows nowhere', icon: 'eye',
    lede: 'The file is on the disk and in the database, but its media item points at an episode that is not there any more - Plex shows it nowhere, and a scan does not help: it knows the file.',
    why: 'Most often a merge or a split while Plex was analysing (loudness, intros, credits): the analysis wrote the old episode back after the merge had destroyed it (P4). A crash of Plex while it wrote, or a hand in the database, can do it too.',
    how: 'Plex is stopped, a checked copy of its database made, its files copied aside; the orphan media items are taken out in one transaction through Plex\'s own SQLite as Plex\'s own user, the finding itself as its guard; integrity_check; Plex started; their folders scanned - Plex takes the files in as new.'},
  P2: {cls: 'confirm', title: 'Files under another show', icon: 'fold',
    lede: 'A file from the folder of one show is attached to an episode of another show on the same match - one show misses the episode, the other has it twice.',
    why: 'Several shows on one match (P3): Plex gives a new file to the FIRST show with the match, not to the show whose folder it is in - after P1 is put right, a new episode added, or a Plex dance of one of them.',
    how: 'Through Plex\'s own API, nothing in its database: once Plex has nothing running, a checked copy of the database, a Split of the show the files went to, the new small show found by its folder and merged into the folder\'s owner; the show\'s own folders merged together again, the titles chosen by hand put back, looked at again. It stops at anything unexpected.'},
  P3: {cls: 'info', title: 'Several shows on one match', icon: 'people',
    lede: 'One show more than once in a library - a version, a dubbing - all on the same Plex match. Wanted, often; but Plex only half supports it.',
    why: 'New files go to the first show of the match (P2); a refresh, Fix Match or Plex dance of one of them can merge them by itself; a merge and a split by hand risk P4 and then P1.',
    how: 'Nothing to put right here. To be rid of P2 and P3 for good: each version in a library of its own (Plex matches only within one library), e.g. "TV Shows - iPrima" with that one folder.'},
  P4: {cls: 'info', title: 'A merge or a split while Plex was busy', icon: 'history',
    lede: 'Found in Plex\'s logs: an analysis wrote an episode back after a merge had destroyed it, or a merge or split was asked while analyses were scheduled.',
    why: 'A fault of Plex Media Server itself: a job in the background holds on to the old episode. This is how P1 is born.',
    how: 'Nothing can be put right in the log - P1 is what to put right. Before any merge or split: wait until Plex has nothing running (Settings → Plex library shows it), and look here again after.'},
  P5: {cls: 'info', title: 'On the disk, but not taken in by Plex', icon: 'download',
    lede: 'A video file in a library folder that Plex has no record of - with what most likely keeps it out.',
    why: 'Still being downloaded, a name Plex does not understand, outside a season folder, a file Plex\'s user may not read, a broken file - or Plex simply has not scanned that folder.',
    how: 'Only a scan of the folder is asked of Plex (only where nothing else is in the way, only on a disk that is awake). Names and permissions are for you - what to do is said beside each.'},
  P6: {cls: 'safe', title: 'In the database, but not on the disk', icon: 'trash',
    lede: 'Plex has a file that is not on the disk any more - moved, deleted - or a whole folder is not there: a disk not mounted.',
    why: 'A file deleted or moved outside Plex. A folder with more than 5 % missing is taken for a disk not mounted - and then nothing is done.',
    how: 'Its folders scanned (Plex puts them into its trash) and the library\'s trash emptied - only when every folder of the library is there and awake, and no more than 5 % of it is missing or in the trash.'},
  P7: {cls: 'safe', title: 'Empty or without a parent', icon: 'list',
    lede: 'Seasons and shows with nothing in them, episodes with no file, and seasons or episodes whose show or season is not there.',
    why: 'What is left after files were deleted or moved, or after a merge.',
    how: 'The empty ones go with the library\'s trash (same guards as P6). Those without a parent are only shown: deleting from Plex\'s database by hand touches many tables.'},
  P8: {cls: 'info', title: 'Gaps in the numbering of episodes', icon: 'chart',
    lede: 'A season with episodes missing in the middle - 1, 2, 4, 6. Maybe never downloaded; but where the file is there, it says which problem holds it.',
    why: 'A gap is where to look for P1, P2 and P5.',
    how: 'Nothing on its own. A missing number found on the disk is marked with the problem that holds it.'}
};
var IX_REASON = {unseen: ['not scanned yet', 'info'], incomplete: ['still being written', 'dim'], permissions: ['permissions', 'warn'],
  naming: ['its name', 'warn'], placement: ['where it is', 'warn'], damaged: ['damaged', 'crit'], unreadable: ['unreadable', 'crit']};
var IX_ROOT = {awake: ['awake', 'ok', 'check'], asleep: ['asleep - left alone', 'dim', 'clock'], unknown: ['not known if it sleeps', 'warn', 'info'],
  missing: ['not there', 'crit', 'cancel']};

function loadIntegrity() {
  api('integrity').then(function (d) { IX.d = d; drawIntegrity(); if (d.job && d.job.running) ixWatch(); }).catch(oops);
}
function ixWatch() {
  if (IX.poll) return;
  var step = function () {
    api('integrity', {action: 'job'}).then(function (x) {
      var j = x.job || {};
      if (IX.d) IX.d.job = j;
      ixDrawJob();
      if (j.running) { IX.poll = setTimeout(step, 1000); return; }
      IX.poll = null;
      if (j.error) toast(j.error, 'bad');
      else if (j.what) toast(txT(j.what) + ': ' + txT('done'), 'good');
      if (TAB === 'settings' && PANE === 'integrity') loadIntegrity();
    }).catch(function () { IX.poll = setTimeout(step, 3000); });
  };
  step();
}
function ixDo(body, after) {
  return api('integrity', body).then(function (x) {
    if (x.job) { IX.d.job = x.job; toast(x.message || 'started', 'good'); drawIntegrity(); ixWatch(); }
    else if (x.message) toast(x.message, 'good');
    if (after) after(x);
    return x;
  }).catch(oops);
}
function ixRunning() { return !!(IX.d && IX.d.job && IX.d.job.running); }
function ixBusyDb() { return !!(IX.d && IX.d.db_job && IX.d.db_job.running && !ixRunning()); }

function drawIntegrity() {
  var host = $('ix-host');
  if (!host || !IX.d) return;
  clear(host);
  var d = IX.d, r = d.report;
  host.appendChild(ixHero(d, r));
  var jb = el('div', 'ix-jobhost');
  jb.id = 'ix-job';
  host.appendChild(jb);
  ixDrawJob();
  if (!r) { dressButtons(host); return; }
  host.appendChild(ixTiles(r));
  if (r.index_ok === false) {
    var b = el('div', 'ix-banner crit');
    b.appendChild(ixIcon('cancel'));
    var t = el('div');
    t.appendChild(el('b', '', 'An index of Plex\'s database does not agree with its table'));
    t.appendChild(el('span', '', 'Rows Plex cannot find by their number would look like P1 - nothing is put right here. On the server, with Plex stopped: Plex SQLite, REINDEX; or put back a copy (Settings → The database).'));
    b.appendChild(t);
    host.appendChild(b);
  }
  host.appendChild(ixRoots(d, r));
  var order = ['P1', 'P2', 'P6', 'P7', 'P5', 'P4', 'P3', 'P8'];
  var any = false;
  order.forEach(function (k) {
    var c = ixCard(k, r, d);
    if (c) { host.appendChild(c); any = true; }
  });
  if (!any) {
    var ok = el('div', 'card full ix-allgood');
    ok.appendChild(ixIcon('check', 26));
    var t2 = el('div');
    t2.appendChild(el('b', '', 'Everything agrees'));
    t2.appendChild(el('span', '', 'The database agrees with itself' + (r.disks_looked ? ' and with the disks that are awake.' : '. The disks were not looked at this time.')));
    ok.appendChild(t2);
    host.appendChild(ok);
  }
  host.appendChild(ixHistory(d));
  dressButtons(host);
}
function ixIcon(key, size) {
  var s = el('span', 'ix-ic');
  s.innerHTML = iconMarkup(key, size || 18);
  return s;
}

/* The top: a ring of how it stands, when it was looked at, the buttons. */
function ixHero(d, r) {
  var c = el('div', 'card full ix-hero');
  var s = r ? r.summary : null;
  var bad = s ? (s.safe + s.confirm) : 0;
  var tone = !r ? 'none' : r.index_ok === false ? 'crit' : bad ? (s.confirm ? 'warn' : 'safe') : s.info ? 'info' : 'ok';
  var ring = el('div', 'ix-ring ' + tone);
  var total = s ? s.safe + s.confirm + s.info : 0;
  var frac = !r ? 0 : total ? Math.max(0.08, Math.min(1, (s.safe + s.confirm) / Math.max(total, 1))) : 1;
  ring.innerHTML = '<svg viewBox="0 0 120 120"><circle class="bg" cx="60" cy="60" r="52"/><circle class="fg" cx="60" cy="60" r="52" style="stroke-dasharray:' + Math.round(327 * frac) + ' 327"/></svg>';
  var mid = el('div', 'ix-ring-mid');
  if (!r) mid.appendChild(ixIcon('search', 30));
  else if (r.index_ok === false) mid.appendChild(ixIcon('cancel', 30));
  else if (!total) mid.appendChild(ixIcon('check', 34));
  else {
    mid.appendChild(noI18n(el('b', '', num(bad || total))));
    mid.appendChild(el('span', '', bad ? 'to put right' : 'to know'));
  }
  ring.appendChild(mid);
  c.appendChild(ring);
  var txt = el('div', 'ix-hero-t');
  var h = el('h2', '', !r ? 'Not looked through yet' : r.index_ok === false ? 'Plex\'s database needs a hand'
    : !total ? 'The library agrees with itself' : bad ? 'The library needs putting right' : 'Nothing to put right - a few things to know');
  txt.appendChild(h);
  if (r) {
    var m = el('div', 'ix-meta');
    m.appendChild(el('span', '', 'Looked through'));
    m.appendChild(el('span', '', ' ' + ago(r.at)));
    m.appendChild(noI18n(el('span', '', ' · ' + (r.by || '') + ' · ' + (r.took || 0) + ' s · ' + bytes(r.db_bytes || 0))));
    txt.appendChild(m);
    var lo = el('div', 'ix-meta');
    lo.appendChild(el('span', '', r.disks_looked ? 'The disks were looked at too (only those awake).' : 'From the database alone - the disks were not looked at.'));
    txt.appendChild(lo);
  } else {
    txt.appendChild(el('p', 'ix-lede', 'A copy of Plex\'s database is read - Plex goes on, nothing of its own file is written. The disks only when asked, and only those awake: a sleeping disk is never woken.'));
  }
  var p = d.plex || {};
  var pl = el('div', 'ix-plex');
  pl.appendChild(el('span', 'tag ' + (p.running ? 'ok' : 'crit'), p.running ? 'Plex runs' : 'Plex is stopped'));
  if (p.watching) pl.appendChild(el('span', 'tag warn', (p.watching === 1 ? '1 person watches' : p.watching + ' people watch')));
  if ((p.busy || []).length) {
    var a = p.busy[0];
    var bt = el('span', 'tag info');
    bt.appendChild(el('span', '', 'Plex is busy:'));
    bt.appendChild(noI18n(el('span', '', ' ' + (a.title || a.type) + (a.progress != null ? ' ' + Math.round(a.progress) + ' %' : ''))));
    pl.appendChild(bt);
  }
  if (!p.plex_sqlite) {
    var ns = el('span', 'tag dim', 'no Plex SQLite');
    explain(ns, 'Plex\'s own SQLite (/usr/lib/plexmediaserver/Plex SQLite) is not on this machine: everything is found, but P1 - the only change in Plex\'s database - is not put right without it.');
    pl.appendChild(ns);
  }
  txt.appendChild(pl);
  c.appendChild(txt);
  var tools = el('div', 'ix-hero-tools');
  var go = cmdButton(r ? 'Look through again' : 'Look through the library', 'search', 'primary');
  go.disabled = ixRunning() || ixBusyDb();
  if (ixBusyDb()) explain(go, 'Settings → The database is busy (' + (d.db_job.what || '') + ') - once it is done.');
  go.onclick = function () { ixDo({action: 'scan', disks: IX.disks ? 1 : 0}); };
  tools.appendChild(go);
  var lab = el('label', 'ix-check');
  var cb = el('input');
  cb.type = 'checkbox';
  cb.checked = IX.disks;
  cb.onchange = function () { IX.disks = cb.checked; };
  lab.appendChild(cb);
  lab.appendChild(el('span', '', 'The disks too (only those awake)'));
  explain(lab, 'Is every file of the database on the disk (P6), and every video file on the disk in the database (P5)? Only the folders of disks that are awake now are read - a sleeping disk is left out and named, never woken.');
  tools.appendChild(lab);
  if (r) {
    var dl = cmdButton('Download the report', 'download', 'small');
    dl.onclick = function () { api('integrity', {action: 'report-text'}).then(function (x) { saveText(x.text, x.name); }).catch(oops); };
    tools.appendChild(dl);
  }
  c.appendChild(tools);
  return c;
}

/* The job: its steps as a line of stations, a bar, what it says, what it did. */
function ixDrawJob() {
  var host = $('ix-job');
  if (!host || !IX.d) return;
  var j = IX.d.job || {};
  clear(host);
  if (!j.what || (!j.running && !j.error && (Date.now() / 1000 - (j.ended || 0)) > 600)) return;
  var c = el('div', 'card full ix-job' + (j.running ? ' on' : j.error ? ' failed' : ' done'));
  var h = el('div', 'ix-job-h');
  var t = el('b', '', j.what);
  h.appendChild(t);
  h.appendChild(el('span', 'tag ' + (j.running ? 'info' : j.error ? 'crit' : 'ok'), j.running ? 'under way' : j.error ? 'failed' : 'done'));
  if (j.running) {
    var el2 = noI18n(el('span', 'muted small', ''));
    el2.textContent = Math.round(Date.now() / 1000 - j.began) + ' s';
    h.appendChild(el2);
    var stp = cmdButton('Stop', 'cancel', 'small');
    stp.classList.add('right');
    explain(stp, 'Stops at the next safe point - never in the middle of a change: a change already begun is finished, or put back.');
    stp.onclick = function () { busy(stp, true); api('integrity', {action: 'stop'}).then(function (x) { toast(x.message, 'good'); }).catch(oops); };
    h.appendChild(stp);
  }
  c.appendChild(h);
  var rail = el('ol', 'ix-rail');
  (j.steps || []).forEach(function (s, i) {
    var li = el('li', 'st-' + s.state);
    var dot = el('span', 'ix-dot');
    if (s.state === 'done') dot.innerHTML = iconMarkup('check', 12);
    else if (s.state === 'failed') dot.innerHTML = iconMarkup('close', 12);
    else if (s.state === 'skip') dot.innerHTML = iconMarkup('next', 12);
    else dot.appendChild(noI18n(el('span', '', String(i + 1))));
    li.appendChild(dot);
    li.appendChild(el('span', 'ix-st-t', s.title));
    rail.appendChild(li);
  });
  c.appendChild(rail);
  var pc = j.whole ? Math.round(100 * j.done / j.whole) : (j.running ? 4 : 100);
  if (!j.running && !j.error) pc = 100;
  var bar = el('div', 'ix-bar');
  var fill = el('span', '');
  fill.style.width = pc + '%';
  bar.appendChild(fill);
  c.appendChild(bar);
  var says = el('div', 'ix-says');
  says.appendChild(el('span', '', j.error ? j.error : (j.says || '')));
  says.appendChild(noI18n(el('span', 'right muted', pc + ' %')));
  c.appendChild(says);
  if ((j.lines || []).length) {
    var lg = el('div', 'ix-lines');
    j.lines.slice(-14).forEach(function (l) {
      var row = el('div', '');
      row.appendChild(noI18n(el('span', 'muted', stamp(l.at).slice(-8) + '  ')));
      row.appendChild(el('span', '', l.words));
      lg.appendChild(row);
    });
    c.appendChild(lg);
  }
  host.appendChild(c);
  dressButtons(c);
}

function ixTiles(r) {
  var s = r.summary || {};
  var row = el('div', 'ix-tiles');
  [['safe', s.safe, 'shield', 'Can be put right here, a copy of the database first'],
   ['confirm', s.confirm, 'fold', 'Changes how the library looks - each asked first'],
   ['info', s.info, 'info', 'Shown with what to do - nothing to press'],
   ['left', s.left_out, 'clock', 'Folders not looked at: a disk asleep, not known, or not there']].forEach(function (x) {
    var t = el('div', 'ix-tile ' + x[0] + (x[1] ? '' : ' zero'));
    t.appendChild(ixIcon(x[2], 20));
    var w = el('div', '');
    w.appendChild(noI18n(el('b', '', num(x[1] || 0))));
    w.appendChild(el('span', '', x[0] === 'left' ? 'Folders left out' : IX_CLASS[x[0]][0]));
    t.appendChild(w);
    explain(t, x[3]);
    row.appendChild(t);
  });
  return row;
}

/* Every folder of every library, and its disk - now. */
function ixRoots(d, r) {
  var c = el('div', 'card full ix-roots');
  var h = el('h2', '');
  h.appendChild(el('span', '', 'The folders of the libraries'));
  h.appendChild(el('span', 'muted small', 'and their disks, as they are now'));
  c.appendChild(h);
  var now = {};
  (d.roots_now || []).forEach(function (x) { now[x.path] = x; });
  var grid = el('div', 'ix-root-grid');
  (r.sections || []).forEach(function (s) {
    (s.roots || []).forEach(function (x) {
      var n = now[x.path] || x;
      var st = IX_ROOT[n.state] || ['?', 'dim', 'info'];
      var box = el('div', 'ix-root ' + st[1] + (x.mount_problem ? ' mount' : ''));
      var top = el('div', 'ix-root-top');
      top.appendChild(ixIcon(st[2], 15));
      top.appendChild(noI18n(el('span', 'mono', x.path)));
      box.appendChild(top);
      var sub = el('div', 'ix-root-sub');
      sub.appendChild(noI18n(el('span', 'lib', s.name)));
      sub.appendChild(el('span', 'tag ' + st[1], st[0]));
      if ((n.disks || []).length) sub.appendChild(noI18n(el('span', 'muted', (n.disks || []).join(', '))));
      box.appendChild(sub);
      var nums = el('div', 'ix-root-n');
      nums.appendChild(noI18n(el('b', '', num(x.files))));
      nums.appendChild(el('span', '', 'files in Plex'));
      if (x.looked || x.mount_problem) {
        nums.appendChild(noI18n(el('b', x.missing ? 'bad' : 'good', num(x.missing || 0))));
        nums.appendChild(el('span', '', 'not on the disk'));
      }
      box.appendChild(nums);
      if (x.mount_problem) box.appendChild(el('div', 'ix-root-why bad', 'Too much of it is missing - a disk not mounted? Nothing is done for it.'));
      else if (n.why) box.appendChild(el('div', 'ix-root-why', n.why));
      grid.appendChild(box);
    });
  });
  c.appendChild(grid);
  return c;
}

/* One card per problem. */
function ixCard(k, r, d) {
  var p = (r.problems || {})[k] || {};
  var meta = IX_P[k];
  var n = (r.summary.counts || {})[k] || 0;
  if (!n) return null;
  var c = el('div', 'card full ix-card cls-' + meta.cls);
  c.id = 'ix-' + k;
  var h = el('div', 'ix-card-h');
  h.appendChild(noI18n(el('span', 'ix-code', k)));
  h.appendChild(ixIcon(meta.icon, 18));
  h.appendChild(el('h3', '', meta.title));
  h.appendChild(noI18n(el('span', 'count', num(n))));
  h.appendChild(el('span', 'tag ' + IX_CLASS[meta.cls][1], IX_CLASS[meta.cls][0]));
  c.appendChild(h);
  c.appendChild(el('p', 'ix-lede', meta.lede));
  var det = el('details', 'ix-why');
  if (IX.open[k]) det.open = true;
  det.ontoggle = function () { IX.open[k] = det.open; };
  det.appendChild(el('summary', '', 'Why it happens, and what is done'));
  var w1 = el('p', '');
  w1.appendChild(el('b', '', 'Why: '));
  w1.appendChild(el('span', '', meta.why));
  det.appendChild(w1);
  var w2 = el('p', '');
  w2.appendChild(el('b', '', 'Here: '));
  w2.appendChild(el('span', '', meta.how));
  det.appendChild(w2);
  c.appendChild(det);
  var body = el('div', 'ix-body');
  ({P1: ixP1, P2: ixP2, P3: ixP3, P4: ixP4, P5: ixP5, P6: ixP6, P7: ixP7, P8: ixP8})[k](body, p, r, d);
  c.appendChild(body);
  return c;
}
function ixRow(lead, title, sub, extra) {
  var row = el('div', 'ix-row');
  if (lead) row.appendChild(lead);
  var b = el('div', 'ix-row-b');
  if (title) b.appendChild(title);
  if (sub) b.appendChild(sub);
  if (extra) b.appendChild(extra);
  row.appendChild(b);
  return row;
}
function ixFile(f, root) {
  var s = el('span', 'mono ix-file');
  var rest = f || '';
  if (root && rest.indexOf(root + '/') === 0) {
    s.appendChild(noI18n(el('span', 'muted', root + '/')));
    rest = rest.slice(root.length + 1);
  }
  var cut = rest.lastIndexOf('/');
  if (cut > 0) {
    s.appendChild(noI18n(el('span', 'muted', rest.slice(0, cut + 1))));
    rest = rest.slice(cut + 1);
  }
  s.appendChild(noI18n(el('span', '', rest)));
  return s;
}
function ixCheck(key, onchange) {
  var cb = el('input');
  cb.type = 'checkbox';
  cb.checked = !IX.off[key];
  cb.onchange = function () { if (cb.checked) delete IX.off[key]; else IX.off[key] = 1; if (onchange) onchange(); };
  return cb;
}
function ixEp(s, e) { return 'S' + String(s || 0).padStart(2, '0') + 'E' + String(e || 0).padStart(2, '0'); }
function ixMore(body, total, shown) {
  if (total > shown) body.appendChild(el('p', 'muted small ix-more', 'and ' + num(total - shown) + ' more - all of them in the report'));
}
function ixActions(body) {
  var row = el('div', 'btnrow ix-actions');
  body.appendChild(row);
  return row;
}
function ixDisabled(btn) {
  if (ixRunning()) { btn.disabled = true; explain(btn, 'Something is under way - once it is done.'); return true; }
  if (ixBusyDb()) { btn.disabled = true; explain(btn, 'Settings → The database is busy - once it is done.'); return true; }
  return false;
}
/* A confirm with the very steps root takes, numbered. */
function ixConfirm(title, intro, steps, warn, button, go, danger) {
  modal(title, function (body) {
    if (intro) body.appendChild(el('p', 'note', intro));
    var ol = el('ol', 'ix-plan');
    steps.forEach(function (s) {
      var li = el('li', '');
      if (typeof s === 'string') li.appendChild(el('span', '', s));
      else { li.appendChild(el('b', '', s[0])); if (s[1]) li.appendChild(el('span', 'muted', s[1])); }
      ol.appendChild(li);
    });
    body.appendChild(ol);
    (warn || []).forEach(function (w) { if (w) body.appendChild(el('p', 'note warn', w)); });
  }, [{words: 'No, leave it', close: true}, {words: button, cls: danger ? 'danger' : 'primary', icon: 'shield', close: true, go: go}], 'medium');
}

function ixP1(body, p, r, d) {
  var items = (p.items || []).concat(p.parts || []);
  var list = el('div', 'ix-list');
  items.forEach(function (x) {
    var key = x.missing_metadata != null ? 'p1:' + x.media_item : 'p1p:' + x.part;
    var lead = el('span', 'ix-lead');
    if (ADMIN && d.plex.plex_sqlite && r.index_ok !== false) lead.appendChild(ixCheck(key, drawIntegrity));
    else lead.appendChild(ixIcon('eye', 15));
    var t = el('div', 'ix-row-t');
    t.appendChild(noI18n(el('b', '', x.show || '?')));
    t.appendChild(ixFile(x.file, x.dir));
    var s = el('div', 'ix-row-s');
    if (x.missing_metadata != null) {
      s.appendChild(el('span', '', 'media item'));
      s.appendChild(noI18n(el('span', 'mono', ' ' + x.media_item + ' → ' + x.missing_metadata + ' ')));
      s.appendChild(el('span', '', '(an episode that is not there)'));
    } else {
      s.appendChild(el('span', '', 'media part'));
      s.appendChild(noI18n(el('span', 'mono', ' ' + x.part + ' → ' + x.media_item + ' ')));
      s.appendChild(el('span', '', '(a media item that is not there)'));
    }
    list.appendChild(ixRow(lead, t, s));
  });
  body.appendChild(list);
  ixMore(body, p.count, items.length);
  if (p.streams) body.appendChild(el('p', 'muted small', num(p.streams) + ' audio, video or subtitle streams belong to no media item - harmless leftovers, left as they are.'));
  if (!ADMIN) return;
  var pick = (p.items || []).filter(function (x) { return !IX.off['p1:' + x.media_item]; }).map(function (x) { return x.media_item; });
  var pickp = (p.parts || []).filter(function (x) { return !IX.off['p1p:' + x.part]; }).map(function (x) { return x.part; });
  var row = ixActions(body);
  var b = cmdButton('Put right (' + (pick.length + pickp.length) + ')', 'shield', 'primary');
  if (!ixDisabled(b)) {
    if (!d.plex.plex_sqlite) { b.disabled = true; explain(b, 'Plex SQLite is not on this machine - Plex\'s database is written only with it.'); }
    else if (r.index_ok === false) { b.disabled = true; explain(b, 'An index of the database does not agree - this is not P1.'); }
    else if (!pick.length && !pickp.length) b.disabled = true;
  }
  b.onclick = function () { ixFixP1(pick, pickp, d); };
  row.appendChild(b);
  row.appendChild(el('span', 'muted small', 'Plex is stopped for a minute or two. A checked copy of its database comes first.'));
}
function ixFixP1(pick, pickp, d) {
  var n = pick.length + pickp.length;
  var watching = (d.plex || {}).watching || 0;
  var steps = [
    ['A fresh copy of the database looked at', 'only what is an orphan still is touched'],
    ['A checked copy of Plex\'s database', 'packed, kept in Settings → The database - it can be put back from there'],
    ['Plex Media Server stopped', watching ? (watching === 1 ? '1 person watches now - it stops for them' : watching + ' people watch now - it stops for them') : 'nobody watches now'],
    ['Its database files copied aside', 'as they are, to put back at once if anything is not right'],
    ['The ' + n + ' orphan(s) taken out', 'one transaction, Plex SQLite as Plex\'s own user, the finding itself as its guard - media streams, parts, items; nothing else'],
    ['integrity_check', 'not "ok": Plex\'s files are put back as they were'],
    ['Plex Media Server started', 'whatever happened before'],
    ['Their folders scanned', 'Plex takes the files in as new - only folders on disks awake; asleep ones at Plex\'s next scan'],
    ['Looked at again', 'once Plex has nothing running']];
  var go = function (force) {
    ixDo({action: 'fix-p1', ids: pick, parts: pickp, force: force ? 1 : 0}).then(function (x) {
      if (x === undefined) return;
    });
  };
  ixConfirm('Put P1 right?', 'Root does exactly this - nothing else:', steps,
    [watching ? 'Someone is watching: their film stops when Plex stops.' : '',
     'If the show is one of several on one match (P3), Plex may put the file under another show when it scans it - P2. Look here again after.'],
    watching ? 'Stop Plex and put it right' : 'Put it right', function () {
      api('integrity', {action: 'fix-p1', ids: pick, parts: pickp, force: watching ? 1 : 0}).then(function (x) {
        if (x.job) { IX.d.job = x.job; toast(x.message, 'good'); drawIntegrity(); ixWatch(); }
      }).catch(function (e) {
        if (/watching now/.test(String(e && e.message))) {
          confirmThen('Someone is watching', htmlText(String(e.message)), 'Stop it anyway', function () { go(true); });
        } else oops(e);
      });
    }, !!watching);
}

function ixP2(body, p, r, d) {
  (p.cases || []).forEach(function (cs) {
    var box = el('div', 'ix-case');
    var flow = el('div', 'ix-flow');
    var a = el('div', 'ix-show from');
    a.appendChild(el('span', 'ix-flow-l', 'attached to'));
    a.appendChild(noI18n(el('b', '', cs.attached.title)));
    a.appendChild(noI18n(el('span', 'mono muted', '#' + cs.attached.id)));
    var arrow = el('div', 'ix-arrow');
    arrow.appendChild(noI18n(el('span', '', cs.files.length + ' ×')));
    arrow.innerHTML += '<svg viewBox="0 0 60 16"><path d="M2 8h52M46 2l8 6-8 6"/></svg>';
    var o = el('div', 'ix-show to');
    o.appendChild(el('span', 'ix-flow-l', 'belongs to'));
    o.appendChild(noI18n(el('b', '', cs.owner.title)));
    o.appendChild(noI18n(el('span', 'mono muted', '#' + cs.owner.id)));
    flow.appendChild(a); flow.appendChild(arrow); flow.appendChild(o);
    box.appendChild(flow);
    var dir = el('div', 'ix-case-dir');
    dir.appendChild(el('span', 'muted', 'the folder:'));
    dir.appendChild(noI18n(el('span', 'mono', ' ' + cs.dir)));
    box.appendChild(dir);
    var chips = el('div', 'ix-chips');
    cs.files.forEach(function (f) {
      var ch = noI18n(el('span', 'ix-chip', ixEp(f.s, f.e)));
      ch.title = f.file;
      chips.appendChild(ch);
    });
    box.appendChild(chips);
    if (!cs.fixable) box.appendChild(el('p', 'note warn small', 'Not for a button: ' + (cs.why ? txT(cs.why) : 'the two shows are not on one match')));
    if (ADMIN && cs.fixable) {
      var row = ixActions(box);
      var b = cmdButton('Put right through Plex', 'fold', 'primary');
      ixDisabled(b);
      b.onclick = function () { ixFixP2(cs); };
      row.appendChild(b);
      var also = (cs.attached.dirs || []).filter(function (x) { return x !== cs.dir; });
      if (also.length > 1) row.appendChild(el('span', 'muted small', '"' + cs.attached.title + '" has folders on several disks - they are merged together again after the split.'));
    }
    body.appendChild(box);
  });
  body.appendChild(el('p', 'muted small', 'For good: each version in a library of its own (P3) - then Plex cannot put a file under the other show again.'));
}
function ixFixP2(cs) {
  ixConfirm('Put P2 right through Plex?', 'Only Plex\'s own API - its database is not written by plexmon:', [
    ['Wait until Plex has nothing running', 'analyses, scans - otherwise a merge risks P4 and P1; not quiet within half an hour: nothing is done'],
    ['A checked copy of Plex\'s database', 'kept in Settings → The database'],
    ['"' + cs.attached.title + '" split', 'Plex makes a show of each of its folders'],
    ['The new show of ' + cs.dir.split('/').pop() + ' found by its folder', 'never by its number - anything unexpected: STOP, nothing merged'],
    ['Merged into "' + cs.owner.title + '"', 'the owner keeps its title, poster and locked fields'],
    ['"' + cs.attached.title + '"\'s own folders merged together again', 'when it had more than one'],
    ['The titles chosen by hand put back', 'a split gives the pieces the agent\'s title'],
    ['Looked at again', 'the episodes counted against what was expected']],
    ['A split and a merge change how the library looks; if a step goes wrong, plexmon stops there and says what is left - nothing is undone by itself.'],
    'Put it right', function () { ixDo({action: 'fix-p2', key: cs.key}); }, false);
}

function ixP3(body, p) {
  (p.groups || []).forEach(function (g) {
    var box = el('div', 'ix-group');
    var top = el('div', 'ix-group-h');
    top.appendChild(ixIcon('people', 15));
    top.appendChild(noI18n(el('span', 'mono muted', g.guid)));
    box.appendChild(top);
    var shows = el('div', 'ix-group-shows');
    g.shows.forEach(function (s) {
      var sc = el('div', 'ix-show');
      sc.appendChild(noI18n(el('b', '', s.title || ('#' + s.id))));
      (s.dirs || []).forEach(function (x) { sc.appendChild(noI18n(el('span', 'mono muted small', x))); });
      shows.appendChild(sc);
    });
    box.appendChild(shows);
    body.appendChild(box);
  });
}
function ixP4(body, p) {
  var tl = el('div', 'ix-timeline');
  (p.races || []).forEach(function (x) {
    var row = el('div', 'ix-tl bad');
    row.appendChild(noI18n(el('span', 'ix-tl-at', stamp(x.at))));
    var t = el('div', '');
    var a = el('div', '');
    a.appendChild(el('b', '', 'Written back after it was destroyed'));
    t.appendChild(a);
    var s = el('div', 'muted small');
    s.appendChild(el('span', '', 'media item'));
    s.appendChild(noI18n(el('span', 'mono', ' ' + x.media_item + ' → ' + x.metadata + ' ')));
    s.appendChild(el('span', '', 'destroyed'));
    s.appendChild(noI18n(el('span', '', ' ' + stamp(x.destroyed_at))));
    t.appendChild(s);
    if (x.still_orphan) t.appendChild(el('span', 'tag crit', 'still an orphan - P1'));
    row.appendChild(t);
    tl.appendChild(row);
  });
  (p.risky || []).forEach(function (x) {
    var row = el('div', 'ix-tl warn');
    row.appendChild(noI18n(el('span', 'ix-tl-at', stamp(x.at))));
    var t = el('div', '');
    t.appendChild(el('b', '', x.what === 'merge' ? 'A merge while analyses were scheduled' : 'A split while analyses were scheduled'));
    var s = el('div', 'muted small');
    s.appendChild(noI18n(el('span', 'mono', '#' + x.item + ' · ' + (x.jobs || []).join(', ') + ' · ' + x.n_jobs + '×')));
    t.appendChild(s);
    row.appendChild(t);
    tl.appendChild(row);
  });
  body.appendChild(tl);
  body.appendChild(el('p', 'muted small', 'Read: ' + num(p.logs || 0) + ' logs of Plex (they hold the last few days; Plex writes these lines at its debug level).'));
}
function ixP5(body, p, r, d) {
  var reasons = el('div', 'ix-chips');
  Object.keys(p.reasons || {}).forEach(function (k) {
    var rs = IX_REASON[k] || [k, 'dim'];
    var ch = el('span', 'tag ' + rs[1]);
    ch.appendChild(el('span', '', rs[0]));
    ch.appendChild(noI18n(el('span', '', ' ' + p.reasons[k])));
    reasons.appendChild(ch);
  });
  body.appendChild(reasons);
  var list = el('div', 'ix-list');
  (p.missing || []).forEach(function (m) {
    var rs = IX_REASON[m.reason] || [m.reason, 'dim'];
    var lead = el('span', 'ix-lead');
    if (ADMIN && m.reason === 'unseen') lead.appendChild(ixCheck('p5:' + m.dir, drawIntegrity));
    else lead.appendChild(ixIcon(m.reason === 'damaged' ? 'cancel' : 'info', 15));
    var t = el('div', 'ix-row-t');
    t.appendChild(el('span', 'tag ' + rs[1], rs[0]));
    t.appendChild(ixFile(m.file, m.root));
    list.appendChild(ixRow(lead, t, el('div', 'ix-row-s', m.advice)));
  });
  body.appendChild(list);
  ixMore(body, p.count, (p.missing || []).length);
  var ign = p.ignored || {};
  if (Object.keys(ign).length) {
    var ig = el('p', 'muted small');
    ig.appendChild(el('span', '', 'Left out on purpose, as Plex does:'));
    ig.appendChild(noI18n(el('span', '', ' ' + Object.keys(ign).map(function (k) { return txT(k) + ' ' + ign[k]; }).join(' · '))));
    body.appendChild(ig);
  }
  if (!ADMIN) return;
  var dirs = [];
  (p.missing || []).forEach(function (m) { if (m.reason === 'unseen' && !IX.off['p5:' + m.dir] && dirs.indexOf(m.dir) < 0) dirs.push(m.dir); });
  if (!(p.missing || []).some(function (m) { return m.reason === 'unseen'; })) return;
  var row = ixActions(body);
  var b = cmdButton('Scan these folders (' + dirs.length + ')', 'refresh', 'primary');
  if (!ixDisabled(b) && !dirs.length) b.disabled = true;
  b.onclick = function () {
    ixConfirm('Ask Plex to scan these folders?', '', [['Wait until Plex has nothing running', ''], ['A scan of each folder asked of Plex', dirs.length + ' - only on disks awake now'], ['Looked at again', 'the disks too']], [], 'Scan them', function () { ixDo({action: 'fix-p5', dirs: dirs}); }, false);
  };
  row.appendChild(b);
}
function ixP6(body, p, r) {
  (p.roots || []).forEach(function (x) {
    var b = el('div', 'ix-banner crit');
    b.appendChild(ixIcon('cancel'));
    var t = el('div');
    var h = el('b', '');
    h.appendChild(noI18n(el('span', 'mono', x.path)));
    h.appendChild(el('span', '', ': a disk not mounted?'));
    t.appendChild(h);
    var s = el('span', '');
    s.appendChild(noI18n(el('span', '', num(x.missing) + ' / ' + num(x.files) + ' ')));
    s.appendChild(el('span', '', 'of its files are not there. Nothing is done for this folder - and the trash of its library is not emptied - until it is there again.'));
    t.appendChild(s);
    b.appendChild(t);
    body.appendChild(b);
  });
  var bysec = {};
  (p.missing || []).forEach(function (m) { (bysec[m.section] = bysec[m.section] || []).push(m); });
  var names = {};
  (r.sections || []).forEach(function (s) { names[s.id] = s; });
  Object.keys(bysec).forEach(function (sid) {
    var sec = names[sid] || {name: sid, roots: []};
    var box = el('div', 'ix-group');
    var top = el('div', 'ix-group-h');
    top.appendChild(ixIcon('archive', 15));
    top.appendChild(noI18n(el('b', '', sec.name)));
    box.appendChild(top);
    var list = el('div', 'ix-list');
    bysec[sid].forEach(function (m) { list.appendChild(ixRow(ixIcon('trash', 14), ixFile(m.file, m.root))); });
    box.appendChild(list);
    if (ADMIN) {
      var row = ixActions(box);
      var b = cmdButton('Scan and empty the trash', 'broom', 'primary');
      var blocked = ixSecBlocked(sec);
      if (!ixDisabled(b) && blocked) { b.disabled = true; explain(b, blocked); }
      b.onclick = function () { ixTrashConfirm('P6', sec, true); };
      row.appendChild(b);
      if (blocked) row.appendChild(el('span', 'muted small', blocked));
    }
    body.appendChild(box);
  });
  if (!r.disks_looked) body.appendChild(el('p', 'muted small', 'Only the folders of a disk not mounted - the files were not looked for: look through again with "The disks too".'));
}
function ixSecBlocked(sec) {
  var why = [];
  var now = {};
  ((IX.d || {}).roots_now || []).forEach(function (x) { now[x.path] = x; });
  (sec.roots || []).forEach(function (x) {
    var n = now[x.path] || x;
    if (x.mount_problem) why.push(x.path + ': ' + txT('a disk not mounted?'));
    else if (n.state !== 'awake') why.push(x.path + ': ' + txT((IX_ROOT[n.state] || [n.state])[0]));
  });
  return why.length ? txT('Every folder of the library has to be there and awake') + ' - ' + why.join('; ') : '';
}
function ixTrashConfirm(kind, sec, scan) {
  ixConfirm('Empty the trash of "' + sec.name + '"?', '', [
    ['Wait until Plex has nothing running', ''],
    scan ? ['Its folders with files gone scanned', 'Plex puts what is not there into its trash'] : ['', ''],
    ['How much is in the trash looked at again', 'a fresh copy of the database - more than 5 % of a folder in the trash: STOP, nothing emptied'],
    ['The trash emptied', 'Plex forgets what is not there - and what was watched of it'],
    ['Looked at again', '']].filter(function (s) { return s[0]; }),
    ['What Plex forgets cannot be brought back without a copy of the database (Settings → The database).'],
    'Empty the trash', function () { ixDo({action: kind === 'P6' ? 'fix-p6' : 'fix-p7', section: sec.id}); }, true);
}
function ixP7(body, p, r) {
  var names = {};
  (r.sections || []).forEach(function (s) { names[s.id] = s; });
  var secs = {};
  [['empty', 'Empty - nothing in it'], ['no_media', 'An episode with no file'], ['orphans', 'Its show or season is not there']].forEach(function (x) {
    var rows = p[x[0]] || [];
    if (!rows.length) return;
    var box = el('div', 'ix-group');
    var top = el('div', 'ix-group-h');
    top.appendChild(el('b', '', x[1]));
    top.appendChild(noI18n(el('span', 'count', String((p.counts || {})[x[0]] || rows.length))));
    box.appendChild(top);
    var list = el('div', 'ix-list');
    rows.forEach(function (m) {
      if (x[0] !== 'orphans' && m.section) secs[m.section] = 1;
      var t = el('div', 'ix-row-t');
      var w = (m.show ? m.show + ' · ' : '') + (m.title || '') + ' #' + m.id;
      t.appendChild(noI18n(el('span', '', w)));
      list.appendChild(ixRow(ixIcon(x[0] === 'orphans' ? 'info' : 'list', 14), t));
    });
    box.appendChild(list);
    body.appendChild(box);
  });
  if (!ADMIN) return;
  Object.keys(secs).forEach(function (sid) {
    var sec = names[sid];
    if (!sec) return;
    var row = ixActions(body);
    var b = cmdButton('Empty the trash of "' + sec.name + '"', 'broom', 'primary');
    var blocked = ixSecBlocked(sec);
    if (!ixDisabled(b) && blocked) { b.disabled = true; explain(b, blocked); }
    b.onclick = function () { ixTrashConfirm('P7', sec, false); };
    row.appendChild(b);
    if (blocked) row.appendChild(el('span', 'muted small', blocked));
  });
}
function ixP8(body, p) {
  var list = el('div', 'ix-list');
  (p.seasons || []).forEach(function (g) {
    var t = el('div', 'ix-row-t');
    t.appendChild(noI18n(el('b', '', g.title)));
    t.appendChild(noI18n(el('span', 'muted', ' S' + String(g.s).padStart(2, '0') + ' · ' + g.have + ' / ' + g.max)));
    var chips = el('div', 'ix-chips');
    (g.missing || []).forEach(function (n) {
      var f = (g.found || {})[String(n)];
      var ch = noI18n(el('span', 'ix-chip' + (f ? ' found ' + f.why : ''), 'E' + String(n).padStart(2, '0') + (f ? ' · ' + f.why : '')));
      if (f) { ch.title = f.file; ch.onclick = function () { var c = $('ix-' + f.why); if (c) c.scrollIntoView({behavior: 'smooth', block: 'start'}); }; }
      chips.appendChild(ch);
    });
    if (g.n_missing > (g.missing || []).length) chips.appendChild(noI18n(el('span', 'muted small', '+' + (g.n_missing - g.missing.length))));
    list.appendChild(ixRow(ixIcon('chart', 14), t, chips));
  });
  body.appendChild(list);
  ixMore(body, p.count, (p.seasons || []).length);
  if (p.explained) body.appendChild(el('p', 'muted small', 'A number marked P1, P2 or P5 is on the disk - the card of that problem puts it right.'));
}

function ixHistory(d) {
  var c = el('div', 'card full ix-hist');
  var h = el('h2', '');
  h.appendChild(el('span', '', 'What was put right here'));
  c.appendChild(h);
  var hs = d.history || [];
  if (!hs.length) { c.appendChild(el('p', 'note', 'Nothing yet.')); return c; }
  c.appendChild(listTable(['When', 'What', 'Who', 'How it went', 'Kept'], hs, function (x) {
    var tr = el('tr');
    tr.appendChild(el('td', 'muted', ago(x.at)));
    var w = el('td', '');
    w.appendChild(noI18n(el('span', 'ix-code small', x.kind)));
    w.appendChild(el('span', '', ' ' + (IX_P[x.kind] ? txT(IX_P[x.kind].title) : '')));
    tr.appendChild(w);
    tr.appendChild(noI18n(el('td', '', x.by || '')));
    var st = el('td', '');
    st.appendChild(el('span', 'tag ' + (x.ok ? 'ok' : 'crit'), x.ok ? 'done' : 'not done'));
    if (x.error) st.appendChild(el('div', 'small muted', x.error));
    tr.appendChild(st);
    var k = el('td', 'small');
    if (x.copy) k.appendChild(noI18n(el('div', 'mono', x.copy)));
    if (x.aside) k.appendChild(noI18n(el('div', 'mono muted', x.aside)));
    tr.appendChild(k);
    return tr;
  }, 'plain'));
  return c;
}
