#!/bin/sh
# make-template.sh --- the cports template of the Chimera package, for a commit on GitHub.
#
#   packaging/chimera/make-template.sh OUTDIR [COMMIT]
#
# COMMIT (default: HEAD) must already be on github.com/dani-77/mocinha: the
# template's source is GitHub's own archive of that commit, and its sha256 is
# computed here from that very archive, so cbuild (locally or in CI) downloads
# and verifies it like any other source. Writes OUTDIR/template.py from
# packaging/chimera/template.py.in.
set -eu
OUT="${1:?usage: make-template.sh OUTDIR [COMMIT]}"
cd "$(dirname "$0")/../.."
COMMIT=$(git rev-parse "${2:-HEAD}")
URL="https://github.com/dani-77/mocinha/archive/$COMMIT.tar.gz"
git ls-remote origin | grep -q "^$COMMIT" || \
	git branch -r --contains "$COMMIT" 2>/dev/null | grep -q origin/ || \
	{ echo "make-template: $COMMIT is not on GitHub yet (push it first)" >&2; exit 1; }
COUNT=$(git rev-list --count "$COMMIT")
PKGVER="0.1.0.$COUNT"
mkdir -p "$OUT"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
curl -fsSL -o "$TMP" "$URL"
SHA=$(sha256sum "$TMP" | cut -d' ' -f1)
sed -e "s/@PKGVER@/$PKGVER/" -e "s/@COMMIT@/$COMMIT/" -e "s/@SHA256@/$SHA/" \
    packaging/chimera/template.py.in > "$OUT/template.py"
echo "mocinha $PKGVER (commit $COMMIT) sha256 $SHA"
