# Installing plexmon

AlmaLinux / Rocky / RHEL 9 (and similar), nginx + php-fpm in front of Plex Media
Server, Python 3.9 or newer (3.10+ for remote control of televisions, typically
`python3.12`), openssl. SELinux does not matter (the files are in /var/www and /etc/nginx).

## 1. A new installation and moving from the current plexmon

```bash
tar xzf plexmon-X.Y.Z.tar.gz
cd plexmon-X.Y.Z
sudo ./install.sh
```

What `install.sh` does on a server with the current plexmon (`plex-status.service`):

1. **Takes over the old service**: stops `plex-status.service` and replaces it
   with `plexmon.service` (the name `plex-status.service` still works - it is an alias).
   Your limits from `plex-status.service.d/` (e.g. `atv.conf` with `MemoryMax=400M`)
   are carried over to `plexmon.service.d/`.
   **Python** (since 2.2.0): the daemon moves to the newest Python AlmaLinux 9
   has in AppStream - 3.14, 3.13 or 3.12 (`dnf install python3.X
   python3.X-pip`), beside the system `python3`, which dnf runs on and which
   never changes. The television control libraries (aiowebostv, samsungtvws,
   colorama) the previous Python had are installed for the new one with pip
   beforehand; the daemon moves only when everything it needs imports in it -
   otherwise it stays on the previous one. `PLEXMON_KEEP_PYTHON=1 ./install.sh --upgrade`
   leaves Python as it is. pyatv (Apple TV) has its own virtualenv and does not change.
2. **The history stays**: `/var/lib/plex-status` (SMART cache, chart history),
   `/etc/plex-status/config.json` (dashboard settings) do not change.
3. **The program**: `/usr/local/lib/plexmon/` (plexmon.py, plexmon_admin.py,
   plexmon_upgrade.py, release-keys/), `/usr/local/bin/plexmon`.
4. **The pages**: `/var/www/html/smart/` - the dashboard (`index.php`) in the same
   place, new: `settings/`, `assets/`, `lang/`.
5. **nginx** - once, carefully: in `/etc/nginx/conf.d/plex.conf` it moves the lines
   `ssl_certificate`, `ssl_certificate_key`, `ssl_protocols`,
   `ssl_prefer_server_ciphers` (and other `ssl_*`, if there are any) unchanged into
   `/etc/nginx/plexmon/tls.conf` and puts an `include` in their place; into each
   `server {}` it adds the answer for ACME (`/.well-known/acme-challenge/` from
   `/var/www/plexmon-acme`). Nothing else - the proxy to Plex, `/smart`, the limit to
   the LAN stay as they are. Checked with `nginx -t`; when it does not pass, everything goes back.
   A copy of the original file is in `/var/lib/plexmon/backup/`.
6. **The certificate** nginx has (`/etc/nginx/certs/server.pem` and `.key`)
   is taken into Settings as it is - nothing is overwritten.
7. **PHP and php2shell** (CVE-2026-109618, CVE-2026-109619 - remote code
   execution, fixed 21 Oct 2026) - on every installation and upgrade (the one from
   the pages too) PHP moves to **the 8.3 branch, at least 8.3.36**: an older PHP (the default
   8.0, 8.1, 8.2) is switched to `php:8.3` (`dnf module reset/enable`,
   `distro-sync 'php*'`), PHP on 8.3 is updated to the newest build,
   a newer fixed one (8.4) stays. php.ini, php.d and the php-fpm pools stay (and are
   copied to `/var/lib/plexmon/backup/php-*` beforehand); php-fpm is checked (`php-fpm
   -t`) and restarted - when it does not come up on 8.3, everything goes back to the original branch. Where AppStream has no fixed PHP, it offers PHP
   8.4 from the Remi repository (only when asked or with `--remi`, never by itself on
   an upgrade). On top of that a second layer: `/etc/php.d/99-php2shell.ini`, and nginx
   refuses form and multipart bodies to the pages under `/smart/`
   (`/etc/nginx/plexmon/php-guard.conf`, checked with `nginx -t`). Options:
   `--php-stream X.Y`, `--remi`, `--no-php-update`.
8. **The first account**: `admin` with a random password (printed and saved to
   `/var/lib/plexmon/first-login.txt`); on the first sign-in you choose your own.

Then: `https://<server>/smart/settings/`.

## 2. Upgrades

From the pages: *Settings → Upgrades → From the web → Upgrade to X* (downloads
from `https://github.com/falco81/PLEXMon/tree/main/release`, verifies the signature and puts it in),
or by dragging a package in. From the command line:

```bash
plexmon --upgrade-online          # the newest from the web
# or by hand:
tar xzf plexmon-X.Y.Z.tar.gz && cd plexmon-X.Y.Z && sudo ./install.sh --upgrade
```

## 3. Checking

```bash
plexmon --status        # Settings: service, certificate, nginx, ACME
plexmon --check         # dashboard: every line "ok"
systemctl status plexmon
journalctl -u plexmon -n 50
```

## 4. Uninstalling

```bash
sudo ./install.sh --uninstall
```

The history, `/etc/plexmon`, the database and the nginx certificates stay; `plex.conf`
still contains `include /etc/nginx/plexmon/*.conf` (the files stay), so that nginx
keeps working. The original `plex.conf` is in `/var/lib/plexmon/backup/`.

## Time, restart, system updates, the database, backups (since 2.2.0)

In Settings in the groups **The machine** and **Data** - the same as in otp:

- **Time** - chrony: offset, servers, your own NTP servers (a block in
  `/etc/chrony.conf`, the original lines only commented out), time zone.
- **Restart and power off** - from the page or on a schedule (only when an update
  needs it: `needs-restarting -r`).
- **System updates** - dnf, everything or only security, the output live.
  The `plexmediaserver` package is **left out** (`--exclude=plexmediaserver*`)
  until `osup_plex` is turned on.
- **The database** - the Plex Media Server database: a copy while Plex runs (SQLite online
  backup, nothing is written to Plex's file), a Plex SQLite check
  (`integrity_check`), copies in `/var/lib/plexmon/plex-db` (or `plex_db_dir`),
  download, **restore** (Plex is stopped, its files are put aside in
  `before-restore-…` - nothing is deleted - the copy is put in with Plex as its owner, Plex
  is started; on an error everything goes back). Plex's own backups
  (`com.plexapp.plugins.library.db-YYYY-MM-DD`) can be restored too. Optimizing
  and cleaning bundles through the Plex API. The server's own timer (e.g.
  `plex-db-backup.timer`) is shown and can be turned off. And the plexmon database:
  copy, check, VACUUM, deleting events, restore.
- **Backups** - to another machine (folder, FTP, FTPS, SFTP, SCP, SMB), encrypted
  AES-256 with the password `rbackup_password`, with a restore test. A backup contains
  plexmon and, with `rbackup_plex_db`, also a verified copy of the Plex database and its
  Preferences.xml; `RESTORE.md` in each backup describes restoring on a clean machine.

Command line: `plexmon --db`, `--plex-db-copy`, `--plex-db-restore NAME`,
`--backup-now`, `--backup-verify`, `--os-updates`, `--os-update security`,
`--time`, `--reboot` … (`plexmon --help`), console menu 12.

## Maintenance (since 2.3.0)

Settings → Upgrades and maintenance → **Maintenance**: the server is out of service.
plexmon then does nothing by itself - no backups, copies of the databases, system
updates or restarts on a schedule, no renewal of the certificate, no routine
SMART read of the disks (useful e.g. when swapping a disk). The pages, the dashboard and everything
done by hand go on working. An upgrade from the pages takes the server into maintenance by itself and,
once it has run, back.

```bash
plexmon --maintenance on --why "disk swap"
plexmon --maintenance off
plexmon --maintenance status
```

## Proxy (since 2.4.0)

Settings → Proxy: an HTTP proxy for the updates alone - the new versions of plexmon and
(optionally) the system updates through dnf. Nothing else plexmon does goes through it or
changes with it: Plex, the certificates (ACME), the ratings, the televisions and the backups go
out exactly as before. "Try it" shows each step of the way. On the command line:
`plexmon --proxy-test https://github.com/`.

## Files

| | |
|---|---|
| `/usr/local/lib/plexmon/` | the program |
| `/var/www/html/smart/` | the dashboard and Settings |
| `/var/lib/plexmon/plexmon.db` | the Settings database (SQLite) |
| `/var/lib/plexmon/secrets.key` | the key the secrets are sealed with (back it up with the database) |
| `/var/lib/plex-status/` | history and SMART cache of the dashboard |
| `/etc/plex-status/config.json` | dashboard settings ([CONFIG.md](CONFIG.md)) |
| `/etc/plexmon/config.json` | default values of Settings (what is saved on the pages overrides them) |
| `/etc/nginx/plexmon/tls.conf`, `acme.conf` | TLS and ACME for the Plex vhost |
| `/var/log/plexmon/plexmon.log` | the Settings log |
| `/run/plexmon/api-token` | the token for the PHP pages (readable by the php-fpm group) |
