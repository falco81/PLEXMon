# Konzole

`plexmon --console` - celý plexmon přes menu, na obrazovce serveru nebo přes
SSH (jako konzole otp). S `plexmon --console-at-login on` (nebo *O aplikaci →
Konzole po přihlášení*) se otevře sama, když se přihlásí **root** do
interaktivního shellu; `q` ji opustí do shellu, `!` otevře shell a vrátí se.

```
 plexmon 2.0.1  plex   service active   dashboard ok | Plex running | cert plex.falco81.net 61d | ACME off | 1 account(s)

  Main menu

     1  Status and checks
     2  Live log
     3  The disks and the dashboard
     4  The televisions
     5  Accounts of Settings
     6  Settings
     7  Certificates and TLS (nginx, Plex Media Server)
     8  Automatic certificates (ACME)
     9  Upgrades
    10  The service

 Up/Down move   Enter choose   Esc back   ! shell   q leave
```

Konzole převzatá z otp: otevře se na živém logu (Esc → menu), šipky (j/k) pohyb, Enter výběr, Esc zpět, číslo skočí na položku. Otázky jsou okna s poli nad menu (Nahoru/Dolů mezi poli, Vlevo/Vpravo volba, hesla jako hvězdičky), vše nevratné se ptá Ano / Ne. Seznamy (účty) se rolují a filtrují (`/`), nastavení se procházejí po skupinách i s nápovědou. Každá akce je
příkaz `plexmon ...`, který okno ukazuje v titulku - konzole neumí nic víc než
příkazová řádka. Živý log: co napíšete, to filtruje (jako `grep -i`).
Nikdy se neotevře pro příkaz přes SSH, scp, sftp ani skript; vypnout pro jedno
přihlášení: `ssh -t server PLEXMON_NO_CONSOLE=1 bash -l`.
