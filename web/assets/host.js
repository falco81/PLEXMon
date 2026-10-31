/* plexmon - Settings → Time, Restart and power off, System updates, The
   database, Backups (2.2.0). Time, Restart, System updates and Backups are
   otp's own pages (1.0.119-1.0.187), as they are, on one server; The database
   is plexmon's and Plex Media Server's. */
function txT(s) {
  var got = typeof i18nText === 'function' ? i18nText(String(s)) : null;
  return got == null ? String(s) : got;
}
/* A file as the body of a POST - a new version, a backup to put back. */
function bigFetch(query, file) {
  return fetch(query, {method: 'POST', body: file, credentials: 'same-origin',
                       headers: {'X-Pm-Restore': '1', 'Content-Type': 'application/octet-stream'}});
}

/* ---- Settings → Restart and power off (1.0.119) ------------------------------- */
var PW = {d: null, poll: null, failSince: 0, mine: false, reload: false};
function loadPower() {
  api('power').then(function (d) { PW.d = d; PW.failSince = 0; drawPower(); pwPollFor(); }).catch(oops);
}
function pwPollFor() {
  var live = PW.d && PW.d.plan && PW.d.plan.id && !pwAllDone(PW.d);
  if (live && !PW.poll) PW.poll = setInterval(pwPoll, 2000);
  if (!live && PW.poll) { clearInterval(PW.poll); PW.poll = null; }
}
function pwPoll() {
  if (TAB !== 'settings' || PANE !== 'power') { clearInterval(PW.poll); PW.poll = null; return; }
  api('power').then(function (d) {
    var wasAway = !!PW.failSince;
    PW.d = d; PW.failSince = 0;
    drawPower(true);
    if (pwAllDone(d)) {
      clearInterval(PW.poll); PW.poll = null;
      toast(d.plan.how === 'reboot' ? 'every node is up again' : 'switched off', 'good');
      if (wasAway || PW.reload) setTimeout(function () { location.reload(); }, 2500);
    }
  }).catch(function () { if (!PW.failSince) PW.failSince = Date.now(); drawPowerAway(); });
}
function pwStateOf(d, n) {
  var r = n.row || {};
  var plan = d.plan || {};
  if (!r.state) return {k: 'wait', w: plan.order === 'rolling' ? 'waits its turn' : 'asked'};
  if (r.state === 'failed') return {k: 'bad', w: 'failed: ' + (r.said || '')};
  if (r.state === 'back') return {k: 'ok', w: 'up again'};
  if (r.state === 'going') {
    if (plan.how === 'poweroff') return n.alive && (d.now - (r.down_at || 0)) < 20 ? {k: 'going', w: 'switching off'} : {k: 'ok', w: 'switched off'};
    if (n.alive && (n.started || 0) > (r.down_at || 0)) return {k: 'going', w: 'starting'};
    if (n.alive && d.now < (r.down_at || 0) + 15) return {k: 'going', w: 'going down'};
    return {k: 'going', w: 'restarting'};
  }
  return {k: 'wait', w: r.state};
}
function pwAllDone(d) {
  if (!d.plan || !d.plan.id) return true;
  return d.nodes.filter(function (n) { return (d.plan.targets || []).indexOf(n.name) >= 0; })
    .every(function (n) { var s = pwStateOf(d, n); return s.k === 'ok' || s.k === 'bad'; });
}
function drawPower(live) {
  var real = $('pw-host');
  if (!real || !PW.d) return;
  liveFill(real, function (host) {
  var d = PW.d;
  if (d.plan && d.plan.id) host.appendChild(pwProgress(d));
  var grid = el('div', 'pw-grid');
  d.nodes.forEach(function (n) {
    var c = el('div', 'card pw-node');
    var h = el('div', 'pw-head');
    h.appendChild(el('span', 'dot ' + (n.alive ? 'ok' : 'bad')));
    h.appendChild(noI18n(el('b', '', n.name)));
    if (n.me) h.appendChild(el('span', 'tag info', 'this page'));
    c.appendChild(h);
    c.appendChild(netFacts([
      ['State', n.alive ? 'answering' : 'not answering'],
      ['Address', n.address || '', true],
      ['Running for', n.started ? span(d.now - n.started) : ''],
      ['Version', n.version || '', true]
    ]));
    var row = el('div', 'btnrow');
    var rb = cmdButton('Restart', 'refresh', '');
    var off = cmdButton('Power off', 'power', 'danger');
    rb.disabled = off.disabled = !!d.busy || !n.alive;
    rb.onclick = function () { pwAsk('reboot', [n.name], 'one', n); };
    off.onclick = function () { pwAsk('poweroff', [n.name], 'one', n); };
    row.appendChild(rb); row.appendChild(off);
    c.appendChild(row);
    grid.appendChild(c);
  });
  host.appendChild(grid);
  if (d.cluster) {
    var both = el('div', 'card full');
    both.appendChild(el('h2', '', 'Both nodes'));
    var all = d.nodes.map(function (n) { return n.name; });
    var row2 = el('div', 'btnrow');
    var r1 = cmdButton('Restart one after the other', 'refresh', 'primary');
    explain(r1, 'The other node first, then this one - each in maintenance while it restarts, so the VIP address and the service never stop.');
    var r2 = cmdButton('Restart both at once', 'refresh', '');
    var r3 = cmdButton('Power off both', 'power', 'danger');
    [r1, r2, r3].forEach(function (b) { b.disabled = !!d.busy; });
    r1.onclick = function () { pwAsk('reboot', all, 'rolling'); };
    r2.onclick = function () { pwAsk('reboot', all, 'together'); };
    r3.onclick = function () { pwAsk('poweroff', all, 'together'); };
    row2.appendChild(r1); row2.appendChild(r2); row2.appendChild(r3);
    both.appendChild(row2);
    host.appendChild(both);
  }
  }, live, function () { explainWhatWasDrawn(real); });
}
function pwAsk(how, targets, order, n) {
  var d = PW.d;
  var who = targets.join(', ');
  var words;
  if (how === 'poweroff') words = targets.length > 1 ? 'Both nodes are switched off. Nothing answers - RADIUS, the pages - until somebody switches them on again at the machines.'
    : (d.cluster ? n.name + ' is switched off; the VIP address moves to the other node, which goes on answering alone. It has to be switched on again at the machine.' : 'The server is switched off: Plex Media Server, the dashboard and these pages stop - whoever is watching too. It has to be switched on again at the machine.');
  else if (order === 'rolling') words = 'One after the other: each goes into maintenance, the VIP address moves to the other node, it restarts and comes back into service - then the next. RADIUS and the pages go on answering all the time.';
  else if (targets.length > 1) words = 'Both restart at the same moment: for a minute or two nothing answers - RADIUS, the pages, the VIP address.';
  else words = d.cluster ? n.name + ' goes into maintenance, the VIP address moves to the other node, and it restarts; back up, it comes back into service by itself.' : 'The server restarts: for a minute or two nothing answers - Plex Media Server, the dashboard, these pages; whoever is watching is stopped.';
  confirmThen((how === 'reboot' ? 'Restart ' : 'Power off ') + who + '?', htmlText(words), how === 'reboot' ? 'Restart' : 'Power off', function () {
    api('power', {how: how, targets: targets, order: order}).then(function (r) {
      toast(r.message, 'good');
      PW.reload = targets.indexOf(((d.nodes.filter(function (x) { return x.me; })[0]) || {}).name) >= 0;
      loadPower();
    }).catch(oops);
  }, how === 'poweroff');
}
function pwProgress(d) {
  var plan = d.plan;
  var card = el('div', 'card full net-job');
  var done = pwAllDone(d);
  card.appendChild(el('h2', '', (plan.how === 'reboot' ? 'Restart' : 'Power-off') + (done ? ' - done' : ' under way')));
  var ol = el('ol', 'net-steps done');
  d.nodes.filter(function (n) { return (plan.targets || []).indexOf(n.name) >= 0; })
    .sort(function (a, b) { return plan.targets.indexOf(a.name) - plan.targets.indexOf(b.name); })
    .forEach(function (n) {
      var st = pwStateOf(d, n);
      var li = el('li', st.k === 'ok' ? 'good' : (st.k === 'bad' ? 'bad' : ''));
      li.appendChild(el('span', 'mark', st.k === 'ok' ? '✓' : (st.k === 'bad' ? '✗' : '…')));
      li.appendChild(noI18n(el('b', '', n.name + ' ')));
      li.appendChild(el('span', '', st.w));
      var r = n.row || {};
      if (st.k === 'going' && r.down_at) {
        var t = Math.max(0, d.now - r.down_at);
        var bar = el('div', 'going wide');
        bar.innerHTML = '<div class="bar"><span></span></div>';
        bar.querySelector('span').style.width = Math.min(100, Math.round(t / 120 * 100)) + '%';
        li.appendChild(el('span', 'muted small', ' ' + span(t)));
        li.appendChild(bar);
      }
      ol.appendChild(li);
    });
  card.appendChild(ol);
  if (done) card.appendChild(hideBar(function () {
    return api('power', {action: 'hide'}).then(function (x) { PW.d = x; drawPower(); });
  }, 'Hidden by itself half an hour after it began.'));
  return card;
}
function drawPowerAway() {
  var host = $('pw-host');
  if (!host) return;
  var box = $('pw-away');
  if (!box) { box = el('div', 'card full net-job'); box.id = 'pw-away'; host.insertBefore(box, host.firstChild); }
  clear(box);
  var away = Math.round((Date.now() - PW.failSince) / 1000);
  box.appendChild(el('h2', '', 'This page\'s server is restarting'));
  var bar = el('div', 'going wide');
  bar.innerHTML = '<div class="bar"><span></span></div>';
  bar.querySelector('span').style.width = Math.min(100, Math.round(away / 120 * 100)) + '%';
  box.appendChild(bar);
  box.appendChild(el('p', 'note', 'Not answering for ' + span(away) + ' - asked again every two seconds; the page comes back by itself.'));
}

/* ---- Settings → Time (1.0.120) -------------------------------------------------
   Each node's clock as chrony sees it, its sources, and the time servers for the
   whole cluster. */
var NTP = {d: null, poll: null, draft: null, node: ''};
function loadNtp() {
  api('ntp').then(function (d) {
    NTP.d = d;
    if (!NTP.node) NTP.node = (d.nodes[0] || {}).name;
    drawNtp();
    if (!NTP.poll) NTP.poll = setInterval(function () {
      if (TAB !== 'settings' || PANE !== 'time') { clearInterval(NTP.poll); NTP.poll = null; return; }
      api('ntp').then(function (x) { NTP.d = x; drawNtp(true); }).catch(function () {});
    }, 10000);
  }).catch(oops);
}
function ntpMs(s) {
  if (s === undefined || s === null || isNaN(s)) return '–';
  var ms = s * 1000;
  var a = Math.abs(ms);
  return (ms >= 0 ? '+' : '−') + (a >= 1000 ? (a / 1000).toFixed(2) + ' s' : (a >= 10 ? a.toFixed(1) : a.toFixed(3)) + ' ms');
}
function ntpHealth(n) {
  var d = (n.now || {}).data || {};
  var t = d.tracking || {};
  if (!d.time) return {k: 'bad', w: n.alive ? 'not read yet' : 'not answering'};
  if (!d.chrony) return {k: d.ntpsynchronized === 'yes' ? 'ok' : 'bad', w: d.ntpsynchronized === 'yes' ? 'synchronised (not chrony)' : 'chrony is not installed'};
  if (t.leap === 'Not synchronised' || !t.stratum) return {k: 'bad', w: 'not synchronised'};
  var off = Math.abs(t.offset || 0);
  if (off > 1) return {k: 'bad', w: 'synchronised, but off'};
  if (off > 0.1) return {k: 'warn', w: 'synchronised'};
  return {k: 'ok', w: 'synchronised'};
}
function drawNtp(quiet) {
  var real = $('ntp-host');
  if (!real || !NTP.d) return;
  /* What the server has, until something is changed here. */
  if (!NTP.draft || !PENDING.ntp) NTP.draft = JSON.parse(JSON.stringify(NTP.d.conf));
  liveFill(real, function (host) {
  var d = NTP.d;
  var grid = el('div', 'pw-grid');
  d.nodes.forEach(function (n) {
    var nd = (n.now || {}).data || {};
    var t = nd.tracking || {};
    var h = ntpHealth(n);
    var c = el('div', 'card ntp-node');
    var head = el('div', 'pw-head');
    head.appendChild(el('span', 'dot ' + (h.k === 'ok' ? 'ok' : (h.k === 'warn' ? 'warn' : 'bad'))));
    head.appendChild(noI18n(el('b', '', n.name)));
    if (n.me) head.appendChild(el('span', 'tag info', 'this page'));
    head.appendChild(el('span', 'tag ' + (h.k === 'ok' ? 'ok' : (h.k === 'warn' ? 'warn' : 'crit')), h.w));
    c.appendChild(head);
    var big = el('div', 'ntp-big');
    big.appendChild(noI18n(el('span', 'ntp-off', ntpMs(t.offset))));
    big.appendChild(el('span', 'muted small', ' off the time servers'));
    c.appendChild(big);
    var clock = '';
    if (nd.time) {
      var dt = new Date((nd.time + (d.now - (n.now.at || d.now))) * 1000);
      clock = dt.toLocaleString(numLang());
    }
    c.appendChild(netFacts([
      ['Follows', t.ref || t.ref_id || '', true],
      ['Stratum', t.stratum !== undefined ? String(t.stratum) : '', true],
      ['Last correction', ntpMs(t.last_offset), true],
      ['Frequency', t.freq !== undefined ? t.freq.toFixed(3) + ' ppm' : '', true],
      ['Root delay / dispersion', t.root_delay !== undefined ? ntpMs(t.root_delay).replace('+', '') + ' / ' + ntpMs(t.root_disp).replace('+', '') : '', true],
      ['Leap status', t.leap || ''],
      ['Time zone', nd.timezone || '', true],
      ['Its time', clock],
      ['Read', n.now && n.now.at ? ago(n.now.at) : '']
    ]));
    var ap = n.applied || {};
    if (ap.at) {
      var st = el('p', 'small ' + (ap.ok ? 'muted' : 'bad'));
      st.textContent = (ap.ok ? 'Time servers set ' : 'Time servers NOT set ') + ago(ap.at) + (ap.said ? ': ' + ap.said : '');
      c.appendChild(st);
    }
    var row = el('div', 'btnrow');
    var re = cmdButton('Read it again', 'refresh', 'small');
    re.onclick = function () { api('ntp', {action: 'read', node: n.name}).then(function (r) { toast(r.message, 'good'); setTimeout(loadNtp, 4000); }).catch(oops); };
    var sy = cmdButton('Step it now', 'clock', 'small');
    explain(sy, 'The clock set to the time servers at once (chronyc makestep), not slewed slowly - for a clock that is far off.');
    sy.onclick = function () {
      confirmThen('Step the clock of ' + n.name + ' now?', htmlText('The clock jumps to the time servers\' time at once. Codes and logs written in that moment may look out of order.'), 'Step it', function () {
        api('ntp', {action: 'sync', node: n.name}).then(function (r) { toast(r.message, 'good'); setTimeout(loadNtp, 6000); }).catch(oops);
      }, false);
    };
    row.appendChild(re); row.appendChild(sy);
    c.appendChild(row);
    grid.appendChild(c);
  });
  host.appendChild(grid);
  if (d.cluster && d.nodes.length > 1) {
    var a = ((d.nodes[0].now || {}).data || {}).tracking || {};
    var b = ((d.nodes[1].now || {}).data || {}).tracking || {};
    if (a.offset !== undefined && b.offset !== undefined) {
      var diff = Math.abs(a.offset - b.offset);
      host.appendChild(el('p', 'note ' + (diff > 1 ? 'bad' : (diff > 0.1 ? 'warn' : 'good')), 'Between the two nodes: ' + ntpMs(diff).replace('+', '') + (diff > 1 ? ' - codes right on one node may be refused on the other' : '')));
    }
  }
  /* Sources, per node. */
  var sc = el('div', 'card full');
  var sh = el('h2', '', 'Where the time comes from');
  sc.appendChild(sh);
  if (d.cluster) {
    var seg = el('div', 'seg');
    d.nodes.forEach(function (n) {
      var bt = el('button', n.name === NTP.node ? 'on' : '', n.name);
      bt.type = 'button';
      noI18n(bt);
      bt.onclick = function () { NTP.node = n.name; drawNtp(false); };
      seg.appendChild(bt);
    });
    sc.appendChild(seg);
  }
  var sn = d.nodes.filter(function (x) { return x.name === NTP.node; })[0] || d.nodes[0];
  var srcs = (((sn || {}).now || {}).data || {}).sources || [];
  var STATES = {'*': ['ok', 'followed'], '+': ['ok', 'combined'], '-': ['', 'not combined'], '?': ['crit', 'unreachable'],
                'x': ['crit', 'false ticker'], '~': ['warn', 'too variable']};
  if (!srcs.length) sc.appendChild(el('p', 'note', 'No source - chrony has none, or is not installed.'));
  else sc.appendChild(listTable(['State', 'Source', 'Stratum', 'Asked every', 'Reached', 'Last heard', 'Offset'], srcs, function (s) {
    var tr = el('tr');
    var stt = STATES[s.state] || ['', s.state];
    var td = el('td');
    td.appendChild(el('span', 'tag ' + stt[0], stt[1]));
    if (s.mode === '=') td.appendChild(el('span', 'muted small', ' peer'));
    tr.appendChild(td);
    tr.appendChild(noI18n(el('td', 'mono', s.name)));
    tr.appendChild(el('td', 'num', String(s.stratum)));
    tr.appendChild(el('td', 'num', span(Math.pow(2, s.poll || 0))));
    var r = el('td');
    var bits = el('span', 'reach');
    for (var i = 7; i >= 0; i--) bits.appendChild(el('i', (s.reach >> i) & 1 ? 'on' : ''));
    bits.title = (s.reach || 0).toString(2).padStart(8, '0') + ' - the last 8 tries';
    r.appendChild(bits);
    tr.appendChild(r);
    tr.appendChild(el('td', 'muted', s.last_rx === null || s.last_rx === undefined ? '–' : span(s.last_rx) + ' ago'));
    tr.appendChild(noI18n(el('td', 'num mono', ntpMs(s.offset) + ' ± ' + ntpMs(s.error).replace('+', ''))));
    return tr;
  }, 'plain'));
  var own = (((sn || {}).now || {}).data || {}).own || [];
  if (own.length && !(((sn || {}).now || {}).data || {}).managed) {
    sc.appendChild(el('p', 'muted small', 'Its own /etc/chrony.conf: '));
    sc.appendChild(noI18n(el('pre', 'mono small', own.join('\n'))));
  }
  host.appendChild(sc);
  host.appendChild(ntpForm());
  }, !!quiet, function () { explainWhatWasDrawn(real); });
}
function ntpForm() {
  var d = NTP.d;
  var dr = NTP.draft;
  var card = el('div', 'card full');
  card.id = 'ntp-form';
  /* Anything changed here waits for the save bar at the bottom. */
  ['input', 'change'].forEach(function (ev) { card.addEventListener(ev, function () { ntpDirty(); }); });
  card.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('button')) ntpDirty(); });
  card.appendChild(el('h2', '', 'The time servers'));
  var on = switchBox(dr.manage);
  var g0 = el('div', 'formgrid');
  g0.appendChild(formField('plexmon sets the time servers', on, 'Off: the machine keeps its own /etc/chrony.conf (AlmaLinux\'s pool, or what you set by hand) - shown and checked here, not changed. On: the servers below; the file\'s own server and pool lines are commented out, and put back when switched off again.'));
  card.appendChild(g0);
  var body = el('div');
  card.appendChild(body);
  var draw = function () {
    clear(body);
    body.hidden = !dr.manage;
    var list = el('div', 'ntp-servers');
    (dr.servers || []).forEach(function (s, i) {
      var row = el('div', 'ntp-srv');
      var hostIn = textBox(s.host, 'ntp.example.cz', 'mono');
      hostIn.oninput = function () { s.host = hostIn.value.trim(); };
      var kind = selectBox([['server', 'one server'], ['pool', 'a pool (several addresses)']], s.pool ? 'pool' : 'server');
      kind.onchange = function () { s.pool = kind.value === 'pool'; };
      row.appendChild(hostIn); row.appendChild(kind);
      [['iburst', 'iburst', 'Four quick questions at the start: synchronised in seconds, not minutes.'],
       ['prefer', 'preferred', 'Followed before the others when it is good.'],
       ['nts', 'NTS', 'Network Time Security: the server proves who it is (time.cloudflare.com, ptbtime1.ptb.de, nts.netnod.se…). Needs TCP 4460 out.']].forEach(function (o) {
        var l = el('label', 'check small');
        var cb = el('input'); cb.type = 'checkbox'; cb.checked = !!s[o[0]];
        cb.onchange = function () { s[o[0]] = cb.checked; };
        l.appendChild(cb); l.appendChild(el('span', '', o[1]));
        explain(l, o[2]);
        row.appendChild(l);
      });
      var x = iconButton('trash', 'Take it away');
      x.onclick = function () { dr.servers.splice(i, 1); draw(); };
      row.appendChild(x);
      list.appendChild(row);
    });
    body.appendChild(list);
    var adds = el('div', 'btnrow');
    var add = cmdButton('Add a server', 'add', 'small');
    add.onclick = function () { dr.servers.push({host: '', pool: false, iburst: true, prefer: false, nts: false}); draw(); };
    adds.appendChild(add);
    [['cz.pool.ntp.org', true, false], ['pool.ntp.org', true, false], ['time.cloudflare.com', false, true], ['tik.cesnet.cz', false, false], ['tak.cesnet.cz', false, false]].forEach(function (p) {
      var b = cmdButton('+ ' + p[0], '', 'small');
      noI18n(b);
      b.onclick = function () {
        if (dr.servers.some(function (s) { return s.host === p[0]; })) return;
        dr.servers.push({host: p[0], pool: p[1], iburst: true, prefer: false, nts: p[2]}); draw();
      };
      adds.appendChild(b);
    });
    body.appendChild(adds);
    var g = el('div', 'formgrid');
    g.style.marginTop = '14px';
    if (d.cluster) {
      var peers = switchBox(dr.peers);
      peers._input.onchange = function () { dr.peers = !!peers._get(); };
      g.appendChild(formField('The nodes take each other too', peers, 'Each node is one more source for the other: if the servers above are out of reach, the two nodes at least keep one time - which is what codes need.'));
    }
    var stepS = textBox(String((dr.makestep || [1, 3])[0]), '1', 'mono');
    stepS.oninput = function () { dr.makestep = [stepS.value, (dr.makestep || [1, 3])[1]]; };
    var stepN = textBox(String((dr.makestep || [1, 3])[1]), '3', 'mono');
    stepN.oninput = function () { dr.makestep = [(dr.makestep || [1, 3])[0], stepN.value]; };
    g.appendChild(formField('Step when off by more than (s)', stepS, 'Further off than this, the clock jumps to the right time instead of being slowed or sped up gently.'));
    g.appendChild(formField('…in the first updates', stepN, 'How many updates after chronyd starts may step it: 3 is after a boot; -1 any time.'));
    var mins = textBox(String(dr.minsources || 1), '1', 'mono');
    mins.oninput = function () { dr.minsources = mins.value; };
    g.appendChild(formField('Sources that have to agree', mins, 'The clock is set only when at least this many sources agree. 1 with one server; 2 or 3 with several.'));
    var allow = textBox((dr.allow || []).join(', '), '192.0.2.0/24', 'mono');
    /* A list, as it came (1.0.146): kept as the typed text, the next drawing
       of the pane broke on .join and it stopped refreshing. */
    allow.oninput = function () { dr.allow = allow.value.split(/[\s,;]+/).filter(Boolean); };
    g.appendChild(formField('Serve the time to', allow, 'Networks that may ask this server for the time (UDP 123) - the televisions and players of the LAN, say. Empty: nobody. Open UDP 123 in the firewall yourself.'));
    body.appendChild(g);
  };
  on._input.onchange = function () { dr.manage = !!on._get(); draw(); };
  draw();
  var zones = d.zones || [];
  var tz = selectBox([['', '- as each machine has it -']].concat(zones.map(function (z) { return [z, z]; })), dr.timezone || '');
  tz.onchange = function () { dr.timezone = tz.value; };
  var g2 = el('div', 'formgrid');
  g2.style.marginTop = '14px';
  g2.appendChild(formField('Time zone', tz, 'Of the machines: the logs and the times on these pages. Codes do not depend on it.'));
  card.appendChild(g2);
  card.appendChild(el('p', 'muted small', 'Saved with the bar at the bottom; each node then puts them in by itself within seconds.'));
  return card;
}
function ntpDirty() {
  pendingSet('ntp', {pane: 'time', save: ntpSave, reset: function () {
    NTP.draft = JSON.parse(JSON.stringify(NTP.d.conf)); drawNtp(false);
  }});
}
function ntpSave() {
  var dr = NTP.draft;
  if (!dr) return Promise.resolve(true);
  var c = {manage: dr.manage, servers: (dr.servers || []).filter(function (s) { return s.host; }), peers: dr.peers,
           allow: dr.allow, step_s: (dr.makestep || [1, 3])[0], step_n: (dr.makestep || [1, 3])[1],
           minsources: dr.minsources, timezone: dr.timezone};
  return api('ntp', {action: 'save', conf: c}).then(function (r) {
    toast(r.message, 'good'); NTP.draft = null; setTimeout(loadNtp, 5000); return true;
  });
}


/* ---- Settings → System updates (1.0.158) ----------------------------------------
   What each node has waiting, the update one node at a time - out of service,
   dnf or apt, back in service - with its output as it comes, and the restart
   it needs offered at the end. */
var OU = {d: null, poll: null, node: '', hide: false, reboot: 'ask', asked: 0};
try { OU.hide = localStorage.getItem('plexmon-ou-hide') === '1'; } catch (e) { /* only a convenience */ }
var OU_STATE = {waiting: ['waits its turn', 'dim'], draining: ['out of service', 'warn'],
                updating: ['updating', 'info'], rebooting: ['restarting', 'warn'],
                done: ['done', 'good'], failed: ['failed', 'bad'], skipped: ['left out', 'dim']};
function loadOsup() {
  api('osup').then(function (d) { OU.d = d; drawOsup(); ouPoll(ouLive(d)); }).catch(oops);
}
function ouLive(d) {
  d = d || OU.d || {};
  /* Just asked: the node has not begun yet, followed until it says (1.0.159). */
  if (Date.now() - OU.asked < 30000) return true;
  return (d.plan && d.plan.state === 'running') || (d.nodes || []).some(function (n) { return (n.check || {}).state === 'running'; }) || !!(d.power && d.power.id);
}
function ouPoll(on) {
  if (on && !OU.poll) {
    OU.poll = setInterval(function () {
      if (TAB !== 'settings' || PANE !== 'osup') { ouPoll(false); return; }
      api('osup').then(function (d) { OU.d = d; OU.away = 0; drawOsup(true); if (!ouLive(d)) ouPoll(false); })
        .catch(function () { OU.away = OU.away || Date.now(); drawOsup(true); });
    }, 2500);
  }
  if (!on && OU.poll) { clearInterval(OU.poll); OU.poll = null; }
}
/* Something asked of the nodes: followed from now on. */
function ouAsked() { OU.asked = Date.now(); ouPoll(true); }
/* A button clicked keeps the focus, and a live redraw leaves alone what has
   it - the window that was clicked in stayed as it was (1.0.159). */
function liveLetGo(host) {
  if (!host || host._letGo) return;
  host._letGo = true;
  host.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('button') : null;
    if (b && host.contains(b)) setTimeout(function () { if (document.activeElement === b) b.blur(); }, 0);
  });
}
function ouPercent(r) {
  var st = (r || {}).state;
  if (st === 'done' || st === 'failed') return 100;
  if (st === 'draining') return Math.min(8, 3 + Math.round((Date.now() / 1000 - Number(r.drain_at || r.at || 0)) / 3));
  if (st === 'updating') return Math.max(8, Math.min(95, Number(r.pct || 0)));
  if (st === 'rebooting') return 96;
  return 0;
}
function drawOsup(live) {
  var real = $('ou-host');
  if (!real || !OU.d) return;
  liveLetGo(real);
  liveFill(real, function (host) {
    var d = OU.d, plan = d.plan || {}, nodes = d.nodes || [];
    var running = plan.state === 'running';
    if (OU.away) host.appendChild(el('p', 'note warn', 'This page\'s server is not answering for ' + span(Math.round((Date.now() - OU.away) / 1000)) + ' - restarting, or the VIP address moving; asked again every few seconds.'));
    /* A restart the update left to do. */
    var need = nodes.filter(function (n) { return n.reboot; });
    if (need.length && !(d.power && d.power.id)) {
      var rb = el('div', 'card full notice warn ou-reboot');
      rb.appendChild(el('h2', '', need.length > 1 ? 'The nodes need a restart' : need[0].name + ' needs a restart'));
      rb.appendChild(el('p', 'note', 'A new kernel or libraries are in that only a restart brings into use. ' + (d.cluster ? 'Restarted one after the other, each in maintenance while it is, the service never stops.' : 'For a minute or two Plex Media Server does not answer.')));
      var go = cmdButton(need.length > 1 ? 'Restart them, one at a time' : 'Restart ' + need[0].name, 'refresh', 'primary');
      go.onclick = function () {
        confirmThen('Restart after the update', 'Restarted now: ' + htmlText(need.map(function (n) { return n.name; }).join(', ')) + (need.length > 1 ? ', one after the other.' : '.'), 'Restart', function () {
          api('osup', {action: 'reboot', nodes: need.map(function (n) { return n.name; })}).then(function (x) { toast(x.message, 'good'); ouAsked(); }).catch(oops);
        }, false);
      };
      rb.appendChild(go);
      host.appendChild(rb);
    }
    if (d.power && d.power.id) host.appendChild(el('p', 'note', 'Restarting: ' + (d.power.targets || []).join(', ') + ' - followed on Restart and power off.'));
    /* What each node has waiting. */
    var grid = el('div', 'pw-grid');
    nodes.forEach(function (n) {
      var c = el('div', 'card pw-node');
      var hd = el('div', 'pw-head');
      hd.appendChild(el('span', 'dot ' + (n.alive ? 'ok' : 'bad')));
      hd.appendChild(noI18n(el('b', '', n.name)));
      if (n.me) hd.appendChild(el('span', 'tag info', 'this page'));
      var ck = n.check || {};
      if (ck.state === 'running') hd.appendChild(el('span', 'tag info', 'looking…'));
      else if (ck.checked && ck.state === 'done') hd.appendChild(el('span', 'tag ' + (ck.count ? 'warn' : 'good'), ck.count ? num(ck.count) + ' waiting' : 'up to date'));
      /* The restart on a line of its own (2.2.1): beside the name it ran out of the card. */
      if (n.reboot || (ck.reboot_after && ck.count)) hd.appendChild(el('span', 'pw-break'));
      if (n.reboot) hd.appendChild(el('span', 'tag warn', 'restart needed'));
      else if (ck.reboot_after && ck.count) hd.appendChild(el('span', 'tag info', 'restart after the update'));
      c.appendChild(hd);
      c.appendChild(netFacts([
        ['Updates waiting', ck.checked ? num(ck.count || 0) : 'not looked yet'],
        ['Of them security', ck.security !== undefined && ck.security !== null ? num(ck.security) : ''],
        ['A restart', !ck.checked ? '' : (ck.reboot_now ? 'needed now, before any update'
          : (ck.reboot_after && ck.count ? 'needed after the update - ' + (ck.reboot_why || []).join(', ') : 'not needed by these updates'))],
        ['Looked', ck.checked ? ago(ck.checked) : ''],
        ['Updated by', ck.pm || '', true]
      ]));
      if (ck.error) c.appendChild(el('p', 'note bad', ck.error));
      if ((ck.packages || []).length) {
        var more = el('details', 'fold');
        more.appendChild(el('summary', '', 'what is waiting'));
        more.appendChild(noI18n(el('pre', 'lines', ck.packages.slice(0, 300).join('\n') + (ck.packages.length > 300 ? '\n…' : ''))));
        OU.pkOpen = OU.pkOpen || {};
        if (OU.pkOpen[n.name]) more.open = true;
        more.ontoggle = function () { OU.pkOpen[n.name] = more.open; };
        c.appendChild(more);
      }
      var row = el('div', 'btnrow');
      var look = cmdButton('Look for updates', 'search', '');
      look.disabled = !n.alive || ck.state === 'running' || running;
      look.onclick = function () { busy(look, true); api('osup', {action: 'check', nodes: [n.name]}).then(function (x) { toast(x.message); OU.node = n.name; ouAsked(); }).catch(function (e) { busy(look, false); oops(e); }); };
      row.appendChild(look);
      c.appendChild(row);
      grid.appendChild(c);
    });
    host.appendChild(grid);
    /* The update itself. */
    if (!running) {
      var uc = el('div', 'card full');
      uc.appendChild(el('h2', '', d.cluster ? 'Update the nodes' : 'Update this server'));
      uc.appendChild(el('p', 'note', d.cluster
        ? 'The other node first, then this one - each out of service while it is updated, the VIP address on the other, RADIUS answered there; back in service before the next one starts.'
        : 'dnf updates every package' + (d.plex_too ? ', Plex Media Server too (it restarts)' : ', Plex Media Server\'s own left out') + '. Plex goes on playing meanwhile; a restart it needs is offered at the end.'));
      var segb = el('div', 'seg');
      [['ask', 'Ask me when a restart is needed'], ['auto', 'Restart by itself when needed' + (d.cluster ? ', one at a time' : '')]].forEach(function (o) {
        var b = el('button', OU.reboot === o[0] ? 'on' : '', o[1]);
        b.type = 'button';
        b.onclick = function () { OU.reboot = o[0]; drawOsup(); };
        segb.appendChild(b);
      });
      var after = nodes.filter(function (n) { var k = n.check || {}; return k.reboot_after && k.count; });
      if (after.length) {
        var an = el('p', 'note warn', (after.length === nodes.length && nodes.length > 1 ? 'Both nodes need' : after.map(function (n) { return n.name; }).join(', ') + (after.length > 1 ? ' need' : ' needs'))
          + ' a restart after this update: ' + after.map(function (n) { return (n.check.reboot_why || []).join(', '); }).filter(function (x, i, a) { return x && a.indexOf(x) === i; }).join('; ') + '.');
        uc.appendChild(an);
      }
      var seg2 = el('div', 'seg');
      [['all', 'Every update'], ['security', 'Only the security updates']].forEach(function (o) {
        var b = el('button', (OU.what || 'all') === o[0] ? 'on' : '', o[1]);
        b.type = 'button';
        b.onclick = function () { OU.what = o[0]; drawOsup(); };
        seg2.appendChild(b);
      });
      uc.appendChild(formField('Which', seg2));
      uc.appendChild(formField('After the update', segb));
      var row2 = el('div', 'btnrow');
      var start = cmdButton(d.cluster ? 'Update them, one at a time' : 'Update it now', 'download', 'primary');
      start.disabled = !d.helper || nodes.some(function (n) { return !n.alive; }) || !!(d.power && d.power.id);
      start.onclick = function () {
        confirmThen(d.cluster ? 'Update the nodes' : 'Update this server', 'Every package of the system is updated' + (d.cluster ? ', on ' + htmlText(nodes.map(function (n) { return n.name; }).join(' and then ')) + ', each out of service while it is.' : '.')
          + (OU.reboot === 'auto' ? '<br><br>A node that needs it is <b>restarted by itself</b>.' : '<br><br>A restart it needs is offered when it is done.'),
          'Update', function () {
            api('osup', {action: 'start', reboot: OU.reboot, security: OU.what === 'security'}).then(function (x) { toast(x.message, 'good'); OU.node = ''; ouAsked(); loadOsup(); }).catch(oops);
          }, false);
      };
      var lookAll = cmdButton('Look on every node', 'search', '');
      lookAll.onclick = function () { busy(lookAll, true); api('osup', {action: 'check'}).then(function (x) { toast(x.message); ouAsked(); }).catch(function (e) { busy(lookAll, false); oops(e); }); };
      row2.appendChild(start); if (d.cluster) row2.appendChild(lookAll);
      uc.appendChild(row2);
      if (!d.helper) uc.appendChild(el('p', 'note bad', 'The root helper of this node is not installed (install.sh --upgrade puts it in).'));
      host.appendChild(uc);
    }
    /* How far it is. */
    if (plan.id) {
      var pc = el('div', 'card full');
      var ph = el('h2', '', 'The update');
      if (running) {
        var stop = cmdButton('Stop it', 'cancel', 'small danger');
        explain(stop, 'The nodes not begun stay as they are; a node being updated finishes.');
        stop.onclick = function () { confirmThen('Stop the update', 'The nodes not yet begun stay as they are; one being updated finishes.', 'Stop it', function () { api('osup', {action: 'cancel'}).then(function (x) { toast(x.message); loadOsup(); }).catch(oops); }, true); };
        var tl = el('span', 'right'); tl.appendChild(stop); ph.appendChild(tl);
      }
      pc.appendChild(ph);
      pc.appendChild(el('p', 'note', 'Started by ' + (plan.by || '?') + ' · ' + ago(plan.at) + ' · ' + ({running: 'going on', done: 'done', failed: 'failed', cancelled: 'stopped'})[plan.state || 'running']
        + (plan.reboot === 'auto' ? ' · restarted where needed' : '') + (plan.security ? ' · security updates only' : '')));
      var steps = (plan.order || []).map(function (nm) {
        var n = nodes.filter(function (x) { return x.name === nm; })[0] || {name: nm};
        return {name: nm, row: n.row || {}};
      });
      var all = steps.length ? Math.round(steps.reduce(function (m, s) { return m + ouPercent(s.row); }, 0) / steps.length) : 0;
      pc.appendChild(upBar(all, plan.state === 'failed' ? 'bad' : (plan.state === 'done' ? 'good' : ''),
        num(all) + ' % · ' + steps.filter(function (s) { return s.row.state === 'done'; }).length + ' of ' + steps.length + ' node(s) done', true));
      var t = listTable(['Node', 'Step', 'How far', 'What happened', 'When'], steps, function (s) {
        var tr = el('tr');
        tr.appendChild(noI18n(el('td', 'mono', s.name)));
        var w = OU_STATE[s.row.state] || OU_STATE.waiting;
        var c = el('td'); c.appendChild(el('span', 'tag ' + w[1], w[0])); tr.appendChild(c);
        var b2 = el('td', 'up-how');
        var p2 = ouPercent(s.row);
        b2.appendChild(upBar(p2, s.row.state === 'failed' ? 'bad' : (s.row.state === 'done' ? 'good' : ''), num(p2) + ' %' + (s.row.state === 'updating' && s.row.phase ? ' · ' + txT(s.row.phase) : '')));
        tr.appendChild(b2);
        tr.appendChild(el('td', 'small', s.row.said || ''));
        tr.appendChild(el('td', 'muted', s.row.at ? ago(s.row.at) : '–'));
        return tr;
      }, 'plain');
      (t.querySelector('table') || t).classList.add('up-plan-table');
      pc.appendChild(t);
      if (!running) pc.appendChild(hideBar(function () { return api('osup', {action: 'hide'}).then(function (x) { OU.d = x; drawOsup(); }); }));
      host.appendChild(pc);
    }
    /* What the package manager says, a node at a time. */
    var have = nodes.filter(function (n) { return ((n.row || {}).lines || []).length || ((n.check || {}).lines || []).length; });
    if (have.length) {
      if (!OU.node || !have.some(function (n) { return n.name === OU.node; })) {
        var busyOne = have.filter(function (n) { return (n.row || {}).state === 'updating' || (n.check || {}).state === 'running'; })[0];
        OU.node = (busyOne || have[0]).name;
      }
      var lc = el('div', 'card full');
      var lh = el('h2', '', 'What it says');
      var lt = el('span', 'right');
      var hb = cmdButton(OU.hide ? 'Show it' : 'Hide it', OU.hide ? 'unfold' : 'fold', 'small');
      hb.onclick = function () { OU.hide = !OU.hide; try { localStorage.setItem('plexmon-ou-hide', OU.hide ? '1' : '0'); } catch (e) { /* only a convenience */ } drawOsup(); };
      var cb = cmdButton('Clear the window', 'broom', 'small');
      explain(cb, 'Empty the window of every node - for every page, and after a reload too; what comes after is shown.');
      lt.appendChild(hb); lt.appendChild(cb);
      lh.appendChild(lt);
      lc.appendChild(lh);
      if (have.length > 1) {
        var sg = el('div', 'seg');
        sg.style.marginBottom = '10px';
        have.forEach(function (n) {
          var b3 = noI18n(el('button', OU.node === n.name ? 'on' : '', n.name));
          b3.type = 'button';
          b3.onclick = function () { OU.node = n.name; OU.picked = Date.now(); drawOsup(); };
          sg.appendChild(b3);
        });
        lc.appendChild(sg);
      }
      var one = have.filter(function (n) { return n.name === OU.node; })[0];
      var lines = (one.row || {}).lines && (one.row || {}).lines.length ? one.row.lines : ((one.check || {}).lines || []);
      var from = 0;
      cb.onclick = function () {
        busy(cb, true);
        api('osup', {action: 'clear'}).then(function (x) { OU.d = x; drawOsup(); toast('the window is empty'); })
          .catch(function (e) { busy(cb, false); oops(e); });
      };
      if (!OU.hide) {
        var pre = noI18n(el('pre', 'lines ou-log', lines.slice(from).join('\n') || '–'));
        lc.appendChild(pre);
        setTimeout(function () { pre.scrollTop = pre.scrollHeight; }, 0);
      } else {
        lc.appendChild(el('p', 'muted small', 'Hidden - ' + num(lines.length) + ' line(s).'));
      }
      host.appendChild(lc);
    }
  }, live);
}



/* ==== Backups to another machine (1.0.109) ===================================
   Destinations (a folder, FTP, FTPS, SFTP, SCP, SMB) with their sign-in -
   passwords, ssh keys made here or brought, the server's host key or its
   certificate checked, a client certificate for FTPS - a backup sent now or on
   a schedule, the history, the backups at each destination, and putting one
   back. Only a full administrator sees any of it. */
var RB = null;
var RB_POLL = null;
var RB_KIND_ICON = {folder: 'db', ftp: 'upload', ftps: 'lock', sftp: 'terminal', scp: 'terminal', smb: 'net'};
var RB_KIND_SAYS = {
  folder: 'A folder on this machine - an NFS or other share mounted here.',
  ftp: 'Plain FTP: the password and the file go over unencrypted - only inside a network you trust (the file itself is locked).',
  ftps: 'FTP over TLS - explicit (port 21) or implicit (990); the server’s certificate checked, a client certificate if it wants one.',
  sftp: 'SSH file transfer: a key made here or your own, or a password; the server’s host key checked.',
  scp: 'scp over SSH, and ssh to list and take away old ones - needs a shell on the server.',
  smb: 'A Windows or Samba share: the account, its domain, SMB3 encryption.'
};
function rbWhen(ts) {
  if (!ts) return '';
  var n = Number(ts);
  return isNaN(n) ? String(ts) : 'until ' + new Date(n * 1000).toLocaleDateString(numLang());
}
function rbBytes(n) {
  n = Number(n || 0);
  if (n < 1024) return num(n) + ' B';
  var u = ['kB', 'MB', 'GB', 'TB'], i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return n.toFixed(n < 10 ? 1 : 0) + ' ' + u[i];
}
function rbWhere(t) {
  if (t.kind === 'folder') return t.path || '';
  if (t.kind === 'smb') return '\\\\' + t.host + (t.port && t.port !== 445 ? ':' + t.port : '') + '\\' + (t.share || '') + (t.path ? '\\' + String(t.path).replace(/\//g, '\\') : '');
  return (t.user ? t.user + '@' : '') + t.host + (t.port ? ':' + t.port : '') + '/' + String(t.path || '').replace(/^\//, '');
}
function loadRbackup() {
  var top = $('rb-top');
  if (!top) return;
  if (!ME || ME.role !== 'fulladmin') {
    clear(top);
    top.appendChild(el('p', 'note', 'Only a full administrator sees and changes the backups: they hold the key of every secret.'));
    return;
  }
  api('rbackup').then(function (d) {
    RB = d;
    drawRbTop();
    drawRbTargets();
    drawRbRuns();
    rbPassTools();
    if (d.job && d.job.running) rbWatch();
  }).catch(oops);
}
function rbPassTools() {
  /* "Make one" beside the password of the backups: a long random one, shown
     once to be put into the password vault. */
  var item = document.querySelector('.set-item[data-key="rbackup_password"]');
  if (!item || item.querySelector('.rb-gen')) return;
  var b = cmdButton('Make a strong one', 'key', 'small rb-gen');
  b.style.alignSelf = 'flex-start';
  b.onclick = function () {
    var a = new Uint8Array(24);
    crypto.getRandomValues(a);
    var abc = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
    var pw = '';
    for (var i = 0; i < a.length; i++) { pw += abc.charAt(a[i] % abc.length); if (i % 6 === 5 && i < a.length - 1) pw += '-'; }
    var box = item.querySelector('input');
    if (box) {
      if (box._set) box._set(pw); else box.value = pw;
      box.dispatchEvent(new Event('input', {bubbles: true}));
      box.dispatchEvent(new Event('change', {bubbles: true}));
    }
    modal('The password of the backups', function (body) {
      body.appendChild(el('p', 'note', 'Put it into your password vault now - this is the only time it is shown whole. Without it no backup can be opened, by anybody. Then press Save the changes.'));
      var p = noI18n(el('div', 'rb-pass mono', pw));
      body.appendChild(p);
      var c = cmdButton('Copy', 'copy', 'small');
      c.onclick = function () { copyText(pw, c, 'Copied'); };
      body.appendChild(c);
    }, [{words: 'Close', close: true}], true);
  };
  var says = item.querySelector('.says');
  if (says) says.parentNode.insertBefore(b, says); else item.appendChild(b);
}
function drawRbTop() {
  var host = $('rb-top');
  clear(host);
  var d = RB;
  var card = el('div', 'card full rb-hero');
  var last = (d.runs || [])[0];
  var mine = (d.runs || []).filter(function (r) { return r.node === d.node; })[0];
  var on = (d.targets || []).filter(function (t) { return t.enabled; });
  var state = !on.length ? 'warn' : (!mine ? 'warn' : (mine.ok ? 'good' : 'bad'));
  card.classList.add(state);
  var head = el('div', 'rb-hero-head');
  var ic = el('span', 'rb-hero-ic');
  ic.innerHTML = iconMarkup(state === 'good' ? 'check' : (state === 'bad' ? 'flag' : 'info'), 22);
  head.appendChild(ic);
  var words = el('div', 'rb-hero-words');
  var title = !on.length ? 'No destination yet - backups go nowhere'
    : (!mine ? 'No backup of this node has been sent yet'
      : (mine.ok ? 'The last backup of ' + d.node + ' went to every destination'
        : 'The last backup of ' + d.node + ' did not reach every destination'));
  words.appendChild(el('b', '', title));
  var facts = el('div', 'rb-hero-facts');
  var fact = function (k, v, cls) {
    var f = el('span', 'rb-fact ' + (cls || ''));
    f.appendChild(el('span', 'k', k));
    f.appendChild(el('span', 'v', v));
    facts.appendChild(f);
  };
  if (mine) fact('Last', ago(mine.at) + ' · ' + rbBytes(mine.bytes));
  fact('Next', d.next ? new Date(d.next).toLocaleString(numLang(), {weekday: 'short', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'}) : 'by hand only', d.next ? '' : 'warn');
  fact('Locked', d.locked ? 'with the password (AES-256)' : 'NOT locked - set the password', d.locked ? 'good' : 'bad');
  fact('Destinations', num(on.length) + ' of ' + num((d.targets || []).length) + ' switched on');
  /* The last try (1.0.169): whether the newest backup can be put back. */
  var tried = (d.verified || []).filter(function (v) { return v.node === d.node; })[0];
  fact('Tried', tried ? (tried.ok ? 'can be put back' : 'NOT GOOD') + ' · ' + ago(tried.at) : 'never', tried ? (tried.ok ? 'good' : 'bad') : 'warn');
  words.appendChild(facts);
  if (mine && !mine.ok) {
    var bad = (mine.targets || []).filter(function (x) { return !x.ok; });
    var why = el('div', 'rb-hero-why small bad');
    why.textContent = mine.error || bad.map(function (x) { return x.name + ': ' + (x.error || 'failed'); }).join(' · ');
    words.appendChild(why);
  }
  head.appendChild(words);
  var acts = el('div', 'rb-hero-acts');
  var now = cmdButton('Back up now', 'upload', 'primary');
  now.id = 'rb-now';
  now.disabled = !on.length || (d.job && d.job.running);
  explain(now, 'A backup made now and sent to every destination switched on - the same as the scheduled one.');
  now.onclick = function () {
    busy(now, true);
    api('rbackup', {action: 'run'}).then(function (x) { toast(x.message, 'good'); RB.job = x.job; rbWatch(); })
      .catch(function (e) { busy(now, false); oops(e); });
  };
  acts.appendChild(now);
  var tryb = cmdButton('Try the newest', 'check', '');
  tryb.disabled = !on.length || (d.job && d.job.running);
  explain(tryb, 'The newest backup of this node fetched back from the first destination, opened with the password, every file checked, the databases read through (plexmon\'s and Plex\'s) - nothing is put back.');
  tryb.onclick = function () {
    busy(tryb, true);
    api('rbackup', {action: 'verify'}).then(function (x) { toast(x.message, 'good'); RB.job = x.job; rbWatch(); })
      .catch(function (e) { busy(tryb, false); oops(e); });
  };
  acts.appendChild(tryb);
  var dl = cmdButton('Download one here', 'download', '');
  explain(dl, 'A backup made now, saved on the computer you are sitting at - locked with the password as the others.');
  dl.onclick = function () {
    if (!d.locked) {
      confirmThen('Download a backup that is not locked?', 'No password of the backups is set: the file will hold <b>the key of every secret</b> readable. Set the password first unless you know where the file goes.', 'Download it', function () { window.location.href = '?api=rbackup-download'; }, false);
      return;
    }
    toast('the backup is being made - the download starts when it is ready', 'good');
    window.location.href = '?api=rbackup-download';
  };
  acts.appendChild(dl);
  var back = cmdButton('Put one back…', 'undo', '');
  back.onclick = rbPutBackFile;
  acts.appendChild(back);
  var guide = cmdButton('How to put it back', 'book', '');
  explain(guide, 'RESTORE.md - the exact steps to bring everything back from a backup, filled in with this installation, as every backup carries it. To read now, and to keep apart from the backups.');
  guide.onclick = rbGuide;
  acts.appendChild(guide);
  head.appendChild(acts);
  card.appendChild(head);
  var going = el('div', 'going wide');
  going.id = 'rb-going';
  going.hidden = !(d.job && d.job.running);
  going.innerHTML = '<div class="bar"><span id="rb-going-fill"></span></div><div class="says" id="rb-going-says"></div>';
  card.appendChild(going);
  if (!d.tools.openssl) card.appendChild(el('p', 'note bad', 'openssl is not on this machine: backups cannot be locked (dnf install openssl).'));
  host.appendChild(card);
  /* In a cluster each node backs itself up (1.0.169): every node's last
     backup and last try, side by side. */
  var names = Object.keys(d.last_by_node || {});
  (d.verified || []).forEach(function (v) { if (v.node && names.indexOf(v.node) < 0) names.push(v.node); });
  if (names.indexOf(d.node) < 0) names.push(d.node);
  if (names.length > 1) {
    var grid = nodeGrid();
    grid.classList.add('rb-nodes');
    names.sort().forEach(function (n) {
      var last = (d.last_by_node || {})[n];
      var tr = (d.verified || []).filter(function (v) { return v.node === n; })[0];
      var bad = (last && !last.ok) || (tr && !tr.ok);
      var c = nodeCard({node: n, me: n === d.node}, !last ? 'dim' : (bad ? 'bad' : 'ok'),
        !last ? 'no backup yet' : (last.ok ? 'went everywhere' : 'did not go everywhere'), !last ? 'dim' : (last.ok ? 'ok' : 'crit'));
      nodeBig(c, last ? ago(last.at) : '–', last ? 'the last backup' : '');
      c.appendChild(netFacts([['Size', last && last.bytes ? rbBytes(last.bytes) : ''], ['Tokens', last && last.tokens != null ? num(last.tokens) : ''],
        ['Tried', tr ? (tr.ok ? 'can be put back' : 'NOT GOOD') + ' · ' + ago(tr.at) : 'never']]));
      grid.appendChild(c);
    });
    host.appendChild(grid);
  }
  RB.verified = d.verified || [];
  drawRbTried();
}
/* The backups tried (1.0.169): each try, every check of it; the list can be
   emptied, or a try taken off - the backups themselves stay. */
function drawRbTried() {
  var card = $('rb-tried-card'), host = $('rb-tried'), tools = $('rb-tried-tools');
  if (!card) return;
  var rows = RB.verified || [];
  card.hidden = !rows.length;
  clear(host);
  clear(tools);
  if (!rows.length) return;
  var forget = function (q) {
    api('rbackup', Object.assign({action: 'verified_forget'}, q)).then(function (d) {
      toast(d.message, 'good'); RB.verified = d.verified || []; drawRbTried();
    }).catch(oops);
  };
  var wipe = cmdButton('Clear the list', 'trash', 'small');
  wipe.onclick = function () {
    confirmThen('Clear the list of backups tried?', 'Every try goes from this list (for both nodes). <b>The backups themselves stay where they are.</b> Events keeps a line saying who cleared it.', 'Clear the list', function () { forget({all: 1}); }, true);
  };
  tools.appendChild(wipe);
  var list = el('div', 'rb-tried');
  rows.slice(0, 8).forEach(function (v, i) {
    var it = el('details', 'rb-try ' + (v.ok ? 'good' : 'bad'));
    if (i === 0 && !v.ok) it.open = true;
    var sm = el('summary');
    sm.appendChild(el('span', 'dot ' + (v.ok ? 'ok' : 'bad')));
    sm.appendChild(el('b', '', v.ok ? 'Can be put back' : 'Not good'));
    sm.appendChild(noI18n(el('span', 'mono small', v.file || '?')));
    sm.appendChild(el('span', 'muted small', '·'));
    sm.appendChild(el('span', 'muted small', ago(v.at)));
    sm.appendChild(el('span', 'muted small', '·'));
    sm.appendChild(noI18n(el('span', 'muted small', (v.node || '') + ' → ' + (v.target_name || ''))));
    var x = el('button', 'xbtn rb-try-x');
    x.type = 'button';
    x.setAttribute('data-icon', 'close');
    x.title = 'Take it off the list';
    x.onclick = function (e) { e.preventDefault(); e.stopPropagation(); forget({at: v.at}); };
    sm.appendChild(x);
    it.appendChild(sm);
    var ul = el('div', 'rb-checks');
    (v.checks || []).forEach(function (k) {
      var row = el('div', 'rb-check');
      row.appendChild(el('span', 'dot ' + (k.ok ? 'ok' : 'bad')));
      row.appendChild(el('span', '', k.what));
      if (k.said) row.appendChild(noI18n(el('span', 'muted small', '- ' + k.said)));
      ul.appendChild(row);
    });
    it.appendChild(ul);
    list.appendChild(it);
  });
  host.appendChild(list);
  dressButtons(host);
}
/* RESTORE.md on the pages (1.0.110): read here, downloaded as the file. */
function rbGuide() {
  api('rbackup', {action: 'restore_md'}).then(function (d) {
    modal('How to put a backup back', function (body) {
      var lede = el('p', 'note');
      lede.innerHTML = 'The <code>RESTORE.md</code> every backup carries, as one made now would have it. Keep a copy apart from the backups - with the password of the backups in a vault.';
      body.appendChild(lede);
      var doc = noI18n(el('div', 'md-doc'));
      doc.appendChild(mdRender(d.md));
      body.appendChild(doc);
    }, [{words: 'Close', close: true},
        {words: 'Download RESTORE.md', cls: 'primary', icon: 'download', go: function () {
          var a = el('a');
          a.href = URL.createObjectURL(new Blob([d.md], {type: 'text/markdown;charset=utf-8'}));
          a.download = d.name || 'RESTORE.md';
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
        }}], 'large');
  }).catch(oops);
}
/* Markdown as the guides are written: headings, paragraphs, lists, tables,
   fenced code (with a copy button), `code`, **bold**, *italic*. Built from
   elements - nothing in the text becomes HTML. */
function mdInline(text) {
  var frag = document.createDocumentFragment();
  var re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g, last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
    var t = m[0];
    if (t.charAt(0) === '`') frag.appendChild(el('code', '', t.slice(1, -1)));
    else if (t.slice(0, 2) === '**') frag.appendChild(el('b', '', t.slice(2, -2)));
    else frag.appendChild(el('i', '', t.slice(1, -1)));
    last = re.lastIndex;
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}
function mdRender(md) {
  var out = document.createDocumentFragment();
  var lines = String(md || '').split('\n'), i = 0;
  var para = [];
  var flush = function () {
    if (!para.length) return;
    var p = el('p');
    p.appendChild(mdInline(para.join(' ')));
    out.appendChild(p);
    para = [];
  };
  while (i < lines.length) {
    var ln = lines[i];
    if (/^```/.test(ln)) {
      flush();
      var code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i += 1; }
      i += 1;
      var wrap = el('div', 'md-code');
      var pre = el('pre', '', code.join('\n'));
      wrap.appendChild(pre);
      var cp = iconButton('copy', 'Copy');
      (function (txt, b) {
        b.onclick = function () {
          (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(function () { toast('copied', 'good'); }).catch(function () { toast('select it and copy it by hand'); });
        };
      })(code.join('\n'), cp);
      wrap.appendChild(cp);
      out.appendChild(wrap);
      continue;
    }
    var h = /^(#{1,4})\s+(.*)$/.exec(ln);
    if (h) { flush(); var hn = el('h' + Math.min(6, h[1].length + 2)); hn.appendChild(mdInline(h[2])); out.appendChild(hn); i += 1; continue; }
    if (/^\|/.test(ln)) {
      flush();
      var rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i += 1; }
      var tbl = el('table', 'md-table');
      var tb = el('tbody');
      rows.forEach(function (r, n) {
        if (/^\|[\s|:-]+\|$/.test(r)) return;
        var cells = r.replace(/^\|/, '').replace(/\|$/, '').split('|');
        if (n === 0 && cells.every(function (c) { return !c.trim(); })) return;
        var tr = el('tr');
        cells.forEach(function (c) { var td = el(n === 0 && rows.length > 1 && /^\|[\s|:-]+\|$/.test(rows[1]) ? 'th' : 'td'); td.appendChild(mdInline(c.trim())); tr.appendChild(td); });
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      out.appendChild(tbl);
      continue;
    }
    if (/^\s*[-*] /.test(ln)) {
      flush();
      var ul = el('ul');
      var li = null;
      while (i < lines.length && (/^\s*[-*] /.test(lines[i]) || (li && /^\s{2,}\S/.test(lines[i])))) {
        if (/^\s*[-*] /.test(lines[i])) { li = el('li'); li._t = lines[i].replace(/^\s*[-*] /, ''); ul.appendChild(li); }
        else li._t += ' ' + lines[i].trim();
        i += 1;
      }
      Array.prototype.forEach.call(ul.children, function (x) { x.appendChild(mdInline(x._t)); });
      out.appendChild(ul);
      continue;
    }
    if (/^---+\s*$/.test(ln)) { flush(); out.appendChild(el('hr')); i += 1; continue; }
    if (!ln.trim()) { flush(); i += 1; continue; }
    para.push(ln.trim());
    i += 1;
  }
  flush();
  return out;
}
function rbWatch() {
  if (RB_POLL) return;
  var step = function () {
    api('rbackup', {action: 'job'}).then(function (x) {
      var j = x.job || {};
      var g = $('rb-going');
      if (g) {
        g.hidden = !j.running;
        var f = $('rb-going-fill');
        if (f) f.style.width = (j.whole ? Math.round(100 * j.done / j.whole) : 5) + '%';
        var sy = $('rb-going-says');
        if (sy) sy.textContent = j.says || '';
      }
      if (j.running) { RB_POLL = setTimeout(step, TAB === 'settings' && PANE === 'backup' ? 1000 : 5000); return; }
      RB_POLL = null;
      if (j.error) toast('the backup: ' + j.error, 'bad');
      else if (j.result) toast(j.result.ok ? 'the backup went to every destination' : 'the backup did not reach every destination', j.result.ok ? 'good' : 'bad');
      if (TAB === 'settings' && PANE === 'backup') loadRbackup();
    }).catch(function () { RB_POLL = null; });
  };
  step();
}
function drawRbTargets() {
  var host = $('rb-targets');
  clear(host);
  var add = cmdButton('Add a destination', 'add', 'primary small');
  add.onclick = rbPickKind;
  var ah = $('rb-add-host');
  clear(ah);
  ah.appendChild(add);
  var ts = RB.targets || [];
  if (!ts.length) {
    var empty = el('div', 'rb-empty');
    empty.appendChild(el('p', '', 'No destination yet. A backup kept only on this machine is lost with it: add a place on another machine.'));
    host.appendChild(empty);
    return;
  }
  var grid = el('div', 'rb-grid');
  ts.forEach(function (t) {
    var c = el('div', 'rb-card' + (t.enabled ? '' : ' off'));
    var top = el('div', 'rb-card-top');
    var ic = el('span', 'rb-kind k-' + t.kind);
    ic.innerHTML = iconMarkup(RB_KIND_ICON[t.kind] || 'db', 18);
    top.appendChild(ic);
    var nm = el('div', 'rb-card-name');
    nm.appendChild(noI18n(el('b', '', t.name)));
    nm.appendChild(el('span', 'rb-kind-words', t.kind_words));
    top.appendChild(nm);
    var sw = switchBox(t.enabled);
    explain(sw, t.enabled ? 'Backups go here. Switch off to stop sending, keeping everything set.' : 'Switched off: nothing is sent here.');
    sw._input.onchange = function () {
      api('rbackup', {action: 'save', id: t.id, target: {kind: t.kind, enabled: sw._get(), name: t.name, host: t.host, port: t.port, path: t.path, user: t.user, share: t.share, domain: t.domain, smb_encrypt: t.smb_encrypt, implicit: t.implicit, tls_check: t.tls_check, passive: t.passive}})
        .then(function () { loadRbackup(); }).catch(function (e) { oops(e); loadRbackup(); });
    };
    top.appendChild(sw);
    c.appendChild(top);
    c.appendChild(noI18n(el('div', 'rb-where mono', rbWhere(t))));
    var chips = el('div', 'rb-chips');
    var chip = function (w, cls, tip) { var x = el('span', 'tag ' + (cls || 'dim'), w); if (tip) x.title = tip; chips.appendChild(x); };
    if (t.has_password) chip('password', 'dim');
    if (t.has_key) chip('ssh key', 'violet');
    if (t.ssh_cert) chip('ssh certificate', 'violet');
    if (t.kind === 'sftp' || t.kind === 'scp') chip(t.hostkey ? 'host key trusted' : 'host key not trusted yet', t.hostkey ? 'ok' : 'warn', (t.hostkey_fp || []).join('\n'));
    if (t.kind === 'ftps') chip({system: 'certificate: system authorities', ca: 'certificate: own authority', pin: 'certificate pinned', none: 'certificate NOT checked'}[t.tls_check] || '', t.tls_check === 'none' ? 'warn' : 'ok');
    if (t.has_client_key) chip('client certificate', 'violet');
    if (t.kind === 'smb' && t.smb_encrypt) chip('SMB3 encrypted', 'ok');
    if (t.kind === 'ftp') chip('not encrypted on the way', 'warn');
    c.appendChild(chips);
    var last = t.last;
    var ls = el('div', 'rb-last ' + (last ? (last.ok ? 'good' : 'bad') : 'muted'));
    ls.textContent = last ? (last.ok ? '✓ last sent ' + ago(last.at) + ' · ' + rbBytes(last.bytes) : '✗ ' + ago(last.at) + ': ' + (last.error || 'failed')) : 'nothing sent here yet';
    c.appendChild(ls);
    var acts = el('div', 'rb-acts');
    var tr = cmdButton('Try it', 'play', 'small');
    tr.onclick = function () { rbTry(t, null, tr); };
    acts.appendChild(tr);
    var ed = cmdButton('Edit', 'edit', 'small');
    ed.onclick = function () { rbEdit(t); };
    acts.appendChild(ed);
    var ls2 = cmdButton('Backups there', 'list', 'small');
    ls2.onclick = function () { rbListThere(t); };
    acts.appendChild(ls2);
    var del = iconButton('trash', 'Take this destination away');
    del.onclick = function () {
      confirmThen('Take the destination ' + htmlText(t.name) + ' away?', 'Nothing is deleted on the server; backups simply stop going there, and its keys and certificates set here are forgotten.', 'Take it away', function () {
        api('rbackup', {action: 'delete', id: t.id}).then(function (x) { toast(x.message, 'good'); loadRbackup(); }).catch(oops);
      });
    };
    acts.appendChild(del);
    c.appendChild(acts);
    grid.appendChild(c);
  });
  host.appendChild(grid);
}
function drawRbRuns() {
  var host = $('rb-runs');
  clear(host);
  var runs = RB.runs || [];
  /* The list can be emptied, or a line taken off (1.0.110) - the backups
     themselves stay where they went. */
  var tools = $('rb-runs-tools');
  if (tools) {
    clear(tools);
    if (runs.length) {
      var wipe = cmdButton('Clear the list', 'trash', 'small');
      wipe.onclick = function () {
        confirmThen('Clear the list of backups sent?', 'Every line goes from this list (for both nodes). <b>The backups themselves stay where they went</b> - delete them under <i>Backups there</i> on a destination. Events keeps a line saying who cleared it.', 'Clear the list', function () {
          api('rbackup', {action: 'runs_forget', all: 1}).then(function (d) { toast(d.message, 'good'); RB.runs = d.runs || []; drawRbRuns(); }).catch(oops);
        }, true);
      };
      tools.appendChild(wipe);
    }
  }
  if (!runs.length) { host.appendChild(el('p', 'note', 'No backup sent yet.')); return; }
  host.appendChild(listTable(['When', 'Node', 'File', 'Size', 'Where it went', 'Who', ''], runs, function (r) {
    var tr = el('tr');
    tr.appendChild(el('td', 'muted', new Date(r.at * 1000).toLocaleString(numLang())));
    tr.appendChild(noI18n(el('td', '', r.node || '')));
    var f = el('td', 'mono small');
    f.appendChild(noI18n(el('span', '', r.file || '–')));
    if (r.file && !r.encrypted) { f.appendChild(document.createTextNode(' ')); f.appendChild(el('span', 'tag bad', 'not locked')); }
    tr.appendChild(f);
    tr.appendChild(el('td', 'num', r.bytes ? rbBytes(r.bytes) : '–'));
    var w = el('td');
    if (r.error) w.appendChild(el('span', 'small bad', r.error));
    (r.targets || []).forEach(function (x) {
      var s = el('span', 'tag namepill ' + (x.ok ? 'ok' : 'crit'), (x.ok ? '✓ ' : '✗ ') + x.name);
      s.title = x.ok ? (x.secs + ' s' + ((x.taken_away || []).length ? ' · taken away: ' + x.taken_away.join(', ') : '')) : (x.error || '');
      noI18n(s);
      w.appendChild(s);
      w.appendChild(document.createTextNode(' '));
    });
    tr.appendChild(w);
    tr.appendChild(noI18n(el('td', 'muted small', r.by || '')));
    var a = el('td', 'rowtools');
    var del = iconButton('trash', 'Take it off the list');
    del.onclick = function () {
      api('rbackup', {action: 'runs_forget', at: r.at, file: r.file || ''}).then(function (d) { toast(d.message, 'good'); RB.runs = d.runs || []; drawRbRuns(); }).catch(oops);
    };
    a.appendChild(del);
    tr.appendChild(a);
    return tr;
  }, 'plain'));
}
function rbPickKind() {
  modal('Where should the backups go?', function (body) {
    var g = el('div', 'rb-kinds');
    (RB.kinds || []).forEach(function (k) {
      var b = el('button', 'rb-kind-tile');
      b.type = 'button';
      var ic = el('span', 'rb-kind k-' + k.kind);
      ic.innerHTML = iconMarkup(RB_KIND_ICON[k.kind] || 'db', 20);
      b.appendChild(ic);
      b.appendChild(el('b', '', k.words));
      b.appendChild(el('span', 'small muted', RB_KIND_SAYS[k.kind] || ''));
      var need = {sftp: ['ssh', 'sftp'], scp: ['ssh', 'scp'], smb: ['smbclient']}[k.kind] || [];
      var missing = need.filter(function (x) { return !RB.tools[x]; });
      if (missing.length) {
        b.appendChild(el('span', 'small bad', 'missing here: ' + missing.join(', ') + (k.kind === 'smb' ? ' (dnf install samba-client)' : ' (dnf install openssh-clients)')));
      }
      b.onclick = function () { closeModal(); setTimeout(function () { rbEdit({kind: k.kind, enabled: 1, port: k.port}); }, 30); };
      if (hsOn('backup:ftp') && k.kind === 'ftp') { b.disabled = true; b.appendChild(el('span', 'small hs-note', 'not under high security')); }
      g.appendChild(b);
    });
    body.appendChild(g);
  }, [{words: 'Close', close: true}], 'medium');
}
/* The editor of one destination. A new one is saved before a key or a
   certificate is made for it - they are kept with it. */
function rbEdit(t) {
  t = t || {};
  var isNew = !t.id;
  var cur = {id: t.id};
  modal(isNew ? 'A new destination: ' + (t.kind_words || (RB.kinds.filter(function (k) { return k.kind === t.kind; })[0] || {}).words || t.kind) : t.name, function (body) {
    body.classList.add('rb-ed');
    var f = {};
    var sec = function (title, says) {
      var s = el('div', 'rb-sec');
      s.appendChild(el('h4', '', title));
      if (says) s.appendChild(el('p', 'small muted', says));
      var g = el('div', 'formgrid');
      s.appendChild(g);
      body.appendChild(s);
      return g;
    };
    var g0 = sec('What it is called', '');
    f.name = textBox(t.name || '', 'NAS in the server room');
    g0.appendChild(formField('Name', f.name));
    f.enabled = switchBox(t.enabled === undefined ? 1 : t.enabled);
    g0.appendChild(formField('Backups go here', f.enabled));
    var g1 = sec('Where', t.kind === 'folder' ? 'A whole path on this machine; plexmon (root) writes into it.' : '');
    if (t.kind === 'folder') {
      f.path = textBox(t.path || '', '/mnt/backup/plexmon', 'mono');
      var fp = formField('The folder', f.path);
      fp.classList.add('wide');
      g1.appendChild(fp);
    } else {
      f.host = textBox(t.host || '', 'backup.example.cz', 'mono');
      g1.appendChild(formField('The server', f.host));
      f.port = textBox(t.port || '', String({ftp: 21, ftps: t.implicit ? 990 : 21, sftp: 22, scp: 22, smb: 445}[t.kind] || ''), 'mono');
      g1.appendChild(formField('Port', f.port));
      if (t.kind === 'smb') {
        f.share = textBox(t.share || '', 'backup', 'mono');
        g1.appendChild(formField('The share', f.share));
        f.path = textBox(t.path || '', 'plexmon\\nightly', 'mono');
        g1.appendChild(formField('The folder in it', f.path, 'Made if it is not there.'));
      } else {
        f.path = textBox(t.path || '', t.kind === 'ftp' || t.kind === 'ftps' ? 'plexmon/backups' : '/home/backup/plexmon', 'mono');
        g1.appendChild(formField('The folder there', f.path, 'Made if it is not there. Without / at the start: under the account’s home.'));
      }
    }
    if (t.kind !== 'folder') {
      var g2 = sec('Signing in', t.kind === 'sftp' || t.kind === 'scp' ? 'A key (below) or a password - or both.' : '');
      f.user = textBox(t.user || '', t.kind === 'smb' ? 'backup' : 'plexmon-backup', 'mono');
      g2.appendChild(formField('Account', f.user));
      f.password = maskBox(t.password || '', t.has_password ? '' : '(none)', 'mono');
      g2.appendChild(formField('Password', f.password, t.has_password ? 'Kept, sealed. Type a new one to change it.' : ''));
      if (t.kind === 'smb') {
        f.domain = textBox(t.domain || '', 'CORP', 'mono');
        g2.appendChild(formField('Domain (workgroup)', f.domain));
        f.smb_encrypt = switchBox(t.smb_encrypt === undefined ? 1 : t.smb_encrypt);
        if (hsOn('backup:smb') && f.smb_encrypt._get()) f.smb_encrypt._input.disabled = true;
        g2.appendChild(formField('SMB3 encryption required', f.smb_encrypt, 'Everything on the wire encrypted; a server that cannot is refused.'));
      }
      if (t.kind === 'ftp' || t.kind === 'ftps') {
        f.passive = switchBox(t.passive === undefined ? 1 : t.passive);
        g2.appendChild(formField('Passive mode', f.passive, 'The usual one, through firewalls and NAT.'));
      }
    }
    if (t.kind === 'ftps') rbFtpsBlock(body, t, f, cur);
    if (t.kind === 'sftp' || t.kind === 'scp') rbSshBlock(body, t, f, cur);
    var out = el('div', 'rb-try-out');
    out.id = 'rb-try-out';
    body.appendChild(out);
    rbEdit.form = f;
  }, [
    {words: 'Close', close: true},
    {words: 'Try it', icon: 'play', go: function (b) { rbTry(cur.id ? rbTarget(cur.id) || t : t, rbFormData(t.kind), b, true); }},
    {words: 'Save', cls: 'primary', icon: 'save', go: function (b) {
      busy(b, true);
      rbSaveForm(t.kind, cur).then(function () { busy(b, false); toast('saved', 'good'); closeModal(); loadRbackup(); })
        .catch(function (e) { busy(b, false); oops(e); });
    }}], 'large');
}
function rbTarget(id) { return (RB.targets || []).filter(function (x) { return x.id === id; })[0]; }
function rbFormData(kind) {
  var f = rbEdit.form || {};
  var v = function (k) { return f[k] ? (f[k]._get ? f[k]._get() : f[k].value) : undefined; };
  var d = {kind: kind};
  ['name', 'enabled', 'host', 'port', 'path', 'user', 'password', 'share', 'domain', 'smb_encrypt', 'passive', 'implicit', 'tls_check', 'ca', 'pin', 'ssh_cert'].forEach(function (k) {
    var x = v(k);
    if (x !== undefined) d[k] = typeof x === 'string' ? x.trim() : x;
  });
  if (f.password) d.password = f.password.value;
  return d;
}
function rbSaveForm(kind, cur) {
  return api('rbackup', {action: 'save', id: cur.id || '', target: rbFormData(kind)}).then(function (x) {
    cur.id = x.target.id;
    var i = (RB.targets || []).map(function (t) { return t.id; }).indexOf(x.target.id);
    if (i >= 0) RB.targets[i] = x.target; else RB.targets.push(x.target);
    return x.target;
  });
}
function rbNeedSaved(kind, cur) {
  return cur.id ? Promise.resolve(rbTarget(cur.id)) : rbSaveForm(kind, cur);
}
function rbKeyBox(text) {
  var w = el('div', 'rb-keybox');
  var pre = noI18n(el('pre', 'mono', text));
  w.appendChild(pre);
  var c = cmdButton('Copy', 'copy', 'small');
  c.onclick = function () { copyText(text, c, 'Copied'); };
  w.appendChild(c);
  return w;
}
/* The key and the certificates, as isemon has them (1.0.109): one box each,
   a line saying what it is, the same row of buttons always there (Make,
   Use my own, Download, Forget - the last two greyed until there is one),
   Try it at the end of the row, and below it the half to copy - a click on
   it copies it. */
function rbConfirm(title, words, yes, go, danger) {
  /* Over the editor, which stays as it is underneath. */
  modalOver(function () { confirmThen(title, words, yes, go, danger); });
}
function rbCopyme(text, said) {
  var code = noI18n(el('code', 'copyme', text));
  explain(code, 'Click to copy.');
  code.onclick = function () { copyText(text, code, said || 'Copied'); };
  return code;
}
function rbPanel(note) {
  var box = el('div', 'subpanel rb-panel');
  var p = el('p', 'note');
  p.innerHTML = note;
  box.appendChild(p);
  var row = el('div', 'btnrow');
  box.appendChild(row);
  var below = el('div', 'rb-below');
  box.appendChild(below);
  return {box: box, row: row, below: below};
}
function rbTryButton(t, cur) {
  var b = cmdButton('Try reaching it', 'pulse', 'primary');
  explain(b, 'Sign in, write a small file, find it in the list and take it away again - with what is typed above, saved or not.');
  b.onclick = function () { rbTry(cur.id ? (rbTarget(cur.id) || t) : t, rbFormData(t.kind), b, true); };
  return b;
}
function rbSshBlock(body, t, f, cur) {
  var s = el('div', 'rb-sec');
  s.appendChild(el('h4', '', 'The SSH key'));
  var P = rbPanel('A key is better than a password: made here, it never leaves this machine except when you download it, and the server holds only its <b>public</b> half - in <code>~/.ssh/authorized_keys</code> of the account.');
  if (!RB.tools['ssh-keygen']) {
    var miss = el('p', 'note');
    miss.style.color = 'var(--warn)';
    miss.textContent = 'ssh-keygen is not on this machine, so a key cannot be made or read here: dnf install openssh-clients. A password still works.';
    P.box.insertBefore(miss, P.row);
  }
  var mk = cmdButton('Make a key', 'key', '');
  explain(mk, 'Make a fresh ed25519 pair here. The private half stays on this machine, sealed; the public half is shown below for you to put on the server. Any key already kept is replaced.');
  var own = cmdButton('Use my own', 'upload', '');
  explain(own, 'Use a private key you already have. Paste it whole, with its BEGIN and END lines; its public half is worked out here.');
  var dl = cmdButton('Download it', 'download', '');
  explain(dl, 'The private half as a file, for keeping somewhere safe. That it was downloaded, and by whom, is written into Events.');
  var fg = cmdButton('Forget it', 'forget', 'danger');
  explain(fg, 'Delete the private key from this machine. Unless the server takes a password instead, nothing here reaches it until a new key is made and put on it.');
  [mk, own, dl, fg].forEach(function (b) { P.row.appendChild(b); });
  P.row.appendChild(el('span', 'spacer'));
  P.row.appendChild(rbTryButton(t, cur));
  var state = {pub: t.pub, has: !!t.has_key};
  var paint = function () {
    clear(P.below);
    dl.disabled = !state.has;
    fg.disabled = !state.has;
    mk.disabled = own.disabled = !RB.tools['ssh-keygen'];
    if (!state.has || !state.pub) {
      P.below.appendChild(el('p', 'btnhelp', 'No key yet — make one, or let it sign in with a password.'));
      return;
    }
    P.below.appendChild(el('p', 'btnhelp', 'The public half. Click it to copy.'));
    P.below.appendChild(rbCopyme(state.pub, 'the public key is on the clipboard'));
    var u = (f.user && f.user.value) || 'the account';
    P.below.appendChild(el('p', 'btnhelp', 'On the server, as ' + u + ':'));
    P.below.appendChild(rbCopyme("mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '" + state.pub + "' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"));
  };
  mk.onclick = function () {
    rbConfirm('Make a key for ' + ((f.name && f.name.value) || t.name || 'this destination'),
      'A fresh ed25519 pair is made here. The private half stays on this machine and the public half is shown for you to put on the server.' + (state.has ? '<br><br><b>The key kept now is replaced</b>, so until the new public half is on the server, backups cannot sign in there.' : ''),
      'Make one', function () {
        rbNeedSaved(t.kind, cur).then(function () { return api('rbackup', {action: 'keygen', id: cur.id}); })
          .then(function (x) { state.pub = x.pub; state.has = true; paint(); toast('a new key was made — put the public half on the server', 'good'); })
          .catch(oops);
      }, false);
  };
  own.onclick = function () {
    modalOver(function () {
      modal('Use my own key', function (body2) {
        var p = el('p', 'note');
        p.innerHTML = 'An OpenSSH private key, whole, including its <code>BEGIN</code> and <code>END</code> lines. It is kept in this machine’s database, sealed, and never shown back. Its public half is worked out here, so you do not have to hand that over as well.';
        body2.appendChild(p);
        var area = el('textarea', 'mono');
        area.rows = 11;
        area.id = 'rb-paste-area';
        area.spellcheck = false;
        area.placeholder = '-----BEGIN OPENSSH PRIVATE KEY-----';
        body2.appendChild(area);
        var pp = maskBox('', '', 'mono');
        pp.id = 'rb-paste-pp';
        body2.appendChild(formField('Its passphrase, if it has one', pp));
        setTimeout(function () { area.focus(); }, 60);
      }, [{words: 'Cancel', close: true},
          {words: 'Keep this key', cls: 'primary', close: true, go: function () {
            var key = $('rb-paste-area').value, pass = $('rb-paste-pp').value;
            rbNeedSaved(t.kind, cur).then(function () { return api('rbackup', {action: 'key_paste', id: cur.id, key: key, passphrase: pass}); })
              .then(function (x) { state.pub = x.pub; state.has = true; paint(); toast('the key was stored', 'good'); })
              .catch(oops);
          }}]);
    });
  };
  dl.onclick = function () {
    rbConfirm('Download the private key', 'The private half leaves this machine as a file. Anybody holding it can sign in to the server as <code>' + htmlText((f.user && f.user.value) || 'that account') + '</code>. That it was downloaded, and by whom, is written into Events.', 'Download it', function () {
      api('rbackup', {action: 'key_download', id: cur.id}).then(function (x) {
        var a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([x.pem], {type: 'application/octet-stream'}));
        a.download = x.file;
        document.body.appendChild(a); a.click(); a.remove();
      }).catch(oops);
    }, false);
  };
  fg.onclick = function () {
    rbConfirm('Forget the key', 'The private key is deleted from this machine. Unless the server takes a password instead, backups will not reach it until a new key is made and put on it.', 'Forget it', function () {
      api('rbackup', {action: 'key_forget', id: cur.id}).then(function () { state.has = false; state.pub = ''; paint(); toast('the key is gone'); }).catch(oops);
    });
  };
  paint();
  s.appendChild(P.box);
  f.ssh_cert = el('textarea', 'mono rb-area small');
  f.ssh_cert.value = t.ssh_cert || '';
  f.ssh_cert.placeholder = 'ssh-ed25519-cert-v01@openssh.com AAAA…';
  var cf = formField('SSH certificate of the key (only if the server wants one)', f.ssh_cert);
  cf.style.marginTop = '12px';
  s.appendChild(cf);
  body.appendChild(s);
  var h = el('div', 'rb-sec');
  h.appendChild(el('h4', '', 'The server’s host key'));
  var H = rbPanel('The key the server proves itself with. Trusted once, after comparing its fingerprint with the server’s own (<code>ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</code> on it); a server answering with any other is refused - somebody in between, or the server reinstalled.');
  var hf = cmdButton('Forget it', 'forget', 'danger');
  H.row.appendChild(hf);
  H.row.appendChild(el('span', 'spacer'));
  H.row.appendChild(rbTryButton(t, cur));
  var paintHost = function (fps) {
    clear(H.below);
    hf.disabled = !(fps && fps.length);
    if (fps && fps.length) {
      H.below.appendChild(el('p', 'btnhelp ro', 'Trusted. Click to copy.'));
      fps.forEach(function (x) { H.below.appendChild(rbCopyme(x)); });
    } else {
      H.below.appendChild(el('p', 'btnhelp', 'Not trusted yet — Try reaching it shows the fingerprint to trust.'));
    }
  };
  hf.onclick = function () {
    rbConfirm('Forget the host key', 'Backups stop going there until it is trusted again.', 'Forget it', function () {
      api('rbackup', {action: 'hostkey_forget', id: cur.id}).then(function () { paintHost([]); }).catch(oops);
    });
  };
  paintHost(t.hostkey_fp);
  rbSshBlock.paintHost = paintHost;
  h.appendChild(H.box);
  body.appendChild(h);
}
function rbFtpsBlock(body, t, f, cur) {
  var s = el('div', 'rb-sec');
  s.appendChild(el('h4', '', 'TLS'));
  var g = el('div', 'formgrid');
  f.implicit = selectBox([['0', 'explicit - AUTH TLS on port 21'], ['1', 'implicit - TLS from the start, port 990']], t.implicit ? '1' : '0');
  f.implicit._get = function () { return f.implicit.value === '1' ? 1 : 0; };
  f.implicit.onchange = function () { if (f.port && (!f.port.value || f.port.value === '21' || f.port.value === '990')) f.port.value = f.implicit.value === '1' ? '990' : '21'; };
  g.appendChild(formField('TLS', f.implicit));
  f.tls_check = selectBox([['system', 'against the authorities this machine trusts'], ['ca', 'against an authority of my own'], ['pin', 'pinned: exactly this certificate'], ['none', 'not checked (not safe)']], t.tls_check || 'system');
  hsOptions(f.tls_check, ['none'], 'backup:ftps');
  g.appendChild(formField('The server’s certificate is checked', f.tls_check));
  s.appendChild(g);
  f.ca = el('textarea', 'mono rb-area small');
  f.ca.value = t.ca || '';
  f.ca.placeholder = '-----BEGIN CERTIFICATE-----';
  var caf = formField('The authority (PEM)', f.ca, t.ca_info ? 'Now: ' + (t.ca_info.subject || '') + ' · ' + rbWhen(t.ca_info.not_after) : 'The certificate of the authority that signed the server’s.');
  s.appendChild(caf);
  f.pin = textBox(t.pin || '', '64 hex digits - Try reaching it shows the server’s', 'mono');
  var pinf = formField('Pinned SHA-256 fingerprint', f.pin);
  s.appendChild(pinf);
  var show = function () { caf.hidden = f.tls_check.value !== 'ca'; pinf.hidden = f.tls_check.value !== 'pin'; };
  f.tls_check.onchange = show;
  show();
  rbFtpsBlock.pin = f.pin;
  rbFtpsBlock.check = f.tls_check;
  body.appendChild(s);
  var cc = el('div', 'rb-sec');
  cc.appendChild(el('h4', '', 'Client certificate'));
  var P = rbPanel('Only for a server that asks for one. Made here, its key never leaves this machine except when you download it; the server gets the <b>certificate</b> - give it to its administrator.');
  var mk = cmdButton('Make one', 'key', '');
  explain(mk, 'A key and a certificate for it, made here (EC P-256, five years). Any one kept is replaced.');
  var own = cmdButton('Use my own', 'upload', '');
  explain(own, 'A certificate and its key you have: PEM (both, in any order) or a PFX / PKCS#12 file with its password.');
  var dl = cmdButton('Download it', 'download', '');
  explain(dl, 'The certificate as a file (not its key), for the server’s administrator.');
  var fg = cmdButton('Forget it', 'forget', 'danger');
  [mk, own, dl, fg].forEach(function (b) { P.row.appendChild(b); });
  P.row.appendChild(el('span', 'spacer'));
  P.row.appendChild(rbTryButton(t, cur));
  var state = {cert: t.client_cert, info: t.client_cert_info};
  var paint = function () {
    clear(P.below);
    dl.disabled = fg.disabled = !state.cert;
    if (!state.cert) {
      P.below.appendChild(el('p', 'btnhelp', 'No client certificate — most servers want none.'));
      return;
    }
    if (state.info) P.below.appendChild(noI18n(el('p', 'btnhelp ro', (state.info.subject || '') + ' · ' + rbWhen(state.info.not_after))));
    P.below.appendChild(el('p', 'btnhelp', 'The certificate. Click it to copy.'));
    P.below.appendChild(rbCopyme(state.cert, 'the certificate is on the clipboard'));
  };
  mk.onclick = function () {
    rbConfirm('Make a client certificate', 'A key and a certificate for it are made here; the key stays on this machine.' + (state.cert ? '<br><br><b>The one kept now is replaced</b>: the server has to be given the new certificate.' : ''), 'Make one', function () {
      rbNeedSaved(t.kind, cur).then(function () { return api('rbackup', {action: 'cert_make', id: cur.id, cn: 'plexmon-backup-' + (RB.node || '')}); })
        .then(function (x) { state.cert = x.cert; state.info = x.info; paint(); toast('made — give the certificate to the server', 'good'); }).catch(oops);
    }, false);
  };
  own.onclick = function () {
    modalOver(function () {
      modal('Use my own client certificate', function (body2) {
        var p = el('p', 'note');
        p.innerHTML = 'The certificate and its private key, as PEM - pasted, in any order - or a <code>.pfx</code> / <code>.p12</code> file. The key is kept sealed and never shown back.';
        body2.appendChild(p);
        var area = el('textarea', 'mono');
        area.rows = 11;
        area.id = 'rb-cert-area';
        area.spellcheck = false;
        area.placeholder = '-----BEGIN CERTIFICATE-----\n…\n-----BEGIN PRIVATE KEY-----';
        body2.appendChild(area);
        var file = el('input');
        file.type = 'file';
        file.accept = '.pem,.crt,.key,.pfx,.p12';
        file.onchange = function () {
          var fl = file.files[0];
          if (!fl) return;
          if (/\.(pfx|p12)$/i.test(fl.name)) fl.arrayBuffer().then(function (buf) {
            var b = new Uint8Array(buf), s2 = '';
            for (var i = 0; i < b.length; i++) s2 += String.fromCharCode(b[i]);
            area.value = btoa(s2);
          });
          else fl.text().then(function (x) { area.value = (area.value ? area.value + '\n' : '') + x; });
        };
        body2.appendChild(formField('Or a file', file));
        var pw = maskBox('', '', 'mono');
        pw.id = 'rb-cert-pw';
        body2.appendChild(formField('The password of the key or of the PFX, if it has one', pw));
      }, [{words: 'Cancel', close: true},
          {words: 'Keep this certificate', cls: 'primary', close: true, go: function () {
            var text = $('rb-cert-area').value, pass = $('rb-cert-pw').value;
            rbNeedSaved(t.kind, cur).then(function () { return api('rbackup', {action: 'cert_given', id: cur.id, text: text, password: pass}); })
              .then(function (x) { state.cert = x.cert; state.info = x.info; paint(); toast('the certificate was stored', 'good'); }).catch(oops);
          }}]);
    });
  };
  dl.onclick = function () {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([state.cert], {type: 'application/x-pem-file'}));
    a.download = 'plexmon-backup-client.crt';
    document.body.appendChild(a); a.click(); a.remove();
  };
  fg.onclick = function () {
    rbConfirm('Forget the client certificate', 'Its key is deleted from this machine. A server that asks for one will refuse backups until another is made and given to it.', 'Forget it', function () {
      api('rbackup', {action: 'cert_forget', id: cur.id}).then(function () { state.cert = ''; state.info = null; paint(); }).catch(oops);
    });
  };
  paint();
  cc.appendChild(P.box);
  body.appendChild(cc);
}
function rbTry(t, data, button, inEditor) {
  if (button) busy(button, true);
  var ask = {action: 'try', id: t.id || ''};
  if (data) ask.target = data;
  api('rbackup', ask).then(function (x) {
    if (button) busy(button, false);
    var show = function (host) {
      clear(host);
      if (x.hostkey_needed) {
        host.appendChild(el('p', 'note warn', 'The server shows this host key. Compare it with the server’s own fingerprint before you trust it:'));
        (x.fingerprints || []).forEach(function (fp) { host.appendChild(noI18n(el('div', 'mono small', fp))); });
        var tb = cmdButton('Trust it', 'check', 'small primary');
        tb.onclick = function () {
          var save = (inEditor && rbEdit.form) ? rbSaveForm(t.kind, {id: t.id}) : Promise.resolve(t);
          save.then(function (saved) {
            return api('rbackup', {action: 'hostkey_trust', id: saved.id, hostkey: x.hostkey}).then(function (y) {
              toast('trusted', 'good');
              if (rbSshBlock.paintHost) rbSshBlock.paintHost(y.fingerprints);
              t.id = saved.id;
              rbTry(saved, inEditor ? rbFormData(t.kind) : null, button, inEditor);
            });
          }).catch(oops);
        };
        host.appendChild(tb);
        return;
      }
      var box = el('div', 'note ' + (x.ok ? 'good' : 'bad'));
      box.appendChild(el('b', '', x.ok ? 'It works (' + x.secs + ' s)' : 'It does not work'));
      var ul = el('ul', 'rb-steps');
      (x.steps || []).forEach(function (s) { ul.appendChild(el('li', '', '✓ ' + s)); });
      if (x.error) ul.appendChild(el('li', 'bad', '✗ ' + x.error));
      box.appendChild(ul);
      var sc = x.server_cert;
      if (sc && sc.fingerprint) {
        var p = el('div', 'small');
        p.appendChild(el('span', '', 'The server’s certificate: '));
        p.appendChild(noI18n(el('span', 'mono', ((sc.cert || {}).subject || '') + ' · SHA-256 ' + sc.fingerprint)));
        box.appendChild(p);
        if (inEditor && rbFtpsBlock.pin) {
          var pin = cmdButton('Pin this certificate', 'pin', 'small');
          pin.onclick = function () { rbFtpsBlock.check.value = 'pin'; rbFtpsBlock.check.onchange(); rbFtpsBlock.pin.value = sc.fingerprint; toast('pinned - Save to keep it', 'good'); };
          box.appendChild(pin);
        }
      }
      host.appendChild(box);
      if (inEditor) host.appendChild(hideBar(function () { clear(host); }));
    };
    var host = inEditor ? $('rb-try-out') : null;
    if (host) show(host);
    else modal('Trying ' + (t.name || ''), function (body) { var h = el('div'); body.appendChild(h); show(h); }, [{words: 'Close', close: true}], 'medium');
    if (!inEditor) loadRbackup();
  }).catch(function (e) { if (button) busy(button, false); oops(e); });
}
function rbListThere(t) {
  modal('Backups at ' + t.name, function (body) {
    var host = el('div');
    host.appendChild(el('p', 'center muted', 'Asking…'));
    body.appendChild(host);
    api('rbackup', {action: 'list', id: t.id}).then(function (x) {
      clear(host);
      var files = x.files || [];
      if (!files.length) { host.appendChild(el('p', 'note', 'No backup of plexmon there.')); return; }
      host.appendChild(listTable(['Made', 'Node', 'Size', '', ''], files, function (fl) {
        var tr = el('tr');
        var w = el('td');
        w.appendChild(el('div', '', new Date(fl.when * 1000).toLocaleString(numLang())));
        w.appendChild(noI18n(el('div', 'mono small muted', fl.name)));
        tr.appendChild(w);
        var n = el('td');
        n.appendChild(noI18n(el('span', '', fl.node)));
        if (fl.mine) { n.appendChild(document.createTextNode(' ')); n.appendChild(el('span', 'tag info', 'this node')); }
        tr.appendChild(n);
        var sz = el('td', 'num');
        sz.textContent = rbBytes(fl.bytes);
        if (!fl.encrypted) { sz.appendChild(document.createTextNode(' ')); sz.appendChild(el('span', 'tag bad', 'not locked')); }
        tr.appendChild(sz);
        var a = el('td', 'rowtools');
        var dl = iconButton('download', 'Download it');
        dl.onclick = function () { window.location.href = '?api=rbackup-download&id=' + encodeURIComponent(t.id) + '&name=' + encodeURIComponent(fl.name); };
        a.appendChild(dl);
        var pb = iconButton('undo', 'Put this back…');
        pb.onclick = function () { rbPutBackConfirm({id: t.id, name: fl.name, encrypted: fl.encrypted, when: fl.when, node: fl.node}); };
        a.appendChild(pb);
        var del = iconButton('trash', 'Delete it there');
        del.onclick = function () {
          confirmThen('Delete ' + htmlText(fl.name) + '?', 'It is deleted at ' + htmlText(t.name) + ', with its checksum and its RESTORE.md.', 'Delete it', function () {
            api('rbackup', {action: 'delete_file', id: t.id, name: fl.name}).then(function () { toast('deleted', 'good'); rbListThere(t); }).catch(oops);
          });
        };
        a.appendChild(del);
        tr.appendChild(a);
        return tr;
      }, 'plain'));
    }).catch(function (e) { clear(host); host.appendChild(el('p', 'note bad', String(e.message || e))); });
  }, [{words: 'Close', close: true}], 'large');
}
function rbPutBackConfirm(what) {
  modal('Put this backup back?', function (body) {
    var facts = [];
    if (what.when) facts.push(['Made', new Date(what.when * 1000).toLocaleString(numLang())]);
    if (what.node) facts.push(['On', what.node]);
    if (what.manifest) {
      var m = what.manifest;
      facts = [['Made', m.made_words], ['On', m.node + ' (' + (m.host || '') + ')'], ['plexmon', m.plexmon], ['Plex\'s database', m.plex_db ? 'in it' : 'not in it']];
    }
    if (facts.length) body.appendChild(peekFacts(facts));
    body.appendChild(el('p', 'note warn', 'plexmon\'s database is replaced with what the backup holds - accounts, settings, certificates, events, and who is signed in: you sign in again afterwards, with an account the backup has. Its key and the dashboard\'s configuration come back too; plexmon starts again. A copy of what is here now is made first (Settings → The database). Plex Media Server\'s database in it is put back apart, in The database.'));
    var pw = maskBox('', what.encrypted === false ? '(not locked)' : 'the password of the backups - empty: the one set here', 'mono');
    body.appendChild(formField('Its password', pw, 'Only when it was locked with another password than the one set here now.'));
    rbPutBackConfirm.pw = pw;
  }, [{words: 'No, leave it', close: true},
      {words: 'Put it back', cls: 'danger', icon: 'undo', close: true, go: function () {
        var ask = {action: 'restore', password: rbPutBackConfirm.pw.value};
        if (what.upload) ask.upload = what.upload; else { ask.id = what.id; ask.name = what.name; }
        api('rbackup', ask).then(function (x) {
          toast(x.message, 'good');
          rbRestoreWatch();
        }).catch(oops);
      }}], 'medium');
}
function rbRestoreWatch(title) {
  modal(title || 'Putting the backup back', function (body) {
    var bar = el('div', 'going wide');
    bar.innerHTML = '<div class="bar"><span id="rbr-fill"></span></div><div class="says" id="rbr-says"></div>';
    body.appendChild(bar);
  }, [{words: 'Close', close: true}], 'medium');
  var step = function () {
    api('db', {action: 'job'}).then(function (d) {
      var j = d.job || {};
      if ($('rbr-fill')) $('rbr-fill').style.width = (j.whole ? Math.round(100 * j.done / j.whole) : 5) + '%';
      if ($('rbr-says')) $('rbr-says').textContent = (j.what || '') + (j.says ? ' — ' + j.says : '');
      if (j.running) { setTimeout(step, 1000); return; }
      if (!j.what && !j.result && !j.error) { setTimeout(step, 1000); return; }
      if (j.error) { if ($('rbr-says')) $('rbr-says').textContent = j.error; toast('putting it back failed: ' + j.error, 'bad'); return; }
      var r = j.result || {};
      if ($('rbr-says')) {
        var said = $('rbr-says');
        clear(said);
        said.appendChild(el('div', '', 'Put back.'));
        (r.said || []).concat(r.key ? [r.key] : []).forEach(function (x) { said.appendChild(el('div', 'small', x)); });
        if (r.safety) said.appendChild(el('div', 'small muted', 'What was here is kept as ' + r.safety));
      }
      toast(title ? 'the database is back' : 'the backup is back', 'good');
      if (title) setTimeout(function () { location.reload(); }, 4000);
    }).catch(function (e) {
      /* Who was signed in is what the backup says now: the sign-in page. */
      if (/signed in|401/i.test(String(e && e.message || e))) {
        if ($('rbr-says')) $('rbr-says').textContent = 'Put back. Sign in again - with an account the backup has.';
        setTimeout(function () { location.reload(); }, 3500);
        return;
      }
      setTimeout(step, 2000);
    });
  };
  setTimeout(step, 500);
}
function rbPutBackFile() {
  var file = el('input');
  file.type = 'file';
  file.accept = '.enc,.gz';
  file.onchange = function () {
    var fl = file.files[0];
    if (!fl) return;
    toast('sending ' + fl.name + '…', 'good');
    bigFetch('?api=rbackup-upload', fl).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok || d.ok === false) throw new Error(d.error || ('the server said ' + r.status));
        return d;
      });
    }).then(function (d) {
      if (d.said) toast(d.said, 'bad');
      rbPutBackConfirm({upload: d.upload, encrypted: d.locked, manifest: d.manifest});
    }).catch(oops);
  };
  file.click();
}

/* A button busy with something slow, the seconds counted beside its words
   (1.0.109). Returns the function that ends it. */
function busyCount(button) {
  busy(button, true);
  var began = Date.now();
  var tag = el('span', 'busy-secs', '');
  button.appendChild(tag);
  var tick = setInterval(function () { tag.textContent = ' · ' + Math.round((Date.now() - began) / 1000) + ' s'; }, 500);
  return function () { clearInterval(tick); tag.remove(); busy(button, false); };
}


/* ---- Settings → The database (plexmon 2.2.0) ------------------------------------
   plexmon's own SQLite, and Plex Media Server's library database: copies taken
   while Plex runs, checked on the copy, put back with Plex stopped. */
var DBV = {d: null, poll: null};
function loadDb() {
  api('db').then(function (d) { DBV.d = d; drawDb(); if (d.job && d.job.running) dbWatch(); }).catch(oops);
}
function dbWatch() {
  if (DBV.poll) return;
  var step = function () {
    api('db', {action: 'job'}).then(function (x) {
      var j = x.job || {};
      var g = $('db-going');
      if (g) {
        g.hidden = !j.running;
        var f = g.querySelector('span');
        if (f) f.style.width = (j.whole ? Math.round(100 * j.done / j.whole) : 6) + '%';
        var sy = g.querySelector('.says');
        if (sy) sy.textContent = (j.what ? txT(j.what) + ' - ' : '') + txT(j.says || '');
      }
      if (j.running) { DBV.poll = setTimeout(step, 1000); return; }
      DBV.poll = null;
      if (j.error) toast(j.error, 'bad');
      else if (j.result) toast(txT(j.what || 'done') + ': ' + txT('done'), 'good');
      if (TAB === 'settings' && PANE === 'db') loadDb();
    }).catch(function () { DBV.poll = setTimeout(step, 3000); });
  };
  step();
}
function dbMetric(label, value, cls, sub, words) {
  var m = el('div', 'card metric ' + (cls || ''));
  m.appendChild(el('div', 'label', label));
  var v = el('div', 'value', value);
  m.appendChild(words ? v : noI18n(v));
  if (sub) m.appendChild(el('div', 'sub', sub));
  return m;
}
function dbCopies(rows, kind, actions) {
  if (!rows.length) return el('p', 'note', 'No copy yet.');
  return listTable(['Made', 'File', 'Size', ''], rows, function (c) {
    var tr = el('tr');
    tr.appendChild(el('td', '', ago(c.at)));
    tr.appendChild(noI18n(el('td', 'mono small', c.name)));
    tr.appendChild(el('td', 'num', bytes(c.bytes)));
    var a = el('td', 'rowtools');
    actions(c).forEach(function (b) { a.appendChild(b); });
    tr.appendChild(a);
    return tr;
  }, 'plain');
}
function dbDo(body, words) {
  return api('db', body).then(function (x) {
    if (x.job) { toast(txT(words || 'started'), 'good'); DBV.d.job = x.job; drawDb(); dbWatch(); return x; }
    toast(x.message || 'done', x.ok === false ? 'bad' : 'good');
    loadDb();
    return x;
  }).catch(oops);
}
function drawDb() {
  var d = DBV.d;
  if (!d) return;
  var own = d.own || {}, p = d.plex || {}, last = p.last || {};
  var running = d.job && d.job.running;
  /* The top: four numbers. */
  var top = $('db-top');
  clear(top);
  var row = el('div', 'grid top');
  var pbytes = (p.files || []).reduce(function (s, f) { return s + (f.bytes || 0); }, 0);
  row.appendChild(dbMetric('Plex\'s database', pbytes ? bytes(pbytes) : '–', '', p.running ? 'Plex is running' : 'Plex is stopped'));
  row.appendChild(dbMetric('Its last copy', last.at ? ago(last.at) : 'never', last.at ? ((last.check || {}).ok === false ? 'crit' : 'ok') : 'warn',
    last.at ? ((last.check || {}).ok === false ? 'checked: NOT whole' : ((last.check || {}).ok ? 'checked: whole' : 'not checked')) : '', true));
  row.appendChild(dbMetric('plexmon\'s database', bytes(own.bytes || 0), '', (own.copies || []).length + ' copies'));
  if (p.disk) row.appendChild(dbMetric('Free where the copies are', bytes(p.disk.free), p.disk.free < pbytes * 3 ? 'warn' : '', Math.round(100 * p.disk.free / p.disk.total) + ' %'));
  top.appendChild(row);
  var going = el('div', 'going wide');
  going.id = 'db-going';
  going.hidden = !running;
  going.innerHTML = '<div class="bar"><span></span></div><div class="says"></div>';
  top.appendChild(going);
  /* Plex Media Server's. */
  var ph = $('pdb-host');
  clear(ph);
  var tools = $('pdb-tools');
  clear(tools);
  var mk = cmdButton('Make a copy now', 'save', 'small primary');
  explain(mk, 'A copy of the library database (and its blobs) taken now while Plex runs - SQLite\'s own online backup, nothing in Plex\'s file is written - checked, packed and kept here.');
  mk.disabled = running;
  mk.onclick = function () { dbDo({action: 'plex-copy'}, 'the copy is being made'); };
  tools.appendChild(mk);
  ph.appendChild(netFacts([['Where', p.dir || '', true], ['Plex Media Server', p.running ? 'running' : 'stopped'],
    ['Checked with', p.plex_sqlite ? 'Plex SQLite (integrity_check)' : 'SQLite (quick_check)'], ['Copies kept in', p.copies_dir || '', true]]));
  if ((p.files || []).length) ph.appendChild(listTable(['File', 'Size', 'Changed'], p.files, function (f) {
    var tr = el('tr');
    tr.appendChild(noI18n(el('td', 'mono small', f.name)));
    tr.appendChild(el('td', 'num', bytes(f.bytes)));
    tr.appendChild(el('td', 'muted', ago(f.at)));
    return tr;
  }, 'plain'));
  else ph.appendChild(el('p', 'note bad', 'Plex Media Server\'s database is not where it is looked for.'));
  if (last.check && last.check.ok === false) ph.appendChild(el('p', 'note bad', 'The last copy is NOT whole: ' + (last.check.said || []).join('; ')));
  var pr = el('div', 'btnrow');
  var opt = cmdButton('Optimize it (Plex\'s own)', 'db', '');
  explain(opt, 'Plex Media Server\'s own Optimize Database (as in Plex: Troubleshooting) - asked of Plex, done by Plex. A copy first is wise.');
  opt.onclick = function () {
    confirmThen('Let Plex optimize its database?', htmlText('Plex rebuilds its database file to give the room back and tidy its indexes. While it does, the library may answer slowly. Make a copy first if there is none of today.'), 'Optimize', function () { dbDo({action: 'plex-optimize'}); }, false);
  };
  var cln = cmdButton('Clean the bundles (Plex\'s own)', 'broom', '');
  explain(cln, 'Plex Media Server\'s own Clean Bundles: metadata bundles of what is no longer in the library deleted by Plex.');
  cln.onclick = function () { dbDo({action: 'plex-clean'}); };
  pr.appendChild(opt); pr.appendChild(cln);
  ph.appendChild(pr);
  ph.appendChild(el('h4', '', 'Copies made here'));
  ph.appendChild(dbCopies(p.copies || [], 'plex', function (c) {
    var dl = iconButton('download', 'Download it');
    dl.onclick = function () { window.location.href = '?api=db-file&kind=plex&name=' + encodeURIComponent(c.name); };
    var pb = iconButton('undo', 'Put it back…');
    pb.disabled = running;
    pb.onclick = function () { plexPutBack(c.name, false); };
    var del = iconButton('trash', 'Delete it');
    del.onclick = function () { confirmThen('Delete ' + htmlText(c.name) + '?', htmlText('The copy is deleted from this server.'), 'Delete it', function () { dbDo({action: 'plex-forget', name: c.name}); }); };
    return [dl, pb, del];
  }));
  var pob = p.own_backups || [];
  ph.appendChild(el('h4', '', 'Plex\'s own backups'));
  ph.appendChild(el('p', 'note', 'Made by Plex itself every three days (in Plex: Settings → Scheduled Tasks → Back up database), beside its database. They can be put back too.'));
  if (!pob.length) ph.appendChild(el('p', 'note', 'None.'));
  else ph.appendChild(listTable(['Day', 'File', 'Size', ''], pob, function (c) {
    var tr = el('tr');
    tr.appendChild(noI18n(el('td', '', c.date)));
    tr.appendChild(noI18n(el('td', 'mono small', c.name)));
    tr.appendChild(el('td', 'num', bytes(c.bytes)));
    var a = el('td', 'rowtools');
    if (!c.blobs) {
      var pb = iconButton('undo', 'Put it back…');
      pb.disabled = running;
      pb.onclick = function () { plexPutBack(c.name, true); };
      a.appendChild(pb);
    }
    tr.appendChild(a);
    return tr;
  }, 'plain'));
  var tm = p.timers || [];
  if (tm.length) {
    ph.appendChild(el('h4', '', 'Backups of it by this server\'s own timers'));
    ph.appendChild(el('p', 'note', 'A timer of systemd on this server that backs Plex\'s database up by itself - left exactly as it is; it can be switched off here if plexmon\'s copies take its place.'));
    ph.appendChild(listTable(['Timer', 'State', 'Next', 'What it runs', ''], tm, function (t) {
      var tr = el('tr');
      tr.appendChild(noI18n(el('td', 'mono', t.unit)));
      var on = t.enabled === 'enabled';
      var c = el('td'); c.appendChild(el('span', 'tag ' + (on ? 'ok' : 'dim'), on ? 'on' : 'off')); tr.appendChild(c);
      tr.appendChild(noI18n(el('td', 'muted small', t.next || '–')));
      tr.appendChild(noI18n(el('td', 'mono small', (t.service || '').replace(/^\{\s*path=/, '').split(' ;')[0])));
      var a = el('td', 'rowtools');
      var b = cmdButton(on ? 'Switch it off' : 'Switch it on', 'power', 'small');
      b.onclick = function () {
        confirmThen((on ? 'Switch off ' : 'Switch on ') + htmlText(t.unit) + '?', htmlText(on ? 'Its backups stop; what it made stays where it is.' : 'It backs the database up again on its own schedule.'), on ? 'Switch it off' : 'Switch it on', function () { dbDo({action: 'timer', unit: t.unit, on: !on}); }, on);
      };
      a.appendChild(b);
      tr.appendChild(a);
      return tr;
    }, 'plain'));
  }
  /* plexmon's own. */
  var oh = $('odb-host');
  clear(oh);
  var ot = $('odb-tools');
  clear(ot);
  var mk2 = cmdButton('Make a copy now', 'save', 'small');
  mk2.onclick = function () { dbDo({action: 'own-copy'}); };
  ot.appendChild(mk2);
  oh.appendChild(netFacts([['File', own.where || '', true], ['Size', bytes(own.bytes || 0)], ['The key of its secrets', own.key || '', true], ['Copies kept in', own.dir || '', true]]));
  if ((own.tables || []).length) oh.appendChild(listTable(['Table', 'Rows'], own.tables, function (t) {
    var tr = el('tr');
    tr.appendChild(noI18n(el('td', 'mono', t.name)));
    tr.appendChild(el('td', 'num', num(t.rows)));
    return tr;
  }, 'plain'));
  var orow = el('div', 'btnrow');
  var ck = cmdButton('Check it', 'check', '');
  explain(ck, 'SQLite\'s integrity_check: the database read through.');
  ck.onclick = function () { dbDo({action: 'own-check'}); };
  var gb = cmdButton('Give the disk back', 'db', '');
  explain(gb, 'VACUUM: the room of what was deleted given back to the disk.');
  gb.onclick = function () { dbDo({action: 'own-optimize'}); };
  var td = cmdButton('Apply what is kept, now', 'broom', '');
  explain(td, 'The events older than "Events kept for" deleted now - as every hour by itself.');
  td.onclick = function () { dbDo({action: 'own-tidy'}); };
  var er = cmdButton('Erase every event…', 'forget', 'danger');
  er.onclick = function () {
    confirmThen('Erase every event?', htmlText('Every line of Events goes - sign-ins, changes, certificates. Accounts, settings and certificates are not touched. A copy of the database is made just before.'), 'Erase them', function () { dbDo({action: 'own-erase', what: 'events'}); });
  };
  [ck, gb, td, er].forEach(function (b) { orow.appendChild(b); });
  oh.appendChild(orow);
  oh.appendChild(el('h4', '', 'Copies'));
  oh.appendChild(dbCopies(own.copies || [], 'own', function (c) {
    var dl = iconButton('download', 'Download it');
    dl.onclick = function () { window.location.href = '?api=db-file&kind=own&name=' + encodeURIComponent(c.name); };
    var pb = iconButton('undo', 'Put it back…');
    pb.onclick = function () {
      confirmThen('Put ' + htmlText(c.name) + ' back?', htmlText('plexmon\'s database becomes this copy - accounts, settings, certificates, events as they were then; who is signed in too: you may have to sign in again. A copy of now is made first. plexmon starts again in a few seconds.'), 'Put it back', function () {
        api('db', {action: 'own-restore', name: c.name}).then(function (x) { toast(x.message, 'good'); setTimeout(function () { location.reload(); }, 6000); }).catch(oops);
      });
    };
    var del = iconButton('trash', 'Delete it');
    del.onclick = function () { confirmThen('Delete ' + htmlText(c.name) + '?', htmlText('The copy is deleted from this server.'), 'Delete it', function () { dbDo({action: 'own-forget', name: c.name}); }); };
    return [dl, pb, del];
  }));
  dressButtons(document.getElementById('pane-db'));
  explainWhatWasDrawn($('pane-db'));
}
function plexPutBack(name, own) {
  modal('Put Plex\'s database back?', function (body) {
    body.appendChild(peekFacts([['The copy', name], ['From', own ? 'Plex\'s own backups' : 'the copies made here']]));
    body.appendChild(el('p', 'note warn', 'Plex Media Server is stopped (whoever is watching too), its database files are set aside - kept, not deleted - the copy is put in their place, and Plex is started again. Everything since the copy was made is not in it: what was watched, added or changed after. The copy is checked first; if it is not whole, nothing happens.'));
  }, [{words: 'No, leave it', close: true},
      {words: 'Put it back', cls: 'danger', icon: 'undo', close: true, go: function () {
        dbDo({action: 'plex-restore', name: name, own: own ? 1 : 0}, 'Plex\'s database is being put back');
      }}], true);
}
