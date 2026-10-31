# Instalace plexmonu

AlmaLinux / Rocky / RHEL 9 (a podobné), nginx + php-fpm před Plex Media
Serverem, Python 3.9 nebo novější (pro dálkové ovládání televizí 3.10+, typicky
`python3.12`), openssl. SELinux nevadí (soubory jsou ve /var/www a /etc/nginx).

## 1. Nová instalace i přechod ze současného plexmonu

```bash
tar xzf plexmon-X.Y.Z.tar.gz
cd plexmon-X.Y.Z
sudo ./install.sh
```

Co `install.sh` udělá na serveru se současným plexmonem (`plex-status.service`):

1. **Převezme starou službu**: `plex-status.service` zastaví a nahradí
   `plexmon.service` (jméno `plex-status.service` dál funguje - je to alias).
   Vaše limity z `plex-status.service.d/` (např. `atv.conf` s `MemoryMax=400M`)
   přenese do `plexmon.service.d/`.
   **Python** (od 2.2.0): démon přejde na nejnovější Python, který AlmaLinux 9
   má v AppStreamu - 3.14, 3.13 nebo 3.12 (`dnf install python3.X
   python3.X-pip`), vedle systémového `python3`, na kterém běží dnf a který
   se nikdy nemění. Knihovny ovládání televizí (aiowebostv, samsungtvws,
   colorama), které měl dosavadní Python, se pro nový nainstalují pipem
   předem; démon přejde, jen když se v něm všechno potřebné naimportuje -
   jinak zůstane na dosavadním. `PLEXMON_KEEP_PYTHON=1 ./install.sh --upgrade`
   Python nechá, jak je. pyatv (Apple TV) má vlastní virtualenv a nemění se.
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
7. **PHP a php2shell** (CVE-2026-109618, CVE-2026-109619 - vzdálené spuštění
   kódu, opraveno 21. 10. 2026) - při každé instalaci i aktualizaci (i té ze
   stránek) přejde PHP na **větev 8.3, nejméně 8.3.36**: starší PHP (výchozí
   8.0, 8.1, 8.2) se přepne na `php:8.3` (`dnf module reset/enable`,
   `distro-sync 'php*'`), PHP na 8.3 se aktualizuje na nejnovější build,
   novější opravené (8.4) zůstane. php.ini, php.d a pooly php-fpm zůstanou (a předtím se
   zkopírují do `/var/lib/plexmon/backup/php-*`); php-fpm se ověří (`php-fpm
   -t`) a restartuje - když na 8.3 nenaběhne, vše se vrátí na původní větev. Kde AppStream opravené PHP nemá, nabídne PHP
   8.4 z repozitáře Remi (jen na dotaz nebo `--remi`, nikdy samo při
   aktualizaci). Navíc druhá vrstva: `/etc/php.d/99-php2shell.ini` a nginx
   odmítá formulářové a multipart tělo stránkám pod `/smart/`
   (`/etc/nginx/plexmon/php-guard.conf`, ověřeno `nginx -t`). Volby:
   `--php-stream X.Y`, `--remi`, `--no-php-update`.
8. **První účet**: `admin` s náhodným heslem (vypíše ho a uloží do
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

## Čas, restart, aktualizace systému, databáze, zálohy (od 2.2.0)

V Nastavení ve skupinách **Stroj** a **Data** - stejně jako v otp:

- **Čas** - chrony: odchylka, servery, vlastní NTP servery (blok v
  `/etc/chrony.conf`, původní řádky jen zakomentované), časové pásmo.
- **Restart a vypnutí** - ze stránky nebo podle plánu (jen když to aktualizace
  potřebuje: `needs-restarting -r`).
- **Aktualizace systému** - dnf, vše nebo jen bezpečnostní, výstup živě.
  Balíček `plexmediaserver` se **vynechává** (`--exclude=plexmediaserver*`),
  dokud se nezapne `osup_plex`.
- **Databáze** - databáze Plex Media Serveru: kopie za běhu Plexu (online
  backup SQLite, do souboru Plexu se nic nezapisuje), ověření Plex SQLite
  (`integrity_check`), kopie v `/var/lib/plexmon/plex-db` (nebo `plex_db_dir`),
  stažení, **vrácení** (Plex se zastaví, jeho soubory se odloží do
  `before-restore-…` - nic se nemaže - kopie se vloží s vlastníkem Plexu, Plex
  se spustí; při chybě se vše vrátí). Vlastní zálohy Plexu
  (`com.plexapp.plugins.library.db-YYYY-MM-DD`) jdou vrátit také. Optimalizace
  a čištění bundlů přes API Plexu. Vlastní timer serveru (např.
  `plex-db-backup.timer`) se zobrazí a jde vypnout. A databáze plexmonu:
  kopie, kontrola, VACUUM, mazání událostí, vrácení.
- **Zálohy** - na jiný stroj (složka, FTP, FTPS, SFTP, SCP, SMB), šifrované
  AES-256 heslem `rbackup_password`, se zkouškou obnovy. Záloha obsahuje
  plexmon a s `rbackup_plex_db` i ověřenou kopii databáze Plexu a jeho
  Preferences.xml; `RESTORE.md` v každé záloze popisuje obnovu na čistém stroji.

Příkazová řádka: `plexmon --db`, `--plex-db-copy`, `--plex-db-restore JMÉNO`,
`--backup-now`, `--backup-verify`, `--os-updates`, `--os-update security`,
`--time`, `--reboot` … (`plexmon --help`), konzole menu 12.

## Údržba (od 2.3.0)

Nastavení → Aktualizace a údržba → **Údržba**: server je vyřazený z provozu.
plexmon pak nic nedělá sám - žádné zálohy, kopie databází, aktualizace
systému ani restarty podle plánu, žádné obnovení certifikátu, žádné pravidelné
čtení SMART z disků (hodí se např. při výměně disku). Stránky, dashboard a vše
ruční funguje dál. Aktualizace ze stránek server do údržby dá sama a po
doběhnutí ho vrátí.

```bash
plexmon --maintenance on --why "výměna disku"
plexmon --maintenance off
plexmon --maintenance status
```

## Proxy (od 2.4.0)

Nastavení → Proxy: když server smí na web jen přes HTTP proxy. Přes ni jdou
nové verze, certifikáty od ACME CA, Plex Discover a hodnocení a (volitelně)
dnf. „Vyzkoušet“ ukáže každý krok cesty. Na příkazové řádce:
`plexmon --proxy-test https://github.com/`.

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
