# Upgrades and releasing versions

## On the server

*Settings → Upgrades and maintenance*:

- **From the web**: once a day (and after *Look now*) plexmon looks at
  `upgrade_url` (default `https://github.com/falco81/PLEXMon/tree/main/release`,
  the last ten versions). A newer one is offered: *Upgrade to X now*
  fetches it, checks it and puts it in; *Only fetch it* gets it ready. Nothing is put in by itself.
- **A new version from a file**: drop `plexmon-X.Y.Z.tar.gz` on it.
- **Going back to an older version** (`upgrade_downgrade`): only to a version that is
  safe (not older than the `DOWNGRADE_FLOOR` of any version that ran here).

Security: the daemon only gets the package ready in `/var/lib/plexmon/upgrade/` and writes
`request.json`; `plexmon-upgrade.path` starts `plexmon-upgrade.service`, which
as root (outside `plexmon.service`) copies the package, checks the **Ed25519 signature**
`MANIFEST.sig` with a key from `/usr/local/lib/plexmon/release-keys/`, every file
against `MANIFEST` (SHA-256), and runs its `install.sh --upgrade --yes`. The output
is on the page (*What root said last*) and in `/var/lib/plexmon/upgrade/last.log`.

Command line: `plexmon --upgrade-check`, `--upgrade-online [VERSION] [--yes]`,
`--upgrade-file FILE`, `--upgrade-status`, `--downgrade-check`, `--downgrade VERSION`.

## Releasing a new version (for the repository's maintainer)

1. Raise the version in three places: `__version__` in `plexmon_admin.py` and
   `plexmon.py`, `PM_VERSION` in `web/plexmon-page.php`; an entry `## X.Y.Z`
   in `CHANGELOG.md`.
2. Sign and package (the private key is not in the repository):

   ```bash
   PLEXMON_SIGNING_KEY=~/plexmon-release-1.key PLEXMON_SYNC_ROOT=1 tools/build-release.sh
   ```

   This makes `release/plexmon-X.Y.Z.tar.gz`, those older than the last ten are
   deleted, and `MANIFEST` + `MANIFEST.sig` in the root match the unpacked content.
3. Commit and push to `main` - the servers offer the new version by themselves.

The public key: `deploy/release-keys/plexmon-release-1.pub`. A new key = a new
`.pub` file next to it; install.sh puts them all in.

## Tasks for the server

Most of an upgrade is done by root's helper (`install.sh --upgrade`) - from Settings → Upgrades
too. What it does not do, or must not do by itself - a SELinux policy, the firewall, nginx's own
file, a restart, a reboot - a version declares as a **task for the server**:

- the card **To be done by hand** at the top of Settings → Upgrades: a band for each task that
  is open - red with a cross when something does not work until it is done, amber with a flag
  otherwise - with why, what to type on the server as root (with a Copy), how the band goes
  away and since when it has been open; the card has a red edge while anything is open and says
  *Nothing is to be done by hand* when nothing is. Below the bands, **Done lately**: the last ten
  tasks done, taken back or gone by themselves - what, how, who and when, with *Not done after
  all* on one said done. The Settings tab and Upgrades in its menu carry a red count;
- the command line: `plexmon` says them first, `plexmon --tasks` lists them (exit 1 while one
  that is needed now is open), an event is written when one comes and when it is done.

A task plexmon can check (the version on the disk the one running, the upgrade helper there)
goes away by itself once it is done - looked at every 15 s, or at once with *Look again*; *Done*
keeps it away until the check no longer finds it (and it is shown again if it comes back later).
One it cannot see from here stays until a full administrator says *Done* (or
`plexmon --task-done ID`; `--task-undo ID` shows it again).

For a developer: a task is one entry in `SERVER_TASKS` in `plexmon_admin.py` - `id`, `since`,
`level` (`crit`, `warn`, `info`), `title`, `why`, `commands`, and either `check(store)` (returns
what it found while there is something to do, else `None`) or none (a step said done by hand);
`when(store)` limits it to the machines it concerns (e.g. only where SELinux is enforcing).

