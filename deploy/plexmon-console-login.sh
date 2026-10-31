# /etc/profile.d/zz-plexmon-console.sh - written by plexmon (plexmon --console-at-login).
# The plexmon console for root signing in at the console or over SSH, in an
# interactive login shell only. q leaves it for this shell; plexmon --console
# opens it again. Never for a command run over SSH, scp, sftp or a script.
if [ "$(id -u)" = "0" ] && [ -t 0 ] && [ -t 1 ] && [ -z "$PLEXMON_NO_CONSOLE" ] \
   && [ -z "$PLEXMON_IN_CONSOLE" ] && [ -r /etc/plexmon/console-login ] \
   && [ "$(cat /etc/plexmon/console-login 2>/dev/null)" = "on" ] \
   && [ -x /usr/local/bin/plexmon ]; then
    case "$-" in *i*) /usr/local/bin/plexmon --console ;; esac
fi
