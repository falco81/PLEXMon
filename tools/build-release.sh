#!/bin/bash
# tools/build-release.sh - a signed plexmon-X.Y.Z.tar.gz from this repository.
#
#   PLEXMON_SIGNING_KEY=/path/to/plexmon-release-1.key tools/build-release.sh [OUTDIR]
#
# MANIFEST lists the SHA-256 of every file of the package; MANIFEST.sig is its
# Ed25519 signature by the release key (openssl pkeyutl -sign -rawin). The
# public half is deploy/release-keys/plexmon-release-1.pub, which install.sh
# puts in /usr/local/lib/plexmon/release-keys/ - only packages signed by it are
# taken by Settings → Upgrades. The private key is never in the repository.
#
# The package goes to OUTDIR (release/ by default); only the newest ten
# packages are kept there.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
KEY="${PLEXMON_SIGNING_KEY:-}"
[ -n "$KEY" ] && [ -r "$KEY" ] || { echo "PLEXMON_SIGNING_KEY: the private release key (Ed25519, PEM)"; exit 1; }
VERSION="$(sed -n "s/^__version__ = '\([0-9.]*\)'.*/\1/p" plexmon_admin.py | head -1)"
PYV="$(sed -n 's/^__version__ = "\([0-9.]*\)".*/\1/p' plexmon.py | head -1)"
[ "$VERSION" = "$PYV" ] || { echo "plexmon_admin.py says $VERSION, plexmon.py $PYV"; exit 1; }
grep -q "^## $VERSION\b" CHANGELOG.md || { echo "CHANGELOG.md has no ## $VERSION"; exit 1; }
grep -q "const PM_VERSION = '$VERSION';" web/plexmon-page.php || { echo "web/plexmon-page.php: PM_VERSION is not $VERSION"; exit 1; }
OUT="${1:-$ROOT/release}"
mkdir -p "$OUT"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TOP="plexmon-$VERSION"
mkdir -p "$WORK/$TOP"
FILES=(install.sh plexmon.py plexmon_admin.py README.md INSTALL.md CHANGELOG.md CONFIG.md LICENSE.md
       deploy web tools docs)
for f in "${FILES[@]}"; do cp -a "$f" "$WORK/$TOP/"; done
find "$WORK/$TOP" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$WORK/$TOP" \( -name '*.pyc' -o -name 'data.json' -o -name 'sessions.json' \
     -o -name 'history-full.json' -o -name 'art-*.jpg' \) -delete
chmod 755 "$WORK/$TOP/install.sh" "$WORK/$TOP/plexmon.py" "$WORK/$TOP"/tools/*
( cd "$WORK/$TOP" && find . -type f ! -name MANIFEST ! -name MANIFEST.sig | sed 's#^\./##' | LC_ALL=C sort \
    | while read -r f; do printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"; done > MANIFEST )
openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$WORK/$TOP/MANIFEST" -out "$WORK/$TOP/MANIFEST.sig"
openssl pkeyutl -verify -pubin -inkey deploy/release-keys/plexmon-release-1.pub -rawin \
    -in "$WORK/$TOP/MANIFEST" -sigfile "$WORK/$TOP/MANIFEST.sig" >/dev/null
( cd "$WORK" && tar --owner=0 --group=0 --sort=name -czf "$OUT/$TOP.tar.gz" "$TOP" )
echo "$OUT/$TOP.tar.gz"
# The newest ten kept.
ls -1 "$OUT"/plexmon-*.tar.gz 2>/dev/null | sed 's#.*/plexmon-\(.*\)\.tar\.gz#\1#' | sort -V | head -n -10 \
    | while read -r old; do rm -f "$OUT/plexmon-$old.tar.gz"; echo "dropped plexmon-$old.tar.gz"; done
# The unpacked package, as it is in the repository's root.
if [ "${PLEXMON_SYNC_ROOT:-0}" = 1 ]; then
    cp "$WORK/$TOP/MANIFEST" "$WORK/$TOP/MANIFEST.sig" "$ROOT/"
fi
