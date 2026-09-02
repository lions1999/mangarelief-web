#!/usr/bin/env bash
# Copy the engine package from the desktop repo into this one.
#
# The engine is vendored rather than installed as a package: extracting it into
# a third repo is the tidy end state, but it buys packaging and versioning work
# before the product is validated. This script keeps the copy honest by
# recording the desktop commit it came from in ENGINE_SOURCE.
#
#   ./scripts/sync_engine.sh ../MangaRelief
set -euo pipefail

DESKTOP_REPO="${1:-../MangaRelief}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../api" && pwd)"

if [ ! -d "$DESKTOP_REPO/engine" ]; then
    echo "No engine/ in $DESKTOP_REPO — pass the path to the desktop repo." >&2
    exit 1
fi

rm -rf "$HERE/engine"
cp -r "$DESKTOP_REPO/engine" "$HERE/engine"
rm -rf "$HERE/engine/__pycache__"

BRANCH=$(git -C "$DESKTOP_REPO" rev-parse --abbrev-ref HEAD)
COMMIT=$(git -C "$DESKTOP_REPO" rev-parse HEAD)
cat > "$HERE/ENGINE_SOURCE" <<EOF
repo:   MangaRelief (desktop)
branch: $BRANCH
commit: $COMMIT
synced: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

echo "engine/ synced from $BRANCH @ ${COMMIT:0:8}"
