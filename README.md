# plexmon

Dohled nad Plex serverem - a jeho **Nastavení** (Settings) postavené na enginu
a stránkách [otp](https://github.com/falco81/OTP).

```
https://plex.example.cz/smart/            dashboard - v LAN bez přihlášení, jako dosud
https://plex.example.cz/smart/settings/   Nastavení - jen po přihlášení
```

**Dashboard** dělá přesně to, co dělal: SMART, teploty, kapacity a opotřebení
disků (spící disk se nikdy neprobudí), co se právě přehrává, aktivita Plexu
(překódování, analýza, skenování), výkon API a reverzní proxy, historie a
trendy, ovládání televizí (Apple TV, LG, Samsung). Nově v češtině i angličtině
(přepínač EN / CS nahoře) a s odkazem na Nastavení.

**Nastavení** je administrace z otp - stejný engine, stejné stránky, stejné
chování - zúžená na to, co Plex server potřebuje:

| | |
|---|---|
| Účty | plný správce / jen čtení, hesla, vypnutí, kdo je přihlášen, kdo je zamčen, druhý krok přihlášení (ověřovací aplikace), sloupce tabulek každého účtu, export / import vlastního nastavení |
| Certifikáty a TLS | certifikát, kterým odpovídá adresa Plexu (nginx): nahrání PEM i PFX / PKCS#12, žádost (CSR) udělaná tady, podepsaný sám sebou; protokoly, šifry, DH, HSTS; vše přes `nginx -t`, při odmítnutí se vrátí původní stav. Navíc pro Plex: **Plex Media Server používá tento certifikát také** (vlastní certifikát Plexu, PKCS#12 v Preferences.xml) |
| Automatické certifikáty | Let's Encrypt nebo jiná ACME CA, HTTP-01 i DNS-01 (deSEC, Cloudflare, RFC 2136, acme-dns, ručně), obnova sama, *Ve vaší DNS*, *Vyzkoušet port 80*, *Zpět na ten předchozí* |
| Aktualizace | nová verze z webu jedním kliknutím (denně se dívá do `release/` tohoto repozitáře) nebo ze souboru; podpis Ed25519, každý soubor ověřen; návrat ke starší verzi (jen bezpečné) |
| O aplikaci | verze, na čem běží, jazyk stránek (čeština / angličtina - `lang/*.json`, upravitelné), konzole po přihlášení roota, ladicí log |
| Události | každé přihlášení, změna, certifikát, aktualizace |

SQLite (`/var/lib/plexmon/plexmon.db`), Python se standardní knihovnou, PHP
stránky, které se jen ptají démona na 127.0.0.1. plexmon nikdy neběží v clusteru.

## Příkazová řádka

Stejná jako u otp: barevně (colorama), `--json` všude, kde to dává smysl,
`plexmon --help` ve dvou sloupcích. Démona spouští systemd (`plexmon --daemon`).

```
plexmon                          co dělá (= --status)
plexmon --check                  kontrola všeho: disky, SMART, API, nginx, certifikát
plexmon --console                vše přes menu (živý log, účty, certifikáty, upgrady)
plexmon --wake | --refresh | --diag | --atv ...   dashboard a televize, jako dosud
plexmon --users                  účty Nastavení
plexmon --add-user jan --role fulladmin
plexmon --cert-status            certifikát nginxu
plexmon --cert-import cert.pem key.pem chain.pem
plexmon --tls-preset intermediate --tls-hsts 31536000
plexmon --set acme=1 --set acme_node_name=plex.example.cz
plexmon --acme-order             objednat certifikát hned, sledovat průběh
plexmon --plex-cert on           Plex Media Server dostane certifikát také
plexmon --upgrade-online         nejnovější verze: stáhnout, ověřit, nasadit
plexmon --help                   všechno
```

## Instalace a přechod ze současného plexmonu

[INSTALL.md](INSTALL.md). Krátce:

```bash
tar xzf plexmon-2.0.0.tar.gz && cd plexmon-2.0.0
sudo ./install.sh            # i přes stávající plexmon (plex-status.service) - převezme ho
```

Balíčky jsou v [release/](release/) (posledních deset); obsah posledního je
rozbalený v kořeni tohoto repozitáře.

## Dokumentace

- [docs/settings.md](docs/settings.md) - Nastavení: účty, certifikáty, Plex, jazyky
- [docs/acme.md](docs/acme.md) - automatické certifikáty (HTTP-01 a DNS-01)
- [docs/upgrades.md](docs/upgrades.md) - aktualizace z webu, podpis, návrat zpět, vydání verze
- [docs/console.md](docs/console.md) - konzole
- [CONFIG.md](CONFIG.md) - nastavení dashboardu (`/etc/plex-status/config.json`)
