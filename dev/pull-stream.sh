#!/bin/bash
# Copy Hearsay's utterance stream from the NAS to eeyore, read-only on the NAS side.
# Until Idea Machine runs on the NAS, this is how real data reaches it.
set -euo pipefail
dest="${IM_STREAM_DIR:-$HOME/.local/share/ideamachine/stream}"
mkdir -p "$dest"
# Hearsay's stream is on the NVMe pool once Hearsay has moved there, else on storage.
src=/mnt/fast/hearsay/stream
ssh nas test -f "$src/index.json" || src=/mnt/storage/hearsay/stream
# The index is written last on the NAS, so copy it last here too: then every file it names is already in place.
rsync -a --delete --exclude index.json "nas:$src/" "$dest/"
rsync -a "nas:$src/index.json" "$dest/"
echo "stream copied from $src to $dest"
