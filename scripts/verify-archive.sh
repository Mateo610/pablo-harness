#!/usr/bin/env bash
# Verify every SHA256SUMS under an archive root (default: $PABLO_DATA/archive).
# Use after copying the archive to an external HDD (see docs/archive.md).
#
#   bash scripts/verify-archive.sh [archive-root]
#
# Exit 0 only when every checksum file passes. Prints each directory checked.
set -euo pipefail

root="${1:-${PABLO_ARCHIVE:-${PABLO_DATA:-$HOME/pablo-data}/archive}}"
root="$(realpath "$root")"

if [[ ! -d "$root" ]]; then
  echo "verify-archive: not a directory: $root" >&2
  exit 2
fi

mapfile -t sums < <(find "$root" -type f -name SHA256SUMS | sort)
if [[ ${#sums[@]} -eq 0 ]]; then
  echo "verify-archive: no SHA256SUMS under $root" >&2
  exit 2
fi

failed=0
for sums_file in "${sums[@]}"; do
  dir="$(dirname "$sums_file")"
  rel="${dir#"$root"/}"
  if (cd "$dir" && sha256sum -c SHA256SUMS --quiet); then
    echo "ok  $rel"
  else
    echo "BAD $rel" >&2
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  echo "verify-archive: one or more checksums failed under $root" >&2
  exit 1
fi
echo "verify-archive: ${#sums[@]} directories ok under $root"
