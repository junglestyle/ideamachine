#!/bin/bash
# Copy Hearsay's utterance stream from the NAS to eeyore, read-only on the NAS side.
# Until Idea Machine runs on the NAS, this is how real data reaches it.
set -euo pipefail
dest="${IM_STREAM_DIR:-$HOME/.local/share/ideamachine/stream}"
mkdir -p "$dest"
# The index is written last on the NAS, so copy it last here too: then every file it names is already in place.
rsync -a --delete --exclude index.json nas:/mnt/storage/hearsay/stream/ "$dest/"
rsync -a nas:/mnt/storage/hearsay/stream/index.json "$dest/"
echo "stream copied to $dest"
