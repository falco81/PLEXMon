# plexmon

Monitoring of a Plex server - and its **Settings** built on the engine and
pages of [otp](https://github.com/falco81/OTP).

```
https://plex.example.cz/smart/            dashboard - in the LAN without signing in, as before
https://plex.example.cz/smart/settings/   Settings - only after signing in
```

**The dashboard** does exactly what it did: SMART, temperatures, capacity and wear
of the disks (a sleeping disk is never woken), what is playing right now, Plex
activity (transcoding, analysis, scanning), API and reverse proxy performance,
history and trends, control of televisions (Apple TV, LG, Samsung). New: in
Czech and English (the EN / CS switch at the top) and with a link to Settings.

**Plex library** (the first tab of Settings, only after signing in) - what
`plex_tools.py` could do, right on the server: posters and details of films and
series, the default audio and subtitles (of a whole series too), watched,
labels, titles in another language, ratings from IMDb / Rotten Tomatoes / TMDB /
TVmaze / Trakt, the watchlist. Only through the Plex API on 127.0.0.1 with the
server's own token, always a preview first. See [docs/plex-library.md](docs/plex-library.md).

**Settings** is the administration from otp - the same engine, the same pages,
the same behaviour - narrowed to what a Plex server needs:

| | |
|---|---|
| Accounts | full administrator / read-only, passwords, disabling, who is signed in, who is locked out, the second sign-in step (authenticator app), the table columns of each account, export / import of its own settings |
| Certificates and TLS | the certificate the Plex address answers with (nginx): upload of PEM and PFX / PKCS#12, a request (CSR) made here, self-signed; protocols, ciphers, DH, HSTS; all through `nginx -t`, on a refusal the original state comes back. On top of that for Plex: **Plex Media Server uses this certificate too** (Plex's own certificate, PKCS#12 in Preferences.xml). **Certificates from acme** (acme push, off by default): an acme certificate manager pairs once and pushes and renews the certificate by itself ([docs/acme-push.md](docs/acme-push.md)). **Trusted roots of the system**: certificate authorities of your own trusted by the whole operating system, beside the distribution's list ([docs/selinux.md](docs/selinux.md)) |
| Automatic certificates | Let's Encrypt or another ACME CA, HTTP-01 and DNS-01 (deSEC, Cloudflare, RFC 2136, acme-dns, by hand), renewal by itself, *In your DNS*, *Try port 80*, *Back to the one before* |
| SELinux | how SELinux is on the server, what plexmon needs of it, the way from disabled through permissive to enforcing - the data disks never relabelled, enforcing only once nothing was refused; a report for whoever writes the module ([docs/selinux.md](docs/selinux.md)) |
| Upgrades | a new version from the web in one click (it looks into `release/` of this repository daily) or from a file; Ed25519 signature, every file verified; back to an older version (only a safe one) |
| About | the version, what it runs on, the language of the pages (Czech / English - `lang/*.json`, editable), the console after root signs in, the debug log |
| Events | every sign-in, change, certificate, upgrade |

SQLite (`/var/lib/plexmon/plexmon.db`), Python with the standard library, PHP
pages that only ask the daemon on 127.0.0.1. plexmon never runs in a cluster.

## Command line

The same as in otp: in colour (colorama), `--json` wherever it makes sense,
`plexmon --help` in two columns. systemd starts the daemon (`plexmon --daemon`).

```
plexmon                          what it is doing (= --status)
plexmon --check                  check of everything: disks, SMART, API, nginx, certificate
plexmon --console                everything through a menu (live log, accounts, certificates, upgrades)
plexmon --wake | --refresh | --diag | --atv ...   dashboard and televisions, as before
plexmon --users                  Settings accounts
plexmon --add-user jan --role fulladmin
plexmon --cert-status            the nginx certificate
plexmon --cert-import cert.pem key.pem chain.pem
plexmon --tls-preset intermediate --tls-hsts 31536000
plexmon --settings               every setting, and where it comes from
plexmon --get proxy --get backup_keep   only these (--json for a script)
plexmon --set acme=1 --set acme_node_name=plex.example.cz
plexmon --set passkeys=1 && plexmon --passkeys   sign-in with Face ID / Touch ID (passkeys)
plexmon --acme-order             order a certificate now, follow the progress
plexmon --plex-cert on           Plex Media Server gets the certificate too
plexmon --upgrade-online         the newest version: download, verify, put in
plexmon --selinux-check          SELinux: the mode, what plexmon needs, what it refused
plexmon --os-roots-add ca.pem     a certificate authority of your own into the system's trust
plexmon --plex-tracks "Breaking Bad" --audio eng --subs cze   default tracks of a whole series
plexmon --help                   everything
```

## Installation and moving from the current plexmon

[INSTALL.md](INSTALL.md). In short:

```bash
tar xzf plexmon-X.Y.Z.tar.gz && cd plexmon-X.Y.Z
sudo ./install.sh            # also over the existing plexmon (plex-status.service) - takes it over
```

The packages are in [release/](release/) (the last ten); the content of the
newest one is unpacked in the root of this repository.

## Documentation

- [docs/plex-library.md](docs/plex-library.md) - Plex library (plex_tools)
- [docs/settings.md](docs/settings.md) - Settings: accounts, certificates, Plex, languages
- [docs/acme.md](docs/acme.md) - automatic certificates (HTTP-01 and DNS-01)
- [docs/upgrades.md](docs/upgrades.md) - upgrades from the web, signature, going back, releasing a version
- [docs/selinux.md](docs/selinux.md) - SELinux and the system's trusted roots
- [docs/console.md](docs/console.md) - the console
- [CONFIG.md](CONFIG.md) - dashboard settings (`/etc/plex-status/config.json`)
