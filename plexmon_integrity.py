#!/usr/bin/env python3
"""plexmon_integrity.py - Settings → Library integrity (2.11.0).

Finds where Plex Media Server's library no longer agrees with itself or with
the disks - the problems P1 to P8 of docs/library-integrity.md - and puts
right what can be put right safely.

How it keeps Plex Media Server safe
-----------------------------------
- Every finding is read from a COPY of the library database (SQLite's own
  online backup, read-only on Plex's file): Plex goes on, nothing of its file
  is written to look.
- A sleeping disk is never woken. The disks are looked at (P5, P6, P8 on the
  disk) only when asked, and only the folders of disks that are awake now;
  the others are named as left out. A folder that is not there - a disk not
  mounted - is never taken for files that were deleted.
- Nothing is written to Plex's database while Plex runs. P1 is the only
  write: Plex stopped (who watches is told first), a checked copy made before,
  Plex's files copied aside, the change in one transaction through Plex's own
  SQLite as Plex's own user with the finding itself as its guard,
  integrity_check after - and Plex's files put back if anything is not right.
  Plex is started again whatever happens.
- Never a file on a disk deleted; never DELETE /library/metadata/.../media.
- A Split or a Merge (P2) only when Plex has nothing running for a while
  (/activities), a checked copy of the database first, and every step looked
  at before the next: it stops at anything unexpected.
- The trash of a library emptied only when every folder of it is there and
  awake and no more than 5 % of it is missing or in the trash.
- One job at a time - together with the copies and restores of Settings →
  The database - followed on the pages, every step in Events.
"""

import fnmatch
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

A = None                # plexmon_admin
HX = None               # plexmon_host
PX = None               # plexmon_plex

VIDEO_EXT = {'mkv', 'mp4', 'm4v', 'avi', 'mov', 'wmv', 'ts', 'm2ts', 'mpg', 'mpeg', 'webm', 'flv',
             'ogv', 'vob', 'iso'}
PARTIAL_EXT = {'part', '!qb', 'crdownload', 'tmp', 'partial', '!ut'}
EXTRAS_DIRS = {'behind the scenes', 'deleted scenes', 'featurettes', 'interviews', 'scenes',
               'shorts', 'trailers', 'other', 'extras', 'specials-extras'}
SKIP_DIRS = {'@eadir', '#recycle', '$recycle.bin', 'plex versions', 'lost+found',
             '.grab', '#snapshot'}
SHOW_TYPE, MOVIE_TYPE = 2, 1
ROW_CAP = 400
MOUNT_SHARE = 0.05
MOUNT_MIN = 10          # a few files gone from a small folder are files gone, not a disk (2.11.2)


def too_much(gone, total):
    """More than 5 % of a folder - and at least ten files - missing or in the
    trash: taken for a disk not mounted, not for files deleted."""
    return bool(total) and gone >= MOUNT_MIN and gone > MOUNT_SHARE * total
SE_RE = re.compile(r'(?i)s(\d{1,3})[ ._-]*e(\d{1,4})(?:-?e(\d{1,4}))?')
DATE_RE = re.compile(r'\d{4}[.\-]\d{2}[.\-]\d{2}')
SEASON_DIR_RE = re.compile(r'(?i)^(season|series|s|staffel|serie|sezona|sezóna)[ ._-]*\d+$|^specials$')
LOG_TS = re.compile(r'^([A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2}:\d{2})(?:\.\d+)?')
LOG_MERGE = re.compile(r'We are merging ID (\d+) into ID (\d+)')
LOG_DESTROY = re.compile(r'Destroying metadata item (\d+)')
LOG_SPLITMERGE = re.compile(r'PUT /library/metadata/(\d+)/(split|merge)')
LOG_JOB = re.compile(r'LongRunningJobManager \[(\w+)\]: scheduling job for item (\d+)')
LOG_WRITEBACK = re.compile(r'Updating media item (\d+), size=\d+, metadata_item_id=(\d+)')

KINDS = {
    'P1': ('safe', 'A file in the database that Plex shows nowhere'),
    'P2': ('confirm', 'A file under another show'),
    'P3': ('info', 'Several shows on one match'),
    'P4': ('info', 'A merge or split while Plex was busy'),
    'P5': ('info', 'A file on the disk Plex has not taken in'),
    'P6': ('safe', 'In the database, but not on the disk'),
    'P7': ('safe', 'Seasons and shows left empty or without a parent'),
    'P8': ('info', 'Gaps in the numbering of episodes'),
}

_LOCK = threading.Lock()
JOB = {'running': False, 'id': '', 'kind': '', 'what': '', 'steps': [], 'step': '',
       'says': '', 'done': 0, 'whole': 0, 'error': '', 'result': None, 'began': 0.0,
       'ended': 0.0, 'by': '', 'stop': False, 'lines': []}


def bind(admin, host, plex):
    global A, HX, PX
    A, HX, PX = admin, host, plex
    try:
        _plex_back_after_crash()
    except Exception:                                   # noqa: BLE001
        pass


def _marker():
    return Path(str(A.STATE_DIR)) / 'integrity' / 'plex-stopped'


def _plex_back_after_crash():
    """plexmon stopped Plex for P1 and did not live to start it again (killed,
    the machine's memory): Plex is started as plexmon starts."""
    m = _marker()
    if not m.exists():
        return
    try:
        age = time.time() - m.stat().st_mtime
    except OSError:
        return

    def go():
        if age < 86400 and not HX.plex_running():
            ok, out = start_plex()
            log('Plex Media Server was left stopped by a P1 that did not finish - started again: '
                '%s' % ('ok' if ok else out), 'warn')
        try:
            m.unlink()
        except OSError:
            pass
    threading.Thread(target=go, name='plexmon-integrity-plex-back', daemon=True).start()


def log(words, level='info'):
    try:
        A.log('library integrity: %s' % words, level)
    except Exception:                                   # noqa: BLE001
        pass


def state_dir():
    d = Path(str(A.STATE_DIR)) / 'integrity'
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    return d


def _read_json(name, default):
    try:
        return json.loads((state_dir() / name).read_text())
    except (OSError, ValueError):
        return default


def _write_json(name, data):
    p = state_dir() / name
    tmp = p.with_suffix('.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str))
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(p))


def last_report():
    return _read_json('report.json', None)


def history():
    h = _read_json('history.json', [])
    return h if isinstance(h, list) else []


def remember(entry):
    h = history()
    h.insert(0, dict(entry, at=time.time()))
    _write_json('history.json', h[:40])


# ---------------------------------------------------------------------------
# The job: one at a time, steps and progress for the pages
# ---------------------------------------------------------------------------
def job_view():
    with _LOCK:
        j = {k: (list(v) if isinstance(v, list) else v) for k, v in JOB.items()}
    j['steps'] = [dict(s) for s in JOB['steps']]
    j['lines'] = j['lines'][-60:]
    j.pop('stop', None)
    return j


def job_start(kind, what, by, steps, fn):
    """The job runs in Settings → The database's slot too: no copy, no restore
    of Plex's database meanwhile, and none of this during one."""
    with _LOCK:
        if JOB['running']:
            raise ValueError('%s is under way - wait for it' % (JOB['what'] or 'something'))
        JOB.update(running=True, id=os.urandom(5).hex(), kind=kind, what=what,
                   steps=[{'key': k, 'title': t, 'state': 'wait'} for k, t in steps],
                   step='', says='starting', done=0, whole=0, error='', result=None,
                   began=time.time(), ended=0.0, by=by, stop=False, lines=[])

    def go():
        try:
            JOB['result'] = fn()
            for s in JOB['steps']:
                if s['state'] == 'run':
                    s['state'] = 'done'
        except Exception as e:                          # noqa: BLE001
            JOB['error'] = str(e)[:500] or e.__class__.__name__
            for s in JOB['steps']:
                if s['state'] == 'run':
                    s['state'] = 'failed'
            log('%s: %s' % (what, e), 'warn')
        finally:
            JOB.update(says='done' if not JOB['error'] else 'failed')
            return JOB.get('result')

    try:
        HX.db_job('library integrity: %s' % what, by, go)
    except ValueError:
        with _LOCK:
            JOB.update(running=False, ended=time.time())
        raise

    def watch():
        while HX.DB_JOB.get('running'):
            time.sleep(0.5)
        with _LOCK:
            JOB.update(running=False, ended=time.time())
    threading.Thread(target=watch, name='plexmon-integrity-watch', daemon=True).start()
    return JOB['id']


def phase(key, says=None, done=None, whole=None):
    for s in JOB['steps']:
        if s['key'] == key:
            s['state'] = 'run'
            JOB['step'] = s['title']
        elif s['state'] == 'run':
            s['state'] = 'done'
    if says is not None:
        JOB['says'] = says
        HX.DB_JOB['says'] = says
    if done is not None:
        JOB['done'] = done
    if whole is not None:
        JOB['whole'] = whole


def skip(key):
    for s in JOB['steps']:
        if s['key'] == key and s['state'] == 'wait':
            s['state'] = 'skip'


def says(words, done=None, whole=None):
    JOB['says'] = words
    HX.DB_JOB['says'] = words
    if done is not None:
        JOB['done'] = done
    if whole is not None:
        JOB['whole'] = whole


def line(words):
    with _LOCK:
        JOB['lines'].append({'at': time.time(), 'words': str(words)[:400]})
        del JOB['lines'][:-300]


def stop_asked():
    return bool(JOB.get('stop'))


def job_stop():
    if JOB['running']:
        JOB['stop'] = True
        return True
    return False


class Stopped(Exception):
    pass


def check_stop():
    if stop_asked():
        raise Stopped('stopped when asked')


# ---------------------------------------------------------------------------
# The disks: where each folder of a library is, and whether it sleeps
# ---------------------------------------------------------------------------
def _unescape(p):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), p)


def mounts():
    out = []
    try:
        with open('/proc/self/mountinfo') as f:
            for ln in f:
                parts = ln.split()
                if len(parts) < 10:
                    continue
                sep = parts.index('-')
                out.append({'mount': _unescape(parts[4]), 'devno': parts[2],
                            'fs': parts[sep + 1], 'src': parts[sep + 2]})
    except (OSError, ValueError):
        pass
    return out


def mount_of(path, mts=None):
    best = None
    for m in mts if mts is not None else mounts():
        mp = m['mount'].rstrip('/') or '/'
        if path == mp or path.startswith(mp.rstrip('/') + '/') or mp == '/':
            if best is None or len(mp) >= len(best['mount'].rstrip('/') or '/'):
                best = m
    return best


def _block_of(devno):
    """The whole disks under a device number: sdb for sdb1, the disks of an
    md or dm device."""
    try:
        p = os.path.realpath('/sys/dev/block/%s' % devno)
    except OSError:
        return []
    name = os.path.basename(p)
    if not os.path.isdir(p):
        return []
    if os.path.exists(os.path.join(p, 'partition')):
        return [os.path.basename(os.path.dirname(p))]
    slaves = os.path.join(p, 'slaves')
    if os.path.isdir(slaves) and os.listdir(slaves):
        out = []
        for s in os.listdir(slaves):
            sp = os.path.realpath(os.path.join(slaves, s))
            try:
                with open(os.path.join(sp, 'dev')) as f:
                    out += _block_of(f.read().strip())
            except OSError:
                pass
        return sorted(set(out))
    return [name]


def disk_power(monitor):
    try:
        got = (monitor() or {}).get('disk_state') if monitor else None
    except Exception:                                   # noqa: BLE001
        got = None
    out = {str(d.get('dev')): str(d.get('power') or '') for d in got or [] if isinstance(d, dict)}
    if os.environ.get('PLEXMON_TEST_POWER'):
        try:
            out.update(json.loads(os.environ['PLEXMON_TEST_POWER']))
        except ValueError:
            pass
    return out


def _asleep(v):
    v = str(v or '').lower()
    return 'standby' in v or 'sleep' in v


def _known(v):
    v = str(v or '').lower()
    return bool(v) and v not in ('unknown', '?', 'n/a')


def root_state(path, monitor, mts=None, power=None):
    """{'state': awake | asleep | unknown | missing, 'mount', 'disks', 'why'}"""
    power = power if power is not None else disk_power(monitor)
    m = mount_of(path, mts)
    disks = _block_of(m['devno']) if m else []
    out = {'mount': m['mount'] if m else '', 'disks': disks, 'fs': m['fs'] if m else ''}
    asleep = [d for d in disks if _asleep(power.get(d))]
    if os.environ.get('PLEXMON_TEST_ASLEEP') and any(
            path.startswith(x) for x in os.environ['PLEXMON_TEST_ASLEEP'].split(':') if x):
        asleep = asleep or ['test-disk']
    if asleep:
        return dict(out, state='asleep', why='%s sleeps - never woken by this' % ', '.join(asleep))
    known = [d for d in disks if _known(power.get(d))]
    if m and m['fs'] in ('nfs', 'nfs4', 'cifs', 'smb3', 'fuse.sshfs', 'fuse.rclone'):
        state = 'awake'
    elif disks and len(known) == len(disks):
        state = 'awake'
    elif m and m['mount'] == '/' and not disks:
        state = 'awake'
    else:
        return dict(out, state='unknown', why='plexmon cannot tell whether %s sleeps - left out' %
                    (', '.join(disks) or 'its disk'))
    # Only now, the disk awake: is the folder there?
    if not os.path.isdir(path):
        return dict(out, state='missing', why='the folder is not there - a disk not mounted?')
    return dict(out, state=state, why='')


# ---------------------------------------------------------------------------
# The snapshot: a copy of the library database, read-only
# ---------------------------------------------------------------------------
def snapshot(tmp):
    main = HX.PLEX_DB_DIR / HX.PLEX_DB
    if not main.is_file():
        raise ValueError('Plex Media Server\'s database is not at %s' % main)
    size = main.stat().st_size
    try:
        wal = (HX.PLEX_DB_DIR / (HX.PLEX_DB + '-wal')).stat().st_size
    except OSError:
        wal = 0
    free = shutil.disk_usage(tmp).free
    if free < (size + wal) * 1.3 + (64 << 20):
        raise ValueError('not room enough for a copy of the database where the copies are kept '
                         '(%s free, %s needed)' % (HX.human_bytes(free), HX.human_bytes(size + wal)))
    dst = os.path.join(tmp, 'library.db')
    HX.sqlite_copy(str(main), dst)
    return dst, size


def _open(path):
    c = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def _has(c, table, col=None):
    try:
        cols = [r[1] for r in c.execute('PRAGMA table_info("%s")' % table)]
    except sqlite3.Error:
        return False
    return bool(cols) and (col is None or col in cols)


def sections(c):
    secs = {}
    for r in _rows(c, 'SELECT id, name, section_type FROM library_sections'):
        secs[r['id']] = {'id': r['id'], 'name': r['name'], 'type': r['section_type'], 'roots': []}
    for r in _rows(c, 'SELECT id, library_section_id, root_path FROM section_locations'):
        s = secs.get(r['library_section_id'])
        if s:
            s['roots'].append({'id': r['id'], 'path': (r['root_path'] or '').rstrip('/') or '/'})
    return secs


def root_of(path, roots):
    best = None
    for r in roots:
        p = r['path']
        if path.startswith(p.rstrip('/') + '/'):
            if best is None or len(p) > len(best['path']):
                best = r
    return best


def show_dir(path, roots):
    r = root_of(path, roots)
    if not r:
        return None, None
    rest = path[len(r['path'].rstrip('/')) + 1:]
    if '/' not in rest:
        return r, None
    return r, r['path'].rstrip('/') + '/' + rest.split('/', 1)[0]


def index_ok(c):
    """A primary key that does not find what is there would look like P1 -
    the count by the index and the count by a full read (id+0) agree."""
    a = c.execute('SELECT count(*) FROM metadata_items').fetchone()[0]
    b = c.execute('SELECT count(*) FROM metadata_items WHERE id+0 > 0').fetchone()[0]
    a2 = c.execute('SELECT count(*) FROM media_items').fetchone()[0]
    b2 = c.execute('SELECT count(*) FROM media_items WHERE id+0 > 0').fetchone()[0]
    return a == b and a2 == b2, {'metadata_items': [a, b], 'media_items': [a2, b2]}


# ---- P1 -----------------------------------------------------------------------------
def detect_p1(c, secs):
    deleted = ' AND mi.deleted_at IS NULL' if _has(c, 'media_items', 'deleted_at') else ''
    a = _rows(c, 'SELECT mi.id AS media_item, mi.metadata_item_id AS missing_metadata, '
                 'mi.library_section_id AS section, mp.id AS part, mp.file AS file '
                 'FROM media_items mi LEFT JOIN media_parts mp ON mp.media_item_id = mi.id '
                 'WHERE mi.metadata_item_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM '
                 'metadata_items m WHERE m.id = mi.metadata_item_id)' + deleted +
              ' ORDER BY mp.file')
    b = _rows(c, 'SELECT mp.id AS part, mp.media_item_id AS media_item, mp.file AS file '
                 'FROM media_parts mp WHERE NOT EXISTS (SELECT 1 FROM media_items mi '
                 'WHERE mi.id = mp.media_item_id)')
    cc = c.execute('SELECT count(*) FROM media_streams ms WHERE NOT EXISTS (SELECT 1 FROM '
                   'media_items mi WHERE mi.id = ms.media_item_id)').fetchone()[0] \
        if _has(c, 'media_streams') else 0
    for r in a + b:
        s = secs.get(r.get('section'))
        roots = s['roots'] if s else [x for v in secs.values() for x in v['roots']]
        _, d = show_dir(r.get('file') or '', roots)
        r['dir'] = d
        r['show'] = os.path.basename(d) if d else ''
        if not s and r.get('file'):
            rt = root_of(r['file'], roots)
            for v in secs.values():
                if rt in v['roots']:
                    r['section'] = v['id']
    return {'items': a[:ROW_CAP], 'parts': b[:ROW_CAP], 'streams': cc,
            'count': len(a) + len(b), 'ids': sorted({r['media_item'] for r in a}),
            'part_ids': sorted({r['part'] for r in b})}


# ---- P2 and P3 ----------------------------------------------------------------------------
def episode_parts(c):
    d1 = ' AND mi.deleted_at IS NULL' if _has(c, 'media_items', 'deleted_at') else ''
    d2 = ' AND mp.deleted_at IS NULL' if _has(c, 'media_parts', 'deleted_at') else ''
    return _rows(c, 'SELECT show.id AS show_id, show.title AS show_title, show.guid AS guid, '
                    'show.library_section_id AS section, season."index" AS s, ep."index" AS e, '
                    'ep.id AS episode, mi.id AS media_item, mi.section_location_id AS loc, '
                    'mp.file AS file FROM metadata_items ep '
                    'JOIN metadata_items season ON season.id = ep.parent_id '
                    'JOIN metadata_items show ON show.id = season.parent_id '
                    'JOIN media_items mi ON mi.metadata_item_id = ep.id' + d1 +
                    ' JOIN media_parts mp ON mp.media_item_id = mi.id' + d2 +
                    ' WHERE ep.metadata_type = 4')


def detect_p3(c, secs):
    dl = ' AND deleted_at IS NULL' if _has(c, 'metadata_items', 'deleted_at') else ''
    got = _rows(c, "SELECT library_section_id AS section, guid, count(*) AS n, "
                   "group_concat(id, ',') AS ids FROM metadata_items WHERE metadata_type = 2 "
                   "AND guid IS NOT NULL AND guid != '' AND guid NOT LIKE 'local://%'" + dl +
                ' GROUP BY library_section_id, guid HAVING count(*) > 1 ORDER BY n DESC')
    return got


def detect_p2(parts, secs, p3):
    rows = []
    for r in parts:
        s = secs.get(r['section'])
        if not s or not r['file']:
            continue
        roots = s['roots']
        if r['loc']:
            own = [x for x in roots if x['id'] == r['loc']]
            if own and r['file'].startswith(own[0]['path'].rstrip('/') + '/'):
                roots = own
        _, d = show_dir(r['file'], roots)
        if d:
            rows.append(dict(r, dir=d))
    cnt = defaultdict(Counter)
    titles = {}
    guids = {}
    dirs_of = defaultdict(set)
    for r in rows:
        cnt[r['dir']][r['show_id']] += 1
        titles[r['show_id']] = r['show_title']
        guids[r['show_id']] = r['guid']
        dirs_of[r['show_id']].add(r['dir'])
    owner = {}
    tied = set()
    for d, cc in cnt.items():
        top = cc.most_common(2)
        owner[d] = top[0][0]
        if len(top) > 1 and top[0][1] == top[1][1]:
            tied.add(d)
    p3_shows = {int(i) for g in p3 for i in str(g['ids']).split(',') if i}
    cases = {}
    for r in rows:
        o = owner[r['dir']]
        if r['show_id'] == o:
            continue
        key = '%s:%s:%s' % (r['show_id'], o, r['dir'])
        cs = cases.get(key)
        if not cs:
            same = guids.get(o) == r['guid'] and bool(r['guid'])
            why = ''
            if r['dir'] in tied:
                why = 'the folder\'s files are split evenly between the shows - which one owns it ' \
                      'is not clear'
            elif not same:
                why = 'the two shows are not on one match - put it right in Plex (Fix Match)'
            cs = cases[key] = {
                'key': key, 'section': r['section'], 'guid': r['guid'], 'dir': r['dir'],
                'attached': {'id': r['show_id'], 'title': titles[r['show_id']],
                             'dirs': sorted(dirs_of[r['show_id']])},
                'owner': {'id': o, 'title': titles.get(o, ''), 'dirs': sorted(dirs_of[o])},
                'files': [], 'fixable': not why and r['show_id'] in p3_shows, 'why': why}
        cs['files'].append({'media_item': r['media_item'], 'file': r['file'], 's': r['s'],
                            'e': r['e'], 'episode': r['episode']})
    out = sorted(cases.values(), key=lambda x: (x['attached']['title'] or '', x['dir']))
    for cs in out:
        cs['files'].sort(key=lambda f: (f['s'] or 0, f['e'] or 0))
    return out, dirs_of, titles


# ---- P7 and P8 ----------------------------------------------------------------------------
def detect_p7(c):
    dl = ' AND s.deleted_at IS NULL' if _has(c, 'metadata_items', 'deleted_at') else ''
    dl2 = ' AND ep.deleted_at IS NULL' if dl else ''
    dmi = ' AND mi.deleted_at IS NULL' if _has(c, 'media_items', 'deleted_at') else ''
    orphans = _rows(c, 'SELECT c.id, c.title, c.metadata_type AS type, c.parent_id AS parent, '
                       'c.library_section_id AS section FROM metadata_items c WHERE c.parent_id '
                       'IS NOT NULL AND c.metadata_type IN (3,4) AND NOT EXISTS (SELECT 1 FROM '
                       'metadata_items p WHERE p.id = c.parent_id)')
    empty = _rows(c, 'SELECT s.id, s.title, s.metadata_type AS type, s.library_section_id AS '
                     'section, s.parent_id AS parent FROM metadata_items s WHERE s.metadata_type '
                     'IN (2,3)' + dl + ' AND NOT EXISTS (SELECT 1 FROM metadata_items c WHERE '
                     'c.parent_id = s.id)')
    nomedia = _rows(c, 'SELECT ep.id, ep.title, ep.parent_id AS parent, ep.library_section_id '
                       'AS section FROM metadata_items ep WHERE ep.metadata_type = 4' + dl2 +
                    ' AND NOT EXISTS (SELECT 1 FROM media_items mi WHERE mi.metadata_item_id = '
                    'ep.id' + dmi + ')')
    names = {}
    ids = {r['parent'] for r in empty + nomedia if r.get('parent')}
    if ids:
        q = ','.join(str(int(i)) for i in list(ids)[:2000])
        for r in _rows(c, 'SELECT s.id, s.title, s."index" AS n, p.title AS show FROM '
                          'metadata_items s LEFT JOIN metadata_items p ON p.id = s.parent_id '
                          'WHERE s.id IN (%s)' % q):
            names[r['id']] = r
    for r in empty + nomedia:
        p = names.get(r.get('parent')) or {}
        r['show'] = p.get('show') or p.get('title') or ''
        r['season'] = p.get('n') if p.get('show') else None
    return {'orphans': orphans[:ROW_CAP], 'empty': empty[:ROW_CAP], 'no_media': nomedia[:ROW_CAP],
            'count': len(orphans) + len(empty) + len(nomedia),
            'counts': {'orphans': len(orphans), 'empty': len(empty), 'no_media': len(nomedia)}}


def detect_p8(c, parts):
    dl = ' AND ep.deleted_at IS NULL' if _has(c, 'metadata_items', 'deleted_at') else ''
    eps = _rows(c, 'SELECT season.id AS season, show.id AS show_id, show.title AS title, '
                   'show.library_section_id AS section, season."index" AS s, ep."index" AS e '
                   'FROM metadata_items ep JOIN metadata_items season ON season.id = ep.parent_id '
                   'JOIN metadata_items show ON show.id = season.parent_id WHERE '
                   'ep.metadata_type = 4 AND ep."index" > 0' + dl)
    by = defaultdict(set)
    meta = {}
    for r in eps:
        if (r['s'] or 0) <= 0:
            continue
        by[r['season']].add(r['e'])
        meta[r['season']] = r
    dirs = defaultdict(set)
    for p in parts:
        if p.get('dir'):
            dirs[p['show_id']].add(p['dir'])
    out = []
    for sid, have in by.items():
        top = max(have)
        if top > len(have) and top - len(have) <= 400:
            m = meta[sid]
            missing = [n for n in range(1, top + 1) if n not in have]
            out.append({'show_id': m['show_id'], 'title': m['title'], 'section': m['section'],
                        's': m['s'], 'max': top, 'have': len(have), 'missing': missing[:60],
                        'n_missing': len(missing), 'dirs': sorted(dirs.get(m['show_id']) or []),
                        'found': {}})
    out.sort(key=lambda x: (-x['n_missing'], x['title'] or ''))
    return out


# ---- P4: the logs --------------------------------------------------------------------------
def log_files():
    d = HX.PLEX_DATA / 'Logs'
    out = []
    try:
        for n in os.listdir(str(d)):
            if n.startswith('Plex Media Server') and n.endswith('.log'):
                out.append(d / n)
    except OSError:
        pass
    return out


def _ts(text):
    m = LOG_TS.match(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%b %d, %Y %H:%M:%S').timestamp()
    except ValueError:
        return None


def detect_p4(orphan_items):
    destroyed, writebacks, ops, jobs = {}, [], [], []
    files = log_files()
    read = 0
    for p in files:
        try:
            with open(str(p), 'r', encoding='utf-8', errors='replace') as f:
                for ln in f:
                    if 'metadata' not in ln and 'LongRunningJobManager' not in ln and \
                            'merging' not in ln:
                        continue
                    t = _ts(ln)
                    if t is None:
                        continue
                    m = LOG_DESTROY.search(ln)
                    if m:
                        destroyed.setdefault(int(m.group(1)), t)
                        continue
                    m = LOG_WRITEBACK.search(ln)
                    if m:
                        writebacks.append((t, int(m.group(1)), int(m.group(2))))
                        continue
                    m = LOG_SPLITMERGE.search(ln)
                    if m:
                        ops.append((t, int(m.group(1)), m.group(2)))
                        continue
                    m = LOG_JOB.search(ln)
                    if m:
                        jobs.append((t, m.group(1), int(m.group(2))))
            read += 1
        except OSError:
            continue
    races = []
    seen = set()
    for t, mi, md in sorted(writebacks):
        dt = destroyed.get(md)
        if dt is not None and t > dt and (mi, md) not in seen:
            seen.add((mi, md))
            races.append({'at': t, 'media_item': mi, 'metadata': md, 'destroyed_at': dt,
                          'still_orphan': mi in orphan_items})
    risky = []
    jobs.sort()
    last = {}
    for t, item, what in sorted(ops):
        # One request is written more than once (Plex logs it as it comes and as it ends).
        if t - last.get((item, what), -1e9) < 30:
            continue
        last[(item, what)] = t
        near = [j for j in jobs if t - 600 <= j[0] <= t]
        if near:
            risky.append({'at': t, 'item': item, 'what': what,
                          'jobs': sorted({j[1] for j in near})[:6], 'n_jobs': len(near)})
    return {'races': races[-ROW_CAP:], 'risky': risky[-ROW_CAP:], 'logs': read,
            'count': len(races), 'ops': len(ops)}


# ---- P5, P6, P8 on the disks ----------------------------------------------------------------
def _plex_user():
    try:
        st = HX.PLEX_DB_DIR.stat()
    except OSError:
        return None
    try:
        name = pwd.getpwuid(st.st_uid).pw_name
        groups = set(os.getgrouplist(name, st.st_gid))
    except (KeyError, OSError):
        groups = {st.st_gid}
    return {'uid': st.st_uid, 'gid': st.st_gid, 'groups': groups}


def _may(st, who, bit):
    """Whether Plex's user may read (bit 4) or go into (bit 1) - by the mode
    bits alone (ACLs and SELinux are not looked at)."""
    if who is None or who['uid'] == 0:
        return True
    if st.st_uid == who['uid']:
        return bool(st.st_mode >> 6 & bit)
    if st.st_gid in who['groups']:
        return bool(st.st_mode >> 3 & bit)
    return bool(st.st_mode & bit)


def _reachable(path, root, who):
    d = os.path.dirname(path)
    stop = os.path.dirname(root.rstrip('/'))
    while d and d != stop and d != '/':
        try:
            if not _may(os.stat(d), who, 1):
                return d
        except OSError:
            return d
        d = os.path.dirname(d)
    return ''


def _plexignore(dirpath, cache):
    if dirpath in cache:
        return cache[dirpath]
    pats = []
    try:
        with open(os.path.join(dirpath, '.plexignore'), encoding='utf-8', errors='replace') as f:
            pats = [x.strip() for x in f if x.strip() and not x.strip().startswith('#')]
    except OSError:
        pass
    cache[dirpath] = pats
    return pats


def _ignored(full, root, ign_dirs, cache):
    for d in ign_dirs:
        pats = _plexignore(d, cache)
        rel = os.path.relpath(full, d)
        for p in pats:
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(full), p) or \
                    fnmatch.fnmatch(rel, p.rstrip('/') + '/*'):
                return True
    return False


def walk_root(root, known, is_show, who, monitor, on_file=None):
    """The video files of a folder Plex does not have: (missing, ignored)."""
    missing, ignored = [], Counter()
    cache = {}
    has_ign = set()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        check_stop()
        if '.plexignore' in filenames:
            has_ign.add(dirpath)
        keep = []
        for dn in dirnames:
            low = dn.lower()
            if dn.startswith('.') or low in SKIP_DIRS:
                ignored['hidden or system folder'] += 1
                continue
            if low in EXTRAS_DIRS:
                ignored['extras (not episodes)'] += 1
                continue
            keep.append(dn)
        dirnames[:] = sorted(keep)
        ign_dirs = [d for d in has_ign if dirpath == d or dirpath.startswith(d.rstrip('/') + '/')]
        for fn in filenames:
            count += 1
            if fn.startswith('.'):
                continue
            ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
            partial = ext in PARTIAL_EXT and '.' in fn[:-len(ext) - 1] and \
                fn[:-len(ext) - 1].rsplit('.', 1)[-1].lower() in VIDEO_EXT
            if ext not in VIDEO_EXT and not partial:
                continue
            full = os.path.join(dirpath, fn)
            if 'sample' in fn.lower():
                ignored['a sample'] += 1
                continue
            if ign_dirs and _ignored(full, root, ign_dirs, cache):
                ignored['.plexignore'] += 1
                continue
            if full in known:
                continue
            missing.append(classify(full, root, is_show, who, partial))
            if on_file:
                on_file(len(missing))
    return missing, ignored, count


def classify(full, root, is_show, who, partial=False):
    out = {'file': full, 'reason': 'unseen', 'advice': 'a scan of its folder'}
    try:
        st = os.stat(full)
    except OSError as e:
        return dict(out, reason='unreadable', advice=str(e)[:120])
    out['bytes'] = st.st_size
    if partial or st.st_size == 0 or time.time() - st.st_mtime < 300:
        return dict(out, reason='incomplete', advice='still being written or downloaded - wait')
    if not _may(st, who, 4):
        return dict(out, reason='permissions', advice='Plex\'s user may not read it: chown '
                    'plex:plex the file and chmod 644')
    blocked = _reachable(full, root, who)
    if blocked:
        return dict(out, reason='permissions', advice='Plex\'s user may not go into %s: chmod '
                    '755 the folder' % blocked)
    rel = os.path.relpath(full, root).split('/')
    if is_show:
        name = rel[-1]
        if not SE_RE.search(name) and not DATE_RE.search(name):
            return dict(out, reason='naming', advice='no S01E05 (or a date) in the name - name '
                        'it "Show (Year) - S01E05.ext"')
        if len(rel) < 2:
            return dict(out, reason='placement', advice='straight in the library folder - put it '
                        'into a folder of its show')
        if len(rel) > 3:
            return dict(out, reason='placement', advice='deeper than Show/Season XX/ - Plex may '
                        'not look there')
    return out


def ffprobe_bad(path):
    exe = shutil.which('ffprobe')
    if not exe:
        return None
    try:
        got = subprocess.run([exe, '-v', 'error', '-show_entries', 'format=duration', '-of',
                              'csv=p=0', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    err = got.stderr.decode('utf-8', 'replace').strip()
    if got.returncode != 0 or (err and 'Invalid data' in err):
        return err.splitlines()[0][:160] if err else 'ffprobe could not read it'
    return ''


# ---------------------------------------------------------------------------
# The look: everything, from a copy
# ---------------------------------------------------------------------------
SCAN_STEPS = [('snapshot', 'A copy of the database'), ('db', 'The database read'),
              ('logs', 'Plex\'s logs read'), ('wake', 'The disks woken'), ('disks', 'The disks'),
              ('report', 'The report')]


def wake_disks(wake, monitor, roots):
    """Asked for (2.11.1; 2.11.2 waits for real): every data disk spun up first -
    the dashboard's own Wake and an O_DIRECT read of each disk's probe file,
    which returns only once its platters turn. Disks behind one USB bridge
    spin up one after another - a couple of minutes for a set of them."""
    says('the disks are woken - each is waited for until it turns (a set of disks can take a '
         'couple of minutes)')
    t0 = time.time()

    def told(n, total, name):
        says('the disks are woken: %d of %d turn (%s), %d s' % (n, total, name,
                                                             round(time.time() - t0)))
        line('%s turns (%d s)' % (name, round(time.time() - t0)))
    try:
        got = wake(progress=told)
    except TypeError:
        got = wake()
    except Exception as e:                              # noqa: BLE001
        line('the disks could not be woken: %s' % e)
        return
    got = got if isinstance(got, dict) else {}
    if got.get('slow'):
        line('not answering after %d s: %s - read all the same (that wakes it)' % (
            got.get('took') or 0, ', '.join(got['slow'])))
    else:
        line('every disk turns: %s of them in %s s' % (got.get('disks', '?'), got.get('took', '?')))
    # And plexmon's own disk monitor told it too - a few seconds at most now.
    t1 = time.time()
    while time.time() - t1 < 30:
        check_stop()
        power = disk_power(monitor)
        if not any(root_state(r, monitor, None, power)['state'] == 'asleep' for r in roots):
            return
        time.sleep(2)


def scan(store, by, disks=False, monitor=None, wake=None):
    t0 = time.time()
    rep = {'at': t0, 'by': by, 'disks_looked': bool(disks), 'woken': bool(wake),
           'version': A.__version__}
    with tempfile.TemporaryDirectory(dir=str(HX.plex_dir()), prefix='integrity-') as tmp:
        phase('snapshot', 'the library database is copied (Plex goes on)', 0, 6)
        path, size = snapshot(tmp)
        rep['db_bytes'] = size
        check_stop()
        phase('db', 'the database is read', 1, 6)
        c = _open(path)
        try:
            secs = sections(c)
            ok, counts = index_ok(c)
            rep['index_ok'] = ok
            rep['index'] = counts
            says('P1 - files Plex shows nowhere', 1, 6)
            p1 = detect_p1(c, secs)
            says('P2, P3 - files under another show', 2, 6)
            parts = episode_parts(c)
            p3 = detect_p3(c, secs)
            p2, dirs_of, titles = detect_p2(parts, secs, p3)
            says('P7, P8 - empty items, gaps in the numbering', 2, 6)
            p7 = detect_p7(c)
            for r in parts:
                s = secs.get(r['section'])
                r['dir'] = show_dir(r['file'] or '', s['roots'])[1] if s else None
            p8 = detect_p8(c, parts)
            all_files = defaultdict(set)
            for r in _rows(c, 'SELECT mp.file AS file, mi.library_section_id AS section FROM '
                              'media_parts mp JOIN media_items mi ON mi.id = mp.media_item_id' +
                           (' WHERE mi.deleted_at IS NULL' if _has(c, 'media_items', 'deleted_at')
                            else '')):
                if r['file']:
                    all_files[r['section']].add(r['file'])
            trash = Counter()
            if _has(c, 'media_parts', 'deleted_at'):
                for r in _rows(c, 'SELECT mp.file AS file, mi.library_section_id AS section FROM '
                                  'media_parts mp JOIN media_items mi ON mi.id = mp.media_item_id '
                                  'WHERE mp.deleted_at IS NOT NULL OR mi.deleted_at IS NOT NULL'):
                    s = secs.get(r['section'])
                    rt = root_of(r['file'] or '', s['roots']) if s else None
                    if rt:
                        trash[rt['path']] += 1
        finally:
            c.close()
    check_stop()
    p3_groups = []
    for g in p3:
        ids = [int(i) for i in str(g['ids']).split(',') if i]
        p3_groups.append({'section': g['section'], 'guid': g['guid'],
                          'shows': [{'id': i, 'title': titles.get(i, ''),
                                     'dirs': sorted(dirs_of.get(i) or [])} for i in ids]})
    for g in p3_groups:
        if any(not s['title'] for s in g['shows']):
            pass
    phase('logs', 'Plex\'s logs are read', 3, 6)
    p4 = detect_p4(set(p1['ids']))
    check_stop()
    if wake:
        phase('wake', 'the disks are woken', 4, 6)
        wake_disks(wake, monitor, [r['path'] for s in secs.values() for r in s['roots']])
    else:
        skip('wake')
    phase('disks', 'the disks', 4, 6)
    mts = mounts()
    power = disk_power(monitor)
    who = _plex_user()
    sec_out = []
    p5 = {'missing': [], 'ignored': Counter(), 'roots': 0, 'files': 0, 'count': 0,
          'reasons': Counter()}
    p6 = {'missing': [], 'count': 0, 'roots': []}
    for sid in sorted(secs):
        s = secs[sid]
        so = {'id': sid, 'name': s['name'], 'type': s['type'], 'roots': []}
        files = all_files.get(sid) or set()
        for r in s['roots']:
            st = root_state(r['path'], monitor, mts, power)
            if wake and st['state'] in ('asleep', 'unknown'):
                # Woken when asked: read whatever its disk says now.
                st = dict(st, state='awake' if os.path.isdir(r['path']) else 'missing', why='' if
                          os.path.isdir(r['path']) else 'the folder is not there - a disk not '
                          'mounted?')
            mine = [f for f in files if f.startswith(r['path'].rstrip('/') + '/')]
            ro = dict(st, path=r['path'], id=r['id'], files=len(mine), trash=trash.get(r['path'], 0),
                      looked=False, missing=0)
            if st['state'] == 'missing' and mine:
                ro['mount_problem'] = True
                ro['missing'] = len(mine)
            if disks and st['state'] == 'awake':
                # Again, just before: a disk that fell asleep meanwhile stays asleep
                # (unless waking was asked for).
                st2 = root_state(r['path'], monitor, None, disk_power(monitor))
                if st2['state'] != 'awake' and not (wake and st2['state'] in ('asleep', 'unknown')):
                    ro.update(st2)
                else:
                    says('P6 - %s: is every file there?' % r['path'], 4, 6)
                    gone = [f for f in mine if not os.path.exists(f)]
                    check_stop()
                    ro['missing'] = len(gone)
                    ro['looked'] = True
                    share = len(gone) / max(1, len(mine))
                    try:
                        empty = not any(True for _ in os.scandir(r['path']))
                    except OSError:
                        empty = True
                    ro['mount_problem'] = bool(mine) and (too_much(len(gone), len(mine)) or
                                                          (empty and share >= 1))
                    if gone and not ro['mount_problem']:
                        for f in gone[:ROW_CAP]:
                            _, d = show_dir(f, s['roots'])
                            p6['missing'].append({'file': f, 'section': sid, 'root': r['path'],
                                                  'dir': d or os.path.dirname(f)})
                        p6['count'] += len(gone)
                    says('P5 - %s: what Plex has not taken in' % r['path'], 4, 6)
                    miss, ign, n = walk_root(r['path'], files, s['type'] == SHOW_TYPE, who, monitor)
                    p5['roots'] += 1
                    p5['files'] += n
                    p5['ignored'].update(ign)
                    probed = 0
                    for m in miss:
                        m['section'] = sid
                        m['root'] = r['path']
                        _, d = show_dir(m['file'], s['roots'])
                        m['dir'] = d or os.path.dirname(m['file'])
                        if m['reason'] == 'unseen' and probed < 15:
                            probed += 1
                            bad = ffprobe_bad(m['file'])
                            if bad:
                                m.update(reason='damaged', advice='the file cannot be read (%s) '
                                         '- download it again' % bad)
                        p5['reasons'][m['reason']] += 1
                    p5['count'] += len(miss)
                    p5['missing'] += miss[:max(0, ROW_CAP - len(p5['missing']))]
                    # P8: a missing number that is on the disk after all.
                    for g in p8:
                        if g['section'] != sid:
                            continue
                        for d in g['dirs']:
                            if not d.startswith(r['path'].rstrip('/') + '/'):
                                continue
                            for n2 in g['missing']:
                                hit = [m['file'] for m in miss if m['file'].startswith(d + '/')
                                       and _se_is(m['file'], g['s'], n2)]
                                if hit:
                                    g['found'][str(n2)] = {'file': hit[0], 'why': 'P5'}
            so['roots'].append(ro)
        sec_out.append(so)
    p6['roots'] = [dict(r, section=s['id']) for s in sec_out for r in s['roots']
                   if r.get('mount_problem')]
    for g in p8:
        for n2 in g['missing']:
            for f in p1['items']:
                if f.get('dir') in g['dirs'] and _se_is(f.get('file') or '', g['s'], n2):
                    g['found'].setdefault(str(n2), {'file': f['file'], 'why': 'P1'})
            for cs in p2:
                if cs['dir'] in g['dirs'] or cs['owner']['id'] == g['show_id']:
                    for f in cs['files']:
                        if f['s'] == g['s'] and f['e'] == n2 and cs['owner']['id'] == g['show_id']:
                            g['found'].setdefault(str(n2), {'file': f['file'], 'why': 'P2'})
    phase('report', 'the report', 5, 6)
    p5['ignored'] = dict(p5['ignored'])
    p5['reasons'] = dict(p5['reasons'])
    rep.update(sections=sec_out, took=round(time.time() - t0, 1), problems={
        'P1': p1, 'P2': {'cases': p2[:ROW_CAP], 'count': sum(len(x['files']) for x in p2)},
        'P3': {'groups': p3_groups[:ROW_CAP], 'count': len(p3_groups)}, 'P4': p4,
        'P5': p5, 'P6': p6, 'P7': p7,
        'P8': {'seasons': p8[:ROW_CAP], 'count': len(p8),
               'explained': sum(len(g['found']) for g in p8)}})
    rep['summary'] = summary(rep)
    _write_json('report.json', rep)
    s = rep['summary']
    HX.event(store, 'plex', '%s: the library looked through - %s' % (
        by, s['words']), 'warn' if s['safe'] + s['confirm'] else 'info', who=by)
    says('done', 6, 6)
    return {'summary': s}


def _se_is(path, s, e):
    for m in SE_RE.finditer(os.path.basename(path)):
        try:
            a, b = int(m.group(1)), int(m.group(2))
            c = int(m.group(3)) if m.group(3) else b
        except ValueError:
            continue
        if a == s and b <= e <= c:
            return True
    return False


def summary(rep):
    p = rep['problems']
    n = {'P1': p['P1']['count'], 'P2': p['P2']['count'], 'P3': p['P3']['count'],
         'P4': p['P4']['count'], 'P5': p['P5']['count'], 'P6': p['P6']['count'] +
         len(p['P6']['roots']), 'P7': p['P7']['count'], 'P8': p['P8']['count']}
    safe = n['P1'] + p['P6']['count'] + p['P7']['counts']['empty'] + p['P7']['counts']['no_media']
    confirm = n['P2']
    info = n['P3'] + n['P4'] + n['P5'] + n['P8'] + p['P7']['counts']['orphans'] + \
        len(p['P6']['roots'])
    left = [r for s in rep['sections'] for r in s['roots'] if r['state'] != 'awake' or
            (rep['disks_looked'] and not r.get('looked'))]
    if not rep.get('index_ok', True):
        words = 'an index of Plex\'s database does not agree with its table - nothing to put ' \
                'right here; Plex SQLite REINDEX'
    elif not safe + confirm + info:
        words = 'everything agrees'
    else:
        words = ', '.join('%s: %d' % (k, v) for k, v in n.items() if v)
    return {'counts': n, 'safe': safe, 'confirm': confirm, 'info': info, 'words': words,
            'left_out': len(left)}


# ---------------------------------------------------------------------------
# Plex: idle, its answers
# ---------------------------------------------------------------------------
def busy_now():
    return [a for a in PX.activities() if a.get('type')]


def wait_idle(timeout=3600, quiet=30, why=''):
    t0, calm = time.time(), None
    while time.time() - t0 < timeout:
        check_stop()
        acts = busy_now()
        if not acts:
            calm = calm or time.time()
            left = quiet - (time.time() - calm)
            if left <= 0:
                return True
            says('%swaiting until Plex has nothing running (%d s more)' % (
                (why + ' - ') if why else '', left + 1))
        else:
            calm = None
            a = acts[0]
            says('%swaiting for Plex: %s%s' % ((why + ' - ') if why else '', a.get('title') or
                                             a.get('type'), (' %d %%' % int(a['progress']))
                                             if isinstance(a.get('progress'), (int, float)) else ''))
        time.sleep(2 if os.environ.get('PLEXMON_TEST') else 5)
    return False


def plex_answers(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            PX.get('/identity', timeout=10)
            return True
        except Exception:                               # noqa: BLE001
            time.sleep(3)
    return False


def existing_dir(d, roots=()):
    """The folder itself, or - gone - the nearest folder above it that is there,
    never above its library's folder."""
    stop = max((r for r in roots if d.startswith(r.rstrip('/') + '/')), key=len, default='')
    while d and not os.path.isdir(d):
        up = os.path.dirname(d)
        if up == d or (stop and len(up) < len(stop)):
            return None
        d = up
    if stop and not os.path.isdir(stop):
        return None
    return d


def refresh_dir(section, d):
    st, text = PX.raw('GET', '/library/sections/%s/refresh' % section, {'path': d})
    if st >= 400:
        raise ValueError('Plex said %s to a scan of %s' % (st, d))
    line('a scan of %s asked of Plex' % d)


def awake_now(path, monitor):
    """The disk of a folder awake now - the folder itself may be gone (P6)."""
    return root_state(path, monitor)['state'] in ('awake', 'missing')


# ---------------------------------------------------------------------------
# P1: the only write into Plex's database
# ---------------------------------------------------------------------------
def _owner_run(argv, stdin_text, timeout):
    st = HX.PLEX_DB_DIR.stat()
    uid, gid = st.st_uid, st.st_gid

    def drop():
        if os.getuid() == 0 and uid != 0:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
    try:
        got = subprocess.run(argv, input=stdin_text.encode(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=timeout, cwd=str(HX.PLEX_DB_DIR),
                             preexec_fn=drop)
    except subprocess.TimeoutExpired:
        return 124, 'took longer than %d s' % timeout
    except OSError as e:
        return 127, str(e)
    return got.returncode, got.stdout.decode('utf-8', 'replace')


def _marker_off():
    try:
        _marker().unlink()
    except OSError:
        pass


def plex_process():
    rc, out = HX.host_run(['pgrep', '-f', r'/Plex Media Server( |$)'], 10)
    return rc == 0 and bool(out.strip())


def stop_plex():
    rc, out = HX.host_run([HX.SYSTEMCTL, 'stop', HX.PLEX_SERVICE], 300)
    if rc != 0:
        raise ValueError('Plex Media Server would not stop: %s' % out.strip()[-200:])
    for _ in range(60):
        if not plex_process():
            return
        time.sleep(1)
    raise ValueError('Plex Media Server\'s process is still there after it was stopped')


def start_plex():
    rc, out = HX.host_run([HX.SYSTEMCTL, 'start', HX.PLEX_SERVICE], 300)
    return rc == 0, out.strip()[-200:]


DB_SET = lambda: [HX.PLEX_DB, HX.PLEX_DB + '-wal', HX.PLEX_DB + '-shm']   # noqa: E731


def files_aside(dst):
    dst.mkdir(mode=0o700, parents=True)
    kept = []
    for n in DB_SET():
        p = HX.PLEX_DB_DIR / n
        if p.exists():
            shutil.copy2(str(p), str(dst / n))
            if (dst / n).stat().st_size != p.stat().st_size:
                raise ValueError('the copy of %s is not whole' % n)
            kept.append(n)
    return kept


def files_back(src, kept):
    st = HX.PLEX_DB_DIR.stat()
    for n in DB_SET():
        p = HX.PLEX_DB_DIR / n
        if p.exists() and n not in kept:
            p.unlink()
    for n in kept:
        shutil.copy2(str(src / n), str(HX.PLEX_DB_DIR / n))
        os.chown(str(HX.PLEX_DB_DIR / n), st.st_uid, st.st_gid)


def owners_right():
    st = HX.PLEX_DB_DIR.stat()
    for n in DB_SET():
        p = HX.PLEX_DB_DIR / n
        try:
            s = p.stat()
        except OSError:
            continue
        if (s.st_uid, s.st_gid) != (st.st_uid, st.st_gid):
            os.chown(str(p), st.st_uid, st.st_gid)


FIX_P1_STEPS = [('look', 'Looked at again'), ('copy', 'A checked copy of the database'),
                ('stop', 'Plex stopped'), ('aside', 'Its files copied aside'),
                ('write', 'The orphans taken out'), ('check', 'integrity_check'),
                ('start', 'Plex started'), ('scan', 'The folders scanned'),
                ('verify', 'Looked at again')]


def fix_p1(store, by, ids, part_ids, monitor=None):
    ids = sorted({int(i) for i in ids or []})
    part_ids = sorted({int(i) for i in part_ids or []})
    if not ids and not part_ids:
        raise ValueError('nothing chosen')
    if not os.path.exists(HX.PLEX_SQLITE):
        raise ValueError('Plex SQLite is not on this machine (%s) - plexmon writes Plex\'s '
                         'database only with it' % HX.PLEX_SQLITE)
    phase('look', 'the database is looked at again (a fresh copy)', 0, 9)
    with tempfile.TemporaryDirectory(dir=str(HX.plex_dir()), prefix='integrity-') as tmp:
        path, _ = snapshot(tmp)
        c = _open(path)
        try:
            ok, counts = index_ok(c)
            if not ok:
                raise ValueError('an index of Plex\'s database does not agree with its table (%s) '
                                 '- this is not P1; nothing is deleted. Plex SQLite: REINDEX' %
                                 counts)
            secs = sections(c)
            now = detect_p1(c, secs)
        finally:
            c.close()
    ids = [i for i in ids if i in set(now['ids'])]
    part_ids = [i for i in part_ids if i in set(now['part_ids'])]
    if not ids and not part_ids:
        raise ValueError('none of these is an orphan any more - nothing to do')
    rows = [r for r in now['items'] if r['media_item'] in ids] + \
        [r for r in now['parts'] if r['part'] in part_ids]
    line('%d media item(s), %d part(s) without a media item: still orphans' % (len(ids),
                                                                           len(part_ids)))
    check_stop()
    phase('copy', 'a checked copy of Plex\'s database (Plex goes on)', 1, 9)
    cp = HX.plex_copy(store, by + ' (before P1 was put right)', check=True)
    if (cp.get('check') or {}).get('ok') is False:
        raise ValueError('Plex\'s database is not whole even now (%s) - nothing is changed: '
                         'put back a copy first (Settings → The database)' %
                         '; '.join((cp['check'].get('said') or [])[:3]))
    line('the copy %s: checked, whole' % cp['name'])
    check_stop()
    was = HX.plex_running()
    aside = HX.plex_dir() / ('before-integrity-%s' % HX._stamp())
    kept = []
    result = {'copy': cp['name'], 'aside': str(aside), 'ids': ids, 'parts': part_ids}
    stopped = False
    try:
        phase('stop', 'Plex Media Server is stopped', 2, 9)
        if was:
            state_dir()
            _marker().write_text(str(time.time()))
            stopped = True          # started again whatever comes of the stop
            stop_plex()
            line('Plex Media Server stopped')
        phase('aside', 'its database files are copied aside', 3, 9)
        kept = files_aside(aside)
        line('copied aside: %s → %s' % (', '.join(kept), aside))
        phase('write', 'the orphans are taken out (Plex SQLite, one transaction)', 4, 9)
        q = ','.join(str(i) for i in ids) or '-1'
        qp = ','.join(str(i) for i in part_ids) or '-1'
        sql = ('.bail on\nBEGIN IMMEDIATE;\n'
               'CREATE TEMP TABLE pm_orphan AS SELECT id FROM media_items WHERE id IN (%s) '
               'AND metadata_item_id IS NOT NULL AND metadata_item_id NOT IN '
               '(SELECT id FROM metadata_items);\n'
               'CREATE TEMP TABLE pm_orphan_part AS SELECT id FROM media_parts WHERE id IN (%s) '
               'AND media_item_id NOT IN (SELECT id FROM media_items);\n'
               'DELETE FROM media_streams WHERE media_item_id IN (SELECT id FROM pm_orphan);\n'
               'DELETE FROM media_parts WHERE media_item_id IN (SELECT id FROM pm_orphan);\n'
               'DELETE FROM media_items WHERE id IN (SELECT id FROM pm_orphan);\n'
               'DELETE FROM media_streams WHERE media_part_id IN (SELECT id FROM pm_orphan_part);\n'
               'DELETE FROM media_parts WHERE id IN (SELECT id FROM pm_orphan_part);\n'
               "SELECT 'pm_items=' || (SELECT count(*) FROM pm_orphan);\n"
               "SELECT 'pm_parts=' || (SELECT count(*) FROM pm_orphan_part);\n"
               "SELECT 'pm_left=' || count(*) FROM media_items WHERE id IN (%s) AND "
               'metadata_item_id NOT IN (SELECT id FROM metadata_items);\n'
               'COMMIT;\n') % (q, qp, q)
        rc, out = _owner_run([HX.PLEX_SQLITE, HX.PLEX_DB], sql, 900)
        got = dict(re.findall(r'(pm_\w+)=(\d+)', out))
        if rc != 0 or 'pm_left' not in got:
            raise ValueError('Plex SQLite said: %s' % out.strip()[-300:])
        result.update(deleted_items=int(got.get('pm_items', 0)),
                      deleted_parts=int(got.get('pm_parts', 0)), left=int(got['pm_left']))
        line('taken out: %s media item(s), %s part(s); left: %s' % (
            got.get('pm_items'), got.get('pm_parts'), got['pm_left']))
        phase('check', 'Plex SQLite: integrity_check', 5, 9)
        rc, out = _owner_run([HX.PLEX_SQLITE, HX.PLEX_DB], 'PRAGMA integrity_check;\n', 3600)
        said = [x for x in out.strip().splitlines() if x.strip()]
        if rc != 0 or said != ['ok']:
            raise ValueError('integrity_check after the change: %s' % '; '.join(said[:4]))
        line('integrity_check: ok')
        owners_right()
    except Exception as e:
        if kept or aside.exists():
            try:
                if kept:
                    files_back(aside, kept)
                    line('Plex\'s database files put back as they were')
                    result['put_back'] = True
            except Exception as e2:                     # noqa: BLE001
                line('PUTTING BACK FAILED: %s - the files are in %s' % (e2, aside))
        if stopped:
            ok, out = start_plex()
            line('Plex Media Server started again' if ok else 'Plex would not start: %s' % out)
        _marker_off()
        HX.event(store, 'plex', '%s: P1 was NOT put right - %s; Plex\'s database is as it was '
                 '(its files: %s)' % (by, e, aside), 'bad', who=by)
        remember({'kind': 'P1', 'by': by, 'ok': False, 'error': str(e)[:300],
                  'aside': str(aside), 'copy': cp['name']})
        raise
    phase('start', 'Plex Media Server is started', 6, 9)
    if stopped:
        ok, out = start_plex()
        _marker_off()
        if not ok:
            line('Plex would not start: %s' % out)
            raise ValueError('Plex Media Server would not start again: %s' % out)
        if not plex_answers():
            line('Plex does not answer yet')
    HX.event(store, 'plex', '%s: P1 put right - %d orphan media item(s) and %d part(s) taken out '
             'of Plex\'s database; a copy before: %s, its files before: %s' % (
                 by, result['deleted_items'], result['deleted_parts'], cp['name'], aside),
             'warn', who=by)
    phase('scan', 'Plex scans their folders', 7, 9)
    dirs = sorted({(r.get('section'), r.get('dir') or os.path.dirname(r.get('file') or ''))
                   for r in rows if r.get('file')})
    scanned, asleep = [], []
    if dirs and wait_idle(timeout=600, quiet=20, why='before the scan'):
        for sec, d in dirs:
            if not sec or not d:
                continue
            if awake_now(d, monitor):
                refresh_dir(sec, d)
                scanned.append(d)
            else:
                asleep.append(d)
                line('%s: its disk sleeps - not woken; Plex takes the files in at its next scan '
                     'of the library' % d)
    result.update(scanned=scanned, asleep=asleep)
    phase('verify', 'looked at again, once Plex is done', 8, 9)
    if scanned:
        wait_idle(timeout=1800, quiet=45, why='after the scan')
    remember({'kind': 'P1', 'by': by, 'ok': True, 'copy': cp['name'], 'aside': str(aside),
              'items': result['deleted_items'], 'parts': result['deleted_parts'],
              'scanned': len(scanned)})
    try:
        scan(store, by + ' (after P1)', disks=False, monitor=monitor)
        result['after'] = last_report()['summary']
    except Stopped:
        pass
    says('done', 9, 9)
    return result


# ---------------------------------------------------------------------------
# P2: through Plex's own API - split, merge into the folder's owner
# ---------------------------------------------------------------------------
def api_sections():
    out = {}
    for d in PX.mc(PX.get('/library/sections')).get('Directory') or []:
        if d.get('type') == 'show':
            out[str(d.get('key'))] = [str(x.get('path') or '').rstrip('/')
                                      for x in d.get('Location') or []]
    return out


def api_group(sec, guid):
    got = PX.mc(PX.get('/library/sections/%s/all' % sec, {'type': 2, 'includeGuids': 1}))
    return [str(s.get('ratingKey')) for s in got.get('Metadata') or [] if s.get('guid') == guid]


def api_inventory(ids, roots):
    rows = []
    for sid in ids:
        got = PX.mc(PX.get('/library/metadata/%s/allLeaves' % sid, timeout=120))
        for ep in got.get('Metadata') or []:
            for m in ep.get('Media') or []:
                for p in m.get('Part') or []:
                    f = p.get('file') or ''
                    rt = max((r for r in roots if f.startswith(r + '/')), key=len, default=None)
                    d = (rt + '/' + f[len(rt) + 1:].split('/', 1)[0]) if rt and \
                        '/' in f[len(rt) + 1:] else None
                    rows.append({'show': str(sid), 'season': ep.get('parentIndex'),
                                 'ep': ep.get('index'), 'media': m.get('id'), 'file': f, 'dir': d})
    return rows


def _dirs(rows, sid):
    return {r['dir'] for r in rows if r['show'] == str(sid) and r['dir']}


def api_detect(rows):
    cnt = defaultdict(Counter)
    for r in rows:
        if r['dir']:
            cnt[r['dir']][r['show']] += 1
    owner = {d: c.most_common(1)[0][0] for d, c in cnt.items()}
    cases = defaultdict(list)
    for r in rows:
        if r['dir'] and r['show'] != owner[r['dir']]:
            cases[(r['show'], owner[r['dir']], r['dir'])].append(r)
    return cases


def _detail(sid):
    md = (PX.mc(PX.get('/library/metadata/%s' % sid)).get('Metadata') or [{}])[0]
    return md


FIX_P2_STEPS = [('idle', 'Plex has nothing running'), ('copy', 'A checked copy of the database'),
                ('look', 'The shows as they are now'), ('split', 'Split'),
                ('find', 'The new shows found by their folders'),
                ('merge', 'Merged into the folder\'s owner'), ('remerge', 'Its own folders together again'),
                ('titles', 'The titles back'), ('verify', 'Looked at again')]


def fix_p2(store, by, key, monitor=None):
    rep = last_report() or {}
    cs = next((x for x in (rep.get('problems') or {}).get('P2', {}).get('cases') or []
               if x['key'] == key), None)
    if not cs:
        raise ValueError('that case is not in the last look - look again')
    if not cs.get('fixable'):
        raise ValueError('this one is not for a button: %s' % (cs.get('why') or 'not on one match'))
    sec, guid = str(cs['section']), cs['guid']
    attached, owner = str(cs['attached']['id']), str(cs['owner']['id'])
    log_ = []

    def did(what, **kw):
        log_.append(dict(kw, what=what, at=time.time()))
        line(what)

    phase('idle', 'waiting until Plex has nothing running', 0, 9)
    if not wait_idle(timeout=1800, quiet=30, why='before the split'):
        raise ValueError('Plex did not stop working for half an hour - nothing done; try at night')
    phase('copy', 'a checked copy of Plex\'s database (Plex goes on)', 1, 9)
    cp = HX.plex_copy(store, by + ' (before P2 was put right)', check=True)
    if (cp.get('check') or {}).get('ok') is False:
        raise ValueError('Plex\'s database is not whole even now - nothing is changed')
    did('a checked copy: %s' % cp['name'])
    phase('look', 'the shows of this match as they are now', 2, 9)
    roots = api_sections().get(sec)
    if not roots:
        raise ValueError('library %s is not a TV library of Plex any more' % sec)
    before_ids = api_group(sec, guid)
    if attached not in before_ids or owner not in before_ids:
        raise ValueError('the two shows are no longer on one match - look again')
    before = api_inventory(before_ids, roots)
    bad = {(a, o, d): r for (a, o, d), r in api_detect(before).items() if a == attached}
    if not bad:
        raise ValueError('Plex shows nothing under the wrong show now - nothing to do')
    snap = {}
    for sid in before_ids:
        md = _detail(sid)
        lf = PX.locked_fields(md)
        snap[sid] = {'title': md.get('title') or '', 'locked': 'title' in lf,
                     'titleSort': md.get('titleSort') or '', 'sort_locked': 'titleSort' in lf,
                     'dirs': sorted(_dirs(before, sid)),
                     'eps': len({(r['season'], r['ep']) for r in before if r['show'] == sid}),
                     'versions': len([r for r in before if r['show'] == sid])}
    bad_dirs = {d for (_, _, d) in bad}
    owners = {d: o for (_, o, d) in bad}
    own_dirs = set(snap[attached]['dirs']) - bad_dirs
    expected = {}
    for o in set(owners.values()):
        mine = {d for d, oo in owners.items() if oo == o}
        expected[o] = len({(r['season'], r['ep']) for r in before if r['show'] == o or
                           (r['dir'] in mine and r['show'] == attached)})
    did('before: %s' % '; '.join('%s "%s" %d episodes in %s' % (
        k, v['title'], v['eps'], ', '.join(v['dirs'])) for k, v in snap.items()), snapshot=snap)
    check_stop()
    phase('split', 'Split of "%s"' % snap[attached]['title'], 3, 9)
    PX.put('/library/metadata/%s/split' % attached)
    did('PUT /library/metadata/%s/split' % attached)
    phase('find', 'the new shows found by their folders', 4, 9)
    if not wait_idle(timeout=1800, quiet=30, why='after the split'):
        raise ValueError('after the split Plex did not stop working - STOP: nothing more done; '
                         'the shows are split, merge them by hand in Plex')
    after_ids = api_group(sec, guid)
    after = api_inventory(after_ids, roots)
    frag = {}
    for d in bad_dirs:
        f = [s for s in after_ids if s != owners[d] and _dirs(after, s) == {d}]
        if len(f) != 1:
            raise ValueError('STOP after the split: %d shows hold only %s (one was expected) - '
                             'nothing merged; the split is as Plex left it' % (len(f), d))
        frag[d] = f[0]
    for o in set(owners.values()):
        if o not in after_ids or _dirs(after, o) != set(snap[o]['dirs']):
            raise ValueError('STOP after the split: the owner %s changed - nothing merged' % o)
    remainder = [s for s in after_ids if s not in frag.values() and s not in owners.values()
                 and _dirs(after, s) & own_dirs]
    for d in own_dirs:
        if len([s for s in after_ids if d in _dirs(after, s)]) != 1:
            raise ValueError('STOP after the split: %s is in more than one show' % d)
    did('after the split: %s' % ', '.join('%s holds %s' % (s, ', '.join(sorted(_dirs(after, s))))
                                         for s in after_ids))
    phase('merge', 'merged into the folder\'s owner', 5, 9)
    for d, f in frag.items():
        o = owners[d]
        for attempt in (1, 2):
            try:
                PX.put('/library/metadata/%s/merge' % o, {'ids': f})
                did('PUT /library/metadata/%s/merge?ids=%s (%s)' % (o, f, d))
                break
            except Exception as e:                      # noqa: BLE001
                if attempt == 2:
                    raise ValueError('STOP: the merge of %s into %s failed twice: %s' % (f, o, e))
                wait_idle(timeout=900, quiet=20, why='before trying the merge again')
    phase('remerge', 'the show\'s own folders together again', 6, 9)
    if len(remainder) > 1:
        primary = attached if attached in remainder else max(
            remainder, key=lambda s: len([r for r in after if r['show'] == s]))
        others = [s for s in remainder if s != primary]
        wait_idle(timeout=1800, quiet=30, why='before the merge')
        PX.put('/library/metadata/%s/merge' % primary, {'ids': ','.join(others)})
        did('PUT /library/metadata/%s/merge?ids=%s' % (primary, ','.join(others)))
    else:
        skip('remerge')
    phase('titles', 'the titles chosen by hand put back', 7, 9)
    wait_idle(timeout=1800, quiet=30, why='before the titles')
    final_ids = api_group(sec, guid)
    final = api_inventory(final_ids, roots)
    for sid in final_ids:
        dirs = _dirs(final, sid)
        for old_id, old in snap.items():
            want = set(old['dirs']) - (bad_dirs if old_id == attached else set())
            if want and want == dirs and old['locked']:
                md = _detail(sid)
                if (md.get('title') or '') != old['title']:
                    PX.put('/library/sections/%s/all' % sec, {
                        'type': 2, 'id': sid, 'title.value': old['title'], 'title.locked': 1})
                    did('the title of %s back to "%s"' % (sid, old['title']))
                if old['sort_locked'] and old['titleSort']:
                    PX.put('/library/sections/%s/all' % sec, {
                        'type': 2, 'id': sid, 'titleSort.value': old['titleSort'],
                        'titleSort.locked': 1})
    phase('verify', 'looked at again, once Plex is done', 8, 9)
    wait_idle(timeout=1800, quiet=60, why='before looking again')
    check_ids = api_group(sec, guid)
    check = api_inventory(check_ids, roots)
    left = [k for k in api_detect(check) if k[2] in bad_dirs]
    got = {o: len({(r['season'], r['ep']) for r in check if r['show'] == o}) for o in expected}
    ok = not left and all(got[o] == expected[o] for o in expected)
    res = {'ok': ok, 'left': [list(k) for k in left], 'expected': expected, 'got': got,
           'copy': cp['name'], 'steps': log_}
    HX.event(store, 'plex', '%s: P2 %s - "%s" split, %s merged into %s%s; a copy before: %s' % (
        by, 'put right' if ok else 'done, but the check does not agree', snap[attached]['title'],
        ', '.join(frag.values()), ', '.join(sorted(set(owners.values()))),
        '' if ok else ' (left: %s, episodes %s, expected %s)' % (left, got, expected), cp['name']),
        'warn' if ok else 'bad', who=by)
    remember({'kind': 'P2', 'by': by, 'ok': ok, 'copy': cp['name'], 'show': snap[attached]['title'],
              'dirs': sorted(bad_dirs), 'got': got, 'expected': expected})
    try:
        scan(store, by + ' (after P2)', disks=False, monitor=monitor)
    except Stopped:
        pass
    says('done', 9, 9)
    return res


# ---------------------------------------------------------------------------
# P5: a scan of the folder; P6 and P7: a scan and the trash emptied
# ---------------------------------------------------------------------------
FIX_SCAN_STEPS = [('idle', 'Plex has nothing running'), ('scan', 'The folders scanned'),
                  ('wait', 'Plex done'), ('trash', 'The trash emptied'), ('verify', 'Looked at again')]


def fix_scan(store, by, kind, section=None, dirs=None, trash=False, monitor=None):
    rep = last_report() or {}
    secs = {s['id']: s for s in rep.get('sections') or []}
    want = []
    if kind == 'P5':
        allowed = {(m['section'], m['dir']) for m in rep['problems']['P5']['missing']
                   if m['reason'] == 'unseen'}
        want = [(s, d) for s, d in allowed if not dirs or d in dirs]
    elif kind in ('P6', 'P7'):
        section = int(section or 0)
        if section not in secs:
            raise ValueError('which library?')
        if kind == 'P6':
            want = sorted({(m['section'], m['dir']) for m in rep['problems']['P6']['missing']
                           if m['section'] == section})
        trash = True
    if trash:
        s = secs.get(section) or {}
        why = []
        for r in s.get('roots') or []:
            st = root_state(r['path'], monitor)
            if st['state'] != 'awake':
                why.append('%s: %s' % (r['path'], st['why'] or st['state']))
            elif r.get('mount_problem'):
                why.append('%s: too much of it is missing - a disk not mounted?' % r['path'])
            elif too_much(r.get('trash', 0), r.get('files') or 0):
                why.append('%s: %d of its %d files are in Plex\'s trash - more than 5 %%' % (
                    r['path'], r['trash'], r['files']))
        if why:
            raise ValueError('the trash of "%s" is not emptied: %s' % (s.get('name'),
                                                                       '; '.join(why)))
    phase('idle', 'waiting until Plex has nothing running', 0, 5)
    if not wait_idle(timeout=1800, quiet=20, why='before the scan'):
        raise ValueError('Plex did not stop working for half an hour - nothing done')
    phase('scan', 'the folders are scanned', 1, 5)
    done, asleep = [], []
    for sec, d in want:
        check_stop()
        if awake_now(d, monitor):
            roots = [r['path'] for r in (secs.get(sec) or {}).get('roots') or []]
            d2 = existing_dir(d, roots)
            if not d2:
                line('%s: no folder of it is there - left alone' % d)
                continue
            refresh_dir(sec, d2)
            done.append(d2)
        else:
            asleep.append(d)
            line('%s: its disk sleeps - not woken' % d)
    if not want:
        skip('scan')
    phase('wait', 'until Plex is done', 2, 5)
    if done:
        wait_idle(timeout=3600, quiet=45, why='after the scan')
    phase('trash', 'the trash is emptied', 3, 5)
    if trash:
        # Looked at again now, after the scan: what is in the trash.
        says('how much is in the trash now - a fresh copy of the database', 3, 5)
        tr, fl = trash_now(section)
        for root, n in fl.items():
            if too_much(tr.get(root, 0), n):
                raise ValueError('the trash is NOT emptied: %d of the %d files of %s are in '
                                 'Plex\'s trash - more than 5 %%; a disk not mounted when Plex '
                                 'looked?' % (tr[root], n, root))
            if not n and tr.get(root, 0):
                raise ValueError('the trash is NOT emptied: %s has nothing but trash' % root)
        PX.put('/library/sections/%s/emptyTrash' % section)
        line('PUT /library/sections/%s/emptyTrash' % section)
        wait_idle(timeout=1800, quiet=30, why='after the trash')
    else:
        skip('trash')
    HX.event(store, 'plex', '%s: %s - %d folder(s) scanned%s%s' % (
        by, kind, len(done), ' and the trash of "%s" emptied' % secs[section]['name']
        if trash else '', ' (%d left alone: their disks sleep)' % len(asleep) if asleep else ''),
        who=by)
    remember({'kind': kind, 'by': by, 'ok': True, 'scanned': len(done), 'asleep': len(asleep),
              'trash': bool(trash)})
    phase('verify', 'looked at again', 4, 5)
    try:
        scan(store, by + ' (after %s)' % kind, disks=kind in ('P5', 'P6'), monitor=monitor)
    except Stopped:
        pass
    says('done', 5, 5)
    return {'scanned': done, 'asleep': asleep, 'trash': bool(trash)}


def trash_now(section):
    with tempfile.TemporaryDirectory(dir=str(HX.plex_dir()), prefix='integrity-') as tmp:
        path, _ = snapshot(tmp)
        c = _open(path)
        try:
            secs = sections(c)
            roots = (secs.get(section) or {}).get('roots') or []
            dl = _has(c, 'media_parts', 'deleted_at')
            tr, fl = Counter(), Counter()
            for r in _rows(c, 'SELECT mp.file AS file, %s AS gone FROM media_parts mp JOIN '
                              'media_items mi ON mi.id = mp.media_item_id WHERE '
                              'mi.library_section_id = ?' % (
                                  '(mp.deleted_at IS NOT NULL OR mi.deleted_at IS NOT NULL)'
                                  if dl else '0'), (section,)):
                rt = root_of(r['file'] or '', roots)
                if rt:
                    fl[rt['path']] += 1
                    if r['gone']:
                        tr[rt['path']] += 1
        finally:
            c.close()
    return tr, fl


# ---------------------------------------------------------------------------
# Plex's own Optimize and Clean Bundles, followed to the end
# ---------------------------------------------------------------------------
TASK_MATCH = {'optimize': re.compile(r'optimi|vacuum|database', re.I),
              'clean': re.compile(r'bundle|clean', re.I)}


def _db_bytes():
    n = 0
    for x in HX.plex_db_files():
        if x['name'].startswith(HX.PLEX_DB):
            n += x['bytes']
    return n


def _db_stamp():
    out = []
    for x in HX.plex_db_files():
        out.append((x['name'], x['bytes'], round(x['at'], 1)))
    return tuple(out)


def plex_task(store, by, kind):
    """Optimize: a checked copy first; asked of Plex; followed through
    /activities (its progress) and its files until it is done."""
    whole = 100
    before = _db_bytes()
    res = {'kind': kind, 'before': before}
    if kind == 'optimize':
        HX.says('a checked copy of the database first (Plex goes on)', 2, whole)
        HX.SAYS_SCALE.append((2, 20))
        try:
            cp = HX.plex_copy(store, by + ' (before Plex optimized its database)', check=True)
        finally:
            HX.SAYS_SCALE.pop()
        res['copy'] = cp['name']
        if (cp.get('check') or {}).get('ok') is False:
            raise ValueError('the database is not whole - not optimized: put a copy back first')
        HX.says('Plex is asked to optimize its database', 20, whole)
        HX.plex_api('/library/optimize?async=1')
        base = 20
    else:
        HX.says('Plex is asked to clean its bundles', 5, whole)
        HX.plex_api('/library/clean/bundles?async=1')
        base = 5
    HX.event(store, 'database', '%s asked Plex Media Server to %s' % (
        by, 'optimize its database' if kind == 'optimize' else 'clean its bundles'), who=by)
    t0 = time.time()
    seen = False
    calm = None
    last = _db_stamp()
    still = time.time()
    pat = TASK_MATCH[kind]
    top = [base]

    def at(n, words):
        top[0] = max(top[0], min(whole - 2, n))
        HX.says(words, top[0], whole)
    while time.time() - t0 < 4 * 3600:
        acts = [a for a in PX.activities() if pat.search('%s %s %s' % (
            a.get('type') or '', a.get('title') or '', a.get('subtitle') or ''))]
        now = _db_stamp()
        if now != last:
            last, still = now, time.time()
        if acts:
            seen, calm = True, None
            a = acts[0]
            p = a.get('progress')
            pc = int(p) if isinstance(p, (int, float)) else None
            at(base + int((whole - base - 2) * (pc or 0) / 100),
               'Plex: %s%s' % (a.get('title') or a.get('type'), (' - %s' % a['subtitle'])
                               if a.get('subtitle') else ''))
            res['activity'] = a.get('title') or a.get('type')
        else:
            calm = calm or time.time()
            el = time.time() - t0
            quiet_files = time.time() - still
            if seen:
                at(whole - 3, 'Plex is done - its files settle')
            else:
                at(base + int(el / 3), 'Plex works on it (it says nothing of it as it goes)')
            if seen and time.time() - calm > 8:
                break
            if not seen and el > 20 and quiet_files > 20 and time.time() - calm > 20:
                break
        time.sleep(1 if os.environ.get('PLEXMON_TEST') else 2)
    res['after'] = _db_bytes()
    res['took'] = round(time.time() - t0)
    res['followed'] = seen
    if kind == 'optimize':
        HX.event(store, 'database', 'Plex Media Server optimized its database: %s → %s (%d s)' % (
            HX.human_bytes(before), HX.human_bytes(res['after']), res['took']), who=by)
    else:
        HX.event(store, 'database', 'Plex Media Server cleaned its bundles (%d s)' % res['took'],
                 who=by)
    HX.says('done', whole, whole)
    put = getattr(HX, 'put_meta', None)
    if put:
        put(store, 'plexdb:task:%s' % kind, dict(res, at=time.time(), by=by))
    return res


# ---------------------------------------------------------------------------
# A package for analysis (2.11.2): everything found, in full, to send on -
# nothing is put right, nothing of Plex's is changed
# ---------------------------------------------------------------------------
BUNDLE_STEPS = [('snapshot', 'A copy of the database'), ('tables', 'The tables written out'),
                ('logs', 'Plex\'s logs, the lines that matter'), ('env', 'Plex, the disks, plexmon'),
                ('pack', 'Packed')]
BUNDLE_KEEP = 3
LOG_KEEP = re.compile(r'merging ID|Destroying metadata item|/library/metadata/\d+/(split|merge)|'
                      r'LongRunningJobManager|Updating media item|found agent match|emptyTrash|'
                      r'/library/sections/\d+/refresh|Scanner|metadata item .* deleted|'
                      r'Fix ?match|unmatch|Migrating metadata|ERROR|WARN', re.I)
SECRET_RE = re.compile(r'(?i)(x-plex-token|token|plexonlinetoken|authToken|password)([=:"\s]+)'
                       r'[A-Za-z0-9_\-.~%]{6,}')


def bundle_dir():
    d = state_dir() / 'packages'
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    return d


def bundles():
    out = []
    for p in sorted(bundle_dir().glob('plex-integrity-*.zip'), reverse=True):
        st = p.stat()
        out.append({'name': p.name, 'bytes': st.st_size, 'at': st.st_mtime})
    return out


def _scrub(text):
    return SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + '***', text)


def _csv(rows, cols=None):
    import csv
    import io
    buf = io.StringIO()
    if not rows:
        return ''
    cols = cols or list(rows[0].keys())
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow(['' if r.get(k) is None else r.get(k) for k in cols])
    return buf.getvalue()


def bundle(store, by, monitor=None):
    import zipfile
    files = {}
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=str(HX.plex_dir()), prefix='integrity-') as tmp:
        phase('snapshot', 'the library database is copied (Plex goes on)', 0, 5)
        path, size = snapshot(tmp)
        check_stop()
        phase('tables', 'the tables are written out', 1, 5)
        c = _open(path)
        try:
            secs = sections(c)
            ok, counts = index_ok(c)
            tabs = []
            for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                try:
                    tabs.append({'table': n, 'rows': c.execute('SELECT count(*) FROM "%s"' % n)
                                 .fetchone()[0]})
                except sqlite3.Error as e:
                    tabs.append({'table': n, 'rows': 'error: %s' % e})
            files['db/tables.csv'] = _csv(tabs)
            schema = []
            for n in ('library_sections', 'section_locations', 'metadata_items', 'media_items',
                      'media_parts', 'media_streams'):
                for (sql,) in c.execute("SELECT sql FROM sqlite_master WHERE tbl_name = ? AND sql "
                                        "IS NOT NULL", (n,)):
                    schema.append(sql + ';')
            files['db/schema.sql'] = '\n\n'.join(schema) + '\n'
            try:
                uv = c.execute('PRAGMA user_version').fetchone()[0]
            except sqlite3.Error:
                uv = None
            files['db/index-check.json'] = json.dumps({'index_ok': ok, 'counts': counts,
                                                       'user_version': uv}, indent=1)
            files['db/sections.csv'] = _csv([{'section': s['id'], 'name': s['name'], 'type':
                                              s['type'], 'location_id': r['id'], 'root': r['path']}
                                             for s in secs.values() for r in s['roots']])
            says('P1 in full', 1, 5)
            dl = ' AND mi.deleted_at IS NULL' if _has(c, 'media_items', 'deleted_at') else ''
            files['p1-orphan-media-items.csv'] = _csv(_rows(
                c, 'SELECT mi.id AS media_item, mi.metadata_item_id AS missing_metadata, '
                   'mi.library_section_id AS section, mi.section_location_id AS location, '
                   'mp.id AS part, mp.file, mp.size FROM media_items mi LEFT JOIN media_parts mp '
                   'ON mp.media_item_id = mi.id WHERE mi.metadata_item_id IS NOT NULL AND NOT EXISTS '
                   '(SELECT 1 FROM metadata_items m WHERE m.id = mi.metadata_item_id)' + dl +
                   ' ORDER BY mp.file'))
            files['p1-parts-without-item.csv'] = _csv(_rows(
                c, 'SELECT mp.id AS part, mp.media_item_id AS media_item, mp.file, mp.size FROM '
                   'media_parts mp WHERE NOT EXISTS (SELECT 1 FROM media_items mi WHERE mi.id = '
                   'mp.media_item_id)'))
            says('P2, P3 - every episode of the shows on one match', 2, 5)
            p3 = detect_p3(c, secs)
            files['p3-groups.csv'] = _csv(p3)
            ids = sorted({int(i) for g in p3 for i in str(g['ids']).split(',') if i})
            rows = []
            dpart = ', mp.deleted_at AS part_deleted' if _has(c, 'media_parts', 'deleted_at') else ''
            ditem = ', mi.deleted_at AS item_deleted' if _has(c, 'media_items', 'deleted_at') else ''
            for i in range(0, len(ids), 400):
                q = ','.join(str(x) for x in ids[i:i + 400])
                rows += _rows(c, 'SELECT show.id AS show_id, show.title AS show_title, show.guid, '
                                 'show.library_section_id AS section, season.id AS season_id, '
                                 'season."index" AS s, ep.id AS episode, ep."index" AS e, ep.title '
                                 'AS episode_title, mi.id AS media_item, mi.section_location_id AS '
                                 'location, mp.id AS part, mp.file' + ditem + dpart +
                              ' FROM metadata_items show JOIN metadata_items season ON '
                              'season.parent_id = show.id JOIN metadata_items ep ON ep.parent_id '
                              '= season.id LEFT JOIN media_items mi ON mi.metadata_item_id = ep.id '
                              'LEFT JOIN media_parts mp ON mp.media_item_id = mi.id WHERE show.id '
                              'IN (%s) ORDER BY show.id, season."index", ep."index"' % q)
            files['p2-p3-episodes-of-shows-on-one-match.csv'] = _csv(rows)
            parts = episode_parts(c)
            p2, _, _ = detect_p2(parts, secs, p3)
            files['p2-cases.json'] = json.dumps(p2, indent=1, ensure_ascii=False)
            says('P7, P8 in full', 2, 5)
            p7 = detect_p7(c)
            files['p7.json'] = json.dumps(p7, indent=1, ensure_ascii=False)
            for r in parts:
                sx = secs.get(r['section'])
                r['dir'] = show_dir(r['file'] or '', sx['roots'])[1] if sx else None
            files['p8-gaps.json'] = json.dumps(detect_p8(c, parts), indent=1, ensure_ascii=False)
            files['db/metadata-by-type.csv'] = _csv(_rows(
                c, 'SELECT library_section_id AS section, metadata_type AS type, count(*) AS n '
                   'FROM metadata_items GROUP BY 1, 2 ORDER BY 1, 2'))
        finally:
            c.close()
    check_stop()
    phase('logs', 'Plex\'s logs - the lines that matter, tokens taken out', 2, 5)
    kept, n_files = [], 0
    for p in sorted(log_files(), key=lambda x: x.stat().st_mtime if x.exists() else 0):
        n_files += 1
        try:
            with open(str(p), 'r', encoding='utf-8', errors='replace') as f:
                for ln in f:
                    if LOG_KEEP.search(ln):
                        kept.append('%s: %s' % (p.name, _scrub(ln.rstrip('\n'))[:600]))
        except OSError:
            continue
    files['logs/plex-media-server-selected.txt'] = '\n'.join(kept[-30000:]) + '\n'
    files['p4.json'] = json.dumps(detect_p4(set()), indent=1, default=str)
    check_stop()
    phase('env', 'Plex, the disks, plexmon', 3, 5)
    env = {'plexmon': A.__version__, 'made': datetime.now().isoformat(timespec='seconds'),
           'by': by, 'db_bytes': size, 'plex_sqlite': os.path.exists(HX.PLEX_SQLITE),
           'plex_running': HX.plex_running()}
    try:
        env['plex'] = PX.server_info()
    except Exception as e:                              # noqa: BLE001
        env['plex'] = 'not answering: %s' % e
    try:
        env['activities'] = PX.activities()
        env['watching'] = PX.now_playing()
    except Exception:                                   # noqa: BLE001
        pass
    power = disk_power(monitor)
    env['disk_power'] = power
    mts = mounts()
    env['roots'] = [dict(root_state(r['path'], monitor, mts, power), section=s['id'],
                         path=r['path']) for s in secs.values() for r in s['roots']]
    env['mounts'] = [m for m in mts if m['mount'].startswith(('/data', '/mnt', '/media', '/srv',
                                                              '/home', '/'))
                     and m['fs'] not in ('proc', 'sysfs', 'cgroup2', 'tmpfs', 'devtmpfs',
                                         'securityfs', 'debugfs', 'tracefs', 'bpf', 'mqueue',
                                         'devpts', 'pstore', 'configfs', 'fusectl', 'hugetlbfs',
                                         'autofs', 'binfmt_misc', 'selinuxfs', 'rpc_pipefs')]
    files['environment.json'] = json.dumps(env, indent=1, default=str, ensure_ascii=False)
    rep = last_report()
    if rep:
        files['last-look/report.json'] = json.dumps(rep, indent=1, default=str, ensure_ascii=False)
        files['last-look/report.txt'] = report_text(rep)
    files['history.json'] = json.dumps(history(), indent=1, default=str, ensure_ascii=False)
    plan = []
    for cs in p2:
        plan.append({'case': cs['key'], 'fixable': cs['fixable'], 'why_not': cs['why'],
                     'steps': ['wait until /activities is empty for 30 s',
                               'a checked copy of the database',
                               'PUT /library/metadata/%s/split' % cs['attached']['id'],
                               'find the new show holding only %s' % cs['dir'],
                               'PUT /library/metadata/%s/merge?ids=<that show>' % cs['owner']['id'],
                               'merge the remaining pieces of %s back if it had several folders'
                               % cs['attached']['id'], 'put the locked titles back',
                               'look again']})
    files['what-would-be-done.json'] = json.dumps(plan, indent=1, ensure_ascii=False)
    files['README.txt'] = (
        'plexmon %s - a package for analysis of the integrity of the Plex library\n'
        'made %s by %s. Nothing was put right to make it; nothing of Plex\'s was changed.\n\n'
        'environment.json             Plex, plexmon, every library folder and its disk now\n'
        'last-look/report.*           the last look (Library integrity), as the page shows it\n'
        'p1-*.csv                     P1 in full: orphan media items, parts without an item\n'
        'p2-cases.json                P2: files under another show, with the folder\'s owner\n'
        'p2-p3-episodes-of-shows-on-one-match.csv   every episode and file of the shows on one '
        'match (P3)\n'
        'p3-groups.csv, p7.json, p8-gaps.json, p4.json   the other findings in full\n'
        'what-would-be-done.json      the steps a fix of each P2 case would take\n'
        'db/                          tables and their rows, the schema, the index check\n'
        'logs/                        the lines of Plex\'s logs that matter (merges, splits, '
        'scans, jobs, errors) - tokens taken out\n'
        'history.json                 what was put right here before\n\n'
        'Not in it: Plex\'s database itself, its token, any password. It does hold the names and '
        'paths of the library.\n') % (A.__version__, env['made'], by)
    phase('pack', 'packed', 4, 5)
    name = 'plex-integrity-%s.zip' % HX._stamp()
    dst = bundle_dir() / name
    part = str(dst) + '.part'
    with zipfile.ZipFile(part, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for k, v in files.items():
            z.writestr('plex-integrity/' + k, v)
    os.chmod(part, 0o600)
    os.replace(part, str(dst))
    for old in bundles()[BUNDLE_KEEP:]:
        try:
            (bundle_dir() / old['name']).unlink()
        except OSError:
            pass
    HX.event(store, 'plex', '%s made a package for analysis of the library: %s (%s)' % (
        by, name, HX.human_bytes(dst.stat().st_size)), who=by)
    says('done', 5, 5)
    return {'name': name, 'bytes': dst.stat().st_size, 'took': round(time.time() - t0, 1),
            'files': len(files)}


# ---------------------------------------------------------------------------
# What the pages are given
# ---------------------------------------------------------------------------
def view(store, monitor=None):
    rep = last_report()
    try:
        watching = PX.now_playing()
    except Exception:                                   # noqa: BLE001
        watching = 0
    try:
        acts = busy_now()
    except Exception:                                   # noqa: BLE001
        acts = []
    roots = []
    if rep:
        power = disk_power(monitor)
        mts = mounts()
        for s in rep.get('sections') or []:
            for r in s.get('roots') or []:
                st = root_state(r['path'], monitor, mts, power)
                roots.append({'section': s['id'], 'path': r['path'], 'state': st['state'],
                              'why': st['why'], 'disks': st['disks']})
    return {'report': rep, 'job': job_view(), 'history': history()[:20], 'now': time.time(),
            'plex': {'running': HX.plex_running(), 'watching': watching, 'busy': acts[:6],
                     'plex_sqlite': os.path.exists(HX.PLEX_SQLITE)},
            'roots_now': roots, 'db_job': dict(HX.DB_JOB), 'packages': bundles()}


def report_text(rep):
    if not rep:
        return 'No look yet.\n'
    out = ['plexmon %s - the integrity of the Plex library' % rep.get('version', ''),
           'looked at %s by %s, %s s' % (datetime.fromtimestamp(rep['at']).strftime(
               '%Y-%m-%d %H:%M:%S'), rep.get('by'), rep.get('took')),
           'summary: %s' % rep['summary']['words'], '']
    for s in rep.get('sections') or []:
        out.append('library %s "%s"' % (s['id'], s['name']))
        for r in s['roots']:
            out.append('  %-40s %-8s %6d files%s%s' % (r['path'], r['state'], r['files'],
                                                      ', %d missing' % r['missing'] if r.get('looked')
                                                      else '', (' - ' + r['why']) if r.get('why')
                                                      else ''))
    p = rep['problems']
    out.append('\nP1 (%d)' % p['P1']['count'])
    for r in p['P1']['items']:
        out.append('  media_item %s -> missing metadata %s  %s' % (r['media_item'],
                                                                 r['missing_metadata'], r['file']))
    for r in p['P1']['parts']:
        out.append('  media_part %s -> missing media_item %s  %s' % (r['part'], r['media_item'],
                                                                   r['file']))
    out.append('\nP2 (%d files)' % p['P2']['count'])
    for cs in p['P2']['cases']:
        out.append('  %s "%s" holds files of %s "%s" (%s)%s' % (
            cs['attached']['id'], cs['attached']['title'], cs['owner']['id'], cs['owner']['title'],
            cs['dir'], '' if cs['fixable'] else ' - not for a button: ' + cs['why']))
        for f in cs['files']:
            out.append('      S%02dE%02d media %s %s' % (f['s'] or 0, f['e'] or 0, f['media_item'],
                                                       f['file']))
    out.append('\nP3 (%d groups)' % p['P3']['count'])
    for g in p['P3']['groups']:
        out.append('  %s: %s' % (g['guid'], ' | '.join('%s "%s"' % (s['id'], s['title'])
                                                      for s in g['shows'])))
    out.append('\nP4 (%d races, %d risky operations, %d logs read)' % (
        p['P4']['count'], len(p['P4']['risky']), p['P4']['logs']))
    for r in p['P4']['races']:
        out.append('  media item %s written back to %s, destroyed before' % (r['media_item'],
                                                                           r['metadata']))
    out.append('\nP5 (%d)' % p['P5']['count'])
    for m in p['P5']['missing']:
        out.append('  %-11s %s - %s' % (m['reason'], m['file'], m['advice']))
    out.append('\nP6 (%d)' % p['P6']['count'])
    for m in p['P6']['missing']:
        out.append('  %s' % m['file'])
    for r in p['P6']['roots']:
        out.append('  MOUNT? %s: %d of %d missing' % (r['path'], r['missing'], r['files']))
    out.append('\nP7 (%d)' % p['P7']['count'])
    for k in ('orphans', 'empty', 'no_media'):
        for r in p['P7'][k]:
            out.append('  %-8s %s "%s"' % (k, r['id'], r.get('title')))
    out.append('\nP8 (%d seasons)' % p['P8']['count'])
    for g in p['P8']['seasons']:
        out.append('  "%s" S%02d: %d of %d, missing %s%s' % (
            g['title'], g['s'], g['have'], g['max'], ','.join(str(x) for x in g['missing']),
            ''.join(' [E%s on the disk: %s]' % (k, v['why']) for k, v in g['found'].items())))
    return '\n'.join(out) + '\n'
