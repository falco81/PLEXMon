<?php
/**
 * settings/index.php — plexmon's Settings.
 *
 * otp's administration pages, for a Plex server: the same header, the same
 * tabs, the same settings down a rail at the left edge, the same popups. It
 * collects nothing itself and decides nothing: every word comes from the
 * plexmon daemon over its API on localhost, and the daemon decides whether
 * whoever is signed in may have it. The dashboard (../) stays open to the
 * LAN without signing in; this does not.
 */
declare(strict_types=1);

define('PM_COOKIE', 'plexmon_session');
define('PM_COOKIE_PATH', rtrim(dirname((string) ($_SERVER['SCRIPT_NAME'] ?? '/smart/settings/index.php')), '/') . '/');
require_once dirname(__DIR__) . '/plexmon-page.php';
require_once dirname(__DIR__) . '/plexmon-admin-api.php';

/* Where the pages are: /smart/ as the Plex vhost serves them. */
$base = rtrim(dirname(PM_COOKIE_PATH), '/') . '/';

if (isset($_GET['api'])) {
    $what = (string) $_GET['api'];
    $post = $_SERVER['REQUEST_METHOD'] === 'POST';
    if ($what === 'lang' && !$post) {
        pm_serve_lang();
    }
    if ($what === 'software' && !$post) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        [$status, $body] = pm_call('/api/software', 'GET', null, 20.0);
        $data = json_decode((string) $body, true);
        if ($status !== 200 || !is_array($data)) {
            http_response_code($status ?: 503);
            echo $body ?: json_encode(['ok' => false, 'error' => 'the daemon is not answering']);
            exit;
        }
        $ext = [];
        foreach (get_loaded_extensions() as $e) {
            $v = phpversion($e);
            $ext[$e] = ($v === false || $v === PHP_VERSION) ? '' : (string) $v;
        }
        ksort($ext, SORT_NATURAL | SORT_FLAG_CASE);
        $data['pages'] = [
            'php' => PHP_VERSION, 'sapi' => PHP_SAPI, 'extensions' => $ext,
            'opcache' => function_exists('opcache_get_status') && is_array(@opcache_get_status(false))
                && !empty(@opcache_get_status(false)['opcache_enabled']),
            'server' => (string) ($_SERVER['SERVER_SOFTWARE'] ?? ''),
            'os' => PHP_OS_FAMILY,
        ];
        echo json_encode($data);
        exit;
    }
    if ($post) {
        pm_guard_post();
    }
    if ($what === 'login' && $post) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        $sent = sent_body();
        [$status, $body] = pm_call('/api/auth/login', 'POST', [
            'name'     => (string) ($sent['name'] ?? ''),
            'password' => (string) ($sent['password'] ?? ''),
        ], 30.0);
        if ($status === 0) {
            http_response_code(503);
            echo json_encode(['ok' => false, 'error' =>
                'the daemon is not answering — is plexmon.service running?']);
            exit;
        }
        $data = json_decode($body, true);
        if ($status === 200 && is_array($data) && !empty($data['token'])) {
            pm_set_session((string) $data['token']);
            unset($data['token']);
            echo json_encode($data);
            exit;
        }
        http_response_code($status === 200 ? 400 : $status);
        echo json_encode(is_array($data) ? $data : ['ok' => false, 'error' => 'sign-in failed']);
        exit;
    }
    if ($what === 'mfa' && $post) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        [$status, $body] = pm_call('/api/auth/mfa', 'POST', sent_json(), 90.0);
        if ($status === 0) {
            http_response_code(503);
            echo json_encode(['ok' => false, 'error' =>
                'the daemon is not answering — is plexmon.service running?']);
            exit;
        }
        $data = json_decode($body, true);
        if (is_array($data) && !empty($data['token'])) {
            if ($status === 200) {
                pm_set_session((string) $data['token']);
            }
            unset($data['token']);
            $body = json_encode($data);
        }
        http_response_code($status);
        echo $body;
        exit;
    }
    if ($what === 'upgrade-package' && $post) {
        /* A new version of plexmon: the .tar.gz itself is the body. A full
           administrator only, asked before a byte is read; the daemon checks
           its signature, and root checks it again before putting it in. */
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        $who = pm_whoami();
        if (!is_array($who) || ($who['role'] ?? '') !== 'fulladmin') {
            http_response_code(401);
            echo json_encode(['ok' => false, 'error' => is_array($who)
                ? 'only a full administrator may send a new version'
                : 'not signed in']);
            exit;
        }
        [$tmp, $why] = pm_take_body(64 * 1024 * 1024);
        if ($tmp === null) {
            http_response_code(400);
            echo json_encode(['ok' => false, 'error' => $why]);
            exit;
        }
        try {
            [$status, $body] = pm_send_file('/api/upgrade/package', $tmp);
        } finally {
            @unlink($tmp);
        }
        if ($status === 0) {
            http_response_code(503);
            echo json_encode(['ok' => false, 'error' =>
                'the daemon is not answering — is plexmon.service running?']);
            exit;
        }
        http_response_code($status);
        echo $body;
        exit;
    }
    if ($what === 'logout' && $post) {
        pm_call('/api/auth/logout', 'POST', [], 10.0);
        pm_clear_session();
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(['ok' => true]);
        exit;
    }
    /* Reads apart from writes, so a GET can never reach a road that changes
       something, whatever is typed in the address bar. */
    $reads = [
        'status' => '/api/status', 'settings' => '/api/settings', 'users' => '/api/users',
        'sessions' => '/api/sessions', 'events' => '/api/events', 'log' => '/api/log',
        'whoami' => '/api/auth/whoami', 'my-settings' => '/api/auth/settings',
        'upgrade' => '/api/upgrade', 'web' => '/api/web', 'lockouts' => '/api/lockouts',
        'console-login' => '/api/console-login', 'mfa' => '/api/auth/mfa', 'acme' => '/api/acme',
    ];
    $writes = [
        'settings' => '/api/settings', 'users' => '/api/users', 'sessions' => '/api/sessions',
        'password' => '/api/auth/password', 'prefs' => '/api/auth/prefs',
        'columns' => '/api/auth/columns', 'my-settings' => '/api/auth/settings',
        'upgrade' => '/api/upgrade', 'web' => '/api/web', 'lockouts' => '/api/lockouts',
        'console-login' => '/api/console-login', 'mfa' => '/api/auth/mfa', 'acme' => '/api/acme',
    ];
    if ($post) {
        if (!isset($writes[$what])) {
            http_response_code(404);
            header('Content-Type: application/json');
            echo json_encode(['ok' => false, 'error' => 'unknown endpoint']);
            exit;
        }
        pm_relay($writes[$what], 'POST', sent_json(), 120.0);
        exit;
    }
    if (!isset($reads[$what])) {
        http_response_code(404);
        header('Content-Type: application/json');
        echo json_encode(['ok' => false, 'error' => 'unknown endpoint']);
        exit;
    }
    $query = $_GET;
    unset($query['api']);
    pm_relay($reads[$what] . ($query ? '?' . http_build_query($query) : ''), 'GET', null, 60.0);
    exit;
}

/* ---- the page ------------------------------------------------------------ */
$me      = pm_whoami();
$mfa     = (is_array($me) && !empty($me['mfa'])) ? (string) $me['mfa'] : '';
if ($mfa !== '') { $me = null; }
$health  = pm_get('/api/health', 5.0);
$alive   = is_array($health);
$token_there = file_exists(PM_TOKEN);
$token_ok    = pm_token() !== null;
$web_user    = function_exists('posix_getpwuid') && function_exists('posix_geteuid')
    ? (posix_getpwuid(posix_geteuid())['name'] ?? '?') : '?';
if (!$me && !pm_local()) {
    $token_there = false;
    if (!$token_ok || empty($health['database']['ok']) || empty($health['users'])) {
        $alive = false;
    }
    $health = ['lang' => (string) ($health['lang'] ?? 'cs'), 'database' => ['ok' => true],
               'users' => 1];
}
$initial = $me ? (pm_get('/api/status', 8.0) ?: null) : null;
$host    = (string) ($_SERVER['HTTP_HOST'] ?? php_uname('n'));
$site    = (string) ($initial['site'] ?? '');
$title   = $site !== '' ? $site . ' · plexmon' : 'plexmon';
$lang    = pm_lang_pick($me, (string) ($health['lang'] ?? 'cs'));
$load    = $lang !== 'en' && pm_lang_pack($lang) !== null;
if ($me && ($_COOKIE['plexmon_lang'] ?? '') !== $lang) {
    setcookie('plexmon_lang', $lang, ['expires' => time() + 365 * 86400, 'path' => '/',
              'secure' => pm_https(), 'httponly' => false, 'samesite' => 'Strict']);
}
?>
<!doctype html>
<html lang="<?= h($lang) ?>" class="app-shell-root<?= $load ? ' i18n-wait' : '' ?>">
<head>
<?php pm_head($title, $lang, $load, $nonce, $base); ?>
</head>
<body class="app-shell fold-words<?= $me ? ' framed' : '' ?>">
<div class="wrap">

<?php if (!$me): ?>
<div class="signin">
  <div class="signbox">
    <div class="card">
    <h1 data-no-i18n><?= h($title) ?></h1>
    <p class="lede">Settings of the Plex server on <span data-no-i18n><?= h($host) ?></span>. Sign in to go on.</p>
    <?php if (!$token_ok && $token_there): ?>
      <div class="offline">
        The daemon is running, but this page may not read what it wrote.<br><br>
        <span class="mono"><?= h(PM_TOKEN) ?></span> exists and is not readable by
        <span class="mono"><?= h($web_user) ?></span>, which is what this page runs as.
        On the server:<br>
        <span class="mono">chgrp <?= h($web_user) ?> /run/plexmon /run/plexmon/api-token</span><br>
        <span class="mono">systemctl restart plexmon</span>
      </div>
    <?php elseif (!$alive): ?>
      <div class="offline">The daemon is not answering. Nobody can sign in until
        <span class="mono">plexmon.service</span> is running:<br>
        <span class="mono">systemctl status plexmon</span> ·
        <span class="mono">journalctl -u plexmon -n 50</span></div>
    <?php elseif (empty($health['database']['ok'])): ?>
      <div class="offline">The daemon is running, but its database will not open. On the
        server: <span class="mono">plexmon --status</span> · <span class="mono">journalctl -u plexmon -n 50</span></div>
    <?php elseif (empty($health['users'])): ?>
      <div class="offline">There is no account on this machine yet. Make one on the server:<br>
        <span class="mono">plexmon --add-user yourname --role fulladmin</span></div>
    <?php endif; ?>
    <div id="mfa-box" data-stage="<?= h($mfa) ?>" hidden></div>
    <form id="signin-form" autocomplete="on"<?= $mfa !== '' ? ' hidden' : '' ?>>
      <input type="text" id="in-name" name="username" placeholder="name" autocomplete="username"
             autocapitalize="none" spellcheck="false" required>
      <input type="password" id="in-pass" name="password" placeholder="password"
             autocomplete="current-password" required>
      <button class="btn primary cmd" type="submit" id="in-go"><svg
        viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
        stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
        ><path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4"/><path d="M14 12h7"/><path
        d="M18 9l3 3-3 3"/></svg><span>Sign in</span></button>
      <div class="said" id="in-said"></div>
    </form>
    <div style="margin-top:14px;display:flex;justify-content:space-between;align-items:center">
      <span class="muted small"><a class="fgo" href="<?= h($base) ?>">← the dashboard</a> · plexmon <?= h(PM_VERSION) ?></span>
      <span class="langpick" id="langpick"></span>
    </div>
    </div>
  </div>
</div>
</div>
<script nonce="<?= h($nonce) ?>" src="<?= h($base) ?>assets/core.js?v=<?= h(PM_VERSION) ?>"></script>
<script nonce="<?= h($nonce) ?>">
(function () {
  var form = $('signin-form'), said = $('in-said'), go = $('in-go');
  var host = $('langpick');
  if (host && window.OTP_LANGS && window.OTP_LANGS.length > 1) {
    window.OTP_LANGS.forEach(function (l) {
      var b = el('button', l.code === window.OTP_LANG ? 'on' : '', l.code.toUpperCase());
      b.type = 'button';
      b.setAttribute('data-no-i18n', '');
      b.title = l.name;
      b.onclick = function () {
        document.cookie = 'plexmon_lang=' + encodeURIComponent(l.code) + '; path=/; max-age=31536000; '
          + 'samesite=strict' + (location.protocol === 'https:' ? '; secure' : '');
        location.reload();
      };
      host.appendChild(b);
    });
  }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    said.className = 'said';
    said.textContent = '';
    go.disabled = true;
    var words = go.querySelector('span');
    words.textContent = 'Signing in…';
    api('login', {name: $('in-name').value, password: $('in-pass').value}).then(function (d) {
      if (d && d.mfa) { form.hidden = true; mfaStep(); return; }
      said.className = 'said good';
      said.textContent = 'Welcome back.';
      location.reload();
    }).catch(function (e) {
      said.className = 'said bad';
      said.textContent = String(e.message || e);
      go.disabled = false;
      words.textContent = 'Sign in';
      $('in-pass').value = '';
      $('in-pass').focus();
    });
  });
  if ($('mfa-box').dataset.stage) mfaStep(); else $('in-name').focus();
}());
</script>
</body>
</html>
<?php exit; endif; ?>

<header>
  <h1 data-no-i18n><?= h($site ?: 'plexmon') ?> <span class="host"><?= h($host) ?></span></h1>
  <span id="state-pill" class="pill unknown"><span class="dot"></span><span>…</span></span>
  <div class="spacer"></div>
  <div class="btnrow">
    <a class="btn small" id="to-dash" href="<?= h($base) ?>" data-icon="chart"
       title="The dashboard of the Plex server - open to the LAN without signing in.">Dashboard</a>
    <button type="button" class="btn small mebtn" id="me-btn" data-icon="person"
            title="The account you are signed in as: what it may do, its password, the language of these pages, the columns of the tables and your settings in a file."><span class="me-name" data-no-i18n><?= h($me['name']) ?></span></button>
    <button class="btn small" id="signout" data-icon="out">Sign out</button>
  </div>
  <div class="updated" id="updated" title="Click to pause or resume the live updates">
    <b>live</b><br><span id="updated-at">…</span></div>
</header>

<div id="offline" class="offline" hidden></div>
<div id="phpsafety" class="offline" hidden></div>
<div id="problems" class="offline warnish" hidden></div>

<div class="tabs" role="tablist">
  <button role="tab" aria-selected="true" data-tab="settings" data-icon="gear"
          title="The accounts, the certificates of the Plex server and how TLS is spoken, automatic certificates, upgrades and what this is.">Settings</button>
  <button role="tab" aria-selected="false" data-tab="events" data-icon="history"
          title="Every sign-in, every change of a setting, every certificate put in - and the daemon’s own log.">Events</button>
</div>

<!-- ===================== Events ===================== -->
<section class="panel" id="tab-events" hidden>
  <div class="card full" data-hold>
    <h2>What this daemon has done</h2>
    <p class="note">Every sign-in, every account made or changed, every change of a setting,
      every certificate put in or renewed, every upgrade. Kept in the database, so it survives
      a restart.</p>
    <div class="btnrow filters">
      <input type="search" id="ev-q" placeholder="anything in an event…" style="flex:1;min-width:200px">
      <select id="ev-kind"><option value="">everything</option></select>
    </div>
    <div id="ev-table" class="scroll rowsonly"></div>
    <div class="pager" id="ev-pager"></div>
  </div>
  <div class="card full fulladmin-only aside">
    <h2>The daemon's own log</h2>
    <p class="note">The tail of <span class="mono">/var/log/plexmon/plexmon.log</span>. Switch on
      <i>debug</i> in Settings → About to have it say every step.</p>
    <div class="btnrow"><button class="btn cmd" id="log-read" data-icon="log">Read it</button>
      <select id="log-lines"><option>200</option><option selected>600</option><option>2000</option></select></div>
    <pre class="lines" id="log-out" style="margin-top:12px" hidden></pre>
  </div>
</section>

<!-- ===================== Settings ===================== -->
<section class="panel" id="tab-settings">
<div class="ctl-layout">
  <nav class="ctl-nav" id="ctl-nav" aria-label="Settings sections">
    <div class="ctl-navtop">
      <button type="button" class="ctl-pin" id="ctl-pin" aria-pressed="false" aria-label="Keep this menu open"></button>
      <div class="ctl-find-wrap">
        <input type="search" id="ctl-find" placeholder="Find a setting…" autocomplete="off" aria-label="Find a setting">
        <div class="ctl-found" id="ctl-found" hidden></div>
      </div>
    </div>
    <div class="ctl-group">Access</div>
    <button type="button" data-pane="people" data-icon="person" title="Who may open these pages, what each of them may do, and who is signed in right now.">Accounts</button>
    <div class="ctl-group">Certificates</div>
    <button type="button" data-pane="web" data-icon="lock" title="The certificate the Plex address answers with, and how TLS is spoken - and Plex Media Server's own.">Certificates and TLS</button>
    <button type="button" class="fulladmin-only" data-pane="acme" data-icon="lock" title="The certificate ordered from Let's Encrypt (or another ACME CA) and renewed by itself.">Automatic certificates</button>
    <div class="ctl-group">The server</div>
    <button type="button" class="fulladmin-only" data-pane="upgrade" data-icon="up" title="A new version, put in from here - from the web or as a file.">Upgrades</button>
    <button type="button" data-pane="about" data-icon="info" title="Version, what is running, the language, the console at login, the debug log.">About</button>
  </nav>

  <div class="ctl-main">
  <div class="card full" id="readonly-note" hidden>
    <h2>You may look, but not change</h2>
    <p class="note" style="margin:0">This account may not change the settings. A full administrator can change that under <i>Accounts</i>.</p>
  </div>

  <div class="ctl-pane" id="pane-people">
    <div class="ctl-head"><h2 class="ctl-title">Accounts</h2>
      <p class="ctl-lede">Who may open these pages. <b>full admin</b> may change everything;
        <b>read only</b> may look and change nothing. The dashboard itself asks nobody.</p></div>
    <div class="card full">
      <h2>Accounts <span class="right"><button class="btn cmd small primary admin-only" id="us-add" data-icon="add">Make an account</button></span></h2>
      <div id="us-table"></div>
    </div>
    <div class="card full fulladmin-only"><h2>Signed in now</h2><div id="se-table"></div></div>
    <div class="card full fulladmin-only" id="lo-card"><h2>Locked out <span class="right" id="lo-tools"></span></h2>
      <p class="note">Addresses and names with wrong attempts still counted - at the limit they
        are locked out until the time runs out. Clear one, or all of them, and they may try
        again at once.</p>
      <div id="lo-table"></div></div>
    <div class="card full" id="cols-card">
      <h2>Columns of the tables <span class="right" id="cols-count"></span></h2>
      <p class="note">Every table on these pages can be laid out the way you read it. Press
        your name at the top right and switch on <b>Edit the columns</b>; then drag the edge of
        a column's heading to make it wider or narrower, and drag the heading itself to move
        the column. The right button on a heading hides a column, brings a hidden one back or
        adds an empty one for room. Twice on an edge puts that table's widths back. Each change
        is kept with your account the moment you let go, not in this browser, and comes back
        wherever you sign in; nobody else's tables change. The switch is for this sign-in
        only: signing out, the sign-in running out or the service restarting turn it off
        again, and the columns stay as you left them.</p>
      <div class="btnrow" style="margin-top:12px">
        <button class="btn cmd" id="cols-edit-go" data-icon="edit"
                title="The same switch as under your name: on for this sign-in only.">Edit the columns</button>
        <button class="btn cmd" id="cols-reset" data-icon="undo"
                title="Every table back as it comes: the widths and the order of the columns you set, on every page. Nobody else's are touched.">Put my columns back as they come</button>
        <button class="btn cmd" id="cols-export" data-icon="download"
                title="How you have set these pages up for yourself, saved as a JSON file.">Export my settings</button>
        <button class="btn cmd" id="cols-import" data-icon="upload"
                title="Read a file saved by Export my settings - here or on another machine running plexmon.">Import settings…</button>
      </div>
    </div>
    <div class="card full"><div id="sect-people"></div></div>
  </div>

  <div class="ctl-pane" id="pane-web" hidden>
    <div class="ctl-head"><h2 class="ctl-title">Certificates and TLS</h2>
      <p class="ctl-lede">How the Plex address speaks HTTPS - nginx in front of Plex Media
        Server, the dashboard and these pages: the certificate with its chain, the protocols,
        the ciphers. And, where asked, the same certificate in Plex Media Server itself.</p></div>
    <div class="card full">
      <h2>Certificates</h2>
      <p class="note">The certificate of this server's name. A certificate is sent as PEM files
        (the certificate, its key, the chain, the root - in any order, or all in one file) or as
        a PFX / PKCS#12 file, with its password or without; or the request is made here and only
        what the authority sends back is uploaded. It goes into the files the Plex vhost of
        nginx names, nginx is asked before it is reloaded, and everything is put back as it was
        when nginx says no.</p>
      <div id="web-certs" class="cert-grid"></div>
    </div>
    <div class="card full" id="plex-card">
      <h2>Plex Media Server</h2>
      <p class="note">Plex Media Server answers on port 32400 with a certificate of its own
        (*.plex.direct). Switched on below, it is given this certificate too - as its custom
        certificate - so the Plex apps that reach it straight by your own name see the same
        one. Plex reads it only when it starts.</p>
      <div id="plex-state"></div>
      <div id="sect-web"></div>
    </div>
  </div>

  <div class="ctl-pane" id="pane-acme" hidden>
    <div class="ctl-head"><h2 class="ctl-title">Automatic certificates</h2>
      <p class="ctl-lede">The certificate of this Plex server ordered from Let's Encrypt or
        another ACME CA and renewed by itself well before it runs out. The CA checks the name by
        asking it on port 80 (HTTP-01) - nginx answers it from a folder of plexmon's - or by a
        record in DNS (DNS-01), for a server the CA cannot see from the internet.</p></div>
    <div id="ac-host"></div>
    <div class="card full"><h2>Settings</h2><div id="sect-acme"></div></div>
  </div>

  <div class="ctl-pane" id="pane-upgrade" hidden>
    <div class="ctl-head"><h2 class="ctl-title">Upgrades</h2>
      <p class="ctl-lede">A new version put in from here: from the web with one click, or as a
        package sent by hand - its signature checked, then root runs its install.sh --upgrade,
        and plexmon comes back as the new version. The command line (install.sh --upgrade)
        works as it always has.</p></div>
    <div class="card full" id="up-nodes-card" hidden>
      <h2>The server</h2>
      <div id="up-nodes"></div>
    </div>
    <div class="card full admin-only" id="up-web-card">
      <h2>From the web</h2>
      <p class="note">Where new versions are published - the address below - is looked at once a
        day. A newer plexmon found there is offered here, and put in with one click: fetched, its
        signature checked like a package sent by hand, then put in. Nothing is put in by
        itself.</p>
      <div id="up-web"></div>
      <div id="sect-upgrade"></div>
    </div>
    <div class="card full admin-only">
      <h2>A new version</h2>
      <p class="note">The .tar.gz of a new plexmon, as it was released. It is checked before
        anything else: it must be signed by a release key this server trusts, every file in
        it must be the one signed, and it must not be older than what runs here. Root checks
        it all again before it is put in.</p>
      <div class="drop" id="up-drop">
        <input type="file" id="up-file" accept=".gz,.tgz,application/gzip,application/octet-stream" hidden>
        <span id="up-drop-says">Drop the plexmon-X.Y.Z.tar.gz here, or press to choose it</span>
      </div>
      <div id="up-pkg"></div>
      <div class="btnrow" style="margin-top:10px">
        <button class="btn cmd primary" id="up-start" data-icon="play" disabled>Upgrade</button>
        <button class="btn cmd" id="up-forget" data-icon="trash" hidden>Forget the package</button>
      </div>
      <div id="up-ready"></div>
    </div>
    <div class="card full admin-only" id="up-down-card" hidden>
      <h2>Going back to an older version</h2>
      <p class="note">Only to a version that is safe: one that reads what the newer versions
        wrote into the database the same - each version says how far back it may go, and the
        highest of what ran on this database and was installed on this machine counts. The
        database is not put back nor changed: the older version runs on it as it is.</p>
      <div id="up-down"></div>
    </div>
    <div class="card full" id="up-plan-card" hidden>
      <h2>The upgrade</h2>
      <div id="up-plan"></div>
      <p class="note small" id="up-retry" style="min-height:1.4em;margin:6px 0 0"></p>
      <div class="btnrow" style="margin-top:10px">
        <button class="btn cmd danger admin-only" id="up-cancel" data-icon="cancel" hidden>Stop it</button>
      </div>
    </div>
    <div class="card full">
      <h2>What root said last <span class="mono small muted" id="up-log-node" data-no-i18n></span></h2>
      <p class="note"><span>The output of install.sh, the last time this server was upgraded from these pages.</span>
        <span class="mono" data-no-i18n>/var/lib/plexmon/upgrade/last.log</span></p>
      <div class="seg" id="up-log-nodes" hidden style="margin-bottom:10px"></div>
      <pre class="lines" id="up-log">–</pre>
    </div>
    <div class="card full">
      <h2>On the command line</h2>
      <p class="note">The same, by hand, as root:</p>
      <pre class="lines">plexmon --upgrade-check                    # what is at the address
plexmon --upgrade-online                   # the newest: fetched, checked, put in
tar xzf plexmon-X.Y.Z.tar.gz && cd plexmon-X.Y.Z
./install.sh --upgrade                     # a package by hand
plexmon --upgrade-status                   # an upgrade started on these pages</pre>
    </div>
  </div>

  <div class="ctl-pane" id="pane-about" hidden>
    <div class="ctl-head"><h2 class="ctl-title">About</h2></div>
    <div class="card full"><div id="about-state"></div></div>
    <div class="card full"><div id="sect-about"></div></div>
  </div>

  <div class="card full" id="rest-card" hidden><div id="sect-rest"></div></div>

  <div class="save-bar admin-only" id="save-bar">
    <button class="btn cmd primary" id="set-save" data-icon="save"
            title="Save the changed settings into the database. They take effect the moment they are saved - nothing restarts.">Save the changes</button>
    <button class="btn cmd" id="set-reset" data-icon="undo"
            title="Throw away the changes on this page and read the settings back from the daemon as they are.">Put them back</button>
    <span class="muted small" id="set-said"></span>
  </div>
  </div>
</div>
</section>

</div><!-- /wrap -->

<div class="modal" id="modal" hidden>
  <div class="box" id="modal-box">
    <div class="head"><h3 id="modal-title">…</h3>
      <button class="xbtn" id="modal-x" data-icon="close" aria-label="Close"></button></div>
    <div class="body" id="modal-body"></div>
    <div class="foot" id="modal-foot"></div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script nonce="<?= h($nonce) ?>">
var ME = <?= json_encode($me, JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP) ?>;
var STATUS = <?= $initial ? json_encode($initial, JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP) : 'null' ?>;
var PHP_SAFETY = <?= ($me['role'] ?? '') === 'fulladmin' ? json_encode(pm_php_safety(), JSON_HEX_TAG | JSON_HEX_AMP) : 'null' ?>;
</script>
<script nonce="<?= h($nonce) ?>" src="<?= h($base) ?>assets/core.js?v=<?= h(PM_VERSION) ?>"></script>
<script nonce="<?= h($nonce) ?>" src="<?= h($base) ?>assets/cols.js?v=<?= h(PM_VERSION) ?>"></script>
<script nonce="<?= h($nonce) ?>" src="<?= h($base) ?>assets/admin.js?v=<?= h(PM_VERSION) ?>"></script>
</body>
</html>
