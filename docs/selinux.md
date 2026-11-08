# SELinux and the system's trusted roots

Two parts of Settings that change the operating system itself (2.9.0, taken from otp 1.0.265):
**Settings → SELinux** and **Settings → Certificates and TLS → Trusted roots of the system**.
Both need a full administrator; nothing changes in either unless somebody asks.

plexmon's daemon runs as root (SMART needs it), so it does these steps itself - there is no
separate root helper. `plexmon.service` may write only what they need (`ReadWritePaths=`):
`/etc/selinux`, `/var/lib/selinux`, `/etc/pki/ca-trust`, `/etc/pki/java`,
`/usr/local/share/ca-certificates`, `/etc/ssl/certs`, `/etc/ca-certificates`.

## SELinux

### What it does and does not do

- **A machine with SELinux disabled stays disabled** and plexmon runs exactly as before. The
  installer only puts the tools in (`policycoreutils`, `policycoreutils-python-utils`,
  `libselinux-utils`, `audit`, `selinux-policy-targeted` - dnf systems where SELinux is
  present) and lays plexmon's module beside the program.
- plexmon itself runs **unconfined** (`unconfined_service_t`, as any service from `/usr/local`);
  so does Plex Media Server. What needs care are the confined programs around them: nginx and
  php-fpm (`httpd_t`).
- The way to enforcing: **disabled → permissive (a restart) → a day or two of use →
  enforcing**. Enforcing is refused while anything plexmon needs is missing, anything was
  refused, or auditd does not run. Every step after the first takes effect at once - only the
  first one from *disabled* needs a restart (the kernel starts SELinux and labels every file).
- **The data disks are never relabelled.** Before the first step from *disabled* the mount
  points of the data disks (every real filesystem that is not one of the system's own and not
  on the system's disk) are written into `/etc/selinux/fixfiles_exclude_dirs`: the
  relabelling at the next start would otherwise wake every sleeping disk and read every file
  on it. Those files stay unlabelled - Plex runs unconfined and reads them as before. A Samba
  or NFS share of them would be refused under enforcing; that shows up as a refusal while
  permissive (`smbd_t`, `nfsd_t` are counted) and keeps enforcing from being switched on.
- The checks read only the system's disk (the policy, the audit log, plexmon's own files),
  at the lowest priority (`nice -n 19`, `ionice -c 3`): the first look two minutes after the
  start, then every 30 minutes, and at once when asked.

### What plexmon needs

| What | Why | How |
|---|---|---|
| `/run/plexmon`, `/run/plex-status` labelled `httpd_var_run_t` | the pages (php-fpm) read the daemon's API token; systemd makes the folders as `var_run_t` | `plexmon_server.cil`, for both `/run` and `/var/run` |
| systemd may chown / chmod in them | `RuntimeDirectory=` is handed back to the service before every unit command | a narrow `allow init_t httpd_var_run_t { getattr setattr }` in an `optional` block |
| `httpd_can_network_connect` on | **nginx passes Plex on (127.0.0.1:32400)** and the pages ask the daemon on 127.0.0.1 - without it Plex is not reached through nginx at all | boolean |
| the ports nginx listens on as `http_port_t` | a `listen` port of the Plex vhost that is neither `http_port_t` nor `http_cache_port_t` keeps nginx from starting | `semanage port -a` - never `-m`: a port of another service is refused with "choose another port" |
| `/var/www/html/smart`, `/var/www/plexmon-acme` | the pages, the answers for the ACME CA | the policy's own labels - `restorecon` |
| systemd may mount on `/etc/selinux`, `/var/lib/selinux` | `ReadWritePaths=` of plexmon.service binds them into the service's own view before it starts (`init_t mounton` on `selinux_config_t`, `semanage_store_t`) - without it plexmon does not start under enforcing | narrow `allow` in `optional` blocks (2.9.2) |

The module (`deploy/plexmon_server.cil`) is CIL - `semodule -i` needs no compiler on the
server. It is put in by the installer where SELinux is on and it changed (compared with
`plexmon_server.cil.in-policy`, written only after a successful `semodule -i`), and by
*Put in what plexmon needs*; `install.sh --uninstall` takes it out (`semodule -r
plexmon_server`). The mode, the boolean and the ports are the machine's and stay.

### The card

The mode (pill), what it will be after the next restart, whether every file is labelled at the
next start, `selinux=0` on the kernel's command line; then a line each: the module, every
label, the boolean, every port, the refusals (since the machine started, or since plexmon's
needs were last put in - what was refused before is what they put right), refusals the policy
loaded now allows (`audit2why`: *would be allowed by active policy*, not counted), auditd.
*What was refused (N)* lists them as `N× source → target class { perms } comm name`.

Buttons: *Make it ready (permissive)* (disabled), *Put in what plexmon needs* and *Switch to
enforcing* (permissive; enforcing only once ready), *Back to permissive* (enforcing). Each asks
first and says what will happen. *Look again* and *Download the report* are at the top.

**What counts before enforcing** (2.9.2): every program's refusals - the whole system goes
enforcing. A refusal from before plexmon's needs were last put in counts until the policy loaded
now allows it (`audit2why` says *would be allowed by active policy*) - a refusal that comes only
when a service starts would otherwise hide until the next restart. Without `audit2why` only
what came after counts. Before switching, restart the server once more in permissive and look
again.

**Plex Media Server and nginx** - a card of its own: the domain Plex's processes run in (read
from `/proc`; `unconfined_service_t` means SELinux does not restrict Plex, the data disks
included; `init_t` or a container keeps enforcing from being switched on), the labels of Plex's
program and library and of what nginx reads to publish Plex (the vhost, the TLS settings, the
certificate and its key - `httpd_config_t`, `cert_t` or `etc_t`), the ports nginx listens on,
`httpd_can_network_connect`, and what was refused to Plex or to nginx on its way to
127.0.0.1:32400.

When everything plexmon knows it needs is there and something is still refused, the card says
*plexmon does not know this one yet* - the report is what tells how to put it right.

`selinux=0` on the kernel's command line: `/boot` is not plexmon's to change. It shows up in
Settings → Upgrades → *To be done by hand* with `grubby --update-kernel ALL --remove-args
selinux`, and goes by itself once the kernel starts SELinux.
It is shown only once plexmon made SELinux ready itself (permissive in the config, every file
to be labelled at the next start). A machine switched off by `selinux=0` alone usually keeps
`SELINUX=enforcing` in its config: taking `selinux=0` away there starts it enforcing on files
never labelled, and services - Plex among them - do not start (the card warns about it). The
way back: `setenforce 0`, then `grubby --update-kernel ALL --args selinux=0`.

The first start with SELinux labels every file (the data disks left out) - a long while, up to
tens of minutes on a large system disk - and then restarts the machine once more by itself.

### The report

*Download the report* / `plexmon --selinux-report [FILE]`: nothing is changed to make it. The
refusals (raw, readable, `audit2why`, an `audit2allow` suggestion - a hint, never loaded), the
card's state, the labels of plexmon's files against what `matchpathcon` says, the domains of
the processes, ports, booleans, the system and its packages, what plexmon needs. No password,
key or token of plexmon is in it - but the audit log holds the command lines of other programs:
look through `avc*.txt` before sending it on. From the pages it is kept for an hour
(`/var/lib/plexmon/selinux-report.tar.gz`, root's alone).

### Command line

```bash
plexmon --selinux-check           # the card; exit 1 when something is missing (--json)
plexmon --selinux prepare         # check | prepare | enforce | permissive - asks first (--yes)
plexmon --selinux-report [FILE]   # /root/plexmon-selinux-report-HOST-DATE.tar.gz
```

## The system's trusted roots

Certificate authorities of one's own - a company's root, an intermediate - trusted by the
operating system as a whole: curl, dnf, mail, LDAPS, plexmon's own connections.

- **The distribution's list is never touched** and keeps coming with the system's updates
  (`ca-certificates`). These go beside it, by the system's own way of adding to it:

  | Family | Local additions | Tool | The list it makes |
  |---|---|---|---|
  | RHEL / AlmaLinux / Rocky | `/etc/pki/ca-trust/source/anchors/*.pem` | `update-ca-trust extract` | `/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem` |
  | Debian / Ubuntu | `/usr/local/share/ca-certificates/*.crt` | `update-ca-certificates` | `/etc/ssl/certs/ca-certificates.crt` |

- Only files named `plexmon-<the first 16 hex of the SHA-256>.pem|.crt` are ever written or
  removed. Anything else in the folder is somebody else's and is only shown; links are never
  followed. The folder must be root's and not writable by anyone else.
- The whole set is brought in place each time (idempotent), the tool run only when something
  changed, and each certificate checked to be really in the resulting list. Under SELinux the
  new file gets `restorecon -F`. Again every 30 minutes, and at once when asked.
- **Plex Media Server keeps a list of its own** and is not touched by any of it.

What is taken: PEM (one or a chain) or one certificate in DER (`.cer`); 50 at most.
**Refused**: a private key or anything but certificates in the text, a certificate that is not
an authority (no `basicConstraints`, or `CA:FALSE`), a key usage without `keyCertSign`.
**Warned** (taken): ran out or not valid yet, an intermediate signed with SHA-1/MD5, an RSA key
under 2048 bits, an intermediate whose root is neither in the set nor in the same paste.

The card: a table (authority, issued by, valid, key, SHA-256 - a click copies it, whether it is
in the system's list), *Add an authority* (pasted or a file, checked as you type), download as
PEM, take it away (asks first); under it the system's own list (how many, its package, when
made), the folder, the tool, and what somebody else added there.

```bash
plexmon --os-roots                          # the set and how the machine is (--json)
plexmon --os-roots-add company-root.pem     # asks first (--yes)
plexmon --os-roots-remove 6433b38639c3      # the SHA-256, 12 characters of it at least
```

If plexmon's Python was built with an OpenSSL of its own that looks where there is nothing,
`SSL_CERT_FILE` is pointed at the system's list at the start, so that plexmon's own connections
use the system's trust too; the card says whether they do.
