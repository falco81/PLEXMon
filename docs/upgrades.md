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
