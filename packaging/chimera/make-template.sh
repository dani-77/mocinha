#!/bin/sh
# make-template.sh --- the cports template of the Chimera package, for a commit on GitHub.
#
#   packaging/chimera/make-template.sh OUTDIR [REF]
#
# REF (default: HEAD) must already be on github.com/dani-77/mocinha. A release
# tag (e.g. 0.1.0) gives pkgver = the tag and the tag's archive; any other
# commit gives pkgver 0.1.0.<commit count> and that commit's archive. The
# template's source is GitHub's own archive, and its sha256 is
# computed here from that very archive, so cbuild (locally or in CI) downloads
# and verifies it like any other source. Writes OUTDIR/template.py from
# packaging/chimera/template.py.in.
set -eu
OUT="${1:?usage: make-template.sh OUTDIR [COMMIT]}"
cd "$(dirname "$0")/../.."
REF="${2:-HEAD}"
COMMIT=$(git rev-parse "$REF^{commit}")
if git show-ref --verify --quiet "refs/tags/$REF"; then
	PKGVER="$REF"
	ARCHIVE="refs/tags/$REF"
else
	PKGVER="0.1.0.$(git rev-list --count "$COMMIT")"
	ARCHIVE="$COMMIT"
fi
URL="https://github.com/dani-77/mocinha/archive/$ARCHIVE.tar.gz"
git ls-remote origin | grep -q "^$COMMIT" || \
	git branch -r --contains "$COMMIT" 2>/dev/null | grep -q origin/ || \
	{ echo "make-template: $COMMIT is not on GitHub yet (push it first)" >&2; exit 1; }
mkdir -p "$OUT"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
curl -fsSL -o "$TMP" "$URL"
SHA=$(sha256sum "$TMP" | cut -d' ' -f1)
sed -e "s/@PKGVER@/$PKGVER/" -e "s#@ARCHIVE@#$ARCHIVE#" -e "s/@COMMIT@/$COMMIT/" -e "s/@SHA256@/$SHA/" \
    packaging/chimera/template.py.in > "$OUT/template.py"
echo "mocinha $PKGVER (commit $COMMIT) sha256 $SHA"
