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
# PHP (php2shell, CVE-2026-109618 / CVE-2026-109619 - fixed 21 October 2026),
# as otp does it - on every install and every upgrade:
#   --php-stream X.Y             the AppStream stream for a PHP not installed yet (8.3)
#   --remi                       PHP 8.4 from Remi's repository instead
#   --no-php-update              PHP left as it is (still checked, still guarded)
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
PHP_UPDATE=yes
PHP_STREAM=8.3
USE_REMI=""
while [ $# -gt 0 ]; do
    case "$1" in
        --upgrade) MODE=upgrade ;;
        --uninstall) MODE=uninstall ;;
        --yes|-y) YES=1 ;;
        --php-stream) PHP_STREAM="${2:-8.3}"; shift ;;
        --remi) USE_REMI=yes ;;
        --no-php-update) PHP_UPDATE="" ;;
        --help|-h) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: what is $1? (--upgrade, --uninstall, --yes, --php-stream X.Y, --remi, --no-php-update, --help)"; exit 2 ;;
    esac
    shift
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
# ---------------------------------------------------------------------------
# PHP, fixed against php2shell - as otp does it
# ---------------------------------------------------------------------------
# On 21 October 2026 PHP fixed two remote code execution flaws reached through
# how PHP takes a request apart before any page runs (CVE-2026-109618,
# CVE-2026-109619). Fixed upstream: 8.2.35, 8.3.36, 8.4.27, 8.5.12. AlmaLinux
# 9 rebuilt its streams the same day - php:8.3 is 8.3.36, php:8.2 is 8.2.35,
# its default 8.0 has the fix backported and keeps its number - so the
# package's changelog is read as well as the version.
#
# What this does: a PHP already fixed is left exactly as it is; one that is
# not has its packages brought up to the newest build on the stream it is on
# (never switched to another stream by itself - that would move every PHP
# site on the machine); where AppStream has no fixed build, Remi's PHP 8.4 is
# offered (asked, or --remi; never during an upgrade by itself). Either way
# the guard goes on: upload progress off and strict sessions for every PHP on
# the machine, and nginx refusing a multipart body to plexmon's pages, which
# take no request body apart (they read php://input).
PKG=""
command -v dnf >/dev/null 2>&1 && PKG=dnf
[ -z "$PKG" ] && command -v apt-get >/dev/null 2>&1 && PKG=apt
OS_MAJOR="$(sed -n 's/^VERSION_ID="\{0,1\}\([0-9]*\).*/\1/p' /etc/os-release 2>/dev/null | head -1)"
PHP_RELOAD=""

php_fpm_units() {
    local found="" listed unit
    systemctl cat php-fpm.service >/dev/null 2>&1 && found="php-fpm"
    listed="$(systemctl list-unit-files --no-legend 'php*-fpm.service' 2>/dev/null || true)"
    for unit in $(awk '{print $1}' <<< "$listed" | sed 's/\.service$//'); do
        [[ -n "$unit" && "$unit" != "php-fpm" && "$unit" =~ ^php[0-9.]*-fpm$ ]] && found="$found $unit"
    done
    echo "$found"
}
php_fpm_bin() {         # the php-fpm that serves the pages: the newest one installed
    local b
    b="$(command -v php-fpm 2>/dev/null || true)"
    [[ -z "$b" && -x /usr/sbin/php-fpm ]] && b=/usr/sbin/php-fpm
    [[ -z "$b" ]] && b="$(ls -1 /usr/sbin/php-fpm[0-9]* 2>/dev/null | sort -V | tail -n 1 || true)"
    echo "$b"
}
php_fpm_version() {     # from php-fpm itself, not the command-line php
    local b v
    b="$(php_fpm_bin)"
    [[ -n "$b" ]] || { echo ""; return; }
    v="$("$b" -v 2>/dev/null | head -n 1 | awk '{print $2}')"
    echo "${v%%[-+~]*}"
}
ver_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]; }
php_fixed() {           # is the PHP php-fpm runs fixed? 0 yes, 1 no
    local v b f log
    v="$(php_fpm_version)"
    [[ -n "$v" ]] || return 1
    b="$(echo "$v" | cut -d. -f1,2)"
    case "$b" in
        8.2) f=8.2.35 ;; 8.3) f=8.3.36 ;; 8.4) f=8.4.27 ;; 8.5) f=8.5.12 ;;
        *) if ver_ge "$b" 8.6; then return 0; fi; f="" ;;
    esac
    [[ -n "$f" ]] && ver_ge "$v" "$f" && return 0
    # A backport keeps the old number: the changelog says it instead (read
    # whole first, then searched - rpm | grep -q dies on the pipe).
    if [[ "$PKG" == "dnf" ]]; then
        log="$(rpm -q --changelog php-fpm php-common 2>/dev/null || true)"
        grep -q 'CVE-2026-109618' <<< "$log" && return 0
    elif [[ "$PKG" == "apt" ]]; then
        for log in /usr/share/doc/php*-fpm/changelog.Debian.gz /usr/share/doc/php*-common/changelog.Debian.gz; do
            [[ -f "$log" ]] || continue
            grep -q 'CVE-2026-109618' <<< "$(zcat "$log" 2>/dev/null || true)" && return 0
        done
    fi
    return 1
}
php_says() {
    local v
    v="$(php_fpm_version)"; v="${v:-?}"
    if php_fixed; then
        ok "PHP $v has the php2shell fix (CVE-2026-109618, CVE-2026-109619)"
    else
        warn "PHP $v does NOT have the php2shell fix (CVE-2026-109618, CVE-2026-109619)"
    fi
}
php_guard() {           # the second layer, for every PHP on the machine
    local dir
    for dir in /etc/php.d /etc/php/*/fpm/conf.d /etc/php/*/cli/conf.d; do
        [[ -d "$dir" ]] || continue
        if [[ -f "$dir/99-php2shell.ini" ]] && grep -q 'upload_progress.enabled *= *0' "$dir/99-php2shell.ini"; then
            continue
        fi
        install -m 0644 "$HERE/deploy/99-php2shell.ini" "$dir/99-php2shell.ini"
        PHP_RELOAD=yes
        ok "$dir/99-php2shell.ini - session upload progress off, sessions strict"
    done
}
remi_php() {
    [[ "$PKG" == "dnf" ]] || { warn "Remi's repository is for the RHEL family only"; return 1; }
    say "Remi's repository (rpms.remirepo.net), PHP 8.4"
    if ! rpm -q remi-release >/dev/null 2>&1; then
        dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-${OS_MAJOR:-9}.rpm" \
            || { warn "remi-release could not be installed (is rpms.remirepo.net reachable?)"; return 1; }
    fi
    dnf config-manager --set-enabled remi-modular >/dev/null 2>&1 || true
    dnf module reset -y php || true
    dnf module enable -y php:remi-8.4 || { warn "php:remi-8.4 could not be enabled"; return 1; }
    dnf distro-sync -y 'php*'
    dnf install -y php-fpm php-cli php-common
}
ensure_php() {
    step "PHP, and php2shell"
    local had_php="" unit
    if command -v php-fpm >/dev/null 2>&1 || ls /usr/sbin/php-fpm* >/dev/null 2>&1; then had_php=yes; fi
    if [[ -z "$PHP_UPDATE" ]]; then
        say "--no-php-update: PHP is left as it is"
        php_says; php_guard; return 0
    fi
    if [[ -n "$had_php" && -z "$USE_REMI" ]] && php_fixed; then
        say "PHP is installed and fixed: left as it is (updates come with dnf upgrade)"
        php_says; php_guard; return 0
    fi
    if [[ "$PKG" == "dnf" ]]; then
        if [[ -n "$USE_REMI" ]]; then
            remi_php || true
        elif [[ -n "$had_php" ]]; then
            say "PHP is installed but not fixed: the packages that are there are updated"
            dnf upgrade -y --refresh 'php*' || warn "dnf upgrade 'php*' did not go through"
        else
            local streams now
            streams="$(dnf -q module list php 2>/dev/null || true)"
            now="$(dnf -q module list --enabled php 2>/dev/null || true)"
            now="$(awk '/^php /{print $2; exit}' <<< "$now")"
            if [[ -z "$now" ]] && grep -qE "^php +${PHP_STREAM//./\\.}( |$)" <<< "$streams"; then
                say "PHP comes from the stream php:$PHP_STREAM"
                dnf module enable -y "php:$PHP_STREAM" || true
            elif [[ -n "$now" ]]; then
                say "the PHP stream php:$now is enabled already: it is kept"
            fi
            dnf install -y php-fpm php-cli php-common || warn "php-fpm could not be installed"
        fi
    elif [[ "$PKG" == "apt" ]]; then
        apt-get update || true
        if [[ -n "$had_php" ]]; then
            local have
            have="$(dpkg-query -W -f='${Package} ${Status}\n' 'php*' 2>/dev/null | awk '/ installed$/{print $1}' | tr '\n' ' ' || true)"
            [[ -n "$have" ]] && { apt-get install -y --only-upgrade $have || true; }
        else
            apt-get install -y php-fpm php-cli || true
        fi
    else
        warn "this distribution is not one this knows: install php-fpm yourself, fixed against php2shell"
    fi
    if ! php_fixed && [[ "$PKG" == "dnf" && -z "$USE_REMI" ]]; then
        warn "the PHP AppStream has here is not fixed yet: $(php_fpm_version)"
        # Never by itself in an upgrade: it moves every PHP site to 8.4.
        if [[ "$MODE" == upgrade || "$YES" == 1 ]]; then
            say "Remi's PHP 8.4 is not taken by itself: ./install.sh --upgrade --remi"
        elif [ -t 0 ] && ask "Take PHP 8.4 from Remi's repository instead (fixed in 8.4.27)?"; then
            remi_php || true
        fi
    fi
    php_says
    if ! php_fixed; then
        warn "Until it is fixed: dnf upgrade --refresh 'php*' as soon as the fixed build is out,"
        warn "and ./install.sh --upgrade. plexmon's pages are guarded meanwhile: upload progress"
        warn "is off everywhere and nginx refuses every multipart body to /smart/."
    fi
    php_guard
    # The new build only counts once php-fpm runs it.
    for unit in $(php_fpm_units); do
        systemctl is-active --quiet "$unit" 2>/dev/null || continue
        systemctl restart "$unit" || warn "$unit would not restart"
    done
    PHP_RELOAD=""
}
ensure_php

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
install -m 644 "$HERE/plexmon_plex.py" "$LIB/plexmon_plex.py"
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
        # php2shell: plexmon's PHP pages refuse a form or multipart body.
        PLEXMON_WEB_GROUP="$WEB_GROUP" "$PY" "$LIB/plexmon_admin.py" --nginx-adopt | sed -n '2,$p' | sed 's/^/    /'
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
