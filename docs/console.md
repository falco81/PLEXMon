# Console

`plexmon --console` - the whole of plexmon through menus, on the server's screen or over
SSH (like the otp console). With `plexmon --console-at-login on` (or *About →
The console at login*) it opens by itself when **root** signs in to an
interactive shell; `q` leaves it for the shell, `!` opens a shell and comes back.

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

The console taken over from otp: it opens on the live log (Esc → menu), the arrows (j/k) move, Enter chooses, Esc goes back, a number jumps to that item. Questions are windows with fields above the menu (Up/Down between the fields, Left/Right a choice, passwords as asterisks), everything that cannot be undone asks Yes / No. Lists (accounts) scroll and filter (`/`), the settings are gone through by group, with their help. Every action is
a `plexmon ...` command, which the window shows in its title - the console can do nothing more than
the command line. Live log: what you type filters it (like `grep -i`).
It never opens for a command over SSH, scp, sftp or a script; to switch it off for one
sign-in: `ssh -t server PLEXMON_NO_CONSOLE=1 bash -l`.
