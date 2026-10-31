#!/usr/bin/python3
"""plexmon_admin - the Settings side of plexmon.

plexmon's dashboard (/smart/) is open to the LAN and asks nothing of anybody.
Settings (/smart/settings/) is not: it is otp's administration, brought over
whole for a Plex server - the same engine, the same pages, the same words -
and kept to what a Plex server needs:

  * Accounts            who may open Settings, their roles, who is signed in,
                        who is locked out, the columns of the tables
  * Certificates and TLS the certificate nginx answers the Plex address with
                        (uploaded, requested, self-signed), how TLS is spoken,
                        and - where asked - Plex Media Server's own
  * Automatic certificates  Let's Encrypt / ACME, HTTP-01 and DNS-01
  * Upgrades            a new plexmon from the web or as a file, signed
  * About               the version, what it runs on, the language, the
                        console at login

One Python file, the standard library, SQLite. It runs inside the plexmon
daemon (plexmon.py starts it as a thread) and answers the PHP pages on
127.0.0.1 only, for whoever holds the token in /run/plexmon. Run by itself it
is the command line: `plexmon --help`, `plexmon --console`.

plexmon never runs in a cluster: there is one node, this machine.
"""
import argparse
import base64
import binascii
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import string
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import sqlite3

__version__ = '2.0.0'
# The oldest version this one may go back to (as otp's DOWNGRADE_FLOOR).
DOWNGRADE_FLOOR = '2.0.0'

# ---------------------------------------------------------------------------
# Paths and defaults
# ---------------------------------------------------------------------------
CONFIG_FILE = Path(os.environ.get('PLEXMON_ADMIN_CONFIG', '/etc/plexmon/config.json'))
STATE_DIR = Path(os.environ.get('PLEXMON_STATE', '/var/lib/plexmon'))
DB_FILE = STATE_DIR / 'plexmon.db'
RUN_DIR = Path(os.environ.get('PLEXMON_RUN', '/run/plexmon'))
TOKEN_FILE = RUN_DIR / 'api-token'
PORT_FILE = RUN_DIR / 'api-port'
LOG_DIR = Path(os.environ.get('PLEXMON_LOGDIR', '/var/log/plexmon'))
LIB_DIR = Path(os.environ.get('PLEXMON_LIB', str(Path(__file__).resolve().parent)))
WEB_ROOT = Path(os.environ.get('PLEXMON_WEB', '/var/www/html/smart'))
# Who the web pages run as: their group may read the API token.
WEB_GROUP = os.environ.get('PLEXMON_WEB_GROUP', '')

# nginx: the Plex reverse proxy, as the server has it (/etc/nginx/conf.d/plex.conf).
NGINX_DIR = Path(os.environ.get('PLEXMON_NGINX', '/etc/nginx'))
NGINX_SNIPPETS = NGINX_DIR / 'plexmon'
NGINX_TLS_CONF = NGINX_SNIPPETS / 'tls.conf'
NGINX_ACME_CONF = NGINX_SNIPPETS / 'acme.conf'
NGINX_SITE = Path(os.environ.get('PLEXMON_NGINX_SITE', str(NGINX_DIR / 'conf.d' / 'plex.conf')))
NGINX_CERT = Path(os.environ.get('PLEXMON_NGINX_CERT', str(NGINX_DIR / 'certs' / 'server.pem')))
NGINX_KEY = Path(os.environ.get('PLEXMON_NGINX_KEY', str(NGINX_DIR / 'certs' / 'server.key')))
NGINX_DH = NGINX_SNIPPETS / 'dhparam.pem'
ACME_WEBROOT = Path(os.environ.get('PLEXMON_ACME_ROOT', '/var/www/plexmon-acme'))
NGINX_BIN = os.environ.get('PLEXMON_NGINX_BIN', 'nginx')
SYSTEMCTL = os.environ.get('PLEXMON_SYSTEMCTL', 'systemctl')

# Plex Media Server's own settings, where its custom certificate is named.
PLEX_PREFS = Path(os.environ.get('PLEXMON_PLEX_PREFS',
                                 '/var/lib/plexmediaserver/Library/Application Support/'
                                 'Plex Media Server/Preferences.xml'))
PLEX_CERT_DIR = Path(os.environ.get('PLEXMON_PLEX_CERT_DIR', '/var/lib/plexmediaserver/plexmon'))
PLEX_SERVICE = os.environ.get('PLEXMON_PLEX_SERVICE', 'plexmediaserver')
SERVICE = os.environ.get('PLEXMON_SERVICE', 'plexmon')

ROLES_ALL = ('fulladmin', 'readonly')
ROLE_WORDS = {'fulladmin': 'full admin', 'readonly': 'read only'}

DEFAULTS = {
    # --- the administration pages -------------------------------------------
    'session_hours': 12,
    'session_idle_min': 120,
    'login_tries': 5,
    'login_wait_min': 15,
    'admin_2fa': 0,
    'admin_2fa_app': 1,
    # --- Plex Media Server's own certificate -----------------------------------
    'plex_cert': 0,
    'plex_cert_domain': '',
    'plex_cert_restart': 1,
    # --- certificates by ACME -------------------------------------------------
    'acme': 0,
    'acme_email': '',
    'acme_ca': 'letsencrypt',
    'acme_url': '',
    'acme_ca_pem': '',
    'acme_node_name': '',
    'acme_days': 30,
    'acme_key': 'ec',
    'acme_challenge': 'http-01',
    'acme_dns': 'manual',
    'acme_dns_server': '',
    'acme_dns_zone': '',
    'acme_dns_tsig_name': '',
    'acme_dns_tsig_alg': 'hmac-sha256',
    'acme_dns_tsig_secret': '',
    'acme_dns_token': '',
    'acme_dns_url': '',
    'acme_dns_user': '',
    'acme_dns_key': '',
    'acme_dns_subdomain': '',
    'acme_desec_domain': '',
    'acme_desec_token': '',
    'acme_dns_alias': '',
    'acme_dns_wait': 30,
    'acme_resolvers': '',
    # --- upgrades from the web --------------------------------------------------
    'upgrade_check': 1,
    'upgrade_downgrade': 0,
    'upgrade_url': 'https://github.com/falco81/PLEXMon/tree/main/release',
    # --- about ------------------------------------------------------------------
    'site_name': '',
    'ui_lang': 'cs',
    'api_port': 9848,
    'debug': False,
    'debug_api': 0,
    # --- the database ---------------------------------------------------------
    'retain_events_days': 365,
}

FILE_ONLY_KEYS = ()
SECRET_KEYS = ('acme_dns_tsig_secret', 'acme_dns_token', 'acme_dns_key', 'acme_desec_token')
SEALED_KEYS = SECRET_KEYS

TUNABLE = {
    'session_hours': (1, 168), 'session_idle_min': (5, 1440),
    'login_tries': (1, 100), 'login_wait_min': (1, 1440),
    'admin_2fa': (0, 1), 'admin_2fa_app': (0, 1),
    'plex_cert': (0, 1), 'plex_cert_restart': (0, 1),
    'upgrade_check': (0, 1), 'upgrade_downgrade': (0, 1),
    'acme': (0, 1), 'acme_ca': ('letsencrypt', 'letsencrypt-staging', 'custom'),
    'acme_days': (7, 60), 'acme_key': ('ec', 'rsa2048'),
    'acme_challenge': ('http-01', 'dns-01'),
    'acme_dns': ('manual', 'rfc2136', 'cloudflare', 'acmedns', 'desec'),
    'acme_dns_tsig_alg': ('hmac-sha256', 'hmac-sha512', 'hmac-sha384', 'hmac-sha224', 'hmac-sha1',
                          'hmac-md5'),
    'acme_dns_wait': (0, 600),
    'ui_lang': ('cs', 'en'), 'api_port': (1024, 65535), 'debug_api': (0, 1),
    'retain_events_days': (0, 36500),
}

CHOICE_WORDS = {
    'acme_challenge': {'http-01': 'HTTP-01 - the CA asks port 80 of the name',
                       'dns-01': 'DNS-01 - a TXT record in DNS (no port open to the internet)'},
    'acme_dns': {'manual': 'by hand - the record is shown here, you put it in',
                 'rfc2136': 'RFC 2136 dynamic update with a TSIG key (BIND, PowerDNS, Knot...)',
                 'cloudflare': 'Cloudflare, with an API token', 'acmedns': 'an acme-dns server',
                 'desec': 'deSEC.io (free), with an API token - a CNAME in your DNS'},
    'acme_ca': {'letsencrypt': 'Let’s Encrypt',
                'letsencrypt-staging': 'Let’s Encrypt - staging (to try it)',
                'custom': 'another ACME CA (step-ca, an internal CA...)'},
    'acme_key': {'ec': 'ECDSA P-256', 'rsa2048': 'RSA 2048'},
    'ui_lang': {'cs': 'Čeština', 'en': 'English'},
}

NAMES = {
    'session_hours': 'A sign-in lasts (h)',
    'session_idle_min': '...and may sit untouched (min)',
    'login_tries': 'Wrong passwords before a pause',
    'login_wait_min': '...and the pause (min)',
    'admin_2fa': 'A second step when signing in to these pages',
    'admin_2fa_app': '...with an authenticator app',
    'plex_cert': 'Plex Media Server uses this certificate too',
    'plex_cert_domain': 'Plex’s custom certificate domain',
    'plex_cert_restart': 'Restart Plex Media Server to take a new one',
    'acme': 'Certificates from an ACME CA',
    'acme_email': 'Mail of the account at the CA',
    'acme_ca': 'The CA',
    'acme_url': 'Its ACME directory',
    'acme_ca_pem': 'The CA’s own certificate',
    'acme_node_name': 'The node’s DNS name',
    'acme_days': 'Renewed when fewer days are left than',
    'acme_key': 'The key of the certificate',
    'acme_challenge': 'How the CA checks the name',
    'acme_dns': 'Who puts the TXT record in DNS',
    'acme_dns_server': 'The DNS server taking the updates',
    'acme_dns_zone': 'The zone',
    'acme_dns_tsig_name': 'The name of the TSIG key',
    'acme_dns_tsig_alg': 'Its algorithm',
    'acme_dns_tsig_secret': 'The TSIG secret',
    'acme_dns_token': 'The Cloudflare API token',
    'acme_dns_url': 'The acme-dns server',
    'acme_dns_user': 'acme-dns user',
    'acme_dns_key': 'acme-dns key',
    'acme_dns_subdomain': 'acme-dns subdomain',
    'acme_dns_wait': 'Wait for DNS to carry it (s)',
    'acme_resolvers': 'DNS servers for ACME',
    'acme_desec_domain': 'The domain at deSEC',
    'acme_desec_token': 'The deSEC API token',
    'acme_dns_alias': 'The TXT records go into',
    'upgrade_check': 'Look for a new version every day',
    'upgrade_downgrade': 'Offer going back to an older version',
    'upgrade_url': 'Where new versions are looked for',
    'site_name': 'What to call this installation',
    'ui_lang': 'Language of the pages',
    'api_port': 'API port (localhost)',
    'debug': 'Debug log',
    'debug_api': '...every API call',
    'retain_events_days': 'Events kept for (days)',
}

SAYS = {
    'session_hours': 'How long an administrator stays signed in, however busy.',
    'session_idle_min': 'How long a sign-in may sit without anything being asked before it '
                        'has to be done again.',
    'login_tries': 'Wrong passwords from one address before it has to wait.',
    'login_wait_min': 'How long it waits.',
    'admin_2fa': 'After the password, every account gives a code too - from an authenticator '
                 'app (Google or Microsoft Authenticator…). The first time, the account sets '
                 'it up. Accounts → Reset the second step, or plexmon --reset-2fa NAME, makes '
                 'it set it up again. Sign-ins already open end, but the one saving it.',
    'admin_2fa_app': 'A code from an app (Google or Microsoft Authenticator…) - the way the '
                     'second step is given here.',
    'plex_cert': 'The certificate nginx answers with is also handed to Plex Media Server as '
                 'its custom certificate (a PKCS#12 file in /var/lib/plexmediaserver/plexmon, '
                 'named in its Preferences.xml) - so the Plex apps that connect straight to '
                 'port 32400 by your own name see it too. Every new certificate - uploaded, '
                 'self-signed or renewed by ACME - goes there as well.',
    'plex_cert_domain': 'The name Plex announces for its custom certificate, '
                        '<code>plex.example.cz</code>. Empty: the certificate’s own name (CN).',
    'plex_cert_restart': 'Plex Media Server reads its certificate only when it starts: on, it '
                         'is restarted when a new one is put in (playing streams stop for a '
                         'moment); off, it takes the new one at its next start.',
    'acme': 'The certificate of this server ordered from the CA and renewed by itself - by '
            'HTTP-01 (port 80 of the name must reach this machine) or DNS-01. Off until '
            'switched on.',
    'acme_email': 'Where the CA writes when a certificate runs out unrenewed. Empty: none.',
    'acme_ca': 'Let’s Encrypt, its staging (certificates browsers do not trust - to try it), '
               'or another.',
    'acme_url': 'The directory of another ACME CA, '
                '<code>https://ca.example.cz/acme/acme/directory</code>.',
    'acme_ca_pem': 'The CA’s root (PEM), where the system does not trust it already.',
    'acme_node_name': 'The name the certificate is for - the one people type to reach Plex, '
                      '<code>plex.example.cz</code>. Empty: the name of the certificate it '
                      'has now, else its full host name.',
    'acme_days': 'Let’s Encrypt’s certificates last 90 days; 30 is what it advises.',
    'acme_key': 'ECDSA is smaller and quicker; RSA for old clients (old Plex apps, old '
                'televisions).',
    'acme_challenge': 'HTTP-01 needs port 80 of the name reachable from the internet (or from '
                      'the CA, for a CA of your own). DNS-01 needs nothing open: the CA reads a '
                      'TXT record - the way for a Plex server only the LAN reaches.',
    'acme_dns': 'By hand the order waits up to an hour for you; the others renew by themselves.',
    'acme_dns_server': 'The primary DNS server of the zone, <code>ns1.example.cz</code> or with '
                       'a port <code>10.0.0.53:53</code>.',
    'acme_dns_zone': 'Where the record goes, <code>example.cz</code>. Empty: as the server '
                     'finds it.',
    'acme_dns_tsig_name': 'As the DNS server knows it, <code>plexmon-acme.</code>; allowed to '
                          'update _acme-challenge records only.',
    'acme_dns_tsig_alg': 'As the key was made (tsig-keygen makes hmac-sha256).',
    'acme_dns_tsig_secret': 'The key’s secret (base64). Kept sealed; written to a file only '
                            'plexmon reads for the moment nsupdate runs.',
    'acme_dns_token': 'A token with Zone - DNS - Edit for the zone. Kept sealed.',
    'acme_dns_url': '<code>https://auth.acme-dns.example.cz</code>; _acme-challenge.NAME is a '
                    'CNAME to the subdomain below.',
    'acme_dns_user': 'From the registration at acme-dns.',
    'acme_dns_key': 'From the registration at acme-dns. Kept sealed.',
    'acme_dns_subdomain': 'From the registration at acme-dns.',
    'acme_dns_wait': 'How long the record is given to reach every DNS server before the CA is '
                     'asked.',
    'acme_resolvers': 'The DNS servers asked for everything on this page: the address of the '
                      'CA and of the DNS API, <i>Check DNS</i>, <i>Try port 80</i>, and whether '
                      'the TXT record can be seen before the CA is asked - '
                      '<code>1.1.1.1, 9.9.9.9</code> to see what the internet sees. Empty: the '
                      'machine’s own (/etc/resolv.conf).',
    'acme_desec_domain': 'Your domain at deSEC, <code>example.dedyn.io</code> (free) or one of '
                         'your own whose DNS is there. The records go into it; your DNS has a '
                         'CNAME to it for each name - <i>In your DNS</i> above says which.',
    'acme_desec_token': 'A token from deSEC (Token management) - one that may change this '
                        'domain’s records. Kept sealed.',
    'acme_dns_alias': 'A zone the records go into instead of the name’s own '
                      '(<code>acme.example.net</code>), each name with a CNAME to it in your '
                      'DNS - <i>In your DNS</i> above says which. Empty: _acme-challenge.NAME '
                      'itself.',
    'upgrade_check': 'Once a day (and when Look now is pressed) the address below is asked for '
                     'plexmon-X.Y.Z.tar.gz packages; a newer one than runs here is offered on '
                     'this page - put in with one click, after its signature has been checked '
                     'like any package sent by hand. Nothing is put in by itself.',
    'upgrade_downgrade': 'Off: nothing on these pages offers going back to an older version. '
                         'On: Settings → Upgrades shows <i>Going back to an older version</i> - '
                         'only the versions it is safe to go back to, each with what changed in '
                         'it. On the server, root can always: plexmon --downgrade.',
    'upgrade_url': 'A GitHub folder (<code>https://github.com/OWNER/REPO/tree/BRANCH/FOLDER</code>), '
                   'a web page or folder listing that links to the packages, or one package\'s '
                   'own address. HTTPS only; a proxy is taken from https_proxy in the service\'s '
                   'environment.',
    'site_name': 'The name at the top of Settings. Empty: the machine’s own name.',
    'ui_lang': 'The language every page opens in - the dashboard too - until somebody chooses '
               'another for themselves.',
    'api_port': 'The port on 127.0.0.1 the Settings pages ask this daemon on. The pages read it '
                'from <code>/run/plexmon/api-port</code>, written when the daemon starts.',
    'debug': 'Write everything into <code>/var/log/plexmon/plexmon.log</code>. Loud: for finding '
             'something out, not for leaving on.',
    'debug_api': 'Every API call the web pages make.',
    'retain_events_days': 'How long the events (sign-ins, changes, certificates) are kept. '
                          '0: for ever.',
}
SAYS_SHORT = {}

# Which settings sit on which page of Settings, in order.
PANES = {
    'people': ['session_hours', 'session_idle_min', 'login_tries', 'login_wait_min',
               'admin_2fa', 'admin_2fa_app'],
    'web': ['plex_cert', 'plex_cert_domain', 'plex_cert_restart'],
    'acme': ['acme', 'acme_email', 'acme_ca', 'acme_url', 'acme_ca_pem',
             'acme_node_name', 'acme_days', 'acme_key', 'acme_challenge', 'acme_dns',
             'acme_dns_server', 'acme_dns_zone', 'acme_dns_tsig_name', 'acme_dns_tsig_alg',
             'acme_dns_tsig_secret', 'acme_dns_token', 'acme_dns_url', 'acme_dns_user',
             'acme_dns_key', 'acme_dns_subdomain', 'acme_desec_domain', 'acme_desec_token',
             'acme_dns_alias', 'acme_dns_wait', 'acme_resolvers'],
    'upgrade': ['upgrade_check', 'upgrade_url', 'upgrade_downgrade'],
    'about': ['site_name', 'ui_lang', 'api_port', 'retain_events_days', 'debug', 'debug_api'],
}

ONLY_WHEN = {
    'admin_2fa_app': ['admin_2fa', '1'],
    'plex_cert_domain': ['plex_cert', '1'], 'plex_cert_restart': ['plex_cert', '1'],
    'acme_email': ['acme', '1'], 'acme_ca': ['acme', '1'],
    'acme_url': ['acme_ca', 'custom'], 'acme_ca_pem': ['acme_ca', 'custom'],
    'acme_node_name': ['acme', '1'], 'acme_days': ['acme', '1'], 'acme_key': ['acme', '1'],
    'acme_challenge': ['acme', '1'], 'acme_dns': ['acme_challenge', 'dns-01'],
    'acme_dns_wait': ['acme_challenge', 'dns-01'],
    'acme_dns_server': ['acme_dns', 'rfc2136'], 'acme_dns_zone': ['acme_dns', 'rfc2136'],
    'acme_dns_tsig_name': ['acme_dns', 'rfc2136'], 'acme_dns_tsig_alg': ['acme_dns', 'rfc2136'],
    'acme_dns_tsig_secret': ['acme_dns', 'rfc2136'], 'acme_dns_token': ['acme_dns', 'cloudflare'],
    'acme_dns_url': ['acme_dns', 'acmedns'], 'acme_dns_user': ['acme_dns', 'acmedns'],
    'acme_dns_key': ['acme_dns', 'acmedns'], 'acme_dns_subdomain': ['acme_dns', 'acmedns'],
    'acme_desec_domain': ['acme_dns', 'desec'], 'acme_desec_token': ['acme_dns', 'desec'],
    'acme_dns_alias': ['acme_dns', 'acmedns-not'], 'acme_resolvers': ['acme', '1'],
    'debug_api': ['debug', '1'],
}

SETTING_TALL = ('acme_ca_pem',)

CONFIG = dict(DEFAULTS)
FILE_CONFIG = dict(DEFAULTS)
FILE_KEYS = set()
BAD_CONFIG = []


class BadSetting(ValueError):
    """A value that cannot be read as the setting it was given for."""


def _coerce(current, value):
    """A value made to look like the default beside it - or refused."""
    if isinstance(current, bool):
        if isinstance(value, str):
            said = value.strip().lower()
            if said in ('1', 'true', 'yes', 'on'):
                return True
            if said in ('0', 'false', 'no', 'off', ''):
                return False
            raise BadSetting('%r is not yes or no' % value)
        if value is None:
            raise BadSetting('a yes-or-no setting cannot be nothing')
        return bool(value)
    if isinstance(current, int):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise BadSetting('a number is needed, and nothing was given')
        if isinstance(value, bool):
            return int(value)
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            raise BadSetting('%r is not a number' % (value,))
    if isinstance(current, str):
        if value is None:
            raise BadSetting('nothing was given')
        if isinstance(value, (list, tuple)):
            return ', '.join(str(one).strip() for one in value if str(one).strip())
        if isinstance(value, (dict, set)):
            raise BadSetting('%r is not something this setting can hold' % (value,))
        if isinstance(value, bool):
            return 'yes' if value else ''
        return str(value)
    return value


def check_setting(key, value):
    """The value as it will be kept, or BadSetting saying why not."""
    if key not in DEFAULTS:
        raise BadSetting('%s is not a setting this version knows' % key)
    got = _coerce(DEFAULTS[key], value)
    shape = TUNABLE.get(key)
    if isinstance(shape, tuple) and shape and isinstance(shape[0], str):
        if str(got) not in shape:
            raise BadSetting('%s: %r is not one of %s' % (key, got, ', '.join(shape)))
    elif isinstance(shape, tuple) and len(shape) == 2:
        if not (shape[0] <= int(got) <= shape[1]):
            raise BadSetting('%s must be between %d and %d' % (key, shape[0], shape[1]))
    if isinstance(got, str) and key not in SECRET_KEYS:
        tall = key in SETTING_TALL
        most = 20000 if tall else 500
        if len(got) > most:
            raise BadSetting('%s: %d characters at most' % (key, most))
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]' if tall else r'[\x00-\x1f\x7f]', got):
            raise BadSetting('%s: no line breaks or other control characters' % key)
    if key in SECRET_KEYS and any(ord(ch) < 32 or ord(ch) == 127 for ch in str(got or '')):
        raise BadSetting('%s: no line breaks or other control characters' % key)
    if key == 'acme_resolvers':
        for one in [w for w in re.split(r'[\s,;]+', str(got)) if w]:
            try:
                ipaddress.ip_address(one)
            except ValueError:
                raise BadSetting('%s: %r is not the address of a DNS server' % (key, one))
    if key == 'acme_email' and str(got).strip() and \
            not re.match(r'^[^@\s<>"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}\Z', str(got).strip()):
        raise BadSetting('acme_email: a mail address')
    if key == 'acme_url' and str(got).strip() and \
            not re.match(r'^https://[^\s"<>]+\Z', str(got).strip()):
        raise BadSetting('acme_url: an address starting https://')
    if key == 'acme_dns_url' and str(got).strip() and \
            not re.match(r'^(https://[^\s"<>]+|http://(127\.0\.0\.1|localhost)(:\d+)?(/[^\s"<>]*)?)\Z',
                         str(got).strip()):
        raise BadSetting('acme_dns_url: an address starting https://')
    if key in ('acme_dns_server',) and str(got).strip() and \
            not re.match(r'^[A-Za-z0-9.\-:\[\]]{1,260}\Z', str(got).strip()):
        raise BadSetting('acme_dns_server: a name or an address, :port if not 53')
    if key in ('acme_desec_domain', 'acme_dns_alias', 'plex_cert_domain') and str(got).strip() and \
            not re.match(r'^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\.?\Z',
                         str(got).strip()):
        raise BadSetting('%s: a DNS name, like plex.example.cz' % key)
    if key == 'acme_desec_token' and str(got).strip() and not is_masked(got) and \
            not re.match(r'^[A-Za-z0-9_.-]{20,100}\Z', str(got).strip()):
        raise BadSetting('acme_desec_token: the token as deSEC shows it')
    if key in ('acme_dns_zone', 'acme_dns_tsig_name') and str(got).strip() and \
            not re.match(r'^[A-Za-z0-9][A-Za-z0-9.\-_]{0,252}\Z', str(got).strip()):
        raise BadSetting('%s: a DNS name' % key)
    if key == 'acme_dns_tsig_secret' and str(got).strip() and not is_masked(got) and \
            not re.match(r'^[A-Za-z0-9+/=]{8,512}\Z', str(got).strip()):
        raise BadSetting('acme_dns_tsig_secret: the secret in base64, as tsig-keygen makes it')
    if key in ('acme_dns_user', 'acme_dns_subdomain') and str(got).strip() and \
            not re.match(r'^[A-Za-z0-9-]{1,64}\Z', str(got).strip()):
        raise BadSetting('%s: letters, digits and dashes' % key)
    if key == 'acme_ca_pem' and str(got).strip() and '-----BEGIN CERTIFICATE-----' not in str(got):
        raise BadSetting('acme_ca_pem: a certificate in PEM, -----BEGIN CERTIFICATE-----')
    if key == 'acme_node_name' and str(got).strip() and \
            not re.match(r'^[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z', str(got).strip()):
        raise BadSetting('acme_node_name: a DNS name')
    if key == 'upgrade_url' and str(got).strip() and \
            not re.match(r'^https://[^\s]+\Z', str(got).strip()):
        raise BadSetting('upgrade_url: an address starting https://')
    if key == 'site_name' and len(str(got)) > 80:
        raise BadSetting('site_name: 80 characters at most')
    return got


CONFIG_LOADED = [False]


def load_config(path=None, saved=None):
    """The defaults, with config.json over them and anything saved over that."""
    global CONFIG, FILE_CONFIG, FILE_KEYS
    where = Path(path) if path else CONFIG_FILE
    fresh = dict(DEFAULTS)
    named, refused, unknown = set(), [], []
    try:
        with open(where, encoding='utf-8') as f:
            given = json.load(f)
        if not isinstance(given, dict):
            raise ValueError('the config file is not an object')
        for key, value in given.items():
            if key in fresh:
                try:
                    fresh[key] = _coerce(fresh[key], value)
                    named.add(key)
                except BadSetting as bad:
                    refused.append('%s (%s)' % (key, bad))
            elif not key.startswith('_'):
                unknown.append(key)
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as e:
        if CONFIG_LOADED[0]:
            log('%s could not be read (%s): the settings in effect are kept until it is '
                'mended' % (where, e), 'error')
            return CONFIG
        sys.stderr.write('plexmon: %s could not be read: %s - the built-in defaults are used\n'
                         % (where, e))
    CONFIG_LOADED[0] = True
    FILE_CONFIG = dict(fresh)
    FILE_KEYS = named
    whole = dict(fresh)
    for key, value in (saved or {}).items():
        if key in DEFAULTS and key not in FILE_ONLY_KEYS:
            try:
                whole[key] = _coerce(DEFAULTS[key], value)
            except BadSetting:
                pass
    CONFIG = whole
    if unknown:
        sys.stderr.write('plexmon: %s holds settings this version does not know: %s\n'
                         % (where, ', '.join(sorted(unknown)[:12])))
    BAD_CONFIG[:] = sorted(refused)
    return CONFIG


def debugging(what=''):
    if not CONFIG.get('debug'):
        return False
    return not what or bool(CONFIG.get('debug_' + what))


# ---------------------------------------------------------------------------
# Small things, said once
# ---------------------------------------------------------------------------
def human_span(seconds):
    s = max(0, int(seconds or 0))
    if s < 60:
        return '%d s' % s
    if s < 3600:
        return '%d min' % (s // 60)
    if s < 86400:
        return '%d h %d min' % (s // 3600, (s % 3600) // 60)
    days = s // 86400
    return '%s %d h' % ('1 day' if days == 1 else '%d days' % days, (s % 86400) // 3600)


def stamp(ts):
    if not ts:
        return '-'
    return datetime.fromtimestamp(float(ts)).strftime('%d-%m-%Y %H:%M:%S')


def short_host():
    try:
        return socket.gethostname().split('.')[0] or 'plexmon'
    except OSError:
        return 'plexmon'


def node_name():
    """plexmon is one machine: its name is the machine's short host name."""
    return short_host()


def node_address():
    """This machine's address towards the LAN (the one a default route leaves by)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('192.0.2.1', 9))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return '127.0.0.1'


def write_replacing(path, text, mode=0o640, owner=None):
    """A file written beside itself and moved into place, never half there."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = '%s.new-%d-%s' % (path, os.getpid(), secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        if owner is not None:
            try:
                os.fchown(fd, owner[0], owner[1])
            except OSError:
                pass
        with os.fdopen(fd, 'wb' if isinstance(text, bytes) else 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def plain_ip(text, what='address'):
    try:
        return str(ipaddress.ip_address(str(text).strip().strip('[]')))
    except ValueError:
        raise ValueError('%r is not an %s' % (text, what))


def secret_shown(plain):
    """What the page sees of a secret: dots, as many as there are letters (at
    most 12), so a box is seen to be filled - never the secret itself."""
    return '•' * min(12, len(plain)) if plain else ''


def is_masked(value):
    return isinstance(value, str) and value != '' and set(value) == {'•'}


# ---------------------------------------------------------------------------
# The log this daemon writes about itself
# ---------------------------------------------------------------------------
_LOG_LOCK = threading.Lock()
_LOG_WANTED = [False]           # this process writes plexmon.log (the daemon)
_LOG_TO_STDERR = [True]
LOG_FILE = LOG_DIR / 'plexmon.log'


def log(words, level='info'):
    if level == 'debug' and not debugging():
        return
    line = '%s %-5s %s' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), level, words)
    with _LOG_LOCK:
        if _LOG_WANTED[0]:
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                with open(str(LOG_FILE), 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except OSError:
                pass
        if _LOG_TO_STDERR[0] and (_LOG_WANTED[0] or level in ('warn', 'error')):
            try:
                sys.stderr.write('plexmon-admin: %s %s\n' % (level, words))
            except Exception:                           # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Secrets kept in the database, sealed (otp's, as isemon seals its passwords)
# ---------------------------------------------------------------------------
SEALED = 'sealed:1:'
SEALED2 = 'sealed:2:'
_KEY_CACHE = {}
_KEY_LOCK = threading.Lock()


def sealing_key_path():
    return Path(STATE_DIR) / 'secrets.key'


def sealing_key(make=False):
    path = sealing_key_path()
    try:
        st = os.stat(str(path))
        mark = (st.st_mtime_ns, st.st_size, st.st_ino)
    except FileNotFoundError:
        mark = None
    except OSError:
        mark = 'unreadable'
    if mark is not None and mark != 'unreadable':
        got = _KEY_CACHE.get(str(path))
        if got and got[0] == mark:
            return got[1]
    try:
        key = bytes.fromhex(path.read_text(encoding='ascii').strip())
    except FileNotFoundError:
        key = None
    except (OSError, ValueError):
        key = b''
    if key and len(key) == 32:
        if mark and mark != 'unreadable':
            _KEY_CACHE[str(path)] = (mark, key)
        return key
    if not make:
        return b''
    if key is not None:
        raise OSError('%s is there but cannot be read as a key - it is never replaced, '
                      'because every secret sealed with it would be lost' % path)
    with _KEY_LOCK:
        key = secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = '%s.new-%d-%s' % (path, os.getpid(), secrets.token_hex(4))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as f:
            f.write(key.hex() + '\n')
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, str(path))
        except FileExistsError:
            os.unlink(tmp)
            return sealing_key(make=False) or b''
        os.unlink(tmp)
        log('a new key to seal secrets with was made: %s - keep a copy of it beside every '
            'copy of the database' % path, 'warn')
    return key


def _seal_stream(key, nonce, size):
    out = bytearray()
    block = 0
    while len(out) < size:
        out += hmac.new(key, b'stream' + nonce + block.to_bytes(8, 'big'),
                        hashlib.sha256).digest()
        block += 1
    return bytes(out[:size])


def seal_secret(text, bind=None):
    key = sealing_key(make=True)
    if not key:
        raise OSError('no key to seal with')
    data = str(text).encode('utf-8')
    nonce = secrets.token_bytes(16)
    hide = hmac.new(key, b'hide', hashlib.sha256).digest()
    sign = hmac.new(key, b'sign', hashlib.sha256).digest()
    body = bytes(a ^ b for a, b in zip(data, _seal_stream(hide, nonce, len(data))))
    if bind is not None:
        tag = hmac.new(sign, b'2|' + str(bind).encode('utf-8') + b'|' + nonce + body,
                       hashlib.sha256).digest()[:16]
        return SEALED2 + base64.urlsafe_b64encode(nonce + body + tag).decode('ascii')
    tag = hmac.new(sign, nonce + body, hashlib.sha256).digest()[:16]
    return SEALED + base64.urlsafe_b64encode(nonce + body + tag).decode('ascii')


def unseal_secret(value, bind=None):
    if not isinstance(value, str):
        return value
    if value.startswith(SEALED2):
        v2, prefix = True, SEALED2
    elif value.startswith(SEALED):
        v2, prefix = False, SEALED
    else:
        return value
    if v2 and bind is None:
        return None
    key = sealing_key()
    try:
        raw = base64.urlsafe_b64decode(value[len(prefix):].encode('ascii'))
    except (ValueError, TypeError):
        return None
    if not key or len(raw) < 32:
        return None
    nonce, body, tag = raw[:16], raw[16:-16], raw[-16:]
    sign = hmac.new(key, b'sign', hashlib.sha256).digest()
    signed = (b'2|' + str(bind).encode('utf-8') + b'|' if v2 else b'') + nonce + body
    if not hmac.compare_digest(tag, hmac.new(sign, signed, hashlib.sha256).digest()[:16]):
        return None
    hide = hmac.new(key, b'hide', hashlib.sha256).digest()
    try:
        return bytes(a ^ b for a, b in zip(body, _seal_stream(hide, nonce, len(body)))
                     ).decode('utf-8')
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# Passwords of the accounts
# ---------------------------------------------------------------------------
ACCOUNT_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\Z')


def password_hash(password, salt=None, rounds=310000):
    salt = salt or secrets.token_bytes(16)
    got = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'), salt, rounds)
    return 'pbkdf2_sha256$%d$%s$%s' % (rounds, base64.b64encode(salt).decode('ascii'),
                                       base64.b64encode(got).decode('ascii'))


def password_ok(password, stored):
    try:
        kind, rounds, salt, want = str(stored or '').split('$')
        if kind != 'pbkdf2_sha256':
            return False
        got = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'),
                                  base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(got, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


def password_problem(password):
    p = str(password or '')
    if len(p) < 8:
        return 'a password needs at least eight characters'
    if len(p) > 200:
        return 'that password is longer than anybody types'
    return ''


DUMMY_PW_HASH = password_hash(secrets.token_urlsafe(18), rounds=310000)


# ---------------------------------------------------------------------------
# One-time passwords (RFC 4226 / RFC 6238) and QR codes - for the second step
# of signing in to Settings. otp's own, taken whole.
# ---------------------------------------------------------------------------
HASHES = {'SHA1': hashlib.sha1, 'SHA256': hashlib.sha256, 'SHA512': hashlib.sha512}


def b32_clean(text):
    """A base32 key as somebody may have written it - small letters, spaces,
    dashes, no padding - as the bytes it stands for. ValueError if it is not
    base32 at all."""
    said = re.sub(r'[\s\-]', '', str(text or '')).upper().rstrip('=')
    if not said or not re.match(r'^[A-Z2-7]+\Z', said):
        raise ValueError('that is not a base32 key (A-Z and 2-7)')
    pad = '=' * ((8 - len(said) % 8) % 8)
    return base64.b32decode(said + pad)


def b32_new(size=20):
    """A fresh key: 160 bits, the size RFC 4226 recommends, as base32."""
    return base64.b32encode(secrets.token_bytes(size)).decode('ascii').rstrip('=')


def hotp(key, counter, digits=6, algorithm='SHA1'):
    """The code for one counter value, as a string of `digits` digits."""
    if isinstance(key, str):
        key = b32_clean(key)
    mac = hmac.new(key, struct.pack('>Q', int(counter)),
                   HASHES.get(str(algorithm).upper(), hashlib.sha1)).digest()
    at = mac[-1] & 0x0F
    code = struct.unpack('>I', mac[at:at + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** int(digits))).zfill(int(digits))


def totp_step(when=None, period=30):
    return int((time.time() if when is None else float(when)) // int(period))


def totp(key, when=None, digits=6, period=30, algorithm='SHA1'):
    return hotp(key, totp_step(when, period), digits, algorithm)


def totp_match(key, code, when=None, digits=6, period=30, algorithm='SHA1', window=1):
    """Which period the code belongs to - its step number - or None.

    Looked for in this period first and then outwards, one step either side
    at a time, so that when two would do the nearest one is the answer.
    Compared in constant time, every one of them.
    """
    code = re.sub(r'\s', '', str(code or ''))
    if not re.fullmatch(r'[0-9]+', code, re.ASCII) or len(code) != int(digits):
        return None
    if isinstance(key, str):
        key = b32_clean(key)
    here = totp_step(when, period)
    found = None
    for off in [0] + [d for i in range(1, int(window) + 1) for d in (-i, i)]:
        if hmac.compare_digest(hotp(key, here + off, digits, algorithm), code) and found is None:
            found = here + off
    return found


def otpauth_uri(secret, label, issuer, digits=6, period=30, algorithm='SHA1'):
    """What the QR code holds: the Key URI Format every phone application
    reads (otpauth://totp/Issuer:label?secret=...&issuer=...)."""
    from urllib.parse import quote
    issuer = str(issuer or '').strip()
    who = (quote(issuer, safe='') + ':' if issuer else '') + quote(str(label), safe='@.')
    args = ['secret=' + str(secret).upper().replace('=', '')]
    if issuer:
        args.append('issuer=' + quote(issuer, safe=''))
    if str(algorithm).upper() != 'SHA1':
        args.append('algorithm=' + str(algorithm).upper())
    if int(digits) != 6:
        args.append('digits=%d' % int(digits))
    if int(period) != 30:
        args.append('period=%d' % int(period))
    return 'otpauth://totp/%s?%s' % (who, '&'.join(args))


# ---------------------------------------------------------------------------
# QR codes
# ---------------------------------------------------------------------------
# The picture the phone scans, made here rather than by a package: the
# standard (ISO/IEC 18004) in byte mode, every version, every level of error
# correction, the mask chosen the way the standard says. It comes out as SVG
# for the pages and as half-block characters for a terminal.
_QR_ECC_PER_BLOCK = {
    'L': (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
          28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    'M': (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
          26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    'Q': (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
          28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    'H': (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
          30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}
_QR_BLOCKS = {
    'L': (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12,
          12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    'M': (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20,
          21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    'Q': (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25,
          27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    'H': (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30,
          32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}
_QR_FORMAT = {'L': 1, 'M': 0, 'Q': 3, 'H': 2}


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(len(result)):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _qr_raw_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        result -= (25 * align - 10) * align - 55
        if ver >= 7:
            result -= 36
    return result


def _qr_data_codewords(ver, ecl):
    return (_qr_raw_modules(ver) // 8
            - _QR_ECC_PER_BLOCK[ecl][ver] * _QR_BLOCKS[ecl][ver])


class QrCode(object):
    """One QR code: `modules[y][x]` is True where the square is dark."""

    def __init__(self, text, ecl='M'):
        data = text.encode('utf-8') if isinstance(text, str) else bytes(text)
        self.ecl = ecl
        for ver in range(1, 41):
            count_bits = 8 if ver <= 9 else 16
            need = 4 + count_bits + 8 * len(data)
            if need <= _qr_data_codewords(ver, ecl) * 8:
                break
        else:
            raise ValueError('that is too long for a QR code')
        self.version = ver
        self.size = ver * 4 + 17
        bits = []

        def put(value, n):
            for i in reversed(range(n)):
                bits.append((value >> i) & 1)
        put(0x4, 4)
        put(len(data), count_bits)
        for b in data:
            put(b, 8)
        capacity = _qr_data_codewords(ver, ecl) * 8
        put(0, min(4, capacity - len(bits)))
        put(0, (8 - len(bits) % 8) % 8)
        pad = 0xEC
        while len(bits) < capacity:
            put(pad, 8)
            pad ^= 0xEC ^ 0x11
        words = [int(''.join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
        all_words = self._with_ecc(words)
        size = self.size
        self.modules = [[False] * size for _ in range(size)]
        self.function = [[False] * size for _ in range(size)]
        self._function_patterns()
        self._codewords(all_words)
        best, best_penalty = 0, None
        for mask in range(8):
            self._mask(mask)
            self._format(mask)
            penalty = self._penalty()
            if best_penalty is None or penalty < best_penalty:
                best, best_penalty = mask, penalty
            self._mask(mask)
        self._mask(best)
        self._format(best)
        self.mask = best

    # ---- the pieces ------------------------------------------------------
    def _set(self, x, y, dark):
        self.modules[y][x] = bool(dark)
        self.function[y][x] = True

    def _function_patterns(self):
        size = self.size
        for i in range(size):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for x, y in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < size and 0 <= yy < size:
                        self._set(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
        places = self._alignment_places()
        last = len(places) - 1
        for i, a in enumerate(places):
            for j, b in enumerate(places):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self._format(0)
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            value = self.version << 12 | rem
            for i in range(18):
                bit = (value >> i) & 1
                a, b = size - 11 + i % 3, i // 3
                self._set(a, b, bit)
                self._set(b, a, bit)

    def _alignment_places(self):
        ver = self.version
        if ver == 1:
            return []
        count = ver // 7 + 2
        step = (ver * 8 + count * 3 + 5) // (count * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(count - 1)] + [6]
        return list(reversed(result))

    def _format(self, mask):
        data = _QR_FORMAT[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1
        size = self.size
        for i in range(0, 6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(0, 8):
            self._set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, size - 15 + i, bit(i))
        self._set(8, size - 8, True)

    def _with_ecc(self, data):
        ver, ecl = self.version, self.ecl
        blocks_n = _QR_BLOCKS[ecl][ver]
        ecc_len = _QR_ECC_PER_BLOCK[ecl][ver]
        raw = _qr_raw_modules(ver) // 8
        short_n = blocks_n - raw % blocks_n
        short_len = raw // blocks_n
        divisor = _rs_divisor(ecc_len)
        blocks = []
        k = 0
        for i in range(blocks_n):
            take = short_len - ecc_len + (0 if i < short_n else 1)
            dat = data[k:k + take]
            k += take
            ecc = _rs_remainder(dat, divisor)
            if i < short_n:
                dat = dat + [0]
            blocks.append(dat + ecc)
        out = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != short_len - ecc_len or j >= short_n:
                    out.append(blk[i])
        return out

    def _codewords(self, data):
        size = self.size
        i = 0
        right = size - 1
        total = len(data) * 8
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.function[y][x] and i < total:
                        self.modules[y][x] = bool((data[i >> 3] >> (7 - (i & 7))) & 1)
                        i += 1
            right -= 2

    def _mask(self, mask):
        test = (lambda x, y: (x + y) % 2 == 0,
                lambda x, y: y % 2 == 0,
                lambda x, y: x % 3 == 0,
                lambda x, y: (x + y) % 3 == 0,
                lambda x, y: (x // 3 + y // 2) % 2 == 0,
                lambda x, y: x * y % 2 + x * y % 3 == 0,
                lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
                lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)[mask]
        for y in range(self.size):
            row, fn = self.modules[y], self.function[y]
            for x in range(self.size):
                if not fn[x] and test(x, y):
                    row[x] = not row[x]

    def _penalty(self):
        size, m = self.size, self.modules
        score = 0
        lines = [m[y] for y in range(size)] + [[m[y][x] for y in range(size)]
                                                for x in range(size)]
        finder = ([True, False, True, True, True, False, True, False, False, False, False],
                  [False, False, False, False, True, False, True, True, True, False, True])
        for line in lines:
            run, last = 0, None
            for v in line:
                if v == last:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run, last = 1, v
            if run >= 5:
                score += 3 + run - 5
            for i in range(len(line) - 10):
                seg = line[i:i + 11]
                if seg == finder[0] or seg == finder[1]:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                v = m[y][x]
                if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(1 for v in row if v) for row in m)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        score += k * 10
        return score

    # ---- drawn -------------------------------------------------------------
    def svg(self, scale=6, border=4, dark='#000', light='#fff'):
        """The code as an SVG picture, light squares and all - a phone needs
        the quiet zone round it, so it is part of the picture."""
        size = self.size + border * 2
        path = []
        for y in range(self.size):
            x = 0
            while x < self.size:
                if self.modules[y][x]:
                    start = x
                    while x < self.size and self.modules[y][x]:
                        x += 1
                    path.append('M%d %dh%dv1h-%dz' % (start + border, y + border,
                                                      x - start, x - start))
                else:
                    x += 1
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" '
                'height="%d" shape-rendering="crispEdges"><rect width="100%%" height="100%%" '
                'fill="%s"/><path d="%s" fill="%s"/></svg>'
                % (size, size, size * scale, size * scale, light, ''.join(path), dark))

    def terminal(self, border=2, invert=False):
        """The code as half-block characters, two rows of squares a line.
        Drawn for a dark terminal: a dark square is the terminal's own dark
        and a light one is a full block. `invert` for a light terminal."""
        size = self.size
        out = []
        def light(x, y):
            if x < 0 or y < 0 or x >= size or y >= size:
                return not invert
            return (not self.modules[y][x]) != invert
        for y in range(-border, size + border, 2):
            row = []
            for x in range(-border, size + border):
                top, bottom = light(x, y), light(x, y + 1)
                row.append('█' if top and bottom else
                           '▀' if top else '▄' if bottom else ' ')
            out.append(''.join(row))
        return '\n'.join(out)


def qr_svg(text, scale=6):
    return QrCode(text, 'M').svg(scale=scale)


def qr_data_uri(text, scale=6):
    return 'data:image/svg+xml;base64,' + base64.b64encode(
        qr_svg(text, scale).encode('ascii')).decode('ascii')


# ---------------------------------------------------------------------------
# The database: SQLite, a file here (/var/lib/plexmon/plexmon.db)
# ---------------------------------------------------------------------------
class DbError(Exception):
    pass


TABLES = [
    'CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)',
    'CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT, at REAL, by_who TEXT)',
    'CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, '
    'kind TEXT, node TEXT, level TEXT, who TEXT, words TEXT)',
    'CREATE INDEX IF NOT EXISTS events_ts ON events (ts)',
    'CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, '
    'role TEXT, pw TEXT, source TEXT, disabled INTEGER DEFAULT 0, must_change INTEGER DEFAULT 0, '
    'note TEXT, created REAL, last_in REAL, last_from TEXT, prefs TEXT, display TEXT, dn TEXT, '
    'group_list TEXT, cols TEXT, cols_at REAL)',
    'CREATE UNIQUE INDEX IF NOT EXISTS accounts_name ON accounts (name)',
    'CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT, '
    'kind TEXT, name TEXT, role TEXT, created REAL, last REAL, expires REAL, ip TEXT, data TEXT)',
    'CREATE INDEX IF NOT EXISTS sessions_token ON sessions (token_hash)',
]


class Db(object):
    """One SQLite file, one connection per thread, every write in its own
    short transaction."""
    engine = 'sqlite'

    def __init__(self, path=None):
        self.path = str(path or DB_FILE)
        self.local = threading.local()
        self.write_lock = threading.RLock()

    def where(self):
        return self.path

    def conn(self):
        c = getattr(self.local, 'c', None)
        if c is None:
            try:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
                c = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                    check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute('PRAGMA journal_mode=WAL')
                c.execute('PRAGMA synchronous=NORMAL')
                c.execute('PRAGMA foreign_keys=ON')
            except sqlite3.Error as e:
                raise DbError('the database %s will not open: %s' % (self.path, e))
            self.local.c = c
        return c

    def close(self):
        c = getattr(self.local, 'c', None)
        if c is not None:
            try:
                c.close()
            except sqlite3.Error:
                pass
            self.local.c = None

    def make_tables(self):
        with self.write_lock:
            for sql in TABLES:
                self.conn().execute(sql)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def rows(self, sql, args=()):
        try:
            return [dict(r) for r in self.conn().execute(sql, tuple(args)).fetchall()]
        except sqlite3.Error as e:
            raise DbError('the database: %s' % e)

    def row(self, sql, args=()):
        got = self.rows(sql, args)
        return got[0] if got else None

    def value(self, sql, args=(), default=None):
        r = self.row(sql, args)
        if not r:
            return default
        v = list(r.values())[0]
        return default if v is None else v

    def run(self, sql, args=()):
        try:
            with self.write_lock:
                return self.conn().execute(sql, tuple(args)).rowcount
        except sqlite3.Error as e:
            raise DbError('the database: %s' % e)

    def insert(self, table, data):
        cols = list(data)
        sql = 'INSERT INTO %s (%s) VALUES (%s)' % (table, ', '.join(cols),
                                                   ', '.join('?' for _ in cols))
        try:
            with self.write_lock:
                return self.conn().execute(sql, tuple(data[c] for c in cols)).lastrowid
        except sqlite3.IntegrityError as e:
            raise ValueError('that is there already (%s)' % e)
        except sqlite3.Error as e:
            raise DbError('the database: %s' % e)

    def update(self, table, ident, data):
        if not data:
            return 0
        cols = list(data)
        sql = 'UPDATE %s SET %s WHERE id = ?' % (table, ', '.join('%s = ?' % c for c in cols))
        return self.run(sql, tuple(data[c] for c in cols) + (ident,))

    def upsert(self, table, key, data):
        cols = list(data)
        sql = 'INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(%s) DO UPDATE SET %s' % (
            table, ', '.join(cols), ', '.join('?' for _ in cols), key,
            ', '.join('%s = excluded.%s' % (c, c) for c in cols if c != key))
        return self.run(sql, tuple(data[c] for c in cols))

    def size(self):
        try:
            return sum(os.path.getsize(p) for p in (self.path, self.path + '-wal')
                       if os.path.exists(p))
        except OSError:
            return 0


def like_pattern(text):
    return re.sub(r'([!%_])', r'!\1', str(text))


class Storage(object):
    """Everything Settings keeps."""
    _KINDS = (0.0, [])

    def __init__(self, db):
        self.db = db
        self.db.make_tables()
        self.lock = threading.RLock()

    # ---- meta, settings, events ------------------------------------------
    def get_meta(self, k, default=None):
        got = self.db.value('SELECT v FROM meta WHERE k = ?', (k,))
        return default if got is None else got

    def set_meta(self, k, v):
        self.db.upsert('meta', 'k', {'k': k, 'v': str(v)})

    def drop_meta(self, k):
        self.db.run('DELETE FROM meta WHERE k = ?', (k,))

    def settings_saved(self):
        out = {}
        for r in self.db.rows('SELECT k, v FROM settings'):
            try:
                out[r['k']] = json.loads(r['v'])
            except (TypeError, ValueError):
                continue
        return out

    def settings_written(self):
        return {r['k']: {'at': r['at'], 'by': r['by_who']}
                for r in self.db.rows('SELECT k, at, by_who FROM settings')}

    def save_setting(self, k, v, by=''):
        self.db.upsert('settings', 'k', {'k': k, 'v': json.dumps(v), 'at': time.time(),
                                         'by_who': str(by or '')[:190]})

    def drop_setting(self, k):
        self.db.run('DELETE FROM settings WHERE k = ?', (k,))

    def add_event(self, kind, words, level='info', who=''):
        try:
            self.db.insert('events', {'ts': time.time(), 'kind': str(kind)[:60],
                                      'node': node_name(), 'level': level,
                                      'who': str(who or '')[:190], 'words': str(words)[:4000]})
        except Exception as e:                          # noqa: BLE001
            log('the event could not be written down: %s (%s)' % (words, e), 'warn')
        log('%s: %s%s' % (kind, words, (' (%s)' % who) if who else ''),
            'warn' if level in ('warn', 'bad') else 'info')

    def events(self, limit=100, offset=0, q='', kind='', since=0, until=0):
        where, args = [], []
        if q:
            where.append("(LOWER(words) LIKE ? ESCAPE '!' OR LOWER(who) LIKE ? ESCAPE '!' "
                         "OR LOWER(kind) LIKE ? ESCAPE '!')")
            like = '%' + like_pattern(str(q).lower()) + '%'
            args += [like, like, like]
        if kind:
            where.append('kind = ?')
            args.append(kind)
        if since:
            where.append('ts >= ?')
            args.append(float(since))
        if until:
            where.append('ts < ?')
            args.append(float(until))
        sql = ' WHERE ' + ' AND '.join(where) if where else ''
        total = self.db.value('SELECT COUNT(*) AS n FROM events' + sql, args, 0)
        rows = self.db.rows('SELECT * FROM events' + sql + ' ORDER BY ts DESC, id DESC '
                            'LIMIT %d OFFSET %d' % (int(limit), int(offset)), args)
        return rows, int(total or 0)

    def event_kinds(self):
        at, kinds = Storage._KINDS
        if time.time() - at < 60 and kinds:
            return kinds
        kinds = [r['kind'] for r in self.db.rows('SELECT DISTINCT kind FROM events ORDER BY kind')]
        Storage._KINDS = (time.time(), kinds)
        return kinds

    def tidy(self):
        days = int(CONFIG.get('retain_events_days') or 0)
        if days:
            self.db.run('DELETE FROM events WHERE ts < ?', (time.time() - days * 86400,))
        self.sessions_tidy(int(CONFIG.get('session_idle_min') or 120) * 60)

    # ---- accounts of the Settings pages ------------------------------------
    def accounts(self):
        rows = self.db.rows('SELECT * FROM accounts ORDER BY name')
        for r in rows:
            r.pop('pw', None)
            r['cols_tables'] = len(self._cols_of(r.pop('cols', None)))
            r.pop('cols_at', None)
        return rows

    def account(self, name):
        return self.db.row('SELECT * FROM accounts WHERE LOWER(name) = ?',
                           (str(name or '').strip().lower(),))

    def add_account(self, name, role, password=None, source='local', note='', must_change=0,
                    display=''):
        name = str(name or '').strip()
        if not ACCOUNT_NAME_RE.match(name):
            raise ValueError('a name has letters, digits, dots, dashes, @ and nothing else')
        if role not in ROLES_ALL:
            raise ValueError('the role is one of %s' % ', '.join(ROLES_ALL))
        if self.account(name):
            raise ValueError('there is an account called %s already' % name)
        bad = password_problem(password)
        if bad:
            raise ValueError(bad)
        self.db.run('DELETE FROM meta WHERE k = ?', ('adm2fa:' + name.lower(),))
        return self.db.insert('accounts', {
            'name': name, 'role': role, 'pw': password_hash(password) if password else '',
            'source': source, 'disabled': 0, 'must_change': int(bool(must_change)),
            'note': str(note or '')[:400], 'created': time.time(), 'last_in': None,
            'last_from': '', 'prefs': '{}', 'display': display, 'dn': '', 'group_list': ''})

    def set_account(self, name, **fields):
        one = self.account(name)
        if not one:
            raise ValueError('there is no account called %s' % name)
        if 'password' in fields:
            bad = password_problem(fields['password'])
            if bad:
                raise ValueError(bad)
            fields['pw'] = password_hash(fields.pop('password'))
        if 'role' in fields and fields['role'] not in ROLES_ALL:
            raise ValueError('the role is one of %s' % ', '.join(ROLES_ALL))
        self.db.update('accounts', one['id'], fields)

    def drop_account(self, name):
        one = self.account(name)
        if not one:
            raise ValueError('there is no account called %s' % name)
        self.db.run('DELETE FROM accounts WHERE id = ?', (one['id'],))
        self.db.run('DELETE FROM sessions WHERE kind = ? AND LOWER(name) = ?',
                    ('admin', one['name'].lower()))
        self.db.run('DELETE FROM meta WHERE k = ?', ('adm2fa:' + one['name'].lower(),))

    # ---- how somebody laid out the tables (as on isemon) ------------------
    COLS_MOST_TABLES = 300
    COLS_MOST_COLUMNS = 60
    COLS_MOST_BYTES = 60000

    @staticmethod
    def _cols_of(text):
        if not text:
            return {}
        try:
            got = json.loads(text)
        except (TypeError, ValueError):
            return {}
        return got if isinstance(got, dict) else {}

    @classmethod
    def clean_cols(cls, cols):
        if cols == [] or cols is None:
            cols = {}
        if not isinstance(cols, dict):
            raise ValueError('a layout of the columns is an object of tables')
        out = {}

        def names(v, most):
            return [str(c)[:80] for c in v[:most] if isinstance(c, (str, int))] \
                if isinstance(v, list) else []

        for key, one in list(cols.items())[:cls.COLS_MOST_TABLES]:
            key = str(key)[:160]
            if not key or not isinstance(one, dict):
                continue
            kept = {}
            widths = one.get('w')
            if isinstance(widths, dict):
                w = {}
                for col, px in list(widths.items())[:cls.COLS_MOST_COLUMNS]:
                    try:
                        px = int(round(float(px)))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    w[str(col)[:80]] = max(24, min(3000, px))
                if w:
                    kept['w'] = w
            for k in ('o', 'h', 'v'):
                got = names(one.get(k), cls.COLS_MOST_COLUMNS)
                if got:
                    kept[k] = got
            sp = [c for c in names(one.get('s'), 20) if re.match(r'^~\d{1,4}\Z', c)]
            if sp:
                kept['s'] = sp
            if kept:
                try:
                    kept['t'] = int(float(one.get('t') or 0))
                except (TypeError, ValueError, OverflowError):
                    kept['t'] = 0
                out[key] = kept
        if len(json.dumps(out, separators=(',', ':'))) > cls.COLS_MOST_BYTES:
            raise ValueError('that layout of the columns is too big to keep (more than %d '
                             'characters)' % cls.COLS_MOST_BYTES)
        return out

    def user_cols(self, name):
        try:
            one = self.account(name)
        except Exception:                               # noqa: BLE001
            return {}
        return self._cols_of(one.get('cols')) if one else {}

    def put_user_cols(self, name, cols):
        one = self.account(name)
        if not one:
            raise ValueError('there is no account called %s' % name)
        cols = self.clean_cols(cols or {})
        self.db.update('accounts', one['id'], {
            'cols': json.dumps(cols, separators=(',', ':')) if cols else None,
            'cols_at': time.time()})
        return cols

    @staticmethod
    def clean_prefs(prefs):
        if prefs == [] or prefs is None:
            prefs = {}
        if not isinstance(prefs, dict):
            raise ValueError('the choices are an object')
        out = {}
        if 'lang' in prefs:
            if str(prefs['lang']) not in ('cs', 'en'):
                raise ValueError('no such language here: %s' % prefs['lang'])
            out['lang'] = str(prefs['lang'])
        return out

    def user_prefs(self, name):
        one = self.account(name)
        return self._cols_of(one.get('prefs')) if one else {}

    def put_user_prefs(self, name, prefs):
        one = self.account(name)
        if not one:
            raise ValueError('there is no account called %s' % name)
        kept = dict(self._cols_of(one.get('prefs')))
        kept.update(self.clean_prefs(prefs))
        self.db.update('accounts', one['id'], {'prefs': json.dumps(kept)})
        return kept

    def fulladmins_left(self, but=''):
        return sum(1 for a in self.accounts() if a['role'] == 'fulladmin' and not a['disabled']
                   and a['name'].lower() != str(but).lower())

    # ---- sessions ----------------------------------------------------------
    def session_new(self, kind, name, role, ip, hours, data=None):
        token = secrets.token_urlsafe(32)
        t = time.time()
        self.db.insert('sessions', {
            'token_hash': hashlib.sha256(token.encode()).hexdigest(), 'kind': kind,
            'name': name, 'role': role, 'created': t, 'last': t,
            'expires': t + float(hours) * 3600, 'ip': str(ip or '')[:60],
            'data': json.dumps(data or {})})
        return token

    def session_get(self, token, kind, idle_s, touch=True):
        if not token:
            return None
        row = self.db.row('SELECT * FROM sessions WHERE token_hash = ? AND kind = ?',
                          (hashlib.sha256(str(token).encode()).hexdigest(), kind))
        if not row:
            return None
        t = time.time()
        if row['expires'] < t or t - float(row['last'] or 0) > idle_s:
            self.db.run('DELETE FROM sessions WHERE id = ?', (row['id'],))
            return None
        if touch and t - float(row['last'] or 0) > 20:
            self.db.run('UPDATE sessions SET last = ? WHERE id = ?', (t, row['id']))
        try:
            row['data'] = json.loads(row.get('data') or '{}')
        except ValueError:
            row['data'] = {}
        return row

    def session_merge(self, ident, changes, expect=None):
        data = {}
        for _round in range(8):
            raw = self.db.value('SELECT data FROM sessions WHERE id = ?', (ident,))
            if raw is None:
                return None
            try:
                data = json.loads(raw or '{}')
            except ValueError:
                data = {}
            for k, v in (expect or {}).items():
                if data.get(k) != v:
                    return False
            for k, v in changes.items():
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            if self.db.run('UPDATE sessions SET data = ? WHERE id = ? AND data = ?',
                           (json.dumps(data), ident, raw)):
                return data
        return data

    def session_set_data(self, ident, data):
        self.db.run('UPDATE sessions SET data = ? WHERE id = ?', (json.dumps(data), ident))

    def session_end(self, token):
        self.db.run('DELETE FROM sessions WHERE token_hash = ?',
                    (hashlib.sha256(str(token or '').encode()).hexdigest(),))

    def sessions(self, kind=None):
        sql, args = 'SELECT id, kind, name, role, created, last, expires, ip FROM sessions', []
        if kind:
            sql += ' WHERE kind = ?'
            args.append(kind)
        return self.db.rows(sql + ' ORDER BY last DESC', args)

    def session_drop(self, ident):
        self.db.run('DELETE FROM sessions WHERE id = ?', (int(ident),))

    def sessions_tidy(self, idle_admin_s=None):
        t = time.time()
        self.db.run('DELETE FROM sessions WHERE expires < ?', (t,))
        if idle_admin_s:
            self.db.run("DELETE FROM sessions WHERE kind = 'admin' AND last < ?",
                        (t - float(idle_admin_s),))

    def counts(self):
        return {'accounts': int(self.db.value('SELECT COUNT(*) AS n FROM accounts', (), 0)),
                'events': int(self.db.value('SELECT COUNT(*) AS n FROM events', (), 0)),
                'sessions': int(self.db.value('SELECT COUNT(*) AS n FROM sessions', (), 0))}


def _meta_json(store, k):
    try:
        got = store.get_meta(k)
        got = json.loads(got) if got else None
        return got if isinstance(got, dict) else None
    except (ValueError, TypeError):
        return None


STORE = {'store': None}


def open_store():
    load_config()
    st = Storage(Db())
    load_config(saved=st.settings_saved())
    STORE['store'] = st
    return st


# ---------------------------------------------------------------------------
# Certificates and TLS (otp 1.0.47 on): the certificate nginx answers the Plex
# address with, kept in the database (its key sealed), put into the files the
# Plex vhost names and into /etc/nginx/plexmon/tls.conf - nginx asked (nginx -t)
# before it is reloaded, and everything put back as it was when it says no.
# One node, no VIP address: plexmon never runs in a cluster.
# ---------------------------------------------------------------------------
TLS_PRESETS = {
    # The ciphers otp has always used (and so plexmon): ECDHE only, AEAD only.
    'standard': {'protocols': ['TLSv1.2', 'TLSv1.3'], 'ciphers': (
        'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:'
        'ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:'
        'ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305')},
    # Mozilla's "intermediate": the same and the DHE ones (DH parameters).
    'intermediate': {'protocols': ['TLSv1.2', 'TLSv1.3'], 'ciphers': (
        'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:'
        'ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:'
        'ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:'
        'DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384:DHE-RSA-CHACHA20-POLY1305')},
    # Mozilla's "modern": TLS 1.3 alone - its ciphers are not chosen.
    'modern': {'protocols': ['TLSv1.3'], 'ciphers': ''},
}
DH_WORDS = {'none': 'none (ECDHE only needs none)',
            'ffdhe2048': 'RFC 7919 ffdhe2048', 'ffdhe3072': 'RFC 7919 ffdhe3072',
            'ffdhe4096': 'RFC 7919 ffdhe4096', 'gen2048': 'made here, 2048 bits',
            'gen3072': 'made here, 3072 bits (a minute or so)',
            'gen4096': 'made here, 4096 bits (several minutes)'}
WEB_LOCK = threading.Lock()


# ---- certificates: read, taken apart, made -----------------------------------
def _openssl(args, data=None, env=None, what='openssl'):
    e = dict(os.environ)
    e.update(env or {})
    got = subprocess.run(['openssl'] + args, input=data, capture_output=True, env=e,
                         timeout=120)
    if got.returncode != 0:
        said = (got.stderr or got.stdout or b'').decode('utf-8', 'replace').strip()
        raise ValueError('%s: %s' % (what, (said.splitlines() or ['openssl said no'])[-1][:200]))
    return got.stdout


PEM_CERT = re.compile(r'-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----')
PEM_KEY = re.compile(r'-----BEGIN ((?:RSA |EC |ENCRYPTED )?PRIVATE KEY)-----[A-Za-z0-9+/=\s:,-]+?'
                     r'-----END \1-----')


def cert_info(pem):
    """What a certificate says of itself, for the pages."""
    out = _openssl(['x509', '-noout', '-subject', '-issuer', '-startdate', '-enddate',
                    '-fingerprint', '-sha256', '-ext', 'subjectAltName', '-nameopt',
                    'RFC2253'], pem.encode(), what='the certificate').decode()
    info = {'san': []}
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('subject='):
            info['subject'] = line[8:].strip()
        elif line.startswith('issuer='):
            info['issuer'] = line[7:].strip()
        elif line.startswith('notBefore='):
            info['not_before'] = _cert_time(line[10:])
        elif line.startswith('notAfter='):
            info['not_after'] = _cert_time(line[9:])
        elif 'Fingerprint=' in line:
            info['fingerprint'] = line.split('=', 1)[1].strip()
        elif 'Subject Alternative Name' in line and i + 1 < len(lines):
            info['san'] = [x.strip().split(':', 1)[-1] for x in lines[i + 1].split(',')
                           if x.strip()]
    info['self_signed'] = info.get('subject') == info.get('issuer')
    cn = re.search(r'(?:^|,)CN=([^,]+)', info.get('subject') or '')
    info['cn'] = cn.group(1) if cn else ''
    try:
        pub = _openssl(['x509', '-noout', '-pubkey'], pem.encode()).decode()
        txt = _openssl(['pkey', '-pubin', '-noout', '-text'], pub.encode()).decode()
        m = re.search(r'\((\d+) bit\)', txt)
        info['key'] = ('%s %s bit' % ('EC' if 'ASN1 OID' in txt or 'NIST' in txt else 'RSA',
                                      m.group(1))) if m else ''
    except ValueError:
        info['key'] = ''
    return info


def _cert_time(text):
    try:
        return datetime.strptime(text.strip().replace('  ', ' '),
                                 '%b %d %H:%M:%S %Y %Z').replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _pub_of_cert(pem):
    return _openssl(['x509', '-noout', '-pubkey'], pem.encode(), what='the certificate')


def _pub_of_key(pem):
    return _openssl(['pkey', '-pubout'], pem.encode(), what='the key')


def key_plain(pem, password=''):
    """A private key without its passphrase, as PEM."""
    return _openssl(['pkey', '-passin', 'env:PLEXMON_PW'], pem.encode(),
                    {'PLEXMON_PW': password or ''},
                    what='the key (a wrong or missing password?)').decode()


def cert_bundle(cert='', key='', chain='', root='', pfx=b'', password='', pending_key=''):
    """Whatever was sent - one PEM with everything in it, separate parts, or
    a PFX / PKCS#12 with or without a password - as the parts nginx needs:
    the certificate, its key, the chain up from it, the root. The key is the
    certificate's own or nothing is taken."""
    certs, keys = [], []
    if pfx:
        got = None
        for extra in ([], ['-legacy']):
            try:
                got = _openssl(['pkcs12', '-nodes', '-passin', 'env:PLEXMON_PW'] + extra, pfx,
                               {'PLEXMON_PW': password or ''}, what='the PFX / PKCS#12 file')
                break
            except ValueError as e:
                last = e
        if got is None:
            raise ValueError('%s - is the password right?' % last)
        text = got.decode('utf-8', 'replace')
        certs += [m.group(0) for m in PEM_CERT.finditer(text)]
        keys += [m.group(0) for m in PEM_KEY.finditer(text)]
    for part in (cert, chain, root, key):
        part = str(part or '')
        certs += [m.group(0) for m in PEM_CERT.finditer(part)]
        keys += [m.group(0) for m in PEM_KEY.finditer(part)]
    if not certs:
        raise ValueError('no certificate was found in what was sent (PEM, or a PFX / PKCS#12 '
                         'file)')
    if len(keys) > 1:
        raise ValueError('more than one private key was sent')
    if keys:
        key_pem = key_plain(keys[0], password)
    elif pending_key:
        key_pem = pending_key
    else:
        raise ValueError('no private key came with it: send the key too (or the PFX), or make '
                         'the request here first so the key is waiting')
    want = _pub_of_key(key_pem)
    leaf = next((c for c in certs if _pub_of_cert(c) == want), None)
    if not leaf:
        raise ValueError('none of the certificates is the one of this key')
    others = [c for c in certs if c != leaf]
    infos = {c: cert_info(c) for c in others}
    roots = [c for c in others if infos[c]['self_signed']]
    roots += [m.group(0) for m in PEM_CERT.finditer(str(root or '')) if m.group(0) not in roots]
    inter = [c for c in others if c not in roots]
    # The chain in order: each the issuer of the one before it.
    chain_out, at = [], cert_info(leaf)
    while inter:
        nxt = next((c for c in inter if infos[c].get('subject') == at.get('issuer')), None)
        if not nxt:
            chain_out += inter
            break
        chain_out.append(nxt)
        inter.remove(nxt)
        at = infos[nxt]
    info = cert_info(leaf)
    verify = ''
    if roots:
        with tempfile.TemporaryDirectory(prefix='plexmon-cert-') as t:
            def put(name, text):
                p = os.path.join(t, name)
                with open(p, 'w') as f:
                    f.write(text)
                return p
            args = ['verify', '-CAfile', put('root.pem', '\n'.join(roots) + '\n')]
            if chain_out:
                args += ['-untrusted', put('chain.pem', '\n'.join(chain_out) + '\n')]
            args.append(put('leaf.pem', leaf + '\n'))
            try:
                _openssl(args, what='the chain')
                verify = 'ok'
            except ValueError as e:
                verify = str(e)
    return {'cert': leaf + '\n', 'chain': ''.join(c + '\n' for c in chain_out),
            'root': ''.join(c + '\n' for c in roots), 'key': key_pem, 'info': info,
            'chain_info': [cert_info(c) for c in chain_out],
            'root_info': [cert_info(c) for c in roots], 'verify': verify}


def _san_ext(names):
    out = []
    for n in names:
        n = str(n).strip()
        if not n:
            continue
        try:
            plain_ip(n, 'address')
            out.append('IP:' + n)
        except ValueError:
            if not re.match(r'^(\*\.)?[A-Za-z0-9.-]{1,253}\Z', n):
                raise ValueError('%r is not a name or an address' % n)
            out.append('DNS:' + n)
    return ','.join(out)


def _subject(cn, org='', unit='', country='', city=''):
    def esc(v):
        return re.sub(r'([/\\=+,])', r'\\\1', str(v).strip())
    if not cn or not re.match(r'^[A-Za-z0-9*.-]{1,64}\Z', cn):
        raise ValueError('the common name is a host name')
    s = '/CN=' + esc(cn)
    if org:
        s += '/O=' + esc(org)
    if unit:
        s += '/OU=' + esc(unit)
    if city:
        s += '/L=' + esc(city)
    if country:
        if not re.match(r'^[A-Za-z]{2}\Z', country):
            raise ValueError('the country is two letters (CZ)')
        s += '/C=' + country.upper()
    return s


def new_key(kind):
    if kind == 'ec':
        return _openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt',
                         'ec_paramgen_curve:P-256']).decode()
    bits = {'rsa2048': 2048, 'rsa3072': 3072, 'rsa4096': 4096}.get(kind)
    if not bits:
        raise ValueError('the key is ec, rsa2048, rsa3072 or rsa4096')
    return _openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt',
                     'rsa_keygen_bits:%d' % bits]).decode()


def make_csr(cn, names, kind='rsa2048', **subject):
    key = new_key(kind)
    with tempfile.TemporaryDirectory(prefix='plexmon-csr-') as t:
        kf = os.path.join(t, 'k.pem')
        with open(kf, 'w') as f:
            f.write(key)
        csr = _openssl(['req', '-new', '-key', kf, '-subj', _subject(cn, **subject),
                        '-addext', 'subjectAltName=' + _san_ext([cn] + list(names))],
                       what='the request').decode()
    return key, csr


def make_selfsigned(cn, names, days=825, kind='rsa2048', **subject):
    key = new_key(kind)
    with tempfile.TemporaryDirectory(prefix='plexmon-crt-') as t:
        kf = os.path.join(t, 'k.pem')
        with open(kf, 'w') as f:
            f.write(key)
        crt = _openssl(['req', '-new', '-x509', '-key', kf, '-days', str(int(days)), '-subj',
                        _subject(cn, **subject), '-addext',
                        'subjectAltName=' + _san_ext([cn] + list(names))],
                       what='the certificate').decode()
    return key, crt




# ---- the TLS of this server, kept ------------------------------------------------------
def tls_targets(store):
    return [node_name()]


def tls_profile(store, target):
    return _meta_json(store, 'web:tls:' + target)


def tls_save(store, target, prof, by):
    prof = dict(prof)
    prof['at'] = time.time()
    prof['by'] = by
    store.set_meta('web:tls:' + target, json.dumps(prof))
    WEB_WORK['wake'].set()


def tls_view(prof):
    """A profile as the pages may see it: no key."""
    if not prof:
        return None
    out = {k: v for k, v in prof.items() if k not in ('key', 'pending_key')}
    out['has_key'] = bool(prof.get('key'))
    out['pending'] = bool(prof.get('pending_key'))
    return out


def tls_policy_checked(b):
    preset = str(b.get('preset') or 'standard')
    if preset not in list(TLS_PRESETS) + ['custom']:
        raise ValueError('the TLS settings are standard, intermediate, modern or custom')
    out = {'preset': preset}
    if preset == 'custom':
        prot = b.get('protocols') or []
        if not isinstance(prot, list) or not prot or \
                any(p not in ('TLSv1.2', 'TLSv1.3') for p in prot):
            raise ValueError('the protocols are TLSv1.2 and/or TLSv1.3')
        out['protocols'] = [p for p in ('TLSv1.2', 'TLSv1.3') if p in prot]
        ciphers = str(b.get('ciphers') or '').strip()
        if ciphers:
            if not re.match(r'^[A-Za-z0-9_+!@=:.-]{1,2000}\Z', ciphers):
                raise ValueError('the ciphers are an OpenSSL cipher list')
            _openssl(['ciphers', ciphers], what='OpenSSL does not know that cipher list')
        out['ciphers'] = ciphers
    dh = str(b.get('dh') or 'none')
    if dh not in DH_WORDS:
        raise ValueError('the DH parameters are one of %s' % ', '.join(DH_WORDS))
    out['dh'] = dh
    out['prefer'] = bool(b.get('prefer'))
    try:
        hv = b.get('hsts')
        out['hsts'] = max(0, min(63072000, int(hv) if hv not in (None, '') else 0))
    except (TypeError, ValueError):
        raise ValueError('HSTS is a number of seconds')
    return out


def tls_resolved(prof):
    """What is written for a profile: the parts and the lines."""
    if not prof:
        return None
    preset = prof.get('preset') or 'standard'
    p = TLS_PRESETS.get(preset) or {'protocols': prof.get('protocols') or ['TLSv1.2', 'TLSv1.3'],
                                    'ciphers': prof.get('ciphers') or ''}
    out = {'protocols': p['protocols'], 'ciphers': p['ciphers'], 'prefer': bool(prof.get('prefer')),
           'dh': prof.get('dh') or 'none',
           'hsts': int(prof['hsts']) if prof.get('hsts') not in (None, '') else 0}
    if prof.get('cert') and prof.get('key'):
        out.update(cert=prof['cert'], chain=prof.get('chain') or '', root=prof.get('root') or '',
                   key=unseal_secret(prof['key'], 'web:tls'))
    return out


# ---- nginx: the Plex vhost, adopted once, then written to ------------------------------
# install.sh hands the Plex vhost (/etc/nginx/conf.d/plex.conf) over once: the
# ssl_* lines of its HTTPS server go into /etc/nginx/plexmon/tls.conf as they
# are, an include takes their place, and every server answers
# /.well-known/acme-challenge/ from /var/www/plexmon-acme for the CA. Nothing
# else in it is touched - the Plex proxy, /smart, the LAN rules stay exactly so.
TLS_LINES = re.compile(r'^\s*(ssl_certificate|ssl_certificate_key|ssl_protocols|ssl_ciphers|'
                       r'ssl_prefer_server_ciphers|ssl_dhparam|ssl_trusted_certificate|'
                       r'add_header\s+Strict-Transport-Security)\b[^;{}]*;[ \t]*(#.*)?$',
                       re.I)
INCLUDE_TLS = 'include %s;' % NGINX_TLS_CONF
INCLUDE_ACME = 'include %s;' % NGINX_ACME_CONF


def _server_blocks(text):
    """[(start, end)] of every top-level-ish `server {` ... `}` in text
    (comments and quoted strings skipped)."""
    out = []
    i, n = 0, len(text)
    depth = 0
    starts = []
    while i < n:
        c = text[i]
        if c == '#':
            j = text.find('\n', i)
            i = n if j < 0 else j
            continue
        if c in ('"', "'"):
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            i = j + 1
            continue
        if c == '{':
            line_start = text.rfind('\n', 0, i) + 1
            head = text[line_start:i].strip()
            starts.append((depth, i, re.match(r'^server\s*$', head) is not None, line_start))
            depth += 1
        elif c == '}':
            depth -= 1
            if starts:
                d, at, is_server, ls = starts.pop()
                if is_server:
                    out.append((ls, i + 1))
        i += 1
    return sorted(out)


def nginx_conf_text():
    try:
        return NGINX_SITE.read_text(encoding='utf-8')
    except OSError:
        return ''


def nginx_adopted(text=None):
    text = nginx_conf_text() if text is None else text
    return INCLUDE_TLS in text


def nginx_conf_cert_paths(text=None):
    """The certificate and key the HTTPS server names now - in tls.conf once
    adopted, else in the vhost itself."""
    srcs = []
    try:
        srcs.append(NGINX_TLS_CONF.read_text(encoding='utf-8'))
    except OSError:
        pass
    srcs.append(nginx_conf_text() if text is None else text)
    cert = key = None
    for t in srcs:
        m = re.search(r'^\s*ssl_certificate\s+([^;\s]+)\s*;', t, re.M)
        k = re.search(r'^\s*ssl_certificate_key\s+([^;\s]+)\s*;', t, re.M)
        if m and not cert:
            cert = m.group(1)
        if k and not key:
            key = k.group(1)
    return Path(cert or NGINX_CERT), Path(key or NGINX_KEY)


ACME_CONF_TEXT = '''# Written by plexmon - the answers to an ACME CA (Let's Encrypt), HTTP-01.
location ^~ /.well-known/acme-challenge/ {
    root %s;
    default_type text/plain;
    try_files $uri =404;
    allow all;
    access_log off;
}
'''


def nginx_adopt(dry=False):
    """The Plex vhost handed over to plexmon, once: (changed, words). Kept as
    it was when nginx does not take the result."""
    text = nginx_conf_text()
    if not text:
        return False, '%s is not there - nothing to adopt' % NGINX_SITE
    if nginx_adopted(text):
        return False, '%s is plexmon\'s already' % NGINX_SITE
    blocks = _server_blocks(text)
    if not blocks:
        return False, 'no server { } in %s' % NGINX_SITE
    out, at, moved, tls_done = [], 0, [], False
    for start, end in blocks:
        out.append(text[at:start])
        block = text[start:end]
        lines = block.split('\n')
        is_tls = bool(re.search(r'^\s*listen\s+[^;]*\b(443|ssl)\b', block, re.M)) or \
            bool(re.search(r'^\s*ssl_certificate\s', block, re.M))
        new = []
        put_tls = False
        for ln in lines:
            if is_tls and TLS_LINES.match(ln):
                moved.append(ln.strip())
                if not put_tls:
                    indent = re.match(r'^(\s*)', ln).group(1)
                    new.append(indent + INCLUDE_TLS)
                    put_tls = True
                continue
            new.append(ln)
        # The answers for the CA, in every server, after server_name (or first).
        where = next((i for i, ln in enumerate(new) if re.match(r'^\s*server_name\b', ln)), 0)
        indent = re.match(r'^(\s*)', new[where + 1] if where + 1 < len(new) else '    ').group(1) \
            or '    '
        new.insert(where + 1, indent + INCLUDE_ACME)
        block = '\n'.join(new)
        # A port-80 server that only sends everything to https: its return put
        # in a location, so the CA's question is answered before it.
        if not is_tls:
            block = re.sub(r'^(\s*)return\s+30[12]\s+(https://[^;]+);[ \t]*$',
                           lambda m: '%slocation / { return 301 %s; }' % (m.group(1), m.group(2)),
                           block, count=1, flags=re.M)
        tls_done = tls_done or put_tls
        out.append(block)
        at = end
    out.append(text[at:])
    new_text = ''.join(out)
    if not tls_done:
        return False, 'no HTTPS server with ssl_certificate in %s - left as it is' % NGINX_SITE
    if dry:
        return True, new_text
    tls_now = '# Written by plexmon (Settings → Certificates and TLS) - taken over from\n' \
              '# %s as it was. Changed on the pages; changes made here are overwritten.\n' \
              % NGINX_SITE + '\n'.join(moved) + '\n'
    snap = _snapshot([NGINX_SITE, NGINX_TLS_CONF, NGINX_ACME_CONF])
    try:
        backup = STATE_DIR / 'backup'
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(NGINX_SITE), str(backup / ('%s.%s' % (NGINX_SITE.name,
                                                               time.strftime('%Y%m%d-%H%M%S')))))
        NGINX_SNIPPETS.mkdir(parents=True, exist_ok=True)
        ACME_WEBROOT.joinpath('.well-known', 'acme-challenge').mkdir(parents=True, exist_ok=True)
        write_replacing(NGINX_TLS_CONF, tls_now, 0o644)
        write_replacing(NGINX_ACME_CONF, ACME_CONF_TEXT % ACME_WEBROOT, 0o644)
        mode = os.stat(str(NGINX_SITE)).st_mode & 0o777
        write_replacing(NGINX_SITE, new_text, mode)
        ok, said = nginx_test()
        if not ok:
            raise ValueError('nginx does not take it: %s' % said)
        nginx_reload()
    except Exception as e:                              # noqa: BLE001
        _put_back(snap)
        return False, 'left as it was: %s' % e
    return True, 'adopted: %d line(s) went into %s; the CA is answered from %s' % (
        len(moved), NGINX_TLS_CONF, ACME_WEBROOT)


def _snapshot(paths):
    got = {}
    for p in paths:
        p = str(p)
        try:
            with open(p, 'rb') as f:
                got[p] = (f.read(), os.stat(p).st_mode & 0o777)
        except FileNotFoundError:
            got[p] = None
        except OSError:
            got[p] = None
    return got


def _put_back(snap):
    for p, was in snap.items():
        try:
            if was is None:
                if os.path.exists(p):
                    os.unlink(p)
            else:
                write_replacing(p, was[0], was[1])
        except OSError as e:
            log('%s could not be put back: %s' % (p, e), 'error')


def nginx_test():
    try:
        got = subprocess.run([NGINX_BIN, '-t'], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, 'nginx is not installed'
    except subprocess.SubprocessError as e:
        return False, str(e)
    said = (got.stderr or got.stdout or '').strip()
    return got.returncode == 0, said.splitlines()[-1] if said else ''


def nginx_reload():
    try:
        got = subprocess.run([SYSTEMCTL, 'reload', 'nginx'], capture_output=True, text=True,
                             timeout=60)
        if got.returncode != 0:
            got = subprocess.run([NGINX_BIN, '-s', 'reload'], capture_output=True, text=True,
                                 timeout=60)
        return got.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


CERT_NOW = {}


def cert_now(path):
    """What the certificate nginx has in `path` says - read again only when
    the file changes."""
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    had = CERT_NOW.get(str(path))
    if had and had[0] == (st.st_mtime, st.st_size):
        return had[1]
    try:
        with open(str(path)) as f:
            text = f.read(200000)
        m = PEM_CERT.search(text)
        info = cert_info(m.group(0)) if m else None
        if info:
            info['chain'] = max(0, len(PEM_CERT.findall(text)) - 1)
    except (OSError, ValueError):
        return None
    CERT_NOW[str(path)] = ((st.st_mtime, st.st_size), info)
    return info


def _dh_file(choice, had):
    if choice == 'none':
        return None
    if choice == had and NGINX_DH.exists():
        return str(NGINX_DH)
    if choice.startswith('ffdhe'):
        pem = _openssl(['genpkey', '-genparam', '-algorithm', 'DH', '-pkeyopt',
                        'group:%s' % choice], what='the DH group %s' % choice).decode()
    else:
        web_progress_set('making DH parameters of %s bits' % choice[3:], 2,
                         {'gen2048': 30, 'gen3072': 150, 'gen4096': 900}.get(choice))
        pem = _openssl(['dhparam', choice[3:]], what='making DH parameters').decode()
    write_replacing(NGINX_DH, pem, 0o644)
    return str(NGINX_DH)


def tls_conf_text(t, cert_path, key_path, dh_path):
    lines = ['# Written by plexmon (Settings → Certificates and TLS).',
             '# Changed on the pages; changes made here are overwritten.',
             'ssl_certificate     %s;' % cert_path,
             'ssl_certificate_key %s;' % key_path,
             'ssl_protocols %s;' % ' '.join(t['protocols'])]
    if t['ciphers']:
        lines.append('ssl_ciphers %s;' % t['ciphers'])
    lines.append('ssl_prefer_server_ciphers %s;' % ('on' if t['prefer'] else 'off'))
    if dh_path:
        lines.append('ssl_dhparam %s;' % dh_path)
    if t['hsts']:
        lines.append('add_header Strict-Transport-Security "max-age=%d" always;' % t['hsts'])
    return '\n'.join(lines) + '\n'


WEB_WORK = {'wake': threading.Event(), 'progress': None, 'busy': False}


def web_progress_set(words, step=None, expect=None):
    p = WEB_WORK.get('progress') or {'step': 1, 'total': 5, 'at': time.time()}
    if step is not None:
        p['step'] = step
    p.update(words=words, now=time.time(), expect_s=expect, at=p.get('at') or time.time())
    WEB_WORK['progress'] = p


def web_hash(store):
    prof = tls_profile(store, node_name())
    plex = {'on': bool(CONFIG.get('plex_cert')), 'domain': CONFIG.get('plex_cert_domain') or ''}
    if not prof:
        return '', plex
    keep = {k: prof.get(k) for k in ('cert', 'chain', 'key', 'preset', 'protocols', 'ciphers',
                                     'dh', 'prefer', 'hsts')}
    return hashlib.sha256(json.dumps([keep, plex], sort_keys=True).encode()).hexdigest(), plex


def web_status(store):
    return _meta_json(store, 'web:state:' + node_name()) or {}


def web_apply(store):
    """What the pages asked for, put in place - when it differs from what was."""
    want, plex = web_hash(store)
    st = web_status(store)
    if not want or st.get('hash') == want:
        return
    # A failed one is tried again only when something changes or Try again is pressed.
    if st.get('state') == 'failed' and st.get('tried') == want and \
            float(store.get_meta('web:retry:' + node_name()) or 0) <= float(st.get('at') or 0):
        return
    prof = tls_profile(store, node_name()) or {}
    t = tls_resolved(prof)
    said = []
    WEB_WORK.update(busy=True, progress={'step': 1, 'total': 5, 'at': time.time(),
                                         'now': time.time(), 'words': 'starting'})
    cert_path, key_path = nginx_conf_cert_paths()
    snap = _snapshot([cert_path, key_path, NGINX_TLS_CONF, NGINX_DH])
    try:
        adopted = nginx_adopted()
        if t.get('cert'):
            web_progress_set('writing the certificate and its key', 3)
            write_replacing(cert_path, t['cert'] + t['chain'], 0o644)
            write_replacing(key_path, t['key'], 0o600)
            said.append('certificate: %s, until %s' % ((prof.get('info') or {}).get('subject'),
                                                       stamp((prof.get('info') or {})
                                                             .get('not_after'))))
        if adopted:
            dh = _dh_file(t['dh'], st.get('dh'))
            web_progress_set('writing the TLS settings', 3)
            write_replacing(NGINX_TLS_CONF, tls_conf_text(t, cert_path, key_path, dh), 0o644)
            said.append('%s; %s; DH %s%s' % (' '.join(t['protocols']),
                                             'ciphers as set' if t['ciphers'] else
                                             'the ciphers of the preset', t['dh'],
                                             '; HSTS %d s' % t['hsts'] if t['hsts'] else ''))
        else:
            said.append('the TLS settings could not be written: %s is not plexmon\'s yet '
                        '(install.sh --upgrade adopts it) - only the certificate was put in'
                        % NGINX_SITE)
        web_progress_set('asking nginx, and reloading it', 4)
        ok, words = nginx_test()
        if not ok:
            raise ValueError('nginx does not take it: %s' % words)
        if not nginx_reload():
            raise ValueError('nginx could not be reloaded')
        said.append('nginx took it and was reloaded')
        if plex['on'] and t.get('cert'):
            web_progress_set('Plex Media Server: its own certificate', 5)
            said.append(plex_cert_apply(store, t, prof))
        state = {'state': 'done', 'hash': want, 'said': said, 'at': time.time(),
                 'dh': t['dh']}
    except Exception as e:                              # noqa: BLE001
        _put_back(snap)
        try:
            nginx_reload()
        except Exception:                               # noqa: BLE001
            pass
        said.append(str(e))
        state = {'state': 'failed', 'tried': want, 'hash': st.get('hash'), 'said': said,
                 'at': time.time(), 'dh': st.get('dh')}
        store.add_event('settings', 'the certificate could not be put in: %s - everything is '
                        'as it was' % e, 'bad')
    finally:
        WEB_WORK.update(busy=False, progress=None)
    store.set_meta('web:state:' + node_name(), json.dumps(state))
    if state['state'] == 'done':
        store.add_event('settings', 'the certificate and the TLS settings are in place: %s'
                        % '; '.join(said))


def web_state(store):
    st = web_status(store)
    want, _plex = web_hash(store)
    cert_path, _k = nginx_conf_cert_paths()
    return {'cert_now': cert_now(cert_path), 'state': st.get('state') or '',
            'said': (st.get('said') or [])[-6:], 'at': st.get('at'),
            'current': bool(st) and bool(want) and st.get('hash') == want,
            'waiting': bool(want) and st.get('hash') != want and not WEB_WORK['busy'],
            'progress': WEB_WORK.get('progress'), 'adopted': nginx_adopted(),
            'site': str(NGINX_SITE), 'cert_file': str(cert_path)}


def web_loop(store, stop):
    """The certificate put in by itself whenever the pages change it."""
    while not stop.is_set():
        WEB_WORK['wake'].wait(5)
        WEB_WORK['wake'].clear()
        try:
            with WEB_LOCK:
                web_apply(store)
        except Exception as e:                          # noqa: BLE001
            log('putting the certificate in: %s\n%s' % (e, traceback.format_exc()), 'error')


# ---- Plex Media Server's own certificate ------------------------------------------------
# Plex takes a custom certificate as a PKCS#12 file, its password and the
# domain it is for, named in Preferences.xml - read when it starts. Off unless
# switched on (Settings → Certificates and TLS → Plex Media Server).
PLEX_P12 = 'plexmon.p12'


def plex_user_ids():
    try:
        import pwd
        p = pwd.getpwnam('plex')
        return p.pw_uid, p.pw_gid
    except (KeyError, ImportError):
        return None


def plex_prefs_read():
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(str(PLEX_PREFS))
    except (OSError, ET.ParseError):
        return None
    return tree


def plex_prefs_now():
    tree = plex_prefs_read()
    if tree is None:
        return {}
    a = tree.getroot().attrib
    return {'path': a.get('customCertificatePath', ''), 'domain': a.get('customCertificateDomain',
                                                                        ''),
            'key_set': bool(a.get('customCertificateKey'))}


def plex_running():
    try:
        return subprocess.run([SYSTEMCTL, 'is-active', '--quiet', PLEX_SERVICE],
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def plex_cert_apply(store, t, prof):
    """The certificate as Plex's custom certificate: the .p12 written, its
    Preferences.xml told, Plex restarted when asked to."""
    if not PLEX_PREFS.exists():
        raise ValueError('Plex Media Server\'s Preferences.xml is not where it is looked for '
                         '(%s)' % PLEX_PREFS)
    domain = str(CONFIG.get('plex_cert_domain') or '').strip() or \
        str((prof.get('info') or {}).get('cn') or '').strip()
    if not domain or '*' in domain:
        raise ValueError('the domain for Plex is not known: give it (Plex’s custom certificate '
                         'domain)')
    pw = unseal_secret(store.get_meta('plex:p12pw') or '', 'plex:p12') or ''
    if not pw:
        pw = secrets.token_urlsafe(24)
        store.set_meta('plex:p12pw', seal_secret(pw, 'plex:p12'))
    ids = plex_user_ids()
    PLEX_CERT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(str(PLEX_CERT_DIR), 0o700)
    if ids:
        os.chown(str(PLEX_CERT_DIR), ids[0], ids[1])
    with tempfile.TemporaryDirectory(prefix='plexmon-p12-') as tmp:
        kf, cf, chf = (os.path.join(tmp, n) for n in ('k.pem', 'c.pem', 'ch.pem'))
        for p, v in ((kf, t['key']), (cf, t['cert']), (chf, t['chain'] or '')):
            with open(p, 'w') as f:
                f.write(v)
        args = ['pkcs12', '-export', '-inkey', kf, '-in', cf, '-passout', 'env:PLEXMON_PW',
                '-name', domain, '-certpbe', 'PBE-SHA1-3DES', '-keypbe', 'PBE-SHA1-3DES',
                '-macalg', 'sha1']
        if t['chain']:
            args += ['-certfile', chf]
        p12 = _openssl(args, env={'PLEXMON_PW': pw}, what='the PKCS#12 file for Plex')
    target = PLEX_CERT_DIR / PLEX_P12
    write_replacing(target, p12, 0o600, owner=ids)
    restart = bool(CONFIG.get('plex_cert_restart', 1))
    was_running = plex_running()
    if restart and was_running:
        subprocess.run([SYSTEMCTL, 'stop', PLEX_SERVICE], timeout=180)
    try:
        tree = plex_prefs_read()
        if tree is None:
            raise ValueError('Preferences.xml of Plex cannot be read')
        root = tree.getroot()
        root.set('customCertificatePath', str(target))
        root.set('customCertificateKey', pw)
        root.set('customCertificateDomain', domain)
        import xml.etree.ElementTree as ET
        text = ET.tostring(root, encoding='unicode')
        st = os.stat(str(PLEX_PREFS))
        backup = STATE_DIR / 'backup'
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(PLEX_PREFS), str(backup / 'Preferences.xml.before-plexmon'))
        write_replacing(PLEX_PREFS, '<?xml version="1.0" encoding="utf-8"?>\n' + text + '\n',
                        st.st_mode & 0o777, owner=(st.st_uid, st.st_gid))
    finally:
        if restart and was_running:
            subprocess.run([SYSTEMCTL, 'start', PLEX_SERVICE], timeout=180)
    store.set_meta('plex:cert', json.dumps({'at': time.time(), 'domain': domain,
                                            'path': str(target),
                                            'fingerprint': (prof.get('info') or {})
                                            .get('fingerprint')}))
    return 'Plex Media Server: %s for %s%s' % (target, domain,
                                               ', restarted' if restart and was_running else
                                               ' - it takes it at its next start')


def plex_cert_off(store, by):
    """Plex back to its own certificate: the names taken out of
    Preferences.xml (the .p12 stays, harmless)."""
    tree = plex_prefs_read()
    if tree is None:
        raise ValueError('Preferences.xml of Plex cannot be read')
    root = tree.getroot()
    if not root.get('customCertificatePath', '').endswith(PLEX_P12):
        return 'Plex does not use plexmon\'s certificate'
    was_running = plex_running()
    if was_running:
        subprocess.run([SYSTEMCTL, 'stop', PLEX_SERVICE], timeout=180)
    try:
        for k in ('customCertificatePath', 'customCertificateKey', 'customCertificateDomain'):
            root.attrib.pop(k, None)
        import xml.etree.ElementTree as ET
        st = os.stat(str(PLEX_PREFS))
        write_replacing(PLEX_PREFS, '<?xml version="1.0" encoding="utf-8"?>\n' +
                        ET.tostring(root, encoding='unicode') + '\n', st.st_mode & 0o777,
                        owner=(st.st_uid, st.st_gid))
    finally:
        if was_running:
            subprocess.run([SYSTEMCTL, 'start', PLEX_SERVICE], timeout=180)
    store.drop_meta('plex:cert')
    store.add_event('settings', '%s: Plex Media Server uses its own certificate again' % by,
                    'warn', who=by)
    return 'Plex Media Server uses its own certificate again'


def plex_view(store):
    got = _meta_json(store, 'plex:cert') or {}
    now = plex_prefs_now()
    return {'on': bool(CONFIG.get('plex_cert')), 'prefs': str(PLEX_PREFS),
            'there': PLEX_PREFS.exists(), 'ours': str(now.get('path') or '').endswith(PLEX_P12),
            'path': now.get('path') or '', 'domain': now.get('domain') or '',
            'at': got.get('at'), 'running': plex_running() if PLEX_PREFS.exists() else False}


# ---- the web, asked by this daemon (new versions, the CA, the DNS APIs) ----------------
def url_open(req, timeout=30, context=None, purpose='web'):
    """urllib.request.urlopen, through https_proxy when the service's
    environment names one (systemctl edit plexmon: Environment=https_proxy=…)."""
    if not isinstance(req, urllib.request.Request):
        req = urllib.request.Request(req)
    handlers = [urllib.request.ProxyHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


# ---- a DNS client of its own, without a library (otp's, from its mail) ------------------
def _dns_name(data, at):
    labels, jumped, end = [], False, at
    for _ in range(64):
        n = data[at]
        if n == 0:
            at += 1
            break
        if n & 0xC0 == 0xC0:
            if not jumped:
                end = at + 2
            at = ((n & 0x3F) << 8) | data[at + 1]
            jumped = True
            continue
        labels.append(data[at + 1:at + 1 + n].decode('ascii', 'replace'))
        at += 1 + n
    return '.'.join(labels), (end if jumped else at)


def _dns_ask(server, name, qtype, timeout):
    """One question to one DNS server: the raw answer. EDNS0 asks for room
    for a long answer; one that is cut short anyway is asked again over TCP
    (1.0.22)."""
    ident = secrets.randbits(16)
    q = struct.pack('>HHHHHH', ident, 0x0100, 1, 0, 0, 1)
    for label in name.split('.'):
        if not label:
            continue
        raw = label.encode('idna') if any(ord(c) > 127 for c in label) else label.encode()
        q += bytes([len(raw)]) + raw
    q += b'\x00' + struct.pack('>HH', qtype, 1)
    q += b'\x00' + struct.pack('>HHIH', 41, 4096, 0, 0)          # OPT: 4096 bytes
    fam = socket.AF_INET6 if ':' in server else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_DGRAM)
    deadline = time.time() + timeout
    question = q[12:-11]                    # the name, the type and the class asked
    try:
        # connect(): only answers from the server asked are read at all.
        s.connect((server, 53))
        s.send(q)
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise socket.timeout('no answer from %s' % server)
            s.settimeout(left)
            data = s.recv(65535)
            # Its own answer: the same ID, and the same question (1.0.33) -
            # and no waiting past the deadline however many others come.
            if len(data) >= 12 + len(question) and struct.unpack('>H', data[:2])[0] == ident \
                    and data[12:12 + len(question)].lower() == question.lower():
                break
    finally:
        s.close()
    if struct.unpack('>H', data[2:4])[0] & 0x0200:                # TC: cut short
        t = socket.create_connection((server, 53), timeout=timeout)
        try:
            t.sendall(struct.pack('>H', len(q)) + q)
            want, got = None, b''
            while want is None or len(got) < want + 2:
                chunk = t.recv(65535)
                if not chunk:
                    break
                got += chunk
                if want is None and len(got) >= 2:
                    want = struct.unpack('>H', got[:2])[0]
            data = got[2:]
        finally:
            t.close()
    return data


def _dns_records(data):
    """(rcode, [(type, name, rdata-offset, size)]) of an answer."""
    flags, qd, an = struct.unpack('>HHH', data[2:8])
    at = 12
    for _ in range(qd):
        _, at = _dns_name(data, at)
        at += 4
    out = []
    for _ in range(an):
        rname, at = _dns_name(data, at)
        rtype, _cls, _ttl, size = struct.unpack('>HHIH', data[at:at + 10])
        at += 10
        out.append((rtype, rname, at, size))
        at += size
    return flags & 0x0F, out


DNS_RCODES = {1: 'could not read the question', 2: 'failed (SERVFAIL)', 4: 'does not do that',
              5: 'refused to answer'}



# ---- certificates by ACME: Let's Encrypt and the like (otp 1.0.169) -----------------------
# Settings → Automatic certificates: the certificate of this Plex server
# ordered from an ACME CA - Let's Encrypt, its staging, or one of your own -
# by HTTP-01 (nginx answers /.well-known/acme-challenge/ from
# /var/www/plexmon-acme, where the answer is written for the moment the CA
# looks) or DNS-01, and renewed when fewer than acme_days are left. Signed
# with openssl; done in a thread of its own.
ACME_CAS = {'letsencrypt': 'https://acme-v02.api.letsencrypt.org/directory',
            'letsencrypt-staging': 'https://acme-staging-v02.api.letsencrypt.org/directory'}
ACME_WORK = {'busy': False, 'at': 0.0, 'says': '', 'target': ''}
ACME_EVERY = float(os.environ.get('PLEXMON_ACME_EVERY') or 3600)
ACME_RETRY = float(os.environ.get('PLEXMON_ACME_RETRY') or 6 * 3600)


class AcmeError(Exception):
    pass


def _b64u(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def acme_directory_url():
    ca = CONFIG.get('acme_ca') or 'letsencrypt'
    if ca == 'custom':
        return str(CONFIG.get('acme_url') or '').strip()
    return ACME_CAS.get(ca, ACME_CAS['letsencrypt'])


def _acme_ctx():
    ctx = ssl.create_default_context()
    pem = str(CONFIG.get('acme_ca_pem') or '').strip()
    if pem and CONFIG.get('acme_ca') == 'custom':
        ctx.load_verify_locations(cadata=pem)
    return ctx


def _acme_account_gone(kind, said):
    """The CA's error says the account it was asked with is no more (RFC 8555
    accountDoesNotExist; Boulder's 'unauthorized' for one deactivated or not found)."""
    if kind.endswith(':accountDoesNotExist') or kind == 'accountDoesNotExist':
        return True
    low = str(said or '').lower()
    return kind.endswith('unauthorized') and 'account' in low and \
        any(w in low for w in ('deactivated', 'not valid', 'not found', 'does not exist', 'revoked'))


class Acme(object):
    """One conversation with the CA: the account, its key, the nonces."""
    def __init__(self, store):
        self.store = store
        self.dir_url = acme_directory_url()
        if not re.match(r'^https://[^\s]+\Z', self.dir_url):
            raise AcmeError('the ACME directory is an https:// address')
        self.work = tempfile.mkdtemp(prefix='plexmon-acme-', dir=str(STATE_DIR))
        os.chmod(self.work, 0o700)
        self.nonce = None
        self.kid = None
        self.dir = self._get(self.dir_url)[0]
        self._account()

    def close(self):
        shutil.rmtree(self.work, ignore_errors=True)

    # -- HTTP ------------------------------------------------------------------------
    def _req(self, url, data=None, method=None, accept='application/json'):
        if not str(url).startswith('https://'):
            raise AcmeError('the CA sent an address that is not https: %s' % url)
        req = urllib.request.Request(url, data=data, method=method or ('POST' if data else 'GET'),
                                     headers={'User-Agent': 'plexmon/%s (acme)' % __version__,
                                              'Accept': accept})
        if data is not None:
            req.add_header('Content-Type', 'application/jose+json')
        try:
            with url_open(req, timeout=30, context=_acme_ctx()) as r:
                body, headers, status = r.read(1 << 20), r.headers, r.status
        except urllib.error.HTTPError as e:
            body, headers, status = e.read(1 << 20), e.headers, e.code
        except (OSError, ssl.SSLError) as e:
            raise AcmeError('the CA did not answer (%s): %s' % (
                urllib.parse.urlsplit(url).hostname, getattr(e, 'reason', e)))
        if headers.get('Replay-Nonce'):
            self.nonce = headers['Replay-Nonce']
        return body, headers, status

    def _get(self, url):
        body, headers, status = self._req(url)
        if status >= 400:
            raise AcmeError('the CA said %d: %s' % (status, body[:200]))
        return json.loads(body.decode('utf-8')), headers

    # -- signing ---------------------------------------------------------------------
    def _key_file(self):
        p = os.path.join(self.work, 'account.pem')
        if not os.path.exists(p):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(self.key_pem)
        return p

    def _jwk(self):
        der = _openssl(['pkey', '-in', self._key_file(), '-pubout', '-outform', 'DER'],
                       what='the account key')
        point = der[-64:]
        return {'crv': 'P-256', 'kty': 'EC', 'x': _b64u(point[:32]), 'y': _b64u(point[32:])}

    def thumbprint(self):
        jwk = self._jwk()
        return _b64u(hashlib.sha256(json.dumps(
            {'crv': jwk['crv'], 'kty': jwk['kty'], 'x': jwk['x'], 'y': jwk['y']},
            separators=(',', ':'), sort_keys=True).encode()).digest())

    def _sign(self, data):
        der = _openssl(['dgst', '-sha256', '-sign', self._key_file()], data=data,
                       what='signing for the CA')
        # DER SEQUENCE { INTEGER r, INTEGER s } -> r || s, 32 bytes each.
        i = 2 if der[1] < 0x80 else 3

        def integer(at):
            n = der[at + 1]
            v = der[at + 2:at + 2 + n].lstrip(b'\x00')
            return v.rjust(32, b'\x00'), at + 2 + n
        r, i = integer(i)
        s, _ = integer(i)
        return r + s

    def post(self, url, payload, use_jwk=False):
        anew = False
        for attempt in (1, 2, 3):
            if not self.nonce:
                self._req(self.dir['newNonce'], method='HEAD')
            protected = {'alg': 'ES256', 'nonce': self.nonce, 'url': url}
            if use_jwk or not self.kid:
                protected['jwk'] = self._jwk()
            else:
                protected['kid'] = self.kid
            p64 = _b64u(json.dumps(protected))
            b64 = '' if payload is None else _b64u(json.dumps(payload))
            sig = _b64u(self._sign(('%s.%s' % (p64, b64)).encode('ascii')))
            self.nonce = None
            body, headers, status = self._req(url, json.dumps(
                {'protected': p64, 'payload': b64, 'signature': sig}).encode('utf-8'),
                accept='application/json' if 'cert' not in url else
                'application/pem-certificate-chain')
            if status == 400 and b'badNonce' in body and attempt == 1:
                continue
            if status >= 400:
                try:
                    err = json.loads(body.decode('utf-8'))
                    said = err.get('detail') or err.get('type')
                    kind = str(err.get('type') or '')
                except (ValueError, AttributeError):
                    said, kind = body[:200].decode('utf-8', 'replace'), ''
                # The account kept here gone from the CA (1.0.187): deleted,
                # deactivated, a CA of the same address started anew. Each
                # renewal was refused for it until the certificates ran out;
                # now a new account is made, and the request asked again.
                if self.kid and not use_jwk and not anew and url != self.dir.get('newAccount') and \
                        _acme_account_gone(kind, said):
                    anew = True
                    log('ACME: the CA does not know the account any more (%s) - a new one is made'
                        % said, 'warn')
                    self._account(fresh=True)
                    continue
                raise AcmeError('the CA refused it: %s' % said)
            return body, headers

    def post_json(self, url, payload):
        body, headers = self.post(url, payload)
        return (json.loads(body.decode('utf-8')) if body else {}), headers

    # -- the account -----------------------------------------------------------------
    def _account(self, fresh=False):
        row = {} if fresh else (_meta_json(self.store, 'acme:account') or {})
        pem = unseal_secret(row.get('key') or '', 'acme') if row.get('key') else ''
        if row.get('directory') != self.dir_url or not pem:
            pem = ''
        if fresh:
            self.kid = None
            try:
                os.unlink(os.path.join(self.work, 'account.pem'))
            except OSError:
                pass
        if not pem:
            pem = _openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256'],
                           what='the account key').decode()
            row = {}
        self.key_pem = pem
        if row.get('kid'):
            self.kid = row['kid']
            return
        mail = str(CONFIG.get('acme_email') or '').strip()
        payload = {'termsOfServiceAgreed': True}
        if mail:
            payload['contact'] = ['mailto:' + mail]
        body, headers = self.post(self.dir['newAccount'], payload, use_jwk=True)
        self.kid = headers.get('Location')
        if not self.kid:
            raise AcmeError('the CA made no account')
        self.store.set_meta('acme:account', json.dumps({
            'key': seal_secret(pem, 'acme'), 'kid': self.kid, 'directory': self.dir_url,
            'email': mail, 'at': time.time()}))

    # -- an order --------------------------------------------------------------------
    def _poll(self, url, done, what, secs=90):
        end = time.time() + secs
        while True:
            got, _ = self.post_json(url, None)
            st = got.get('status')
            if st in done:
                return got
            if st == 'invalid':
                errs = [c.get('error', {}).get('detail') for c in got.get('challenges') or []
                        if c.get('error')] or [got.get('error', {}).get('detail')]
                raise AcmeError('%s: %s' % (what, '; '.join(e for e in errs if e) or 'invalid'))
            if time.time() > end:
                raise AcmeError('%s: the CA has not said in %d s (%s)' % (what, secs, st))
            time.sleep(2)

    def order(self, names, say):
        say('ordered from the CA')
        got, headers = self.post_json(self.dir['newOrder'], {
            'identifiers': [{'type': 'dns', 'value': n} for n in names]})
        order_url = headers.get('Location')
        thumb = self.thumbprint()
        for az in got.get('authorizations') or []:
            a, _ = self.post_json(az, None)
            if a.get('status') == 'valid':
                continue
            name = a.get('identifier', {}).get('value')
            kind = 'dns-01' if (CONFIG.get('acme_challenge') or 'http-01') == 'dns-01' else 'http-01'
            ch = [c for c in a.get('challenges') or [] if c.get('type') == kind]
            if not ch:
                raise AcmeError('the CA offers no %s challenge for %s' % (kind.upper(), name))
            token = ch[0]['token']
            if not re.match(r'^[A-Za-z0-9_-]{8,256}\Z', token):
                raise AcmeError('the CA sent a token that is not one')
            auth = token + '.' + thumb
            if kind == 'dns-01':
                # A TXT record in DNS (1.0.169): for a server the CA cannot
                # reach - nothing has to be open to the internet.
                txt = _b64u(hashlib.sha256(auth.encode()).digest())
                undo = acme_dns_put(self.store, name, txt, say)
                try:
                    say('the CA looks up _acme-challenge.%s' % name)
                    self.post_json(ch[0]['url'], {})
                    self._poll(az, ('valid',), 'the CA did not take %s' % name, 180)
                finally:
                    try:
                        undo()
                    except Exception as e:              # noqa: BLE001
                        log('ACME: the TXT record of %s was not taken away: %s' % (name, e), 'warn')
                continue
            # Written where nginx answers the CA from, for the moment it looks.
            acme_chal_put(token, auth)
            time.sleep(float(os.environ.get('PLEXMON_ACME_SETTLE') or 1))
            say('the CA looks at http://%s/.well-known/acme-challenge/' % name)
            try:
                self.post_json(ch[0]['url'], {})
                self._poll(az, ('valid',), 'the CA did not take %s' % name)
            finally:
                acme_chal_drop(token)
        key, csr = make_csr(names[0], names[1:], CONFIG.get('acme_key') or 'ec')
        der = _openssl(['req', '-in', '/dev/stdin', '-outform', 'DER'], data=csr.encode(),
                       what='the request')
        say('the certificate is made')
        o, _ = self.post_json(got['finalize'], {'csr': _b64u(der)})
        if o.get('status') != 'valid':
            o = self._poll(order_url, ('valid',), 'the order', 120)
        body, _ = self.post(o['certificate'], None)
        return key, body.decode('utf-8')


# ---- what is ordered, and when ----------------------------------------------------------
def acme_name_of(store, target):
    """The DNS name the certificate is for: the one given, else the name of
    the certificate it has now, else the machine's full name."""
    name = str(store.settings_saved().get('acme_node_name') or CONFIG.get('acme_node_name')
               or '').strip()
    if not name:
        info = (tls_profile(store, target) or {}).get('info') or {}
        if not info:
            info = cert_now(nginx_conf_cert_paths()[0]) or {}
        for n in [info.get('cn')] + list(info.get('san') or []):
            n = str(n or '').strip().lower()
            if re.match(r'^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z', n):
                name = n
                break
    if not name:
        name = socket.getfqdn()
    return name


def acme_before(store, target):
    """The certificate a target had before ACME replaced it, as the page may
    see it (no key)."""
    got = _meta_json(store, 'acme:before:' + target) or {}
    if not got.get('cert'):
        return None
    info = got.get('info') or {}
    return {'how': got.get('how'), 'subject': info.get('subject'), 'issuer': info.get('issuer'),
            'not_after': info.get('not_after'), 'at': got.get('at'),
            'ran_out': float(info.get('not_after') or 0) < time.time()}


def acme_put_back(store, target, by):
    """The certificate from before ACME back in place, ACME no longer renewing
    it; its TLS settings (protocols, ciphers, DH, HSTS) stay as they are now."""
    got = _meta_json(store, 'acme:before:' + target) or {}
    if not got.get('cert'):
        raise ValueError('there is no certificate from before ACME for this one')
    info = got.get('info') or {}
    if float(info.get('not_after') or 0) < time.time():
        raise ValueError('the certificate from before ACME ran out on %s - put a new one in on '
                         'Certificates and TLS' % stamp(info.get('not_after')))
    prof = tls_profile(store, target) or {}
    prof.update({k: got.get(k) for k in ('cert', 'chain', 'root', 'key', 'info', 'chain_info',
                                         'root_info', 'verify', 'how')})
    prof.pop('pending_key', None)
    prof.pop('csr', None)
    tls_save(store, target, prof, by)
    store.set_meta('acme:care:' + target, json.dumps({'on': False, 'at': time.time(), 'by': by}))
    store.db.run('DELETE FROM meta WHERE k = ?', ('acme:before:' + target,))
    store.add_event('settings', '%s put the certificate of %s from before ACME back (%s, until %s); '
                    'ACME no longer renews it' % (by, 'the VIP address' if target == 'vip' else
                                                  target, info.get('issuer') or '?',
                                                  stamp(info.get('not_after'))), 'warn', who=by)


def acme_care(store, target):
    """Whether ACME looks after this certificate (1.0.170). Said on either page;
    unsaid: yes for one from the CA, a self-signed one and none at all - never
    for one uploaded or answered to a request (a CA of your own): switching ACME
    on does not replace those."""
    got = _meta_json(store, 'acme:care:' + target)
    if got and 'on' in got:
        return bool(got['on'])
    prof = tls_profile(store, target) or {}
    how = str(prof.get('how') or '')
    return not prof.get('cert') or how == 'ACME' or how.startswith('self-signed')


def acme_care_set(store, target, on, by, why=''):
    was = acme_care(store, target)
    store.set_meta('acme:care:' + target, json.dumps({'on': bool(on), 'at': time.time(),
                                                     'by': by}))
    if was != bool(on) and CONFIG.get('acme'):
        store.add_event('settings', '%s: ACME %s the certificate of %s%s' % (
            by, 'looks after' if on else 'no longer renews', 'the VIP address' if target == 'vip'
            else target, (' - ' + why) if why else ''), 'info' if on else 'warn', who=by)


def acme_targets(store):
    """[(target, name)] ordered here: this server's, when ACME looks after it."""
    if not CONFIG.get('acme'):
        return []
    t = node_name()
    n = acme_name_of(store, t)
    return [(t, n)] if n and acme_care(store, t) else []


def acme_wanted(store):
    return [node_name()]


def acme_state(store, target):
    return _meta_json(store, 'acme:state:' + target) or {}


def acme_due(store, target, name):
    """Why this certificate is to be ordered now, or ''."""
    st = acme_state(store, target)
    if st.get('failed_at') and time.time() - float(st['failed_at']) < ACME_RETRY and \
            st.get('name') == name:
        return ''
    prof = tls_profile(store, target) or {}
    info = prof.get('info') or {}
    if prof.get('how') != 'ACME' or st.get('name') != name:
        return 'not ordered yet for %s' % name
    left = (float(info.get('not_after') or 0) - time.time()) / 86400
    days = float(CONFIG.get('acme_days') or 30)
    # A third of its life at most (1.0.187): a CA giving fewer days than
    # acme_days (short-lived certificates, a test CA) had every new one due
    # at once - ordered again every hour, until its limits refused them all.
    life = (float(info.get('not_after') or 0) - float(info.get('not_before') or 0)) / 86400
    if info.get('not_before') and life > 0:
        days = min(days, life / 3)
    if left < days:
        return '%d days left' % int(left)
    return ''


def acme_order(store, target, name, by, say=None):
    """One certificate ordered and put in; the state kept, an event written -
    and what was done on the way (1.0.175), for the card."""
    tell = say or (lambda t: None)
    steps = []

    def say(t):
        steps.append([round(time.time(), 1), str(t)[:300]])
        tell(t)
    if not re.match(r'^(?=.{1,253}\Z)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+'
                    r'[A-Za-z]{2,63}\Z', name or ''):
        raise AcmeError('%s is not a name a CA gives a certificate for (a full DNS name)' % name)
    st = {'name': name, 'at': time.time(), 'by': by, 'ca': acme_directory_url(), 'steps': steps}
    a = None
    try:
        a = Acme(store)
        key, chain = a.order([name], say)
        got = cert_bundle(chain, key)
        info = got['info']
        prof = tls_profile(store, target) or {}
        if prof.get('cert') and prof.get('how') != 'ACME':
            # The certificate it had - uploaded, from your own CA - kept to go
            # back to (1.0.172): ACME switched off or left alone, it is put
            # back with a click, its key as sealed as it was.
            store.set_meta('acme:before:' + target, json.dumps(
                {k: prof.get(k) for k in ('cert', 'chain', 'root', 'key', 'info', 'chain_info',
                                          'root_info', 'verify', 'how')}
                | {'at': time.time()}))
        prof.update(cert=got['cert'], chain=got['chain'], root=got['root'],
                    key=seal_secret(got['key'], 'web:tls'), info=info,
                    chain_info=got['chain_info'], root_info=got['root_info'],
                    verify=got['verify'], how='ACME')
        prof.pop('pending_key', None)
        prof.pop('csr', None)
        prof.setdefault('preset', 'standard')
        prof.setdefault('dh', 'none')
        tls_save(store, target, prof, by)
        st.update(ok=True, not_after=info.get('not_after'), issuer=info.get('issuer'),
                  ordered_at=time.time())
        st['steps'] = steps[-25:]
        store.set_meta('acme:state:' + target, json.dumps(st))
        store.add_event('settings', 'a certificate for %s (%s) came from the CA, good until %s'
                        % (name, target,
                           stamp(info.get('not_after')) if info.get('not_after') else '?'),
                        'info', who=by)
        return st
    except (AcmeError, ValueError, OSError) as e:
        if 'NXDOMAIN' in str(e) and (CONFIG.get('acme_challenge') or '') == 'dns-01':
            e = AcmeError('%s - the CA does not find the record: In your DNS → Check DNS, with '
                          'DNS servers for ACME set to public ones (1.1.1.1), shows where the way '
                          'to it stops (the CNAME, or the delegation of its zone)' % e)
        st.update(ok=False, failed_at=time.time(), said=str(e)[:600])
        old = acme_state(store, target)
        for k in ('not_after', 'issuer', 'ordered_at'):
            if old.get(k):
                st[k] = old[k]
        st['steps'] = steps[-25:]
        store.set_meta('acme:state:' + target, json.dumps(st))
        store.add_event('settings', 'no certificate for %s came from the CA: %s' % (name, e),
                        'bad', who=by)
        raise AcmeError(str(e))
    finally:
        if a:
            a.close()


def acme_tick(store):
    """Once an hour: what is due, ordered - in a thread of its own."""
    if not CONFIG.get('acme') or ACME_WORK['busy'] or \
            time.time() - ACME_WORK['at'] < ACME_EVERY:
        return
    ACME_WORK['at'] = time.time()
    due = [(t, n) for t, n in acme_targets(store) if acme_due(store, t, n)]
    if not due:
        return
    acme_start(store, due, 'plexmon (renewal)')


def acme_start(store, which, by):
    if ACME_WORK['busy']:
        raise ValueError('a certificate is being ordered already')
    ACME_WORK.update(busy=True, says='starting')

    def go():
        _ACME_DNS.on = True
        try:
            for t, n in which:
                ACME_WORK['target'] = t
                try:
                    acme_order(store, t, n, by, lambda s: ACME_WORK.update(says=s))
                except AcmeError as e:
                    log('ACME: %s' % e, 'warn')
        finally:
            ACME_WORK.update(busy=False, says='', target='')
    threading.Thread(target=go, name='plexmon-acme', daemon=True).start()


def _chal_path(token):
    if not re.match(r'^[A-Za-z0-9_-]{8,256}\Z', str(token or '')):
        raise AcmeError('the CA sent a token that is not one')
    return ACME_WEBROOT / '.well-known' / 'acme-challenge' / token


def acme_chal_put(token, auth):
    p = _chal_path(token)
    p.parent.mkdir(parents=True, exist_ok=True)
    for d in (ACME_WEBROOT, ACME_WEBROOT / '.well-known', p.parent):
        try:
            os.chmod(str(d), 0o755)
        except OSError:
            pass
    write_replacing(p, auth, 0o644)


def acme_chal_drop(token):
    try:
        _chal_path(token).unlink()
    except (OSError, AcmeError):
        pass


def acme_view(store):
    out = []
    for t in tls_targets(store):
        prof = tls_profile(store, t) or {}
        info = prof.get('info') or cert_now(nginx_conf_cert_paths()[0]) or {}
        out.append({'target': t, 'name': acme_name_of(store, t), 'state': acme_state(store, t),
                    'how': prof.get('how') or '', 'subject': info.get('subject'),
                    'issuer': info.get('issuer'), 'not_after': info.get('not_after'),
                    'care': acme_care(store, t), 'wanted': t in acme_wanted(store),
                    'before': acme_before(store, t),
                    'named': bool(str(store.settings_saved().get('acme_node_name') or '')
                                  .strip())})
    mine = [t for t, _ in acme_targets(store)]
    acc = _meta_json(store, 'acme:account') or {}
    return {'on': bool(CONFIG.get('acme')), 'targets': out, 'mine': mine,
            'directory': acme_directory_url(), 'account': {
                'kid': acc.get('kid'), 'email': acc.get('email'), 'at': acc.get('at')},
            'work': dict(ACME_WORK), 'node': node_name(),
            'challenge': CONFIG.get('acme_challenge') or 'http-01',
            'dns_guide': acme_dns_guide(store),
            'manual': {k: v for k, v in (_meta_json(store, 'acme:manual') or {}).items()
                       if k in ('rr', 'txt', 'at', 'done')} or None}


def acme_probe(store, target):
    """Whether http://NAME/.well-known/acme-challenge/ reaches this server's
    answer - asked from here, as the CA will (a redirect to https followed
    the way the CA follows it, its certificate not checked)."""
    name = acme_name_of(store, target) if target else ''
    if not name:
        raise ValueError('which certificate? its name is not known')
    token = 'plexmon-probe-' + secrets.token_urlsafe(16)
    want = token + '.' + secrets.token_urlsafe(8)
    acme_chal_put(token, want)
    url = 'http://%s/.well-known/acme-challenge/%s' % (name, token)
    began = time.time()
    out = {'name': name, 'url': url, 'ok': False}
    scope = acme_dns_scope()
    scope.__enter__()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={'User-Agent': 'plexmon/%s (acme probe)' %
                                                   __version__})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=10) as r:
            body = r.read(4096).decode('utf-8', 'replace').strip()
        out.update(ok=body == want, status=r.status,
                   said='answered as the CA wants it' if body == want else
                   'something answers on port 80, but not plexmon\'s answer: %r' % body[:80])
    except urllib.error.HTTPError as e:
        out['said'] = 'port 80 answers %d - nginx does not answer /.well-known/acme-challenge/ ' \
                      'from %s (is %s plexmon\'s? install.sh --upgrade adopts it)' % (
                          e.code, ACME_WEBROOT, NGINX_SITE)
    except (OSError, ValueError) as e:
        out['said'] = 'port 80 of %s cannot be reached: %s' % (name, getattr(e, 'reason', e))
    finally:
        scope.__exit__()
        acme_chal_drop(token)
    out['ms'] = int((time.time() - began) * 1000)
    return out


# ---- DNS-01: the TXT record put in DNS (1.0.169) -------------------------------------
# For a server the CA cannot reach: _acme-challenge.NAME TXT, put in by
#   rfc2136    - nsupdate with a TSIG key (BIND, PowerDNS, Knot...), the key in a
#                file only otp reads for the moment it runs, never on a command line;
#   cloudflare - its API, with a token that may edit the zone's DNS;
#   acmedns    - an acme-dns server (_acme-challenge.NAME a CNAME to its subdomain);
#   manual     - shown on the page; the order waits until it is said to be there.
# Then waited for (acme_dns_wait s) and taken away again once the CA has looked.
NSUPDATE = os.environ.get('PLEXMON_NSUPDATE') or 'nsupdate'


def _dns_wait(say, least=0):
    w = max(int(CONFIG.get('acme_dns_wait') or 0),
            int(os.environ.get('PLEXMON_ACME_DNS_LEAST', least)))
    if w:
        say('waiting %d s for DNS to carry it' % w)
        time.sleep(w)


def acme_dns_where(name):
    """(where the TXT record goes, the CNAME your DNS needs or None) - 1.0.170.
    The record goes to _acme-challenge.NAME itself, or - with a zone of its own
    for them (deSEC, acme_dns_alias) - to _acme-challenge.LABEL.ZONE, and
    _acme-challenge.NAME is a CNAME to it in your DNS, put in once: the CA
    follows it. So a domain whose DNS has no API gets certificates by itself."""
    name = str(name or '').rstrip('.').lower()
    own = '_acme-challenge.' + name
    how = CONFIG.get('acme_dns') or 'manual'
    if how == 'acmedns':
        url = str(CONFIG.get('acme_dns_url') or '').strip()
        host = urllib.parse.urlparse(url).hostname or ''
        sub = str(CONFIG.get('acme_dns_subdomain') or '').strip()
        return own, ('%s.%s' % (sub, host) if sub and host else '')
    zone = str(CONFIG.get('acme_dns_alias') or '').strip().rstrip('.').lower()
    if how == 'desec':
        dom = str(CONFIG.get('acme_desec_domain') or '').strip().rstrip('.').lower()
        if dom and (name == dom or name.endswith('.' + dom)):
            return own, None                            # its DNS is at deSEC itself
        if not zone or not (zone == dom or zone.endswith('.' + dom)):
            zone = dom
    if not zone or name == zone or name.endswith('.' + zone):
        return own, None
    there = '_acme-challenge.%s.%s' % (name.split('.')[0], zone)
    return there, there


def acme_dns_put(store, name, txt, say):
    """The TXT record put in; returns what takes it away again."""
    how = CONFIG.get('acme_dns') or 'manual'
    rr, cname = acme_dns_where(name)
    if cname and how != 'acmedns':
        say('the TXT record %s put in DNS (%s; _acme-challenge.%s is a CNAME to it)'
            % (rr, how, name))
    else:
        say('the TXT record %s put in DNS (%s)' % (rr, how))
    if how == 'rfc2136':
        undo = _dns_rfc2136(rr, txt)
    elif how == 'cloudflare':
        undo = _dns_cloudflare(rr, txt)
    elif how == 'acmedns':
        undo = _dns_acmedns(txt)
    elif how == 'desec':
        undo = _dns_desec(rr, txt)
    else:
        undo = _dns_manual(store, rr, txt, say)
    if how == 'desec':
        _desec_seen(rr, txt, say)
    else:
        _dns_wait(say)
        if how != 'manual':
            acme_txt_seen(name, txt, say)
    return undo


DESEC_NS = ('ns1.desec.io', 'ns2.desec.org')


def _desec_seen(rr, txt, say):
    """deSEC's own name servers asked until both have the record (1.0.175),
    then a while more: they are anycast, and the CA asks from several places
    of the world - "During secondary validation: NXDOMAIN" was a place deSEC
    had not reached yet. Asked straight, never through a resolver, so no
    resolver keeps "not there" for the CA."""
    _dns_wait(say)
    addrs = []
    for ns in DESEC_NS:
        try:
            addrs += [(ns, a) for t, a in dns_ask(ns, 1) if t == 'A'][:1]
        except OSError:
            pass
    if not addrs:
        say('deSEC\'s name servers not found - waited %d s instead' % 120)
        _dns_wait(say, 120)
        return
    began = time.time()
    end = began + float(os.environ.get('PLEXMON_DESEC_PROPAGATION') or 300)
    while True:
        seen = []
        for ns, a in addrs:
            try:
                rc, got = _dns_parse(_dns_ask(a, rr, 16, 3.0))
                if txt in [v for t, v in got if t == 'TXT']:
                    seen.append(ns)
            except OSError:
                pass
        if len(seen) == len(addrs):
            break
        if time.time() > end:
            say('%s TXT not on every deSEC name server yet (%s) - the CA is asked anyway'
                % (rr, ', '.join(seen) or 'none'))
            return
        # What is waited for, said as it goes (1.0.176): the line said "waited
        # 5 s" for minutes while deSEC was being asked.
        ACME_WORK['says'] = 'waiting for deSEC\'s name servers to have the record - %s so far, %d s' % (
            ', '.join(seen) or 'none', int(time.time() - began))
        time.sleep(5)
    settle = float(os.environ.get('PLEXMON_DESEC_SETTLE') or 60)
    say('%s TXT is on %s after %d s; %d s more for deSEC\'s other places' % (
        rr, ', '.join(seen), int(time.time() - began), settle))
    left = settle
    while left > 0:
        ACME_WORK['says'] = '%d s more for deSEC\'s other places of the world, then the CA is asked' % left
        time.sleep(min(5, left))
        left -= 5


def _dns_rfc2136(rr, txt):
    server = str(CONFIG.get('acme_dns_server') or '').strip()
    if not server:
        raise AcmeError('the DNS server for the updates is not set')
    keyname = str(CONFIG.get('acme_dns_tsig_name') or '').strip()
    secret = unseal_secret(CONFIG.get('acme_dns_tsig_secret') or '') or ''
    alg = CONFIG.get('acme_dns_tsig_alg') or 'hmac-sha256'
    host, _, port = server.partition(':')
    zone = str(CONFIG.get('acme_dns_zone') or '').strip()

    def run(op):
        work = tempfile.mkdtemp(prefix='plexmon-dns-', dir=str(STATE_DIR))
        os.chmod(work, 0o700)
        try:
            args = [NSUPDATE]
            if keyname:
                kf = os.path.join(work, 'tsig.key')
                fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write('key "%s" {\n  algorithm %s;\n  secret "%s";\n};\n' % (keyname, alg, secret))
                args += ['-k', kf]
            script = 'server %s %s\n' % (host, port or '53')
            if zone:
                script += 'zone %s\n' % zone
            script += 'update %s %s 60 TXT "%s"\nsend\n' % (op, rr, txt) if op == 'add' else \
                'update delete %s TXT "%s"\nsend\n' % (rr, txt)
            try:
                got = subprocess.run(args, input=script, capture_output=True, text=True, timeout=60)
            except FileNotFoundError:
                raise AcmeError('nsupdate is not on this machine (dnf install bind-utils)')
            if got.returncode != 0:
                raise AcmeError('the DNS server did not take the update: %s'
                                % (got.stderr or got.stdout).strip()[-300:])
        finally:
            shutil.rmtree(work, ignore_errors=True)
    run('add')
    return lambda: run('delete')


def _cf(method, path, body=None):
    tok = unseal_secret(CONFIG.get('acme_dns_token') or '') or ''
    if not tok:
        raise AcmeError('the Cloudflare API token is not set')
    base = str(os.environ.get('PLEXMON_CF_API') or 'https://api.cloudflare.com/client/v4').rstrip('/')
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + tok,
                                          'Content-Type': 'application/json',
                                          'User-Agent': 'plexmon/%s (acme)' % __version__})
    try:
        with url_open(req, timeout=30, context=ssl.create_default_context()
                      if base.startswith('https') else None) as r:
            got = json.loads(r.read(1 << 20).decode())
    except urllib.error.HTTPError as e:
        try:
            got = json.loads(e.read().decode())
        except ValueError:
            got = {}
        raise AcmeError('Cloudflare said %d: %s' % (e.code, '; '.join(
            x.get('message', '') for x in got.get('errors') or []) or e.reason))
    if not got.get('success'):
        raise AcmeError('Cloudflare said no: %s' % '; '.join(
            x.get('message', '') for x in got.get('errors') or []))
    return got.get('result')


def _dns_cloudflare(rr, txt):
    labels = rr.split('.')
    zone_id = None
    for i in range(1, len(labels) - 1):
        z = '.'.join(labels[i:])
        got = _cf('GET', '/zones?name=' + urllib.parse.quote(z))
        if got:
            zone_id = got[0]['id']
            break
    if not zone_id:
        raise AcmeError('Cloudflare has no zone for %s that the token may see' % rr)
    rec = _cf('POST', '/zones/%s/dns_records' % zone_id,
              {'type': 'TXT', 'name': rr, 'content': txt, 'ttl': 60})
    return lambda: _cf('DELETE', '/zones/%s/dns_records/%s' % (zone_id, rec['id']))


def _dns_acmedns(txt):
    url = str(CONFIG.get('acme_dns_url') or '').strip().rstrip('/')
    if not url:
        raise AcmeError('the acme-dns server is not set')
    req = urllib.request.Request(url + '/update', method='POST', data=json.dumps(
        {'subdomain': str(CONFIG.get('acme_dns_subdomain') or ''), 'txt': txt}).encode(),
        headers={'X-Api-User': str(CONFIG.get('acme_dns_user') or ''),
                 'X-Api-Key': unseal_secret(CONFIG.get('acme_dns_key') or '') or '',
                 'Content-Type': 'application/json'})
    try:
        with url_open(req, timeout=30, context=ssl.create_default_context()
                      if url.startswith('https') else None) as r:
            r.read(4096)
    except urllib.error.HTTPError as e:
        raise AcmeError('acme-dns said %d: %s' % (e.code, e.read(300).decode('utf-8', 'replace')))
    except OSError as e:
        raise AcmeError('acme-dns did not answer: %s' % getattr(e, 'reason', e))
    return lambda: None             # acme-dns keeps the last two; nothing to take away


def _desec(method, path, body=None):
    """deSEC's API (1.0.170): Authorization: Token; a 429 waited out (it says
    how long) up to twice."""
    tok = unseal_secret(CONFIG.get('acme_desec_token') or '') or ''
    if not tok:
        raise AcmeError('the deSEC API token is not set')
    base = str(os.environ.get('PLEXMON_DESEC_API') or 'https://desec.io/api/v1').rstrip('/')
    for attempt in range(3):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Token ' + tok,
                                              'Content-Type': 'application/json',
                                              'User-Agent': 'plexmon/%s (acme)' % __version__})
        try:
            with url_open(req, timeout=30, context=ssl.create_default_context()
                          if base.startswith('https') else None) as r:
                raw = r.read(1 << 20)
                return json.loads(raw.decode()) if raw.strip() else None
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == 'GET':
                return None
            if e.code == 429 and attempt < 2:
                try:
                    pause = float(e.headers.get('Retry-After') or 5)
                except ValueError:
                    pause = 5
                time.sleep(min(max(pause, 1), 60))
                continue
            said = e.read(400).decode('utf-8', 'replace')
            raise AcmeError('deSEC said %d: %s' % (e.code, said.strip() or e.reason))
        except OSError as e:
            raise AcmeError('deSEC did not answer: %s' % getattr(e, 'reason', e))
    raise AcmeError('deSEC asked to wait too long (429)')


def _dns_desec(rr, txt):
    dom = str(CONFIG.get('acme_desec_domain') or '').strip().rstrip('.').lower()
    if not dom:
        raise AcmeError('the domain at deSEC is not set')
    if not rr.endswith('.' + dom):
        raise AcmeError('%s is not in %s' % (rr, dom))
    sub = rr[:-len(dom) - 1]
    path = '/domains/%s/rrsets/' % urllib.parse.quote(dom)
    info = _desec('GET', '/domains/%s/' % urllib.parse.quote(dom))
    if info is None:
        raise AcmeError('deSEC has no domain %s that the token may see' % dom)
    ttl = max(int(info.get('minimum_ttl') or 3600), 60)
    one = '"%s"' % txt

    def have():
        got = _desec('GET', '%s%s/TXT/' % (path, urllib.parse.quote(sub))) or {}
        return list(got.get('records') or [])

    recs = have()
    if one not in recs:
        recs.append(one)
    _desec('PATCH', path, [{'subname': sub, 'type': 'TXT', 'ttl': ttl, 'records': recs}])

    def undo():
        left = [x for x in have() if x != one]
        _desec('PATCH', path, [{'subname': sub, 'type': 'TXT', 'ttl': ttl, 'records': left}])
    return undo


ACME_MANUAL_WAIT = float(os.environ.get('PLEXMON_ACME_MANUAL_WAIT') or 3600)


def _dns_manual(store, rr, txt, say):
    """Shown on the page; waited for until somebody says it is in DNS."""
    ident = secrets.token_hex(6)
    store.set_meta('acme:manual', json.dumps({'id': ident, 'rr': rr, 'txt': txt, 'at': time.time()}))
    say('waiting: put %s TXT "%s" in DNS, then say so on the page' % (rr, txt))
    end = time.time() + ACME_MANUAL_WAIT
    try:
        while time.time() < end:
            got = _meta_json(store, 'acme:manual') or {}
            if got.get('id') != ident:
                raise AcmeError('the manual DNS step was given up')
            if got.get('done'):
                return lambda: None
            time.sleep(2)
        raise AcmeError('nobody said the TXT record was in DNS within an hour')
    finally:
        got = _meta_json(store, 'acme:manual') or {}
        if got.get('id') == ident:
            store.db.run("DELETE FROM meta WHERE k = 'acme:manual'")


# ---- what goes into your DNS, and whether it is there (1.0.170) -------------------------
def acme_resolvers():
    """The DNS servers ACME asks (1.0.172): its own setting, else the
    machine's (/etc/resolv.conf) - never the mail's."""
    out = []
    for w in re.split(r'[\s,;]+', str(CONFIG.get('acme_resolvers') or '')):
        try:
            out.append(str(ipaddress.ip_address(w)))
        except ValueError:
            pass
    return out


def _os_resolvers():
    out = []
    try:
        with open('/etc/resolv.conf') as f:
            for line in f:
                bits = line.split()
                if len(bits) >= 2 and bits[0] == 'nameserver':
                    out.append(bits[1])
    except OSError:
        pass
    return out or ['127.0.0.1']


def dns_ask(name, qtype):
    """One question to ACME's DNS servers (the mail's own DNS client):
    [(type, text)] of the answer - CNAME (5), TXT (16), A (1), AAAA (28), NS (2).
    [] for a name that is not there; OSError when no resolver answers."""
    return dns_ask_rc(name, qtype)[1]


def dns_ask_rc(name, qtype):
    """dns_ask, and whether the name is there at all: (rcode, records) - 3 is
    NXDOMAIN, which a CNAME to a name nobody serves ends in too."""
    last = None
    for srv in (acme_resolvers() or _os_resolvers())[:3]:
        try:
            data = _dns_ask(srv, str(name).rstrip('.'), qtype, 3.0)
        except OSError as e:
            last = e
            continue
        rcode, out = _dns_parse(data)
        if rcode in (0, 3):
            return rcode, out
        last = OSError(DNS_RCODES.get(rcode, 'rcode %d' % rcode))
    raise OSError('no DNS server answered (%s)' % (last or 'none'))


def _dns_parse(data):
    """(rcode, [(type, text)]) of one answer."""
    rcode, recs = _dns_records(data)
    if rcode:
        return rcode, []
    out = []
    for rtype, _n, at, size in recs:
        rd = data[at:at + size]
        if rtype == 5:
            out.append(('CNAME', _dns_name(data, at)[0].lower()))
        elif rtype == 16:
            j, parts = 0, []
            while j < len(rd):
                parts.append(rd[j + 1:j + 1 + rd[j]].decode('utf-8', 'replace'))
                j += 1 + rd[j]
            out.append(('TXT', ''.join(parts)))
        elif rtype == 1 and size == 4:
            out.append(('A', socket.inet_ntop(socket.AF_INET, rd)))
        elif rtype == 28 and size == 16:
            out.append(('AAAA', socket.inet_ntop(socket.AF_INET6, rd)))
        elif rtype == 2:
            out.append(('NS', _dns_name(data, at)[0].lower()))
    return 0, out


# Names looked up by ACME's DNS servers (1.0.172): in the threads of this page
# only - the CA, the DNS API, Try port 80 - every other lookup of the daemon
# as the machine's.
_ACME_DNS = threading.local()
_SYS_GETADDRINFO = socket.getaddrinfo


def _acme_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if getattr(_ACME_DNS, 'on', False) and host and acme_resolvers():
        h = host.decode() if isinstance(host, bytes) else str(host)
        try:
            ipaddress.ip_address(h.strip('[]'))
        except ValueError:
            if h not in ('localhost',):
                addrs = []
                for qtype, kind in ((1, 'A'), (28, 'AAAA')):
                    if family in (0, socket.AF_INET if kind == 'A' else socket.AF_INET6):
                        try:
                            addrs += [a for t, a in dns_ask(h, qtype) if t == kind]
                        except OSError:
                            pass
                if not addrs:
                    raise socket.gaierror(socket.EAI_NONAME, '%s is not known to %s' % (
                        h, ', '.join(acme_resolvers())))
                out = []
                for a in addrs:
                    out += _SYS_GETADDRINFO(a, port, family, type, proto,
                                            flags | socket.AI_NUMERICHOST)
                return out
    return _SYS_GETADDRINFO(host, port, family, type, proto, flags)


socket.getaddrinfo = _acme_getaddrinfo


class acme_dns_scope(object):
    """with acme_dns_scope(): names looked up by ACME's DNS servers."""
    def __enter__(self):
        self.was = getattr(_ACME_DNS, 'on', False)
        _ACME_DNS.on = True

    def __exit__(self, *a):
        _ACME_DNS.on = self.was


def acme_txt_seen(name, txt, say):
    """After the wait: whether ACME's DNS servers see the TXT record (through
    its CNAME) - asked again for up to two minutes; the CA asked either way."""
    rr = '_acme-challenge.' + name.rstrip('.')
    end = time.time() + float(os.environ.get('PLEXMON_ACME_SEEN_WAIT') or 120)
    while True:
        try:
            got = [v for t, v in dns_ask(rr, 16) if t == 'TXT']
        except OSError as e:
            say('DNS could not be asked whether the record is there (%s): the CA is asked anyway'
                % str(e)[:80])
            return False
        if txt in got:
            say('%s TXT is seen by %s' % (rr, ', '.join(acme_resolvers()) or 'this machine\'s DNS'))
            return True
        if time.time() > end:
            say('%s TXT is not seen yet by %s - the CA is asked anyway' % (
                rr, ', '.join(acme_resolvers()) or 'this machine\'s DNS'))
            return False
        time.sleep(5)


def acme_dns_guide(store):
    """What each certificate asked for needs in your DNS, by how the CA
    checks it - for the card In your DNS."""
    rows = []
    http = (CONFIG.get('acme_challenge') or 'http-01') != 'dns-01'
    how = CONFIG.get('acme_dns') or 'manual'
    addr = {node_name(): node_address()}
    for t in acme_wanted(store):
        name = acme_name_of(store, t)
        if not name:
            rows.append({'target': t, 'name': '', 'type': '', 'record': '', 'value': '',
                         'why': 'its name is not known yet'})
            continue
        if http:
            where = addr.get(t) or ''
            rows.append({'target': t, 'name': name, 'type': 'A', 'record': name, 'value': where,
                         'why': 'the name points at an address that reaches port 80 of %s' % t,
                         'check': 'a'})
            continue
        rr, cname = acme_dns_where(name)
        if how == 'desec' and cname:
            dom = str(CONFIG.get('acme_desec_domain') or '').strip().rstrip('.').lower()
            if dom and not dom.endswith('.dedyn.io') and not any(
                    x.get('type') == 'NS' for x in rows):
                # A domain of your own at deSEC (1.0.174): the zone above it
                # must send the internet there - NS records, once.
                rows.append({'target': '', 'name': dom, 'type': 'NS', 'record': dom,
                             'value': 'ns1.desec.io. ns2.desec.org.',
                             'why': 'once, in the zone above it: %s at deSEC' % dom,
                             'check': 'ns'})
        own = '_acme-challenge.' + name
        if how == 'acmedns' or cname:
            rows.append({'target': t, 'name': name, 'type': 'CNAME', 'record': own,
                         'value': (cname or '?') + '.', 'why': 'once, by hand, in your DNS',
                         'check': 'cname'})
        elif how == 'manual':
            rows.append({'target': t, 'name': name, 'type': 'TXT', 'record': rr,
                         'value': 'shown here at every order', 'why': 'by hand, at every order'})
        else:
            rows.append({'target': t, 'name': name, 'type': 'TXT', 'record': rr, 'value': '',
                         'why': 'nothing to do: plexmon puts it in and takes it away'})
    return {'rows': rows, 'challenge': 'http-01' if http else 'dns-01', 'dns': how}


def acme_dns_check(store):
    """Each row of the guide looked up from this node, as the CA will."""
    g = acme_dns_guide(store)
    for r in g['rows']:
        if not r.get('check'):
            continue
        try:
            if r['check'] == 'cname':
                rc, recs = dns_ask_rc(r['record'], 5)
                got = [x[1] for x in recs if x[0] == 'CNAME']
                want = r['value'].rstrip('.').lower()
                r['found'] = got[0] + '.' if got else ''
                r['ok'] = bool(got) and got[0].rstrip('.') == want
                r['said'] = 'there' if r['ok'] else (
                    'it points elsewhere: %s' % r['found'] if got else 'not there yet')
            elif r['check'] == 'ns':
                rc, recs = dns_ask_rc(r['record'], 2)
                got = sorted(x[1] for x in recs if x[0] == 'NS')
                r['found'] = ' '.join(got)
                r['ok'] = any(x.rstrip('.').endswith(('desec.io', 'desec.org')) for x in got)
                r['said'] = 'there' if r['ok'] else (
                    ('it is served by %s, not deSEC' % r['found']) if got else
                    'not delegated: the internet does not know %s' % r['record'])
            else:
                with acme_dns_scope():
                    got = sorted({a[4][0] for a in socket.getaddrinfo(
                        r['record'], 80, proto=socket.IPPROTO_TCP)})
                r['found'] = ', '.join(got)
                r['ok'] = bool(got)
                r['said'] = 'it points at %s' % r['found'] if got else 'not there'
        except (OSError, socket.gaierror) as e:
            r.update(ok=False, said='could not be looked up: %s' % str(e)[:120])
    g['at'] = time.time()
    g['resolvers'] = acme_resolvers() or ['this machine\'s DNS']
    g['own_resolvers'] = bool(acme_resolvers())
    return g




# ---------------------------------------------------------------------------
# A new version put in from the pages (otp 1.0.37 on, for one server)
# ---------------------------------------------------------------------------
# The package is checked (plexmon_upgrade.py --inspect: signed by a release
# key this machine trusts, every file the one signed), kept in
# /var/lib/plexmon/upgrade/, and - Upgrade pressed - handed to root's helper
# through request.json, which plexmon-upgrade.path starts apart from
# plexmon.service. Its install.sh --upgrade restarts plexmon; the new version
# checks itself and says done.
UPGRADE_DIR = STATE_DIR / 'upgrade'
UPGRADE_HELPER = Path(os.environ.get('PLEXMON_UPGRADE_HELPER',
                                     str(LIB_DIR / 'plexmon_upgrade.py')))
UPGRADE_MOST = 64 * 1024 * 1024
UPGRADE_TIMEOUT_S = 30 * 60
UPGRADE_PATH_UNIT = Path('/etc/systemd/system/plexmon-upgrade.path')
FLOOR_FILE = LIB_DIR / 'downgrade-floor'


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split('.')[:3])
    except ValueError:
        return (0, 0, 0)


def upgrade_inspect(path):
    if not UPGRADE_HELPER.exists():
        raise ValueError('plexmon_upgrade.py is not installed beside plexmon.py; upgrade once '
                         'on the command line (install.sh --upgrade)')
    try:
        r = subprocess.run([sys.executable, '-I', str(UPGRADE_HELPER), '--inspect', str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        got = json.loads(r.stdout.decode('utf-8', 'replace') or '{}')
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise ValueError('the package could not be checked: %s' % e)
    if not got.get('ok'):
        raise ValueError(got.get('said') or 'the package was refused')
    return got


def upgrade_store_package(store, path, info, by, downgrade=False):
    """The package kept in the upgrade folder, the one before taken away."""
    UPGRADE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    for old in UPGRADE_DIR.glob('plexmon-*.tar.gz'):
        try:
            old.unlink()
        except OSError:
            pass
    out = UPGRADE_DIR / ('plexmon-%s.tar.gz' % info['version'])
    write_replacing(out, raw, 0o640)
    meta = {'sha': sha, 'version': info['version'], 'bytes': len(raw), 'file': out.name,
            'signed_by': info.get('signed_by'), 'changelog': info.get('changelog', ''),
            'by': by, 'at': time.time(), 'downgrade': bool(downgrade)}
    store.set_meta('upgrade:pkg', json.dumps(meta))
    return meta


def upgrade_package_meta(store):
    meta = _meta_json(store, 'upgrade:pkg')
    if meta and not (UPGRADE_DIR / str(meta.get('file') or '')).exists():
        store.drop_meta('upgrade:pkg')
        return None
    return meta


def upgrade_plan(store):
    plan = _meta_json(store, 'upgrade:plan')
    if not plan:
        return None
    plan['drain_s'] = 0
    row = _meta_json(store, 'upgrade:node:' + node_name()) or {}
    if row.get('id') != plan.get('id'):
        row = {'state': 'waiting'}
    row['name'] = node_name()
    plan['steps'] = [row]
    st = row.get('state')
    if st == 'failed':
        plan['state'] = 'failed'
    elif st == 'done':
        plan['state'] = 'done'
    elif plan.get('cancelled'):
        plan['state'] = 'cancelled'
    else:
        plan['state'] = 'running'
    return plan


def root_floor():
    try:
        text = FLOOR_FILE.read_text().strip()
        st = os.stat(str(FLOOR_FILE))
    except OSError:
        return None
    if st.st_uid != 0 or st.st_mode & 0o022 or not re.match(r'^\d+\.\d+\.\d+\Z', text):
        return None
    return text


def downgrade_lowest(store):
    floors = [DOWNGRADE_FLOOR, root_floor() or '0.0.0',
              store.get_meta('downgrade:floor:' + node_name()) or '0.0.0']
    return max(floors, key=_ver_tuple)


def downgrade_why_not(store, version):
    why = []
    low = downgrade_lowest(store)
    if _ver_tuple(version) < _ver_tuple(low):
        why.append('older than %s, the oldest this server may go back to (a version that ran '
                   'here or is installed says so)' % low)
    if _ver_tuple(version) >= _ver_tuple(__version__):
        why.append('not older than %s, which runs here' % __version__)
    return why


def upgrade_checks(store, meta, pages=False):
    why = []
    if not meta:
        return False, ['no package has been sent yet']
    down = bool(meta.get('downgrade'))
    if down:
        why += downgrade_why_not(store, meta['version'])
        if pages and not CONFIG.get('upgrade_downgrade'):
            why.append('going back to an older version is switched off (Settings → Upgrades)')
    elif _ver_tuple(meta['version']) < _ver_tuple(__version__):
        why.append('%s runs %s, newer than %s' % (node_name(), __version__, meta['version']))
    if not UPGRADE_HELPER.exists():
        why.append('plexmon_upgrade.py is not installed - upgrade once on the command line '
                   '(install.sh --upgrade)')
    plan = upgrade_plan(store)
    if plan and plan['state'] == 'running':
        why.append('an upgrade is going on already')
    return not why, why


def upgrade_start(store, by, pages=False):
    meta = upgrade_package_meta(store)
    ok, why = upgrade_checks(store, meta, pages)
    if not ok:
        raise ValueError('; '.join(why))
    ident = '%d-%s' % (int(time.time()), secrets.token_hex(4))
    plan = {'id': ident, 'version': meta['version'], 'sha': meta['sha'],
            'order': [node_name()], 'by': by, 'at': time.time(), 'from': node_name(),
            'downgrade': bool(meta.get('downgrade'))}
    store.set_meta('upgrade:plan', json.dumps(plan))
    store.set_meta('upgrade:node:' + node_name(), json.dumps({'id': ident, 'state': 'waiting',
                                                             'at': time.time()}))
    store.add_event('starting and stopping', '%s started %s %s' % (
        by, 'going back to' if plan['downgrade'] else 'the upgrade to', meta['version']),
        'warn', who=by)
    UPGRADE_WAKE.set()
    return upgrade_plan(store)


def upgrade_cancel(store, by):
    plan = _meta_json(store, 'upgrade:plan')
    if not plan:
        raise ValueError('no upgrade is going on')
    plan['cancelled'] = {'by': by, 'at': time.time()}
    store.set_meta('upgrade:plan', json.dumps(plan))
    store.add_event('starting and stopping', '%s stopped the upgrade to %s'
                    % (by, plan.get('version')), 'warn', who=by)


def _my_step(store, plan, **fields):
    k = 'upgrade:node:' + node_name()
    row = _meta_json(store, k) or {}
    if row.get('id') != plan['id']:
        row = {'id': plan['id']}
    row.update(fields)
    row['at'] = time.time()
    store.set_meta(k, json.dumps(row))
    return row


def _helper_status():
    try:
        fd = os.open(str(UPGRADE_DIR / 'status.json'), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            return json.loads(f.read(1 << 20).decode('utf-8'))
    except (OSError, ValueError):
        return {}


def upgrade_log_tail(lines=200):
    try:
        fd = os.open(str(UPGRADE_DIR / 'last.log'), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            text = f.read(4 << 20).decode('utf-8', 'replace')
        return text.splitlines()[-lines:]
    except OSError:
        return []


UPGRADE_WAKE = threading.Event()
STARTED = [time.time()]


def _upgrade_failed(store, plan, said, tail=None):
    _my_step(store, plan, state='failed', said=str(said)[:500], tail=(tail or [])[-40:])
    store.add_event('starting and stopping', 'the upgrade of %s to %s failed: %s'
                    % (node_name(), plan['version'], said), 'bad')
    log('the upgrade to %s failed: %s' % (plan['version'], said), 'error')


def upgrade_tick(store):
    """This server's part of the plan, every few seconds."""
    plan = upgrade_plan(store)
    if not plan or plan['state'] not in ('running',):
        return
    turn = plan['steps'][0]
    state = turn.get('state')
    now = time.time()
    if state in ('waiting', 'none', None):
        meta = upgrade_package_meta(store) or {}
        try:
            if meta.get('sha') != plan['sha']:
                raise ValueError('the package kept is not the one this upgrade was started with')
            req = {'id': plan['id'], 'file': meta['file'], 'sha': plan['sha'],
                   'version': plan['version'], 'by': plan.get('by'), 'at': now,
                   'downgrade': bool(plan.get('downgrade'))}
            tmp = UPGRADE_DIR / 'request.part'
            try:
                tmp.unlink()
            except OSError:
                pass
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
            with os.fdopen(fd, 'w') as f:
                json.dump(req, f)
            os.replace(str(tmp), str(UPGRADE_DIR / 'request.json'))
        except Exception as e:                          # noqa: BLE001
            _upgrade_failed(store, plan, 'the package could not be handed over: %s' % e)
            return
        _my_step(store, plan, state='installing', asked=now,
                 said='handed to root (plexmon-upgrade.service)')
        log('the upgrade to %s is handed to plexmon-upgrade.service' % plan['version'])
        return
    if state == 'installing':
        if __version__ == plan['version'] and STARTED[0] > float(turn.get('asked') or 0):
            _my_step(store, plan, state='checking', said='restarted as %s' % __version__,
                     asked=turn.get('asked'))
            return
        got = _helper_status()
        if got.get('id') == plan['id'] and got.get('state') == 'failed':
            _upgrade_failed(store, plan, got.get('said') or 'install.sh ended with %s'
                            % got.get('rc'), tail=got.get('tail'))
            return
        if not (UPGRADE_DIR / 'request.json').exists() and \
                not (UPGRADE_DIR / 'request.taken').exists() and \
                got.get('id') != plan['id'] and now - float(turn.get('asked') or now) > 120:
            _upgrade_failed(store, plan, 'plexmon-upgrade.service never took the request - is '
                            'plexmon-upgrade.path installed and running? (install.sh --upgrade '
                            'once on the command line puts it in)')
            return
        if now - float(turn.get('asked') or now) > UPGRADE_TIMEOUT_S:
            _upgrade_failed(store, plan, 'it has not come back as %s in %d min'
                            % (plan['version'], UPGRADE_TIMEOUT_S // 60))
            return
        if got.get('id') == plan['id'] and got.get('state') == 'running':
            parts = [x[4:].strip() for x in upgrade_log_tail(400) if x.startswith('==> ')]
            if parts and len(parts) != int(turn.get('part') or 0):
                _my_step(store, plan, part=len(parts), part_words=parts[-1][:120],
                         asked=turn.get('asked'))
        return
    if state == 'checking':
        trouble = []
        try:
            store.db.value('SELECT 1')
        except Exception as e:                          # noqa: BLE001
            trouble.append('the database: %s' % e)
        got = _helper_status()
        if got.get('id') == plan['id'] and got.get('state') == 'running':
            if now - float(turn.get('at') or now) < 600:
                return
            trouble.append('install.sh has not said it finished in 10 min (see '
                           'journalctl -u plexmon-upgrade)')
        if got.get('id') == plan['id'] and got.get('state') == 'failed':
            trouble.append(got.get('said') or 'install.sh ended with %s' % got.get('rc'))
        if trouble:
            _upgrade_failed(store, plan, '; '.join(trouble), tail=got.get('tail'))
            return
        _my_step(store, plan, state='done', said='%s, checked, running' % __version__,
                 tail=(got.get('tail') or [])[-12:])
        store.add_event('starting and stopping', '%s is upgraded to %s' % (node_name(),
                                                                          __version__))
        log('upgraded to %s' % __version__)


# ---- new versions from the web ---------------------------------------------------------
ONLINE_PKG_RE = re.compile(r'(?:^|/)(plexmon-(\d+)\.(\d+)\.(\d+)\.tar\.gz)(?:\?|#|$)')
ONLINE_EVERY_S = 86400
ONLINE_WORK = {'busy': False, 'at': 0.0}
ONLINE_LOCK = threading.Lock()


def _online_get(url, limit=4 * 1024 * 1024, accept='*/*', timeout=30):
    if not str(url).startswith('https://'):
        raise ValueError('only https:// addresses are asked: %s' % url)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'plexmon/%s (+upgrade check)' % __version__, 'Accept': accept})
    ctx = ssl.create_default_context()
    with url_open(req, timeout=timeout, context=ctx) as r:
        final = r.geturl()
        if not str(final).startswith('https://'):
            raise ValueError('it was sent on to an address that is not https: %s' % final)
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError('the answer from %s is bigger than %d MB' % (url, limit >> 20))
        return data, r.headers.get('Content-Type') or '', final


def _online_ver(m):
    return tuple(int(x) for x in m.group(2, 3, 4))


def online_url():
    return str(CONFIG.get('upgrade_url') or '').strip()


def online_list(url):
    """The packages at `url`: [{'name', 'version', 'url', 'bytes'}], newest first."""
    url = str(url or '').strip()
    if not url:
        raise ValueError('no address to look at (Settings → Upgrades)')
    found = {}

    def add(name, link, size=None):
        m = ONLINE_PKG_RE.search('/' + name)
        if not m or not str(link).startswith('https://'):
            return
        found[m.group(1)] = {'name': m.group(1), 'version': '%d.%d.%d' % _online_ver(m),
                             'v': _online_ver(m), 'url': link, 'bytes': size}

    gh = re.match(r'^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)'
                  r'(?:/tree/([^/]+)(?:/(.*?))?)?/?$', url)
    m1 = ONLINE_PKG_RE.search(urllib.parse.urlparse(url).path)
    if m1:
        link = url
        b = re.match(r'^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$', url)
        if b:
            link = 'https://raw.githubusercontent.com/%s/%s/%s' % b.groups()
        add(m1.group(1), link)
    elif gh:
        owner, repo, ref, folder = gh.group(1), gh.group(2), gh.group(3), gh.group(4) or ''
        api = 'https://api.github.com/repos/%s/%s/contents/%s' % (
            owner, repo, urllib.parse.quote(folder.strip('/')))
        if ref:
            api += '?ref=' + urllib.parse.quote(ref)
        try:
            data, _ct, _f = _online_get(api, accept='application/vnd.github+json')
            items = json.loads(data.decode('utf-8'))
        except Exception as e:                          # noqa: BLE001
            try:
                data, _ct, _f = _online_get(url)
            except Exception:                           # noqa: BLE001
                raise ValueError('GitHub did not answer: %s' % (getattr(e, 'reason', None) or e))
            items = []
            base = 'https://raw.githubusercontent.com/%s/%s/%s/%s' % (
                owner, repo, ref or 'HEAD', (folder.strip('/') + '/') if folder.strip('/') else '')
            for name in set(re.findall(r'plexmon-\d+\.\d+\.\d+\.tar\.gz',
                                       data.decode('utf-8', 'replace'))):
                items.append({'type': 'file', 'name': name, 'download_url': base + name})
        if isinstance(items, dict):
            items = [items]
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get('type') == 'file' and it.get('download_url'):
                add(str(it.get('name') or ''), str(it['download_url']), it.get('size'))
    else:
        data, ct, final = _online_get(url)
        text = data.decode('utf-8', 'replace')
        try:
            items = json.loads(text) if 'json' in ct or text.lstrip()[:1] in '[{' else None
        except ValueError:
            items = None
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    link = it.get('download_url') or it.get('url') or ''
                    add(str(it.get('name') or link), urllib.parse.urljoin(final, str(link)),
                        it.get('size'))
        else:
            for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', text, re.I):
                link = urllib.parse.urljoin(final, html.unescape(href))
                mm = ONLINE_PKG_RE.search(urllib.parse.urlparse(link).path)
                if mm:
                    add(mm.group(1), link)
    out = sorted(found.values(), key=lambda x: x['v'], reverse=True)
    for x in out:
        x.pop('v', None)
    return out


def online_look(store, by=''):
    url = online_url()
    row = {'at': time.time(), 'url': url, 'by': by, 'node': node_name(), 'here': __version__}
    try:
        got = online_list(url)
        row['packages'] = got[:20]
        newest = got[0] if got else None
        row['newest'] = newest
        row['newer'] = bool(newest and _ver_tuple(newest['version']) > _ver_tuple(__version__))
        if row['newer']:
            was = _meta_json(store, 'upgrade:online') or {}
            if ((was.get('newest') or {}).get('version') != newest['version']):
                store.add_event('starting and stopping', 'plexmon %s is out (%s) - Settings → '
                                'Upgrades' % (newest['version'], url), 'info')
    except Exception as e:                              # noqa: BLE001
        row['error'] = str(getattr(e, 'reason', None) or e).split('\n')[0][:300]
    store.set_meta('upgrade:online', json.dumps(row, default=str))
    return row


def online_tick(store):
    if not CONFIG.get('upgrade_check') or not online_url():
        return
    if time.time() - float(ONLINE_WORK['at'] or 0) < 300:
        return
    ONLINE_WORK['at'] = time.time()
    last = _meta_json(store, 'upgrade:online') or {}
    if last.get('url') == online_url() and \
            time.time() - float(last.get('at') or 0) < ONLINE_EVERY_S:
        return
    with ONLINE_LOCK:
        if ONLINE_WORK['busy']:
            return
        ONLINE_WORK['busy'] = True

    def go():
        try:
            online_look(store)
        except Exception as e:                          # noqa: BLE001
            log('looking for a new version: %s' % e, 'warn')
        finally:
            ONLINE_WORK['busy'] = False
    threading.Thread(target=go, name='plexmon-online', daemon=True).start()


def online_view(store):
    row = _meta_json(store, 'upgrade:online') or {}
    if row:
        n = row.get('newest') or {}
        row['newer'] = bool(n and _ver_tuple(n.get('version')) > _ver_tuple(__version__))
        row['here'] = __version__
        row['newer_list'] = sorted(
            (p for p in row.get('packages') or []
             if _ver_tuple(p.get('version')) > _ver_tuple(__version__)),
            key=lambda p: _ver_tuple(p.get('version')), reverse=True)
    return {'online': row, 'url': online_url(), 'check': bool(CONFIG.get('upgrade_check'))}


def online_fetch(store, version, by, older=False):
    pk = [p for p in online_list(online_url()) if p['version'] == str(version)]
    if not pk:
        raise ValueError('plexmon %s is not at %s any more' % (version, online_url()))
    pk = pk[0]
    if older and _ver_tuple(pk['version']) >= _ver_tuple(__version__):
        raise ValueError('plexmon %s is not older than %s, which runs here'
                         % (pk['version'], __version__))
    if not older and _ver_tuple(pk['version']) <= _ver_tuple(__version__):
        raise ValueError('plexmon %s is not newer than %s, which runs here'
                         % (pk['version'], __version__))
    UPGRADE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='online-', suffix='.tar.gz', dir=str(UPGRADE_DIR))
    os.close(fd)
    tmp = Path(tmp)
    try:
        data, _ct, final = _online_get(pk['url'], limit=UPGRADE_MOST, timeout=300)
        tmp.write_bytes(data)
        try:
            info = upgrade_inspect(tmp)
        except ValueError as e:
            store.add_event('starting and stopping', 'plexmon %s from %s was refused: %s'
                            % (version, final, e), 'warn', who=by)
            raise ValueError('the package from %s was refused: %s' % (final, e))
        if older:
            why = downgrade_why_not(store, info['version'])
            if why:
                raise ValueError('; '.join(why))
            meta = upgrade_store_package(store, tmp, info, by, downgrade=True)
        else:
            if info.get('older'):
                raise ValueError('%s is older than %s, which runs here'
                                 % (info['version'], info.get('installed')))
            meta = upgrade_store_package(store, tmp, info, by)
        store.add_event('starting and stopping', '%s fetched plexmon %s from %s (signed by %s), '
                        'ready to be put in' % (by, meta['version'], final,
                                                meta.get('signed_by')), 'warn', who=by)
        return meta
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def changelog_text():
    try:
        return (LIB_DIR / 'CHANGELOG.md').read_text(encoding='utf-8')
    except OSError:
        return ''


def changelog_entry(text, version):
    m = re.search(r'^## %s\b.*?(?=^## |\Z)' % re.escape(version), text, re.M | re.S)
    return m.group(0).strip() if m else ''


def downgrade_list(store):
    got = online_list(online_url())
    out = []
    for p in got:
        if _ver_tuple(p['version']) >= _ver_tuple(__version__):
            continue
        why = downgrade_why_not(store, p['version'])
        out.append({'version': p['version'], 'url': p['url'], 'why': why,
                    'short': 'too old' if why else ''})
    return {'url': online_url(), 'here': __version__, 'floor': DOWNGRADE_FLOOR,
            'lowest': downgrade_lowest(store), 'versions': out}


def downgrade_changelog(version):
    text = changelog_text()
    lost = []
    for m in re.finditer(r'^## (\d+\.\d+\.\d+)\b.*?(?=^## |\Z)', text, re.M | re.S):
        if _ver_tuple(version) < _ver_tuple(m.group(1)) <= _ver_tuple(__version__):
            lost.append({'version': m.group(1), 'text': m.group(0).strip()})
    return {'entry': changelog_entry(text, version), 'from': 'installed', 'lost': lost,
            'here': __version__}


def upgrade_view(store):
    meta = upgrade_package_meta(store)
    ok, why = upgrade_checks(store, meta, True) if meta else (False, [])
    plan = upgrade_plan(store)
    hidden = _meta_json(store, 'upgrade:hidden') or {}
    if plan and hidden.get('id') == plan.get('id') and plan['state'] != 'running':
        plan = None
    me = {'name': node_name(), 'me': True, 'alive': True, 'version': __version__,
          'at': time.time(), 'maintenance': {'on': False}}
    out = {'version': __version__, 'node': node_name(), 'nodes': [me], 'package': meta,
           'ready': ok, 'not_ready': why, 'plan': plan, 'log': upgrade_log_tail(),
           'helper': UPGRADE_HELPER.exists(),
           'path_unit': UPGRADE_PATH_UNIT.exists() or bool(os.environ.get('PLEXMON_TEST')),
           'web': online_view(store), 'downgrade_on': bool(CONFIG.get('upgrade_downgrade'))}
    return out


# ---------------------------------------------------------------------------
# The second step of signing in to Settings (otp 1.0.125): a code from an app
# ---------------------------------------------------------------------------
MFA_ALLOWED = ('/api/auth/mfa', '/api/auth/logout', '/api/auth/whoami')
MFA_LOCK = threading.Lock()
MFA_KIND_WORDS = {'app': 'an authenticator app'}


def mfa_key(name):
    return 'adm2fa:' + str(name or '').strip().lower()


def mfa_get(store, name):
    try:
        got = json.loads(store.get_meta(mfa_key(name)) or 'null')
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def mfa_all(store):
    out = {}
    for r in store.db.rows("SELECT k, v FROM meta WHERE k LIKE 'adm2fa:%'"):
        try:
            v = json.loads(r['v'] or '{}')
        except ValueError:
            continue
        out[r['k'][len('adm2fa:'):]] = {'kind': v.get('kind'), 'at': v.get('at'), 'dest': ''}
    return out


def mfa_reset(store, name, by):
    had = mfa_get(store, name)
    store.db.run('DELETE FROM meta WHERE k = ?', (mfa_key(name),))
    store.add_event('accounts', '%s reset the second step of signing in of %s%s' % (
        by, name, ' (it was %s)' % MFA_KIND_WORDS.get(had.get('kind'), had.get('kind'))
        if had else ' (none was set up)'), 'warn', who=by)
    return bool(had)


def mfa_methods():
    return ['app']


def mfa_stage(store, name):
    rec = mfa_get(store, name)
    return 'check' if rec and rec.get('kind') == 'app' else 'enrol'


def mfa_pending(s):
    if not CONFIG.get('admin_2fa'):
        return ''
    return str(((s or {}).get('data') or {}).get('mfa') or '')


# ---------------------------------------------------------------------------
# The console at login (otp 1.0.141)
# ---------------------------------------------------------------------------
CONSOLE_FLAG = Path(os.environ.get('PLEXMON_CONSOLE_FLAG', '/etc/plexmon/console-login'))
CONSOLE_PROFILE = Path(os.environ.get('PLEXMON_CONSOLE_PROFILE',
                                      '/etc/profile.d/zz-plexmon-console.sh'))
CONSOLE_PROFILE_TEXT = '''# /etc/profile.d/zz-plexmon-console.sh - written by plexmon (plexmon --console-at-login).
# The plexmon console for root signing in at the console or over SSH, in an
# interactive login shell only. q leaves it for this shell; plexmon --console
# opens it again. Never for a command run over SSH, scp, sftp or a script.
if [ "$(id -u)" = "0" ] && [ -t 0 ] && [ -t 1 ] && [ -z "$PLEXMON_NO_CONSOLE" ] \\
   && [ -z "$PLEXMON_IN_CONSOLE" ] && [ -r /etc/plexmon/console-login ] \\
   && [ "$(cat /etc/plexmon/console-login 2>/dev/null)" = "on" ] \\
   && [ -x /usr/local/bin/plexmon ]; then
    case "$-" in *i*) /usr/local/bin/plexmon --console ;; esac
fi
'''


def console_login_state():
    try:
        v = CONSOLE_FLAG.read_text().strip()
    except OSError:
        v = 'off'
    return v == 'on' and CONSOLE_PROFILE.exists()


def console_login_set(on, by=''):
    write_replacing(CONSOLE_FLAG, 'on\n' if on else 'off\n', 0o644)
    if on and not CONSOLE_PROFILE.exists():
        write_replacing(CONSOLE_PROFILE, CONSOLE_PROFILE_TEXT, 0o644)
    return console_login_state()


# ---------------------------------------------------------------------------
# What this server runs on (Settings → About)
# ---------------------------------------------------------------------------
_SOFTWARE = {'at': 0.0, 'state': None, 'busy': False}


def _first_line(cmd):
    try:
        got = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        text = (got.stdout or '') + (got.stderr or '')
        return text.strip().splitlines()[0].strip() if text.strip() else ''
    except (OSError, subprocess.SubprocessError, IndexError):
        return ''


def plex_version():
    try:
        got = subprocess.run(['rpm', '-q', '--qf', '%{VERSION}', 'plexmediaserver'],
                             capture_output=True, text=True, timeout=10)
        if got.returncode == 0 and got.stdout.strip():
            return got.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        got = subprocess.run(['dpkg-query', '-W', '-f', '${Version}', 'plexmediaserver'],
                             capture_output=True, text=True, timeout=10)
        if got.returncode == 0:
            return got.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ''


def software_info():
    if _SOFTWARE['state'] is not None and time.time() - _SOFTWARE['at'] < 600:
        return dict(_SOFTWARE['state'])
    import platform
    out = {'plexmon': __version__,
           'python': '%s %s' % (platform.python_implementation(), platform.python_version()),
           'python_path': os.path.realpath(sys.executable),
           'openssl': ssl.OPENSSL_VERSION, 'sqlite': sqlite3.sqlite_version,
           'kernel': '%s %s' % (platform.system(), platform.release()),
           'arch': platform.machine(), 'modules': {}, 'programs': {}}
    try:
        with open('/etc/os-release', encoding='utf-8') as f:
            rel = dict(ln.strip().split('=', 1) for ln in f if '=' in ln)
        out['os'] = rel.get('PRETTY_NAME', '').strip('"')
    except OSError:
        out['os'] = ''
    for mod in ('colorama', 'pyatv', 'aiowebostv', 'samsungtvws'):
        try:
            import importlib.metadata as md
            out['modules'][mod] = md.version(mod)
        except Exception:                               # noqa: BLE001
            out['modules'][mod] = ''
    progs = (('nginx', ['nginx', '-v']), ('php', ['php', '-v']), ('php-fpm', None),
             ('openssl', ['openssl', 'version']), ('systemd', ['systemctl', '--version']),
             ('smartctl', ['smartctl', '--version']), ('firewalld', ['firewall-cmd', '--version']))
    for name, cmd in progs:
        if name == 'php-fpm':
            import glob
            fpm = sorted(glob.glob('/usr/sbin/php-fpm*') + glob.glob('/usr/sbin/php*-fpm*'))
            line = _first_line([fpm[-1], '-v']) if fpm else ''
        else:
            exe = shutil.which(cmd[0]) or next((p for p in ('/usr/sbin/' + cmd[0],
                                                            '/sbin/' + cmd[0])
                                                if os.path.exists(p)), None)
            line = _first_line([exe] + cmd[1:]) if exe else ''
        m = re.search(r'(\d+\.\d+(?:\.\d+)?[a-z0-9.~+-]*)', line or '')
        out['programs'][name] = m.group(1) if m else (line[:60] if line else '')
    out['php_cli'] = out['programs'].pop('php', '')
    out['plex'] = plex_version()
    _SOFTWARE.update(at=time.time(), state=out)
    return dict(out)


# ---------------------------------------------------------------------------
# The API the Settings pages ask
# ---------------------------------------------------------------------------
# On 127.0.0.1 only, and only for whoever holds the token in /run/plexmon -
# the web server's group and nobody else. The PHP pages relay to it and add
# nothing: the session travels as a header, and this decides what that
# session may have.
ROLE_RANK = {'readonly': 1, 'fulladmin': 3}
LOCKOUT_KINDS = {'admin': 'signing in to Settings', 'password': 'changing a password'}


class ApiError(Exception):
    def __init__(self, status, words, detail=''):
        Exception.__init__(self, words)
        self.status = status
        self.detail = detail


def api_token(make=False):
    if make:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        write_replacing(TOKEN_FILE, token + '\n', 0o640)
        gid = web_gid()
        if gid is not None:
            try:
                os.chown(str(TOKEN_FILE), 0, gid)
                os.chown(str(RUN_DIR), 0, gid)
                os.chmod(str(RUN_DIR), 0o750)
            except OSError:
                pass
        return token
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ''


def web_gid():
    """The group the web pages (php-fpm) run as: the one named, else the
    owner of the pages, else apache's or nginx's."""
    import grp
    for name in [WEB_GROUP] if WEB_GROUP else []:
        try:
            return grp.getgrnam(name).gr_gid
        except KeyError:
            pass
    try:
        st = os.stat(str(WEB_ROOT / 'settings'))
        if st.st_gid:
            return st.st_gid
    except OSError:
        pass
    for name in ('apache', 'nginx', 'www-data'):
        try:
            return grp.getgrnam(name).gr_gid
        except KeyError:
            continue
    return None


BODY_WORDS = ('name', 'to', 'dest', 'note', 'code', 'password', 'passphrase', 'lang',
              'kind', 'action', 'display', 'user', 'old', 'new', 'q', 'state', 'mode',
              'label', 'target', 'version')


def _no_constant(word):
    raise ValueError('%s is not a number' % word)


ROUTES = {}
MUST_CHANGE_ALLOWED = ('/api/auth/password', '/api/auth/logout', '/api/auth/whoami')


def route(method, path):
    def wrap(fn):
        ROUTES[(method, path)] = fn
        return fn
    return wrap


class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'plexmon'
    protocol_version = 'HTTP/1.1'
    timeout = 60
    THROTTLE_LOCK = threading.Lock()

    def log_message(self, fmt, *args):
        if debugging('api'):
            log('api %s' % (fmt % args), 'debug')

    @property
    def d(self):
        return self.server.admin

    @property
    def store(self):
        return self.server.admin.store

    def client_ip(self):
        said = str(self.headers.get('X-Otp-Client') or '').strip()
        return said if re.match(r'^[0-9a-fA-F:.]{2,45}\Z', said) else self.client_address[0]

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        if hasattr(self, '_body'):
            return self._body
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise ApiError(400, 'the length of the body is not a number')
        if n < 0:
            raise ApiError(400, 'the length of the body is not a number')
        if n > 8 * 1024 * 1024:
            raise ApiError(413, 'that is more than this takes')
        raw = self.rfile.read(n) if n else b''
        try:
            self._body = json.loads(raw.decode('utf-8'), parse_constant=_no_constant) \
                if raw else {}
        except (ValueError, RecursionError):
            raise ApiError(400, 'the body is not JSON')
        if not isinstance(self._body, dict):
            raise ApiError(400, 'the body is not an object')
        for k in BODY_WORDS:
            v = self._body.get(k)
            if isinstance(v, (list, dict, bool)):
                raise ApiError(400, '%s is a piece of text here' % k)
            if isinstance(v, float) and k != 'code':
                raise ApiError(400, '%s is a piece of text here' % k)
        return self._body

    def body_to_file(self, path, most):
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0:
            raise ApiError(400, 'no file arrived')
        if n > most:
            raise ApiError(413, 'that file is bigger than this takes')
        left = n
        with open(str(path), 'wb') as out:
            while left > 0:
                lump = self.rfile.read(min(left, 1 << 20))
                if not lump:
                    raise ApiError(400, 'the file did not arrive whole')
                out.write(lump)
                left -= len(lump)
        self._body = {}
        return n

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')

    def route(self, method):
        self.__dict__.pop('_body', None)
        url = urlparse(self.path)
        self.args = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip('/')
        self.route_path = path
        if path != '/api/health':
            want = api_token()
            given = str(self.headers.get('X-Otp-Token') or '')
            if not want or not hmac.compare_digest(want.encode('utf-8'),
                                                   given.encode('utf-8', 'replace')):
                return self.send_json({'ok': False, 'error': 'not this API’s token'}, 403)
        fn = ROUTES.get((method, path))
        if not fn:
            return self.send_json({'ok': False, 'error': 'no such endpoint: %s %s'
                                   % (method, path)}, 404)
        self._held = None
        self._failed = False
        try:
            got = fn(self)
            if got is not None:
                self.send_json(got if 'ok' in got else dict(ok=True, **got))
        except ApiError as e:
            self.send_json({'ok': False, 'error': str(e), 'detail': e.detail or None}, e.status)
        except (ValueError, BadSetting) as e:
            said = str(e)
            if said.startswith(('invalid literal for int()', 'could not convert string to float')):
                said = 'a field that is a number holds something else'
            self.send_json({'ok': False, 'error': said}, 400)
        except DbError as e:
            self.send_json({'ok': False, 'error': str(e)}, 503)
        except (TypeError, AttributeError, KeyError) as e:
            log('%s %s could not be read: %s\n%s' % (method, path, e, traceback.format_exc()),
                'warn')
            self.send_json({'ok': False, 'error': 'the request is not in the shape this '
                                                  'takes (a field of the wrong kind)'}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                          # noqa: BLE001
            log('the API broke on %s %s: %s\n%s' % (method, path, e, traceback.format_exc()),
                'error')
            self.send_json({'ok': False, 'error': 'it broke - the reason is in the log of the '
                                                  'service (/var/log/plexmon/plexmon.log)'}, 500)
        finally:
            self._release()

    def arg(self, name, default=''):
        return self.args.get(name, default)

    def num(self, name, default, lo=0, hi=10 ** 9):
        try:
            return max(lo, min(hi, int(float(self.args.get(name, default)))))
        except (TypeError, ValueError, OverflowError):
            return default

    # ---- who is asking ----------------------------------------------------------
    def session(self, kind='admin'):
        token = str(self.headers.get('X-Otp-Session') or '')
        idle = int(CONFIG.get('session_idle_min') or 120) * 60
        return self.store.session_get(token, kind, idle)

    def me(self, role='readonly'):
        s = self.session('admin')
        if not s:
            raise ApiError(401, 'not signed in, or the sign-in has run out')
        acc = self.store.account(s['name'])
        if not acc or acc.get('disabled'):
            self.store.session_end(self.headers.get('X-Otp-Session'))
            raise ApiError(401, 'that account may no longer sign in')
        s['role'] = acc['role']
        if mfa_pending(s) and getattr(self, 'route_path', '') not in MFA_ALLOWED:
            raise ApiError(401, 'the second step of signing in is not done yet')
        if acc.get('must_change') and getattr(self, 'route_path', '') not in MUST_CHANGE_ALLOWED:
            raise ApiError(403, 'change the password first')
        if ROLE_RANK.get(s['role'], 0) < ROLE_RANK[role]:
            raise ApiError(403, 'only a full administrator may do that' if role == 'fulladmin'
                           else 'that account may not do that')
        s['account'] = acc
        return s

    def throttle(self, kind, tries, wait_min, name='', what='wrong passwords'):
        ip = self.client_ip()
        try:
            a = ipaddress.ip_address(str(ip).split('%')[0])
            if a.version == 6 and not a.ipv4_mapped:
                ip = str(ipaddress.ip_network('%s/64' % a, strict=False))
        except ValueError:
            pass
        keys = [(kind, ip)]
        if name:
            keys.append((kind + '-name', str(name).strip().lower()[:190]))
        t = time.time()
        span = wait_min * 60
        fails_all = self.d.login_fails
        with self.THROTTLE_LOCK:
            for i, key in enumerate(keys):
                limit = tries if i == 0 else tries * 4
                fails = [x for x in fails_all.get(key, []) if t - x < span]
                fails_all[key] = fails
                info = self.d.login_info.setdefault(key, {'others': []})
                info.update(limit=limit, span=span, what=what, at=t)
                other = (str(name).strip()[:120] if i == 0 else str(ip)) if name or i else ''
                if other:
                    if other in info['others']:
                        info['others'].remove(other)
                    info['others'] = (info['others'] + [other])[-6:]
                if len(fails) >= limit:
                    raise ApiError(429, 'too many %s %s: try again in %s'
                                   % (what, 'from %s' % ip if i == 0 else 'for this name',
                                      human_span(span - (t - fails[0]))))
            for key in keys:
                fails_all.setdefault(key, []).append(t)
        self._held = (keys, t)
        return keys

    def failed(self, key=None):
        self._failed = True

    def _release(self):
        held = getattr(self, '_held', None)
        if not held or getattr(self, '_failed', False):
            return
        keys, t = held
        with self.THROTTLE_LOCK:
            for key in keys:
                got = self.d.login_fails.get(key)
                if got and t in got:
                    got.remove(t)
        self._held = None


# ---- health and signing in -------------------------------------------------------
@route('GET', '/api/health')
def _health(h):
    try:
        users = 1 if h.store.db.value('SELECT 1 FROM accounts LIMIT 1') else 0
        db_ok = True
    except Exception:                                   # noqa: BLE001
        users, db_ok = 0, False
    return {'ok': True, 'users': users, 'database': {'ok': db_ok},
            'lang': CONFIG.get('ui_lang') or 'cs', 'node': node_name()}


@route('GET', '/api/lang')
def _lang(h):
    """The language the dashboard opens in - asked without a sign-in."""
    return {'lang': CONFIG.get('ui_lang') or 'cs'}


@route('POST', '/api/auth/login')
def _login(h):
    b = h.body()
    name = str(b.get('name') or '').strip()[:190]
    password = str(b.get('password') or '')
    key = h.throttle('admin', int(CONFIG.get('login_tries') or 5),
                     int(CONFIG.get('login_wait_min') or 15), name=name)
    acc = h.store.account(name)
    ok = None
    if acc:
        ok = acc if (acc.get('pw') and password_ok(password, acc['pw'])) else None
    else:
        password_ok(password, DUMMY_PW_HASH)
    if not ok:
        h.failed(key)
        h.store.add_event('sign-ins', 'a wrong password for %s from %s'
                          % (name or '(nobody)', h.client_ip()), 'warn')
        raise ApiError(401, 'the name or the password is not right')
    if ok.get('disabled'):
        h.failed(key)
        h.store.add_event('sign-ins', '%s, switched off, tried to sign in from %s'
                          % (ok['name'], h.client_ip()), 'warn')
        raise ApiError(401, 'the name or the password is not right')
    stage = mfa_stage(h.store, ok['name']) if CONFIG.get('admin_2fa') else ''
    token = h.store.session_new('admin', ok['name'], ok['role'], h.client_ip(),
                                CONFIG.get('session_hours') or 12,
                                data={'mfa': stage} if stage else None)
    h.store.set_account(ok['name'], last_in=time.time(), last_from=h.client_ip())
    h.store.add_event('sign-ins', '%s signed in from %s%s' % (
        ok['name'], h.client_ip(), {'check': ' - the second step to give',
                                     'enrol': ' - the second step to set up'}.get(stage, '')),
                      who=ok['name'])
    return {'token': token, 'who': {'name': ok['name'], 'role': ok['role'] if not stage else ''},
            'must_change': bool(ok.get('must_change')), 'mfa': stage}


def _mfa_session(h):
    s = h.session('admin')
    if not s:
        raise ApiError(401, 'not signed in, or the sign-in has run out')
    acc = h.store.account(s['name'])
    if not acc or acc.get('disabled'):
        raise ApiError(401, 'that account may no longer sign in')
    return s, acc


def _mfa_done(h, s, how):
    data = {k: v for k, v in (s.get('data') or {}).items()
            if k not in ('mfa', 'mfa_new', 'mfa_code', 'mfa_sent_at', 'mfa_sent')}
    hours = max(0.05, (float(s.get('expires') or 0) - time.time()) / 3600.0)
    token = h.store.session_new('admin', s['name'], s['role'], h.client_ip(), hours, data=data)
    h.store.session_drop(s['id'])
    h.store.add_event('sign-ins', '%s gave the second step (%s) from %s' % (
        s['name'], how, h.client_ip()), who=s['name'])
    return token


@route('GET', '/api/auth/mfa')
def _mfa(h):
    s, acc = _mfa_session(h)
    stage = mfa_pending(s)
    rec = mfa_get(h.store, s['name']) or {}
    new = (s.get('data') or {}).get('mfa_new') or {}
    return {'name': acc['name'], 'display': acc.get('display') or '', 'stage': stage,
            'methods': mfa_methods(), 'kind': rec.get('kind') if stage == 'check' else '',
            'dest': '', 'started': {'kind': new.get('kind'), 'dest': ''} if new else None,
            'digits': 6}


@route('POST', '/api/auth/mfa')
def _mfa_do(h):
    s, acc = _mfa_session(h)
    stage = mfa_pending(s)
    if not stage:
        raise ApiError(409, 'this sign-in has no second step to give')
    b = h.body()
    act = b.get('action')
    name = acc['name']
    data = s.get('data') or {}
    if act in ('enrol_start', 'enrol_confirm') and stage == 'enrol' and \
            mfa_stage(h.store, name) != 'enrol':
        raise ApiError(409, 'this account has its second step set up already - sign in again')
    if act == 'enrol_start':
        if stage != 'enrol':
            raise ApiError(409, 'this account has its second step set up already')
        if str(b.get('kind') or '') != 'app':
            raise ApiError(400, 'that way is not offered here')
        secret = b32_new(20)
        h.store.session_merge(s['id'], {'mfa_new': {
            'kind': 'app', 'secret': seal_secret(secret, 'adm2fa-new:' + name.lower())}})
        site = str(CONFIG.get('site_name') or socket.gethostname())
        uri = otpauth_uri(secret, name, '%s (plexmon)' % site)
        return {'kind': 'app', 'qr': qr_data_uri(uri), 'uri': uri,
                'secret': ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))}
    if act == 'enrol_confirm':
        if stage != 'enrol':
            raise ApiError(409, 'this account has its second step set up already')
        new = data.get('mfa_new') or {}
        if not new.get('kind'):
            raise ApiError(400, 'choose a way first')
        key = h.throttle('admin', int(CONFIG.get('login_tries') or 5),
                         int(CONFIG.get('login_wait_min') or 15), name=name, what='wrong codes')
        code = re.sub(r'\s', '', str(b.get('code') or ''))
        secret = unseal_secret(new.get('secret'), 'adm2fa-new:' + name.lower())
        step = totp_match(secret, code, digits=6, window=1) if secret else None
        if step is None:
            h.failed(key)
            raise ApiError(400, 'that code is not right - the one the app shows now')
        rec = {'kind': 'app', 'at': time.time(), 'by': name,
               'secret': seal_secret(secret, mfa_key(name)), 'step': step}
        with MFA_LOCK:
            if mfa_stage(h.store, name) != 'enrol':
                raise ApiError(409, 'this account has its second step set up already - sign '
                                    'in again')
            h.store.set_meta(mfa_key(name), json.dumps(rec))
        token = _mfa_done(h, s, 'set up now: an authenticator app')
        h.store.add_event('accounts', '%s set up the second step of signing in: an '
                          'authenticator app' % name, who=name)
        return {'message': 'the second step is set up', 'done': True, 'token': token}
    if act == 'check':
        if stage != 'check':
            raise ApiError(409, 'set the second step up first')
        key = h.throttle('admin', int(CONFIG.get('login_tries') or 5),
                         int(CONFIG.get('login_wait_min') or 15), name=name, what='wrong codes')
        code = re.sub(r'\s', '', str(b.get('code') or ''))
        with MFA_LOCK:
            rec = mfa_get(h.store, name) or {}
            secret = unseal_secret(rec.get('secret'), mfa_key(name))
            step = totp_match(secret, code, digits=6, window=1) if secret else None
            if step is None or step <= int(rec.get('step') or 0):
                h.failed(key)
                raise ApiError(400, 'that code is not right' if step is None else
                               'that code was used already - wait for the next one')
            rec['step'] = step
            h.store.set_meta(mfa_key(name), json.dumps(rec))
        token = _mfa_done(h, s, 'an authenticator app')
        return {'message': 'signed in', 'done': True, 'token': token}
    if act == 'send':
        raise ApiError(400, 'the code comes from the app - nothing is sent')
    raise ApiError(400, 'no such thing to do')


# ---- editing the columns, switched on for one sign-in --------------------------
def _session_row(token):
    store = STORE.get('store')
    if not store or not token:
        return None, None
    row = store.db.row('SELECT id, data FROM sessions WHERE token_hash = ?',
                       (hashlib.sha256(str(token).encode()).hexdigest(),))
    if not row:
        return store, None
    try:
        row['data'] = json.loads(row.get('data') or '{}')
    except ValueError:
        row['data'] = {}
    return store, row


def cols_editing(token):
    _, row = _session_row(token)
    return bool(row and row['data'].get('cols_edit'))


def set_cols_editing(token, on):
    store, row = _session_row(token)
    if not row:
        return False
    data = row['data']
    if on:
        data['cols_edit'] = 1
    else:
        data.pop('cols_edit', None)
    store.session_set_data(row['id'], data)
    return bool(on)


SETTINGS_FILE_KIND = 'plexmon-user-settings'


@route('POST', '/api/auth/logout')
def _logout(h):
    token = h.headers.get('X-Otp-Session')
    set_cols_editing(token, False)
    h.store.session_end(token)
    return {}


@route('POST', '/api/auth/columns')
def _columns(h):
    s = h.me()
    b = h.body()
    token = h.headers.get('X-Otp-Session') or ''
    if 'edit' in b:
        set_cols_editing(token, bool(b.get('edit')))
        if 'cols' not in b:
            return {'edit': cols_editing(token)}
    reset = bool(b.get('reset'))
    if not reset and not cols_editing(token):
        raise ApiError(409, 'switch on "Edit the columns" under your name first: the columns '
                       'are not being edited from this sign-in')
    try:
        kept = h.store.put_user_cols(s['name'], {} if reset else (b.get('cols') or {}))
    except ValueError as e:
        raise ApiError(400, str(e))
    if reset:
        h.store.add_event('accounts', '%s put the columns of every table back as they come'
                          % s['name'], who=s['name'])
    return {'tables': len(kept), 'edit': cols_editing(token)}


@route('GET', '/api/auth/settings')
def _my_settings(h):
    s = h.me()
    return {'name': s['name'], 'version': __version__, 'kind': SETTINGS_FILE_KIND,
            'settings': {'columns': h.store.user_cols(s['name']),
                         'prefs': h.store.user_prefs(s['name'])}}


@route('POST', '/api/auth/settings')
def _my_settings_put(h):
    s = h.me()
    b = h.body()
    got = b.get('settings')
    if not isinstance(got, dict):
        raise ApiError(400, 'that is not a file of settings: there is no "settings" in it')
    known = ('columns', 'prefs')
    ignored = sorted(k for k in got if k not in known)
    for section, check, words in (('columns', h.store.clean_cols, 'the columns'),
                                  ('prefs', h.store.clean_prefs, 'the choices')):
        if section in got:
            try:
                check(got[section] if got[section] is not None else {})
            except (ValueError, TypeError, AttributeError) as e:
                raise ApiError(400, '%s in that file: %s' % (words, e))
    took = {}
    if 'columns' in got:
        took['columns'] = len(h.store.put_user_cols(s['name'], got['columns'] or {}))
    if 'prefs' in got:
        took['prefs'] = len(h.store.put_user_prefs(s['name'], got['prefs'] or {}))
    if not took:
        raise ApiError(400, 'nothing in that file is a setting this version keeps'
                       + (' (it holds: %s)' % ', '.join(ignored) if ignored else ''))
    h.store.add_event('accounts', '%s read their own settings back from a file (%s)'
                      % (s['name'], ', '.join('%s: %d' % kv for kv in took.items())),
                      who=s['name'])
    return {'took': took, 'ignored': ignored}


@route('GET', '/api/auth/whoami')
def _whoami(h):
    s = h.session('admin')
    if not s:
        return {'who': None}
    acc = h.store.account(s['name'])
    if not acc or acc.get('disabled'):
        return {'who': None}
    if mfa_pending(s):
        return {'who': {'name': acc['name'], 'role': '', 'display': acc.get('display') or '',
                        'mfa': mfa_pending(s)}}
    try:
        prefs = json.loads(acc.get('prefs') or '{}')
    except ValueError:
        prefs = {}
    return {'idle_min': int(CONFIG.get('session_idle_min') or 120),
            'who': {'name': acc['name'], 'role': acc['role'], 'source': acc.get('source'),
                    'display': acc.get('display') or '', 'must_change': bool(
                        acc.get('must_change')), 'prefs': prefs,
                    'cols': h.store.user_cols(acc['name']),
                    'cols_edit': cols_editing(h.headers.get('X-Otp-Session') or '')}}


@route('POST', '/api/auth/password')
def _password(h):
    s = h.me()
    b = h.body()
    acc = s['account']
    key = h.throttle('password', int(CONFIG.get('login_tries') or 5), 5, name=acc['name'])
    if not password_ok(str(b.get('old') or ''), acc.get('pw')):
        h.failed(key)
        raise ApiError(400, 'the password you have now is not right')
    h.store.set_account(acc['name'], password=str(b.get('new') or ''), must_change=0)
    h.store.db.run('DELETE FROM sessions WHERE kind = ? AND LOWER(name) = ? AND token_hash <> ?',
                   ('admin', acc['name'].lower(), hashlib.sha256(
                       str(h.headers.get('X-Otp-Session') or '').encode()).hexdigest()))
    h.store.add_event('accounts', '%s changed their own password' % acc['name'],
                      who=acc['name'])
    first = STATE_DIR / 'first-login.txt'
    try:
        if first.exists() and ('name: %s\n' % acc['name']) in first.read_text():
            first.unlink()
    except OSError:
        pass
    return {'message': 'the password is changed'}


@route('POST', '/api/auth/prefs')
def _prefs(h):
    s = h.me()
    acc = s['account']
    try:
        prefs = json.loads(acc.get('prefs') or '{}')
    except ValueError:
        prefs = {}
    b = h.body()
    if 'lang' in b:
        if str(b['lang']) not in ('cs', 'en'):
            raise ApiError(400, 'no such language here')
        prefs['lang'] = str(b['lang'])
    h.store.set_account(acc['name'], prefs=json.dumps(prefs))
    return {'prefs': prefs}


# ---- what it is doing --------------------------------------------------------------
@route('GET', '/api/status')
def _status(h):
    s = h.me()
    out = h.d.status()
    out['role'] = s['role']
    return out


@route('GET', '/api/software')
def _software(h):
    h.me()
    return {'nodes': [{'name': node_name(), 'me': True, 'alive': True,
                       'software': software_info()}]}


@route('GET', '/api/console-login')
def _console_login(h):
    h.me('fulladmin')
    return {'nodes': [{'name': node_name(), 'me': True, 'on': console_login_state(),
                       'pending': False}]}


@route('POST', '/api/console-login')
def _console_login_do(h):
    s = h.me('fulladmin')
    b = h.body()
    on = b.get('on')
    if not isinstance(on, bool):
        raise ApiError(400, 'on is true or false')
    try:
        now = console_login_set(on, s['name'])
    except OSError as e:
        raise ApiError(500, 'the console at login could not be switched: %s' % e)
    h.store.add_event('settings', '%s switched the console at login %s' % (
        s['name'], 'on' if now else 'off'), who=s['name'])
    return {'message': 'the console at login is on' if now else 'the console at login is off'}


# ---- settings, accounts, events --------------------------------------------------
@route('GET', '/api/settings')
def _settings(h):
    s = h.me()
    full = s['role'] == 'fulladmin'
    return {'settings': h.d.settings_state(full), 'may_change': full, 'panes': PANES,
            'only_when': ONLY_WHEN, 'config_file': str(CONFIG_FILE),
            'database': h.store.db.where(), 'node': node_name(), 'nodes': [node_name()],
            'cluster': False}


@route('POST', '/api/settings')
def _settings_do(h):
    s = h.me('fulladmin')
    b = h.body()
    try:
        return h.d.save_settings(b.get('settings') or {}, by=s['name'],
                                 keep_session=s.get('id'))
    except BadSetting as e:
        raise ApiError(400, str(e))


@route('GET', '/api/users')
def _users(h):
    s = h.me()
    mfa = mfa_all(h.store)
    users = h.store.accounts()
    for u in users:
        u['mfa'] = mfa.get(str(u.get('name') or '').lower())
    return {'users': users, 'may_change': s['role'] == 'fulladmin',
            'mfa_on': bool(CONFIG.get('admin_2fa')),
            'roles': [{'role': r, 'words': ROLE_WORDS[r]} for r in ROLES_ALL]}


@route('POST', '/api/users')
def _users_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act, name = b.get('action'), str(b.get('name') or '').strip()
    who = s['name']
    if act == 'add':
        h.store.add_account(name, b.get('role') or 'readonly', b.get('password'),
                            note=b.get('note') or '', must_change=b.get('must_change', 1))
        h.store.add_event('accounts', '%s made the account %s (%s)' % (who, name,
                                                                         b.get('role')), who=who)
        return {'message': 'the account %s is made' % name}
    if not name:
        raise ApiError(400, 'which account?')
    acc = h.store.account(name)
    if not acc:
        raise ApiError(404, 'there is no account called %s' % name)
    if act in ('delete', 'disable') or (act == 'role' and b.get('role') != 'fulladmin'):
        if acc['role'] == 'fulladmin' and not h.store.fulladmins_left(but=name):
            raise ApiError(400, 'that is the last full administrator who can sign in')
    if act == 'delete':
        h.store.drop_account(name)
        h.store.add_event('accounts', '%s removed the account %s' % (who, name), 'warn', who=who)
        return {'message': 'the account %s is gone' % name}
    if act == 'role':
        h.store.set_account(name, role=b.get('role'))
        h.store.add_event('accounts', '%s made %s %s' % (who, name,
                                                         ROLE_WORDS.get(b.get('role'))), who=who)
        return {'message': 'saved'}
    if act == 'mfa_reset':
        had = mfa_reset(h.store, acc['name'], who)
        h.store.db.run('DELETE FROM sessions WHERE kind = ? AND LOWER(name) = ?',
                       ('admin', acc['name'].lower()))
        return {'message': ('the second step of %s is reset: they set it up again at the next '
                            'sign-in; their sign-ins are ended' if had else
                            '%s had no second step set up') % acc['name']}
    if act == 'reset_cols':
        h.store.put_user_cols(acc['name'], {})
        h.store.add_event('accounts', '%s put the columns of every table back as they come '
                          'for %s' % (who, acc['name']), who=who)
        return {'message': 'the columns of %s are back as they come; they see it at their '
                           'next page load' % acc['name']}
    if act == 'password':
        h.store.set_account(name, password=b.get('password'), must_change=int(bool(
            b.get('must_change', 1))))
        h.store.db.run('DELETE FROM sessions WHERE kind = ? AND LOWER(name) = ?',
                       ('admin', name.lower()))
        h.store.add_event('accounts', '%s set a new password for %s; their sign-ins are ended'
                          % (who, name), 'warn', who=who)
        return {'message': 'the password is set'}
    if act in ('disable', 'enable'):
        h.store.set_account(name, disabled=1 if act == 'disable' else 0)
        if act == 'disable':
            h.store.db.run('DELETE FROM sessions WHERE kind = ? AND LOWER(name) = ?',
                           ('admin', name.lower()))
        h.store.add_event('accounts', '%s switched %s %s' % (who, name, 'off' if
                                                            act == 'disable' else 'on'), who=who)
        return {'message': 'saved'}
    raise ApiError(400, 'no such thing to do to an account')


@route('GET', '/api/sessions')
def _sessions(h):
    h.me('fulladmin')
    h.store.sessions_tidy(int(CONFIG.get('session_idle_min') or 120) * 60)
    return {'sessions': h.store.sessions()}


@route('POST', '/api/sessions')
def _sessions_do(h):
    s = h.me('fulladmin')
    ident = int(h.body().get('id') or 0)
    row = h.store.db.row('SELECT kind, name, ip FROM sessions WHERE id = ?', (ident,))
    if not row:
        raise ApiError(404, 'that sign-in has ended already')
    h.store.session_drop(ident)
    h.store.add_event('sign-ins', '%s ended the sign-in of %s%s' % (
        s['name'], row.get('name') or '?', (' from %s' % row['ip']) if row.get('ip') else ''),
        'warn', who=s['name'])
    return {'message': 'ended'}


def lockouts_view(d):
    t = time.time()
    rows = []
    for key, fails in list(d.login_fails.items()):
        info = d.login_info.get(key) or {}
        span = float(info.get('span') or 0)
        if not span:
            continue
        fails = [x for x in fails if t - x < span]
        if not fails:
            continue
        kind, who = key
        by_name = kind.endswith('-name')
        base = kind[:-5] if by_name else kind
        limit = int(info.get('limit') or 0)
        locked = bool(limit) and len(fails) >= limit
        rows.append({
            'kind': kind, 'who': who, 'by': 'name' if by_name else 'address',
            'what': LOCKOUT_KINDS.get(base, base), 'base': base,
            'words': info.get('what') or 'wrong passwords',
            'fails': len(fails), 'limit': limit, 'locked': locked,
            'until': (fails[0] + span) if locked else None,
            'forgets': fails[-1] + span, 'first': fails[0], 'last': fails[-1],
            'others': list(info.get('others') or [])})
    rows.sort(key=lambda r: (not r['locked'], -r['last']))
    return rows


@route('GET', '/api/lockouts')
def _lockouts(h):
    h.me('fulladmin')
    return {'nodes': [{'node': node_name(), 'me': True, 'fresh': True,
                       'rows': lockouts_view(h.d)}], 'now': time.time(), 'cluster': False}


@route('POST', '/api/lockouts')
def _lockouts_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act = b.get('action')
    with ApiHandler.THROTTLE_LOCK:
        if act == 'clear_all':
            n = len(h.d.login_fails)
            h.d.login_fails.clear()
            h.d.login_info.clear()
            words = 'every lockout (%d)' % n
        elif act == 'clear':
            key = (str(b.get('kind') or ''), str(b.get('who') or ''))
            if key not in h.d.login_fails:
                raise ApiError(404, 'that is not counted any more')
            h.d.login_fails.pop(key, None)
            h.d.login_info.pop(key, None)
            words = '%s (%s)' % (key[1], key[0])
        else:
            raise ApiError(400, 'no such thing to do')
    h.store.add_event('sign-ins', '%s cleared %s' % (s['name'], words), 'warn', who=s['name'])
    return {'message': 'cleared', 'told': []}


@route('GET', '/api/events')
def _events(h):
    h.me()
    rows, total = h.store.events(h.num('limit', 100, 1, 1000), h.num('offset', 0),
                                 h.arg('q'), h.arg('kind'), h.num('from', 0, 0, 10 ** 11),
                                 h.num('to', 0, 0, 10 ** 11))
    return {'rows': rows, 'total': total, 'kinds': h.store.event_kinds()}


def log_tail(lines=400):
    try:
        with open(str(LOG_FILE), 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4 * 1024 * 1024))
            text = f.read().decode('utf-8', 'replace')
        return text.splitlines()[-lines:]
    except OSError:
        return []


@route('GET', '/api/log')
def _log(h):
    h.me('fulladmin')
    return {'lines': log_tail(h.num('lines', 400, 10, 5000)), 'file': str(LOG_FILE)}


# ---- Certificates and TLS -------------------------------------------------------------
@route('GET', '/api/web')
def _web(h):
    me = h.me()
    store = h.store
    t = node_name()
    nodes = [{'name': t, 'me': True, 'alive': True, 'address': node_address(),
              'version': __version__, 'host': socket.getfqdn(), 'web': web_state(store)}]
    return {'node': t, 'cluster': False, 'nodes': nodes,
            'vip': {'on': False, 'address': '', 'fqdn': '', 'holder': []},
            'tls': {t: tls_view(tls_profile(store, t))},
            'acme': {'on': bool(CONFIG.get('acme')), 'care': {t: acme_care(store, t)},
                     'state': {t: acme_state(store, t)}, 'before': {t: acme_before(store, t)}},
            'presets': TLS_PRESETS, 'dh': DH_WORDS, 'plex': plex_view(store),
            'may': me.get('role') == 'fulladmin'}


@route('POST', '/api/web')
def _web_do(h):
    s = h.me('fulladmin')
    who = s['name']
    b = h.body()
    act = str(b.get('action') or '')
    store = h.store
    target = str(b.get('target') or node_name())
    if target not in tls_targets(store):
        raise ApiError(400, 'there is no node called %s' % target)
    prof = tls_profile(store, target) or {}
    words = target
    if act in ('tls_upload', 'tls_selfsigned'):
        if act == 'tls_upload':
            pfx = b''
            if b.get('pfx'):
                try:
                    pfx = base64.b64decode(str(b['pfx']), validate=True)
                except (ValueError, binascii.Error):
                    raise ApiError(400, 'the PFX / PKCS#12 file did not arrive whole')
                if len(pfx) > 200000:
                    raise ApiError(400, 'that is far bigger than any certificate file')
            pending = unseal_secret(prof['pending_key'], 'web:tls') if prof.get('pending_key') \
                else ''
            got = cert_bundle(b.get('cert'), b.get('key'), b.get('chain'), b.get('root'), pfx,
                              str(b.get('password') or ''), pending)
            how = 'uploaded'
        else:
            names = [x.strip() for x in re.split(r'[\s,;]+', str(b.get('names') or '')) if x.strip()]
            key, crt = make_selfsigned(str(b.get('cn') or '').strip(), names,
                                       int(b.get('days') or 825), str(b.get('kind') or 'rsa2048'),
                                       org=str(b.get('org') or ''))
            got = cert_bundle(crt, key)
            how = 'self-signed, made here'
        info = got['info']
        if info.get('not_after') and info['not_after'] < time.time():
            raise ApiError(400, 'this certificate ran out on %s' % stamp(info['not_after']))
        prof.update(cert=got['cert'], chain=got['chain'], root=got['root'],
                    key=seal_secret(got['key'], 'web:tls'), info=info,
                    chain_info=got['chain_info'], root_info=got['root_info'],
                    verify=got['verify'], how=how)
        prof.pop('pending_key', None)
        prof.pop('csr', None)
        prof.setdefault('preset', 'standard')
        prof.setdefault('dh', 'none')
        prof.setdefault('hsts', 0)
        tls_save(store, target, prof, who)
        acme_care_set(store, target, False, who, 'a certificate was put in by hand')
        store.add_event('settings', '%s put a certificate in for %s: %s (until %s)' % (
            who, words, info.get('subject'), stamp(info.get('not_after')) if
            info.get('not_after') else '?'), 'warn', who=who)
        return {'message': 'the certificate of %s is kept; it is put in within a few '
                           'seconds' % words, 'tls': tls_view(prof)}
    if act == 'tls_csr':
        names = [x.strip() for x in re.split(r'[\s,;]+', str(b.get('names') or '')) if x.strip()]
        key, csr = make_csr(str(b.get('cn') or '').strip(), names, str(b.get('kind') or 'rsa2048'),
                            org=str(b.get('org') or ''), unit=str(b.get('unit') or ''),
                            country=str(b.get('country') or ''), city=str(b.get('city') or ''))
        prof.update(pending_key=seal_secret(key, 'web:tls'), csr=csr,
                    csr_at=time.time(), csr_cn=str(b.get('cn') or ''))
        store.set_meta('web:tls:' + target, json.dumps(dict(prof, at=time.time(), by=who)))
        store.add_event('settings', '%s made a certificate request for %s (%s)'
                        % (who, words, b.get('cn')), who=who)
        return {'message': 'the request is made: give it to your certificate authority, then '
                           'upload what it sends back - the key waits here', 'csr': csr,
                'tls': tls_view(prof)}
    if act == 'tls_csr_drop':
        prof.pop('pending_key', None)
        prof.pop('csr', None)
        store.set_meta('web:tls:' + target, json.dumps(dict(prof, at=time.time(), by=who)))
        return {'message': 'the request and its waiting key are gone', 'tls': tls_view(prof)}
    if act == 'tls_policy':
        if not prof.get('cert'):
            raise ApiError(409, 'put a certificate in first (Upload, Request or Self-signed): '
                                'the TLS settings are written with it')
        prof.update(tls_policy_checked(b))
        tls_save(store, target, prof, who)
        store.add_event('settings', '%s changed the TLS settings of %s: %s, DH %s%s' % (
            who, words, prof['preset'], prof['dh'],
            ', HSTS %d s' % prof['hsts'] if prof.get('hsts') else ''), who=who)
        slow = prof['dh'] in ('gen3072', 'gen4096')
        return {'message': 'the TLS settings of %s are kept%s' % (
            words, '; making the DH parameters takes a while' if slow else ''),
                'tls': tls_view(prof)}
    if act == 'tls_forget':
        store.db.run('DELETE FROM meta WHERE k = ?', ('web:tls:' + target,))
        store.db.run('DELETE FROM meta WHERE k = ?', ('web:state:' + target,))
        acme_care_set(store, target, False, who, 'plexmon lets its TLS go')
        store.add_event('settings', '%s: plexmon no longer looks after the TLS of %s (nginx '
                        'keeps what it has)' % (who, words), 'warn', who=who)
        return {'message': 'plexmon no longer looks after the TLS of %s; nginx keeps the files '
                           'it has' % words}
    if act == 'tls_pem':
        if not prof.get('cert'):
            raise ApiError(404, 'no certificate is kept for %s' % words)
        return {'pem': prof['cert'] + (prof.get('chain') or ''), 'csr': prof.get('csr') or ''}
    if act == 'retry':
        store.set_meta('web:retry:' + node_name(), str(time.time()))
        WEB_WORK['wake'].set()
        return {'message': '%s tries again within a few seconds' % node_name()}
    if act == 'plex_off':
        try:
            said = plex_cert_off(store, who)
        except (ValueError, OSError) as e:
            raise ApiError(400, str(e))
        return {'message': said}
    if act == 'plex_now':
        if not prof.get('cert'):
            raise ApiError(409, 'there is no certificate kept here to hand to Plex')
        try:
            said = plex_cert_apply(store, tls_resolved(prof), prof)
        except (ValueError, OSError) as e:
            raise ApiError(400, str(e))
        store.add_event('settings', '%s: %s' % (who, said), who=who)
        return {'message': said}
    raise ApiError(400, 'no such thing to do')


# ---- certificates by ACME -----------------------------------------------------------------
@route('GET', '/api/acme')
def _acme(h):
    h.me('fulladmin')
    return acme_view(h.store)


@route('POST', '/api/acme')
def _acme_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act = str(b.get('action') or '')
    try:
        if act == 'order':
            if not CONFIG.get('acme'):
                raise ValueError('certificates by ACME are off - switch them on and save first')
            target = str(b.get('target') or node_name())
            if target not in tls_targets(h.store):
                raise ValueError('there is no such certificate')
            name = acme_name_of(h.store, target)
            if not name:
                raise ValueError('the name of this certificate is not known - give it (Its name…)')
            acme_care_set(h.store, target, True, s['name'], 'ordered by hand')
            acme_start(h.store, [(target, name)], s['name'])
            return dict(acme_view(h.store), message='the certificate of %s is being ordered' % name)
        if act == 'dns_check':
            return acme_dns_check(h.store)
        if act == 'put_back':
            target = str(b.get('target') or node_name())
            if target not in tls_targets(h.store):
                raise ValueError('there is no such certificate')
            acme_put_back(h.store, target, s['name'])
            return dict(acme_view(h.store), message='the certificate from before ACME is back; '
                        'it is put in within a few seconds')
        if act == 'care':
            target = str(b.get('target') or node_name())
            if target not in tls_targets(h.store):
                raise ValueError('there is no such certificate')
            acme_care_set(h.store, target, bool(b.get('on')), s['name'])
            return acme_view(h.store)
        if act == 'probe':
            return acme_probe(h.store, str(b.get('target') or node_name()))
        if act == 'name':
            name = str(b.get('name') or '').strip().lower().rstrip('.')
            if name and not re.match(r'^(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+'
                                     r'[a-z]{2,63}\Z', name):
                raise ValueError('%s is not a full DNS name (like plex.example.cz)' % name[:80])
            h.d.save_settings({'acme_node_name': name}, by=s['name'])
            h.store.add_event('settings', '%s: the certificate of %s is for %s' % (
                s['name'], node_name(), name or 'its full host name'), who=s['name'])
            return acme_view(h.store)
        if act in ('manual_done', 'manual_drop'):
            got = _meta_json(h.store, 'acme:manual') or {}
            if not got.get('id'):
                raise ValueError('no order waits for a TXT record now')
            if act == 'manual_done':
                got['done'] = time.time()
                h.store.set_meta('acme:manual', json.dumps(got))
            else:
                h.store.db.run("DELETE FROM meta WHERE k = 'acme:manual'")
            h.store.add_event('settings', '%s said the TXT record %s %s' % (
                s['name'], got.get('rr'), 'is in DNS' if act == 'manual_done' else
                'will not be put in - the order is given up'), who=s['name'])
            return acme_view(h.store)
        if act == 'forget':
            h.store.db.run("DELETE FROM meta WHERE k = 'acme:account'")
            h.store.add_event('settings', '%s forgot the account at the ACME CA - a new one is '
                              'made at the next order' % s['name'], 'warn', who=s['name'])
            return acme_view(h.store)
    except (ValueError, AcmeError) as e:
        raise ApiError(400, str(e))
    raise ApiError(400, 'no such thing to do')


# ---- upgrades ---------------------------------------------------------------------------
@route('GET', '/api/upgrade')
def _upgrade(h):
    h.me('fulladmin')
    return upgrade_view(h.store)


@route('POST', '/api/upgrade/package')
def _upgrade_package(h):
    s = h.me('fulladmin')
    UPGRADE_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='incoming-', suffix='.tar.gz', dir=str(UPGRADE_DIR))
    os.close(fd)
    tmp = Path(tmp)
    try:
        h.body_to_file(tmp, UPGRADE_MOST)
        try:
            info = upgrade_inspect(tmp)
        except ValueError as e:
            h.store.add_event('starting and stopping', '%s sent a package that was refused: %s'
                              % (s['name'], e), 'warn', who=s['name'])
            raise ApiError(400, str(e))
        if info.get('older'):
            raise ApiError(400, '%s is older than %s, which runs here: going back is done on '
                                'Going back to an older version, or on the command line'
                           % (info['version'], info.get('installed')))
        plan = upgrade_plan(h.store)
        if plan and plan['state'] == 'running':
            raise ApiError(409, 'an upgrade is going on: wait for it, or stop it first')
        meta = upgrade_store_package(h.store, tmp, info, s['name'])
        h.store.add_event('starting and stopping', '%s sent plexmon %s (signed by %s), ready '
                          'to be put in' % (s['name'], meta['version'], meta.get('signed_by')),
                          'warn', who=s['name'])
        return dict(upgrade_view(h.store), message='plexmon %s is checked and ready'
                    % meta['version'])
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@route('POST', '/api/upgrade')
def _upgrade_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act = b.get('action')
    store = h.store
    try:
        if act == 'start':
            upgrade_start(store, s['name'], pages=True)
            return dict(upgrade_view(store), message='the upgrade has begun')
        if act == 'online_look':
            row = online_look(store, s['name'])
            if row.get('error'):
                raise ValueError('%s: %s' % (row.get('url'), row['error']))
            n = row.get('newest') or {}
            return dict(upgrade_view(store), message=(
                'plexmon %s is there - newer than %s' % (n['version'], __version__)
                if row.get('newer') else ('nothing newer than %s' % __version__ if n else
                                          'no plexmon package there')))
        if str(act or '').startswith('downgrade') and not CONFIG.get('upgrade_downgrade'):
            raise ValueError('going back to an older version is not offered on these pages: '
                             'switch it on in Settings → Upgrades')
        if act == 'downgrade_changelog':
            return {'changelog': downgrade_changelog(str(b.get('version') or ''))}
        if act == 'downgrade_list':
            return {'downgrade': downgrade_list(store)}
        if act in ('downgrade_get', 'online_get'):
            plan = upgrade_plan(store)
            if plan and plan['state'] == 'running':
                raise ValueError('an upgrade is going on: wait for it, or stop it first')
            meta = online_fetch(store, str(b.get('version') or ''), s['name'],
                                older=act == 'downgrade_get')
            if b.get('start'):
                upgrade_start(store, s['name'], pages=True)
                return dict(upgrade_view(store), message='plexmon %s fetched and checked; %s '
                            'has begun' % (meta['version'], 'going back to it'
                                           if act == 'downgrade_get' else 'the upgrade'))
            return dict(upgrade_view(store), message='plexmon %s is fetched, checked and ready'
                        % meta['version'])
        if act == 'hide':
            plan = upgrade_plan(store)
            if plan and plan.get('state') == 'running':
                raise ValueError('the upgrade is going on')
            if plan:
                store.set_meta('upgrade:hidden', json.dumps({'id': plan.get('id')}))
            return upgrade_view(store)
        if act == 'cancel':
            upgrade_cancel(store, s['name'])
            return dict(upgrade_view(store), message='the upgrade is stopped')
        if act == 'forget':
            plan = upgrade_plan(store)
            if plan and plan['state'] == 'running':
                raise ValueError('an upgrade is going on with it')
            for old in UPGRADE_DIR.glob('plexmon-*.tar.gz'):
                try:
                    old.unlink()
                except OSError:
                    pass
            store.drop_meta('upgrade:pkg')
            return dict(upgrade_view(store), message='the package is forgotten')
        if act == 'maintenance':
            raise ValueError('plexmon has no maintenance: it is one server, never a cluster')
    except ValueError as e:
        raise ApiError(400, str(e))
    except (OSError, subprocess.SubprocessError) as e:
        raise ApiError(502, 'the address could not be read: %s'
                       % (getattr(e, 'reason', None) or e))
    raise ApiError(400, 'no such thing to do')


class ApiHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# The Settings side, running: its API, its threads, what it says of itself
# ---------------------------------------------------------------------------
class Admin(object):
    """Started by plexmon.py's daemon (or by itself, plexmon --admin-only)."""

    def __init__(self, monitor=None):
        self.monitor = monitor              # () -> what the dashboard's daemon says
        self.store = open_store()
        self.started = time.time()
        STARTED[0] = self.started
        self.login_fails = {}
        self.login_info = {}
        self.stop = threading.Event()
        self.server = None

    # ---- settings -----------------------------------------------------------
    def settings_state(self, may_change=True):
        out = []
        saved = self.store.settings_saved()
        written = self.store.settings_written()
        for key in DEFAULTS:
            value = CONFIG.get(key)
            shape = TUNABLE.get(key)
            source = 'saved' if key in saved else ('file' if key in FILE_KEYS else 'default')
            one = {'key': key, 'value': value, 'default': DEFAULTS[key],
                   'name': NAMES.get(key, key), 'says': SAYS.get(key, ''),
                   'short': SAYS_SHORT.get(key, ''),
                   'secret': key in SECRET_KEYS, 'source': source,
                   'source_says': {'saved': 'saved on these pages',
                                   'file': 'from config.json',
                                   'default': 'the built-in default'}[source],
                   'in_file': key in FILE_KEYS,
                   'file': FILE_CONFIG.get(key) if key in FILE_KEYS else None,
                   'file_only': key in FILE_ONLY_KEYS, 'node_key': False, 'hisec': None}
            if key in written:
                one['saved_at'] = written[key]['at']
                one['saved_by'] = written[key]['by']
            if key in SECRET_KEYS:
                plain = unseal_secret(value) if key in SEALED_KEYS else value
                one['value'] = secret_shown(plain or '') if may_change else ''
                one['set'] = bool(value)
                one['file'] = None
            if isinstance(shape, tuple) and shape and isinstance(shape[0], str):
                one['choices'] = list(shape)
                if key in CHOICE_WORDS:
                    one['choice_words'] = CHOICE_WORDS[key]
            elif isinstance(shape, tuple):
                one['min'], one['max'] = shape
            out.append(one)
        return out

    def save_settings(self, changes, by='', keep_session=None):
        checked = {}
        was_2fa = bool(CONFIG.get('admin_2fa'))
        was_plex = bool(CONFIG.get('plex_cert'))
        for key, value in (changes or {}).items():
            if key in SECRET_KEYS and is_masked(value):
                continue
            checked[key] = check_setting(key, value)
            if key in SEALED_KEYS and checked[key]:
                checked[key] = seal_secret(checked[key])
        for k, v in checked.items():
            if v == FILE_CONFIG.get(k, DEFAULTS[k]):
                self.store.drop_setting(k)
            else:
                self.store.save_setting(k, v, by)
        if checked:
            self.store.add_event('settings', 'changed: %s' % ', '.join(
                '%s=%s' % (k, '***' if k in SECRET_KEYS else json.dumps(v, ensure_ascii=False))
                for k, v in sorted(checked.items())), who=by)
        load_config(saved=self.store.settings_saved())
        if not was_2fa and CONFIG.get('admin_2fa'):
            gone = self.store.db.run('DELETE FROM sessions WHERE kind = ? AND id <> ?',
                                     ('admin', int(keep_session or 0)))
            if gone:
                self.store.add_event('accounts', 'the second step was switched on: %d other '
                                     'sign-in(s) to the pages ended' % gone, 'warn', who=by)
        if was_plex and not CONFIG.get('plex_cert'):
            def off():
                try:
                    plex_cert_off(self.store, by)
                except Exception as e:                  # noqa: BLE001
                    log('Plex back to its own certificate: %s' % e, 'warn')
            threading.Thread(target=off, name='plexmon-plex-off', daemon=True).start()
        if 'api_port' in checked:
            said = ' The API port takes effect when plexmon is restarted.'
        else:
            said = ''
        WEB_WORK['wake'].set()
        publish_lang()
        return {'message': '%d setting(s) saved.%s' % (len(checked), said),
                'saved': sorted(checked)}

    # ---- what it is doing -----------------------------------------------------
    def status(self):
        problems = []
        if not nginx_adopted():
            problems.append('%s is not plexmon\'s yet: the certificate can be put in, but its '
                            'TLS settings and the answers for ACME cannot - install.sh '
                            '--upgrade adopts it' % NGINX_SITE)
        st = web_status(self.store)
        if st.get('state') == 'failed':
            problems.append('the certificate could not be put in: %s'
                            % ((st.get('said') or ['?'])[-1]))
        cert = cert_now(nginx_conf_cert_paths()[0])
        if cert and cert.get('not_after'):
            left = (cert['not_after'] - time.time()) / 86400
            if left < 0:
                problems.append('the certificate of %s ran out %s' % (cert.get('cn') or '?',
                                                                      stamp(cert['not_after'])))
            elif left < 14:
                problems.append('the certificate of %s runs out in %d days'
                                % (cert.get('cn') or '?', int(left)))
        ac = acme_state(self.store, node_name())
        if CONFIG.get('acme') and ac.get('ok') is False:
            problems.append('the last order from the ACME CA failed: %s' % ac.get('said'))
        if BAD_CONFIG:
            problems.append('%s: %s' % (CONFIG_FILE, ', '.join(BAD_CONFIG)))
        mon = {}
        try:
            mon = self.monitor() if self.monitor else {}
        except Exception:                               # noqa: BLE001
            mon = {}
        ev = self.store.db.value('SELECT MAX(id) AS n FROM events', (), 0) or 0
        up = _meta_json(self.store, 'upgrade:online') or {}
        newest = (up.get('newest') or {}).get('version')
        return {'ok': True, 'version': __version__,
                'site': CONFIG.get('site_name') or short_host(), 'host': socket.gethostname(),
                'started': self.started, 'uptime': time.time() - self.started, 'now': time.time(),
                'serial': '%s/%s' % (ev, int(web_status(self.store).get('at') or 0)),
                'problems': problems, 'node': node_name(),
                'database': {'engine': 'sqlite', 'where': self.store.db.where(),
                             'size': self.store.db.size()},
                'counts': self.store.counts(), 'lang': CONFIG.get('ui_lang') or 'cs',
                'monitor': mon, 'cert': cert, 'acme_on': bool(CONFIG.get('acme')),
                'newer': newest if newest and _ver_tuple(newest) > _ver_tuple(__version__)
                else '', 'high_security': False, 'hisec_waived': [],
                'nodes': [{'name': node_name(), 'alive': True, 'age': 0,
                           'version': __version__}]}

    # ---- starting -------------------------------------------------------------
    def adopt_existing_cert(self):
        """The certificate nginx has now, taken into Settings once: its card
        shows it whole, Plex can be given it, and nothing is rewritten - the
        TLS settings are kept exactly as the vhost had them."""
        t = node_name()
        if tls_profile(self.store, t) or self.store.get_meta('web:adopted'):
            return
        cert_path, key_path = nginx_conf_cert_paths()
        try:
            got = cert_bundle(cert_path.read_text(), key_path.read_text())
        except (OSError, ValueError) as e:
            log('the certificate nginx has (%s) was not taken over: %s' % (cert_path, e), 'warn')
            self.store.set_meta('web:adopted', 'no: %s' % e)
            return
        tls_text = ''
        try:
            tls_text = NGINX_TLS_CONF.read_text()
        except OSError:
            tls_text = nginx_conf_text()
        prot = re.search(r'^\s*ssl_protocols\s+([^;]+);', tls_text, re.M)
        ciph = re.search(r'^\s*ssl_ciphers\s+([^;]+);', tls_text, re.M)
        pref = re.search(r'^\s*ssl_prefer_server_ciphers\s+(on|off)\s*;', tls_text, re.M)
        hsts = re.search(r'Strict-Transport-Security\s+"?max-age=(\d+)', tls_text)
        protocols = [p for p in (prot.group(1).split() if prot else ['TLSv1.2', 'TLSv1.3'])
                     if p in ('TLSv1.2', 'TLSv1.3')] or ['TLSv1.2', 'TLSv1.3']
        prof = {'cert': got['cert'], 'chain': got['chain'], 'root': got['root'],
                'key': seal_secret(got['key'], 'web:tls'), 'info': got['info'],
                'chain_info': got['chain_info'], 'root_info': got['root_info'],
                'verify': got['verify'], 'how': 'taken over from nginx',
                'preset': 'custom', 'protocols': protocols,
                'ciphers': ciph.group(1).strip().strip('"\'') if ciph else '',
                'prefer': bool(pref and pref.group(1) == 'on'), 'dh': 'none',
                'hsts': int(hsts.group(1)) if hsts else 0, 'at': time.time(), 'by': 'install'}
        self.store.set_meta('web:tls:' + t, json.dumps(prof))
        want, _plex = web_hash(self.store)
        # Already in place as it is: marked done, so nothing is written now.
        self.store.set_meta('web:state:' + t, json.dumps({
            'state': 'done', 'hash': want, 'at': time.time(), 'dh': 'none',
            'said': ['taken over from nginx as it was: %s' % got['info'].get('subject')]}))
        self.store.set_meta('web:adopted', 'yes')
        self.store.add_event('settings', 'the certificate nginx has (%s, until %s) is looked '
                             'after on Settings → Certificates and TLS now' % (
                                 got['info'].get('subject'), stamp(got['info'].get('not_after'))))

    def start(self):
        """The API on 127.0.0.1 and the threads; returns at once."""
        _LOG_WANTED[0] = True
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            sealing_key(make=True)
        except OSError as e:
            log('the key to seal secrets with: %s' % e, 'error')
        try:
            self.adopt_existing_cert()
        except Exception as e:                          # noqa: BLE001
            log('taking over the certificate: %s' % e, 'warn')
        api_token(make=True)
        publish_lang()
        port = int(CONFIG.get('api_port') or 9848)
        self.server = ApiHttpServer(('127.0.0.1', port), ApiHandler)
        self.server.admin = self
        try:
            write_replacing(PORT_FILE, '%d\n' % port, 0o644)
        except OSError:
            pass
        threading.Thread(target=self.server.serve_forever, name='plexmon-admin-api',
                         daemon=True).start()
        threading.Thread(target=web_loop, args=(self.store, self.stop), name='plexmon-web',
                         daemon=True).start()
        threading.Thread(target=self.loop, name='plexmon-admin', daemon=True).start()
        floor = self.store.get_meta('downgrade:floor:' + node_name()) or '0.0.0'
        if _ver_tuple(DOWNGRADE_FLOOR) > _ver_tuple(floor):
            self.store.set_meta('downgrade:floor:' + node_name(), DOWNGRADE_FLOOR)
        log('plexmon %s Settings: API on http://127.0.0.1:%d' % (__version__, port))
        self.store.add_event('starting and stopping', 'plexmon %s started on %s'
                             % (__version__, node_name()))

    def loop(self):
        last_tidy = 0.0
        while not self.stop.is_set():
            UPGRADE_WAKE.wait(3)
            UPGRADE_WAKE.clear()
            for fn in (upgrade_tick, acme_tick, online_tick):
                try:
                    fn(self.store)
                except Exception as e:                  # noqa: BLE001
                    log('%s: %s\n%s' % (fn.__name__, e, traceback.format_exc()), 'error')
            if time.time() - last_tidy > 3600:
                last_tidy = time.time()
                try:
                    self.store.tidy()
                except Exception as e:                  # noqa: BLE001
                    log('tidying the database: %s' % e, 'warn')

    def close(self):
        self.stop.set()
        if self.server:
            self.server.shutdown()


def publish_lang():
    """The language the dashboard opens in, where its PHP reads it."""
    try:
        write_replacing(RUN_DIR / 'ui-lang', '%s\n' % (CONFIG.get('ui_lang') or 'cs'), 0o644)
    except OSError:
        pass


def start(monitor=None):
    """What plexmon.py calls: Settings started beside the dashboard's daemon."""
    a = Admin(monitor)
    a.start()
    return a


# ---------------------------------------------------------------------------
# The command line: plexmon --help (the dashboard's options are plexmon.py's)
# ---------------------------------------------------------------------------
class Palette(object):
    def __init__(self, on):
        self.on = bool(on)

    def _c(self, code, text):
        return '\033[%sm%s\033[0m' % (code, text) if self.on else str(text)

    def good(self, t):
        return self._c('32', t)

    def bad(self, t):
        return self._c('31', t)

    def warn(self, t):
        return self._c('33', t)

    def info(self, t):
        return self._c('36', t)

    def dim(self, t):
        return self._c('2', t)

    def bold(self, t):
        return self._c('1', t)


PAL = Palette(sys.stdout.isatty() and os.environ.get('TERM') != 'dumb')


def _print_json(obj):
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def _kv(pairs):
    w = max([len(k) for k, _ in pairs] + [4])
    for k, v in pairs:
        print('  %s  %s' % (PAL.dim(k.ljust(w)), v))


def _ask_password(words='password'):
    import getpass
    if not sys.stdin.isatty():
        got = sys.stdin.readline().rstrip('\n')
        return got
    a = getpass.getpass('  %s: ' % words)
    b = getpass.getpass('  again: ')
    if a != b:
        raise SystemExit('plexmon: the two are not the same')
    return a


def _daemon_get(path, timeout=10):
    """Ask the running daemon (as the pages do) - for what only it knows."""
    try:
        port = int(PORT_FILE.read_text().strip())
    except (OSError, ValueError):
        port = int(CONFIG.get('api_port') or 9848)
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                 headers={'X-Otp-Token': api_token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:                                   # noqa: BLE001
        return None


def service_active(name=SERVICE):
    try:
        return subprocess.run([SYSTEMCTL, 'is-active', name], capture_output=True, text=True,
                              timeout=10).stdout.strip() or '?'
    except (OSError, subprocess.SubprocessError):
        return '?'


def cli_status(store, as_json=False):
    health = _daemon_get('/api/health')
    cert = cert_now(nginx_conf_cert_paths()[0])
    prof = tls_profile(store, node_name()) or {}
    up = online_view(store).get('online') or {}
    out = {'version': __version__, 'node': node_name(), 'service': service_active(),
           'answering': bool(health), 'database': store.db.where(),
           'accounts': store.counts()['accounts'],
           'nginx_adopted': nginx_adopted(), 'nginx_site': str(NGINX_SITE),
           'certificate': {'cn': (cert or {}).get('cn'), 'issuer': (cert or {}).get('issuer'),
                           'not_after': (cert or {}).get('not_after'), 'how': prof.get('how')},
           'tls_state': web_status(store).get('state') or '',
           'acme': bool(CONFIG.get('acme')), 'acme_state': acme_state(store, node_name()),
           'newer': up.get('newest', {}).get('version') if up.get('newer') else '',
           'console_at_login': console_login_state(),
           'plex_cert': plex_view(store)}
    if as_json:
        _print_json(out)
        return 0
    c = out['certificate']
    left = int((c['not_after'] - time.time()) / 86400) if c.get('not_after') else None
    print(PAL.bold('plexmon %s' % __version__) + '  ' + node_name())
    _kv([('service', (PAL.good if out['service'] == 'active' else PAL.bad)(out['service'])),
         ('Settings API', PAL.good('answering') if out['answering'] else PAL.bad('not answering')),
         ('database', out['database']), ('accounts', str(out['accounts'])),
         ('nginx', ('%s is plexmon\'s' % NGINX_SITE) if out['nginx_adopted'] else
          PAL.warn('%s not adopted (install.sh --upgrade)' % NGINX_SITE)),
         ('certificate', '%s, %s%s' % (c.get('cn') or '-', (c.get('issuer') or '-')[:60],
                                       '' if left is None else (', %d days left' % left))),
         ('TLS', out['tls_state'] or '-'),
         ('ACME', ('on' + (', last order failed: %s' % out['acme_state'].get('said')
                           if out['acme_state'].get('ok') is False else ''))
          if out['acme'] else 'off'),
         ('Plex certificate', 'on' if out['plex_cert']['on'] else 'off'),
         ('newer version', out['newer'] or 'none known'),
         ('console at login', 'on' if out['console_at_login'] else 'off')])
    return 0


def cli_users(store, as_json=False):
    rows = store.accounts()
    if as_json:
        _print_json(rows)
        return 0
    mfa = mfa_all(store)
    if not rows:
        print('  no account yet: plexmon --add-user NAME --role fulladmin')
    for u in rows:
        print('  %-24s %-10s %-4s %s%s' % (u['name'], ROLE_WORDS.get(u['role'], u['role']),
                                           'off' if u['disabled'] else 'on',
                                           ('2FA ' if mfa.get(u['name'].lower()) else ''),
                                           ('last in %s from %s' % (stamp(u['last_in']),
                                                                    u['last_from'])
                                            if u.get('last_in') else 'never signed in')))
    return 0


def cli_settings(store, as_json=False):
    a = Admin.__new__(Admin)
    a.store = store
    rows = a.settings_state(True)
    if as_json:
        _print_json(rows)
        return 0
    for pane, keys in PANES.items():
        print(PAL.bold(pane))
        for k in keys:
            s = next(x for x in rows if x['key'] == k)
            v = s['value']
            print('  %-24s %-30s %s' % (k, json.dumps(v, ensure_ascii=False)[:30],
                                        PAL.dim(s['source_says'])))
    return 0


def cli_cert(store, as_json=False):
    prof = tls_profile(store, node_name()) or {}
    st = web_state(store)
    out = {'kept': tls_view(prof), 'nginx': st}
    if as_json:
        _print_json(out)
        return 0
    info = prof.get('info') or st.get('cert_now') or {}
    _kv([('name', info.get('cn') or '-'), ('names', ', '.join(info.get('san') or []) or '-'),
         ('issued by', info.get('issuer') or '-'),
         ('valid until', stamp(info.get('not_after'))), ('key', info.get('key') or '-'),
         ('how', prof.get('how') or 'not kept here'),
         ('TLS', prof.get('preset') or '-'), ('in nginx', st.get('cert_file')),
         ('state', st.get('state') or '-'), ('said', '; '.join(st.get('said') or []) or '-')])
    return 0


def cli_cert_import(store, cert, key, chain=''):
    got = cert_bundle(Path(cert).read_text(), Path(key).read_text() if key else '',
                      Path(chain).read_text() if chain else '')
    prof = tls_profile(store, node_name()) or {}
    prof.update(cert=got['cert'], chain=got['chain'], root=got['root'],
                key=seal_secret(got['key'], 'web:tls'), info=got['info'],
                chain_info=got['chain_info'], root_info=got['root_info'], verify=got['verify'],
                how='uploaded')
    prof.setdefault('preset', 'standard')
    prof.setdefault('dh', 'none')
    prof.setdefault('hsts', 0)
    tls_save(store, node_name(), prof, 'root (command line)')
    acme_care_set(store, node_name(), False, 'root', 'a certificate was put in by hand')
    store.add_event('settings', 'root put a certificate in on the command line: %s'
                    % got['info'].get('subject'), 'warn', who='root')
    with WEB_LOCK:
        web_apply(store)
    return cli_cert(store)


def cli_upgrade_follow(store):
    last = None
    while True:
        plan = upgrade_plan(store)
        if not plan:
            print('no upgrade is going on')
            return 0
        st = plan['steps'][0]
        line = '%s → %s: %s %s' % (node_name(), plan['version'], st.get('state'),
                                   st.get('said') or '')
        if line != last:
            print(line)
            last = line
        if plan['state'] in ('done', 'failed', 'cancelled'):
            return 0 if plan['state'] == 'done' else 1
        time.sleep(2)


def cli_main(argv):
    ap = argparse.ArgumentParser(prog='plexmon', description='plexmon Settings on the command '
                                 'line - the same database as the pages. The dashboard\'s own '
                                 'options (--check, --diag, --wake, --atv...) are plexmon.py\'s.')
    ap.add_argument('--json', action='store_true', help='answer in JSON')
    ap.add_argument('--no-color', action='store_true')
    ap.add_argument('--yes', action='store_true', help='do not ask')
    g = ap.add_argument_group('what it is doing')
    g.add_argument('--status', action='store_true', help='Settings in one screen')
    g.add_argument('--version', action='store_true')
    g.add_argument('--events', nargs='?', const=50, type=int, metavar='N')
    g.add_argument('--log', nargs='?', const=100, type=int, metavar='N',
                   help='the tail of /var/log/plexmon/plexmon.log')
    g = ap.add_argument_group('accounts of Settings')
    g.add_argument('--users', action='store_true')
    g.add_argument('--add-user', metavar='NAME')
    g.add_argument('--role', metavar='ROLE', choices=ROLES_ALL, help='fulladmin or readonly')
    g.add_argument('--passwd', metavar='NAME', help='a new password (asked, or from stdin)')
    g.add_argument('--set-role', nargs=2, metavar=('NAME', 'ROLE'))
    g.add_argument('--del-user', metavar='NAME')
    g.add_argument('--disable-user', metavar='NAME')
    g.add_argument('--enable-user', metavar='NAME')
    g.add_argument('--reset-2fa', metavar='NAME')
    g.add_argument('--sessions', action='store_true')
    g = ap.add_argument_group('settings')
    g.add_argument('--settings', action='store_true')
    g.add_argument('--set', metavar='KEY=VALUE', action='append')
    g.add_argument('--unset', metavar='KEY', action='append')
    g = ap.add_argument_group('certificates and TLS')
    g.add_argument('--cert-status', action='store_true')
    g.add_argument('--cert-import', nargs='+', metavar='FILE', help='CERT KEY [CHAIN] (PEM)')
    g.add_argument('--cert-selfsigned', metavar='NAME')
    g.add_argument('--tls-apply', action='store_true', help='put in what is kept, now')
    g.add_argument('--nginx-adopt', action='store_true',
                   help='hand the Plex vhost over to plexmon (install.sh does it)')
    g.add_argument('--plex-cert', choices=('on', 'off', 'now'),
                   help='Plex Media Server uses the certificate too')
    g = ap.add_argument_group('automatic certificates (ACME)')
    g.add_argument('--acme-status', action='store_true')
    g.add_argument('--acme-order', action='store_true', help='order it now, followed here')
    g.add_argument('--acme-probe', action='store_true', help='try port 80 as the CA will')
    g = ap.add_argument_group('upgrades')
    g.add_argument('--upgrade-check', action='store_true')
    g.add_argument('--upgrade-online', nargs='?', const='', metavar='VERSION')
    g.add_argument('--upgrade-file', metavar='FILE')
    g.add_argument('--upgrade-status', action='store_true')
    g.add_argument('--upgrade-follow', action='store_true')
    g.add_argument('--upgrade-cancel', action='store_true')
    g.add_argument('--downgrade-check', action='store_true')
    g.add_argument('--downgrade', metavar='VERSION|FILE')
    g = ap.add_argument_group('the console')
    g.add_argument('--console', action='store_true', help='plexmon by its menus')
    g.add_argument('--console-at-login', choices=('on', 'off', 'status'))
    g.add_argument('--admin-only', action='store_true',
                   help='Settings alone, without the dashboard\'s daemon (for tests)')
    a = ap.parse_args(argv)
    if a.no_color:
        PAL.on = False
    if a.version:
        print('plexmon %s' % __version__)
        return 0
    if a.console:
        return console_main()
    if a.admin_only:
        _LOG_TO_STDERR[0] = True
        adm = Admin()
        adm.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    store = open_store()
    by = 'root (command line)'
    if a.status:
        return cli_status(store, a.json)
    if a.events is not None:
        rows, _n = store.events(a.events)
        if a.json:
            _print_json(rows)
        for r in reversed(rows):
            print('%s  %-22s %s%s' % (stamp(r['ts']), r['kind'], r['words'],
                                      PAL.dim(' (%s)' % r['who']) if r['who'] else ''))
        return 0
    if a.log is not None:
        for ln in log_tail(a.log):
            print(ln)
        return 0
    if a.users:
        return cli_users(store, a.json)
    if a.add_user:
        pw = _ask_password('a password for %s' % a.add_user)
        store.add_account(a.add_user, a.role or 'fulladmin', pw, note='made on the command line',
                          must_change=0)
        store.add_event('accounts', 'root made the account %s (%s) on the command line'
                        % (a.add_user, a.role or 'fulladmin'), who='root')
        print('the account %s is made (%s)' % (a.add_user, ROLE_WORDS[a.role or 'fulladmin']))
        return 0
    if a.passwd:
        pw = _ask_password('a new password for %s' % a.passwd)
        store.set_account(a.passwd, password=pw, must_change=0)
        store.db.run("DELETE FROM sessions WHERE kind = 'admin' AND LOWER(name) = ?",
                     (a.passwd.lower(),))
        store.add_event('accounts', 'root set a new password for %s on the command line'
                        % a.passwd, 'warn', who='root')
        print('the password of %s is set; their sign-ins are ended' % a.passwd)
        return 0
    if a.set_role:
        name, role = a.set_role
        if role not in ROLES_ALL:
            raise SystemExit('plexmon: the role is fulladmin or readonly')
        store.set_account(name, role=role)
        store.add_event('accounts', 'root made %s %s' % (name, ROLE_WORDS[role]), who='root')
        print('%s is %s' % (name, ROLE_WORDS[role]))
        return 0
    for flag, act in ((a.del_user, 'delete'), (a.disable_user, 'disable'),
                      (a.enable_user, 'enable')):
        if flag:
            acc = store.account(flag)
            if not acc:
                raise SystemExit('plexmon: there is no account called %s' % flag)
            if act in ('delete', 'disable') and acc['role'] == 'fulladmin' and \
                    not store.fulladmins_left(but=flag) and not a.yes:
                raise SystemExit('plexmon: that is the last full administrator (--yes to do it '
                                 'anyway)')
            if act == 'delete':
                store.drop_account(flag)
            else:
                store.set_account(flag, disabled=1 if act == 'disable' else 0)
                if act == 'disable':
                    store.db.run("DELETE FROM sessions WHERE kind = 'admin' AND LOWER(name) = ?",
                                 (flag.lower(),))
            store.add_event('accounts', 'root: %s %s' % (act, flag), 'warn', who='root')
            print('%s: %s' % (flag, act))
            return 0
    if a.reset_2fa:
        had = mfa_reset(store, a.reset_2fa, 'root')
        store.db.run("DELETE FROM sessions WHERE kind = 'admin' AND LOWER(name) = ?",
                     (a.reset_2fa.lower(),))
        print('the second step of %s is reset' % a.reset_2fa if had else
              '%s had no second step set up' % a.reset_2fa)
        return 0
    if a.sessions:
        rows = store.sessions()
        if a.json:
            _print_json(rows)
        for s in rows:
            print('  %-20s %-16s since %s, last %s' % (s['name'], s['ip'], stamp(s['created']),
                                                       stamp(s['last'])))
        return 0
    if a.settings:
        return cli_settings(store, a.json)
    if a.set or a.unset:
        adm = Admin.__new__(Admin)
        adm.store = store
        changes = {}
        for kv in a.set or []:
            k, _, v = kv.partition('=')
            changes[k.strip()] = v
        for k in a.unset or []:
            if k not in DEFAULTS:
                raise SystemExit('plexmon: %s is not a setting this version knows' % k)
            store.drop_setting(k)
            print('%s: back to %s' % (k, json.dumps(FILE_CONFIG.get(k, DEFAULTS[k]))))
        if changes:
            try:
                print(adm.save_settings(changes, by=by)['message'])
            except BadSetting as e:
                raise SystemExit('plexmon: %s' % e)
        return 0
    if a.cert_status:
        return cli_cert(store, a.json)
    if a.cert_import:
        files = a.cert_import + ['', '']
        return cli_cert_import(store, files[0], files[1], files[2])
    if a.cert_selfsigned:
        key, crt = make_selfsigned(a.cert_selfsigned, [], 825)
        got = cert_bundle(crt, key)
        prof = tls_profile(store, node_name()) or {}
        prof.update(cert=got['cert'], chain='', root='', key=seal_secret(got['key'], 'web:tls'),
                    info=got['info'], chain_info=[], root_info=[], verify='',
                    how='self-signed, made here')
        prof.setdefault('preset', 'standard')
        prof.setdefault('dh', 'none')
        prof.setdefault('hsts', 0)
        tls_save(store, node_name(), prof, by)
        with WEB_LOCK:
            web_apply(store)
        return cli_cert(store)
    if a.tls_apply:
        store.set_meta('web:retry:' + node_name(), str(time.time()))
        st = web_status(store)
        st.pop('hash', None)
        store.set_meta('web:state:' + node_name(), json.dumps(st))
        with WEB_LOCK:
            web_apply(store)
        return cli_cert(store)
    if a.nginx_adopt:
        changed, said = nginx_adopt()
        print(said)
        return 0 if changed or 'already' in said else 1
    if a.plex_cert:
        if a.plex_cert == 'off':
            store.drop_setting('plex_cert')
            store.add_event('settings', 'root: Plex Media Server no longer takes plexmon\'s '
                            'certificate', who='root')
            print(plex_cert_off(store, by))
            return 0
        if a.plex_cert == 'on':
            Admin.save_settings(_bare(store), {'plex_cert': 1}, by=by)
        prof = tls_profile(store, node_name()) or {}
        if not prof.get('cert'):
            raise SystemExit('plexmon: no certificate is kept here yet')
        print(plex_cert_apply(store, tls_resolved(prof), prof))
        return 0
    if a.acme_status:
        v = acme_view(store)
        if a.json:
            _print_json(v)
            return 0
        t = v['targets'][0]
        _kv([('ACME', 'on' if v['on'] else 'off'), ('CA', v['directory']),
             ('for', t.get('name') or '-'), ('how', v['challenge']),
             ('issued by', t.get('issuer') or '-'), ('until', stamp(t.get('not_after'))),
             ('renews it', 'yes' if t.get('care') else 'no'),
             ('last', ('failed: %s' % t['state'].get('said')) if t['state'].get('ok') is False
              else (stamp(t['state'].get('ordered_at')) if t['state'].get('ordered_at')
                    else 'never'))])
        return 0
    if a.acme_order:
        if not CONFIG.get('acme'):
            raise SystemExit('plexmon: ACME is off - plexmon --set acme=1 first')
        t = node_name()
        name = acme_name_of(store, t)
        acme_care_set(store, t, True, by, 'ordered on the command line')
        try:
            acme_order(store, t, name, by, lambda w: print('  ' + PAL.dim(w)))
        except AcmeError as e:
            print(PAL.bad('no certificate: %s' % e))
            return 1
        with WEB_LOCK:
            web_apply(store)
        print(PAL.good('the certificate for %s came and is in place' % name))
        return 0
    if a.acme_probe:
        r = acme_probe(store, node_name())
        print(('%s  %s' % (PAL.good('ok') if r['ok'] else PAL.bad('no'), r.get('said'))))
        return 0 if r['ok'] else 1
    if a.upgrade_check:
        row = online_look(store, by)
        if row.get('error'):
            print(PAL.bad('%s: %s' % (row['url'], row['error'])))
            return 1
        n = row.get('newest') or {}
        print('%s: newest %s, here %s%s' % (row['url'], n.get('version') or '-', __version__,
                                            PAL.good(' - newer') if row.get('newer') else ''))
        return 0
    if a.upgrade_online is not None:
        row = online_look(store, by)
        want = a.upgrade_online or ((row.get('newest') or {}).get('version') if row.get('newer')
                                    else '')
        if not want:
            print('nothing newer than %s at %s' % (__version__, online_url()))
            return 0
        if not a.yes and sys.stdin.isatty():
            if input('  upgrade to plexmon %s? (y/N) ' % want).strip().lower() not in ('y',
                                                                                      'yes'):
                return 1
        meta = online_fetch(store, want, by)
        print('plexmon %s fetched and checked (signed by %s)' % (meta['version'],
                                                                 meta.get('signed_by')))
        upgrade_start(store, by)
        UPGRADE_WAKE.set()
        print('the upgrade has begun - the service puts it in within seconds')
        return cli_upgrade_follow(store)
    if a.upgrade_file:
        info = upgrade_inspect(a.upgrade_file)
        if info.get('older'):
            raise SystemExit('plexmon: %s is older than %s - plexmon --downgrade FILE'
                             % (info['version'], __version__))
        upgrade_store_package(store, a.upgrade_file, info, by)
        upgrade_start(store, by)
        return cli_upgrade_follow(store)
    if a.upgrade_status:
        v = upgrade_view(store)
        if a.json:
            _print_json(v)
            return 0
        p = v['plan']
        print('plexmon %s; package: %s; upgrade: %s' % (
            __version__, (v['package'] or {}).get('version') or '-',
            ('%s to %s - %s' % (p['state'], p['version'], p['steps'][0].get('said') or ''))
            if p else 'none'))
        return 0
    if a.upgrade_follow:
        return cli_upgrade_follow(store)
    if a.upgrade_cancel:
        upgrade_cancel(store, by)
        print('stopped')
        return 0
    if a.downgrade_check:
        g = downgrade_list(store)
        print('here %s; it may go back to %s or newer' % (g['here'], g['lowest']))
        for v in g['versions']:
            print('  %-10s %s' % (v['version'], PAL.good('yes') if not v['why'] else
                                  PAL.bad('no: ' + '; '.join(v['why']))))
        return 0
    if a.downgrade:
        if os.path.exists(a.downgrade):
            info = upgrade_inspect(a.downgrade)
            why = downgrade_why_not(store, info['version'])
            if why:
                raise SystemExit('plexmon: %s' % '; '.join(why))
            upgrade_store_package(store, a.downgrade, info, by, downgrade=True)
        else:
            online_fetch(store, a.downgrade, by, older=True)
        upgrade_start(store, by)
        return cli_upgrade_follow(store)
    if a.console_at_login:
        if a.console_at_login != 'status':
            console_login_set(a.console_at_login == 'on', 'root')
            store.add_event('settings', 'root switched the console at login %s'
                            % a.console_at_login, who='root')
        on = console_login_state()
        print('the console at login is %s' % ('on' if on else 'off'))
        return 0 if on or a.console_at_login != 'status' else 3
    return cli_status(store, a.json)


def _bare(store):
    adm = Admin.__new__(Admin)
    adm.store = store
    return adm


ADMIN_FLAGS = ('--status', '--version', '--events', '--log', '--users', '--add-user',
               '--passwd', '--set-role', '--del-user', '--disable-user', '--enable-user',
               '--reset-2fa', '--sessions', '--settings', '--set', '--unset', '--cert-status',
               '--cert-import', '--cert-selfsigned', '--tls-apply', '--nginx-adopt',
               '--plex-cert', '--acme-status', '--acme-order', '--acme-probe', '--upgrade-check',
               '--upgrade-online', '--upgrade-file', '--upgrade-status', '--upgrade-follow',
               '--upgrade-cancel', '--downgrade-check', '--downgrade', '--console',
               '--console-at-login', '--admin-only', '--json')


def wants_admin(argv):
    return any(a.split('=')[0] in ADMIN_FLAGS for a in argv)


# ---------------------------------------------------------------------------
# The console: plexmon run by its menus, on the server's own screen (as otp's)
# ---------------------------------------------------------------------------
# `plexmon --console` - and, with `plexmon --console-at-login on`, what root sees
# first after signing in at the console or over SSH. Every action is the
# `plexmon ...` command its window names, run as it would be typed; q leaves
# it for the shell, ! opens a shell and comes back.
class ConsoleBack(Exception):
    pass


def plexmon_argv():
    me = Path(__file__).resolve().parent / 'plexmon.py'
    if me.exists():
        return [sys.executable, str(me)]
    return [sys.executable, str(Path(__file__).resolve())]


class Console(object):
    HELP = ' Up/Down move   Enter choose   Esc back   ! shell   q leave '

    def __init__(self, scr):
        import curses
        self.c = curses
        self.scr = scr
        self.st = {}
        self.st_at = 0.0
        curses.curs_set(0)
        try:
            curses.start_color()
            curses.use_default_colors()
            for i, col in enumerate((curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_RED,
                                     curses.COLOR_YELLOW, curses.COLOR_WHITE), 1):
                curses.init_pair(i, col, -1)
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)
        except curses.error:
            pass
        scr.keypad(True)

    # ---- drawing ------------------------------------------------------------------
    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        try:
            self.scr.addnstr(y, x, str(text), max(0, w - x - 1), attr)
        except self.c.error:
            pass

    def status(self):
        if time.time() - self.st_at > 20:
            self.st_at = time.time()
            try:
                store = STORE.get('store') or open_store()
                cert = cert_now(nginx_conf_cert_paths()[0]) or {}
                left = int((cert['not_after'] - time.time()) / 86400) \
                    if cert.get('not_after') else None
                up = online_view(store).get('online') or {}
                self.st = {'active': service_active(), 'cn': cert.get('cn') or '-',
                           'left': left, 'acme': bool(CONFIG.get('acme')),
                           'newer': (up.get('newest') or {}).get('version') if up.get('newer')
                           else '', 'accounts': store.counts()['accounts'],
                           'adopted': nginx_adopted()}
            except Exception as e:                      # noqa: BLE001
                self.st = {'active': '?', 'error': str(e)}
        return self.st

    def frame(self, title):
        c = self.c
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        s = self.status()
        bar = ' plexmon %s  %s  service %s | certificate %s%s | ACME %s | %s account(s)%s ' % (
            __version__, node_name(), s.get('active'), s.get('cn'),
            (' (%d days)' % s['left']) if s.get('left') is not None else '',
            'on' if s.get('acme') else 'off', s.get('accounts', '?'),
            (' | %s is out' % s['newer']) if s.get('newer') else '')
        self.put(0, 0, bar.ljust(w), c.color_pair(7) | c.A_BOLD)
        y = 1
        if s.get('active') not in ('active', '?'):
            self.put(y, 1, 'plexmon.service is %s: systemctl status plexmon' % s.get('active'),
                     c.color_pair(3) | c.A_BOLD)
            y += 1
        if s.get('left') is not None and s['left'] < 14:
            self.put(y, 1, 'the certificate runs out in %d days' % s['left'], c.color_pair(4))
            y += 1
        if s.get('adopted') is False:
            self.put(y, 1, 'nginx: %s is not plexmon\'s (install.sh --upgrade adopts it)'
                     % NGINX_SITE, c.color_pair(4))
            y += 1
        self.put(y + 1, 2, title, c.A_BOLD)
        self.put(h - 1, 0, self.HELP.ljust(w), c.color_pair(6))
        return y + 3

    def menu(self, title, items, at=0):
        """A list to choose from: the index chosen, or raises ConsoleBack."""
        c = self.c
        typed = ''
        while True:
            top = self.frame(title)
            h, _w = self.scr.getmaxyx()
            room = max(1, h - top - 2)
            first = max(0, at - room + 1)
            for i, words in enumerate(items[first:first + room]):
                n = first + i
                attr = c.A_REVERSE if n == at else 0
                self.put(top + i, 3, ' %2d  %s ' % (n + 1, words), attr)
            self.scr.refresh()
            self.scr.timeout(20000)
            k = self.scr.getch()
            if k == -1:
                continue
            if k in (c.KEY_UP, ord('k')):
                at = (at - 1) % len(items)
            elif k in (c.KEY_DOWN, ord('j'), 9):
                at = (at + 1) % len(items)
            elif k in (c.KEY_HOME,):
                at = 0
            elif k in (c.KEY_END,):
                at = len(items) - 1
            elif k in (c.KEY_ENTER, 10, 13, c.KEY_RIGHT):
                return at
            elif k in (27, c.KEY_LEFT, c.KEY_BACKSPACE, 127):
                raise ConsoleBack()
            elif k == ord('q'):
                raise SystemExit(0)
            elif k == ord('!'):
                self.shell()
            elif ord('0') <= k <= ord('9'):
                typed = (typed + chr(k))[-2:]
                n = int(typed)
                if 1 <= n <= len(items):
                    at = n - 1
                elif 1 <= int(chr(k)) <= len(items):
                    typed = chr(k)
                    at = int(chr(k)) - 1
            if k == c.KEY_RESIZE:
                self.scr.clear()

    def page(self, title, lines):
        """A long answer on a screen of its own, to scroll."""
        c = self.c
        top_line = 0
        while True:
            top = self.frame(title)
            h, w = self.scr.getmaxyx()
            room = max(1, h - top - 2)
            for i, ln in enumerate(lines[top_line:top_line + room]):
                attr = 0
                low = ln.lower()
                if any(x in low for x in ('fail', 'refused', 'error', ' no ', 'not right')):
                    attr = c.color_pair(3)
                elif any(x in low for x in (' ok', 'done', 'is made', 'in place', 'came')):
                    attr = c.color_pair(2)
                self.put(top + i, 2, ln.replace('\t', '    '), attr)
            if len(lines) > room:
                pos = int((h - top - 3) * top_line / max(1, len(lines) - room))
                self.put(top + pos, w - 2, '█', c.color_pair(1))
            self.scr.refresh()
            self.scr.timeout(-1)
            k = self.scr.getch()
            if k in (c.KEY_UP, ord('k')):
                top_line = max(0, top_line - 1)
            elif k in (c.KEY_DOWN, ord('j')):
                top_line = min(max(0, len(lines) - room), top_line + 1)
            elif k == c.KEY_PPAGE:
                top_line = max(0, top_line - room)
            elif k == c.KEY_NPAGE:
                top_line = min(max(0, len(lines) - room), top_line + room)
            elif k == c.KEY_HOME:
                top_line = 0
            elif k == c.KEY_END:
                top_line = max(0, len(lines) - room)
            elif k in (27, 10, 13, ord('q'), c.KEY_LEFT, c.KEY_ENTER):
                return

    def form(self, title, fields):
        """A window over the menu: [{'label', 'value', 'secret', 'choices', 'need'}].
        Returns the values, or raises ConsoleBack."""
        c = self.c
        vals = [str(f.get('value') or '') for f in fields]
        at = 0
        said = ''
        c.curs_set(1)
        try:
            while True:
                top = self.frame(title)
                for i, f in enumerate(fields):
                    y = top + i * 2
                    self.put(y, 3, f['label'], c.A_BOLD if i == at else 0)
                    shown = ('*' * len(vals[i])) if f.get('secret') else vals[i]
                    if f.get('choices'):
                        shown = '< %s >' % vals[i]
                    self.put(y + 1, 5, shown + ' ', c.A_UNDERLINE if i == at else 0)
                if fields[at].get('help'):
                    self.put(top + len(fields) * 2 + 1, 3, fields[at]['help'], c.color_pair(1))
                if said:
                    self.put(top + len(fields) * 2 + 2, 3, said, c.color_pair(3))
                self.put(top + len(fields) * 2 + 3, 3, 'Enter: go on   Esc: back', c.A_DIM)
                y = top + at * 2 + 1
                x = 5 + (len(vals[at]) + 2 if fields[at].get('choices') else len(vals[at]))
                try:
                    self.scr.move(y, min(x, self.scr.getmaxyx()[1] - 2))
                except c.error:
                    pass
                self.scr.refresh()
                self.scr.timeout(-1)
                k = self.scr.get_wch()
                f = fields[at]
                if k in ('\x1b', 27):
                    raise ConsoleBack()
                if k in (c.KEY_UP,):
                    at = (at - 1) % len(fields)
                elif k in (c.KEY_DOWN, '\t'):
                    at = (at + 1) % len(fields)
                elif k in ('\n', '\r', c.KEY_ENTER):
                    bad = [fl['label'] for fl, v in zip(fields, vals) if fl.get('need') and not v]
                    if bad:
                        said = 'needed: %s' % ', '.join(bad)
                        continue
                    return vals
                elif f.get('choices') and k in (c.KEY_LEFT, c.KEY_RIGHT, ' '):
                    ch = f['choices']
                    i = ch.index(vals[at]) if vals[at] in ch else 0
                    vals[at] = ch[(i + (1 if k != c.KEY_LEFT else -1)) % len(ch)]
                elif not f.get('choices'):
                    if k in (c.KEY_BACKSPACE, '\x7f', '\b', 127):
                        vals[at] = vals[at][:-1]
                    elif k == '\x15':
                        vals[at] = ''
                    elif isinstance(k, str) and k.isprintable():
                        vals[at] += k
        finally:
            c.curs_set(0)

    def yes(self, words):
        try:
            return self.menu(words, ['No', 'Yes']) == 1
        except ConsoleBack:
            return False

    def run(self, args, title=None):
        """`plexmon ARGS`, its output on a page of its own."""
        argv = plexmon_argv() + [str(x) for x in args] + ['--no-color']
        try:
            got = subprocess.run(argv, capture_output=True, text=True, timeout=1800,
                                 env=dict(os.environ, PLEXMON_IN_CONSOLE='1'))
            lines = (got.stdout + got.stderr).splitlines() or ['(nothing said)']
        except subprocess.TimeoutExpired:
            lines = ['it did not end in 30 minutes']
        self.page(title or '$ plexmon ' + ' '.join(str(x) for x in args), lines)

    def run_live(self, args, title=None, stdin=None):
        """A command that takes a while, its output shown as it comes."""
        self.c.endwin()
        print('\n$ plexmon ' + ' '.join(str(x) for x in args))
        try:
            subprocess.run(plexmon_argv() + [str(x) for x in args], input=stdin, text=True,
                           env=dict(os.environ, PLEXMON_IN_CONSOLE='1'))
        except KeyboardInterrupt:
            pass
        input('\n  Enter - back to the menu ')
        self.scr.clear()

    def shell(self):
        self.c.endwin()
        print('\nA shell - exit comes back to the plexmon console.\n')
        subprocess.call([os.environ.get('SHELL', '/bin/bash')],
                        env=dict(os.environ, PLEXMON_IN_CONSOLE='1'))
        self.scr.clear()

    def live_log(self, path, title):
        """The newest line at the bottom, followed as it comes; what is typed
        filters it (as grep -i). Esc goes back."""
        c = self.c
        lines, pos, grep = [], 0, ''
        while True:
            try:
                with open(path, 'rb') as f:
                    f.seek(0, 2)
                    size = f.tell()
                    if size < pos:
                        pos = 0
                    f.seek(max(pos, size - 2 * 1024 * 1024))
                    lines += f.read().decode('utf-8', 'replace').splitlines()
                    pos = size
            except OSError:
                lines = ['%s cannot be read' % path]
            lines = lines[-20000:]
            top = self.frame('%s   following%s' % (title, ('   grep: %s' % grep) if grep else ''))
            h, w = self.scr.getmaxyx()
            room = max(1, h - top - 2)
            shown = [ln for ln in lines if not grep or grep.lower() in ln.lower()][-room:]
            for i, ln in enumerate(shown):
                attr = c.color_pair(3) if ' error ' in ln or ' bad ' in ln else (
                    c.color_pair(4) if ' warn ' in ln else 0)
                self.put(top + i, 1, ln, attr)
            self.scr.refresh()
            self.scr.timeout(1000)
            try:
                k = self.scr.get_wch()
            except c.error:
                continue
            if k in ('\x1b', 27):
                if grep:
                    grep = ''
                    continue
                return
            if k in (c.KEY_BACKSPACE, '\x7f', 127):
                grep = grep[:-1]
            elif isinstance(k, str) and k.isprintable():
                grep += k

    # ---- the menus ------------------------------------------------------------------
    def main(self):
        items = [('Status and checks', self.m_status), ('Live log', self.m_log),
                 ('Accounts of Settings', self.m_accounts), ('Settings', self.m_settings),
                 ('Certificates and TLS', self.m_certs), ('Automatic certificates (ACME)',
                                                          self.m_acme),
                 ('Upgrades', self.m_upgrades), ('The service', self.m_service),
                 ('The disks and the dashboard', self.m_disks),
                 ('The console at login', self.m_login)]
        at = 0
        while True:
            try:
                at = self.menu('Main menu', [w for w, _ in items], at)
            except ConsoleBack:
                continue
            try:
                items[at][1]()
            except ConsoleBack:
                pass

    def m_status(self):
        while True:
            i = self.menu('Status and checks', ['Settings in one screen (plexmon --status)',
                                                'The dashboard\'s daemon (plexmon --check)',
                                                'Latency and the disks (plexmon --diag)',
                                                'Events (plexmon --events)'])
            self.run([['--status'], ['--check'], ['--diag'], ['--events', '200']][i])

    def m_log(self):
        i = self.menu('Live log', ['plexmon.log (Settings)', 'The journal of plexmon.service'])
        if i == 0:
            self.live_log(str(LOG_FILE), 'plexmon.log')
        else:
            self.c.endwin()
            try:
                subprocess.call(['journalctl', '-u', SERVICE, '-f', '-n', '200'])
            except KeyboardInterrupt:
                pass
            self.scr.clear()

    def m_accounts(self):
        while True:
            i = self.menu('Accounts of Settings', ['The accounts', 'Make an account',
                                                   'A new password', 'Change a role',
                                                   'Switch one off', 'Switch one on',
                                                   'Reset the second step', 'Remove one',
                                                   'Who is signed in'])
            if i == 0:
                self.run(['--users'])
            elif i == 1:
                v = self.form('Make an account (plexmon --add-user)', [
                    {'label': 'Name', 'need': True},
                    {'label': 'Role', 'choices': ['fulladmin', 'readonly'], 'value': 'fulladmin'},
                    {'label': 'Password (8 characters at least)', 'secret': True, 'need': True},
                    {'label': 'Again', 'secret': True, 'need': True}])
                if v[2] != v[3]:
                    self.page('Make an account', ['the two passwords are not the same'])
                    continue
                self._run_stdin(['--add-user', v[0], '--role', v[1]], v[2] + '\n')
            elif i == 2:
                v = self.form('A new password (plexmon --passwd)', [
                    {'label': 'Name', 'need': True},
                    {'label': 'Password', 'secret': True, 'need': True},
                    {'label': 'Again', 'secret': True, 'need': True}])
                if v[1] != v[2]:
                    self.page('A new password', ['the two passwords are not the same'])
                    continue
                self._run_stdin(['--passwd', v[0]], v[1] + '\n')
            elif i == 3:
                v = self.form('Change a role (plexmon --set-role)', [
                    {'label': 'Name', 'need': True},
                    {'label': 'Role', 'choices': ['fulladmin', 'readonly'], 'value': 'readonly'}])
                self.run(['--set-role', v[0], v[1]])
            elif i in (4, 5, 6, 7):
                v = self.form(['Switch one off', 'Switch one on', 'Reset the second step',
                               'Remove one'][i - 4], [{'label': 'Name', 'need': True}])
                flag = ['--disable-user', '--enable-user', '--reset-2fa', '--del-user'][i - 4]
                if i in (4, 7) and not self.yes('%s %s?' % (flag[2:], v[0])):
                    continue
                self.run([flag, v[0]])
            else:
                self.run(['--sessions'])

    def _run_stdin(self, args, text):
        argv = plexmon_argv() + args + ['--no-color']
        got = subprocess.run(argv, input=text, capture_output=True, text=True, timeout=120)
        self.page('$ plexmon ' + ' '.join(args), (got.stdout + got.stderr).splitlines())

    def m_settings(self):
        while True:
            i = self.menu('Settings', ['Every setting (plexmon --settings)',
                                       'Change one by its key (plexmon --set KEY=VALUE)',
                                       'Put one back as it comes (plexmon --unset KEY)'])
            if i == 0:
                self.run(['--settings'])
            elif i == 1:
                v = self.form('Change a setting', [
                    {'label': 'Key', 'need': True, 'help': ', '.join(sorted(DEFAULTS))[:200]},
                    {'label': 'Value'}])
                self.run(['--set', '%s=%s' % (v[0], v[1])])
            else:
                v = self.form('Put a setting back', [{'label': 'Key', 'need': True}])
                self.run(['--unset', v[0]])

    def m_certs(self):
        while True:
            i = self.menu('Certificates and TLS', [
                'The certificate (plexmon --cert-status)',
                'Put one in from PEM files (plexmon --cert-import)',
                'A self-signed one (plexmon --cert-selfsigned)',
                'Put in again what is kept (plexmon --tls-apply)',
                'Plex Media Server takes it too (plexmon --plex-cert on)',
                'Plex Media Server back to its own (plexmon --plex-cert off)',
                'Hand the Plex vhost over to plexmon (plexmon --nginx-adopt)'])
            if i == 0:
                self.run(['--cert-status'])
            elif i == 1:
                v = self.form('Put a certificate in', [
                    {'label': 'The certificate (PEM file)', 'need': True},
                    {'label': 'Its key (PEM file)', 'need': True},
                    {'label': 'The chain (PEM file, may be empty)'}])
                self.run(['--cert-import'] + [x for x in v if x])
            elif i == 2:
                v = self.form('A self-signed certificate', [
                    {'label': 'Its name (CN)', 'need': True, 'value': socket.getfqdn()}])
                if self.yes('Replace the certificate nginx answers with?'):
                    self.run(['--cert-selfsigned', v[0]])
            elif i == 3:
                self.run(['--tls-apply'])
            elif i == 4:
                if self.yes('Plex Media Server is restarted to take it - go on?'):
                    self.run(['--plex-cert', 'on'])
            elif i == 5:
                if self.yes('Plex Media Server is restarted - go on?'):
                    self.run(['--plex-cert', 'off'])
            else:
                self.run(['--nginx-adopt'])

    def m_acme(self):
        while True:
            i = self.menu('Automatic certificates (ACME)', [
                'How it is (plexmon --acme-status)', 'Try port 80 as the CA will',
                'Order it now (plexmon --acme-order)', 'Switch ACME on', 'Switch ACME off',
                'The name of the certificate'])
            if i == 0:
                self.run(['--acme-status'])
            elif i == 1:
                self.run(['--acme-probe'])
            elif i == 2:
                self.run_live(['--acme-order'])
            elif i == 3:
                self.run(['--set', 'acme=1'])
            elif i == 4:
                self.run(['--set', 'acme=0'])
            else:
                v = self.form('The name of the certificate', [
                    {'label': 'DNS name', 'value': CONFIG.get('acme_node_name') or ''}])
                self.run(['--set', 'acme_node_name=%s' % v[0]])

    def m_upgrades(self):
        while True:
            i = self.menu('Upgrades', ['What is at the address (plexmon --upgrade-check)',
                                       'Upgrade from the web (plexmon --upgrade-online)',
                                       'Upgrade from a package file',
                                       'How the last one went (plexmon --upgrade-status)',
                                       'Go back to an older version (only a safe one)'])
            if i == 0:
                self.run(['--upgrade-check'])
            elif i == 1:
                if self.yes('Fetch the newest, check it and put it in?'):
                    self.run_live(['--upgrade-online', '--yes'])
            elif i == 2:
                v = self.form('Upgrade from a file', [{'label': 'plexmon-X.Y.Z.tar.gz',
                                                       'need': True}])
                self.run_live(['--upgrade-file', v[0]])
            elif i == 3:
                self.run(['--upgrade-status'])
            else:
                self.run(['--downgrade-check'])
                v = self.form('Go back to', [{'label': 'Version', 'need': True}])
                if self.yes('Go back to %s?' % v[0]):
                    self.run_live(['--downgrade', v[0]])

    def m_service(self):
        while True:
            i = self.menu('The service', ['systemctl status plexmon', 'Restart plexmon',
                                          'Restart nginx', 'Restart Plex Media Server'])
            cmds = [['systemctl', 'status', '--no-pager', SERVICE],
                    ['systemctl', 'restart', SERVICE], ['systemctl', 'restart', 'nginx'],
                    ['systemctl', 'restart', PLEX_SERVICE]]
            if i and not self.yes('%s?' % ' '.join(cmds[i])):
                continue
            got = subprocess.run(cmds[i], capture_output=True, text=True, timeout=300)
            self.page('$ ' + ' '.join(cmds[i]), ((got.stdout + got.stderr).splitlines()
                                                 or ['done']))

    def m_disks(self):
        while True:
            i = self.menu('The disks and the dashboard', [
                'Wake the disks and read SMART (plexmon --wake)',
                'Read SMART from the spinning ones (plexmon --refresh)',
                'The televisions (plexmon --atv)'])
            self.run([['--wake'], ['--refresh'], ['--atv']][i])

    def m_login(self):
        on = console_login_state()
        i = self.menu('The console at login - now %s' % ('on' if on else 'off'),
                      ['On: root lands in this console', 'Off: root lands in the shell'])
        self.run(['--console-at-login', 'on' if i == 0 else 'off'])


def console_main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('plexmon --console needs a terminal')
        return 2
    import curses
    os.environ.setdefault('ESCDELAY', '25')
    try:
        curses.wrapper(lambda scr: Console(scr).main())
    except (SystemExit, KeyboardInterrupt):
        pass
    return 0


if __name__ == '__main__':
    sys.exit(cli_main(sys.argv[1:]))
