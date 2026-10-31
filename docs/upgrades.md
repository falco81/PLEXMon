# Aktualizace a vydávání verzí

## Na serveru

*Nastavení → Aktualizace*:

- **Z webu**: jednou denně (a po *Podívat se teď*) se plexmon podívá na
  `upgrade_url` (výchozí `https://github.com/falco81/PLEXMon/tree/main/release`,
  posledních deset verzí). Novější se nabídne: *Aktualizovat na X teď* ji
  stáhne, ověří a nasadí; *Jen stáhnout* ji připraví. Nic se nenasadí samo.
- **Nová verze ze souboru**: přetáhněte `plexmon-X.Y.Z.tar.gz`.
- **Návrat ke starší verzi** (`upgrade_downgrade`): jen na verzi, která je
  bezpečná (ne starší než `DOWNGRADE_FLOOR` žádné verze, která tu běžela).

Bezpečnost: démon balíček jen připraví do `/var/lib/plexmon/upgrade/` a zapíše
`request.json`; `plexmon-upgrade.path` spustí `plexmon-upgrade.service`, který
jako root (mimo `plexmon.service`) balíček zkopíruje, ověří **podpis Ed25519**
`MANIFEST.sig` klíčem z `/usr/local/lib/plexmon/release-keys/`, každý soubor
proti `MANIFEST` (SHA-256), a spustí jeho `install.sh --upgrade --yes`. Výstup
je na stránce (*Co naposledy řekl root*) a v `/var/lib/plexmon/upgrade/last.log`.

Příkazová řádka: `plexmon --upgrade-check`, `--upgrade-online [VERZE] [--yes]`,
`--upgrade-file SOUBOR`, `--upgrade-status`, `--downgrade-check`, `--downgrade VERZE`.

## Vydání nové verze (pro správce repozitáře)

1. Zvyšte verzi na třech místech: `__version__` v `plexmon_admin.py` a
   `plexmon.py`, `PM_VERSION` ve `web/plexmon-page.php`; zápis `## X.Y.Z`
   v `CHANGELOG.md`.
2. Podepište a zabalte (soukromý klíč není v repozitáři):

   ```bash
   PLEXMON_SIGNING_KEY=~/plexmon-release-1.key PLEXMON_SYNC_ROOT=1 tools/build-release.sh
   ```

   Vznikne `release/plexmon-X.Y.Z.tar.gz`, starší než posledních deset se
   smažou, a `MANIFEST` + `MANIFEST.sig` v kořeni odpovídají rozbalenému obsahu.
3. Commit a push do `main` - servery nabídnou novou verzi samy.

Veřejný klíč: `deploy/release-keys/plexmon-release-1.pub`. Nový klíč = nový
soubor `.pub` vedle; install.sh nasadí všechny.
