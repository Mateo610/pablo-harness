# Archive: second copy on cold storage

`$PABLO_DATA/archive` (default `~/pablo-data/archive`) is the on-box copy of every
weight, engine build, sandbox image, host `.deb`, and rebuild cache Pablo needs when
the network is gone. Keep a second copy on an **external HDD** (preferred over a
portable SSD for cold storage: cheaper per TB, meant to sit powered off).

## Layout

| Path under `archive/` | What |
|---|---|
| `<model>/` | GGUF (and companions) + `SHA256SUMS` + `SOURCE.txt` — see `scripts/archive-model.sh` |
| `engines/<name>/` | Pinned engine source tarball or binary + checksums |
| `images/<name>/` | `podman save` OCI archives for `pablo-check:py311` and `pablo-check-deps:<hash>` |
| `debs/` | `podman`, `runsc`, `uidmap` `.deb`s matching the installed versions |
| `caches/uv/` | `uv` cache + lockfile for an offline `uv sync` |
| `caches/npm-web/` | npm cache + `npm pack` tarballs for `web/` |
| `caches/unsloth-pip/` | pip freeze + pip cache used by `$PABLO_DATA/engines/unsloth` |

Every directory that holds bytes also has `SHA256SUMS` and a filled-in `SOURCE.txt`
(URL, license, revision, archive date).

## Copy to an HDD

1. Mount the drive (example: `/mnt/pablo-archive`).
2. Copy the whole tree. `rsync` is fine; avoid tools that rewrite sparse files oddly.

```bash
sudo mount /dev/sdX1 /mnt/pablo-archive
rsync -aH --info=progress2 "$PABLO_DATA/archive"/ /mnt/pablo-archive/pablo-archive/
```

3. Verify on the copy, not only on the NVMe:

```bash
bash scripts/verify-archive.sh /mnt/pablo-archive/pablo-archive
```

`verify-archive.sh` walks every `SHA256SUMS` under the root and runs `sha256sum -c`.
It exits non-zero if any file fails.

4. Unmount and store the drive powered off. Re-verify after any future rsync.

## Adding something new

- Models: `bash scripts/archive-model.sh $PABLO_DATA/models/<name>` then fill `SOURCE.txt`
  and add a row to `THIRD_PARTY.md`.
- Everything else: put the files in a new directory under `archive/`, write `SOURCE.txt`,
  then `(cd dir && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)`.
