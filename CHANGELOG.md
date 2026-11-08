# plexmon - what changed

Every version is a package in `release/` (the newest ten), signed with the
release key; Settings → Upgrades offers a newer one by itself. The entries are
read by Settings → Upgrades (*What changed*), so each starts `## X.Y.Z`.

## 2.9.1 - 2026-11-08

- **Fixed: "Let the kernel start SELinux" was shown where it must not be.** A machine switched
  off by `selinux=0` on the kernel's command line alone usually keeps `SELINUX=enforcing` in
  `/etc/selinux/config`; the task told to take `selinux=0` away, and the machine started
  enforcing on files never labelled - Plex did not start. The task now comes only once plexmon
  made SELinux ready itself (permissive, every file to be labelled at the next start), and the
  card warns against taking `selinux=0` away while the config says enforcing.
- The card and the task say that the first start with SELinux labels every file and takes a
  long while (up to tens of minutes), then restarts the machine once more.

## 2.9.0 - 2026-11-08

- **Settings → SELinux** (as otp 1.0.265): the mode now and after a restart, what plexmon
  needs of it - its module `plexmon_server` (the API token's folders labelled for the pages),
  `httpd_can_network_connect` (nginx passing Plex on), the ports nginx listens on - and what
  SELinux refused. The way to enforcing step by step: *Make it ready (permissive)*, *Put in
  what plexmon needs*, *Switch to enforcing* (only once nothing is missing and nothing was
  refused), *Back to permissive*. Nothing changes unless asked; disabled stays disabled.
- **The data disks are never relabelled**: before the first step from disabled their mount
  points go into `/etc/selinux/fixfiles_exclude_dirs`, so the relabelling at the next start
  does not wake them or read them.
- The checks read only the system's disk, at the lowest priority, every 30 minutes.
- `selinux=0` on the kernel's command line is a task in *To be done by hand* (grubby), gone by
  itself once the kernel starts SELinux.
- *Download the report* / `plexmon --selinux-report`; `plexmon --selinux-check`,
  `plexmon --selinux STEP`.
- **Trusted roots of the system** (Certificates and TLS): certificate authorities of your own
  put into the trust of the whole operating system by its own way (update-ca-trust,
  update-ca-certificates) - the distribution's list untouched, only `plexmon-<sha>` files ever
  written; refused unless it is an authority, warnings for what the crypto policy refuses.
  `plexmon --os-roots`, `--os-roots-add FILE`, `--os-roots-remove SHA256`.
- install.sh: the SELinux tools and auditd where SELinux is present, the module put in when it
  changed, the labels of the pages; `--uninstall` takes the module out. `plexmon.service` may
  write `/etc/selinux` and the folders of the system's certificate authorities.

## 2.8.4 - 2026-11-06

- **Clear list** at the right of *Done lately*: empties exactly the rows the
  card shows - one come meanwhile stays, what is still to be done is never
  touched; an event says who cleared how many. `plexmon --tasks-clear`
  empties all of it from the command line.
- Tasks for the server are a full administrator's only: `/api/tasks` asks
  for one, and `/api/status` no longer sends them to anyone else.

## 2.8.3 - 2026-11-06

- **Tasks for the server look like upsmon's "To be done by hand"**: one card
  at the top of Settings → Upgrades, red-edged while a task is open, a band
  each (a cross when needed now, a flag otherwise) with the command, how it
  goes away and since when it is open; *Look again* and *Done* at the right -
  Done now also for a task plexmon checks (kept away until it is gone).
- **Done lately**: the last ten tasks done, taken back or gone by themselves,
  what, how, who and when, with *Not done after all*.
- The count is a red badge over the Upgrades icon of the menu, shut or open.
- *Not now* is gone - the card lives on Upgrades only.

## 2.8.2 - 2026-11-06

- **Tasks for the server are on Settings → Upgrades**, not at the top of every
  page: a band each at the top of Upgrades while one is to do, and a badge
  with how many on the Settings tab and on Upgrades in its menu (red when one
  is needed now). The card listing every task, in order or done, is gone -
  `plexmon --tasks` lists them.
- The alerts at the top of the pages stay as they were - and *nginx is not
  plexmon's yet* is one of them again (2.8.0 had made it a task).

## 2.8.1 - 2026-11-06

- **Tasks for the server: a band each, as isemon draws its own** - the mark, what
  to do and why (and what plexmon found), *As root, on the server:* with the
  commands and a *Copy*, and how it goes away (by itself within 15 s once it is
  right, or *Done* for a step nobody can see from here). At the right *Open it*
  (the list on Settings → Upgrades) and *Not now*: put away in this browser for
  a day - the list still has it, and it comes back if it is still to do. Red
  for one needed now, amber for the others.

## 2.8.0 - 2026-11-06

- **Tasks for the server** - one way for every version to say what only root
  can do on the machine and it must not do by itself (a SELinux policy, the
  firewall, nginx's own file, a restart):
  - a band at the top of every page of Settings while one is to do - red when
    something does not work until it is done - with why, what to type on the
    server (with a Copy) and the version that brought it;
  - the whole list in Settings → Upgrades → *Tasks for the server*;
  - `plexmon` says them first, `plexmon --tasks` lists them (exit 1 while one
    needed now is open); an event when one comes and when it is done.
  - A task plexmon can check goes away by itself once it is done (every 15 s,
    or *Check again*); one it cannot see stays until a full administrator says
    *Done* (`plexmon --task-done ID`, `--task-undo ID`).
  - The first ones: *Let plexmon look after nginx's TLS* (`plexmon
    --nginx-adopt`; it was a line among the troubles), *Restart plexmon* (a
    newer plexmon on the disk than the one running), *Upgrade once on the
    command line* (root's helper for upgrades from the pages missing).
  - For a later version a task is one entry in `SERVER_TASKS`
    (docs/upgrades.md).

## 2.7.9 - 2026-11-06

- **The performance meter is there the moment a page opens**: the page comes
  with the meter's last state - the two minutes of the graphs (the daemon's, or
  its row in the database), the last numbers and the last details of a panel -
  and draws them at once; the next reading only brings them up to date. Before,
  the header stayed empty until the first answer and a panel said "reading…".
  Nothing is read anew for it.
  - Measured: back on a page, the numbers and graphs stood there in 0.12 s
    with every question to the meter held back 4 s; a panel opened with its
    cores at once.
  - The graphs move together: one reading stands at the same share of every
    graph - the four of the header and the panel's.

## 2.7.8 - 2026-11-05

- **A new icon**: a chevron in Plex's amber beside the green bars of a load
  meter, on Plex's dark grey - the Plex server, watched. The tab of the
  browser (`favicon.svg`), the icon on the home screen of an iPad or an iPhone
  (180 px) and those of the manifests (192, 512 px; square, no transparency -
  iOS rounds the corners itself), on the dashboard and on Settings. The
  addresses of the icons carry their date, so a browser shows the new one at
  once; an icon already on the home screen stays as it was until it is added
  again.

## 2.7.7 - 2026-11-05

- **The performance meter's graphs move smoothly** - evenly, all the time,
  with no jump when a reading comes:
  - the SVG animation of 2.7.6 (SMIL) did not move in Chromium at all, so the
    line stood still and jumped a step every 5 s; it is a CSS animation of
    each graph now (still one a graph), linear;
  - a point stands where its time is now, against the daemon's clock (each
    answer says it): a graph drawn again stands exactly where the moving one
    was, whenever the reading came - late, early, or with nothing new;
  - a new reading comes in from beyond the right edge and slides in, instead
    of appearing at the edge at once.
  - Measured: the line moves at 0.49 px/s in the header (two minutes across),
    and across a redraw by 0.01-0.11 px - before, 2.4 px every 5 s.

## 2.7.6 - 2026-11-05

- **The performance meter's graphs move by one SVG animation each**: a graph
  is drawn once a reading comes and moved one step to the left in the 5 s
  until the next one - nothing is redrawn by a timer. Each point stands at its
  own time; where readings are missing (the meter was closed for a while, the
  daemon started again) the line breaks instead of drawing them as one step.
- **The daemon samples, and only while somebody looks**: one reading of the
  kernel's counters every 5 s, the last two minutes kept in **one small row of
  this node in the database**, written over each time - only while a page with
  the meter is open (it asks every 5 s); **45 s after the last page is closed
  the sampling ends by itself**. A page loaded again (F5), another tab, another
  device or the daemon started again draw the same graphs at once. Nothing else
  is read or written; the details are read only while their panel is open, and
  nothing at all while the meter is off.

## 2.7.5 - 2026-11-05

- **The performance meter switched off is off altogether** (Settings → About →
  *The performance meter in the header*, off by default): the pages load
  nothing of it - no script, no style, no element in the header, no timer -
  ask the daemon nothing, and the daemon reads nothing for it. Before, its
  script and style were loaded on every page and a timer looked every 3 s
  whether it had been switched on.
  - Switched on while a page is open, the page loads it within seconds (with
    its next look at the state); switched off, it is stopped there and taken
    off the page - without reloading it.

## 2.7.4 - 2026-11-05

- **The performance meter: the memory the processes take is measured against
  the memory in use**, not all of it - against 31 GB, Plex Media Server's 125 MB
  was no bar at all beside "125 MB". The bars at 0 % (2.7.2) are checked in
  every look of the header (graphs, pills, line, rings) and every style of the
  details (tiles, table, map, wide) for the processor, the memory, the disks
  and the network: all empty.

## 2.7.3 - 2026-11-05

- **The performance meter has the temperature of every core**: Intel's
  sensors (coretemp) give each physical core's ("Core N" of a package), and it
  was put to the logical cpu of the same number - with hyperthreading (2 cores,
  4 threads) or core ids with gaps (0, 1, 4, 5…) half the rows had none. Each
  cpu now takes its core's by the kernel's topology (`core_id`, the package).
  AMD's sensors give the whole processor's only, as before.

## 2.7.2 - 2026-11-05

- **The performance meter's bars say what their numbers say**:
  - at 0 % no bar - a sliver of 1-2 % stood there with nothing busy (the
    cores, the disks, the filesystems, the pills in the header), and a ring
    drew a dot (its round end);
  - the processes taking most: the bar is the share itself - of one core
    (or of the busiest, when it takes more than one), of all the memory -
    not of the busiest process: one at 0 % drew a third of a bar beside
    "0 %".

## 2.7.1 - 2026-11-05

- **The performance meter never wakes a sleeping disk** (as nothing in plexmon
  does - its very purpose):
  - the `drivetemp` sensors are never read: their reading is an ATA command
    sent to the disk itself - through a USB-SATA bridge too - and can spin a
    sleeping disk up (ai's meter read every hwmon sensor, and offered
    `modprobe drivetemp` for the temperatures of SATA disks);
  - a disk's temperature is now the one plexmon's own SMART read gave - read
    only while the disk is turning anyway - and a disk plexmon sees asleep is
    shown as *asleep*, nothing read from it;
  - free space is asked only of the kernel's own filesystems (ext4, xfs, btrfs,
    zfs, f2fs, exfat, ntfs3…), which answer from memory, as the dashboard asks
    them already (lsblk) - never of FUSE (mergerfs, ntfs-3g: a program that may
    read the disks), a network share (NFS, CIFS: it may hang the daemon) or FAT
    (it may count its clusters on the disk);
  - checked with strace: the meter reads only `/proc` and `/sys` counters and
    the filesystems' `statfs`; no command goes to any disk.
- The dashboard's processor temperature, where the machine has no thermal zone
  and it falls back to the hwmon sensors, passes over `drivetemp` too.

## 2.7.0 - 2026-11-05

- **The performance meter** - ai's server load (1.0.72-1.0.76) in the header,
  **off by default** (Settings → About → *The performance meter in the header*):
  - the processor, the memory, the disks and the network every 3 s, each with
    its place fixed (nothing jumps), green / yellow from 70 % / red from 90 %;
  - pointed at - tapped on an iPad - a number opens its details: each core
    (busy, GHz, temperature), the sensors, each disk (read, write, busy,
    temperature), the filesystems, each network card, the processes taking most
    processor and memory; Esc, moving away or a tap elsewhere closes it;
  - in *Your account → The performance meter* each full administrator picks
    the look of the numbers (a graph of the last two minutes, pills, one line,
    rings, not shown) and of the details (tiles, a table as htop, a map of the
    cores with rings, one wide panel, none) from cards drawn by the same code;
    kept with the account;
  - **a page loaded again (F5) goes on with its graphs** - the server keeps the
    last two minutes of what it read (40 readings) and hands them to the first
    question of a page, so a new tab or another device has them at once too;
  - only a full administrator sees it and may ask (`GET /api/load`, refused
    while it is off); it reads `/proc` and `/sys` only when a page asks -
    nothing in the background, one reading a second for every page, nothing
    while the page is hidden or paused, the details only while a panel is open.

## 2.6.9 - 2026-11-05

- **Sign-in with a passkey - Face ID, Touch ID, Windows Hello** (WebAuthn, as in
  ai), **off by default** (`passkeys`, Settings → Accounts):
  - *Your account → Passkeys*: *Add a passkey of this device* (the iPad asks for
    the face, the passkey goes into the iCloud Keychain), the list with its
    name, the name of the server it is for, added, last used; rename it, take
    it away. A full administrator sees and takes away anybody's (the key button
    beside the account - a lost iPad).
  - The sign-in page offers *Sign in with a passkey* - only while it is on, an
    account has one and the browser can (HTTPS, the pages opened by their name).
  - With the second step on, a passkey checked by the face counts as both
    (`passkey_is_2fa`, on by default); off, the code is asked for after it.
  - The server checks everything WebAuthn says to: the challenge (once, 5
    minutes, of its kind, for this name and account), the origin, the name's
    hash, the user present **and verified** (face, finger, PIN), the signature
    (ES256, RS256 with openssl, EdDSA), the counter, a switched-off account;
    failed tries count as wrong passwords do; every add, removal, sign-in and
    refusal is an event. It keeps only the public half; 20 passkeys an account.
  - `plexmon --passkeys [NAME]`, `plexmon --passkey-forget ID|all`.
  - A password signs in exactly as before.
- **On an iPad**:
  - *Add to Home Screen*: plexmon's own icon (180/192/512 px) and a window of
    its own without Safari's bars, turning with the iPad (`manifest.json`;
    the dashboard as *Plex status*). Nothing changes in a browser.
  - A tap on a button that explains itself works the first time - Safari took
    the first tap for showing its words; with a finger the words come by
    holding it.
  - The counts of the library open their card with a tap (and close with
    another, or a tap anywhere else), not by pointing; the track under the
    count is not picked by it.
  - A popup is never taller than what the keyboard on the screen leaves;
    finger-sized icon buttons; the rating and the shade of a poster shown
    without pointing at it; lists that scroll by a finger keep the page still.
  - An iPad with a trackpad or a mouse points as a PC does.
- **`plexmon --set` reaches the running service within seconds** - before, a
  setting changed on the command line was taken by the service only when it
  restarted (`--get` and the pages already showed it as saved).

## 2.6.8 - 2026-11-05

- **The tabs in the order Plex library · Events · Settings** - Settings always
  the last one, at the right.

## 2.6.7 - 2026-11-05

- **The buttons of a popup's step are in its bottom bar, beside Close** - not
  at the end of what scrolls, where a long list of tracks pushed them out of
  sight:
  - *Audio and subtitles*: *Scan the files again* and *Show the plan*; in the
    plan *Back* and *Set it on N videos*;
  - *Ratings*: *The sources…* and *Look them up*; in the preview *Back* and
    *Write N ratings* (the two switches stay above, with what they are for);
  - *Choose your own password first*: *Change the password* and *Sign out*.
  - While a step runs (a scan, a lookup) its buttons are away and only *Stop*
    and *Close* are there; the next step puts its own. Every other popup had
    its buttons there already.

## 2.6.6 - 2026-11-05

- **Where yes and where no, under the pointer** - a count in the Plex library
  opens a card that says which ones: the episodes (S01E03 and the title, with
  its still) or the titles (with the poster), in groups - *has it* / *does not
  have it*. It stays while the pointer is over it, so a long list can be
  scrolled; Escape or moving away closes it.
  - **Audio and subtitles**: *in 10 of 12* of every track, *default in 1*
    (plays by default / there, but another one plays / not there), *2 of 12 do
    not have it*, and in the plan *without that audio* / *without those
    subtitles*.
  - **Labels** of the ticked titles: *3 of 8* of each label - which carry it.
- **Scan the files again counts anew**: after Plex has scanned, the episodes
  of what was picked are read again as they are now (an episode Plex took in
  as new, or let go, counts), and every count is made anew from them. A track
  is counted once a video - before, once a part, so a video with two files
  (a new one while the old one is still in Plex's trash) counted twice - and
  *default in* is counted again too (it stayed as it was).

## 2.6.5 - 2026-11-04

- **`plexmon --get KEY`** - one setting, as `--set KEY=VALUE` changes it:
  `key = value  (where it comes from)`; as often as you like
  (`--get proxy --get backup_keep`), `--json` for a script (the key, the value,
  the default, where it comes from). A password or a key only says *set* or
  *not set*, never itself. A key this version does not know is refused with
  the nearest ones it does ("did you mean proxy_password?").
- **`plexmon --settings` lists every setting** - it showed 43 of the 93: the
  proxy, restart and power off, system updates, the database, Plex's database,
  backups and the Plex library were left out of the text (the `--json` had
  them). The console's *Settings → Browse and change* left out the same ones,
  and now has them too.

## 2.6.4 - 2026-11-03

- **Upload takes a ZIP** - the one *Download → Both certificates* gives, or
  any ZIP of certificate files: its PEM files and its `.pfx` / `.p12` files
  are taken as if they had been chosen one by one, so a dual certificate
  downloaded from one server goes into another as it is (the PFX files'
  password in *Password*). Also `plexmon --cert-import both.zip`.
  - Opened in memory by the daemon, nothing written to the disk; at most 20
    files in it, 200 kB each, 1 MiB in all; a ZIP locked with a password, one
    with no certificate in it, or one that cannot be read is refused, said.
  - Two PFX files at most in all (RSA and ECDSA), with or without a ZIP.

## 2.6.3 - 2026-11-03

- **A dual certificate (RSA + ECDSA) can be downloaded and uploaded whole.**
  Before, Download gave only the main (RSA) certificate and its key - the
  ECDSA one beside it, and its key, never left the server - and Upload took
  one key only ("more than one private key was sent").
  - **Download**: for a dual certificate a new choice, *Both certificates*:
    the RSA and the ECDSA certificate, each with its own chain (and its own
    private key, in the formats that have one), in one ZIP
    (`NAME-rsa.pfx` + `NAME-ecdsa.pfx`, `NAME-rsa-fullchain.pem` + …). The
    same rules as for one: the key only to a full administrator with their
    own password, the same file password for both, every export an event.
  - **Upload**: both pairs at once - PEM with the two certificates, the two
    keys and their chains in any order, or two PFX / PKCS#12 files with one
    password. RSA is the main one, ECDSA goes beside it (as Automatic
    certificates keep their second one); each gets its own chain, issuer by
    issuer. Refused, with words: two keys of the same kind, other names, a
    key without its certificate, more than two keys.
  - The command line too: `plexmon --cert-import rsa.pfx ecdsa.pfx`, or the
    PEM files of both.
  - One certificate is uploaded and downloaded exactly as before.

## 2.6.2 - 2026-11-01

- **Certificates from acme looks exactly as in ai** (1.0.60): the state in the
  card's title (on - paired / on - not paired yet / off), the two switches side
  by side with their words under them, the address for acme, this server's id
  and its sealing key as tiles - a click copies what is in one - the pairing
  code large in its popup (a click copies it), *Or paste acme's key…* with a
  name for the key, the paired acmes and what acme sees here as the pages'
  usual tables, and the last push under them.
  - The switches and *Forget it* still wait for **Save the changes** at the
    bottom; a key pasted is taken by its popup's *Take this key*, as in ai.
  - The last push says what was done ("put in for node:… / already there").

## 2.6.1 - 2026-11-01

- **Certificates from acme: the changes wait for Save the changes** at the
  bottom, as every other setting does - nothing on the card is done the moment
  it is clicked any more:
  - *Take certificates from acme* and *Take certificates of other names*,
    switched, are shown as changed until saved;
  - a key pasted (*Or paste acme's key*) is listed as "taken once the changes
    are saved" (*Leave it* drops it), an acme to forget is struck through
    (*Keep it* keeps it);
  - *Put them back* throws the changes away. *Pair with an acme* still shows
    its code at once - the code is what is given to acme.

## 2.6.0 - 2026-11-01

- **Certificates from acme (acme push)** - a new card on Settings →
  Certificates and TLS, **off by default**: switched on, *acme* - the
  certificate manager - puts its certificates in here and renews them by
  itself (acme push protocol 1, plexmon as the receiver;
  [docs/acme-push.md](docs/acme-push.md)).
  - **Pair with an acme** shows a one-time code (15 minutes, 5 tries), or
    **Or paste acme's key**; the acmes paired are listed with **Forget it**.
  - Every request is signed (Ed25519, time within 300 s, nonces kept in the
    database); the certificate's private key arrives sealed to this
    installation's own X25519 key (HKDF-SHA256 + AES-256-GCM) - with the
    `cryptography` package where it is installed, in plain Python otherwise,
    both checked against the protocol's test vectors
    (`tools/test-acme-push.py`).
  - What acme sees here: the slot of these pages (dual RSA + ECDSA) and, with
    Plex Media Server's own certificate on, a `plex` slot that follows it.
  - A pushed certificate is checked (fingerprint, key, validity, the names of
    these pages unless **Take certificates of other names** is on) and kept
    exactly as an upload is - nginx and Plex take it as after an upload,
    Automatic certificates leave it alone; the same one again is "already
    there". The card shows the last push and the last refusal.
  - **Nothing existing changes**: upload, request, self-signed, the TLS
    settings, Automatic certificates, Plex's certificate, backups and the
    command line work exactly as before, with push off and on; no existing
    function is changed, the receiver only calls them.

## 2.5.5 - 2026-11-01

- **Saved passwords: a full administrator sees the last two characters, all
  over the pages** - checked everywhere one is kept:
  - Settings (the proxy's password, the backups' password, the DNS API tokens
    and keys, the TSIG secret, the TMDB / OMDb / Trakt keys) and a backup
    destination's password already showed dots and the last two to a full
    administrator, and nothing to anybody else;
  - **new**: the passphrase of an ssh key given for a backup destination, shown
    beside the key the same way (before, only that one was kept).
  - Typed, a password shows dots only, everywhere. Private keys (TLS, ssh,
    the ACME account), the second step's secrets and the accounts' passwords
    are never shown back.

## 2.5.4 - 2026-11-01

- **The console is no longer flooded with
  `systemd-rc-local-generator: /etc/rc.d/rc.local is not marked executable,
  skipping`** (found in otp 1.0.210). systemd says it at every
  `systemctl daemon-reload` - several in each system update - into the kernel's
  log, and a kernel whose console log level is above 4 prints it over the
  machine's console, `plexmon --console` included. `install.sh` now sets the
  level to errors only (`kernel.printk = 4 4 1 7`, RHEL's own default) in
  `/etc/sysctl.d/80-plexmon-console.conf` and applies it; the journal keeps
  every message (`journalctl -k`). A `kernel.printk` set by hand elsewhere is
  left alone; the file is the same on every run and goes with
  `install.sh --uninstall`. Nothing else is touched: not `/etc/rc.d/rc.local`'s
  mode, not the generator.

## 2.5.3 - 2026-10-31

- **Plex library: F5 and F9 do nothing on the page** - they are the
  browser's again (F5 reloads the page). Scanning the library and refreshing
  all its metadata are done with the buttons *Scan the library* and *Refresh
  all metadata* alone. (The command line and the console keep their own
  `--plex-scan` / `--plex-refresh-all`.)

## 2.5.2 - 2026-10-31

- **Posters come faster.**
  - The images no longer wait in the same queue as everything else asked of
    Plex (three at a time): five images are made at once by Plex's own image
    transcoder, apart from the library's other questions, and the same picture
    asked twice at once (two cards, a reload) is made once.
  - The watchlist's posters from the internet (plex.tv, TMDB) are made small
    by Plex's transcoder too - a few kilobytes instead of the whole picture of
    a megabyte or more; where Plex will not, fetched whole as before.
  - The page begins loading posters twice as far ahead of the scroll.
  - Already so, and kept: each picture is made once and kept on the disk
    (`/var/cache/plexmon/img`, 400 MB, the least used go first) and in the
    browser for a week. A picture that changes in Plex changes its address
    (Plex puts the time of the change into it), so an old one is never shown
    for a new one - the library's list is read again within two minutes (or
    at once with its refresh button).

## 2.5.1 - 2026-10-31

- **Audio and subtitles → Scan the files again** shows how far Plex is: the
  bar follows Plex's own scan (its activity, in per cent), and the words say
  how many videos have new tracks so far ("Plex is scanning - 0 of 16 videos
  have new tracks so far"). Before, the bar counted only the videos with new
  tracks, so with none it stood at 0 / 16 the whole time and looked stuck.

## 2.5.0 - 2026-10-31

**Plex library: the rest of plex_tools.** Gone through plex_tools.py again
and brought over what was missing or only partly there:

- *Flip each* watched state (dock, a season, ticked episodes,
  `--plex-flip-watched`); episodes ticked across seasons and acted on
  together (watched, unwatched, flip, tracks).
- *Invert* the selection and *Unselect the titles shown* (library, Titles,
  Ratings) - plex_tools' `i` and `-`.
- The filter *without a label*; *As Plex keeps them* - the exact label strings
  and ids, case variants marked; a label renamed on the selected titles only.
- After F5 / F9, Plex's activities followed to their end in a panel.
- Audio and subtitles: a chain of replacements where the chosen track is
  missing.
- Titles: the plan before *Other ways…* (now → after, held back, no entry of
  its own); filters for a locked title, sort title, either or any field;
  *Refresh their metadata* of the ticked ones - in Ratings too.
- Watchlist: several taken off at once; a bookmark on the library posters of
  what is on it, and a filter; adding sends only the new ones and says how
  many were on it already; the full Plex Discover detail of a title not on
  this server (plot, ratings, genres, director, cast).

## 2.4.5 - 2026-10-31

- **No red error during an upgrade.** An upgrade restarts the daemon, and for
  those seconds the page's ask for the status found nobody and showed *The
  daemon is not answering* in red, though all went well. plexmon is one
  server, so this is expected: during an upgrade (or a restart asked on these
  pages) the page says calmly, in blue, *plexmon is starting again after the
  upgrade to X - the page waits for it*, and goes on once it answers.
  Outside of them the red error comes only after two misses in a row, not
  after one blip.

## 2.4.4 - 2026-10-31

- **Plex library: plex_tools' F5 and F9 over the whole library**, named and in
  sight in the library's toolbar: *Scan the library* (F5 - Plex scans its
  files: what is new comes in, what is gone goes) and *Refresh all metadata*
  (F9 - every item's metadata, posters and backgrounds downloaded again,
  asked first). The keys F5 and F9 do the same while the library is in front
  (no popup open, nothing being typed); Ctrl+F5 / Ctrl+R still reload the
  page. Before, the scan was an unlabelled magnifier and *Refresh all
  metadata* was not on the page at all.
- `plexmon --plex-refresh-all LIB` (asked first, or `--yes`); the console's
  Plex library menu: *Scan the files of a library (F5)*, *Refresh all the
  metadata of a library (F9)*.

## 2.4.3 - 2026-10-31

- **Plex library → Audio and subtitles → Scan the files again** works:
  - Plex is asked to scan just the folders of the videos shown (a partial scan
    by path), not the whole library, and they are read again as soon as Plex
    says its scan is done - seconds, not a blind 90 s wait.
  - It says what it found: how many videos have new tracks, or that Plex
    found none (with the reminder that a subtitle file needs the video's own
    name: `Name.cs.srt`, `Name.forced.cz.srt`).
  - A scan still waiting from another title no longer blocks it ("…is under
    way already"): it only reads, so it is stopped and the new one starts. A
    job that writes to Plex is never stopped this way.

## 2.4.2 - 2026-10-31

- **Settings → Proxy: what uses it, chosen one by one.** The proxy is off and
  used nowhere by default; switched on, only what is switched on beside it
  goes through it - each off by default:
  *New versions of plexmon*, *System updates (dnf)*, *Certificates from the
  ACME CA*, *The DNS API for DNS-01* (deSEC, Cloudflare, acme-dns), *Plex
  Discover and posters*, *The rating databases* (TMDB, OMDb, Trakt, TVmaze).
  What is left off goes out exactly as without a proxy. Plex Media Server, the
  dashboard, the televisions, the backups and RFC 2136 (nsupdate) never use
  it. *Try it* tries the proxy itself, whatever is switched on.

## 2.4.1 - 2026-10-31

- **Settings → Proxy is for the updates alone**: the new versions of plexmon
  and, where switched on, the system updates (dnf). Nothing else plexmon does
  goes through it or changes with it - the ACME CA and the DNS APIs, Plex
  Discover, the rating databases go out exactly as before 2.4.0 (the
  service's own environment), whatever is set there. The setting *Plex
  Discover and the ratings through it too* is gone; *Try it* asks the way the
  new versions are fetched.

## 2.4.0 - 2026-10-31

- **Settings → Proxy**, as otp has it: where the server may reach the web only
  through an HTTP proxy. *Go out through a proxy*, its address, a user name
  and a password (kept sealed), what goes straight (names, `.domains`,
  addresses and nets), and whether *the system updates* and *Plex Discover
  and the ratings* go through it too. Through it: the new versions, the
  certificates from the ACME CA (and the DNS APIs), Plex Discover and the
  rating databases (TMDB, OMDb, Trakt, TVmaze), dnf. Never: Plex Media Server
  itself, the televisions, mail, the backups.
  - **Try it**: an address asked the way plexmon asks the web, each step
    shown with its time - the proxy looked up, connected, the tunnel, TLS and
    the certificate, the answer - and the way drawn: this server, the proxy,
    the address.
  - dnf gets the proxy in a file only root reads (`/run/plexmon/osup-proxy.env`,
    named to systemd-run), never on a command line.
  - With no proxy set here, the service's own environment
    (`Environment=https_proxy=…`) is used as before.
  - `plexmon --proxy-test [URL]`.

## 2.3.0 - 2026-10-31

- **Settings → Upgrades and maintenance**, as otp 1.0.197 has it (without its
  cluster): *The nodes* - this server, its version, when it was heard from,
  in service or in maintenance (why, who, since when) - with **Maintenance** /
  **Back in service**; *From the web*; *A new version*; *The upgrade*; *What
  root said last* folded until asked for; the command line.
- **Maintenance**: the server out of service - plexmon does nothing by
  itself: no backups, copies of the databases, system updates or restarts on
  a schedule, no renewal of the certificate (ACME), no routine SMART read of
  the disks. These pages, the dashboard and everything done by hand go on
  working. The header says *in maintenance*; Events says who and why.
  - An upgrade from the pages takes the server into maintenance while it is
    put in, and back into service once it runs again (or fails, or is
    stopped) - only a maintenance it began itself.
  - `plexmon --maintenance on --why "…"`, `--maintenance off`,
    `--maintenance status` (exit 3 while in maintenance); `plexmon --status`
    says it; the console: The service → Maintenance.
- **Automatic certificates → In your DNS** laid out across the card, each
  column at a fixed share of it (as isemon): nothing moves between F5 (*not
  checked yet*) and *Check DNS* (*there*), nor on a redraw.

## 2.2.10 - 2026-10-31

- **Automatic certificates → In your DNS**: room between the columns - a
  clear gap before each next column, while each copy button stays close
  beside its text.

## 2.2.9 - 2026-10-31

- **Automatic certificates → In your DNS** on a wide screen: every column is
  as narrow as what is in it and the room left goes to the last one (*In DNS
  now*), so the copy buttons stand right after the text - before, the table
  spread the spare width over the columns and left wide gaps.

## 2.2.8 - 2026-10-31

- **Automatic certificates → In your DNS**: the copy buttons no longer
  flicker when the page refreshes, and they stand one under the other right
  after the text. Each sits in a narrow column of its own after the record
  and after the value, so the table lines them up by itself - before, the
  text was measured after every redraw and the buttons jumped into place.
- The sentence over the table in Czech too.

## 2.2.7 - 2026-10-31

- **Automatic certificates → The CA's own certificate** is a proper memo:
  the whole width, twelve lines tall (and taller by its corner), monospace,
  the PEM's lines as they are (not wrapped) - room for a root and its
  intermediates. Under it: *Load a file…* (.pem, .crt, .cer), *Empty it*,
  and a line saying what is in it - how many certificates and lines, or that
  it is not a whole PEM, or that a private key does not belong there.

## 2.2.6 - 2026-10-31

ACME made the same as upsmon's (4.1.6 and 4.1.7), its UI and what it does:

- **The certificate's key**: *Both: RSA 2048 + ECDSA P-256* (advised, the
  default), *Both: RSA 4096 + ECDSA P-384*, or one key - ECDSA P-256, P-384,
  RSA 2048, 3072, 4096. (*RSA 3072 + ECDSA P-384* of 2.2.4 becomes *RSA 4096
  + ECDSA P-384*.) The second certificate only signs: the name is already
  proven.
- **The key of the ACME account** (new setting): ECDSA P-256 (ES256), P-384
  (ES384) or RSA 2048, 3072, 4096 (RS256) - never a certificate's key. A
  change makes a new account at the next order.
- The keys show on the ACME card (*Keys*), on the certificate (*Key: RSA 2048
  bit + EC 256 bit (a second certificate beside it)*), beside the account at
  the CA, and in the order's steps (*a second certificate, with a … key*).
- **Fixed**: an order whose name was not proven yet stopped with "the key is
  ec256, ec384, …" - the order's loop reused the name of the key's kind for
  the challenge's (dns-01 / http-01), as upsmon 4.1.7 found. Tested through
  the whole order with both challenges and every key.
- What the last order did: the time one narrow column, the words left
  beside it.

## 2.2.5 - 2026-10-31

- **Automatic certificates: the ciphers from there too.** Each certificate's
  card has *TLS settings (ciphers)* - the protocols, the cipher list
  (Standard, Mozilla intermediate, Mozilla modern or your own OpenSSL list),
  the DH parameters and HSTS - the same dialog as Certificates and TLS → TLS
  settings, kept through every renewal by ACME.
- The card says which keys the certificate was ordered with (*Keys*: RSA 2048
  + ECDSA P-256, …).

## 2.2.4 - 2026-10-31

- **ACME: every key Let's Encrypt takes, and its dual certificate**
  (letsencrypt.org/docs/integration-guide). *The key of the certificate* is
  now RSA 2048, 3072 or 4096, ECDSA P-256 or P-384 - or **both**, as Let's
  Encrypt recommends and now the default: an RSA certificate served by
  default and a much smaller ECDSA one to the clients that say they take it.
  - Both are ordered at once (the second order reuses the authorization the
    CA has just given - nothing is checked twice); *RSA 2048 + ECDSA P-256* or
    *RSA 3072 + ECDSA P-384*.
  - nginx gets both: `ssl_certificate` twice in `/etc/nginx/plexmon/tls.conf`,
    the ECDSA one in `server-ecdsa.pem` / `server-ecdsa.key` beside the RSA
    one; `nginx -t` first, everything put back if it says no. The ciphers of
    every preset already carry ECDHE-ECDSA and ECDHE-RSA alike.
  - Plex Media Server keeps the RSA one (old Plex apps, televisions).
  - Should the ECDSA one not come, the RSA one is put in alone (said in
    Events). A certificate put in some other way leaves the ECDSA one behind.
  - Certificates and TLS shows it under the main one (*And beside it*).
  - The account key stays its own (ECDSA P-256), never a certificate's.
- A request or a self-signed certificate can be ECDSA P-384 too.

## 2.2.3 - 2026-10-31

- **Backups to SMB (Windows / Samba share)**: *smbclient: ERROR: Could not
  determine network interfaces, you must use a interfaces config line* no
  longer stops them. smbclient lists the machine's network interfaces (for
  NetBIOS broadcasts) and gives up when the service's sandbox does not let it
  (a limit carried over from plex-status.service.d, say); it is then told, as
  it asks, the one address this machine reaches the SMB server from
  (`interfaces = IP/32`), and the connection goes on.

## 2.2.2 - 2026-10-31

- **System updates after a restart by itself**: the page no longer stays in
  a strange state. The restart did happen, but the page went on showing what
  was said before it - *needs a restart* (the update's own words, kept from
  before the boot) and *looking…* (a look for updates started just after the
  update, killed by the restart). Now:
  - with *Restart by itself*, the server is restarted straight after the
    update, with no look for updates in between to be cut off;
  - after every boot what was said before it is put right (*updated -
    restarted*) and the updates are looked for again once by themselves;
  - a look or an update that plexmon or the server cut off is shown as such,
    never as running for ever;
  - a restart by itself that cannot be asked is said on the card and in
    Events (before, only in the log).

## 2.2.1 - 2026-10-31

- **System updates**: *restart after the update* (and *restart needed*) on a
  line of its own under the server's name - beside it, it ran out of the card.
- The two sentences under *Update this server* are in Czech too.

## 2.2.0 - 2026-10-31

Five new parts of Settings, as otp has them - the same pages, the same
behaviour - and, beyond otp, Plex Media Server's own database looked after.

- **Time** - the clock of the server through chrony: how far off it is, which
  servers it follows, the time servers set here (or the machine's own
  `/etc/chrony.conf` left alone), the time zone, step it now. A clock that is
  off shows among the problems.
- **Restart and power off** - the server restarted or switched off from the
  page (asked twice), followed down and up again; a restart on a schedule,
  weekly or monthly, optionally only when an update needs one
  (`needs-restarting -r`).
- **System updates** - dnf: what is waiting (and how much of it is security),
  every update or only the security ones, its output live on the page, a
  restart offered at the end or made by itself; looked for daily and/or put in
  on a schedule. **Plex Media Server's own package is left out** unless
  `osup_plex` is switched on - updating it restarts Plex in the middle of a
  film.
- **The database** - two of them:
  - *Plex Media Server's library database*: a copy taken while Plex runs
    (SQLite's online backup - nothing in Plex's file is written), checked with
    Plex SQLite (`integrity_check`), packed and kept (`plex_db_keep`), on a
    schedule if wanted; downloaded; **put back** - checked first, Plex stopped,
    its files set aside (kept, never deleted), the copy put in with Plex's
    owner, Plex started, everything rolled back if a step fails. Plex's own
    backups (every three days, beside its database) listed and can be put back
    too. Plex's own *Optimize database* and *Clean bundles* from the page. A
    timer of the server's own that backs Plex's database up (such as
    `plex-db-backup.timer`) shown and can be switched off.
  - *plexmon's own*: its tables, copies (daily, kept), check, give the disk
    back (VACUUM), the events trimmed or erased, a copy put back.
- **Backups** - otp's backups to another machine, as they are: destinations by
  folder, FTP, FTPS, SFTP, SCP or SMB, ssh keys and host keys, client
  certificates, a schedule, how many kept, AES-256 with a password, tried by
  themselves (fetched back, opened, every file and database checked), a
  backup downloaded here or sent from a file and **put back**. A backup holds
  plexmon (database, secrets key, dashboard and nginx/TLS configuration) and,
  with `rbackup_plex_db`, **a checked copy of Plex Media Server's database and
  its Preferences.xml** - put back from a backup it lands in The database, to
  be put into Plex there.
- The command line and the console have all of it: `--time`, `--time-sync`,
  `--db`, `--db-copy`, `--db-check`, `--db-restore`, `--plex-db-copy`,
  `--plex-db-restore NAME [--plex-own]`, `--backups`, `--backup-now`,
  `--backup-verify`, `--os-updates`, `--os-update [security]`, `--reboot`,
  `--poweroff`; console menu 12.
- **Python goes to the newest AlmaLinux 9 has** - 3.14, 3.13 or 3.12 from
  AppStream, put in beside the system's `python3` (which dnf runs on and which
  is never touched). The libraries of the TV remotes are put in for it first,
  and the daemon moves only when everything it needs imports there; otherwise
  it stays where it was. `PLEXMON_KEEP_PYTHON=1` leaves it alone.
- Upgrade from a file in Settings → Upgrades works again (its uploader was
  missing on the page).

## 2.1.6 - 2026-10-31

- **Nothing closes by a click beside it** - neither a popup nor the detail of a
  title in the Plex library: only their own buttons (× , Close) close them, or
  Escape. 2.1.5 had it the other way round.

## 2.1.5 - 2026-10-31

- **A click beside a popup closes it** (pressed and let go beside it - text
  selected inside and let go outside does not). Only a popup with something
  typed into it stays open - it gives a little shake instead, so a stray click
  does not lose a half-filled form; its × , Close or Escape close it.

## 2.1.4 - 2026-10-31

- **PHP goes onto the 8.3 branch** - at least 8.3.36, fixed against php2shell
  - with every install and upgrade (the one from Settings too): PHP 8.0, 8.1
  or 8.2 is moved onto `php:8.3`, one on 8.3 brought up to its newest build,
  a newer fixed one (8.4) left as it is. As in 2.1.2: the settings kept and
  copied first, php-fpm checked and restarted, and everything put back on the
  branch it was on if it does not come up.

## 2.1.3 - 2026-10-31

- **Ratings on every title**: a *Ratings* button on the card of a movie, a show
  (the show, its seasons and every episode) and an episode, and *Ratings of
  the season*. Either Plex is asked for its own ratings again (a refresh: its
  IMDb / Rotten Tomatoes / TMDB badges anew), or the numbers of the databases
  are looked up, shown and written - each into the field whose badge is that
  database: an IMDb badge gets IMDb's number or is left alone, never another
  database's under it. Each row shows the badges now, what is written (critics
  / audience, with its source's badge) and everything found.
- Ratings of seasons and episodes take the ids of their show (TMDB, IMDb,
  TVmaze episode by its number).
- **Labels**: a label's posters stay inside its card.
- **Download the certificate**: compact, fits the page without scrolling; one
  password of the file (shown with the eye, no second box) and your plexmon
  password, now said plainly to only confirm it is you - it never goes into
  the file.

## 2.1.2 - 2026-10-31

- **PHP goes onto the 8.2 branch** (AlmaLinux 9 AppStream, at least 8.2.35 -
  fixed against php2shell) with every install and upgrade, the one from
  Settings too: a PHP older than 8.2 (the default 8.0, 8.1) is moved onto
  `php:8.2` (`dnf module reset/enable`, `distro-sync 'php*'`), one on 8.2
  brought up to its newest build; a newer fixed one (8.3, 8.4) is left as it
  is. php.ini, php.d and the php-fpm pools are kept (and copied to
  `/var/lib/plexmon/backup/php-*` first); php-fpm is checked (`php-fpm -t`)
  and restarted, and if it does not come up on 8.2 everything goes back to the
  branch it was on. `--php-stream X.Y` for another branch.
- **Spacing** across the pages: the blocks of the Plex library (the libraries,
  the toolbar, the posters) no longer touch, cards and rows of buttons have
  one rhythm (8 / 12 / 16 / 20 px), the buttons of a certificate are evenly
  spaced, popups and settings breathe.
- Czech for the events of the Plex library and of a certificate exported.

## 2.1.1 - 2026-10-31

- **Certificates and TLS → Download** opens a window to choose the format:
  the certificate alone (PEM `.crt` or DER `.cer`), with its chain (PEM, or
  PKCS#7 `.p7b`), and - for a full administrator - with the private key:
  **PFX / PKCS#12** locked with a password (AES-256, or 3DES / SHA-1 for older
  Windows, macOS and Java), one PEM with the key, or the key alone (locked with
  a password if you give one). The file password is typed twice, with a
  strength bar; the private key is handed out only after your own password,
  and every export of it is an event.
- Czech: some 200 translations of the pages dropped by the 2.0.x updates are
  back (the tool merging them numbered new entries over older ones).

## 2.1.0 - 2026-10-31

**Plex library** - what plex_tools.py did from a Windows console, now on the
Plex server itself: a tab of Settings (signed in only), the command line and
the console. Nobody signs in to Plex, there are no Home users and no PIN:
plexmon asks Plex Media Server on 127.0.0.1:32400 with the server's own token
(PlexOnlineToken in Preferences.xml), which never leaves the daemon.

- **The library**: the posters of every movie and show (made small by Plex's
  own transcoder, kept on disk, loaded as they come into view), searched
  (`*` and `?` as wildcards), filtered (unwatched, in progress, watched, a
  label, a genre, a country, without a rating), sorted, bigger or smaller.
  A title opens over the page: its backdrop, poster, ratings (IMDb, Rotten
  Tomatoes, TMDB), 4K / HDR / Dolby Vision, the cast with their photos (a
  face shows everything else on the server with that person), the facts,
  the file, its labels, its tracks; a show its seasons and every episode,
  each watched or not by one press. *Open in Plex* goes to the same title in
  Plex Web.
- **Several at once**: Select (or Ctrl-click, Shift for a range) and the bar
  at the bottom: watched, unwatched, audio & subtitles, labels, ratings,
  titles, the watchlist, refresh metadata, the titles exported to a .txt.
- **The default audio and subtitles** of a movie, a show (every episode), a
  season or the episodes ticked - the very track, not only its language: an
  external .srt is told from one inside the file, forced from SDH. Where one
  is missing: leave it, subtitles off, or another track. *Scan the files
  again* for a subtitle just put beside the video. The plan, episode by
  episode, before anything is set.
- **Watched / unwatched**: a movie, a show, a season, an episode, many.
- **Labels**: added and taken off several titles at once (on all, on none,
  as it was), renamed, cleaned out of the whole library (items, seasons,
  episodes, collections, every letter case).
- **Titles in another language**: found by the writing system, the letters
  and the little words of a title - or by the item's own metadata language,
  read from Plex - and put into English (or another language): the title
  alone (written and locked, its own ending like *- Viki* kept), the metadata
  language and a refresh, or a Fix Match to the item's own entry. A title
  from another entry of the agent is marked to be checked (*Find…* picks the
  right one by hand); a split entry and a locked title are held back.
- **Ratings**: IMDb and Rotten Tomatoes (OMDb), TMDB, TVmaze, Trakt - for
  movies, shows, seasons and episodes; only the missing ones or all of them,
  locked or not; looked up and shown first, written when you say so. The
  keys in Settings → Plex library, sealed.
- **The watchlist** of the server's owner (Plex Discover): what is on it,
  what of it is on this server, put on and taken off.
- **Plex is not harmed**: only its HTTP API, never its files or its
  database; at most three requests at once, writes paced, metadata read in
  batches of 40, one job at a time with a Stop; everything that writes shows
  its plan first; an event for every change.
- The command line: `--plex`, `--plex-items`, `--plex-show`, `--plex-tracks
  --audio --subs`, `--plex-watched`, `--plex-unwatched`, `--plex-labels`,
  `--plex-label --label-add --label-remove`, `--plex-titles [--fix]`,
  `--plex-ratings [--overwrite] [--lock]`, `--plex-watchlist[-add|-remove]`,
  `--plex-refresh`, `--plex-scan`, `--dry-run`. The console: *The Plex
  library*.
- Settings opens on the Plex library; Czech and English as everything else.

## 2.0.3 - 2026-10-31

PHP and php2shell (CVE-2026-109618, CVE-2026-109619 - remote code execution
through how PHP takes a request apart, fixed 21 October 2026), as otp does it.

- **install.sh brings PHP up to the fixed release** - on every install and
  every upgrade, so also when the upgrade comes from Settings → Upgrades. A PHP
  already fixed is left exactly as it is; one that is not has its packages
  updated on the stream it is on (`dnf upgrade --refresh 'php*'`; AlmaLinux 9:
  php:8.3 is 8.3.36, php:8.2 is 8.2.35, the default 8.0 has the fix backported
  and keeps its number - so the packages' changelog is read as well as the
  version), and php-fpm is restarted. Where AppStream has no fixed build,
  Remi's PHP 8.4 is offered (asked; `./install.sh --upgrade --remi`; never
  taken by itself during an upgrade - it would move every PHP site on the
  machine). `--php-stream X.Y` for a machine without PHP, `--no-php-update` to
  leave it alone (it is still checked and guarded).
- **The guard as a second layer**: `/etc/php.d/99-php2shell.ini` (upload
  progress off, strict sessions) for every PHP on the machine, and nginx
  refusing a form or multipart body to plexmon's PHP pages under `/smart/`
  (`/etc/nginx/plexmon/php-guard.conf`, included in the vhost's PHP location,
  checked with `nginx -t` and put back on a refusal). The pages never had PHP
  take a body apart - they read JSON or a file from php://input.
- Settings says it: a red bar at the top for a full administrator while the
  PHP running the pages may lack the fix (gone once the daemon reads the fix in
  the packages' changelog), the line in *Problems*, the PHP and its state in
  About, and `plexmon --check` (`PHP 8.x.y has the php2shell fix`).

## 2.0.2 - 2026-10-31

- A secret kept in Settings (the ACME DNS tokens, keys and passwords) is shown
  to a full administrator as otp shows it: dots and its last two characters
  (`••••••••XY`), so the one set can be told from another; while one is being
  typed, only dots. A read-only account sees nothing of it. Saved back
  unchanged, the kept one stays.

## 2.0.1 - 2026-10-31

The command line and the console, as otp's.

- `plexmon` with nothing after it says what it is doing (`--status`) - it no
  longer starts a second daemon; the daemon is `plexmon --daemon`, which
  plexmon.service runs (an older unit starting `plexmon.py` bare still works).
- One command line for everything, otp's: `plexmon --help` in two columns and
  in colour (colorama, or the same colours written by hand; `--no-color`,
  `NO_COLOR`), every line coloured by what it says, `[ ok ]` / `[fail]` when
  piped, `--json` everywhere it makes sense. The dashboard's own options
  (`--wake`, `--refresh`, `--oneshot`, `--diag`, `--atv...`) are in it, and
  `--check` checks the dashboard's whole chain and Settings, nginx and the
  certificate together.
- New on the command line: `--admin-2fa`, `--tls-preset`, `--tls-dh`,
  `--tls-hsts`, `--plex-cert status`, `--acme`, `--acme-dns-check`,
  `--events --search`, `--log`.
- `plexmon --console` is otp's console: the bar at the top (the service, the
  dashboard, Plex, the certificate and its days, ACME, the accounts), menus
  by the arrows and numbers, windows with fields over the menu, Yes / No before
  anything that cannot be undone, lists to scroll and filter (`/`), every
  setting browsed and changed with its help, the live log and the journal
  (what is typed is a grep), `!` a shell. Menus: Status and checks, Live log,
  The disks and the dashboard, The televisions, Accounts of Settings, Settings,
  Certificates and TLS, Automatic certificates, Upgrades, The service.

## 2.0.0 - 2026-10-31

plexmon on otp's engine and pages: the dashboard as it was, and **Settings**.

- **The dashboard** (`/smart/`) does exactly what it did - SMART, temperatures,
  disks that are never woken, now playing, activity, performance, history, the
  television remotes - still open to the LAN without signing in. New: Czech and
  English (the EN / CS switch at the top) and a link to Settings.
- **Settings** (`/smart/settings/`) - signing in needed; otp's administration,
  the same engine and the same pages:
  - *Accounts*: full admin / read only, passwords, switched off and on, who is
    signed in (ended from here), who is locked out (cleared from here), a second
    step by an authenticator app (`admin_2fa`), the columns of every table laid
    out per account, your own settings exported and imported.
  - *Certificates and TLS*: the certificate the Plex address answers with -
    uploaded (PEM or PFX / PKCS#12), from a request made here (CSR) or
    self-signed; the TLS of nginx (standard, Mozilla intermediate / modern,
    custom; DH parameters; HSTS) - written into the Plex vhost's files, nginx
    asked (`nginx -t`) before it is reloaded, everything put back on a refusal.
    The certificate nginx had is taken over as it was. New for a Plex server:
    *Plex Media Server uses this certificate too* - its custom certificate
    (PKCS#12 named in Preferences.xml), renewed with the rest.
  - *Automatic certificates*: Let's Encrypt (or another ACME CA), HTTP-01 -
    nginx answers the CA from /var/www/plexmon-acme - or DNS-01 (deSEC,
    Cloudflare, RFC 2136, acme-dns, by hand), renewed by itself; *In your DNS*,
    *Try port 80*, *Back to the one before*.
  - *Upgrades*: a new plexmon from the web (`release/` of
    github.com/falco81/PLEXMon, looked at daily) or as a file - signed (Ed25519),
    every file checked, put in by root through plexmon-upgrade.path; going back
    to an older version, only a safe one.
  - *About*: the version, what it runs on, the language of the pages (Czech,
    English - `web/lang/*.json`, editable), the console at login, the debug log.
  - *Events*: every sign-in, change, certificate and upgrade.
- **SQLite** (`/var/lib/plexmon/plexmon.db`), secrets sealed with
  `/var/lib/plexmon/secrets.key`.
- **The command line**: `plexmon --status`, `--add-user`, `--cert-import`,
  `--acme-order`, `--upgrade-online`, `--set KEY=VALUE`... (`plexmon --help-settings`);
  the dashboard's own options as before (`--check`, `--diag`, `--wake`, `--atv`...).
- **The console**: `plexmon --console` - menus, the live log, every action; at
  root's login with `plexmon --console-at-login on`.
- **install.sh** takes the old plexmon over: `plex-status.service` becomes
  `plexmon.service` (the old name still works), its limits (`plex-status.service.d/`)
  and its Python (3.12 for the TV remotes) are kept, the history and the SMART
  cache in /var/lib/plex-status stay, the Plex vhost is adopted once and checked.
- plexmon never runs in a cluster: one server, no VIP address, no replication.
