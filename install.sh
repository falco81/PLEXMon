#!/bin/bash
# install.sh - plexmon: the Plex server's dashboard and its Settings.
#
#   ./install.sh                 install (a fresh machine, or over the old
#                                plexmon / plex-status.service - it is taken
#                                over: its history, its config, its limits)
#   ./install.sh --upgrade       put this version in over an installed one
#   ./install.sh --upgrade --yes the same, asking nothing (what Settings →
#                                Upgrades runs, as root, through
#                                plexmon-upgrade.service)
#   ./install.sh --uninstall     take plexmon away (the history, the config
#                                and nginx's files are left as they are)
#
# What it never does: touch Plex Media Server, its library or its database,
# change the Plex reverse proxy beyond handing its TLS lines and the ACME
# answers to plexmon (once, checked with nginx -t, put back on any doubt), or
# wake a sleeping disk.
set -u
umask 022

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(sed -n "s/^__version__ = '\([0-9.]*\)'.*/\1/p" "$HERE/plexmon_admin.py" | head -1)"
FLOOR="$(sed -n "s/^DOWNGRADE_FLOOR = '\([0-9.]*\)'.*/\1/p" "$HERE/plexmon_admin.py" | head -1)"
LIB=/usr/local/lib/plexmon
BIN=/usr/local/bin/plexmon
WEB=/var/www/html/smart
STATE=/var/lib/plexmon
OLD_STATE=/var/lib/plex-status
ETC=/etc/plexmon
LOGS=/var/log/plexmon
ACME_ROOT=/var/www/plexmon-acme
UNITS=/etc/systemd/system
NGINX_SITE=/etc/nginx/conf.d/plex.conf

MODE=install
YES=0
for a in "$@"; do
    case "$a" in
        --upgrade) MODE=upgrade ;;
        --uninstall) MODE=uninstall ;;
        --yes|-y) YES=1 ;;
        --help|-h) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: what is $a? (--upgrade, --uninstall, --yes, --help)"; exit 2 ;;
    esac
done

if [ -t 1 ] && [ "${TERM:-dumb}" != dumb ]; then
    G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[1m'; D=$'\033[2m'; N=$'\033[0m'
else
    G=; Y=; R=; B=; D=; N=
fi
step() { echo; echo "==> $*"; }
say()  { echo "    $*"; }
ok()   { echo "    ${G}ok${N} $*"; }
warn() { echo "    ${Y}warning${N} $*"; }
die()  { echo "    ${R}failed${N} $*"; exit 1; }
ask()  {
    [ "$YES" = 1 ] && return 0
    [ -t 0 ] || return 0
    read -r -p "    $* (Y/n) " got
    case "$got" in n|N|no|ne) return 1 ;; *) return 0 ;; esac
}

[ "$(id -u)" = 0 ] || die "run it as root (sudo ./install.sh)"
[ -n "$VERSION" ] || die "this is not a plexmon package: plexmon_admin.py has no version"

# ---------------------------------------------------------------------------
if [ "$MODE" = uninstall ]; then
    step "Taking plexmon away"
    ask "plexmon, its Settings and its units are taken away (the history in $OLD_STATE, $ETC and nginx's certificates stay). Go on?" || exit 1
    systemctl disable --now plexmon.service plexmon-upgrade.path 2>/dev/null
    rm -f "$UNITS/plexmon.service" "$UNITS/plexmon-upgrade.path" "$UNITS/plexmon-upgrade.service" \
          "$UNITS/plex-status.service" /etc/profile.d/zz-plexmon-console.sh "$BIN" \
          /etc/logrotate.d/plexmon
    rm -rf "$LIB"
    rm -rf "$WEB/settings" "$WEB/assets" "$WEB/lang" "$WEB/plexmon-page.php" "$WEB/plexmon-admin-api.php"
    systemctl daemon-reload
    if grep -q "include /etc/nginx/plexmon/" "$NGINX_SITE" 2>/dev/null; then
        say "$NGINX_SITE still includes /etc/nginx/plexmon/ - left as it is, so nginx keeps"
        say "working; the copy from before plexmon is in $STATE/backup/."
    fi
    ok "plexmon is gone; the database and the backups are in $STATE"
    exit 0
fi

# ---------------------------------------------------------------------------
step "plexmon $VERSION - $([ "$MODE" = upgrade ] && echo upgrade || echo installing) on $(hostname)"
HAVE=""
[ -f "$LIB/plexmon_admin.py" ] && HAVE="$(sed -n "s/^__version__ = '\([0-9.]*\)'.*/\1/p" "$LIB/plexmon_admin.py" | head -1)"
OLD_UNIT=""
[ -f "$UNITS/plex-status.service" ] && [ ! -L "$UNITS/plex-status.service" ] && OLD_UNIT="$UNITS/plex-status.service"
if [ -n "$HAVE" ]; then
    say "installed now: plexmon $HAVE"
elif [ -n "$OLD_UNIT" ] || [ -f "$LIB/plexmon.py" ]; then
    say "installed now: the plexmon before Settings (plex-status.service) - it is taken over"
else
    say "nothing installed yet"
fi
if [ "$MODE" = upgrade ] && [ -n "$HAVE" ] && [ "${PLEXMON_UPGRADE_FROM_PAGES:-}" != 1 ]; then
    newer=$(printf '%s\n%s\n' "$HAVE" "$VERSION" | sort -V | tail -1)
    if [ "$newer" != "$VERSION" ] && [ "$HAVE" != "$VERSION" ]; then
        ask "$HAVE is newer than $VERSION - go back to $VERSION?" || exit 1
    fi
fi

# ---------------------------------------------------------------------------
step "What it needs"
PY=""
# The interpreter the daemon ran on before (python3.12 for the TV remotes'
# libraries), else the newest one there is - 3.9 at least.
for unit in "$UNITS/plexmon.service" "$UNITS/plex-status.service"; do
    if [ -f "$unit" ] && [ ! -L "$unit" ]; then
        cand=$(sed -n 's/^ExecStart=\(\S*python3[0-9.]*\)\s.*/\1/p' "$unit" | head -1)
        if [ -n "$cand" ] && [ -x "$cand" ]; then PY="$cand"; break; fi
    fi
done
if [ -z "$PY" ]; then
    for cand in /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3.11 /usr/bin/python3; do
        [ -x "$cand" ] && { PY="$cand"; break; }
    done
fi
[ -n "$PY" ] || die "no python3 - dnf install -y python3"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "$PY is older than 3.9"
ok "python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"
command -v openssl >/dev/null || die "openssl is needed (dnf install -y openssl)"
ok "openssl: $(openssl version | awk '{print $2}')"
if command -v nginx >/dev/null; then ok "nginx: $(nginx -v 2>&1 | sed 's/.*\///')"; else warn "nginx is not installed - the dashboard and Settings need it in front"; fi
FPM_USER=""
for f in /etc/php-fpm.d/www.conf /etc/php/*/fpm/pool.d/www.conf; do
    [ -f "$f" ] && FPM_USER=$(sed -n 's/^\s*user\s*=\s*\(\S*\).*/\1/p' "$f" | head -1) && break
done
[ -n "$FPM_USER" ] || { id apache >/dev/null 2>&1 && FPM_USER=apache; }
[ -n "$FPM_USER" ] || { id nginx >/dev/null 2>&1 && FPM_USER=nginx; }
[ -n "$FPM_USER" ] || FPM_USER=root
WEB_GROUP="$(id -gn "$FPM_USER" 2>/dev/null || echo root)"
if command -v php-fpm >/dev/null || ls /usr/sbin/php-fpm* >/dev/null 2>&1; then
    ok "php-fpm runs the pages as $FPM_USER (group $WEB_GROUP)"
else
    warn "php-fpm is not installed - the pages need it (dnf install -y php-fpm)"
fi
if ! "$PY" -c 'import colorama' 2>/dev/null; then
    "$PY" -m pip install -q colorama >/dev/null 2>&1 || dnf install -y -q python3-colorama >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
step "The old setup taken over"
mkdir -p "$STATE/backup" && chmod 700 "$STATE"
stamp=$(date +%Y%m%d-%H%M%S)
# The timers and helpers of the PHP collector before the daemon (the old
# INSTALL.md's step 1), where any are still there.
for u in plex-status.timer plex-status-sessions.timer plex-status-wake.path plex-status-wake.service; do
    if [ -e "$UNITS/$u" ]; then
        systemctl disable --now "$u" >/dev/null 2>&1
        mv -f "$UNITS/$u" "$STATE/backup/$u.$stamp"
        say "the old $u is gone (kept in $STATE/backup)"
    fi
done
rm -f /etc/sudoers.d/plex-status /usr/local/sbin/plex-status-collect.php /usr/local/sbin/plex-lib.php
if [ -n "$OLD_UNIT" ]; then
    systemctl stop plex-status.service >/dev/null 2>&1
    systemctl disable plex-status.service >/dev/null 2>&1
    cp -a "$OLD_UNIT" "$STATE/backup/plex-status.service.$stamp"
    rm -f "$OLD_UNIT"
    ok "plex-status.service stopped and replaced by plexmon.service (that name still works)"
fi
if [ -d "$UNITS/plex-status.service.d" ]; then
    mkdir -p "$UNITS/plexmon.service.d"
    for f in "$UNITS/plex-status.service.d"/*.conf; do
        [ -f "$f" ] || continue
        cp -a "$f" "$UNITS/plexmon.service.d/$(basename "$f")"
        say "your limits carried over: $(basename "$f") → plexmon.service.d/"
    done
    mv "$UNITS/plex-status.service.d" "$STATE/backup/plex-status.service.d.$stamp"
fi
[ -d "$OLD_STATE" ] && ok "the history and the SMART cache stay in $OLD_STATE"
[ -f /etc/plex-status/config.json ] && ok "the dashboard's settings stay in /etc/plex-status/config.json"

# ---------------------------------------------------------------------------
step "The program"
systemctl stop plexmon.service >/dev/null 2>&1
mkdir -p "$LIB/release-keys" "$LIB/tools"
install -m 755 "$HERE/plexmon.py" "$LIB/plexmon.py"
install -m 644 "$HERE/plexmon_admin.py" "$LIB/plexmon_admin.py"
install -m 755 "$HERE/deploy/plexmon_upgrade.py" "$LIB/plexmon_upgrade.py"
install -m 644 "$HERE/CHANGELOG.md" "$LIB/CHANGELOG.md"
for f in "$HERE"/tools/*; do install -m 755 "$f" "$LIB/tools/$(basename "$f")"; done
for k in "$HERE"/deploy/release-keys/*.pub; do install -m 644 "$k" "$LIB/release-keys/$(basename "$k")"; done
chown -R root:root "$LIB"
chmod 755 "$LIB" "$LIB/release-keys"
# The oldest version this machine may go back to: the highest of every
# version put in here - root's alone (plexmon_upgrade.py reads it).
was_floor="$(cat "$LIB/downgrade-floor" 2>/dev/null || echo 0.0.0)"
printf '%s\n%s\n' "$was_floor" "$FLOOR" | sort -V | tail -1 > "$LIB/downgrade-floor"
chmod 644 "$LIB/downgrade-floor"
rm -rf "$LIB/__pycache__"
cat > "$BIN" <<EOF
#!/bin/bash
# plexmon - the dashboard's daemon and Settings, on the command line (plexmon --help).
exec $PY $LIB/plexmon.py "\$@"
EOF
chmod 755 "$BIN"
ok "$LIB, $BIN"

step "The pages"
mkdir -p "$WEB/settings" "$WEB/assets" "$WEB/lang"
for f in index.php plexmon-api.php plexmon-page.php plexmon-admin-api.php; do
    install -m 644 "$HERE/web/$f" "$WEB/$f"
done
install -m 644 "$HERE/web/settings/index.php" "$WEB/settings/index.php"
for f in "$HERE"/web/assets/*; do install -m 644 "$f" "$WEB/assets/$(basename "$f")"; done
for f in "$HERE"/web/lang/*.json; do install -m 644 "$f" "$WEB/lang/$(basename "$f")"; done
chown root:root "$WEB/settings" "$WEB/assets" "$WEB/lang"
chgrp "$WEB_GROUP" "$WEB/settings" 2>/dev/null
ok "$WEB (the dashboard) and $WEB/settings/ (Settings)"

step "Where it keeps things"
mkdir -p "$ETC" "$LOGS" "$STATE/upgrade" "$ACME_ROOT/.well-known/acme-challenge" /etc/plex-status "$OLD_STATE"
chmod 750 "$ETC" "$LOGS" "$STATE/upgrade"
chmod 755 "$ACME_ROOT" "$ACME_ROOT/.well-known" "$ACME_ROOT/.well-known/acme-challenge"
[ -f "$ETC/config.json" ] || echo '{}' > "$ETC/config.json"
chmod 640 "$ETC/config.json"
install -m 644 "$HERE/deploy/plexmon.logrotate" /etc/logrotate.d/plexmon
[ -f "$ETC/console-login" ] || echo off > "$ETC/console-login"
install -m 644 "$HERE/deploy/plexmon-console-login.sh" /etc/profile.d/zz-plexmon-console.sh
ok "$STATE (SQLite), $ETC, $LOGS"

step "The services"
for u in plexmon.service plexmon-upgrade.path plexmon-upgrade.service; do
    sed "s#@PYTHON@#$PY#g" "$HERE/deploy/$u" > "$UNITS/$u"
    chmod 644 "$UNITS/$u"
done
mkdir -p "$UNITS/plexmon.service.d"
cat > "$UNITS/plexmon.service.d/00-web-group.conf" <<EOF
# Written by install.sh: the group of the web pages (php-fpm's $FPM_USER), which
# may read the API token in /run/plexmon.
[Service]
Environment=PLEXMON_WEB_GROUP=$WEB_GROUP
EOF
systemctl daemon-reload
systemctl enable plexmon.service plexmon-upgrade.path >/dev/null 2>&1
systemctl start plexmon-upgrade.path
ok "plexmon.service, plexmon-upgrade.path"

step "nginx: the Plex vhost"
if [ -f "$NGINX_SITE" ]; then
    if grep -q "include /etc/nginx/plexmon/tls.conf" "$NGINX_SITE"; then
        ok "$NGINX_SITE is plexmon's already"
    else
        say "its ssl_* lines go to /etc/nginx/plexmon/tls.conf as they are, and every server"
        say "answers /.well-known/acme-challenge/ for the CA - nothing else changes; a copy"
        say "of it as it was goes to $STATE/backup/"
        if PLEXMON_WEB_GROUP="$WEB_GROUP" "$PY" "$LIB/plexmon_admin.py" --nginx-adopt; then
            ok "adopted"
        else
            warn "left as it was - the certificate can be put in from Settings, but not its TLS settings"
        fi
    fi
else
    warn "$NGINX_SITE is not there: put deploy/nginx-plex.conf.example there (and adjust it), then ./install.sh --upgrade"
fi

step "Starting"
systemctl restart plexmon.service
for i in $(seq 1 30); do
    [ -s /run/plexmon/api-port ] && break
    sleep 1
done
if systemctl is-active --quiet plexmon.service; then
    ok "plexmon.service is running"
else
    warn "plexmon.service does not run: journalctl -u plexmon -n 50"
fi
for svc in php-fpm nginx; do
    systemctl is-active --quiet "$svc" && systemctl reload "$svc" >/dev/null 2>&1
done

# ---------------------------------------------------------------------------
if ! "$PY" "$LIB/plexmon_admin.py" --users --json 2>/dev/null | grep -q '"role": "fulladmin"'; then
    step "The first account of Settings"
    first="$STATE/first-login.txt"
    pw="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(12))')"
    if echo "$pw" | "$PY" "$LIB/plexmon_admin.py" --add-user admin --role fulladmin >/dev/null; then
        "$PY" - "$STATE/plexmon.db" <<'EOF' >/dev/null 2>&1
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.execute("UPDATE accounts SET must_change = 1 WHERE name = 'admin'"); c.commit()
EOF
        printf 'Settings of plexmon - the first sign-in\nname: admin\npassword: %s\n(chosen anew at the first sign-in; this file goes then)\n' "$pw" > "$first"
        chmod 600 "$first"
        ok "name ${B}admin${N}, password ${B}$pw${N} - you choose your own at the first sign-in"
        say "(also in $first, until then)"
    else
        warn "no account could be made: plexmon --add-user NAME --role fulladmin"
    fi
fi

step "Done"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "plexmon $VERSION"
say "the dashboard:  https://$(hostname -f 2>/dev/null || hostname)/smart/   (or https://$IP/smart/)"
say "Settings:       https://$(hostname -f 2>/dev/null || hostname)/smart/settings/"
say "on the server:  plexmon · plexmon --console · plexmon --help"
exit 0
