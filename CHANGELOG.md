# plexmon - what changed

Every version is a package in `release/` (the newest ten), signed with the
release key; Settings → Upgrades offers a newer one by itself. The entries are
read by Settings → Upgrades (*What changed*), so each starts `## X.Y.Z`.

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
