#!/usr/bin/env python3
"""plexmon_host.py - the machine itself, for Settings (2.2.0): as otp has it.

  - Restart and power off - now, or on a schedule (only when an update needs
    it), followed down and up again;
  - Time - chrony: how far off the clock is, its sources, the time servers
    (a block of its own in chrony.conf, put back when switched off), the
    time zone, the clock stepped now;
  - System updates - what dnf has waiting (security ones apart), the update
    with its output as it comes, a restart offered or made when one is
    needed; on a schedule if asked. Plex Media Server's own package is left
    out unless asked: an update of it restarts it in the middle of a film;
  - The database - plexmon's own (SQLite): copies, a check, the disk given
    back, what is kept, erased; and Plex Media Server's own library database:
    copies taken while it runs (SQLite's own online backup, read-only), checked
    on the copy - never on the live file - Plex's own optimizing, Plex's own
    automatic backups listed, a copy put back (Plex stopped, the files it had
    set aside, Plex started again);
  - Backups to another machine - a folder, FTP, FTPS, SFTP, SCP or SMB,
    locked with a password (AES-256, opened with openssl alone too), on a
    schedule, the newest kept, RESTORE.md beside them - with Plex's database
    in them if wanted.

The daemon runs as root under ProtectSystem=full: what writes /usr (dnf) runs
as a transient unit of its own (systemd-run), outside plexmon's memory limit.
"""

import base64
import binascii
import ftplib
import gzip
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

A = None                    # plexmon_admin, bound by bind()
shlex_quote = shlex.quote

SYSTEMCTL = os.environ.get('PLEXMON_SYSTEMCTL', 'systemctl')
CHRONYC = os.environ.get('PLEXMON_CHRONYC', 'chronyc')
TIMEDATECTL = os.environ.get('PLEXMON_TIMEDATECTL', 'timedatectl')
CHRONY_CONF = os.environ.get('PLEXMON_CHRONY_CONF', '/etc/chrony.conf')
NTP_BEGIN = '# ---- plexmon: the time servers (Settings → Time) ----'
NTP_END = '# ---- plexmon: end ----'
PLEX_SERVICE = os.environ.get('PLEXMON_PLEX_SERVICE', 'plexmediaserver')
PLEX_DATA = Path(os.environ.get('PLEXMON_PLEX_DATA',
                                '/var/lib/plexmediaserver/Library/Application Support/'
                                'Plex Media Server'))
PLEX_DB_DIR = PLEX_DATA / 'Plug-in Support' / 'Databases'
PLEX_DB = 'com.plexapp.plugins.library.db'
PLEX_BLOBS = 'com.plexapp.plugins.library.blobs.db'
PLEX_SQLITE = os.environ.get('PLEXMON_PLEX_SQLITE', '/usr/lib/plexmediaserver/Plex SQLite')


def bind(admin):
    global A
    A = admin
    for n in ('PEM_CERT', '__version__', '_openssl', '_pub_of_cert', '_pub_of_key', 'cert_info',
              'is_masked', 'new_key', 'node_name', 'seal_secret', 'secret_shown',
              'unseal_secret', 'log', 'CONFIG', 'STATE_DIR', '_meta_json', 'stamp'):
        if hasattr(admin, n):
            globals()[n] = getattr(admin, n)


def scrub_secret(text):
    return re.sub(r'(?i)(password|pass|pw|secret|token)=\S+', r'\1=***', str(text or ''))


def hisec_backup_target(t):
    """otp's high security has no counterpart here."""
    return t


def human_bytes(n):
    n = float(n or 0)
    for unit in ('B', 'kB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return '%d B' % n


def cfg(k, default=None):
    v = A.CONFIG.get(k)
    return default if v in (None, '') else v


def meta(store, k, default=None):
    """A value kept as JSON in the meta table - a dict, a list or a plain
    value (plexmon_admin's _meta_json gives dicts only)."""
    try:
        raw = store.get_meta(k)
        got = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        got = None
    return default if got is None else got


def put_meta(store, k, v):
    store.set_meta(k, json.dumps(v, default=str))


def event(store, kind, words, level='info', who=''):
    try:
        store.add_event(kind, words, level, who=who)
    except Exception:                                   # noqa: BLE001
        pass


def under_systemd():
    return os.path.isdir('/run/systemd/system') and shutil.which('systemd-run') and \
        not os.environ.get('PLEXMON_TEST')


def host_run(argv, timeout=600, transient=False, name='job', memory='2G'):
    """(exit code, output) of a command. `transient`: as a unit of its own
    (systemd-run --wait --pipe) - outside plexmon's sandbox and memory limit,
    for what writes /usr (dnf)."""
    if transient and under_systemd():
        argv = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect',
                '--unit', 'plexmon-%s-%s' % (name, secrets.token_hex(3)),
                '-p', 'MemoryMax=%s' % memory, '--'] + list(argv)
    try:
        got = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=timeout)
    except FileNotFoundError:
        return 127, '%s is not installed' % argv[0]
    except subprocess.TimeoutExpired:
        return 124, '%s took longer than %d s' % (argv[0], timeout)
    return got.returncode, got.stdout.decode('utf-8', 'replace')


def boot_time():
    try:
        with open('/proc/stat') as f:
            for line in f:
                if line.startswith('btime '):
                    return float(line.split()[1])
    except OSError:
        pass
    return 0.0


def due(every, day, at, last_day, now=None):
    """Whether a schedule is due now, and the day it is for (once a day)."""
    now = now or datetime.now()
    try:
        hh, mm = (int(x) for x in str(at or '04:00').split(':', 1))
    except ValueError:
        hh, mm = 4, 0
    if (now.hour, now.minute) < (hh, mm):
        return None
    if every == 'weekly' and now.isoweekday() != int(day or 7):
        return None
    if every == 'monthly' and now.day != 1:
        return None
    today = now.strftime('%Y-%m-%d')
    return today if today != last_day else None


def thread(name, fn, *args):
    def go():
        try:
            fn(*args)
        except Exception as e:                          # noqa: BLE001
            log('%s: %s' % (name, e), 'warn')
    t = threading.Thread(target=go, name='plexmon-' + name, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Restart and power off
# ---------------------------------------------------------------------------
POWER_LIVE_S = 1800
STARTED = {'at': time.time(), 'daemon': False}   # daemon: set by tick()


def power_plan(store):
    return meta(store, 'power:plan', {}) or {}


def power_row(store):
    return meta(store, 'power:node:' + node_name(), {}) or {}


def power_busy(store):
    p = power_plan(store)
    if not p or time.time() - float(p.get('at') or 0) > POWER_LIVE_S:
        return False
    r = power_row(store)
    if r.get('id') != p.get('id'):
        return True
    return r.get('state') in ('asked',) or (r.get('state') == 'going' and p.get('how') == 'reboot')


def power_ask(store, how, by, why=''):
    if how not in ('reboot', 'poweroff'):
        raise ValueError('reboot or poweroff')
    if power_busy(store):
        raise ValueError('a restart or a power-off is under way already')
    plan = {'id': secrets.token_hex(6), 'how': how, 'targets': [node_name()], 'order': 'one',
            'by': by, 'at': time.time(), 'from': node_name()}
    put_meta(store, 'power:plan', plan)
    row = {'id': plan['id'], 'how': how, 'at': time.time(), 'boot': boot_time(),
           'down_at': time.time() + 5, 'state': 'going', 'said': 'going down', 'moved': time.time()}
    put_meta(store, 'power:node:' + node_name(), row)
    event(store, 'starting and stopping', '%s asked for a %s of %s%s' % (
        by, 'restart' if how == 'reboot' else 'power-off', node_name(),
        (' (%s)' % why) if why else ''), 'warn', who=by)
    log('starting and stopping: %s in 5 s, asked by %s' % (how, by), 'warn')
    if os.environ.get('PLEXMON_TEST'):
        return plan
    if under_systemd():
        rc, out = host_run(['systemd-run', '--quiet', '--collect', '--on-active=5',
                            '--unit', 'plexmon-power-%s' % plan['id'], SYSTEMCTL, how], 30)
    else:
        rc, out = host_run(['shutdown', '-r' if how == 'reboot' else '-h', '+0'], 30)
    if rc != 0:
        row.update(state='failed', said=out.strip()[-300:] or 'it could not be asked')
        put_meta(store, 'power:node:' + node_name(), row)
        raise ValueError('the %s could not be asked: %s' % (how, row['said']))
    return plan


def power_tick(store):
    """After a restart: the row says up again."""
    row = power_row(store)
    if row.get('state') == 'going' and row.get('how') == 'reboot':
        if boot_time() > float(row.get('boot') or 0) + 1:
            row.update(state='back', said='up again', moved=time.time())
            put_meta(store, 'power:node:' + node_name(), row)
            event(store, 'starting and stopping', '%s is up again after the restart' %
                  node_name())
        elif time.time() - float(row.get('moved') or 0) > 900:
            row.update(state='failed', said='it did not restart in 15 minutes')
            put_meta(store, 'power:node:' + node_name(), row)


def power_view(store):
    plan = power_plan(store)
    row = power_row(store)
    seen = store.get_meta('power:seen') == str(plan.get('id') or '')
    live = plan and not seen and time.time() - float(plan.get('at') or 0) < POWER_LIVE_S
    node = {'name': node_name(), 'me': True, 'alive': True, 'started': STARTED['at'],
            'boot': boot_time(), 'address': A.node_address() if hasattr(A, 'node_address') else '',
            'version': A.__version__, 'row': row if row.get('id') == plan.get('id') else {}}
    return {'plan': plan if live else {}, 'nodes': [node], 'now': time.time(), 'cluster': False,
            'busy': power_busy(store), 'reboot_needed': reboot_needed()}


def reboot_needed():
    """Whether an update left something only a restart brings into use."""
    if os.environ.get('PLEXMON_TEST_REBOOT'):
        return os.environ['PLEXMON_TEST_REBOOT'] == '1'
    if shutil.which('needs-restarting'):
        rc, _out = host_run(['needs-restarting', '-r'], 60)
        return rc == 1
    return os.path.exists('/var/run/reboot-required')


def power_schedule(store):
    if not cfg('power_auto'):
        return
    day = due(cfg('power_auto_every', 'weekly'), cfg('power_auto_day', '7'),
              cfg('power_auto_at', '05:40'), store.get_meta('power:auto:day') or '')
    if not day or osup_busy(store):
        return
    store.set_meta('power:auto:day', day)
    if int(cfg('power_auto_needed', 1) or 0) and not reboot_needed():
        event(store, 'settings', 'the restart on a schedule: not needed', 'info')
        return
    try:
        power_ask(store, 'reboot', 'plexmon (on a schedule)', 'on a schedule')
    except ValueError as e:
        event(store, 'settings', 'the restart on a schedule did not start: %s' % e, 'bad')


# ---------------------------------------------------------------------------
# Time - chrony
# ---------------------------------------------------------------------------
NTP_DEFAULT = {'manage': False, 'servers': [], 'peers': True, 'allow': [],
               'makestep': [1.0, 3], 'minsources': 1, 'timezone': ''}
NTP_KEYS = ('server', 'pool', 'peer', 'makestep', 'allow', 'minsources', 'maxdistance', 'sourcedir')
NAME_RE = re.compile(r'^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}'
                     r'[a-z0-9])?)*$')
NTP_WORK = {'busy': False, 'read_at': 0.0}
NTP_LOCK = threading.Lock()


def ntp_conf(store):
    c = dict(NTP_DEFAULT)
    c.update(meta(store, 'ntp:conf', {}) or {})
    return c


def ntp_read():
    out = {'chrony': bool(shutil.which(CHRONYC)), 'tracking': {}, 'sources': []}
    rc, t = host_run([TIMEDATECTL, 'show'], 20)
    if rc in (0, 1):
        for line in t.splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                if k in ('Timezone', 'NTP', 'NTPSynchronized', 'LocalRTC', 'CanNTP'):
                    out[k.lower()] = v
    out['time'] = time.time()
    if not out['chrony']:
        return out
    rc, t = host_run([CHRONYC, '-c', 'tracking'], 20)
    f = t.strip().split(',')
    if rc == 0 and len(f) >= 14:
        try:
            out['tracking'] = {'ref_id': f[0], 'ref': f[1], 'stratum': int(f[2] or 0),
                               'ref_time': float(f[3] or 0), 'offset': float(f[4] or 0),
                               'last_offset': float(f[5] or 0), 'rms': float(f[6] or 0),
                               'freq': float(f[7] or 0), 'skew': float(f[9] or 0),
                               'root_delay': float(f[10] or 0), 'root_disp': float(f[11] or 0),
                               'interval': float(f[12] or 0), 'leap': f[13]}
        except ValueError:
            pass
    rc, t = host_run([CHRONYC, '-c', 'sources'], 20)
    for line in t.splitlines() if rc == 0 else []:
        f = line.split(',')
        if len(f) < 10:
            continue
        try:
            out['sources'].append({'mode': f[0], 'state': f[1], 'name': f[2][:120],
                                   'stratum': int(f[3] or 0), 'poll': int(f[4] or 0),
                                   'reach': int(f[5] or 0, 8) if f[5] else 0,
                                   'last_rx': int(f[6]) if f[6].lstrip('-').isdigit() else None,
                                   'offset': float(f[7] or 0), 'measured': float(f[8] or 0),
                                   'error': float(f[9] or 0)})
        except ValueError:
            continue
    try:
        with open(CHRONY_CONF) as fh:
            text = fh.read()
        out['managed'] = NTP_BEGIN in text
        out['own'] = [ln.strip() for ln in text.splitlines()
                      if ln.strip().split(' ')[0] in ('server', 'pool', 'peer')][:20]
    except OSError:
        out['managed'] = False
    return out


def ntp_zones():
    rc, t = host_run([TIMEDATECTL, 'list-timezones'], 20)
    return [z.strip() for z in t.splitlines() if z.strip() and '/' in z][:700] if rc == 0 else []


def ntp_publish(store, zones=False):
    now = {'at': time.time(), 'data': ntp_read()}
    put_meta(store, 'ntp:now:' + node_name(), now)
    NTP_WORK['read_at'] = time.time()
    if zones or not store.get_meta('ntp:zones'):
        z = ntp_zones()
        if z:
            put_meta(store, 'ntp:zones', z)
    return now


def ntp_checked(b):
    out = dict(NTP_DEFAULT)
    out['manage'] = bool(b.get('manage'))
    servers = []
    for x in (b.get('servers') or [])[:12]:
        if not isinstance(x, dict):
            continue
        host = str(x.get('host') or '').strip().lower().rstrip('.')
        if not host:
            continue
        try:
            import ipaddress
            host = str(ipaddress.ip_address(host))
        except ValueError:
            if not NAME_RE.match(host):
                raise ValueError('%r is not a host name or an address' % host)
        servers.append({'host': host, 'pool': bool(x.get('pool')),
                        'iburst': bool(x.get('iburst', True)), 'prefer': bool(x.get('prefer')),
                        'nts': bool(x.get('nts'))})
    if out['manage'] and not servers:
        raise ValueError('at least one time server - or let the machine keep its own')
    out['servers'] = servers
    out['peers'] = False
    allow = []
    import ipaddress
    raw = b.get('allow')
    for a in re.split(r'[\s,;]+', raw if isinstance(raw, str) else ' '.join(raw or [])):
        if not a:
            continue
        try:
            allow.append(str(ipaddress.ip_network(a, strict=False)))
        except ValueError:
            raise ValueError('%r is not a network (192.0.2.0/24)' % a)
    out['allow'] = allow[:20]
    try:
        th = float(b.get('step_s', 1.0))
        lim = int(b.get('step_n', 3))
    except (TypeError, ValueError):
        raise ValueError('the step: seconds, and how many updates')
    if not (0.001 <= th <= 3600) or not (-1 <= lim <= 1000):
        raise ValueError('the step: 0.001 to 3600 s; -1 (always) to 1000 updates')
    out['makestep'] = [th, lim]
    try:
        out['minsources'] = max(1, min(10, int(b.get('minsources') or 1)))
    except (TypeError, ValueError):
        raise ValueError('how many sources have to agree is a number')
    tz = str(b.get('timezone') or '').strip()
    if tz and not re.match(r'^[A-Za-z0-9_+-]{1,40}(/[A-Za-z0-9_+-]{1,40}){0,3}\Z', tz):
        raise ValueError('%r is not a time zone' % tz)
    out['timezone'] = tz
    return out


def ntp_conf_text(text, n):
    """chrony.conf with plexmon's block - or without it, as it was."""
    out, inside = [], False
    for ln in text.splitlines():
        if ln.strip() == NTP_BEGIN:
            inside = True
            continue
        if ln.strip() == NTP_END:
            inside = False
            continue
        if inside:
            continue
        if ln.startswith('#plexmon# '):
            ln = ln[10:]
        out.append(ln)
    if not n['manage']:
        return '\n'.join(out).rstrip('\n') + '\n'
    kept = ['#plexmon# ' + ln if ln.strip().split(' ')[0] in NTP_KEYS else ln for ln in out]
    block = [NTP_BEGIN]
    for x in n['servers']:
        opts = [o for o, on in (('iburst', x['iburst']), ('prefer', x['prefer']),
                                ('nts', x['nts'])) if on]
        block.append('%s %s %s' % ('pool' if x['pool'] else 'server', x['host'],
                                   ' '.join(opts)).rstrip())
    for a in n['allow']:
        block.append('allow %s' % a)
    block.append('makestep %s %d' % ('%g' % n['makestep'][0], n['makestep'][1]))
    if n['minsources'] > 1:
        block.append('minsources %d' % n['minsources'])
    block.append(NTP_END)
    return '\n'.join(ln.rstrip() for ln in kept).rstrip('\n') + '\n\n' + '\n'.join(block) + '\n'


def ntp_apply(store):
    n = ntp_conf(store)
    said = []
    row = {'at': time.time()}
    try:
        try:
            with open(CHRONY_CONF) as f:
                had = f.read()
        except FileNotFoundError:
            had = 'driftfile /var/lib/chrony/drift\nrtcsync\nlogdir /var/log/chrony\n'
        new = ntp_conf_text(had, n)
        if new != had:
            if NTP_BEGIN not in had:
                bk = STATE_DIR / 'backup'
                bk.mkdir(parents=True, exist_ok=True)
                (bk / ('chrony.conf.%s' % time.strftime('%Y%m%d-%H%M%S'))).write_text(had)
            # Written in place: /etc is read-only to plexmon but this one file
            # (ReadWritePaths) - a new file beside it could not be made.
            with open(CHRONY_CONF, 'w') as f:
                f.write(new)
            said.append('%s: %s' % (CHRONY_CONF, ', '.join(x['host'] for x in n['servers'])
                                    if n['manage'] else 'its own servers again'))
            rc, out = host_run([SYSTEMCTL, 'restart', 'chronyd'], 60)
            said.append('chronyd restarted' if rc == 0 else 'chronyd: %s' % out.strip()[-120:])
        if n['timezone']:
            rc, cur = host_run([TIMEDATECTL, 'show', '-p', 'Timezone', '--value'], 20)
            if cur.strip() != n['timezone']:
                rc, out = host_run([TIMEDATECTL, 'set-timezone', n['timezone']], 30)
                if rc != 0:
                    raise ValueError('timedatectl set-timezone: %s' % out.strip()[-160:])
                said.append('time zone: %s' % n['timezone'])
        host_run([TIMEDATECTL, 'set-ntp', 'true'], 30)
        row.update(ok=True, said='; '.join(said) or 'as it was')
    except (OSError, ValueError) as e:
        row.update(ok=False, said=str(e)[:300])
        log('the time servers could not be set: %s' % e, 'warn')
    put_meta(store, 'ntp:applied:' + node_name(), row)
    time.sleep(2)
    ntp_publish(store)


def ntp_save(store, b, by):
    c = ntp_checked(b)
    c.update(at=time.time(), by=by)
    put_meta(store, 'ntp:conf', c)
    what = ('the servers %s' % ', '.join(x['host'] for x in c['servers'])) if c['manage'] \
        else 'the machine keeps its own servers'
    if c['timezone']:
        what += '; time zone %s' % c['timezone']
    event(store, 'settings', '%s set the time: %s' % (by, what), 'warn', who=by)
    ntp_job(ntp_apply, store)
    return c


def ntp_job(fn, *args):
    with NTP_LOCK:
        if NTP_WORK['busy']:
            return False
        NTP_WORK['busy'] = True

    def go():
        try:
            fn(*args)
        except Exception as e:                          # noqa: BLE001
            log('the time: %s' % e, 'warn')
        finally:
            NTP_WORK['busy'] = False
    threading.Thread(target=go, name='plexmon-ntp', daemon=True).start()
    return True


def ntp_sync(store, by):
    rc, out = host_run([CHRONYC, 'makestep'], 60)
    event(store, 'settings', '%s: the clock stepped to the time servers now (%s)' % (
        by, out.strip()[:120] or rc), 'warn', who=by)
    time.sleep(3)
    ntp_publish(store)


def ntp_view(store):
    if time.time() - NTP_WORK['read_at'] > 60:
        ntp_job(ntp_publish, store)
    try:
        zones = json.loads(store.get_meta('ntp:zones') or '[]')
    except ValueError:
        zones = []
    node = {'name': node_name(), 'me': True, 'alive': True,
            'now': meta(store, 'ntp:now:' + node_name(), {}) or {},
            'applied': meta(store, 'ntp:applied:' + node_name(), {}) or {}}
    return {'nodes': [node], 'conf': ntp_conf(store), 'zones': zones, 'cluster': False,
            'now': time.time()}


def ntp_problem(store):
    now = meta(store, 'ntp:now:' + node_name(), {}) or {}
    d = now.get('data') or {}
    t = d.get('tracking') or {}
    if d.get('chrony') and t:
        if t.get('leap') == 'Not synchronised' or not t.get('stratum'):
            return 'the clock is not synchronised to a time server (chrony): Settings → Time'
        if abs(float(t.get('offset') or 0)) > 1.0:
            return 'the clock is %.1f s off the time servers: Settings → Time' % abs(
                float(t['offset']))
    return ''


# ---------------------------------------------------------------------------
# System updates - dnf
# ---------------------------------------------------------------------------
OSUP_LOCK = threading.Lock()
OSUP_WORK = {'check': False, 'run': False, 'proc': None}
REBOOT_PKGS = ('kernel', 'kernel-core', 'glibc', 'systemd', 'linux-firmware', 'dbus',
               'dbus-broker', 'openssl-libs', 'microcode_ctl', 'gnutls')


def pm():
    return 'dnf' if shutil.which('dnf') else ('apt' if shutil.which('apt-get') else '')


def osup_check_row(store):
    return meta(store, 'host:osup:check', {}) or {}


def osup_plan(store):
    return meta(store, 'host:osup:plan', {}) or {}


def osup_row(store):
    return meta(store, 'host:osup:row', {}) or {}


def _osup_stale(row, flag):
    """Running, says the database - but not in this daemon, and begun before it
    or the server started: a restart (of the server or of plexmon) ended it."""
    since = max(boot_time() or 0, STARTED['at'] if STARTED['daemon'] else 0)
    return row.get('state') == 'running' and not OSUP_WORK[flag] and \
        float(row.get('began') or row.get('at') or 0) < since


def osup_busy(store):
    p, c = osup_plan(store), osup_check_row(store)
    return (p.get('state') == 'running' and not _osup_stale(dict(p, began=p.get('at')), 'run')) \
        or (c.get('state') == 'running' and not _osup_stale(c, 'check'))


def _exclude():
    return [] if int(cfg('osup_plex', 0) or 0) else ['--exclude=plexmediaserver*']


def osup_check(store, by):
    with OSUP_LOCK:
        if OSUP_WORK['check'] or OSUP_WORK['run']:
            raise ValueError('it is looking already, or updating')
        OSUP_WORK['check'] = True
    row = {'state': 'running', 'began': time.time(), 'by': by, 'pm': pm(), 'lines': []}
    put_meta(store, 'host:osup:check', row)

    def go():
        try:
            if row['pm'] != 'dnf':
                raise ValueError('updates are looked for with dnf (AlmaLinux, Rocky, RHEL) - '
                                 'none here')
            rc, out = host_run(['dnf', '-q', 'check-update', '--refresh'] + _exclude(), 900,
                               transient=True, name='osup-check')
            if rc not in (0, 100):
                raise ValueError(out.strip()[-400:] or 'dnf check-update said %s' % rc)
            pk = []
            for ln in out.splitlines():
                f = ln.split()
                if len(f) == 3 and '.' in f[0] and not ln.startswith(' '):
                    pk.append('%s  %s' % (f[0], f[1]))
                if ln.startswith('Obsoleting'):
                    break
            rc2, sec = host_run(['dnf', '-q', 'updateinfo', 'list', '--security', '--available']
                                + _exclude(), 600, transient=True, name='osup-security')
            security = len({ln.split()[-1] for ln in sec.splitlines() if ln.strip() and
                             len(ln.split()) >= 3}) if rc2 == 0 else None
            names = {p.split('.')[0] for p in (x.split()[0] for x in pk)}
            why = sorted(n for n in names if n in REBOOT_PKGS)
            row.update(state='done', checked=time.time(), count=len(pk), packages=pk,
                       security=security, reboot_after=bool(why), reboot_why=why,
                       reboot_now=reboot_needed(), lines=out.splitlines()[-400:])
        except Exception as e:                          # noqa: BLE001
            row.update(state='done', checked=time.time(), error=str(e)[:400])
        finally:
            put_meta(store, 'host:osup:check', row)
            OSUP_WORK['check'] = False
    thread('osup-check', go)
    return row


PCT_RE = re.compile(r'\s(\d+)/(\d+)\s*$')


def osup_start(store, reboot, by, security=False):
    if osup_busy(store) or OSUP_WORK['run']:
        raise ValueError('an update is going on already')
    if power_busy(store):
        raise ValueError('a restart is under way')
    if pm() != 'dnf':
        raise ValueError('the system is updated with dnf (AlmaLinux, Rocky, RHEL) - none here')
    plan = {'id': secrets.token_hex(6), 'at': time.time(), 'by': by, 'state': 'running',
            'reboot': 'auto' if reboot == 'auto' else 'ask', 'security': bool(security),
            'order': [node_name()]}
    put_meta(store, 'host:osup:plan', plan)
    row = {'plan': plan['id'], 'state': 'updating', 'began': time.time(), 'at': time.time(),
           'pct': 3, 'phase': 'starting', 'lines': []}
    put_meta(store, 'host:osup:row', row)
    event(store, 'settings', '%s started the update of the system%s' % (
        by, ' (security only)' if security else ''), 'warn', who=by)
    OSUP_WORK['run'] = True

    def go():
        argv = ['dnf', '-y', 'upgrade', '--refresh'] + _exclude() + (['--security'] if security
                                                                    else [])
        if under_systemd():
            argv = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--unit',
                    'plexmon-osup-%s' % plan['id'], '-p', 'MemoryMax=3G', '--'] + argv
        last_put = 0.0
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
            OSUP_WORK['proc'] = p
            for raw in iter(p.stdout.readline, b''):
                ln = raw.decode('utf-8', 'replace').rstrip()
                if not ln:
                    continue
                row['lines'] = (row['lines'] + [ln])[-600:]
                m = PCT_RE.search(ln)
                if m and int(m.group(2)):
                    k, n = int(m.group(1)), int(m.group(2))
                    ph = ln.strip().split(' ')[0].lower()
                    row['phase'] = ph
                    # Installing and cleaning up are each about half.
                    base = 50 if ph.startswith(('cleanup', 'verifying', 'erasing')) else 10
                    row['pct'] = min(97, base + int(40 * k / n))
                elif 'Downloading Packages' in ln:
                    row.update(phase='downloading', pct=max(row['pct'], 6))
                if time.time() - last_put > 1.5:
                    put_meta(store, 'host:osup:row', row)
                    last_put = time.time()
            rc = p.wait()
            if rc != 0:
                raise ValueError('dnf said %s: %s' % (rc, row['lines'][-1] if row['lines'] else ''))
            need = reboot_needed()
            row.update(state='done', pct=100, said='updated' + (' - a restart is needed'
                                                               if need else ''),
                       at=time.time(), reboot=need)
            plan['state'] = 'done'
        except Exception as e:                          # noqa: BLE001
            row.update(state='failed', said=str(e)[:300], at=time.time())
            plan['state'] = 'failed'
        finally:
            OSUP_WORK['run'] = False
            OSUP_WORK['proc'] = None
            put_meta(store, 'host:osup:row', row)
            put_meta(store, 'host:osup:plan', plan)
            event(store, 'settings', 'the update of the system: %s' % (row.get('said') or
                                                                       plan['state']),
                  'info' if plan['state'] == 'done' else 'bad')
        if plan['state'] == 'done' and row.get('reboot') and plan['reboot'] == 'auto':
            # Restarted now; looked for updates again once it is up (osup_after_boot).
            try:
                power_ask(store, 'reboot', 'plexmon (after the update)', 'the update needs it')
                return
            except ValueError as e:
                log('the restart after the update: %s' % e, 'warn')
                row['said'] = '%s - the restart by itself failed: %s' % (row.get('said'), e)
                put_meta(store, 'host:osup:row', row)
                event(store, 'settings', 'the restart after the update failed: %s' % e, 'bad')
        try:
            osup_check(store, 'plexmon (after the update)')
        except ValueError:
            pass
    thread('osup', go)
    return plan


def osup_view(store):
    plan = osup_plan(store)
    row = osup_row(store)
    chk = osup_check_row(store)
    hidden = bool(plan) and store.get_meta('host:osup:seen') == plan.get('id') and \
        plan.get('state') != 'running'
    cleared = meta(store, 'host:osup:cleared', {}) or {}
    for part, p in (('row', row), ('check', chk)):
        c = cleared.get(part) or {}
        lines = p.get('lines') or []
        if lines and c and c.get('mark') == '%s|%s' % (p.get('plan') or '', p.get('began') or ''):
            at = max((i for i, ln in enumerate(lines) if ln == c.get('last')), default=None)
            p['lines'] = lines[at + 1:] if at is not None else lines
    # What was said before the server last started is over (2.2.2): an update's
    # "a restart is needed" once it has restarted, a look killed by the restart.
    boot = boot_time() or 0
    if _osup_stale(chk, 'check'):
        chk = dict(chk, state='done', checked=chk.get('began'), interrupted=True,
                   error='stopped by the restart - looked again in a minute')
    stale_row = float(row.get('at') or 0) < boot
    node = {'name': node_name(), 'me': True, 'alive': True,
            'row': row if row.get('plan') == plan.get('id') else {}, 'check': chk,
            'reboot': bool(row.get('reboot') and row.get('plan') == plan.get('id')
                           and not stale_row and not power_busy(store))
            or bool(chk.get('reboot_now') and float(chk.get('checked') or 0) > boot),
            'restarted': bool(row.get('reboot') and stale_row),
            'started': STARTED['at']}
    return {'plan': {} if hidden else plan, 'nodes': [node], 'now': time.time(),
            'cluster': False, 'helper': True, 'pm': pm(), 'plex_too': bool(int(
                cfg('osup_plex', 0) or 0)),
            'power': power_plan(store) if power_busy(store) else {}}


def osup_clear(store):
    out = {}
    for part, p in (('row', osup_row(store)), ('check', osup_check_row(store))):
        if p.get('lines'):
            out[part] = {'mark': '%s|%s' % (p.get('plan') or '', p.get('began') or ''),
                         'last': p['lines'][-1]}
    put_meta(store, 'host:osup:cleared', out)


def osup_cancel(store, by):
    plan = osup_plan(store)
    if plan.get('state') != 'running':
        raise ValueError('nothing is being updated')
    p = OSUP_WORK.get('proc')
    if p and osup_row(store).get('pct', 0) < 10:
        # Still downloading: stopped cleanly; once dnf installs, it finishes.
        p.terminate()
        event(store, 'settings', '%s stopped the update before it put anything in' % by, 'warn',
              who=by)
        return 'stopped before anything was put in'
    return 'dnf is putting packages in: it finishes - stopping it in the middle would break them'


def osup_after_boot(store):
    """After a restart (2.2.2): what the update and the look said before it is
    put right, and the updates looked for again - once per boot."""
    boot = boot_time() or 0
    if not boot or store.get_meta('osup:boot:seen') == str(int(boot)):
        return
    store.set_meta('osup:boot:seen', str(int(boot)))
    plan, chk = osup_plan(store), osup_check_row(store)
    if plan.get('state') == 'running' and _osup_stale(dict(plan, began=plan.get('at')), 'run'):
        plan['state'] = 'failed'
        put_meta(store, 'host:osup:plan', plan)
        row = osup_row(store)
        row.update(state='failed', at=time.time(), said='plexmon or the server restarted while '
                   'dnf ran - see dnf history')
        put_meta(store, 'host:osup:row', row)
    row = osup_row(store)
    if row.get('reboot') and row.get('state') == 'done' and float(row.get('at') or 0) < boot:
        row.update(reboot=False, said='updated - restarted')
        put_meta(store, 'host:osup:row', row)
    if not chk or pm() != 'dnf':
        return
    if _osup_stale(chk, 'check') or float(chk.get('checked') or 0) < boot:
        try:
            osup_check(store, 'plexmon (after a restart)')
        except ValueError:
            pass


def osup_schedule(store):
    osup_after_boot(store)
    if cfg('osup_check'):
        day = due('daily', '', cfg('osup_check_at', '06:15'),
                  store.get_meta('osup:check:day') or '')
        if day and not osup_busy(store):
            store.set_meta('osup:check:day', day)
            try:
                osup_check(store, 'plexmon (on a schedule)')
            except ValueError:
                pass
    if cfg('osup_auto'):
        day = due(cfg('osup_auto_every', 'weekly'), cfg('osup_auto_day', '7'),
                  cfg('osup_auto_at', '04:45'), store.get_meta('osup:auto:day') or '')
        if day and not osup_busy(store):
            store.set_meta('osup:auto:day', day)
            try:
                osup_start(store, cfg('osup_auto_reboot', 'auto'), 'plexmon (on a schedule)',
                           security=cfg('osup_auto_what', 'security') == 'security')
            except ValueError as e:
                event(store, 'settings', 'the update of the system on a schedule did not start: '
                      '%s' % e, 'bad')


# ---------------------------------------------------------------------------
# The database - plexmon's own, and Plex Media Server's
# ---------------------------------------------------------------------------
DB_JOB = {'running': False, 'what': '', 'says': '', 'done': 0, 'whole': 0, 'error': '',
          'result': None, 'began': 0.0, 'ended': 0.0, 'by': ''}
DB_LOCK = threading.Lock()


def db_job(what, by, fn):
    with DB_LOCK:
        if DB_JOB['running']:
            raise ValueError('%s is under way - wait for it' % (DB_JOB['what'] or 'something'))
        DB_JOB.update(running=True, what=what, says='starting', done=0, whole=0, error='',
                      result=None, began=time.time(), ended=0.0, by=by)

    def go():
        try:
            DB_JOB['result'] = fn()
        except Exception as e:                          # noqa: BLE001
            DB_JOB['error'] = str(e)[:400]
            log('%s: %s' % (what, e), 'warn')
        finally:
            DB_JOB.update(running=False, ended=time.time(), says='done' if not DB_JOB['error']
                          else 'failed')
    threading.Thread(target=go, name='plexmon-db', daemon=True).start()
    return dict(DB_JOB)


def says(words, done=None, whole=None):
    DB_JOB['says'] = words
    if done is not None:
        DB_JOB['done'] = done
    if whole is not None:
        DB_JOB['whole'] = whole


def own_dir():
    d = STATE_DIR / 'backups'
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    return d


def plex_dir():
    d = Path(str(cfg('plex_db_dir', '') or (STATE_DIR / 'plex-db')))
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    return d


def _stamp():
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def _files(d, prefix, suffix):
    out = []
    try:
        names = os.listdir(str(d))
    except OSError:
        return out
    for n in names:
        if n.startswith(prefix) and n.endswith(suffix):
            p = os.path.join(str(d), n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({'name': n, 'bytes': st.st_size, 'at': st.st_mtime})
    out.sort(key=lambda x: -x['at'])
    return out


def _keep(d, prefix, suffix, keep):
    for x in _files(d, prefix, suffix)[max(1, int(keep or 1)):]:
        try:
            os.unlink(os.path.join(str(d), x['name']))
        except OSError:
            pass


def sqlite_copy(src, dst, readonly=True):
    """SQLite's own online backup: a consistent copy of a database in use -
    the writer is never stopped, nothing is written to the source."""
    uri = 'file:%s?mode=ro' % src.replace('?', '%3f').replace('#', '%23') if readonly else src
    s = sqlite3.connect(uri, uri=readonly, timeout=60)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d, pages=4096, sleep=0.02)
        finally:
            d.close()
    finally:
        s.close()


def gz(src, dst):
    with open(src, 'rb') as f, gzip.open(dst, 'wb', compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1 << 20)


# ---- plexmon's own ----------------------------------------------------------------
def own_copy(store, why=''):
    d = own_dir()
    name = 'plexmon-%s.db.gz' % _stamp()
    with tempfile.TemporaryDirectory(dir=str(STATE_DIR)) as tmp:
        raw = os.path.join(tmp, 'plexmon.db')
        sqlite_copy(store.db.where(), raw, readonly=False)
        gz(raw, str(d / name))
    os.chmod(str(d / name), 0o600)
    _keep(d, 'plexmon-', '.db.gz', int(cfg('backup_keep', 14) or 14))
    if why:
        event(store, 'database', 'a copy of the database: %s (%s)' % (name, why))
    return name


def own_check(store):
    got = store.db.rows('PRAGMA integrity_check')
    said = [list(r.values())[0] if isinstance(r, dict) else r[0] for r in got]
    return {'ok': said == ['ok'], 'said': said[:20]}


def own_optimize(store, by):
    before = store.db.size()
    store.db.run('VACUUM')
    after = store.db.size()
    event(store, 'database', '%s gave the disk back: %s → %s' % (by, human_bytes(before),
                                                                 human_bytes(after)), who=by)
    return {'before': before, 'after': after}


def own_erase(store, what, by):
    if what == 'events':
        n = store.db.value('SELECT COUNT(*) FROM events') or 0
        own_copy(store, 'before the events were erased')
        store.db.run('DELETE FROM events')
        event(store, 'database', '%s erased every event (%d) - a copy was made just before' % (
            by, n), 'warn', who=by)
        return {'erased': n}
    if what == 'sessions':
        n = store.db.value('SELECT COUNT(*) FROM sessions') or 0
        store.db.run('DELETE FROM sessions WHERE token != ?', ('',))
        return {'erased': n}
    raise ValueError('nothing like that to erase')


def own_tables(store):
    out = []
    for r in store.db.rows("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        n = r['name'] if isinstance(r, dict) else r[0]
        try:
            out.append({'name': n, 'rows': store.db.value('SELECT COUNT(*) FROM "%s"' % n) or 0})
        except Exception:                               # noqa: BLE001
            pass
    return out


def own_restore(store, name, by):
    """A copy put back: checked first, then put in place when plexmon starts
    again (in a moment) - the copy of now is kept beside it."""
    d = own_dir()
    src = d / os.path.basename(name)
    if not src.is_file():
        raise ValueError('there is no copy %s' % name)
    pending = STATE_DIR / 'restore-pending.db'
    with gzip.open(str(src), 'rb') as g, open(str(pending), 'wb') as f:
        shutil.copyfileobj(g, f, 1 << 20)
    c = sqlite3.connect(str(pending))
    try:
        ok = c.execute('PRAGMA integrity_check').fetchone()[0]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()
    if ok != 'ok' or not {'accounts', 'settings', 'meta'} <= tables:
        os.unlink(str(pending))
        raise ValueError('that copy is not a whole plexmon database (%s)' % ok)
    os.chmod(str(pending), 0o600)
    own_copy(store, 'before %s was put back' % name)
    event(store, 'database', '%s put the copy %s back - plexmon starts again with it' % (by, name),
          'warn', who=by)
    restart_plexmon()
    return {'message': 'plexmon starts again with %s in a few seconds' % name}


def apply_pending_restore(db_file):
    """Called before the database is opened: a copy waiting to be put back."""
    pending = Path(str(db_file)).parent / 'restore-pending.db'
    if not pending.is_file():
        return False
    for ext in ('-wal', '-shm'):
        try:
            os.unlink(str(db_file) + ext)
        except OSError:
            pass
    os.replace(str(pending), str(db_file))
    return True


def restart_plexmon():
    if os.environ.get('PLEXMON_TEST'):
        return
    if under_systemd():
        host_run(['systemd-run', '--quiet', '--collect', '--on-active=2', '--unit',
                  'plexmon-restart-%s' % secrets.token_hex(3), SYSTEMCTL, 'restart',
                  'plexmon'], 30)


# ---- Plex Media Server's -----------------------------------------------------------
def plex_running():
    rc, out = host_run([SYSTEMCTL, 'is-active', PLEX_SERVICE], 20)
    return out.strip() == 'active'


def plex_db_files():
    out = []
    for n in (PLEX_DB, PLEX_DB + '-wal', PLEX_BLOBS, PLEX_BLOBS + '-wal'):
        p = PLEX_DB_DIR / n
        try:
            st = p.stat()
            out.append({'name': n, 'bytes': st.st_size, 'at': st.st_mtime})
        except OSError:
            pass
    return out


def plex_own_backups():
    """Plex's own automatic backups (Settings → Scheduled Tasks → Back up
    database every three days): com.plexapp.plugins.library.db-YYYY-MM-DD."""
    out = []
    try:
        names = os.listdir(str(PLEX_DB_DIR))
    except OSError:
        return out
    for n in names:
        m = re.match(r'^com\.plexapp\.plugins\.library(\.blobs)?\.db-(\d{4}-\d{2}-\d{2})$', n)
        if m:
            st = (PLEX_DB_DIR / n).stat()
            out.append({'name': n, 'bytes': st.st_size, 'at': st.st_mtime, 'date': m.group(2),
                        'blobs': bool(m.group(1))})
    out.sort(key=lambda x: (-x['at'], x['blobs']))
    return out


def user_timer():
    """A backup of the Plex database made by the server's own timer (as
    plex-db-backup.timer here): shown, never touched unless asked."""
    out = []
    rc, t = host_run([SYSTEMCTL, 'list-timers', '--all', '--no-legend', '--no-pager'], 20)
    for ln in t.splitlines() if rc == 0 else []:
        f = ln.split()
        units = [x for x in f if x.endswith('.timer')]
        if not units or not re.search(r'plex', units[0], re.I) or units[0].startswith('plexmon'):
            continue
        u = units[0]
        rc1, en = host_run([SYSTEMCTL, 'is-enabled', u], 10)
        rc2, act = host_run([SYSTEMCTL, 'is-active', u], 10)
        rc3, show = host_run([SYSTEMCTL, 'show', u.replace('.timer', '.service'), '-p',
                              'ExecStart', '-p', 'Result', '--value'], 10)
        out.append({'unit': u, 'enabled': en.strip(), 'active': act.strip(),
                    'next': ' '.join(f[:4]) if f[0] != 'n/a' else '',
                    'service': show.strip()[:300]})
    return out


def plex_copies():
    return _files(plex_dir(), 'plexdb-', '.tar.gz')


def plex_check_file(path):
    """integrity_check of a copy - with Plex's own SQLite when it is there
    (its collations), else SQLite's quick_check (the structure)."""
    if os.path.exists(PLEX_SQLITE):
        rc, out = host_run([PLEX_SQLITE, path, 'PRAGMA integrity_check;'], 3600)
        said = [x for x in out.strip().splitlines() if x.strip()]
        return {'ok': rc == 0 and said == ['ok'], 'said': said[:20], 'how': 'Plex SQLite, '
                'integrity_check'}
    c = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
    try:
        said = [r[0] for r in c.execute('PRAGMA quick_check').fetchall()]
    finally:
        c.close()
    return {'ok': said == ['ok'], 'said': said[:20], 'how': 'SQLite, quick_check'}


def plex_copy(store, by, check=True):
    main = PLEX_DB_DIR / PLEX_DB
    if not main.is_file():
        raise ValueError('Plex Media Server\'s database is not at %s' % main)
    d = plex_dir()
    name = 'plexdb-%s.tar.gz' % _stamp()
    whole = 5 if check else 4
    with tempfile.TemporaryDirectory(dir=str(d)) as tmp:
        says('the library database is copied (Plex goes on)', 1, whole)
        sqlite_copy(str(main), os.path.join(tmp, PLEX_DB))
        got = {'name': name, 'check': None}
        if (PLEX_DB_DIR / PLEX_BLOBS).is_file():
            says('the blobs database is copied', 2, whole)
            sqlite_copy(str(PLEX_DB_DIR / PLEX_BLOBS), os.path.join(tmp, PLEX_BLOBS))
        if check:
            says('the copy is checked', 3, whole)
            got['check'] = plex_check_file(os.path.join(tmp, PLEX_DB))
        says('packed', whole - 1, whole)
        part = str(d / (name + '.part'))
        with tarfile.open(part, 'w:gz', compresslevel=6) as tf:
            for n in (PLEX_DB, PLEX_BLOBS):
                if os.path.exists(os.path.join(tmp, n)):
                    tf.add(os.path.join(tmp, n), arcname=n)
        os.replace(part, str(d / name))
        os.chmod(str(d / name), 0o600)
    _keep(d, 'plexdb-', '.tar.gz', int(cfg('plex_db_keep', 7) or 7))
    got['bytes'] = (d / name).stat().st_size
    put_meta(store, 'plexdb:last', dict(got, at=time.time(), by=by))
    ck = got.get('check') or {}
    event(store, 'database', '%s: a copy of Plex Media Server\'s database, %s (%s)%s' % (
        by, name, human_bytes(got['bytes']), '' if not ck else (' - checked: ok' if ck.get('ok')
                                                              else ' - checked: NOT ok')),
          'info' if not ck or ck.get('ok') else 'bad', who=by)
    says('done', whole, whole)
    return got


def plex_restore(store, name, by, own=False):
    """A copy put back: checked; Plex stopped; its database files now set
    aside (kept); the copy put in their place with Plex's owner; Plex started.
    Anything going wrong after Plex stopped puts its files back."""
    src_dir = PLEX_DB_DIR if own else plex_dir()
    src = src_dir / os.path.basename(name)
    if not src.is_file():
        raise ValueError('there is no copy %s' % name)
    st = PLEX_DB_DIR.stat()
    uid, gid = st.st_uid, st.st_gid
    with tempfile.TemporaryDirectory(dir=str(plex_dir())) as tmp:
        says('the copy is opened', 1, 6)
        if own:
            shutil.copyfile(str(src), os.path.join(tmp, PLEX_DB))
            blob = PLEX_DB_DIR / (PLEX_BLOBS + '-' + name[-10:])
            if blob.is_file():
                shutil.copyfile(str(blob), os.path.join(tmp, PLEX_BLOBS))
        else:
            with tarfile.open(str(src), 'r:gz') as tf:
                for m in tf.getmembers():
                    if m.name in (PLEX_DB, PLEX_BLOBS) and m.isfile():
                        tf.extract(m, tmp)
        if not os.path.exists(os.path.join(tmp, PLEX_DB)):
            raise ValueError('%s has no library database in it' % name)
        says('the copy is checked', 2, 6)
        ck = plex_check_file(os.path.join(tmp, PLEX_DB))
        if not ck['ok']:
            raise ValueError('the copy is not whole (%s): %s' % (ck['how'], '; '.join(ck['said'])))
        aside = plex_dir() / ('before-restore-%s' % _stamp())
        aside.mkdir(mode=0o700)
        was = plex_running()
        says('Plex Media Server is stopped', 3, 6)
        if was:
            rc, out = host_run([SYSTEMCTL, 'stop', PLEX_SERVICE], 300)
            if rc != 0:
                raise ValueError('Plex Media Server would not stop: %s' % out.strip()[-200:])
        moved = []
        try:
            says('its database is set aside and the copy put in', 4, 6)
            for n in (PLEX_DB, PLEX_DB + '-wal', PLEX_DB + '-shm', PLEX_BLOBS, PLEX_BLOBS + '-wal',
                      PLEX_BLOBS + '-shm'):
                p = PLEX_DB_DIR / n
                if p.exists():
                    shutil.move(str(p), str(aside / n))
                    moved.append(n)
            for n in (PLEX_DB, PLEX_BLOBS):
                if os.path.exists(os.path.join(tmp, n)):
                    shutil.copyfile(os.path.join(tmp, n), str(PLEX_DB_DIR / n))
                    os.chown(str(PLEX_DB_DIR / n), uid, gid)
                    os.chmod(str(PLEX_DB_DIR / n), 0o644)
        except Exception:
            for n in moved:
                try:
                    if (PLEX_DB_DIR / n).exists():
                        os.unlink(str(PLEX_DB_DIR / n))
                    shutil.move(str(aside / n), str(PLEX_DB_DIR / n))
                except OSError:
                    pass
            if was:
                host_run([SYSTEMCTL, 'start', PLEX_SERVICE], 300)
            raise
        says('Plex Media Server is started', 5, 6)
        if was:
            host_run([SYSTEMCTL, 'start', PLEX_SERVICE], 300)
    event(store, 'database', '%s put the copy %s of Plex Media Server\'s database back - its '
          'database before is kept in %s' % (by, name, aside), 'warn', who=by)
    says('done', 6, 6)
    return {'aside': str(aside), 'checked': ck}


def plex_api(path):
    import plexmon_plex as PX
    return PX.put(path)


def db_view(store):
    own = {'where': store.db.where(), 'bytes': store.db.size(), 'tables': own_tables(store),
           'copies': _files(own_dir(), 'plexmon-', '.db.gz'), 'dir': str(own_dir()),
           'key': str(A.sealing_key_path()) if hasattr(A, 'sealing_key_path') else ''}
    try:
        du = shutil.disk_usage(str(STATE_DIR))
        own['disk'] = {'total': du.total, 'free': du.free}
    except OSError:
        pass
    pdb = {'dir': str(PLEX_DB_DIR), 'files': plex_db_files(), 'own_backups': plex_own_backups(),
           'copies': plex_copies(), 'copies_dir': str(plex_dir()),
           'last': meta(store, 'plexdb:last', {}) or {}, 'running': plex_running(),
           'plex_sqlite': os.path.exists(PLEX_SQLITE), 'timers': user_timer()}
    try:
        du = shutil.disk_usage(str(plex_dir()))
        pdb['disk'] = {'total': du.total, 'free': du.free}
    except OSError:
        pass
    return {'own': own, 'plex': pdb, 'job': dict(DB_JOB), 'now': time.time()}


def db_schedule(store):
    if int(cfg('backup_daily', 1) or 0):
        day = due('daily', '', cfg('backup_at', '03:30'), store.get_meta('db:copy:day') or '')
        if day:
            store.set_meta('db:copy:day', day)
            try:
                own_copy(store)
            except Exception as e:                      # noqa: BLE001
                event(store, 'database', 'the copy of the database of the day failed: %s' % e,
                      'bad')
    if int(cfg('plex_db_daily', 0) or 0) and not DB_JOB['running']:
        day = due(cfg('plex_db_every', 'daily'), cfg('plex_db_day', '7'),
                  cfg('plex_db_at', '03:45'), store.get_meta('plexdb:copy:day') or '')
        if day:
            store.set_meta('plexdb:copy:day', day)
            try:
                db_job('a copy of Plex\'s database', 'plexmon (on a schedule)',
                       lambda: plex_copy(store, 'plexmon (on a schedule)',
                                         check=bool(int(cfg('plex_db_check', 1) or 0))))
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Backups to another machine - otp's destinations and transports, as they are
# ---------------------------------------------------------------------------
RB_KINDS = ('folder', 'ftp', 'ftps', 'sftp', 'scp', 'smb')
RB_KIND_WORDS = {'folder': 'a folder', 'ftp': 'FTP', 'ftps': 'FTPS', 'sftp': 'SFTP',
                 'scp': 'SCP (ssh)', 'smb': 'SMB (Windows share, Samba)'}
RB_PORTS = {'ftp': 21, 'ftps': 21, 'sftp': 22, 'scp': 22, 'smb': 445}
RB_SEALED = ('password', 'key', 'client_key', 'key_pass')
RB_BIND = 'rbackup'
RB_LOCK = threading.Lock()
RB_JOB = {'running': False, 'what': '', 'says': '', 'done': 0, 'whole': 0, 'error': '',
          'began': 0, 'ended': 0, 'result': None, 'by': ''}
RB_ITER = 600000
RB_NAME_RE = re.compile(r'^plexmon-backup-(?P<site>[A-Za-z0-9_.-]+?)-(?P<node>[A-Za-z0-9_.-]+)-'
                        r'(?P<stamp>\d{8}-\d{6})\.tar\.gz(?P<enc>\.enc)?\Z')
RB_TIMEOUT = 60


class RbError(Exception):
    pass


def rb_clean(text, what, most=200, pattern=None, empty=True):
    t = str(text if text is not None else '').strip()
    if not t and empty:
        return ''
    if len(t) > most or re.search(r'[\x00-\x1f\x7f]', t):
        raise ValueError('%s: at most %d characters, one line' % (what, most))
    if pattern and t and not re.match(pattern, t):
        raise ValueError('%s: %r is not one' % (what, t[:60]))
    return t


def rb_slug(text):
    return re.sub(r'[^A-Za-z0-9_.]+', '_', str(text or 'plexmon')).strip('_.')[:40] or 'plexmon'


# ---- the destinations ---------------------------------------------------------------
def rb_targets(store):
    try:
        got = json.loads(store.get_meta('rbackup:targets') or '[]')
    except ValueError:
        got = []
    return [t for t in got if isinstance(t, dict) and t.get('id')]


def rb_put_targets(store, targets):
    store.set_meta('rbackup:targets', json.dumps(targets))


def rb_target(store, tid):
    return next((t for t in rb_targets(store) if t['id'] == str(tid)), None)


def rb_secret(t, k):
    v = t.get(k) or ''
    return (unseal_secret(v, bind=RB_BIND) or '') if v else ''


def ssh_fingerprints(lines):
    """The SHA256 fingerprints of known_hosts lines, as ssh-keygen says them."""
    if not lines.strip() or not shutil.which('ssh-keygen'):
        return []
    got = subprocess.run(['ssh-keygen', '-lf', '-'], input=lines, capture_output=True,
                         text=True, timeout=20)
    return [x.strip() for x in got.stdout.splitlines() if x.strip()]


def rb_view(t):
    """A destination as the pages see it: nothing secret, what it has said."""
    out = {k: v for k, v in t.items() if k not in RB_SEALED}
    for k in RB_SEALED:
        out['has_' + k] = bool(t.get(k))
    out['kind_words'] = RB_KIND_WORDS.get(t.get('kind'), t.get('kind'))
    if t.get('password'):
        out['password'] = secret_shown(rb_secret(t, 'password'), last=True)
    if t.get('hostkey'):
        out['hostkey_fp'] = ssh_fingerprints(t['hostkey'])
    if t.get('client_cert'):
        try:
            out['client_cert_info'] = cert_info(t['client_cert'])
        except Exception:                               # noqa: BLE001
            out['client_cert_info'] = None
    if t.get('ca'):
        try:
            out['ca_info'] = cert_info(t['ca'])
        except Exception:                               # noqa: BLE001
            out['ca_info'] = None
    return out


def rb_checked(data, old=None):
    """A destination as typed on the pages, checked; secrets left out or as
    dots keep what was there."""
    old = dict(old or {})
    if not isinstance(data, dict):
        raise ValueError('a destination is an object')
    kind = str(data.get('kind') or old.get('kind') or '')
    if kind not in RB_KINDS:
        raise ValueError('the kind is one of %s' % ', '.join(RB_KINDS))
    t = {'id': old.get('id') or secrets.token_hex(6), 'kind': kind}
    t['name'] = rb_clean(data.get('name'), 'the name', 60) or \
        ('%s %s' % (RB_KIND_WORDS[kind], data.get('host') or data.get('path') or '')).strip()
    t['enabled'] = 1 if data.get('enabled', old.get('enabled', 1)) else 0
    t['path'] = rb_clean(data.get('path'), 'the folder', 400)
    if kind == 'folder':
        if not t['path'].startswith('/'):
            raise ValueError('the folder is a whole path on this machine (/mnt/backup/plexmon)')
        if re.search(r'(^|/)\.\.(/|$)', t['path']):
            raise ValueError('the folder has no .. in it')
    else:
        t['host'] = rb_clean(data.get('host'), 'the server', 253,
                             r'^[A-Za-z0-9][A-Za-z0-9.:_-]*\Z', empty=False)
        try:
            port = int(data.get('port') or RB_PORTS.get(kind) or 0)
        except (TypeError, ValueError):
            raise ValueError('the port is a number')
        if kind == 'ftps' and data.get('implicit') and not data.get('port'):
            port = 990
        if not 1 <= port <= 65535:
            raise ValueError('the port is 1 to 65535')
        t['port'] = port
        t['user'] = rb_clean(data.get('user'), 'the account', 128, r'^[^\s:@/\\]+\Z') or ''
        # Nothing a tool on the way reads as more (1.0.136): smbclient takes
        # ; as the next command whatever the quotes, ssh a leading - as an
        # option of its own.
        if t['user'].startswith('-'):
            raise ValueError('the account does not begin with -')
        if re.search(r'[;"]', t['path']) or t['path'].lstrip().startswith('-'):
            raise ValueError('the folder has no ; or " in it, and does not begin with -')
        if kind != 'smb' and re.search(r'(^|/)\.\.(/|$)', t['path']):
            raise ValueError('the folder has no .. in it')
        if kind == 'scp' and re.search(r'[^A-Za-z0-9._/~+@%=,-]', t['path']):
            # scp before OpenSSH 9 hands the far path to the shell there
            # (1.0.139): $( ), `, |, & and spaces ran or broke.
            raise ValueError('the folder (SCP) is letters, digits and . _ / ~ + @ % = , - '
                             'only - use SFTP for any other')
    pw = data.get('password')
    # The saved password goes only to the server it was saved for (1.0.149):
    # with another server typed in, Try it sent it there - to whoever the
    # administrator chose. Typed again for a new one.
    moved = bool(old) and (old.get('kind') != kind or
                           str(old.get('host') or '').lower() != str(t.get('host') or '').lower() or
                           int(old.get('port') or 0) != int(t.get('port') or 0))
    if (pw is None or is_masked(pw)) and moved and old.get('password'):
        raise ValueError('the server changed: type the password again')
    if pw is None or is_masked(pw):
        t['password'] = old.get('password') or ''
    else:
        pw = str(pw)
        if len(pw) > 512 or '\n' in pw or '\x00' in pw:
            raise ValueError('the password is one line, at most 512 characters')
        t['password'] = seal_secret(pw, bind=RB_BIND) if pw else ''
    if kind in ('sftp', 'scp'):
        for k in ('key', 'pub', 'ssh_cert', 'hostkey', 'key_pass'):
            if k in old:
                t[k] = old[k]
        if data.get('ssh_cert') is not None:
            c = str(data.get('ssh_cert') or '').strip()
            if c and not re.match(r'^[a-z0-9@.-]+-cert-v01@openssh\.com AAAA[A-Za-z0-9+/=]+( .*)?\Z',
                                  c):
                raise ValueError('the SSH certificate is the one line of a ...-cert.pub file')
            t['ssh_cert'] = c
    if kind in ('ftp', 'ftps'):
        t['passive'] = 0 if data.get('passive', old.get('passive', 1)) in (0, '0', False) else 1
    if kind == 'ftps':
        t['implicit'] = 1 if data.get('implicit', old.get('implicit')) else 0
        check = str(data.get('tls_check') or old.get('tls_check') or 'system')
        if check not in ('system', 'ca', 'pin', 'none'):
            raise ValueError('the server certificate is checked against system, ca, pin or none')
        t['tls_check'] = check
        for k in ('ca', 'pin', 'client_cert', 'client_key'):
            if k in old:
                t[k] = old[k]
        if data.get('ca') is not None:
            ca = str(data.get('ca') or '').strip()
            if ca and not PEM_CERT.search(ca):
                raise ValueError('the authority is a PEM certificate (-----BEGIN CERTIFICATE-----)')
            t['ca'] = '\n'.join(m.group(0) for m in PEM_CERT.finditer(ca)) + '\n' if ca else ''
        if data.get('pin') is not None:
            pin = re.sub(r'[^0-9A-Fa-f]', '', str(data.get('pin') or '')).lower()
            if pin and len(pin) != 64:
                raise ValueError('the pinned fingerprint is SHA-256: 64 hex digits')
            t['pin'] = pin
        if check == 'ca' and not t.get('ca'):
            raise ValueError('checked against an authority: give its certificate')
        if check == 'pin' and not t.get('pin'):
            raise ValueError('pinned: the fingerprint is needed - Try it shows the server\'s')
    if kind == 'smb':
        t['share'] = rb_clean(data.get('share'), 'the share', 80, r'^[^\\/:*?"<>|;-][^\\/:*?"<>|;]*\Z',
                              empty=False)
        t['domain'] = rb_clean(data.get('domain'), 'the domain', 64, r'^[A-Za-z0-9._-]+\Z')
        t['smb_encrypt'] = 1 if data.get('smb_encrypt', old.get('smb_encrypt', 1)) else 0
        if re.search(r'(^|[\\/])\.\.([\\/]|$)', t['path']):
            raise ValueError('the folder has no .. in it')
    for k in ('last', 'made'):
        if k in old:
            t[k] = old[k]
    return t


def rb_save(store, data, by):
    with RB_LOCK:
        targets = rb_targets(store)
        old = next((x for x in targets if x['id'] == str(data.get('id') or '')), None)
        if data.get('id') and not old:
            raise ValueError('no such destination')
        t = rb_checked(data, old)
        hisec_backup_target(t)              # 1.0.168
        if old:
            targets = [t if x['id'] == t['id'] else x for x in targets]
        else:
            t['made'] = time.time()
            if len(targets) >= 20:
                raise ValueError('20 destinations at most')
            targets.append(t)
        rb_put_targets(store, targets)
    store.add_event('settings', '%s %s the backup destination %s (%s)' % (
        by, 'changed' if old else 'added', t['name'], RB_KIND_WORDS[t['kind']]), who=by)
    return t


def rb_set_fields(store, tid, by, said, **fields):
    with RB_LOCK:
        targets = rb_targets(store)
        t = next((x for x in targets if x['id'] == str(tid)), None)
        if not t:
            raise ValueError('no such destination')
        for k, v in fields.items():
            if v is None:
                t.pop(k, None)
            else:
                t[k] = v
        rb_put_targets(store, targets)
    if said:
        store.add_event('settings', '%s: the backup destination %s - %s' % (by, t['name'], said),
                        who=by)
    return t


def rb_delete(store, tid, by):
    with RB_LOCK:
        targets = rb_targets(store)
        t = next((x for x in targets if x['id'] == str(tid)), None)
        if not t:
            raise ValueError('no such destination')
        rb_put_targets(store, [x for x in targets if x['id'] != t['id']])
    store.add_event('settings', '%s took away the backup destination %s' % (by, t['name']),
                    'warn', who=by)


# ---- keys and certificates ----------------------------------------------------------
def ssh_keygen(comment):
    if not shutil.which('ssh-keygen'):
        raise ValueError('ssh-keygen is not on this machine (dnf install openssh-clients)')
    with tempfile.TemporaryDirectory(prefix='plexmon-key-') as d:
        path = os.path.join(d, 'k')
        got = subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', comment,
                              '-f', path], capture_output=True, text=True, timeout=30)
        if got.returncode != 0:
            raise ValueError('ssh-keygen: %s' % (got.stderr or got.stdout).strip()[:200])
        with open(path) as f:
            private = f.read()
        with open(path + '.pub') as f:
            public = f.read().strip()
    return private, public


def ssh_public_of(private, passphrase=''):
    """The public half of a private key given - and that it is one."""
    if not shutil.which('ssh-keygen'):
        raise ValueError('ssh-keygen is not on this machine (dnf install openssh-clients)')
    private = str(private or '').replace('\r\n', '\n').strip() + '\n'
    if 'PRIVATE KEY-----' not in private:
        raise ValueError('that is not a private key (-----BEGIN OPENSSH PRIVATE KEY----- or '
                         'another PEM private key)')
    with tempfile.TemporaryDirectory(prefix='plexmon-key-') as d:
        path = os.path.join(d, 'k')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(private)
        # The passphrase through SSH_ASKPASS, not on the command line where
        # `ps` shows it (1.0.136).
        env = dict(os.environ, DISPLAY=':0', SSH_ASKPASS_REQUIRE='force')
        if passphrase:
            ask = os.path.join(d, 'ask')
            with open(ask, 'w') as f:
                f.write('#!/bin/sh\nprintf "%s\\n" "$OTP_RB_SECRET"\n')
            os.chmod(ask, 0o700)
            env.update(SSH_ASKPASS=ask, OTP_RB_SECRET=passphrase)
            argv = ['ssh-keygen', '-y', '-f', path]
        else:
            argv = ['ssh-keygen', '-y', '-P', '', '-f', path]
        got = subprocess.run(argv, capture_output=True, text=True, timeout=20, env=env,
                             stdin=subprocess.DEVNULL)
    if got.returncode != 0:
        said = (got.stderr or got.stdout).strip()
        if 'passphrase' in said.lower() or 'incorrect' in said.lower():
            raise ValueError('the key is locked with a passphrase: give it too')
        raise ValueError('ssh-keygen does not read it: %s' % said[:200])
    return private, got.stdout.strip()


def rb_client_cert_make(cn, days=1825):
    """A key and a self-signed client certificate for FTPS."""
    key = new_key('ec')
    with tempfile.TemporaryDirectory(prefix='plexmon-crt-') as d:
        kf = os.path.join(d, 'k.pem')
        with open(kf, 'w') as f:
            f.write(key)
        os.chmod(kf, 0o600)
        cn = re.sub(r'[^A-Za-z0-9.@_-]', '', str(cn or 'plexmon-backup'))[:64] or 'plexmon-backup'
        crt = _openssl(['req', '-new', '-x509', '-key', kf, '-days', str(int(days)), '-subj',
                        '/CN=' + cn, '-addext', 'extendedKeyUsage=clientAuth',
                        '-addext', 'keyUsage=critical,digitalSignature'],
                       what='the certificate').decode()
    return key, crt


def rb_client_cert_given(text, password=''):
    """A client certificate brought: PEM (certificate and key, in any order)
    or PFX (base64 of it, with its password)."""
    text = str(text or '').strip()
    if 'BEGIN' in text:
        certs = [m.group(0) for m in PEM_CERT.finditer(text)]
        m = re.search(r'-----BEGIN (?:[A-Z ]*)PRIVATE KEY-----[\s\S]+?-----END (?:[A-Z ]*)'
                      r'PRIVATE KEY-----', text)
        if not certs or not m:
            raise ValueError('a client certificate needs its certificate and its private key')
        key = m.group(0) + '\n'
        if 'ENCRYPTED' in key:
            key = _openssl(['pkey', '-passin', 'env:OTP_PW'], key.encode(),
                           env={'OTP_PW': password}, what='the key (its password)').decode()
        if _pub_of_key(key).strip() != _pub_of_cert(certs[0]).strip():
            raise ValueError('the key is not the one of this certificate')
        return key, '\n'.join(certs) + '\n'
    try:
        pfx = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        raise ValueError('neither PEM nor PFX')
    with tempfile.TemporaryDirectory(prefix='plexmon-pfx-') as d:
        pf = os.path.join(d, 'c.pfx')
        with open(pf, 'wb') as f:
            f.write(pfx)
        both = ''
        for extra in ([], ['-legacy']):
            try:
                both = _openssl(['pkcs12', '-in', pf, '-nodes', '-passin', 'env:OTP_PW'] + extra,
                                env={'OTP_PW': password}, what='the PFX (its password?)').decode()
                break
            except ValueError:
                if extra:
                    raise
    return rb_client_cert_given(both)


# ---- the transports ------------------------------------------------------------------
class RbFolder(object):
    def __init__(self, t):
        self.t = t
        self.dir = t['path']

    def open(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as e:
            raise RbError('the folder %s cannot be made: %s' % (self.dir, e))
        if not os.access(self.dir, os.W_OK):
            raise RbError('plexmon (root) may not write into %s' % self.dir)

    def put(self, local, name):
        part = os.path.join(self.dir, '.%s.part' % name)
        shutil.copyfile(local, part)
        os.chmod(part, 0o640)
        os.replace(part, os.path.join(self.dir, name))

    def get(self, name, local):
        shutil.copyfile(os.path.join(self.dir, rb_safe_name(name)), local)

    def list(self):
        out = []
        for e in os.scandir(self.dir):
            if e.is_file() and not e.name.startswith('.'):
                st = e.stat()
                out.append({'name': e.name, 'size': st.st_size, 'mtime': st.st_mtime})
        return out

    def delete(self, name):
        os.unlink(os.path.join(self.dir, rb_safe_name(name)))

    def close(self):
        pass


def rb_safe_name(name):
    name = str(name or '')
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,200}\Z', name):
        raise RbError('not a file name of a backup: %r' % name[:80])
    return name


class _ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTPS on its own port (990): TLS from the first byte."""
    def connect(self, host='', port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile('r', encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class _ReuseMixin(object):
    """The data connection resumes the control connection's TLS session:
    vsftpd, ProFTPD and FileZilla Server refuse one that does not (522)."""
    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host,
                                            session=self.sock.session)
        return conn, size


class RbFtpTls(_ReuseMixin, ftplib.FTP_TLS):
    pass


class RbFtpTlsImplicit(_ReuseMixin, _ImplicitFTP_TLS):
    pass


class RbFtp(object):
    def __init__(self, t):
        self.t = t
        self.ftp = None
        self.tmp = None
        self.seen = {}

    def _context(self):
        t = self.t
        check = t.get('tls_check') or 'system'
        if check == 'ca':
            ctx = ssl.create_default_context(cadata=t.get('ca') or '')
        elif check == 'system':
            ctx = ssl.create_default_context()
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if t.get('client_cert') and t.get('client_key'):
            self.tmp = tempfile.mkdtemp(prefix='plexmon-ftps-')
            cf, kf = os.path.join(self.tmp, 'c.pem'), os.path.join(self.tmp, 'k.pem')
            with open(cf, 'w') as f:
                f.write(t['client_cert'])
            fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(rb_secret(t, 'client_key'))
            ctx.load_cert_chain(cf, kf)
        return ctx

    def open(self):
        t = self.t
        try:
            if t['kind'] == 'ftps':
                cls = RbFtpTlsImplicit if t.get('implicit') else RbFtpTls
                self.ftp = cls(context=self._context(), timeout=RB_TIMEOUT)
                self.ftp.connect(t['host'], int(t['port']))
                if not t.get('implicit'):
                    self.ftp.auth()
                der = self.ftp.sock.getpeercert(True)
                fp = hashlib.sha256(der).hexdigest() if der else ''
                self.seen = {'fingerprint': fp}
                try:
                    pem = ssl.DER_cert_to_PEM_cert(der)
                    self.seen['cert'] = cert_info(pem)
                except Exception:                       # noqa: BLE001
                    pass
                if (t.get('tls_check') == 'pin') and fp != (t.get('pin') or ''):
                    raise RbError('the server answers with another certificate than the one '
                                  'pinned (SHA-256 %s)' % fp)
            else:
                self.ftp = ftplib.FTP(timeout=RB_TIMEOUT)
                self.ftp.connect(t['host'], int(t['port']))
            self.ftp.login(t.get('user') or 'anonymous', rb_secret(t, 'password'))
            if t['kind'] == 'ftps':
                self.ftp.prot_p()
            self.ftp.set_pasv(bool(t.get('passive', 1)))
            self._cd(t.get('path') or '')
        except ssl.SSLCertVerificationError as e:
            raise RbError('the server\'s certificate is not trusted: %s - check it against '
                          'its authority, or pin it' % e.verify_message)
        except ftplib.error_perm as e:
            raise RbError('the FTP server said: %s' % str(e)[:200])
        except (OSError, EOFError, ftplib.Error) as e:
            raise RbError(str(e)[:200] or e.__class__.__name__)

    def _cd(self, path):
        if not path:
            return
        if path.startswith('/'):
            self.ftp.cwd('/')
        for part in [p for p in path.split('/') if p]:
            try:
                self.ftp.cwd(part)
            except ftplib.error_perm:
                self.ftp.mkd(part)
                self.ftp.cwd(part)

    def put(self, local, name):
        with open(local, 'rb') as f:
            self.ftp.storbinary('STOR .%s.part' % name, f, blocksize=262144)
        try:
            self.ftp.delete(name)
        except ftplib.error_perm:
            pass
        self.ftp.rename('.%s.part' % name, name)

    def get(self, name, local):
        with open(local, 'wb') as f:
            self.ftp.retrbinary('RETR %s' % rb_safe_name(name), f.write, blocksize=262144)

    def list(self):
        out = []
        try:
            for name, facts in self.ftp.mlsd(facts=['type', 'size', 'modify']):
                if facts.get('type') == 'file':
                    m = facts.get('modify') or ''
                    try:
                        mt = datetime.strptime(m[:14], '%Y%m%d%H%M%S').replace(
                            tzinfo=timezone.utc).timestamp()
                    except ValueError:
                        mt = 0
                    out.append({'name': name, 'size': int(facts.get('size') or 0), 'mtime': mt})
            return out
        except ftplib.error_perm:
            pass
        for name in self.ftp.nlst():
            name = name.rsplit('/', 1)[-1]
            if name in ('.', '..'):
                continue
            try:
                size = self.ftp.size(name) or 0
            except ftplib.error_perm:
                continue            # a folder
            out.append({'name': name, 'size': size, 'mtime': 0})
        return out

    def delete(self, name):
        self.ftp.delete(rb_safe_name(name))

    def close(self):
        try:
            if self.ftp:
                self.ftp.quit()
        except Exception:                               # noqa: BLE001
            try:
                self.ftp.close()
            except Exception:                           # noqa: BLE001
                pass
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


def ssh_keyscan(host, port):
    """The server's host keys, as known_hosts lines - and their fingerprints."""
    if not shutil.which('ssh-keyscan'):
        raise ValueError('ssh-keyscan is not on this machine (dnf install openssh-clients)')
    got = subprocess.run(['ssh-keyscan', '-T', '10', '-p', str(int(port)), host],
                         capture_output=True, text=True, timeout=30)
    lines = [x for x in got.stdout.splitlines() if x.strip() and not x.startswith('#')]
    if not lines:
        raise ValueError('%s:%s did not show a host key: %s' % (
            host, port, (got.stderr or '').strip().splitlines()[-1:] or 'no answer'))
    return '\n'.join(lines) + '\n', ssh_fingerprints('\n'.join(lines) + '\n')


class RbSsh(object):
    """SFTP (the sftp program) or SCP (scp, and ssh for the list and the
    deleting) - OpenSSH itself, the server's host key checked against the one
    trusted on the pages, a key of its own or a password."""
    def __init__(self, t):
        self.t = t
        self.d = None

    def open(self):
        t = self.t
        tools = ['ssh'] + (['sftp'] if t['kind'] == 'sftp' else ['scp'])
        for tool in tools:
            if not shutil.which(tool):
                raise RbError('%s is not on this machine (dnf install openssh-clients)' % tool)
        if not t.get('hostkey'):
            raise RbError('the server\'s host key is not trusted yet: Try it, then trust the '
                          'fingerprint it shows')
        self.d = tempfile.mkdtemp(prefix='plexmon-ssh-')
        kh = os.path.join(self.d, 'known_hosts')
        with open(kh, 'w') as f:
            f.write(t['hostkey'])
        self.opts = ['-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + kh,
                     '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=20',
                     '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4',
                     '-o', 'LogLevel=ERROR', '-o', 'IdentitiesOnly=yes', '-F', '/dev/null']
        key = rb_secret(t, 'key')
        if key:
            kf = os.path.join(self.d, 'id')
            fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(key if key.endswith('\n') else key + '\n')
            self.opts += ['-i', kf]
            if t.get('ssh_cert'):
                cf = os.path.join(self.d, 'id-cert.pub')
                with open(cf, 'w') as f:
                    f.write(t['ssh_cert'] + '\n')
                self.opts += ['-o', 'CertificateFile=' + cf]
        else:
            self.opts += ['-o', 'IdentityFile=/dev/null']
        # A password (or the key's passphrase) through SSH_ASKPASS: never on
        # a command line, never in a file of plexmon (root)'s.
        self.env = dict(os.environ, DISPLAY=':0', SSH_ASKPASS_REQUIRE='force')
        # Each prompt its own answer (1.0.139): with a password and a key that
        # has a passphrase, the password went to the key, and the key failed.
        pw = rb_secret(t, 'password') or rb_secret(t, 'key_pass')
        kp = rb_secret(t, 'key_pass') or rb_secret(t, 'password')
        ask = os.path.join(self.d, 'ask')
        with open(ask, 'w') as f:
            f.write('#!/bin/sh\ncase "$1" in\n  *assphrase*) printf "%s\\n" "$OTP_RB_KEYPASS";;\n'
                    '  *) printf "%s\\n" "$OTP_RB_SECRET";;\nesac\n')
        os.chmod(ask, 0o700)
        self.env.update(SSH_ASKPASS=ask, OTP_RB_SECRET=pw, OTP_RB_KEYPASS=kp)
        auths = ['publickey'] if key else []
        if rb_secret(t, 'password'):
            auths += ['password', 'keyboard-interactive']
        if not auths:
            raise RbError('neither a key nor a password is set')
        self.opts += ['-o', 'PreferredAuthentications=' + ','.join(auths),
                      '-o', 'NumberOfPasswordPrompts=1']
        if rb_secret(t, 'password') or rb_secret(t, 'key_pass'):
            # sftp -b asks ssh for batch mode, which takes no password; the
            # first value given wins, and this comes first.
            self.opts += ['-o', 'BatchMode=no']
        host = str(t['host']).strip('[]')
        self.dest = '%s@%s' % (t.get('user') or 'root', host)
        # scp and sftp read user@host:path - an IPv6 address in brackets
        # there (1.0.146), ssh takes it bare.
        self.rdest = '%s@[%s]' % (t.get('user') or 'root', host) if ':' in host else self.dest
        self.dir = (t.get('path') or '').rstrip('/') or '.'
        # Signed in and the folder made, at once - the errors said here.
        if t['kind'] == 'sftp':
            parts = [p for p in self.dir.split('/') if p and p != '.']
            cmds = []
            acc = '/' if self.dir.startswith('/') else ''
            for p in parts:
                acc = (acc + '/' + p) if acc not in ('', '/') else (acc + p)
                cmds.append('-mkdir "%s"' % acc)
            cmds.append('cd "%s"' % self.dir)
            self._sftp(cmds)
        else:
            self._ssh('mkdir -p -- %s && test -w %s' % (shlex_quote(self.dir),
                                                        shlex_quote(self.dir)))

    def _run(self, args, what, stdin=None, timeout=3600):
        got = subprocess.run(args, input=stdin, capture_output=True, text=True,
                             timeout=timeout, env=self.env, start_new_session=True)
        if got.returncode != 0:
            said = (got.stderr or got.stdout or '').strip().splitlines()
            said = [x for x in said if x.strip() and not x.startswith('Warning: Permanently')]
            tail = ' / '.join(said[-3:])[:300] or 'it said nothing (exit %d)' % got.returncode
            if 'Host key verification failed' in tail or 'REMOTE HOST IDENTIFICATION' in tail:
                tail = ('the server shows another host key than the one trusted - if it was '
                        'reinstalled on purpose, Try it and trust the new one')
            elif 'Permission denied' in tail:
                tail = 'the server did not let the account in (%s)' % tail
            raise RbError('%s: %s' % (what, tail))
        return got.stdout

    def _ssh(self, command):
        return self._run(['ssh'] + self.opts + ['-p', str(int(self.t['port'])), self.dest,
                                                command], 'ssh')

    def _sftp(self, cmds, timeout=3600):
        batch = os.path.join(self.d, 'batch')
        with open(batch, 'w') as f:
            f.write('\n'.join(cmds) + '\n')
        return self._run(['sftp'] + self.opts + ['-P', str(int(self.t['port'])), '-b', batch,
                                                 self.rdest], 'sftp', timeout=timeout)

    def put(self, local, name):
        name = rb_safe_name(name)
        if self.t['kind'] == 'sftp':
            self._sftp(['cd "%s"' % self.dir, 'put "%s" ".%s.part"' % (local, name),
                        '-rm "%s"' % name, 'rename ".%s.part" "%s"' % (name, name)])
        else:
            self._run(['scp'] + self.opts + ['-P', str(int(self.t['port'])), '-q', local,
                                             '%s:%s/.%s.part' % (self.rdest, self.dir, name)],
                      'scp')
            self._ssh('mv -f -- %s %s' % (shlex_quote('%s/.%s.part' % (self.dir, name)),
                                          shlex_quote('%s/%s' % (self.dir, name))))

    def get(self, name, local):
        name = rb_safe_name(name)
        if self.t['kind'] == 'sftp':
            self._sftp(['cd "%s"' % self.dir, 'get "%s" "%s"' % (name, local)])
        else:
            self._run(['scp'] + self.opts + ['-P', str(int(self.t['port'])), '-q',
                                             '%s:%s/%s' % (self.rdest, self.dir, name), local],
                      'scp')

    def list(self):
        if self.t['kind'] == 'sftp':
            text = self._sftp(['cd "%s"' % self.dir, 'ls -ln'])
        else:
            text = self._ssh('LC_ALL=C ls -ln -- %s' % shlex_quote(self.dir))
        out = []
        for line in text.splitlines():
            f = line.split(None, 8)
            if len(f) == 9 and f[0].startswith('-'):
                name = f[8].rsplit('/', 1)[-1]
                try:
                    out.append({'name': name, 'size': int(f[4]), 'mtime': 0})
                except ValueError:
                    pass
        return out

    def delete(self, name):
        name = rb_safe_name(name)
        if self.t['kind'] == 'sftp':
            self._sftp(['cd "%s"' % self.dir, 'rm "%s"' % name])
        else:
            self._ssh('rm -f -- %s' % shlex_quote('%s/%s' % (self.dir, name)))

    def close(self):
        if self.d:
            shutil.rmtree(self.d, ignore_errors=True)


class RbSmb(object):
    """SMB through smbclient: an authentication file of its own (never the
    password on a command line), SMB2 at least, SMB3 encryption when asked."""
    def __init__(self, t):
        self.t = t
        self.d = None

    def open(self):
        t = self.t
        if not shutil.which('smbclient'):
            raise RbError('smbclient is not on this machine (dnf install samba-client)')
        self.d = tempfile.mkdtemp(prefix='plexmon-smb-')
        auth = os.path.join(self.d, 'auth')
        fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write('username = %s\npassword = %s\n' % (t.get('user') or 'guest',
                                                       rb_secret(t, 'password')))
            if t.get('domain'):
                f.write('domain = %s\n' % t['domain'])
        self.args = ['smbclient', '//%s/%s' % (t['host'], t['share']), '-A', auth,
                     '-p', str(int(t['port'])), '--option=client min protocol=SMB2',
                     '-t', str(RB_TIMEOUT)]
        if t.get('smb_encrypt'):
            self.args += ['--client-protection=encrypt']
        self.dir = (t.get('path') or '').replace('/', '\\').strip('\\')
        cmds = []
        acc = ''
        for p in [x for x in self.dir.split('\\') if x]:
            acc = (acc + '\\' + p) if acc else p
            cmds.append('mkdir "%s"' % acc)
        self._run(cmds + ['cd "\\%s"' % self.dir, 'ls'], ignore=('NT_STATUS_OBJECT_NAME_COLLISION',))

    def _local_ip(self):
        """The address of this machine the SMB server is reached from."""
        t = self.t
        for fam, _t, _p, _c, addr in socket.getaddrinfo(t['host'], int(t['port']), 0,
                                                        socket.SOCK_DGRAM):
            s = socket.socket(fam, socket.SOCK_DGRAM)
            try:
                s.connect(addr[:2] if fam == socket.AF_INET else addr)
                return s.getsockname()[0], fam
            except OSError:
                continue
            finally:
                s.close()
        return '127.0.0.1', socket.AF_INET

    def _run(self, cmds, ignore=(), timeout=3600):
        got = subprocess.run(self.args + ['-c', '; '.join(cmds)], capture_output=True, text=True,
                             timeout=timeout, cwd=self.d, start_new_session=True)
        text = (got.stdout or '') + (got.stderr or '')
        # smbclient lists the machine's interfaces for NetBIOS broadcasts and gives
        # up when it cannot (a service sandbox without netlink, say): it is told the
        # one address it goes out from, as it asks (2.2.3).
        if 'Could not determine network interfaces' in text and \
                not any(a.startswith('--option=interfaces') for a in self.args):
            ip, fam = self._local_ip()
            self.args += ['--option=interfaces=%s/%d' % (ip, 32 if fam == socket.AF_INET
                                                         else 128),
                          '--option=bind interfaces only=no']
            return self._run(cmds, ignore, timeout)
        bad = [x for x in text.splitlines() if 'NT_STATUS_' in x and
               not any(i in x for i in ignore)]
        if got.returncode != 0 or bad:
            said = ' / '.join((bad or text.strip().splitlines() or ['no answer'])[-2:])[:300]
            if 'NT_STATUS_LOGON_FAILURE' in said:
                said = 'the server did not take the account and password (%s)' % said
            elif 'NT_STATUS_BAD_NETWORK_NAME' in said:
                said = 'there is no share %s on %s' % (self.t['share'], self.t['host'])
            elif 'protection' in said.lower() and 'unknown' in said.lower():
                said = ('this smbclient does not know --client-protection: switch "SMB3 '
                        'encryption required" off or install a newer samba-client')
            raise RbError('smbclient: %s' % said)
        return got.stdout or ''

    def _cd(self):
        return 'cd "\\%s"' % self.dir

    def put(self, local, name):
        name = rb_safe_name(name)
        self._run([self._cd(), 'put "%s" ".%s.part"' % (local, name), 'del "%s"' % name,
                   'rename ".%s.part" "%s"' % (name, name)], ignore=('NT_STATUS_NO_SUCH_FILE',
                                                                   'NT_STATUS_OBJECT_NAME_NOT_FOUND'))

    def get(self, name, local):
        self._run([self._cd(), 'get "%s" "%s"' % (rb_safe_name(name), local)])

    def list(self):
        out = []
        for line in self._run([self._cd(), 'ls']).splitlines():
            m = re.match(r'^\s\s(.+?)\s+([ADHNRS]*)\s+(\d+)\s+(\w{3} \w{3}\s+\d+ [\d:]+ \d{4})\s*$',
                         line)
            if m and 'D' not in m.group(2) and m.group(1) not in ('.', '..'):
                try:
                    mt = datetime.strptime(re.sub(r'\s+', ' ', m.group(4)),
                                           '%a %b %d %H:%M:%S %Y').timestamp()
                except ValueError:
                    mt = 0
                out.append({'name': m.group(1), 'size': int(m.group(3)), 'mtime': mt})
        return out

    def delete(self, name):
        self._run([self._cd(), 'del "%s"' % rb_safe_name(name)])

    def close(self):
        if self.d:
            shutil.rmtree(self.d, ignore_errors=True)


def rb_said(e):
    """An error in words, whatever library raised it."""
    if isinstance(e, EOFError):
        return 'the server closed the connection'
    if isinstance(e, socket.timeout):
        return 'the server did not answer in time'
    if isinstance(e, ssl.SSLCertVerificationError):
        return ('the server\'s certificate is not trusted: %s - check it against its authority, '
                'or pin it' % e.verify_message)
    if isinstance(e, ftplib.Error):
        return 'the FTP server said: %s' % str(e)[:300]
    return scrub_secret(str(e) or e.__class__.__name__)[:400]


def rb_transport(t):
    return {'folder': RbFolder, 'ftp': RbFtp, 'ftps': RbFtp, 'sftp': RbSsh, 'scp': RbSsh,
            'smb': RbSmb}[t['kind']](t)


def rb_try(t):
    """Signed in, a small file written, seen in the list, taken away again."""
    out = {'ok': False, 'steps': []}
    began = time.time()
    tr = rb_transport(t)
    try:
        tr.open()
        out['steps'].append('signed in, the folder is there')
        if getattr(tr, 'seen', None):
            out['server_cert'] = tr.seen
        name = 'plexmon-try-%s-%s.txt' % (rb_slug(node_name()), secrets.token_hex(4))
        with tempfile.NamedTemporaryFile('w', prefix='plexmon-try-', delete=False) as f:
            f.write('plexmon %s on %s tried this destination at %s - this file is taken away again\n'
                    % (__version__, node_name(), datetime.now().isoformat(timespec='seconds')))
            local = f.name
        try:
            tr.put(local, name)
        finally:
            os.unlink(local)
        out['steps'].append('a file written')
        names = [x['name'] for x in tr.list()]
        if name not in names:
            raise RbError('the file written is not in the list the server gives')
        out['steps'].append('it is in the list (%d file(s) there)' % len(names))
        tr.delete(name)
        out['steps'].append('taken away again')
        out['ok'] = True
    except Exception as e:                              # noqa: BLE001
        out['error'] = rb_said(e)
        if getattr(tr, 'seen', None):
            out['server_cert'] = tr.seen
    finally:
        tr.close()
    out['secs'] = round(time.time() - began, 1)
    return out


# ---- the backup itself -----------------------------------------------------------------
RB_ITER = 600000
RB_JOB = {'running': False, 'what': '', 'says': '', 'done': 0, 'whole': 0, 'error': '',
          'began': 0.0, 'ended': 0.0, 'result': None, 'by': ''}


def rb_password():
    pw = str(cfg('rbackup_password', '') or '')
    return (unseal_secret(pw) or '') if pw else ''


def rb_files_named():
    """What else makes this server what it is, beside the database."""
    out = []
    for p in ('/etc/plex-status/config.json', str(getattr(A, 'CONFIG_FILE', '')),
              '/etc/plexmon/config.json', '/etc/nginx/plexmon/tls.conf',
              '/etc/nginx/plexmon/acme.conf', '/etc/nginx/plexmon/php-guard.conf',
              str(getattr(A, 'NGINX_SITE', '/etc/nginx/conf.d/plex.conf'))):
        if p and os.path.isfile(p) and p not in out:
            out.append(p)
    try:
        cert, key = A.nginx_conf_cert_paths()
        for p in (str(cert), str(key)) + tuple(A.ec_paths(cert, key)):
            if os.path.isfile(p) and p not in out:
                out.append(p)
    except Exception:                                   # noqa: BLE001
        pass
    return out


def rb_restore_md(m):
    enc = m.get('encrypted')
    f = m['file']
    tar = f[:-4] if enc else f
    top = f.split('.tar.gz')[0]
    lines = [
        '# Putting plexmon back - %s' % m.get('node'),
        '',
        'Made %s by plexmon %s on %s (%s).' % (m.get('made_words'), m.get('plexmon'),
                                              m.get('node'), m.get('host')),
        '',
        'In it: plexmon\'s database (accounts, settings, certificates, events), the key that',
        'opens its secrets (secrets.key), the dashboard\'s and Settings\' configuration, the',
        'nginx TLS files%s.' % (', and a copy of Plex Media Server\'s database and its '
                               'Preferences.xml' if m.get('plex_db') else ''),
        '',
        '## 1. Open it',
        '',
    ]
    if enc:
        lines += ['```', 'openssl enc -d -aes-256-cbc -pbkdf2 -iter %d -md sha256 -in %s -out %s'
                  % (m.get('iter') or RB_ITER, f, tar), '```', '',
                  'It asks for the password of the backups (Settings → Backups).', '']
    lines += ['```', 'tar xzf %s' % tar, 'cd %s' % top, 'sha256sum -c SHA256SUMS', '```', '',
              '## 2. plexmon', '',
              'Install plexmon first (./install.sh from its package), then, as root:', '',
              '```', 'systemctl stop plexmon',
              'install -m 600 secrets.key /var/lib/plexmon/secrets.key',
              'gunzip -c plexmon-copy.db.gz > /var/lib/plexmon/plexmon.db',
              'chmod 600 /var/lib/plexmon/plexmon.db',
              'cp -a files/etc/plex-status/config.json /etc/plex-status/   # the dashboard',
              'systemctl start plexmon', '```', '',
              'The certificate and the TLS of nginx come back with the database: Settings → '
              'Certificates and TLS puts them in again (the files are in files/ too).', '']
    if m.get('plex_db'):
        lines += ['## 3. Plex Media Server\'s database', '',
                  '```', 'systemctl stop plexmediaserver',
                  'cd "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/'
                  'Plug-in Support/Databases"',
                  'mkdir -p before && mv com.plexapp.plugins.library* before/',
                  'tar xzf /path/to/%s/plex/plexdb.tar.gz' % top,
                  'chown plex:plex com.plexapp.plugins.library*.db',
                  'systemctl start plexmediaserver', '```', '',
                  'Or in Settings → The database → Plex Media Server → Put a copy back.', '']
    return '\n'.join(lines) + '\n'


def rb_build(store, work, progress=None):
    def say(n, t):
        if progress:
            progress(n, 6, t)
    stamp_ = datetime.now().strftime('%Y%m%d-%H%M%S')
    site = rb_slug(cfg('site_name', '') or 'plexmon')
    node = rb_slug(node_name())
    base = 'plexmon-backup-%s-%s-%s' % (site, node, stamp_)
    inner = os.path.join(work, base)
    os.makedirs(inner, mode=0o700)
    say(1, 'plexmon\'s database is copied')
    raw = os.path.join(work, 'copy.db')
    sqlite_copy(store.db.where(), raw, readonly=False)
    gz(raw, os.path.join(inner, 'plexmon-copy.db.gz'))
    os.unlink(raw)
    kp = A.sealing_key_path() if hasattr(A, 'sealing_key_path') else STATE_DIR / 'secrets.key'
    if not Path(str(kp)).is_file():
        raise RbError('this machine has no key that opens plexmon\'s secrets')
    shutil.copyfile(str(kp), os.path.join(inner, 'secrets.key'))
    for p in rb_files_named():
        dst = os.path.join(inner, 'files', p.lstrip('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(p, dst)
    with_plex = bool(int(cfg('rbackup_plex_db', 1) or 0))
    if with_plex and (PLEX_DB_DIR / PLEX_DB).is_file():
        say(2, 'Plex Media Server\'s database is copied (Plex goes on)')
        pd = os.path.join(work, 'plex')
        os.makedirs(pd)
        sqlite_copy(str(PLEX_DB_DIR / PLEX_DB), os.path.join(pd, PLEX_DB))
        if (PLEX_DB_DIR / PLEX_BLOBS).is_file():
            sqlite_copy(str(PLEX_DB_DIR / PLEX_BLOBS), os.path.join(pd, PLEX_BLOBS))
        os.makedirs(os.path.join(inner, 'plex'))
        with tarfile.open(os.path.join(inner, 'plex', 'plexdb.tar.gz'), 'w:gz') as tf:
            for n in (PLEX_DB, PLEX_BLOBS):
                if os.path.exists(os.path.join(pd, n)):
                    tf.add(os.path.join(pd, n), arcname=n)
        shutil.rmtree(pd, ignore_errors=True)
        prefs = PLEX_DATA / 'Preferences.xml'
        if prefs.is_file():
            shutil.copyfile(str(prefs), os.path.join(inner, 'plex', 'Preferences.xml'))
    else:
        with_plex = False
    say(3, 'counted and checksummed')
    pw = rb_password()
    m = {'plexmon': A.__version__, 'made': time.time(),
         'made_words': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'site': site,
         'node': node_name(), 'host': socket.getfqdn(), 'encrypted': bool(pw),
         'iter': RB_ITER if pw else 0, 'plex_db': with_plex,
         'file': base + ('.tar.gz.enc' if pw else '.tar.gz'), 'files': {}}
    sums = []
    for root, _d, files in os.walk(inner):
        for n in sorted(files):
            p = os.path.join(root, n)
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            rel = os.path.relpath(p, inner)
            m['files'][rel] = {'sha256': h.hexdigest(), 'bytes': os.path.getsize(p)}
            sums.append('%s  %s' % (h.hexdigest(), rel))
    with open(os.path.join(inner, 'SHA256SUMS'), 'w') as f:
        f.write('\n'.join(sums) + '\n')
    md = rb_restore_md(m)
    with open(os.path.join(inner, 'RESTORE.md'), 'w') as f:
        f.write(md)
    with open(os.path.join(inner, 'manifest.json'), 'w') as f:
        json.dump(m, f, indent=2, default=str)
    say(4, 'packed')
    tar = os.path.join(work, base + '.tar.gz')
    with tarfile.open(tar, 'w:gz', compresslevel=6) as tf:
        tf.add(inner, arcname=base)
    shutil.rmtree(inner, ignore_errors=True)
    out = tar
    if pw:
        say(5, 'locked with the password of the backups')
        out = tar + '.enc'
        _openssl(['enc', '-aes-256-cbc', '-pbkdf2', '-iter', str(RB_ITER), '-md', 'sha256',
                  '-salt', '-in', tar, '-out', out, '-pass', 'env:PLEXMON_RB_PASS'],
                 env={'PLEXMON_RB_PASS': pw}, what='locking it')
        os.unlink(tar)
    h = hashlib.sha256()
    with open(out, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    m['sha256'] = h.hexdigest()
    m['bytes'] = os.path.getsize(out)
    with open(os.path.join(work, m['file'] + '.sha256'), 'w') as f:
        f.write('%s  %s\n' % (m['sha256'], m['file']))
    with open(os.path.join(work, base + '-RESTORE.md'), 'w') as f:
        f.write(md)
    return out, m, md


def rb_companions(name):
    if not RB_NAME_RE.match(name):
        return []
    base = name.split('.tar.gz')[0]
    return [name + '.sha256', base + '-RESTORE.md']


def rb_retain(tr, keep, days, now=None):
    now = now or time.time()
    mine = rb_slug(node_name())
    files = tr.list()
    names = set(x['name'] for x in files)
    ours = []
    for x in files:
        mm = RB_NAME_RE.match(x['name'])
        if mm and mm.group('node') == mine:
            try:
                when = datetime.strptime(mm.group('stamp'), '%Y%m%d-%H%M%S').timestamp()
            except ValueError:
                continue
            ours.append((when, x['name']))
    ours.sort(reverse=True)
    gone = []
    for i, (when, name) in enumerate(ours):
        if i == 0:
            continue
        if (keep and i >= keep) or (days and now - when > days * 86400):
            for n in [name] + [c for c in rb_companions(name) if c in names]:
                try:
                    tr.delete(n)
                except Exception:                       # noqa: BLE001
                    pass
            gone.append(name)
    return gone


def rb_history(store):
    got = meta(store, 'rbackup:runs', []) or []
    return [x for x in got if isinstance(x, dict)]


def rb_history_add(store, run):
    with RB_LOCK:
        put_meta(store, 'rbackup:runs', ([run] + rb_history(store))[:60])


def rb_run(store, by, only=None, progress=None):
    targets = [t for t in rb_targets(store)
               if (only and t['id'] in only) or (not only and t.get('enabled'))]
    if not targets:
        raise RbError('no destination is switched on')
    if not rb_password():
        raise RbError('the backups have no password: set one (Settings → Backups → When, how '
                      'many, and the password) - unlocked, a copy would hand the key of every '
                      'secret to whoever can read the destination')
    work = tempfile.mkdtemp(prefix='plexmon-rbackup-', dir=str(STATE_DIR))
    os.chmod(work, 0o700)
    began = time.time()
    run = {'at': began, 'by': by, 'node': node_name(), 'targets': [], 'ok': False}
    try:
        path, m, _md = rb_build(store, work, progress)
        run.update(file=m['file'], bytes=m['bytes'], encrypted=m['encrypted'],
                   plex_db=m['plex_db'])
        sha = os.path.join(work, m['file'] + '.sha256')
        md = os.path.join(work, m['file'].split('.tar.gz')[0] + '-RESTORE.md')
        for i, t in enumerate(targets):
            if progress:
                progress(6, 6, 'sent to %s (%d of %d)' % (t['name'], i + 1, len(targets)))
            one = {'id': t['id'], 'name': t['name'], 'kind': t['kind'], 'ok': False}
            t0 = time.time()
            tr = rb_transport(t)
            try:
                tr.open()
                tr.put(path, m['file'])
                tr.put(sha, os.path.basename(sha))
                tr.put(md, os.path.basename(md))
                got = {x['name']: x['size'] for x in tr.list()}
                if got.get(m['file']) not in (None, m['bytes']):
                    raise RbError('the server holds %s bytes of it, not %s' % (
                        got.get(m['file']), m['bytes']))
                one['ok'] = True
                try:
                    one['taken_away'] = rb_retain(tr, int(cfg('rbackup_keep', 30) or 0),
                                                  int(cfg('rbackup_keep_days', 0) or 0))
                except Exception as e:                  # noqa: BLE001
                    one['retain_error'] = str(e)[:200]
            except Exception as e:                      # noqa: BLE001
                one['error'] = rb_said(e)
            finally:
                tr.close()
            one['secs'] = round(time.time() - t0, 1)
            run['targets'].append(one)
            rb_set_fields(store, t['id'], '', '', last={
                'at': time.time(), 'ok': one['ok'], 'error': one.get('error') or '',
                'file': m['file'], 'bytes': m['bytes']})
        run['ok'] = all(x['ok'] for x in run['targets'])
    except (RbError, OSError, ValueError, subprocess.SubprocessError, sqlite3.Error) as e:
        run['error'] = scrub_secret(str(e))[:400]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    run['secs'] = round(time.time() - began, 1)
    rb_history_add(store, run)
    good = [x['name'] for x in run['targets'] if x['ok']]
    bad = ['%s (%s)' % (x['name'], x.get('error')) for x in run['targets'] if not x['ok']]
    event(store, 'backups', 'a backup (%s) %s%s%s' % (
        human_bytes(run.get('bytes') or 0), ('went to ' + ', '.join(good)) if good
        else 'went nowhere', ('; not to ' + '; '.join(bad)) if bad else '',
        ('; ' + run['error']) if run.get('error') else ''), 'info' if run['ok'] else 'bad', who=by)
    return run


def rb_job_start(store, by, only=None):
    with RB_LOCK:
        if RB_JOB['running']:
            raise ValueError('a backup is being made already')
        RB_JOB.update(running=True, what='backup', says='starting', done=0, whole=6, error='',
                      began=time.time(), ended=0, result=None, by=by)

    def progress(done, whole, words):
        RB_JOB.update(done=done, whole=whole, says=words)

    def work():
        try:
            RB_JOB['result'] = rb_run(store, by, only, progress)
            if RB_JOB['result'].get('error'):
                RB_JOB['error'] = RB_JOB['result']['error']
            elif RB_JOB['result'].get('ok') and cfg('rbackup_verify', 'off') == 'after':
                RB_JOB.update(what='verify', says='the backup just sent is tried', done=0,
                              whole=4)
                sent = RB_JOB['result']
                v = rb_verify(store, by, sent['targets'][0]['id'], sent.get('file'), progress)
                sent['verified'] = v
                if not v['ok']:
                    RB_JOB['error'] = 'sent, but it is not good: ' + v.get('said', '')
        except Exception as e:                          # noqa: BLE001
            RB_JOB['error'] = scrub_secret(str(e))[:400]
            log('remote backup failed: %s' % e, 'error')
        finally:
            RB_JOB.update(running=False, ended=time.time())
    threading.Thread(target=work, name='plexmon-rbackup', daemon=True).start()
    return dict(RB_JOB)


def rb_remote_list(store, tid):
    t = rb_target(store, tid)
    if not t:
        raise ValueError('no such destination')
    tr = rb_transport(t)
    try:
        tr.open()
        files = [x for x in tr.list() if RB_NAME_RE.match(x['name'])]
    finally:
        tr.close()
    files.sort(key=lambda x: x['name'], reverse=True)
    return files


def rb_open(path, password, work):
    src = path
    with open(path, 'rb') as f:
        head = f.read(8)
    if head.startswith(b'Salted__'):
        if not password:
            raise RbError('the backup is locked: its password is needed')
        src = os.path.join(work, 'b.tar.gz')
        _openssl(['enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', str(RB_ITER), '-md', 'sha256',
                  '-in', path, '-out', src, '-pass', 'env:PLEXMON_RB_PASS'],
                 env={'PLEXMON_RB_PASS': password}, what='opening it (the password?)')
    out = os.path.join(work, 'x')
    os.makedirs(out)
    with tarfile.open(src, 'r:gz') as tf:
        for mbr in tf.getmembers():
            if mbr.name.startswith('/') or '..' in mbr.name.split('/') or not (
                    mbr.isfile() or mbr.isdir()):
                raise RbError('%s is not a backup of plexmon' % os.path.basename(path))
        tf.extractall(out)
    tops = os.listdir(out)
    if len(tops) != 1:
        raise RbError('%s is not a backup of plexmon' % os.path.basename(path))
    top = os.path.join(out, tops[0])
    with open(os.path.join(top, 'manifest.json')) as f:
        m = json.load(f)
    for rel, info in (m.get('files') or {}).items():
        h = hashlib.sha256()
        with open(os.path.join(top, rel), 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        if h.hexdigest() != info.get('sha256'):
            raise RbError('%s in it is not as it was made' % rel)
    return top, m


def rb_verify(store, by, tid=None, name=None, progress=None):
    """The newest backup (or the one named) fetched back, opened with the
    password, every file checked, the databases read through. Nothing is put
    back."""
    targets = [rb_target(store, tid)] if tid else [t for t in rb_targets(store) if
                                                   t.get('enabled')]
    targets = [t for t in targets if t]
    if not targets:
        raise ValueError('no destination to try')
    t = targets[0]
    work = tempfile.mkdtemp(prefix='plexmon-verify-', dir=str(STATE_DIR))
    out = {'at': time.time(), 'by': by, 'target': t['name'], 'ok': False, 'checks': []}
    try:
        if progress:
            progress(1, 4, 'fetched from %s' % t['name'])
        files = rb_remote_list(store, t['id'])
        pick = name or next((x['name'] for x in files if x['name'].endswith(('.enc', '.tar.gz'))
                             and not x['name'].endswith('.sha256')), None)
        if not pick:
            raise RbError('there is no backup at %s' % t['name'])
        out['file'] = pick
        local = os.path.join(work, pick)
        tr = rb_transport(t)
        try:
            tr.open()
            tr.get(pick, local)
        finally:
            tr.close()
        if progress:
            progress(2, 4, 'opened and every file checked')
        top, m = rb_open(local, rb_password(), work)
        out['checks'].append({'what': 'opened with the password, %d file(s) as they were made' %
                              len(m.get('files') or {}), 'ok': True})
        if progress:
            progress(3, 4, 'the databases read through')
        raw = os.path.join(work, 'own.db')
        with gzip.open(os.path.join(top, 'plexmon-copy.db.gz'), 'rb') as g, open(raw, 'wb') as f:
            shutil.copyfileobj(g, f)
        c = sqlite3.connect(raw)
        ok = c.execute('PRAGMA integrity_check').fetchone()[0]
        c.close()
        out['checks'].append({'what': 'plexmon\'s database: %s' % ok, 'ok': ok == 'ok'})
        if m.get('plex_db'):
            pdir = os.path.join(work, 'plex')
            os.makedirs(pdir)
            with tarfile.open(os.path.join(top, 'plex', 'plexdb.tar.gz'), 'r:gz') as tf:
                tf.extractall(pdir)
            ck = plex_check_file(os.path.join(pdir, PLEX_DB))
            out['checks'].append({'what': 'Plex Media Server\'s database (%s): %s' % (
                ck['how'], ', '.join(ck['said'][:3])), 'ok': ck['ok']})
        out['ok'] = all(x['ok'] for x in out['checks'])
    except Exception as e:                              # noqa: BLE001
        out['checks'].append({'what': scrub_secret(str(e))[:300], 'ok': False})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out['said'] = '; '.join(x['what'] for x in out['checks'] if not x['ok']) or 'all good'
    put_meta(store, 'rbackup:verified', ([out] + (meta(store, 'rbackup:verified', []) or []))[:20])
    event(store, 'backups', 'a backup tried (%s at %s): %s' % (out.get('file', '?'), t['name'],
                                                               'good' if out['ok'] else
                                                               out['said']),
          'info' if out['ok'] else 'bad', who=by)
    return out


def rb_verify_start(store, by, tid=None, name=None):
    with RB_LOCK:
        if RB_JOB['running']:
            raise ValueError('a backup is being made or tried already')
        RB_JOB.update(running=True, what='verify', says='starting', done=0, whole=4, error='',
                      began=time.time(), ended=0, result=None, by=by)

    def progress(done, whole, words):
        RB_JOB.update(done=done, whole=whole, says=words)

    def work():
        try:
            RB_JOB['result'] = rb_verify(store, by, tid, name, progress)
            if not RB_JOB['result']['ok']:
                RB_JOB['error'] = RB_JOB['result']['said']
        except Exception as e:                          # noqa: BLE001
            RB_JOB['error'] = str(e)[:400]
        finally:
            RB_JOB.update(running=False, ended=time.time())
    threading.Thread(target=work, name='plexmon-verify', daemon=True).start()
    return dict(RB_JOB)


def rb_view_all(store):
    return {'targets': [rb_view(t) for t in rb_targets(store)], 'runs': rb_history(store)[:30],
            'verified': (meta(store, 'rbackup:verified', []) or [])[:10], 'job': dict(RB_JOB),
            'password': bool(rb_password()), 'kinds': RB_KIND_WORDS, 'ports': RB_PORTS,
            'plex_db': bool(int(cfg('rbackup_plex_db', 1) or 0)), 'now': time.time(),
            'node': node_name()}


def rb_schedule(store):
    if not cfg('rbackup') or RB_JOB['running']:
        return
    day = due(cfg('rbackup_every', 'daily'), cfg('rbackup_day', 7), cfg('rbackup_at', '04:15'),
              store.get_meta('rbackup:day') or '')
    if not day:
        return
    store.set_meta('rbackup:day', day)
    if not any(t.get('enabled') for t in rb_targets(store)):
        return
    try:
        rb_job_start(store, 'plexmon (on a schedule)')
    except ValueError:
        pass
    if cfg('rbackup_verify', 'off') == 'weekly' and datetime.now().isoweekday() == 7:
        pass


# ---------------------------------------------------------------------------
# Once a minute from the daemon's loop
# ---------------------------------------------------------------------------
_TICK = {'at': 0.0}


def tick(store):
    STARTED['daemon'] = True
    if time.time() - _TICK['at'] < 50:
        return
    _TICK['at'] = time.time()
    # In maintenance (2.3.0) nothing on a schedule - only the follow-up of a restart.
    maint = bool((A.maintenance_now(store) or {}).get('on'))
    for fn in (power_tick,) + (() if maint else (power_schedule, osup_schedule, db_schedule,
                                                 rb_schedule)):
        try:
            fn(store)
        except Exception as e:                          # noqa: BLE001
            log('%s: %s' % (fn.__name__, e), 'warn')


# ---- a backup put back -----------------------------------------------------------------
def upload_dir():
    d = STATE_DIR / 'upload'
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    for n in os.listdir(str(d)):
        p = d / n
        try:
            if time.time() - p.stat().st_mtime > 6 * 3600:
                p.unlink()
        except OSError:
            pass
    return d


def rb_upload_look(path):
    """What an uploaded backup is - its manifest when it is not locked."""
    with open(path, 'rb') as f:
        head = f.read(8)
    if head.startswith(b'Salted__'):
        return {'locked': True, 'manifest': None}
    work = tempfile.mkdtemp(prefix='plexmon-look-', dir=str(STATE_DIR))
    try:
        _top, m = rb_open(path, '', work)
        return {'locked': False, 'manifest': m}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rb_restore(store, path, password, by):
    """plexmon put back from a backup: its database (when plexmon starts again,
    in a moment), the key of its secrets and the dashboard's configuration. A
    copy of what is here now is made first. Plex Media Server's database in
    it is put back apart - The database → Plex Media Server."""
    work = tempfile.mkdtemp(prefix='plexmon-restore-', dir=str(STATE_DIR))
    said = []
    try:
        says('the backup is opened and every file checked', 1, 5)
        top, m = rb_open(path, password or rb_password(), work)
        says('a copy of what is here now', 2, 5)
        safety = own_copy(store, 'before a backup was put back')
        kp = A.sealing_key_path() if hasattr(A, 'sealing_key_path') else STATE_DIR / 'secrets.key'
        says('the key of the secrets', 3, 5)
        if os.path.isfile(os.path.join(top, 'secrets.key')):
            keep = STATE_DIR / 'backup'
            keep.mkdir(parents=True, exist_ok=True)
            if Path(str(kp)).is_file():
                shutil.copy2(str(kp), str(keep / ('secrets.key.%s' % _stamp())))
            shutil.copyfile(os.path.join(top, 'secrets.key'), str(kp))
            os.chmod(str(kp), 0o600)
            said.append('the key of the secrets put back')
        cfgf = os.path.join(top, 'files', 'etc', 'plex-status', 'config.json')
        if os.path.isfile(cfgf) and os.path.isdir('/etc/plex-status'):
            shutil.copyfile(cfgf, '/etc/plex-status/config.json')
            said.append('the dashboard\'s configuration put back')
        says('the database, as plexmon starts again', 4, 5)
        pending = STATE_DIR / 'restore-pending.db'
        with gzip.open(os.path.join(top, 'plexmon-copy.db.gz'), 'rb') as g, \
                open(str(pending), 'wb') as f:
            shutil.copyfileobj(g, f, 1 << 20)
        os.chmod(str(pending), 0o600)
        said.append('the database (made %s by plexmon %s on %s)' % (
            m.get('made_words'), m.get('plexmon'), m.get('node')))
        if m.get('plex_db'):
            pd = plex_dir()
            name = 'plexdb-%s.tar.gz' % (str(m.get('made_words') or '').replace('-', '')
                                         .replace(':', '').replace(' ', '-') or _stamp())
            shutil.copyfile(os.path.join(top, 'plex', 'plexdb.tar.gz'), str(pd / name))
            said.append('its copy of Plex Media Server\'s database is in The database → Plex '
                        'Media Server (%s) - put it back there if you want it' % name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    event(store, 'backups', '%s put a backup back: %s' % (by, '; '.join(said)), 'warn', who=by)
    says('plexmon starts again', 5, 5)
    restart_plexmon()
    return {'said': said, 'safety': safety}
