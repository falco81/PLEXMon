# plexmon configuration

Everything is set in **one** JSON object in `/etc/plex-status/config.json`.
Anything you leave out of the file keeps its default value — feel free to keep
only what you actually change.

JSON has no comments, so this file serves as the reference manual,
and a clean template to paste is at the end.

```bash
mkdir -p /etc/plex-status
# ... paste the content ...
systemctl restart plex-status.service
plexmon --diag        # the "effective settings" section shows what actually applies
```

An unknown key is **not used, and the daemon reports it** (in the log, in `--diag` and in
`--check`) — so a typo does not go unnoticed. The same holds for broken JSON.

Every option can alternatively be set by an environment variable with the prefix
`PLEXMON_`, e.g. `PLEXMON_TEMP_WARN=55`. The environment takes precedence over the file.

---

## Temperatures

| option | default | meaning |
|---|---|---|
| `temp_warn` | `60` | °C at which a platter disk is reported as warm |
| `temp_crit` | `70` | °C at which a critical temperature is reported |
| `temp_warn_nvme` | `70` | the same for NVMe drives that report no limits of their own |
| `temp_crit_nvme` | `80` | |

The threshold for each disk is chosen in this order:

1. **what the disk itself reports** — it knows its own tolerances best (your Intel reports
   77 °C warning / 80 °C critical)
2. **the NVMe defaults**, if it is an NVMe drive and reports nothing
3. **the global values**, meant for platter disks

Flash takes more than platters: an NVMe drive at 60 °C is normal, a platter disk at
60 °C is not. If the global thresholds were used for NVMe, it would raise an alarm long
before the manufacturer would consider the temperature a problem.

## Disk spin-down

| option | default | meaning |
|---|---|---|
| `spindown_after_s` | `900` | after how many seconds without I/O a disk is considered spun down |

This is **an assumption, not a measurement** — the daemon never asks the disk for its state, because
over a USB bridge the answer cannot be relied on and the query would wake the disk. You find the actual
value with the `spindown-info.sh` script. The daemon watches the assumption passively:
when the spin-up counter grows without the disk ever having been reported as spun down, it points
this out in `plexmon --check`.

| option | default | meaning |
|---|---|---|
| `bridge_wakes_siblings` | `true` | disks on one USB bridge share their power state |

Multi-bay USB enclosures spin all their disks up together. With this option on,
a disk whose neighbour is working is reported as spinning (because it is spinning) and SMART
may be read from it for free. Disks on a **different** bridge are not affected. Set `false`
for enclosures that really power each bay separately.

| option | default | meaning |
|---|---|---|
| `use_hdparm` | `false` | ask for the power state through hdparm |
| `wake_standby` | `false` | read SMART even from a spun-down disk during a normal run |

Leave both off. `hdparm -C` over a JMicron bridge returns nonsense
and wakes the disk as well. `wake_standby` breaks the main guarantee of the whole daemon.

## SMART reads

| option | default | meaning |
|---|---|---|
| `smart_min_interval` | `300` | minimum gap between routine SMART reads (s) |
| `smart_daily_at` | `4` | the hour at which SMART is read from all disks once a day |

An awake disk is not read on every pass — the values change over minutes and every
read runs `smartctl`. This gap is ignored by the wake button, the daily pass
and the `plexmon --refresh` command.

**Only fresh SMART readings** are written to the history. While a disk sleeps, a gap stays
in the graph: writing the last known value instead would mean claiming
something we did not measure, and all the spin-ups gathered during the sleep would then
be attributed to the five minutes between two samples.

## Collection cadence

| option | default | meaning |
|---|---|---|
| `fast_interval` | `10` | the fast loop (s) — playback, activity, power state |
| `slow_interval` | `300` | the slow work (s) — API response time, databases, history sample |

The fast loop works only with memory (`/proc`, `/sys`, Plex API) and does not touch
the disks.

## History and graphs

| option | default | meaning |
|---|---|---|
| `history_days` | `7` | how long to keep samples |
| `history_max` | `25000` | hard cap on the number of samples |
| `history_points` | `140` | how many points to put into `data.json` for the first render |

Retention is governed by age, not count — so changing the cadence does not shorten the history.

## Performance probe

| option | default | meaning |
|---|---|---|
| `perf_endpoints` | `["/identity","/library/sections","/hubs"]` | what to measure |
| `perf_samples` | `5` | how many samples per endpoint (the median is taken) |
| `perf_proxy_url` | `"https://plex.falco81.net"` | the address through the reverse proxy |

The difference between measuring directly and through the proxy gives the number on the "Reverse proxy" card.
An empty string `""` turns the probe off. It measures over one reused connection, so that
the TLS handshake is not counted in the result.

## Network and API

| option | default | meaning |
|---|---|---|
| `api_host` | `"127.0.0.1"` | the address the daemon listens on |
| `api_port` | `9847` | the API port |

Leave `api_host` at `127.0.0.1`. The daemon runs as root and the API can wake
disks — exposing it to the network is not a good idea, even with a token. If you change the port,
change it at the top of `plexmon-api.php` too.

| option | default | meaning |
|---|---|---|
| `plex_url` | `"http://127.0.0.1:32400"` | Plex Media Server |
| `plex_prefs` | path to `Preferences.xml` | the token is read from here |

## TV control

| option | default | meaning |
|---|---|---|
| `atv_enable` | `false` | turns playback control on (applies to all brands) |
| `atv_remote` | `""` | path to `atvremote`; empty = it finds it by itself |
| `atv_storage` | `/var/lib/plex-status/atv-creds.json` | Apple TV pairing credentials |
| `tv_creds` | `/var/lib/plex-status/tv-creds.json` | LG and Samsung pairing credentials |
| `tv_samsung_keys` | see below | which keys to send to a Samsung |
| `tv_pair_timeout` | `90` | how many seconds to wait for you to confirm the prompt on the television |

Three brands are supported. Plex cannot control its own clients — its apps
do not announce themselves to the server as controllable and `/clients` returns nothing — so the daemon
talks to the television directly in its own protocol.

| brand | library | what it can do |
|---|---|---|
| Apple TV | [pyatv](https://pyatv.dev) | real playback control, 10 s skip |
| LG (webOS) | [aiowebostv](https://pypi.org/project/aiowebostv/) | real playback control |
| Samsung (Tizen) | [samsungtvws](https://pypi.org/project/samsungtvws/) | only remote control key presses |

### The difference between brands

With **Apple TV and LG** it is real playback control: you say "pause" and
the television pauses, whatever happens to be focused on the screen.

With **Samsung** no such option exists. Its interface can only send remote control
key codes, and what is done with them is decided by the app in the foreground.
In practice it works, because Plex on Tizen responds to these keys, but it is
more fragile: when a menu or a dialog is on the screen, the key press goes there.

Which keys are sent can be changed — not every remote sends the same codes:

```json
{
  "tv_samsung_keys": {
    "play_pause": "KEY_PLAY_BACK",
    "skip_forward": "KEY_FF",
    "skip_backward": "KEY_REWIND"
  },
  "tv_samsung_key_delay": 0.4
}
```

The value can be **one key or a sequence**. The default is one, because
two are unpredictable: whether an arrow skips or only wakes the control bar
depends on whether the bar happens to be visible — so a double press skipped ten
seconds, twenty, or nothing. Use a sequence only when your television
really needs it; `tv_samsung_key_delay` is the delay between keys in seconds.

### When a button does nothing

Which code does what is not fixed anywhere — the app in the foreground decides. So it is
better to try it than to guess:

```bash
plexmon --atv-key "Living Room" KEY_FF
```

It sends one key and you look whether the television responded. Candidates for
skipping, in order of how often they work:

| code | what it usually is | how far it skips |
|---|---|---|
| `KEY_FF`, `KEY_REWIND` | fast forward / rewind — the default | 30 s in Plex |
| `KEY_RIGHT`, `KEY_LEFT` | arrows | 10 s in Plex, but often only after the bar is woken |
| `KEY_ENTER` | wakes the control bar | — |

**How far it skips is decided not by the daemon but by the app on the television.** We only send
a key press. If you want ten seconds instead of thirty, switch to the arrows:

```json
{
  "tv_samsung_keys": {
    "play_pause": "KEY_PLAY_BACK",
    "skip_forward": "KEY_RIGHT",
    "skip_backward": "KEY_LEFT"
  }
}
```

The drawback of the arrows is that the first press after a while of inactivity may only bring up the control
bar and not skip. The bar then stays visible for a few seconds, though, so with
repeated presses it fits — only the very first press is wasted.

Always check it with **a single press**. When it seems to you that a key "works only
sometimes", it is usually because you send several in a row and the app responds to them
differently each time depending on what is on the screen.

Once you find what works, write it into `tv_samsung_keys`. When the bar needs to be
woken first, use a sequence, e.g. `["KEY_ENTER", "KEY_RIGHT"]`.

Older models may want `KEY_PLAY` and `KEY_PAUSE` separately; then set
`play_pause` to one of them and leave the other alone, or use `KEY_ENTER`.

A note on pairing: some models (e.g. the Q60 series) send, right after connecting,
the message `ms.remote.touchDisable`, which the library takes as a connection failure.
The daemon therefore extends the list of tolerated messages — if pairing reports
`the television refused` with some other `ms.*` message, send it and I will add it
to the tolerated ones.

### Finding televisions

Apple TVs are found over the network (mDNS). LG and Samsung are not — there is no point, because
the address of the television that is playing is **reported by Plex itself**. So the daemon only
checks that one address:

- Samsung answers on `http://<ip>:8001/api/v2/` with a device description
- LG has an open WebSocket on port 3000

The result is remembered, so this is not repeated on every query.

### Pairing

**Apple TV** shows a four-digit code on the screen, which you enter in the window on the page.

**LG and Samsung** instead ask directly on the television — a prompt appears,
which you confirm with the physical remote. No code is entered, so it is one
step. The page recognizes this by itself and does not offer the PIN window.

You have `tv_pair_timeout` seconds to confirm, 90 by default. If you cannot get
to the remote in time, raise it — it is waiting for a person, not a machine.

### Installing the libraries

All of them need **Python 3.10 or newer** and must be available to the
Python the daemon runs under:

```bash
dnf install -y python3.12
python3.12 -m pip install pyatv          # Apple TV
python3.12 -m pip install aiowebostv     # LG
python3.12 -m pip install samsungtvws    # Samsung
```

It is enough to install only the ones you actually have. When a library is missing, only
that one brand is turned off — the others and the disk monitoring keep running.

If the daemon has so far run under the system Python, switch it in the unit file:

```ini
ExecStart=/usr/bin/python3.12 /usr/local/lib/plexmon/plexmon.py
```

Do **not install** any of this into the RPM-managed system Python — `pip` will
try to overwrite packages that belong to `dnf` and end with an error.

### How it works inside

With Apple TV and LG the daemon **keeps the connection open**. Setting up an encrypted session
takes three to five seconds, so if it were done on every press, the pause button
would respond after five seconds and be useless. The connection is opened in the background
as soon as playback appears, so even the first press is instant.

When the connection drops (the television went to sleep, changed its address), the daemon tries once
to connect again. For Apple TV there is also a fallback through `atvremote` — slower,
on the order of seconds, but the buttons keep working.

Samsung does not keep a connection: its library is synchronous and one connection per command
is cheap there.

For the fast connection the daemon **imports** the libraries. The import is deferred until
first use, runs in the background and is guarded: when a library fails to load, that
brand is turned off and the disk monitoring is not affected. It is, however, a weaker guarantee than
for the rest of the daemon, which makes do with the standard library.

### Service limits

With the libraries loaded the daemon sits around 90–110 MB. The default cap of 200 MB is
too little for that:

```ini
# /etc/systemd/system/plex-status.service.d/atv.conf
[Service]
MemoryMax=400M
CPUQuota=60%
```

From the memory use you can tell whether the fast path is active:

```bash
systemctl show plex-status.service -p MemoryCurrent
```

Under 30 MB = the library is not loaded yet; around 90–110 MB = the connection is running.

### Usage

Turn on `atv_enable`, restart the daemon, and in the Now Playing panel the button **Pair this TV**
appears by the playing stream. After pairing it turns into controls:
back, play/pause, forward.

The same can be done from the command line:

```bash
plexmon --atv                      # what is known and what is paired, including the brand
plexmon --atv-scan                 # scan the network again (finds only Apple TV)
plexmon --atv-probe 192.168.40.55  # what television is at this address
plexmon --atv-pair 192.168.40.55   # pair an LG or Samsung by address
plexmon --atv-pair "Living Room"   # or by name, once it is known
plexmon --atv-key "Living Room" KEY_FF  # try a single key (Samsung only)
plexmon --atv-unpair "Living Room"
plexmon --atv-unpair-all
```

Apple TV announces itself over the network, so it is in the listing right away. **LG and Samsung are not** — until
they are paired, nobody knows about them, and that is why they are given by **IP address**. You
find it either in the television's settings, or in the Now Playing panel, where Plex reports it.

A stream is matched to a television by the **IP address** Plex reports for the player
— not by the name, which need not be unique.

In the top right corner of the card is a muted **unpair** link (the first click asks,
the second does it). It removes only *our* access — the television keeps the server among its
paired remotes, and it can be removed there only in its own settings.

When something does not work, the reason is in the log:

```bash
journalctl -u plex-status.service -f | grep -iE "apple tv|samsung|lg |television"
```

## Paths

| option | default | meaning |
|---|---|---|
| `state_dir` | `/var/lib/plex-status` | SMART cache and history |
| `web_dir` | `/var/www/html/smart` | where `data.json` and the posters are written |
| `run_dir` | `/run/plex-status` | API token |

If you change a directory, the files in it move with it. The individual files
(`data_file`, `cache_file`, `history_file`, `sessions_web`, `history_web`,
`token_file`) can also be set separately, and then they take precedence.

When changing `web_dir`, do not forget to adjust `ReadWritePaths` in `plex-status.service`
and to deploy `index.php` to the new location.

| option | default | meaning |
|---|---|---|
| `smartctl` | `/usr/sbin/smartctl` | path to the binary |
| `hdparm` | `/usr/sbin/hdparm` | path to the binary (not used) |

---

## Template to paste

Commonly changed options with their default values. Delete the lines you do not change — what
you leave out keeps its default value.

```json
{
  "temp_warn": 60,
  "temp_crit": 70,
  "temp_warn_nvme": 70,
  "temp_crit_nvme": 80,

  "spindown_after_s": 900,
  "bridge_wakes_siblings": true,

  "smart_min_interval": 300,
  "smart_daily_at": 4,

  "fast_interval": 10,
  "slow_interval": 300,

  "history_days": 7,
  "history_max": 25000,
  "history_points": 140,

  "perf_samples": 5,
  "perf_proxy_url": "https://plex.falco81.net",

  "api_host": "127.0.0.1",
  "api_port": 9847,

  "atv_enable": false
}
```

## Full template

Everything that can be set — including paths and options that are not normally changed.
Take it as a reference rather than as a file to deploy.

```json
{
  "temp_warn": 60,
  "temp_crit": 70,
  "temp_warn_nvme": 70,
  "temp_crit_nvme": 80,

  "spindown_after_s": 900,
  "bridge_wakes_siblings": true,
  "use_hdparm": false,
  "wake_standby": false,

  "smart_min_interval": 300,
  "smart_daily_at": 4,

  "fast_interval": 10,
  "slow_interval": 300,

  "history_days": 7,
  "history_max": 25000,
  "history_points": 140,

  "perf_endpoints": ["/identity", "/library/sections", "/hubs"],
  "perf_samples": 5,
  "perf_proxy_url": "https://plex.falco81.net",

  "api_host": "127.0.0.1",
  "api_port": 9847,

  "plex_url": "http://127.0.0.1:32400",
  "plex_prefs": "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",

  "atv_enable": false,
  "atv_remote": "",
  "atv_storage": "/var/lib/plex-status/atv-creds.json",
  "tv_creds": "/var/lib/plex-status/tv-creds.json",
  "tv_samsung_keys": {
    "play_pause": "KEY_PLAY_BACK",
    "skip_forward": "KEY_RIGHT",
    "skip_backward": "KEY_LEFT"
  },

  "smartctl": "/usr/sbin/smartctl",
  "hdparm": "/usr/sbin/hdparm",

  "state_dir": "/var/lib/plex-status",
  "web_dir": "/var/www/html/smart",
  "run_dir": "/run/plex-status",

  "data_file": "/var/www/html/smart/data.json",
  "sessions_web": "/var/www/html/smart/sessions.json",
  "history_web": "/var/www/html/smart/history-full.json",
  "cache_file": "/var/lib/plex-status/smart-cache.json",
  "history_file": "/var/lib/plex-status/history.json",
  "token_file": "/run/plex-status/api-token"
}
```

Give the last block of paths only when you want individual files somewhere other than
their directory — otherwise `state_dir` / `web_dir` / `run_dir` is enough and the files
move with them.

## Minimal variant

For your server, in practice this is enough:

```json
{
  "spindown_after_s": 600,
  "bridge_wakes_siblings": true
}
```
