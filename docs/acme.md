# Automatické certifikáty (ACME)

*Nastavení → Automatické certifikáty*. Certifikát Plex serveru od Let's Encrypt
(nebo jiné ACME CA: staging, step-ca, vlastní), obnovovaný sám, když zbývá
méně než `acme_days` (30) dní - nejvýš třetina jeho života.

1. Zapněte *Certifikáty od ACME CA*, vyplňte e-mail, jméno (`acme_node_name`,
   např. `plex.falco81.net`) a způsob ověření; *Uložit změny*.
2. *Vyzkoušet port 80* (u HTTP-01), *Zkontrolovat DNS*.
3. *Objednat teď*. Průběh je vidět na kartě; *Co poslední objednávka udělala*.

## HTTP-01

CA se ptá `http://JMÉNO/.well-known/acme-challenge/...`. nginx odpovídá ze
`/var/www/plexmon-acme` (install.sh to do Plex vhostu přidal); port 80 musí
být z internetu dostupný (router přesměruje 80 na server, firewalld služba
`http`). Přesměrování na https vadit nemusí - CA ho následuje.

## DNS-01

Pro server, na který se z internetu nedostanete: CA čte TXT záznam
`_acme-challenge.JMÉNO`.

| `acme_dns` | |
|---|---|
| `desec` | deSEC.io zdarma: doména `něco.dedyn.io` + token; ve vaší DNS jednou CNAME `_acme-challenge.plex.example.cz` → na deSEC (karta *Ve vaší DNS* řekne přesně) |
| `cloudflare` | API token se Zone - DNS - Edit |
| `rfc2136` | nsupdate s TSIG klíčem (BIND, PowerDNS, Knot); `dnf install bind-utils` |
| `acmedns` | server acme-dns |
| `manual` | záznam se ukáže na stránce, vložíte ho ručně při každé objednávce |

## Co se stane s certifikátem

Certifikát od CA nahradí ten v nginx (a v Plexu, je-li `plex_cert` zapnuté).
Ten předchozí (třeba komerční) se uschová: *Zpět na ten předchozí*. Certifikát
nahraný ručně ACME nepřepíše, dokud se nedá *Starat se o něj* / *Objednat teď*.

Příkazová řádka: `plexmon --acme-status`, `--acme-probe`, `--acme-order`.
