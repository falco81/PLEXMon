# Nastavení (Settings)

`https://<server>/smart/settings/` - administrace převzatá z otp: stejné
stránky, stejné ovládání (záhlaví, záložky, lišta vlevo, okna, lišta *Uložit
změny* dole). Rozhoduje vždy démon; PHP stránky se ho jen ptají na 127.0.0.1.

## Účty

- **plný správce** smí vše, **jen čtení** se jen dívá.
- Heslo nastavené jiným si člověk při prvním přihlášení změní.
- *Druhý krok přihlášení*: `admin_2fa` (Účty → nastavení dole) - kód
  z ověřovací aplikace; reset: tlačítko u účtu nebo `plexmon --reset-2fa JMÉNO`.
- *Přihlášeni teď* - ukončení přihlášení jinde; *Zamčení* - adresy a jména
  s chybnými pokusy, uvolnění jedním kliknutím.
- *Sloupce tabulek* - každý si rozloží tabulky po svém (vaše jméno → Upravit
  sloupce); export / import vlastního nastavení jako JSON.

## Certifikáty a TLS

Karta certifikátu serveru: kdo ho vydal, do kdy platí, jména (SAN), řetěz,
kořen, stav v nginx. Tlačítka:

| | |
|---|---|
| Nahrát | PEM (certifikát, klíč, řetěz, kořen - v libovolném pořadí) nebo PFX / PKCS#12 s heslem |
| Žádost (CSR) | klíč se vyrobí tady a zůstane tady; žádost jde k vaší CA, nahrajete jen certifikát |
| Podepsaný sám sebou | na zkoušku nebo do příchodu pravého |
| Nastavení TLS | Standard (TLS 1.2 + 1.3, ECDHE), Mozilla intermediate / modern, vlastní; DH parametry; server volí šifru; HSTS |
| Stáhnout | certifikát s řetězem (bez klíče) |
| Pustit | plexmon se o TLS přestane starat, nginx si nechá soubory |

Certifikát se zapíše do souborů, které jmenuje Plex vhost
(`/etc/nginx/certs/server.pem` + `.key`), nastavení TLS do
`/etc/nginx/plexmon/tls.conf`; nginx se zeptá (`nginx -t`) a reloadne; když
odmítne, vše se vrátí.

### Plex Media Server používá tento certifikát také

Plex Media Server (port 32400) má vlastní certifikát `*.plex.direct`. Se
zapnutým `plex_cert` dostane i certifikát plexmonu jako *custom certificate*:
`/var/lib/plexmediaserver/plexmon/plexmon.p12` (heslo náhodné, zapečetěné),
v `Preferences.xml` se nastaví `customCertificatePath`, `customCertificateKey`
a `customCertificateDomain` (doména = `plex_cert_domain`, jinak CN certifikátu).
Plex certifikát čte jen při startu: s `plex_cert_restart` (výchozí) se restartuje
(přehrávání se na chvíli zastaví). Každý další certifikát - i obnovený přes
ACME - jde do Plexu také. *Plex zpět na vlastní* jména z Preferences.xml odebere.
V Plexu pak nastavte *Custom server access URLs* na `https://plex.example.cz:32400`.

## Jazyky

Stránky jsou psané anglicky; `lang/cs.json` (`/var/www/html/smart/lang/`) říká,
co je která věta česky - stejný engine jako isemon a otp. Výchozí jazyk:
*O aplikaci → Jazyk stránek* (`ui_lang`, i pro dashboard); každý si může
zvolit vlastní (vaše jméno → Jazyk, na dashboardu přepínač EN / CS).

Soubor jde upravit: měňte jen `text`, nikdy `source`; prázdný `text` = anglicky.
`{0:jeden|dva až čtyři|pět a víc}` vybere český tvar podle čísla. Nový jazyk =
nový soubor se svým `code`; v nabídkách se objeví sám.
