#!/usr/bin/env python3
"""plexmon_plex.py - the Plex library tools of plexmon's Settings.

What plex_tools.py did from a Windows console, done here on the Plex server
itself, for the pages (Settings → Plex library), the command line and the
console:

  - the default audio track and subtitles of a movie, or of every episode of
    a show (one season, a few episodes) - the very track, not only the
    language: an external .srt is told from an embedded one, forced from SDH;
  - watched / unwatched - a movie, a show, a season, single episodes;
  - labels - added, taken away, renamed, cleaned out of a whole library;
  - titles in another language - found (by the writing system, the letters
    and the little words of a title, or the item's own metadata language) and
    put into English (or another language) the way that is safest: the title
    alone, the metadata language and a refresh, or a Fix Match to the item's
    own entry;
  - the watchlist of the server's owner (Plex Discover);
  - ratings filled in from IMDb / Rotten Tomatoes (OMDb), TMDB, TVmaze, Trakt.

How it keeps Plex Media Server safe
-----------------------------------
- Only Plex's own HTTP API, on 127.0.0.1:32400 - never its files, never its
  database. Nothing here stops, restarts or reconfigures Plex.
- The server's own token (PlexOnlineToken in Preferences.xml), read here and
  never handed to the pages; no plex.tv sign-in, no Home users, no PIN.
- At most PLEX_PARALLEL requests at once, every write paced, metadata read in
  batches of 40 (a show of 300 episodes is 8 requests), one job at a time -
  with its progress on the pages and a Stop that stops between two items.
- Everything that writes is shown first (the plan, item by item) and only
  done when confirmed; an item with a locked title or a split entry (two
  library items on one match) is held back unless asked for by name.
- Images: Plex's own transcoder makes a small one (a poster at 300 px wide),
  kept on disk here so that a page drawn again asks Plex for nothing.
"""

import hashlib
import json
import os
import re
import ssl
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

A = None                # plexmon_admin, bound by bind(): CONFIG, log, the store

PLEX_PARALLEL = 3
WRITE_GAP_S = 0.04
READ_TIMEOUT_S = 30
ST_VIDEO, ST_AUDIO, ST_SUBTITLE = 1, 2, 3
SECTION_TYPE_NUM = {'movie': 1, 'show': 2, 'season': 3, 'episode': 4}
DISCOVER_URL = os.environ.get('PLEXMON_DISCOVER_URL') or 'https://discover.provider.plex.tv'
PRODUCT = 'plexmon'
IMG_DIR = Path(os.environ.get('PLEXMON_IMG_CACHE', '/var/cache/plexmon/img'))
IMG_CACHE_MB = 400
IMG_REMOTE_HOSTS = ('metadata-static.plex.tv', 'image.tmdb.org', 'images.plex.tv',
                    'provider-static.plex.tv', 'plex.tv')

_SEM = threading.BoundedSemaphore(PLEX_PARALLEL)
_WRITE_LOCK = threading.Lock()
_LAST_WRITE = [0.0]
_TOKEN = {'mtime': None, 'token': None}
_URL = {'url': None}
_CACHE = {}             # ('items', lib) -> {'at', 'data'}
_CACHE_LOCK = threading.Lock()
_SCANS = {}             # track scans kept for their plan: id -> {...}
_PLANS = {}             # rating plans: id -> {...}
JOB = {'id': '', 'kind': '', 'what': '', 'running': False, 'done': 0, 'whole': 0, 'says': '',
       'result': None, 'error': '', 'began': 0.0, 'ended': 0.0, 'by': '', 'stop': False,
       'lines': []}
_JOB_LOCK = threading.Lock()


class PlexError(Exception):
    pass


def _web(req, timeout):
    """What is asked of the internet - images, Plex Discover, the rating
    databases - straight, as always: Settings → Proxy is for the updates alone."""
    return urllib.request.urlopen(req, timeout=timeout)


def bind(admin):
    global A
    A = admin


def cfg(key, default=None):
    try:
        v = A.CONFIG.get(key)
    except Exception:                                   # noqa: BLE001
        v = None
    return default if v in (None, '') else v


def secret(key):
    v = str(cfg(key, '') or '')
    if not v:
        return ''
    try:
        return A.unseal_secret(v) or ''
    except Exception:                                   # noqa: BLE001
        return ''


def log(words, level='info'):
    try:
        A.log('plex: ' + words, level)
    except Exception:                                   # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Talking to Plex Media Server, here on this machine
# ---------------------------------------------------------------------------
def prefs_path():
    return Path(str(getattr(A, 'PLEX_PREFS', '') or os.environ.get(
        'PLEXMON_PLEX_PREFS', '/var/lib/plexmediaserver/Library/Application Support/'
        'Plex Media Server/Preferences.xml')))


def plex_token():
    """The server's own token, from Preferences.xml - read again when the file
    changes (a claim of the server, a sign-out)."""
    env = os.environ.get('PLEXMON_PLEX_TOKEN')
    if env:
        return env
    p = prefs_path()
    try:
        m = p.stat().st_mtime
    except OSError:
        return None
    if _TOKEN['mtime'] != m:
        try:
            text = p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None
        got = re.search(r'\bPlexOnlineToken="([^"]+)"', text)
        _TOKEN.update(mtime=m, token=got.group(1) if got else None)
    return _TOKEN['token']


def client_id():
    try:
        return 'plexmon-' + hashlib.sha1(A.node_name().encode()).hexdigest()[:12]
    except Exception:                                   # noqa: BLE001
        return 'plexmon'


def base_urls():
    first = str(cfg('plex_url', '') or os.environ.get('PLEXMON_PLEX_URL') or
                'http://127.0.0.1:32400').rstrip('/')
    out = [first]
    if first.startswith('http://127.0.0.1') or first.startswith('http://localhost'):
        out.append(first.replace('http://', 'https://', 1))
    return out


def _ctx(url):
    if not url.startswith('https'):
        return None
    c = ssl.create_default_context()
    # Plex on 127.0.0.1 answers with its *.plex.direct certificate: not the
    # name asked for. The address is this machine's own loopback.
    host = urllib.parse.urlparse(url).hostname or ''
    if host in ('127.0.0.1', 'localhost', '::1'):
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
    return c


def _headers(accept='application/json', token=None):
    h = {'Accept': accept, 'X-Plex-Product': PRODUCT, 'X-Plex-Version': getattr(
        A, '__version__', '2'), 'X-Plex-Client-Identifier': client_id(),
        'X-Plex-Platform': 'Linux', 'X-Plex-Device-Name': 'plexmon'}
    tok = token or plex_token()
    if tok:
        h['X-Plex-Token'] = tok
    return h


def raw(method, path, params=None, accept='application/json', timeout=READ_TIMEOUT_S,
        binary=False):
    """(status, body) from the local Plex. A write waits its turn (paced); at
    most PLEX_PARALLEL requests are open at once."""
    if not plex_token():
        raise PlexError('no token of the Plex server here (PlexOnlineToken in %s) - is the '
                        'server claimed?' % prefs_path())
    q = urllib.parse.urlencode(params or {}, doseq=True)
    if method != 'GET':
        with _WRITE_LOCK:
            gap = WRITE_GAP_S - (time.time() - _LAST_WRITE[0])
            if gap > 0:
                time.sleep(gap)
            _LAST_WRITE[0] = time.time()
    last = None
    urls = [_URL['url']] if _URL['url'] else base_urls()
    for n, base in enumerate(urls):
        url = base + path + ('?' + q if q else '')
        req = urllib.request.Request(url, method=method, headers=_headers(accept))
        with _SEM:
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=_ctx(url)) as r:
                    body = r.read()
                    _URL['url'] = base
                    return r.status, (body if binary else body.decode('utf-8', 'replace'))
            except urllib.error.HTTPError as e:
                body = e.read() if hasattr(e, 'read') else b''
                if e.code in (401, 403) and n + 1 < len(urls):
                    # "Secure connections: required" refuses plain HTTP even
                    # on 127.0.0.1: the same over HTTPS.
                    last = e
                    continue
                _URL['url'] = base
                return e.code, (body if binary else body.decode('utf-8', 'replace'))
            except (OSError, ValueError) as e:
                last = e
                continue
    _URL['url'] = None
    raise PlexError('Plex Media Server does not answer on %s: %s' % (
        ', '.join(urls), getattr(last, 'reason', last)))


def get(path, params=None, timeout=READ_TIMEOUT_S):
    st, text = raw('GET', path, params, timeout=timeout)
    if st == 401:
        raise PlexError('Plex refused the server\'s own token (401)')
    if st >= 400:
        raise PlexError('Plex said %s to %s' % (st, path))
    try:
        return json.loads(text or '{}')
    except ValueError:
        raise PlexError('Plex did not answer %s with JSON' % path)


def put(path, params=None):
    st, text = raw('PUT', path, params)
    if st >= 400:
        raise PlexError('Plex said %s to %s: %s' % (st, path, (text or '')[:160]))
    return st


def mc(j):
    return (j or {}).get('MediaContainer') or {}


# ---------------------------------------------------------------------------
# What is in the libraries
# ---------------------------------------------------------------------------
def server_info():
    j = mc(get('/'))
    return {'name': j.get('friendlyName') or 'Plex', 'version': j.get('version') or '',
            'machine': j.get('machineIdentifier') or '', 'platform': j.get('platform') or '',
            'transcoding': j.get('transcoderActiveVideoSessions') or 0,
            'myplex': j.get('myPlexUsername') or ''}


def sections():
    out = []
    for d in mc(get('/library/sections')).get('Directory') or []:
        out.append({'key': str(d.get('key')), 'title': d.get('title'), 'type': d.get('type'),
                    'language': d.get('language'), 'agent': d.get('agent'),
                    'thumb': d.get('composite') or d.get('thumb') or '', 'art': d.get('art') or '',
                    'scanned': d.get('scannedAt'), 'updated': d.get('updatedAt'),
                    'refreshing': bool(d.get('refreshing'))})
    return out


def section(lib):
    for s in sections():
        if s['key'] == str(lib):
            return s
    raise PlexError('there is no library %s' % lib)


_COUNTS = {}


def section_count(s):
    """How many movies or shows a library has - Plex's totalSize, no items."""
    k = s['key']
    c = _COUNTS.get(k)
    if c and time.time() - c[0] < 300:
        return c[1]
    try:
        j = mc(get('/library/sections/%s/all' % k, {
            'type': SECTION_TYPE_NUM.get(s['type'], 1), 'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': 0}))
        n = int(j.get('totalSize') if j.get('totalSize') is not None else j.get('size') or 0)
    except (PlexError, ValueError, TypeError):
        return None
    _COUNTS[k] = (time.time(), n)
    return n


def video_sections():
    return [s for s in sections() if s['type'] in ('movie', 'show')]


def _tags(m, name, n=None):
    got = [t.get('tag') for t in (m.get(name) or []) if t.get('tag')]
    return got[:n] if n else got


def _compact(m):
    """An item of a listing as the pages need it - nothing more."""
    t = m.get('type')
    out = {'rk': str(m.get('ratingKey')), 'type': t, 'title': m.get('title') or '',
           'year': m.get('year'), 'thumb': m.get('thumb') or '', 'art': m.get('art') or '',
           'added': m.get('addedAt'), 'rating': m.get('rating'),
           'audience': m.get('audienceRating'), 'rating_img': m.get('ratingImage') or '',
           'audience_img': m.get('audienceRatingImage') or '', 'cr': m.get('contentRating') or '',
           'guid': m.get('guid') or '', 'sort': m.get('titleSort') or '',
           'orig': m.get('originalTitle') or '', 'studio': m.get('studio') or '',
           'genres': _tags(m, 'Genre', 4), 'countries': _tags(m, 'Country'),
           'labels': _tags(m, 'Label'), 'duration': m.get('duration'),
           'viewed': m.get('lastViewedAt'), 'views': m.get('viewCount') or 0,
           'offset': m.get('viewOffset') or 0}
    if t == 'show':
        out.update(leaves=m.get('leafCount') or 0, seen=m.get('viewedLeafCount') or 0,
                   seasons=m.get('childCount') or 0)
    ids = {}
    for g in m.get('Guid') or []:
        gid = str(g.get('id') or '')
        if '://' in gid:
            k, v = gid.split('://', 1)
            ids[k.lower()] = v
    if ids:
        out['ids'] = ids
    return out


def items(lib, fresh=False):
    """Every movie or show of a library, compact, kept for two minutes."""
    k = ('items', str(lib))
    with _CACHE_LOCK:
        c = _CACHE.get(k)
        if c and not fresh and time.time() - c['at'] < 120:
            return c['data']
    s = section(lib)
    num = SECTION_TYPE_NUM.get(s['type'])
    params = {'includeGuids': 1}
    if num:
        params['type'] = num
    rows = [_compact(m) for m in mc(get('/library/sections/%s/all' % lib, params,
                                        timeout=120)).get('Metadata') or []]
    rows.sort(key=lambda r: (r.get('sort') or r['title'] or '').lower())
    data = {'section': s, 'items': rows, 'at': time.time()}
    with _CACHE_LOCK:
        _CACHE[k] = {'at': time.time(), 'data': data}
    return data


def forget(lib=None):
    with _CACHE_LOCK:
        for k in list(_CACHE):
            if lib is None or k[1] == str(lib):
                _CACHE.pop(k, None)


def metadata(rk):
    md = mc(get('/library/metadata/%s' % rk, {'includeGuids': 1})).get('Metadata') or []
    return md[0] if md else None


def metadata_many(rks, chunk=40, stop=None):
    """Full metadata (with the streams) of many items in few requests; an item
    that will not load is left out, not fatal."""
    out = {}
    keys = [str(k) for k in rks if k is not None]
    for i in range(0, len(keys), chunk):
        if stop and stop():
            break
        batch = keys[i:i + chunk]
        try:
            for m in mc(get('/library/metadata/' + ','.join(batch),
                            {'includeGuids': 1}, timeout=120)).get('Metadata') or []:
                out[str(m.get('ratingKey'))] = m
        except PlexError:
            for one in batch:
                try:
                    m = metadata(one)
                    if m:
                        out[one] = m
                except PlexError as e:
                    log('item %s could not be read: %s' % (one, e), 'warn')
    return out


def children(rk):
    return mc(get('/library/metadata/%s/children' % rk)).get('Metadata') or []


def all_leaves(rk):
    return mc(get('/library/metadata/%s/allLeaves' % rk, timeout=120)).get('Metadata') or []


def item_prefs(rk):
    try:
        j = get('/library/metadata/%s/prefs' % rk)
    except PlexError:
        return {}
    return {s.get('id'): s.get('value') for s in mc(j).get('Setting') or [] if s.get('id')}


def activities():
    try:
        acts = mc(get('/activities')).get('Activity') or []
    except PlexError:
        return []
    return [{'type': a.get('type'), 'title': a.get('title'), 'subtitle': a.get('subtitle'),
             'progress': a.get('progress'), 'uuid': a.get('uuid')} for a in acts]


def now_playing():
    try:
        return int(mc(get('/status/sessions')).get('size') or 0)
    except PlexError:
        return 0


def locked_fields(md):
    return sorted({f.get('name') for f in (md or {}).get('Field') or []
                   if f.get('locked') in (1, True, '1', 'true') and f.get('name')})


# ---------------------------------------------------------------------------
# The job: one at a time, followed by the pages
# ---------------------------------------------------------------------------
def job_view():
    with _JOB_LOCK:
        j = dict(JOB)
        j['lines'] = list(JOB['lines'][-40:])
    j.pop('stop', None)
    return j


def job_start(kind, what, by, fn):
    with _JOB_LOCK:
        if JOB['running']:
            raise ValueError('%s is under way already - wait for it, or stop it' % (
                JOB['what'] or 'another job'))
        JOB.update(id=os.urandom(5).hex(), kind=kind, what=what, running=True, done=0,
                   whole=0, says='starting', result=None, error='', began=time.time(),
                   ended=0.0, by=by, stop=False, lines=[])
        ident = JOB['id']

    def go():
        try:
            JOB['result'] = fn()
        except (PlexError, ValueError, OSError) as e:
            JOB['error'] = str(e)[:400]
            log('%s failed: %s' % (what, e), 'warn')
        except Exception as e:                          # noqa: BLE001
            JOB['error'] = 'it broke: %s' % e
            log('%s broke: %s' % (what, e), 'error')
        finally:
            JOB.update(running=False, ended=time.time(), says='done' if not JOB['error']
                       else 'failed')
    threading.Thread(target=go, name='plex-job', daemon=True).start()
    return ident


def step(done=None, whole=None, says=None, line=None):
    if done is not None:
        JOB['done'] = done
    if whole is not None:
        JOB['whole'] = whole
    if says is not None:
        JOB['says'] = says
    if line:
        with _JOB_LOCK:
            JOB['lines'].append(line)
            del JOB['lines'][:-300]


def stopped():
    return bool(JOB.get('stop'))


def job_stop():
    if JOB['running']:
        JOB['stop'] = True
        return True
    return False


def event(store, words, who, level='info'):
    try:
        store.add_event('plex', words, level, who=who)
    except Exception:                                   # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Images - a small one by Plex's own transcoder, kept here
# ---------------------------------------------------------------------------
def _img_ok_path(u):
    if u.startswith('/library/') or u.startswith('/photo/') or u.startswith('/:/'):
        return 'local'
    p = urllib.parse.urlparse(u)
    if p.scheme == 'https' and p.hostname and any(
            p.hostname == h or p.hostname.endswith('.' + h) for h in IMG_REMOTE_HOSTS):
        return 'remote'
    return ''


def image(u, w, h):
    """(bytes, content type) of an image, `w` x `h` at most."""
    kind = _img_ok_path(u)
    if not kind or '..' in u:
        raise PlexError('not an image of Plex')
    w = max(40, min(1920, int(w or 300)))
    h = max(40, min(1080, int(h or 450)))
    key = hashlib.sha1(('%s|%d|%d' % (u, w, h)).encode()).hexdigest()
    path = IMG_DIR / key[:2] / (key + '.img')
    try:
        data = path.read_bytes()
        if data:
            os.utime(str(path), None)
            return data, _ctype(data)
    except OSError:
        pass
    if kind == 'local':
        st, data = raw('GET', '/photo/:/transcode', {
            'url': u, 'width': w, 'height': h, 'minSize': 1, 'upscale': 1, 'quality': 82,
            'format': 'jpeg'}, accept='image/*', timeout=40, binary=True)
        if st >= 400 or not data:
            st, data = raw('GET', u, accept='image/*', timeout=40, binary=True)
        if st >= 400 or not data:
            raise PlexError('Plex has no such image (%s)' % st)
    else:
        req = urllib.request.Request(u, headers={'User-Agent': 'plexmon'})
        with _SEM:
            with _web(req, 30) as r:
                data = r.read(8 << 20)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp%d' % os.getpid())
        tmp.write_bytes(data)
        os.replace(str(tmp), str(path))
        _img_tidy()
    except OSError:
        pass
    return data, _ctype(data)


def _ctype(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'


_TIDY = {'at': 0.0}


def _img_tidy():
    """Kept under IMG_CACHE_MB: the least lately used go first (once in 10 min)."""
    if time.time() - _TIDY['at'] < 600:
        return
    _TIDY['at'] = time.time()
    files = []
    total = 0
    for root, _d, names in os.walk(str(IMG_DIR)):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
    limit = IMG_CACHE_MB << 20
    if total <= limit:
        return
    files.sort()
    for _m, size, p in files:
        if total <= limit * 0.8:
            break
        try:
            os.unlink(p)
            total -= size
        except OSError:
            pass


def img_cache_size():
    total = n = 0
    for root, _d, names in os.walk(str(IMG_DIR)):
        for x in names:
            try:
                total += os.path.getsize(os.path.join(root, x))
                n += 1
            except OSError:
                pass
    return {'bytes': total, 'files': n, 'where': str(IMG_DIR)}


# ---------------------------------------------------------------------------
# The detail of one item
# ---------------------------------------------------------------------------
LANG_NAMES = {
    'eng': 'English', 'en': 'English', 'cze': 'Czech', 'ces': 'Czech', 'cs': 'Czech',
    'slo': 'Slovak', 'slk': 'Slovak', 'sk': 'Slovak', 'ger': 'German', 'deu': 'German',
    'de': 'German', 'fre': 'French', 'fra': 'French', 'fr': 'French', 'spa': 'Spanish',
    'es': 'Spanish', 'ita': 'Italian', 'it': 'Italian', 'por': 'Portuguese', 'pt': 'Portuguese',
    'rus': 'Russian', 'ru': 'Russian', 'ukr': 'Ukrainian', 'uk': 'Ukrainian', 'pol': 'Polish',
    'pl': 'Polish', 'kor': 'Korean', 'ko': 'Korean', 'jpn': 'Japanese', 'ja': 'Japanese',
    'chi': 'Chinese', 'zho': 'Chinese', 'zh': 'Chinese', 'dan': 'Danish', 'da': 'Danish',
    'dut': 'Dutch', 'nld': 'Dutch', 'nl': 'Dutch', 'swe': 'Swedish', 'sv': 'Swedish',
    'nor': 'Norwegian', 'no': 'Norwegian', 'fin': 'Finnish', 'fi': 'Finnish',
    'hun': 'Hungarian', 'hu': 'Hungarian', 'gre': 'Greek', 'ell': 'Greek', 'el': 'Greek',
    'tur': 'Turkish', 'tr': 'Turkish', 'ara': 'Arabic', 'ar': 'Arabic', 'heb': 'Hebrew',
    'he': 'Hebrew', 'hin': 'Hindi', 'hi': 'Hindi', 'tha': 'Thai', 'th': 'Thai',
    'vie': 'Vietnamese', 'vi': 'Vietnamese', 'ron': 'Romanian', 'rum': 'Romanian',
    'ro': 'Romanian', 'bul': 'Bulgarian', 'bg': 'Bulgarian', 'hrv': 'Croatian', 'hr': 'Croatian',
    'srp': 'Serbian', 'sr': 'Serbian', 'slv': 'Slovenian', 'sl': 'Slovenian',
}


def _is_latin(s):
    return all((ord(ch) < 0x250) or (not ch.isalpha()) for ch in s)


def lang_name(code, plex_name=None):
    c = (code or '').lower()
    if c in LANG_NAMES:
        return LANG_NAMES[c]
    if plex_name and _is_latin(plex_name):
        return plex_name
    return code or '?'


def _truthy(v):
    return str(v).lower() in ('1', 'true')


def stream_is_external(s):
    return bool(s.get('key')) or _truthy(s.get('external', ''))


def stream_signature(s):
    """The same track across episodes (the ids differ, this does not)."""
    return ((s.get('languageCode') or s.get('languageTag') or '').lower(),
            (s.get('codec') or '').lower(), stream_is_external(s), _truthy(s.get('forced', '')),
            _truthy(s.get('hearingImpaired', '')), (s.get('language') or '').strip().lower(),
            (s.get('title') or '').strip().lower())


def variant_label(s):
    code = (s.get('languageCode') or '').lower()
    disp = (s.get('extendedDisplayTitle') or s.get('displayTitle') or '').strip()
    if disp and _is_latin(disp):
        if code and '[%s]' % code not in disp and '(%s)' % code not in disp:
            disp += ' [%s]' % code
        return disp
    base = lang_name(code, s.get('language'))
    quals = []
    if s.get('codec'):
        quals.append(str(s['codec']).upper())
    if stream_is_external(s):
        quals.append('external')
    if _truthy(s.get('forced', '')):
        quals.append('forced')
    if _truthy(s.get('hearingImpaired', '')):
        quals.append('SDH')
    title = (s.get('title') or '').strip()
    if title and _is_latin(title) and title.lower() not in base.lower():
        quals.append(title)
    return base + (' [%s]' % code if code else '') + (' (%s)' % ', '.join(quals) if quals else '')


def iter_parts(md):
    for media in md.get('Media') or []:
        for part in media.get('Part') or []:
            yield part.get('id'), part.get('Stream') or []


def _vkey(sig, ordinal):
    return 'v' + hashlib.sha1(json.dumps([list(sig), ordinal]).encode()).hexdigest()[:12]


def streams_variants(streams, stype):
    reg, seen = {}, {}
    for s in streams:
        if s.get('streamType') != stype:
            continue
        sig = stream_signature(s)
        o = seen.get(sig, 0)
        seen[sig] = o + 1
        k = _vkey(sig, o)
        if k not in reg:
            reg[k] = {'key': k, 'sig': sig, 'ordinal': o,
                      'code': (s.get('languageCode') or '').lower(), 'label': variant_label(s),
                      'external': stream_is_external(s), 'forced': _truthy(s.get('forced', '')),
                      'sdh': _truthy(s.get('hearingImpaired', '')),
                      'codec': (s.get('codec') or '').lower(), 'count': 1, 'selected': 0}
        if s.get('selected'):
            reg[k]['selected'] = 1
    return reg


def finalize_variants(reg):
    vs = list(reg.values())
    lab = Counter(v['label'] for v in vs)
    for v in vs:
        if lab[v['label']] > 1:
            v['label'] = '%s #%d' % (v['label'], v['ordinal'] + 1)
    vs.sort(key=lambda v: (v['label'].lower(), v['ordinal']))
    return vs


def pick_variant_id(streams, stype, variant):
    cnt = 0
    for s in streams:
        if s.get('streamType') != stype:
            continue
        if stream_signature(s) == tuple(variant['sig']):
            if cnt == variant['ordinal']:
                return s.get('id')
            cnt += 1
    return None


def _media_facts(md):
    out = []
    for media in md.get('Media') or []:
        parts = media.get('Part') or []
        size = sum(int(p.get('size') or 0) for p in parts)
        out.append({'resolution': media.get('videoResolution'), 'vcodec': media.get('videoCodec'),
                    'acodec': media.get('audioCodec'), 'channels': media.get('audioChannels'),
                    'container': media.get('container'), 'bitrate': media.get('bitrate'),
                    'hdr': bool(media.get('videoDynamicRange') or ''), 'dr': media.get(
                        'videoDynamicRange') or '', 'size': size, 'width': media.get('width'),
                    'height': media.get('height'), 'files': [os.path.basename(
                        str(p.get('file') or '')) for p in parts][:4]})
    return out


def rating_bits(md):
    """The badges Plex shows: IMDb 7.7 · RT 94% · TMDB 82%."""
    def source(image):
        im = (image or '').lower()
        if 'imdb' in im:
            return 'imdb', 'IMDb', 'ten'
        if 'rottentomatoes' in im:
            up = 'upright' in im or 'spilled' in im
            return ('rt_audience' if up else 'rt'), ('RT Audience' if up else 'RT'), 'pct'
        if 'themoviedb' in im or 'tmdb' in im:
            return 'tmdb', 'TMDB', 'pct'
        return 'plex', '', 'ten'
    pairs = []
    if isinstance(md.get('Rating'), list) and md['Rating']:
        pairs = [(r.get('value'), r.get('image')) for r in md['Rating']]
    else:
        pairs = [(md.get('rating'), md.get('ratingImage')),
                 (md.get('audienceRating'), md.get('audienceRatingImage'))]
    out, seen = [], set()
    for value, img in pairs:
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        k, label, kind = source(img)
        text = ('%d%%' % round(v if v > 10 else v * 10)) if kind == 'pct' else '%.1f' % v
        if (k, text) in seen:
            continue
        seen.add((k, text))
        out.append({'src': k, 'label': label, 'value': text, 'raw': v})
    return out


def _person(r):
    return {'id': r.get('id'), 'name': r.get('tag'), 'role': r.get('role') or '',
            'thumb': r.get('thumb') or ''}


def _child(m):
    return {'rk': str(m.get('ratingKey')), 'type': m.get('type'), 'title': m.get('title') or '',
            'index': m.get('index'), 'parent_index': m.get('parentIndex'),
            'thumb': m.get('thumb') or '', 'summary': (m.get('summary') or '')[:600],
            'views': m.get('viewCount') or 0, 'offset': m.get('viewOffset') or 0,
            'duration': m.get('duration'), 'rating': m.get('rating'),
            'audience': m.get('audienceRating'), 'aired': m.get('originallyAvailableAt'),
            'leaves': m.get('leafCount'), 'seen': m.get('viewedLeafCount')}


def item_view(rk):
    md = metadata(rk)
    if not md:
        raise PlexError('there is no item %s' % rk)
    t = md.get('type')
    out = {'rk': str(rk), 'type': t, 'title': md.get('title') or '', 'year': md.get('year'),
           'summary': md.get('summary') or '', 'tagline': md.get('tagline') or '',
           'thumb': md.get('thumb') or '', 'art': md.get('art') or '',
           'cr': md.get('contentRating') or '', 'studio': md.get('studio') or '',
           'duration': md.get('duration'), 'aired': md.get('originallyAvailableAt'),
           'genres': _tags(md, 'Genre'), 'countries': _tags(md, 'Country'),
           'directors': _tags(md, 'Director'), 'writers': _tags(md, 'Writer'),
           'labels': _tags(md, 'Label'), 'collections': _tags(md, 'Collection'),
           'cast': [_person(r) for r in (md.get('Role') or [])[:24]],
           'ratings': rating_bits(md), 'rating': md.get('rating'),
           'audience': md.get('audienceRating'), 'guid': md.get('guid') or '',
           'sort': md.get('titleSort') or '', 'orig': md.get('originalTitle') or '',
           'locked': locked_fields(md), 'lib': str(md.get('librarySectionID') or ''),
           'lib_title': md.get('librarySectionTitle') or '', 'views': md.get('viewCount') or 0,
           'offset': md.get('viewOffset') or 0, 'viewed': md.get('lastViewedAt'),
           'added': md.get('addedAt'), 'ids': {}, 'dk': discover_key(md.get('guid')),
           'parent': {'rk': str(md.get('parentRatingKey') or ''),
                      'title': md.get('parentTitle') or '',
                      'index': md.get('parentIndex')},
           'grandparent': {'rk': str(md.get('grandparentRatingKey') or ''),
                           'title': md.get('grandparentTitle') or '',
                           'thumb': md.get('grandparentThumb') or '',
                           'art': md.get('grandparentArt') or ''},
           'index': md.get('index')}
    for g in md.get('Guid') or []:
        gid = str(g.get('id') or '')
        if '://' in gid:
            k, v = gid.split('://', 1)
            out['ids'][k.lower()] = v
    prefs = item_prefs(rk) if t in ('movie', 'show') else {}
    out['language'] = prefs.get('languageOverride') or ''
    if t in ('movie', 'episode'):
        out['media'] = _media_facts(md)
        out['tracks'] = tracks_of(md)
    if t == 'show':
        out['leaves'] = md.get('leafCount') or 0
        out['seen'] = md.get('viewedLeafCount') or 0
        out['seasons'] = [_child(c) for c in children(rk)]
    if t == 'season':
        out['episodes'] = [_child(c) for c in children(rk)]
    return out


def tracks_of(md):
    """The audio and subtitle tracks of a movie or an episode, and which one
    is its default now."""
    audio, subs = {}, {}
    for _pid, streams in iter_parts(md):
        for k, v in streams_variants(streams, ST_AUDIO).items():
            audio.setdefault(k, v)
        for k, v in streams_variants(streams, ST_SUBTITLE).items():
            subs.setdefault(k, v)
    a = finalize_variants(audio)
    s = finalize_variants(subs)
    for v in a + s:
        v.pop('sig', None)
    return {'audio': a, 'subs': s, 'subs_off': not any(v['selected'] for v in s)}


def children_view(rk):
    return [_child(c) for c in children(rk)]


def discover_key(guid):
    g = str(guid or '')
    return g.rsplit('/', 1)[-1] if g.startswith('plex://') else None


# ---------------------------------------------------------------------------
# Default audio and subtitles
# ---------------------------------------------------------------------------
def _expand_to_videos(rks, stop=None):
    """Movies and episodes behind what was picked: a show is every episode of
    it, a season its episodes."""
    md_map = metadata_many(rks, stop=stop)
    out = []
    for rk in rks:
        m = md_map.get(str(rk))
        if not m:
            continue
        t = m.get('type')
        if t in ('movie', 'episode'):
            out.append(str(rk))
        elif t in ('show', 'season'):
            out.extend(str(e.get('ratingKey')) for e in all_leaves(rk))
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def ep_label(m):
    if m.get('type') == 'episode':
        try:
            return 'S%02dE%02d %s' % (int(m.get('parentIndex')), int(m.get('index')),
                                      m.get('title') or '')
        except (TypeError, ValueError):
            pass
    return m.get('title') or '#%s' % m.get('ratingKey')


def tracks_scan(rks):
    """What tracks every picked video has, merged across the episodes by the
    track itself; kept here under an id for the plan that follows."""
    keys = _expand_to_videos([str(x) for x in rks])
    if not keys:
        raise ValueError('nothing to scan: no movie or episode in what was picked')
    if len(keys) > 3000:
        raise ValueError('%d videos at once is too many - pick fewer' % len(keys))
    md_map = metadata_many(keys)
    data, audio_reg, sub_reg = [], {}, {}
    for k in keys:
        m = md_map.get(k)
        if not m:
            continue
        parts = list(iter_parts(m))
        for _pid, streams in parts:
            for stt, reg in ((ST_AUDIO, audio_reg), (ST_SUBTITLE, sub_reg)):
                for vk, v in streams_variants(streams, stt).items():
                    if vk in reg:
                        reg[vk]['count'] += 1
                        reg[vk]['selected'] += v['selected']
                    else:
                        reg[vk] = v
        data.append({'rk': k, 'label': ep_label(m), 'lib': str(m.get('librarySectionID') or ''),
                     'show': m.get('grandparentTitle') or '', 'parts': parts,
                     's': m.get('parentIndex'), 'e': m.get('index')})
    data.sort(key=lambda d: (d['show'], d['s'] or 0, d['e'] or 0, d['label']))
    ident = os.urandom(6).hex()
    audio, subs = finalize_variants(audio_reg), finalize_variants(sub_reg)
    _SCANS[ident] = {'at': time.time(), 'items': data, 'audio': audio, 'subs': subs}
    for old in [k for k, v in _SCANS.items() if time.time() - v['at'] > 3600]:
        _SCANS.pop(old, None)
    pub = lambda vs: [{k: v for k, v in x.items() if k != 'sig'} for x in vs]  # noqa: E731
    return {'scan': ident, 'videos': len(data), 'audio': pub(audio), 'subs': pub(subs),
            'items': [{'rk': d['rk'], 'label': d['label'], 'show': d['show']} for d in data]}


def _scan(ident):
    sc = _SCANS.get(str(ident))
    if not sc:
        raise ValueError('that scan is gone (an hour old, or the service restarted) - scan again')
    return sc


def _variant(sc, kind, key):
    for v in sc['audio' if kind == 'audio' else 'subs']:
        if v['key'] == key:
            return v
    raise ValueError('no such %s track in the scan' % kind)


def _has(item, stype, v):
    return any(pick_variant_id(st, stype, v) is not None for _p, st in item['parts'])


def tracks_plan(ident, audio='keep', subs='keep', missing_audio='skip', missing_subs='skip'):
    """Item by item: what its default audio and subtitles become."""
    sc = _scan(ident)
    av = None if audio in ('keep', '', None) else _variant(sc, 'audio', audio)
    sv = None if subs in ('keep', 'off', '', None) else _variant(sc, 'subs', subs)
    alt_a = None if missing_audio in ('skip', '', None) else _variant(sc, 'audio', missing_audio)
    alt_s = None if missing_subs in ('skip', 'off', '', None) else _variant(
        sc, 'subs', missing_subs)
    rows = []
    for it in sc['items']:
        row = {'rk': it['rk'], 'label': it['label'], 'show': it['show'], 'parts': []}
        for pid, streams in it['parts']:
            p = {'part': pid}
            if av is not None:
                aid = pick_variant_id(streams, ST_AUDIO, av)
                if aid is None and alt_a is not None:
                    aid = pick_variant_id(streams, ST_AUDIO, alt_a)
                    if aid is not None:
                        p['audio_alt'] = alt_a['label']
                if aid is not None:
                    p['audio'] = aid
                else:
                    p['audio_missing'] = True
            if subs == 'off':
                p['subs'] = 0
            elif sv is not None:
                tid = pick_variant_id(streams, ST_SUBTITLE, sv)
                if tid is None:
                    if missing_subs == 'off':
                        tid = 0
                        p['subs_alt'] = 'off'
                    elif alt_s is not None:
                        tid = pick_variant_id(streams, ST_SUBTITLE, alt_s)
                        if tid is not None:
                            p['subs_alt'] = alt_s['label']
                if tid is not None:
                    p['subs'] = tid
                else:
                    p['subs_missing'] = True
            row['parts'].append(p)
        rows.append(row)
    n_a_miss = sum(1 for r in rows if any(p.get('audio_missing') for p in r['parts']))
    n_s_miss = sum(1 for r in rows if any(p.get('subs_missing') for p in r['parts']))
    change = sum(1 for r in rows if any('audio' in p or 'subs' in p for p in r['parts']))
    sc['plan'] = rows
    sc['plan_words'] = {'audio': av['label'] if av else 'unchanged',
                        'subs': 'off' if subs == 'off' else (sv['label'] if sv else 'unchanged')}
    return {'scan': ident, 'rows': rows, 'change': change, 'audio_missing': n_a_miss,
            'subs_missing': n_s_miss, 'videos': len(rows), 'words': sc['plan_words']}


def tracks_apply(store, ident, by):
    sc = _scan(ident)
    rows = sc.get('plan')
    if not rows:
        raise ValueError('make the plan first')
    words = sc.get('plan_words') or {}

    def work():
        todo = [(r, p) for r in rows for p in r['parts'] if 'audio' in p or 'subs' in p]
        step(0, len(todo), 'the default tracks are set')
        ok = fail = 0
        for i, (r, p) in enumerate(todo, 1):
            if stopped():
                step(line='stopped after %d of %d' % (i - 1, len(todo)))
                break
            params = {'allParts': 1}
            if 'audio' in p:
                params['audioStreamID'] = p['audio']
            if 'subs' in p:
                params['subtitleStreamID'] = p['subs']
            try:
                put('/library/parts/%s' % p['part'], params)
                ok += 1
                step(line='✓ %s' % r['label'])
            except PlexError as e:
                fail += 1
                step(line='✗ %s: %s' % (r['label'], e))
            step(i, None, r['label'])
        event(store, '%s set the default tracks (audio: %s, subtitles: %s) of %d video(s)%s'
              % (by, words.get('audio'), words.get('subs'), ok,
                 ', %d failed' % fail if fail else ''), by, 'info' if not fail else 'warn')
        return {'ok': ok, 'failed': fail}
    return job_start('tracks', 'the default audio and subtitles', by, work)


def tracks_rescan(store, ident, by, wait_s=90):
    """Scan Library Files, then read the items of the scan again until their
    new tracks are there (fresh .srt files beside the videos)."""
    sc = _scan(ident)

    def work():
        libs = sorted({d['lib'] for d in sc['items'] if d['lib']})
        for lib in libs:
            raw('GET', '/library/sections/%s/refresh' % lib)
        step(0, len(sc['items']), 'Plex scans the files; their tracks are read again')
        end = time.time() + wait_s
        before = {d['rk']: sum(len(st) for _p, st in d['parts']) for d in sc['items']}
        while time.time() < end and not stopped():
            time.sleep(4)
            md_map = metadata_many([d['rk'] for d in sc['items']])
            changed = 0
            for d in sc['items']:
                m = md_map.get(d['rk'])
                if m:
                    d['parts'] = list(iter_parts(m))
                    if sum(len(st) for _p, st in d['parts']) != before[d['rk']]:
                        changed += 1
            step(changed, None, '%d video(s) have new tracks so far' % changed)
        return tracks_scan_again(ident)
    return job_start('rescan', 'scanning the files for new tracks', by, work)


def tracks_scan_again(ident):
    sc = _scan(ident)
    audio_reg, sub_reg = {}, {}
    for d in sc['items']:
        for _pid, streams in d['parts']:
            for stt, reg in ((ST_AUDIO, audio_reg), (ST_SUBTITLE, sub_reg)):
                for vk, v in streams_variants(streams, stt).items():
                    if vk in reg:
                        reg[vk]['count'] += 1
                    else:
                        reg[vk] = v
    sc['audio'], sc['subs'] = finalize_variants(audio_reg), finalize_variants(sub_reg)
    pub = lambda vs: [{k: v for k, v in x.items() if k != 'sig'} for x in vs]  # noqa: E731
    return {'scan': ident, 'videos': len(sc['items']), 'audio': pub(sc['audio']),
            'subs': pub(sc['subs'])}


# ---------------------------------------------------------------------------
# Watched and unwatched
# ---------------------------------------------------------------------------
def set_watched(store, rks, watched, by):
    """A movie, show, season or episode - Plex marks what is under it too."""
    rks = [str(x) for x in rks if str(x).isdigit()]
    if not rks:
        raise ValueError('nothing picked')
    path = '/:/scrobble' if watched else '/:/unscrobble'

    def one(rk):
        st, _t = raw('GET', path, {'identifier': 'com.plexapp.plugins.library', 'key': rk})
        if st >= 400:
            raise PlexError('Plex said %s' % st)

    def finish(ok, fail):
        forget()
        event(store, '%s marked %d item(s) %s' % (by, ok, 'watched' if watched else 'unwatched')
              + (', %d failed' % fail if fail else ''), by)
        return {'ok': ok, 'failed': fail}
    if len(rks) <= 12:
        ok = fail = 0
        for rk in rks:
            try:
                one(rk)
                ok += 1
            except PlexError:
                fail += 1
        return {'done': finish(ok, fail)}

    def work():
        ok = fail = 0
        step(0, len(rks), 'marked %s' % ('watched' if watched else 'unwatched'))
        for i, rk in enumerate(rks, 1):
            if stopped():
                break
            try:
                one(rk)
                ok += 1
            except PlexError:
                fail += 1
            step(i)
        return finish(ok, fail)
    return {'job': job_start('watched', 'watched / unwatched', by, work)}


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def label_entries(lib):
    try:
        j = get('/library/sections/%s/label' % lib)
    except PlexError:
        return []
    return [{'id': d.get('key'), 'title': d.get('title')}
            for d in mc(j).get('Directory') or [] if d.get('title')]


def items_with(lib, flt, value, type_num):
    params = {flt: value}
    if type_num:
        params['type'] = type_num
    j = get('/library/sections/%s/all' % lib, params)
    return [str(m.get('ratingKey')) for m in mc(j).get('Metadata') or []]


def label_groups(lib, with_members=True):
    """The labels of a library, one per name whatever its letter case
    ('iPrima' / 'IPrima'), with what carries each."""
    s = section(lib)
    num = SECTION_TYPE_NUM.get(s['type'])
    grouped = {}
    for e in label_entries(lib):
        g = grouped.setdefault(e['title'].casefold(), {'title': e['title'], 'ids': [],
                                                       'names': []})
        g['ids'].append(e['id'])
        if e['title'] not in g['names']:
            g['names'].append(e['title'])
    out = []
    for cf in sorted(grouped, key=lambda c: grouped[c]['title'].lower()):
        g = grouped[cf]
        if with_members:
            rks = set()
            for lid in g['ids']:
                try:
                    rks |= set(items_with(lib, 'label', lid, num))
                except PlexError:
                    pass
            g['items'] = sorted(rks)
            g['count'] = len(rks)
        out.append(g)
    return out


def edit_labels(lib, rks, add=(), remove=(), type_num=None):
    s = section(lib)
    num = type_num or SECTION_TYPE_NUM.get(s['type'], 2)
    rks = [str(x) for x in rks if str(x).isdigit()]
    if not rks or (not add and not remove):
        return 0
    for i in range(0, len(rks), 100):
        batch = rks[i:i + 100]
        params = {'type': num, 'id': ','.join(batch), 'label.locked': 1}
        for n, tag in enumerate(add):
            params['label[%d].tag.tag' % n] = tag
        simple = [t for t in remove if ',' not in t]
        if simple:
            params['label[].tag.tag-'] = ','.join(simple)
        for tag in [t for t in remove if ',' in t]:
            put('/library/sections/%s/all' % lib, {'type': num, 'id': ','.join(batch),
                                                  'label.locked': 1, 'label[].tag.tag-': tag})
        put('/library/sections/%s/all' % lib, params)
    forget(lib)
    return len(rks)


def _clean_label(t):
    t = re.sub(r'\s+', ' ', str(t or '')).strip()
    if not t or len(t) > 120:
        raise ValueError('a label is 1 to 120 characters')
    return t


def labels_do(store, lib, action, by, rks=(), add=(), remove=(), old='', new=''):
    lib = str(lib)
    if action == 'edit':
        add = [_clean_label(x) for x in add or []]
        remove = [str(x) for x in remove or [] if str(x).strip()]
        n = edit_labels(lib, rks, add, remove)
        event(store, '%s changed the labels of %d item(s)%s%s' % (
            by, n, (': + ' + ', '.join(add)) if add else '',
            ('; - ' + ', '.join(remove)) if remove else ''), by)
        return {'done': n}
    groups = {g['title'].casefold(): g for g in label_groups(lib)}
    if action == 'rename':
        new = _clean_label(new)
        g = groups.get(str(old).casefold())
        if not g:
            raise ValueError('there is no label %s in this library' % old)
        targets = [x for x in (rks or g['items']) if x in set(g['items'])]
        if not targets:
            raise ValueError('nothing picked carries %s' % old)
        if g['title'].casefold() == new.casefold():
            # Only the letter case: in two steps, or Plex folds the new one
            # back onto the old tag and nothing changes.
            edit_labels(lib, targets, remove=g['names'])
            edit_labels(lib, targets, add=[new])
        else:
            edit_labels(lib, targets, add=[new], remove=g['names'])
        event(store, '%s renamed the label %s to %s on %d item(s)' % (
            by, g['title'], new, len(targets)), by)
        return {'done': len(targets)}
    if action == 'purge':
        g = groups.get(str(old).casefold())
        if not g:
            raise ValueError('there is no label %s in this library' % old)

        def work():
            touched = 0
            kinds = [('item', None), ('season', 3), ('episode', 4), ('collection', 18)]
            step(0, len(kinds), 'taken off everything in the library')
            for i, (kind, tnum) in enumerate(kinds, 1):
                num = tnum or SECTION_TYPE_NUM.get(section(lib)['type'], 2)
                got = set()
                for lid in g['ids']:
                    try:
                        got |= set(items_with(lib, 'label', lid, num))
                    except PlexError:
                        pass
                if got:
                    edit_labels(lib, sorted(got), remove=g['names'], type_num=num)
                    touched += len(got)
                    step(line='taken off %d %s(s)' % (len(got), kind))
                step(i)
            event(store, '%s cleaned the label %s out of the library (%d object(s))' % (
                by, g['title'], touched), by, 'warn')
            return {'touched': touched}
        return {'job': job_start('labels', 'cleaning a label out of the library', by, work)}
    raise ValueError('no such thing to do with labels')


# ---------------------------------------------------------------------------
# Titles in another language
# ---------------------------------------------------------------------------
_SCRIPT_RANGES = [
    ('Hangul (Korean)', ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F),
                         (0xAC00, 0xD7AF))),
    ('Kana (Japanese)', ((0x3040, 0x30FF), (0x31F0, 0x31FF))),
    ('Han (Chinese/Japanese)', ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))),
    ('Cyrillic', ((0x0400, 0x04FF), (0x0500, 0x052F))),
    ('Greek', ((0x0370, 0x03FF),)),
    ('Arabic', ((0x0600, 0x06FF), (0x0750, 0x077F))),
    ('Hebrew', ((0x0590, 0x05FF),)),
    ('Thai', ((0x0E00, 0x0E7F),)),
    ('Devanagari', ((0x0900, 0x097F),)),
]
_LANG_CHARS = [
    ('Czech', 'ěřů'), ('Polish', 'ąćęłńśźż'), ('Slovak', 'ľĺŕô'), ('Hungarian', 'őű'),
    ('Romanian', 'ăîșț'), ('Turkish', 'ğı'), ('Spanish', 'ñ¿¡'), ('Portuguese', 'ãõ'),
    ('German', 'ß'), ('Nordic', 'åæø'), ('French', 'œ'),
]
_LANG_CHARS_SHARED = [('Czech/Slovak', 'žščďťň'), ('German', 'äöü'),
                      ('French/Romance', 'çèêëàùû')]
_LANG_WORDS = {
    'Czech': {'a', 'o', 'u', 'v', 've', 'na', 'do', 'po', 'za', 'se', 'si', 'je', 'jsou', 'byl',
              'byla', 'bylo', 'byly', 'neni', 'nebylo', 'to', 'ta', 'ten', 'ty', 'jak', 'kdy',
              'kde', 'ze', 'ale', 'nebo', 'az', 'ano', 'ne', 'muj', 'moje', 'nas', 'nase',
              'jeho', 'jeji', 'jedna', 'jedno', 'jednou', 'vsechno', 'vsema', 'mezi', 'bez',
              'pro', 'pres', 'tak', 'jen', 'jeste', 'uz', 'kdyz', 'aby', 'laska', 'lasky',
              'srdce', 'zivot', 'svatba', 'sny', 'svet', 'svetlo', 'pripad', 'muz', 'zena',
              'divka', 'kluk', 'skola', 'mesto', 'mestecko', 'domu', 'beru', 'peklo', 'polibek',
              'hmota', 'duse'},
    'Slovak': {'sa', 'ako', 'ked', 'aby', 'alebo', 'velmi', 'zivot', 'laska', 'svet', 'dievca'},
    'English': {'the', 'an', 'of', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'with', 'my',
                'your', 'his', 'her', 'our', 'their', 'is', 'are', 'was', 'were', 'be', 'you',
                'we', 'they', 'it', 'this', 'that', 'from', 'by', 'about', 'love', 'story',
                'man', 'woman', 'girl', 'boy', 'life', 'world', 'house', 'night', 'day', 'who',
                'what', 'when', 'where', 'why', 'how', 'up', 'out', 'down', 'into', 'over',
                'under', 'again'},
    'German': {'der', 'die', 'das', 'und', 'ein', 'eine', 'ist', 'mit', 'von', 'fur', 'nicht',
               'auf', 'aus', 'dem', 'den', 'im', 'zu'},
    'French': {'le', 'les', 'un', 'une', 'des', 'et', 'du', 'au', 'aux', 'la', 'de', 'pour',
               'dans', 'avec', 'sur', 'est', 'qui', 'que', 'mon', 'ma'},
    'Spanish': {'el', 'los', 'las', 'una', 'y', 'del', 'que', 'en', 'la', 'de', 'casa', 'por',
                'para', 'con', 'es', 'mi', 'su', 'amor', 'vida'},
    'Italian': {'il', 'lo', 'gli', 'una', 'di', 'del', 'che', 'per', 'con', 'non', 'sono',
                'amore', 'vita'},
}
_WORD_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
PLEX_LANGS = [
    ('en', 'English'), ('cs', 'Czech'), ('ar', 'Arabic'), ('bg', 'Bulgarian'),
    ('ca', 'Catalan'), ('zh', 'Chinese'), ('hr', 'Croatian'), ('da', 'Danish'), ('nl', 'Dutch'),
    ('et', 'Estonian'), ('fi', 'Finnish'), ('fr', 'French'), ('de', 'German'), ('el', 'Greek'),
    ('he', 'Hebrew'), ('hi', 'Hindi'), ('hu', 'Hungarian'), ('id', 'Indonesian'),
    ('it', 'Italian'), ('ja', 'Japanese'), ('ko', 'Korean'), ('lv', 'Latvian'),
    ('lt', 'Lithuanian'), ('ms', 'Malay'), ('nb', 'Norwegian'), ('fa', 'Persian'),
    ('pl', 'Polish'), ('pt', 'Portuguese'), ('pt-BR', 'Portuguese (Brazil)'), ('ro', 'Romanian'),
    ('ru', 'Russian'), ('sr', 'Serbian'), ('sk', 'Slovak'), ('sl', 'Slovenian'),
    ('es', 'Spanish'), ('es-MX', 'Spanish (Mexico)'), ('sv', 'Swedish'), ('th', 'Thai'),
    ('tr', 'Turkish'), ('uk', 'Ukrainian'), ('vi', 'Vietnamese'),
]
LOCK_FIELD_NAMES = {
    'title': 'title', 'titleSort': 'sort title', 'originalTitle': 'original title',
    'summary': 'summary', 'tagline': 'tagline', 'studio': 'studio',
    'contentRating': 'content rating', 'originallyAvailableAt': 'release date', 'year': 'year',
    'rating': 'rating', 'audienceRating': 'audience rating', 'thumb': 'poster',
    'art': 'background', 'banner': 'banner', 'theme': 'theme', 'genre': 'genres',
    'collection': 'collections', 'label': 'labels', 'director': 'directors',
    'writer': 'writers', 'producer': 'producers', 'country': 'countries', 'similar': 'similar',
    'editionTitle': 'edition',
}
_SUFFIX_RE = re.compile(r'\s+[-–—]\s+([^-–—]{1,24})$')


def _deaccent(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def title_script(text):
    text = text or ''
    for name, ranges in _SCRIPT_RANGES:
        for ch in text:
            o = ord(ch)
            if any(lo <= o <= hi for lo, hi in ranges):
                return name
    return 'Latin' if all(ord(c) < 128 for c in text) else 'Latin (accented)'


def title_language(text):
    """A guess at the language of a title: the writing system decides the
    non-Latin ones; the Latin ones by their letters and little words - a
    hint, marked '?', not a verdict."""
    text = (text or '').strip()
    if not text:
        return 'unknown'
    script = title_script(text)
    if script.startswith('Hangul'):
        return 'Korean'
    if script.startswith('Kana'):
        return 'Japanese'
    if script.startswith('Han'):
        return 'Chinese/Japanese'
    if script in ('Cyrillic', 'Greek', 'Arabic', 'Hebrew', 'Thai', 'Devanagari'):
        return script
    low = text.lower()
    for lang, chars in _LANG_CHARS:
        if any(c in low for c in chars):
            return lang
    for lang, chars in _LANG_CHARS_SHARED:
        if any(c in low for c in chars):
            return lang
    words = {w.lower() for w in _WORD_RE.findall(_deaccent(text))}
    if words:
        hits = {lang: len(words & ws) for lang, ws in _LANG_WORDS.items()}
        best = max(hits, key=lambda k: hits[k])
        if hits[best] >= 2:
            rivals = sorted(k for k, v in hits.items() if v == hits[best])
            if len(rivals) > 1 and 'English' in rivals:
                rivals.remove('English')
            return rivals[0] + '?'
    return 'unknown (has accents)' if script == 'Latin (accented)' else 'unknown'


def lang_name_from_code(code):
    if not code:
        return None
    for c, name in PLEX_LANGS:
        if c.lower() == str(code).lower():
            return name
    base = str(code).split('-')[0].lower()
    for c, name in PLEX_LANGS:
        if c.lower() == base:
            return name
    return str(code)


def is_target_lang(detected, target_name):
    d = (detected or '').rstrip('?')
    if d == 'library default':
        return True
    if target_name == 'English':
        return d in ('English', 'unknown')
    return d == target_name or d.startswith(target_name + '/') or d.endswith('/' + target_name)


def title_suffix(title):
    m = _SUFFIX_RE.search(title or '')
    return ' - %s' % m.group(1).strip() if m else ''


def sort_title_differs(title, sort):
    if not sort:
        return False
    t, s = (title or '').strip(), sort.strip()
    if t == s:
        return False
    low = t.lower()
    for art in ('the ', 'a ', 'an '):
        if low.startswith(art) and t[len(art):].strip() == s:
            return False
    return True


def library_language(lib):
    s = section(lib)
    if s.get('language'):
        return s['language']
    try:
        for x in mc(get('/library/sections/%s/prefs' % lib)).get('Setting') or []:
            if x.get('id') in ('languageOverride', 'language') and x.get('value'):
                return x['value']
    except PlexError:
        pass
    return None


def titles_rows(lib):
    """Every item with the language its title looks to be in, its locked
    fields and whether it is one of a split (two items, one match)."""
    data = items(lib)
    rows = []
    guid_n = Counter(r['guid'] for r in data['items'] if r.get('guid'))
    for r in data['items']:
        rows.append({'rk': r['rk'], 'title': r['title'], 'year': r.get('year'),
                     'thumb': r.get('thumb'), 'sort': r.get('sort') or '',
                     'lang': title_language(r['title']), 'certain': False, 'src': 'guessed',
                     'guid': r.get('guid'), 'dup': bool(r.get('guid') and
                                                        guid_n[r['guid']] > 1),
                     'sort_differs': sort_title_differs(r['title'], r.get('sort'))})
    return {'section': data['section'], 'rows': rows,
            'library_language': lang_name_from_code(library_language(lib)) or ''}


def titles_read(store, lib, rks, by):
    """The exact metadata language and the locked fields of the items picked
    (one request each for the language - so a job)."""
    lib_name = lang_name_from_code(library_language(lib)) or 'library default'
    rks = [str(x) for x in rks]

    def work():
        out = {}
        step(0, len(rks), 'their metadata language is read')
        for i in range(0, len(rks), 40):
            md_map = metadata_many(rks[i:i + 40])
            for k in rks[i:i + 40]:
                m = md_map.get(k) or {}
                out[k] = {'locked': locked_fields(m), 'sort': m.get('titleSort') or ''}
        for i, k in enumerate(rks, 1):
            if stopped():
                break
            code = (item_prefs(k).get('languageOverride') or '').strip()
            out.setdefault(k, {})
            out[k].update(lang=lang_name_from_code(code) if code else lib_name,
                          src='the item' if code else 'the library', certain=True)
            step(i)
        return {'rows': out}
    return job_start('titles-read', 'reading the metadata language', by, work)


def matches(rk, lang=None, title=None, year=None, manual=1):
    params = {'manual': manual}
    if lang:
        params['language'] = lang
    if title:
        params['title'] = title
    if year:
        params['year'] = year
    st, text = raw('GET', '/library/metadata/%s/matches' % rk, params, accept='application/xml',
                   timeout=60)
    if st >= 400:
        raise PlexError('Plex said %s to the search' % st)
    out = []
    try:
        root = ET.fromstring(text)
        found = list(root.iter('SearchResult'))
    except ET.ParseError:
        found = []
        try:
            for m in mc(json.loads(text)).get('SearchResult') or []:
                out.append({'name': m.get('name'), 'guid': m.get('guid'), 'year': m.get('year'),
                            'score': int(m.get('score') or 0), 'thumb': m.get('thumb') or ''})
        except ValueError:
            pass
    for e in found:
        out.append({'name': e.get('name'), 'guid': e.get('guid'), 'year': e.get('year'),
                    'score': int(e.get('score') or 0), 'thumb': e.get('thumb') or ''})
    return out


def _protected(rows_by_rk, md_map, rks):
    out = {}
    for k in rks:
        why = []
        if 'title' in locked_fields(md_map.get(k)):
            why.append('locked')
        if (rows_by_rk.get(k) or {}).get('dup'):
            why.append('split')
        if why:
            out[k] = why
    return out


def titles_propose(store, lib, rks, lang, by):
    """For each item the name the agent has for it in `lang` - its own entry
    (the same guid) if there is one, else the best one, marked to be checked."""
    rks = [str(x) for x in rks]
    tr = {r['rk']: r for r in titles_rows(lib)['rows']}

    def work():
        md_map = metadata_many(rks)
        prot = _protected(tr, md_map, rks)
        props = []
        step(0, len(rks), 'the agent is asked for the titles')
        for i, k in enumerate(rks, 1):
            if stopped():
                break
            m = md_map.get(k) or {}
            cur = m.get('title') or (tr.get(k) or {}).get('title') or ''
            guid = m.get('guid')
            best = same = None
            try:
                for c in matches(k, lang=lang):
                    name = (c.get('name') or '').strip()
                    if not name or name == cur:
                        continue
                    if guid and c.get('guid') == guid:
                        same = dict(c, name=name, exact=True)
                        break
                    if best is None or c['score'] > best['score']:
                        best = dict(c, name=name, exact=False)
            except PlexError as e:
                step(line='%s: %s' % (cur, e))
            got = same or best
            if got:
                new = got['name']
                sfx = title_suffix(cur) if 'locked' in prot.get(k, []) else ''
                if sfx and not new.endswith(sfx):
                    new += sfx
                old_sort = m.get('titleSort') or ''
                props.append({'rk': k, 'old': cur, 'new': new, 'old_sort': old_sort,
                              'new_sort': new, 'do_sort': bool(old_sort),
                              'exact': got['exact'], 'year': got.get('year'),
                              'thumb': m.get('thumb') or '', 'risky': prot.get(k, [])})
            step(i, None, cur)
        return {'props': props, 'none': len(rks) - len(props)}
    return job_start('titles', 'looking up the titles', by, work)


def titles_apply(store, lib, method, by, items_=(), rks=(), lang='en', risky=False):
    """Title only (written and locked), the metadata language and a refresh,
    or a Fix Match to the item's own entry. Split entries and locked titles
    are held back unless `risky`."""
    lib = str(lib)
    s = section(lib)
    num = SECTION_TYPE_NUM.get(s['type'], 2)
    lname = lang_name_from_code(lang) or lang
    tr = {r['rk']: r for r in titles_rows(lib)['rows']}

    if method == 'title':
        todo = [x for x in items_ or [] if str(x.get('rk') or '').isdigit() and
                str(x.get('new') or '').strip()]
        if not todo:
            raise ValueError('no title to write')

        def work():
            ok = fail = 0
            step(0, len(todo), 'the titles are written')
            for i, x in enumerate(todo, 1):
                if stopped():
                    break
                params = {'type': num, 'id': str(x['rk']),
                          'title.value': str(x['new']).strip()[:300], 'title.locked': 1}
                if x.get('new_sort') not in (None, '') and x.get('do_sort'):
                    params['titleSort.value'] = str(x['new_sort']).strip()[:300]
                    params['titleSort.locked'] = 1
                try:
                    put('/library/sections/%s/all' % lib, params)
                    ok += 1
                    step(line='✓ %s → %s' % (x.get('old') or x['rk'], x['new']))
                except PlexError as e:
                    fail += 1
                    step(line='✗ %s: %s' % (x.get('old') or x['rk'], e))
                step(i)
            forget(lib)
            event(store, '%s wrote %d title(s) in %s%s' % (by, ok, s['title'],
                                                           ', %d failed' % fail if fail else ''),
                  by)
            return {'ok': ok, 'failed': fail}
        return job_start('titles-apply', 'writing the titles', by, work)

    rks = [str(x) for x in rks or [] if str(x).isdigit()]
    if not rks:
        raise ValueError('nothing picked')
    md_map = metadata_many(rks)
    prot = _protected(tr, md_map, rks)
    held = [] if risky else [k for k in rks if k in prot]
    go = [k for k in rks if k not in held]
    if not go:
        raise ValueError('every one picked is a split entry or has a locked title - tick '
                         '"also the risky ones" to change them anyway')

    if method == 'relanguage':
        def work():
            ok = fail = 0
            step(0, len(go), 'the metadata language becomes %s and they are refreshed' % lname)
            for i, k in enumerate(go, 1):
                if stopped():
                    break
                try:
                    put('/library/sections/%s/all' % lib, {'type': num, 'id': k,
                                                          'title.locked': 0})
                    put('/library/metadata/%s/prefs' % k, {'languageOverride': lang})
                    put('/library/metadata/%s/refresh' % k)
                    ok += 1
                except PlexError as e:
                    fail += 1
                    step(line='✗ %s: %s' % ((md_map.get(k) or {}).get('title') or k, e))
                step(i)
            forget(lib)
            event(store, '%s set the metadata language of %d item(s) in %s to %s' % (
                by, ok, s['title'], lname), by)
            return {'ok': ok, 'failed': fail, 'held': held}
        return job_start('titles-apply', 'the metadata language', by, work)

    if method == 'fixmatch':
        def work():
            ok = fail = none = 0
            step(0, len(go), 'each matched again to its own entry, in %s' % lname)
            for i, k in enumerate(go, 1):
                if stopped():
                    break
                m = md_map.get(k) or {}
                guid = m.get('guid')
                try:
                    same = next((c for c in matches(k, lang=lang)
                                 if guid and c.get('guid') == guid), None)
                    if not same:
                        none += 1
                        step(line='– %s: no entry of its own in %s - left as it is' % (
                            m.get('title') or k, lname))
                    else:
                        put('/library/metadata/%s/match' % k, {'guid': same['guid'],
                                                               'name': same['name']})
                        ok += 1
                        step(line='✓ %s → %s' % (m.get('title') or k, same['name']))
                except PlexError as e:
                    fail += 1
                    step(line='✗ %s: %s' % (m.get('title') or k, e))
                step(i)
            forget(lib)
            event(store, '%s matched %d item(s) in %s again to their own entry, in %s' % (
                by, ok, s['title'], lname), by, 'warn')
            return {'ok': ok, 'failed': fail, 'none': none, 'held': held}
        return job_start('titles-apply', 'Fix Match to their own entry', by, work)
    raise ValueError('no such way of changing a title')


def refresh_items(store, rks, by):
    rks = [str(x) for x in rks if str(x).isdigit()]
    if not rks:
        raise ValueError('nothing picked')

    def work():
        ok = 0
        step(0, len(rks), 'their metadata is refreshed (no new match)')
        for i, k in enumerate(rks, 1):
            if stopped():
                break
            try:
                put('/library/metadata/%s/refresh' % k)
                ok += 1
            except PlexError:
                pass
            step(i)
            time.sleep(0.2)
        forget()
        event(store, '%s refreshed the metadata of %d item(s)' % (by, ok), by)
        return {'ok': ok}
    return job_start('refresh', 'refreshing metadata', by, work)


def scan_library(store, lib, by, force=False):
    s = section(lib)
    st, _t = raw('GET', '/library/sections/%s/refresh' % lib, {'force': 1} if force else None)
    if st >= 400:
        raise PlexError('Plex said %s' % st)
    forget(lib)
    event(store, '%s asked Plex to %s %s' % (by, 'refresh all the metadata of' if force else
                                              'scan the files of', s['title']), by,
          'warn' if force else 'info')
    return {'message': 'Plex is %s %s' % ('refreshing all the metadata of' if force else
                                          'scanning the files of', s['title'])}


def person_titles(pid, name=''):
    found, seen = [], set()
    for s in video_sections():
        try:
            j = get('/library/sections/%s/all' % s['key'], {
                'type': SECTION_TYPE_NUM[s['type']], 'actor': pid})
        except PlexError:
            continue
        for m in mc(j).get('Metadata') or []:
            k = str(m.get('ratingKey'))
            if k not in seen:
                seen.add(k)
                c = _compact(m)
                c['lib'] = s['key']
                c['lib_title'] = s['title']
                found.append(c)
    found.sort(key=lambda x: (x.get('type') != 'movie', (x.get('title') or '').lower()))
    return {'name': name, 'items': found}


# ---------------------------------------------------------------------------
# The watchlist of the server's owner (Plex Discover, on the internet)
# ---------------------------------------------------------------------------
def _discover(method, path, params=None):
    url = DISCOVER_URL + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, method=method, headers=_headers())
    try:
        with _web(req, 30) as r:
            body = r.read().decode('utf-8', 'replace')
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, ''
    except (OSError, ValueError) as e:
        raise PlexError('Plex Discover (discover.provider.plex.tv) is not reachable: %s' %
                        getattr(e, 'reason', e))


_WL = {'at': 0.0, 'items': None}


def watchlist(fresh=False):
    if _WL['items'] is not None and not fresh and time.time() - _WL['at'] < 300:
        return _WL['items']
    out, start = [], 0
    while True:
        st, text = _discover('GET', '/library/sections/watchlist/all', {
            'includeCollections': 0, 'includeExternalMedia': 0,
            'X-Plex-Container-Start': start, 'X-Plex-Container-Size': 50})
        if st == 401:
            raise PlexError('Plex Discover refused the server\'s token for the watchlist')
        if st >= 400:
            raise PlexError('Plex Discover said %s to the watchlist' % st)
        c = mc(json.loads(text or '{}'))
        batch = c.get('Metadata') or []
        for m in batch:
            out.append({'dk': str(m.get('ratingKey')), 'guid': m.get('guid'),
                        'title': m.get('title'), 'year': m.get('year'), 'type': m.get('type'),
                        'thumb': m.get('thumb') or '', 'art': m.get('art') or '',
                        'summary': (m.get('summary') or '')[:400],
                        'cr': m.get('contentRating') or '', 'added': m.get('watchlistedAt')})
        got = start + len(batch)
        total = c.get('totalSize')
        if not batch or len(batch) < 50 or (total is not None and got >= total):
            break
        start = got
    _WL.update(at=time.time(), items=out)
    return out


def server_index():
    idx = {}
    for s in video_sections():
        try:
            data = items(s['key'])
        except PlexError:
            continue
        for it in data['items']:
            dk = discover_key(it.get('guid'))
            if dk:
                idx[dk] = {'rk': it['rk'], 'lib': s['key'], 'lib_title': s['title'],
                           'seen': bool(it.get('views')) or (it.get('leaves') and
                                                             it.get('seen') == it.get('leaves'))}
    return idx


def watchlist_view(fresh=False):
    wl = watchlist(fresh)
    idx = server_index()
    for w in wl:
        w['here'] = idx.get(w['dk'])
    return {'items': wl, 'at': _WL['at'], 'keys': sorted(w['dk'] for w in wl)}


def watchlist_do(store, action, dks, by):
    if action not in ('add', 'remove'):
        raise ValueError('add or remove')
    ok = fail = 0
    for dk in dks:
        if not re.match(r'^[0-9a-f]{12,40}$', str(dk)):
            fail += 1
            continue
        st, _t = _discover('PUT', '/actions/%s' % ('addToWatchlist' if action == 'add'
                                                   else 'removeFromWatchlist'),
                           {'ratingKey': dk})
        if st >= 400:
            fail += 1
        else:
            ok += 1
    _WL['at'] = 0
    event(store, '%s %s %d title(s) %s the watchlist' % (
        by, 'put' if action == 'add' else 'took', ok, 'on' if action == 'add' else 'off'), by)
    return {'ok': ok, 'failed': fail}


# ---------------------------------------------------------------------------
# Ratings from IMDb (OMDb), Rotten Tomatoes, TMDB, TVmaze, Trakt
# ---------------------------------------------------------------------------
def _score(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def current_rating(md, kind):
    v = _score(md.get('rating') if kind == 'critic' else md.get('audienceRating'))
    if v is not None:
        return v
    for r in md.get('Rating') or []:
        val = _score(r.get('value'))
        if val is None:
            continue
        if (kind == 'audience') == ((r.get('type') or 'critic') == 'audience'):
            return val
    return None


def ext_ids(md):
    out = {}
    for m in re.finditer(r'(tmdb|imdb|tvdb|themoviedb|thetvdb)://([^?/\s]+)', md.get('guid') or ''):
        out[m.group(1).replace('themoviedb', 'tmdb').replace('thetvdb', 'tvdb')] = m.group(2)
    for g in md.get('Guid') or []:
        gid = g.get('id') or ''
        if '://' in gid:
            k, v = gid.split('://', 1)
            out[k.lower()] = v
    return out


def _ext_json(url, headers=None, timeout=20):
    h = {'User-Agent': 'plexmon/ratings', 'Accept': 'application/json'}
    h.update(headers or {})
    try:
        with _web(urllib.request.Request(url, headers=h), timeout) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except (OSError, ValueError, urllib.error.HTTPError):
        return None


class _Pace(object):
    def __init__(self, calls, period):
        self.calls, self.period, self.stamps = calls, period, []

    def wait(self):
        now = time.time()
        self.stamps = [t for t in self.stamps if now - t < self.period]
        if len(self.stamps) >= self.calls:
            time.sleep(max(0.0, self.period - (now - self.stamps[0]) + 0.01))
        self.stamps.append(time.time())


class _TMDB(object):
    name = 'tmdb'
    BASE = 'https://api.themoviedb.org/3'

    def __init__(self, key):
        self.key, self.pace, self.cache = key, _Pace(40, 1.0), {}

    def _get(self, path, **params):
        params['api_key'] = self.key
        params.setdefault('language', 'en-US')
        self.pace.wait()
        return _ext_json('%s%s?%s' % (self.BASE, path, urllib.parse.urlencode(params)))

    def _id(self, ids, want):
        if ids.get('tmdb'):
            return ids['tmdb']
        for src, ext in (('imdb', 'imdb_id'), ('tvdb', 'tvdb_id')):
            if not ids.get(src):
                continue
            ck = (src, ids[src])
            if ck not in self.cache:
                d = self._get('/find/%s' % ids[src], external_source=ext) or {}
                res = (d.get('movie_results') if want == 'movie' else d.get('tv_results')) or []
                self.cache[ck] = str(res[0]['id']) if res else None
            if self.cache[ck]:
                return self.cache[ck]
        return None

    def show(self, ids):
        s = self._id(ids, 'tv')
        return _score((self._get('/tv/%s' % s) or {}).get('vote_average')) if s else None

    def movie(self, ids):
        s = self._id(ids, 'movie')
        return _score((self._get('/movie/%s' % s) or {}).get('vote_average')) if s else None

    def episode(self, ids, season, number):
        s = self._id(ids, 'tv')
        if not s:
            return None
        return _score((self._get('/tv/%s/season/%s/episode/%s' % (s, season, number)) or {})
                      .get('vote_average'))

    def season(self, ids, season):
        s = self._id(ids, 'tv')
        if not s:
            return None
        d = self._get('/tv/%s/season/%s' % (s, season)) or {}
        vals = [v for v in (_score(e.get('vote_average')) for e in d.get('episodes') or []) if v]
        return round(sum(vals) / len(vals), 1) if vals else None


class _OMDb(object):
    name = 'imdb'
    BASE = 'https://www.omdbapi.com/'

    def __init__(self, key):
        self.key, self.pace, self.cache = key, _Pace(10, 1.0), {}

    def raw_(self, ids, season=None, number=None):
        imdb = ids.get('imdb')
        if not imdb:
            return None
        ck = (imdb, season, number)
        if ck not in self.cache:
            p = {'i': imdb, 'apikey': self.key}
            if season is not None:
                p['Season'] = season
            if number is not None:
                p['Episode'] = number
            self.pace.wait()
            d = _ext_json('%s?%s' % (self.BASE, urllib.parse.urlencode(p)))
            self.cache[ck] = d if (d and d.get('Response') == 'True') else None
        return self.cache[ck]

    def show(self, ids):
        return _score((self.raw_(ids) or {}).get('imdbRating'))

    movie = show

    def episode(self, ids, season, number):
        return _score((self.raw_(ids, season, number) or {}).get('imdbRating'))

    def season(self, ids, season):
        return None


class _OMDbRT(object):
    name = 'rottentomatoes'

    def __init__(self, omdb):
        self.omdb = omdb

    @staticmethod
    def _rt(d):
        for r in (d or {}).get('Ratings') or []:
            if r.get('Source') == 'Rotten Tomatoes':
                m = re.match(r'\s*(\d+(?:\.\d+)?)\s*%', str(r.get('Value') or ''))
                if m:
                    return _score(float(m.group(1)) / 10.0)
        return None

    def show(self, ids):
        return self._rt(self.omdb.raw_(ids))

    movie = show

    def episode(self, ids, season, number):
        return self._rt(self.omdb.raw_(ids, season, number))

    def season(self, ids, season):
        return None


class _TVmaze(object):
    name = 'tvmaze'
    BASE = os.environ.get('PLEXMON_TVMAZE_URL') or 'https://api.tvmaze.com'

    def __init__(self):
        self.pace, self.cache = _Pace(15, 10.0), {}

    def _get(self, path, **params):
        self.pace.wait()
        return _ext_json(self.BASE + path + ('?' + urllib.parse.urlencode(params)
                                             if params else ''))

    def _id(self, ids):
        for src, param in (('tvdb', 'thetvdb'), ('imdb', 'imdb')):
            if not ids.get(src):
                continue
            ck = (src, ids[src])
            if ck not in self.cache:
                d = self._get('/lookup/shows', **{param: ids[src]}) or {}
                self.cache[ck] = str(d['id']) if d.get('id') else None
            if self.cache[ck]:
                return self.cache[ck]
        return None

    def show(self, ids):
        s = self._id(ids)
        return _score(((self._get('/shows/%s' % s) or {}).get('rating') or {})
                      .get('average')) if s else None

    def movie(self, ids):
        return None

    def episode(self, ids, season, number):
        s = self._id(ids)
        if not s:
            return None
        d = self._get('/shows/%s/episodebynumber' % s, season=season, number=number) or {}
        return _score((d.get('rating') or {}).get('average'))

    def season(self, ids, season):
        return None


class _Trakt(object):
    name = 'trakt'
    BASE = 'https://api.trakt.tv'

    def __init__(self, cid):
        self.headers = {'trakt-api-version': '2', 'trakt-api-key': cid}
        self.pace = _Pace(8, 1.0)

    def _get(self, path):
        self.pace.wait()
        return _ext_json(self.BASE + path, headers=self.headers)

    def show(self, ids):
        s = ids.get('imdb')
        return _score((self._get('/shows/%s/ratings' % s) or {}).get('rating')) if s else None

    def movie(self, ids):
        s = ids.get('imdb')
        return _score((self._get('/movies/%s/ratings' % s) or {}).get('rating')) if s else None

    def episode(self, ids, season, number):
        s = ids.get('imdb')
        if not s:
            return None
        return _score((self._get('/shows/%s/seasons/%s/episodes/%s/ratings' % (
            s, season, number)) or {}).get('rating'))

    def season(self, ids, season):
        return None


RATING_ORDER = ['imdb', 'rottentomatoes', 'trakt', 'tmdb', 'tvmaze']
AUDIENCE_ORDER = ['tmdb', 'tvmaze', 'rottentomatoes', 'trakt', 'imdb']


def rating_sources():
    return {'tmdb': bool(secret('plex_tmdb_key')), 'imdb': bool(secret('plex_omdb_key')),
            'rottentomatoes': bool(secret('plex_omdb_key')), 'trakt': bool(secret(
                'plex_trakt_id')), 'tvmaze': bool(int(cfg('plex_tvmaze', 1) or 0))}


def providers():
    out = []
    if secret('plex_tmdb_key'):
        out.append(_TMDB(secret('plex_tmdb_key')))
    if secret('plex_omdb_key'):
        o = _OMDb(secret('plex_omdb_key'))
        out += [o, _OMDbRT(o)]
    if int(cfg('plex_tvmaze', 1) or 0):
        out.append(_TVmaze())
    if secret('plex_trakt_id'):
        out.append(_Trakt(secret('plex_trakt_id')))
    return out


def _gather(provs, tn, ids, season=None, number=None):
    found = {}
    for p in provs:
        try:
            if tn == 4:
                v = p.episode(ids, season, number)
            elif tn == 3:
                v = p.season(ids, season)
            elif tn == 1:
                v = p.movie(ids)
            else:
                v = p.show(ids)
        except Exception:                               # noqa: BLE001
            v = None
        if v:
            found[p.name] = round(v, 1)
    return found


def _pick(found, order):
    for n in order:
        if found.get(n):
            return found[n], n
    return None, None


def badge_source(image, audience=False):
    """Which database a badge of Plex stands for: imdb://image.rating -> imdb,
    themoviedb://... -> tmdb, rottentomatoes://... -> rottentomatoes."""
    im = str(image or '').lower()
    if im.startswith('imdb'):
        return 'imdb'
    if im.startswith('themoviedb') or im.startswith('tmdb'):
        return 'tmdb'
    if im.startswith('rottentomatoes'):
        # The audience score of Rotten Tomatoes is not in OMDb: only the
        # Tomatometer (a critic score) can be put behind a tomato.
        return None if audience else 'rottentomatoes'
    return None


def _order(first, order):
    return ([first] if first else []) + [n for n in order if n != first]


def ratings_preview(store, lib, rks, overwrite, by, expand=True):
    """What the ratings of the items picked would become - a movie; a show is
    the show, its seasons and every episode; a season its episodes; an
    episode itself. Each number goes where the badge Plex shows for that
    field already stands for the same database (an IMDb badge gets IMDb's).
    Nothing is written."""
    provs = providers()
    if not provs:
        raise ValueError('no source of ratings: give a TMDB or OMDb key (or switch TVmaze on)')
    rks = [str(x) for x in rks if str(x).isdigit()]
    if not rks:
        raise ValueError('nothing picked')

    def row(m, label=None):
        t = m.get('type')
        r = {'rk': str(m.get('ratingKey')), 'tn': SECTION_TYPE_NUM.get(t, 1), 'md': m,
             'thumb': m.get('thumb') or m.get('parentThumb') or '', 'season': None,
             'number': None, 'gk': str(m.get('ratingKey')),
             'lib': str(m.get('librarySectionID') or lib or '')}
        if t == 'season':
            r.update(gk=str(m.get('parentRatingKey')), season=m.get('index'),
                     label=label or '%s · Season %s' % (m.get('parentTitle') or '', m.get('index')))
        elif t == 'episode':
            r.update(gk=str(m.get('grandparentRatingKey')), season=m.get('parentIndex'),
                     number=m.get('index'),
                     label=label or '%s · %s' % (m.get('grandparentTitle') or '', ep_label(m)))
        else:
            r['label'] = label or (m.get('title') or '') + (' (%s)' % m['year'] if m.get('year')
                                                            else '')
        return r

    def work():
        step(0, 0, 'what is picked is read')
        md_map = metadata_many(rks)
        rows, seen = [], set()
        for k in rks:
            m = md_map.get(k)
            if not m or k in seen:
                continue
            seen.add(k)
            rows.append(row(m))
            if not expand or m.get('type') not in ('show', 'season'):
                continue
            under = []
            if m.get('type') == 'show':
                under += [str(c.get('ratingKey')) for c in children(k)]
            under += [str(e.get('ratingKey')) for e in all_leaves(k)]
            more = metadata_many([u for u in under if u not in seen], stop=stopped)
            for u in under:
                if u in more and u not in seen:
                    seen.add(u)
                    rows.append(row(more[u]))
        # The ids another database knows live on the movie or the show.
        need = sorted({r['gk'] for r in rows if r['gk'] and r['gk'] not in md_map})
        if need:
            md_map.update(metadata_many(need))
        ids_by = {k: ext_ids(m) for k, m in md_map.items()}
        plan = []
        step(0, len(rows), 'the sources are asked')
        for i, r in enumerate(rows, 1):
            if stopped():
                break
            m = r.get('md') or {}
            cur, aud = current_rating(m, 'critic'), current_rating(m, 'audience')
            ids = ids_by.get(r['gk']) or ext_ids(m)
            show_md = md_map.get(r['gk']) or {}
            c_badge = badge_source(m.get('ratingImage') or show_md.get('ratingImage'))
            a_badge = badge_source(m.get('audienceRatingImage') or
                                   show_md.get('audienceRatingImage'), audience=True)
            e = {'rk': r['rk'], 'lib': r['lib'], 'label': r['label'], 'tn': r['tn'],
                 'cur': cur, 'aud': aud, 'thumb': r.get('thumb') or '',
                 'cur_src': c_badge, 'aud_src': a_badge, 'badges': rating_bits(m),
                 'status': 'no match' if not ids else 'kept'}
            if ids:
                found = _gather(provs, r['tn'], ids, r['season'], r['number'])
                # A field whose badge says a database takes that database's
                # number or nothing - TVmaze's 8.7 under an IMDb badge would
                # be a lie. A field without a badge takes the best there is.
                has_c = bool(m.get('ratingImage') or show_md.get('ratingImage'))
                has_a = bool(m.get('audienceRatingImage') or show_md.get('audienceRatingImage'))
                if c_badge:
                    nr, sr = (found[c_badge], c_badge) if found.get(c_badge) else (None, None)
                elif has_c and cur is not None:
                    nr, sr = None, None
                else:
                    nr, sr = _pick(found, RATING_ORDER)
                if a_badge:
                    na, sa = (found[a_badge], a_badge) if found.get(a_badge) else (None, None)
                elif has_a and aud is not None:
                    na, sa = None, None
                else:
                    na, sa = _pick(found, AUDIENCE_ORDER)
                if sr and sr == sa and not a_badge:
                    # One database in both fields says nothing twice: the
                    # audience field takes the next one.
                    na, sa = _pick({k: v for k, v in found.items() if k != sr},
                                   _order(a_badge, AUDIENCE_ORDER))
                e['found'] = found
                if overwrite or (cur is None and aud is None):
                    same = (nr is None or (cur is not None and abs(cur - nr) < 0.05)) and \
                        (na is None or (aud is not None and abs(aud - na) < 0.05))
                    if (nr is not None or na is not None) and not same:
                        e.update(new=nr, new_src=sr, new_aud=na, new_aud_src=sa,
                                 status='update' if (cur is not None or aud is not None)
                                 else 'add')
                    elif same and (nr is not None or na is not None):
                        e['status'] = 'same'
            plan.append(e)
            step(i, None, r['label'])
        ident = os.urandom(6).hex()
        _PLANS[ident] = {'at': time.time(), 'lib': str(lib), 'plan': plan}
        for old in [k for k, v in _PLANS.items() if time.time() - v['at'] > 3600]:
            _PLANS.pop(old, None)
        c = Counter(e['status'] for e in plan)
        return {'plan_id': ident, 'rows': plan, 'counts': dict(c),
                'sources': [p.name for p in provs]}
    return job_start('ratings', 'looking up the ratings', by, work)


def ratings_apply(store, plan_id, lock, only, by):
    pl = _PLANS.get(str(plan_id))
    if not pl:
        raise ValueError('that preview is gone - make it again')
    lib = pl['lib']
    want = set(str(x) for x in only) if only else None
    todo = [e for e in pl['plan'] if e['status'] in ('add', 'update') and
            (want is None or e['rk'] in want)]
    if not todo:
        raise ValueError('nothing to write')

    def work():
        ok = fail = 0
        step(0, len(todo), 'the ratings are written')
        for i, e in enumerate(todo, 1):
            if stopped():
                break
            params = {'type': e['tn'], 'id': e['rk']}
            if e.get('new') is not None:
                params['rating.value'] = '%.1f' % float(e['new'])
                params['rating.locked'] = '1' if lock else '0'
            if e.get('new_aud') is not None:
                params['audienceRating.value'] = '%.1f' % float(e['new_aud'])
                params['audienceRating.locked'] = '1' if lock else '0'
            try:
                put('/library/sections/%s/all' % (e.get('lib') or lib), params)
                ok += 1
            except PlexError as ex:
                fail += 1
                step(line='✗ %s: %s' % (e['label'], ex))
            step(i)
        forget()
        event(store, '%s wrote %d rating(s)%s%s' % (by, ok, ' (locked)' if lock else '',
                                                   ', %d failed' % fail if fail else ''), by)
        return {'ok': ok, 'failed': fail}
    return job_start('ratings-apply', 'writing the ratings', by, work)


# ---------------------------------------------------------------------------
# The whole state, for the top of the page
# ---------------------------------------------------------------------------
def state():
    out = {'token': bool(plex_token()), 'prefs': str(prefs_path()), 'url': _URL['url'] or
           base_urls()[0], 'reachable': False, 'sections': [], 'job': job_view(),
           'sources': rating_sources(), 'langs': PLEX_LANGS, 'img_cache': None}
    if not out['token']:
        out['error'] = 'no token of the Plex server in %s - is it claimed?' % prefs_path()
        return out
    try:
        out['server'] = server_info()
        out['reachable'] = True
        secs = video_sections()
        for s in secs:
            s['count'] = section_count(s)
        out['sections'] = secs
        out['activities'] = activities()
        out['playing'] = now_playing()
        out['url'] = _URL['url'] or out['url']
    except PlexError as e:
        out['error'] = str(e)
    return out
