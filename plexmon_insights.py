#!/usr/bin/env python3
"""plexmon_insights.py - the Insights tab and what keeps watch by itself (2.12.0).

  - Watching: who watched what, how (direct play, direct stream, transcode and
    why) - a look at Plex's sessions once a minute, kept in plexmon's own
    database, beside Plex's own history.
  - Transcoding: what made Plex transcode, and which files in the library are
    likely to (HEVC, AV1, 4K, TrueHD/DTS, picture subtitles).
  - Subtitles and audio: where the languages wanted are missing.
  - Duplicates: one film or episode in more than one version.
  - Episodes: what has aired and is not in Plex, and what comes next (TVmaze).
  - New files: each file Plex took in, read once by ffprobe - only on a disk
    that is awake.
  - Plex server: a watchdog, its load, a new version of it, and its caches.
  - Notifications: e-mail, Telegram, ntfy.
  - The integrity of the library looked at on a schedule.

Plex is only read: its API on 127.0.0.1 and a copy of its database (as in
Library integrity). A sleeping disk is never woken by any of this. Nothing in
the library is ever deleted; the cleanup takes only Plex's caches.
"""

import base64
import json
import os
import re
import shutil
import smtplib
import ssl
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from pathlib import Path

A = None                # plexmon_admin
HX = None               # plexmon_host
PX = None               # plexmon_plex
IX = None               # plexmon_integrity
CTX = {'store': None, 'monitor': None, 'wake': None}

SUB_IMAGE = {'pgs', 'hdmv_pgs_subtitle', 'vobsub', 'dvd_subtitle', 'dvdsub', 'dvb_subtitle'}
AUDIO_HEAVY = {'truehd', 'dca', 'dts', 'dtshd', 'dts-hd', 'mlp'}
VIDEO_HEAVY = {'hevc': 'HEVC (H.265)', 'h265': 'HEVC (H.265)', 'av1': 'AV1', 'vp9': 'VP9',
               'mpeg2video': 'MPEG-2', 'vc1': 'VC-1'}
OLD_CONTAINERS = {'avi', 'wmv', 'asf', 'mpeg', 'mpg', 'flv', 'vob', 'ts', 'm2ts'}
LANGS = {'cs': ('cs', 'cze', 'ces'), 'sk': ('sk', 'slo', 'slk'), 'en': ('en', 'eng'),
         'de': ('de', 'ger', 'deu'), 'fr': ('fr', 'fre', 'fra'), 'es': ('es', 'spa'),
         'it': ('it', 'ita'), 'pl': ('pl', 'pol'), 'hu': ('hu', 'hun'), 'ru': ('ru', 'rus'),
         'uk': ('uk', 'ukr'), 'ja': ('ja', 'jpn'), 'ko': ('ko', 'kor'), 'zh': ('zh', 'chi', 'zho'),
         'pt': ('pt', 'por'), 'nl': ('nl', 'dut', 'nld'), 'sv': ('sv', 'swe'),
         'da': ('da', 'dan'), 'fi': ('fi', 'fin'), 'no': ('no', 'nor', 'nb', 'nob')}
ROW_CAP = 5000


def bind(admin, host, plex, ix):
    global A, HX, PX, IX
    A, HX, PX, IX = admin, host, plex, ix
    if on_event not in admin.EVENT_HOOKS:
        admin.EVENT_HOOKS.append(on_event)


def loop(store, stop, monitor=None, wake=None):
    """Once a minute, in a thread of its own: nothing here holds up the rest of plexmon."""
    attach(store, monitor, wake)
    stop.wait(20)
    while not stop.is_set():
        t0 = time.time()
        try:
            maint = bool((A.maintenance_now(store) or {}).get('on'))
            tick(store, maint)
        except Exception as e:                          # noqa: BLE001
            log('tick: %s' % e, 'warn')
        stop.wait(max(5.0, 60.0 - (time.time() - t0)))


def attach(store, monitor=None, wake=None):
    CTX.update(store=store, monitor=monitor, wake=wake)


def cfg(k, default=None):
    return A.CONFIG.get(k, default)


def ev(store, kind, words, level='info', who=''):
    try:
        store.add_event(kind, words, level, who)
    except Exception:                                   # noqa: BLE001
        pass


def log(words, level='info'):
    try:
        A.log('insights: %s' % words, level)
    except Exception:                                   # noqa: BLE001
        pass


def sdir():
    d = Path(str(A.STATE_DIR)) / 'insights'
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    return d


def jread(name, default=None):
    try:
        return json.loads((sdir() / name).read_text())
    except (OSError, ValueError):
        return default


def jwrite(name, data):
    p = sdir() / name
    tmp = p.with_suffix('.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str))
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# One job at a time, followed by the pages
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
JOB = {'running': False, 'kind': '', 'what': '', 'says': '', 'done': 0, 'whole': 0, 'error': '',
       'result': None, 'began': 0.0, 'ended': 0.0, 'by': '', 'stop': False}


class Stopped(Exception):
    pass


def job_view():
    with _LOCK:
        j = dict(JOB)
    j.pop('stop', None)
    return j


def job_start(kind, what, by, fn, heavy=False):
    """`heavy`: reads a copy of Plex's database - in the slot of Settings → The database
    and Library integrity, never beside them."""
    with _LOCK:
        if JOB['running']:
            raise ValueError('%s is under way - wait for it' % (JOB['what'] or 'something'))
        JOB.update(running=True, kind=kind, what=what, says='starting', done=0, whole=0,
                   error='', result=None, began=time.time(), ended=0.0, by=by, stop=False)

    def go():
        try:
            JOB['result'] = fn()
        except Stopped:
            JOB['error'] = ''
            JOB['says'] = 'stopped'
        except Exception as e:                          # noqa: BLE001
            JOB['error'] = str(e)[:400] or e.__class__.__name__
            log('%s: %s' % (what, e), 'warn')
        finally:
            with _LOCK:
                JOB.update(running=False, ended=time.time())
                if JOB['says'] != 'stopped':
                    JOB['says'] = 'failed' if JOB['error'] else 'done'
            return JOB.get('result')
    if heavy:
        try:
            HX.db_job('insights: %s' % what, by, go)
        except ValueError:
            with _LOCK:
                JOB.update(running=False, ended=time.time())
            raise
    else:
        threading.Thread(target=go, name='plexmon-insights', daemon=True).start()


def says(words, done=None, whole=None):
    JOB['says'] = words
    if done is not None:
        JOB['done'] = done
    if whole is not None:
        JOB['whole'] = whole


def check_stop():
    if JOB.get('stop'):
        raise Stopped()


def job_stop():
    if JOB['running']:
        JOB['stop'] = True
        return True
    return False


# ---------------------------------------------------------------------------
# 4 and 5: what is watched, and how - a look at Plex's sessions once a minute
# ---------------------------------------------------------------------------
PLAYS_SQL = (
    'CREATE TABLE IF NOT EXISTS plays (id INTEGER PRIMARY KEY AUTOINCREMENT, skey TEXT, '
    'rk TEXT, kind TEXT, title TEXT, show TEXT, season INTEGER, ep INTEGER, section TEXT, '
    'user TEXT, player TEXT, product TEXT, platform TEXT, local INTEGER, started REAL, '
    'last REAL, watched REAL DEFAULT 0, duration REAL, offset REAL, decision TEXT, vdec TEXT, '
    'adec TEXT, sdec TEXT, vcodec TEXT, acodec TEXT, container TEXT, res TEXT, bitrate INTEGER, '
    'bandwidth INTEGER, hw INTEGER, reasons TEXT, file TEXT)',
    'CREATE INDEX IF NOT EXISTS plays_started ON plays (started)',
    'CREATE INDEX IF NOT EXISTS plays_skey ON plays (skey)',
    'CREATE TABLE IF NOT EXISTS filecheck (part TEXT PRIMARY KEY, file TEXT, title TEXT, '
    'section TEXT, added REAL, checked REAL, state TEXT, said TEXT, bytes INTEGER, '
    'duration REAL)',
    'CREATE INDEX IF NOT EXISTS filecheck_checked ON filecheck (checked)',
)
_TABLES = {'done': False}
LIVE = {'at': 0.0, 'sessions': []}
PLAY_COLS = ('rk', 'kind', 'title', 'show', 'season', 'ep', 'section', 'user', 'player',
             'product', 'platform', 'local', 'duration', 'offset', 'decision', 'vdec', 'adec',
             'sdec', 'vcodec', 'acodec', 'container', 'res', 'bitrate', 'bandwidth', 'hw',
             'reasons', 'file')
RANK = {'direct play': 0, 'direct stream': 1, 'transcode': 2}


def tables(store):
    if _TABLES['done']:
        return
    for sql in PLAYS_SQL:
        store.db.run(sql)
    _TABLES['done'] = True


def _session(m):
    user = (m.get('User') or {}).get('title') or '?'
    pl = m.get('Player') or {}
    se = m.get('Session') or {}
    ts = m.get('TranscodeSession') or {}
    media = (m.get('Media') or [{}])[0]
    part = (media.get('Part') or [{}])[0]
    streams = part.get('Stream') or []
    vdec = str(ts.get('videoDecision') or ('' if ts else 'directplay')).lower()
    adec = str(ts.get('audioDecision') or ('' if ts else 'directplay')).lower()
    sdec = ''
    sub_codec = ''
    for s in streams:
        if int(s.get('streamType') or 0) == 3 and (s.get('selected') or s.get('decision')):
            sdec = str(s.get('decision') or '').lower()
            sub_codec = str(s.get('codec') or '').lower()
    if not ts:
        decision = 'direct play'
    elif vdec == 'transcode':
        decision = 'transcode'
    else:
        decision = 'direct stream'
    reasons = []
    vcodec = str(media.get('videoCodec') or ts.get('sourceVideoCodec') or '').lower()
    acodec = str(media.get('audioCodec') or ts.get('sourceAudioCodec') or '').lower()
    res = str(media.get('videoResolution') or '')
    if vdec == 'transcode':
        reasons.append('video %s %s' % (vcodec or '?', res))
    if adec == 'transcode':
        reasons.append('audio %s' % (acodec or '?'))
    if sdec in ('burn', 'transcode'):
        reasons.append('subtitles %s %s' % ('burned in' if sdec == 'burn' else 'converted',
                                            sub_codec or ''))
    if ts and vdec != 'transcode' and adec != 'transcode' and not reasons:
        reasons.append('container %s' % (media.get('container') or '?'))
    kind = m.get('type') or ''
    return {
        'key': '%s|%s|%s' % (m.get('sessionKey') or se.get('id') or '', m.get('ratingKey'), user),
        'rk': str(m.get('ratingKey') or ''), 'kind': kind,
        'title': m.get('title') or '', 'show': m.get('grandparentTitle') or '',
        'season': m.get('parentIndex'), 'ep': m.get('index'),
        'section': str(m.get('librarySectionTitle') or m.get('librarySectionID') or ''),
        'user': user, 'player': pl.get('title') or '', 'product': pl.get('product') or '',
        'platform': pl.get('platform') or '', 'local': 1 if pl.get('local') else 0,
        'state': pl.get('state') or '', 'duration': (m.get('duration') or 0) / 1000.0,
        'offset': (m.get('viewOffset') or 0) / 1000.0, 'decision': decision,
        'vdec': vdec, 'adec': adec, 'sdec': sdec, 'vcodec': vcodec, 'acodec': acodec,
        'container': str(media.get('container') or ''), 'res': res,
        'bitrate': int(media.get('bitrate') or 0), 'bandwidth': int(se.get('bandwidth') or 0),
        'hw': 1 if ts.get('transcodeHwRequested') or ts.get('transcodeHwFullPipeline') else 0,
        'reasons': '; '.join(reasons), 'file': part.get('file') or '',
        'progress': ts.get('progress'), 'speed': ts.get('speed'),
        'thumb': m.get('grandparentThumb') or m.get('thumb') or ''}


def sample(store):
    tables(store)
    try:
        got = PX.mc(PX.get('/status/sessions', timeout=10)).get('Metadata') or []
    except Exception:                                   # noqa: BLE001
        LIVE.update(at=time.time(), sessions=[], error=True)
        return
    now = time.time()
    live = []
    for m in got:
        if (m.get('type') or '') in ('photo', 'clip'):
            continue
        s = _session(m)
        live.append(s)
        row = store.db.row('SELECT id, last, watched, decision FROM plays WHERE skey = ? '
                           'ORDER BY id DESC LIMIT 1', (s['key'],))
        playing = s['state'] == 'playing'
        if row and now - float(row['last'] or 0) < 900:
            add = min(now - float(row['last'] or now), 120.0) if playing else 0.0
            dec = s['decision'] if RANK.get(s['decision'], 0) >= RANK.get(row['decision'], 0) \
                else row['decision']
            store.db.run('UPDATE plays SET last = ?, watched = watched + ?, offset = ?, '
                         'decision = ?, vdec = ?, adec = ?, sdec = ?, bandwidth = ?, hw = ?, '
                         'reasons = CASE WHEN ? != \'\' THEN ? ELSE reasons END WHERE id = ?',
                         (now, add, s['offset'], dec, s['vdec'], s['adec'], s['sdec'],
                          s['bandwidth'], s['hw'], s['reasons'], s['reasons'], row['id']))
        else:
            row = dict(skey=s['key'], started=now, last=now, watched=0.0)
            row.update({k: s[k] for k in PLAY_COLS})
            store.db.insert('plays', row)
    LIVE.update(at=now, sessions=live, error=False)


def prune(store):
    tables(store)
    store.db.run('DELETE FROM plays WHERE started < ?', (time.time() - 400 * 86400,))
    store.db.run('DELETE FROM filecheck WHERE checked IS NOT NULL AND checked < ?',
                 (time.time() - 120 * 86400,))


_HIST = {'at': 0.0, 'days': 0, 'data': None}


def plex_history(days):
    """Plex's own history of what was watched, with the names of its accounts."""
    if _HIST['data'] is not None and _HIST['days'] >= days and time.time() - _HIST['at'] < 300:
        return _HIST['data']
    since = int(time.time() - days * 86400)
    accounts = {}
    try:
        for a in PX.mc(PX.get('/accounts', timeout=20)).get('Account') or []:
            accounts[str(a.get('id'))] = a.get('name') or ''
    except Exception:                                   # noqa: BLE001
        pass
    rows = []
    start = 0
    while start < 20000:
        got = PX.mc(PX.get('/status/sessions/history/all', {
            'sort': 'viewedAt:desc', 'viewedAt>': since, 'X-Plex-Container-Start': start,
            'X-Plex-Container-Size': 1000}, timeout=60))
        md = got.get('Metadata') or []
        for m in md:
            if int(m.get('viewedAt') or 0) < since:
                continue
            rows.append({'at': int(m.get('viewedAt') or 0), 'type': m.get('type') or '',
                         'title': m.get('title') or '', 'show': m.get('grandparentTitle') or '',
                         'season': m.get('parentIndex'), 'ep': m.get('index'),
                         'user': accounts.get(str(m.get('accountID')), '') or
                         ('#%s' % m.get('accountID')), 'rk': str(m.get('ratingKey') or ''),
                         'section': str(m.get('librarySectionID') or '')})
        if len(md) < 1000 or (md and int(md[-1].get('viewedAt') or 0) < since):
            break
        start += 1000
    _HIST.update(at=time.time(), days=days, data=rows)
    return rows


def watching(store, days=30):
    tables(store)
    days = max(1, min(int(days or 30), 400))
    since = time.time() - days * 86400
    out = {'days': days, 'now': time.time(), 'live': LIVE.get('sessions') or [],
           'live_at': LIVE.get('at'), 'error': ''}
    try:
        hist = plex_history(days)
    except Exception as e:                              # noqa: BLE001
        hist = []
        out['error'] = 'Plex\'s history could not be read: %s' % e
    per_day = Counter()
    for h in hist:
        per_day[datetime.fromtimestamp(h['at']).strftime('%Y-%m-%d')] += 1
    plays = store.db.rows('SELECT * FROM plays WHERE started >= ? ORDER BY started DESC',
                          (since,))
    dec_day = defaultdict(Counter)
    hours_day = Counter()
    for p in plays:
        d = datetime.fromtimestamp(p['started']).strftime('%Y-%m-%d')
        dec_day[d][p['decision'] or 'direct play'] += 1
        hours_day[d] += (p['watched'] or 0) / 3600.0
    days_list = [(date.today() - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    out['series'] = [{'day': d, 'plays': per_day.get(d, 0),
                      'direct': dec_day[d].get('direct play', 0),
                      'stream': dec_day[d].get('direct stream', 0),
                      'transcode': dec_day[d].get('transcode', 0),
                      'hours': round(hours_day.get(d, 0), 2)} for d in days_list]
    titles = Counter()
    kinds = {}
    for h in hist:
        t = h['show'] or h['title']
        titles[t] += 1
        kinds[t] = 'show' if h['show'] else (h['type'] or 'movie')
    out['top'] = [{'title': t, 'plays': n, 'kind': kinds.get(t)} for t, n in titles.most_common(12)]
    users = defaultdict(lambda: {'plays': 0, 'hours': 0.0, 'transcode': 0, 'last': 0})
    for h in hist:
        u = users[h['user']]
        u['plays'] += 1
        u['last'] = max(u['last'], h['at'])
    for p in plays:
        u = users[p['user']]
        u['hours'] += (p['watched'] or 0) / 3600.0
        if p['decision'] == 'transcode':
            u['transcode'] += 1
        u['last'] = max(u['last'], p['last'] or 0)
    out['users'] = sorted(({'user': k, 'plays': v['plays'], 'hours': round(v['hours'], 1),
                            'transcode': v['transcode'], 'last': v['last']}
                           for k, v in users.items()), key=lambda x: (-x['plays'], -x['hours']))
    dec = Counter(p['decision'] or 'direct play' for p in plays)
    players = defaultdict(Counter)
    for p in plays:
        players['%s · %s' % (p['product'] or '?', p['platform'] or '?')][p['decision']] += 1
    out['players'] = sorted(({'player': k, 'direct': v.get('direct play', 0),
                              'stream': v.get('direct stream', 0),
                              'transcode': v.get('transcode', 0)} for k, v in players.items()),
                            key=lambda x: -(x['direct'] + x['stream'] + x['transcode']))[:12]
    out['totals'] = {'plays': len(hist), 'sampled': len(plays),
                     'hours': round(sum((p['watched'] or 0) for p in plays) / 3600.0, 1),
                     'direct': dec.get('direct play', 0), 'stream': dec.get('direct stream', 0),
                     'transcode': dec.get('transcode', 0),
                     'users': len([u for u in out['users'] if u['plays'] or u['hours']]),
                     'remote': sum(1 for p in plays if not p['local'])}
    out['recent'] = [{k: p[k] for k in ('title', 'show', 'season', 'ep', 'user', 'player',
                                        'product', 'decision', 'reasons', 'started', 'watched',
                                        'local', 'res', 'vcodec', 'acodec')}
                     for p in plays[:40]]
    tc = defaultdict(lambda: {'n': 0, 'reasons': Counter(), 'users': set()})
    for p in plays:
        if p['decision'] != 'transcode' and not (p['decision'] == 'direct stream' and p['reasons']):
            continue
        name = ('%s S%02dE%02d' % (p['show'], p['season'] or 0, p['ep'] or 0)) if p['show'] \
            else p['title']
        x = tc[name]
        x['n'] += 1
        for r in (p['reasons'] or '').split('; '):
            if r:
                x['reasons'][re.sub(r'\s+', ' ', r).strip()] += 1
        x['users'].add(p['product'] or p['player'] or '')
        x['file'] = p['file']
    out['transcoded'] = sorted(({'title': k, 'n': v['n'], 'file': v.get('file'),
                                 'reasons': [r for r, _ in v['reasons'].most_common(4)],
                                 'players': sorted(x for x in v['users'] if x)[:4]}
                                for k, v in tc.items()), key=lambda x: -x['n'])[:40]
    why = Counter()
    for p in plays:
        for r in (p['reasons'] or '').split('; '):
            if r and p['decision'] != 'direct play':
                why[r.split(' ')[0] + ' ' + (r.split(' ')[1] if len(r.split(' ')) > 1 else '')] += 1
    out['why'] = [{'what': k.strip(), 'n': n} for k, n in why.most_common(10)]
    return out


# ---------------------------------------------------------------------------
# 5 (the library), 9 and 11: one look through the files of the library
# ---------------------------------------------------------------------------
def want_langs(key):
    out = set()
    for w in re.split(r'[\s,;]+', str(cfg(key, '') or '').lower()):
        if w:
            out.update(LANGS.get(w, (w,)))
    return out


def analyse(store, by):
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=str(HX.plex_dir()), prefix='insights-') as tmp:
        says('a copy of Plex\'s database (Plex goes on)', 0, 5)
        path, size = IX.snapshot(tmp)
        check_stop()
        says('what is in the library', 1, 5)
        c = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        try:
            secs = {r[0]: {'id': r[0], 'name': r[1], 'type': r[2]} for r in c.execute(
                'SELECT id, name, section_type FROM library_sections')}
            md = {}
            mcols = [x[1] for x in c.execute('PRAGMA table_info(metadata_items)')]
            for r in c.execute('SELECT id, metadata_type, title, parent_id, "index", '
                               'library_section_id, guid FROM metadata_items WHERE metadata_type '
                               'IN (1, 2, 3, 4)%s' % (' AND deleted_at IS NULL'
                                                     if 'deleted_at' in mcols else '')):
                md[r[0]] = r
            cols = [x[1] for x in c.execute('PRAGMA table_info(media_items)')]
            want = [x for x in ('width', 'height', 'bitrate', 'container', 'video_codec',
                                'audio_codec', 'size', 'duration') if x in cols]
            items = {}
            for r in c.execute('SELECT id, metadata_item_id, %s FROM media_items%s' % (
                    ', '.join(want), ' WHERE deleted_at IS NULL' if 'deleted_at' in cols else '')):
                items[r[0]] = dict(zip(['id', 'md'] + want, r))
            check_stop()
            says('the files and their sizes', 2, 5)
            pcols = [x[1] for x in c.execute('PRAGMA table_info(media_parts)')]
            for r in c.execute('SELECT media_item_id, file, %s FROM media_parts' % (
                    'size' if 'size' in pcols else '0')):
                it = items.get(r[0])
                if it is not None:
                    it.setdefault('files', []).append(r[1] or '')
                    it['psize'] = (it.get('psize') or 0) + int(r[2] or 0)
            says('the audio and subtitle tracks', 3, 5)
            scols = [x[1] for x in c.execute('PRAGMA table_info(media_streams)')]
            audio, subs, subcodec = defaultdict(set), defaultdict(set), defaultdict(set)
            if all(x in scols for x in ('stream_type_id', 'codec', 'language')):
                for r in c.execute('SELECT media_item_id, stream_type_id, codec, language FROM '
                                   'media_streams WHERE stream_type_id IN (2, 3)'):
                    if r[1] == 2:
                        audio[r[0]].add((r[3] or '').lower())
                    else:
                        subs[r[0]].add((r[3] or '').lower())
                        subcodec[r[0]].add((r[2] or '').lower())
        finally:
            c.close()
    check_stop()
    says('worked out', 4, 5)

    def name_of(mid):
        r = md.get(mid)
        if not r:
            return {'title': '?', 'show': '', 's': None, 'e': None, 'section': None, 'kind': ''}
        if r[1] == 4:
            season = md.get(r[3])
            show = md.get(season[3]) if season else None
            return {'title': r[2] or '', 'show': (show[2] if show else '') or '',
                    'show_id': show[0] if show else None,
                    's': season[4] if season else None, 'e': r[4], 'section': r[5],
                    'kind': 'episode'}
        return {'title': r[2] or '', 'show': '', 's': None, 'e': None, 'section': r[5],
                'kind': 'movie' if r[1] == 1 else 'other'}

    # 5: what is likely to be transcoded
    risk = {'video': Counter(), 'uhd': 0, 'bitrate': 0, 'audio': Counter(), 'subs': Counter(),
            'container': Counter()}
    risky = []
    for it in items.values():
        reasons = []
        vc = str(it.get('video_codec') or '').lower()
        ac = str(it.get('audio_codec') or '').lower()
        h = int(it.get('height') or 0)
        w = int(it.get('width') or 0)
        br = int(it.get('bitrate') or 0)
        if vc in VIDEO_HEAVY:
            risk['video'][VIDEO_HEAVY[vc]] += 1
            reasons.append(VIDEO_HEAVY[vc])
        if h >= 1800 or w >= 3200:
            risk['uhd'] += 1
            reasons.append('4K')
        if br > 40000:
            risk['bitrate'] += 1
            reasons.append('%d Mb/s' % (br // 1000))
        if ac in AUDIO_HEAVY:
            risk['audio'][ac.upper().replace('DCA', 'DTS')] += 1
            reasons.append(ac.upper().replace('DCA', 'DTS'))
        sc = subcodec.get(it['id']) or set()
        img = sorted(sc & SUB_IMAGE)
        if img:
            risk['subs'][img[0].upper()] += 1
            reasons.append('subtitles %s' % img[0].upper())
        cont = str(it.get('container') or '').lower()
        if cont in OLD_CONTAINERS:
            risk['container'][cont.upper()] += 1
            reasons.append(cont.upper())
        if reasons:
            n = name_of(it['md'])
            risky.append(dict(n, reasons=reasons, score=len(reasons) + (2 if '4K' in reasons else 0),
                              file=(it.get('files') or [''])[0]))
    risky.sort(key=lambda x: (-x['score'], x['show'] or x['title']))
    # 9: subtitles and audio
    ws, wa = want_langs('ins_sub_langs'), want_langs('ins_audio_langs')
    by_show = defaultdict(lambda: {'n': 0, 'subs': 0, 'audio': 0, 'miss_subs': [], 'miss_audio': [],
                                   'section': None})
    movies_miss = []
    mcount = {'n': 0, 'subs': 0, 'audio': 0}
    seen_md = set()
    for it in items.values():
        if it['md'] in seen_md:
            continue
        seen_md.add(it['md'])
        n = name_of(it['md'])
        if n['kind'] not in ('episode', 'movie'):
            continue
        langs_s = subs.get(it['id']) or set()
        langs_a = audio.get(it['id']) or set()
        has_s = bool(langs_s & ws) if ws else True
        has_a = bool(langs_a & wa) if wa else True
        if n['kind'] == 'episode':
            g = by_show[n['show']]
            g['n'] += 1
            g['section'] = n['section']
            g['subs'] += 1 if has_s else 0
            g['audio'] += 1 if has_a else 0
            if not has_s and len(g['miss_subs']) < 60:
                g['miss_subs'].append([n['s'], n['e']])
            if not has_a and len(g['miss_audio']) < 60:
                g['miss_audio'].append([n['s'], n['e']])
        else:
            mcount['n'] += 1
            mcount['subs'] += 1 if has_s else 0
            mcount['audio'] += 1 if has_a else 0
            if (not has_s or not has_a) and len(movies_miss) < ROW_CAP:
                movies_miss.append({'title': n['title'], 'subs': has_s, 'audio': has_a,
                                    'have_subs': sorted(x for x in langs_s if x)[:8],
                                    'have_audio': sorted(x for x in langs_a if x)[:8]})
    shows = sorted(({'show': k, **v} for k, v in by_show.items() if v['n']),
                   key=lambda x: (x['subs'] / max(1, x['n']), x['show']))
    # 11: duplicates
    per_md = defaultdict(list)
    for it in items.values():
        per_md[it['md']].append(it)
    dups = []
    for mid, its in per_md.items():
        if len(its) < 2:
            continue
        n = name_of(mid)
        vers = []
        for it in its:
            vers.append({'res': '%sx%s' % (it.get('width') or '?', it.get('height') or '?'),
                         'height': int(it.get('height') or 0), 'codec': it.get('video_codec') or '',
                         'audio': it.get('audio_codec') or '', 'bitrate': int(it.get('bitrate') or 0),
                         'bytes': int(it.get('size') or it.get('psize') or 0),
                         'file': (it.get('files') or [''])[0]})
        best = max(range(len(vers)), key=lambda i: (vers[i]['height'], vers[i]['bitrate']))
        for i, v in enumerate(vers):
            v['keep'] = i == best
        dups.append(dict(n, versions=vers, spare=sum(v['bytes'] for v in vers if not v['keep'])))
    by_guid = defaultdict(list)
    for r in md.values():
        if r[1] == 1 and r[6] and not str(r[6]).startswith('local://'):
            by_guid[(r[5], r[6])].append(r[0])
    twins = []
    for (sec, guid), ids in by_guid.items():
        if len(ids) > 1:
            twins.append({'guid': guid, 'section': sec, 'items': [
                {'id': i, 'title': md[i][2], 'bytes': sum(int(it.get('size') or it.get('psize')
                                                              or 0) for it in per_md.get(i, [])),
                 'file': ((per_md.get(i) or [{}])[0].get('files') or [''])[0]} for i in ids]})
    dups.sort(key=lambda x: -x['spare'])
    out = {'at': time.time(), 'by': by, 'took': round(time.time() - t0, 1), 'db_bytes': size,
           'sections': list(secs.values()), 'items': len(items),
           'risk': {'video': dict(risk['video']), 'uhd': risk['uhd'], 'bitrate': risk['bitrate'],
                    'audio': dict(risk['audio']), 'subs': dict(risk['subs']),
                    'container': dict(risk['container']), 'count': len(risky)},
           'risky': risky[:ROW_CAP],
           'langs': {'subs': sorted(ws), 'audio': sorted(wa), 'want_subs': cfg('ins_sub_langs', ''),
                     'want_audio': cfg('ins_audio_langs', ''), 'movies': mcount,
                     'shows': shows[:ROW_CAP], 'movies_missing': movies_miss,
                     'episodes': {'n': sum(s['n'] for s in shows), 'subs': sum(s['subs'] for s in shows),
                                  'audio': sum(s['audio'] for s in shows)}},
           'dups': dups[:ROW_CAP], 'twins': twins[:ROW_CAP],
           'dup_spare': sum(d['spare'] for d in dups), 'dup_count': len(dups)}
    jwrite('library.json', out)
    ev(store, 'plex', '%s looked through the files of the library: %d files, %d likely to be '
             'transcoded, %d in more than one version' % (by, len(items), len(risky), len(dups)),
             who=by)
    says('done', 5, 5)
    return {'items': len(items)}


# ---------------------------------------------------------------------------
# 8: missing and coming episodes - TVmaze
# ---------------------------------------------------------------------------
def episodes(store, by):
    if not int(cfg('plex_tvmaze', 1) or 0):
        raise ValueError('TVmaze is switched off (Settings → Plex library)')
    t0 = time.time()
    says('the shows of Plex', 0, 1)
    with tempfile.TemporaryDirectory(dir=str(HX.plex_dir()), prefix='insights-') as tmp:
        path, _ = IX.snapshot(tmp)
        c = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        try:
            have = defaultdict(set)
            for r in c.execute('SELECT show.id, season."index", ep."index" FROM metadata_items ep '
                               'JOIN metadata_items season ON season.id = ep.parent_id '
                               'JOIN metadata_items show ON show.id = season.parent_id '
                               'WHERE ep.metadata_type = 4'):
                have[r[0]].add((r[1], r[2]))
        finally:
            c.close()
    shows = []
    for d in PX.mc(PX.get('/library/sections')).get('Directory') or []:
        if d.get('type') != 'show':
            continue
        got = PX.mc(PX.get('/library/sections/%s/all' % d.get('key'), {
            'type': 2, 'includeGuids': 1, 'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': 100000}, timeout=120))
        for m in got.get('Metadata') or []:
            shows.append({'id': int(m.get('ratingKey')), 'title': m.get('title') or '',
                          'section': d.get('title'), 'ids': PX.ext_ids(m),
                          'thumb': m.get('thumb') or ''})
    cache = jread('tvmaze-cache.json', {}) or {}
    tv = PX._TVmaze()
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=60)).isoformat()
    out_shows, upcoming, unknown = [], [], []
    for i, sh in enumerate(shows):
        check_stop()
        says('TVmaze: %s' % sh['title'], i, len(shows))
        ck = 'p%s' % sh['id']
        ent = cache.get(ck)
        fresh = ent and time.time() - ent.get('at', 0) < (86400 if ent.get('status') != 'Ended'
                                                          else 7 * 86400)
        if not fresh:
            try:
                tid = tv._id(sh['ids'])
            except Exception:                           # noqa: BLE001
                tid = None
            if not tid:
                ent = {'at': time.time(), 'tid': None}
            else:
                info = tv._get('/shows/%s' % tid) or {}
                eps = tv._get('/shows/%s/episodes' % tid) or []
                ent = {'at': time.time(), 'tid': tid, 'name': info.get('name'),
                       'status': info.get('status'), 'url': info.get('url'),
                       'network': ((info.get('network') or info.get('webChannel') or {})
                                   .get('name')),
                       'eps': [[e.get('season'), e.get('number'), e.get('name') or '',
                                e.get('airdate') or ''] for e in eps
                               if isinstance(e, dict) and e.get('number')]}
            cache[ck] = ent
        if not ent.get('tid'):
            unknown.append({'title': sh['title'], 'section': sh['section']})
            continue
        h = have.get(sh['id']) or set()
        aired = [e for e in ent['eps'] if e[3] and e[3] <= today and (e[0] or 0) > 0]
        missing = [e for e in aired if (e[0], e[1]) not in h]
        nxt = [e for e in ent['eps'] if e[3] and e[3] > today]
        for e in nxt:
            if e[3] <= soon:
                upcoming.append({'show': sh['title'], 's': e[0], 'e': e[1], 'name': e[2],
                                 'date': e[3], 'have_prev': bool(h)})
        out_shows.append({'title': sh['title'], 'section': sh['section'], 'status': ent.get('status'),
                          'network': ent.get('network'), 'url': ent.get('url'),
                          'aired': len(aired), 'have': len([e for e in aired if (e[0], e[1]) in h]),
                          'missing': missing[:80], 'n_missing': len(missing),
                          'next': nxt[0] if nxt else None,
                          'last': aired[-1] if aired else None})
        if i % 20 == 0:
            jwrite('tvmaze-cache.json', cache)
    jwrite('tvmaze-cache.json', cache)
    out_shows.sort(key=lambda x: (-x['n_missing'], x['title']))
    upcoming.sort(key=lambda x: (x['date'], x['show']))
    res = {'at': time.time(), 'by': by, 'took': round(time.time() - t0, 1), 'shows': out_shows,
           'upcoming': upcoming[:200], 'unknown': unknown[:ROW_CAP],
           'totals': {'shows': len(out_shows), 'missing': sum(s['n_missing'] for s in out_shows),
                      'complete': sum(1 for s in out_shows if not s['n_missing']),
                      'running': sum(1 for s in out_shows if s['status'] == 'Running'),
                      'unknown': len(unknown)}}
    jwrite('episodes.json', res)
    ev(store, 'plex', '%s looked up the episodes on TVmaze: %d shows, %d aired episodes '
             'missing, %d coming in 60 days' % (by, len(out_shows), res['totals']['missing'],
                                                 len(upcoming)), who=by)
    says('done', 1, 1)
    return res['totals']


# ---------------------------------------------------------------------------
# 10: each new file read once by ffprobe - only on a disk that is awake
# ---------------------------------------------------------------------------
def newfiles(store, by='plexmon', limit=20):
    tables(store)
    if not shutil.which('ffprobe'):
        put_state('newfiles', {'at': time.time(), 'error': 'ffprobe is not on this machine '
                               '(dnf install ffmpeg)'})
        return {'checked': 0}
    found = []
    for d in PX.mc(PX.get('/library/sections')).get('Directory') or []:
        kind = {'movie': 1, 'show': 4}.get(d.get('type'))
        if not kind:
            continue
        got = PX.mc(PX.get('/library/sections/%s/all' % d.get('key'), {
            'type': kind, 'sort': 'addedAt:desc', 'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': 60}, timeout=60))
        for m in got.get('Metadata') or []:
            for med in m.get('Media') or []:
                for p in med.get('Part') or []:
                    found.append({'part': str(p.get('id')), 'file': p.get('file') or '',
                                  'title': ('%s S%02dE%02d' % (m.get('grandparentTitle'),
                                                               int(m.get('parentIndex') or 0),
                                                               int(m.get('index') or 0)))
                                  if m.get('type') == 'episode' else (m.get('title') or ''),
                                  'section': d.get('title') or '', 'added': m.get('addedAt'),
                                  'bytes': p.get('size'),
                                  'duration': (p.get('duration') or med.get('duration') or 0)
                                  / 1000.0})
    found.sort(key=lambda x: -(x['added'] or 0))
    known = {r['part']: r for r in store.db.rows('SELECT part, state FROM filecheck')}
    checked = 0
    bad = []
    power = IX.disk_power(CTX['monitor'])
    for f in found:
        k = known.get(f['part'])
        if k and k['state'] not in ('waiting', 'busy'):
            continue
        if checked >= limit:
            break
        st = IX.root_state(os.path.dirname(f['file']) or '/', CTX['monitor'], None, power)
        if st['state'] == 'asleep':
            state, said = 'waiting', 'its disk sleeps - read when it is awake'
        elif st['state'] != 'awake' and st['state'] != 'missing':
            # Never a disk plexmon cannot tell is awake: the read could spin it up.
            state, said = 'waiting', 'not known whether its disk sleeps - left alone'
        elif st['state'] == 'missing' or not os.path.exists(f['file']):
            state, said = 'gone', 'the file is not there'
        else:
            try:
                age = time.time() - os.stat(f['file']).st_mtime
            except OSError:
                age = 1e9
            if age < 600:
                state, said = 'busy', 'written less than ten minutes ago - read later'
            else:
                state, said = probe(f['file'], f['duration'])
                checked += 1
        store.db.run('INSERT OR REPLACE INTO filecheck (part, file, title, section, added, '
                     'checked, state, said, bytes, duration) VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (f['part'], f['file'], f['title'], f['section'], f['added'], time.time(),
                      state, said, f['bytes'], f['duration']))
        if state in ('damaged', 'short'):
            bad.append('%s (%s): %s' % (f['title'], f['file'], said))
            ev(store, 'plex', 'a new file does not read right: %s - %s' % (f['file'], said),
               'warn')
    if bad:
        notify('newfiles', 'New files that do not read right' if len(bad) > 1 else
               'A new file does not read right', '\n'.join(bad[:15]) +
               ('\n... and %d more' % (len(bad) - 15) if len(bad) > 15 else ''))
    put_state('newfiles', {'at': time.time(), 'checked': checked})
    return {'checked': checked}


def probe(path, plex_duration):
    try:
        got = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                              'format=duration:stream=codec_type,codec_name', '-of', 'json', path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except subprocess.TimeoutExpired:
        return 'damaged', 'ffprobe did not finish in 90 s'
    except OSError as e:
        return 'damaged', str(e)[:200]
    err = got.stderr.decode('utf-8', 'replace').strip()
    try:
        j = json.loads(got.stdout.decode('utf-8', 'replace') or '{}')
    except ValueError:
        j = {}
    if got.returncode != 0 or not j.get('streams'):
        return 'damaged', (err.splitlines()[0] if err else 'ffprobe could not read it')[:200]
    dur = float((j.get('format') or {}).get('duration') or 0)
    kinds = Counter(s.get('codec_type') for s in j.get('streams') or [])
    if not kinds.get('video'):
        return 'damaged', 'no video stream in it'
    if plex_duration and dur and dur < plex_duration * 0.9 and plex_duration - dur > 60:
        return 'short', 'shorter than Plex says: %d min of %d - not downloaded whole?' % (
            dur // 60, plex_duration // 60)
    words = ', '.join('%s %s' % (s.get('codec_type'), s.get('codec_name'))
                      for s in (j.get('streams') or [])[:4])
    if err:
        return 'warn', ('%s - %s' % (words, err.splitlines()[0]))[:200]
    return 'ok', ('%d min · %s' % (dur // 60, words))[:200]


def newfiles_view(store):
    tables(store)
    rows = store.db.rows('SELECT * FROM filecheck ORDER BY added DESC LIMIT 200')
    c = Counter(r['state'] for r in rows)
    return {'rows': rows, 'counts': dict(c), 'state': get_state('newfiles'),
            'ffprobe': bool(shutil.which('ffprobe')), 'on': bool(int(cfg('ins_newfiles', 1) or 0))}


# ---------------------------------------------------------------------------
# 6: Plex Media Server - a watchdog, its load, a new version
# ---------------------------------------------------------------------------
HEALTH = {'fails': 0, 'down_since': 0.0, 'alerted': False, 'last_restart': 0.0,
          'cpu': None, 'cpu_hist': [], 'rss': None, 'pid': None, 'started': None,
          'cpu_alert': 0.0, 'answers': None, 'active': None, 'version': '', 'prev_active': None,
          '_tick': None}


def plex_pid():
    rc, out = HX.host_run(['pgrep', '-f', r'/Plex Media Server( |$)'], 10)
    for w in out.split():
        if w.isdigit():
            return int(w)
    return None


def _proc_cpu(pid):
    try:
        with open('/proc/%d/stat' % pid) as f:
            f_ = f.read().rsplit(')', 1)[1].split()
        return (int(f_[11]) + int(f_[12])) / float(os.sysconf('SC_CLK_TCK')), int(f_[19])
    except (OSError, ValueError, IndexError):
        return None, None


def _rss(pid):
    try:
        with open('/proc/%d/status' % pid) as f:
            for ln in f:
                if ln.startswith('VmRSS:'):
                    return int(ln.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def watch_plex(store):
    now = time.time()
    active = A.plex_running()
    answers = False
    try:
        PX.get('/identity', timeout=10)
        answers = True
    except Exception:                                   # noqa: BLE001
        answers = False
    HEALTH.update(answers=answers, active=active)
    if answers:
        try:
            HEALTH['version'] = (PX.server_info() or {}).get('version') or HEALTH['version']
        except Exception:                               # noqa: BLE001
            pass
    mode = str(cfg('plex_watch', 'notify') or 'notify')
    planned = IX._marker().exists() if hasattr(IX, '_marker') else False
    busy = bool(HX.DB_JOB.get('running')) and 'restore' in str(HX.DB_JOB.get('what') or '')
    if active and not answers and not planned and not busy:
        HEALTH['fails'] += 1
        HEALTH['down_since'] = HEALTH['down_since'] or now
    else:
        if HEALTH['alerted'] and answers:
            ev(store, 'plex', 'Plex Media Server answers again', 'info')
            if mode != 'off':
                notify('plex', 'Plex Media Server answers again',
                       'after %d min' % ((now - HEALTH['down_since']) // 60))
        HEALTH.update(fails=0, down_since=0.0, alerted=False)
    limit = max(2, int(cfg('plex_watch_min', 5) or 5))
    if mode != 'off' and HEALTH['fails'] >= limit and not HEALTH['alerted']:
        HEALTH['alerted'] = True
        words = 'Plex Media Server runs, but has not answered for %d min' % HEALTH['fails']
        if mode == 'restart' and now - HEALTH['last_restart'] > 1800 and \
                not (A.maintenance_now(store) or {}).get('on'):
            HEALTH['last_restart'] = now
            rc, out = HX.host_run([A.SYSTEMCTL, 'restart', A.PLEX_SERVICE], 300)
            words += ' - restarted by plexmon%s' % ('' if rc == 0 else ' (it said: %s)' %
                                                  out.strip()[-160:])
            remember_restart(words)
        ev(store, 'plex', words, 'bad')
        notify('plex', 'Plex Media Server does not answer', words)
    # Stopped by itself: only after the time set, and never while something of plexmon (a
    # restore, a fix, an update of the system, maintenance) has it down on purpose.
    quiet = planned or busy or bool(HX.DB_JOB.get('running')) or \
        bool((A.maintenance_now(store) or {}).get('on')) or now - HEALTH['last_restart'] < 600
    if active or quiet:
        HEALTH['inactive_since'] = 0.0
        HEALTH['stop_said'] = False
    elif HEALTH['prev_active'] or HEALTH.get('inactive_since'):
        HEALTH['inactive_since'] = HEALTH.get('inactive_since') or now
        if now - HEALTH['inactive_since'] >= limit * 60 and not HEALTH.get('stop_said') and \
                mode != 'off':
            HEALTH['stop_said'] = True
            ev(store, 'plex', 'Plex Media Server has been stopped for %d min' % limit, 'warn')
            notify('plex', 'Plex Media Server stopped', 'its service has not run for %d min' % limit)
    HEALTH['prev_active'] = active
    pid = plex_pid() if active else None
    HEALTH['pid'] = pid
    if pid:
        cpu, start_ticks = _proc_cpu(pid)
        last = HEALTH.get('_tick')
        if cpu is not None and last and last[0] == pid and now > last[1]:
            pct = 100.0 * (cpu - last[2]) / (now - last[1]) / max(1, os.cpu_count() or 1)
            HEALTH['cpu'] = round(max(0.0, pct), 1)
            HEALTH['cpu_hist'] = (HEALTH['cpu_hist'] + [[now, HEALTH['cpu']]])[-180:]
        HEALTH['_tick'] = (pid, now, cpu or 0.0)
        HEALTH['rss'] = _rss(pid)
        try:
            with open('/proc/uptime') as f:
                up = float(f.read().split()[0])
            HEALTH['started'] = now - up + (start_ticks or 0) / float(os.sysconf('SC_CLK_TCK'))
        except (OSError, ValueError):
            pass
        hot = [c for t, c in HEALTH['cpu_hist'] if now - t < 15 * 60]
        if len(hot) >= 12 and min(hot) > 90 and not LIVE.get('sessions') and \
                not PX.activities() and now - HEALTH['cpu_alert'] > 6 * 3600 and mode != 'off':
            HEALTH['cpu_alert'] = now
            words = 'Plex Media Server has used over 90 % of the processor for 15 min with ' \
                    'nobody watching and nothing of its own running'
            ev(store, 'plex', words, 'warn')
            notify('plex', 'Plex Media Server is stuck?', words)


def remember_restart(words):
    h = jread('restarts.json', []) or []
    h.insert(0, {'at': time.time(), 'words': words})
    jwrite('restarts.json', h[:30])


def plex_package():
    """(installed, available) versions of plexmediaserver by rpm and dnf."""
    if HX.pm() != 'dnf':
        return None, None
    rc, out = HX.host_run(['rpm', '-q', '--qf', '%{VERSION}-%{RELEASE}', 'plexmediaserver'], 30)
    have = out.strip() if rc == 0 else None
    rc, out = HX.host_run(['dnf', '-q', 'check-update', '--refresh', 'plexmediaserver'], 600,
                          transient=True, name='plex-check', proxy=True)
    new = None
    if rc == 100:
        for ln in out.splitlines():
            f = ln.split()
            if len(f) >= 2 and f[0].startswith('plexmediaserver'):
                new = f[1]
    elif rc not in (0, 100):
        raise ValueError(out.strip()[-300:] or 'dnf check-update said %s' % rc)
    return have, new


def check_plex_update(store, by='plexmon'):
    have, new = plex_package()
    st = {'at': time.time(), 'have': have, 'new': new, 'by': by}
    old = get_state('plexup') or {}
    put_state('plexup', st)
    if new and new != old.get('new'):
        ev(store, 'plex', 'a new version of Plex Media Server is there: %s (this one: %s)' % (
            new, have), 'info')
        notify('update', 'A new Plex Media Server', '%s is there (this server: %s)' % (new, have))
    return st


def update_plex(store, by):
    if HX.pm() != 'dnf':
        raise ValueError('Plex Media Server is updated with dnf here - none on this machine')
    says('dnf upgrades plexmediaserver - Plex restarts', 1, 3)
    ev(store, 'plex', '%s updates Plex Media Server' % by, 'warn', who=by)
    rc, out = HX.host_run(['dnf', '-y', 'upgrade', '--refresh', 'plexmediaserver'], 1800,
                          transient=True, name='plex-update', proxy=True)
    if rc != 0:
        raise ValueError(out.strip()[-400:] or 'dnf said %s' % rc)
    says('waiting for Plex to answer again', 2, 3)
    IX.plex_answers(300)
    st = check_plex_update(store, by)
    ev(store, 'plex', '%s updated Plex Media Server: %s' % (by, st.get('have')), who=by)
    says('done', 3, 3)
    return st


def health_view(store):
    try:
        info = PX.server_info()
    except Exception:                                   # noqa: BLE001
        info = {}
    h = {k: v for k, v in HEALTH.items() if not k.startswith('_')}
    h['cpu_hist'] = HEALTH['cpu_hist'][-120:]
    return {'health': h, 'info': info, 'live': len(LIVE.get('sessions') or []),
            'transcodes': sum(1 for s in LIVE.get('sessions') or [] if s['decision'] == 'transcode'),
            'activities': PX.activities()[:6] if HEALTH.get('answers') else [],
            'package': get_state('plexup'), 'restarts': (jread('restarts.json', []) or [])[:10],
            'mode': cfg('plex_watch', 'notify'), 'minutes': cfg('plex_watch_min', 5),
            'dnf': HX.pm() == 'dnf', 'cleanup': get_state('cleanup')}


# ---------------------------------------------------------------------------
# 7: Plex's caches - what can go, never the library or its database
# ---------------------------------------------------------------------------
CLEAN = [
    ('Cache/PhotoTranscoder', 'Posters and pictures made smaller', 30,
     'made again by Plex when they are needed'),
    ('Cache/Transcode', 'Pieces left by transcoding', 1, 'only when nobody is watching'),
    ('Crash Reports', 'Crash reports', 0, 'kept for Plex\'s developers only'),
    ('Diagnostics', 'Diagnostics', 1, 'what Plex packed to send'),
    ('Updates', 'Downloaded installers of Plex', 14, 'the versions already put in'),
]


def _du(path, older=None):
    total, n, old_b, old_n = 0, 0, 0, 0
    now = time.time()
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                total += st.st_size
                n += 1
                if older is not None and now - st.st_mtime > older * 86400:
                    old_b += st.st_size
                    old_n += 1
    return total, n, old_b, old_n


def plex_sizes(store, by):
    base = HX.PLEX_DATA
    out = []
    names = []
    try:
        names = sorted(n for n in os.listdir(str(base)) if (base / n).is_dir())
    except OSError as e:
        raise ValueError('Plex\'s folder cannot be read: %s' % e)
    for i, n in enumerate(names):
        check_stop()
        says('the size of %s' % n, i, len(names) + len(CLEAN))
        b, c, _, _ = _du(str(base / n))
        out.append({'name': n, 'bytes': b, 'files': c})
    out.sort(key=lambda x: -x['bytes'])
    clean = []
    for j, (rel, title, days, why) in enumerate(CLEAN):
        says('what of %s can go' % rel, len(names) + j, len(names) + len(CLEAN))
        p = base / rel
        if not p.is_dir():
            continue
        b, c, ob, oc = _du(str(p), days)
        clean.append({'rel': rel, 'title': title, 'days': days, 'why': why, 'bytes': b,
                      'files': c, 'can': ob, 'can_files': oc})
    try:
        du = shutil.disk_usage(str(base))
        disk = {'total': du.total, 'free': du.free}
    except OSError:
        disk = None
    st = {'at': time.time(), 'by': by, 'dirs': out, 'clean': clean, 'disk': disk,
          'total': sum(x['bytes'] for x in out)}
    put_state('cleanup', st)
    says('done', 1, 1)
    return st


def plex_clean(store, by, which):
    base = Path(os.path.realpath(str(HX.PLEX_DATA)))
    freed, files = 0, 0
    live = [s for s in LIVE.get('sessions') or [] if s['decision'] != 'direct play']
    for rel, title, days, why in CLEAN:
        if rel not in which:
            continue
        if rel == 'Cache/Transcode' and live:
            raise ValueError('someone is watching with transcoding now - its pieces stay')
        p = Path(os.path.realpath(str(base / rel)))
        if not str(p).startswith(str(base) + '/') or not p.is_dir():
            continue
        now = time.time()
        for dp, dn, fn in os.walk(str(p), topdown=False, followlinks=False):
            check_stop()
            for f in fn:
                fp = os.path.join(dp, f)
                try:
                    st = os.lstat(fp)
                    if os.path.islink(fp) or now - st.st_mtime <= days * 86400:
                        continue
                    os.unlink(fp)
                    freed += st.st_size
                    files += 1
                except OSError:
                    continue
            if dp != str(p):
                try:
                    os.rmdir(dp)
                except OSError:
                    pass
        says('%s: %s given back' % (title, HX.human_bytes(freed)))
    ev(store, 'plex', '%s cleaned Plex\'s caches (%s): %s in %d files given back' % (
        by, ', '.join(which), HX.human_bytes(freed), files), who=by)
    plex_sizes(store, by)
    return {'freed': freed, 'files': files}


# ---------------------------------------------------------------------------
# 12: notifications - e-mail, Telegram, ntfy
# ---------------------------------------------------------------------------
CATS = {'disks': 'Disks (SMART, temperature, the dashboard\'s warnings)', 'plex': 'Plex Media Server',
        'backup': 'Backups and copies', 'integrity': 'Library integrity', 'cert': 'Certificates',
        'newfiles': 'New files', 'update': 'New versions'}
_SENT = {'keys': {}, 'hour': []}
_QUEUE = []
_QLOCK = threading.Lock()


def channels():
    out = []
    if int(cfg('notify_email', 0) or 0) and cfg('notify_smtp_host') and cfg('notify_to'):
        out.append('email')
    if int(cfg('notify_telegram', 0) or 0) and cfg('notify_tg_token') and cfg('notify_tg_chat'):
        out.append('telegram')
    if int(cfg('notify_ntfy', 0) or 0) and cfg('notify_ntfy_url'):
        out.append('ntfy')
    return out


def notify(cat, title, text, force=False):
    """Queued and sent in the background - once in six hours for the same thing, at most
    twenty an hour."""
    if not force and not int(cfg('notify_on_' + cat, 1) or 0):
        return
    if not channels():
        return
    key = '%s|%s|%s' % (cat, title, re.sub(r'\d+', '#', text)[:200])
    now = time.time()
    with _QLOCK:
        if not force and now - _SENT['keys'].get(key, 0) < 6 * 3600:
            return
        _SENT['hour'] = [t for t in _SENT['hour'] if now - t < 3600]
        if not force and len(_SENT['hour']) >= 20:
            return
        _SENT['keys'][key] = now
        _SENT['hour'].append(now)
        _QUEUE.append((cat, title, text))
    threading.Thread(target=_drain, name='plexmon-notify', daemon=True).start()


def _drain():
    while True:
        with _QLOCK:
            if not _QUEUE:
                return
            cat, title, text = _QUEUE.pop(0)
        send_all(cat, title, text)


def _site():
    return str(cfg('site_name') or '') or A.node_name()


def send_all(cat, title, text, only=None):
    res = {}
    for ch in only or channels():
        try:
            {'email': send_email, 'telegram': send_telegram, 'ntfy': send_ntfy}[ch](cat, title, text)
            res[ch] = 'sent'
        except Exception as e:                          # noqa: BLE001
            res[ch] = 'failed: %s' % str(e)[:200]
            log('a notification by %s failed: %s' % (ch, e), 'warn')
    h = jread('notified.json', []) or []
    h.insert(0, {'at': time.time(), 'cat': cat, 'title': title, 'text': text[:500], 'res': res})
    jwrite('notified.json', h[:60])
    return res


def _secret(k):
    v = cfg(k, '') or ''
    return (A.unseal_secret(v) or '') if v else ''


def send_email(cat, title, text):
    host = str(cfg('notify_smtp_host') or '')
    port = int(cfg('notify_smtp_port', 587) or 587)
    how = str(cfg('notify_smtp_tls', 'starttls') or 'starttls')
    msg = EmailMessage()
    msg['Subject'] = '[plexmon %s] %s' % (_site(), title)
    msg['From'] = str(cfg('notify_from') or cfg('notify_smtp_user') or 'plexmon@%s' % A.node_name())
    msg['To'] = str(cfg('notify_to'))
    msg.set_content('%s\n\n%s\n\n-- plexmon on %s (%s)\n' % (title, text, _site(), CATS.get(cat, cat)))
    ctx = ssl.create_default_context()
    if how == 'ssl':
        s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
    try:
        if how == 'starttls':
            s.starttls(context=ctx)
        user = str(cfg('notify_smtp_user') or '')
        if user:
            s.login(user, _secret('notify_smtp_password'))
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:                               # noqa: BLE001
            pass


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    with A.url_open(req, timeout=20, purpose='notify') as r:
        return r.status, r.read()[:400]


def send_telegram(cat, title, text):
    tok = _secret('notify_tg_token')
    body = json.dumps({'chat_id': str(cfg('notify_tg_chat')), 'disable_web_page_preview': True,
                       'text': '%s — %s\n%s' % (_site(), title, text)}).encode()
    st, got = _post('https://api.telegram.org/bot%s/sendMessage' % tok, body,
                    {'Content-Type': 'application/json'})
    if st >= 300:
        raise ValueError('Telegram said %s' % st)


def send_ntfy(cat, title, text):
    url = str(cfg('notify_ntfy_url') or '')
    t = '%s: %s' % (_site(), title)
    try:
        t.encode('ascii')
    except UnicodeEncodeError:
        t = '=?UTF-8?B?%s?=' % base64.b64encode(t.encode('utf-8')).decode('ascii')
    h = {'Title': t,
         'Tags': {'disks': 'floppy_disk', 'plex': 'tv', 'backup': 'package', 'integrity': 'books',
                  'cert': 'lock', 'newfiles': 'film_frames', 'update': 'arrow_up'}.get(cat, 'bell'),
         'Priority': 'high' if cat in ('disks', 'plex') else 'default'}
    tok = _secret('notify_ntfy_token')
    if tok:
        h['Authorization'] = 'Bearer %s' % tok
    st, got = _post(url, text.encode('utf-8'), h)
    if st >= 300:
        raise ValueError('ntfy said %s' % st)


def on_event(kind, words, level):
    """Events of plexmon that are worth a message."""
    k = str(kind or '').lower()
    if level not in ('bad', 'warn'):
        return
    if k in ('backup', 'rbackup', 'database') and level == 'bad':
        notify('backup', 'A backup or a copy failed', words)
    elif k in ('acme', 'web', 'certificates', 'tls', 'certificate'):
        notify('cert', 'Certificates', words)


_REASONS = {'last': None}


def watch_dashboard():
    """What the dashboard warns of (SMART, temperatures, space): a message when something
    new comes, one when all is well again."""
    mon = CTX.get('monitor')
    if not mon:
        return
    try:
        st = mon() or {}
    except Exception:                                   # noqa: BLE001
        return
    now = set(st.get('reasons') or [])
    last = _REASONS['last']
    _REASONS['last'] = now
    if last is None:
        return
    new = sorted(now - last)
    if new:
        notify('disks', 'The dashboard warns', '\n'.join(new))
    elif last and not now:
        notify('disks', 'All is well again', 'the dashboard has nothing to warn of')


# ---------------------------------------------------------------------------
# 13: Library integrity on a schedule; Plex's optimize once a month
# ---------------------------------------------------------------------------
def integrity_schedule(store):
    if not int(cfg('ix_auto', 0) or 0):
        return
    if IX.JOB.get('running') or HX.DB_JOB.get('running'):
        return
    if int(cfg('ix_auto_optimize', 0) or 0):
        day = HX.due('monthly', '', cfg('ix_auto_at', '02:30'), store.get_meta('ins:opt:day') or '')
        if day:
            store.set_meta('ins:opt:day', day)
            try:
                HX.db_job('Plex optimizes its database', 'plexmon (on a schedule)',
                          lambda: IX.plex_task(store, 'plexmon (on a schedule)', 'optimize'))
            except ValueError:
                pass
            return
    day = HX.due(cfg('ix_auto_every', 'daily'), cfg('ix_auto_day', '7'), cfg('ix_auto_at', '02:30'),
                 store.get_meta('ins:ix:day') or '')
    if not day:
        return
    store.set_meta('ins:ix:day', day)
    before = (IX.last_report() or {}).get('summary') or {}
    disks = str(cfg('ix_auto_mode', 'db')) == 'awake'
    by = 'plexmon (on a schedule)'

    def go():
        IX.scan(store, by, disks=disks, monitor=CTX['monitor'])
        after = (IX.last_report() or {}).get('summary') or {}
        bad = (after.get('safe') or 0) + (after.get('confirm') or 0)
        was = (before.get('safe') or 0) + (before.get('confirm') or 0)
        newer = [k for k, v in (after.get('counts') or {}).items()
                 if v > ((before.get('counts') or {}).get(k) or 0) and k in ('P1', 'P2', 'P5', 'P6',
                                                                            'P7')]
        if bad and (bad > was or newer):
            notify('integrity', 'The library needs putting right',
                   '%s (looked at by itself)' % after.get('words'))
        return after
    try:
        IX.job_start('scan', 'the library looked through', by, IX.SCAN_STEPS, go)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Once a minute
# ---------------------------------------------------------------------------
STATE = {}


def get_state(k):
    st = STATE.get(k)
    if st is None:
        st = (jread('state.json', {}) or {}).get(k)
        STATE[k] = st
    return st


def put_state(k, v):
    STATE[k] = v
    all_ = jread('state.json', {}) or {}
    all_[k] = v
    jwrite('state.json', all_)


_T = {'prune': 0.0, 'newfiles': 0.0, 'plexup': 0.0}


def tick(store, maint=False):
    if CTX['store'] is None:
        CTX['store'] = store
    for fn in (sample, watch_plex):
        try:
            fn(store)
        except Exception as e:                          # noqa: BLE001
            log('%s: %s' % (fn.__name__, e), 'warn')
    try:
        watch_dashboard()
    except Exception as e:                              # noqa: BLE001
        log('watch_dashboard: %s' % e, 'warn')
    now = time.time()
    if now - _T['prune'] > 86400:
        _T['prune'] = now
        try:
            prune(store)
        except Exception as e:                          # noqa: BLE001
            log('prune: %s' % e, 'warn')
    if maint:
        return
    if int(cfg('ins_newfiles', 1) or 0) and now - _T['newfiles'] > 3600 and not JOB['running'] \
            and HEALTH.get('answers'):
        _T['newfiles'] = now
        threading.Thread(target=_safe, args=(newfiles, store), daemon=True,
                         name='plexmon-newfiles').start()
    if int(cfg('plex_update_check', 1) or 0) and now - _T['plexup'] > 86400:
        _T['plexup'] = now
        last = (get_state('plexup') or {}).get('at') or 0
        if now - last > 20 * 3600:
            threading.Thread(target=_safe, args=(check_plex_update, store), daemon=True,
                             name='plexmon-plexup').start()
    try:
        integrity_schedule(store)
    except Exception as e:                              # noqa: BLE001
        log('integrity_schedule: %s' % e, 'warn')


def _safe(fn, store):
    try:
        fn(store)
    except Exception as e:                              # noqa: BLE001
        log('%s: %s' % (fn.__name__, e), 'warn')


def view(store):
    return {'job': job_view(), 'library': jread('library.json'),
            'episodes': jread('episodes.json'), 'now': time.time(),
            'tvmaze': bool(int(cfg('plex_tvmaze', 1) or 0)),
            'langs': {'subs': cfg('ins_sub_langs', ''), 'audio': cfg('ins_audio_langs', '')},
            'channels': channels(), 'notified': (jread('notified.json', []) or [])[:20],
            'ix_auto': {'on': bool(int(cfg('ix_auto', 0) or 0)), 'every': cfg('ix_auto_every'),
                        'day': cfg('ix_auto_day'), 'at': cfg('ix_auto_at'),
                        'mode': cfg('ix_auto_mode')}}
