#!/bin/sh
# make-source.sh --- the source archive of the Chimera (cports) package.
#
#   packaging/chimera/make-source.sh OUTDIR
#
# Builds from a local checkout (no download): this writes
# mocinha-<pkgver>.tar.gz from the committed HEAD (git archive, reproducible
# gzip -n) into OUTDIR, prints its sha256 and writes the matching template
# (OUTDIR/template.py, from packaging/chimera/template.py.in). The remaster's
# cbuild run then seeds cbuild's source cache (sources/by_sha256) with it.
set -eu
OUT="${1:?usage: make-source.sh OUTDIR}"
cd "$(dirname "$0")/../.."
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "make-source: commit your changes first" >&2; exit 1; }
COUNT=$(git rev-list --count HEAD)
COMMIT=$(git rev-parse --short HEAD)
PKGVER="0.1.0.$COUNT"
mkdir -p "$OUT"
TARBALL="$OUT/mocinha-$PKGVER.tar.gz"
git archive --format=tar --prefix="mocinha-$PKGVER/" HEAD | gzip -n -9 > "$TARBALL"
SHA=$(sha256sum "$TARBALL" | cut -d' ' -f1)
sed -e "s/@PKGVER@/$PKGVER/" -e "s/@COMMIT@/$COMMIT/" -e "s/@SHA256@/$SHA/" \
    packaging/chimera/template.py.in > "$OUT/template.py"
echo "$TARBALL $SHA (commit $COMMIT)"
