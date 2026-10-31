# Konzole

`plexmon --console` - celý plexmon přes menu, na obrazovce serveru nebo přes
SSH (jako konzole otp). S `plexmon --console-at-login on` (nebo *O aplikaci →
Konzole po přihlášení*) se otevře sama, když se přihlásí **root** do
interaktivního shellu; `q` ji opustí do shellu, `!` otevře shell a vrátí se.

```
 plexmon 2.0.0  plex  service active | certificate plex.falco81.net (64 days) | ACME on | 1 account(s)

  Main menu
     1  Status and checks
     2  Live log
     3  Accounts of Settings
     4  Settings
     5  Certificates and TLS
     6  Automatic certificates (ACME)
     7  Upgrades
     8  The service
     9  The disks and the dashboard
    10  The console at login
```

Šipky (j/k) pohyb, Enter výběr, Esc zpět, číslo skočí na položku. Každá akce je
příkaz `plexmon ...`, který okno ukazuje v titulku - konzole neumí nic víc než
příkazová řádka. Živý log: co napíšete, to filtruje (jako `grep -i`).
Nikdy se neotevře pro příkaz přes SSH, scp, sftp ani skript; vypnout pro jedno
přihlášení: `ssh -t server PLEXMON_NO_CONSOLE=1 bash -l`.
