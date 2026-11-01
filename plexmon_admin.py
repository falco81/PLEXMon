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
import collections
import getpass
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
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import sqlite3

__version__ = '2.6.2'
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
NGINX_PHP_GUARD = NGINX_SNIPPETS / 'php-guard.conf'
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
    'acme_key': 'dual',
    # Settings → Proxy (2.4.0, as otp's)
    'proxy': 0,
    'proxy_url': '',
    'proxy_user': '',
    'proxy_password': '',
    'proxy_no': 'localhost, 127.0.0.1, ::1',
    # What goes through it - each one by choice, none by default (2.4.2).
    'proxy_upgrade': 0,
    'proxy_os': 0,
    'proxy_acme': 0,
    'proxy_dnsapi': 0,
    'proxy_discover': 0,
    'proxy_ratings': 0,
    'acme_account_key': 'ec',
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
    # --- the Plex library (plex_tools) --------------------------------------------
    'plex_title_lang': 'en',
    'plex_tmdb_key': '',
    'plex_omdb_key': '',
    'plex_trakt_id': '',
    'plex_tvmaze': 1,
    # --- the database (2.2.0) -------------------------------------------------------
    'backup_daily': 1,
    'backup_keep': 14,
    'backup_at': '03:30',
    'plex_db_daily': 0,
    'plex_db_every': 'daily',
    'plex_db_day': '7',
    'plex_db_at': '03:45',
    'plex_db_keep': 7,
    'plex_db_check': 1,
    'plex_db_dir': '',
    # --- backups to another machine ---------------------------------------------------
    'rbackup': 0,
    'rbackup_at': '04:15',
    'rbackup_every': 'daily',
    'rbackup_day': 7,
    'rbackup_keep': 30,
    'rbackup_keep_days': 0,
    'rbackup_password': '',
    'rbackup_verify': 'off',
    'rbackup_plex_db': 1,
    # --- system updates ----------------------------------------------------------------
    'osup_check': 0,
    'osup_check_at': '06:15',
    'osup_auto': 0,
    'osup_auto_what': 'security',
    'osup_auto_every': 'weekly',
    'osup_auto_day': '7',
    'osup_auto_at': '04:45',
    'osup_auto_reboot': 'auto',
    'osup_plex': 0,
    # --- restart and power off ------------------------------------------------------------
    'power_auto': 0,
    'power_auto_every': 'weekly',
    'power_auto_day': '7',
    'power_auto_at': '05:40',
    'power_auto_needed': 1,
}

FILE_ONLY_KEYS = ()
SECRET_KEYS = ('acme_dns_tsig_secret', 'acme_dns_token', 'acme_dns_key', 'acme_desec_token',
               'plex_tmdb_key', 'plex_omdb_key', 'plex_trakt_id', 'rbackup_password',
               'proxy_password')
SEALED_KEYS = SECRET_KEYS

TUNABLE = {
    'session_hours': (1, 168), 'session_idle_min': (5, 1440),
    'login_tries': (1, 100), 'login_wait_min': (1, 1440),
    'admin_2fa': (0, 1), 'admin_2fa_app': (0, 1),
    'proxy': (0, 1), 'proxy_upgrade': (0, 1), 'proxy_os': (0, 1), 'proxy_acme': (0, 1),
    'proxy_dnsapi': (0, 1), 'proxy_discover': (0, 1), 'proxy_ratings': (0, 1),
    'plex_cert': (0, 1), 'plex_cert_restart': (0, 1),
    'upgrade_check': (0, 1), 'upgrade_downgrade': (0, 1),
    'acme': (0, 1), 'acme_ca': ('letsencrypt', 'letsencrypt-staging', 'custom'),
    'acme_days': (7, 60), 'acme_key': ('dual', 'dual-strong', 'ec', 'ec384', 'rsa2048', 'rsa3072', 'rsa4096'),
    'acme_account_key': ('ec', 'ec384', 'rsa2048', 'rsa3072', 'rsa4096'),
    'acme_challenge': ('http-01', 'dns-01'),
    'acme_dns': ('manual', 'rfc2136', 'cloudflare', 'acmedns', 'desec'),
    'acme_dns_tsig_alg': ('hmac-sha256', 'hmac-sha512', 'hmac-sha384', 'hmac-sha224', 'hmac-sha1',
                          'hmac-md5'),
    'acme_dns_wait': (0, 600),
    'ui_lang': ('cs', 'en'), 'api_port': (1024, 65535), 'debug_api': (0, 1),
    'retain_events_days': (0, 36500),
    'backup_daily': (0, 1), 'backup_keep': (1, 365),
    'plex_db_daily': (0, 1), 'plex_db_every': ('daily', 'weekly'), 'plex_db_day': ('1', '2', '3', '4', '5', '6', '7'),
    'plex_db_keep': (1, 100), 'plex_db_check': (0, 1),
    'rbackup': (0, 1), 'rbackup_every': ('daily', 'weekly'), 'rbackup_day': (1, 7),
    'rbackup_keep': (1, 1000), 'rbackup_keep_days': (0, 3650),
    'rbackup_verify': ('off', 'after', 'weekly'), 'rbackup_plex_db': (0, 1),
    'osup_check': (0, 1), 'osup_auto': (0, 1), 'osup_auto_what': ('security', 'all'),
    'osup_auto_every': ('daily', 'weekly', 'monthly'), 'osup_auto_day': ('1', '2', '3', '4', '5', '6', '7'),
    'osup_auto_reboot': ('ask', 'auto'), 'osup_plex': (0, 1),
    'power_auto': (0, 1), 'power_auto_every': ('weekly', 'monthly'), 'power_auto_day': ('1', '2', '3', '4', '5', '6', '7'),
    'power_auto_needed': (0, 1),
    'plex_tvmaze': (0, 1),
    'plex_title_lang': ('en', 'cs', 'ar', 'bg', 'ca', 'zh', 'hr', 'da', 'nl', 'et', 'fi', 'fr',
                        'de', 'el', 'he', 'hi', 'hu', 'id', 'it', 'ja', 'ko', 'lv', 'lt', 'ms',
                        'nb', 'fa', 'pl', 'pt', 'pt-BR', 'ro', 'ru', 'sr', 'sk', 'sl', 'es',
                        'es-MX', 'sv', 'th', 'tr', 'uk', 'vi'),
}

CHOICE_WORDS = {
    'rbackup_verify': {'off': 'only by hand', 'after': 'after every backup, the one just sent',
                       'weekly': 'once a week, the newest one'},
    'osup_auto_what': {'security': 'only the security updates', 'all': 'every update'},
    'osup_auto_every': {'daily': 'every day', 'weekly': 'every week, on the day below',
                        'monthly': 'on the first of every month'},
    'osup_auto_day': {'1': 'Monday', '2': 'Tuesday', '3': 'Wednesday', '4': 'Thursday', '5': 'Friday', '6': 'Saturday', '7': 'Sunday'},
    'osup_auto_reboot': {'auto': 'restarted by itself where an update needs it',
                         'ask': 'a restart offered on the page'},
    'power_auto_every': {'weekly': 'every week, on the day below',
                         'monthly': 'on the first of every month'},
    'power_auto_day': {'1': 'Monday', '2': 'Tuesday', '3': 'Wednesday', '4': 'Thursday', '5': 'Friday', '6': 'Saturday', '7': 'Sunday'},
    'plex_db_every': {'daily': 'every day', 'weekly': 'every week, on the day below'},
    'plex_db_day': {'1': 'Monday', '2': 'Tuesday', '3': 'Wednesday', '4': 'Thursday', '5': 'Friday', '6': 'Saturday', '7': 'Sunday'},
    'rbackup_every': {'daily': 'every day', 'weekly': 'every week, on the day below'},
    'plex_title_lang': {'en': 'English', 'cs': 'Czech', 'ar': 'Arabic', 'bg': 'Bulgarian',
                        'ca': 'Catalan', 'zh': 'Chinese', 'hr': 'Croatian', 'da': 'Danish',
                        'nl': 'Dutch', 'et': 'Estonian', 'fi': 'Finnish', 'fr': 'French',
                        'de': 'German', 'el': 'Greek', 'he': 'Hebrew', 'hi': 'Hindi',
                        'hu': 'Hungarian', 'id': 'Indonesian', 'it': 'Italian',
                        'ja': 'Japanese', 'ko': 'Korean', 'lv': 'Latvian', 'lt': 'Lithuanian',
                        'ms': 'Malay', 'nb': 'Norwegian', 'fa': 'Persian', 'pl': 'Polish',
                        'pt': 'Portuguese', 'pt-BR': 'Portuguese (Brazil)', 'ro': 'Romanian',
                        'ru': 'Russian', 'sr': 'Serbian', 'sk': 'Slovak', 'sl': 'Slovenian',
                        'es': 'Spanish', 'es-MX': 'Spanish (Mexico)', 'sv': 'Swedish',
                        'th': 'Thai', 'tr': 'Turkish', 'uk': 'Ukrainian', 'vi': 'Vietnamese'},
    'acme_challenge': {'http-01': 'HTTP-01 - the CA asks port 80 of the name',
                       'dns-01': 'DNS-01 - a TXT record in DNS (no port open to the internet)'},
    'acme_dns': {'manual': 'by hand - the record is shown here, you put it in',
                 'rfc2136': 'RFC 2136 dynamic update with a TSIG key (BIND, PowerDNS, Knot...)',
                 'cloudflare': 'Cloudflare, with an API token', 'acmedns': 'an acme-dns server',
                 'desec': 'deSEC.io (free), with an API token - a CNAME in your DNS'},
    'acme_ca': {'letsencrypt': 'Let’s Encrypt',
                'letsencrypt-staging': 'Let’s Encrypt - staging (to try it)',
                'custom': 'another ACME CA (step-ca, an internal CA...)'},
    'acme_key': {'dual': 'Both: RSA 2048 + ECDSA P-256 (advised)',
                 'dual-strong': 'Both: RSA 4096 + ECDSA P-384',
                 'ec': 'ECDSA P-256', 'ec384': 'ECDSA P-384',
                 'rsa2048': 'RSA 2048', 'rsa3072': 'RSA 3072', 'rsa4096': 'RSA 4096'},
    'acme_account_key': {'ec': 'ECDSA P-256 (ES256)', 'ec384': 'ECDSA P-384 (ES384)',
                         'rsa2048': 'RSA 2048 (RS256)', 'rsa3072': 'RSA 3072 (RS256)',
                         'rsa4096': 'RSA 4096 (RS256)'},
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
    'proxy': 'Go out through a proxy',
    'proxy_url': 'The proxy',
    'proxy_user': 'User name at the proxy',
    'proxy_password': 'Password at the proxy',
    'proxy_no': 'Straight, not through the proxy',
    'proxy_upgrade': 'New versions of plexmon',
    'proxy_os': 'System updates (dnf)',
    'proxy_acme': 'Certificates from the ACME CA',
    'proxy_dnsapi': 'The DNS API for DNS-01',
    'proxy_discover': 'Plex Discover and posters',
    'proxy_ratings': 'The rating databases',
    'acme_account_key': 'The key of the ACME account',
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
    'plex_title_lang': 'The language titles are put into',
    'plex_tmdb_key': 'TMDB API key',
    'plex_omdb_key': 'OMDb API key (IMDb, Rotten Tomatoes)',
    'plex_trakt_id': 'Trakt client id',
    'plex_tvmaze': 'TVmaze (no key needed)',
    'backup_daily': 'A copy of the database every day', 'backup_keep': 'Copies kept',
    'backup_at': 'At',
    'plex_db_daily': 'A copy of Plex Media Server\'s database on a schedule',
    'plex_db_every': 'How often', 'plex_db_day': 'On the day', 'plex_db_at': 'At',
    'plex_db_keep': 'Copies kept', 'plex_db_check': 'Check every copy',
    'plex_db_dir': 'Where the copies are kept',
    'rbackup': 'Backups to the destinations on a schedule', 'rbackup_at': 'At',
    'rbackup_every': 'How often', 'rbackup_day': 'On (1 Monday - 7 Sunday)',
    'rbackup_keep': 'Backups kept at each destination',
    'rbackup_keep_days': 'And none older than (days)',
    'rbackup_password': 'The password of the backups', 'rbackup_verify': 'Try the backups',
    'rbackup_plex_db': 'Plex Media Server\'s database in them too',
    'osup_check': 'Look for updates every day', 'osup_check_at': 'At',
    'osup_auto': 'Updates on a schedule', 'osup_auto_what': 'Which', 'osup_auto_every': 'How often',
    'osup_auto_day': 'On the day', 'osup_auto_at': 'At', 'osup_auto_reboot': 'After the update',
    'osup_plex': 'Plex Media Server\'s own package too',
    'power_auto': 'A restart on a schedule', 'power_auto_every': 'How often',
    'power_auto_day': 'On the day', 'power_auto_at': 'At',
    'power_auto_needed': 'Only when an update needs it',
}

SAYS = {
    'proxy': 'An HTTP proxy, used only by what is switched on below - each one by choice, '
             'none of them by default. Whatever is left off goes out exactly as without a '
             'proxy.',
    'proxy_url': '<code>http://proxy.example.cz:3128</code>',
    'proxy_user': 'Where the proxy asks for one. Empty: none.',
    'proxy_password': 'Kept sealed, like the other passwords.',
    'proxy_no': 'Names and addresses reached without it, separated by commas: '
                '<code>.example.cz</code> is every name under it, <code>10.0.0.0/8</code> a net.',
    'proxy_upgrade': 'Upgrades → From the web: the look for a newer plexmon and its download.',
    'proxy_os': 'dnf fetches the updates of System updates through it.',
    'proxy_acme': 'Automatic certificates: every request to Let’s Encrypt (or the CA set there) - '
                  'the account, the order, the certificate.',
    'proxy_dnsapi': 'DNS-01 by an API: deSEC, Cloudflare, acme-dns - the TXT record put in and '
                    'taken away. RFC 2136 (nsupdate) speaks DNS and never goes through it.',
    'proxy_discover': 'Plex library: the watchlist (discover.provider.plex.tv) and the posters '
                      'fetched from plex.tv and TMDB.',
    'proxy_ratings': 'Plex library → Ratings: TMDB, OMDb, Trakt and TVmaze.',
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
    'acme_key': 'Let’s Encrypt takes RSA keys of 2048, 3072 or 4096 bits and ECDSA keys on P-256 '
                'or P-384. Both - what Let’s Encrypt advises - orders two certificates for the '
                'name: RSA served by default, the much smaller ECDSA one to the clients that say '
                'they take it.',
    'acme_account_key': 'The key the account with the CA signs with - never one of a '
                        'certificate. RSA 2048, 3072, 4096 or ECDSA P-256, P-384. A change makes '
                        'a new account at the next order.',
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
    'plex_title_lang': 'Plex library → Titles: the language a title in another one is put '
                       'into - English unless you say otherwise.',
    'plex_tmdb_key': 'The ratings of movies, shows, seasons and episodes from The Movie '
                     'Database - a free key from <code>themoviedb.org/settings/api</code>.',
    'plex_omdb_key': 'IMDb and Rotten Tomatoes, read through OMDb - a free key from '
                     '<code>omdbapi.com/apikey.aspx</code> (1000 a day).',
    'plex_trakt_id': 'The client id of an API app at <code>trakt.tv/oauth/applications</code>.',
    'plex_tvmaze': 'The ratings of shows and episodes from TVmaze - no key, no account.',
    'backup_daily': 'A copy of plexmon\'s database once a day, into '
                    '<code>/var/lib/plexmon/backups</code>.',
    'backup_keep': 'How many of those copies are kept; the oldest goes first.',
    'backup_at': 'When, as <code>HH:MM</code> on this machine’s clock.',
    'plex_db_daily': 'A copy of Plex Media Server\'s library database taken while it runs '
                     '(SQLite\'s own online backup - Plex goes on, nothing in it is written), '
                     'packed and kept here. Plex makes its own every three days too '
                     '(Settings → Scheduled Tasks in Plex), and a backup timer of the server '
                     'may as well - both are listed below and left as they are.',
    'plex_db_every': 'Every day, or a week on the day below.',
    'plex_db_day': 'The day of a weekly copy.',
    'plex_db_at': 'When, as <code>HH:MM</code> - a quiet hour: the copy reads the whole '
                  'database.',
    'plex_db_keep': 'How many copies are kept; the oldest goes first.',
    'plex_db_check': 'Every copy read through (Plex\'s own SQLite when it is there, '
                     '<code>integrity_check</code>) - on the copy, never on Plex\'s own file.',
    'plex_db_dir': 'A folder on this machine (another disk, a mount). Empty: '
                   '<code>/var/lib/plexmon/plex-db</code>.',
    'rbackup': 'A backup made and sent to every destination switched on, at the time below. '
               'Without it, only Back up now sends one.',
    'rbackup_at': 'When, as <code>HH:MM</code> on this machine’s clock.',
    'rbackup_every': 'Daily, or weekly on the day below.',
    'rbackup_day': 'The day of a weekly backup: 1 is Monday, 7 Sunday.',
    'rbackup_keep': 'How many backups each destination keeps; the oldest go first, the newest '
                    'never.',
    'rbackup_keep_days': 'Older ones are taken away too - 0: only the count above decides.',
    'rbackup_password': 'Every backup is locked with it (AES-256, opened with plexmon or with '
                        'openssl alone). Keep it apart from the backups too - in a password '
                        'vault: without it nobody, you included, opens them. Without it no '
                        'backup is sent.',
    'rbackup_verify': 'The newest backup fetched back, opened with the password, every file '
                      'checked and the databases read through - nothing is put back.',
    'rbackup_plex_db': 'A copy of Plex Media Server\'s library database (and its '
                       'Preferences.xml) goes into every backup - taken while Plex runs. '
                       'Off: plexmon alone (much smaller).',
    'osup_check': 'dnf looks what it has waiting every day at the time below - for the card '
                  'here. Nothing is put in.',
    'osup_check_at': 'When, as <code>HH:MM</code> on this machine’s clock.',
    'osup_auto': 'The updates put in by themselves in the window below. Off until switched on.',
    'osup_auto_what': 'Only the security updates (dnf --security), or every update.',
    'osup_auto_every': 'A day, a week or a month.',
    'osup_auto_day': 'The day a weekly update goes.',
    'osup_auto_at': 'When, as <code>HH:MM</code> - a quiet hour, away from the backups.',
    'osup_auto_reboot': 'When an update needs a restart - made by itself, or offered on the '
                        'page.',
    'osup_plex': 'Off: an update of the system leaves <code>plexmediaserver</code> out - it '
                 'restarts Plex in the middle of a film. On: Plex is updated with the rest.',
    'power_auto': 'The server restarted in the window below. Off until switched on.',
    'power_auto_every': 'A week or a month.',
    'power_auto_day': 'The day a weekly restart goes.',
    'power_auto_at': 'When, as <code>HH:MM</code> on this machine’s clock.',
    'power_auto_needed': 'Only when an update left something only a restart brings in; off: '
                         'every time.',
}
SAYS_SHORT = {}

# Which settings sit on which page of Settings, in order.
PANES = {
    'proxy': ['proxy', 'proxy_url', 'proxy_user', 'proxy_password', 'proxy_no', 'proxy_upgrade',
              'proxy_os', 'proxy_acme', 'proxy_dnsapi', 'proxy_discover', 'proxy_ratings'],
    'people': ['session_hours', 'session_idle_min', 'login_tries', 'login_wait_min',
               'admin_2fa', 'admin_2fa_app'],
    'web': ['plex_cert', 'plex_cert_domain', 'plex_cert_restart'],
    'acme': ['acme', 'acme_email', 'acme_ca', 'acme_url', 'acme_ca_pem',
             'acme_node_name', 'acme_days', 'acme_key', 'acme_account_key', 'acme_challenge', 'acme_dns',
             'acme_dns_server', 'acme_dns_zone', 'acme_dns_tsig_name', 'acme_dns_tsig_alg',
             'acme_dns_tsig_secret', 'acme_dns_token', 'acme_dns_url', 'acme_dns_user',
             'acme_dns_key', 'acme_dns_subdomain', 'acme_desec_domain', 'acme_desec_token',
             'acme_dns_alias', 'acme_dns_wait', 'acme_resolvers'],
    'upgrade': ['upgrade_check', 'upgrade_url', 'upgrade_downgrade'],
    'about': ['site_name', 'ui_lang', 'api_port', 'debug', 'debug_api'],
    'plex': ['plex_title_lang', 'plex_tmdb_key', 'plex_omdb_key', 'plex_trakt_id', 'plex_tvmaze'],
    'db': ['backup_daily', 'backup_keep', 'backup_at', 'retain_events_days'],
    'plexdb': ['plex_db_daily', 'plex_db_every', 'plex_db_day', 'plex_db_at', 'plex_db_keep',
               'plex_db_check', 'plex_db_dir'],
    'backup': ['rbackup', 'rbackup_at', 'rbackup_every', 'rbackup_day', 'rbackup_keep',
               'rbackup_keep_days', 'rbackup_password', 'rbackup_verify', 'rbackup_plex_db'],
    'osup': ['osup_check', 'osup_check_at', 'osup_auto', 'osup_auto_what', 'osup_auto_every',
             'osup_auto_day', 'osup_auto_at', 'osup_auto_reboot', 'osup_plex'],
    'power': ['power_auto', 'power_auto_every', 'power_auto_day', 'power_auto_at',
              'power_auto_needed'],
}

ONLY_WHEN = {
    'proxy_url': ['proxy', '1'], 'proxy_user': ['proxy', '1'], 'proxy_password': ['proxy', '1'],
    'proxy_no': ['proxy', '1'], 'proxy_upgrade': ['proxy', '1'], 'proxy_os': ['proxy', '1'],
    'proxy_acme': ['proxy', '1'], 'proxy_dnsapi': ['proxy', '1'], 'proxy_discover': ['proxy', '1'],
    'proxy_ratings': ['proxy', '1'],
    'backup_keep': ['backup_daily', '1'], 'backup_at': ['backup_daily', '1'],
    'plex_db_every': ['plex_db_daily', '1'], 'plex_db_day': ['plex_db_every', 'weekly'],
    'plex_db_at': ['plex_db_daily', '1'],
    'rbackup_at': ['rbackup', '1'], 'rbackup_every': ['rbackup', '1'],
    'rbackup_day': ['rbackup_every', 'weekly'],
    'osup_check_at': ['osup_check', '1'], 'osup_auto_what': ['osup_auto', '1'],
    'osup_auto_every': ['osup_auto', '1'], 'osup_auto_day': ['osup_auto_every', 'weekly'],
    'osup_auto_at': ['osup_auto', '1'], 'osup_auto_reboot': ['osup_auto', '1'],
    'power_auto_every': ['power_auto', '1'], 'power_auto_day': ['power_auto_every', 'weekly'],
    'power_auto_at': ['power_auto', '1'], 'power_auto_needed': ['power_auto', '1'],
    'admin_2fa_app': ['admin_2fa', '1'],
    'plex_cert_domain': ['plex_cert', '1'], 'plex_cert_restart': ['plex_cert', '1'],
    'acme_email': ['acme', '1'], 'acme_ca': ['acme', '1'],
    'acme_url': ['acme_ca', 'custom'], 'acme_ca_pem': ['acme_ca', 'custom'],
    'acme_node_name': ['acme', '1'], 'acme_days': ['acme', '1'], 'acme_key': ['acme', '1'], 'acme_account_key': ['acme', '1'],
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
    if key == 'proxy_url' and str(got).strip() and \
            not re.match(r'^https?://[A-Za-z0-9.\-]+(?::\d{1,5})?/?\Z|^https?://\[[0-9a-fA-F:]+\]'
                         r'(?::\d{1,5})?/?\Z', str(got).strip()):
        raise BadSetting('proxy_url: http://host:port - the user name and the password go '
                         'in the fields below, not in the address')
    if key == 'proxy_user' and re.search(r'[\s:@/]', str(got)):
        raise BadSetting('proxy_user: no spaces, no : @ or /')
    if key == 'proxy_no' and str(got).strip() and \
            not re.match(r'^[A-Za-z0-9.*:/\[\]_-]+([\s,;]+[A-Za-z0-9.*:/\[\]_-]+)*[\s,;]*\Z',
                         str(got).strip()):
        raise BadSetting('proxy_no: names, .domains and addresses, separated by commas')
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
    if key in ('backup_at', 'plex_db_at', 'rbackup_at', 'osup_check_at', 'osup_auto_at',
               'power_auto_at') and not re.match(r'^([01]?\d|2[0-3]):[0-5]\d\Z', str(got).strip()):
        raise BadSetting('%s: a time of day as HH:MM, like 03:30' % key)
    if key == 'plex_db_dir' and str(got).strip() and (
            not str(got).startswith('/') or re.search(r'(^|/)\.\.(/|$)', str(got))
            or str(got).rstrip('/') in ('', '/etc', '/usr', '/boot', '/proc', '/sys', '/dev')):
        raise BadSetting('plex_db_dir: a whole path of a folder on this machine (/mnt/backup/plex)')
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
        if key == 'acme_key' and value == 'dual384':
            value = 'dual-strong'           # its name in 2.2.4-2.2.5 (RSA 4096 + P-384 now)
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


def secret_shown(value, last=True):
    """A secret as the page may see it: dots and its last two characters, so
    that "the one I set" can be told from "something else" (otp's). Only a
    full administrator is ever given this (everybody else gets nothing);
    `last=False` - dots only. Typed on the page, a secret shows dots alone."""
    said = str(value or '')
    if not said:
        return ''
    if not last:
        return '•' * 8
    return '•' * min(10, max(4, len(said) - 2)) + said[-2:] if len(said) > 4 \
        else '•' * 6


def is_masked(value):
    """What the page sent back as it was given it (dots and the last two):
    the secret kept stays kept, never replaced by its own picture."""
    return isinstance(value, str) and value.startswith('•')


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


def open_store(restore=False):
    load_config()
    if restore:
        # A copy waiting to be put back (Settings → The database / Backups):
        # put in place before the database is opened, by the daemon only.
        try:
            import plexmon_host
            if plexmon_host.apply_pending_restore(DB_FILE):
                log('a copy of the database was put back as plexmon started', 'warn')
        except Exception as e:                          # noqa: BLE001
            log('the copy waiting to be put back could not be: %s' % e, 'error')
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


# The keys Let's Encrypt takes for a certificate (2.2.4): RSA of 2048, 3072
# or 4096 bits, ECDSA on P-256 or P-384 (letsencrypt.org/docs/integration-guide).
EC_CURVES = {'ec': 'P-256', 'ec256': 'P-256', 'ec384': 'P-384'}


def new_key(kind):
    if kind in EC_CURVES:
        return _openssl(['genpkey', '-algorithm', 'EC', '-pkeyopt',
                         'ec_paramgen_curve:%s' % EC_CURVES[kind]]).decode()
    bits = {'rsa2048': 2048, 'rsa3072': 3072, 'rsa4096': 4096}.get(kind)
    if not bits:
        raise ValueError('the key is ec256, ec384, rsa2048, rsa3072 or rsa4096')
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
    out = {k: v for k, v in prof.items() if k not in ('key', 'pending_key', 'ec_key')}
    out['has_key'] = bool(prof.get('key'))
    ec = tls_ec_of(prof)
    out['ec'] = {'info': ec.get('ec_info')} if ec else None
    for k in ('ec_cert', 'ec_chain', 'ec_for'):
        out.pop(k, None)
    out.pop('ec_info', None)
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
        if tls_ec_of(prof):
            out.update(ec_cert=prof['ec_cert'], ec_chain=prof.get('ec_chain') or '',
                       ec_key=unseal_secret(prof['ec_key'], 'web:tls'))
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
INCLUDE_PHP_GUARD = 'include %s;' % NGINX_PHP_GUARD


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


PHP_GUARD_TEXT = '''# Written by plexmon - php2shell (CVE-2026-109618, CVE-2026-109619), as otp
# guards its pages. plexmon's pages read what is sent to them from php://input
# (JSON, or a file as it is) and never have PHP take a body apart: a form or a
# multipart body is refused here, before PHP sees it. Updating PHP is the fix
# (install.sh does it); this stays as a second layer.
if ($http_content_type ~* "^[[:space:]]*(multipart/|application/x-www-form-urlencoded)") {
    return 415;
}
# A backup or a new version sent to be put back is the body itself (read by
# the page from php://input, a lump at a time): the size of one, here only.
client_max_body_size 8g;
fastcgi_param PHP_VALUE "post_max_size=8G";
fastcgi_request_buffering off;
'''

PHP_LOCATION_RE = re.compile(r'^([ \t]*)location\s+~\*?\s+[^{\n]*php[^{\n]*\{[ \t]*$', re.M)


def nginx_php_guard(dry=False):
    """php-guard.conf put in every PHP location of the Plex vhost (the pages
    under /smart/), on every install and upgrade: (changed, words). Kept as it
    was when nginx does not take it."""
    text = nginx_conf_text()
    if not text:
        return False, '%s is not there' % NGINX_SITE
    found = list(PHP_LOCATION_RE.finditer(text))
    if not found:
        return False, 'no PHP location in %s - nothing to guard' % NGINX_SITE
    have = ''
    try:
        have = NGINX_PHP_GUARD.read_text(encoding='utf-8')
    except OSError:
        pass
    out, at, added = [], 0, 0
    for m in found:
        end = m.end()
        nxt = text[end:end + 400]
        out.append(text[at:end])
        if INCLUDE_PHP_GUARD not in nxt.split('}', 1)[0]:
            out.append('\n%s    %s' % (m.group(1), INCLUDE_PHP_GUARD))
            added += 1
        at = end
    out.append(text[at:])
    new_text = ''.join(out)
    if not added and have == PHP_GUARD_TEXT:
        return False, 'the PHP pages are guarded already (%s)' % NGINX_PHP_GUARD
    if dry:
        return True, new_text
    snap = _snapshot([NGINX_SITE, NGINX_PHP_GUARD])
    try:
        NGINX_SNIPPETS.mkdir(parents=True, exist_ok=True)
        write_replacing(NGINX_PHP_GUARD, PHP_GUARD_TEXT, 0o644)
        if added:
            backup = STATE_DIR / 'backup'
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(NGINX_SITE), str(backup / ('%s.%s' % (
                NGINX_SITE.name, time.strftime('%Y%m%d-%H%M%S')))))
            mode = os.stat(str(NGINX_SITE)).st_mode & 0o777
            write_replacing(NGINX_SITE, new_text, mode)
        ok, said = nginx_test()
        if not ok:
            raise ValueError('nginx does not take it: %s' % said)
        nginx_reload()
    except Exception as e:                              # noqa: BLE001
        _put_back(snap)
        return False, 'left as it was: %s' % e
    return True, 'the PHP pages are guarded: a form or multipart body is refused (%s)' % (
        NGINX_PHP_GUARD)


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


def ec_paths(cert_path, key_path):
    """Where the ECDSA certificate of a dual one goes: beside the main one."""
    def side(p):
        root, ext = os.path.splitext(str(p))
        return root + '-ecdsa' + (ext or '.pem')
    return side(cert_path), side(key_path)


def tls_conf_text(t, cert_path, key_path, dh_path):
    lines = ['# Written by plexmon (Settings → Certificates and TLS).',
             '# Changed on the pages; changes made here are overwritten.',
             'ssl_certificate     %s;' % cert_path,
             'ssl_certificate_key %s;' % key_path]
    if t.get('ec_cert'):
        # A dual certificate (2.2.4): RSA first - the default - and ECDSA beside
        # it, given to the clients that say they take it.
        ec_cert, ec_key = ec_paths(cert_path, key_path)
        lines += ['ssl_certificate     %s;' % ec_cert, 'ssl_certificate_key %s;' % ec_key]
    lines.append('ssl_protocols %s;' % ' '.join(t['protocols']))
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
    if tls_ec_of(prof):
        keep.update(ec_cert=prof['ec_cert'], ec_chain=prof.get('ec_chain'), ec_key=prof['ec_key'])
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
    ec_cert_path, ec_key_path = ec_paths(cert_path, key_path)
    snap = _snapshot([cert_path, key_path, NGINX_TLS_CONF, NGINX_DH, ec_cert_path, ec_key_path])
    try:
        adopted = nginx_adopted()
        if t.get('cert'):
            web_progress_set('writing the certificate and its key', 3)
            write_replacing(cert_path, t['cert'] + t['chain'], 0o644)
            write_replacing(key_path, t['key'], 0o600)
            said.append('certificate: %s, until %s' % ((prof.get('info') or {}).get('subject'),
                                                       stamp((prof.get('info') or {})
                                                             .get('not_after'))))
        if t.get('ec_cert') and not adopted:
            t = dict(t, ec_cert='')         # nginx's file is not plexmon's: RSA alone
        if t.get('ec_cert'):
            write_replacing(ec_cert_path, t['ec_cert'] + t['ec_chain'], 0o644)
            write_replacing(ec_key_path, t['ec_key'], 0o600)
            said.append('and its ECDSA certificate beside it (%s)' % ec_cert_path)
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
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
# ---- going out through a proxy (2.4.0, otp's 1.0.166; by choice since 2.4.2) ------------
# Settings → Proxy: off, and used nowhere, until it is switched on - and then only
# by what is switched on beside it, each a purpose of url_open: 'upgrade' (the
# new versions), 'os' (dnf, handed over in a file only root reads, never on a
# command line), 'acme' (the ACME CA), 'dnsapi' (deSEC, Cloudflare, acme-dns),
# 'discover' (Plex Discover, posters from the internet), 'ratings' (TMDB, OMDb,
# Trakt, TVmaze). Whatever is left off goes out exactly as before 2.4.0.
PROXY_PURPOSES = ('upgrade', 'os', 'acme', 'dnsapi', 'discover', 'ratings')
def proxy_on(purpose='upgrade'):
    """Whether this purpose goes through the proxy: the proxy on, with an
    address, and this purpose switched on. 'any' - the proxy itself, for Try it."""
    if not CONFIG.get('proxy') or not str(CONFIG.get('proxy_url') or '').strip():
        return False
    if purpose == 'any':
        return True
    return purpose in PROXY_PURPOSES and bool(CONFIG.get('proxy_' + purpose))


def proxy_full(purpose='upgrade'):
    """The proxy's address with its user and password in it, for urllib, dnf
    and apt - '' where none is to be used. Never logged, never shown."""
    if not proxy_on(purpose):
        return ''
    url = str(CONFIG.get('proxy_url') or '').strip().rstrip('/')
    user = str(CONFIG.get('proxy_user') or '').strip()
    if not user:
        return url
    pw = unseal_secret(CONFIG.get('proxy_password') or '') or ''
    scheme, _, rest = url.partition('://')
    return '%s://%s:%s@%s' % (scheme, urllib.parse.quote(user, safe=''),
                              urllib.parse.quote(pw, safe=''), rest)


def proxy_no_list():
    return [w.strip().lower() for w in re.split(r'[\s,;]+', str(CONFIG.get('proxy_no') or ''))
            if w.strip()]


def proxy_skips(host):
    """Whether this host is reached straight, as proxy_no says."""
    host = str(host or '').strip('[]').lower()
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for one in proxy_no_list():
        if one == '*':
            return True
        if ip is not None and ('/' in one or re.match(r'^[0-9a-f.:]+\Z', one)):
            try:
                if ip in ipaddress.ip_network(one.strip('[]'), strict=False):
                    return True
            except ValueError:
                pass
            continue
        one = one.lstrip('*')
        if one.startswith('.'):
            if host.endswith(one) or host == one[1:]:
                return True
        elif host == one:
            return True
    return False


class _ProxyOnly(urllib.request.ProxyHandler):
    """ProxyHandler without its look at no_proxy in the environment: only
    proxy_no says what goes straight."""
    def proxy_open(self, req, proxy, type):
        proxy_type, user, password, hostport = urllib.request._parse_proxy(proxy)
        if user and password:
            creds = base64.b64encode(('%s:%s' % (urllib.parse.unquote(user),
                                                 urllib.parse.unquote(password))).encode())
            req.add_header('Proxy-authorization', 'Basic ' + creds.decode('ascii'))
        req.set_proxy(urllib.parse.unquote(hostport), proxy_type or req.type)
        return None


def url_open(req, timeout=30, context=None, purpose='other'):
    """urllib.request.urlopen. Through Settings → Proxy where it is on and this
    purpose is switched on there, and the host is not one reached straight.
    Anything else: exactly as before 2.4.0 - the service's environment alone."""
    if not isinstance(req, urllib.request.Request):
        req = urllib.request.Request(req)
    proxy = proxy_full(purpose)
    host = urllib.parse.urlsplit(req.full_url).hostname or ''
    if not proxy_on(purpose):
        # Not switched on for this (or no proxy at all): exactly as before
        # 2.4.0 - the service's own environment (systemctl edit plexmon:
        # Environment=https_proxy=…), Settings → Proxy changing nothing of it.
        handlers = [urllib.request.ProxyHandler()]
    else:
        handlers = [_ProxyOnly(
            {'http': proxy, 'https': proxy} if proxy and not proxy_skips(host) else {})]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


def proxy_try(url=''):
    """Settings → Proxy → Try it (1.0.169): an address asked the way plexmon asks
    the web - through the proxy where one is set and the address is not one
    reached straight, else straight - one step at a time: the name looked up,
    the connection, the tunnel through the proxy, TLS and the certificate,
    the answer. Always tried; each step with how long it took."""
    url = str(url or '').strip() or online_url() or 'https://github.com/'
    if not re.match(r'^https?://[^\s/]+[^\s]*\Z', url):
        raise ValueError('an address starting https:// (or http://)')
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ''
    tls_on = u.scheme == 'https'
    port = u.port or (443 if tls_on else 80)
    via, why = '', ''
    if not proxy_on('any'):
        why = 'no proxy is set' if not CONFIG.get('proxy') else 'the proxy has no address'
    elif proxy_skips(host):
        why = '%s is in the list reached straight' % host
    else:
        via = proxy_full('any')
    px = urllib.parse.urlsplit(via) if via else None
    out = {'url': url, 'host': host, 'port': port, 'mode': 'proxy' if via else 'direct',
           'why_direct': why, 'node': node_name(), 'steps': [], 'ok': False,
           'proxy': '%s:%d' % (px.hostname, px.port or 3128) if px else '',
           'proxy_auth': bool(px and px.username)}
    began = time.time()
    sock = [None]

    def step(what, fn):
        t0 = time.time()
        try:
            said = fn() or ''
        except Exception as e:                          # noqa: BLE001
            out['steps'].append({'what': what, 'ok': False, 'ms': int((time.time() - t0) * 1000),
                                 'said': str(getattr(e, 'reason', '') or e)[:300]})
            raise
        out['steps'].append({'what': what, 'ok': True, 'ms': int((time.time() - t0) * 1000),
                             'said': said})

    def resolve(name, p):
        got = socket.getaddrinfo(name, p, 0, socket.SOCK_STREAM)
        addrs = []
        for g in got:
            if g[4][0] not in addrs:
                addrs.append(g[4][0])
        out.setdefault('addrs', {})[name] = addrs
        return ', '.join(addrs[:4])

    def connect(name, p):
        sock[0] = socket.create_connection((name, p), timeout=10)
        sock[0].settimeout(15)
        peer = sock[0].getpeername()
        return '%s port %d' % (peer[0], peer[1])

    def read_head():
        data = b''
        while b'\r\n\r\n' not in data and len(data) < 65536:
            chunk = sock[0].recv(4096)
            if not chunk:
                break
            data += chunk
        line = data.split(b'\r\n', 1)[0].decode('latin-1', 'replace')
        m = re.match(r'^HTTP/\d(?:\.\d)?\s+(\d{3})\s*(.*)$', line)
        if not m:
            raise ValueError('no HTTP answer: %r' % line[:80])
        return int(m.group(1)), m.group(2).strip(), data

    def auth_header():
        if not (px and px.username):
            return ''
        creds = '%s:%s' % (urllib.parse.unquote(px.username), urllib.parse.unquote(px.password or ''))
        return 'Proxy-Authorization: Basic %s\r\n' % base64.b64encode(creds.encode()).decode()

    try:
        if via:
            step('the proxy looked up (%s)' % px.hostname, lambda: resolve(px.hostname, px.port or 3128))
            step('connected to the proxy', lambda: connect(px.hostname, px.port or 3128))
            if tls_on:
                def tunnel():
                    sock[0].sendall(('CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n%s'
                                     'User-Agent: plexmon/%s (+proxy check)\r\n\r\n' % (
                                         host, port, host, port, auth_header(), __version__)).encode())
                    code, words, _ = read_head()
                    if code == 407:
                        raise ValueError('the proxy asks for a user name and a password (407) - '
                                         'they are %s' % ('wrong' if px.username else 'missing'))
                    if code != 200:
                        raise ValueError('the proxy said %d %s' % (code, words))
                    return '%d %s%s' % (code, words, ' - with the user name and password'
                                        if px.username else '')
                step('a tunnel through the proxy to %s:%d' % (host, port), tunnel)
        else:
            step('%s looked up' % host, lambda: resolve(host, port))
            step('connected to %s' % host, lambda: connect(host, port))
        if tls_on:
            def handshake():
                ctx = ssl.create_default_context()
                sock[0] = ctx.wrap_socket(sock[0], server_hostname=host)
                c = sock[0].getpeercert() or {}
                subj = dict(x[0] for x in c.get('subject', ()) if x)
                iss = dict(x[0] for x in c.get('issuer', ()) if x)
                out['tls'] = {'version': sock[0].version(), 'cipher': (sock[0].cipher() or [''])[0],
                              'subject': subj.get('commonName', ''),
                              'issuer': iss.get('organizationName') or iss.get('commonName', ''),
                              'until': c.get('notAfter', '')}
                return '%s, %s - %s, by %s' % (out['tls']['version'], out['tls']['cipher'],
                                               out['tls']['subject'], out['tls']['issuer'])
            step('TLS, the certificate of %s checked' % host, handshake)

        def ask():
            path = (u.path or '/') + ('?' + u.query if u.query else '')
            target = url if (via and not tls_on) else path
            sock[0].sendall(('HEAD %s HTTP/1.1\r\nHost: %s\r\n%sUser-Agent: plexmon/%s (+proxy check)'
                             '\r\nConnection: close\r\n\r\n' % (
                                 target, u.netloc.split('@')[-1],
                                 auth_header() if (via and not tls_on) else '', __version__)).encode())
            code, words, _ = read_head()
            out['status'] = code
            if code == 407:
                raise ValueError('the proxy asks for a user name and a password (407)')
            return '%d %s' % (code, words)
        step('asked for %s' % (u.path or '/'), ask)
        out['ok'] = True
    except Exception:                                   # noqa: BLE001
        pass
    finally:
        try:
            if sock[0]:
                sock[0].close()
        except OSError:
            pass
    out['ms'] = int((time.time() - began) * 1000)
    return out


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
            with url_open(req, timeout=30, context=_acme_ctx(), purpose='acme') as r:
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

    def _alg(self):
        """(JWS alg, digest, bytes of r and s) of the account key (4.1.6)."""
        kind = getattr(self, 'akind', 'ec')
        if kind == 'ec384':
            return 'ES384', '-sha384', 48
        if kind.startswith('rsa'):
            return 'RS256', '-sha256', 0
        return 'ES256', '-sha256', 32

    def _jwk(self):
        if getattr(self, '_jwk_done', None):
            return self._jwk_done
        alg, _, size = self._alg()
        if alg == 'RS256':
            txt = _openssl(['pkey', '-in', self._key_file(), '-noout', '-text_pub'],
                           what='the account key').decode()
            m = re.search(r'(?s)Modulus:\s*\n(.*?)\n\S', txt)
            e = re.search(r'Exponent:\s*(\d+)', txt)
            if not m or not e:
                raise AcmeError('the RSA account key does not read')
            n = bytes.fromhex(re.sub(r'[^0-9a-fA-F]', '', m.group(1))).lstrip(b'\x00')
            ev = int(e.group(1))
            jwk = {'e': _b64u(ev.to_bytes((ev.bit_length() + 7) // 8, 'big')), 'kty': 'RSA',
                   'n': _b64u(n)}
        else:
            der = _openssl(['pkey', '-in', self._key_file(), '-pubout', '-outform', 'DER'],
                           what='the account key')
            point = der[-2 * size:]
            jwk = {'crv': 'P-384' if size == 48 else 'P-256', 'kty': 'EC',
                   'x': _b64u(point[:size]), 'y': _b64u(point[size:])}
        self._jwk_done = jwk
        return jwk

    def thumbprint(self):
        # RFC 7638: the required members only, sorted - the JWK holds just those.
        return _b64u(hashlib.sha256(json.dumps(
            self._jwk(), separators=(',', ':'), sort_keys=True).encode()).digest())

    def _sign(self, data):
        alg, digest, size = self._alg()
        der = _openssl(['dgst', digest, '-sign', self._key_file()], data=data,
                       what='signing for the CA')
        if alg == 'RS256':
            return der                       # PKCS#1 v1.5, as JWS wants it
        # DER SEQUENCE { INTEGER r, INTEGER s } -> r || s, `size` bytes each.
        i = 2 if der[1] < 0x80 else 3

        def integer(at):
            n = der[at + 1]
            v = der[at + 2:at + 2 + n].lstrip(b'\x00')
            return v.rjust(size, b'\x00'), at + 2 + n
        r, i = integer(i)
        s, _ = integer(i)
        return r + s

    def post(self, url, payload, use_jwk=False):
        anew = False
        for attempt in (1, 2, 3):
            if not self.nonce:
                self._req(self.dir['newNonce'], method='HEAD')
            protected = {'alg': self._alg()[0], 'nonce': self.nonce, 'url': url}
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
        # The kind of key asked for (4.1.6): another one than the account has
        # makes a new account with a key of that kind.
        self.akind = str(CONFIG.get('acme_account_key') or 'ec')
        if self.akind not in KEY_WORDS:
            self.akind = 'ec'
        if row.get('directory') != self.dir_url or not pem or \
                (row.get('kind') or 'ec') != self.akind:
            pem = ''
        if fresh:
            self.kid = None
            try:
                os.unlink(os.path.join(self.work, 'account.pem'))
            except OSError:
                pass
        if not pem:
            pem = new_key(self.akind)
            row = {}
            self.kid = None
            self._jwk_done = None
            try:
                os.unlink(os.path.join(self.work, 'account.pem'))
            except OSError:
                pass
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
            'email': mail, 'at': time.time(), 'kind': self.akind}))

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

    def order(self, names, say, key_kind=None):
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
        key, csr = make_csr(names[0], names[1:], key_kind or 'ec')
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


KEY_WORDS = {'ec': 'ECDSA P-256', 'ec384': 'ECDSA P-384', 'rsa2048': 'RSA 2048',
             'rsa3072': 'RSA 3072', 'rsa4096': 'RSA 4096'}
# A certificate's choice: one key, or two certificates - RSA served by
# default, ECDSA to the clients that take it (what Let's Encrypt advises).
ACME_CERT_KEYS = {'dual': ('rsa2048', 'ec'), 'dual-strong': ('rsa4096', 'ec384'),
                  'dual384': ('rsa4096', 'ec384'),      # its name in 2.2.4-2.2.5
                  'ec': ('ec',), 'ec384': ('ec384',), 'rsa2048': ('rsa2048',),
                  'rsa3072': ('rsa3072',), 'rsa4096': ('rsa4096',)}


def acme_key_kinds():
    """The keys of what is ordered: one, or RSA then ECDSA for both."""
    return list(ACME_CERT_KEYS.get(str(CONFIG.get('acme_key') or 'dual')) or ('ec',))


def tls_ec_of(prof):
    """The ECDSA certificate beside the main one - only while it belongs to it
    (a certificate put in some other way leaves it behind)."""
    if not prof or not prof.get('ec_cert') or not prof.get('ec_key') or not prof.get('cert'):
        return None
    if prof.get('ec_for') != hashlib.sha256(prof['cert'].encode()).hexdigest():
        return None
    return prof


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
        kinds = acme_key_kinds()
        say('the key%s: %s' % ('s' if len(kinds) > 1 else '', ' + '.join(KEY_WORDS[k] for k in kinds)))
        key, chain = a.order([name], say, kinds[0])
        got = cert_bundle(chain, key)
        info = got['info']
        ec = None
        if len(kinds) > 1:
            # The second certificate of a dual one (2.2.4): ECDSA, for the clients
            # that take it - ordered again for the same name; the authorization
            # the CA has just given is used again, nothing is checked twice.
            try:
                say('a second certificate, with a %s key' % KEY_WORDS[kinds[1]])
                ekey, echain = a.order([name], say, kinds[1])
                ec = cert_bundle(echain, ekey)
            except (AcmeError, ValueError, OSError) as e:
                say('the ECDSA certificate did not come (%s) - the RSA one alone' % e)
                store.add_event('settings', 'ACME: the ECDSA certificate beside the RSA one did '
                                'not come: %s - the RSA one alone is put in' % e, 'warn', who=by)
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
        for k in ('ec_cert', 'ec_chain', 'ec_key', 'ec_info', 'ec_for'):
            prof.pop(k, None)
        if ec:
            prof.update(ec_cert=ec['cert'], ec_chain=ec['chain'],
                        ec_key=seal_secret(ec['key'], 'web:tls'), ec_info=ec['info'],
                        ec_for=hashlib.sha256(got['cert'].encode()).hexdigest())
        prof.setdefault('preset', 'standard')
        prof.setdefault('dh', 'none')
        tls_save(store, target, prof, by)
        st.update(ok=True, not_after=info.get('not_after'), issuer=info.get('issuer'),
                  ordered_at=time.time(), keys=' + '.join(KEY_WORDS[k] for k in kinds[:2 if ec else 1]),
                  ec_not_after=(ec or {}).get('info', {}).get('not_after'))
        st['steps'] = steps[-25:]
        store.set_meta('acme:state:' + target, json.dumps(st))
        store.add_event('settings', 'a certificate for %s (%s) came from the CA, good until %s'
                        ' (%s)' % (name, target,
                                   stamp(info.get('not_after')) if info.get('not_after') else '?',
                                   st['keys']),
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
                'kid': acc.get('kid'), 'email': acc.get('email'), 'at': acc.get('at'),
                'key': KEY_WORDS.get(acc.get('kind') or 'ec', '') if acc.get('kid') else ''},
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
                      if base.startswith('https') else None, purpose='dnsapi') as r:
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
                      if url.startswith('https') else None, purpose='dnsapi') as r:
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
                          if base.startswith('https') else None, purpose='dnsapi') as r:
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
    _upgrade_back(store)
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


def _upgrade_back(store):
    """Back in service after an upgrade - only from the maintenance it began."""
    m = maintenance_row(store)
    if m.get('on') and m.get('by') == 'upgrade':
        maintenance_set(store, False, '', 'upgrade')


def _upgrade_failed(store, plan, said, tail=None):
    _my_step(store, plan, state='failed', said=str(said)[:500], tail=(tail or [])[-40:])
    _upgrade_back(store)
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
        if not maintenance_row(store).get('on'):
            maintenance_set(store, True, 'the upgrade to %s' % plan['version'], 'upgrade')
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
        _upgrade_back(store)
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
    with url_open(req, timeout=timeout, context=ctx, purpose='upgrade') as r:
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


# ---- maintenance (2.3.0, as otp's - one server, no cluster) ---------------------------
# A server in maintenance is out of service: plexmon does nothing by itself -
# no backups, copies of the databases, system updates, restarts on a schedule,
# no renewal of the certificate, no routine SMART read of the disks - and
# says so on every page. These pages, the dashboard and everything done by
# hand go on working. An upgrade from the pages takes the server into
# maintenance while it is put in, and back once it runs again.
MAINT_CACHE = {'at': 0.0, 'row': None}


def maintenance_row(store, name=None):
    return _meta_json(store, 'maint:' + (name or node_name())) or {'on': False}


def maintenance_now(store):
    """This server's maintenance, read at most every two seconds."""
    now = time.time()
    if now - MAINT_CACHE['at'] > 2:
        MAINT_CACHE['at'] = now
        try:
            MAINT_CACHE['row'] = maintenance_row(store)
        except Exception:                               # noqa: BLE001
            pass                                        # the last one known stands
    return MAINT_CACHE['row'] or {'on': False}


def maintenance_mark(on):
    """The mark the dashboard's daemon (plexmon.py) reads: no routine SMART."""
    mark = RUN_DIR / 'maintenance'
    try:
        if on:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            mark.write_text('1\n')
        elif mark.exists():
            mark.unlink()
    except OSError:
        pass


def maintenance_set(store, on, why='', by='', name=None):
    name = name or node_name()
    if name != node_name():
        raise ValueError('there is no node %s' % name)
    row = {'on': bool(on), 'why': str(why or '')[:200], 'by': str(by or '')[:100],
           'at': time.time()}
    store.set_meta('maint:' + name, json.dumps(row))
    MAINT_CACHE['at'] = 0
    maintenance_mark(on)
    store.add_event('starting and stopping', '%s: %s%s' % (
        name, 'in maintenance' if on else 'back in service',
        (' - ' + why) if why and on else ''), 'warn' if on else 'info', who=by or None)
    return row


def upgrade_view(store):
    meta = upgrade_package_meta(store)
    ok, why = upgrade_checks(store, meta, True) if meta else (False, [])
    plan = upgrade_plan(store)
    hidden = _meta_json(store, 'upgrade:hidden') or {}
    if plan and hidden.get('id') == plan.get('id') and plan['state'] != 'running':
        plan = None
    me = {'name': node_name(), 'me': True, 'alive': True, 'version': __version__,
          'at': time.time(), 'maintenance': maintenance_row(store)}
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


# ---------------------------------------------------------------------------
# PHP and php2shell, as the daemon can tell from outside it (as otp)
# ---------------------------------------------------------------------------
# The pages say what version of PHP they run on; a version below the fixed
# ones (8.2.35, 8.3.36, 8.4.27, 8.5.12) is "may be affected", because a
# distribution that backported the fix keeps its old number. The packages'
# own changelog says whether the fix is in, and the daemon can read it where
# PHP cannot: so the warning on the pages goes away when the fix is there.
PHP_FIXED = {'8.2': (8, 2, 35), '8.3': (8, 3, 36), '8.4': (8, 4, 27), '8.5': (8, 5, 12)}
_PHP_STATE = {'at': 0, 'state': None, 'busy': False}


def _php_state_now():
    out = {'version': '', 'fixed': None, 'how': ''}
    import glob
    fpm = sorted(glob.glob('/usr/sbin/php-fpm*'))
    php = (fpm[-1] if fpm else None) or shutil.which('php')
    if php:
        try:
            line = subprocess.run([php, '-v'], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
            m = re.match(r'^PHP\s+(\d+\.\d+\.\d+)', line)
            out['version'] = m.group(1) if m else ''
        except (OSError, subprocess.SubprocessError):
            pass
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)', out['version'])
    if m:
        v = tuple(int(x) for x in m.groups())
        want = PHP_FIXED.get('%d.%d' % v[:2])
        if (want and v >= want) or (not want and v[:2] > (8, 5)):
            out.update(fixed=True, how='version')
    if not out['fixed']:
        said = ''
        rpm = shutil.which('rpm')
        if rpm:
            for pkg in ('php-fpm', 'php-common'):
                try:
                    said += subprocess.run([rpm, '-q', '--changelog', pkg], capture_output=True,
                                           text=True, timeout=20).stdout
                except (OSError, subprocess.SubprocessError):
                    pass
        else:
            import gzip
            for f in glob.glob('/usr/share/doc/php*-fpm/changelog.Debian.gz') + glob.glob(
                    '/usr/share/doc/php*-common/changelog.Debian.gz'):
                try:
                    with gzip.open(f, 'rt', errors='replace') as g:
                        said += g.read(200000)
                except OSError:
                    pass
        if 'CVE-2026-109618' in said:
            out.update(fixed=True, how='changelog')
        elif out['version']:
            out['fixed'] = False
    return out


def php_state(wait=False):
    """What PHP php-fpm runs and whether it has the php2shell fix - read in a
    thread of its own once an hour (rpm --changelog takes seconds), so a page
    asking never waits; `wait` for the command line."""
    if wait and (_PHP_STATE['state'] is None or time.time() - _PHP_STATE['at'] > 3600):
        _PHP_STATE.update(state=_php_state_now(), at=time.time())
    elif time.time() - _PHP_STATE['at'] > 3600 and not _PHP_STATE['busy']:
        _PHP_STATE['busy'] = True

        def go():
            try:
                _PHP_STATE['state'] = _php_state_now()
            except Exception:                           # noqa: BLE001
                pass
            finally:
                _PHP_STATE.update(at=time.time(), busy=False)
        threading.Thread(target=go, name='plexmon-php', daemon=True).start()
    return dict(_PHP_STATE['state'] or {'version': '', 'fixed': None, 'how': ''})


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
    out['php'] = php_state()
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

    def send_raw(self, data, ctype, status=200, cache='private, max-age=604800'):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, name, ctype='application/octet-stream'):
        """A file of any size, a lump at a time, as a download."""
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        self.send_header('Content-Disposition', 'attachment; filename="%s"' %
                         re.sub(r'[^A-Za-z0-9@._-]', '', name))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        with open(path, 'rb') as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

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
            if k == 'target' and isinstance(v, dict) and \
                    self.path.split('?')[0] == '/api/rbackup':
                continue                        # a backup destination's fields (2.2.0)
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


# ---- the Plex library (plex_tools, 2.1.0) ------------------------------------------------
# Read by any account of Settings; changed by a full administrator. The token
# of the Plex server stays here - the pages get the items and small images.
def _plex(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PX.PlexError as e:
        raise ApiError(502, str(e))


def _rks(b, key='rks'):
    got = b.get(key) or []
    if not isinstance(got, list):
        raise ValueError('%s is a list' % key)
    return [str(x) for x in got if str(x).isdigit()][:5000]


@route('GET', '/api/plex')
def _plex_state(h):
    h.me()
    out = _plex(PX.state)
    out['title_lang'] = CONFIG.get('plex_title_lang') or 'en'
    return out


@route('GET', '/api/plex/items')
def _plex_items(h):
    h.me()
    data = _plex(PX.items, h.arg('lib'), fresh=h.arg('fresh') == '1')
    return {'section': data['section'], 'items': data['items'], 'at': data['at']}


@route('GET', '/api/plex/item')
def _plex_item(h):
    h.me()
    rk = h.arg('rk')
    if not rk.isdigit():
        raise ApiError(400, 'which item?')
    return {'item': _plex(PX.item_view, rk)}


@route('GET', '/api/plex/children')
def _plex_children(h):
    h.me()
    rk = h.arg('rk')
    if not rk.isdigit():
        raise ApiError(400, 'which item?')
    return {'items': _plex(PX.children_view, rk)}


@route('GET', '/api/plex/img')
def _plex_img(h):
    h.me()
    try:
        data, ctype = PX.image(h.arg('u'), h.num('w', 300, 40, 1920), h.num('h', 450, 40, 1080))
    except (PX.PlexError, OSError, ValueError) as e:
        raise ApiError(404, str(e))
    h.send_raw(data, ctype)
    return None


@route('GET', '/api/plex/job')
def _plex_job(h):
    h.me()
    return {'job': PX.job_view()}


@route('GET', '/api/plex/labels')
def _plex_labels(h):
    h.me()
    return {'labels': _plex(PX.label_groups, h.arg('lib'))}


@route('GET', '/api/plex/titles')
def _plex_titles(h):
    h.me()
    return _plex(PX.titles_rows, h.arg('lib'))


@route('GET', '/api/plex/matches')
def _plex_matches(h):
    h.me('fulladmin')
    rk = h.arg('rk')
    if not rk.isdigit():
        raise ApiError(400, 'which item?')
    return {'matches': _plex(PX.matches, rk, lang=h.arg('lang') or None,
                             title=h.arg('title') or None, year=h.arg('year') or None)}


@route('GET', '/api/plex/watchlist')
def _plex_watchlist(h):
    h.me()
    return _plex(PX.watchlist_view, fresh=h.arg('fresh') == '1')


@route('GET', '/api/plex/discover')
def _plex_discover(h):
    h.me()
    return _plex(PX.discover_metadata, h.arg('dk'))


@route('GET', '/api/plex/person')
def _plex_person(h):
    h.me()
    pid = h.arg('id')
    if not pid.isdigit():
        raise ApiError(400, 'which person?')
    return _plex(PX.person_titles, pid, h.arg('name'))


@route('POST', '/api/plex')
def _plex_do(h):
    s = h.me('fulladmin')
    by = s['name']
    b = h.body()
    act = str(b.get('action') or '')
    st = h.store
    lib = str(b.get('lib') or '')
    if act == 'stop':
        return {'stopped': PX.job_stop(), 'job': PX.job_view()}
    if act == 'forget':
        PX.forget()
        return {'message': 'read again from Plex'}
    if act in ('watched', 'unwatched'):
        return _plex(PX.set_watched, st, _rks(b), act == 'watched', by)
    if act == 'activities':
        # What Plex is doing now (2.5.0): F5 / F9 followed to their end, as plex_tools did.
        return {'activities': PX.activities()}
    if act == 'watched-toggle':
        return _plex(PX.toggle_watched, st, _rks(b), by)
    if act == 'tracks-scan':
        return _plex(PX.tracks_scan, _rks(b))
    if act == 'tracks-plan':
        return _plex(PX.tracks_plan, str(b.get('scan') or ''), str(b.get('audio') or 'keep'),
                     str(b.get('subs') or 'keep'), str(b.get('missing_audio') or 'skip'),
                     str(b.get('missing_subs') or 'skip'))
    if act == 'tracks-apply':
        return {'job': _plex(PX.tracks_apply, st, str(b.get('scan') or ''), by)}
    if act == 'tracks-rescan':
        return {'job': _plex(PX.tracks_rescan, st, str(b.get('scan') or ''), by)}
    if act == 'labels':
        return _plex(PX.labels_do, st, lib, str(b.get('how') or 'edit'), by, rks=_rks(b),
                     add=[str(x) for x in b.get('add') or []][:50],
                     remove=[str(x) for x in b.get('remove') or []][:50],
                     old=str(b.get('old') or ''), new=str(b.get('new') or ''))
    if act == 'titles-read':
        return {'job': _plex(PX.titles_read, st, lib, _rks(b), by)}
    if act == 'titles-propose':
        lang = str(b.get('lang') or CONFIG.get('plex_title_lang') or 'en')
        return {'job': _plex(PX.titles_propose, st, lib, _rks(b), lang, by)}
    if act == 'titles-preview':
        return _plex(PX.titles_preview, st, lib, str(b.get('how') or ''), by, rks=_rks(b),
                     lang=str(b.get('lang') or CONFIG.get('plex_title_lang') or 'en'),
                     risky=bool(b.get('risky')))
    if act == 'titles-apply':
        items = [x for x in (b.get('items') or []) if isinstance(x, dict)][:5000]
        return {'job': _plex(PX.titles_apply, st, lib, str(b.get('how') or ''), by,
                             items_=items, rks=_rks(b),
                             lang=str(b.get('lang') or CONFIG.get('plex_title_lang') or 'en'),
                             risky=bool(b.get('risky')))}
    if act == 'refresh':
        return {'job': _plex(PX.refresh_items, st, _rks(b), by)}
    if act == 'scan':
        return _plex(PX.scan_library, st, lib, by, force=bool(b.get('force')))
    if act in ('watchlist-add', 'watchlist-remove'):
        dks = [str(x) for x in b.get('dks') or []][:500]
        return _plex(PX.watchlist_do, st, act.split('-')[1], dks, by)
    if act == 'ratings-preview':
        return {'job': _plex(PX.ratings_preview, st, lib, _rks(b), bool(b.get('overwrite')), by,
                             expand=b.get('expand', True) is not False)}
    if act == 'ratings-apply':
        return {'job': _plex(PX.ratings_apply, st, str(b.get('plan') or ''),
                             bool(b.get('lock')), _rks(b, 'only'), by)}
    raise ApiError(400, 'no such thing to do')


# ---- the machine: Restart and power off, Time, System updates, The database,
# Backups (2.2.0, as otp's) -----------------------------------------------------------------
@route('GET', '/api/power')
def _power(h):
    h.me('fulladmin')
    return HX.power_view(h.store)


@route('POST', '/api/power')
def _power_do(h):
    s = h.me('fulladmin')
    b = h.body()
    if b.get('action') == 'hide':
        plan = HX.power_plan(h.store)
        if plan.get('id'):
            h.store.set_meta('power:seen', str(plan['id']))
        return HX.power_view(h.store)
    plan = HX.power_ask(h.store, str(b.get('how') or ''), s['name'])
    return {'message': 'asked: %s in 5 seconds' % ('the restart' if plan['how'] == 'reboot'
                                                    else 'the power-off'), 'plan': plan}


@route('GET', '/api/ntp')
def _ntp(h):
    h.me('fulladmin')
    return HX.ntp_view(h.store)


@route('POST', '/api/ntp')
def _ntp_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act = b.get('action')
    if act == 'save':
        HX.ntp_save(h.store, b.get('conf') or {}, s['name'])
        return {'message': 'saved; the time servers are set within seconds'}
    if act == 'read':
        HX.ntp_job(HX.ntp_publish, h.store, True)
        return {'message': 'read again within seconds'}
    if act == 'sync':
        HX.ntp_job(HX.ntp_sync, h.store, s['name'])
        return {'message': 'the clock is stepped within seconds'}
    raise ApiError(400, 'no such thing to do')


@route('GET', '/api/osup')
def _osup(h):
    h.me('fulladmin')
    return HX.osup_view(h.store)


@route('POST', '/api/osup')
def _osup_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act = str(b.get('action') or '')
    if act == 'check':
        HX.osup_check(h.store, s['name'])
        return {'message': 'looking for updates'}
    if act == 'start':
        HX.osup_start(h.store, str(b.get('reboot') or 'ask'), s['name'],
                      security=bool(b.get('security')))
        return {'message': 'the update starts'}
    if act == 'cancel':
        return {'message': HX.osup_cancel(h.store, s['name'])}
    if act == 'clear':
        HX.osup_clear(h.store)
        return HX.osup_view(h.store)
    if act == 'hide':
        plan = HX.osup_plan(h.store)
        if plan.get('state') == 'running':
            raise ApiError(400, 'it is still going on')
        if plan.get('id'):
            h.store.set_meta('host:osup:seen', str(plan['id']))
        return HX.osup_view(h.store)
    if act == 'reboot':
        plan = HX.power_ask(h.store, 'reboot', s['name'], 'after the update')
        return {'message': 'restarting in 5 seconds', 'plan': plan}
    raise ApiError(400, 'no such thing to do')


@route('GET', '/api/db')
def _db(h):
    h.me('fulladmin')
    return HX.db_view(h.store)


@route('GET', '/api/db/file')
def _db_file(h):
    s = h.me('fulladmin')
    kind, name = h.arg('kind'), os.path.basename(h.arg('name'))
    if kind == 'own':
        d, ok = HX.own_dir(), name.startswith('plexmon-') and name.endswith('.db.gz')
    elif kind == 'plex':
        d, ok = HX.plex_dir(), name.startswith('plexdb-') and name.endswith('.tar.gz')
    else:
        raise ApiError(400, 'which copy?')
    path = os.path.join(str(d), name)
    if not ok or not os.path.isfile(path):
        raise ApiError(404, 'there is no copy %s' % name)
    h.store.add_event('database', '%s downloaded the copy %s' % (s['name'], name), 'warn',
                      who=s['name'])
    h.send_file(path, name)
    return None


@route('POST', '/api/db')
def _db_do(h):
    s = h.me('fulladmin')
    by = s['name']
    b = h.body()
    act = str(b.get('action') or '')
    st = h.store
    name = str(b.get('name') or '')
    if act == 'job':
        return {'job': dict(HX.DB_JOB)}
    if act == 'own-copy':
        return {'message': 'a copy made: %s' % HX.own_copy(st, 'made by %s' % by)}
    if act == 'own-check':
        got = HX.own_check(st)
        st.add_event('database', '%s checked the database: %s' % (by, ', '.join(got['said'][:3])),
                     'info' if got['ok'] else 'bad', who=by)
        return dict(got, message='the database is whole' if got['ok'] else
                    'the database is NOT whole: %s' % '; '.join(got['said'][:3]))
    if act == 'own-optimize':
        got = HX.own_optimize(st, by)
        return dict(got, message='%s → %s' % (HX.human_bytes(got['before']),
                                             HX.human_bytes(got['after'])))
    if act == 'own-tidy':
        st.tidy()
        return {'message': 'what is kept is applied now'}
    if act == 'own-erase':
        got = HX.own_erase(st, str(b.get('what') or ''), by)
        return dict(got, message='erased: %s' % got['erased'])
    if act == 'own-restore':
        return HX.own_restore(st, name, by)
    if act == 'own-forget':
        p = HX.own_dir() / os.path.basename(name)
        if not name.startswith('plexmon-') or not p.is_file():
            raise ApiError(404, 'there is no copy %s' % name)
        p.unlink()
        st.add_event('database', '%s deleted the copy %s' % (by, name), 'warn', who=by)
        return {'message': 'deleted'}
    if act in ('plex-copy', 'plex-check'):
        return {'job': HX.db_job('a copy of Plex\'s database', by,
                                 lambda: HX.plex_copy(st, by, check=True))}
    if act == 'plex-restore':
        own = bool(b.get('own'))
        return {'job': HX.db_job('Plex\'s database put back', by,
                                 lambda: HX.plex_restore(st, name, by, own=own))}
    if act == 'plex-forget':
        p = HX.plex_dir() / os.path.basename(name)
        if not name.startswith('plexdb-') or not p.is_file():
            raise ApiError(404, 'there is no copy %s' % name)
        p.unlink()
        st.add_event('database', '%s deleted the copy %s of Plex\'s database' % (by, name), 'warn',
                     who=by)
        return {'message': 'deleted'}
    if act == 'plex-optimize':
        HX.plex_api('/library/optimize?async=1')
        st.add_event('database', '%s asked Plex Media Server to optimize its database' % by,
                     who=by)
        return {'message': 'Plex Media Server optimizes its database now (its own task)'}
    if act == 'plex-clean':
        HX.plex_api('/library/clean/bundles?async=1')
        st.add_event('database', '%s asked Plex Media Server to clean its bundles' % by, who=by)
        return {'message': 'Plex Media Server cleans its bundles now'}
    if act == 'timer':
        unit = str(b.get('unit') or '')
        if unit not in [t['unit'] for t in HX.user_timer()]:
            raise ApiError(400, 'no such timer')
        on = bool(b.get('on'))
        rc, out = HX.host_run([HX.SYSTEMCTL, 'enable' if on else 'disable', '--now', unit], 60)
        if rc != 0:
            raise ApiError(400, out.strip()[-200:] or 'systemctl said no')
        st.add_event('database', '%s switched %s %s' % (by, 'on' if on else 'off', unit), 'warn',
                     who=by)
        return {'message': '%s is %s' % (unit, 'on' if on else 'off')}
    raise ApiError(400, 'no such thing to do')


def _rb_target_for(h, b):
    t = HX.rb_target(h.store, b.get('id')) if b.get('id') else None
    if b.get('target'):
        t = HX.rb_checked(b['target'], t)
    if not t:
        raise ApiError(404, 'no such destination')
    return t


def _rb_list(store, tid):
    out = []
    mine = HX.rb_slug(node_name())
    for x in HX.rb_remote_list(store, tid):
        m = HX.RB_NAME_RE.match(x['name'])
        if not m or x['name'].endswith(('.sha256', '.md')):
            continue
        try:
            when = datetime.strptime(m.group('stamp'), '%Y%m%d-%H%M%S').timestamp()
        except ValueError:
            when = 0
        out.append({'name': x['name'], 'bytes': x.get('size'), 'when': when,
                    'node': m.group('node'), 'mine': m.group('node') == mine,
                    'encrypted': x['name'].endswith('.enc')})
    out.sort(key=lambda x: -x['when'])
    return out


def _rb_next():
    if not CONFIG.get('rbackup'):
        return 0
    try:
        hh, mm = (int(x) for x in str(CONFIG.get('rbackup_at') or '04:15').split(':', 1))
    except ValueError:
        return 0
    now = datetime.now()
    for add in range(0, 9):
        d = (now + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if d <= now:
            continue
        if CONFIG.get('rbackup_every') == 'weekly' and d.isoweekday() != int(
                CONFIG.get('rbackup_day') or 7):
            continue
        return int(d.timestamp() * 1000)
    return 0


PROXY_TRIES = {}


@route('POST', '/api/proxy')
def _proxy_do(h):
    """Settings → Proxy → Try it (as otp's): started in a thread, its result
    asked for until it is there - one server, so one node's card."""
    h.me('fulladmin')
    b = h.body()
    act = str(b.get('action') or '')
    try:
        if act == 'try':
            url = str(b.get('url') or '').strip()
            if url and not re.match(r'^https?://[^\s/]+[^\s]*\Z', url):
                raise ValueError('an address starting https:// (or http://)')
            ident = secrets.token_hex(6)
            PROXY_TRIES.clear()
            PROXY_TRIES[ident] = None

            def go():
                try:
                    PROXY_TRIES[ident] = proxy_try(url)
                except Exception as e:                  # noqa: BLE001
                    PROXY_TRIES[ident] = {'ok': False, 'error': str(e)[:300]}
            threading.Thread(target=go, name='plexmon-proxy-try', daemon=True).start()
            return {'id': ident, 'nodes': [node_name()]}
        if act == 'result':
            return {'nodes': {node_name(): PROXY_TRIES.get(str(b.get('id') or ''))}}
    except ValueError as e:
        raise ApiError(400, str(e))
    raise ApiError(400, 'no such thing to do')


@route('GET', '/api/rbackup')
def _rbackup(h):
    h.me('fulladmin')
    v = HX.rb_view_all(h.store)
    runs = v['runs']
    v.update(locked=v['password'], next=_rb_next(),
             tools={k: bool(shutil.which(k)) for k in ('ssh', 'sftp', 'scp', 'ssh-keygen',
                                                       'ssh-keyscan', 'smbclient', 'openssl')},
             kinds=[{'kind': k, 'words': HX.RB_KIND_WORDS[k], 'port': HX.RB_PORTS.get(k)}
                    for k in HX.RB_KINDS], iter=HX.RB_ITER,
             site=CONFIG.get('site_name') or 'plexmon',
             last_by_node={node_name(): runs[0]} if runs else {},
             verified=[dict(x, node=node_name(), target_name=x.get('target'))
                       for x in v['verified']])
    return v


@route('POST', '/api/rbackup')
def _rbackup_do(h):
    s = h.me('fulladmin')
    b = h.body()
    act = str(b.get('action') or '')
    by = s['name']
    st = h.store
    if act == 'save':
        t = HX.rb_save(st, dict(b.get('target') or {}, id=b.get('id') or
                                (b.get('target') or {}).get('id')), by)
        return {'message': 'saved', 'target': HX.rb_view(t)}
    if act == 'delete':
        HX.rb_delete(st, b.get('id'), by)
        return {'message': 'taken away'}
    if act == 'try':
        t = _rb_target_for(h, b)
        if t['kind'] in ('sftp', 'scp') and not t.get('hostkey'):
            lines, fps = HX.ssh_keyscan(t['host'], t['port'])
            return {'hostkey_needed': True, 'hostkey': lines, 'fingerprints': fps,
                    'message': 'the server\'s host key is not trusted yet'}
        out = HX.rb_try(t)
        st.add_event('settings', '%s tried the backup destination %s: %s' % (
            by, t['name'], 'it works' if out['ok'] else out.get('error')), who=by)
        if t.get('id') and HX.rb_target(st, t['id']):
            HX.rb_set_fields(st, t['id'], '', '', tried={'at': time.time(), 'ok': out['ok'],
                                                        'error': out.get('error') or ''})
        return out
    if act == 'hostkey_scan':
        t = _rb_target_for(h, b)
        lines, fps = HX.ssh_keyscan(t['host'], t['port'])
        return {'hostkey': lines, 'fingerprints': fps}
    if act == 'hostkey_trust':
        t = _rb_target_for(h, b)
        lines = str(b.get('hostkey') or '')
        fresh, fps = HX.ssh_keyscan(t['host'], t['port'])
        if sorted(x.strip() for x in lines.splitlines() if x.strip()) != \
                sorted(x.strip() for x in fresh.splitlines() if x.strip()):
            raise ApiError(400, 'the server shows other host keys now than the ones shown: '
                                'Try it again')
        HX.rb_set_fields(st, t['id'], by, 'its host key trusted (%s)' % '; '.join(fps),
                         hostkey=fresh)
        return {'message': 'trusted', 'fingerprints': fps}
    if act == 'hostkey_forget':
        t = _rb_target_for(h, b)
        HX.rb_set_fields(st, t['id'], by, 'its host key forgotten', hostkey=None)
        return {'message': 'forgotten'}
    if act == 'keygen':
        t = _rb_target_for(h, b)
        private, public = HX.ssh_keygen('plexmon-backup@%s' % node_name())
        HX.rb_set_fields(st, t['id'], by, 'a new ssh key made here',
                         key=seal_secret(private, bind=HX.RB_BIND), pub=public, key_pass=None)
        return {'message': 'made', 'pub': public}
    if act == 'key_paste':
        t = _rb_target_for(h, b)
        pp = str(b.get('passphrase') or '')
        private, public = HX.ssh_public_of(b.get('key'), pp)
        HX.rb_set_fields(st, t['id'], by, 'an ssh key of their own given',
                         key=seal_secret(private, bind=HX.RB_BIND), pub=public,
                         key_pass=seal_secret(pp, bind=HX.RB_BIND) if pp else None)
        return {'message': 'taken', 'pub': public, 'key_pass': secret_shown(pp) if pp else ''}
    if act == 'key_download':
        t = _rb_target_for(h, b)
        private = HX.rb_secret(t, 'key')
        if not private:
            raise ApiError(404, 'there is no key')
        st.add_event('settings', '%s downloaded the private ssh key of the backup destination %s'
                     % (by, t['name']), 'warn', who=by)
        return {'pem': private if private.endswith('\n') else private + '\n',
                'file': 'plexmon-backup-%s-key' % HX.rb_slug(t['name'])}
    if act == 'key_forget':
        t = _rb_target_for(h, b)
        HX.rb_set_fields(st, t['id'], by, 'its ssh key forgotten', key=None, pub=None,
                         key_pass=None, ssh_cert=None)
        return {'message': 'forgotten'}
    if act == 'cert_make':
        t = _rb_target_for(h, b)
        key, crt = HX.rb_client_cert_make(b.get('cn') or 'plexmon-backup-%s' %
                                          HX.rb_slug(node_name()))
        HX.rb_set_fields(st, t['id'], by, 'a client certificate made here', client_cert=crt,
                         client_key=seal_secret(key, bind=HX.RB_BIND))
        return {'message': 'made', 'cert': crt, 'info': cert_info(crt)}
    if act == 'cert_given':
        t = _rb_target_for(h, b)
        key, crt = HX.rb_client_cert_given(b.get('text'), str(b.get('password') or ''))
        HX.rb_set_fields(st, t['id'], by, 'a client certificate of their own given',
                         client_cert=crt, client_key=seal_secret(key, bind=HX.RB_BIND))
        return {'message': 'taken', 'cert': crt, 'info': cert_info(crt)}
    if act == 'cert_forget':
        t = _rb_target_for(h, b)
        HX.rb_set_fields(st, t['id'], by, 'its client certificate forgotten', client_cert=None,
                         client_key=None)
        return {'message': 'forgotten'}
    if act == 'pin':
        t = _rb_target_for(h, b)
        pin = re.sub(r'[^0-9a-f]', '', str(b.get('pin') or '').lower())
        if len(pin) != 64:
            raise ApiError(400, 'the fingerprint is SHA-256: 64 hex digits')
        HX.rb_set_fields(st, t['id'], by, 'the server\'s certificate pinned (%s)' % pin, pin=pin,
                         tls_check='pin')
        return {'message': 'pinned'}
    if act == 'run':
        only = [str(x) for x in (b.get('ids') or [])] or None
        return {'message': 'the backup is being made', 'job': HX.rb_job_start(st, by, only)}
    if act == 'job':
        return {'job': dict(HX.RB_JOB)}
    if act == 'verify':
        return {'message': 'the backup is being tried',
                'job': HX.rb_verify_start(st, by, str(b.get('id') or '') or None,
                                          str(b.get('name') or '') or None)}
    if act == 'verified_forget':
        got = HX.meta(st, 'rbackup:verified', []) or []
        left = [] if b.get('all') else [x for x in got if abs(float(x.get('at') or 0) -
                                                             float(b.get('at') or 0)) > 0.001]
        HX.put_meta(st, 'rbackup:verified', left)
        st.add_event('backups', '%s cleared %d line(s) of the backups tried' % (
            by, len(got) - len(left)), who=by)
        return {'message': 'cleared', 'verified': [dict(x, node=node_name(),
                                                         target_name=x.get('target'))
                                                    for x in left]}
    if act == 'runs_forget':
        runs = HX.rb_history(st)
        left = [] if b.get('all') else [r for r in runs if not (
            abs(float(r.get('at') or 0) - float(b.get('at') or 0)) < 0.001 and
            str(r.get('file') or '') == str(b.get('file') or ''))]
        HX.put_meta(st, 'rbackup:runs', left)
        st.add_event('backups', '%s cleared %d line(s) of the backups sent (the backups stay)' % (
            by, len(runs) - len(left)), who=by)
        return {'message': 'cleared', 'runs': left}
    if act == 'list':
        return {'files': _rb_list(st, b.get('id'))}
    if act == 'delete_file':
        t = HX.rb_target(st, b.get('id'))
        name = os.path.basename(str(b.get('name') or ''))
        if not t or not HX.RB_NAME_RE.match(name):
            raise ApiError(404, 'no such backup')
        tr = HX.rb_transport(t)
        try:
            tr.open()
            for n in [name] + HX.rb_companions(name):
                try:
                    tr.delete(n)
                except Exception:                       # noqa: BLE001
                    pass
        finally:
            tr.close()
        st.add_event('backups', '%s deleted the backup %s at %s' % (by, name, t['name']), 'warn',
                     who=by)
        return {'message': 'deleted'}
    if act == 'restore_md':
        site = HX.rb_slug(CONFIG.get('site_name') or 'plexmon')
        node = HX.rb_slug(node_name())
        m = {'plexmon': __version__, 'made_words': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             'node': node_name(), 'host': socket.getfqdn(), 'encrypted': bool(HX.rb_password()),
             'iter': HX.RB_ITER, 'plex_db': bool(int(CONFIG.get('rbackup_plex_db', 1) or 0)),
             'file': 'plexmon-backup-%s-%s-%s.tar.gz%s' % (
                 site, node, datetime.now().strftime('%Y%m%d-%H%M%S'),
                 '.enc' if HX.rb_password() else '')}
        return {'md': HX.rb_restore_md(m), 'name': 'RESTORE-%s-%s.md' % (site, node),
                'file': m['file']}
    if act == 'restore':
        pw = str(b.get('password') or '')
        if b.get('upload'):
            up = re.sub(r'[^a-f0-9]', '', str(b.get('upload')))
            path = str(HX.upload_dir() / ('%s.bin' % up))
            if not up or not os.path.isfile(path):
                raise ApiError(404, 'the file sent is gone - send it again')

            def go():
                return HX.rb_restore(st, path, pw, by)
        else:
            t = HX.rb_target(st, b.get('id'))
            name = os.path.basename(str(b.get('name') or ''))
            if not t or not HX.RB_NAME_RE.match(name):
                raise ApiError(404, 'no such backup')

            def go():
                HX.says('fetched from %s' % t['name'], 0, 5)
                local = str(HX.upload_dir() / ('fetched-%s' % name))
                tr = HX.rb_transport(t)
                try:
                    tr.open()
                    tr.get(name, local)
                finally:
                    tr.close()
                try:
                    return HX.rb_restore(st, local, pw, by)
                finally:
                    try:
                        os.unlink(local)
                    except OSError:
                        pass
        return {'message': 'the backup is being put back', 'job': HX.db_job(
            'a backup put back', by, go)}
    raise ApiError(400, 'no such thing to do')


@route('POST', '/api/rbackup/upload')
def _rbackup_upload(h):
    s = h.me('fulladmin')
    ident = secrets.token_hex(8)
    path = str(HX.upload_dir() / ('%s.bin' % ident))
    h.body_to_file(path, 8 << 30)
    try:
        look = HX.rb_upload_look(path)
    except Exception as e:                              # noqa: BLE001
        os.unlink(path)
        raise ApiError(400, 'that is not a backup of plexmon: %s' % e)
    h.store.add_event('backups', '%s sent a backup to be put back' % s['name'], who=s['name'])
    return {'upload': ident, 'locked': look['locked'], 'manifest': look['manifest']}


@route('GET', '/api/rbackup/download')
def _rbackup_download(h):
    s = h.me('fulladmin')
    st = h.store
    if h.arg('id'):
        t = HX.rb_target(st, h.arg('id'))
        name = os.path.basename(h.arg('name'))
        if not t or not HX.RB_NAME_RE.match(name):
            raise ApiError(404, 'no such backup')
        work = tempfile.mkdtemp(prefix='plexmon-dl-', dir=str(STATE_DIR))
        try:
            tr = HX.rb_transport(t)
            try:
                tr.open()
                tr.get(name, os.path.join(work, name))
            finally:
                tr.close()
            h.send_file(os.path.join(work, name), name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return None
    work = tempfile.mkdtemp(prefix='plexmon-dl-', dir=str(STATE_DIR))
    try:
        path, m, _md = HX.rb_build(st, work)
        st.add_event('backups', '%s downloaded a backup made now (%s)' % (
            s['name'], HX.human_bytes(m['bytes'])), 'warn', who=s['name'])
        h.send_file(path, m['file'])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return None


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

# ---- the certificate taken away (2.1.1) ------------------------------------------------------
EXPORT_FORMATS = {
    'crt': ('certificate only (PEM)', False),
    'fullchain': ('certificate and its chain (PEM)', False),
    'der': ('certificate only (DER, .cer)', False),
    'p7b': ('certificate and its chain (PKCS#7, .p7b)', False),
    'key': ('private key only (PEM)', True),
    'pem': ('certificate, chain and private key in one PEM', True),
    'pfx': ('PFX / PKCS#12 - certificate, chain and private key', True),
}


def cert_export(h, prof, target, b, who):
    """The certificate as a file in the format asked: with the private key only
    for a full administrator who gives their own password again, every such
    export an event. A PFX always has a password; a PEM key may have one."""
    fmt = str(b.get('format') or '')
    if fmt not in EXPORT_FORMATS:
        raise ApiError(400, 'no such format')
    if not prof.get('cert'):
        raise ApiError(404, 'no certificate is kept for %s' % target)
    words, with_key = EXPORT_FORMATS[fmt]
    cert = prof['cert'].strip() + '\n'
    chain = (prof.get('chain') or '').strip()
    chain = chain + '\n' if chain else ''
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', str((prof.get('info') or {}).get('cn') or target)) or 'certificate'
    pw = str(b.get('password') or '')
    key = ''
    if with_key:
        acc = h.me('fulladmin')['account']
        k = h.throttle('password', int(CONFIG.get('login_tries') or 5), 5, name=acc['name'])
        if not password_ok(str(b.get('mine') or ''), acc.get('pw')):
            h.failed(k)
            raise ApiError(400, 'your own password is not right - the private key is given only '
                                'with it')
        key = unseal_secret(prof.get('key') or '', 'web:tls') or ''
        if not key:
            raise ApiError(404, 'the private key of %s is not kept here (a request made '
                                'elsewhere, or a certificate taken over without it)' % target)
        if fmt == 'pfx' and len(pw) < 4:
            raise ApiError(400, 'a PFX needs a password of 4 characters at least')
        if pw and pw != str(b.get('password2') or ''):
            raise ApiError(400, 'the two passwords are not the same')
    with tempfile.TemporaryDirectory(prefix='plexmon-export-') as tmp:
        os.chmod(tmp, 0o700)
        cf, chf, kf = (os.path.join(tmp, n) for n in ('c.pem', 'ch.pem', 'k.pem'))
        for path_, v in ((cf, cert), (chf, chain), (kf, key)):
            with open(path_, 'w') as f:
                f.write(v)
        env = {'PLEXMON_PW': pw}
        if fmt == 'crt':
            data, fn, mime = cert.encode(), name + '.crt', 'application/x-pem-file'
        elif fmt == 'fullchain':
            data, fn, mime = (cert + chain).encode(), name + '-fullchain.pem', \
                'application/x-pem-file'
        elif fmt == 'der':
            data = _openssl(['x509', '-in', cf, '-outform', 'DER'], what='the DER file')
            fn, mime = name + '.cer', 'application/pkix-cert'
        elif fmt == 'p7b':
            args = ['crl2pkcs7', '-nocrl', '-certfile', cf]
            if chain:
                args += ['-certfile', chf]
            data = _openssl(args + ['-outform', 'DER'], what='the PKCS#7 file')
            fn, mime = name + '.p7b', 'application/x-pkcs7-certificates'
        elif fmt in ('key', 'pem'):
            kdata = key.encode()
            if pw:
                kdata = _openssl(['pkey', '-in', kf, '-aes256', '-passout', 'env:PLEXMON_PW'],
                                 env=env, what='locking the key')
            if fmt == 'key':
                data, fn = kdata, name + '.key'
            else:
                data, fn = (cert + chain).encode() + kdata, name + '-with-key.pem'
            mime = 'application/x-pem-file'
        else:
            args = ['pkcs12', '-export', '-inkey', kf, '-in', cf, '-passout', 'env:PLEXMON_PW',
                    '-name', name]
            if chain:
                args += ['-certfile', chf]
            if b.get('legacy'):
                # Older Windows, macOS before 10.15, old Java and Android:
                # what they read (as Plex's own file).
                args += ['-certpbe', 'PBE-SHA1-3DES', '-keypbe', 'PBE-SHA1-3DES', '-macalg', 'sha1']
            else:
                args += ['-certpbe', 'AES-256-CBC', '-keypbe', 'AES-256-CBC', '-macalg', 'sha256']
            data = _openssl(args, env=env, what='the PFX file')
            fn, mime = name + '.pfx', 'application/x-pkcs12'
    if with_key:
        h.store.add_event('settings', '%s exported the certificate of %s WITH its private key '
                          '(%s%s)' % (who, target, words, ', locked with a password' if pw
                                      else ', not locked'), 'warn', who=who)
    else:
        h.store.add_event('settings', '%s exported the certificate of %s (%s)' % (
            who, target, words), 'info', who=who)
    return {'name': fn, 'mime': mime, 'b64': base64.b64encode(data).decode(), 'bytes': len(data),
            'with_key': with_key}

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
    if act == 'tls_export':
        return cert_export(h, prof, target, b, who)
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
            name = str(b.get('node') or node_name())
            on = bool(b.get('on'))
            maintenance_set(store, on, str(b.get('why') or 'by hand'), s['name'], name)
            return dict(upgrade_view(store), message='%s: maintenance %s' % (
                name, 'on' if on else 'off'))
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
        self.store = open_store(restore=True)
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
        ph = php_state()
        if ph.get('fixed') is False:
            problems.append('PHP %s does not have the php2shell fix (CVE-2026-109618, '
                            'CVE-2026-109619): ./install.sh --upgrade (or Settings → Upgrades) '
                            'brings it up to date' % ph.get('version'))
        try:
            tp = HX.ntp_problem(self.store)
            if tp:
                problems.append(tp)
        except Exception:                               # noqa: BLE001
            pass
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
                'maintenance': maintenance_now(self.store),
                'database': {'engine': 'sqlite', 'where': self.store.db.where(),
                             'size': self.store.db.size()},
                'counts': self.store.counts(), 'lang': CONFIG.get('ui_lang') or 'cs',
                'monitor': mon, 'cert': cert, 'acme_on': bool(CONFIG.get('acme')),
                'php': php_state(),
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
        try:
            maintenance_mark(bool(maintenance_row(self.store).get('on')))   # 2.3.0
        except Exception:                               # noqa: BLE001
            pass
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
            # In maintenance plexmon does nothing by itself (2.3.0): no renewal,
            # nothing on a schedule - the upgrade and the look for a new version go on.
            maint = bool(maintenance_now(self.store).get('on'))
            for fn in (upgrade_tick, online_tick) + (() if maint else (acme_tick,)) + (HX.tick,):
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
# The command line (otp's, as isemon's): plexmon --help
# ---------------------------------------------------------------------------
# Everything the pages can do, and the accounts and passwords besides - handled
# straight in the database, never through the API, because the moment the
# command line is most needed is the moment nobody can sign in. In colour on a
# terminal and plain anywhere else, `[ ok ]` and `[fail]` standing in for the
# colours.
# ---------------------------------------------------------------------------
# Colour - as isemon does it
# ---------------------------------------------------------------------------
# colorama when it is installed, and the same escape sequences written by hand
# when it is not. A nicety, never a dependency: a server being put right at two
# in the morning is not the one to find that the token server will not start
# because a colour library is missing. colorama adds a Windows console, a
# terminal reset however the program ends, and nothing else on Linux.
try:
    import colorama
    from colorama import Fore as _Fore, Style as _Style
    HAVE_COLORAMA = True
except Exception:                       # not installed, or installed broken
    colorama = None
    HAVE_COLORAMA = False

    class _Fore(object):
        RED = '\033[31m'; GREEN = '\033[32m'; YELLOW = '\033[33m'
        BLUE = '\033[34m'; MAGENTA = '\033[35m'; CYAN = '\033[36m'
        WHITE = '\033[37m'; RESET = '\033[39m'

    class _Style(object):
        BRIGHT = '\033[1m'; DIM = '\033[2m'; NORMAL = '\033[22m'
        RESET_ALL = '\033[0m'

_COLORAMA_READY = False


def start_colour():
    global _COLORAMA_READY
    if _COLORAMA_READY or not HAVE_COLORAMA:
        return
    _COLORAMA_READY = True
    try:
        if hasattr(colorama, 'just_fix_windows_console'):
            colorama.just_fix_windows_console()
        else:
            colorama.init()
    except Exception:
        pass


ANSI = re.compile(r'\033\[[0-9;]*m')
TERM_CTL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')


def term_safe(text):
    """What came from outside, safe on a terminal (1.0.139): a User-Name or a
    reason from a packet carried escapes that cleared the screen, set the
    title or wrote the clipboard. Colours are kept; any other control
    character is shown as \\xNN."""
    t = str(text)
    out, at = [], 0
    for m in ANSI.finditer(t):
        out.append(TERM_CTL.sub(lambda c: '\\x%02x' % ord(c.group(0)), t[at:m.start()]))
        out.append(m.group(0))
        at = m.end()
    out.append(TERM_CTL.sub(lambda c: '\\x%02x' % ord(c.group(0)), t[at:]))
    return ''.join(out)


def plain_len(text):
    """How wide something looks, the colour taken out."""
    return len(ANSI.sub('', str(text)))


def plain_pad(text, width):
    return str(text) + ' ' * max(0, width - plain_len(text))


class Palette(object):
    """Every colour the command line uses, in one place."""

    def __init__(self, on):
        self.on = bool(on)
        if self.on:
            start_colour()

        def code(*parts):
            return ''.join(parts) if self.on else ''

        self.c_ok = code(_Fore.GREEN)
        self.c_warn = code(_Fore.YELLOW)
        self.c_bad = code(_Style.BRIGHT, _Fore.RED)
        self.c_info = code(_Fore.CYAN)
        self.c_head = code(_Style.BRIGHT, _Fore.CYAN)
        self.c_key = code(_Fore.WHITE)
        self.c_addr = code(_Fore.MAGENTA)
        self.c_hi = code(_Style.BRIGHT)
        self.c_dim = code(_Style.DIM)
        self.off = code(_Style.RESET_ALL)
        try:
            self.width = max(60, min(120, shutil.get_terminal_size((100, 24)).columns))
        except Exception:
            self.width = 100

    def paint(self, text, colour):
        if not self.on or not colour:
            return str(text)
        return '%s%s%s' % (colour, text, self.off)

    def good(self, t):
        return self.paint(t, self.c_ok)

    def bad(self, t):
        return self.paint(t, self.c_bad)

    def warn(self, t):
        return self.paint(t, self.c_warn)

    def info(self, t):
        return self.paint(t, self.c_info)

    def dim(self, t):
        return self.paint(t, self.c_dim)

    soft = dim

    def head(self, t):
        return self.paint(t, self.c_head)

    def bold(self, t):
        return self.paint(t, self.c_hi)

    hi = bold

    def addr(self, t):
        return self.paint(t, self.c_addr)

    def ok_line(self, t):
        return (self.good('✔ ') if self.on else '[ ok ] ') + str(t)

    def warn_line(self, t):
        return (self.warn('! ') if self.on else '[warn] ') + str(t)

    def bad_line(self, t):
        return (self.bad('✘ ') if self.on else '[fail] ') + str(t)


def colour_wanted(no_color=False, stream=None):
    """What the person asked for, then NO_COLOR, then FORCE_COLOR, then the
    terminal itself - in that order, as isemon decides it."""
    if no_color or os.environ.get('NO_COLOR') is not None:
        return False
    if os.environ.get('FORCE_COLOR') or os.environ.get('CLICOLOR_FORCE'):
        return True
    if str(os.environ.get('TERM') or '').lower() in ('dumb', '') \
            and not os.environ.get('TERM_PROGRAM'):
        return False
    try:
        return bool((stream or sys.stdout).isatty())
    except Exception:
        return False


# ---- colour in every output (1.0.132) ---------------------------------------------
# With colour on, every table and every line the command line prints is
# coloured the same way - tables by their cells, other lines as they read:
# "key: value", addresses, times, and the words that say how a thing is.
# Whatever already carries its own colour is left as it is.
TABLE_PAL = None
TONE_GOOD = {'on', 'yes', 'active', 'ok', 'accept', 'accepted', 'running', 'answering',
             'in place', 'in service', 'done', 'synchronised', 'healthy', 'up', 'enabled',
             'signed in', 'good', 'valid', 'sent', 'taken', 'checked'}
TONE_BAD = {'off', 'no', 'reject', 'rejected', 'failed', 'fail', 'error', 'gone', 'down',
            'locked', 'locked out', 'expired', 'not answering', 'inactive', 'dead', 'disabled',
            'refused', 'missing', 'broken', 'lost', 'crit'}
TONE_WARN = {'pending', 'waiting', 'maintenance', 'in maintenance', 'paused', 'warn',
             'warning', 'not set up', 'unknown', 'stale', 'not yet'}
TONE_DIM = {'never', '-', '–', '(none)', 'none', 'as they come', 'not set', ''}
RE_ADDR = re.compile(r'(?<![\w.:])(\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?(?::\d{1,5})?)(?![\w.])')
RE_TIME = re.compile(r'\b(\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2})([ T]\d{2}:\d{2}(?::\d{2})?)?\b')


def tone_cell(text, pal):
    """One value coloured by what it says."""
    t = str(text)
    if not pal or not pal.on or ANSI.search(t):
        return t
    low = t.strip().lower()
    if low in TONE_GOOD:
        return pal.good(t)
    if low in TONE_BAD:
        return pal.bad(t)
    if low in TONE_WARN:
        return pal.warn(t)
    if low in TONE_DIM:
        return pal.dim(t)
    if RE_TIME.fullmatch(t.strip()):
        return pal.dim(t)
    return tone_words(t, pal)


def tone_words(t, pal):
    """Addresses and times inside a text."""
    t = RE_ADDR.sub(lambda m: pal.addr(m.group(1)), t)
    return RE_TIME.sub(lambda m: pal.dim(m.group(0)), t)


def _cut(text, width):
    """`text` no wider than `width` on the screen, its colour kept or - when it
    has to be cut - taken off."""
    if plain_len(text) <= width:
        return text
    return ANSI.sub('', text)[:width]


def table(rows, heads, widths=None):
    if not rows:
        return '  (none)'
    pal = TABLE_PAL
    cells = [[tone_cell(term_safe(c), pal) for c in r] for r in rows]
    cols = list(zip(*([heads] + [[str(c) for c in r] for r in cells])))
    w = widths or [min(48, max(plain_len(x) for x in col)) for col in cols]
    head = '  ' + '  '.join(h.upper().ljust(w[i]) for i, h in enumerate(heads))
    out = [pal.head(head) if pal and pal.on else head]
    for r in cells:
        out.append('  ' + '  '.join(plain_pad(_cut(str(c), w[i]), w[i])
                                    for i, c in enumerate(r)))
    return '\n'.join(out)


class ColourOut(object):
    """stdout, every line coloured on its way out unless it is already."""
    LINE_KV = re.compile(r'^(\s*)([A-Za-z][\w .()/\'-]{0,40}?)(:\s+|\s{2,})(\S.*)$')

    def __init__(self, stream, pal):
        self.s, self.pal, self.buf = stream, pal, ''

    def _line(self, ln):
        p = self.pal
        ln = term_safe(ln)
        if not p.on or not ln.strip() or ANSI.search(ln):
            return ln
        m = self.LINE_KV.match(ln)
        if m and not ln.lstrip().startswith(('$', '#')):
            return '%s%s%s%s' % (m.group(1), p.info(m.group(2)), m.group(3),
                                 tone_cell(m.group(4), p))
        return tone_words(ln, p)

    def write(self, text):
        self.buf += str(text)
        if '\n' in self.buf:
            done, self.buf = self.buf.rsplit('\n', 1)
            self.s.write('\n'.join(self._line(x) for x in done.split('\n')) + '\n')
        return len(text)

    def flush(self):
        if self.buf:
            self.s.write(self.buf)
            self.buf = ''
        self.s.flush()

    def __getattr__(self, name):
        return getattr(self.s, name)


def ask_password(prompt='password', twice=True, env='PLEXMON_PASSWORD'):
    said = os.environ.get(env) if env else None
    if said:
        return said
    first = getpass.getpass('%s: ' % prompt)
    if twice:
        again = getpass.getpass('and again: ')
        if first != again:
            raise SystemExit('those two are not the same')
    return first


def not_on_command_line(what, value, how):
    """A secret written out on the command line is refused (1.0.187): ps, the
    shell's history and the process accounting all keep it. - asks for it."""
    if value not in (None, '', '-') and not str(value).startswith('@env'):
        raise SystemExit('plexmon: %s is not taken on the command line, where ps and the shell\'s '
                         'history keep it: %s' % (what, how))


def ask_secret_value(key):
    """KEY=- of --set: from PLEXMON_VALUE_<KEY>, else asked (twice), else one line of stdin."""
    env = 'PLEXMON_VALUE_' + key.upper()
    if os.environ.get(env):
        return os.environ[env]
    if sys.stdin.isatty():
        return ask_password(key, twice=True, env=None)
    return sys.stdin.readline().rstrip('\r\n')


class HelpfulParser(argparse.ArgumentParser):
    """An ArgumentParser whose help is isemon's: two columns, in colour.
    Anything going wrong in the drawing falls back to argparse's own."""
    palette = None

    def format_help(self):
        try:
            return help_text(self, self.palette or Palette(False))
        except Exception:
            return super(HelpfulParser, self).format_help()

    def error(self, message):
        pal = self.palette or Palette(False)
        sys.stderr.write('%s\n' % pal.bad_line(message))
        sys.stderr.write(pal.dim('try:  plexmon --help') + '\n')
        raise SystemExit(2)


def help_option_words(action):
    names = ', '.join(action.option_strings)
    if action.nargs == 0:
        return names, ''
    if action.metavar:
        meta = ' '.join(action.metavar) if isinstance(action.metavar, tuple) else action.metavar
    elif action.choices:
        meta = '{%s}' % ','.join(str(c) for c in action.choices)
    else:
        meta = action.dest.upper()
    if action.nargs in ('+', '*'):
        meta += ' ...'
    return names, meta


def help_text(parser, pal):
    """The whole of --help, in the width of this terminal."""
    import textwrap
    width = pal.width
    out = ['%s %s' % (pal.head('plexmon'), pal.bold(__version__)),
           pal.dim('the Plex server\'s dashboard and its Settings: SMART, temperatures, now '
                   'playing, the certificates of nginx and Plex, ACME, upgrades'),
           '',
           '%s plexmon %s' % (pal.paint('usage:', pal.c_key), pal.dim('[--json] [--no-color] '
                                                                      '[what to do]')),
           pal.dim('       with nothing at all it says what it is doing')]
    every = [a for g in parser._action_groups for a in g._group_actions]
    left = min(34, max([len(' '.join(x for x in help_option_words(a) if x))
                        for a in every] or [20]) + 2)
    for group in parser._action_groups:
        actions = [a for a in group._group_actions if a.help != argparse.SUPPRESS]
        if not actions:
            continue
        out.append('')
        out.append(pal.head((group.title or '').upper()))
        if group.description:
            for line in textwrap.wrap(group.description, width - 2):
                out.append('  ' + pal.dim(line))
        for action in actions:
            names, meta = help_option_words(action)
            painted = pal.good(names) + (' ' + pal.warn(meta) if meta else '')
            says = action.help or ''
            if (action.default not in (None, False, '', argparse.SUPPRESS)
                    and action.nargs != 0 and action.default != []
                    and '[%s]' % action.default not in says):
                says += ' [%s]' % action.default
            lines = textwrap.wrap(says, max(24, width - left - 4)) or ['']
            if plain_len(painted) > left:
                out.append('  ' + painted)
                lines_at = 0
            else:
                out.append('  %s  %s' % (plain_pad(painted, left), lines[0]))
                lines_at = 1
            for more in lines[lines_at:]:
                out.append('  %s  %s' % (' ' * left, more))
    out.append('')
    out.append(pal.head('THINGS PEOPLE ACTUALLY TYPE'))
    wide = min(36, max(len(c.split('\n')[0]) for c, _ in HELP_EXAMPLES) + 2)
    for command, says in HELP_EXAMPLES:
        first, *rest = command.split('\n')
        if len(first) + 2 > wide:
            out.append('  ' + pal.info(first) + ('' if rest or not says else ''))
            for r in rest:
                out.append('  ' + pal.info(r))
            if says:
                out.append('  %s  %s' % (' ' * wide, pal.dim(says)))
        else:
            out.append('  %s%s' % (plain_pad(pal.info(first), wide), pal.dim(says)))
            for r in rest:
                out.append('  ' + pal.info(r))
    out.append('')
    for note in ('the pages are at https://<this machine>/smart/ (the dashboard) and '
                 '/smart/settings/ (Settings) · the configuration is %s and '
                 '/etc/plex-status/config.json' % CONFIG_FILE,
                 'typed with nothing at all it is --status; systemd runs the daemon as '
                 '--daemon (plexmon.service), and only one runs at a time',
                 'colour is off when this is piped, and with --no-color or NO_COLOR=1'
                 + ('' if HAVE_COLORAMA else ' (colorama is not installed; colour is '
                                             'written by hand)')):
        for line in textwrap.wrap(note, width - 4):
            out.append('  ' + pal.dim(line))
    out.append('')
    return '\n'.join(out)



# ---- acme push: certificates pushed here by an acme (2.6.0) ----------------------
#
# The receiver of acme push protocol 1 (docs/acme-push.md): an acme - the
# certificate manager - pairs with these pages once (a code shown here, or
# its key pasted), then signs every request with its Ed25519 key and sends
# each certificate's private key sealed to this installation's X25519 key.
# What it pushes is kept exactly where an upload on Certificates and TLS
# keeps it. Off until it is switched on; nothing else changes because of it.

PUSH_APP = 'plexmon'
PUSH_ALG = 'X25519-HKDF-SHA256-A256GCM'
PUSH_CODE_ABC = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
PUSH_CODE_LIFE = 15 * 60
PUSH_CODE_TRIES = 5
PUSH_SKEW = 300
PUSH_NONCE_LIFE = 900
PUSH_NONCES_MOST = 20000
PUSH_KEYS_MOST = 8
PUSH_BODY_MOST = 1 << 20
PUSH_LOCK = threading.Lock()

try:                                    # the fast way, where it is installed
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _c_ed25519
    from cryptography.hazmat.primitives.asymmetric import x25519 as _c_x25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _C_AESGCM
    from cryptography.exceptions import InvalidSignature as _C_BadSig, InvalidTag as _C_BadTag
    HAVE_CRYPTOGRAPHY = True
except Exception:                       # noqa: BLE001 - plain Python below does it all
    HAVE_CRYPTOGRAPHY = False


class PushRefused(Exception):
    """A refusal of the protocol: its code, its status, words."""

    def __init__(self, code, status, words):
        super().__init__(words)
        self.code = code
        self.status = status


# -- the primitives in plain Python (RFC 8032, RFC 7748, RFC 5869, FIPS 197 + SP 800-38D)

_ED_P = 2 ** 255 - 19
_ED_L = 2 ** 252 + 27742317777372353535851937790883648493
_ED_D = -121665 * pow(121666, _ED_P - 2, _ED_P) % _ED_P
_ED_I = pow(2, (_ED_P - 1) // 4, _ED_P)


def _ed_x(y, sign):
    if y >= _ED_P:
        return None
    x2 = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_P - 2, _ED_P) % _ED_P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_ED_P + 3) // 8, _ED_P)
    if (x * x - x2) % _ED_P:
        x = x * _ED_I % _ED_P
    if (x * x - x2) % _ED_P:
        return None
    if (x & 1) != sign:
        x = _ED_P - x
    return x


_ED_GY = 4 * pow(5, _ED_P - 2, _ED_P) % _ED_P
_ED_GX = _ed_x(_ED_GY, 0)
_ED_G = (_ED_GX, _ED_GY, 1, _ED_GX * _ED_GY % _ED_P)


def _ed_add(a, b):
    p = _ED_P
    x = (a[1] - a[0]) * (b[1] - b[0]) % p
    y = (a[1] + a[0]) * (b[1] + b[0]) % p
    c = 2 * a[3] * b[3] * _ED_D % p
    d = 2 * a[2] * b[2] % p
    e, f, g, h = y - x, d - c, d + c, y + x
    return (e * f % p, g * h % p, f * g % p, e * h % p)


def _ed_mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _ed_add(q, pt)
        pt = _ed_add(pt, pt)
        s >>= 1
    return q


def _ed_same(a, b):
    p = _ED_P
    return (a[0] * b[2] - b[0] * a[2]) % p == 0 and (a[1] * b[2] - b[1] * a[2]) % p == 0


def _ed_point(raw):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _ed_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _ED_P)


def _ed_pack(pt):
    zi = pow(pt[2], _ED_P - 2, _ED_P)
    x, y = pt[0] * zi % _ED_P, pt[1] * zi % _ED_P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def ed25519_verify(pub, msg, sig):
    """True when sig is pub's Ed25519 signature of msg."""
    if len(pub) != 32 or len(sig) != 64:
        return False
    if HAVE_CRYPTOGRAPHY:
        try:
            _c_ed25519.Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            return True
        except (_C_BadSig, ValueError):
            return False
    a = _ed_point(pub)
    r = _ed_point(sig[:32])
    s = int.from_bytes(sig[32:], 'little')
    if a is None or r is None or s >= _ED_L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), 'little') % _ED_L
    return _ed_same(_ed_mul(s, _ED_G), _ed_add(r, _ed_mul(h, a)))


def ed25519_public(seed):
    """The public key of a 32-byte seed (for the tests and the vectors)."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return _ed_pack(_ed_mul(a, _ED_G))


def ed25519_sign(seed, msg):
    """A signature (for the tests and the vectors - nothing here signs)."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    pub = _ed_pack(_ed_mul(a, _ED_G))
    r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), 'little') % _ED_L
    rp = _ed_pack(_ed_mul(r, _ED_G))
    k = int.from_bytes(hashlib.sha512(rp + pub + msg).digest(), 'little') % _ED_L
    return rp + ((r + k * a) % _ED_L).to_bytes(32, 'little')


def x25519(k, u):
    """X25519(k, u), both 32 bytes."""
    if HAVE_CRYPTOGRAPHY:
        priv = _c_x25519.X25519PrivateKey.from_private_bytes(bytes(k))
        if u == (9).to_bytes(32, 'little'):
            from cryptography.hazmat.primitives import serialization as _ser
            return priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
        try:
            return priv.exchange(_c_x25519.X25519PublicKey.from_public_bytes(bytes(u)))
        except ValueError:
            return bytes(32)
    p = _ED_P
    kb = bytearray(k)
    kb[0] &= 248
    kb[31] &= 127
    kb[31] |= 64
    n = int.from_bytes(bytes(kb), 'little')
    x1 = int.from_bytes(u, 'little') & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        bit = (n >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = x2 + z2, x2 - z2
        aa, bb = a * a % p, b * b % p
        e = aa - bb
        c, d = x3 + z3, x3 - z3
        da, cb = d * a % p, c * b % p
        x3 = (da + cb) * (da + cb) % p
        z3 = x1 * (da - cb) * (da - cb) % p
        x2 = aa * bb % p
        z2 = e * (aa + 121665 * e) % p
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, p - 2, p) % p).to_bytes(32, 'little')


def x25519_public(k):
    return x25519(k, (9).to_bytes(32, 'little'))


def hkdf_sha256(ikm, salt, info, length=32):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, t, i = b'', b'', 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _aes_sbox():
    box = [0] * 256
    p = q = 1
    while True:
        p = (p ^ (p << 1) ^ (0x1b if p & 0x80 else 0)) & 0xff
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xff
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) \
            ^ ((q << 4) | (q >> 4))
        box[p] = (x ^ 0x63) & 0xff
        if p == 1:
            break
    box[0] = 0x63
    return box


_AES_SBOX = _aes_sbox()


def _xt(b):
    return ((b << 1) ^ 0x1b) & 0xff if b & 0x80 else b << 1


class _Aes256Push:
    """AES-256, the encryption of one block - all GCM needs."""

    def __init__(self, key):
        if len(key) != 32:
            raise ValueError('an AES-256 key is 32 bytes')
        w = [list(key[i:i + 4]) for i in range(0, 32, 4)]
        rcon = 1
        for i in range(8, 60):
            t = list(w[i - 1])
            if i % 8 == 0:
                t = [_AES_SBOX[b] for b in t[1:] + t[:1]]
                t[0] ^= rcon
                rcon = _xt(rcon)
            elif i % 8 == 4:
                t = [_AES_SBOX[b] for b in t]
            w.append([a ^ b for a, b in zip(w[i - 8], t)])
        self.rk = [sum(w[r * 4:r * 4 + 4], []) for r in range(15)]

    def block(self, data):
        s = [a ^ b for a, b in zip(data, self.rk[0])]
        sb = _AES_SBOX
        for r in range(1, 15):
            s = [sb[b] for b in s]
            s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]
            if r != 14:
                m = []
                for c in range(4):
                    a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                    t = a0 ^ a1 ^ a2 ^ a3
                    m += [a0 ^ t ^ _xt(a0 ^ a1), a1 ^ t ^ _xt(a1 ^ a2),
                          a2 ^ t ^ _xt(a2 ^ a3), a3 ^ t ^ _xt(a3 ^ a0)]
                s = m
            s = [a ^ b for a, b in zip(s, self.rk[r])]
        return bytes(s)


def _gcm_mul(x, y):
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
    return z


def _gcm(key, nonce, data, aad, opening):
    """AES-256-GCM in plain Python: (out, tag); data is the ciphertext without
    its tag when opening."""
    aes = _Aes256Push(key)
    h = int.from_bytes(aes.block(bytes(16)), 'big')
    if len(nonce) != 12:
        raise ValueError('the GCM nonce is 12 bytes here')
    j0 = nonce + b'\x00\x00\x00\x01'
    out = bytearray()
    ctr = int.from_bytes(j0, 'big')
    for i in range(0, len(data), 16):
        ctr = (ctr & ~0xffffffff) | (((ctr & 0xffffffff) + 1) & 0xffffffff)
        ks = aes.block(ctr.to_bytes(16, 'big'))
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], ks))
    ct = bytes(data) if opening else bytes(out)
    g = 0
    for part in (aad, ct):
        for i in range(0, len(part), 16):
            g = _gcm_mul(g ^ int.from_bytes(part[i:i + 16].ljust(16, b'\x00'), 'big'), h)
    g = _gcm_mul(g ^ ((len(aad) * 8) << 64 | (len(ct) * 8)), h)
    tag = bytes(a ^ b for a, b in zip(g.to_bytes(16, 'big'), aes.block(j0)))
    return bytes(out), tag


def aes256gcm_open(key, nonce, ct_tag, aad):
    """The plaintext, or None when the tag does not match."""
    if len(ct_tag) < 16:
        return None
    if HAVE_CRYPTOGRAPHY:
        try:
            return _C_AESGCM(key).decrypt(nonce, ct_tag, aad)
        except (_C_BadTag, ValueError):
            return None
    out, tag = _gcm(key, nonce, ct_tag[:-16], aad, True)
    return out if hmac.compare_digest(tag, ct_tag[-16:]) else None


def aes256gcm_seal(key, nonce, plain, aad):
    if HAVE_CRYPTOGRAPHY:
        return _C_AESGCM(key).encrypt(nonce, plain, aad)
    out, tag = _gcm(key, nonce, plain, aad, False)
    return out + tag


# -- the protocol's own pieces

def push_canonical(op, app_id, key_id, ts, nonce, body):
    return '\n'.join(['acme-push/1', op, app_id, key_id, str(ts), nonce,
                      hashlib.sha256(body).hexdigest()]).encode('utf-8')


def push_key_id(pub):
    return hashlib.sha256(pub).hexdigest()[:16]


def push_code_hmac(code, words, *parts):
    k = ''.join(c for c in str(code).upper() if c in string.ascii_uppercase + string.digits)
    return hmac.new(k.encode(), '\n'.join(['acme-push/1 ' + words] + list(parts)).encode(),
                    hashlib.sha256).hexdigest()


def push_b64(text, size=None):
    try:
        raw = base64.b64decode(str(text or ''), validate=True)
    except (ValueError, binascii.Error):
        return None
    return raw if size is None or len(raw) == size else None


def push_parse_key(text):
    """acme's key as text - 'acme-push-ed25519 BASE64 comment' - as (pub32, comment)."""
    parts = str(text or '').strip().split(None, 2)
    if len(parts) < 2 or parts[0] != 'acme-push-ed25519':
        raise ValueError('that is not an acme key (it starts with acme-push-ed25519)')
    pub = push_b64(parts[1], 32)
    if pub is None or _ed_point(pub) is None:
        raise ValueError('the key in it is not an Ed25519 public key')
    return pub, (parts[2].strip()[:120] if len(parts) > 2 else '')


def push_seal_key(pem, app_id, app_pub, fingerprint, eph=None, nonce=None):
    """A key sealed as acme seals it (for the tests and the vectors)."""
    eph = eph or secrets.token_bytes(32)
    epk = x25519_public(eph)
    shared = x25519(eph, app_pub)
    aes = hkdf_sha256(shared, epk + app_pub, b'acme-push/1 key_pem')
    nonce = nonce or secrets.token_bytes(12)
    aad = ('acme-push/1 key\n%s\n%s' % (app_id, fingerprint.lower())).encode()
    ct = aes256gcm_seal(aes, nonce, pem.encode('utf-8'), aad)
    return {'alg': PUSH_ALG, 'epk': base64.b64encode(epk).decode(),
            'nonce': base64.b64encode(nonce).decode(), 'ct': base64.b64encode(ct).decode()}


def push_conf(store):
    got = _meta_json(store, 'acme_push:conf') or {}
    return {'on': bool(got.get('on')), 'other_names': bool(got.get('other_names')),
            'at': got.get('at'), 'by': got.get('by')}


def push_conf_set(store, by, **kw):
    c = push_conf(store)
    c.update(kw, at=time.time(), by=by)
    store.set_meta('acme_push:conf', json.dumps(c))
    return c


def push_box(store, make=False):
    """(app_id, X25519 private, public) - made once, the private half sealed."""
    got = _meta_json(store, 'acme_push:box') or {}
    priv = push_b64(unseal_secret(got.get('priv') or '', 'acme_push:box') or '', 32)
    if got.get('app_id') and priv:
        return got['app_id'], priv, x25519_public(priv)
    if not make:
        return None, None, None
    with PUSH_LOCK:
        got = _meta_json(store, 'acme_push:box') or {}
        priv = push_b64(unseal_secret(got.get('priv') or '', 'acme_push:box') or '', 32)
        if got.get('app_id') and priv:
            return got['app_id'], priv, x25519_public(priv)
        app_id = secrets.token_hex(16)
        priv = secrets.token_bytes(32)
        store.set_meta('acme_push:box', json.dumps({
            'app_id': app_id, 'priv': seal_secret(base64.b64encode(priv).decode(),
                                                  'acme_push:box'),
            'pub': base64.b64encode(x25519_public(priv)).decode(), 'at': time.time()}))
        return app_id, priv, x25519_public(priv)


def push_keys(store):
    got = _meta_json(store, 'acme_push:keys') or {}
    keys = got.get('keys') if isinstance(got.get('keys'), list) else []
    return [k for k in keys if isinstance(k, dict) and k.get('id') and k.get('pub')]


def push_keys_put(store, keys):
    store.set_meta('acme_push:keys', json.dumps({'keys': keys[-PUSH_KEYS_MOST:],
                                                 'at': time.time()}))


def push_key_add(store, pub, comment, name, how, by):
    kid = push_key_id(pub)
    keys = [k for k in push_keys(store) if k['id'] != kid]
    keys.append({'id': kid, 'pub': base64.b64encode(pub).decode(), 'comment': comment,
                 'name': str(name or '')[:160], 'how': how, 'at': time.time(), 'by': by})
    if len(keys) > PUSH_KEYS_MOST:
        gone = keys[0]
        store.add_event('settings', 'acme push: the oldest acme key (%s) was let go - %d are '
                        'kept at most' % (gone.get('name') or gone['id'], PUSH_KEYS_MOST),
                        'warn', who=by)
    push_keys_put(store, keys)
    return kid


def push_code_state(store):
    got = _meta_json(store, 'acme_push:code') or {}
    if not got.get('code'):
        return None
    if time.time() - float(got.get('at') or 0) > PUSH_CODE_LIFE or \
            int(got.get('tries') or 0) >= PUSH_CODE_TRIES:
        return None
    return got


def push_nonce_seen(store, kid, nonce):
    """True when (kid, nonce) came already in the last 900 s; else it is kept."""
    with PUSH_LOCK:
        got = _meta_json(store, 'acme_push:nonces') or {}
        seen = got.get('seen') if isinstance(got.get('seen'), dict) else {}
        t = time.time()
        seen = {k: v for k, v in seen.items() if isinstance(v, (int, float))
                and t - v < PUSH_NONCE_LIFE}
        key = kid + ':' + nonce
        if key in seen:
            return True
        seen[key] = t
        if len(seen) > PUSH_NONCES_MOST:
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-PUSH_NONCES_MOST:])
        store.set_meta('acme_push:nonces', json.dumps({'seen': seen}))
        return False


def push_refusal_note(store, op, code, words):
    store.set_meta('acme_push:refused', json.dumps({'at': time.time(), 'op': op,
                                                    'code': code, 'error': str(words)[:300]}))


def _push_fp(pem):
    m = PEM_CERT.search(str(pem or ''))
    if not m:
        return ''
    der = base64.b64decode(re.sub(r'-----[A-Z ]+-----|\s', '', m.group(0)))
    return hashlib.sha256(der).hexdigest()


def _push_key_type(info):
    m = re.match(r'^(EC|RSA) (\d+) bit', str((info or {}).get('key') or ''))
    return ('%s-%s' % (m.group(1).lower(), m.group(2))) if m else ''


def _push_names(info):
    out = [str(n).strip().lower() for n in (info or {}).get('san') or [] if str(n).strip()]
    cn = str((info or {}).get('cn') or '').strip().lower()
    if not out and cn:
        out = [cn]
    return out


def _push_holds(have, name):
    name = str(name).strip().lower().rstrip('.')
    for h in have:
        h = h.rstrip('.')
        if h == name:
            return True
        if h.startswith('*.') and '.' in name and name.split('.', 1)[1] == h[2:]:
            return True
    return False


def push_slots(store):
    """The places a certificate goes here: this machine's pages (nginx) - and
    Plex's own certificate, which follows them when it is switched on."""
    t = node_name()
    prof = tls_profile(store, t) or {}
    name = acme_name_of(store, t)
    cur = None
    how = str(prof.get('how') or '')
    if prof.get('cert'):
        info = prof.get('info') or {}
        ec = tls_ec_of(prof)
        alt = None
        if ec:
            ei = ec.get('ec_info') or {}
            alt = {'fingerprint': _push_fp(ec['ec_cert']), 'not_after': ei.get('not_after'),
                   'key_type': _push_key_type(ei)}
        cur = {'fingerprint': _push_fp(prof['cert']), 'not_before': info.get('not_before'),
               'not_after': info.get('not_after'), 'subject': info.get('subject'),
               'issuer': info.get('issuer'), 'names': _push_names(info),
               'key_type': _push_key_type(info), 'alt': alt}
    if not prof.get('cert'):
        managed = 'none'
    elif how.startswith('acme push'):
        managed = 'acme-push'
    elif how == 'ACME' and acme_care(store, t):
        managed = 'own-acme'
    else:
        managed = 'manual'
    slots = [{'id': 'node:' + t, 'kind': 'node', 'node': t,
              'title': 'the pages of %s (%s)' % (t, name), 'names': [name] if name else [],
              'key_types': ['ec', 'rsa'], 'dual': True, 'current': cur, 'how': how,
              'managed_by': managed, 'installable': bool(name), 'why_not':
              '' if name else 'the name of these pages is not known'}]
    if CONFIG.get('plex_cert'):
        pn = str(CONFIG.get('plex_cert_domain') or '').strip().lower() or name
        slots.append({'id': 'plex', 'kind': 'other', 'node': None,
                      'title': 'Plex Media Server\'s own certificate (32400)',
                      'names': [pn] if pn else [], 'key_types': ['rsa', 'ec'], 'dual': False,
                      'current': dict(cur, alt=None) if cur else None, 'how': how,
                      'managed_by': 'follows:node:' + t, 'installable': False,
                      'why_not': 'it takes the certificate of these pages by itself: push to '
                                 'node:' + t})
    return slots


def push_hello(store):
    c = push_conf(store)
    out = {'ok': True, 'protocol': [1], 'app': PUSH_APP, 'version': __version__,
           'on': c['on']}
    if not c['on']:
        return out
    app_id, _priv, pub = push_box(store, make=True)
    t = node_name()
    out.update(app_id=app_id, enc_key=base64.b64encode(pub).decode(), enc_alg=PUSH_ALG,
               node=t, cluster=False, nodes=[t], vip={'on': False, 'fqdn': '', 'address': ''},
               slots=[s['id'] for s in push_slots(store)], paired=len(push_keys(store)),
               pairing=bool(push_code_state(store)))
    return out


def push_check(store, op, raw, hdr, app_id):
    """The signature, in the order of the protocol: the key it was made with."""
    ver = str(hdr.get('Version') or '').strip()
    if ver not in ('', '1'):
        raise PushRefused('version', 400, 'this speaks acme push protocol 1, not %s' % ver[:20])
    kid = str(hdr.get('Key') or '')
    ts = str(hdr.get('Time') or '')
    nonce = str(hdr.get('Nonce') or '')
    sig = push_b64(hdr.get('Signature'), 64)
    if not re.match(r'^[0-9a-f]{16}\Z', kid) or not re.match(r'^[0-9]{1,12}\Z', ts) or \
            not re.match(r'^[A-Za-z0-9_-]{16,64}\Z', nonce) or sig is None:
        raise PushRefused('unsigned', 401, 'the request is not signed (a header is missing or '
                                           'not in its shape)')
    if op == 'pair':
        try:
            body = json.loads(raw.decode('utf-8'), parse_constant=_no_constant)
            pub, comment = push_parse_key(body.get('acme_key'))
        except (ValueError, AttributeError, UnicodeDecodeError, RecursionError):
            raise PushRefused('bad_request', 400, 'the pairing does not carry acme\'s key')
        if push_key_id(pub) != kid:
            raise PushRefused('unknown_key', 401, 'the key id is not the one of the key sent')
        key = {'id': kid, 'pub': base64.b64encode(pub).decode(), 'comment': comment}
    else:
        key = next((k for k in push_keys(store) if k['id'] == kid), None)
        if not key or push_key_id(push_b64(key['pub'], 32) or b'') != kid:
            raise PushRefused('unknown_key', 401, 'that acme key is not paired here')
    off = int(ts) - time.time()
    if abs(off) > PUSH_SKEW:
        raise PushRefused('stale', 401, 'the request\'s time is %d s %s this machine\'s - a '
                                        'clock is wrong' % (abs(off), 'ahead of' if off > 0
                                                            else 'behind'))
    if not ed25519_verify(push_b64(key['pub'], 32), push_canonical(op, app_id, kid, ts, nonce,
                                                                   raw), sig):
        raise PushRefused('bad_signature', 401, 'the signature does not match')
    if push_nonce_seen(store, kid, nonce):
        raise PushRefused('replay', 401, 'that request came already (its nonce was seen)')
    try:
        body = json.loads(raw.decode('utf-8'), parse_constant=_no_constant) if raw else {}
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise PushRefused('bad_request', 400, 'the body is not JSON')
    if not isinstance(body, dict):
        raise PushRefused('bad_request', 400, 'the body is not a JSON object')
    return key, nonce, body


def push_who(key):
    return 'acme push (%s)' % (key.get('name') or key.get('comment') or key['id'])


def push_pair(store, key, nonce, body, app_id, pub):
    code = push_code_state(store)
    if not code:
        raise PushRefused('pairing_closed', 403, 'no pairing code is waiting here (Pair with an '
                                                 'acme makes one; it lasts 15 minutes)')
    plain = unseal_secret(code['code'], 'acme_push:code') or ''
    want = push_code_hmac(plain, 'pair', app_id, key['id'], nonce)
    if not plain or not hmac.compare_digest(want, str(body.get('proof') or '').lower()):
        with PUSH_LOCK:
            got = _meta_json(store, 'acme_push:code') or {}
            got['tries'] = int(got.get('tries') or 0) + 1
            store.set_meta('acme_push:code', json.dumps(got))
        left = PUSH_CODE_TRIES - got['tries']
        store.add_event('settings', 'acme push: a pairing with a wrong code (%s)%s' % (
            key['comment'] or key['id'], ' - %d tries left' % left if left > 0 else
            ' - the code is gone'), 'warn', who='acme push')
        raise PushRefused('bad_code', 403, 'that is not the pairing code waiting here')
    store.set_meta('acme_push:code', '')
    name = str(body.get('acme_name') or key['comment'] or '').strip()[:160]
    push_key_add(store, push_b64(key['pub'], 32), key['comment'], name, 'paired with a code',
                 'acme push')
    enc = base64.b64encode(pub).decode()
    store.add_event('settings', 'acme push: paired with %s (key %s) - it may put certificates '
                    'in here now' % (name or key['comment'] or 'an acme', key['id']), 'warn',
                    who='acme push')
    return {'ok': True, 'app': PUSH_APP, 'app_id': app_id, 'key_id': key['id'], 'enc_key': enc,
            'enc_alg': PUSH_ALG, 'confirm': push_code_hmac(plain, 'paired', app_id, key['id'],
                                                           nonce, enc)}


def push_inventory(store, app_id):
    t = node_name()
    return {'ok': True, 'app': PUSH_APP, 'version': __version__, 'app_id': app_id,
            'cluster': {'on': False, 'node': t, 'nodes': [t],
                        'vip': {'on': False, 'fqdn': '', 'address': ''}},
            'other_names': push_conf(store)['other_names'], 'slots': push_slots(store)}


def _push_one(store, part, app_id, priv, what):
    """One certificate of an install, checked: (bundle, fingerprint)."""
    if not isinstance(part, dict):
        raise PushRefused('bad_request', 400, '%s is not an object' % what)
    pem = part.get('cert_pem')
    chain = part.get('chain_pem') or ''
    fp = str(part.get('fingerprint') or '').strip().lower()
    if not isinstance(pem, str) or not isinstance(chain, str) or \
            not re.match(r'^[0-9a-f]{64}\Z', fp):
        raise PushRefused('bad_request', 400, '%s needs cert_pem and its fingerprint' % what)
    try:
        have = _push_fp(pem)
    except (ValueError, binascii.Error):
        have = ''
    if not have:
        raise PushRefused('bad_cert', 422, '%s cannot be read' % what)
    if have != fp:
        raise PushRefused('bad_request', 400, 'the fingerprint is not the one of %s' % what)
    k = part.get('key')
    if not isinstance(k, dict) or k.get('alg') != PUSH_ALG:
        raise PushRefused('bad_request', 400, 'the key of %s is not sealed as %s' % (what,
                                                                                      PUSH_ALG))
    epk = push_b64(k.get('epk'), 32)
    nonce = push_b64(k.get('nonce'), 12)
    ct = push_b64(k.get('ct'))
    if epk is None or nonce is None or not ct:
        raise PushRefused('bad_request', 400, 'the sealed key of %s is not in its shape' % what)
    shared = x25519(priv, epk)
    if shared == bytes(32):
        raise PushRefused('bad_key', 400, 'the sealed key of %s does not open' % what)
    aes = hkdf_sha256(shared, epk + x25519_public(priv), b'acme-push/1 key_pem')
    plain = aes256gcm_open(aes, nonce, ct, ('acme-push/1 key\n%s\n%s' % (app_id, fp)).encode())
    if plain is None:
        raise PushRefused('bad_key', 400, 'the sealed key of %s does not open: it was sealed '
                                          'to another key (pair again) or for another '
                                          'certificate' % what)
    try:
        key_pem = plain.decode('utf-8')
        m = PEM_CERT.search(pem)
        leaf = m.group(0) + '\n'
        if _pub_of_key(key_pem) != _pub_of_cert(leaf):
            raise PushRefused('key_mismatch', 422, 'the key is not the one of %s' % what)
        got = cert_bundle(leaf + chain, key_pem)
    except PushRefused:
        raise
    except (ValueError, UnicodeDecodeError, OSError) as e:
        raise PushRefused('bad_cert', 422, '%s cannot be read: %s' % (what, e))
    info = got['info']
    t = time.time()
    if info.get('not_before') and info['not_before'] > t + 60:
        raise PushRefused('not_yet_valid', 422, 'the certificate (%s) is valid only from %s'
                          % (what, stamp(info['not_before'])))
    if not info.get('not_after') or info['not_after'] < t:
        raise PushRefused('expired', 422, 'the certificate (%s) ran out on %s'
                          % (what, stamp(info.get('not_after'))))
    return got, fp


def push_install(store, key, body, app_id, priv):
    who = push_who(key)
    want = body.get('slots') if body.get('slots') is not None else ['auto']
    if isinstance(want, str):
        want = [want]
    if not isinstance(want, list) or not want or len(want) > 50 or \
            not all(isinstance(x, str) and 0 < len(x) <= 200 for x in want):
        raise PushRefused('bad_request', 400, 'slots is a list of slot ids')
    if body.get('alt') is not None and not isinstance(body.get('alt'), dict):
        raise PushRefused('bad_request', 400, 'alt is an object')
    main, fp = _push_one(store, body, app_id, priv, 'the certificate')
    alt, afp = (None, '')
    if body.get('alt'):
        alt, afp = _push_one(store, body['alt'], app_id, priv, 'the twin')
        if _push_key_type(alt['info'])[:2] == _push_key_type(main['info'])[:2]:
            raise PushRefused('bad_request', 400, 'the twin has the same kind of key as the '
                                                  'certificate (ECDSA + RSA is a dual one)')
        if sorted(_push_names(alt['info'])) != sorted(_push_names(main['info'])):
            raise PushRefused('bad_request', 400, 'the twin is not for the same names')
    have = _push_names(main['info'])
    slots = push_slots(store)
    by_id = {s['id']: s for s in slots}
    t = node_name()
    picked = []
    loose = bool(body.get('check_names') is False and push_conf(store)['other_names'])
    for w in want:
        w = w.strip()
        if w == 'auto':
            hit = [s for s in slots if s['installable'] and s['names']
                   and all(_push_holds(have, n) for n in s['names'])]
            if not hit:
                raise PushRefused('names', 422, 'no slot here takes a certificate for %s (the '
                                                'pages are %s)' % (', '.join(have) or '?',
                                                                   ', '.join(by_id['node:' + t]
                                                                             ['names'])))
            picked += hit
            continue
        if w in ('nodes', 'this'):
            sid = 'node:' + t
        else:
            sid = w if w in by_id else ('node:' + w if 'node:' + w in by_id else w)
        if sid not in by_id:
            raise PushRefused('no_slot', 404, 'there is no slot %s here (there are %s)'
                              % (w[:80], ', '.join(by_id)))
        s = by_id[sid]
        if not s['installable']:
            raise PushRefused('not_installable', 409, '%s cannot take it: %s'
                              % (sid, s['why_not']))
        if not loose:
            miss = [n for n in s['names'] if not _push_holds(have, n)]
            if miss:
                raise PushRefused('names', 422, 'the certificate does not hold %s, the name '
                                                'of %s' % (', '.join(miss), sid))
        picked.append(s)
    seen, todo = set(), []
    for s in picked:
        if s['id'] not in seen:
            seen.add(s['id'])
            todo.append(s)
    # The RSA one is the main one here (nginx's first, and what Plex takes);
    # an ECDSA twin goes beside it as the pages' second certificate.
    first, second = main, alt
    if alt and _push_key_type(main['info']).startswith('ec'):
        first, second = alt, main
    out, said = [], []
    for s in todo:
        prof = tls_profile(store, t) or {}
        ec = tls_ec_of(prof)
        same = prof.get('cert') and str(prof.get('how') or '').startswith('acme push') and \
            {_push_fp(prof['cert']), _push_fp(ec['ec_cert']) if ec else ''} == \
            {_push_fp(first['cert']), _push_fp(second['cert']) if second else ''}
        row = {'slot': s['id'], 'node': s['node'], 'names': s['names'], 'fingerprint': fp,
               'not_after': main['info'].get('not_after')}
        if same and not body.get('force'):
            out.append(dict(row, state='already'))
            said.append('already there for %s' % s['id'])
            continue
        prof.update(cert=first['cert'], chain=first['chain'], root=first['root'],
                    key=seal_secret(first['key'], 'web:tls'), info=first['info'],
                    chain_info=first['chain_info'], root_info=first['root_info'],
                    verify=first['verify'], how='acme push: %s' % (
                        key.get('name') or key.get('comment') or key['id']))
        for k in ('pending_key', 'csr', 'ec_cert', 'ec_chain', 'ec_key', 'ec_info', 'ec_for'):
            prof.pop(k, None)
        if second:
            prof.update(ec_cert=second['cert'], ec_chain=second['chain'],
                        ec_key=seal_secret(second['key'], 'web:tls'), ec_info=second['info'],
                        ec_for=hashlib.sha256(first['cert'].encode()).hexdigest())
        prof.setdefault('preset', 'standard')
        prof.setdefault('dh', 'none')
        prof.setdefault('hsts', 0)
        tls_save(store, t, prof, who)
        acme_care_set(store, t, False, who, 'a certificate came from acme push')
        info = main['info']
        store.add_event('settings', '%s put a certificate in for %s: %s, %s (until %s)%s' % (
            who, s['id'], ', '.join(_push_names(info)), info.get('issuer'),
            stamp(info.get('not_after')), ' + its %s twin' % _push_key_type(alt['info'])
            if alt else ''), 'info', who=who)
        out.append(dict(row, state='installed'))
        said.append('put in for %s - nginx takes it within seconds%s' % (
            s['id'], ', and Plex its own after it' if CONFIG.get('plex_cert') else ''))
    store.set_meta('acme_push:last', json.dumps({
        'at': time.time(), 'acme': key.get('name') or key.get('comment') or key['id'],
        'key_id': key['id'], 'installed': out, 'names': have,
        'not_after': main['info'].get('not_after'), 'issuer': main['info'].get('issuer'),
        'alt': _push_key_type(alt['info']) if alt else '',
        'key_type': _push_key_type(main['info']), 'said': '; '.join(said)}))
    return {'ok': True, 'installed': out, 'said': '; '.join(said)}


def push_serve(h, op):
    """One request of an acme, relayed by the page as it came."""
    store = h.store
    try:
        c = push_conf(store)
        if op == 'hello':
            return h.send_json(push_hello(store))
        b = h.body()
        raw = push_b64(b.get('body') if isinstance(b.get('body'), str) else '')
        if raw is None:
            raise PushRefused('bad_request', 400, 'the page did not hand the body on whole')
        if len(raw) > PUSH_BODY_MOST:
            raise PushRefused('too_big', 413, 'the request is over 1 MiB')
        hdr = b.get('headers') if isinstance(b.get('headers'), dict) else {}
        hdr = {k: str(v)[:300] for k, v in hdr.items() if isinstance(v, (str, int))}
        if not c['on']:
            raise PushRefused('off', 403, 'these pages take no certificates from acme (it is '
                                          'switched off on Certificates and TLS)')
        if op != 'pair' and not push_keys(store):
            raise PushRefused('not_paired', 401, 'no acme is paired here yet')
        app_id, priv, pub = push_box(store, make=True)
        key, nonce, body = push_check(store, op, raw, hdr, app_id)
        if op == 'pair':
            return h.send_json(push_pair(store, key, nonce, body, app_id, pub))
        key = dict(key)
        if op == 'inventory':
            return h.send_json(push_inventory(store, app_id))
        return h.send_json(push_install(store, key, body, app_id, priv))
    except PushRefused as e:
        log('acme push: %s refused (%s)' % (op, e.code), 'warn')
        if op == 'install' or e.code in ('bad_signature', 'unknown_key', 'bad_code', 'replay'):
            push_refusal_note(store, op, e.code, e)
        return h.send_json({'ok': False, 'code': e.code, 'error': str(e)}, e.status)
    except ApiError as e:
        return h.send_json({'ok': False, 'code': 'too_big' if e.status == 413 else
                            'bad_request', 'error': str(e)}, e.status)


@route('GET', '/api/acme-push/hello')
def _push_hello(h):
    return push_serve(h, 'hello')


@route('POST', '/api/acme-push/pair')
def _push_pair(h):
    return push_serve(h, 'pair')


@route('POST', '/api/acme-push/inventory')
def _push_inventory(h):
    return push_serve(h, 'inventory')


@route('POST', '/api/acme-push/install')
def _push_install(h):
    return push_serve(h, 'install')


def push_view(store, me=None):
    """The card Certificates from acme: what it shows (no secrets)."""
    c = push_conf(store)
    code = push_code_state(store)
    full = bool(me) and ROLE_RANK.get(me.get('role'), 0) >= ROLE_RANK['fulladmin']
    app_id, _p, pub = push_box(store)
    return {'on': c['on'], 'other_names': c['other_names'], 'at': c['at'], 'by': c['by'],
            'app': PUSH_APP, 'app_id': app_id or '',
            'enc_key': base64.b64encode(pub).decode() if pub else '',
            'keys': [{k: x.get(k) for k in ('id', 'comment', 'name', 'how', 'at', 'by')}
                     for x in push_keys(store)],
            'pairing': {'until': float(code['at']) + PUSH_CODE_LIFE,
                        'tries_left': PUSH_CODE_TRIES - int(code.get('tries') or 0)}
            if code else None,
            'slots': push_slots(store), 'last': _meta_json(store, 'acme_push:last'),
            'refused': _meta_json(store, 'acme_push:refused'), 'full': full,
            'engine': 'cryptography' if HAVE_CRYPTOGRAPHY else 'Python'}


@route('GET', '/api/acme-push')
def _push_card(h):
    me = h.me()
    return {'push': push_view(h.store, me)}


@route('POST', '/api/acme-push')
def _push_card_do(h):
    me = h.me('fulladmin')
    who = me['name']
    store = h.store
    b = h.body()
    act = str(b.get('action') or '')
    said = ''
    extra = {}
    if act in ('on', 'off'):
        push_conf_set(store, who, on=act == 'on')
        if act == 'on':
            push_box(store, make=True)
        else:
            store.set_meta('acme_push:code', '')
        store.add_event('settings', '%s: certificates from acme %s' % (
            who, 'are taken now (acme push)' if act == 'on' else 'are no longer taken'),
            'warn' if act == 'on' else 'info', who=who)
        said = 'certificates from acme are taken now' if act == 'on' else \
            'certificates from acme are no longer taken'
    elif act == 'other_names':
        on = str(b.get('mode') or '') == 'on'
        push_conf_set(store, who, other_names=on)
        store.add_event('settings', '%s: acme push %s certificates of other names' % (
            who, 'may put in' if on else 'may no longer put in'), 'warn' if on else 'info',
            who=who)
        said = 'certificates of other names are taken' if on else \
            'only certificates holding the names of these pages are taken'
    elif act == 'pair':
        if not push_conf(store)['on']:
            raise ApiError(409, 'switch Take certificates from acme on first')
        push_box(store, make=True)
        code = ''.join(secrets.choice(PUSH_CODE_ABC) for _ in range(16))
        store.set_meta('acme_push:code', json.dumps({
            'code': seal_secret(code, 'acme_push:code'), 'at': time.time(), 'tries': 0,
            'by': who}))
        store.add_event('settings', '%s made a pairing code for an acme (15 minutes)' % who,
                        'info', who=who)
        extra['code'] = '-'.join(code[i:i + 4] for i in range(0, 16, 4))
        said = 'the pairing code is made: give it to acme within 15 minutes'
    elif act == 'unpair':
        store.set_meta('acme_push:code', '')
        said = 'the pairing code is taken back'
    elif act == 'paste':
        if not push_conf(store)['on']:
            raise ApiError(409, 'switch Take certificates from acme on first')
        try:
            pub, comment = push_parse_key(b.get('note'))
        except ValueError as e:
            raise ApiError(400, str(e))
        push_box(store, make=True)
        label = str(b.get('label') or '').strip()[:160]
        kid = push_key_add(store, pub, comment, label or comment, 'pasted', who)
        store.add_event('settings', '%s pasted an acme key (%s, %s): it may put certificates in '
                        'here now' % (who, comment or 'no comment', kid), 'warn', who=who)
        said = 'acme\'s key is taken (%s)' % kid
    elif act == 'forget':
        kid = str(b.get('name') or '')
        keys = push_keys(store)
        gone = [k for k in keys if k['id'] == kid]
        if not gone:
            raise ApiError(404, 'no such acme key here')
        push_keys_put(store, [k for k in keys if k['id'] != kid])
        store.add_event('settings', '%s forgot the acme key %s (%s): nothing more is taken from '
                        'it' % (who, kid, gone[0].get('name') or gone[0].get('comment') or '-'),
                        'warn', who=who)
        said = 'that acme is forgotten: nothing more is taken from it'
    else:
        raise ApiError(400, 'no such action')
    return dict({'message': said, 'push': push_view(store, me)}, **extra)


import plexmon_plex as PX  # noqa: E402
PX.bind(sys.modules[__name__])
import plexmon_host as HX  # noqa: E402
HX.bind(sys.modules[__name__])


def build_parser():
    p = HelpfulParser(prog='plexmon', add_help=False, allow_abbrev=False,
                      usage='plexmon [--json] [--no-color] [what to do]',
                      description='the Plex server\'s dashboard and its Settings',
                      formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(
                          prog, max_help_position=36, width=100))
    g = p.add_argument_group('EVERYWHERE')
    g.add_argument('-h', '--help', action='store_true', help='this, and stop')
    g.add_argument('--version', action='store_true', help='which version this is, and stop')
    g.add_argument('--no-color', '--no-colour', action='store_true', dest='no_color',
                   help='plain text, no colour')
    g.add_argument('--json', action='store_true', help='answer as JSON where it makes sense')
    g.add_argument('--daemon', action='store_true',
                   help='run the daemon in the foreground (what systemd starts as '
                        'plexmon.service); one at a time')
    g.add_argument('-v', '--debug', action='store_true', help='say everything, this run only')
    g.add_argument('--yes', action='store_true', help='do it, ask nothing')
    g = p.add_argument_group('LOOKING', 'Reading what it knows. None of these change anything.')
    g.add_argument('--status', action='store_true', help='what it is doing, in one screen')
    g.add_argument('--check', action='store_true',
                   help='a health check, every part of it: the dashboard\'s whole chain, '
                        'Settings, nginx, the certificate')
    g.add_argument('--diag', action='store_true',
                   help='the dashboard\'s API latency, each disk\'s power state and the '
                        'settings in force')
    g.add_argument('--events', action='store_true', help='Settings\' events: sign-ins, '
                   'changes, certificates, upgrades')
    g.add_argument('--log', action='store_true', help='the tail of /var/log/plexmon/plexmon.log')
    g.add_argument('--limit', type=int, default=40, metavar='N', help='how many rows [40]')
    g.add_argument('--search', metavar='TEXT', default='', help='only rows holding this')
    g = p.add_argument_group('THE DISKS AND THE DASHBOARD',
                             'A normal run never wakes a sleeping disk; only --wake does.')
    g.add_argument('--wake', action='store_true',
                   help='tell the running daemon to wake the disks and read SMART')
    g.add_argument('--refresh', action='store_true',
                   help='read SMART again from the disks already spinning (wakes nothing)')
    g.add_argument('--oneshot', action='store_true',
                   help='one collection without the service, a summary, and stop '
                        '(with --wake: SMART read too)')
    g = p.add_argument_group('THE TELEVISIONS', 'Apple TV, LG and Samsung remotes '
                             '(atv_enable in /etc/plex-status/config.json).')
    g.add_argument('--atv', action='store_true',
                   help='the televisions known, and which are paired')
    g.add_argument('--atv-scan', action='store_true', help='search the network again')
    g.add_argument('--atv-probe', metavar='ADDRESS',
                   help='what kind of television answers at an address')
    g.add_argument('--atv-pair', metavar='TV', help='pair with a television (asks for its PIN)')
    g.add_argument('--atv-key', nargs=2, metavar=('TV', 'KEY'),
                   help='one raw remote key to a Samsung')
    g.add_argument('--atv-unpair', metavar='TV', help='forget one television')
    g.add_argument('--atv-unpair-all', action='store_true', help='forget every television')
    g = p.add_argument_group('ACCOUNTS OF SETTINGS',
                             'Straight in the database - they work when nobody can sign in.')
    g.add_argument('--users', action='store_true', help='who may open Settings')
    g.add_argument('--add-user', metavar='NAME', help='a new account (the password asked, or '
                   'PLEXMON_PASSWORD; --role, --note)')
    g.add_argument('--role', choices=ROLES_ALL, help='with --add-user / --set-role [fulladmin]')
    g.add_argument('--note', metavar='TEXT', default='', help='with --add-user: a note')
    g.add_argument('--passwd', metavar='NAME', help='a new password for an account; its '
                   'sign-ins end')
    g.add_argument('--set-role', metavar='NAME', help='change the role of an account (--role)')
    g.add_argument('--disable-user', metavar='NAME', help='it may no longer sign in')
    g.add_argument('--enable-user', metavar='NAME', help='it may sign in again')
    g.add_argument('--del-user', metavar='NAME', help='take an account away')
    g.add_argument('--reset-2fa', metavar='NAME', help='it sets its second step up again')
    g.add_argument('--admin-2fa', choices=('on', 'off', 'status'),
                   help='a second step (an authenticator app) for every sign-in to Settings')
    g.add_argument('--sessions', action='store_true', help='who is signed in to Settings now')
    g = p.add_argument_group('SETTINGS', 'The same settings as the pages, checked the same way.')
    g.add_argument('--settings', action='store_true', help='every setting, and where it comes '
                   'from')
    g.add_argument('--set', action='append', metavar='KEY=VALUE',
                   help='change one (KEY=- asks for a secret); as often as you like')
    g.add_argument('--unset', action='append', metavar='KEY',
                   help='back to config.json\'s, or the built-in default')
    g = p.add_argument_group('CERTIFICATES AND TLS',
                             'The certificate the Plex address answers with (nginx), and Plex '
                             'Media Server\'s own.')
    g.add_argument('--cert-status', '--web-status', action='store_true', dest='cert_status',
                   help='the certificate, its TLS, and whether nginx has it')
    g.add_argument('--cert-import', nargs='+', metavar='FILE',
                   help='put a certificate in: PEM files (certificate, key, chain - any order) '
                        'or one .pfx/.p12 (its password from PLEXMON_PFX_PASSWORD)')
    g.add_argument('--cert-selfsigned', metavar='NAME', help='a self-signed certificate for NAME')
    g.add_argument('--tls-preset', choices=sorted(TLS_PRESETS), help='how TLS is spoken')
    g.add_argument('--tls-dh', choices=sorted(DH_WORDS), help='the DH parameters')
    g.add_argument('--tls-hsts', type=int, metavar='SECONDS', help='HSTS (0: off)')
    g.add_argument('--tls-apply', action='store_true',
                   help='put in again what is kept, now (nginx -t, reload)')
    g.add_argument('--nginx-adopt', action='store_true',
                   help='hand the Plex vhost over to plexmon (install.sh does it, once)')
    g.add_argument('--plex-cert', choices=('on', 'off', 'now', 'status'),
                   help='Plex Media Server uses the certificate too (its custom certificate)')
    g = p.add_argument_group('AUTOMATIC CERTIFICATES', 'Let\'s Encrypt or another ACME CA.')
    g.add_argument('--acme', action='store_true', help='how the certificate by ACME stands')
    g.add_argument('--acme-order', action='store_true', help='order it now, followed here')
    g.add_argument('--acme-probe', action='store_true', help='try port 80 as the CA will')
    g.add_argument('--acme-dns-check', action='store_true',
                   help='what has to be in your DNS, looked up now')
    g = p.add_argument_group('UPGRADES', 'Signed packages only (Ed25519, every file checked).')
    g.add_argument('--upgrade-check', action='store_true',
                   help='what is at upgrade_url, and whether it is newer')
    g.add_argument('--upgrade-online', nargs='?', const='', metavar='VERSION',
                   help='the newest (or VERSION): fetched, checked, put in, followed')
    g.add_argument('--upgrade-file', metavar='FILE', help='put in a plexmon-X.Y.Z.tar.gz')
    g.add_argument('--upgrade-status', action='store_true', help='how the last one went')
    g.add_argument('--upgrade-follow', action='store_true', help='follow one going on')
    g.add_argument('--upgrade-cancel', action='store_true', help='stop one not yet begun')
    g.add_argument('--downgrade-check', action='store_true',
                   help='the older versions there, and which are safe to go back to')
    g.add_argument('--downgrade', metavar='VERSION|FILE', help='go back to a safe older version')
    g = p.add_argument_group('THE PLEX LIBRARY', 'Asked of Plex Media Server here, with its own '
                             'token; what writes shows its plan first (--dry-run: only that).')
    g.add_argument('--plex', action='store_true', help='the server and its libraries')
    g.add_argument('--plex-items', metavar='LIB', help='the movies or shows of a library '
                   '(its number or name; --search narrows it)')
    g.add_argument('--plex-show', metavar='TITLE', help='one title, all Plex knows of it '
                   '(a ratingKey, or words of its title)')
    g.add_argument('--plex-tracks', metavar='TITLE', help='the default audio and subtitles of a '
                   'movie, or of every episode of a show; with --audio / --subs')
    g.add_argument('--audio', metavar='X', help='keep, a language code (eng, cze…) or part of '
                   'the track\'s name')
    g.add_argument('--subs', metavar='X', help='keep, off, a language code or part of the name')
    g.add_argument('--plex-watched', metavar='TITLE', nargs='+', help='mark watched')
    g.add_argument('--plex-unwatched', metavar='TITLE', nargs='+', help='mark unwatched')
    g.add_argument('--plex-flip-watched', metavar='TITLE', nargs='+', help='flip each movie and '
                   'episode: the watched ones unwatched, the others watched')
    g.add_argument('--plex-labels', metavar='LIB', help='the labels of a library, and how many '
                   'carry each')
    g.add_argument('--plex-label', metavar='TITLE', nargs='+', help='change the labels of titles: '
                   'with --label-add / --label-remove')
    g.add_argument('--label-add', metavar='LABEL', action='append', default=[], help='a label to put on them (again for more)')
    g.add_argument('--label-remove', metavar='LABEL', action='append', default=[], help='a label to take off them')
    g.add_argument('--plex-titles', metavar='LIB', help='the titles not in --lang (English unless '
                   'set); with --fix: put them into it (only the title, own entry only)')
    g.add_argument('--lang', metavar='CODE', help='en, cs, de… for --plex-titles')
    g.add_argument('--fix', action='store_true', help='with --plex-titles: write them (asked first)')
    g.add_argument('--plex-ratings', metavar='LIB', help='fill the missing ratings of a library in '
                   '(--overwrite: all of them; --lock: lock the fields)')
    g.add_argument('--overwrite', action='store_true', help='with --plex-ratings: every one, not only the missing')
    g.add_argument('--lock', action='store_true', help='with --plex-ratings: lock the fields written')
    g.add_argument('--plex-watchlist', action='store_true',
                   help='the watchlist of the server\'s owner (Plex Discover)')
    g.add_argument('--plex-watchlist-add', metavar='TITLE', nargs='+', help='put on it')
    g.add_argument('--plex-watchlist-remove', metavar='TITLE', nargs='+', help='take off it')
    g.add_argument('--plex-refresh', metavar='TITLE', nargs='+', help='refresh their metadata')
    g.add_argument('--plex-scan', metavar='LIB', help='scan the files of a library (plex_tools\' F5)')
    g.add_argument('--plex-refresh-all', metavar='LIB', help='refresh all the metadata of a '
                   'library (plex_tools\' F9; asked first)')
    g.add_argument('--dry-run', action='store_true', help='show the plan, write nothing')
    g = p.add_argument_group('MAINTENANCE AND UPGRADES')
    g.add_argument('--maintenance', choices=('on', 'off', 'status'), help='this server out of '
                   'service: plexmon does nothing by itself (backups, copies, updates, renewals, '
                   'SMART reads) - or back in service')
    g.add_argument('--why', metavar='TEXT', help='with --maintenance on: why, for the events')
    g.add_argument('--proxy-test', metavar='URL', nargs='?', const='', help='the way out through '
                   'the proxy tried (an address of your own, or the upgrades\' one)')
    g = p.add_argument_group('THE MACHINE AND THE DATA (as Settings: Time, Restart and power off, '
                             'System updates, The database, Backups)')
    g.add_argument('--time', action='store_true', help='the clock: chrony, the time servers, '
                   'the time zone')
    g.add_argument('--time-sync', action='store_true', help='step the clock to the time servers now')
    g.add_argument('--db', action='store_true', help='plexmon\'s database and Plex Media '
                   'Server\'s: sizes, copies, Plex\'s own backups')
    g.add_argument('--db-copy', action='store_true', help='a copy of plexmon\'s database now')
    g.add_argument('--db-check', action='store_true', help='plexmon\'s database read through')
    g.add_argument('--db-restore', metavar='NAME', help='put a copy of plexmon\'s database back '
                   '(plexmon starts again)')
    g.add_argument('--plex-db-copy', action='store_true', help='a copy of Plex Media Server\'s '
                   'database now, taken while it runs, checked')
    g.add_argument('--plex-db-restore', metavar='NAME', help='put a copy of Plex\'s database '
                   'back: Plex stopped, its files set aside, the copy in, Plex started')
    g.add_argument('--plex-own', action='store_true', help='with --plex-db-restore: NAME is one '
                   'of Plex\'s own backups (com.plexapp.plugins.library.db-YYYY-MM-DD)')
    g.add_argument('--backups', action='store_true', help='the destinations and the last backups')
    g.add_argument('--backup-now', action='store_true', help='a backup made and sent to every '
                   'destination switched on')
    g.add_argument('--backup-verify', action='store_true', help='the newest backup fetched back '
                   'and tried')
    g.add_argument('--os-updates', action='store_true', help='look for updates of the system (dnf)')
    g.add_argument('--os-update', nargs='?', const='all', choices=('all', 'security'),
                   help='update the system now (Plex Media Server left out unless osup_plex=1)')
    g.add_argument('--reboot', action='store_true', help='restart the server (asked first)')
    g.add_argument('--poweroff', action='store_true', help='switch the server off (asked first)')
    g = p.add_argument_group('THE CONSOLE')
    g.add_argument('--console', action='store_true', help='plexmon by its menus: the live log, '
                   'the accounts, the certificates, upgrades')
    g.add_argument('--console-at-login', choices=('on', 'off', 'status'),
                   help='the console when root signs in')
    g.add_argument('--admin-only', action='store_true', help=argparse.SUPPRESS)
    return p


HELP_EXAMPLES = [
    ('plexmon', 'what it is doing (the same as --status)'),
    ('plexmon --check', 'every part of it: disks, SMART, the API, nginx, the certificate'),
    ('plexmon --console', 'everything by its menus'),
    ('plexmon --wake', 'spin the disks up and read SMART now'),
    ('plexmon --add-user jan --role fulladmin', 'an account for Settings (password asked)'),
    ('plexmon --cert-import plex.pem plex.key chain.pem', 'a certificate for nginx'),
    ('plexmon --set acme=1 --set acme_node_name=plex.example.cz', ''),
    ('plexmon --acme-order', 'a certificate from Let\'s Encrypt now'),
    ('plexmon --plex-cert on', 'Plex Media Server takes the certificate too'),
    ('plexmon --upgrade-online', 'the newest version: fetched, checked, put in'),
    ('plexmon --plex-items Filmy --search matrix', 'the movies of a library'),
    ('plexmon --plex-tracks "Crash Landing on You" --audio kor --subs cze', 'every episode'),
    ('plexmon --plex-ratings Filmy --dry-run', 'the missing ratings, shown, not written'),
    ('plexmon --plex-db-copy', 'a checked copy of Plex Media Server\'s database'),
    ('plexmon --backup-now', 'a backup to every destination'),
    ('plexmon --os-update security', 'the security updates of the system'),
]


# ---------------------------------------------------------------------------
# What the command line does
# ---------------------------------------------------------------------------
def out_json(obj):
    sys.stdout.write(json.dumps(obj, indent=1, default=str, ensure_ascii=False) + '\n')


def _daemon_get(path, timeout=10):
    """Ask the running daemon (as the pages do) - for what only it knows."""
    try:
        port = int(PORT_FILE.read_text().strip())
    except (OSError, ValueError):
        port = int(CONFIG.get('api_port') or 9848)
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
                                 headers={'X-Otp-Token': api_token()})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:                                   # noqa: BLE001
        return None


def service_active(name=SERVICE):
    try:
        return subprocess.run([SYSTEMCTL, 'is-active', name], capture_output=True, text=True,
                              timeout=10).stdout.strip() or '?'
    except (OSError, subprocess.SubprocessError):
        return '?'


DASH_DATA = Path(os.environ.get('PLEXMON_DATA', '/var/www/html/smart/data.json'))


def dashboard_state():
    """What the dashboard's daemon last wrote (data.json): never a disk touched."""
    try:
        d = json.loads(DASH_DATA.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    plex = d.get('plex') or {}
    return {'overall': d.get('overall'), 'generated': d.get('generated'),
            'age': time.time() - float(d.get('generated') or 0) if d.get('generated') else None,
            'reasons': [r.get('text') for r in d.get('reasons') or []],
            'disks': [{'name': x.get('name'), 'model': x.get('model'), 'power': x.get('power'),
                       'temp': x.get('temp'), 'health': x.get('health')}
                      for x in d.get('disks') or []],
            'plex': {'active': plex.get('active'), 'version': plex.get('version'),
                     'sessions': plex.get('sessions')}}


def status_now(store):
    cert = cert_now(nginx_conf_cert_paths()[0]) or {}
    prof = tls_profile(store, node_name()) or {}
    up = online_view(store).get('online') or {}
    health = _daemon_get('/api/health', 3)
    problems = []
    if not health:
        problems.append('Settings is not answering on 127.0.0.1:%s - systemctl status %s'
                        % (CONFIG.get('api_port'), SERVICE))
    if not nginx_adopted():
        problems.append('%s is not plexmon\'s: its TLS settings and the ACME answers cannot be '
                        'written (plexmon --nginx-adopt)' % NGINX_SITE)
    st = web_status(store)
    if st.get('state') == 'failed':
        problems.append('the certificate could not be put in: %s' % (st.get('said') or ['?'])[-1])
    left = None
    if cert.get('not_after'):
        left = int((cert['not_after'] - time.time()) / 86400)
        if left < 14:
            problems.append('the certificate of %s %s' % (cert.get('cn') or '?', (
                'ran out' if left < 0 else 'runs out in %d days' % left)))
    ac = acme_state(store, node_name())
    if CONFIG.get('acme') and ac.get('ok') is False:
        problems.append('the last order from the ACME CA failed: %s' % ac.get('said'))
    dash = dashboard_state()
    if dash.get('age') is not None and dash['age'] > 600:
        problems.append('the dashboard has written nothing for %s' % human_span(dash['age']))
    plan = upgrade_plan(store)
    return {'version': __version__, 'node': node_name(), 'host': socket.gethostname(),
            'service': service_active(), 'daemon_alive': bool(health),
            'database': store.db.where(), 'counts': store.counts(),
            'dashboard': dash, 'nginx_adopted': nginx_adopted(), 'nginx_site': str(NGINX_SITE),
            'certificate': {'cn': cert.get('cn'), 'issuer': cert.get('issuer'),
                            'not_after': cert.get('not_after'), 'days_left': left,
                            'how': prof.get('how'), 'file': str(nginx_conf_cert_paths()[0])},
            'tls': {'state': st.get('state') or '', 'preset': prof.get('preset')},
            'acme': {'on': bool(CONFIG.get('acme')), 'state': ac},
            'plex_cert': plex_view(store),
            'upgrade': {'newer': (up.get('newest') or {}).get('version') if up.get('newer')
                        else '', 'plan': plan['state'] if plan else ''},
            'console_at_login': console_login_state(), 'problems': problems,
            'nodes': [{'name': node_name(), 'alive': True}]}


def cli_status(store, pal, as_json, check=False, mon=None):
    s = status_now(store)
    if as_json and not check:
        out_json(s)
        return 0
    if as_json and check:
        lines = []
        bad = _settings_checks(store, s, lambda ok, t: lines.append(('ok ' if ok else 'FAIL ') + t))
        out_json(dict(s, check={'ok': not bad, 'exit': 1 if bad else 0, 'lines': lines}))
        return 1 if bad else 0
    print(pal.head('plexmon %s on %s' % (__version__, s['host'])))
    print((pal.ok_line if s['service'] == 'active' else pal.bad_line)(
        '%s.service is %s' % (SERVICE, s['service'])))
    print((pal.ok_line if s['daemon_alive'] else pal.bad_line)(
        'Settings %s' % ('answers on 127.0.0.1:%s' % CONFIG.get('api_port') if s['daemon_alive']
                         else 'is not answering - systemctl status %s' % SERVICE)))
    m = maintenance_row(store)
    if m.get('on'):
        print(pal.warn_line('IN MAINTENANCE since %s%s - plexmon does nothing by itself '
                            '(plexmon --maintenance off)' % (
                                datetime.fromtimestamp(float(m.get('at') or 0)).strftime(
                                    '%d-%m-%Y %H:%M'), (' - ' + m['why']) if m.get('why') else '')))
    d = s['dashboard']
    if d:
        tone = {'ok': pal.ok_line, 'warn': pal.warn_line}.get(d.get('overall'), pal.bad_line)
        print(tone('the dashboard: %s, %d disk(s), written %s ago' % (
            {'ok': 'all is well', 'warn': 'a warning', 'crit': 'critical'}.get(
                d.get('overall'), d.get('overall') or '?'), len(d.get('disks') or []),
            human_span(d.get('age') or 0))))
        for r in d.get('reasons') or []:
            print('    ' + pal.warn(r))
        px = d.get('plex') or {}
        print('  Plex Media Server: %s%s, %s stream(s)' % (
            'running' if px.get('active') else 'stopped',
            (' ' + px['version']) if px.get('version') else '', px.get('sessions') or 0))
    else:
        print(pal.warn_line('the dashboard has written nothing yet (%s)' % DASH_DATA))
    c = s['certificate']
    if c.get('cn'):
        print('  certificate: %s, from %s, %s' % (
            c['cn'], re.sub(r'^.*?CN=([^,]+).*$', r'\1', c.get('issuer') or '?'),
            ('%d days left' % c['days_left']) if c.get('days_left') is not None else '?'))
    print('  nginx: %s' % ('%s is plexmon\'s, TLS %s' % (NGINX_SITE, s['tls']['state'] or '-')
                           if s['nginx_adopted'] else 'not adopted'))
    print('  ACME: %s · Plex\'s own certificate: %s · console at login: %s' % (
        'on' if s['acme']['on'] else 'off', 'on' if s['plex_cert']['on'] else 'off',
        'on' if s['console_at_login'] else 'off'))
    print('  database: %s · accounts %d · signed in %d' % (
        s['database'], s['counts']['accounts'], s['counts']['sessions']))
    if s['upgrade']['newer']:
        print(pal.warn_line('plexmon %s is out - plexmon --upgrade-online' % s['upgrade']['newer']))
    for p in s['problems']:
        print(pal.bad_line(p))
    bad = 0
    if check:
        if mon is not None:
            print(pal.head('the dashboard\'s whole chain'))
            sys.stdout.flush()
            try:
                bad += 1 if mon.check() else 0
            except Exception as e:                      # noqa: BLE001
                print(pal.bad_line('the dashboard\'s check broke: %s' % e))
                bad += 1
        print(pal.head('Settings, nginx and the certificate'))
        bad += _settings_checks(store, s, lambda ok, t: print((pal.ok_line if ok else
                                                               pal.bad_line)(t)))
    return 0 if s['daemon_alive'] and not s['problems'] and not bad else 1


def _settings_checks(store, s, say):
    bad = 0

    def one(ok, text):
        say(ok, text)
        return 0 if ok else 1
    key = sealing_key_path()
    bad += one(key.is_file(), 'the key secrets are sealed with: %s' % key)
    try:
        store.db.value('SELECT 1')
        bad += one(True, 'the database: %s' % store.db.where())
    except Exception as e:                              # noqa: BLE001
        bad += one(False, 'the database: %s' % e)
    bad += one(bool(store.fulladmins_left()), 'a full administrator can sign in to Settings')
    ok, said = nginx_test()
    bad += one(ok, 'nginx -t: %s' % (said or 'ok'))
    bad += one(nginx_adopted(), '%s is plexmon\'s' % NGINX_SITE)
    c = s['certificate']
    if c.get('cn'):
        bad += one((c.get('days_left') or 0) >= 14, 'the certificate %s: %s days left'
                   % (c['cn'], c.get('days_left')))
    bad += one(TOKEN_FILE.exists(), 'the token of the pages: %s' % TOKEN_FILE)
    bad += one(UPGRADE_HELPER.exists() and UPGRADE_PATH_UNIT.exists(),
               'upgrades from the pages: %s and plexmon-upgrade.path' % UPGRADE_HELPER)
    keys = sorted((LIB_DIR / 'release-keys').glob('*.pub'))
    bad += one(bool(keys), 'release keys: %s' % (', '.join(k.name for k in keys) or 'none'))
    ph = php_state(wait=True)
    if ph.get('version'):
        bad += one(bool(ph.get('fixed')), 'PHP %s %s' % (
            ph['version'], 'has the php2shell fix' if ph.get('fixed')
            else 'does NOT have the php2shell fix (CVE-2026-109618/109619) - '
                 './install.sh --upgrade puts it right'))
    guard = nginx_conf_text()
    if PHP_LOCATION_RE.search(guard or ''):
        bad += one(INCLUDE_PHP_GUARD in guard, 'the PHP pages refuse a form or multipart body '
                   '(%s)' % NGINX_PHP_GUARD)
    return bad


def cli_users(store, pal, as_json):
    mfa = mfa_all(store)
    rows = store.accounts()
    for u in rows:
        u['mfa'] = mfa.get(u['name'].lower())
    if as_json:
        out_json({'users': rows, 'mfa_on': bool(CONFIG.get('admin_2fa'))})
        return 0
    print(table([(u['name'], u['role'], 'off' if u['disabled'] else 'on',
                  'app' if u.get('mfa') else '-', stamp(u['last_in']) if u.get('last_in')
                  else 'never', u.get('last_from') or '-', u.get('note') or '')
                 for u in rows],
                ['account', 'role', 'state', '2nd step', 'last in', 'from', 'note']))
    print(pal.dim('  the second step of signing in is %s (plexmon --admin-2fa on|off)'
                  % ('ON' if CONFIG.get('admin_2fa') else 'off')))
    return 0


def cli_settings(store, pal, as_json):
    adm = _bare(store)
    rows = adm.settings_state(True)
    if as_json:
        out_json({'settings': rows, 'panes': PANES})
        return 0
    by = {r['key']: r for r in rows}
    for pane, words in PANE_TITLES:
        print(pal.head(words))
        tbl = []
        for k in PANES.get(pane, []):
            r = by[k]
            v = r['value']
            shown = ('set' if r.get('set') else 'not set') if r['secret'] else (
                'on' if v is True else 'off' if v is False else str(v))
            tbl.append((k, shown, r['source_says']))
        print(table(tbl, ['key', 'value', 'from'], [26, 40, 24]))
    return 0


PANE_TITLES = [('people', 'Accounts'), ('web', 'Certificates and TLS'),
               ('acme', 'Automatic certificates'), ('upgrade', 'Upgrades'),
               ('about', 'About and debugging')]


def cli_cert(store, pal, as_json):
    prof = tls_profile(store, node_name()) or {}
    st = web_state(store)
    if as_json:
        out_json({'kept': tls_view(prof), 'nginx': st, 'plex': plex_view(store),
                  'acme_care': acme_care(store, node_name())})
        return 0
    info = prof.get('info') or st.get('cert_now') or {}
    left = int((info['not_after'] - time.time()) / 86400) if info.get('not_after') else None
    print(pal.head('the certificate of %s' % (info.get('cn') or node_name())))
    print('  names:       %s' % (', '.join(info.get('san') or []) or '-'))
    print('  issued by:   %s' % (info.get('issuer') or '-'))
    print('  valid until: %s%s' % (stamp(info.get('not_after')),
                                   '  (%d days left)' % left if left is not None else ''))
    print('  key:         %s' % (info.get('key') or '-'))
    print('  how:         %s' % (prof.get('how') or 'not kept here'))
    print('  TLS:         %s, DH %s, HSTS %s' % (prof.get('preset') or '-', prof.get('dh') or
                                                  'none', prof.get('hsts') or 'off'))
    print('  in nginx:    %s (%s)' % (st.get('cert_file'), 'adopted' if st.get('adopted')
                                      else 'NOT adopted'))
    print('  ACME:        %s' % ('renews it' if acme_care(store, node_name()) and
                                 CONFIG.get('acme') else 'leaves it alone'))
    state = st.get('state') or '-'
    print(((pal.ok_line if state == 'done' else pal.bad_line if state == 'failed'
            else pal.warn_line)('%s%s' % (state, (': ' + '; '.join(st.get('said') or []))
                                          if st.get('said') else ''))))
    pv = plex_view(store)
    print('  Plex Media Server: %s%s' % ('takes it' if pv['on'] else 'its own certificate',
                                         (' (%s for %s)' % (pv['path'], pv['domain']))
                                         if pv.get('ours') else ''))
    return 0


def _put_cert(store, got, how, by):
    prof = tls_profile(store, node_name()) or {}
    prof.update(cert=got['cert'], chain=got['chain'], root=got['root'],
                key=seal_secret(got['key'], 'web:tls'), info=got['info'],
                chain_info=got['chain_info'], root_info=got['root_info'], verify=got['verify'],
                how=how)
    prof.pop('pending_key', None)
    prof.pop('csr', None)
    prof.setdefault('preset', 'standard')
    prof.setdefault('dh', 'none')
    prof.setdefault('hsts', 0)
    tls_save(store, node_name(), prof, by)
    acme_care_set(store, node_name(), False, by, 'a certificate was put in by hand')
    store.add_event('settings', '%s put a certificate in: %s (until %s)' % (
        by, got['info'].get('subject'), stamp(got['info'].get('not_after'))), 'warn', who=by)


def _apply_now(store):
    with WEB_LOCK:
        web_apply(store)
    return web_status(store)


def cli_upgrade_follow(store, pal):
    last = None
    while True:
        plan = upgrade_plan(store)
        if not plan:
            print('no upgrade is going on')
            return 0
        st = plan['steps'][0]
        line = '%s → %s: %s %s' % (node_name(), plan['version'], st.get('state'),
                                   st.get('part_words') or st.get('said') or '')
        if line != last:
            print(pal.dim(time.strftime('%H:%M:%S')) + '  ' + line)
            last = line
        if plan['state'] in ('done', 'failed', 'cancelled'):
            print((pal.ok_line if plan['state'] == 'done' else pal.bad_line)(
                'the upgrade to %s: %s' % (plan['version'], plan['state'])))
            return 0 if plan['state'] == 'done' else 1
        time.sleep(2)


def _bare(store):
    adm = Admin.__new__(Admin)
    adm.store = store
    return adm


def _confirm(args, words):
    if args.yes or not sys.stdin.isatty():
        return True
    try:
        return input('  %s (y/N) ' % words).strip().lower() in ('y', 'yes', 'a', 'ano')
    except EOFError:
        return False


MONITOR_FLAGS = ('wake', 'refresh', 'oneshot', 'diag', 'atv', 'atv_scan', 'atv_probe',
                 'atv_pair', 'atv_key', 'atv_unpair', 'atv_unpair_all')



# ---- the Plex library on the command line (2.1.0) ----------------------------------------
def _px_wait(pal, quiet=False):
    """The job just started, followed here until it ends: its result."""
    last = ''
    while True:
        j = PX.job_view()
        line = '%s %s' % (j.get('says') or '', ('%d/%d' % (j['done'], j['whole'])) if j.get(
            'whole') else '')
        if not quiet and line != last and sys.stderr.isatty():
            sys.stderr.write('\r  ' + pal.dim(line[:90]).ljust(70) + '\x1b[K')
            sys.stderr.flush()
            last = line
        if not j.get('running'):
            break
        time.sleep(0.4)
    if not quiet and sys.stderr.isatty():
        sys.stderr.write('\r\x1b[K')
    if j.get('error'):
        raise PX.PlexError(j['error'])
    return j.get('result') or {}


def _px_ask(args, words):
    if args.yes:
        return True
    if args.json or not sys.stdin.isatty():
        print('  (add --yes to do it)')
        return False
    try:
        return input('  %s [y/N] ' % words).strip().lower() in ('y', 'yes', 'a', 'ano')
    except EOFError:
        return False


def _px_lib(ref):
    secs = PX.video_sections()
    ref = str(ref or '').strip()
    for s in secs:
        if s['key'] == ref or s['title'].casefold() == ref.casefold():
            return s
    hits = [s for s in secs if ref.casefold() in s['title'].casefold()]
    if len(hits) == 1:
        return hits[0]
    raise ValueError('no library %s - there are: %s' % (ref, ', '.join(
        '%s (%s)' % (s['title'], s['key']) for s in secs)))


def _px_find(ref):
    """A title by its ratingKey, or by words of it - in every library."""
    ref = str(ref or '').strip()
    if ref.isdigit():
        md = PX.metadata(ref)
        if md:
            return {'rk': ref, 'title': md.get('title'), 'year': md.get('year'),
                    'type': md.get('type'), 'lib': str(md.get('librarySectionID') or ''),
                    'guid': md.get('guid')}
    m = re.search(r'/library/metadata/(\d+)', ref) or re.search(r'key=%2Flibrary%2Fmetadata%2F(\d+)',
                                                               ref)
    if m:
        return _px_find(m.group(1))
    match = None
    found = []
    for s in PX.video_sections():
        for it in PX.items(s['key'])['items']:
            t = (it['title'] or '').casefold()
            if t == ref.casefold():
                match = dict(it, lib=s['key'])
            elif ref.casefold() in t:
                found.append(dict(it, lib=s['key']))
    if match:
        return match
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError('nothing called %s' % ref)
    raise ValueError('%s is %d titles: %s - say which (its number)' % (ref, len(found), '; '.join(
        '%s (%s) %s' % (f['title'], f.get('year') or '', f['rk']) for f in found[:8])))


def _px_variant(arg, variants, kind, allow_off):
    a = str(arg or '').strip().lower()
    if a in ('', 'keep', '-'):
        return 'keep'
    if allow_off and a in ('off', 'none', '0'):
        return 'off'
    c = [v for v in variants if v['key'] == a] or [v for v in variants if v['code'] == a] or \
        [v for v in variants if a in v['label'].lower()]
    if not c:
        raise ValueError('%s: %s is not there - there are: %s' % (
            kind, arg, ', '.join(sorted({v['code'] or '?' for v in variants})) or 'none'))
    c.sort(key=lambda v: -v.get('count', 0))
    return c[0]['key']


def cli_plex(args, store, pal):
    """The Plex library: None when nothing of it was asked."""
    asked = [a for a in ('plex', 'plex_items', 'plex_show', 'plex_tracks', 'plex_watched',
                         'plex_unwatched', 'plex_flip_watched', 'plex_labels', 'plex_label', 'plex_titles',
                         'plex_ratings', 'plex_refresh', 'plex_scan', 'plex_refresh_all',
                         'plex_watchlist',
                         'plex_watchlist_add', 'plex_watchlist_remove')
             if getattr(args, a, None)]
    if not asked:
        return None
    by = 'root (command line)'
    try:
        return _cli_plex(args, store, pal, asked[0], by)
    except (PX.PlexError, ValueError) as e:
        if args.json:
            out_json({'ok': False, 'error': str(e)})
        else:
            print(pal.bad_line(str(e)))
        return 1


def _cli_plex(args, store, pal, what, by):
    if what == 'plex':
        st = PX.state()
        if args.json:
            out_json(st)
            return 0 if st.get('reachable') else 1
        if not st.get('reachable'):
            print(pal.bad_line(st.get('error') or 'Plex Media Server does not answer'))
            return 1
        sv = st['server']
        print(pal.ok_line('%s %s · %s · %d playing now' % (pal.bold(sv['name']), sv['version'],
                                                            st['url'], st.get('playing') or 0)))
        print(table([[s['key'], s['title'], s['type'], s.get('count') if s.get('count') is not None
                      else '?', 'refreshing' if s.get('refreshing') else '']
                     for s in st['sections']], ['#', 'library', 'kind', 'items', '']))
        src = st.get('sources') or {}
        print(pal.dim('  ratings from: %s' % (', '.join(k for k, v in src.items() if v) or 'nothing')))
        return 0
    if what == 'plex_items':
        lib = _px_lib(args.plex_items)
        rows = PX.items(lib['key'])['items']
        if args.search:
            q = args.search.casefold()
            rows = [r for r in rows if q in (r['title'] or '').casefold()]
        if args.json:
            out_json(rows)
            return 0
        out = []
        for r in rows:
            if r['type'] == 'show':
                seen = '%d/%d' % (r.get('seen') or 0, r.get('leaves') or 0)
            else:
                seen = 'watched' if r.get('views') else ('in progress' if r.get('offset') else '')
            out.append([r['rk'], r['title'], r.get('year') or '', seen,
                        ('%.1f' % float(r['rating'])) if r.get('rating') else '',
                        ', '.join(r.get('labels') or [])])
        print(table(out, ['#', 'title', 'year', 'watched', 'rating', 'labels']))
        print(pal.dim('  %d in %s' % (len(rows), lib['title'])))
        return 0
    if what == 'plex_show':
        it = PX.item_view(_px_find(args.plex_show)['rk'])
        if args.json:
            out_json(it)
            return 0
        print(pal.head('%s%s' % (it['title'], ' (%s)' % it['year'] if it.get('year') else '')))
        facts = [('kind', it['type']), ('library', it.get('lib_title')),
                 ('ratings', '  '.join('%s %s' % (b['label'] or '★', b['value'])
                                       for b in it.get('ratings') or [])),
                 ('genres', ', '.join(it.get('genres') or [])),
                 ('labels', ', '.join(it.get('labels') or [])),
                 ('locked', ', '.join(it.get('locked') or [])),
                 ('language', it.get('language') or 'the library\'s'),
                 ('watched', ('%d of %d episodes' % (it.get('seen') or 0, it.get('leaves') or 0))
                  if it['type'] == 'show' else ('yes' if it.get('views') else 'no')),
                 ('ids', ', '.join('%s %s' % kv for kv in (it.get('ids') or {}).items()))]
        for k, v in facts:
            if v:
                print('  %s %s' % (pal.dim(k.ljust(10)), v))
        if it.get('summary'):
            print('  ' + it['summary'][:400])
        t = it.get('tracks') or {}
        for kind, lst in (('audio', t.get('audio')), ('subtitles', t.get('subs'))):
            if lst:
                print('  %s %s' % (pal.dim(kind.ljust(10)), '; '.join(
                    ('*' if v['selected'] else '') + v['label'] for v in lst)))
        for sea in it.get('seasons') or []:
            print('  %s %d/%d watched' % (sea['title'].ljust(12), sea.get('seen') or 0,
                                          sea.get('leaves') or 0))
        return 0
    if what == 'plex_tracks':
        f = _px_find(args.plex_tracks)
        sc = PX.tracks_scan([f['rk']])
        if not args.audio and not args.subs:
            if args.json:
                out_json(dict(sc, title=f['title'], rk=f['rk']))
                return 0
            print(pal.head('%s - %d video(s)' % (f['title'], sc['videos'])))
            for kind, lst in (('audio', sc['audio']), ('subtitles', sc['subs'])):
                print(table([[v['code'] or '?', v['label'], '%d/%d' % (v['count'], sc['videos']),
                              'default' if v['selected'] else ''] for v in lst],
                            [kind, 'track', 'in', '']))
            print(pal.dim('  then: --audio CODE --subs CODE|off'))
            return 0
        a = _px_variant(args.audio, sc['audio'], 'audio', False)
        s_ = _px_variant(args.subs, sc['subs'], 'subtitles', True)
        plan = PX.tracks_plan(sc['scan'], a, s_)
        if args.json and args.dry_run:
            out_json(plan)
            return 0
        print(pal.head('%s: audio → %s, subtitles → %s' % (f['title'], plan['words']['audio'],
                                                          plan['words']['subs'])))
        for r in plan['rows']:
            miss = any(x.get('audio_missing') or x.get('subs_missing') for x in r['parts'])
            print('  %s %s' % (pal.warn('–') if miss else pal.good('→'), r['label']))
        print(pal.dim('  %d change; %d without that audio, %d without those subtitles (left)' % (
            plan['change'], plan['audio_missing'], plan['subs_missing'])))
        if args.dry_run or not plan['change']:
            return 0
        if not _px_ask(args, 'Set it on %d video(s)?' % plan['change']):
            return 1
        PX.tracks_apply(store, sc['scan'], by)
        r = _px_wait(pal)
        print((pal.ok_line if not r.get('failed') else pal.bad_line)(
            '%d set, %d failed' % (r.get('ok') or 0, r.get('failed') or 0)))
        return 0 if not r.get('failed') else 1
    if what in ('plex_watched', 'plex_unwatched'):
        refs = args.plex_watched or args.plex_unwatched
        found = [_px_find(x) for x in refs]
        got = PX.set_watched(store, [f['rk'] for f in found], what == 'plex_watched', by)
        r = got.get('done') or _px_wait(pal)
        print(pal.ok_line('%s marked %s' % (', '.join(f['title'] for f in found),
                                           'watched' if what == 'plex_watched' else 'unwatched')))
        return 0 if not r.get('failed') else 1
    if what == 'plex_flip_watched':
        found = [_px_find(x) for x in args.plex_flip_watched]
        PX.toggle_watched(store, [f['rk'] for f in found], by)
        r = _px_wait(pal) or {}
        print(pal.ok_line('%s: %s now watched, %s unwatched' % (
            ', '.join(f['title'] for f in found), r.get('watched', 0), r.get('unwatched', 0))))
        return 0 if not r.get('failed') else 1
    if what == 'plex_labels':
        lib = _px_lib(args.plex_labels)
        g = PX.label_groups(lib['key'])
        if args.json:
            out_json(g)
            return 0
        print(table([[x['title'], x['count'], ' / '.join(x['names']) if len(x['names']) > 1 else '']
                     for x in g], ['label', 'titles', 'spelt']))
        return 0
    if what == 'plex_label':
        if not args.label_add and not args.label_remove:
            raise ValueError('say what: --label-add LABEL and/or --label-remove LABEL')
        found = [_px_find(x) for x in args.plex_label]
        libs = {f['lib'] for f in found}
        if len(libs) != 1:
            raise ValueError('the titles have to be in one library')
        r = PX.labels_do(store, libs.pop(), 'edit', by, rks=[f['rk'] for f in found],
                         add=args.label_add, remove=args.label_remove)
        print(pal.ok_line('labels changed on %d title(s)' % r['done']))
        return 0
    if what == 'plex_titles':
        lib = _px_lib(args.plex_titles)
        lang = args.lang or CONFIG.get('plex_title_lang') or 'en'
        target = PX.lang_name_from_code(lang)
        rows = [r for r in PX.titles_rows(lib['key'])['rows']
                if not PX.is_target_lang(r['lang'], target)]
        if not args.fix:
            if args.json:
                out_json(rows)
                return 0
            print(table([[r['rk'], r['title'], r['lang'], 'split' if r['dup'] else '']
                         for r in rows], ['#', 'title', 'looks like', '']))
            print(pal.dim('  %d not in %s - add --fix to put them into it' % (len(rows), target)))
            return 0
        PX.titles_propose(store, lib['key'], [r['rk'] for r in rows], lang, by)
        props = [p_ for p_ in _px_wait(pal).get('props') or [] if p_['exact'] and not p_['risky']]
        for p_ in props:
            print('  %s  %s  %s' % (p_['old'], pal.dim('→'), pal.good(p_['new'])))
        print(pal.dim('  %d from their own entry (the rest: on the pages, to be checked)' %
                      len(props)))
        if args.dry_run or not props or not _px_ask(args, 'Write %d title(s)?' % len(props)):
            return 0
        PX.titles_apply(store, lib['key'], 'title', by, items_=props, lang=lang)
        r = _px_wait(pal)
        print(pal.ok_line('%d written, %d failed' % (r.get('ok') or 0, r.get('failed') or 0)))
        return 0
    if what == 'plex_ratings':
        lib = _px_lib(args.plex_ratings)
        rows = PX.items(lib['key'])['items']
        rks = [r['rk'] for r in rows if args.overwrite or not (r.get('rating') or r.get('audience'))]
        if not rks:
            print(pal.ok_line('every one of %s has a rating' % lib['title']))
            return 0
        PX.ratings_preview(store, lib['key'], rks, args.overwrite, by)
        r = _px_wait(pal)
        ch = [e for e in r['rows'] if e['status'] in ('add', 'update')]
        print(table([[e['label'], e['status'],
                      ('%.1f→' % e['cur'] if e.get('cur') is not None else '') +
                      ('%.1f %s' % (e['new'], e['new_src']) if e.get('new') is not None else ''),
                      ('%.1f %s' % (e['new_aud'], e['new_aud_src']) if e.get('new_aud') is not None
                       else '')] for e in ch], ['title', '', 'rating', 'audience']))
        print(pal.dim('  %s' % ', '.join('%s %d' % kv for kv in r['counts'].items())))
        if args.dry_run or not ch or not _px_ask(args, 'Write %d rating(s)%s?' % (
                len(ch), ' and lock them' if args.lock else '')):
            return 0
        PX.ratings_apply(store, r['plan_id'], args.lock, [], by)
        x = _px_wait(pal)
        print(pal.ok_line('%d written, %d failed' % (x.get('ok') or 0, x.get('failed') or 0)))
        return 0
    if what in ('plex_watchlist_add', 'plex_watchlist_remove'):
        how = 'add' if what == 'plex_watchlist_add' else 'remove'
        found = [_px_find(x) for x in (args.plex_watchlist_add or args.plex_watchlist_remove)]
        dks = [PX.discover_key(f.get('guid')) for f in found]
        if not all(dks):
            raise ValueError('a title without a match Plex Discover knows')
        r = PX.watchlist_do(store, how, dks, by)
        print(pal.ok_line('%d %s' % (r['ok'], 'put on the watchlist' if how == 'add'
                                    else 'taken off the watchlist')))
        return 0 if not r['failed'] else 1
    if what == 'plex_watchlist':
        v = PX.watchlist_view(fresh=True)
        if args.json:
            out_json(v)
            return 0
        print(table([[x['title'], x.get('year') or '', x['type'],
                      ('here: %s' % x['here']['lib_title']) if x.get('here') else '']
                     for x in v['items']], ['title', 'year', 'kind', '']))
        return 0
    if what == 'plex_refresh':
        found = [_px_find(x) for x in args.plex_refresh]
        PX.refresh_items(store, [f['rk'] for f in found], by)
        r = _px_wait(pal)
        print(pal.ok_line('%d refreshed' % (r.get('ok') or 0)))
        return 0
    if what == 'plex_scan':
        lib = _px_lib(args.plex_scan)
        print(pal.ok_line(PX.scan_library(store, lib['key'], by)['message']))
        return 0
    if what == 'plex_refresh_all':
        lib = _px_lib(args.plex_refresh_all)
        if not _cli_sure(args, 'Plex downloads the metadata, posters and backgrounds of every '
                         'item of %s again - long, and it loads the server' % lib['title']):
            return 1
        print(pal.ok_line(PX.scan_library(store, lib['key'], by, force=True)['message']))
        return 0
    return None

HOST_ASKS = ('time', 'time_sync', 'db', 'db_copy', 'db_check', 'db_restore', 'plex_db_copy',
             'plex_db_restore', 'backups', 'backup_now', 'backup_verify', 'os_updates',
             'os_update', 'reboot', 'poweroff')


def cli_host(args, store, pal):
    """Time, restart and power off, system updates, the databases, backups:
    None when nothing of it was asked."""
    asked = [a for a in HOST_ASKS if getattr(args, a, None)]
    if not asked:
        return None
    try:
        return _cli_host(args, store, pal, asked[0], 'root (command line)')
    except ValueError as e:
        if args.json:
            out_json({'ok': False, 'error': str(e)})
        else:
            print(pal.bad_line(str(e)))
        return 1


def _cli_sure(args, words):
    if args.yes:
        return True
    if not sys.stdin.isatty():
        raise ValueError('%s - add --yes to do it without being asked' % words)
    try:
        return input('%s [y/N] ' % words).strip().lower() in ('y', 'yes', 'a', 'ano')
    except EOFError:
        return False


def _cli_job_wait(pal, job, quiet=False):
    """A job of the database (or a backup) followed until it is over."""
    last = ''
    while job['running']:
        says = '%s %s/%s' % (job.get('says') or '', job.get('done') or 0, job.get('whole') or 0)
        if says != last and not quiet:
            print(pal.dim('  ... ' + says))
            last = says
        time.sleep(0.5)
    if job.get('error'):
        raise ValueError(job['error'])
    return job.get('result')


def _cli_host(args, store, pal, what, by):
    j = args.json
    if what == 'time' or what == 'time_sync':
        if what == 'time_sync':
            HX.ntp_sync(store, by)
        d = HX.ntp_read()
        if j:
            out_json(d)
            return 0
        t = d.get('tracking') or {}
        if not d.get('chrony'):
            print(pal.warn_line('chrony is not installed - the clock follows nothing'))
        else:
            print((pal.ok_line if t.get('stratum') else pal.bad_line)(
                'follows %s, stratum %s, %+.6f s off' % (t.get('ref') or '-', t.get('stratum'),
                                                       float(t.get('offset') or 0))))
            for src in d.get('sources') or []:
                print('  %s %s %s' % (src.get('mode'), src.get('state'), src.get('name')))
        print('  the time zone: %s' % (d.get('timezone') or '-'))
        print('  the time servers set here: %s' % (', '.join(HX.ntp_conf(store).get('servers')
                                                             or []) or 'the machine\'s own'))
        return 0
    if what == 'db':
        d = HX.db_view(store)
        if j:
            out_json(d)
            return 0
        o, px = d['own'], d['plex']
        print(pal.head('plexmon\'s database') + '  %s, %s' % (o['where'], HX.human_bytes(o['bytes'])))
        for c in o['copies'][:10]:
            print('  %s  %s' % (c['name'], HX.human_bytes(c['bytes'])))
        print(pal.head('Plex Media Server\'s database') + '  %s (Plex %s)' % (
            px['dir'], 'running' if px['running'] else 'stopped'))
        for f in px['files']:
            print('  %s  %s' % (f['name'], HX.human_bytes(f['bytes'])))
        last = px.get('last') or {}
        print('  the last copy: %s' % (time.strftime('%Y-%m-%d %H:%M', time.localtime(last['at']))
                                       if last.get('at') else 'never'))
        print(pal.head('  copies made here') + '  (%s)' % px['copies_dir'])
        for c in px['copies'][:10]:
            print('  %s  %s' % (c['name'], HX.human_bytes(c['bytes'])))
        print(pal.head('  Plex\'s own backups'))
        for c in px['own_backups']:
            print('  %s  %s' % (c['name'], HX.human_bytes(c['bytes'])))
        for t in px.get('timers') or []:
            print('  a timer of this server: %s (%s, next %s)' % (t['unit'], t['enabled'],
                                                               t['next'] or '-'))
        return 0
    if what == 'db_copy':
        name = HX.own_copy(store, 'made by %s' % by)
        print(pal.ok_line('a copy made: %s' % name))
        return 0
    if what == 'db_check':
        r = HX.own_check(store)
        print((pal.ok_line if r['ok'] else pal.bad_line)('plexmon\'s database: %s' % '; '.join(
            r['said'])))
        return 0 if r['ok'] else 1
    if what == 'db_restore':
        if not _cli_sure(args, 'plexmon\'s database becomes %s, and plexmon starts again'
                         % args.db_restore):
            return 1
        print(pal.ok_line(str(HX.own_restore(store, args.db_restore, by))))
        return 0
    if what == 'plex_db_copy':
        r = HX.plex_copy(store, by, check=True)
        if j:
            out_json(r)
            return 0
        ck = r.get('check') or {}
        print((pal.bad_line if ck.get('ok') is False else pal.ok_line)(
            'a copy of Plex Media Server\'s database: %s (%s)%s' % (
                r.get('name'), HX.human_bytes(r.get('bytes') or 0),
                ' - checked: %s' % ('; '.join(ck.get('said') or [])) if ck else '')))
        return 1 if ck.get('ok') is False else 0
    if what == 'plex_db_restore':
        if not _cli_sure(args, 'Plex Media Server is stopped, its database set aside and %s put '
                         'in its place' % args.plex_db_restore):
            return 1
        r = HX.plex_restore(store, args.plex_db_restore, by, own=args.plex_own)
        print(pal.ok_line(r if isinstance(r, str) else json.dumps(r, default=str)))
        return 0
    if what == 'backups':
        d = HX.rb_view_all(store)
        if j:
            out_json(d)
            return 0
        print(pal.head('destinations') + ('' if d['password'] else '  ' + pal.bad_line(
            'no password set: rbackup_password')))
        for t in d['targets']:
            print('  %s %s (%s)' % ('on ' if t.get('enabled') else 'off', t['name'],
                                   t.get('kind_words')))
        print(pal.head('the last backups'))
        for r in d['runs'][:10]:
            print('  %s  %s  %s' % (time.strftime('%Y-%m-%d %H:%M', time.localtime(r['at'])),
                                    'ok  ' if r.get('ok') else 'FAIL', r.get('file') or
                                    r.get('error') or ''))
        return 0
    if what == 'backup_now':
        run = HX.rb_run(store, by)
        for t in run.get('targets') or []:
            print((pal.ok_line if t.get('ok') else pal.bad_line)('%s: %s' % (
                t['name'], 'sent' if t.get('ok') else t.get('error') or 'failed')))
        return 0 if run.get('ok') else 1
    if what == 'backup_verify':
        r = HX.rb_verify(store, by)
        for c in r.get('checks') or []:
            print((pal.ok_line if c.get('ok') else pal.bad_line)(c.get('what')))
        return 0 if r.get('ok') else 1
    if what == 'os_updates':
        HX.osup_check(store, by)
        while (HX.meta(store, 'host:osup:check', {}) or {}).get('state') == 'running':
            time.sleep(1)
        r = HX.meta(store, 'host:osup:check', {}) or {}
        if j:
            out_json(r)
            return 0
        if r.get('error'):
            raise ValueError(r['error'])
        print((pal.warn_line if r.get('count') else pal.ok_line)(
            '%s update(s) waiting, %s of them security' % (r.get('count'), r.get('security'))))
        for p in (r.get('packages') or [])[:60]:
            print('  ' + p)
        if r.get('reboot_after'):
            print(pal.warn_line('a restart is needed after them: %s' % ', '.join(r['reboot_why'])))
        return 0
    if what == 'os_update':
        sec = args.os_update == 'security'
        if not _cli_sure(args, 'update %s of the system now' % ('the security updates' if sec
                                                               else 'every package')):
            return 1
        plan = HX.osup_start(store, 'ask', by, security=sec)
        seen = 0
        while True:
            row = HX.meta(store, 'host:osup:row', {}) or {}
            lines = row.get('lines') or []
            for ln in lines[seen:]:
                print('  ' + ln)
            seen = len(lines)
            if row.get('plan') == plan['id'] and row.get('state') in ('done', 'failed') \
                    and not HX.OSUP_WORK['run']:
                break
            time.sleep(1)
        print((pal.ok_line if row['state'] == 'done' else pal.bad_line)(row.get('said') or
                                                                        row['state']))
        return 0 if row['state'] == 'done' else 1
    if what in ('reboot', 'poweroff'):
        how = 'reboot' if what == 'reboot' else 'poweroff'
        if not _cli_sure(args, 'Plex Media Server and everything on this server stops - %s now'
                         % ('restart' if how == 'reboot' else 'switch off')):
            return 1
        HX.power_ask(store, how, by, 'asked on the command line')
        print(pal.ok_line('in 5 seconds'))
        return 0
    return None


def cli_main(argv=None, mon=None):
    """plexmon on the command line. `mon` is plexmon.py - the dashboard's daemon -
    for --daemon, --check, --wake, --atv and the rest of its own."""
    global TABLE_PAL
    said = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    pal = Palette(colour_wanted(any(x in ('--no-color', '--no-colour') for x in said))
                  and '--json' not in said)
    parser.palette = pal
    TABLE_PAL = pal
    args = parser.parse_args(said)
    if (pal.on or sys.stdout.isatty()) and not args.console and not args.json and \
            not args.daemon and not isinstance(sys.stdout, ColourOut):
        sys.stdout = ColourOut(sys.stdout, pal)
    if args.help:
        sys.stdout.write(parser.format_help())
        return 0
    if args.version:
        print('%s %s%s' % (pal.head('plexmon'), pal.bold(__version__), '' if HAVE_COLORAMA
                           else pal.dim('  (colorama is not installed; colour is written by '
                                        'hand)')))
        return 0
    acted = any(v is not None and v is not False and v != [] and v != ''
                for k, v in vars(args).items()
                if k not in ('no_color', 'json', 'debug', 'limit', 'search', 'role', 'note',
                             'yes', 'audio', 'subs', 'label_add', 'label_remove', 'lang', 'fix',
                             'overwrite', 'lock', 'dry_run', 'plex_own', 'why'))
    # Nothing asked for: under systemd (an older unit starts plexmon.py bare)
    # that is the daemon; typed by somebody - or by a script - it is the
    # question they meant, what is it doing, never a second daemon.
    if args.daemon or (not acted and os.environ.get('INVOCATION_ID') and not args.json):
        if mon is None:
            raise SystemExit('plexmon: the daemon is plexmon.py --daemon')
        return mon.run_daemon(debug=args.debug)
    if args.admin_only:
        _LOG_TO_STDERR[0] = True
        adm = Admin()
        adm.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    if not acted:
        args.status = True
        if not args.json:
            print(pal.dim('(plexmon --help for everything it does; the daemon itself is '
                          'systemctl status %s)' % SERVICE))
    _LOG_TO_STDERR[0] = False
    if any(getattr(args, k) not in (None, False) for k in MONITOR_FLAGS):
        if mon is None:
            raise SystemExit('plexmon: that is the dashboard\'s daemon\'s: plexmon.py')
        return mon.monitor_cli(args)
    if args.console_at_login:
        if args.console_at_login != 'status':
            on = console_login_set(args.console_at_login == 'on')
            print(pal.ok_line('the plexmon console opens when root signs in at the console or '
                              'over SSH (q leaves it for the shell)' if on else
                              'root signs in to the ordinary shell; `plexmon --console` opens '
                              'the console'))
        else:
            print(pal.ok_line('the console at login is %s' % ('ON' if console_login_state()
                                                                else 'off')))
        return 0
    if args.console:
        return console_main(pal)
    store = open_store()
    by = 'root (command line)'
    if args.status or args.check:
        return cli_status(store, pal, args.json, args.check, mon)
    if args.events:
        rows, total = store.events(args.limit, 0, args.search)
        if args.json:
            out_json({'rows': rows, 'total': total})
            return 0
        print(table([(stamp(r['ts']), r['kind'], r['who'] or '-', r['words'])
                     for r in reversed(rows)], ['when', 'kind', 'who', 'what'],
                    [19, 22, 16, max(30, pal.width - 66)]))
        print(pal.dim('  %d of %d' % (len(rows), total)))
        return 0
    if args.log:
        lines = log_tail(max(args.limit, 1))
        if args.search:
            lines = [ln for ln in lines if args.search.lower() in ln.lower()]
        if args.json:
            out_json({'lines': lines, 'file': str(LOG_FILE)})
            return 0
        for ln in lines:
            print(ln)
        return 0
    if args.users:
        return cli_users(store, pal, args.json)
    if args.add_user:
        pw = ask_password('a password for %s' % args.add_user) if sys.stdin.isatty() or \
            os.environ.get('PLEXMON_PASSWORD') else sys.stdin.readline().rstrip('\n')
        store.add_account(args.add_user, args.role or 'fulladmin', pw, note=args.note or
                          'made on the command line', must_change=0)
        store.add_event('accounts', 'root made the account %s (%s) on the command line'
                        % (args.add_user, args.role or 'fulladmin'), who='root')
        print(pal.ok_line('the account %s is made (%s)' % (args.add_user,
                                                           ROLE_WORDS[args.role or 'fulladmin'])))
        return 0
    if args.passwd:
        if not store.account(args.passwd):
            raise SystemExit('plexmon: there is no account called %s' % args.passwd)
        pw = ask_password('a new password for %s' % args.passwd) if sys.stdin.isatty() or \
            os.environ.get('PLEXMON_PASSWORD') else sys.stdin.readline().rstrip('\n')
        store.set_account(args.passwd, password=pw, must_change=0)
        store.db.run("DELETE FROM sessions WHERE kind = 'admin' AND LOWER(name) = ?",
                     (args.passwd.lower(),))
        store.add_event('accounts', 'root set a new password for %s on the command line'
                        % args.passwd, 'warn', who='root')
        print(pal.ok_line('the password of %s is set; their sign-ins are ended' % args.passwd))
        return 0
    if args.set_role:
        if not args.role:
            raise SystemExit('plexmon: which role? --role fulladmin|readonly')
        acc = store.account(args.set_role)
        if not acc:
            raise SystemExit('plexmon: there is no account called %s' % args.set_role)
        if acc['role'] == 'fulladmin' and args.role != 'fulladmin' and \
                not store.fulladmins_left(but=args.set_role):
            raise SystemExit('plexmon: that is the last full administrator')
        store.set_account(args.set_role, role=args.role)
        store.add_event('accounts', 'root made %s %s' % (args.set_role, ROLE_WORDS[args.role]),
                        who='root')
        print(pal.ok_line('%s is %s' % (args.set_role, ROLE_WORDS[args.role])))
        return 0
    for flag, act in ((args.del_user, 'delete'), (args.disable_user, 'disable'),
                      (args.enable_user, 'enable')):
        if not flag:
            continue
        acc = store.account(flag)
        if not acc:
            raise SystemExit('plexmon: there is no account called %s' % flag)
        if act in ('delete', 'disable') and acc['role'] == 'fulladmin' and \
                not store.fulladmins_left(but=flag) and not args.yes:
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
        print(pal.ok_line({'delete': 'the account %s is gone', 'disable':
                           '%s may no longer sign in', 'enable': '%s may sign in again'}[act]
                          % flag))
        return 0
    if args.reset_2fa:
        had = mfa_reset(store, args.reset_2fa, 'root')
        store.db.run("DELETE FROM sessions WHERE kind = 'admin' AND LOWER(name) = ?",
                     (args.reset_2fa.lower(),))
        print(pal.ok_line('the second step of %s is reset: set up again at the next sign-in'
                          % args.reset_2fa if had else
                          '%s had no second step set up' % args.reset_2fa))
        return 0
    if args.admin_2fa:
        if args.admin_2fa != 'status':
            _bare(store).save_settings({'admin_2fa': 1 if args.admin_2fa == 'on' else 0},
                                       by='root')
        mfa = mfa_all(store)
        print(pal.ok_line('the second step of signing in to Settings is %s'
                          % ('ON' if CONFIG.get('admin_2fa') else 'off')))
        for u in store.accounts():
            print('  %-24s %s' % (u['name'], 'an app' if mfa.get(u['name'].lower()) else
                                  'not set up yet'))
        return 0
    if args.sessions:
        store.sessions_tidy(int(CONFIG.get('session_idle_min') or 120) * 60)
        rows = store.sessions()
        if args.json:
            out_json(rows)
            return 0
        print(table([(s['name'], s['ip'], stamp(s['created']), stamp(s['last']))
                     for s in rows], ['who', 'from', 'since', 'last asked']))
        return 0
    if args.settings:
        return cli_settings(store, pal, args.json)
    if args.set or args.unset:
        changes = {}
        for kv in args.set or []:
            k, eq, v = kv.partition('=')
            k = k.strip()
            if not eq:
                raise SystemExit('plexmon: --set KEY=VALUE')
            if v == '-' and k in SECRET_KEYS:
                v = ask_secret_value(k)
            changes[k] = v
        for k in args.unset or []:
            if k not in DEFAULTS:
                raise SystemExit('plexmon: %s is not a setting this version knows' % k)
            store.drop_setting(k)
            print(pal.ok_line('%s: back to %s' % (k, json.dumps(FILE_CONFIG.get(k, DEFAULTS[k])))))
        if changes:
            try:
                print(pal.ok_line(_bare(store).save_settings(changes, by=by)['message']))
            except BadSetting as e:
                raise SystemExit('plexmon: %s' % e)
        load_config(saved=store.settings_saved())
        return 0
    if args.cert_status:
        return cli_cert(store, pal, args.json)
    if args.cert_import:
        pems, pfx = [], b''
        for f in args.cert_import:
            raw = Path(f).read_bytes()
            if b'-----BEGIN' in raw:
                pems.append(raw.decode('utf-8', 'replace'))
            else:
                pfx = raw
        got = cert_bundle('\n'.join(pems), '', '', '', pfx,
                          os.environ.get('PLEXMON_PFX_PASSWORD', ''))
        _put_cert(store, got, 'uploaded', by)
        st = _apply_now(store)
        print((pal.ok_line if st.get('state') == 'done' else pal.bad_line)(
            '; '.join(st.get('said') or [])))
        return 0 if st.get('state') == 'done' else 1
    if args.cert_selfsigned:
        key, crt = make_selfsigned(args.cert_selfsigned, [], 825)
        _put_cert(store, cert_bundle(crt, key), 'self-signed, made here', by)
        st = _apply_now(store)
        print((pal.ok_line if st.get('state') == 'done' else pal.bad_line)(
            '; '.join(st.get('said') or [])))
        return 0 if st.get('state') == 'done' else 1
    if args.tls_preset or args.tls_dh or args.tls_hsts is not None:
        prof = tls_profile(store, node_name()) or {}
        if not prof.get('cert'):
            raise SystemExit('plexmon: no certificate is kept here yet')
        b = {'preset': args.tls_preset or prof.get('preset') or 'standard',
             'protocols': prof.get('protocols'), 'ciphers': prof.get('ciphers'),
             'dh': args.tls_dh or prof.get('dh') or 'none', 'prefer': prof.get('prefer'),
             'hsts': args.tls_hsts if args.tls_hsts is not None else prof.get('hsts')}
        prof.update(tls_policy_checked(b))
        tls_save(store, node_name(), prof, by)
        store.add_event('settings', 'root changed the TLS settings: %s, DH %s, HSTS %s' % (
            prof['preset'], prof['dh'], prof.get('hsts')), who='root')
        st = _apply_now(store)
        print((pal.ok_line if st.get('state') == 'done' else pal.bad_line)(
            '; '.join(st.get('said') or [])))
        return 0 if st.get('state') == 'done' else 1
    if args.tls_apply:
        store.set_meta('web:retry:' + node_name(), str(time.time()))
        st = web_status(store)
        st.pop('hash', None)
        store.set_meta('web:state:' + node_name(), json.dumps(st))
        st = _apply_now(store)
        print((pal.ok_line if st.get('state') == 'done' else pal.bad_line)(
            '; '.join(st.get('said') or []) or 'nothing kept to put in'))
        return 0 if st.get('state') in ('done', '') else 1
    if args.nginx_adopt:
        changed, words = nginx_adopt()
        print((pal.ok_line if changed or 'already' in words else pal.bad_line)(words))
        gch, gwords = nginx_php_guard()
        print((pal.ok_line if gch or 'already' in gwords else pal.bad_line)(gwords))
        return 0 if changed or 'already' in words else 1
    got = cli_plex(args, store, pal)
    if got is not None:
        return got
    got = cli_host(args, store, pal)
    if got is not None:
        return got
    if args.proxy_test is not None:
        got = proxy_try(args.proxy_test)
        if args.json:
            out_json(got)
            return 0 if got.get('ok') else 1
        print(pal.head('%s - %s' % (got['url'], 'through the proxy %s' % got['proxy']
                                    if got['mode'] == 'proxy' else 'straight' + (
                                        ' (%s)' % got['why_direct'] if got.get('why_direct') else ''))))
        for st in got['steps']:
            print((pal.ok_line if st['ok'] else pal.bad_line)('%s%s  %s' % (
                st['what'], (': ' + st['said']) if st.get('said') else '', pal.dim('%d ms' % st['ms']))))
        print((pal.ok_line if got['ok'] else pal.bad_line)(
            ('reached - HTTP %s, %d ms' % (got.get('status'), got['ms'])) if got['ok']
            else 'not reached'))
        return 0 if got.get('ok') else 1
    if args.maintenance:
        name = node_name()
        if args.maintenance == 'status':
            row = maintenance_row(store, name)
            if args.json:
                print(json.dumps(dict(row, node=name), default=str))
            else:
                print(('%s: in maintenance since %s%s' % (
                    name, datetime.fromtimestamp(float(row.get('at') or 0)).strftime(
                        '%d-%m-%Y %H:%M:%S'), (' - ' + row['why']) if row.get('why') else ''))
                      if row.get('on') else '%s: in service' % name)
            return 0 if not row.get('on') else 3
        maintenance_set(store, args.maintenance == 'on', args.why or 'on the command line',
                        'command line', name)
        print(pal.ok_line('%s: %s' % (name, 'in maintenance - plexmon does nothing by itself '
                                      '(backups, copies, updates, renewals, SMART reads)'
                                      if args.maintenance == 'on' else 'back in service')))
        return 0
    if args.plex_cert:
        pv = plex_view(store)
        if args.plex_cert == 'status':
            if args.json:
                out_json(pv)
                return 0
            print(pal.ok_line('Plex Media Server %s' % ('takes plexmon\'s certificate'
                                                        if pv['on'] else 'keeps its own')))
            print('  Preferences.xml: %s (%s)' % (pv['prefs'], 'there' if pv['there']
                                                  else 'NOT there'))
            print('  custom certificate: %s%s' % (pv['path'] or 'none', (' for %s'
                                                                          % pv['domain'])
                                                  if pv['domain'] else ''))
            return 0
        if args.plex_cert == 'off':
            store.drop_setting('plex_cert')
            store.add_event('settings', 'root: Plex Media Server no longer takes plexmon\'s '
                            'certificate', who='root')
            print(pal.ok_line(plex_cert_off(store, by)))
            return 0
        if args.plex_cert == 'on':
            _bare(store).save_settings({'plex_cert': 1}, by=by)
        prof = tls_profile(store, node_name()) or {}
        if not prof.get('cert'):
            raise SystemExit('plexmon: no certificate is kept here yet')
        if not _confirm(args, 'Plex Media Server is restarted to take it - go on?'):
            return 1
        print(pal.ok_line(plex_cert_apply(store, tls_resolved(prof), prof)))
        return 0
    if args.acme:
        v = acme_view(store)
        if args.json:
            out_json(v)
            return 0
        t = v['targets'][0]
        st = t['state']
        print(pal.head('automatic certificates: %s' % ('ON' if v['on'] else 'off')))
        print('  the CA:        %s' % v['directory'])
        print('  for:           %s' % (t.get('name') or '-'))
        print('  checked by:    %s%s' % (v['challenge'], (' (%s)' % CONFIG.get('acme_dns'))
                                         if v['challenge'] == 'dns-01' else ''))
        print('  now:           %s, until %s' % (t.get('issuer') or '-', stamp(t.get('not_after'))))
        print('  ACME renews:   %s' % ('yes' if t.get('care') else 'no - its own is kept'))
        if st.get('ok') is False:
            print(pal.bad_line('the last order failed (%s): %s' % (stamp(st.get('failed_at')),
                                                                  st.get('said'))))
        elif st.get('ordered_at'):
            print(pal.ok_line('the last came %s' % stamp(st['ordered_at'])))
        if v['account'].get('kid'):
            print(pal.dim('  the account at the CA: %s' % v['account']['kid']))
        return 0
    if args.acme_order:
        if not CONFIG.get('acme'):
            raise SystemExit('plexmon: ACME is off - plexmon --set acme=1 first')
        t = node_name()
        name = acme_name_of(store, t)
        acme_care_set(store, t, True, by, 'ordered on the command line')
        print(pal.head('ordering a certificate for %s from %s' % (name, acme_directory_url())))
        try:
            acme_order(store, t, name, by, lambda w: print('  ' + pal.dim(w)))
        except AcmeError as e:
            print(pal.bad_line('no certificate: %s' % e))
            return 1
        st = _apply_now(store)
        print((pal.ok_line if st.get('state') == 'done' else pal.bad_line)(
            'the certificate for %s came: %s' % (name, '; '.join(st.get('said') or []))))
        return 0
    if args.acme_probe:
        r = acme_probe(store, node_name())
        if args.json:
            out_json(r)
            return 0
        print((pal.ok_line if r['ok'] else pal.bad_line)('%s: %s (%d ms)' % (
            r['url'], r.get('said'), r.get('ms') or 0)))
        return 0 if r['ok'] else 1
    if args.acme_dns_check:
        g = acme_dns_check(store)
        if args.json:
            out_json(g)
            return 0
        print(table([(r.get('record') or '-', r.get('type') or '-', r.get('value') or '-',
                      'yes' if r.get('ok') else ('no' if r.get('check') else '-'),
                      r.get('said') or r.get('why') or '') for r in g['rows']],
                    ['record', 'type', 'points at / value', 'there', 'said']))
        return 0
    if args.upgrade_check:
        row = online_look(store, by)
        if args.json:
            out_json(row)
            return 0
        if row.get('error'):
            print(pal.bad_line('%s: %s' % (row['url'], row['error'])))
            return 1
        n = row.get('newest') or {}
        print((pal.warn_line if row.get('newer') else pal.ok_line)(
            '%s: the newest is %s, this runs %s%s' % (row['url'], n.get('version') or '-',
                                                      __version__, ' - newer!' if
                                                      row.get('newer') else '')))
        for p in row.get('packages') or []:
            print('  %s  %s' % (p['version'], pal.dim(p['url'])))
        return 0
    if args.upgrade_online is not None:
        row = online_look(store, by)
        want = args.upgrade_online or ((row.get('newest') or {}).get('version')
                                       if row.get('newer') else '')
        if not want:
            print(pal.ok_line('nothing newer than %s at %s' % (__version__, online_url())))
            return 0
        if not _confirm(args, 'upgrade to plexmon %s?' % want):
            return 1
        meta = online_fetch(store, want, by)
        print(pal.ok_line('plexmon %s fetched and checked (signed by %s)' % (
            meta['version'], meta.get('signed_by'))))
        upgrade_start(store, by)
        return cli_upgrade_follow(store, pal)
    if args.upgrade_file:
        info = upgrade_inspect(args.upgrade_file)
        if info.get('older'):
            raise SystemExit('plexmon: %s is older than %s - plexmon --downgrade FILE'
                             % (info['version'], __version__))
        upgrade_store_package(store, args.upgrade_file, info, by)
        print(pal.ok_line('plexmon %s checked (signed by %s)' % (info['version'],
                                                                 info.get('signed_by'))))
        upgrade_start(store, by)
        return cli_upgrade_follow(store, pal)
    if args.upgrade_status:
        v = upgrade_view(store)
        if args.json:
            out_json(v)
            return 0
        p = v['plan']
        print(pal.head('plexmon %s on %s' % (__version__, node_name())))
        print('  package kept: %s' % ((v['package'] or {}).get('version') or 'none'))
        if p:
            st = p['steps'][0]
            print(((pal.ok_line if p['state'] == 'done' else pal.bad_line if p['state'] ==
                    'failed' else pal.warn_line)('the upgrade to %s: %s - %s' % (
                        p['version'], p['state'], st.get('said') or ''))))
        else:
            print('  no upgrade going on')
        on = (v['web'].get('online') or {})
        if on.get('newer'):
            print(pal.warn_line('plexmon %s is out' % on['newest']['version']))
        return 0
    if args.upgrade_follow:
        return cli_upgrade_follow(store, pal)
    if args.upgrade_cancel:
        upgrade_cancel(store, by)
        print(pal.ok_line('stopped'))
        return 0
    if args.downgrade_check:
        g = downgrade_list(store)
        if args.json:
            out_json(g)
            return 0
        print(pal.head('here %s; it may go back to %s or newer' % (g['here'], g['lowest'])))
        for v in g['versions']:
            print('  %-10s %s' % (v['version'], pal.good('yes') if not v['why'] else
                                  pal.bad('no: ' + '; '.join(v['why']))))
        return 0
    if args.downgrade:
        if os.path.exists(args.downgrade):
            info = upgrade_inspect(args.downgrade)
            why = downgrade_why_not(store, info['version'])
            if why:
                raise SystemExit('plexmon: %s' % '; '.join(why))
            upgrade_store_package(store, args.downgrade, info, by, downgrade=True)
        else:
            online_fetch(store, args.downgrade, by, older=True)
        upgrade_start(store, by)
        return cli_upgrade_follow(store, pal)
    return cli_status(store, pal, args.json)


def plexmon_argv():
    """How this program runs itself again (the console's commands)."""
    me = Path(__file__).resolve().parent / 'plexmon.py'
    return [sys.executable, str(me if me.exists() else Path(__file__).resolve())]


# ---------------------------------------------------------------------------
# The console: plexmon run by its menus, on the server's own screen (otp's)
# ---------------------------------------------------------------------------
# `plexmon --console` - and, with `plexmon --console-at-login on`, what root sees
# first after signing in at the console or over SSH. Everything is done by the
# same `plexmon ...` commands the command line has (each one run as it would be
# typed, so nothing here can do more than they do), plus the live log and the
# journal. q leaves it for the ordinary shell; ! opens a shell and comes back
# when it is closed. Coloured with colorama when it is there.
class ConsoleBack(Exception):
    """Back to the menu."""


class Console(object):
    def __init__(self, pal):
        self.pal = pal
        self.me = os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0].endswith('.py')
                                  else __file__)
        try:
            __import__('readline')     # arrows and history in input()
        except ImportError:
            pass

    # ---- small things ---------------------------------------------------------------
    def cmd(self, *args, show=True):
        """`plexmon ARGS`, its output on the screen; its exit code."""
        argv = plexmon_argv() + [str(a) for a in args]
        if not self.pal.on:
            argv.append('--no-color')
        if show:
            print(self.pal.dim('$ plexmon ' + ' '.join(self._quote(a) for a in args)))
        try:
            return subprocess.call(argv)
        except KeyboardInterrupt:
            print()
            return 130

    def out(self, *args):
        """`plexmon ARGS --json`, read."""
        try:
            got = subprocess.run(plexmon_argv() + [str(a) for a in args] + ['--json'],
                                 capture_output=True, text=True, timeout=60)
            return json.loads(got.stdout or 'null')
        except Exception:                               # noqa: BLE001
            return None

    @staticmethod
    def _quote(a):
        a = str(a)
        return a if re.match(r'^[A-Za-z0-9_@%+=:,./-]+\Z', a) else "'%s'" % a.replace("'", "'\\''")

    # ---- keys on the ordinary terminal: Esc goes back everywhere (1.0.132) ---------
    def _raw(self):
        """The terminal's settings, to be put back; None without a terminal."""
        try:
            import termios
            return termios.tcgetattr(sys.stdin.fileno())
        except Exception:                               # noqa: BLE001
            return None

    def _restore(self, was):
        if was is None:
            return
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, was)

    def key(self, wait=None):
        """One key, without Enter: 'esc', 'enter', 'bs', 'up'..., a character -
        or None when `wait` seconds went by without one."""
        import select
        import tty
        fd = sys.stdin.fileno()
        was = self._raw()
        try:
            if was is not None:
                # TCSANOW: what was typed ahead is kept, not thrown away.
                import termios
                tty.setcbreak(fd, termios.TCSANOW)
            r, _w, _x = select.select([fd], [], [], wait)
            if not r:
                return None
            ch = os.read(fd, 1)
            if ch == b'\x1b':
                # Alone, Esc; followed at once by more, a key's sequence.
                r, _w, _x = select.select([fd], [], [], 0.05)
                if not r:
                    return 'esc'
                rest = os.read(fd, 8)
                full = {b'[5~': 'pgup', b'[6~': 'pgdn', b'[1~': 'home', b'[4~': 'end',
                        b'[7~': 'home', b'[8~': 'end', b'[H': 'home', b'[F': 'end',
                        b'OH': 'home', b'OF': 'end'}.get(rest)
                if full:
                    return full
                return {b'[A': 'up', b'[B': 'down', b'[C': 'right', b'[D': 'left', b'OA': 'up',
                        b'OB': 'down', b'OC': 'right', b'OD': 'left'}.get(rest[:2], 'seq')
            if ch in (b'\r', b'\n'):
                return 'enter'
            if ch in (b'\x7f', b'\x08'):
                return 'bs'
            if ch == b'\x03':
                return 'ctrl-c'
            if ch == b'\x04':
                return 'ctrl-d'
            if ch == b'\x15':
                return 'ctrl-u'
            try:
                return ch.decode('utf-8')
            except UnicodeDecodeError:
                more = ch
                while len(more) < 4:
                    more += os.read(fd, 1)
                    try:
                        return more.decode('utf-8')
                    except UnicodeDecodeError:
                        continue
                return ''
        finally:
            self._restore(was)

    def ask(self, words, default='', secret=False):
        """A line typed; Esc (or Ctrl+C) goes back to the menu. A secret shows
        a star for each character, never the characters."""
        p = self.pal
        hint = (' [%s]' % default) if default else ''
        prompt = '  %s%s: ' % (p.info(words), p.dim(hint))
        if self._raw() is None:
            try:
                got = input(prompt)
            except EOFError:
                raise ConsoleBack()
            return got.strip() or default
        sys.stdout.write(prompt)
        sys.stdout.flush()
        got = ''
        while True:
            k = self.key()
            if k in ('esc', 'ctrl-c', 'ctrl-d'):
                sys.stdout.write(p.dim('  (back)') + '\n')
                sys.stdout.flush()
                raise ConsoleBack()
            if k == 'enter':
                sys.stdout.write('\n')
                sys.stdout.flush()
                return got.strip() or default
            if k == 'bs':
                if got:
                    got = got[:-1]
                    sys.stdout.write('\b \b')
            elif k == 'ctrl-u':
                sys.stdout.write('\b \b' * len(got))
                got = ''
            elif k and len(k) == 1 and k.isprintable():
                got += k
                sys.stdout.write('*' if secret else k)
            sys.stdout.flush()

    def yes(self, words):
        return self.ask(words + ' (y/N)').lower() in ('y', 'yes', 'a', 'ano')

    def pause(self):
        sys.stdout.write(self.pal.dim('\n  Enter or Esc - back to the menu '))
        sys.stdout.flush()
        if self._raw() is None:
            try:
                input()
            except EOFError:
                pass
            return
        while self.key() not in ('enter', 'esc', 'ctrl-c', 'ctrl-d', 'q'):
            pass
        sys.stdout.write('\n')

    def stopped(self, wait):
        """In a live view: True when Esc, q or Ctrl+C was pressed within
        `wait` seconds."""
        if self._raw() is None:
            time.sleep(wait)
            return False
        return self.key(wait) in ('esc', 'q', 'ctrl-c', 'ctrl-d')

    def clear_screen(self):
        if sys.stdout.isatty():
            sys.stdout.write('\033[H\033[2J')
            sys.stdout.flush()

    # ---- the menus: a text screen, arrows and Enter (1.0.131) ----------------------
    # curses: only what changed is written, so nothing blinks; a window made
    # bigger or smaller (PuTTY's too) is drawn again to its new size. Leaving it
    # for a command's output and coming back is the one full redraw.
    HELP = ' Up/Down move   Enter choose   Esc back   ! shell   q leave '

    def status_thread(self):
        """The line at the top, asked for again every 20 s off the screen's
        thread - the menu never waits for it."""
        def look():
            while not self.closing:
                # Nothing asked while nobody sees the bar (1.0.136): in a live
                # view, a page or a shell - asked again on the way back.
                if self.plain and not self.st_now:
                    time.sleep(0.5)
                    continue
                self.st_now = False
                st = self.out('--status') or {}
                active = '?'
                if shutil.which('systemctl'):
                    try:
                        active = subprocess.run(['systemctl', 'is-active', SERVICE],
                                                capture_output=True, text=True,
                                                timeout=10).stdout.strip() or '?'
                    except Exception:                   # noqa: BLE001
                        pass
                self.st = dict(st, _active=active)
                self.st_gen += 1
                for _ in range(60):
                    if self.closing or self.st_now:
                        break
                    time.sleep(0.5)
        threading.Thread(target=look, name='plexmon-console-status', daemon=True).start()

    def _put(self, y, x, text, attr=0):
        try:
            h, w = self.scr.getmaxyx()
            if y < 0 or y >= h or x >= w:
                return
            self.scr.addnstr(y, x, str(text), max(0, w - x - (1 if y == h - 1 else 0)), attr)
        except Exception:                               # noqa: BLE001 - the edge
            pass

    def _colours(self):
        c = self.curses
        self.A = {'head': c.A_REVERSE, 'sel': c.A_REVERSE | c.A_BOLD, 'ok': c.A_BOLD,
                  'warn': c.A_BOLD, 'bad': c.A_BOLD, 'dim': c.A_DIM, 'key': c.A_BOLD,
                  'title': c.A_BOLD}
        if not c.has_colors():
            return
        try:
            c.start_color()
            try:
                c.use_default_colors()
                bg = -1
            except Exception:                           # noqa: BLE001
                bg = c.COLOR_BLACK
            c.init_pair(1, c.COLOR_BLACK, c.COLOR_CYAN)
            c.init_pair(2, c.COLOR_BLACK, c.COLOR_GREEN)
            c.init_pair(3, c.COLOR_GREEN, bg)
            c.init_pair(4, c.COLOR_YELLOW, bg)
            c.init_pair(5, c.COLOR_RED, bg)
            c.init_pair(6, c.COLOR_CYAN, bg)
            self.A.update(head=c.color_pair(1), sel=c.color_pair(2) | c.A_BOLD,
                          ok=c.color_pair(3) | c.A_BOLD, warn=c.color_pair(4) | c.A_BOLD,
                          bad=c.color_pair(5) | c.A_BOLD, key=c.color_pair(6) | c.A_BOLD)
        except Exception:                               # noqa: BLE001
            pass

    def draw(self, title, items, sel, top, path):
        scr, A = self.scr, self.A
        scr.erase()
        h, w = scr.getmaxyx()
        st = self.st or {}
        # The bar at the top: what this is, and how it is.
        self._put(0, 0, ' ' * w, A['head'])
        left = ' plexmon %s  %s ' % (__version__, socket.gethostname())
        self._put(0, 0, left, A['head'] | self.curses.A_BOLD)
        active = st.get('_active') or '...'
        svc = ' service %s ' % active
        self._put(0, len(left) + 1, svc, A['sel'] if active == 'active' else
                  (A['head'] if active == '...' else self.curses.A_REVERSE | A['bad']))
        # As --status --json says it: the dashboard, Plex, the certificate,
        # ACME, the accounts.
        bits = []
        dash = st.get('dashboard') or {}
        if dash.get('overall'):
            bits.append('dashboard %s' % {'ok': 'ok', 'warn': 'WARN', 'crit': 'CRIT'}.get(
                dash['overall'], dash['overall']))
        px = dash.get('plex') or {}
        if px:
            bits.append('Plex %s' % ('running' if px.get('active') else 'STOPPED'))
        cert = st.get('certificate') or {}
        if cert.get('cn'):
            bits.append('cert %s%s' % (cert['cn'], (' %dd' % cert['days_left'])
                                       if cert.get('days_left') is not None else ''))
        if st.get('acme'):
            bits.append('ACME %s' % ('on' if st['acme'].get('on') else 'off'))
        acc = (st.get('counts') or {}).get('accounts')
        if acc is not None:
            bits.append('%s account(s)' % acc)
        short = [b.replace('dashboard ', 'dash ').replace(' account(s)', ' acc.') for b in bits]
        tries = [bits, bits[:-1], short[:-1], short[:-2]]
        y = 1
        for b in tries:
            right = ' ' + ' | '.join(b) + ' '
            if len(left) + len(svc) + len(right) + 2 < w:
                self._put(0, w - len(right), right, A['head'])
                break
        else:
            self._put(1, 1, ' | '.join(bits), A['key'])
            y = 2
        if (st.get('upgrade') or {}).get('newer'):
            self._put(y, 1, 'plexmon %s is out - Upgrades' % st['upgrade']['newer'], A['warn'])
            y += 1
        if st and st.get('daemon_alive') is False:
            self._put(y, 1, 'Settings is not answering (systemctl status %s)' % SERVICE, A['bad'])
            y += 1
        for one in (st.get('problems') or [])[:max(0, min(4, h - 12))]:
            if 'not answering' in str(one) and st.get('daemon_alive') is False:
                continue
            self._put(y, 1, '! ' + str(one), A['warn'])
            y += 1
        y += 1
        self._put(y, 2, path, A['dim'])
        self._put(y, 2 + len(path), title, A['title'])
        y += 2
        # The items, a window of them when the screen is short.
        room = max(1, h - y - 2)
        if sel < top:
            top = sel
        if sel >= top + room:
            top = sel - room + 1
        width = max(20, min(w - 6, max(len(it[1]) for it in items) + 10))
        for n, it in enumerate(items[top:top + room]):
            i = top + n
            line = ' %3s  %s' % (it[0], it[1])
            line = line + ' ' * max(0, width - len(line))
            if i == sel:
                self._put(y + n, 2, line, A['sel'])
            else:
                self._put(y + n, 2, ' %3s' % it[0], A['key'])
                self._put(y + n, 8, it[1])
        if top > 0:
            self._put(y - 1, 4, '^ more', A['dim'])
        if top + room < len(items):
            self._put(y + room, 4, 'v more', A['dim'])
        self._put(h - 1, 0, ' ' * (w - 1), A['head'])
        self._put(h - 1, 0, self.HELP[:w - 1], A['head'])
        scr.noutrefresh()
        return top

    def menu(self, title, items, path=''):
        """items: [(key, words, function[, 'plain'])]; returns 'back' or 'quit'."""
        c = self.curses
        sel, top, typed, typed_at, seen = 0, 0, '', 0.0, -1
        while True:
            if self.plain:
                # Back from a command's screen: drawn whole, once.
                c.reset_prog_mode()
                self.scr.clear()
                self.plain = False
                self.st_now = True
            top = self.draw(title, items, sel, top, path)
            self.curses.doupdate()
            self.backdrop = (lambda _s=sel, _t=top: self.draw(title, items, _s, _t, path))
            seen = self.st_gen
            self.scr.timeout(500)
            while True:
                k = self.scr.getch()
                if k != -1 or self.st_gen != seen:
                    break
                # The window's size looked at too (PuTTY and others whose
                # change is not always told): drawn again to the new one.
                try:
                    size = os.get_terminal_size(sys.__stdout__.fileno())
                    if (size.lines, size.columns) != self.scr.getmaxyx():
                        c.resizeterm(size.lines, size.columns)
                        k = c.KEY_RESIZE
                        break
                except (OSError, ValueError, c.error):
                    pass
            if k == -1:
                continue
            if k == 27:
                # An arrow some terminals send as ESC [ B rather than as the
                # key the terminal description names: read whole here.
                self.scr.nodelay(True)
                k2 = self.scr.getch()
                if k2 in (ord('['), ord('O')):
                    k3 = self.scr.getch()
                    k = {ord('A'): c.KEY_UP, ord('B'): c.KEY_DOWN, ord('C'): c.KEY_RIGHT,
                         ord('D'): c.KEY_LEFT, ord('H'): c.KEY_HOME,
                         ord('F'): c.KEY_END}.get(k3, -1)
                    if k3 in (ord('5'), ord('6')):            # PgUp / PgDn: ESC [ 5 ~
                        self.scr.getch()
                        k = c.KEY_PPAGE if k3 == ord('5') else c.KEY_NPAGE
                    if k == -1:
                        continue
                elif k2 != -1:
                    c.ungetch(k2)
                self.scr.nodelay(False)
            if k == c.KEY_RESIZE:
                try:
                    c.update_lines_cols()
                except Exception:                       # noqa: BLE001
                    pass
                self.scr.clear()
                continue
            if k in (c.KEY_UP, ord('k')):
                sel = (sel - 1) % len(items)
            elif k in (c.KEY_DOWN, ord('j'), 9):
                sel = (sel + 1) % len(items)
            elif k in (c.KEY_HOME, c.KEY_PPAGE):
                sel = 0
            elif k in (c.KEY_END, c.KEY_NPAGE):
                sel = len(items) - 1
            elif k in (27, c.KEY_LEFT, c.KEY_BACKSPACE, 127, 8):
                if path:
                    return 'back'
            elif k in (ord('q'), ord('Q')):
                return 'quit'
            elif k == ord('!'):
                self.go_plain(self.shell)
            elif ord('0') <= k <= ord('9'):
                now = time.time()
                typed = (typed if now - typed_at < 1.0 else '') + chr(k)
                typed_at = now
                hit = [i for i, it in enumerate(items) if it[0] == typed]
                if hit:
                    sel = hit[0]
                    # One that no longer number can grow into: chosen at once.
                    if not any(it[0].startswith(typed) and it[0] != typed for it in items):
                        k = 10
                        typed = ''
            if k in (10, 13, c.KEY_ENTER, c.KEY_RIGHT):
                it = items[sel]
                # Run on the text screen - its questions are windows there
                # (1.0.135); only a view of its own ('plain') leaves it.
                if len(it) > 3 and it[3] == 'plain':
                    r = self.go_plain(it[2])
                else:
                    try:
                        r = it[2]()
                    except ConsoleBack:
                        r = None
                if r == 'quit':
                    return 'quit'

    def go_plain(self, fn):
        """`fn` on the ordinary terminal - a command's output, a question, the
        live log - and back to the screen after it."""
        c = self.curses
        c.def_prog_mode()
        c.endwin()
        self.plain = True
        # A clean screen for it - not what the last command left there.
        if sys.stdout.isatty():
            sys.stdout.write('\033[H\033[2J\033[3J')
            sys.stdout.flush()
        try:
            return fn()
        except ConsoleBack:
            return None
        except KeyboardInterrupt:
            print()
            return None

    def sub(self, title, items):
        r = self.menu(title, items, path='Main menu > ')
        return 'quit' if r == 'quit' else None

    # ---- long output: a screen of its own, scrolled (1.0.134) -------------------
    # As the fullscreen pickers of video_tools: the terminal is not scrolled, a
    # window (top) into the lines is; each frame is one write (home, each line
    # cleared to its end, the rest cleared) so nothing blinks, and the scroll
    # bar is drawn after it into the last column. Arrows, PgUp/PgDn, Home/End,
    # Space; Esc, q or Enter back. A window made bigger or smaller is drawn
    # again to its size.
    def capture(self, argv, env=None):
        """What a command writes, its colour kept: (text, exit code)."""
        e = dict(os.environ, **(env or {}))
        if self.pal.on:
            e.update(FORCE_COLOR='1', SYSTEMD_COLORS='1')
        e['COLUMNS'] = str(max(60, self._cols() - 2))
        try:
            got = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, env=e, timeout=1800)
            # Made safe here whatever the colour (1.0.146): with colour off the
            # command wrote what a packet carried as it came, and page() put it
            # on root's terminal - the title, the clipboard, the screen.
            return term_safe(got.stdout.decode('utf-8', 'replace')), got.returncode
        except subprocess.TimeoutExpired:
            return 'it took more than 30 minutes and was stopped', 124
        except OSError as ex:
            return str(ex), 127

    @staticmethod
    def _cols():
        try:
            return os.get_terminal_size(sys.__stdout__.fileno()).columns
        except (OSError, ValueError):
            return 100

    @staticmethod
    def _fit(line, width):
        """`line` cut to `width` columns on the screen, its colours kept."""
        out, seen = [], 0
        for part in re.split(r'(\x1b\[[0-9;?]*[A-Za-z])', line):
            if not part:
                continue
            if part.startswith('\x1b['):
                out.append(part)
                continue
            if seen + len(part) > width:
                out.append(part[:max(0, width - seen)])
                out.append('\x1b[0m')
                return ''.join(out)
            out.append(part)
            seen += len(part)
        return ''.join(out) + ('\x1b[0m' if '\x1b[' in line else '')

    def page(self, title, text):
        p = self.pal
        lines = [ln.expandtabs(8).replace('\r', '') for ln in text.rstrip('\n').split('\n')] or ['']
        tty = self._raw() is not None and sys.stdout.isatty()
        if not tty:
            print(text)
            return
        out = sys.stdout
        out.write('\x1b[?1049h\x1b[?25l')
        top, size = 0, None
        try:
            while True:
                try:
                    sz = os.get_terminal_size(sys.__stdout__.fileno())
                    cols, rows = max(40, sz.columns), max(8, sz.lines)
                except OSError:
                    cols, rows = 100, 30
                avail = max(3, rows - 2)
                top = max(0, min(top, max(0, len(lines) - avail)))
                end = min(len(lines), top + avail)
                head = ' %s ' % title
                where = ' %d-%d of %d ' % (top + 1, end, len(lines)) if len(lines) > avail \
                    else ' %d lines ' % len(lines)
                bar = (head + ' ' * max(0, cols - len(head) - len(where)) + where)[:cols]
                foot = (' Up/Down PgUp/PgDn Home/End scroll   Esc, q or Enter back ')[:cols - 1]
                body = [self._fit(ln, cols - 2) for ln in lines[top:end]]
                body += [''] * (avail - len(body))
                frame = ['\x1b[7m' + bar + '\x1b[0m' if p.on else bar] + body + \
                    ['\x1b[7m' + foot + ' ' * max(0, cols - 1 - len(foot)) + '\x1b[0m'
                     if p.on else foot]
                out.write('\x1b[H' + '\n'.join(ln + '\x1b[K' for ln in frame) + '\x1b[J')
                self._scrollbar(2, avail, len(lines), avail, top, cols)
                out.flush()
                size = (cols, rows)
                while True:
                    k = self.key(0.3)
                    if k is not None:
                        break
                    try:
                        sz = os.get_terminal_size(sys.__stdout__.fileno())
                        if (max(40, sz.columns), max(8, sz.lines)) != size:
                            break
                    except OSError:
                        pass
                if k in ('esc', 'q', 'enter', 'ctrl-c', 'ctrl-d'):
                    return
                if k in ('up', 'k'):
                    top -= 1
                elif k in ('down', 'j'):
                    top += 1
                elif k in ('pgup', 'b'):
                    top -= avail - 1
                elif k in ('pgdn', ' ', 'f'):
                    top += avail - 1
                elif k in ('home', 'g'):
                    top = 0
                elif k in ('end', 'G'):
                    top = len(lines)
        finally:
            out.write('\x1b[?25h\x1b[?1049l')
            out.flush()

    def _scrollbar(self, base_row, nrows, total, visible, top, cols):
        if total <= visible or nrows <= 0:
            return
        thumb = max(1, min(nrows, round(nrows * visible / total)))
        span = nrows - thumb
        denom = max(1, total - visible)
        tstart = max(0, min(span, round(span * (top / denom))))
        on = self.pal.on
        parts = ['\x1b[?7l']
        for r in range(nrows):
            hit = tstart <= r < tstart + thumb
            g = '\u2588' if hit else '\u2502'
            if on:
                g = ('\x1b[36m' if hit else '\x1b[2m') + g + '\x1b[0m'
            parts.append('\x1b[%d;%dH%s' % (base_row + r, cols, g))
        parts.append('\x1b[?7h')
        sys.stdout.write(''.join(parts))

    def show(self, *args):
        """`plexmon ARGS`, its output on a screen of its own."""
        # A question left empty is nothing asked for: back to the menu.
        if any(a is None or str(a).strip() == '' for a in args):
            return
        self.clear_screen()
        print(self.pal.dim('  working… $ plexmon ' + ' '.join(self._quote(a) for a in args)))
        argv = plexmon_argv() + [str(a) for a in args]
        text, rc = self.capture(argv)
        if rc not in (0, None):
            text += '\n' + self.pal.dim('(it ended with %d)' % rc)
        self.page('$ plexmon ' + ' '.join(self._quote(a) for a in args), text)

    def show_live(self, *args):
        """`plexmon ARGS` straight on the terminal - one that asks something."""
        if any(a is None or str(a).strip() == '' for a in args):
            return
        self.clear_screen()
        self.cmd(*args)
        self.pause()

    LOG_TONES = {'error': 'bad', 'crit': 'bad', 'warn': 'warn', 'info': '', 'debug': 'dim'}

    LIVE_KEEP = 20000

    def live_log(self, what=''):
        path = LOG_FILE
        st = {'f': None, 'ino': None}

        def opened():
            # Not through a link, nor stuck on a FIFO (1.0.149): the folder is
            # root's console, never through a link.
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                import stat as _st
                if not _st.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError('not a plain file')
            except OSError:
                os.close(fd)
                raise
            return os.fdopen(fd, 'r', encoding='utf-8', errors='replace')

        def first():
            try:
                f = opened()
            except OSError as e:
                return [self.pal.bad_line('%s cannot be read: %s' % (path, e))]
            st['f'] = f
            st['ino'] = os.fstat(f.fileno()).st_ino
            # Read from near the end only: a log can be gigabytes.
            f.seek(0, 2)
            start = max(0, f.tell() - 2 * 1024 * 1024)
            f.seek(start)
            got = f.read().splitlines()
            # The first line only when reading began inside it (1.0.146): a short
            # log lost its first whole line.
            if start and len(got) > 1:
                got = got[1:]
            return got[-self.LIVE_KEEP:]

        def more():
            f = st['f']
            if f is None:
                return []
            out = []
            while len(out) < 5000:
                ln = f.readline()
                if not ln:
                    break
                out.append(ln.rstrip('\n'))
            if not out:
                try:
                    if os.stat(str(path)).st_ino != st['ino']:      # rotated
                        f.close()
                        st['f'] = opened()
                        st['ino'] = os.fstat(st['f'].fileno()).st_ino
                except OSError:
                    pass
            return out
        try:
            self.live_view('Live log  %s' % path, first, more, self._log_text, what)
        finally:
            if st['f']:
                st['f'].close()

    @staticmethod
    def _grep(find):
        """What is typed as `grep -i` takes it: a test of one line."""
        if not find:
            return lambda s: True
        try:
            rx = re.compile(find, re.I)
            return lambda s: rx.search(s) is not None
        except (re.error, OverflowError, RecursionError):   # a{9999999999} too (1.0.139)
            low = find.lower()
            return lambda s: low in s.lower()

    def _mark(self, coloured, find):
        """The hits of `find` shown reversed - in the text between the colours."""
        if not find or not self.pal.on:
            return coloured
        try:
            rx = re.compile(find, re.I)
        except (re.error, OverflowError, RecursionError):
            rx = re.compile(re.escape(find), re.I)
        out = []
        for part in re.split(r'(\x1b\[[0-9;?]*[A-Za-z])', coloured):
            if part.startswith('\x1b['):
                out.append(part)
            elif part:
                out.append(rx.sub(lambda m: '\x1b[7m' + m.group(0) + '\x1b[27m'
                                  if m.group(0) else '', part))
        return ''.join(out)

    @staticmethod
    def _slice(line, start, width):
        """Columns start..start+width of `line` on the screen, its colours kept."""
        out, seen = [], 0
        end = start + width
        for part in re.split(r'(\x1b\[[0-9;?]*[A-Za-z])', line):
            if not part:
                continue
            if part.startswith('\x1b['):
                out.append(part)
                continue
            a, b = seen, seen + len(part)
            if b > start and a < end:
                out.append(part[max(0, start - a):max(0, end - a)])
            seen = b
        return ''.join(out) + ('\x1b[0m' if '\x1b[' in line else '')

    def live_view(self, title, first, more, paint, find=''):
        """The live screen: `first()` the lines there are, `more()` those come
        since (asked a few times a second), `paint(line)` its colours."""
        p = self.pal
        if self._raw() is None or not sys.stdout.isatty():
            for ln in first() + more():
                print(ln)
            return
        import termios
        import tty
        fd = sys.stdin.fileno()
        was = termios.tcgetattr(fd)
        out = sys.stdout
        lines = []                       # (plain, coloured)

        def add(raw):
            for ln in raw:
                ln = term_safe(ln.expandtabs(8).replace('\r', ''))
                col = (paint(ln) if paint else ln) if p.on else self._plain_text(ln)
                lines.append((self._plain_text(col), col))
        add(first())
        test = self._grep(find)
        hits = [i for i, x in enumerate(lines) if test(x[0])]
        top, follow, xoff, new, dirty, size = 0, True, 0, 0, True, None
        tty.setcbreak(fd, termios.TCSANOW)
        out.write('\x1b[?1049h\x1b[?25l')
        try:
            while True:
                came = more()
                if came:
                    n0 = len(lines)
                    add(came)
                    got = [i for i in range(n0, len(lines)) if test(lines[i][0])]
                    hits += got
                    if not follow:
                        new += len(got)
                    if len(lines) > self.LIVE_KEEP * 1.25:
                        cut = len(lines) - self.LIVE_KEEP
                        gone = sum(1 for i in hits if i < cut)
                        del lines[:cut]
                        hits = [i - cut for i in hits if i >= cut]
                        top = max(0, top - gone)
                    dirty = dirty or bool(got)
                try:
                    sz = os.get_terminal_size(sys.__stdout__.fileno())
                    cols, rows = max(40, sz.columns), max(8, sz.lines)
                except OSError:
                    cols, rows = 100, 30
                if (cols, rows) != size:
                    size, dirty = (cols, rows), True
                avail = max(3, rows - 2)
                last = max(0, len(hits) - avail)
                if follow:
                    top = last
                top = max(0, min(top, last))
                if dirty:
                    end = min(len(hits), top + avail)
                    where = (' %d-%d of %d ' % (top + 1, end, len(hits))) if hits else ' 0 '
                    state = ' following ' if follow else (' PAUSED%s ' % (
                        ', %d new below' % new if new else ''))
                    head = ' %s ' % title
                    gp = '  grep -i: %s_ ' % find if find else '  type to grep '
                    right = state + where
                    bar = (head + gp)[:max(0, cols - len(right))]
                    bar = bar + ' ' * max(0, cols - len(bar) - len(right)) + right
                    foot = (' type: grep (any case)  Bksp  Ctrl+U clear   Up/Down PgUp/PgDn '
                            'Home  End: follow   Left/Right  Esc back ')[:cols - 1]
                    body = [self._slice(self._mark(lines[i][1], find), xoff, cols - 2)
                            for i in hits[top:end]]
                    if not hits:
                        body = ['', '   ' + (p.dim('nothing with "%s" yet' % find) if find
                                             else p.dim('nothing yet'))]
                    body += [''] * (avail - len(body))
                    inv = (lambda t: '\x1b[7m' + t + '\x1b[0m') if p.on else (lambda t: t)
                    tone = '\x1b[33m' if (not follow and p.on) else ''
                    frame = [tone + inv(bar[:cols])] + body[:avail] + \
                        [inv(foot + ' ' * max(0, cols - 1 - len(foot)))]
                    out.write('\x1b[H' + '\n'.join(ln + '\x1b[K' for ln in frame) + '\x1b[J')
                    self._scrollbar(2, avail, len(hits), avail, top, cols)
                    out.flush()
                    dirty = False
                k = self.key(0.25)
                if k is None:
                    continue
                dirty = True
                if k in ('esc', 'ctrl-c', 'ctrl-d'):
                    if find and k == 'esc':
                        find = ''
                    else:
                        return
                elif k == 'up':
                    follow, top = False, top - 1
                elif k == 'down':
                    top += 1
                    follow = top >= last
                elif k == 'pgup':
                    follow, top = False, top - (avail - 1)
                elif k == 'pgdn':
                    top += avail - 1
                    follow = top >= last
                elif k == 'home':
                    follow, top = False, 0
                elif k in ('end', 'enter'):
                    follow, new = True, 0
                elif k == 'left':
                    xoff = max(0, xoff - 20)
                    continue
                elif k == 'right':
                    xoff += 20
                    continue
                elif k == 'bs':
                    find = find[:-1]
                elif k == 'ctrl-u':
                    find = ''
                elif k and len(k) == 1 and k.isprintable():
                    find += k
                else:
                    continue
                if k in ('bs', 'ctrl-u', 'esc') or (k and len(k) == 1 and k.isprintable()):
                    test = self._grep(find)
                    hits = [i for i, x in enumerate(lines) if test(x[0])]
                    follow, new = True, 0
                if follow:
                    new = 0
        finally:
            out.write('\x1b[?25h\x1b[?1049l')
            out.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, was)

    def _log_text(self, ln):
        """One line of the log, coloured (colorama when it is there): the time
        dim, the level by how bad, what it is about in cyan, addresses in
        magenta, accept and reject in green and red, a whole warning in yellow
        and a whole error in red."""
        p = self.pal
        ln = ln.rstrip('\n')
        m = re.match(r'^(\S+ \S+) (\w+)(\s+)(.*)$', ln)
        if not m or not p.on:
            return ln
        lvl, rest = m.group(2), m.group(4)
        tone = self.LOG_TONES.get(lvl, '')
        paint = {'bad': p.bad, 'warn': p.warn, 'dim': p.dim}.get(tone)
        what = re.match(r'^([a-z][a-z -]{1,30}): (.*)$', rest)
        head, body = (what.group(1) + ':', what.group(2)) if what else ('', rest)

        def words(t):
            t = re.sub(r'(\[?[0-9a-fA-F:]*\d{1,3}(?:\.\d{1,3}){3}\]?(?::\d+)?|\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}\b)',
                       lambda x: p.addr(x.group(1)) + (p.c_warn if tone == 'warn' else
                                                       p.c_bad if tone == 'bad' else ''), t)
            t = re.sub(r'\b(accept(?:ed)?|signed in|in place|done|ok)\b',
                       lambda x: p.good(x.group(1)) + (p.c_warn if tone == 'warn' else ''), t)
            t = re.sub(r'\b(reject(?:ed)?|refused|failed|wrong|locked out|not answering)\b',
                       lambda x: p.bad(x.group(1)) + (p.c_warn if tone == 'warn' else ''), t)
            return t
        body = words(body)
        if paint is not None and tone != 'dim':
            body = paint(body)
        elif tone == 'dim':
            body = p.dim(body)
        lv = (paint or p.info)(lvl.ljust(5))
        return '%s %s %s%s' % (p.dim(m.group(1)), lv, p.info(head) + (' ' if head else ''), body)

    def _log_line(self, ln):
        print(self._log_text(ln))



    def _pw_fields(self):
        return [{'key': 'pw', 'label': 'password', 'kind': 'secret', 'need': True,
                 'help': 'At least 8 characters; only stars are shown.',
                 'check': lambda v, _: None if len(v) >= 8 else 'at least 8 characters'},
                {'key': 'again', 'label': 'again', 'kind': 'secret', 'need': True,
                 'check': lambda v, vals: None if v == vals.get('pw')
                 else 'the two passwords are not the same'}]


    def _settings_rows(self):
        got = self.out('--settings') or []
        rows = got.get('settings') if isinstance(got, dict) else got
        by = {r['key']: r for r in rows or [] if isinstance(r, dict) and r.get('key')}
        out, seen = [], set()
        for pane, words in self.PANE_TITLES:
            keys = [k for k in PANES.get(pane, []) if k in by]
            if not keys:
                continue
            out.append(('head', words))
            for k in keys:
                out.append(('set', by[k]))
                seen.add(k)
        rest = [k for k in by if k not in seen]
        if rest:
            out.append(('head', 'Everything else'))
            out += [('set', by[k]) for k in rest]
        return out

    @staticmethod
    def _shown(v):
        if v is True:
            return 'on'
        if v is False:
            return 'off'
        return '' if v is None else str(v)

    def _kind(self, key):
        shape = TUNABLE.get(key)
        d = DEFAULTS.get(key)
        if isinstance(d, bool) or shape == (0, 1):
            return 'switch', ['1', '0']
        if isinstance(shape, tuple) and shape and isinstance(shape[0], str):
            return 'choice', [str(x) for x in shape]
        if isinstance(shape, tuple) and len(shape) == 2:
            return 'number', list(shape)
        return 'text', None

    def settings_tui(self):
        c, A = self.curses, self.A
        rows = self._settings_rows()
        if not rows:
            return None
        sel = next(i for i, r in enumerate(rows) if r[0] == 'set')
        top, said, said_bad, find = 0, '', False, ''
        while True:
            if self.plain:
                c.reset_prog_mode()
                self.scr.clear()
                self.plain = False

            def paint():
                nonlocal top
                h, w = self.scr.getmaxyx()
                self.scr.erase()
                self._put(0, 0, ' ' * w, A['head'])
                self._put(0, 0, ' plexmon %s  Settings' % __version__, A['head'] | c.A_BOLD)
                if find:
                    self._put(0, max(0, w - len(find) - 12), ' find: %s ' % find, A['head'])
                avail = max(3, h - 3)
                if sel < top:
                    top = sel
                if sel >= top + avail:
                    top = sel - avail + 1
                top = max(0, min(top, max(0, len(rows) - avail)))
                if top > 0 and rows[top - 1][0] == 'head' and sel - top < avail - 1:
                    top -= 1                    # the group's heading with its first setting
                namew = max(20, min(44, w // 3))
                valw = max(10, min(40, w - namew - 30))
                for n, r in enumerate(rows[top:top + avail]):
                    y = 1 + n
                    if r[0] == 'head':
                        self._put(y, 1, r[1].upper(), A['key'])
                        continue
                    st = r[1]
                    val = self._shown(st.get('value'))
                    line = '  %-*s %-*s %s' % (namew, (st.get('name') or st['key'])[:namew], valw,
                                              val[:valw], st.get('key'))
                    if top + n == sel:
                        self._put(y, 0, line + ' ' * max(0, w - 2 - len(line)), A['sel'])
                    else:
                        self._put(y, 0, '  ' + (st.get('name') or st['key'])[:namew])
                        tone = A['dim'] if st.get('source') == 'default' else A['ok']
                        self._put(y, 3 + namew, val[:valw], tone)
                        self._put(y, 4 + namew + valw, st.get('key'), A['dim'])
                # The bar on the right.
                total = len(rows)
                if total > avail:
                    thumb = max(1, min(avail, round(avail * avail / total)))
                    tstart = max(0, min(avail - thumb, round((avail - thumb) * top
                                                              / max(1, total - avail))))
                    utf = 'utf' in (sys.stdout.encoding or '').lower()
                    for r in range(avail):
                        hit = tstart <= r < tstart + thumb
                        self._put(1 + r, w - 1, ('\u2588' if hit else '\u2502') if utf
                                  else ('#' if hit else '|'), A['key'] if hit else A['dim'])
                if said:
                    self._put(h - 2, 1, said[:w - 2], A['bad'] if said_bad else A['ok'])
                help_ = ' Up/Down PgUp/PgDn Home/End move   Enter change   / find   Esc back '
                self._put(h - 1, 0, ' ' * (w - 1), A['head'])
                self._put(h - 1, 0, help_[:w - 1], A['head'])
                self.scr.noutrefresh()
                return h, avail
            self.backdrop = paint
            h, avail = paint()
            c.doupdate()
            self.scr.timeout(500)
            k = self._getkey()
            if k is None:
                continue
            said = ''
            sets = [i for i, r in enumerate(rows) if r[0] == 'set']
            if k == 'esc':
                if find:
                    find = ''
                    continue
                return None
            if k == 'q':
                return 'quit'
            if k in ('up', 'down', 'pgup', 'pgdn', 'home', 'end'):
                pos = sets.index(sel) if sel in sets else 0
                pos = {'up': pos - 1, 'down': pos + 1, 'pgup': pos - (avail - 2),
                       'pgdn': pos + (avail - 2), 'home': 0, 'end': len(sets) - 1}[k]
                sel = sets[max(0, min(len(sets) - 1, pos))]
            elif k == '/':
                find = self._line_in(h - 2, 'Find (a word of its name or key): ', '') or ''
                if find:
                    hit = [i for i in sets if find.lower() in (
                        str(rows[i][1].get('name')) + ' ' + rows[i][1]['key']).lower()]
                    if hit:
                        sel = hit[0]
                    else:
                        said, said_bad = 'nothing with "%s"' % find, True
            elif k == 'n' and find:
                hit = [i for i in sets if i > sel and find.lower() in (
                    str(rows[i][1].get('name')) + ' ' + rows[i][1]['key']).lower()]
                if hit:
                    sel = hit[0]
            elif k == 'enter':
                done = self._edit_one(rows[sel][1])
                if done:
                    said, said_bad = done
                    if not said_bad:
                        cur = rows[sel][1]['key']
                        rows = self._settings_rows()
                        sel = next((i for i, r in enumerate(rows)
                                    if r[0] == 'set' and r[1]['key'] == cur), sel)

    def _getkey(self, win=None):
        """A key on the text screen, by name: 'up', 'enter', 'esc', or the character."""
        c = self.curses
        src = win or self.scr
        k = src.getch()
        if k == -1:
            # The window's size looked at, as the menus do.
            try:
                size = os.get_terminal_size(sys.__stdout__.fileno())
                if (size.lines, size.columns) != self.scr.getmaxyx():
                    c.resizeterm(size.lines, size.columns)
                    self.scr.clear()
                    return 'resize'
            except (OSError, ValueError, c.error):
                pass
            return None
        if k == 27:
            src.nodelay(True)
            k2 = src.getch()
            if k2 in (ord('['), ord('O')):
                seq = ''
                while True:
                    k3 = src.getch()
                    if k3 == -1:
                        break
                    seq += chr(k3)
                    if chr(k3).isalpha() or chr(k3) == '~':
                        break
                src.nodelay(False)
                return {'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left', 'H': 'home',
                        'F': 'end', '5~': 'pgup', '6~': 'pgdn', '1~': 'home', '4~': 'end',
                        '7~': 'home', '8~': 'end', '3~': 'del'}.get(seq, None)
            if k2 != -1:
                c.ungetch(k2)
            src.nodelay(False)
            return 'esc'
        named = {c.KEY_UP: 'up', c.KEY_DOWN: 'down', c.KEY_LEFT: 'left', c.KEY_RIGHT: 'right',
                 c.KEY_PPAGE: 'pgup', c.KEY_NPAGE: 'pgdn', c.KEY_HOME: 'home', c.KEY_END: 'end',
                 c.KEY_ENTER: 'enter', 10: 'enter', 13: 'enter', c.KEY_BACKSPACE: 'bs',
                 127: 'bs', 8: 'bs', c.KEY_DC: 'del', c.KEY_RESIZE: 'resize', 21: 'ctrl-u',
                 9: 'tab'}
        if k in named:
            if k == c.KEY_RESIZE:
                try:
                    c.update_lines_cols()
                except Exception:                       # noqa: BLE001
                    pass
            return named[k]
        if 0xc0 <= k < 0xf8:
            # getch() gives UTF-8 a byte at a time (1.0.139): "ž" came as two
            # Latin-1 letters, and a password typed here was not the one meant.
            raw = bytearray([k])
            src.nodelay(True)
            try:
                for _ in range(1 if k < 0xe0 else 2 if k < 0xf0 else 3):
                    k2 = src.getch()
                    if not 0x80 <= k2 < 0xc0:
                        break
                    raw.append(k2)
            finally:
                src.nodelay(False)
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError:
                return None
        if 32 <= k < 0x110000:
            try:
                return chr(k)
            except ValueError:
                return None
        return None

    def _line_in(self, y, prompt, text, secret=False, win=None, x=1, width=None):
        """A line typed on the text screen: Enter gives it, Esc gives None."""
        c = self.curses
        scr = win or self.scr
        pos = len(text)
        try:
            c.curs_set(1)
        except Exception:                               # noqa: BLE001
            pass
        try:
            while True:
                h, w = scr.getmaxyx()
                room = (width or (w - x - len(prompt) - 2))
                shown = ('*' * len(text)) if secret else text
                start = max(0, pos - room + 1)
                try:
                    scr.addstr(y, x, prompt, self.A['key'])
                    scr.addstr(y, x + len(prompt), shown[start:start + room].ljust(room),
                               self.curses.A_UNDERLINE)
                    scr.move(y, x + len(prompt) + pos - start)
                except c.error:
                    pass
                scr.refresh()
                scr.timeout(500)
                k = self._getkey(scr)
                if k == 'enter':
                    return text
                if k == 'esc':
                    return None
                if k == 'bs' and pos > 0:
                    text = text[:pos - 1] + text[pos:]
                    pos -= 1
                elif k == 'del':
                    text = text[:pos] + text[pos + 1:]
                elif k == 'left':
                    pos = max(0, pos - 1)
                elif k == 'right':
                    pos = min(len(text), pos + 1)
                elif k == 'home':
                    pos = 0
                elif k == 'end':
                    pos = len(text)
                elif k == 'ctrl-u':
                    text, pos = '', 0
                elif k and len(k) == 1 and k.isprintable():
                    text = text[:pos] + k + text[pos:]
                    pos += 1
        finally:
            try:
                c.curs_set(0)
            except Exception:                           # noqa: BLE001
                pass

    def _box(self, win, h, w):
        """A frame round a window, in UTF-8 lines where the terminal takes them."""
        c, A = self.curses, self.A
        if 'utf' in (sys.stdout.encoding or '').lower():
            hz, vt, tl, tr, bl, br = '\u2500', '\u2502', '\u250c', '\u2510', '\u2514', '\u2518'
        else:
            hz, vt, tl, tr, bl, br = '-', '|', '+', '+', '+', '+'
        for y, line in ((0, tl + hz * (w - 2) + tr), (h - 1, bl + hz * (w - 2) + br)):
            try:
                win.addstr(y, 0, line, A['key'])
            except c.error:
                pass                                    # the bottom-right corner
        for y in range(1, h - 1):
            for x in (0, w - 1):
                try:
                    win.addstr(y, x, vt, A['key'])
                except c.error:
                    pass

    def _edit_one(self, st):
        """The window to change one setting: what it is, what it may be, the
        value checked as the daemon checks it before it is saved. Returns
        (words, bad) or None when nothing was done."""
        c, A = self.curses, self.A
        key = st['key']
        kind, shape = self._kind(key)
        secret = bool(st.get('secret'))
        cur = self._shown(st.get('value'))
        if kind == 'switch':
            cur = '1' if st.get('value') in (True, 1, '1', 'on') else '0'
        text = '' if secret else cur
        choice = shape.index(cur) if kind in ('switch', 'choice') and cur in (shape or []) else 0
        err = ''
        helptext = re.sub(r'<[^>]+>', '', html.unescape(str(st.get('says') or '')))
        while True:
            self._under()
            h, w = self.scr.getmaxyx()
            ww = max(40, min(96, w - 4))
            body = []
            for para in helptext.split('\n'):
                body += textwrap.wrap(para, ww - 4) or ['']
            facts = ['now: %s%s' % ('(hidden)' if secret else (self._shown(st.get('value')) or '(empty)'),
                                    '' if st.get('source') == 'default' else
                                    '   - %s' % (st.get('source_says') or st.get('source'))),
                     'as it comes: %s' % (self._shown(st.get('default')) or '(empty)')]
            if kind == 'number':
                facts.append('a whole number from %s to %s' % (shape[0], shape[1]))
            elif kind == 'choice':
                facts.append('one of: ' + ', '.join(shape))
            if st.get('file_only'):
                facts.append('kept in config.json on this machine - it takes effect after a restart')
            if st.get('node_key'):
                facts.append('this node\'s own (each node of a cluster has its own)')
            # The help cut to what the screen has room for - the rest always fits.
            fixed = len(facts) + 9 + (len(shape) - 1 if kind == 'choice' else 0)
            body = body[:max(1, h - 2 - fixed)]
            wh = min(h - 2, len(body) + fixed)
            win = c.newwin(wh, ww, max(0, (h - wh) // 2), max(0, (w - ww) // 2))
            win.keypad(True)
            win.erase()
            self._box(win, wh, ww)
            title = ' %s ' % (st.get('name') or key)
            try:
                win.addnstr(0, 2, title, ww - 4, A['title'])
                win.addnstr(1, 2, key, ww - 4, A['dim'])
                y = 2
                for ln in body:
                    win.addnstr(y, 2, ln, ww - 4)
                    y += 1
                y += 1
                for f in facts:
                    win.addnstr(y, 2, f, ww - 4, A['dim'])
                    y += 1
                y += 1
                if err:
                    win.addnstr(wh - 2, 2, err[:ww - 4], ww - 4, A['bad'])
                else:
                    win.addnstr(wh - 2, 2, ('Left/Right choose' if kind in ('switch', 'choice')
                                            else 'type the new value') +
                                '   Enter save   Esc cancel', ww - 4, A['dim'])
            except c.error:
                pass
            if kind in ('switch', 'choice'):
                words = {'1': 'on', '0': 'off'} if kind == 'switch' else {}
                x = 2
                for i, opt in enumerate(shape):
                    lab = ' %s ' % (words.get(opt) or (CHOICE_WORDS.get(key, {}).get(opt) or opt))
                    if kind == 'choice':
                        # One under the other: their words can be long.
                        lab = (' > ' if i == choice else '   ') + lab.strip() + ' '
                        try:
                            win.addnstr(y, 2, lab, ww - 4, A['sel'] if i == choice else 0)
                        except c.error:
                            pass
                        y += 1
                        continue
                    try:
                        win.addnstr(y, x, lab, max(0, ww - x - 2), A['sel'] if i == choice else 0)
                    except c.error:
                        pass
                    x += len(lab) + 1
                win.noutrefresh()
                c.doupdate()
                win.timeout(500)
                k = self._getkey(win)
                if k == 'esc':
                    return None
                if k in ('left', 'up'):
                    choice = (choice - 1) % len(shape)
                elif k in ('right', 'down', 'tab', ' '):
                    choice = (choice + 1) % len(shape)
                elif k == 'enter':
                    value = shape[choice]
                else:
                    continue
                if k != 'enter':
                    continue
            else:
                win.noutrefresh()
                c.doupdate()
                got = self._line_in(y, 'value: ', text, secret=secret, win=win, x=2,
                                    width=ww - 14)
                if got is None:
                    return None
                text = got
                if secret and not got:
                    return ('nothing typed - %s stays as it was' % key, False)
                value = got
            # Checked here as the daemon checks it, then saved by `plexmon --set`.
            try:
                check_setting(key, value)
            except (BadSetting, ValueError, TypeError) as e:
                err = str(e)
                continue
            # A secret goes by the environment, not the command line (1.0.136).
            env = {'PLEXMON_VALUE_' + key.upper(): value} if secret else None
            out, rc = self.capture(plexmon_argv() + ['--set', '%s=%s' % (
                key, '@env' if secret else value), '--no-color'], env=env)
            if rc != 0:
                err = (out.strip().splitlines() or ['it was not saved'])[-1]
                continue
            return ('%s = %s - saved%s' % (key, '(hidden)' if secret else value,
                                           '; the running service takes it within seconds'
                                           if not st.get('file_only') else
                                           '; restart the service for it'), False)

    # ---- windows on the text screen for every menu: arrows and Enter (1.0.135) ----
    # Nothing is typed at a bare prompt any more: a question is a window (a
    # form) whose fields are moved between by the arrows, a choice is picked
    # by the arrows, a list (tokens, accounts, clients, files…) is scrolled and
    # Enter on one offers what can be done with it. What is typed is checked
    # before anything is run; Esc closes the window and goes back. The screen
    # under a window is drawn again by `self.backdrop` - so a window made
    # bigger or smaller (PuTTY's too) is drawn right.
    def _fresh(self):
        """Back from a command's own screen: the text screen drawn whole again."""
        if self.plain:
            self.curses.reset_prog_mode()
            self.scr.clear()
            self.plain = False
            self.st_now = True

    def _under(self):
        """The screen under a window, written but not yet shown."""
        self._fresh()
        fn = getattr(self, 'backdrop', None)
        if fn:
            try:
                fn()
                return
            except Exception:                           # noqa: BLE001
                pass
        self.scr.noutrefresh()

    def _window(self, want_h, want_w):
        """A framed window in the middle of the screen; (win, h, w)."""
        c = self.curses
        h, w = self.scr.getmaxyx()
        ww = max(20, min(want_w, w - 2))
        wh = max(5, min(want_h, h - 2))
        win = c.newwin(wh, ww, max(0, (h - wh) // 2), max(0, (w - ww) // 2))
        win.keypad(True)
        win.erase()
        self._box(win, wh, ww)
        return win, wh, ww

    def _wput(self, win, y, x, text, attr=0, width=None):
        try:
            wh, ww = win.getmaxyx()
            if 0 < y < wh - 1 and x < ww - 1:
                win.addnstr(y, x, str(text), max(0, (width or ww) - (x if width is None else 0)
                                                 - (1 if width is None else 0)), attr)
        except self.curses.error:
            pass

    def _wkey(self, win, wait=500):
        win.timeout(wait)
        return self._getkey(win)

    def _wrap(self, text, width):
        out = []
        for para in str(text or '').split('\n'):
            out += textwrap.wrap(para, max(10, width)) or ['']
        return out

    @staticmethod
    def _plain_text(text):
        return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', str(text or ''))

    def note(self, title, text, tone=''):
        """Words in a window; any of Enter, Esc, q or Space closes it."""
        A = self.A
        while True:
            self._under()
            h, w = self.scr.getmaxyx()
            ww = max(30, min(100, w - 4))
            lines = self._wrap(text, ww - 4)[:max(1, h - 6)]
            win, wh, ww = self._window(len(lines) + 4, max(len(title) + 8, min(
                ww, max(len(x) for x in lines + ['']) + 6, ww)))
            self._wput(win, 0, 2, ' %s ' % title, A['title'])
            try:
                win.addnstr(0, 2, ' %s ' % title, ww - 4, A['title'])
            except self.curses.error:
                pass
            for i, ln in enumerate(lines):
                self._wput(win, 1 + i, 2, ln, A.get(tone, 0) if tone else 0)
            self._wput(win, wh - 2, 2, 'Enter or Esc - back', A['dim'])
            win.noutrefresh()
            self.curses.doupdate()
            k = self._wkey(win)
            if k in ('enter', 'esc', 'q', ' '):
                return

    def working(self, words):
        """A window that says something is being done (the screen waits for it)."""
        self._under()
        win, wh, ww = self._window(3, min(100, len(words) + 6))
        self._wput(win, 1, 2, words, self.A['dim'])
        win.noutrefresh()
        self.curses.doupdate()

    def confirm(self, title, words, yes='Yes', no='No', danger=False):
        """Yes or no, chosen by the arrows (or y / n); No unless chosen."""
        A = self.A
        pick = 1
        while True:
            self._under()
            h, w = self.scr.getmaxyx()
            ww = max(40, min(80, w - 4))
            lines = self._wrap(words, ww - 4)[:max(1, h - 8)]
            win, wh, ww = self._window(len(lines) + 5, ww)
            try:
                win.addnstr(0, 2, ' %s ' % title, ww - 4, A['bad'] if danger else A['title'])
            except self.curses.error:
                pass
            for i, ln in enumerate(lines):
                self._wput(win, 1 + i, 2, ln)
            x = 2
            y = wh - 2
            for i, lab in enumerate((yes, no)):
                lab = '  %s  ' % lab
                self._wput(win, y, x, lab, (A['bad'] if danger and i == 0 else A['sel'])
                           if i == pick else A['key'])
                x += len(lab) + 2
            self._wput(win, y, x + 2, 'Left/Right  Enter  Esc', A['dim'])
            win.noutrefresh()
            self.curses.doupdate()
            k = self._wkey(win)
            if k in ('esc', 'n', 'N'):
                return False
            if k in ('y', 'Y'):
                return True
            if k in ('left', 'right', 'tab', 'up', 'down'):
                pick = 1 - pick
            elif k == 'enter':
                return pick == 0

    def choose(self, title, options, words='', cur=0):
        """One of `options` ([(value, words)]), picked by the arrows; Esc None."""
        A = self.A
        if not options:
            return None
        sel = max(0, min(cur or 0, len(options) - 1))
        top = 0
        while True:
            self._under()
            h, w = self.scr.getmaxyx()
            ww = min(90, w - 4, max(36, max(len(o[1]) for o in options) + 12,
                                    len(title) + 8, len(words) + 6 if words else 0))
            head = self._wrap(words, ww - 4) if words else []
            room = max(1, min(len(options), h - 6 - len(head)))
            win, wh, ww = self._window(room + len(head) + (4 if head else 3), ww)
            try:
                win.addnstr(0, 2, ' %s ' % title, ww - 4, A['title'])
            except self.curses.error:
                pass
            y = 1
            for ln in head:
                self._wput(win, y, 2, ln, A['dim'])
                y += 1
            if head:
                y += 1
            if sel < top:
                top = sel
            if sel >= top + room:
                top = sel - room + 1
            for n, (val, lab) in enumerate(options[top:top + room]):
                i = top + n
                line = ' %2d  %s' % (i + 1, lab)
                if i == sel:
                    self._wput(win, y + n, 2, line.ljust(ww - 5), A['sel'])
                else:
                    self._wput(win, y + n, 2, ' %2d' % (i + 1), A['key'])
                    self._wput(win, y + n, 7, lab)
            if len(options) > room:
                self._wbar(win, y, room, len(options), top, ww - 2)
            win.noutrefresh()
            self.curses.doupdate()
            k = self._wkey(win)
            if k in ('esc', 'left'):
                return None
            if k == 'q':
                return None
            if k == 'up':
                sel = (sel - 1) % len(options)
            elif k in ('down', 'tab'):
                sel = (sel + 1) % len(options)
            elif k in ('home', 'pgup'):
                sel = 0
            elif k in ('end', 'pgdn'):
                sel = len(options) - 1
            elif k and k.isdigit():
                n = int(k) - 1
                if 0 <= n < len(options):
                    sel = n
                    return options[sel][0]
            elif k in ('enter', 'right'):
                return options[sel][0]

    def _wbar(self, win, y0, rows, total, top, x):
        """The scroll bar on the right of a list in a window."""
        A = self.A
        if total <= rows:
            return
        thumb = max(1, min(rows, round(rows * rows / total)))
        tstart = max(0, min(rows - thumb, round((rows - thumb) * top / max(1, total - rows))))
        utf = 'utf' in (sys.stdout.encoding or '').lower()
        for r in range(rows):
            hit = tstart <= r < tstart + thumb
            try:
                win.addstr(y0 + r, x, ('█' if hit else '│') if utf else
                           ('#' if hit else '|'), A['key'] if hit else A['dim'])
            except self.curses.error:
                pass

    def form(self, title, fields, words='', check=None, ok='OK'):
        """Questions in one window. fields: [{'key', 'label', 'kind': 'text' |
        'secret' | 'choice' | 'number', 'value', 'options': [(value, words)],
        'need': True, 'range': (a, b), 'help', 'check': fn(value, values) ->
        an error or None, 'when': fn(values) -> shown or not}]. Up/Down (Tab)
        move between them, a choice is changed by Left/Right, Enter checks
        them all and gives {key: value}; Esc gives None. A secret shows a star
        for each character, never the characters."""
        c, A = self.curses, self.A
        vals, pos = {}, {}
        for f in fields:
            f.setdefault('kind', 'text')
            if f['kind'] == 'choice':
                opts = [o[0] for o in f['options']]
                vals[f['key']] = f.get('value') if f.get('value') in opts else opts[0]
            else:
                vals[f['key']] = str(f.get('value') if f.get('value') is not None else '')
            pos[f['key']] = len(vals[f['key']]) if f['kind'] != 'choice' else 0
        at, err, err_at = 0, '', None
        try:
            c.curs_set(1)
        except Exception:                               # noqa: BLE001
            pass
        try:
            while True:
                shown = [f for f in fields if not f.get('when') or f['when'](vals)]
                at = max(0, min(at, len(shown) - 1))
                f = shown[at]
                self._under()
                h, w = self.scr.getmaxyx()
                ww = max(50, min(100, w - 4))
                head = self._wrap(words, ww - 4) if words else []
                labw = min(30, max(len(x.get('label') or x['key']) for x in shown) + 1)
                fieldw = max(8, ww - labw - 7)
                helps = self._wrap(f.get('help') or '', ww - 4) if f.get('help') else []
                helps = helps[:4]
                wh = len(head) + (1 if head else 0) + len(shown) + 2 + 4 + 1
                win, wh, ww = self._window(wh, ww)
                try:
                    win.addnstr(0, 2, ' %s ' % title, ww - 4, A['title'])
                except c.error:
                    pass
                y = 1
                for ln in head:
                    self._wput(win, y, 2, ln, A['dim'])
                    y += 1
                if head:
                    y += 1
                cursor = None
                for i, x in enumerate(shown):
                    lab = (x.get('label') or x['key'])[:labw - 1]
                    here = i == at
                    self._wput(win, y, 2, lab.rjust(labw), A['key'] if here else 0)
                    vx = 3 + labw + 1
                    v = vals[x['key']]
                    if x['kind'] == 'choice':
                        words_ = dict(x['options']).get(v, v)
                        txt = ('< %s >' % words_) if here else ('  %s' % words_)
                        self._wput(win, y, vx, txt[:fieldw], A['sel'] if here else A['ok'])
                    else:
                        t = ('*' * len(v)) if x['kind'] == 'secret' else v
                        p = pos[x['key']] if here else len(t)
                        start = max(0, p - fieldw + 1)
                        self._wput(win, y, vx, t[start:start + fieldw].ljust(fieldw),
                                   c.A_UNDERLINE | (A['key'] if here else 0))
                        if here:
                            cursor = (y, vx + p - start)
                    if err and err_at == x['key']:
                        self._wput(win, y, ww - 3, '!', A['bad'])
                    y += 1
                y += 1
                for ln in helps:
                    self._wput(win, y, 2, ln, A['dim'])
                    y += 1
                if err:
                    self._wput(win, wh - 2, 2, err[:ww - 4], A['bad'])
                else:
                    self._wput(win, wh - 2, 2, ('Up/Down field   ' + ('Left/Right choose   '
                               if any(x['kind'] == 'choice' for x in shown) else '') +
                               'Enter %s   Esc cancel' % ok)[:ww - 4], A['dim'])
                if cursor:
                    try:
                        c.curs_set(1)
                        win.move(*cursor)
                    except c.error:
                        pass
                else:
                    try:
                        c.curs_set(0)
                    except Exception:                   # noqa: BLE001
                        pass
                win.noutrefresh()
                c.doupdate()
                k = self._wkey(win)
                if k is None or k == 'resize':
                    continue
                key, kind = f['key'], f['kind']
                if k == 'esc':
                    return None
                if k in ('up',) or (k == 'tab' and False):
                    at = (at - 1) % len(shown)
                elif k in ('down', 'tab'):
                    at = (at + 1) % len(shown)
                elif k == 'enter':
                    err, err_at = '', None
                    for i, x in enumerate(shown):
                        v = vals[x['key']]
                        if x['kind'] != 'choice':
                            v = v.strip() if x['kind'] != 'secret' else v
                            vals[x['key']] = v
                        bad = None
                        if x.get('need') and v == '':
                            bad = '%s - it is needed' % (x.get('label') or x['key'])
                        elif x['kind'] == 'number' and v != '':
                            try:
                                n = int(v)
                                lo, hi = x.get('range') or (None, None)
                                if (lo is not None and n < lo) or (hi is not None and n > hi):
                                    bad = '%s - a whole number from %s to %s' % (
                                        x.get('label') or x['key'], lo, hi)
                            except ValueError:
                                bad = '%s - a whole number' % (x.get('label') or x['key'])
                        if not bad and x.get('check') and v != '':
                            try:
                                bad = x['check'](v, vals)
                            except Exception as e:              # noqa: BLE001
                                bad = str(e)
                        if bad:
                            err, err_at, at = bad, x['key'], i
                            break
                    if err:
                        continue
                    got = {x['key']: vals[x['key']] for x in shown}
                    if check:
                        bad = check(got)
                        if bad:
                            err, err_at = (bad if isinstance(bad, tuple) else (bad, None))
                            if err_at:
                                at = next((i for i, x in enumerate(shown) if x['key'] == err_at),
                                          at)
                            continue
                    return got
                elif kind == 'choice':
                    opts = [o[0] for o in f['options']]
                    i = opts.index(vals[key]) if vals[key] in opts else 0
                    if k in ('left',):
                        vals[key] = opts[(i - 1) % len(opts)]
                    elif k in ('right', ' '):
                        vals[key] = opts[(i + 1) % len(opts)]
                    err = ''
                else:
                    t, p = vals[key], pos[key]
                    if k == 'bs' and p > 0:
                        t, p = t[:p - 1] + t[p:], p - 1
                    elif k == 'del':
                        t = t[:p] + t[p + 1:]
                    elif k == 'left':
                        p = max(0, p - 1)
                    elif k == 'right':
                        p = min(len(t), p + 1)
                    elif k == 'home':
                        p = 0
                    elif k == 'end':
                        p = len(t)
                    elif k == 'ctrl-u':
                        t, p = '', 0
                    elif k and len(k) == 1 and k.isprintable():
                        if kind == 'number' and not (k.isdigit() or (k == '-' and p == 0)):
                            continue
                        t, p = t[:p] + k + t[p:], p + 1
                    else:
                        continue
                    vals[key], pos[key] = t, p
                    if err_at == key:
                        err, err_at = '', None
        finally:
            try:
                c.curs_set(0)
            except Exception:                           # noqa: BLE001
                pass

    def ask_one(self, title, label, value='', help_='', need=True, secret=False, check=None):
        """One question in a window; its answer, or None (Esc)."""
        got = self.form(title, [{'key': 'v', 'label': label, 'value': value, 'help': help_,
                                 'need': need, 'kind': 'secret' if secret else 'text',
                                 'check': check}])
        return None if got is None else got['v']

    def pick(self, title, load, cols, act=None, empty='nothing here', words=''):
        """A list to scroll with the arrows: `load()` gives its rows (asked
        again after something was done to one), `cols` [(heading, fn(row),
        width, tone fn(row) or None)]. Enter calls `act(row)`; / finds. Esc
        back, q leaves the console."""
        c, A = self.curses, self.A
        rows = load() or []
        sel, top, find, said, said_bad = 0, 0, '', '', False
        while True:
            self._fresh()
            h, w = self.scr.getmaxyx()
            seen = [r for r in rows if not find or find.lower() in ' '.join(
                str(fn(r)) for _h, fn, _w, _t in cols).lower()]
            sel = max(0, min(sel, len(seen) - 1))
            avail = max(3, h - 5 - (1 if words else 0))
            if sel < top:
                top = sel
            if sel >= top + avail:
                top = sel - avail + 1
            top = max(0, min(top, max(0, len(seen) - avail)))
            # Widths: as given; the last one takes what is left.
            ws = [cw for _h, _f, cw, _t in cols]
            fixed = sum(x + 2 for x in ws[:-1])
            ws[-1] = max(8, min(ws[-1] or 200, w - 4 - fixed))

            def paint(_title=title, _seen=seen, _top=top, _sel=sel, _ws=ws, _avail=avail):
                self.scr.erase()
                self._put(0, 0, ' ' * w, A['head'])
                self._put(0, 0, ' plexmon %s  %s ' % (__version__, _title), A['head'] | c.A_BOLD)
                right = ' %d%s ' % (len(_seen), (' of %d, with "%s"' % (len(rows), find))
                                    if find else '')
                self._put(0, max(0, w - len(right) - 1), right, A['head'])
                y = 1
                if words:
                    self._put(y, 1, words[:w - 2], A['dim'])
                    y += 1
                x = 2
                for (hd, _f, _w, _t), cw in zip(cols, _ws):
                    self._put(y + 1, x, hd[:cw], A['key'])
                    x += cw + 2
                y += 2
                if not _seen:
                    self._put(y + 1, 4, empty if not find else 'nothing with "%s"' % find,
                              A['dim'])
                for n, r in enumerate(_seen[_top:_top + _avail]):
                    i = _top + n
                    x = 2
                    if i == _sel:
                        self._put(y + n, 1, ' ' * (w - 3), A['sel'])
                    for (hd, fn, _w, tone), cw in zip(cols, _ws):
                        try:
                            v = str(fn(r))
                        except Exception:               # noqa: BLE001
                            v = '?'
                        attr = A['sel'] if i == _sel else (A.get(tone(r) or '', 0) if tone else 0)
                        self._put(y + n, x, v[:cw], attr)
                        x += cw + 2
                if len(_seen) > _avail:
                    thumb = max(1, min(_avail, round(_avail * _avail / len(_seen))))
                    ts = max(0, min(_avail - thumb, round((_avail - thumb) * _top
                                                          / max(1, len(_seen) - _avail))))
                    utf = 'utf' in (sys.stdout.encoding or '').lower()
                    for r_ in range(_avail):
                        hit = ts <= r_ < ts + thumb
                        self._put(y + r_, w - 1, ('█' if hit else '│') if utf
                                  else ('#' if hit else '|'), A['key'] if hit else A['dim'])
                if said:
                    self._put(h - 2, 1, said[:w - 2], A['bad'] if said_bad else A['ok'])
                help_ = ' Up/Down PgUp/PgDn Home/End move   Enter %s   / find   Esc back ' % (
                    'what to do with it' if act else 'look')
                self._put(h - 1, 0, ' ' * (w - 1), A['head'])
                self._put(h - 1, 0, help_[:w - 1], A['head'])
                self.scr.noutrefresh()
            self.backdrop = paint
            paint()
            c.doupdate()
            self.scr.timeout(500)
            k = self._getkey()
            if k is None:
                continue
            said = ''
            if k == 'esc':
                if find:
                    find = ''
                    continue
                return None
            if k == 'q':
                return 'quit'
            if k in ('up', 'down', 'pgup', 'pgdn', 'home', 'end'):
                sel = {'up': sel - 1, 'down': sel + 1, 'pgup': sel - (avail - 1),
                       'pgdn': sel + (avail - 1), 'home': 0, 'end': len(seen) - 1}[k]
            elif k == '/':
                got = self.ask_one('Find', 'with the words', find, need=False,
                                   help_='Only the rows with these words in them stay; '
                                         'Esc in the list shows them all again.')
                if got is not None:
                    find, sel, top = got, 0, 0
            elif k in ('enter', 'right') and seen and act:
                r = act(seen[sel])
                if r == 'quit':
                    return 'quit'
                if isinstance(r, tuple):
                    said, said_bad = r
                rows = load() or []

    def act(self, *args, env=None, title=None, quiet_ok=None):
        """`plexmon ARGS` run: a short answer in a window, a long one on a screen
        of its own to scroll. Returns the exit code."""
        argv = plexmon_argv() + [str(a) for a in args]
        shown = '$ plexmon ' + ' '.join(self._quote(a) for a in args)
        return self.act_argv(argv, title or shown, env=env, quiet_ok=quiet_ok)

    def act_argv(self, argv, title, env=None, quiet_ok=None):
        self.working('working…  ' + title)
        text, rc = self.capture(argv, env=env)
        plain = self._plain_text(text).rstrip()
        if rc == 0 and not plain and quiet_ok:
            plain = text = quiet_ok
        h, w = self.scr.getmaxyx()
        lines = self._wrap(plain, max(30, min(100, w - 4)) - 4)
        if len(lines) <= max(3, min(14, h - 8)):
            self.note(title if rc == 0 else '%s - it ended with %d' % (title, rc),
                      plain or '(it said nothing)', '' if rc == 0 else 'bad')
        else:
            if rc not in (0, None):
                text += '\n' + self.pal.dim('(it ended with %d)' % rc)
            self.go_plain(lambda: self.page(title, text))
        return rc

    def live(self, fn):
        """A view of its own on the terminal (a live log, a progress)."""
        self.go_plain(fn)

    # ---- small parts the lists use ------------------------------------------------
    @staticmethod
    def _when(ts):
        if not ts:
            return '-'
        try:
            return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(ts)))
        except (TypeError, ValueError):
            return str(ts)

    @staticmethod
    def _rows(got, *keys):
        if isinstance(got, dict):
            for k in keys:
                if isinstance(got.get(k), list):
                    return got[k]
            return []
        return got if isinstance(got, list) else []

    def details(self, title, row, skip=()):
        """One row's fields as words, in a window or on its own screen."""
        lines = []
        for k, v in row.items():
            if k in skip or v in (None, '', [], {}):
                continue
            if isinstance(v, (list, dict)):
                v = json.dumps(v, default=str, ensure_ascii=False)
            elif k in ('created', 'last_used', 'last_seen', 'last_in', 'activated', 'at',
                       'pending_at', 'locked_until') and isinstance(v, (int, float)):
                v = self._when(v)
            lines.append('%-16s %s' % (k, v))
        h, w = self.scr.getmaxyx()
        if len(lines) <= h - 8:
            self.note(title, '\n'.join(lines))
        else:
            self.go_plain(lambda: self.page(title, '\n'.join(lines)))

    # ---- plexmon's own menus -------------------------------------------------------
    def run(self):
        main = [
            ('1', 'Status and checks', self.m_status),
            ('2', 'Live log', self.m_live_log, 'plain'),
            ('3', 'The disks and the dashboard', self.m_dashboard),
            ('4', 'The televisions', self.m_tv),
            ('5', 'Accounts of Settings', self.m_accounts),
            ('6', 'Settings', self.m_settings),
            ('7', 'Certificates and TLS (nginx, Plex Media Server)', self.m_certs),
            ('8', 'Automatic certificates (ACME)', self.m_acme),
            ('9', 'Upgrades', self.m_upgrades),
            ('10', 'The service', self.m_service),
            ('11', 'The Plex library', self.m_plex),
            ('12', 'The machine and the data: time, databases, backups, updates, restart',
             self.m_machine),
        ]
        os.environ.setdefault('ESCDELAY', '25')
        os.environ.setdefault('NCURSES_NO_UTF8_ACS', '1')
        try:
            import locale
            locale.setlocale(locale.LC_ALL, '')
        except Exception:                               # noqa: BLE001
            pass
        import curses
        self.curses = curses
        self.st, self.st_gen, self.st_now, self.closing, self.plain = {}, 0, False, False, False

        def body(scr):
            self.scr = scr
            try:
                curses.curs_set(0)
            except Exception:                           # noqa: BLE001
                pass
            scr.keypad(True)
            self._colours()
            self.status_thread()
            # It opens on the live log, every line; Esc: the menu.
            if os.environ.get('PLEXMON_CONSOLE_START', 'log') == 'log':
                self.go_plain(lambda: self.live_log(''))
            self.menu('Main menu', main)
        try:
            curses.wrapper(body)
        finally:
            self.closing = True
        print(self.pal.dim('  The plexmon console is closed - `plexmon --console` opens it again.'))
        return 0

    def shell(self):
        sh = os.environ.get('SHELL') or '/bin/bash'
        print(self.pal.dim('  A shell - exit (or Ctrl+D) comes back to the plexmon console.'))
        try:
            subprocess.call([sh], env=dict(os.environ, PLEXMON_IN_CONSOLE='1'))
        except KeyboardInterrupt:
            pass

    def journal(self):
        """journalctl -u plexmon -f, in the live view."""
        try:
            pr = subprocess.Popen(['journalctl', '-u', SERVICE, '-f', '-n', '400', '--no-pager'],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  errors='replace')
        except OSError as e:
            self.page('journalctl', self.pal.bad_line(str(e)))
            return
        q = collections.deque()

        def pump():
            for ln in pr.stdout:
                q.append(ln.rstrip('\n'))
        threading.Thread(target=pump, daemon=True).start()

        def more():
            got = []
            while q:
                got.append(q.popleft())
            return got

        def paint(ln):
            return self._log_text(ln) if re.match(
                r'^\S+ \S+ (info|warn|error|debug|crit) ', ln) else tone_words(ln, self.pal)
        try:
            self.live_view('journalctl -u %s -f' % SERVICE, lambda: [], more, paint, '')
        finally:
            pr.terminate()
            try:
                pr.wait(3)
            except Exception:                           # noqa: BLE001
                pr.kill()

    # ---- 1 status -------------------------------------------------------------------
    def m_status(self):
        def events():
            got = self.form('Events', [
                {'key': 'n', 'label': 'how many', 'kind': 'number', 'value': '40',
                 'range': (1, 5000), 'need': True},
                {'key': 'q', 'label': 'only with the words', 'help':
                 'Empty: every event. Or a name, a kind (sign-ins, accounts, settings) or a '
                 'word.'}])
            if got:
                self.act('--events', '--limit', got['n'], *(['--search', got['q']]
                                                             if got['q'] else []))
        return self.sub('Status and checks', [
            ('1', 'Status', lambda: self.act('--status')),
            ('2', 'Checks (the disks, SMART, the API, nginx, the certificate)',
             lambda: self.act('--check')),
            ('3', 'The dashboard: latency and each disk\'s power state', lambda: self.act('--diag')),
            ('4', 'Events of Settings', events),
            ('5', 'Who is signed in to Settings', lambda: self.act('--sessions')),
        ])

    # ---- 2 live log -----------------------------------------------------------------
    def m_live_log(self):
        return self.live_log('')

    # ---- 3 the disks ----------------------------------------------------------------
    def m_dashboard(self):
        def wake():
            if self.confirm('Wake the disks', 'Every disk is spun up and SMART read from it - '
                            'the one thing that wakes them.', 'Wake them'):
                self.act('--wake')
        return self.sub('The disks and the dashboard', [
            ('1', 'How the dashboard stands', lambda: self.act('--status')),
            ('2', 'Latency and each disk\'s power state (--diag)', lambda: self.act('--diag')),
            ('3', 'Read SMART from the disks already spinning', lambda: self.act('--refresh')),
            ('4', 'Wake the disks and read SMART', wake),
            ('5', 'The dashboard\'s own check (--check)', lambda: self.act('--check')),
        ])

    # ---- 11 the Plex library --------------------------------------------------------
    def _plex_lib(self, title):
        st = self.out('--plex') or {}
        secs = st.get('sections') or []
        if not secs:
            self.note(title, st.get('error') or 'Plex Media Server has no library of movies or '
                      'shows, or does not answer.')
            return None
        got = self.choose(title, [(s['key'], '%s  (%s, %s)' % (s['title'], s['type'],
                                                              s.get('count', '?')))
                                  for s in secs])
        return got

    def _plex_title(self, it):
        rk = it['rk']
        while True:
            seen = it.get('views') or (it.get('leaves') and it.get('seen') == it.get('leaves'))
            r = self.choose(it['title'], [
                ('show', 'Everything Plex knows of it'),
                ('watched', 'Mark it unwatched' if seen else 'Mark it watched'),
                ('tracks', 'The default audio and subtitles' + (' (every episode)'
                                                                 if it.get('type') == 'show' else '')),
                ('labels', 'Labels: add or take one off'),
                ('wl', 'Put it on the watchlist / take it off'),
                ('refresh', 'Refresh its metadata (no new match)'),
            ])
            if not r:
                return
            if r == 'show':
                self.act('--plex-show', rk)
            elif r == 'watched':
                self.act('--plex-unwatched' if seen else '--plex-watched', rk)
                it['views'] = 0 if seen else 1
                if it.get('leaves'):
                    it['seen'] = 0 if seen else it['leaves']
            elif r == 'tracks':
                self._plex_tracks(it)
            elif r == 'labels':
                got = self.form('Labels of ' + it['title'], [
                    {'key': 'add', 'label': 'Put on', 'need': False, 'help': 'a label, or empty'},
                    {'key': 'remove', 'label': 'Take off', 'need': False,
                     'help': 'a label, or empty'}],
                    'The label is put on or taken off this title only.')
                if got and (got.get('add') or got.get('remove')):
                    a = ['--plex-label', rk]
                    if got.get('add'):
                        a += ['--label-add', got['add']]
                    if got.get('remove'):
                        a += ['--label-remove', got['remove']]
                    self.act(*a)
            elif r == 'wl':
                how = self.choose('The watchlist', [('add', 'Put it on the watchlist'),
                                                    ('remove', 'Take it off the watchlist')])
                if how:
                    self.act('--plex-watchlist-' + how, rk)
            elif r == 'refresh':
                self.act('--plex-refresh', rk)

    def _plex_tracks(self, it):
        sc = self.out('--plex-tracks', it['rk']) or {}
        if not sc.get('videos'):
            self.note('Audio and subtitles', sc.get('error') or 'no video in it')
            return
        n = sc['videos']

        def opts(lst, off):
            o = [('keep', 'Leave it as it is')]
            if off:
                o.append(('off', 'Off - no subtitles'))
            return o + [(v['key'], '%s  (in %d of %d%s)' % (v['label'], v['count'], n,
                                                            ', default' if v['selected'] else ''))
                        for v in lst]
        a = self.choose('Audio of %s (%d video%s)' % (it['title'], n, '' if n == 1 else 's'),
                        opts(sc.get('audio') or [], False))
        if not a:
            return
        s_ = self.choose('Subtitles of %s' % it['title'], opts(sc.get('subs') or [], True))
        if not s_ or (a == 'keep' and s_ == 'keep'):
            return
        self.act('--plex-tracks', it['rk'], '--audio', a, '--subs', s_, '--dry-run',
                 title='The plan')
        if self.confirm('Audio and subtitles', 'Set it as the plan says? Only which track plays '
                        'by default changes - the files and the subtitles stay as they are.',
                        'Set it'):
            self.act('--plex-tracks', it['rk'], '--audio', a, '--subs', s_, '--yes')

    def m_plex(self):
        def browse():
            lib = self._plex_lib('Which library')
            if not lib:
                return

            def load():
                got = self.out('--plex-items', lib)
                return got if isinstance(got, list) else []
            cols = [('title', lambda r: r.get('title'), 44, None),
                    ('year', lambda r: r.get('year') or '', 5, None),
                    ('watched', lambda r: ('%d/%d' % (r.get('seen') or 0, r.get('leaves') or 0))
                     if r.get('type') == 'show' else ('yes' if r.get('views') else ''), 8,
                     lambda r: 'ok' if (r.get('views') or (r.get('leaves') and r.get('seen') ==
                                                          r.get('leaves'))) else None),
                    ('rating', lambda r: ('%.1f' % float(r['rating'])) if r.get('rating') else '', 6,
                     None),
                    ('labels', lambda r: ', '.join(r.get('labels') or []), 30, None)]
            self.pick('The library', load, cols, act=self._plex_title,
                      words='Enter: what to do with it · / finds')

        def titles():
            lib = self._plex_lib('Titles in another language: which library')
            if not lib:
                return
            self.act('--plex-titles', lib, title='Not in the language set (Settings → Plex library)')
            if self.confirm('Put them into it', 'The agent is asked for each; only those from the '
                            'title\'s own entry are written - the title alone, locked. The rest, '
                            'to be checked, on the pages.', 'Look them up'):
                self.act('--plex-titles', lib, '--fix', '--yes')

        def ratings():
            lib = self._plex_lib('Ratings: which library')
            if not lib:
                return
            self.act('--plex-ratings', lib, '--dry-run', title='What would be written')
            if self.confirm('Ratings', 'Write the missing ratings shown?', 'Write them'):
                self.act('--plex-ratings', lib, '--yes')

        def labels():
            lib = self._plex_lib('Labels: which library')
            if lib:
                self.act('--plex-labels', lib)

        def scan():
            lib = self._plex_lib('Scan the files of which library')
            if lib:
                self.act('--plex-scan', lib)

        def refresh_all():
            lib = self._plex_lib('Refresh all the metadata of which library')
            if lib and self.confirm('Refresh all metadata', 'Plex downloads the metadata, posters '
                                    'and backgrounds of every item again - long, and it loads '
                                    'the server.', 'Refresh it all'):
                self.act('--plex-refresh-all', lib, '--yes')
        return self.sub('The Plex library', [
            ('1', 'The server and its libraries', lambda: self.act('--plex')),
            ('2', 'Browse a library - a title: watched, tracks, labels…', browse),
            ('3', 'Titles in another language', titles),
            ('4', 'Ratings: fill the missing in', ratings),
            ('5', 'The labels of a library', labels),
            ('6', 'The watchlist', lambda: self.act('--plex-watchlist')),
            ('7', 'Scan the files of a library (F5)', scan),
            ('8', 'Refresh all the metadata of a library (F9)', refresh_all),
        ])

    # ---- 12 the machine and the data (2.2.0) -------------------------------------------
    def m_machine(self):
        def plex_back():
            opts = [(c['name'], 'a copy made here - %s' % c['name']) for c in HX.plex_copies()]
            opts += [('own:' + c['name'], 'Plex\'s own backup - %s' % c['date'])
                     for c in HX.plex_own_backups() if not c['blobs']]
            if not opts:
                self.note('Put Plex\'s database back', 'There is no copy yet.')
                return
            r = self.choose('Put Plex\'s database back', opts[:30], 'Plex Media Server is stopped, '
                            'its database files set aside (kept), the copy put in, Plex started.')
            if not r:
                return
            own = r.startswith('own:')
            name = r[4:] if own else r
            if self.confirm('Put it back', 'Everything since the copy was made is not in it. '
                            'Whoever is watching is stopped.', 'Put it back'):
                self.act('--plex-db-restore', name, *(['--plex-own'] if own else []), '--yes')

        def update():
            r = self.choose('Update the system', [
                ('security', 'Only the security updates'), ('all', 'Every update')],
                'dnf, Plex Media Server\'s own package left out (osup_plex). A restart it needs '
                'is not made by itself here.')
            if r:
                self.live(lambda: self.show_live('--os-update', r, '--yes'))

        def power(how, words):
            def go():
                if self.confirm(words, 'Plex Media Server, the dashboard, these menus - '
                                'everything on this server stops.', words):
                    self.act('--' + how, '--yes')
            return go
        return self.sub('The machine and the data', [
            ('1', 'The clock (chrony)', lambda: self.act('--time')),
            ('2', 'Step the clock to the time servers now', lambda: self.act('--time-sync')),
            ('3', 'The databases: plexmon\'s and Plex Media Server\'s', lambda: self.act('--db')),
            ('4', 'A copy of plexmon\'s database now', lambda: self.act('--db-copy')),
            ('5', 'A copy of Plex Media Server\'s database now (checked)',
             lambda: self.act('--plex-db-copy')),
            ('6', 'Put Plex Media Server\'s database back…', plex_back),
            ('7', 'Backups: destinations and the last ones', lambda: self.act('--backups')),
            ('8', 'Back up now', lambda: self.act('--backup-now')),
            ('9', 'Try the newest backup', lambda: self.act('--backup-verify')),
            ('10', 'Look for updates of the system', lambda: self.act('--os-updates')),
            ('11', 'Update the system…', update),
            ('12', 'Restart the server', power('reboot', 'Restart')),
            ('13', 'Switch the server off', power('poweroff', 'Switch off')),
        ])

    # ---- 4 the televisions ------------------------------------------------------------
    def m_tv(self):
        def pair():
            tv = self.ask_one('Pair a television', 'the TV', help_=(
                'Its name or identifier (plexmon --atv shows them), or for an LG or Samsung '
                'not yet known its IP address. The PIN the television shows is asked.'))
            if tv:
                self.live(lambda: self.show_live('--atv-pair', tv))

        def probe():
            a = self.ask_one('What answers at an address', 'the address',
                             check=lambda v, _: None if re.match(r'^[0-9a-fA-F:.]{3,45}$', v)
                             else 'an IP address')
            if a:
                self.act('--atv-probe', a)

        def unpair():
            tv = self.ask_one('Forget a television', 'the TV')
            if tv and self.confirm('Forget', 'The credentials of %s are forgotten.' % tv,
                                   'Forget it'):
                self.act('--atv-unpair', tv)
        return self.sub('The televisions', [
            ('1', 'The televisions known, and which are paired', lambda: self.act('--atv')),
            ('2', 'Search the network again', lambda: self.act('--atv-scan')),
            ('3', 'What answers at an address', probe),
            ('4', 'Pair one (its PIN asked)', pair),
            ('5', 'Forget one', unpair),
        ])

    # ---- 5 accounts -----------------------------------------------------------------
    ROLES = [('fulladmin', 'fulladmin - everything'),
             ('readonly', 'readonly - looks, changes nothing')]

    def m_accounts(self):
        def load():
            got = self.out('--users') or {}
            self._mfa_on = bool(got.get('mfa_on')) if isinstance(got, dict) else False
            return self._rows(got, 'users')

        cols = [('account', lambda u: u.get('name'), 24, None),
                ('role', lambda u: u.get('role'), 10, None),
                ('state', lambda u: 'off' if u.get('disabled') else 'on', 5,
                 lambda u: 'dim' if u.get('disabled') else 'ok'),
                ('second step', lambda u: (u.get('mfa') or {}).get('kind') or '-'
                 if isinstance(u.get('mfa'), dict) else (u.get('mfa') or '-'), 11, None),
                ('last in', lambda u: self._when(u.get('last_in')), 16, None),
                ('from', lambda u: u.get('last_from') or '', 16, None),
                ('note', lambda u: u.get('note') or '', 40, lambda u: 'dim')]
        return self.sub('Accounts of Settings', [
            ('1', 'Browse them (Enter: what to do with one)',
             lambda: self.pick('Accounts of Settings', load, cols, self.account_do)),
            ('2', 'Make one', self.account_new),
            ('3', 'The second step of signing in, for everybody', self.mfa_switch),
            ('4', 'Who is signed in now', lambda: self.act('--sessions')),
        ])

    def account_do(self, u):
        name = u['name']
        opts = [('pw', 'Set its password'),
                ('role', 'Change its role (now %s)' % u.get('role')),
                ('on', 'Switch it on again') if u.get('disabled') else ('off', 'Switch it off')]
        if u.get('mfa'):
            opts.append(('mfa', 'Reset its second step (it sets one up again)'))
        opts += [('show', 'Show it all'), ('delete', 'Delete it')]
        what = self.choose(name, opts, '%s, %s' % (u.get('role'),
                                                   'off' if u.get('disabled') else 'on'))
        if what == 'pw':
            got = self.form('A new password for %s' % name, self._pw_fields())
            if got:
                self.act('--passwd', name, env={'PLEXMON_PASSWORD': got['pw']})
        elif what == 'role':
            r = self.choose('The role of %s' % name, self.ROLES,
                            cur=[x[0] for x in self.ROLES].index(u.get('role'))
                            if u.get('role') in [x[0] for x in self.ROLES] else 0)
            if r and r != u.get('role'):
                self.act('--set-role', name, '--role', r)
        elif what == 'off':
            if self.confirm('Switch off', '%s can no longer sign in to Settings.' % name,
                            'Switch it off'):
                self.act('--disable-user', name)
        elif what == 'on':
            self.act('--enable-user', name)
        elif what == 'mfa':
            if self.confirm('Reset the second step', '%s sets its second step up again at '
                            'its next sign-in.' % name, 'Reset it'):
                self.act('--reset-2fa', name)
        elif what == 'show':
            self.details(name, u, skip=('prefs', 'cols'))
        elif what == 'delete':
            if self.confirm('Delete', 'The account %s is deleted. This cannot be undone.' % name,
                            'Delete it', danger=True):
                self.act('--del-user', name)

    def account_new(self):
        got = self.form('Make an account', [
            {'key': 'name', 'label': 'name', 'need': True,
             'help': 'What it signs in with: letters, digits, . _ - @',
             'check': lambda v, _: None if re.match(r'^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$', v)
             else 'letters, digits and . _ - @ only'},
            {'key': 'role', 'label': 'role', 'kind': 'choice', 'value': 'fulladmin',
             'options': self.ROLES,
             'help': 'fulladmin may do everything; readonly looks.'}]
            + self._pw_fields())
        if got:
            self.act('--add-user', got['name'], '--role', got['role'],
                     env={'PLEXMON_PASSWORD': got['pw']})

    def mfa_switch(self):
        got = self.out('--users') or {}
        on = bool(got.get('mfa_on')) if isinstance(got, dict) else False
        r = self.choose('The second step for Settings', [
            ('on', 'On - every account gives a code from an app after its password'),
            ('off', 'Off - the password is enough'),
            ('status', 'Show how it stands, account by account')],
            'It is %s now.' % ('ON' if on else 'off'), cur=0 if on else 1)
        if r == 'status':
            self.act('--admin-2fa', 'status')
        elif r and (r == 'on') != on:
            self.act('--admin-2fa', r)
        elif r:
            self.note('The second step', 'It is %s already.' % r)

    # ---- 6 settings -----------------------------------------------------------------
    PANE_TITLES = PANE_TITLES

    def m_settings(self):
        def by_key():
            keys = sorted(DEFAULTS)
            key = self.ask_one('Change one by its key', 'the key', help_=(
                'Its key, as `plexmon --settings` shows it - e.g. acme_node_name, ui_lang. '
                'Then the same window as in Browse opens.'),
                check=lambda v, _: None if v in keys else 'there is no setting %s' % v)
            if not key:
                return
            row = next((r[1] for r in self._settings_rows() if r[0] == 'set'
                        and r[1]['key'] == key), None)
            if not row:
                self.note('Change one', 'there is no setting %s' % key, 'bad')
                return
            done = self._edit_one(row)
            if done:
                self.note('Change one', done[0], 'bad' if done[1] else 'ok')
        return self.sub('Settings', [
            ('1', 'Browse and change (arrows, Enter)', self.settings_tui),
            ('2', 'Change one by its key', by_key),
            ('3', 'All of them as text', lambda: self.act('--settings')),
        ])

    # ---- 7 certificates -----------------------------------------------------------------
    def m_certs(self):
        def import_():
            got = self.form('Put a certificate in', [
                {'key': 'cert', 'label': 'the certificate', 'need': True, 'value': '/root/',
                 'help': 'A PEM file (it may hold the key and the chain too), or a .pfx / .p12.',
                 'check': lambda v, _: None if os.path.isfile(v) else 'there is no such file'},
                {'key': 'key', 'label': 'its key', 'help': 'A PEM file; empty when it is in the '
                 'file above (or in the .pfx).',
                 'check': lambda v, _: None if not v or os.path.isfile(v) else
                 'there is no such file'},
                {'key': 'chain', 'label': 'the chain', 'help': 'The intermediate certificates '
                 '(PEM); empty when they are in the file above.',
                 'check': lambda v, _: None if not v or os.path.isfile(v) else
                 'there is no such file'},
                {'key': 'pw', 'label': 'password of the .pfx', 'kind': 'secret',
                 'help': 'Only for a .pfx / .p12 with a password.'}])
            if got and self.confirm('Put it in', 'nginx answers with it from now on (nginx -t '
                                    'first; on a refusal everything stays as it was).',
                                    'Put it in'):
                self.act('--cert-import', *[x for x in (got['cert'], got['key'], got['chain'])
                                            if x], env={'PLEXMON_PFX_PASSWORD': got['pw'] or ''})

        def selfsigned():
            name = self.ask_one('A self-signed certificate', 'its name (CN)', socket.getfqdn(),
                                help_='Browsers warn about it until it is trusted by hand.')
            if name and self.confirm('Self-signed', 'nginx answers with a self-signed '
                                     'certificate for %s from now on.' % name, 'Make it',
                                     danger=True):
                self.act('--cert-selfsigned', name)

        def tls():
            got = self.form('How TLS is spoken', [
                {'key': 'preset', 'label': 'the protocols and ciphers', 'kind': 'choice',
                 'value': 'standard', 'options': [
                     ('standard', 'Standard - TLS 1.2 + 1.3, ECDHE'),
                     ('intermediate', 'Mozilla intermediate - DHE too, for old clients'),
                     ('modern', 'Mozilla modern - TLS 1.3 only')]},
                {'key': 'dh', 'label': 'DH parameters', 'kind': 'choice', 'value': 'none',
                 'options': sorted(DH_WORDS.items())},
                {'key': 'hsts', 'label': 'HSTS', 'kind': 'choice', 'value': '0',
                 'options': [('0', 'off'), ('15768000', '6 months'), ('31536000', '1 year'),
                             ('63072000', '2 years')]}])
            if got:
                self.act('--tls-preset', got['preset'], '--tls-dh', got['dh'], '--tls-hsts',
                         got['hsts'])

        def plex():
            got = self.out('--plex-cert', 'status') or {}
            r = self.choose('Plex Media Server\'s own certificate', [
                ('on', 'On - Plex takes this certificate (it is restarted)'),
                ('now', 'Hand it to Plex again now (it is restarted)'),
                ('off', 'Off - Plex back to its own *.plex.direct (it is restarted)'),
                ('status', 'How it stands')],
                'It is %s now.' % ('ON' if got.get('on') else 'off'))
            if r == 'status':
                self.act('--plex-cert', 'status')
            elif r and self.confirm('Plex Media Server', 'Plex Media Server is restarted: what '
                                    'is playing stops for a moment.', 'Go on'):
                self.act('--plex-cert', r, '--yes')
        return self.sub('Certificates and TLS', [
            ('1', 'The certificate, and whether nginx has it', lambda: self.act('--cert-status')),
            ('2', 'Put one in from files (PEM or PFX)', import_),
            ('3', 'A self-signed one', selfsigned),
            ('4', 'How TLS is spoken (protocols, DH, HSTS)', tls),
            ('5', 'Put in again what is kept (nginx -t, reload)', lambda: self.act('--tls-apply')),
            ('6', 'Plex Media Server takes it too', plex),
            ('7', 'Hand the Plex vhost over to plexmon (once)', lambda: self.act('--nginx-adopt')),
        ])

    # ---- 8 ACME ---------------------------------------------------------------------
    def m_acme(self):
        def switch():
            on = bool(CONFIG.get('acme'))
            r = self.choose('Certificates from an ACME CA', [
                ('1', 'On - ordered and renewed by itself'), ('0', 'Off')],
                'It is %s now.' % ('ON' if on else 'off'), cur=0 if on else 1)
            if r is not None:
                self.act('--set', 'acme=%s' % r)

        def name():
            n = self.ask_one('The name of the certificate', 'DNS name',
                             CONFIG.get('acme_node_name') or '',
                             help_='The full name people type to reach Plex, plex.example.cz. '
                                   'Empty: the name of the certificate it has now.', need=False)
            if n is not None:
                self.act('--set', 'acme_node_name=%s' % n)

        def order():
            if self.confirm('Order it now', 'A certificate is ordered from %s and put in once '
                            'it comes; ACME renews it from then on.' % acme_directory_url(),
                            'Order it'):
                self.live(lambda: self.show_live('--acme-order'))
        return self.sub('Automatic certificates (ACME)', [
            ('1', 'How it stands', lambda: self.act('--acme')),
            ('2', 'On or off', switch),
            ('3', 'The name of the certificate', name),
            ('4', 'Try port 80 as the CA will', lambda: self.act('--acme-probe')),
            ('5', 'What has to be in your DNS', lambda: self.act('--acme-dns-check')),
            ('6', 'Order it now (followed)', order),
            ('7', 'Every ACME setting (the CA, DNS-01...)', self.settings_tui),
        ])

    # ---- 9 upgrades -----------------------------------------------------------------
    def m_upgrades(self):
        def online():
            self.working('working…  looking for a new version')
            row = self.out('--upgrade-check') or {}
            if row.get('error'):
                self.note('Upgrade from the web', '%s:\n%s' % (row.get('url'), row['error']),
                          'bad')
                return
            here = _ver_tuple(__version__)
            newer = [p for p in row.get('packages') or []
                     if _ver_tuple(p.get('version')) > here]
            if not newer:
                self.note('Upgrade from the web', 'Nothing newer than %s at %s.' % (
                    __version__, row.get('url')), 'ok')
                return
            ver = self.choose('Upgrade from the web', [
                (p['version'], 'plexmon %s%s' % (p['version'], '   (the newest)' if i == 0
                                                 else ''))
                for i, p in enumerate(newer)], 'This server runs %s. Found at %s:' % (
                    __version__, row.get('url')))
            if not ver:
                return
            if not self.confirm('Upgrade to %s' % ver, 'plexmon is upgraded and restarted - '
                                'the dashboard and Settings are back within a minute; Plex '
                                'itself is not touched.', 'Upgrade'):
                return
            self.live(lambda: self.show_live('--upgrade-online', ver, '--yes'))

        def install():
            path = self.ask_one('Upgrade from a package', 'the package', '/root/',
                                help_='plexmon-X.Y.Z.tar.gz - its signature is checked first.',
                                check=lambda v, _: None if os.path.isfile(v) and
                                v.endswith('.tar.gz') else 'not a plexmon-X.Y.Z.tar.gz')
            if path and self.confirm('Upgrade', 'plexmon is upgraded with %s once its '
                                     'signature is checked.' % path, 'Upgrade'):
                self.live(lambda: self.show_live('--upgrade-file', path))

        def cancel():
            if self.confirm('Stop the upgrade', 'One not yet handed to root stays as it is.',
                            'Stop it'):
                self.act('--upgrade-cancel')

        def back():
            self.working('working…  looking for older versions')
            got = self.out('--downgrade-check') or {}
            vs = got.get('versions') or []
            ok = [v for v in vs if not v.get('why')]
            if not ok:
                self.note('Go back to an older version', 'Nothing older at %s may be gone back '
                          'to.\n\n%s' % (got.get('url'), '\n'.join(
                              '%s: %s' % (v['version'], '; '.join(v['why'])) for v in vs)
                              or 'No older version is there.'), 'warn')
                return
            ver = self.choose('Go back to an older version', [
                (v['version'], 'plexmon %s' % v['version']) for v in ok],
                'This server runs %s and may go back to %s or newer (the database is not '
                'touched). Found at %s:' % (__version__, got.get('lowest'), got.get('url')))
            if ver and self.confirm('Go back to %s' % ver, 'What came in the newer versions is '
                                    'gone until you upgrade again.', 'Go back', danger=True):
                self.live(lambda: self.show_live('--downgrade', ver))
        return self.sub('Upgrades', [
            ('1', 'How it stands', lambda: self.act('--upgrade-status')),
            ('2', 'Look for a new version on the web now', lambda: self.act('--upgrade-check')),
            ('3', 'Upgrade from the web (with its progress)', online),
            ('4', 'Follow the upgrade going on', lambda: self.show_live('--upgrade-follow'),
             'plain'),
            ('5', 'Upgrade from a package file', install),
            ('6', 'Stop an upgrade going on', cancel),
            ('7', 'Go back to an older version (only a safe one)', back),
        ])

    # ---- 10 the service ---------------------------------------------------------------
    def m_service(self):
        def restart(unit, words):
            def go():
                if self.confirm('Restart %s' % unit, words, 'Restart'):
                    self.act_argv(['systemctl', 'restart', unit], '$ systemctl restart %s' % unit,
                                  quiet_ok='restarted')
                    self.st_now = True
            return go

        def at_login():
            on = console_login_state()
            r = self.choose('The console at login', [
                ('on', 'On - root sees this console first when signing in'),
                ('off', 'Off - root gets the ordinary shell')],
                'It is %s now.' % ('ON' if on else 'off'), cur=0 if on else 1)
            if r and (r == 'on') != on:
                self.act('--console-at-login', r)
        def maint():
            on = bool(maintenance_row(open_store()).get('on'))
            r = self.choose('Maintenance of this server', [
                ('on', 'Into maintenance - plexmon does nothing by itself'),
                ('off', 'Back into service')], 'It is %s now.' % (
                    'IN MAINTENANCE' if on else 'in service'), cur=1 if on else 0)
            if r == 'on':
                why = self.ask_one('Into maintenance', 'why', 'from the console', help_=(
                    'Shown to the other administrators and in Events. Backups, copies, '
                    'updates, renewals and SMART reads wait until it is back in service.'))
                if why is not None:
                    self.act('--maintenance', 'on', '--why', why)
            elif r == 'off':
                self.act('--maintenance', 'off')
        return self.sub('The service', [
            ('1', 'Its state', lambda: self.act_argv(
                ['systemctl', 'status', SERVICE, '--no-pager', '-n', '60', '-l'],
                '$ systemctl status %s' % SERVICE)),
            ('2', 'Restart it', restart(SERVICE, 'The dashboard and Settings are back in a few '
                                                 'seconds; Plex itself is not touched.')),
            ('3', 'The pages (nginx, PHP)', lambda: self.act_argv(
                ['systemctl', 'status', 'nginx', 'php-fpm', '--no-pager', '-n', '5'],
                '$ systemctl status nginx php-fpm')),
            ('4', 'Restart nginx', restart('nginx', 'Plex is not reachable through nginx for a '
                                                    'moment.')),
            ('5', 'Plex Media Server', lambda: self.act_argv(
                ['systemctl', 'status', PLEX_SERVICE, '--no-pager', '-n', '20'],
                '$ systemctl status %s' % PLEX_SERVICE)),
            ('6', 'Restart Plex Media Server', restart(PLEX_SERVICE, 'What is playing stops.')),
            ('7', 'Its journal, live', self.journal, 'plain'),
            ('8', 'The console at login: on or off', at_login),
            ('9', 'Maintenance: out of service or back', maint),
        ])


def console_main(pal):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('plexmon: the console needs a terminal')
    load_config(saved=open_store().settings_saved())
    return Console(pal).run()


def entry():
    try:
        sys.exit(cli_main(sys.argv[1:]) or 0)
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == '__main__':
    entry()
