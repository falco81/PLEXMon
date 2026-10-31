# plexmon - what changed

Every version is a package in `release/` (the newest ten), signed with the
release key; Settings → Upgrades offers a newer one by itself. The entries are
read by Settings → Upgrades (*What changed*), so each starts `## X.Y.Z`.

## 2.0.3 - 2026-10-31

PHP and php2shell (CVE-2026-109618, CVE-2026-109619 - remote code execution
through how PHP takes a request apart, fixed 21 October 2026), as otp does it.

- **install.sh brings PHP up to the fixed release** - on every install and
  every upgrade, so also when the upgrade comes from Settings → Upgrades. A PHP
  already fixed is left exactly as it is; one that is not has its packages
  updated on the stream it is on (`dnf upgrade --refresh 'php*'`; AlmaLinux 9:
  php:8.3 is 8.3.36, php:8.2 is 8.2.35, the default 8.0 has the fix backported
  and keeps its number - so the packages' changelog is read as well as the
  version), and php-fpm is restarted. Where AppStream has no fixed build,
  Remi's PHP 8.4 is offered (asked; `./install.sh --upgrade --remi`; never
  taken by itself during an upgrade - it would move every PHP site on the
  machine). `--php-stream X.Y` for a machine without PHP, `--no-php-update` to
  leave it alone (it is still checked and guarded).
- **The guard as a second layer**: `/etc/php.d/99-php2shell.ini` (upload
  progress off, strict sessions) for every PHP on the machine, and nginx
  refusing a form or multipart body to plexmon's PHP pages under `/smart/`
  (`/etc/nginx/plexmon/php-guard.conf`, included in the vhost's PHP location,
  checked with `nginx -t` and put back on a refusal). The pages never had PHP
  take a body apart - they read JSON or a file from php://input.
- Settings says it: a red bar at the top for a full administrator while the
  PHP running the pages may lack the fix (gone once the daemon reads the fix in
  the packages' changelog), the line in *Problems*, the PHP and its state in
  About, and `plexmon --check` (`PHP 8.x.y has the php2shell fix`).

## 2.0.2 - 2026-10-31

- A secret kept in Settings (the ACME DNS tokens, keys and passwords) is shown
  to a full administrator as otp shows it: dots and its last two characters
  (`••••••••XY`), so the one set can be told from another; while one is being
  typed, only dots. A read-only account sees nothing of it. Saved back
  unchanged, the kept one stays.

## 2.0.1 - 2026-10-31

The command line and the console, as otp's.

- `plexmon` with nothing after it says what it is doing (`--status`) - it no
  longer starts a second daemon; the daemon is `plexmon --daemon`, which
  plexmon.service runs (an older unit starting `plexmon.py` bare still works).
- One command line for everything, otp's: `plexmon --help` in two columns and
  in colour (colorama, or the same colours written by hand; `--no-color`,
  `NO_COLOR`), every line coloured by what it says, `[ ok ]` / `[fail]` when
  piped, `--json` everywhere it makes sense. The dashboard's own options
  (`--wake`, `--refresh`, `--oneshot`, `--diag`, `--atv...`) are in it, and
  `--check` checks the dashboard's whole chain and Settings, nginx and the
  certificate together.
- New on the command line: `--admin-2fa`, `--tls-preset`, `--tls-dh`,
  `--tls-hsts`, `--plex-cert status`, `--acme`, `--acme-dns-check`,
  `--events --search`, `--log`.
- `plexmon --console` is otp's console: the bar at the top (the service, the
  dashboard, Plex, the certificate and its days, ACME, the accounts), menus
  by the arrows and numbers, windows with fields over the menu, Yes / No before
  anything that cannot be undone, lists to scroll and filter (`/`), every
  setting browsed and changed with its help, the live log and the journal
  (what is typed is a grep), `!` a shell. Menus: Status and checks, Live log,
  The disks and the dashboard, The televisions, Accounts of Settings, Settings,
  Certificates and TLS, Automatic certificates, Upgrades, The service.

## 2.0.0 - 2026-10-31

plexmon on otp's engine and pages: the dashboard as it was, and **Settings**.

- **The dashboard** (`/smart/`) does exactly what it did - SMART, temperatures,
  disks that are never woken, now playing, activity, performance, history, the
  television remotes - still open to the LAN without signing in. New: Czech and
  English (the EN / CS switch at the top) and a link to Settings.
- **Settings** (`/smart/settings/`) - signing in needed; otp's administration,
  the same engine and the same pages:
  - *Accounts*: full admin / read only, passwords, switched off and on, who is
    signed in (ended from here), who is locked out (cleared from here), a second
    step by an authenticator app (`admin_2fa`), the columns of every table laid
    out per account, your own settings exported and imported.
  - *Certificates and TLS*: the certificate the Plex address answers with -
    uploaded (PEM or PFX / PKCS#12), from a request made here (CSR) or
    self-signed; the TLS of nginx (standard, Mozilla intermediate / modern,
    custom; DH parameters; HSTS) - written into the Plex vhost's files, nginx
    asked (`nginx -t`) before it is reloaded, everything put back on a refusal.
    The certificate nginx had is taken over as it was. New for a Plex server:
    *Plex Media Server uses this certificate too* - its custom certificate
    (PKCS#12 named in Preferences.xml), renewed with the rest.
  - *Automatic certificates*: Let's Encrypt (or another ACME CA), HTTP-01 -
    nginx answers the CA from /var/www/plexmon-acme - or DNS-01 (deSEC,
    Cloudflare, RFC 2136, acme-dns, by hand), renewed by itself; *In your DNS*,
    *Try port 80*, *Back to the one before*.
  - *Upgrades*: a new plexmon from the web (`release/` of
    github.com/falco81/PLEXMon, looked at daily) or as a file - signed (Ed25519),
    every file checked, put in by root through plexmon-upgrade.path; going back
    to an older version, only a safe one.
  - *About*: the version, what it runs on, the language of the pages (Czech,
    English - `web/lang/*.json`, editable), the console at login, the debug log.
  - *Events*: every sign-in, change, certificate and upgrade.
- **SQLite** (`/var/lib/plexmon/plexmon.db`), secrets sealed with
  `/var/lib/plexmon/secrets.key`.
- **The command line**: `plexmon --status`, `--add-user`, `--cert-import`,
  `--acme-order`, `--upgrade-online`, `--set KEY=VALUE`... (`plexmon --help-settings`);
  the dashboard's own options as before (`--check`, `--diag`, `--wake`, `--atv`...).
- **The console**: `plexmon --console` - menus, the live log, every action; at
  root's login with `plexmon --console-at-login on`.
- **install.sh** takes the old plexmon over: `plex-status.service` becomes
  `plexmon.service` (the old name still works), its limits (`plex-status.service.d/`)
  and its Python (3.12 for the TV remotes) are kept, the history and the SMART
  cache in /var/lib/plex-status stay, the Plex vhost is adopted once and checked.
- plexmon never runs in a cluster: one server, no VIP address, no replication.
