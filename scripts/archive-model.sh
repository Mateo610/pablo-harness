#!/usr/bin/env bash
# Keep your own copy of every model you depend on, with checksums, so no host can take it away.
#   bash scripts/archive-model.sh <model-dir> [archive-root]
# Writes <archive-root>/<name>/ with the files, SHA256SUMS, and a SOURCE.txt you fill in
# (where it came from, license, date). Verify later with: (cd <dir> && sha256sum -c SHA256SUMS)
set -euo pipefail

src="${1:?usage: archive-model.sh <model-dir> [archive-root]}"
root="${2:-${PABLO_ARCHIVE:-$HOME/pablo-data/archive}}"
name="$(basename "$(realpath "$src")")"
dest="$root/$name"

mkdir -p "$dest"
echo "Hashing $src"
(cd "$src" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum) > "$src/SHA256SUMS"
echo "Copying to $dest"
rsync -a --info=progress2 "$src"/ "$dest"/
(cd "$dest" && sha256sum --quiet -c SHA256SUMS) && echo "Verified $dest"
[[ -f "$dest/SOURCE.txt" ]] || printf 'name: %s\nsource_url: \nlicense: \nrevision: \narchived: %s\n' \
  "$name" "$(date -I)" > "$dest/SOURCE.txt"
echo "Fill in $dest/SOURCE.txt (URL, license, revision)."
