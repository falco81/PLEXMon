# Instalace plexmonu

AlmaLinux / Rocky / RHEL 9 (a podobné), nginx + php-fpm před Plex Media
Serverem, Python 3.9 nebo novější (pro dálkové ovládání televizí 3.10+, typicky
`python3.12`), openssl. SELinux nevadí (soubory jsou ve /var/www a /etc/nginx).

## 1. Nová instalace i přechod ze současného plexmonu

```bash
tar xzf plexmon-2.0.0.tar.gz
cd plexmon-2.0.0
sudo ./install.sh
```

Co `install.sh` udělá na serveru se současným plexmonem (`plex-status.service`):

1. **Převezme starou službu**: `plex-status.service` zastaví a nahradí
   `plexmon.service` (jméno `plex-status.service` dál funguje - je to alias).
   Vaše limity z `plex-status.service.d/` (např. `atv.conf` s `MemoryMax=400M`)
   přenese do `plexmon.service.d/`. Python, na kterém démon běžel (`python3.12`
   kvůli pyatv), zůstane.
2. **Historie zůstane**: `/var/lib/plex-status` (SMART cache, historie grafů),
   `/etc/plex-status/config.json` (nastavení dashboardu) se nemění.
3. **Program**: `/usr/local/lib/plexmon/` (plexmon.py, plexmon_admin.py,
   plexmon_upgrade.py, release-keys/), `/usr/local/bin/plexmon`.
4. **Stránky**: `/var/www/html/smart/` - dashboard (`index.php`) na stejném
   místě, nově `settings/`, `assets/`, `lang/`.
5. **nginx** - jednou, opatrně: v `/etc/nginx/conf.d/plex.conf` přesune řádky
   `ssl_certificate`, `ssl_certificate_key`, `ssl_protocols`,
   `ssl_prefer_server_ciphers` (a další `ssl_*`, pokud tam jsou) beze změny do
   `/etc/nginx/plexmon/tls.conf` a na jejich místo dá `include`; do každého
   `server {}` přidá odpověď pro ACME (`/.well-known/acme-challenge/` ze
   `/var/www/plexmon-acme`). Nic jiného - proxy na Plex, `/smart`, omezení na
   LAN zůstávají, jak jsou. Ověří `nginx -t`; když neprojde, vrátí vše zpět.
   Kopie původního souboru je v `/var/lib/plexmon/backup/`.
6. **Certifikát**, který nginx má (`/etc/nginx/certs/server.pem` a `.key`),
   převezme do Nastavení tak, jak je - nic se nepřepisuje.
7. **První účet**: `admin` s náhodným heslem (vypíše ho a uloží do
   `/var/lib/plexmon/first-login.txt`); při prvním přihlášení si zvolíte vlastní.

Pak: `https://<server>/smart/settings/`.

## 2. Aktualizace

Ze stránek: *Nastavení → Aktualizace → Z webu → Aktualizovat na X* (stáhne
z `https://github.com/falco81/PLEXMon/tree/main/release`, ověří podpis a nasadí),
nebo přetažením balíčku. Z příkazové řádky:

```bash
plexmon --upgrade-online          # nejnovější z webu
# nebo ručně:
tar xzf plexmon-X.Y.Z.tar.gz && cd plexmon-X.Y.Z && sudo ./install.sh --upgrade
```

## 3. Ověření

```bash
plexmon --status        # Nastavení: služba, certifikát, nginx, ACME
plexmon --check         # dashboard: každý řádek "ok"
systemctl status plexmon
journalctl -u plexmon -n 50
```

## 4. Odinstalace

```bash
sudo ./install.sh --uninstall
```

Historie, `/etc/plexmon`, databáze a certifikáty nginxu zůstanou; `plex.conf`
dál obsahuje `include /etc/nginx/plexmon/*.conf` (soubory zůstávají), aby nginx
fungoval. Původní `plex.conf` je v `/var/lib/plexmon/backup/`.

## Soubory

| | |
|---|---|
| `/usr/local/lib/plexmon/` | program |
| `/var/www/html/smart/` | dashboard a Nastavení |
| `/var/lib/plexmon/plexmon.db` | databáze Nastavení (SQLite) |
| `/var/lib/plexmon/secrets.key` | klíč, kterým jsou zapečetěna tajemství (zálohujte s databází) |
| `/var/lib/plex-status/` | historie a SMART cache dashboardu |
| `/etc/plex-status/config.json` | nastavení dashboardu ([CONFIG.md](CONFIG.md)) |
| `/etc/plexmon/config.json` | výchozí hodnoty Nastavení (přebíjí je to, co se uloží na stránkách) |
| `/etc/nginx/plexmon/tls.conf`, `acme.conf` | TLS a ACME pro Plex vhost |
| `/var/log/plexmon/plexmon.log` | log Nastavení |
| `/run/plexmon/api-token` | token pro PHP stránky (čitelný skupinou php-fpm) |
